"""/api/telegram — webhook + issue-token + bot info + active-event."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ... import config
from ... import telegram as tg
from ..deps import require_admin

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    # If a webhook secret has been registered (via /set-webhook), Telegram
    # echoes it in this header on every update. Reject anything that doesn't
    # match — stops randoms POSTing fake updates to the public endpoint.
    expected = tg.webhook_secret()
    if expected:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if got != expected:
            raise HTTPException(403, "bad webhook secret")
    update = await request.json()
    return tg.handle_update(update)


class SetWebhookBody(BaseModel):
    base_url: str  # public https origin of the deployed API


@router.post("/set-webhook", dependencies=[Depends(require_admin)])
def set_webhook(body: SetWebhookBody) -> dict:
    """One-call deploy setup: register this server's public webhook with
    Telegram (with a spoof-proof secret, rotated on each call). Admin-only.
    Call once after deploy, e.g. base_url = "https://grain-api.onrender.com"."""
    return tg.set_webhook(body.base_url)


@router.delete("/webhook", dependencies=[Depends(require_admin)])
def remove_webhook() -> dict:
    """Unregister the webhook (switch back to no-webhook / local). Admin-only —
    this disables the field-capture bot, so it's a destructive op."""
    return tg.delete_webhook()


@router.get("/webhook-info", dependencies=[Depends(require_admin)])
def webhook_info() -> dict:
    """What webhook Telegram currently has for this bot + its health.
    Admin-only — the response can reveal the deployment URL."""
    return tg.get_webhook_info()


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
