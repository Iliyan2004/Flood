"""
Snow + glacier melt hydrograph for a basin, built on hydro_model.py.

hydro_model.py is a daily, pixel-distributed snow / glacier-ice / soil / lake model driven by
ERA5 temperature and precipitation. This module prepares its inputs for ONE basin from the data
already on the shared drive, runs it, and turns the routed outlet discharge into the
{"times_hr", "q_m3s"} series that flood_model.py accepts as a boundary inflow.

Inputs used (all local):
  * Pakistan SRTM DEM (data_paths PAKISTAN_DEM), reprojected to the basin's UTM zone and
    depression-filled (pysheds) -- the model derives its D8 routing from it
  * PMD glacier + lake inventories (data_paths PMD_GLACIER_SHP / PMD_LAKES_SHP)
  * hourly ERA5-Land 2 m temperature and total precipitation (zarr cubes, 1980 -> May 2026)

Honest limits (also returned to the caller):
  * daily timestep: seasonal melt and basin discharge are meaningful, an outburst-flood peak is not
  * no glacier thickness raster: every glacier pixel gets the uniform default thickness
  * ERA5 precipitation is not corrected for the mountain under-catch (precip_correction = 1)
  * temperature is lapse-rate corrected from each ERA5 cell's mean DEM height (Config.lapse_rate_c_per_m)
"""
import json
import math
import os
import subprocess
import sys

import numpy as np

import data_paths

HERE = os.path.dirname(os.path.abspath(__file__))
HYDRO_MODEL = os.path.join(HERE, "hydro_model.py")

DEFAULT_MAX_PIXELS = 40000        # python routing loop is per pixel per day
ERA_PAD_DEG = 0.3
LIMITATIONS = [
    "Daily timestep: seasonal melt and basin discharge are meaningful; an outburst-flood peak is not "
    "(use the GLOF tool for that).",
    "Ice thickness is IceBoost v2.0 MODELLED thickness and debris is a melt-factor map, not field data; "
    "glacier-inventory pixels the thickness map has no ice for are treated as ice-free.",
    "ERA5 precipitation and temperature are not bias-corrected unless you set the factors (use Calibrate).",
    "Degree-day factors are generic (snow 4, ice 8 mm w.e. per degC per day) unless calibrated for this basin.",
]


def _utm_epsg(lon, lat):
    zone = int(math.floor((lon + 180) / 6) % 60) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _clip_glacier_rasters(workdir, poly_wgs84, transform, nrows, ncols, epsg):
    """Average IceBoost ice thickness and the debris melt factor onto the basin's model grid, over glacier
    ice only (thickness > 0): a 260 m model cell that is half glacier gets the mean thickness of its ice,
    not half of it. Returns {"thickness": path, "debris": path, "meta": {...}}; empty if data is missing."""
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds
    tpath, dpath = data_paths.get("GLACIER_THICKNESS_TIF"), data_paths.get("DEBRIS_FACTOR_TIF")
    if not (os.path.exists(tpath) and os.path.exists(dpath)):
        return {}
    out = {"meta": {}}
    with rasterio.open(tpath) as ts, rasterio.open(dpath) as ds:
        b = transform_bounds("EPSG:4326", ts.crs, *poly_wgs84.bounds)
        pad = 3 * abs(transform.a)
        w = from_bounds(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad, ts.transform)
        w = w.intersection(rasterio.windows.Window(0, 0, ts.width, ts.height))
        if w.width < 1 or w.height < 1:
            return {}
        w = w.round_offsets().round_lengths()
        thick = ts.read(1, window=w).astype(np.float32)
        deb = ds.read(1, window=w).astype(np.float32)
        wt = ts.window_transform(w)
        ice = (thick != ts.nodata) & np.isfinite(thick) & (thick > 0)
        thick = np.where(ice, thick, np.nan)
        deb = np.where(ice & (deb != ds.nodata) & np.isfinite(deb), deb, np.nan)
        prof = dict(driver="GTiff", height=nrows, width=ncols, count=1, dtype="float32", crs=f"EPSG:{epsg}",
                    transform=transform, nodata=np.nan, compress="deflate")
        for key, arr in (("thickness", thick), ("debris", deb)):
            dst = np.full((nrows, ncols), np.nan, np.float32)
            reproject(arr, dst, src_transform=wt, src_crs=ts.crs, src_nodata=np.nan, dst_transform=transform,
                      dst_crs=f"EPSG:{epsg}", dst_nodata=np.nan, resampling=Resampling.average)
            path = os.path.join(workdir, f"glacier_{key}.tif")
            with rasterio.open(path, "w", **prof) as f:
                f.write(dst, 1)
            out[key] = path
            v = dst[np.isfinite(dst)]
            out["meta"][key] = (dict(cells=int(v.size), mean=round(float(v.mean()), 3),
                                     p10=round(float(np.percentile(v, 10)), 3),
                                     p90=round(float(np.percentile(v, 90)), 3)) if v.size else dict(cells=0))
    out["meta"]["source"] = "IceBoost v2.0 thickness (RGI v7 region 14) + debris melt factor, 26.6 m"
    return out


