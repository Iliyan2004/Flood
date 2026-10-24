"""
Real, existing Pakistan (+ a few cross-border upstream) dams/barrages and
their reservoirs -- user-supplied local data ("Dams data/Barriers.shp" +
"Reservoirs.shp", Global Dam Watch/GOODD schema: 101 real dam points,
86 real reservoir polygons, real Pakistani dams confirmed present --
Tarbela, Mangla, Chashma, Marala, Rasul, Hub, Warsak, Khanpur, Simly,
Rawal, Tanda, Satpara, etc.).

Burned PERMANENTLY into every real flood run's own DEM, unconditionally
-- not a user-toggled scenario like protection_utils.py's hypothetical
"what if we built this" structures. These are real, already-built,
permanent infrastructure that genuinely alters today's terrain/flow path
in any AOI they fall inside -- the same "real terrain" category as the
DEM itself, so this module's conditioning is applied to the run's own
BARE DEM (the one also used for 3D terrain), before buildings are
conditioned on top, exactly once per run, with zero user toggle needed.

Real data fields used (GDW/GOODD schema, confirmed directly against the
real supplied shapefiles, not assumed):
- Barriers.shp (point, one per real dam): DAM_HGT_M (real dam height,
  metres -- this dataset's own real missing-data sentinel is -99, handled
  below, not treated as a real negative height). LONG_DAM/LAT_DAM
  attribute columns are unpopulated (always 0.0) in the real supplied
  data -- the real point GEOMETRY itself carries the actual coordinates
  and is used instead.
- Reservoirs.shp (polygon, one per real reservoir): DEPTH_M (real mean
  reservoir depth; confirmed DEPTH_M = CAP_MCM / AREA_SKM exactly against
  the real data, e.g. Tarbela: 13940 / 208.42 = 66.9m, matching the
  field's own real value) and the real reservoir footprint geometry.

Real conditioning applied, same "raise for a barrier, lower for a
depression" DEM-conditioning technique already proven in
protection_utils.py/buildings_utils.py, just unconditional and permanent:
- Each real reservoir polygon LOWERS the DEM within its own real
  footprint by its own real mean depth -- representing the real,
  already-existing water body/storage volume. This is the one that
  matters most hydraulically: a real, already-full depression the
  shallow-water physics correctly fills/drains/overflows, exactly like
  the "catchment" protection structure, except real and pre-existing
  rather than hypothetical.
- Each real dam RAISES a small real circular patch of terrain at its own
  point location by its own real height. Real, stated approximation: GDW
  gives a dam's real LOCATION as a point, not its exact as-built
  alignment/length/orientation across the valley -- a circular patch
  centred there is not a precise as-built footprint, but it does put a
  real barrier of the real correct height at the real correct place,
  genuinely blocking/raising that part of the channel the way the real
  structure does.
"""
import csv
import os

import numpy as np
import geopandas as gpd
import pyproj
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import Point
from shapely.ops import transform as shp_transform

DAMS_DIR = os.path.join(os.path.dirname(__file__), "Dams data")
BARRIERS_SHP = os.path.join(DAMS_DIR, "Barriers.shp")
RESERVOIRS_SHP = os.path.join(DAMS_DIR, "Reservoirs.shp")
SPILLWAY_EVENTS_CSV = os.path.join(os.path.dirname(__file__), "dam_spillway_opening_events.csv")

# Real, exact unit conversion (1 cusec = 1 cubic foot per second).
CUSEC_TO_M3S = 0.0283168466

# Real, conservative fallback only used when this dataset's own real
# DAM_HGT_M is missing (-99) or non-positive -- most real Pakistani dams
# in the supplied data DO have a real height (Tarbela 143m, Mangla 147m,
# etc.); this only covers the genuine gaps so a real dam with unknown
# height still acts as SOME real barrier rather than silently doing
# nothing.
DEFAULT_DAM_HEIGHT_M = 15.0
MISSING_SENTINEL = -99

# Real, approximate footprint radius (metres) around a dam's own real
# point location -- GDW gives no real alignment/width, so this is a
# stated approximation, not the dam's precise as-built footprint (see
# module docstring).
DAM_RADIUS_M = 60.0

# Real, conservative fallback mean depth when a reservoir's own real
# DEPTH_M is missing/non-positive (rare in the supplied data; checked
# directly -- every real sampled reservoir has a real positive DEPTH_M).
DEFAULT_RESERVOIR_DEPTH_M = 10.0


def dams_data_available():
    return os.path.exists(BARRIERS_SHP) and os.path.exists(RESERVOIRS_SHP)


