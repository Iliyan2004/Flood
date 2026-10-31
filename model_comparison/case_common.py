"""Shared pieces for the multi-model 2022 Sindh case study (same DEM, forcing, observed map and scoring for every model)."""
import json, os, subprocess, sys, time
import numpy as np, rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, shape
sys.path.insert(0, os.path.expanduser("~/flood"))
import geopandas as gpd, polygon_utils, flood_model, data_paths, validate_2022_flood as v

CMP = os.path.expanduser("~/flood/cmp")
AOIS = {"A_manchar_edge": (67.70, 27.40), "B_north_dadu": (68.36, 28.18), "C_shikarpur": (67.64, 27.94)}
HALF = 0.06                       # 0.12 deg box, about 13 km
RAIN_TOTAL_MM, HOURS = 253.5, 48  # real ERA5 wettest-48h window (validate_2022_flood.py)
PEAK = 2 * RAIN_TOTAL_MM / HOURS  # triangular storm that delivers the total
DRY = 0.05
_GT = None

def gt_geom():
    global _GT
    if _GT is None:
        _GT = gpd.read_file(v.GT_SHP).to_crs(4326).union_all()
    return _GT

def prep(name):
    lon, lat = AOIS[name]
    d = os.path.join(CMP, name); os.makedirs(d, exist_ok=True)
    dem = os.path.join(d, "aoi.dem.ascii")
    geom = mapping(box(lon - HALF, lat - HALF, lon + HALF, lat + HALF))
    if not os.path.exists(dem):
        header, act, tot = polygon_utils.crop_dem_to_polygon(data_paths.get("PAKISTAN_DEM"), geom, dem)
    h, z = flood_model.read_ascii(dem)
    with rasterio.open(data_paths.get("PAKISTAN_DEM")) as s:
        h["src_crs"] = str(s.crs)
    return d, dem, h, z

def obs_mask(h, z):
    nr, nc, cs = int(h["nrows"]), int(h["ncols"]), h["cellsize"]
    tr = rasterio.transform.from_origin(h["xllcorner"], h["yllcorner"] + nr * cs, cs, cs)
    g = shape(transform_geom("EPSG:4326", h["src_crs"], mapping(gt_geom())))
    return rasterize([(g, 1)], out_shape=(nr, nc), transform=tr, fill=0, dtype="uint8").astype(bool)

def score(depth, obs, z, dry=DRY):
    valid = z != -9999
    w = valid & (depth > dry)
    hit = int((w & obs & valid).sum()); mis = int((~w & obs & valid).sum()); fa = int((w & ~obs & valid).sum())
    cn = int((~w & ~obs & valid).sum())
    csi = hit / max(hit + mis + fa, 1); pod = hit / max(hit + mis, 1); far = fa / max(hit + fa, 1)
    f1 = 2 * hit / max(2 * hit + mis + fa, 1)
    return {"CSI": round(csi, 3), "POD": round(pod, 3), "FAR": round(far, 3), "F1": round(f1, 3),
            "wet_km2": round(float(w.sum()) * (h_cs(z)) , 2) if False else int(w.sum()), "obs_cells": int((obs & valid).sum()),
            "hits": hit, "misses": mis, "false_alarms": fa, "correct_neg": cn,
            "area_ratio": round(float(w.sum()) / max(float((obs & valid).sum()), 1), 3)}

def h_cs(z): return 0
