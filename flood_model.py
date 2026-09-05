#!/usr/bin/env python3
"""
PluviSim — a rainfall-flooding front end for the LISFLOOD-FP engine.

Give it high-level values (rainfall intensity/duration, terrain size/slope
or a real DEM, friction) and it builds every LISFLOOD-FP input file
(.dem.ascii, .rain, .bci, .par), runs the compiled inertial solver, and
produces a full technical report:
  - flood extent (area, % of domain) and depth statistics
  - DEFRA (2006) hazard-to-people classification, from the solver's own
    depth x (velocity + 1.5) hazard rating
  - time-of-arrival mapping (how fast floodwater reaches each cell)
  - mass-balance sanity check
  - a self-contained HTML report (report.html) with map plates and
    interactive charts
  - a JSON summary for machine consumption

Usage:
  # fully synthetic terrain, quick demo
  python3 flood_model.py --rain-mm-hr 40 --duration-hr 2

  # real DEM (ARC/INFO ASCII format)
  python3 flood_model.py --dem mysite.dem.ascii \
      --rain-mm-hr 30 --duration-hr 3 --friction 0.04 --outlet-edges S,E
"""
import argparse
import base64
import io
import json
import glob
import os
import shutil
import subprocess
import sys
import time

import numpy as np

# GPU-accelerated LISFLOOD-FP 8.1/8.2 (built from source against CUDA 13.0
# for this machine's GB10 GPU -- see CLAUDE.md for the real build fixes
# this needed) replaces the older CPU-only v5.9 binary this app used to
# run. Verified equivalent first, not just faster: a real benchmark run
# (Bahawalpur, 104 km^2) produced bit-identical results on this new
# binary's own CPU path vs the old v5.9 binary, and the GPU path matched
# within normal floating-point/reduction-order tolerance (0.003m max
# diff on a 2.8m peak depth) while running ~10-11x faster (2.0s vs
# 23.7s). Same "acceleration" (local inertial) physics either way --
# this is a faster/GPU execution of the identical scheme, not a
# different solver.
LISFLOOD_DIR = os.path.expanduser("~/flood/lisflood-fp-8.2/LISFLOOD-FP/build")
LISFLOOD_BIN = os.path.join(LISFLOOD_DIR, "lisflood")
LISFLOOD_CUDA_LIB_DIR = "/usr/local/cuda/lib64"

MODEL_NAME = "PluviSim"
MODEL_TAGLINE = "Rainfall flood simulation on the LISFLOOD-FP inertial solver"

# SCS/NRCS Antecedent Moisture Condition (AMC) describes how saturated the
# ground already was right before the storm being simulated -- it directly
# controls how much infiltration capacity is left. This solver only
# exposes a single flat infiltration rate (no full Curve-Number/Green-Ampt
# soil model), so AMC is applied as a documented multiplier on the user's
# own baseline (AMC-II/"average") --infiltration-mm-hr, rather than a full
# SCS-CN recalculation (which needs a real curve number + hydrologic soil
# group this app doesn't model). The three reference points below
# approximate the relative retention-capacity ratios the standard
# CN(II)->CN(I)/CN(III) conversion implies: AMC-I (dry) soil has
# substantially more room to absorb rain than average; AMC-III (already
# saturated, e.g. after days of prior rain) has very little left.
#
# --amc takes a continuous 0-100 slider position (0=I fully dry, 50=II
# average, 100=III fully saturated) rather than forcing a pick among
# exactly three discrete presets -- real antecedent soil moisture is a
# continuum, not three buckets, so a UI slider lets the user land anywhere
# between "drier than average" and "wetter than average" too. The
# multiplier is linearly interpolated between the same three real
# reference points, not a new invented curve.
AMC_REFERENCE_POINTS = [(0, 1.8), (50, 1.0), (100, 0.25)]


def amc_infiltration_multiplier(amc_slider):
    pts = AMC_REFERENCE_POINTS
    x = max(0.0, min(100.0, float(amc_slider)))
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return pts[-1][1]


def amc_label(amc_slider):
    x = max(0.0, min(100.0, float(amc_slider)))
    if x < 33.3:
        return f"dry-leaning ({x:.0f}/100, ref AMC-I..II)"
    if x > 66.7:
        return f"wet-leaning ({x:.0f}/100, ref AMC-II..III)"
    return f"average ({x:.0f}/100, ref AMC-II)"

# DEFRA (2006) "Flood Risks to People" hazard-rating bands.
# HR = depth * (velocity + 1.5)  -- computed inside the LISFLOOD-FP solver.
HAZARD_BANDS = [
    ("low", 0.75, "#0ca30c"),
    ("moderate", 1.25, "#fab219"),
    ("significant", 2.00, "#ec835a"),
    ("extreme", float("inf"), "#d03b3b"),
]


# --------------------------------------------------------------------------
# ARC/INFO ASCII raster I/O
# --------------------------------------------------------------------------
def read_ascii(path):
    with open(path) as f:
        header = {}
        for _ in range(6):
            k, v = f.readline().split()
            header[k.lower()] = float(v) if ("." in v or v.lstrip("-").isdigit() is False) else float(v)
        data = np.loadtxt(f)
    return header, data


def write_ascii(path, header, data, nodata=-9999):
    with open(path, "w") as f:
        f.write(f"ncols         {int(header['ncols'])}\n")
        f.write(f"nrows         {int(header['nrows'])}\n")
        f.write(f"xllcorner     {header['xllcorner']}\n")
        f.write(f"yllcorner     {header['yllcorner']}\n")
        f.write(f"cellsize      {header['cellsize']}\n")
        f.write(f"NODATA_value  {nodata}\n")
        np.savetxt(f, data, fmt="%.4f")


# --------------------------------------------------------------------------
# Synthetic terrain generator (used only if --dem is not supplied)
# --------------------------------------------------------------------------
def _box_blur(a, passes=8, k=7):
    """Cheap Gaussian-smoothing approximation (no scipy dependency): repeated
    1D box filters along both axes converge to a Gaussian by the CLT."""
    pad = k // 2
    kernel = np.ones(k) / k
    out = a.copy()
    for _ in range(passes):
        out = np.apply_along_axis(
            lambda m: np.convolve(np.pad(m, pad, mode="edge"), kernel, mode="valid"),
            axis=1, arr=out)
        out = np.apply_along_axis(
            lambda m: np.convolve(np.pad(m, pad, mode="edge"), kernel, mode="valid"),
            axis=0, arr=out)
    return out


def make_synthetic_dem(width_m, height_m, cellsize, slope_pct, seed=1):
    ncols = max(10, int(width_m / cellsize))
    nrows = max(10, int(height_m / cellsize))
    rng = np.random.default_rng(seed)

    y = np.arange(nrows)[:, None]
    elev = 20.0 - (y * cellsize * slope_pct / 100.0)
    elev = np.repeat(elev, ncols, axis=1)

    raw_noise = rng.standard_normal((nrows, ncols))
    smooth_noise = _box_blur(raw_noise)
    smooth_noise *= 1.5 / (smooth_noise.std() + 1e-9)
    elev = elev + smooth_noise

    header = dict(ncols=ncols, nrows=nrows, xllcorner=0.0, yllcorner=0.0,
                  cellsize=float(cellsize))
    return header, elev


# --------------------------------------------------------------------------
# Input file writers
# --------------------------------------------------------------------------
def write_rain_file(path, peak_mm_hr, duration_hr, sim_hours):
    """Symmetric triangular design storm peaking at half the storm duration."""
    t_peak = duration_hr / 2.0
    pts = [(0.0, 0.0), (peak_mm_hr, t_peak), (0.0, duration_hr), (0.0, sim_hours)]
    with open(path, "w") as f:
        f.write("# design storm generated by PluviSim\n")
        f.write(f"{len(pts)}   hours\n")
        for rate, t in pts:
            f.write(f"{rate:.4f}   {t:.4f}\n")
    return pts


