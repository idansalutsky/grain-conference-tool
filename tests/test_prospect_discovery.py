"""Prospect discovery — exclusion list, cooldown, insertion shape.

These tests stub the LLM call so they're deterministic and offline.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from grain import companies, db, llm, prospect_discovery


@pytest.fixture
def clean_db():
    conn = db.get_conn()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in ("people", "contacts", "encounters", "companies", "feedback"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()
    yield


def _fake_sonar(monkeypatch, response_prospects):
    """Patch llm.search_grounded to return a fixed list of prospects."""
    def _fake(query, system=None):
        return (
            json.dumps({"prospects": response_prospects}, ensure_ascii=False),
            [],
        )
    monkeypatch.setattr(llm, "search_grounded", _fake)


def test_discover_inserts_as_pending(clean_db, monkeypatch):
    _fake_sonar(monkeypatch, [{
        "name": "Klook",
        "domain": "klook.com",
        "hq_country": "Hong Kong",
        "industry": "online travel agency",
        "vertical": "travel",
        "employee_band": "1001-5000",
        "fx_exposure_hint": "high",
        "why_grain_fit": "Multi-currency travel marketplace.",
        "source_url": "https://example.com",
    }])

    result = prospect_discovery.discover_prospects(
        vertical_hint="travel", max_results=5,
    )

    assert len(result["prospects"]) == 1
    cid = result["prospects"][0]["company_id"]
    # Row should be is_prospect=1, approved=0
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT name, is_prospect, approved, source_kind, fx_exposure_hint "
            "FROM companies WHERE id = ?", (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["name"] == "Klook"
    assert row["is_prospect"] == 1
    assert row["approved"] == 0
    assert row["source_kind"] == "discovered"
    assert row["fx_exposure_hint"] == "high"


def test_discover_skips_already_known(clean_db, monkeypatch):
    """If a discovered prospect's normalized name matches an existing
    approved company, it should NOT be inserted as a duplicate."""
    conn = db.get_conn()
    try:
        companies.resolve_company(conn, "Klook")  # already an approved company
    finally:
        conn.close()

    _fake_sonar(monkeypatch, [{
        "name": "Klook",  # same name — should be skipped
        "domain": "klook.com",
        "vertical": "travel",
        "fx_exposure_hint": "high",
        "why_grain_fit": "...",
    }])

    result = prospect_discovery.discover_prospects(
        vertical_hint="travel", max_results=5,
    )
    assert len(result["prospects"]) == 0, (
        "discovery must not re-insert a name that already exists as approved"
    )


def test_approve_prospect_flips_approved_flag(clean_db, monkeypatch):
    _fake_sonar(monkeypatch, [{
        "name": "Eatwith",
        "domain": "eatwith.com",
        "vertical": "marketplace",
        "fx_exposure_hint": "medium",
        "why_grain_fit": "...",
    }])
    result = prospect_discovery.discover_prospects(vertical_hint="travel")
    company_id = result["prospects"][0]["company_id"]

    # Before approval
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT approved FROM companies WHERE id = ?", (company_id,)).fetchone()
        assert row["approved"] == 0
    finally:
        conn.close()

    after = prospect_discovery.approve_prospect(company_id)
    assert after["approved"] == 1
    assert after["icp_score"] is not None  # was scored on approval


def test_reject_prospect_audit_logged(clean_db, monkeypatch):
    _fake_sonar(monkeypatch, [{
        "name": "Bad Fit Co",
        "domain": "badfitco.com",
        "vertical": "other",
        "fx_exposure_hint": "low",
        "why_grain_fit": "Not actually a fit.",
    }])
    result = prospect_discovery.discover_prospects(vertical_hint=None)
    company_id = result["prospects"][0]["company_id"]
    prospect_discovery.reject_prospect(
        company_id, reason="domestic only", decided_by="test",
    )

    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT decision_kind, reason FROM feedback "
            "WHERE target_id = ? AND decision_kind = 'prospect_discovery_rejected'",
            (company_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "rejection must be logged in feedback"
    assert row["reason"] == "domestic only"


def test_list_pending_returns_only_unapproved(clean_db, monkeypatch):
    _fake_sonar(monkeypatch, [
        {"name": "First", "domain": "first.com", "vertical": "travel",
         "fx_exposure_hint": "high", "why_grain_fit": "..."},
        {"name": "Second", "domain": "second.com", "vertical": "travel",
         "fx_exposure_hint": "medium", "why_grain_fit": "..."},
    ])
    result = prospect_discovery.discover_prospects(vertical_hint="travel")
    assert len(result["prospects"]) == 2

    first_id = result["prospects"][0]["company_id"]
    prospect_discovery.approve_prospect(first_id)  # approve the first

    pending = prospect_discovery.list_pending_prospects()
    pending_names = {p["name"] for p in pending}
    assert "Second" in pending_names
    assert "First" not in pending_names
