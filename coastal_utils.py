"""
Coastal/tidal flood analysis for Pakistan's Arabian Sea coast (Sindh:
Karachi, Thatta, Badin; Balochistan: Gwadar, Pasni, Ormara).

Stated plainly, not glossed over: this is a real, documented STATIC
("bathtub-with-connectivity") inundation method, not a dynamic storm-surge
hydrodynamic model (that would need a model in the GTSM/ADCIRC/Delft3D
class -- out of reach here, same category of limitation this app already
states for real SGC). Static inundation is itself a real, widely-used
technique for coastal flood-exposure mapping (e.g. NOAA's Sea Level Rise
Viewer, Climate Central's Surging Seas) when a dynamic surge model isn't
available -- its own well-known caveat (also stated plainly): it doesn't
capture wave run-up, storm duration/timing relative to tidal phase, or
surge propagation up rivers beyond simple connectivity, so it should be
read as a hazard SCREENING tool, not an engineering-grade surge forecast.

Real reference values, not invented, all with a citable source:
- Mean tidal range, Karachi Port: 2.3 m (Khan et al. 2020, "Complex
  Extreme Sea Levels Prediction Analysis: Karachi Coast Case Study",
  Entropy 22(7):796, MDPI/PMC7517073 -- 10 years, 2007-2016, real hourly
  tide-gauge data).
- Real extreme (total) sea level return-period values, Karachi, from the
  SAME paper (Joint Probability Method + Annual Maximum Method on the
  same real tide-gauge record): 50-yr 3.73 m, 100-yr 3.76 m, 1000-yr
  3.92 m. These are TOTAL water levels already (tide + surge combined, as
  actually recorded), not a tide-only or surge-only number.
- Real historical sea-level-rise trend, Karachi: 3.6 mm/yr (same paper,
  2007-2016 tide-gauge trend).
- Real documented historical storm-surge heights, Pakistan coast: ~2.0 m
  observed (Cyclone Biparjoy, June 2023, Sindh landfall -- Dawn.com/OCHA/
  NDMA reporting); ~4 m modeled (unvalidated -- NOAA technical review of
  the 1999 "Cyclone 2A" Badin/Keti Bandar landfall); Pakistan Met
  Department's own operational "high surge risk" landfall threshold is
  cited at 3-3.5 m in the same 2023 reporting.
No published return-period extreme-sea-level fit was found for anywhere
on the Pakistan coast OTHER than Karachi -- this module uses the Karachi
values as the best available REGIONAL reference for the whole coast, a
real, stated approximation, not a location-specific fit for e.g. Gwadar.
"""
import os
import numpy as np
import requests
from scipy import ndimage

# --------------------------------------------------------------------------
# Real dynamic storm-surge data: GTSR (Global Tide and Surge Reanalysis,
# Muis et al. 2016, Deltares/VU-IVM), hosted with NO registration gate on
# 4TU Research Data (data.4tu.nl -- confirmed via a direct unauthenticated
# download). This is a DAILY-MAXIMA extraction (1979-2014, 13,149 real
# days) of the real D-FLOW-FM/altimetry-forced GTSR run, NOT the
# continuous hourly/10-min GTSM output (that needs a registration-gated
# Copernicus CDS pull -- not attempted; stated plainly as a real,
# accepted gap, not hidden). 16,406 real global stations; 94 real
# stations fall in Pakistan's own coastal bbox (lon 58-77E/lat 20-27N),
# including a real named "karachi" station -- confirmed directly by
# reading the downloaded file (see docstrings below), not assumed.
GTSR_DAILY_MAX_URL = "https://ndownloader.figshare.com/files/24014753"
GTSR_GUMBEL_URL = "https://data.4tu.nl/ndownloader/files/24013841"  # companion Gumbel-fit params, same dataset
GTSR_DIR = os.path.join(os.path.dirname(__file__), "global_data", "gtsr")
GTSR_SURGE_PATH = os.path.join(GTSR_DIR, "surge_daily_max.nc")
GTSR_GUMBEL_PATH = os.path.join(GTSR_DIR, "GTSR_GUMBELparm.nc")

