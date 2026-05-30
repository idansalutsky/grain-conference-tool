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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import arc, db, entity_resolution, llm, nudge

log = logging.getLogger("grain.voice")

# Default capture-session window: inputs from the same rep within this many
# seconds about a compatible person are STITCHED into one encounter (a real
# handshake arrives as a burst: badge photo, then a voice note, then maybe a
# contact card). Tunable via the `capture.stitch_window_seconds` setting.
DEFAULT_STITCH_WINDOW_SECONDS = 120


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


def capture_contact_fast(
    *,
    name: Optional[str],
    phone: Optional[str] = None,
    rep_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    capture_mode: str = "contact_share",
) -> dict:
    """A shared phone contact (Telegram contact / vCard) → encounter.

    Phone is a strong identity key; name comes from the card. No LLM call — this
    is structured data already. Stitches into an open session if the rep just
    captured the same person another way (e.g. snapped the badge first)."""
    nm = (name or "").strip()
    ph = (phone or "").strip()
    if not nm and not ph:
        return {"ok": False, "reason": "contact card had no name or number"}
    lead = {
        "name": nm or None, "company": None, "title": None, "vertical": None,
        "what_discussed": None, "soft_signals": [], "sentiment": 3,
        "meeting_requested": False, "phone": ph or None, "linkedin": None,
        "transcript": None,
    }
    return _persist_fast(
        raw_input=f"[contact card] {nm or '?'} {ph}".strip(), structured=lead,
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
    """Persist encounter + resolve to contact. No LLM cascade.

    If this rep has an OPEN, person-compatible encounter within the capture
    window, the new input is STITCHED into it (one handshake = one encounter)
    rather than creating a duplicate — keeping the arc honest.
    """
    structured = _normalize_lead(structured)
    media_path = audio_path or image_path

    stitched = _try_stitch(
        new_structured=structured, raw_input=raw_input,
        media_path=media_path, rep_id=rep_id, conference_id=conference_id,
        capture_mode=capture_mode,
    )
    if stitched is not None:
        return stitched

    enc_id = "enc_" + uuid.uuid4().hex[:14]
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
                json.dumps(structured.get("soft_signals") or [], ensure_ascii=False),
                structured["sentiment"], 1 if structured["meeting_requested"] else 0,
            ),
        )
    finally:
        conn.close()

    # Entity resolution is fast (deterministic fuzzy match) — keep on fast path.
    resolution = entity_resolution.resolve_and_attach(enc_id)
    return _snapshot(enc_id, structured, resolution, resolution.get("contact_id"))


def _normalize_lead(structured: dict) -> dict:
    """Coerce the volatile fields to safe types/ranges (defensive against
    whatever the model returned)."""
    out = dict(structured)
    out["soft_signals"] = out.get("soft_signals") or []
    try:
        s = int(out.get("sentiment") or 3)
    except (ValueError, TypeError):
        s = 3
    out["sentiment"] = max(1, min(5, s))
    out["meeting_requested"] = bool(out.get("meeting_requested"))
    # mentioned_events must be a list of non-empty strings — the model sometimes
    # returns a bare string or null, which would break downstream aggregation.
    def _as_str_list(v):
        if isinstance(v, str):
            v = [v]
        return ([x.strip() for x in v if isinstance(x, str) and x.strip()]
                if isinstance(v, list) else [])

    out["mentioned_events"] = _as_str_list(out.get("mentioned_events"))
    # Market intelligence from the conversation (competitors + product/PMF signal).
    out["competitor_signals"] = _as_str_list(out.get("competitor_signals"))
    out["product_signals"] = _as_str_list(out.get("product_signals"))
    return out


def _snapshot(enc_id: str, structured: dict, resolution: dict,
              contact_id: Optional[str], *, stitched: bool = False) -> dict:
    """Build the fast-path response: structured lead + PRIOR arc/nudge on the
    resolved contact (so the rep recognises a returning contact on the floor)."""
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
                    "kind": row["arc_verdict"], "confidence": row["arc_confidence"],
                    "summary": row["arc_summary"], "from_prior_encounters": True,
                }
            nudge_snapshot = {
                "nudge_active": bool(row["nudge_active"]),
                "nudge_text": row["nudge_text"], "from_prior_encounters": True,
            }
    decision = resolution.get("decision") if resolution else None
    return {
        "encounter_id": enc_id,
        "structured": structured,
        "resolution": resolution,
        "contact_id": contact_id,
        "arc": arc_snapshot,
        "nudge": nudge_snapshot,
        "stitched": stitched,
        "cascade_status": "pending" if decision in {"created_new", "auto_merged"} else "skipped",
    }


