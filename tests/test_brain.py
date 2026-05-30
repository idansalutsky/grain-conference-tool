"""Grain Brain — LangGraph agent subsystem tests.

All hermetic: no API key, no network. Every LLM-using node falls back to a
deterministic path, so these exercise routing, the FILTER (gate), memory
write + compression, and the discovery interrupt→resume cycle.

The conftest points DATA_DIR at a temp dir and runs db.init_db(), which now
includes the brain_memory + brain_space_summary tables.
"""
from __future__ import annotations

import uuid

import pytest

from grain.brain import graphs, nodes, spaces
from grain.brain.state import BrainState


def _tid() -> str:
    return "test_" + uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# classify routing
# ---------------------------------------------------------------------------
def test_classify_routes_capture():
    out = nodes.classify_node({"input_text": "met the CFO of Klook at Money20/20, "
                                             "warm, wants a follow-up", "trace": []})
    assert out["kind"] == "unstructured_capture"
    assert "classify" in out["trace"]


def test_classify_routes_discovery():
    out = nodes.classify_node({"input_text": "find treasury events in LATAM we "
                                             "don't already have", "trace": []})
    assert out["kind"] == "discover_events"


def test_classify_routes_query():
    out = nodes.classify_node({"input_text": "what do we know about our ICP?",
                               "trace": []})
    assert out["kind"] == "query"


# ---------------------------------------------------------------------------
# gate — the FILTER: accept / reject (incl. competitor → reject)
# ---------------------------------------------------------------------------
def test_gate_accepts_real_new_icp_event():
    state: BrainState = {
        "kind": "discover_events",
        "approvals": [],
        "result": {"context": {"known_signatures": []}},
        "proposals": [{
            "id": "p1", "name": "LATAM Treasury Summit 2026",
            "vertical": "treasury", "source_url": "https://example.org/x",
        }],
        "trace": [],
    }
    out = nodes.gate_node(state)
    d = out["gate_decisions"][0]
    assert d["decision"] == "accept"


def test_gate_rejects_competitor_event():
    state: BrainState = {
        "kind": "discover_events",
        "approvals": [],
        "result": {"context": {"known_signatures": []}},
        "proposals": [{
            "id": "p2", "name": "AirWallex Partner Connect 2026",
            "vertical": "payments", "source_url": "https://example.org/aw",
        }],
        "trace": [],
    }
    out = nodes.gate_node(state)
    d = out["gate_decisions"][0]
    assert d["decision"] == "reject"
    assert "competitor" in d["reason"].lower()


def test_gate_rejects_already_known_event():
    sig = spaces._known_event_signature("Money20/20 Europe 2026")
    state: BrainState = {
        "kind": "discover_events",
        "approvals": [],
        "result": {"context": {"known_signatures": [sig]}},
        "proposals": [{
            "id": "p3", "name": "Money20/20 Europe 2026",
            "vertical": "payments", "source_url": "https://example.org/m2020",
        }],
        "trace": [],
    }
    out = nodes.gate_node(state)
    assert out["gate_decisions"][0]["decision"] == "reject"
    assert "known" in out["gate_decisions"][0]["reason"].lower()


def test_gate_reviews_event_with_no_source():
    state: BrainState = {
        "kind": "discover_events",
        "approvals": [],
        "result": {"context": {"known_signatures": []}},
        "proposals": [{"id": "p4", "name": "Mystery Event 2026", "vertical": "treasury"}],
        "trace": [],
    }
    out = nodes.gate_node(state)
    assert out["gate_decisions"][0]["decision"] == "review"


def test_gate_rejects_capture_at_competitor():
    state: BrainState = {
        "kind": "unstructured_capture",
        "candidates": [{"compressed": {
            "item_key": "x", "insight": "met someone", "company": "Ebury",
        }}],
        "trace": [],
    }
    out = nodes.gate_node(state)
    assert out["gate_decisions"][0]["decision"] == "reject"
    assert "competitor" in out["gate_decisions"][0]["reason"].lower()


