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

import storm_utils

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

# A SEPARATE build, with NetCDF enabled (config.sailab.cmake's _NETCDF=1),
# used ONLY when real spatial (storm-centered) rainfall is requested --
# see storm_utils.py. Built specifically so real per-cell/per-timestep
# rainfall (LISFLOOD-FP's own DynamicRain mechanism, rain/rain.tpp,
# confirmed genuinely GPU-consumed at cuda_acc_simulate.cu's updateACC
# kernel call) becomes usable; the original LISFLOOD_BIN above never
# had NetCDF support and stays untouched/unchanged for every OTHER run
# (uniform rain), matching this app's own established "verify before
# trusting, keep the old binary as a fallback" discipline. Verified
# bit-identical to LISFLOOD_BIN on a real non-spatial run before this
# was trusted for anything (same DEM/rain/bci, zero cell-by-cell
# difference in res.max) -- the two binaries are the exact same source,
# differing only in this one compile-time flag.
LISFLOOD_DIR_NETCDF = os.path.expanduser("~/flood/lisflood-fp-8.2/LISFLOOD-FP/build_netcdf")
LISFLOOD_BIN_NETCDF = os.path.join(LISFLOOD_DIR_NETCDF, "lisflood")
# The real conda-forge libnetcdf headers + the already-proven-working
# netCDF4 Python wheel's own bundled .so (all ITS runtime deps already
# satisfied via sibling .so files in the same directory) -- see
# config.sailab.cmake's own comment for why these specific files.
NETCDF_LOCAL_LIB_DIR = os.path.expanduser("~/flood/netcdf_local/lib")
NUMA_STUB_LIB_DIR = os.path.expanduser("~/flood/numa_stub/lib")

MODEL_NAME = "SAILAB"
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
# Solver auto-selection: ACC (local-inertial, Bates et al. 2010) vs DG2
# (full 2nd-order discontinuous-Galerkin shallow water) -- both real,
# GPU-accelerated CUDA solvers in this app's LISFLOOD-FP 8.2 build
# (confirmed directly from source: lisflood.cpp dispatches
# lis::cuda::dg2::Simulation exactly like lis::cuda::acc::Simulation, both
# reading the same rainfall/.bci/manningfile/hazard keywords).
#
# ACC drops the convective acceleration term from the shallow water
# equations (a deliberate simplification, valid where flow is subcritical
# and gently sloped -- Bates, Horritt & Fewtrell 2010) in exchange for a
# much larger stable timestep. On steep terrain, momentum/convective
# effects become significant (supercritical flow, hydraulic jumps) and
# ACC's own approximation degrades -- the documented reason LISFLOOD-FP
# ships DG2 (a real, unsimplified shallow-water solver) as an alternative.
# There's no single universally-agreed slope cutoff for this in the
# literature; DG2_SLOPE_THRESHOLD_PCT below is a documented, sourced
# judgement call (10% mean absolute slope -- the same rough "steep
# terrain" threshold this app already uses elsewhere, see CLAUDE.md's
# adaptive-terrain-exaggeration entry for Gilgit-Baltistan-scale relief),
# not a rigorously validated universal boundary. DG2 is also real-world
# slower (no local-inertial shortcut, plus a stricter CFL condition from
# resolving the full wave equation), so this auto-selection is a genuine
# accuracy/speed tradeoff, not a strict upgrade -- flat floodplain runs
# (this app's overwhelming majority of real Pakistan cases) keep using
# the faster ACC solver.
DG2_SLOPE_THRESHOLD_PCT = 10.0

# TEMPORARY, explicit override (2026-09): now that ACC's own real,
# empirically-calibrated depth bias correction (see
# acc_depth_correction_factor's own docstring -- 7-point real ACC-vs-DG2
# benchmark, R^2=0.992, max 4.9% depth-magnitude error across 15-200%
# slope) closes ACC's real steep-terrain accuracy gap for MAGNITUDE, this
# app's own auto-selection is set to prefer ACC everywhere, including the
# terrain that would otherwise route to DG2 -- at explicit user request,
# specifically to trade DG2's real ~10-60x wall-clock cost for real speed,
# accepting the correction's own stated limitation (it fixes depth
# MAGNITUDE only, not real routing/flow-path differences from ACC's
# missing convective-acceleration term -- see the same docstring).
# Flip this back to False to restore the original slope-based auto-choice
# (DG2 above DG2_SLOPE_THRESHOLD_PCT) -- every other real override
# (has_boundary_inflow, DG2_CUDA_MAX_CELLS) is unaffected either way,
# since both of those already forced ACC before this toggle existed.
FORCE_ACC_ONLY = True

# Real, directly-tested limit on this LISFLOOD-FP 8.2 build's DG2 CUDA
# kernel on this machine's GPU (NVIDIA GB10/DGX Spark) -- see
# choose_solver_for_terrain's own docstring for the real bisection this
# came from. Not a claim about DG2-CUDA everywhere or on other hardware,
# same "measured for this build on this machine" honesty already applied
# elsewhere in this app (see CLAUDE.md's OMP-threading-pin entry).
#
# UPDATED after the real root cause of the original crash was found and
# fixed (see CLAUDE.md's "Root-caused and FIXED the real LISFLOOD-FP
# DG2/CUDA crash" entry): a genuine upstream missing-constructor-argument
# bug in cuda_dg2_simulate.cu (and the same defect in fv1/fv2) caused
# MaxField's own max-depth-tracking buffers to be allocated at the WRONG
# (unpadded) size, an out-of-bounds GPU write confirmed directly with
# compute-sanitizer -- not a fundamental DG2/CUDA domain-size limit. Fixed
# upstream and rebuilt (both `build` and `build_netcdf`). Re-tested after
# the fix: the original 490,000-cell crashing case now completes cleanly,
# and a real 4,000,000-cell domain (3,600 km^2 synthetic terrain) also
# completed cleanly in 606.5s. This constant is raised to exactly that
# DIRECTLY TESTED figure, not extrapolated further -- a genuine full-scale
# GB basin run (6,608,310 cells) was in progress to justify raising it
# further but was stopped before completion; raise again only once a real
# run at or above the new limit is confirmed clean, per this file's own
# "measured, not assumed" discipline.
DG2_CUDA_MAX_CELLS = 4_000_000


def _fill_nodata_nearest(dem, valid):
    """Real nearest-valid-neighbor fill for NODATA cells -- NOT a global
    mean. A real, live-caught bug: filling with `dem[valid].mean()`
    (the original approach here) is fine for a small, elevation-uniform
    AOI, but for a real large/elevation-diverse one (a whole-Islamabad
    AOI spanning plains at ~410m up to real Margalla Hills terrain at
    ~1600m, global mean ~587m) it plants a wildly wrong "neighbor"
    elevation right next to a real steep valid/invalid boundary -- a
    real ~1564m mountain-edge cell suddenly bordering a fake ~587m
    "neighbor" a few dozen metres away. Both real downstream consumers
    of a filled grid (the plain finite-difference gradient in
    choose_solver_for_terrain, and the corner-averaging DG2 slope
    reconstruction in compute_dg2_slopes below) read that as a genuine,
    enormous local slope -- confirmed directly on a real production
    crash: a real DG2 slope coefficient of -142.68 came out of exactly
    this mechanism (a real per-cell coefficient this large should never
    occur on a real ~29m grid cell), and LISFLOOD-FP's own DG2 CUDA
    kernel crashed with `cudaErrorIllegalAddress` at the very first
    timestep on the same real input. Uses scipy's real nearest-neighbor
    index transform (`distance_transform_edt(..., return_indices=True)`)
    -- each invalid cell takes its nearest REAL valid cell's own real
    elevation, so a fake fill never disagrees with genuinely nearby
    terrain the way a single global scalar can."""
    if valid.all():
        return dem
    from scipy import ndimage
    if not valid.any():
        return np.zeros_like(dem)
    _, (iy, ix) = ndimage.distance_transform_edt(~valid, return_distances=True, return_indices=True)
    return dem[iy, ix]


def mean_terrain_slope_pct(dem, cellsize, nodata=-9999.0):
    """Real mean/max terrain slope (percent rise/run), over valid
    (non-NODATA) cells only -- the same real computation
    choose_solver_for_terrain already used inline, factored out here so
    main()'s own steep-terrain initial_tstep decision (see below) can
    reuse it without a second, subtly-different implementation. Returns
    (None, None) if there's no valid terrain at all to assess."""
    valid = dem != nodata
    if not valid.any():
        return None, None
    filled = _fill_nodata_nearest(dem, valid)
    gy, gx = np.gradient(filled, cellsize)
    slope_pct = 100.0 * np.sqrt(gx ** 2 + gy ** 2)
    return float(slope_pct[valid].mean()), float(slope_pct[valid].max())


# Real, empirically-fit ACC depth bias correction for steep terrain,
# where ACC's dropped convective-acceleration term is known to overestimate
# max depth relative to DG2 (the physically correct reference on this
# terrain class). Calibrated from 7 real paired ACC/DG2 runs on identical
# synthetic 64x64-cell, 30m-cellsize, uniform-slope terrain (15/30/50/75/
# 100/150/200% slope), same real 40mm/hr constant-rate storm, same real
# 0.2s initial_tstep, same real cfl=0.7, both under CUDA:
#   slope%   ACC max depth   DG2 max depth   ratio (ACC/DG2)
#     15         0.077           0.023          3.348
#     30         0.095           0.023          4.130
#     50         0.112           0.024          4.667
#     75         0.127           0.022          5.773
#    100         0.139           0.020          6.950
#    150         0.158           0.018          8.778
#    200         0.171           0.017         10.059
# A linear fit of ratio vs slope% (numpy polyfit, degree 1) gave the best
# fit of three forms tried (linear/power-law/log-linear): R^2 = 0.992,
# max real residual error on CORRECTED depth (not just the ratio) = 4.9%
# across this whole real 15-200% range -- a real, defensible correction
# for MAGNITUDE only.
#
# Explicitly NOT a physics fix -- it does not touch, and cannot fix, real
# routing/flow-path errors from ACC's missing momentum terms (water ending
# up in the wrong cell, not just the wrong depth). It is also a narrow
# empirical fit: one storm intensity/duration, one synthetic uniform-slope
# ramp geometry, one domain size -- not verified to generalize to a
# different real storm, a real channelized/complex terrain shape, or a
# much larger domain. Deliberately gated to ONLY apply above this same
# module's own DG2_SLOPE_THRESHOLD_PCT (10%) -- i.e. only in the case
# where ACC is already known to be a compromise fallback (steep terrain
# forced onto ACC by the DG2_CUDA_MAX_CELLS domain-size override), never
# on genuinely gentle terrain where ACC's own real accuracy is already
# good and applying this correction would make it WORSE (at slope=0 the
# fitted ratio is still ~2.97, which would wrongly divide a already-correct
# depth by 3x).
ACC_DEPTH_CORRECTION_INTERCEPT = 2.9664
ACC_DEPTH_CORRECTION_SLOPE = 0.037


def acc_depth_correction_factor(mean_slope_pct):
    """Real, empirically-fit ACC/DG2 depth ratio at a given mean terrain
    slope (percent) -- see the calibration table and honest limitations
    documented above ACC_DEPTH_CORRECTION_INTERCEPT. Divide ACC's own raw
    depth by this factor to get a real, calibrated estimate of what DG2
    would have produced. Only meaningful (and only ever applied by this
    module) above DG2_SLOPE_THRESHOLD_PCT; returns 1.0 (no correction)
    below it, since the fit was never calibrated down there and ACC's own
    real accuracy doesn't need correcting on gentle terrain anyway."""
    if mean_slope_pct is None or mean_slope_pct <= DG2_SLOPE_THRESHOLD_PCT:
        return 1.0
    return ACC_DEPTH_CORRECTION_INTERCEPT + ACC_DEPTH_CORRECTION_SLOPE * mean_slope_pct


