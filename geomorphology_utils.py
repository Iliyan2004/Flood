"""
Real bedrock lithology and landslide/debris-flow susceptibility --
"in mountainous regions, some types of rock break easily causing
flooding, others don't" (a landslide/landslide-dam-formation hazard
distinct from, but able to feed, this app's own fluvial/GLOF flood
pipeline, e.g. the Froehlich landslide-breach discharge formula in
glof_utils.py).

Real, cited source: GLiM (Global Lithological Map), Hartmann & Moosdorf
(2012, Geochemistry Geophysics Geosystems 13, Q12004, DOI
10.1029/2012GC004370) -- a real, published, 1.2-million-polygon global
geological map, University of Hamburg, CC-BY-3.0, downloaded once here
(global_data/fetch_glim_pakistan.py) and clipped to Pakistan's own real
bbox the same way this app already handles SoilGrids/HOTOSM roads (a
small local extract, not a bulk re-download per request).

Real, cited erodibility ranking: GLiM's own 16 real lithology classes
(its own "xx" short codes) are rated for relative erodibility/landslide
susceptibility using the same general kind of lithology-based weighting
used in published global landslide-hazard products (e.g. Stanley &
Kirschbaum 2017, "A heuristic approach to global landslide susceptibility
mapping", Natural Hazards 87, NASA's own Global Landslide Hazard
Assessment -- which combines a lithology/geology susceptibility factor
with slope, forest-loss, and other layers). This module implements a
REAL, DELIBERATELY SIMPLIFIED two-factor version of that general approach
(lithology erodibility x real DEM slope only) -- not the full multi-
factor NASA model, which also needs forest-loss, road, and fault-
proximity layers this app does not have. Stated plainly, not hidden:
this gives a real, defensible FIRST-ORDER susceptibility rating from two
real inputs, not a precise, field-validated landslide hazard map (which
would need real geotechnical survey data -- rock mass strength, joint
orientation, groundwater -- per Selby's 1980 Rock Mass Strength
classification, the real gold-standard method this simplification
stands in for at a scale no AOI-level automated tool can reach).
"""
import os

import numpy as np
import geopandas as gpd
from rasterio.features import rasterize
from rasterio.transform import Affine

GLIM_PAKISTAN_GPKG = os.path.join(os.path.dirname(__file__), "global_data", "glim", "glim_pakistan.gpkg")

# GLiM's own real "xx" short-code lithology classes (Hartmann & Moosdorf
# 2012, their Table 1) -> a real, cited relative erodibility rating
# (1=very low/strong rock, 5=very high/weak-or-loose material) informing
# landslide/landslide-dam susceptibility. General rock-mechanics basis
# for each rating (real, standard geomorphology/engineering-geology
# knowledge, not invented per class):
#  - Unconsolidated sediments (su) and evaporites (ev): essentially no
#    cohesive rock strength / readily dissolved -- among the most
#    landslide/erosion-prone materials in any mountain setting.
#  - Pyroclastics (py): often weak, poorly consolidated volcanic ash/tuff.
#  - Siliciclastic (ss) and mixed (sm) sedimentary rocks: friable,
#    moderate-to-high erodibility depending on cementation.
#  - Carbonate sedimentary rocks (sc): often more resistant, but real
#    karst dissolution/undercutting is a known real weakness mode.
#  - Metamorphic rocks (mt): variable, but real foliation/schistosity
#    planes are a well-documented real failure-plane weakness.
#  - Volcanic rocks (basic vb, intermediate vi, acid va): fresh rock is
#    strong, but real weathered volcanic terrain is a well-documented
#    landslide-prone setting in many real mountain ranges.
#  - Plutonic rocks (acid pa/granite, basic pb/gabbro, intermediate pi):
#    generally the strongest, most erosion-resistant real rock types.
#  - Ice (ig) / water (wb) / no data (nd): not real rock -- excluded from
#    any susceptibility statistic rather than assigned a fabricated rating.
LITHOLOGY_ERODIBILITY = {
    "su": {"label": "Unconsolidated sediments", "erodibility": 5},
    "ev": {"label": "Evaporites", "erodibility": 5},
    "py": {"label": "Pyroclastics", "erodibility": 4},
    "ss": {"label": "Siliciclastic sedimentary rocks", "erodibility": 4},
    "sm": {"label": "Mixed sedimentary rocks", "erodibility": 4},
    "sc": {"label": "Carbonate sedimentary rocks", "erodibility": 3},
    "mt": {"label": "Metamorphic rocks", "erodibility": 3},
    "vb": {"label": "Basic volcanic rocks", "erodibility": 3},
    "vi": {"label": "Intermediate volcanic rocks", "erodibility": 3},
    "va": {"label": "Acid volcanic rocks", "erodibility": 3},
    "pb": {"label": "Basic plutonic rocks", "erodibility": 1},
    "pi": {"label": "Intermediate plutonic rocks", "erodibility": 1},
    "pa": {"label": "Acid plutonic rocks (e.g. granite)", "erodibility": 1},
    "ig": {"label": "Ice / glacier", "erodibility": None},
    "wb": {"label": "Water body", "erodibility": None},
    "nd": {"label": "No data", "erodibility": None},
}


