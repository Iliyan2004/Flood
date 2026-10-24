"""
Pakistan coastal data layers added from the user-supplied coastal model folder
(global_data/coastal/):

* GTSM-ERA5-E v2 return levels (2-100 yr) at 42 stations along the Pakistan
  coast -> a LOCAL still-water level for any coastal AOI, instead of applying
  Karachi's Khan et al. 2020 anchors to the whole coast.
* A 30 m sea mask (1 sea / 0 land / 255 outside the coastal AOI), used to seed
  the connected-bathtub flood and to measure distance to the sea.
* GMW 2025 mangrove extent and a 0-1 "vegetation buffer" (fraction of mangrove
  in an 11x11-pixel window) on the same grid.

Real limits, stated up front:
* GTSM levels are modelled, coarse near the coast, and their vertical datum is
  NOT reconciled with the SRTM DEM (the source folder's own README blocks
  inundation until that is done). At Karachi GTSM gives ~1.79 m for 100 yr;
  Khan et al. 2020 (tide gauge) gives 3.76 m from the GAUGE ZERO, i.e. ~1.91 m
  above mean sea level once the gauge's own 1.849 m offset is removed (see
  coastal_utils.karachi_anchors_above_msl) -- the two now agree within ~7%.
  Until 14 Oct 2026 the 3.76 m was applied to the DEM as if it were above MSL,
  which is why the two looked 2x apart.
* Vegetation only feeds the susceptibility index; it does not attenuate water
  in the inundation calculation.
"""
import json
import os

import numpy as np

COASTAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "coastal")
RETURN_LEVELS_CSV = os.path.join(COASTAL_DIR, "pakistan_coast_all_return_levels.csv")
SEA_MASK_TIF = os.path.join(COASTAL_DIR, "pakistan_sea_mask.tif")
MANGROVE_TIF = os.path.join(COASTAL_DIR, "mangroves_2025_dem_aligned.tif")
VEG_BUFFER_TIF = os.path.join(COASTAL_DIR, "vegetation_buffer_0to1.tif")

GTSM_DATUM_NOTE = ("GTSM-ERA5-E modelled total water level (Zenodo 10.5281/zenodo.14671593); vertical datum NOT "
                   "reconciled with the SRTM DEM and the model is coarse near the coast -- at Karachi it is about "
                   "half the Khan et al. 2020 tide-gauge value.")

# Susceptibility index (weights and ramps from the supplied model's own CVI-style defaults;
# only the factors this app has data for are used and the weights renormalised).
SUSC_WEIGHTS = {"elevation": 0.25, "distance_to_sea": 0.10, "slope": 0.10, "vegetation_buffer": 0.07}
SUSC_RAMPS = {  # (value giving score 0, value giving score 1)
    "elevation": (10.0, 0.0),        # m above mean high water
    "distance_to_sea": (20.0, 0.0),  # km
    "slope": (5.0, 0.0),             # degrees
    "vegetation_buffer": (1.0, 0.0),
}
SUSC_BREAKS = (0.2, 0.4, 0.6, 0.8)
SUSC_CLASS_NAMES = ["Very low", "Low", "Moderate", "High", "Very high"]
SUSC_COLORS = ["#fff2a8", "#fdc35c", "#f4803a", "#d7301f", "#7f0000"]
MEAN_TIDAL_RANGE_M = 2.3  # Karachi Port (Khan et al. 2020); MHW ~ MSL + range/2


def gtsm_available():
    return os.path.exists(RETURN_LEVELS_CSV)


def sea_mask_available():
    return os.path.exists(SEA_MASK_TIF)


def mangrove_available():
    return os.path.exists(MANGROVE_TIF)


_STATIONS = None


def gtsm_stations():
    """{station_index: {"lon","lat","levels": {rp: level_m}}}"""
    global _STATIONS
    if _STATIONS is not None:
        return _STATIONS
    import pandas as pd
    df = pd.read_csv(RETURN_LEVELS_CSV)
    out = {}
    for sid, g in df.groupby("station_index"):
        out[int(sid)] = {"lon": float(g.lon.iloc[0]), "lat": float(g.lat.iloc[0]),
                         "levels": {int(r): float(v) for r, v in zip(g.return_period_yr, g.level_m)}}
    _STATIONS = out
    return out


def nearest_gtsm_station(lon, lat):
    from coastal_utils import _haversine_km
    best = None
    for sid, s in gtsm_stations().items():
        d = _haversine_km(lon, lat, s["lon"], s["lat"])
        if best is None or d < best[0]:
            best = (d, sid, s)
    d, sid, s = best
    return {"station_index": sid, "lon": s["lon"], "lat": s["lat"], "distance_km": round(float(d), 1), "levels": s["levels"]}