def choose_solver_for_terrain(dem, cellsize, nodata=-9999.0, threshold_pct=DG2_SLOPE_THRESHOLD_PCT,
                               has_boundary_inflow=False):
    """Real terrain-slope-based solver choice. Returns (solver, reason).
    Slope computed as a plain centered finite-difference gradient (percent
    rise/run) over valid (non-NODATA) cells only -- the same real DEM
    already loaded for the run, not a separate fetch.

    has_boundary_inflow: real, direct finding from testing this app's own
    GLOF feature (a real 3737 m^3/s discharge over steep Gilgit-Baltistan
    terrain) -- DG2's adaptive timestep collapsed from 0.2s to ~1e-6s
    within the first 7 minutes of simulated time (confirmed via res.mass),
    making the run practically intractable, while the exact same scenario
    forced onto ACC completed in 19s with a near-zero mass-balance error.
    Root-caused via source (cuda_boundary.cu): QVAR/QFIX boundary
    discharge is applied as a direct depth injection (H += discharge*dt/
    dx), the same technique ACC uses -- but DG2's interior scheme is a
    Riemann-solver-based Godunov method expecting a proper ghost-cell
    flux state at boundaries, not a raw depth injection; the resulting
    locally near-discontinuous state is exactly what DG2's CFL condition
    is supposed to react to by shrinking its timestep, so this is DG2
    behaving correctly given a boundary condition it was never designed
    for, not a bug in DG2 itself.

    **Re-confirmed independently of the later CUDA-boundary-injection bug
    fix** (see write_par_file's own docstring for that separate, real
    silent-zero-flow issue): re-ran this exact scenario on the CPU build
    (which the CUDA fix now also routes boundary-inflow runs through) and
    the identical collapse signature reproduced there too (MinTstep down
    to 7.7e-05s) -- proving DG2's own instability is a distinct, separate
    problem from the CUDA bug, not something the CPU fallback happens to
    also fix. A real solver-internals fix (a proper Riemann ghost-state
    boundary treatment inside LISFLOOD-FP's own DG2 C++/CUDA source) was
    evaluated and deliberately NOT attempted -- specialist numerical-
    methods surgery on a foreign, published scientific codebase, with
    real risk of silently wrong physics for every future run. Forcing ACC
    for any real boundary inflow (regardless of terrain slope) is the
    settled, correct mitigation, not a placeholder for a future fix.
    Rain-only runs on steep terrain are unaffected and still get DG2.

    **Second real override, originally found via a real production crash
    (whole-Islamabad AOI, 446,787 buildings, 1769x1175 = 2,078,575
    cells)**: LISFLOOD-FP 8.2's own DG2 CUDA kernel used to crash with a
    real `cudaErrorIllegalAddress` at the very first timestep on large
    domains on this machine's GPU -- root-caused via direct bisection on
    the exact real crashing DEM (not guessed): a 150x150 (22,500-cell)
    crop of the SAME steep terrain ran fine (45s); a 500x500 (250,000-
    cell) crop was genuinely computing (real per-timestep output files
    being written, not crashing) before being cut off by a test timeout;
    a 700x700 (490,000-cell) crop crashed immediately, identically to the
    full 2,078,575-cell domain. The real CPU (non-CUDA) DG2 build did NOT
    crash on the same full-size input either (confirmed: still genuinely
    computing, not erroring, well past the point CUDA had already
    failed) -- proving this was a real CUDA-kernel-specific bug in this
    LISFLOOD-FP 8.2 build, not a fundamental DG2 numerical instability
    with this terrain, and not something the earlier NODATA-fill fix
    (see _fill_nodata_nearest) was ever going to resolve on its own (that
    fix was real and worth keeping -- it corrects a genuinely wrong
    per-cell slope reconstruction -- it just wasn't the cause of this
    particular crash).

    **UPDATE: this crash IS now fixed, root-caused with compute-sanitizer
    rather than declined as unfixable.** The real defect was a genuine
    upstream missing-constructor-argument bug in `cuda_dg2_simulate.cu`
    (and the identical copy-paste defect in fv1/fv2's own simulate.cu):
    `MaxField`'s constructor call was missing its `acceleration` argument
    entirely, so `pars.output_precision` silently shifted one parameter
    slot early into `acceleration` -- causing the max-depth-tracking
    buffers to be allocated at the WRONG (unpadded) size for a DG2 run,
    while `update_max_field`'s own kernel indexes them with DG2's real
    padded pitch. compute-sanitizer confirmed the exact out-of-bounds
    write, growing with domain perimeter (why small domains "worked" by
    luck and large ones didn't). The commented-out reference call in
    `cuda_acc_simulate.cu` still shows the correct, original 7-argument
    call, confirming this was a real refactor/copy-paste regression, not
    a fundamental limitation -- so fixing it (supplying the real,
    already-in-scope `states.acceleration` argument) was a safe, narrowly
    scoped fix, unlike the deep solver-internals surgery this project has
    correctly declined elsewhere. See CLAUDE.md's own entry for the full
    diagnosis and verification (re-tested clean at the original 490,000-
    cell crashing case, and fresh at a real 4,000,000-cell domain).
    DG2_CUDA_MAX_CELLS is raised accordingly, to exactly what has been
    directly tested -- not further. A real fix inside LISFLOOD-FP's own
    foreign CUDA source was, in this one specific instance, NOT declined
    the way it was for the boundary-inflow numerical-instability case
    below, because compute-sanitizer gave an exact, unambiguous, low-risk
    diagnosis (a missing argument, not a numerics judgment call) --
    that distinction is the reason one was fixed and the other wasn't.
    Note this fix does NOT touch the SEPARATE, still-real `has_boundary_
    inflow` override above (DG2's own numerical-instability collapse
    under boundary-injected flow) -- that remains a genuine, distinct,
    unresolved limitation of DG2's own boundary treatment, not a memory
    bug, and boundary inflow still forces ACC regardless of terrain.

    ACC is still used above DG2_CUDA_MAX_CELLS, regardless of terrain
    slope -- the same class of trade-off already established for
    boundary inflow (a real, working, slightly-less-accurate run beats a
    guaranteed crash)."""
    if has_boundary_inflow:
        return "acc", ("a real boundary-injected river/GLOF inflow is present -- ACC is used regardless of "
                        "terrain slope, since DG2's explicit adaptive timestep was found (via direct testing) "
                        "to collapse toward numerical infeasibility under a concentrated boundary discharge "
                        "spike, even though DG2 would otherwise be preferred for this terrain's steepness")
    n_cells = int(dem.size)
    if n_cells > DG2_CUDA_MAX_CELLS:
        mean_slope, max_slope = mean_terrain_slope_pct(dem, cellsize, nodata=nodata)
        return "acc", (f"domain has {n_cells:,} cells, above the {DG2_CUDA_MAX_CELLS:,}-cell limit directly "
                        "confirmed safe for this LISFLOOD-FP 8.2 build's DG2 CUDA kernel on this machine's GPU "
                        "(a real cudaErrorIllegalAddress crash was reproduced above this size on a real "
                        "whole-Islamabad AOI) -- ACC is used regardless of terrain slope "
                        f"(mean {mean_slope:.1f}%, max {max_slope:.1f}%), a real, working, "
                        "slightly-less-accurate run instead of a guaranteed crash")
    mean_slope, max_slope = mean_terrain_slope_pct(dem, cellsize, nodata=nodata)
    if mean_slope is None:
        return "acc", "no valid terrain cells to assess slope; defaulting to ACC"
    if mean_slope > threshold_pct:
        if FORCE_ACC_ONLY:
            return "acc", (f"mean terrain slope {mean_slope:.1f}% exceeds the {threshold_pct:.0f}% "
                            f"steep-terrain threshold (max {max_slope:.1f}%) -- would normally use DG2, but "
                            f"FORCE_ACC_ONLY is set: using ACC with its real, empirically-calibrated depth "
                            f"bias correction instead (see acc_depth_correction_factor), trading DG2's real "
                            f"wall-clock cost for speed at the cost of real routing/flow-path accuracy, "
                            f"which the correction does not fix -- see flood_model.py's own FORCE_ACC_ONLY "
                            f"comment to revert")
        return "dg2", (f"mean terrain slope {mean_slope:.1f}% exceeds the {threshold_pct:.0f}% "
                        f"steep-terrain threshold (max {max_slope:.1f}%) -- using DG2 (full shallow "
                        f"water) instead of ACC (local-inertial), since ACC's convective-acceleration "
                        f"simplification degrades on steep, momentum-dominated flow")
    return "acc", (f"mean terrain slope {mean_slope:.1f}% is at or below the {threshold_pct:.0f}% "
                   f"steep-terrain threshold (max {max_slope:.1f}%) -- using ACC (local-inertial), "
                   f"the faster solver, appropriate for gentle floodplain slopes")


def compute_dg2_slopes(dem, nodata=-9999.0):
    """Real 2nd-order (DG2) DEM reconstruction: a per-cell elevation plus
    its own x/y slope coefficients in LISFLOOD-FP's own Legendre-polynomial
    basis -- confirmed directly from source (swe/dg2/fields.cpp's
    dg2::initialise_field): given the elevation at a cell's 4 corners
    (NW/NE/SW/SE), the solver's own convention is
        F1x = (NE+SE-NW-SW) / (4*sqrt(3))
        F1y = (NE+NW-SE-SW) / (4*sqrt(3))
    Corner elevations aren't directly available from a plain cell-centered
    DEM, so they're reconstructed here the standard way: each corner is
    the mean of the (up to 4) cell centers touching it (edge-replicated at
    the domain boundary, matching how the solver zeros slopes there
    anyway). The cell's own elevation (F) is kept as the ORIGINAL DEM
    value, not the corner-average -- keeps this run's mean elevation
    identical to what the ACC solver would see on the same DEM, so an
    ACC-vs-DG2 comparison isn't skewed by a subtly different terrain.
    Returns (dem1x, dem1y), NODATA-safe (zero slope at any NODATA cell)."""
    nrows, ncols = dem.shape
    valid = dem != nodata
    filled = _fill_nodata_nearest(dem, valid)

    # Vertex (corner) grid: (nrows+1, ncols+1), each vertex = mean of the
    # up to 4 adjacent cell centers (fewer at domain edges/corners).
    padded = np.pad(filled, ((1, 1), (1, 1)), mode="edge")
    vert = 0.25 * (padded[0:-1, 0:-1] + padded[0:-1, 1:] + padded[1:, 0:-1] + padded[1:, 1:])
    # vert has shape (nrows+1, ncols+1): vert[j, i] is the NW corner of cell (j, i).
    nw = vert[0:-1, 0:-1]
    ne = vert[0:-1, 1:]
    sw = vert[1:, 0:-1]
    se = vert[1:, 1:]

    sqrt3 = np.sqrt(3.0)
    dem1x = (ne + se - nw - sw) / (4.0 * sqrt3)
    dem1y = (ne + nw - se - sw) / (4.0 * sqrt3)
    dem1x = np.where(valid, dem1x, 0.0)
    dem1y = np.where(valid, dem1y, 0.0)
    # LISFLOOD-FP's own dg2::zero_dem_perimeter_slopes zeros the outermost
    # ring at load time regardless -- matched here too so a DEM written by
    # this function alone (e.g. for inspection) is already consistent.
    dem1x[0, :] = 0.0; dem1x[-1, :] = 0.0; dem1x[:, 0] = 0.0; dem1x[:, -1] = 0.0
    dem1y[0, :] = 0.0; dem1y[-1, :] = 0.0; dem1y[:, 0] = 0.0; dem1y[:, -1] = 0.0
    return dem1x, dem1y


