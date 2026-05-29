"""/api/companies — first-class account view + prospect discovery."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import companies, prospect_discovery

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("")
def list_all(
    tier: Optional[str] = None,
    is_prospect: Optional[bool] = None,
    approved: Optional[bool] = True,
    limit: int = 200,
) -> dict:
    items = companies.list_companies(
        tier=tier, is_prospect=is_prospect, approved=approved, limit=limit,
    )
    return {"count": len(items), "items": items}


@router.get("/{company_id}")
def detail(company_id: str) -> dict:
    out = companies.get_company_with_rollup(company_id)
    if not out:
        raise HTTPException(404, "company not found")
    return out


# ---------------------------------------------------------------------------
# Backfill — one-shot or after data import
# ---------------------------------------------------------------------------
class BackfillRequest(BaseModel):
    enrich_domains: bool = True


@router.post("/backfill", status_code=201)
def backfill(body: BackfillRequest | None = None) -> dict:
    """Walk people+contacts, create companies, link company_id FK, enrich
    domains via LLM, score every company. Idempotent."""
    return companies.backfill(enrich_domains=(body.enrich_domains if body else True))


@router.post("/rescore")
def rescore() -> dict:
    n = companies.score_all()
    return {"rescored": n}


@router.post("/enrich/inherit-vertical")
def enrich_inherit_vertical() -> dict:
    """Pass 1 — inherit vertical from mode(people.vertical) on each company."""
    return companies.inherit_vertical_from_people()


@router.post("/enrich/entities")
def enrich_entities() -> dict:
    """Pass 2 — batched Gemini call to populate industry, hq_country,
    employee_band, fx_exposure_hint, why_grain_fit for the skeletons.
    Cost ~$0.10 for 143 companies in 8 batches."""
    return companies.enrich_entities_llm()


@router.post("/enrich/ground-tier-a")
def enrich_ground_tier_a(limit: int = 30, only_missing: bool = False) -> dict:
    """Pass 3 — for each tier-A company, ask Sonar for a grounded
    why_grain_fit with a real source URL. One Sonar call per company.
    Pass `only_missing=true` to retry just the ones without source_url."""
    return companies.ground_tier_a_with_sonar(limit=limit, only_missing=only_missing)


# ---------------------------------------------------------------------------
# Prospect discovery — Sonar scraping for Grain-fit companies
# ---------------------------------------------------------------------------
class DiscoverRequest(BaseModel):
    vertical_hint: Optional[str] = None
    region_hint: Optional[str] = None
    max_results: int = 8


@router.post("/discover", status_code=201)
def discover(body: DiscoverRequest) -> dict:
    """Trigger a prospect-discovery scrape via Perplexity Sonar."""
    return prospect_discovery.discover_prospects(
        vertical_hint=body.vertical_hint,
        region_hint=body.region_hint,
        max_results=body.max_results,
    )


@router.get("/discover/pending")
def pending() -> dict:
    items = prospect_discovery.list_pending_prospects()
    return {"count": len(items), "items": items}


class ProspectActionBody(BaseModel):
    decided_by: Optional[str] = "ui"
    reason: Optional[str] = None


@router.post("/{company_id}/approve")
def approve(company_id: str, body: ProspectActionBody | None = None) -> dict:
    try:
        return prospect_discovery.approve_prospect(
            company_id,
            decided_by=(body and body.decided_by) or "ui",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{company_id}/reject")
def reject(company_id: str, body: ProspectActionBody | None = None) -> dict:
    try:
        prospect_discovery.reject_prospect(
            company_id,
            reason=(body and body.reason) or "",
            decided_by=(body and body.decided_by) or "ui",
        )
        return {"status": "rejected"}
    except ValueError as exc:
        raise HTTPException(404, str(exc))
