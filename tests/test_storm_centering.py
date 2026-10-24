"""
Fast, pure-function tests for storm_utils.py's spatial rain-weighting
(exponential_decay_weights) and NetCDF writers (write_rain_netcdf /
write_rain_netcdf_moving) -- no LISFLOOD-FP subprocess involved, so these
stay in the fast (`not slow`) suite even though they exercise real
spatial/temporal logic.

Covers the specific property CLAUDE.md/the task both call out: the FIXED
and MOVING storm-center NetCDF rain paths must produce genuinely DIFFERENT
per-cell depth patterns once the storm has had time to translate -- if a
future edit made write_rain_netcdf_moving silently degenerate into the
fixed-center behavior (e.g. dropping the velocity term), this suite would
keep passing on every other test in this file while missing that one
regression, so it gets its own direct check.
"""
import os
import sys

import netCDF4
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storm_utils  # noqa: E402


def _small_header(ncols=21, nrows=21, cellsize=20.0, xll=431260.0, yll=3312480.0):
    """A realistic-ish nonzero-origin header (UTM-42N scale coordinates),
    same discipline as test_cuda_boundary_inflow_realistic_dem.py -- not
    load-bearing for this specific bug, but costs nothing and keeps every
    synthetic-DEM test in this suite consistent about not defaulting to a
    0,0 origin that could mask an origin-dependent bug elsewhere."""
    return dict(ncols=ncols, nrows=nrows, xllcorner=xll, yllcorner=yll, cellsize=cellsize)


def test_exponential_decay_weights_peak_at_center_cell():
    """Real physics of the weight field: the cell nearest the chosen storm
    center must have the single highest weight, and weight must fall off
    monotonically-ish with distance (checked via the corner being lowest)."""
    header = _small_header()
    valid_mask = np.ones((header["nrows"], header["ncols"]), dtype=bool)
    # Center of the domain in real projected coords -> back to lon/lat.
    from pyproj import Transformer
    cx = header["xllcorner"] + header["ncols"] * header["cellsize"] / 2.0
    cy = header["yllcorner"] + header["nrows"] * header["cellsize"] / 2.0
    to_wgs84 = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True).transform
    center_lon, center_lat = to_wgs84(cx, cy)

    w, decay_len = storm_utils.exponential_decay_weights(
        header, "EPSG:32642", valid_mask, center_lon, center_lat)

    assert decay_len > 0
    center_row, center_col = header["nrows"] // 2, header["ncols"] // 2
    assert w[center_row, center_col] == pytest.approx(w.max())
    # A far corner must be weighted (much) lower than the center.
    assert w[0, 0] < w[center_row, center_col]
    # Normalization: mean weight over the valid AOI is 1.0.
    assert w[valid_mask].mean() == pytest.approx(1.0, rel=1e-6)


def test_exponential_decay_weights_zero_area_edge_case_no_crash():
    """valid_mask all-False must not divide-by-zero/crash: the function's
    own documented fallback (mean_w defaults to 1.0 when there are no
    valid cells to average over) means it returns the raw, un-normalized
    exp(-r/L) field rather than NaN -- checked here as "finite everywhere
    and never negative" (a real weight field), not a crash/NaN poison."""
    header = _small_header()
    valid_mask = np.zeros((header["nrows"], header["ncols"]), dtype=bool)
    w, _ = storm_utils.exponential_decay_weights(header, "EPSG:32642", valid_mask, 73.0, 31.5)
    assert np.isfinite(w).all()
    assert (w >= 0).all()


