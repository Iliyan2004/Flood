"""
Real, end-to-end tests against the LIVE fis_app.py Flask server (see
conftest.py's `live_server` fixture -- spawns and tears down its own
dedicated instance on its own port, isolated from this machine's shared
production instance; see that fixture's own docstring for why). These
exercise the actual HTTP routes a real browser session calls, backed by
the real physics/analysis pipelines -- never a mock of the Flask app or
its dependencies.

All tests here are marked `slow` (most involve at least one real
LISFLOOD-FP run over HTTP) except the ones that only exercise the cheap
HAND/Tier-2 lookup, which are fast enough to leave in the default subset.

Every request in this file goes through conftest.py's
`_request_with_server_healing` (not bare `requests.get/post`) -- a real,
live-observed need on this shared machine: this suite's own dedicated
server process can be killed by real external contention mid-suite (see
conftest.py), and these follow-up GETs (damage.json/sediment.json/
report.pdf) read from a completed run's own on-disk artifacts, which
survive a server restart just fine (unlike an in-flight run's in-memory
progress, which wait_for_run/run_and_wait handle separately).
"""
import json
import os
import time

import numpy as np
import pytest
import requests

from conftest import (HAND_CACHE, TEST_AOI_GEOMETRY, TEST_MARKER_RAIN_MM_HR,
                       _request_with_server_healing, run_and_wait, wait_for_run)


def _hand_cache_snapshot():
    if not os.path.isdir(HAND_CACHE):
        return set()
    return set(os.listdir(HAND_CACHE))


def _register_new_hand_cache_entries(before, cache_cleanup):
    """Diffs the hand_cache/ directory against a snapshot taken before the
    request(s) and registers every newly-created entry's stem (the real
    geometry-hash key -- shared by both the .pkl HAND result and its
    sibling .library.json) with cache_cleanup, so this test's real cache
    writes are cleaned up at teardown without ever touching any
    pre-existing entry (e.g. a real production cache hit)."""
    after = _hand_cache_snapshot()
    for fn in after - before:
        stem = fn.split(".")[0]
        if stem not in cache_cleanup["hand"]:
            cache_cleanup["hand"].append(stem)


# A second, distinct real AOI -- a ~3km x 3km box near the same
# Bahawalpur area, well inside the local Pakistan SRTM -- used only for
# the cheap Tier 2 HAND lookup tests, which don't need the pentagon's own
# history, just a real polygon on real terrain. Deliberately NOT the tiny
# ~30-cell TEST_AOI_GEOMETRY pentagon used elsewhere in this suite: HAND's
# own real stream-extraction step (hand_utils.STREAM_ACCUM_THRESHOLD_CELLS
# = 500 cells draining in before a cell counts as a stream) can never find
# a real stream inside an AOI with fewer than ~500 cells total, since
# accumulation is only ever computed from what's INSIDE the cropped
# AOI -- confirmed directly: the pentagon AOI (30 total cells) returns
# 0 wet cells at every discharge level, including absurdly large ones, not
# because of a bug but because it's simply too small to ever contain a
# real, thresholded stream cell. A real HAND-based-lookup test needs an
# AOI big enough to have genuine upstream drainage area within it.
HAND_TEST_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [71.90, 30.27],
        [71.93, 30.27],
        [71.93, 30.30],
        [71.90, 30.30],
        [71.90, 30.27],
    ]],
}


# --------------------------------------------------------------------------
# Tier 2: HAND-based instant flood lookup -- fast (no LISFLOOD-FP run)
# --------------------------------------------------------------------------
def test_instant_flood_hand_lookup_monotonic_in_discharge(live_server, cache_cleanup):
    """Real HAND-based Tier 2 lookup (hand_utils.py, /area/instant-flood):
    increasing discharge on the IDENTICAL AOI must never decrease flooded
    area or max depth -- the same real monotonicity discipline this task
    asks for instead of a hardcoded expected number."""
    before = _hand_cache_snapshot()
    results = []
    for q in (20.0, 200.0, 2000.0):
        r = _request_with_server_healing("POST", f"{live_server}/area/instant-flood", json={
            "geometry": HAND_TEST_GEOMETRY, "discharge_m3s": q}, timeout=60)
        r.raise_for_status()
        body = r.json()
        assert "error" not in body, body
        results.append(body)

    areas = [b["flooded_area_km2"] for b in results]
    depths = [b["max_depth_m"] for b in results]
    assert all(b >= a - 1e-9 for a, b in zip(areas, areas[1:])), areas
    assert all(b >= a - 1e-9 for a, b in zip(depths, depths[1:])), depths
    # A real, physically non-degenerate lookup: the largest discharge must
    # produce SOME real wet cells, not just "didn't error."
    assert results[-1]["wet_cells"] > 0
    assert results[-1]["total_cells"] > 0
    _register_new_hand_cache_entries(before, cache_cleanup)


