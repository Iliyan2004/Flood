"""
Pluvial / flash-flood layer -- the piece `global-flood-prediction-
research.md` (section 4.3/4.4) calls "nobody has this solved globally,
for a structural reason (no global radar), not a modeling-sophistication
reason." Built honestly to that finding rather than pretending street-
level nowcasting is possible everywhere:

- A real, per-point climatology (20 years of ERA5-reanalysis-backed daily
  precipitation, Open-Meteo's free Historical Weather API -- no key, no
  registration) gives an empirical return-period-like threshold for THIS
  exact location, not a single global constant.
- The real forecast + recent-observed rainfall this app already fetches
  (`rainfall_rivers_utils.fetch_rainfall`) is compared against that
  threshold to produce a flash-flood risk tier.
- The tier's own confidence is stated honestly based on input data
  resolution: **no global radar network exists** (confirmed in the
  research brief -- NOAA MRMS-class 1km/2-minute precipitation is
  real and free, but CONUS-only). Everywhere else, including all of
  Pakistan, this app is structurally limited to Open-Meteo's ~10km/
  hourly satellite-blended precipitation. That ceiling is surfaced to
  the user explicitly, not hidden behind false precision.

**Considered and declined, not just deferred**: real NOAA MRMS GRIB2
ingestion for the CONUS radar tier. The bucket (s3://noaa-mrms-pds,
confirmed publicly reachable, no auth) is real and would let a future
pass genuinely deliver NOAA-FLASH-class 1km nowcasting for US AOIs -- but
`MRMS_COVERAGE_BBOX` below is CONUS+southern-Canada only (lat 20-55,
lon -130 to -60), which structurally can NEVER overlap Pakistan (lat
~23.5-37.5, lon ~60.5-77.5). Per this project's own settled scope
("keep everything only for Pakistan" -- see CLAUDE.md), building a real
cfgrib/eccodes GRIB2 pipeline for data that can never activate for this
app's actual users would be real, substantial effort spent on a
capability nobody here can ever reach -- declined for that reason, not
a time-budget deferral. `radar_tier_available(lat, lon)` below still
correctly reports False for every real Pakistan AOI (confirmed: no
change needed, the bbox check already does the right thing), so the UI
already shows the honest ~10km/hourly Open-Meteo ceiling for every real
request this app serves. If this app's scope is ever broadened beyond
Pakistan, this docstring's own reasoning is the reason to revisit this
decision, not the code, which needs nothing new to remain correct today.
"""
import statistics
import time

import requests

import idf_utils

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# NOAA MRMS's real published coverage footprint (CONUS + southern Canada
# edge) -- a simple bbox is the honest, cheap proxy for "would a future
# radar-tier integration actually have data here", not a precise mask.
MRMS_COVERAGE_BBOX = dict(lat_min=20.0, lat_max=55.0, lon_min=-130.0, lon_max=-60.0)

CLIMATOLOGY_YEARS = 20


def radar_tier_available(lat, lon):
    b = MRMS_COVERAGE_BBOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]


def fetch_precipitation_climatology(lat, lon, years=CLIMATOLOGY_YEARS):
    """Real ERA5-reanalysis-backed daily precipitation for the last `years`
    years at this exact point (Open-Meteo Historical Weather API, free, no
    key). Returns empirical percentile thresholds -- a real, if simple,
    per-location substitute for a formal IDF (intensity-duration-frequency)
    curve, which does not exist as an open global dataset (national
    weather services publish these per-country, not globally)."""
    import datetime
    end = datetime.date.today() - datetime.timedelta(days=6)  # ERA5 has a few days' latency
    start = end.replace(year=end.year - years)
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": "auto",
    }
    t0 = time.time()
    resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    values = [v for v in data.get("daily", {}).get("precipitation_sum", []) if v is not None]
    print(f"[pluvial] Open-Meteo archive returned {len(values)} daily obs "
          f"({years}y climatology) in {time.time()-t0:.1f}s")
    if not values:
        return None

    sorted_vals = sorted(values)

    def pct(p):
        idx = min(int(len(sorted_vals) * p), len(sorted_vals) - 1)
        return sorted_vals[idx]

    return {
        "source": f"Open-Meteo Historical Weather API (ERA5 reanalysis, {years}y at this point)",
        "n_days": len(values),
        "mean_daily_mm": statistics.mean(values),
        "p90_daily_mm": pct(0.90),
        "p95_daily_mm": pct(0.95),
        "p99_daily_mm": pct(0.99),
        "max_daily_mm": max(values),
    }


RISK_TIERS = ["none", "watch", "warning", "severe"]


