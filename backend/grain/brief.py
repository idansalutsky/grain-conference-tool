"""Approach brief — the AI dossier a rep reads on the way to the booth.

Combines:
  1. Web-search-grounded recent news about the target's company (Perplexity Sonar)
  2. The FX angle tailored to that company's vertical
  3. 4-6 bullet talk track grounded in Grain's value prop
  4. 4-6 line follow-up email draft

The brief is cached in the `briefs` table keyed by (contact_id OR person_id,
conference_id) so the demo never hangs on a live web call.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Optional

from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.brief")


GRAIN_SYSTEM = (
    "Grain (grainfinance.com) is a Tel Aviv fintech (Series A, $33M led by "
    "Bain Capital Ventures) building embedded cross-currency FX hedging for "
    "software platforms and marketplaces. Grain's customers are typically "
    "CFO / Treasurer / VP Finance / Head of Payments at travel platforms, "
    "OTAs, PSPs, marketplaces, and cross-border payment companies — "
    "businesses with heavy multi-currency volume. Grain eliminates FX risk "
    "and frictional spread via API-embedded hedging.\n\n"
    "You are writing for a Grain account executive who is about to walk up "
    "to this person at a conference. Be specific, concrete, and avoid "
    "generic fintech filler. Cite real, recent news when available."
)


_VERTICAL_FX_ANGLES = {
    "travel": "Travel platforms book in dozens of currencies but settle in 1-2; the spread on hotel/flight inventory eats margin. Grain hedges per-booking FX exposure programmatically.",
    "booking": "Booking-flow FX (consumer paid in currency A, supplier paid in currency B, days apart) is exactly Grain's wedge — match the hedge to the booking horizon.",
    "psp": "PSPs touching cross-border volume can offer embedded FX to merchants as a paid value-add — Grain provides the rails without owning treasury infrastructure.",
    "payments": "Same — embedded FX as a paid value-add for merchants.",
    "marketplace": "Two-sided marketplaces with EM-currency sellers and USD/EUR buyers sit on multi-day FX exposure between escrow and payout. Grain hedges that float.",
    "cross_border_payments": "Direct competitor adjacency — Grain's embedded model is a build-vs-buy conversation for cross-border platforms looking to monetize FX without a hedging desk.",
    "treasury": "Treasury teams at multi-market companies juggle exposure manually; Grain automates the hedging layer.",
    "crypto": "Crypto-fiat off-ramps and stablecoin payouts still carry FX on the fiat leg; Grain hedges that without the platform needing FX licensing.",
}


def generate(
    *,
    name: str,
    company: str,
    title: Optional[str] = None,
    vertical: Optional[str] = None,
    conference_id: Optional[str] = None,
    contact_id: Optional[str] = None,
    person_id: Optional[str] = None,
    use_web_search: bool = True,
    persist: bool = True,
) -> dict:
    """Produce + (optionally) persist an approach brief."""
    icp = IcpConfig.default()
    vertical_l = (vertical or "").lower() or None

    # Step 1: grounded search for trigger news
    search_text, citations = "", []
    if use_web_search and company:
        search_text, citations = _grounded_search(company, vertical_l, icp)

    # Step 2: synthesize structured brief JSON
    bj = _synthesize(
        name=name, company=company, title=title,
        vertical=vertical_l, icp=icp,
        search_text=search_text,
        citation_urls=[c["url"] for c in citations],
    )

    # Step 3: render markdown for display
    text = _render_markdown(name, company, title, vertical_l, bj)

    brief_id: Optional[str] = None
    if persist:
        brief_id = "brf_" + uuid.uuid4().hex[:14]
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO briefs (id, contact_id, conference_id, person_id, "
                "brief_text, brief_json, generated_at) VALUES (?,?,?,?,?,?,?)",
                (brief_id, contact_id, conference_id, person_id, text,
                 json.dumps(bj, ensure_ascii=False), db.now_iso()),
            )
        finally:
            conn.close()

    return {"brief_id": brief_id, "brief_text": text, "brief_json": bj}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _grounded_search(company: str, vertical: Optional[str],
                     icp: IcpConfig) -> tuple[str, list[dict]]:
    angle = "FX exposure, cross-border revenue, multi-market expansion, treasury hires, new payment corridors"
    if vertical in ("travel", "booking"):
        angle += ", new destinations, inventory in new currencies"
    elif vertical == "marketplace":
        angle += ", new seller-country onboarding, cross-border GMV"
    elif vertical in ("psp", "cross_border_payments", "payments"):
        angle += ", new payout currencies, licensing wins, treasury hires"

    query = (
        f"Find recent (last 12 months) news about \"{company}\" relevant to: {angle}. "
        "For each item, give headline, date, and one sentence on why it matters "
        "for cross-currency FX hedging. Prefer primary sources (company blog, "
        "Reuters, Bloomberg, PYMNTS, Finextra, Skift). If nothing notable, say so."
    )
    system = (
        "You are a research analyst feeding a Grain Finance account executive. "
        "Grain sells embedded cross-currency FX hedging to platforms with "
        "heavy cross-border transaction volume."
    )
    try:
        text, citations = llm.search_grounded(query, system=system)
    except llm.LLMError as exc:
        log.warning("grounded search failed for %s: %s", company, exc)
        return "", []
    # Extract any inline URLs the citation field missed
    seen = {c["url"] for c in citations}
    for u in _URL_RE.findall(text):
        u = u.rstrip(".,;)]")
        if u not in seen:
            citations.append({"url": u, "title": ""})
            seen.add(u)
    return text, citations


def _synthesize(*, name: str, company: str, title: Optional[str],
                vertical: Optional[str], icp: IcpConfig,
                search_text: str, citation_urls: list[str]) -> dict:
    competitors = ", ".join(icp.competitors[:6])
    fallback_angle = _VERTICAL_FX_ANGLES.get(vertical or "", "")
    targets = ", ".join(icp.person_level["target_titles"][:6])

    user = f"""Target person: {name}
