"""
Fast tests for pluvial_utils.py's real percentile-based flash-flood risk
tiering. `fetch_precipitation_climatology` (a real Open-Meteo network
call) is monkeypatched with a fixed synthetic climatology so the tiering
LOGIC itself is tested deterministically -- the real network call is
exercised instead, live, in `/area/context`'s own end-to-end route
coverage, not duplicated here. `radar_tier_available`'s bbox check needs
no network and is tested directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pluvial_utils  # noqa: E402


FAKE_CLIMATOLOGY = {
    "source": "fake", "n_days": 20 * 365, "mean_daily_mm": 5.0,
    "p90_daily_mm": 30.0, "p95_daily_mm": 50.0, "p99_daily_mm": 80.0,
    "max_daily_mm": 120.0,
}


def _rainfall_context(hourly_mm):
    return {"hourly_precipitation_mm": hourly_mm, "hourly_time": [f"h{i}" for i in range(len(hourly_mm))]}


def test_radar_tier_available_true_inside_conus_bbox():
    assert pluvial_utils.radar_tier_available(35.0, -95.0) is True


def test_radar_tier_available_false_for_pakistan():
    """Real, load-bearing check: Pakistan (lat ~23.5-37.5, lon ~60.5-77.5)
    must never report real MRMS radar coverage -- this is the whole
    reason MRMS was declined outright rather than half-built (see
    CLAUDE.md/pluvial_utils.py's own docstring)."""
    assert pluvial_utils.radar_tier_available(33.7, 73.1) is False  # Islamabad


def test_tier_severe_when_peak_24h_meets_p99(monkeypatch):
    monkeypatch.setattr(pluvial_utils, "fetch_precipitation_climatology", lambda lat, lon, years=20: FAKE_CLIMATOLOGY)
    # 24 consecutive hours totalling >= 80mm (p99)
    ctx = _rainfall_context([4.0] * 24)
    result = pluvial_utils.assess_pluvial_risk(33.7, 73.1, ctx)
    assert result["risk_tier"] == "severe"


def test_tier_warning_when_peak_24h_between_p95_and_p99(monkeypatch):
    monkeypatch.setattr(pluvial_utils, "fetch_precipitation_climatology", lambda lat, lon, years=20: FAKE_CLIMATOLOGY)
    ctx = _rainfall_context([2.5] * 24)  # 60mm total: >= p95 (50), < p99 (80)
    result = pluvial_utils.assess_pluvial_risk(33.7, 73.1, ctx)
    assert result["risk_tier"] == "warning"


def test_tier_watch_when_peak_24h_between_p90_and_p95(monkeypatch):
    monkeypatch.setattr(pluvial_utils, "fetch_precipitation_climatology", lambda lat, lon, years=20: FAKE_CLIMATOLOGY)
    ctx = _rainfall_context([1.5] * 24)  # 36mm total: >= p90 (30), < p95 (50)
    result = pluvial_utils.assess_pluvial_risk(33.7, 73.1, ctx)
    assert result["risk_tier"] == "watch"


def test_tier_none_when_below_p90(monkeypatch):
    monkeypatch.setattr(pluvial_utils, "fetch_precipitation_climatology", lambda lat, lon, years=20: FAKE_CLIMATOLOGY)
    ctx = _rainfall_context([0.1] * 24)  # 2.4mm total: well below p90
    result = pluvial_utils.assess_pluvial_risk(33.7, 73.1, ctx)
    assert result["risk_tier"] == "none"


def test_tier_unknown_when_climatology_fetch_fails(monkeypatch):
    """A real, tolerated failure mode (e.g. Open-Meteo down/rate-limited)
    must report 'unknown' honestly, never silently default to 'none' as
    if the location were confirmed low-risk."""
    monkeypatch.setattr(pluvial_utils, "fetch_precipitation_climatology", lambda lat, lon, years=20: None)
    ctx = _rainfall_context([4.0] * 24)
    result = pluvial_utils.assess_pluvial_risk(33.7, 73.1, ctx)
    assert result["risk_tier"] == "unknown"


def test_rolling_24h_window_ignores_none_values(monkeypatch):
    """A real Open-Meteo response can contain None for a missing hour;
    the rolling-24h sum must skip it rather than crashing or treating it
    as 0-with-a-type-error."""
    monkeypatch.setattr(pluvial_utils, "fetch_precipitation_climatology", lambda lat, lon, years=20: FAKE_CLIMATOLOGY)
    ctx = _rainfall_context([None, 5.0, None] + [3.0] * 21)
    result = pluvial_utils.assess_pluvial_risk(33.7, 73.1, ctx)
    assert result["peak_24h_forecast_mm"] > 0
