"""Single source for configuration.

Precedence for integration secrets (OpenRouter / Perplexity / HubSpot /
Telegram): an in-app value stored in the `settings` table WINS over the
environment variable, which in turn wins over the built-in default.

This lets the keys be "configurable by the user, not hardcoded" without ever
breaking the env path: if no in-app value is set (or the settings table does
not exist yet), we silently fall back to the env var. Consumers keep reading
`config.OPENROUTER_API_KEY` etc. unchanged — a module-level ``__getattr__``
(PEP 562) resolves those names dynamically at access time.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH, override=False)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    return val if val not in (None, "") else default


DATA_DIR = Path(_env("DATA_DIR", str(ROOT / "data")))
DB_PATH = DATA_DIR / "grain.db"
AUDIO_DIR = DATA_DIR / "audio"
for p in (DATA_DIR, AUDIO_DIR):
    p.mkdir(parents=True, exist_ok=True)

# --- LLM (static, non-secret) ---
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_TEXT_MODEL = _env("OPENROUTER_TEXT_MODEL", "google/gemini-2.5-flash")
OPENROUTER_AUDIO_MODEL = _env("OPENROUTER_AUDIO_MODEL", "google/gemini-2.5-flash")
OPENROUTER_SEARCH_MODEL = _env("OPENROUTER_SEARCH_MODEL", "perplexity/sonar")

# --- Telegram (static, non-secret) ---
TELEGRAM_BOT_USERNAME = _env("TELEGRAM_BOT_USERNAME", "GrainSales_bot")

# --- Admin gate (server-side) ---
# Guards deploy-time admin ops (e.g. registering the Telegram webhook). When
# unset, those endpoints fail CLOSED (503) rather than running unauthenticated.
ADMIN_API_KEY = _env("ADMIN_API_KEY")

# Optional allowlist: if set, the Telegram webhook may only be pointed at this
# exact public origin (defence-in-depth so a stray admin call can't repoint the
# bot at an arbitrary host). Leave unset to accept any https origin.
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL")

LOG_LEVEL = _env("LOG_LEVEL", "INFO")


# ---------------------------------------------------------------------------
# Overridable integration secrets
# ---------------------------------------------------------------------------
# Maps the public config attribute name -> (settings_table_key, env_var_name).
# The settings key namespace mirrors the PUT /api/settings/integrations body.
_SECRET_KEYS: dict[str, tuple[str, str]] = {
    "OPENROUTER_API_KEY": ("integrations.openrouter_api_key", "OPENROUTER_API_KEY"),
    "PERPLEXITY_API_KEY": ("integrations.perplexity_api_key", "PERPLEXITY_API_KEY"),
    "HUBSPOT_PRIVATE_APP_TOKEN": (
        "integrations.hubspot_token", "HUBSPOT_PRIVATE_APP_TOKEN",
    ),
    "TELEGRAM_BOT_TOKEN": ("integrations.telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
}

# Public attr name -> settings key, for the settings router to reuse.
INTEGRATION_SETTING_KEYS: dict[str, str] = {
    attr: settings_key for attr, (settings_key, _) in _SECRET_KEYS.items()
}


def _setting_lookup(key: str) -> Optional[str]:
    """Lazy, defensive read from the settings table.

    Imports db lazily to avoid the config<->db import cycle, and swallows any
    error (missing table on a fresh DB, locked file, etc.) so we always fall
    back to env.
    """
    try:
        from . import db  # local import: db imports config at module top
        val = db.get_setting(key)
        return val if val not in (None, "") else None
    except Exception:  # pragma: no cover - defensive fallback path
        return None


def _resolve_secret(attr: str) -> Optional[str]:
    """In-app setting wins, else env var, else None."""
    settings_key, env_name = _SECRET_KEYS[attr]
    val = _setting_lookup(settings_key)
    if val:
        return val
    return _env(env_name)


def __getattr__(name: str):  # PEP 562 — module-level dynamic attributes
    """Resolve secret attributes dynamically so in-app overrides take effect
    at access time without restarting the app."""
    if name in _SECRET_KEYS:
        return _resolve_secret(name)
    if name == "DRY_RUN_HUBSPOT":
        return _resolve_secret("HUBSPOT_PRIVATE_APP_TOKEN") is None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def integration_status() -> dict:
    """Per-integration config status for GET /api/settings/integrations.

    Never returns raw secrets — only a masked tail + booleans describing where
    the value came from.
    """
    def mask(v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return f"…{v[-4:]}" if len(v) >= 4 else "…"

    out: dict[str, dict] = {}
    for attr, (settings_key, env_name) in _SECRET_KEYS.items():
        in_app = _setting_lookup(settings_key)
        env_val = _env(env_name)
        effective = in_app or env_val
        out[settings_key.split(".", 1)[1]] = {
            "configured": bool(effective),
            "masked": mask(effective),
            "source": "in_app" if in_app else ("env" if env_val else None),
        }
    return out


def summary() -> dict:
    """Redacted summary for healthcheck."""
    def redact(v: Optional[str]) -> str:
        if not v:
            return "<not set>"
        return f"{v[:6]}…{v[-4:]}" if len(v) > 12 else "<short>"
    return {
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "openrouter": redact(_resolve_secret("OPENROUTER_API_KEY")),
        "telegram_token": redact(_resolve_secret("TELEGRAM_BOT_TOKEN")),
        "telegram_bot": TELEGRAM_BOT_USERNAME or "<not set>",
        "hubspot_token": redact(_resolve_secret("HUBSPOT_PRIVATE_APP_TOKEN")),
        "hubspot_dry_run": _resolve_secret("HUBSPOT_PRIVATE_APP_TOKEN") is None,
    }