def write_bdy_file(path, name, peak_m3s, duration_hr, sim_hours, span_m):
    """Real time-varying discharge boundary (QVAR), a symmetric triangular
    design hydrograph peaking at half the inflow duration -- the exact same
    shape/logic as write_rain_file's design storm, applied to river
    discharge instead of rainfall, so a fluvial event rises, peaks, and
    recedes instead of the earlier constant (QFIX) inflow just switching on
    and staying flat for the whole run. `name` must match the QVAR
    reference written into the .bci file for the same edge.

    LISFLOOD-FP's own .bdy parser (input.cpp's LoadTimeSeries) expects
    lines as "<value> <time>" (value first, confirmed from source -- the
    opposite of what might be assumed) under a header block:
        <comment line, always skipped>
        <name>
        <count> <time units>
        <value> <time>
        ...
    Values are per-unit-width flux (m2/s), same convention QFIX already
    used -- the solver multiplies by the boundary segment length (span_m)
    to recover the real total discharge (m3/s)."""
    t_peak = duration_hr / 2.0
    pts = [(0.0, 0.0), (peak_m3s / span_m, t_peak), (0.0, duration_hr), (0.0, sim_hours)]
    with open(path, "w") as f:
        f.write("# river discharge hydrograph generated by SAILAB\n")
        f.write(f"{name}\n")
        f.write(f"{len(pts)}   hours\n")
        for q_per_width, t in pts:
            f.write(f"{q_per_width:.6f}   {t:.4f}\n")
    return pts


def write_bci_file(path, header, outlet_edges, inflow_edge=None, inflow_m3s=0.0,
                    inflow_bdy_name=None, inflow_start_m=None, inflow_end_m=None):
    """outlet_edges: edges water may freely drain from (FREE).
    inflow_edge: an optional edge carrying discharge representing a river/
    stream entering the domain there -- e.g. compound fluvial+pluvial
    flooding. inflow_edge takes priority over outlet_edges on the same edge.

    inflow_bdy_name: when given, the inflow edge references a real
    time-varying hydrograph (QVAR, from a .bdy file written by
    write_bdy_file) instead of a constant discharge (QFIX) -- a real
    rising/peaking/receding flood wave instead of a flat, instantly-on
    inflow. Falls back to QFIX (this app's original behaviour) when not
    given, so existing constant-inflow callers are unaffected.

    inflow_start_m/inflow_end_m: when given, the discharge is applied only
    over this sub-segment of inflow_edge (DEM-local metres measured from
    that edge's own start -- 0 at the edge's own first corner, up to the
    edge's own span). This is what lets a real river's discharge
    concentrate into its own real channel width instead of smearing across
    the WHOLE domain edge -- spreading, say, 300 m^3/s across a 3km-wide
    AOI edge dilutes it to a flux too thin to ever build up and overflow a
    channel; concentrating the same discharge into a ~30m channel-width
    segment lets it actually pond/overtop realistically. The rest of that
    same edge still gets FREE (if it's also a listed outlet) or CLOSED,
    exactly as it would without an inflow. Omitting these (the default)
    keeps the original whole-edge behaviour unchanged, for backward
    compatibility.

    IMPORTANT, found the hard way: LISFLOOD-FP's .bci start/finish are NOT
    relative to the DEM's own corner at 0 -- confirmed directly from
    input.cpp (LoadBCs fscanf's Parptr->blx/bly straight from the DEM
    ASCII header's own xllcorner/yllcorner, overwriting lisflood.cpp's
    initial 0.0 default) and from a live test: a .bci line written as
    "N 0 4047.8 FREE" against a real Pakistan-UTM DEM (xllcorner ~870793)
    printed "FREE ... start 870793.527313 end 870793.527313" at runtime --
    both ends clamped to the same point, a truly zero-width segment, since
    0 and 4047.8 are both far less than the real xllcorner and clamp UP to
    it identically. This silently zeroed out EVERY custom boundary segment
    (inflow AND, previously, every whole-edge FREE/QFIX/QVAR line this
    module has ever written) on any real cropped DEM -- confirmed with a
    real 6700 m^3/s river hydrograph producing exactly 0.0 m depth
    everywhere, at every timestep, on a real AOI (res.mass's Vol column
    stayed at exactly 0.0 for the full run, not just below a display
    threshold). A synthetic DEM (make_synthetic_dem, xllcorner=0) never
    exposed this, since 0-to-span coordinates happen to already be
    absolute there. Fixed by adding the DEM's own xllcorner (N/S edges) or
    yllcorner (E/W edges) to every coordinate written below."""
    ncols, nrows, cs = header["ncols"], header["nrows"], header["cellsize"]
    width, height = ncols * cs, nrows * cs
    xll, yll = header["xllcorner"], header["yllcorner"]
    lines = []
    for edge, span, origin in (("N", width, xll), ("E", height, yll), ("S", width, xll), ("W", height, yll)):
        if edge == inflow_edge and inflow_start_m is not None and inflow_end_m is not None:
            s = max(0.0, min(span, inflow_start_m))
            e = max(0.0, min(span, inflow_end_m))
            if e < s:
                s, e = e, s
            rest_bc = "FREE   0.001" if edge in outlet_edges else "CLOSED"
            if inflow_bdy_name:
                inflow_bc = f"QVAR   {inflow_bdy_name}"
            else:
                # Per-unit-width flux (m2/s); the solver multiplies by the
                # segment's own length (e - s), not the whole edge, to
                # recover the real m^3/s -- consistent with the QFIX
                # whole-edge branch below, just over a narrower segment.
                q_per_width = inflow_m3s / max(e - s, 1e-6)
                inflow_bc = f"QFIX   {q_per_width:.6f}"
            if s > 0:
                lines.append(f"{edge}   {origin:.4f}   {origin + s:.4f}   {rest_bc}")
            lines.append(f"{edge}   {origin + s:.4f}   {origin + e:.4f}   {inflow_bc}")
            if e < span:
                lines.append(f"{edge}   {origin + e:.4f}   {origin + span:.4f}   {rest_bc}")
            continue
        if edge == inflow_edge and inflow_bdy_name:
            bc = f"QVAR   {inflow_bdy_name}"
        elif edge == inflow_edge and inflow_m3s > 0:
            # QFIX takes mass flux PER UNIT WIDTH (m2/s); the code multiplies by
            # the boundary segment length to recover the total m3/s you asked for.
            q_per_width = inflow_m3s / span
            bc = f"QFIX   {q_per_width:.6f}"
        elif edge in outlet_edges:
            bc = "FREE   0.001"
        else:
            bc = "CLOSED"
        lines.append(f"{edge}   {origin:.4f}   {origin + span:.4f}   {bc}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# LISFLOOD-FP's own user manual (p.12): "Using the acceleration solver, low
# Manning's friction conditions can cause instabilities and a numerical
# diffusion term [theta] must be included." This app's UI default (0.05)
# is already safely above this, but nothing stopped a user from dialing
# the friction input down toward 0, with no floor and no theta safety net
# -- a real, documented instability trigger with no guard rail.
MIN_FPFRIC = 0.02  # below this, LISFLOOD-FP's own manual warns of ACC-solver instability
LOW_FRIC_THETA = 0.8  # numerical diffusion (theta<1) recommended once friction is this low


def write_par_file(path, dem_name, rain_name, bci_name, resroot, dirroot,
                    sim_time_s, saveint_s, massint_s, cfl, fpfric, initial_tstep,
                    infiltration_m_s=0.0, bdy_name=None, manning_file=None, min_fric_seen=None):
    """fpfric is always written -- LISFLOOD-FP's ACC solver uses it as the
    NODATA/fallback friction for any cell not covered by manning_file (see
    input.cpp's LoadManningsn), and as the only friction value at all when
    manning_file is None. manning_file (a per-cell ARC ASCII Manning's n
    raster, real ESA WorldCover-derived, see damage_utils.lulc_to_manning_grid)
    lets floodplain roughness vary by real land cover instead of one guess
    for the whole AOI -- confirmed to be genuinely consumed by the ACC/CUDA
    solver from source (cuda/acc/cuda_acc_simulate.cu's initialise_manning),
    not just the legacy CPU path.

    min_fric_seen: the lowest friction value that will actually be used
    anywhere in this run (the uniform fpfric if no manning_file, or the real
    per-cell grid's own minimum if one is given) -- decides theta the same
    way a uniform low fpfric already did, so a per-cell grid with a locally
    low-roughness class (e.g. water/bare ground) gets the same instability
    safety net a uniformly-low fpfric would have. Defaults to fpfric when
    not given, preserving old behaviour for callers with no grid on hand."""
    if min_fric_seen is None:
        min_fric_seen = fpfric
    theta = LOW_FRIC_THETA if min_fric_seen < MIN_FPFRIC else 1.0
    with open(path, "w") as f:
        f.write(f"DEMfile          {dem_name}\n")
        f.write(f"resroot          {resroot}\n")
        f.write(f"dirroot          {dirroot}\n")
        f.write(f"sim_time         {sim_time_s:.1f}\n")
        f.write(f"initial_tstep    {initial_tstep:.2f}\n")
        f.write(f"saveint          {saveint_s:.1f}\n")
        f.write(f"massint          {massint_s:.1f}\n")
        f.write("acceleration\n")
        f.write("cuda\n")  # real GPU-accelerated ACC solver -- see LISFLOOD_BIN's own comment
        f.write(f"cfl              {cfl:.2f}\n")
        f.write(f"fpfric           {fpfric:.4f}\n")
        if manning_file:
            f.write(f"manningfile      {manning_file}\n")
        if theta < 1.0:
            # Adds numerical diffusion to the inertial (ACC) scheme --
            # LISFLOOD-FP's own documented mitigation for the low-friction
            # instability above. Only written when actually needed (fpfric
            # below MIN_FPFRIC); LISFLOOD-FP's own default (1.0, no extra
            # diffusion) applies otherwise, unchanged from before.
            f.write(f"theta            {theta:.2f}\n")
        f.write(f"rainfall         {rain_name}\n")
        f.write(f"bcifile          {bci_name}\n")
        if bdy_name:
            f.write(f"bdyfile          {bdy_name}\n")  # real time-varying (QVAR) river inflow hydrograph
        if infiltration_m_s > 0:
            f.write(f"infiltration     {infiltration_m_s:.10f}\n")
        f.write("hazard\n")   # also enables voutput -> velocity + DEFRA hazard rasters
        f.write("comp_out\n")


# --------------------------------------------------------------------------
# Run + parse
# --------------------------------------------------------------------------
def run_lisflood(workdir, par_name, verbose=True):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LISFLOOD_DIR + ":" + LISFLOOD_CUDA_LIB_DIR + ":" + env.get("LD_LIBRARY_PATH", "")
    cmd = [LISFLOOD_BIN] + (["-v"] if verbose else []) + [par_name]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True)
    # A real, repeatedly-observed transient failure on this GPU/driver/CUDA
    # 13 combination: the very first CUDA call in a freshly-launched
    # process occasionally returns cudaErrorMemoryAllocation even with
    # plenty of free memory (confirmed via a direct minimal CUDA test and
    # by immediately retrying the exact same LISFLOOD-FP run, which then
    # succeeds) -- a cold-start CUDA context/driver quirk, not a real
    # out-of-memory condition or a bug in this app's own code. Retried up
    # to 2 more times, only for this specific recognizable error, same
    # reliability principle already applied to the equally-transient
    # Overpass 504s elsewhere in this app (see CLAUDE.md).
    for attempt in range(2):
        if proc.returncode == 0 or "cudaErrorMemoryAllocation" not in (proc.stdout + proc.stderr):
            break
        proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True)
    return proc, time.time() - t0


