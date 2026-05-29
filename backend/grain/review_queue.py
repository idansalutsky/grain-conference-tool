"""Match-review queue — the resolver's "I'm not sure" inbox.

Whenever entity resolution returns `decision=review_needed`, the encounter
stays UNATTACHED. This module surfaces those cases for the rep to confirm
or split.

Why we don't auto-merge in the review band:
  Two real people can share a name + company (Maria Garcia at Booking has
  3 LinkedIn profiles). Auto-merging them silently destroys data. Surfacing
  the ambiguity respects the rep's judgment.

When the rep confirms (yes, same person):
  - Encounter is attached to the candidate contact
  - Arc reclassifies on the contact (now has one more encounter)
  - Nudge re-evaluates

When the rep rejects (no, different person):
  - A new contact is created from the encounter
  - The resolver decision is logged with `outcome=overridden` so the
    supervisor can use it later
"""
from __future__ import annotations

import json
from typing import Optional

from . import arc, db, entity_resolution, nudge


def list_pending(limit: int = 50) -> list[dict]:
    """Encounters with a logged `review_needed` decision that haven't been
    confirmed or rejected yet, AND aren't yet attached to a contact.
    """
    conn = db.get_conn()
    try:
        # Pull all entity_resolution decisions; pick review_needed; check
        # whether a subsequent rep_match_confirmed/rejected exists.
        rows = conn.execute(
            "SELECT id, target_id, after_value, decided_at FROM feedback "
            "WHERE decision_kind = 'entity_resolution' "
            "ORDER BY decided_at DESC LIMIT 500"
        ).fetchall()
        decided = {
            r["target_id"] for r in conn.execute(
                "SELECT DISTINCT target_id FROM feedback "
                "WHERE decision_kind IN ('rep_match_confirmed','rep_match_rejected')"
            ).fetchall()
        }
        out: list[dict] = []
        for r in rows:
            if r["target_id"] in decided:
                continue
            try:
                payload = json.loads(r["after_value"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("decision") != "review_needed":
                continue
            enc_id = r["target_id"]
            # Pull the encounter itself + the candidate contact
            enc = conn.execute(
                "SELECT id, contact_id, conference_id, captured_at, capture_mode, "
                "structured_json FROM encounters WHERE id = ?", (enc_id,),
            ).fetchone()
            if not enc or enc["contact_id"]:
                continue  # already attached → skip
            try:
                struct = json.loads(enc["structured_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                struct = {}
            candidate_id = payload.get("contact_id")
            candidate = None
            if candidate_id:
                cand_row = conn.execute(
                    "SELECT id, primary_name, primary_email, primary_company, "
                    "primary_title FROM contacts WHERE id = ?", (candidate_id,),
                ).fetchone()
                if cand_row:
                    candidate = dict(cand_row)
            out.append({
                "encounter_id": enc_id,
                "captured_at": enc["captured_at"],
                "conference_id": enc["conference_id"],
                "encounter_lead": {
                    "name": struct.get("name"),
                    "title": struct.get("title") or struct.get("role"),
                    "company": struct.get("company"),
                    "email": struct.get("email"),
                    "what_discussed": (struct.get("what_discussed") or "")[:200],
                },
                "candidate_contact": candidate,
                "confidence": payload.get("confidence"),
                "factors": payload.get("factors") or {},
                "logged_at": r["decided_at"],
            })
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


def confirm(encounter_id: str, *, decided_by: str = "ui") -> dict:
    """Yes — same person. Attach the encounter to the candidate + cascade."""
    payload = _latest_review_payload(encounter_id)
    if not payload:
        raise ValueError("no review_needed decision for this encounter")
    candidate_id = payload.get("contact_id")
    if not candidate_id:
        raise ValueError("review decision has no candidate contact_id")
    entity_resolution.attach_encounter_to_contact(encounter_id, candidate_id)
    db.log_feedback(
        decision_kind="rep_match_confirmed",
        target_kind="encounter", target_id=encounter_id,
        after={"contact_id": candidate_id, "decision": "confirmed",
               "from_factors": payload.get("factors")},
        reason="manually confirmed via review queue",
        decided_by=decided_by,
    )
    # Cascade: arc + nudge
    try:
        verdict = arc.classify(candidate_id, use_llm=True)
        arc_dict = {
            "kind": verdict.kind, "confidence": verdict.confidence,
            "summary": verdict.summary,
        }
    except Exception:  # noqa: BLE001
        arc_dict = None
    try:
        nudge_state = nudge.evaluate(candidate_id)
    except Exception:  # noqa: BLE001
        nudge_state = None
    return {"status": "confirmed", "contact_id": candidate_id,
            "arc": arc_dict, "nudge": nudge_state}


def reject(encounter_id: str, *, decided_by: str = "ui",
           reason: Optional[str] = None) -> dict:
    """No — different person. Create a fresh contact from the encounter."""
    conn = db.get_conn()
    try:
        enc = conn.execute(
            "SELECT structured_json FROM encounters WHERE id = ?", (encounter_id,),
        ).fetchone()
    finally:
        conn.close()
    if not enc:
        raise ValueError("encounter not found")
    try:
        struct = json.loads(enc["structured_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        struct = {}
    new_cid = entity_resolution.create_contact_from_encounter(struct)
    entity_resolution.attach_encounter_to_contact(encounter_id, new_cid)
    db.log_feedback(
        decision_kind="rep_match_rejected",
        target_kind="encounter", target_id=encounter_id,
        after={"contact_id": new_cid, "decision": "rejected_match"},
        reason=reason or "different person — split into new contact",
        decided_by=decided_by,
    )
    try:
        verdict = arc.classify(new_cid, use_llm=True)
        arc_dict = {
            "kind": verdict.kind, "confidence": verdict.confidence,
            "summary": verdict.summary,
        }
    except Exception:  # noqa: BLE001
        arc_dict = None
    return {"status": "split_into_new_contact", "contact_id": new_cid, "arc": arc_dict}


def _latest_review_payload(encounter_id: str) -> Optional[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT after_value FROM feedback "
            "WHERE decision_kind = 'entity_resolution' AND target_id = ? "
            "ORDER BY decided_at DESC LIMIT 1", (encounter_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["after_value"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
