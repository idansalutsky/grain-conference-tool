"""/api/brain — the Grain Brain (LangGraph agent subsystem).

Endpoints:
  GET  /api/brain/spaces          → list the 5 memory spaces (summary + counts)
  POST /api/brain/sync            → rebuild relationship/playbook from real captures
  GET  /api/brain/space/{name}    → items + summary + provenance for one space
  POST /api/brain/run             → run the graph on an input (capture/discover/query)
  POST /api/brain/resume          → resume an interrupted discovery run with approvals
  GET  /api/brain/graph           → static node/edge description (for visualization)

All endpoints are robust without an LLM key — every model call has a
deterministic fallback, so this works offline.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import db, scoring
from ...brain import graphs, rollups, spaces

router = APIRouter(prefix="/api/brain", tags=["brain"])


# ---------------------------------------------------------------------------
# L1 — middle-management rollups (hierarchical memory tier).
# ONE judged summary per ENTITY (event / account / segment). Bounded by the
# number of entities, never by a magic 50. `limit` here is PAGINATION of the
# returned list, NOT a cap on how many rollups exist.
# ---------------------------------------------------------------------------
@router.get("/rollups")
def list_rollups(scope: Optional[str] = None, limit: int = 200,
                 sort: str = "priority") -> dict:
    """List L1 rollups (title, summary, features, priority, source_count).

    `scope` ∈ {event, account, segment} (omit for all). `sort` ∈ {priority,
    recent}. Ordered by judged priority by default — every entity has a rollup,
    so this is judgment-ordered, not a salience cutoff.
    """
    if scope is not None and scope not in rollups.SCOPE_TYPES:
        raise HTTPException(
            404, f"unknown scope '{scope}'; valid: {rollups.SCOPE_TYPES}")
    items = rollups.list_rollups(scope, limit=limit, sort=sort)
    return {
        "scope": scope,
        "sort": sort,
        "count": rollups.count_rollups(scope),  # TOTAL that exist (not page size)
        "returned": len(items),
        "rollups": items,
    }


@router.get("/rollup/{scope_type}/{scope_id}")
def get_rollup(scope_type: str, scope_id: str, refine: bool = False) -> dict:
    """One rollup. Pass ?refine=true to LLM-refine + cache its prose on demand
    (no-op without an API key — the deterministic summary stands)."""
    if scope_type not in rollups.SCOPE_TYPES:
        raise HTTPException(
            404, f"unknown scope_type '{scope_type}'; valid: {rollups.SCOPE_TYPES}")
    roll = (rollups.refine_rollup_summary(scope_type, scope_id) if refine
            else rollups.get_rollup(scope_type, scope_id))
    if roll is None:
        raise HTTPException(404, f"no rollup for {scope_type}/{scope_id}")
    return roll


@router.post("/rollups/rebuild")
def rebuild_rollups() -> dict:
    """Recompute EVERY L1 rollup from the L0 dots (idempotent), then rederive the
    L2 space summaries from those rollups. Bounded by entity count, no top-50 cap.
    """
    build = rollups.rebuild_all_rollups()
    l2 = spaces.rebuild_space_summaries_from_rollups()
    return {"status": "rebuilt", "rollup_build": build,
            "l2_rewire": l2["rollup_counts"]}


# ---------------------------------------------------------------------------
# Memory spaces (read)
# ---------------------------------------------------------------------------
@router.get("/spaces")
def list_spaces() -> dict:
    """List all memory spaces with their rolling summary + item count."""
    return {"spaces": spaces.list_spaces()}


@router.post("/sync")
def sync() -> dict:
    """Refresh the brain's relationship/playbook spaces from the REAL captured
    contacts (contacts + encounters + arc verdicts) on demand.

    Idempotent: rebuilds the `capture:field` rows so the brain reflects the
    current state of the live capture pipeline, not a stale snapshot.
    """
    result = spaces.sync_relationship_space_from_db()
    return {
        "status": "synced",
        "relationship_sync": result,
        "relationship": spaces.get_summary("relationship"),
        "playbook": spaces.get_summary("playbook"),
    }


@router.get("/space/{name}")
def get_space(name: str, limit: int = 100) -> dict:
    """Items + summary + provenance for one space."""
    if name not in spaces.SPACES:
        raise HTTPException(404, f"unknown space '{name}'; valid: {spaces.SPACES}")
    summary = spaces.get_summary(name)
    items = spaces.read_items(name, limit=limit)
    return {
        "space": name,
        "summary": (summary or {}).get("summary"),
        "item_count": (summary or {}).get("item_count", len(items)),
        "updated_at": (summary or {}).get("updated_at"),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Run / resume the graph
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    input_text: str
    thread_id: Optional[str] = None


@router.post("/run")
def run(body: RunRequest) -> dict:
    """Run the brain graph on an input.

    Returns:
      capture/query → {status:"complete", kind, trace, result, ...}
      discovery     → {status:"awaiting_approval", thread_id, proposals:[...], kind, trace}
                      (call POST /api/brain/resume to finish)
    """
    if not (body.input_text or "").strip():
        raise HTTPException(400, "input_text is required")
    thread_id = body.thread_id or ("brain_" + uuid.uuid4().hex[:14])
    return graphs.run_brain(body.input_text, thread_id)


class Approval(BaseModel):
    id: str
    approved: bool


class ResumeRequest(BaseModel):
    thread_id: str
    approvals: list[Approval] = Field(default_factory=list)


@router.post("/resume")
def resume(body: ResumeRequest) -> dict:
    """Resume an interrupted discovery run with the human's approve/reject calls.

    Returns {status:"complete", writes:[...], result:{updated_summaries,...}}.
    """
    if not body.thread_id:
        raise HTTPException(400, "thread_id is required")
    approvals = [a.model_dump() for a in body.approvals]
    return graphs.resume_brain(body.thread_id, approvals)


# ---------------------------------------------------------------------------
# Calibration — rep score-overrides auto-tune the scoring weights (HIL-gated).
#
# This closes the learning loop: reps log `conference_score_adjust` overrides;
# scoring.learn_scoring_weights() turns the accumulated overrides into a BOUNDED
# nudge of the 7 factor weights so the model learns what reps value. Like the
# rest of the app's AI, it is human-in-the-loop: PREVIEW (GET) then APPLY (POST)
# on demand, and RESET to undo. Safe by construction — bounded step, clamped,
# renormalised, and a hard floor of >= 3 override signals before anything moves.
# ---------------------------------------------------------------------------
def _top_moved_factor(current: dict, proposed: dict) -> tuple[str | None, float]:
    """The factor whose weight moved UP the most (for the playbook note)."""
    best_k, best_delta = None, 0.0
    for k in current:
        d = proposed.get(k, current[k]) - current[k]
        if d > best_delta:
            best_k, best_delta = k, d
    return best_k, best_delta


@router.get("/calibration")
def calibration_preview() -> dict:
    """PREVIEW the proposed weight nudge — READ-ONLY (no writes, no rescore).

    Returns {n_signals, current_weights, proposed_weights, per_factor_rationale,
    would_change}. With < 3 override signals this is a no-op (proposed == current).
    """
    return scoring.learn_scoring_weights(apply=False)


@router.post("/calibration/apply")
def calibration_apply() -> dict:
    """APPLY the proposed weights (HIL on-demand), then rescore + record it.

    Reuses the EXISTING live-weights/settings store (scoring.write_weights →
    db.set_setting → the same keys _live_weights reads + PUT /api/settings writes).
    Then rescore_all(), log `weights_calibrated` feedback, and write a brain
    `playbook` note. Returns before/after weights AND before/after tier
    distribution so we can confirm the tiers stay sane.
    """
    learned = scoring.learn_scoring_weights(apply=False)
    before_weights = learned["current_weights"]
    proposed = learned["proposed_weights"]
    before_tiers = scoring.tier_distribution()

    if not learned["would_change"]:
        return {
            "status": "noop",
            "reason": f"insufficient/neutral signal ({learned['n_signals']} signals)",
            "n_signals": learned["n_signals"],
            "before_weights": before_weights,
            "after_weights": before_weights,
            "before_tiers": before_tiers,
            "after_tiers": before_tiers,
            "per_factor_rationale": learned["per_factor_rationale"],
        }

    # WRITE via the existing mechanism, then rescore.
    scoring.write_weights(proposed)
    rescored = scoring.rescore_all()
    after_tiers = scoring.tier_distribution()

    # Audit trail — same feedback table every other AI decision uses.
    db.log_feedback(
        decision_kind="weights_calibrated",
        target_kind="scoring_weights", target_id="scoring.weights",
        before={"weights": before_weights, "tiers": before_tiers},
        after={"weights": proposed, "tiers": after_tiers,
               "n_signals": learned["n_signals"]},
        reason=f"auto-tuned from {learned['n_signals']} rep score-overrides",
        decided_by="ui",
    )

    # Brain playbook note — the calibration is a learned "what reps value" fact.
    top_k, top_delta = _top_moved_factor(before_weights, proposed)
    if top_k:
        note = (f"Learned: reps value '{top_k}' - nudged its scoring weight up "
                f"({before_weights[top_k]:.3f} -> {proposed[top_k]:.3f}) from "
                f"{learned['n_signals']} score-overrides.")
    else:
        note = (f"Calibrated scoring weights from {learned['n_signals']} "
                f"rep score-overrides (bounded nudge).")
    try:
        spaces.write_item(
            "playbook", "weights_calibration",
            {"summary": note, "note": note,
             "before_weights": before_weights, "after_weights": proposed,
             "n_signals": learned["n_signals"], "signal": "weights_calibrated"},
            provenance="feedback:weights_calibrated", salience=0.7,
        )
    except Exception:  # noqa: BLE001 — note is best-effort, never block apply
        pass

    return {
        "status": "applied",
        "n_signals": learned["n_signals"],
        "rescored": rescored,
        "before_weights": before_weights,
        "after_weights": proposed,
        "before_tiers": before_tiers,
        "after_tiers": after_tiers,
        "per_factor_rationale": learned["per_factor_rationale"],
        "playbook_note": note,
    }


@router.post("/calibration/reset")
def calibration_reset() -> dict:
    """RESET weights to defaults + rescore — makes calibration reversible."""
    before_weights = scoring._live_weights()
    before_tiers = scoring.tier_distribution()
    defaults = scoring.reset_weights_to_default()
    rescored = scoring.rescore_all()
    after_tiers = scoring.tier_distribution()
    db.log_feedback(
        decision_kind="weights_calibrated",
        target_kind="scoring_weights", target_id="scoring.weights",
        before={"weights": {k: round(v, 4) for k, v in before_weights.items()},
                "tiers": before_tiers},
        after={"weights": defaults, "tiers": after_tiers, "reset": True},
        reason="reset scoring weights to defaults",
        decided_by="ui",
    )
    return {
        "status": "reset",
        "rescored": rescored,
        "before_weights": {k: round(v, 4) for k, v in before_weights.items()},
        "after_weights": defaults,
        "before_tiers": before_tiers,
        "after_tiers": after_tiers,
    }


# ---------------------------------------------------------------------------
# Static graph description (for the frontend visualization)
# ---------------------------------------------------------------------------
@router.get("/graph")
def graph() -> dict:
    """Static description of the graph nodes + edges (+ which node interrupts)."""
    return graphs.graph_description()
