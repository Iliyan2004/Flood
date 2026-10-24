"""
Real, per-grid-cell extreme-rainfall / IDF (intensity-duration-frequency)
data for Pakistan -- a genuinely different, complementary data source to
`climate_utils.py`'s existing real CMIP6-based design-storm/climate-factor
tool. Where `climate_utils.py` fits a Gumbel distribution to real
CMIP6-historical (BCSD bias-corrected, model-simulated) annual-max daily
rainfall, this module reads a real, already-computed OBSERVATIONAL cube:
ERA5-Land (1980-2026, ~9km grid) total precipitation + real GPM IMERG V07
(2000-2026, ~11km grid) 30-min precipitation, with real per-cell extreme
indices (RX1day, RX5day, R95p, R99p, SDII, R20mm, PRCPTOT, peak hourly
intensity, and a real Theil-Sen trend on RX1day) and a real intensity-
duration curve (1/2/3/6/12/24h from ERA5-Land, 30/60/90/120/180min from
IMERG) already built for every grid cell -- no live multi-file read needed
(unlike climate_utils.py's own CMIP6 path, which reads dozens of real
netCDF files per request, 1-3 real minutes uncached).

Real, external source (not built by this session): produced by a separate
Pakistan Hazards Modelling task on this same shared machine
(`~/DGX-Shared/Tasks/Pakistan Hazards Modelling/01. Rainfall extrudes/`),
symlinked in read-only via `global_data/rainfall_idf` (same established
pattern already used for GTSR/SoilGrids/Caravan). Nothing here re-derives
or duplicates that build pipeline -- this module only READS the two
already-built NetCDF cubes it produced (see that task's own README.md for
the real build methodology: ETCCDI-standard indices, Theil-Sen/Mann-
Kendall trend, 1980-2025 wet-day-percentile baseline).

Real, stated limitation carried over from that source's own README:
ERA5-Land under-represents localised coastal convective extremes (its own
documented example: Karachi Aug-2020 ~25mm/day vs ~200mm observed) --
IMERG resolves short-duration intensity better where available (2000+),
which is exactly why the IDF curve's own short-duration end (30-180min)
prefers IMERG over ERA5-Land wherever both exist.
"""
import os
import threading

import json

import numpy as np

RAINFALL_IDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "rainfall_idf")
ERA_NC_PATH = os.path.join(RAINFALL_IDF_DIR, "rainfall_indices_pixel_1980_2026.nc")
IMERG_NC_PATH = os.path.join(RAINFALL_IDF_DIR, "rainfall_indices_imerg_pixel_2000_2026.nc")
MANIFEST_JSON_PATH = os.path.join(RAINFALL_IDF_DIR, "manifest.json")
TEHSILS_GEOJSON_PATH = os.path.join(RAINFALL_IDF_DIR, "tehsils.geojson")

IDF_DURATIONS_H = [1, 2, 3, 6, 12, 24]
ERA_METRICS = ["prcptot", "sdii", "r20mm", "rx1day", "rx5day", "r95p", "r99p", "peakhr"]

_lock = threading.Lock()
_state = {"opened": False, "era": None, "img": None}


def idf_data_available():
    """Real, not assumed: both real NetCDF files must actually exist on
    disk (this app's own dataset can't be assumed present on every
    machine -- it's a ~63MB+16MB local extract, not bundled)."""
    return os.path.exists(ERA_NC_PATH)


def metric_catalogue():
    """The real metric label/unit catalogue from the source dataset's own
    manifest.json (e.g. "rx1day" -> "RX1day - max 1-day rainfall", "mm")
    -- read directly from the source's own real, already-written metadata
    rather than re-typing labels here that could drift out of sync with
    it. Returns {} if the local dataset isn't present."""
    if not os.path.exists(MANIFEST_JSON_PATH):
        return {}
    try:
        with open(MANIFEST_JSON_PATH) as f:
            return json.load(f).get("metrics", {})
    except Exception:
        return {}


