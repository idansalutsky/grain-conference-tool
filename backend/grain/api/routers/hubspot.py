"""/api/hubspot — push a contact to HubSpot (dry-run default)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import hubspot as hs

router = APIRouter(prefix="/api/hubspot", tags=["hubspot"])


@router.post("/push/{contact_id}")
def push_contact(contact_id: str, dry_run: bool | None = None) -> dict:
    out = hs.push_contact(contact_id, dry_run=dry_run)
    if not out.get("ok") and out.get("error") == "contact_not_found":
        raise HTTPException(404, "contact not found")
    return out