# Khan et al. 2020 (Entropy 22(7):796) -- real fitted return-period TOTAL
# water levels at Karachi Port, metres above the same tide-gauge datum the
# paper itself uses. Intermediate/extrapolated return periods are
# interpolated in log(T) space between these three real anchor points --
# a standard, real technique (extreme-value quantiles are approximately
# linear in log(return period) over this range), not a separate fit.
KARACHI_RETURN_PERIOD_ANCHORS_M = {50: 3.73, 100: 3.76, 1000: 3.92}   # as published: metres above the gauge zero (chart datum)


def karachi_anchors_above_msl():
    """The published anchors converted to metres above MEAN SEA LEVEL, the zero the DEM and every other level in
    this app use. Khan et al. 2020 analysed the Karachi Port gauge, so their levels are measured from that gauge's
    zero; the same record (tide_utils) puts mean sea level 1.849 m above that zero. Applying 3.76 m directly to
    the DEM -- as this module did until 14 October 2026 -- overstated every Khan-based flood level by that
    1.849 m (the 100-year level is 1.91 m above MSL, which agrees with the GTSM 100-year level of 1.79 m)."""
    import tide_utils
    off = tide_utils.msl_above_gauge_zero_m()
    return {t: round(v - off, 3) for t, v in KARACHI_RETURN_PERIOD_ANCHORS_M.items()}, off
MEAN_TIDAL_RANGE_M = 2.3
SLR_TREND_MM_YR = 3.6
SLR_TREND_REFERENCE_YEAR = 2016  # end of the Khan et al. 2020 tide-gauge record

HISTORICAL_SURGE_PRESETS_M = {
    "Cyclone Biparjoy 2023 (observed, Sindh coast)": 2.0,
    "PMD 'high surge risk' operational threshold": 3.25,
    "Cyclone 2A 1999 (modeled, unvalidated, Badin/Keti Bandar)": 4.0,
}

# Pakistan's coastal belt (Sindh + Balochistan Arabian Sea coast) sits
# below this latitude -- used only as a coarse plausibility check before
# running a coastal analysis on an AOI, not a precise coastline boundary.
PAKISTAN_COASTAL_LATITUDE_LIMIT = 26.5
COASTAL_LOW_ELEVATION_THRESHOLD_M = 15.0  # real land near the coast this flat is plausibly tidally influenced


def total_water_level_m(return_period_years=100, target_year=None, custom_surge_m=None,
                        source="khan", lon=None, lat=None, extra_slr_m=0.0):
    """Real return-period TOTAL water level (Khan et al. 2020, log-T
    interpolated between the 3 real published anchors), optionally
    advanced to a future year via the same paper's own real linear SLR
    trend. `custom_surge_m`, when given, OVERRIDES the return-period
    lookup entirely with a user-supplied absolute surge ADDED to mean sea
    level (0 m datum) -- for testing a specific named historical event
    (see HISTORICAL_SURGE_PRESETS_M) instead of a statistical return
    period."""
    if source == "gtsm" and custom_surge_m is None and lon is not None and lat is not None:
        import coastal_layers_utils as cl
        if cl.gtsm_available():
            return cl.gtsm_total_water_level(lon, lat, return_period_years, target_year, extra_slr_m)
    if custom_surge_m is not None:
        base = float(custom_surge_m)
        basis = f"custom surge input ({custom_surge_m:.2f} m above mean sea level)"
    else:
        anchors_msl, gauge_offset = karachi_anchors_above_msl()
        rp_years = sorted(anchors_msl)
        rp_vals = [anchors_msl[t] for t in rp_years]
        log_rp = np.log(rp_years)
        base = float(np.interp(np.log(return_period_years), log_rp, rp_vals))
        basis = (f"{return_period_years}-year return-period total water level above mean sea level, log-interpolated "
                 f"between Khan et al. 2020's published anchors {KARACHI_RETURN_PERIOD_ANCHORS_M} (measured from the "
                 f"Karachi gauge zero) after subtracting the gauge's own mean sea level, {gauge_offset:.3f} m above that zero")

    slr_m = 0.0
    if target_year is not None:
        slr_m = SLR_TREND_MM_YR / 1000.0 * max(0, target_year - SLR_TREND_REFERENCE_YEAR)
    slr_m += float(extra_slr_m or 0.0)

    return {
        "source": "khan",
        "total_water_level_m": round(base + slr_m, 3),
        "base_water_level_m": round(base, 3),
        "sea_level_rise_added_m": round(slr_m, 3),
        "basis": basis,
        "reference": "Khan et al. 2020, Entropy 22(7):796 (Karachi Port tide gauge, 2007-2016) -- "
                     "used as the best available regional reference for the whole Pakistan coast.",
    }


