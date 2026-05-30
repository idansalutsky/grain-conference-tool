"""Thin OpenRouter client. Three operations: text JSON, grounded search, audio.

Single integration point so swapping providers later is a one-file change.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config

log = logging.getLogger("grain.llm")


class LLMError(RuntimeError):
    pass


def _headers() -> dict:
    if not config.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY not set — required for AI features.")
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/grain-finance/conference-intel",
        "X-Title": "Grain Conference Intelligence",
    }


# ---------------------------------------------------------------------------
# 1. chat_json — structured text completion
# ---------------------------------------------------------------------------
def chat_json(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> dict:
    """Call OpenRouter, return parsed JSON.

    The model is asked to reply with strict JSON; we also strip ``` fences
    just in case. Raises LLMError on transport or parse failure.
    """
    url = f"{config.OPENROUTER_BASE_URL}/chat/completions"
    payload = {
        "model": model or config.OPENROUTER_TEXT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenRouter HTTP error: {exc}") from exc
    if r.status_code != 200:
        raise LLMError(f"OpenRouter {r.status_code}: {r.text[:500]}")
    data = r.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    content = content.strip()
    # Strip code fences
    if content.startswith("```"):
        content = re.sub(r"^```(json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        # Last-resort: find the first { ... } substring and parse that
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMError(f"could not parse JSON from model output: {content[:300]}") from exc


# ---------------------------------------------------------------------------
# 2. search_grounded — Perplexity Sonar with citations
# ---------------------------------------------------------------------------
def search_grounded(query: str, *, system: Optional[str] = None) -> tuple[str, list[dict]]:
    """Grounded-search call. Returns (text, citations). Used for brief gen
    (recent news about a target's company) and conference discovery."""
    url = f"{config.OPENROUTER_BASE_URL}/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": query})
    payload = {
        "model": config.OPENROUTER_SEARCH_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            r = client.post(url, headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenRouter grounded search error: {exc}") from exc
    if r.status_code != 200:
        raise LLMError(f"OpenRouter {r.status_code}: {r.text[:500]}")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    text = choice.get("message", {}).get("content") or ""
    # Perplexity returns citations either inline ([1] etc.) or in a separate
    # `citations` field at the top level. Handle both.
    citations: list[dict] = []
    for url_ in data.get("citations", []) or []:
        if isinstance(url_, str):
            citations.append({"url": url_, "title": ""})
    return text, citations


# ---------------------------------------------------------------------------
# 3. audio_to_lead — Gemini multimodal, voice memo → structured lead
# ---------------------------------------------------------------------------
EXTRACT_SYSTEM = (
    "You are a sales assistant for Grain Finance, a fintech selling embedded "
    "FX hedging to platforms with cross-border transaction volume (PSPs, "
    "travel marketplaces, cross-border payment companies). A sales rep at a "
    "conference will send you a voice memo about someone they just met. "
    "Extract a structured lead.\n\n"
    "Reply with ONLY a JSON object with these keys:\n"
    "  name           — best-effort full name (string OR null)\n"
    "  company        — company name (string OR null)\n"
    "  title          — job title (string OR null)\n"
    "  email          — an email address if the rep mentioned/spelled one, else null\n"
    "  vertical       — one of: fintech_other / payments / travel / saas / treasury / crypto / unknown\n"
    "  what_discussed — 1-2 sentence summary IN ENGLISH (translate if needed)\n"
    "  soft_signals   — array of: wants_meeting / asked_about_pricing / explicit_pain / "
    "strong_fit_signal / time_sensitive / lukewarm / dismissive\n"
    "  sentiment      — 1 (cold) to 5 (very warm)\n"
    "  meeting_requested — boolean\n"
    "  linkedin       — a LinkedIn profile URL if one is visible/derivable, else null\n"
    "  phone          — a phone number if the rep stated one, else null\n"
    "  mentioned_events — array of conference/event NAMES the person says they "
    "attend, attended, or are going to (e.g. 'we were at Sibos', 'see you at "
    "Money20/20'), else []. Only real named events, not generic phrases.\n"
    "  competitor_signals — array of SHORT notes (else []) when they mention a "
    "competitor or current FX/payments provider: who they use/evaluate/are "
    "leaving and why (e.g. 'uses Convera, unhappy with the spreads', 'comparing "
    "us to Wise'). One concise note per mention.\n"
    "  product_signals — array of SHORT notes (else []) capturing market/product "
    "intelligence worth routing to GTM/product: a pain with their current "
    "approach, a feature/capability they wish existed, an objection, or a "
    "buying-process/budget/timing signal (e.g. 'wants a real-time hedging API', "
    "'manual multi-entity hedging is painful', 'budget approved for Q3'). Only "
    "genuinely useful signal — omit small talk.\n"
    "  transcript     — verbatim transcript in original language\n"
    "Extract the email whenever it appears in the note (e.g. 'her email is "
    "michael@wise.com' → \"michael@wise.com\"). Email is a strong identity key "
    "for matching this person across conferences, so never drop it when present.\n"
    "If a field is unknown, use null (or empty array)."
)


# Badge / business-card photo → same structured lead. A conference badge shows
# name + company (+ sometimes title); a business card adds title/email. There is
# no spoken context, so sentiment defaults to neutral and meeting_requested false
# unless the rep added a note alongside the photo.
BADGE_SYSTEM = (
    "You are a sales assistant for Grain Finance, a fintech selling embedded "
    "FX hedging to platforms with cross-border volume (PSPs, travel "
    "marketplaces, cross-border payment companies). A sales rep at a "
    "conference photographed someone's BADGE or BUSINESS CARD. Read the image "
    "and extract a structured lead.\n\n"
    "Reply with ONLY a JSON object with these keys:\n"
    "  name           — full name as printed (string OR null)\n"
    "  company        — company/organisation as printed (string OR null)\n"
    "  title          — job title if printed (string OR null)\n"
    "  email          — email if printed on a card (string OR null)\n"
    "  phone          — phone number if printed on a card (string OR null)\n"
    "  vertical       — one of: fintech_other / payments / travel / saas / "
    "treasury / crypto / unknown (infer from the company if you can)\n"
    "  what_discussed — null (a photo carries no conversation)\n"
    "  soft_signals   — [] (none from a photo alone)\n"
    "  sentiment      — 3 (neutral — no spoken signal)\n"
    "  meeting_requested — false\n"
    "  linkedin       — null unless a LinkedIn handle/QR is clearly readable\n"
    "  transcript     — null\n"
    "  ocr_confidence — your confidence the name+company were read correctly, 0..1\n"
    "If the image is NOT a badge/card or is unreadable, set name and company to "
    "null and ocr_confidence to 0. Never invent a name."
)


def audio_to_lead(audio_path: Path) -> dict:
    """Voice memo → structured lead via Gemini multimodal."""
    p = Path(audio_path)
    if not p.exists():
        raise LLMError(f"audio file not found: {p}")
    audio_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    suffix = p.suffix.lstrip(".").lower() or "mp3"
    # OpenRouter/Gemini audio input prefers wav/mp3; browser MediaRecorder
    # emits webm/ogg (opus). Map both so the format string is honest.
    mime = {
        "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
        "m4a": "audio/mp4", "mp4": "audio/mp4", "flac": "audio/flac",
        "webm": "audio/webm", "aac": "audio/aac",
    }.get(suffix, "audio/mpeg")

    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "Voice memo from a sales rep on a conference floor:"},
            {"type": "input_audio", "input_audio": {
                "data": audio_b64, "format": suffix,
            }},
        ]},
    ]
    return chat_json(
        messages,
        model=config.OPENROUTER_AUDIO_MODEL,
        temperature=0.0,
        max_tokens=1024,
    )


