"""
Fast, pure-math tests for loss_utils.py's real Huizinga-et-al.-2017-(JRC)
-based monetary loss estimate (PKR). No network/physics -- feeds
synthetic building-feature lists and land-cover/damage arrays directly.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loss_utils  # noqa: E402


def _square_poly_rc(row0, col0, side_cells):
    return [(row0, col0), (row0, col0 + side_cells), (row0 + side_cells, col0 + side_cells), (row0 + side_cells, col0)]


def test_dry_building_contributes_zero_loss():
    features = [{"poly_rc": _square_poly_rc(0, 0, 10), "height_m": 3.0, "damage_fraction": 0.0}]
    result = loss_utils.estimate_building_loss_pkr(features, cellsize_m=1.0)
    assert result["total_loss_pkr"] == 0.0
    assert result["buildings_counted"] == 1


def test_flooded_single_story_building_matches_hand_computed_value():
    # 10x10 cell footprint at cellsize=1m -> 100 m^2 footprint, 1 storey
    # (height 3m == ASSUMED_STORY_HEIGHT_M), damage_fraction=0.5.
    features = [{"poly_rc": _square_poly_rc(0, 0, 10), "height_m": 3.0, "damage_fraction": 0.5}]
    result = loss_utils.estimate_building_loss_pkr(features, cellsize_m=1.0)
    expected_structure = 100.0 * loss_utils.JRC_ASIA_MAX_DAMAGE_PKR_PER_M2["Residential"] * 0.5
    expected_content = expected_structure * loss_utils.CONTENT_TO_STRUCTURE_RATIO
    assert abs(result["structure_loss_pkr"] - expected_structure) < 1e-6
    assert abs(result["content_loss_pkr"] - expected_content) < 1e-6
    assert abs(result["total_loss_pkr"] - (expected_structure + expected_content)) < 1e-6


def test_pkr_value_is_eur_value_times_the_named_rate():
    features = [{"poly_rc": _square_poly_rc(0, 0, 10), "height_m": 3.0, "damage_fraction": 1.0}]
    result = loss_utils.estimate_building_loss_pkr(features, cellsize_m=1.0)
    expected_structure_eur = 100.0 * loss_utils.JRC_ASIA_MAX_DAMAGE_EUR_PER_M2["Residential"]
    assert abs(result["structure_loss_pkr"] - expected_structure_eur * loss_utils.EUR_TO_PKR_RATE) < 1e-3


def test_taller_building_scales_loss_by_storey_count():
    # Same footprint, 3x the height (3 storeys) -> exactly 3x the loss.
    one_story = [{"poly_rc": _square_poly_rc(0, 0, 10), "height_m": 3.0, "damage_fraction": 0.4}]
    three_story = [{"poly_rc": _square_poly_rc(0, 0, 10), "height_m": 9.0, "damage_fraction": 0.4}]
    r1 = loss_utils.estimate_building_loss_pkr(one_story, cellsize_m=2.0)
    r3 = loss_utils.estimate_building_loss_pkr(three_story, cellsize_m=2.0)
    assert abs(r3["total_loss_pkr"] - 3.0 * r1["total_loss_pkr"]) < 1e-6


def test_degenerate_polygon_skipped_not_crashed():
    features = [{"poly_rc": [(0, 0), (0, 1)], "height_m": 3.0, "damage_fraction": 0.5}]  # only 2 points
    result = loss_utils.estimate_building_loss_pkr(features, cellsize_m=1.0)
    assert result["buildings_skipped_no_geometry"] == 1
    assert result["total_loss_pkr"] == 0.0


def test_content_ratio_is_half_of_structure_by_definition():
    features = [{"poly_rc": _square_poly_rc(0, 0, 5), "height_m": 3.0, "damage_fraction": 1.0}]
    result = loss_utils.estimate_building_loss_pkr(features, cellsize_m=3.0)
    assert abs(result["content_loss_pkr"] - 0.5 * result["structure_loss_pkr"]) < 1e-6


def test_no_cropland_returns_zero_not_error():
    lulc = np.full((10, 10), 10)  # all "Tree cover", no cropland (code 40)
    damage = np.full((10, 10), 0.5)
    valid = np.ones((10, 10), dtype=bool)
    result = loss_utils.estimate_agricultural_loss_pkr(damage, lulc, valid, cellsize_m=30.0)
    assert result["agricultural_loss_pkr"] == 0.0
    assert result["cropland_area_km2"] == 0.0


def test_agricultural_loss_matches_hand_computed_value():
    lulc = np.full((4, 4), 40)  # all cropland
    damage = np.full((4, 4), 0.2)
    valid = np.ones((4, 4), dtype=bool)
    cellsize_m = 100.0
    result = loss_utils.estimate_agricultural_loss_pkr(damage, lulc, valid, cellsize_m=cellsize_m)
    n_cells = 16
    expected = n_cells * 0.2 * (cellsize_m ** 2) * loss_utils.JRC_ASIA_MAX_DAMAGE_PKR_PER_M2["Agriculture"]
    assert abs(result["agricultural_loss_pkr"] - expected) < 1e-6
    assert abs(result["cropland_area_km2"] - (n_cells * cellsize_m ** 2 / 1e6)) < 1e-9


def test_agricultural_loss_respects_valid_mask():
    lulc = np.full((4, 4), 40)
    damage = np.full((4, 4), 1.0)
    valid = np.zeros((4, 4), dtype=bool)
    valid[:2, :2] = True  # only a quarter of the grid is real AOI
    result = loss_utils.estimate_agricultural_loss_pkr(damage, lulc, valid, cellsize_m=10.0)
    assert result["cropland_area_km2"] == round(4 * 100 / 1e6, 4)