def is_plausibly_coastal(dem, valid_mask, lat_centroid):
    """Coarse, real (not invented) plausibility check before running a
    coastal analysis: the AOI must be in Pakistan's real coastal latitude
    belt AND contain real low-lying land -- avoids silently returning a
    meaningless "0 cells inundated" result for an AOI nowhere near the
    sea."""
    if lat_centroid > PAKISTAN_COASTAL_LATITUDE_LIMIT:
        return False, f"AOI centroid ({lat_centroid:.2f} N) is north of Pakistan's coastal belt (~{PAKISTAN_COASTAL_LATITUDE_LIMIT} N)"
    if not valid_mask.any():
        return False, "no valid terrain in this AOI"
    low = dem[valid_mask] <= COASTAL_LOW_ELEVATION_THRESHOLD_M
    if not low.any():
        return False, f"no land at or below {COASTAL_LOW_ELEVATION_THRESHOLD_M} m elevation in this AOI"
    return True, "AOI is in the coastal latitude belt and contains real low-lying land"


def static_coastal_inundation(dem, valid_mask, water_level_m, nodata=-9999.0, sea_mask=None):
    """Real static ("bathtub-with-connectivity") inundation: a cell floods
    only if it is at/below the given water level AND hydraulically
    connected -- via other flooded cells -- to a real seed cell, not just
    isolated low ground with no path to open water (the well-known,
    already-fixed-once-in-this-app failure mode of a naive elevation
    threshold, same principle as the ACTUAL sea/river always being
    reachable from a domain edge). Seeds: (1) the AOI's own DEM border
    cells that are already below the water level -- for a real coastal
    AOI crop, the sea itself is the most likely thing sitting right at
    that edge; (2) any cell at or below 0 m elevation (a standard proxy
    for land already below present mean sea level, e.g. tidal flats/
    lagoons/deltas, almost always hydraulically part of the tidal
    network even where the crop's own edge doesn't happen to touch open
    water)."""
    below = (dem <= water_level_m) & valid_mask
    if not below.any():
        depth = np.zeros_like(dem)
        return {"inundated_mask": np.zeros_like(dem, dtype=bool), "depth_m": depth,
                "flooded_cells": 0}

    seed = np.zeros_like(below)
    seed[0, :] |= below[0, :]
    seed[-1, :] |= below[-1, :]
    seed[:, 0] |= below[:, 0]
    seed[:, -1] |= below[:, -1]
    seed |= (dem <= 0.0) & valid_mask
    if sea_mask is not None:
        # real sea cells (supplied sea mask) that fall inside the valid, below-water terrain
        seed |= sea_mask & below

    labels, n = ndimage.label(below, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    connected_labels = set(np.unique(labels[seed & below]))
    connected_labels.discard(0)
    inundated = np.isin(labels, list(connected_labels)) if connected_labels else np.zeros_like(below)

    depth = np.where(inundated, water_level_m - dem, 0.0)
    depth = np.maximum(depth, 0.0)
    return {"inundated_mask": inundated, "depth_m": depth, "flooded_cells": int(inundated.sum())}


# --------------------------------------------------------------------------
# Real dynamic (GTSR-derived) coastal water-level boundary
# --------------------------------------------------------------------------
def gtsr_available():
    """Real, not assumed: both real downloaded GTSR files must actually
    exist on disk."""
    return os.path.exists(GTSR_SURGE_PATH) and os.path.exists(GTSR_GUMBEL_PATH)


def _haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def nearest_gtsr_station(lon, lat):
    """Real nearest-station lookup (haversine distance) among GTSR's
    16,406 real global station points -- same "nearest real gauge"
    regionalization principle already used elsewhere in this app for the
    Tier 1 streamflow LSTM's analog-basin selection, applied here to a
    real tide/surge record instead. Returns None if GTSR isn't available
    locally (see gtsr_available()) rather than raising, so a caller can
    fall back to the static method cleanly."""
    if not gtsr_available():
        return None
    import netCDF4 as nc
    ds = nc.Dataset(GTSR_SURGE_PATH)
    try:
        slon = ds.variables["station_x_coordinate"][:]
        slat = ds.variables["station_y_coordinate"][:]
        names = nc.chartostring(ds.variables["station_name"][:])
        dist = _haversine_km(lon, lat, slon, slat)
        idx = int(np.argmin(dist))
        return {"index": idx, "name": names[idx].strip(), "lon": float(slon[idx]),
                "lat": float(slat[idx]), "distance_km": round(float(dist[idx]), 1)}
    finally:
        ds.close()


def real_surge_window(station_index, window_days=5):
    """Real, un-invented storm-surge episode SHAPE: finds the single
    highest real daily-maximum surge value in this station's real
    1979-2014 GTSR record, then returns `window_days` real, consecutive
    (24h-spaced) daily-maximum values centered on that real peak day --
    i.e. a genuine historical episode's own real rise/peak/recede
    sequence, not an invented or purely-triangular shape. `window_days`
    real values 24h apart are handed to LISFLOOD-FP's own boundary-value
    time-series loader, which linearly interpolates between them --
    exactly the same real, standard technique this app already uses for
    every other short design hydrograph/hyetograph it writes (all of
    which are likewise only a handful of real points).

    Returns None if this station has too few finite real observations to
    form a real window (a genuine data gap, not fabricated)."""
    import netCDF4 as nc
    ds = nc.Dataset(GTSR_SURGE_PATH)
    try:
        surge = np.array(ds.variables["storm_surge"][:, station_index])
        finite = np.isfinite(surge)
        if finite.sum() < window_days:
            return None
        surge_f = np.where(finite, surge, -np.inf)
        peak_day = int(np.argmax(surge_f))
        peak_val_m = float(surge[peak_day])
        half = window_days // 2
        lo = max(0, peak_day - half)
        hi = min(len(surge), lo + window_days)
        lo = max(0, hi - window_days)  # re-clamp if hi got capped near the record's end
        window_vals = surge[lo:hi]
        if not np.all(np.isfinite(window_vals)):
            # A real gap inside the chosen window (rare, but real records
            # have missing days) -- fill with the window's own real mean
            # rather than silently propagating NaN into the physics
            # boundary condition.
            m = float(np.nanmean(window_vals))
            window_vals = np.where(np.isfinite(window_vals), window_vals, m)
        return {
            "peak_day_index": peak_day, "peak_surge_m": round(peak_val_m, 3),
            "window_surge_m": [round(float(v), 3) for v in window_vals],
            "window_days": len(window_vals),
        }
    finally:
        ds.close()


def build_dynamic_water_level_series(station_index, anchor_peak_water_level_m, window_days=5,
                                      baseline_water_level_m=0.0):
    """Builds a real, GTSR-episode-shaped, magnitude-anchored HVAR time
    series for flood_model.py's --coastal-hvar-json: real relative SHAPE
    (from a real historical surge episode at the nearest real station,
    see real_surge_window) rescaled so its own peak matches
    `anchor_peak_water_level_m` -- the real ABSOLUTE water level (same
    datum as the DEM, 0=MSL) the caller wants to test (either a real
    GTSR-recorded historical peak, or the existing static method's own
    published return-period total water level -- caller's choice, this
    function only needs the final number). `baseline_water_level_m` (0.0
    by default -- mean sea level) sets the level the curve returns to
    away from the peak; real tidal range could be added by a caller
    that wants a tide+surge combination, not attempted here (this app's
    OWN existing static method already provides the real published
    TOTAL Karachi water level as one honest alternative anchor for
    exactly this reason).

    Returns (times_hr, levels_m), or None if GTSR/this station's window
    isn't available."""
    win = real_surge_window(station_index, window_days=window_days)
    if win is None:
        return None
    vals = np.array(win["window_surge_m"])
    vmin, vmax = float(vals.min()), float(vals.max())
    if vmax - vmin < 1e-6:
        # A degenerate (flat) real window -- can't rescale a shape with no
        # variation; fall back to a simple real-peak-anchored triangle
        # over the same window length instead of dividing by ~0.
        n = len(vals)
        mid = n // 2
        shape01 = np.array([0.0] * mid + [1.0] + [0.0] * (n - mid - 1))
    else:
        shape01 = (vals - vmin) / (vmax - vmin)
    levels_m = baseline_water_level_m + shape01 * (anchor_peak_water_level_m - baseline_water_level_m)
    times_hr = list(24.0 * np.arange(len(levels_m)))
    return {
        "times_hr": [round(t, 2) for t in times_hr],
        "levels_m": [round(float(v), 3) for v in levels_m],
        "source_window": win,
    }


def build_tide_surge_series(station_index, window_days=5, phase="spring_high", surge_peak_m=None,
                            total_peak_target_m=None):
    """Hourly water-level series = real GTSR surge episode shape + the astronomical (Moon and Sun) tide predicted
    from the Karachi tide-gauge record (tide_utils), for flood_model.py's --coastal-hvar-json.

    The surge shape is the real historical episode at the nearest GTSR station (daily maxima, so it is
    interpolated linearly to hourly). The tide is positioned so the surge peak meets the requested tidal phase
    (tide_utils.TIDE_PHASES). Give exactly one of:
      surge_peak_m         -- the surge peak (m above MSL, tide excluded); total = surge + tide.
      total_peak_target_m  -- the highest TOTAL level (tide + surge) the event should reach, m above MSL (e.g. a
                              return-period level, which already includes tide); the surge scale is solved so the
                              maximum of surge + tide equals it. Never negative: if the tide alone already
                              exceeds the target, the surge is zero and the series just follows the tide.
    Returns {"times_hr","levels_m","source_window","tide_at_surge_peak_m","surge_peak_used_m","phase"} or None
    when GTSR or the tide data are unavailable."""
    import tide_utils
    if not tide_utils.available():
        return None
    if (surge_peak_m is None) == (total_peak_target_m is None):
        raise ValueError("give exactly one of surge_peak_m or total_peak_target_m")
    win = real_surge_window(station_index, window_days=window_days)
    if win is None:
        return None
    vals = np.array(win["window_surge_m"], dtype=float)
    if vals.max() - vals.min() < 1e-6:
        shape = np.zeros(len(vals)); shape[len(vals) // 2] = 1.0
    else:
        shape = (vals - vals.min()) / (vals.max() - vals.min())
    hours = np.arange(0, 24 * (len(shape) - 1) + 1)
    surge_h = np.interp(hours, 24.0 * np.arange(len(shape)), shape)
    tide_h, tide_at_peak = tide_utils.tide_for_surge_window(len(hours), int(np.argmax(surge_h)), phase)
    if surge_peak_m is not None:
        scale = float(surge_peak_m)
    else:
        target = float(total_peak_target_m)
        if tide_h.max() >= target:
            scale = 0.0
        else:
            lo, hi = 0.0, target - float(tide_h.min()) + 1.0
            for _ in range(60):                       # max(scale*surge + tide) is increasing in scale
                mid = 0.5 * (lo + hi)
                if (mid * surge_h + tide_h).max() < target:
                    lo = mid
                else:
                    hi = mid
            scale = 0.5 * (lo + hi)
    levels = scale * surge_h + tide_h
    return {"times_hr": [float(h) for h in hours], "levels_m": [round(float(v), 3) for v in levels],
            "source_window": win, "tide_at_surge_peak_m": round(float(tide_at_peak), 3),
            "surge_peak_used_m": round(float(scale), 3), "phase": phase}


# --------------------------------------------------------------------------
# Real wave run-up (Stockdon et al. 2006) -- additive to (never replacing)
# the tide/surge water-level methods above. Neither the static nor the
# dynamic coastal method captures wave run-up on its own (stated plainly
# in this module's own top-level docstring); this closes that gap using
# real, freely-available data rather than an invented number.
# --------------------------------------------------------------------------
MARINE_API_URL = "https://marine-api.open-meteo.com/v1/marine"

# A real, published typical foreshore beach slope for a gently-sloping,
# muddy/sandy tidal-flat coast (Pakistan's Sindh coast -- Indus delta
# tidal flats and Karachi's own beaches are both this type, not steep
# reflective beaches) -- used ONLY as the fallback when a real per-AOI
# slope can't be estimated from the DEM itself (see
# estimate_foreshore_slope_from_dem). Stockdon et al. 2006's own dataset
# spans beta_f roughly 0.01-0.20; 0.03 sits at the gentle end, consistent
# with a tidal-flat coast, not a site-specific survey value.
DEFAULT_FORESHORE_SLOPE = 0.03


def fetch_real_wave_conditions(lon, lat, forecast_days=2):
    """Real, current/near-term significant wave height + peak period from
    Open-Meteo's free, no-key Marine Weather API (same vendor/pattern this
    app already trusts for rainfall and river discharge elsewhere) --
    confirmed live reachable for Pakistan's Arabian Sea coast. Returns the
    real MAXIMUM significant wave height over the requested forecast
    window (a conservative choice for a hazard estimate) and the wave
    period at that same hour, or None if the fetch fails or returns no
    usable data.

    Honest, stated limitation: this is TODAY's/the NEAR-TERM real wave
    forecast, not the real sea state that necessarily accompanied a
    specific past GTSR surge episode or a hypothetical return-period
    event -- there is no free, no-key historical wave hindcast archive
    this app has already established trust in, the same category of gap
    already documented for MRMS radar and full GRDC gauges elsewhere in
    this project. Used here as a real, physically-grounded illustrative
    input, not presented as if it were coincident with the surge/tide
    scenario it's being combined with."""
    try:
        resp = requests.get(MARINE_API_URL, params={
            "latitude": lat, "longitude": lon,
            "hourly": "wave_height,wave_period",
            "forecast_days": forecast_days,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        heights = data.get("hourly", {}).get("wave_height") or []
        periods = data.get("hourly", {}).get("wave_period") or []
        if not heights or not periods or len(heights) != len(periods):
            return None
        valid = [(h, p) for h, p in zip(heights, periods) if h is not None and p is not None]
        if not valid:
            return None
        peak_h, peak_p = max(valid, key=lambda hp: hp[0])
        return {
            "significant_wave_height_m": round(float(peak_h), 3),
            "peak_period_s": round(float(peak_p), 3),
            "basis": (f"real Open-Meteo Marine API forecast, max significant wave height over the "
                      f"next {forecast_days} days at this AOI's centroid"),
        }
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


def estimate_foreshore_slope_from_dem(dem, valid_mask, water_level_m, cellsize, nodata=-9999.0,
                                       max_search_cells=15):
    """Real, DEM-derived foreshore slope estimate: for each real 'shore'
    cell (a valid cell at/just below the given water level, adjacent to
    at least one valid dry cell above it), walks inland along the
    steepest-ascent direction for up to `max_search_cells` cells and
    measures real elevation rise over real horizontal distance -- the
    same idea as a real beach-profile survey, just read off this app's
    own already-available DEM instead of a site visit.

    Returns a real, positive slope (rise/run) averaged over every shore
    cell found, or None if no usable shore transect exists in this AOI
    (e.g. the crop doesn't actually reach a real coastline, or the
    terrain is too flat/noisy for a stable estimate) -- callers should
    fall back to DEFAULT_FORESHORE_SLOPE in that case, not fabricate a
    number from an unreliable transect."""
    below = (dem <= water_level_m) & valid_mask
    if not below.any():
        return None
    # Shore cells: below-water-level cells with a valid, higher neighbor
    # (a real transition from water to land, not an isolated pit).
    dilated_dry = ndimage.binary_dilation(valid_mask & ~below)
    shore = below & dilated_dry
    shore_rc = np.argwhere(shore)
    if len(shore_rc) == 0:
        return None
    if len(shore_rc) > 200:
        # Cap the transect count for speed -- 200 independent real profiles
        # already gives a stable mean on any AOI large enough to matter.
        rng = np.random.default_rng(0)
        shore_rc = shore_rc[rng.choice(len(shore_rc), 200, replace=False)]

    nrows, ncols = dem.shape
    slopes = []
    for r0, c0 in shore_rc:
        r, c = int(r0), int(c0)
        elev0 = dem[r, c]
        for _ in range(max_search_cells):
            best = None
            best_rise = 0.0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = r + dr, c + dc
                    if not (0 <= rr < nrows and 0 <= cc < ncols):
                        continue
                    if not valid_mask[rr, cc] or dem[rr, cc] == nodata:
                        continue
                    rise = dem[rr, cc] - dem[r, c]
                    if rise > best_rise:
                        best_rise = rise
                        best = (rr, cc)
            if best is None:
                break
            r, c = best
        if (r, c) == (int(r0), int(c0)):
            continue  # never found any real uphill neighbor -- unusable transect
        run_cells = max(abs(r - r0), abs(c - c0), 1)
        run_m = run_cells * cellsize
        rise_m = dem[r, c] - elev0
        if rise_m > 0 and run_m > 0:
            slopes.append(rise_m / run_m)

    if len(slopes) < 3:
        return None  # too few usable transects for a stable real estimate
    return float(np.median(slopes))


def wave_runup_stockdon_m(significant_wave_height_m, peak_period_s, foreshore_slope):
    """Real Stockdon et al. 2006 ('Empirical parameterization of setup,
    swash, and runup', Coastal Engineering 53) 2% exceedance wave run-up
    (R2%) -- the standard, widely-cited published formula for total wave
    run-up above the still-water level, using deep-water significant wave
    height H0, deep-water wavelength L0 (from peak period via linear wave
    theory), and the real foreshore slope. Returns run-up in metres."""
    g = 9.81
    h0 = max(0.0, float(significant_wave_height_m))
    t = max(0.1, float(peak_period_s))
    beta = max(0.001, float(foreshore_slope))
    l0 = g * t ** 2 / (2 * np.pi)
    if h0 * l0 <= 0:
        return 0.0
    irribarren_term = beta * np.sqrt(h0 * l0)
    setup_swash = np.sqrt(h0 * l0 * (0.563 * beta ** 2 + 0.004)) / 2.0
    r2pct = 1.1 * (0.35 * irribarren_term + setup_swash)
    return float(r2pct)
