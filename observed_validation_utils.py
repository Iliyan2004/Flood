"""Compares a finished run's modelled wet extent with an OBSERVED flood extent the user uploads.

Observed extent: a polygon file (.geojson/.json/.gpkg/.shp or zipped shapefile; every polygon = flooded) or a
GeoTIFF (cells > 0 = flooded, nodata ignored). It is put onto the run's own grid, and only cells that are
real terrain inside the area of interest are scored (outside-AOI cells would otherwise count as correct
'dry'). Metrics are the standard categorical flood scores: CSI, POD, FAR, bias, F1.
"""
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from shapely.geometry import mapping
from shapely.ops import unary_union
from rasterio.warp import transform_geom
import geopandas as gpd

VECTOR_EXT = (".geojson", ".json", ".gpkg", ".shp", ".zip")
RASTER_EXT = (".tif", ".tiff")


def grid_transform(header):
    nrows, cs = int(header["nrows"]), header["cellsize"]
    return from_origin(header["xllcorner"], header["yllcorner"] + nrows * cs, cs, cs)


def observed_mask_on_grid(path, header, grid_crs):
    """Boolean (nrows, ncols) observed-wet mask on the run grid, plus a bool of cells the observation covers."""
    nrows, ncols = int(header["nrows"]), int(header["ncols"])
    transform = grid_transform(header)
    low = path.lower()
    if low.endswith(RASTER_EXT):
        with rasterio.open(path) as src:
            dst = np.full((nrows, ncols), np.nan, dtype="float32")
            reproject(source=rasterio.band(src, 1), destination=dst, dst_transform=transform, dst_crs=grid_crs,
                      resampling=Resampling.nearest, dst_nodata=np.nan)
            if src.nodata is not None:
                dst[dst == src.nodata] = np.nan
        covered = ~np.isnan(dst)
        return covered & (np.nan_to_num(dst, nan=0.0) > 0), covered
    if low.endswith(VECTOR_EXT):
        if low.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(path) as z:
                shp = [n for n in z.namelist() if n.lower().endswith(".shp")]
            gdf = gpd.read_file(f"zip://{path}!{shp[0]}" if shp else f"zip://{path}")
        else:
            gdf = gpd.read_file(path)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        geoms = [g for g in gdf.geometry if g is not None and not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon")]
        if not geoms:
            raise ValueError("the file has no polygon features (a flooded area must be drawn as polygons)")
        merged = unary_union(geoms)
        g_run = transform_geom(str(gdf.crs), grid_crs, mapping(merged))
        wet = rasterize([(g_run, 1)], out_shape=(nrows, ncols), transform=transform, fill=0, dtype="uint8").astype(bool)
        return wet, np.ones((nrows, ncols), dtype=bool)
    raise ValueError("unsupported file type; use a GeoTIFF (.tif) or polygons (.geojson, .gpkg, .shp, zipped shapefile)")


def score_extent(model_wet, observed_wet, valid, cell_area_km2):
    """Categorical scores over valid cells. Returns plain-float dict; ratios are None when undefined."""
    m, o = model_wet & valid, observed_wet & valid
    hits = int((m & o).sum()); misses = int((~m & o).sum()); fa = int((m & ~o).sum()); cn = int((~m & ~o & valid).sum())

    def ratio(a, b):
        return round(a / b, 4) if b > 0 else None
    return {
        "hits": hits, "misses": misses, "false_alarms": fa, "correct_negatives": cn,
        "CSI": ratio(hits, hits + misses + fa), "POD": ratio(hits, hits + misses), "FAR": ratio(fa, hits + fa),
        "F1": ratio(2 * hits, 2 * hits + misses + fa),
        "bias": ratio(hits + fa, hits + misses),
        "model_wet_km2": round(int(m.sum()) * cell_area_km2, 4), "observed_wet_km2": round(int(o.sum()) * cell_area_km2, 4),
        "scored_cells": int(valid.sum()),
    }
