"""Graph state for the Grain Brain.

A single TypedDict threads through every node. LangGraph merges each node's
returned partial dict into this state. We keep it flat and JSON-serialisable so
the SqliteSaver checkpointer can persist it across an interrupt/resume cycle.

`trace` is the observability backbone: every node appends its own name, so the
final state (and the API response) shows exactly which path the input took.
"""
from __future__ import annotations

from typing import Any, TypedDict


class BrainState(TypedDict, total=False):
    # --- input ---
    input_text: str

    # --- classification (set by classify_node; routes the graph) ---
    kind: str          # unstructured_capture | discover_events | query

    # --- working set (capture + discovery share these slots) ---
    candidates: list[dict]          # things considered for memory
    gate_decisions: list[dict]      # one per candidate: accept | review | reject + reason
    writes: list[dict]              # what memory_writer actually wrote

    # --- discovery / human-in-the-loop ---
    proposals: list[dict]           # candidate events surfaced by search/propose
    approvals: list[dict]           # [{id, approved: bool}] supplied on resume

    # --- observability ---
    trace: list[str]                # node names visited, in order

    # --- final answer ---
    result: dict[str, Any]          # node-specific payload returned to the API