def write_dg2_dem_slopes(dem_ascii_path, header, dem, nodata=-9999.0):
    """Writes <dem_ascii_path>1x and <dem_ascii_path>1y -- the exact
    filename convention LISFLOOD-FP's DG2 solver (CPU and CUDA alike,
    confirmed from source) expects alongside the DEM itself."""
    dem1x, dem1y = compute_dg2_slopes(dem, nodata=nodata)
    write_ascii(dem_ascii_path + "1x", header, dem1x, nodata=-9999)
    write_ascii(dem_ascii_path + "1y", header, dem1y, nodata=-9999)


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
    """Symmetric triangular design storm peaking at half the storm duration.

    Real bug found while building a pure-fluvial (rain_mm_hr=0, duration_hr=0,
    e.g. a GLOF with no accompanying rainfall) run through the CPU solver
    path: with duration_hr=0, the triangular points degenerate to three
    IDENTICAL t=0.0 timestamps ((0,0),(peak,0),(0,0),(0,sim_hours)) --
    LISFLOOD-FP's CPU time-series loader rejects this outright ("times
    should be in increasing order"), aborting the whole run. The GPU/CUDA
    loader apparently didn't validate this as strictly, so the bug was
    latent/invisible under the GPU-only path this app used until now.
    Genuinely no rainfall to model in this case, so a flat two-point
    zero-everywhere series is written instead of the (degenerate)
    triangular shape -- correct either way since peak_mm_hr is 0 too."""
    if duration_hr <= 0 or peak_mm_hr <= 0:
        pts = [(0.0, 0.0), (0.0, max(sim_hours, 0.01))]
        with open(path, "w") as f:
            f.write("# design storm generated by PluviSim\n")
            f.write(f"{len(pts)}   hours\n")
            for rate, t in pts:
                f.write(f"{rate:.4f}   {t:.4f}\n")
        return pts
    t_peak = duration_hr / 2.0
    pts = [(0.0, 0.0), (peak_mm_hr, t_peak), (0.0, duration_hr), (0.0, sim_hours)]
    with open(path, "w") as f:
        f.write("# design storm generated by PluviSim\n")
        f.write(f"{len(pts)}   hours\n")
        for rate, t in pts:
            f.write(f"{rate:.4f}   {t:.4f}\n")
    return pts


def write_rain_file_from_series(path, times_hr, rates_mm_hr, sim_hours):
    """Real observed/uploaded rainfall time series -- SAME on-disk format
    write_rain_file already uses (LISFLOOD-FP's own "rainfall" keyword
    time-series file: a comment line, "<count> hours", then "<rate_mm_hr>
    <time_hr>" pairs), just populated with real measured values instead
    of a synthetic triangle. No new solver capability needed: this is
    the exact same mechanism every other rainfall run in this app already
    uses, just given real points instead of 3-4 design-storm ones.

    Real-world CSV rain-gauge exports (e.g. a 15-minute MACH station
    export) can contain small negative values from real sensor resets/
    drift artifacts -- clamped to 0.0 here (a real physical rain rate can
    never be negative), not treated as a data error to reject the whole
    upload over. Also guarantees the series ends with a real 0.0 point at
    or after `sim_hours` (LISFLOOD-FP's own parser requires strictly
    increasing times and needs SOME point covering the full simulated
    window), and that every input time is made STRICTLY increasing
    (duplicate timestamps merged -- see below) regardless of what the
    caller already did."""
    pairs = sorted(zip(times_hr, rates_mm_hr), key=lambda p: p[0])
    # Real, previously-shipped gap fixed here, not assumed away: sorting
    # alone only guarantees non-decreasing times, not the STRICTLY
    # increasing sequence LISFLOOD-FP's own parser requires -- a real
    # rain-gauge export can contain two rows at the identical timestamp
    # (a duplicate/resent row), which would otherwise reach the solver as
    # a tie, exactly the same failure mode already found and fixed once
    # for this app's IDF curve builder (duplicate durations breaking
    # np.interp). Merges any tied timestamp into one point here, keeping
    # the LARGER real rate of the two rather than silently keeping
    # whichever happened to sort first.
    merged = []
    for t, rate in pairs:
        rate = max(0.0, rate)
        if merged and abs(t - merged[-1][0]) < 1e-9:
            merged[-1] = (t, max(merged[-1][1], rate))
        else:
            merged.append((t, rate))
    pts = [(rate, t) for t, rate in merged]
    if not pts:
        pts = [(0.0, 0.0), (0.0, max(sim_hours, 0.01))]
    elif pts[-1][1] < sim_hours:
        pts.append((0.0, sim_hours))
    # write_rain_file's own on-disk convention: rate first, then time.
    with open(path, "w") as f:
        f.write("# real observed/uploaded rainfall series\n")
        f.write(f"{len(pts)}   hours\n")
        for rate, t in pts:
            f.write(f"{rate:.4f}   {t:.4f}\n")
    return pts


def write_bdy_file(path, name, peak_m3s, duration_hr, sim_hours, span_m, rise_frac=0.5):
    """Real time-varying discharge boundary (QVAR), a triangular design
    hydrograph peaking at `rise_frac` of the way through the inflow
    duration -- the exact same shape/logic as write_rain_file's design
    storm, applied to river discharge instead of rainfall, so a fluvial
    event rises, peaks, and recedes instead of the earlier constant
    (QFIX) inflow just switching on and staying flat for the whole run.
    `name` must match the QVAR reference written into the .bci file for
    the same edge.

    rise_frac=0.5 (default) is the original symmetric storm-runoff shape
    (rise and recession take equally long) -- appropriate for an ordinary
    river flood. A real GLOF (Glacial Lake Outburst Flood) hydrograph is
    NOT symmetric: a moraine/ice-dam breach releases its peak flow within
    minutes to about an hour, then recedes much more slowly over several
    hours as the lake continues draining -- a real, documented shape
    difference (e.g. the real Shishper Glacier GLOF events, Gilgit-
    Baltistan), not a minor detail. Callers building a GLOF scenario
    should pass a small rise_frac (e.g. 0.05-0.15) instead of the default.

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
    to recover the real total discharge (m3/s). A single real .bdy FILE
    can (and, for multiple real simultaneous inflows, must) hold several
    of these named blocks back to back -- LoadTimeSeries keeps reading
    blocks until it finds every QVAR name referenced in the .bci file, so
    this function writes exactly one file with exactly one block; a
    caller with multiple real inflows should build each block via
    bdy_block() and write them all into ONE file with write_bdy_multi()."""
    with open(path, "w") as f:
        f.write(bdy_block(name, peak_m3s, duration_hr, sim_hours, span_m, rise_frac=rise_frac))
    return None


def bdy_block(name, peak_m3s, duration_hr, sim_hours, span_m, rise_frac=0.5):
    """Real triangular design hydrograph as one named .bdy block of text
    (see write_bdy_file's own docstring for the real format/reasoning) --
    factored out so multiple real inflows can be combined into one real
    multi-block .bdy file via write_bdy_multi()."""
    t_peak = duration_hr * max(0.001, min(0.999, rise_frac))
    pts = [(0.0, 0.0), (peak_m3s / span_m, t_peak), (0.0, duration_hr), (0.0, sim_hours)]
    lines = ["# river discharge hydrograph generated by SAILAB", name, f"{len(pts)}   hours"]
    lines += [f"{q_per_width:.6f}   {t:.4f}" for q_per_width, t in pts]
    return "\n".join(lines) + "\n"


def normalize_inflow_series(inflow):
    """If an inflow carries a real time series (`series`: {times_hr, q_m3s}
    -- e.g. a real multi-day snowmelt hydrograph), validates it and sets the
    fields the rest of this module already keys off (duration_hr = series
    span, peak_m3s = series maximum). Returns the cleaned (times, q) lists,
    or None for an ordinary triangular inflow. Raises ValueError on a series
    that can't be written as a valid .bdy block."""
    ser = inflow.get("series")
    if not ser:
        return None
    times, q = list(ser["times_hr"]), list(ser["q_m3s"])
    if len(times) != len(q) or len(times) < 2:
        raise ValueError("inflow series needs >=2 matching times_hr / q_m3s points")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("inflow series times_hr must be strictly increasing")
    if min(q) < 0:
        raise ValueError("inflow series discharges must be >= 0")
    inflow["duration_hr"] = float(times[-1])
    inflow["peak_m3s"] = float(max(q))
    return times, q


def bdy_block_series(name, times_hr, q_m3s, sim_hours, span_m):
    """Real time-varying discharge boundary from an arbitrary real series
    (same .bdy format/units as bdy_block: per-unit-width flux, value then
    time). A series that doesn't start at t=0 is extended back to 0 at its
    first value; after its last point the LAST value is held until the end
    of the simulation (stated assumption -- the source forecast simply
    doesn't say what happens next)."""
    times, q = list(times_hr), list(q_m3s)
    if times[0] > 0:
        times.insert(0, 0.0)
        q.insert(0, q[0])
    pts = [(qq / span_m, t) for t, qq in zip(times, q)]
    if sim_hours > times[-1]:
        pts.append((q[-1] / span_m, sim_hours))
    lines = ["# river discharge series generated by SAILAB", name, f"{len(pts)}   hours"]
    lines += [f"{v:.6f}   {t:.4f}" for v, t in pts]
    return "\n".join(lines) + "\n"


def write_bdy_multi(path, blocks):
    """Writes several real named hydrograph blocks (each from bdy_block())
    into ONE .bdy file -- the real format multiple simultaneous QVAR
    inflows need (see write_bdy_file's docstring)."""
    with open(path, "w") as f:
        f.write("".join(blocks))


def bdy_block_hvar(name, times_hr, levels_m):
    """Real time-varying WATER-LEVEL (stage, HVAR) boundary block -- same
    real LISFLOOD-FP .bdy block format as bdy_block()/QVAR (value-then-time
    per line under a <name>/<count> header, confirmed from input.cpp's
    LoadTimeSeries and HVAR3 parsing), but unlike QVAR's per-unit-width
    flux, an HVAR value is an ABSOLUTE water-surface elevation (metres,
    same vertical datum as the DEM itself -- i.e. 0 = mean sea level, the
    same convention coastal_utils.py's static method already uses) applied
    directly at the boundary, no division by segment width.

    Takes arbitrary real (times_hr, levels_m) pairs rather than assuming a
    symmetric triangular shape (unlike bdy_block's design-storm shape) --
    built this way specifically so a real historical GTSR daily-maxima
    storm-surge episode's own actual rise/peak/recede shape can be used
    directly instead of approximating it as one clean triangle."""
    assert len(times_hr) == len(levels_m) and len(times_hr) >= 2
    for i in range(1, len(times_hr)):
        assert times_hr[i] > times_hr[i - 1], "HVAR times must be strictly increasing"
    lines = ["# coastal water-level (HVAR) boundary generated by SAILAB", name,
             f"{len(times_hr)}   hours"]
    lines += [f"{lv:.4f}   {t:.4f}" for lv, t in zip(levels_m, times_hr)]
    return "\n".join(lines) + "\n"


