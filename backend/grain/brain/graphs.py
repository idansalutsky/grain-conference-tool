"""Top-level LangGraph wiring + run/resume helpers.

Graph shape (one StateGraph(BrainState)):

    START → classify ─┬─(unstructured_capture)→ extract → resolve → arc →
                      │                           compress_capture → gate →
                      │                           memory_writer → END
                      ├─(discover_events)→ read_context → search → propose →
                      │                    approval_gate(interrupt) → gate →
                      │                    memory_writer → END
                      └─(query)→ query → END

Human-in-the-loop: the DISCOVERY path PAUSES at `approval_gate` via LangGraph's
`interrupt()`. The graph is compiled with a SqliteSaver checkpointer over the
app's SQLite DB file, so the paused run is durably persisted under its
`thread_id` and can be resumed in a separate process/request with the human's
approve/reject decisions.

LangSmith tracing is automatic and env-gated: set LANGCHAIN_TRACING_V2=true and
LANGCHAIN_API_KEY in the environment and every graph run is traced. No code or
hardcoded keys here.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import config
from . import nodes
from .state import BrainState

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
_VALID_KINDS = ("unstructured_capture", "discover_events", "query")


def _route(state: BrainState) -> str:
    kind = state.get("kind")
    return kind if kind in _VALID_KINDS else "query"


# ---------------------------------------------------------------------------
# The approval interrupt node (discovery only)
# ---------------------------------------------------------------------------
def approval_gate_node(state: BrainState) -> BrainState:
    """PAUSE the discovery run for human approve/reject of the proposals.

    `interrupt(...)` raises a GraphInterrupt the first time through; the payload
    surfaces to the caller (the API returns it as `proposals`). On resume with
    Command(resume={"approvals": [...]}), `interrupt` RETURNS that value and the
    node continues, recording the approvals into state for the gate.
    """
    proposals = state.get("proposals") or []
    decision = interrupt({
        "ask": "approve_events",
        "proposals": proposals,
    })
    approvals = []
    if isinstance(decision, dict):
        approvals = decision.get("approvals") or []
    elif isinstance(decision, list):
        approvals = decision
    return {
        "approvals": approvals,
        "trace": (list(state.get("trace") or []) + ["approval_gate"]),
    }


# ---------------------------------------------------------------------------
# Build + compile
# ---------------------------------------------------------------------------
def _build_graph() -> StateGraph:
    g = StateGraph(BrainState)

    g.add_node("classify", nodes.classify_node)
    # capture
    g.add_node("extract", nodes.extract_node)
    g.add_node("resolve", nodes.resolve_node)
    g.add_node("arc", nodes.arc_node)
    g.add_node("compress_capture", nodes.compress_capture_node)
    # discovery
    g.add_node("read_context", nodes.read_context_node)
    g.add_node("search", nodes.search_node)
    g.add_node("propose", nodes.propose_node)
    g.add_node("approval_gate", approval_gate_node)
    # shared
    g.add_node("gate", nodes.gate_node)
    g.add_node("memory_writer", nodes.memory_writer_node)
    # query
    g.add_node("query", nodes.query_node)

    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify", _route,
        {
            "unstructured_capture": "extract",
            "discover_events": "read_context",
            "query": "query",
        },
    )

    # capture path
    g.add_edge("extract", "resolve")
    g.add_edge("resolve", "arc")
    g.add_edge("arc", "compress_capture")
    g.add_edge("compress_capture", "gate")

    # discovery path
    g.add_edge("read_context", "search")
    g.add_edge("search", "propose")
    g.add_edge("propose", "approval_gate")
    g.add_edge("approval_gate", "gate")

    # shared tail
    g.add_edge("gate", "memory_writer")
    g.add_edge("memory_writer", END)

    # query tail
    g.add_edge("query", END)
    return g


# ---------------------------------------------------------------------------
# Checkpointer concurrency.
#
# A SINGLE shared sqlite3.Connection is NOT safe to drive from multiple threads
# at once: two concurrent run_brain calls would interleave statements on the same
# connection and can raise "Recursive use of cursors not allowed" / corrupt the
# checkpoint. Previously one `_saver_conn` (check_same_thread=False) was shared
# across all runs.
#
# Fix: give each brain invocation its OWN short-lived SqliteSaver connection
# (with WAL + busy_timeout so concurrent writers don't lock-fail), while keeping
# the GRAPH compiled ONCE. SqliteSaver is just a thin checkpointer bound to a
# connection; we recompile cheaply per-call against a fresh saver, but reuse the
# already-built StateGraph (the expensive part is the node wiring, which we cache).
# ---------------------------------------------------------------------------
_graph = None          # the built (uncompiled) StateGraph — built once
_setup_done = False    # checkpoint tables created once
_lock = threading.Lock()


def _new_saver_conn() -> sqlite3.Connection:
    """A fresh sqlite connection for ONE brain run's checkpointer.

    check_same_thread=False so a resume on a different FastAPI worker thread can
    reuse the saver if needed; WAL + busy_timeout match db.get_conn() so brain
    checkpoint writes coexist with app writes under concurrency without
    "database is locked". WAL is on-disk only — guarded for memory/test DBs.
    """
    conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        p = str(config.DB_PATH)
        is_file = bool(p) and p != ":memory:" and not (
            p.startswith("file:") and ("mode=memory" in p or ":memory:" in p)
        )
        if is_file:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _ensure_setup() -> None:
    """Create the LangGraph checkpoint tables once (idempotent, race-safe)."""
    global _setup_done
    if _setup_done:
        return
    with _lock:
        if _setup_done:
            return
        conn = _new_saver_conn()
        try:
            SqliteSaver(conn).setup()
        finally:
            conn.close()
        _setup_done = True


def _get_graph():
    """Return the (uncompiled) StateGraph, building it exactly once."""
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                _graph = _build_graph()
    return _graph


def build_brain():
    """Return a compiled brain graph bound to a FRESH per-call checkpointer conn.

    The graph topology is built once (`_get_graph`) and the checkpoint tables are
    created once (`_ensure_setup`); only the lightweight compile-with-checkpointer
    step runs per call, against a dedicated connection so concurrent runs never
    share one sqlite connection.

    Returns (compiled_app, saver_conn). The caller MUST close `saver_conn` when
    the run/resume finishes.
    """
    _ensure_setup()
    conn = _new_saver_conn()
    saver = SqliteSaver(conn)
    compiled = _get_graph().compile(checkpointer=saver)
    return compiled, conn


# ---------------------------------------------------------------------------
# Run / resume helpers
# ---------------------------------------------------------------------------
def _normalize_result(state: dict) -> dict:
    """Shape the raw graph state into the API's stable response contract."""
    interrupts = state.get("__interrupt__")
    if interrupts:
        # Discovery paused for approval. Surface the proposals.
        payload: dict[str, Any] = {}
        first = interrupts[0]
        val = getattr(first, "value", None)
        if isinstance(val, dict):
            payload = val
        return {
            "status": "awaiting_approval",
            "kind": state.get("kind"),
            "trace": state.get("trace") or [],
            "proposals": payload.get("proposals") or state.get("proposals") or [],
            "result": {},
        }
    return {
        "status": "complete",
        "kind": state.get("kind"),
        "trace": state.get("trace") or [],
        "result": state.get("result") or {},
        "gate_decisions": state.get("gate_decisions") or [],
        "writes": state.get("writes") or [],
    }


