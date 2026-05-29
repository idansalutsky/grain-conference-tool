"""L1 — MIDDLE-MANAGEMENT rollups. The core of the hierarchical Grain Brain.

The owner's critique of the old brain: "top-50 by salience is a dumb cutoff with
no judgment, and it drops dots." This tier fixes that.

Three tiers of memory:
  L0 — DOTS    : the relational tables (contacts, companies, conferences, reps,
                 encounters, coverage). Full recall, never dropped, never changed
                 here. This module only READS them.
  L1 — ROLLUPS : ONE JUDGED summary per ENTITY (this module). The number of
                 rollups is bounded by the number of ENTITIES, never by a magic
                 50. Every event with encounters, every account (company with
                 contacts), every active segment gets a rollup — so NOTHING is
                 dropped. Each rollup connects its dots into structured features
                 (counts, arc mix, hit-rate, finance%, verdict), a judged prose
                 `summary`, and a `priority` used only for ORDERING (never for
                 dropping). Stored in db.brain_rollup, UNIQUE(scope_type,scope_id).
  L2 — BRAIN   : the space summaries (spaces.py) now roll up THESE L1 rollups,
                 not a salience-truncated top-50 pile. Every conclusion is
                 reachable down through L1 → L0.

JUDGMENT, not a cutoff. A rollup's `priority` orders entities by how much they
deserve attention (a warming multi-event account outranks a one-touch flat one),
but priority NEVER removes a rollup — the entity itself is the bound.

PERFORMANCE. rebuild_all_rollups() runs at seed over 100+ entities, so features
+ summary are DETERMINISTIC and fast by default — NO LLM call in the bulk loop.
An OPTIONAL refine_rollup_summary(scope_type, scope_id) upgrades ONE rollup's
prose on demand (when viewed / for the top-priority few) and caches it. The whole
module is hermetic (no network) so seeds + tests never hit the wire.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from typing import Any, Optional

from .. import db, llm

log = logging.getLogger("grain.brain.rollups")

SCOPE_TYPES = ("event", "account", "segment")

# Arc verdicts we roll up. Order = display order.
_ARCS = ("warming", "flat", "cooling", "tire_kicker")

# Persona/title fragments that indicate a finance / treasury buying-committee hit
# (Grain's ICP buyer). Used to measure how much of an event's audience is the
# real buyer, and which committee personas an event/account touched.
_FINANCE_TITLE_KEYS = (
    "cfo", "chief financial", "treasur", "finance", "controller", "fp&a",
    "head of payments", "vp finance", "vp, finance", "head of finance",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _norm_company_key(name: str) -> str:
    """Canonical account key from a company name (the reliable join — contacts
    carry primary_company by name; company_id is often NULL)."""
    base = (name or "").lower().strip()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "unknown_company"


def _arc_priority_weight(arc: str | None) -> float:
    """How much an arc verdict pulls an entity's priority UP. Judgment: a warming
    relationship is the thing to act on; a tire_kicker is worth knowing but lower."""
    return {
        "warming": 1.0,
        "tire_kicker": 0.55,
        "cooling": 0.45,
        "flat": 0.35,
    }.get((arc or "").lower(), 0.35)


def _is_finance_title(title: str | None) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _FINANCE_TITLE_KEYS)


def _audience_finance_pct(conf_row: dict) -> Optional[float]:
    """MEASURED finance/treasury share of an event's audience, from the curated
    audience_composition_json (NOT invented). None when unknown."""
    raw = conf_row.get("audience_composition_json")
    if not raw:
        return None
    try:
        comp = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(comp, dict):
        return None
    # Prefer an explicit finance/treasury bucket; sum any finance-ish keys.
    total = 0.0
    found = False
    for k, v in comp.items():
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        kl = k.lower()
        if any(s in kl for s in ("cfo", "treasur", "finance")):
            total += val
            found = True
    return round(total, 1) if found else None


# ---------------------------------------------------------------------------
# Persist primitive — UNIQUE upsert (idempotent rebuild)
# ---------------------------------------------------------------------------
def upsert_rollup(scope_type: str, scope_id: str, *, title: str, summary: str,
                  features: dict, priority: float, source_count: int) -> dict:
    """Idempotent upsert of ONE rollup by (scope_type, scope_id)."""
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"unknown scope_type {scope_type!r}; valid: {SCOPE_TYPES}")
    now = db.now_iso()
    features_json = json.dumps(features, ensure_ascii=False)
    conn = db.get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM brain_rollup WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        ).fetchone()
        rid = existing["id"] if existing else ("roll_" + uuid.uuid4().hex[:14])
        conn.execute(
            "INSERT INTO brain_rollup (id, scope_type, scope_id, title, summary, "
            "features_json, priority, source_count, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(scope_type, scope_id) DO UPDATE SET "
            "title=excluded.title, summary=excluded.summary, "
            "features_json=excluded.features_json, priority=excluded.priority, "
            "source_count=excluded.source_count, updated_at=excluded.updated_at",
            (rid, scope_type, scope_id, title, summary, features_json,
             float(priority), int(source_count), now),
        )
    finally:
        conn.close()
    return {"id": rid, "scope_type": scope_type, "scope_id": scope_id,
            "title": title, "summary": summary, "features": features,
            "priority": float(priority), "source_count": int(source_count),
            "updated_at": now}


def _row_to_dict(r) -> dict:
    d = dict(r)
    try:
        d["features"] = json.loads(d.pop("features_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["features"] = {}
    return d


def get_rollup(scope_type: str, scope_id: str) -> Optional[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, scope_type, scope_id, title, summary, features_json, "
            "priority, source_count, updated_at FROM brain_rollup "
            "WHERE scope_type = ? AND scope_id = ?", (scope_type, scope_id),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def list_rollups(scope_type: Optional[str] = None, *, limit: int = 200,
                 sort: str = "priority") -> list[dict]:
    """All rollups (optionally filtered by scope), ordered by priority by default.

    NOTE: `limit` bounds how many rows are RETURNED to a caller/UI page — it does
    NOT cap how many rollups EXIST. Every entity always has its rollup in the
    table; this is pagination, not a salience cutoff.
    """
    order = "priority DESC, source_count DESC" if sort == "priority" \
        else "updated_at DESC"
    conn = db.get_conn()
    try:
        if scope_type:
            rows = conn.execute(
                "SELECT id, scope_type, scope_id, title, summary, features_json, "
                f"priority, source_count, updated_at FROM brain_rollup "
                f"WHERE scope_type = ? ORDER BY {order} LIMIT ?",
                (scope_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, scope_type, scope_id, title, summary, features_json, "
                f"priority, source_count, updated_at FROM brain_rollup "
                f"ORDER BY scope_type, {order} LIMIT ?", (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def count_rollups(scope_type: Optional[str] = None) -> int:
    conn = db.get_conn()
    try:
        if scope_type:
            return int(conn.execute(
                "SELECT COUNT(*) FROM brain_rollup WHERE scope_type = ?",
                (scope_type,)).fetchone()[0])
        return int(conn.execute("SELECT COUNT(*) FROM brain_rollup").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# L1 MANAGER: rollup_event — judge ONE conference from its encounters + audience
# ---------------------------------------------------------------------------
def rollup_event(conference_id: str) -> Optional[dict]:
    """Roll up ONE event: read all encounters at it + its audience/agenda + the
    contacts met → judged features + summary + priority.

    Returns the upserted rollup, or None if the conference doesn't exist.
    """
    conn = db.get_conn()
    try:
        conf = conn.execute(
            "SELECT id, name, vertical, region, tier, score, "
            "estimated_attendance, agenda_summary, audience_composition_json "
            "FROM conferences WHERE id = ?", (conference_id,),
        ).fetchone()
        if conf is None:
            return None
        conf = dict(conf)
        # All encounters captured AT this event, joined to their contact (for arc).
        enc_rows = [dict(r) for r in conn.execute(
            "SELECT e.id AS enc_id, e.contact_id, e.meeting_requested, "
            "e.followup_draft, c.primary_name, c.primary_company, "
            "c.primary_title, c.arc_verdict "
            "FROM encounters e LEFT JOIN contacts c ON c.id = e.contact_id "
            "WHERE e.conference_id = ?", (conference_id,),
        ).fetchall()]
    finally:
        conn.close()

    n_encounters = len(enc_rows)
    contact_ids = {r["contact_id"] for r in enc_rows if r.get("contact_id")}
    n_contacts_met = len(contact_ids)

    # Arc mix is judged at the CONTACT level (one verdict per person), not per
    # raw encounter — so a person met 3 times counts once toward the mix.
    contact_arc: dict[str, str] = {}
    contact_title: dict[str, str] = {}
    for r in enc_rows:
        cid = r.get("contact_id")
        if cid:
            contact_arc.setdefault(cid, r.get("arc_verdict") or "flat")
            if r.get("primary_title"):
                contact_title.setdefault(cid, r.get("primary_title"))
    arc_mix = {a: 0 for a in _ARCS}
    for arc in contact_arc.values():
        arc_mix[arc if arc in arc_mix else "flat"] += 1

    buying_committee_hits = sum(1 for t in contact_title.values()
                                if _is_finance_title(t))
    follow_ups_drafted = sum(1 for r in enc_rows if r.get("followup_draft"))
    finance_pct = _audience_finance_pct(conf)

    # JUDGED verdict — worth returning? This is the manager's call, derived from
    # the dots: warming relationships + a finance-heavy audience say YES.
    warming = arc_mix["warming"]
    tire = arc_mix["tire_kicker"]
    verdict, verdict_reason = _event_verdict(
        n_encounters, warming, tire, finance_pct, conf.get("tier"))

    features = {
        "conference_name": conf.get("name"),
        "vertical": conf.get("vertical"),
        "region": conf.get("region"),
        "tier": conf.get("tier"),
        "score": conf.get("score"),
        "estimated_attendance": conf.get("estimated_attendance"),
        "n_encounters": n_encounters,
        "n_contacts_met": n_contacts_met,
        "arc_mix": arc_mix,
        "buying_committee_personas_hit": buying_committee_hits,
        "measured_finance_pct": finance_pct,
        "follow_ups_drafted": follow_ups_drafted,
        "worth_returning_verdict": verdict,
    }

    # Priority (ORDERING only): activity + warming pull it up; tier helps a
    # never-yet-attended A/B event still rank as worth-planning.
    tier_boost = {"A": 0.30, "B": 0.18, "C": 0.05}.get(conf.get("tier") or "", 0.0)
    priority = round(
        min(1.0,
            0.10
            + min(n_contacts_met, 10) * 0.04
            + warming * 0.12
            + tier_boost
            + (0.10 if (finance_pct or 0) >= 40 else 0.0)),
        4,
    )

    summary = _event_summary_deterministic(conf, features, verdict_reason)
    title = conf.get("name") or conference_id
    return upsert_rollup("event", conference_id, title=title, summary=summary,
                         features=features, priority=priority,
                         source_count=n_encounters)


def _event_verdict(n_enc: int, warming: int, tire: int,
                   finance_pct: Optional[float], tier: str | None) -> tuple[str, str]:
    if n_enc == 0:
        # Never attended — judge by the audience fit (planning signal).
        if tier == "A" or (finance_pct or 0) >= 40:
            return "worth_attending", "strong audience fit, not yet worked"
        return "untested", "no encounters yet; audience fit unproven"
    if warming >= 1:
        return "worth_returning", f"{warming} warming relationship(s) started here"
    if tire >= 1 and warming == 0:
        return "low_yield", f"{tire} tire-kicker(s), no warming relationships"
    return "marginal", "encounters but no clear warming signal yet"


def _event_summary_deterministic(conf: dict, f: dict, verdict_reason: str) -> str:
    mix = f["arc_mix"]
    mix_str = ", ".join(f"{mix[a]} {a}" for a in _ARCS if mix[a]) or "no arcs yet"
    fin = (f"{f['measured_finance_pct']}% finance/treasury audience"
           if f.get("measured_finance_pct") is not None
           else "finance-audience share unknown")
    return (
        f"{conf.get('name')} ({conf.get('vertical') or 'unknown vertical'} / "
        f"{conf.get('region') or 'unknown region'}, tier {conf.get('tier') or '?'}). "
        f"Met {f['n_contacts_met']} contact(s) over {f['n_encounters']} encounter(s); "
        f"arc mix: {mix_str}. {f['buying_committee_personas_hit']} buying-committee "
        f"(finance/treasury) contact(s); {fin}; {f['follow_ups_drafted']} follow-up(s) "
        f"drafted. Verdict: {f['worth_returning_verdict']} - {verdict_reason}."
    )


# ---------------------------------------------------------------------------
# L1 MANAGER: rollup_account — judge ONE company across EVERY event
# ---------------------------------------------------------------------------
def rollup_account(company: str) -> Optional[dict]:
    """Roll up ONE account: read ALL contacts + their encounters for the company
    across every event → judged features + summary + priority.

    `company` may be a company NAME or a normalized key — we match on the
    normalized primary_company (the reliable join). Returns the upserted rollup,
    or None if no contacts match.
    """
    key = _norm_company_key(company)
    conn = db.get_conn()
    try:
        # Resolve a display name for the account from any matching contact.
        contacts = [dict(r) for r in conn.execute(
            "SELECT id, primary_name, primary_company, primary_title, "
            "arc_verdict, arc_summary, updated_at FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''",
        ).fetchall()]
        contacts = [c for c in contacts
                    if _norm_company_key(c.get("primary_company")) == key]
        if not contacts:
            return None
        contact_ids = [c["id"] for c in contacts]
        qmarks = ",".join("?" * len(contact_ids))
        enc_rows = [dict(r) for r in conn.execute(
            f"SELECT id, contact_id, conference_id, captured_at, meeting_requested "
            f"FROM encounters WHERE contact_id IN ({qmarks})", contact_ids,
        ).fetchall()]
    finally:
        conn.close()

    display_name = next((c.get("primary_company") for c in contacts
                         if c.get("primary_company")), company)
    n_contacts = len(contacts)
    n_encounters = len(enc_rows)
    events_spanned = len({r.get("conference_id") for r in enc_rows
                          if r.get("conference_id")})
    last_seen = max((r.get("captured_at") or "" for r in enc_rows), default="") or None

    arc_counts = Counter((c.get("arc_verdict") or "flat") for c in contacts)
    arc_mix = {a: int(arc_counts.get(a, 0)) for a in _ARCS}
    has_warming = arc_mix["warming"] > 0
    has_tire_kicker = arc_mix["tire_kicker"] > 0
    # ACCOUNT ARC — rolled up from per-contact arcs (judgment: any warming wins;
    # else a tire_kicker dominates; else cooling; else flat).
    if has_warming:
        account_arc = "warming"
    elif has_tire_kicker:
        account_arc = "tire_kicker"
    elif arc_mix["cooling"]:
        account_arc = "cooling"
    else:
        account_arc = "flat"

    features = {
        "company": display_name,
        "company_key": key,
        "n_contacts": n_contacts,
        "n_encounters": n_encounters,
        "events_spanned": events_spanned,
        "account_arc": account_arc,
        "arc_mix": arc_mix,
        "last_seen": last_seen,
        "has_warming": has_warming,
        "has_tire_kicker": has_tire_kicker,
    }

    # Priority (ORDERING only): a warming, multi-touch, multi-event account is
    # the thing to act on. Never used to drop the rollup.
    priority = round(
        min(1.0,
            0.10
            + _arc_priority_weight(account_arc) * 0.45
            + min(n_contacts, 6) * 0.04
            + min(events_spanned, 4) * 0.05),
        4,
    )

    summary = _account_summary_deterministic(display_name, features)
    return upsert_rollup("account", key, title=display_name, summary=summary,
                         features=features, priority=priority,
                         source_count=n_contacts)


def _account_summary_deterministic(name: str, f: dict) -> str:
    mix = f["arc_mix"]
    mix_str = ", ".join(f"{mix[a]} {a}" for a in _ARCS if mix[a]) or "no arc yet"
    span = (f"across {f['events_spanned']} event(s)" if f["events_spanned"]
            else "no event linkage")
    tail = ""
    if f["has_warming"]:
        tail = " WARMING - prioritise the close."
    elif f["has_tire_kicker"]:
        tail = " Tire-kicker present - qualify hard before more effort."
    return (
        f"{name}: {f['n_contacts']} contact(s), {f['n_encounters']} encounter(s) "
        f"{span}. Account arc: {f['account_arc']} (mix: {mix_str})."
        f"{tail}"
    )


# ---------------------------------------------------------------------------
# L1 MANAGER: rollup_segment — judge ONE vertical (the cross-cut)
# ---------------------------------------------------------------------------
def rollup_segment(segment_key: str) -> Optional[dict]:
    """Roll up ONE segment (a vertical): aggregate the events + accounts in it →
    judged features + summary. segment_key is a vertical name (e.g. 'treasury').
    """
    seg = (segment_key or "").strip().lower()
    if not seg:
        return None
    conn = db.get_conn()
    try:
        confs = [dict(r) for r in conn.execute(
            "SELECT id, name, vertical, region, tier FROM conferences "
            "WHERE LOWER(IFNULL(vertical,'')) = ?", (seg,),
        ).fetchall()]
        # Accounts in this segment = companies whose contacts were met at an
        # event in this vertical (connect dots across tiers).
        conf_ids = [c["id"] for c in confs]
        companies_in_seg: set[str] = set()
        if conf_ids:
            qmarks = ",".join("?" * len(conf_ids))
            rows = conn.execute(
                f"SELECT DISTINCT ct.primary_company FROM encounters e "
                f"JOIN contacts ct ON ct.id = e.contact_id "
                f"WHERE e.conference_id IN ({qmarks}) "
                f"AND ct.primary_company IS NOT NULL", conf_ids,
            ).fetchall()
            companies_in_seg = {_norm_company_key(r["primary_company"])
                                for r in rows if r["primary_company"]}
    finally:
        conn.close()

    if not confs:
        return None

    n_events = len(confs)
    tier_mix = {t: 0 for t in ("A", "B", "C")}
    for c in confs:
        t = c.get("tier")
        if t in tier_mix:
            tier_mix[t] += 1
    regions = sorted({c.get("region") for c in confs if c.get("region")})
    n_accounts = len(companies_in_seg)
    # Coverage gap: a target segment with no A-tier events (or no worked accounts)
    # is a gap to go discover.
    coverage_gap = (tier_mix["A"] == 0) or (n_accounts == 0)

    features = {
        "segment": seg,
        "n_events": n_events,
        "tier_mix": tier_mix,
        "regions": regions,
        "n_accounts": n_accounts,
        "coverage_gap": coverage_gap,
    }
    priority = round(
        min(1.0, 0.15 + tier_mix["A"] * 0.10 + min(n_accounts, 8) * 0.05
            + (0.15 if coverage_gap else 0.0)),
        4,
    )
    summary = (
        f"Segment '{seg}': {n_events} event(s) "
        f"(A={tier_mix['A']}, B={tier_mix['B']}, C={tier_mix['C']}) "
        f"across {', '.join(regions) or 'no regions'}; {n_accounts} worked "
        f"account(s). " + ("COVERAGE GAP - go discover here."
                           if coverage_gap else "Coverage adequate.")
    )
    return upsert_rollup("segment", seg, title=f"Segment: {seg}", summary=summary,
                         features=features, priority=priority,
                         source_count=n_events)


# ---------------------------------------------------------------------------
# rebuild_all_rollups — recompute EVERY entity. Idempotent, deterministic, fast.
# ---------------------------------------------------------------------------
def rebuild_all_rollups() -> dict:
    """Recompute every L1 rollup from the L0 dots. IDEMPOTENT (UNIQUE upsert).

    Scope:
      - event   : every conference that has encounters OR is tier A/B (so a
                  high-fit event you haven't worked yet still gets a planning
                  rollup).
      - account : EVERY company that has at least one contact — one rollup per
                  distinct company. This is the no-cap proof: N companies → N
                  account rollups, NOTHING dropped.
      - segment : every distinct vertical present in the conferences table.

    DETERMINISTIC + FAST: no LLM call here (refine_rollup_summary is the opt-in
    upgrade). Hermetic — safe at seed and in tests.
    """
    conn = db.get_conn()
    try:
        # Events to roll up: those with encounters OR tier A/B.
        event_ids = [r["id"] for r in conn.execute(
            "SELECT DISTINCT c.id FROM conferences c "
            "WHERE c.id IN (SELECT DISTINCT conference_id FROM encounters "
            "               WHERE conference_id IS NOT NULL) "
            "   OR c.tier IN ('A','B')"
        ).fetchall()]
        # Accounts: every distinct company that has a contact (by normalized name).
        company_names = [r["primary_company"] for r in conn.execute(
            "SELECT DISTINCT primary_company FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''"
        ).fetchall()]
        # Segments: every distinct vertical.
        segments = [r["vertical"] for r in conn.execute(
            "SELECT DISTINCT vertical FROM conferences "
            "WHERE vertical IS NOT NULL AND vertical != ''"
        ).fetchall()]
    finally:
        conn.close()

    # Dedupe accounts by normalized key (so 'Maersk' / 'A.P. Moller Maersk' don't
    # double-count — one rollup per real account).
    seen_acct: set[str] = set()
    n_acct = 0
    for name in company_names:
        key = _norm_company_key(name)
        if key in seen_acct:
            continue
        seen_acct.add(key)
        if rollup_account(name):
            n_acct += 1

    n_event = 0
    for cid in event_ids:
        if rollup_event(cid):
            n_event += 1

    seen_seg: set[str] = set()
    n_seg = 0
    for seg in segments:
        sk = (seg or "").strip().lower()
        if not sk or sk in seen_seg:
            continue
        seen_seg.add(sk)
        if rollup_segment(seg):
            n_seg += 1

    return {"events": n_event, "accounts": n_acct, "segments": n_seg,
            "total": n_event + n_acct + n_seg}


# ---------------------------------------------------------------------------
# Recompute hook — best-effort, called after a real capture.
# ---------------------------------------------------------------------------
def recompute_for_contact(contact_id: str) -> dict:
    """After a capture touches `contact_id`, recompute the affected account +
    event rollup(s). Best-effort: wrapped by the caller so it never breaks
    capture. Returns what was recomputed.
    """
    out: dict[str, Any] = {"account": None, "events": []}
    if not contact_id:
        return out
    conn = db.get_conn()
    try:
        crow = conn.execute(
            "SELECT primary_company FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        company = crow["primary_company"] if crow else None
        ev_ids = [r["conference_id"] for r in conn.execute(
            "SELECT DISTINCT conference_id FROM encounters "
            "WHERE contact_id = ? AND conference_id IS NOT NULL", (contact_id,)
        ).fetchall()]
    finally:
        conn.close()
    if company:
        r = rollup_account(company)
        out["account"] = r["scope_id"] if r else None
    for cid in ev_ids:
        if rollup_event(cid):
            out["events"].append(cid)
    return out


# ---------------------------------------------------------------------------
# OPTIONAL LLM refinement — upgrade ONE rollup's prose on demand, cache it.
# NOT called in the bulk rebuild loop (that stays deterministic + fast).
# ---------------------------------------------------------------------------
_REFINE_SYSTEM = (
    "You are a sales-ops manager for Grain Finance (embedded cross-border FX "
    "hedging). You receive STRUCTURED features for ONE entity (an event, account, "
    "or segment) that were rolled up from real captured data. Write ONE crisp, "
    "judged summary (2-3 sentences) a rep can act on: what this entity is, the "
    "relationship/coverage state, and the recommended next move. Ground every "
    "claim in the features - do not invent numbers. Reply with ONLY JSON "
    '{"summary": "..."}.'
)


def refine_rollup_summary(scope_type: str, scope_id: str) -> Optional[dict]:
    """OPTIONALLY upgrade ONE rollup's prose with the LLM, and CACHE it back into
    the rollup row. No-op (returns the existing rollup) when no key is configured
    or the rollup doesn't exist. Used on view / for the top-priority few — never
    in the bulk rebuild loop.
    """
    roll = get_rollup(scope_type, scope_id)
    if roll is None:
        return None
    if not llm.config.OPENROUTER_API_KEY:
        return roll  # deterministic summary stands; hermetic
    try:
        data = llm.chat_json(
            [{"role": "system", "content": _REFINE_SYSTEM},
             {"role": "user", "content": json.dumps(
                 {"scope_type": scope_type, "scope_id": scope_id,
                  "title": roll.get("title"), "features": roll.get("features")},
                 ensure_ascii=False)}],
            temperature=0.2, max_tokens=300,
        )
        cand = (data.get("summary") or "").strip()
    except llm.LLMError as exc:
        log.info("refine_rollup_summary(%s,%s) fell back: %s",
                 scope_type, scope_id, exc)
        return roll
    if not cand:
        return roll
    # Cache the refined prose back (keeps features/priority/source_count).
    return upsert_rollup(
        scope_type, scope_id, title=roll.get("title"), summary=cand,
        features=roll.get("features") or {}, priority=roll.get("priority") or 0.0,
        source_count=roll.get("source_count") or 0,
    )