def prepare_inputs(geom_wgs84, start, end, workdir, max_pixels=DEFAULT_MAX_PIXELS, params_extra=None):
    """Write DEM / subbasin / glacier / lake / ERA5 subsets and params.json into `workdir`.
    `geom_wgs84`: GeoJSON polygon of the basin. `start`/`end`: 'YYYY-MM-DD' (local days).
    Returns the params dict (also saved as workdir/params.json)."""
    import geopandas as gpd
    import pandas as pd
    import rasterio
    import xarray as xr
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    import pyproj

    os.makedirs(workdir, exist_ok=True)
    poly = shape(geom_wgs84)
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = poly.centroid.x, poly.centroid.y
    epsg = _utm_epsg(cx, cy)
    to_utm = pyproj.Transformer.from_crs(4326, epsg, always_xy=True).transform
    poly_utm = shp_transform(to_utm, poly)

    # cell size: coarsest of (30 m SRTM native, whatever keeps the pixel count manageable)
    cell = max(30.0, round(math.sqrt(poly_utm.area / max_pixels) / 10.0) * 10.0)
    bx0, by0, bx1, by1 = poly_utm.bounds
    pad = 2 * cell
    x0, y1 = math.floor((bx0 - pad) / cell) * cell, math.ceil((by1 + pad) / cell) * cell
    ncols = int(math.ceil((bx1 + pad - x0) / cell))
    nrows = int(math.ceil((y1 - (by0 - pad)) / cell))
    transform = from_origin(x0, y1, cell, cell)

    # DEM -> UTM grid
    dem_src = data_paths.get("PAKISTAN_DEM")
    if not os.path.exists(dem_src):
        raise FileNotFoundError(f"Pakistan DEM not found: {dem_src}")
    dem = np.full((nrows, ncols), np.nan, np.float32)
    with rasterio.open(dem_src) as src:
        reproject(rasterio.band(src, 1), dem, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs=f"EPSG:{epsg}", resampling=Resampling.bilinear,
                  src_nodata=src.nodata, dst_nodata=np.nan)
    inside = rasterize([(poly_utm, 1)], out_shape=(nrows, ncols), transform=transform, fill=0, dtype="uint8")
    if not np.isfinite(dem[inside > 0]).any():
        raise ValueError("Basin lies outside the Pakistan DEM coverage")
    # fill holes inside the basin so D8 routing has no gaps
    bad = ~np.isfinite(dem)
    if bad.any():
        from scipy.ndimage import distance_transform_edt
        _, (iy, ix) = distance_transform_edt(bad, return_indices=True)
        dem = dem[iy, ix]

    dem_raw = os.path.join(workdir, "dem_raw.tif")
    prof = dict(driver="GTiff", height=nrows, width=ncols, count=1, dtype="float32",
                crs=f"EPSG:{epsg}", transform=transform, nodata=-9999.0, compress="deflate")
    with rasterio.open(dem_raw, "w", **prof) as dst:
        dst.write(dem, 1)

    # depression-filled copy (routing needs a DEM that drains)
    if not hasattr(np, "in1d"):
        np.in1d = np.isin                      # pysheds 0.5 vs numpy 2.x
    from pysheds.grid import Grid
    grid = Grid.from_raster(dem_raw)
    r = grid.read_raster(dem_raw)
    filled = grid.resolve_flats(grid.fill_depressions(grid.fill_pits(r)))
    dem_filled = os.path.join(workdir, "dem_filled.tif")
    with rasterio.open(dem_filled, "w", **prof) as dst:
        dst.write(np.asarray(filled, np.float32), 1)

    # D8 flow direction straight from pysheds (ESRI codes) on the filled DEM -- handles flats properly
    fdir = np.asarray(grid.flowdir(filled), np.int64)
    fdir = np.where(np.isin(fdir, (1, 2, 4, 8, 16, 32, 64, 128)), fdir, 0).astype(np.int32)
    d8_path = os.path.join(workdir, "flowdir.tif")
    with rasterio.open(d8_path, "w", **dict(prof, dtype="int32", nodata=-1)) as dst:
        dst.write(fdir, 1)

    sub_path = os.path.join(workdir, "subbasin.tif")
    sprof = dict(prof, dtype="int32", nodata=0)
    with rasterio.open(sub_path, "w", **sprof) as dst:
        dst.write(inside.astype(np.int32), 1)

    # glacier + lake inventories, clipped to the basin
    def clip_vector(path, name):
        src_crs = gpd.read_file(path, rows=1).crs
        bb = gpd.GeoSeries([poly], crs=4326).to_crs(src_crs).total_bounds
        g = gpd.read_file(path, bbox=tuple(bb))
        if len(g):
            g = g.to_crs(4326)
            g = g[g.intersects(poly)]
        out = os.path.join(workdir, name)
        if len(g) == 0:
            g = gpd.GeoDataFrame({"id": []}, geometry=[], crs=4326)
        g.to_file(out)
        return out, len(g)

    gl_src, lk_src = data_paths.get("PMD_GLACIER_SHP"), data_paths.get("PMD_LAKES_SHP")
    glacier_shp, n_gl = clip_vector(gl_src, "glaciers.shp")
    lake_shp, n_lk = clip_vector(lk_src, "lakes.shp")

    # ERA5 hourly subsets (a few days of padding each side so every local day is complete)
    t0 = pd.Timestamp(start) - pd.Timedelta(days=1)
    t1 = pd.Timestamp(end) + pd.Timedelta(days=2)
    nc = {}
    for key, cube, var in (("temperature_files", "ERA5_T2M_ZARR", "t2m"), ("precipitation_files", "ERA5_TP_ZARR", "tp")):
        ds = xr.open_zarr(data_paths.get(cube))
        if t1 > pd.Timestamp(ds.time.values[-1]):
            t1 = pd.Timestamp(ds.time.values[-1])
        lat, lon = ds.latitude, ds.longitude
        sel = ds[var].sel(time=slice(t0, t1),
                          latitude=slice(*sorted((miny - ERA_PAD_DEG, maxy + ERA_PAD_DEG))[::(1 if lat[0] < lat[-1] else -1)]),
                          longitude=slice(minx - ERA_PAD_DEG, maxx + ERA_PAD_DEG))
        if sel.sizes["time"] < 48:
            raise ValueError(f"ERA5 cube has too little data for {start}..{end}")
        out = os.path.join(workdir, f"era5_{var}.nc")
        sel.astype("float32").load().to_netcdf(out)
        nc[key] = [out]

    glacier_inputs = _clip_glacier_rasters(workdir, poly, transform, nrows, ncols, epsg)

    params = dict(
        data_dir=workdir, proc_dir=workdir, out_dir=os.path.join(workdir, "out"),
        dem_file=dem_filled, subbasin_raster=sub_path, flow_direction_raster=d8_path, d8_encoding="esri", glacier_shp=glacier_shp, lake_shp=lake_shp,
        temperature_files=nc["temperature_files"], precipitation_files=nc["precipitation_files"],
        write_discharge_csv=True, write_final_state_rasters=False, write_subbasin_netcdf=False,
        lakes_enabled=bool(n_lk),
        glacier_thickness_raster=glacier_inputs.get("thickness", ""),
        debris_factor_raster=glacier_inputs.get("debris", ""),
    )
    if glacier_inputs.get("thickness"):
        # Pixels inside a PMD glacier polygon where IceBoost (RGI v7) models no ice are treated as ice-free
        # rather than given a flat 50 m. Measured, cal year Oct24-Oct25 vs GloFAS: Hunza KGE 0.77 -> 0.86,
        # bias +16% -> +9%; basin 4080575700 bias +245% -> +209%. Those pixels carried ~15% of all ice melt.
        params["default_glacier_thickness_m"] = 0.0
    params.update(params_extra or {})
    with open(os.path.join(workdir, "params.json"), "w") as f:
        json.dump(params, f, indent=1)
    params["_meta"] = dict(epsg=epsg, cellsize_m=cell, ncols=ncols, nrows=nrows,
                           basin_pixels=int((inside > 0).sum()), glacier_polygons=n_gl, lake_polygons=n_lk,
                           basin_area_km2=poly_utm.area / 1e6, glacier_inputs=glacier_inputs.get("meta", {}))
    return params


