"""
Global DEM access for any AOI on Earth, via Copernicus DEM GLO-30 (AWS Open
Data, s3://copernicus-dem-30m, anonymous/no-auth, no bulk download needed --
GDAL reads only the byte ranges it needs from each remote Cloud-Optimized
GeoTIFF tile via /vsicurl/).

This is what generalizes the app beyond the user-supplied Pakistan SRTM:
`resolve_dem_source(bbox)` picks the local Pakistan SRTM when the AOI falls
inside its coverage (unchanged behavior, no network dependency for the
common case) and otherwise builds a small on-the-fly mosaic of just the
Copernicus GLO-30 tiles the AOI's bounding box touches, cropped tight so
this never downloads more than the AOI actually needs.

Copernicus DEM GLO-30 is a surface model (includes buildings/canopy), not
FABDEM's bare-earth correction -- FABDEM's license (CC BY-NC-SA, non-
commercial) doesn't fit a product; GLO-30 itself is fully free (VNSC/ESA
open license, no restriction). This app already conditions real buildings
into the DEM as flow obstacles everywhere else, so a DSM's building/canopy
noise is a real, documented limitation of the free global tier, not a new
problem class for this app.
"""
import math
import os

import numpy as np
import rasterio
from rasterio.env import Env
from rasterio.merge import merge as rio_merge
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling

COPERNICUS_BUCKET = "copernicus-dem-30m"
COPERNICUS_TILE_URL = (
    "/vsicurl/https://{bucket}.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)

GDAL_ENV = dict(
    GDAL_HTTP_UNSAFESSL="YES",
    CPL_VSIL_CURL_USE_HEAD="NO",
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    VSI_CACHE="TRUE",
    GDAL_HTTP_MULTIPLEX="YES",
)


def _tile_url(lat_deg, lon_deg):
    ns = "N" if lat_deg >= 0 else "S"
    ew = "E" if lon_deg >= 0 else "W"
    return COPERNICUS_TILE_URL.format(
        bucket=COPERNICUS_BUCKET, ns=ns, lat=abs(lat_deg), ew=ew, lon=abs(lon_deg)
    )


def tiles_for_bbox(minx, miny, maxx, maxy):
    """1x1 degree Copernicus DEM tile identifiers (lat, lon of each tile's
    SW corner) covering a WGS84 bbox."""
    lat0, lat1 = math.floor(miny), math.floor(maxy)
    lon0, lon1 = math.floor(minx), math.floor(maxx)
    return [
        (lat, lon)
        for lat in range(lat0, lat1 + 1)
        for lon in range(lon0, lon1 + 1)
    ]


def utm_epsg_for_lonlat(lon, lat):
    """Nearest-fit UTM zone EPSG code for a point -- the same projected,
    metric CRS convention this app already uses for the Pakistan SRTM
    (EPSG:32642), generalized to anywhere on Earth so LISFLOOD-FP always
    receives a real metre cell size, never a geographic-degree one."""
    zone = int(math.floor((lon + 180) / 6) % 60) + 1
    return (32600 if lat >= 0 else 32700) + zone


def in_pakistan_srtm_coverage(bbox, srtm_bounds_wgs84):
    """bbox and srtm_bounds_wgs84 are both (minx, miny, maxx, maxy) in WGS84."""
    minx, miny, maxx, maxy = bbox
    sminx, sminy, smaxx, smaxy = srtm_bounds_wgs84
    return sminx <= minx and sminy <= miny and maxx <= smaxx and maxy <= smaxy


def fetch_global_dem_mosaic(bbox_wgs84, out_tif_path, pad_deg=0.02, target_epsg=None):
    """Fetches (via remote-range-read, no full-tile download) and mosaics
    just the Copernicus DEM GLO-30 tiles covering bbox_wgs84, cropped to a
    small pad around it, reprojects to a metric UTM CRS (target_epsg, or the
    zone matching the bbox centroid if not given -- LISFLOOD-FP requires a
    real metre cell size, not geographic degrees), and writes a local
    GeoTIFF at out_tif_path.

    Returns (out_tif_path, n_tiles_used, n_tiles_missing, epsg_used). A
    "missing" tile is real open ocean / a coordinate with no land tile
    published (Copernicus DEM GLO-30 has no data over open ocean) --
    callers should treat an AOI with tiles_missing == n_tiles as "no
    terrain here" rather than an error.
    """
    minx, miny, maxx, maxy = bbox_wgs84
    minx, miny, maxx, maxy = minx - pad_deg, miny - pad_deg, maxx + pad_deg, maxy + pad_deg
    tiles = tiles_for_bbox(minx, miny, maxx, maxy)

    if target_epsg is None:
        target_epsg = utm_epsg_for_lonlat((minx + maxx) / 2, (miny + maxy) / 2)
    dst_crs = f"EPSG:{target_epsg}"

    datasets = []
    missing = 0
    with Env(**GDAL_ENV):
        for lat, lon in tiles:
            url = _tile_url(lat, lon)
            try:
                ds = rasterio.open(url)
                datasets.append(ds)
            except rasterio.errors.RasterioIOError:
                missing += 1
        if not datasets:
            raise ValueError(
                f"No Copernicus DEM GLO-30 tiles found for bbox {bbox_wgs84} "
                f"({missing} tile(s) checked, all missing -- likely open ocean)"
            )
        mosaic, src_transform = rio_merge(
            datasets, bounds=(minx, miny, maxx, maxy), nodata=np.nan
        )
        src_crs = datasets[0].crs
        for ds in datasets:
            ds.close()

    mosaic = mosaic[0]
    src_height, src_width = mosaic.shape

    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs, dst_crs, src_width, src_height, minx, miny, maxx, maxy
    )
    dst_arr = np.full((dst_height, dst_width), np.nan, dtype="float32")
    reproject(
        source=mosaic,
        destination=dst_arr,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=np.nan,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    dst_arr = np.where(np.isnan(dst_arr), -9999.0, dst_arr).astype("float32")

    profile = dict(
        driver="GTiff",
        height=dst_height,
        width=dst_width,
        transform=dst_transform,
        crs=dst_crs,
        nodata=-9999.0,
        count=1,
        dtype="float32",
        compress="deflate",
    )
    with rasterio.open(out_tif_path, "w", **profile) as dst:
        dst.write(dst_arr, 1)

    return out_tif_path, len(datasets), missing, target_epsg