def write_bci_file(path, header, outlet_edges, inflows=None, outflows=None):
    """Real multi-inflow, multi-outflow boundary conditions -- generalizes
    the app's original single-inflow-edge design to any number of real,
    independently-sited river/stream/GLOF inflows and outflows, each
    concentrated into its own real channel-width segment rather than
    smeared across a whole domain edge (the same real reasoning already
    established for a single inflow: diluting a real discharge across a
    multi-km edge makes it too thin a flux to ever pond/overtop
    realistically -- true for outflow too, a real river doesn't drain
    through the WHOLE hillside it happens to exit near).

    outlet_edges: the ORIGINAL whole-edge-FREE fallback -- an edge with no
    explicit `outflows` entries of its own still gets this simpler
    treatment (FREE if listed here, else CLOSED), so a plain pluvial-only
    run (no real detected/added outflow points) drains exactly as it
    always has. An edge that DOES have one or more explicit `outflows`
    entries switches to precise per-segment treatment instead: FREE only
    at each real outflow's own position/width, CLOSED everywhere else on
    that edge (including whatever outlet_edges says) -- once a real
    outflow location is known, guessing a wider drain elsewhere on the
    same edge is no longer the best available information.

    inflows / outflows: each a dict with "edge" (N/E/S/W -- see
    rainfall_rivers_utils.edge_and_position_for_point for how any real
    360-degree lon/lat crossing point is resolved down to one of these 4
    plus a position, since LISFLOOD-FP's own .bci format is inherently
    edge-segment-based, not a limitation added here) and "start_m"/"end_m"
    (DEM-local metres along that edge, already clamped by the caller).
    inflows additionally carry either "peak_m3s" (QFIX, constant) or
    "bdy_name" (QVAR, references a real hydrograph written by
    write_bdy_file -- takes priority if both are present).

    IMPORTANT, found the hard way: LISFLOOD-FP's .bci start/finish are NOT
    relative to the DEM's own corner at 0 -- confirmed directly from
    input.cpp (LoadBCs fscanf's Parptr->blx/bly straight from the DEM
    ASCII header's own xllcorner/yllcorner, overwriting lisflood.cpp's
    initial 0.0 default). Fixed by adding the DEM's own xllcorner (N/S
    edges) or yllcorner (E/W edges) to every coordinate written below --
    see CLAUDE.md for the real blow-up this caused before being found."""
    inflows = inflows or []
    outflows = outflows or []
    ncols, nrows, cs = header["ncols"], header["nrows"], header["cellsize"]
    width, height = ncols * cs, nrows * cs
    xll, yll = header["xllcorner"], header["yllcorner"]
    lines = []
    for edge, span, origin in (("N", width, xll), ("E", height, yll), ("S", width, xll), ("W", height, yll)):
        segs = []  # (start, end, bc_string), local 0..span coordinates
        for inflow in inflows:
            if inflow["edge"] != edge:
                continue
            s = max(0.0, min(span, inflow["start_m"]))
            e = max(0.0, min(span, inflow["end_m"]))
            if e < s:
                s, e = e, s
            if inflow.get("hvar_name"):
                # Real time-varying WATER-LEVEL (stage) boundary -- see
                # bdy_block_hvar's own docstring. Unlike QFIX/QVAR (a flux
                # divided across the segment width), HVAR is an absolute
                # elevation and needs no width-based scaling.
                bc = f"HVAR   {inflow['hvar_name']}"
            elif inflow.get("bdy_name"):
                bc = f"QVAR   {inflow['bdy_name']}"
            else:
                q_per_width = inflow.get("peak_m3s", 0.0) / max(e - s, 1e-6)
                bc = f"QFIX   {q_per_width:.6f}"
            segs.append((s, e, bc))
        for outflow in outflows:
            if outflow["edge"] != edge:
                continue
            s = max(0.0, min(span, outflow["start_m"]))
            e = max(0.0, min(span, outflow["end_m"]))
            if e < s:
                s, e = e, s
            segs.append((s, e, "FREE   0.001"))

        if not segs:
            bc = "FREE   0.001" if edge in outlet_edges else "CLOSED"
            lines.append(f"{edge}   {origin:.4f}   {origin + span:.4f}   {bc}")
            continue

        segs.sort(key=lambda t: t[0])
        cursor = 0.0
        for s, e, bc in segs:
            if s > cursor:
                lines.append(f"{edge}   {origin + cursor:.4f}   {origin + s:.4f}   CLOSED")
            lines.append(f"{edge}   {origin + s:.4f}   {origin + e:.4f}   {bc}")
            cursor = max(cursor, e)
        if cursor < span:
            lines.append(f"{edge}   {origin + cursor:.4f}   {origin + span:.4f}   CLOSED")
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


def inflow_segment_touches_nodata(dem, header, nodata, edge, start_m, end_m):
    """True if any DEM cell along the edge segment [start_m, end_m] (metres from the edge's
    low-coordinate end: x from the west for N/S, y from the south for E/W) is NODATA."""
    import numpy as _np
    nrows, ncols, cs = int(header["nrows"]), int(header["ncols"]), float(header["cellsize"])
    if edge in ("N", "S"):
        n, along = ncols, (dem[0, :] if edge == "N" else dem[-1, :])
        c0, c1 = int(_np.floor(start_m / cs)), int(_np.ceil(end_m / cs))
        seg = along[max(0, c0):min(n, max(c1, c0 + 1))]
    else:
        col = dem[:, 0] if edge == "W" else dem[:, -1]
        r_hi = nrows - int(_np.floor(start_m / cs))
        r_lo = nrows - int(_np.ceil(end_m / cs))
        seg = col[max(0, min(r_lo, r_hi - 1)):min(nrows, r_hi)]
    return bool(seg.size == 0 or _np.any(seg == nodata))


def write_par_file(path, dem_name, rain_name, bci_name, resroot, dirroot,
                    sim_time_s, saveint_s, massint_s, cfl, fpfric, initial_tstep,
                    infiltration_m_s=0.0, bdy_name=None, manning_file=None, min_fric_seen=None,
                    solver="acc", dynamic_rain=False, has_boundary_inflow=False, mint_hk=False,
                    depththresh=None, cuda_unsafe_boundary=None):
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
        # Real, directly-reproduced bug found while building GB GLOF/fluvial
        # demo videos (2026): this LISFLOOD-FP 8.2 CUDA build's edge
        # boundary-condition Q injection (QFIX AND QVAR, i.e. every real
        # river/GLOF inflow this app supports) silently injects ZERO flow
        # under BOTH the ACC and DG2 cuda paths -- confirmed by isolating
        # a minimal synthetic DEM+QFIX .bci/.par, running it once with
        # "cuda" (0.0m depth everywhere, exit code 0, no warning) and once
        # without (14.08m depth, 595 wet cells, identical inputs otherwise)
        # -- and separately reproduced end-to-end on a real Hunza-valley
        # GLOF scenario (real river inflow silently produced 0.0 km^2
        # flooded / NaN mass-balance with no error). This is NOT the
        # already-documented "res.maxHaz/Qin column not written under CUDA"
        # limitation (those are diagnostic-only gaps with real physics
        # still happening underneath) -- this is the actual physics never
        # entering the domain at all, while the run reports success. Until
        # this upstream CUDA solver bug is fixed, any run with a real
        # boundary inflow (river/GLOF -- has_boundary_inflow) falls back to
        # the CPU build of the same ACC/DG2 scheme, which was directly
        # verified to inject correctly. Pure-pluvial runs (rain only, no
        # boundary inflow) are unaffected and keep the real ~10-11x GPU
        # speedup verified earlier for that case.
        # SAILAB_FORCE_CPU=1 (opt-in, default off): run on the CPU build even for a
        # pure-pluvial run -- for when the shared GPU is busy/hot. Same physics
        # (the CPU build is bit-identical to the old v5.9 binary, see CLAUDE.md),
        # just much slower on large domains.
        # UPDATE (Oct 2026, measured): the "CUDA boundary-injection bug" described above was
        # mostly a misdiagnosis. Re-tested on a real DEM with the inflow on VALID terrain, the
        # CUDA ACC QVAR boundary matches the CPU build to 5 digits (stored volume 1.9672e5 /
        # 1.5746e6 m3 at 1 h / 4 h on both). What actually broke was (a) the inlet sitting on
        # NODATA cells -- CUDA zeroes water in NODATA cells every step and the -9999 m "pit"
        # drives a blow-up, so the flood was silently lost or exploded -- and (b) CUDA ACC has no
        # QFIX4 case (constant inflow = 0), which main() now avoids by writing a constant QVAR.
        # HVAR (coastal stage): the CUDA gate only applied it to already-wet cells; patched in
        # cuda_acc_solver.cu (Oct 2026) to match the CPU build, verified identical on a real DEM.
        # cuda_unsafe_boundary=None keeps the old behaviour for callers that don't say.
        if cuda_unsafe_boundary is None:
            cuda_unsafe_boundary = has_boundary_inflow
        use_cuda = ((not cuda_unsafe_boundary) or os.environ.get("SAILAB_FORCE_CUDA") == "1") and os.environ.get("SAILAB_FORCE_CPU") != "1"
        if solver == "dg2":
            # Real, unsimplified 2nd-order shallow-water solver (see
            # choose_solver_for_terrain) -- also a real GPU/CUDA path
            # (lis::cuda::dg2::Simulation, confirmed from lisflood.cpp's
            # own dispatch), same "cuda" keyword as ACC. theta (ACC's own
            # low-friction numerical-diffusion mitigation) doesn't apply
            # here -- DG2's Riemann-solver-based flux is not subject to
            # the same instability, so it's simply not written.
            f.write("dg2\n")
            if use_cuda:
                f.write("cuda\n")
        else:
            f.write("acceleration\n")
            if use_cuda:
                f.write("cuda\n")  # real GPU-accelerated ACC solver -- see LISFLOOD_BIN's own comment
        f.write(f"cfl              {cfl:.2f}\n")
        f.write(f"fpfric           {fpfric:.4f}\n")
        if manning_file:
            f.write(f"manningfile      {manning_file}\n")
        if solver != "dg2" and theta < 1.0:
            # Adds numerical diffusion to the inertial (ACC) scheme --
            # LISFLOOD-FP's own documented mitigation for the low-friction
            # instability above. Only written when actually needed (fpfric
            # below MIN_FPFRIC); LISFLOOD-FP's own default (1.0, no extra
            # diffusion) applies otherwise, unchanged from before.
            f.write(f"theta            {theta:.2f}\n")
        if dynamic_rain:
            # Real per-cell/per-timestep rainfall (storm centering) --
            # LISFLOOD-FP's own DynamicRain/NetCDF mechanism, a genuinely
            # separate keyword/code path from the uniform "rainfall" one
            # above (confirmed from source: pars.cpp's dynamicrainfile
            # sets Statesptr->dynamicrainfall, not Statesptr->rainfall).
            f.write(f"dynamicrainfile {rain_name}\n")
        else:
            f.write(f"rainfall         {rain_name}\n")
        f.write(f"bcifile          {bci_name}\n")
        if bdy_name:
            f.write(f"bdyfile          {bdy_name}\n")  # real time-varying (QVAR) river inflow hydrograph
        if infiltration_m_s > 0:
            f.write(f"infiltration     {infiltration_m_s:.10f}\n")
        f.write("hazard\n")   # also enables voutput -> velocity + DEFRA hazard rasters
        f.write("comp_out\n")
        if depththresh is not None:
            f.write(f"depththresh      {depththresh:.6f}\n")
        if mint_hk:
            # Real, confirmed-wired-into-CUDA-ACC keyword (cuda_acc_
            # simulate.cu's own stats.need_to_write check) -- updates
            # maxH/maxHtm/totalHtm/initHtm only at the massint cadence
            # instead of every physics timestep, a real speedup on huge
            # domains with many small timesteps. NOT enabled unconditionally
            # here: see this app's own direct A/B benchmark (a real sharp
            # QVAR spike run with/without this flag) before any caller turns
            # it on -- a coarse massint can genuinely miss a fast event's
            # true peak, not just add "a small precision cost".
            f.write("mint_hk\n")


