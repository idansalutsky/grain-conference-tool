"""Arc classifier — interpret a multi-encounter relationship.

The brief explicitly asked: "is this a warming relationship worth closing,
or a polite tire-kicker who's been listening for a year and never buying?"

This module turns the encounter history of a single contact into one of:
  - warming      — clear progression (more positive signals over time)
  - flat         — one or two encounters, no movement
  - cooling      — signals have weakened (sentiment dropped, meeting requests stopped)
  - tire_kicker  — 3+ encounters across a long window with no concrete next step

It runs a deterministic feature-vector classifier first (fast, explainable),
then asks an LLM for a higher-fidelity verdict. The LLM is the JUDGE, not the
oracle — if the LLM disagrees with the deterministic rule by too much, we
keep the deterministic call and log the disagreement.

The output carries CONFIDENCE — used by the nudge module to decide whether
to fire (silent on weak signal, by design).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import db, llm

log = logging.getLogger("grain.arc")


@dataclass
class ArcVerdict:
    kind: str            # warming / flat / cooling / tire_kicker
    confidence: float    # 0..1
    summary: str         # one-sentence rationale
    features: dict


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
def _load_encounters(contact_id: str) -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, captured_at, sentiment, meeting_requested, "
            "structured_json, soft_signals_json FROM encounters "
            "WHERE contact_id = ? ORDER BY captured_at ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["structured"] = json.loads(d["structured_json"] or "{}")
        d["soft_signals"] = json.loads(d["soft_signals_json"] or "[]")
        out.append(d)
    return out


def _days_between(a: str, b: str) -> int:
    da = datetime.fromisoformat(a.replace("Z", "+00:00"))
    db_ = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return abs((db_ - da).days)


def _features(encounters: list[dict]) -> dict:
    n = len(encounters)
    if n == 0:
        return {"n": 0}
    first = encounters[0]["captured_at"]
    last = encounters[-1]["captured_at"]
    span = _days_between(first, last) if n > 1 else 0
    sentiments = [e["sentiment"] or 3 for e in encounters]
    meeting_reqs = sum(1 for e in encounters if e["meeting_requested"])
    signals_all = [s for e in encounters for s in (e["soft_signals"] or [])]

    # Trend: did sentiment improve from first half to second half?
    if n >= 2:
        half = n // 2 or 1
        early_avg = sum(sentiments[:half]) / half
        late_avg = sum(sentiments[-half:]) / half
        sentiment_trend = late_avg - early_avg
    else:
        sentiment_trend = 0.0

    return {
        "n": n,
        "span_days": span,
        "first_sentiment": sentiments[0],
        "last_sentiment": sentiments[-1],
        "avg_sentiment": round(sum(sentiments) / n, 2),
        "sentiment_trend": round(sentiment_trend, 2),
        "meeting_requests": meeting_reqs,
        "explicit_pain_signals": sum(1 for s in signals_all if "pain" in s),
        "wants_meeting_signals": sum(1 for s in signals_all if "wants_meeting" in s),
        "lukewarm_signals": sum(1 for s in signals_all if "lukewarm" in s
                                or "dismissive" in s),
    }


# ---------------------------------------------------------------------------
# Deterministic classifier (the safety net)
# ---------------------------------------------------------------------------
def _deterministic_verdict(f: dict) -> ArcVerdict:
    n = f.get("n", 0)
    if n == 0:
        return ArcVerdict("flat", 0.4, "no encounters yet", f)
    if n <= 2:
        return ArcVerdict("flat", 0.55,
                          f"only {n} encounter(s) — not enough history to call",
                          f)

    span = f.get("span_days", 0)
    avg_sent = f.get("avg_sentiment", 3)
    trend = f.get("sentiment_trend", 0)
    meets = f.get("meeting_requests", 0)
    pain = f.get("explicit_pain_signals", 0)
    lukewarm = f.get("lukewarm_signals", 0)

    # Tire-kicker: many encounters across a long window with NO meeting + lukewarm signals
    if n >= 3 and span >= 180 and meets == 0 and lukewarm >= 2:
        return ArcVerdict("tire_kicker", 0.80,
                          f"{n} encounters over {span}d with no meeting + lukewarm signals",
                          f)

    # Cooling: sentiment trend is negative or last < 3 + meetings dropped off
    if trend <= -0.5 or (f.get("last_sentiment", 3) <= 2 and meets > 0
                         and f.get("first_sentiment", 3) > 2):
        return ArcVerdict("cooling", 0.70,
                          f"sentiment trend {trend:+.1f}; was warmer earlier",
                          f)

    # Warming: positive trend + at least one meeting request + at least one pain signal
    if trend >= 0.5 and meets >= 1:
        return ArcVerdict("warming", 0.80,
                          f"sentiment trend {trend:+.1f}; {meets} meeting requests",
                          f)
    if avg_sent >= 4 and pain >= 1:
        return ArcVerdict("warming", 0.72,
                          f"avg sentiment {avg_sent}; explicit pain signals",
                          f)

    return ArcVerdict("flat", 0.55,
                      f"{n} encounters but no clear directional signal",
                      f)


# ---------------------------------------------------------------------------
# LLM judge (the higher-fidelity opinion)
# ---------------------------------------------------------------------------
_JUDGE_SYSTEM = (
    "You judge the trajectory of a sales relationship across multiple "
    "conference encounters. You will receive a structured history. Reply "
    "with ONLY this JSON: "
    '{"verdict": "warming|flat|cooling|tire_kicker", "confidence": 0..1, '
    '"summary": "one short sentence"}. Be honest — if there is not enough '
    "signal to call it warming, say flat. A tire-kicker has shown up 3+ "
    "times over a long window with no concrete next-step."
)


def _llm_judge(encounters: list[dict]) -> Optional[ArcVerdict]:
    if not encounters:
        return None
    if not llm.config.OPENROUTER_API_KEY:
        return None  # no key → skip judge
    history_lines = []
    for i, e in enumerate(encounters, 1):
        s = e["structured"] or {}
        signals = ",".join(e["soft_signals"] or []) or "(none)"
        history_lines.append(
            f"Encounter {i} @ {e['captured_at'][:10]} | sentiment={e['sentiment']} "
            f"| meeting_requested={bool(e['meeting_requested'])} "
            f"| signals={signals} | discussed: "
            f"{(s.get('what_discussed') or '')[:180]}"
        )
    user = "Encounter history:\n" + "\n".join(history_lines)
    try:
        data = llm.chat_json([
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ], temperature=0.0, max_tokens=300)
    except llm.LLMError as exc:
        log.warning("LLM judge failed: %s", exc)
        return None
    verdict = (data.get("verdict") or "").lower()
    if verdict not in {"warming", "flat", "cooling", "tire_kicker"}:
        return None
    return ArcVerdict(
        kind=verdict,
        confidence=float(data.get("confidence") or 0.5),
        summary=data.get("summary") or "",
        features={},
    )


# ---------------------------------------------------------------------------
# Top-level: classify + persist
# ---------------------------------------------------------------------------
def classify(contact_id: str, *, use_llm: bool = True) -> ArcVerdict:
    encounters = _load_encounters(contact_id)
    f = _features(encounters)
    det = _deterministic_verdict(f)

    final = det
    if use_llm:
        judge = _llm_judge(encounters)
        if judge is not None:
            # If the LLM agrees, lift confidence.
            if judge.kind == det.kind:
                final = ArcVerdict(
                    kind=det.kind,
                    confidence=min(0.95, det.confidence * 0.5 + judge.confidence * 0.5 + 0.1),
                    summary=judge.summary or det.summary,
                    features=f,
                )
            else:
                # Disagreement: trust the deterministic rule if its confidence
                # is high; otherwise believe the LLM but cap confidence.
                if det.confidence >= 0.75:
                    final = det
                    log.info("Arc disagreement contact=%s det=%s llm=%s — using det",
                             contact_id, det.kind, judge.kind)
                else:
                    final = ArcVerdict(
                        kind=judge.kind,
                        confidence=min(0.7, judge.confidence),
                        summary=judge.summary,
                        features=f,
                    )

    # Persist on the contact row
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE contacts SET arc_verdict = ?, arc_summary = ?, "
            "arc_confidence = ?, updated_at = ? WHERE id = ?",
            (final.kind, final.summary, final.confidence, db.now_iso(), contact_id),
        )
    finally:
        conn.close()
    return final
