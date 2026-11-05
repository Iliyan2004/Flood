"""Run files: a saved, re-loadable description of a flood run's inputs.

A run file holds the exact request the app sent to /run (area, storm, solver and soil settings, boundary
inflows and outflows, storm centre, uploaded rainfall series ...) so the same run can be reloaded on this or
another SAILAB install. It does NOT contain results or the elevation data: a run that used the Pakistan SRTM
reloads against it, and one that used an uploaded DEM records that DEM's content id and needs the file
uploaded again (the id then matches and the cache key is identical).
"""
import json

RUNFILE_KIND = "sailab-runfile"
RUNFILE_VERSION = 1

# Fields of the /run request body that make up a run file (anything else is dropped).
REQUEST_FIELDS = (
    "geometry", "rain_mm_hr", "duration_hr", "rainfall_series", "include_buildings", "friction", "cfl",
    "infiltration_mm_hr", "use_soil_infiltration", "use_green_ampt", "use_channel_conditioning", "amc",
    "manning_scale", "inflows", "outflows", "use_global_dem", "custom_dem_id", "enable_arf", "storm_center_lon",
    "storm_center_lat", "predict_storm_motion",
)


def build_runfile(request_body, app_version, run_id=None):
    req = {k: request_body[k] for k in REQUEST_FIELDS if k in request_body}
    return {"kind": RUNFILE_KIND, "version": RUNFILE_VERSION, "app_version": app_version, "source_run_id": run_id,
            "request": req}


def runfile_from_meta(meta, app_version, run_id=None):
    """Rebuilds a run file for a run made before request bodies were saved, from its run_meta.json."""
    body = {
        "geometry": meta.get("geometry"), "rain_mm_hr": meta.get("rain_mm_hr"), "duration_hr": meta.get("duration_hr"),
        "rainfall_series": meta.get("rainfall_series"), "friction": meta.get("friction"), "cfl": meta.get("cfl"),
        "infiltration_mm_hr": meta.get("infiltration_mm_hr"), "amc": meta.get("amc"),
        "inflows": meta.get("inflows", []), "outflows": meta.get("outflows", []),
        "use_soil_infiltration": meta.get("use_soil_infiltration", False), "use_green_ampt": meta.get("use_green_ampt", False),
        "use_channel_conditioning": meta.get("use_channel_conditioning", False), "enable_arf": meta.get("enable_arf", False),
        "storm_center_lon": meta.get("storm_center_lon"), "storm_center_lat": meta.get("storm_center_lat"),
        "predict_storm_motion": meta.get("predict_storm_motion", False),
    }
    rf = build_runfile({k: v for k, v in body.items() if v is not None}, app_version, run_id)
    rf["reconstructed_from_run_meta"] = True   # building toggle and DEM choice were not recorded for such runs
    return rf


def parse_runfile(text):
    """Validates an uploaded run file; returns its `request` dict. Raises ValueError with a plain reason."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not a valid JSON file: {e}")
    if not isinstance(doc, dict) or doc.get("kind") != RUNFILE_KIND:
        raise ValueError("this is not a SAILAB run file")
    if int(doc.get("version", 0)) > RUNFILE_VERSION:
        raise ValueError("this run file was made by a newer SAILAB; update this install first")
    req = doc.get("request")
    if not isinstance(req, dict) or not req.get("geometry"):
        raise ValueError("the run file has no area (geometry)")
    return {k: req[k] for k in REQUEST_FIELDS if k in req}
