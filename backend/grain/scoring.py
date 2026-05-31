"""Conference ICP-fit scoring.

7 factors, each yielding a 0..1 sub-score + a transparent evidence string.
Default weights sum to 1.0; a tenant can re-tune via the Settings UI without
changing code. The 7th factor (historical_yield) is opt-in; defaults to 0.

  vertical_concentration  0.25  - is this an event where Grain's verticals concentrate?
  buyer_reachability      0.25  - can we reach Grain's BUYING COMMITTEE here?
  fx_exposure_proxy       0.20  - themes carrying FX-relevant signal
  reachability            0.10  - format/size - can a rep actually meet people?
  geo_cost_efficiency     0.10  - region weighted by typical travel cost
  icp_strategic_fit       0.10  - vertical-strategic-fit + ICP-company density
  historical_yield        0.00  - boost from prior meetings/deals (when we have data)

METHODOLOGY NOTE (re-tuned 2026-05).
The previous model had two structural problems that buried Grain's stated LEAD
wedge (travel / booking / marketplaces) and wasted 10% of the weight:

  1. buyer_density keyed almost entirely on `cfo_treasury_finance_pct`. That is
     the right signal for a treasury-pure event, but it is the WRONG signal for
     the travel/marketplace wedge. Grain reaches travel & platform targets
     through their COMMERCIAL / PARTNERSHIP / PRODUCT people (the ENTRY_POINT,
     CHAMPION and PAIN_OWNER personas in icp.py), not through their CFO. A
     Phocuswright-type event is ~15% finance but ~35-50% commercial - i.e.
     densely populated with exactly the personas a Grain rep opens the door
     through. So the factor is now `buyer_reachability`: it scores the share of
     the audience that maps to ANY reachable buying-committee persona, with the
     finance/treasury slice weighted highest (it is the BUYER) and the
     commercial/product slice weighted via the ENTRY_POINT/CHAMPION persona
     weights. Treasury-pure events still score at the top (their finance slice
     is huge); high-fit travel/payments-platform events can now also reach the
     top on the strength of their reachable commercial committee.

  2. competitive_validation scanned the conference name/themes for competitor
     COMPANY names. Those never appear in event metadata, so it fired on 0/195
     events - a constant +3.0 that differentiated nothing. It is replaced by
     `icp_strategic_fit`: a real, differentiating signal that rewards events
     sitting squarely on a Grain strategic wedge (travel/booking/marketplace =
     the lead wedge; payments/PSP/cross-border = the rails wedge; treasury =
     the direct-buyer wedge) and blends in the ICP-company density we can
     measure from the audience composition. Every event now gets a distinct
     value here.

Every sub-score's evidence string is shown in the UI - sales should be able
to argue with the model, not just the output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import db


DEFAULT_WEIGHTS = {
    "vertical_concentration": 0.25,
    "buyer_reachability": 0.25,
    "fx_exposure_proxy": 0.20,
    "reachability": 0.10,
    "geo_cost_efficiency": 0.10,
    "icp_strategic_fit": 0.10,
    "historical_yield": 0.00,  # opt-in
}

# Backwards-compat: a tenant who set the OLD setting keys in the DB shouldn't
# silently fall back to defaults after this rename. Map old -> new.
_RENAMED_WEIGHTS = {
    "buyer_density": "buyer_reachability",
    "competitive_validation": "icp_strategic_fit",
}


def _live_weights() -> dict[str, float]:
    """Read live weights from settings; fall back to defaults per factor.

    Honours legacy setting keys (the pre-rename names) so a tenant who already
    tuned `scoring.buyer_density` / `scoring.competitive_validation` keeps that
    value under the new factor name instead of snapping back to the default.
    """
    keys = [f"scoring.{k}" for k in DEFAULT_WEIGHTS]
    keys += [f"scoring.{old}" for old in _RENAMED_WEIGHTS]
    raw = db.get_settings_many(keys)
    out = {}
    for k, default in DEFAULT_WEIGHTS.items():
        v = raw.get(f"scoring.{k}")
        if v is None:
            # fall back to a legacy key that maps to this factor, if present
            old = next((o for o, n in _RENAMED_WEIGHTS.items() if n == k), None)
            if old is not None:
                v = raw.get(f"scoring.{old}")
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


# Reachable buying-committee model (mirrors icp.py personas). The audience
# composition we scrape splits attendees into broad professional buckets; we map
# each bucket to the buying-committee persona a Grain rep actually engages there,
# and weight it by how directly that persona drives a Grain deal.
#
#   finance/treasury bucket   -> BUYER             (decision owner)         1.00
#   commercial/sales bucket   -> ENTRY_POINT       (partnerships/BD/sales)  0.55
#   engineering/product bucket-> CHAMPION/GATEKEEPER (product/platform)     0.30
#
# A treasury-pure event is ~75-80% BUYER => near-max. A travel/marketplace event
# is ~12-15% BUYER but ~35-50% ENTRY_POINT => a genuinely reachable event instead
# of being buried at ~0.15 on finance % alone. The commercial bucket is the
# door-opener (ENTRY_POINT) so it carries real weight; the engineering/product
# bucket is the weakest path to a Grain FX deal, so it is discounted hardest -
# this stops a developer-heavy show (e.g. a crypto/dev expo) from riding raw
# headcount into Tier A on bodies that don't move a treasury purchase.
_PERSONA_BUCKET_WEIGHT = {
    "cfo_treasury_finance_pct": 1.00,   # BUYER
    "marketing_sales_pct": 0.55,        # ENTRY_POINT (commercial / partnerships)
    "engineering_product_pct": 0.30,    # CHAMPION / GATEKEEPER (product / platform)
}


def _buyer_reachability(conf: dict) -> tuple[float, str]:
    """How reachable is Grain's BUYING COMMITTEE at this event?

    Not just CFO density - that single-axis signal buried Grain's travel /
    marketplace lead wedge, where the door opens through commercial / product
    people (ENTRY_POINT, CHAMPION). We score the persona-weighted share of the
    audience that maps to ANY reachable committee persona, finance weighted
    highest as the decision owner. Prefers the MEASURED audience composition
    when we scraped it (grounded, not guessed); else a name/theme fallback.
    """
    ac = conf.get("audience_composition_json")
    if ac:
        try:
            import json as _json
            comp = _json.loads(ac) if isinstance(ac, str) else ac
            fin = float(comp.get("cfo_treasury_finance_pct") or 0)
            sales = float(comp.get("marketing_sales_pct") or 0)
            eng = float(comp.get("engineering_product_pct") or 0)
            # Persona-weighted reachable share of the audience (0..1).
            reachable = (
                fin * _PERSONA_BUCKET_WEIGHT["cfo_treasury_finance_pct"]
                + sales * _PERSONA_BUCKET_WEIGHT["marketing_sales_pct"]
                + eng * _PERSONA_BUCKET_WEIGHT["engineering_product_pct"]
            ) / 100.0
            # Calibrate: a 75% finance event -> 0.75; a 50%-commercial travel
            # event -> ~0.42 base. Give the finance (BUYER) slice an extra
            # bonus so treasury-pure events stay decisively at the top, but let
            # a committee-dense commercial event clear the Tier-A bar on merit.
            raw = reachable + 0.0015 * fin   # up to +0.12 for an 80%-finance event
            raw = max(0.05, min(1.0, raw))
            # Human-readable composition of the reachable committee.
            parts = []
            if fin:
                parts.append(f"{fin:.0f}% finance/treasury (BUYER)")
            if sales:
                parts.append(f"{sales:.0f}% commercial (ENTRY_POINT)")
            if eng:
                parts.append(f"{eng:.0f}% product/eng (CHAMPION)")
            return round(raw, 2), "reachable committee: " + ", ".join(parts or ["unknown mix"])
        except (ValueError, TypeError, AttributeError):
            pass
    # Fallback (no measured composition): name/theme signals.
    themes = (conf.get("themes") or "").lower()
    name = (conf.get("name") or "").lower()
    if "treasury" in name or "treasury" in themes:
        return 0.9, "treasury-pure event - direct buyer attendance (inferred)"
    score = 0.35
    notes = []
    for k in BUYER_KEYWORDS:
        if k in themes or k in name:
            score += 0.15
            notes.append(k)
    # Commercial-committee signals (the travel/marketplace door-openers).
    for k in ("partnerships", "commercial", "business development",
              "ecommerce", "e-commerce", "travel", "marketplace", "retail"):
        if k in themes or k in name:
            score += 0.08
            notes.append(k)
    score = min(score, 0.9)
    if not notes:
        notes = ["no explicit committee signal in name/themes"]
    return round(score, 2), f"committee signals: {', '.join(notes[:4])}"


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
    fmt = (conf.get("format") or "").lower().strip()
    n = conf.get("estimated_attendance") or 0
    # Normalise format spelling so the data's actual values match (the data uses
    # "trade_show", not "trade show"; it also carries festival/forum/leadership).
    fmt = fmt.replace(" ", "_")
    # Expos / trade shows = an open floor a rep can work = best reachability.
    if fmt in ("expo", "trade_show", "tradeshow"):
        return 0.9, f"expo / trade-show format; {n} attendees"
    # Large open-format gatherings (festivals, big forums) also have a floor.
    if fmt in ("festival", "forum"):
        if n >= 5000:
            return 0.8, f"large {fmt}; {n} attendees"
        return 0.6, f"{fmt}; {n} attendees"
    if fmt in ("summit", "conference"):
        if n >= 1000:
            return 0.75, f"large {fmt}; {n} attendees"
        return 0.55, f"mid-size {fmt}; {n} attendees"
    # Leadership / exec roundtable formats: small but high-quality rooms - a rep
    # can meet decision-makers directly even though there is no expo floor.
    if fmt in ("leadership", "roundtable", "executive"):
        return 0.6, f"curated {fmt} format; {n} senior attendees"
    if fmt in ("webinar", "virtual", "online"):
        return 0.2, f"virtual ({fmt}) - no floor"
    # Unknown / missing format: fall back to size alone.
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


# Strategic-wedge tiers. These mirror Grain's go-to-market: travel/booking/
# marketplaces are the LEAD wedge, payments rails are the second wedge, treasury
# is the direct-buyer wedge. An event sitting on a wedge is strategically worth
# more than a generic in-ICP event, independent of audience mix.
_WEDGE_FIT = {
    # lead wedge (travel / platforms) - Grain's stated tip of the spear
    "travel": 0.95, "booking": 0.95, "marketplace": 0.90,
    # rails wedge (payments / cross-border)
    "payments": 0.85, "psp": 0.85, "cross_border_payments": 0.90,
    # direct-buyer wedge (treasury) + adjacent fintech
    "treasury": 0.80, "fintech_other": 0.55, "crypto": 0.45,
    "supply_chain": 0.50,
}


def _icp_strategic_fit(conf: dict) -> tuple[float, str]:
    """Vertical-strategic-fit blended with measurable ICP-company density.

    Replaces the dead `competitive_validation` factor (which scanned event
    metadata for competitor COMPANY names that never appear there, firing 0/195
    and contributing a constant +3.0 that differentiated nothing).

    Two real, differentiating signals:
      - strategic wedge: how central is this event's vertical to Grain's GTM
        (travel/marketplace lead wedge > payments rails > treasury > generic).
      - ICP-company density: the non-"other" share of the audience composition
        is a proxy for how concentrated this room is with ICP-shaped companies
        (a 90%-industry travel event beats a 50%-other generalist expo).
    Every event gets a distinct value, so this 10% of weight now does work.
    """
    v = (conf.get("vertical") or "").lower()
    wedge = _WEDGE_FIT.get(v, 0.30)

    density = None
    ac = conf.get("audience_composition_json")
    if ac:
        try:
            import json as _json
            comp = _json.loads(ac) if isinstance(ac, str) else ac
            other = float(comp.get("other_pct") or 0)
            # ICP-shaped share = everything that isn't the "other / general" bucket.
            density = max(0.0, min(1.0, (100.0 - other) / 100.0))
        except (ValueError, TypeError, AttributeError):
            density = None

    if density is not None:
        # 70% wedge centrality, 30% measured ICP-company density.
        score = round(0.70 * wedge + 0.30 * density, 2)
        return score, (
            f"strategic fit: {v or '?'} wedge ({wedge:.2f}), "
            f"{density * 100:.0f}% ICP-shaped audience"
        )
    return round(wedge, 2), f"strategic fit: {v or '?'} wedge ({wedge:.2f}), audience mix unknown"


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
        return 0.85, f"strong yield: {meetings}/{encs} encounters -> meetings"
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
    weighted: float     # raw * weight * 100 -> contributes to overall
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
    """Compute the 7-factor score. Returns a ConferenceScore with full audit trail.

    `icp_competitors` is accepted for backwards-compatibility but is no longer
    used: the old competitor-name scan was dead weight and is replaced by
    `icp_strategic_fit`, which needs no competitor list.
    """
    if weights is None:
        weights = _live_weights()

    # Weights are RELATIVE EMPHASIS, not absolutes. Normalise them to sum to 1.0
    # before scoring. This keeps three promises the old code quietly broke:
    #   - the score ALWAYS lands on a stable 0..100 scale, no matter how a user
    #     drags a slider (before, dragging a weight to 1.0 pushed totals past 100
    #     and made the A/B/C thresholds meaningless);
    #   - the UI's "normalised when scoring" label is now literally true;
    #   - the DEFAULT weights already sum to 1.0, so normalisation is a no-op for
    #     them — every calibrated score and tier is preserved exactly.
    raw_w = {k: max(0.0, float(weights.get(k, 0.0))) for k in DEFAULT_WEIGHTS}
    w_sum = sum(raw_w.values())
    norm_w = {k: (v / w_sum if w_sum > 0 else DEFAULT_WEIGHTS[k])
              for k, v in raw_w.items()}

    scorers = [
        ("vertical_concentration", _vertical_concentration(conf)),
        ("buyer_reachability", _buyer_reachability(conf)),
        ("fx_exposure_proxy", _fx_exposure_proxy(conf)),
        ("reachability", _reachability(conf)),
        ("geo_cost_efficiency", _geo_cost_efficiency(conf)),
        ("icp_strategic_fit", _icp_strategic_fit(conf)),
        ("historical_yield", _historical_yield(conf)),
    ]

    factors: list[FactorScore] = []
    total = 0.0
    for key, (raw, ev) in scorers:
        w = norm_w.get(key, 0.0)  # normalised emphasis (sums to 1.0)
        weighted = raw * w * 100  # to 0..100 scale
        total += weighted
        factors.append(FactorScore(key=key, raw=raw, weight=w, weighted=weighted, evidence=ev))

    # Tiering is deliberately selective: A is the elite ~top-15% (the genuinely
    # finance-dense rooms worth a booth), C is the clearly off-ICP tail
    # (crypto-retail, consumer fairs, generic SaaS). Travel/marketplace events
    # land as strong, visible B — honest, since a 12%-finance travel expo
    # shouldn't outrank a 70%-finance treasury event.
    tier = "A" if total >= 78 else "B" if total >= 58 else "C"
    return ConferenceScore(total=total, tier=tier, factors=factors)


# ---------------------------------------------------------------------------
# Manual score override (DEFECT 6) — a human's adjusted score must persist
# across rescore. We store the override in the `settings` table (no schema
# change needed) keyed by conference id. rescore_all() honours it: it still
# recomputes the model breakdown (so the evidence stays fresh and explains what
# the model *would* have said) but clamps the persisted score/tier to the human
# value. Clearing the override returns the event to pure model scoring.
# ---------------------------------------------------------------------------
def _override_key(conference_id: str) -> str:
    return f"score_override.{conference_id}"


def set_score_override(conference_id: str, score: float) -> None:
    """Pin a conference's score to a human-set value that survives rescore."""
    db.set_setting(_override_key(conference_id), float(score))


