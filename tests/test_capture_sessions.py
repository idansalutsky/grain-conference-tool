"""Capture-session tests — real-life encounters arrive as a BURST.

Covers: time-window stitching (badge → context merges into one encounter),
auto-split (two people back-to-back stay separate), the window boundary,
shared-contact (phone) capture, phone as an identity signal, and editing a
capture (re-resolve + orphan cleanup).

Offline: LLM extraction is patched; everything else is deterministic.
Each test uses a UNIQUE rep_id so the rep's "most recent encounter" lookup is
isolated from the shared session DB.
"""
from __future__ import annotations

from unittest.mock import patch

from grain import db, entity_resolution as er, voice


def _enc_count_for_contact(contact_id: str) -> int:
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM encounters WHERE contact_id = ?", (contact_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stitching: badge (name) → voice/text (context) = ONE encounter
# ---------------------------------------------------------------------------
def test_burst_badge_then_context_merges_into_one_encounter():
    rep = "rep-stitch-merge"
    with patch("grain.voice.llm.image_to_lead", return_value={
        "name": "Dana Levi", "company": "PayCo", "title": "CFO",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": None,
    }):
        first = voice.capture_image_fast(image_path=__file__, rep_id=rep)
    assert first["contact_id"]

    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": None, "company": None, "title": None, "vertical": None,
        "sentiment": 5, "soft_signals": ["wants_meeting", "explicit_pain"],
        "meeting_requested": True,
        "what_discussed": "wants a demo; big FX spread leakage on payouts",
        "transcript": "...",
    }):
        second = voice.capture_text_fast(text="wants a demo, big FX pain", rep_id=rep)

    assert second["stitched"] is True
    assert second["contact_id"] == first["contact_id"]          # same person
    assert _enc_count_for_contact(first["contact_id"]) == 1      # ONE encounter

    s = second["structured"]
    assert s["name"] == "Dana Levi"          # kept from the badge
    assert "FX" in (s["what_discussed"] or "")  # gained from the voice note
    assert s["meeting_requested"] is True       # OR'd in
    assert s["sentiment"] == 5                  # max


# ---------------------------------------------------------------------------
# Auto-split: two different people in the window stay separate
# ---------------------------------------------------------------------------
def test_two_people_back_to_back_do_not_merge():
    rep = "rep-stitch-split"
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Alice Anderson", "company": "AlphaCo", "title": "CFO",
        "vertical": "payments", "sentiment": 4, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "a", "transcript": "",
    }):
        a = voice.capture_text_fast(text="met Alice", rep_id=rep)
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Bob Brown", "company": "BetaCo", "title": "Treasurer",
        "vertical": "payments", "sentiment": 4, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "b", "transcript": "",
    }):
        b = voice.capture_text_fast(text="met Bob", rep_id=rep)

    assert b.get("stitched") is False
    assert a["contact_id"] != b["contact_id"]


# ---------------------------------------------------------------------------
# Window boundary: a closed window starts a new encounter
# ---------------------------------------------------------------------------
def test_closed_window_does_not_stitch(monkeypatch):
    monkeypatch.setattr(voice, "_stitch_window_seconds", lambda: 0)
    rep = "rep-stitch-window"
    payload = {
        "name": "Window Person", "company": "WinCo", "title": "CFO",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "x", "transcript": "",
    }
    with patch("grain.voice.llm.text_to_lead", return_value=payload):
        a = voice.capture_text_fast(text="first", rep_id=rep)
        b = voice.capture_text_fast(text="second", rep_id=rep)
    # Same person, but window=0 → two distinct touches (both resolve to one
    # contact, but as separate encounters).
    assert b.get("stitched") is False
    assert a["contact_id"] == b["contact_id"]
    assert _enc_count_for_contact(a["contact_id"]) == 2


# ---------------------------------------------------------------------------
# Shared contact card (phone) capture
# ---------------------------------------------------------------------------
def test_contact_share_capture_stores_phone():
    res = voice.capture_contact_fast(
        name="Phone Person", phone="+1 (415) 555-1212", rep_id="rep-contact-x")
    assert res["contact_id"]
    assert res["structured"]["phone"] == "+1 (415) 555-1212"
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT phone FROM contacts WHERE id = ?",
                           (res["contact_id"],)).fetchone()
    finally:
        conn.close()
    assert row["phone"] == "+1 (415) 555-1212"


def test_contact_share_empty_is_rejected():
    res = voice.capture_contact_fast(name=None, phone=None, rep_id="rep-contact-y")
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# Phone as an identity signal
# ---------------------------------------------------------------------------
def test_phone_normalisation_and_match():
    assert er._phone_match("+1 (415) 555-1212", "415.555.1212") == 1.0
    assert er._phone_match("415-555-1212", "415-555-9999") == 0.0
    assert er._phone_match("12345", "12345") == 0.0   # too short to trust


def test_phone_plus_name_auto_merges():
    f = {"email_match": 0, "linkedin_match": 0, "phone_match": 1.0,
         "name_similarity": 0.7, "company_similarity": 0.0}
    assert er._score_factors(f) >= 0.85          # strong → auto-merge band
    f_phone_only = {**f, "name_similarity": 0.0}
    assert er._score_factors(f_phone_only) <= 0.8  # phone-only → review, not auto


# ---------------------------------------------------------------------------
# Editing a capture re-resolves + cleans the orphan
# ---------------------------------------------------------------------------
def test_edit_capture_reresolves_and_cleans_orphan():
    rep = "rep-edit-x"
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Misherd Naam", "company": "Wrongco", "title": "CFO",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "z", "transcript": "",
    }):
        cap = voice.capture_text_fast(text="mis-heard", rep_id=rep)
    old_contact = cap["contact_id"]
    enc_id = cap["encounter_id"]

    out = voice.edit_encounter(enc_id, {"name": "Correct Name", "company": "RightCo"})
    assert out["ok"]
    assert out["structured"]["name"] == "Correct Name"
    new_contact = out["contact_id"]
    assert new_contact != old_contact          # re-pointed to a fresh identity
    # old contact had only this encounter → cleaned up
    conn = db.get_conn()
    try:
        gone = conn.execute("SELECT 1 FROM contacts WHERE id = ?", (old_contact,)).fetchone()
    finally:
        conn.close()
    assert gone is None
