"""
Real Areal Reduction Factor (ARF) + spatially-distributed, storm-centered
design rainfall.

ARF: the UK Flood Studies Report / Flood Estimation Handbook equation
(Keers & Wescott, 1977; as reproduced in the ReFH2 technical guide's
Design Rainfall Depth section, Table 3): ARF = 1 - b*D^-a, with real
published area-banded coefficients for a and b. This converts a POINT
design rainfall depth (this app's existing CMIP6/Gumbel design-storm
lookup, or a manually-entered value) into the correct AREA-AVERAGE depth
for the AOI's own real area -- a point value applied uniformly over an
area systematically overstates the true areal total, since real storm
cells are never perfectly uniform over a large area.

Storm centering + exponential decay: LISFLOOD-FP's GPU/CUDA ACC solver's
own uniform-rain kernel (cuda_acc_solver.cu's update_uniform_rain_func)
broadcasts ONE scalar rain rate identically to every cell -- confirmed
directly from source -- so true spatial rainfall needs the solver's real,
separate DynamicRain/NetCDF mechanism (rain/rain.tpp, genuinely consumed
by the GPU path at cuda_acc_simulate.cu's updateACC<<<>>> kernel call).
This module builds a real exponential-decay spatial weight field centered
on a chosen (or AOI-centroid default) storm center, normalized so the
AOI's own spatial MEAN weight is exactly 1.0 -- so multiplying the
ARF-adjusted areal-average total rainfall by this field preserves the
real total storm volume (mass-conserving), while concentrating more of it
near the chosen center and less at the AOI's edges, exactly like a real
storm cell does.
"""
import numpy as np
import netCDF4

# Keers & Wescott (1977) ARF = 1 - b*D^-a, area-banded coefficients
# (A in km^2). Duration D is assumed in HOURS, matching this app's own
# rain_mm_hr/duration_hr convention -- the source technical guide doesn't
# state D's units explicitly, but its own worked examples use durations
# of 1/2/6/24 hours, the same convention already used throughout this app.
ARF_MIN_AREA_KM2 = 1.0  # below this, real guidance is ARF is not required (~1.0)


def _arf_a_b(area_km2):
    A = max(area_km2, ARF_MIN_AREA_KM2)
    lnA = np.log(A)
    if A <= 20:
        a = 0.40 - 0.0208 * np.log(4.6 - lnA)
        b = 0.0394 * A ** 0.354
    elif A < 100:
        a = 0.40 - 0.00382 * (4.6 - lnA) ** 2
        b = 0.0394 * A ** 0.354
    elif A < 500:
        a = 0.40 - 0.00382 * (4.6 - lnA) ** 2
        b = 0.0627 * A ** 0.254
    elif A < 1000:
        a = 0.40 - 0.0208 * np.log(lnA - 4.6)
        b = 0.0627 * A ** 0.254
    else:
        a = 0.40 - 0.0208 * np.log(lnA - 4.6)
        b = 0.1050 * A ** 0.180
    return a, b


def areal_reduction_factor(area_km2, duration_hr):
    """Real ARF (Keers & Wescott 1977 / FSR-FEH) -- returns 1.0 (no
    reduction) for AOIs below ~1km^2, where real published guidance says
    ARF isn't meaningfully different from 1. Clamped to (0, 1]: this is
    an empirical fit, not guaranteed well-behaved arbitrarily far outside
    its calibration range."""
    if area_km2 < ARF_MIN_AREA_KM2:
        return 1.0
    a, b = _arf_a_b(area_km2)
    D = max(duration_hr, 0.25)
    arf = 1.0 - b * (D ** (-a))
    return float(np.clip(arf, 0.05, 1.0))


