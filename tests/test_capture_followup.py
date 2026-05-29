"""Tests for the new capture inputs (badge photo, LinkedIn URL) + the
post-event follow-up loop + the HubSpot event batch.

Offline: no key is assumed, so LLM paths exercise the deterministic fallbacks.
Where an LLM extraction would be needed, we patch it (as the existing suite does).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from grain import db, followup, hubspot, voice
from grain.api.main import app

client = TestClient(app)


@pytest.fixture()
def no_llm_key(monkeypatch):
    """Force the keyless fallback path everywhere config.OPENROUTER_API_KEY is read."""
    monkeypatch.setattr("grain.config.OPENROUTER_API_KEY", None, raising=False)
    yield


def _make_conf(cid="conf-test-evt", name="Money20/20 Test 2026"):
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conferences (id, name, region, vertical, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (cid, name, "EU", "payments", db.now_iso(), db.now_iso()),
        )
    finally:
        conn.close()
    return cid, name


# ---------------------------------------------------------------------------
# LinkedIn-URL capture
# ---------------------------------------------------------------------------
def test_linkedin_only_text_routes_to_identity_capture(no_llm_key):
    r = client.post("/api/encounters/text", json={
        "text": "https://www.linkedin.com/in/jane-doe-cfo-acmepay/",
        "rep_id": "rep-na-01",
    })
    assert r.status_code == 201
    d = r.json()
    s = d["structured"]
    assert s["linkedin"].startswith("https://www.linkedin.com/in/jane-doe")
    assert s["name"] == "Jane Doe"          # derived from slug, not invented
    assert d["contact_id"]                  # a contact was resolved/created


def test_linkedin_slug_does_not_invent_employer(no_llm_key):
    lead = voice.llm.linkedin_url_to_lead("linkedin.com/in/m-schmidt-99887766")
    assert lead["company"] is None          # never fabricate
    assert lead["linkedin"] == "linkedin.com/in/m-schmidt-99887766"


# ---------------------------------------------------------------------------
# Badge-photo capture
# ---------------------------------------------------------------------------
@patch("grain.voice.llm.image_to_lead")
def test_badge_photo_unreadable_does_not_create_contact(mock_img):
    """OCR returns no name → we refuse to create a junk contact."""
    mock_img.return_value = {"name": None, "company": None, "ocr_confidence": 0}
    res = voice.capture_image_fast(image_path=__file__, rep_id="rep-na-01")  # any path; mocked
    assert res["ok"] is False
    assert "retry" in res["reason"].lower() or "type" in res["reason"].lower()


@patch("grain.voice.llm.image_to_lead")
def test_badge_photo_success_creates_contact(mock_img):
    mock_img.return_value = {
        "name": "Badge Reader", "company": "BadgeCo", "title": "VP Finance",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": None,
    }
    res = voice.capture_image_fast(image_path=__file__, rep_id="rep-na-01")
    assert res["contact_id"]
    assert res["structured"]["name"] == "Badge Reader"


# ---------------------------------------------------------------------------
# Post-event follow-up loop
# ---------------------------------------------------------------------------
@patch("grain.voice.llm.text_to_lead")
def test_followup_is_event_and_conversation_grounded(mock_llm, no_llm_key):
    _make_conf()
    mock_llm.return_value = {
        "name": "Follow Up Target", "title": "Treasurer", "company": "PayrailsX",
        "vertical": "payments", "sentiment": 4, "soft_signals": ["explicit_pain"],
        "meeting_requested": False,
        "what_discussed": "FX spread leakage on multi-currency settlement",
        "transcript": "...",
    }
    cap = client.post("/api/encounters/text", json={
        "text": "Met the treasurer of PayrailsX, big FX leakage pain",
        "rep_id": "rep-na-01", "conference_id": "conf-test-evt",
    })
    contact_id = cap.json()["contact_id"]

    out = followup.draft_for_contact(contact_id, "conf-test-evt")
    assert out["ok"]
    assert out["event_name"] == "Money20/20 Test 2026"
    # fallback body references the event + what was discussed (not "nice to meet you")
    assert "Money20/20 Test 2026" in out["body"]
    assert "leakage" in out["body"].lower()
    assert "nice to meet you" not in out["body"].lower()

    # The draft was persisted onto the encounter so HubSpot can carry it.
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT followup_draft FROM encounters WHERE id = ?",
            (out["encounter_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["followup_draft"] == out["body"]


@patch("grain.voice.llm.text_to_lead")
def test_followup_tire_kicker_not_recommended(mock_llm, no_llm_key):
    _make_conf()
    mock_llm.return_value = {
        "name": "Tire Kicker Tk", "title": "Analyst", "company": "KickCo",
        "vertical": "payments", "sentiment": 2, "soft_signals": ["lukewarm"],
        "meeting_requested": False, "what_discussed": "vague chat", "transcript": "",
    }
    cap = client.post("/api/encounters/text", json={
        "text": "met a tire kicker", "rep_id": "rep-na-01",
        "conference_id": "conf-test-evt",
    })
    contact_id = cap.json()["contact_id"]
    # Force the arc verdict to tire_kicker directly (avoid running the classifier).
    conn = db.get_conn()
    try:
        conn.execute("UPDATE contacts SET arc_verdict='tire_kicker' WHERE id=?",
                     (contact_id,))
    finally:
        conn.close()
    out = followup.draft_for_contact(contact_id, "conf-test-evt")
    assert out["ok"]
    assert out["recommended"] is False     # don't pressure a tire-kicker


def test_followup_event_batch_endpoint(no_llm_key):
    _make_conf()
    with patch("grain.voice.llm.text_to_lead") as mock_llm:
        mock_llm.return_value = {
            "name": "Batch One", "title": "CFO", "company": "BatchCo",
            "vertical": "payments", "sentiment": 4, "soft_signals": [],
            "meeting_requested": False, "what_discussed": "cross-border payouts",
            "transcript": "",
        }
        client.post("/api/encounters/text", json={
            "text": "met batch one", "rep_id": "rep-na-01",
            "conference_id": "conf-test-evt",
        })
    r = client.post("/api/followups/event/conf-test-evt")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["event_name"] == "Money20/20 Test 2026"
    assert d["count"] >= 1
    assert all("body" in dr for dr in d["drafts"])


# ---------------------------------------------------------------------------
# HubSpot post-event batch
# ---------------------------------------------------------------------------
@patch("grain.voice.llm.text_to_lead")
def test_hubspot_push_event_dry_run_skips_no_email(mock_llm, no_llm_key):
    _make_conf(cid="conf-hs-evt", name="HS Event 2026")
    # Two clearly-different people, captured by two different reps, so capture
    # stitching never merges them: one has an email, one doesn't.
    mock_llm.return_value = {
        "name": "Quentin Withemail", "title": "CFO", "company": "HasCo",
        "email": "cfo@hasco.com", "vertical": "payments", "sentiment": 4,
        "soft_signals": [], "meeting_requested": False, "what_discussed": "x",
        "transcript": "",
    }
    client.post("/api/encounters/text", json={
        "text": "has email", "rep_id": "rep-hs-a", "conference_id": "conf-hs-evt"})
    mock_llm.return_value = {
        "name": "Zara Noemail", "title": "CFO", "company": "NoCo",
        "email": None, "vertical": "payments", "sentiment": 4,
        "soft_signals": [], "meeting_requested": False, "what_discussed": "y",
        "transcript": "",
    }
    client.post("/api/encounters/text", json={
        "text": "no email", "rep_id": "rep-hs-b", "conference_id": "conf-hs-evt"})

    out = hubspot.push_event("conf-hs-evt", dry_run=True)
    assert out["ok"]
    assert out["event_name"] == "HS Event 2026"
    assert out["pushed"] >= 1          # the one with an email
    assert out["skipped"] >= 1         # the one without
    # the skipped reason is explicit
    assert any("email" in s["reason"] for s in out["detail"]["skipped"])
