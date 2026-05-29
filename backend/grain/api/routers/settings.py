"""/api/settings — tunable sliders + ICP read."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import config, db
from ...icp import IcpConfig
from ...scoring import DEFAULT_WEIGHTS

router = APIRouter(prefix="/api/settings", tags=["settings"])


# Every tunable lives here so the UI can render sliders without code changes.
PARAMETER_REGISTRY = [
    {"key": f"scoring.{k}", "default": v, "min": 0.0, "max": 1.0,
     "ui": "slider",
     "description": f"Weight for {k.replace('_', ' ')} in conference scoring"}
    for k, v in DEFAULT_WEIGHTS.items()
] + [
    {"key": "entity_resolution.auto_merge_threshold", "default": 0.85,
     "min": 0.5, "max": 1.0, "ui": "slider",
     "description": "Above this confidence, auto-merge the encounter into a contact"},
    {"key": "entity_resolution.review_threshold", "default": 0.65,
     "min": 0.3, "max": 0.95, "ui": "slider",
     "description": "Below this, create a new contact instead of attaching"},
    {"key": "nudge.arc_confidence_threshold", "default": 0.70,
     "min": 0.0, "max": 1.0, "ui": "slider",
     "description": "Minimum arc-classifier confidence to fire a nudge"},
    {"key": "nudge.recency_days_max", "default": 90,
     "min": 30, "max": 365, "ui": "number",
     "description": "Max days since last touch before a contact goes 'cold'"},
]


@router.get("")
def list_parameters() -> dict:
    keys = [p["key"] for p in PARAMETER_REGISTRY]
    overrides = db.get_settings_many(keys)
    out = []
    for p in PARAMETER_REGISTRY:
        cur = overrides.get(p["key"])
        if cur is None:
            cur = p["default"]
        else:
            try:
                cur = type(p["default"])(cur)
            except (ValueError, TypeError):
                pass
        out.append({**p, "current": cur})
    return {"parameters": out, "icp": _icp_summary()}


class ParameterUpdate(BaseModel):
    key: str
    value: float | int | str
    reason: str | None = None
    decided_by: str | None = "ui"


@router.put("")
def update_parameter(body: ParameterUpdate) -> dict:
    meta = next((p for p in PARAMETER_REGISTRY if p["key"] == body.key), None)
    if not meta:
        raise HTTPException(400, f"unknown parameter {body.key}")
    if "min" in meta and isinstance(body.value, (int, float)):
        if not (meta["min"] <= body.value <= meta["max"]):
            raise HTTPException(400, f"out of range [{meta['min']}, {meta['max']}]")
    before = db.get_setting(body.key) or meta["default"]
    db.set_setting(body.key, body.value)
    db.log_feedback(
        decision_kind="parameter_update", target_kind="parameter",
        target_id=body.key, before={"value": before}, after={"value": body.value},
        reason=body.reason, decided_by=body.decided_by,
    )
    return {"status": "updated", "key": body.key, "value": body.value, "before": before}


# ---------------------------------------------------------------------------
# Integrations — in-app API key configuration
# ---------------------------------------------------------------------------
# Request field name -> public config attribute. Field names match the masked
# response keys (the part after "integrations." in the settings table key).
_INTEGRATION_FIELDS = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "hubspot_token": "HUBSPOT_PRIVATE_APP_TOKEN",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
}


@router.get("/integrations")
def get_integrations() -> dict:
    """Which integration keys are configured. Secrets are NEVER returned raw.

    Response:
      {"integrations": {
         "<field>": {"configured": bool,
                     "masked": "…1234" | null,
                     "source": "in_app" | "env" | null},
         ...}}
    where <field> is one of openrouter_api_key, perplexity_api_key,
    hubspot_token, telegram_bot_token.
    """
    return {"integrations": config.integration_status()}


class IntegrationsUpdate(BaseModel):
    openrouter_api_key: str | None = None
    perplexity_api_key: str | None = None
    hubspot_token: str | None = None
    telegram_bot_token: str | None = None


@router.put("/integrations")
def update_integrations(body: IntegrationsUpdate) -> dict:
    """Store any provided (non-empty) integration secrets in the settings table.

    Only non-None, non-blank fields are written; omitted fields are left
    untouched. Values are stored as-is in the settings table (plaintext,
    matching how every other setting is stored in this single-tenant demo).
    Saving an in-app key makes it take precedence over the env var at the next
    access — the env fallback is preserved when no in-app key is set.

    Returns the updated masked status (same shape as GET).
    """
    updated: list[str] = []
    payload = body.model_dump()
    for field, attr in _INTEGRATION_FIELDS.items():
        val = payload.get(field)
        if val is None:
            continue
        val = val.strip()
        if not val:
            continue
        settings_key = config.INTEGRATION_SETTING_KEYS[attr]
        db.set_setting(settings_key, val)
        db.log_feedback(
            decision_kind="parameter_update", target_kind="integration",
            target_id=settings_key,
            before={"configured": True}, after={"configured": True},
            reason="integration key updated via settings UI", decided_by="ui",
        )
        updated.append(field)
    return {"status": "updated", "updated": updated,
            "integrations": config.integration_status()}


def _icp_summary() -> dict:
    icp = IcpConfig.default()
    return {
        "version": icp.version,
        "verticals": icp.company_level["verticals"],
        "target_titles": icp.person_level["target_titles"],
        "competitors": icp.competitors,
        "personas": {k: {"weight": v["weight"], "patterns_count": len(v["title_patterns"])}
                     for k, v in icp.personas.items()},
    }