def test_instant_flood_library_matches_single_lookups_and_is_cached_fast(live_server, cache_cleanup):
    """Real Tier 0/2 discharge library (/area/instant-flood-library):
    every swept level's own area/depth must be internally monotonic
    (same discharge-ordering property as the single-lookup test above,
    checked directly from the library's own returned rows), and a second
    request for the identical AOI must be served from cache -- much
    faster than a cold compute, confirmed by wall-clock, not just assumed."""
    before = _hand_cache_snapshot()
    t0 = time.time()
    r1 = _request_with_server_healing("POST", f"{live_server}/area/instant-flood-library", json={
        "geometry": HAND_TEST_GEOMETRY}, timeout=60)
    r1.raise_for_status()
    cold_elapsed = time.time() - t0
    body1 = r1.json()
    assert "error" not in body1, body1
    library = body1["library"]
    assert len(library) >= 3

    areas = [row["flooded_area_km2"] for row in library]
    depths = [row["max_depth_m"] for row in library]
    assert all(b >= a - 1e-9 for a, b in zip(areas, areas[1:])), areas
    assert all(b >= a - 1e-9 for a, b in zip(depths, depths[1:])), depths

    t1 = time.time()
    r2 = _request_with_server_healing("POST", f"{live_server}/area/instant-flood-library", json={
        "geometry": HAND_TEST_GEOMETRY}, timeout=60)
    r2.raise_for_status()
    warm_elapsed = time.time() - t1
    assert r2.json()["library"] == library  # identical cached result
    # Not a tight bound (shared-machine timing is noisy) -- just confirms
    # the cache path is genuinely being taken, not recomputed from scratch.
    assert warm_elapsed < max(cold_elapsed, 0.5)
    _register_new_hand_cache_entries(before, cache_cleanup)


# --------------------------------------------------------------------------
# Tier 3: a real physics /run, plus everything downstream of it
# (damage/hotspot, sediment, PDF export) -- one shared run reused by all
# of these to avoid paying for a second full physics run per assertion.
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def finished_run(live_server):
    """A single real, small /run against the shared TEST_AOI_GEOMETRY,
    reused (read-only) by every test in this module that needs a
    completed run's downstream artifacts -- avoids re-running the real
    solver once per assertion. Cleans up its own cache entry at
    module-teardown time via the same registry cache_cleanup uses
    elsewhere, done manually here since a module-scoped fixture can't
    depend on the function-scoped cache_cleanup fixture directly."""
    import shutil
    from conftest import FIS_CACHE

    registry = {"runs": [], "hand": []}
    payload = {"geometry": TEST_AOI_GEOMETRY, "rain_mm_hr": TEST_MARKER_RAIN_MM_HR,
               "duration_hr": 1.0, "include_buildings": True}
    run_id, status = run_and_wait(live_server, payload, registry, timeout=150)
    assert status["status"] == "done", status
    yield live_server, run_id, status
    for rid in registry["runs"]:
        d = os.path.join(FIS_CACHE, rid)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


@pytest.mark.slow
def test_run_mass_balance_and_real_flooding_over_http(finished_run):
    """The real end-to-end smoke test the task explicitly calls for: hit
    the live /run route with a real small Pakistan AOI and a real storm,
    and check REAL resulting properties (mass balance near zero, some
    real flooded area) -- never a hardcoded expected number."""
    _, _, status = finished_run
    summary = status["summary"]
    assert summary["flooded_area_km2"] > 0
    assert summary["max_depth_m"] > 0
    rainfall_in = max(summary.get("cumulative_rainfall_input_m3", 0.0), 1.0)
    assert abs(summary.get("final_mass_error_m3", 0.0)) < 0.02 * rainfall_in