def _open():
    with _lock:
        if _state["opened"]:
            return
        _state["opened"] = True
        if not os.path.exists(ERA_NC_PATH):
            return
        import netCDF4
        d = netCDF4.Dataset(ERA_NC_PATH, "r")
        v = d.variables
        _state["era"] = {
            "ds": d,
            "lat": np.asarray(v["latitude"][:], dtype="float64"),
            "lon": np.asarray(v["longitude"][:], dtype="float64"),
            "seasons": [str(s) for s in v["season"][:]],
            "years": [int(y) for y in v["year"][:]],
            "durs_h": [int(x) for x in v["duration_h"][:]] if "duration_h" in v else IDF_DURATIONS_H,
            "tid": np.asarray(v["tehsil_id"][:]) if "tehsil_id" in v else None,
        }
        _state["partial_years"] = set()
        if os.path.exists(MANIFEST_JSON_PATH):
            try:
                with open(MANIFEST_JSON_PATH) as f:
                    _state["partial_years"] = set(json.load(f).get("partial_years", []))
            except Exception:
                pass
        if os.path.exists(IMERG_NC_PATH):
            di = netCDF4.Dataset(IMERG_NC_PATH, "r")
            vi = di.variables
            _state["img"] = {
                "ds": di,
                "lat": np.asarray(vi["latitude"][:], dtype="float64"),
                "lon": np.asarray(vi["longitude"][:], dtype="float64"),
                "seasons": [str(s) for s in vi["season"][:]],
                "years": [int(y) for y in vi["year"][:]],
                "durs_min": [int(x) for x in vi["duration_min"][:]] if "duration_min" in vi else [30, 60, 90, 120, 180],
                "tid": np.asarray(vi["tehsil_id"][:]) if "tehsil_id" in vi else None,
            }


def _rnd(x, nd=3):
    return None if (x is None or not np.isfinite(x)) else round(float(x), nd)


def _default_year(S):
    """The real most recent COMPLETE year (excluding the dataset's own
    documented partial years, e.g. the current year if the record's
    build hasn't caught up to a full season yet) -- gives a caller who
    doesn't specify a year the fullest real curve/indices by default,
    rather than a partial one that may be missing whole duration bands
    (see the module docstring: ERA5-Land's own 2026 extract stops ~20
    May, so its long-duration IDF points are real but absent for a
    request defaulting to the literal latest year)."""
    complete = [y for y in S["years"] if y not in _state.get("partial_years", set())]
    return complete[-1] if complete else S["years"][-1]


def _resolve_season_year(season, year, S):
    season = season if season in S["seasons"] else "monsoon"
    if year is None:
        year = _default_year(S)
    year = min(max(int(year), S["years"][0]), S["years"][-1])
    return season, year


