"""Team & coverage admin — reps CRUD, manual event creation, assignment."""
from __future__ import annotations

from fastapi.testclient import TestClient

from grain.api.main import app

client = TestClient(app)


def test_create_rep_then_appears_in_roster():
    r = client.post("/api/reps", json={"full_name": "Dana Levi", "region": "EU"})
    assert r.status_code == 201
    rep_id = r.json()["id"]
    roster = client.get("/api/reps").json()
    assert any(x["id"] == rep_id for x in roster["items"])


def test_create_rep_rejects_bad_region():
    r = client.post("/api/reps", json={"full_name": "X", "region": "MARS"})
    assert r.status_code == 400


def test_manual_event_is_created_and_scored():
    r = client.post("/api/conferences", json={
        "name": "Cross-Border Treasury Forum",
        "start_date": "2026-08-10", "city": "Singapore", "country": "Singapore",
        "region": "APAC", "vertical": "treasury", "format": "summit",
        "themes": "cross-border, FX, treasury, settlement",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["tier"] in ("A", "B", "C")
    assert 0 <= body["score"] <= 100
    # treasury + FX themes should land it high
    assert body["score"] >= 60


def test_assign_and_unassign_coverage():
    rep = client.post("/api/reps", json={"full_name": "Coverage Tester", "region": "NA"}).json()["id"]
    conf = client.post("/api/conferences", json={"name": "Coverage Test Expo", "start_date": "2026-09-01"}).json()["id"]

    a = client.post("/api/coverage", json={"conference_id": conf, "rep_id": rep})
    assert a.status_code == 201

    # duplicate assignment is rejected
    dup = client.post("/api/coverage", json={"conference_id": conf, "rep_id": rep})
    assert dup.status_code == 409

    cov = client.get("/api/coverage", params={"conference_id": conf}).json()
    assert cov["count"] == 1 and cov["items"][0]["rep_id"] == rep
    assert cov["items"][0]["rep_name"] == "Coverage Tester"

    d = client.delete(f"/api/coverage?conference_id={conf}&rep_id={rep}")
    assert d.status_code == 200
    assert client.get("/api/coverage", params={"conference_id": conf}).json()["count"] == 0


def test_assign_unknown_rep_404():
    conf = client.post("/api/conferences", json={"name": "Ghost Coverage Event"}).json()["id"]
    r = client.post("/api/coverage", json={"conference_id": conf, "rep_id": "nope"})
    assert r.status_code == 404


def test_event_links_returns_trip_message_and_link():
    rep = client.post("/api/reps", json={"full_name": "Trip Rep", "region": "EU"}).json()["id"]
    conf = client.post("/api/conferences", json={
        "name": "FX Field Capture Expo", "start_date": "2026-10-05",
        "city": "Lisbon", "country": "Portugal",
    }).json()["id"]
    client.post("/api/coverage", json={"conference_id": conf, "rep_id": rep})

    r = client.get(f"/api/reps/{rep}/event-links")
    assert r.status_code == 200
    body = r.json()
    assert body["rep_id"] == rep
    assert body["rep_name"] == "Trip Rep"
    # one assigned event surfaced
    assert len(body["events"]) == 1
    assert body["events"][0]["id"] == conf
    assert body["events"][0]["name"] == "FX Field Capture Expo"
    # a single t.me bind link, embedded in the paste-ready message
    assert body["deep_link"].startswith("https://t.me/")
    assert "?start=" in body["deep_link"]
    assert body["deep_link"] in body["message_text"]
    assert "Trip" in body["message_text"]          # first name
    assert "FX Field Capture Expo" in body["message_text"]


def test_event_links_no_coverage_still_returns_link():
    rep = client.post("/api/reps", json={"full_name": "Lonely Rep"}).json()["id"]
    r = client.get(f"/api/reps/{rep}/event-links")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == []
    assert body["deep_link"].startswith("https://t.me/")
    assert body["deep_link"] in body["message_text"]


def test_event_links_unknown_rep_404():
    assert client.get("/api/reps/nope/event-links").status_code == 404