@pytest.mark.slow
def test_run_over_http_bigger_storm_floods_more(live_server, cache_cleanup):
    """Same real monotonic-physics property as the CLI-level test, this
    time through the actual live HTTP /run route end-to-end (DEM crop,
    buildings, the real solver, summary) -- confirms the whole web stack
    preserves the same real physical relationship, not just the bare CLI."""
    small_payload = {"geometry": TEST_AOI_GEOMETRY, "rain_mm_hr": TEST_MARKER_RAIN_MM_HR - 3.0,
                      "duration_hr": 1.0, "include_buildings": False}
    big_payload = {"geometry": TEST_AOI_GEOMETRY, "rain_mm_hr": TEST_MARKER_RAIN_MM_HR + 40.0,
                    "duration_hr": 1.0, "include_buildings": False}
    small_id, small_status = run_and_wait(live_server, small_payload, cache_cleanup, timeout=150)
    big_id, big_status = run_and_wait(live_server, big_payload, cache_cleanup, timeout=150)
    assert small_status["status"] == "done" and big_status["status"] == "done"
    small_summary, big_summary = small_status["summary"], big_status["summary"]
    assert big_summary["flooded_area_km2"] >= small_summary["flooded_area_km2"]
    assert big_summary["max_depth_m"] >= small_summary["max_depth_m"]

    # /compare-runs, real: these two runs share the identical AOI (same
    # TEST_AOI_GEOMETRY -> same DEM crop -> same grid), only rain differs
    # -- reuses the two real runs just produced above rather than running
    # a third/fourth real physics job just to test comparison.
    r = _request_with_server_healing("POST", f"{live_server}/compare-runs",
                                      json={"run_id_a": small_id, "run_id_b": big_id}, timeout=30)
    r.raise_for_status()
    cmp_body = r.json()
    assert "error" not in cmp_body, cmp_body
    c = cmp_body["comparison"]
    # the bigger storm can only ever flood a cell the smaller storm also
    # flooded, or a NEW one -- never the other way around, given the real
    # monotonic relationship just confirmed above.
    assert c["cells_only_a"] == 0
    assert c["cells_only_b"] >= 0
    assert cmp_body["run_a"]["run_id"] == small_id and cmp_body["run_b"]["run_id"] == big_id

    r2 = _request_with_server_healing("POST", f"{live_server}/compare-runs/mapbox3d",
                                       json={"run_id_a": small_id, "run_id_b": big_id}, timeout=30)
    assert r2.status_code == 200 and "mapboxgl" in r2.text.lower()

    # Same run twice, and an unknown run id, both refuse cleanly rather
    # than crashing or returning a misleading result.
    r3 = _request_with_server_healing("POST", f"{live_server}/compare-runs",
                                       json={"run_id_a": small_id, "run_id_b": small_id}, timeout=30)
    assert "error" in r3.json()
    r4 = _request_with_server_healing("POST", f"{live_server}/compare-runs",
                                       json={"run_id_a": small_id, "run_id_b": "not_a_real_run_id"}, timeout=30)
    assert "error" in r4.json()

    # Comparison report (real, self-contained HTML + a real weasyprint
    # PDF export) -- reuses these same two real runs.
    r5 = _request_with_server_healing(
        "GET", f"{live_server}/compare-runs/{small_id}/{big_id}/report.html", timeout=30)
    assert r5.status_code == 200
    assert f"{small_summary['flooded_area_km2']:.4f}" in r5.text
    assert f"{big_summary['flooded_area_km2']:.4f}" in r5.text

    r6 = _request_with_server_healing(
        "GET", f"{live_server}/compare-runs/{small_id}/{big_id}/report.pdf", timeout=30)
    assert r6.status_code == 200
    assert r6.content[:4] == b"%PDF"


