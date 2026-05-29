"""/api/planning — coverage, clusters, gaps."""
from __future__ import annotations

from fastapi import APIRouter

from ... import planning as plan

router = APIRouter(prefix="/api/planning", tags=["planning"])


@router.get("/coverage")
def coverage() -> dict:
    return plan.coverage()


@router.get("/clusters")
def clusters(min_size: int = 2) -> dict:
    return {"clusters": plan.trip_clusters(min_size=min_size)}


@router.get("/gaps")
def gaps() -> dict:
    return plan.gaps()
