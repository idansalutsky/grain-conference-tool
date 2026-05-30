"""Graph nodes — the steps/agents of the Grain Brain.

Each node takes the BrainState and returns a PARTIAL state dict that LangGraph
merges. Every node appends its own name to `trace` for observability.

Three subgraphs share these nodes:
  CAPTURE    classify → extract → resolve → arc → compress_capture → gate → memory_writer
  DISCOVERY  classify → read_context → search → propose → (interrupt) → gate → memory_writer
  QUERY      classify → query_node

Every LLM-using node has a deterministic fallback, so the whole graph runs
hermetically (no key, no network) — required for the tests.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from .. import arc, db, entity_resolution, llm, voice
from ..icp import IcpConfig
from . import spaces
from .state import BrainState

log = logging.getLogger("grain.brain.nodes")


def _push(state: BrainState, name: str) -> list[str]:
    return list(state.get("trace") or []) + [name]


# ---------------------------------------------------------------------------
# CLASSIFY — the router the product owner asked for.
# "Is this unstructured data to record, or an event to find — or a question?"
# ---------------------------------------------------------------------------
_VALID_KINDS = {"unstructured_capture", "discover_events", "query"}

_CLASSIFY_SYSTEM = (
    "You route a sales-rep message for Grain Finance into exactly one bucket. "
    "Reply with ONLY JSON {\"kind\": \"...\"}. The buckets:\n"
    "  unstructured_capture — a note about a PERSON the rep met/spoke to "
    "(meeting, voice memo, freeform). Record it into memory.\n"
    "  discover_events — a request to FIND new conferences/events to attend.\n"
    "  query — a QUESTION about what the brain already knows.\n"
    "Pick the single best bucket."
)

# Keyword fallback (deterministic, hermetic).
_DISCOVER_KEYS = (
    "find", "discover", "search for", "look for", "any events", "any conferences",
    "what conferences", "events in", "conferences in", "events we", "new events",
    "new conferences", "upcoming events", "upcoming conferences",
)
_QUERY_KEYS = (
    "what do we know", "who do we know", "what's in", "whats in", "show me",
    "summarize", "summarise", "tell me about", "how many", "which ", "what is our",
    "what are our", "?",
)
_CAPTURE_KEYS = (
    "met ", "spoke", "talked", "introduced", "ran into", "chatted",
    "cfo of", "head of", "wants a follow", "follow-up", "follow up",
    "warm", "interested", "their company", "at money", "at the booth",
)


def _keyword_classify(text: str) -> str:
    t = (text or "").lower().strip()
    if not t:
        return "query"
    discover_hit = any(k in t for k in _DISCOVER_KEYS)
    capture_hit = any(k in t for k in _CAPTURE_KEYS)
    # An imperative "find / discover / search for" is unambiguously discovery —
    # even when the sentence also says "...we don't already have" (which is the
    # exclusion clause, not a question about existing inventory).
    imperative_find = any(t.startswith(v) or f" {v}" in f" {t}"
                          for v in ("find ", "discover ", "search for ",
                                    "look for ", "surface "))
    if imperative_find and ("event" in t or "conference" in t):
        return "discover_events"
    # Discovery wins over a stray '?' only when it clearly asks to find events.
    if discover_hit and ("event" in t or "conference" in t or "find" in t
                         or "discover" in t):
        # ...unless it's a "what conferences do we already have" question with
        # no imperative-find verb (pure inventory question).
        if any(q in t for q in ("do we have", "do we know", "already have",
                                "what conferences do we", "which conferences")):
            return "query"
        return "discover_events"
    if capture_hit and not t.endswith("?"):
        return "unstructured_capture"
    if any(k in t for k in _QUERY_KEYS):
        return "query"
    # Default: a freeform statement (no question mark) about someone → capture.
    if not t.endswith("?"):
        return "unstructured_capture"
    return "query"


def classify_node(state: BrainState) -> BrainState:
    text = state.get("input_text") or ""
    kind = _keyword_classify(text)
    if llm.config.OPENROUTER_API_KEY:
        try:
            data = llm.chat_json(
                [{"role": "system", "content": _CLASSIFY_SYSTEM},
                 {"role": "user", "content": text}],
                temperature=0.0, max_tokens=50,
            )
            cand = (data.get("kind") or "").strip()
            if cand in _VALID_KINDS:
                kind = cand
        except llm.LLMError as exc:
            log.info("classify LLM fell back to keywords: %s", exc)
    return {"kind": kind, "trace": _push(state, "classify")}


# ---------------------------------------------------------------------------
# CAPTURE subgraph
# ---------------------------------------------------------------------------
def extract_node(state: BrainState) -> BrainState:
    """Structure the freeform note into a lead (reuses voice/llm.text_to_lead)."""
    text = state.get("input_text") or ""
    structured: dict[str, Any]
    try:
        structured = llm.text_to_lead(text)  # LLM path
    except llm.LLMError:
        structured = _fallback_extract(text)  # hermetic fallback
    cand = {
        "kind": "capture",
        "structured": structured,
        "raw_input": text,
    }
    return {"candidates": [cand], "trace": _push(state, "extract")}


def _fallback_extract(text: str) -> dict:
    """Deterministic, key-free extraction good enough for routing + memory.

    Pulls a name after "CFO of"/"met"/"head of ...of", a company, a rough
    sentiment, and meeting intent — entirely from the text."""
    t = text or ""
    low = t.lower()
    company = None
    m = re.search(r"\b(?:of|at|from)\s+([A-Z][A-Za-z0-9&.\- ]{1,40})", t)
    if m:
        company = m.group(1).strip().rstrip(".,")
        # trim trailing connective words
        company = re.split(r"\b(?:at|wants|who|and|the|in|on)\b", company)[0].strip()
    title = None
    tm = re.search(r"\b(cfo|chief financial officer|treasurer|head of [a-z ]+|"
                   r"vp [a-z ]+|director of [a-z ]+|ceo|cto)\b", low)
    if tm:
        title = tm.group(1)
    meeting = any(k in low for k in ("follow-up", "follow up", "meeting",
                                     "wants to meet", "wants a follow"))
    warm = any(k in low for k in ("warm", "interested", "keen", "excited",
                                  "loved", "great chat"))
    cold = any(k in low for k in ("not interested", "cold", "dismissive",
                                  "lukewarm", "no need"))
    sentiment = 4 if warm else (2 if cold else 3)
    signals = []
    if meeting:
        signals.append("wants_meeting")
    if warm:
        signals.append("strong_fit_signal")
    if "pain" in low or "struggling" in low or "problem" in low:
        signals.append("explicit_pain")
    return {
        "name": None,
        "company": company,
        "title": title,
        "vertical": "unknown",
        "what_discussed": t[:200],
        "soft_signals": signals,
        "sentiment": sentiment,
        "meeting_requested": meeting,
        "transcript": t,
    }


def resolve_node(state: BrainState) -> BrainState:
    """Entity-resolve the extracted person against existing contacts.

    Uses entity_resolution.resolve_encounter directly (no DB write) so the brain
    stays a read-mostly analysis layer — the live capture pipeline (voice.py)
    owns encounter persistence."""
    cands = state.get("candidates") or []
    if not cands:
        return {"trace": _push(state, "resolve")}
    struct = cands[0].get("structured") or {}
    match = entity_resolution.resolve_encounter(struct)
    resolution = (
        {"decision": "no_existing_contacts", "contact_id": None}
        if match is None else
        {"decision": match.decision_hint, "contact_id": match.contact_id,
         "confidence": match.confidence, "factors": match.factors}
    )
    new_cands = [{**cands[0], "resolution": resolution}] + cands[1:]
    return {"candidates": new_cands, "trace": _push(state, "resolve")}


def arc_node(state: BrainState) -> BrainState:
    """Attach an arc verdict.

    If the person resolved to an existing contact we classify their real history;
    otherwise we emit a deterministic single-touch verdict from the current note
    (a brand-new warm meeting → "warming")."""
    cands = state.get("candidates") or []
    if not cands:
        return {"trace": _push(state, "arc")}
    c = cands[0]
    resolution = c.get("resolution") or {}
    contact_id = resolution.get("contact_id")
    verdict: dict
    if contact_id and resolution.get("decision") in {"auto_merge", "auto_merged"}:
        try:
            v = arc.classify(contact_id, use_llm=True)
            verdict = {"kind": v.kind, "confidence": v.confidence,
                       "summary": v.summary}
        except Exception as exc:  # noqa: BLE001
            log.info("arc classify fell back: %s", exc)
            verdict = _single_touch_arc(c)
    else:
        verdict = _single_touch_arc(c)
    new_cands = [{**c, "arc": verdict}] + cands[1:]
    return {"candidates": new_cands, "trace": _push(state, "arc")}


def _single_touch_arc(cand: dict) -> dict:
    s = cand.get("structured") or {}
    sentiment = int(s.get("sentiment") or 3)
    meeting = bool(s.get("meeting_requested"))
    if sentiment >= 4 and meeting:
        return {"kind": "warming", "confidence": 0.6,
                "summary": "first touch: warm + meeting requested"}
    if sentiment <= 2:
        return {"kind": "cooling", "confidence": 0.5,
                "summary": "first touch: lukewarm/cold"}
    return {"kind": "flat", "confidence": 0.45,
            "summary": "first touch: no clear direction yet"}


def compress_capture_node(state: BrainState) -> BrainState:
    """Distill the capture into ONE salient relationship insight (compressed)."""
    cands = state.get("candidates") or []
    if not cands:
        return {"trace": _push(state, "compress_capture")}
    c = cands[0]
    s = c.get("structured") or {}
    arc_v = c.get("arc") or {}
    name = s.get("name") or "Unknown contact"
    company = s.get("company") or "unknown company"
    title = s.get("title") or "unknown role"
    discussed = (s.get("what_discussed") or "").strip()
    insight = (
        f"{name} ({title} @ {company}) - {arc_v.get('kind', 'flat')}. "
        f"{discussed[:160]}"
    ).strip()
    item_key = _capture_item_key(name, company)
    salience = 0.5
    if arc_v.get("kind") == "warming":
        salience = 0.8
    elif arc_v.get("kind") == "cooling":
        salience = 0.4
    compressed = {
        "insight": insight,
        "name": name, "company": company, "title": title,
        "arc": arc_v.get("kind"),
        "sentiment": s.get("sentiment"),
        "meeting_requested": bool(s.get("meeting_requested")),
        "soft_signals": s.get("soft_signals") or [],
        "item_key": item_key,
        "salience": salience,
    }
    new_cands = [{**c, "compressed": compressed}] + cands[1:]
    return {"candidates": new_cands, "trace": _push(state, "compress_capture")}


def _capture_item_key(name: str, company: str) -> str:
    base = f"{(name or 'unknown')}|{(company or 'unknown')}".lower()
    base = re.sub(r"[^a-z0-9|]+", "_", base).strip("_")
    return base or ("contact_" + uuid.uuid4().hex[:8])


# ---------------------------------------------------------------------------
# DISCOVERY subgraph
# ---------------------------------------------------------------------------
def read_context_node(state: BrainState) -> BrainState:
    """Pull ICP summary + gaps + the known-events exclusion set."""
    icp_summary = spaces.get_summary("icp") or {}
    gaps_items = spaces.read_items("gaps", limit=5)
    gaps = gaps_items[0]["content"] if gaps_items else {}
    # Known event signatures from the events space (seeded) + live DB.
    known_sigs: set[str] = set()
    for it in spaces.read_items("events", limit=20):
        sigs = (it.get("content") or {}).get("signatures")
        if isinstance(sigs, list):
            known_sigs.update(sigs)
    known_sigs.update(_db_known_signatures())
    ctx = {
        "icp_summary": icp_summary.get("summary"),
        "gaps": gaps,
        "known_signatures": sorted(known_sigs),
    }
    return {"result": {"context": ctx}, "trace": _push(state, "read_context")}


def _db_known_signatures() -> set[str]:
    conn = db.get_conn()
    try:
        try:
            names = [r["name"] for r in conn.execute(
                "SELECT name FROM conferences").fetchall()]
        except Exception:
            names = []
    finally:
        conn.close()
    return {spaces._known_event_signature(n) for n in names if n}


_MAX_PROPOSALS = 6


def search_node(state: BrainState) -> BrainState:
    """Propose REAL, specifically-named conferences that fill the gap.

    Primary path: ask the funded OpenRouter LLM for real events it is confident
    exist that match the gap (region + vertical), EXCLUDING the known-events
    exclusion set assembled by read_context. The model returns strict JSON; we
    parse robustly, dedupe by name, cap at ~6, and guarantee every proposal has
    a region. Each LLM-proposed event is marked with provenance
    "llm-proposed (verify)" so the gate sends it to review (rather than
    auto-accept) unless it carries a strong source.

    Hermetic fallback (no key, for tests): we do NOT fabricate realistic-looking
    events. Instead we return a single, clearly-labelled placeholder asking the
    operator to configure a search key — the gate routes it to review.
    """
    text = state.get("input_text") or ""
    region_hint = _region_hint_from_text(text) or _region_hint_from_gaps(state)
    focus_vertical = _focus_vertical(state, text)
    known_names = _known_event_names(state)

    proposals: list[dict] = []
    if llm.config.OPENROUTER_API_KEY:
        try:
            proposals = _llm_propose_events(
                region_hint=region_hint,
                focus_vertical=focus_vertical,
                known_names=known_names,
                user_request=text,
            )
        except Exception as exc:  # noqa: BLE001
            log.info("LLM event discovery fell back to placeholder: %s", exc)

    proposals = _dedupe_and_finalize(proposals, region_hint)
    if not proposals:
        proposals = _placeholder_proposals(region_hint, focus_vertical)
    return {"proposals": proposals, "trace": _push(state, "search")}


_DISCOVERY_SYSTEM = (
    "You are a sales-ops analyst for Grain Finance, a fintech selling embedded "
    "cross-currency FX hedging to payment service providers, travel platforms, "
    "cross-border payment companies, and corporate treasury teams. Your job is "
    "to surface REAL, currently-operating conferences/events that a Grain sales "
    "rep should consider attending to reach that ICP.\n"
    "STRICT RULES:\n"
    " - Only list events you are CONFIDENT genuinely exist (a real recurring "
    "series or a well-known event). Never invent a name. If unsure, omit it.\n"
    " - Do NOT return placeholder, generic, or templated names.\n"
    " - Never return two events with the same name.\n"
    " - Exclude any event in the provided EXCLUDE list (already known to us).\n"
    " - For source_url, give the event's real/official website (your best known "
    "official URL). If you do not know an official URL, use null.\n"
    "Reply with ONLY a JSON object of the form:\n"
    '{"proposals": [{"name": str, "city": str, "country": str, '
    '"region": "LATAM|EU|NA|APAC|MEA", '
    '"start_date": "YYYY-MM-DD or YYYY-MM", '
    '"vertical": "payments|treasury|travel|cross_border_payments|fintech_other|crypto|marketplace", '
    '"why_relevant": str, "estimated_attendance": int or null, '
    '"source_url": str or null}]}.'
)


def _llm_propose_events(*, region_hint: str | None, focus_vertical: str,
                        known_names: list[str], user_request: str) -> list[dict]:
    """Ask the LLM for real, specifically-named events filling the gap."""
    region_clause = (f"in the {region_hint} region" if region_hint
                     else "globally")
    exclude_clause = (
        "EXCLUDE (already known — do NOT propose any of these): "
        + "; ".join(known_names[:60])
        if known_names else "We currently track no events; nothing to exclude."
    )
    user = (
        f"The sales rep asked: \"{user_request.strip()}\".\n"
        f"Propose up to {_MAX_PROPOSALS} REAL conferences {region_clause} most "
        f"relevant to Grain's ICP, prioritising the '{focus_vertical}' theme "
        "(treasury/finance leaders, heads of payments, cross-border / "
        "FX-exposed executives).\n"
        f"{exclude_clause}\n"
        "Return only events you are confident actually exist, as strict JSON. "
        "Always populate region. Output only the JSON object."
    )
    data = llm.chat_json(
        [{"role": "system", "content": _DISCOVERY_SYSTEM},
         {"role": "user", "content": user}],
        temperature=0.2, max_tokens=1200,
    )
    raw = data.get("proposals")
    if not isinstance(raw, list):
        return []
    known_sigs = {spaces._known_event_signature(n) for n in known_names}
    out: list[dict] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        # Honour the exclusion set even if the model ignored it.
        if spaces._known_event_signature(name) in known_sigs:
            continue
        src = p.get("source_url")
        src = src.strip() if isinstance(src, str) and src.strip() and \
            src.strip().lower() not in ("null", "none") else None
        out.append({
            "name": name,
            "city": (p.get("city") or "").strip() or None,
            "country": (p.get("country") or "").strip() or None,
            "region": _normalize_region(p.get("region"), region_hint),
            "start_date": (p.get("start_date") or None),
            "vertical": (p.get("vertical") or focus_vertical or "finance"),
            "why_relevant": (p.get("why_relevant") or "").strip()
            or f"Reaches {focus_vertical} buyers in "
               f"{region_hint or 'the target region'}.",
            "estimated_attendance": _as_int(p.get("estimated_attendance")),
            "source_url": src,
            # Provenance flag so the gate treats these as "verify" rather than
            # blindly trusting the model. A real official source_url still lets
            # the gate accept; otherwise it routes to human review.
            "provenance": "llm-proposed (verify)",
        })
    return out


def _dedupe_and_finalize(proposals: list[dict],
                         region_hint: str | None) -> list[dict]:
    """Dedupe by name-signature, guarantee a region, drop past-dated events,
    cap at _MAX_PROPOSALS."""
    seen: set[str] = set()
    out: list[dict] = []
    for p in proposals:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        # DEFECT 4 — recency guard: never propose an event that already happened.
        if not _is_future_or_today(p.get("start_date")):
            log.info("discovery: dropped past-dated proposal %r (%s)",
                     name, p.get("start_date"))
            continue
        sig = spaces._known_event_signature(name)
        if sig in seen:
            continue
        seen.add(sig)
        p = dict(p)
        p["region"] = _normalize_region(p.get("region"), region_hint)
        out.append(p)
        if len(out) >= _MAX_PROPOSALS:
            break
    return out


def _is_future_or_today(start_date: Any) -> bool:
    """True if start_date is today-or-future (or unknown). Accepts 'YYYY-MM-DD',
    'YYYY-MM', or 'YYYY'. Unknown/unparseable dates pass (the gate still routes
    them to review) — we only DROP events we can prove are in the past."""
    import calendar
    import datetime as _dt
    if not start_date:
        return True
    s = str(start_date).strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        return True  # can't parse → don't drop
    year = int(m.group(1))
    # When month/day are absent, be lenient (treat as END of the period) so we
    # only drop events we can prove are fully in the past, never a current-month
    # event given as YYYY-MM.
    month = int(m.group(2)) if m.group(2) else 12
    month = min(max(month, 1), 12)
    last_dom = calendar.monthrange(year, month)[1]
    day = int(m.group(3)) if m.group(3) else last_dom
    day = min(max(day, 1), last_dom)
    try:
        ev = _dt.date(year, month, day)
    except ValueError:
        return True
    return ev >= _dt.date.today()


def _placeholder_proposals(region_hint: str | None,
                           focus_vertical: str) -> list[dict]:
    """Hermetic, KEY-FREE fallback. NOT a realistic event — a single, clearly
    labelled prompt to configure a search key. The gate routes it to review
    (no real source_url), and a human approval can still lift it for the
    interrupt→resume contract."""
    region = _normalize_region(region_hint, region_hint)
    return [{
        "name": "sample - configure a search key to discover real events",
        "city": None,
        "country": None,
        "region": region,
        "start_date": None,
        "vertical": focus_vertical or "finance",
        "why_relevant": "No AI search key is configured, so no real events "
                        "could be discovered. Set OPENROUTER_API_KEY to get "
                        f"real {focus_vertical} events in {region}.",
        "estimated_attendance": None,
        "source_url": None,
        "provenance": "placeholder (no search key)",
    }]


def _focus_vertical(state: BrainState, text: str) -> str:
    low = (text or "").lower()
    for kw in ("treasury", "payments", "travel", "crypto", "marketplace"):
        if kw in low:
            return kw
    ctx = (state.get("result") or {}).get("context") or {}
    thin = (ctx.get("gaps") or {}).get("thin_verticals") or []
    return thin[0] if thin else "treasury"


def _known_event_names(state: BrainState) -> list[str]:
    """Known event NAMES for the exclusion set.

    read_context stores name-SIGNATURES (lowercased, year-stripped); those are
    enough to instruct the model what to avoid. We also fold in any real names
    we can read from the DB so the prompt is concrete."""
    ctx = (state.get("result") or {}).get("context") or {}
    names: list[str] = []
    seen: set[str] = set()
    for n in (ctx.get("known_signatures") or []):
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return names


def _normalize_region(value: Any, fallback: str | None) -> str:
    """Map a model/region string to a canonical region; never return null."""
    if isinstance(value, str) and value.strip():
        v = value.strip()
        up = v.upper()
        if up in ("LATAM", "EU", "NA", "APAC", "MEA"):
            return up
        mapped = _region_hint_from_text(v)
        if mapped:
            return mapped
    if fallback:
        fb = _region_hint_from_text(fallback) or fallback
        if isinstance(fb, str) and fb.strip():
            return fb.strip().upper() if len(fb) <= 5 else fb.strip()
    return "Global"


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


_REGIONS = {
    "latam": "LATAM", "latin america": "LATAM",
    "apac": "APAC", "asia": "APAC", "asia pacific": "APAC",
    "emea": "MEA", "mea": "MEA", "middle east": "MEA", "africa": "MEA",
    "europe": "EU", "eu ": "EU",
    "north america": "NA", "usa": "NA", "us ": "NA",
}


def _region_hint_from_text(text: str) -> str | None:
    low = (text or "").lower()
    for k, v in _REGIONS.items():
        if k in low:
            return v
    return None


def _region_hint_from_gaps(state: BrainState) -> str | None:
    ctx = (state.get("result") or {}).get("context") or {}
    thin = (ctx.get("gaps") or {}).get("thin_regions") or []
    return thin[0] if thin else None


def propose_node(state: BrainState) -> BrainState:
    """Assemble candidate events with stable proposal ids (for approval).

    Enforces the downstream gate contract: every assembled proposal carries
    id, name, city, country, region (never null), start_date, vertical,
    why_relevant, estimated_attendance, source_url, and a provenance flag.
    De-dupes one final time by name-signature."""
    proposals = state.get("proposals") or []
    assembled: list[dict] = []
    seen: set[str] = set()
    for p in proposals:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        sig = spaces._known_event_signature(p.get("name") or "")
        if sig in seen:
            continue
        seen.add(sig)
        pid = p.get("proposal_id") or p.get("id") or ("prop_" + uuid.uuid4().hex[:10])
        assembled.append({
            "name": p.get("name"),
            "city": p.get("city"),
            "country": p.get("country"),
            "region": _normalize_region(p.get("region"), None),
            "start_date": p.get("start_date"),
            "vertical": p.get("vertical") or "finance",
            "why_relevant": p.get("why_relevant"),
            "estimated_attendance": p.get("estimated_attendance"),
            "source_url": p.get("source_url"),
            "provenance": p.get("provenance") or "llm-proposed (verify)",
            "id": pid,
            "proposal_id": pid,
        })
    return {"proposals": assembled, "trace": _push(state, "propose")}


# ---------------------------------------------------------------------------
# GATE — THE FILTER. Validate / ICP-fit / NEW. Most important node.
# ---------------------------------------------------------------------------
def gate_node(state: BrainState) -> BrainState:
    """For each candidate, decide accept | review | reject with a reason.

    Three checks, applied to BOTH capture insights and discovered events:
      1. REAL    — has substance / a source. (Discovered events need a source_url
                   or citation; captures need at least a company or insight.)
      2. ICP-FIT — reject Grain competitors and clearly off-ICP verticals.
      3. NEW     — reject events whose name-signature is already known.
    """
    kind = state.get("kind")
    icp = IcpConfig.default()
    competitors = {c.lower() for c in icp.competitors}
    icp_verticals = set(icp.company_level["verticals"])

    decisions: list[dict] = []
    accepted: list[dict] = []

    if kind == "discover_events":
        # Only gate proposals the human APPROVED (approvals supplied on resume).
        approvals = {a.get("id"): bool(a.get("approved"))
                     for a in (state.get("approvals") or [])}
        ctx = (state.get("result") or {}).get("context") or {}
        known_sigs = set(ctx.get("known_signatures") or [])
        for p in state.get("proposals") or []:
            pid = p.get("id") or p.get("proposal_id")
            d = _gate_event(p, competitors, icp_verticals, known_sigs)
            # Human override: an explicit reject from the human always wins.
            if pid in approvals and approvals[pid] is False:
                d = {"decision": "reject", "reason": "rejected by human reviewer"}
            elif pid in approvals and approvals[pid] is True and d["decision"] == "review":
                # Human approval lifts a borderline 'review' to accept.
                d = {"decision": "accept",
                     "reason": "human-approved (was: " + d["reason"] + ")"}
            entry = {"id": pid, "candidate": p, **d}
            decisions.append(entry)
            if d["decision"] == "accept":
                accepted.append(entry)
    else:  # capture
        for c in state.get("candidates") or []:
            comp = c.get("compressed") or {}
            d = _gate_capture(comp, competitors)
            entry = {"id": comp.get("item_key"), "candidate": c, **d}
            decisions.append(entry)
            if d["decision"] == "accept":
                accepted.append(entry)

    return {
        "gate_decisions": decisions,
        "result": {**(state.get("result") or {}), "accepted_count": len(accepted)},
        "trace": _push(state, "gate"),
    }


def _gate_event(p: dict, competitors: set[str], icp_verticals: set[str],
                known_sigs: set[str]) -> dict:
    name = (p.get("name") or "").strip()
    if not name:
        return {"decision": "reject", "reason": "no name"}
    # 1. REAL — needs a source.
    if not (p.get("source_url") or p.get("citations")):
        return {"decision": "review", "reason": "no source/citation - verify before trusting"}
    # 2. ICP-FIT — reject competitor-branded events.
    low = name.lower()
    for comp in competitors:
        if comp and comp in low:
            return {"decision": "reject",
                    "reason": f"competitor event ({comp}) - off-ICP by definition"}
    vertical = (p.get("vertical") or "").lower()
    if vertical and vertical not in icp_verticals and vertical != "unknown":
        return {"decision": "review",
                "reason": f"vertical '{vertical}' not in ICP target set"}
    # 3. NEW — reject if we already know this event.
    sig = spaces._known_event_signature(name)
    if sig in known_sigs:
        return {"decision": "reject", "reason": "already known - not new"}
    return {"decision": "accept",
            "reason": f"real, ICP-fit ({vertical or 'finance'}), and new"}


def _gate_capture(comp: dict, competitors: set[str]) -> dict:
    company = (comp.get("company") or "").lower()
    # ICP-FIT — reject if the person works for a Grain COMPETITOR.
    for c in competitors:
        if c and c in company:
            return {"decision": "reject",
                    "reason": f"contact works at a competitor ({c}) - do not record as a target"}
    # REAL — needs at least an insight or a company.
    if not (comp.get("insight") or comp.get("company")):
        return {"decision": "review", "reason": "too thin to record"}
    return {"decision": "accept", "reason": "ICP-relevant relationship insight"}


# ---------------------------------------------------------------------------
# MEMORY WRITER — only accepted items reach long-term memory.
# ---------------------------------------------------------------------------
def memory_writer_node(state: BrainState) -> BrainState:
    kind = state.get("kind")
    writes: list[dict] = []
    for d in state.get("gate_decisions") or []:
        if d.get("decision") != "accept":
            continue
        if kind == "discover_events":
            p = d.get("candidate") or {}
            # Never let the no-key placeholder ("sample - configure a search
            # key…") become a real conference or pollute the events space, even
            # if a human clicks approve on it — it's a notice, not an event.
            _name = (p.get("name") or "").strip().lower()
            if "placeholder" in (p.get("provenance") or "").lower() \
                    or _name.startswith("sample - configure"):
                continue
            # The Events Brain CREATES the event: an approved discovery becomes a
            # real, scored conference you can plan around — not a dead-end memory
            # entry. Reuses the same creator as the Discovery page (dedup-safe).
            conference_id = None
            try:
                from .. import discovery as _discovery
                res = _discovery.create_conference_from_payload(
                    p, decided_by="brain", source="events_brain")
                conference_id = res.get("conference_id")
            except Exception as exc:  # noqa: BLE001 — never let creation break the run
                log.info("brain discovery -> conference creation failed: %s", exc)
            item_key = spaces._known_event_signature(p.get("name") or "") \
                or (d.get("id") or uuid.uuid4().hex[:8])
            w = spaces.write_item(
                "events", item_key,
                {"summary": p.get("why_relevant") or p.get("name"),
                 "name": p.get("name"), "city": p.get("city"),
                 "country": p.get("country"), "region": p.get("region"),
                 "start_date": p.get("start_date"), "vertical": p.get("vertical"),
                 "estimated_attendance": p.get("estimated_attendance"),
                 "source_url": p.get("source_url"),
                 "why_relevant": p.get("why_relevant"),
                 "conference_id": conference_id},
                provenance=f"discovery:{p.get('source_url') or 'agent'}",
                salience=0.7,
            )
            writes.append({"space": "events", "conference_id": conference_id, **w})
        else:  # capture → relationship (+ a playbook signal)
            c = d.get("candidate") or {}
            comp = c.get("compressed") or {}
            w = spaces.write_item(
                "relationship", comp.get("item_key") or uuid.uuid4().hex[:8],
                {"summary": comp.get("insight"), **comp},
                provenance="capture:brain",
                salience=float(comp.get("salience") or 0.5),
            )
            writes.append({"space": "relationship", **w})
            # If the capture is a strong warming signal, log a playbook note.
            if comp.get("arc") == "warming" and comp.get("meeting_requested"):
                pw = spaces.write_item(
                    "playbook",
                    "win_" + (comp.get("item_key") or uuid.uuid4().hex[:6]),
                    {"summary": f"Worked: {comp.get('title') or 'buyer'} at "
                                f"{comp.get('company')} → meeting after warm chat.",
                     "signals": comp.get("soft_signals")},
                    provenance="capture:brain", salience=0.55,
                )
                writes.append({"space": "playbook", **pw})

    spaces_touched = sorted({w["space"] for w in writes})
    summaries = {s: spaces.get_summary(s) for s in spaces_touched}
    return {
        "writes": writes,
        "result": {**(state.get("result") or {}),
                   "writes": writes,
                   "updated_summaries": summaries},
        "trace": _push(state, "memory_writer"),
    }


# ---------------------------------------------------------------------------
# QUERY — read spaces + answer.
# ---------------------------------------------------------------------------
_QUERY_SYSTEM = (
    "You answer a sales-team question using ONLY the provided memory-space "
    "summaries for Grain Finance. Be concise (2-4 sentences). If the answer "
    "isn't in the summaries, say so. Reply with ONLY JSON {\"answer\": \"...\"}."
)


def query_node(state: BrainState) -> BrainState:
    question = state.get("input_text") or ""
    all_spaces = spaces.list_spaces()
    context = {s["name"]: s["summary"] for s in all_spaces}
    # Read the relevant L1 rollups so the answer can CONNECT DOTS across entities
    # (specific accounts/events) and cite them — not just the 5 space summaries.
    rollup_lines = _relevant_rollup_lines(question)
    answer = _deterministic_answer(question, all_spaces, rollup_lines)
    if llm.config.OPENROUTER_API_KEY:
        try:
            rollup_block = ("\n\nRelevant entity rollups (L1):\n"
                            + "\n".join(f"- {ln}" for ln in rollup_lines)
                            if rollup_lines else "")
            data = llm.chat_json(
                [{"role": "system", "content": _QUERY_SYSTEM},
                 {"role": "user", "content":
                  f"Question: {question}\n\nMemory spaces:\n"
                  + "\n".join(f"- {k}: {v}" for k, v in context.items())
                  + rollup_block}],
                temperature=0.2, max_tokens=400,
            )
            cand = (data.get("answer") or "").strip()
            if cand:
                answer = cand
        except llm.LLMError as exc:
            log.info("query LLM fell back to deterministic: %s", exc)
    return {
        "result": {"answer": answer, "spaces": all_spaces, "question": question,
                   "rollups": rollup_lines},
        "trace": _push(state, "query"),
    }


def _relevant_rollup_lines(question: str, limit: int = 8) -> list[str]:
    """Pull the most relevant L1 rollups for the question so the query can cite
    specific entities (accounts/events/segments) — the dots connected by L1.

    Chooses the scope by keyword; defaults to the top-priority accounts (the
    relationship view is the most common 'who/what do we know' question)."""
    from . import rollups
    q = (question or "").lower()
    if any(k in q for k in ("event", "conference", "attend", "booth")):
        scope = "event"
    elif any(k in q for k in ("segment", "vertical", "gap", "coverage")):
        scope = "segment"
    else:
        scope = "account"
    out: list[str] = []
    for r in rollups.list_rollups(scope, limit=limit, sort="priority"):
        out.append(f"{r.get('title')}: {r.get('summary')}")
    return out


def _deterministic_answer(question: str, all_spaces: list[dict],
                          rollup_lines: list[str] | None = None) -> str:
    """Key-free answer: surface the most relevant space summary(ies), then cite
    the top L1 rollups so the answer connects dots to specific entities."""
    q = (question or "").lower()
    rollup_tail = ""
    if rollup_lines:
        rollup_tail = " Specifically: " + " | ".join(rollup_lines[:4])
    keyword_space = {
        "icp": "icp", "ideal customer": "icp", "buyer": "icp",
        "competitor": "icp", "vertical": "icp",
        "event": "events", "conference": "events",
        "gap": "gaps", "under-cover": "gaps", "thin": "gaps",
        "playbook": "playbook", "works": "playbook", "outreach": "playbook",
        "relationship": "relationship", "contact": "relationship",
        "warm": "relationship", "met": "relationship",
    }
    target = None
    for kw, sp in keyword_space.items():
        if kw in q:
            target = sp
            break
    by_name = {s["name"]: s for s in all_spaces}
    if target and by_name.get(target, {}).get("summary"):
        s = by_name[target]
        return f"[{s['name']}] {s['summary']}{rollup_tail}"
    # No keyword hit → return the non-empty summaries joined.
    parts = [f"[{s['name']}] {s['summary']}" for s in all_spaces
             if s.get("summary") and s.get("item_count")]
    base = " ".join(parts) if parts else "The brain has no memory yet."
    return base + rollup_tail
