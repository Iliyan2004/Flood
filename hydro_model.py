"""
Fully distributed snow + glacier + lake hydrological model (daily)
=================================================================

Design goals
------------
1. Native ERA5 forcing: temperature and precipitation are mapped to model pixels at
   their native ERA5 grid-cell values. Temperature is shifted to each pixel's elevation
   with a single lapse rate (Config.lapse_rate_c_per_m, default -6.5 C/km; 0 disables);
   precipitation is NOT downscaled (use precip_correction / snowfall_correction).
2. Pixelwise hydrology on the DEM grid: SWE, liquid snow water, soil moisture,
   groundwater, fast runoff, glacier runoff, glacier thickness and mass loss are
   state variables for individual model pixels.
3. Terrain controls (slope/aspect) modify melt response, not meteorological forcing.
4. Glacier ice melt is limited by remaining ice thickness. Ice thickness evolves.
5. D8 routing is pixelwise. Dynamic glacial lakes are graph nodes in the same
   drainage network and can switch between normal and rapid-release regimes.
6. Validation is observation-specific:
      - Sentinel-1 binary wet-snow maps validate modelled wet/melting snow.
      - Sentinel-2 binary snow-cover maps validate modelled snow presence/SCF.
      - Optional observed discharge validates routed discharge.
7. Spatial results are written as final-state GeoTIFFs and optional snapshot
   GeoTIFFs. Basin/subbasin daily summaries are written to CSV/NetCDF.

IMPORTANT
---------
- The DEM and D8 grid should be projected in metres. A projected grid is required.
- Sentinel validation rasters may have any CRS/resolution; they are reprojected to
  the DEM grid with nearest-neighbour sampling.
- Sentinel-1 wet-snow masks should already be preprocessed/classified. This model
  does NOT derive wet snow from raw SAR backscatter.
- Sentinel-2 snow masks should already be cloud-masked/classified (binary snow/no-snow).
- Native ERA5 forcing means spatial hydrological detail can be finer than ERA5,
  but meteorological information itself is still coarse.

Usage
-----
    python hydro_model.py inspect --params params.json
    python hydro_model.py preprocess --params params.json --force
    python hydro_model.py run --params params.json
    python hydro_model.py all --params params.json --force

Required packages
-----------------
numpy pandas scipy xarray rasterio geopandas shapely pyproj matplotlib
netCDF4 is optional; xarray will fall back to another available engine.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from dataclasses import dataclass, asdict, fields
from pathlib import Path

import numpy as np
import pandas as pd

EARTH_R = 6371008.8
INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)
RHO_WATER = 1000.0  # kg m-3
RHO_ICE = 900.0     # kg m-3; configurable below
SECONDS_PER_DAY = 86400.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# =============================================================================
# CONFIGURATION
# =============================================================================
@dataclass
class Config:
    # folders
    data_dir: str = "."
    proc_dir: str = "."
    out_dir: str = "model_output_distributed"

    # required spatial inputs
    dem_file: str = ""
    subbasin_raster: str = ""
    glacier_shp: str = ""
    lake_shp: str = ""

    # high-priority spatial inputs; blank means derive/use documented fallback
    slope_file: str = ""                 # degrees, 0..90; derived from DEM if blank
    aspect_file: str = ""                # degrees clockwise from north; derived if blank
    debris_factor_raster: str = ""       # dimensionless ice-melt multiplier
    glacier_thickness_raster: str = ""   # metres ice; strongly recommended
    soil_capacity_raster: str = ""       # mm
    infiltration_capacity_raster: str = ""  # mm/day
    percolation_fraction_raster: str = ""   # fraction/day, 0..1
    flow_direction_raster: str = ""      # D8 pointer; derived from DEM if blank
    flow_accumulation_raster: str = ""   # optional, used to identify lake outlet
    streams_raster: str = ""             # optional binary stream mask
    d8_encoding: str = "whitebox"        # whitebox or esri

    # native ERA5 forcing
    temperature_files: tuple = ()          # hourly ERA5 t2m NetCDF files (required)
    precipitation_files: tuple = ()        # hourly ERA5 total-precip NetCDF files (required)
    t_var_names: tuple = ("t2m", "2m_temperature", "temperature_2m", "t2")
    p_var_names: tuple = ("tp", "total_precipitation", "precipitation", "precip", "pr")
    tz_offset_hours: float = 5.0
    # Elevation correction of the ERA5 temperature: each ERA5 cell's value is taken to represent the
    # cell-mean DEM elevation and is shifted to every pixel by lapse_rate_c_per_m * (z_pixel - z_cell_mean).
    # ERA5 cells are ~9-30 km wide and span thousands of metres of relief in the Karakoram, so without
    # this a degree-day melt model gives the same melt at 2000 m and 6000 m. Set to 0 to disable.
    lapse_rate_c_per_m: float = -0.0065
    # Uniform additive correction to ERA5 temperature (C). ERA5-Land's summer 0 C isotherm differs between
    # neighbouring Karakoram/Hindu Kush basins by >500 m; a per-basin offset is the knob that controls ice melt.
    temp_bias_c: float = 0.0
    precip_accumulated: object = None
    precip_units: object = None
    precip_correction: float = 1.0
    snowfall_correction: float = 1.0

    # snow / ice physics
    tt_snow: float = 0.0
    tt_rain: float = 2.0
    ddf_snow: float = 4.0              # mm w.e. degC-1 day-1
    ddf_ice: float = 8.0               # mm w.e. degC-1 day-1
    debris_factor: float = 1.0         # fallback when no raster
    refreeze_coef: float = 0.05
    water_holding: float = 0.10
    use_subdaily_temperature_spread: bool = True
    sigma_min: float = 0.5
    swe_full_cover_mm: float = 50.0
    wet_snow_min_swe_mm: float = 5.0
    rho_ice_kg_m3: float = 900.0
    default_glacier_thickness_m: float = 50.0  # fallback only; use thickness raster for science runs

    # terrain-sensitive melt response; this modifies melt, NOT ERA5 forcing
    terrain_melt_strength: float = 0.25
    terrain_factor_min: float = 0.70
    terrain_factor_max: float = 1.30

    # soil / runoff
    soil_capacity_mm: float = 150.0
    infiltration_capacity_mm_day: float = 25.0
    percolation_fraction_day: float = 0.02
    soil_initial_fraction: float = 0.50
    et_coef: float = 0.10              # simple mm/degC/day demand
    k_fast_days: float = 2.0
    k_groundwater_days: float = 90.0
    k_glacier_days: float = 4.0

    # pixel routing
    routing_enabled: bool = True
    route_k_days: float = 0.02          # fallback residence time per pixel (~0.5 h)
    route_min_k_days: float = 1.0 / 144.0  # 10 min
    channel_manning_n: float = 0.040
    channel_hydraulic_radius_m: float = 1.0
    channel_min_slope: float = 1.0e-4
    use_manning_pixel_travel_time: bool = True

    # lakes: attributes are read when present; otherwise fallbacks
    lakes_enabled: bool = True
    lake_default_mean_depth_m: float = 10.0
    lake_initial_storage_fraction: float = 0.70
    lake_outlet_threshold_fraction: float = 0.65
    lake_weir_coefficient: float = 1.70
    lake_default_outlet_width_m: float = 5.0
    lake_evap_coef: float = 0.04
    rapid_release_enabled: bool = True
    rapid_trigger_fraction: float = 0.98
    rapid_target_fraction: float = 0.75
    rapid_release_hours: float = 6.0

    # Sentinel validation inputs
    # Files must contain a parseable YYYYMMDD or YYYY-MM-DD date in filename.
    # S1: 1=wet snow, 0=not-wet-snow, nodata/other excluded.
    # S2: 1=snow, 0=no snow, nodata/cloud excluded.
    sentinel1_wetsnow_dir: str = ""
    sentinel2_snow_dir: str = ""
    sentinel1_valid_values: tuple = (0, 1)
    sentinel2_valid_values: tuple = (0, 1)
    write_validation_maps: bool = False

    # optional discharge validation
    qobs_csv: str = ""                 # date,subbasin_id,q_obs_m3s

    # outputs
    write_discharge_csv: bool = False  # write daily routed discharge per subbasin -> <out_dir>/subbasin_discharge_daily.csv
    block_size: int = 1024
    snapshot_interval_days: int = 0     # retained for config compatibility; snapshots disabled
    write_final_state_rasters: bool = True
    write_subbasin_netcdf: bool = True
    save_preprocess_cache: bool = True

    # spinup
    spinup_cycles: int = 0
    spinup_days: int = 0


def load_config(args) -> Config:
    cfg = Config()
    if getattr(args, "params", None):
        with open(args.params, "r", encoding="utf-8") as f:
            d = json.load(f)
        valid = {f.name for f in fields(Config)}
        for k, v in d.items():
            if k not in valid:
                raise KeyError(f"Unknown parameter in {args.params}: {k}")
            setattr(cfg, k, tuple(v) if isinstance(v, list) else v)
    for name in ("data_dir", "proc_dir", "out_dir"):
        v = getattr(args, name, None)
        if v:
            setattr(cfg, name, v)

    if cfg.tt_rain <= cfg.tt_snow:
        raise ValueError("tt_rain must be greater than tt_snow")
    if cfg.d8_encoding.lower() not in ("whitebox", "esri"):
        raise ValueError("d8_encoding must be 'whitebox' or 'esri'")
    if not 0 <= cfg.soil_initial_fraction <= 1:
        raise ValueError("soil_initial_fraction must be in [0,1]")
    if not 0 <= cfg.percolation_fraction_day <= 1:
        raise ValueError("percolation_fraction_day must be in [0,1]")
    for k in (cfg.k_fast_days, cfg.k_groundwater_days, cfg.k_glacier_days, cfg.route_k_days):
        if k <= 0:
            raise ValueError("reservoir/routing time constants must be > 0")
    if cfg.rho_ice_kg_m3 <= 0:
        raise ValueError("rho_ice_kg_m3 must be > 0")
    return cfg


def require_inputs(cfg: Config) -> None:
    """The required inputs have no defaults (they used to be one machine's Windows paths)."""
    missing = [k for k in ("dem_file", "subbasin_raster", "glacier_shp") if not getattr(cfg, k)]
    missing += [k for k in ("temperature_files", "precipitation_files") if not getattr(cfg, k)]
    if missing:
        raise SystemExit("Missing required parameter(s) in --params: " + ", ".join(missing))


def find_file(cfg: Config, name: str) -> Path:
    p = Path(name)
    if p.exists():
        return p
    for d in (cfg.data_dir, cfg.proc_dir, "."):
        q = Path(d) / name
        if q.exists():
            return q
    hits = list(Path(cfg.data_dir).rglob(Path(name).name)) if Path(cfg.data_dir).exists() else []
    if hits:
        return hits[0]
    raise FileNotFoundError(name)


def optional_path(cfg: Config, name: str) -> Path | None:
    if not name:
        return None
    try:
        return find_file(cfg, name)
    except FileNotFoundError:
        raise FileNotFoundError(f"Configured optional input not found: {name}")


# =============================================================================
# ERA5 NATIVE FORCING
# =============================================================================
def _pick_var(ds, candidates):
    low = {v.lower(): v for v in ds.data_vars}
    for c in candidates:
        if c.lower() in low:
            return low[c.lower()]
    return None


def discover_nc(cfg: Config):
    import xarray as xr
    tl, pl = [], []
    for fstr in cfg.temperature_files:
        f = Path(fstr)
        if not f.exists():
            raise FileNotFoundError(f"Temperature NetCDF not found: {f}")
        with xr.open_dataset(f) as ds:
            v = _pick_var(ds, cfg.t_var_names)
        if v is None:
            raise ValueError(f"{f}: no temperature variable found")
        tl.append((f, v))
    for fstr in cfg.precipitation_files:
        f = Path(fstr)
        if not f.exists():
            raise FileNotFoundError(f"Precipitation NetCDF not found: {f}")
        with xr.open_dataset(f) as ds:
            v = _pick_var(ds, cfg.p_var_names)
        if v is None:
            raise ValueError(f"{f}: no precipitation variable found")
        pl.append((f, v))
    return tl, pl


def _standardize(da):
    ren = {}
    for d in da.dims:
        dl = d.lower()
        if dl in ("valid_time", "time_counter", "t"):
            ren[d] = "time"
        elif dl in ("lat", "latitude", "y"):
            ren[d] = "latitude"
        elif dl in ("lon", "longitude", "x"):
            ren[d] = "longitude"
    da = da.rename(ren)
    if "expver" in da.dims:
        da = da.isel(expver=0).combine_first(da.isel(expver=-1))
    for d in [x for x in da.dims if x not in ("time", "latitude", "longitude")]:
        if da.sizes[d] != 1:
            raise ValueError(f"Unexpected dimension {d} size={da.sizes[d]}")
        da = da.isel({d: 0}, drop=True)
    if "time" not in da.dims:
        da = da.expand_dims("time")
    return da.transpose("time", "latitude", "longitude")


def _time_values(ds, var):
    for d in ds[var].dims:
        if d.lower() in ("time", "valid_time", "time_counter", "t"):
            return pd.DatetimeIndex(ds[d].values)
    raise ValueError(f"No time dimension in {var}")


def read_nc_series(file_vars):
    import xarray as xr
    info = []
    for f, v in file_vars:
        with xr.open_dataset(f) as ds:
            t = _time_values(ds, v)
        if not (t.is_monotonic_increasing and t.is_unique):
            raise ValueError(f"{f.name}: time axis must be strictly increasing")
        info.append([f, v, t])
    info.sort(key=lambda r: r[2][0])

    last = None
    total = 0
    for row in info:
        keep = np.ones(len(row[2]), bool) if last is None else np.asarray(row[2] > last)
        row.append(keep)
        total += int(keep.sum())
        if keep.any():
            last = row[2][keep][-1]

    vals = None
    lat = lon = None
    units = set()
    pos = 0
    times = []
    for f, v, t, keep in info:
        if not keep.any():
            continue
        log(f"reading {f.name}:{v}")
        with xr.open_dataset(f) as ds:
            da = _standardize(ds[v])
            arr = np.asarray(da.values, np.float32)
            la = np.asarray(da.latitude.values, np.float64)
            lo = np.asarray(da.longitude.values, np.float64)
            units.add(str(da.attrs.get("units", "")).strip())
        if vals is None:
            vals = np.empty((total,) + arr.shape[1:], np.float32)
            lat, lon = la, lo
        else:
            if la.shape != lat.shape or lo.shape != lon.shape or not (
                np.allclose(la, lat, atol=1e-5) and np.allclose(lo, lon, atol=1e-5)
            ):
                raise ValueError("NetCDF grids differ between files")
        sel = arr[keep]
        vals[pos:pos + len(sel)] = sel
        pos += len(sel)
        times.append(t[keep])
    idx = times[0].append(times[1:]) if len(times) > 1 else times[0]
    return idx, vals, lat, lon, units


def check_regular(idx: pd.DatetimeIndex, name: str):
    sec = np.diff(idx.values).astype("timedelta64[s]").astype(np.int64)
    if len(sec) == 0:
        raise ValueError(f"{name}: only one timestep")
    step = int(pd.Series(sec).mode()[0])
    if np.any(sec != step):
        raise ValueError(f"{name}: irregular/missing timesteps")
    return pd.Timedelta(seconds=step)


def detect_accumulated(times: pd.DatetimeIndex, vals: np.ndarray) -> bool:
    hrs = np.asarray(times.hour)
    m1, m23 = hrs == 1, hrs == 23
    if not m1.any() or not m23.any():
        return False
    a1, a23 = float(np.nanmean(vals[m1])), float(np.nanmean(vals[m23]))
    return a1 > 0 and a23 > 4 * a1


def deaccumulate(times: pd.DatetimeIndex, vals: np.ndarray) -> np.ndarray:
    hrs = np.asarray(times.hour)
    for i in range(len(vals) - 1, 0, -1):
        if hrs[i] != 1:
            vals[i] -= vals[i - 1]
    if hrs[0] != 1:
        vals[0] = np.nan
    np.maximum(vals, 0, out=vals)
    return vals


def precip_to_mm(vals: np.ndarray, units: set, override=None):
    u = (override if override else next((x for x in units if x), "")).lower().replace(" ", "")
    if u in ("", "m", "metre", "meter", "metres", "meters") or u.startswith("mofwater"):
        vals *= 1000.0
    elif u in ("mm", "kgm-2", "kg/m2", "kgm**-2"):
        pass
    elif u in ("kgm-2s-1", "kgm**-2s**-1", "mm/s"):
        vals *= 3600.0
    else:
        raise ValueError(f"Unsupported precipitation units: {u}")
    return vals


def day_groups(idx_local, expected):
    days = idx_local.normalize()
    codes, uniq = pd.factorize(days)
    counts = np.bincount(codes)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    return [(uniq[k], int(s), int(s + c)) for k, (s, c) in enumerate(zip(starts, counts)) if c == expected]


def daily_temperature(idx_utc, vals2d, tz_hours, expected):
    groups = day_groups(idx_utc + pd.Timedelta(hours=tz_hours), expected)
    if not groups:
        raise ValueError("No complete temperature days")
    mean = np.empty((len(groups), vals2d.shape[1]), np.float32)
    std = np.empty_like(mean)
    for i, (_, s, e) in enumerate(groups):
        blk = vals2d[s:e]
        mean[i] = blk.mean(axis=0, dtype=np.float64)
        std[i] = blk.std(axis=0, dtype=np.float64)
    return pd.DatetimeIndex([g[0] for g in groups]), mean, std


def daily_precipitation(idx_utc, vals2d, tz_hours, expected):
    groups = day_groups(idx_utc + pd.Timedelta(hours=tz_hours - 1), expected)
    if not groups:
        raise ValueError("No complete precipitation days")
    total = np.empty((len(groups), vals2d.shape[1]), np.float32)
    for i, (_, s, e) in enumerate(groups):
        total[i] = vals2d[s:e].sum(axis=0, dtype=np.float64)
    return pd.DatetimeIndex([g[0] for g in groups]), total


def fill_nan_cells(arr: np.ndarray, ny, nx, name):
    from scipy.ndimage import distance_transform_edt
    good = np.isfinite(arr).all(axis=0)
    if good.all():
        return arr
    if not good.any():
        raise ValueError(f"{name}: all ERA cells are NaN")
    log(f"{name}: filling {(~good).sum()} invalid ERA cells from nearest valid cell")
    _, (iy, ix) = distance_transform_edt(~good.reshape(ny, nx), return_indices=True)
    nearest = (iy * nx + ix).reshape(-1)
    bad = np.nonzero(~good)[0]
    arr[:, bad] = arr[:, nearest[bad]]
    return arr


def build_native_forcing(cfg: Config):
    """Return daily ERA5 values on the native ERA grid; no spatial downscaling."""
    tl, pl = discover_nc(cfg)

    tidx, tv, lat, lon, tunits = read_nc_series(tl)
    step_t = check_regular(tidx, "temperature")
    expected_t = int(round(SECONDS_PER_DAY / step_t.total_seconds()))
    if any(u.lower() in ("k", "kelvin") for u in tunits) or float(np.nanmean(tv[: min(24, len(tv))])) > 150:
        tv -= 273.15
    tdays, tmean, tstd = daily_temperature(tidx, tv.reshape(len(tv), -1), cfg.tz_offset_hours, expected_t)
    del tv

    pidx, pv, plat, plon, punits = read_nc_series(pl)
    if pv.shape[1:] != (len(lat), len(lon)) or not (
        np.allclose(plat, lat, atol=1e-5) and np.allclose(plon, lon, atol=1e-5)
    ):
        raise ValueError("Temperature and precipitation ERA grids differ")
    step_p = check_regular(pidx, "precipitation")
    expected_p = int(round(SECONDS_PER_DAY / step_p.total_seconds()))
    acc = cfg.precip_accumulated
    if acc is None:
        acc = detect_accumulated(pidx, pv)
        log(f"ERA precipitation accumulated-since-00UTC detected: {acc}")
    pv = precip_to_mm(pv, punits, cfg.precip_units)
    if acc:
        pv = deaccumulate(pidx, pv.reshape(len(pv), -1)).reshape(pv.shape)
    pdays, ptot = daily_precipitation(pidx, pv.reshape(len(pv), -1), cfg.tz_offset_hours, expected_p)
    del pv

    common = tdays.intersection(pdays)
    if len(common) < 2:
        raise ValueError("Insufficient common ERA temperature/precipitation period")
    if not (np.diff(common.values) == np.timedelta64(1, "D")).all():
        raise ValueError("Common forcing period is not continuous")
    ti, pi = tdays.get_indexer(common), pdays.get_indexer(common)
    tmean, tstd, ptot = tmean[ti], tstd[ti], ptot[pi]
    ny, nx = len(lat), len(lon)
    tmean = fill_nan_cells(tmean, ny, nx, "T2m")
    tstd = fill_nan_cells(tstd, ny, nx, "T2m std")
    ptot = fill_nan_cells(ptot, ny, nx, "precip")
    return dict(dates=common, t=tmean, sigma=tstd, p=ptot * cfg.precip_correction,
                lat=lat, lon=lon, ny=ny, nx=nx)


# =============================================================================
# SPATIAL UTILITIES
# =============================================================================
def read_polygons(path: Path, crs, what: str):
    import geopandas as gpd
    g = gpd.read_file(path)
    g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
    if g.crs is None:
        raise ValueError(f"{what} layer has no CRS: {path}")
    g = g.to_crs(crs)
    bad = ~g.geometry.is_valid
    if bad.any():
        g.loc[bad, "geometry"] = g.loc[bad, "geometry"].buffer(0)
    log(f"{what}: {len(g)} polygons")
    return g.reset_index(drop=True)


def aligned_raster(path: Path, ref, resampling="bilinear", dst_dtype="float32", fill=np.nan):
    """Read any raster onto ref raster grid."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    rs = getattr(Resampling, resampling)
    out = np.full((ref.height, ref.width), fill, dtype=dst_dtype)
    with rasterio.open(path) as src:
        source = src.read(1)
        src_nodata = src.nodata
        reproject(
            source=source,
            destination=out,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src_nodata,
            dst_transform=ref.transform,
            dst_crs=ref.crs,
            dst_nodata=fill if np.issubdtype(np.dtype(dst_dtype), np.floating) else None,
            resampling=rs,
        )
    return out


def derive_slope_aspect(dem: np.ndarray, dx: float, dy: float):
    z = dem.astype(np.float64)
    gy, gx = np.gradient(z, dy, dx)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    # aspect: clockwise from north; downslope direction
    aspect = (np.degrees(np.arctan2(-gx, gy)) + 360.0) % 360.0
    flat = slope < 1e-6
    aspect[flat] = 0.0
    return slope.astype(np.float32), aspect.astype(np.float32)


def derive_d8_pointer(dem: np.ndarray, valid: np.ndarray, encoding: str, dx: float, dy: float):
    """Derive steepest-descent D8 pointer from filled DEM."""
    h, w = dem.shape
    ptr = np.zeros((h, w), np.uint8)
    if encoding == "whitebox":
        dirs = [(-1, 1, 1), (0, 1, 2), (1, 1, 4), (1, 0, 8),
                (1, -1, 16), (0, -1, 32), (-1, -1, 64), (-1, 0, 128)]
    else:
        dirs = [(0, 1, 1), (1, 1, 2), (1, 0, 4), (1, -1, 8),
                (0, -1, 16), (-1, -1, 32), (-1, 0, 64), (-1, 1, 128)]
    best = np.full((h, w), -np.inf, np.float64)
    for dr, dc, code in dirs:
        shifted = np.full_like(dem, np.nan, dtype=np.float64)
        # shifted[r, c] must be the elevation of the neighbour this direction points to: dem[r+dr, c+dc]
        # (the source/destination slices were swapped, so it held dem[r-dr, c-dc]: every derived pointer
        # pointed uphill and the routing graph was full of cycles).
        r0d, r1d = max(0, -dr), min(h, h - dr)
        c0d, c1d = max(0, -dc), min(w, w - dc)
        r0s, r1s = max(0, dr), min(h, h + dr)
        c0s, c1s = max(0, dc), min(w, w + dc)
        shifted[r0d:r1d, c0d:c1d] = dem[r0s:r1s, c0s:c1s]
        dist = math.hypot(dx if dc else 0.0, dy if dr else 0.0)
        slope = (dem - shifted) / max(dist, 1e-9)
        ok = valid & np.isfinite(shifted) & (slope > best) & (slope > 0)
        ptr[ok] = code
        best[ok] = slope[ok]
    return ptr


def d8_offsets(encoding: str):
    if encoding == "whitebox":
        return {1: (-1, 1), 2: (0, 1), 4: (1, 1), 8: (1, 0),
                16: (1, -1), 32: (0, -1), 64: (-1, -1), 128: (-1, 0)}
    return {1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1),
            16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)}


def rasterize_polygons(gdf, ref, values=None, fill=0, dtype="int32"):
    from rasterio.features import rasterize
    if values is None:
        shapes = [(geom, 1) for geom in gdf.geometry]
    else:
        shapes = [(geom, int(v)) for geom, v in zip(gdf.geometry, values)]
    return rasterize(shapes, out_shape=(ref.height, ref.width), transform=ref.transform,
                     fill=fill, dtype=dtype)


def pixel_centres_for_indices(transform, rows, cols):
    xs = transform.c + (cols + 0.5) * transform.a + (rows + 0.5) * transform.b
    ys = transform.f + (cols + 0.5) * transform.d + (rows + 0.5) * transform.e
    return xs, ys


def nearest_grid_index(vals, grid):
    grid = np.asarray(grid)
    if len(grid) < 2:
        return np.zeros(len(vals), np.int64)
    asc = grid[-1] > grid[0]
    g = grid if asc else grid[::-1]
    pos = np.searchsorted(g, vals)
    pos = np.clip(pos, 1, len(g) - 1)
    left, right = g[pos - 1], g[pos]
    take_right = np.abs(vals - right) < np.abs(vals - left)
    out = pos - 1 + take_right.astype(np.int64)
    if not asc:
        out = len(grid) - 1 - out
    return out


def parse_date_from_name(path: Path):
    name = path.stem
    for pat in (r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)",):
        m = re.search(pat, name)
        if m:
            try:
                return pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
            except ValueError:
                pass
    return None


def discover_validation_files(folder: str):
    if not folder:
        return {}
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(f"Validation directory not found: {root}")
    out = {}
    for ext in ("*.tif", "*.tiff"):
        for p in root.rglob(ext):
            d = parse_date_from_name(p)
            if d is not None:
                out[d.normalize()] = p
    return out


# =============================================================================
# PREPROCESSING
# =============================================================================
def _attr_first(row, names, default=np.nan):
    for n in names:
        if n in row.index:
            v = row[n]
            try:
                if pd.notna(v):
                    return float(v)
            except Exception:
                pass
    return default


def preprocess(cfg: Config, forcing_meta: dict | None = None, force=False):
    import rasterio
    from pyproj import Transformer

    outdir = Path(cfg.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    cache = outdir / "distributed_preprocessed.npz"
    meta_json = outdir / "distributed_preprocessed_meta.json"

    dem_p = find_file(cfg, cfg.dem_file)
    sub_p = find_file(cfg, cfg.subbasin_raster)
    gl_p = find_file(cfg, cfg.glacier_shp)
    lake_p = find_file(cfg, cfg.lake_shp) if cfg.lakes_enabled else None

    filesig = {
        "dem": [str(dem_p), dem_p.stat().st_size, dem_p.stat().st_mtime_ns],
        "sub": [str(sub_p), sub_p.stat().st_size, sub_p.stat().st_mtime_ns],
        "gl": [str(gl_p), gl_p.stat().st_size, gl_p.stat().st_mtime_ns],
        "lake": None if lake_p is None else [str(lake_p), lake_p.stat().st_size, lake_p.stat().st_mtime_ns],
        "slope": cfg.slope_file, "aspect": cfg.aspect_file, "debris": cfg.debris_factor_raster,
        "thickness": cfg.glacier_thickness_raster, "soil": cfg.soil_capacity_raster,
        "infil": cfg.infiltration_capacity_raster, "perc": cfg.percolation_fraction_raster,
        "d8": cfg.flow_direction_raster, "fac": cfg.flow_accumulation_raster,
        "streams": cfg.streams_raster, "encoding": cfg.d8_encoding, "v": 7,
    }
    if cache.exists() and meta_json.exists() and not force:
        try:
            if json.loads(meta_json.read_text()) == filesig:
                z = np.load(cache, allow_pickle=False)
                log(f"Using cached distributed preprocessing: {cache}")
                return {k: z[k] for k in z.files}
        except Exception:
            pass

    with rasterio.open(dem_p) as dem, rasterio.open(sub_p) as sb:
        if dem.crs is None:
            raise ValueError("DEM has no CRS")
        if dem.crs.is_geographic:
            raise ValueError("Distributed model requires a projected DEM in metres; reproject the DEM first")
        if dem.shape != sb.shape or not np.allclose(tuple(dem.transform)[:6], tuple(sb.transform)[:6], atol=1e-8):
            raise ValueError("DEM and subbasin raster must have identical grid/transform")
        if sb.crs != dem.crs:
            raise ValueError("DEM and subbasin CRS differ")
        if dem.transform.b != 0 or dem.transform.d != 0:
            raise ValueError("Rotated DEM grids are not supported")

        z2 = dem.read(1).astype(np.float32)
        sid2 = sb.read(1)
        valid = np.isfinite(z2)
        if dem.nodata is not None and np.isfinite(dem.nodata):
            valid &= z2 != dem.nodata
        valid &= sid2 > 0
        if sb.nodata is not None and np.isfinite(sb.nodata):
            valid &= sid2 != sb.nodata
        if not valid.any():
            raise ValueError("No valid model pixels")

        dx, dy = abs(dem.transform.a), abs(dem.transform.e)
        area_m2 = dx * dy
        h, w = dem.shape
        rows, cols = np.nonzero(valid)
        ncell = len(rows)
        grid_flat = rows.astype(np.int64) * w + cols.astype(np.int64)
        flat_to_active = np.full(h * w, -1, np.int64)
        flat_to_active[grid_flat] = np.arange(ncell, dtype=np.int64)

        ids = np.unique(sid2[valid].astype(np.int64))
        sub_lookup = {int(v): i for i, v in enumerate(ids)}
        sub = np.array([sub_lookup[int(v)] for v in sid2[valid]], np.int32)
        nsub = len(ids)

        # slope/aspect
        slope_p = optional_path(cfg, cfg.slope_file)
        aspect_p = optional_path(cfg, cfg.aspect_file)
        if slope_p is None or aspect_p is None:
            log("Slope/aspect not both supplied -> deriving from DEM")
            slope2, aspect2 = derive_slope_aspect(z2, dx, dy)
        else:
            slope2 = aligned_raster(slope_p, dem, "bilinear")
            aspect2 = aligned_raster(aspect_p, dem, "nearest")
        slope = np.clip(np.nan_to_num(slope2[valid], nan=0.0), 0, 89.9).astype(np.float32)
        aspect = np.mod(np.nan_to_num(aspect2[valid], nan=0.0), 360.0).astype(np.float32)
        exposure = np.sin(np.deg2rad(slope)) * np.cos(np.deg2rad(aspect - 180.0))
        terrain_factor = np.clip(1.0 + cfg.terrain_melt_strength * exposure,
                                 cfg.terrain_factor_min, cfg.terrain_factor_max).astype(np.float32)

        # glaciers
        glaciers = read_polygons(gl_p, dem.crs, "glaciers")
        gl2 = rasterize_polygons(glaciers, dem, fill=0, dtype="uint8").astype(bool)
        glacier = gl2[valid]

        debris_p = optional_path(cfg, cfg.debris_factor_raster)
        if debris_p:
            deb2 = aligned_raster(debris_p, dem, "bilinear")
            debris = np.nan_to_num(deb2[valid], nan=cfg.debris_factor).astype(np.float32)
        else:
            debris = np.full(ncell, cfg.debris_factor, np.float32)
        debris = np.clip(debris, 0.05, 3.0)
        debris[~glacier] = 1.0

        thick_p = optional_path(cfg, cfg.glacier_thickness_raster)
        if thick_p:
            thick2 = aligned_raster(thick_p, dem, "bilinear")
            ice_thickness = np.nan_to_num(thick2[valid], nan=0.0).astype(np.float32)
            ice_thickness = np.maximum(ice_thickness, 0)
            # Glacier inventory controls glacier mask; missing thickness gets fallback only on glacier pixels.
            miss = glacier & (ice_thickness <= 0)
            if miss.any():
                log(f"WARNING: {miss.sum()} glacier pixels lack positive thickness; using fallback {cfg.default_glacier_thickness_m} m")
                ice_thickness[miss] = cfg.default_glacier_thickness_m
        else:
            log("WARNING: glacier thickness raster not supplied; using uniform fallback thickness. Mass-loss totals are less defensible.")
            ice_thickness = np.where(glacier, cfg.default_glacier_thickness_m, 0.0).astype(np.float32)

        # soil parameters
        def spatial_or_default(pathstr, default, resampling="bilinear"):
            p = optional_path(cfg, pathstr)
            if p:
                a2 = aligned_raster(p, dem, resampling)
                a = np.nan_to_num(a2[valid], nan=default).astype(np.float32)
            else:
                a = np.full(ncell, default, np.float32)
            return a

        soil_cap = np.maximum(spatial_or_default(cfg.soil_capacity_raster, cfg.soil_capacity_mm), 0.1)
        infil_cap = np.maximum(spatial_or_default(cfg.infiltration_capacity_raster, cfg.infiltration_capacity_mm_day), 0.0)
        perc_frac = np.clip(spatial_or_default(cfg.percolation_fraction_raster, cfg.percolation_fraction_day), 0, 1)
        soil_cap[glacier] = 0.0
        infil_cap[glacier] = 0.0
        perc_frac[glacier] = 0.0

        # lakes
        lake_id2 = np.zeros((h, w), np.int32)
        lake_area = np.zeros(0, np.float64)
        lake_capacity = np.zeros(0, np.float64)
        lake_width = np.zeros(0, np.float64)
        lake_elev = np.zeros(0, np.float64)
        if cfg.lakes_enabled and lake_p is not None:
            lakes = read_polygons(lake_p, dem.crs, "glacial lakes")
            lake_values = np.arange(1, len(lakes) + 1, dtype=np.int32)
            lake_id2 = rasterize_polygons(lakes, dem, lake_values, fill=0, dtype="int32")
            lake_id2[~valid] = 0
            nlake = len(lakes)
            lake_area = np.zeros(nlake, np.float64)
            lake_capacity = np.zeros(nlake, np.float64)
            lake_width = np.zeros(nlake, np.float64)
            lake_elev = np.zeros(nlake, np.float64)
            for j in range(nlake):
                pix = (lake_id2 == j + 1) & valid
                a = float(pix.sum() * area_m2)
                lake_area[j] = max(a, 1.0)
                row = lakes.iloc[j]
                vol = _attr_first(row, ("volume_m3", "vol_m3", "volume", "Volume_m3"), np.nan)
                depth = _attr_first(row, ("mean_depth_m", "depth_m", "depth", "MeanDepth"), np.nan)
                widthv = _attr_first(row, ("outlet_width_m", "outlet_width", "width_m"), cfg.lake_default_outlet_width_m)
                lake_width[j] = max(widthv, 0.1)
                if np.isfinite(vol) and vol > 0:
                    lake_capacity[j] = vol
                elif np.isfinite(depth) and depth > 0:
                    lake_capacity[j] = a * depth
                else:
                    lake_capacity[j] = a * cfg.lake_default_mean_depth_m
                lake_elev[j] = float(np.nanmean(z2[pix])) if pix.any() else np.nan
        nlake = len(lake_area)
        lake_id_active = lake_id2[valid].astype(np.int32) - 1  # -1 means no lake

        # D8
        d8_p = optional_path(cfg, cfg.flow_direction_raster)
        if d8_p:
            d82 = aligned_raster(d8_p, dem, "nearest", dst_dtype="float32")
            d82 = np.nan_to_num(d82, nan=0).astype(np.uint8)
        else:
            log("D8 raster not supplied -> deriving steepest-descent D8 from DEM")
            d82 = derive_d8_pointer(z2, valid, cfg.d8_encoding.lower(), dx, dy)

        fac_p = optional_path(cfg, cfg.flow_accumulation_raster)
        fac2 = aligned_raster(fac_p, dem, "bilinear") if fac_p else None
        stream_p = optional_path(cfg, cfg.streams_raster)
        stream2 = aligned_raster(stream_p, dem, "nearest") if stream_p else None

        offsets = d8_offsets(cfg.d8_encoding.lower())
        downstream_flat = np.full(h * w, -1, np.int64)
        for code, (dr, dc) in offsets.items():
            rr, cc = np.nonzero(valid & (d82 == code))
            nr, nc = rr + dr, cc + dc
            inside = (nr >= 0) & (nr < h) & (nc >= 0) & (nc < w)
            rr, cc, nr, nc = rr[inside], cc[inside], nr[inside], nc[inside]
            ok = valid[nr, nc]
            srcf = rr[ok].astype(np.int64) * w + cc[ok]
            dstf = nr[ok].astype(np.int64) * w + nc[ok]
            downstream_flat[srcf] = dstf
        downstream_active = np.full(ncell, -1, np.int64)
        df = downstream_flat[grid_flat]
        ok = df >= 0
        downstream_active[ok] = flat_to_active[df[ok]]

        # ERA native cell mapping: precompute only if forcing metadata available
        era_cell = np.full(ncell, -1, np.int64)
        if forcing_meta is not None:
            xs, ys = pixel_centres_for_indices(dem.transform, rows, cols)
            tr = Transformer.from_crs(dem.crs, 4326, always_xy=True)
            lonp, latp = tr.transform(xs, ys)
            iy = nearest_grid_index(np.asarray(latp), forcing_meta["lat"])
            ix = nearest_grid_index(np.asarray(lonp), forcing_meta["lon"])
            era_cell = iy * forcing_meta["nx"] + ix

        # Determine lake outlet cell and lake downstream target.
        lake_outlet_active = np.full(nlake, -1, np.int64)
        lake_downstream_active = np.full(nlake, -1, np.int64)
        if nlake:
            for j in range(nlake):
                pix_flat = np.flatnonzero((lake_id2.reshape(-1) == j + 1) & valid.reshape(-1))
                candidates = []
                for pf in pix_flat:
                    df0 = downstream_flat[pf]
                    if df0 >= 0 and lake_id2.reshape(-1)[df0] != j + 1:
                        score = fac2.reshape(-1)[pf] if fac2 is not None and np.isfinite(fac2.reshape(-1)[pf]) else -z2.reshape(-1)[pf]
                        candidates.append((float(score), int(pf), int(df0)))
                if not candidates:
                    # fallback to lowest lake pixel; downstream may be absent
                    pf = int(pix_flat[np.argmin(z2.reshape(-1)[pix_flat])]) if len(pix_flat) else -1
                    df0 = downstream_flat[pf] if pf >= 0 else -1
                else:
                    _, pf, df0 = max(candidates, key=lambda x: x[0])
                if pf >= 0:
                    lake_outlet_active[j] = flat_to_active[pf]
                if df0 >= 0:
                    lake_downstream_active[j] = flat_to_active[df0]

        # Build routing graph nodes: land pixels + lake supernodes.
        # Lake surface pixels are not land-routing nodes; upstream cells route into the lake node.
        is_lake = lake_id_active >= 0
        land_active = np.flatnonzero(~is_lake)
        land_node = np.full(ncell, -1, np.int64)
        land_node[land_active] = np.arange(len(land_active), dtype=np.int64)
        nland = len(land_active)
        nnodes = nland + nlake
        node_down = np.full(nnodes, -1, np.int64)

        for ai in land_active:
            node = land_node[ai]
            da = downstream_active[ai]
            if da < 0:
                continue
            lid = lake_id_active[da]
            if lid >= 0:
                node_down[node] = nland + lid
            elif land_node[da] >= 0:
                node_down[node] = land_node[da]

        for j in range(nlake):
            da = lake_downstream_active[j]
            if da < 0:
                continue
            lid2 = lake_id_active[da]
            if lid2 >= 0 and lid2 != j:
                node_down[nland + j] = nland + lid2
            elif land_node[da] >= 0:
                node_down[nland + j] = land_node[da]

        indeg = np.zeros(nnodes, np.int64)
        for u, v in enumerate(node_down):
            if v >= 0:
                indeg[v] += 1
        stack = list(np.flatnonzero(indeg == 0))
        topo = []
        while stack:
            u = stack.pop()
            topo.append(u)
            v = node_down[u]
            if v >= 0:
                indeg[v] -= 1
                if indeg[v] == 0:
                    stack.append(int(v))
        if len(topo) != nnodes:
            raise ValueError("D8/lake routing graph contains a cycle. Check DEM conditioning and D8 pointers.")
        topo = np.asarray(topo, np.int64)

        # routing residence time per land node
        land_slope = slope[land_active]
        if cfg.use_manning_pixel_travel_time:
            v_ms = (1.0 / cfg.channel_manning_n) * (cfg.channel_hydraulic_radius_m ** (2.0 / 3.0)) * np.sqrt(
                np.maximum(np.tan(np.deg2rad(land_slope)), cfg.channel_min_slope)
            )
            # D8 diagonal vs orthogonal path length
            codes_land = d82[rows[land_active], cols[land_active]]
            diag_codes = {1, 4, 16, 64} if cfg.d8_encoding.lower() == "whitebox" else {2, 8, 32, 128}
            length = np.where(np.isin(codes_land, list(diag_codes)), math.hypot(dx, dy), max(dx, dy))
            k_route = np.maximum(length / np.maximum(v_ms, 1e-4) / SECONDS_PER_DAY, cfg.route_min_k_days)
        else:
            k_route = np.full(nland, max(cfg.route_k_days, cfg.route_min_k_days), np.float64)
        route_release = 1.0 - np.exp(-1.0 / np.maximum(k_route, 1e-8))

        # stream flag per land node (optional)
        stream_land = np.zeros(nland, bool)
        if stream2 is not None:
            stream_land = np.nan_to_num(stream2[rows[land_active], cols[land_active]], nan=0) > 0

        # subbasin properties
        sub_area = np.bincount(sub, weights=np.full(ncell, area_m2), minlength=nsub)
        glacier_area = np.bincount(sub, weights=glacier.astype(float) * area_m2, minlength=nsub)

        res = dict(
            ids=ids.astype(np.int64), rows=rows.astype(np.int32), cols=cols.astype(np.int32),
            grid_flat=grid_flat.astype(np.int64), sub=sub, elevation=z2[valid].astype(np.float32),
            slope=slope, aspect=aspect, terrain_factor=terrain_factor, glacier=glacier.astype(np.uint8),
            debris=debris, ice_thickness0=ice_thickness, soil_capacity=soil_cap,
            infiltration_capacity=infil_cap, percolation_fraction=perc_frac,
            lake_id=lake_id_active, era_cell=era_cell.astype(np.int64), downstream_active=downstream_active,
            land_active=land_active.astype(np.int64), land_node=land_node, node_down=node_down,
            topo=topo, route_release=route_release.astype(np.float32), stream_land=stream_land.astype(np.uint8),
            lake_area=lake_area, lake_capacity=lake_capacity, lake_width=lake_width, lake_elev=lake_elev,
            lake_outlet_active=lake_outlet_active, lake_downstream_active=lake_downstream_active,
            sub_area=sub_area, glacier_area=glacier_area,
            shape=np.array([h, w], np.int32), transform=np.array(tuple(dem.transform)[:6], np.float64),
            crs_wkt=np.array([dem.crs.to_wkt()]), pixel_area_m2=np.array([area_m2], np.float64),
            nland=np.array([nland], np.int64), nlake=np.array([nlake], np.int64)
        )

    if cfg.save_preprocess_cache:
        np.savez_compressed(cache, **res)
        meta_json.write_text(json.dumps(filesig, indent=2), encoding="utf-8")
        log(f"Preprocessing cache -> {cache}")
    return res


# =============================================================================
# VALIDATION HELPERS
# =============================================================================
def read_validation_to_active(path: Path, pre: dict):
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import reproject, Resampling
    h, w = map(int, pre["shape"])
    tr = Affine(*pre["transform"])
    crs = pre["crs_wkt"][0]
    dst = np.full((h, w), np.nan, np.float32)
    with rasterio.open(path) as src:
        a = src.read(1)
        src_nodata = src.nodata
        reproject(a, dst, src_transform=src.transform, src_crs=src.crs, src_nodata=src_nodata,
                  dst_transform=tr, dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.nearest)
    return dst[pre["rows"], pre["cols"]]


def binary_metrics(obs, mod):
    obs = np.asarray(obs).astype(bool)
    mod = np.asarray(mod).astype(bool)
    tp = int(np.sum(obs & mod))
    tn = int(np.sum(~obs & ~mod))
    fp = int(np.sum(~obs & mod))
    fn = int(np.sum(obs & ~mod))
    n = tp + tn + fp + fn
    precision = tp / (tp + fp) if tp + fp else np.nan
    recall = tp / (tp + fn) if tp + fn else np.nan
    f1 = 2 * precision * recall / (precision + recall) if np.isfinite(precision) and np.isfinite(recall) and precision + recall else np.nan
    csi = tp / (tp + fp + fn) if tp + fp + fn else np.nan
    acc = (tp + tn) / n if n else np.nan
    bias = (tp + fp) / (tp + fn) if tp + fn else np.nan
    return dict(n=n, tp=tp, tn=tn, fp=fp, fn=fn, accuracy=acc, precision=precision,
                recall=recall, f1=f1, csi=csi, frequency_bias=bias)


def write_validation_map(cfg, pre, date, sensor, obs, model, validmask):
    # Validation metrics are retained, but map-file output is disabled.
    return
    if not cfg.write_validation_maps:
        return
    import rasterio
    from rasterio.transform import Affine
    od = Path(cfg.out_dir) / "validation_maps"
    od.mkdir(parents=True, exist_ok=True)
    h, w = map(int, pre["shape"])
    arr = np.full((h, w), 255, np.uint8)
    # 0 true negative, 1 true positive, 2 false positive, 3 false negative, 255 nodata
    code = np.zeros(validmask.sum(), np.uint8)
    o, m = obs[validmask].astype(bool), model[validmask].astype(bool)
    code[o & m] = 1
    code[~o & m] = 2
    code[o & ~m] = 3
    active_idx = np.flatnonzero(validmask)
    arr[pre["rows"][active_idx], pre["cols"][active_idx]] = code
    profile = dict(driver="GTiff", height=h, width=w, count=1, dtype="uint8", nodata=255,
                   crs=pre["crs_wkt"][0], transform=Affine(*pre["transform"]), compress="deflate")
    with rasterio.open(od / f"{sensor}_{date:%Y%m%d}_comparison.tif", "w", **profile) as dst:
        dst.write(arr, 1)


# =============================================================================
# DISTRIBUTED MODEL CORE
# =============================================================================
def run_model(cfg: Config, pre: dict, fo: dict):
    from scipy.special import ndtr

    dates = fo["dates"]
    nd = len(dates)
    ncell = len(pre["rows"])
    ids = pre["ids"]
    nsub = len(ids)
    area = float(pre["pixel_area_m2"][0])
    sub = pre["sub"].astype(np.int64)
    glacier0 = pre["glacier"].astype(bool)
    lake_id = pre["lake_id"].astype(np.int64)
    landmask = lake_id < 0
    glacier = glacier0 & landmask
    nonglacier = (~glacier) & landmask
    terrain = pre["terrain_factor"].astype(np.float64)
    debris = pre["debris"].astype(np.float64)
    era_cell = pre["era_cell"].astype(np.int64)
    if (era_cell < 0).any():
        raise ValueError("ERA-cell mapping absent. Re-run preprocess/all after loading forcing metadata.")

    # States (pixelwise)
    swe = np.zeros(ncell, np.float64)
    liq = np.zeros(ncell, np.float64)
    soil = pre["soil_capacity"].astype(np.float64) * cfg.soil_initial_fraction
    soil[~nonglacier] = 0.0
    fast_store = np.zeros(ncell, np.float64)       # mm over pixel
    gw_store = np.zeros(ncell, np.float64)         # mm over pixel
    glacier_store = np.zeros(ncell, np.float64)    # mm over pixel
    ice_thickness = pre["ice_thickness0"].astype(np.float64).copy()
    cumulative_ice_loss_kg = np.zeros(ncell, np.float64)
    cumulative_surface_mb_mmwe = np.zeros(ncell, np.float64)

    # Main-run pixel statistics accumulated for the per-pixel CSV.
    cumulative_snowfall_mm = np.zeros(ncell, np.float64)
    cumulative_rain_mm = np.zeros(ncell, np.float64)
    cumulative_snowmelt_mm = np.zeros(ncell, np.float64)
    cumulative_ice_melt_mmwe = np.zeros(ncell, np.float64)
    cumulative_infiltration_mm = np.zeros(ncell, np.float64)
    cumulative_percolation_mm = np.zeros(ncell, np.float64)
    cumulative_et_mm = np.zeros(ncell, np.float64)
    cumulative_local_runoff_mm = np.zeros(ncell, np.float64)
    sum_routed_discharge_m3s = np.zeros(ncell, np.float64)
    max_routed_discharge_m3s = np.zeros(ncell, np.float64)

    nland = int(pre["nland"][0])
    nlake = int(pre["nlake"][0])
    land_active = pre["land_active"].astype(np.int64)
    land_node = pre["land_node"].astype(np.int64)
    node_down = pre["node_down"].astype(np.int64)
    topo = pre["topo"].astype(np.int64)
    route_rel = pre["route_release"].astype(np.float64)
    route_store = np.zeros(nland, np.float64)  # m3

    # Lake states
    lake_area = pre["lake_area"].astype(np.float64)
    lake_capacity = pre["lake_capacity"].astype(np.float64)
    lake_width = pre["lake_width"].astype(np.float64)
    lake_storage = lake_capacity * cfg.lake_initial_storage_fraction if nlake else np.zeros(0)

    # Lapse-rate shift of ERA5 temperature to pixel elevation (relative to the ERA5 cell's mean DEM height).
    z_pix = pre["elevation"].astype(np.float64)
    ncells_era = int(era_cell.max()) + 1
    cnt = np.bincount(era_cell, minlength=ncells_era).astype(np.float64)
    zsum = np.bincount(era_cell, weights=np.nan_to_num(z_pix), minlength=ncells_era)
    z_cell_mean = np.where(cnt > 0, zsum / np.maximum(cnt, 1.0), 0.0)
    t_shift = cfg.lapse_rate_c_per_m * (np.nan_to_num(z_pix) - z_cell_mean[era_cell]) + cfg.temp_bias_c
    log(f"Temperature lapse correction {cfg.lapse_rate_c_per_m * 1000:.2f} C/km: pixel shift range "
        f"{t_shift.min():.1f} .. {t_shift.max():.1f} C")

    k_fast = 1.0 - np.exp(-1.0 / cfg.k_fast_days)
    k_gw = 1.0 - np.exp(-1.0 / cfg.k_groundwater_days)
    k_gl = 1.0 - np.exp(-1.0 / cfg.k_glacier_days)
    span = cfg.tt_rain - cfg.tt_snow

    # Validation files
    s1_files = discover_validation_files(cfg.sentinel1_wetsnow_dir)
    s2_files = discover_validation_files(cfg.sentinel2_snow_dir)
    s1_rows, s2_rows = [], []

    # Minimal retained daily output.
    # Only routed subbasin discharge is kept when gauge validation is requested.
    out = {}
    want_q = bool(cfg.qobs_csv) or cfg.write_discharge_csv
    if want_q:
        out["q_total_m3s"] = np.zeros((nd, nsub), np.float32)
    lake_out = None

    mass = dict(P_m3=0.0, ice_source_m3we=0.0, ET_m3=0.0, outlet_Q_m3=0.0,
                rapid_release_m3=0.0, lake_evap_m3=0.0)

    # Periodic snapshot files are disabled in the minimal-output build.
    def write_snapshot(day_index, snowmelt, ice_melt, local_runoff_mm, routed_q_pixel):
        return

    # Cells through which each subbasin's water leaves it (downstream cell is outside the subbasin, in a lake,
    # or off the domain). Outlet discharge is the SUM over them (it used to be the max, which under-reports a
    # subbasin with more than one exit cell).
    sub_exits = []
    if want_q:
        da_all = pre["downstream_active"].astype(np.int64)
        for s_i in range(nsub):
            ai = np.flatnonzero(sub == s_i)
            ex = [a for a in ai if da_all[a] < 0 or sub[da_all[a]] != s_i or lake_id[da_all[a]] >= 0]
            sub_exits.append(np.asarray(ex if ex else ai, np.int64))

    def step(d: int, record: bool):
        nonlocal swe, liq, soil, fast_store, gw_store, glacier_store, ice_thickness, lake_storage, route_store

        # Native ERA5 forcing. Every model pixel receives exactly its mapped ERA grid-cell value.
        T = fo["t"][d, era_cell].astype(np.float64) + t_shift
        sigma = np.maximum(fo["sigma"][d, era_cell].astype(np.float64), cfg.sigma_min)
        P = fo["p"][d, era_cell].astype(np.float64)

        # Lake pixels are handled in the lake water balance, not terrestrial snow/soil hydrology.
        P_land = np.where(landmask, P, 0.0)
        T_land = T

        if cfg.use_subdaily_temperature_spread:
            x = T_land / sigma
            pdd = T_land * ndtr(x) + sigma * INV_SQRT_2PI * np.exp(-0.5 * x * x)
            pdd = np.maximum(pdd, 0.0)
        else:
            pdd = np.maximum(T_land, 0.0)

        sf = np.clip((cfg.tt_rain - T_land) / span, 0.0, 1.0)
        snowfall = P_land * sf * cfg.snowfall_correction
        rain = P_land * (1.0 - sf)

        snow_store_before = swe.copy()
        swe += snowfall
        pot_snow = cfg.ddf_snow * pdd * terrain
        melt_s = np.minimum(swe, pot_snow)
        swe -= melt_s
        liq += melt_s + rain
        refreeze = np.minimum(liq, cfg.refreeze_coef * cfg.ddf_snow * np.maximum(-T_land, 0.0))
        swe += refreeze
        liq -= refreeze
        drain = np.maximum(liq - cfg.water_holding * swe, 0.0)
        liq -= drain

        # Glacier ice ablation begins only after available snow-melt energy is not fully consumed by snow.
        frac_energy_left = np.where(pot_snow > 1e-12,
                                    np.clip(1.0 - melt_s / np.maximum(pot_snow, 1e-12), 0, 1), 1.0)
        active_glacier = glacier & (ice_thickness > 0)
        pot_ice = cfg.ddf_ice * pdd * terrain * debris * frac_energy_left
        available_ice_mmwe = ice_thickness * 1000.0 * cfg.rho_ice_kg_m3 / RHO_WATER
        ice_melt = np.where(active_glacier, np.minimum(pot_ice, available_ice_mmwe), 0.0)
        thickness_loss_m = ice_melt / 1000.0 * RHO_WATER / cfg.rho_ice_kg_m3
        ice_thickness = np.maximum(ice_thickness - thickness_loss_m, 0.0)
        ice_loss_kg = ice_melt / 1000.0 * area * RHO_WATER
        cumulative_ice_loss_kg[:] += ice_loss_kg

        # Glacier surface mass balance: snowfall accumulation - snow ablation - ice ablation.
        smb_mmwe = np.where(glacier, snowfall - melt_s - ice_melt, 0.0)
        cumulative_surface_mb_mmwe[:] += smb_mmwe

        # Non-glacier soil water balance.
        water_ng = np.where(nonglacier, drain, 0.0)
        capacity = pre["soil_capacity"].astype(np.float64)
        space = np.maximum(capacity - soil, 0.0)
        infil = np.minimum(water_ng, np.minimum(pre["infiltration_capacity"], space))
        surface_excess = water_ng - infil
        soil += infil
        et_demand = cfg.et_coef * np.maximum(T_land, 0.0)
        et = np.minimum(soil, np.where(nonglacier, et_demand, 0.0))
        soil -= et
        perc = np.minimum(soil, soil * pre["percolation_fraction"].astype(np.float64))
        soil -= perc
        gw_store += perc
        fast_store += surface_excess

        # Glacier liquid drainage + ice melt enters glacier reservoir.
        glacier_input = np.where(glacier, drain + ice_melt, 0.0)
        glacier_store += glacier_input

        q_fast_mm = fast_store * k_fast
        q_gw_mm = gw_store * k_gw
        q_gl_mm = glacier_store * k_gl
        fast_store -= q_fast_mm
        gw_store -= q_gw_mm
        glacier_store -= q_gl_mm
        local_runoff_mm = q_fast_mm + q_gw_mm + q_gl_mm
        local_vol = local_runoff_mm * area * 1e-3  # m3/day

        # Pixel/lake network routing.
        node_in = np.zeros(nland + nlake, np.float64)
        routed_q_pixel = np.zeros(ncell, np.float64)
        lake_qout = np.zeros(nlake, np.float64)
        lake_rapid = np.zeros(nlake, np.float64)

        # Add land-pixel local runoff to corresponding land nodes.
        node_in[land_node[land_active]] += local_vol[land_active]

        # Direct precipitation/evaporation on each lake is handled when its lake node is processed.
        for u in topo:
            if u < nland:
                ai = land_active[u]
                route_store[u] += node_in[u]
                released = route_store[u] * route_rel[u]
                route_store[u] -= released
                routed_q_pixel[ai] = released / SECONDS_PER_DAY
                v = node_down[u]
                if v >= 0:
                    node_in[v] += released
                else:
                    if record:
                        mass["outlet_Q_m3"] += released
            else:
                j = u - nland
                inflow = node_in[u]
                # Area-weighted native ERA forcing over lake pixels.
                lpix = lake_id == j
                if lpix.any():
                    p_lake_mm = float(np.mean(P[lpix]))
                    t_lake = float(np.mean(T[lpix]))
                else:
                    p_lake_mm = 0.0
                    t_lake = 0.0
                pvol = p_lake_mm * 1e-3 * lake_area[j]
                evap_mm = cfg.lake_evap_coef * max(t_lake, 0.0)
                evol = min(lake_storage[j] + inflow + pvol, evap_mm * 1e-3 * lake_area[j])
                lake_storage[j] += inflow + pvol - evol

                cap = max(lake_capacity[j], 1.0)
                threshold = cfg.lake_outlet_threshold_fraction * cap
                normal = 0.0
                if lake_storage[j] > threshold:
                    head = (lake_storage[j] - threshold) / max(lake_area[j], 1.0)
                    q_weir = cfg.lake_weir_coefficient * lake_width[j] * max(head, 0.0) ** 1.5  # m3/s
                    normal = min(lake_storage[j] - threshold, q_weir * SECONDS_PER_DAY)
                    lake_storage[j] -= normal

                rapid = 0.0
                if cfg.rapid_release_enabled and lake_storage[j] / cap >= cfg.rapid_trigger_fraction:
                    target = cfg.rapid_target_fraction * cap
                    excess = max(lake_storage[j] - target, 0.0)
                    # Internal short-timescale release, but volume is passed into daily routing.
                    qrapid = excess / max(cfg.rapid_release_hours * 3600.0, 1.0)
                    rapid = min(excess, qrapid * cfg.rapid_release_hours * 3600.0)
                    lake_storage[j] -= rapid

                total_out = normal + rapid
                lake_qout[j] = total_out / SECONDS_PER_DAY
                lake_rapid[j] = rapid / SECONDS_PER_DAY
                v = node_down[u]
                if v >= 0:
                    node_in[v] += total_out
                else:
                    if record:
                        mass["outlet_Q_m3"] += total_out
                if record:
                    mass["lake_evap_m3"] += evol
                    mass["rapid_release_m3"] += rapid

        if record:
            # Accumulate pixelwise statistics for the main simulation period.
            cumulative_snowfall_mm[:] += snowfall
            cumulative_rain_mm[:] += rain
            cumulative_snowmelt_mm[:] += melt_s
            cumulative_ice_melt_mmwe[:] += ice_melt
            cumulative_infiltration_mm[:] += infil
            cumulative_percolation_mm[:] += perc
            cumulative_et_mm[:] += et
            cumulative_local_runoff_mm[:] += local_runoff_mm
            sum_routed_discharge_m3s[:] += routed_q_pixel
            np.maximum(max_routed_discharge_m3s, routed_q_pixel, out=max_routed_discharge_m3s)

            # Water-balance diagnostics.
            mass["P_m3"] += float(np.sum(P_land * area * 1e-3) + sum(
                (float(np.mean(P[lake_id == j])) if np.any(lake_id == j) else 0.0) * 1e-3 * lake_area[j]
                for j in range(nlake)))
            mass["ice_source_m3we"] += float(np.sum(ice_melt * area * 1e-3))
            mass["ET_m3"] += float(np.sum(et * area * 1e-3))

            # Compute subbasin outlet discharge only if observed-discharge validation is configured.
            if want_q:
                qsub = np.zeros(nsub, np.float64)
                for s_i in range(nsub):
                    qsub[s_i] = float(routed_q_pixel[sub_exits[s_i]].sum())
                out["q_total_m3s"][d] = qsub

            # Sentinel-1 wet snow validation: model wet snow = snow present + active melt.
            day = dates[d].normalize()
            model_wet = ((snow_store_before + snowfall) >= cfg.wet_snow_min_swe_mm) & (melt_s > 0) & landmask
            if day in s1_files:
                obs = read_validation_to_active(s1_files[day], pre)
                validv = np.isfinite(obs) & np.isin(obs.astype(np.int64), np.asarray(cfg.sentinel1_valid_values, np.int64)) & landmask
                if validv.any():
                    met = binary_metrics(obs[validv] == 1, model_wet[validv])
                    s1_rows.append(dict(date=day, file=str(s1_files[day]), **met))
                    write_validation_map(cfg, pre, day, "S1_wetsnow", obs, model_wet, validv)

            # Sentinel-2 snow cover validation: compare snow presence, not wetness.
            model_snow = (swe >= cfg.wet_snow_min_swe_mm) & landmask
            if day in s2_files:
                obs = read_validation_to_active(s2_files[day], pre)
                validv = np.isfinite(obs) & np.isin(obs.astype(np.int64), np.asarray(cfg.sentinel2_valid_values, np.int64)) & landmask
                if validv.any():
                    met = binary_metrics(obs[validv] == 1, model_snow[validv])
                    obs_scf = float(np.mean(obs[validv] == 1))
                    mod_scf = float(np.mean(model_snow[validv]))
                    s2_rows.append(dict(date=day, file=str(s2_files[day]), obs_scf=obs_scf,
                                        model_scf=mod_scf, scf_bias=mod_scf - obs_scf, **met))
                    write_validation_map(cfg, pre, day, "S2_snow", obs, model_snow, validv)

            write_snapshot(d, melt_s, ice_melt, local_runoff_mm, routed_q_pixel)

        return dict(snowmelt=melt_s, ice_melt=ice_melt, local_runoff_mm=local_runoff_mm,
                    routed_q_pixel=routed_q_pixel)

    # Spinup uses observed forcing but does not record/validate.
    ns = min(max(cfg.spinup_days, 0), nd)
    for c in range(max(cfg.spinup_cycles, 0)):
        log(f"spin-up cycle {c+1}/{cfg.spinup_cycles} ({ns} days)")
        for d in range(ns):
            step(d, False)

    # Storage before main run for mass balance.
    storage0 = (
        np.sum((swe + liq + soil + fast_store + gw_store + glacier_store) * area * 1e-3)
        + np.sum(route_store) + np.sum(lake_storage)
    )
    log(f"Main distributed run: {nd} days, {ncell:,} active pixels")
    last_diag = None
    for d in range(nd):
        last_diag = step(d, True)
        if (d + 1) % 50 == 0 or d == nd - 1:
            log(f"  day {d+1}/{nd}")

    storage1 = (
        np.sum((swe + liq + soil + fast_store + gw_store + glacier_store) * area * 1e-3)
        + np.sum(route_store) + np.sum(lake_storage)
    )
    mass["dStorage_m3"] = float(storage1 - storage0)
    # P + ice source = outlet Q + ET + lake evaporation + storage change.
    mass["residual_m3"] = (
        mass["P_m3"] + mass["ice_source_m3we"] - mass["outlet_Q_m3"] - mass["ET_m3"]
        - mass["lake_evap_m3"] - mass["dStorage_m3"]
    )

    states = dict(
        swe_mm=swe.astype(np.float32), liquid_snow_mm=liq.astype(np.float32),
        soil_moisture_mm=soil.astype(np.float32), groundwater_storage_mm=gw_store.astype(np.float32),
        fast_storage_mm=fast_store.astype(np.float32), glacier_storage_mm=glacier_store.astype(np.float32),
        ice_thickness_m=ice_thickness.astype(np.float32),
        cumulative_ice_mass_loss_kg=cumulative_ice_loss_kg.astype(np.float64),
        cumulative_ice_mass_loss_kg_m2=(cumulative_ice_loss_kg / float(pre["pixel_area_m2"][0])).astype(np.float32),
        cumulative_surface_mass_balance_mmwe=cumulative_surface_mb_mmwe.astype(np.float32),
        cumulative_snowfall_mm=cumulative_snowfall_mm.astype(np.float32),
        cumulative_rain_mm=cumulative_rain_mm.astype(np.float32),
        cumulative_snowmelt_mm=cumulative_snowmelt_mm.astype(np.float32),
        cumulative_ice_melt_mmwe=cumulative_ice_melt_mmwe.astype(np.float32),
        cumulative_infiltration_mm=cumulative_infiltration_mm.astype(np.float32),
        cumulative_percolation_mm=cumulative_percolation_mm.astype(np.float32),
        cumulative_et_mm=cumulative_et_mm.astype(np.float32),
        cumulative_local_runoff_mm=cumulative_local_runoff_mm.astype(np.float32),
        mean_routed_discharge_m3s=(sum_routed_discharge_m3s / max(nd, 1)).astype(np.float32),
        max_routed_discharge_m3s=max_routed_discharge_m3s.astype(np.float32),
    )
    validation = dict(s1=pd.DataFrame(s1_rows), s2=pd.DataFrame(s2_rows))
    return out, lake_out, states, validation, mass, last_diag


# =============================================================================
# OUTPUTS
# =============================================================================
def write_active_rasters(cfg: Config, pre: dict, layers: dict, folder: Path, prefix=""):
    import rasterio
    from rasterio.transform import Affine
    folder.mkdir(parents=True, exist_ok=True)
    h, w = map(int, pre["shape"])
    tr = Affine(*pre["transform"])
    for name, values in layers.items():
        vals = np.asarray(values)
        if vals.shape[0] != len(pre["rows"]):
            continue
        arr = np.full((h, w), -9999.0, np.float32)
        arr[pre["rows"], pre["cols"]] = vals.astype(np.float32)
        profile = dict(driver="GTiff", height=h, width=w, count=1, dtype="float32", nodata=-9999.0,
                       crs=pre["crs_wkt"][0], transform=tr, compress="deflate", predictor=3)
        with rasterio.open(folder / f"{prefix}{name}.tif", "w", **profile) as dst:
            dst.write(arr, 1)


def write_pixel_summary_csv(cfg: Config, pre: dict, states: dict):
    """Write one summary row per active model pixel.

    Coordinates are pixel centres transformed to WGS84 longitude/latitude.
    Dynamic columns summarize the main simulation period; this intentionally avoids
    a potentially enormous pixel x day CSV.
    """
    from rasterio.transform import Affine, xy
    from pyproj import CRS, Transformer

    rows = pre["rows"].astype(np.int64)
    cols = pre["cols"].astype(np.int64)
    tr = Affine(*pre["transform"])
    xs, ys = xy(tr, rows, cols, offset="center")
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    src_crs = CRS.from_wkt(str(pre["crs_wkt"][0]))
    if src_crs.to_epsg() == 4326:
        lon, lat = xs, ys
    else:
        transformer = Transformer.from_crs(src_crs, CRS.from_epsg(4326), always_xy=True)
        lon, lat = transformer.transform(xs, ys)
        lon, lat = np.asarray(lon), np.asarray(lat)

    sub_idx = pre["sub"].astype(np.int64)
    sub_ids = pre["ids"][sub_idx].astype(np.int64)
    lake_id = pre["lake_id"].astype(np.int64)
    downstream = pre["downstream_active"].astype(np.int64)
    downstream_pixel_id = np.where(downstream >= 0, downstream + 1, -1)

    initial_thickness = pre["ice_thickness0"].astype(np.float64)
    final_thickness = states["ice_thickness_m"].astype(np.float64)
    glacier_flag = pre["glacier"].astype(bool)

    df = pd.DataFrame({
        "pixel_id": np.arange(1, len(rows) + 1, dtype=np.int64),
        "row": rows,
        "col": cols,
        "latitude": lat,
        "longitude": lon,
        "subbasin_id": sub_ids,
        "era5_cell_id": pre["era_cell"].astype(np.int64),
        "lake_id": np.where(lake_id >= 0, lake_id + 1, 0),
        "downstream_pixel_id": downstream_pixel_id,
        "elevation_m": pre["elevation"],
        "slope_deg": pre["slope"],
        "aspect_deg": pre["aspect"],
        "terrain_melt_factor": pre["terrain_factor"],
        "is_glacier": glacier_flag.astype(np.uint8),
        "debris_melt_factor": pre["debris"],
        "initial_ice_thickness_m": initial_thickness,
        "final_ice_thickness_m": final_thickness,
        "ice_thickness_loss_m": np.maximum(initial_thickness - final_thickness, 0.0),
        "cumulative_ice_mass_loss_kg_m2": states["cumulative_ice_mass_loss_kg"] / float(pre["pixel_area_m2"][0]),
        "cumulative_surface_mass_balance_mmwe": states["cumulative_surface_mass_balance_mmwe"],
        "soil_capacity_mm": pre["soil_capacity"],
        "infiltration_capacity_mm_day": pre["infiltration_capacity"],
        "percolation_fraction_day": pre["percolation_fraction"],
        "final_swe_mm": states["swe_mm"],
        "final_liquid_snow_mm": states["liquid_snow_mm"],
        "final_soil_moisture_mm": states["soil_moisture_mm"],
        "final_groundwater_storage_mm": states["groundwater_storage_mm"],
        "final_fast_storage_mm": states["fast_storage_mm"],
        "final_glacier_storage_mm": states["glacier_storage_mm"],
        "cumulative_snowfall_mm": states["cumulative_snowfall_mm"],
        "cumulative_rain_mm": states["cumulative_rain_mm"],
        "cumulative_snowmelt_mm": states["cumulative_snowmelt_mm"],
        "cumulative_glacier_melt_mmwe": states["cumulative_ice_melt_mmwe"],
        "cumulative_infiltration_mm": states["cumulative_infiltration_mm"],
        "cumulative_percolation_mm": states["cumulative_percolation_mm"],
        "cumulative_et_mm": states["cumulative_et_mm"],
        "cumulative_runoff_mm": states["cumulative_local_runoff_mm"],
        "mean_routed_discharge_m3s": states["mean_routed_discharge_m3s"],
        "max_routed_discharge_m3s": states["max_routed_discharge_m3s"],
    })
    path = Path(cfg.out_dir) / "pixel_summary.csv"
    df.to_csv(path, index=False, float_format="%.6f")
    log(f"Wrote {path} ({len(df):,} pixels)")


def write_outputs(cfg: Config, pre: dict, fo: dict, out: dict, lake_out, states, validation, mass):
    """Write only spatial science outputs plus pixel_summary.csv."""
    od = Path(cfg.out_dir)
    od.mkdir(parents=True, exist_ok=True)

    # The only CSV output.
    write_pixel_summary_csv(cfg, pre, states)

    # Minimal spatial products required by the model objective.
    spatial_dir = od / "spatial_outputs"
    layers = {
        "cumulative_runoff_mm": states["cumulative_local_runoff_mm"],
        "mean_routed_runoff_m3s": states["mean_routed_discharge_m3s"],
        "max_routed_runoff_m3s": states["max_routed_discharge_m3s"],
        "cumulative_snowmelt_mm": states["cumulative_snowmelt_mm"],
        "cumulative_glacier_melt_mmwe": states["cumulative_ice_melt_mmwe"],
        "cumulative_glacier_mass_loss_kg_m2": states["cumulative_ice_mass_loss_kg_m2"],
        "cumulative_glacier_surface_mass_balance_mmwe": states["cumulative_surface_mass_balance_mmwe"],
    }
    write_active_rasters(cfg, pre, layers, spatial_dir)

    # Validation remains active but is reported only to the console.
    if not validation["s1"].empty:
        s1 = validation["s1"]
        log(f"Sentinel-1 wet-snow validation: {len(s1)} dates; "
            f"mean F1={s1['f1'].mean():.3f}, mean CSI={s1['csi'].mean():.3f}")
    if not validation["s2"].empty:
        s2 = validation["s2"]
        log(f"Sentinel-2 snow validation: {len(s2)} dates; "
            f"mean F1={s2['f1'].mean():.3f}, mean CSI={s2['csi'].mean():.3f}")

    log("Mass balance: " + json.dumps(mass, indent=2))


def validate_discharge(cfg: Config, fo: dict, pre: dict, out: dict):
    if not cfg.qobs_csv:
        return
    p = find_file(cfg, cfg.qobs_csv)
    obs = pd.read_csv(p)
    required = {"date", "subbasin_id", "q_obs_m3s"}
    if not required.issubset(obs.columns):
        raise ValueError(f"qobs CSV requires columns {sorted(required)}")
    obs["date"] = pd.to_datetime(obs["date"]).dt.normalize()
    ids = pre["ids"]
    rows = []
    for sid, g in obs.groupby("subbasin_id"):
        pos = np.where(ids == int(sid))[0]
        if not len(pos):
            continue
        si = pos[0]
        di = fo["dates"].get_indexer(pd.DatetimeIndex(g["date"]))
        ok = di >= 0
        if not ok.any():
            continue
        y = g.loc[ok, "q_obs_m3s"].to_numpy(float)
        x = out["q_total_m3s"][di[ok], si].astype(float)
        e = x - y
        rmse = float(np.sqrt(np.mean(e * e)))
        bias = float(np.mean(e))
        r = float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 and np.std(x) > 0 and np.std(y) > 0 else np.nan
        den = np.sum((y - y.mean()) ** 2)
        nse = 1.0 - np.sum((x - y) ** 2) / den if den > 0 else np.nan
        if len(x) > 1 and np.std(y) > 0 and y.mean() != 0:
            rr = np.corrcoef(x, y)[0, 1]
            alpha = np.std(x, ddof=1) / np.std(y, ddof=1)
            beta = np.mean(x) / np.mean(y)
            kge = 1.0 - np.sqrt((rr - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2)
        else:
            kge = np.nan
        rows.append(dict(subbasin_id=int(sid), n=len(x), bias_m3s=bias, rmse_m3s=rmse, r=r, nse=nse, kge=kge))
    if rows:
        df = pd.DataFrame(rows)
        log("Observed-discharge validation (console only):")
        for _, r in df.iterrows():
            log(f"  subbasin={int(r['subbasin_id'])} n={int(r['n'])} "
                f"RMSE={r['rmse_m3s']:.3f} m3/s NSE={r['nse']:.3f} KGE={r['kge']:.3f}")


# =============================================================================
# INSPECT / MAIN
# =============================================================================
def inspect_inputs(cfg: Config):
    import rasterio
    import geopandas as gpd
    log("RASTERS")
    for label, name in [
        ("DEM", cfg.dem_file), ("subbasins", cfg.subbasin_raster), ("slope", cfg.slope_file),
        ("aspect", cfg.aspect_file), ("debris", cfg.debris_factor_raster),
        ("glacier thickness", cfg.glacier_thickness_raster), ("soil capacity", cfg.soil_capacity_raster),
        ("infiltration", cfg.infiltration_capacity_raster), ("percolation", cfg.percolation_fraction_raster),
        ("D8", cfg.flow_direction_raster), ("flow accumulation", cfg.flow_accumulation_raster),
        ("streams", cfg.streams_raster),
    ]:
        if not name:
            print(f"{label}: <not configured>")
            continue
        try:
            p = find_file(cfg, name)
            with rasterio.open(p) as r:
                print(f"{label}: {p} | {r.width}x{r.height} CRS={r.crs} res={r.res} nodata={r.nodata}")
        except Exception as e:
            print(f"{label}: PROBLEM -> {e}")
    log("VECTORS")
    for label, name in [("glaciers", cfg.glacier_shp), ("lakes", cfg.lake_shp)]:
        try:
            p = find_file(cfg, name)
            g = gpd.read_file(p)
            print(f"{label}: {p} | {len(g)} features CRS={g.crs} fields={list(g.columns)}")
        except Exception as e:
            print(f"{label}: PROBLEM -> {e}")
    log("VALIDATION")
    print(f"Sentinel-1 wet snow maps: {len(discover_validation_files(cfg.sentinel1_wetsnow_dir)) if cfg.sentinel1_wetsnow_dir else 0}")
    print(f"Sentinel-2 snow maps: {len(discover_validation_files(cfg.sentinel2_snow_dir)) if cfg.sentinel2_snow_dir else 0}")
    log("ERA5")
    tl, pl = discover_nc(cfg)
    print("Temperature:", [(str(f), v) for f, v in tl])
    print("Precipitation:", [(str(f), v) for f, v in pl])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["inspect", "preprocess", "run", "all"])
    ap.add_argument("--data-dir")
    ap.add_argument("--proc-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--params")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args)
    require_inputs(cfg)

    if args.command == "inspect":
        inspect_inputs(cfg)
        return

    log("Building native ERA5 daily forcing (no spatial downscaling) ...")
    fo = build_native_forcing(cfg)
    pre = preprocess(cfg, forcing_meta=fo, force=args.force)
    if args.command == "preprocess":
        return

    log("Running fully distributed pixelwise model ...")
    out, lake_out, states, validation, mass, _ = run_model(cfg, pre, fo)
    write_outputs(cfg, pre, fo, out, lake_out, states, validation, mass)
    validate_discharge(cfg, fo, pre, out)
    if cfg.write_discharge_csv and "q_total_m3s" in out:
        q = out["q_total_m3s"]
        df = pd.DataFrame({"date": np.repeat(fo["dates"].values, len(pre["ids"])),
                           "subbasin_id": np.tile(pre["ids"], len(fo["dates"])),
                           "q_m3s": q.reshape(-1)})
        p = Path(cfg.out_dir) / "subbasin_discharge_daily.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)
        log(f"Daily subbasin discharge -> {p}")
    log("DONE")


if __name__ == "__main__":
    main()
