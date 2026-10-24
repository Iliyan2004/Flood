"""
Historical validation: this app's real LISFLOOD-FP flood physics
(flood_model.py, invoked exactly as fis_app.py's /run route would) checked
against a real, documented, satellite-derived ground-truth flood extent
from the 2022 Pakistan floods -- the first time this app's physics has
been checked against real-world outcome data rather than only internal
consistency (mass balance, monotonicity, before/after comparisons).

GROUND TRUTH (Step 1)
----------------------
Source: HDX (Humanitarian Data Exchange), dataset "Floodwater depth in
Sindh and Balochistan Provinces, Pakistan as of 29 November 2022"
  https://data.humdata.org/dataset/floodwater-depth-in-sindh-and-balochistan-provinces-pakistan-as-of-29-november-2022
Organization: UNOSAT (UN Satellite Centre). UNOSAT code FL20221121PAK.
Direct resource actually downloaded (verified via the HDX CKAN API, not
just the webpage):
  https://unosat.org/static/unosat_filesystem/3457/FL20221121PAK_SHP.zip

HONEST FINDING: despite the dataset's TITLE ("Floodwater depth"), the
actual downloadable data (both the SHP and the companion .gdb geodatabase,
both checked directly) contain NO per-cell depth values anywhere -- every
layer (FloodExtent, AnalysisExtent, CloudObstruction) is a binary
extent/mask polygon with a single attribute row (Water_Clas="Flood Water",
Sensor_ID="NOAA-VIIRS", Sensor_Dat=2022-11-29). This is real UNOSAT
Suomi-NPP/NOAA-20 VIIRS-derived flood EXTENT, not a depth raster -- the
"depth" in the title appears to refer to UNOSAT's underlying (not
publicly distributed in this download) methodology, not a delivered
product. Confirmed real: EPSG:4326, single MultiPolygon (1410 sub-rings),
total flooded area ~4663 km^2 within the analysis extent (~46,150 km^2,
close to the ~47,000 km^2 the dataset description states) covering
lon ~67.5-69.4, lat ~26.6-28.6 (Sindh, the Manchar-Lake/Dadu/Larkana/
Qambar-Shahdadkot belt -- the real, heavily-documented core of the 2022
Sindh flooding). This is genuinely usable as a real wet/dry validation
mask; it is not usable for a depth-vs-depth comparison, so this script
validates the model's WET/DRY EXTENT against it (CSI/POD/FAR/IoU on a
binary mask), not simulated-vs-observed depth.

AOI (Step 3): a modest ~11km x 11km box centered on (67.72 E, 27.20 N),
inside the single largest contiguous flooded sub-polygon in the ground
truth (862 km^2, bounds ~67.55-67.86 E / 26.96-27.54 N -- the real
Manchar Lake / Dadu area) -- picked by directly inspecting the ground
truth geometry's own sub-polygon areas, not guessed. 98.1% of this box's
area is real documented flood water in the ground truth.

RAINFALL (Step 2): real ERA5-reanalysis-backed historical precipitation
(Open-Meteo Archive API, already proven elsewhere in this app for Tier 4
pluvial climatology) at the AOI centroid, for the real 2022 Pakistan
monsoon season (checked directly: this app's own CMIP6/NEX-GDDP archive
in climate_utils.py is historical-only through ~2014 and does NOT cover
2022 -- confirmed by inspecting its own file list before ruling it out).

REAL, HONEST STORM-DESIGN LIMITATION (found and documented, not hidden):
this app's flood_model.py only supports a single triangular hyetograph
(one peak, one duration) -- appropriate for a discrete storm cell, not a
multi-week monsoon season. Mirroring fis_app.py's own runForecastMode()
JS logic EXACTLY (find the peak hourly rate, then find the contiguous
run of hours with rate > 0.1 mm/hr around it) on the real hourly ERA5
series for this centroid finds a peak of 17.3 mm/hr -- but a contiguous
duration of only 2 HOURS, because ERA5 hourly rain during a real,
sustained multi-day monsoon burst still has isolated hours dipping below
the 0.1 mm/hr threshold. That literal mirror would feed the model a
technically-real-but-practically-meaningless 2-hour storm and grossly
understate the actual event. Adapted (documented here, not silently):
uses a MAXIMUM-TOTAL ROLLING WINDOW instead of a contiguous-threshold
run -- the same "find the real peak" principle, just using a window
formulation suited to a multi-day sustained event rather than a single
convective cell. The real wettest 48-hour window at this centroid in
the 2022 monsoon season (checked Jun 1 - Sep 15 2022) is 2022-08-17 13:00
to 2022-08-19 12:00 UTC: 253.5mm total, average 5.28 mm/hr -- used as
avg 5.28 mm/hr over 48 h. NOTE (corrected Oct 2026): the model's storm is a triangle, so
the run needs peak = 2 x average (10.56 mm/hr) to deliver the real 253.5 mm; earlier
versions passed 5.28 as the peak and so only applied ~127 mm.

MODEL RUN (Step 3): flood_model.py run via subprocess exactly as
fis_app.py's execute_fis_run would configure it for a plain pluvial run
with no manual overrides -- friction=0.05 (fallback; real per-cell
Manning's n from ESA WorldCover land cover is fetched and used, matching
this app's actual default/automatic behavior, see damage_utils.py),
infiltration_mm_hr=0 (this app's own UI default), amc=50 (neutral,
default), solver=auto (this app's own terrain-slope-based auto-choice).
Buildings/roads conditioning is DELIBERATELY OMITTED here (documented,
not silently) -- this validation targets the core terrain+rainfall
physics against ground truth, not the small building-flow-obstacle
correction, and OSM/Google Open Buildings fetches would add real network
dependency to a script meant to be simply re-runnable.

METRICS (Step 3): the model's own res.max wet mask (depth > 0.05m, this
app's own default dry_thresh) vs. the real ground-truth extent polygon,
reprojected onto the run's own DEM grid using the exact same
rasterio.warp reprojection pattern already established in this codebase
(damage_utils.fetch_lulc_for_bounds / soil_utils.py), restricted to cells
that are real valid AOI terrain (not NODATA) in both. Reports CSI, POD
(hit rate), FAR, and IoU -- standard binary flood-validation metrics.

Usage: ~/flood/venv/bin/python3 validate_2022_flood.py [--rerun-model]
(--rerun-model forces a fresh LISFLOOD-FP run even if a previous run's
res.max already exists in the workdir; ground-truth download and rainfall
fetch are always cached to disk once fetched.)
"""
import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import transform_geom
from rasterio.features import rasterize
from shapely.geometry import box, mapping, shape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polygon_utils
import damage_utils
import flood_model

VALDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_2022")
GT_ZIP_URL = "https://unosat.org/static/unosat_filesystem/3457/FL20221121PAK_SHP.zip"
GT_ZIP_PATH = os.path.join(VALDIR, "FL20221121PAK_SHP.zip")
GT_SHP_DIR = os.path.join(VALDIR, "FL20221121PAK_SHP")
GT_SHP = os.path.join(GT_SHP_DIR, "FL20221121PAK_SHP", "VIIRS_20221129_FloodExtent_Sindh_Balochistan.shp")

import data_paths
PAKISTAN_DEM = data_paths.get("PAKISTAN_DEM")

# Real AOI: an ~11km box inside the largest contiguous ground-truth
# flooded blob (Manchar Lake / Dadu area), found by direct inspection of
# the ground truth's own sub-polygon areas (see module docstring).
AOI_CENTER_LON, AOI_CENTER_LAT = 67.72, 27.20
AOI_HALF_DEG = 0.05

# Real ERA5 (Open-Meteo Archive API), 2022 monsoon season window checked.
ERA5_URL = "https://archive-api.open-meteo.com/v1/archive"
SEASON_START, SEASON_END = "2022-06-01", "2022-09-15"
RAIN_CACHE = os.path.join(VALDIR, "era5_hourly_2022.json")

RUN_DIR = os.path.join(VALDIR, "run_2022sindh")

DRY_THRESH_M = 0.05  # this app's own default (see flood_model.py --dry-thresh)