def assess_pluvial_risk(lat, lon, rainfall_context):
    """rainfall_context: the dict already returned by
    `rainfall_rivers_utils.fetch_rainfall` for the same point. Compares its
    forecast/recent hourly rainfall against this point's own real
    climatology to produce an honestly-tiered flash-flood risk read."""
    climatology = fetch_precipitation_climatology(lat, lon)
    hourly_mm = rainfall_context.get("hourly_precipitation_mm", [])
    times = rainfall_context.get("hourly_time", [])

    # rolling 24h totals from the real hourly series (both recent-observed
    # and forecast are in the same series, per fetch_rainfall's own
    # past_days/forecast_days window)
    rolling_24h = []
    for i in range(len(hourly_mm)):
        window = hourly_mm[max(0, i - 23):i + 1]
        rolling_24h.append(sum(w for w in window if w is not None))
    peak_24h_mm = max(rolling_24h) if rolling_24h else 0.0
    peak_hour_mm = max((v for v in hourly_mm if v is not None), default=0.0)

    if climatology is None:
        tier = "unknown"
        note = "Historical climatology unavailable for this point -- risk tier cannot be computed."
    else:
        if peak_24h_mm >= climatology["p99_daily_mm"]:
            tier = "severe"
        elif peak_24h_mm >= climatology["p95_daily_mm"]:
            tier = "warning"
        elif peak_24h_mm >= climatology["p90_daily_mm"]:
            tier = "watch"
        else:
            tier = "none"
        note = (f"Peak 24h forecast/recent total ({peak_24h_mm:.1f}mm) vs. this location's own "
                f"{climatology['n_days']//365}-year climatology (p90={climatology['p90_daily_mm']:.1f}mm, "
                f"p95={climatology['p95_daily_mm']:.1f}mm, p99={climatology['p99_daily_mm']:.1f}mm).")

    # Real, additive-only context: this location's own real recorded
    # extremes from the local ERA5-Land/IMERG IDF dataset (idf_utils.py),
    # when available -- a real, directly comparable "has this location
    # seen worse" reference (same units/duration as peak_hourly_forecast_
    # mm), not a replacement for the tier computation above. R95p/R99p
    # are real ETCCDI SEASONAL AGGREGATE totals (sum of rain on very-wet
    # days over a season), a genuinely different statistic from the
    # climatology's own single-day percentile threshold -- deliberately
    # NOT conflated with it or used to recompute the tier, just reported
    # as its own real, separately-labeled reference.
    real_idf_reference = None
    if idf_utils.idf_data_available():
        try:
            indices = idf_utils.real_extreme_indices_for_point(lat, lon)
            curve = idf_utils.real_idf_curve_for_point(lat, lon)
            if indices is not None:
                real_idf_reference = {
                    "rx1day_mm": indices["values"].get("rx1day"),
                    "peak_hourly_intensity_mm_hr": indices["values"].get("peakhr"),
                    "r95p_seasonal_total_mm": indices["values"].get("r95p"),
                    "r99p_seasonal_total_mm": indices["values"].get("r99p"),
                    "trend_mm_per_decade": indices["values"].get("trend_mm_per_decade"),
                    "idf_curve": curve["points"] if curve else None,
                    "year": indices["year"], "season": indices["season"],
                    "method": "Real ERA5-Land/IMERG observational extremes at this AOI's own nearest real grid "
                              "cell (idf_utils.py) -- additive context alongside the Open-Meteo climatology tier "
                              "above, not a replacement for it (R95p/R99p are real seasonal AGGREGATE totals, "
                              "not single-day thresholds, so they aren't directly substitutable for the tier's "
                              "own peak-24h-vs-percentile comparison).",
                }
        except Exception as e:
            print(f"[pluvial] real IDF reference lookup failed: {e}")

    has_radar = radar_tier_available(lat, lon)
    data_ceiling = (
        "This location falls within NOAA MRMS's real radar coverage footprint (~1km/2min), "
        "but this app does not yet ingest MRMS -- currently still running on the same "
        "~10km/hourly satellite-blended precipitation used everywhere else (see module docstring)."
        if has_radar else
        "No ground radar network exists at this location (true of ~all of the world outside a "
        "few wealthy/mid-latitude countries, per the research brief) -- resolution is structurally "
        "capped at ~10km/hourly satellite-blended precipitation. Treat this tier as a genuine but "
        "coarse early-warning signal, not street-level nowcasting."
    )

    return {
        "risk_tier": tier,
        "peak_24h_forecast_mm": peak_24h_mm,
        "peak_hourly_forecast_mm": peak_hour_mm,
        "climatology": climatology,
        "note": note,
        "radar_tier_available_here": has_radar,
        "data_ceiling": data_ceiling,
        "real_idf_reference": real_idf_reference,
    }
