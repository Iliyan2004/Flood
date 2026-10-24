"""
Fast, pure-logic tests for flood_model.py's compute_max_velocity_from_
timesteps -- real synthetic ARC ASCII res-*.wd/.Vx/.Vy files on disk (no
real LISFLOOD-FP run needed), specifically covering the real numerical-
artifact bug found and fixed in this function: a near-dry cell (depth
just above 0) can carry a tiny leftover momentum that makes velocity =
momentum/depth spike to a physically implausible value, and an earlier
version of this function reported that spike as the run's own "max
velocity" even though the SAME cell was never actually deep enough, at
that same instant, to be a real hazard.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402

HEADER = {"ncols": 4, "nrows": 4, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 30.0}
NODATA = -9999.0


def _write_timestep(results_dir, idx, depth, vx, vy):
    flood_model.write_ascii(os.path.join(results_dir, f"res-{idx:04d}.wd"), HEADER, depth, nodata=NODATA)
    flood_model.write_ascii(os.path.join(results_dir, f"res-{idx:04d}.Vx"), HEADER, vx, nodata=NODATA)
    flood_model.write_ascii(os.path.join(results_dir, f"res-{idx:04d}.Vy"), HEADER, vy, nodata=NODATA)


def test_wetting_front_velocity_spike_excluded(tmp_path):
    # One genuinely wet cell (depth=1.0m, real moderate 2 m/s flow), one
    # near-dry cell (depth=0.001m, an artificially huge 500 m/s "speed" --
    # the exact real class of numerical artifact this function must
    # exclude). Same cell-centered grid shape for Vx/Vy (DG2 convention)
    # to keep this test focused on the real depth-gating fix, not the
    # separate de-staggering logic (already covered by direct real-data
    # verification elsewhere).
    depth = np.full((4, 4), NODATA)
    vx = np.zeros((4, 4))
    vy = np.zeros((4, 4))
    depth[1, 1] = 1.0
    vx[1, 1] = 2.0
    vy[1, 1] = 0.0
    depth[2, 2] = 0.001  # near-dry, below dry_thresh
    vx[2, 2] = 500.0     # a real, physically impossible spike if not excluded
    vy[2, 2] = 0.0

    _write_timestep(str(tmp_path), 0, depth, vx, vy)

    _, max_ms, mean_ms = flood_model.compute_max_velocity_from_timesteps(
        str(tmp_path), NODATA, dry_thresh=0.05)
    assert max_ms == 2.0, f"the near-dry artifact leaked into the real result: {max_ms}"
    assert mean_ms == 2.0


def test_same_cell_wet_later_still_excludes_its_own_earlier_dry_spike():
    # A real, slightly harder case: the SAME cell is near-dry (with a
    # spurious spike) at t=0, then genuinely wet with a real, moderate
    # velocity at t=1 -- confirms the fix gates PER-TIMESTEP, not just
    # "was this cell ever wet at any point in the whole run" (the exact
    # real bug found live: that coarser test let an all-time peak search
    # find the spike from a DIFFERENT, dry moment).
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        depth0 = np.full((4, 4), NODATA)
        vx0 = np.zeros((4, 4))
        vy0 = np.zeros((4, 4))
        depth0[1, 1] = 0.001
        vx0[1, 1] = 900.0  # spurious, must never surface

        depth1 = np.full((4, 4), NODATA)
        vx1 = np.zeros((4, 4))
        vy1 = np.zeros((4, 4))
        depth1[1, 1] = 0.8
        vx1[1, 1] = 1.5  # the real, genuine value

        _write_timestep(d, 0, depth0, vx0, vy0)
        _write_timestep(d, 1, depth1, vx1, vy1)

        _, max_ms, mean_ms = flood_model.compute_max_velocity_from_timesteps(
            d, NODATA, dry_thresh=0.05)
        assert max_ms == 1.5, f"a dry-moment spike from an earlier timestep leaked in: {max_ms}"


def test_no_wet_cells_returns_zero_not_crash():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        depth = np.full((4, 4), NODATA)
        depth[1, 1] = 0.001  # below dry_thresh everywhere
        vx = np.zeros((4, 4))
        vy = np.zeros((4, 4))
        _write_timestep(d, 0, depth, vx, vy)
        _, max_ms, mean_ms = flood_model.compute_max_velocity_from_timesteps(d, NODATA, dry_thresh=0.05)
        assert max_ms == 0.0
        assert mean_ms == 0.0


def test_no_timestep_files_returns_none_not_crash(tmp_path):
    grid, max_ms, mean_ms = flood_model.compute_max_velocity_from_timesteps(str(tmp_path), NODATA)
    assert grid is None and max_ms is None and mean_ms is None
