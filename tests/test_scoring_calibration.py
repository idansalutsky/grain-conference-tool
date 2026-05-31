"""Calibration tests — rep score-overrides gently auto-tune the scoring weights.

Safe by construction: no movement below the signal floor, bounded per-factor
step, clamp to [0.02, 0.40], renormalise to sum 1.0, and reversible reset. These
tests run against the conftest temp DB; each seeds its own conferences + override
feedback so they're independent and never touch the live data/grain.db.
"""
from __future__ import annotations

import uuid

from grain import db, scoring


def _mk_conf(vertical: str, name: str, *, region="EU", fmt="summit",
             att=2000, audience=None) -> str:
    """Insert one conference, score it, persist score/tier. Returns its id."""
    import json
    cid = "calib_" + uuid.uuid4().hex[:10]
    row = {
        "id": cid, "name": name, "vertical": vertical,
        "themes": "treasury, cross-border payments, FX risk",
        "region": region, "format": fmt, "estimated_attendance": att,
    }
    if audience is not None:
        row["audience_composition_json"] = json.dumps(audience)
    db.insert_row("conferences", row)
    conf = scoring._conf_by_id(cid)
    s = scoring.score_conference(conf)
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE conferences SET score = ?, tier = ?, score_breakdown_json = ? "
            "WHERE id = ?",
            (s.total, s.tier, json.dumps(s.to_breakdown_dict()), cid),
        )
    finally:
        conn.close()
    return cid


def _clear_overrides() -> None:
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM feedback WHERE decision_kind = 'conference_score_adjust'")
    finally:
        conn.close()


def _override(cid: str, before: float, after: float) -> None:
    db.log_feedback(
        decision_kind="conference_score_adjust",
        target_kind="conference", target_id=cid,
        before={"score": before, "tier": scoring._tier_for(before)},
        after={"score": after, "tier": scoring._tier_for(after),
               "delta": after - before},
        reason="test", decided_by="test",
    )


def test_no_op_when_no_signal():
    """Zero (or < MIN_SIGNALS) overrides -> proposed == current, no movement."""
    _clear_overrides()
    res = scoring.learn_scoring_weights()
    assert res["n_signals"] == 0
    assert res["would_change"] is False
    assert res["proposed_weights"] == res["current_weights"]


def test_below_min_signals_is_no_op():
    """Two overrides (< MIN_SIGNALS=3) must still be a strict no-op."""
    _clear_overrides()
    c1 = _mk_conf("treasury", "Treasury One")
    c2 = _mk_conf("treasury", "Treasury Two")
    _override(c1, 80.0, 90.0)
    _override(c2, 80.0, 90.0)
    res = scoring.learn_scoring_weights()
    assert res["n_signals"] == 2
    assert res["would_change"] is False
    assert res["proposed_weights"] == res["current_weights"]


def test_overrides_up_move_strong_factor_up_bounded_and_normalised():
    """>=3 overrides pushing high-buyer_density events UP nudge that factor
    UP, bounded (<=20% rel pre-norm), clamped to [0.02,0.40], and sum to 1.0."""
    _clear_overrides()
    # Treasury events: buyer_density is one of the strongest factors.
    treasury_audience = {"cfo_treasury_finance_pct": 78,
                         "marketing_sales_pct": 12,
                         "engineering_product_pct": 5, "other_pct": 5}
    ids = [_mk_conf("treasury", f"Treasury Calib {i}", audience=treasury_audience)
           for i in range(4)]
    for cid in ids:
        conf = scoring._conf_by_id(cid)
        before = scoring.score_conference(conf).total
        _override(cid, before, min(100.0, before + 8.0))

    res = scoring.learn_scoring_weights()
    cur, prop = res["current_weights"], res["proposed_weights"]
    assert res["n_signals"] == 4
    assert res["would_change"] is True
    # The strong factor for treasury moves UP.
    assert prop["buyer_density"] > cur["buyer_density"]
    # Bounded pre-norm step on the strongest factor (<= 20% rel, allow renorm slack).
    rel = (prop["buyer_density"] - cur["buyer_density"]) / cur["buyer_density"]
    assert rel <= scoring.CALIBRATION_MAX_REL_STEP + 0.02
    # Clamp + sum-to-1.0 invariants.
    tunable = [k for k in scoring.DEFAULT_WEIGHTS if k != "historical_yield"]
    for k in tunable:
        assert scoring.WEIGHT_FLOOR - 1e-9 <= prop[k] <= scoring.WEIGHT_CEIL + 1e-9
    assert abs(sum(prop[k] for k in tunable) - 1.0) < 1e-6


def test_normalization_holds_after_rounding():
    """The proposed weights must sum to EXACTLY 1.0 even after 4dp rounding."""
    _clear_overrides()
    ids = [_mk_conf("treasury", f"Norm {i}") for i in range(4)]
    for cid in ids:
        conf = scoring._conf_by_id(cid)
        before = scoring.score_conference(conf).total
        _override(cid, before, min(100.0, before + 6.0))
    res = scoring.learn_scoring_weights()
    tunable = [k for k in scoring.DEFAULT_WEIGHTS if k != "historical_yield"]
    total = sum(res["proposed_weights"][k] for k in tunable)
    assert total == 1.0 or abs(total - 1.0) < 1e-9


def test_apply_then_reset_round_trip():
    """write_weights + reset_weights_to_default is reversible (live weights match)."""
    _clear_overrides()
    ids = [_mk_conf("treasury", f"RT {i}") for i in range(4)]
    for cid in ids:
        conf = scoring._conf_by_id(cid)
        before = scoring.score_conference(conf).total
        _override(cid, before, min(100.0, before + 7.0))
    res = scoring.learn_scoring_weights()
    scoring.write_weights(res["proposed_weights"])
    live = scoring._live_weights()
    # A factor actually changed in the live store.
    assert any(abs(live[k] - scoring.DEFAULT_WEIGHTS[k]) > 1e-4 for k in live)
    # Reset restores defaults exactly.
    scoring.reset_weights_to_default()
    live2 = scoring._live_weights()
    for k in scoring.DEFAULT_WEIGHTS:
        assert abs(live2[k] - scoring.DEFAULT_WEIGHTS[k]) < 1e-9
    _clear_overrides()


def test_overrides_down_move_factor_down():
    """Overrides pushing strong-factor events DOWN nudge that factor DOWN."""
    _clear_overrides()
    ids = [_mk_conf("treasury", f"Down {i}") for i in range(4)]
    for cid in ids:
        conf = scoring._conf_by_id(cid)
        before = scoring.score_conference(conf).total
        _override(cid, before, max(0.0, before - 8.0))
    res = scoring.learn_scoring_weights()
    cur, prop = res["current_weights"], res["proposed_weights"]
    assert res["would_change"] is True
    # buyer_density is strong for treasury, so a DOWN push lowers it.
    assert prop["buyer_density"] < cur["buyer_density"]
    _clear_overrides()
