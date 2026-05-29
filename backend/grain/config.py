"""Single source for env-driven configuration."""
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

# --- LLM ---
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_TEXT_MODEL = _env("OPENROUTER_TEXT_MODEL", "google/gemini-2.5-flash")
OPENROUTER_AUDIO_MODEL = _env("OPENROUTER_AUDIO_MODEL", "google/gemini-2.5-flash")
OPENROUTER_SEARCH_MODEL = _env("OPENROUTER_SEARCH_MODEL", "perplexity/sonar")

# --- Telegram (optional) ---
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_USERNAME = _env("TELEGRAM_BOT_USERNAME", "GrainSales_bot")

# --- HubSpot (optional) ---
HUBSPOT_PRIVATE_APP_TOKEN = _env("HUBSPOT_PRIVATE_APP_TOKEN")
DRY_RUN_HUBSPOT = HUBSPOT_PRIVATE_APP_TOKEN is None

LOG_LEVEL = _env("LOG_LEVEL", "INFO")


def summary() -> dict:
    """Redacted summary for healthcheck."""
    def redact(v: Optional[str]) -> str:
        if not v:
            return "<not set>"
        return f"{v[:6]}…{v[-4:]}" if len(v) > 12 else "<short>"
    return {
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "openrouter": redact(OPENROUTER_API_KEY),
        "telegram_token": redact(TELEGRAM_BOT_TOKEN),
        "telegram_bot": TELEGRAM_BOT_USERNAME or "<not set>",
        "hubspot_token": redact(HUBSPOT_PRIVATE_APP_TOKEN),
        "hubspot_dry_run": DRY_RUN_HUBSPOT,
    }
