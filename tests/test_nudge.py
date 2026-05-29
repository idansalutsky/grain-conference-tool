"""Calibrated nudge — silent on weak signal."""
from __future__ import annotations

import json
import uuid

from grain import db, nudge


def _seed_contact_and_encounters(arc: str, arc_conf: float, encounters: list[dict]) -> str:
    cid = uuid.uuid4().hex
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO contacts (id, primary_name, primary_company, "
            "primary_title, arc_verdict, arc_confidence, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, "Test Person", "Test Co", "CFO", arc, arc_conf,
             db.now_iso(), db.now_iso()),
        )
        for i, e in enumerate(encounters):
            conn.execute(
                "INSERT INTO encounters (id, contact_id, captured_at, sentiment, "
                "meeting_requested, structured_json, soft_signals_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex, cid,
                    e["captured_at"], e.get("sentiment", 3),
                    1 if e.get("meeting_requested") else 0,
                    json.dumps(e.get("structured", {})),
                    json.dumps(e.get("soft_signals", [])),
                ),
            )
    finally:
        conn.close()
    return cid


def test_warming_with_two_encounters_no_meeting_fires():
    cid = _seed_contact_and_encounters(
        arc="warming", arc_conf=0.85,
        encounters=[
            {"captured_at": "2026-03-01T00:00:00+00:00", "sentiment": 4,
             "structured": {"company": "Acme"}},
            {"captured_at": "2026-05-15T00:00:00+00:00", "sentiment": 5,
             "structured": {"company": "Acme"},
             "soft_signals": ["explicit_pain"]},
        ],
    )
    out = nudge.evaluate(cid)
    assert out["nudge_active"] is True
    assert out["nudge_text"]


def test_warming_meeting_requested_not_booked_nudges_to_confirm():
    """A meeting that was REQUESTED but not confirmed/booked is the hottest
    lead — it must still surface a nudge ('confirm the meeting / lock the
    time'), NOT go silent. (Previously this was wrongly suppressed.)"""
    cid = _seed_contact_and_encounters(
        arc="warming", arc_conf=0.85,
        encounters=[
            {"captured_at": "2026-03-01T00:00:00+00:00", "sentiment": 4,
             "meeting_requested": True, "structured": {"company": "Acme"}},
            {"captured_at": "2026-05-15T00:00:00+00:00", "sentiment": 5,
             "structured": {"company": "Acme"}},
        ],
    )
    out = nudge.evaluate(cid)
    assert out["nudge_active"] is True
    assert out["meeting_to_confirm"] is True
    assert "confirm the meeting" in out["nudge_text"].lower()


def test_warming_meeting_booked_suppressed():
    """A CONFIRMED/booked meeting genuinely needs no nudge — stay silent."""
    cid = _seed_contact_and_encounters(
        arc="warming", arc_conf=0.85,
        encounters=[
            {"captured_at": "2026-03-01T00:00:00+00:00", "sentiment": 4,
             "meeting_requested": True, "structured": {"company": "Acme"},
             "soft_signals": ["meeting_booked"]},
            {"captured_at": "2026-05-15T00:00:00+00:00", "sentiment": 5,
             "structured": {"company": "Acme"}},
        ],
    )
    out = nudge.evaluate(cid)
    assert out["nudge_active"] is False
    assert any("meeting already" in r for r in out["why_suppressed"])


def test_flat_contact_suppressed():
    cid = _seed_contact_and_encounters(
        arc="flat", arc_conf=0.5,
        encounters=[
            {"captured_at": "2026-03-01T00:00:00+00:00", "sentiment": 3,
             "structured": {"company": "Acme"}},
            {"captured_at": "2026-05-15T00:00:00+00:00", "sentiment": 3,
             "structured": {"company": "Acme"}},
        ],
    )
    out = nudge.evaluate(cid)
    assert out["nudge_active"] is False
    assert any("not 'warming'" in r for r in out["why_suppressed"])


def test_old_contact_suppressed_by_recency():
    cid = _seed_contact_and_encounters(
        arc="warming", arc_conf=0.85,
        encounters=[
            {"captured_at": "2024-01-01T00:00:00+00:00", "sentiment": 4,
             "structured": {"company": "Acme"}},
            {"captured_at": "2024-03-01T00:00:00+00:00", "sentiment": 4,
             "structured": {"company": "Acme"}},
        ],
    )
    out = nudge.evaluate(cid)
    assert out["nudge_active"] is False
    assert any("last touch" in r for r in out["why_suppressed"])
