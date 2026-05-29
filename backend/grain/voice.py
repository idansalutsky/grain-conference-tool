"""Voice → structured lead → encounter persistence.

The field-capture path is split into a FAST path (the rep waits for this) and
a SLOW cascade (runs in the background; results appear on the contact page
within ~15 seconds).

FAST path (~3-4s, the rep is staring at the screen):
  1. Gemini multimodal: audio → structured JSON
  2. Persist encounter row
  3. Entity resolution: created_new / auto_merged / review_needed
  4. Return immediately to the rep

SLOW background cascade (~10-15s, runs after the response is sent):
  5. Arc classifier (deterministic + LLM judge)
  6. Nudge gate re-evaluation

The brief said "speed and friction matters more than completeness". On a
busy show floor the rep needs the confirmation + structured fields in 4
seconds, not 23. The arc verdict is end-of-day information; it does not need
to block the floor flow.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from . import arc, db, entity_resolution, llm, nudge

log = logging.getLogger("grain.voice")


# ---------------------------------------------------------------------------
# FAST PATH — what the rep waits for
# ---------------------------------------------------------------------------
def capture_voice_fast(
    *,
    audio_path: Path,
    rep_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    capture_mode: str = "voice",
) -> dict:
    """Audio → encounter → resolved contact. Returns in ~3-4s.

    Does NOT run arc + nudge. Caller should schedule `run_cascade_in_background`
    via FastAPI BackgroundTasks for the contact_id this returns.
    """
    lead = llm.audio_to_lead(audio_path)
    return _persist_fast(
        raw_input=lead.get("transcript") or "",
        structured=lead,
        audio_path=audio_path,
        rep_id=rep_id, conference_id=conference_id,
        capture_mode=capture_mode,
    )


def capture_text_fast(
    *,
    text: str,
    rep_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    capture_mode: str = "text",
) -> dict:
    """Text → encounter → resolved contact. ~2s."""
    lead = llm.text_to_lead(text)
    return _persist_fast(
        raw_input=text, structured=lead,
        rep_id=rep_id, conference_id=conference_id,
        capture_mode=capture_mode,
    )


def capture_image_fast(
    *,
    image_path: Path,
    rep_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    capture_mode: str = "badge_photo",
) -> dict:
    """Badge / business-card photo → encounter → resolved contact.

    If OCR couldn't read a name (ocr_confidence 0 / name null), we surface that
    rather than creating a junk contact — the rep should retry or type it.
    """
    lead = llm.image_to_lead(image_path)
    if not (lead.get("name") or "").strip():
        return {
            "ok": False,
            "reason": "couldn't read a name from that image — retry the photo "
                      "(fill the frame, good light) or type the name.",
            "structured": lead,
        }
    return _persist_fast(
        raw_input=f"[badge photo] {lead.get('name')} @ {lead.get('company') or '?'}",
        structured=lead, image_path=image_path,
        rep_id=rep_id, conference_id=conference_id, capture_mode=capture_mode,
    )


def capture_linkedin_fast(
    *,
    url: str,
    rep_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    capture_mode: str = "linkedin_url",
) -> dict:
    """A bare LinkedIn URL → encounter → resolved contact.

    The URL is a strong identity key (entity resolution matches on linkedin),
    so even a slug-only lead is worth persisting.
    """
    lead = llm.linkedin_url_to_lead(url)
    if not (lead.get("name") or "").strip() and not (lead.get("linkedin") or "").strip():
        return {"ok": False, "reason": "not a usable LinkedIn URL", "structured": lead}
    return _persist_fast(
        raw_input=url, structured=lead,
        rep_id=rep_id, conference_id=conference_id, capture_mode=capture_mode,
    )


def _persist_fast(
    *, raw_input: str, structured: dict,
    audio_path: Optional[Path] = None,
    image_path: Optional[Path] = None,
    rep_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    capture_mode: str = "text",
) -> dict:
    """Persist encounter + resolve to contact. No LLM cascade."""
    enc_id = "enc_" + uuid.uuid4().hex[:14]
    soft_signals = structured.get("soft_signals") or []
    # sentiment may arrive as a non-int (e.g. "5", None, "high") — be defensive.
    try:
        sentiment = int(structured.get("sentiment") or 3)
    except (ValueError, TypeError):
        sentiment = 3
    sentiment = max(1, min(5, sentiment))
    meeting_requested = bool(structured.get("meeting_requested"))
    # The audio_path column doubles as the captured-media reference (audio OR
    # badge image) for audit / possible re-processing.
    media_path = audio_path or image_path

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO encounters (id, contact_id, conference_id, rep_id, "
            "captured_at, capture_mode, raw_input, audio_path, structured_json, "
            "soft_signals_json, sentiment, meeting_requested) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                enc_id, None, conference_id, rep_id, db.now_iso(), capture_mode,
                raw_input, str(media_path) if media_path else None,
                json.dumps(structured, ensure_ascii=False),
                json.dumps(soft_signals, ensure_ascii=False),
                sentiment, 1 if meeting_requested else 0,
            ),
        )
    finally:
        conn.close()

    # Entity resolution is fast (deterministic fuzzy match) — keep on fast path.
    resolution = entity_resolution.resolve_and_attach(enc_id)
    contact_id = resolution.get("contact_id")

    # Look up CURRENT arc + nudge from the contact row, if any. This shows
    # the rep what we already know about this person from prior encounters.
    arc_snapshot = None
    nudge_snapshot = None
    if contact_id:
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT arc_verdict, arc_confidence, arc_summary, "
                "nudge_active, nudge_text FROM contacts WHERE id = ?",
                (contact_id,),
            ).fetchone()
        finally:
            conn.close()
        if row:
            if row["arc_verdict"]:
                arc_snapshot = {
                    "kind": row["arc_verdict"],
                    "confidence": row["arc_confidence"],
                    "summary": row["arc_summary"],
                    "from_prior_encounters": True,
                }
            nudge_snapshot = {
                "nudge_active": bool(row["nudge_active"]),
                "nudge_text": row["nudge_text"],
                "from_prior_encounters": True,
            }

    return {
        "encounter_id": enc_id,
        "structured": structured,
        "resolution": resolution,
        "contact_id": contact_id,
        "arc": arc_snapshot,           # PRIOR verdict (or null for new contact)
        "nudge": nudge_snapshot,       # PRIOR nudge state
        "cascade_status": "pending" if resolution["decision"] in {"created_new", "auto_merged"} else "skipped",
    }


# ---------------------------------------------------------------------------
# SLOW BACKGROUND CASCADE — runs after the response returns to the rep
# ---------------------------------------------------------------------------
def run_cascade_in_background(contact_id: str) -> dict:
    """Re-classify arc + re-evaluate nudge for a contact.

    Designed to be called via FastAPI BackgroundTasks. Errors are logged but
    never raise — the response was already sent.
    """
    if not contact_id:
        return {"ok": False, "reason": "no contact_id"}
    try:
        verdict = arc.classify(contact_id, use_llm=True)
        verdict_dict = {
            "kind": verdict.kind,
            "confidence": verdict.confidence,
            "summary": verdict.summary,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("arc classify failed for %s: %s", contact_id, exc)
        verdict_dict = None

    try:
        nudge_state = nudge.evaluate(contact_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("nudge evaluate failed for %s: %s", contact_id, exc)
        nudge_state = None

    return {"ok": True, "contact_id": contact_id,
            "arc": verdict_dict, "nudge": nudge_state}


# ---------------------------------------------------------------------------
# Backwards-compat shim — keep the old name for any test that called it
# ---------------------------------------------------------------------------
def capture_text(**kwargs) -> dict:
    """Legacy synchronous capture — runs arc + nudge inline. Used by tests."""
    fast = capture_text_fast(**kwargs)
    if fast.get("contact_id") and fast.get("cascade_status") == "pending":
        cascade = run_cascade_in_background(fast["contact_id"])
        fast["arc"] = cascade.get("arc")
        fast["nudge"] = cascade.get("nudge")
        fast["cascade_status"] = "complete"
    return fast


def capture_voice(**kwargs) -> dict:
    """Legacy synchronous capture — runs arc + nudge inline. Used by tests."""
    fast = capture_voice_fast(**kwargs)
    if fast.get("contact_id") and fast.get("cascade_status") == "pending":
        cascade = run_cascade_in_background(fast["contact_id"])
        fast["arc"] = cascade.get("arc")
        fast["nudge"] = cascade.get("nudge")
        fast["cascade_status"] = "complete"
    return fast


def capture_image(**kwargs) -> dict:
    """Synchronous badge-photo capture (cascade inline). Used by the Telegram
    path, which replies immediately with intel."""
    fast = capture_image_fast(**kwargs)
    if fast.get("contact_id") and fast.get("cascade_status") == "pending":
        cascade = run_cascade_in_background(fast["contact_id"])
        fast["arc"] = cascade.get("arc")
        fast["nudge"] = cascade.get("nudge")
        fast["cascade_status"] = "complete"
    return fast


def capture_linkedin(**kwargs) -> dict:
    """Synchronous LinkedIn-URL capture (cascade inline)."""
    fast = capture_linkedin_fast(**kwargs)
    if fast.get("contact_id") and fast.get("cascade_status") == "pending":
        cascade = run_cascade_in_background(fast["contact_id"])
        fast["arc"] = cascade.get("arc")
        fast["nudge"] = cascade.get("nudge")
        fast["cascade_status"] = "complete"
    return fast
