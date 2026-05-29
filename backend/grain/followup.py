"""Post-event follow-up drafting — event + conversation grounded.

The pre-event *approach brief* (brief.py) is about walking UP to someone. This
is the other side: after the event, turn each encounter into a follow-up email
that references the SPECIFIC event by name and what was actually discussed —
"Good catching up at Money20/20; you mentioned FX leakage on multi-currency
settlement…", not "nice to meet you".

Design (draft-and-review, NOT auto-send):
  - draft_for_contact: one contact → {subject, body}, grounded in their latest
    encounter (event name + what_discussed + arc) + Grain's value prop.
  - The body is written to encounters.followup_draft so the HubSpot push carries
    it (grain_followup_draft) — the judgment travels with the contact.
  - draft_for_event: batch every contact a rep met at one event, with a
    `recommended` flag so the rep deprioritises tire-kickers / already-met.
  - Nothing is sent here. The rep edits, then sends (mailto / HubSpot sequence).
  - Deterministic fallback when no LLM key — still event + name grounded.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from . import db, llm

log = logging.getLogger("grain.followup")


def _conf_name(conference_id: Optional[str]) -> Optional[str]:
    if not conference_id:
        return None
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT name FROM conferences WHERE id = ?", (conference_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["name"] if row else None


def _load(contact_id: str) -> tuple[Optional[dict], list[dict]]:
    conn = db.get_conn()
    try:
        crow = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        encs = conn.execute(
            "SELECT id, conference_id, captured_at, sentiment, meeting_requested, "
            "structured_json FROM encounters WHERE contact_id = ? "
            "ORDER BY captured_at ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()
    if not crow:
        return None, []
    out = []
    for r in encs:
        d = dict(r)
        d["structured"] = json.loads(d.pop("structured_json") or "{}")
        out.append(d)
    return dict(crow), out


def _pick_anchor(encounters: list[dict], conference_id: Optional[str]) -> Optional[dict]:
    """The encounter the email is anchored on: the latest at the requested
    event if given, else the most recent encounter overall."""
    if not encounters:
        return None
    if conference_id:
        at_event = [e for e in encounters if e["conference_id"] == conference_id]
        if at_event:
            return at_event[-1]
    return encounters[-1]


_SYSTEM = (
    "You write short, specific post-conference follow-up emails for a Grain "
    "Finance account executive. Grain sells embedded cross-currency FX hedging "
    "to platforms with heavy cross-border volume (PSPs, travel platforms, "
    "marketplaces, cross-border payments). Tone: warm, concise, peer-to-peer — "
    "NOT salesy. Reference the specific event and what was actually discussed. "
    "No 'nice to meet you' filler. One clear, low-friction next step. "
    "Reply with ONLY JSON: {\"subject\": \"...\", \"body\": \"...\"}. The body "
    "is 3-5 short lines, no signature block, no placeholders like [Name]."
)


def draft_for_contact(
    contact_id: str,
    conference_id: Optional[str] = None,
    *,
    persist: bool = True,
) -> dict:
    """Draft one follow-up. Returns {ok, contact_id, subject, body, event_name,
    is_repeat, recommended, used_llm, encounter_id}."""
    contact, encounters = _load(contact_id)
    if contact is None:
        return {"ok": False, "error": "contact_not_found"}

    anchor = _pick_anchor(encounters, conference_id)
    anchor_conf = anchor["conference_id"] if anchor else conference_id
    event_name = _conf_name(anchor_conf)
    discussed = ((anchor or {}).get("structured", {}) or {}).get("what_discussed") or ""
    name = contact.get("primary_name") or "there"
    first = name.split(" ")[0] if name else "there"
    company = contact.get("primary_company") or ""
    title = contact.get("primary_title") or ""
    arc = contact.get("arc_verdict")
    # distinct events this person was met at → "catching up again"
    distinct_events = {e["conference_id"] for e in encounters if e["conference_id"]}
    is_repeat = len(distinct_events) > 1
    ever_meeting = any(e["meeting_requested"] for e in encounters)
    # Recommend sending for everyone EXCEPT tire-kickers (low-pressure there).
    recommended = arc != "tire_kicker"

    subject, body, used_llm = _generate(
        first=first, name=name, company=company, title=title,
        event_name=event_name, discussed=discussed, arc=arc,
        is_repeat=is_repeat, ever_meeting=ever_meeting,
    )

    encounter_id = anchor["id"] if anchor else None
    if persist and encounter_id:
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE encounters SET followup_draft = ? WHERE id = ?",
                (body, encounter_id),
            )
        finally:
            conn.close()

    return {
        "ok": True, "contact_id": contact_id, "name": name,
        "company": company, "title": title,
        "subject": subject, "body": body,
        "event_name": event_name, "is_repeat": is_repeat,
        "recommended": recommended, "used_llm": used_llm,
        "encounter_id": encounter_id, "arc": arc,
    }


def _generate(*, first: str, name: str, company: str, title: str,
              event_name: Optional[str], discussed: str, arc: Optional[str],
              is_repeat: bool, ever_meeting: bool) -> tuple[str, str, bool]:
    if not llm.config.OPENROUTER_API_KEY:
        return (*_fallback(first, company, event_name, discussed, is_repeat), False)
    where = f"at {event_name}" if event_name else "at the conference"
    again = " (we'd met before, so this is a re-connect)" if is_repeat else ""
    stage = ("They already asked for a meeting — confirm/scheduling tone."
             if ever_meeting else
             "No meeting yet — propose one low-friction next step.")
    arc_note = f"Relationship read: {arc}. " if arc else ""
    user = (
        f"Write the follow-up.\n"
        f"Person: {name}" + (f", {title}" if title else "") +
        (f" at {company}" if company else "") + f"\n"
        f"Met {where}{again}.\n"
        f"What was discussed: {discussed or '(not recorded — keep it light and ask)'}\n"
        f"{arc_note}{stage}\n"
        "Tie the next step to Grain's embedded FX-hedging value where natural."
    )
    try:
        data = llm.chat_json(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.4, max_tokens=400,
        )
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
        if body:
            return subject or _default_subject(event_name), body, True
    except llm.LLMError as exc:
        log.info("follow-up LLM draft failed (%s) — fallback", exc)
    return (*_fallback(first, company, event_name, discussed, is_repeat), False)


def _default_subject(event_name: Optional[str]) -> str:
    return f"Following up from {event_name}" if event_name else "Following up"


def _fallback(first: str, company: str, event_name: Optional[str],
              discussed: str, is_repeat: bool) -> tuple[str, str]:
    where = event_name or "the conference"
    opener = (f"Good catching up again at {where}"
              if is_repeat else f"Great connecting at {where}")
    mid = (f" — you mentioned {discussed.rstrip('.')}. " if discussed
           else " — I enjoyed the conversation. ")
    body = (
        f"Hi {first},\n\n"
        f"{opener}{mid}At Grain we embed cross-currency FX hedging directly "
        f"into platforms like {company or 'yours'}, so the spread on "
        "multi-currency flows stops leaking margin.\n\n"
        "Worth a quick 15 minutes next week to see if it maps to your setup?"
    )
    return _default_subject(event_name), body


def draft_for_event(conference_id: str) -> dict:
    """Draft follow-ups for every contact met at one event (post-event close)."""
    event_name = _conf_name(conference_id)
    if event_name is None:
        return {"ok": False, "error": "conference_not_found"}
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT contact_id FROM encounters "
            "WHERE conference_id = ? AND contact_id IS NOT NULL",
            (conference_id,),
        ).fetchall()
    finally:
        conn.close()
    drafts = []
    for r in rows:
        d = draft_for_contact(r["contact_id"], conference_id, persist=True)
        if d.get("ok"):
            drafts.append(d)
    drafts.sort(key=lambda d: (not d["recommended"], d.get("contact_id") or ""))
    return {
        "ok": True, "conference_id": conference_id, "event_name": event_name,
        "count": len(drafts),
        "recommended_count": sum(1 for d in drafts if d["recommended"]),
        "drafts": drafts,
    }


def update_draft(encounter_id: str, body: str) -> dict:
    """Persist a rep's edited follow-up body on the encounter."""
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "UPDATE encounters SET followup_draft = ? WHERE id = ?",
            (body, encounter_id),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": "encounter_not_found"}
    finally:
        conn.close()
    return {"ok": True, "encounter_id": encounter_id}
