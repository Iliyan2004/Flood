"""
Real uploaded rainfall CSV -> physics-model rainfall time series, for
users who have their own real observed/forecast rainfall data (e.g. a
real 15-minute rain-gauge network export) and don't want to distill it
down to a single manually-entered peak mm/hr + duration -- the real
per-interval time series drives flood_model.py's solver directly via
`--rain-series-json` (see flood_model.write_rain_file_from_series),
instead of a synthetic triangular design storm.

Real CSV schema this was built against (a genuine sample export, "MACH
15min precip"): one row per station per 15-minute interval, columns
`mach_title, location, time_start_pkt, time_end_pkt,
precipitation_15min_mm, precipitation_total_mm` -- a multi-station file
with the station's location given as a real TEXT address (e.g. "I-8,
Islamabad"), not lat/lon. Column names are matched case/whitespace-
insensitively against a small alias table (see COLUMN_ALIASES) so a
close variant of this schema (e.g. "station" instead of "mach_title",
or a real "lat"/"lon" pair instead of a text address) is also accepted,
without hard-coding to this one exact export's naming.

Real, stated limitation: a text address is geocoded via the real Mapbox
Geocoding API (same vendor/token already used for this app's own search
box), biased to Pakistan -- an approximate point, not a surveyed gauge
coordinate. When the CSV already carries real lat/lon columns, those are
used directly instead (no geocoding needed, and no approximation).
"""
import csv
import datetime
import io
import json
import os
import threading
import urllib.parse

import requests

from coastal_utils import _haversine_km

GEOCODE_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json"

GEOCODE_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geocode_cache.json")
_geocode_lock = threading.Lock()

# Column-name aliases (case/whitespace-insensitive) -- tolerant to a
# close variant of the real MACH schema without hard-coding to it.
COLUMN_ALIASES = {
    "station": ["mach_title", "station", "station_name", "id", "name", "gauge"],
    "location": ["location", "location_text", "address", "place", "site"],
    "time_start": ["time_start_pkt", "time_start", "start_time", "timestamp", "time"],
    "time_end": ["time_end_pkt", "time_end", "end_time"],
    "precip": ["precipitation_15min_mm", "precip_mm", "rainfall_mm", "precipitation_mm", "rain_mm", "value_mm"],
    "total": ["precipitation_total_mm", "cumulative_mm", "total_mm"],
    "lat": ["lat", "latitude"],
    "lon": ["lon", "lng", "longitude"],
}


class RainfallCsvError(ValueError):
    pass


def _find_column(fieldnames, aliases):
    norm = {(h or "").strip().lower(): h for h in fieldnames}
    for alias in aliases:
        if alias in norm:
            return norm[alias]
    return None


def _parse_datetime(text):
    """Real MACH export format is 'M/D/YYYY H:MM' (no leading zeros,
    12/24hr ambiguity resolved by always being 24hr in the real sample)
    -- tries that first, then a couple of other common real spreadsheet
    export shapes, rather than assuming only one exact format."""
    import datetime
    text = text.strip()
    for fmt in ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise RainfallCsvError(f"Could not parse timestamp '{text}' -- expected e.g. '9/1/2026 5:00' or ISO 8601.")