def run_model(workdir, timeout_s=3600):
    """Run hydro_model.py `all` on a prepared workdir (own process: it is numpy-heavy and prints a lot)."""
    p = subprocess.run([sys.executable, HYDRO_MODEL, "all", "--params", os.path.join(workdir, "params.json"),
                        "--force"], capture_output=True, text=True, timeout=timeout_s)
    with open(os.path.join(workdir, "run.log"), "w") as f:
        f.write(p.stdout + "\n" + p.stderr)
    if p.returncode != 0:
        raise RuntimeError("hydro_model failed: " + (p.stderr or p.stdout)[-800:])
    return p.stdout


def load_discharge(workdir, subbasin_id=1):
    """Daily routed outlet discharge: (dates 'YYYY-MM-DD' list, q m3/s ndarray)."""
    import pandas as pd
    df = pd.read_csv(os.path.join(workdir, "out", "subbasin_discharge_daily.csv"), parse_dates=["date"])
    df = df[df["subbasin_id"] == subbasin_id].sort_values("date")
    return [d.strftime("%Y-%m-%d") for d in df["date"]], df["q_m3s"].to_numpy(float)


def daily_to_inflow_series(q_m3s, window=None):
    """Daily mean discharge -> the inflow `series` flood_model.py accepts: day i is a daily mean, placed at the
    middle of its day (hour 24*i + 12). `window=(i0, i1)` selects a day range and re-zeros time at its start."""
    q = np.asarray(q_m3s, float)
    i0, i1 = window if window else (0, len(q))
    q = q[i0:i1]
    times = (np.arange(len(q)) * 24.0 + 12.0).tolist()
    return {"times_hr": [0.0] + times, "q_m3s": [float(q[0])] + q.tolist()} if len(q) else {"times_hr": [], "q_m3s": []}