# --------------------------------------------------------------------------
# Run + parse
# --------------------------------------------------------------------------
def run_lisflood(workdir, par_name, verbose=True, use_netcdf_binary=False):
    env = dict(os.environ)
    # Real, DIRECTLY MEASURED finding on this specific shared machine --
    # the opposite of the naive assumption that more OpenMP threads means
    # faster. The CPU/ACC solver (fp_acc.cpp) is genuinely OpenMP-parallel
    # (`#pragma omp parallel for`, confirmed linked against libgomp.so.1)
    # but ALSO uses `#pragma omp critical` inside that same parallel
    # region -- and a real, controlled benchmark (identical boundary-
    # inflow scenario, same 300x300 grid, back-to-back on this same
    # machine) showed OMP_NUM_THREADS=1 finishing in ~60s while EVERY
    # multi-threaded value tried (2, 4, 20/unset-default) finished in
    # 6-6.8 MINUTES -- a 6-7x SLOWDOWN, not a speedup, and the slowdown
    # was essentially identical regardless of how many threads (2 was
    # just as bad as 20). This is consistent with the critical section
    # serializing most of the real work while every thread still pays
    # real fork-join/wake-up overhead every timestep, on a machine this
    # heavily shared (34 concurrent real users, load average ~17-19 at
    # benchmark time) where thread wake-up latency is itself elevated.
    # Explicitly pinning to 1 thread (rather than leaving this to
    # libgomp's own default, which already silently claimed every core
    # and produced the slow behavior above even before this fix existed)
    # is the real, measured, correct choice for this app on this
    # machine today. If this solver's own OpenMP implementation is ever
    # revised upstream (a real fix to the critical-section granularity),
    # or this app moves to less-contended hardware, this constant is the
    # one place to revisit -- re-run the same kind of A/B benchmark
    # before changing it, don't just assume more cores will help.
    env["OMP_NUM_THREADS"] = "1"
    if use_netcdf_binary:
        # Real spatial (storm-centered) rainfall needs the NetCDF-enabled
        # build -- see LISFLOOD_BIN_NETCDF's own comment.
        binary = LISFLOOD_BIN_NETCDF
        env["LD_LIBRARY_PATH"] = (LISFLOOD_DIR_NETCDF + ":" + LISFLOOD_CUDA_LIB_DIR + ":" +
                                   NETCDF_LOCAL_LIB_DIR + ":" + NUMA_STUB_LIB_DIR + ":" +
                                   env.get("LD_LIBRARY_PATH", ""))
    else:
        binary = LISFLOOD_BIN
        env["LD_LIBRARY_PATH"] = LISFLOOD_DIR + ":" + LISFLOOD_CUDA_LIB_DIR + ":" + env.get("LD_LIBRARY_PATH", "")
    cmd = [binary] + (["-v"] if verbose else []) + [par_name]
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

    Vx/Vy's grid convention depends on the solver, confirmed directly from
    each one's own CUDA snapshot source: ACC writes them on the staggered
    (Arakawa C) grid (Vx has one extra column -- vertical cell faces; Vy
    one extra row -- horizontal faces), de-staggered here by averaging
    back onto depth's cell centers (the same de-staggering already proven
    correct in fis_app.py's _read_water_stack). DG2's own VelocityWriter
    is already cell-centered (same shape as depth, no staggering) -- used
    directly, no averaging needed."""
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
        if vx_face.shape == depth_c.shape and vy_face.shape == depth_c.shape:
            vx_c, vy_c = vx_face, vy_face  # DG2: already cell-centered
        elif vx_face.shape[1] == depth_c.shape[1] + 1 and vy_face.shape[0] == depth_c.shape[0] + 1:
            vx_c = 0.5 * (vx_face[:, :-1] + vx_face[:, 1:])  # ACC: staggered, de-stagger
            vy_c = 0.5 * (vy_face[:-1, :] + vy_face[1:, :])
        else:
            return None  # unexpected grid shape -- don't guess, just skip the fallback
        speed = np.sqrt(vx_c ** 2 + vy_c ** 2)
        haz = depth_c * (speed + 1.5)
        max_haz = np.maximum(max_haz, haz)

    max_haz[~valid_mask] = nodata
    return max_haz


def compute_max_velocity_from_timesteps(results_dir, nodata, dry_thresh=0.05):
    """Real peak flow velocity (m/s), taken as a running per-cell maximum
    over the whole run, computed directly from the solver's own real
    per-timestep depth/velocity rasters (res-*.wd/.Vx/.Vy) -- the same
    real files, same real de-staggering logic (see
    compute_max_hazard_from_timesteps's own docstring for the real
    Arakawa-C grid-convention finding this shares), just reporting the
    real speed itself rather than folding it into the DEFRA hazard
    rating. Returns (max_velocity_grid, real_scalar_max_ms,
    real_scalar_mean_ms_in_flooded_area) -- the grid for anyone who wants
    per-cell detail, the two scalars for a real, honest one-line summary
    figure.

    Real bug found and fixed here, not shipped blind: an initial version
    of this function tracked "was this cell EVER wet above dry_thresh, at
    ANY timestep" as a separate spatial mask, then reported that cell's
    own all-time peak speed regardless of what the depth was AT THE
    MOMENT that peak speed occurred. Live-verified this produced a
    physically impossible real result (72.8 m/s reported for a shallow
    pluvial flood) -- traced directly to a real, well-known shallow-water
    numerical artifact: at a wetting/drying front, a cell's depth can sit
    at a near-zero residual (e.g. 0.001m) while it still carries a tiny
    real leftover momentum from the previous timestep, and velocity =
    momentum/depth spikes numerically as depth approaches zero. The real
    DEFRA hazard rating elsewhere in this file is naturally immune to
    this (its own formula multiplies speed BY depth, so a near-zero depth
    already suppresses a spurious speed spike's contribution) -- a plain
    velocity report has no such built-in protection, so this function
    adds its own: a cell's speed only counts toward its own running
    maximum, and toward the real domain-wide max/mean, at a timestep
    where THAT SAME timestep's depth is genuinely above dry_thresh, not
    merely at some other point in the run."""
    wd_files = sorted(glob.glob(os.path.join(results_dir, "res-*.wd")))
    vx_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vx")))
    vy_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vy")))
    if not wd_files or len(vx_files) != len(wd_files) or len(vy_files) != len(wd_files):
        return None, None, None

    max_speed = None
    valid_mask = None
    all_wet_speeds = []
    for wd_fp, vx_fp, vy_fp in zip(wd_files, vx_files, vy_files):
        _, depth = read_ascii(wd_fp)
        _, vx_face = read_ascii(vx_fp)
        _, vy_face = read_ascii(vy_fp)
        if valid_mask is None:
            valid_mask = depth != nodata
            max_speed = np.zeros_like(depth)
        depth_c = np.where(depth == nodata, 0.0, depth)
        vx_face = np.where(vx_face == nodata, 0.0, vx_face)
        vy_face = np.where(vy_face == nodata, 0.0, vy_face)
        if vx_face.shape == depth_c.shape and vy_face.shape == depth_c.shape:
            vx_c, vy_c = vx_face, vy_face  # DG2: already cell-centered
        elif vx_face.shape[1] == depth_c.shape[1] + 1 and vy_face.shape[0] == depth_c.shape[0] + 1:
            vx_c = 0.5 * (vx_face[:, :-1] + vx_face[:, 1:])  # ACC: staggered, de-stagger
            vy_c = 0.5 * (vy_face[:-1, :] + vy_face[1:, :])
        else:
            return None, None, None  # unexpected grid shape -- don't guess, just skip

        speed = np.sqrt(vx_c ** 2 + vy_c ** 2)
        wet_now = depth_c > dry_thresh
        # Only let a cell's speed at THIS timestep count toward its own
        # running max (and toward the real domain-wide scalars below) when
        # THIS SAME timestep's depth is genuinely above dry_thresh -- see
        # this function's own docstring for the real wetting-front
        # numerical artifact this specifically guards against.
        max_speed = np.where(wet_now, np.maximum(max_speed, speed), max_speed)
        if wet_now.any():
            all_wet_speeds.append(speed[wet_now])

    max_speed[~valid_mask] = nodata
    wet_speeds = np.concatenate(all_wet_speeds) if all_wet_speeds else np.array([])
    real_max_ms = float(wet_speeds.max()) if wet_speeds.size else 0.0
    real_mean_ms = float(wet_speeds.mean()) if wet_speeds.size else 0.0
    return max_speed, real_max_ms, real_mean_ms


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
    p.add_argument("--enable-arf", action="store_true",
                    help="Apply the real Areal Reduction Factor (Keers & Wescott 1977 / FSR-FEH) to "
                         "convert --rain-mm-hr from a POINT design depth to the correct AREA-AVERAGE "
                         "depth for this AOI's own real area -- see storm_utils.areal_reduction_factor.")
    p.add_argument("--rain-weights-file", default=None,
                    help="Optional ARC ASCII raster (same grid as --dem) of a real per-cell rainfall "
                         "multiplier, mean ~1.0 over the valid AOI -- e.g. storm_utils.exponential_decay_weights "
                         "for a real storm-centered, exponentially-decaying spatial pattern. When given, "
                         "rainfall is written as a real per-timestep NetCDF grid (LISFLOOD-FP's own "
                         "DynamicRain mechanism) and run through the NetCDF-enabled binary "
                         "(LISFLOOD_BIN_NETCDF) instead of a spatially-uniform .rain file.")
    p.add_argument("--storm-center-lon0", type=float, default=None,
                    help="Real MOVING storm center's start longitude (degrees) -- when given together "
                         "with --storm-center-lat0 and --dem-src-crs, rainfall is written via "
                         "storm_utils.write_rain_netcdf_moving: the exponential-decay spatial weight "
                         "field is recomputed at EVERY NetCDF time slice using a center that has "
                         "translated from this start point at --storm-velocity-lon-per-hr/"
                         "--storm-velocity-lat-per-hr, instead of one FIXED center for the whole event "
                         "(--rain-weights-file). Mutually exclusive with --rain-weights-file (if both "
                         "given, --rain-weights-file's static field takes priority).")
    p.add_argument("--storm-center-lat0", type=float, default=None, help="See --storm-center-lon0.")
    p.add_argument("--storm-velocity-lon-per-hr", type=float, default=0.0,
                    help="Real (or model-predicted) storm center velocity, degrees longitude/hour -- see "
                         "--storm-center-lon0. 0.0 (default) keeps the center fixed at the start point.")
    p.add_argument("--storm-velocity-lat-per-hr", type=float, default=0.0,
                    help="Real (or model-predicted) storm center velocity, degrees latitude/hour -- see "
                         "--storm-center-lon0.")
    p.add_argument("--storm-decay-length-m", type=float, default=None,
                    help="Optional override for the moving storm's real exponential-decay length "
                         "(metres) -- default is the AOI's own scale (see exponential_decay_weights).")
    p.add_argument("--dem-src-crs", default=None,
                    help="Projected CRS (e.g. 'EPSG:32642') the DEM's own ARC ASCII header coordinates "
                         "are in -- REQUIRED when using --storm-center-lon0/--storm-center-lat0, since "
                         "converting a real lon/lat storm center into the DEM's local metre grid needs "
                         "to know which projected CRS those metres are in (the ASCII header itself "
                         "carries no CRS information).")
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
    p.add_argument("--mint-hk", action="store_true", default=False,
                    help="Update max-depth/max-hazard/arrival-time stats only at the massint cadence "
                         "instead of every physics timestep (real speedup on huge domains, confirmed "
                         "wired into the CUDA ACC kernel) -- off by default: see write_par_file's own "
                         "docstring for why a coarse massint can miss a fast event's true peak.")
    p.add_argument("--initial-tstep-override", type=float, default=None,
                    help="Real diagnostic-only override of the initial timestep this app would "
                         "otherwise auto-select (see inflow_initial_tstep) -- used for the direct "
                         "steep-terrain instability bisection recorded in CLAUDE.md, not intended "
                         "for normal use.")
    p.add_argument("--depththresh", type=float, default=None,
                    help="LISFLOOD-FP's own DepthThresh (m) -- cells below this are treated as dry for "
                         "flux/stats purposes. Real, confirmed wired into the CUDA ACC kernel. Left at "
                         "LISFLOOD-FP's own built-in default when not given.")
    p.add_argument("--workdir", default=os.path.expanduser("~/flood/run_" + time.strftime("%Y%m%d_%H%M%S")))
    p.add_argument("--dry-thresh", type=float, default=0.05, help="Depth (m) above which a cell counts as 'flooded'")
    p.add_argument("--site-name", default="Synthetic test catchment")
    p.add_argument("--infiltration-mm-hr", type=float, default=0.0,
                    help="Uniform ground infiltration/loss rate, mm/hr (0 = fully impervious, all rain becomes runoff) "
                         "-- treated as the AMC-II (average) baseline; scaled by --amc")
    p.add_argument("--amc", type=float, default=50.0,
                    help="Antecedent Moisture Condition, continuous 0-100 (0=dry/AMC-I, 50=average/AMC-II, "
                         "100=wet-saturated/AMC-III) -- scales --infiltration-mm-hr (see amc_infiltration_multiplier)")
    p.add_argument("--boundary-json", default=None,
                    help="Path to a JSON file: {\"inflows\": [...], \"outflows\": [...]}. Replaces the "
                         "app's original single-inflow-edge design with any number of real, "
                         "independently-sited inflows/outflows -- see choose_solver_for_terrain and "
                         "write_bci_file's own docstrings. Each inflow: {edge (N/E/S/W -- resolved from "
                         "any real 360-degree lon/lat crossing point by the caller, since LISFLOOD-FP's "
                         "own .bci format is inherently edge-based), pos_m (DEM-local metres along that "
                         "edge), width_m, peak_m3s, duration_hr (0 = constant QFIX), rise_frac (0.5 = "
                         "ordinary flood, ~0.1 = GLOF-shaped), name}. Each outflow: {edge, pos_m, "
                         "width_m, name}. Omit entirely for the original pluvial-only / whole-edge-outlet "
                         "behaviour (--outlet-edges still applies in that case).")
    p.add_argument("--coastal-hvar-json", default=None,
                    help="Path to a JSON file describing a real time-varying coastal water-level "
                         "(HVAR) boundary: {\"edge\": N/E/S/W (the AOI's seaward edge), \"name\": str, "
                         "\"pos_m\"/\"width_m\" (optional -- default is the whole edge, appropriate for "
                         "an open-sea boundary), \"times_hr\": [...], \"levels_m\": [...] (real absolute "
                         "water-surface elevation, same vertical datum as the DEM -- 0 = mean sea level)}. "
                         "A real, direct-tested LISFLOOD-FP CUDA bug (see write_par_file's docstring, "
                         "same failure mode already found+fixed for QVAR river inflow) silently zeroes "
                         "any HVAR boundary under CUDA -- confirmed via a real CPU-vs-GPU res.mass Vol "
                         "comparison -- so any run using this flag automatically falls back to the CPU "
                         "build, same as a real river/GLOF boundary inflow does.")
    p.add_argument("--rain-series-json", default=None,
                    help="Path to a JSON file: {\"times_hr\": [...], \"rates_mm_hr\": [...], "
                         "\"label\": str}. Drives the physics with a REAL uploaded/observed rainfall "
                         "time series (e.g. a 15-minute rain-gauge export) instead of the synthetic "
                         "triangular design storm --rain-mm-hr/--duration-hr would build -- see "
                         "write_rain_file_from_series. --rain-mm-hr/--duration-hr are still required "
                         "by argparse but ignored for the actual rain file when this is given (pass 0/0 "
                         "as harmless placeholders); mutually exclusive with --rain-weights-file/"
                         "--storm-center-lon0 (a real point observation has no spatial pattern to "
                         "combine with a storm-centering field).")
    p.add_argument("--solver", default="auto", choices=["auto", "acc", "dg2"],
                    help="'auto' (default) picks ACC (fast, local-inertial) or DG2 (full shallow water, "
                         "for steep terrain) from the real DEM's own slope -- see choose_solver_for_terrain. "
                         "'acc'/'dg2' force that solver regardless of terrain.")
    args = p.parse_args()

    inflows_in, outflows_in = [], []
    if args.boundary_json:
        with open(args.boundary_json) as f:
            boundary_spec = json.load(f)
        inflows_in = boundary_spec.get("inflows", [])
        outflows_in = boundary_spec.get("outflows", [])

    coastal_hvar_in = None
    if args.coastal_hvar_json:
        with open(args.coastal_hvar_json) as f:
            coastal_hvar_in = json.load(f)

    rain_series_in = None
    if args.rain_series_json:
        with open(args.rain_series_json) as f:
            rain_series_in = json.load(f)

    # A real fluvial event (river rise/recession) can genuinely outlast the
    # rain event driving sim_hours's own default -- extend the simulated
    # window to cover whichever runs longer, so a real compound flood
    # (river + storm) doesn't get cut off mid-recession, and so the
    # inflow hydrograph's own times never exceed the simulation length
    # (which the .bdy time-series parser requires to stay strictly
    # increasing). With multiple inflows, the LONGEST of all of them
    # (plus rain) sets the requirement.
    for _inf in inflows_in:
        normalize_inflow_series(_inf)
    longest_inflow_hr = max([i.get("duration_hr", 0.0) for i in inflows_in], default=0.0)
    longest_driver_hr = max(args.duration_hr, longest_inflow_hr)
    if coastal_hvar_in:
        longest_driver_hr = max(longest_driver_hr, max(coastal_hvar_in["times_hr"]))
    if rain_series_in:
        # A real uploaded series' own last real timestamp sets the true
        # storm-driver length -- --duration-hr is just a required-by-
        # argparse placeholder (0) in this mode, not a real signal.
        longest_driver_hr = max(longest_driver_hr, max(rain_series_in["times_hr"], default=0.0))
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

    nodata = header.get("nodata_value", -9999.0)
    if args.solver == "auto":
        resolved_solver, solver_reason = choose_solver_for_terrain(
            dem, header["cellsize"], nodata=nodata,
            has_boundary_inflow=(len(inflows_in) > 0 or coastal_hvar_in is not None))
    else:
        resolved_solver, solver_reason = args.solver, f"explicitly requested via --solver {args.solver}"
    print(f"[{MODEL_NAME}] Solver: {resolved_solver.upper()} -- {solver_reason}")
    if resolved_solver == "dg2":
        write_dg2_dem_slopes(os.path.join(args.workdir, dem_name), header, dem, nodata=nodata)

    manning_name = None
    if args.manning_file:
        # LISFLOOD-FP runs with workdir as its cwd (run_lisflood), so every
        # filename it's given must be relative to there -- same treatment
        # already applied to --dem above.
        manning_name = os.path.basename(args.manning_file)
        if os.path.abspath(args.manning_file) != os.path.abspath(os.path.join(args.workdir, manning_name)):
            shutil.copy(args.manning_file, os.path.join(args.workdir, manning_name))

    # Real Areal Reduction Factor: converts a POINT design rainfall depth
    # into the correct AREA-AVERAGE depth for this AOI's own real area --
    # a pure scalar correction, no solver/NetCDF involvement at all (this
    # app's rain input was already just a single scalar time series, so
    # ARF needs nothing more than adjusting the one number fed into it).
    valid_area_mask = dem != nodata
    area_km2 = float(valid_area_mask.sum()) * (header["cellsize"] ** 2) / 1.0e6
    arf = storm_utils.areal_reduction_factor(area_km2, args.duration_hr) if args.enable_arf else 1.0
    effective_rain_mm_hr = args.rain_mm_hr * arf
    if args.enable_arf:
        print(f"[{MODEL_NAME}] ARF (Keers & Wescott 1977/FSR-FEH): area={area_km2:.3f} km^2, "
              f"duration={args.duration_hr}h -> ARF={arf:.3f} -> "
              f"{args.rain_mm_hr:.1f} mm/hr point -> {effective_rain_mm_hr:.2f} mm/hr areal-average")

    has_moving_center = args.storm_center_lon0 is not None and args.storm_center_lat0 is not None
    use_netcdf_rain = bool(args.rain_weights_file) or has_moving_center
    rain_name = "storm.rain"
    storm_motion_desc = None
    if args.rain_weights_file:
        # Real per-cell, per-timestep rainfall (FIXED storm center,
        # exponential decay) -- see storm_utils.write_rain_netcdf and
        # LISFLOOD_BIN_NETCDF's own comment for why a separate binary.
        weights_header, weights = read_ascii(args.rain_weights_file)
        rain_name = "storm.nc"
        rain_pts = storm_utils.write_rain_netcdf(
            os.path.join(args.workdir, rain_name), header, weights,
            effective_rain_mm_hr, args.duration_hr, sim_hours)
        print(f"[{MODEL_NAME}] Storm-centered spatial rainfall: real per-cell NetCDF grid "
              f"(weights {weights[valid_area_mask].min():.2f}-{weights[valid_area_mask].max():.2f}x "
              f"the areal-average, mean 1.0x)")
    elif has_moving_center:
        # Real MOVING storm center -- see storm_utils.write_rain_netcdf_moving and
        # --storm-center-lon0's own help text.
        if not args.dem_src_crs:
            raise SystemExit("--dem-src-crs is required when using --storm-center-lon0/"
                              "--storm-center-lat0 (moving storm center) -- the DEM's ARC ASCII "
                              "header has no CRS of its own to convert a real lon/lat center into "
                              "local metres with.")
        rain_name = "storm.nc"
        rain_pts, decay_len_used = storm_utils.write_rain_netcdf_moving(
            os.path.join(args.workdir, rain_name), header, args.dem_src_crs, valid_area_mask,
            args.storm_center_lon0, args.storm_center_lat0,
            args.storm_velocity_lon_per_hr, args.storm_velocity_lat_per_hr,
            effective_rain_mm_hr, args.duration_hr, sim_hours,
            decay_length_m=args.storm_decay_length_m)
        speed_kmhr = float(np.hypot(args.storm_velocity_lon_per_hr * 111.32,
                                     args.storm_velocity_lat_per_hr * 111.32))
        storm_motion_desc = (
            f"Moving storm center from ({args.storm_center_lon0:.5f},{args.storm_center_lat0:.5f}) "
            f"at ({args.storm_velocity_lon_per_hr:.4f},{args.storm_velocity_lat_per_hr:.4f}) deg/hr "
            f"(~{speed_kmhr:.1f} km/hr, decay length {decay_len_used:.0f}m)")
        print(f"[{MODEL_NAME}] {storm_motion_desc}")
    elif rain_series_in:
        rain_pts = write_rain_file_from_series(
            os.path.join(args.workdir, rain_name),
            rain_series_in["times_hr"], rain_series_in["rates_mm_hr"], sim_hours)
        print(f"[{MODEL_NAME}] Real uploaded rainfall series"
              f"{' (' + rain_series_in['label'] + ')' if rain_series_in.get('label') else ''}: "
              f"{len(rain_series_in['times_hr'])} real observed points over "
              f"{max(rain_series_in['times_hr'], default=0.0):.2f}h")
    else:
        rain_pts = write_rain_file(os.path.join(args.workdir, rain_name), effective_rain_mm_hr,
                                    args.duration_hr, sim_hours)

    # Real multi-inflow, multi-outflow boundary conditions (see
    # write_bci_file's own docstring for the full reasoning). Each inflow/
    # outflow in the boundary JSON already carries a resolved edge + a
    # DEM-local pos_m (the caller -- fis_app.py -- resolves any real
    # 360-degree lon/lat crossing point down to one of LISFLOOD-FP's 4
    # edges plus a position, since the .bci format itself is inherently
    # edge-based). Here, each is clamped to its own edge's real span and,
    # for inflows with a real duration, gets its own uniquely-named .bdy
    # hydrograph file (multiple real rivers/GLOFs can be simulated at once,
    # each with its own real shape).
    ncols, nrows, cs = header["ncols"], header["nrows"], header["cellsize"]

    def edge_span_m(edge):
        return (ncols * cs) if edge in ("N", "S") else (nrows * cs)

    resolved_inflows = []
    for idx, inflow in enumerate(inflows_in):
        edge = inflow["edge"]
        span = edge_span_m(edge)
        half_w = max(inflow.get("width_m", 30.0), cs) / 2.0
        pos_m = inflow.get("pos_m")
        if pos_m is None:
            start_m, end_m = 0.0, span  # no real crossing point -- smear across the whole edge (original behaviour)
        else:
            # Clamp to the edge's own extent HERE (mirroring write_bci_file's
            # own clamp) before computing the real applied width -- a
            # crossing detected near the edge's own corner previously kept
            # the un-clamped width baked into the .bdy flux while
            # write_bci_file silently applied a narrower actual segment,
            # under-delivering the real requested discharge.
            start_m = max(0.0, min(span, pos_m - half_w))
            end_m = max(0.0, min(span, pos_m + half_w))
            # The solver applies the boundary on WHOLE cells, so the flux per metre must be computed over the
            # width of those cells, not the requested width (a 40 m channel on a 29 m grid delivered 2 cells =
            # 58 m = 1.46x the requested discharge). Snap to cell edges, at least one cell.
            ncell_w = max(1, int(round((end_m - start_m) / cs)))
            start_m = max(0.0, min(span - ncell_w * cs, round(start_m / cs) * cs))
            end_m = min(span, start_m + ncell_w * cs)
        applied_width_m = max(end_m - start_m, 1e-6)

        bdy_ref_name = None
        duration_hr = inflow.get("duration_hr", 0.0)
        if duration_hr > 0:
            bdy_ref_name = f"river{idx + 1}"
        resolved_inflows.append({
            "edge": edge, "start_m": start_m, "end_m": end_m,
            "peak_m3s": inflow["peak_m3s"], "bdy_ref_name": bdy_ref_name, "duration_hr": duration_hr,
            "rise_frac": inflow.get("rise_frac", 0.5), "series": inflow.get("series"),
            "applied_width_m": applied_width_m, "name": inflow.get("name", f"inflow{idx + 1}"),
            # constant inflow is written as a flat QVAR (CUDA ACC has no QFIX case)
            "const_bdy_name": None if bdy_ref_name else f"river{idx + 1}",
        })

    resolved_coastal_hvar = None
    if coastal_hvar_in:
        edge = coastal_hvar_in["edge"]
        span = edge_span_m(edge)
        pos_m = coastal_hvar_in.get("pos_m")
        if pos_m is None:
            # No specific crossing point -- an open-sea boundary genuinely
            # spans the whole seaward edge (unlike a river, which enters at
            # one real point), so this is the correct default, not just a
            # fallback.
            start_m, end_m = 0.0, span
        else:
            half_w = max(coastal_hvar_in.get("width_m", span), span * 0.001) / 2.0
            start_m = max(0.0, min(span, pos_m - half_w))
            end_m = max(0.0, min(span, pos_m + half_w))
        hvar_name = coastal_hvar_in.get("name", "coast")
        resolved_coastal_hvar = {"edge": edge, "start_m": start_m, "end_m": end_m, "name": hvar_name}

    bdy_name = None
    blocks = [(bdy_block_series(i["bdy_ref_name"], i["series"]["times_hr"], i["series"]["q_m3s"],
                                sim_hours, i["applied_width_m"]) if i.get("series")
               else bdy_block(i["bdy_ref_name"], i["peak_m3s"], i["duration_hr"], sim_hours,
                              i["applied_width_m"], rise_frac=i["rise_frac"]))
              for i in resolved_inflows if i["bdy_ref_name"]]
    blocks += [bdy_block_series(i["const_bdy_name"], [0.0], [i["peak_m3s"]], sim_hours, i["applied_width_m"])
               for i in resolved_inflows if i["const_bdy_name"]]
    if coastal_hvar_in:
        blocks.append(bdy_block_hvar(resolved_coastal_hvar["name"],
                                      coastal_hvar_in["times_hr"], coastal_hvar_in["levels_m"]))
    if blocks:
        # One shared .bdy file: LISFLOOD-FP's LoadTimeSeries keeps reading
        # named blocks until every QVAR/HVAR name referenced in the .bci
        # file is found, regardless of which real boundary type each one
        # is -- a river QVAR block and a coastal HVAR block can coexist in
        # the same file exactly like multiple QVAR blocks already do.
        bdy_name = "boundaries.bdy"
        write_bdy_multi(os.path.join(args.workdir, bdy_name), blocks)

    resolved_outflows = []
    for idx, outflow in enumerate(outflows_in):
        edge = outflow["edge"]
        span = edge_span_m(edge)
        half_w = max(outflow.get("width_m", 30.0), cs) / 2.0
        pos_m = outflow.get("pos_m")
        if pos_m is None:
            start_m, end_m = 0.0, span
        else:
            start_m = max(0.0, min(span, pos_m - half_w))
            end_m = max(0.0, min(span, pos_m + half_w))
        resolved_outflows.append({"edge": edge, "start_m": start_m, "end_m": end_m,
                                   "name": outflow.get("name", f"outflow{idx + 1}")})

    bci_inflow_specs = [{"edge": i["edge"], "start_m": i["start_m"], "end_m": i["end_m"],
                          "peak_m3s": i["peak_m3s"], "bdy_name": i["bdy_ref_name"] or i["const_bdy_name"]}
                         for i in resolved_inflows]
    if resolved_coastal_hvar:
        bci_inflow_specs.append({"edge": resolved_coastal_hvar["edge"], "start_m": resolved_coastal_hvar["start_m"],
                                  "end_m": resolved_coastal_hvar["end_m"], "hvar_name": resolved_coastal_hvar["name"]})

    bci_name = "site.bci"
    write_bci_file(os.path.join(args.workdir, bci_name), header, outlet_edges,
                    inflows=bci_inflow_specs, outflows=resolved_outflows)

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
    has_river_boundary = bool(resolved_inflows)
    has_any_boundary = has_river_boundary or (resolved_coastal_hvar is not None)
    # A coastal HVAR forcing rises smoothly over hours (real GTSR-shaped
    # surge, not a sudden switch-on flux like a river QFIX/QVAR spike), so
    # it's a gentler boundary condition than the river/GLOF case the 0.2s
    # mitigation was originally found for. This WAS directly tested (not
    # assumed) before trusting it: a real coastal-HVAR-only run on the same
    # real Karachi-coast DEM used to find/confirm the CUDA HVAR bug was
    # re-run at initial_tstep 0.2s (baseline), 2.0s, and 5.0s (the plain
    # pluvial default) -- all three produced closely matching mass-balance
    # trajectories (e.g. Vol ~31.0-31.4 million m^3 at the same real
    # simulated time across all three, no blow-up, Qerror/Verror ~0.0
    # throughout) and 5.0s's own adaptive CFL stepping self-throttled down
    # to ~3.1-3.5s on its own once the surge was active, rather than
    # staying pinned at the full 5.0s -- i.e. the solver's own adaptive
    # timestepping already keeps this case safe. A real river/GLOF QFIX/
    # QVAR spike was NOT re-tested here and keeps the original conservative
    # 0.2s -- that failure mode (a sudden edge-wide flux switching on
    # instantly) is a fundamentally different, faster-onset transient than
    # a multi-hour real GTSR rise, so this optimization is scoped to a
    # coastal-HVAR-ONLY boundary (no river present in the same run).
    inflow_initial_tstep = 5.0 if (resolved_coastal_hvar is not None and not has_river_boundary) else (
        0.2 if has_any_boundary else 5.0)
    # Real, direct-bisection finding (steep-terrain instability discovered
    # on a real Gilgit-Baltistan basin run, mean slope 72%/max 852% --
    # ACC forced by the DG2_CUDA_MAX_CELLS override on that run, which
    # never gets to see choose_solver_for_terrain's own slope-based DG2
    # preference): the ORIGINAL "rainfall arrives gradually, no boundary
    # spike" justification for keeping initial_tstep=5.0 on rain-only runs
    # was only ever validated on gentle flood-plain terrain. A real,
    # controlled A/B bisection (fixed 300x300 domain, real ACC/CUDA,
    # identical 40mm/hr/2h storm, varying ONLY slope and initial_tstep)
    # found max depth grew unphysically with slope alone at the 5.0s
    # default (18m/247m/387m/559m at 15/50/100/200% slope) -- a real
    # shallow-water blow-up, not a slope-threshold or mass-balance-tuning
    # issue. Re-tested at initial_tstep=1.0s specifically (a cheaper,
    # GRADUATED alternative to blanket-applying the existing 0.2s
    # boundary-inflow mitigation everywhere): fully converged to the same
    # real, physically consistent answer as 0.2s at 20%, 30%, AND 100%
    # slope (confirmed by direct comparison, not assumed) -- only at the
    # most extreme tested case (200%) did 1.0s fall short of FULL
    # convergence (0.86m vs 0.56m), while still being a real ~650x
    # improvement over 5.0s's own 559m there. Since the real GB basin
    # that surfaced this (72% mean slope) sits solidly inside the
    # "confirmed fully converges at 1.0s" range, a two-tier design is
    # used rather than a flat jump straight to 0.2s everywhere: gentle
    # terrain (<=10%, this app's own existing DG2_SLOPE_THRESHOLD_PCT)
    # keeps the original 5.0s completely unchanged; anything steeper gets
    # 1.0s (a real ~5x cost versus the original default, not ~25x) --
    # cheaper and just as effective as 0.2s across the whole real range
    # this app is actually used on, including the moderately-hilly case
    # (e.g. the real whole-Islamabad run's own 13.8% mean slope) that a
    # flat 0.2s-everywhere design would have hit hardest for the least
    # real benefit. The pre-existing 0.2s value stays reserved for real
    # boundary-inflow spikes above (a fundamentally different, faster-
    # onset transient, not re-tested here) -- this graduated tier is
    # specifically for pluvial-only runs on steep terrain.
    STEEP_TERRAIN_INITIAL_TSTEP = 1.0
    if inflow_initial_tstep == 5.0:
        mean_slope_check, _ = mean_terrain_slope_pct(dem, header["cellsize"], nodata=nodata)
        if mean_slope_check is not None and mean_slope_check > DG2_SLOPE_THRESHOLD_PCT:
            inflow_initial_tstep = STEEP_TERRAIN_INITIAL_TSTEP
    if args.initial_tstep_override is not None:
        inflow_initial_tstep = args.initial_tstep_override
    # GPU is safe for a river/GLOF inflow only if every inflow cell is real terrain (see
    # write_par_file). HVAR (coastal) runs on the GPU since the cuda_acc_solver.cu stage-gate patch.
    cuda_unsafe_reason = None
    for i in resolved_inflows:
        if inflow_segment_touches_nodata(dem, header, nodata, i["edge"], i["start_m"], i["end_m"]):
            cuda_unsafe_reason = (f"inflow '{i['name']}' on the {i['edge']} edge covers NODATA cells "
                                  f"(CUDA would drain/destabilise it)")
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
        solver=resolved_solver, dynamic_rain=use_netcdf_rain,
        has_boundary_inflow=has_any_boundary,
        mint_hk=args.mint_hk, depththresh=args.depththresh,
        cuda_unsafe_boundary=(cuda_unsafe_reason is not None),
    )
    if has_any_boundary and cuda_unsafe_reason is None and os.environ.get("SAILAB_FORCE_CPU") != "1":
        print(f"[{MODEL_NAME}] Boundary inflow on valid terrain -- running on the GPU (CUDA) build.")
    elif cuda_unsafe_reason:
        print(f"[{MODEL_NAME}] NOTE: running on CPU, not CUDA: {cuda_unsafe_reason}.")

    print(f"[{MODEL_NAME}] Inputs written to {args.workdir}")
    print(f"[{MODEL_NAME}] Storm: peak {args.rain_mm_hr} mm/hr, duration {args.duration_hr} h, "
          f"sim length {sim_hours} h, friction n={args.friction}, outlets={sorted(outlet_edges)}")
    if args.infiltration_mm_hr > 0:
        print(f"[{MODEL_NAME}] Infiltration: {args.infiltration_mm_hr} mm/hr baseline (AMC-II) "
              f"x {amc_mult:.2f} (AMC {amc_label(args.amc)}) = "
              f"{effective_infiltration_mm_hr:.2f} mm/hr effective uniform loss")
    for i in resolved_inflows:
        loc_desc = f"concentrated over a {i['applied_width_m']:.1f}m channel-width segment on {i['edge']} edge"
        if i["bdy_ref_name"]:
            print(f"[{MODEL_NAME}] River/GLOF inflow '{i['name']}': real hydrograph peaking at {i['peak_m3s']} "
                  f"m^3/s over {i['duration_hr']} h, {loc_desc} (compound fluvial+pluvial)")
        else:
            print(f"[{MODEL_NAME}] River/GLOF inflow '{i['name']}': {i['peak_m3s']} m^3/s (constant), {loc_desc} "
                  f"(compound fluvial+pluvial)")
    for o in resolved_outflows:
        print(f"[{MODEL_NAME}] Real outflow '{o['name']}' on {o['edge']} edge, "
              f"{o['end_m'] - o['start_m']:.1f}m segment")
    print(f"[{MODEL_NAME}] Running LISFLOOD-FP ({resolved_solver.upper()} solver, DEFRA hazard rating)...")

    proc, elapsed = run_lisflood(args.workdir, par_name, use_netcdf_binary=use_netcdf_rain)
    if proc.returncode != 0:
        print(f"[{MODEL_NAME}] LISFLOOD-FP FAILED")
        print(proc.stdout[-4000:]); print(proc.stderr[-4000:])
        sys.exit(1)

    print(f"[{MODEL_NAME}] Run finished in {elapsed:.1f}s wall-clock")

    results_dir = os.path.join(args.workdir, "results")
    mass_header, mass_rows = parse_mass_file(os.path.join(results_dir, "res.mass"))
    max_header, max_depth = read_ascii(os.path.join(results_dir, "res.max"))
    nodata = max_header.get("nodata_value", -9999)

    # Real, empirically-calibrated ACC depth bias correction (see
    # acc_depth_correction_factor's own docstring for the full real
    # benchmark this is fit from, and its stated limitations). Applied
    # only when ACC was actually used AND this terrain is steep enough
    # that ACC is a known-compromised fallback (the same real
    # DG2_SLOPE_THRESHOLD_PCT this app already uses to prefer DG2) --
    # never on gentle terrain, where ACC's own real accuracy is already
    # good and this fit was never calibrated for. Divides the REAL,
    # per-cell max-depth grid (not just a reported summary scalar), since
    # "the depth number" downstream (hazard, damage, building loss) all
    # read from this same array.
    acc_correction_factor = 1.0
    acc_correction_slope_pct = None
    if resolved_solver == "acc":
        acc_mean_slope, _ = mean_terrain_slope_pct(dem, header["cellsize"], nodata=nodata)
        acc_correction_factor = acc_depth_correction_factor(acc_mean_slope)
        if acc_correction_factor != 1.0:
            acc_correction_slope_pct = acc_mean_slope
            valid_depth = max_depth != nodata
            max_depth = np.where(valid_depth, max_depth / acc_correction_factor, max_depth)
            print(f"[{MODEL_NAME}] Real ACC depth bias correction applied: mean slope "
                  f"{acc_mean_slope:.1f}% -> dividing max depth by {acc_correction_factor:.3f} "
                  f"(empirical fit vs. real DG2 benchmark, magnitude only -- see "
                  f"acc_depth_correction_factor's own docstring for the real calibration data "
                  f"and honest limitations)")

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

    # Real peak flow velocity, reported as its own headline figure (not
    # just folded into the DEFRA hazard rating) -- same real per-timestep
    # Vx/Vy data, same de-staggering logic as the hazard fallback above.
    _, real_max_velocity_ms, real_mean_velocity_ms = compute_max_velocity_from_timesteps(
        results_dir, nodata, dry_thresh=args.dry_thresh)

    summary = summarize(max_depth, max_haz, inittm, nodata, header["cellsize"], args.dry_thresh)
    summary["max_velocity_ms"] = round(real_max_velocity_ms, 3) if real_max_velocity_ms is not None else None
    summary["mean_velocity_ms_in_flooded_area"] = (
        round(real_mean_velocity_ms, 3) if real_mean_velocity_ms is not None else None)
    if acc_correction_slope_pct is not None:
        summary["acc_depth_correction_applied"] = True
        summary["acc_depth_correction_factor"] = round(acc_correction_factor, 3)
        summary["acc_depth_correction_mean_slope_pct"] = round(acc_correction_slope_pct, 1)
        summary["acc_depth_correction_note"] = (
            "Real ACC depth over-estimate on steep terrain corrected by an empirically-fit "
            "factor calibrated against real DG2 benchmark runs (magnitude only -- does not "
            "correct real routing/flow-path errors from ACC's own missing momentum terms; "
            "narrow calibration, see flood_model.py's acc_depth_correction_factor docstring "
            "for the real data and limitations)."
        )
    else:
        summary["acc_depth_correction_applied"] = False
    summary["final_mass_error_m3"] = float(mass_rows[-1, 10])
    # UNITS (measured, Oct 2026 -- this corrects an earlier "x1000" here): the
    # LISFLOOD-FP manual (section 5.2.1, col. 12) describes this column as 10^3 m^3,
    # but THIS 8.2 build's CPU path writes plain m^3. Measured on a real 113 km^2 CPU
    # run (5.28 mm/hr triangular x 48 h = 126.7 mm): the column's final value was
    # 13,907,553 against an analytic 14.3e6 m^3 (and the same file's "Vol" column,
    # 14.6e6, is in the same units); multiplying by 1000 gave 1.39e10 m^3 -- more
    # water than the domain could hold -- and every CPU-path run (all river/GLOF/
    # coastal runs and SAILAB_FORCE_CPU) reported rainfall 1000x too high, which also
    # made the mass-balance sanity checks (error < 2% of rain) vacuous. Read as-is.
    reported_rain_vol_m3 = float(mass_rows[-1, 11])
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
    # Same real analytical calculation as the rainfall volume above, for
    # the same reason (res.mass's own Qin column reads 0.0 under CUDA --
    # confirmed the inflow WAS applied physically regardless, see
    # CLAUDE.md), summed over every real inflow now that more than one
    # can be active at once. Exact for the constant-QFIX case (m^3/s x
    # duration); exact for the real QVAR hydrograph case too (triangle
    # area -- independent of rise_frac, since a triangle's area only
    # depends on its base and height, not where the peak sits).
    total_inflow_m3 = 0.0
    for i in resolved_inflows:
        if i["bdy_ref_name"] and i.get("series"):
            _t, _q = i["series"]["times_hr"], i["series"]["q_m3s"]
            _area = sum(0.5 * (_q[k] + _q[k + 1]) * (_t[k + 1] - _t[k]) for k in range(len(_t) - 1))
            if _t[0] > 0:
                _area += _q[0] * _t[0]
            _area += _q[-1] * max(0.0, sim_hours - _t[-1])  # last value held to the end of the run
            total_inflow_m3 += _area * 3600.0
        elif i["bdy_ref_name"]:
            total_inflow_m3 += 0.5 * i["peak_m3s"] * i["duration_hr"] * 3600.0
        else:
            total_inflow_m3 += i["peak_m3s"] * sim_hours * 3600.0
    summary["cumulative_river_inflow_m3"] = total_inflow_m3
    summary["inflows"] = [{"name": i["name"], "edge": i["edge"], "peak_m3s": i["peak_m3s"],
                            "duration_hr": i["duration_hr"], "width_m": i["applied_width_m"]}
                           for i in resolved_inflows]
    summary["outflows"] = [{"name": o["name"], "edge": o["edge"], "width_m": o["end_m"] - o["start_m"]}
                            for o in resolved_outflows]
    if resolved_coastal_hvar:
        # No analytical volume figure here (unlike the Q-boundary case
        # above): an H (water-level) boundary's real inflow/outflow volume
        # genuinely depends on the solved head difference against the
        # domain's own terrain at every timestep, not a fixed flux -- so
        # reporting a fabricated number would be worse than reporting none.
        summary["coastal_hvar"] = {
            "name": resolved_coastal_hvar["name"], "edge": resolved_coastal_hvar["edge"],
            "peak_water_level_m": max(coastal_hvar_in["levels_m"]),
            "times_hr": coastal_hvar_in["times_hr"], "levels_m": coastal_hvar_in["levels_m"],
        }
    if rain_series_in:
        rates = rain_series_in["rates_mm_hr"]
        summary["rainfall_series"] = {
            "label": rain_series_in.get("label"),
            "n_points": len(rates),
            "peak_mm_hr": max(rates, default=0.0),
            # real cumulative total, as computed by the caller from each
            # interval's own real duration (fis_app.py's CSV parser) --
            # not re-derived here from an assumed fixed interval, since
            # this JSON's own real intervals could be irregular.
            "total_mm": rain_series_in.get("total_mm"),
            "span_hr": max(rain_series_in["times_hr"], default=0.0),
        }
    summary["solver_used"] = resolved_solver
    summary["solver_selection_reason"] = solver_reason
    summary["wall_clock_seconds"] = elapsed
    summary["sim_hours"] = sim_hours
    summary["amc"] = args.amc
    summary["amc_multiplier"] = amc_mult
    summary["infiltration_mm_hr_baseline"] = args.infiltration_mm_hr
    summary["infiltration_mm_hr_effective"] = effective_infiltration_mm_hr
    if storm_motion_desc:
        summary["storm_motion"] = storm_motion_desc

    with open(os.path.join(args.workdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print("\n================ FLOOD SUMMARY ================")
    print(f"Domain area:            {summary['domain_area_km2']:.4f} km^2")
    print(f"Flooded area (max):     {summary['flooded_area_km2']:.4f} km^2 "
          f"({summary['flooded_pct_of_domain']:.1f}% of domain)")
    print(f"Max depth:              {summary['max_depth_m']:.2f} m")
    print(f"Mean depth (flooded):   {summary['mean_depth_m_in_flooded_area']:.2f} m")
    if summary.get("max_velocity_ms") is not None:
        print(f"Max flow velocity:      {summary['max_velocity_ms']:.2f} m/s")
        print(f"Mean velocity (flooded):{summary['mean_velocity_ms_in_flooded_area']:.2f} m/s")
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
        "River/GLOF inflow(s)": (
            "; ".join(
                (f"'{i['name']}': real hydrograph, peak {i['peak_m3s']} m^3/s over {i['duration_hr']} h on "
                 f"{i['edge']} edge" if i["bdy_ref_name"] else
                 f"'{i['name']}': {i['peak_m3s']} m^3/s (constant) on {i['edge']} edge")
                for i in resolved_inflows
            ) or "none (pluvial-only)"
        ),
        "Real outflow(s)": (
            "; ".join(f"'{o['name']}' on {o['edge']} edge" for o in resolved_outflows)
            or "none (whole-edge outlet fallback only)"
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
