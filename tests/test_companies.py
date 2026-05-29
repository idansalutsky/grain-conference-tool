"""Companies module — normalize, resolve, score, backfill, rollup.

No live LLM calls. The `enrich_*` paths are exercised separately via
manual run; this file covers the deterministic logic.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from grain import companies, db


# ---------------------------------------------------------------------------
# Test helpers — write a fresh isolated dataset per test
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_db():
    """Wipe companies + the rows we'll touch on people/contacts/conferences
    so each test starts from a known state."""
    conn = db.get_conn()
    try:
        # Disable FKs so we can wipe in any order
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in ("people", "contacts", "encounters", "companies", "conferences"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()
    yield


def _insert_person(*, name, company_name, conference_id,
                   persona="BUYER", persona_weight=1.0, vertical="payments"):
    pid = "p_" + uuid.uuid4().hex[:10]
    db.insert_row("people", {
        "id": pid, "full_name": name, "company_name": company_name,
        "title": "VP Finance", "conference_id": conference_id,
        "persona": persona, "persona_weight": persona_weight,
        "icp_score": persona_weight, "vertical": vertical,
        "source_kind": "test", "created_at": db.now_iso(),
    })
    return pid


def _insert_conf(cid="conf_test"):
    db.insert_row("conferences", {
        "id": cid, "name": "Test Conference", "vertical": "payments",
        "region": "NA",
    })
    return cid


# ---------------------------------------------------------------------------
# normalize_name — the dedupe key
# ---------------------------------------------------------------------------
def test_normalize_strips_legal_suffixes():
    assert companies.normalize_name("Booking Holdings Inc.") == "booking holdings"
    assert companies.normalize_name("Acme Corp") == "acme"
    assert companies.normalize_name("Globex GmbH") == "globex"
    assert companies.normalize_name("Foo LLC") == "foo"


def test_normalize_canonical_aliases():
    """Maersk surface variants all collapse to the same key."""
    assert companies.normalize_name("Maersk") == "maersk"
    assert companies.normalize_name("AP Moller Maersk") == "maersk"
    assert companies.normalize_name("apmoller maersk") == "maersk"


def test_normalize_booking_variants():
    assert companies.normalize_name("Booking.com") == "booking holdings"
    assert companies.normalize_name("Booking Holdings") == "booking holdings"
    assert companies.normalize_name("Booking Holdings Inc.") == "booking holdings"


def test_normalize_handles_unicode_and_spaces():
    assert companies.normalize_name("  Stripe   Inc.  ") == "stripe"
    # We want the rep entering "stripe-inc" to land on "stripe".
    assert companies.normalize_name("stripe-inc") == "stripeinc"  # documented


def test_normalize_empty_returns_empty():
    assert companies.normalize_name("") == ""
    assert companies.normalize_name(None) == ""


# ---------------------------------------------------------------------------
# resolve_company — upsert + variant tracking
# ---------------------------------------------------------------------------
def test_resolve_inserts_and_returns_id(clean_db):
    conn = db.get_conn()
    try:
        cid = companies.resolve_company(conn, "Wise")
        assert cid is not None
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
        assert row["name"] == "Wise"
        assert row["name_normalized"] == "wise"
    finally:
        conn.close()


def test_resolve_dedupes_via_normalization(clean_db):
    """Calling resolve twice with variant names returns the same id."""
    conn = db.get_conn()
    try:
        a = companies.resolve_company(conn, "Maersk")
        b = companies.resolve_company(conn, "AP Moller Maersk")
        assert a == b, "variants must collapse to one company"
        # Variants list should contain both surface forms
        row = conn.execute(
            "SELECT name_variants_json FROM companies WHERE id = ?", (a,)
        ).fetchone()
        import json as _json
        variants = _json.loads(row["name_variants_json"])
        assert "Maersk" in variants
        assert "AP Moller Maersk" in variants
    finally:
        conn.close()


def test_resolve_empty_name_returns_none(clean_db):
    conn = db.get_conn()
    try:
        assert companies.resolve_company(conn, "") is None
        assert companies.resolve_company(conn, None) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# score_company — the ICP formula
# ---------------------------------------------------------------------------
def test_score_basic_formula(clean_db):
    """A BUYER (weight 1.0) at one ICP-vertical conference with high FX
    should score: 0.5*1.0 + 0.2*0.333 + 0.15*1.0 + 0.15*1.0 = 0.866 → A."""
    cid = _insert_conf()
    conn = db.get_conn()
    try:
        company_id = companies.resolve_company(conn, "Stripe")
        # Set vertical + fx so score formula has all components
        conn.execute(
            "UPDATE companies SET vertical = 'payments', fx_exposure_hint = 'high' "
            "WHERE id = ?", (company_id,)
        )
    finally:
        conn.close()
    _insert_person(name="Pat Buyer", company_name="Stripe",
                   conference_id=cid, persona="BUYER", persona_weight=1.0)

    # Link the person we just inserted to the company
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE people SET company_id = ? WHERE company_name = 'Stripe'",
            (company_id,)
        )
        out = companies.score_company(conn, company_id)
    finally:
        conn.close()

    # All four components should be > 0
    assert out["score"] >= 0.8, f"expected high score, got {out}"
    assert out["tier"] == "A"
    assert out["breakdown"]["vertical_match"] == 1.0
    assert out["breakdown"]["fx_exposure_factor"] == 1.0


def test_score_tier_thresholds(clean_db):
    """Tier A ≥ 0.65, B ≥ 0.45, else C."""
    cid = _insert_conf()
    conn = db.get_conn()
    try:
        # Low-signal company: INFLUENCER persona, off-vertical, low FX
        company_id = companies.resolve_company(conn, "Tiny Local Bakery")
        conn.execute(
            "UPDATE companies SET vertical = 'other', fx_exposure_hint = 'low' "
            "WHERE id = ?", (company_id,)
        )
    finally:
        conn.close()
    _insert_person(name="Some Founder", company_name="Tiny Local Bakery",
                   conference_id=cid, persona="INFLUENCER", persona_weight=0.4)

    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE people SET company_id = ? WHERE company_name = 'Tiny Local Bakery'",
            (company_id,)
        )
        out = companies.score_company(conn, company_id)
    finally:
        conn.close()

    # 0.5*0.4 + 0.2*0.333 + 0.15*0.0 + 0.15*0.2 = 0.297 → C
    assert out["tier"] == "C"


def test_score_zero_people_handled(clean_db):
    """A company with NO people should not crash; avg_persona = 0."""
    conn = db.get_conn()
    try:
        company_id = companies.resolve_company(conn, "Empty Co")
        out = companies.score_company(conn, company_id)
        assert out["breakdown"]["avg_persona_weight"] == 0.0
        assert out["breakdown"]["people_count"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backfill — end-to-end
# ---------------------------------------------------------------------------
def test_backfill_creates_one_company_per_normalized_name(clean_db):
    """Three surface variants of Maersk should collapse to ONE company,
    and the dedupe count should report it."""
    cid = _insert_conf()
    _insert_person(name="A", company_name="Maersk", conference_id=cid)
    _insert_person(name="B", company_name="AP Moller Maersk", conference_id=cid)
    _insert_person(name="C", company_name="A.P. Moller Maersk", conference_id=cid)

    # enrich_domains=False keeps this test offline
    out = companies.backfill(enrich_domains=False)

    assert out["created"] == 1, f"3 variants must dedupe to 1, got {out}"
    assert out["surface_names_seen"] == 3
    assert out["dedupe_savings"] == 2
    assert out["linked_people"] == 3


def test_backfill_links_company_id(clean_db):
    """Every person row should get its company_id populated."""
    cid = _insert_conf()
    _insert_person(name="A", company_name="Acme", conference_id=cid)
    _insert_person(name="B", company_name="Acme Corp", conference_id=cid)
    companies.backfill(enrich_domains=False)

    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT company_id FROM people WHERE company_name LIKE 'Acme%'"
        ).fetchall()
        ids = {r["company_id"] for r in rows}
        assert len(ids) == 1, "both Acme variants must point to the same company_id"
        assert None not in ids
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# list_companies — filter behaviour
# ---------------------------------------------------------------------------
def test_list_companies_filters_approved(clean_db):
    conn = db.get_conn()
    try:
        a = companies.resolve_company(conn, "Approved Inc")
        b = companies.resolve_company(conn, "Pending Inc")
        conn.execute("UPDATE companies SET approved = 0 WHERE id = ?", (b,))
    finally:
        conn.close()

    approved = companies.list_companies(approved=True)
    pending = companies.list_companies(approved=False)

    approved_ids = {c["id"] for c in approved}
    pending_ids = {c["id"] for c in pending}
    assert a in approved_ids
    assert b in pending_ids
    assert a not in pending_ids
