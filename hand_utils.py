"""
Tier 0 (static terrain-derived layers) + Tier 2 (discharge -> instant
inundation lookup) of the global-flood-prediction architecture in
`global-flood-prediction-research.md`.

Real technique, not invented: this is the same HAND (Height Above Nearest
Drainage) + synthetic-rating-curve methodology NOAA's own Office of Water
Prediction uses for its CONUS Flood Inundation Mapping service (Zheng et
al. 2018, "GeoFlood"), and the same mechanism the research brief's own
Tier-2 diagram describes -- "HAND/terrain-index lookup for fluvial, all
reaches globally". Computed per-AOI on demand here (a literal one-time
global precompute is the multi-TB/multi-week batch job the brief itself
says is out of solo-team reach -- see CLAUDE.md), but the *mechanism* is
the real, standard one: once HAND is computed for an area, turning a new
discharge forecast into a flood extent is a cheap array lookup, not a
physics re-run.

Pipeline:
1. `compute_hand` -- D8 flow direction + accumulation (pysheds) -> fills
   pits/depressions, resolves flats, extracts a stream network by
   thresholding flow accumulation, computes each cell's HAND (metres above
   its nearest downslope stream cell) and which stream cell that is.
2. `discharge_to_inundation` -- for a given discharge (m^3/s) arriving at
   the AOI's outlet, estimates each stream cell's local discharge share via
   its own flow-accumulation fraction of the outlet's total, solves
   Manning's equation for that cell's water depth in a synthetic trapezoidal
   channel sized by a real regional hydraulic-geometry scaling law
   (Leopold & Maddock 1953: channel width/depth scale with drainage area as
   a power law), then floods every cell whose HAND is below its nearest
   stream cell's stage. This is an approximation deliberately documented as
   such (no surveyed channel cross-sections exist for an arbitrary global
   AOI) -- Tier 3 (this app's existing real LISFLOOD-FP integration) is
   still the source of truth wherever the extra compute is worth it; this
   tier exists to make "instant, everywhere" possible at all.
"""
import os
import threading
import uuid
import pickle

import numpy as np

# pysheds 0.5 (last release) calls the removed np.in1d alias internally;
# numpy 2.x dropped it in favor of np.isin (same underlying function, just
# renamed) -- same class of numpy-2.x-vs-unmaintained-geo-library break
# already documented in CLAUDE.md for richdem. Shimming here rather than
# pinning numpy down app-wide, since every other module in this app already
# depends on numpy 2.x behavior.
if not hasattr(np, "in1d"):
    np.in1d = np.isin

from pysheds.grid import Grid

STREAM_ACCUM_THRESHOLD_CELLS = 500  # cells draining in before counting as a "stream"
MANNING_N_CHANNEL = 0.035  # natural channel, moderate roughness (Chow 1959)
HYDRAULIC_GEOM_A = 0.60   # width(m) = A * drainage_area(km^2)^B -- Leopold & Maddock 1953 mid-range
HYDRAULIC_GEOM_B = 0.50
CHANNEL_SIDE_SLOPE = 1.5   # trapezoidal side slope (run:rise), typical natural channel


_CACHEABLE_KEYS = (
    "dem", "hand", "accumulation", "stream_mask", "nearest_drainage_idx", "cellsize_m", "shape",
    "dem_raw", "filled",    # added for street-level ponding (pluvial_ponding_depth); older cache files lack them
)


def save_hand_result(hand_result, cache_path):
    """Caches the picklable parts of compute_hand's result (skips the
    pysheds `grid`/`affine`/`crs` objects, which discharge_to_inundation
    doesn't need and which aren't reliably picklable across pysheds
    versions) -- this is what lets repeat requests over the SAME drawn
    area (e.g. scrubbing the instant-flood discharge slider) skip the
    whole flow-direction/depression-fill/HAND recompute, not just the
    DEM crop."""
    payload = {k: hand_result[k] for k in _CACHEABLE_KEYS}
    tmp_path = cache_path + f".tmp{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex[:8]}"
    with open(tmp_path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp_path, cache_path)


def load_hand_result(cache_path):
    with open(cache_path, "rb") as f:
        return pickle.load(f)


