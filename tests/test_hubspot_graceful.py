"""HubSpot graceful-path tests — no real portal token is present, so we prove
the no-token, bad-token, and dry-run paths return correct STRUCTURED results
and never crash the endpoint.

These assert the contract the brief requires:
  - bad/expired/no token  -> clear {connected: false, reason} (never a 500)
  - dry-run default        -> synthetic OK whose payload carries grain_* intel
  - /status                -> the single "is this working?" check, never leaks
                              the token, never raises

HubSpot HTTP calls are mocked so the suite is hermetic (no network).
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from grain import db
from grain import hubspot as hs
from grain.api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fake httpx responses
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text

    def json(self):
        return self._json


def _no_token():
    """Force config to report no HubSpot token regardless of env/.env."""
    return patch.object(hs.config, "HUBSPOT_PRIVATE_APP_TOKEN", None)


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------
def test_connection_no_token():
    with _no_token():
        out = hs.test_connection()
    assert out == {"connected": False, "reason": "no_token"}


def test_connection_bad_token_maps_to_invalid_token():
    bad = _Resp(401, text="auth error")
    with patch("httpx.Client.get", return_value=bad):
        out = hs.test_connection("pat-bad")
    assert out["connected"] is False
    assert out["reason"] == "invalid_token"
    assert out["status_code"] == 401


def test_connection_network_error_is_structured_not_raised():
    with patch("httpx.Client.get", side_effect=httpx.ConnectError("boom")):
        out = hs.test_connection("pat-anything")
    assert out["connected"] is False
    assert out["reason"].startswith("network_error")


def test_connection_timeout_is_structured():
    with patch("httpx.Client.get", side_effect=httpx.ReadTimeout("slow")):
        out = hs.test_connection("pat-anything")
    assert out["connected"] is False
    assert out["reason"].startswith("network_error")


def test_connection_ok_parses_portal():
    ok = _Resp(200, json_body={"portalId": 12345, "accountType": "STANDARD",
                               "timeZone": "Asia/Jerusalem", "uiDomain": "app.hubspot.com"})
    with patch("httpx.Client.get", return_value=ok):
        out = hs.test_connection("pat-good")
    assert out["connected"] is True
    assert out["portal"]["portal_id"] == 12345


# ---------------------------------------------------------------------------
# ensure_custom_properties
# ---------------------------------------------------------------------------
def test_ensure_props_no_token():
    with _no_token():
        out = hs.ensure_custom_properties()
    assert out["ok"] is False
    assert out["reason"] == "no_token"
    assert out["created"] == [] and out["failed"] == []


def test_ensure_props_bad_token_short_circuits_with_one_reason():
    """A bad token must yield ONE clear invalid_token reason, not 8 x 401s."""
    bad = _Resp(401, text="auth error")
    with patch("httpx.Client.get", return_value=bad):
        out = hs.ensure_custom_properties("pat-bad")
    assert out["ok"] is False
    assert out["reason"] == "invalid_token"
    assert out["failed"] == []  # short-circuited before per-property writes


def test_ensure_props_creates_then_treats_existing_as_success():
    good_account = _Resp(200, json_body={"portalId": 1})
    # group create returns 409 (exists); each property: first create 201, but
    # to keep it simple, return 409 (already exists) for all -> existing.
    def fake_post(url, **kwargs):
        return _Resp(409, text="already exists")
    with patch("httpx.Client.get", return_value=good_account), \
         patch("httpx.Client.post", side_effect=fake_post):
        out = hs.ensure_custom_properties("pat-good")
    assert out["ok"] is True
    assert set(out["existing"]) == set(hs.CUSTOM_PROPS)
    assert out["failed"] == []


def test_property_definitions_use_valid_hubspot_types():
    """Every grain_* property must declare a valid HubSpot type+fieldType pair."""
    valid_types = {"string", "enumeration", "bool", "datetime", "date", "number"}
    valid_fieldtypes = {"text", "textarea", "select", "booleancheckbox",
                        "date", "number", "checkbox", "radio"}
    for name in hs.CUSTOM_PROPS:
        spec = hs._PROP_DEFINITIONS[name]
        assert spec["type"] in valid_types, name
        assert spec["fieldType"] in valid_fieldtypes, name
        # enumeration/bool must carry options
        if spec["type"] in ("enumeration", "bool"):
            assert spec.get("options"), name


# ---------------------------------------------------------------------------
# Dry-run push — synthetic OK carrying grain_* intelligence
# ---------------------------------------------------------------------------
def _seed_contact(cid: str) -> None:
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM encounters WHERE contact_id=?", (cid,))
        conn.execute("DELETE FROM contacts WHERE id=?", (cid,))
        conn.execute(
            "INSERT INTO contacts (id, primary_name, primary_email, primary_company, "
            "primary_title, arc_verdict, arc_summary, arc_confidence, nudge_active, "
            "nudge_text, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, "Dana Levi", "dana@acme.io", "Acme FX", "VP Treasury",
             "warming", "Asked about settlement twice.", 0.82, 1,
             "Send the deck Friday.", db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO conferences (id, name, created_at, updated_at) "
            "VALUES (?,?,?,?)", ("conf-hs", "Money20/20", db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT INTO encounters (id, contact_id, conference_id, captured_at, "
            "followup_draft) VALUES (?,?,?,?,?)",
            (f"enc-{cid}", cid, "conf-hs", db.now_iso(),
             "Hi Dana, great chatting..."),
        )
    finally:
        conn.close()


def test_dry_run_push_carries_grain_intelligence():
    cid = "hs-dry-contact"
    _seed_contact(cid)
    with _no_token():  # no token -> dry_run defaults true
        out = hs.push_contact(cid)
    assert out["ok"] is True and out["dry_run"] is True
    props = out["payload"]["properties"]
    assert props["email"] == "dana@acme.io"
    assert props["grain_arc_verdict"] == "warming"
    assert props["grain_arc_confidence"] == "0.82"
    assert props["grain_nudge_active"] == "true"
    assert props["grain_nudge_text"] == "Send the deck Friday."
    assert props["grain_followup_draft"].startswith("Hi Dana")
    assert props["grain_source_event"] == "Money20/20"


def test_push_missing_contact_is_structured():
    with _no_token():
        out = hs.push_contact("no-such-contact-id")
    assert out["ok"] is False
    assert out["error"] == "contact_not_found"


def test_explicit_dry_run_false_without_token_refuses_blind_write():
    """dry_run=False but no token must NOT attempt a write — clear error."""
    cid = "hs-dry-contact-2"
    _seed_contact(cid)
    with _no_token():
        out = hs.push_contact(cid, dry_run=False)
    assert out["ok"] is False
    assert out["error"] == "no HubSpot token"


# ---------------------------------------------------------------------------
# /api/hubspot/status — the single readiness check
# ---------------------------------------------------------------------------
def test_status_no_token():
    with patch("grain.hubspot._token_source", return_value="none"):
        r = client.get("/api/hubspot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["dry_run"] is True
    assert body["token_source"] == "none"
    assert body["reason"] == "no_token"


def test_status_bad_token_is_not_connected_no_500():
    with patch("grain.hubspot._token_source", return_value="in_app"), \
         patch("grain.hubspot.test_connection",
               return_value={"connected": False, "reason": "invalid_token",
                             "status_code": 401}):
        r = client.get("/api/hubspot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["reason"] == "invalid_token"
    assert body["token_source"] == "in_app"


def test_status_connected_reports_ready_and_portal():
    with patch("grain.hubspot._token_source", return_value="in_app"), \
         patch("grain.hubspot.test_connection",
               return_value={"connected": True, "reason": "ok",
                             "portal": {"portal_id": 42}}):
        r = client.get("/api/hubspot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["properties_ready"] is True
    assert body["portal"]["portal_id"] == 42


def test_setup_endpoint_no_token_returns_200_not_error():
    with _no_token():
        r = client.post("/api/hubspot/setup")
    assert r.status_code == 200
    assert r.json()["reason"] == "no_token"
