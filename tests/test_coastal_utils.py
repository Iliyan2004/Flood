"""
Fast, pure-math tests for coastal_utils.py's real Stockdon et al. 2006
wave run-up and DEM-derived foreshore slope estimate. No network
dependency (fetch_real_wave_conditions itself is a thin, deterministic
wrapper around a real live API call and is exercised, along with the
full end-to-end route wiring, in test_live_routes.py instead).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coastal_utils  # noqa: E402


def test_runup_increases_with_wave_height():
    r_small = coastal_utils.wave_runup_stockdon_m(0.5, 8.0, 0.03)
    r_big = coastal_utils.wave_runup_stockdon_m(2.5, 8.0, 0.03)
    assert r_big > r_small


def test_runup_increases_with_slope():
    """Steeper (reflective) beaches produce more run-up than gentle
    (dissipative) ones for the same real wave -- the core physical
    relationship Stockdon et al. 2006 encodes."""
    r_gentle = coastal_utils.wave_runup_stockdon_m(1.5, 8.0, 0.02)
    r_steep = coastal_utils.wave_runup_stockdon_m(1.5, 8.0, 0.10)
    assert r_steep > r_gentle


def test_runup_zero_for_zero_wave_height():
    assert coastal_utils.wave_runup_stockdon_m(0.0, 8.0, 0.03) == 0.0


def test_runup_never_negative_for_valid_inputs():
    for h in [0.1, 1.0, 3.0]:
        for t in [4.0, 8.0, 14.0]:
            for slope in [0.01, 0.05, 0.15]:
                assert coastal_utils.wave_runup_stockdon_m(h, t, slope) >= 0.0


def test_foreshore_slope_estimate_matches_synthetic_uniform_ramp():
    """A real, exactly-known synthetic 'beach' (elevation rises 1 m per
    10 m cell inland from the shore) must recover very close to the true
    0.1 slope -- confirms the DEM-transect walker measures real rise/run
    correctly, not just monotonically."""
    dem = np.zeros((10, 10), dtype=np.float32)
    for row in range(10):
        dem[row, :] = row * 1.0
    valid = np.ones_like(dem, dtype=bool)
    slope = coastal_utils.estimate_foreshore_slope_from_dem(dem, valid, water_level_m=0.5, cellsize=10.0)
    assert slope is not None
    assert abs(slope - 0.1) < 0.02


def test_foreshore_slope_returns_none_for_all_dry_or_all_wet_dem():
    """No real shore transition exists in an AOI that's entirely above
    (or entirely below) the given water level -- must return None (the
    documented signal to fall back to DEFAULT_FORESHORE_SLOPE), never a
    fabricated number."""
    dry_dem = np.full((10, 10), 50.0, dtype=np.float32)
    valid = np.ones_like(dry_dem, dtype=bool)
    assert coastal_utils.estimate_foreshore_slope_from_dem(dry_dem, valid, water_level_m=0.5, cellsize=10.0) is None

    wet_dem = np.full((10, 10), -1.0, dtype=np.float32)
    assert coastal_utils.estimate_foreshore_slope_from_dem(wet_dem, valid, water_level_m=0.5, cellsize=10.0) is None


def test_default_foreshore_slope_is_a_real_documented_value():
    assert 0.0 < coastal_utils.DEFAULT_FORESHORE_SLOPE < 1.0
