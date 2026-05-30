"""/api/hubspot — push a contact to HubSpot (dry-run default).

LIVE VERIFICATION (the exact 4 steps a user runs with a real token)
-------------------------------------------------------------------
No real portal token ships with this repo, so pushes default to DRY-RUN: the
payload (carrying the grain_* intelligence) is built and returned, but nothing
is written. To go live and prove a real write end-to-end:

  1. PASTE THE TOKEN — In the app: Settings → Integrations → HubSpot, paste a
     HubSpot *Private App* token (scopes: crm.objects.contacts.read/write,
     crm.schemas.contacts.read/write). Saving it stores the token in the
     `settings` table; the in-app value WINS over any env var, and the moment a
     token is present DRY_RUN_HUBSPOT flips to false (config PEP-562 resolves
     this at access time — no restart). Equivalent API call:
        PUT /api/settings/integrations  {"hubspot_token": "pat-na1-..."}

  2. GET /api/hubspot/status — the single "is this working?" check. Expect
     {connected: true, dry_run: false, token_source: "in_app",
      properties_ready: true, portal: {portal_id: ...}}. If the token is bad
     you get {connected: false, reason: "invalid_token"} — fix the token, never
     a 500.

  3. POST /api/hubspot/setup — idempotently create the grain_* custom contact
     properties + the "Grain" property group in the portal. Expect
     {ok: true, created: [...], existing: [...], failed: []}. Safe to re-run
     (existing props are reported as "existing", not errors). This step is
     optional — the first real push self-heals missing props — but running it
     makes a fresh portal ready up front.

  4. POST /api/hubspot/push/{contact_id} — push ONE contact for real. Expect
     {ok: true, dry_run: false, hubspot_id: "<numeric id>"}. Then open the
     contact in HubSpot (Contacts → search the email): the grain_* properties
     (Arc Verdict, Arc Confidence, Arc Summary, Nudge Active/Text, Last
     Encounter At, Follow-up Draft, Source Event) carry the intelligence the
     tool computed — the judgment travels WITH the contact.

  (Post-event close: POST /api/hubspot/push-event/{conference_id} pushes
  everyone met at one event, each with its grain_* intel.)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import config
from ... import hubspot as hs

router = APIRouter(prefix="/api/hubspot", tags=["hubspot"])


@router.get("/status")
def status() -> dict:
    """The single "is this working?" check a non-dev runs after pasting a token.

    Never leaks the token. Never 500s — every failure (no token, bad token,
    timeout, network) maps to a structured ``connected: false`` with a
    human-readable ``reason``.

    Returns ``{connected, dry_run, token_source, properties_ready, reason,
    portal?}``:
      - ``connected``       — did a cheap authenticated GET succeed?
      - ``dry_run``         — does a default push WRITE to HubSpot, or only
                              log a synthetic OK? Reflects the effective config
                              (true when no real token is configured).
      - ``token_source``    — ``env`` / ``in_app`` / ``none``.
      - ``properties_ready``— None until connected; True once connected (the
                              first push self-heals any missing grain_* props).
      - ``reason``          — ``ok`` / ``no_token`` / ``invalid_token`` / etc.
    """
    src = hs._token_source()
    if src == "none":
        return {
            "connected": False, "dry_run": True, "token_source": "none",
            "properties_ready": None, "reason": "no_token",
        }
    # A token is configured, so a default push is NOT a dry-run at the config
    # level (config.DRY_RUN_HUBSPOT is "token is None"). Surface that truth.
    try:
        conn = hs.test_connection()
    except Exception as exc:  # pragma: no cover - test_connection never raises
        return {
            "connected": False, "dry_run": False, "token_source": src,
            "properties_ready": None, "reason": f"error: {str(exc)[:160]}",
        }
    connected = bool(conn.get("connected"))
    return {
        "connected": connected,
        "dry_run": bool(config.DRY_RUN_HUBSPOT),
        "token_source": src,
        # We only know properties are *ready* once setup has been run; we don't
        # eagerly list them here to keep status cheap. Connected => the first
        # push will self-heal them if absent.
        "properties_ready": True if connected else None,
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
