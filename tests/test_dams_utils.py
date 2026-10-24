"""
Tests for dams_utils.py's real Global Dam Watch/GOODD dam/reservoir data
and the permanent DEM conditioning it drives. Real-data tests skip
gracefully if the local "Dams data/" folder isn't present on this
machine -- same established pattern as test_infra_utils.py/
test_geomorphology_utils.py.
"""
import os
import sys

import numpy as np
import pytest
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dams_utils  # noqa: E402

_dams_skip = pytest.mark.skipif(not dams_utils.dams_data_available(), reason="local Dams data/ not present")

# Real Tarbela dam/reservoir bbox (known present in the real supplied data).
TARBELA_BBOX = (72.4, 33.9, 72.9, 34.3)


@_dams_skip
def test_fetch_dams_finds_real_tarbela():
    dams = dams_utils.fetch_dams_for_bounds(*TARBELA_BBOX)
    names = [d["name"] for d in dams]
    assert "Tarbela" in names
    tarbela = next(d for d in dams if d["name"] == "Tarbela")
    assert tarbela["height_m"] == pytest.approx(143.0)
    assert tarbela["height_is_real"] is True


@_dams_skip
def test_fetch_reservoirs_finds_real_tarbela_with_correct_depth():
    reservoirs = dams_utils.fetch_reservoirs_for_bounds(*TARBELA_BBOX)
    names = [r["name"] for r in reservoirs]
    assert "Tarbela" in names
    tarbela = next(r for r in reservoirs if r["name"] == "Tarbela")
    # Real DEPTH_M = CAP_MCM / AREA_SKM, confirmed directly against the
    # real data (13940 / 208.42 = 66.9m).
    assert tarbela["depth_m"] == pytest.approx(66.9, abs=0.1)
    assert tarbela["depth_is_real"] is True


@_dams_skip
def test_fetch_missing_height_falls_back_honestly(monkeypatch):
    # Real data has at least one dam with the dataset's own -99 sentinel
    # (confirmed directly) -- its height must be flagged NOT real.
    dams = dams_utils.fetch_dams_for_bounds(60.0, 23.0, 78.0, 38.0)
    missing = [d for d in dams if not d["height_is_real"]]
    assert len(missing) > 0
    assert all(d["height_m"] == dams_utils.DEFAULT_DAM_HEIGHT_M for d in missing)


def test_fetch_dams_missing_file_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(dams_utils, "BARRIERS_SHP", "/nonexistent/barriers.shp")
    assert dams_utils.fetch_dams_for_bounds(*TARBELA_BBOX) == []


def test_fetch_reservoirs_missing_file_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(dams_utils, "RESERVOIRS_SHP", "/nonexistent/reservoirs.shp")
    assert dams_utils.fetch_reservoirs_for_bounds(*TARBELA_BBOX) == []


@_dams_skip
def test_condition_dem_with_dams_lowers_reservoir_and_raises_dam():
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
    x0, y0 = to_utm(TARBELA_BBOX[0], TARBELA_BBOX[1])
    x1, y1 = to_utm(TARBELA_BBOX[2], TARBELA_BBOX[3])
    header = {"ncols": 300, "nrows": 300, "xllcorner": min(x0, x1), "yllcorner": min(y0, y1),
              "cellsize": (max(x0, x1) - min(x0, x1)) / 300.0}
    dem = np.full((300, 300), 500.0)
    modified, summary = dams_utils.condition_dem_with_dams(dem, header, "EPSG:32643")
    assert summary["n_reservoirs_conditioned"] >= 1
    assert summary["n_dams_conditioned"] >= 1
    assert "Tarbela" in summary["reservoir_names"]
    assert "Tarbela" in summary["dam_names"]
    assert modified.min() < 500.0  # real reservoir depression applied
    assert modified.max() > 500.0  # real dam barrier applied


def test_condition_dem_with_dams_never_mutates_input(monkeypatch):
    monkeypatch.setattr(dams_utils, "BARRIERS_SHP", "/nonexistent/barriers.shp")
    monkeypatch.setattr(dams_utils, "RESERVOIRS_SHP", "/nonexistent/reservoirs.shp")
    header = {"ncols": 10, "nrows": 10, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 1000.0}
    dem = np.full((10, 10), 100.0)
    original = dem.copy()
    dams_utils.condition_dem_with_dams(dem, header, "EPSG:32643")
    assert np.array_equal(dem, original)