def gtsm_return_level(station, return_period_years):
    """Log-T interpolation between the station's own published return periods; beyond 100 yr extends the
    last segment's log-linear slope (flagged by the caller), below 2 yr clamps to the 2-yr level."""
    rps = sorted(station["levels"])
    vals = [station["levels"][r] for r in rps]
    T = float(return_period_years)
    extrapolated = T > rps[-1]
    if extrapolated:
        slope = (vals[-1] - vals[-2]) / (np.log(rps[-1]) - np.log(rps[-2]))
        return vals[-1] + slope * (np.log(T) - np.log(rps[-1])), True
    return float(np.interp(np.log(T), np.log(rps), vals)), False


def gtsm_total_water_level(lon, lat, return_period_years, target_year=None, extra_slr_m=0.0):
    from coastal_utils import SLR_TREND_MM_YR, SLR_TREND_REFERENCE_YEAR
    st = nearest_gtsm_station(lon, lat)
    base, extrap = gtsm_return_level(st, return_period_years)
    slr = 0.0
    if target_year is not None:
        slr += SLR_TREND_MM_YR / 1000.0 * max(0, target_year - SLR_TREND_REFERENCE_YEAR)
    slr += float(extra_slr_m or 0.0)
    return {
        "total_water_level_m": round(base + slr, 3), "base_water_level_m": round(base, 3),
        "sea_level_rise_added_m": round(slr, 3),
        "basis": (f"{return_period_years}-year GTSM-ERA5-E return level at station {st['station_index']} "
                  f"({st['distance_km']} km away)" + (" -- extrapolated beyond the published 100 yr" if extrap else "")),
        "reference": GTSM_DATUM_NOTE, "source": "gtsm", "station": {k: st[k] for k in ("station_index", "lon", "lat", "distance_km")},
    }


# ----------------------------------------------------------------- raster layers on the DEM grid
def _grid_transform(header):
    from rasterio.transform import from_origin
    top = header["yllcorner"] + header["nrows"] * header["cellsize"]
    return from_origin(header["xllcorner"], top, header["cellsize"], header["cellsize"])


