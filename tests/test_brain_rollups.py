"""Grain Brain L1 — hierarchical middle-management rollups.

Proves the re-architecture the owner asked for: RETAIN every dot (L0 untouched),
aggregate BY JUDGMENT per entity (one rollup per entity, NOT a top-50 cutoff),
and flow up the chain (L2 space summaries derive from the L1 rollups).

All hermetic: no API key, no network. The deterministic features + summary are
what the bulk rebuild uses, so these tests never hit the wire. The conftest
points DATA_DIR at a temp dir and runs db.init_db() (which now includes the
brain_rollup table).

Covers:
  - rollup-per-entity (account/event/segment)
  - features reflect the dots (n_encounters / events_spanned cross-checked vs L0)
  - idempotent rebuild (UNIQUE upsert — re-running does not duplicate)
  - NO-CAP at scale (N companies -> N account rollups, dots remain in L0)
  - recompute-on-capture hook (recompute_for_contact)
  - L2 rewire (space summaries derive from rollups)
"""
from __future__ import annotations

import uuid

import pytest

from grain import db
from grain.brain import rollups, spaces


# ---------------------------------------------------------------------------
# L0 dot helpers — insert genuine contacts + encounters + a conference.
# ---------------------------------------------------------------------------
def _conf(name, vertical="treasury", region="EU", tier="A",
          audience=None) -> str:
    cid = "conf_" + uuid.uuid4().hex[:10]
    now = db.now_iso()
    import json as _json
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO conferences (id, name, vertical, region, tier, score, "
            "estimated_attendance, audience_composition_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, name, vertical, region, tier, 80.0, 2000,
             _json.dumps(audience) if audience else None, now, now),
        )
    finally:
        conn.close()
    return cid


def _contact(name, company, title="CFO", arc="warming") -> str:
    cid = "c_" + uuid.uuid4().hex[:10]
    now = db.now_iso()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO contacts (id, primary_name, primary_company, "
            "primary_title, arc_verdict, arc_summary, arc_confidence, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, name, company, title, arc, "test", 0.8, now, now),
        )
    finally:
        conn.close()
    return cid


def _encounter(contact_id, conference_id=None, meeting=False, followup=None):
    now = db.now_iso()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO encounters (id, contact_id, conference_id, captured_at, "
            "capture_mode, sentiment, meeting_requested, followup_draft) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("e_" + uuid.uuid4().hex[:10], contact_id, conference_id, now,
             "text", 4, 1 if meeting else 0, followup),
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rollup_account — one judged rollup per account; features reflect the dots
# ---------------------------------------------------------------------------
def test_rollup_account_features_reflect_dots():
    """An account with 3 encounters across 2 events shows n_encounters=3,
    events_spanned=2 — the rollup connects its L0 dots."""
    company = "DotsCo " + uuid.uuid4().hex[:6]
    e1 = _conf("DotsCo Event A " + uuid.uuid4().hex[:4])
    e2 = _conf("DotsCo Event B " + uuid.uuid4().hex[:4])
    cid = _contact("Dots Person", company, arc="warming")
    _encounter(cid, e1)
    _encounter(cid, e1)   # same event twice
    _encounter(cid, e2)   # second event

    roll = rollups.rollup_account(company)
    assert roll is not None
    f = roll["features"]
    assert f["n_contacts"] == 1
    assert f["n_encounters"] == 3      # 3 dots
    assert f["events_spanned"] == 2    # across 2 events
    assert f["account_arc"] == "warming"
    assert f["has_warming"] is True
    assert roll["priority"] > 0
    assert roll["summary"]             # judged prose exists


def test_rollup_account_arc_rolls_up_from_contacts():
    """account_arc is a judgment over per-contact arcs (any warming wins)."""
    company = "MixCo " + uuid.uuid4().hex[:6]
    c1 = _contact("Warm One", company, arc="warming")
    c2 = _contact("Flat Two", company, arc="flat")
    _encounter(c1)
    _encounter(c2)
    roll = rollups.rollup_account(company)
    assert roll["features"]["account_arc"] == "warming"
    assert roll["features"]["arc_mix"]["warming"] == 1
    assert roll["features"]["arc_mix"]["flat"] == 1
    assert roll["source_count"] == 2   # 2 contacts fed it


