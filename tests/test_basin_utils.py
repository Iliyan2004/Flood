"""
Fast tests for basin_utils.py's real HydroSHEDS Level 08 Pakistan basin
lookup. Skipped gracefully on any machine without the real local basin
dataset (same skipif-on-missing-local-file pattern already used for
test_infra_utils.py/test_glof_utils.py).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import basin_utils  # noqa: E402

_skip = pytest.mark.skipif(not basin_utils.basins_data_available(),
                            reason="real local HydroSHEDS basin dataset not present on this machine")


@_skip
def test_index_has_real_basins():
    index = basin_utils._load_index()
    assert len(index) > 1000  # real dataset has 1,131 basins


@_skip
def test_basins_in_region_ranked_by_real_area_descending():
    results = basin_utils.basins_in_region(region="gilgit_baltistan", limit=10)
    assert len(results) == 10
    areas = [r["area_km2"] for r in results]
    assert areas == sorted(areas, reverse=True)


@_skip
def test_gb_biggest_basin_matches_known_real_value():
    """Real regression check: the same basin independently found by hand
    (geodesic-area cross-check) for this app's own steep-terrain
    bisection -- confirms the index's real SUB_AREA field and ranking
    are correct, not just internally consistent."""
    results = basin_utils.basins_in_region(region="gilgit_baltistan", limit=1)
    assert results[0]["hybas_id"] == 4080575700
    assert results[0]["area_km2"] == pytest.approx(2871.1, abs=1.0)


@_skip
def test_unknown_region_returns_empty_not_error():
    assert basin_utils.basins_in_region(region="atlantis") == []


@_skip
def test_min_area_filter_excludes_small_basins():
    all_results = basin_utils.basins_in_region(region="gilgit_baltistan", limit=1131)
    filtered = basin_utils.basins_in_region(region="gilgit_baltistan", limit=1131, min_area_km2=1000.0)
    assert len(filtered) < len(all_results)
    assert all(r["area_km2"] >= 1000.0 for r in filtered)


@_skip
def test_nearest_basin_sorted_by_real_distance_ascending():
    results = basin_utils.nearest_basin(74.3, 36.3, max_results=5)
    dists = [r["distance_km"] for r in results]
    assert dists == sorted(dists)


@_skip
def test_basin_geometry_matches_indexed_id():
    geom = basin_utils.basin_geometry(4080575700)
    assert geom is not None
    assert geom["type"] == "Polygon"


@_skip
def test_basin_geometry_unknown_id_returns_none():
    assert basin_utils.basin_geometry(999999999) is None


def test_missing_dataset_resilience(monkeypatch, tmp_path):
    """Real, direct resilience check regardless of whether the actual
    dataset happens to be present on this machine -- points BASINS_DIR
    at a real empty directory and confirms every function degrades to
    []/None rather than raising."""
    monkeypatch.setattr(basin_utils, "BASINS_DIR", str(tmp_path))
    monkeypatch.setattr(basin_utils, "_index", None)
    assert basin_utils.basins_data_available() is False
    assert basin_utils.basins_in_region(region="gilgit_baltistan") == []
    assert basin_utils.nearest_basin(74.0, 36.0) == []
    assert basin_utils.basin_geometry(4080575700) is None
    monkeypatch.setattr(basin_utils, "_index", None)