def summarize(dates, q):
    q = np.asarray(q, float)
    k = int(np.nanargmax(q))
    return dict(n_days=len(q), first_day=dates[0], last_day=dates[-1], mean_m3s=float(np.nanmean(q)),
                peak_m3s=float(q[k]), peak_day=dates[k], volume_m3=float(np.nansum(q) * 86400.0))


CACHE_DIR = os.path.join(HERE, "melt_cache")
GLOFAS_CHECK_VERSION = 2   # bump when glofas_check's comparison changes (re-evaluated on cache hits)
MODEL_VERSION = 3   # bump when hydro_model.py's results change so cached hydrographs are not reused


def _cache_key(geom, start, end, extra):
    import hashlib
    blob = json.dumps({"g": geom, "s": start, "e": end, "x": extra or {}, "v": MODEL_VERSION,
                       "m": os.path.getmtime(HYDRO_MODEL)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# Regional glacier mass balance, Karakoram / Hindu Kush, 2000-2019 geodetic: about -0.1 to -0.5 m w.e./yr
# (Hugonnet et al. 2021, Nature). A run whose glacier-wide balance is far more negative than this is melting ice
# that does not exist in reality -- a fit to GloFAS achieved that way is not physical.
MIN_PLAUSIBLE_GLACIER_MB_M_WE_YR = -1.5


def glacier_mass_balance(workdir, dates):
    """Mean surface mass balance over glacier pixels, m w.e. per year of run. None if no glaciers / no output."""
    try:
        import pandas as pd
        ps = pd.read_csv(os.path.join(workdir, "out", "pixel_summary.csv"))
        gl = ps[ps["is_glacier"] == 1]
        if gl.empty:
            return None
        years = max(len(dates), 1) / 365.25
        return round(float(gl["cumulative_surface_mass_balance_mmwe"].mean()) / 1000.0 / years, 3)
    except Exception:
        return None


def run_for_geometry(geom, start, end, params_extra=None, max_pixels=DEFAULT_MAX_PIXELS):
    """Prepare + run + summarise for one basin polygon; cached on disk by geometry/period/params.
    Returns a JSON-able dict: dates, q_m3s (daily mean at the basin outlet), summary, meta, limitations."""
    key = _cache_key(geom, start, end, params_extra)
    wd = os.path.join(CACHE_DIR, key)
    res_path = os.path.join(wd, "result.json")
    if os.path.exists(res_path):
        with open(res_path) as f:
            res = json.load(f)
        stale = False
        if "glacier_mass_balance_m_we_yr" not in res:
            res["glacier_mass_balance_m_we_yr"] = glacier_mass_balance(wd, res["dates"])
            stale = True
        gc = res.get("glofas_check") or {}
        if gc.get("glofas_check_version") != GLOFAS_CHECK_VERSION:   # comparison logic changed, model output did not
            res["glofas_check"] = glofas_check(geom, wd, res["dates"], res["q_m3s"])
            stale = True
        if stale:
            with open(res_path, "w") as f:
                json.dump(res, f)
        return dict(res, cached=True)
    p = prepare_inputs(geom, start, end, wd, max_pixels=max_pixels, params_extra=params_extra)
    run_model(wd)
    dates, q = load_discharge(wd)
    res = dict(dates=dates, q_m3s=[round(float(x), 3) for x in q], summary=summarize(dates, q),
               meta=p["_meta"], limitations=LIMITATIONS, cached=False,
               glofas_check=glofas_check(geom, wd, dates, q),
               glacier_mass_balance_m_we_yr=glacier_mass_balance(wd, dates))
    with open(res_path, "w") as f:
        json.dump(res, f)
    return res


def flood_window_series(dates, q, center_day=None, half_window_days=3):
    """Inflow `series` for flood_model.py covering +/- half_window_days around the peak (or `center_day`)."""
    q = np.asarray(q, float)
    k = dates.index(center_day) if center_day in dates else int(np.nanargmax(q))
    i0, i1 = max(0, k - half_window_days), min(len(q), k + half_window_days + 1)
    ser = daily_to_inflow_series(q, (i0, i1))
    ser["window"] = [dates[i0], dates[i1 - 1]]
    return ser


# ---------------------------------------------------------------------------------------------
# Plausibility check against the local GloFAS historical reanalysis (itself a model, not gauges)
GLOFAS_DIR = os.path.join(data_paths.SHARE, "7. GLOFAS Variables", "GLOFAS_Historical", "river_discharge")
WARN_ABS_BIAS_PCT = 40.0
WARN_MIN_KGE = 0.3


GLOFAS_RUNOFF_DIR = os.path.join(data_paths.SHARE, "7. GLOFAS Variables", "GLOFAS_Historical", "runoff_water")
WB_MAX_RATIO = 1.5                    # accept a cell whose mean flow is within 1.5x of the basin's own runoff


GLOFAS_CLIM_NPZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "glofas_clim",
                               "glofas_mean_2003_2012.npz")   # built by global_data/build_glofas_climatology.py
_CLIM = None


def _glofas_clim():
    global _CLIM
    if _CLIM is None and os.path.exists(GLOFAS_CLIM_NPZ):
        _CLIM = dict(np.load(GLOFAS_CLIM_NPZ))
    return _CLIM


def glofas_expected_mean_q(poly, area_km2):
    """GloFAS's own long-term runoff generated INSIDE the basin (rowe, mm/day, 2003-2012 mean) x basin area, m3/s.
    This is the flow a GloFAS cell at the basin outlet should carry. None if unavailable."""
    from shapely.geometry import Point
    c = _glofas_clim()
    if c is None:
        return None
    lat, lon, ro = c["rowe_lat"], c["rowe_lon"], c["rowe"]
    x0, y0, x1, y1 = poly.bounds
    iy = np.where((lat >= y0 - 0.05) & (lat <= y1 + 0.05))[0]
    ix = np.where((lon >= x0 - 0.05) & (lon <= x1 + 0.05))[0]
    vals = [ro[i, j] for i in iy for j in ix if poly.contains(Point(float(lon[j]), float(lat[i])))]
    vals = [v for v in vals if np.isfinite(v)]
    if not vals:
        return None
    return float(np.mean(vals)) / 1000.0 * area_km2 * 1e6 / 86400.0


def glofas_series(lat, lon, start, end, poly=None, radius_cells=3, area_km2=None):
    """Daily GloFAS dis24 at the cell near the basin outlet that actually drains this basin.
    GloFAS's 0.05 deg river network differs from HydroSHEDS, so the highest-flow cell inside the polygon can sit
    on a NEIGHBOURING river (measured: Hunza 4080566900's old pick carried 78 m3/s against 20 m3/s generated in the
    basin). When poly + area are given, the cell is chosen by water balance: its 2003-2012 mean flow must match the
    basin's own GloFAS runoff within 1.5x. Returns (DatetimeIndex, values, info) or None."""
    import pandas as pd
    import xarray as xr
    parts = []
    for y in range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1):
        f = os.path.join(GLOFAS_DIR, f"river_discharge_pak_{y}.nc")
        if os.path.exists(f):
            with xr.open_dataset(f) as ds:
                parts.append(ds["dis24"].load())
    if not parts:
        return None
    da = xr.concat(parts, dim="valid_time").sel(valid_time=slice(start, end))
    if da.sizes["valid_time"] < 30:
        return None
    ilat = int(np.abs(da.latitude.values - lat).argmin())
    ilon = int(np.abs(da.longitude.values - lon).argmin())
    sl = (slice(max(ilat - radius_cells, 0), ilat + radius_cells + 1),
          slice(max(ilon - radius_cells, 0), ilon + radius_cells + 1))
    win = da.isel(latitude=sl[0], longitude=sl[1])
    info = {}
    q_exp = glofas_expected_mean_q(poly, area_km2) if (poly is not None and area_km2) else None
    if q_exp:
        c = _glofas_clim()
        jy = [int(np.abs(c["dis24_lat"] - float(v)).argmin()) for v in win.latitude.values]
        jx = [int(np.abs(c["dis24_lon"] - float(v)).argmin()) for v in win.longitude.values]
        hmean = c["dis24"][np.ix_(jy, jx)].astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            err = np.abs(np.log(hmean / q_exp))
        err = np.where(np.isfinite(err), err, np.inf)
        k = np.unravel_index(int(np.argmin(err)), err.shape)
        ratio = float(hmean[k] / q_exp)
        info = dict(method="water_balance", expected_mean_m3s=round(q_exp, 1),
                    cell_mean_2003_2012_m3s=round(float(hmean[k]), 1), ratio=round(ratio, 2))
        if not (1 / WB_MAX_RATIO <= ratio <= WB_MAX_RATIO):
            info["reason"] = (f"no GloFAS cell near the outlet carries this basin's own runoff "
                              f"({q_exp:.0f} m3/s; best {hmean[k]:.0f})")
            return None, None, info
    else:
        mean = win.mean("valid_time")
        if poly is not None:
            from shapely.geometry import Point
            inside = np.array([[poly.contains(Point(float(lo), float(la))) for lo in win.longitude.values]
                               for la in win.latitude.values])
            if not inside.any():
                return None
            mean = mean.where(inside)
        k = np.unravel_index(int(np.nanargmax(mean.values)), mean.shape)
        info = dict(method="max_flow_inside")
    cell = win.isel(latitude=k[0], longitude=k[1])
    info.update(lat=float(cell.latitude), lon=float(cell.longitude))
    return pd.DatetimeIndex(cell.valid_time.values), cell.values.astype(float), info