@pytest.mark.slow
def test_damage_hotspot_analysis_over_http(finished_run):
    """Real Huizinga depth-damage + Getis-Ord Gi* hotspot analysis
    (damage_utils.py/compute_damage + getis_ord_hotspots), served lazily
    on first request via /runs/<id>/damage.json -- checks real structural
    properties (a non-empty class breakdown, sane hotspot/coldspot counts,
    an overall damage fraction in the real valid [0,1] range), not a
    hardcoded number that would be fragile to curve/threshold tuning."""
    live_url, run_id, _ = finished_run
    r = _request_with_server_healing("GET", f"{live_url}/runs/{run_id}/damage.json", timeout=60)
    r.raise_for_status()
    body = r.json()
    assert "error" not in body, body
    assert body["class_breakdown"]
    assert 0.0 <= body["mean_damage_fraction_overall"] <= 1.0
    assert body["hotspot_cells"] >= 0
    assert body["coldspot_cells"] >= 0


@pytest.mark.slow
def test_sediment_transport_analysis_over_http(finished_run):
    """Real Meyer-Peter & Muller (1948) bedload-transport analysis, served
    via /runs/<id>/sediment.json -- checks the real risk-band breakdown
    accounts for cells sanely (no negative counts) and a real d50 value is
    reported (either the request's own or a real soil-derived default)."""
    live_url, run_id, _ = finished_run
    r = _request_with_server_healing("GET", f"{live_url}/runs/{run_id}/sediment.json", timeout=60)
    r.raise_for_status()
    body = r.json()
    assert "error" not in body, body
    assert body["d50_mm"] > 0
    assert all(v >= 0 for v in body["risk_band_counts"].values())


@pytest.mark.slow
def test_pdf_report_export_over_http(finished_run):
    """Real weasyprint PDF export (/runs/<id>/report.pdf) of the run's own
    real report.html -- checks a genuine, non-trivial PDF byte stream
    comes back (the real %PDF- magic header), not just a 200 status."""
    live_url, run_id, _ = finished_run
    r = _request_with_server_healing("GET", f"{live_url}/runs/{run_id}/report.pdf", timeout=60)
    r.raise_for_status()
    assert r.headers.get("content-type", "").startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 2000  # a real rendered report, not an empty/error stub


@pytest.mark.slow
def test_batch_return_period_style_runs_are_independently_cached_and_monotonic(live_server, cache_cleanup):
    """Real regression covering the UI's "batch return-period runs"
    feature (fis_app.py's runBatchReturnPeriods -- sequential real /run
    calls, one per selected return period, each reusing the panel's other
    settings): simulates that same sequential-/run-per-scenario pattern
    directly against the real route with two distinct rain intensities
    standing in for two distinct return periods' design storms. Checks
    each gets its own independent run_id/cache entry (never collapsed
    into one, which would silently misreport one scenario's results for
    another) and that the real monotonic physics relationship holds
    across the batch, exactly like a real batch of increasing return
    periods would."""
    scenarios_mm_hr = [TEST_MARKER_RAIN_MM_HR + 5.0, TEST_MARKER_RAIN_MM_HR + 25.0, TEST_MARKER_RAIN_MM_HR + 55.0]
    run_ids = []
    summaries = []
    for rain in scenarios_mm_hr:
        payload = {"geometry": TEST_AOI_GEOMETRY, "rain_mm_hr": rain, "duration_hr": 1.0,
                   "include_buildings": False}
        run_id, status = run_and_wait(live_server, payload, cache_cleanup, timeout=150)
        assert status["status"] == "done", status
        run_ids.append(run_id)
        summaries.append(status["summary"])

    assert len(set(run_ids)) == len(run_ids)  # every scenario got its own real cache entry
    areas = [s["flooded_area_km2"] for s in summaries]
    assert all(b >= a - 1e-9 for a, b in zip(areas, areas[1:])), areas