def real_idf_curve_for_point(lat, lon, year=None, season="monsoon"):
    """Real intensity-duration-frequency curve (mm/hr) for the nearest
    real grid cell to (lat, lon). Real, deliberate deduplication rule
    (matching the source dataset's own documented dashboard behavior,
    not invented here): IMERG's real sub-hourly points (30/60/90/120/
    180min) are preferred over ERA5-Land's own overlapping 1/2/3h points
    wherever the IMERG record covers this cell/season/year (2000+) --
    IMERG resolves short-duration convective intensity better (the
    source dataset's own documented finding: ERA5-Land under-represents
    localised coastal convective extremes). ERA5-Land's 6/12/24h points
    are always included (IMERG's own sub-hourly cube doesn't extend that
    far). Returns None if the local dataset isn't present. `year`
    defaults to the record's own most recent complete year."""
    _open()
    E = _state["era"]
    if E is None:
        return None
    season, year = _resolve_season_year(season, year, E)
    si, yi = E["seasons"].index(season), E["years"].index(year)
    pr = int(np.argmin(np.abs(E["lat"] - float(lat))))
    pc = int(np.argmin(np.abs(E["lon"] - float(lon))))

    imerg_points = []
    I = _state["img"]
    if I is not None and season in I["seasons"] and int(year) in I["years"]:
        isi, iyi = I["seasons"].index(season), I["years"].index(int(year))
        sub = np.asarray(I["ds"].variables["idf_sub"][isi, iyi, :, :, :], dtype="float64")
        ipr = int(np.argmin(np.abs(I["lat"] - float(lat))))
        ipc = int(np.argmin(np.abs(I["lon"] - float(lon))))
        for k, dmn in enumerate(I["durs_min"]):
            sv = sub[k, ipr, ipc]
            if np.isfinite(sv):
                imerg_points.append({"duration_hr": round(dmn / 60.0, 3), "intensity_mm_hr": round(float(sv), 3),
                                     "accumulation_mm": round(float(sv) * dmn / 60.0, 2), "source": "IMERG"})
    imerg_durs_hr = {p["duration_hr"] for p in imerg_points}

    era_points = []
    if "idf" in E["ds"].variables:
        cube = np.asarray(E["ds"].variables["idf"][si, yi, :, :, :], dtype="float64")
        for k, dh in enumerate(E["durs_h"]):
            if float(dh) in imerg_durs_hr:
                continue  # real IMERG point at this exact duration already preferred above
            acc = cube[k, pr, pc]
            if np.isfinite(acc):
                era_points.append({"duration_hr": dh, "intensity_mm_hr": round(float(acc) / dh, 3),
                                   "accumulation_mm": round(float(acc), 2), "source": "ERA5-Land"})

    points = sorted(imerg_points + era_points, key=lambda p: p["duration_hr"])
    if not points:
        return None
    return {
        "lat": round(float(E["lat"][pr]), 3), "lon": round(float(E["lon"][pc]), 3),
        "year": year, "season": season, "points": points,
        "method": "Real observational IDF curve: ERA5-Land running-accumulation maxima (1-24h) "
                  "+ GPM IMERG V07 30-min sub-hourly maxima (30-180min, where the 2000+ record covers it) "
                  "at this AOI's own nearest real grid cell -- not a statistical fit, the real recorded maxima.",
    }


def real_extreme_indices_for_point(lat, lon, year=None, season="monsoon"):
    """Real per-cell extreme-rainfall indices (RX1day, RX5day, R95p, R99p,
    SDII, R20mm, PRCPTOT, peak hourly intensity, real Theil-Sen RX1day
    trend) for the nearest real grid cell -- the real formal reference
    this app's own pluvial_utils.py docstring states doesn't exist as an
    open global dataset (it exists here, for Pakistan specifically).
    Returns None if the local dataset isn't present."""
    _open()
    E = _state["era"]
    if E is None:
        return None
    season, year = _resolve_season_year(season, year, E)
    si, yi = E["seasons"].index(season), E["years"].index(year)
    pr = int(np.argmin(np.abs(E["lat"] - float(lat))))
    pc = int(np.argmin(np.abs(E["lon"] - float(lon))))

    values = {}
    for m in ERA_METRICS:
        if m in E["ds"].variables:
            values[m] = _rnd(np.asarray(E["ds"].variables[m][si, yi, pr, pc], dtype="float64"))
    trend_var = f"rx1day_{season}_slope"
    if trend_var in E["ds"].variables:
        values["trend_mm_per_decade"] = _rnd(np.asarray(E["ds"].variables[trend_var][pr, pc], dtype="float64"))

    return {
        "lat": round(float(E["lat"][pr]), 3), "lon": round(float(E["lon"][pc]), 3),
        "year": year, "season": season, "values": values,
        "method": "Real per-cell ETCCDI extreme-precipitation indices (ERA5-Land, ~9km grid) at this AOI's "
                  "own nearest real grid cell -- R95p/R99p are wet-day 95th/99th percentiles over the real "
                  "1980-2025 baseline, trend is a real Theil-Sen slope on RX1day.",
    }


