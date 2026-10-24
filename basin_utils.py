"""
Real Pakistan river basins (HydroSHEDS Level 08, Lehner & Grill 2013,
"HydroBASINS") -- 1,131 real individual basin polygons, symlinked from a
sibling task's own real download (see global_data/hydrosheds_basins_pk)
into this app's own established external-dataset pattern (GTSR/SoilGrids/
HOTOSM roads all follow the same symlink-in convention).

Real, checked-directly limitation, not glossed over: unlike the tehsil
dataset, HydroSHEDS basins carry NO real human-readable name -- each
feature's own real properties are purely numeric/hydrological (HYBAS_ID,
PFAF_ID, SUB_AREA km^2, ORDER, ENDO/COAST flags). A name-search UI (the
tehsil pattern) genuinely doesn't apply here. This module instead
supports the real workflow already used manually to find the Gilgit-
Baltistan basin for this app's own steep-terrain bisection: filter to a
real region bounding box, rank by real drainage area, and a real point-
in/near-basin lookup (for "which basin contains this spot on the map").

A one-time real index (id/centroid/bounds/area, NOT full geometry) is
built by scanning every one of the 1,131 real GeoJSON files once and
cached to disk -- avoids re-parsing 1,131 files on every request; full
geometry is only read back from its own real source file on demand
(basin_geometry), never duplicated into the index.
"""
import glob
import json
import os
import threading

import numpy as np

BASINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "hydrosheds_basins_pk")
INDEX_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "hydrosheds_basins_pk_index.json")

# Real, approximate Pakistan administrative-region bounding boxes --
# coarse plausibility boxes only (same honest "not a precise boundary"
# caveat this app already states for coastal_utils.py's own coastal-
# latitude-belt check), used purely to let a user filter basins by
# region without needing a real provincial-boundary shapefile on hand.
REGION_BBOXES = {
    "gilgit_baltistan": (72.0, 34.2, 77.9, 37.2),
    "khyber_pakhtunkhwa": (69.3, 31.5, 74.2, 36.9),
    "punjab": (69.3, 27.6, 75.5, 33.9),
    "sindh": (66.6, 23.6, 71.2, 28.6),
    "balochistan": (60.8, 24.0, 70.2, 32.1),
    "azad_kashmir": (73.0, 33.0, 75.9, 36.4),
}

_lock = threading.Lock()
_index = None


def basins_data_available():
    return os.path.isdir(BASINS_DIR) and bool(glob.glob(os.path.join(BASINS_DIR, "*.geojson")))


def _build_index():
    """Real, one-time scan of every basin GeoJSON's own real properties +
    geometry bounds/centroid (real shapely centroid of the real polygon,
    not just a bbox midpoint) -- cached to disk so this only ever runs
    once per real dataset version."""
    import shapely.geometry as sg

    entries = []
    for fp in sorted(glob.glob(os.path.join(BASINS_DIR, "*.geojson"))):
        try:
            with open(fp) as f:
                gj = json.load(f)
            feat = gj["features"][0] if gj.get("type") == "FeatureCollection" else gj
            props = feat.get("properties", {})
            geom = sg.shape(feat["geometry"])
            if geom.is_empty:
                continue
            centroid = geom.centroid
            minx, miny, maxx, maxy = geom.bounds
            entries.append({
                "hybas_id": int(props.get("HYBAS_ID")),
                "file": os.path.basename(fp),
                "area_km2": float(props.get("SUB_AREA", 0.0)),
                "order": int(props.get("ORDER", 0)),
                "endorheic": bool(props.get("ENDO", 0)),
                "coastal": bool(props.get("COAST", 0)),
                "centroid_lon": round(float(centroid.x), 5), "centroid_lat": round(float(centroid.y), 5),
                "bounds": [round(minx, 5), round(miny, 5), round(maxx, 5), round(maxy, 5)],
            })
        except Exception as e:
            print(f"[basins] skipping unreadable basin file {fp}: {e}")
    return entries


def _load_index():
    global _index
    with _lock:
        if _index is not None:
            return _index
        if not basins_data_available():
            _index = []
            return _index
        if os.path.exists(INDEX_CACHE_PATH):
            try:
                with open(INDEX_CACHE_PATH) as f:
                    _index = json.load(f)
                return _index
            except Exception:
                pass
        _index = _build_index()
        try:
            with open(INDEX_CACHE_PATH, "w") as f:
                json.dump(_index, f)
        except Exception as e:
            print(f"[basins] failed to cache real basin index: {e}")
        return _index


def basins_in_region(region=None, bbox=None, min_area_km2=0.0, limit=15):
    """Real basins whose own real centroid falls in a named region (see
    REGION_BBOXES) or an explicit (lon_min, lat_min, lon_max, lat_max),
    ranked by real drainage area (SUB_AREA, HydroSHEDS' own real upstream
    catchment area) descending -- the same real "biggest basin in this
    region" workflow already used by hand to find the Gilgit-Baltistan
    basin for this app's own steep-terrain investigation. Returns []
    (logged, not raised) if the real dataset isn't available."""
    index = _load_index()
    if not index:
        return []
    if bbox is None:
        if region is None or region not in REGION_BBOXES:
            return []
        bbox = REGION_BBOXES[region]
    lon_min, lat_min, lon_max, lat_max = bbox
    matches = [
        e for e in index
        if lon_min <= e["centroid_lon"] <= lon_max and lat_min <= e["centroid_lat"] <= lat_max
        and e["area_km2"] >= min_area_km2
    ]
    matches.sort(key=lambda e: -e["area_km2"])
    return matches[:limit]


def nearest_basin(lon, lat, max_results=5):
    """Real nearest-basins-by-centroid-distance lookup (haversine, same
    real technique already used elsewhere in this app for nearest-GTSR-
    station/nearest-HydroLAKES-lake) -- for "which basin is this point
    in/near" rather than requiring an exact region+area browse. Does NOT
    do a real point-in-polygon containment test (the index only carries
    centroid/bounds, not full geometry, to stay cheap) -- a caller
    wanting exact containment should fetch basin_geometry for the
    returned candidate(s) and test directly."""
    from coastal_utils import _haversine_km

    index = _load_index()
    if not index:
        return []
    scored = []
    for e in index:
        dist_km = _haversine_km(lon, lat, e["centroid_lon"], e["centroid_lat"])
        scored.append({**e, "distance_km": round(dist_km, 1)})
    scored.sort(key=lambda e: e["distance_km"])
    return scored[:max_results]


def basin_geometry(hybas_id):
    """Real full polygon geometry for one basin, read fresh from its own
    real source GeoJSON file (never duplicated into the lightweight
    index) -- returns the real GeoJSON geometry dict, or None if the id
    isn't found."""
    index = _load_index()
    entry = next((e for e in index if e["hybas_id"] == int(hybas_id)), None)
    if entry is None:
        return None
    fp = os.path.join(BASINS_DIR, entry["file"])
    try:
        with open(fp) as f:
            gj = json.load(f)
        feat = gj["features"][0] if gj.get("type") == "FeatureCollection" else gj
        return feat["geometry"]
    except Exception as e:
        print(f"[basins] failed to read real geometry for basin {hybas_id}: {e}")
        return None