def test_condition_dem_missing_data_folder_returns_zero_summary(monkeypatch):
    monkeypatch.setattr(dams_utils, "BARRIERS_SHP", "/nonexistent/barriers.shp")
    monkeypatch.setattr(dams_utils, "RESERVOIRS_SHP", "/nonexistent/reservoirs.shp")
    header = {"ncols": 10, "nrows": 10, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 1000.0}
    dem = np.full((10, 10), 100.0)
    modified, summary = dams_utils.condition_dem_with_dams(dem, header, "EPSG:32643")
    assert summary["n_dams_conditioned"] == 0
    assert summary["n_reservoirs_conditioned"] == 0
    assert np.array_equal(modified, dem)


@_dams_skip
def test_condition_dem_respects_nodata_mask():
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
    x0, y0 = to_utm(TARBELA_BBOX[0], TARBELA_BBOX[1])
    x1, y1 = to_utm(TARBELA_BBOX[2], TARBELA_BBOX[3])
    header = {"ncols": 300, "nrows": 300, "xllcorner": min(x0, x1), "yllcorner": min(y0, y1),
              "cellsize": (max(x0, x1) - min(x0, x1)) / 300.0}
    dem = np.full((300, 300), -9999.0)  # entirely NODATA
    modified, summary = dams_utils.condition_dem_with_dams(dem, header, "EPSG:32643", nodata=-9999.0)
    assert np.array_equal(modified, dem)  # nothing real to condition onto


# ---- real spillway event CSV ----
_spill_skip = pytest.mark.skipif(not dams_utils.spillway_events_available(),
                                  reason="local dam_spillway_opening_events.csv not present")


@_spill_skip
def test_spillway_events_load_real_rows():
    events = dams_utils.load_spillway_events()
    assert len(events) >= 4
    assert {e["dam"] for e in events} <= {"Mangla", "Tarbela"}


@_spill_skip
def test_spillway_usable_events_have_numeric_outflow_and_narrative_only_rows_do_not():
    events = dams_utils.load_spillway_events()
    usable = [e for e in events if e["has_usable_outflow"]]
    unusable = [e for e in events if not e["has_usable_outflow"]]
    assert usable and unusable  # real data has both kinds
    assert all(isinstance(e["outflow_m3s"], float) and e["outflow_m3s"] > 0 for e in usable)
    assert all(e["outflow_m3s"] is None for e in unusable)


def test_cusec_conversion_matches_real_csv_values():
    # Real CSV rows: 70000 cusec -> 1982, 153000 -> 4332, 250000 -> 7079
    for cusec, expected_m3s in [(70000, 1982), (153000, 4332), (250000, 7079)]:
        assert cusec * dams_utils.CUSEC_TO_M3S == pytest.approx(expected_m3s, abs=1.0)


def test_spillway_cusec_fallback_when_m3s_blank(tmp_path, monkeypatch):
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "dam,date,time_PKT,spillway,what_happened,outflow_cusec,inflow_cusec,reservoir_level_ft,source,url,"
        "outflow_m3s,inflow_m3s,reservoir_level_m\n"
        'Tarbela,2030-01-01,,Spillway,test,100000,,,src,http://x,,,\n')
    monkeypatch.setattr(dams_utils, "SPILLWAY_EVENTS_CSV", str(csv_path))
    events = dams_utils.load_spillway_events()
    assert events[0]["has_usable_outflow"] is True
    assert events[0]["outflow_m3s"] == pytest.approx(2831.7, abs=0.2)


def test_spillway_missing_csv_returns_empty(monkeypatch):
    monkeypatch.setattr(dams_utils, "SPILLWAY_EVENTS_CSV", "/nonexistent/events.csv")
    assert dams_utils.load_spillway_events() == []


@_dams_skip
def test_dam_location_by_name_finds_real_tarbela_and_unknown_is_none():
    loc = dams_utils.dam_location_by_name("Tarbela")
    assert loc is not None and 72.0 < loc["lon"] < 73.0 and 33.5 < loc["lat"] < 34.5
    assert dams_utils.dam_location_by_name("Not A Real Dam") is None


