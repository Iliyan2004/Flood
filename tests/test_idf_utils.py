"""
Fast tests for idf_utils.py's real ERA5-Land/IMERG per-cell IDF (real
observational, not synthetic) lookups. Real network-free (no HTTP, just a
local NetCDF read via the symlinked real dataset). Skipped gracefully on
any machine without this real local extract, matching the same
skipif-on-missing-local-file pattern used elsewhere in this app's own
test suite (test_infra_utils.py, test_glof_utils.py).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import idf_utils  # noqa: E402

_has_idf = idf_utils.idf_data_available()
_idf_skip = pytest.mark.skipif(not _has_idf, reason="local rainfall IDF dataset not present on this machine")

# Islamabad-area point, real, known-good coverage confirmed during development.
ISB_LAT, ISB_LON = 33.7, 73.1


@_idf_skip
def test_real_idf_curve_returns_points_sorted_by_duration():
    curve = idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON, year=2025, season="monsoon")
    assert curve is not None
    durs = [p["duration_hr"] for p in curve["points"]]
    assert durs == sorted(durs)


@_idf_skip
def test_real_idf_curve_intensity_generally_decreases_with_duration():
    """Real physical property of an IDF curve: longer accumulation
    windows have lower peak intensity (rain can't sustain its 30-min
    burst rate for 24h) -- checked end-to-end (first vs last point),
    not strictly monotonic point-to-point (real data can have small
    real non-monotonic wiggles between adjacent close durations)."""
    curve = idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON, year=2025, season="monsoon")
    assert curve is not None
    assert curve["points"][0]["intensity_mm_hr"] > curve["points"][-1]["intensity_mm_hr"]


@_idf_skip
def test_real_idf_curve_no_duplicate_durations():
    """Real, load-bearing regression check: IMERG's own sub-hourly points
    (up to 3h) must be preferred over ERA5-Land's overlapping 1/2/3h
    points, not both included -- a duplicate x-value at the same real
    duration would make any later log-log interpolation ill-defined."""
    curve = idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON, year=2025, season="monsoon")
    assert curve is not None
    durs = [p["duration_hr"] for p in curve["points"]]
    assert len(durs) == len(set(durs))


@_idf_skip
def test_real_idf_curve_short_durations_prefer_imerg_when_available():
    curve = idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON, year=2025, season="monsoon")
    assert curve is not None
    short = [p for p in curve["points"] if p["duration_hr"] <= 3.0]
    assert short  # a real Islamabad-area cell has real IMERG coverage
    assert all(p["source"] == "IMERG" for p in short)


@_idf_skip
def test_real_design_intensity_matches_exact_curve_point():
    curve = idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON, year=2025, season="monsoon")
    target = next(p for p in curve["points"] if p["duration_hr"] == 6)
    design = idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=6, year=2025, season="monsoon")
    assert design is not None
    assert design["intensity_mm_hr"] == pytest.approx(target["intensity_mm_hr"], rel=1e-6)


@_idf_skip
def test_real_design_intensity_interpolates_between_real_points():
    """A duration with no exact real curve point (e.g. 4h, between the
    real 3h and 6h points) must be interpolated, not error out or
    silently snap to one endpoint."""
    d3 = idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=3.0, year=2025, season="monsoon")
    d4 = idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=4.0, year=2025, season="monsoon")
    d6 = idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=6.0, year=2025, season="monsoon")
    assert d3["intensity_mm_hr"] > d4["intensity_mm_hr"] > d6["intensity_mm_hr"]


@_idf_skip
def test_real_design_intensity_clamps_to_curve_range():
    """A requested duration outside the real curve's own real min/max
    (e.g. 48h, beyond the real 24h ERA5-Land point) clamps to the
    nearest real endpoint rather than extrapolating past real data."""
    d24 = idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=24.0, year=2025, season="monsoon")
    d48 = idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=48.0, year=2025, season="monsoon")
    assert d48["intensity_mm_hr"] == pytest.approx(d24["intensity_mm_hr"], rel=1e-6)
    assert d48["duration_hr"] == pytest.approx(24.0)


@_idf_skip
def test_real_extreme_indices_returns_real_expected_keys():
    indices = idf_utils.real_extreme_indices_for_point(ISB_LAT, ISB_LON, year=2024, season="monsoon")
    assert indices is not None
    for key in ("rx1day", "rx5day", "r95p", "r99p", "sdii", "r20mm", "prcptot", "peakhr"):
        assert key in indices["values"]
    assert "trend_mm_per_decade" in indices["values"]


@_idf_skip
def test_default_year_prefers_a_real_complete_year_over_a_partial_one():
    """Real, load-bearing regression check: the dataset's own most recent
    year (2026, per this real dataset's build) is a documented PARTIAL
    year (ERA5-Land stops ~20 May) -- the default (no year given) must
    resolve to a real complete year instead, not silently return a curve
    missing its own long-duration (6-24h) points."""
    curve = idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON, season="monsoon")
    assert curve is not None
    assert curve["year"] != 2026
    durs = {p["duration_hr"] for p in curve["points"]}
    assert 24 in durs  # a real complete year has the real long-duration ERA5-Land points


def test_idf_data_available_false_when_files_missing(monkeypatch):
    monkeypatch.setattr(idf_utils, "ERA_NC_PATH", "/nonexistent/path.nc")
    assert idf_utils.idf_data_available() is False


# --------------------------------------------------------------------------
# Real Pakistan tehsil boundary search/lookup (578 real tehsils).
# --------------------------------------------------------------------------
_has_tehsils = idf_utils.tehsils_available()
_tehsils_skip = pytest.mark.skipif(not _has_tehsils, reason="local tehsil boundary geojson not present on this machine")


@_tehsils_skip
def test_search_tehsils_name_match_ranked_before_district_match():
    """A search for "Thatta" should surface the real Thatta TEHSIL first,
    not some other real tehsil that merely sits in Thatta DISTRICT."""
    results = idf_utils.search_tehsils("Thatta")
    assert results
    assert results[0]["name"] == "Thatta"


@_tehsils_skip
def test_search_tehsils_is_case_insensitive():
    assert idf_utils.search_tehsils("charsadda") == idf_utils.search_tehsils("CHARSADDA")


@_tehsils_skip
def test_search_tehsils_short_query_returns_empty():
    assert idf_utils.search_tehsils("a") == []
    assert idf_utils.search_tehsils("") == []


@_tehsils_skip
def test_search_tehsils_no_match_returns_empty_not_raises():
    assert idf_utils.search_tehsils("xyz_not_a_real_place_zzz") == []


@_tehsils_skip
def test_search_tehsils_respects_limit():
    results = idf_utils.search_tehsils("Punjab", limit=3)
    assert len(results) <= 3


@_tehsils_skip
def test_tehsil_geometry_returns_real_polygon_for_known_id():
    results = idf_utils.search_tehsils("Charsadda")
    tid = results[0]["id"]
    g = idf_utils.tehsil_geometry(tid)
    assert g is not None
    assert g["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert g["properties"]["id"] == tid


@_tehsils_skip
def test_tehsil_geometry_unknown_id_returns_none():
    assert idf_utils.tehsil_geometry(999999) is None


def test_tehsils_available_false_when_file_missing(monkeypatch):
    monkeypatch.setattr(idf_utils, "TEHSILS_GEOJSON_PATH", "/nonexistent/tehsils.geojson")
    assert idf_utils.tehsils_available() is False


def test_search_tehsils_returns_empty_when_dataset_missing(monkeypatch):
    monkeypatch.setattr(idf_utils, "_tehsils_state", {"loaded": True, "features": None})
    assert idf_utils.search_tehsils("Charsadda") == []
    assert idf_utils.tehsil_geometry(1) is None


# --------------------------------------------------------------------------
# National hotspots (top_cells) -- the highest individual real grid cells
# nationally for a chosen metric, each labeled with its real tehsil.
# --------------------------------------------------------------------------
@_idf_skip
def test_top_cells_returns_results_sorted_descending():
    top = idf_utils.top_cells("rx1day", season="monsoon", n=10)
    assert top
    vals = [t["value"] for t in top]
    assert vals == sorted(vals, reverse=True)


@_idf_skip
def test_top_cells_respects_n():
    top = idf_utils.top_cells("rx1day", season="monsoon", n=3)
    assert len(top) <= 3


@_idf_skip
def test_top_cells_each_result_has_real_tehsil_label():
    top = idf_utils.top_cells("rx1day", season="monsoon", n=5)
    assert top
    for t in top:
        assert t["tehsil"] is not None
        assert t["tehsil_id"] > 0


@_idf_skip
def test_top_cells_peak30_uses_imerg_grid():
    top = idf_utils.top_cells("peak30", season="monsoon", n=5)
    assert top  # real IMERG coverage exists for Pakistan
    assert all(t["value"] > 0 for t in top)


@_idf_skip
def test_top_cells_trend_metric_works():
    top = idf_utils.top_cells("trend", season="monsoon", n=5)
    assert top


def test_top_cells_unknown_metric_returns_empty():
    assert idf_utils.top_cells("not_a_real_metric") == []


def test_top_cells_returns_empty_when_dataset_missing(monkeypatch):
    monkeypatch.setattr(idf_utils, "_state", {"opened": True, "era": None, "img": None, "partial_years": set()})
    assert idf_utils.top_cells("rx1day") == []
    assert idf_utils.top_cells("peak30") == []


@_idf_skip
def test_metric_catalogue_has_real_expected_metrics():
    cat = idf_utils.metric_catalogue()
    assert cat
    for key in ("rx1day", "r95p", "peak30", "trend"):
        assert key in cat
        assert "label" in cat[key] and "unit" in cat[key]


def test_metric_catalogue_empty_when_manifest_missing(monkeypatch):
    monkeypatch.setattr(idf_utils, "MANIFEST_JSON_PATH", "/nonexistent/manifest.json")
    assert idf_utils.metric_catalogue() == {}


def test_functions_return_none_when_dataset_missing(monkeypatch):
    monkeypatch.setattr(idf_utils, "_state", {"opened": True, "era": None, "img": None, "partial_years": set()})
    assert idf_utils.real_idf_curve_for_point(ISB_LAT, ISB_LON) is None
    assert idf_utils.real_extreme_indices_for_point(ISB_LAT, ISB_LON) is None
    assert idf_utils.real_design_intensity_for_duration(ISB_LAT, ISB_LON, duration_hr=2.0) is None