Title: {title or '?'}
Company: {company}
Vertical (if known): {vertical or 'unknown — infer'}

Grain known competitors (mention only if the company has clearly chosen one): {competitors}
Grain's target titles: {targets}

--- Recent web-grounded news about {company} ---
{search_text or '(no grounded results — use what you know first-principles)'}

--- Task ---
Produce a JSON object with EXACTLY these keys:

1. "fx_angle" (2-3 sentences): why Grain matters specifically to {company}. Tie to currency corridors, multi-market revenue, FX spread leakage, or treasury complexity. If you don't know the company, write "Limited public info on {company}'s FX footprint; rep should ask:" + 2 discovery questions.
{('Fallback angle for this vertical: ' + fallback_angle) if fallback_angle else ''}

2. "trigger_news" (array, 0-4 items): only with real URLs from the search above. Each: {{"headline", "url", "date_iso": "YYYY-MM-DD or YYYY-MM", "why_relevant"}}. Empty array if nothing — do NOT invent.

3. "talk_track" (array of 4-6 strings): conversational openers/pivots the rep can actually say. Each one short sentence. Reference the news or the FX angle.

4. "follow_up_draft" (string, 4-6 lines): casual but specific email body (no subject, no signature). Reference the conversation, name a concrete thing about {company}, propose a clear next step.

5. "sources" (array of URLs): every URL from trigger_news + any other URL you cited in fx_angle.

