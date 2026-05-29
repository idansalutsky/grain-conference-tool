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
# Curated, offline enrichment for recognisable ICP companies
# ---------------------------------------------------------------------------
# Grounded (not LLM-fabricated) attributes for the well-known accounts that
# actually appear in our seed data. Keyed by normalize_name(). Each tuple is:
#   (domain, industry, fx_exposure_hint, why_grain_fit)
# Domains are the real primary corporate domains (so the favicon/logo works);
# why_grain_fit names the specific cross-border / multi-currency FX pattern
# tied to Grain's ICP. Long-tail / genuinely-unknown names are intentionally
# left out (domain stays NULL) and get an honest generic fit line at runtime.
_HI = "high"
_MED = "medium"
_LOW = "low"
CURATED_ENRICHMENT: dict[str, tuple] = {
    # --- Payments / PSP / acquiring ----------------------------------------
    "stripe": ("stripe.com", "Payments / PSP", _HI,
               "Global payments platform processing in 135+ currencies — settlement and payout FX is core."),
    "adyen": ("adyen.com", "Payments / PSP", _HI,
              "Single-platform acquirer settling merchant funds across dozens of currencies and markets."),
    "worldpay": ("worldpay.com", "Payments / PSP", _HI,
                 "Global acquirer moving cross-border card volume — multi-currency settlement at scale."),
    "fis": ("fisglobal.com", "Financial technology", _MED,
            "Banking and payments tech serving institutions worldwide with cross-border transaction flows."),
    "paypal": ("paypal.com", "Payments / wallet", _HI,
               "Cross-border consumer and merchant payments with built-in currency conversion."),
    "mollie": ("mollie.com", "Payments / PSP", _HI,
               "European PSP settling merchant payments across multiple currencies and markets."),
    "mastercard": ("mastercard.com", "Card network", _HI,
                   "Global card network clearing cross-border transactions in every major currency."),
    "american express": ("americanexpress.com", "Card network / payments", _HI,
                         "Global card issuer and network with heavy cross-border travel and FX volume."),
    "marqeta": ("marqeta.com", "Card issuing platform", _MED,
                "Modern card-issuing platform expanding internationally with multi-currency programs."),
    "network international": ("network.global", "Payments / PSP", _HI,
                             "Middle East / Africa acquirer processing multi-currency merchant payments."),
    "paytabs": ("paytabs.com", "Payments / PSP", _HI,
                "MENA payment gateway handling cross-border and multi-currency merchant settlement."),
    "tap payments": ("tap.company", "Payments / PSP", _HI,
                     "Gulf-region PSP processing multi-currency online payments across MENA markets."),
    "tabby": ("tabby.ai", "BNPL / fintech", _MED,
              "MENA buy-now-pay-later operating across multiple currencies and regulatory regimes."),
    "tamara": ("tamara.co", "BNPL / fintech", _MED,
               "Gulf BNPL provider with cross-border merchant and consumer flows."),
    "payabl": ("payabl.com", "Payments / PSP", _HI,
               "European PSP and acquirer settling merchant funds in multiple currencies."),
    "silverflow": ("silverflow.com", "Payments infrastructure", _HI,
                   "Cloud card-processing platform connecting acquirers to networks across currencies."),
    "optty": ("optty.com", "Payments orchestration", _MED,
              "Payment-method orchestration spanning many markets and currencies."),
    "tabapay": ("tabapay.com", "Payments / money movement", _MED,
                "Instant money-movement rails expanding cross-border."),
    "tassat": ("tassat.com", "B2B payments / blockchain", _MED,
               "B2B real-time payments network with digital-currency settlement."),
    # --- Fintech / banking infra -------------------------------------------
    "klarna": ("klarna.com", "BNPL / fintech", _HI,
               "Pan-European BNPL and bank settling consumer and merchant flows in many currencies."),
    "trade republic": ("traderepublic.com", "Neobroker / fintech", _MED,
                       "European broker-bank with multi-currency securities and cash holdings."),
    "tyme": ("tymebank.co.za", "Digital banking", _MED,
             "Emerging-market digital bank operating across South Africa and Southeast Asia."),
    "thought machine": ("thoughtmachine.net", "Core banking platform", _MED,
                        "Cloud core-banking vendor powering multi-currency ledgers for global banks."),
    "ncino": ("ncino.com", "Banking software / SaaS", _LOW,
              "Cloud banking software; modest direct FX, mostly USD SaaS revenue."),
    "q2": ("q2.com", "Banking software / SaaS", _LOW,
           "Digital banking platform; predominantly domestic US revenue."),
    "jack henry": ("jackhenry.com", "Banking software", _LOW,
                   "Core banking and payments for US community banks — mostly single-currency."),
    "codat": ("codat.io", "Fintech API / data", _LOW,
              "SMB financial-data API; limited direct FX exposure."),
    "finmid": ("finmid.com", "Embedded finance", _MED,
               "Embedded B2B financing across European markets and currencies."),
    "yodlee": ("yodlee.com", "Financial data aggregation", _LOW,
               "Financial-data aggregation; limited direct currency exposure."),
    "amount": ("amount.com", "Lending software / SaaS", _LOW,
               "Digital lending platform; predominantly US-domestic."),
    "extend": ("paywithextend.com", "Virtual cards / fintech", _MED,
               "Virtual-card platform with cross-border spend use cases."),
    "alpaca": ("alpaca.markets", "Brokerage API / fintech", _MED,
               "Global brokerage API with multi-currency investing flows."),
    "astra": ("astra.finance", "Payments automation", _MED,
              "Automated money-movement infrastructure expanding across rails."),
    "paytm": ("paytm.com", "Payments / fintech", _MED,
              "Indian super-app; large domestic volume with growing cross-border remittance."),
    "infosys": ("infosys.com", "IT services", _MED,
                "Global IT services firm billing clients across many currencies."),
    "ibm": ("ibm.com", "Technology / services", _MED,
            "Multinational tech vendor with revenue and costs across dozens of currencies."),
    # --- Remittance / cross-border / super-apps ----------------------------
    "ant": ("antgroup.com", "Fintech / payments", _HI,
            "Global fintech (Alipay) moving cross-border consumer and merchant payments."),
    "ant international": ("antgroup.com", "Cross-border payments", _HI,
                          "Ant's international arm built around cross-border, multi-currency settlement."),
    "flutterwave": ("flutterwave.com", "Payments / PSP", _HI,
                    "African payments infrastructure built for cross-border, multi-currency flows."),
    "rappi": ("rappi.com", "Delivery super-app", _MED,
              "LatAm super-app operating across multiple countries and currencies."),
    "mercado libre": ("mercadolibre.com", "Marketplace / fintech", _HI,
                      "LatAm marketplace + Mercado Pago settling across many currencies and borders."),
    "neon pagamentos": ("neon.com.br", "Digital banking", _LOW,
                        "Brazilian digital bank — primarily BRL-domestic."),
    # --- Crypto / digital assets -------------------------------------------
    "binance": ("binance.com", "Crypto exchange", _HI,
                "Global crypto exchange with fiat on/off-ramps in dozens of currencies."),
    "coinbase": ("coinbase.com", "Crypto exchange", _HI,
                 "Crypto exchange operating fiat rails across multiple currencies and regions."),
    "kraken": ("kraken.com", "Crypto exchange", _HI,
               "Crypto exchange with multi-currency fiat funding across global markets."),
    "fireblocks": ("fireblocks.com", "Digital-asset infrastructure", _HI,
                   "Digital-asset transfer network settling value across currencies and chains."),
    "galaxy digital": ("galaxy.com", "Digital-asset financial services", _HI,
                       "Crypto financial-services firm with cross-border, multi-asset treasury flows."),
    # --- Travel: OTAs / booking --------------------------------------------
    "booking holdings": ("booking.com", "Online travel / OTA", _HI,
                         "Global OTA settling hotel and travel inventory in many currencies — heavy multi-currency FX exposure."),
    "expedia": ("expedia.com", "Online travel / OTA", _HI,
                "Global OTA collecting and paying out travel bookings across dozens of currencies."),
    "trip.com": ("trip.com", "Online travel / OTA", _HI,
                 "International OTA settling cross-border travel bookings in many currencies."),
    "ctrip": ("trip.com", "Online travel / OTA", _HI,
              "Trip.com Group's China travel platform with cross-border, multi-currency bookings."),
    "elong": ("ly.com", "Online travel / OTA", _MED,
              "Chinese OTA with cross-border travel inventory and currency exposure."),
    "airbnb": ("airbnb.com", "Travel marketplace", _HI,
               "Global stays marketplace collecting from guests and paying hosts across currencies."),
    "agoda": ("agoda.com", "Online travel / OTA", _HI,
              "Asia-focused OTA settling accommodation bookings in many currencies."),
    "hopper": ("hopper.com", "Travel app / fintech", _HI,
               "Travel-booking app with FX-sensitive fare and fintech products across markets."),
    "kayak": ("kayak.com", "Travel metasearch", _MED,
              "Travel metasearch routing bookings across global suppliers and currencies."),
    "skyscanner": ("skyscanner.net", "Travel metasearch", _HI,
                   "Global flight metasearch directing cross-border, multi-currency bookings."),
    "wego": ("wego.com", "Travel metasearch", _HI,
             "MENA / APAC travel metasearch with multi-currency cross-border bookings."),
    "makemytrip": ("makemytrip.com", "Online travel / OTA", _HI,
                   "Indian OTA with significant outbound, cross-border travel and FX flows."),
    "despegar": ("despegar.com", "Online travel / OTA", _HI,
                 "Leading LatAm OTA settling travel across many countries and currencies."),
    "despegar.com": ("despegar.com", "Online travel / OTA", _HI,
                     "Leading LatAm OTA settling travel across many countries and currencies."),
    "edreams odigeo": ("edreamsodigeo.com", "Online travel / OTA", _HI,
                       "Pan-European OTA processing flight bookings across many currencies."),
    "on the beach": ("onthebeach.co.uk", "Online travel / OTA", _MED,
                     "UK beach-holiday OTA with EUR-denominated supplier payments."),
    "almosafer": ("almosafer.com", "Online travel / OTA", _HI,
                  "Saudi travel agency with cross-border supplier settlement in multiple currencies."),
    "klook": ("klook.com", "Travel experiences platform", _HI,
              "APAC experiences platform collecting and paying across many currencies and markets."),
    "travelperk": ("travelperk.com", "Business travel / SaaS", _HI,
                   "Business-travel platform settling bookings for clients across currencies."),
    "intrepid travel": ("intrepidtravel.com", "Tour operator", _HI,
                        "Global adventure-tour operator paying suppliers in many local currencies."),
    "easyjet holidays": ("easyjet.com", "Travel / tour operator", _MED,
                         "European package-holiday arm with EUR/GBP supplier exposure."),
    # --- Travel: tech / GDS / hospitality ----------------------------------
    "amadeus": ("amadeus.com", "Travel technology / GDS", _HI,
                "Global distribution system processing travel transactions in every major currency."),
    "sabre": ("sabre.com", "Travel technology / GDS", _HI,
              "Global GDS clearing airline and hotel transactions across currencies."),
    "travelport": ("travelport.com", "Travel technology / GDS", _HI,
                   "Global travel-commerce platform settling cross-border bookings."),
    "hotelbeds": ("hotelbeds.com", "Travel B2B / bedbank", _HI,
                  "B2B bedbank buying and reselling hotel inventory across dozens of currencies."),
    "mews": ("mews.com", "Hospitality software / SaaS", _HI,
             "Cloud hotel-management platform processing guest payments in many currencies."),
    "marriott": ("marriott.com", "Hospitality", _HI,
                 "Global hotel group with revenue and franchise fees across many currencies."),
    "hilton": ("hilton.com", "Hospitality", _HI,
               "Global hotel group collecting room revenue in dozens of currencies."),
    "hyatt": ("hyatt.com", "Hospitality", _HI,
              "Global hotel group with multi-currency revenue and cross-border operations."),
    "ihg": ("ihg.com", "Hospitality", _HI,
            "Global hotel group (IHG) with franchise and room revenue across currencies."),
    "accor": ("accor.com", "Hospitality", _HI,
              "European-headquartered global hotel group with broad multi-currency revenue."),
    "shangrila": ("shangri-la.com", "Hospitality", _HI,
                  "Asian luxury hotel group with multi-currency revenue across markets."),
    "banyan tree": ("banyantree.com", "Hospitality", _HI,
                    "Resort group operating across Asia-Pacific with multi-currency revenue."),
    # --- Airlines ----------------------------------------------------------
    "lufthansa": ("lufthansagroup.com", "Airline group", _HI,
                  "Global airline group with ticket revenue and fuel costs across many currencies."),
    "singapore airlines": ("singaporeair.com", "Airline", _HI,
                           "International airline selling tickets and buying fuel across currencies."),
    "united airlines": ("united.com", "Airline", _HI,
                        "Global airline with cross-border ticket revenue and FX-exposed costs."),
    "cathay pacific": ("cathaypacific.com", "Airline", _HI,
                       "Hong Kong flag carrier with multi-currency ticket and cargo revenue."),
    "tui": ("tui.com", "Travel / tour operator", _HI,
            "Integrated tourism group settling across source and destination currencies."),
    # --- Retail / marketplace / commerce -----------------------------------
    "shopify": ("shopify.com", "E-commerce platform", _HI,
                "Commerce platform processing merchant sales and payouts across currencies."),
    "bigcommerce": ("bigcommerce.com", "E-commerce platform", _MED,
                    "SaaS commerce platform serving cross-border merchants."),
    "spryker": ("spryker.com", "E-commerce platform", _MED,
                "Enterprise commerce platform used by international, multi-currency merchants."),
    "noon": ("noon.com", "E-commerce marketplace", _HI,
             "MENA marketplace with cross-border sourcing and multi-currency settlement."),
    "6thstreet.com": ("6thstreet.com", "E-commerce / fashion retail", _MED,
                      "GCC online fashion retailer with cross-border sourcing."),
    "styli": ("styli.com", "E-commerce / fashion retail", _MED,
              "GCC fast-fashion e-tailer with cross-border supply chain."),
    "desertcart": ("desertcart.com", "Cross-border e-commerce", _HI,
                   "Cross-border online retailer shipping to 160+ countries in many currencies."),
    "kibsons international": ("kibsons.com", "Online grocery", _LOW,
                             "UAE online grocer — largely AED-domestic with some imports."),
    "landmark": ("landmarkgroup.com", "Retail conglomerate", _HI,
                 "MENA retail conglomerate sourcing globally with multi-currency payables."),
    "al-futtaim": ("alfuttaim.com", "Retail / conglomerate", _HI,
                   "Diversified MENA conglomerate with global suppliers and multi-currency trade."),
    "alfuttaim": ("alfuttaim.com", "Retail / conglomerate", _HI,
                  "Diversified MENA conglomerate with global suppliers and multi-currency trade."),
    "maf": ("majidalfuttaim.com", "Retail / real estate", _HI,
            "Majid Al Futtaim — MENA retail and malls group with cross-border sourcing."),
    # --- Telecom -----------------------------------------------------------
    "etisalat": ("eand.com", "Telecom", _MED,
                 "UAE-based telecom group (e&) with operations across multiple currencies."),
    "du": ("du.ae", "Telecom", _LOW,
           "UAE telecom operator — predominantly AED-domestic."),
    # --- Other tech --------------------------------------------------------
    "tencent": ("tencent.com", "Technology / fintech", _HI,
                "Global tech and payments (WeChat Pay) with cross-border transaction flows."),
}
del _HI, _MED, _LOW

