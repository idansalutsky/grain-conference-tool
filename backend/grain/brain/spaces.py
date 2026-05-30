"""Long-term memory "spaces" — namespaced, compressed.

A space is a namespace of memory items plus ONE rolling summary. The summary is
what keeps the brain bounded: as items accumulate, we re-compress them into a
~150-250 word summary and the consumer reads the SUMMARY, not the raw pile.
"Don't overflow the brain."

Spaces (namespaces):
    icp           — Grain's ideal customer profile (verticals, buyers, competitors)
    events        — the conference landscape (vertical/region distribution)
    playbook      — what works in outreach (learned from captures)
    gaps          — under-covered verticals/regions (where to go discover)
    relationship  — salient, compressed insights about specific people/accounts

Storage (tables created in grain.db.init_db):
    brain_memory(id, space, item_key, content_json, salience, provenance,
                 created_at, updated_at)        — UNIQUE(space, item_key) upsert
    brain_space_summary(space PK, summary, item_count, updated_at)

Every LLM call here has a deterministic fallback so the module is hermetic with
no API key — tests never hit the network.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from .. import db, llm

log = logging.getLogger("grain.brain.spaces")

SPACES = ("icp", "events", "playbook", "gaps", "relationship")

# Re-compress a space once its item count crosses one of these thresholds (and
# always re-summarise on the first few writes so a space is never empty of a
# summary). Keeps the summary fresh without a re-compress on every single write
# once a space is large.
_RESUMMARY_THRESHOLDS = {1, 2, 3, 5, 8, 13, 21, 34, 55}

# Soft budget: how many of the most-salient items the deterministic summary and
# the LLM prompt consider. Bounds both cost and summary length.
_SUMMARY_BUDGET = 24

# Hard cap on the RAW item store per space. The rolling summary already bounds
# what consumers read, but brain_memory itself must not grow unbounded — so when
# a space exceeds this many rows we prune the lowest-salience / oldest items
# beyond the cap (keep top-N by salience then recency). "Don't overflow the brain"
# applies to the raw store too, not just the summary.
_MAX_ITEMS_PER_SPACE = 50

# Resummarize on a NEW key only every Nth new write (in addition to the early
# Fibonacci thresholds below), so a busy capture stream doesn't re-compress on
# literally every new contact. Updates to an EXISTING key never force a resummary
# on their own — they ride the same threshold cadence.
_RESUMMARY_EVERY_N = 8


# ---------------------------------------------------------------------------
# Read / write primitives
# ---------------------------------------------------------------------------
def write_item(
    space: str,
    item_key: str,
    content: dict,
    provenance: str,
    salience: float = 0.5,
) -> dict:
    """Upsert one memory item by (space, item_key).

    Returns the written row as a dict. Triggers a re-summarise when the space's
    item count crosses a budget threshold (so summaries stay current but we
    don't re-compress on every write of a big space).
    """
    if space not in SPACES:
        raise ValueError(f"unknown space {space!r}; valid: {SPACES}")
    now = db.now_iso()
    content_json = json.dumps(content, ensure_ascii=False)
    conn = db.get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM brain_memory WHERE space = ? AND item_key = ?",
            (space, item_key),
        ).fetchone()
        if existing:
            mid = existing["id"]
            conn.execute(
                "UPDATE brain_memory SET content_json = ?, salience = ?, "
                "provenance = ?, updated_at = ? WHERE id = ?",
                (content_json, float(salience), provenance, now, mid),
            )
        else:
            mid = "mem_" + uuid.uuid4().hex[:14]
            conn.execute(
                "INSERT INTO brain_memory (id, space, item_key, content_json, "
                "salience, provenance, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (mid, space, item_key, content_json, float(salience),
                 provenance, now, now),
            )
        # DEFECT 2 — bound the raw store: prune lowest-salience / oldest rows
        # beyond the cap so brain_memory never grows unbounded.
        pruned = _prune_space(conn, space)
        count = conn.execute(
            "SELECT COUNT(*) FROM brain_memory WHERE space = ?", (space,)
        ).fetchone()[0]
    finally:
        conn.close()

    # DEFECT 3 — throttle resummarize. We re-compress when the count crosses an
    # early Fibonacci threshold (so a small/new space always has a fresh summary)
    # OR every _RESUMMARY_EVERY_N items once large — NOT on literally every new
    # key. Updates to an existing key ride the same cadence (no per-write cost).
    # Pruning only ever removes the LOWEST-salience rows (beyond the cap), which
    # by construction sit outside the summary budget (_SUMMARY_BUDGET < cap) — so
    # a prune never changes what the summary says and does NOT force a resummary.
    _ = pruned
    is_new = existing is None
    cnt = int(count)
    should_resummarize = (
        cnt in _RESUMMARY_THRESHOLDS
        or (is_new and cnt % _RESUMMARY_EVERY_N == 0)
    )
    if should_resummarize:
        try:
            resummarize(space)
        except Exception as exc:  # noqa: BLE001 - summary is best-effort
            log.warning("resummarize(%s) failed: %s", space, exc)

    return {
        "id": mid, "space": space, "item_key": item_key,
        "content": content, "salience": float(salience),
        "provenance": provenance, "updated_at": now,
        "created": existing is None,
    }


def _prune_space(conn, space: str) -> bool:
    """Drop raw items beyond _MAX_ITEMS_PER_SPACE (keep top-N by salience then
    recency). Returns True if anything was deleted. Caller owns the connection."""
    count = conn.execute(
        "SELECT COUNT(*) FROM brain_memory WHERE space = ?", (space,)
    ).fetchone()[0]
    if int(count) <= _MAX_ITEMS_PER_SPACE:
        return False
    # Keep the top-N by salience (then recency); delete the rest by id.
    keep_ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM brain_memory WHERE space = ? "
            "ORDER BY salience DESC, updated_at DESC LIMIT ?",
            (space, _MAX_ITEMS_PER_SPACE),
        ).fetchall()
    }
    victims = [
        r["id"] for r in conn.execute(
            "SELECT id FROM brain_memory WHERE space = ?", (space,)
        ).fetchall() if r["id"] not in keep_ids
    ]
    if not victims:
        return False
    conn.executemany(
        "DELETE FROM brain_memory WHERE id = ?", [(v,) for v in victims]
    )
    log.info("pruned %d items from space %s (cap %d)",
             len(victims), space, _MAX_ITEMS_PER_SPACE)
    return True


def read_items(space: str, limit: int = 100) -> list[dict]:
    """Most-salient-first items for a space."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, space, item_key, content_json, salience, provenance, "
            "created_at, updated_at FROM brain_memory WHERE space = ? "
            "ORDER BY salience DESC, updated_at DESC LIMIT ?",
            (space, limit),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["content"] = json.loads(d.pop("content_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["content"] = {}
        out.append(d)
    return out


def get_summary(space: str) -> Optional[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT space, summary, item_count, updated_at "
            "FROM brain_space_summary WHERE space = ?", (space,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_spaces() -> list[dict]:
    """One row per known space: name, item_count, summary, updated_at.

    Always returns all five canonical spaces even if a space has no rows yet
    (so the frontend can render the full set of namespaces).
    """
    conn = db.get_conn()
    try:
        counts = {
            r["space"]: r["n"] for r in conn.execute(
                "SELECT space, COUNT(*) AS n FROM brain_memory GROUP BY space"
            ).fetchall()
        }
        summaries = {
            r["space"]: dict(r) for r in conn.execute(
                "SELECT space, summary, item_count, updated_at "
                "FROM brain_space_summary"
            ).fetchall()
        }
    finally:
        conn.close()
    out = []
    for s in SPACES:
        summ = summaries.get(s) or {}
        out.append({
            "name": s,
            "item_count": int(counts.get(s, 0)),
            "summary": summ.get("summary"),
            "updated_at": summ.get("updated_at"),
        })
    return out


# ---------------------------------------------------------------------------
# Compression — the rolling summary that keeps a space bounded
# ---------------------------------------------------------------------------
_SUMMARY_SYSTEM = (
    "You compress a sales-intelligence memory space for Grain Finance (embedded "
    "cross-border FX hedging). You will receive a list of memory items. Produce "
    "a SINGLE rolling summary of 150-250 words that keeps the most SALIENT facts "
    "and drops detail. Prioritise: which segments/accounts/events matter, what "
    "patterns recur, and what the team should do next. Do NOT list every item. "
    "Reply with ONLY the summary prose — no preamble, no JSON, no markdown."
)


def _deterministic_summary(space: str, items: list[dict]) -> str:
    """Key-free fallback: join the top-N salient item keys + a one-line gist.

    Bounded by construction (top _SUMMARY_BUDGET keys), so the space stays small
    even with no LLM available."""
    if not items:
        return f"[{space}] empty - no items recorded yet."
    top = items[:_SUMMARY_BUDGET]
    bits = []
    for it in top:
        key = it.get("item_key") or it.get("id")
        content = it.get("content") or {}
        gist = (
            content.get("summary")
            or content.get("why_relevant")
            or content.get("insight")
            or content.get("note")
            or content.get("name")
            or ""
        )
        gist = str(gist).strip()
        bits.append(f"{key}: {gist}" if gist else str(key))
    more = "" if len(items) <= _SUMMARY_BUDGET else f" (+{len(items) - _SUMMARY_BUDGET} more)"
    return f"[{space}] {len(items)} items{more}. Salient: " + "; ".join(bits)


def resummarize(space: str) -> dict:
    """Re-compress a space into one rolling summary; persist it.

    LLM path produces bounded prose; deterministic fallback joins top salient
    keys. Either way the result is bounded — this is what keeps the brain from
    overflowing.
    """
    items = read_items(space, limit=_SUMMARY_BUDGET)
    all_count_conn = db.get_conn()
    try:
        item_count = int(all_count_conn.execute(
            "SELECT COUNT(*) FROM brain_memory WHERE space = ?", (space,)
        ).fetchone()[0])
    finally:
        all_count_conn.close()

    summary_text: Optional[str] = None
    if llm.config.OPENROUTER_API_KEY and items:
        compact = [
            {"item_key": it.get("item_key"),
             "salience": it.get("salience"),
             "content": it.get("content")}
            for it in items
        ]
        try:
            data = llm.chat_json(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM
                        + ' Reply as JSON {"summary": "..."} only.'},
                    {"role": "user", "content": json.dumps(
                        {"space": space, "items": compact}, ensure_ascii=False)},
                ],
                temperature=0.2, max_tokens=500,
            )
            cand = (data.get("summary") or "").strip()
            if cand:
                summary_text = cand
        except llm.LLMError as exc:
            log.info("LLM resummarize(%s) fell back to deterministic: %s", space, exc)

    if not summary_text:
        summary_text = _deterministic_summary(space, items)

    return _persist_space_summary(space, summary_text, item_count)


def _persist_space_summary(space: str, summary_text: str, item_count: int) -> dict:
    """Write ONE rolling summary for a space (the L2 read-surface)."""
    now = db.now_iso()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO brain_space_summary (space, summary, item_count, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(space) DO UPDATE SET "
            "summary = excluded.summary, item_count = excluded.item_count, "
            "updated_at = excluded.updated_at",
            (space, summary_text, int(item_count), now),
        )
    finally:
        conn.close()
    return {"space": space, "summary": summary_text,
            "item_count": int(item_count), "updated_at": now}


# ---------------------------------------------------------------------------
# L2 REWIRE — the space summaries are JUDGMENTS OVER ALL the L1 rollups, NOT a
# salience-truncated top-50 pile. The owner's critique was that top-50 by
# salience is a dumb cutoff that drops dots. Now:
#   - relationship ← rolls up EVERY account rollup (one judged line per account,
#                    compressed into one space summary). No account is dropped.
#   - events       ← rolls up EVERY event rollup (one judged line per event).
#   - gaps         ← rolls up EVERY segment rollup (the coverage-gap verdicts).
#   - icp          ← rolls up the segment rollups against the ICP target verticals.
# Each summary is a judgment over the FULL set of entities (we may LLM-compress
# the final prose, but it summarises the JUDGED rollups, not a truncated subset).
# playbook stays in brain_memory as-is (genuinely incremental learning items).
# ---------------------------------------------------------------------------
_ROLLUP_SPACE_SYSTEM = (
    "You compress a Grain Finance sales-intelligence memory space. You receive "
    "JUDGED middle-management rollups (one per entity: events, accounts, or "
    "segments) - already summarised from real captured data, NOT raw items. "
    "Produce ONE rolling summary of 120-220 words that keeps the judgment: which "
    "entities matter most (by priority), the relationship/coverage state, and "
    "what the team should do next. Ground every claim in the rollups - do not "
    "invent. Reply with ONLY JSON {\"summary\": \"...\"}."
)


def _llm_compress_rollups(space: str, rollups: list[dict]) -> Optional[str]:
    """OPTIONALLY LLM-compress the final space summary over the JUDGED rollups
    (not a salience-truncated subset). Returns None when no key (hermetic)."""
    if not (llm.config.OPENROUTER_API_KEY and rollups):
        return None
    compact = [{"title": r.get("title"), "priority": r.get("priority"),
                "summary": r.get("summary"), "features": r.get("features")}
               for r in rollups[:60]]
    try:
        data = llm.chat_json(
            [{"role": "system", "content": _ROLLUP_SPACE_SYSTEM},
             {"role": "user", "content": json.dumps(
                 {"space": space, "rollups": compact}, ensure_ascii=False)}],
            temperature=0.2, max_tokens=500,
        )
        cand = (data.get("summary") or "").strip()
        return cand or None
    except llm.LLMError as exc:
        log.info("LLM rollup-compress(%s) fell back to deterministic: %s",
                 space, exc)
        return None


def _summarize_relationship_from_rollups(rollups: list[dict]) -> str:
    if not rollups:
        return "[relationship] no accounts rolled up yet."
    n = len(rollups)
    warming = [r for r in rollups if (r["features"] or {}).get("has_warming")]
    tire = [r for r in rollups if (r["features"] or {}).get("has_tire_kicker")
            and not (r["features"] or {}).get("has_warming")]
    top = rollups[:8]  # highest-priority for the named highlights ONLY
    top_str = "; ".join(
        f"{r['title']} ({(r['features'] or {}).get('account_arc','?')}, "
        f"{(r['features'] or {}).get('n_encounters',0)} enc / "
        f"{(r['features'] or {}).get('events_spanned',0)} events)"
        for r in top
    )
    return (
        f"[relationship] {n} account(s) tracked (judged from L1 rollups, none "
        f"dropped). {len(warming)} warming, {len(tire)} tire-kicker-only. "
        f"Top by priority: {top_str}"
        + (f" (+{n - len(top)} more accounts rolled up)." if n > len(top) else ".")
    )


def _summarize_events_from_rollups(rollups: list[dict]) -> str:
    if not rollups:
        return "[events] no events rolled up yet."
    n = len(rollups)
    worked = [r for r in rollups if (r["features"] or {}).get("n_encounters", 0) > 0]
    worth_return = [r for r in rollups
                    if (r["features"] or {}).get("worth_returning_verdict")
                    in ("worth_returning", "worth_attending")]
    from collections import Counter
    vert_counts = Counter((r["features"] or {}).get("vertical") or "unknown"
                          for r in rollups)
    top = rollups[:6]
    top_str = "; ".join(
        f"{r['title']} [{(r['features'] or {}).get('worth_returning_verdict','?')}]"
        for r in top
    )
    return (
        f"[events] {n} event(s) rolled up (judged, none dropped); {len(worked)} "
        f"worked with encounters, {len(worth_return)} judged worth attending/"
        f"returning. By vertical: {dict(vert_counts)}. Top by priority: {top_str}"
        + (f" (+{n - len(top)} more)." if n > len(top) else ".")
    )


def _summarize_gaps_from_rollups(seg_rollups: list[dict]) -> str:
    if not seg_rollups:
        return "[gaps] no segments rolled up yet."
    gaps = [r for r in seg_rollups if (r["features"] or {}).get("coverage_gap")]
    gap_str = "; ".join(
        # Prefer the segment name from features; fall back to the (possibly None)
        # title. Guard against a None title so a malformed rollup can't NPE the
        # whole space summary.
        f"{(r.get('features') or {}).get('segment') or (r.get('title') or '').replace('Segment: ', '') or '?'} "
        f"(A={(r['features'] or {}).get('tier_mix',{}).get('A',0)}, "
        f"{(r['features'] or {}).get('n_accounts',0)} accounts)"
        for r in gaps
    ) or "(none - coverage adequate across segments)"
    return (
        f"[gaps] {len(seg_rollups)} segment(s) judged; {len(gaps)} flagged as "
        f"coverage gaps (go discover here): {gap_str}."
    )


def _summarize_icp_from_rollups(seg_rollups: list[dict]) -> str:
    """ICP space rolled up from the segment rollups against the ICP targets."""
    from ..icp import IcpConfig
    icp = IcpConfig.default()
    targets = icp.company_level["verticals"]
    by_seg = {(r["features"] or {}).get("segment"): r for r in seg_rollups}
    covered = [v for v in targets if v in by_seg]
    uncovered = [v for v in targets if v not in by_seg]
    buyers = ", ".join(icp.person_level["target_titles"][:5])
    comps = ", ".join(icp.competitors[:6])
    return (
        f"[icp] Grain targets {len(targets)} verticals; {len(covered)} have "
        f"event coverage, {len(uncovered)} do not "
        f"({', '.join(uncovered) or 'all covered'}). Primary buyers: {buyers}. "
        f"Competitors (auto-rejected): {comps}."
    )


def rebuild_space_summaries_from_rollups(use_llm: bool = True) -> dict:
    """L2 rewire: recompute the events / relationship / gaps / icp space
    summaries by ROLLING UP the L1 rollups (one judged line per entity →
    compressed), NOT from the top-50 brain_memory pile.

    Every event/account/segment has an L1 rollup, so the space summary is a
    judgment over ALL of them — nothing is dropped. We may LLM-compress the
    final prose, but it summarises the JUDGED rollups, not a truncated subset.
    Hermetic: with no key the deterministic summary stands.

    playbook is NOT touched here (it stays its own incremental brain_memory
    space — genuinely additive learning items).
    """
    from . import rollups as _rollups
    acct = _rollups.list_rollups("account", limit=10_000, sort="priority")
    evt = _rollups.list_rollups("event", limit=10_000, sort="priority")
    seg = _rollups.list_rollups("segment", limit=10_000, sort="priority")

    out: dict[str, dict] = {}

    rel_text = (use_llm and _llm_compress_rollups("relationship", acct)) \
        or _summarize_relationship_from_rollups(acct)
    out["relationship"] = _persist_space_summary("relationship", rel_text, len(acct))

    ev_text = (use_llm and _llm_compress_rollups("events", evt)) \
        or _summarize_events_from_rollups(evt)
    out["events"] = _persist_space_summary("events", ev_text, len(evt))

    # gaps + icp derive from the segment rollups (deterministic — they are
    # already compact verdicts; no LLM needed).
    out["gaps"] = _persist_space_summary(
        "gaps", _summarize_gaps_from_rollups(seg), len(seg))
    out["icp"] = _persist_space_summary(
        "icp", _summarize_icp_from_rollups(seg), len(seg))

    return {"rollup_counts": {"account": len(acct), "event": len(evt),
                              "segment": len(seg)},
            "summaries": out}


# ---------------------------------------------------------------------------
# Seeding — idempotent, called from seed_db.main()
# ---------------------------------------------------------------------------
def _known_event_signature(name: str) -> str:
    import re
    n = re.sub(r"\b(19|20)\d{2}\b", "", (name or "").lower())
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


# ---------------------------------------------------------------------------
# Ingest — connect the brain to the REAL capture pipeline (contacts/encounters)
#
# The brain is supposed to sit ON TOP of the app's real captured relationships,
# not beside them. These functions take a genuine captured contact (the same
# `contacts` rows that the voice/text capture pipeline writes, carrying an arc
# verdict computed from real encounter history) and fold ONE compressed, salient
# insight per contact into the `relationship` (and `playbook`) space. Provenance
# is `capture:field`, distinguishing real field captures from brain-path test
# items (`capture:brain`) and seeds (`seed:*`).
# ---------------------------------------------------------------------------
def _contact_item_key(name: str, company: str) -> str:
    import re
    base = f"{(name or 'unknown')}|{(company or 'unknown')}".lower()
    base = re.sub(r"[^a-z0-9|]+", "_", base).strip("_")
    return base or "contact_unknown"


def _arc_salience(arc_verdict: str | None) -> float:
    return {
        "warming": 0.85,
        "tire_kicker": 0.6,
        "cooling": 0.45,
        "flat": 0.4,
    }.get((arc_verdict or "").lower(), 0.5)


def _is_competitor_company(company: str | None) -> str | None:
    """Return the matched competitor name if the company is a Grain competitor."""
    if not company:
        return None
    from ..icp import IcpConfig
    low = company.lower()
    for c in IcpConfig.default().competitors:
        cl = (c or "").lower()
        if cl and cl in low:
            return c
    return None


def ingest_encounter(encounter_or_contact: dict) -> Optional[dict]:
    """Fold ONE real captured contact into the relationship (+ playbook) space.

    Accepts a captured contact/encounter dict. Recognised keys (all optional but
    a name+company or an arc is expected):
        primary_name / name, primary_company / company, primary_title / title,
        arc_verdict / arc, arc_summary, arc_confidence, encounter_count / n,
        soft_signals, meeting_requested, contact_id / id.

    Runs the lightweight capture gate (reject Grain competitors; require some
    substance) then writes a compressed insight with provenance `capture:field`.
    Returns the written relationship row, or None if the gate rejected it.
    This is the real-capture analogue of the brain's memory_writer node — but it
    is driven by the genuine `contacts` table, not a brain-path test input.
    """
    c = encounter_or_contact or {}
    name = (c.get("primary_name") or c.get("name") or "").strip() or "Unknown contact"
    company = (c.get("primary_company") or c.get("company") or "").strip() or "unknown company"
    title = (c.get("primary_title") or c.get("title") or "").strip() or "unknown role"
    arc_verdict = (c.get("arc_verdict") or c.get("arc") or "flat")
    arc_summary = (c.get("arc_summary") or "").strip()
    n_enc = c.get("encounter_count")
    if n_enc is None:
        n_enc = c.get("n")
    meeting = bool(c.get("meeting_requested"))
    signals = c.get("soft_signals") or []

    # GATE (REAL/ICP-FIT) — never record a contact who works at a competitor.
    comp_hit = _is_competitor_company(company)
    if comp_hit:
        log.info("ingest_encounter: rejected %s @ %s (competitor %s)",
                 name, company, comp_hit)
        return None
    # REAL — need at least a name+company or an arc verdict.
    if name == "Unknown contact" and company == "unknown company":
        return None

    # COMPRESS — one salient insight per contact (NOT the raw encounters).
    enc_clause = f" over {int(n_enc)} encounters" if isinstance(n_enc, int) and n_enc else ""
    insight = (
        f"{name} ({title} @ {company}) - {arc_verdict}{enc_clause}."
        + (f" {arc_summary}" if arc_summary else "")
    ).strip()
    item_key = _contact_item_key(name, company)
    salience = _arc_salience(arc_verdict)

    compressed = {
        "summary": insight,
        "insight": insight,
        "name": name, "company": company, "title": title,
        "arc": arc_verdict,
        "arc_summary": arc_summary or None,
        "encounter_count": int(n_enc) if isinstance(n_enc, int) else None,
        "meeting_requested": meeting,
        "soft_signals": signals,
        "contact_id": c.get("contact_id") or c.get("id"),
        "item_key": item_key,
        "salience": salience,
    }
    rel = write_item(
        "relationship", item_key,
        {k: v for k, v in compressed.items() if v is not None},
        provenance="capture:field", salience=salience,
    )

    # PLAYBOOK — a warming captured relationship is a "what works" signal.
    if (arc_verdict or "").lower() == "warming":
        write_item(
            "playbook", "win_" + item_key,
            {"summary": f"Working: {title} at {company} is warming"
                        + (f" ({arc_summary})" if arc_summary else "") + ".",
             "signals": signals,
             "contact_id": compressed["contact_id"],
             "arc": "warming"},
            provenance="capture:field", salience=0.6,
        )
    return {"space": "relationship", **rel}


# ---------------------------------------------------------------------------
# Ingest HUMAN DECISIONS — close the feedback→learning loop.
#
# Every human decision (`db.log_feedback`) is now folded into the brain as ONE
# COMPRESSED knowledge item in the right space, with provenance `feedback:<kind>`.
# This is what makes the playbook/events/icp spaces visibly get SMARTER as reps
# use the tool — the brain CONSUMES the feedback table instead of just auditing it.
#
# The mapping (decision_kind → space) uses the REAL strings emitted across the
# codebase (scoring/conferences, nudges, review_queue, discovery, people/contacts,
# prospect_discovery). Unknown kinds are ignored (return None). write_item already
# prunes + throttles the resummarize, so this stays cheap and bounded.
# ---------------------------------------------------------------------------
def _fb_key(prefix: str, *parts: Any) -> str:
    import re
    base = (prefix + "_" + "_".join(str(p) for p in parts if p not in (None, ""))).lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    return (base or prefix)[:80]


def _as_dict(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def ingest_feedback(
    decision_kind: str,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    before_value: Any = None,
    after_value: Any = None,
    note: Optional[str] = None,
) -> Optional[dict]:
    """Map ONE human decision into a COMPRESSED knowledge update in the brain.

    Returns the written brain item (dict with `space`) or None for kinds the
    brain doesn't learn from. `before_value`/`after_value` may be dicts or JSON
    strings (the feedback table stores JSON). Provenance is `feedback:<kind>`;
    salience reflects signal strength (an explicit human override is a strong
    signal). This is the consumer that closes the loop the audit flagged.
    """
    kind = (decision_kind or "").strip()
    if not kind:
        return None
    before = _as_dict(before_value)
    after = _as_dict(after_value)
    prov = f"feedback:{kind}"

    # --- conference score override → events space -------------------------
    if kind == "conference_score_adjust":
        new_score = after.get("score")
        old_score = before.get("score")
        new_tier = after.get("tier")
        delta = after.get("delta")
        direction = "up" if isinstance(delta, (int, float)) and delta >= 0 else "down"
        summary = (
            f"Rep adjusted event {target_id} {direction} to score "
            f"{new_score} (tier {new_tier}) - model said {old_score}. "
            f"Reps value this event more than the model did."
            if direction == "up" else
            f"Rep adjusted event {target_id} down to score {new_score} "
            f"(tier {new_tier}) - model said {old_score}. "
            f"Reps value this event LESS than the model did."
        )
        if note:
            summary += f" Reason: {note}"
        return {"space": "events", **write_item(
            "events", _fb_key("override", target_id),
            {"summary": summary, "note": summary,
             "conference_id": target_id,
             "model_score": old_score, "rep_score": new_score,
             "tier": new_tier, "delta": delta},
            provenance=prov, salience=0.78,
        )}

    # --- discovery approve / reject → events space ------------------------
    if kind == "conference_discovery_approved":
        vertical = after.get("vertical") or "unknown vertical"
        region = after.get("region") or after.get("country") or "unknown region"
        name = after.get("name") or target_id
        summary = (f"Reps WANT {vertical} / {region} events - approved "
                   f"discovered event '{name}'. Find more like it.")
        return {"space": "events", **write_item(
            "events", _fb_key("want", vertical, region),
            {"summary": summary, "note": summary,
             "vertical": vertical, "region": region,
             "example": name, "signal": "approved"},
            provenance=prov, salience=0.7,
        )}
    if kind == "conference_discovery_rejected":
        summary = (f"Reps SKIPPED discovered event {target_id}"
                   + (f" - {note}" if note else "")
                   + ". Down-rank similar discoveries.")
        return {"space": "events", **write_item(
            "events", _fb_key("skip", target_id),
            {"summary": summary, "note": summary,
             "proposal_id": target_id, "signal": "rejected", "reason": note},
            provenance=prov, salience=0.55,
        )}

    # --- nudge accept / dismiss → playbook space --------------------------
    if kind == "nudge_accept":
        summary = (f"Reps ACT on nudges (contact {target_id} - accepted). "
                   f"This nudge situation works - keep surfacing it.")
        return {"space": "playbook", **write_item(
            "playbook", _fb_key("nudge_acted", target_id),
            {"summary": summary, "note": summary,
             "contact_id": target_id, "signal": "nudge_accepted"},
            provenance=prov, salience=0.65,
        )}
    if kind == "nudge_dismiss":
        situation = (before.get("kind") or before.get("nudge_kind")
                     or before.get("situation") or "this situation")
        summary = (f"Reps IGNORE '{situation}' nudges (contact {target_id} - "
                   f"dismissed" + (f": {note}" if note else "") + "). Tune down.")
        return {"space": "playbook", **write_item(
            "playbook", _fb_key("nudge_ignored", situation, target_id),
            {"summary": summary, "note": summary,
             "contact_id": target_id, "situation": situation,
             "signal": "nudge_dismissed", "reason": note},
            provenance=prov, salience=0.6,
        )}

    # --- entity confirm / reject (rep_match) → relationship space ---------
    if kind == "rep_match_confirmed":
        cid = after.get("contact_id") or target_id
        summary = (f"Rep CONFIRMED encounter {target_id} is the same person "
                   f"(contact {cid}) across events. Identity merge validated.")
        return {"space": "relationship", **write_item(
            "relationship", _fb_key("merge_ok", cid, target_id),
            {"summary": summary, "note": summary, "insight": summary,
             "contact_id": cid, "encounter_id": target_id,
             "signal": "match_confirmed"},
            provenance=prov, salience=0.7,
        )}
    if kind == "rep_match_rejected":
        cid = after.get("contact_id") or target_id
        summary = (f"Rep SPLIT encounter {target_id} into a NEW contact "
                   f"({cid}) - the model's match was wrong"
                   + (f": {note}" if note else "") + ".")
        return {"space": "relationship", **write_item(
            "relationship", _fb_key("split", cid, target_id),
            {"summary": summary, "note": summary, "insight": summary,
             "contact_id": cid, "encounter_id": target_id,
             "signal": "match_rejected", "reason": note},
            provenance=prov, salience=0.7,
        )}

    # --- ICP / persona / arc override → icp space -------------------------
    if kind in ("people_score_override", "arc_override"):
        persona = after.get("persona") or before.get("persona")
        score = after.get("icp_score")
        arc = after.get("arc_verdict")
        bits = []
        if persona:
            bits.append(f"persona={persona}")
        if score is not None:
            bits.append(f"icp_score={score}")
        if arc:
            bits.append(f"arc={arc}")
        marker = ", ".join(bits) or "ICP fit"
        summary = (f"Rep marked person {target_id} as {marker} (override of the "
                   f"model)" + (f": {note}" if note else "")
                   + ". Reps know this fit better than the title classifier.")
        return {"space": "icp", **write_item(
            "icp", _fb_key("rep_fit", target_id),
            {"summary": summary, "note": summary,
             "person_id": target_id, "persona": persona,
             "icp_score": score, "arc": arc, "signal": kind, "reason": note},
            provenance=prov, salience=0.75,
        )}

    # --- prospect (company) discovery approve / reject → icp space ---------
    if kind == "prospect_discovery_approved":
        tier = after.get("account_tier")
        summary = (f"Rep marked company {target_id} as ICP-fit (approved "
                   f"prospect, tier {tier})"
                   + (f": {note}" if note else "") + ".")
        return {"space": "icp", **write_item(
            "icp", _fb_key("company_fit", target_id),
            {"summary": summary, "note": summary,
             "company_id": target_id, "account_tier": tier,
             "signal": "prospect_approved"},
            provenance=prov, salience=0.7,
        )}
    if kind == "prospect_discovery_rejected":
        summary = (f"Rep marked company {target_id} as NOT ICP-fit (rejected "
                   f"prospect)" + (f": {note}" if note else "") + ".")
        return {"space": "icp", **write_item(
            "icp", _fb_key("company_notfit", target_id),
            {"summary": summary, "note": summary,
             "company_id": target_id, "signal": "prospect_rejected",
             "reason": note},
            provenance=prov, salience=0.6,
        )}

    # Unknown / audit-only kinds (entity_resolution auto decisions, brief_rate,
    # parameter_update, person_added/deleted, rep_added, discovery proposals,
    # ...) — not a human learning signal the brain folds in. Ignore.
    return None


def sync_relationship_space_from_db() -> dict:
    """(Re)build the relationship/playbook spaces from the REAL contacts table.

    Reads every genuine captured contact (with its arc verdict computed from real
    encounter history) and folds ONE compressed insight per contact into the
    relationship space (and playbook for warming contacts), provenance
    `capture:field`. Compressed: one salient line per contact, never raw
    encounters. Idempotent — clears prior `capture:field` rows first, so a
    re-sync reflects the current DB state exactly (no stale/dupe contacts).

    Returns a summary of what was ingested.
    """
    # 1. Clear prior field-captured rows so the rebuild is exact + idempotent.
    conn = db.get_conn()
    try:
        conn.execute(
            "DELETE FROM brain_memory WHERE provenance = ? "
            "AND space IN ('relationship', 'playbook')",
            ("capture:field",),
        )
    finally:
        conn.close()

    # 2. Read real contacts + their encounter counts.
    conn = db.get_conn()
    try:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT c.id, c.primary_name, c.primary_company, c.primary_title, "
                "c.arc_verdict, c.arc_summary, c.arc_confidence, "
                "(SELECT COUNT(*) FROM encounters e WHERE e.contact_id = c.id) AS n, "
                "(SELECT MAX(e.meeting_requested) FROM encounters e "
                " WHERE e.contact_id = c.id) AS any_meeting "
                "FROM contacts c"
            ).fetchall()]
        except Exception:  # contacts table may not exist on a bare DB
            rows = []
    finally:
        conn.close()

    ingested = 0
    rejected = 0
    for r in rows:
        payload = {
            "contact_id": r.get("id"),
            "primary_name": r.get("primary_name"),
            "primary_company": r.get("primary_company"),
            "primary_title": r.get("primary_title"),
            "arc_verdict": r.get("arc_verdict"),
            "arc_summary": r.get("arc_summary"),
            "arc_confidence": r.get("arc_confidence"),
            "encounter_count": int(r.get("n") or 0),
            "meeting_requested": bool(r.get("any_meeting")),
        }
        result = ingest_encounter(payload)
        if result is None:
            rejected += 1
        else:
            ingested += 1

    # Keep the playbook (incremental) summary fresh after a bulk rebuild.
    resummarize("playbook")
    # L1 + L2: rebuild the judged rollups from the (now-current) dots and derive
    # the relationship/events/gaps/icp space summaries from them — so the
    # relationship summary is a judgment over ALL accounts, not a top-50 pile.
    from . import rollups as _rollups
    _rollups.rebuild_all_rollups()
    rebuild_space_summaries_from_rollups()
    return {"contacts_seen": len(rows), "ingested": ingested,
            "rejected": rejected}


def seed_brain_spaces() -> dict:
    """Idempotent seed of the five spaces from the existing app data.

    - icp           ← icp.py (verticals, buyers, competitors)
    - events        ← summarised distribution from the conferences table
    - gaps          ← under-covered verticals/regions computed from conferences
    - playbook      ← a minimal starter heuristic (+ warming captured contacts)
    - relationship  ← synced from the REAL contacts/encounters table (arc-verdict
                      captures), one compressed insight per contact

    Safe to re-run: write_item upserts by (space, item_key); the relationship
    sync clears + rebuilds its own capture:field rows so it's idempotent too.
    """
    from ..icp import IcpConfig
    icp = IcpConfig.default()
    written = {s: 0 for s in SPACES}

    # --- ICP space ---
    write_item(
        "icp", "verticals",
        {"summary": "Target verticals: "
                    + ", ".join(icp.company_level["verticals"]),
         "verticals": icp.company_level["verticals"]},
        provenance="seed:icp.py", salience=0.95,
    )
    written["icp"] += 1
    write_item(
        "icp", "buyers",
        {"summary": "Primary buyers are finance/treasury: "
                    + ", ".join(icp.person_level["target_titles"][:6]),
         "target_titles": icp.person_level["target_titles"],
         "personas": list(icp.personas.keys())},
        provenance="seed:icp.py", salience=0.9,
    )
    written["icp"] += 1
    write_item(
        "icp", "competitors",
        {"summary": "Grain competitors (reject if they show up as a target): "
                    + ", ".join(icp.competitors),
         "competitors": icp.competitors},
        provenance="seed:icp.py", salience=0.85,
    )
    written["icp"] += 1
    write_item(
        "icp", "fx_signals",
        {"summary": "FX-exposure signals that qualify a company: "
                    + ", ".join(icp.company_level["fx_exposure_signals"]),
         "signals": icp.company_level["fx_exposure_signals"]},
        provenance="seed:icp.py", salience=0.7,
    )
    written["icp"] += 1

    # --- Events + gaps spaces (computed from the conferences table) ---
    conn = db.get_conn()
    try:
        try:
            confs = [dict(r) for r in conn.execute(
                "SELECT name, vertical, region FROM conferences"
            ).fetchall()]
        except Exception:  # table may not exist on a bare DB
            confs = []
    finally:
        conn.close()

    from collections import Counter
    vert_counts = Counter((c.get("vertical") or "unknown") for c in confs)
    region_counts = Counter((c.get("region") or "unknown") for c in confs)

    write_item(
        "events", "distribution",
        {"summary": f"{len(confs)} known conferences. "
                    f"By vertical: {dict(vert_counts)}. "
                    f"By region: {dict(region_counts)}.",
         "total": len(confs),
         "by_vertical": dict(vert_counts),
         "by_region": dict(region_counts)},
        provenance="seed:conferences", salience=0.8,
    )
    written["events"] += 1

    # Record the known-event signatures so the discovery gate can dedupe later.
    known_sigs = sorted({_known_event_signature(c.get("name") or "") for c in confs
                         if c.get("name")})
    write_item(
        "events", "known_signatures",
        {"summary": f"{len(known_sigs)} known event name-signatures (for dedupe).",
         "signatures": known_sigs},
        provenance="seed:conferences", salience=0.6,
    )
    written["events"] += 1

    # --- Gaps: thin verticals/regions across the ICP target verticals ---
    icp_verticals = icp.company_level["verticals"]
    all_regions = ["NA", "EU", "APAC", "MEA", "LATAM"]
    thin_verticals = sorted(
        [v for v in icp_verticals if vert_counts.get(v, 0) <= 1],
        key=lambda v: vert_counts.get(v, 0),
    )
    thin_regions = sorted(
        [r for r in all_regions if region_counts.get(r, 0) <= 2],
        key=lambda r: region_counts.get(r, 0),
    )
    write_item(
        "gaps", "coverage_gaps",
        {"summary": "Under-covered (go discover here) - "
                    f"thin verticals: {thin_verticals or '(none)'}; "
                    f"thin regions: {thin_regions or '(none)'}.",
         "thin_verticals": thin_verticals,
         "thin_regions": thin_regions},
        provenance="seed:computed", salience=0.85,
    )
    written["gaps"] += 1

    # --- Playbook: minimal starter ---
    write_item(
        "playbook", "outreach_baseline",
        {"summary": "Baseline: lead with cross-border FX pain for the buyer's "
                    "vertical; a meeting-requested + explicit-pain signal is the "
                    "strongest warming indicator; treasury/finance titles convert "
                    "best.",
         "note": "evolves as captures accumulate"},
        provenance="seed:baseline", salience=0.5,
    )
    written["playbook"] += 1

    # --- Relationship (+ playbook): reflect the REAL captured contacts ---
    # The brain sits ON TOP of the app's real capture pipeline: read the genuine
    # contacts/encounters (with arc verdicts) from the DB and fold one compressed
    # insight per contact into the relationship space (provenance capture:field).
    # On a bare DB with no contacts yet this just ensures a summary row exists.
    rel_sync = sync_relationship_space_from_db()

    # --- L1 rollups + L2 rewire ------------------------------------------
    # Build ONE judged rollup per ENTITY (event / account / segment) from the L0
    # dots, then recompute the events/relationship/gaps/icp space summaries as a
    # JUDGMENT OVER ALL of those rollups (not a salience-truncated top-50 pile).
    # Deterministic + fast (no per-entity LLM); idempotent (UNIQUE upsert).
    from . import rollups as _rollups
    rollup_build = _rollups.rebuild_all_rollups()
    l2 = rebuild_space_summaries_from_rollups()

    return {"written": written,
            "relationship_sync": rel_sync,
            "rollup_build": rollup_build,
            "l2_rewire": l2["rollup_counts"],
            "spaces": {s: get_summary(s) for s in SPACES}}
