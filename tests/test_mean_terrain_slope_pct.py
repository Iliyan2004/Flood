"""
Fast, pure-math tests for flood_model.mean_terrain_slope_pct -- the real
slope helper factored out of choose_solver_for_terrain so main()'s own
steep-terrain initial_tstep decision (see CLAUDE.md's real bisection
finding: initial_tstep=5.0 causes a real, unphysical depth blow-up on
terrain as gentle as 15% slope) can reuse the identical computation.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402

CELLSIZE = 30.0
NODATA = -9999.0


def test_flat_terrain_has_zero_slope():
    dem = np.full((20, 20), 100.0)
    mean_slope, max_slope = flood_model.mean_terrain_slope_pct(dem, CELLSIZE)
    assert mean_slope == pytest.approx(0.0, abs=1e-6)
    assert max_slope == pytest.approx(0.0, abs=1e-6)


def test_known_ramp_matches_hand_computed_slope():
    ncols = 21
    rise_m = 200.0
    x = np.linspace(0, rise_m, ncols)
    dem = np.tile(x, (10, 1))
    mean_slope, max_slope = flood_model.mean_terrain_slope_pct(dem, CELLSIZE)
    expected_pct = rise_m / ((ncols - 1) * CELLSIZE) * 100.0
    assert mean_slope == pytest.approx(expected_pct, rel=1e-6)


def test_all_nodata_returns_none_none():
    dem = np.full((10, 10), NODATA)
    mean_slope, max_slope = flood_model.mean_terrain_slope_pct(dem, CELLSIZE, nodata=NODATA)
    assert mean_slope is None and max_slope is None


def test_steeper_ramp_gives_larger_slope():
    ncols = 21
    x_gentle = np.linspace(0, 10.0, ncols)
    x_steep = np.linspace(0, 500.0, ncols)
    dem_gentle = np.tile(x_gentle, (10, 1))
    dem_steep = np.tile(x_steep, (10, 1))
    mean_gentle, _ = flood_model.mean_terrain_slope_pct(dem_gentle, CELLSIZE)
    mean_steep, _ = flood_model.mean_terrain_slope_pct(dem_steep, CELLSIZE)
    assert mean_steep > mean_gentle


def test_matches_choose_solver_for_terrains_own_slope_computation():
    """The refactor must not have changed the real numeric result
    choose_solver_for_terrain's own reason string reports."""
    dem = np.tile(np.linspace(0, 10.0, 20), (20, 1))
    mean_slope, _ = flood_model.mean_terrain_slope_pct(dem, CELLSIZE)
    _, reason = flood_model.choose_solver_for_terrain(dem, CELLSIZE)
    assert f"{mean_slope:.1f}%" in reason