# ---------------------------------------------------------------------------
# rollup_event — one judged rollup per event; features reflect encounters+audience
# ---------------------------------------------------------------------------
def test_rollup_event_features_reflect_dots():
    conf_id = _conf("Event Rollup Test " + uuid.uuid4().hex[:4],
                    audience={"cfo_treasury_finance_pct": 55,
                              "engineering_product_pct": 20})
    c1 = _contact("Warm Buyer", "BuyerCo", title="CFO", arc="warming")
    c2 = _contact("Tire Kicker", "KickCo", title="Analyst", arc="tire_kicker")
    _encounter(c1, conf_id, meeting=True, followup="draft email")
    _encounter(c2, conf_id)

    roll = rollups.rollup_event(conf_id)
    assert roll is not None
    f = roll["features"]
    assert f["n_encounters"] == 2
    assert f["n_contacts_met"] == 2
    assert f["arc_mix"]["warming"] == 1
    assert f["arc_mix"]["tire_kicker"] == 1
    assert f["buying_committee_personas_hit"] == 1   # the CFO
    assert f["measured_finance_pct"] == 55           # from audience json
    assert f["follow_ups_drafted"] == 1
    assert f["worth_returning_verdict"] == "worth_returning"  # a warming arc


def test_rollup_event_untested_high_fit_still_rolled_up():
    """A high-fit event with NO encounters still gets a planning rollup (judged
    worth_attending) — nothing dropped just because it isn't worked yet."""
    conf_id = _conf("Planning Event " + uuid.uuid4().hex[:4], tier="A",
                    audience={"cfo_treasury_finance_pct": 60})
    roll = rollups.rollup_event(conf_id)
    assert roll is not None
    assert roll["features"]["n_encounters"] == 0
    assert roll["features"]["worth_returning_verdict"] == "worth_attending"


# ---------------------------------------------------------------------------
# rollup_segment — aggregates events + accounts in a vertical
# ---------------------------------------------------------------------------
def test_rollup_segment_aggregates_and_flags_gap():
    seg = "segtest_" + uuid.uuid4().hex[:6]
    _conf("Seg Event 1 " + uuid.uuid4().hex[:4], vertical=seg, tier="C")
    _conf("Seg Event 2 " + uuid.uuid4().hex[:4], vertical=seg, tier="C")
    roll = rollups.rollup_segment(seg)
    assert roll is not None
    f = roll["features"]
    assert f["n_events"] == 2
    assert f["tier_mix"]["C"] == 2
    assert f["n_accounts"] == 0
    assert f["coverage_gap"] is True   # no A-tier + no worked accounts


# ---------------------------------------------------------------------------
# Idempotent rebuild — UNIQUE(scope_type, scope_id) upsert, no duplicates
# ---------------------------------------------------------------------------
def test_rebuild_all_rollups_is_idempotent():
    company = "IdemCo " + uuid.uuid4().hex[:6]
    e = _conf("Idem Event " + uuid.uuid4().hex[:4])
    cid = _contact("Idem Person", company)
    _encounter(cid, e)

    rollups.rebuild_all_rollups()
    n_acct_1 = rollups.count_rollups("account")
    n_event_1 = rollups.count_rollups("event")
    rollups.rebuild_all_rollups()
    n_acct_2 = rollups.count_rollups("account")
    n_event_2 = rollups.count_rollups("event")
    assert n_acct_1 == n_acct_2     # re-running did NOT duplicate
    assert n_event_1 == n_event_2
    # The specific account rollup exists exactly once.
    roll = rollups.get_rollup("account", rollups._norm_company_key(company))
    assert roll is not None