def clear_score_override(conference_id: str) -> None:
    db.set_setting(_override_key(conference_id), "")


def get_score_override(conference_id: str) -> Optional[float]:
    raw = db.get_setting(_override_key(conference_id))
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _tier_for(score: float) -> str:
    return "A" if score >= 78 else "B" if score >= 58 else "C"


# ---------------------------------------------------------------------------
# Learning loop — let accumulated rep OVERRIDES gently auto-tune the weights.
#
# Reps log `conference_score_adjust` feedback whenever they override a model
# score (see api/routers/conferences.py + db.log_feedback). Those overrides are
# the ground truth signal of what reps actually value. This function turns the
# accumulated overrides into a BOUNDED nudge of the 7 factor weights so the model
# slowly learns the team's taste — WITHOUT destabilising the calibrated tier
# distribution.
#
# Attribution model. For each override we have residual = human_after − model_before.
#   - residual > 0 (rep pushed the event UP): the factors that scored STRONG for
#     this event are the ones the model UNDER-weighted → nudge those weights UP,
#     proportional to how much they contributed to the event's model score.
#   - residual < 0 (rep pushed it DOWN): the strong factors are OVER-weighted →
#     nudge them DOWN.
# We credit each factor by its share of the event's weighted score (a factor that
# carried the event gets most of the residual's blame/credit). Summed across all
# overrides this yields one signed signal per factor.
#
# GUARDRAILS (safe by construction — a calibration that wrecks the tiers is a FAIL):
#   (a) require >= MIN_SIGNALS overrides before ANY movement (else exact no-op);
#   (b) each weight moves at most MAX_REL_STEP (20%) relative, per calibration;
#   (c) clamp every weight to [WEIGHT_FLOOR, WEIGHT_CEIL] = [0.02, 0.40];
#   (d) re-normalise the tunable weights so they sum to 1.0 afterwards.
# The result is always a gentle, defensible nudge, never a swing.
# ---------------------------------------------------------------------------
CALIBRATION_MIN_SIGNALS = 3       # (a) no movement below this many overrides
CALIBRATION_MAX_REL_STEP = 0.20   # (b) <= 20% relative move per weight per run
WEIGHT_FLOOR = 0.02               # (c) clamp floor
WEIGHT_CEIL = 0.40                # (c) clamp ceiling