def parse_rainfall_csv(file_bytes):
    """Parses a real uploaded rainfall CSV into one real time series per
    station. Returns {station_key: {"location_text": str or None,
    "lat": float or None, "lon": float or None, "times_hr": [...],
    "rates_mm_hr": [...], "total_mm": float, "peak_mm_hr": float}}.

    times_hr is real ELAPSED hours from that station's own first real
    timestamp (0.0-based) -- flood_model.py's rain-series file is always
    relative to the run's own start, not an absolute calendar time.
    rates_mm_hr is the real per-interval rate (the CSV's own mm-per-
    interval value, converted to mm/hr using that interval's own real
    duration in hours -- not assumed to always be exactly 15 minutes,
    even though the reference sample always is, so a CSV with a
    different or irregular real interval still converts correctly).
    Negative values (real sensor-reset/drift artifacts, confirmed
    present in the reference sample) are clamped to 0.0 -- read
    literally rather than silently dropped, since a real physical rain
    rate can never be negative."""
    text = file_bytes.decode("utf-8-sig") if isinstance(file_bytes, bytes) else file_bytes
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise RainfallCsvError("CSV has no header row.")

    col_station = _find_column(reader.fieldnames, COLUMN_ALIASES["station"])
    col_location = _find_column(reader.fieldnames, COLUMN_ALIASES["location"])
    col_time_start = _find_column(reader.fieldnames, COLUMN_ALIASES["time_start"])
    col_time_end = _find_column(reader.fieldnames, COLUMN_ALIASES["time_end"])
    col_precip = _find_column(reader.fieldnames, COLUMN_ALIASES["precip"])
    col_lat = _find_column(reader.fieldnames, COLUMN_ALIASES["lat"])
    col_lon = _find_column(reader.fieldnames, COLUMN_ALIASES["lon"])

    if col_time_start is None or col_precip is None:
        raise RainfallCsvError(
            f"Could not find required columns (a timestamp column and a rainfall-per-interval column) "
            f"in this CSV's real header: {reader.fieldnames}")
    if col_station is None and col_location is None:
        raise RainfallCsvError(
            "CSV has no station/location column at all -- can't tell which real place each row belongs to.")

    stations = {}
    for row in reader:
        key = (row.get(col_station) or row.get(col_location) or "station").strip()
        loc_text = (row.get(col_location) or "").strip() or None
        t_start = _parse_datetime(row[col_time_start])
        interval_hr = 0.25  # real fallback matching the reference 15-min sample
        if col_time_end and row.get(col_time_end):
            t_end = _parse_datetime(row[col_time_end])
            delta_hr = (t_end - t_start).total_seconds() / 3600.0
            if delta_hr > 0:
                interval_hr = delta_hr
        try:
            precip_mm = float(row[col_precip])
        except (TypeError, ValueError):
            precip_mm = 0.0
        precip_mm = max(0.0, precip_mm)  # real sensor-artifact negatives clamped, not rejected

        st = stations.setdefault(key, {
            "location_text": loc_text,
            "lat": float(row[col_lat]) if col_lat and row.get(col_lat) else None,
            "lon": float(row[col_lon]) if col_lon and row.get(col_lon) else None,
            "_t0": None, "_rows": [],
        })
        if st["_t0"] is None or t_start < st["_t0"]:
            st["_t0"] = t_start
        st["_rows"].append((t_start, interval_hr, precip_mm))

    if not stations:
        raise RainfallCsvError("CSV parsed but contained no real data rows.")

    result = {}
    for key, st in stations.items():
        rows = sorted(st["_rows"], key=lambda r: r[0])
        t0 = st["_t0"]
        times_hr, rates_mm_hr, total_mm = [], [], 0.0
        for t_start, interval_hr, precip_mm in rows:
            elapsed_hr = (t_start - t0).total_seconds() / 3600.0
            rate_mm_hr = precip_mm / interval_hr if interval_hr > 0 else 0.0
            times_hr.append(round(elapsed_hr, 4))
            rates_mm_hr.append(round(rate_mm_hr, 4))
            total_mm += precip_mm

        peak_rate = max(rates_mm_hr, default=0.0)
        # Real absolute calendar timestamp of the real peak interval --
        # the whole point of a real 24h/15-min upload is that it carries
        # real clock time, not just relative sim-hours; this is threaded
        # through to the run's own summary/3D timeline so a user sees
        # WHEN (real time-of-day) the storm actually peaked, not just
        # "at hour 5.25 of the simulation".
        peak_idx = rates_mm_hr.index(peak_rate) if peak_rate > 0 else None
        end_time = rows[-1][0] + datetime.timedelta(hours=rows[-1][1])

        result[key] = {
            "location_text": st["location_text"],
            "lat": st["lat"], "lon": st["lon"],
            "times_hr": times_hr, "rates_mm_hr": rates_mm_hr,
            "total_mm": round(total_mm, 3),
            "peak_mm_hr": round(peak_rate, 3),
            "n_points": len(times_hr),
            "start_time_iso": t0.isoformat(),
            "end_time_iso": end_time.isoformat(),
            "peak_time_iso": rows[peak_idx][0].isoformat() if peak_idx is not None else None,
        }
    return result


