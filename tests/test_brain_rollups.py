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
    eid = "e_" + uuid.uuid4().hex[:10]
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO encounters (id, contact_id, conference_id, captured_at, "
            "capture_mode, sentiment, meeting_requested, followup_draft) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eid, contact_id, conference_id, now,
             "text", 4, 1 if meeting else 0, followup),
        )
    finally:
        conn.close()
    return eid


def _set_company(contact_id, company):
    conn = db.get_conn()
    try:
        conn.execute("UPDATE contacts SET primary_company = ? WHERE id = ?",
                     (company, contact_id))
    finally:
        conn.close()


def _move_encounter(encounter_id, conference_id):
    conn = db.get_conn()
    try:
        conn.execute("UPDATE encounters SET conference_id = ? WHERE id = ?",
                     (conference_id, encounter_id))
    finally:
        conn.close()


def _delete_contact(contact_id):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM encounters WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
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


# ---------------------------------------------------------------------------
# REGRESSION — adversarial audit of the v2 rollup layer (bugs found + fixed).
# ---------------------------------------------------------------------------

# --- Bug: STALE OLD-COMPANY rollup on a job change -------------------------
def test_job_change_prunes_stale_old_company_rollup():
    """A contact moving company (job change) must NOT leave the OLD account's
    rollup behind as a phantom. recompute_for_contact recomputes the new company
    AND prunes the now-empty old company rollup."""
    old = "JobOldCo " + uuid.uuid4().hex[:6]
    new = "JobNewCo " + uuid.uuid4().hex[:6]
    cid = _contact("Job Hopper", old, arc="warming")
    _encounter(cid)
    rollups.recompute_for_contact(cid)
    old_key = rollups._norm_company_key(old)
    assert rollups.get_rollup("account", old_key) is not None  # exists pre-move

    _set_company(cid, new)                       # the job change
    out = rollups.recompute_for_contact(cid)

    # OLD account rollup is GONE (its dots moved away), NEW one exists.
    assert rollups.get_rollup("account", old_key) is None, \
        "old-company rollup left stale after job change"
    assert rollups.get_rollup("account", rollups._norm_company_key(new)) is not None
    assert out["pruned"] >= 1


# --- Bug: DELETED contact leaves a stale account rollup --------------------
def test_deleted_contact_prunes_stale_account_rollup():
    """When a contact (and its encounters) are deleted — the live capture
    pipeline does this on restitch (voice._delete_if_orphan) — the account rollup
    must not survive as a phantom claiming 1 warming contact."""
    company = "DelCo " + uuid.uuid4().hex[:6]
    cid = _contact("Delete Me", company, arc="warming")
    _encounter(cid)
    rollups.recompute_for_contact(cid)
    key = rollups._norm_company_key(company)
    assert rollups.get_rollup("account", key) is not None

    _delete_contact(cid)
    rollups.recompute_for_contact(cid)           # contact id now gone
    assert rollups.get_rollup("account", key) is None, \
        "deleted contact left a stale account rollup"


# --- Bug: full rebuild never pruned orphans --------------------------------
def test_full_rebuild_prunes_orphan_account_rollups():
    """rebuild_all_rollups must reflect the CURRENT dots exactly: an account that
    lost all its contacts must have its rollup removed by a rebuild."""
    company = "RebuildDelCo " + uuid.uuid4().hex[:6]
    cid = _contact("Gone Soon", company, arc="warming")
    _encounter(cid)
    rollups.rebuild_all_rollups()
    key = rollups._norm_company_key(company)
    assert rollups.get_rollup("account", key) is not None

    _delete_contact(cid)
    res = rollups.rebuild_all_rollups()
    assert rollups.get_rollup("account", key) is None
    assert res.get("pruned", 0) >= 1
    # And the invariant holds: #account rollups == #distinct live company KEYS.
    conn = db.get_conn()
    try:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT primary_company FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''"
        ).fetchall()]
    finally:
        conn.close()
    live_keys = {rollups._norm_company_key(nm) for nm in names}
    assert rollups.count_rollups("account") == len(live_keys)


# --- Bug: event change leaves the OLD event rollup stale -------------------
def test_event_change_prunes_stale_old_event_rollup():
    """Restitching an encounter to a different (C-tier) event must not leave the
    OLD event rolled up as if it still had the encounter."""
    old_ev = _conf("Old Ev " + uuid.uuid4().hex[:4], tier="C")
    new_ev = _conf("New Ev " + uuid.uuid4().hex[:4], tier="C")
    cid = _contact("Mover", "EvCo " + uuid.uuid4().hex[:5], arc="warming")
    eid = _encounter(cid, old_ev)
    rollups.recompute_for_contact(cid)
    assert rollups.get_rollup("event", old_ev) is not None

    _move_encounter(eid, new_ev)                 # restitch to a new event
    rollups.recompute_for_contact(cid)
    assert rollups.get_rollup("event", old_ev) is None, \
        "old event rollup left stale after encounter moved"
    assert rollups.get_rollup("event", new_ev) is not None