def compute_hand(dem_path, accum_threshold=STREAM_ACCUM_THRESHOLD_CELLS):
    """Returns a dict with the real per-cell terrain-derived layers needed
    for Tier 0/2: hand (m), flow accumulation (cell count), the boolean
    stream mask used, each cell's nearest-drainage flat index, cell size
    (m, for converting accumulation cell-count to drainage area), and the
    DEM's own affine/crs for georeferencing results back out.
    """
    grid = Grid.from_raster(dem_path)
    dem = grid.read_raster(dem_path)

    pit_filled = grid.fill_pits(dem)
    flooded = grid.fill_depressions(pit_filled)
    inflated = grid.resolve_flats(flooded)

    fdir = grid.flowdir(inflated)
    acc = grid.accumulation(fdir)

    stream_mask = acc > accum_threshold
    if not np.any(stream_mask):
        # AOI too small / too flat for any cell to cross the threshold --
        # fall back to the single highest-accumulation cell so HAND still
        # resolves to *something* rather than raising.
        stream_mask = acc == acc.max()

    # compute_hand's return_index flag picks ONE of two different outputs
    # (height OR nearest-channel-cell index), not both -- two calls needed.
    hand = grid.compute_hand(fdir, inflated, stream_mask, return_index=False)
    nearest_idx = grid.compute_hand(fdir, inflated, stream_mask, return_index=True)

    cellsize_m = abs(grid.affine.a)
    return dict(
        grid=grid,
        dem=np.asarray(inflated),
        dem_raw=np.asarray(dem, dtype="float32"),
        filled=np.asarray(flooded, dtype="float32"),
        hand=np.asarray(hand),
        accumulation=np.asarray(acc),
        stream_mask=np.asarray(stream_mask),
        nearest_drainage_idx=np.asarray(nearest_idx),
        cellsize_m=cellsize_m,
        shape=dem.shape,
        affine=grid.affine,
        crs=grid.crs,
    )


def _manning_depth_for_discharge(q_m3s, width_m, side_slope, manning_n, slope, max_depth=30.0):
    """Solves Manning's equation for normal depth in a trapezoidal channel
    via bisection (monotonic in depth, so this is robust and fast -- no
    need for Newton's method / derivatives)."""
    if q_m3s <= 0 or slope <= 0:
        return 0.0
    slope = max(slope, 1e-4)  # a perfectly flat reach still needs a nonzero slope to solve

    def discharge_at_depth(d):
        if d <= 0:
            return 0.0
        area = (width_m + side_slope * d) * d
        wetted_perimeter = width_m + 2 * d * (1 + side_slope ** 2) ** 0.5
        if wetted_perimeter <= 0:
            return 0.0
        hydraulic_radius = area / wetted_perimeter
        return (1.0 / manning_n) * area * hydraulic_radius ** (2.0 / 3.0) * slope ** 0.5

    lo, hi = 0.0, max_depth
    if discharge_at_depth(hi) < q_m3s:
        return max_depth  # discharge exceeds what this synthetic channel can convey even at max_depth
    for _ in range(40):
        mid = (lo + hi) / 2
        if discharge_at_depth(mid) < q_m3s:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


DEFAULT_LIBRARY_LEVELS_M3S = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2500, 5000]


def build_inundation_library(hand_result, discharge_levels=DEFAULT_LIBRARY_LEVELS_M3S):
    """The research brief's own Tier 0 diagram line: "Precomputed
    inundation-extent library per discharge level (many discharge levels
    x HAND lookup = instant flood maps)". No such library exists as a
    downloadable dataset anywhere (this is always a derived product built
    from a HAND raster someone already has, not a distributed one -- see
    CLAUDE.md for what was actually checked) -- so this builds the real
    thing FOR this AOI, once its HAND is already computed/cached, and
    caches the result alongside it. Each level is one call to
    `discharge_to_inundation` (already millisecond-fast on a cached HAND
    result); computing all of them once and caching the summary stats is
    what lets a UI discharge slider interpolate between precomputed rows
    instantly client-side, instead of a fresh server round-trip (and a
    fresh Manning's-equation solve) per slider tick. Returns a list of
    dicts sorted by discharge, one per level -- summary stats only
    (area/max depth/wet cells), not full depth grids, so the payload stays
    small enough to ship to the browser in one response."""
    cellsize_m = hand_result["cellsize_m"]
    cell_area_km2 = (cellsize_m ** 2) / 1e6
    library = []
    for q in sorted(discharge_levels):
        depth_grid, _ = discharge_to_inundation(hand_result, q)
        wet_cells = int((depth_grid > 0.05).sum())
        library.append({
            "discharge_m3s": float(q),
            "flooded_area_km2": round(wet_cells * cell_area_km2, 4),
            "max_depth_m": round(float(depth_grid.max()), 3),
            "wet_cells": wet_cells,
        })
    return library


