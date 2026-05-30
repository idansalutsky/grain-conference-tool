"""Calibrated nudge — the brief's "right nudge: not too aggressive, not too subtle".

A nudge fires for a contact only when ALL of these are true:

  1. arc verdict is 'warming'   (cooling/flat/tire-kicker stay silent — by design)
  2. arc confidence ≥ ARC_CONFIDENCE_THRESHOLD (default 0.7)
  3. last touch is recent enough  (≤ 90 days)
  4. no meeting has been taken yet
  5. ≥ 2 encounters of history    (one encounter ≠ a relationship)

OR the bypass rule:

  - arc verdict is 'warming' AND we detected a job-change to an ICP-fit role
    (re-engage even if last touch is older)

When a nudge does NOT fire, we record WHY in a structured 'reasons' array.
The Settings page surfaces dismiss rates so the team can tune thresholds
from real data.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.nudge")


ARC_CONFIDENCE_THRESHOLD = 0.70
RECENCY_DAYS_MAX = 90
MIN_ENCOUNTERS = 2


def _live_arc_threshold() -> float:
    v = db.get_setting("nudge.arc_confidence_threshold")
    try:
        return float(v) if v is not None else ARC_CONFIDENCE_THRESHOLD
    except (ValueError, TypeError):
        return ARC_CONFIDENCE_THRESHOLD


def _live_recency_max() -> int:
    v = db.get_setting("nudge.recency_days_max")
    try:
        return int(v) if v is not None else RECENCY_DAYS_MAX
    except (ValueError, TypeError):
        return RECENCY_DAYS_MAX


def _load_contact_and_encounters(contact_id: str) -> tuple[Optional[dict], list[dict]]:
    conn = db.get_conn()
    try:
        contact_row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        enc_rows = conn.execute(
            "SELECT id, conference_id, captured_at, sentiment, meeting_requested, "
            "structured_json, soft_signals_json FROM encounters "
            "WHERE contact_id = ? ORDER BY captured_at ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()
    if not contact_row:
        return None, []
    contact = dict(contact_row)
    encs = []
    for r in enc_rows:
        d = dict(r)
        d["structured"] = json.loads(d["structured_json"] or "{}")
        d["soft_signals"] = json.loads(d["soft_signals_json"] or "[]")
        encs.append(d)
    return contact, encs


def _is_icp_title(title: Optional[str]) -> bool:
    if not title:
        return False
    icp = IcpConfig.default()
    persona, _, _ = icp.classify_persona(title)
    return persona in {"BUYER", "CHAMPION", "PAIN_OWNER", "ENTRY_POINT"}


def _days_since(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).days


# Soft signals (or structured flags) that mean the meeting is already locked in —
# a confirmed/booked/taken meeting genuinely needs no nudge. A meeting that was
# merely *requested* is NOT here: that contact still needs a "confirm the time"
# nudge rather than going silent.
_MEETING_LOCKED_SIGNALS = {
    "meeting_booked", "meeting_confirmed", "meeting_scheduled",
    "meeting_taken", "meeting_held", "demo_booked", "demo_scheduled",
}


def _meeting_locked(encounters: list[dict]) -> bool:
    """True if any encounter shows the meeting is confirmed/booked/taken (not
    merely requested). Reads soft_signals and an optional structured flag."""
    for e in encounters:
        signals = {str(s).lower() for s in (e.get("soft_signals") or [])}
        if signals & _MEETING_LOCKED_SIGNALS:
            return True
        struct = e.get("structured") or {}
        if struct.get("meeting_confirmed") or struct.get("meeting_booked"):
            return True
    return False


def _detect_job_change(encounters: list[dict]) -> tuple[bool, Optional[str], Optional[str]]:
    """Look at structured.company across encounter history; if it changed,
    return (True, old, new)."""
    companies = [e["structured"].get("company") for e in encounters if e["structured"].get("company")]
    if len(set(companies)) >= 2:
        return True, companies[0], companies[-1]
    return False, None, None


def _draft_nudge_text(contact: dict, encounters: list[dict], features: dict) -> str:
    """Generate the actual rep-facing nudge message. Best-effort LLM; deterministic
    fallback if no key / call fails."""
    name = (contact.get("primary_name") or "this contact").split()[0]
    company = contact.get("primary_company") or "?"
    last_disc = ""
    if encounters:
        last_disc = (encounters[-1]["structured"].get("what_discussed") or "")[:200]

    if not llm.config.OPENROUTER_API_KEY:
        return (
            f"{name} at {company} — warming. Last conversation: {last_disc[:120]} "
            f"Worth a 15-min follow-up call this week."
        )
    try:
        data = llm.chat_json([
            {"role": "system", "content": (
                "You are a sales coach writing a one-sentence nudge to a Grain "
                "sales rep. The contact has a warming arc verdict — the rep "
                "should re-engage. Be specific to the last conversation. "
                "Reply with JSON: {\"nudge\": \"...\"}"
            )},
            {"role": "user", "content": (
                f"Contact: {contact.get('primary_name')} ({contact.get('primary_title')}) "
                f"at {company}. Last discussed: {last_disc}. "
                f"Features: {features}. Write the nudge."
            )},
        ], temperature=0.3, max_tokens=200)
        return (data.get("nudge") or "").strip() or (
            f"{name} at {company} — warming. Worth a 15-min call this week."
        )
    except llm.LLMError as exc:
        log.warning("nudge LLM draft failed (%s) — using fallback", exc)
        return f"{name} at {company} — warming. Worth a 15-min call this week."


def evaluate(contact_id: str) -> dict:
    """Run the gate. Returns the dict the contact row carries:

      {
        nudge_active: bool,
        nudge_text: str | None,
        why_suppressed: [str, ...],
        gate_checks: { ... },
      }
    """
    contact, encounters = _load_contact_and_encounters(contact_id)
    if contact is None:
        return {"nudge_active": False, "why_suppressed": ["contact not found"]}

    arc = contact.get("arc_verdict")
    arc_conf = float(contact.get("arc_confidence") or 0)
    n = len(encounters)
    recency = _days_since(encounters[-1]["captured_at"]) if encounters else 99999
    ever_meeting = any(e["meeting_requested"] for e in encounters)
    meeting_locked = _meeting_locked(encounters)
    # A meeting that was REQUESTED but not yet confirmed/booked is a HOT lead
    # that still needs action ("lock the time"), not a reason to go silent.
    meeting_to_confirm = ever_meeting and not meeting_locked
    job_changed, old_co, new_co = _detect_job_change(encounters)
    # Title can land under either "title" or "role" depending on the extractor /
    # capture path; check both or the flagship job-change-to-ICP nudge silently
    # never fires for leads whose title was stored as "role".
    _last_struct = encounters[-1]["structured"] if encounters else {}
    last_title = _last_struct.get("title") or _last_struct.get("role")
    job_change_to_icp = job_changed and _is_icp_title(last_title)

    thr_arc = _live_arc_threshold()
    thr_rec = _live_recency_max()
    gate = {
        "arc": arc, "arc_confidence": arc_conf,
        "n_encounters": n, "recency_days": recency,
        "ever_meeting_requested": ever_meeting,
        "meeting_locked": meeting_locked,
        "meeting_to_confirm": meeting_to_confirm,
        "job_changed_to_icp_role": job_change_to_icp,
        "thresholds": {"arc_confidence": thr_arc, "recency_max_days": thr_rec},
    }

    # Primary rule — only a CONFIRMED/booked meeting suppresses; a meeting that
    # was merely requested keeps the contact in the nudge flow (see confirm rule).
    primary = (
        arc == "warming"
        and arc_conf >= thr_arc
        and recency <= thr_rec
        and not meeting_locked
        and n >= MIN_ENCOUNTERS
    )
    # Bypass rule: warming + job change to ICP role
    bypass = arc == "warming" and job_change_to_icp

    if not (primary or bypass):
        why = []
        if arc != "warming":
            why.append(f"arc is {arc!r}, not 'warming'")
        if arc_conf < thr_arc:
            why.append(f"arc confidence {arc_conf:.2f} below {thr_arc}")
        if recency > thr_rec:
            why.append(f"last touch {recency}d ago > {thr_rec}d")
        if meeting_locked and not bypass:
            why.append("meeting already booked — no nudge needed")
        if n < MIN_ENCOUNTERS:
            why.append(f"only {n} encounter — need ≥ {MIN_ENCOUNTERS}")
        if not why:
            why = ["no qualifying trigger fired"]
        out = {"nudge_active": False, "nudge_text": None,
               "why_suppressed": why, "gate_checks": gate}
        _persist(contact_id, False, None)
        return out

    text = _draft_nudge_text(contact, encounters, gate)
    # A requested-but-unconfirmed meeting is the hottest state: make the nudge a
    # concrete "lock the time" call to action rather than a generic re-engage.
    if meeting_to_confirm:
        name = (contact.get("primary_name") or "this contact").split()[0]
        text = (f"{name} asked for a meeting that isn't on the calendar yet — "
                f"confirm the meeting and lock a time this week. " + text)
    if bypass and job_change_to_icp:
        text += f"  (Note: job change {old_co} → {new_co}; ICP-relevant.)"
    _persist(contact_id, True, text)
    return {"nudge_active": True, "nudge_text": text,
            "why_suppressed": [], "gate_checks": gate,
            "meeting_to_confirm": meeting_to_confirm,
            "bypass_used": bypass and not primary}


def _persist(contact_id: str, active: bool, text: Optional[str]) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE contacts SET nudge_active = ?, nudge_text = ?, updated_at = ? "
            "WHERE id = ?",
            (1 if active else 0, text, db.now_iso(), contact_id),
        )
    finally:
        conn.close()
