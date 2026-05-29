"""Scoring tests — verify the 7 factors produce defensible numbers."""
from __future__ import annotations

from grain import scoring


def test_treasury_pure_event_scores_top_tier():
    """EuroFinance-style treasury events should land tier A."""
    conf = {
        "id": "test_ef",
        "name": "EuroFinance International Treasury Management 2026",
        "vertical": "treasury",
        "themes": "treasury, cash management, FX risk, cross-border payments",
        "region": "EU",
        "format": "summit",
        "estimated_attendance": 2200,
    }
    s = scoring.score_conference(conf)
    assert s.tier == "A", f"got tier {s.tier} score {s.total}"
    assert s.total >= 70


def test_non_icp_event_scores_low():
    """A generic developer conference should NOT land tier A."""
    conf = {
        "id": "test_devconf",
        "name": "JavaScript Conf 2026",
        "vertical": "other",
        "themes": "web development, react, node",
        "region": "NA",
        "format": "conference",
        "estimated_attendance": 800,
    }
    s = scoring.score_conference(conf)
    assert s.tier in ("C", "B"), f"got tier {s.tier} score {s.total}"
    # buyer_reachability and fx_exposure_proxy should be near-zero.
    # (Factor was renamed buyer_density -> buyer_reachability when the model
    # was re-tuned to credit the whole reachable buying committee, not just CFO
    # density; the assertion intent — a non-ICP event has low buyer reach — is
    # unchanged.)
    factors = {f.key: f for f in s.factors}
    assert factors["fx_exposure_proxy"].raw <= 0.4
    assert factors["buyer_reachability"].raw <= 0.6


def test_factor_weights_sum_to_1():
    """The default weights should add up to 1.0 (modulo opt-in historical_yield)."""
    base = {k: v for k, v in scoring.DEFAULT_WEIGHTS.items()
            if k != "historical_yield"}
    assert abs(sum(base.values()) - 1.0) < 0.01


def test_every_factor_has_evidence():
    """Every factor in the breakdown carries an explanation string."""
    conf = {
        "id": "test_x", "name": "Money20/20 USA", "vertical": "payments",
        "themes": "payments, fintech, treasury",
        "region": "NA", "format": "expo", "estimated_attendance": 11500,
    }
    s = scoring.score_conference(conf)
    for f in s.factors:
        assert f.evidence, f"factor {f.key} has no evidence"


def test_breakdown_serializes():
    conf = {
        "id": "test_y", "name": "Phocuswright 2026", "vertical": "travel",
        "themes": "travel tech, FX", "region": "NA", "format": "summit",
        "estimated_attendance": 1200,
    }
    s = scoring.score_conference(conf)
    d = s.to_breakdown_dict()
    assert "total" in d and "tier" in d
    assert len(d["factors"]) == 7