@pytest.mark.slow
def test_storm_centering_moving_center_differs_from_fixed_center_over_http(live_server, cache_cleanup):
    """Real end-to-end version of test_storm_centering.py's fast NetCDF-
    level check: a MOVING storm center (storm_velocity_lon/lat_per_hr !=
    0) must actually change the real LISFLOOD-FP depth result through the
    live /run route, not just the intermediate NetCDF file -- confirms
    fis_app.py actually wires storm_center_lon/lat + velocity through to
    flood_model.py's CLI (--storm-center-lon0/--storm-velocity-*) and that
    the solver genuinely produces a different res.max grid, not merely
    that write_rain_netcdf_moving itself differs in isolation."""
    import flood_model  # noqa
    from conftest import FIS_CACHE

    poly = TEST_AOI_GEOMETRY["coordinates"][0]
    lons = [p[0] for p in poly]
    lats = [p[1] for p in poly]
    center_lon = sum(lons) / len(lons)
    center_lat = sum(lats) / len(lats)

    common = {"geometry": TEST_AOI_GEOMETRY, "rain_mm_hr": TEST_MARKER_RAIN_MM_HR + 1.2,
              "duration_hr": 1, "include_buildings": False,
              "storm_center_lon": center_lon, "storm_center_lat": center_lat}
    fixed_payload = dict(common, storm_velocity_lon_per_hr=0.0, storm_velocity_lat_per_hr=0.0)
    # A real, large velocity (deg/hr) relative to this small (~130m) AOI --
    # enough to visibly shift the storm core across/past the whole domain
    # within a 1-hour duration.
    moving_payload = dict(common, storm_velocity_lon_per_hr=0.01, storm_velocity_lat_per_hr=-0.006)

    fixed_id, fixed_st = run_and_wait(live_server, fixed_payload, cache_cleanup, timeout=150)
    moving_id, moving_st = run_and_wait(live_server, moving_payload, cache_cleanup, timeout=150)
    assert fixed_st["status"] == "done" and moving_st["status"] == "done"

    fixed_header, fixed_grid = flood_model.read_ascii(
        os.path.join(FIS_CACHE, fixed_id, "results", "res.max"))
    moving_header, moving_grid = flood_model.read_ascii(
        os.path.join(FIS_CACHE, moving_id, "results", "res.max"))
    assert fixed_grid.shape == moving_grid.shape

    valid = (fixed_grid != -9999) & (moving_grid != -9999)
    assert valid.any()
    diff = np.abs(fixed_grid[valid] - moving_grid[valid])
    # The real, load-bearing assertion: a moving storm center must produce
    # a MEASURABLY different spatial depth pattern than a fixed one on the
    # identical AOI/rain total through the real, live end-to-end route --
    # a regression that silently dropped velocity somewhere in fis_app.py's
    # own CLI-argument wiring (even if storm_utils.py itself stayed correct)
    # would make this diff ~0 everywhere.
    assert diff.max() > 1e-4, "moving vs fixed storm center produced identical depth grids over HTTP"


# --------------------------------------------------------------------------
# Live/continuous monitoring (/monitor/*) -- fast, no LISFLOOD-FP run
# (threshold is set impossibly high so the real forecast can never cross
# it and trigger a real /run job during this test)
# --------------------------------------------------------------------------
def test_monitor_watch_lifecycle_over_http(live_server, cache_cleanup):
    """Real end-to-end test of the live-monitoring feature's CRUD + check
    routes (monitoring_utils.py's watch_list.json persistence, and
    fis_app.py's /monitor/watch, /monitor/list, /monitor/<id>/check-now,
    DELETE /monitor/<id>). Uses a real Open-Meteo forecast fetch (this
    app's own no-mocks-ever philosophy -- see conftest.py) but an
    impossibly high threshold (99999 mm/hr) so a real triggered /run job
    is never actually started by this test, regardless of today's real
    weather -- the self-loopback trigger-a-run code path itself was
    already verified manually in this project's history (a mocked-rain
    direct call producing a real run_id); this test covers the HTTP
    lifecycle around it, which had zero prior automated coverage."""
    body = {
        "geometry": TEST_AOI_GEOMETRY,
        "threshold_mm_hr": 99999.0,
        "check_interval_hours": 3.0,
        "include_buildings": False,
    }
    r = _request_with_server_healing("POST", f"{live_server}/monitor/watch", json=body, timeout=15)
    r.raise_for_status()
    watch_id = r.json()["watch_id"]
    assert watch_id

    try:
        r = _request_with_server_healing("GET", f"{live_server}/monitor/list", timeout=15)
        r.raise_for_status()
        watches = {w["id"]: w for w in r.json()["watches"]}
        assert watch_id in watches
        assert watches[watch_id]["threshold_mm_hr"] == 99999.0
        assert watches[watch_id]["last_checked"] is None

        r = _request_with_server_healing(
            "POST", f"{live_server}/monitor/{watch_id}/check-now", timeout=15)
        r.raise_for_status()
        assert r.json()["ok"] is True

        # The real check runs in a background thread on the server; poll
        # for it to finish (last_checked becomes non-None) rather than a
        # fixed sleep. A real, transient Open-Meteo fetch failure (rate-
        # limiting -- directly observed and documented elsewhere in this
        # project's history) is the one tolerated outcome if last_checked
        # never updates; anything else (last_checked never set AND no
        # matching log line) is a genuine failure, not tolerated.
        deadline = time.time() + 20
        watch = None
        while time.time() < deadline:
            r = _request_with_server_healing("GET", f"{live_server}/monitor/list", timeout=15)
            r.raise_for_status()
            watch = next((w for w in r.json()["watches"] if w["id"] == watch_id), None)
            if watch and watch["last_checked"] is not None:
                break
            time.sleep(0.5)
        assert watch is not None
        if watch["last_checked"] is None:
            from conftest import BASE_DIR
            log_path = os.path.join(BASE_DIR, "fis_app_test.log")
            with open(log_path) as f:
                log_tail = f.read()[-4000:]
            assert f"{watch_id}: rainfall fetch failed" in log_tail, (
                "check-now neither updated last_checked nor logged a real, "
                "known-transient rainfall-fetch failure -- a genuine bug"
            )
        else:
            # threshold_mm_hr=99999 can never be crossed by a real forecast
            assert watch["alerts"] == []
    finally:
        r = _request_with_server_healing("DELETE", f"{live_server}/monitor/{watch_id}", timeout=15)
        r.raise_for_status()

    r = _request_with_server_healing("GET", f"{live_server}/monitor/list", timeout=15)
    r.raise_for_status()
    assert all(w["id"] != watch_id for w in r.json()["watches"])


