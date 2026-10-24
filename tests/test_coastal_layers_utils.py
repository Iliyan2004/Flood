import numpy as np
import pytest

import coastal_layers_utils as cl
import coastal_utils as cu

needs_gtsm = pytest.mark.skipif(not cl.gtsm_available(), reason="local GTSM return-level table missing")


@needs_gtsm
def test_gtsm_has_42_stations_and_levels_increase_with_return_period():
    st = cl.gtsm_stations()
    assert len(st) == 42
    for s in st.values():
        v = [s["levels"][r] for r in sorted(s["levels"])]
        assert v == sorted(v)


@needs_gtsm
def test_nearest_station_is_karachi_for_karachi_point():
    st = cl.nearest_gtsm_station(67.0, 24.8)
    assert st["station_index"] == 16942 and st["distance_km"] < 10


@needs_gtsm
def test_gtsm_log_interpolation_and_extrapolation_flag():
    st = cl.nearest_gtsm_station(67.0, 24.8)
    v10, e10 = cl.gtsm_return_level(st, 10)
    assert v10 == pytest.approx(st["levels"][10]) and not e10
    v7, _ = cl.gtsm_return_level(st, 7)
    assert st["levels"][5] < v7 < st["levels"][10]
    v500, e500 = cl.gtsm_return_level(st, 500)
    assert e500 and v500 > st["levels"][100]
    assert cl.gtsm_return_level(st, 1)[0] == pytest.approx(st["levels"][2])


@needs_gtsm
def test_total_water_level_source_switch_and_slr():
    k = cu.total_water_level_m(100, source="khan")
    g = cu.total_water_level_m(100, source="gtsm", lon=67.0, lat=24.8)
    assert k["source"] == "khan" and g["source"] == "gtsm"
    assert g["total_water_level_m"] < k["total_water_level_m"]  # GTSM slightly lower; both now above MSL
    g2 = cu.total_water_level_m(100, source="gtsm", lon=67.0, lat=24.8, extra_slr_m=0.5)
    assert g2["total_water_level_m"] == pytest.approx(g["total_water_level_m"] + 0.5, abs=1e-3)
    assert cu.total_water_level_m(100, extra_slr_m=0.25)["sea_level_rise_added_m"] == pytest.approx(0.25)


def test_gtsm_source_falls_back_to_khan_without_coordinates():
    assert cu.total_water_level_m(100, source="gtsm")["source"] == "khan"


def test_sea_mask_seeds_flooding_that_edge_seeding_misses():
    dem = np.full((20, 20), 5.0); dem[10, 10:14] = 0.5  # a low pocket far from the edges, above 0 m
    valid = np.ones_like(dem, bool)
    plain = cu.static_coastal_inundation(dem, valid, 1.0)
    assert plain["flooded_cells"] == 0
    sea = np.zeros_like(valid); sea[10, 10] = True
    seeded = cu.static_coastal_inundation(dem, valid, 1.0, sea_mask=sea)
    assert seeded["flooded_cells"] == 4


def test_level_library_is_monotonic():
    dem = np.tile(np.linspace(-1, 6, 40), (30, 1)); valid = np.ones_like(dem, bool)
    sea = np.zeros_like(valid); sea[:, 0] = True
    rows = cl.level_library(dem, valid, [0.5, 1.5, 3.0, 5.0], sea, 30.0)
    areas = [r["area_km2"] for r in rows]
    assert areas == sorted(areas) and areas[-1] > areas[0]


def test_susceptibility_classes_rank_low_flat_ground_above_high_ground(monkeypatch):
    monkeypatch.setattr(cl, "distance_to_sea_km", lambda h: None)
    monkeypatch.setattr(cl, "vegetation_buffer_for_grid", lambda h: None)
    dem = np.tile(np.linspace(0.5, 30, 50), (10, 1)); valid = np.ones_like(dem, bool)
    s = cl.coastal_susceptibility(dem, valid, {"cellsize": 30.0, "src_crs": "EPSG:32642"})
    assert s["factors_used"] == ["elevation", "slope"]
    assert s["classes"][:, 0].min() >= s["classes"][:, -1].max()
    assert s["classes"][:, 0].max() == 5 and s["classes"][:, -1].min() <= 2
    assert sum(s["weights_used"].values()) == pytest.approx(1.0, abs=0.01)


def test_khan_levels_are_converted_from_gauge_zero_to_mean_sea_level():
    """Regression: 3.76 m is measured from the Karachi gauge zero, not from MSL (datum fix, 14 Oct 2026)."""
    import tide_utils
    k = cu.total_water_level_m(100, source="khan")
    assert k["total_water_level_m"] == pytest.approx(3.76 - tide_utils.msl_above_gauge_zero_m(), abs=1e-3)
    assert 1.8 < k["total_water_level_m"] < 2.0
    g = cu.total_water_level_m(100, source="gtsm", lon=67.0, lat=24.8)
    if g["source"] == "gtsm":
        assert abs(g["total_water_level_m"] - k["total_water_level_m"]) < 0.25   # the two independent sources now agree