def run_brain(input_text: str, thread_id: str) -> dict:
    """Start a brain run on `thread_id`.

    Returns the normalized result. If the discovery path interrupts for human
    approval, `status` == "awaiting_approval" and `proposals` is populated;
    call `resume_brain(thread_id, approvals)` to finish.
    """
    app, saver_conn = build_brain()
    cfg = {"configurable": {"thread_id": thread_id}}
    init: BrainState = {
        "input_text": input_text,
        "candidates": [], "gate_decisions": [], "writes": [],
        "proposals": [], "approvals": [], "trace": [], "result": {},
    }
    try:
        # No global lock: each run owns its own checkpointer connection, so two
        # concurrent run_brain calls proceed in parallel without sharing a conn.
        state = app.invoke(init, cfg)
    finally:
        saver_conn.close()
    out = _normalize_result(state)
    out["thread_id"] = thread_id
    return out


def resume_brain(thread_id: str, approvals: list[dict]) -> dict:
    """Resume an interrupted (discovery) run with the human's approvals.

    `approvals` = [{"id": <proposal_id>, "approved": bool}, ...]. The graph
    re-enters at `approval_gate`, then runs gate → memory_writer → END.
    """
    app, saver_conn = build_brain()
    cfg = {"configurable": {"thread_id": thread_id}}
    try:
        state = app.invoke(Command(resume={"approvals": approvals}), cfg)
    finally:
        saver_conn.close()
    out = _normalize_result(state)
    out["thread_id"] = thread_id
    return out


# ---------------------------------------------------------------------------
# Static graph description (for frontend visualization)
# ---------------------------------------------------------------------------
def graph_description() -> dict:
    """Static node + edge description of the brain graph for the frontend."""
    nodes_list = [
        {"id": "classify", "kind": "router",
         "desc": "Routes input: capture vs discover vs query"},
        {"id": "extract", "kind": "capture", "desc": "Structure the freeform note"},
        {"id": "resolve", "kind": "capture", "desc": "Entity-resolve against contacts"},
        {"id": "arc", "kind": "capture", "desc": "Relationship arc verdict"},
        {"id": "compress_capture", "kind": "capture",
         "desc": "Distill to one salient insight"},
        {"id": "read_context", "kind": "discovery",
         "desc": "Pull ICP + gaps + known-events"},
        {"id": "search", "kind": "discovery",
         "desc": "Find events targeting the gaps"},
        {"id": "propose", "kind": "discovery", "desc": "Assemble candidate events"},
        {"id": "approval_gate", "kind": "interrupt",
         "desc": "PAUSE for human approve/reject"},
        {"id": "gate", "kind": "filter",
         "desc": "THE FILTER: real? ICP-fit? new? accept/review/reject"},
        {"id": "memory_writer", "kind": "memory",
         "desc": "Write accepted items to spaces + resummarize"},
        {"id": "query", "kind": "query", "desc": "Answer over the memory spaces"},
    ]
    edges = [
        {"from": "START", "to": "classify"},
        {"from": "classify", "to": "extract", "when": "unstructured_capture"},
        {"from": "classify", "to": "read_context", "when": "discover_events"},
        {"from": "classify", "to": "query", "when": "query"},
        {"from": "extract", "to": "resolve"},
        {"from": "resolve", "to": "arc"},
        {"from": "arc", "to": "compress_capture"},
        {"from": "compress_capture", "to": "gate"},
        {"from": "read_context", "to": "search"},
        {"from": "search", "to": "propose"},
        {"from": "propose", "to": "approval_gate"},
        {"from": "approval_gate", "to": "gate", "note": "after human approval"},
        {"from": "gate", "to": "memory_writer"},
        {"from": "memory_writer", "to": "END"},
        {"from": "query", "to": "END"},
    ]
    return {
        "nodes": nodes_list,
        "edges": edges,
        "interrupts": ["approval_gate"],
        "spaces": list(nodes.spaces.SPACES),
    }
