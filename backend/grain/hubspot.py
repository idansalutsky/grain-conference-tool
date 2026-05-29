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


CUSTOM_PROPS = [
    "grain_arc_verdict", "grain_arc_confidence", "grain_arc_summary",
    "grain_nudge_active", "grain_nudge_text",
    "grain_last_encounter_at", "grain_followup_draft",
]


def _contact_payload(contact: dict, latest_enc: Optional[dict]) -> dict:
    name = contact.get("primary_name") or ""
    first, *rest = name.split(" ")
    last = " ".join(rest)
    props: dict[str, Any] = {
        "email": contact.get("primary_email") or "",
        "firstname": first,
        "lastname": last,
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
    return {"properties": props}


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
            "SELECT captured_at, followup_draft FROM encounters "
            "WHERE contact_id = ? ORDER BY captured_at DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
        latest_enc = dict(latest_enc_row) if latest_enc_row else None
    finally:
        conn.close()

    payload = _contact_payload(contact, latest_enc)

    if dry_run:
        log.info("[HubSpot DRY-RUN] would PUSH contact=%s email=%s",
                 contact_id, payload["properties"].get("email"))
        return {"ok": True, "dry_run": True, "payload": payload}

    token = config.HUBSPOT_PRIVATE_APP_TOKEN
    if not token:
        return {"ok": False, "error": "no HubSpot token", "payload": payload}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20.0) as client:
            # Upsert by email — HubSpot's contacts API
            email = payload["properties"].get("email") or ""
            if email:
                # Try to update by idProperty=email
                r = client.patch(
                    f"{HUBSPOT_API}/crm/v3/objects/contacts/{email}?idProperty=email",
                    headers=headers, json=payload,
                )
                if r.status_code == 404:
                    r = client.post(
                        f"{HUBSPOT_API}/crm/v3/objects/contacts",
                        headers=headers, json=payload,
                    )
            else:
                r = client.post(
                    f"{HUBSPOT_API}/crm/v3/objects/contacts",
                    headers=headers, json=payload,
                )
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