# ---- dam failure scenarios ----
@_dams_skip
def test_failure_table_statuses_partition_all_dams_and_match_audit():
    t = dams_utils.load_dam_failure_table()
    assert len(t) == 101
    assert {d["status"] for d in t} == {"real_height", "estimated_height", "unusable"}
    for d in t:
        if d["status"] == "unusable":
            assert d["reason"]
        else:
            assert d["volume_m3"] > 0


@_dams_skip
def test_failure_real_height_dam_uses_froehlich_exactly_and_volume_conserves():
    import glof_utils
    t = dams_utils.load_dam_failure_table()
    tb = next(d for d in t if d["name"] == "Tarbela")
    s = dams_utils.dam_failure_scenario(tb["gdw_id"])
    assert s["peak_range_m3s"] is None
    assert s["peak_m3s"] == pytest.approx(
        glof_utils.froehlich_breach_discharge_m3s(tb["volume_m3"], 143.0), abs=1)
    # triangular hydrograph volume = 0.5*Qp*T equals stored volume
    assert 0.5 * s["peak_m3s"] * s["duration_hr"] * 3600 == pytest.approx(tb["volume_m3"], rel=0.02)


@_dams_skip
def test_failure_estimated_height_reports_ordered_range_not_single_number():
    t = dams_utils.load_dam_failure_table()
    est = next(d for d in t if d["status"] == "estimated_height")
    s = dams_utils.dam_failure_scenario(est["gdw_id"])
    r = s["peak_range_m3s"]
    assert r["low"] < r["central"] < r["high"]
    assert s["peak_m3s"] == r["central"]


@_dams_skip
def test_failure_unusable_and_unknown_dams_never_get_a_made_up_number():
    t = dams_utils.load_dam_failure_table()
    un = next(d for d in t if d["status"] == "unusable")
    assert dams_utils.dam_failure_scenario(un["gdw_id"])["peak_m3s"] is None
    assert dams_utils.dam_failure_scenario(-12345) is None


def test_failure_table_missing_data_returns_empty(monkeypatch):
    monkeypatch.setattr(dams_utils, "BARRIERS_SHP", "/nonexistent/b.shp")
    assert dams_utils.load_dam_failure_table() == []


# ---- spillway outlook (forecast inflow vs historical spill level) ----
def _fake_fetch(values):
    def f(lat, lon, past_days, forecast_days):
        return {"source": "fake", "daily_time": [f"2030-01-0{i+1}" for i in range(len(values))],
                "daily_river_discharge_m3s": values}
    return f


@_spill_skip
@_dams_skip
def test_outlook_flags_days_at_or_above_the_lowest_documented_spill_inflow():
    thr, _ = dams_utils.spillway_inflow_threshold("Tarbela")
    assert thr == pytest.approx(5947.0)            # lowest of the documented 6881 / 5947
    o = dams_utils.spillway_outlook("Tarbela", fetch=_fake_fetch([1000.0, thr, thr * 1.5, None, 100.0]))
    assert o["status"] == "ok"
    assert [d["at_or_above"] for d in o["forecast"]] == [False, True, True, False]   # None day skipped
    assert o["n_days_at_or_above"] == 2
    assert o["forecast"][2]["ratio_to_threshold"] == pytest.approx(1.5)
    assert "indicator, not a prediction" in o["caveat"]


def test_outlook_refuses_mangla_because_the_feed_failed_validation():
    called = []
    o = dams_utils.spillway_outlook("Mangla", fetch=lambda *a: called.append(a))
    assert o["status"] == "unreliable_feed" and "8,495" in o["reason"]
    assert called == []                              # never even queried the feed


def test_outlook_refuses_dams_with_no_validation_data():
    o = dams_utils.spillway_outlook("Chashma", fetch=lambda *a: pytest.fail("must not fetch"))
    assert o["status"] == "no_validation"


@_spill_skip
def test_threshold_none_for_dam_without_documented_inflow():
    assert dams_utils.spillway_inflow_threshold("Chashma") == (None, [])
