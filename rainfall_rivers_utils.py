"""
Real rainfall (Open-Meteo) for an AOI's centroid.

River/stream/lake context (OSM waterways, channel-corridor rasterization,
flow-accumulation proxy) was removed at the user's request -- see
CLAUDE.md for the removal note. This module now covers rainfall AND (for
the fluvial/river-inflow feature) two real, separate things: real river
discharge data (Open-Meteo's Flood API, itself real GloFAS reanalysis/
forecast data -- same free/no-key vendor and pattern already used for
rainfall) and real river GEOMETRY (OSM waterways) purely to site WHERE a
river crosses the drawn AOI and which domain edge it should enter from --
not to display a live flow/context panel, which is exactly what was
removed before and is not being reintroduced here.
"""
import time

import requests

from overpass_utils import query_overpass

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"


def fetch_rainfall(lat, lon):
    """Real forecast + recent-past precipitation from Open-Meteo (free, no key)."""
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "precipitation",
        "past_days": 3, "forecast_days": 3,
        "timezone": "auto",
    }
    t0 = time.time()
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    print(f"[rainfall] Open-Meteo returned {len(times)} hourly points in {time.time()-t0:.1f}s")
    return {
        "source": "Open-Meteo (real forecast + recent observed)",
        "timezone": data.get("timezone"),
        "hourly_time": times,
        "hourly_precipitation_mm": precip,
        "total_past_3days_mm": float(sum(p for t, p in zip(times, precip) if t < data.get("current", {}).get("time", times[len(times)//2]))) if precip else 0.0,
    }


def fetch_river_discharge(lat, lon, past_days=30, forecast_days=7):
    """Real river discharge (m^3/s) for a point, from Open-Meteo's Flood
    API -- itself real GloFAS (Copernicus/ECMWF Global Flood Awareness
    System) reanalysis + forecast data, not a live physical gauge (no
    public real-time gauge API exists for arbitrary Pakistani streams,
    per this app's own earlier finding) but a real, physically-based
    global hydrological model, at the same level of "real" this app
    already accepts for rainfall (Open-Meteo's own forecast is likewise a
    real modeled product, not a rain gauge). 5km grid -- the API's own
    documented caveat is that the closest river might not be selected
    correctly at that resolution, so this is context to help a user pick
    a sensible design peak discharge, not a value that silently drives
    the simulation on its own."""
    params = {"latitude": lat, "longitude": lon, "daily": "river_discharge",
              "past_days": past_days, "forecast_days": forecast_days}
    t0 = time.time()
    resp = requests.get(OPEN_METEO_FLOOD_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    daily = data.get("daily", {})
    times = daily.get("time", [])
    values = [v for v in daily.get("river_discharge", []) if v is not None]
    print(f"[river] Open-Meteo Flood API returned {len(times)} daily points in {time.time()-t0:.1f}s")
    return {
        "source": "Open-Meteo Flood API (real GloFAS reanalysis + forecast, not a live gauge)",
        "daily_time": times,
        "daily_river_discharge_m3s": daily.get("river_discharge", []),
        "recent_m3s": values[-1] if values else None,
        "mean_m3s": (sum(values) / len(values)) if values else None,
        "max_m3s": max(values) if values else None,
        "min_m3s": min(values) if values else None,
    }


def fetch_waterway_crossing(lon_min, lat_min, lon_max, lat_max, timeout=30):
    """Real OSM waterway geometry (way["waterway"]), used ONLY to site
    where a real river/stream/canal crosses the drawn AOI's bounding box
    and which domain edge it's closest to -- not for a live-flow display
    (that whole feature was removed earlier at the user's request, see
    CLAUDE.md). Prefers `river` over `stream`/`canal` (a real river is far
    more likely to carry flood-relevant discharge), and among candidates
    of the same tag, the one with the most vertices inside the bbox (a
    real, if simple, proxy for "most of this waterway is actually in our
    AOI", not just clipping through a corner)."""
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f"""
    [out:json][timeout:{timeout}];
    (
      way["waterway"~"^(river|stream|canal)$"]({bbox});
    );
    out geom;
    """
    elements = query_overpass(query, timeout=timeout)

    priority = {"river": 0, "stream": 1, "canal": 2}
    best = None
    for el in elements:
        tags = el.get("tags", {})
        wtype = tags.get("waterway")
        if wtype not in priority or "geometry" not in el:
            continue
        pts = [(g["lon"], g["lat"]) for g in el["geometry"]]
        inside = [(lon, lat) for lon, lat in pts if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max]
        if not inside:
            continue
        key = (priority[wtype], -len(inside))
        if best is None or key < best[0]:
            best = (key, wtype, tags.get("name"), pts, inside)

    if best is None:
        return None
    _, wtype, name, pts, inside = best
    lon_c = sum(p[0] for p in inside) / len(inside)
    lat_c = sum(p[1] for p in inside) / len(inside)

    # Nearest domain edge -- whichever of the 4 bbox sides this crossing
    # point sits closest to, in real distance terms (normalized by that
    # axis's own span so a wide-but-short AOI doesn't bias the answer).
    lon_span = max(lon_max - lon_min, 1e-9)
    lat_span = max(lat_max - lat_min, 1e-9)
    dists = {
        "W": (lon_c - lon_min) / lon_span, "E": (lon_max - lon_c) / lon_span,
        "S": (lat_c - lat_min) / lat_span, "N": (lat_max - lat_c) / lat_span,
    }
    nearest_edge = min(dists, key=dists.get)

    return {"waterway_type": wtype, "name": name or f"unnamed {wtype}",
            "lon": lon_c, "lat": lat_c, "nearest_edge": nearest_edge}