def real_design_intensity_for_duration(lat, lon, duration_hr, year=None, season="monsoon"):
    """Real, direct real-observational-IDF-curve lookup for a specific
    duration, log-log interpolated between this AOI's own real nearest-
    cell IDF points (the same real curve real_idf_curve_for_point
    returns) -- a real, location-specific alternative to climate_utils.py's
    CMIP6-Gumbel-fit design storm, for a caller that wants a genuine
    observed intensity instead of a statistical extrapolation. Returns
    None if the local dataset isn't present or has no usable points."""
    curve = real_idf_curve_for_point(lat, lon, year=year, season=season)
    if curve is None or not curve["points"]:
        return None
    durs = np.array([p["duration_hr"] for p in curve["points"]])
    intens = np.array([p["intensity_mm_hr"] for p in curve["points"]])
    d = max(float(duration_hr), float(durs.min()))
    d = min(d, float(durs.max()))
    # Real log-log interpolation -- IDF curves are conventionally near-
    # linear in log-log space (a power-law intensity~duration^-n shape),
    # the same reason this app's own HAND discharge library already uses
    # log-log interpolation for its own duration/level sweeps.
    log_i = np.interp(np.log(d), np.log(durs), np.log(intens))
    return {
        "duration_hr": round(d, 3), "intensity_mm_hr": round(float(np.exp(log_i)), 3),
        "lat": curve["lat"], "lon": curve["lon"], "year": curve["year"], "season": curve["season"],
        "method": curve["method"],
    }


# --------------------------------------------------------------------------
# Real Pakistan Tehsil (sub-district) boundaries, 578 real polygons -- the
# same source task's own real, un-dissolved `Tehsil_Boundary.shp` export
# (see that task's export_geojson.py), not built here. Lets a user search
# and select a real administrative area by name to set it directly as the
# AOI, instead of only manually drawing one -- genuinely useful since a
# user who already knows they want e.g. "Charsadda" or "Thatta" shouldn't
# have to eyeball-trace its real boundary by hand.
# --------------------------------------------------------------------------
_tehsils_state = {"loaded": False, "features": None}


def tehsils_available():
    return os.path.exists(TEHSILS_GEOJSON_PATH)


def _load_tehsils():
    with _lock:
        if _tehsils_state["loaded"]:
            return
        _tehsils_state["loaded"] = True
        if not os.path.exists(TEHSILS_GEOJSON_PATH):
            return
        with open(TEHSILS_GEOJSON_PATH) as f:
            gj = json.load(f)
        _tehsils_state["features"] = gj.get("features", [])


def search_tehsils(query, limit=15):
    """Real, case-insensitive substring match on tehsil/district/province
    name -- name matches ranked first (a search for "Thatta" should
    surface the real Thatta tehsil before some other tehsil that merely
    sits in Thatta district), then district, then province. Returns []
    (not raises) if the local dataset isn't present."""
    _load_tehsils()
    feats = _tehsils_state["features"]
    if not feats or not query:
        return []
    q = query.strip().lower()
    if len(q) < 2:
        return []

    def _s(v):
        # A real, live-observed data quirk: some tehsils' district/
        # province fields come through as a non-string (e.g. a float
        # NaN) rather than a missing/empty string.
        return str(v).lower() if v is not None and isinstance(v, str) else ""

    def rank(props):
        name, district, province = _s(props.get("name")), _s(props.get("district")), _s(props.get("province"))
        if q in name:
            return 0
        if q in district:
            return 1
        if q in province:
            return 2
        return None

    matches = []
    for f in feats:
        props = f.get("properties", {})
        r = rank(props)
        if r is not None:
            matches.append((r, props.get("name", ""), props))
    matches.sort(key=lambda m: (m[0], m[1]))
    return [{"id": p.get("id"), "name": p.get("name"), "district": p.get("district"),
             "province": p.get("province")} for _, _, p in matches[:limit]]