Return JSON ONLY. No markdown fences. No prose outside JSON."""

    messages = [
        {"role": "system", "content": GRAIN_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.3, max_tokens=2048)
    except llm.LLMError as exc:
        log.warning("brief synthesis failed for %s (%s) — fallback", company, exc)
        data = _fallback_brief(name, company, title, vertical, fallback_angle)
    return _normalize(data, citation_urls)


def _normalize(data: dict, fallback_sources: list[str]) -> dict:
    fx_angle = (data.get("fx_angle") or "").strip()
    tn_raw = data.get("trigger_news") or []
    if not isinstance(tn_raw, list):
        tn_raw = []
    trigger_news = []
    for it in tn_raw:
        if not isinstance(it, dict):
            continue
        url = (it.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        trigger_news.append({
            "headline": (it.get("headline") or "").strip(),
            "url": url,
            "date_iso": (it.get("date_iso") or it.get("date") or "").strip(),
            "why_relevant": (it.get("why_relevant") or it.get("why") or "").strip(),
        })
    tt_raw = data.get("talk_track") or []
    if isinstance(tt_raw, str):
        tt_raw = [tt_raw]
    talk_track = [str(x).strip() for x in tt_raw if str(x).strip()]
    follow_up = (data.get("follow_up_draft") or "").strip()
    srcs = data.get("sources") or []
    if not isinstance(srcs, list):
        srcs = []
    sources = [s for s in srcs if isinstance(s, str) and s.startswith("http")]
    for u in fallback_sources:
        if u not in sources:
            sources.append(u)
    for it in trigger_news:
        if it["url"] not in sources:
            sources.append(it["url"])
    return {
        "fx_angle": fx_angle, "trigger_news": trigger_news,
        "talk_track": talk_track, "follow_up_draft": follow_up,
        "sources": sources,
    }


def _fallback_brief(name: str, company: str, title: Optional[str],
                    vertical: Optional[str], fallback_angle: str) -> dict:
    first = name.split(" ")[0]
    return {
        "fx_angle": fallback_angle or f"Limited info on {company}; probe FX corridor + cross-border revenue mix.",
        "trigger_news": [],
        "talk_track": [
            f"Hi {first} — saw you're {title or 'leading something interesting'} at {company}.",
            f"Curious how {company} handles FX exposure today, especially on the cross-border leg.",
            "Grain embeds hedging directly into the booking/payment flow — no treasury build-out.",
            "Happy to share a one-pager or grab 15 minutes next week.",
        ],
        "follow_up_draft": (
            f"Hi {first}, great to bump into you at the conference. I'd love to "
            f"understand more about {company}'s cross-currency setup — where does "
            "FX spread show up in your P&L today? At Grain we embed hedging "
            "directly into platforms like yours; happy to send a one-pager and "
            "find 15 minutes next week. Worth a quick call?"
        ),
        "sources": [],
    }


def _render_markdown(name: str, company: str, title: Optional[str],
                     vertical: Optional[str], bj: dict) -> str:
    parts = [f"# Approach Brief — {name}", "",
             f"**{title or '?'}** · **{company}**"
             + (f" · _{vertical}_" if vertical else ""), "",
             "## Why Grain matters here",
             bj["fx_angle"] or "_(no angle generated)_", ""]
    if bj["trigger_news"]:
        parts.append("## Recent trigger news")
        for it in bj["trigger_news"]:
            line = f"- **{it['headline']}**"
            if it.get("date_iso"):
                line += f" _( {it['date_iso']} )_"
            parts.append(line)
            if it.get("why_relevant"):
                parts.append(f"  - {it['why_relevant']}")
            parts.append(f"  - <{it['url']}>")
        parts.append("")
    else:
        parts.append("## Recent trigger news")
        parts.append("_No specific recent news surfaced — open with discovery questions._")
        parts.append("")
    parts.append("## Talk track")
    for b in bj["talk_track"]:
        parts.append(f"- {b}")
    parts.append("")
    parts.append("## Follow-up email draft")
    parts.append("```")
    parts.append(bj["follow_up_draft"])
    parts.append("```")
    parts.append("")
    if bj["sources"]:
        parts.append("## Sources")
        for s in bj["sources"]:
            parts.append(f"- {s}")
    return "\n".join(parts)


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