def _fallback_text_to_lead(text: str) -> dict:
    """Deterministic, key-free extraction for the text-capture path.

    Weak vs the LLM, but it means a non-dev can host this and a rep can capture
    a typed note with NO API key (the brief: "a non-developer should be able to
    host"). Graceful degradation, not a 500. Clearly heuristic.
    """
    import re
    t = (text or "").strip()
    low = t.lower()
    email = (re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", t) or [None])
    email = email.group(0) if hasattr(email, "group") else None
    phone = re.search(r"(\+?\d[\d\s().-]{7,}\d)", t)
    linkedin = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/[\w\-/%.]+", t, re.I)
    _STOP = r"(?:warm|cold|big|very|strong|keen|interested|now|still|owns|wants|nice|great)"
    # company: "at/@/with/for/of <Company>" — stop at punctuation or a filler word
    comp = re.search(r"\b(?:at|@|with|for|from|of)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2})", t)
    company = comp.group(1).strip(" .,–-") if comp else None
    if company:
        company = re.split(r"[.,;]\s|\s+" + _STOP + r"\b", company, flags=re.I)[0].strip(" .,–-") or None
    # title: common finance/commercial titles (cut anything after " at/@/with")
    tm = re.search(r"\b((?:Group\s+|Head of\s+|VP\s+|Vice President\s+|Chief\s+|Director of\s+|Senior\s+)?"
                   r"(?:CFO|CEO|COO|CTO|CCO|CRO|Treasurer|Treasury|Finance|Payments|"
                   r"Partnerships|Controller|FP&A)[\w &/]*)", t, re.I)
    title = tm.group(1).strip() if tm else None
    if title:
        title = re.split(r"\s+(?:at|@|with|for|of)\b", title, flags=re.I)[0].strip(" .,–-") or None
    # name: "Met <Name>" / "<Name>," at the start (case-insensitive verb)
    nm = re.search(r"\b(?:met|spoke (?:to|with)|ran into|saw|caught|introduced to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", t, re.I)
    if not nm:
        nm = re.search(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", t)
    name = nm.group(1).strip() if nm else None
    sentiment = 3
    if any(w in low for w in ("warm", "keen", "interested", "excited", "great chat", "strong fit", "loved")):
        sentiment = 4
    if any(w in low for w in ("very warm", "ready to", "wants to buy", "perfect fit")):
        sentiment = 5
    if any(w in low for w in ("not interested", "cold", "tire", "polite", "no budget")):
        sentiment = 2
    meeting = any(w in low for w in ("meeting", "call", "demo", "follow-up", "follow up",
                                     "followup", "schedule", "catch up", "next week", "send pricing"))
    signals = []
    if any(w in low for w in ("fx", "hedg", "currency", "cross-border", "multi-currency", "payout")):
        signals.append("fx_relevant")
    if any(w in low for w in ("pain", "manual", "painful", "losing", "spread", "exposure")):
        signals.append("explicit_pain")
    if "strong fit" in low or "perfect fit" in low:
        signals.append("strong_fit_signal")
    return {
        "name": name, "company": company, "title": title, "vertical": None,
        "what_discussed": t[:240] or None, "soft_signals": signals,
        "sentiment": sentiment, "meeting_requested": meeting,
        "phone": phone.group(1).strip() if phone else None,
        "linkedin": linkedin.group(0) if linkedin else None,
        "email": email, "mentioned_events": [],
        "competitor_signals": [], "product_signals": [], "transcript": t,
        "_extraction": "deterministic-fallback (no LLM key)",
    }


def text_to_lead(text: str) -> dict:
    """Text relay → structured lead. Same schema as audio.

    Degrades gracefully: with no OpenRouter key, a deterministic heuristic
    extractor runs so typed capture still works (the "type a quick note" path
    the field interface relies on). With a key, the LLM does the real extraction.
    """
    if not config.OPENROUTER_API_KEY:
        return _fallback_text_to_lead(text)
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": (
            "The rep wrote this message about someone they met "
            f"(not voice — text relay):\n\n\"\"\"\n{text}\n\"\"\""
        )},
    ]
    try:
        return chat_json(messages, temperature=0.0, max_tokens=1024)
    except LLMError:
        # Key present but the call failed (rate limit / network) — don't 500 the
        # rep on the show floor; fall back to the deterministic extractor.
        return _fallback_text_to_lead(text)


