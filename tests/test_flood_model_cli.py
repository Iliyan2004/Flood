"""
Real, end-to-end tests of flood_model.py's own CLI against a small
SYNTHETIC DEM (make_synthetic_dem) -- these run the actual real
LISFLOOD-FP GPU/CUDA solver (the exact same binary/pipeline every live
`/run` request uses), just on a tiny, fast, network-free domain instead
of a real Pakistan AOI. This is the layer to catch real regressions in
the solver-invocation/boundary-condition/summary-computation code in
flood_model.py itself, independent of the Flask app and any live-network
dependency (buildings/roads/land-cover fetches).

All tests here are marked `slow` (each spawns a real subprocess running
the real GPU solver, ~1-5s) -- run `pytest -m "not slow"` to skip them
for fast iteration on the pure-math suite.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import run_subprocess_group  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
FLOOD_MODEL = os.path.join(BASE_DIR, "flood_model.py")

pytestmark = pytest.mark.slow


def _run_flood_model(workdir, extra_args, timeout=420):
    """Runs the real flood_model.py CLI as a subprocess, retrying once on
    the specific known-transient cudaErrorMemoryAllocation cold-start
    quirk (see CLAUDE.md) -- never swallows any other failure. Uses
    run_subprocess_group (see conftest.py) so a timeout can never leak an
    orphaned, still-running `lisflood` grandchild process (a real,
    live-observed cause of this suite's own flakiness on this shared
    machine -- see that function's docstring).

    timeout=420 (not the tiny domain's own normal ~1-5s): this machine is
    genuinely heavily shared (other real users' processes, including the
    live production fis_app.py server itself, routinely run concurrently)
    -- confirmed live during this session via `ps` that a real production
    run and another session's own manual test were both contending for the
    single GPU at the same time as this suite, pushing an otherwise-~5s
    run past the previous 180s budget purely from contention, not from any
    real bug. A generous timeout tolerates genuine shared-machine
    contention without masking an actual hang -- 420s is still bounded, so
    a truly stuck run still fails the suite rather than hanging forever."""
    base_cmd = [VENV_PY, FLOOD_MODEL, "--width", "300", "--height", "300", "--cellsize", "20",
                "--slope", "1.0", "--workdir", workdir] + extra_args
    proc = run_subprocess_group(base_cmd, timeout=timeout)
    if proc.returncode != 0 and "cudaErrorMemoryAllocation" in (proc.stdout + proc.stderr):
        shutil.rmtree(workdir, ignore_errors=True)
        os.makedirs(workdir, exist_ok=True)
        proc = run_subprocess_group(base_cmd, timeout=timeout)
    return proc


def _summary(workdir):
    with open(os.path.join(workdir, "summary.json")) as f:
        return json.load(f)


@pytest.fixture
def tmp_workdir():
    d = tempfile.mkdtemp(prefix="sailab_test_flood_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_basic_pluvial_run_mass_balance_and_flooding(tmp_workdir):
    """A real storm on a small synthetic catchment must: finish
    successfully, flood some real nonzero area, and balance mass to
    (near) zero -- the same real check this app's own report tile
    ('Mass-balance error ... should be ~= 0') displays to a user."""
    proc = _run_flood_model(tmp_workdir, ["--rain-mm-hr", "40", "--duration-hr", "1",
                                           "--saveint-min", "20"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(tmp_workdir)
    assert summary["flooded_area_km2"] > 0
    assert summary["max_depth_m"] > 0
    domain_area_m2 = summary["domain_area_km2"] * 1e6
    # Mass error should be tiny relative to the real rainfall volume input --
    # not exactly zero (floating point), but nowhere near a real physics bug.
    assert abs(summary["final_mass_error_m3"]) < 0.01 * max(summary["cumulative_rainfall_input_m3"], 1.0)


def test_bigger_storm_floods_more_than_smaller_storm(tmp_workdir):
    """Real monotonic physics on the IDENTICAL synthetic catchment: a
    heavier storm must flood at least as much area and at least as deep
    as a lighter one -- exactly the kind of check this task asks for
    instead of a hardcoded expected number (robust to any real future
    physics/parameter tweak, as long as the basic monotonic relationship
    between rainfall and flooding holds)."""
    small_dir = tmp_workdir + "_small"
    big_dir = tmp_workdir
    os.makedirs(small_dir, exist_ok=True)
    proc_small = _run_flood_model(small_dir, ["--rain-mm-hr", "10", "--duration-hr", "1", "--saveint-min", "20"])
    proc_big = _run_flood_model(big_dir, ["--rain-mm-hr", "80", "--duration-hr", "1", "--saveint-min", "20"])
    try:
        assert proc_small.returncode == 0, proc_small.stdout + proc_small.stderr
        assert proc_big.returncode == 0, proc_big.stdout + proc_big.stderr
        small = _summary(small_dir)
        big = _summary(big_dir)
        assert big["flooded_area_km2"] >= small["flooded_area_km2"]
        assert big["max_depth_m"] >= small["max_depth_m"]
        assert big["cumulative_rainfall_input_m3"] > small["cumulative_rainfall_input_m3"]
    finally:
        shutil.rmtree(small_dir, ignore_errors=True)


def test_amc_wetter_antecedent_condition_floods_more(tmp_workdir):
    """Real AMC physics (amc_infiltration_multiplier): a wetter antecedent
    condition (AMC closer to 100) reduces effective infiltration, so an
    IDENTICAL storm on IDENTICAL terrain with real infiltration configured
    should flood more (or equal) area under AMC=100 than AMC=0."""
    dry_dir = tmp_workdir + "_amcdry"
    wet_dir = tmp_workdir
    os.makedirs(dry_dir, exist_ok=True)
    common = ["--rain-mm-hr", "30", "--duration-hr", "1", "--saveint-min", "20",
              "--infiltration-mm-hr", "15"]
    proc_dry = _run_flood_model(dry_dir, common + ["--amc", "0"])
    proc_wet = _run_flood_model(wet_dir, common + ["--amc", "100"])
    try:
        assert proc_dry.returncode == 0, proc_dry.stdout + proc_dry.stderr
        assert proc_wet.returncode == 0, proc_wet.stdout + proc_wet.stderr
        dry = _summary(dry_dir)
        wet = _summary(wet_dir)
        assert wet["infiltration_mm_hr_effective"] < dry["infiltration_mm_hr_effective"]
        assert wet["flooded_area_km2"] >= dry["flooded_area_km2"]
    finally:
        shutil.rmtree(dry_dir, ignore_errors=True)


def test_low_friction_gets_clamped_to_safe_floor(tmp_workdir):
    """Real known-bug regression (CLAUDE.md finding #4): requesting a
    friction value below the documented safe floor must be clamped
    (with a logged warning), not passed through -- a real, documented
    instability trigger LISFLOOD-FP's own manual warns about. A
    regression that removed the clamp would either crash/destabilize the
    solver or silently use an unsafe value; this asserts the real
    post-clamp behavior via the CLI's own stdout warning."""
    proc = _run_flood_model(tmp_workdir, ["--rain-mm-hr", "20", "--duration-hr", "1",
                                          "--saveint-min", "20", "--friction", "0.0"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "below the safe floor" in combined
    assert "clamping to" in combined


def test_qvar_boundary_inflow_width_clamped_delivers_requested_discharge(tmp_workdir):
    """Real known-bug regression (CLAUDE.md's QVAR/ARF width-clamping
    under-delivery bug): a real boundary inflow sited very close to its
    own edge's corner must still deliver its FULL requested peak
    discharge, not a fraction of it silently diluted by an un-clamped
    edge span baked into the .bdy per-unit-width flux. Verified directly
    from the real files flood_model.py writes (site.bci / rivers.bdy),
    not just that the run doesn't crash: the applied segment width in
    site.bci and the per-unit-width peak flux in rivers.bdy must multiply
    back out to exactly the requested peak_m3s."""
    width, height, cellsize = 300.0, 300.0, 20.0
    edge_span = width  # N/S edge span == domain width
    half_w = 15.0  # requested width_m=30 -> half-width 15
    pos_m = 5.0  # 5m from the edge's own corner -- well inside half_w of it
    peak_m3s = 300.0
    boundary = {
        "inflows": [{"edge": "N", "pos_m": pos_m, "width_m": 2 * half_w,
                     "peak_m3s": peak_m3s, "duration_hr": 2.0, "name": "test_river"}],
        "outflows": [],
    }
    boundary_path = os.path.join(tmp_workdir, "boundary.json")
    os.makedirs(tmp_workdir, exist_ok=True)
    with open(boundary_path, "w") as f:
        json.dump(boundary, f)

    # _run_flood_model's base_cmd already supplies --width/--height/
    # --cellsize/--slope matching these same 300/300/20/1.0 values -- not
    # repeated here (argparse would just take the later value anyway, but
    # passing it twice was needless duplication).
    proc = _run_flood_model(tmp_workdir, ["--rain-mm-hr", "0", "--duration-hr", "0.1",
                                          "--saveint-min", "20",
                                          "--boundary-json", boundary_path])
    assert proc.returncode == 0, proc.stdout + proc.stderr

    bci_path = os.path.join(tmp_workdir, "site.bci")
    # The real filename flood_model.py's write_par_file/write_bdy_file
    # actually uses is "boundaries.bdy" (confirmed directly from
    # flood_model.py's own bdy_name assignment and cross-checked against a
    # real LISFLOOD-FP run's own "bdyfile = boundaries.bdy" log line) --
    # NOT "rivers.bdy", which this test originally (incorrectly) assumed
    # and which the app never writes.
    bdy_path = os.path.join(tmp_workdir, "boundaries.bdy")
    assert os.path.exists(bci_path), "site.bci was not written"
    assert os.path.exists(bdy_path), "boundaries.bdy was not written"

    with open(bci_path) as f:
        bci_lines = [l.strip() for l in f if l.strip()]
    # Find the N-edge QVAR line: "N <start> <end> QVAR <name>"
    qvar_line = next(l for l in bci_lines if l.startswith("N") and "QVAR" in l)
    parts = qvar_line.split()
    start_m, end_m = float(parts[1]), float(parts[2])
    applied_width_m = end_m - start_m
    # The real clamp: start/end must both be clamped into [0, edge_span] --
    # since pos_m=5 and half_w=15, an UNCLAMPED segment would be [-10, 20],
    # and the real applied width must be strictly less than the requested
    # 30m as a result.
    assert 0.0 <= start_m <= edge_span
    assert 0.0 <= end_m <= edge_span
    assert applied_width_m < 2 * half_w - 1e-6
    assert applied_width_m == pytest.approx(pos_m + half_w, abs=1e-6)  # clamped to [0, pos+half_w]

    with open(bdy_path) as f:
        bdy_text = f.read()
    # bdy_block writes "<flux> <time>" pairs; the peak point is the 2nd
    # data line (after name + count/units lines) -- real format confirmed
    # in flood_model.py's own write_bdy_file docstring.
    data_lines = [l for l in bdy_text.splitlines() if l.strip() and not l.startswith("#")]
    # First non-comment line is the name, second is "<count> <units>",
    # remaining lines are "<value> <time>" pairs; the peak (2nd pair) has
    # the largest flux value.
    value_time_pairs = []
    for l in data_lines[2:]:
        parts = l.split()
        if len(parts) == 2:
            try:
                value_time_pairs.append((float(parts[0]), float(parts[1])))
            except ValueError:
                pass
    assert value_time_pairs, f"could not parse any value/time pairs from:\n{bdy_text}"
    peak_flux_per_width = max(v for v, t in value_time_pairs)

    # The real, load-bearing assertion: peak_flux_per_width * applied_width_m
    # must reconstruct the REQUESTED peak_m3s exactly (mass-conserving
    # delivery) -- this is exactly what the pre-fix bug got wrong (it used
    # the UNCLAMPED width for this multiplication, silently under-delivering).
    delivered_m3s = peak_flux_per_width * applied_width_m
    assert delivered_m3s == pytest.approx(peak_m3s, rel=1e-3)


def test_rain_series_json_drives_real_flooding_not_just_writes_a_file(tmp_workdir):
    """Real end-to-end regression for the uploaded-rainfall-CSV feature
    (rainfall_csv_utils.py + flood_model.py's --rain-series-json): a real
    uploaded time series -- not a triangular design storm -- must
    actually flood the solver's own domain, and a heavier real series
    must flood more than a lighter one, on the identical synthetic
    catchment. Confirms the JSON file is truly consumed by the physics,
    not merely parsed and ignored."""
    series_light = {"times_hr": [0.0, 0.25, 0.5, 0.75, 1.0], "rates_mm_hr": [0.0, 40.0, 40.0, 0.0, 0.0], "label": "test-light"}
    series_heavy = {"times_hr": [0.0, 0.25, 0.5, 0.75, 1.0], "rates_mm_hr": [0.0, 120.0, 120.0, 40.0, 0.0], "label": "test-heavy"}

    light_dir = tmp_workdir + "_light"
    heavy_dir = tmp_workdir
    os.makedirs(light_dir, exist_ok=True)
    light_json = os.path.join(light_dir, "rain_series.json")
    heavy_json = os.path.join(heavy_dir, "rain_series.json")
    with open(light_json, "w") as f:
        json.dump(series_light, f)
    with open(heavy_json, "w") as f:
        json.dump(series_heavy, f)

    proc_light = _run_flood_model(light_dir, ["--rain-mm-hr", "0", "--duration-hr", "0",
                                               "--saveint-min", "10", "--rain-series-json", light_json])
    proc_heavy = _run_flood_model(heavy_dir, ["--rain-mm-hr", "0", "--duration-hr", "0",
                                               "--saveint-min", "10", "--rain-series-json", heavy_json])
    try:
        assert proc_light.returncode == 0, proc_light.stdout + proc_light.stderr
        assert proc_heavy.returncode == 0, proc_heavy.stdout + proc_heavy.stderr
        light = _summary(light_dir)
        heavy = _summary(heavy_dir)
        assert light["flooded_area_km2"] > 0
        assert heavy["flooded_area_km2"] >= light["flooded_area_km2"]
        assert heavy["max_depth_m"] >= light["max_depth_m"]
        assert heavy["rainfall_series"]["label"] == "test-heavy"
        assert heavy["rainfall_series"]["peak_mm_hr"] == 120.0
    finally:
        shutil.rmtree(light_dir, ignore_errors=True)


def test_cpu_path_reports_rainfall_volume_in_real_cubic_metres(tmp_workdir, monkeypatch):
    """Regression for a 1000x over-report: on the CPU path the solver's own res.mass rain
    column is already in m^3 (an earlier x1000 'fix' inflated every CPU-path run's
    cumulative_rainfall_input_m3 -- all river/GLOF/coastal runs -- to more water than the
    domain could hold). The reported value must match the analytic triangular-storm volume
    (0.5 * peak * duration * domain area) within 10%."""
    monkeypatch.setenv("SAILAB_FORCE_CPU", "1")
    proc = _run_flood_model(tmp_workdir, ["--rain-mm-hr", "60", "--duration-hr", "1", "--saveint-min", "20"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    s = _summary(tmp_workdir)
    expected = 0.5 * (60 / 1000.0) * 1.0 * s["domain_area_km2"] * 1e6
    assert s["cumulative_rainfall_input_m3"] == pytest.approx(expected, rel=0.10)


@pytest.mark.parametrize("solver", ["gpu", "cpu"])
def test_closed_domain_conserves_rain_mass_on_both_solvers(tmp_workdir, monkeypatch, solver):
    """With every boundary closed and no infiltration, nothing can leave: the water stored at the
    end must equal the rain delivered (triangle: 0.5*peak*duration*area). Measured Oct 2026 at three
    sizes: CPU 1.000, GPU 0.997-0.998 of analytic. Guards both solver paths against mass creation/loss."""
    import glob, numpy as np
    monkeypatch.setenv("SAILAB_FORCE_CPU", "1" if solver == "cpu" else "0")
    proc = _run_flood_model(tmp_workdir, ["--rain-mm-hr", "40", "--duration-hr", "1", "--saveint-min", "30",
                                          "--outlet-edges", ""])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    last = sorted(glob.glob(os.path.join(tmp_workdir, "results", "res-*.wd")))[-1]
    with open(last) as f:
        hdr = {k.lower(): float(v) for k, v in (f.readline().split() for _ in range(6))}
    w = np.loadtxt(last, skiprows=6)
    cs = hdr["cellsize"]
    stored = float(np.where(w > -9990, w, 0).sum() * cs * cs)
    analytic = 0.5 * 0.040 * 1.0 * float((w > -9990).sum() * cs * cs)
    assert stored / analytic == pytest.approx(1.0, abs=0.01)
