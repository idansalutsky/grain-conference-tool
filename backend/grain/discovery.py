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
import uuid
from typing import Optional

from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.discovery")


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


def discover_conferences(*,
                         region_hint: Optional[str] = None,
                         max_results: int = 6) -> dict:
    """Return a list of proposed (new) conferences with citations.

    Each proposal is also logged to `feedback` so the UI can show the
    pending approval queue. Returns {proposals, citations, raw_text}.
    """
    icp = IcpConfig.default()
    verticals = ", ".join(icp.company_level["verticals"])
    anchors = ", ".join(icp.anchor_events_known_attended[:6])

    region_clause = f"in the {region_hint} region" if region_hint else "globally"
    query = (
        f"List the next {max_results} upcoming conferences {region_clause} "
        f"most relevant to Grain Finance's ICP (verticals: {verticals}). "
        f"EXCLUDE these well-known anchor events we already track: {anchors}. "
        "Focus on under-indexed events where CFOs, treasurers, heads of "
        "payments, or cross-border payment / travel-platform executives "
        "actually attend. For each, give: name, city, country, exact start "
        "date, vertical, one-sentence why_relevant, estimated_attendance if "
        "known, and a source_url citation. Output only JSON."
    )

    try:
        text, citations = llm.search_grounded(query, system=DISCOVERY_SYSTEM)
    except llm.LLMError as exc:
        log.warning("discovery search failed: %s", exc)
        return {"proposals": [], "citations": [], "error": str(exc)}

    # The model is asked to reply with JSON, but Sonar sometimes wraps it in
    # prose. Try strict JSON first; fall back to first { ... } substring.
    proposals: list[dict] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed.get("proposals"), list):
            proposals = parsed["proposals"]
    except (json.JSONDecodeError, AttributeError):
        import re
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed.get("proposals"), list):
                    proposals = parsed["proposals"]
            except json.JSONDecodeError:
                pass

    # Persist each proposal to feedback (id is the round-tripped pk)
    saved: list[dict] = []
    for p in proposals[:max_results]:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        proposal_id = "disc_" + uuid.uuid4().hex[:14]
        db.log_feedback(
            decision_kind="conference_discovery_proposal",
            target_kind="conference",
            target_id=proposal_id,
            after={**p, "citations": citations},
            reason=p.get("why_relevant"),
            decided_by="discovery_agent",
        )
        saved.append({"proposal_id": proposal_id, **p})

    return {
        "proposals": saved,
        "citations": citations,
        "raw_text_preview": text[:500],
    }


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


def approve_proposal(proposal_id: str, *, decided_by: str = "ui") -> dict:
    """Promote a proposal into a real conferences row. Returns {conference_id}."""
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

    new_id = "c_disc_" + uuid.uuid4().hex[:12]
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO conferences (id, name, start_date, city, country, "
            "vertical, estimated_attendance, website, themes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id,
                payload.get("name") or "Unknown",
                payload.get("start_date"),
                payload.get("city"),
                payload.get("country"),
                payload.get("vertical"),
                payload.get("estimated_attendance"),
                payload.get("source_url"),
                payload.get("why_relevant"),
                db.now_iso(), db.now_iso(),
            ),
        )
    finally:
        conn.close()

    db.log_feedback(
        decision_kind="conference_discovery_approved",
        target_kind="conference", target_id=proposal_id,
        after={"conference_id": new_id, **payload},
        decided_by=decided_by,
    )

    # Re-score the new one
    from . import scoring
    scoring.rescore_all()  # cheap — 80 conferences in ~300ms
    return {"conference_id": new_id, "proposal": payload}


def reject_proposal(proposal_id: str, *, reason: str = "",
                    decided_by: str = "ui") -> None:
    db.log_feedback(
        decision_kind="conference_discovery_rejected",
        target_kind="conference", target_id=proposal_id,
        reason=reason, decided_by=decided_by,
    )
