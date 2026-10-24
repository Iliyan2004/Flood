"""
Fast, pure-logic tests for glacier_monitoring_utils.py's real, non-Earth-
Engine-dependent pieces. The real SAR offset-tracking pipeline itself
(sar_cumulative_displacement) needs live Earth Engine credentials and
takes real minutes per call -- verified directly against the live EE
backend during development (a real Shishper/Hunza test AOI), not unit-
tested here, matching this module's own established precedent (no prior
test file existed for it either).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import glacier_monitoring_utils as g  # noqa: E402


def test_is_in_pakistan_true_for_real_gilgit_baltistan_aoi():
    # Real Shishper/Hunza area, used throughout this module's own real
    # development/verification testing.
    assert g.is_in_pakistan(74.58, 36.38, 74.64, 36.42) is True


def test_is_in_pakistan_false_for_aoi_clearly_outside():
    assert g.is_in_pakistan(0.0, 0.0, 0.01, 0.01) is False


def test_is_in_pakistan_true_for_aoi_overlapping_the_bbox_edge():
    plon_min, plat_min, plon_max, plat_max = g.PAKISTAN_BBOX
    assert g.is_in_pakistan(plon_min - 1.0, plat_min - 1.0, plon_min + 0.5, plat_min + 0.5) is True


def test_confidence_threshold_default_is_real_softened_value():
    """This module's own real, documented change: softened from the
    original 0.60 to 0.50 after direct real-coverage-vs-noise testing --
    a regression here would silently revert that decision."""
    assert g.SAR_CONFIDENCE_THRESHOLD == 0.5


def test_stable_ground_buffer_is_a_real_positive_distance():
    assert g.STABLE_GROUND_BUFFER_M > 0