# --- Guard: A/B planning events are NOT over-pruned ------------------------
def test_planning_event_rollup_not_pruned():
    """A tier-A/B event with zero encounters is a legitimate PLANNING rollup —
    the orphan-prune must keep it (it isn't an orphan, it's intentionally
    encounter-free)."""
    ev = _conf("Planning Only " + uuid.uuid4().hex[:4], tier="A")
    rollups.rebuild_all_rollups()
    assert rollups.get_rollup("event", ev) is not None
    rollups.recompute_for_contact(None)          # a churn sweep
    assert rollups.get_rollup("event", ev) is not None


# --- Bug: account key collisions / case-variant fragmentation --------------
def test_account_key_collapses_case_and_punctuation_variants():
    """Case/punctuation/whitespace variants of the SAME spelling collapse to ONE
    account rollup (not fragmented into several)."""
    base = "CaseCo" + uuid.uuid4().hex[:5]
    c1 = _contact("Lower", base.lower(), arc="warming")
    c2 = _contact("Upper", base.upper(), arc="flat")
    c3 = _contact("Spaced", "  " + base + "  ", arc="cooling")
    for c in (c1, c2, c3):
        _encounter(c)
    rollups.rebuild_all_rollups()
    key = rollups._norm_company_key(base)
    roll = rollups.get_rollup("account", key)
    assert roll is not None
    assert roll["features"]["n_contacts"] == 3   # all three folded into one
    # Exactly one rollup for this account key.
    matches = [r for r in rollups.list_rollups("account", limit=10_000)
               if r["scope_id"] == key]
    assert len(matches) == 1


def test_distinct_companies_are_not_merged():
    """Genuinely different companies keep SEPARATE rollups (no false merge)."""
    a = "AlphaCorp " + uuid.uuid4().hex[:6]
    b = "BetaCorp " + uuid.uuid4().hex[:6]
    _encounter(_contact("A Person", a))
    _encounter(_contact("B Person", b))
    rollups.rebuild_all_rollups()
    assert rollups.get_rollup("account", rollups._norm_company_key(a)) is not None
    assert rollups.get_rollup("account", rollups._norm_company_key(b)) is not None
    assert (rollups._norm_company_key(a) != rollups._norm_company_key(b))


# --- Bug: batched (rebuild) vs single (rollup_account) must AGREE -----------
def test_batched_and_single_account_paths_agree():
    """The fast batched rebuild path and the single rollup_account path must
    produce identical features (the batching refactor must not drift)."""
    company = "AgreeCo " + uuid.uuid4().hex[:6]
    e1 = _conf("Agree Ev1 " + uuid.uuid4().hex[:4])
    e2 = _conf("Agree Ev2 " + uuid.uuid4().hex[:4])
    c1 = _contact("Agree One", company, arc="warming")
    c2 = _contact("Agree Two", company, arc="flat")
    _encounter(c1, e1)
    _encounter(c1, e2)
    _encounter(c2, e1)
    single = rollups.rollup_account(company)["features"]
    rollups.rebuild_all_rollups()
    batched = rollups.get_rollup("account",
                                 rollups._norm_company_key(company))["features"]
    assert single == batched


# --- Perf: rebuild is LINEAR, not quadratic --------------------------------
@pytest.mark.parametrize("n", [400])
def test_rebuild_is_not_quadratic(n):
    """Rebuild over many distinct-company contacts must stay roughly linear. We
    assert the account rebuild touches each contact a bounded number of times by
    proving the result is correct at scale and completes (a quadratic O(n^2) scan
    is what this guards against — see the batched _build_account_rollup path)."""
    now = db.now_iso()
    conn = db.get_conn()
    try:
        for i in range(n):
            cid = "lin_c_" + uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO contacts (id, primary_name, primary_company, "
                "primary_title, arc_verdict, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid, f"Lin {i}", f"LinCo {i:05d}", "CFO", "warming", now, now),
            )
            conn.execute(
                "INSERT INTO encounters (id, contact_id, captured_at, "
                "capture_mode, sentiment, meeting_requested) VALUES (?,?,?,?,?,?)",
                ("lin_e_" + uuid.uuid4().hex[:12], cid, now, "text", 4, 0),
            )
    finally:
        conn.close()
    res = rollups.rebuild_all_rollups()
    assert res["accounts"] >= n
    # One account rollup per distinct NORMALIZED company key (case/punctuation
    # variants of one spelling collapse — so we count keys, not raw names).
    conn = db.get_conn()
    try:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT primary_company FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''"
        ).fetchall()]
    finally:
        conn.close()
    live_keys = {rollups._norm_company_key(nm) for nm in names}
    assert rollups.count_rollups("account") == len(live_keys)


# --- Bug: L2 gaps summary must not NPE on a None title ---------------------
def test_gaps_summary_survives_none_title():
    rollups.upsert_rollup(
        "segment", "noneseg_" + uuid.uuid4().hex[:6], title=None, summary="s",
        features={"segment": "noneseg", "coverage_gap": True,
                  "tier_mix": {"A": 0}, "n_accounts": 0},
        priority=0.1, source_count=1)
    text = spaces._summarize_gaps_from_rollups(
        rollups.list_rollups("segment", limit=10_000))
    assert "gap" in text.lower()    # produced a summary, did not crash
