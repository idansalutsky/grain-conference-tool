"""'Today' aggregator — the rep's one-screen morning view.

The single entry point that ties the 7 AI features together:

  - Active event (or countdown to next)
  - Top 3 ICP-fit targets at the active event (with prep state)
  - Active warming nudges to clear
  - Most recent captures (rep's own pulse)
  - Pending discovery proposals awaiting approval

This file is intentionally a single function — `for_rep(rep_id)` — that
emits the dict the frontend renders. No business logic; just compose
existing queries so the surface stays tight.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from . import db


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _conf_has_targets(conn, conf_id: str) -> bool:
    """Helper — does this event have any ICP-fit buying-committee people?"""
    row = conn.execute(
        "SELECT 1 FROM people WHERE conference_id = ? "
        "AND persona IN ('BUYER','CHAMPION','PAIN_OWNER','ENTRY_POINT') LIMIT 1",
        (conf_id,),
    ).fetchone()
    return bool(row)


def _active_or_next_event(rep_id: str) -> dict:
    """Find the event the rep is currently AT, or the next upcoming one.

    Order of precedence:
      1. rep.active_conference_id (explicitly bound via /start <token>)
      2. An event happening RIGHT NOW (start_date <= today <= end_date)
         AND with at least one ICP-fit target
      3. The next upcoming event with the highest score that has targets
    """
    conn = db.get_conn()
    try:
        rep_row = conn.execute(
            "SELECT active_conference_id FROM reps WHERE id = ?", (rep_id,)
        ).fetchone()
        active_id = None
        if rep_row:
            try:
                active_id = rep_row["active_conference_id"]
            except (IndexError, KeyError):
                active_id = None
        if active_id:
            row = conn.execute(
                "SELECT id, name, start_date, end_date, city, country, "
                "score, tier, vertical FROM conferences WHERE id = ?",
                (active_id,),
            ).fetchone()
            if row:
                return {**dict(row), "is_active_now": True, "is_explicit_bind": True}

        today = datetime.now(timezone.utc).date().isoformat()
        # Events happening NOW — require end_date to be in the future too
        rows = conn.execute(
            "SELECT id, name, start_date, end_date, city, country, score, tier, vertical "
            "FROM conferences WHERE start_date <= ? AND end_date >= ? "
            "ORDER BY score DESC",
            (today, today),
        ).fetchall()
        for row in rows:
            d = dict(row)
            if _conf_has_targets(conn, d["id"]):
                return {**d, "is_active_now": True, "is_explicit_bind": False}

        # Next upcoming event with the highest score AND targets
        rows = conn.execute(
            "SELECT id, name, start_date, end_date, city, country, score, tier, vertical "
            "FROM conferences WHERE start_date > ? "
            "ORDER BY score DESC, start_date ASC LIMIT 20",
            (today,),
        ).fetchall()
        for row in rows:
            d = dict(row)
            if _conf_has_targets(conn, d["id"]):
                sd = _parse_date(d["start_date"])
                d["days_until"] = (sd.date() - datetime.now(timezone.utc).date()).days if sd else None
                d["is_active_now"] = False
                return d

        # No event at all
        return {}
    finally:
        conn.close()


def _top_targets(conference_id: str, n: int = 3) -> list[dict]:
    """The buying-committee targets the rep should hunt for at this event.

    Also flags whether a brief already exists for each (the pre-event prep
    state) so the rep knows what's ready to read on the way in.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, full_name, title, company_name, persona, persona_weight, "
            "icp_score, vertical FROM people WHERE conference_id = ? "
            "AND persona IN ('BUYER','CHAMPION','PAIN_OWNER','ENTRY_POINT') "
            "ORDER BY persona_weight DESC, icp_score DESC NULLS LAST LIMIT ?",
            (conference_id, n),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Does a brief exist for this person?
            br = conn.execute(
                "SELECT id, generated_at FROM briefs WHERE person_id = ? "
                "ORDER BY generated_at DESC LIMIT 1",
                (d["id"],),
            ).fetchone()
            d["has_brief"] = bool(br)
            d["brief_id"] = br["id"] if br else None
            out.append(d)
        return out
    finally:
        conn.close()


def _active_nudges(limit: int = 3) -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, primary_name, primary_title, primary_company, "
            "arc_verdict, arc_confidence, nudge_text, updated_at "
            "FROM contacts WHERE nudge_active = 1 "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _recent_captures(rep_id: str, limit: int = 5) -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT e.id, e.contact_id, e.captured_at, e.capture_mode, "
            "e.structured_json, e.meeting_requested, c.primary_name as contact_name "
            "FROM encounters e LEFT JOIN contacts c ON c.id = e.contact_id "
            "WHERE e.rep_id = ? ORDER BY e.captured_at DESC LIMIT ?",
            (rep_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["structured"] = json.loads(d.pop("structured_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["structured"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def _pending_discovery_count() -> int:
    conn = db.get_conn()
    try:
        proposed = conn.execute(
            "SELECT COUNT(DISTINCT target_id) FROM feedback "
            "WHERE decision_kind = 'conference_discovery_proposal'"
        ).fetchone()[0]
        decided = conn.execute(
            "SELECT COUNT(DISTINCT target_id) FROM feedback "
            "WHERE decision_kind IN ('conference_discovery_approved', "
            "                         'conference_discovery_rejected')"
        ).fetchone()[0]
        return max(0, proposed - decided)
    finally:
        conn.close()


def _review_needed_count() -> int:
    """Encounters that the resolver flagged as ambiguous match. Surfaced on
    the Contacts page's review section."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT after_value FROM feedback "
            "WHERE decision_kind = 'entity_resolution' "
            "AND decided_at >= datetime('now', '-30 days')"
        ).fetchall()
    finally:
        conn.close()
    n = 0
    for r in rows:
        try:
            v = json.loads(r["after_value"] or "{}")
            if v.get("decision") == "review_needed":
                n += 1
        except (json.JSONDecodeError, TypeError):
            continue
    return n


def for_rep(rep_id: str) -> dict:
    """One-call aggregator that powers the rep's morning screen."""
    event = _active_or_next_event(rep_id)
    return {
        "rep_id": rep_id,
        "event": event,
        "targets": _top_targets(event["id"], 3) if event.get("id") else [],
        "nudges": _active_nudges(3),
        "recent_captures": _recent_captures(rep_id, 5),
        "pending_discovery_count": _pending_discovery_count(),
        "review_needed_count": _review_needed_count(),
    }