def parse_mass_file(path):
    rows = []
    with open(path) as f:
        header = f.readline().split()
        for line in f:
            parts = line.split()
            if len(parts) < 11:
                continue
            rows.append([float(x) for x in parts])
    return header, np.array(rows)


def compute_max_hazard_from_timesteps(results_dir, nodata):
    """Real DEFRA hazard rating (depth * (speed + 1.5)), peak over the whole
    run, computed from the solver's own real per-timestep depth/velocity
    rasters (res-*.wd/.Vx/.Vy) -- used when the solver itself doesn't write
    res.maxHaz directly.

    This is genuinely needed, not a defensive fallback for a case that
    never happens: LISFLOOD-FP's own CUDA/GPU ACC solver (see CLAUDE.md)
    never implements hazard-raster output at all -- confirmed by reading
    the actual source (`maxHaz`/hazard-writing logic exists only in the
    legacy CPU output.cpp, nothing in cuda/) -- so switching this app to
    the GPU solver for real speed would otherwise silently lose hazard
    classification (used throughout Tab 4/road risk assessment) with no
    error, just an empty/"none" result.

    Vx/Vy are on LISFLOOD-FP's staggered (Arakawa C) grid, not the same
    cell-centered grid as depth -- Vx has one extra column (vertical cell
    faces), Vy has one extra row (horizontal faces). Averaged back onto
    the depth grid's cell centers before computing speed, the same
    de-staggering already proven correct in fis_app.py's
    _read_water_stack (confirmed there via the real file headers)."""
    wd_files = sorted(glob.glob(os.path.join(results_dir, "res-*.wd")))
    vx_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vx")))
    vy_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vy")))
    if not wd_files or len(vx_files) != len(wd_files) or len(vy_files) != len(wd_files):
        return None

    max_haz = None
    valid_mask = None
    for wd_fp, vx_fp, vy_fp in zip(wd_files, vx_files, vy_files):
        _, depth = read_ascii(wd_fp)
        _, vx_face = read_ascii(vx_fp)
        _, vy_face = read_ascii(vy_fp)
        if valid_mask is None:
            valid_mask = depth != nodata
            max_haz = np.zeros_like(depth)
        depth_c = np.where(depth == nodata, 0.0, depth)
        vx_face = np.where(vx_face == nodata, 0.0, vx_face)
        vy_face = np.where(vy_face == nodata, 0.0, vy_face)
        if vx_face.shape[1] != depth_c.shape[1] + 1 or vy_face.shape[0] != depth_c.shape[0] + 1:
            return None  # unexpected grid shape -- don't guess, just skip the fallback
        vx_c = 0.5 * (vx_face[:, :-1] + vx_face[:, 1:])
        vy_c = 0.5 * (vy_face[:-1, :] + vy_face[1:, :])
        speed = np.sqrt(vx_c ** 2 + vy_c ** 2)
        haz = depth_c * (speed + 1.5)
        max_haz = np.maximum(max_haz, haz)

    max_haz[~valid_mask] = nodata
    return max_haz


def classify_hazard(haz_values):
    """Bucket a 1D array of DEFRA hazard-rating values into the 4 bands."""
    out = {}
    lo = 0.0
    for name, hi, _color in HAZARD_BANDS:
        out[name] = float(100.0 * (((haz_values >= lo) & (haz_values < hi)).sum())
                           / max(haz_values.size, 1))
        lo = hi
    return out


def summarize(max_depth, max_haz, inittm, nodata, cellsize, dry_thresh):
    valid = max_depth != nodata
    flooded = valid & (max_depth > dry_thresh)
    cell_area = cellsize * cellsize
    depths = max_depth[flooded]
    haz = max_haz[flooded] if max_haz is not None else np.array([])
    arrivals = inittm[flooded] if inittm is not None else np.array([])

    hazard_pct = classify_hazard(haz) if haz.size else {n: 0.0 for n, _, _ in HAZARD_BANDS}
    peak_band = "none"
    for name, _, _ in reversed(HAZARD_BANDS):
        if hazard_pct.get(name, 0.0) > 0.5:
            peak_band = name
            break

    return {
        "domain_area_km2": float(valid.sum() * cell_area / 1e6),
        "flooded_area_km2": float(flooded.sum() * cell_area / 1e6),
        "flooded_pct_of_domain": float(100.0 * flooded.sum() / max(valid.sum(), 1)),
        "max_depth_m": float(depths.max()) if depths.size else 0.0,
        "mean_depth_m_in_flooded_area": float(depths.mean()) if depths.size else 0.0,
        "median_depth_m_in_flooded_area": float(np.median(depths)) if depths.size else 0.0,
        "flooded_cells": int(flooded.sum()),
        "hazard_breakdown_pct": hazard_pct,
        "peak_hazard_band": peak_band,
        "median_arrival_hr": float(np.median(arrivals)) if arrivals.size else None,
        "fastest_arrival_hr": float(arrivals.min()) if arrivals.size else None,
    }


# --------------------------------------------------------------------------
# Color ramps (validated dataviz-skill blue sequential ramp + a matching
# orange ramp built the same way, plus the fixed DEFRA/status palette)
# --------------------------------------------------------------------------
BLUE_RAMP = ["#f3f9ff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
             "#256abf", "#184f95", "#0d366b"]


def _mix(hex_color, white_frac):
    r = int(hex_color[1:3], 16); g = int(hex_color[3:5], 16); b = int(hex_color[5:7], 16)
    r = int(r + (255 - r) * white_frac); g = int(g + (255 - g) * white_frac); b = int(b + (255 - b) * white_frac)
    return f"#{r:02x}{g:02x}{b:02x}"


ORANGE_DEEP = "#c8501f"
ORANGE_RAMP = [_mix(ORANGE_DEEP, f) for f in (0.94, 0.78, 0.60, 0.42, 0.24, 0.10, 0.0)]