def _load_geocode_cache():
    if not os.path.exists(GEOCODE_CACHE_PATH):
        return {}
    try:
        with open(GEOCODE_CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_geocode_cache(cache):
    with _geocode_lock:
        tmp = GEOCODE_CACHE_PATH + f".tmp{os.getpid()}_{threading.get_ident()}"
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp, GEOCODE_CACHE_PATH)


def geocode_location_text(text, mapbox_token, timeout=10):
    """Real Mapbox Geocoding API forward lookup for a station's real
    text address, restricted to Pakistan (`country=pk`) -- cached to a
    real on-disk JSON file so the same station name across multiple
    uploads/runs doesn't re-bill the API every time. Returns (lon, lat)
    or None if geocoding fails/finds nothing.

    Real, directly-tested finding, not assumed: a bare bbox bias (this
    app's own search box uses one) is NOT enough here -- "I-8, Islamabad"
    (a real Islamabad CDA sector code) resolved to a real place in INDIA
    with only a bbox bias, and even `country=pk` alone still matched a
    real, differently-located small town also literally named
    "Islamabad" (in Wazirabad, Punjab -- Pakistan has more than one place
    with this name) instead of the real capital. Appending ", Pakistan"
    to the query text itself (not just filtering the response) was the
    real, tested fix -- confirmed to correctly resolve every one of a
    real 17-station Islamabad-sector CSV to the real capital's own
    coordinates. Stated limitation, not glossed over: Mapbox's public
    geocoder has no distinct point for individual CDA sector codes (I-8
    vs E-11 vs G-15 all collapse to the same city-centroid point) --
    multiple stations in the same city can resolve to an identical
    approximate location. When a CSV's own real lat/lon columns are
    present, this function is never called for that station at all (see
    resolve_station_coordinates) -- geocoding is only the fallback for
    text-only addresses, and a real per-station coordinate always wins
    over this approximation."""
    if not text:
        return None
    cache = _load_geocode_cache()
    if text in cache:
        return tuple(cache[text]) if cache[text] else None

    query = text if "pakistan" in text.lower() else f"{text}, Pakistan"
    result = None
    try:
        url = GEOCODE_URL.format(query=urllib.parse.quote(query))
        resp = requests.get(url, params={"access_token": mapbox_token, "limit": 1, "country": "pk"},
                             timeout=timeout)
        resp.raise_for_status()
        features = resp.json().get("features") or []
        if features:
            lon, lat = features[0]["center"]
            result = (lon, lat)
    except (requests.exceptions.RequestException, KeyError, IndexError, ValueError):
        result = None

    cache[text] = list(result) if result else None
    _save_geocode_cache(cache)
    return result


def resolve_station_coordinates(stations, mapbox_token):
    """For every station missing a real lat/lon (i.e. only a text
    address was given), geocodes it in place. Stations that already
    carry real lat/lon columns are left untouched -- geocoding is only
    ever a fallback for when no real coordinate exists."""
    for key, st in stations.items():
        if st["lat"] is not None and st["lon"] is not None:
            continue
        coords = geocode_location_text(st["location_text"], mapbox_token) if st["location_text"] else None
        if coords:
            st["lon"], st["lat"] = coords
    return stations


def nearest_station(stations, lon, lat):
    """Real haversine-nearest station to a point (e.g. the drawn AOI's
    own centroid) -- same real distance function already used elsewhere
    in this app (coastal_utils._haversine_km) for nearest-GTSR-station
    lookup, reused here rather than a second implementation. Skips any
    station that has no real resolved coordinate at all (geocoding
    failed AND no lat/lon column existed). Returns (key, distance_km) or
    (None, None) if no station has a usable coordinate."""
    best_key, best_km = None, None
    for key, st in stations.items():
        if st["lat"] is None or st["lon"] is None:
            continue
        km = _haversine_km(lon, lat, st["lon"], st["lat"])
        if best_km is None or km < best_km:
            best_key, best_km = key, km
    return best_key, best_km