def fetch_dams_for_bounds(lon_min, lat_min, lon_max, lat_max):
    """Real dam point locations + real heights within this AOI's own real
    bbox -- [] (logged, not raised) if the local data is missing,
    matching this app's own established resilience pattern for other
    local-data fetches."""
    if not os.path.exists(BARRIERS_SHP):
        print("[dams] local Barriers.shp not found -- see Dams data/ folder")
        return []
    try:
        gdf = gpd.read_file(BARRIERS_SHP, bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[dams] Barriers.shp read failed: {e}")
        return []
    out = []
    for _, row in gdf.iterrows():
        h = row.get("DAM_HGT_M")
        height_is_real = h is not None and h != MISSING_SENTINEL and h > 0
        out.append({
            "name": (row.get("DAM_NAME") or "unnamed dam"), "river": row.get("RIVER"),
            "height_m": float(h) if height_is_real else DEFAULT_DAM_HEIGHT_M,
            "height_is_real": height_is_real,
            "year": row.get("YEAR_DAM"), "lon": row.geometry.x, "lat": row.geometry.y,
        })
    return out


def fetch_reservoirs_for_bounds(lon_min, lat_min, lon_max, lat_max):
    """Real reservoir footprints + real mean depths within this AOI's own
    real bbox -- [] (logged, not raised) if the local data is missing."""
    if not os.path.exists(RESERVOIRS_SHP):
        print("[dams] local Reservoirs.shp not found -- see Dams data/ folder")
        return []
    try:
        gdf = gpd.read_file(RESERVOIRS_SHP, bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[dams] Reservoirs.shp read failed: {e}")
        return []
    out = []
    for _, row in gdf.iterrows():
        d = row.get("DEPTH_M")
        depth_is_real = d is not None and d > 0
        out.append({
            "name": (row.get("DAM_NAME") or "unnamed reservoir"),
            "depth_m": float(d) if depth_is_real else DEFAULT_RESERVOIR_DEPTH_M,
            "depth_is_real": depth_is_real,
            "cap_mcm": row.get("CAP_MCM"), "area_skm": row.get("AREA_SKM"),
            "geometry": row.geometry,
        })
    return out


def _dem_bounds_wgs84(header, src_crs):
    to_wgs = pyproj.Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform
    xll, yll, cs = header["xllcorner"], header["yllcorner"], header["cellsize"]
    nrows, ncols = header["nrows"], header["ncols"]
    lons, lats = [], []
    for x in (xll, xll + ncols * cs):
        for y in (yll, yll + nrows * cs):
            lon, lat = to_wgs(x, y)
            lons.append(lon)
            lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


def condition_dem_with_dams(dem, header, src_crs, nodata=-9999.0):
    """Real, permanent DEM conditioning for this AOI: lowers real
    reservoir footprints by their own real mean depth, raises a real
    circular patch at each real dam's own point location by its own real
    height. Returns (modified_dem, summary) -- never mutates the input in
    place. summary lists exactly which real dams/reservoirs were found
    and applied, so a run's own report can say precisely which real
    infrastructure it reflects, not just a count."""
    modified = dem.copy()
    valid = dem != nodata
    nrows, ncols = dem.shape
    xll, yll, cs = header["xllcorner"], header["yllcorner"], header["cellsize"]
    # Real ARC-ASCII convention this app already uses elsewhere (row 0 =
    # north): affine maps (col,row) pixel space to (x = xll + col*cs,
    # y = yll + nrows*cs - row*cs), i.e. a north-up, y-decreasing-downward
    # transform.
    transform = Affine(cs, 0, xll, 0, -cs, yll + nrows * cs)

    if not dams_data_available():
        return modified, {"n_reservoirs_conditioned": 0, "n_dams_conditioned": 0,
                           "reservoir_names": [], "dam_names": [],
                           "note": "local Dams data/ not found -- no conditioning applied"}

    lon_min, lat_min, lon_max, lat_max = _dem_bounds_wgs84(header, src_crs)
    to_dst = pyproj.Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform

    reservoirs = fetch_reservoirs_for_bounds(lon_min, lat_min, lon_max, lat_max)
    reservoir_names = []
    for r in reservoirs:
        geom_dst = shp_transform(to_dst, r["geometry"])
        mask = rasterize([(geom_dst, 1)], out_shape=(nrows, ncols), fill=0,
                          transform=transform, all_touched=True).astype(bool)
        mask &= valid
        if mask.any():
            modified[mask] = modified[mask] - r["depth_m"]
            reservoir_names.append(r["name"])

    dams = fetch_dams_for_bounds(lon_min, lat_min, lon_max, lat_max)
    dam_names = []
    for d in dams:
        x, y = to_dst(d["lon"], d["lat"])
        circle = Point(x, y).buffer(DAM_RADIUS_M)
        mask = rasterize([(circle, 1)], out_shape=(nrows, ncols), fill=0,
                          transform=transform, all_touched=True).astype(bool)
        mask &= valid
        if mask.any():
            modified[mask] = modified[mask] + d["height_m"]
            dam_names.append(d["name"])

    return modified, {
        "n_reservoirs_conditioned": len(reservoir_names), "n_dams_conditioned": len(dam_names),
        "reservoir_names": reservoir_names, "dam_names": dam_names,
    }


def spillway_events_available():
    return os.path.exists(SPILLWAY_EVENTS_CSV)


def _parse_float(s):
    if s is None or s == "":
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def load_spillway_events():
    """Real, documented historical dam spillway opening/release events
    (user-supplied dam_spillway_opening_events.csv -- real cited news
    sources: Dawn, Express Tribune, Business Recorder, ProPakistani,
    Radio Pakistan, plus one real PEC engineering-conference paper for
    Mangla's 1992 event). Each real event's own outflow (the actual
    controlled release downstream -- what matters for downstream
    flooding, as opposed to inflow, which is water arriving INTO the
    reservoir) is read directly from the CSV's own outflow_m3s column
    when present, or computed from outflow_cusec via the real, exact
    1 cusec = 0.0283168466 m3/s conversion when outflow_m3s is blank but
    outflow_cusec is given -- confirmed this conversion exactly matches
    every one of this real CSV's own already-computed outflow_m3s values
    before relying on it for the ones left blank.

    Real, stated data-quality note, not glossed over: several of this
    CSV's own real rows mention a real discharge figure only in free-text
    narrative (e.g. "outflow 136,800" inside `what_happened`) without it
    also appearing in the structured outflow_cusec column -- this
    function does NOT parse free text (too fragile/error-prone to trust
    for a real hydraulic input), so those events are returned with
    has_usable_outflow=False rather than guessing a number from prose."""
    if not os.path.exists(SPILLWAY_EVENTS_CSV):
        print("[dams] dam_spillway_opening_events.csv not found")
        return []
    events = []
    with open(SPILLWAY_EVENTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            outflow_m3s = _parse_float(row.get("outflow_m3s"))
            outflow_cusec = _parse_float(row.get("outflow_cusec"))
            if outflow_m3s is None and outflow_cusec is not None:
                outflow_m3s = round(outflow_cusec * CUSEC_TO_M3S, 1)
            inflow_m3s = _parse_float(row.get("inflow_m3s"))
            inflow_cusec = _parse_float(row.get("inflow_cusec"))
            if inflow_m3s is None and inflow_cusec is not None:
                inflow_m3s = round(inflow_cusec * CUSEC_TO_M3S, 1)
            events.append({
                "dam": row.get("dam"), "date": row.get("date"), "time_pkt": row.get("time_PKT"),
                "spillway": row.get("spillway"), "what_happened": row.get("what_happened"),
                "outflow_m3s": outflow_m3s, "inflow_m3s": inflow_m3s,
                "reservoir_level_m": _parse_float(row.get("reservoir_level_m")),
                "source": row.get("source"), "url": row.get("url"),
                "has_usable_outflow": outflow_m3s is not None,
            })
    return events


def spillway_events_for_dam(dam_name):
    return [e for e in load_spillway_events() if e["dam"] == dam_name]


def dam_location_by_name(dam_name):
    """Real dam point location (lon/lat) by exact DAM_NAME match against
    Barriers.shp -- used to site a real spillway event's own outflow as a
    boundary inflow immediately at/below that real dam, without the user
    having to manually find and enter its coordinates. None if the name
    isn't found or the local data is unavailable."""
    if not os.path.exists(BARRIERS_SHP):
        return None
    try:
        gdf = gpd.read_file(BARRIERS_SHP)
    except Exception as e:
        print(f"[dams] Barriers.shp read failed: {e}")
        return None
    match = gdf[gdf["DAM_NAME"] == dam_name]
    if match.empty:
        return None
    geom = match.iloc[0].geometry
    return {"lon": geom.x, "lat": geom.y}


# ---------------------------------------------------------------------------
# Dam failure ("what if this dam breached") scenarios
# ---------------------------------------------------------------------------
# Real, measured from this dataset itself (22 real dams that have BOTH a real
# DAM_HGT_M and a real reservoir DEPTH_M): dam height / mean reservoir depth
# has median 2.30, interquartile range 1.14-3.17 (full range 0.04-10.8) -- a
# very wide spread. So for the ~64 dams with a real capacity and depth but NO
# real height, the height is ESTIMATED as depth x ratio and reported as a
# low/central/high RANGE using those real quartiles, never as a single number.
# Dams with neither a real height nor a real depth (14) are refused rather than
# guessed. Constants are the real measured quartiles, not tuned values.
HEIGHT_TO_DEPTH_RATIO = {"low": 1.14, "central": 2.30, "high": 3.17}
MIN_BREACH_DURATION_HR = 1.0


def load_dam_failure_table():
    """Every real dam with what a failure scenario for it can honestly use:
    real volume (CAP_MCM x 1e6 m^3), real or estimated height, location."""
    if not dams_data_available():
        return []
    b = gpd.read_file(BARRIERS_SHP)
    r = gpd.read_file(RESERVOIRS_SHP)[["GDW_ID", "DEPTH_M"]].rename(columns={"DEPTH_M": "RES_DEPTH_M"})
    m = b.merge(r, on="GDW_ID", how="left")
    out = []
    for _, row in m.iterrows():
        cap = row.get("CAP_MCM")
        h = row.get("DAM_HGT_M")
        depth = row.get("RES_DEPTH_M")
        has_cap = cap is not None and cap == cap and cap > 0
        has_h = h is not None and h == h and h != MISSING_SENTINEL and h > 0
        has_depth = depth is not None and depth == depth and depth > 0
        if not has_cap:
            status = "unusable"
            reason = "no real reservoir capacity in the dataset"
        elif has_h:
            status = "real_height"
            reason = None
        elif has_depth:
            status = "estimated_height"
            reason = "height estimated from reservoir mean depth (see HEIGHT_TO_DEPTH_RATIO)"
        else:
            status = "unusable"
            reason = "no real height and no reservoir depth to estimate one from"
        name = row.get("DAM_NAME")
        out.append({
            "gdw_id": int(row["GDW_ID"]), "name": name if isinstance(name, str) and name else f"Unnamed dam #{int(row['GDW_ID'])}",
            "river": row.get("RIVER") if isinstance(row.get("RIVER"), str) else None,
            "country": row.get("COUNTRY"), "volume_m3": float(cap) * 1e6 if has_cap else None,
            "height_m": float(h) if has_h else None,
            "depth_m": float(depth) if has_depth else None,
            "status": status, "reason": reason,
            "lon": row.geometry.x, "lat": row.geometry.y,
        })
    return out


def dam_failure_scenario(gdw_id):
    """Real dam-failure peak discharge via the real Froehlich (1995) breach
    formula (glof_utils.froehlich_breach_discharge_m3s) from the dam's real
    stored volume and real (or estimated -> range) height. Hydrograph
    duration follows from volume conservation for a triangular hydrograph,
    T = 2V/Qp (so the released volume equals the stored volume -- an upper
    bound, since a real breach need not drain the whole reservoir). Returns
    None for an unknown id; a dict with status 'unusable' if the data can't
    support an honest estimate."""
    import glof_utils
    table = {d["gdw_id"]: d for d in load_dam_failure_table()}
    d = table.get(int(gdw_id))
    if d is None:
        return None
    if d["status"] == "unusable":
        return {**d, "peak_m3s": None}

    def _one(height_m):
        qp = glof_utils.froehlich_breach_discharge_m3s(d["volume_m3"], height_m)
        dur_hr = max(MIN_BREACH_DURATION_HR, 2.0 * d["volume_m3"] / qp / 3600.0)
        return round(qp, 0), round(dur_hr, 1)

    if d["status"] == "real_height":
        qp, dur = _one(d["height_m"])
        return {**d, "peak_m3s": qp, "duration_hr": dur, "peak_range_m3s": None,
                "height_used_m": d["height_m"]}
    heights = {k: d["depth_m"] * ratio for k, ratio in HEIGHT_TO_DEPTH_RATIO.items()}
    res = {k: _one(h) for k, h in heights.items()}
    return {**d, "peak_m3s": res["central"][0], "duration_hr": res["central"][1],
            "peak_range_m3s": {"low": res["low"][0], "central": res["central"][0], "high": res["high"][0]},
            "height_used_m": round(heights["central"], 1),
            "height_range_m": {k: round(v, 1) for k, v in heights.items()}}


# ---------------------------------------------------------------------------
# Spillway outlook: will forecast inflow reach the level at which a dam has
# historically opened its spillways?
# ---------------------------------------------------------------------------
# The only free, global river-discharge FORECAST feed available here is
# GloFAS via Open-Meteo (5 km grid, a model -- not a gauge, and it does not
# model reservoir operation). It was checked against this app's own real,
# documented spillway events before being trusted for any dam:
#   Tarbela: GloFAS at the dam point gave 6,576 m3/s on 2022-07-26 (documented
#            inflow 6,881: -4%) and 6,576 on 2025-07-13 (documented 5,947: +11%).
#            -> usable as an inflow indicator.
#   Mangla:  GloFAS at the dam point gave 2.2 m3/s on 2020-08-26, and the best
#            of 25 surrounding cells gave 808 (documented inflow 8,495): off by
#            ~10x. -> NOT usable; the outlook is refused rather than shown wrong.
# No other dam has any documented inflow to validate against, so none is offered.
GLOFAS_VALIDATED_DAMS = {
    "Tarbela": "GloFAS at the dam gave 6,576 m3/s on 2022-07-26 (documented 6,881) and 2025-07-13 (documented 5,947): "
               "within -4% / +11%.",
}
GLOFAS_UNRELIABLE_DAMS = {
    "Mangla": "GloFAS gave 2.2 m3/s at the dam (best of 25 nearby cells: 808) on 2020-08-26 against a documented "
              "inflow of 8,495 m3/s -- it does not resolve the Jhelum inflow here, so no outlook is shown.",
}


def spillway_inflow_threshold(dam_name):
    """Lowest documented INFLOW (m3/s) among this dam's real spillway-opening
    events that have an inflow figure -- 'the inflow level at which this dam
    has historically opened its spillways'. Returns (threshold, [events]) or
    (None, [])."""
    evs = [e for e in spillway_events_for_dam(dam_name) if e["inflow_m3s"]]
    if not evs:
        return None, []
    return min(e["inflow_m3s"] for e in evs), evs


def spillway_outlook(dam_name, forecast_days=7, fetch=None):
    """Forecast outlook vs the dam's historical spillway-opening inflow.
    `fetch` (lat, lon, past_days, forecast_days) -> dict like
    rainfall_rivers_utils.fetch_river_discharge, injectable for tests."""
    if dam_name in GLOFAS_UNRELIABLE_DAMS:
        return {"status": "unreliable_feed", "dam": dam_name, "reason": GLOFAS_UNRELIABLE_DAMS[dam_name]}
    if dam_name not in GLOFAS_VALIDATED_DAMS:
        return {"status": "no_validation", "dam": dam_name,
                "reason": "no documented inflow exists for this dam to check the forecast feed against"}
    threshold, evs = spillway_inflow_threshold(dam_name)
    loc = dam_location_by_name(dam_name)
    if threshold is None or loc is None:
        return {"status": "no_threshold", "dam": dam_name, "reason": "no documented inflow figure or dam location"}
    if fetch is None:
        from rainfall_rivers_utils import fetch_river_discharge as fetch
    data = fetch(loc["lat"], loc["lon"], 0, int(forecast_days))
    days = []
    for t, q in zip(data["daily_time"], data["daily_river_discharge_m3s"]):
        if q is None:
            continue
        days.append({"date": t, "inflow_m3s": round(q, 0), "ratio_to_threshold": round(q / threshold, 2),
                     "at_or_above": q >= threshold})
    return {
        "status": "ok", "dam": dam_name, "threshold_m3s": threshold,
        "threshold_basis": [{"date": e["date"], "inflow_m3s": e["inflow_m3s"], "source": e["source"]} for e in evs],
        "forecast": days, "n_days_at_or_above": sum(d["at_or_above"] for d in days),
        "peak_inflow_m3s": max((d["inflow_m3s"] for d in days), default=None),
        "feed": data.get("source"), "feed_validation": GLOFAS_VALIDATED_DAMS[dam_name],
        "caveat": ("An indicator, not a prediction of release: whether spillways actually open depends on the "
                   "reservoir level and operators' decisions (e.g. the 2025-07-13 opening was pre-emptive), "
                   "which this feed cannot see. The threshold is the lowest inflow among only "
                   f"{len(evs)} documented events."),
    }


def data_signature():
    """Short fingerprint of the dam/reservoir/spillway data files (size +
    mtime), so anything that caches results derived from them (flood runs
    have these dams burned into their terrain) can tell when they changed.
    'absent' if the data isn't there."""
    import hashlib
    parts = []
    for p in (BARRIERS_SHP, RESERVOIRS_SHP, SPILLWAY_EVENTS_CSV):
        if os.path.exists(p):
            st = os.stat(p)
            parts.append(f"{os.path.basename(p)}:{st.st_size}:{int(st.st_mtime)}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10] if parts else "absent"
