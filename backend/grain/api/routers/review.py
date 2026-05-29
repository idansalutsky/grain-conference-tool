"""/api/review — match-review queue endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import review_queue

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("")
def list_pending(limit: int = 50) -> dict:
    return {"items": review_queue.list_pending(limit)}


class DecisionBody(BaseModel):
    decided_by: Optional[str] = "ui"
    reason: Optional[str] = None


@router.post("/{encounter_id}/confirm")
def confirm(encounter_id: str, body: DecisionBody | None = None) -> dict:
    try:
        return review_queue.confirm(
            encounter_id,
            decided_by=(body and body.decided_by) or "ui",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{encounter_id}/reject")
def reject(encounter_id: str, body: DecisionBody | None = None) -> dict:
    try:
        return review_queue.reject(
            encounter_id,
            decided_by=(body and body.decided_by) or "ui",
            reason=body.reason if body else None,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