# ---------------------------------------------------------------------------
# Capture-session stitching (time-window + auto-split)
# ---------------------------------------------------------------------------
def _stitch_window_seconds() -> int:
    v = db.get_setting("capture.stitch_window_seconds")
    try:
        return int(v) if v is not None else DEFAULT_STITCH_WINDOW_SECONDS
    except (ValueError, TypeError):
        return DEFAULT_STITCH_WINDOW_SECONDS


def _capture_break_at(rep_id: Optional[str]) -> Optional[datetime]:
    if not rep_id:
        return None
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT capture_break_at FROM reps WHERE id = ?", (rep_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["capture_break_at"]:
        return None
    try:
        return datetime.fromisoformat(row["capture_break_at"].replace("Z", "+00:00"))
    except ValueError:
        return None


def close_capture_session(rep_id: str) -> dict:
    """Mark a session break ("next person") — subsequent captures start a new
    encounter even if they fall inside the stitch window."""
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "UPDATE reps SET capture_break_at = ? WHERE id = ?",
            (db.now_iso(), rep_id),
        )
        ok = cur.rowcount > 0
    finally:
        conn.close()
    return {"ok": ok, "rep_id": rep_id}


# Soft-delete buffer: the last few deleted captures, newest last. A delete is a
# hard row removal (the table must not surface a deleted encounter), but we keep
# a full snapshot here so the rep can UNDO a mis-tap. If the contact was the
# encounter's only one it gets orphan-deleted too — we snapshot it as well so
# restore brings BOTH back.
_DELETED_BUFFER: list[dict] = []
_DELETED_BUFFER_MAX = 50


