"""Companies — accounts as a first-class entity.

What lives here:
  - normalize_name      → canonical lowercased key for dedupe
  - resolve_company     → upsert by normalized name + variants, returns id
  - enrich_domain       → ask an LLM to infer the domain from a name
  - logo_url_for_domain → Google s2 favicons (no auth, 128px)
  - backfill            → walk people+contacts, create companies, link FK
  - score_company       → company-level ICP score
  - rollup              → per-company aggregate for the CompanyDetail page

The brain insight "duplicate company entries: Maersk vs AP Moller Maersk"
was the signal that the resolver wasn't account-aware. This module fixes
that: every person now has a company_id pointing at one canonical row,
even when the surface name varies.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.companies")


# ---------------------------------------------------------------------------
# Normalization — the single source of truth for dedupe
# ---------------------------------------------------------------------------
_SUFFIXES = (
    " inc", " inc.", " corp", " corp.", " corporation", " ltd", " ltd.",
    " limited", " llc", " plc", " gmbh", " sa", " s.a.", " ag", " ab",
    " holdings", " group", " co", " co.", " company",
)
# Manual aliases (the system also learns more from LLM domain lookup; this
# catches the obvious ones that show up in our seed data).
_ALIAS_TO_CANONICAL: dict[str, str] = {
    # normalized form → canonical normalized form
    "maersk": "maersk",
    "apmoller maersk": "maersk",
    "ap moller maersk": "maersk",
    "booking": "booking holdings",
    "bookingcom": "booking holdings",
    "booking.com": "booking holdings",
    "booking holdings": "booking holdings",
    "tripcom": "trip.com",
    "trip": "trip.com",
    "trip.com": "trip.com",
    "alphabet": "google",
    "meta platforms": "meta",
    "facebook": "meta",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip company suffixes, strip non-alphanum-space. Stable."""
    if not name:
        return ""
    s = name.lower().strip()
    # Remove trailing legal suffixes (one pass is enough for our data)
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if s.endswith(suf):
                s = s[: -len(suf)].rstrip(" ,")
                changed = True
                break
    # Strip everything except alnum, space, dot (dot kept for "trip.com")
    s = re.sub(r"[^a-z0-9. ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Apply manual canonical alias — try as-is, then dot-stripped.
    # This catches "A.P. Moller Maersk" → "ap moller maersk" → "maersk".
    if s in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[s]
    s_no_dot = s.replace(".", "")
    s_no_dot = re.sub(r"\s+", " ", s_no_dot).strip()
    return _ALIAS_TO_CANONICAL.get(s_no_dot, s)


# ---------------------------------------------------------------------------
# Domain inference + logo
# ---------------------------------------------------------------------------
DOMAIN_LOOKUP_SYSTEM = (
    "You are a B2B data tool. Given a list of company names, return the "
    "primary corporate website domain for each. Domain only: no http, no "
    "www, no path. If you don't know with high confidence, return null. "
    'Reply with ONLY a JSON object: {"map": {"<name>": "<domain or null>"}}.'
)


def lookup_domains_llm(names: list[str]) -> dict[str, Optional[str]]:
    """One LLM call to map name → domain for up to ~50 names.

    Returns {original_name: domain_or_None}. Resilient to partial coverage.
    """
    if not names:
        return {}
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    user = json.dumps({"names": seen}, ensure_ascii=False)
    try:
        out = llm.chat_json(
            [
                {"role": "system", "content": DOMAIN_LOOKUP_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0, max_tokens=2000,
        )
    except llm.LLMError as exc:
        log.warning("domain lookup failed: %s", exc)
        return {n: None for n in seen}
    raw = out.get("map") or {}
    result: dict[str, Optional[str]] = {}
    for n in seen:
        v = raw.get(n)
        if isinstance(v, str) and v.strip() and "." in v:
            v = v.strip().lower().lstrip("@")
            v = v.replace("http://", "").replace("https://", "").rstrip("/")
            v = v.split("/")[0]
            if v.startswith("www."):
                v = v[4:]
            result[n] = v
        else:
            result[n] = None
    return result


def logo_url_for_domain(domain: Optional[str]) -> Optional[str]:
    """Google s2 favicons — no API key, 128px, ~always exists for real domains."""
    if not domain:
        return None
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


# ---------------------------------------------------------------------------
# Upsert / resolve
# ---------------------------------------------------------------------------
def find_by_name(conn, name: str) -> Optional[dict]:
    norm = normalize_name(name)
    if not norm:
        return None
    row = conn.execute(
        "SELECT * FROM companies WHERE name_normalized = ?", (norm,)
    ).fetchone()
    return dict(row) if row else None


def resolve_company(
    conn,
    name: str,
    *,
    source_kind: str = "backfilled",
    enrich: bool = False,
) -> Optional[str]:
    """Upsert company by normalized name. Returns id. None if name is empty.

    `enrich=True` triggers an LLM domain lookup for this single name on insert.
    For bulk backfill, pass `enrich=False` and run `enrich_missing_domains` once.
    """
    if not name:
        return None
    norm = normalize_name(name)
    if not norm:
        return None
    existing = conn.execute(
        "SELECT id, name_variants_json FROM companies WHERE name_normalized = ?",
        (norm,),
    ).fetchone()
    if existing:
        # Add the surface variant if new
        try:
            variants = json.loads(existing["name_variants_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            variants = []
        if name not in variants:
            variants.append(name)
            conn.execute(
                "UPDATE companies SET name_variants_json = ?, updated_at = ? "
                "WHERE id = ?",
                (json.dumps(variants, ensure_ascii=False),
                 db.now_iso(), existing["id"]),
            )
        return existing["id"]

    new_id = "co_" + uuid.uuid4().hex[:14]
    domain = None
    logo = None
    if enrich:
        dmap = lookup_domains_llm([name])
        domain = dmap.get(name)
        logo = logo_url_for_domain(domain)
    conn.execute(
        "INSERT INTO companies "
        "(id, name, name_normalized, domain, logo_url, source_kind, "
        " name_variants_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            new_id, name, norm, domain, logo, source_kind,
            json.dumps([name], ensure_ascii=False),
            db.now_iso(), db.now_iso(),
        ),
    )
    return new_id


# ---------------------------------------------------------------------------
# Backfill from existing people + contacts
# ---------------------------------------------------------------------------
def backfill(*, enrich_domains: bool = True) -> dict:
    """Walk people+contacts, create companies, link company_id FK.

    Returns {created, linked_people, linked_contacts, dedupe_savings,
             domains_enriched}.
    """
    conn = db.get_conn()
    try:
        # 1. Gather unique surface names from people + contacts
        surface_names: set[str] = set()
        for r in conn.execute(
            "SELECT DISTINCT company_name FROM people "
            "WHERE company_name IS NOT NULL AND company_name != ''"
        ):
            surface_names.add(r[0])
        for r in conn.execute(
            "SELECT DISTINCT primary_company FROM contacts "
            "WHERE primary_company IS NOT NULL AND primary_company != ''"
        ):
            surface_names.add(r[0])

        # 2. Upsert each into companies (dedupes by normalize_name)
        name_to_id: dict[str, str] = {}
        created = 0
        for n in surface_names:
            existing = find_by_name(conn, n)
            if existing:
                cid = existing["id"]
                try:
                    variants = json.loads(existing.get("name_variants_json") or "[]")
                except (json.JSONDecodeError, TypeError):
                    variants = []
                if n not in variants:
                    variants.append(n)
                    conn.execute(
                        "UPDATE companies SET name_variants_json = ?, updated_at = ? "
                        "WHERE id = ?",
                        (json.dumps(variants, ensure_ascii=False),
                         db.now_iso(), cid),
                    )
            else:
                cid = resolve_company(conn, n, source_kind="backfilled", enrich=False)
                created += 1
            if cid:
                name_to_id[n] = cid

        # 3. Link people.company_id
        linked_people = 0
        for surface, cid in name_to_id.items():
            cur = conn.execute(
                "UPDATE people SET company_id = ? "
                "WHERE company_name = ? AND (company_id IS NULL OR company_id != ?)",
                (cid, surface, cid),
            )
            linked_people += cur.rowcount

        # 4. Link contacts.company_id
        linked_contacts = 0
        for surface, cid in name_to_id.items():
            cur = conn.execute(
                "UPDATE contacts SET company_id = ? "
                "WHERE primary_company = ? AND (company_id IS NULL OR company_id != ?)",
                (cid, surface, cid),
            )
            linked_contacts += cur.rowcount
    finally:
        conn.close()

    # 5. Enrich domains (LLM) in batches
    enriched = 0
    if enrich_domains:
        enriched = enrich_missing_domains()

    # 6. Score every company
    score_all()

    return {
        "created": created,
        "surface_names_seen": len(surface_names),
        "dedupe_savings": len(surface_names) - created,
        "linked_people": linked_people,
        "linked_contacts": linked_contacts,
        "domains_enriched": enriched,
    }


# ---------------------------------------------------------------------------
# Domain enrichment in batches
# ---------------------------------------------------------------------------
def enrich_missing_domains(batch_size: int = 40) -> int:
    """For every company with NULL domain, run an LLM lookup in batches.

    Returns count of companies that got a domain populated.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name FROM companies WHERE domain IS NULL OR domain = ''"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return 0

    names = [r["name"] for r in rows]
    name_to_id = {r["name"]: r["id"] for r in rows}
    updated = 0
    for i in range(0, len(names), batch_size):
        chunk = names[i : i + batch_size]
        dmap = lookup_domains_llm(chunk)
        conn = db.get_conn()
        try:
            for n, d in dmap.items():
                if not d:
                    continue
                cid = name_to_id.get(n)
                if not cid:
                    continue
                conn.execute(
                    "UPDATE companies SET domain = ?, logo_url = ?, updated_at = ? "
                    "WHERE id = ?",
                    (d, logo_url_for_domain(d), db.now_iso(), cid),
                )
                updated += 1
        finally:
            conn.close()
    return updated


# ---------------------------------------------------------------------------
# Company-level ICP score
# ---------------------------------------------------------------------------
def score_company(conn, company_id: str) -> dict:
    """Compute icp_score for a company.

    Mix of:
      - 50% avg(persona_weight) of attached people (account quality)
      - 20% multi-conference presence (multi-conf > shows commitment)
      - 15% vertical match vs ICP verticals
      - 15% fx_exposure_hint (high/medium/low/unknown)

    Returns breakdown dict and writes icp_score + account_tier to row.
    """
    icp = IcpConfig.default()
    row = conn.execute(
        "SELECT * FROM companies WHERE id = ?", (company_id,)
    ).fetchone()
    if not row:
        return {}
    row = dict(row)

    # Component 1: avg persona weight
    persona_rows = conn.execute(
        "SELECT persona_weight FROM people "
        "WHERE company_id = ? AND persona_weight IS NOT NULL", (company_id,),
    ).fetchall()
    if persona_rows:
        avg_persona = sum(p["persona_weight"] for p in persona_rows) / len(persona_rows)
    else:
        avg_persona = 0.0

    # Component 2: multi-conference presence (0..1)
    confs = conn.execute(
        "SELECT COUNT(DISTINCT conference_id) FROM people WHERE company_id = ?",
        (company_id,),
    ).fetchone()[0]
    multi_conf = min(confs / 3.0, 1.0)  # 3+ confs caps at 1.0

    # Component 3: vertical match
    icp_verticals = {v.lower() for v in icp.company_level.get("verticals", [])}
    vmatch = 1.0 if (row.get("vertical") or "").lower() in icp_verticals else 0.0

    # Component 4: fx exposure
    fx_map = {"high": 1.0, "medium": 0.6, "low": 0.2, "unknown": 0.4, None: 0.4}
    fx = fx_map.get((row.get("fx_exposure_hint") or "unknown").lower(), 0.4)

    score = round(
        0.50 * avg_persona + 0.20 * multi_conf + 0.15 * vmatch + 0.15 * fx, 3
    )
    tier = "A" if score >= 0.65 else ("B" if score >= 0.45 else "C")

    breakdown = {
        "avg_persona_weight": round(avg_persona, 3),
        "people_count": len(persona_rows),
        "conference_count": confs,
        "multi_conf_factor": round(multi_conf, 3),
        "vertical_match": vmatch,
        "fx_exposure_factor": fx,
        "weights": {"persona": 0.50, "multi_conf": 0.20,
                    "vertical": 0.15, "fx": 0.15},
    }

    conn.execute(
        "UPDATE companies SET icp_score = ?, account_tier = ?, "
        "icp_breakdown_json = ?, updated_at = ? WHERE id = ?",
        (score, tier, json.dumps(breakdown, ensure_ascii=False),
         db.now_iso(), company_id),
    )
    return {"score": score, "tier": tier, "breakdown": breakdown}


def score_all() -> int:
    """Recompute icp_score + account_tier for every company. Returns count."""
    conn = db.get_conn()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM companies WHERE approved = 1"
        ).fetchall()]
        for cid in ids:
            score_company(conn, cid)
    finally:
        conn.close()
    return len(ids)


# ---------------------------------------------------------------------------
# Pass 1 — inherit vertical from mode(people.vertical)
# ---------------------------------------------------------------------------
def inherit_vertical_from_people() -> dict:
    """For each company, set vertical to the most common vertical of its people.

    Backfill only wrote name + domain + logo. People rows have vertical
    (from the seed loader / discovery). This walks the join and writes the
    mode back to companies.vertical, then re-scores everything so
    vertical_match (15% of ICP score) finally returns 1.0 for ICP-fit
    accounts instead of 0.

    Returns {inherited, set_to_unknown}.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, "
            "  (SELECT p.vertical FROM people p "
            "   WHERE p.company_id = c.id AND p.vertical IS NOT NULL "
            "   GROUP BY p.vertical ORDER BY COUNT(*) DESC, p.vertical LIMIT 1) "
            "  AS mode_vertical "
            "FROM companies c WHERE c.approved = 1 "
            "  AND (c.vertical IS NULL OR c.vertical = '')"
        ).fetchall()

        inherited = 0
        unknown = 0
        for r in rows:
            v = r["mode_vertical"]
            if v:
                conn.execute(
                    "UPDATE companies SET vertical = ?, updated_at = ? WHERE id = ?",
                    (v, db.now_iso(), r["id"]),
                )
                inherited += 1
            else:
                unknown += 1
    finally:
        conn.close()
    score_all()
    return {"inherited": inherited, "left_unknown": unknown}


# ---------------------------------------------------------------------------
# Pass 2 — batched LLM entity enrichment
# ---------------------------------------------------------------------------
ENRICH_SYSTEM = """You are a B2B data tool. Given a list of company names, return entity information for each. Be conservative — if unsure of a value, return null.

The fx_exposure_hint must reflect whether the company actually handles cross-border / multi-currency flows in its core business:
  high   — primary business is multi-currency (PSPs, FX brokers, international remittance, global marketplaces, multinational treasury)
  medium — significant international revenue but not the core motion
  low    — mostly single-currency / domestic
  unknown — can't tell

The why_grain_fit must be ONE concrete sentence naming the specific FX exposure pattern, not a generic blurb. Examples:
  Wise   — Cross-border consumer + business transfers in 40+ currencies.
  Maersk — Container shipping with payables and receivables in 30+ currencies across global trade lanes.
  IKEA   — Multinational retailer with cross-border supplier payments and multi-currency revenue.

If a company is genuinely NOT a Grain ICP fit (e.g. domestic-only utility, single-currency local retailer), say so honestly in why_grain_fit (e.g. 'Mostly single-currency domestic operations — low FX exposure'). Better an honest null than a fabricated rationale.

Reply with ONLY a JSON object: {"map": {"<name>": {"industry": str|null, "hq_country": str|null, "employee_band": "1-50|51-200|201-1000|1001-5000|5000+" or null, "fx_exposure_hint": "high|medium|low|unknown", "why_grain_fit": str}}}."""


def enrich_entities_llm(batch_size: int = 20) -> dict:
    """For every company missing entity attributes, call Gemini in batches.

    Targets the backfilled skeletons. Skips rows that already have industry
    or fx_exposure_hint set (discovered prospects came in fully enriched).
    Returns {enriched, batches, errors}.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name FROM companies "
            "WHERE approved = 1 "
            "  AND (industry IS NULL OR industry = '') "
            "  AND (fx_exposure_hint IS NULL OR fx_exposure_hint = '')"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"enriched": 0, "batches": 0, "errors": 0}

    name_to_id = {r["name"]: r["id"] for r in rows}
    names = [r["name"] for r in rows]
    enriched = 0
    errors = 0
    batches = 0

    for i in range(0, len(names), batch_size):
        chunk = names[i : i + batch_size]
        batches += 1
        try:
            data = llm.chat_json(
                [
                    {"role": "system", "content": ENRICH_SYSTEM},
                    {"role": "user", "content": json.dumps(
                        {"names": chunk}, ensure_ascii=False)},
                ],
                temperature=0.0,
                max_tokens=3500,
            )
        except llm.LLMError as exc:
            log.warning("entity enrichment batch %d failed: %s", batches, exc)
            errors += 1
            continue
        raw = data.get("map") or {}
        conn = db.get_conn()
        try:
            for n in chunk:
                rec = raw.get(n)
                if not isinstance(rec, dict):
                    continue
                cid = name_to_id.get(n)
                if not cid:
                    continue
                fx = (rec.get("fx_exposure_hint") or "unknown").lower()
                if fx not in {"high", "medium", "low", "unknown"}:
                    fx = "unknown"
                conn.execute(
                    "UPDATE companies SET "
                    "  industry = ?, hq_country = ?, employee_band = ?, "
                    "  fx_exposure_hint = ?, why_grain_fit = ?, updated_at = ? "
                    "WHERE id = ?",
                    (
                        (rec.get("industry") or None),
                        (rec.get("hq_country") or None),
                        (rec.get("employee_band") or None),
                        fx,
                        (rec.get("why_grain_fit") or None),
                        db.now_iso(),
                        cid,
                    ),
                )
                enriched += 1
        finally:
            conn.close()

    # Re-score now that fx_exposure_hint is populated for many rows
    score_all()
    return {"enriched": enriched, "batches": batches, "errors": errors}


# ---------------------------------------------------------------------------
# Pass 3 — Sonar grounding for tier-A why_grain_fit
# ---------------------------------------------------------------------------
SONAR_GROUND_SYSTEM = (
    "You are a B2B sales researcher for Grain Finance. Given a company "
    "name + domain, produce a GROUNDED why_grain_fit: one sentence naming "
    "the specific FX exposure pattern, citing a real source URL. Reply "
    "with ONLY: "
    '{"why_grain_fit": str, "source_url": str}. '
    "Do not fabricate — if you can't find a citation, return null in both."
)


def ground_tier_a_with_sonar(limit: int = 30, *, only_missing: bool = False) -> dict:
    """For each tier-A company, ask Sonar for a grounded why_grain_fit
    with a real source URL. One Sonar call per company (~3s each).

    `only_missing=True` skips tier-A rows that already have source_url —
    used to retry the ones that failed on the first pass without burning
    tokens on the ones already done.

    Returns {grounded, attempted, errors}.
    """
    conn = db.get_conn()
    try:
        sql = (
            "SELECT id, name, domain FROM companies "
            "WHERE approved = 1 AND account_tier = 'A' "
        )
        if only_missing:
            sql += "AND (source_url IS NULL OR source_url = '') "
        sql += "ORDER BY icp_score DESC LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    finally:
        conn.close()

    grounded = 0
    errors = 0
    for r in rows:
        query = (
            f"Company: {r['name']} ({r['domain'] or 'domain unknown'}). "
            f"Find ONE recent (last 24 months) public source — news article, "
            f"corporate blog post, investor report, SEC filing — that "
            f"evidences this company's cross-border or multi-currency "
            f"transaction flows, treasury operations, or FX exposure. "
            f"Then write a single sentence why_grain_fit that names the "
            f"specific FX exposure pattern, with the source_url. JSON only."
        )
        try:
            text, _cit = llm.search_grounded(query, system=SONAR_GROUND_SYSTEM)
        except llm.LLMError as exc:
            log.warning("sonar ground failed for %s: %s", r["name"], exc)
            errors += 1
            continue

        # Parse JSON (Sonar sometimes wraps in prose)
        import re as _re
        parsed = None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            m = _re.search(r"\{[\s\S]*?\}", text)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    parsed = None
        if not isinstance(parsed, dict):
            errors += 1
            continue
        why = (parsed.get("why_grain_fit") or "").strip()
        src = (parsed.get("source_url") or "").strip() or None
        if not why:
            errors += 1
            continue

        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE companies SET why_grain_fit = ?, source_url = ?, "
                "updated_at = ? WHERE id = ?",
                (why, src, db.now_iso(), r["id"]),
            )
        finally:
            conn.close()
        grounded += 1

    return {"grounded": grounded, "attempted": len(rows), "errors": errors}


# ---------------------------------------------------------------------------
# Per-company rollup for the CompanyDetail page
# ---------------------------------------------------------------------------
def get_company_with_rollup(company_id: str) -> Optional[dict]:
    """Company row + people + encounters + arc aggregate + conferences."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        if not row:
            return None
        comp = dict(row)
        try:
            comp["name_variants"] = json.loads(comp.get("name_variants_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            comp["name_variants"] = []
        try:
            comp["icp_breakdown"] = json.loads(comp.get("icp_breakdown_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            comp["icp_breakdown"] = {}

        # People at this company across all conferences
        people = [
            dict(r) for r in conn.execute(
                "SELECT p.id, p.full_name, p.title, p.persona, p.persona_weight, "
                "       p.icp_score, p.conference_id, c.name as conference_name "
                "FROM people p "
                "LEFT JOIN conferences c ON c.id = p.conference_id "
                "WHERE p.company_id = ? "
                "ORDER BY p.persona_weight DESC NULLS LAST, p.full_name",
                (company_id,),
            ).fetchall()
        ]

        # Contacts linked to this company (post-resolution)
        contacts = [
            dict(r) for r in conn.execute(
                "SELECT id, primary_name, primary_title, arc_verdict, "
                "       arc_confidence, nudge_active "
                "FROM contacts WHERE company_id = ?", (company_id,),
            ).fetchall()
        ]

        # Encounters via those contacts
        if contacts:
            qmarks = ",".join("?" * len(contacts))
            encs = [
                dict(r) for r in conn.execute(
                    f"SELECT e.id, e.contact_id, e.captured_at, e.capture_mode, "
                    f"       e.sentiment, e.meeting_requested, "
                    f"       e.conference_id, c.name as conference_name "
                    f"FROM encounters e "
                    f"LEFT JOIN conferences c ON c.id = e.conference_id "
                    f"WHERE e.contact_id IN ({qmarks}) "
                    f"ORDER BY e.captured_at DESC",
                    tuple(c["id"] for c in contacts),
                ).fetchall()
            ]
        else:
            encs = []

        # Arc aggregate
        arc_counts: dict[str, int] = {}
        for c in contacts:
            v = c.get("arc_verdict") or "unknown"
            arc_counts[v] = arc_counts.get(v, 0) + 1

        # Conferences this company appears at
        confs = [
            dict(r) for r in conn.execute(
                "SELECT DISTINCT c.id, c.name, c.start_date, c.tier "
                "FROM conferences c "
                "JOIN people p ON p.conference_id = c.id "
                "WHERE p.company_id = ? "
                "ORDER BY c.start_date DESC", (company_id,),
            ).fetchall()
        ]

        comp.update({
            "people": people,
            "contacts": contacts,
            "encounters": encs,
            "arc_counts": arc_counts,
            "conferences": confs,
            "encounter_count": len(encs),
            "meeting_count": sum(1 for e in encs if e.get("meeting_requested")),
        })
        return comp
    finally:
        conn.close()


_SORT_COLUMNS = {
    # api sort key -> ORDER BY clause (people_count/conference_count are
    # computed aliases from the LEFT JOIN below)
    "score": "c.icp_score DESC NULLS LAST, c.name ASC",
    "icp_score": "c.icp_score DESC NULLS LAST, c.name ASC",
    "name": "c.name ASC",
    "people": "people_count DESC, c.name ASC",
    "people_count": "people_count DESC, c.name ASC",
    "conferences": "conference_count DESC, c.name ASC",
}


def list_companies(
    *,
    tier: Optional[str] = None,
    is_prospect: Optional[bool] = None,
    approved: Optional[bool] = True,
    vertical: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "score",
    limit: int = 200,
) -> list[dict]:
    """List companies for the index page.

    Filters (all optional, all cheap):
      tier        — exact account_tier match (A / B / C)
      is_prospect — 1/0 on is_prospect flag
      approved    — 1/0 on approved flag (defaults to approved-only)
      vertical    — exact, case-insensitive vertical match
      search      — case-insensitive substring on name / name_variants_json

    Sort: one of score (default), name, people, conferences.

    people_count and conference_count are computed in a single LEFT JOIN
    against people(company_id) so this stays O(1) queries regardless of
    how many companies match. Returns [] on an empty table.
    """
    where = []
    params: list = []
    if tier:
        where.append("c.account_tier = ?")
        params.append(tier)
    if is_prospect is not None:
        where.append("c.is_prospect = ?")
        params.append(1 if is_prospect else 0)
    if approved is not None:
        where.append("c.approved = ?")
        params.append(1 if approved else 0)
    if vertical:
        where.append("LOWER(c.vertical) = ?")
        params.append(vertical.strip().lower())
    if search:
        where.append("(LOWER(c.name) LIKE ? OR LOWER(c.name_variants_json) LIKE ?)")
        like = f"%{search.strip().lower()}%"
        params.extend([like, like])

    order_by = _SORT_COLUMNS.get((sort or "score").lower(), _SORT_COLUMNS["score"])

    sql = (
        "SELECT c.*, "
        "  COUNT(DISTINCT p.id) AS people_count, "
        "  COUNT(DISTINCT p.conference_id) AS conference_count "
        "FROM companies c "
        "LEFT JOIN people p ON p.company_id = c.id "
    )
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "GROUP BY c.id "
    sql += f"ORDER BY {order_by} "
    sql += "LIMIT ?"
    params.append(limit)

    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    # Parse JSON blobs to friendly shapes for the index cards.
    for r in rows:
        try:
            r["name_variants"] = json.loads(r.get("name_variants_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            r["name_variants"] = []
    return rows