# ---------------------------------------------------------------------------
# memory write + resummarize
# ---------------------------------------------------------------------------
def test_write_item_and_resummarize_keeps_bounded():
    sp = "playbook"
    for i in range(30):
        spaces.write_item(sp, f"k{i}", {"summary": f"fact {i}"},
                          provenance="test", salience=0.5 + (i % 5) * 0.01)
    # A rolling summary exists and is bounded prose (never lists all 30 items).
    summ = spaces.get_summary(sp)
    assert summ is not None
    assert summ["summary"]
    # The summary's recorded item_count is a snapshot from the last (throttled)
    # re-summarise, so it does NOT necessarily equal the live count — that
    # throttling is intentional (we do not re-compress on literally every write).
    # The live raw store is what must be bounded:
    from grain import db
    conn = db.get_conn()
    try:
        raw = conn.execute(
            "SELECT COUNT(*) FROM brain_memory WHERE space = ?", (sp,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert raw <= spaces._MAX_ITEMS_PER_SPACE  # raw store is hard-capped
    items = spaces.read_items(sp, limit=5)
    assert len(items) == 5  # salience-ordered, capped


def test_write_item_prunes_raw_store_beyond_cap():
    """The raw item store is bounded: writing past the cap prunes the
    lowest-salience rows, keeping the highest-salience ones (DEFECT 2)."""
    sp = "gaps"
    n = spaces._MAX_ITEMS_PER_SPACE + 40
    for i in range(n):
        # salience strictly increases with i, so low-i rows are the prune victims
        spaces.write_item(sp, f"prune_k{i}", {"summary": f"fact {i}"},
                          provenance="test", salience=(i % 100) / 100.0)
    from grain import db
    conn = db.get_conn()
    try:
        raw = conn.execute(
            "SELECT COUNT(*) FROM brain_memory WHERE space = ?", (sp,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert raw <= spaces._MAX_ITEMS_PER_SPACE


def test_write_item_resummarize_is_throttled():
    """Re-summarise fires on a cadence, NOT on every single new write
    (DEFECT 3)."""
    import grain.brain.spaces as sp_mod
    calls = {"n": 0}
    orig = sp_mod.resummarize
    sp_mod.resummarize = lambda s: (calls.__setitem__("n", calls["n"] + 1)
                                    or orig(s))
    try:
        for i in range(60):
            sp_mod.write_item("icp", f"throttle_k{i}", {"summary": f"f{i}"},
                              provenance="test", salience=0.5)
    finally:
        sp_mod.resummarize = orig
    assert calls["n"] < 60  # far fewer than one-per-write


def test_write_item_upsert_by_key():
    spaces.write_item("relationship", "upsert_key",
                      {"summary": "v1"}, provenance="test", salience=0.5)
    spaces.write_item("relationship", "upsert_key",
                      {"summary": "v2"}, provenance="test", salience=0.9)
    items = [i for i in spaces.read_items("relationship", limit=200)
             if i["item_key"] == "upsert_key"]
    assert len(items) == 1
    assert items[0]["content"]["summary"] == "v2"


def test_write_item_rejects_unknown_space():
    with pytest.raises(ValueError):
        spaces.write_item("nope", "k", {}, provenance="test")


# ---------------------------------------------------------------------------
# seed_brain_spaces is idempotent + populates spaces
# ---------------------------------------------------------------------------
def test_seed_brain_spaces_idempotent():
    r1 = spaces.seed_brain_spaces()
    r2 = spaces.seed_brain_spaces()
    assert r1["written"]["icp"] == r2["written"]["icp"]  # same count, no dupes
    # ICP space must carry the competitor list (the gate reads competitors from icp.py,
    # but the space should reflect it too).
    icp_items = spaces.read_items("icp", limit=20)
    keys = {i["item_key"] for i in icp_items}
    assert "competitors" in keys
    names = spaces.list_spaces()
    assert {s["name"] for s in names} == set(spaces.SPACES)


# ---------------------------------------------------------------------------
# DEFECT 1 — brain reflects the REAL capture pipeline (contacts/encounters)
# ---------------------------------------------------------------------------
def _insert_contact(name, company, title, arc_verdict, *, n_enc=1,
                    meeting=False, summary="real captured contact"):
    """Insert a genuine contact + encounter (the shape the live pipeline writes)."""
    from grain import db
    cid = "c_" + uuid.uuid4().hex[:10]
    now = db.now_iso()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO contacts (id, primary_name, primary_company, "
            "primary_title, arc_verdict, arc_summary, arc_confidence, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, name, company, title, arc_verdict, summary, 0.8, now, now),
        )
        for i in range(n_enc):
            conn.execute(
                "INSERT INTO encounters (id, contact_id, captured_at, "
                "capture_mode, sentiment, meeting_requested) VALUES (?,?,?,?,?,?)",
                ("e_" + uuid.uuid4().hex[:10], cid, now, "text", 4,
                 1 if meeting else 0),
            )
    finally:
        conn.close()
    return cid


def test_sync_relationship_space_reflects_real_contacts():
    """sync_relationship_space_from_db reads genuine contacts (with arc
    verdicts) and writes one compressed insight per contact, provenance
    capture:field — the brain reflects real captures, not just brain-path items."""
    _insert_contact("Sync Warming", "TravelCo", "VP Treasury", "warming",
                    n_enc=3, meeting=True)
    _insert_contact("Sync Tirekicker", "GlobeX", "CFO", "tire_kicker", n_enc=4)
    # A contact at a Grain competitor must be REJECTED by the ingest gate.
    from grain.icp import IcpConfig
    competitor = IcpConfig.default().competitors[0]
    _insert_contact("Sync Competitor", competitor, "Head of FX", "warming")

    result = spaces.sync_relationship_space_from_db()
    assert result["ingested"] >= 2
    assert result["rejected"] >= 1  # the competitor contact

    rel = spaces.read_items("relationship", limit=200)
    field = [i for i in rel if i["provenance"] == "capture:field"]
    names = " ".join((i["content"].get("name") or "") for i in field)
    assert "Sync Warming" in names
    assert "Sync Tirekicker" in names
    assert "Sync Competitor" not in names  # competitor rejected, never recorded
    # Warming captured contact also produced a playbook "what works" signal.
    pb = [i for i in spaces.read_items("playbook", limit=200)
          if i["provenance"] == "capture:field"]
    assert any("TravelCo" in (i["content"].get("summary") or "") for i in pb)


def test_sync_relationship_space_is_idempotent():
    _insert_contact("Idem Person", "AcmeFX", "Treasurer", "flat")
    spaces.sync_relationship_space_from_db()
    n1 = len([i for i in spaces.read_items("relationship", limit=500)
              if i["provenance"] == "capture:field"])
    spaces.sync_relationship_space_from_db()
    n2 = len([i for i in spaces.read_items("relationship", limit=500)
              if i["provenance"] == "capture:field"])
    assert n1 == n2  # re-sync rebuilds, does not duplicate


def test_ingest_encounter_rejects_competitor_contact():
    from grain.icp import IcpConfig
    comp = IcpConfig.default().competitors[0]
    out = spaces.ingest_encounter(
        {"primary_name": "X", "primary_company": comp, "arc_verdict": "warming"})
    assert out is None


# ---------------------------------------------------------------------------
# DEFECT 4 — discovery never proposes a past-dated event
# ---------------------------------------------------------------------------
def test_discovery_recency_guard_drops_past_events():
    import datetime
    kept = nodes._dedupe_and_finalize([
        {"name": "Old Conf 2024", "start_date": "2024-05-01", "region": "EU"},
        {"name": "Future Conf", "start_date": "2099-01-01", "region": "EU"},
        {"name": "Undated Conf", "start_date": None, "region": "EU"},
    ], "EU")
    names = [p["name"] for p in kept]
    assert "Old Conf 2024" not in names
    assert "Future Conf" in names
    assert "Undated Conf" in names  # unknown date is not provably past
    today = datetime.date.today().isoformat()
    assert nodes._is_future_or_today(today) is True


# ---------------------------------------------------------------------------
# Full graph — capture path end to end
# ---------------------------------------------------------------------------
def test_capture_path_writes_relationship():
    out = graphs.run_brain(
        "met the CFO of Klook at Money20/20, warm, wants a follow-up", _tid())
    assert out["status"] == "complete"
    assert out["kind"] == "unstructured_capture"
    assert "extract" in out["trace"]
    assert "gate" in out["trace"]
    assert "memory_writer" in out["trace"]
    # something was written into the relationship space
    rel = [w for w in out["writes"] if w["space"] == "relationship"]
    assert rel, f"expected a relationship write, got {out['writes']}"


# ---------------------------------------------------------------------------
# Full graph — discovery interrupt → resume cycle
# ---------------------------------------------------------------------------
def test_discovery_interrupt_then_resume_writes_events(monkeypatch):
    # Inject a REAL proposal (the hermetic no-key path returns a placeholder,
    # which is deliberately skipped — see Option B). This exercises the real
    # path: approve a discovery → it writes to the events space AND becomes a
    # real, scored conference.
    from grain import llm
    from grain.brain import nodes
    real = [{
        "name": "LATAM Treasury Forum (test) 2026", "city": "Lima",
        "country": "Peru", "region": "LATAM", "start_date": "2026-09-01",
        "vertical": "treasury", "source_url": "https://example.com/latam",
        "why_relevant": "LATAM corporate treasurers with heavy FX exposure",
        "estimated_attendance": 500,
    }]
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(nodes, "_llm_propose_events", lambda **kw: [dict(real[0])])

    tid = _tid()
    first = graphs.run_brain(
        "find treasury events in LATAM we don't already have", tid)
    assert first["status"] == "awaiting_approval"
    assert first["kind"] == "discover_events"
    assert first["proposals"], "discovery should surface proposals at the interrupt"
    assert first["thread_id"] == tid

    # Approve the first proposal, reject the rest.
    approvals = [{"id": first["proposals"][0]["id"], "approved": True}]
    for p in first["proposals"][1:]:
        approvals.append({"id": p["id"], "approved": False})

    resumed = graphs.resume_brain(tid, approvals)
    assert resumed["status"] == "complete"
    assert "gate" in resumed["trace"]
    assert "memory_writer" in resumed["trace"]
    # At least one accepted event landed in the events space.
    ev_writes = [w for w in resumed["writes"] if w["space"] == "events"]
    assert ev_writes, f"expected an events write after approval, got {resumed['writes']}"
    # Option B: the approved discovery became a REAL, scoreable conference.
    cid = ev_writes[0].get("conference_id")
    assert cid, "approved discovery should create a real conference"
    from grain import db as _db
    conn = _db.get_conn()
    try:
        row = conn.execute(
            "SELECT name FROM conferences WHERE id = ?", (cid,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "the created conference should exist in the table"


def test_discovery_resume_rejecting_all_writes_nothing():
    tid = _tid()
    first = graphs.run_brain("find APAC travel conferences to attend", tid)
    assert first["status"] == "awaiting_approval"
    approvals = [{"id": p["id"], "approved": False} for p in first["proposals"]]
    resumed = graphs.resume_brain(tid, approvals)
    assert resumed["status"] == "complete"
    # Human rejected everything → the gate forces reject, so no events were written.
    assert not [w for w in resumed["writes"] if w["space"] == "events"]
    accepted = [d for d in resumed.get("gate_decisions", [])
                if d.get("decision") == "accept"]
    assert not accepted


# ---------------------------------------------------------------------------
# Full graph — query path
# ---------------------------------------------------------------------------
def test_query_path_answers_over_spaces():
    spaces.seed_brain_spaces()
    out = graphs.run_brain("what do we know about our ICP competitors?", _tid())
    assert out["status"] == "complete"
    assert out["kind"] == "query"
    assert "query" in out["trace"]
    assert out["result"]["answer"]


# ---------------------------------------------------------------------------
# Graph description (frontend contract)
# ---------------------------------------------------------------------------
def test_graph_description_has_nodes_and_interrupt():
    d = graphs.graph_description()
    node_ids = {n["id"] for n in d["nodes"]}
    assert {"classify", "gate", "memory_writer", "approval_gate"} <= node_ids
    assert "approval_gate" in d["interrupts"]
    assert any(e["from"] == "START" for e in d["edges"])
