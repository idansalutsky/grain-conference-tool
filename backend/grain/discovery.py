"""Conference discovery — find events not yet in the DB.

This is the brief's example AI feature: "a feature that helps the team find
conferences they don't already know about". Uses Perplexity Sonar via
OpenRouter so the results are GROUNDED (real URLs, citations, dates).

Flow:
  1. Frontend hits POST /api/discovery/conferences
  2. We call Perplexity Sonar with an ICP-aware query
  3. Parse the structured proposals back
  4. Persist each as a `decision_kind='conference_discovery_proposal'` in the
     feedback table — the UI lists pending proposals and the human approves
     each one with one click before it joins the main conferences list.

Costs: ~$0.005 per discovery query. Cap at one call per UI invocation.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from typing import Optional

from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.discovery")


# Today's reference date for the recency guard (DEFECT 11). Normal Python here,
# so a live system reads the real clock; injectable for tests / reproducibility.
def _today() -> date:
    return date.today()


def _norm_conf_name(name: str) -> str:
    """Year-stripped, punctuation-stripped, lowercased name for dedupe.

    Matches the normaliser used by the seed loader so a discovered
    "Global Fintech Fest 2026" collapses onto the seeded "Global Fintech Fest".
    """
    n = re.sub(r"\b(19|20)\d{2}\b", "", (name or "").lower())
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _existing_conference_names() -> set[str]:
    """Normalised names of every event already in the conferences table.

    DEFECT 9: discovery previously deduped only against the 6 hardcoded ICP
    anchor names, so it happily re-proposed events that were already seeded
    (e.g. "Global Fintech Fest"). We now dedupe against the live DB.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT name FROM conferences").fetchall()
    finally:
        conn.close()
    return {_norm_conf_name(r["name"]) for r in rows}


def _pending_proposal_names() -> set[str]:
    """Normalised names of proposals already sitting in the approval queue, so a
    second discovery run doesn't enqueue the same event twice."""
    names: set[str] = set()
    for p in list_pending_proposals(limit=500):
        if p.get("name"):
            names.add(_norm_conf_name(p["name"]))
    return names


def _proposal_is_upcoming(start: Optional[str], today: date) -> bool:
    """DEFECT 11: only keep events dated today-or-future.

    Accepts YYYY-MM-DD or YYYY-MM. A YYYY-MM is treated as the last day of that
    month so a current-month event isn't dropped. Undated proposals are kept
    (we can't prove they're stale) but year-only/obviously-past ones are dropped.
    """
    if not start:
        return True  # undated: can't disprove; let the human decide
    s = str(start).strip()
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", s)
    if not m:
        return True  # unparseable: don't silently drop
    y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
    if d:
        try:
            return date(y, mo, int(d)) >= today
        except ValueError:
            return True
    # YYYY-MM -> compare end of month
    return (y, mo) >= (today.year, today.month)


DISCOVERY_SYSTEM = (
    "You are a sales-ops analyst for Grain Finance, a fintech selling "
    "embedded cross-currency FX hedging to payment service providers, "
    "travel platforms, cross-border payment companies, and treasury teams. "
    "You will be asked to surface upcoming conferences relevant to that ICP. "
    "Reply with ONLY a JSON object: "
    '{"proposals": [{"name": str, "city": str, "country": str, '
    '"start_date": "YYYY-MM-DD or YYYY-MM", "vertical": "payments|treasury|travel|fintech_other|crypto", '
    '"why_relevant": str, "estimated_attendance": int OR null, "source_url": str}, ...]}.'
    "\nOnly include real events you can cite. If you're unsure, leave it out."
)


