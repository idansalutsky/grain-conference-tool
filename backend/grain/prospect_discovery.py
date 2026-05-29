"""Prospect discovery — find companies that fit Grain's ICP.

This is the COMPANY analog of `discovery.py` (which finds conferences).
Uses Perplexity Sonar to surface real, currently-relevant prospects that
match Grain's ICP — companies with cross-border B2B payment flows, FX
exposure, or treasury workloads that Grain Finance can hedge.

Output is a list of `is_prospect=1, approved=0` rows in the companies
table. A rep approves each one before it becomes an active prospect.

Cost: ~$0.005 per call. Designed for on-demand button.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import companies, db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.prospect_discovery")


PROSPECT_SYSTEM = (
    "You are a B2B sales researcher for Grain Finance, a fintech selling "
    "embedded cross-currency FX hedging to companies with international "
    "transaction volume. You will surface real, currently-active companies "
    "that fit Grain's ICP. ICP examples: cross-border payment platforms "
    "(PSPs), travel marketplaces (OTAs, hotel chains, airlines), SaaS "
    "platforms with global checkout, marketplaces with payouts in multiple "
    "currencies, treasury operations at multinational corporates, "
    "international subscription companies, supply-chain companies with "
    "cross-currency invoicing.\n\n"
    "Reply with ONLY a JSON object:\n"
    '{"prospects": [{"name": str, "domain": str, "hq_country": str, '
    '"industry": str, "vertical": "payments|travel|fintech_other|saas|treasury|crypto|marketplace|supply_chain", '
    '"employee_band": "1-50|51-200|201-1000|1001-5000|5000+", '
    '"fx_exposure_hint": "high|medium|low", '
    '"why_grain_fit": str, "source_url": str}, ...]}\n'
    "Only include real, verifiable companies you can cite. If unsure, leave it out."
)


RECENT_DISCOVERY_COOLDOWN_DAYS = 60


def discover_prospects(
    *,
    vertical_hint: Optional[str] = None,
    region_hint: Optional[str] = None,
    max_results: int = 8,
    exclude_known: bool = True,
) -> dict:
    """Sonar call for fresh Grain-fit prospects.

    Smarter than a generic vertical prompt: we seed Sonar with concrete
    examples of OUR validated ICP companies in the target vertical, then
    ask "find more like these". Higher hit rate.

    Also applies a `RECENT_DISCOVERY_COOLDOWN_DAYS` filter so re-runs
    don't surface the same name again right away.

    Each result is inserted as `is_prospect=1, approved=0` so the UI can
    show a pending-approval list. Returns {prospects, citations}.
    """
    icp = IcpConfig.default()
    verticals = ", ".join(icp.company_level["verticals"])

    # Build the "exclude" + "examples like these" lists from real data
    known: list[str] = []
    seed_examples: list[dict] = []
    if exclude_known:
        conn = db.get_conn()
        try:
            # All approved company names (capped for prompt size)
            rows = conn.execute(
                "SELECT name FROM companies WHERE approved = 1"
            ).fetchall()
            known = [r["name"] for r in rows][:60]

            # Add recently-discovered-but-rejected names to the exclude list,
            # plus anything we discovered within the cooldown window
            since = (datetime.now(timezone.utc)
                     - timedelta(days=RECENT_DISCOVERY_COOLDOWN_DAYS)).isoformat()
            recent_rows = conn.execute(
                "SELECT name FROM companies "
                "WHERE source_kind = 'discovered' AND created_at >= ?",
                (since,),
            ).fetchall()
            for r in recent_rows:
                if r["name"] not in known:
                    known.append(r["name"])

            # Seed examples — concrete Tier-A ICP companies in the vertical the
            # user asked for, so Sonar gets "find more like these" not "find
            # generic stuff in this vertical". This is the single biggest
            # signal-quality lever we have.
            if vertical_hint:
                exrows = conn.execute(
                    "SELECT name, why_grain_fit FROM companies "
                    "WHERE approved = 1 AND vertical = ? "
                    "  AND why_grain_fit IS NOT NULL "
                    "ORDER BY icp_score DESC LIMIT 5",
                    (vertical_hint,),
                ).fetchall()
                seed_examples = [
                    {"name": r["name"], "why_grain_fit": r["why_grain_fit"]}
                    for r in exrows
                ]
        finally:
            conn.close()

    vclause = f"in the {vertical_hint} vertical" if vertical_hint else "across verticals"
    rclause = f" headquartered in or with major operations in {region_hint}" if region_hint else ""
    excl = (
        f"\nEXCLUDE these companies we already track or recently surfaced: "
        f"{', '.join(known[:40])}."
        if known else ""
    )

    examples_clause = ""
    if seed_examples:
        ex_lines = "\n".join(
            f"  - {e['name']}: {e['why_grain_fit']}" for e in seed_examples
        )
        examples_clause = (
            f"\n\nHere are FIVE companies we already serve {vclause} that "
            f"exemplify the FX exposure pattern we want. FIND MORE COMPANIES "
            f"LIKE THESE (same scale, same FX pattern, same vertical):\n"
            f"{ex_lines}\n\n"
            f"Use these as the template — do not return them again, but "
            f"return companies whose FX exposure pattern matches them."
        )

    query = (
        f"Surface {max_results} real, currently-active companies {vclause}{rclause} "
        f"that would be high-fit prospects for Grain Finance — a fintech selling "
        f"embedded cross-currency FX hedging. Target ICP verticals: {verticals}. "
        f"For each: corporate name, primary domain, HQ country, industry, "
        f"vertical (must be one of the allowed values), employee band, "
        f"fx_exposure_hint (high/medium/low based on whether they handle "
        f"cross-border flows), a one-sentence why_grain_fit explaining the "
        f"specific FX exposure pattern (e.g. 'pays affiliates in 12 "
        f"currencies', 'PSP serving LATAM merchants', 'global payroll "
        f"platform'), and a source_url for verification.{excl}{examples_clause}\n"
        f"Output only JSON."
    )

    try:
        text, citations = llm.search_grounded(query, system=PROSPECT_SYSTEM)
    except llm.LLMError as exc:
        log.warning("prospect discovery failed: %s", exc)
        return {"prospects": [], "citations": [], "error": str(exc)}

    prospects: list[dict] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed.get("prospects"), list):
            prospects = parsed["prospects"]
    except (json.JSONDecodeError, AttributeError):
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed.get("prospects"), list):
                    prospects = parsed["prospects"]
            except json.JSONDecodeError:
                pass

    saved: list[dict] = []
    conn = db.get_conn()
    try:
        for p in prospects[:max_results]:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            norm = companies.normalize_name(p["name"])
            if not norm:
                continue
            existing = conn.execute(
                "SELECT id FROM companies WHERE name_normalized = ?", (norm,),
            ).fetchone()
            if existing:
                continue  # we already know this one

            new_id = "co_" + uuid.uuid4().hex[:14]
            domain = (p.get("domain") or "").strip().lower().lstrip("@") or None
            if domain:
                domain = domain.replace("http://", "").replace("https://", "")
                domain = domain.rstrip("/").split("/")[0]
                if domain.startswith("www."):
                    domain = domain[4:]
            logo = companies.logo_url_for_domain(domain)
            conn.execute(
                "INSERT INTO companies "
                "(id, name, name_normalized, domain, logo_url, hq_country, "
                " industry, vertical, employee_band, fx_exposure_hint, "
                " why_grain_fit, source_kind, source_url, is_prospect, approved, "
                " name_variants_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id, p["name"], norm, domain, logo,
                    p.get("hq_country"),
                    p.get("industry"),
                    p.get("vertical"),
                    p.get("employee_band"),
                    (p.get("fx_exposure_hint") or "unknown").lower(),
                    p.get("why_grain_fit"),
                    "discovered",
                    p.get("source_url"),
                    1, 0,
                    json.dumps([p["name"]], ensure_ascii=False),
                    db.now_iso(), db.now_iso(),
                ),
            )
            saved.append({"company_id": new_id, **p})
            db.log_feedback(
                decision_kind="prospect_discovery_proposal",
                target_kind="company", target_id=new_id,
                after={**p, "citations": citations},
                reason=p.get("why_grain_fit"),
                decided_by="prospect_discovery_agent",
            )
    finally:
        conn.close()

    return {
        "prospects": saved,
        "citations": citations,
        "raw_text_preview": text[:500],
    }


def list_pending_prospects(limit: int = 50) -> list[dict]:
    """Companies surfaced by discovery but not yet approved."""
    return companies.list_companies(is_prospect=True, approved=False, limit=limit)


def approve_prospect(company_id: str, *, decided_by: str = "ui") -> dict:
    """Promote a prospect to an approved company. Score it. Log feedback."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        if not row:
            raise ValueError("prospect not found")
        before = dict(row)
        conn.execute(
            "UPDATE companies SET approved = 1, updated_at = ? WHERE id = ?",
            (db.now_iso(), company_id),
        )
        # Score it now that it's approved
        companies.score_company(conn, company_id)
        after = dict(conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone())
    finally:
        conn.close()
    db.log_feedback(
        decision_kind="prospect_discovery_approved",
        target_kind="company", target_id=company_id,
        before={"approved": 0, "icp_score": before.get("icp_score")},
        after={"approved": 1, "icp_score": after.get("icp_score"),
               "account_tier": after.get("account_tier")},
        decided_by=decided_by,
    )
    return after


def reject_prospect(company_id: str, *, reason: str = "",
                    decided_by: str = "ui") -> None:
    """Mark a prospect rejected — keep the row for audit but flip approved=-1."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, approved FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        if not row:
            raise ValueError("prospect not found")
        # Use a sentinel value to mark rejection
        conn.execute(
            "UPDATE companies SET approved = 0, is_prospect = 0, updated_at = ? "
            "WHERE id = ?", (db.now_iso(), company_id),
        )
    finally:
        conn.close()
    db.log_feedback(
        decision_kind="prospect_discovery_rejected",
        target_kind="company", target_id=company_id,
        reason=reason, decided_by=decided_by,
    )
