"""/api/discovery — find new conferences + manage proposals."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import discovery

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class DiscoverRequest(BaseModel):
    region: Optional[str] = None
    vertical: Optional[str] = None
    max_results: int = 6


@router.post("/conferences", status_code=201)
def discover(body: DiscoverRequest) -> dict:
    """Iterative grounded research → propose new conferences (with a refinement
    pass if the first is thin). Each proposal lands in the pending-approval queue."""
    return discovery.discover_conferences(
        region_hint=body.region, vertical_hint=body.vertical,
        max_results=body.max_results,
    )


@router.get("/pending")
def list_pending(limit: int = 50) -> dict:
    return {"proposals": discovery.list_pending_proposals(limit)}


@router.get("/mentioned")
def mentioned_events(limit: int = 12) -> dict:
    """Events your buyers told reps they attend — ground-up event intelligence
    from real conversations. Untracked ones are discovery candidates."""
    return {"events": discovery.mentioned_events_signal(limit)}


@router.post("/mentioned/research")
def research_mentioned(limit: int = 8) -> dict:
    """Research the untracked buyer-mentioned events: verify each + find its next
    occurrence (grounded), turn confirmed ones into pending proposals, and report
    the ones the agent couldn't confirm. Human still approves before anything is
    added."""
    return discovery.research_mentioned_events(limit)


class ApprovalBody(BaseModel):
    decided_by: Optional[str] = "ui"


@router.post("/{proposal_id}/approve")
def approve(proposal_id: str, body: ApprovalBody | None = None) -> dict:
    try:
        return discovery.approve_proposal(
            proposal_id, decided_by=(body and body.decided_by) or "ui",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


class RejectBody(BaseModel):
    reason: str = ""
    decided_by: Optional[str] = "ui"


@router.post("/{proposal_id}/reject")
def reject(proposal_id: str, body: RejectBody | None = None) -> dict:
    discovery.reject_proposal(
        proposal_id, reason=(body and body.reason) or "",
        decided_by=(body and body.decided_by) or "ui",
    )
    return {"status": "rejected"}
