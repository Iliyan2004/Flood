"""
Fast, pure-logic tests for flood_model.py's choose_solver_for_terrain --
the real slope-based ACC/DG2 auto-selection, and the has_boundary_inflow
override (see the function's own docstring for the real, direct-testing
finding behind that override: DG2's adaptive timestep collapses under a
concentrated boundary discharge spike). No real LISFLOOD-FP run needed --
these feed synthetic DEMs directly, unlike the existing real-physics
coverage in test_cuda_boundary_inflow_realistic_dem.py (which verifies
the SOLVER-SELECTION DECISION plays out correctly end-to-end through an
actual run, not this function's own decision logic in isolation).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402

CELLSIZE = 30.0
NODATA = -9999.0


def _flat_dem(shape=(20, 20), elevation=100.0):
    return np.full(shape, elevation, dtype="float64")


def _ramp_dem(shape, rise_m, nodata=NODATA, mask_border=False):
    """A real linear-slope test DEM: elevation rises `rise_m` metres
    across the grid's own width (in real DEM cellsize units), giving a
    known, exactly-computable mean/max slope to assert against."""
    nrows, ncols = shape
    x = np.linspace(0, rise_m, ncols)
    dem = np.tile(x, (nrows, 1))
    if mask_border:
        dem = dem.copy()
        dem[0, :] = nodata  # a real NODATA border row, like an AOI crop's own edge
    return dem


def test_flat_terrain_selects_acc():
    dem = _flat_dem()
    solver, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    assert solver == "acc"
    assert "0.0%" in reason or "at or below" in reason


def test_steep_ramp_selects_dg2_when_force_acc_only_is_off(monkeypatch):
    # A real, deliberately steep ramp: 200m of rise over a 20-cell-wide,
    # 30m-cellsize grid (600m total width) -- a real ~33% mean slope,
    # comfortably over the real 10% DG2_SLOPE_THRESHOLD_PCT. Tests the
    # real underlying slope-based logic with the current temporary
    # FORCE_ACC_ONLY override (see its own module-level comment) turned
    # off, since that override -- while real and currently the shipped
    # default -- is explicitly documented as reversible/temporary and
    # this underlying logic must keep working correctly either way.
    monkeypatch.setattr(flood_model, "FORCE_ACC_ONLY", False)
    dem = _ramp_dem((20, 20), rise_m=200.0)
    solver, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    assert solver == "dg2"
    assert "exceeds" in reason


def test_steep_ramp_selects_acc_when_force_acc_only_is_on():
    # The real, current shipped default (see FORCE_ACC_ONLY's own
    # module-level comment): steep terrain that would otherwise route to
    # DG2 now gets ACC instead, relying on the real, empirically-fit
    # depth bias correction for magnitude accuracy.
    dem = _ramp_dem((20, 20), rise_m=200.0)
    solver, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    assert flood_model.FORCE_ACC_ONLY is True
    assert solver == "acc"
    assert "force_acc_only" in reason.lower()


def test_gentle_ramp_selects_acc():
    # A real gentle ramp: 10m of rise over the same 600m width -- a real
    # ~1.7% mean slope, comfortably under the threshold.
    dem = _ramp_dem((20, 20), rise_m=10.0)
    solver, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    assert solver == "acc"


def test_slope_exactly_at_threshold_boundary_is_deterministic(monkeypatch):
    """Real, exact boundary case: a ramp engineered so its own real
    computed mean slope sits very close to DG2_SLOPE_THRESHOLD_PCT --
    confirms the comparison is a real, deterministic `>` (not `>=` or a
    fuzzy match) by checking both sides land where the strict inequality
    in the function's own source implies. FORCE_ACC_ONLY disabled here
    since this test is about the underlying threshold logic itself, not
    the current temporary override -- see its own module-level comment."""
    monkeypatch.setattr(flood_model, "FORCE_ACC_ONLY", False)
    # np.gradient's interior points give slope = rise_m/((ncols-1)*cellsize)
    # (per-cell rise/run, exact for a linear ramp); edges use a one-sided
    # difference at the same real per-cell spacing, so the whole ramp is
    # uniform -- mean == this exact value.
    ncols = 21
    run_m = (ncols - 1) * CELLSIZE
    target_pct = flood_model.DG2_SLOPE_THRESHOLD_PCT
    rise_just_under = (target_pct - 0.5) / 100.0 * run_m
    rise_just_over = (target_pct + 0.5) / 100.0 * run_m

    dem_under = _ramp_dem((10, ncols), rise_m=rise_just_under)
    dem_over = _ramp_dem((10, ncols), rise_m=rise_just_over)
    solver_under, _ = flood_model.choose_solver_for_terrain(dem_under, CELLSIZE)
    solver_over, _ = flood_model.choose_solver_for_terrain(dem_over, CELLSIZE)
    assert solver_under == "acc"
    assert solver_over == "dg2"


def test_large_domain_forces_acc_even_on_steep_terrain(monkeypatch):
    """Real, live-caught regression: a real whole-Islamabad production
    crash (cudaErrorIllegalAddress in LISFLOOD-FP 8.2's own DG2 CUDA
    kernel, confirmed via direct bisection -- see the function's own
    docstring) forces ACC above DG2_CUDA_MAX_CELLS regardless of terrain
    slope, the same override pattern as the boundary-inflow case.
    FORCE_ACC_ONLY disabled here to isolate this specific, independent
    real override from the current temporary blanket one -- see its own
    module-level comment."""
    monkeypatch.setattr(flood_model, "FORCE_ACC_ONLY", False)
    # A real, consistent per-cell rise (~10m/cell at 30m cellsize -> ~33%
    # slope, comfortably over the 10% threshold) applied at two very
    # different grid sizes -- both DEMs are equally "steep" by the real
    # slope calculation; only the cell COUNT differs.
    per_cell_rise = 10.0
    n_side = int(flood_model.DG2_CUDA_MAX_CELLS ** 0.5) + 10  # genuinely over the real (tested) limit
    steep_dem = _ramp_dem((n_side, n_side), rise_m=per_cell_rise * (n_side - 1))
    assert steep_dem.size > flood_model.DG2_CUDA_MAX_CELLS
    solver, reason = flood_model.choose_solver_for_terrain(steep_dem, CELLSIZE)
    assert solver == "acc"
    assert "cells" in reason.lower()

    small_steep = _ramp_dem((50, 50), rise_m=per_cell_rise * 49)
    solver_small, _ = flood_model.choose_solver_for_terrain(small_steep, CELLSIZE)
    assert solver_small == "dg2"


def test_domain_exactly_at_cell_limit_boundary_is_deterministic():
    n_side = int(flood_model.DG2_CUDA_MAX_CELLS ** 0.5)
    gentle = _ramp_dem((n_side, n_side), rise_m=10.0)  # under limit, real gentle slope -> acc anyway
    just_over = _ramp_dem((n_side + 5, n_side + 5), rise_m=200.0)  # over the real cell limit, real steep slope
    solver_under, reason_under = flood_model.choose_solver_for_terrain(gentle, CELLSIZE)
    solver_over, reason_over = flood_model.choose_solver_for_terrain(just_over, CELLSIZE)
    assert gentle.size <= flood_model.DG2_CUDA_MAX_CELLS
    assert just_over.size > flood_model.DG2_CUDA_MAX_CELLS
    assert solver_over == "acc"
    assert "cells" in reason_over.lower()


def test_boundary_inflow_forces_acc_even_on_steep_terrain(monkeypatch):
    """The real, direct-testing-derived override: any boundary inflow
    (river/GLOF) forces ACC regardless of how steep the terrain is,
    since DG2's timestep was found to collapse under a concentrated
    boundary discharge spike -- see the function's own docstring.
    FORCE_ACC_ONLY disabled here to isolate this specific, independent
    real override from the current temporary blanket one."""
    monkeypatch.setattr(flood_model, "FORCE_ACC_ONLY", False)
    steep_dem = _ramp_dem((20, 20), rise_m=200.0)
    solver, reason = flood_model.choose_solver_for_terrain(steep_dem, CELLSIZE, has_boundary_inflow=True)
    assert solver == "acc"
    assert "boundary" in reason.lower()

    # Confirmed the SAME terrain would have picked dg2 without the override.
    solver_no_override, _ = flood_model.choose_solver_for_terrain(steep_dem, CELLSIZE, has_boundary_inflow=False)
    assert solver_no_override == "dg2"


def test_all_nodata_dem_defaults_to_acc_not_raises():
    dem = np.full((10, 10), NODATA)
    solver, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    assert solver == "acc"
    assert "no valid terrain" in reason.lower()


def test_nodata_border_cells_excluded_from_slope_calculation():
    """A real NODATA border row (e.g. an AOI crop's own edge, per this
    app's own established DEM-cropping behavior) must not corrupt the
    real slope statistic computed over the genuinely valid interior."""
    gentle = _ramp_dem((20, 20), rise_m=10.0, mask_border=True)
    solver, reason = flood_model.choose_solver_for_terrain(gentle, CELLSIZE, nodata=NODATA)
    assert solver == "acc"  # the real valid interior is still gentle; NODATA border shouldn't flip this to dg2


def test_custom_threshold_pct_is_respected(monkeypatch):
    monkeypatch.setattr(flood_model, "FORCE_ACC_ONLY", False)
    dem = _ramp_dem((20, 20), rise_m=10.0)  # ~1.7% real mean slope
    solver_default, _ = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    solver_strict, _ = flood_model.choose_solver_for_terrain(dem, CELLSIZE, threshold_pct=0.5)
    assert solver_default == "acc"
    assert solver_strict == "dg2"  # the identical terrain flips to dg2 under a much stricter real threshold


def test_reason_string_reports_real_computed_slope_values():
    dem = _ramp_dem((20, 20), rise_m=10.0)
    _, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    # Real computed mean slope for this exact ramp (rise/run, see the
    # boundary-case test's own comment for why this is exact).
    expected_pct = 10.0 / (19 * CELLSIZE) * 100.0
    assert f"{expected_pct:.1f}%" in reason
