"""Arc classifier — deterministic verdicts cover the 4 cases."""
from __future__ import annotations

from grain.arc import _features, _deterministic_verdict


def _enc(captured_at: str, sentiment: int, meeting: bool, signals: list[str], discussed: str = ""):
    import json
    return {
        "captured_at": captured_at,
        "sentiment": sentiment,
        "meeting_requested": 1 if meeting else 0,
        "structured": {"what_discussed": discussed},
        "soft_signals": signals,
    }


def test_zero_encounters_is_flat():
    f = _features([])
    v = _deterministic_verdict(f)
    assert v.kind == "flat"


def test_one_encounter_is_flat():
    f = _features([_enc("2026-01-15T00:00:00+00:00", 4, True, ["wants_meeting"])])
    v = _deterministic_verdict(f)
    assert v.kind == "flat"
    assert "not enough history" in v.summary


def test_tire_kicker_long_window_no_meeting_lukewarm():
    encs = [
        _enc("2025-01-01T00:00:00+00:00", 3, False, ["lukewarm"]),
        _enc("2025-04-01T00:00:00+00:00", 3, False, ["lukewarm"]),
        _enc("2025-09-01T00:00:00+00:00", 3, False, ["lukewarm"]),
        _enc("2026-01-01T00:00:00+00:00", 3, False, ["dismissive"]),
    ]
    v = _deterministic_verdict(_features(encs))
    assert v.kind == "tire_kicker"


def test_warming_positive_trend_with_meeting():
    encs = [
        _enc("2025-09-01T00:00:00+00:00", 2, False, []),
        _enc("2025-12-01T00:00:00+00:00", 4, True, ["wants_meeting"]),
        _enc("2026-03-01T00:00:00+00:00", 5, True, ["wants_meeting", "explicit_pain"]),
    ]
    v = _deterministic_verdict(_features(encs))
    assert v.kind == "warming"


def test_cooling_negative_trend():
    encs = [
        _enc("2025-09-01T00:00:00+00:00", 5, True, ["wants_meeting"]),
        _enc("2025-12-01T00:00:00+00:00", 3, False, []),
        _enc("2026-03-01T00:00:00+00:00", 2, False, ["dismissive"]),
    ]
    v = _deterministic_verdict(_features(encs))
    assert v.kind == "cooling"
