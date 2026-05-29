"""'From the brain' insights — periodic LLM synthesis over recent activity.

The pattern: the rest of the system is reactive (rep asks → tool answers).
The insights layer is **proactive**: every so often the brain looks at what
just happened, identifies leverage points, and surfaces them with a
suggested action.

  Inputs (deterministic gather):
    - Recent encounters (last 30d)
    - Recent contacts + arc verdicts + nudge state
    - Per-conference encounter counts + meeting-request counts
    - Per-company persona coverage (which BUYER / CHAMPION / ENTRY_POINT
      have we seen, what's missing)
    - Pending discovery proposals + review-queue size
    - Competitor mentions in attended events

  Synthesis (one LLM call):
    - Gemini 2.5 Flash with structured-output prompt
    - Returns array of insights with kind / severity / title / body /
      suggested_action / evidence

  Persistence:
    - One row per insight in `brain_insights`
    - Status starts 'fresh', transitions to dismissed / acknowledged /
      actioned via HIL endpoints
    - 30-day TTL

Cost per synthesis: ~$0.005 (one Gemini Flash call with ~3k token context).
Designed to be called on demand AND on a daily cron.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.brain_insights")


# ---------------------------------------------------------------------------
# Insight schema
# ---------------------------------------------------------------------------
VALID_KINDS = {
    "follow_up_gap", "persona_gap", "arc_regression",
    "yield_retrospective", "missed_opportunity",
    "pattern_detection", "tire_kicker_review",
    "competitor_proximity", "account_pattern",
}
VALID_SEVERITIES = {"high", "medium", "low"}
INSIGHT_TTL_DAYS = 30


# ---------------------------------------------------------------------------
# Evidence gathering — all deterministic SQL, no LLM
# ---------------------------------------------------------------------------
def _gather_evidence(rep_id: str, *, lookback_days: int = 30) -> dict:
    """Pull the structured rep activity the LLM will reason over.

    Returns a dict the LLM can read without hallucinating — every field is
    a real DB count or list. The LLM's job is interpretation, not retrieval.
    """
    conn = db.get_conn()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        # Recent encounters by this rep
        enc_rows = conn.execute(
            "SELECT e.id, e.contact_id, e.conference_id, e.captured_at, "
            "e.sentiment, e.meeting_requested, e.structured_json, "
            "c.name AS conference_name "
            "FROM encounters e LEFT JOIN conferences c ON c.id = e.conference_id "
            "WHERE e.rep_id = ? AND e.captured_at >= ? "
            "ORDER BY e.captured_at DESC LIMIT 120",
            (rep_id, since),
        ).fetchall()

        recent_encounters = []
        for r in enc_rows:
            try:
                s = json.loads(r["structured_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                s = {}
            recent_encounters.append({
                "encounter_id": r["id"],
                "contact_id": r["contact_id"],
                "conference_id": r["conference_id"],
                "conference_name": r["conference_name"],
                "captured_at": r["captured_at"],
                "sentiment": r["sentiment"],
                "meeting_requested": bool(r["meeting_requested"]),
                "name": s.get("name"),
                "company": s.get("company"),
                "title": s.get("title"),
            })

        # Contacts touched in the lookback window
        contact_ids = list({e["contact_id"] for e in recent_encounters if e["contact_id"]})
        contacts_by_id: dict[str, dict] = {}
        if contact_ids:
            placeholders = ",".join("?" * len(contact_ids))
            rows = conn.execute(
                f"SELECT id, primary_name, primary_company, primary_title, "
                f"arc_verdict, arc_confidence, arc_summary, nudge_active, "
                f"nudge_text, updated_at FROM contacts WHERE id IN ({placeholders})",
                contact_ids,
            ).fetchall()
            contacts_by_id = {r["id"]: dict(r) for r in rows}

        # Per-conference yield: # encounters + # meeting_requested
        per_conf: dict[str, dict] = defaultdict(
            lambda: {"encounters": 0, "meetings_requested": 0, "name": None}
        )
        for e in recent_encounters:
            cid = e["conference_id"]
            if not cid:
                continue
            per_conf[cid]["encounters"] += 1
            if e["meeting_requested"]:
                per_conf[cid]["meetings_requested"] += 1
            per_conf[cid]["name"] = e["conference_name"]

        # Per-company persona coverage in recent activity
        per_company: dict[str, dict] = defaultdict(
            lambda: {"personas_seen": set(), "people": [], "n_encounters": 0}
        )
        # Pull people rows for the names we encountered to learn their personas
        names_companies = [
            (e["name"], e["company"]) for e in recent_encounters
            if e["name"] and e["company"]
        ]
        for name, co in names_companies:
            # Try matching a discovered person by name + company
            row = conn.execute(
                "SELECT persona FROM people WHERE lower(company_name) = ? "
                "AND lower(full_name) LIKE ? LIMIT 1",
                (co.lower(), f"%{name.lower().split()[0]}%"),
            ).fetchone()
            if row and row["persona"]:
                per_company[co]["personas_seen"].add(row["persona"])
            per_company[co]["n_encounters"] += 1
            per_company[co]["people"].append(name)

        # Stringify the sets so JSON-dump works
        per_company_serialised = {
            co: {
                "personas_seen": sorted(d["personas_seen"]),
                "n_encounters": d["n_encounters"],
                "people_sample": list(set(d["people"]))[:5],
            }
            for co, d in per_company.items()
        }

        # Pending discovery proposals
        pending_disc = conn.execute(
            "SELECT COUNT(*) FROM (SELECT target_id FROM feedback "
            "WHERE decision_kind = 'conference_discovery_proposal' "
            "AND target_id NOT IN ("
            "  SELECT target_id FROM feedback "
            "  WHERE decision_kind IN ('conference_discovery_approved', "
            "                            'conference_discovery_rejected')))",
        ).fetchone()[0]

        # Review queue size
        review_rows = conn.execute(
            "SELECT after_value FROM feedback "
            "WHERE decision_kind = 'entity_resolution' "
            "AND decided_at >= ?", (since,),
        ).fetchall()
        review_pending = 0
        for r in review_rows:
            try:
                v = json.loads(r["after_value"] or "{}")
                if v.get("decision") == "review_needed":
                    review_pending += 1
            except (json.JSONDecodeError, TypeError):
                continue

        # Competitor mentions across attended conferences
        icp = IcpConfig.default()
        competitor_set = {c.lower() for c in icp.competitors}
        attended_confs = [cid for cid in per_conf]
        comp_hits: list[dict] = []
        for cid in attended_confs:
            rows = conn.execute(
                "SELECT company_name FROM people WHERE conference_id = ?",
                (cid,),
            ).fetchall()
            for r in rows:
                co = (r["company_name"] or "").lower()
                for comp in competitor_set:
                    if comp in co:
                        comp_hits.append({"conference_id": cid, "company": r["company_name"],
                                          "matched": comp})
                        break

        # Contact arc summary
        arc_summary: dict[str, int] = defaultdict(int)
        warming_contacts_no_meeting: list[dict] = []
        tire_kickers: list[dict] = []
        for cid, c in contacts_by_id.items():
            v = c.get("arc_verdict") or "unknown"
            arc_summary[v] += 1
            if v == "warming":
                # Did the rep request a meeting in any encounter?
                ever_meeting = any(
                    e.get("meeting_requested") for e in recent_encounters
                    if e["contact_id"] == cid
                )
                if not ever_meeting:
                    warming_contacts_no_meeting.append({
                        "contact_id": cid,
                        "name": c["primary_name"],
                        "company": c["primary_company"],
                        "title": c["primary_title"],
                        "last_seen": c["updated_at"],
                    })
            elif v == "tire_kicker":
                tire_kickers.append({
                    "contact_id": cid,
                    "name": c["primary_name"],
                    "company": c["primary_company"],
                })

        # ICP-fit profile of conferences attended vs available
        attended_ids = set(attended_confs)
        uncovered_a_rows = conn.execute(
            "SELECT id, name, start_date, city FROM conferences "
            "WHERE tier = 'A' AND id NOT IN ("
            "  SELECT DISTINCT conference_id FROM encounters "
            "  WHERE conference_id IS NOT NULL) "
            "ORDER BY score DESC LIMIT 5"
        ).fetchall()
        uncovered_tier_a = [dict(r) for r in uncovered_a_rows]

        # Top accounts (company-level) — uses the new companies table if present.
        # Surfaces multi-conference companies + tier-A accounts so the LLM can
        # spot account-pattern leverage.
        top_accounts: list[dict] = []
        try:
            top_rows = conn.execute(
                "SELECT id, name, icp_score, account_tier, "
                "       (SELECT COUNT(DISTINCT conference_id) FROM people "
                "        WHERE company_id = c.id) AS confs, "
                "       (SELECT COUNT(*) FROM people WHERE company_id = c.id) AS pn, "
                "       (SELECT COUNT(*) FROM contacts WHERE company_id = c.id) AS cn "
                "FROM companies c WHERE approved = 1 "
                "ORDER BY icp_score DESC NULLS LAST LIMIT 15"
            ).fetchall()
            top_accounts = [
                {"company_id": r["id"], "name": r["name"], "tier": r["account_tier"],
                 "icp_score": r["icp_score"], "conference_count": r["confs"],
                 "people_count": r["pn"], "contact_count": r["cn"]}
                for r in top_rows
            ]
        except Exception:
            pass

        # Tier-A accounts with NO captured encounter (the obvious gap)
        uncovered_accounts: list[dict] = []
        try:
            ua_rows = conn.execute(
                "SELECT id, name, account_tier FROM companies "
                "WHERE approved = 1 AND account_tier = 'A' "
                "  AND id NOT IN ("
                "    SELECT DISTINCT company_id FROM contacts "
                "    WHERE company_id IS NOT NULL"
                "  ) LIMIT 8"
            ).fetchall()
            uncovered_accounts = [
                {"company_id": r["id"], "name": r["name"], "tier": r["account_tier"]}
                for r in ua_rows
            ]
        except Exception:
            pass
    finally:
        conn.close()

    return {
        "rep_id": rep_id,
        "lookback_days": lookback_days,
        "now": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "encounters": len(recent_encounters),
            "contacts_touched": len(contacts_by_id),
            "pending_discovery": pending_disc,
            "pending_review": review_pending,
            "competitor_mentions": len(comp_hits),
            "uncovered_tier_a_count": len(uncovered_tier_a),
        },
        "arc_summary": dict(arc_summary),
        "warming_contacts_without_meeting": warming_contacts_no_meeting[:10],
        "tire_kickers": tire_kickers[:10],
        "per_conference_yield": [
            {"conference_id": cid, "name": d["name"],
             "encounters": d["encounters"],
             "meetings_requested": d["meetings_requested"],
             "meeting_request_rate": round(
                 d["meetings_requested"] / max(d["encounters"], 1), 2)}
            for cid, d in per_conf.items()
        ],
        "per_company_coverage": per_company_serialised,
        "competitor_proximity": comp_hits[:8],
        "uncovered_tier_a": uncovered_tier_a,
        "top_accounts_by_icp": top_accounts,
        "uncovered_tier_a_accounts": uncovered_accounts,
    }


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You are a sales-strategy analyst for Grain Finance, a fintech selling "
    "embedded cross-currency FX hedging to PSPs, travel platforms, and "
    "cross-border payment companies. You will receive a structured JSON "
    "summary of one sales rep's recent activity. Your job: surface "
    "**3-7 HIGH-LEVERAGE, ACTIONABLE insights** that the rep would not "
    "have noticed scrolling through their CRM.\n\n"
    "Reply with ONLY a JSON object: {\"insights\": [<insight>, ...]} where "
    "each insight has:\n"
    "  kind          — one of: follow_up_gap | persona_gap | arc_regression | "
    "yield_retrospective | missed_opportunity | pattern_detection | "
    "tire_kicker_review | competitor_proximity | account_pattern\n"
    "               (account_pattern = company/account-level leverage: "
    "high-ICP account uncovered, multi-conference account with thin "
    "persona coverage, tier-A account with no champion yet, etc.)\n"
    "  severity      — high | medium | low\n"
    "  title         — 1 short sentence\n"
    "  body          — 2-3 sentences of context\n"
    "  suggested_action — one concrete next step the rep can take in 5 min\n"
    "  evidence      — { contact_ids: [], encounter_ids: [], conference_ids: [], "
    "                    companies: [] }  (only fill what's relevant)\n\n"
    "RULES:\n"
    "- Be specific. Use real names, companies, conferences from the data.\n"
    "- High severity = blocking pipeline or imminent miss. Low = nice to know.\n"
    "- Prefer 4-5 insights over 7. Don't fill quota.\n"
    "- If the data is sparse, return fewer (and possibly a single 'sparse_data' note)."
)


def _call_llm(evidence: dict) -> list[dict]:
    user = (
        "Rep activity summary (real data, do NOT invent fields):\n\n"
        f"{json.dumps(evidence, ensure_ascii=False, indent=2)[:8000]}"
    )
    try:
        data = llm.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=2500,
        )
    except llm.LLMError as exc:
        log.warning("brain synthesis LLM failed: %s", exc)
        return []
    raw = data.get("insights") or []
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        kind = (it.get("kind") or "").strip()
        severity = (it.get("severity") or "").strip().lower()
        title = (it.get("title") or "").strip()
        if not title or kind not in VALID_KINDS or severity not in VALID_SEVERITIES:
            continue
        cleaned.append({
            "kind": kind,
            "severity": severity,
            "title": title,
            "body": (it.get("body") or "").strip(),
            "suggested_action": (it.get("suggested_action") or "").strip(),
            "evidence": it.get("evidence") if isinstance(it.get("evidence"), dict) else {},
        })
    return cleaned


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _persist(rep_id: str, insights: list[dict]) -> list[str]:
    """Insert insights, returning the new ids. Fresh insights live 30 days."""
    if not insights:
        return []
    now = db.now_iso()
    expires = (datetime.now(timezone.utc) + timedelta(days=INSIGHT_TTL_DAYS)).isoformat()
    out: list[str] = []
    conn = db.get_conn()
    try:
        for it in insights:
            iid = "ins_" + uuid.uuid4().hex[:14]
            conn.execute(
                "INSERT INTO brain_insights (id, rep_id, kind, severity, title, "
                "body, suggested_action, evidence_json, status, created_at, "
                "expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    iid, rep_id, it["kind"], it["severity"], it["title"],
                    it["body"], it["suggested_action"],
                    json.dumps(it.get("evidence") or {}, ensure_ascii=False),
                    "fresh", now, expires,
                ),
            )
            out.append(iid)
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def synthesize_for_rep(rep_id: str, *, lookback_days: int = 30) -> dict:
    """Gather evidence → call LLM → persist insights. Returns {created, ids, evidence}."""
    evidence = _gather_evidence(rep_id, lookback_days=lookback_days)
    insights = _call_llm(evidence)
    ids = _persist(rep_id, insights)
    return {
        "rep_id": rep_id,
        "created": len(ids),
        "insight_ids": ids,
        "evidence_totals": evidence["totals"],
    }


def list_for_rep(rep_id: str, *, status: Optional[str] = "fresh",
                 limit: int = 20) -> list[dict]:
    conn = db.get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM brain_insights WHERE rep_id = ? AND status = ? "
                "AND expires_at >= ? ORDER BY "
                "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "created_at DESC LIMIT ?",
                (rep_id, status, db.now_iso(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM brain_insights WHERE rep_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (rep_id, limit),
            ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d.pop("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["evidence"] = {}
        out.append(d)
    return out


def update_status(insight_id: str, new_status: str, *,
                  decided_by: str = "ui", reason: str = "") -> dict:
    if new_status not in {"dismissed", "acknowledged", "actioned"}:
        raise ValueError(f"invalid status {new_status!r}")
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, status FROM brain_insights WHERE id = ?", (insight_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"insight {insight_id!r} not found")
        conn.execute(
            "UPDATE brain_insights SET status = ?, decided_at = ?, "
            "decided_by = ?, decided_reason = ? WHERE id = ?",
            (new_status, db.now_iso(), decided_by, reason, insight_id),
        )
    finally:
        conn.close()
    db.log_feedback(
        decision_kind=f"insight_{new_status}",
        target_kind="insight", target_id=insight_id,
        before={"status": row["status"]}, after={"status": new_status},
        reason=reason, decided_by=decided_by,
    )
    return {"status": new_status, "insight_id": insight_id}
