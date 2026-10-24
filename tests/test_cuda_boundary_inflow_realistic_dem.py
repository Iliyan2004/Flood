"""
THE single most important regression test in this suite (per explicit
task instruction).

Real, independently-re-verified bug this session: LISFLOOD-FP's GPU/CUDA
build silently drops 100% of any real .bci/.bdy boundary-condition inflow
(QFIX/QVAR discharge -- used by this app's river/GLOF features) on a real
DEM with realistic (large, nonzero) UTM coordinates. `Vol`/depth stays
zero from the first timestep, with NO error raised anywhere -- a run that
"succeeds" while silently doing nothing. The CPU build handles the exact
same input correctly.

Why every OTHER boundary-inflow test in this suite (e.g.
test_flood_model_cli.py's QVAR width-clamping test) does NOT catch this:
they all use `make_synthetic_dem`, which hardcodes `xllcorner=0.0,
yllcorner=0.0` -- a flat-zero-origin DEM happens to make LISFLOOD-FP's
LOCAL and ABSOLUTE .bci coordinates coincide by pure accident, so a bug
that only manifests with a nonzero origin is invisible on it. This is
exactly the false-positive trap the task flagged: a synthetic-origin-only
regression suite would keep passing even if this exact bug came back.

The fix already applied (and verified NOT duplicated here, only
exercised): flood_model.py's `main()`/`choose_solver_for_terrain()`/
`write_par_file()` force `use_cuda = not has_boundary_inflow` -- any run
with a real boundary inflow runs on CPU instead of GPU/CUDA, sidestepping
the broken CUDA code path entirely regardless of DEM origin.

This test builds a real ARC/INFO ASCII DEM with realistic large UTM-42N
(Pakistan) corner coordinates (xllcorner/yllcorner in the hundreds-of-
thousands/millions range, NOT 0), gives it a real QVAR river hydrograph
inflow, runs flood_model.py's actual CLI end-to-end via `--dem`, and
asserts real, non-zero, non-NaN flooding actually results -- exactly the
property that silently failed before the fix (0.0 km^2 / 0.0m every time,
regardless of discharge magnitude, per CLAUDE.md's own account of the
original discovery).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
FLOOD_MODEL = os.path.join(BASE_DIR, "flood_model.py")

sys.path.insert(0, BASE_DIR)
import flood_model  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import run_subprocess_group  # noqa: E402

pytestmark = pytest.mark.slow


# Realistic UTM-42N (Pakistan) corner coordinates -- NOT 0,0. Chosen in
# the real valid range for this zone/country (comfortably inside
# UTM-42N's own false-easting/northing envelope), the exact class of
# value that triggered the real bug (a synthetic 0,0-origin DEM does not).
REALISTIC_XLLCORNER = 431260.0
REALISTIC_YLLCORNER = 3312480.0


def _build_realistic_dem(workdir, width_m=300.0, height_m=300.0, cellsize=20.0, slope_pct=1.0):
    """A small, fast synthetic terrain (same generator flood_model.py's
    own CLI uses for --width/--height/--slope), but written out as a real
    ARC ASCII file with a realistic nonzero UTM origin instead of the
    CLI's own default synthetic 0,0 origin -- the one deliberate
    difference from every other synthetic-DEM test in this suite."""
    header, dem = flood_model.make_synthetic_dem(width_m, height_m, cellsize, slope_pct, seed=7)
    header["xllcorner"] = REALISTIC_XLLCORNER
    header["yllcorner"] = REALISTIC_YLLCORNER
    dem_path = os.path.join(workdir, "realistic_origin.dem.ascii")
    flood_model.write_ascii(dem_path, header, dem, nodata=-9999)
    return dem_path, header


def _run_with_boundary_inflow(workdir, dem_path, peak_m3s=250.0, timeout=180):
    """Runs the real flood_model.py CLI with a real QVAR river inflow on
    the N edge, pure-fluvial (rain=0) so any resulting flooding can only
    have come from the boundary inflow itself.

    This exercises flood_model.py's CPU fallback path specifically (the
    fix under test forces CPU whenever a boundary inflow is present), which
    runs a tiny 0.2s internal timestep over a multi-hour sim -- a real
    ~60-90s CPU-bound computation, unlike the sub-second GPU path most other
    tests in this suite exercise. Confirmed by direct manual reproduction
    during this session: the identical command can fail with a bare,
    diagnostic-free nonzero exit ("LISFLOOD-FP FAILED" with no further
    detail, no Python traceback) under this shared machine's real, observed
    heavy concurrent load (other live production runs/other users' own
    processes contending for the same CPU cores over that ~60-90s window),
    then succeed cleanly on an immediate retry with no code changes at all
    -- a real, load-dependent flake in the external `lisflood` binary
    itself under contention, not a logic bug in this app. Retried once for
    that reason (mirroring the existing cudaErrorMemoryAllocation retry
    policy used elsewhere in this suite), but ONLY when there's no Python
    traceback in the output -- a genuine code-level bug in flood_model.py's
    own argument handling/pre-flight logic always shows a traceback and is
    never silently retried away."""
    boundary = {
        "inflows": [{"edge": "N", "pos_m": 150.0, "width_m": 60.0,
                     "peak_m3s": peak_m3s, "duration_hr": 1.0, "name": "realistic_test_river"}],
        "outflows": [],
    }
    boundary_path = os.path.join(workdir, "boundary.json")
    with open(boundary_path, "w") as f:
        json.dump(boundary, f)

    cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_path, "--cellsize", "20",
           "--rain-mm-hr", "0", "--duration-hr", "0.1", "--saveint-min", "10",
           "--workdir", workdir, "--boundary-json", boundary_path]
    proc = run_subprocess_group(cmd, timeout=timeout)
    if proc.returncode != 0 and "Traceback" not in (proc.stdout + proc.stderr):
        proc = run_subprocess_group(cmd, timeout=timeout)
    return proc


@pytest.fixture
def tmp_workdir():
    d = tempfile.mkdtemp(prefix="sailab_test_cudabug_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_boundary_inflow_on_realistic_utm_dem_actually_floods(tmp_workdir):
    """THE regression test. On a realistic-origin DEM, a real river
    hydrograph inflow must produce real, non-zero, non-NaN flooding --
    this is precisely what silently read as 0.0 everywhere under the
    (now-fixed) CUDA-boundary-inflow bug."""
    dem_path, header = _build_realistic_dem(tmp_workdir)
    assert header["xllcorner"] != 0.0 and header["yllcorner"] != 0.0  # sanity: not the false-positive-prone case

    proc = _run_with_boundary_inflow(tmp_workdir, dem_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    with open(os.path.join(tmp_workdir, "summary.json")) as f:
        summary = json.load(f)

    assert not (summary["max_depth_m"] != summary["max_depth_m"])  # not NaN
    assert summary["max_depth_m"] > 0.0, (
        "max_depth_m was 0 -- this is EXACTLY the silent CUDA-boundary-inflow-drop "
        "bug this test exists to catch (real discharge, zero real flooding)"
    )
    assert summary["flooded_area_km2"] > 0.0
    assert summary["cumulative_river_inflow_m3"] > 0.0

    # Also verify directly from the real per-cell result grid, not just
    # the summary -- res.max must contain real wet cells, and none of
    # them may be NaN (a silent numerical blow-up would be just as wrong
    # as silent zero flooding).
    max_path = os.path.join(tmp_workdir, "results", "res.max")
    res_header, res_max = flood_model.read_ascii(max_path)
    valid = res_max != -9999
    assert valid.any()
    assert np.isfinite(res_max[valid]).all()
    assert (res_max[valid] > 0.05).sum() > 0, "no real cell exceeded even a trivial 5cm depth"


def _par_lines(workdir):
    par = [f for f in os.listdir(workdir) if f.endswith(".par")]
    assert par, f"no .par file found in {workdir}"
    with open(os.path.join(workdir, par[0])) as f:
        return [l.strip() for l in f.read().splitlines()]


def test_boundary_inflow_on_valid_terrain_uses_cuda_and_matches_cpu(tmp_workdir):
    """Measured Oct 2026: with the inlet on real terrain, the CUDA QVAR boundary equals the
    CPU build, so such runs now use the GPU. Same inputs on both builds must agree."""
    dem_path, _ = _build_realistic_dem(tmp_workdir)
    gpu_dir = os.path.join(tmp_workdir, "gpu"); os.makedirs(gpu_dir)
    cpu_dir = os.path.join(tmp_workdir, "cpu"); os.makedirs(cpu_dir)
    proc = _run_with_boundary_inflow(gpu_dir, dem_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cuda" in _par_lines(gpu_dir)
    os.environ["SAILAB_FORCE_CPU"] = "1"
    try:
        proc = _run_with_boundary_inflow(cpu_dir, dem_path)
    finally:
        os.environ.pop("SAILAB_FORCE_CPU", None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cuda" not in _par_lines(cpu_dir)
    sg = json.load(open(os.path.join(gpu_dir, "summary.json")))
    sc = json.load(open(os.path.join(cpu_dir, "summary.json")))
    assert sg["flooded_area_km2"] == pytest.approx(sc["flooded_area_km2"], rel=0.05)
    assert sg["max_depth_m"] == pytest.approx(sc["max_depth_m"], rel=0.05)


def test_boundary_inflow_on_nodata_cells_falls_back_to_cpu(tmp_workdir):
    """An inlet sitting on NODATA cells is drained/destabilised by CUDA, so it must go to CPU."""
    dem_path, header = _build_realistic_dem(tmp_workdir)
    h, dem = flood_model.read_ascii(dem_path)
    dem[0, 5:11] = -9999  # N edge under the 60 m inflow segment centred at 150 m
    flood_model.write_ascii(dem_path, h, dem, nodata=-9999)
    proc = _run_with_boundary_inflow(tmp_workdir, dem_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cuda" not in _par_lines(tmp_workdir)


def test_pure_pluvial_on_realistic_dem_still_uses_cuda(tmp_workdir):
    """Control/contrast case: a run with NO boundary inflow on the SAME
    realistic-origin DEM must still use CUDA (the fix is scoped to
    boundary-inflow runs only, not a blanket CPU fallback for every real-
    coordinate DEM) -- confirms the fix didn't overreach."""
    dem_path, _ = _build_realistic_dem(tmp_workdir)
    # rain-mm-hr=30 over only 0.5h duration was found (by direct manual
    # reproduction, independent of this test) to leave genuinely zero
    # recorded max-depth on this small (15x15-cell), 4-sided-free-outlet
    # terrain REGARDLESS of DEM origin -- confirmed by running the
    # identical params against a plain 0,0-origin synthetic DEM too, which
    # also came back 0.0 km^2. Not a solver/origin bug: this terrain drains
    # too fast under this specific light/short storm to leave a measurable
    # peak. duration-hr=1.0 (matching the storm strength already proven to
    # reliably flood this same 300x300/slope-1.0 terrain in
    # test_flood_model_cli.py's own basic-run test) makes this a real,
    # non-degenerate check instead of a coin flip on drainage timing.
    # UPDATE (Oct 2026): 30 mm/hr x 1h is itself marginal on this 15x15-cell plot -- the
    # solver's raw peak depth is 6.2 cm at 30 mm/hr (only 2 of 225 cells above the 5 cm wet
    # threshold) and 11.5 cm at 80 (5 cells); on this test's seed-7 terrain the 30 mm/hr case
    # falls under 5 cm, so "flooded > 0" failed deterministically. 80 mm/hr gives a real
    # margin; what the test checks (CUDA is still used for pure pluvial) is unchanged.
    cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_path, "--cellsize", "20",
           "--rain-mm-hr", "80", "--duration-hr", "1.0", "--saveint-min", "10",
           "--workdir", tmp_workdir]
    proc = run_subprocess_group(cmd, timeout=180)
    if proc.returncode != 0 and "cudaErrorMemoryAllocation" in (proc.stdout + proc.stderr):
        proc = run_subprocess_group(cmd, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    par_files = [f for f in os.listdir(tmp_workdir) if f.endswith(".par")]
    with open(os.path.join(tmp_workdir, par_files[0])) as f:
        lines = [l.strip() for l in f.read().splitlines()]
    assert "cuda" in lines, "a plain pluvial run on a realistic-origin DEM should still use CUDA"

    with open(os.path.join(tmp_workdir, "summary.json")) as f:
        summary = json.load(f)
    assert summary["flooded_area_km2"] > 0.0  # rain on a real-origin DEM still floods normally


def test_coastal_hvar_runs_on_cuda_and_matches_cpu(tmp_workdir):
    """HVAR on CUDA needed a kernel patch (gate on already-wet cells); verify the patched
    build floods and agrees with the CPU build on a realistic-origin DEM."""
    dem_path, _ = _build_realistic_dem(tmp_workdir, slope_pct=2.0)
    h, dem = flood_model.read_ascii(dem_path)
    lo = float(dem[dem != -9999].min())
    hv = {"edge": "S", "name": "coast", "times_hr": [0, 1, 2, 3],
          "levels_m": [lo - 1, lo + 3, lo + 3, lo - 1]}
    res = {}
    for tag, cpu in (("gpu", False), ("cpu", True)):
        d = os.path.join(tmp_workdir, tag); os.makedirs(d)
        hp = os.path.join(d, "hvar.json"); json.dump(hv, open(hp, "w"))
        env = dict(os.environ); env.pop("SAILAB_FORCE_CPU", None)
        if cpu:
            env["SAILAB_FORCE_CPU"] = "1"
        cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_path, "--cellsize", "20", "--rain-mm-hr", "0",
               "--duration-hr", "0.1", "--sim-hours", "3", "--saveint-min", "10",
               "--workdir", d, "--coastal-hvar-json", hp]
        old = os.environ.get("SAILAB_FORCE_CPU")
        os.environ.update({k: env[k] for k in env if k == "SAILAB_FORCE_CPU"})
        if not cpu:
            os.environ.pop("SAILAB_FORCE_CPU", None)
        try:
            proc = run_subprocess_group(cmd, timeout=240)
        finally:
            os.environ.pop("SAILAB_FORCE_CPU", None)
            if old is not None:
                os.environ["SAILAB_FORCE_CPU"] = old
        assert proc.returncode == 0, proc.stdout + proc.stderr
        res[tag] = json.load(open(os.path.join(d, "summary.json")))
        assert ("cuda" in _par_lines(d)) == (not cpu)
    assert res["cpu"]["flooded_area_km2"] > 0
    assert res["gpu"]["flooded_area_km2"] == pytest.approx(res["cpu"]["flooded_area_km2"], rel=0.05)
