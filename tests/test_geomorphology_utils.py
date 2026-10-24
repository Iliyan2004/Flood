"""
Tests for geomorphology_utils.py's real GLiM lithology lookup and the
real, simplified lithology-x-slope landslide susceptibility rating.
Real-data tests skip gracefully if the local Pakistan GLiM extract
(global_data/fetch_glim_pakistan.py) isn't present on this machine --
same established pattern as test_infra_utils.py/test_soil_utils.py.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geomorphology_utils as gm  # noqa: E402

_glim_skip = pytest.mark.skipif(not gm.glim_pakistan_available(), reason="local GLiM Pakistan extract not present")

# A real, known mountainous Gilgit-Baltistan bbox (Hunza area, granite +
# metasedimentary terrain per real regional geology).
GB_BBOX = (74.5, 36.0, 75.0, 36.5)


def test_lithology_erodibility_table_only_rates_real_rock_classes():
    for xx, cfg in gm.LITHOLOGY_ERODIBILITY.items():
        if xx in ("ig", "wb", "nd"):
            assert cfg["erodibility"] is None
        else:
            assert 1 <= cfg["erodibility"] <= 5


def test_tier_boundaries_are_strict_and_ordered():
    assert gm._tier(gm.SLOPE_LOW_PCT - 0.1, gm.SLOPE_LOW_PCT, gm.SLOPE_HIGH_PCT) == 0
    assert gm._tier(gm.SLOPE_LOW_PCT, gm.SLOPE_LOW_PCT, gm.SLOPE_HIGH_PCT) == 1
    assert gm._tier(gm.SLOPE_HIGH_PCT, gm.SLOPE_LOW_PCT, gm.SLOPE_HIGH_PCT) == 2


def test_landslide_susceptibility_increases_with_both_erodibility_and_slope():
    low_both = gm.landslide_susceptibility(1.0, 5.0)
    high_erod_only = gm.landslide_susceptibility(4.5, 5.0)
    high_slope_only = gm.landslide_susceptibility(1.0, 50.0)
    high_both = gm.landslide_susceptibility(4.5, 50.0)
    order = {"negligible": 0, "low": 1, "moderate": 2, "high": 3}
    assert order[low_both] <= order[high_erod_only]
    assert order[low_both] <= order[high_slope_only]
    assert order[high_both] >= order[high_erod_only]
    assert order[high_both] >= order[high_slope_only]


def test_landslide_susceptibility_none_for_no_real_rock_data():
    assert gm.landslide_susceptibility(None, 30.0) is None
    assert gm.landslide_susceptibility(float("nan"), 30.0) is None


@_glim_skip
def test_fetch_lithology_for_bounds_returns_real_known_classes():
    feats = gm.fetch_lithology_for_bounds(*GB_BBOX)
    assert len(feats) > 0
    xx_codes = {f["xx"] for f in feats}
    assert xx_codes.issubset(set(gm.LITHOLOGY_ERODIBILITY.keys()))
    # Real regional geology: the Karakoram batholith is a real, dominant
    # granite (acid plutonic, "pa") body in this exact area.
    assert "pa" in xx_codes


@_glim_skip
def test_lithology_erodibility_grid_matches_real_rasterized_classes():
    from pyproj import Transformer
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
    x0, y0 = to_utm(GB_BBOX[0], GB_BBOX[1])
    x1, y1 = to_utm(GB_BBOX[2], GB_BBOX[3])
    header = {"ncols": 80, "nrows": 80, "xllcorner": min(x0, x1), "yllcorner": min(y0, y1),
              "cellsize": (max(x0, x1) - min(x0, x1)) / 80.0}
    grid = gm.lithology_erodibility_grid(header, "EPSG:32643")
    assert grid.shape == (80, 80)
    valid = grid[~np.isnan(grid)]
    assert valid.size > 0
    assert set(np.unique(valid)).issubset({1.0, 3.0, 4.0, 5.0})  # real erodibility ratings only


def test_fetch_lithology_missing_file_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(gm, "GLIM_PAKISTAN_GPKG", "/nonexistent/glim.gpkg")
    assert gm.fetch_lithology_for_bounds(*GB_BBOX) == []


def test_lithology_erodibility_grid_missing_file_returns_all_nan(monkeypatch):
    monkeypatch.setattr(gm, "GLIM_PAKISTAN_GPKG", "/nonexistent/glim.gpkg")
    header = {"ncols": 10, "nrows": 10, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 1000.0}
    grid = gm.lithology_erodibility_grid(header, "EPSG:32643")
    assert grid.shape == (10, 10)
    assert np.all(np.isnan(grid))


# ---- real-map landslide-dam assessment ----
import data_paths  # noqa: E402

_lsm_skip = pytest.mark.skipif(not os.path.exists(data_paths.get("LSM_ENSEMBLE_CLASS")),
                               reason="ML susceptibility map not present")
_valley_skip = pytest.mark.skipif(not os.path.exists(data_paths.get("VALLEY_DEPTH_TIF")),
                                  reason="valley depth raster not present")

HUNZA = {"type": "Polygon", "coordinates": [[[74.5, 36.0], [74.6, 36.0], [74.6, 36.1], [74.5, 36.1], [74.5, 36.0]]]}
KARACHI = {"type": "Polygon", "coordinates": [[[67.0, 24.85], [67.1, 24.85], [67.1, 24.95], [67.0, 24.95], [67.0, 24.85]]]}
ISLAMABAD_EDGE = {"type": "Polygon", "coordinates": [[[73.0, 33.68], [73.1, 33.68], [73.1, 33.78], [73.0, 33.78], [73.0, 33.68]]]}


@_lsm_skip
def test_ml_map_rates_karakoram_high_and_shares_sum_to_one():
    r = gm.landslide_dam_assessment(HUNZA)
    assert r["ml"]["high_or_very_high_share"] >= gm.LSM_ELEVATED_SHARE
    assert r["verdict"] == "elevated"
    assert sum(r["ml"]["class_shares"].values()) == pytest.approx(1.0, abs=0.01)


@_lsm_skip
def test_no_verdict_outside_or_barely_inside_ml_coverage():
    assert gm.landslide_dam_assessment(KARACHI)["ml"] is None
    assert gm.landslide_dam_assessment(KARACHI)["verdict"] is None
    edge = gm.landslide_dam_assessment(ISLAMABAD_EDGE)
    assert edge["ml"]["coverage_of_aoi"] < gm.LSM_MIN_COVERAGE
    assert edge["verdict"] is None          # a sliver of coverage must not produce a verdict


@_valley_skip
def test_valley_geometry_is_deeper_in_karakoram_than_on_the_sindh_coast():
    hunza = gm.landslide_dam_assessment(HUNZA)["valley"]["valley_depth_m"]["median"]
    karachi = gm.landslide_dam_assessment(KARACHI)["valley"]["valley_depth_m"]["median"]
    assert hunza > 300 and karachi < 50


def test_assessment_with_all_rasters_missing_is_empty_not_error(monkeypatch):
    for name in ("LSM_ENSEMBLE_CLASS", "LSM_ENSEMBLE_PROB", "CONFINEMENT_TIF", "VALLEY_DEPTH_TIF"):
        monkeypatch.setitem(data_paths.PATHS, name, "/nonexistent/x.tif")
    assert gm.landslide_dam_assessment(HUNZA) == {"ml": None, "valley": None, "verdict": None}
