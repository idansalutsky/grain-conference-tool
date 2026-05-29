"""/api/hubspot — push a contact to HubSpot (dry-run default)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import hubspot as hs

router = APIRouter(prefix="/api/hubspot", tags=["hubspot"])


@router.get("/status")
def status() -> dict:
    """Connection + readiness status. Never leaks the token.

    Returns ``{connected, dry_run, token_source, properties_ready, reason,
    portal?}``. ``token_source`` is one of ``env`` / ``in_app`` / ``none``.
    With no token configured this returns ``connected: false`` gracefully
    (dry_run: true) so the UI can prompt the rep to paste a key.
    """
    src = hs._token_source()
    if src == "none":
        return {
            "connected": False, "dry_run": True, "token_source": "none",
            "properties_ready": None, "reason": "no_token",
        }
    conn = hs.test_connection()
    return {
        "connected": bool(conn.get("connected")),
        "dry_run": False if conn.get("connected") else True,
        "token_source": src,
        # We only know properties are *ready* once setup has been run; we don't
        # eagerly list them here to keep status cheap. Connected => the first
        # push will self-heal them if absent.
        "properties_ready": True if conn.get("connected") else None,
        "reason": conn.get("reason"),
        **({"portal": conn["portal"]} if conn.get("portal") else {}),
    }


@router.post("/setup")
def setup() -> dict:
    """Idempotently create the grain_* custom contact properties in the
    connected portal. Returns what was created vs. already existing.

    With no token this returns ``{ok: false, reason: "no_token"}`` (200) so the
    UI can show "paste a token first" rather than erroring.
    """
    return hs.ensure_custom_properties()


@router.post("/push/{contact_id}")
def push_contact(contact_id: str, dry_run: bool | None = None) -> dict:
    out = hs.push_contact(contact_id, dry_run=dry_run)
    if not out.get("ok") and out.get("error") == "contact_not_found":
        raise HTTPException(404, "contact not found")
    return out


@router.post("/push-event/{conference_id}")
def push_event(conference_id: str, dry_run: bool | None = None) -> dict:
    """Post-event close: push everyone met at this event (with grain_* intel)."""
    out = hs.push_event(conference_id, dry_run=dry_run)
    if not out.get("ok") and out.get("error") == "conference_not_found":
        raise HTTPException(404, "conference not found")
    return out
