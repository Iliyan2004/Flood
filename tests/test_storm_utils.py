"""
Fast, pure-math tests for storm_utils.py's real Areal Reduction Factor
(Keers & Wescott 1977 / FSR-FEH).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storm_utils  # noqa: E402


def test_arf_below_min_area_is_exactly_one():
    """Real published guidance: below ~1km^2, ARF is not meaningfully
    different from 1 (no reduction) -- this is a documented, exact
    special case, not just 'close to 1'."""
    assert storm_utils.areal_reduction_factor(area_km2=0.5, duration_hr=2.0) == 1.0
    assert storm_utils.areal_reduction_factor(area_km2=0.001, duration_hr=6.0) == 1.0


def test_arf_decreases_with_area():
    """Real physics this factor encodes: a larger area's storm-average
    depth is reduced more relative to the point value than a smaller
    area's -- ARF should be monotonically non-increasing in area for a
    fixed duration."""
    areas = [2, 10, 50, 200, 800, 2000]
    arfs = [storm_utils.areal_reduction_factor(a, duration_hr=6.0) for a in areas]
    assert all(b <= a + 1e-9 for a, b in zip(arfs, arfs[1:]))


def test_arf_always_in_valid_clamped_range():
    """Documented clamp: ARF in (0.05, 1.0] regardless of how far outside
    the empirical fit's calibration range the inputs go."""
    for area in [1, 5, 50, 500, 5000, 50000]:
        for dur in [0.1, 1, 6, 24, 72]:
            arf = storm_utils.areal_reduction_factor(area, dur)
            assert 0.05 <= arf <= 1.0


def test_arf_crosses_area_band_boundaries_continuously():
    """_arf_a_b switches formula at 20/100/500/1000 km^2 -- real published
    piecewise fits are continuous (or nearly so) at their own band edges;
    a coding bug in one branch's coefficients would show up as a visible
    jump right at a boundary. Checks the ARF doesn't jump by more than a
    small tolerance immediately across each boundary."""
    for boundary in [20, 100, 500, 1000]:
        arf_below = storm_utils.areal_reduction_factor(boundary - 0.5, duration_hr=6.0)
        arf_above = storm_utils.areal_reduction_factor(boundary + 0.5, duration_hr=6.0)
        assert abs(arf_below - arf_above) < 0.05


def test_arf_min_area_constant_matches_module():
    assert storm_utils.ARF_MIN_AREA_KM2 == 1.0