STATUS_COLORS = {name: color for name, _, color in HAZARD_BANDS}
ACCENT_LIGHT = "#0E7A8C"
ACCENT_DARK = "#5FD3E8"


# --------------------------------------------------------------------------
# Map plates (matplotlib, embedded as base64 PNG)
# --------------------------------------------------------------------------
def _fig_to_b64(fig, dpi=150):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_maps(dem, max_depth, max_haz, inittm, nodata, cellsize, dry_thresh, workdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, ListedColormap, BoundaryNorm, LightSource

    valid = max_depth != nodata
    flooded_mask = valid & (max_depth > dry_thresh)

    ls = LightSource(azdeg=315, altdeg=45)
    hillshade = ls.hillshade(dem, vert_exag=3, dx=cellsize, dy=cellsize)

    def base_terrain(ax):
        ax.imshow(hillshade, cmap="gray", origin="upper", vmin=0.2, vmax=1.0)
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    images = {}

    # 1. Depth map -----------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    base_terrain(ax)
    cmap = LinearSegmentedColormap.from_list("depth", BLUE_RAMP)
    depth_masked = np.ma.masked_where(~flooded_mask, max_depth)
    vmax = max(0.2, float(np.nanpercentile(max_depth[flooded_mask], 98)) if flooded_mask.any() else 0.2)
    im = ax.imshow(depth_masked, cmap=cmap, origin="upper", vmin=0, vmax=vmax, alpha=0.92)
    cb = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.02)
    cb.set_label("Max water depth (m)", fontsize=9)
    cb.ax.tick_params(labelsize=8)
    fig.tight_layout()
    images["depth"] = _fig_to_b64(fig)
    plt.close(fig)

    # 2. Hazard map (DEFRA bands) ---------------------------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    base_terrain(ax)
    if max_haz is not None:
        bounds = [0.0] + [b for _, b, _ in HAZARD_BANDS[:-1]] + [max(2.5, float(np.nanmax(max_haz)) + 0.01)]
        colors = [c for _, _, c in HAZARD_BANDS]
        haz_cmap = ListedColormap(colors)
        norm = BoundaryNorm(bounds, haz_cmap.N)
        haz_masked = np.ma.masked_where(~flooded_mask, max_haz)
        ax.imshow(haz_masked, cmap=haz_cmap, norm=norm, origin="upper", alpha=0.92)
    fig.tight_layout()
    images["hazard"] = _fig_to_b64(fig)
    plt.close(fig)

    # 3. Time-of-arrival map --------------------------------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    base_terrain(ax)
    if inittm is not None:
        cmap2 = LinearSegmentedColormap.from_list("arrival", ORANGE_RAMP)
        arr_masked = np.ma.masked_where(~flooded_mask, inittm)
        vmax2 = float(np.nanpercentile(inittm[flooded_mask], 98)) if flooded_mask.any() else 1.0
        im2 = ax.imshow(arr_masked, cmap=cmap2, origin="upper", vmin=0, vmax=max(vmax2, 0.1), alpha=0.92)
        cb2 = fig.colorbar(im2, ax=ax, shrink=0.75, pad=0.02)
        cb2.set_label("Time of first inundation (hr)", fontsize=9)
        cb2.ax.tick_params(labelsize=8)
    fig.tight_layout()
    images["arrival"] = _fig_to_b64(fig)
    plt.close(fig)

    return images


# --------------------------------------------------------------------------
# Inline SVG "small multiple" line/area charts with a JS hover crosshair
# --------------------------------------------------------------------------
def _svg_chart_block(chart_id, title, unit, t, y, color, width=460, height=200, y_decimals=1):
    ml, mr, mt, mb = 44, 14, 14, 26
    x0, x1 = t.min(), t.max()
    y0, y1 = 0.0, max(float(y.max()) * 1.15, 1e-6)

    def sx(v):
        return ml + (v - x0) / max(x1 - x0, 1e-9) * (width - ml - mr)

    def sy(v):
        return (height - mb) - (v - y0) / max(y1 - y0, 1e-9) * (height - mt - mb)

    pts = [(sx(tv), sy(yv)) for tv, yv in zip(t, y)]
    line_path = "M " + " L ".join(f"{x:.2f},{yv:.2f}" for x, yv in pts)
    area_path = line_path + f" L {pts[-1][0]:.2f},{height-mb} L {pts[0][0]:.2f},{height-mb} Z"

    grid = []
    for frac in (0.0, 0.5, 1.0):
        yv = y0 + frac * (y1 - y0)
        yy = sy(yv)
        grid.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{width-mr}" y2="{yy:.1f}" class="gridline"/>')
        grid.append(f'<text x="{ml-6}" y="{yy+3:.1f}" class="axis-label" text-anchor="end">{yv:.{y_decimals}f}</text>')

    xt = []
    for frac in (0.0, 0.5, 1.0):
        tv = x0 + frac * (x1 - x0)
        xx = sx(tv)
        xt.append(f'<text x="{xx:.1f}" y="{height-6}" class="axis-label" text-anchor="middle">{tv:.0f}m</text>')

    last = pts[-1]
    points_json = json.dumps([[round(float(a), 3), round(float(b), 4)] for a, b in zip(t, y)])

    return f"""
  <div class="chart-card">
    <div class="chart-head"><span class="chart-title">{title}</span><span class="chart-unit">{unit}</span></div>
    <div class="chart-svg-wrap">
      <svg class="spark-chart" id="{chart_id}" viewBox="0 0 {width} {height}" data-x0="{x0}" data-x1="{x1}"
           data-y0="{y0}" data-y1="{y1}" data-ml="{ml}" data-mr="{mr}" data-mt="{mt}" data-mb="{mb}"
           data-w="{width}" data-h="{height}" data-yd="{y_decimals}" data-unit="{unit}"
           preserveAspectRatio="none">
        {''.join(grid)}
        <path d="{area_path}" class="area-fill" style="fill:{color}"/>
        <path d="{line_path}" class="line-path" style="stroke:{color}"/>
        <circle cx="{last[0]:.2f}" cy="{last[1]:.2f}" r="3.2" class="end-dot" style="fill:{color}"/>
        {''.join(xt)}
        <g class="crosshair" style="display:none">
          <line class="crosshair-line" x1="0" y1="{mt}" x2="0" y2="{height-mb}"/>
          <circle class="crosshair-dot" r="3.6" style="fill:{color}"/>
        </g>
        <rect class="hover-capture" x="{ml}" y="{mt}" width="{width-ml-mr}" height="{height-mt-mb}"
              fill="transparent" data-points='{points_json}'/>
      </svg>
      <div class="chart-tooltip" data-for="{chart_id}"></div>
    </div>
  </div>"""