def _parse_proposals(text: str) -> list[dict]:
    """Sonar usually returns JSON but sometimes wraps it in prose. Try strict
    JSON, then the first {...} substring."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed.get("proposals"), list):
            return parsed["proposals"]
    except (json.JSONDecodeError, AttributeError):
        import re
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed.get("proposals"), list):
                    return parsed["proposals"]
            except json.JSONDecodeError:
                pass
    return []


def discover_conferences(*,
                         region_hint: Optional[str] = None,
                         vertical_hint: Optional[str] = None,
                         max_results: int = 6) -> dict:
    """Iteratively research NEW (untracked) conferences for Grain's ICP.

    Agentic, not single-shot: it runs a grounded search, drops dupes/stale/past,
    and — if the first pass came back thin (mostly events we already know) — runs
    ONE refinement pass that explicitly excludes what it just found and asks for
    different events. Optionally gap-targeted via `vertical_hint`. Each kept
    proposal is logged to `feedback` for the approval queue.
    Returns {proposals, citations, iterations, skipped_*}.
    """
    icp = IcpConfig.default()
    verticals = ", ".join(icp.company_level["verticals"])
    anchors = ", ".join(icp.anchor_events_known_attended[:6])
    region_clause = f"in the {region_hint} region" if region_hint else "globally"
    focus = f" Focus especially on {vertical_hint} events." if vertical_hint else ""

    today = _today()
    known = _existing_conference_names() | _pending_proposal_names()
    seen_this_run: set[str] = set()
    saved: list[dict] = []
    all_citations: list[dict] = []
    skipped_dupe = skipped_stale = 0
    last_err: Optional[str] = None
    MAX_PASSES = 2

    passes = 0
    for attempt in range(MAX_PASSES):
        passes = attempt + 1
        if len(saved) >= max_results:
            break
        exclude_found = (
            " Do NOT repeat any of these you already listed: "
            + "; ".join(s["name"] for s in saved) + "."
        ) if saved else ""
        query = (
            f"List {max_results} upcoming conferences {region_clause} most "
            f"relevant to Grain Finance's ICP (verticals: {verticals}).{focus} "
            f"EXCLUDE these anchor events we already track: {anchors}.{exclude_found} "
            "Focus on under-indexed events where CFOs, treasurers, heads of "
            "payments, or cross-border payment / travel-platform executives "
            "actually attend. For each, give: name, city, country, exact start "
            "date, vertical, one-sentence why_relevant, estimated_attendance if "
            "known, and a source_url citation. Output only JSON."
        )
        try:
            text, citations = llm.search_grounded(query, system=DISCOVERY_SYSTEM)
        except llm.LLMError as exc:
            last_err = str(exc)
            log.warning("discovery search failed (pass %d): %s", passes, exc)
            break
        all_citations.extend(citations or [])
        for p in _parse_proposals(text):
            if not isinstance(p, dict) or not p.get("name"):
                continue
            norm = _norm_conf_name(p["name"])
            if norm in known or norm in seen_this_run:
                skipped_dupe += 1
                continue
            if not _proposal_is_upcoming(p.get("start_date"), today):
                skipped_stale += 1
                continue
            seen_this_run.add(norm)
            proposal_id = "disc_" + uuid.uuid4().hex[:14]
            db.log_feedback(
                decision_kind="conference_discovery_proposal",
                target_kind="conference", target_id=proposal_id,
                after={**p, "citations": citations},
                reason=p.get("why_relevant"), decided_by="discovery_agent",
            )
            saved.append({"proposal_id": proposal_id, **p})
            if len(saved) >= max_results:
                break

    if not saved and last_err:
        return {"proposals": [], "citations": [], "error": last_err}
    return {
        "proposals": saved,
        "citations": all_citations,
        "iterations": passes,
        "skipped_duplicates": skipped_dupe,
        "skipped_stale": skipped_stale,
    }


# ---------------------------------------------------------------------------
# Region / attendance derivation for approved discoveries (DEFECT 10)
# ---------------------------------------------------------------------------
# Country -> Grain scoring region (NA / EU / APAC / MEA / LATAM). Same buckets
# the geo_cost_efficiency factor weights. Not exhaustive; unknown -> None and the
# factor falls back to its neutral default rather than mis-classifying.
_COUNTRY_REGION = {
    # North America
    "united states": "NA", "usa": "NA", "us": "NA", "canada": "NA", "mexico": "LATAM",
    # Europe
    "united kingdom": "EU", "uk": "EU", "ireland": "EU", "germany": "EU",
    "netherlands": "EU", "belgium": "EU", "luxembourg": "EU", "france": "EU",
    "switzerland": "EU", "spain": "EU", "portugal": "EU", "italy": "EU",
    "greece": "EU", "sweden": "EU", "norway": "EU", "denmark": "EU",
    "finland": "EU", "poland": "EU", "czechia": "EU", "czech republic": "EU",
    "hungary": "EU", "romania": "EU", "bulgaria": "EU", "austria": "EU",
    "estonia": "EU", "lithuania": "EU", "latvia": "EU",
    # APAC
    "singapore": "APAC", "malaysia": "APAC", "thailand": "APAC",
    "indonesia": "APAC", "vietnam": "APAC", "philippines": "APAC",
    "japan": "APAC", "south korea": "APAC", "china": "APAC", "taiwan": "APAC",
    "hong kong": "APAC", "india": "APAC", "australia": "APAC",
    "new zealand": "APAC",
    # MEA
    "uae": "MEA", "united arab emirates": "MEA", "saudi arabia": "MEA",
    "qatar": "MEA", "bahrain": "MEA", "egypt": "MEA", "israel": "MEA",
    "south africa": "MEA", "kenya": "MEA", "nigeria": "MEA",
    # LATAM
    "brazil": "LATAM", "argentina": "LATAM", "colombia": "LATAM",
    "chile": "LATAM", "peru": "LATAM",
}


def _region_for_country(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    return _COUNTRY_REGION.get(country.strip().lower())


# Conservative attendance fallback by format when the proposal omits it. These
# are deliberately modest so we never inflate a discovered event's reachability
# score; the human can correct the figure on review.
_ATTENDANCE_BY_FORMAT = {
    "expo": 5000, "trade_show": 5000, "festival": 8000, "forum": 1200,
    "summit": 1500, "conference": 1000, "leadership": 300,
    "webinar": 0, "virtual": 0,
}


def _estimate_attendance(fmt: Optional[str]) -> Optional[int]:
    if not fmt:
        return 1000
    return _ATTENDANCE_BY_FORMAT.get(fmt.strip().lower().replace(" ", "_"), 1000)


# ---------------------------------------------------------------------------
# Approval / rejection — the human-in-the-loop side
# ---------------------------------------------------------------------------
def list_pending_proposals(limit: int = 50) -> list[dict]:
    """Pending proposals = discovery_proposal rows with no matching
    discovery_approved or discovery_rejected for the same target_id."""
    conn = db.get_conn()
    try:
        proposals = conn.execute(
            "SELECT id, target_id, after_value, reason, decided_at "
            "FROM feedback WHERE decision_kind = 'conference_discovery_proposal' "
            "ORDER BY decided_at DESC LIMIT ?", (limit,),
        ).fetchall()
        decided = {
            r["target_id"] for r in conn.execute(
                "SELECT DISTINCT target_id FROM feedback "
                "WHERE decision_kind IN ('conference_discovery_approved', "
                "                         'conference_discovery_rejected')"
            ).fetchall()
        }
    finally:
        conn.close()
    out = []
    for r in proposals:
        if r["target_id"] in decided:
            continue
        try:
            payload = json.loads(r["after_value"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out.append({
            "proposal_id": r["target_id"],
            "feedback_id": r["id"],
            "proposed_at": r["decided_at"],
            "reason": r["reason"],
            **payload,
        })
    return out


def mentioned_events_signal(limit: int = 12) -> list[dict]:
    """Conversations as event intelligence: aggregate the events that captured
    contacts told reps they attend/are going to (encounter.mentioned_events).

    Returns [{name, count, contacts, tracked}] sorted by how many distinct
    contacts mentioned it. `tracked` = we already have a matching conference;
    an untracked event mentioned by multiple buyers is a strong, ground-up
    discovery candidate ("3 of our buyers keep mentioning X → we should be there").
    Read-only.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT e.contact_id, e.structured_json, "
            "c.primary_name, c.primary_company, c.primary_title, "
            "r.full_name AS rep_name "
            "FROM encounters e "
            "LEFT JOIN contacts c ON c.id = e.contact_id "
            "LEFT JOIN reps r ON r.id = e.rep_id "
            "WHERE e.structured_json LIKE '%mentioned_events%'"
        ).fetchall()
        tracked_norms = {
            _norm_conf_name(r["name"])
            for r in conn.execute("SELECT name FROM conferences").fetchall()
        }
    finally:
        conn.close()

    agg: dict[str, dict] = {}
    for r in rows:
        try:
            s = json.loads(r["structured_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        evs = s.get("mentioned_events") or []
        if not isinstance(evs, list):  # defensive: never iterate a bare string
            evs = [evs] if isinstance(evs, str) else []
        # who mentioned it, and to which rep — provenance for the signal.
        src = {
            "contact": r["primary_name"] or s.get("name") or "a buyer",
            "company": r["primary_company"] or s.get("company"),
            "title": r["primary_title"] or s.get("title"),
            "rep": r["rep_name"],
        }
        for ev in evs:
            name = (ev or "").strip() if isinstance(ev, str) else ""
            if not name:
                continue
            norm = _norm_conf_name(name)
            entry = agg.setdefault(
                norm, {"name": name, "count": 0, "_contacts": set(), "sources": []})
            entry["count"] += 1
            if r["contact_id"]:
                entry["_contacts"].add(r["contact_id"])
            entry["sources"].append(src)
            entry["tracked"] = norm in tracked_norms

    out = []
    for norm, e in agg.items():
        # de-dupe provenance by (contact, company); keep a few.
        seen, srcs = set(), []
        for sc in e["sources"]:
            key = (sc["contact"], sc.get("company"))
            if key in seen:
                continue
            seen.add(key)
            srcs.append(sc)
        out.append({
            "name": e["name"],
            "count": e["count"],
            "contacts": len(e["_contacts"]),
            "tracked": e.get("tracked", norm in tracked_norms),
            "sources": srcs[:4],
        })
    out.sort(key=lambda x: (x["contacts"], x["count"]), reverse=True)
    return out[:limit]


def research_mentioned_events(limit: int = 8) -> dict:
    """Close the loop on buyer-mentioned events.

    Takes the events our contacts MENTIONED that we don't already track, and
    researches each with a grounded search to verify it's real and find its NEXT
    upcoming occurrence (date / city / vertical / source). Verified ones become
    PENDING proposals (human-approved, NEVER auto-added), tagged with how many
    buyers mentioned them. Events the agent can't confirm come back in
    `not_found` so the rep knows it looked and came up empty. A PAST mention
    resolves to the event's next future edition. Returns
    {researched, proposals, not_found}.
    """
    signal = mentioned_events_signal(limit=30)
    untracked = [s for s in signal if not s.get("tracked")][:limit]
    if not untracked:
        return {"researched": 0, "proposals": [], "not_found": []}
    mention_counts = {_norm_conf_name(s["name"]): s.get("contacts", 1)
                      for s in untracked}
    names = [s["name"] for s in untracked]

    query = (
        "Our sales contacts mentioned these events in conversation: "
        + "; ".join(names) + ". "
        "For EACH that is a REAL, currently-operating conference, return its NEXT "
        "upcoming (today-or-future) occurrence with: name, city, country, region "
        "(LATAM|EU|NA|APAC|MEA), exact start_date (YYYY-MM-DD), vertical, a "
        "one-sentence why_relevant for a cross-border-FX fintech, and a "
        "source_url. If a mention refers to a PAST event, give its next future "
        "edition. OMIT any you cannot verify as real. Output only JSON "
        '{"proposals":[...]}.'
    )
    try:
        text, citations = llm.search_grounded(query, system=DISCOVERY_SYSTEM)
    except llm.LLMError as exc:
        return {"researched": len(names), "proposals": [],
                "not_found": names, "error": str(exc)}

    today = _today()
    known = _existing_conference_names() | _pending_proposal_names()
    seen: set[str] = set()
    saved: list[dict] = []
    confirmed: set[str] = set()  # mention norms that produced an upcoming proposal
    for p in _parse_proposals(text):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        norm = _norm_conf_name(p["name"])
        if norm in known or norm in seen:
            continue
        if not _proposal_is_upcoming(p.get("start_date"), today):
            continue  # not a verifiable UPCOMING edition → leave it as not-found
        seen.add(norm)
        # Tie this confirmed upcoming event back to the mention(s) it answers.
        matched_here = [mn for mn in mention_counts
                        if mn and (mn in norm or norm in mn)]
        for mn in matched_here:
            confirmed.add(mn)
        pid = "disc_" + uuid.uuid4().hex[:14]
        db.log_feedback(
            decision_kind="conference_discovery_proposal",
            target_kind="conference", target_id=pid,
            after={**p, "citations": citations, "provenance": "buyer-mentioned",
                   "mentioned_by": mention_counts.get(matched_here[0])
                   if matched_here else None},
            reason=p.get("why_relevant"), decided_by="mentioned_research_agent",
        )
        saved.append({"proposal_id": pid, **p, "provenance": "buyer-mentioned"})

    # Honest reporting: anything we couldn't confirm as an UPCOMING event.
    not_found = [s["name"] for s in untracked
                 if _norm_conf_name(s["name"]) not in confirmed]
    return {"researched": len(names), "proposals": saved, "not_found": not_found}


def create_conference_from_payload(payload: dict, *, decided_by: str = "ui",
                                   source: str = "discovery") -> dict:
    """Promote a discovered-event payload into a real, scored conferences row.

    Shared by the Discovery page (`approve_proposal`) AND the Events Brain's
    discovery gate, so an approved brain discovery becomes a real conference you
    can score and plan around — not a dead-end memory entry. Returns
    {conference_id, created}.

    Idempotent on name: if a conference with the same normalised name already
    exists, returns it (created=False) instead of creating a duplicate — so the
    Brain and the Discovery page can never produce two copies of one event.
    """
    name = (payload.get("name") or "").strip() or "Unknown"
    # Defensive: refuse the no-key placeholder / empty-shell payloads so a junk
    # "sample - configure a search key…" row can never reach the conferences table.
    if name.lower().startswith("sample - configure") \
            or "placeholder" in (payload.get("provenance") or "").lower():
        return {"conference_id": None, "created": False, "skipped": "placeholder"}
    norm = _norm_conf_name(name)
    conn = db.get_conn()
    try:
        for r in conn.execute("SELECT id, name FROM conferences").fetchall():
            if _norm_conf_name(r["name"]) == norm:
                return {"conference_id": r["id"], "created": False}
    finally:
        conn.close()

    # Carry region/format/attendance so an approved discovery scores on the same
    # footing as a seeded event (else geo/reachability/buyer factors degrade).
    region = (payload.get("region") or _region_for_country(payload.get("country")) or "")
    region = region.upper() or None
    fmt = payload.get("format") or "conference"
    attendance = payload.get("estimated_attendance")
    if attendance in (None, "", 0):
        attendance = _estimate_attendance(fmt)

    new_id = "c_disc_" + uuid.uuid4().hex[:12]
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO conferences (id, name, start_date, city, country, region, "
            "format, vertical, estimated_attendance, website, themes, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id, name, payload.get("start_date"), payload.get("city"),
                payload.get("country"), region, fmt, payload.get("vertical"),
                attendance, payload.get("source_url"), payload.get("why_relevant"),
                db.now_iso(), db.now_iso(),
            ),
        )
    finally:
        conn.close()

    db.log_feedback(
        decision_kind="conference_created",
        target_kind="conference", target_id=new_id,
        after={"conference_id": new_id, "source": source, **payload},
        decided_by=decided_by,
    )
    from . import scoring
    scoring.rescore_all()  # cheap — rescores the table in ~300ms
    return {"conference_id": new_id, "created": True}


def approve_proposal(proposal_id: str, *, decided_by: str = "ui") -> dict:
    """Promote a Discovery-page proposal into a real conferences row."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT after_value FROM feedback "
            "WHERE decision_kind = 'conference_discovery_proposal' "
            "AND target_id = ? ORDER BY decided_at DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError("proposal not found")
    try:
        payload = json.loads(row["after_value"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}

    result = create_conference_from_payload(
        payload, decided_by=decided_by, source="discovery")
    new_id = result["conference_id"]
    db.log_feedback(
        decision_kind="conference_discovery_approved",
        target_kind="conference", target_id=proposal_id,
        after={"conference_id": new_id, **payload},
        decided_by=decided_by,
    )
    return {"conference_id": new_id, "proposal": payload}


def reject_proposal(proposal_id: str, *, reason: str = "",
                    decided_by: str = "ui") -> None:
    db.log_feedback(
        decision_kind="conference_discovery_rejected",
        target_kind="conference", target_id=proposal_id,
        reason=reason, decided_by=decided_by,
    )
