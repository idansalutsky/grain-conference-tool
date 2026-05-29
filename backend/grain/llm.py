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
    "  vertical       — one of: fintech_other / payments / travel / saas / treasury / crypto / unknown\n"
    "  what_discussed — 1-2 sentence summary IN ENGLISH (translate if needed)\n"
    "  soft_signals   — array of: wants_meeting / asked_about_pricing / explicit_pain / "
    "strong_fit_signal / time_sensitive / lukewarm / dismissive\n"
    "  sentiment      — 1 (cold) to 5 (very warm)\n"
    "  meeting_requested — boolean\n"
    "  transcript     — verbatim transcript in original language\n"
    "If a field is unknown, use null (or empty array)."
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


def text_to_lead(text: str) -> dict:
    """Text relay → structured lead. Same schema as audio."""
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": (
            "The rep wrote this message about someone they met "
            f"(not voice — text relay):\n\n\"\"\"\n{text}\n\"\"\""
        )},
    ]
    return chat_json(messages, temperature=0.0, max_tokens=1024)


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