def exponential_decay_weights(header, src_crs, valid_mask, center_lon, center_lat, decay_length_m=None):
    """Real 2D exponential-decay spatial weight field: w(x,y) = exp(-r/L),
    r = real distance (metres, in the DEM's own projected CRS) from the
    chosen storm center. Normalized so mean(w) over valid AOI cells == 1.0
    -- multiplying an areal-average rainfall total by this field preserves
    the real total storm volume while concentrating it near the center.

    decay_length_m defaults to the AOI's own effective radius (sqrt(valid
    area / pi)) -- an e-folding length matching the AOI's own scale, so a
    small AOI gets a tight storm core and a large one a broader one,
    rather than one fixed distance that would be too sharp or too flat
    depending on AOI size. This is a real, stated modeling choice (there
    is no universal "correct" decay length without site-specific storm
    micro-structure data), analogous to how this app already documents
    its channel-conditioning width/depth as a real, stated approximation
    rather than a surveyed value."""
    from pyproj import Transformer

    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]
    transformer = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True)
    cx, cy = transformer.transform(center_lon, center_lat)

    cols = xll + (np.arange(ncols) + 0.5) * cs
    rows = yll + (nrows - np.arange(nrows) - 0.5) * cs  # row 0 = north (top)
    xx, yy = np.meshgrid(cols, rows)
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    if decay_length_m is None:
        n_valid = int(valid_mask.sum())
        area_m2 = n_valid * cs * cs
        decay_length_m = max(np.sqrt(area_m2 / np.pi), cs)

    w = np.exp(-r / decay_length_m)
    mean_w = w[valid_mask].mean() if valid_mask.any() else 1.0
    if mean_w <= 0:
        return np.ones_like(w), decay_length_m
    return w / mean_w, decay_length_m


def exponential_decay_weights_at(header, src_crs, valid_mask, center_lon, center_lat, decay_length_m):
    """Per-call version of exponential_decay_weights for use inside a
    moving-storm time loop (see write_rain_netcdf_moving) -- identical
    real formula (w(x,y)=exp(-r/L), normalized to mean=1 over the valid
    AOI), but REQUIRES decay_length_m (not None): the AOI-scale default
    computed by exponential_decay_weights doesn't depend on the center
    location, so a moving-storm caller computes it ONCE (outside the
    loop) and passes it here every step, rather than silently
    recomputing the identical value on every one of potentially dozens
    of time slices. Returns just the weights array (the caller already
    knows its own decay_length_m, unlike exponential_decay_weights's own
    callers who may be discovering it for the first time)."""
    w, _ = exponential_decay_weights(header, src_crs, valid_mask, center_lon, center_lat, decay_length_m)
    return w