def discharge_to_inundation(hand_result, outlet_discharge_m3s):
    """Instant (no solver) discharge -> flood depth grid, using the HAND
    layers from `compute_hand`. Returns (depth_grid_m, stream_stage_m) --
    stream_stage_m is per-stream-cell for diagnostics/plotting.
    """
    hand = hand_result["hand"]
    acc = hand_result["accumulation"]
    stream_mask = hand_result["stream_mask"]
    nearest_idx = hand_result["nearest_drainage_idx"]
    cellsize_m = hand_result["cellsize_m"]
    dem = hand_result["dem"]
    nrows, ncols = hand_result["shape"]

    cell_area_km2 = (cellsize_m ** 2) / 1e6
    outlet_accum_cells = float(acc[stream_mask].max())
    outlet_drainage_area_km2 = outlet_accum_cells * cell_area_km2

    stream_rows, stream_cols = np.where(stream_mask)
    stream_flat_idx = stream_rows * ncols + stream_cols
    stage_by_flat_idx = {}

    # real local terrain slope per stream cell: (own elevation - min DEM
    # elevation nearby) / distance, using a 3-cell neighborhood along
    # accumulation gradient as a cheap real proxy (no reach delineation
    # available at this scope) -- floored to avoid a zero-slope solve.
    for r, c, flat_idx in zip(stream_rows, stream_cols, stream_flat_idx):
        cell_accum_km2 = max(acc[r, c] * cell_area_km2, cell_area_km2)
        q_local = outlet_discharge_m3s * (cell_accum_km2 / outlet_drainage_area_km2)
        width_m = max(HYDRAULIC_GEOM_A * (cell_accum_km2 ** HYDRAULIC_GEOM_B), 0.5)

        r0, r1 = max(r - 2, 0), min(r + 3, nrows)
        c0, c1 = max(c - 2, 0), min(c + 3, ncols)
        neighborhood = dem[r0:r1, c0:c1]
        local_slope = (dem[r, c] - float(neighborhood.min())) / (2.5 * cellsize_m)
        local_slope = max(local_slope, 1e-3)

        depth = _manning_depth_for_discharge(
            q_local, width_m, CHANNEL_SIDE_SLOPE, MANNING_N_CHANNEL, local_slope
        )
        # store depth-above-channel-bed (NOT absolute elevation) -- HAND
        # is itself already a relative height above this same bed, so the
        # two are directly comparable without ever touching absolute DEM
        # elevation again (standard GeoFlood/NWS HAND-inundation formulation).
        stage_by_flat_idx[flat_idx] = depth

    stage_lookup = np.zeros(nrows * ncols, dtype="float64")
    default_stage = float(np.median(list(stage_by_flat_idx.values()))) if stage_by_flat_idx else 0.0
    stage_lookup[:] = default_stage
    for flat_idx, stage in stage_by_flat_idx.items():
        stage_lookup[flat_idx] = stage

    nearest_flat_flat = nearest_idx.reshape(-1).astype("int64")
    nearest_flat_flat = np.clip(nearest_flat_flat, 0, nrows * ncols - 1)
    cell_channel_depth = stage_lookup[nearest_flat_flat].reshape(nrows, ncols)

    # a cell with no resolvable drainage path (e.g. an AOI-edge cell whose
    # flow direction points off the grid) gets HAND=nan from pysheds --
    # treated as "effectively unreachable / always dry" rather than
    # propagating nan through the whole array.
    hand_safe = np.where(np.isnan(hand), np.inf, hand)
    depth_grid = np.maximum(cell_channel_depth - hand_safe, 0.0)
    depth_grid = np.where(dem <= -9998, 0.0, depth_grid)

    return depth_grid, stage_by_flat_idx


PONDING_MIN_SINK_M = 0.02    # a depression shallower than this is noise, not a puddle


def pluvial_ponding_depth(hand_result, effective_rain_mm):
    """Water that collects in the terrain's own low spots when rain falls on every cell.

    Each depression (cells that depression-filling raised) receives the effective rain that falls on everything
    draining into it (flow accumulation at its outlet) and fills from its lowest cell up, to at most its spill
    level. Returns depth in metres on the HAND grid (0 elsewhere). Screening only: a real flood routes water
    across the surface while it rains, which this does not.
    """
    from scipy import ndimage
    raw, filled, acc = hand_result["dem_raw"], hand_result["filled"], hand_result["accumulation"]
    cell_area = float(hand_result["cellsize_m"]) ** 2
    valid = raw > -9000
    sink = ((filled - raw) > PONDING_MIN_SINK_M) & valid
    depth = np.zeros(raw.shape, dtype="float32")
    if effective_rain_mm <= 0 or not sink.any():
        return depth
    lab, n = ndimage.label(sink, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return depth
    catch_cells = np.atleast_1d(ndimage.maximum(acc, lab, index=np.arange(1, n + 1)))
    slices = ndimage.find_objects(lab)
    rain_m = effective_rain_mm / 1000.0
    for i, sl in enumerate(slices):
        if sl is None:
            continue
        m = lab[sl] == (i + 1)
        z = raw[sl][m].astype("float64")
        spill = float(np.median(filled[sl][m]))
        volume = rain_m * max(float(catch_cells[i]), float(z.size)) * cell_area / cell_area   # in cell-depth units
        order = np.argsort(z)
        zs = z[order]
        cs = np.cumsum(zs)
        vol_at = np.arange(1, zs.size + 1) * zs - cs        # volume (m of depth x cells) when the level reaches each cell
        k = int(np.searchsorted(vol_at, volume, side="right"))
        level = (volume + cs[k - 1]) / k if k >= 1 else zs[0]
        level = min(level, spill)
        d = np.clip(level - z, 0.0, None)
        block = depth[sl]
        block[m] = d.astype("float32")
    return depth