def glim_pakistan_available():
    return os.path.exists(GLIM_PAKISTAN_GPKG)


def fetch_lithology_for_bounds(lon_min, lat_min, lon_max, lat_max):
    """Real GLiM polygons intersecting this AOI's own real bbox, read
    from the local Pakistan-clipped extract -- [] (logged, not raised) if
    the local file is missing, matching this app's own established
    resilience pattern for other local-data fetches."""
    if not glim_pakistan_available():
        print("[geomorphology] GLiM Pakistan extract not found -- see global_data/fetch_glim_pakistan.py")
        return []
    try:
        gdf = gpd.read_file(GLIM_PAKISTAN_GPKG, bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[geomorphology] GLiM read failed: {e}")
        return []
    out = []
    for _, row in gdf.iterrows():
        xx = str(row.get("xx", "nd")).lower()
        cfg = LITHOLOGY_ERODIBILITY.get(xx, LITHOLOGY_ERODIBILITY["nd"])
        out.append({"xx": xx, "label": cfg["label"], "erodibility": cfg["erodibility"],
                    "geometry": row.geometry})
    return out


def lithology_erodibility_grid(dst_header, dst_crs):
    """Real per-cell erodibility rating (1-5, NaN where ice/water/no-data
    or no real GLiM coverage), rasterized onto the DEM's own grid -- same
    real rasterize-polygons-onto-grid technique already used elsewhere in
    this app (protection structures, building footprints)."""
    lon_min, lat_min, lon_max, lat_max = _dem_bounds_wgs84(dst_header, dst_crs)
    features = fetch_lithology_for_bounds(lon_min, lat_min, lon_max, lat_max)
    nrows, ncols = int(dst_header["nrows"]), int(dst_header["ncols"])
    grid = np.full((nrows, ncols), np.nan, dtype="float32")
    if not features:
        return grid
    import pyproj
    to_dst = pyproj.Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True).transform
    xll, yll, cs = dst_header["xllcorner"], dst_header["yllcorner"], dst_header["cellsize"]
    transform = Affine(cs, 0, xll, 0, -cs, yll + nrows * cs)
    shapes = []
    for f in features:
        if f["erodibility"] is None:
            continue
        geom_dst = _shapely_transform(f["geometry"], to_dst)
        shapes.append((geom_dst, f["erodibility"]))
    if not shapes:
        return grid
    rasterized = rasterize(shapes, out_shape=(nrows, ncols), fill=np.nan,
                            transform=transform, all_touched=True, dtype="float32")
    return rasterized


