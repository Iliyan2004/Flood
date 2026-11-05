"""User-supplied elevation model (DEM) for an area: validation, preparation and lookup.

A user can upload their own DEM (e.g. a LiDAR or drone survey) instead of the Pakistan SRTM. The file is
checked, put into a projected (metre) coordinate system if it came in geographic degrees (the flood solver
needs metre cells), stored under a content hash (the same file uploaded twice is one entry, and the run cache
key stays stable), and later chosen by `custom_dem_id` in a run request.
"""
import hashlib
import os
import re

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_bounds

from global_dem_utils import utm_epsg_for_lonlat

CUSTOM_DEM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_dems")
os.makedirs(CUSTOM_DEM_DIR, exist_ok=True)

MIN_CELLSIZE_M = 0.5      # finer than this is a survey the solver cannot afford over a real area
MAX_CELLSIZE_M = 500.0    # coarser than this cannot resolve a flood plain
MIN_VALID_FRACTION = 0.10
NODATA_OUT = -9999.0
_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class CustomDemError(ValueError):
    pass


def path_for(dem_id):
    """Absolute path of a stored custom DEM, or None for a malformed/unknown id."""
    if not dem_id or not _ID_RE.match(str(dem_id)):
        return None
    p = os.path.join(CUSTOM_DEM_DIR, f"{dem_id}.tif")
    return p if os.path.exists(p) else None


def bounds_wgs84(path):
    with rasterio.open(path) as src:
        return transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)


def covers_bbox(path, bbox_wgs84):
    """True when the DEM's footprint fully contains the (minx, miny, maxx, maxy) bbox."""
    b = bounds_wgs84(path)
    return b[0] <= bbox_wgs84[0] and b[1] <= bbox_wgs84[1] and b[2] >= bbox_wgs84[2] and b[3] >= bbox_wgs84[3]


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def prepare_upload(src_path):
    """Validates an uploaded raster and stores a solver-ready copy. Returns a summary dict (including `dem_id`).
    Raises CustomDemError with a plain-language reason for anything unusable."""
    try:
        src = rasterio.open(src_path)
    except Exception as e:
        raise CustomDemError(f"could not read this file as a raster (use a GeoTIFF): {e}")
    with src:
        if src.crs is None:
            raise CustomDemError("the file has no coordinate system; save it as a GeoTIFF with its CRS embedded")
        if src.count < 1:
            raise CustomDemError("the file has no raster band")
        b = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        lon_c, lat_c = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        geographic = src.crs.is_geographic
        # What the solver needs: a projected CRS in metres.
        if geographic:
            dst_crs = CRS.from_epsg(utm_epsg_for_lonlat(lon_c, lat_c))
        elif src.crs.linear_units_factor[1] not in (1.0,):
            # feet etc.: reproject into UTM so cell sizes are in metres
            dst_crs = CRS.from_epsg(utm_epsg_for_lonlat(lon_c, lat_c))
        else:
            dst_crs = src.crs
        needs_reproject = dst_crs != src.crs
        if needs_reproject:
            transform, width, height = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        else:
            transform, width, height = src.transform, src.width, src.height
        cellsize = abs(transform.a)
        if not (MIN_CELLSIZE_M <= cellsize <= MAX_CELLSIZE_M):
            raise CustomDemError(f"cell size is {cellsize:.2f} m; this tool accepts {MIN_CELLSIZE_M} to {MAX_CELLSIZE_M} m")
        if width * height > 400_000_000:
            raise CustomDemError(f"the raster has {width * height:,} cells, too many to hold; clip it to your area first")

        data = np.full((height, width), NODATA_OUT, dtype="float32")
        reproject(source=rasterio.band(src, 1), destination=data, src_transform=src.transform, src_crs=src.crs,
                  src_nodata=src.nodata, dst_transform=transform, dst_crs=dst_crs, dst_nodata=NODATA_OUT,
                  resampling=Resampling.bilinear)
    data[~np.isfinite(data)] = NODATA_OUT
    valid = data != NODATA_OUT
    if valid.mean() < MIN_VALID_FRACTION:
        raise CustomDemError(f"only {valid.mean() * 100:.1f}% of the raster has elevation values")
    zmin, zmax = float(data[valid].min()), float(data[valid].max())
    if zmin < -500 or zmax > 9000:
        raise CustomDemError(f"elevations run from {zmin:.0f} to {zmax:.0f}; expected metres between -500 and 9000. "
                             "Check the units (feet or centimetres) before uploading")

    tmp = os.path.join(CUSTOM_DEM_DIR, f"tmp_{os.getpid()}_{os.path.basename(src_path)}.tif")
    with rasterio.open(tmp, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
                       crs=dst_crs, transform=transform, nodata=NODATA_OUT, compress="deflate", tiled=True) as dst:
        dst.write(data, 1)
    dem_id = _file_hash(tmp)
    final = os.path.join(CUSTOM_DEM_DIR, f"{dem_id}.tif")
    os.replace(tmp, final)
    ob = transform_bounds(dst_crs, "EPSG:4326", *rasterio.open(final).bounds, densify_pts=21)
    return {
        "dem_id": dem_id, "crs": str(dst_crs), "cellsize_m": round(cellsize, 3), "ncols": width, "nrows": height,
        "bounds_wgs84": [round(v, 6) for v in ob], "min_m": round(zmin, 2), "max_m": round(zmax, 2),
        "valid_percent": round(float(valid.mean()) * 100, 1),
        "reprojected": bool(needs_reproject),
        "note": ("Reprojected to metres (" + str(dst_crs) + ") for the solver." if needs_reproject else "Used as supplied."),
    }
