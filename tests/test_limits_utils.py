"""Pakistan-only areas, run-size limit, and the removal of any online terrain fetch."""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import limits_utils as L


def box(lon, lat, km):
    dl = km / 2 / 111.0; dn = dl / math.cos(math.radians(lat))
    return {"type": "Polygon", "coordinates": [[[lon - dn, lat - dl], [lon + dn, lat - dl], [lon + dn, lat + dl], [lon - dn, lat + dl], [lon - dn, lat - dl]]]}


def test_pakistan_cities_accepted():
    for lon, lat in [(74.33, 31.52), (67.0, 24.86), (74.31, 35.92), (73.05, 33.68), (71.5, 34.0), (66.99, 30.18)]:
        assert L.check_in_pakistan(box(lon, lat, 5))[0], (lon, lat)


def test_outside_and_border_crossing_refused():
    for lon, lat, km in [(77.2, 28.6, 5), (69.2, 34.5, 5), (55.3, 25.2, 5), (74.57, 31.60, 20), (-0.1, 51.5, 5)]:
        ok, msg = L.check_in_pakistan(box(lon, lat, km))
        assert not ok and "Pakistan" in msg, (lon, lat)


def test_garbage_geometry_refused():
    assert not L.check_in_pakistan({"type": "Polygon", "coordinates": []})[0]


def test_cell_estimate_matches_grid():
    # 13 km box on the 29 m grid is about 450 x 450 cells
    assert 190_000 < L.estimate_cells(box(68.3, 28.2, 13)) < 215_000


def test_size_limit_depends_on_storm_length():
    cells = 2_900_000
    assert L.check_physics_size(cells, 4.0)["ok"]
    big = L.check_physics_size(cells, 50.0)
    assert not big["ok"] and "reduce the area" in big["message"] and "contact" in big["message"]


def test_hard_cap_message_is_consistent():
    r = L.check_physics_size(16_000_000, 4.0)
    assert not r["ok"] and "more than" in r["message"] and "minutes; the limit" not in r["message"]


def test_sim_hours_follows_longest_driver():
    assert L.sim_hours_for_request(2, None, None) == 4.0
    assert L.sim_hours_for_request(2, [{"duration_hr": 30}], None) == 32.0
    assert L.sim_hours_for_request(2, [{"series": {"times_hr": [0, 10, 70]}}], None) == 72.0
    assert L.sim_hours_for_request(0, None, {"times_hr": [0, 25.75]}) == 27.75


def test_no_online_terrain_code_left():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(here, "global_dem_utils.py"))
    src = open(os.path.join(here, "fis_app.py")).read()
    for word in ("fetch_global_dem_mosaic", "GlobalDemNotEnabled", "use_global_dem", "Copernicus DEM GLO-30, on-demand"):
        assert word not in src, word


def test_area_outside_pakistan_has_no_terrain_source():
    import fis_app
    import pytest
    with pytest.raises(ValueError):
        fis_app.current_dem_source(box(77.2, 28.6, 5))