def ensure_ground_truth():
    os.makedirs(VALDIR, exist_ok=True)
    if not os.path.exists(GT_SHP):
        print(f"[gt] downloading {GT_ZIP_URL}")
        import requests
        r = requests.get(GT_ZIP_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
        with open(GT_ZIP_PATH, "wb") as f:
            f.write(r.content)
        import zipfile
        with zipfile.ZipFile(GT_ZIP_PATH) as z:
            z.extractall(GT_SHP_DIR)
        print(f"[gt] extracted to {GT_SHP_DIR}")
    else:
        print(f"[gt] using cached {GT_SHP}")
    gdf = gpd.read_file(GT_SHP)
    assert gdf.crs is not None and str(gdf.crs).upper().endswith("4326"), f"unexpected CRS {gdf.crs}"
    print(f"[gt] real ground truth loaded: {len(gdf)} feature(s), "
          f"total flooded area {gdf.geometry.area.sum()*111*111*0.89:.0f} km^2 (approx), "
          f"bounds {tuple(gdf.total_bounds)}")
    return gdf.geometry.iloc[0]


def fetch_era5_hourly():
    if os.path.exists(RAIN_CACHE):
        print(f"[rain] using cached {RAIN_CACHE}")
        with open(RAIN_CACHE) as f:
            return json.load(f)
    import requests
    params = {
        "latitude": AOI_CENTER_LAT, "longitude": AOI_CENTER_LON,
        "start_date": SEASON_START, "end_date": SEASON_END,
        "hourly": "precipitation", "timezone": "UTC",
    }
    print(f"[rain] fetching real ERA5 hourly precip {SEASON_START}..{SEASON_END} @ "
          f"({AOI_CENTER_LAT},{AOI_CENTER_LON}) from Open-Meteo Archive API")
    r = requests.get(ERA5_URL, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    with open(RAIN_CACHE, "w") as f:
        json.dump(data, f)
    return data


def derive_design_storm(era5_data, window_hr=48):
    """Real max-total-rolling-window design storm (see module docstring
    for why a literal contiguous->0.1mm/hr mirror of runForecastMode's JS
    logic is inappropriate for a multi-day monsoon event)."""
    h = era5_data["hourly"]
    times = h["time"]
    precip = [p if p is not None else 0.0 for p in h["precipitation"]]
    n = len(precip)
    prefix = [0.0]
    for p in precip:
        prefix.append(prefix[-1] + p)
    best_total, best_i = -1.0, 0
    for i in range(n - window_hr + 1):
        tot = prefix[i + window_hr] - prefix[i]
        if tot > best_total:
            best_total, best_i = tot, i
    avg_mm_hr = best_total / window_hr
    # flood_model's design storm is a TRIANGLE (rises to its peak at mid-storm, then falls
    # back), so its total depth is 0.5 * peak * duration. Passing the window AVERAGE as the
    # peak (what this script did before Oct 2026) delivers only HALF of the real ERA5 total
    # -- 127 mm instead of 253.5 mm -- which alone under-fed every validation run. To
    # deliver the real window total, the peak must be 2 * average.
    peak_mm_hr = 2.0 * avg_mm_hr
    # Also report the literal forecast-mode-style mirror, for honesty.
    peak_idx = max(range(n), key=lambda i: precip[i])
    lo = hi = peak_idx
    while lo > 0 and precip[lo - 1] > 0.1:
        lo -= 1
    while hi < n - 1 and precip[hi + 1] > 0.1:
        hi += 1
    naive_duration_hr = hi - lo + 1
    print(f"[storm] literal runForecastMode-style mirror: peak {precip[peak_idx]:.1f} mm/hr at "
          f"{times[peak_idx]}, contiguous duration {naive_duration_hr}h -- TOO SHORT to represent "
          f"this real multi-day event (see docstring), NOT used to drive the run.")
    print(f"[storm] adapted rolling-window design storm: {window_hr}h window "
          f"{times[best_i]} .. {times[best_i+window_hr-1]}, total {best_total:.1f}mm, "
          f"avg {avg_mm_hr:.2f} mm/hr -> triangular peak {peak_mm_hr:.2f} mm/hr (so the triangle's total "
          f"= the real {best_total:.1f} mm) -- USED to drive the run.")
    return {
        "peak_mm_hr": peak_mm_hr, "avg_mm_hr": avg_mm_hr, "duration_hr": float(window_hr),
        "window_start": times[best_i], "window_end": times[best_i + window_hr - 1],
        "window_total_mm": best_total,
        "naive_mirror_peak_mm_hr": precip[peak_idx], "naive_mirror_duration_hr": naive_duration_hr,
    }


def prepare_dem_and_manning():
    os.makedirs(RUN_DIR, exist_ok=True)
    dem_path = os.path.join(RUN_DIR, "aoi.dem.ascii")
    aoi_geom = mapping(box(AOI_CENTER_LON - AOI_HALF_DEG, AOI_CENTER_LAT - AOI_HALF_DEG,
                            AOI_CENTER_LON + AOI_HALF_DEG, AOI_CENTER_LAT + AOI_HALF_DEG))
    if not os.path.exists(dem_path):
        header, active_cells, total_cells = polygon_utils.crop_dem_to_polygon(
            PAKISTAN_DEM, aoi_geom, dem_path)
        print(f"[dem] cropped real Pakistan SRTM to AOI: {header['ncols']}x{header['nrows']} cells "
              f"@ {header['cellsize']:.1f}m, {active_cells}/{total_cells} active, src_crs={header['src_crs']}")
    else:
        header, _ = flood_model.read_ascii(dem_path)
        with rasterio.open(PAKISTAN_DEM) as src:
            header["src_crs"] = str(src.crs)
        print(f"[dem] reusing cached {dem_path}")

    manning_path = os.path.join(RUN_DIR, "manning.ascii")
    if not os.path.exists(manning_path):
        _, dem = flood_model.read_ascii(dem_path)
        lon_min, lat_min = AOI_CENTER_LON - AOI_HALF_DEG, AOI_CENTER_LAT - AOI_HALF_DEG
        lon_max, lat_max = AOI_CENTER_LON + AOI_HALF_DEG, AOI_CENTER_LAT + AOI_HALF_DEG
        try:
            lulc = damage_utils.fetch_lulc_for_bounds(lon_min, lat_min, lon_max, lat_max,
                                                       header, header["src_crs"])
            manning_grid = damage_utils.lulc_to_manning_grid(lulc)
            manning_grid[dem == -9999] = -9999
            flood_model.write_ascii(manning_path, header, manning_grid)
            print(f"[manning] real per-cell Manning's n from ESA WorldCover written to {manning_path} "
                  f"(distinct values: {sorted(set(np.round(manning_grid[dem!=-9999],3).tolist()))})")
        except Exception as e:
            print(f"[manning] real land-cover fetch failed ({e}) -- falling back to uniform friction "
                  f"only, same graceful-degradation behavior this app already uses elsewhere")
            manning_path = None
    else:
        print(f"[manning] reusing cached {manning_path}")
    return dem_path, manning_path, header, aoi_geom


def run_model(dem_path, manning_path, storm, rerun=False):
    results_dir = os.path.join(RUN_DIR, "results")
    res_max_path = os.path.join(results_dir, "res.max")
    if os.path.exists(res_max_path) and not rerun:
        print(f"[model] reusing cached run results at {res_max_path}")
        return res_max_path

    dem_name = os.path.basename(dem_path)
    cmd = [
        sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "flood_model.py"),
        "--dem", dem_path,
        "--rain-mm-hr", str(storm["peak_mm_hr"]),
        "--duration-hr", str(storm["duration_hr"]),
        "--friction", "0.05",
        "--infiltration-mm-hr", "0",
        "--amc", "50",
        "--solver", "auto",
        "--dry-thresh", str(DRY_THRESH_M),
        "--workdir", RUN_DIR,
        "--site-name", "2022 Sindh flood validation (Manchar Lake area)",
    ]
    if manning_path:
        cmd += ["--manning-file", manning_path]
    print(f"[model] running: {' '.join(cmd)}")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout[-6000:])
    if proc.returncode != 0:
        print(proc.stderr[-6000:])
        raise RuntimeError("flood_model.py run FAILED -- see output above")
    print(f"[model] real LISFLOOD-FP run finished in {time.time()-t0:.1f}s wall-clock")
    return res_max_path