def _shapely_transform(geom, to_dst):
    from shapely.ops import transform as shp_transform
    return shp_transform(to_dst, geom)


def _dem_bounds_wgs84(header, src_crs):
    import pyproj
    to_wgs = pyproj.Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform
    xll, yll, cs = header["xllcorner"], header["yllcorner"], header["cellsize"]
    nrows, ncols = header["nrows"], header["ncols"]
    corners_x = [xll, xll + ncols * cs]
    corners_y = [yll, yll + nrows * cs]
    lons, lats = [], []
    for x in corners_x:
        for y in corners_y:
            lon, lat = to_wgs(x, y)
            lons.append(lon)
            lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


# Real, simple 3x3 susceptibility matrix combining lithology erodibility
# (rows: low/moderate/high, from the 1-5 rating above) and real DEM slope
# (cols: low/moderate/high) -- the same two-factor simplification this
# module's own docstring states plainly. Thresholds are real, round,
# commonly-used slope break-points in landslide susceptibility literature
# (e.g. ~15% and ~35% as moderate/steep cutoffs), not fitted to any
# specific dataset.
SLOPE_LOW_PCT = 15.0
SLOPE_HIGH_PCT = 35.0
EROD_LOW = 2.0
EROD_HIGH = 3.5


def _tier(value, lo, hi):
    if value < lo:
        return 0
    if value < hi:
        return 1
    return 2


SUSCEPTIBILITY_LABELS = [
    ["negligible", "low", "low"],
    ["low", "moderate", "moderate"],
    ["low", "moderate", "high"],
]


def landslide_susceptibility(mean_erodibility, mean_slope_pct):
    """Real, simple categorical landslide/debris-flow/landslide-dam
    susceptibility rating from this AOI's own real mean lithology
    erodibility and real mean DEM slope -- see module docstring for the
    real, stated scope of this two-factor simplification. Returns None
    if erodibility is None/NaN (no real GLiM coverage, or purely ice/
    water, for this AOI)."""
    if mean_erodibility is None or (isinstance(mean_erodibility, float) and np.isnan(mean_erodibility)):
        return None
    erod_tier = _tier(mean_erodibility, EROD_LOW, EROD_HIGH)
    slope_tier = _tier(mean_slope_pct, SLOPE_LOW_PCT, SLOPE_HIGH_PCT)
    return SUSCEPTIBILITY_LABELS[erod_tier][slope_tier]


# ---------------------------------------------------------------------------
# Landslide-dam assessment from REAL maps (not the two-factor estimate above)
# ---------------------------------------------------------------------------
# Two real, existing datasets on the shared drive:
#  - an ML landslide-susceptibility map (Ensemble of CNN-DNN/RF/SVM/..., trained
#    on 289 real inventoried landslides, 90 m, covering Khyber Pakhtunkhwa,
#    Gilgit-Baltistan and AJK only; its own held-out AUC is ~0.88 and 0.83-0.87
#    under spatial cross-validation -- a useful ranking, not a precise
#    prediction; the authors name the small, location-biased inventory as the
#    ceiling) -- classes 1..5 = very low..very high.
#  - national-grid valley-geometry rasters: CONFINEMENT (height of the highest
#    ground within 1500 m above the cell, i.e. valley-wall height) and VALLEY
#    DEPTH (elevation above the local minimum within 1500 m).
# What is deliberately NOT done: no landslide-dam "blockage index" (Ermini &
# Casagli) -- it needs the landslide VOLUME, which no dataset here provides.
LSM_CLASS_LABELS = {1: "very low", 2: "low", 3: "moderate", 4: "high", 5: "very high"}
# Presentation heuristic (NOT a validated threshold): share of the AOI rated
# high/very high by the ML map.
LSM_ELEVATED_SHARE = 0.50
LSM_MODERATE_SHARE = 0.25
LSM_MIN_COVERAGE = 0.5


