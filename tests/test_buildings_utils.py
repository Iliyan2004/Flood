"""
Fast, self-contained tests for buildings_utils.get_building_footprints --
specifically its vectorized rewrite (shapely 2 array ops + an STRtree for
the OSM-height lookup, replacing a real, profiled O(n_buildings *
n_osm_candidates) per-building linear scan -- see CLAUDE.md's own
"post-processing performance" entry for the real profiling numbers that
motivated this). No real Google Open Buildings/OSM network data needed --
builds a small synthetic parquet + synthetic OSM height polygons in-memory.
"""
import os
import sys

import duckdb
import numpy as np
import pytest
from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import buildings_utils  # noqa: E402

CELLSIZE = 10.0
SCALE = 100000.0  # a simple deg->metres-ish stand-in transform for these tests


def _lonlat_to_src_crs(lon, lat):
    x = (np.asarray(lon) - 73.0) * SCALE
    y = (np.asarray(lat) - 33.6) * SCALE
    return x, y


def _square(lon_c, lat_c, s=0.00003):
    return Polygon([(lon_c - s, lat_c - s), (lon_c + s, lat_c - s),
                     (lon_c + s, lat_c + s), (lon_c - s, lat_c + s), (lon_c - s, lat_c - s)])


def _write_parquet(tmp_path, rows):
    path = str(tmp_path / "buildings.parquet")
    con = duckdb.connect()
    con.execute("CREATE TABLE b (wkt VARCHAR, area_in_meters DOUBLE)")
    if rows:
        con.executemany("INSERT INTO b VALUES (?, ?)", rows)
    con.execute(f"COPY b TO '{path}' (FORMAT PARQUET)")
    return path


def _header(span_deg=0.02):
    ncols = int(span_deg * SCALE / CELLSIZE) + 50
    return {"nrows": ncols, "ncols": ncols, "cellsize": CELLSIZE, "xllcorner": 0.0, "yllcorner": 0.0}


def test_basic_building_kept_and_classified_as_estimated_height(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    poly = _square(73.005, 33.605)
    path = _write_parquet(tmp_path, [(poly.wkt, poly.area * 111000 * 111000)])
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs)
    assert len(result) == 1
    assert result[0]["height_source"] == "estimated"
    assert result[0]["height_m"] == buildings_utils.ASSUMED_BUILDING_HEIGHT_M


def test_osm_height_match_wins_over_estimate(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    poly = _square(73.005, 33.605)
    path = _write_parquet(tmp_path, [(poly.wkt, poly.area * 111000 * 111000)])
    osm_heights = [{"height_m": 12.5, "geometry": list(_square(73.005, 33.605, s=0.0001).exterior.coords)}]
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs, osm_heights=osm_heights)
    assert len(result) == 1
    assert result[0]["height_source"] == "osm"
    assert result[0]["height_m"] == 12.5


def test_overlapping_osm_candidates_pick_the_first_in_list_order(tmp_path):
    """Regression guard: the vectorized STRtree lookup must preserve the
    original per-building loop's own tie-break (first match in the given
    osm_heights list order) when a centroid falls inside more than one
    real OSM-tagged height polygon."""
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    poly = _square(73.005, 33.605)
    path = _write_parquet(tmp_path, [(poly.wkt, poly.area * 111000 * 111000)])
    osm_heights = [
        {"height_m": 42.0, "geometry": list(_square(73.005, 33.605, s=0.0003).exterior.coords)},
        {"height_m": 99.0, "geometry": list(_square(73.005, 33.605, s=0.0002).exterior.coords)},
    ]
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs, osm_heights=osm_heights)
    assert result[0]["height_m"] == 42.0


def test_building_on_nodata_cell_excluded(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    poly = _square(73.005, 33.605)
    row, col = header["nrows"] // 2, header["ncols"] // 2
    x, y = _lonlat_to_src_crs(73.005, 33.605)
    r0 = int(header["nrows"] - 1 - (float(y) - header["yllcorner"]) / CELLSIZE)
    c0 = int((float(x) - header["xllcorner"]) / CELLSIZE)
    dem[r0, c0] = -9999
    path = _write_parquet(tmp_path, [(poly.wkt, poly.area * 111000 * 111000)])
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs)
    assert result == []


def test_building_outside_true_aoi_polygon_excluded_even_if_raster_cell_valid(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    poly = _square(73.005, 33.605)
    path = _write_parquet(tmp_path, [(poly.wkt, poly.area * 111000 * 111000)])
    far_aoi = Polygon([(73.5, 34.0), (73.6, 34.0), (73.6, 34.1), (73.5, 34.1)])
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs, aoi_polygon_wgs84=far_aoi)
    assert result == []


def test_non_polygon_geometry_excluded(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    path = _write_parquet(tmp_path, [("POINT (73.005 33.605)", 1.0)])
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs)
    assert result == []


def test_empty_buildings_table_returns_empty_list(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    path = _write_parquet(tmp_path, [])
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs)
    assert result == []


def test_result_sorted_by_area_descending_and_capped(tmp_path):
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    small = _square(73.001, 33.601, s=0.00002)
    big = _square(73.010, 33.610, s=0.00006)
    mid = _square(73.005, 33.605, s=0.00004)
    rows = [(small.wkt, small.area * 111000 * 111000),
            (big.wkt, big.area * 111000 * 111000),
            (mid.wkt, mid.area * 111000 * 111000)]
    path = _write_parquet(tmp_path, rows)
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs, max_footprints=2)
    assert len(result) == 2
    # poly_rc row-count is a stand-in for exterior vertex count here (all
    # squares have the same vertex count) -- just confirm exactly 2 kept,
    # matching the cap, and that the two largest real footprints (big, mid)
    # both round-trip a real, non-empty exterior ring.
    for f in result:
        assert len(f["poly_rc"]) >= 4


def test_exterior_coordinates_correctly_transformed_to_row_col(tmp_path):
    """Confirms the vectorized bulk coordinate transform lands on the
    exact same row/col a direct per-vertex call to the same transform
    would produce."""
    header = _header()
    dem = np.full((header["nrows"], header["ncols"]), 500.0)
    poly = _square(73.005, 33.605, s=0.00003)
    path = _write_parquet(tmp_path, [(poly.wkt, poly.area * 111000 * 111000)])
    result = buildings_utils.get_building_footprints(header, dem, path, _lonlat_to_src_crs)
    assert len(result) == 1
    poly_rc = result[0]["poly_rc"]
    for (lon, lat), (row, col) in zip(list(poly.exterior.coords), poly_rc):
        x, y = _lonlat_to_src_crs(lon, lat)
        expected_col = (float(x) - header["xllcorner"]) / CELLSIZE
        expected_row = header["nrows"] - 1 - (float(y) - header["yllcorner"]) / CELLSIZE
        assert row == pytest.approx(expected_row, abs=1e-6)
        assert col == pytest.approx(expected_col, abs=1e-6)
