import datetime as dt

import numpy as np
import pytest
import xarray as xr
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform as shp_transform

import itslive_utils as it


def _feature(valid=60, days=10, with_data=True):
    f = {"properties": {"percent_valid_pixels": valid, "date_dt": days}, "assets": {}}
    if with_data:
        f["assets"]["data"] = {"href": "https://example.invalid/x.nc"}
    return f


def test_usable_filters_by_validity_pair_length_and_asset():
    assert it._usable(_feature())
    assert not it._usable(_feature(valid=10))
    assert not it._usable(_feature(days=2)) and not it._usable(_feature(days=90))
    assert not it._usable(_feature(with_data=False))


def test_shift_year_handles_leap_day():
    assert it._shift_year(dt.datetime(2028, 2, 29), 1) == dt.datetime(2027, 2, 28)
    assert it._shift_year(dt.datetime(2026, 10, 12), 2) == dt.datetime(2024, 10, 12)


def test_granule_stats_measures_only_inside_glacier_polygon(tmp_path):
    x = 400000.0 + 120.0 * np.arange(60); y = 4000000.0 - 120.0 * np.arange(50)
    v = np.full((50, 60), 500.0)
    gl_utm = box(400000 + 120 * 10 + 60, 4000000 - 120 * 40 + 60, 400000 + 120 * 30 + 60, 4000000 - 120 * 10 + 60)  # edges between cell centres
    inside = (x[None, :] > gl_utm.bounds[0]) & (x[None, :] < gl_utm.bounds[2]) & (y[:, None] > gl_utm.bounds[1]) & (y[:, None] < gl_utm.bounds[3])
    v[inside] = 100.0
    ds = xr.Dataset({"v": (("y", "x"), v), "v_error": (("y", "x"), np.full_like(v, 5.0)), "mapping": ((), 0)}, coords={"x": x, "y": y})
    ds["mapping"].attrs["utm_zone_number"] = 43.0
    ds.attrs["spatial_epsg"] = 32643
    path = tmp_path / "g.nc"; ds.to_netcdf(path)
    to_ll = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform
    glac = shp_transform(to_ll, gl_utm)
    aoi_ll = shp_transform(to_ll, box(400000, 4000000 - 120 * 50, 400000 + 120 * 60, 4000000))
    st = it.granule_stats(str(path), glac, aoi_ll.bounds)
    assert st["median"] == pytest.approx(100.0) and st["mean"] == pytest.approx(100.0)
    assert st["valid_frac"] == pytest.approx(1.0) and st["mean_error"] == pytest.approx(5.0)
    assert st["n_glacier_cells"] > 300


def _fake_run(monkeypatch, recent, baseline):
    """recent/baseline: lists of (median, valid_frac, platform). Returns analyse_aoi's result without network."""
    from shapely.geometry import box as _box
    monkeypatch.setattr(it, "glacier_geometry_wgs84", lambda g: _box(74.4, 36.3, 74.5, 36.4))
    state = {"calls": 0}

    def fake_search(bbox, start, end, max_items=100):
        label = "recent" if start.year == 2026 else "base"
        rows = recent if label == "recent" else baseline
        return [{"properties": {"percent_valid_pixels": 80, "date_dt": 12, "platform": pl, "mid_datetime": f"{start.year}-09-{10 + i:02d}T00:00:00Z",
                                "_m": m, "_v": v}, "assets": {"data": {"href": f"x{start.year}{i}"}}} for i, (m, v, pl) in enumerate(rows)]

    monkeypatch.setattr(it, "search_granules", fake_search)
    monkeypatch.setattr(it, "download_granule", lambda f: f)
    monkeypatch.setattr(it, "granule_stats", lambda f, g, b, want_grid=False: {"epsg": 32643, "cell_m": 120, "n_glacier_cells": 1000, "n_valid": 500,
                                                                               "valid_frac": f["properties"]["_v"], "median": f["properties"]["_m"], "mean": f["properties"]["_m"], "p95": 1.0})
    geom = {"type": "Polygon", "coordinates": [[[74.4, 36.3], [74.5, 36.3], [74.5, 36.4], [74.4, 36.4], [74.4, 36.3]]]}
    return it.analyse_aoi(geom, end_date="2026-10-01T00:00:00", recent_days=30, baseline_years=1, max_per_window=5)


def test_flag_marked_speedup_with_enough_comparable_pairs(monkeypatch):
    r = _fake_run(monkeypatch, [(90, .8, "S2B")] * 4, [(50, .8, "S2B")] * 4)
    assert r["flag"] == "marked speed-up" and r["speedup_ratio"] == pytest.approx(1.8) and "flag_caution" not in r


def test_flag_possible_and_no_speedup(monkeypatch):
    assert _fake_run(monkeypatch, [(65, .8, "S2B")] * 3, [(50, .8, "S2B")] * 3)["flag"] == "possible speed-up"
    assert _fake_run(monkeypatch, [(52, .8, "S2B")] * 3, [(50, .8, "S2B")] * 3)["flag"] == "no speed-up"


def test_too_few_pairs_is_inconclusive_not_a_warning(monkeypatch):
    r = _fake_run(monkeypatch, [(100, .8, "S2B")] * 4, [(50, .8, "S2B")] * 2)
    assert r["flag"].startswith("inconclusive") and r["flag_raw"] == "marked speed-up"


def test_sensor_and_coverage_mismatch_is_cautioned_and_low_coverage_pairs_skipped(monkeypatch):
    r = _fake_run(monkeypatch, [(90, .9, "S1D")] * 3, [(50, .12, "LC08")] * 3 + [(10, .02, "LC08")])
    assert "different sensors" in r["flag_caution"] and "tracked differs" in r["flag_caution"]
    assert any("skipped" in e for e in r["errors"])