# --------------------------------------------------------------------------
# HTML report
# --------------------------------------------------------------------------
def build_report_html(args, header, summary, mass_rows, rain_pts, sim_hours,
                       map_images, run_meta, par_text):
    t = mass_rows[:, 0] / 60.0
    area_km2 = mass_rows[:, 4] / 1e6
    vol = mass_rows[:, 5]
    qout = mass_rows[:, 8]
    rain_t = np.array([p[1] * 60.0 for p in rain_pts])
    rain_v = np.array([p[0] for p in rain_pts])

    charts_html = "".join([
        _svg_chart_block("c-rain", "Rainfall intensity", "mm / hr", rain_t, rain_v, ACCENT_LIGHT, y_decimals=0),
        _svg_chart_block("c-area", "Inundated area", "km²", t, area_km2, ACCENT_LIGHT, y_decimals=3),
        _svg_chart_block("c-vol", "Water stored in domain", "m³", t, vol, ACCENT_LIGHT, y_decimals=0),
        _svg_chart_block("c-qout", "Outflow at domain boundary", "m³ / s", t, qout, ACCENT_LIGHT, y_decimals=2),
    ])

    def tile(label, value, sub=""):
        return f"""<div class="tile"><div class="tile-label">{label}</div>
        <div class="tile-value">{value}</div><div class="tile-sub">{sub}</div></div>"""

    peak_band = summary["peak_hazard_band"]
    peak_color = STATUS_COLORS.get(peak_band, "#7C929B")
    arrival_txt = f'{summary["fastest_arrival_hr"]:.2f} hr' if summary["fastest_arrival_hr"] is not None else "—"

    stat_tiles = "".join([
        tile("Flooded area", f'{summary["flooded_area_km2"]:.3f} km²',
             f'{summary["flooded_pct_of_domain"]:.1f}% of domain'),
        tile("Max depth", f'{summary["max_depth_m"]:.2f} m',
             f'mean {summary["mean_depth_m_in_flooded_area"]:.2f} m in flooded area'),
        tile("Peak hazard band", peak_band.title(),
             "DEFRA (2006) rating"),
        tile("Fastest water arrival", arrival_txt, "time to first inundation"),
        tile("Mass-balance error", f'{summary["final_mass_error_m3"]:.1e} m³', "should be ≈ 0"),
        tile("Wall-clock runtime", f'{summary["wall_clock_seconds"]:.1f} s',
             f'{sim_hours:.1f} h simulated'),
    ])

    hazard_legend = "".join(
        f'<div class="legend-chip"><span class="swatch" style="background:{c}"></span>{n.title()} '
        f'<span class="legend-pct">{summary["hazard_breakdown_pct"].get(n,0):.0f}%</span></div>'
        for n, _, c in HAZARD_BANDS
    )

    par_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in run_meta.items())

    return f"""<!doctype html>
<title>{MODEL_NAME}</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --page: #F3F6F8;
  --surface: #FFFFFF;
  --surface-2: #E9F1F3;
  --ink: #142430;
  --ink-2: #46606B;
  --muted: #7C929B;
  --line: #D7E2E5;
  --accent: {ACCENT_LIGHT};
  --accent-soft: #E3F2F4;
  --paper: #FCFDFD;
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --page: #0B1417;
    --surface: #101B1F;
    --surface-2: #142327;
    --ink: #E7EEF0;
    --ink-2: #A9BEC4;
    --muted: #6E848B;
    --line: #22343A;
    --accent: {ACCENT_DARK};
    --accent-soft: #10333A;
    --paper: #F4F6F5;
    color-scheme: dark;
  }}
}}
:root[data-theme="dark"] {{
  --page: #0B1417;
  --surface: #101B1F;
  --surface-2: #142327;
  --ink: #E7EEF0;
  --ink-2: #A9BEC4;
  --muted: #6E848B;
  --line: #22343A;
  --accent: {ACCENT_DARK};
  --accent-soft: #10333A;
  --paper: #F4F6F5;
  color-scheme: dark;
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--page);
  color: var(--ink);
  font-family: "Public Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 64px; }}

.masthead {{
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 20px; flex-wrap: wrap;
  border-bottom: 2px solid var(--ink);
  padding-bottom: 14px; margin-bottom: 22px;
}}
.brand {{ display: flex; align-items: baseline; gap: 10px; }}
.brand-mark {{
  font-family: "Barlow Condensed", sans-serif; font-weight: 700;
  font-size: 34px; letter-spacing: 0.01em; text-transform: uppercase;
  color: var(--ink);
}}
.brand-tag {{ color: var(--muted); font-size: 13px; max-width: 30ch; }}
.run-meta {{
  font-family: "IBM Plex Mono", monospace; font-size: 12px; color: var(--ink-2);
  text-align: right; line-height: 1.7;
}}
.run-meta b {{ color: var(--ink); }}

h2.section-title {{
  font-family: "Barlow Condensed", sans-serif; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em; font-size: 15px;
  color: var(--ink-2); margin: 40px 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
}}

.tiles {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 1px; background: var(--line); border: 1px solid var(--line);
  border-radius: 10px; overflow: hidden;
}}
.tile {{ background: var(--surface); padding: 16px 18px; }}
.tile-label {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }}
.tile-value {{
  font-family: "IBM Plex Mono", monospace; font-size: 24px; font-weight: 500;
  color: var(--ink); margin-top: 4px; font-variant-numeric: tabular-nums;
}}
.tile-sub {{ font-size: 11.5px; color: var(--muted); margin-top: 3px; }}

.maps-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 18px;
}}
.plate {{
  background: var(--paper); border: 1px solid var(--line); border-radius: 10px;
  overflow: hidden;
}}
.plate img {{ display: block; width: 100%; height: auto; }}
.plate-cap {{
  padding: 10px 14px; font-size: 12.5px; color: #46606B; background: var(--paper);
  border-top: 1px solid #e3e9ea;
}}
.plate-cap b {{ color: #142430; }}

.legend-row {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 12px 0 4px; }}
.legend-chip {{
  display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px;
  padding: 4px 10px 4px 8px;
}}
.legend-chip .swatch {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}
.legend-pct {{ font-family: "IBM Plex Mono", monospace; color: var(--ink); margin-left: 2px; }}

.charts-grid {{
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px;
}}
@media (max-width: 640px) {{ .charts-grid {{ grid-template-columns: 1fr; }} }}
.chart-card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px 8px;
}}
.chart-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }}
.chart-title {{ font-size: 13px; font-weight: 500; color: var(--ink); }}
.chart-unit {{ font-size: 11px; color: var(--muted); font-family: "IBM Plex Mono", monospace; }}
.chart-svg-wrap {{ position: relative; }}
.spark-chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
.gridline {{ stroke: var(--line); stroke-width: 1; }}
.axis-label {{ font-size: 9px; fill: var(--muted); font-family: "IBM Plex Mono", monospace; }}
.area-fill {{ opacity: 0.14; }}
.line-path {{ fill: none; stroke-width: 2; }}
.end-dot {{ stroke: var(--surface); stroke-width: 1.5; }}
.crosshair-line {{ stroke: var(--muted); stroke-width: 1; stroke-dasharray: 2 2; }}
.crosshair-dot {{ stroke: var(--surface); stroke-width: 1.5; }}
.hover-capture {{ cursor: crosshair; }}
.chart-tooltip {{
  position: absolute; pointer-events: none; opacity: 0; transition: opacity 0.08s;
  background: var(--ink); color: var(--paper); font-family: "IBM Plex Mono", monospace;
  font-size: 11px; padding: 4px 7px; border-radius: 5px; white-space: nowrap;
  transform: translate(-50%, -130%); z-index: 5;
}}

table.appendix {{ width: 100%; border-collapse: collapse; font-family: "IBM Plex Mono", monospace; font-size: 12.5px; }}
table.appendix td {{ padding: 5px 10px; border-bottom: 1px solid var(--line); color: var(--ink-2); }}
table.appendix td:first-child {{ color: var(--muted); width: 40%; }}
table.appendix td:last-child {{ color: var(--ink); }}

footer {{ margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--line);
  font-size: 12px; color: var(--muted); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }}
footer a {{ color: var(--accent); }}
</style>

<div class="wrap">
  <div class="masthead">
    <div class="brand">
      <span class="brand-mark">{MODEL_NAME}</span>
      <span class="brand-tag">{MODEL_TAGLINE}</span>
    </div>
    <div class="run-meta">
      <div><b>Solver</b> LISFLOOD-FP v5.9 &middot; inertial (acceleration)</div>
      <div><b>Domain</b> {int(header['ncols'])}&times;{int(header['nrows'])} cells @ {header['cellsize']:.0f} m</div>
      <div><b>Storm</b> {args.rain_mm_hr:.0f} mm/hr peak, {args.duration_hr:.1f} h duration</div>
    </div>
  </div>

  <h2 class="section-title">Summary</h2>
  <div class="tiles">{stat_tiles}</div>

  <h2 class="section-title">Flood extent &amp; hazard maps</h2>
  <div class="maps-grid">
    <div class="plate">
      <img src="data:image/png;base64,{map_images['depth']}" alt="Maximum flood depth map">
      <div class="plate-cap"><b>Maximum depth.</b> Blue intensity = peak simulated water depth per cell over the full run.</div>
    </div>
    <div class="plate">
      <img src="data:image/png;base64,{map_images['hazard']}" alt="DEFRA hazard classification map">
      <div class="plate-cap"><b>Hazard rating (DEFRA 2006).</b> HR = depth &times; (velocity + 1.5), classified into 4 bands.</div>
    </div>
    <div class="plate">
      <img src="data:image/png;base64,{map_images['arrival']}" alt="Time of arrival map">
      <div class="plate-cap"><b>Time of arrival.</b> Hours from simulation start until each cell first floods.</div>
    </div>
  </div>
  <div class="legend-row">{hazard_legend}</div>

  <h2 class="section-title">Storm &amp; flood evolution</h2>
  <div class="charts-grid">{charts_html}</div>

  <h2 class="section-title">Run configuration</h2>
  <table class="appendix">{par_rows}</table>

  <footer>
    <span>{MODEL_NAME} &middot; built on LISFLOOD-FP (Bates &amp; De Roo, 2000; Bates, Horritt &amp; Fewtrell, 2010)</span>
    <span>Generated {run_meta.get('Generated at', '')}</span>
  </footer>
</div>

<script>
(function() {{
  document.querySelectorAll('.spark-chart').forEach(function(svg) {{
    var rect = svg.querySelector('.hover-capture');
    var cross = svg.querySelector('.crosshair');
    var line = svg.querySelector('.crosshair-line');
    var dot = svg.querySelector('.crosshair-dot');
    var tip = document.querySelector('.chart-tooltip[data-for="' + svg.id + '"]');
    var pts = JSON.parse(rect.getAttribute('data-points'));
    var x0 = parseFloat(svg.dataset.x0), x1 = parseFloat(svg.dataset.x1);
    var y0 = parseFloat(svg.dataset.y0), y1 = parseFloat(svg.dataset.y1);
    var ml = parseFloat(svg.dataset.ml), mr = parseFloat(svg.dataset.mr);
    var mt = parseFloat(svg.dataset.mt), mb = parseFloat(svg.dataset.mb);
    var w = parseFloat(svg.dataset.w), h = parseFloat(svg.dataset.h);
    var yd = parseInt(svg.dataset.yd), unit = svg.dataset.unit;
    function sx(v) {{ return ml + (v - x0) / Math.max(x1 - x0, 1e-9) * (w - ml - mr); }}
    function sy(v) {{ return (h - mb) - (v - y0) / Math.max(y1 - y0, 1e-9) * (h - mt - mb); }}
    function move(evt) {{
      var box = svg.getBoundingClientRect();
      var px = (evt.clientX - box.left) / box.width * w;
      var frac = Math.min(1, Math.max(0, (px - ml) / (w - ml - mr)));
      var tv = x0 + frac * (x1 - x0);
      var idx = 0, best = Infinity;
      for (var i = 0; i < pts.length; i++) {{
        var d = Math.abs(pts[i][0] - tv);
        if (d < best) {{ best = d; idx = i; }}
      }}
      var p = pts[idx];
      var xx = sx(p[0]), yy = sy(p[1]);
      cross.style.display = 'block';
      line.setAttribute('x1', xx); line.setAttribute('x2', xx);
      dot.setAttribute('cx', xx); dot.setAttribute('cy', yy);
      var svgBox = svg.getBoundingClientRect();
      tip.style.left = (svgBox.left - svg.closest('.chart-svg-wrap').getBoundingClientRect().left + xx / w * svgBox.width) + 'px';
      tip.style.top = (yy / h * svgBox.height) + 'px';
      tip.textContent = p[0].toFixed(0) + ' min — ' + p[1].toFixed(yd) + ' ' + unit;
      tip.style.opacity = 1;
    }}
    function leave() {{ cross.style.display = 'none'; tip.style.opacity = 0; }}
    rect.addEventListener('mousemove', move);
    rect.addEventListener('mouseleave', leave);
    rect.addEventListener('touchmove', function(e) {{ move(e.touches[0]); }}, {{passive: true}});
  }});
}})();
</script>
"""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description=f"{MODEL_NAME} — {MODEL_TAGLINE}")
    p.add_argument("--dem", help="Existing ARC/INFO ASCII DEM file to use instead of synthetic terrain")
    p.add_argument("--width", type=float, default=800.0, help="Synthetic domain width in metres")
    p.add_argument("--height", type=float, default=600.0, help="Synthetic domain height in metres")
    p.add_argument("--cellsize", type=float, default=10.0, help="Grid resolution in metres")
    p.add_argument("--slope", type=float, default=1.0, help="Synthetic terrain slope in percent")
    p.add_argument("--rain-mm-hr", type=float, required=True, help="Peak rainfall intensity, mm/hr")
    p.add_argument("--duration-hr", type=float, required=True, help="Storm duration, hours")
    p.add_argument("--sim-hours", type=float, default=None, help="Total sim length incl. drain-down (default duration+2h)")
    p.add_argument("--friction", type=float, default=0.05,
                    help="Manning's n floodplain friction -- the uniform value used everywhere if "
                         "--manning-file isn't given, or the NODATA/fallback value for any cell "
                         "--manning-file doesn't cover")
    p.add_argument("--manning-file", default=None,
                    help="Optional ARC ASCII raster (same grid as --dem) of real per-cell Manning's n "
                         "-- e.g. damage_utils.lulc_to_manning_grid from real ESA WorldCover land cover. "
                         "Lets floodplain roughness vary by real land cover instead of one uniform guess.")
    p.add_argument("--cfl", type=float, default=0.7, help="CFL number for the inertial solver")
    p.add_argument("--outlet-edges", default="N,E,S,W", help="Comma list of domain edges water may drain from")
    p.add_argument("--saveint-min", type=float, default=10.0, help="Grid save interval, minutes")
    p.add_argument("--workdir", default=os.path.expanduser("~/flood/run_" + time.strftime("%Y%m%d_%H%M%S")))
    p.add_argument("--dry-thresh", type=float, default=0.05, help="Depth (m) above which a cell counts as 'flooded'")
    p.add_argument("--site-name", default="Synthetic test catchment")
    p.add_argument("--infiltration-mm-hr", type=float, default=0.0,
                    help="Uniform ground infiltration/loss rate, mm/hr (0 = fully impervious, all rain becomes runoff) "
                         "-- treated as the AMC-II (average) baseline; scaled by --amc")
    p.add_argument("--amc", type=float, default=50.0,
                    help="Antecedent Moisture Condition, continuous 0-100 (0=dry/AMC-I, 50=average/AMC-II, "
                         "100=wet-saturated/AMC-III) -- scales --infiltration-mm-hr (see amc_infiltration_multiplier)")
    p.add_argument("--inflow-m3s", type=float, default=0.0,
                    help="Additional river/channel discharge entering the domain, m^3/s -- the PEAK of a "
                         "real rising/receding hydrograph if --inflow-duration-hr > 0, else a constant "
                         "inflow for the whole run (0 = pluvial-only)")
    p.add_argument("--inflow-edge", default="N", choices=["N", "E", "S", "W"],
                    help="Which domain edge the --inflow-m3s discharge enters from (only used if --inflow-m3s > 0)")
    p.add_argument("--inflow-duration-hr", type=float, default=0.0,
                    help="Duration (hours) of a real triangular fluvial hydrograph peaking at "
                         "--inflow-m3s halfway through -- same design-storm shape as --duration-hr's "
                         "rainfall hyetograph, applied to river discharge instead. 0 (default) keeps "
                         "the original constant (QFIX) inflow for the whole run instead.")
    p.add_argument("--inflow-pos-m", type=float, default=None,
                    help="Position of the real river/channel crossing along --inflow-edge, in DEM-local "
                         "metres from that edge's own origin corner (0 to the edge's full span). When "
                         "given, the discharge is concentrated into a --inflow-width-m-wide segment "
                         "centered here instead of smeared across the whole domain edge -- lets the "
                         "inflow actually build up to a realistic channel depth and overtop, rather than "
                         "entering as a flux too thin (diluted over a whole multi-km edge) to ever pond "
                         "and spread. Omit to keep the original whole-edge behaviour.")
    p.add_argument("--inflow-width-m", type=float, default=30.0,
                    help="Width (metres) of the concentrated inflow segment when --inflow-pos-m is given "
                         "-- a documented typical channel-width estimate, not a measurement (see the "
                         "caller for the per-waterway-type default).")
    args = p.parse_args()

    # A real fluvial event (river rise/recession) can genuinely outlast the
    # rain event driving sim_hours's own default -- extend the simulated
    # window to cover whichever runs longer, so a real compound flood
    # (river + storm) doesn't get cut off mid-recession, and so the
    # inflow hydrograph's own times never exceed the simulation length
    # (which the .bdy time-series parser requires to stay strictly
    # increasing).
    longest_driver_hr = max(args.duration_hr, args.inflow_duration_hr)
    sim_hours = args.sim_hours or (longest_driver_hr + 2.0)
    os.makedirs(args.workdir, exist_ok=True)
    # Hard floor on Manning's friction -- a value near/at 0 is not a real
    # floodplain (LISFLOOD-FP's own manual: low friction can destabilize
    # the ACC solver even with theta's numerical diffusion), and the UI
    # previously let a user dial this all the way down with no guard rail
    # at all. write_par_file's theta mitigation (above) handles the "low
    # but plausible" range below MIN_FPFRIC; this floor catches the
    # "someone typed 0" case that theta alone isn't meant to rescue.
    ABS_MIN_FPFRIC = 0.01
    if args.friction < ABS_MIN_FPFRIC:
        print(f"[{MODEL_NAME}] WARNING: friction n={args.friction} is below the safe floor "
              f"({ABS_MIN_FPFRIC}) -- LISFLOOD-FP's ACC solver can become unstable at very low "
              f"friction; clamping to {ABS_MIN_FPFRIC}.")
        args.friction = ABS_MIN_FPFRIC
    # A per-cell manning_file can carry its own real minimum below the
    # uniform fpfric floor above (e.g. a real "water"/"bare ground" class) --
    # this app's own generator (damage_utils.lulc_to_manning_grid) already
    # clamps to this same floor, but a manning_file from any other source
    # might not, so re-clamp defensively here too and base the theta
    # decision on the grid's real (post-clamp) minimum, not just fpfric.
    min_fric_seen = args.friction
    if args.manning_file:
        mf_header, mf_grid = read_ascii(args.manning_file)
        mf_nodata = mf_header.get("nodata_value", -9999)
        mf_valid = mf_grid != mf_nodata
        if mf_valid.any():
            real_min = float(mf_grid[mf_valid].min())
            if real_min < ABS_MIN_FPFRIC:
                print(f"[{MODEL_NAME}] WARNING: manning_file's minimum ({real_min:.4f}) is below the "
                      f"safe floor ({ABS_MIN_FPFRIC}); clamping those cells.")
                mf_grid[mf_valid & (mf_grid < ABS_MIN_FPFRIC)] = ABS_MIN_FPFRIC
                write_ascii(args.manning_file, mf_header, mf_grid, nodata=mf_nodata)
            min_fric_seen = min(min_fric_seen, max(real_min, ABS_MIN_FPFRIC))
    outlet_edges = set(e.strip().upper() for e in args.outlet_edges.split(","))
    amc_mult = amc_infiltration_multiplier(args.amc)
    effective_infiltration_mm_hr = args.infiltration_mm_hr * amc_mult
    infiltration_m_s = effective_infiltration_mm_hr / (1000.0 * 3600.0)

    if args.dem:
        header, dem = read_ascii(args.dem)
        dem_name = os.path.basename(args.dem)
        if os.path.abspath(args.dem) != os.path.abspath(os.path.join(args.workdir, dem_name)):
            write_ascii(os.path.join(args.workdir, dem_name), header, dem)
    else:
        header, dem = make_synthetic_dem(args.width, args.height, args.cellsize, args.slope)
        dem_name = "site.dem.ascii"
        write_ascii(os.path.join(args.workdir, dem_name), header, dem)
        print(f"[{MODEL_NAME}] Generated synthetic terrain: {header['ncols']}x{header['nrows']} "
              f"cells @ {header['cellsize']} m, slope {args.slope}%")

    manning_name = None
    if args.manning_file:
        # LISFLOOD-FP runs with workdir as its cwd (run_lisflood), so every
        # filename it's given must be relative to there -- same treatment
        # already applied to --dem above.
        manning_name = os.path.basename(args.manning_file)
        if os.path.abspath(args.manning_file) != os.path.abspath(os.path.join(args.workdir, manning_name)):
            shutil.copy(args.manning_file, os.path.join(args.workdir, manning_name))

    rain_name = "storm.rain"
    rain_pts = write_rain_file(os.path.join(args.workdir, rain_name), args.rain_mm_hr,
                                args.duration_hr, sim_hours)

    inflow_edge = args.inflow_edge if args.inflow_m3s > 0 else None
    inflow_bdy_name, bdy_name = None, None
    inflow_start_m, inflow_end_m = None, None
    ncols, nrows, cs = header["ncols"], header["nrows"], header["cellsize"]
    edge_span_m = (ncols * cs) if inflow_edge in ("N", "S") else (nrows * cs)
    if inflow_edge and args.inflow_pos_m is not None:
        half_w = max(args.inflow_width_m, cs) / 2.0
        # Clamp to the edge's own extent HERE (mirroring write_bci_file's
        # own clamp below) before computing span_m -- a crossing detected
        # near the edge's own corner (within half_w of it) previously kept
        # span_m at the full un-clamped width (e.g. 40m) while write_bci_file
        # silently applied a NARROWER actual segment (e.g. 30m, the rest cut
        # off by the corner). write_bdy_file then baked a per-unit-width
        # flux sized for the wider, un-clamped width into the .bdy file, so
        # actual delivered discharge = that flux * the narrower REAL segment
        # = a real, silent under-delivery (e.g. 75% of the requested peak)
        # with the summary's analytically-computed cumulative inflow still
        # reporting the full requested number. Clamping before computing
        # span_m makes span_m equal the segment write_bci_file will actually
        # apply, so the .bdy flux and the real delivered width always agree.
        inflow_start_m = max(0.0, min(edge_span_m, args.inflow_pos_m - half_w))
        inflow_end_m = max(0.0, min(edge_span_m, args.inflow_pos_m + half_w))
        span_m = max(inflow_end_m - inflow_start_m, 1e-6)
    else:
        span_m = edge_span_m
    if inflow_edge and args.inflow_duration_hr > 0:
        inflow_bdy_name = "river1"
        bdy_name = "river.bdy"
        write_bdy_file(os.path.join(args.workdir, bdy_name), inflow_bdy_name,
                        args.inflow_m3s, args.inflow_duration_hr, sim_hours, span_m)
    bci_name = "site.bci"
    write_bci_file(os.path.join(args.workdir, bci_name), header, outlet_edges,
                    inflow_edge=inflow_edge, inflow_m3s=args.inflow_m3s,
                    inflow_bdy_name=inflow_bdy_name,
                    inflow_start_m=inflow_start_m, inflow_end_m=inflow_end_m)

    # A boundary-injected discharge (QFIX/QVAR) concentrates a real flux at
    # a handful of edge cells from the very first instant -- a much sharper
    # local gradient than rainfall (which arrives gradually, spread evenly
    # over the whole domain). Found the hard way: with the .bci coordinate
    # fix above actually letting inflow apply for the first time, the
    # default initial_tstep=5.0 (fine for every rain-only run this app has
    # shipped) let a real 20-6700 m^3/s edge inflow blow up to thousands of
    # metres of depth on a real cropped DEM within a few real timesteps --
    # not a sign of the fix being wrong (a pure-pluvial run through the
    # identical, newly-fixed .bci code stayed correctly stable, ~5m max
    # depth, confirming the coordinate fix itself doesn't break anything),
    # but a genuine numerical-stability gap this app never needed to worry
    # about before, since boundary inflow silently never applied. Cutting
    # the starting timestep to 0.2s for any run WITH a boundary inflow (rain
    # -only runs keep the original 5.0s, unaffected) resolved it in direct
    # testing on the same real DEM/discharge (2918m -> 9.65m, a physically
    # plausible real river depth) -- a mitigation, not a rigorously derived
    # CFL bound, so watch for renewed instability reports on other real
    # AOIs/discharges and shrink further if one appears.
    inflow_initial_tstep = 0.2 if inflow_edge else 5.0
    par_name = "site.par"
    write_par_file(
        os.path.join(args.workdir, par_name),
        dem_name=dem_name, rain_name=rain_name, bci_name=bci_name,
        resroot="res", dirroot="results",
        sim_time_s=sim_hours * 3600.0,
        saveint_s=args.saveint_min * 60.0,
        massint_s=max(30.0, args.saveint_min * 6.0),
        cfl=args.cfl, fpfric=args.friction, initial_tstep=inflow_initial_tstep,
        infiltration_m_s=infiltration_m_s, bdy_name=bdy_name,
        manning_file=manning_name, min_fric_seen=min_fric_seen,
    )

    print(f"[{MODEL_NAME}] Inputs written to {args.workdir}")
    print(f"[{MODEL_NAME}] Storm: peak {args.rain_mm_hr} mm/hr, duration {args.duration_hr} h, "
          f"sim length {sim_hours} h, friction n={args.friction}, outlets={sorted(outlet_edges)}")
    if args.infiltration_mm_hr > 0:
        print(f"[{MODEL_NAME}] Infiltration: {args.infiltration_mm_hr} mm/hr baseline (AMC-II) "
              f"x {amc_mult:.2f} (AMC {amc_label(args.amc)}) = "
              f"{effective_infiltration_mm_hr:.2f} mm/hr effective uniform loss")
    inflow_loc_desc = (f"concentrated over a {span_m:.1f}m channel-width segment at {args.inflow_pos_m:.1f}m"
                       if inflow_edge and args.inflow_pos_m is not None else
                       "smeared across the whole edge (no detected crossing point)")
    if inflow_edge and inflow_bdy_name:
        print(f"[{MODEL_NAME}] River/channel inflow: real hydrograph peaking at {args.inflow_m3s} m^3/s "
              f"over {args.inflow_duration_hr} h on {inflow_edge} edge, {inflow_loc_desc} (compound fluvial+pluvial)")
    elif inflow_edge:
        print(f"[{MODEL_NAME}] River/channel inflow: {args.inflow_m3s} m^3/s (constant) on {inflow_edge} edge, "
              f"{inflow_loc_desc} (compound fluvial+pluvial)")
    print(f"[{MODEL_NAME}] Running LISFLOOD-FP (acceleration/inertial solver, DEFRA hazard rating)...")

    proc, elapsed = run_lisflood(args.workdir, par_name)
    if proc.returncode != 0:
        print(f"[{MODEL_NAME}] LISFLOOD-FP FAILED")
        print(proc.stdout[-4000:]); print(proc.stderr[-4000:])
        sys.exit(1)

    print(f"[{MODEL_NAME}] Run finished in {elapsed:.1f}s wall-clock")

    results_dir = os.path.join(args.workdir, "results")
    mass_header, mass_rows = parse_mass_file(os.path.join(results_dir, "res.mass"))
    max_header, max_depth = read_ascii(os.path.join(results_dir, "res.max"))
    nodata = max_header.get("nodata_value", -9999)

    max_haz, inittm = None, None
    haz_path = os.path.join(results_dir, "res.maxHaz")
    if os.path.exists(haz_path):
        _, max_haz = read_ascii(haz_path)
    else:
        # The GPU/CUDA ACC solver doesn't write res.maxHaz at all (a real
        # limitation confirmed in LISFLOOD-FP's own source, not a bug in
        # this app) -- self-computed from the same real per-timestep
        # depth/velocity data instead of silently losing hazard
        # classification. See compute_max_hazard_from_timesteps's own
        # docstring.
        max_haz = compute_max_hazard_from_timesteps(results_dir, nodata)
    tm_path = os.path.join(results_dir, "res.inittm")
    if os.path.exists(tm_path):
        _, inittm = read_ascii(tm_path)

    summary = summarize(max_depth, max_haz, inittm, nodata, header["cellsize"], args.dry_thresh)
    summary["final_mass_error_m3"] = float(mass_rows[-1, 10])
    # LISFLOOD-FP's own user manual (section 5.2.1, column 12) documents
    # this column as "Cumulative effect of infiltration, evaporation and
    # rainfall over the simulation in 10^3 m^3" -- i.e. THOUSANDS of cubic
    # metres, not plain m^3 like every other volume column in this file.
    # Reading it as-is (no *1000) would under-report by exactly 1000x
    # whenever this column is ever genuinely nonzero (a CPU-path run, or a
    # future CUDA fix -- it's always 0 under the current GPU/CUDA solver,
    # so this multiplier is inert today, but a real landmine otherwise).
    reported_rain_vol_m3 = float(mass_rows[-1, 11]) * 1000.0
    if reported_rain_vol_m3 == 0.0 and args.rain_mm_hr > 0:
        # The GPU/CUDA ACC solver's own mass-accounting log never
        # populates this column (confirmed: the same run's "Vol" column
        # genuinely grows from real rainfall, so rain IS being applied
        # physically -- this one specific reporting column just isn't
        # wired up under CUDA) -- computed analytically instead, from the
        # exact same triangular hyetograph write_rain_file already
        # encodes (total depth of a triangular hyetograph = half its
        # peak rate times its duration), which is exact, not an estimate.
        domain_area_m2 = summary["domain_area_km2"] * 1e6
        total_rain_depth_m = 0.5 * (args.rain_mm_hr / 1000.0) * args.duration_hr
        reported_rain_vol_m3 = total_rain_depth_m * domain_area_m2
    summary["cumulative_rainfall_input_m3"] = reported_rain_vol_m3
    if inflow_edge:
        # Same real analytical calculation as the rainfall volume above,
        # for the same reason: res.mass's own Qin column also reads 0.0
        # throughout under CUDA (confirmed: the compound run's real
        # flooded area/depth are genuinely larger than an identical
        # pluvial-only run, proving the inflow WAS applied physically --
        # only this one reporting column isn't wired up in the CUDA code
        # path). Exact for the constant-QFIX case (m^3/s * duration);
        # exact for the real QVAR hydrograph case too (triangle area).
        if inflow_bdy_name:
            summary["cumulative_river_inflow_m3"] = 0.5 * args.inflow_m3s * args.inflow_duration_hr * 3600.0
        else:
            summary["cumulative_river_inflow_m3"] = args.inflow_m3s * sim_hours * 3600.0
    else:
        summary["cumulative_river_inflow_m3"] = 0.0
    summary["wall_clock_seconds"] = elapsed
    summary["sim_hours"] = sim_hours
    summary["amc"] = args.amc
    summary["amc_multiplier"] = amc_mult
    summary["infiltration_mm_hr_baseline"] = args.infiltration_mm_hr
    summary["infiltration_mm_hr_effective"] = effective_infiltration_mm_hr

    with open(os.path.join(args.workdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print("\n================ FLOOD SUMMARY ================")
    print(f"Domain area:            {summary['domain_area_km2']:.4f} km^2")
    print(f"Flooded area (max):     {summary['flooded_area_km2']:.4f} km^2 "
          f"({summary['flooded_pct_of_domain']:.1f}% of domain)")
    print(f"Max depth:              {summary['max_depth_m']:.2f} m")
    print(f"Mean depth (flooded):   {summary['mean_depth_m_in_flooded_area']:.2f} m")
    print(f"Peak DEFRA hazard band: {summary['peak_hazard_band']}")
    print(f"Cumulative rainfall in: {summary['cumulative_rainfall_input_m3']:.1f} m^3")
    if summary["cumulative_river_inflow_m3"] > 0:
        print(f"Cumulative river inflow: {summary['cumulative_river_inflow_m3']:.1f} m^3")
    print(f"Mass-balance error:     {summary['final_mass_error_m3']:.3e} m^3 (should be ~0)")
    print(f"Wall-clock time:        {elapsed:.1f} s")

    print(f"[{MODEL_NAME}] Rendering map plates + report...")
    map_images = render_maps(dem, max_depth, max_haz, inittm, nodata, header["cellsize"],
                              args.dry_thresh, args.workdir)

    run_meta = {
        "Site": args.site_name,
        "DEM source": args.dem or "synthetic (generated)",
        "Resolution": f"{header['cellsize']:.1f} m",
        "Domain size": f"{int(header['ncols'])} x {int(header['nrows'])} cells",
        "Solver": "LISFLOOD-FP v5.9, local inertial (acceleration) formulation",
        "CFL number": f"{args.cfl}",
        "Floodplain friction (Manning's n)": (
            f"per-cell, real ESA WorldCover land cover (fallback {args.friction} for any uncovered cell)"
            if manning_name else f"{args.friction} (uniform)"
        ),
        "Rainfall peak": f"{args.rain_mm_hr} mm/hr",
        "Storm duration": f"{args.duration_hr} h",
        "Total simulated time": f"{sim_hours} h",
        "Outlet edges": ", ".join(sorted(outlet_edges)) or "none (closed basin)",
        "Infiltration / ground loss": (
            f"{args.infiltration_mm_hr} mm/hr baseline, AMC {amc_label(args.amc)} "
            f"-> {effective_infiltration_mm_hr:.2f} mm/hr effective"
        ) if args.infiltration_mm_hr > 0 else f"0 (fully impervious, AMC {amc_label(args.amc)} has no effect)",
        "River/channel inflow": (
            f"real hydrograph, peak {args.inflow_m3s} m^3/s over {args.inflow_duration_hr} h on {inflow_edge} edge"
            if inflow_edge and inflow_bdy_name else
            f"{args.inflow_m3s} m^3/s (constant) on {inflow_edge} edge" if inflow_edge else
            "none (pluvial-only)"
        ),
        "'Flooded' depth threshold": f"{args.dry_thresh} m",
        "Generated at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(args.workdir, par_name)) as f:
        par_text = f.read()

    html = build_report_html(args, header, summary, mass_rows, rain_pts, sim_hours,
                              map_images, run_meta, par_text)
    report_path = os.path.join(args.workdir, "report.html")
    with open(report_path, "w") as f:
        f.write(html)

    print(f"\nReport:    {report_path}")
    print(f"Full data: {args.workdir}")
    print("=================================================")


if __name__ == "__main__":
    main()
