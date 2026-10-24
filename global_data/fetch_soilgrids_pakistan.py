"""
Downloads real SoilGrids 2.0 (ISRIC, free, no registration -- confirmed
live via a direct HTTP range-request probe against files.isric.org)
surface-layer (0-5cm) soil texture rasters, clipped to Pakistan's real
bounding box, and saves them locally at 250m resolution reprojected to
EPSG:4326 -- small enough (~tens of MB per layer for a country-sized
clip) to genuinely "save here" the way the user asked, unlike a global
bulk download (each global 250m SoilGrids layer is multiple GB).

Real layers pulled: sand %, clay %, bulk density (bdod, cg/cm^3) -- the
minimum real inputs a published soil-texture-based infiltration/hydraulic
-conductivity lookup (Rawls et al. 1983) needs. See soil_utils.py for how
they're turned into a per-cell infiltration rate.

SoilGrids is served as VRTs over per-tile COGs in the Interrupted Goode
Homolosine projection -- opened here via a WarpedVRT so the windowed
read + reprojection to EPSG:4326 happens in one step, GDAL fetching only
the byte ranges the Pakistan window actually needs (no global download).
"""
import os

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.env import Env
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from rasterio.windows import from_bounds

SOILGRIDS_BASE = "https://files.isric.org/soilgrids/latest/data"
LAYERS = {
    "sand": f"{SOILGRIDS_BASE}/sand/sand_0-5cm_mean.vrt",
    "clay": f"{SOILGRIDS_BASE}/clay/clay_0-5cm_mean.vrt",
    "bdod": f"{SOILGRIDS_BASE}/bdod/bdod_0-5cm_mean.vrt",
}

# Real WGS84 bounding box of the Pakistan SRTM this app already has on
# disk (fis_app.PAKISTAN_SRTM_BOUNDS_WGS84) -- same coverage, so every
# Pakistan AOI this app can already simulate is covered by this clip too.
PAKISTAN_BBOX_WGS84 = (59.695013, 23.468104, 80.812387, 37.542288)
OUT_DIR = os.path.join(os.path.dirname(__file__), "soil_data")
GDAL_ENV = dict(GDAL_HTTP_UNSAFESSL="YES", CPL_VSIL_CURL_USE_HEAD="NO",
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", VSI_CACHE="TRUE")


def fetch_layer(name, url, bbox=PAKISTAN_BBOX_WGS84, dst_res_deg=0.0025):
    """Reprojects straight from the opened source VRT (`reproject(source=
    rasterio.band(src, 1), ...)`) into a destination array/transform built
    for the Pakistan bbox -- the same proven-fast pattern
    `damage_utils.fetch_lulc_for_bounds` already uses for ESA WorldCover,
    which lets GDAL's warp machinery pick the source COG's overview level
    appropriate to the requested output resolution instead of reading
    full-resolution pixels for the whole area. Two earlier attempts were
    slower for the wrong reason each: a WarpedVRT windowed read (window
    specified in the DESTINATION crs against a global-extent VRT) took
    10+ minutes with zero output; a manual native-CRS windowed `.read()`
    completed but was still reading full-res data for the whole country
    rather than letting GDAL downsample via overviews during the warp."""
    minx, miny, maxx, maxy = bbox
    out_path = os.path.join(OUT_DIR, f"pakistan_{name}.tif")
    print(f"[soilgrids] fetching {name} ({url})...", flush=True)

    dst_width = int(round((maxx - minx) / dst_res_deg))
    dst_height = int(round((maxy - miny) / dst_res_deg))
    dst_transform = rasterio.transform.from_origin(minx, maxy, dst_res_deg, dst_res_deg)
    dst_arr = np.zeros((dst_height, dst_width), dtype="int16")

    with Env(**GDAL_ENV):
        with rasterio.open(f"/vsicurl/{url}") as src:
            nodata = src.nodata
            reproject(
                source=rasterio.band(src, 1), destination=dst_arr,
                src_transform=src.transform, src_crs=src.crs, src_nodata=nodata,
                dst_transform=dst_transform, dst_crs="EPSG:4326", dst_nodata=nodata,
                resampling=Resampling.bilinear,
            )

    profile = dict(driver="GTiff", height=dst_height, width=dst_width, count=1, dtype=dst_arr.dtype,
                    crs="EPSG:4326", transform=dst_transform, nodata=nodata, compress="deflate")
    os.makedirs(OUT_DIR, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(dst_arr, 1)
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"[soilgrids] saved {out_path} ({dst_width}x{dst_height}, {size_mb:.1f}MB)", flush=True)
    return out_path


def main():
    for name, url in LAYERS.items():
        fetch_layer(name, url)
    print("[soilgrids] done.", flush=True)


if __name__ == "__main__":
    main()
