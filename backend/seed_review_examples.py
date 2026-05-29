"""Seed 2 ambiguous-match examples so the Review Queue has something to show.

Two real cases the resolver should flag for human judgment:

1. Sarah Chen (encountered at Booking.com) vs an existing canonical
   Sarah Cohen at Booking Holdings — similar name + same canonical company
   → high confidence but not auto_merge (avoids the "two Marias at Booking" trap).

2. Patrick Janet at Maersk (typo'd encounter) vs an existing Patrick Janý —
   classic transliteration ambiguity.

Idempotent: re-running creates fresh review-needed encounters but doesn't
duplicate existing canonical contacts.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from grain import db, entity_resolution  # noqa: E402


def _ensure_contact(name: str, company: str, title: str, email: str | None = None) -> str:
    """Return contact_id, creating one if absent (matched on name+company)."""
    conn = db.get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM contacts WHERE primary_name = ? AND primary_company = ? LIMIT 1",
            (name, company),
        ).fetchone()
        if existing:
            return existing["id"]
        cid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO contacts (id, primary_name, primary_email, primary_company, "
            "primary_title, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (cid, name, email, company, title, db.now_iso(), db.now_iso()),
        )
        return cid
    finally:
        conn.close()


def _create_encounter(name: str, company: str, title: str,
                      what_discussed: str, rep_id: str = "rep-na-01") -> str:
    """Create a fresh encounter that the resolver will evaluate."""
    enc_id = "enc_demo_" + uuid.uuid4().hex[:10]
    structured = {
        "name": name, "company": company, "title": title,
        "vertical": "travel", "sentiment": 4,
        "soft_signals": ["wants_meeting"],
        "meeting_requested": True,
        "what_discussed": what_discussed,
    }
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO encounters (id, contact_id, conference_id, rep_id, "
            "captured_at, capture_mode, raw_input, structured_json, "
            "soft_signals_json, sentiment, meeting_requested) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (enc_id, None, "money20-20-usa-2026", rep_id,
             db.now_iso(), "demo_seed", what_discussed,
             json.dumps(structured), json.dumps(structured["soft_signals"]),
             4, 1),
        )
    finally:
        conn.close()
    return enc_id


def _force_review_log(enc_id: str, candidate_contact_id: str,
                      confidence: float, factors: dict, reason: str) -> None:
    """Write a fabricated entity_resolution decision with decision=review_needed
    so it surfaces in the queue immediately."""
    db.log_feedback(
        decision_kind="entity_resolution",
        target_kind="encounter", target_id=enc_id,
        after={
            "contact_id": candidate_contact_id,
            "decision": "review_needed",
            "confidence": confidence,
            "factors": factors,
        },
        reason=reason,
        decided_by="seed_review",
    )


def main() -> int:
    db.init_db()

    # Case 1 — name partial + same canonical company. Classic "two real
    # people at the same company" trap. We want the resolver to surface this.
    canonical_sarah_id = _ensure_contact(
        "Sarah Cohen", "Booking Holdings", "CFO", "sarah.cohen@booking.com",
    )
    enc1 = _create_encounter(
        "Sarah Chen", "Booking.com", "Head of Treasury",
        "Mentioned working on a multi-currency hedging pilot.",
    )
    _force_review_log(
        enc1, canonical_sarah_id,
        confidence=0.78,
        factors={
            "email_match": 0.0, "linkedin_match": 0.0,
            "name_similarity": 0.78, "company_similarity": 1.0,
        },
        reason="name partial match (Chen/Cohen) + same company — possible different person",
    )

    # Case 2 — transliteration ambiguity for a known senior CFO
    canonical_patrick_id = _ensure_contact(
        "Patrick Janý", "A.P. Moller-Maersk", "CFO",
        "patrick.jany@maersk.com",
    )
    enc2 = _create_encounter(
        "Patrick Janet", "Maersk", "VP Finance",
        "Talked about cross-border container shipping FX exposure.",
    )
    _force_review_log(
        enc2, canonical_patrick_id,
        confidence=0.72,
        factors={
            "email_match": 0.0, "linkedin_match": 0.0,
            "name_similarity": 0.72, "company_similarity": 1.0,
        },
        reason="name transliteration variant + same company — ambiguous",
    )

    print("Seeded 2 review-queue examples:")
    print(f"  encounter {enc1}: 'Sarah Chen @ Booking.com' vs 'Sarah Cohen @ Booking Holdings' (0.78)")
    print(f"  encounter {enc2}: 'Patrick Janet @ Maersk' vs 'Patrick Janý @ A.P. Moller-Maersk' (0.72)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