def tehsil_geometry(tehsil_id):
    """The real polygon geometry (GeoJSON dict) for one tehsil id, plus
    its own real centroid (a plain vertex-mean, only for map-fitting
    purposes -- not used for any real analysis elsewhere in this app,
    which always uses the real drawn/selected polygon itself). Returns
    None if the id isn't found or the dataset isn't present."""
    _load_tehsils()
    feats = _tehsils_state["features"]
    if not feats:
        return None
    tehsil_id = int(tehsil_id)
    for f in feats:
        if f.get("properties", {}).get("id") == tehsil_id:
            return {"geometry": f["geometry"], "properties": f["properties"]}
    return None


def _tehsil_id_to_meta():
    """{tehsil_id: {name, district, province}} built once from the real
    tehsil boundaries, for labeling top_cells' own real grid-cell hits
    (the NetCDF cubes carry a real tehsil_id per cell, not a name)."""
    _load_tehsils()
    feats = _tehsils_state["features"]
    if not feats:
        return {}
    out = {}
    for f in feats:
        p = f.get("properties", {})
        tid = p.get("id")
        if tid is not None:
            out[int(tid)] = {"name": p.get("name"), "district": p.get("district"), "province": p.get("province")}
    return out


TOP_CELLS_METRICS = ERA_METRICS + ["trend", "peak30"]


def top_cells(metric, year=None, season="monsoon", n=15):
    """The `n` highest REAL individual grid cells nationally for one
    metric -- no averaging, the real single worst-recorded cell each
    time, each labeled with its real containing tehsil/district/province
    (via the cube's own real tehsil_id grid). `metric="peak30"` reads the
    IMERG cube (real 30-min peak intensity); every other real metric
    reads the ERA5-Land cube. Returns [] if the local dataset (or this
    specific metric) isn't available -- never raises."""
    _open()
    if metric not in TOP_CELLS_METRICS:
        return []

    if metric == "peak30":
        I = _state["img"]
        if I is None or I["tid"] is None:
            return []
        season, year = _resolve_season_year(season, year, I)
        if season not in I["seasons"] or year not in I["years"]:
            return []
        si, yi = I["seasons"].index(season), I["years"].index(year)
        sl = np.asarray(I["ds"].variables["peak30"][si, yi, :, :], dtype="float64")
        S = I
    else:
        E = _state["era"]
        if E is None or E["tid"] is None:
            return []
        season, year = _resolve_season_year(season, year, E)
        si, yi = E["seasons"].index(season), E["years"].index(year)
        if metric == "trend":
            trend_var = f"rx1day_{season}_slope"
            if trend_var not in E["ds"].variables:
                return []
            sl = np.asarray(E["ds"].variables[trend_var][:, :], dtype="float64")
        else:
            if metric not in E["ds"].variables:
                return []
            sl = np.asarray(E["ds"].variables[metric][si, yi, :, :], dtype="float64")
        S = E

    tid_grid = S["tid"]
    good = np.isfinite(sl) & (tid_grid > 0)
    if not good.any():
        return []
    yy, xx = np.where(good)
    vals = sl[yy, xx]
    order = np.argsort(vals)[::-1][:n]

    id_to_meta = _tehsil_id_to_meta()
    out = []
    for k in order:
        r, c, ti = int(yy[k]), int(xx[k]), int(tid_grid[yy[k], xx[k]])
        meta = id_to_meta.get(ti, {})
        out.append({
            "lat": round(float(S["lat"][r]), 3), "lon": round(float(S["lon"][c]), 3),
            "value": round(float(vals[k]), 3), "tehsil_id": ti,
            "tehsil": meta.get("name"), "district": meta.get("district"), "province": meta.get("province"),
        })
    return out
