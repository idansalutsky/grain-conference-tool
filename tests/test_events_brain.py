"""Events Brain — an approved discovery becomes a REAL, scored conference.

Option B: the brain's discovery is no longer a dead-end memory entry. When a
discovered event is approved it is promoted into the conferences table via the
same creator the Discovery page uses (dedup-safe), and the no-key placeholder
can never become a real conference.
"""
from __future__ import annotations

from grain import db, discovery


def _count_conferences() -> int:
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM conferences").fetchone()[0]
    finally:
        conn.close()


def test_real_payload_becomes_a_scored_conference():
    before = _count_conferences()
    res = discovery.create_conference_from_payload(
        {
            "name": "Test LATAM Treasury Forum 2026", "city": "Bogota",
            "country": "Colombia", "region": "LATAM", "vertical": "treasury",
            "start_date": "2026-10-01", "estimated_attendance": 500,
            "source_url": "https://example.com/x", "why_relevant": "treasurers",
        },
        decided_by="test", source="events_brain",
    )
    assert res["created"] is True
    assert res["conference_id"]
    assert _count_conferences() == before + 1
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT tier, score FROM conferences WHERE id = ?",
            (res["conference_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["tier"] in ("A", "B", "C")
    assert row["score"] is not None and row["score"] > 0


def test_placeholder_is_refused():
    """The no-key 'sample - configure a search key…' notice must never become a
    real conference, even if a human clicks approve."""
    before = _count_conferences()
    res = discovery.create_conference_from_payload(
        {
            "name": "sample - configure a search key to discover real events",
            "provenance": "placeholder (no search key)", "region": "LATAM",
        },
        decided_by="test", source="events_brain",
    )
    assert res["created"] is False
    assert res.get("skipped") == "placeholder"
    assert res["conference_id"] is None
    assert _count_conferences() == before  # nothing added


def test_creation_is_idempotent_on_name():
    payload = {
        "name": "Test Dedup Summit 2026", "region": "EU", "vertical": "payments",
        "start_date": "2026-06-01", "source_url": "https://example.com/d",
    }
    first = discovery.create_conference_from_payload(payload, source="events_brain")
    assert first["created"] is True
    n = _count_conferences()
    second = discovery.create_conference_from_payload(payload, source="events_brain")
    assert second["created"] is False
    assert second["conference_id"] == first["conference_id"]
    assert _count_conferences() == n  # no duplicate row