# Honest, non-fabricated fallback for accounts we don't have a curated row for.
_GENERIC_FIT = (
    "Operates across multiple markets — likely multi-currency exposure; "
    "review for cross-border FX flows."
)


def seed_enrich_curated() -> dict:
    """Populate domain / logo_url / industry / fx_exposure_hint / why_grain_fit
    from the grounded CURATED_ENRICHMENT map (offline, deterministic).

    For every approved company:
      - if its normalized name is in the curated map → write the real domain,
        logo, industry, fx hint and grounded why_grain_fit.
      - otherwise → leave domain NULL (we don't fabricate domains) and write an
        honest generic why_grain_fit so the rationale field is never empty.

    Only fills NULL/empty fields, so it never clobbers richer data written by a
    later LLM/Sonar pass. Re-scores afterwards so the now-populated
    fx_exposure_hint feeds the ICP score. Idempotent.

    Returns {curated, generic, domains_set}.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, name_normalized, domain, industry, "
            "       fx_exposure_hint, why_grain_fit "
            "FROM companies WHERE approved = 1"
        ).fetchall()

        curated = 0
        generic = 0
        domains_set = 0
        for r in rows:
            norm = r["name_normalized"]
            entry = CURATED_ENRICHMENT.get(norm)
            if entry:
                domain, industry, fx, why = entry
                new_domain = r["domain"] or domain
                new_logo = logo_url_for_domain(new_domain)
                new_industry = r["industry"] or industry
                new_fx = r["fx_exposure_hint"] or fx
                new_why = r["why_grain_fit"] or why
                conn.execute(
                    "UPDATE companies SET domain = ?, logo_url = ?, industry = ?, "
                    "  fx_exposure_hint = ?, why_grain_fit = ?, updated_at = ? "
                    "WHERE id = ?",
                    (new_domain, new_logo, new_industry, new_fx, new_why,
                     db.now_iso(), r["id"]),
                )
                curated += 1
                if not r["domain"] and domain:
                    domains_set += 1
            else:
                # No curated row — fill only the rationale + an unknown fx hint,
                # never a fabricated domain.
                new_fx = r["fx_exposure_hint"] or "unknown"
                new_why = r["why_grain_fit"] or _GENERIC_FIT
                conn.execute(
                    "UPDATE companies SET fx_exposure_hint = ?, why_grain_fit = ?, "
                    "  updated_at = ? WHERE id = ?",
                    (new_fx, new_why, db.now_iso(), r["id"]),
                )
                generic += 1
    finally:
        conn.close()

    # fx_exposure_hint feeds 15% of the ICP score — recompute now.
    score_all()
    return {"curated": curated, "generic": generic, "domains_set": domains_set}


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
