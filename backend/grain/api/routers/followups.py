"""/api/followups — post-event follow-up drafting (draft-and-review)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import followup

router = APIRouter(prefix="/api/followups", tags=["followups"])


@router.post("/contact/{contact_id}")
def draft_contact(contact_id: str, conference_id: Optional[str] = None) -> dict:
    """Draft (and persist) a follow-up for one contact, optionally anchored to
    a specific event."""
    out = followup.draft_for_contact(contact_id, conference_id, persist=True)
    if not out.get("ok"):
        raise HTTPException(404, out.get("error", "could not draft"))
    return out


@router.post("/event/{conference_id}")
def draft_event(conference_id: str) -> dict:
    """Draft follow-ups for everyone met at one event — the post-event close."""
    out = followup.draft_for_event(conference_id)
    if not out.get("ok"):
        raise HTTPException(404, out.get("error", "could not draft"))
    return out


class EditBody(BaseModel):
    encounter_id: str
    body: str


@router.put("/draft")
def edit_draft(body: EditBody) -> dict:
    """Persist a rep's edited follow-up body before they send it."""
    out = followup.update_draft(body.encounter_id, body.body)
    if not out.get("ok"):
        raise HTTPException(404, out.get("error", "encounter not found"))
    return out
