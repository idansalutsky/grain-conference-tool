"""API surface — assert each router responds shape-correctly."""
from __future__ import annotations

from fastapi.testclient import TestClient

from grain.api.main import app


client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_list_conferences_empty_ok():
    r = client.get("/api/conferences?limit=1")
    assert r.status_code == 200
    assert "items" in r.json()


def test_list_people_ok():
    r = client.get("/api/people?limit=1")
    assert r.status_code == 200


def test_list_contacts_ok():
    r = client.get("/api/contacts?limit=1")
    assert r.status_code == 200


def test_planning_endpoints_ok():
    assert client.get("/api/planning/coverage").status_code == 200
    assert client.get("/api/planning/clusters").status_code == 200
    assert client.get("/api/planning/gaps").status_code == 200


def test_settings_parameters_listed():
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert len(r.json()["parameters"]) >= 7  # at least the 7 scoring factors


def test_setting_update_persists():
    body = {"key": "scoring.vertical_fit", "value": 0.27}
    r = client.put("/api/settings", json=body)
    assert r.status_code == 200
    again = client.get("/api/settings").json()
    cur = next(p["current"] for p in again["parameters"]
               if p["key"] == "scoring.vertical_fit")
    assert abs(float(cur) - 0.27) < 0.001


def test_add_and_delete_person():
    r = client.post("/api/people", json={
        "full_name": "Test API Person", "title": "CFO",
        "company_name": "Acme Test",
    })
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["persona"] == "BUYER"
    r = client.delete(f"/api/people/{pid}")
    assert r.status_code == 200
    r = client.get(f"/api/people/{pid}")
    assert r.status_code == 404


def test_telegram_issue_token_with_missing_rep_returns_404():
    r = client.post("/api/telegram/issue-token", json={"rep_id": "no-such-rep"})
    assert r.status_code == 404


def test_telegram_bot_info():
    r = client.get("/api/telegram/bot-info")
    assert r.status_code == 200
    assert "configured_username" in r.json()


def test_hubspot_push_dry_run_for_missing_contact():
    r = client.post("/api/hubspot/push/no-such-contact")
    assert r.status_code == 404