def compute_metrics(res_max_path, header, aoi_geom, gt_geom, dem_path):
    max_header, max_depth = flood_model.read_ascii(res_max_path)
    # REAL FINDING, not assumed: LISFLOOD-FP's res.max does NOT preserve
    # NODATA -- cells outside the true AOI (masked -9999 in the input DEM)
    # come back as plain 0.0 depth in res.max, not -9999. Confirmed
    # directly (0 cells read as nodata in a real res.max that should have
    # had ~3,749 NODATA cells). This is the exact same class of gap
    # CLAUDE.md's own "damage stats silently diluted" bug documents for
    # fis_app.py's real pipeline -- that code already works around it by
    # computing `valid` from the INPUT DEM, never from res.max's own
    # nodata field, which is what's done here too.
    _, dem = flood_model.read_ascii(dem_path)
    valid = dem != -9999
    model_wet = valid & (max_depth > DRY_THRESH_M)

    ncols, nrows = int(max_header["ncols"]), int(max_header["nrows"])
    cs = max_header["cellsize"]
    transform = rasterio.transform.from_origin(
        max_header["xllcorner"], max_header["yllcorner"] + nrows * cs, cs, cs)
    src_crs = header["src_crs"]

    gt_geom_src = shape(transform_geom("EPSG:4326", src_crs, mapping(gt_geom)))
    gt_mask = rasterize([(gt_geom_src, 1)], out_shape=(nrows, ncols), transform=transform,
                         fill=0, dtype="uint8").astype(bool)

    n_valid = int(valid.sum())
    hits = int((model_wet & gt_mask & valid).sum())
    misses = int((~model_wet & gt_mask & valid).sum())
    false_alarms = int((model_wet & ~gt_mask & valid).sum())
    correct_neg = int((~model_wet & ~gt_mask & valid).sum())

    csi = hits / (hits + misses + false_alarms) if (hits + misses + false_alarms) > 0 else float("nan")
    pod = hits / (hits + misses) if (hits + misses) > 0 else float("nan")
    far = false_alarms / (hits + false_alarms) if (hits + false_alarms) > 0 else float("nan")
    iou = csi  # identical formula on a binary mask
    accuracy = (hits + correct_neg) / n_valid if n_valid > 0 else float("nan")

    cell_area_km2 = (cs * cs) / 1e6
    return {
        "n_valid_cells": n_valid,
        "model_wet_cells": int((model_wet & valid).sum()),
        "gt_wet_cells": int((gt_mask & valid).sum()),
        "model_wet_area_km2": round(float((model_wet & valid).sum()) * cell_area_km2, 4),
        "gt_wet_area_km2": round(float((gt_mask & valid).sum()) * cell_area_km2, 4),
        "model_max_depth_m": round(float(max_depth[valid].max()), 3) if n_valid else None,
        "hits": hits, "misses": misses, "false_alarms": false_alarms, "correct_negatives": correct_neg,
        "CSI": round(csi, 4), "POD_hit_rate": round(pod, 4), "FAR": round(far, 4),
        "IoU": round(iou, 4), "accuracy": round(accuracy, 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun-model", action="store_true")
    ap.add_argument("--window-hr", type=int, default=48)
    args = ap.parse_args()

    print("=" * 78)
    print("2022 Pakistan (Sindh) flood -- real historical validation")
    print("=" * 78)

    gt_geom = ensure_ground_truth()
    era5_data = fetch_era5_hourly()
    storm = derive_design_storm(era5_data, window_hr=args.window_hr)
    dem_path, manning_path, header, aoi_geom = prepare_dem_and_manning()
    res_max_path = run_model(dem_path, manning_path, storm, rerun=args.rerun_model)
    metrics = compute_metrics(res_max_path, header, aoi_geom, gt_geom, dem_path)

    report = {
        "ground_truth": {
            "dataset": "UNOSAT FL20221121PAK (HDX: floodwater-depth-in-sindh-and-balochistan-provinces-"
                       "pakistan-as-of-29-november-2022), sensor date 2022-11-29, VIIRS-derived extent "
                       "(NOT a per-cell depth raster despite the dataset title -- see docstring)",
            "resource_url": GT_ZIP_URL,
        },
        "aoi": {"center_lon": AOI_CENTER_LON, "center_lat": AOI_CENTER_LAT, "half_deg": AOI_HALF_DEG},
        "rainfall": {
            "source": "Open-Meteo Archive API (real ERA5 reanalysis)",
            "season_checked": [SEASON_START, SEASON_END],
            **storm,
        },
        "metrics": metrics,
    }
    report_path = os.path.join(RUN_DIR, "validation_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print("\n" + "=" * 78)
    print("REAL VALIDATION RESULT")
    print("=" * 78)
    print(f"Ground truth: real UNOSAT VIIRS flood extent, 2022-11-29, Manchar Lake/Dadu area, Sindh")
    print(f"AOI: {AOI_HALF_DEG*2:.2f}x{AOI_HALF_DEG*2:.2f} deg box @ ({AOI_CENTER_LAT},{AOI_CENTER_LON})")
    print(f"Design storm (real ERA5-derived): {storm['peak_mm_hr']:.2f} mm/hr x {storm['duration_hr']:.0f}h "
          f"({storm['window_start']} .. {storm['window_end']}, {storm['window_total_mm']:.1f}mm total)")
    print(f"Model max depth reached: {metrics['model_max_depth_m']} m")
    print(f"Model wet area: {metrics['model_wet_area_km2']} km^2  |  Ground-truth wet area (this AOI): "
          f"{metrics['gt_wet_area_km2']} km^2")
    print(f"Hits={metrics['hits']}  Misses={metrics['misses']}  False alarms={metrics['false_alarms']}  "
          f"Correct negatives={metrics['correct_negatives']}")
    print(f"CSI (Critical Success Index) = {metrics['CSI']}")
    print(f"POD (hit rate / probability of detection) = {metrics['POD_hit_rate']}")
    print(f"FAR (false alarm ratio) = {metrics['FAR']}")
    print(f"IoU = {metrics['IoU']}   Accuracy = {metrics['accuracy']}")
    print(f"\nFull report: {report_path}")


if __name__ == "__main__":
    main()