# Image extensions OpenRouter/Gemini accept as inline data URIs.
_IMAGE_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif", "heic": "image/heic",
}


def image_to_lead(image_path: Path) -> dict:
    """Badge / business-card photo → structured lead via Gemini vision."""
    p = Path(image_path)
    if not p.exists():
        raise LLMError(f"image file not found: {p}")
    raw = p.read_bytes()
    if not raw:
        raise LLMError("image file is empty")
    suffix = p.suffix.lstrip(".").lower()
    mime = _IMAGE_MIME.get(suffix, "image/jpeg")
    b64 = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"
    messages = [
        {"role": "system", "content": BADGE_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "Conference badge / business card photo:"},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]},
    ]
    return chat_json(
        messages,
        model=config.OPENROUTER_AUDIO_MODEL,  # the multimodal Gemini model
        temperature=0.0,
        max_tokens=700,
    )


def linkedin_url_to_lead(url: str) -> dict:
    """A bare LinkedIn URL → best-effort structured lead.

    The URL itself is the highest-value field (entity resolution matches on
    linkedin), so we always return it. When a key is available we attempt one
    grounded lookup to fill name/title/company; on any failure we fall back to
    a name guessed from the URL slug. Never invents employer/title.
    """
    lead: dict = {
        "name": _name_from_linkedin_slug(url), "company": None, "title": None,
        "vertical": None, "what_discussed": None, "soft_signals": [],
        "sentiment": 3, "meeting_requested": False, "linkedin": url.strip(),
        "transcript": None,
    }
    if not config.OPENROUTER_API_KEY:
        return lead
    try:
        text, _ = search_grounded(
            f"Identify the person at this LinkedIn profile: {url}. "
            "Reply with ONLY JSON: "
            '{"name": "...", "title": "...", "company": "..."}. '
            "Use null for anything you cannot determine — do not guess.",
            system="You resolve a LinkedIn URL to the person's current name, "
                   "title, and employer. Be accurate; null over guess.",
        )
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            for k in ("name", "title", "company"):
                v = data.get(k)
                if isinstance(v, str) and v.strip() and v.strip().lower() != "null":
                    lead[k] = v.strip()
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        log.info("LinkedIn enrichment fell back to slug for %s: %s", url, exc)
    return lead


