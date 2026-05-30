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


def test_mentioned_events_signal_aggregates_and_marks_tracked():
    """Events buyers mention in conversation become a ranked signal; an event we
    already track is marked tracked, an unknown one is a discovery candidate."""
    import json as _json
    # A tracked conference to match against.
    discovery.create_conference_from_payload(
        {"name": "Sibos Signal Test 2026", "region": "EU", "vertical": "payments",
         "start_date": "2026-10-01", "source_url": "https://example.com/sibos"},
        source="test",
    )
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO contacts (id, primary_name, created_at, "
            "updated_at) VALUES ('c-me1','Mentioner One',?,?)",
            (db.now_iso(), db.now_iso()),
        )
        for eid, cid, evs in [
            ("e-me1", "c-me1", ["Sibos Signal Test 2026", "Phantom Forum 2026"]),
            ("e-me2", "c-me1", ["Phantom Forum 2026"]),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO encounters (id, contact_id, conference_id, "
                "captured_at, capture_mode, structured_json) VALUES (?,?,?,?,?,?)",
                (eid, cid, None, db.now_iso(), "telegram",
                 _json.dumps({"name": "Mentioner One",
                              "mentioned_events": evs})),
            )
    finally:
        conn.close()
    sig = {e["name"]: e for e in discovery.mentioned_events_signal()}
    assert "Sibos Signal Test 2026" in sig and sig["Sibos Signal Test 2026"]["tracked"] is True
    assert "Phantom Forum 2026" in sig
    assert sig["Phantom Forum 2026"]["tracked"] is False
    assert sig["Phantom Forum 2026"]["count"] >= 2


def test_research_mentioned_events_confirms_and_reports_not_found(monkeypatch):
    """The loop: untracked buyer-mentioned events get researched — a verifiable
    upcoming one becomes a pending proposal; an unconfirmable one is reported in
    not_found (the agent says it looked and came up empty, never fabricates)."""
    import json as _json
    from grain import llm
    # Seed two untracked mentions on a contact.
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO contacts (id, primary_name, created_at, "
            "updated_at) VALUES ('c-men1','Mention Source',?,?)",
            (db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO encounters (id, contact_id, conference_id, "
            "captured_at, capture_mode, structured_json) VALUES (?,?,?,?,?,?)",
            ("e-men1", "c-men1", None, db.now_iso(), "telegram",
             _json.dumps({"name": "Mention Source",
                          "mentioned_events": ["Zephyr Treasury Forum 2099",
                                               "Defunct Ghost Expo"]})),
        )
    finally:
        conn.close()

    # Grounded search confirms only the real one, with a far-future date.
    def fake_search(query, **kw):
        return _json.dumps({"proposals": [{
            "name": "Zephyr Treasury Forum 2099", "city": "Zurich",
            "country": "Switzerland", "region": "EU", "vertical": "treasury",
            "start_date": "2099-09-01", "why_relevant": "treasurers",
            "source_url": "https://example.com/zephyr",
        }]}), []
    monkeypatch.setattr(discovery.llm, "search_grounded", fake_search)

    res = discovery.research_mentioned_events()
    names = [p["name"] for p in res["proposals"]]
    assert any("Zephyr" in n for n in names), names
    assert "Defunct Ghost Expo" in res["not_found"]


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