# ---------------------------------------------------------------------------
# THE POINT — no-cap at scale: N companies -> N account rollups, dots stay in L0
# ---------------------------------------------------------------------------
def test_no_cap_at_scale_300_companies():
    """Insert encounters for 300 DISTINCT companies, rebuild, and prove there
    are ~300 ACCOUNT rollups (one per company — NOT capped at 50), each judged,
    while the raw dots remain in L0. This is the whole point of the re-arch."""
    conn = db.get_conn()
    try:
        before_companies = conn.execute(
            "SELECT COUNT(DISTINCT primary_company) FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''"
        ).fetchone()[0]
        before_enc = conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
    finally:
        conn.close()

    N = 300
    arcs = ["warming", "flat", "cooling", "tire_kicker"]
    now = db.now_iso()
    conn = db.get_conn()
    try:
        for i in range(N):
            cid = "scale_c_" + uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO contacts (id, primary_name, primary_company, "
                "primary_title, arc_verdict, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid, f"Scale Person {i}", f"ScaleCo {i:04d} Ltd", "CFO",
                 arcs[i % 4], now, now),
            )
            for _ in range(2):  # 2 raw dots per company
                conn.execute(
                    "INSERT INTO encounters (id, contact_id, captured_at, "
                    "capture_mode, sentiment, meeting_requested) "
                    "VALUES (?,?,?,?,?,?)",
                    ("scale_e_" + uuid.uuid4().hex[:12], cid, now, "text", 4, 0),
                )
    finally:
        conn.close()

    rollups.rebuild_all_rollups()

    conn = db.get_conn()
    try:
        after_companies = conn.execute(
            "SELECT COUNT(DISTINCT primary_company) FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''"
        ).fetchone()[0]
        after_enc = conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
    finally:
        conn.close()

    acct_rollups = rollups.count_rollups("account")
    # ONE account rollup per distinct company — NOT capped at 50.
    assert acct_rollups == after_companies
    assert acct_rollups >= before_companies + N
    assert acct_rollups > 50      # the dumb cutoff is gone
    # Raw dots remain fully in L0 (nothing dropped).
    assert after_enc == before_enc + 2 * N
    # Every account rollup is JUDGED (priority + summary + features).
    all_acct = rollups.list_rollups("account", limit=10_000)
    assert all(r.get("priority") is not None and r.get("summary")
               and r.get("features") for r in all_acct)


# ---------------------------------------------------------------------------
# Recompute-on-capture hook
# ---------------------------------------------------------------------------
def test_recompute_for_contact_updates_account_and_event_rollups():
    company = "RecomputeCo " + uuid.uuid4().hex[:6]
    e = _conf("Recompute Event " + uuid.uuid4().hex[:4])
    cid = _contact("Recompute Person", company, arc="warming")
    _encounter(cid, e)

    out = rollups.recompute_for_contact(cid)
    assert out["account"] == rollups._norm_company_key(company)
    assert e in out["events"]
    # The rollups now exist and reflect the single encounter.
    acct = rollups.get_rollup("account", rollups._norm_company_key(company))
    assert acct["features"]["n_encounters"] == 1
    evt = rollups.get_rollup("event", e)
    assert evt["features"]["n_encounters"] == 1

    # Add a second encounter for the same contact, recompute → reflects 2 dots.
    _encounter(cid, e)
    rollups.recompute_for_contact(cid)
    acct2 = rollups.get_rollup("account", rollups._norm_company_key(company))
    assert acct2["features"]["n_encounters"] == 2


def test_recompute_for_contact_is_best_effort_on_missing():
    out = rollups.recompute_for_contact("does-not-exist")
    assert out["account"] is None
    assert out["events"] == []


# ---------------------------------------------------------------------------
# L2 rewire — the space summaries derive from the L1 rollups (not a top-50 pile)
# ---------------------------------------------------------------------------
def test_l2_relationship_summary_reflects_account_rollups():
    company = "L2Co " + uuid.uuid4().hex[:6]
    e = _conf("L2 Event " + uuid.uuid4().hex[:4])
    cid = _contact("L2 Warm Person", company, arc="warming")
    _encounter(cid, e)

    rollups.rebuild_all_rollups()
    spaces.rebuild_space_summaries_from_rollups(use_llm=False)
    summ = spaces.get_summary("relationship")
    assert summ is not None and summ["summary"]
    # item_count of the relationship space summary == number of account rollups.
    assert summ["item_count"] == rollups.count_rollups("account")
    # Deterministic relationship summary names that it judged accounts (not a
    # salience-truncated pile).
    assert "account" in summ["summary"].lower()


def test_l2_events_summary_reflects_event_rollups():
    rollups.rebuild_all_rollups()
    spaces.rebuild_space_summaries_from_rollups(use_llm=False)
    summ = spaces.get_summary("events")
    assert summ is not None and summ["summary"]
    assert summ["item_count"] == rollups.count_rollups("event")


def test_l2_gaps_summary_from_segment_rollups():
    seg = "gaptest_" + uuid.uuid4().hex[:6]
    _conf("Gap Event " + uuid.uuid4().hex[:4], vertical=seg, tier="C")
    rollups.rebuild_all_rollups()
    spaces.rebuild_space_summaries_from_rollups(use_llm=False)
    summ = spaces.get_summary("gaps")
    assert summ is not None
    assert "gap" in summ["summary"].lower()


# ---------------------------------------------------------------------------
# upsert validation
# ---------------------------------------------------------------------------
def test_upsert_rollup_rejects_unknown_scope():
    with pytest.raises(ValueError):
        rollups.upsert_rollup("nope", "x", title="t", summary="s",
                              features={}, priority=0.1, source_count=0)
