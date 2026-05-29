"""/api/today/{rep_id} — the rep's morning-screen aggregator."""
from __future__ import annotations

from fastapi import APIRouter

from ... import today

router = APIRouter(prefix="/api/today", tags=["today"])


@router.get("/{rep_id}")
def for_rep(rep_id: str) -> dict:
    return today.for_rep(rep_id)