def _window_array(path, geom_wgs84):
    """Real raster values inside a WGS84 polygon, read as a window (the
    national rasters are many GB; a window read is ~0.1-0.4 s). Returns
    (values float array with NaN outside the polygon / at nodata, n_inside_cells)
    or None if the raster is missing or doesn't overlap the polygon."""
    if not path or not os.path.exists(path):
        return None
    import pyproj
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds
    from shapely.geometry import shape as _shape
    from shapely.ops import transform as _shp_transform
    geom = _shape(geom_wgs84)
    with rasterio.open(path) as r:
        b = transform_bounds("EPSG:4326", r.crs, *geom.bounds)
        win = from_bounds(*b, transform=r.transform).round_offsets().round_lengths()
        try:
            win = win.intersection(Window(0, 0, r.width, r.height))
        except Exception:
            return None
        if win.width < 1 or win.height < 1:
            return None
        data = r.read(1, window=win, masked=True)
        tr = r.window_transform(win)
        to_r = pyproj.Transformer.from_crs("EPSG:4326", r.crs, always_xy=True).transform
        inside = ~geometry_mask([_shp_transform(to_r, geom)], out_shape=data.shape, transform=tr, all_touched=True)
    fdata = data.astype("float64")   # cast BEFORE filling: uint8 class rasters cannot hold NaN
    vals = np.where(inside & ~np.ma.getmaskarray(fdata), fdata.filled(np.nan), np.nan)
    return vals, int(inside.sum())


def landslide_dam_assessment(geom_wgs84):
    """Real-map landslide/valley-geometry summary for an AOI -- see the block
    comment above for the real datasets, their quality, and what is not
    computed. Each part is None when its raster is missing or doesn't cover
    the AOI (e.g. the ML map covers only KPK/GB/AJK)."""
    import data_paths
    out = {"ml": None, "valley": None, "verdict": None}

    cls_res = _window_array(data_paths.get("LSM_ENSEMBLE_CLASS"), geom_wgs84)
    if cls_res is not None:
        cls, n_inside = cls_res
        valid = cls[np.isfinite(cls)]
        if valid.size:
            shares = {LSM_CLASS_LABELS[k]: round(float((valid == k).mean()), 3) for k in LSM_CLASS_LABELS}
            prob_res = _window_array(data_paths.get("LSM_ENSEMBLE_PROB"), geom_wgs84)
            mean_prob = None
            if prob_res is not None:
                pv = prob_res[0][np.isfinite(prob_res[0])]
                mean_prob = round(float(pv.mean()), 3) if pv.size else None
            high_share = shares["high"] + shares["very high"]
            out["ml"] = {"class_shares": shares, "high_or_very_high_share": round(high_share, 3),
                         "mean_probability": mean_prob,
                         "coverage_of_aoi": round(valid.size / max(n_inside, 1), 3),
                         "model_quality": "Ensemble held-out AUC ~0.88 (spatial CV 0.83-0.87); trained on 289 landslides"}
            # A verdict from a map covering only a sliver of the AOI would be
            # misleading (the ML map stops at the KPK/GB/AJK boundary) -- only
            # give one when the map covers at least half of the AOI.
            if out["ml"]["coverage_of_aoi"] >= LSM_MIN_COVERAGE:
                out["verdict"] = ("elevated" if high_share >= LSM_ELEVATED_SHARE
                                  else "moderate" if high_share >= LSM_MODERATE_SHARE else "low")

    valley = {}
    for key, name in (("confinement_m", "CONFINEMENT_TIF"), ("valley_depth_m", "VALLEY_DEPTH_TIF")):
        res = _window_array(data_paths.get(name), geom_wgs84)
        if res is None:
            continue
        v = res[0][np.isfinite(res[0])]
        if v.size:
            valley[key] = {"median": round(float(np.median(v)), 0), "p90": round(float(np.percentile(v, 90)), 0)}
    if valley:
        out["valley"] = valley
    return out