def delete_encounter(encounter_id: str) -> dict:
    """Remove a capture (mistake / junk OCR). Re-cascades the contact if it has
    other encounters, else deletes the now-orphaned contact.

    The deleted encounter (and an orphaned contact, if any) is buffered so it can
    be brought back with `restore_encounter` / `restore_last_deleted`.
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM encounters WHERE id = ?", (encounter_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "encounter_not_found"}
        enc_snapshot = dict(row)
        contact_id = enc_snapshot.get("contact_id")
        conn.execute("DELETE FROM encounters WHERE id = ?", (encounter_id,))
    finally:
        conn.close()

    contact_snapshot = None
    contact_deleted = False
    if contact_id:
        contact_snapshot = _contact_row(contact_id)
        remaining = _delete_if_orphan(contact_id)
        if remaining:  # contact still has encounters → its arc/nudge changed
            run_cascade_in_background(contact_id)
        else:
            contact_deleted = True  # the orphan was removed alongside the enc

    _buffer_deleted(enc_snapshot, contact_snapshot if contact_deleted else None)
    return {"ok": True, "encounter_id": encounter_id, "contact_id": contact_id,
            "undo_available": True}


def _contact_row(contact_id: str) -> Optional[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _buffer_deleted(enc_snapshot: dict, contact_snapshot: Optional[dict]) -> None:
    _DELETED_BUFFER.append({"encounter": enc_snapshot, "contact": contact_snapshot})
    if len(_DELETED_BUFFER) > _DELETED_BUFFER_MAX:
        del _DELETED_BUFFER[:-_DELETED_BUFFER_MAX]


def _reinsert_row(table: str, row: dict) -> None:
    cols = ", ".join(row.keys())
    ph = ", ".join("?" * len(row))
    conn = db.get_conn()
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({ph})",
            tuple(row.values()),
        )
    finally:
        conn.close()


def restore_encounter(encounter_id: str) -> dict:
    """Undo a delete: bring a buffered encounter (and its orphan-deleted contact,
    if any) back, then re-resolve + re-cascade. Returns ok:false if nothing
    buffered for that id."""
    entry = None
    for i in range(len(_DELETED_BUFFER) - 1, -1, -1):
        if _DELETED_BUFFER[i]["encounter"].get("id") == encounter_id:
            entry = _DELETED_BUFFER.pop(i)
            break
    if entry is None:
        return {"ok": False, "error": "nothing_to_restore"}
    return _apply_restore(entry)


def restore_last_deleted() -> dict:
    """Undo the most recent delete (LIFO). Returns ok:false if the buffer is
    empty."""
    if not _DELETED_BUFFER:
        return {"ok": False, "error": "nothing_to_restore"}
    return _apply_restore(_DELETED_BUFFER.pop())


def _apply_restore(entry: dict) -> dict:
    enc = entry["encounter"]
    contact = entry.get("contact")
    # Restore the orphan-deleted contact first (so the FK target exists), then
    # the encounter. INSERT OR IGNORE keeps this idempotent if a row reappeared.
    if contact:
        _reinsert_row("contacts", contact)
    # Re-resolution decides the right contact, so drop the stale contact_id and
    # let resolve_and_attach re-link it (the old contact may be gone/changed).
    enc_to_insert = dict(enc)
    enc_to_insert["contact_id"] = None
    _reinsert_row("encounters", enc_to_insert)

    resolution = entity_resolution.resolve_and_attach(enc["id"])
    contact_id = resolution.get("contact_id")
    if contact_id:
        run_cascade_in_background(contact_id)
    return {"ok": True, "encounter_id": enc["id"], "contact_id": contact_id,
            "resolution": resolution, "restored": True}


def last_encounter_for_rep(rep_id: str) -> Optional[dict]:
    """The rep's most recent encounter (for /undo and /fix in chat)."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, structured_json FROM encounters WHERE rep_id = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (rep_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _try_stitch(*, new_structured: dict, raw_input: str, media_path,
                rep_id: Optional[str], conference_id: Optional[str],
                capture_mode: str) -> Optional[dict]:
    """If the rep has a recent, person-compatible encounter AT THE SAME EVENT,
    merge into it and return its snapshot. Else None (caller creates new)."""
    if not rep_id:
        return None
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM encounters WHERE rep_id = ? ORDER BY captured_at DESC LIMIT 1",
            (rep_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    enc = dict(row)
    # Only stitch within the same event — a burst happens at one conference.
    # (If either side is unattributed, allow it; the rep just didn't pick an event.)
    if conference_id and enc.get("conference_id") and enc["conference_id"] != conference_id:
        return None
    try:
        last = datetime.fromisoformat((enc["captured_at"] or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if (datetime.now(timezone.utc) - last).total_seconds() > _stitch_window_seconds():
        return None  # window closed → new encounter
    # Explicit "next person" break: if the rep marked a session break AFTER this
    # candidate was captured, the candidate belongs to a previous person.
    break_at = _capture_break_at(rep_id)
    if break_at and last <= break_at:
        return None

    base = json.loads(enc.get("structured_json") or "{}")
    base_name = (base.get("name") or "").strip()
    new_name = (new_structured.get("name") or "").strip()
    # Auto-split: if both inputs clearly name DIFFERENT people, don't merge.
    if base_name and new_name:
        if entity_resolution._name_similarity(base_name, new_name) < 0.6:
            return None
    merged = _merge_structured(
        base, new_structured,
        base_mode=enc.get("capture_mode") or "", new_mode=capture_mode,
    )
    return _merge_into_encounter(enc, merged, raw_input, media_path, capture_mode)


# Source reliability for identity fields: a PRINTED badge/business-card/contact
# beats a HEARD voice/text, which beats a LinkedIn-slug GUESS.
def _source_rank(mode: str) -> int:
    ranks = [3 if ("badge" in m or "contact" in m) else 1 if "linkedin" in m else 2
             for m in (mode or "").split("+") if m]
    return max(ranks) if ranks else 2


def _merge_structured(base: dict, new: dict, *, base_mode: str = "",
                      new_mode: str = "") -> dict:
    """Deterministic, explainable field merge. Identity fields fill-if-missing,
    but a more-authoritative SOURCE (e.g. a badge photo) corrects a mis-heard
    voice value on conflict. Union signals; concat discussion; OR meeting;
    latest-meaningful sentiment."""
    out = _normalize_lead(base)
    for k in ("name", "company", "title", "email", "phone", "linkedin", "vertical"):
        bv = (out.get(k) or "").strip()
        nv = (new.get(k) or "").strip()
        if not nv:
            continue
        if not bv:
            out[k] = new[k]                      # fill the gap
        elif k in ("name", "company", "title") and _source_rank(new_mode) > _source_rank(base_mode):
            out[k] = new[k]                      # printed source corrects a heard one
    discussed = [d for d in [base.get("what_discussed"), new.get("what_discussed")]
                 if (d or "").strip()]
    if discussed:
        out["what_discussed"] = " | ".join(dict.fromkeys(discussed))
    out["soft_signals"] = sorted(set((base.get("soft_signals") or [])
                                     + (new.get("soft_signals") or [])))
    # Sentiment: the LATEST meaningful read wins (a later "actually, lukewarm"
    # should override the badge's neutral, not be masked by a max()). Keep the
    # prior read only when the new input is neutral (e.g. a badge added later).
    b_sent = _normalize_lead(base)["sentiment"]
    n_sent = _normalize_lead(new)["sentiment"]
    out["sentiment"] = n_sent if n_sent != 3 else b_sent
    out["meeting_requested"] = bool(base.get("meeting_requested")) or bool(new.get("meeting_requested"))
    return out


def _merge_into_encounter(enc: dict, merged: dict, raw_input: str,
                          media_path, capture_mode: str) -> dict:
    modes = {m for m in (enc.get("capture_mode") or "").split("+") if m}
    modes.add(capture_mode)
    combined_mode = "+".join(sorted(modes))
    new_raw = (enc.get("raw_input") or "")
    if raw_input:
        new_raw = (new_raw + "\n" + raw_input).strip()
    old_contact = enc.get("contact_id")

    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE encounters SET structured_json = ?, soft_signals_json = ?, "
            "sentiment = ?, meeting_requested = ?, raw_input = ?, capture_mode = ?, "
            "audio_path = COALESCE(audio_path, ?), contact_id = NULL WHERE id = ?",
            (
                json.dumps(merged, ensure_ascii=False),
                json.dumps(merged.get("soft_signals") or [], ensure_ascii=False),
                merged["sentiment"], 1 if merged["meeting_requested"] else 0,
                new_raw, combined_mode,
                str(media_path) if media_path else None, enc["id"],
            ),
        )
    finally:
        conn.close()

    resolution = entity_resolution.resolve_and_attach(enc["id"])
    new_contact = resolution.get("contact_id")
    # The merge gained fields (phone/title/email) — backfill the contact's empty
    # primaries so the enrichment isn't stranded on the encounter.
    if new_contact:
        _enrich_contact_from_struct(new_contact, merged)
    # If re-resolution moved the encounter to a different contact, the original
    # (created seconds ago by the first message) may be orphaned — clean it up.
    if old_contact and old_contact != new_contact:
        _delete_if_orphan(old_contact)
    return _snapshot(enc["id"], merged, resolution, new_contact, stitched=True)


def _enrich_contact_from_struct(contact_id: str, struct: dict) -> None:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not row:
            return
        c = dict(row)
        updates = {}
        pairs = [
            ("primary_email", struct.get("email")),
            ("primary_company", struct.get("company")),
            ("primary_title", struct.get("title") or struct.get("role")),
            ("linkedin_handle", struct.get("linkedin")),
            ("phone", struct.get("phone")),
        ]
        for col, val in pairs:
            if (val or "").strip() and not (c.get(col) or "").strip():
                updates[col] = val
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE contacts SET {sets}, updated_at = ? WHERE id = ?",
                         (*updates.values(), db.now_iso(), contact_id))
    finally:
        conn.close()


def _delete_if_orphan(contact_id: str) -> bool:
    """Delete a contact that has no remaining encounters. Returns True if the
    contact still has encounters (survived), False if it was deleted."""
    conn = db.get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM encounters WHERE contact_id = ?", (contact_id,)
        ).fetchone()[0]
        if n == 0:
            conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
            return False
        return True
    finally:
        conn.close()


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

    # Feed the genuine field capture into the Grain Brain's relationship space in
    # real time, once the arc verdict is known (best-effort — never breaks capture).
    try:
        from grain.brain import spaces as _brain
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT c.id, c.primary_name, c.primary_company, c.primary_title, "
                "c.arc_verdict, c.arc_summary, c.arc_confidence, "
                "(SELECT COUNT(*) FROM encounters e WHERE e.contact_id = c.id) AS n, "
                "(SELECT MAX(e.meeting_requested) FROM encounters e "
                " WHERE e.contact_id = c.id) AS any_meeting "
                "FROM contacts c WHERE c.id = ?", (contact_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            r = dict(row)
            _brain.ingest_encounter({
                "contact_id": r.get("id"),
                "primary_name": r.get("primary_name"),
                "primary_company": r.get("primary_company"),
                "primary_title": r.get("primary_title"),
                "arc_verdict": r.get("arc_verdict"),
                "arc_summary": r.get("arc_summary"),
                "arc_confidence": r.get("arc_confidence"),
                "encounter_count": int(r.get("n") or 0),
                "meeting_requested": bool(r.get("any_meeting")),
            })
    except Exception as exc:  # noqa: BLE001
        log.warning("brain ingest failed for %s: %s", contact_id, exc)

    # L1 hierarchical memory: recompute the affected ACCOUNT + EVENT rollup(s)
    # for this contact so the middle-management tier reflects the new dot. Best-
    # effort — never breaks capture (the response was already sent).
    try:
        from grain.brain import rollups as _rollups
        _rollups.recompute_for_contact(contact_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("brain rollup recompute failed for %s: %s", contact_id, exc)

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


def capture_contact(**kwargs) -> dict:
    """Synchronous shared-contact capture (cascade inline). Telegram path."""
    fast = capture_contact_fast(**kwargs)
    if fast.get("contact_id") and fast.get("cascade_status") == "pending":
        cascade = run_cascade_in_background(fast["contact_id"])
        fast["arc"] = cascade.get("arc")
        fast["nudge"] = cascade.get("nudge")
        fast["cascade_status"] = "complete"
    return fast


# ---------------------------------------------------------------------------
# Edit a capture — correct mis-heard fields, then re-resolve + re-cascade
# ---------------------------------------------------------------------------
_EDITABLE_FIELDS = {"name", "company", "title", "email", "phone", "linkedin",
                    "vertical", "what_discussed", "sentiment", "meeting_requested"}


def edit_encounter(encounter_id: str, fields: dict) -> dict:
    """Apply rep corrections to an encounter's structured lead, then re-resolve
    identity (name/company/phone may have changed) and re-run arc + nudge.

    Changing identity can re-point the encounter to a different contact; the
    previously-attached contact is cleaned up if it's left with no encounters.
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT structured_json, contact_id FROM encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"ok": False, "error": "encounter_not_found"}

    structured = json.loads(row["structured_json"] or "{}")
    old_contact = row["contact_id"]
    for k, v in (fields or {}).items():
        if k in _EDITABLE_FIELDS:
            structured[k] = v
    structured = _normalize_lead(structured)

    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE encounters SET structured_json = ?, soft_signals_json = ?, "
            "sentiment = ?, meeting_requested = ?, contact_id = NULL WHERE id = ?",
            (
                json.dumps(structured, ensure_ascii=False),
                json.dumps(structured.get("soft_signals") or [], ensure_ascii=False),
                structured["sentiment"], 1 if structured["meeting_requested"] else 0,
                encounter_id,
            ),
        )
    finally:
        conn.close()

    resolution = entity_resolution.resolve_and_attach(encounter_id)
    new_contact = resolution.get("contact_id")
    if new_contact:
        _enrich_contact_from_struct(new_contact, structured)
    if old_contact and old_contact != new_contact:
        _delete_if_orphan(old_contact)
    # Editing is off the floor — run the cascade inline so the corrected verdict
    # is immediately visible.
    if new_contact:
        run_cascade_in_background(new_contact)
        if old_contact and old_contact != new_contact:
            run_cascade_in_background(old_contact)
    snap = _snapshot(encounter_id, structured, resolution, new_contact)
    snap["ok"] = True
    return snap
