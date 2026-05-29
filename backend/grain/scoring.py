"""Conference ICP-fit scoring.

7 factors, each yielding a 0..1 sub-score + a transparent evidence string.
Default weights sum to 1.0; a tenant can re-tune via the Settings UI without
changing code. The 7th factor (historical_yield) is opt-in; defaults to 0.

  vertical_concentration  0.25  — is this an event where Grain's verticals concentrate?
  buyer_density           0.25  — likelihood CFO/Treasury/Payments leaders attend
  fx_exposure_proxy       0.20  — themes carrying FX-relevant signal
  reachability            0.10  — format/size — can a rep actually meet people?
  geo_cost_efficiency     0.10  — region weighted by typical travel cost
  competitive_validation  0.10  — competitor presence (signal, not a deterrent)
  historical_yield        0.00  — boost from prior meetings/deals (when we have data)

Every sub-score's evidence string is shown in the UI — sales should be able
to argue with the model, not just the output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import db


DEFAULT_WEIGHTS = {
    "vertical_concentration": 0.25,
    "buyer_density": 0.25,
    "fx_exposure_proxy": 0.20,
    "reachability": 0.10,
    "geo_cost_efficiency": 0.10,
    "competitive_validation": 0.10,
    "historical_yield": 0.00,  # opt-in
}


def _live_weights() -> dict[str, float]:
    """Read live weights from settings; fall back to defaults per factor."""
    raw = db.get_settings_many([f"scoring.{k}" for k in DEFAULT_WEIGHTS])
    out = {}
    for k, default in DEFAULT_WEIGHTS.items():
        v = raw.get(f"scoring.{k}")
        try:
            out[k] = float(v) if v is not None else default
        except (ValueError, TypeError):
            out[k] = default
    return out


# ---------------------------------------------------------------------------
# Per-factor scorers (each returns (sub_score 0..1, evidence string))
# ---------------------------------------------------------------------------
ICP_VERTICALS = {
    "fintech_other", "payments", "psp", "cross_border_payments",
    "travel", "booking", "marketplace", "treasury", "crypto", "supply_chain",
}

FX_KEYWORDS = {
    "cross-border", "cross border", "treasury", "fx", "foreign exchange",
    "settlement", "international payments", "payouts", "multi-currency",
    "stablecoin", "currencies", "remittance", "corridor", "embedded finance",
}

BUYER_KEYWORDS = {
    "cfo", "treasury", "treasurer", "payments leader", "head of finance",
    "head of payments", "vp finance", "vp treasury",
}


def _vertical_concentration(conf: dict) -> tuple[float, str]:
    v = (conf.get("vertical") or "").lower()
    themes = (conf.get("themes") or "").lower()
    if v in ICP_VERTICALS:
        # Core wedge: travel/booking (lead wedge) + payments rails + treasury +
        # marketplaces all get the strongest company-type signal.
        strong = {"travel", "booking", "marketplace", "treasury",
                  "payments", "psp", "cross_border_payments"}
        if v in strong:
            return 1.0, f"core vertical: {v}"
        return 0.85, f"in-ICP vertical: {v}"
    # Fallback: theme-text scan
    hits = sum(1 for k in FX_KEYWORDS if k in themes)
    if hits >= 3:
        return 0.7, f"{hits} FX-adjacent themes despite vertical={v or '?'}"
    if hits >= 1:
        return 0.4, f"{hits} FX-adjacent themes"
    return 0.1, f"vertical={v or '?'}, no FX themes"


def _buyer_density(conf: dict) -> tuple[float, str]:
    # Prefer the MEASURED audience composition when we scraped it — grounded,
    # not guessed. cfo_treasury_finance_pct is the buyer-density signal.
    ac = conf.get("audience_composition_json")
    if ac:
        try:
            import json as _json
            comp = _json.loads(ac) if isinstance(ac, str) else ac
            pct = comp.get("cfo_treasury_finance_pct")
            if pct is not None:
                raw = max(0.05, min(1.0, float(pct) / 80.0))  # 80%+ → max
                return round(raw, 2), f"{pct}% finance/treasury audience (measured)"
        except (ValueError, TypeError, AttributeError):
            pass
    themes = (conf.get("themes") or "").lower()
    name = (conf.get("name") or "").lower()
    # Treasury-pure events
    if "treasury" in name or "treasury" in themes:
        return 0.95, "treasury-pure event — direct buyer attendance"
    # Strong CFO signals
    score = 0.3
    notes = []
    for k in BUYER_KEYWORDS:
        if k in themes or k in name:
            score += 0.15
            notes.append(k)
    score = min(score, 0.95)
    if not notes:
        notes = ["no explicit CFO/treasury signal in name/themes"]
    return score, f"buyer signals: {', '.join(notes[:3])}"


def _fx_exposure_proxy(conf: dict) -> tuple[float, str]:
    themes = (conf.get("themes") or "").lower()
    name = (conf.get("name") or "").lower()
    haystack = themes + " " + name
    hits = [k for k in FX_KEYWORDS if k in haystack]
    if not hits:
        return 0.1, "no FX-relevant themes"
    score = min(0.4 + 0.15 * len(hits), 0.95)
    return score, f"FX signals: {', '.join(hits[:4])}"


def _reachability(conf: dict) -> tuple[float, str]:
    fmt = (conf.get("format") or "").lower()
    n = conf.get("estimated_attendance") or 0
    # Expos and large summits = good reachability. Closed roundtables = harder.
    if fmt in ("expo", "trade show"):
        return 0.9, f"expo / trade-show format; {n} attendees"
    if fmt in ("summit", "conference"):
        if n >= 1000:
            return 0.75, f"large {fmt}; {n} attendees"
        return 0.55, f"mid-size {fmt}; {n} attendees"
    if fmt in ("webinar", "virtual"):
        return 0.2, f"virtual ({fmt}) — no floor"
    if n >= 5000:
        return 0.85, f"{n} attendees, format={fmt or '?'}"
    if n >= 1000:
        return 0.65, f"{n} attendees, format={fmt or '?'}"
    return 0.4, f"{n} attendees, format={fmt or '?'}"


_REGION_COST_FACTOR = {
    "NA": 0.8, "EU": 0.75, "MEA": 0.6, "APAC": 0.55, "LATAM": 0.5,
}


def _geo_cost_efficiency(conf: dict) -> tuple[float, str]:
    region = (conf.get("region") or "").upper()
    f = _REGION_COST_FACTOR.get(region, 0.5)
    return f, f"region {region or '?'} cost factor {f}"


def _competitive_validation(conf: dict, competitors: list[str]) -> tuple[float, str]:
    themes_l = (conf.get("themes") or "").lower()
    name_l = (conf.get("name") or "").lower()
    haystack = themes_l + " " + name_l
    hits = [c for c in competitors if c.lower() in haystack]
    if hits:
        # 2-3 competitor mentions = strong validation
        score = min(0.6 + 0.1 * len(hits), 0.95)
        return score, f"competitor presence validates ICP: {', '.join(hits[:3])}"
    return 0.3, "no competitor signal — neutral"


def _historical_yield(conf: dict) -> tuple[float, str]:
    """Boost from prior meetings + deals at this conference (or its series)."""
    cid = conf.get("id")
    if not cid:
        return 0.0, "no conference id"
    conn = db.get_conn()
    try:
        encs = conn.execute(
            "SELECT COUNT(*) FROM encounters WHERE conference_id = ?", (cid,)
        ).fetchone()[0]
        meetings = conn.execute(
            "SELECT COUNT(*) FROM encounters WHERE conference_id = ? AND meeting_requested = 1",
            (cid,)
        ).fetchone()[0]
    finally:
        conn.close()
    if not encs:
        return 0.0, "no historical encounters yet"
    yield_ratio = meetings / encs
    if yield_ratio >= 0.4:
        return 0.85, f"strong yield: {meetings}/{encs} encounters → meetings"
    if yield_ratio >= 0.2:
        return 0.6, f"moderate yield: {meetings}/{encs}"
    return 0.3, f"weak yield: {meetings}/{encs}"


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
@dataclass
class FactorScore:
    key: str
    raw: float          # 0..1 sub-score
    weight: float       # current weight
    weighted: float     # raw * weight * 100 → contributes to overall
    evidence: str


@dataclass
class ConferenceScore:
    total: float                    # 0..100
    tier: str                       # A / B / C
    factors: list[FactorScore]

    def to_breakdown_dict(self) -> dict:
        return {
            "total": round(self.total, 2),
            "tier": self.tier,
            "factors": [
                {
                    "key": f.key, "raw": round(f.raw, 3),
                    "weight": round(f.weight, 3),
                    "weighted": round(f.weighted, 3),
                    "evidence": f.evidence,
                }
                for f in self.factors
            ],
        }


def score_conference(conf: dict, *, icp_competitors: Optional[list[str]] = None,
                     weights: Optional[dict[str, float]] = None) -> ConferenceScore:
    """Compute the 7-factor score. Returns a ConferenceScore with full audit trail."""
    if weights is None:
        weights = _live_weights()
    if icp_competitors is None:
        from .icp import IcpConfig
        icp_competitors = IcpConfig.default().competitors

    scorers = [
        ("vertical_concentration", _vertical_concentration(conf)),
        ("buyer_density", _buyer_density(conf)),
        ("fx_exposure_proxy", _fx_exposure_proxy(conf)),
        ("reachability", _reachability(conf)),
        ("geo_cost_efficiency", _geo_cost_efficiency(conf)),
        ("competitive_validation", _competitive_validation(conf, icp_competitors)),
        ("historical_yield", _historical_yield(conf)),
    ]

    factors: list[FactorScore] = []
    total = 0.0
    for key, (raw, ev) in scorers:
        w = float(weights.get(key, 0.0))
        weighted = raw * w * 100  # to 0..100 scale
        total += weighted
        factors.append(FactorScore(key=key, raw=raw, weight=w, weighted=weighted, evidence=ev))

    tier = "A" if total >= 70 else "B" if total >= 50 else "C"
    return ConferenceScore(total=total, tier=tier, factors=factors)


def rescore_all() -> int:
    """Recompute + persist scores for every conference. Returns count."""
    import json
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM conferences").fetchall()
    finally:
        conn.close()
    n = 0
    for row in rows:
        conf = dict(row)
        s = score_conference(conf)
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE conferences SET score = ?, tier = ?, "
                "score_breakdown_json = ?, updated_at = ? WHERE id = ?",
                (s.total, s.tier, json.dumps(s.to_breakdown_dict(), ensure_ascii=False),
                 db.now_iso(), conf["id"]),
            )
            n += 1
        finally:
            conn.close()
    return n
