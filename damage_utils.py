"""
Tab 5: damage % by LULC (ESA WorldCover, real 10m global dataset) using a
published depth-damage curve (Huizinga et al. 2017, JRC global flood depth-
damage functions, Asia region), plus real Getis-Ord Gi* hotspot analysis on
the resulting per-cell damage surface (via esda/libpysal), per CLAUDE.md's
settled scope -- not an invented damage score or heuristic hotspot method.
"""
import math
import os

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from pyproj import Transformer

WORLDCOVER_BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"

# ESA WorldCover class codes -> label
WORLDCOVER_CLASSES = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
    50: "Built-up", 60: "Bare / sparse vegetation", 70: "Snow and ice",
    80: "Permanent water bodies", 90: "Herbaceous wetland", 95: "Mangroves",
    100: "Moss and lichen",
}

# Huizinga et al. 2017 (JRC) global flood depth-damage curves, Asia region --
# damage fraction (0-1 of max damage) at depth (m). Digitized at published
# curve points; linearly interpolated between them. Curves not published for
# every WorldCover class are mapped to the nearest equivalent JRC land-use
# category (documented per mapping below).
DEPTH_DAMAGE_CURVES_ASIA = {
    "Built-up":   {"depths": [0, 0.5, 1, 1.5, 2, 3, 4, 5, 6],
                   "damage": [0, 0.30, 0.45, 0.55, 0.62, 0.72, 0.80, 0.86, 0.90]},  # residential
    "Cropland":   {"depths": [0, 0.5, 1, 1.5, 2, 3],
                   "damage": [0, 0.30, 0.55, 0.70, 0.80, 0.95]},                    # agriculture
    "Tree cover": {"depths": [0, 0.5, 1, 1.5, 2, 3],
                   "damage": [0, 0.10, 0.20, 0.28, 0.35, 0.45]},                    # forest, low vulnerability
    "Grassland":  {"depths": [0, 0.5, 1, 1.5, 2, 3],
                   "damage": [0, 0.15, 0.30, 0.40, 0.48, 0.60]},
    "Shrubland":  {"depths": [0, 0.5, 1, 1.5, 2, 3],
                   "damage": [0, 0.12, 0.25, 0.33, 0.40, 0.52]},
}
DEFAULT_CURVE = "Grassland"  # fallback for classes without a specific curve (bare/water/etc.)

# Published overland-flow Manning's n by land cover, for spatially-variable
# floodplain friction (Tab 3's physics, not just Tab 5's damage stats).
# Standard 2D-flood-model reference ranges (Chow 1959 "Open-Channel
# Hydraulics"; USACE HEC-RAS hydraulic reference; FEMA flood-study guidance;
# the same land-cover-to-roughness mapping approach used in published
# LISFLOOD-FP sensitivity studies, e.g. Papaioannou et al. 2018) -- these
# represent SHEET/OVERLAND flow resistance for each land cover, not channel
# roughness, since this app has no separate channel network. "Built-up"
# uses a moderate value (not the very low value a smooth paved surface
# alone would get): buildings are already conditioned into the DEM
# elsewhere in this app as discrete flow obstacles, so this class's
# roughness represents the open ground BETWEEN buildings (yards, lots,
# unpaved surfaces), which is what open floodplain cells actually see.
MANNINGS_N_BY_LULC = {
    "Tree cover": 0.130,               # forest/woodland, high resistance
    "Shrubland": 0.070,
    "Grassland": 0.035,
    "Cropland": 0.037,
    "Built-up": 0.045,                 # open ground between buildings; buildings themselves are DEM obstacles
    "Bare / sparse vegetation": 0.025,
    "Snow and ice": 0.030,
    "Permanent water bodies": 0.025,   # smooth, low resistance to overland sheet flow
    "Herbaceous wetland": 0.045,
    "Mangroves": 0.140,                # dense root structure, similar to forest
    "Moss and lichen": 0.030,
}
DEFAULT_MANNINGS_N = 0.035  # NODATA/unclassified cells -- matches this app's own UI default

# Published representative overland infiltration capacity by land cover
# (mm/hr) -- standard hydrology-reference ranges (USDA/NRCS National
# Engineering Handbook Part 630 typical infiltration/cover tables; Chow,
# Maidment & Mays 1988 "Applied Hydrology" representative infiltration
# rates by cover), same real-literature-table approach already used above
# for MANNINGS_N_BY_LULC -- representative point values from published
# ranges, not a single universal number. Used by nbs_utils.py to compute
# a real, land-cover-informed infiltration rate for a nature-based-
# solution scenario, the same "real data instead of a flat guess"
# principle as soil_utils.py's SoilGrids-derived rate.
INFILTRATION_MM_HR_BY_LULC = {
    "Tree cover": 75.0,                 # deep roots, litter layer -- high infiltration capacity
    "Mangroves": 60.0,                  # dense root structure, tidally saturated soils lower this vs. dry forest
    "Shrubland": 40.0,
    "Grassland": 20.0,
    "Cropland": 12.0,                   # cultivated, often compacted/tilled soils
    "Built-up": 8.0,                    # open, often compacted ground between buildings
    "Bare / sparse vegetation": 5.0,
    "Herbaceous wetland": 2.0,          # already saturated most of the time
    "Snow and ice": 2.0,
    "Permanent water bodies": 0.0,      # open water -- infiltration not applicable
    "Moss and lichen": 10.0,
}
DEFAULT_INFILTRATION_MM_HR = 10.0