def _warp_from_file(path, header, resampling, fill, window_pad_m=0.0, dtype="float32"):
    """Reads a window of the raster around the DEM grid and resamples it onto the DEM grid."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.windows import from_bounds
    dst = np.full((int(header["nrows"]), int(header["ncols"])), fill, dtype=dtype)
    if not os.path.exists(path):
        return dst, False
    with rasterio.open(path) as src:
        if str(src.crs) != str(header["src_crs"]):
            return dst, False
        x0, y0 = header["xllcorner"], header["yllcorner"]
        x1, y1 = x0 + header["ncols"] * header["cellsize"], y0 + header["nrows"] * header["cellsize"]
        win = from_bounds(x0 - window_pad_m, y0 - window_pad_m, x1 + window_pad_m, y1 + window_pad_m, src.transform)
        win = win.round_offsets().round_lengths()
        arr = src.read(1, window=win, boundless=True, fill_value=src.nodata if src.nodata is not None else 0)
        tr = src.window_transform(win)
        reproject(arr, dst, src_transform=tr, src_crs=src.crs, dst_transform=_grid_transform(header),
                  dst_crs=header["src_crs"], resampling=resampling, src_nodata=src.nodata, dst_nodata=fill)
    return dst, True


def sea_mask_for_grid(header):
    """Boolean sea mask on the DEM grid (None when unavailable or the AOI lies outside the mask's coastal AOI)."""
    from rasterio.warp import Resampling
    if not sea_mask_available():
        return None
    arr, ok = _warp_from_file(SEA_MASK_TIF, header, Resampling.nearest, 255, dtype="uint8")
    if not ok or not (arr == 1).any():
        return None
    return arr == 1


def distance_to_sea_km(header, cellsize_m=30.0, pad_m=25000.0):
    """Distance (km) to the nearest sea-mask cell for every DEM-grid cell, measured on a padded window so the
    sea outside the AOI crop still counts. None when no sea lies within the window."""
    import rasterio
    from scipy import ndimage
    from rasterio.windows import from_bounds
    from rasterio.warp import reproject, Resampling
    if not sea_mask_available():
        return None
    with rasterio.open(SEA_MASK_TIF) as src:
        if str(src.crs) != str(header["src_crs"]):
            return None
        x0, y0 = header["xllcorner"], header["yllcorner"]
        x1, y1 = x0 + header["ncols"] * header["cellsize"], y0 + header["nrows"] * header["cellsize"]
        win = from_bounds(x0 - pad_m, y0 - pad_m, x1 + pad_m, y1 + pad_m, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win, boundless=True, fill_value=255)
        tr = src.window_transform(win)
        sea = arr == 1
        if not sea.any():
            return None
        dist_m = ndimage.distance_transform_edt(~sea) * abs(tr.a)
        dst = np.full((int(header["nrows"]), int(header["ncols"])), np.nan, dtype="float32")
        reproject(dist_m.astype("float32"), dst, src_transform=tr, src_crs=src.crs, dst_transform=_grid_transform(header),
                  dst_crs=header["src_crs"], resampling=Resampling.bilinear)
    return dst / 1000.0


def mangrove_for_grid(header):
    from rasterio.warp import Resampling
    arr, ok = _warp_from_file(MANGROVE_TIF, header, Resampling.nearest, 255, dtype="uint8")
    return (arr == 1) if ok else None


def vegetation_buffer_for_grid(header):
    from rasterio.warp import Resampling
    arr, ok = _warp_from_file(VEG_BUFFER_TIF, header, Resampling.bilinear, -9999.0)
    if not ok:
        return None
    return np.where(arr < 0, 0.0, arr).astype("float32")


def mangrove_stats(header, depth_m, valid, cellsize):
    """Mangrove area in the AOI and how much of it floods at the given depth grid."""
    m = mangrove_for_grid(header)
    if m is None:
        return None
    cell_km2 = cellsize * cellsize / 1e6
    mg = m & valid
    return {"mangrove_area_km2": round(float(mg.sum()) * cell_km2, 3),
            "mangrove_flooded_km2": round(float((mg & (depth_m > 0)).sum()) * cell_km2, 3),
            "source": "Global Mangrove Watch 2025"}


# ----------------------------------------------------------------- susceptibility index
def _slope_deg(dem, valid, cellsize):
    fill = np.where(valid, dem, np.nanmean(np.where(valid, dem, np.nan)) if valid.any() else 0.0)
    gy, gx = np.gradient(fill, cellsize)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def coastal_susceptibility(dem, valid, header, nodata=-9999.0):
    """5-class coastal flood susceptibility index (0-1). Returns
    {"index","classes"(1..5, 0 outside),"factors_used","weights_used"}. Factors: elevation above mean high
    water (MSL + half the Karachi tidal range), distance to sea, slope, mangrove buffer."""
    cs = header["cellsize"]
    layers = {"elevation": np.where(valid, dem - MEAN_TIDAL_RANGE_M / 2.0, np.nan),
              "slope": np.where(valid, _slope_deg(dem, valid, cs), np.nan)}
    d = distance_to_sea_km(header)
    if d is not None:
        layers["distance_to_sea"] = np.where(valid, d, np.nan)
    v = vegetation_buffer_for_grid(header)
    if v is not None:
        layers["vegetation_buffer"] = np.where(valid, v, np.nan)
    num = np.zeros(dem.shape); den = np.zeros(dem.shape); used = {}
    for name, arr in layers.items():
        w = SUSC_WEIGHTS[name]; z, o = SUSC_RAMPS[name]
        score = np.clip((arr - z) / (o - z), 0.0, 1.0)
        ok = np.isfinite(score)
        num += np.where(ok, w * score, 0.0); den += np.where(ok, w, 0.0); used[name] = w
    idx = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    idx = np.where(valid, idx, np.nan)
    cls = np.zeros(dem.shape, dtype="uint8")
    ok = np.isfinite(idx)
    cls[ok] = 1 + np.digitize(idx[ok], SUSC_BREAKS)
    tot = sum(used.values()); used = {k: round(v_ / tot, 3) for k, v_ in used.items()}
    return {"index": idx, "classes": cls, "factors_used": list(used), "weights_used": used}


def susceptibility_summary(cls, valid, cellsize):
    km2 = cellsize * cellsize / 1e6
    tot = float(valid.sum()) * km2
    rows = []
    for i, name in enumerate(SUSC_CLASS_NAMES, start=1):
        a = float((cls == i).sum()) * km2
        rows.append({"class": i, "name": name, "area_km2": round(a, 3), "pct": round(100 * a / tot, 1) if tot else 0.0})
    return rows


def level_library(dem, valid, levels, sea_mask=None, cellsize=30.0):
    """Area / max depth / mean depth / volume at each still-water level (m) -- one connected-bathtub per level."""
    from coastal_utils import static_coastal_inundation
    km2 = cellsize * cellsize / 1e6
    rows = []
    for L in levels:
        r = static_coastal_inundation(dem, valid, float(L), sea_mask=sea_mask)
        n = r["flooded_cells"]
        d = r["depth_m"][r["inundated_mask"]] if n else np.array([0.0])
        rows.append({"level_m": round(float(L), 3), "area_km2": round(n * km2, 4), "max_depth_m": round(float(d.max()), 3),
                     "mean_depth_m": round(float(d.mean()), 3) if n else 0.0, "volume_m3": round(float(d.sum()) * cellsize * cellsize, 1) if n else 0.0})
    return rows
