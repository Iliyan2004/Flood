"""
Fast, pure-logic tests for storm_motion_lstm.py -- the one real,
already-shipped module this app imports (fis_app.py's real-time storm-
motion-nowcasting checkbox) that had zero test coverage at all, unlike
every other module in this app. No GPU/training/network/Earth-Engine
needed for these: `_lat_km_per_deg_lon`, `_contiguous_runs`,
`build_sequences`, `day_level_split`, and `displacement_error_km` are all
pure data/math, testable directly against small synthetic track data.
`predict_next_displacement`/`predict_storm_velocity_for_aoi` need a real
trained model file and/or live Earth Engine access respectively -- left
untested here the same way this app's other LSTM-adjacent inference
paths (streamflow, glacier SAR) are verified live rather than unit
tested, not because they're less important, but because a fast test
can't meaningfully exercise them without that real infrastructure.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storm_motion_lstm  # noqa: E402


def test_lat_km_per_deg_lon_at_equator_matches_lat_constant():
    # cos(0) == 1, so at the equator a degree of longitude is exactly as
    # many km as a degree of latitude, by this same spherical approximation.
    assert storm_motion_lstm._lat_km_per_deg_lon(0.0) == pytest.approx(storm_motion_lstm.KM_PER_DEG_LAT)


def test_lat_km_per_deg_lon_shrinks_toward_the_poles():
    km_30 = storm_motion_lstm._lat_km_per_deg_lon(30.0)
    km_60 = storm_motion_lstm._lat_km_per_deg_lon(60.0)
    assert 0 < km_60 < km_30 < storm_motion_lstm.KM_PER_DEG_LAT


def _pt(t_min, lat, lon):
    return {"t_epoch_ms": int(t_min * 60000), "lat": lat, "lon": lon}


def test_contiguous_runs_splits_on_a_real_missing_half_hour_gap():
    """A real gap (a half-hour with no cell above the extraction
    threshold, per this module's own docstring) must never be bridged --
    doing so would silently imply a much larger real time step than
    STEP_MIN actually elapsed."""
    points = [
        _pt(0, 30.0, 70.0), _pt(30, 30.1, 70.1), _pt(60, 30.2, 70.2),  # one real contiguous run
        _pt(150, 31.0, 71.0), _pt(180, 31.1, 71.1),                     # a gap (120min, not 30), then a new run
    ]
    runs = storm_motion_lstm._contiguous_runs(points)
    assert len(runs) == 2
    assert len(runs[0]) == 3
    assert len(runs[1]) == 2


def test_contiguous_runs_drops_isolated_single_point_runs():
    """A run of length 1 has zero real displacement steps to offer and
    must be dropped, not kept as a degenerate run."""
    points = [_pt(0, 30.0, 70.0), _pt(90, 32.0, 72.0)]  # two isolated points, no real pair 30min apart
    runs = storm_motion_lstm._contiguous_runs(points)
    assert runs == []


def test_contiguous_runs_handles_empty_input():
    assert storm_motion_lstm._contiguous_runs([]) == []


def _synthetic_track(day_offset_min, n_steps, lat0=30.0, lon0=70.0, dlat=0.05, dlon=0.03):
    """One real contiguous run of n_steps+1 points, STEP_MIN apart,
    moving at a constant real (dlat, dlon) per step -- day_offset_min
    just keeps different synthetic tracks' timestamps from overlapping,
    it has no other real meaning."""
    points = []
    for i in range(n_steps + 1):
        points.append(_pt(day_offset_min + i * storm_motion_lstm.STEP_MIN,
                           lat0 + i * dlat, lon0 + i * dlon))
    return {"points": points}


def test_build_sequences_shapes_and_real_window_content():
    window = storm_motion_lstm.WINDOW
    track = _synthetic_track(0, n_steps=window + 3)  # enough real steps for >1 sample
    X, y, day_idx = storm_motion_lstm.build_sequences([track], window=window)
    n_disp = window + 3  # one displacement per consecutive pair
    expected_n = n_disp - window
    assert X.shape == (expected_n, window, 2)
    assert y.shape == (expected_n, 2)
    assert len(day_idx) == expected_n
    assert set(day_idx.tolist()) == {0}
    # every real displacement step here is the same constant (dlat, dlon)
    assert X[0][0] == pytest.approx([0.05, 0.03])
    assert y[0] == pytest.approx([0.05, 0.03])


def test_build_sequences_skips_a_track_too_short_for_one_real_window():
    window = storm_motion_lstm.WINDOW
    short_track = _synthetic_track(0, n_steps=window - 1)  # not even window+1 real displacement steps
    X, y, day_idx = storm_motion_lstm.build_sequences([short_track], window=window)
    assert len(X) == 0 and len(y) == 0 and len(day_idx) == 0


def test_build_sequences_assigns_correct_day_index_across_multiple_tracks():
    window = storm_motion_lstm.WINDOW
    t0 = _synthetic_track(0, n_steps=window + 2)
    t1 = _synthetic_track(100000, n_steps=window + 2, lat0=25.0, lon0=68.0)
    X, y, day_idx = storm_motion_lstm.build_sequences([t0, t1], window=window)
    assert set(day_idx.tolist()) == {0, 1}
    assert (day_idx == 0).sum() == (day_idx == 1).sum()  # both tracks are the same real length here


def test_day_level_split_every_window_from_a_day_goes_entirely_to_one_side():
    """The real, load-bearing generalization guarantee this module's own
    docstring claims: no day is split across train and test."""
    day_idx = np.array([0, 0, 0, 1, 1, 2, 2, 2, 2, 3])
    train_mask, test_mask, test_days = storm_motion_lstm.day_level_split(day_idx, n_days=4, test_frac=0.25, seed=42)
    assert not np.any(train_mask & test_mask)  # mutually exclusive
    assert np.all(train_mask | test_mask)      # every window is on exactly one side
    for day in range(4):
        this_day = (day_idx == day)
        # every window for a given day is uniformly in train XOR uniformly in test
        assert len(set(train_mask[this_day].tolist())) == 1


def test_day_level_split_is_reproducible_with_the_same_seed():
    day_idx = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    r1 = storm_motion_lstm.day_level_split(day_idx, n_days=10, test_frac=0.2, seed=123)
    r2 = storm_motion_lstm.day_level_split(day_idx, n_days=10, test_frac=0.2, seed=123)
    assert r1[2] == r2[2]  # identical real test_days set


def test_day_level_split_always_holds_out_at_least_one_day():
    day_idx = np.array([0, 1, 2])
    _, _, test_days = storm_motion_lstm.day_level_split(day_idx, n_days=3, test_frac=0.01, seed=1)
    assert len(test_days) >= 1


def test_displacement_error_km_zero_when_prediction_matches_truth():
    pred = [(0.05, 0.03)]
    true = [(0.05, 0.03)]
    errs = storm_motion_lstm.displacement_error_km(pred, true, lats_at_step=[30.0])
    assert errs[0] == pytest.approx(0.0, abs=1e-9)


def test_displacement_error_km_hand_computed_value_at_the_equator():
    """At the equator, km_per_lon == KM_PER_DEG_LAT (cos(0)=1), so a pure
    dlat-only error is a simple, exactly hand-computable distance."""
    pred = [(0.1, 0.0)]
    true = [(0.0, 0.0)]
    errs = storm_motion_lstm.displacement_error_km(pred, true, lats_at_step=[0.0])
    expected = 0.1 * storm_motion_lstm.KM_PER_DEG_LAT
    assert errs[0] == pytest.approx(expected, rel=1e-6)


def test_displacement_error_km_uses_each_samples_own_real_local_latitude():
    """A real, load-bearing check: the SAME (pred, true) lon-only error
    must come out smaller at high latitude than at the equator, since
    longitude degrees compress toward the poles -- confirming this
    function samples each row's own real lat, not one fixed constant."""
    pred = [(0.0, 0.1), (0.0, 0.1)]
    true = [(0.0, 0.0), (0.0, 0.0)]
    errs = storm_motion_lstm.displacement_error_km(pred, true, lats_at_step=[0.0, 60.0])
    assert errs[1] < errs[0]
