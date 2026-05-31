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
            "icp_score, vertical, verified, linkedin_url FROM people WHERE conference_id = ? "
            "AND persona IN ('BUYER','CHAMPION','PAIN_OWNER','ENTRY_POINT') "
            "ORDER BY verified DESC, persona_weight DESC, icp_score DESC NULLS LAST LIMIT ?",
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
        out = []
        for r in rows:
            d = dict(r)
            # The cross-conference proof: how many distinct events this person was
            # met at — so "warming across conferences" is shown, not just claimed.
            d["n_conferences"] = conn.execute(
                "SELECT COUNT(DISTINCT conference_id) FROM encounters "
                "WHERE contact_id = ? AND conference_id IS NOT NULL", (d["id"],)
            ).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def _warming_count() -> int:
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE nudge_active = 1"
        ).fetchone()[0]
    finally:
        conn.close()


def _buyer_density(raw: Optional[str]) -> Optional[int]:
    """Measured finance/treasury % of the audience — the buyer-density signal."""
    if not raw:
        return None
    try:
        comp = json.loads(raw)
        v = comp.get("cfo_treasury_finance_pct")
        return int(v) if v is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _covering_reps(conn, conf_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT r.full_name FROM coverage c JOIN reps r ON r.id = c.rep_id "
        "WHERE c.conference_id = ? ORDER BY r.full_name",
        (conf_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _priority_events(n: int = 8) -> list[dict]:
    """The manager's planning view: the highest-value events still ahead, each
    tagged with who's covering it, what it costs, and how dense the buyers are.
    Uncovered tier-A events are the gap that matters — they self-highlight in
    this one ranked list rather than needing a separate 'gaps' panel. The extra
    fields back an expandable row so a manager can drill without leaving."""
    conn = db.get_conn()
    try:
        today = datetime.now(timezone.utc).date()
        rows = conn.execute(
            "SELECT id, name, start_date, end_date, city, country, region, "
            "score, tier, vertical, estimated_attendance, cost_pass_usd, "
            "audience_composition_json, agenda_summary "
            "FROM conferences WHERE start_date >= ? AND tier IN ('A','B') "
            "ORDER BY score DESC, start_date ASC LIMIT ?",
            (today.isoformat(), n),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["buyer_density_pct"] = _buyer_density(d.pop("audience_composition_json"))
            d["covering_reps"] = _covering_reps(conn, d["id"])
            d["reps_assigned"] = len(d["covering_reps"])
            sd = _parse_date(d["start_date"])
            d["days_until"] = (sd.date() - today).days if sd else None
            out.append(d)
        return out
    finally:
        conn.close()


def _events_with_results(n: int = 3) -> list[dict]:
    """Events the team has actually worked — anything with captured encounters —
    most-recent activity first, with the results that came back. This is the
    'what did our events return' recap (the product, not a CRUD list). We key on
    'has encounters' rather than calendar-past because the value is the same: an
    event in motion with connections to show."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.name, c.city, c.country, c.tier, "
            "MAX(e.captured_at) AS last_at, COUNT(*) AS encounters "
            "FROM encounters e JOIN conferences c ON c.id = e.conference_id "
            "GROUP BY c.id ORDER BY last_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["contacts"] = conn.execute(
                "SELECT COUNT(DISTINCT contact_id) FROM encounters "
                "WHERE conference_id = ? AND contact_id IS NOT NULL", (d["id"],)
            ).fetchone()[0]
            d["meetings"] = conn.execute(
                "SELECT COUNT(*) FROM encounters WHERE conference_id = ? "
                "AND meeting_requested = 1", (d["id"],)
            ).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def _floor_summary() -> dict:
    """The state of the floor — the numbers a manager wants the second they open
    the tool: how much high-value ground is ahead, how much of it we actually
    have someone on, and how the team is deployed."""
    conn = db.get_conn()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        ab = conn.execute(
            "SELECT id, tier FROM conferences "
            "WHERE start_date >= ? AND tier IN ('A','B')", (today,)
        ).fetchall()
        covered_ids = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT conference_id FROM coverage").fetchall()
        }
        events_ahead = len(ab)
        covered = sum(1 for r in ab if r[0] in covered_ids)
        uncovered_tier_a = sum(
            1 for r in ab if r[1] == "A" and r[0] not in covered_ids)
        reps_total = conn.execute("SELECT COUNT(*) FROM reps").fetchone()[0]
        reps_deployed = conn.execute(
            "SELECT COUNT(DISTINCT rep_id) FROM coverage").fetchone()[0]
        # next imminent uncovered high-value event (the time-pressure one)
        next_uncovered = conn.execute(
            "SELECT name, start_date FROM conferences c WHERE c.tier = 'A' "
            "AND c.start_date >= ? "
            "AND NOT EXISTS (SELECT 1 FROM coverage v WHERE v.conference_id = c.id) "
            "ORDER BY c.start_date ASC LIMIT 1", (today,)
        ).fetchone()
        return {
            "events_ahead": events_ahead,
            "covered": covered,
            "uncovered": events_ahead - covered,
            "uncovered_tier_a": uncovered_tier_a,
            "reps_total": reps_total,
            "reps_deployed": reps_deployed,
            "next_uncovered_name": next_uncovered[0] if next_uncovered else None,
            "next_uncovered_date": next_uncovered[1] if next_uncovered else None,
        }
    finally:
        conn.close()


def _under_invested_segment() -> Optional[dict]:
    """The one intelligence read worth leading with: which vertical has the most
    high-value ground we're not on. Combines the scoring data with the coverage
    reality — a conclusion, not a raw list."""
    conn = db.get_conn()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        covered_ids = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT conference_id FROM coverage").fetchall()
        }
        rows = conn.execute(
            "SELECT vertical, id, tier FROM conferences "
            "WHERE start_date >= ? AND tier IN ('A','B')", (today,)
        ).fetchall()
        by_vert: dict[str, dict] = {}
        for vert, cid, tier in rows:
            v = vert or "other"
            slot = by_vert.setdefault(v, {"vertical": v, "ahead": 0, "uncovered": 0})
            slot["ahead"] += 1
            if cid not in covered_ids:
                slot["uncovered"] += 1
        ranked = [s for s in by_vert.values() if s["uncovered"] > 0]
        if not ranked:
            return None
        ranked.sort(key=lambda s: s["uncovered"], reverse=True)
        return ranked[0]
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
        "nudges": _active_nudges(4),
        "warming_count": _warming_count(),
        "priority_events": _priority_events(15),
        "floor": _floor_summary(),
        "under_invested_segment": _under_invested_segment(),
        "recent_results": _events_with_results(3),
        "recent_captures": _recent_captures(rep_id, 5),
        "pending_discovery_count": _pending_discovery_count(),
        "review_needed_count": _review_needed_count(),
    }