# historical_yield is opt-in and defaults to 0.0; auto-calibration leaves it
# alone (a 0-weight factor can't be credited a residual share, and we never want
# the learner to silently switch on an opt-in factor). Only the 6 active factors
# that sum to 1.0 are tuned + renormalised.
_TUNABLE_FACTORS = [k for k in DEFAULT_WEIGHTS if k != "historical_yield"]


def _read_score_overrides() -> list[dict]:
    """Read every `conference_score_adjust` feedback row, newest first.

    Returns a list of {conference_id, model_before, human_after} dicts. Rows that
    don't carry a usable before/after score are skipped (defensive — the feedback
    table stores JSON blobs we don't fully control).
    """
    import json
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT target_id, before_value, after_value FROM feedback "
            "WHERE decision_kind = 'conference_score_adjust' "
            "ORDER BY decided_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()

    def _load(v):
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                d = json.loads(v)
                return d if isinstance(d, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    out: list[dict] = []
    for r in rows:
        before = _load(r["before_value"])
        after = _load(r["after_value"])
        try:
            model_before = float(before.get("score"))
            human_after = float(after.get("score"))
        except (TypeError, ValueError):
            continue
        out.append({
            "conference_id": r["target_id"],
            "model_before": model_before,
            "human_after": human_after,
        })
    return out


def _conf_by_id(conference_id: str) -> Optional[dict]:
    if not conference_id:
        return None
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM conferences WHERE id = ?", (conference_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def learn_scoring_weights(*, apply: bool = False) -> dict:
    """Turn accumulated rep score-overrides into a BOUNDED weight nudge.

    Reads `conference_score_adjust` feedback, attributes each residual
    (human_after − model_before) to the factors that contributed most to that
    event's model score, aggregates across all overrides, and converts the
    signal into per-factor weight deltas under hard guardrails (see module
    notes above). Returns:

        {
          n_signals: int,                 # usable overrides found
          current_weights: {factor: w},   # live weights now
          proposed_weights: {factor: w},  # nudged + renormalised (== current if no-op)
          per_factor_rationale: {factor: str},
          would_change: bool,             # proposed != current
        }

    With < CALIBRATION_MIN_SIGNALS signals this is a strict NO-OP: proposed ==
    current. `apply` is accepted for symmetry but this function NEVER writes —
    the brain router owns the apply/rescore side so the write path stays in one
    place (HIL-gated). The caller passes the returned `proposed_weights` to the
    existing live-weights/settings mechanism.
    """
    _ = apply  # this function is pure; the router performs the gated write.
    current = _live_weights()
    overrides = _read_score_overrides()
    n_signals = len(overrides)

    # GUARDRAIL (a): below the floor of evidence, do nothing at all.
    if n_signals < CALIBRATION_MIN_SIGNALS:
        return {
            "n_signals": n_signals,
            "current_weights": current,
            "proposed_weights": dict(current),
            "per_factor_rationale": {
                k: f"no movement — only {n_signals} override(s); "
                   f"need >= {CALIBRATION_MIN_SIGNALS}"
                for k in _TUNABLE_FACTORS
            },
            "would_change": False,
        }

    # Aggregate a signed, contribution-weighted residual signal per factor.
    # signal[f] += residual_norm * (factor f's share of THIS event's weighted score)
    # residual is normalised to [-1, 1] by the 100-pt scale so one big override
    # can't dominate; the share weights it toward the factors that carried the event.
    signal = {k: 0.0 for k in _TUNABLE_FACTORS}
    usable = 0
    for ov in overrides:
        conf = _conf_by_id(ov["conference_id"])
        if conf is None:
            continue
        residual = ov["human_after"] - ov["model_before"]
        if abs(residual) < 1e-9:
            continue
        residual_norm = max(-1.0, min(1.0, residual / 100.0))
        # Recompute the model factor contributions for this event (raw * weight).
        s = score_conference(conf, weights=current)
        contribs = {f.key: max(0.0, f.raw * f.weight)
                    for f in s.factors if f.key in _TUNABLE_FACTORS}
        total_contrib = sum(contribs.values())
        if total_contrib <= 0:
            continue
        for k, c in contribs.items():
            share = c / total_contrib
            signal[k] += residual_norm * share
        usable += 1

    # If, after filtering, we have no usable directional signal, NO-OP.
    if usable < CALIBRATION_MIN_SIGNALS or all(abs(v) < 1e-9 for v in signal.values()):
        return {
            "n_signals": n_signals,
            "current_weights": current,
            "proposed_weights": dict(current),
            "per_factor_rationale": {
                k: f"no usable directional signal ({usable} attributable override(s))"
                for k in _TUNABLE_FACTORS
            },
            "would_change": False,
        }

    # Normalise the per-factor signal to [-1, 1] by its own max magnitude so the
    # strongest factor takes the full bounded step and the rest scale down. This
    # keeps the move PROPORTIONAL and bounded regardless of how many overrides.
    max_mag = max(abs(v) for v in signal.values()) or 1.0

    proposed = dict(current)
    rationale: dict[str, str] = {}
    for k in _TUNABLE_FACTORS:
        cur_w = current[k]
        scaled = signal[k] / max_mag                       # in [-1, 1]
        # GUARDRAIL (b): at most MAX_REL_STEP relative move for this weight.
        rel_delta = scaled * CALIBRATION_MAX_REL_STEP       # in [-0.20, 0.20]
        new_w = cur_w * (1.0 + rel_delta)
        # GUARDRAIL (c): clamp absolute weight.
        new_w = max(WEIGHT_FLOOR, min(WEIGHT_CEIL, new_w))
        proposed[k] = new_w
        if scaled > 0.01:
            rationale[k] = (
                f"reps push events strong in {k} UP - nudging weight "
                f"{cur_w:.3f} -> (pre-norm) {new_w:.3f} "
                f"(+{rel_delta * 100:.0f}% rel, signal {scaled:+.2f})"
            )
        elif scaled < -0.01:
            rationale[k] = (
                f"reps push events strong in {k} DOWN - nudging weight "
                f"{cur_w:.3f} -> (pre-norm) {new_w:.3f} "
                f"({rel_delta * 100:.0f}% rel, signal {scaled:+.2f})"
            )
        else:
            rationale[k] = f"~unchanged (signal {scaled:+.2f})"

    # GUARDRAIL (d): re-normalise tunable weights to sum to 1.0 (clamp may have
    # shifted the total). Re-clamp once after normalising in case normalisation
    # pushed a weight just outside the band, then renormalise the remainder. In
    # practice the band is wide enough that one pass settles it.
    def _normalise(weights: dict[str, float]) -> dict[str, float]:
        total = sum(weights[k] for k in _TUNABLE_FACTORS)
        if total <= 0:
            return weights
        out = dict(weights)
        for k in _TUNABLE_FACTORS:
            out[k] = weights[k] / total
        return out

    proposed = _normalise(proposed)
    # Post-normalise clamp + one renormalise (defensive; keeps band invariant).
    clamped = {k: max(WEIGHT_FLOOR, min(WEIGHT_CEIL, proposed[k]))
               for k in _TUNABLE_FACTORS}
    proposed = _normalise(clamped)
    # Carry through any non-tunable factor (historical_yield) unchanged.
    for k in current:
        if k not in _TUNABLE_FACTORS:
            proposed[k] = current[k]

    # Round to a clean precision for storage / display, then absorb the tiny
    # rounding residual into the largest tunable weight so the set still sums to
    # EXACTLY 1.0 (guardrail (d) must hold after rounding, not just before).
    proposed = {k: round(v, 4) for k, v in proposed.items()}
    drift = round(1.0 - sum(proposed[k] for k in _TUNABLE_FACTORS), 4)
    if abs(drift) >= 1e-9:
        biggest = max(_TUNABLE_FACTORS, key=lambda k: proposed[k])
        proposed[biggest] = round(proposed[biggest] + drift, 4)

    would_change = any(
        abs(proposed[k] - current[k]) > 1e-4 for k in current
    )
    return {
        "n_signals": n_signals,
        "current_weights": {k: round(v, 4) for k, v in current.items()},
        "proposed_weights": proposed,
        "per_factor_rationale": rationale,
        "would_change": would_change,
    }


def write_weights(weights: dict[str, float]) -> None:
    """Persist factor weights via the EXISTING live-weights/settings mechanism.

    Writes each `scoring.<factor>` key with db.set_setting — the SAME store that
    `_live_weights()` reads and that `PUT /api/settings` writes. No parallel store.
    """
    for k, v in weights.items():
        if k in DEFAULT_WEIGHTS:
            db.set_setting(f"scoring.{k}", float(v))


def reset_weights_to_default() -> dict[str, float]:
    """Restore the default factor weights (reversible calibration)."""
    write_weights(DEFAULT_WEIGHTS)
    return dict(DEFAULT_WEIGHTS)


def tier_distribution() -> dict[str, int]:
    """Count conferences per persisted tier — used to confirm tiers stay sane."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT tier, COUNT(*) AS n FROM conferences GROUP BY tier"
        ).fetchall()
    finally:
        conn.close()
    out = {"A": 0, "B": 0, "C": 0}
    for r in rows:
        t = r["tier"] or "?"
        out[t] = int(r["n"])
    return out


def rescore_all() -> int:
    """Recompute + persist scores for every conference. Returns count.

    Conferences with a sticky human override (DEFECT 6) keep the overridden
    score/tier; we still refresh their model breakdown so the UI can show what
    the model thinks alongside the human's call.
    """
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
        override = get_score_override(conf["id"])
        if override is not None:
            persisted_score = override
            persisted_tier = _tier_for(override)
            breakdown = s.to_breakdown_dict()
            breakdown["override"] = {
                "score": round(override, 2),
                "model_score": round(s.total, 2),
                "note": "human override - persists across rescore",
            }
            breakdown_json = json.dumps(breakdown, ensure_ascii=False)
        else:
            persisted_score = s.total
            persisted_tier = s.tier
            breakdown_json = json.dumps(s.to_breakdown_dict(), ensure_ascii=False)
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE conferences SET score = ?, tier = ?, "
                "score_breakdown_json = ?, updated_at = ? WHERE id = ?",
                (persisted_score, persisted_tier, breakdown_json,
                 db.now_iso(), conf["id"]),
            )
            n += 1
        finally:
            conn.close()
    return n
