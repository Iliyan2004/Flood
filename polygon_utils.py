"""
Polygon-to-DEM utilities for the Flood Information System.

LISFLOOD-FP only ever simulates a rectangle. An arbitrary user-drawn or
uploaded polygon is handled by: (1) cropping the source DEM to the
polygon's bounding box, (2) rasterizing the polygon as a mask and setting
NODATA outside it within that bounding box -- LISFLOOD-FP excludes NODATA
cells from computation, so this correctly simulates only the drawn shape
while still handing the solver a rectangular array.
"""
import hashlib
import json
import os

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_geom
import geopandas as gpd
from shapely.geometry import shape, mapping


def polygon_geometry_hash(geojson_geom):
    """Cache key = hash of the actual geometry, not just a bbox/lat-lon+size,
    since arbitrary polygons (not just boxes) are now allowed."""
    canonical = json.dumps(geojson_geom, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def load_polygon_from_upload(file_path):
    """Accepts a .geojson or a zipped shapefile (.zip containing .shp/.dbf/...).
    Returns (geojson_geometry_in_wgs84, area_km2)."""
    if file_path.lower().endswith(".zip"):
        gdf = gpd.read_file(f"zip://{file_path}")
    else:
        gdf = gpd.read_file(file_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")
    geom = gdf.geometry.unary_union  # dissolve multiple features into one AOI
    area_km2 = _geodesic_area_km2(geom)
    return mapping(geom), area_km2


def _geodesic_area_km2(geom_wgs84):
    gdf = gpd.GeoDataFrame(geometry=[geom_wgs84], crs="EPSG:4326")
    # equal-area projection for Pakistan's latitude band, good enough for a UI area readout
    area_m2 = gdf.to_crs("EPSG:6933").geometry.area.iloc[0]
    return area_m2 / 1e6


def polygon_area_km2(geojson_geom):
    return _geodesic_area_km2(shape(geojson_geom))


def crop_dem_to_polygon(src_dem_path, geojson_geom_wgs84, out_ascii_path, target_cellsize_m=None,
                         fallback_src_crs=None):
    """Crops src_dem_path (any CRS rasterio can read) to the polygon's bounding
    box, masks cells outside the polygon to NODATA, and writes an ARC ASCII
    file at out_ascii_path (the format flood_model.py expects). Returns the
    header dict and the number of active (non-NODATA) cells.

    fallback_src_crs: ARC ASCII grids carry no CRS metadata (unlike GeoTIFF,
    which embeds it) -- used only when src.crs is None."""
    with rasterio.open(src_dem_path) as src:
        src_crs = src.crs or fallback_src_crs
        if src_crs is None:
            raise ValueError(f"{src_dem_path} has no embedded CRS and no fallback_src_crs was given")
        geom_in_src_crs = transform_geom("EPSG:4326", src_crs, geojson_geom_wgs84)
        out_image, out_transform = rio_mask(src, [geom_in_src_crs], crop=True, nodata=-9999, filled=True)
        data = out_image[0].astype("float64")
        nrows, ncols = data.shape
        cellsize = out_transform.a
        xll = out_transform.c
        yll = out_transform.f + nrows * out_transform.e  # e is negative

    active_cells = int((data != -9999).sum())

    header = dict(ncols=ncols, nrows=nrows, xllcorner=xll, yllcorner=yll, cellsize=cellsize,
                  src_crs=str(src_crs))
    with open(out_ascii_path, "w") as f:
        f.write(f"ncols         {ncols}\n")
        f.write(f"nrows         {nrows}\n")
        f.write(f"xllcorner     {xll}\n")
        f.write(f"yllcorner     {yll}\n")
        f.write(f"cellsize      {cellsize}\n")
        f.write(f"NODATA_value  -9999\n")
        np.savetxt(f, data, fmt="%.4f")

    return header, active_cells, ncols * nrows