def write_rain_netcdf(path, header, weights, peak_mm_hr, duration_hr, sim_hours, rise_frac=0.5):
    """Real per-cell, per-timestep rainfall grid for LISFLOOD-FP's own
    DynamicRain mechanism (rain/rain.tpp's read_file_netCDF_start/
    read_file_netCDF, confirmed real GPU consumption at
    cuda_acc_simulate.cu). Same triangular design-storm TIME shape as
    write_rain_file (rise to a peak at half the duration, then recede),
    but each cell's own accumulated depth is scaled by `weights` (see
    exponential_decay_weights) -- so every cell shares the same real
    storm timing, only the total local depth varies spatially.

    File format confirmed directly from LISFLOOD-FP 8.2's own source, not
    guessed: dims x/y (cell-CENTER coords, same projected CRS/resolution
    as the DEM so rate_at_cell's tile math is an exact 1:1 mapping, no
    resampling), y strictly DESCENDING (row 0 = north, matching
    DynamicRain's own geometry_.dy = ys[0]-ys[1] > 0 assumption); dim
    `time` (hours, matching update_time's t/3600.0 conversion); variable
    `rainfall_depth(time,y,x)` = rainfall ACCUMULATED (mm) over the
    interval since the PREVIOUS time entry (not an instantaneous rate --
    DynamicRain itself divides by the real dt between entries to recover
    a rate). All doubles (NUMERIC_TYPE=double in this build's
    config.sailab.cmake, confirmed from lisflood.h)."""
    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]
    xs = xll + (np.arange(ncols) + 0.5) * cs
    ys = (yll + nrows * cs) - (np.arange(nrows) + 0.5) * cs  # descending, row 0 = north

    # Same real triangular hyetograph breakpoints as write_rain_file,
    # but here we need per-INTERVAL accumulated depth, not instantaneous
    # rate -- sample the rate at a modest number of sub-storm times so the
    # rise/recession shape is real, not a single flat block.
    t_peak = duration_hr * rise_frac
    def rate_mm_hr(t):
        if t <= 0 or t >= duration_hr:
            return 0.0
        if t <= t_peak:
            return peak_mm_hr * (t / max(t_peak, 1e-9))
        return peak_mm_hr * ((duration_hr - t) / max(duration_hr - t_peak, 1e-9))

    n_sub = 12  # real sub-storm resolution for the rise/recession shape
    times_hr = sorted(set([0.0, t_peak] + [duration_hr * i / n_sub for i in range(n_sub + 1)] + [duration_hr, sim_hours]))
    times_hr = [t for t in times_hr if t <= sim_hours]
    if times_hr[-1] < sim_hours:
        times_hr.append(sim_hours)

    ds = netCDF4.Dataset(path, "w", format="NETCDF4")
    try:
        ds.createDimension("x", ncols)
        ds.createDimension("y", nrows)
        ds.createDimension("time", len(times_hr))
        vx = ds.createVariable("x", "f8", ("x",))
        vy = ds.createVariable("y", "f8", ("y",))
        vt = ds.createVariable("time", "f8", ("time",))
        vr = ds.createVariable("rainfall_depth", "f8", ("time", "y", "x"))
        vx[:] = xs
        vy[:] = ys
        vt[:] = times_hr
        prev_t = times_hr[0]
        for k, t in enumerate(times_hr):
            if k == 0:
                vr[k, :, :] = 0.0
                prev_t = t
                continue
            # Real accumulated depth over (prev_t, t]: trapezoidal
            # integral of the triangular rate profile, per cell scaled by
            # its own real spatial weight. t_peak is always one of the
            # breakpoints (see times_hr above), so no interval straddles
            # the rise/recession kink -- a plain trapezoidal average of
            # the two endpoint rates is exact for each piecewise-linear
            # segment, not an approximation.
            mean_rate_mm_hr = 0.5 * (rate_mm_hr(prev_t) + rate_mm_hr(t))
            depth_mm_point = mean_rate_mm_hr * (t - prev_t)
            vr[k, :, :] = (depth_mm_point * weights).astype("float64")
            prev_t = t
    finally:
        ds.close()
    # Same real (rate_mm_hr, time_hr) pair format write_rain_file returns
    # (used by flood_model.py's build_report_html to plot the storm's own
    # hyetograph) -- here the AREAL-AVERAGE rate at each real breakpoint,
    # since the true per-cell rate varies spatially and a report plot
    # needs one representative curve.
    return [(rate_mm_hr(t), t) for t in times_hr]