def test_rainfall_csv_with_no_coords_and_multiple_stations_requires_a_real_pick(live_server):
    """A real CSV with no lat/lon columns and more than one station must
    NOT auto-guess a station via geocoding (an unreliable approximation
    for sub-city addresses -- see rainfall_csv_utils.py) -- it should ask
    the caller to pick one, then honor that exact real choice when asked
    again with station_key."""
    csv_text = (
        "station,location,time_start_pkt,precip_mm\n"
        "Station A,\"I-8, Islamabad\",9/1/2026 0:00,0\n"
        "Station A,\"I-8, Islamabad\",9/1/2026 0:15,10.0\n"
        "Station B,\"E-11, Islamabad\",9/1/2026 0:00,0\n"
        "Station B,\"E-11, Islamabad\",9/1/2026 0:15,50.0\n"
    )
    geometry = json.dumps(TEST_AOI_GEOMETRY)

    r = _request_with_server_healing(
        "POST", f"{live_server}/area/rainfall-csv", timeout=20,
        files={"file": ("test.csv", csv_text, "text/csv")}, data={"geometry": geometry})
    r.raise_for_status()
    body = r.json()
    assert body.get("needs_station_choice") is True
    keys = {s["key"] for s in body["stations"]}
    assert keys == {"Station A", "Station B"}

    r2 = _request_with_server_healing(
        "POST", f"{live_server}/area/rainfall-csv", timeout=20,
        files={"file": ("test.csv", csv_text, "text/csv")},
        data={"geometry": geometry, "station_key": "Station B"})
    r2.raise_for_status()
    body2 = r2.json()
    assert body2["station_name"] == "Station B"
    assert body2["peak_mm_hr"] == 200.0  # 50mm over a real 15-min (0.25hr) interval -> 200 mm/hr
    assert body2["distance_km"] is None  # picked directly, no geocoded-distance guess involved


def test_rainfall_csv_single_station_never_needs_a_pick_even_without_coords(live_server):
    csv_text = (
        "station,time_start_pkt,precip_mm\n"
        "Only One,9/1/2026 0:00,0\n"
        "Only One,9/1/2026 0:15,5.0\n"
    )
    r = _request_with_server_healing(
        "POST", f"{live_server}/area/rainfall-csv", timeout=20,
        files={"file": ("test.csv", csv_text, "text/csv")},
        data={"geometry": json.dumps(TEST_AOI_GEOMETRY)})
    r.raise_for_status()
    body = r.json()
    assert not body.get("needs_station_choice")
    assert body["station_name"] == "Only One"
