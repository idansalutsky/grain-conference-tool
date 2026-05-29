"""/api/insights — periodic brain synthesis + HIL actions."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import brain_insights

router = APIRouter(prefix="/api/insights", tags=["insights"])


class SynthesizeRequest(BaseModel):
    rep_id: str
    lookback_days: int = 30


@router.post("/synthesize", status_code=201)
def synthesize(body: SynthesizeRequest) -> dict:
    """Trigger an LLM synthesis pass over the rep's recent activity.

    Inserts fresh insight rows that are then exposed via GET /api/insights.
    Cost ~$0.005 per call. Designed for on-demand button + daily cron.
    """
    return brain_insights.synthesize_for_rep(
        body.rep_id, lookback_days=body.lookback_days,
    )


@router.get("")
def list_insights(
    rep_id: str,
    status: Optional[str] = "fresh",
    limit: int = 20,
) -> dict:
    return {
        "rep_id": rep_id,
        "items": brain_insights.list_for_rep(
            rep_id, status=status or None, limit=limit,
        ),
    }


class StatusBody(BaseModel):
    decided_by: Optional[str] = "ui"
    reason: Optional[str] = None


@router.post("/{insight_id}/dismiss")
def dismiss(insight_id: str, body: StatusBody | None = None) -> dict:
    try:
        return brain_insights.update_status(
            insight_id, "dismissed",
            decided_by=(body and body.decided_by) or "ui",
            reason=(body and body.reason) or "",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{insight_id}/acknowledge")
def acknowledge(insight_id: str, body: StatusBody | None = None) -> dict:
    try:
        return brain_insights.update_status(
            insight_id, "acknowledged",
            decided_by=(body and body.decided_by) or "ui",
            reason=(body and body.reason) or "",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{insight_id}/actioned")
def mark_actioned(insight_id: str, body: StatusBody | None = None) -> dict:
    """Mark an insight as 'I did the suggested action'. Used when the rep
    clicks 'Do this' and we don't auto-execute (e.g. they're going to
    write the follow-up themselves)."""
    try:
        return brain_insights.update_status(
            insight_id, "actioned",
            decided_by=(body and body.decided_by) or "ui",
            reason=(body and body.reason) or "",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
