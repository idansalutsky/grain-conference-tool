"""Shared FastAPI dependencies.

`require_admin` gates server-side admin operations (deploy-time things like
registering the Telegram webhook) behind a shared admin token. It fails
CLOSED: if no ADMIN_API_KEY is configured the endpoint returns 503 rather
than running unauthenticated. The token is compared with hmac.compare_digest
to avoid timing leaks.

This is intentionally lightweight — the product is a single-tenant demo tool
with no user-auth layer (per the brief's simplicity constraint). These guards
exist for the handful of operations that are genuinely dangerous if exposed
(e.g. repointing the capture bot's webhook).
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from .. import config


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    expected = config.ADMIN_API_KEY
    if not expected:
        # Fail closed — never run an admin op unauthenticated.
        raise HTTPException(
            503, "admin endpoints disabled: set ADMIN_API_KEY on the server"
        )
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "valid X-Admin-Token header required")