def skill_metrics(sim, obs):
    sim, obs = np.asarray(sim, float), np.asarray(obs, float)
    ok = np.isfinite(sim) & np.isfinite(obs)
    s, o = sim[ok], obs[ok]
    if len(s) < 20 or o.std() == 0 or s.std() == 0 or o.mean() == 0:
        return None
    r = float(np.corrcoef(s, o)[0, 1])
    kge = 1 - np.sqrt((r - 1) ** 2 + (s.std() / o.std() - 1) ** 2 + (s.mean() / o.mean() - 1) ** 2)
    return dict(n=int(ok.sum()), r=r, kge=float(kge), bias_pct=float(100 * (s.mean() - o.mean()) / o.mean()),
                nse=float(1 - ((s - o) ** 2).sum() / ((o - o.mean()) ** 2).sum()),
                sim_peak=float(s.max()), glofas_peak=float(o.max()))


def glofas_check(geom, workdir, dates, q):
    """Compare the melt season (Apr-Sep days in the run) with GloFAS. Returns a dict with a `warning` text when the
    result looks off, or {"available": False, "reason": ...}. Never raises."""
    try:
        import pandas as pd
        from shapely.geometry import shape
        ps = pd.read_csv(os.path.join(workdir, "out", "pixel_summary.csv"))
        o = ps.loc[ps["max_routed_discharge_m3s"].idxmax()]
        poly = shape(geom)
        import pyproj
        from shapely.ops import transform as _tr
        _c = poly.centroid
        _utm = pyproj.Transformer.from_crs(4326, _utm_epsg(_c.x, _c.y), always_xy=True).transform
        g = glofas_series(float(o["latitude"]), float(o["longitude"]), dates[0], dates[-1], poly=poly,
                          area_km2=_tr(_utm, poly).area / 1e6)
        if g is None:
            return {"available": False, "reason": "no GloFAS data covers this basin/period",
                    "glofas_check_version": GLOFAS_CHECK_VERSION}
        if g[0] is None:
            return {"available": False, "reason": g[2]["reason"], "glofas_cell": g[2],
                    "glofas_check_version": GLOFAS_CHECK_VERSION}
        sim = pd.Series(q, index=pd.DatetimeIndex(dates))
        both = pd.concat([sim.rename("s"), pd.Series(g[1], index=g[0]).rename("g")], axis=1).dropna()
        se = both[(both.index.month >= 4) & (both.index.month <= 9)]
        mt = skill_metrics(se["s"], se["g"])
        if mt is None:
            return {"available": False, "reason": "too few melt-season days (Apr-Sep) to compare"}
        bad = abs(mt["bias_pct"]) > WARN_ABS_BIAS_PCT or mt["kge"] < WARN_MIN_KGE
        warning = None
        if bad:
            direction = "below" if mt["bias_pct"] < 0 else "above"
            warning = (f"Melt-season flow is {abs(mt['bias_pct']):.0f}% {direction} the GloFAS reanalysis "
                       f"(KGE {mt['kge']:.2f}); treat the magnitude as unreliable until precipitation / degree-day "
                       f"factors are checked for this basin. GloFAS is itself a model, not gauge data.")
        return dict(available=True, warning=warning, glofas_cell=g[2], glofas_check_version=GLOFAS_CHECK_VERSION, **mt)
    except Exception as e:  # a failed sanity check must never fail the run
        return {"available": False, "reason": f"check failed: {e}"}