def test_fixed_vs_moving_storm_netcdf_produce_different_spatial_patterns(tmp_path):
    """THE real regression check for storm-centering: with a real nonzero
    velocity, the moving-center rain NetCDF's LATER timestep must show a
    genuinely different spatial rainfall pattern than the fixed-center
    one -- proving the storm actually translated, not just that the file
    parses. Both use the identical grid/duration/peak so any difference is
    attributable only to the moving center."""
    header = _small_header(ncols=31, nrows=31, cellsize=30.0)
    valid_mask = np.ones((header["nrows"], header["ncols"]), dtype=bool)
    from pyproj import Transformer
    to_wgs84 = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True).transform
    # Start the storm center at the domain's own SW corner so a real
    # eastward/northward velocity clearly sweeps it across the grid.
    center_lon0, center_lat0 = to_wgs84(header["xllcorner"] + 50.0, header["yllcorner"] + 50.0)

    peak_mm_hr, duration_hr, sim_hours = 40.0, 2.0, 2.5

    weights_fixed, decay_len = storm_utils.exponential_decay_weights(
        header, "EPSG:32642", valid_mask, center_lon0, center_lat0)
    fixed_path = tmp_path / "fixed.nc"
    storm_utils.write_rain_netcdf(str(fixed_path), header, weights_fixed, peak_mm_hr, duration_hr, sim_hours)

    moving_path = tmp_path / "moving.nc"
    # A real, substantial velocity (degrees/hr) so the storm crosses a
    # meaningful fraction of this small domain within duration_hr.
    vel_lon_per_hr = 0.01
    vel_lat_per_hr = 0.01
    storm_utils.write_rain_netcdf_moving(
        str(moving_path), header, "EPSG:32642", valid_mask, center_lon0, center_lat0,
        vel_lon_per_hr, vel_lat_per_hr, peak_mm_hr, duration_hr, sim_hours,
        decay_length_m=decay_len)

    with netCDF4.Dataset(str(fixed_path)) as ds_f, netCDF4.Dataset(str(moving_path)) as ds_m:
        rain_f = ds_f.variables["rainfall_depth"][:]
        rain_m = ds_m.variables["rainfall_depth"][:]
        times_f = ds_f.variables["time"][:]
        times_m = ds_m.variables["time"][:]

    # Same time discretization (both derived identically from duration_hr/sim_hours).
    assert np.allclose(times_f, times_m)
    assert rain_f.shape == rain_m.shape

    # Total areal input (summed over all time/space) should be broadly
    # similar -- both are the same real triangular hyetograph total, just
    # redistributed differently in space over time -- but the PER-CELL,
    # PER-TIMESTEP pattern at a slice with real active rainfall must
    # genuinely differ, since the moving storm has, by then, translated
    # its core away from where the fixed storm keeps it. Use the LAST
    # slice with any real rainfall in it (not the final NetCDF index,
    # which covers the drain-down tail after the storm ends at
    # duration_hr, where both fields are correctly all-zero and therefore
    # trivially identical -- that would be testing nothing).
    active_idx = max(i for i, t in enumerate(times_f) if float(rain_f[i].sum()) > 0)
    assert not np.allclose(rain_f[active_idx], rain_m[active_idx], atol=1e-9), (
        "fixed- and moving-center rain fields were identical at a real active "
        "rainfall timestep -- the moving-center path is not actually moving the storm"
    )

    # Real, physically sane property: the moving storm's peak-weighted
    # cell at this slice should have shifted relative to the fixed
    # storm's own (still-centered-at-start) peak location.
    peak_row_fixed, peak_col_fixed = np.unravel_index(np.argmax(rain_f[active_idx]), rain_f[active_idx].shape)
    peak_row_moving, peak_col_moving = np.unravel_index(np.argmax(rain_m[active_idx]), rain_m[active_idx].shape)
    # row 0 = north, so a real northward/eastward-moving storm should end
    # up at a smaller row index (further north) and larger col index
    # (further east) than the fixed storm's peak, or at least not be
    # identical -- checked as "moved" rather than pinning an exact cell,
    # since the precise peak cell is sensitive to grid/weight discretization.
    assert (peak_row_moving, peak_col_moving) != (peak_row_fixed, peak_col_fixed)


def test_moving_storm_zero_velocity_matches_fixed_storm():
    """Sanity/consistency check: a moving storm with velocity exactly 0 is
    mathematically the same as a fixed storm at that same center -- if
    this ever stopped holding, it would mean write_rain_netcdf_moving's
    per-interval weight-averaging introduced some spurious drift even
    with no real motion."""
    header = _small_header(ncols=15, nrows=15, cellsize=25.0)
    valid_mask = np.ones((header["nrows"], header["ncols"]), dtype=bool)
    from pyproj import Transformer
    to_wgs84 = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True).transform
    lon0, lat0 = to_wgs84(header["xllcorner"] + 150.0, header["yllcorner"] + 150.0)

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        fixed_path = os.path.join(d, "fixed.nc")
        moving_path = os.path.join(d, "moving.nc")
        weights_fixed, decay_len = storm_utils.exponential_decay_weights(
            header, "EPSG:32642", valid_mask, lon0, lat0)
        storm_utils.write_rain_netcdf(fixed_path, header, weights_fixed, 30.0, 1.0, 1.5)
        storm_utils.write_rain_netcdf_moving(
            moving_path, header, "EPSG:32642", valid_mask, lon0, lat0,
            0.0, 0.0, 30.0, 1.0, 1.5, decay_length_m=decay_len)

        with netCDF4.Dataset(fixed_path) as ds_f, netCDF4.Dataset(moving_path) as ds_m:
            rain_f = ds_f.variables["rainfall_depth"][:]
            rain_m = ds_m.variables["rainfall_depth"][:]

    assert np.allclose(rain_f, rain_m, atol=1e-6)