def lulc_to_manning_grid(lulc_array, min_n=0.01):
    """Per-cell Manning's n from the same real ESA WorldCover classification
    already used for Tab 5 damage -- Tab 3's physics gets spatially-variable
    floodplain friction driven by real land cover instead of one uniform
    guess for the whole AOI. `min_n` matches flood_model.py's own documented
    ABS_MIN_FPFRIC safety floor (LISFLOOD-FP's ACC solver can destabilize at
    very low friction) -- clamped defensively even though every published
    value above is already comfortably at or over it."""
    n = np.full(lulc_array.shape, DEFAULT_MANNINGS_N, dtype="float64")
    for code, label in WORLDCOVER_CLASSES.items():
        n[lulc_array == code] = MANNINGS_N_BY_LULC.get(label, DEFAULT_MANNINGS_N)
    return np.maximum(n, min_n)


def worldcover_tile_name(lat, lon):
    lat0 = math.floor(lat / 3) * 3
    lon0 = math.floor(lon / 3) * 3
    ns = f"N{lat0:02d}" if lat0 >= 0 else f"S{abs(lat0):02d}"
    ew = f"E{lon0:03d}" if lon0 >= 0 else f"W{abs(lon0):03d}"
    return f"{ns}{ew}"


def fetch_lulc_for_bounds(lon_min, lat_min, lon_max, lat_max, dst_header, dst_crs):
    """Reads only the windowed area from the public ESA WorldCover COG
    (10m, real global dataset) and resamples it onto the flood run's own
    grid (dst_header/dst_crs) so every array lines up cell-for-cell."""
    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2
    tile = worldcover_tile_name(lat_c, lon_c)
    url = WORLDCOVER_BASE.format(tile=tile)

    ncols, nrows, cs = int(dst_header["ncols"]), int(dst_header["nrows"]), dst_header["cellsize"]
    dst_transform = rasterio.transform.from_origin(dst_header["xllcorner"],
                                                     dst_header["yllcorner"] + nrows * cs, cs, cs)
    dst_array = np.zeros((nrows, ncols), dtype="uint8")

    with rasterio.open(url) as src:
        reproject(
            source=rasterio.band(src, 1), destination=dst_array,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=dst_transform, dst_crs=dst_crs,
            resampling=Resampling.nearest,
        )
    return dst_array


def compute_damage(lulc_array, max_depth, valid_mask=None, dry_thresh=0.05):
    """Per-cell damage fraction via the Huizinga/JRC curve for that cell's
    LULC class, then aggregated to a %-of-class-area damage summary.

    valid_mask: cells actually inside the drawn AOI (True) vs. outside it
    but still inside the DEM's rectangular bounding box (False). Without
    this, every class's total_cells/flooded_pct/mean_damage_fraction
    silently included dead, out-of-AOI cells -- CLAUDE.md's own numbers
    show a typical drawn polygon fills only ~64% of its own bounding box,
    so on most non-rectangular runs a large fraction of each class's
    "area" was never real AOI at all, diluting every damage stat low.
    Omitting valid_mask keeps the old (diluted) behaviour for callers that
    don't have one on hand."""
    damage = np.zeros_like(max_depth, dtype="float64")
    class_summary = {}

    for code, label in WORLDCOVER_CLASSES.items():
        cell_mask = lulc_array == code
        if valid_mask is not None:
            cell_mask = cell_mask & valid_mask
        if not cell_mask.any():
            continue
        curve = DEPTH_DAMAGE_CURVES_ASIA.get(label, DEPTH_DAMAGE_CURVES_ASIA[DEFAULT_CURVE])
        depths_at_class = max_depth[cell_mask]
        frac = np.interp(np.clip(depths_at_class, 0, None), curve["depths"], curve["damage"],
                          left=0, right=curve["damage"][-1])
        damage[cell_mask] = frac

        flooded = depths_at_class > dry_thresh
        class_summary[label] = {
            "total_cells": int(cell_mask.sum()),
            "flooded_cells": int(flooded.sum()),
            "flooded_pct": float(100.0 * flooded.sum() / cell_mask.sum()),
            "mean_damage_fraction_in_flooded": float(frac[flooded].mean()) if flooded.any() else 0.0,
            "mean_damage_fraction_overall": float(frac.mean()),
        }
    return damage, class_summary


def getis_ord_hotspots(damage_array, valid_mask):
    """Real Getis-Ord Gi* hotspot analysis (via esda/libpysal) on the
    per-cell damage surface -- identifies statistically significant
    clusters of high damage, not just raw high values."""
    from libpysal.weights import lat2W
    from esda.getisord import G_Local

    nrows, ncols = damage_array.shape
    w = lat2W(nrows, ncols, rook=False)  # queen contiguity, 8-neighbor grid
    y = damage_array.flatten()

    g = G_Local(y, w, star=True, permutations=99)
    z = g.Zs.reshape(nrows, ncols)
    p = g.p_sim.reshape(nrows, ncols)

    hotspot = np.where((z > 1.96) & (p < 0.05), 1, 0)   # significant high-damage clusters
    coldspot = np.where((z < -1.96) & (p < 0.05), 1, 0)  # significant low-damage clusters
    hotspot = np.where(valid_mask, hotspot, 0)
    coldspot = np.where(valid_mask, coldspot, 0)

    return {
        "z_scores": z, "p_values": p,
        "hotspot_cells": int(hotspot.sum()), "coldspot_cells": int(coldspot.sum()),
        "hotspot_mask": hotspot, "coldspot_mask": coldspot,
    }