def _name_from_linkedin_slug(url: str) -> Optional[str]:
    """Derive a rough display name from a /in/<slug> path. 'jane-doe-cfo' →
    'Jane Doe'. Drops trailing role/hash tokens and numeric IDs."""
    m = re.search(r"/in/([^/?#]+)", url or "", re.IGNORECASE)
    if not m:
        return None
    slug = m.group(1)
    parts = [pt for pt in slug.split("-") if pt and not pt.isdigit()]
    # LinkedIn slugs often append role/company words after the name; the name is
    # usually the first 2 tokens. Keep up to 3 alpha tokens, title-cased.
    name_parts = []
    for pt in parts[:3]:
        if pt.isalpha() and len(pt) <= 20:
            name_parts.append(pt.capitalize())
    return " ".join(name_parts[:2]) if name_parts else None


# ---------------------------------------------------------------------------
# 4. chat_with_tools — OpenAI-style function calling for agent loops
# ---------------------------------------------------------------------------
def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
) -> dict:
    """Call OpenRouter with tools enabled.

    Returns the FULL response so the caller can read `.choices[0].message.tool_calls`
    and execute each tool, appending `role: "tool"` messages and looping.

    Tools follow the OpenAI function-calling schema:
        {
            "type": "function",
            "function": {
                "name": str,
                "description": str,
                "parameters": <JSON schema>
            }
        }
    """
    url = f"{config.OPENROUTER_BASE_URL}/chat/completions"
    payload = {
        "model": model or config.OPENROUTER_TEXT_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(url, headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenRouter tool-call error: {exc}") from exc
    if r.status_code != 200:
        raise LLMError(f"OpenRouter {r.status_code}: {r.text[:500]}")
    return r.json()
