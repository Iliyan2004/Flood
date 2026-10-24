import os
import numpy as np
import pytest
import tide_utils as tu

needs_data = pytest.mark.skipif(not tu.available(), reason="Karachi tide-gauge data not on this machine")


def _synthetic_constants():
    """A pure M2 tide of 1.0 m amplitude (cos phase) -- no data needed."""
    sp = dict(tu.CONSTITUENTS)
    return {"epoch": "2007-01-31T10", "period": ["2007-01-31T10", "2016-12-15T12"],
            "constants_cos_sin_m": {"M2": [1.0, 0.0]}, "speeds_deg_per_hr": sp, "msl_above_gauge_zero_m": 1.8}


def test_predict_pure_m2_is_a_cosine_with_the_m2_period():
    c = _synthetic_constants()
    h = np.arange(0, 60.0)
    y = tu.predict_tide_m(h, c)
    assert y[0] == pytest.approx(1.0) and y.max() <= 1.0 + 1e-9 and y.min() >= -1.0 - 1e-9
    period = 360.0 / tu.CONSTITUENTS["M2"]            # 12.42 h
    assert tu.predict_tide_m([period], c)[0] == pytest.approx(1.0, abs=1e-6)
    assert tu.predict_tide_m([period / 2], c)[0] == pytest.approx(-1.0, abs=1e-6)


def test_surge_peak_alignment_matches_requested_tidal_phase():
    c = _synthetic_constants()
    c["constants_cos_sin_m"] = {"M2": [1.0, 0.0], "S2": [0.4, 0.0]}      # spring/neap beating
    _, hi = tu.tide_for_surge_window(97, 48, "spring_high", c)
    _, typ = tu.tide_for_surge_window(97, 48, "typical_high", c)
    _, lo = tu.tide_for_surge_window(97, 48, "low", c)
    assert hi > typ > 0 > lo
    assert hi == pytest.approx(1.4, abs=0.02)                              # M2 + S2 in phase = spring high water


def test_unknown_phase_rejected():
    with pytest.raises(ValueError):
        tu.tide_for_surge_window(10, 5, "nope", _synthetic_constants())


@needs_data
def test_karachi_constants_are_sane_and_beat_the_holdout_check():
    c = tu.load_constants()
    assert 1.7 < c["msl_above_gauge_zero_m"] < 2.0                       # MSL about 1.85 m above gauge zero
    assert 2.0 < c["mean_daily_range_m"] < 2.6                           # matches the 2.3 m Karachi tidal range
    assert c["holdout"]["rmse_m"] < 0.15 and c["holdout"]["corr"] > 0.97
    amps = {k: np.hypot(*v) for k, v in c["constants_cos_sin_m"].items()}
    assert max(amps, key=amps.get) == "M2" and amps["M2"] > amps["S2"] > 0.2


@pytest.mark.skipif(not os.path.exists(tu.GAUGE_CSV), reason="needs the raw Karachi tide-gauge record (not in git)")
def test_prediction_tracks_the_real_record_on_an_unseen_period():
    t, v = tu.read_gauge()
    c = tu.load_constants()
    epoch = np.datetime64(c["epoch"], "h")
    n = 24 * 30
    h = (t[-n:] - epoch).astype("timedelta64[h]").astype(float)
    pred = tu.predict_tide_m(h, c) + c["msl_above_gauge_zero_m"]
    ok = ~np.isnan(v[-n:])
    assert np.sqrt(np.mean((pred[ok] - v[-n:][ok]) ** 2)) < 0.2