# temp_bias_c x precip_correction x (ddf_snow, ddf_ice). Both directions: ERA5 under-catches precipitation in the
# glacierised Karakoram but its summer temperature differs between neighbouring basins by several degrees.
CALIBRATION_GRID = [dict(temp_bias_c=t, precip_correction=p, ddf_snow=s, ddf_ice=i)
                    for t in (-3.0, -2.0, -1.0, 0.0, 1.0) for p in (0.5, 1.0, 2.0, 3.0, 4.0)
                    for (s, i) in ((4.0, 8.0), (7.0, 16.0))]


def calibrate_against_glofas(geom, cal_start, cal_end, val_start, val_end, grid=None):
    """Grid-search precipitation factor and degree-day factors for ONE basin on a calibration period, then check the
    winner on a different (validation) period. Benchmark is the GloFAS reanalysis (a model, not gauges), melt season
    Apr-Sep. Measured on the Hunza and neighbouring basins: parameters do NOT transfer between basins, so this must
    be run per basin; `transferable` says whether the hold-out year agreed. Returns a JSON-able dict."""
    grid = grid or CALIBRATION_GRID
    default = dict(temp_bias_c=0.0, precip_correction=1.0, ddf_snow=4.0, ddf_ice=8.0)
    trials = []
    for extra in grid:
        extra = dict(extra)
        r = run_for_geometry(geom, cal_start, cal_end, params_extra=extra)
        gc = r.get("glofas_check", {})
        if gc.get("available"):
            mb = r.get("glacier_mass_balance_m_we_yr")
            trials.append(dict(params=extra, kge=gc["kge"], bias_pct=gc["bias_pct"], nse=gc["nse"],
                               glacier_mb_m_we_yr=mb,
                               physical=(mb is None or mb >= MIN_PLAUSIBLE_GLACIER_MB_M_WE_YR)))
    if not trials:
        return {"available": False, "reason": "GloFAS comparison unavailable for this basin/period"}
    physical = [t for t in trials if t["physical"]]
    best = max(physical or trials, key=lambda t: t["kge"])

    def val(extra):
        r = run_for_geometry(geom, val_start, val_end, params_extra=extra)
        gc = r.get("glofas_check", {})
        out = {k: gc.get(k) for k in ("available", "kge", "bias_pct", "nse", "reason")}
        out["glacier_mb_m_we_yr"] = r.get("glacier_mass_balance_m_we_yr")
        return out
    v_best, v_def = val(best["params"]), val(default)
    mb_ok = v_best["glacier_mb_m_we_yr"] is None or v_best["glacier_mb_m_we_yr"] >= MIN_PLAUSIBLE_GLACIER_MB_M_WE_YR
    ok = bool(v_best.get("available") and v_best["kge"] >= 0.3 and abs(v_best["bias_pct"]) <= WARN_ABS_BIAS_PCT
              and best["physical"] and mb_ok)
    return dict(available=True, recommended=best["params"], calibration=best, defaults_calibration=next(
        (t for t in trials if t["params"] == default), None), validation_recommended=v_best,
        validation_defaults=v_def, transferable=ok, n_trials=len(trials), n_physical=len(physical),
        min_plausible_glacier_mb_m_we_yr=MIN_PLAUSIBLE_GLACIER_MB_M_WE_YR,
        note=("Hold-out year agrees with GloFAS; the factors are usable for this basin (still relative to a model)."
              if ok else "Hold-out year does NOT agree with GloFAS, or the fit needs implausible glacier ice loss "
              "-- do not trust these factors; treat melt magnitudes for this basin as unreliable."))
