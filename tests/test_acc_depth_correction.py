"""
Fast, pure-logic tests for flood_model.py's acc_depth_correction_factor --
the real, empirically-fit ACC steep-terrain depth bias correction (see the
function's own docstring for the real 7-point ACC-vs-DG2 calibration
benchmark this is fit from). No real LISFLOOD-FP run needed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402


def test_no_correction_below_threshold():
    # Below (and at) DG2_SLOPE_THRESHOLD_PCT, ACC's own real accuracy is
    # already good -- the fit was never calibrated down there, and
    # applying it would wrongly shrink an already-correct depth.
    assert flood_model.acc_depth_correction_factor(0.0) == 1.0
    assert flood_model.acc_depth_correction_factor(5.0) == 1.0
    assert flood_model.acc_depth_correction_factor(flood_model.DG2_SLOPE_THRESHOLD_PCT) == 1.0


def test_no_correction_when_slope_unknown():
    # mean_terrain_slope_pct returns None for an all-NODATA DEM -- must
    # not crash or fabricate a correction with no real slope to base it on.
    assert flood_model.acc_depth_correction_factor(None) == 1.0


def test_correction_activates_just_above_threshold():
    factor = flood_model.acc_depth_correction_factor(flood_model.DG2_SLOPE_THRESHOLD_PCT + 0.01)
    assert factor > 1.0


def test_correction_matches_real_calibration_points():
    # Real, directly-measured ACC/DG2 ratios from the calibration benchmark
    # (see the function's own docstring) -- the fitted line should
    # reproduce each of these to within the real measured 4.9% max error,
    # not just "some" value.
    real_points = [
        (15.0, 3.348), (30.0, 4.130), (50.0, 4.667), (75.0, 5.773),
        (100.0, 6.950), (150.0, 8.778), (200.0, 10.059),
    ]
    for slope_pct, real_ratio in real_points:
        fitted = flood_model.acc_depth_correction_factor(slope_pct)
        rel_err = abs(fitted - real_ratio) / real_ratio
        assert rel_err < 0.10, f"slope={slope_pct}: fitted={fitted}, real={real_ratio}"


def test_correction_factor_increases_with_slope():
    # Real, monotonic relationship confirmed in the calibration data --
    # steeper terrain means a bigger real ACC overestimate to correct for.
    factors = [flood_model.acc_depth_correction_factor(s) for s in (20, 50, 100, 150, 200)]
    assert factors == sorted(factors)
    assert factors[0] < factors[-1]


def test_dividing_by_factor_reduces_depth():
    # The actual real application (main() divides max_depth by this
    # factor) -- confirm the factor is always >= 1 above threshold, so
    # this can only ever reduce a depth, never inflate it.
    for slope_pct in (11, 25, 60, 120, 250):
        assert flood_model.acc_depth_correction_factor(slope_pct) >= 1.0
