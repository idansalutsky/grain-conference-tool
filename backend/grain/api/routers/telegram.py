"""/api/telegram — webhook + issue-token + bot info + active-event."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ... import config
from ... import telegram as tg

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    update = await request.json()
    return tg.handle_update(update)


class TokenRequest(BaseModel):
    rep_id: str
    conference_id: Optional[str] = None  # per-event bind


@router.post("/issue-token", status_code=201)
def issue_token(body: TokenRequest) -> dict:
    """Generate a connect link. If `conference_id` is set, the rep gets
    bound to that event on /start — all subsequent captures auto-tag."""
    try:
        token = tg.issue_link_token(body.rep_id, conference_id=body.conference_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {
        "rep_id": body.rep_id,
        "conference_id": body.conference_id,
        "token": token,
        "deep_link": tg.deep_link(token),
        "bot_username": config.TELEGRAM_BOT_USERNAME,
    }


class ActiveEventBody(BaseModel):
    rep_id: str
    conference_id: Optional[str] = None


@router.put("/active-event")
def set_active(body: ActiveEventBody) -> dict:
    """Manually set/clear which event the rep is currently capturing for —
    bypasses the /start flow when the rep is already bound."""
    tg.set_active_conference(body.rep_id, body.conference_id)
    return {"status": "set", "rep_id": body.rep_id,
            "conference_id": body.conference_id}


@router.get("/bot-info")
def bot_info() -> dict:
    return {
        "configured_username": config.TELEGRAM_BOT_USERNAME,
        "token_set": bool(config.TELEGRAM_BOT_TOKEN),
    }