def write_rain_netcdf_moving(path, header, src_crs, valid_mask, center_lon0, center_lat0,
                              velocity_lon_per_hr, velocity_lat_per_hr, peak_mm_hr, duration_hr,
                              sim_hours, decay_length_m=None, rise_frac=0.5):
    """Real MOVING-storm-center extension of write_rain_netcdf, additive
    -- write_rain_netcdf and its fixed-center callers are completely
    unaffected. The storm's exponential-decay weight field (see
    exponential_decay_weights) is recomputed at EVERY real NetCDF time
    slice using a center that has translated linearly from
    (center_lon0, center_lat0) at a real, constant, user/model-predicted
    velocity (degrees/hour) -- center(t) = center0 + velocity * t_hours
    -- instead of reusing one static weights array for the whole event.
    This is a real, deliberate simplification (constant velocity, no
    curvature/acceleration) consistent with this app's own existing
    triangular-hyetograph design-storm shape being a documented
    simplification of a real storm's true (never perfectly triangular)
    time profile -- not a claim that real storms move in perfectly
    straight lines forever, just the natural first-order extension of a
    fixed-center storm to a moving one.

    Everything else mirrors write_rain_netcdf EXACTLY: the same real
    triangular hyetograph rise/recession time discretization
    (t_peak/times_hr breakpoints), the same real per-interval trapezoidal
    accumulated-depth integration, the same NetCDF dims/variable
    layout LISFLOOD-FP's DynamicRain mechanism expects. The only
    difference is that the per-cell spatial weight multiplying each
    interval's depth is no longer one fixed array -- it's the AVERAGE of
    the weight field evaluated at the interval's two endpoint times
    (prev_t, t), a real (if simple) way to account for the storm having
    moved partway across the AOI during that interval rather than
    freezing it at either endpoint.

    decay_length_m: computed ONCE (same AOI-scale default as
    exponential_decay_weights, if not given) and held fixed for the whole
    event -- a moving storm's CORE SIZE isn't modeled as changing over
    time, only its position.

    Returns (rain_pts, decay_length_m) -- rain_pts is the same
    (rate_mm_hr, time_hr) list write_rain_netcdf returns (the real
    areal-average hyetograph, for the report plot); decay_length_m is
    returned too (unlike write_rain_netcdf) since a moving-storm caller
    typically hasn't computed/logged it separately beforehand."""
    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]
    xs = xll + (np.arange(ncols) + 0.5) * cs
    ys = (yll + nrows * cs) - (np.arange(nrows) + 0.5) * cs  # descending, row 0 = north

    if decay_length_m is None:
        n_valid = int(valid_mask.sum())
        area_m2 = n_valid * cs * cs
        decay_length_m = max(np.sqrt(area_m2 / np.pi), cs)

    t_peak = duration_hr * rise_frac

    def rate_mm_hr(t):
        if t <= 0 or t >= duration_hr:
            return 0.0
        if t <= t_peak:
            return peak_mm_hr * (t / max(t_peak, 1e-9))
        return peak_mm_hr * ((duration_hr - t) / max(duration_hr - t_peak, 1e-9))

    n_sub = 12
    times_hr = sorted(set([0.0, t_peak] + [duration_hr * i / n_sub for i in range(n_sub + 1)] + [duration_hr, sim_hours]))
    times_hr = [t for t in times_hr if t <= sim_hours]
    if times_hr[-1] < sim_hours:
        times_hr.append(sim_hours)

    def weights_at_time(t_hr):
        lon = center_lon0 + velocity_lon_per_hr * t_hr
        lat = center_lat0 + velocity_lat_per_hr * t_hr
        return exponential_decay_weights_at(header, src_crs, valid_mask, lon, lat, decay_length_m)

    ds = netCDF4.Dataset(path, "w", format="NETCDF4")
    try:
        ds.createDimension("x", ncols)
        ds.createDimension("y", nrows)
        ds.createDimension("time", len(times_hr))
        vx = ds.createVariable("x", "f8", ("x",))
        vy = ds.createVariable("y", "f8", ("y",))
        vt = ds.createVariable("time", "f8", ("time",))
        vr = ds.createVariable("rainfall_depth", "f8", ("time", "y", "x"))
        vx[:] = xs
        vy[:] = ys
        vt[:] = times_hr
        prev_t = times_hr[0]
        prev_w = weights_at_time(prev_t)
        for k, t in enumerate(times_hr):
            if k == 0:
                vr[k, :, :] = 0.0
                prev_t, prev_w = t, weights_at_time(t)
                continue
            w_t = weights_at_time(t)
            mean_rate_mm_hr = 0.5 * (rate_mm_hr(prev_t) + rate_mm_hr(t))
            depth_mm_point = mean_rate_mm_hr * (t - prev_t)
            mean_w = 0.5 * (prev_w + w_t)
            vr[k, :, :] = (depth_mm_point * mean_w).astype("float64")
            prev_t, prev_w = t, w_t
    finally:
        ds.close()
    return [(rate_mm_hr(t), t) for t in times_hr], decay_length_m
