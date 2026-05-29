"""HubSpot push — contact + intelligence.

The push doesn't just carry name/email/company — it carries the intelligence
the system computed. Custom HubSpot properties (prefixed `grain_`) hold:

  grain_arc_verdict        — warming / flat / cooling / tire_kicker
  grain_arc_confidence     — 0..1
  grain_arc_summary        — one-sentence rationale
  grain_nudge_active       — boolean
  grain_nudge_text         — the rep-facing nudge if active
  grain_last_encounter_at  — ISO timestamp of most recent capture
  grain_followup_draft     — last-drafted follow-up email body

The judgment travels WITH the contact. It doesn't die in our tool.

DRY_RUN_HUBSPOT defaults to true when HUBSPOT_PRIVATE_APP_TOKEN is not set —
the push returns a synthetic OK response so the demo doesn't fail.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from . import config, db

log = logging.getLogger("grain.hubspot")

HUBSPOT_API = "https://api.hubapi.com"
HTTP_TIMEOUT = 20.0

# The custom property group all grain_* properties live under in HubSpot.
GROUP_NAME = "grain"
GROUP_LABEL = "Grain"

CUSTOM_PROPS = [
    "grain_arc_verdict", "grain_arc_confidence", "grain_arc_summary",
    "grain_nudge_active", "grain_nudge_text",
    "grain_last_encounter_at", "grain_followup_draft", "grain_source_event",
]

# Full HubSpot Properties API definition for every grain_* property.
# fieldType/type chosen to match the payload built by `_contact_payload`:
#   - arc verdict      -> enumeration (fixed set of states)
#   - confidence       -> string (we send it as a "0.0..1.0" string)
#   - summaries/text   -> text (single line) / textarea (multi-line bodies)
#   - nudge_active     -> bool checkbox (we send "true"/"false")
#   - last_encounter   -> datetime
# These are *contact* properties (object type = contacts).
_ARC_OPTIONS = [
    {"label": "Warming", "value": "warming", "displayOrder": 0, "hidden": False},
    {"label": "Flat", "value": "flat", "displayOrder": 1, "hidden": False},
    {"label": "Cooling", "value": "cooling", "displayOrder": 2, "hidden": False},
    {"label": "Tire kicker", "value": "tire_kicker", "displayOrder": 3, "hidden": False},
]

_PROP_DEFINITIONS: dict[str, dict[str, Any]] = {
    "grain_arc_verdict": {
        "label": "Grain Arc Verdict", "type": "enumeration",
        "fieldType": "select", "options": _ARC_OPTIONS,
    },
    "grain_arc_confidence": {
        "label": "Grain Arc Confidence", "type": "string", "fieldType": "text",
    },
    "grain_arc_summary": {
        "label": "Grain Arc Summary", "type": "string", "fieldType": "textarea",
    },
    "grain_nudge_active": {
        "label": "Grain Nudge Active", "type": "bool", "fieldType": "booleancheckbox",
        "options": [
            {"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
            {"label": "No", "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    "grain_nudge_text": {
        "label": "Grain Nudge Text", "type": "string", "fieldType": "textarea",
    },
    "grain_last_encounter_at": {
        "label": "Grain Last Encounter At", "type": "datetime",
        "fieldType": "date",
    },
    "grain_followup_draft": {
        "label": "Grain Follow-up Draft", "type": "string", "fieldType": "textarea",
    },
    "grain_source_event": {
        "label": "Grain Source Event", "type": "string", "fieldType": "text",
    },
}


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _resolve_token() -> Optional[str]:
    """Effective token (in-app setting wins over env), or None."""
    return config.HUBSPOT_PRIVATE_APP_TOKEN


def _token_source() -> str:
    """Where the effective token comes from, without ever leaking it.

    Mirrors config precedence: in-app setting > env var > none.
    """
    try:
        from . import db
        if db.get_setting(config.INTEGRATION_SETTING_KEYS["HUBSPOT_PRIVATE_APP_TOKEN"]):
            return "in_app"
    except Exception:  # pragma: no cover - defensive
        pass
    import os
    if os.getenv("HUBSPOT_PRIVATE_APP_TOKEN"):
        return "env"
    return "none"


def test_connection(token: Optional[str] = None) -> dict:
    """Validate the HubSpot token with a cheap authenticated GET.

    Returns ``{connected: bool, reason: str, portal?: dict}``. Never raises:
    a missing token, a bad token, a timeout, or any network error all map to
    ``connected: False`` with a human-readable ``reason`` so the caller (and
    ultimately the rep clicking "Test connection") gets a clear answer instead
    of an opaque push failure.
    """
    token = token or _resolve_token()
    if not token:
        return {"connected": False, "reason": "no_token"}

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            r = client.get(
                f"{HUBSPOT_API}/account-info/v3/details",
                headers=_headers(token),
            )
    except httpx.HTTPError as exc:
        return {"connected": False, "reason": f"network_error: {str(exc)[:160]}"}
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return {"connected": False, "reason": f"error: {str(exc)[:160]}"}

    if r.status_code in (401, 403):
        return {"connected": False, "reason": "invalid_token",
                "status_code": r.status_code}
    if r.status_code >= 400:
        return {"connected": False, "reason": f"http_{r.status_code}",
                "status_code": r.status_code}

    portal: dict[str, Any] = {}
    try:
        data = r.json()
        portal = {
            "portal_id": data.get("portalId"),
            "account_type": data.get("accountType"),
            "time_zone": data.get("timeZone"),
            "ui_domain": data.get("uiDomain"),
        }
    except Exception:  # pragma: no cover - body parse is best-effort
        portal = {}
    return {"connected": True, "reason": "ok", "portal": portal}


def _ensure_group(client: httpx.Client, token: str) -> Optional[str]:
    """Create the 'grain' property group if absent. Returns an error string on
    hard failure, else None. 409/'already exists' is treated as success."""
    body = {"name": GROUP_NAME, "label": GROUP_LABEL, "displayOrder": -1}
    r = client.post(
        f"{HUBSPOT_API}/crm/v3/properties/contacts/groups",
        headers=_headers(token), json=body,
    )
    if r.status_code < 300 or r.status_code == 409:
        return None
    txt = (r.text or "").lower()
    if "already exists" in txt or "duplicate" in txt:
        return None
    return f"group_create_failed: http_{r.status_code} {r.text[:160]}"


def ensure_custom_properties(token: Optional[str] = None) -> dict:
    """Idempotently create every grain_* custom contact property in the
    connected HubSpot portal via the Properties API.

    A fresh portal has none of these properties, so the very first live push
    would 400. Calling this once makes a fresh portal ready. Safe to call
    repeatedly: an existing property (409 / "already exists") is treated as
    success, not an error.

    Returns ``{ok, dry_run?, created: [...], existing: [...], failed: [...],
    reason?}``. Never raises — with no token it returns a graceful
    ``{ok: False, reason: "no_token"}`` result.
    """
    token = token or _resolve_token()
    if not token:
        return {"ok": False, "dry_run": True, "reason": "no_token",
                "created": [], "existing": [], "failed": []}

    created: list[str] = []
    existing: list[str] = []
    failed: list[dict] = []

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            group_err = _ensure_group(client, token)
            if group_err:
                # Non-fatal: the group may already exist under a different
                # path, or the portal disallows group creation. We still try
                # the properties (HubSpot will accept groupName="grain" if it
                # exists, or we surface a clear failure per-property below).
                log.warning("[HubSpot] property group ensure: %s", group_err)

            for name in CUSTOM_PROPS:
                spec = _PROP_DEFINITIONS[name]
                body = {
                    "name": name,
                    "label": spec["label"],
                    "type": spec["type"],
                    "fieldType": spec["fieldType"],
                    "groupName": GROUP_NAME,
                }
                if "options" in spec:
                    body["options"] = spec["options"]
                try:
                    r = client.post(
                        f"{HUBSPOT_API}/crm/v3/properties/contacts",
                        headers=_headers(token), json=body,
                    )
                except httpx.HTTPError as exc:
                    failed.append({"property": name, "error": str(exc)[:160]})
                    continue

                if r.status_code < 300:
                    created.append(name)
                elif r.status_code == 409 or "already exists" in (r.text or "").lower():
                    existing.append(name)
                else:
                    failed.append({"property": name,
                                   "status_code": r.status_code,
                                   "error": r.text[:200]})
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"network_error: {str(exc)[:160]}",
                "created": created, "existing": existing, "failed": failed}
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return {"ok": False, "reason": f"error: {str(exc)[:160]}",
                "created": created, "existing": existing, "failed": failed}

    ok = not failed
    return {"ok": ok, "created": created, "existing": existing, "failed": failed,
            "reason": "ok" if ok else "some_properties_failed"}


def _missing_property_error(text: str) -> bool:
    """Heuristic: does this 400 body indicate an unknown/missing property?"""
    t = (text or "").lower()
    return (
        "property" in t and (
            "does not exist" in t
            or "doesn't exist" in t
            or "was not found" in t
            or "not found" in t
            or "PROPERTY_DOESNT_EXIST".lower() in t
            or "invalid property" in t
        )
    )


def _contact_payload(contact: dict, latest_enc: Optional[dict],
                     event_name: Optional[str] = None) -> dict:
    name = contact.get("primary_name") or ""
    first, *rest = name.split(" ")
    last = " ".join(rest)
    props: dict[str, Any] = {
        "email": contact.get("primary_email") or "",
        "firstname": first,
        "lastname": last,
        "phone": contact.get("phone") or "",
        "company": contact.get("primary_company") or "",
        "jobtitle": contact.get("primary_title") or "",
        "grain_arc_verdict": contact.get("arc_verdict") or "",
        "grain_arc_confidence": str(contact.get("arc_confidence") or 0),
        "grain_arc_summary": contact.get("arc_summary") or "",
        "grain_nudge_active": "true" if contact.get("nudge_active") else "false",
        "grain_nudge_text": contact.get("nudge_text") or "",
    }
    if latest_enc:
        props["grain_last_encounter_at"] = latest_enc["captured_at"]
        props["grain_followup_draft"] = latest_enc.get("followup_draft") or ""
    if event_name:
        props["grain_source_event"] = event_name
    return {"properties": props}


def _upsert_contact(client: httpx.Client, token: str, payload: dict) -> httpx.Response:
    """Upsert one contact by email (PATCH idProperty=email, falling back to a
    POST create on 404). Returns the raw httpx.Response for the caller to
    interpret (including the missing-property self-heal)."""
    headers = _headers(token)
    email = payload["properties"].get("email") or ""
    if email:
        r = client.patch(
            f"{HUBSPOT_API}/crm/v3/objects/contacts/{email}?idProperty=email",
            headers=headers, json=payload,
        )
        if r.status_code == 404:
            r = client.post(
                f"{HUBSPOT_API}/crm/v3/objects/contacts",
                headers=headers, json=payload,
            )
        return r
    return client.post(
        f"{HUBSPOT_API}/crm/v3/objects/contacts",
        headers=headers, json=payload,
    )


def push_contact(contact_id: str, *, dry_run: Optional[bool] = None) -> dict:
    """Push one contact (with grain_* intelligence) to HubSpot.

    Returns {ok, dry_run, status_code?, hubspot_id?, payload}.
    """
    if dry_run is None:
        dry_run = config.DRY_RUN_HUBSPOT

    conn = db.get_conn()
    try:
        crow = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not crow:
            return {"ok": False, "error": "contact_not_found"}
        contact = dict(crow)
        latest_enc_row = conn.execute(
            "SELECT captured_at, followup_draft, conference_id FROM encounters "
            "WHERE contact_id = ? ORDER BY captured_at DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
        latest_enc = dict(latest_enc_row) if latest_enc_row else None
        event_name = None
        if latest_enc and latest_enc.get("conference_id"):
            crow2 = conn.execute(
                "SELECT name FROM conferences WHERE id = ?",
                (latest_enc["conference_id"],),
            ).fetchone()
            event_name = crow2["name"] if crow2 else None
    finally:
        conn.close()

    payload = _contact_payload(contact, latest_enc, event_name)

    if dry_run:
        log.info("[HubSpot DRY-RUN] would PUSH contact=%s email=%s",
                 contact_id, payload["properties"].get("email"))
        return {"ok": True, "dry_run": True, "payload": payload}

    token = config.HUBSPOT_PRIVATE_APP_TOKEN
    if not token:
        return {"ok": False, "error": "no HubSpot token", "payload": payload}

    # Don't fire a blind write at an unverified token: validate first so the
    # rep gets a clear "invalid_token"/"network_error" reason instead of an
    # opaque write failure.
    conn_check = test_connection(token)
    if not conn_check.get("connected"):
        return {"ok": False, "error": f"not_connected: {conn_check.get('reason')}",
                "connection": conn_check, "payload": payload}

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            r = _upsert_contact(client, token, payload)
            # Self-heal: a fresh portal lacks the grain_* properties, so the
            # first write 400s. Create them once, then retry the write.
            if r.status_code >= 400 and _missing_property_error(r.text):
                log.info("[HubSpot] missing grain_* property on push — "
                         "ensuring custom properties then retrying")
                ensure_custom_properties(token)
                r = _upsert_contact(client, token, payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)[:200], "payload": payload}

    if r.status_code >= 400:
        return {"ok": False, "status_code": r.status_code,
                "error": r.text[:300], "payload": payload}

    data = r.json()
    hubspot_id = data.get("id")
    if hubspot_id:
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE contacts SET hubspot_contact_id = ?, updated_at = ? WHERE id = ?",
                (hubspot_id, db.now_iso(), contact_id),
            )
        finally:
            conn.close()
    return {"ok": True, "status_code": r.status_code,
            "hubspot_id": hubspot_id, "payload": payload}


def push_event(conference_id: str, *, dry_run: Optional[bool] = None) -> dict:
    """Post-event close: push every contact met at one event to HubSpot, each
    carrying its grain_* intelligence (arc, nudge, follow-up draft, source
    event). Returns a per-contact summary. Contacts with no email are reported
    as skipped (HubSpot upsert keys on email)."""
    conn = db.get_conn()
    try:
        crow = conn.execute(
            "SELECT name FROM conferences WHERE id = ?", (conference_id,)
        ).fetchone()
        if not crow:
            return {"ok": False, "error": "conference_not_found"}
        rows = conn.execute(
            "SELECT DISTINCT e.contact_id, c.primary_email "
            "FROM encounters e JOIN contacts c ON c.id = e.contact_id "
            "WHERE e.conference_id = ? AND e.contact_id IS NOT NULL",
            (conference_id,),
        ).fetchall()
    finally:
        conn.close()

    pushed, skipped, failed = [], [], []
    for r in rows:
        cid = r["contact_id"]
        if not (r["primary_email"] or "").strip():
            skipped.append({"contact_id": cid, "reason": "no email on contact"})
            continue
        res = push_contact(cid, dry_run=dry_run)
        if res.get("ok"):
            pushed.append({"contact_id": cid, "hubspot_id": res.get("hubspot_id"),
                           "dry_run": res.get("dry_run", False)})
        else:
            failed.append({"contact_id": cid, "error": res.get("error")})

    return {
        "ok": True, "conference_id": conference_id, "event_name": crow["name"],
        "pushed": len(pushed), "skipped": len(skipped), "failed": len(failed),
        "detail": {"pushed": pushed, "skipped": skipped, "failed": failed},
    }
