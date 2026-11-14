#!/usr/bin/env python3
"""
SAILAB (Simulation & Analytics for Inundation, Loss, Assets & Built
Environment) — Flask app. Formerly "Pakistan Flood Information System".

Phase 1 (this build): Tab 1 (area selection: draw or upload, no size cap)
wired directly into Tab 3 (the flood model), generalized from bounding-box
to arbitrary polygon. Tabs 2/4/5 are visible in the nav as upcoming phases
(see CLAUDE.md for the settled build order and why).

Run:
    ~/flood/venv/bin/python fis_app.py
Then open http://127.0.0.1:5060
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import threading
import re
import time
import uuid

import imageio_ffmpeg
import requests
import weasyprint
from flask import Flask, request, jsonify, Response, abort, send_file, redirect
from dotenv import load_dotenv

from polygon_utils import (polygon_geometry_hash, load_polygon_from_upload,
                            polygon_area_km2, crop_dem_to_polygon)
from global_dem_utils import (fetch_global_dem_mosaic, in_pakistan_srtm_coverage)
from hand_utils import (pluvial_ponding_depth, compute_hand, discharge_to_inundation, save_hand_result, load_hand_result,
                         build_inundation_library)
from pluvial_utils import assess_pluvial_risk
from progress_utils import read_sim_minutes, latest_progress
from buildings_utils import (fetch_buildings_for_aoi, burn_buildings_into_dem, get_building_footprints,
                              building_footprints_lonlat)
from infra_utils import (fetch_osm_infrastructure, assess_infrastructure_risk,
                          fetch_osm_building_heights, assess_building_submersion_risk)
from rainfall_rivers_utils import fetch_rainfall, fetch_waterway_lines, detect_all_boundary_crossings, fetch_temperature, fetch_temperature_forecast
from channel_utils import condition_dem_with_channel
from damage_utils import fetch_lulc_for_bounds, lulc_to_manning_grid, compute_damage, getis_ord_hotspots, WORLDCOVER_CLASSES
import flood_model
import loss_utils
import aal_utils
import basin_utils
import soil_utils
import idf_utils
from osm_buildings_3d import render_osm_buildings_3d_page
from mapbox3d_utils import build_mapbox_flood_scene
import coastal_layers_utils as coastal_layers
import glof_events_utils
import itslive_utils
import susceptibility_map_utils
from coastal_utils import (is_plausibly_coastal, total_water_level_m, static_coastal_inundation,
                            HISTORICAL_SURGE_PRESETS_M, gtsr_available, nearest_gtsr_station,
                            real_surge_window, build_dynamic_water_level_series, build_tide_surge_series, MEAN_TIDAL_RANGE_M,
                            fetch_real_wave_conditions, estimate_foreshore_slope_from_dem,
                            wave_runup_stockdon_m, DEFAULT_FORESHORE_SLOPE)
import climate_utils
import nbs_utils
import protection_utils
import protection_optimizer
import glof_utils
import srm_utils
import melt_hydrograph_utils
import geomorphology_utils
import dams_utils
import glacier_monitoring_utils
import sediment_utils
import storm_utils
import monitoring_utils
import ops_agents
import custom_dem_utils
import observed_validation_utils as ov_mod
import runfile_utils
import calibration_utils
import rainfall_csv_utils
import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import shape as shapely_shape, Point as ShapelyPoint, Polygon as ShapelyPolygon, mapping
import shapely

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "")
# Optional: a real externally-reachable base URL (e.g. an ngrok forwarding
# URL) for this app -- when set, live-monitoring webhook alerts include a
# real clickable link to the triggered run's own report.html. Left unset
# by default since 127.0.0.1:8000 (this process's own local bind address)
# would be a broken/misleading link for anyone outside this machine, and
# this app has no way to know its own real public address on its own.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
FLOOD_MODEL = os.path.join(BASE_DIR, "flood_model.py")
FIS_CACHE = os.path.join(BASE_DIR, "fis_cache")
NBS_CACHE = os.path.join(BASE_DIR, "fis_nbs_cache")
PROTECT_CACHE = os.path.join(BASE_DIR, "fis_protect_cache")
COMPARE_REPORT_CACHE = os.path.join(BASE_DIR, "fis_compare_reports")
os.makedirs(NBS_CACHE, exist_ok=True)
os.makedirs(PROTECT_CACHE, exist_ok=True)
os.makedirs(COMPARE_REPORT_CACHE, exist_ok=True)
UPLOADS = os.path.join(BASE_DIR, "fis_uploads")
IMPORTS = os.path.join(BASE_DIR, "fis_imports")   # plans handed over by other apps (Islamabad SimCity)
os.makedirs(IMPORTS, exist_ok=True)
AOI3D_CACHE = os.path.join(BASE_DIR, "fis_aoi3d_cache")
AREA3D_BUILDINGS_CACHE = os.path.join(BASE_DIR, "fis_area3d_buildings_cache")
os.makedirs(FIS_CACHE, exist_ok=True)
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(AOI3D_CACHE, exist_ok=True)
os.makedirs(AREA3D_BUILDINGS_CACHE, exist_ok=True)

# Fallback DEM until the Pakistan-wide SRTM is uploaded and converted.
# Real Pakistan-wide SRTM (EPSG:32642, ~29m, ~3.37 billion cells, 13.48GB).
# Left as GeoTIFF -- crop_dem_to_polygon reads only the small windowed area
# per request, so there's no need to (and no reason to) convert the whole
# country to ASCII up front.
#
# Path fixed 2026-09-07: this pointed at a "flood/" subdirectory that never
# existed under the real DGX-Shared mount (the real file sits one level up)
# -- `os.path.exists` silently failed and every run had actually been using
# the Bahawalpur fallback sample this whole time, not the real Pakistan-wide
# SRTM. Found while generalizing this function to global AOIs (below), not
# something that was investigated on its own -- see CLAUDE.md.
import data_paths
PAKISTAN_DEM = data_paths.get("PAKISTAN_DEM")
# Real WGS84 bounding box of PAKISTAN_DEM (computed once via rasterio
# transform_bounds against its real EPSG:32642 extent) -- an AOI inside
# this box uses the fast local file; anything outside it is handled by
# fetch_global_dem_mosaic below, which is what makes this app work
# anywhere on Earth rather than Pakistan-only.
PAKISTAN_SRTM_BOUNDS_WGS84 = (59.695013, 23.468104, 80.812387, 37.542288)
GLOBAL_DEM_CACHE = os.path.join(BASE_DIR, "global_dem_cache")
os.makedirs(GLOBAL_DEM_CACHE, exist_ok=True)
# Tier 0/2 HAND cache -- keyed by (AOI geometry, DEM source), so scrubbing
# the instant-flood discharge slider over the SAME drawn area only ever
# pays for the flow-direction/depression-fill/HAND computation once, not
# on every request (see hand_utils.save_hand_result's own docstring for
# why this is real cached compute, not a downloaded precomputed dataset --
# no free global HAND dataset without registration exists, checked
# directly rather than assumed; see CLAUDE.md).
HAND_CACHE = os.path.join(BASE_DIR, "hand_cache")
os.makedirs(HAND_CACHE, exist_ok=True)
# Real dynamic (GTSR-derived) coastal HVAR run workdirs -- each job gets
# its own persistent directory (unlike _resolve_coastal_dem's throwaway
# tmp file) since a real LISFLOOD-FP run needs a stable DEM/bci/bdy/par
# set on disk for the whole subprocess lifetime, and the finished run's
# own results/res-*.wd stack is read afterward by _read_water_stack for
# the animated 3D view.
COASTAL_DYN_CACHE = os.path.join(BASE_DIR, "fis_coastal_dyn_cache")
os.makedirs(COASTAL_DYN_CACHE, exist_ok=True)

app = Flask(__name__)

# ---- optional access token for non-local clients ----------------------------------------------------------
# When SAILAB_ACCESS_TOKEN is set (in .env), requests from other machines must carry it: visit
# http://<host>:8000/?token=<token> once (it sets a cookie), or send "Authorization: Bearer <token>". Requests
# from this machine itself (127.0.0.1 / ::1) are always allowed -- that covers the SSH-tunnel workflow (the tunnel
# is already authenticated by SSH), the app's own self-calls and the test suite. Unset = no restriction.
ACCESS_TOKEN_COOKIE = "sailab_access"


@app.before_request
def _require_access_token():
    token = os.environ.get("SAILAB_ACCESS_TOKEN", "").strip()
    if not token or request.remote_addr in ("127.0.0.1", "::1"):
        return None
    import hmac
    supplied = request.args.get("token", "") or request.cookies.get(ACCESS_TOKEN_COOKIE, "") or \
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if hmac.compare_digest(supplied, token):
        if request.args.get("token"):
            resp = redirect(request.path)
            resp.set_cookie(ACCESS_TOKEN_COOKIE, token, httponly=True, samesite="Lax", max_age=30 * 24 * 3600)
            return resp
        return None
    return Response("<h3 style='font-family:sans-serif'>SAILAB: access token required</h3>"
                    "<p style='font-family:sans-serif'>Open this address once with <code>?token=...</code> appended.</p>",
                    status=401, mimetype="text/html")
RUN_STATE = {}
RUN_STATE_LOCK = threading.Lock()

# Climate-scenario jobs (return-period design storm / climate intensification
# factor) -- real work here means reading many small real CMIP6 netCDF files
# (see climate_utils.py), which takes 1-6+ real minutes uncached, so these
# run as background jobs polled the same way a flood run is, not a plain
# synchronous request.
CLIMATE_JOBS = {}
CLIMATE_JOBS_LOCK = threading.Lock()
# Real, named pipeline stages (in actual execution order) -- shown to the
# user as a step list instead of just a raw percentage, so "what's it
# doing right now" is always answerable. "dem" is marked done immediately
# since DEM cropping happens synchronously in the /run route before this
# state even exists for polling.
RUN_STAGES = [
    ("dem", "Preparing DEM"),
    ("buildings", "Fetching real buildings"),
    ("model", "Running flood simulation"),
    ("postprocess", "Fetching roads & processing results"),
    ("scene", "Building 3D visualization"),
    ("done", "Done"),
]
class TrackedSlot:
    """The one-at-a-time solver slot (replaces a bare Semaphore(1)) that also remembers who holds it and who waits,
    so the UI can show why a job is not starting. `with SLOT:` still works; `with SLOT.labelled("name"):` adds a name."""
    def __init__(self):
        self._sem = threading.Semaphore(1)
        self._lock = threading.Lock()
        self.holder = None      # {"label", "since"}
        self.waiting = []       # [{"label", "since"}]

    def labelled(self, label):
        slot = self

        class _Ctx:
            def __enter__(self_inner):
                slot._acquire(label)
                return slot

            def __exit__(self_inner, *exc):
                slot._release()
                return False
        return _Ctx()

    def _acquire(self, label):
        entry = {"label": label, "since": time.time()}
        with self._lock:
            self.waiting.append(entry)
        self._sem.acquire()
        with self._lock:
            if entry in self.waiting:
                self.waiting.remove(entry)
            self.holder = {"label": label, "since": time.time()}

    def _release(self):
        with self._lock:
            self.holder = None
        self._sem.release()

    def __enter__(self):
        self._acquire("flood run")
        return self

    def __exit__(self, *exc):
        self._release()
        return False

    def snapshot(self):
        now = time.time()
        with self._lock:
            h = self.holder
            return {"holder": ({"label": h["label"], "running_s": round(now - h["since"], 1)} if h else None),
                    "waiting": [{"label": w["label"], "waiting_s": round(now - w["since"], 1)} for w in self.waiting]}


SIM_SLOT = TrackedSlot()  # shared-machine concurrency guard, same as gui_app.py
RUN_QUEUE = []      # run ids of /run jobs still waiting for SIM_SLOT, oldest first
RUN_CANCEL = set()  # run ids the user asked to cancel
RUN_PROCS = {}      # run_id -> the running flood_model.py subprocess

MODEL_NAME = "SAILAB"
APP_VERSION = "6.1"  # product version: major = capability jump, minor = visible feature round (see CLAUDE.md)
MODEL_FULL_NAME = "Simulation &amp; Analytics for Inundation, Loss, Assets &amp; Built Environment"
MODEL_TAGLINE = "From rainfall to risk, modeled."


class GlobalDemNotEnabled(Exception):
    """Raised when an AOI falls outside the local Pakistan SRTM and the
    caller hasn't explicitly opted in to fetching the open-source global
    DEM (Copernicus GLO-30, an external network fetch) -- per explicit
    user instruction, this is opt-in ("use the open source if the user
    asks for that"), not an automatic silent fallback. Callers surface
    this distinctly (a `needs_global_dem_consent` flag in the JSON error)
    so the UI can offer an explicit "yes, fetch it" action rather than
    just showing a generic failure."""
    pass


# Bump this whenever a change alters what a run computes for the SAME inputs
# (a new always-on terrain/physics conditioning, a bug fix in the model...), so
# cached results from before the change are not served as if still valid.
# History: 1 = permanent dam/reservoir DEM conditioning added (Oct 2026).
PIPELINE_VERSION = 1


def pipeline_signature():
    """Part of every flood run's cache key: the pipeline version plus a
    fingerprint of the dam data baked into every run's terrain."""
    return f"v{PIPELINE_VERSION}:{dams_utils.data_signature()}"


def current_dem_source(geom_wgs84=None, allow_global=False, custom_dem_id=None):
    """Picks the right DEM for an AOI. `geom_wgs84` is optional (the
    generic "/dem-source" status display calls this with none, and just
    wants the default local source's label) -- every real run/crop call
    site passes its own AOI geometry.

    - AOI inside the real Pakistan-wide SRTM's own coverage -> that file
      directly (fast, no network -- the actual downloaded DEM this app
      has on disk).
    - AOI outside it -> requires `allow_global=True` (an explicit user
      opt-in, threaded from a `use_global_dem` request field) before
      fetching an on-demand Copernicus DEM GLO-30 mosaic for just that
      AOI's bounding box (global_dem_utils.fetch_global_dem_mosaic),
      cached to disk by a hash of the rounded bbox so repeated requests
      over the same area don't re-fetch. Without that opt-in, raises
      GlobalDemNotEnabled rather than silently reaching out to an
      external open-source dataset -- see CLAUDE.md.
    - Pakistan SRTM missing from disk -> raises FileNotFoundError (the old
      silent fallback to a small Bahawalpur sample was removed).
    """
    if custom_dem_id:
        # A user-uploaded elevation model (custom_dem_utils) overrides the Pakistan SRTM, but only
        # where it actually covers the area -- a partial cover would silently give NODATA edges.
        cpath = custom_dem_utils.path_for(custom_dem_id)
        if cpath is None:
            raise ValueError("the uploaded elevation model was not found on this server; upload it again")
        if geom_wgs84 is not None and not custom_dem_utils.covers_bbox(cpath, shapely_shape(geom_wgs84).bounds):
            raise ValueError("the uploaded elevation model does not fully cover the selected area")
        return cpath, "Custom DEM (uploaded)"
    if geom_wgs84 is not None:
        bbox = shapely_shape(geom_wgs84).bounds  # (minx, miny, maxx, maxy)
        if os.path.exists(PAKISTAN_DEM) and in_pakistan_srtm_coverage(bbox, PAKISTAN_SRTM_BOUNDS_WGS84):
            return PAKISTAN_DEM, "Pakistan-wide SRTM (uploaded)"
        if not allow_global:
            raise GlobalDemNotEnabled(
                "This area is outside the downloaded Pakistan SRTM's coverage. "
                "Enable \"Use open-source global DEM\" to fetch real Copernicus DEM "
                "GLO-30 elevation data (AWS Open Data, free, ~30m) for this area.")
        return _resolve_global_dem(bbox), "Global (Copernicus DEM GLO-30, on-demand, ~30m)"
    if os.path.exists(PAKISTAN_DEM):
        return PAKISTAN_DEM, "Pakistan-wide SRTM (uploaded)"
    raise FileNotFoundError(
        f"The Pakistan SRTM DEM was not found at {PAKISTAN_DEM} -- update PAKISTAN_DEM in fis_app.py "
        f"if the data has moved. (The old small Bahawalpur sample fallback was removed deliberately, "
        f"so a wrong path can no longer silently run on the wrong terrain.)")


def _resolve_global_dem(bbox_wgs84):
    """Cached wrapper around fetch_global_dem_mosaic -- keyed by the
    rounded bbox (0.001 degree, ~100m) so re-requesting the same AOI (a
    cache hit on the flood-run cache itself, or just re-opening Tab 1)
    doesn't re-fetch Copernicus tiles every time."""
    minx, miny, maxx, maxy = bbox_wgs84
    key = hashlib.sha256(f"{minx:.3f},{miny:.3f},{maxx:.3f},{maxy:.3f}".encode()).hexdigest()[:16]
    out_path = os.path.join(GLOBAL_DEM_CACHE, f"{key}.tif")
    if not os.path.exists(out_path):
        tmp_path = out_path + f".tmp{uuid.uuid4().hex}"
        fetch_global_dem_mosaic(bbox_wgs84, tmp_path)
        os.replace(tmp_path, out_path)
    return out_path




PAGE = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{MODEL_NAME}</title>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.css" rel="stylesheet">
<link href="https://unpkg.com/@mapbox/mapbox-gl-draw@1.4.3/dist/mapbox-gl-draw.css" rel="stylesheet">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --page:#070B0F; --page-2:#0A1015; --surface:#101820; --surface-2:#182028; --surface-3:#1E2932;
  --ink:#EAF3F6; --ink-2:#9FB8C2; --muted:#5C7680;
  --line:#233039; --line-soft:#1A252D;
  --accent:#2FE0D8; --accent-2:#2FB8CF; --accent-dim:#1C8A94; --accent-soft:#0E2C30;
  --violet:#8C7CF0; --amber:#F5A742; --danger:#F0575A; --success:#3ED598;
  --glow-accent:0 0 0 1px rgba(47,224,216,0.25), 0 0 24px rgba(47,224,216,0.18);
  --glow-accent-strong:0 0 0 1px rgba(47,224,216,0.5), 0 0 32px rgba(47,224,216,0.35), 0 0 4px rgba(47,224,216,0.6);
  --shadow-card:0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -8px rgba(0,0,0,0.55);
  --shadow-float:0 10px 40px -8px rgba(0,0,0,0.65), 0 2px 8px rgba(0,0,0,0.4);
  --ease:cubic-bezier(.2,.8,.25,1);
  --radius:12px; --radius-sm:8px; --radius-lg:16px;
}}
* {{ box-sizing:border-box; }}
::selection {{ background:var(--accent); color:#04181a; }}
html, body {{ margin:0; height:100%; font-family:"Public Sans",system-ui,sans-serif; color:var(--ink);
  background:
    radial-gradient(1100px 620px at 14% -8%, rgba(47,224,216,0.10), transparent 60%),
    radial-gradient(900px 700px at 108% 8%, rgba(140,124,240,0.08), transparent 55%),
    linear-gradient(180deg, var(--page-2), var(--page) 70%);
  background-attachment:fixed;
  -webkit-font-smoothing:antialiased;
}}
::-webkit-scrollbar {{ width:9px; height:9px; }}
::-webkit-scrollbar-track {{ background:transparent; }}
::-webkit-scrollbar-thumb {{ background:var(--surface-3); border-radius:99px; border:2px solid var(--page); }}
::-webkit-scrollbar-thumb:hover {{ background:var(--accent-dim); }}
* {{ scrollbar-width:thin; scrollbar-color:var(--surface-3) transparent; }}
.app {{ display:flex; flex-direction:column; height:100vh; position:relative; }}
header {{ display:flex; align-items:center; justify-content:space-between; padding:13px 22px;
  background:linear-gradient(180deg, rgba(16,24,32,0.92), rgba(16,24,32,0.80));
  backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--line); position:relative; z-index:20;
  box-shadow:0 1px 0 rgba(255,255,255,0.02) inset; }}
header::after {{ content:''; position:absolute; left:0; right:0; bottom:-1px; height:1px;
  background:linear-gradient(90deg, transparent, var(--accent), var(--violet), transparent);
  background-size:200% 100%; opacity:0.55; animation:sheen 9s linear infinite; }}
@keyframes sheen {{ 0% {{ background-position:0% 0; }} 100% {{ background-position:200% 0; }} }}
.brand {{ font-family:"Barlow Condensed",sans-serif; font-weight:800; font-size:23px; text-transform:uppercase;
  letter-spacing:0.05em; display:flex; align-items:center; gap:9px; }}
.brand::before {{ content:''; width:9px; height:9px; border-radius:99px; background:var(--accent);
  box-shadow:0 0 0 3px rgba(47,224,216,0.15), 0 0 14px rgba(47,224,216,0.85); flex:0 0 auto;
  animation:pulseDot 2.6s ease-in-out infinite; }}
@keyframes pulseDot {{ 0%,100% {{ opacity:1; transform:scale(1); }} 50% {{ opacity:0.55; transform:scale(0.72); }} }}
.brand span {{ background:linear-gradient(120deg, var(--accent) 0%, #7FF0EA 45%, var(--violet) 100%);
  -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent;
  text-shadow:0 0 30px rgba(47,224,216,0.25); }}
#demSource {{ display:flex; align-items:center; gap:7px; font-family:"IBM Plex Mono",monospace; letter-spacing:0.01em; }}
#demSource:not(:empty)::before {{ content:''; width:6px; height:6px; border-radius:99px; background:var(--success);
  box-shadow:0 0 8px rgba(62,213,152,0.9); flex:0 0 auto; animation:pulseDot 2.6s ease-in-out infinite; }}
.main {{ display:flex; flex:1; overflow:hidden; position:relative; z-index:1; }}
.left-col {{ display:flex; flex-direction:column; flex:1; min-width:320px; }}
#mapWrap {{ position:relative; flex:1; display:flex; min-height:120px; }}
#map {{ flex:1; }}
.vresizer {{ height:5px; cursor:row-resize; background:var(--line); flex:0 0 auto; transition:background 0.2s var(--ease); }}
.vresizer:hover, .vresizer.active {{ background:var(--accent); box-shadow:var(--glow-accent); }}
.view3d-wrap {{ position:relative; flex:1; display:flex; flex-direction:column; min-height:120px; }}
.view3d-tabs {{ display:flex; gap:0; background:var(--surface); flex:0 0 auto; border-bottom:1px solid var(--line); }}
.view3d-tabs button, .view3d-tabs a {{ flex:1; padding:9px 4px; border:none; border-right:1px solid var(--line-soft);
  background:transparent; color:var(--ink-2);
  font-family:"Barlow Condensed",sans-serif; font-weight:600; text-transform:uppercase; letter-spacing:0.04em;
  font-size:11.5px; cursor:pointer; text-align:center; box-sizing:border-box; transition:all 0.2s var(--ease); }}
.view3d-tabs button:hover:not(:disabled), .view3d-tabs a:hover {{ background:var(--surface-2); color:var(--ink); }}
.view3d-tabs button.active {{ background:var(--accent-soft); color:var(--accent); box-shadow:inset 0 -2px 0 var(--accent); }}
.view3d-tabs button:disabled {{ color:var(--muted); cursor:not-allowed; opacity:0.4; }}
.tier-tabs {{ display:flex; gap:8px; background:transparent; flex:0 0 auto; position:sticky; top:0; z-index:5;
  padding:10px 10px 8px; background:linear-gradient(180deg, var(--surface), rgba(16,24,32,0.97));
  border-bottom:1px solid var(--line); }}
.tier-tabs button {{ flex:1; padding:11px 6px; border:1px solid var(--line); background:var(--surface-2); color:var(--ink-2);
  font-family:"Barlow Condensed",sans-serif; font-weight:700; text-transform:uppercase; letter-spacing:0.03em;
  font-size:12.5px; cursor:pointer; text-align:center; box-sizing:border-box; border-radius:var(--radius-sm);
  transition:all 0.22s var(--ease); position:relative; }}
.tier-tabs button:hover {{ color:var(--ink); border-color:var(--accent-dim); transform:translateY(-1px); }}
.tier-tabs button.active {{ background:linear-gradient(160deg, var(--accent), var(--accent-2)); color:#04181a;
  border-color:var(--accent); box-shadow:var(--glow-accent-strong); }}
iframe.frame3d {{ width:100%; flex:1; border:1px solid var(--line); border-top:none; background:#0B1417; display:block; }}
.resizer {{ width:5px; cursor:col-resize; background:var(--line); flex:0 0 auto; transition:background 0.2s var(--ease); }}
.resizer:hover, .resizer.active {{ background:var(--accent); box-shadow:var(--glow-accent); }}
.right-col {{ width:400px; min-width:280px; max-width:70vw; background:linear-gradient(180deg, var(--surface), var(--page-2) 96%);
  border-left:1px solid var(--line); overflow-y:auto; display:flex; flex-direction:column; }}
.panel-section {{ padding:17px 19px; border-bottom:1px solid var(--line-soft); display:flex; flex-direction:column; gap:12px;
  position:relative; transition:background 0.25s var(--ease); }}
.panel-section:hover {{ background:rgba(255,255,255,0.012); }}
.panel-section h3 {{ font-family:"Barlow Condensed",sans-serif; text-transform:uppercase; font-size:14.5px;
  color:var(--ink); margin:0 0 2px; letter-spacing:0.045em; display:flex; align-items:center; gap:8px; font-weight:700; }}
.panel-section h3::before {{ content:''; width:3px; height:14px; border-radius:2px;
  background:linear-gradient(180deg, var(--accent), var(--violet)); box-shadow:0 0 8px rgba(47,224,216,0.5); }}
label {{ font-size:12.5px; color:var(--ink-2); display:block; margin-bottom:4px; font-weight:500; }}
input[type=number], input[type=file], input[type=text] {{ width:100%; padding:9px 10px; border:1px solid var(--line); border-radius:var(--radius-sm);
  font-family:"IBM Plex Mono",monospace; font-size:12.5px; background:var(--surface-2); color:var(--ink);
  transition:all 0.2s var(--ease); outline:none; }}
input[type=number]:focus, input[type=file]:focus, input[type=text]:focus {{ border-color:var(--accent); box-shadow:var(--glow-accent); background:var(--surface-3); }}
input[type=checkbox] {{ accent-color:var(--accent); }}
select {{ padding:8px 10px; border:1px solid var(--line); border-radius:var(--radius-sm); font-size:12.5px;
  background:var(--surface-2); color:var(--ink); outline:none; transition:all 0.2s var(--ease); cursor:pointer; }}
select:focus, select:hover {{ border-color:var(--accent-dim); }}
button {{ font-family:inherit; }}
button.action {{ padding:12px; border:none; border-radius:var(--radius-sm);
  background:linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%); color:#04181a;
  font-family:"Barlow Condensed",sans-serif; font-weight:700; text-transform:uppercase; letter-spacing:0.045em;
  cursor:pointer; font-size:14px; box-shadow:0 4px 16px -4px rgba(47,224,216,0.45), 0 1px 0 rgba(255,255,255,0.2) inset;
  transition:all 0.18s var(--ease); position:relative; overflow:hidden; }}
button.action:hover:not(:disabled) {{ box-shadow:0 6px 22px -4px rgba(47,224,216,0.6), 0 1px 0 rgba(255,255,255,0.25) inset;
  transform:translateY(-1px); }}
button.action:active:not(:disabled) {{ transform:translateY(0px) scale(0.985); }}
button.action:disabled {{ opacity:0.35; cursor:not-allowed; box-shadow:none; filter:grayscale(0.3); }}
button.secondary {{ padding:9px 12px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--surface-2);
  color:var(--ink); font-size:12.5px; cursor:pointer; transition:all 0.18s var(--ease); font-weight:500; }}
button.secondary:hover {{ border-color:var(--accent); color:var(--accent); background:var(--accent-soft);
  box-shadow:var(--glow-accent); transform:translateY(-1px); }}
button.secondary:active {{ transform:translateY(0); }}
.pill {{ display:inline-block; padding:3.5px 11px; border-radius:999px; font-size:11px; background:var(--accent-soft);
  color:var(--accent); text-transform:uppercase; letter-spacing:0.045em; border:1px solid rgba(47,224,216,0.25); font-weight:600; }}
.progress-track {{ width:100%; height:10px; background:var(--surface-2); border-radius:99px; overflow:hidden; border:1px solid var(--line); position:relative; }}
.progress-fill {{ height:100%; background:linear-gradient(90deg, var(--accent-dim), var(--accent), var(--violet)); width:2%;
  transition:width 0.6s var(--ease); position:relative; box-shadow:0 0 10px rgba(47,224,216,0.6); }}
.progress-fill::after {{ content:''; position:absolute; inset:0; background:linear-gradient(90deg, transparent, rgba(255,255,255,0.35), transparent);
  background-size:60px 100%; animation:shimmer 1.6s linear infinite; }}
@keyframes shimmer {{ 0% {{ background-position:-60px 0; }} 100% {{ background-position:220px 0; }} }}
.note {{ font-size:11.5px; color:var(--muted); line-height:1.55; }}
.stat-row {{ display:flex; justify-content:space-between; align-items:center; font-family:"IBM Plex Mono",monospace;
  font-size:12.5px; padding:6px 2px; border-bottom:1px solid var(--line-soft); color:var(--ink-2); transition:background 0.15s; }}
.stat-row:hover {{ background:rgba(255,255,255,0.02); }}
.stat-row span:last-child {{ color:var(--ink); font-weight:600; }}
a.btn {{ display:inline-block; background:var(--surface-2); color:var(--ink); text-decoration:none;
  padding:9px 14px; border-radius:var(--radius-sm); border:1px solid var(--line); font-size:12.5px; text-align:center;
  transition:all 0.18s var(--ease); font-weight:500; }}
a.btn:hover {{ border-color:var(--accent); color:var(--accent); transform:translateY(-1px); box-shadow:var(--glow-accent); }}
a.btn.primary {{ background:linear-gradient(135deg, var(--accent), var(--accent-2)); color:#04181a; border-color:var(--accent); font-weight:700;
  box-shadow:0 4px 16px -4px rgba(47,224,216,0.4); }}
a.btn.primary:hover {{ color:#04181a; box-shadow:0 6px 20px -4px rgba(47,224,216,0.6); }}
.placeholder {{ margin:auto; text-align:center; color:var(--muted); font-size:14px; max-width:420px; }}
.layer-switch {{ position:absolute; top:12px; left:56px; z-index:5; background:rgba(16,24,32,0.75);
  backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
  border-radius:var(--radius-sm); border:1px solid var(--line); overflow:hidden; display:flex; box-shadow:var(--shadow-float); }}
.layer-switch button {{ border:none; background:transparent; padding:7px 13px; font-size:12px; cursor:pointer;
  color:var(--ink-2); font-family:"Barlow Condensed",sans-serif; font-weight:600; text-transform:uppercase; letter-spacing:0.03em;
  transition:all 0.18s var(--ease); }}
.layer-switch button:hover {{ color:var(--ink); background:rgba(255,255,255,0.04); }}
.layer-switch button.active {{ background:var(--accent); color:#04181a; }}
#geocoderSlot {{ position:absolute; top:12px; left:50%; transform:translateX(-50%); z-index:6; width:340px; max-width:60vw; }}
#geoInput {{ width:100%; box-sizing:border-box; padding:10px 14px; font-size:13px; font-family:"Public Sans",sans-serif;
  border:1px solid var(--line); border-radius:var(--radius-sm); box-shadow:var(--shadow-float); outline:none;
  background:rgba(16,24,32,0.82); backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px); color:var(--ink);
  transition:all 0.2s var(--ease); }}
#geoInput::placeholder {{ color:var(--muted); }}
#geoInput:focus {{ border-color:var(--accent); box-shadow:var(--glow-accent), var(--shadow-float); }}
#geoResults {{ position:absolute; top:calc(100% + 6px); left:0; right:0; background:rgba(16,24,32,0.94);
  backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); border:1px solid var(--line);
  border-radius:var(--radius-sm); box-shadow:var(--shadow-float); overflow:hidden; display:none; max-height:260px; overflow-y:auto;
  animation:dropIn 0.18s var(--ease); }}
@keyframes dropIn {{ from {{ opacity:0; transform:translateY(-6px); }} to {{ opacity:1; transform:translateY(0); }} }}
#geoResults.open {{ display:block; }}
#geoResults .geo-item {{ padding:10px 14px; font-size:12.5px; cursor:pointer; border-bottom:1px solid var(--line-soft); transition:background 0.15s; }}
#geoResults .geo-item:last-child {{ border-bottom:none; }}
#geoResults .geo-item:hover, #geoResults .geo-item.sel {{ background:var(--accent); color:#04181a; }}
.struct-row {{ display:flex; gap:6px; align-items:center; justify-content:space-between; font-size:12px; padding:7px 2px;
  border-bottom:1px solid var(--line-soft); }}
details summary {{ cursor:pointer; color:var(--ink-2); transition:color 0.15s; }}
details summary:hover {{ color:var(--accent); }}
::placeholder {{ color:var(--muted); opacity:1; }}
@keyframes revealIn {{ from {{ opacity:0; transform:translateY(7px); }} to {{ opacity:1; transform:translateY(0); }} }}
.reveal-in {{ animation:revealIn 0.4s var(--ease); }}

/* ---- Map-first layout (FastFlood-style): full-screen map, floating toolbar, floating drawer, flood view on the main screen.
   Colours and every control are unchanged; only structure, spacing, shape and type scale differ. ---- */
:root {{ --drawer-w:420px; }}
html, body {{ overflow:hidden; }}
.main {{ position:absolute; inset:0; display:block; }}
.left-col {{ position:absolute; inset:0; display:block; min-width:0; }}
#mapWrap {{ position:absolute; inset:0; display:block; min-height:0; }}
#map {{ position:absolute; inset:0; }}
.vresizer, .resizer {{ display:none !important; }}
header {{ position:absolute; top:10px; left:10px; right:calc(var(--drawer-w) + 22px); z-index:25; gap:10px; justify-content:flex-start;
  padding:7px 10px; border:1px solid var(--line); border-radius:16px; background:rgba(14,22,29,0.84);
  box-shadow:var(--shadow-float); transition:right .3s var(--ease); }}
header::after {{ display:none; }}
body.drawer-closed header {{ right:10px; }}
header .brand {{ font-size:20px; flex:0 0 auto; padding:0 6px 0 2px; }}
header .brand small {{ display:none; }}
header #geocoderSlot {{ position:static; transform:none; flex:1 1 220px; min-width:140px; max-width:420px; width:auto; }}
header #geoInput {{ padding:9px 12px; border-radius:12px; box-shadow:none; background:var(--surface-2); }}
header .layer-switch {{ position:static; border-radius:12px; box-shadow:none; background:var(--surface-2); flex:0 0 auto; }}
header .layer-switch button {{ padding:8px 11px; font-size:11.5px; }}
header .hdr-nav {{ display:flex; gap:6px; margin-left:auto; flex:0 0 auto; }}
header .hdr-nav a {{ text-decoration:none; color:var(--ink-2); border:1px solid var(--line); border-radius:12px; padding:8px 12px; font-size:12.5px; font-weight:600; background:var(--surface-2); }}
header .hdr-nav a:hover {{ color:var(--accent); border-color:var(--accent); }}
header #demSource {{ font-size:11px; flex:0 0 auto; }}
.mapboxgl-ctrl-top-left {{ top:68px !important; }}
.mapboxgl-ctrl-top-right, .mapboxgl-ctrl-bottom-right {{ right:calc(var(--drawer-w) + 12px) !important; transition:right .3s var(--ease); }}
body.drawer-closed .mapboxgl-ctrl-top-right, body.drawer-closed .mapboxgl-ctrl-bottom-right {{ right:0 !important; }}
.right-col {{ position:absolute; top:10px; right:10px; bottom:10px; width:var(--drawer-w) !important; max-width:none; min-width:0; z-index:22;
  border:1px solid var(--line); border-radius:20px; background:rgba(14,22,29,0.90); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  box-shadow:var(--shadow-float); padding-bottom:12px; transition:transform .3s var(--ease); }}
body.drawer-closed .right-col {{ transform:translateX(calc(100% + 24px)); }}
#drawerToggle {{ position:absolute; z-index:23; top:50%; right:calc(var(--drawer-w) + 14px); width:26px; height:56px; margin-top:-28px;
  border:1px solid var(--line); border-radius:12px; background:rgba(14,22,29,0.9); color:var(--ink-2); cursor:pointer; font-size:15px;
  transition:right .3s var(--ease), color .15s; }}
#drawerToggle:hover {{ color:var(--accent); border-color:var(--accent); }}
body.drawer-closed #drawerToggle {{ right:10px; }}
.right-col .panel-section {{ margin:10px 12px 0; padding:15px 16px; border:1px solid var(--line-soft); border-radius:16px; background:rgba(24,32,40,0.55); }}
.right-col .panel-section:hover {{ background:rgba(24,32,40,0.7); }}
.panel-section h3 {{ font-family:"Public Sans",system-ui,sans-serif; text-transform:none; font-size:15.5px; font-weight:700; letter-spacing:0; }}
.panel-section h3::before {{ display:none; }}

/* ---- SAILAB Physics: tabbed side panel; only the physics workflow is shown ---- */
.phys-off {{ display:none !important; }}
#rightCol .panel-section[data-pane] {{ display:none; }}
.ptabs {{ padding-bottom:10px; }}
header .hdr-nav a.on {{ color:var(--accent); border-color:var(--accent); }}
#rightCol select {{ max-width:100%; text-overflow:ellipsis; }}
.ptabs .ptab {{ flex:1; }}

/* ---- no overlaps: map controls clear of the toolbar, flood card clear of the map logo ---- */
.mapboxgl-ctrl-top-right {{ top:68px !important; }}
.view3d-wrap {{ bottom:34px !important; }}
body.view-main .view3d-wrap {{ bottom:34px !important; }}
#viewShowBtn {{ bottom:34px !important; }}
header .brand {{ text-decoration:none; color:inherit; }}
.tier-tabs {{ padding:12px 12px 0; gap:6px; }}
.tier-tabs button {{ border-radius:999px; font-family:"Public Sans",system-ui,sans-serif; font-weight:600; text-transform:none; letter-spacing:0; font-size:12.5px; padding:10px 6px; }}
button.action {{ border-radius:14px; font-family:"Public Sans",system-ui,sans-serif; text-transform:none; letter-spacing:0; font-weight:700; font-size:15px; padding:13px; }}
button.secondary, a.btn {{ border-radius:12px; }}
input[type=number], input[type=file], input[type=text], select {{ border-radius:12px; }}
.pill {{ font-size:11px; }}
.view3d-wrap {{ position:absolute; z-index:9; left:10px; bottom:10px; width:min(48vw, 720px); height:42vh; min-height:0;
  border:1px solid var(--line); border-radius:18px; overflow:hidden; box-shadow:var(--shadow-float); background:var(--surface); }}
.view3d-wrap iframe.frame3d {{ border:none; }}
body.view-main .view3d-wrap {{ top:66px; width:auto; height:auto; right:calc(var(--drawer-w) + 22px); }}
body.view-main.drawer-closed .view3d-wrap {{ right:10px; }}
body.view-hidden .view3d-wrap {{ display:none; }}
.view3d-tabs .v3d-btn {{ flex:0 0 auto; padding:8px 12px; border:none; background:transparent; color:var(--ink-2); cursor:pointer; font-size:12px; font-weight:600; }}
.view3d-tabs .v3d-btn:hover {{ color:var(--accent); }}
#viewShowBtn {{ position:absolute; z-index:9; left:10px; bottom:10px; display:none; padding:10px 16px; border-radius:14px; border:1px solid var(--line);
  background:rgba(14,22,29,0.9); color:var(--ink); font-weight:600; cursor:pointer; box-shadow:var(--shadow-float); }}
body.view-hidden #viewShowBtn {{ display:block; }}
#welcomeOverlay {{ position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center; padding:16px;
  background:rgba(4,10,13,0.55); backdrop-filter:blur(5px); -webkit-backdrop-filter:blur(5px); }}
#welcomeOverlay .wcard {{ position:relative; width:min(760px,100%); display:grid; grid-template-columns:1fr 1fr; border-radius:22px; overflow:hidden;
  border:1px solid var(--line); box-shadow:var(--shadow-float); background:var(--surface); }}
#welcomeOverlay .wl {{ padding:28px; background:linear-gradient(160deg, var(--accent-soft), var(--surface) 80%); }}
#welcomeOverlay .wl h2 {{ margin:0 0 14px; font-family:"Barlow Condensed",sans-serif; font-size:30px; text-transform:uppercase; letter-spacing:.05em; }}
#welcomeOverlay .wl li {{ list-style:none; margin:0 0 12px; font-size:13.5px; color:var(--ink-2); line-height:1.45; }}
#welcomeOverlay .wl li b {{ display:block; color:var(--ink); font-size:14.5px; }}
#welcomeOverlay ul {{ margin:0; padding:0; }}
#welcomeOverlay .wr {{ padding:28px; display:flex; flex-direction:column; gap:10px; background:var(--surface-2); }}
#welcomeOverlay .wr h3 {{ margin:0 0 4px; font-size:22px; line-height:1.2; }}
#welcomeOverlay .wr h3 em {{ font-style:normal; color:var(--accent); }}
#welcomeOverlay .wx {{ position:absolute; top:12px; right:12px; width:32px; height:32px; border-radius:10px; border:1px solid var(--line); background:var(--surface); color:var(--ink); cursor:pointer; font-size:17px; }}
@media (max-width:860px) {{ :root {{ --drawer-w:340px; }} #welcomeOverlay .wcard {{ grid-template-columns:1fr; }} #welcomeOverlay .wl {{ display:none; }} }}
/* ---- light theme: same tokens, light values; toggled by data-theme on <html> ---- */
:root[data-theme="light"] {{
  --page:#EAF0F4; --page-2:#F3F7FA; --surface:#FFFFFF; --surface-2:#F1F5F8; --surface-3:#D5DFE6;
  --ink:#10222B; --ink-2:#3F5964; --muted:#6B8490;
  --line:#CBD8E0; --line-soft:#DCE6EC;
  --accent:#0C8E98; --accent-2:#0A7C8C; --accent-dim:#5FB6BD; --accent-soft:#D8EFF1;
  --violet:#6554D6; --amber:#B8730F; --danger:#C93A3D; --success:#1E9C6B;
  --glow-accent:0 0 0 1px rgba(12,142,152,0.25), 0 0 18px rgba(12,142,152,0.12);
  --glow-accent-strong:0 0 0 1px rgba(12,142,152,0.5), 0 0 22px rgba(12,142,152,0.25);
  --shadow-card:0 1px 2px rgba(16,34,43,0.08), 0 8px 24px -10px rgba(16,34,43,0.18);
  --shadow-float:0 10px 36px -10px rgba(16,34,43,0.28), 0 2px 8px rgba(16,34,43,0.10);
}}
:root[data-theme="light"], :root[data-theme="light"] body {{
  background:radial-gradient(1100px 620px at 14% -8%, rgba(12,142,152,0.07), transparent 60%), linear-gradient(180deg, var(--page-2), var(--page) 70%); }}
:root[data-theme="light"] ::selection {{ color:#fff; }}
:root[data-theme="light"] header {{ background:rgba(255,255,255,0.88); }}
:root[data-theme="light"] .layer-switch, :root[data-theme="light"] #geoInput {{ background:rgba(255,255,255,0.9); }}
:root[data-theme="light"] #geoResults {{ background:rgba(255,255,255,0.97); }}
:root[data-theme="light"] .right-col {{ background:rgba(244,248,250,0.92); }}
:root[data-theme="light"] .tier-tabs {{ background:transparent; }}
:root[data-theme="light"] .right-col .panel-section {{ background:rgba(255,255,255,0.8); }}
:root[data-theme="light"] .right-col .panel-section:hover {{ background:#fff; }}
:root[data-theme="light"] .panel-section:hover, :root[data-theme="light"] .stat-row:hover, :root[data-theme="light"] .layer-switch button:hover {{ background:rgba(16,34,43,0.04); }}
:root[data-theme="light"] #drawerToggle, :root[data-theme="light"] #viewShowBtn {{ background:rgba(255,255,255,0.95); }}
:root[data-theme="light"] #welcomeOverlay {{ background:rgba(234,240,244,0.7); }}
:root[data-theme="light"] button.action, :root[data-theme="light"] .tier-tabs button.active {{ color:#fff; }}
:root[data-theme="light"] input, :root[data-theme="light"] select, :root[data-theme="light"] textarea {{ background:#fff; color:var(--ink); }}
#themeToggle {{ flex:0 0 auto; width:34px; height:34px; border-radius:10px; border:1px solid var(--line); background:var(--surface-2);
  color:var(--ink-2); cursor:pointer; font-size:16px; line-height:1; padding:0; }}
#themeToggle:hover {{ color:var(--ink); border-color:var(--accent); }}
#queueChip {{ flex:0 0 auto; height:34px; padding:0 12px; border-radius:10px; border:1px solid var(--line); background:var(--surface-2);
  color:var(--ink-2); cursor:pointer; font-size:12px; font-weight:600; white-space:nowrap; position:relative; }}
#queueChip:hover {{ color:var(--ink); border-color:var(--accent); }}
#queueChip.busy {{ color:var(--accent); border-color:var(--accent); }}
#queuePop {{ position:absolute; top:calc(100% + 8px); right:0; width:340px; max-height:60vh; overflow:auto; z-index:40; display:none;
  background:var(--surface); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow-float); padding:10px; text-align:left; }}
#queuePop.open {{ display:block; }}
#queuePop h4 {{ margin:2px 4px 8px; font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }}
.qrow {{ display:flex; align-items:center; gap:8px; padding:8px; border:1px solid var(--line-soft); border-radius:10px; margin-bottom:6px; font-size:12px; color:var(--ink); }}
.qrow .qmeta {{ flex:1; min-width:0; }} .qrow .qsub {{ color:var(--muted); font-size:11px; }}
.qrow button {{ border:1px solid var(--danger); color:var(--danger); background:transparent; border-radius:8px; padding:4px 9px; cursor:pointer; font-size:11px; font-weight:600; }}
.qrow button:hover {{ background:var(--danger); color:#fff; }}
.qrow button:disabled {{ opacity:.5; cursor:default; }}
.qempty {{ color:var(--muted); font-size:12px; padding:6px 4px; }}
.dg {{ margin-top:10px; padding:10px 12px; border:1px solid var(--line); border-radius:12px; }}
.dg-title {{ font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin-bottom:6px; }}
.dg-svg {{ width:100%; height:auto; display:block; }}
.dg-water {{ fill:var(--accent, var(--aqua)); opacity:.42; }}
.dg-surface {{ stroke:var(--accent, var(--aqua)); stroke-width:2; }}
.dg-person {{ fill:var(--ink); opacity:.78; }}
.dg-tick {{ stroke:var(--muted); stroke-width:1; }}
.dg-lbl {{ fill:var(--muted); font-size:9px; font-family:ui-monospace,Menlo,monospace; }}
.dg-ground {{ fill:var(--muted); opacity:.6; }}
.dg-row {{ display:flex; align-items:center; gap:8px; margin-top:6px; font-size:12px; color:var(--muted); }}
.dg-row input[type=range] {{ flex:1; width:auto; }}
.dg-row b {{ color:var(--ink); min-width:48px; text-align:right; font-variant-numeric:tabular-nums; }}
.dg-msg {{ margin-top:8px; padding:8px 10px; border-left:3px solid var(--accent, var(--aqua)); border-radius:8px; background:rgba(127,127,127,.12); font-size:13px; font-weight:600; color:var(--ink); }}
.dg-note {{ margin-top:6px; font-size:11px; color:var(--muted); line-height:1.4; }}
</style>
</head>
<body class="view-hidden">
<div class="app">
  <header>
    <div class="brand"><a href="/" title="{MODEL_TAGLINE}" style="color:inherit;text-decoration:none;">{MODEL_NAME}</a> <small style="font-size:10px;opacity:.6;font-weight:500;letter-spacing:.04em;" title="SAILAB version">v{APP_VERSION}</small></div>
    <div id="geocoderSlot">
      <input id="geoInput" type="text" placeholder="Search for a place or a real Pakistan tehsil..." autocomplete="off">
      <div id="geoResults"></div>
    </div>
    <div class="layer-switch">
      <button id="layerSat" class="active" onclick="setMapLayer('satellite')">Satellite</button>
      <button id="layerStreets" onclick="setMapLayer('streets')">Streets</button>
      <button id="damsToggleBtn" class="phys-off" onclick="toggleDamsLayer()">Dams</button>
    </div>
    <!--MODENAV-->
    <div style="position:relative;flex:0 0 auto;"><button id="queueChip" onclick="toggleQueue()" title="Show running and queued jobs, and cancel them">Queue: idle</button><div id="queuePop"></div></div>
    <button id="themeToggle" onclick="toggleTheme()" title="Switch between dark and light mode" aria-label="Switch between dark and light mode">&#9680;</button>
    <div class="note phys-off" id="demSource"></div>
  </header>
  <div class="main">
    <div class="left-col">
      <div id="mapWrap">
        <div id="map" style="flex:1;"></div>
      </div>
      <div class="vresizer" id="vResizer"></div>
      <div class="view3d-wrap">
        <div class="view3d-tabs">
          <span style="flex:1;padding:8px 4px;color:var(--ink-2);font-size:12.5px;">Flood visualization (Mapbox 3D)</span>
          <button class="v3d-btn" id="viewExpandBtn" onclick="toggleViewMain()" title="Show the flood view on the whole screen">Expand</button>
          <button class="v3d-btn" id="viewHideBtn" onclick="window.viewUserHidden = true; setViewMode('hidden')" title="Hide this view">Hide</button>
          <a class="v3d-btn" id="v3dFloodMbFullBtn" style="display:none;text-decoration:none;" target="_blank" href="#">Full Page &#8599;</a>
        </div>
        <iframe id="view3dFrame" class="frame3d" allowfullscreen></iframe>
      </div>
    </div>
    <div class="resizer" id="resizerMain"></div>
    <button id="drawerToggle" onclick="toggleDrawer()" title="Show or hide the side panel" aria-label="Show or hide the side panel">&#8250;</button>
    <button id="viewShowBtn" onclick="window.viewUserHidden = false; setViewMode('card')">Flood view</button>
    <div class="right-col" id="rightCol" data-pane="scenario">
      <div class="tier-tabs ptabs" id="ptabs" role="tablist" aria-label="Sections"></div>


      <div class="panel-section" data-pane="scenario" data-modes="physics glof coastal">
        <h3>Area</h3>
        <p class="note">Draw a polygon on the map, or upload a shapefile (.zip) or GeoJSON.</p>
        <input type="file" id="uploadFile" accept=".zip,.geojson,.json">
        <button class="secondary" onclick="uploadShape()">Upload as area</button>
        <div id="areaInfo" style="display:none;">
          <div class="stat-row"><span>Area</span><span id="areaKm2">-</span></div>
        </div>
        <label class="phys-off" style="display:flex;align-items:center;gap:6px;margin-top:8px;font-weight:normal;">
          <input type="checkbox" id="useGlobalDem" style="width:auto;">
          Use open-source global DEM (Copernicus DEM GLO-30, AWS, free) for areas outside the downloaded Pakistan SRTM
        </label>
        <details style="margin:8px 0;"><summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Own elevation model or saved run</summary>
        <div style="margin-top:10px;">
          <label style="font-weight:600;">Your elevation model (GeoTIFF)</label>
          <input type="file" id="customDemFile" accept=".tif,.tiff" onchange="uploadCustomDem()" style="max-width:100%;">
          <button class="secondary" id="customDemClear" style="display:none;" onclick="clearCustomDem()">Remove my DEM</button>
          <div id="customDemBox"></div>
        </div>
        <div style="margin-top:10px;">
          <label style="font-weight:600;">Saved run file</label>
          <input type="file" id="runFileInput" accept=".json" onchange="loadRunFile()" style="max-width:100%;">
          <div id="runFileBox"></div>
        </div>
        </details>
        <div id="globalDemPrompt" style="display:none;">
          <p class="note" style="color:#d9822b;"></p>
        </div>
      </div>

      <div class="panel-section phys-off">
        <h3>National rainfall hotspots (discovery)</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <div style="flex:1;min-width:160px;">
            <label>Metric</label>
            <select id="hotspotMetric">
              <option value="rx1day" selected>RX1day - max 1-day rainfall</option>
              <option value="rx5day">RX5day - max 5-day rainfall</option>
              <option value="r95p">R95p - very-wet-day total</option>
              <option value="r99p">R99p - extremely-wet-day total</option>
              <option value="peakhr">Peak hourly intensity (ERA5-Land)</option>
              <option value="peak30">Peak 30-min intensity (IMERG)</option>
              <option value="trend">Trend on RX1day (per decade)</option>
              <option value="sdii">SDII - mean wet-day intensity</option>
              <option value="r20mm">R20mm - heavy-rain days</option>
              <option value="prcptot">PRCPTOT - total wet-day rain</option>
            </select>
          </div>
          <div style="flex:1;min-width:140px;">
            <label>Season</label>
            <select id="hotspotSeason">
              <option value="monsoon" selected>Monsoon (Jul-Sep)</option>
              <option value="pre">Pre-monsoon (Apr-Jun)</option>
              <option value="post">Post-monsoon (Oct-Nov)</option>
              <option value="all">Full year</option>
            </select>
          </div>
        </div>
        <button class="secondary" onclick="loadRainfallHotspots()">Show hotspots &rarr;</button>
        <div id="hotspotsBox"></div>
      </div>

      <div class="panel-section phys-off">
        <h3>River basins (HydroSHEDS discovery)</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <div style="flex:1;min-width:180px;">
            <label>Region</label>
            <select id="basinRegion">
              <option value="gilgit_baltistan" selected>Gilgit-Baltistan</option>
              <option value="khyber_pakhtunkhwa">Khyber Pakhtunkhwa</option>
              <option value="punjab">Punjab</option>
              <option value="sindh">Sindh</option>
              <option value="balochistan">Balochistan</option>
              <option value="azad_kashmir">Azad Kashmir</option>
            </select>
          </div>
        </div>
        <button class="secondary" onclick="loadBasins()">Show basins &rarr;</button>
        <div id="basinsBox"></div>
      </div>

      <div class="panel-section phys-off">
        <h3>Rainfall</h3>
        <div id="contextBox"><p class="note">Draw or upload an area above first.</p></div>
      </div>

      <div class="panel-section phys-off">
        <h3>Live Monitoring</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:8px;">
          <div><label>Threshold (mm/hr)</label><input type="number" id="watchThreshold" value="10" step="1" min="0.1" style="width:90px;"></div>
          <div><label>Check every</label>
            <select id="watchInterval">
              <option value="1">1 hour</option>
              <option value="3" selected>3 hours</option>
              <option value="6">6 hours</option>
              <option value="12">12 hours</option>
              <option value="24">24 hours</option>
            </select>
          </div>
          <div style="flex:1;min-width:220px;"><label>Webhook URL (optional)</label>
            <input type="text" id="watchWebhookUrl" placeholder="Slack/Discord/ntfy.sh webhook -- notified on threshold crossing" style="width:100%;"></div>
          <button class="secondary" id="watchAreaBtn" disabled onclick="watchCurrentArea()">Watch this area &rarr;</button>
        </div>
        <div id="watchListBox"><p class="note">No areas currently watched.</p></div>
      </div>

      <div class="tier-content phys-off" data-tier="2">
        <div class="panel-section">
          <h3>Tier 2 &middot; Instant flood preview (HAND)</h3>
          <div>
            <label>Discharge (m&sup3;/s)</label>
            <input type="number" id="instantDischarge" value="50" step="10" min="0" oninput="syncInstantDischargeSlider('number')">
            <input type="range" id="instantDischargeSlider" min="0" max="3" step="0.01" value="1" style="margin-top:6px;" oninput="syncInstantDischargeSlider('slider')">
            <p class="note" id="instantLibraryNote" style="margin-top:4px;">Drag the slider for an instant estimate (interpolated from a precomputed discharge library, no server call) -- click Preview below to fetch the real map overlay for the exact value.</p>
          </div>
          <button class="secondary" id="instantFloodBtn" disabled onclick="runInstantFlood()">Preview instant flood extent &rarr;</button>
          <div id="instantFloodBox"></div>
        </div>
      </div>

      <div class="tier-content" data-tier="3">
        <div class="panel-section" data-pane="scenario" data-modes="physics">
          <h3>Storm</h3>
          <label>Peak rainfall (mm/hr)</label>
          <input type="number" id="rainMmHr" value="40">
          <label>Duration (hr)</label>
          <input type="number" id="durationHr" value="2">
          <details style="margin:2px 0;">
            <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Design storm by return period</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
              <div>
                <label>Return period (years)</label>
                <select id="returnPeriod">
                  <option value="2">2</option><option value="5">5</option><option value="10">10</option>
                  <option value="25">25</option><option value="50">50</option>
                  <option value="100" selected>100</option><option value="200">200</option>
                </select>
              </div>
              <div>
                <label>Climate scenario</label>
                <select id="climateScenario">
                  <option value="current">Current climate</option>
                  <option value="ssp245|2050 (mid-century)">SSP2-4.5, ~2050</option>
                  <option value="ssp585|2050 (mid-century)">SSP5-8.5, ~2050</option>
                  <option value="ssp245|2085 (end-of-century)">SSP2-4.5, ~2085</option>
                  <option value="ssp585|2085 (end-of-century)">SSP5-8.5, ~2085</option>
                </select>
              </div>
              <button class="secondary" id="designStormBtn" disabled onclick="runDesignStorm()">Fetch real design storm &rarr;</button>
              <div id="designStormBox"></div>
            </div>
          </details>
          <details style="margin:2px 0;">
            <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Recorded rainfall curve (IDF)</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
              <div>
                <label>Duration (hr)</label>
                <input type="number" id="idfDurationHr" value="2" step="0.5" min="0.5" max="24">
              </div>
              <div>
                <label>Season</label>
                <select id="idfSeason">
                  <option value="monsoon" selected>Monsoon (Jul-Sep)</option>
                  <option value="pre">Pre-monsoon (Apr-Jun)</option>
                  <option value="post">Post-monsoon (Oct-Nov)</option>
                  <option value="all">Full year</option>
                </select>
              </div>
              <button class="secondary" id="idfDesignBtn" disabled onclick="runIdfDesignStorm()">Use real recorded intensity &rarr;</button>
              <div id="idfDesignBox"></div>
            </div>
          </details>
          <details style="margin:2px 0;">
            <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Upload rain-gauge data (CSV)</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
              <input type="file" id="rainfallCsvFile" accept=".csv" onchange="uploadRainfallCsv()">
              <div id="rainfallCsvBox"></div>
            </div>
          </details>
        </div>

        <div class="panel-section" data-pane="scenario" data-modes="glof">
          <h3>GLOF scenario</h3>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <div>
                    <label>Source</label>
                    <select id="glofSource" onchange="updateGlofInputs()">
                      <option value="shishper_2022">Shishper Glacier Lake (Hassanabad, Hunza) -- real 2022 event</option>
                      <option value="hydrolakes">Real HydroLAKES lake near this AOI (any Pakistan lake, auto-detected)</option>
                      <option value="pmd">PMD 2024 glacial-lake inventory lake near this AOI (dam type + nearest village)</option>
                      <option value="volume">Custom: I know the lake's real volume (m&sup3;)</option>
                      <option value="area">Custom: I only know the lake's real area (km&sup2;, less reliable)</option>
                    </select>
                  </div>
                  <div id="glofVolumeRow" style="display:none;">
                    <label>Lake volume (m&sup3;)</label>
                    <input type="number" id="glofVolumeM3" step="1000" min="0">
                  </div>
                  <div id="glofAreaRow" style="display:none;">
                    <label>Lake area (km&sup2;)</label>
                    <input type="number" id="glofAreaKm2" step="0.01" min="0">
                  </div>
                  <div id="glofTriggerRow" style="display:none;">
                    <label>Trigger / breach mechanism</label>
                    <select id="glofTrigger" onchange="document.getElementById('glofBreachHeightRow').style.display=(this.value==='landslide_breach'?'block':'none');">
                      <option value="moraine_breach">Gradual moraine failure (Popov 1991)</option>
                      <option value="landslide_breach">Landslide / rock-ice avalanche impact (Froehlich 1995 breach discharge)</option>
                    </select>
                  </div>
                  <div id="glofBreachHeightRow" style="display:none;">
                    <label>Real breach / dam height (m)</label>
                    <input type="number" id="glofBreachHeightM" step="1" min="1" value="20">
                  </div>
                  <div id="glofHydrolakesRow" style="display:none;">
                    <button class="secondary" id="glofFindLakesBtn" onclick="findNearbyHydrolakes()">Find nearby real lakes &rarr;</button>
                    <div id="glofHydrolakesResult"></div>
                    <select id="glofHydrolakesPicker" style="display:none;margin-top:6px;"></select>
                  </div>
                  <div id="glofPmdRow" style="display:none;">
                    <button class="secondary" id="glofFindPmdBtn" onclick="findNearbyPmdLakes()">Find nearby inventory lakes &rarr;</button>
                    <div id="glofPmdResult"></div>
                    <select id="glofPmdPicker" style="display:none;margin-top:6px;"></select>
                  </div>
                  <button class="secondary" id="glofBtn" disabled onclick="applyGlofScenario()">Apply GLOF discharge &rarr;</button>
                  <div id="glofBox"></div>
                  <hr style="margin:10px 0;border-color:#2a3a3e;">
                  <label>Documented outbursts near this area</label>
                  <button class="secondary" id="glofEventsBtn" disabled onclick="showGlofHistoricalEvents()">Show documented events &rarr;</button>
                  <div id="glofEventsBox"></div>
                </div>
                      </div>

        <div class="panel-section" data-pane="scenario" data-modes="physics glof">
          <h3>Boundary conditions</h3>
        <button class="secondary" id="detectBoundariesBtn" disabled onclick="detectAllBoundaries()">Detect rivers automatically</button>
        <p class="note" id="boundaryDetectNote"></p>
        <div id="boundaryList"></div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="secondary" onclick="addManualBoundary('inflow')">+ Add manual inflow</button>
          <button class="secondary" onclick="addManualBoundary('outflow')">+ Add manual outflow</button>
        </div>
        </div>

        <div class="panel-section" data-pane="scenario" data-modes="physics glof">
          <details style="margin:2px 0;">
            <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Advanced settings</summary>
            <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px;">
              <div>
                <label>Fallback friction (Manning's n)</label>
                <input type="number" id="friction" value="0.05" step="0.005" min="0.01" title="Floodplain friction is now determined per-cell automatically from real ESA WorldCover land cover (forest, cropland, built-up, water, etc. each get their own real published roughness value) as the model runs. This value is only used as a fallback for any cell the land-cover data doesn't cover, or if the land-cover fetch fails. LISFLOOD-FP's ACC solver can become numerically unstable at very low Manning's friction -- the backend also enforces a floor and adds numerical diffusion (theta) if you get close to it.">
              </div>
              <div>
                <label>CFL number (inertial solver stability, 0-1)</label>
                <input type="number" id="cfl" value="0.7" step="0.05" min="0.05" max="1">
              </div>

              <details class="phys-off" style="margin:2px 0;">
                <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Dam spillway release (real historical event)</summary>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <div>
                    <label>Event</label>
                    <select id="spillwayEventSelect"></select>
                  </div>
                  <div id="spillwayEventDetail"></div>
                  <button class="secondary" onclick="checkSpillwayOutlook()">Forecast outlook for this dam &rarr;</button>
                  <div id="spillwayOutlookBox"></div>
                  <button class="secondary" id="spillwayBtn" disabled onclick="addSpillwayEventAsInflow()">Add as real inflow &rarr;</button>
                  <div id="spillwayBox"></div>
                </div>
              </details>
              <details class="phys-off" style="margin:2px 0;">
                <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Dam failure scenario (what if a real dam breached)</summary>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <div><label>Dam</label><select id="damFailureSelect"></select></div>
                  <div id="damFailureDetail"></div>
                  <button class="secondary" id="damFailureBtn" disabled onclick="runDamFailure()">Compute failure flood &amp; add as inflow &rarr;</button>
                  <div id="damFailureBox"></div>
                </div>
              </details>
              <details class="phys-off" style="margin:2px 0;">
                <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Snowmelt contribution (SRM, real temperature + elevation)</summary>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <div>
                    <label>Snow line elevation (m)</label>
                    <input type="number" id="srmSnowLine" step="50" value="4500">
                  </div>
                  <div>
                    <label>Degree-day factor (mm/&deg;C/day)</label>
                    <input type="number" id="srmDdf" step="0.1" value="4.5">
                  </div>
                  <div>
                    <label>Lapse rate (&deg;C/km)</label>
                    <input type="number" id="srmLapseRate" step="0.1" value="6.5">
                  </div>
                  <button class="secondary" id="srmBtn" disabled onclick="computeSnowmelt()">Compute real snowmelt discharge &rarr;</button>
                  <div id="srmBox"></div>
                  <div>
                    <label>Forecast days (multi-day melt curve)</label>
                    <input type="number" id="srmDays" min="1" max="16" step="1" value="7">
                  </div>
                  <button class="secondary" id="srmHydroBtn" disabled onclick="computeSnowmeltHydrograph()">Compute multi-day melt hydrograph (real forecast) &rarr;</button>
                  <div id="srmHydroBox"></div>
                </div>
              </details>
              <details class="phys-off" style="margin:2px 0;">
                <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Snow &amp; glacier melt model (distributed, ERA5 reanalysis, whole basin)</summary>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <div><label>Start</label><input type="date" id="meltStart" value="2024-10-01"></div>
                  <div><label>End</label><input type="date" id="meltEnd" value="2025-10-31"></div>
                  <div><label>Lapse rate (&deg;C/km)</label><input type="number" id="meltLapse" step="0.1" value="6.5"></div>
                  <div><label>Precipitation factor (ERA5 under-catch; 1 = none)</label><input type="number" id="meltPrecip" step="0.1" min="0.5" max="6" value="1"></div>
                  <div><label>Degree-day factor, snow (mm/&deg;C/day)</label><input type="number" id="meltDdfSnow" step="0.5" min="1" max="12" value="4"></div>
                  <div><label>Degree-day factor, ice (mm/&deg;C/day)</label><input type="number" id="meltDdfIce" step="0.5" min="2" max="25" value="8"></div>
                  <div><label>Temperature offset added to ERA5 (&deg;C)</label><input type="number" id="meltTempBias" step="0.5" min="-6" max="6" value="0"></div>
                  <button class="secondary" id="meltBtn" disabled onclick="runMeltModel()">Run snow &amp; glacier melt model &rarr;</button>
                  <button class="secondary" id="meltCalBtn" disabled onclick="calibrateMeltModel()">Calibrate this basin against GloFAS (about 10 minutes)</button>
                  <div id="meltCalBox"></div>
                  <div id="meltBox"></div>
                </div>
              </details>


              <div class="panel-section" style="border:1px solid var(--line);border-radius:8px;padding:10px;margin:4px 0;">
                <h3 style="margin-top:0;">Storm centering &amp; Areal Reduction Factor</h3>
                <label style="display:flex;align-items:center;gap:6px;font-weight:normal;">
                  <input type="checkbox" id="enableArf" style="width:auto;">
                  Apply Areal Reduction Factor (real Keers &amp; Wescott 1977 / FSR-FEH formula)
                </label>
                <label style="display:flex;align-items:center;gap:6px;font-weight:normal;margin-top:8px;">
                  <input type="checkbox" id="enableStormCenter" style="width:auto;" onchange="updateStormCenterFields()">
                  Real storm centering (exponential spatial decay)
                </label>
                <div id="stormCenterFields" style="display:none;">
                  <label>Center longitude</label>
                  <input type="number" id="stormCenterLon" step="0.0001">
                  <label>Center latitude</label>
                  <input type="number" id="stormCenterLat" step="0.0001">
                  <button class="secondary" type="button" onclick="useAoiCentroidForStorm()">Use AOI centroid</button>
                  <label style="display:flex;align-items:center;gap:6px;font-weight:normal;margin-top:8px;">
                    <input type="checkbox" id="predictStormMotion" style="width:auto;">
                    Predict storm motion (real LSTM, Pakistan monsoon)
                  </label>
                </div>
              </div>
              <div>
                <label style="display:flex;align-items:center;gap:6px;font-weight:normal;">
                  <input type="checkbox" id="useChannelConditioning" style="width:auto;">
                  Condition real river channel depth into the DEM (pseudo-SGC, GPU-compatible)
                </label>
                <p class="note" title="LISFLOOD-FP's real Sub-Grid Channel (SGC) solver has zero support on the GPU/CUDA path this app uses (confirmed from source). This instead burns a real channel depth -- sized via Manning's equation for this run's own largest peak inflow, same technique as Tier 2's HAND rating curve -- directly into the DEM along real OSM waterway centerlines before the GPU run, so the river has real conveyance capacity instead of none. A real approximation, not true sub-grid channel physics: whole grid cells are deepened, not a channel narrower than one cell; depth comes from a width/Manning's regression, not a surveyed cross-section.">Only meaningful with at least one real inflow above -- see tooltip.</p>
              </div>
            </div>
          </details>
        </div>

        <div class="panel-section" data-pane="glacier" data-modes="glof">
          <h3>Glacier monitoring</h3>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <button class="secondary" id="glacierBaselineBtn" disabled onclick="runGlacierBaseline()">Get real glacier inventory (GLIMS) &rarr;</button>
                  <div id="glacierBaselineBox"></div>
                  <div>
                    <label>Mark a known real GLOF lake on the result (optional)</label>
                    <select id="glacierLakeKey">
                      <option value="">None</option>
                      <option value="shishper_2022">Shishper Glacier Lake (Hassanabad, Hunza)</option>
                    </select>
                  </div>
                  <div>
                    <label>Real confidence threshold (lower = more coverage, more noise)</label>
                    <input type="number" id="glacierConfidence" value="0.5" step="0.05" min="0.1" max="0.95">
                  </div>
                  <button class="secondary" id="glacierDispBtn" disabled onclick="runGlacierDisplacement()">Run SAR displacement analysis (Jan-Aug 2026, real, ~2-12 min) &rarr;</button>
                  <div id="glacierDispBox"></div>
                  <hr style="margin:10px 0;border-color:#2a3a3e;">
                  <button class="secondary" id="itsliveBtn" disabled onclick="runItsliveVelocity()">Check recent glacier speed (ITS_LIVE) &rarr;</button>
                  <div id="itsliveBox"></div>
                </div>
                      </div>

        <div class="panel-section" data-pane="glacier" data-modes="glof">
          <h3>Geomorphology</h3>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">
                  <button class="secondary" id="geomorphBtn" disabled onclick="computeGeomorphology()">Check real geomorphology &amp; susceptibility &rarr;</button>
                  <div id="geomorphBox"></div>
                </div>
                      </div>

        <div class="panel-section" data-pane="scenario" data-modes="physics">
          <h3>Soil &amp; infiltration</h3>
          <div style="display:flex;flex-direction:column;gap:10px;">
      <div>
        <label>Infiltration rate (mm/hr)</label>
        <input type="number" id="infiltrationMmHr" value="0" step="1" min="0">
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;font-weight:normal;">
          <input type="checkbox" id="useSoilInfiltration" style="width:auto;" onchange="if(this.checked)document.getElementById('useGreenAmpt').checked=false;">
          Use soil-derived rate (SoilGrids)
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;font-weight:normal;">
          <input type="checkbox" id="useGreenAmpt" style="width:auto;" onchange="if(this.checked)document.getElementById('useSoilInfiltration').checked=false;">
          Use Green-Ampt infiltration
        </label>
      </div>
      <div>
        <label>Soil moisture before the storm</label>
        <input type="range" id="amc" min="0" max="100" step="1" value="50" oninput="updateAmcLabel()" style="width:100%;">
        <div class="stat-row" style="margin-top:2px;">
          <span class="note">Dry</span>
          <span class="note" id="amcLabel">Average (AMC-II) &middot; 50/100</span>
          <span class="note">Wet</span>
        </div>
      </div>
          </div>
        </div>

        <div class="panel-section" data-pane="scenario" data-modes="physics glof">
          <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="includeBuildings" checked style="width:auto;"> Include real buildings (affects flow)</label>
          <button class="action" id="runBtn" disabled onclick="runModel()">Run flood model</button>
          <p class="note" id="statusNote"></p>
        </div>

        <div class="panel-section" data-pane="runs" data-modes="physics glof">
          <h3>Run status</h3>
          <div id="runStatusBox">
            <p class="note">Draw/upload an area and run the model above first.</p>
          </div>
        </div>

        <div class="panel-section" data-pane="runs" data-modes="physics">
          <h3>Return-period runs</h3>
          <div id="batchPeriodChecks" style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0;">
            <label style="font-weight:normal;"><input type="checkbox" value="2" style="width:auto;"> 2yr</label>
            <label style="font-weight:normal;"><input type="checkbox" value="5" style="width:auto;"> 5yr</label>
            <label style="font-weight:normal;"><input type="checkbox" value="10" checked style="width:auto;"> 10yr</label>
            <label style="font-weight:normal;"><input type="checkbox" value="25" style="width:auto;"> 25yr</label>
            <label style="font-weight:normal;"><input type="checkbox" value="50" checked style="width:auto;"> 50yr</label>
            <label style="font-weight:normal;"><input type="checkbox" value="100" checked style="width:auto;"> 100yr</label>
            <label style="font-weight:normal;"><input type="checkbox" value="200" style="width:auto;"> 200yr</label>
          </div>
          <button class="secondary" id="batchRunBtn" disabled onclick="runBatchReturnPeriods()">Run all selected return periods &rarr;</button>
          <div id="batchRunBox"></div>
        </div>

        <div class="panel-section" data-pane="runs" data-modes="physics">
          <h3>Average annual loss</h3>
          <button class="secondary" id="aalRunBtn" disabled onclick="runAALCompute()">Compute Average Annual Loss &rarr;</button>
          <div id="aalRunBox"></div>
        </div>

        <div class="panel-section" data-pane="runs" data-modes="physics">
          <h3>Compare two runs</h3>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <div style="flex:1;min-width:140px;"><label>Run A id</label><input type="text" id="compareRunA" placeholder="run id"></div>
            <div style="flex:1;min-width:140px;"><label>Run B id</label><input type="text" id="compareRunB" placeholder="run id"></div>
          </div>
          <button class="secondary" onclick="compareRuns()">Compare &rarr;</button>
          <div id="compareRunsBox"></div>
        </div>

        <div class="panel-section" data-pane="impact" data-modes="physics glof">
          <h3>Buildings &amp; infrastructure</h3>
          <div id="infraBox"><p class="note">Run the flood model first.</p></div>
        </div>

        <div class="panel-section" data-pane="impact" data-modes="physics glof">
          <h3>Damage</h3>
          <div id="damageBox"><p class="note">Run the flood model first.</p></div>
        </div>

        <div class="panel-section" data-pane="impact" data-modes="physics">
          <h3>Sediment</h3>
          <div>
            <label style="display:flex;align-items:center;gap:6px;font-weight:normal;">
              <input type="checkbox" id="sedimentD50Auto" checked style="width:auto;" onchange="updateSedimentD50Mode()">
              Use real soil-derived grain size (SoilGrids, Pakistan only)
            </label>
            <p class="note" style="margin-top:4px;" id="sedimentD50AutoNote">Taken from SoilGrids sand/clay for this area.</p>
            <label style="margin-top:6px;">Median grain size, d50 (mm)</label>
            <input type="number" id="sedimentD50" value="2.0" step="0.1" min="0.1" max="200" disabled>
          </div>
          <div id="sedimentBox"><p class="note">Run the flood model first.</p></div>
        </div>

        <div class="panel-section" data-pane="mitigate" data-modes="physics">
          <h3>Nature-based solutions</h3>
          <div>
            <label>Intervention</label>
            <select id="nbsIntervention">
              <option value="reforestation">Reforestation / afforestation</option>
              <option value="wetland_restoration">Wetland restoration</option>
              <option value="grassland_restoration">Grassland / rangeland restoration</option>
            </select>
          </div>
          <div>
            <label>Pathway</label>
            <select id="nbsPathway">
              <option value="active">Active (full conversion)</option>
              <option value="passive">Passive (one improvement step)</option>
            </select>
          </div>
          <button class="secondary" id="nbsBtn" disabled onclick="runNbsScenario()">Run NbS scenario &rarr;</button>
          <div id="nbsBox"></div>
          <button class="secondary" id="nbsRecommendBtn" disabled onclick="runNbsRecommend()">Auto-recommend best NbS option &rarr;</button>
          <div id="nbsRecommendBox"></div>
        </div>

        <div class="panel-section" data-pane="mitigate" data-modes="physics">
          <h3>Protection structures</h3>
          <div id="protectStructList"><p class="note">Draw a line on the map (line tool, top-left) to add a structure.</p></div>
          <button class="secondary" id="protectRunBtn" disabled onclick="runProtectionScenario()">Run protection scenario &rarr;</button>
          <div id="protectBox"></div>

          <div style="margin-top:14px;border-top:1px solid var(--line-soft);padding-top:12px;">
            <label style="font-weight:600;">Or find the best placement automatically</label>
            <label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input type="checkbox" class="protectOptType" value="dam" checked style="width:auto;">Dam / embankment</label>
            <label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input type="checkbox" class="protectOptType" value="canal" checked style="width:auto;">Diversion canal</label>
            <label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input type="checkbox" class="protectOptType" value="barrage" style="width:auto;">Barrage</label>
            <label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input type="checkbox" class="protectOptType" value="drainage" style="width:auto;">Drainage pipe</label>
            <label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input type="checkbox" class="protectOptType" value="catchment" style="width:auto;">Rainwater catchment / retention basin</label>
          </div>
          <div>
            <label>Number of structures</label>
            <select id="protectOptN"><option value="1">1</option><option value="2">2</option><option value="3">3</option></select>
          </div>
          <div>
            <label>Search effort</label>
            <select id="protectOptEffort">
              <option value="12">Quick (~12 real trials)</option>
              <option value="24">Balanced (~24 real trials)</option>
              <option value="48">Thorough (~48 real trials)</option>
            </select>
          </div>
          <button class="secondary" id="protectOptBtn" disabled onclick="runProtectionOptimize()">Auto-optimize placement &rarr;</button>
          <div id="protectOptBox"></div>
        </div>
      </div>

      <div class="tier-content" data-tier="4">
        <div class="panel-section" data-pane="scenario" data-modes="coastal">
          <h3>Coastal / tidal flood (static inundation)</h3>
          <div>
            <label>Water-level source</label>
            <select id="coastalSource" onchange="updateCoastalSourceUI()">
              <option value="khan" selected>Karachi tide gauge (Khan et al. 2020) &mdash; 50/100/1000 yr</option>
              <option value="gtsm">GTSM-ERA5-E, nearest of 42 coastal stations &mdash; 2 to 100 yr</option>
            </select>
            <p class="note" id="coastalSourceNote"></p>
          </div>
          <div>
            <label>Return period (years)</label>
            <select id="coastalReturnPeriod">
              <option value="2">2</option><option value="5">5</option><option value="10">10</option><option value="25">25</option>
              <option value="50">50</option><option value="100" selected>100</option><option value="1000">1000</option>
            </select>
          </div>
          <div>
            <label>Or use a documented historical event instead</label>
            <select id="coastalSurgePreset">
              <option value="">(use return period above)</option>
              <option value="2.0">Cyclone Biparjoy 2023 (observed, ~2.0 m)</option>
              <option value="3.25">PMD "high surge risk" threshold (~3.25 m)</option>
              <option value="4.0">Cyclone 2A 1999 (modeled, unvalidated, ~4.0 m)</option>
            </select>
          </div>
          <div>
            <label>Project to future year (adds real Karachi SLR trend, 3.6mm/yr -- optional)</label>
            <input type="number" id="coastalTargetYear" placeholder="e.g. 2050" min="2024" max="2100">
          </div>
          <div>
            <label>Add sea-level rise (m) &mdash; scenario value, not a forecast</label>
            <select id="coastalSlrPreset" onchange="document.getElementById('coastalSlr').value=this.value">
              <option value="0">None (present day)</option><option value="0.25">+0.25 m</option><option value="0.5">+0.5 m</option>
              <option value="0.75">+0.75 m</option><option value="1.0">+1.0 m</option>
            </select>
            <input type="number" id="coastalSlr" value="0" min="0" max="3" step="0.05" style="margin-top:4px;">
          </div>
          <div>
            <label><input type="checkbox" id="includeWaveRunup" style="width:auto;margin-right:6px;">Add real wave run-up (Open-Meteo Marine API + Stockdon et al. 2006)</label>
          </div>
          <button class="secondary" id="coastalBtn" disabled onclick="runCoastalFlood()">Run coastal flood analysis &rarr;</button>
          <div id="coastalBox"></div>

          <hr style="margin:16px 0;border-color:#2a3a3e;">
          <h3>Water-level explorer (instant)</h3>
          <input type="range" id="coastalLevelSlider" min="0" max="8" step="0.05" value="2" disabled oninput="syncCoastalLevel()">
          <div id="coastalLevelBox"><p class="note">Set an area on the coast to load this.</p></div>
          <button class="secondary" id="coastalLevelMapBtn" disabled onclick="showCoastalLevelOnMap()">Show this level on map &rarr;</button>

          <hr style="margin:16px 0;border-color:#2a3a3e;">
          <h3>Coastal susceptibility map</h3>
          <button class="secondary" id="coastalSuscBtn" disabled onclick="runCoastalSusceptibility()">Compute susceptibility &rarr;</button>
          <div id="coastalSuscBox"></div>

        </div>
        <div class="panel-section" data-pane="surge" data-modes="coastal">
          <h3>Fast animated preview (approximate)</h3>
          <div>
            <label>Anchor magnitude to</label>
            <select id="coastalPreviewAnchor">
              <option value="static" selected>Static method's total water level (above)</option>
              <option value="gtsr">Real GTSR station's own recorded historical peak surge</option>
            </select>
          </div>
          <div>
            <label for="coastalPreviewTide">Astronomical tide (Moon and Sun)</label>
            <select id="coastalPreviewTide">
              <option value="" selected>Off</option>
              <option value="spring_high">Spring high water at the surge peak (worst case)</option>
              <option value="typical_high">Ordinary high water at the surge peak</option>
              <option value="low">Low water at the surge peak (best case)</option>
            </select>
          </div>
          <button class="secondary" id="coastalPreviewBtn" disabled onclick="runCoastalFloodPreview()">Preview animated coastal flood &rarr;</button>
          <div id="coastalPreviewBox"></div>

          <hr style="margin:16px 0;border-color:#2a3a3e;">
          <h3>Real dynamic surge (GTSR, experimental)</h3>
          <div>
            <label>Anchor magnitude to</label>
            <select id="coastalDynAnchor" onchange="updateCombineTideVisibility()">
              <option value="static" selected>Static method's total water level (above)</option>
              <option value="gtsr">Real GTSR station's own recorded historical peak surge</option>
            </select>
          </div>
          <div>
            <label for="coastalDynTide">Astronomical tide (Moon and Sun)</label>
            <select id="coastalDynTide">
              <option value="" selected>Off</option>
              <option value="spring_high">Spring high water at the surge peak (worst case)</option>
              <option value="typical_high">Ordinary high water at the surge peak</option>
              <option value="low">Low water at the surge peak (best case)</option>
            </select>
          </div>
          <div id="combineTideRow" style="display:none;">
            <label><input type="checkbox" id="combineTide" style="width:auto;margin-right:6px;">Add real mean tidal range (Karachi Port, 2.3 m, Khan et al. 2020) on top of the surge peak</label>
          </div>
          <div>
            <label><input type="checkbox" id="includeWaveRunupDyn" style="width:auto;margin-right:6px;">Add real wave run-up (Open-Meteo Marine API + Stockdon et al. 2006)</label>
          </div>
          <div>
            <label>Real GTSR episode window (longer = more of the real recede tail, but real hours slower)</label>
            <select id="coastalDynWindow">
              <option value="2">2 days (fastest, rise+peak only)</option>
              <option value="3" selected>3 days (rise/peak/partial recede)</option>
              <option value="5">5 days (fullest real recede tail, slowest)</option>
            </select>
          </div>
          <div>
            <label><input type="checkbox" id="coastalCompound" style="width:auto;margin-right:6px;" onchange="document.getElementById('coastalCompoundRow').style.display=this.checked?'block':'none';">Add real rainfall at the same time (compound flooding / ocean backwater)</label>
          </div>
          <div id="coastalCompoundRow" style="display:none;">
            <label>Rainfall during this event (mm/hr)</label>
            <input type="number" id="coastalCompoundRain" value="20" min="0" step="1">
            <label>Duration (hr)</label>
            <input type="number" id="coastalCompoundDuration" value="3" min="0.5" step="0.5">
          </div>
          <button class="secondary" id="coastalDynBtn" disabled onclick="runCoastalFloodDynamic()">Run dynamic surge (real physics) &rarr;</button>
          <div id="coastalDynBox"></div>
        </div>
      </div>

    </div>
  </div>
</div>


<div id="welcomeOverlay" role="dialog" aria-modal="true" aria-labelledby="wTitle" style="display:none;">
  <div class="wcard">
    <button class="wx" onclick="closeWelcome()" aria-label="Close">&times;</button>
    <div class="wl">
      <h2>{MODEL_NAME}</h2>
      <ul>
        <li><b>Physical flood model</b>The full GPU solver: depth and speed through the storm, not just a snapshot.</li>
        <li><b>Impact and loss</b>Buildings, roads and facilities at risk, with loss in PKR.</li>
        <li><b>Scenarios</b>Coastal, glacier-lake, dam and protection options, and live monitoring.</li>
      </ul>
    </div>
    <div class="wr">
      <h3>Get flood information<br>for <em>your area</em></h3>
      <button class="action" onclick="closeWelcome(); document.getElementById('geoInput').focus();">Search a place</button>
      <button class="secondary" onclick="closeWelcome(); startDrawArea();">Draw an area on the map</button>
      <button class="secondary" onclick="closeWelcome(); document.getElementById('uploadFile').scrollIntoView({{behavior:'smooth', block:'center'}});">Upload an area file</button>
      <p class="note">Draw or choose an area, set the storm, then run the flood model. Results appear on the main screen.</p>
    </div>
  </div>
</div>
<script src="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.js"></script>
<script src="https://unpkg.com/@mapbox/mapbox-gl-draw@1.4.3/dist/mapbox-gl-draw.js"></script>
<script>
const MAPBOX_TOKEN = {json.dumps(MAPBOX_TOKEN)};

// ---- draggable resizers: main (map+3D | right panel) and vertical (map | 3D) ----
function initResizerX(resizerId, targetId, containerSel) {{
  const resizer = document.getElementById(resizerId), target = document.getElementById(targetId);
  let dragging = false;
  resizer.addEventListener('mousedown', () => {{ dragging = true; resizer.classList.add('active'); document.body.style.userSelect = 'none'; }});
  window.addEventListener('mousemove', (e) => {{
    if (!dragging) return;
    const rect = target.parentElement.getBoundingClientRect();
    const newWidth = Math.min(Math.max(rect.right - e.clientX, 280), rect.width - 320);
    target.style.width = newWidth + 'px';
    if (map) map.resize();
  }});
  window.addEventListener('mouseup', () => {{ dragging = false; resizer.classList.remove('active'); document.body.style.userSelect = ''; }});
}}
function initResizerY(resizerId, topId) {{
  const resizer = document.getElementById(resizerId), top = document.getElementById(topId);
  let dragging = false;
  resizer.addEventListener('mousedown', () => {{ dragging = true; resizer.classList.add('active'); document.body.style.userSelect = 'none'; }});
  window.addEventListener('mousemove', (e) => {{
    if (!dragging) return;
    const rect = top.parentElement.getBoundingClientRect();
    const newHeight = Math.min(Math.max(e.clientY - rect.top, 100), rect.height - 100);
    top.style.flex = 'none';
    top.style.height = newHeight + 'px';
    if (map) map.resize();
  }});
  window.addEventListener('mouseup', () => {{ dragging = false; resizer.classList.remove('active'); document.body.style.userSelect = ''; }});
}}
initResizerX('resizerMain', 'rightCol');
initResizerY('vResizer', 'mapWrap');

// ---- map-first layout helpers ----
var queueTimer = null;
function toggleQueue() {{ document.getElementById('queuePop').classList.toggle('open'); refreshQueue(); }}
function fmtAge(sec) {{ if (sec == null) return ''; return sec < 90 ? sec + ' s' : Math.round(sec / 60) + ' min'; }}
function queueRowHtml(kind, id, title, sub) {{
  return '<div class="qrow"><div class="qmeta"><div>' + title + '</div><div class="qsub">' + sub + '</div></div>' +
    '<button onclick="cancelQueued(\\'' + kind + '\\',\\'' + id + '\\',this)">Cancel</button></div>';
}}
function refreshQueue() {{
  fetch('/solver-status').then(r => r.json()).then(d => {{
    const runs = d.runs || [], jobs = d.jobs || [];
    const n = runs.length + jobs.length;
    const chip = document.getElementById('queueChip');
    chip.textContent = n ? 'Queue: ' + n + ' job' + (n > 1 ? 's' : '') : 'Queue: idle';
    chip.classList.toggle('busy', n > 0);
    const pop = document.getElementById('queuePop');
    if (!pop.classList.contains('open')) return;
    let h = '<h4>Solver</h4>';
    h += d.holder ? '<div class="qempty">Running now: ' + d.holder.label + ' (' + fmtAge(Math.round(d.holder.running_s || 0)) + ')</div>' : '<div class="qempty">Idle</div>';
    h += '<h4>Flood runs</h4>';
    if (!runs.length) h += '<div class="qempty">None</div>';
    runs.forEach(r => {{
      const state = r.status === 'queued' ? 'waiting' + (r.position ? ', position ' + r.position : '') : (r.status === 'postprocessing' ? 'processing results' : 'running');
      h += queueRowHtml('run', r.id, 'Run ' + r.id.slice(0, 8), state + (r.age_s != null ? ' &middot; ' + fmtAge(r.age_s) : ''));
    }});
    h += '<h4>Other jobs</h4>';
    if (!jobs.length) h += '<div class="qempty">None</div>';
    jobs.forEach(j => h += queueRowHtml('job', j.id, (j.label || 'Background job') + ' ' + j.id.slice(0, 8), 'running or waiting for the solver'));
    pop.innerHTML = h;
  }}).catch(() => {{}});
}}
function cancelQueued(kind, id, btn) {{
  if (!confirm('Cancel this ' + (kind === 'run' ? 'flood run' : 'job') + '?')) return;
  btn.disabled = true;
  fetch((kind === 'run' ? '/runs/' : '/jobs/') + id + '/cancel', {{method:'POST'}}).then(r => r.json()).then(d => {{
    if (d.error) alert(d.error);
    refreshQueue();
  }});
}}
document.addEventListener('click', e => {{ const pop = document.getElementById('queuePop'); if (pop && !e.target.closest('#queueChip') && !e.target.closest('#queuePop')) pop.classList.remove('open'); }});
queueTimer = setInterval(refreshQueue, 5000); setTimeout(refreshQueue, 800);
function applyTheme(t) {{ document.documentElement.setAttribute('data-theme', t);
  var b = document.getElementById('themeToggle'); if (b) b.innerHTML = t === 'light' ? '&#9790;' : '&#9728;'; }}
function toggleTheme() {{ var t = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  applyTheme(t); try {{ localStorage.setItem('sailabTheme', t); }} catch (e) {{}} }}
(function () {{ var t = 'dark'; try {{ t = localStorage.getItem('sailabTheme') || 'dark'; }} catch (e) {{}} applyTheme(t); }})();
function toggleDrawer() {{ document.body.classList.toggle('drawer-closed'); setTimeout(() => {{ if (map) map.resize(); }}, 320); }}
function setViewMode(m) {{
  document.body.classList.toggle('view-main', m === 'main');
  document.body.classList.toggle('view-hidden', m === 'hidden');
  const b = document.getElementById('viewExpandBtn'); if (b) b.textContent = (m === 'main') ? 'Shrink' : 'Expand';
}}
function toggleViewMain() {{ setViewMode(document.body.classList.contains('view-main') ? 'card' : 'main'); }}
function startDrawArea() {{ try {{ draw.changeMode('draw_polygon'); }} catch (e) {{}} }}
function closeWelcome() {{ document.getElementById('welcomeOverlay').style.display = 'none'; try {{ sessionStorage.setItem('sailabWelcome', '1'); }} catch (e) {{}} }}
(function () {{
  let seen = false; try {{ seen = !!sessionStorage.getItem('sailabWelcome'); }} catch (e) {{}}
  if (!seen && !/import=/.test(location.search)) document.getElementById('welcomeOverlay').style.display = 'flex';
  window.addEventListener('load', () => {{ if (typeof map !== 'undefined' && map) map.resize(); }});
}})();

const MAP_STYLES = {{
  // "Globe" reuses the SAME real satellite imagery as "Satellite" -- the
  // only difference is projection (globe + a dark space backdrop while
  // zoomed out to whole-Earth, vs flat mercator once zoomed in to trace a
  // precise polygon). Real Earth imagery, not a plain vector globe.
  satellite: 'mapbox://styles/mapbox/satellite-streets-v12',
  streets: 'mapbox://styles/mapbox/streets-v12',
}};
function applyGlobeFog() {{
  map.setFog({{
    color: 'rgb(20,26,38)', 'high-color': 'rgb(10,14,24)',
    'horizon-blend': 0.03, 'space-color': 'rgb(3,5,10)', 'star-intensity': 0.4,
  }});
}}
function setMapLayer(kind) {{
  document.getElementById('layerSat').classList.toggle('active', kind === 'satellite');
  document.getElementById('layerStreets').classList.toggle('active', kind === 'streets');
  const nextStyle = kind === 'streets' ? MAP_STYLES.streets : MAP_STYLES.satellite;
  // Both remaining options are for zoomed-in, precise polygon drawing --
  // flat mercator, not the globe (the globe+fog view is only ever the
  // page's own initial load state, with no dedicated toggle back to it).
  // Set this UNCONDITIONALLY, not inside the style.load callback below:
  // clicking "Satellite" when satellite is already the active style calls
  // setStyle() with the SAME url, which Mapbox GL treats as a no-op and
  // never re-fires 'style.load' -- so gating the projection switch behind
  // that event silently never ran it, and the map stayed a globe forever
  // no matter which button was clicked (confirmed: this is exactly why
  // Satellite looked identical to the removed Globe button).
  map.setProjection('mercator');
  const savedData = draw.getAll();
  map.setStyle(nextStyle);
  map.once('style.load', () => {{
    map.addControl(draw, 'top-left');
    if (savedData.features.length) draw.set(savedData);
    ensureBoundaryMarkerLayer();
    ensureDamsLayer();
    updateBoundaryMarkers();
  }});
}}

// Real inflow/outflow markers on the SAME 2D map the AOI is drawn on --
// every configured item (auto-detected, manual, or added by a GLOF
// scenario) shows up here, not just in the text list, so it's visually
// obvious where water is entering/leaving before a run is even started.
// Re-added after every style.load (setMapLayer above), since Mapbox GL
// drops custom sources/layers on setStyle() the same way it drops draw.
function ensureBoundaryMarkerLayer() {{
  if (map.getSource('boundary-markers')) return;
  map.addSource('boundary-markers', {{type: 'geojson', data: {{type: 'FeatureCollection', features: []}}}});
  map.addLayer({{
    id: 'boundary-markers-circle', type: 'circle', source: 'boundary-markers',
    paint: {{
      'circle-radius': 8,
      'circle-color': ['case', ['==', ['get', 'markerType'], 'inflow'], '#e05252', '#2ecc71'],
      'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff',
    }},
  }});
  map.addLayer({{
    id: 'boundary-markers-label', type: 'symbol', source: 'boundary-markers',
    layout: {{
      'text-field': ['get', 'label'], 'text-size': 11, 'text-offset': [0, 1.4], 'text-anchor': 'top',
      'text-allow-overlap': true, 'text-ignore-placement': true,
    }},
    paint: {{'text-color': '#ffffff', 'text-halo-color': '#0B1417', 'text-halo-width': 1.5}},
  }});
  if (!map._boundaryPopupBound) {{
    map._boundaryPopupBound = true;
    const popup = new mapboxgl.Popup({{closeButton: false, closeOnClick: false}});
    map.on('mouseenter', 'boundary-markers-circle', (e) => {{
      map.getCanvas().style.cursor = 'pointer';
      const p = e.features[0].properties;
      popup.setLngLat(e.features[0].geometry.coordinates)
        .setHTML('<b>' + (p.markerType === 'inflow' ? '↘ Inflow' : '↗ Outflow') + ':</b> ' + p.label +
                 (p.detail ? '<br>' + p.detail : '')).addTo(map);
    }});
    map.on('mouseleave', 'boundary-markers-circle', () => {{ map.getCanvas().style.cursor = ''; popup.remove(); }});
  }}
}}

// Approximates a real lon/lat for an item that only has an edge+percentage
// (a manual entry with no real detected crossing point) by walking that
// fraction along the AOI's own real bounding-box edge -- an honest visual
// placement, not claiming precision the manual entry doesn't have.
function boundaryItemLonLat(b) {{
  if (b.lon != null && b.lat != null) return [b.lon, b.lat];
  if (!currentGeoJSON) return null;
  const ring = currentGeoJSON.type === 'Polygon' ? currentGeoJSON.coordinates[0] : currentGeoJSON.coordinates[0][0];
  const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
  const lonMin = Math.min(...lons), lonMax = Math.max(...lons);
  const latMin = Math.min(...lats), latMax = Math.max(...lats);
  const frac = (b.pos_pct != null ? b.pos_pct : 50) / 100;
  if (b.edge === 'N') return [lonMin + frac * (lonMax - lonMin), latMax];
  if (b.edge === 'S') return [lonMin + frac * (lonMax - lonMin), latMin];
  if (b.edge === 'W') return [lonMin, latMin + frac * (latMax - latMin)];
  return [lonMax, latMin + frac * (latMax - latMin)];  // E
}}

// Real, nationwide existing dam/reservoir overlay (Global Dam Watch/
// GOODD, Dams data/Barriers.shp + Reservoirs.shp -- see dams_utils.py) --
// purely visual here (so a user can see real infrastructure before
// drawing an AOI); the real, permanent effect on a flood run's own
// terrain happens unconditionally server-side regardless of whether this
// toggle is ever turned on. Fetched once (small, static, nationwide
// data), re-added after every style.load like the boundary markers.
let damsLayerData = null, damsLayerVisible = false;
function ensureDamsLayer() {{
  if (map.getSource('dams-reservoirs')) return;
  map.addSource('dams-reservoirs', {{type: 'geojson', data: damsLayerData || {{type: 'FeatureCollection', features: []}}}});
  map.addLayer({{
    id: 'dams-reservoirs-fill', type: 'fill', source: 'dams-reservoirs',
    filter: ['==', ['get', 'kind'], 'reservoir'],
    paint: {{'fill-color': '#2d7dd2', 'fill-opacity': 0.45}},
    layout: {{visibility: damsLayerVisible ? 'visible' : 'none'}},
  }});
  map.addLayer({{
    id: 'dams-reservoirs-outline', type: 'line', source: 'dams-reservoirs',
    filter: ['==', ['get', 'kind'], 'reservoir'],
    paint: {{'line-color': '#2d7dd2', 'line-width': 1.5}},
    layout: {{visibility: damsLayerVisible ? 'visible' : 'none'}},
  }});
  map.addLayer({{
    id: 'dams-reservoirs-point', type: 'circle', source: 'dams-reservoirs',
    filter: ['==', ['get', 'kind'], 'dam'],
    paint: {{'circle-radius': 6, 'circle-color': '#c0622f', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff'}},
    layout: {{visibility: damsLayerVisible ? 'visible' : 'none'}},
  }});
  if (!map._damsPopupBound) {{
    map._damsPopupBound = true;
    const popup = new mapboxgl.Popup({{closeButton: false, closeOnClick: false}});
    const show = (e) => {{
      map.getCanvas().style.cursor = 'pointer';
      const p = e.features[0].properties;
      const html = p.kind === 'dam'
        ? '<b>' + p.name + '</b> (real dam)<br>Height: ' + p.height_m + 'm' + (p.height_is_real ? '' : ' (estimated, real height unavailable)') + (p.river ? '<br>River: ' + p.river : '')
        : '<b>' + p.name + '</b> (real reservoir)<br>Depth: ' + p.depth_m + 'm, Area: ' + p.area_skm + ' km&sup2;';
      popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
    }};
    map.on('mouseenter', 'dams-reservoirs-point', show);
    map.on('mouseenter', 'dams-reservoirs-fill', show);
    map.on('mouseleave', 'dams-reservoirs-point', () => {{ map.getCanvas().style.cursor = ''; popup.remove(); }});
    map.on('mouseleave', 'dams-reservoirs-fill', () => {{ map.getCanvas().style.cursor = ''; popup.remove(); }});
  }}
}}
function toggleDamsLayer() {{
  damsLayerVisible = !damsLayerVisible;
  document.getElementById('damsToggleBtn').classList.toggle('active', damsLayerVisible);
  const vis = damsLayerVisible ? 'visible' : 'none';
  ['dams-reservoirs-fill', 'dams-reservoirs-outline', 'dams-reservoirs-point'].forEach(id => {{
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis);
  }});
  if (damsLayerVisible && !damsLayerData) {{
    fetch('/dams/geojson').then(r => r.json()).then(d => {{
      damsLayerData = d;
      if (map.getSource('dams-reservoirs')) map.getSource('dams-reservoirs').setData(d);
    }});
  }}
}}

function updateBoundaryMarkers() {{
  if (!map.getSource('boundary-markers')) return;
  const features = boundaryItems.filter(b => b.included).map(b => {{
    const lonlat = boundaryItemLonLat(b);
    if (!lonlat) return null;
    const detail = b.type === 'inflow'
      ? (b.peak_m3s != null ? 'Peak ' + b.peak_m3s + ' m³/s' + (b.duration_hr ? ' over ' + b.duration_hr + 'h' : ' (constant)') : '')
      : ('Width ' + b.width_m + 'm');
    return {{type: 'Feature', geometry: {{type: 'Point', coordinates: lonlat}},
             properties: {{markerType: b.type, label: b.name, detail}}}};
  }}).filter(f => f);
  map.getSource('boundary-markers').setData({{type: 'FeatureCollection', features}});
}}

mapboxgl.accessToken = MAPBOX_TOKEN;
function exportMapPng(mapInstance, baseName) {{
  if (!mapInstance) return;
  mapInstance.once('render', () => {{
    const a = document.createElement('a');
    a.download = baseName + '.png';
    a.href = mapInstance.getCanvas().toDataURL('image/png');
    a.click();
  }});
  mapInstance.triggerRepaint();
}}

const map = new mapboxgl.Map({{
  container: 'map',
  style: MAP_STYLES.satellite,  // real satellite imagery on the globe, not a plain vector map
  center: [69.3451, 30.3753],
  zoom: 1.5,          // zoomed out to a whole-Earth view by default, not straight into Pakistan
  projection: 'globe',
  preserveDrawingBuffer: true
}});
map.once('style.load', () => {{
  // The initial view is always the satellite globe -- setMapLayer() re-
  // applies this same fog (or clears it back to flat mercator) on switch.
  applyGlobeFog();
  ensureBoundaryMarkerLayer();
  ensureDamsLayer();
}});
map.addControl(new mapboxgl.NavigationControl(), 'top-right');

// Real Mapbox geocoder search -- flies/zooms the map to whatever the user
// searches, so they can find their area of interest before drawing on it
// instead of hunting for it by hand from the whole-Earth default view.
// Biased (not hard-restricted) toward Pakistan, since that's this app's
// real modeling scope, but the whole-globe default view still allows
// searching/panning anywhere for orientation.
// Real Mapbox geocoder search, called directly via fetch() -- NOT the
// official mapbox-gl-geocoder plugin. Found the hard way that the
// plugin's bundled @mapbox/mapbox-sdk client never parses the response at
// all: Mapbox's live Geocoding API returns
// `content-type: application/vnd.geo+json`, a content-type that SDK
// version doesn't recognize, so it silently left the real JSON as an
// unparsed string on `.rawBody` and returned no usable features array to
// the plugin -- every search silently found nothing, with no error
// anywhere (confirmed directly: calling the SAME underlying
// geocoderService.forwardGeocode().send() call by hand returned the real
// Lahore result with a correct 200 status; only the plugin's own
// higher-level parsing dropped it). A plain fetch()+response.json() call
// has no such problem, and is simpler than debugging a stale bundled
// dependency.
const geoInput = document.getElementById('geoInput');
const geoResults = document.getElementById('geoResults');
let geoDebounce = null, geoFeatures = [], geoSelIdx = -1;

function geoRender() {{
  geoResults.innerHTML = geoFeatures.map((f, i) =>
    `<div class="geo-item${{i === geoSelIdx ? ' sel' : ''}}" data-i="${{i}}">${{f.kind === 'tehsil' ? '<b>Tehsil:</b> ' : ''}}${{f.place_name}}</div>`
  ).join('');
  geoResults.classList.toggle('open', geoFeatures.length > 0);
}}

// Selecting a real tehsil sets its own real boundary polygon directly as
// the AOI (same real setArea/MapboxDraw wiring uploadShape() already
// uses for an uploaded shapefile/.geojson) -- a real administrative area
// a user already knows the name of shouldn't need to be hand-traced.
function geoGoToTehsil(tehsilId, displayName) {{
  fetch('/tehsils/' + tehsilId).then(r => r.json()).then(d => {{
    if (d.error) {{ alert(d.error); return; }}
    draw.getAll().features.forEach(other => {{ if (other.geometry.type === 'Polygon') draw.delete(other.id); }});
    draw.add({{type:'Feature', properties:{{}}, geometry: d.geometry}});
    fetch('/area/polygon', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{geometry: d.geometry}})
    }}).then(r => r.json()).then(a => setArea(d.geometry, a.area_km2));
    const bounds = new mapboxgl.LngLatBounds();
    const coords = d.geometry.type === 'Polygon' ? d.geometry.coordinates[0] : d.geometry.coordinates[0][0];
    coords.forEach(c => bounds.extend(c));
    map.fitBounds(bounds, {{padding: 40, duration: 1500}});
  }});
}}

function loadRainfallHotspots() {{
  const box = document.getElementById('hotspotsBox');
  const metric = document.getElementById('hotspotMetric').value;
  const season = document.getElementById('hotspotSeason').value;
  box.innerHTML = '<p class="note">Loading real national records...</p>';
  fetch('/rainfall-hotspots?metric=' + encodeURIComponent(metric) + '&season=' + encodeURIComponent(season) + '&n=15')
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      if (!d.results.length) {{ box.innerHTML = '<p class="note">No real data for this metric/season combination.</p>'; return; }}
      const meta = d.metrics[metric] || {{}};
      box.innerHTML = '<p class="note">' + (meta.label || metric) + (meta.unit ? ' (' + meta.unit + ')' : '') + '</p>' +
        d.results.map((r, i) =>
          '<div class="stat-row" style="align-items:center;"><span>' + (i + 1) + '. ' +
          (r.tehsil || (r.lat + ', ' + r.lon)) + (r.district ? ', ' + r.district : '') + '</span>' +
          '<span>' + r.value + (r.tehsil_id ? ' <button class="secondary" style="padding:3px 7px;margin-left:6px;" onclick="geoGoToTehsil(' + r.tehsil_id + ')">Draw AOI here</button>' : '') + '</span></div>'
        ).join('');
    }});
}}

function loadBasins() {{
  const box = document.getElementById('basinsBox');
  const region = document.getElementById('basinRegion').value;
  box.innerHTML = '<p class="note">Loading real basins...</p>';
  fetch('/basins/search?region=' + encodeURIComponent(region) + '&n=15')
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      if (!d.results.length) {{ box.innerHTML = '<p class="note">No real basins found for this region.</p>'; return; }}
      box.innerHTML = d.results.map((r, i) =>
        '<div class="stat-row" style="align-items:center;"><span>' + (i + 1) + '. HYBAS ' + r.hybas_id +
        ' (' + r.area_km2.toFixed(1) + ' km&sup2;)</span>' +
        '<span><button class="secondary" style="padding:3px 7px;margin-left:6px;" onclick="useBasinAsAOI(' + r.hybas_id + ')">Use as AOI</button></span></div>'
      ).join('');
    }});
}}

function useBasinAsAOI(hybasId) {{
  fetch('/basins/' + hybasId).then(r => r.json()).then(d => {{
    if (d.error) {{ alert(d.error); return; }}
    draw.getAll().features.forEach(other => {{ if (other.geometry.type === 'Polygon') draw.delete(other.id); }});
    draw.add({{type:'Feature', properties:{{}}, geometry: d.geometry}});
    fetch('/area/polygon', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{geometry: d.geometry}})
    }}).then(r => r.json()).then(a => setArea(d.geometry, a.area_km2));
    const bounds = new mapboxgl.LngLatBounds();
    const coords = d.geometry.type === 'Polygon' ? d.geometry.coordinates[0] : d.geometry.coordinates[0][0];
    coords.forEach(c => bounds.extend(c));
    map.fitBounds(bounds, {{padding: 40, duration: 1500}});
  }});
}}

function geoGoTo(feature) {{
  if (!feature) return;
  geoInput.value = feature.place_name;
  geoFeatures = []; geoSelIdx = -1;
  geoRender();
  geoInput.blur();
  if (feature.kind === 'tehsil') {{ geoGoToTehsil(feature.id, feature.place_name); return; }}
  if (feature.bbox) {{
    map.fitBounds([[feature.bbox[0], feature.bbox[1]], [feature.bbox[2], feature.bbox[3]]], {{ padding: 60, duration: 1500 }});
  }} else {{
    map.flyTo({{ center: feature.center, zoom: 11, duration: 1500 }});
  }}
}}

geoInput.addEventListener('input', () => {{
  clearTimeout(geoDebounce);
  const q = geoInput.value.trim();
  if (q.length < 2) {{ geoFeatures = []; geoRender(); return; }}
  geoDebounce = setTimeout(() => {{
    const placesUrl = `https://api.mapbox.com/geocoding/v5/mapbox.places/${{encodeURIComponent(q)}}.json` +
      `?access_token=${{encodeURIComponent(MAPBOX_TOKEN)}}&limit=5&bbox=60.87,23.63,77.84,37.13`;
    const tehsilUrl = `/tehsils/search?q=${{encodeURIComponent(q)}}`;
    Promise.all([
      fetch(placesUrl).then(r => r.json()).catch(() => ({{features: []}})),
      fetch(tehsilUrl).then(r => r.json()).catch(() => ({{results: []}})),
    ]).then(([placeData, tehsilData]) => {{
      // Real Pakistan tehsil matches shown first -- more precise/
      // authoritative for a real named administrative area than a
      // generic geocoder hit, and directly settable as the AOI.
      const tehsilFeatures = (tehsilData.results || []).map(t => ({{
        kind: 'tehsil', id: t.id,
        place_name: t.name + (t.district && t.district !== t.name ? ', ' + t.district : '') +
          (t.province ? ' (' + t.province + ')' : ''),
      }}));
      const placeFeatures = (placeData.features || []).map(f => Object.assign({{kind: 'place'}}, f));
      geoFeatures = tehsilFeatures.concat(placeFeatures);
      geoSelIdx = -1;
      geoRender();
    }});
  }}, 300);
}});
geoInput.addEventListener('keydown', (e) => {{
  if (e.key === 'ArrowDown') {{ geoSelIdx = Math.min(geoSelIdx + 1, geoFeatures.length - 1); geoRender(); e.preventDefault(); }}
  else if (e.key === 'ArrowUp') {{ geoSelIdx = Math.max(geoSelIdx - 1, 0); geoRender(); e.preventDefault(); }}
  else if (e.key === 'Enter') {{ if (geoSelIdx >= 0) geoGoTo(geoFeatures[geoSelIdx]); }}
  else if (e.key === 'Escape') {{ geoFeatures = []; geoRender(); }}
}});
geoResults.addEventListener('mousedown', (e) => {{
  const item = e.target.closest('.geo-item');
  if (item) geoGoTo(geoFeatures[+item.dataset.i]);
}});
document.addEventListener('click', (e) => {{
  if (!e.target.closest('#geocoderSlot')) {{ geoFeatures = []; geoRender(); }}
}});

const draw = new MapboxDraw({{
  displayControlsDefault: false,
  controls: {{ polygon: true, line_string: true, trash: true }}
}});
map.addControl(draw, 'top-left');

let currentGeoJSON = null;
// A plan handed over by Islamabad SimCity (open this page with ?import=<id>): sets the area and tells /run to use
// the plan's own buildings and drains instead of the real ones. The user sets every run parameter here.
let importedPlan = null;
// Real protection-scenario structures (Tier 3's grey-infrastructure
// panel): each drawn LINE is a candidate structure, not a redefinition
// of the AOI -- which action a drawn feature triggers is decided by its
// own geometry type (Polygon -> AOI, LineString -> a structure), so
// there's no separate mode toggle and both can be visible at once.
let protectStructures = [];
// Real, possibly-multiple inflows/outflows -- replaces the old single
// detectedRiver/glofRiseFrac globals. Each item has: id, type
// (inflow/outflow), edge, lon, lat, pos_pct, width_m, peak_m3s,
// duration_hr, rise_frac, name, source (detected/manual/glof), included.
let boundaryItems = [];
let boundaryIdSeq = 1;

function setArea(geojsonGeometry, areaKm2) {{
  currentGeoJSON = geojsonGeometry;
  // Real boundary crossings detected against a PREVIOUS polygon shape are
  // no longer valid once the AOI changes -- their crossing points could
  // now sit anywhere relative to the new shape/edges, so they must be
  // re-detected, not silently carried over. Manually-added items are
  // edge-relative (not tied to a specific lon/lat), so those ARE kept.
  boundaryItems = boundaryItems.filter(b => b.source === 'manual');
  renderBoundaryList();
  document.getElementById('boundaryDetectNote').textContent = '';
  document.getElementById('glofBox').innerHTML = '';
  // A new AOI invalidates any previously-drawn protection structures --
  // their lines were positioned relative to the old polygon's own
  // baseline run, which no longer applies.
  protectStructures.forEach(s => {{ if (draw.get(s.drawId)) draw.delete(s.drawId); }});
  protectStructures = [];
  document.getElementById('protectBox').innerHTML = '';
  document.getElementById('protectOptBox').innerHTML = '';
  renderProtectStructures();
  document.getElementById('areaInfo').style.display = 'block';
  document.getElementById('areaKm2').textContent = areaKm2.toFixed(3) + ' km²';
  document.getElementById('runBtn').disabled = false;
  document.getElementById('detectBoundariesBtn').disabled = false;
  document.getElementById('instantFloodBtn').disabled = false;
  document.getElementById('designStormBtn').disabled = false;
  document.getElementById('idfDesignBtn').disabled = false;
  document.getElementById('coastalBtn').disabled = false;
  document.getElementById('coastalPreviewBtn').disabled = false;
  document.getElementById('coastalDynBtn').disabled = false;
  document.getElementById('glofEventsBtn').disabled = false;
  document.getElementById('coastalSuscBtn').disabled = false;
  fetchCoastalLibrary();
  document.getElementById('glofBtn').disabled = false;
  document.getElementById('srmBtn').disabled = false;
  document.getElementById('srmHydroBtn').disabled = false;
  document.getElementById('meltBtn').disabled = false;
  document.getElementById('meltCalBtn').disabled = false;
  document.getElementById('geomorphBtn').disabled = false;
  document.getElementById('spillwayBtn').disabled = false;
  document.getElementById('damFailureBtn').disabled = false;
  document.getElementById('glacierBaselineBtn').disabled = false;
  document.getElementById('glacierDispBtn').disabled = false;
  document.getElementById('itsliveBtn').disabled = false;
  document.getElementById('instantFloodBox').innerHTML = '';
  fetchInstantFloodLibrary();
  document.getElementById('batchRunBtn').disabled = false;
  document.getElementById('aalRunBtn').disabled = false;
  document.getElementById('watchAreaBtn').disabled = false;
  loadContext();
  showTier(2);
}}

// ---- each tier shows its own real Mapbox 3D visualization, per its own
// capabilities: Tier 2 shows the real HAND-based flood overlay for
// whatever discharge is currently set; Tier 3 shows the real animated
// physics-run overlay once a run exists.
let tier3dToken = 0;
function loadTier3D(n) {{
  if (!currentGeoJSON) return;
  if (document.body.classList.contains('view-hidden') && !window.viewUserHidden) setViewMode('card');   // first area: show the view
  const frame = document.getElementById('view3dFrame');
  const myToken = ++tier3dToken;
  if (n === 2) {{
    fetch('/area/instant-flood-mapbox3d', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{geometry: currentGeoJSON, discharge_m3s: parseFloat(document.getElementById('instantDischarge').value) || 0, use_global_dem: document.getElementById('useGlobalDem').checked}})
    }}).then(r => r.text()).then(html => {{ if (myToken === tier3dToken) {{ frame.removeAttribute('src'); frame.srcdoc = html; }} }});
  }} else if (n === 3) {{
    if (currentRunId) {{ frame.removeAttribute('srcdoc'); frame.src = '/runs/' + currentRunId + '/mapbox_3d.html'; }}
    else {{ frame.removeAttribute('src'); frame.srcdoc = '<body style="background:#0B1417;color:#8fa3a9;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">Run the flood model to see it here.</body>'; }}
  }} else if (n === 4) {{
    if (!lastCoastalResult) {{
      frame.removeAttribute('src'); frame.srcdoc = '<body style="background:#0B1417;color:#8fa3a9;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">Run the coastal flood analysis to see it here.</body>';
      return;
    }}
    fetch('/area/coastal-flood-mapbox3d', {{
      method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(lastCoastalRequest)
    }}).then(r => r.text()).then(html => {{ if (myToken === tier3dToken) {{ frame.removeAttribute('src'); frame.srcdoc = html; }} }});
  }}
}}

// Real, purely-additive entrance animation for dynamically-rendered
// results (a run's own stat panels, hotspot/basin lists, watch entries,
// etc.) -- a MutationObserver on the whole right column instead of
// touching every one of the hundreds of `box.innerHTML = ...` call
// sites individually. A single `elem.innerHTML = markup` assignment
// reports its own new top-level children as one mutation record (their
// own descendants arrive already-formed, not as separate records), so
// this naturally animates exactly the new content's own outer wrapper(s)
// once per real update, not a cascading per-element effect.
(function() {{
  const rc = document.getElementById('rightCol');
  if (!rc) return;
  new MutationObserver(muts => {{
    for (const m of muts) {{
      for (const node of m.addedNodes) {{
        if (node.nodeType === 1) node.classList.add('reveal-in');
      }}
    }}
  }}).observe(rc, {{childList:true, subtree:true}});
}})();

// SAILAB Physics only: the old tier switcher is gone. showTier() is kept so existing callers still work;
// it always shows the physics view, and showPane() switches the side-panel tabs.
const APP_MODE = document.body.dataset.mode || 'physics';
const MODE_TABS = {{
  physics: [['scenario', 'Scenario'], ['runs', 'Runs'], ['impact', 'Impact'], ['mitigate', 'Mitigation']],
  glof: [['scenario', 'Scenario'], ['runs', 'Runs'], ['impact', 'Impact'], ['glacier', 'Glacier']],
  coastal: [['scenario', 'Static'], ['surge', 'Surge']]
}};
// showTier() is kept so existing callers still work: it shows this page's own 3D view.
function showTier(n) {{
  loadTier3D(APP_MODE === 'coastal' ? 4 : 3);
}}
function showPane(name) {{
  const rc = document.getElementById('rightCol');
  if (!rc) return;
  const tabs = MODE_TABS[APP_MODE] || MODE_TABS.physics;
  if (!tabs.some(t => t[0] === name)) name = tabs[0][0];
  rc.setAttribute('data-pane', name);
  document.querySelectorAll('.ptab').forEach(b => {{
    const on = b.dataset.ptab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  }});
  rc.querySelectorAll('[data-pane]').forEach(el => {{
    const modes = (el.dataset.modes || 'physics').split(' ');
    el.style.display = (modes.includes(APP_MODE) && el.dataset.pane.split(' ').includes(name)) ? 'block' : 'none';
  }});
  rc.scrollTop = 0;
  try {{ sessionStorage.setItem('sailabPane_' + APP_MODE, name); }} catch (e) {{}}
}}
document.addEventListener('DOMContentLoaded', () => {{
  const bar = document.getElementById('ptabs');
  bar.innerHTML = (MODE_TABS[APP_MODE] || MODE_TABS.physics).map(t =>
    '<button class="ptab" data-ptab="' + t[0] + '" role="tab" onclick="showPane(\\'' + t[0] + '\\')">' + t[1] + '</button>').join('');
  if (APP_MODE === 'glof') {{ document.getElementById('rainMmHr').value = '0'; document.getElementById('durationHr').value = '1'; }}
  let p = '';
  try {{ p = sessionStorage.getItem('sailabPane_' + APP_MODE) || ''; }} catch (e) {{}}
  showPane(p);
}});

// Generic poller for the async job endpoints (design storm / climate
// factor / NbS scenario) -- all real work that can take real minutes
// (reading CMIP6 files or re-running the physics solver), so all three
// share one background-job + poll pattern instead of a plain fetch.
function pollJob(jobId, box, onDone, waitingMsg) {{
  box.innerHTML = '<p class="note">' + waitingMsg + '</p>';
  const check = () => {{
    fetch('/climate-jobs/' + jobId + '/progress.json').then(r => r.json()).then(d => {{
      if (d.status === 'running') {{ setTimeout(check, 3000); return; }}
      if (d.status === 'failed') {{ box.innerHTML = '<p class="note">Failed: ' + d.error + '</p>'; return; }}
      onDone(d.result);
    }});
  }};
  setTimeout(check, 1000);
}}

function runDesignStorm() {{
  const box = document.getElementById('designStormBox');
  const rp = parseInt(document.getElementById('returnPeriod').value);
  const scen = document.getElementById('climateScenario').value;
  document.getElementById('designStormBtn').disabled = true;
  fetch('/area/design-storm', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, return_period_years: rp}})
  }}).then(r => r.json()).then(d => {{
    pollJob(d.job_id, box, result => {{
      document.getElementById('designStormBtn').disabled = false;
      if (result.error) {{ box.innerHTML = '<p class="note">' + result.error + '</p>'; return; }}
      let depth = result.design_depth_mm_24hr;
      let factorHtml = '';
      const finish = () => {{
        document.getElementById('durationHr').value = 24;
        document.getElementById('rainMmHr').value = (2 * depth / 24).toFixed(2);
        box.innerHTML =
          '<div class="stat-row"><span>24hr design depth</span><span>' + result.design_depth_mm_24hr + ' mm</span></div>' +
          '<div class="stat-row"><span>Models used</span><span>' + result.n_models_used + '</span></div>' +
          '<div class="stat-row"><span>Ensemble range</span><span>' + result.ensemble_min_mm + '-' + result.ensemble_max_mm + ' mm</span></div>' +
          factorHtml +
          '<p class="note">Filled Duration=24h, Peak rainfall=' + (2*depth/24).toFixed(2) + ' mm/hr (a triangular storm totalling this real design depth). ' + result.method + '</p>';
      }};
      if (scen === 'current') {{ finish(); return; }}
      const [scenario, futurePeriod] = scen.split('|');
      box.innerHTML = '<p class="note">Fetching real climate-change intensification factor (CMIP6, this can take a few minutes)...</p>';
      fetch('/area/climate-factor', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{geometry: currentGeoJSON, return_period_years: rp, scenario: scenario, future_period: futurePeriod}})
      }}).then(r => r.json()).then(d2 => {{
        pollJob(d2.job_id, box, factorResult => {{
          if (!factorResult.error) {{
            depth = depth * factorResult.intensification_factor;
            factorHtml = '<div class="stat-row"><span>Climate factor (' + scenario + ', ' + futurePeriod + ')</span><span>&times;' + factorResult.intensification_factor + '</span></div>' +
              '<div class="stat-row"><span>Effective 24hr depth</span><span>' + depth.toFixed(1) + ' mm</span></div>';
          }}
          finish();
        }}, 'Fetching real climate-change intensification factor (CMIP6, this can take a few minutes)...');
      }});
    }}, 'Fetching real design storm from local CMIP6 data (first time can take 1-3 minutes; cached after)...');
  }});
}}

function runIdfDesignStorm() {{
  const box = document.getElementById('idfDesignBox');
  const durationHr = parseFloat(document.getElementById('idfDurationHr').value) || 2.0;
  const season = document.getElementById('idfSeason').value;
  box.innerHTML = '<p class="note">Looking up the real recorded IDF curve for this AOI...</p>';
  fetch('/area/idf-design-storm', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, duration_hr: durationHr, season: season}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    document.getElementById('durationHr').value = d.duration_hr;
    document.getElementById('rainMmHr').value = d.intensity_mm_hr;
    box.innerHTML =
      '<div class="stat-row"><span>Real recorded intensity</span><span>' + d.intensity_mm_hr + ' mm/hr</span></div>' +
      '<div class="stat-row"><span>Nearest grid cell</span><span>' + d.lat + '&deg;, ' + d.lon + '&deg;</span></div>' +
      '<div class="stat-row"><span>Season / year</span><span>' + d.season + ' ' + d.year + '</span></div>' +
      '<p class="note">Filled Peak rainfall=' + d.intensity_mm_hr + ' mm/hr, Duration=' + d.duration_hr + 'h. ' + d.method + '</p>';
  }});
}}

// Real uploaded rainfall time series (see rainfall_csv_utils.py) --
// null means "use the manual Peak rainfall/Duration fields above"
// (the original behaviour, unaffected). Once set, runModel() sends this
// instead of rain_mm_hr/duration_hr.
let uploadedRainfallSeries = null;

// Real, deliberate retry-once-on-transient-failure -- same reliability
// principle already used elsewhere in this app (Overpass 504s, etc.):
// a real network blip on this one request must not silently look
// identical to "the CSV was rejected" (see the sticky, explicit failure
// state uploadRainfallCsv sets on a SECOND real failure, and runModel's
// own guard below, which refuses to silently fall back to manual entry
// with no warning if a file was chosen but never successfully parsed).
function renderRainfallStationResult(d, box) {{
  uploadedRainfallSeries = {{times_hr: d.times_hr, rates_mm_hr: d.rates_mm_hr, total_mm: d.total_mm,
                              label: d.label, start_time_iso: d.start_time_iso, end_time_iso: d.end_time_iso,
                              peak_time_iso: d.peak_time_iso}};
  const fmtReal = (iso) => iso ? new Date(iso).toLocaleString(undefined, {{month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}}) : '?';
  box.innerHTML =
    '<div class="stat-row"><span>Station used</span><span>' + d.station_name + (d.location_text ? ' (' + d.location_text + ')' : '') + '</span></div>' +
    (d.n_stations > 1 && d.distance_km != null ? '<div class="stat-row"><span>Distance from AOI centroid</span><span>' + d.distance_km.toFixed(1) + ' km (nearest of ' + d.n_stations + ' stations in file)</span></div>' : '') +
    '<div class="stat-row"><span>Real storm window</span><span>' + fmtReal(d.start_time_iso) + ' &rarr; ' + fmtReal(d.end_time_iso) + '</span></div>' +
    '<div class="stat-row"><span>Real peak rate</span><span>' + d.peak_mm_hr.toFixed(1) + ' mm/hr at ' + fmtReal(d.peak_time_iso) + '</span></div>' +
    '<div class="stat-row"><span>Real total</span><span>' + d.total_mm.toFixed(1) + ' mm over ' + d.span_hr.toFixed(1) + 'h</span></div>' +
    '<p class="note">This real observed time series will drive the physics run directly (Peak rainfall/Duration fields above are ignored while this is active) -- the 3D flood animation will show real clock time and vary its rain intensity with the real observed rate. <a href="#" onclick="clearRainfallCsv();return false;">Clear and use manual entry instead</a></p>' +
    (d.geocode_note ? '<p class="note">' + d.geocode_note + '</p>' : '');
}}

// Real, deliberate retry-once-on-transient-failure -- same reliability
// principle already used elsewhere in this app (Overpass 504s, etc.):
// a real network blip on this one request must not silently look
// identical to "the CSV was rejected" (see the sticky, explicit failure
// state uploadRainfallCsv sets on a SECOND real failure, and runModel's
// own guard below, which refuses to silently fall back to manual entry
// with no warning if a file was chosen but never successfully parsed).
function uploadRainfallCsv(isRetry, stationKey) {{
  const f = document.getElementById('rainfallCsvFile').files[0];
  const box = document.getElementById('rainfallCsvBox');
  if (!f || !currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  uploadedRainfallSeries = null;
  window.rainfallCsvUploadFailed = false;
  if (!isRetry && !stationKey) box.innerHTML = '<p class="note">Parsing real rainfall CSV...</p>';
  const fd = new FormData();
  fd.append('file', f);
  fd.append('geometry', JSON.stringify(currentGeoJSON));
  if (stationKey) fd.append('station_key', stationKey);
  fetch('/area/rainfall-csv', {{method:'POST', body: fd}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; uploadedRainfallSeries = null;
                       window.rainfallCsvUploadFailed = true; return; }}
      if (d.needs_station_choice) {{
        // Real, deliberate design choice (not a fallback UI): this CSV
        // has no real lat/lon at all and more than one station -- rather
        // than silently auto-picking via an unreliable geocoded guess
        // (see /area/rainfall-csv's own docstring), the user is asked to
        // pick whichever real station matches wherever they've actually
        // drawn the AOI. Re-POSTs the SAME file with the chosen
        // station_key once picked (pickRainfallStation below).
        const rows = d.stations.map(s =>
          '<div class="stat-row"><span>' + s.key + (s.location_text ? ' (' + s.location_text + ')' : '') +
          '</span><span><button class="secondary" onclick="pickRainfallStation(\\'' + s.key.replace(/'/g, "\\\\'") + '\\')">' +
          'Use this one (peak ' + s.peak_mm_hr.toFixed(1) + ' mm/hr)</button></span></div>').join('');
        box.innerHTML = '<p class="note">' + d.note + '</p>' + rows;
        return;
      }}
      renderRainfallStationResult(d, box);
    }})
    .catch((e) => {{
      if (!isRetry) {{
        // One real, silent retry for a transient network error (the
        // most likely real cause -- confirmed live: the exact same
        // request succeeds reliably via a plain server-side client,
        // so a first-attempt browser/network hiccup is the realistic
        // failure mode here, not a bad file) -- never retried a second
        // time, so a genuinely broken upload still surfaces clearly.
        setTimeout(() => uploadRainfallCsv(true, stationKey), 1500);
        return;
      }}
      box.innerHTML = '<p class="note">Upload/parse failed after retrying: ' + (e && e.message ? e.message : 'network error') +
        '. Your Peak rainfall/Duration fields above will be used instead unless you try uploading again -- ' +
        'this is NOT silent: check this box before running.</p>';
      uploadedRainfallSeries = null;
      window.rainfallCsvUploadFailed = true;
    }});
}}

function clearRainfallCsv() {{
  uploadedRainfallSeries = null;
  window.rainfallCsvUploadFailed = false;
  document.getElementById('rainfallCsvFile').value = '';
  document.getElementById('rainfallCsvBox').innerHTML = '';
}}

function pickRainfallStation(stationKey) {{
  document.getElementById('rainfallCsvBox').innerHTML = '<p class="note">Loading real data for "' + stationKey + '"...</p>';
  uploadRainfallCsv(false, stationKey);
}}

// Fetches the real CMIP6/Gumbel design storm for one return period,
// resolving with {{peak_mm_hr, duration_hr, depth_mm_24hr}} -- same real
// triangular-hyetograph conversion (peak = 2*depth/24) runDesignStorm
// already uses, factored out so the batch loop below can reuse it
// without duplicating the design-storm-fetch/poll logic.
function fetchDesignStormAsPromise(returnPeriodYears) {{
  return new Promise((resolve, reject) => {{
    fetch('/area/design-storm', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{geometry: currentGeoJSON, return_period_years: returnPeriodYears}})
    }}).then(r => r.json()).then(d => {{
      const tick = () => {{
        fetch('/climate-jobs/' + d.job_id + '/progress.json').then(r => r.json()).then(result => {{
          if (result.status === 'running') {{ setTimeout(tick, 2000); return; }}
          if (result.status === 'failed' || result.error) {{ reject(result.error || 'design storm fetch failed'); return; }}
          const depth = result.result.design_depth_mm_24hr;
          resolve({{peak_mm_hr: 2 * depth / 24, duration_hr: 24, depth_mm_24hr: depth}});
        }});
      }};
      tick();
    }});
  }});
}}

// Real run-progress poll as a Promise, keyed by an explicit run_id --
// deliberately separate from pollStatus() (which mutates the single
// "current run" UI via the global currentRunId) so a batch of runs in
// the background never fights the main single-run status panel.
// Overall progress of one run, 0-100: each finished stage fills its share of the bar, and the simulation
// stage fills by simulated time. Weights are rough shares of the wall-clock time of a typical run.
const STAGE_START = {{dem:0, buildings:5, model:20, postprocess:70, scene:90, done:100}};
const STAGE_SHARE = {{dem:5, buildings:15, model:50, postprocess:20, scene:10, done:0}};
function overallPct(d) {{
  if (d.status === 'done') return 100;
  if (d.status === 'queued') return 0;
  const st = d.stage || 'dem';
  let p = STAGE_START[st] != null ? STAGE_START[st] : 0;
  if (st === 'model') p += STAGE_SHARE.model * Math.min(100, Math.max(0, d.pct || 0)) / 100;
  return Math.min(99, Math.round(p));
}}
function stageLabel(d) {{
  if (d.status === 'queued') return 'Waiting for the solver';
  const row = (d.stages || []).find(x => x[0] === d.stage);
  return row ? row[1] : 'Working';
}}
function pollRunProgressAsPromise(runId, onProgress) {{
  return new Promise((resolve, reject) => {{
    const tick = () => {{
      fetch('/runs/' + runId + '/progress.json').then(r => r.json()).then(d => {{
        if (onProgress) {{ try {{ onProgress(d); }} catch (e) {{}} }}
        if (d.status === 'done') resolve(d.summary);
        else if (d.status === 'failed') reject(d.log_tail || 'run failed');
        else if (d.status === 'cancelled') reject('run cancelled');
        else setTimeout(tick, 1000);
      }}).catch(() => setTimeout(tick, 2000));
    }};
    tick();
  }});
}}
function batchBar(done, total, d, head) {{
  const frac = (done + (d ? overallPct(d) / 100 : 0)) / total;
  const pct = Math.min(100, Math.round(frac * 100));
  return '<p class="note">' + head + (d ? ' &middot; ' + stageLabel(d) : '') + '</p>' +
    '<div class="progress-track" style="margin:6px 0 4px;"><div class="progress-fill" style="width:' + pct + '%"></div></div>' +
    '<p class="note" style="margin:0 0 6px;">' + pct + '% of ' + total + ' run' + (total > 1 ? 's' : '') + '</p>';
}}

async function runBatchReturnPeriods() {{
  if (!currentGeoJSON) return;
  const periods = Array.from(document.querySelectorAll('#batchPeriodChecks input:checked')).map(el => parseInt(el.value));
  if (!periods.length) return;
  const btn = document.getElementById('batchRunBtn');
  const box = document.getElementById('batchRunBox');
  btn.disabled = true;
  const rows = [];
  const baseBody = {{
    include_buildings: document.getElementById('includeBuildings').checked,
    friction: parseFloat(document.getElementById('friction').value),
    cfl: parseFloat(document.getElementById('cfl').value),
    infiltration_mm_hr: parseFloat(document.getElementById('infiltrationMmHr').value),
    use_soil_infiltration: document.getElementById('useSoilInfiltration').checked,
    use_green_ampt: document.getElementById('useGreenAmpt').checked,
    amc: parseFloat(document.getElementById('amc').value),
    use_global_dem: document.getElementById('useGlobalDem').checked,
  }};
  for (const rp of periods) {{
    const head = periods.indexOf(rp) + 1 + ' of ' + periods.length + ' &middot; ' + rp + '-year storm';
    box.innerHTML = batchBar(rows.length, periods.length, null, head + ' &middot; fetching design storm') + renderBatchRows(rows);
    try {{
      const storm = await fetchDesignStormAsPromise(rp);
      box.innerHTML = batchBar(rows.length, periods.length, null, head + ' &middot; starting') + renderBatchRows(rows);
      const runResp = await fetch('/run', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify(Object.assign({{}}, baseBody, {{
          geometry: currentGeoJSON, rain_mm_hr: storm.peak_mm_hr, duration_hr: storm.duration_hr,
        }}))
      }}).then(r => r.json());
      if (runResp.error) {{ rows.push({{rp, error: runResp.error}}); continue; }}
      const summary = await pollRunProgressAsPromise(runResp.run_id, d => {{
        box.innerHTML = batchBar(rows.length, periods.length, d, head) + renderBatchRows(rows);
      }});
      const loss = summary.estimated_building_loss;
      rows.push({{rp, run_id: runResp.run_id, depth_mm_24hr: storm.depth_mm_24hr,
        flooded_area_km2: summary.flooded_area_km2, max_depth_m: summary.max_depth_m,
        peak_hazard_band: summary.peak_hazard_band,
        estimated_building_loss_pkr: loss ? loss.total_loss_pkr : null}});
    }} catch (e) {{
      rows.push({{rp, error: String(e)}});
    }}
    box.innerHTML = renderBatchRows(rows);
  }}
  btn.disabled = false;
  const okRows = rows.filter(r => !r.error);
  if (okRows.length >= 2) {{
    const runIds = okRows.map(r => r.run_id).join(',');
    const labels = okRows.map(r => r.rp + 'yr').join(',');
    box.innerHTML += '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
      '<a class="btn primary" href="/batch-report/report.html?run_ids=' + encodeURIComponent(runIds) + '&labels=' + encodeURIComponent(labels) + '" target="_blank">Full batch report</a>' +
      '<a class="btn secondary" href="/batch-report/report.pdf?run_ids=' + encodeURIComponent(runIds) + '&labels=' + encodeURIComponent(labels) + '">Export PDF</a>' +
      '</div>';
  }}
}}

function renderBatchRows(rows) {{
  if (!rows.length) return '';
  const anyLoss = rows.some(r => !r.error && r.estimated_building_loss_pkr !== null && r.estimated_building_loss_pkr !== undefined);
  let html = '<table style="width:100%;margin-top:10px;font-size:12.5px;border-collapse:collapse;">' +
    '<tr style="text-align:left;color:var(--ink-2);"><th>Return period</th><th>24h depth</th><th>Flooded area</th><th>Max depth</th><th>Hazard</th>' +
    (anyLoss ? '<th>Est. loss</th>' : '') + '<th></th></tr>';
  for (const r of rows) {{
    if (r.error) {{
      html += '<tr><td>' + r.rp + 'yr</td><td colspan="' + (anyLoss ? 6 : 5) + '" style="color:#d03b3b;">' + r.error + '</td></tr>';
    }} else {{
      html += '<tr><td>' + r.rp + 'yr</td><td>' + r.depth_mm_24hr.toFixed(1) + ' mm</td>' +
        '<td>' + r.flooded_area_km2.toFixed(3) + ' km&sup2;</td><td>' + r.max_depth_m.toFixed(2) + ' m</td>' +
        '<td>' + r.peak_hazard_band + '</td>' +
        (anyLoss ? '<td>' + (r.estimated_building_loss_pkr !== null && r.estimated_building_loss_pkr !== undefined ?
          'Rs ' + Math.round(r.estimated_building_loss_pkr).toLocaleString() : '&mdash;') + '</td>' : '') +
        '<td><button class="secondary" onclick="viewBatchRun(\\'' + r.run_id + '\\')">View</button> ' +
        '<button class="secondary" onclick="compareVsCurrent(\\'' + r.run_id + '\\')">Cmp</button></td></tr>';
    }}
  }}
  return html + '</table>';
}}

// A SEPARATE, explicit action from runBatchReturnPeriods() above (per
// explicit request: this must never run automatically, since it's a
// real, potentially multi-minute sequence of real physics runs). Reuses
// the exact same real design-storm-fetch + /run + poll flow, but always
// forces include_buildings on (a real AAL needs a real building-loss
// figure at every point) and, once every real return period has
// finished, calls the real server-side trapezoidal AAL integration
// (aal_utils.compute_aal) instead of just tabulating flooded area.
async function runAALCompute() {{
  if (!currentGeoJSON) return;
  const periods = Array.from(document.querySelectorAll('#batchPeriodChecks input:checked')).map(el => parseInt(el.value));
  if (periods.length < 2) {{
    document.getElementById('aalRunBox').innerHTML = '<p class="note">Check at least 2 return periods above first.</p>';
    return;
  }}
  const btn = document.getElementById('aalRunBtn');
  const box = document.getElementById('aalRunBox');
  btn.disabled = true;
  const points = [];
  const baseBody = {{
    include_buildings: true,  // a real AAL needs real building-loss data at every point
    friction: parseFloat(document.getElementById('friction').value),
    cfl: parseFloat(document.getElementById('cfl').value),
    infiltration_mm_hr: parseFloat(document.getElementById('infiltrationMmHr').value),
    use_soil_infiltration: document.getElementById('useSoilInfiltration').checked,
    use_green_ampt: document.getElementById('useGreenAmpt').checked,
    amc: parseFloat(document.getElementById('amc').value),
    use_global_dem: document.getElementById('useGlobalDem').checked,
  }};
  let hadError = false;
  for (const rp of periods) {{
    const head = periods.indexOf(rp) + 1 + ' of ' + periods.length + ' &middot; ' + rp + '-year storm';
    box.innerHTML = batchBar(points.length, periods.length, null, head + ' &middot; fetching design storm');
    try {{
      const storm = await fetchDesignStormAsPromise(rp);
      box.innerHTML = batchBar(points.length, periods.length, null, head + ' &middot; starting');
      const runResp = await fetch('/run', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify(Object.assign({{}}, baseBody, {{
          geometry: currentGeoJSON, rain_mm_hr: storm.peak_mm_hr, duration_hr: storm.duration_hr,
        }}))
      }}).then(r => r.json());
      if (runResp.error) {{ throw new Error(runResp.error); }}
      await pollRunProgressAsPromise(runResp.run_id, d => {{ box.innerHTML = batchBar(points.length, periods.length, d, head); }});
      points.push({{return_period_years: rp, run_id: runResp.run_id}});
    }} catch (e) {{
      box.innerHTML = '<p class="note" style="color:#d03b3b;">Return period ' + rp + 'yr failed: ' + e + '</p>';
      hadError = true;
      break;
    }}
  }}
  if (hadError) {{ btn.disabled = false; return; }}

  box.innerHTML = '<p class="note">All runs finished &middot; computing the average annual loss</p><div class="progress-track" style="margin:6px 0;"><div class="progress-fill" style="width:97%"></div></div>';
  fetch('/area/compute-aal', {{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{points}})
  }}).then(r => r.json()).then(d => {{
    btn.disabled = false;
    if (d.error) {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">' + d.error + '</p>'; return; }}
    let html = '<div class="stat-row" style="font-weight:600;font-size:14px;"><span>Average Annual Loss</span><span>Rs ' +
      Math.round(d.aal_pkr).toLocaleString() + '/yr</span></div>' +
      '<table style="width:100%;margin-top:8px;font-size:12.5px;border-collapse:collapse;">' +
      '<tr style="text-align:left;color:var(--ink-2);"><th>Return period</th><th>Annual exceedance prob.</th><th>Loss</th></tr>';
    for (const p of d.points) {{
      html += '<tr><td>' + p.return_period_years + 'yr</td><td>' + p.annual_exceedance_probability + '</td>' +
        '<td>Rs ' + Math.round(p.loss_pkr).toLocaleString() + '</td></tr>';
    }}
    html += '</table><p class="note" style="margin-top:6px;">' + d.method + '</p>';
    box.innerHTML = html;
  }}).catch(() => {{ btn.disabled = false; box.innerHTML = '<p class="note" style="color:#d03b3b;">AAL computation failed.</p>'; }});
}}

// Switches the main single-run view (3D pane, status panel) to a
// specific batch run's own result -- reuses pollStatus()'s existing
// rendering by just pointing currentRunId at the chosen run and
// re-polling once (that run is already 'done', so this resolves instantly).
function viewBatchRun(runId) {{
  currentRunId = runId;
  showTier(3);
  pollStatus();
}}

function compareVsCurrent(runId) {{
  if (!currentRunId) {{ alert('Run the baseline flood model (above) first.'); return; }}
  document.getElementById('compareRunA').value = currentRunId;
  document.getElementById('compareRunB').value = runId;
  compareRuns();
}}

function compareRuns() {{
  const runA = document.getElementById('compareRunA').value.trim();
  const runB = document.getElementById('compareRunB').value.trim();
  const box = document.getElementById('compareRunsBox');
  if (!runA || !runB) {{ box.innerHTML = '<p class="note">Enter both run ids.</p>'; return; }}
  box.innerHTML = '<p class="note">Comparing...</p>';
  fetch('/compare-runs', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{run_id_a: runA, run_id_b: runB}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    const a = d.run_a, b = d.run_b, c = d.comparison;
    box.innerHTML =
      '<table style="width:100%;font-size:12.5px;border-collapse:collapse;">' +
      '<tr style="text-align:left;color:var(--ink-2);"><th></th><th>Run A</th><th>Run B</th></tr>' +
      '<tr><td>Scenario</td><td>' + a.scenario + '</td><td>' + b.scenario + '</td></tr>' +
      '<tr><td>Flooded area</td><td>' + a.flooded_area_km2 + ' km&sup2;</td><td>' + b.flooded_area_km2 + ' km&sup2;</td></tr>' +
      '<tr><td>Max depth</td><td>' + a.max_depth_m + ' m</td><td>' + b.max_depth_m + ' m</td></tr>' +
      '<tr><td>Peak hazard</td><td>' + a.peak_hazard_band + '</td><td>' + b.peak_hazard_band + '</td></tr>' +
      (a.estimated_building_loss_pkr !== null && b.estimated_building_loss_pkr !== null ?
        '<tr><td>Est. building loss</td><td>Rs ' + Math.round(a.estimated_building_loss_pkr).toLocaleString() +
        '</td><td>Rs ' + Math.round(b.estimated_building_loss_pkr).toLocaleString() + '</td></tr>' : '') +
      '</table>' +
      '<div class="stat-row" style="margin-top:8px;"><span>Only A flooded</span><span>' + c.area_only_a_km2 + ' km&sup2; (' + c.cells_only_a + ' cells)</span></div>' +
      '<div class="stat-row"><span>Only B flooded</span><span>' + c.area_only_b_km2 + ' km&sup2; (' + c.cells_only_b + ' cells)</span></div>' +
      '<div class="stat-row"><span>Both flooded</span><span>' + c.area_both_km2 + ' km&sup2; (' + c.cells_both + ' cells)</span></div>' +
      '<div class="stat-row"><span>Mean depth diff (B-A) where both wet</span><span>' + c.mean_depth_diff_where_both_m + ' m</span></div>' +
      (c.building_loss_diff_pkr !== null && c.building_loss_diff_pkr !== undefined ?
        '<div class="stat-row" style="font-weight:600;"><span>Est. building loss diff (B-A)</span><span>Rs ' +
        Math.round(c.building_loss_diff_pkr).toLocaleString() + '</span></div>' : '') +
      '<p class="note">' + d.method + '</p>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">' +
      '<button class="secondary" onclick="showCompareRunsOnMap(\\'' + runA + '\\',\\'' + runB + '\\')">Show diff on map</button>' +
      '<a class="btn primary" href="/compare-runs/' + runA + '/' + runB + '/report.html" target="_blank">Full comparison report</a>' +
      '<a class="btn secondary" href="/compare-runs/' + runA + '/' + runB + '/report.pdf">Export PDF</a>' +
      '</div>';
  }});
}}

function showCompareRunsOnMap(runA, runB) {{
  const frame = document.getElementById('view3dFrame');
  fetch('/compare-runs/mapbox3d', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{run_id_a: runA, run_id_b: runB}})
  }}).then(r => r.text()).then(html => {{ frame.removeAttribute('src'); frame.srcdoc = html; }});
}}

// Real live/continuous monitoring -- watches the currently drawn AOI's
// real Open-Meteo forecast on a schedule server-side (see
// monitoring_utils.py); this just saves the watch + renders the list,
// the actual periodic checking happens in a background thread on the
// server regardless of whether this page is even open.
function watchCurrentArea() {{
  if (!currentGeoJSON) return;
  const body = {{
    geometry: currentGeoJSON,
    threshold_mm_hr: parseFloat(document.getElementById('watchThreshold').value) || 10,
    check_interval_hours: parseFloat(document.getElementById('watchInterval').value) || 3,
    webhook_url: document.getElementById('watchWebhookUrl').value.trim(),
    include_buildings: document.getElementById('includeBuildings').checked,
    friction: parseFloat(document.getElementById('friction').value),
    cfl: parseFloat(document.getElementById('cfl').value),
  }};
  fetch('/monitor/watch', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}})
    .then(r => r.json()).then(() => loadWatchList());
}}

function loadWatchList() {{
  fetch('/monitor/list').then(r => r.json()).then(d => renderWatchList(d.watches || []));
}}

function renderWatchList(watches) {{
  const box = document.getElementById('watchListBox');
  if (!watches.length) {{ box.innerHTML = '<p class="note">No areas currently watched.</p>'; return; }}
  let html = '';
  for (const w of watches) {{
    const lastPeak = (w.last_peak_mm_hr != null) ? w.last_peak_mm_hr.toFixed(1) + ' mm/hr' : 'not yet checked';
    const lastChecked = w.last_checked ? new Date(w.last_checked * 1000).toLocaleString() : 'never';
    const alerts = (w.alerts || []).slice(-3).reverse();
    const alertsHtml = alerts.length ?
      '<div style="margin-top:6px;">' + alerts.map(a =>
        '<div class="stat-row"><span>' + new Date(a.timestamp * 1000).toLocaleString() + ' &mdash; ' +
        a.peak_mm_hr.toFixed(1) + ' mm/hr' + (w.webhook_url ? (a.webhook_sent ? ' &middot; notified' : ' &middot; notify failed') : '') +
        '</span><span>' +
        (a.run_id ? '<a href="#" onclick="viewBatchRun(\\'' + a.run_id + '\\');return false;">View run</a>' : 'no run') +
        '</span></div>').join('') + '</div>' : '';
    html += '<div class="panel-section" style="border:1px solid var(--line);border-radius:8px;padding:10px;margin:6px 0;">' +
      '<div class="stat-row"><span><b>' + w.name + '</b></span>' +
      '<span><button class="secondary" onclick="checkWatchNow(\\'' + w.id + '\\')">Check now</button> ' +
      '<button class="secondary" onclick="removeWatch(\\'' + w.id + '\\')">Remove</button></span></div>' +
      '<div class="stat-row"><span>Threshold</span><span>' + w.threshold_mm_hr + ' mm/hr, every ' + w.check_interval_hours + 'h</span></div>' +
      '<div class="stat-row"><span>Last checked</span><span>' + lastChecked + ' (peak ' + lastPeak + ')</span></div>' +
      '<div class="stat-row"><span>Webhook</span><span>' + (w.webhook_url ? 'configured' : 'none') + '</span></div>' +
      alertsHtml + '</div>';
  }}
  box.innerHTML = html;
}}

function checkWatchNow(id) {{
  fetch('/monitor/' + id + '/check-now', {{method:'POST'}}).then(r => r.json()).then(() => loadWatchList());
}}

function removeWatch(id) {{
  fetch('/monitor/' + id, {{method:'DELETE'}}).then(() => loadWatchList());
}}

loadWatchList();

let lastCoastalResult = null, lastCoastalRequest = null;
function runCoastalFlood() {{
  const box = document.getElementById('coastalBox');
  const preset = document.getElementById('coastalSurgePreset').value;
  const targetYear = document.getElementById('coastalTargetYear').value;
  const req = {{
    geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked,
    return_period_years: parseInt(document.getElementById('coastalReturnPeriod').value),
    target_year: targetYear ? parseInt(targetYear) : null,
    custom_surge_m: preset ? parseFloat(preset) : null,
    water_level_source: document.getElementById('coastalSource').value, extra_slr_m: parseFloat(document.getElementById('coastalSlr').value) || 0,
    include_wave_runup: document.getElementById('includeWaveRunup').checked,
  }};
  box.innerHTML = '<p class="note">Computing static coastal inundation...</p>';
  fetch('/area/coastal-flood', {{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(req)
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{
      box.innerHTML = '<p class="note">' + d.error + '</p>';
      if (d.needs_global_dem_consent) showGlobalDemPrompt(d.error);
      return;
    }}
    lastCoastalResult = d; lastCoastalRequest = req;
    box.innerHTML =
      '<div class="stat-row"><span>Total water level</span><span>' + d.water_level.total_water_level_m + ' m</span></div>' +
      (d.wave_runup ? '<div class="stat-row"><span>Wave run-up</span><span>+' + d.wave_runup.runup_m + ' m (Hs ' + d.wave_runup.significant_wave_height_m + 'm, T ' + d.wave_runup.peak_period_s + 's)</span></div>' +
        '<div class="stat-row"><span>Total level with run-up</span><span>' + d.total_level_with_runup_m + ' m</span></div>' : '') +
      '<div class="stat-row"><span>Flooded area</span><span>' + d.flooded_area_km2 + ' km&sup2;</span></div>' +
      '<div class="stat-row"><span>Max depth</span><span>' + d.max_depth_m + ' m</span></div>' +
      '<p class="note">' + d.water_level.basis + (d.water_level.sea_level_rise_added_m ? ' + ' + d.water_level.sea_level_rise_added_m + 'm real SLR trend' : '') + '</p>' +
      (d.wave_runup ? '<p class="note">' + d.wave_runup.wave_basis + ' &middot; foreshore slope ' + d.wave_runup.foreshore_slope_used + ' (' + d.wave_runup.foreshore_slope_basis + ') &middot; ' + d.wave_runup.method + '</p>' : '') +
      '<button class="secondary" onclick="downloadResultPdf(\\'/report/coastal.pdf\\', {{result: lastCoastalResult, request: lastCoastalRequest}})">Export PDF summary</button>';
    loadTier3D(4);
  }});
}}


let coastalLibrary = null, coastalRefLevels = null;
function updateCoastalSourceUI() {{
  const src = document.getElementById('coastalSource').value, sel = document.getElementById('coastalReturnPeriod');
  for (const o of sel.options) {{
    const v = parseInt(o.value);
    o.disabled = (src === 'khan' && v < 50) || (src === 'gtsm' && v > 100 && false);
  }}
  if (src === 'khan' && parseInt(sel.value) < 50) sel.value = '100';
  document.getElementById('coastalSourceNote').textContent = src === 'khan'
    ? 'Tide-gauge based, Karachi Port, applied to the whole coast, measured from mean sea level (the published levels are from the gauge zero, so the 1.85 m between that zero and mean sea level is subtracted). About 1.9 m for 100 years.'
    : 'Modelled (GTSM-ERA5-E) at the nearest station, so it varies along the coast. Coarse near the coast, and its vertical datum is not formally tied to the SRTM DEM. At Karachi it gives about 1.79 m for 100 years, which agrees with the tide-gauge value measured from mean sea level (about 1.9 m).';
}}
function fetchCoastalLibrary() {{
  coastalLibrary = null;
  const box = document.getElementById('coastalLevelBox');
  box.innerHTML = '<p class="note">Computing the level library for this area...</p>';
  document.getElementById('coastalLevelSlider').disabled = true; document.getElementById('coastalLevelMapBtn').disabled = true;
  const tok = currentGeoJSON;
  fetch('/area/coastal-level-library', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked}})}})
    .then(r => r.json()).then(d => {{
      if (tok !== currentGeoJSON) return;
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      coastalLibrary = d.library; coastalRefLevels = d.reference_levels;
      document.getElementById('coastalLevelSlider').disabled = false; document.getElementById('coastalLevelMapBtn').disabled = false;
      syncCoastalLevel();
    }}).catch(() => {{ box.innerHTML = '<p class="note">Level library unavailable.</p>'; }});
}}
function syncCoastalLevel() {{
  const box = document.getElementById('coastalLevelBox');
  if (!coastalLibrary) return;
  const L = parseFloat(document.getElementById('coastalLevelSlider').value);
  let lo = coastalLibrary[0], hi = coastalLibrary[coastalLibrary.length - 1];
  for (let i = 1; i < coastalLibrary.length; i++) if (coastalLibrary[i].level_m >= L) {{ lo = coastalLibrary[i - 1]; hi = coastalLibrary[i]; break; }}
  const t = hi.level_m > lo.level_m ? Math.min(1, Math.max(0, (L - lo.level_m) / (hi.level_m - lo.level_m))) : 0;
  const lerp = k => lo[k] + t * (hi[k] - lo[k]);
  let refs = '';
  if (coastalRefLevels) {{
    refs = 'Tide gauge (Khan): 50 yr ' + coastalRefLevels.khan['50'] + ' m, 100 yr ' + coastalRefLevels.khan['100'] + ' m, 1000 yr ' + coastalRefLevels.khan['1000'] + ' m';
    if (coastalRefLevels.gtsm) refs += '<br>GTSM station ' + coastalRefLevels.gtsm.station.station_index + ' (' + coastalRefLevels.gtsm.station.distance_km + ' km): 2 yr ' + coastalRefLevels.gtsm.levels['2'] + ' m, 10 yr ' + coastalRefLevels.gtsm.levels['10'] + ' m, 100 yr ' + coastalRefLevels.gtsm.levels['100'] + ' m';
  }}
  box.innerHTML =
    '<div class="stat-row"><span>Still-water level</span><span>' + L.toFixed(2) + ' m</span></div>' +
    '<div class="stat-row"><span>Flooded area</span><span>' + lerp('area_km2').toFixed(2) + ' km&sup2;</span></div>' +
    '<div class="stat-row"><span>Max depth</span><span>' + lerp('max_depth_m').toFixed(2) + ' m</span></div>' +
    '<div class="stat-row"><span>Water volume</span><span>' + Math.round(lerp('volume_m3')).toLocaleString() + ' m&sup3;</span></div>' +
    '<p class="note">' + refs + '</p>';
}}
function showCoastalLevelOnMap() {{
  const L = parseFloat(document.getElementById('coastalLevelSlider').value);
  const frame = document.getElementById('view3dFrame');
  fetch('/area/coastal-flood-mapbox3d', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, custom_surge_m: L, use_global_dem: document.getElementById('useGlobalDem').checked}})}})
    .then(r => r.text()).then(html => {{ frame.removeAttribute('src'); frame.srcdoc = html; }});
}}
function runCoastalSusceptibility() {{
  const box = document.getElementById('coastalSuscBox');
  box.innerHTML = '<p class="note">Computing...</p>';
  const req = {{geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked}};
  fetch('/area/coastal-susceptibility', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(req)}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; if (d.needs_global_dem_consent) showGlobalDemPrompt(d.error); return; }}
      lastCoastalSuscRequest = req;
      let rows = d.classes.slice().reverse().map(c =>
        '<div class="stat-row"><span><span style="display:inline-block;width:10px;height:10px;background:' + c.color + ';border:1px solid #333;margin-right:6px;"></span>' + c.name + '</span><span>' + c.area_km2 + ' km&sup2; (' + c.pct + '%)</span></div>').join('');
      box.innerHTML = rows +
        (d.mangroves ? '<div class="stat-row"><span>Mangrove area in AOI</span><span>' + d.mangroves.mangrove_area_km2 + ' km&sup2;</span></div>' : '') +
        '<p class="note">Factors: ' + d.factors_used.join(', ').replace(/_/g, ' ') + '. ' + d.method + '</p>' +
        '<button class="secondary" onclick="showCoastalSuscOnMap()">Show on map</button> ' +
        '<button class="secondary" onclick="downloadCoastalSuscMap()">Download static map (PNG)</button>';
      showCoastalSuscOnMap();
    }});
}}
let lastCoastalSuscRequest = null;
function showCoastalSuscOnMap() {{
  if (!lastCoastalSuscRequest) return;
  const frame = document.getElementById('view3dFrame');
  fetch('/area/coastal-susceptibility-mapbox3d', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(lastCoastalSuscRequest)}})
    .then(r => r.text()).then(html => {{ frame.removeAttribute('src'); frame.srcdoc = html; }});
}}
function downloadCoastalSuscMap() {{
  if (!lastCoastalSuscRequest) return;
  fetch('/area/coastal-susceptibility-map.png', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(lastCoastalSuscRequest)}})
    .then(r => r.ok ? r.blob() : r.json().then(j => {{ throw new Error(j.error || 'failed'); }}))
    .then(b => {{ const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = 'coastal_susceptibility.png'; a.click(); }})
    .catch(e => alert('Map download failed: ' + e.message));
}}
updateCoastalSourceUI();

function updateCombineTideVisibility() {{
  const isGtsr = document.getElementById('coastalDynAnchor').value === 'gtsr';
  document.getElementById('combineTideRow').style.display = isGtsr ? 'block' : 'none';
}}

function runCoastalFloodDynamic() {{
  const box = document.getElementById('coastalDynBox');
  const preset = document.getElementById('coastalSurgePreset').value;
  const targetYear = document.getElementById('coastalTargetYear').value;
  const req = {{
    geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked,
    return_period_years: parseInt(document.getElementById('coastalReturnPeriod').value),
    target_year: targetYear ? parseInt(targetYear) : null,
    custom_surge_m: preset ? parseFloat(preset) : null,
    water_level_source: document.getElementById('coastalSource').value, extra_slr_m: parseFloat(document.getElementById('coastalSlr').value) || 0,
    anchor_mode: document.getElementById('coastalDynAnchor').value,
    window_days: parseInt(document.getElementById('coastalDynWindow').value),
    combine_tide: document.getElementById('combineTide').checked,
    tide_phase: document.getElementById('coastalDynTide').value || null,
    include_wave_runup: document.getElementById('includeWaveRunupDyn').checked,
    rain_mm_hr: document.getElementById('coastalCompound').checked ? parseFloat(document.getElementById('coastalCompoundRain').value) : 0.0,
    duration_hr: document.getElementById('coastalCompound').checked ? parseFloat(document.getElementById('coastalCompoundDuration').value) : 0.0,
  }};
  document.getElementById('coastalDynBtn').disabled = true;
  fetch('/area/coastal-flood-dynamic', {{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(req)
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{
      box.innerHTML = '<p class="note">' + d.error + '</p>';
      document.getElementById('coastalDynBtn').disabled = false;
      return;
    }}
    pollJob(d.job_id, box, result => {{
      document.getElementById('coastalDynBtn').disabled = false;
      box.innerHTML =
        '<div class="stat-row"><span>Nearest GTSR station</span><span>' + result.gtsr_station.name + ' (' + result.gtsr_station.distance_km + ' km)</span></div>' +
        '<div class="stat-row"><span>Anchor peak water level</span><span>' + result.anchor_peak_water_level_m + ' m</span></div>' +
        '<div class="stat-row"><span>Boundary edge</span><span>' + result.boundary_edge + ' (mean elev ' + result.boundary_edge_mean_elev_m + ' m)</span></div>' +
        '<div class="stat-row"><span>Flooded area</span><span>' + result.flooded_area_km2 + ' km&sup2;</span></div>' +
        '<div class="stat-row"><span>Max depth</span><span>' + result.max_depth_m + ' m</span></div>' +
        '<div class="stat-row"><span>Real physics run time</span><span>' + result.elapsed_seconds + ' s</span></div>' +
        (result.compound_event ? '<div class="stat-row" style="font-weight:600;"><span>Compound event</span><span>' +
          result.compound_rain_mm_hr + ' mm/hr rain over ' + result.compound_duration_hr +
          'h, WITH real ocean backwater</span></div>' : '') +
        '<p class="note">' + result.anchor_basis + '</p>' +
        (result.astronomical_tide ? '<p class="note">Astronomical tide: ' + result.astronomical_tide.phase_meaning + '. Tide at the surge peak ' + result.astronomical_tide.tide_at_surge_peak_m + ' m; highest total level ' + result.astronomical_tide.max_total_level_m + ' m above mean sea level.</p>' : '') +
        (result.tide_combination_note ? '<p class="note">Tidal range: ' + result.tide_combination_note + '</p>' : '') +
        (result.wave_runup ? '<p class="note">Wave run-up: +' + result.wave_runup.runup_m + ' m (' + result.wave_runup.wave_basis + ', foreshore slope ' + result.wave_runup.foreshore_slope_used + ')</p>' : '') +
        '<p class="note">' + result.method + '</p>' +
        '<p><a href="/coastal-dyn/' + d.job_id + '/mapbox3d" target="_blank">Open real 3D animated surge view &rarr;</a></p>';
    }}, 'Running real dynamic GTSR-driven physics (CPU) -- this can take a few real minutes...');
  }});
}}

let lastCoastalPreviewRequest = null;
function runCoastalFloodPreview() {{
  const box = document.getElementById('coastalPreviewBox');
  const preset = document.getElementById('coastalSurgePreset').value;
  const targetYear = document.getElementById('coastalTargetYear').value;
  const req = {{
    geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked,
    return_period_years: parseInt(document.getElementById('coastalReturnPeriod').value),
    target_year: targetYear ? parseInt(targetYear) : null,
    custom_surge_m: preset ? parseFloat(preset) : null,
    water_level_source: document.getElementById('coastalSource').value, extra_slr_m: parseFloat(document.getElementById('coastalSlr').value) || 0,
    anchor_mode: document.getElementById('coastalPreviewAnchor').value,
    tide_phase: document.getElementById('coastalPreviewTide').value || null,
    window_days: 5,
  }};
  document.getElementById('coastalPreviewBtn').disabled = true;
  box.innerHTML = '<p class="note">Building fast animated preview (real GTSR series + static bathtub per frame, instant)...</p>';
  fetch('/area/coastal-flood-instant-preview', {{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(req)
  }}).then(r => r.json()).then(d => {{
    document.getElementById('coastalPreviewBtn').disabled = false;
    if (d.error) {{
      box.innerHTML = '<p class="note">' + d.error + '</p>';
      if (d.needs_global_dem_consent) showGlobalDemPrompt(d.error);
      return;
    }}
    lastCoastalPreviewRequest = req;
    const peakArea = Math.max(...d.frames.map(f => f.flooded_area_km2));
    const peakDepth = Math.max(...d.frames.map(f => f.max_depth_m));
    box.innerHTML =
      '<div class="stat-row"><span>Nearest GTSR station</span><span>' + d.station.name + ' (' + d.station.distance_km + ' km)</span></div>' +
      '<div class="stat-row"><span>Anchor peak water level</span><span>' + d.anchor_peak_water_level_m + ' m</span></div>' +
      '<div class="stat-row"><span>Peak frame flooded area</span><span>' + peakArea.toFixed(3) + ' km&sup2;</span></div>' +
      '<div class="stat-row"><span>Peak frame max depth</span><span>' + peakDepth.toFixed(2) + ' m</span></div>' +
      '<p class="note">' + d.anchor_basis + '</p>' +
      '<p class="note">' + d.method + '</p>' +
      '<button class="secondary" onclick="showCoastalPreviewOnMap()">Show animated preview on map</button>';
  }});
}}

function showCoastalPreviewOnMap() {{
  if (!lastCoastalPreviewRequest) return;
  const frame = document.getElementById('view3dFrame');
  fetch('/area/coastal-flood-instant-preview-mapbox3d', {{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(lastCoastalPreviewRequest)
  }}).then(r => r.text()).then(html => {{ frame.removeAttribute('src'); frame.srcdoc = html; }});
}}

function runNbsScenario() {{
  const box = document.getElementById('nbsBox');
  if (!currentRunId) {{ box.innerHTML = '<p class="note">Run the flood model (above) first.</p>'; return; }}
  document.getElementById('nbsBtn').disabled = true;
  fetch('/area/nbs-scenario', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{run_id: currentRunId, intervention: document.getElementById('nbsIntervention').value,
                           pathway: document.getElementById('nbsPathway').value}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; document.getElementById('nbsBtn').disabled = false; return; }}
    pollJob(d.job_id, box, result => {{
      document.getElementById('nbsBtn').disabled = false;
      const c = result.comparison;
      box.innerHTML =
        '<div class="stat-row"><span>Baseline flooded cells</span><span>' + c.baseline_flooded_cells + '</span></div>' +
        '<div class="stat-row"><span>Scenario flooded cells</span><span>' + c.scenario_flooded_cells + '</span></div>' +
        '<div class="stat-row"><span>Reduction</span><span>' + c.reduction_pct + '%</span></div>' +
        '<div class="stat-row"><span>Displacement</span><span>' + c.displacement_pct + '%</span></div>' +
        (result.loss_avoided_pkr !== null && result.loss_avoided_pkr !== undefined ?
          '<div class="stat-row" style="font-weight:600;"><span>Est. building loss avoided</span><span>Rs ' +
          Math.round(result.loss_avoided_pkr).toLocaleString() + '</span></div>' : '') +
        '<div class="stat-row"><span>Real infiltration rate used</span><span>' + result.new_infiltration_mm_hr_baseline_amc2 + ' mm/hr</span></div>' +
        '<p class="note">' + result.intervention + ' (' + result.pathway + ' pathway). Reduction/displacement measured against the baseline run\\'s own flood extent -- an intervention is not free; water reduced in one place can appear elsewhere, reported here rather than hidden.</p>' +
        '<button class="secondary" onclick="showOnMap(\\'/nbs/' + result.job_id + '/mapbox3d\\')">Show on map</button>';
    }}, 'Running the real physics model with the modified land cover (this re-runs the full flood simulation)...');
  }});
}}

function runNbsRecommend() {{
  const box = document.getElementById('nbsRecommendBox');
  if (!currentRunId) {{ box.innerHTML = '<p class="note">Run the flood model (above) first.</p>'; return; }}
  document.getElementById('nbsRecommendBtn').disabled = true;
  fetch('/area/nbs-recommend', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{run_id: currentRunId}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; document.getElementById('nbsRecommendBtn').disabled = false; return; }}
    box.innerHTML = '<p class="note">Evaluating real (intervention, pathway) combinations... trial 0/6</p>';
    const check = () => {{
      fetch('/climate-jobs/' + d.job_id + '/progress.json').then(r => r.json()).then(p => {{
        if (p.status === 'running') {{
          box.innerHTML = '<p class="note">Evaluating real (intervention, pathway) combinations... trial ' + (p.trial || 0) + '/' + (p.max_evals || 6) + '</p>';
          setTimeout(check, 3000);
          return;
        }}
        document.getElementById('nbsRecommendBtn').disabled = false;
        if (p.status === 'failed') {{ box.innerHTML = '<p class="note">Failed: ' + p.error + '</p>'; return; }}
        const ranked = p.result.ranked;
        box.innerHTML = ranked.map((t, i) =>
          '<div class="stat-row"><span>' + (i === 0 ? '<b>Best: </b>' : '') + t.label + ' (' + t.pathway + ')</span>' +
          '<span>' + t.comparison.reduction_pct + '% reduction, ' + t.comparison.displacement_pct + '% displacement' +
          (t.loss_avoided_pkr !== null && t.loss_avoided_pkr !== undefined ?
            ', Rs ' + Math.round(t.loss_avoided_pkr).toLocaleString() + ' loss avoided' : '') + '</span></div>'
        ).join('') +
        '<p class="note">Each row is a real re-run, not an estimate -- ranked by reduction minus half the displacement.</p>' +
        '<button class="secondary" onclick="showOnMap(\\'/nbs/' + p.result.best_job_id + '/mapbox3d\\')">Show best on map</button>';
      }});
    }};
    setTimeout(check, 1000);
  }});
}}

function showGlobalDemPrompt(msg) {{
  const el = document.getElementById('globalDemPrompt');
  el.style.display = 'block';
  el.querySelector('p').textContent = msg;
  document.getElementById('useGlobalDem').parentElement.style.outline = '2px solid #d9822b';
}}

// Tier 0/2's own "precomputed inundation-extent library per discharge
// level" -- fetched ONCE per AOI and cached here, so dragging the
// discharge slider afterward interpolates instantly client-side instead
// of a fresh server round-trip (and a fresh Manning's-equation solve)
// per drag tick. The "Preview" button still does a real server call for
// the actual map overlay at the exact chosen value.
let instantFloodLibrary = null;

function fetchInstantFloodLibrary() {{
  instantFloodLibrary = null;
  if (!currentGeoJSON) return;
  fetch('/area/instant-flood-library', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked}})
  }}).then(r => r.json()).then(d => {{
    if (d.error || !d.library || !d.library.length) return;  // silent -- slider still works, just without instant preview
    instantFloodLibrary = d.library;
    const lo = Math.log10(instantFloodLibrary[0].discharge_m3s);
    const hi = Math.log10(instantFloodLibrary[instantFloodLibrary.length - 1].discharge_m3s);
    const slider = document.getElementById('instantDischargeSlider');
    slider.min = lo; slider.max = hi;
    syncInstantDischargeSlider('number');  // reflect the number input's current value onto the newly-ranged slider
  }});
}}

function interpolateInstantFlood(discharge) {{
  if (!instantFloodLibrary || !instantFloodLibrary.length) return null;
  const lib = instantFloodLibrary;
  if (discharge <= lib[0].discharge_m3s) return lib[0];
  if (discharge >= lib[lib.length - 1].discharge_m3s) return lib[lib.length - 1];
  let i = 0;
  while (i < lib.length - 1 && lib[i + 1].discharge_m3s < discharge) i++;
  const a = lib[i], b = lib[i + 1];
  // Log-log interpolation -- the library's own levels are log-spaced
  // (see hand_utils.DEFAULT_LIBRARY_LEVELS_M3S) and depth/area vs.
  // discharge is a real power-law-like relationship, not linear.
  const la = Math.log(a.discharge_m3s), lb = Math.log(b.discharge_m3s), ld = Math.log(discharge);
  const frac = (lb - la) > 1e-12 ? (ld - la) / (lb - la) : 0;
  return {{
    flooded_area_km2: a.flooded_area_km2 + frac * (b.flooded_area_km2 - a.flooded_area_km2),
    max_depth_m: a.max_depth_m + frac * (b.max_depth_m - a.max_depth_m),
  }};
}}

function syncInstantDischargeSlider(source) {{
  const numInput = document.getElementById('instantDischarge');
  const slider = document.getElementById('instantDischargeSlider');
  let discharge;
  if (source === 'slider') {{
    discharge = Math.pow(10, parseFloat(slider.value));
    numInput.value = discharge.toFixed(discharge < 10 ? 1 : 0);
  }} else {{
    discharge = parseFloat(numInput.value) || 0;
    if (discharge > 0) slider.value = Math.log10(discharge);
  }}
  const est = interpolateInstantFlood(discharge);
  const box = document.getElementById('instantFloodBox');
  if (est) {{
    box.innerHTML = '<div class="stat-row"><span>Flooded area (instant estimate)</span><span>' + est.flooded_area_km2.toFixed(3) + ' km&sup2;</span></div>' +
      '<div class="stat-row"><span>Max depth (instant estimate)</span><span>' + est.max_depth_m.toFixed(2) + ' m</span></div>' +
      '<p class="note" style="margin-top:4px;">Interpolated from the precomputed discharge library -- click Preview for the real map overlay at this exact value.</p>';
  }}
}}

function runInstantFlood() {{
  const box = document.getElementById('instantFloodBox');
  box.innerHTML = '<p class="note">Computing HAND + rating curve...</p>';
  fetch('/area/instant-flood', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, discharge_m3s: parseFloat(document.getElementById('instantDischarge').value), use_global_dem: document.getElementById('useGlobalDem').checked}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{
      box.innerHTML = '<p class="note">' + d.error + '</p>';
      if (d.needs_global_dem_consent) showGlobalDemPrompt(d.error);
      return;
    }}
    box.innerHTML =
      '<div class="stat-row"><span>DEM source</span><span>' + d.dem_source + '</span></div>' +
      '<div class="stat-row"><span>Flooded area</span><span>' + d.flooded_area_km2 + ' km&sup2;</span></div>' +
      '<div class="stat-row"><span>Max depth</span><span>' + d.max_depth_m + ' m</span></div>' +
      '<div class="stat-row"><span>Wet / total cells</span><span>' + d.wet_cells + ' / ' + d.total_cells + '</span></div>';
    loadTier3D(2);
    setViewMode('main');
  }});
}}

function boundaryItemToRequest(b) {{
  return {{edge: b.edge, lon: b.lon, lat: b.lat, pos_pct: b.pos_pct, width_m: b.width_m,
           peak_m3s: b.peak_m3s, duration_hr: b.duration_hr, rise_frac: b.rise_frac, name: b.name,
           series: b.series || null}};
}}

function detectAllBoundaries() {{
  if (!currentGeoJSON) return;
  const note = document.getElementById('boundaryDetectNote');
  const btn = document.getElementById('detectBoundariesBtn');
  btn.disabled = true;
  note.textContent = 'Looking for every real river/stream/canal crossing this area\\'s true boundary (360°)...';
  fetch('/area/detect-boundaries', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      btn.disabled = false;
      if (d.error) {{ note.textContent = 'Detection failed: ' + d.error; return; }}
      const crossings = d.crossings || [];
      // Real detected items replace any PREVIOUS detected items (not
      // manual/GLOF ones) -- re-detecting shouldn't duplicate.
      boundaryItems = boundaryItems.filter(b => b.source !== 'detected');
      for (const c of crossings) {{
        const disc = c.discharge || {{}};
        boundaryItems.push({{
          id: boundaryIdSeq++, type: c.direction, edge: c.edge, lon: c.lon, lat: c.lat,
          pos_pct: null, width_m: c.channel_width_m,
          peak_m3s: c.direction === 'inflow' ? Math.round((disc.recent_m3s || disc.mean_m3s || 0) * 10) / 10 : null,
          duration_hr: c.direction === 'inflow' ? 3 : 0, rise_frac: 0.5,
          name: c.name, source: 'detected', included: true,
          note: c.direction === 'inflow' ? (disc.error ? 'discharge lookup failed: ' + disc.error : 'real GloFAS discharge auto-filled') : null,
        }});
      }}
      note.textContent = crossings.length
        ? `Found ${{crossings.length}} real crossing(s) -- edit or remove any below before running.`
        : 'No real river/stream/canal found crossing this area\\'s boundary (pluvial-only is fine).';
      renderBoundaryList();
    }})
    .catch(e => {{ btn.disabled = false; note.textContent = 'Detection failed: ' + e; }});
}}

function addManualBoundary(type) {{
  boundaryItems.push({{
    id: boundaryIdSeq++, type, edge: 'N', lon: null, lat: null, pos_pct: 50, width_m: 30,
    peak_m3s: type === 'inflow' ? 100 : null, duration_hr: type === 'inflow' ? 0 : 0, rise_frac: 0.5,
    name: (type === 'inflow' ? 'Manual inflow' : 'Manual outflow'), source: 'manual', included: true,
  }});
  renderBoundaryList();
}}

function removeBoundaryItem(id) {{
  boundaryItems = boundaryItems.filter(b => b.id !== id);
  renderBoundaryList();
}}

function updateBoundaryField(id, field, value) {{
  const b = boundaryItems.find(x => x.id === id);
  if (!b) return;
  b[field] = value;
  updateBoundaryMarkers();
}}

function renderBoundaryList() {{
  const el = document.getElementById('boundaryList');
  if (!el) return;
  updateBoundaryMarkers();
  if (!boundaryItems.length) {{ el.innerHTML = '<p class="note">No inflows/outflows -- pluvial-only run.</p>'; return; }}
  el.innerHTML = boundaryItems.map(b => {{
    const isInflow = b.type === 'inflow';
    const siteInfo = b.lon != null
      ? `real crossing (${{b.lat.toFixed(3)}}°N, ${{b.lon.toFixed(3)}}°E) → ${{b.edge}} edge`
      : `manual, ${{b.edge}} edge`;
    return `<div style="border:1px solid var(--line);border-radius:6px;padding:8px;margin-top:6px;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <label style="display:flex;align-items:center;gap:6px;font-weight:normal;margin:0;">
          <input type="checkbox" ${{b.included ? 'checked' : ''}} onchange="updateBoundaryField(${{b.id}},'included',this.checked)" style="width:auto;">
          <b>${{isInflow ? '↘ Inflow' : '↗ Outflow'}}: ${{b.name}}</b>
        </label>
        <button class="secondary" style="padding:2px 8px;" onclick="removeBoundaryItem(${{b.id}})">Remove</button>
      </div>
      <p class="note" style="margin:4px 0;">${{siteInfo}}${{b.note ? ' — ' + b.note : ''}}</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
        ${{b.source === 'manual' ? `<div><label style="font-weight:normal;">Edge</label>
          <select onchange="updateBoundaryField(${{b.id}},'edge',this.value)">
            <option value="N" ${{b.edge==='N'?'selected':''}}>North</option><option value="E" ${{b.edge==='E'?'selected':''}}>East</option>
            <option value="S" ${{b.edge==='S'?'selected':''}}>South</option><option value="W" ${{b.edge==='W'?'selected':''}}>West</option>
          </select></div>
          <div><label style="font-weight:normal;">Position along edge (%)</label>
          <input type="number" min="0" max="100" value="${{b.pos_pct}}" onchange="updateBoundaryField(${{b.id}},'pos_pct',parseFloat(this.value))"></div>` : ''}}
        <div><label style="font-weight:normal;">Width (m)</label>
          <input type="number" min="1" value="${{b.width_m}}" onchange="updateBoundaryField(${{b.id}},'width_m',parseFloat(this.value))"></div>
        ${{isInflow ? `<div><label style="font-weight:normal;">Peak discharge (m&sup3;/s)</label>
          <input type="number" min="0" value="${{b.peak_m3s}}" onchange="updateBoundaryField(${{b.id}},'peak_m3s',parseFloat(this.value))"></div>
          <div><label style="font-weight:normal;">Duration (hr, 0=constant)</label>
          <input type="number" min="0" step="0.5" value="${{b.duration_hr}}" onchange="updateBoundaryField(${{b.id}},'duration_hr',parseFloat(this.value))"></div>` : ''}}
      </div>
    </div>`;
  }}).join('');
}}


function renderGlofBand(b) {{
  if (!b) return '';
  const e = b.documented_equivalent_m3s;
  return '<div class="stat-row"><span>Documented-event range</span><span>' + e.low.toLocaleString() + ' &ndash; ' + e.high.toLocaleString() + ' m&sup3;/s</span></div>' +
    '<p class="note">Popov overestimates by a median ' + b.ratio_median + '&times; on ' + b.n_events + ' documented ' + b.basis + ' events (central ' + e.central.toLocaleString() + ' m&sup3;/s; spread ' + b.popov_over_observed_p10_p90.join('&times; to ') + '&times;).' +
    (b.caveat ? ' <b>Caution: ' + b.caveat + '.</b>' : '') + ' ' + b.note + '</p>';
}}
function findNearbyPmdLakes() {{
  const resultBox = document.getElementById('glofPmdResult'), picker = document.getElementById('glofPmdPicker');
  if (!currentGeoJSON) {{ resultBox.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  resultBox.innerHTML = '<p class="note">Searching the PMD inventory near this AOI...</p>'; picker.style.display = 'none';
  fetch('/area/pmd-lakes-nearby', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ resultBox.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      if (!d.lakes.length) {{ resultBox.innerHTML = '<p class="note">No inventory lake within ' + d.radius_km + ' km of this AOI.</p>'; return; }}
      picker.innerHTML = d.lakes.map(l => '<option value="' + l.pmd_id + '">#' + l.pmd_id + ' &mdash; ' + l.distance_km + ' km, ' + Math.round(l.area_m2).toLocaleString() + ' m&sup2;, ' + l.dam_type +
        (l.glacier_relation ? ', glacier: ' + l.glacier_relation : '') + (l.nearest_village ? ', near ' + l.nearest_village : '') + '</option>').join('');
      picker.style.display = '';
      resultBox.innerHTML = '<p class="note">' + d.lakes.length + ' lake(s) found (nearest first) &mdash; pick one, then "Apply GLOF discharge".</p>';
    }});
}}
function addGlofReplayInflow(peak, durationHr, riseFrac, name) {{
  boundaryItems.push({{
    id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: null, lat: null, pos_pct: 50, width_m: 30, peak_m3s: peak, duration_hr: durationHr,
    rise_frac: riseFrac, name: name, source: 'glof', included: true, note: 'edit edge/position below to site it, or use "Detect all rivers automatically"'}});
  renderBoundaryList();
}}
let glofEventsList = [];
function showGlofHistoricalEvents() {{
  const box = document.getElementById('glofEventsBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  box.innerHTML = '<p class="note">Looking up documented events...</p>';
  Promise.all([
    fetch('/glof/historical-events', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON, radius_km: 100}})}}).then(r => r.json()),
    fetch('/glof/popov-validation').then(r => r.json()).catch(() => null)
  ]).then(([d, v]) => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    glofEventsList = d.events;
    if (!d.events.length) {{ box.innerHTML = '<p class="note">No documented event within ' + d.radius_km + ' km. (Absence of a record is not absence of an outburst.)</p>'; return; }}
    const rows = d.events.map((e, i) => {{
      const bits = [];
      if (e.peak_discharge_m3s != null) bits.push(Math.round(e.peak_discharge_m3s).toLocaleString() + ' m&sup3;/s');
      if (e.volume_m3 != null) bits.push(Math.round(e.volume_m3).toLocaleString() + ' m&sup3;');
      if (e.lives != null) bits.push(e.lives + ' lives');
      const usable = (e.peak_discharge_m3s != null || e.volume_m3 != null);
      return '<div class="stat-row" style="align-items:flex-start;"><span><b>' + (e.year != null ? Math.round(e.year) : '?') + '</b> ' + e.lake_name + ' &middot; ' + e.lake_type + (e.mechanism && e.mechanism !== 'Unknown' ? ' &middot; ' + e.mechanism : '') + '<br><span class="note">' + e.distance_km + ' km away' + (bits.length ? ' &middot; ' + bits.join(' &middot; ') : '') + (e.impact ? ' &middot; ' + e.impact : '') + '</span></span>' +
        (usable ? '<button class="secondary" style="margin-left:6px;" onclick="useGlofEvent(' + i + ')">Use</button>' : '') + '</div>';
    }}).join('');
    const c = d.catalogue;
    let val = '';
    if (v && v.by_type) {{
      val = '<p class="note"><b>How good is the Popov formula?</b> Popov / documented peak discharge, median (events): ' + Object.entries(v.by_type).map(([k, s]) => k + ' ' + s.ratio_median.toFixed(1) + '&times; (' + s.n + ')').join('; ') + '. Ice-dammed lakes, most of Pakistan\\'s events, are badly overestimated, on very few events.</p>';
    }}
    box.innerHTML = '<p class="note">' + d.events.length + ' event(s) within ' + d.radius_km + ' km. Catalogue: ' + c.pakistan_events + ' Pakistan events, only ' + c.pakistan_with_volume + ' with a volume and ' + c.pakistan_with_peak_discharge + ' with a peak discharge. ' + d.source + '</p>' + rows + val;
  }});
}}
function useGlofEvent(i) {{
  const e = glofEventsList[i], box = document.getElementById('glofBox');
  const nm = 'GLOF replay: ' + e.lake_name + ' ' + (e.year != null ? Math.round(e.year) : '');
  if (e.peak_discharge_m3s != null) {{
    addGlofReplayInflow(e.peak_discharge_m3s, 6, 0.2, nm);
    box.innerHTML = '<p class="note">Added the documented peak discharge (' + Math.round(e.peak_discharge_m3s).toLocaleString() + ' m&sup3;/s) of ' + e.lake_name + ' ' + Math.round(e.year) + ' as an inflow with a GLOF-shaped hydrograph. Site it in the boundary list above.</p>';
    return;
  }}
  fetch('/glof/discharge', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{volume_m3: e.volume_m3, lake_type: e.lake_type}})}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      addGlofReplayInflow(d.peak_discharge_m3s, d.suggested_duration_hr, d.suggested_rise_frac, nm);
      box.innerHTML = '<div class="stat-row"><span>Peak discharge (Popov from documented volume)</span><span>' + d.peak_discharge_m3s + ' m&sup3;/s</span></div>' + renderGlofBand(d.empirical_band) +
        '<p class="note">Added as an inflow. Site it in the boundary list above.</p>';
    }});
}}

function updateGlofInputs() {{
  const src = document.getElementById('glofSource').value;
  document.getElementById('glofVolumeRow').style.display = (src === 'volume') ? '' : 'none';
  document.getElementById('glofAreaRow').style.display = (src === 'area') ? '' : 'none';
  document.getElementById('glofHydrolakesRow').style.display = (src === 'hydrolakes') ? '' : 'none';
  document.getElementById('glofPmdRow').style.display = (src === 'pmd') ? '' : 'none';
  // The curated Shishper preset is a real documented EVENT (fixed real
  // peak discharge, not a formula) -- the trigger/breach-formula choice
  // only applies to a formula-derived (volume/area/HydroLAKES) estimate.
  document.getElementById('glofTriggerRow').style.display = (src === 'shishper_2022') ? 'none' : '';
}}

function findNearbyHydrolakes() {{
  const resultBox = document.getElementById('glofHydrolakesResult');
  const picker = document.getElementById('glofHydrolakesPicker');
  if (!currentGeoJSON) {{ resultBox.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  resultBox.innerHTML = '<p class="note">Searching real HydroLAKES data near this AOI...</p>';
  picker.style.display = 'none';
  fetch('/area/hydrolakes-nearby', {{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ resultBox.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    if (!d.lakes.length) {{
      resultBox.innerHTML = '<p class="note">No real HydroLAKES lake with a usable reported volume found within ' + d.radius_km + 'km of this AOI.</p>';
      return;
    }}
    picker.innerHTML = d.lakes.map(l =>
      '<option value="' + l.hydrolakes_id + '">' + (l.name || ('Unnamed lake #' + l.hydrolakes_id)) +
      ' -- ' + l.distance_km + 'km away, ' + Math.round(l.volume_m3).toLocaleString() + ' m&sup3;' +
      (l.elevation_m != null ? ', ' + l.elevation_m + 'm elev.' : '') + '</option>').join('');
    picker.style.display = '';
    resultBox.innerHTML = '<p class="note">' + d.lakes.length + ' real lake(s) found -- pick one above, then click "Apply GLOF discharge".</p>';
  }});
}}

let lastGlofResult = null, lastGlofLabel = '';
function downloadResultPdf(url, payload) {{
  fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(payload)}})
    .then(r => r.ok ? r.blob() : r.json().then(j => {{ throw new Error(j.error || 'failed'); }}))
    .then(b => {{ const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = url.split('/').pop(); a.click(); }})
    .catch(e => alert('PDF export failed: ' + e.message));
}}
function downloadGlofPdf() {{ downloadResultPdf('/report/glof.pdf', {{result: lastGlofResult, label: lastGlofLabel}}); }}

function applyGlofScenario() {{
  const src = document.getElementById('glofSource').value;
  const box = document.getElementById('glofBox');
  const body = {{}};
  if (src === 'volume') {{
    const v = parseFloat(document.getElementById('glofVolumeM3').value);
    if (!v || v <= 0) {{ box.innerHTML = '<p class="note">Enter a real lake volume first.</p>'; return; }}
    body.volume_m3 = v;
  }} else if (src === 'area') {{
    const a = parseFloat(document.getElementById('glofAreaKm2').value);
    if (!a || a <= 0) {{ box.innerHTML = '<p class="note">Enter a real lake area first.</p>'; return; }}
    body.area_m2 = a * 1e6;
  }} else if (src === 'hydrolakes') {{
    const picker = document.getElementById('glofHydrolakesPicker');
    if (!picker.value) {{ box.innerHTML = '<p class="note">Click "Find nearby real lakes" and pick one first.</p>'; return; }}
    body.hydrolakes_id = parseInt(picker.value);
  }} else if (src === 'pmd') {{
    const picker = document.getElementById('glofPmdPicker');
    if (!picker.value) {{ box.innerHTML = '<p class="note">Click "Find nearby inventory lakes" and pick one first.</p>'; return; }}
    body.pmd_lake_id = parseInt(picker.value);
  }} else {{
    body.lake_key = src;
  }}
  if (src !== 'shishper_2022') {{
    body.trigger = document.getElementById('glofTrigger').value;
    if (body.trigger === 'landslide_breach') {{
      body.breach_height_m = parseFloat(document.getElementById('glofBreachHeightM').value);
    }}
  }}
  box.innerHTML = '<p class="note">Computing real GLOF breach discharge...</p>';
  fetch('/glof/discharge', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      let siteNote = '';
      let siteLat = null, siteLon = null;
      if (d.downstream_lat != null) {{
        siteLat = d.downstream_lat; siteLon = d.downstream_lon;
        siteNote = '<p class="note">Draw your AOI around <b>' + d.downstream_lat.toFixed(3) + '&deg;N, ' +
          d.downstream_lon.toFixed(3) + '&deg;E</b> (the real downstream community), then click "Detect all rivers automatically" below to site this inflow -- or add it as a manual inflow if detection misses it.</p>';
      }} else if (d.lake_lat != null) {{
        siteLat = d.lake_lat; siteLon = d.lake_lon;
        siteNote = '<p class="note">The real lake itself is at <b>' + d.lake_lat.toFixed(3) + '&deg;N, ' +
          d.lake_lon.toFixed(3) + '&deg;E</b> -- draw your AOI somewhere real and downstream of it (HydroLAKES has no documented downstream community for this lake, unlike the curated Shishper preset), then site this inflow manually or via "Detect all rivers automatically".</p>';
      }}
      if (siteLat != null) {{
        siteNote += '<button class="secondary" onclick="findBasinNearGlofSite(' + siteLon + ',' + siteLat + ')">Find real basin near this site &rarr;</button><div id="glofBasinBox"></div>';
      }}
      lastGlofResult = d; lastGlofLabel = (src === 'hydrolakes' ? document.getElementById('glofHydrolakesPicker').selectedOptions[0].text : (src === 'pmd' ? document.getElementById('glofPmdPicker').selectedOptions[0].text : document.getElementById('glofSource').selectedOptions[0].text));
      boundaryItems.push({{
        id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: null, lat: null, pos_pct: 50,
        width_m: 30, peak_m3s: d.peak_discharge_m3s, duration_hr: d.suggested_duration_hr,
        rise_frac: d.suggested_rise_frac, name: 'GLOF: ' + (src === 'hydrolakes'
          ? document.getElementById('glofHydrolakesPicker').selectedOptions[0].text
          : (src === 'pmd' ? document.getElementById('glofPmdPicker').selectedOptions[0].text
          : document.getElementById('glofSource').selectedOptions[0].text)),
        source: 'glof', included: true, note: 'edit edge/position below to site it, or use "Detect all rivers automatically"',
      }});
      renderBoundaryList();
      box.innerHTML =
        '<div class="stat-row"><span>Peak discharge</span><span>' + d.peak_discharge_m3s + ' m&sup3;/s</span></div>' +
        '<div class="stat-row"><span>Volume used</span><span>' + Math.round(d.volume_m3).toLocaleString() + ' m&sup3;</span></div>' +
        '<p class="note">' + d.method + (d.note ? ' ' + d.note : '') + '</p>' +
        renderGlofBand(d.empirical_band) +
        siteNote +
        '<p class="note">Added as a new inflow (Peak=' + d.peak_discharge_m3s + ' m&sup3;/s, Duration=' + d.suggested_duration_hr +
        'h) with a real GLOF-shaped hydrograph (fast rise, slow recession) in the Boundary conditions list above -- edit its site/edge there.</p>' +
        '<button class="secondary" onclick="useGlofDischargeInTier2(' + d.peak_discharge_m3s + ')">Preview instant flood extent (Tier 2, no siting needed) &rarr;</button>' +
        '<p class="note" style="margin-top:4px;">Unlike the Tier 3 run above, Tier 2\\'s HAND-based preview needs no downstream siting -- just this real peak discharge applied to whatever AOI is currently drawn.</p>' +
        '<button class="secondary" style="margin-top:6px;" onclick="downloadGlofPdf()">Export PDF summary</button>';
    }});
}}

function computeSnowmelt() {{
  const box = document.getElementById('srmBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  box.innerHTML = '<p class="note">Computing real SRM snowmelt discharge (real temperature fetch + elevation-band melt)...</p>';
  fetch('/area/snowmelt-discharge', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked,
      snow_line_elev_m: parseFloat(document.getElementById('srmSnowLine').value),
      ddf_mm_per_c_per_day: parseFloat(document.getElementById('srmDdf').value),
      lapse_rate_c_per_m: parseFloat(document.getElementById('srmLapseRate').value) / 1000.0,
    }})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + (d.needs_global_dem_consent ? ' Enable "Use open-source global DEM" above.' : '') + '</p>'; return; }}
    box.innerHTML =
      '<div class="stat-row"><span>Real snowmelt discharge</span><span>' + d.discharge_m3s + ' m&sup3;/s</span></div>' +
      '<div class="stat-row"><span>Snow-covered area (&ge; snow line)</span><span>' + d.snow_covered_area_km2 + ' km&sup2;</span></div>' +
      '<div class="stat-row"><span>Reference temp / elevation</span><span>' + d.ref_temp_c + '&deg;C @ ' + Math.round(d.ref_elev_m) + 'm</span></div>' +
      '<p class="note">' + d.bands.length + ' real elevation band(s) at/above the snow line, each lapse-adjusted from the real reference temperature (' + d.ref_temp_source + '). Degree-day factor ' + d.ddf_mm_per_c_per_day + ' mm/&deg;C/day, lapse rate ' + (d.lapse_rate_c_per_m*1000).toFixed(1) + '&deg;C/km.</p>' +
      '<button class="secondary" onclick="addSnowmeltAsInflow(' + d.discharge_m3s + ', ' + d.suggested_duration_hr + ')">Add as a real inflow &rarr;</button>' +
      '<p class="note" style="margin-top:4px;">Added as a real, roughly steady (not sharply-peaked like a GLOF) inflow over ' + d.suggested_duration_hr + 'h -- edit its site/edge in the Boundary conditions list above. Can be combined with a GLOF inflow above to represent snowmelt feeding the lake alongside a separate breach event.</p>';
  }});
}}

let lastMeltSeries = null;
function computeSnowmeltHydrograph() {{
  const box = document.getElementById('srmHydroBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  box.innerHTML = '<p class="note">Fetching real temperature forecast and computing daily melt...</p>';
  fetch('/area/snowmelt-hydrograph', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked,
      snow_line_elev_m: parseFloat(document.getElementById('srmSnowLine').value),
      ddf_mm_per_c_per_day: parseFloat(document.getElementById('srmDdf').value),
      lapse_rate_c_per_m: parseFloat(document.getElementById('srmLapseRate').value) / 1000.0,
      forecast_days: parseInt(document.getElementById('srmDays').value),
    }})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    lastMeltSeries = d;
    const rows = d.times_hr.map((t, i) => '<tr><td>day ' + (i + 1) + '</td><td>' + d.daily_mean_temp_c[i] + '&deg;C</td><td>' + d.q_m3s[i] + ' m&sup3;/s</td></tr>').join('');
    box.innerHTML =
      '<div class="stat-row"><span>Peak melt discharge</span><span>' + d.peak_m3s + ' m&sup3;/s</span></div>' +
      '<div class="stat-row"><span>Total melt over ' + d.n_days + ' days</span><span>' + Math.round(d.total_melt_m3).toLocaleString() + ' m&sup3;</span></div>' +
      '<table style="width:100%;font-size:12px;"><tr><th align="left">Day</th><th align="left">Mean temp @ ref</th><th align="left">Melt</th></tr>' + rows + '</table>' +
      '<p class="note">' + d.temp_source + ' (starting ' + d.forecast_start + '). Snow line held constant over the period (no forecast of snow-line retreat).</p>' +
      '<button class="secondary" onclick="addSnowmeltSeriesAsInflow()">Add as time-varying inflow &rarr;</button>';
  }});
}}

function addSnowmeltSeriesAsInflow() {{
  if (!lastMeltSeries) return;
  boundaryItems.push({{
    id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: null, lat: null, pos_pct: 50,
    width_m: 30, peak_m3s: lastMeltSeries.peak_m3s, duration_hr: lastMeltSeries.times_hr[lastMeltSeries.times_hr.length - 1],
    rise_frac: 0.5, name: 'Snowmelt forecast (SRM, ' + lastMeltSeries.n_days + ' d)', source: 'srm',
    series: {{times_hr: lastMeltSeries.times_hr, q_m3s: lastMeltSeries.q_m3s}}, included: true,
    note: 'real multi-day forecast melt curve -- edit edge/position below to site it',
  }});
  renderBoundaryList();
}}

let lastMeltModel = null;
function runMeltModel() {{
  const box = document.getElementById('meltBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw or select a basin first.</p>'; return; }}
  document.getElementById('meltBtn').disabled = true;
  fetch('/area/melt-model', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, start: document.getElementById('meltStart').value,
      end: document.getElementById('meltEnd').value,
      lapse_rate_c_per_km: parseFloat(document.getElementById('meltLapse').value),
      precip_correction: parseFloat(document.getElementById('meltPrecip').value),
      ddf_snow: parseFloat(document.getElementById('meltDdfSnow').value),
      ddf_ice: parseFloat(document.getElementById('meltDdfIce').value),
      temp_bias_c: parseFloat(document.getElementById('meltTempBias').value)}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; document.getElementById('meltBtn').disabled = false; return; }}
    pollJob(d.job_id, box, res => {{
      document.getElementById('meltBtn').disabled = false;
      lastMeltModel = res;
      const s = res.summary;
      box.innerHTML =
        '<div class="stat-row"><span>Basin</span><span>' + res.meta.basin_area_km2.toFixed(0) + ' km&sup2; (' + res.meta.cellsize_m + ' m cells, ' + res.meta.glacier_polygons + ' glacier polygons)</span></div>' +
        '<div class="stat-row"><span>Peak outlet discharge</span><span>' + s.peak_m3s.toFixed(1) + ' m&sup3;/s on ' + s.peak_day + '</span></div>' +
        '<div class="stat-row"><span>Mean discharge</span><span>' + s.mean_m3s.toFixed(1) + ' m&sup3;/s</span></div>' +
        '<div class="stat-row"><span>Total volume</span><span>' + Math.round(s.volume_m3 / 1e6).toLocaleString() + ' million m&sup3;</span></div>' +
        meltMbHtml(res.glacier_mass_balance_m_we_yr) +
        meltCheckHtml(res.glofas_check) +
        '<canvas id="meltChart" width="360" height="130" style="width:100%;max-width:360px;"></canvas>' +
        '<button class="secondary" onclick="addMeltPeakAsInflow()">Add peak window (&plusmn;3 days) as time-varying inflow &rarr;</button>' +
        '<p class="note">' + res.limitations.join(' ') + (res.cached ? ' (cached result)' : '') + '</p>';
      drawMeltChart(res.q_m3s);
    }}, 'Preparing basin inputs and running the melt model (1-3 minutes)...');
  }});
}}

function meltMbHtml(mb) {{
  if (mb === null || mb === undefined) return '';
  const bad = mb < -1.5;
  return '<div class="stat-row"' + (bad ? ' style="color:#ffb454;"' : '') + '><span>Glacier mass balance</span><span>' + mb.toFixed(2) + ' m w.e./yr' +
    (bad ? ' (implausible; region is about -0.1 to -0.5)' : '') + '</span></div>';
}}

function calibrateMeltModel() {{
  const box = document.getElementById('meltCalBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw or select a basin first.</p>'; return; }}
  const cs = document.getElementById('meltStart').value, ce = document.getElementById('meltEnd').value;
  const back = d => {{ const t = new Date(d); t.setFullYear(t.getFullYear() - 1); return t.toISOString().slice(0, 10); }};
  document.getElementById('meltCalBtn').disabled = true;
  fetch('/area/melt-model/calibrate', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, cal_start: cs, cal_end: ce, val_start: back(cs), val_end: back(ce)}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; document.getElementById('meltCalBtn').disabled = false; return; }}
    pollJob(d.job_id, box, res => {{
      document.getElementById('meltCalBtn').disabled = false;
      if (!res.available) {{ box.innerHTML = '<p class="note">' + res.reason + '</p>'; return; }}
      const p = res.recommended, v = res.validation_recommended, vd = res.validation_defaults;
      const f = x => (x === null || x === undefined) ? '&ndash;' : x.toFixed(2);
      box.innerHTML =
        '<div class="stat-row"><span>Best factors</span><span>T ' + (p.temp_bias_c >= 0 ? '+' : '') + p.temp_bias_c + ' &deg;C, precip &times;' + p.precip_correction + ', DDF ' + p.ddf_snow + '/' + p.ddf_ice + '</span></div>' +
        '<div class="stat-row"><span>Calibration year</span><span>KGE ' + f(res.calibration.kge) + ', bias ' + res.calibration.bias_pct.toFixed(0) + '%, glacier MB ' + f(res.calibration.glacier_mb_m_we_yr) + '</span></div>' +
        '<div class="stat-row"><span>Hold-out year</span><span>KGE ' + f(v.kge) + ', bias ' + (v.bias_pct === null ? '&ndash;' : v.bias_pct.toFixed(0) + '%') + ' (defaults: KGE ' + f(vd.kge) + ')</span></div>' +
        '<p class="note"' + (res.transferable ? '' : ' style="color:#ffb454;"') + '>' + (res.transferable ? '' : '&#9888; ') + res.note + ' ' + res.n_physical + ' of ' + res.n_trials + ' trials had a plausible glacier mass balance.</p>' +
        (res.transferable ? '<button class="secondary" onclick="applyMeltCalibration()">Use these factors</button>' : '');
      window.lastMeltCalibration = res;
    }}, 'Calibrating: up to 100 model runs over two years, about 10 minutes (cached after)...');
  }});
}}

function applyMeltCalibration() {{
  const p = (window.lastMeltCalibration || {{}}).recommended; if (!p) return;
  document.getElementById('meltPrecip').value = p.precip_correction;
  document.getElementById('meltDdfSnow').value = p.ddf_snow;
  document.getElementById('meltDdfIce').value = p.ddf_ice;
  document.getElementById('meltTempBias').value = p.temp_bias_c;
}}

function meltCheckHtml(c) {{
  if (!c || !c.available) return '<p class="note">GloFAS comparison unavailable' + (c && c.reason ? ': ' + c.reason : '') + '.</p>';
  const line = 'vs GloFAS (Apr-Sep): KGE ' + c.kge.toFixed(2) + ', bias ' + c.bias_pct.toFixed(0) + '%, r ' + c.r.toFixed(2) + ', peak ' + c.sim_peak.toFixed(0) + ' vs ' + c.glofas_peak.toFixed(0) + ' m&sup3;/s';
  if (c.warning) return '<div class="stat-row" style="color:#ffb454;"><span>' + line + '</span></div><p class="note" style="color:#ffb454;">&#9888; ' + c.warning + '</p>';
  return '<div class="stat-row"><span>' + line + '</span></div>';
}}

function drawMeltChart(q) {{
  const c = document.getElementById('meltChart'); if (!c) return;
  const g = c.getContext('2d'), W = c.width, H = c.height, m = 4;
  const mx = Math.max.apply(null, q) || 1;
  g.clearRect(0, 0, W, H);
  g.strokeStyle = '#2bc4b4'; g.lineWidth = 1.5; g.beginPath();
  q.forEach((v, i) => {{
    const x = m + (W - 2 * m) * i / Math.max(q.length - 1, 1), y = H - m - (H - 2 * m) * v / mx;
    if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
  }});
  g.stroke();
  g.fillStyle = '#9fb3b8'; g.font = '10px sans-serif'; g.fillText(mx.toFixed(0) + ' m3/s', m + 2, 12);
}}

function addMeltPeakAsInflow() {{
  if (!lastMeltModel) return;
  fetch('/area/melt-model/inflow-series', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{dates: lastMeltModel.dates, q_m3s: lastMeltModel.q_m3s, half_window_days: 3}})
  }}).then(r => r.json()).then(ser => {{
    const pk = Math.max.apply(null, ser.q_m3s);
    boundaryItems.push({{
      id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: null, lat: null, pos_pct: 50,
      width_m: 30, peak_m3s: pk, duration_hr: ser.times_hr[ser.times_hr.length - 1],
      rise_frac: 0.5, name: 'Melt model, ' + ser.window[0] + ' to ' + ser.window[1], source: 'melt-model',
      series: {{times_hr: ser.times_hr, q_m3s: ser.q_m3s}}, included: true,
      note: 'daily mean discharge from the basin melt model -- edit edge/position below to site it',
    }});
    renderBoundaryList();
  }});
}}

function addSnowmeltAsInflow(dischargeM3s, durationHr) {{
  boundaryItems.push({{
    id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: null, lat: null, pos_pct: 50,
    width_m: 30, peak_m3s: dischargeM3s, duration_hr: durationHr, rise_frac: 0.5,
    name: 'Snowmelt (SRM)', source: 'srm', included: true,
    note: 'edit edge/position below to site it, or use "Detect all rivers automatically"',
  }});
  renderBoundaryList();
}}

// Real, documented dam spillway events (dam_spillway_opening_events.csv,
// see dams_utils.py) -- loaded once on page load (small, static,
// nationwide data, not per-AOI), only events with a real numeric outflow
// are selectable (the rest are narrative-only rows in the source CSV and
// are deliberately not guessed at from free text).
let spillwayEvents = [];
function loadSpillwayEvents() {{
  fetch('/dams/spillway-events').then(r => r.json()).then(d => {{
    spillwayEvents = (d.events || []).filter(e => e.has_usable_outflow && e.lon != null);
    const sel = document.getElementById('spillwayEventSelect');
    sel.innerHTML = spillwayEvents.map((e, i) =>
      '<option value="' + i + '">' + e.dam + ' -- ' + e.date + ' (' + Math.round(e.outflow_m3s).toLocaleString() + ' m&sup3;/s)</option>').join('');
    sel.onchange = showSpillwayEventDetail;
    showSpillwayEventDetail();
  }});
}}

function showSpillwayEventDetail() {{
  const sel = document.getElementById('spillwayEventSelect');
  const box = document.getElementById('spillwayEventDetail');
  const e = spillwayEvents[parseInt(sel.value)];
  if (!e) {{ box.innerHTML = ''; return; }}
  box.innerHTML =
    '<div class="stat-row"><span>Real released outflow</span><span>' + Math.round(e.outflow_m3s).toLocaleString() + ' m&sup3;/s</span></div>' +
    (e.inflow_m3s ? '<div class="stat-row"><span>Real reservoir inflow</span><span>' + Math.round(e.inflow_m3s).toLocaleString() + ' m&sup3;/s</span></div>' : '') +
    (e.reservoir_level_m ? '<div class="stat-row"><span>Real reservoir level</span><span>' + e.reservoir_level_m + ' m</span></div>' : '') +
    '<p class="note">' + e.what_happened + ' (' + e.spillway + ') -- <a href="' + e.url + '" target="_blank">' + e.source + '</a></p>';
}}

function checkSpillwayOutlook() {{
  const e = spillwayEvents[parseInt(document.getElementById('spillwayEventSelect').value)];
  const box = document.getElementById('spillwayOutlookBox');
  if (!e) return;
  box.innerHTML = '<p class="note">Fetching the real GloFAS inflow forecast for ' + e.dam + '...</p>';
  fetch('/dams/spillway-outlook?dam=' + encodeURIComponent(e.dam) + '&days=7').then(r => r.json()).then(d => {{
    if (d.status !== 'ok') {{ box.innerHTML = '<p class="note"><b>No outlook for ' + d.dam + '.</b> ' + d.reason + '</p>'; return; }}
    const rows = d.forecast.map(x => '<tr' + (x.at_or_above ? ' style="color:#f0883e;font-weight:600;"' : '') + '><td>' + x.date + '</td><td>' +
      Math.round(x.inflow_m3s).toLocaleString() + ' m&sup3;/s</td><td>' + Math.round(x.ratio_to_threshold * 100) + '%</td></tr>').join('');
    box.innerHTML =
      '<div class="stat-row" style="font-weight:600;"><span>Days at/above the historical spill level</span><span>' + d.n_days_at_or_above + ' of ' + d.forecast.length + '</span></div>' +
      '<div class="stat-row"><span>Historical spill-opening inflow (lowest documented)</span><span>' + Math.round(d.threshold_m3s).toLocaleString() + ' m&sup3;/s</span></div>' +
      '<table style="width:100%;font-size:12px;"><tr><th align="left">Date</th><th align="left">Forecast inflow</th><th align="left">vs threshold</th></tr>' + rows + '</table>' +
      '<p class="note">' + d.feed + '. Feed check: ' + d.feed_validation + '</p><p class="note">' + d.caveat + '</p>';
  }});
}}

function addSpillwayEventAsInflow() {{
  const e = spillwayEvents[parseInt(document.getElementById('spillwayEventSelect').value)];
  const box = document.getElementById('spillwayBox');
  if (!e) {{ box.innerHTML = '<p class="note">No usable real event selected.</p>'; return; }}
  // Duration is a real, stated ESTIMATE, not a documented value -- the CSV
  // records when a release happened and how large it was, not how long it
  // lasted. 48h with a moderate rise reflects how these real releases are
  // described (sustained high-water operations, not a sharp breach peak).
  boundaryItems.push({{
    id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: e.lon, lat: e.lat, pos_pct: 50,
    width_m: 100, peak_m3s: e.outflow_m3s, duration_hr: 48, rise_frac: 0.3,
    name: 'Spillway: ' + e.dam + ' ' + e.date, source: 'spillway', included: true,
    note: 'real documented release at ' + e.dam + ' -- edit edge/position to site it on your AOI boundary',
  }});
  renderBoundaryList();
  box.innerHTML = '<p class="note">Added the real ' + Math.round(e.outflow_m3s).toLocaleString() +
    ' m&sup3;/s ' + e.dam + ' release as an inflow (duration 48h is an estimate -- the source records the release, not how long it lasted). Site it on your AOI\\'s boundary in the list above, or use "Detect all rivers automatically".</p>';
}}
loadSpillwayEvents();

let damFailureList = [];
function loadDamFailureList() {{
  fetch('/dams/failure-list').then(r => r.json()).then(d => {{
    damFailureList = (d.dams || []).filter(x => x.status !== 'unusable')
      .sort((a, b) => (b.volume_m3 || 0) - (a.volume_m3 || 0));
    document.getElementById('damFailureSelect').innerHTML = damFailureList.map(x =>
      '<option value="' + x.gdw_id + '">' + x.name + (x.river ? ' (' + x.river + ')' : '') +
      ' -- ' + (x.volume_m3 / 1e6).toLocaleString(undefined, {{maximumFractionDigits: 0}}) + ' MCM' +
      (x.status === 'estimated_height' ? ' [height estimated]' : '') + '</option>').join('');
  }});
}}
function runDamFailure() {{
  const box = document.getElementById('damFailureBox');
  const id = parseInt(document.getElementById('damFailureSelect').value);
  box.innerHTML = '<p class="note">Computing...</p>';
  fetch('/dams/failure-scenario', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{gdw_id: id}})}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      const rng = d.peak_range_m3s;
      boundaryItems.push({{
        id: boundaryIdSeq++, type: 'inflow', edge: 'N', lon: d.lon, lat: d.lat, pos_pct: 50,
        width_m: 100, peak_m3s: d.peak_m3s, duration_hr: d.duration_hr, rise_frac: 0.1,
        name: 'Dam failure: ' + d.name, source: 'dam_failure', included: true,
        note: 'hypothetical failure of a real dam -- edit edge/position to site it on your AOI boundary',
      }});
      renderBoundaryList();
      box.innerHTML =
        '<div class="stat-row"><span>Stored volume</span><span>' + (d.volume_m3 / 1e6).toLocaleString(undefined, {{maximumFractionDigits: 0}}) + ' MCM</span></div>' +
        '<div class="stat-row"><span>Dam height used</span><span>' + d.height_used_m + ' m' + (rng ? ' (estimated)' : ' (real)') + '</span></div>' +
        '<div class="stat-row" style="font-weight:600;"><span>Peak failure discharge</span><span>' + Math.round(d.peak_m3s).toLocaleString() + ' m&sup3;/s</span></div>' +
        (rng ? '<div class="stat-row"><span>Range (height uncertainty)</span><span>' + Math.round(rng.low).toLocaleString() + ' &ndash; ' + Math.round(rng.high).toLocaleString() + ' m&sup3;/s</span></div>' : '') +
        '<div class="stat-row"><span>Hydrograph duration</span><span>' + d.duration_hr + ' h</span></div>' +
        '<p class="note">' + d.method + '</p>' +
        '<p class="note">Added as an inflow at the dam\\'s real location -- site it on your AOI boundary in the list above.</p>';
    }});
}}
loadDamFailureList();

function renderLandslideDam(ld, simpleRating) {{
  if (!ld || (!ld.ml && !ld.valley)) return '';
  const vcol = {{elevated: '#c81e1e', moderate: '#f2d94e', low: '#8fd14f'}}[ld.verdict] || '#8f7a63';
  let h = '<hr style="margin:10px 0;border-color:#2a3a3e;"><div style="font-weight:600;">Landslide-dam assessment (real maps)</div>';
  if (ld.ml) {{
    const sh = ld.ml.class_shares;
    h += '<div class="stat-row"><span>ML susceptibility: high + very high</span><span>' + Math.round(ld.ml.high_or_very_high_share * 100) + '% of area</span></div>' +
         '<div class="stat-row"><span>Class mix (low / mod / high / v.high)</span><span>' + [sh['low'], sh['moderate'], sh['high'], sh['very high']].map(x => Math.round(x * 100) + '%').join(' / ') + '</span></div>';
    if (ld.verdict) h += '<div class="stat-row" style="font-weight:600;"><span>Heuristic landslide-dam concern</span><span style="color:' + vcol + ';text-transform:uppercase;">' + ld.verdict + '</span></div>';
    else h += '<p class="note">The ML map covers only ' + Math.round(ld.ml.coverage_of_aoi * 100) + '% of this area (it spans KPK/GB/AJK only), so no overall verdict is given.</p>';
    if (ld.verdict && simpleRating && ({{negligible: 0, low: 1, moderate: 2, high: 3, elevated: 3}}[ld.verdict] - {{negligible: 0, low: 1, moderate: 2, high: 3}}[simpleRating]) >= 2)
      h += '<p class="note"><b>These two ratings disagree</b> (simple rock-type &times; slope estimate above: ' + simpleRating + '; real ML map: ' + ld.verdict + '). Prefer the ML map where it covers your area -- it uses 15 factors (geology, slope, distance to faults/rivers/roads, rainfall, stream power, land cover, ...) and was validated against real landslides, while the simple estimate sees only rock strength and slope, so a strong rock like granite on very steep ground reads as low.</p>';
    h += '<p class="note">' + ld.ml.model_quality + '. The verdict is a presentation heuristic (&ge;50% high/very-high = elevated, &ge;25% = moderate), not a validated threshold.</p>';
  }} else {{
    h += '<p class="note">No ML landslide-susceptibility coverage here (that map spans Khyber Pakhtunkhwa, Gilgit-Baltistan and AJK only).</p>';
  }}
  if (ld.valley) {{
    const c = ld.valley.confinement_m, v = ld.valley.valley_depth_m;
    if (v) h += '<div class="stat-row"><span>Valley depth (median / p90)</span><span>' + v.median + ' / ' + v.p90 + ' m</span></div>';
    if (c) h += '<div class="stat-row"><span>Valley-wall height within 1.5 km (median / p90)</span><span>' + c.median + ' / ' + c.p90 + ' m</span></div>';
    h += '<p class="note">Real national valley-geometry rasters. Useful as the scale of a plausible landslide-dam height when filling the GLOF "landslide / breach" inputs above. No landslide-dam blockage index is computed: that needs the landslide volume, which no dataset here provides.</p>';
  }}
  return h;
}}

function computeGeomorphology() {{
  const box = document.getElementById('geomorphBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw/select an area first.</p>'; return; }}
  box.innerHTML = '<p class="note">Checking real GLiM bedrock lithology + DEM slope...</p>';
  fetch('/area/geomorphology', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, use_global_dem: document.getElementById('useGlobalDem').checked}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + (d.needs_global_dem_consent ? ' Enable "Use open-source global DEM" above.' : '') + '</p>'; return; }}
    const susColor = {{negligible:'#2ecc71', low:'#8fd14f', moderate:'#f2d94e', high:'#c81e1e'}}[d.susceptibility] || '#8f7a63';
    box.innerHTML =
      (d.susceptibility ? '<div class="stat-row" style="font-weight:600;"><span>Landslide / debris-flow susceptibility</span><span style="color:' + susColor + ';text-transform:uppercase;">' + d.susceptibility + '</span></div>' :
        '<p class="note">No real GLiM rock-type coverage for this AOI (ice/water/no-data only) -- cannot rate susceptibility.</p>') +
      '<div class="stat-row"><span>Mean real rock erodibility (1=strong, 5=weak)</span><span>' + d.mean_erodibility_1to5 + '</span></div>' +
      '<div class="stat-row"><span>Mean / max real DEM slope</span><span>' + d.mean_slope_pct + '% / ' + d.max_slope_pct + '%</span></div>' +
      '<p class="note">Real rock types present: ' + d.lithology_classes_present.join(', ') + '</p>' +
      renderLandslideDam(d.landslide_dam, d.susceptibility) +
      '<p class="note">' + d.method + '</p>';
  }});
}}

function useGlofDischargeInTier2(dischargeM3s) {{
  document.getElementById('instantDischarge').value = dischargeM3s;
  if (instantFloodLibrary && instantFloodLibrary.length) {{
    const slider = document.getElementById('instantDischargeSlider');
    slider.value = Math.log10(Math.max(dischargeM3s, instantFloodLibrary[0].discharge_m3s));
  }}
  showTier(2);
  syncInstantDischargeSlider('number');
  runInstantFlood();
}}

function findBasinNearGlofSite(lon, lat) {{
  const box = document.getElementById('glofBasinBox');
  box.innerHTML = '<p class="note">Searching real HydroSHEDS basins near this site...</p>';
  fetch('/basins/nearest?lon=' + encodeURIComponent(lon) + '&lat=' + encodeURIComponent(lat) + '&n=3')
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      if (!d.results.length) {{ box.innerHTML = '<p class="note">No real basin data available.</p>'; return; }}
      box.innerHTML = d.results.map((r, i) =>
        '<div class="stat-row" style="align-items:center;"><span>' + (i + 1) + '. HYBAS ' + r.hybas_id +
        ' (' + r.distance_km + 'km away, ' + r.area_km2.toFixed(1) + ' km&sup2;)</span>' +
        '<span><button class="secondary" style="padding:3px 7px;margin-left:6px;" onclick="useBasinAsAOI(' + r.hybas_id + ')">Use as AOI</button></span></div>'
      ).join('');
    }});
}}

function runGlacierBaseline() {{
  const box = document.getElementById('glacierBaselineBox');
  box.innerHTML = '<p class="note">Querying real GLIMS glacier inventory (Earth Engine)...</p>';
  fetch('/glacier/baseline', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      box.innerHTML =
        '<div class="stat-row"><span>Real glaciers (GLIMS)</span><span>' + d.n_glaciers + '</span></div>' +
        '<div class="stat-row"><span>Total glacier area</span><span>' + d.total_area_km2 + ' km&sup2;</span></div>' +
        (d.thumbnail_url ? '<img src="' + d.thumbnail_url + '" style="width:100%;border-radius:6px;margin-top:6px;">' : '') +
        '<p class="note">' + d.method + ' (reference date ' + d.reference_date + ')</p>';
    }});
}}


function runItsliveVelocity() {{
  const box = document.getElementById('itsliveBox'), lakeKey = document.getElementById('glacierLakeKey').value;
  fetch('/glacier/itslive-velocity', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
      pollJob(d.job_id, box, r => {{
        if (r.error) {{ box.innerHTML = '<p class="note">' + r.error + '</p>'; return; }}
        const colour = r.flag === 'marked speed-up' ? '#e0552b' : (r.flag === 'possible speed-up' ? '#e8a13a' : (r.flag.startsWith('inconclusive') || r.flag.startsWith('no ') ? '#9fb2ba' : '#6fbf73'));
        const rows = r.pairs.slice(-14).map(p => '<tr><td>' + p.date + '</td><td>' + (p.platform || '') + '</td><td>' + (p.window === 'recent' ? 'recent' : p.window.replace('baseline-', 'baseline ')) + '</td><td style="text-align:right;">' + p.median + '</td><td style="text-align:right;">' + Math.round(p.valid_frac * 100) + '%</td></tr>').join('');
        box.innerHTML =
          '<div class="stat-row"><span>Verdict</span><span style="color:' + colour + ';font-weight:700;">' + r.flag + (r.speedup_ratio != null ? ' (' + r.speedup_ratio + '&times;)' : '') + '</span></div>' +
          (r.recent_median_m_yr != null ? '<div class="stat-row"><span>Recent median speed</span><span>' + r.recent_median_m_yr + ' m/yr (' + r.n_recent + ' pairs)</span></div>' : '') +
          (r.baseline_median_m_yr != null ? '<div class="stat-row"><span>Same-season baseline</span><span>' + r.baseline_median_m_yr + ' m/yr (' + r.n_baseline + ' pairs)</span></div>' : '') +
          (r.glacier_area_km2 != null ? '<div class="stat-row"><span>Glacier area in AOI</span><span>' + r.glacier_area_km2 + ' km&sup2;</span></div>' : '') +
          (r.flag_caution ? '<p class="note"><b>' + r.flag_caution + '.</b></p>' : '') +
          (r.thumbnail ? '<img src="/itslive/thumb/' + r.thumbnail + '" style="width:100%;border-radius:6px;margin-top:6px;">' : '') +
          '<table style="width:100%;margin-top:8px;font-size:11.5px;"><tr style="text-align:left;color:var(--ink-2);"><th>Pair date</th><th>Sensor</th><th>Window</th><th style="text-align:right;">Median m/yr</th><th style="text-align:right;">Valid</th></tr>' + rows + '</table>' +
          '<p class="note">' + r.method + ' ' + r.source + '.</p>' + (r.errors && r.errors.length ? '<p class="note">Skipped: ' + r.errors.join('; ') + '</p>' : '') +
          (lakeKey ? '<button class="secondary" style="margin-top:8px;" onclick="previewGlofFromGlacier(\\'' + lakeKey + '\\')">Preview this as a GLOF scenario &rarr;</button>' : '');
      }}, 'Fetching ITS_LIVE pairs and measuring glacier speed (first run downloads a few granules)...');
    }});
}}

function runGlacierDisplacement() {{
  const box = document.getElementById('glacierDispBox');
  const lakeKey = document.getElementById('glacierLakeKey').value;
  const confidence = parseFloat(document.getElementById('glacierConfidence').value) || 0.5;
  fetch('/glacier/displacement', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{geometry: currentGeoJSON, start_date: '2026-01-01', end_date: '2026-08-31',
                           confidence_threshold: confidence, lake_key: lakeKey || null}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    pollJob(d.job_id, box, result => {{
      if (result.error) {{ box.innerHTML = '<p class="note">' + result.error + '</p>'; return; }}
      let monthlyRows = result.monthly.map(m => {{
        const a = m.ascending, dsc = m.descending;
        return '<tr><td>' + m.month + '</td>' +
          '<td>' + (a && a.mean_displacement_m != null ? a.mean_displacement_m + ' m (' + a.n_scenes + ' scenes)' : 'no data') + '</td>' +
          '<td>' + (dsc && dsc.mean_displacement_m != null ? dsc.mean_displacement_m + ' m (' + dsc.n_scenes + ' scenes)' : 'no data') + '</td></tr>';
      }}).join('');
      const bc = result.bias_correction;
      box.innerHTML =
        '<div class="stat-row"><span>Cumulative mean displacement</span><span>' + result.cumulative_mean_displacement_m + ' m</span></div>' +
        '<div class="stat-row"><span>Cumulative max displacement</span><span>' + result.cumulative_max_displacement_m + ' m</span></div>' +
        '<div class="stat-row"><span>Mean velocity</span><span>' + result.velocity_cm_per_day_mean + ' cm/day</span></div>' +
        '<div class="stat-row"><span>Max velocity</span><span>' + result.velocity_cm_per_day_max + ' cm/day</span></div>' +
        '<div class="stat-row"><span>Derived active-ice area</span><span>' + result.active_ice_area_km2 + ' km&sup2;</span></div>' +
        (bc ? '<div class="stat-row"><span>Stable-ground bias removed</span><span>dx=' + bc.bias_dx_m + 'm, dy=' + bc.bias_dy_m + 'm (' + bc.stable_ground_pixels + ' px)</span></div>' : '') +
        '<p class="note" style="margin-top:6px;"><b>Cumulative displacement (m)</b></p>' +
        (result.thumbnail_url ? '<img src="' + result.thumbnail_url + '" style="width:100%;border-radius:6px;">' : '') +
        '<p class="note" style="margin-top:6px;"><b>Velocity (cm/day)</b></p>' +
        (result.velocity_thumbnail_url ? '<img src="' + result.velocity_thumbnail_url + '" style="width:100%;border-radius:6px;">' : '') +
        '<table style="width:100%;margin-top:8px;font-size:12px;"><tr style="text-align:left;color:var(--ink-2);"><th>Month</th><th>Ascending</th><th>Descending</th></tr>' + monthlyRows + '</table>' +
        '<p class="note">' + result.method + '</p>' +
        (bc ? '<p class="note">' + bc.method + '</p>' : '') +
        (lakeKey ? '<button class="secondary" style="margin-top:8px;" onclick="previewGlofFromGlacier(\\'' + lakeKey + '\\')">Preview this as a GLOF scenario &rarr;</button>' +
          '<p class="note" style="margin-top:4px;">The real displacement/velocity numbers above are context to help you judge -- this doesn\\'t auto-decide a GLOF is imminent, it jumps to the same real Popov (1991) breach-discharge estimator using this lake\\'s own real documented data.</p>' : '');
    }}, 'Running real ascending+descending Sentinel-1 SAR offset-tracking (Earth Engine) -- this genuinely takes several real minutes...');
  }});
}}

// Closes the loop between glacier SAR monitoring and the GLOF discharge
// estimator -- previously a user had to manually notice a rising real
// displacement/velocity trend above, then separately go open the GLOF
// panel and re-select the same lake by hand. Jumps straight to the same
// real /glof/discharge call the GLOF panel's own "Apply GLOF discharge"
// button already makes, using the identical curated lake this
// displacement result was marked with.
function previewGlofFromGlacier(lakeKey) {{
  const details = document.getElementById('glofDetails');
  details.open = true;
  document.getElementById('glofSource').value = lakeKey;
  updateGlofInputs();
  applyGlofScenario();
  details.scrollIntoView({{behavior: 'smooth', block: 'start'}});
}}

map.on('draw.create', onDrawChange);
map.on('draw.update', onDrawChange);
let drawDebounceTimer = null;
function onDrawChange(e) {{
  const f = e.features[0];
  if (!f) return;
  if (f.geometry.type === 'LineString') {{ addOrUpdateProtectStructure(f); return; }}
  if (f.geometry.type !== 'Polygon') return;
  // only one AOI polygon at a time
  draw.getAll().features.forEach(other => {{
    if (other.id !== f.id && other.geometry.type === 'Polygon') draw.delete(other.id);
  }});
  // Debounced: 'draw.update' can fire once per vertex nudged while a
  // user is actively reshaping a polygon (several times in one editing
  // pass, not just once at the end). Each pass used to kick off its own
  // slow terrain3d/context fetch immediately, and an EARLIER edit's
  // response could resolve AFTER a later one's and overwrite the 3D
  // view with a stale, differently-shaped polygon that no longer
  // matched what was actually drawn on the 2D map (looked like a
  // completely wrong/scrambled AOI shape). Only act once edits settle.
  clearTimeout(drawDebounceTimer);
  drawDebounceTimer = setTimeout(() => {{
    fetch('/area/polygon', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{geometry: f.geometry}})
    }}).then(r => r.json()).then(d => setArea(f.geometry, d.area_km2));
  }}, 400);
}}

function addOrUpdateProtectStructure(f) {{
  if (f.geometry.coordinates.length < 2) return;
  let s = protectStructures.find(s => s.drawId === f.id);
  if (!s) {{
    s = {{ drawId: f.id, type: 'dam' }};
    protectStructures.push(s);
  }}
  s.line = f.geometry.coordinates;
  renderProtectStructures();
}}

function renderProtectStructures() {{
  const box = document.getElementById('protectStructList');
  const runBtn = document.getElementById('protectRunBtn');
  const optBtn = document.getElementById('protectOptBtn');
  if (!protectStructures.length) {{
    box.innerHTML = '<p class="note">Draw a line on the map (line tool, top-left) to add a structure.</p>';
  }} else {{
    box.innerHTML = protectStructures.map((s, i) =>
      '<div class="stat-row" style="align-items:center;">' +
      '<select onchange="protectStructures[' + i + '].type = this.value">' +
      '<option value="dam"' + (s.type === 'dam' ? ' selected' : '') + '>Dam / embankment</option>' +
      '<option value="barrage"' + (s.type === 'barrage' ? ' selected' : '') + '>Barrage</option>' +
      '<option value="canal"' + (s.type === 'canal' ? ' selected' : '') + '>Diversion canal</option>' +
      '<option value="drainage"' + (s.type === 'drainage' ? ' selected' : '') + '>Drainage pipe</option>' +
      '<option value="catchment"' + (s.type === 'catchment' ? ' selected' : '') + '>Rainwater catchment / retention basin</option>' +
      '</select>' +
      '<button class="secondary" style="padding:4px 8px;" onclick="removeProtectStructure(' + i + ')">Remove</button>' +
      '</div>').join('');
  }}
  runBtn.disabled = protectStructures.length === 0 || !currentRunId;
  optBtn.disabled = !currentRunId;
}}

function removeProtectStructure(i) {{
  const s = protectStructures[i];
  if (draw.get(s.drawId)) draw.delete(s.drawId);
  protectStructures.splice(i, 1);
  renderProtectStructures();
}}

function runProtectionScenario() {{
  const box = document.getElementById('protectBox');
  if (!currentRunId) {{ box.innerHTML = '<p class="note">Run the flood model (above) first.</p>'; return; }}
  document.getElementById('protectRunBtn').disabled = true;
  const structures = protectStructures.map(s => ({{ type: s.type, line: s.line }}));
  fetch('/protect/run', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{run_id: currentRunId, structures}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; renderProtectStructures(); return; }}
    pollJob(d.job_id, box, result => {{
      renderProtectStructures();
      const c = result.comparison;
      box.innerHTML =
        '<div class="stat-row"><span>Baseline flooded cells</span><span>' + c.baseline_flooded_cells + '</span></div>' +
        '<div class="stat-row"><span>Scenario flooded cells</span><span>' + c.scenario_flooded_cells + '</span></div>' +
        '<div class="stat-row"><span>Reduction</span><span>' + c.reduction_pct + '%</span></div>' +
        '<div class="stat-row"><span>Displacement</span><span>' + c.displacement_pct + '%</span></div>' +
        (result.loss_avoided_pkr !== null && result.loss_avoided_pkr !== undefined ?
          '<div class="stat-row" style="font-weight:600;"><span>Est. building loss avoided</span><span>Rs ' +
          Math.round(result.loss_avoided_pkr).toLocaleString() + '</span></div>' : '') +
        (result.storage_volume_m3 ?
          '<div class="stat-row"><span>Real catchment storage volume</span><span>' +
          Math.round(result.storage_volume_m3).toLocaleString() + ' m&sup3;</span></div>' : '') +
        '<p class="note">Reduction/displacement measured against the baseline run\\'s own flood extent -- protection is not free; water reduced in one place can appear elsewhere, reported here rather than hidden.</p>' +
        '<button class="secondary" onclick="showOnMap(\\'/protect/' + result.job_id + '/mapbox3d\\')">Show on map</button>';
    }}, 'Running the real physics model with the structure(s) applied (this re-runs the full flood simulation)...');
  }});
}}

function runProtectionOptimize() {{
  const box = document.getElementById('protectOptBox');
  if (!currentRunId) {{ box.innerHTML = '<p class="note">Run the flood model (above) first.</p>'; return; }}
  const types = Array.from(document.querySelectorAll('.protectOptType:checked')).map(el => el.value);
  if (!types.length) {{ box.innerHTML = '<p class="note">Select at least one structure type.</p>'; return; }}
  document.getElementById('protectOptBtn').disabled = true;
  const maxEvals = parseInt(document.getElementById('protectOptEffort').value);
  const nStructures = parseInt(document.getElementById('protectOptN').value);
  fetch('/protect/optimize', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{run_id: currentRunId, types, n_structures: nStructures, max_evals: maxEvals}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; document.getElementById('protectOptBtn').disabled = false; return; }}
    box.innerHTML = '<p class="note">Searching (real per-trial LISFLOOD-FP re-runs, this can take real minutes)... trial 0/' + maxEvals + '</p>';
    const check = () => {{
      fetch('/climate-jobs/' + d.job_id + '/progress.json').then(r => r.json()).then(p => {{
        if (p.status === 'running') {{
          const t = p.trial || 0;
          const lossNote = p.last_loss_avoided_pkr != null ? (', Rs ' + Math.round(p.last_loss_avoided_pkr).toLocaleString() + ' loss avoided') : '';
          const rp = p.last_reduction_pct != null ? (' -- last trial: ' + p.last_reduction_pct + '% reduction, ' + p.last_displacement_pct + '% displacement' + lossNote) : '';
          box.innerHTML = '<p class="note">Searching (real per-trial LISFLOOD-FP re-runs)... trial ' + t + '/' + (p.max_evals || maxEvals) + rp + '</p>';
          setTimeout(check, 3000);
          return;
        }}
        document.getElementById('protectOptBtn').disabled = false;
        if (p.status === 'failed') {{ box.innerHTML = '<p class="note">Failed: ' + p.error + '</p>'; return; }}
        const result = p.result;
        const c = result.comparison;
        box.innerHTML =
          '<div class="stat-row"><span>Real trials evaluated</span><span>' + result.trials_evaluated + '</span></div>' +
          '<div class="stat-row"><span>Reduction</span><span>' + c.reduction_pct + '%</span></div>' +
          '<div class="stat-row"><span>Displacement</span><span>' + c.displacement_pct + '%</span></div>' +
          (result.loss_avoided_pkr !== null && result.loss_avoided_pkr !== undefined ?
            '<div class="stat-row" style="font-weight:600;"><span>Est. building loss avoided</span><span>Rs ' +
            Math.round(result.loss_avoided_pkr).toLocaleString() + '</span></div>' : '') +
          (result.storage_volume_m3 ?
            '<div class="stat-row"><span>Real catchment storage volume</span><span>' +
            Math.round(result.storage_volume_m3).toLocaleString() + ' m&sup3;</span></div>' : '') +
          '<p class="note">Structures found: ' + result.structures.map(s => s.type).join(', ') + '. Each candidate was scored by a real LISFLOOD-FP re-run, not a surrogate.</p>' +
          '<button class="secondary" onclick="showOnMap(\\'/protect/' + result.protect_job_id + '/mapbox3d\\')">Show on map</button>';
      }});
    }};
    setTimeout(check, 1000);
  }});
}}

function uploadShape() {{
  const f = document.getElementById('uploadFile').files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  fetch('/area/upload', {{method:'POST', body: fd}})
    .then(r => r.json()).then(d => {{
      if (d.error) {{ alert(d.error); return; }}
      setArea(d.geometry, d.area_km2);
      draw.getAll().features.forEach(other => {{ if (other.geometry.type === 'Polygon') draw.delete(other.id); }});
      draw.add({{type:'Feature', properties:{{}}, geometry: d.geometry}});
      const bounds = new mapboxgl.LngLatBounds();
      const coords = d.geometry.type === 'Polygon' ? d.geometry.coordinates[0] : d.geometry.coordinates[0][0];
      coords.forEach(c => bounds.extend(c));
      map.fitBounds(bounds, {{padding: 40}});
    }});
}}

let currentRunId = null;

let contextToken = 0;
let lastRainfallContext = null;

function loadContext() {{
  const box = document.getElementById('contextBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw or upload an area above first.</p>'; return; }}
  box.innerHTML = '<p class="note">Loading real rainfall data...</p>';
  const myToken = ++contextToken;
  fetch('/area/context', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      if (myToken !== contextToken) return;
      const rf = d.rainfall;
      lastRainfallContext = rf.error ? null : rf;
      const rainRows = rf.error ? '<p class="note">Rainfall fetch failed: ' + rf.error + '</p>' :
        '<div class="stat-row"><span>Source</span><span>' + rf.source + '</span></div>' +
        '<div class="stat-row"><span>Hourly points (6 days)</span><span>' + rf.hourly_precipitation_mm.length + '</span></div>' +
        '<div class="stat-row"><span>Peak hourly (mm)</span><span>' + Math.max(...rf.hourly_precipitation_mm).toFixed(1) + '</span></div>' +
        '<button class="secondary" style="margin-top:8px;" onclick="runForecastMode()">Run flood model on the real forecast &rarr;</button>' +
        '<p class="note" id="forecastModeNote" style="margin-top:4px;"></p>';

      const pv = d.pluvial;
      const tierColors = {{none:'#0ca30c', watch:'#e0c000', warning:'#e08000', severe:'#d92626', unknown:'#888'}};
      let pluvialHtml = '';
      if (pv && !pv.error) {{
        const c = tierColors[pv.risk_tier] || '#888';
        pluvialHtml = '<h3>Pluvial / flash-flood risk (Tier 4)</h3>' +
          '<div class="stat-row"><span>Risk tier</span><span style="color:' + c + ';font-weight:600;text-transform:uppercase;">' + pv.risk_tier + '</span></div>' +
          '<div class="stat-row"><span>Peak 24h forecast</span><span>' + pv.peak_24h_forecast_mm.toFixed(1) + ' mm</span></div>' +
          (pv.climatology ? '<div class="stat-row"><span>This point\\'s p95 (20y)</span><span>' + pv.climatology.p95_daily_mm.toFixed(1) + ' mm</span></div>' : '') +
          '<p class="note">' + pv.note + '</p>' +
          '<p class="note" style="opacity:0.8;">' + pv.data_ceiling + '</p>' +
          (pv.real_idf_reference ? (() => {{
            const r = pv.real_idf_reference;
            return '<p class="note" style="margin-top:6px;"><b>Real recorded reference (' + r.season + ' ' + r.year + '):</b> RX1day ' +
              r.rx1day_mm + ' mm &middot; peak hourly intensity ' + r.peak_hourly_intensity_mm_hr + ' mm/hr &middot; ' +
              'trend ' + r.trend_mm_per_decade + ' mm/decade. ' + r.method + '</p>';
          }})() : '');
      }}
      box.innerHTML = '<h3 style="margin-top:0;">Rainfall (real, Open-Meteo)</h3>' + rainRows + pluvialHtml;
    }});
}}

// A real "forecast mode": auto-fills the storm inputs from the ACTUAL
// forecasted rainfall (Open-Meteo, already fetched by loadContext) rather
// than a historical design storm or a manually-typed guess, then runs
// Tier 3 directly. This is the honest replacement for a "Tier 1 -> Tier 2
// -> Tier 3" chain -- Tier 1 (an ML discharge predictor) was removed
// entirely earlier in this project at explicit request (see CLAUDE.md),
// so this chains the real forecast straight into the physics run instead
// of resurrecting a removed feature.
function runForecastMode() {{
  const note = document.getElementById('forecastModeNote');
  if (!lastRainfallContext) {{ if (note) note.textContent = 'No real forecast data loaded.'; return; }}
  const times = lastRainfallContext.hourly_time, precip = lastRainfallContext.hourly_precipitation_mm;
  // Real, previously-shipped bug fixed here: hourly_time's own labels are
  // in Open-Meteo's real resolved LOCAL timezone for this AOI (timezone=
  // "auto"), not UTC -- new Date().toISOString() is always UTC, so
  // comparing it directly against these labels silently shifted the real
  // future/past boundary by this AOI's own UTC offset (up to 5 already-
  // ELAPSED local hours wrongly read as "forecast ahead" for any Pakistan
  // AOI). Fixed by shifting "now" into the same local frame the labels
  // use, via the real utc_offset_seconds Open-Meteo also returns (same
  // fix applied to monitoring_utils.real_forecast_peak_and_duration).
  const offsetMs = (lastRainfallContext.utc_offset_seconds || 0) * 1000;
  const nowLocal = new Date(Date.now() + offsetMs);
  const nowIso = nowLocal.toISOString().slice(0, 16);
  let firstFuture = times.findIndex(t => t >= nowIso);
  if (firstFuture < 0) firstFuture = 0;
  // Real peak hourly rate within the actual FUTURE forecast window only
  // (past_days are recent-observed, not what "run on the forecast" means).
  let peakIdx = firstFuture;
  for (let i = firstFuture; i < precip.length; i++) {{
    if (precip[i] > precip[peakIdx]) peakIdx = i;
  }}
  const peakMmHr = precip[peakIdx];
  if (!peakMmHr || peakMmHr <= 0) {{
    note.textContent = 'Real forecast shows no significant rain ahead -- nothing to run.';
    return;
  }}
  // Real contiguous rain-event duration around the peak (>0.1mm/hr on
  // either side), not an arbitrary fixed window.
  let lo = peakIdx, hi = peakIdx;
  while (lo > 0 && precip[lo - 1] > 0.1) lo--;
  while (hi < precip.length - 1 && precip[hi + 1] > 0.1) hi++;
  const durationHr = Math.max(1, hi - lo + 1);
  document.getElementById('rainMmHr').value = peakMmHr.toFixed(1);
  document.getElementById('durationHr').value = durationHr;
  note.textContent = 'Real forecast: peak ' + peakMmHr.toFixed(1) + ' mm/hr at ' + times[peakIdx] +
    ', ' + durationHr + 'h contiguous event -- running now.';
  showTier(3);
  runModel();
}}

function updateStormCenterFields() {{
  document.getElementById('stormCenterFields').style.display =
    document.getElementById('enableStormCenter').checked ? '' : 'none';
}}

// Plain average of the drawn ring's own vertices -- a real centroid needs
// a proper polygon-area-weighted calculation; this simple mean is a good
// enough real default center point for a typical compact AOI shape, and
// the fields stay directly editable if a user wants a different point.
function useAoiCentroidForStorm() {{
  if (!currentGeoJSON) return;
  const ring = (currentGeoJSON.type === 'Polygon' ? currentGeoJSON.coordinates[0]
    : currentGeoJSON.coordinates[0][0]);
  let sx = 0, sy = 0;
  for (const [x, y] of ring) {{ sx += x; sy += y; }}
  document.getElementById('stormCenterLon').value = (sx / ring.length).toFixed(5);
  document.getElementById('stormCenterLat').value = (sy / ring.length).toFixed(5);
}}

function runModel() {{
  // Real, deliberate guard against a silent bad fallback: if a rainfall
  // CSV file was actually chosen but never successfully parsed into a
  // real series (a real failed/still-in-flight upload -- confirmed
  // this can genuinely happen on a real transient network error, not
  // just a bad file), running anyway would silently use the manual
  // Peak rainfall/Duration fields with ZERO indication the intended
  // real data never took effect -- exactly the kind of "ran a totally
  // different, unintended storm with no warning" bug this app never
  // allows elsewhere (see e.g. the needs_global_dem_consent gate).
  const csvFile = document.getElementById('rainfallCsvFile');
  if (csvFile.files.length > 0 && !uploadedRainfallSeries) {{
    const proceed = confirm(
      'A rainfall CSV was selected but has not been successfully parsed yet' +
      (window.rainfallCsvUploadFailed ? ' (the upload failed)' : ' (still processing, or never attempted)') +
      '. Running now will use the manual Peak rainfall/Duration fields instead, NOT your uploaded file.\\n\\n' +
      'Click Cancel to wait/retry the upload, or OK to proceed with manual entry anyway.');
    if (!proceed) return;
  }}
  document.getElementById('runBtn').disabled = true;
  document.getElementById('statusNote').textContent = 'Submitting...';
  showPane('runs');
  fetch('/run', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      geometry: currentGeoJSON,
      rain_mm_hr: parseFloat(document.getElementById('rainMmHr').value),
      duration_hr: parseFloat(document.getElementById('durationHr').value),
      rainfall_series: uploadedRainfallSeries,
      include_buildings: document.getElementById('includeBuildings').checked,
      import_id: importedPlan ? importedPlan.id : null,
      friction: parseFloat(document.getElementById('friction').value),
      cfl: parseFloat(document.getElementById('cfl').value),
      infiltration_mm_hr: parseFloat(document.getElementById('infiltrationMmHr').value),
      use_soil_infiltration: document.getElementById('useSoilInfiltration').checked,
      use_green_ampt: document.getElementById('useGreenAmpt').checked,
      use_channel_conditioning: document.getElementById('useChannelConditioning').checked,
      amc: parseFloat(document.getElementById('amc').value),
      inflows: boundaryItems.filter(b => b.type === 'inflow' && b.included).map(boundaryItemToRequest),
      outflows: boundaryItems.filter(b => b.type === 'outflow' && b.included).map(boundaryItemToRequest),
      use_global_dem: document.getElementById('useGlobalDem').checked,
      custom_dem_id: window.customDemId || null,
      manning_scale: window.manningScale || 1.0,
      enable_arf: document.getElementById('enableArf').checked,
      storm_center_lon: document.getElementById('enableStormCenter').checked ? (parseFloat(document.getElementById('stormCenterLon').value) || null) : null,
      storm_center_lat: document.getElementById('enableStormCenter').checked ? (parseFloat(document.getElementById('stormCenterLat').value) || null) : null,
      predict_storm_motion: document.getElementById('enableStormCenter').checked && document.getElementById('predictStormMotion').checked
    }})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{
      document.getElementById('statusNote').textContent = 'Error: ' + d.error;
      document.getElementById('runBtn').disabled = false;
      if (d.needs_global_dem_consent) showGlobalDemPrompt(d.error);
      return;
    }}
    currentRunId = d.run_id;
    pollStatus();
  }});
}}


let customDemInfo = null;
window.manningScale = 1.0;

function uploadCustomDem() {{
  const inp = document.getElementById('customDemFile'), box = document.getElementById('customDemBox');
  if (!inp.files.length) return;
  const fd = new FormData(); fd.append('file', inp.files[0]);
  box.innerHTML = '<p class="note">Checking the elevation file...</p>';
  fetch('/area/upload-dem', {{method:'POST', body: fd}}).then(r => r.json()).then(d => {{
    if (d.error) {{ customDemInfo = null; window.customDemId = null; box.innerHTML = '<p class="note" style="color:#d03b3b;">' + d.error + '</p>'; return; }}
    customDemInfo = d; window.customDemId = d.dem_id;
    document.getElementById('customDemClear').style.display = '';
    box.innerHTML = '<div class="stat-row"><span>Cell size</span><span>' + d.cellsize_m + ' m</span></div>' +
      '<div class="stat-row"><span>Elevation range</span><span>' + d.min_m + ' to ' + d.max_m + ' m</span></div>' +
      '<div class="stat-row"><span>Grid</span><span>' + d.ncols + ' x ' + d.nrows + '</span></div>' +
      '<p class="note">' + d.note + ' Runs whose area it fully covers will use it instead of the Pakistan SRTM.</p>';
  }}).catch(e => {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">Upload failed: ' + e + '</p>'; }});
}}

function clearCustomDem() {{
  customDemInfo = null; window.customDemId = null;
  document.getElementById('customDemFile').value = '';
  document.getElementById('customDemClear').style.display = 'none';
  document.getElementById('customDemBox').innerHTML = '<p class="note">Back to the Pakistan SRTM.</p>';
}}

function loadRunFile() {{
  const inp = document.getElementById('runFileInput'), box = document.getElementById('runFileBox');
  if (!inp.files.length) return;
  const fd = new FormData(); fd.append('file', inp.files[0]);
  fetch('/runfile/parse', {{method:'POST', body: fd}}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">' + d.error + '</p>'; return; }}
    const q = d.request;
    const setv = (id, v) => {{ const el = document.getElementById(id); if (el && v !== undefined && v !== null) el.value = v; }};
    const setc = (id, v) => {{ const el = document.getElementById(id); if (el && v !== undefined && v !== null) el.checked = !!v; }};
    setv('rainMmHr', q.rain_mm_hr); setv('durationHr', q.duration_hr); setv('friction', q.friction); setv('cfl', q.cfl);
    setv('infiltrationMmHr', q.infiltration_mm_hr); setv('amc', q.amc); if (typeof updateAmcLabel === 'function') updateAmcLabel();
    setc('includeBuildings', q.include_buildings); setc('useSoilInfiltration', q.use_soil_infiltration);
    setc('useGreenAmpt', q.use_green_ampt); setc('useChannelConditioning', q.use_channel_conditioning);
    setc('useGlobalDem', q.use_global_dem); setc('enableArf', q.enable_arf);
    window.manningScale = q.manning_scale || 1.0;
    window.customDemId = q.custom_dem_id || null;
    uploadedRainfallSeries = q.rainfall_series || null;
    boundaryItems = [];
    (q.inflows || []).forEach(b => boundaryItems.push(Object.assign({{id: boundaryIdSeq++, type:'inflow', included:true, source:'runfile'}}, b)));
    (q.outflows || []).forEach(b => boundaryItems.push(Object.assign({{id: boundaryIdSeq++, type:'outflow', included:true, source:'runfile'}}, b)));
    renderBoundaryList();
    draw.getAll().features.forEach(other => {{ if (other.geometry.type === 'Polygon') draw.delete(other.id); }});
    draw.add({{type:'Feature', properties:{{}}, geometry: q.geometry}});
    fetch('/area/polygon', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: q.geometry}})}})
      .then(r => r.json()).then(a => setArea(q.geometry, a.area_km2));
    const bounds = new mapboxgl.LngLatBounds();
    const coords = q.geometry.type === 'Polygon' ? q.geometry.coordinates[0] : q.geometry.coordinates[0][0];
    coords.forEach(c => bounds.extend(c));
    map.fitBounds(bounds, {{padding: 40, duration: 1200}});
    box.innerHTML = '<p class="note">Run file loaded: area, storm and settings restored' +
      (q.rainfall_series ? ' (including its uploaded rainfall series)' : '') + '. Click Run flood model to run it.' +
      (window.manningScale !== 1.0 ? ' It carries a calibrated roughness multiplier of ' + window.manningScale + '.' : '') + '</p>' +
      (d.warnings || []).map(w => '<p class="note" style="color:#d9822b;">' + w + '</p>').join('');
  }}).catch(e => {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">Could not read the file: ' + e + '</p>'; }});
}}

function runCalibration() {{
  const box = document.getElementById('calibBox'), inp = document.getElementById('calibFile');
  if (!currentRunId || !inp.files.length) {{ box.innerHTML = '<p class="note">Choose an observed flood extent file first.</p>'; return; }}
  const fd = new FormData(); fd.append('file', inp.files[0]); fd.append('max_evals', document.getElementById('calibBudget').value);
  document.getElementById('calibBtn').disabled = true;
  fetch('/runs/' + currentRunId + '/calibrate', {{method:'POST', body: fd}}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">' + d.error + '</p>'; document.getElementById('calibBtn').disabled = false; return; }}
    const poll = () => fetch('/climate-jobs/' + d.job_id + '/progress.json').then(r => r.json()).then(j => {{
      if (j.status === 'running') {{
        box.innerHTML = '<p class="note">Trial ' + (j.trial || 0) + ' of ' + (j.max_evals || '?') + ' (each is a real flood run)' +
          (j.last_csi !== undefined && j.last_csi !== null ? ', last CSI ' + j.last_csi : '') + '...</p>';
        setTimeout(poll, 4000); return;
      }}
      document.getElementById('calibBtn').disabled = false;
      if (j.status === 'failed') {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">Calibration failed: ' + j.error + '</p>'; return; }}
      const r = j.result, f = v => (v === null || v === undefined) ? 'n/a' : v;
      box.innerHTML = '<div class="stat-row"><span>Overlap score (CSI) before</span><span>' + f(r.csi_before) + '</span></div>' +
        '<div class="stat-row"><span>Overlap score (CSI) after</span><span><b>' + f(r.csi_after) + '</b></span></div>' +
        '<div class="stat-row"><span>Roughness multiplier</span><span>' + r.apply.manning_scale + '</span></div>' +
        '<div class="stat-row"><span>Infiltration rate</span><span>' + r.apply.infiltration_mm_hr + ' mm/hr</span></div>' +
        '<div class="stat-row"><span>Runs used</span><span>' + r.n_trials + (r.n_failed ? ' (' + r.n_failed + ' failed)' : '') + '</span></div>' +
        '<button class="secondary" onclick="applyCalibration(' + r.apply.manning_scale + ',' + r.apply.infiltration_mm_hr + ')">Use these values for the next run</button>' +
        '<p class="note">' + r.caveat + '</p>';
    }});
    setTimeout(poll, 2000);
  }}).catch(e => {{ box.innerHTML = '<p class="note" style="color:#d03b3b;">Failed: ' + e + '</p>'; document.getElementById('calibBtn').disabled = false; }});
}}

function applyCalibration(scale, infil) {{
  window.manningScale = scale;
  document.getElementById('infiltrationMmHr').value = infil;
  document.getElementById('useSoilInfiltration').checked = false;
  document.getElementById('calibBox').insertAdjacentHTML('beforeend',
    '<p class="note">Set: roughness multiplier ' + scale + ' and infiltration ' + infil + ' mm/hr. They apply to the next run you start.</p>');
}}

function validateObserved() {{
  const box = document.getElementById('obsBox'), inp = document.getElementById('obsFile');
  if (!currentRunId || !inp.files.length) {{ box.innerHTML = '<p class="note">Choose an observed flood extent file first.</p>'; return; }}
  const fd = new FormData(); fd.append('file', inp.files[0]);
  box.innerHTML = '<p class="note">Scoring...</p>';
  fetch('/runs/' + currentRunId + '/validate-observed', {{method:'POST', body: fd}}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    const f = v => (v === null || v === undefined) ? 'n/a' : v;
    box.innerHTML = '<table class="kv"><tr><td>CSI (overlap score, 1 = perfect)</td><td>' + f(d.CSI) + '</td></tr>' +
      '<tr><td>POD (observed flood the model caught)</td><td>' + f(d.POD) + '</td></tr>' +
      '<tr><td>FAR (model flood that was not observed)</td><td>' + f(d.FAR) + '</td></tr>' +
      '<tr><td>Bias (model wet / observed wet)</td><td>' + f(d.bias) + '</td></tr>' +
      '<tr><td>Modelled / observed wet area</td><td>' + d.model_wet_km2 + ' / ' + d.observed_wet_km2 + ' km&sup2;</td></tr></table>' +
      '<p class="note">Wet = depth above ' + d.wet_threshold_m + ' m; only cells inside this run\\'s area are scored.' + (d.warning ? ' <b>' + d.warning + '</b>' : '') + '</p>';
  }}).catch(e => {{ box.innerHTML = '<p class="note">Failed: ' + e + '</p>'; }});
}}

function rebuildScene() {{
  const box = document.getElementById('rebuildSceneBox');
  if (!currentRunId) return;
  fetch('/runs/' + currentRunId + '/rebuild-scene', {{method:'POST'}}).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    pollJob(d.job_id, box, r => {{
      box.innerHTML = '<p class="note">3D view rebuilt (' + r.buildings.toLocaleString() + ' buildings, ' + r.road_segments.toLocaleString() + ' road segments).</p>';
      const frame = document.getElementById('view3dFrame');
      frame.removeAttribute('srcdoc'); frame.src = '/runs/' + currentRunId + '/mapbox_3d.html?v=' + Date.now();
    }}, 'Rebuilding the 3D view from the saved results (no re-simulation)...');
  }});
}}

function cancelRun() {{
  if (!currentRunId || !confirm('Cancel this run?')) return;
  fetch('/runs/' + currentRunId + '/cancel', {{method:'POST'}}).then(r => r.json()).then(d => {{
    if (d.error) alert(d.error);
  }});
}}

var DEPTH_MARKS = [[0.1, "ankle"], [0.5, "knee"], [1, "waist"], [1.35, "chest"], [1.7, "head"]];
function depthMessage(d) {{
  if (d < 0.1) return "Below ankle height. Watch for open drains and moving water.";
  if (d < 0.5) return "Ankle to knee height. Walking gets difficult. Do not drive through it.";
  if (d < 1) return "Knee to waist height. Fast water at this depth can knock an adult over. Avoid it.";
  if (d < 1.35) return "Waist to chest height. Dangerous to wade through. Move to higher ground.";
  if (d < 1.7) return "Chest height. Dangerous. Move to an upper floor or higher ground.";
  return "Over head height for most adults. Move to the upper floor or higher ground.";
}}
function depthGuideSvg(d) {{
  var S = 64, H = 150, W = 300, g = H - 14, dd = Math.min(Math.max(d, 0), 2.1), top = g - dd * S, px = 100;
  var s = '<svg class="dg-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Flood depth compared with a standing adult">';
  s += '<rect class="dg-water" x="0" y="' + top + '" width="' + W + '" height="' + (g - top) + '"/>';
  s += '<g class="dg-person"><rect x="' + (px - 10) + '" y="' + (g - 0.85 * S) + '" width="8" height="' + (0.85 * S) + '" rx="2"/>' +
       '<rect x="' + (px + 2) + '" y="' + (g - 0.85 * S) + '" width="8" height="' + (0.85 * S) + '" rx="2"/>' +
       '<rect x="' + (px - 14) + '" y="' + (g - 1.5 * S) + '" width="28" height="' + (0.7 * S) + '" rx="6"/>' +
       '<circle cx="' + px + '" cy="' + (g - 1.62 * S) + '" r="' + (0.12 * S) + '"/></g>';
  s += '<line class="dg-surface" x1="0" x2="' + W + '" y1="' + top + '" y2="' + top + '"/>';
  DEPTH_MARKS.forEach(function (m) {{
    var y = g - m[0] * S;
    s += '<line class="dg-tick" x1="170" x2="190" y1="' + y + '" y2="' + y + '"/><text class="dg-lbl" x="195" y="' + (y + 3) + '">' + m[1] + ' ' + m[0] + ' m</text>';
  }});
  s += '<rect class="dg-ground" x="0" y="' + g + '" width="' + W + '" height="3"/></svg>';
  return s;
}}
function renderDepthGuide(elId, depth) {{
  var el = document.getElementById(elId); if (!el) return;
  var d0 = Math.min(Math.max(+depth || 0, 0), 2.1);
  el.innerHTML = '<div class="dg"><div class="dg-title">What this depth means</div><div id="' + elId + '_svg">' + depthGuideSvg(d0) + '</div>' +
    '<div class="dg-row"><label for="' + elId + '_s">Depth</label><input type="range" id="' + elId + '_s" min="0" max="2.1" step="0.05" value="' + d0.toFixed(2) + '"><b id="' + elId + '_v">' + d0.toFixed(2) + ' m</b></div>' +
    '<div class="dg-msg" id="' + elId + '_m">' + depthMessage(d0) + '</div>' +
    '<div class="dg-note">Speed matters as much as depth, so check whether the water is moving fast. A modelled depth is an estimate.' + ((+depth) > 2.1 ? ' The model shows ' + (+depth).toFixed(1) + ' m at its deepest point, beyond this scale.' : '') + '</div></div>';
  document.getElementById(elId + '_s').addEventListener('input', function (e) {{
    var v = parseFloat(e.target.value);
    document.getElementById(elId + '_svg').innerHTML = depthGuideSvg(v);
    document.getElementById(elId + '_v').textContent = v.toFixed(2) + ' m';
    document.getElementById(elId + '_m').textContent = depthMessage(v);
  }});
}}

function pollStatus() {{
  fetch('/runs/' + currentRunId + '/progress.json').then(r => r.json()).then(d => {{
    const box = document.getElementById('runStatusBox');
    if (d.status === 'done') {{
      box.innerHTML = '<h3>Complete</h3>' +
        '<div class="stat-row"><span>Flooded area</span><span>' + d.summary.flooded_area_km2.toFixed(3) + ' km²</span></div>' +
        '<div class="stat-row"><span>Max depth</span><span>' + d.summary.max_depth_m.toFixed(2) + ' m</span></div>' +
        (d.summary.max_velocity_ms != null ? '<div class="stat-row"><span>Max flow velocity</span><span>' + d.summary.max_velocity_ms.toFixed(2) + ' m/s</span></div>' +
        '<div class="stat-row"><span>Mean velocity (flooded)</span><span>' + d.summary.mean_velocity_ms_in_flooded_area.toFixed(2) + ' m/s</span></div>' : '') +
        '<div class="stat-row"><span>Peak hazard</span><span>' + d.summary.peak_hazard_band + '</span></div>' +
        '<div class="stat-row"><span>Solver</span><span>' + (d.summary.solver_used || 'acc').toUpperCase() + '</span></div>' +
        '<div class="stat-row"><span>Time taken</span><span>' + d.elapsed.toFixed(1) + ' s</span></div>' +
        '<div id="depthGuideRun"></div>' +
        '<div style="margin-top:14px; display:flex; gap:8px;">' +
        '<a class="btn primary" href="/runs/' + currentRunId + '/report.html" target="_blank">Full 2D report</a>' +
        '<a class="btn secondary" href="/runs/' + currentRunId + '/report.pdf">Export PDF</a>' +
        '<a class="btn secondary" href="/runs/' + currentRunId + '/susceptibility-map.png?download=1" title="Classified peak-depth map with legend, areas and key figures">Susceptibility map (PNG)</a></div>' +
        '<div style="margin-top:8px;"><input type="file" id="obsFile" accept=".tif,.tiff,.geojson,.json,.gpkg,.zip,.shp" style="max-width:100%;"> <button class="secondary" onclick="validateObserved()" title="Upload a mapped flood extent (polygons or GeoTIFF) to score this run against it">Compare with observed flood</button><div id="obsBox"></div></div>' +
        '<div style="margin-top:8px;"><a class="btn secondary" href="/runs/' + currentRunId + '/runfile" title="Download this run\\'s inputs so it can be reloaded here or on another SAILAB">Save run file</a></div>' +
        '<div style="margin-top:8px;"><b>Calibrate roughness and infiltration</b><br><input type="file" id="calibFile" accept=".tif,.tiff,.geojson,.json,.gpkg,.zip,.shp" style="max-width:100%;"> ' +
        '<select id="calibBudget" style="width:auto;"><option value="12">Quick (12 runs)</option><option value="24" selected>Balanced (24 runs)</option><option value="40">Thorough (40 runs)</option></select> ' +
        '<button class="secondary" id="calibBtn" onclick="runCalibration()" title="Searches a Manning multiplier and an infiltration rate that best reproduce an observed flood extent, using real solver runs">Calibrate</button><div id="calibBox"></div></div>' +
        '<div style="margin-top:8px;"><button class="secondary" onclick="rebuildScene()" title="Redraws the 3D view with the newest viewer from the saved results -- no re-simulation">Rebuild 3D view</button><div id="rebuildSceneBox"></div></div>' +
        (d.summary.solver_selection_reason ? '<p class="note" style="margin-top:10px;" title="' + d.summary.solver_selection_reason.replace(/"/g, "&quot;") + '">Solver auto-selected: ' + d.summary.solver_selection_reason + '</p>' : '') +
        (d.summary.buildings_used ? '<p class="note" style="margin-top:10px;">' + d.summary.buildings_used + ' real buildings conditioned into this run.</p>' : '') +
        ((d.summary.dams_conditioning && (d.summary.dams_conditioning.n_dams_conditioned || d.summary.dams_conditioning.n_reservoirs_conditioned)) ?
          '<p class="note" style="margin-top:4px;">Real existing infrastructure permanently conditioned into this AOI\\'s terrain: ' +
          d.summary.dams_conditioning.n_dams_conditioned + ' dam(s) [' + d.summary.dams_conditioning.dam_names.join(', ') + '], ' +
          d.summary.dams_conditioning.n_reservoirs_conditioned + ' reservoir(s) [' + d.summary.dams_conditioning.reservoir_names.join(', ') + '].</p>' : '') +
        (d.summary.storm_motion ? '<p class="note" style="margin-top:10px;">' + d.summary.storm_motion + '</p>' : '') +
        (d.summary.storm_motion_source ? '<p class="note" style="margin-top:4px;">' + d.summary.storm_motion_source + '</p>' : '');
      renderDepthGuide('depthGuideRun', d.summary.max_depth_m);
      renderInfra(d.summary);
      renderDamage(d.summary);
      renderSediment(d.summary);
      document.getElementById('nbsBtn').disabled = false;
      document.getElementById('nbsRecommendBtn').disabled = false;
      renderProtectStructures();
      if (!document.getElementById('compareRunA').value) document.getElementById('compareRunA').value = currentRunId;
      const fullBtn = document.getElementById('v3dFloodMbFullBtn');
      fullBtn.href = '/runs/' + currentRunId + '/mapbox_3d.html';
      fullBtn.style.display = 'inline-block';
      loadTier3D(3);
      setViewMode('main');
    }} else if (d.status === 'cancelled') {{
      box.innerHTML = '<h3>Cancelled</h3><p class="note">This run was cancelled.</p>';
      document.getElementById('runBtn').disabled = false;
    }} else if (d.status === 'failed') {{
      box.innerHTML = '<h3 style="color:#d03b3b;">Failed</h3><pre style="font-size:11px;white-space:pre-wrap;">' + (d.log_tail||'') + '</pre>';
      document.getElementById('runBtn').disabled = false;
    }} else {{
      const pct = d.pct || 3;
      const stages = d.stages || [];
      const curIdx = stages.findIndex(s => s[0] === d.stage);
      const stepsHtml = stages.map((s, i) => {{
        const [key, label] = s;
        let icon = '&#9675;', style = 'color:#8fa3a9;';  // pending
        if (i < curIdx || (key === 'done' && d.status === 'done')) {{ icon = '&#10003;'; style = 'color:#2ecc71;'; }}
        else if (i === curIdx) {{ icon = '&#9679;'; style = 'color:#3fc4dd;font-weight:600;'; }}
        let extra = '';
        if (key === 'model' && i === curIdx && d.model_min != null) {{
          extra = ' <span class="note">(' + d.model_min.toFixed(0) + '/' + (d.sim_min||0).toFixed(0) + ' min simulated, ' + pct + '%)</span>';
        }}
        return '<div style="' + style + 'padding:3px 0;font-size:12.5px;">' + icon + ' ' + label + extra + '</div>';
      }}).join('');
      const queuedHtml = d.status === 'queued'
        ? '<p class="note"><b>Queued</b>' + (d.queue_position ? ' &mdash; position ' + d.queue_position : '') + ', waiting ' + Math.round(d.queued_for || 0) + ' s. ' +
          ((d.waiting_on && d.waiting_on.length) ? 'Another run is using the solver now (' + d.waiting_on[0].stage + ', ' + Math.round(d.waiting_on[0].elapsed) + ' s in).' : ((d.solver && d.solver.holder) ? 'The solver is busy with: ' + d.solver.holder.label + ' (' + Math.round(d.solver.holder.running_s) + ' s in).' : 'Waiting for another job on the solver.')) + '</p>'
        : '';
      const cancelHtml = d.can_cancel ? '<button class="secondary" style="margin-top:8px;" onclick="cancelRun()">Cancel this run</button>' : '';
      const overall = overallPct(d);
      box.innerHTML = '<h3>' + (d.status === 'queued' ? 'Queued' : 'Running') + '</h3>' + queuedHtml +
        '<div class="progress-track" style="margin:6px 0 4px;"><div class="progress-fill" style="width:' + Math.max(overall, 2) + '%"></div></div>' +
        '<p class="note" style="margin:0 0 8px;">' + overall + '% &middot; ' + stageLabel(d) + '</p>' + stepsHtml + cancelHtml;
      setTimeout(pollStatus, 1000);
    }}
  }});
}}

function renderInfra(summary) {{
  const box = document.getElementById('infraBox');
  if (!box) return;
  const risk = summary && summary.infra_risk;
  let html = '<div style="margin-bottom:16px;"><h3>Buildings</h3>' +
    '<div class="stat-row"><span>Real buildings conditioned into this run</span><span>' + (summary.buildings_used||0) + '</span></div>';
  const sub = summary && summary.building_submersion;
  if (sub && sub.band_breakdown) {{
    const bandsHtml = Object.entries(sub.band_breakdown).map(([band,count]) =>
      '<span class="pill" style="margin-right:4px;">' + band + ': ' + count + '</span>').join('');
    html += '<p class="note" style="margin-top:8px;">Damage severity from the real Huizinga et al. 2017 (JRC) ' +
      'depth-damage curve for residential/built-up land use, using each building\\'s own sampled flood depth ' +
      '(the same curve Tab 5\\'s land-use damage analysis uses) -- not depth relative to building height.</p>' +
      '<div style="margin-top:6px;">' + bandsHtml + '</div>';
  }}
  const loss = summary && summary.estimated_building_loss;
  if (loss) {{
    html += '<div class="stat-row" style="margin-top:8px;"><span>Est. structure loss</span><span>Rs ' +
      Math.round(loss.structure_loss_pkr).toLocaleString() + '</span></div>' +
      '<div class="stat-row"><span>Est. content loss</span><span>Rs ' +
      Math.round(loss.content_loss_pkr).toLocaleString() + '</span></div>' +
      '<div class="stat-row" style="font-weight:600;"><span>Est. total building loss</span><span>Rs ' +
      Math.round(loss.total_loss_pkr).toLocaleString() + '</span></div>' +
      '<p class="note" style="opacity:0.85;">' + loss.method + '</p>';
  }}
  html += '</div>';
  if (!risk) {{ box.innerHTML = html + '<p class="note">No infrastructure data for this run.</p>'; return; }}
  const catLabels = {{roads:'Roads', critical:'Hospitals / Schools', power:'Power infrastructure',
                       railway:'Railway lines',
                       strategic:'Airports / Railway Stations / Sea Ports', trees:'Trees / forest',
                       shelters:'Emergency shelters', police:'Police', water_facilities:'Water supply / wastewater',
                       fuel_chemical:'Fuel / chemical sites', governance:'Government buildings', industry:'Industry / waste'}};
  for (const cat of Object.keys(risk)) {{
    const d = risk[cat];
    let bandsHtml = Object.entries(d.band_breakdown).map(([band,count]) =>
      '<span class="pill" style="margin-right:4px;">' + band + ': ' + count + '</span>').join('');
    html += '<div style="margin-bottom:16px;"><h3>' + (catLabels[cat]||cat) + '</h3>' +
      '<div class="stat-row"><span>Total features found</span><span>' + d.total_features + '</span></div>' +
      '<div class="stat-row"><span>In model domain</span><span>' + d.in_domain + '</span></div>' +
      '<div class="stat-row"><span>Flooded</span><span>' + d.flooded_count + ' (' + d.flooded_pct_of_in_domain.toFixed(1) + '%)</span></div>' +
      (d.road_detail ? '<div class="stat-row"><span>Major roads flooded (motorway to tertiary)</span><span>' + d.road_detail.major.flooded + ' of ' + d.road_detail.major.in_domain + ' (' + d.road_detail.major.flooded_km + ' km)</span></div>' +
        '<div class="stat-row"><span>Local roads flooded</span><span>' + d.road_detail.local.flooded + ' of ' + d.road_detail.local.in_domain + ' (' + d.road_detail.local.flooded_km + ' km)</span></div>' +
        '<div class="stat-row"><span>Bridges flooded</span><span>' + d.road_detail.bridges.flooded + ' of ' + d.road_detail.bridges.in_domain + '</span></div>' +
        '<div class="stat-row"><span>Flooded road length</span><span>' + d.road_detail.flooded_length_km + ' of ' + d.road_detail.total_length_km + ' km</span></div>' : '') +
      '<div style="margin-top:6px;">' + bandsHtml + '</div></div>';
  }}
  box.innerHTML = html;
}}

function renderDamage(summary) {{
  // Deliberately NOT auto-computed on run completion -- real Getis-Ord
  // Gi* hotspot analysis (99 permutations) is genuinely expensive and
  // used to run synchronously as part of every flood run, which is what
  // made the whole model "take forever". It's now purely on-demand: a
  // button the user clicks only if they actually want this analysis.
  const box = document.getElementById('damageBox');
  if (!box) return;
  box.innerHTML = '<button class="secondary" onclick="loadDamage()">Compute damage &amp; hotspots (can take a while for large areas)</button>';
}}

function loadDamage() {{
  const box = document.getElementById('damageBox');
  box.innerHTML = '<p class="note">Computing real depth-damage + Getis-Ord Gi* hotspot analysis...</p>';
  fetch('/runs/' + currentRunId + '/damage.json').then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    renderDamageResult(d);
  }});
}}

function renderDamageResult(d) {{
  const box = document.getElementById('damageBox');
  let html = '<div class="stat-row"><span>Mean damage fraction (whole AOI)</span><span>' +
    (d.mean_damage_fraction_overall * 100).toFixed(1) + '%</span></div>' +
    '<div class="stat-row"><span>Statistically significant hotspot cells</span><span>' + d.hotspot_cells + '</span></div>' +
    '<div class="stat-row"><span>Statistically significant coldspot cells</span><span>' + d.coldspot_cells + '</span></div>' +
    '<table style="width:100%;margin-top:10px;font-size:12.5px;border-collapse:collapse;">' +
    '<tr style="text-align:left;color:var(--ink-2);"><th>Land use</th><th>Flooded %</th><th>Mean damage (flooded)</th></tr>';
  for (const [label, c] of Object.entries(d.class_breakdown)) {{
    html += '<tr><td>' + label + '</td><td>' + c.flooded_pct.toFixed(1) + '%</td><td>' +
      (c.mean_damage_fraction_in_flooded * 100).toFixed(1) + '%</td></tr>';
  }}
  html += '</table>';
  const agLoss = d.estimated_agricultural_loss;
  if (agLoss && agLoss.agricultural_loss_pkr > 0) {{
    html += '<div class="stat-row" style="margin-top:10px;font-weight:600;"><span>Est. agricultural loss</span><span>Rs ' +
      Math.round(agLoss.agricultural_loss_pkr).toLocaleString() + '</span></div>' +
      '<div class="stat-row"><span>Cropland in AOI</span><span>' + agLoss.cropland_area_km2 + ' km&sup2;</span></div>' +
      '<p class="note" style="opacity:0.85;">' + agLoss.method + '</p>';
  }}
  html += '<div style="margin-top:10px;display:flex;gap:8px;">' +
    '<button class="secondary" onclick="showOnMap(\\'/runs/' + currentRunId + '/damage-mapbox3d\\')">Show damage on map</button>' +
    '<button class="secondary" onclick="showOnMap(\\'/runs/' + currentRunId + '/hotspot-mapbox3d\\')">Show hotspots on map</button></div>';
  box.innerHTML = html;
}}

function renderSediment(summary) {{
  // Same lazy, on-demand pattern as renderDamage: reads the run's
  // already-saved per-timestep rasters straight off disk (no
  // re-simulation), so it's genuinely cheap -- but still not something
  // every run needs, so it stays a click, not automatic.
  const box = document.getElementById('sedimentBox');
  if (!box) return;
  box.innerHTML = '<button class="secondary" onclick="loadSediment()">Compute sediment transport risk</button>';
}}

function updateSedimentD50Mode() {{
  const auto = document.getElementById('sedimentD50Auto').checked;
  document.getElementById('sedimentD50').disabled = auto;
  document.getElementById('sedimentD50AutoNote').style.display = auto ? '' : 'none';
}}

function loadSediment() {{
  const box = document.getElementById('sedimentBox');
  box.innerHTML = '<p class="note">Computing real Meyer-Peter &amp; Muller bedload transport capacity...</p>';
  const auto = document.getElementById('sedimentD50Auto').checked;
  const qs = auto ? '' : ('?d50_mm=' + encodeURIComponent(document.getElementById('sedimentD50').value || 2.0));
  fetch('/runs/' + currentRunId + '/sediment.json' + qs).then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">' + d.error + '</p>'; return; }}
    if (auto) document.getElementById('sedimentD50').value = d.d50_mm.toFixed(2);
    renderSedimentResult(d);
  }});
}}

function renderSedimentResult(d) {{
  const box = document.getElementById('sedimentBox');
  const c = d.risk_band_counts || {{}};
  let html = '<div class="stat-row"><span>d50 used</span><span>' + d.d50_mm.toFixed(2) + ' mm</span></div>' +
    '<p class="note" style="margin-top:-4px;margin-bottom:6px;">' + d.d50_source + '</p>' +
    '<p class="note" style="margin-top:-4px;margin-bottom:6px;">Bed shear stress: ' + d.shear_stress_method + '</p>' +
    '<div class="stat-row"><span>Wet cells</span><span>' + d.wet_cells + '</span></div>' +
    '<div class="stat-row"><span>Actively transporting cells</span><span>' + d.actively_transporting_cells + '</span></div>' +
    '<div class="stat-row"><span>Peak bedload transport (p99, robust)</span><span>' + d.max_bedload_transport_m2s.toExponential(2) + ' m&sup2;/s</span></div>' +
    '<div style="margin-top:6px;">' +
    '<span class="pill" style="margin-right:4px;">low: ' + (c.low||0) + '</span>' +
    '<span class="pill" style="margin-right:4px;">moderate: ' + (c.moderate||0) + '</span>' +
    '<span class="pill" style="margin-right:4px;">high: ' + (c.high||0) + '</span>' +
    '<span class="pill" style="margin-right:4px;">severe: ' + (c.severe||0) + '</span></div>' +
    '<p class="note" style="margin-top:8px;">' + d.note + '</p>' +
    '<div style="margin-top:10px;"><button class="secondary" onclick="showOnMap(\\'/runs/' + currentRunId +
    '/sediment-mapbox3d?d50_mm=' + encodeURIComponent(d.d50_mm) + '\\')">Show sediment risk on map</button></div>';
  box.innerHTML = html;
}}

// Points the single 3D map pane at any of this app's mapbox3d routes
// on demand (damage/hotspot/NbS-diff overlays) -- outside the normal
// tier3dToken flow since these are user-triggered, not tied to a tier tab.
function showOnMap(url) {{
  const frame = document.getElementById('view3dFrame');
  frame.removeAttribute('srcdoc');
  frame.src = url;
}}

// Same 3-reference-point linear interpolation as flood_model.py's
// amc_infiltration_multiplier -- a live preview only, the real value used
// for the run is always computed server-side from the same slider number.
function amcMultiplierPreview(x) {{
  const pts = [[0, 1.8], [50, 1.0], [100, 0.25]];
  x = Math.max(0, Math.min(100, x));
  for (let i = 1; i < pts.length; i++) {{
    if (x <= pts[i][0]) {{
      const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
      const t = (x - x0) / (x1 - x0);
      return y0 + t * (y1 - y0);
    }}
  }}
  return pts[pts.length - 1][1];
}}
function updateAmcLabel() {{
  const x = parseFloat(document.getElementById('amc').value);
  const mult = amcMultiplierPreview(x);
  const desc = x < 33.3 ? 'Dry-leaning' : x > 66.7 ? 'Wet-leaning' : 'Average';
  document.getElementById('amcLabel').textContent = desc + ' · ' + x.toFixed(0) + '/100 · infiltration x' + mult.toFixed(2);
}}

fetch('/dem-source').then(r => r.json()).then(d => {{
  document.getElementById('demSource').textContent = 'DEM: ' + d.label;
}});

// ---- load a plan imported from Islamabad SimCity (?import=<id>) ----
(function () {{
  const id = new URLSearchParams(location.search).get('import');
  if (!id) return;
  const banner = (html, bad) => {{
    const b = document.createElement('div');
    b.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);top:10px;z-index:9999;max-width:640px;padding:10px 16px;border-radius:10px;font:13px system-ui;box-shadow:0 4px 18px rgba(0,0,0,.35);background:' + (bad ? '#7f1d1d' : '#0f3d2e') + ';color:#fff;';
    b.innerHTML = html + ' <a href="#" style="color:#9be7c4;margin-left:8px" onclick="this.parentNode.remove();return false;">dismiss</a>';
    document.body.appendChild(b);
  }};
  fetch('/import/' + encodeURIComponent(id) + '.json').then(r => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }}).then(d => {{
    importedPlan = {{id: id, name: d.name, buildings: d.buildings.length, drains: d.drains.length}};
    const go = () => {{
      draw.deleteAll();
      draw.add({{type: 'Feature', properties: {{}}, geometry: d.aoi}});
      fetch('/area/polygon', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{geometry: d.aoi}})}})
        .then(r => r.json()).then(a => setArea(d.aoi, a.area_km2));
      const xs = d.aoi.coordinates[0].map(p => p[0]), ys = d.aoi.coordinates[0].map(p => p[1]);
      map.fitBounds([[Math.min.apply(null, xs), Math.min.apply(null, ys)], [Math.max.apply(null, xs), Math.max.apply(null, ys)]], {{padding: 80, duration: 800}});
      const cb = document.getElementById('includeBuildings'); if (cb) cb.checked = true;
      banner('<b>Imported from Islamabad SimCity:</b> ' + d.name + ' &mdash; ' + d.buildings.length + ' proposed buildings and ' + d.drains.length + ' drains. They replace the real buildings of this area (keep &ldquo;Include real buildings&rdquo; ticked to use them). Set your parameters and run.', false);
    }};
    if (map.loaded()) go(); else map.once('load', go);
  }}).catch(e => banner('Could not load the imported plan (' + e.message + '). Open the link from SimCity again.', true));
}})();
</script>
</body>
</html>
"""


def _landing_html():
    # read from disk each time so the page can be tuned without a restart; APP_VERSION is the only placeholder
    with open(os.path.join(BASE_DIR, "landing.html"), encoding="utf-8") as f:
        return f.read().replace("__APP_VERSION__", APP_VERSION)


@app.route("/")
def index():
    # the entry page is the landing page; a SimCity hand-over (?import=<id>) goes straight to the app
    if request.args.get("import"):
        return redirect("/app?" + request.query_string.decode())
    resp = Response(_landing_html(), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/landing-assets/<path:rel>")
def landing_assets(rel):
    # images, depth illustrations and the two background videos used by the landing page
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, "landing_assets"), rel, max_age=3600)


APP_MODES = {
    "physics": {"title": "SAILAB Physics", "welcome": (
        "<li><b>Physical flood model</b>The full GPU solver: depth and speed through the storm.</li>"
        "<li><b>Impact and loss</b>Buildings, roads and facilities at risk, with loss in PKR.</li>"
        "<li><b>Mitigation</b>Test nature-based solutions and protection structures.</li>")},
    "glof": {"title": "SAILAB GLOF", "welcome": (
        "<li><b>Glacial lake outburst</b>Pick a lake and get a peak discharge from published methods.</li>"
        "<li><b>Real events</b>Documented outbursts and glacier speed near your area.</li>"
        "<li><b>Impact</b>Buildings, roads and facilities in the flood path.</li>")},
    "coastal": {"title": "SAILAB Coastal", "welcome": (
        "<li><b>Storm surge and tide</b>Return-period water levels from tide-gauge and model data.</li>"
        "<li><b>Sea-level rise</b>Add rise to any level and see the flooded coast.</li>"
        "<li><b>Animated surge</b>A fast preview, or a full physics run of a real surge.</li>")},
}


def _app_page(mode):
    """The same app shell for the three modelling pages; the body's data-mode decides which panels and tabs show."""
    cfg = APP_MODES[mode]
    html = PAGE.replace('<body class="view-hidden">', '<body class="view-hidden" data-mode="%s">' % mode, 1)
    links = [("instant", "/instant", "Instant"), ("physics", "/app", "Physics"), ("glof", "/glof", "GLOF"), ("coastal", "/coastal", "Coastal")]
    nav = '<div class="hdr-nav">' + "".join('<a href="%s"%s>%s</a>' % (u, ' class="on"' if k == mode else "", n) for k, u, n in links) + "</div>"
    html = html.replace("<!--MODENAV-->", nav, 1)
    html = html.replace("<title>%s</title>" % MODEL_NAME, "<title>%s</title>" % cfg["title"], 1)
    if cfg["welcome"]:
        html = re.sub(r'(<div class="wl">\s*<h2>[^<]*</h2>\s*)<ul>.*?</ul>', lambda m: m.group(1) + "<ul>" + cfg["welcome"] + "</ul>", html, count=1, flags=re.S)
        html = html.replace("<h2>%s</h2>" % MODEL_NAME, "<h2>%s</h2>" % cfg["title"], 1)
    return html


@app.route("/app")
def main_app():
    return _app_page("physics")


@app.route("/glof")
def glof_page():
    return _app_page("glof")


@app.route("/coastal")
def coastal_page():
    return _app_page("coastal")


@app.route("/instant")
def instant_page():
    """SAILAB Instant: the general, near-instant HAND flood preview as its own page (the physical model stays at /app)."""
    with open(os.path.join(BASE_DIR, "instant.html"), encoding="utf-8") as f:
        html = f.read().replace("__MAPBOX_TOKEN__", json.dumps(MAPBOX_TOKEN))
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/dem-source")
def dem_source():
    _, label = current_dem_source()
    return jsonify({"label": label})


@app.route("/export-video", methods=["POST"])
def export_video():
    """Every 3D flood-animation view has an "Export Video" button that
    records the live canvas client-side (MediaRecorder -- the only format
    a browser can produce this way is WebM, there's no reliable
    client-side path to MP4) and uploads the result here for a one-shot
    ffmpeg transcode to MP4, which is what actually gets downloaded.
    Uses imageio_ffmpeg's bundled static ffmpeg binary so this works
    without any system-level (root-requiring) ffmpeg install."""
    f = request.files.get("video")
    if not f:
        return jsonify({"error": "no video uploaded"}), 400

    tmp_id = uuid.uuid4().hex
    webm_path = os.path.join(AOI3D_CACHE, f"export_{tmp_id}.webm")
    mp4_path = os.path.join(AOI3D_CACHE, f"export_{tmp_id}.mp4")
    f.save(webm_path)
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.run(
            # libx264 requires even width/height (YUV420 chroma
            # subsampling) -- the recorded canvas is whatever pixel size
            # the browser window happened to be, often odd (e.g. a real
            # recording came in at 1240x687), which fails outright with
            # "height not divisible by 2" otherwise. The scale filter
            # rounds each dimension down to the nearest even number.
            [ffmpeg_exe, "-y", "-i", webm_path,
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", mp4_path],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not os.path.exists(mp4_path):
            return jsonify({"error": f"ffmpeg transcode failed: {proc.stderr[-1500:]}"}), 500
        # Read fully into memory before cleaning up -- send_file can hand
        # the file object off to the WSGI server's own sendfile() call,
        # which happens after this view function returns, so deleting the
        # path right after calling send_file can race a still-in-progress
        # send. Reading the bytes ourselves first avoids that entirely.
        with open(mp4_path, "rb") as fh:
            data = fh.read()
        return Response(data, mimetype="video/mp4", headers={
            "Content-Disposition": "attachment; filename=flood_animation.mp4"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for p in (webm_path, mp4_path):
            try:
                os.remove(p)
            except OSError:
                pass


@app.route("/import", methods=["POST"])
def import_plan():
    """Receives a plan from another app: {name, aoi (Polygon), buildings: [{coords: [[lon, lat], ...], h?}],
    drains: [{line: [[lon, lat], ...], width?}]}. Open the app with ?import=<id> to load it (area + proposed
    buildings and drains); /run then uses these instead of the real buildings."""
    d = request.json or {}
    aoi = d.get("aoi")
    if not aoi or aoi.get("type") != "Polygon":
        return jsonify({"error": "aoi must be a Polygon"}), 400
    imp_id = uuid.uuid4().hex[:16]
    keep = {"name": str(d.get("name") or "Imported plan")[:120], "source": str(d.get("source") or "")[:60], "aoi": aoi,
            "buildings": [b for b in d.get("buildings", []) if len(b.get("coords", [])) >= 4],
            "drains": [x for x in d.get("drains", []) if len(x.get("line", [])) >= 2], "created": time.time()}
    with open(os.path.join(IMPORTS, imp_id + ".json"), "w") as f:
        json.dump(keep, f)
    return jsonify({"id": imp_id, "buildings": len(keep["buildings"]), "drains": len(keep["drains"])})


def _load_import(imp_id):
    if not imp_id or not re.fullmatch(r"[0-9a-f]{8,32}", str(imp_id)):
        return None
    path = os.path.join(IMPORTS, str(imp_id) + ".json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


@app.route("/import/<imp_id>.json")
def import_get(imp_id):
    d = _load_import(imp_id)
    if d is None:
        abort(404)
    return jsonify(d)


@app.route("/area/polygon", methods=["POST"])
def area_polygon():
    geom = request.json["geometry"]
    area_km2 = polygon_area_km2(geom)
    return jsonify({"area_km2": area_km2})


@app.route("/area/upload", methods=["POST"])
def area_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    path = os.path.join(UPLOADS, f"{uuid.uuid4().hex}_{f.filename}")
    f.save(path)
    try:
        geom, area_km2 = load_polygon_from_upload(path)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"geometry": geom, "area_km2": area_km2})


@app.route("/area/rainfall-csv", methods=["POST"])
def area_rainfall_csv():
    """Real uploaded rainfall CSV -> a real time series to drive the
    physics run directly (see rainfall_csv_utils.py), instead of a
    manually-entered peak mm/hr + duration.

    Station selection, by real data availability, not a single fixed
    strategy: a CSV with real lat/lon columns picks the real nearest-to-
    this-AOI station automatically (real haversine distance). A CSV with
    NO real coordinates at all (text addresses only) does NOT try to
    auto-pick via geocoding when there's more than one station -- Mapbox's
    public geocoder has no distinct point for many real sub-city Pakistani
    addresses (see rainfall_csv_utils.geocode_location_text's own
    docstring: Islamabad's own CDA sector codes all collapse to the same
    city centroid), so an automated "nearest" among them would be
    arbitrary, not genuinely informed. Instead this returns the real list
    of stations found and asks the user to pick the one that matches
    wherever they've actually drawn the AOI -- the user's own real
    knowledge of their own data beats an approximate automated guess.
    A single-station file never needs picking (nothing to choose between)
    regardless of whether it has coordinates."""
    f = request.files.get("file")
    geom_raw = request.form.get("geometry")
    if not f or not geom_raw:
        return jsonify({"error": "no file or no area selected"}), 400
    try:
        geom = json.loads(geom_raw)
        poly = shapely_shape(geom)
        lon_c, lat_c = poly.centroid.x, poly.centroid.y
    except Exception as e:
        return jsonify({"error": f"invalid area geometry: {e}"}), 400

    try:
        stations = rainfall_csv_utils.parse_rainfall_csv(f.read())
    except rainfall_csv_utils.RainfallCsvError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"could not parse this CSV: {e}"}), 400

    has_any_real_coords = any(s["lat"] is not None and s["lon"] is not None for s in stations.values())
    requested_key = request.form.get("station_key")

    if not has_any_real_coords and requested_key is None and len(stations) > 1:
        return jsonify({
            "needs_station_choice": True,
            "note": ("This CSV has no real lat/lon columns and more than one station -- pick which "
                     "one matches wherever you've drawn the AOI (auto-selecting by geocoding a text "
                     "address isn't reliable enough for sub-city addresses to trust automatically)."),
            "stations": [{"key": k, "location_text": s["location_text"],
                           "peak_mm_hr": s["peak_mm_hr"], "total_mm": s["total_mm"],
                           "n_points": s["n_points"]} for k, s in stations.items()],
        })

    distance_km = None
    geocode_note = None
    if requested_key is not None:
        if requested_key not in stations:
            return jsonify({"error": f"station '{requested_key}' not found in this CSV"}), 400
        station_key = requested_key
    elif len(stations) == 1:
        station_key = next(iter(stations))
    else:
        rainfall_csv_utils.resolve_station_coordinates(stations, MAPBOX_TOKEN)
        station_key, distance_km = rainfall_csv_utils.nearest_station(stations, lon_c, lat_c)
        if station_key is None:
            return jsonify({"error": "Could not resolve a real location for any station in this CSV "
                                      "(no lat/lon columns, and geocoding its location text failed)."}), 400
        st_sel = stations[station_key]
        if st_sel["location_text"]:
            # Real, stated limitation (see geocode_location_text's own
            # docstring): Mapbox's public geocoder has no distinct point
            # for granular sub-city addresses -- multiple same-city
            # stations can resolve to an identical approximate
            # coordinate, so "nearest station" among them is only as
            # precise as that shared approximation.
            same_coord = sum(1 for s in stations.values()
                              if s["lat"] == st_sel["lat"] and s["lon"] == st_sel["lon"])
            if same_coord > 1:
                geocode_note = (f"Note: {same_coord} station(s) in this file resolved to the same "
                                 f"approximate geocoded location (Mapbox's public geocoder doesn't "
                                 f"have distinct coordinates for every sub-city address) -- if you have "
                                 f"real per-station coordinates, add lat/lon columns to the CSV instead "
                                 f"for precise nearest-station selection.")

    st = stations[station_key]
    return jsonify({
        "station_name": station_key,
        "location_text": st["location_text"],
        "n_stations": len(stations),
        "distance_km": round(distance_km, 2) if distance_km is not None else None,
        "times_hr": st["times_hr"],
        "rates_mm_hr": st["rates_mm_hr"],
        "peak_mm_hr": st["peak_mm_hr"],
        "total_mm": st["total_mm"],
        "span_hr": max(st["times_hr"], default=0.0),
        "label": f"{station_key}" + (f" ({st['location_text']})" if st["location_text"] else ""),
        "geocode_note": geocode_note,
        "start_time_iso": st["start_time_iso"],
        "end_time_iso": st["end_time_iso"],
        "peak_time_iso": st["peak_time_iso"],
    })


@app.route("/area/context", methods=["POST"])
def area_context():
    """Real rainfall for whatever polygon is selected -- independent of
    whether the flood model has been run yet. (River/stream/lake context
    was removed at the user's request -- see CLAUDE.md.) Also includes the
    pluvial/flash-flood risk tier (pluvial_utils) -- likewise independent
    of any flood run, since it's driven off rainfall + this point's own
    climatology, not the physics solver."""
    from shapely.geometry import shape
    geom = request.json["geometry"]
    poly = shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2

    try:
        rainfall = fetch_rainfall(lat_c, lon_c)
    except Exception as e:
        rainfall = {"error": str(e)}

    pluvial = None
    if isinstance(rainfall, dict) and "error" not in rainfall:
        try:
            pluvial = assess_pluvial_risk(lat_c, lon_c, rainfall)
        except Exception as e:
            pluvial = {"error": str(e)}

    return jsonify({"rainfall": rainfall, "pluvial": pluvial})


@app.route("/area/instant-flood-mapbox3d", methods=["POST"])
def area_instant_flood_mapbox3d():
    """Tier 2's own 3D visualization: real Mapbox terrain + the HAND-based
    instant flood depth overlay for the given discharge, via the exact
    same rendering path Tier 3 uses for its own animated flood overlay
    (build_mapbox_flood_scene) -- just given a single-frame "water" stack
    instead of a real per-timestep one, since Tier 2 is inherently a
    single-discharge snapshot, not an animation over time.

    Known approximation, stated plainly: the overlay's bbox is the AOI
    polygon's own real WGS84 bounds, not the HAND grid's exact (UTM-
    rectangle-reprojected) bounds -- those two differ very slightly for
    AOIs away from the equator (a UTM rectangle isn't perfectly axis-
    aligned in WGS84). Fine for a visual overlay; the actual Tier 2
    numbers (area/depth) come from the real grid, unaffected by this."""
    from shapely.geometry import shape
    body = request.json
    geom = body["geometry"]
    discharge_m3s = float(body.get("discharge_m3s", 50.0))
    allow_global = bool(body.get("use_global_dem", False))
    try:
        hand_result, dem_label, _ = _resolve_hand_result(geom, allow_global)
        depth_grid, _ = discharge_to_inundation(hand_result, discharge_m3s)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": f"instant-flood 3D failed: {e}"}), 400

    bbox = list(shape(geom).bounds)
    max_depth = float(depth_grid.max())
    water = {
        "depth_stack": depth_grid[np.newaxis, :, :].astype("float32"),
        "save_int_min": 1.0, "dry_thresh": 0.05, "max_depth": max_depth,
    }
    legend = (f"<b>Instant flood preview</b> (Tier 2, HAND + rating curve)<br>"
              f"Discharge {discharge_m3s:.0f} m&sup3;/s &middot; max depth {max_depth:.2f}m<br>"
              f"DEM: {dem_label}")
    html_path = os.path.join(AOI3D_CACHE, f"instantflood_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(html_path, "Instant flood (Mapbox)", bbox, geom, MAPBOX_TOKEN,
                                  water=water, legend_html=legend)
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


def _resolve_hand_result(geom, allow_global):
    """Shared by /area/instant-flood and /area/instant-flood-library: the
    HAND computation (flow direction/accumulation/depression-fill) is the
    expensive part and is identical regardless of which discharge value(s)
    are asked for, so both routes go through the same cache
    (hand_cache/, keyed by AOI geometry + resolved DEM source)."""
    tmp_tif = os.path.join(AOI3D_CACHE, f"instanthand_{uuid.uuid4().hex}.tif")
    try:
        dem_source, dem_label = current_dem_source(geom, allow_global=allow_global)
        hand_key = polygon_geometry_hash({"geometry": geom, "dem": dem_source})
        hand_cache_path = os.path.join(HAND_CACHE, f"{hand_key}.pkl")

        hand_result = None
        if os.path.exists(hand_cache_path):
            hand_result = load_hand_result(hand_cache_path)
            if "filled" not in hand_result:    # cached before the ponding layers existed
                hand_result = None
        if hand_result is None:
            header, active_cells, total_cells = crop_dem_to_polygon(
                dem_source, geom, tmp_tif.replace(".tif", ".dem.ascii"))
            ascii_path = tmp_tif.replace(".tif", ".dem.ascii")
            # hand_utils/pysheds reads via rasterio, which needs a real raster
            # (GeoTIFF), not the ARC ASCII crop_dem_to_polygon writes for
            # LISFLOOD-FP -- convert once here.
            _, dem_arr = _read_ascii(ascii_path)
            import rasterio
            from rasterio.transform import from_origin
            transform = from_origin(header["xllcorner"], header["yllcorner"] + header["nrows"] * header["cellsize"],
                                     header["cellsize"], header["cellsize"])
            with rasterio.open(tmp_tif, "w", driver="GTiff", height=header["nrows"], width=header["ncols"],
                                count=1, dtype="float32", crs=header["src_crs"], transform=transform,
                                nodata=-9999.0) as dst:
                dst.write(dem_arr.astype("float32"), 1)

            hand_result = compute_hand(tmp_tif)
            save_hand_result(hand_result, hand_cache_path)
        return hand_result, dem_label, hand_cache_path
    finally:
        for p in (tmp_tif, tmp_tif.replace(".tif", ".dem.ascii")):
            try:
                os.remove(p)
            except OSError:
                pass


@app.route("/area/instant-flood-library", methods=["POST"])
def area_instant_flood_library():
    """Tier 0's own "precomputed inundation-extent library per discharge
    level" (global-flood-prediction-research.md) -- no such library
    exists as a downloadable dataset anywhere (it's always a derived
    product, not a distributed one -- see CLAUDE.md for what was actually
    checked), so this builds the real thing for THIS AOI: once its HAND
    is computed (or loaded from hand_cache/), sweeps a fixed set of
    discharge levels through the same cheap discharge_to_inundation
    lookup and returns summary stats for each. Cached alongside the HAND
    result (a sibling .json) so a repeat request for the same AOI is a
    disk read, not a recompute. The frontend uses this to let the
    discharge slider interpolate instantly client-side instead of a fresh
    server round-trip per drag tick."""
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))
    try:
        hand_result, dem_label, hand_cache_path = _resolve_hand_result(geom, allow_global)
        library_path = hand_cache_path.replace(".pkl", ".library.json")
        if os.path.exists(library_path):
            with open(library_path) as f:
                library = json.load(f)
        else:
            library = build_inundation_library(hand_result)
            _tmp_lib = library_path + f".tmp{os.getpid()}_{threading.get_ident()}"
            with open(_tmp_lib, "w") as f:
                json.dump(library, f)
            os.replace(_tmp_lib, library_path)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": f"instant-flood library failed: {e}"}), 400

    return jsonify({
        "method": "HAND + synthetic rating curve, swept across discharge levels (Tier 0/2 library -- see hand_utils.py)",
        "dem_source": dem_label,
        "library": library,
    })


INSTANT_DEPTH_BINS = [  # lower edge (m), RGB -- same steps as the legend on the Instant page
    (0.05, (176, 224, 245)), (0.10, (140, 200, 240)), (0.25, (86, 166, 226)), (0.50, (34, 126, 204)),
    (1.00, (16, 90, 170)), (2.00, (10, 56, 120)), (5.00, (6, 30, 78)),
]
INSTANT_DEPTH_FRACTIONS = [0.0, 0.05, 0.10, 0.20, 0.35, 0.55, 0.80]   # class lower edges as a share of the deepest water


@app.route("/area/instant-flood-overlay", methods=["POST"])
def area_instant_flood_overlay():
    """Instant flood depth as a coloured image for the 2D map: PNG + the four corners of the terrain grid in
    lon/lat (a Mapbox image source takes any quadrilateral, so the grid's real UTM footprint is placed exactly)."""
    import base64
    import io
    from PIL import Image
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))
    custom_dem_id = body.get("custom_dem_id") or None
    rain_mm_hr = max(0.0, float(body.get("rain_mm_hr", 10.0)))
    duration_hr = max(0.1, float(body.get("duration_hr", 3.0)))
    runoff_c = 0.5
    area_km2 = polygon_area_km2(geom)
    discharge = float(body["discharge_m3s"]) if body.get("discharge_m3s") not in (None, "") else runoff_c * rain_mm_hr * area_km2 / 3.6
    try:
        hand_result, dem_label, hand_cache_path = _resolve_hand_result(geom, allow_global)
        header_path = hand_cache_path.replace(".pkl", ".header.json")
        if os.path.exists(header_path):
            with open(header_path) as f:
                header = json.load(f)
        else:
            dem_source, _ = current_dem_source(geom, allow_global=allow_global, custom_dem_id=custom_dem_id)
            tmp = os.path.join(AOI3D_CACHE, f"ovh_{uuid.uuid4().hex}.dem.ascii")
            try:
                header, _, _ = crop_dem_to_polygon(dem_source, geom, tmp)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            with open(header_path, "w") as f:
                json.dump(header, f)
        channel, _ = discharge_to_inundation(hand_result, discharge)
        ponding = pluvial_ponding_depth(hand_result, runoff_c * rain_mm_hr * duration_hr)
        depth = np.maximum(channel, ponding)
        learned_used = None
        if body.get("method") == "learned":
            import surrogate_utils
            if not surrogate_utils.available():
                return jsonify({"error": "the learned estimate is not installed on this server"}), 400
            raw = hand_result["dem_raw"]
            depth = surrogate_utils.predict_depth(raw, header, rain_mm_hr, duration_hr, float(body.get("friction", 0.05)),
                                                  float(body.get("infiltration_mm_hr", 5.0)), valid=raw > -9000)
            learned_used = surrogate_utils.model_name()
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"instant-flood overlay failed: {e}"}), 400
    nrows, ncols = depth.shape
    if (nrows, ncols) != (int(header["nrows"]), int(header["ncols"])):
        return jsonify({"error": "terrain grid and cached header disagree; clear this area's cache and retry"}), 400
    rgba = np.zeros((nrows, ncols, 4), dtype="uint8")
    # relative legend: the class edges are fractions of THIS result's deepest water, so the colours always span
    # the result (a 0.3 m flood and a 5 m flood both use the whole ramp); 0.05 m stays the wet threshold
    dmax = float(depth.max())
    edges = [max(0.05, dmax * f) for f in INSTANT_DEPTH_FRACTIONS]
    for lo, (_, rgb) in zip(edges, INSTANT_DEPTH_BINS):
        m = depth >= lo
        rgba[m, 0], rgba[m, 1], rgba[m, 2], rgba[m, 3] = rgb[0], rgb[1], rgb[2], 215
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    cs = float(header["cellsize"]); x0, y0 = float(header["xllcorner"]), float(header["yllcorner"])
    x1, y1 = x0 + ncols * cs, y0 + nrows * cs
    to_wgs = Transformer.from_crs(header["src_crs"], "EPSG:4326", always_xy=True).transform
    corners = [list(to_wgs(x0, y1)), list(to_wgs(x1, y1)), list(to_wgs(x1, y0)), list(to_wgs(x0, y0))]  # TL, TR, BR, BL
    wet = int((depth > 0.05).sum())
    return jsonify({
        "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "coordinates": corners,
        "legend": [{"depth_m": round(e, 3), "rgb": list(rgb)} for e, (_, rgb) in zip(edges, INSTANT_DEPTH_BINS)],
        "flooded_area_km2": round(wet * cs * cs / 1e6, 4), "max_depth_m": round(float(depth.max()), 3),
        "discharge_m3s": round(discharge, 2), "rain_mm_hr": rain_mm_hr, "duration_hr": duration_hr,
        "rain_depth_mm": round(rain_mm_hr * duration_hr, 1), "area_km2": round(area_km2, 3),
        "ponded_area_km2": round(int((ponding > 0.05).sum()) * cs * cs / 1e6, 4),
        "channel_area_km2": round(int((channel > 0.05).sum()) * cs * cs / 1e6, 4),
        "cellsize_m": cs, "dem_source": dem_label, "method": "learned" if learned_used else "terrain", "model": learned_used,
    })


@app.route("/area/instant-scenarios", methods=["POST"])
def area_instant_scenarios():
    """Moderate / extreme rain presets for the Instant page, from the real local ERA5-Land/IMERG IDF curve at the area:
    a 3-hour storm at the 3-hour intensity (moderate) or at the 1-hour intensity (extreme)."""
    body = request.json
    poly = shapely_shape(body["geometry"])
    out = {"duration_hr": 3.0, "source": None}
    mod_i = ext_i = None
    if idf_utils.idf_data_available():
        try:
            m = idf_utils.real_design_intensity_for_duration(poly.centroid.y, poly.centroid.x, 3.0)
            e = idf_utils.real_design_intensity_for_duration(poly.centroid.y, poly.centroid.x, 1.0)
            if m and e:
                mod_i, ext_i, out["source"] = m["intensity_mm_hr"], e["intensity_mm_hr"], f"local rainfall curve, {m['season']} {m['year']}"
        except Exception:
            pass
    if mod_i is None:
        mod_i, ext_i, out["source"] = 6.0, 25.0, "default intensities (no local rainfall curve for this place)"
    out["moderate_mm_hr"], out["extreme_mm_hr"] = round(mod_i, 1), round(ext_i, 1)
    return jsonify(out)


@app.route("/area/instant-flood", methods=["POST"])
def area_instant_flood():
    """Tier 0/2 of the global-flood-prediction architecture
    (global-flood-prediction-research.md): a near-instant (no LISFLOOD-FP
    run) discharge -> inundation-extent lookup, using HAND (Height Above
    Nearest Drainage) computed from whatever DEM this AOI resolves to
    (Pakistan SRTM or the on-demand global Copernicus mosaic -- see
    current_dem_source). This is deliberately a cheap approximation (see
    hand_utils.py's own docstring) -- Tier 3 (the real /run endpoint,
    already wired to the same global DEM resolution) is still the
    authoritative result; this exists to make "instant, anywhere" possible
    at all, the same tradeoff every real product in this space makes."""
    body = request.json
    geom = body["geometry"]
    discharge_m3s = float(body.get("discharge_m3s", 50.0))
    allow_global = bool(body.get("use_global_dem", False))

    try:
        hand_result, dem_label, _ = _resolve_hand_result(geom, allow_global)
        total_cells = hand_result["shape"][0] * hand_result["shape"][1]
        depth_grid, _ = discharge_to_inundation(hand_result, discharge_m3s)
        cellsize_m = hand_result["cellsize_m"]
        wet_cells = int((depth_grid > 0.05).sum())
        area_km2 = wet_cells * (cellsize_m ** 2) / 1e6
        max_depth = float(depth_grid.max())
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": f"instant-flood lookup failed: {e}"}), 400

    return jsonify({
        "method": "HAND + synthetic rating curve (Tier 2, instant lookup -- see hand_utils.py)",
        "dem_source": dem_label,
        "discharge_m3s": discharge_m3s,
        "flooded_area_km2": round(area_km2, 4),
        "max_depth_m": round(max_depth, 3),
        "wet_cells": wet_cells,
        "total_cells": total_cells,
    })


def _resolve_coastal_dem(geom, allow_global):
    """Real DEM crop for the coastal analysis, reusing the exact same
    current_dem_source/crop_dem_to_polygon pipeline every other DEM-
    consuming route in this app already uses (Pakistan SRTM, or the
    opt-in global Copernicus mosaic outside its coverage)."""
    tmp_path = os.path.join(AOI3D_CACHE, f"coastal_{uuid.uuid4().hex}.dem.ascii")
    try:
        dem_source, dem_label = current_dem_source(geom, allow_global=allow_global)
        header, active_cells, total_cells = crop_dem_to_polygon(
            dem_source, geom, tmp_path)
        _, dem_arr = _read_ascii(tmp_path)
        return header, dem_arr, dem_label
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _compute_wave_runup(dem, valid_mask, still_water_level_m, cellsize, lon_c, lat_c):
    """Shared by both the static and dynamic coastal routes: real
    Stockdon et al. 2006 wave run-up, added on top of a still-water
    level. Real inputs only -- returns None (never a fabricated number)
    if the real Open-Meteo Marine API fetch fails, or if the AOI has no
    real, usable shore transect to estimate a foreshore slope from (see
    coastal_utils.estimate_foreshore_slope_from_dem's own docstring for
    when that happens) -- in that case DEFAULT_FORESHORE_SLOPE (a real
    published typical value for Pakistan's gently-sloping tidal-flat
    coast) is used instead, not a hard failure, since run-up still has a
    real (if less precise) answer without a site-specific slope."""
    wave = fetch_real_wave_conditions(lon_c, lat_c)
    if wave is None:
        return None
    slope = estimate_foreshore_slope_from_dem(dem, valid_mask, still_water_level_m, cellsize)
    slope_basis = "estimated from this AOI's own real DEM (shore-to-inland elevation transects)"
    if slope is None:
        slope = DEFAULT_FORESHORE_SLOPE
        slope_basis = ("real published typical value for a gently-sloping sandy/muddy tidal-flat coast "
                        "(this AOI's DEM didn't have a usable real shore transect to measure one)")
    runup_m = wave_runup_stockdon_m(wave["significant_wave_height_m"], wave["peak_period_s"], slope)
    return {
        "runup_m": round(runup_m, 3),
        "significant_wave_height_m": wave["significant_wave_height_m"],
        "peak_period_s": wave["peak_period_s"],
        "wave_basis": wave["basis"],
        "foreshore_slope_used": round(slope, 4),
        "foreshore_slope_basis": slope_basis,
        "method": "Stockdon et al. 2006 2% exceedance wave run-up (R2%), additive to the still-water "
                  "level above -- real wave data, real or documented-typical slope, not modeled as a "
                  "true joint-probability combination with tide/surge.",
    }


def _pick_coastal_hvar_edge(header, dem, nodata):
    """Picks which of the DEM crop's 4 rectangular edges (N/E/S/W) to
    place a real open-sea HVAR water-level boundary on. A real coastal AOI
    crop routinely has open water as NODATA (SRTM has no data over most
    water bodies) with real land clustered toward one side -- confirmed
    directly on a real Karachi-coast test crop, where 3 of 4 edges were
    100% NODATA and only the edge nearest the real coastline carried any
    valid land cells at all. An edge with zero valid cells can't usefully
    carry a boundary condition (LISFLOOD-FP's domain only includes real
    valid cells; a boundary line over pure NODATA touches nothing), so
    this picks, among edges that DO have at least one valid cell, the one
    with the LOWEST mean valid elevation -- physically the edge closest to
    sea level, i.e. most likely the real seaward side."""
    edges = {"N": dem[0, :], "S": dem[-1, :], "W": dem[:, 0], "E": dem[:, -1]}
    candidates = []
    for edge, arr in edges.items():
        valid = arr != nodata
        if valid.any():
            candidates.append((float(arr[valid].mean()), edge))
    if not candidates:
        raise RuntimeError("No edge of this AOI's DEM crop touches any real land -- draw the AOI so it "
                            "includes some real coastline, not just open water.")
    candidates.sort()
    mean_elev, edge = candidates[0]
    return edge, mean_elev


def run_coastal_dynamic_flood(geom, return_period_years=None, target_year=None, custom_surge_m=None,
                               anchor_mode="static", window_days=5, allow_global=False, combine_tide=False,
                               include_wave_runup=False, rain_mm_hr=0.0, duration_hr=0.0,
                               water_level_source="khan", extra_slr_m=0.0, tide_phase=None):
    """Real dynamic coastal storm-surge run: a genuine LISFLOOD-FP physics
    run (not the static bathtub method) driven by a real time-varying
    water-level (HVAR) boundary condition, itself built from a real GTSR

    Real, confirmed gap this closes (rain_mm_hr/duration_hr, both 0.0 by
    default -- unchanged, coastal-only behavior when omitted): every prior
    version of this function hardcoded `--rain-mm-hr 0 --duration-hr 0`,
    so a real compound event (heavy rain/river discharge happening AT THE
    SAME TIME as an elevated ocean level) could never be modeled here --
    the domain's own outlet boundary elsewhere in this app (a plain
    `FREE 0.001` condition, see flood_model.write_bci_file) has zero real
    ocean-level awareness either, so neither path could show real
    BACKWATER (rainfall/river water held back and re-flooding upstream
    because the sea is already high) on its own. flood_model.py's own
    `main()` already generalizes `has_boundary_inflow` and
    `inflow_initial_tstep` to cover a coastal HVAR boundary combined with
    real rainfall (confirmed by reading its source, not assumed) -- this
    was purely a missing pass-through in this one call site, not a deeper
    solver limitation. Passing a real nonzero rain_mm_hr/duration_hr here
    now drives real rainfall THROUGH THE SAME conditioned DEM while the
    coastal HVAR boundary holds/raises the outlet level, so any real
    backwater the physics itself produces shows up in the result exactly
    as it would in reality -- not a separate, disconnected compound-event
    approximation.
    (Global Tide and Surge Reanalysis, Muis et al. 2016) historical
    episode's own real rise/peak/recede daily-maxima shape at the nearest
    real station to this AOI -- see coastal_utils.py.

    Runs on CPU, not CUDA, always: a real, direct CPU-vs-GPU res.mass Vol
    comparison on a real Karachi-coast DEM (176x156 real Pakistan SRTM
    crop, EPSG:32642, real UTM coordinates) showed the CUDA build silently
    injecting EXACTLY ZERO real water for an HVAR boundary at every
    timestep (Vol/Area stayed 0.0 for the whole run even once the real
    water level rose well above every boundary cell's real elevation),
    while the CPU build on the IDENTICAL input correctly showed Vol
    growing to >700,000 m^3 by the time the level crossed the real
    boundary elevation -- the exact same silent-failure signature already
    found and fixed for QVAR river inflow (see flood_model.py's
    write_par_file docstring and CLAUDE.md). flood_model.py's own
    has_boundary_inflow mechanism (already generalized to cover
    --coastal-hvar-json, not just --boundary-json) forces this
    automatically; nothing here needs to force it again, but this
    docstring records the real verification that justified trusting it."""
    if not gtsr_available():
        raise RuntimeError("Real GTSR dynamic surge data is not available on this server "
                            "(see coastal_utils.gtsr_available()) -- use the static coastal method instead.")

    job_dir = os.path.join(COASTAL_DYN_CACHE, uuid.uuid4().hex)
    os.makedirs(job_dir, exist_ok=True)
    dem_path = os.path.join(job_dir, "aoi.dem.ascii")
    dem_source, dem_label = current_dem_source(geom, allow_global=allow_global)
    header, active_cells, total_cells = crop_dem_to_polygon(
        dem_source, geom, dem_path)
    _, dem = _read_ascii(dem_path)
    nodata = header.get("nodata_value", -9999.0)
    valid_mask = dem != nodata

    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lon_c, lat_c = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2

    coastal, reason = is_plausibly_coastal(dem, valid_mask, lat_c)
    if not coastal:
        raise RuntimeError(f"This AOI doesn't look coastal: {reason}")

    station = nearest_gtsr_station(lon_c, lat_c)
    if station is None:
        raise RuntimeError("GTSR station lookup failed even though gtsr_available() was true.")

    wl = total_water_level_m(
        return_period_years=return_period_years if custom_surge_m is None else None,
        target_year=target_year, custom_surge_m=custom_surge_m,
        source=water_level_source, lon=lon_c, lat=lat_c, extra_slr_m=extra_slr_m)

    tide_combination_applied = False
    tide_combination_note = None
    use_harmonic_tide = bool(tide_phase)
    if use_harmonic_tide:
        import tide_utils
        if tide_phase not in tide_utils.TIDE_PHASES:
            raise RuntimeError(f"unknown tide phase '{tide_phase}'")
        if not tide_utils.available():
            raise RuntimeError("the Karachi tide-gauge data is not available on this server, so the astronomical tide cannot be added")
        if combine_tide:
            tide_combination_note = "the flat mean-tidal-range option was ignored because the astronomical tide is used instead"
            combine_tide = False
    if anchor_mode == "gtsr":
        win = real_surge_window(station["index"], window_days=window_days)
        if win is None or win["peak_surge_m"] <= 0:
            raise RuntimeError(f"Station '{station['name']}' has no usable real GTSR surge record to anchor to.")
        anchor_peak_m = win["peak_surge_m"]
        anchor_basis = (f"real recorded GTSR historical peak storm-surge at station '{station['name']}' "
                         f"({station['distance_km']} km away): {anchor_peak_m:.2f} m -- SURGE component only "
                         f"(excludes tide), 1979-2014 record")
        if combine_tide:
            # Real published mean tidal range (Khan et al. 2020, Karachi Port)
            # added on top of the real recorded surge peak -- a standard,
            # conservative "surge coincides with high tide" combination
            # (added in full, not modeled as a true non-linear tide-surge
            # interaction, which this app has no data/model to represent).
            # Not applied in "static" anchor mode above: that mode's own
            # anchor is already Khan et al.'s published TOTAL water level,
            # which already includes tide -- adding it again there would
            # double-count it.
            anchor_peak_m = round(anchor_peak_m + MEAN_TIDAL_RANGE_M, 3)
            anchor_basis += (f" + real mean tidal range (Karachi Port, {MEAN_TIDAL_RANGE_M} m, Khan et al. 2020) "
                              f"added on top (conservative surge-at-high-tide assumption) = {anchor_peak_m:.2f} m total")
            tide_combination_applied = True
    else:
        anchor_peak_m = wl["total_water_level_m"]
        anchor_basis = (f"existing static method's own published total water level: {anchor_peak_m:.2f} m "
                         f"({wl['basis']})")
        if combine_tide:
            tide_combination_note = ("ignored -- the static method's own total water level already includes "
                                      "the real mean tidal range (Khan et al. 2020); adding it again would "
                                      "double-count it")

    wave_runup = None
    if include_wave_runup:
        wave_runup = _compute_wave_runup(dem, valid_mask, anchor_peak_m, header["cellsize"], lon_c, lat_c)
        if wave_runup is not None:
            anchor_peak_m = round(anchor_peak_m + wave_runup["runup_m"], 3)
            anchor_basis += (f" + real Stockdon et al. 2006 wave run-up ({wave_runup['runup_m']:.2f} m, "
                              f"riding atop the peak) = {anchor_peak_m:.2f} m total")

    tide_info = None
    if use_harmonic_tide:
        # GTSR anchor = surge only (total = surge + tide); static anchor = a TOTAL level that already includes tide.
        kw = ({"surge_peak_m": anchor_peak_m} if anchor_mode == "gtsr" else {"total_peak_target_m": anchor_peak_m})
        series = build_tide_surge_series(station["index"], window_days=window_days, phase=tide_phase, **kw)
        if series is None:
            raise RuntimeError(f"Station '{station['name']}' has no usable real GTSR surge record to derive a "
                               f"real episode shape from.")
        tide_info = {"phase": tide_phase, "phase_meaning": tide_utils.TIDE_PHASES[tide_phase],
                     "tide_at_surge_peak_m": series["tide_at_surge_peak_m"], "surge_peak_used_m": series["surge_peak_used_m"],
                     "max_total_level_m": round(max(series["levels_m"]), 3),
                     "basis": "astronomical tide predicted from the real Karachi hourly tide-gauge record (harmonic fit), "
                              "added to the real GTSR surge episode shape"}
        anchor_basis += (f"; astronomical tide added ({tide_utils.TIDE_PHASES[tide_phase]}): tide at the surge peak "
                          f"{series['tide_at_surge_peak_m']:+.2f} m, surge scale {series['surge_peak_used_m']:.2f} m, "
                          f"highest total level {tide_info['max_total_level_m']:.2f} m above MSL")
    else:
        series = build_dynamic_water_level_series(station["index"], anchor_peak_water_level_m=anchor_peak_m,
                                                   window_days=window_days)
    if series is None:
        raise RuntimeError(f"Station '{station['name']}' has no usable real GTSR surge record to derive a "
                           f"real episode shape from.")

    edge, edge_mean_elev = _pick_coastal_hvar_edge(header, dem, nodata)
    hvar_spec = {"edge": edge, "name": "coast", "times_hr": series["times_hr"], "levels_m": series["levels_m"]}
    hvar_path = os.path.join(job_dir, "coastal_hvar.json")
    with open(hvar_path, "w") as f:
        json.dump(hvar_spec, f)

    log_path = os.path.join(job_dir, "run.log")
    cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_path,
           "--rain-mm-hr", str(rain_mm_hr), "--duration-hr", str(duration_hr),
           "--friction", "0.045", "--cfl", "0.7",
           "--infiltration-mm-hr", "0", "--amc", "50",
           "--coastal-hvar-json", hvar_path,
           "--site-name", "Coastal dynamic surge", "--workdir", job_dir]
    with SIM_SLOT.labelled("Coastal dynamic surge"):
        t0 = time.time()
        with open(log_path, "w") as logf:
            proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
        elapsed = time.time() - t0
    if proc.returncode != 0:
        with open(log_path) as fh:
            tail = fh.read()[-2000:]
        raise RuntimeError(f"flood model run failed (exit {proc.returncode}): {tail}")

    summary = {}
    summary_path = os.path.join(job_dir, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            summary = json.load(f)

    res_max_path = os.path.join(job_dir, "results", "res.max")
    _, depth_max = _read_ascii(res_max_path)
    depth_max_valid = np.where(depth_max == nodata, 0.0, depth_max)
    cellsize = header["cellsize"]
    flooded_cells = int((depth_max_valid > 0.05).sum())
    area_km2 = flooded_cells * (cellsize ** 2) / 1e6
    max_depth = float(depth_max_valid.max())

    with open(os.path.join(job_dir, "bbox_geom.json"), "w") as f:
        json.dump({"bbox": [lon_min, lat_min, lon_max, lat_max], "geometry": geom}, f)

    return {
        "job_dir_id": os.path.basename(job_dir),
        "dem_source": dem_label,
        "gtsr_station": station,
        "anchor_mode": anchor_mode,
        "anchor_peak_water_level_m": round(anchor_peak_m, 3),
        "anchor_basis": anchor_basis,
        "tide_combination_applied": tide_combination_applied,
        "tide_combination_note": tide_combination_note,
        "astronomical_tide": tide_info,
        "wave_runup": wave_runup,
        "boundary_edge": edge,
        "boundary_edge_mean_elev_m": round(float(edge_mean_elev), 2),
        "times_hr": series["times_hr"],
        "levels_m": series["levels_m"],
        "source_window": series["source_window"],
        "flooded_area_km2": round(area_km2, 4),
        "max_depth_m": round(max_depth, 3),
        "compound_rain_mm_hr": rain_mm_hr, "compound_duration_hr": duration_hr,
        "compound_event": rain_mm_hr > 0 and duration_hr > 0,
        "solver_coastal_hvar_summary": summary.get("coastal_hvar", {}),
        "elapsed_seconds": round(elapsed, 1),
        "method": ("Real dynamic HVAR (time-varying water-level) boundary in LISFLOOD-FP, driven by a real "
                   "GTSR historical-episode rise/peak/recede shape, magnitude-anchored as noted above -- "
                   "additive to (never replacing) the existing static Khan et al. 2020 bathtub method. Runs "
                   "on the GPU (CUDA) build, whose stage-boundary code was patched to match the CPU build; "
                   "see CLAUDE.md, 'coastal HVAR now runs on the GPU'."),
    }


@app.route("/area/coastal-flood-dynamic", methods=["POST"])
def area_coastal_flood_dynamic():
    """Starts a real, async, dynamic (GTSR-driven HVAR) coastal storm-surge
    physics run -- see run_coastal_dynamic_flood's own docstring for the
    real CUDA-vs-CPU verification behind why this always runs on CPU.
    Additive: the existing /area/coastal-flood static method is completely
    unaffected and still the default in the UI."""
    body = request.json
    geom = body["geometry"]
    if not gtsr_available():
        return jsonify({"error": "Real GTSR dynamic surge data is not available on this server -- "
                                  "use the static coastal method instead.", "gtsr_available": False}), 400
    job_id = _start_climate_job(
        run_coastal_dynamic_flood, geom=geom,
        return_period_years=body.get("return_period_years"),
        target_year=body.get("target_year"),
        custom_surge_m=body.get("custom_surge_m"),
        anchor_mode=body.get("anchor_mode", "static"),
        window_days=int(body.get("window_days", 3)),
        allow_global=bool(body.get("use_global_dem", False)),
        combine_tide=bool(body.get("combine_tide", False)),
        include_wave_runup=bool(body.get("include_wave_runup", False)),
        rain_mm_hr=float(body.get("rain_mm_hr", 0.0)), duration_hr=float(body.get("duration_hr", 0.0)),
        water_level_source=body.get("water_level_source", "khan"), extra_slr_m=float(body.get("extra_slr_m") or 0.0),
        tide_phase=(body.get("tide_phase") or None))
    return jsonify({"job_id": job_id})


@app.route("/coastal-dyn/<job_id>/mapbox3d")
def coastal_dyn_mapbox3d(job_id):
    """Real animated Mapbox 3D view of a finished dynamic coastal run --
    reuses the exact same per-timestep water-overlay path
    (_read_water_stack + build_mapbox_flood_scene) every other animated
    3D view in this app already uses, since run_coastal_dynamic_flood
    writes a real results/res-*.wd stack just like any other flood_model.py
    run."""
    with CLIMATE_JOBS_LOCK:
        job = CLIMATE_JOBS.get(job_id)
    if not job:
        return Response("<p style='font-family:sans-serif;padding:20px;'>Unknown job.</p>", mimetype="text/html")
    if job.get("status") != "done":
        return Response(f"<p style='font-family:sans-serif;padding:20px;'>Run not finished yet "
                        f"(status: {job.get('status')}).</p>", mimetype="text/html")
    result = job["result"]
    job_dir = os.path.join(COASTAL_DYN_CACHE, result["job_dir_id"])
    try:
        with open(os.path.join(job_dir, "bbox_geom.json")) as f:
            bg = json.load(f)
        water = _read_water_stack(job_dir)
        html_path = os.path.join(job_dir, "coastal_dyn_mb.html")
        build_mapbox_flood_scene(
            html_path, "Coastal dynamic surge (Mapbox)", bg["bbox"], bg["geometry"], MAPBOX_TOKEN, water=water,
            legend_html=(f"<b>Real dynamic GTSR-derived surge</b> &middot; anchor "
                         f"{result['anchor_peak_water_level_m']:.2f} m ({result['anchor_mode']}) &middot; "
                         f"station {result['gtsr_station']['name']} ({result['gtsr_station']['distance_km']} km) "
                         f"&middot; CPU run (real, verified HVAR/CUDA bug workaround)"))
        with open(html_path) as f:
            html = f.read()
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>"
                        f"Dynamic coastal 3D preview failed: {e}</p>", mimetype="text/html")
    return Response(html, mimetype="text/html")


@app.route("/area/coastal-flood", methods=["POST"])
def area_coastal_flood():
    """Real static ("bathtub-with-connectivity") coastal/tidal inundation
    -- see coastal_utils.py's own docstring for the real published
    reference values used and the honest limitations of a static (not
    dynamic storm-surge) method."""
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))
    return_period_years = body.get("return_period_years")
    target_year = body.get("target_year")
    custom_surge_m = body.get("custom_surge_m")

    try:
        header, dem, dem_label = _resolve_coastal_dem(geom, allow_global)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": f"DEM crop failed: {e}"}), 400

    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lat_c = (lat_min + lat_max) / 2
    lon_c = (lon_min + lon_max) / 2

    valid_mask = dem != -9999
    coastal, reason = is_plausibly_coastal(dem, valid_mask, lat_c)
    if not coastal:
        return jsonify({"error": f"This AOI doesn't look coastal: {reason}", "coastal": False})

    wl = total_water_level_m(
        return_period_years=return_period_years if custom_surge_m is None else None,
        target_year=target_year, custom_surge_m=custom_surge_m,
        source=body.get("water_level_source", "khan"), lon=lon_c, lat=lat_c, extra_slr_m=float(body.get("extra_slr_m") or 0.0))

    wave_runup = None
    total_level_m = wl["total_water_level_m"]
    if bool(body.get("include_wave_runup", False)):
        wave_runup = _compute_wave_runup(dem, valid_mask, total_level_m, header["cellsize"], lon_c, lat_c)
        if wave_runup is not None:
            total_level_m = round(total_level_m + wave_runup["runup_m"], 3)

    sea_mask = coastal_layers.sea_mask_for_grid(header)
    result = static_coastal_inundation(dem, valid_mask, total_level_m, sea_mask=sea_mask)

    cellsize = header["cellsize"]
    area_km2 = result["flooded_cells"] * (cellsize ** 2) / 1e6
    max_depth = float(result["depth_m"].max()) if result["flooded_cells"] > 0 else 0.0

    return jsonify({
        "coastal": True,
        "dem_source": dem_label,
        "water_level": wl,
        "wave_runup": wave_runup,
        "total_level_with_runup_m": total_level_m if wave_runup else None,
        "flooded_area_km2": round(area_km2, 4),
        "max_depth_m": round(max_depth, 3),
        "flooded_cells": result["flooded_cells"],
        "sea_mask_used": sea_mask is not None,
        "mangroves": coastal_layers.mangrove_stats(header, result["depth_m"], valid_mask, cellsize),
        "method": "Static bathtub-with-connectivity inundation -- see coastal_utils.py for real reference "
                  "values and stated limitations (not a dynamic storm-surge model).",
    })


@app.route("/area/coastal-flood-mapbox3d", methods=["POST"])
def area_coastal_flood_mapbox3d():
    """Real Mapbox 3D visualization of the coastal inundation result, via
    the exact same single-frame water-overlay path Tier 2's instant-flood
    3D view already uses (build_mapbox_flood_scene)."""
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))
    try:
        header, dem, _ = _resolve_coastal_dem(geom, allow_global)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>",
                        mimetype="text/html")

    valid_mask = dem != -9999
    _pc = shapely_shape(geom).centroid
    wl = total_water_level_m(
        return_period_years=body.get("return_period_years") if body.get("custom_surge_m") is None else None,
        target_year=body.get("target_year"), custom_surge_m=body.get("custom_surge_m"),
        source=body.get("water_level_source", "khan"), lon=_pc.x, lat=_pc.y, extra_slr_m=float(body.get("extra_slr_m") or 0.0))
    result = static_coastal_inundation(dem, valid_mask, wl["total_water_level_m"], sea_mask=coastal_layers.sea_mask_for_grid(header))
    depth_grid = np.where(valid_mask, result["depth_m"], -9999.0)

    bbox = list(shapely_shape(geom).bounds)
    water = {"depth_stack": depth_grid[np.newaxis, :, :], "save_int_min": 60.0,
             "dry_thresh": 0.05, "max_depth": float(result["depth_m"].max()) if result["flooded_cells"] else 0.1}
    html_path = os.path.join(AOI3D_CACHE, f"coastal_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "Coastal flood (Mapbox)", bbox, geom, MAPBOX_TOKEN, water=water,
            legend_html=f"<b>Static coastal inundation</b> &middot; total water level "
                        f"{wl['total_water_level_m']:.2f} m &middot; {wl['basis']}")
        with open(html_path) as f:
            html = f.read()
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>"
                        f"Coastal 3D preview failed: {e}</p>", mimetype="text/html")
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


def _coastal_susceptibility_for(geom, allow_global):
    """Shared by the susceptibility JSON / 3D / PNG routes: DEM crop + the weighted index (see
    coastal_layers_utils.coastal_susceptibility for the factors, weights and their stated limits)."""
    header, dem, dem_label = _resolve_coastal_dem(geom, allow_global)
    valid = dem != -9999
    poly = shapely_shape(geom)
    coastal, reason = is_plausibly_coastal(dem, valid, poly.centroid.y)
    if not coastal:
        raise RuntimeError(f"This AOI doesn't look coastal: {reason}")
    susc = coastal_layers.coastal_susceptibility(dem, valid, header)
    return header, dem, valid, dem_label, susc


@app.route("/area/coastal-susceptibility", methods=["POST"])
def area_coastal_susceptibility():
    body = request.json
    try:
        header, dem, valid, dem_label, susc = _coastal_susceptibility_for(body["geometry"], bool(body.get("use_global_dem", False)))
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    rows = coastal_layers.susceptibility_summary(susc["classes"], valid, header["cellsize"])
    for r, c in zip(rows, coastal_layers.SUSC_COLORS):
        r["color"] = c
    return jsonify({
        "dem_source": dem_label, "classes": rows, "factors_used": susc["factors_used"], "weights_used": susc["weights_used"],
        "mangroves": coastal_layers.mangrove_stats(header, np.zeros(dem.shape), valid, header["cellsize"]),
        "sea_mask_available": coastal_layers.sea_mask_available(),
        "method": ("Weighted coastal flood susceptibility index (0-1): elevation above mean high water (MSL + half the Karachi "
                   "tidal range), distance to the sea (30 m sea mask), slope and mangrove buffer, weights from the supplied "
                   "coastal model's CVI-style defaults (expert values, not fitted). Missing factors are dropped and the weights "
                   "renormalised. Mangroves feed the index only; they do not attenuate water in the inundation calculation. "
                   "DEM (SRTM) and water-level datums are not reconciled."),
    })


@app.route("/area/coastal-susceptibility-mapbox3d", methods=["POST"])
def area_coastal_susceptibility_mapbox3d():
    body = request.json
    geom = body["geometry"]
    try:
        header, dem, valid, _, susc = _coastal_susceptibility_for(geom, bool(body.get("use_global_dem", False)))
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>", mimetype="text/html")
    idx = np.where(valid & np.isfinite(susc["index"]), susc["index"], -9999.0)
    cols = [[int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)] for c in coastal_layers.SUSC_COLORS]
    stops = []
    for i, c in enumerate(cols):
        lo = 0.0 if i == 0 else coastal_layers.SUSC_BREAKS[i - 1] + 1e-4
        hi = 1.0 if i == len(cols) - 1 else coastal_layers.SUSC_BREAKS[i]
        stops += [[lo, c], [hi, c]]
    water = {"depth_stack": idx[np.newaxis, :, :], "save_int_min": 60.0, "dry_thresh": 0.0, "max_depth": 1.0, "color_stops": stops}
    legend = "<b>Coastal flood susceptibility</b><br>" + " ".join(
        f"<span style=\"color:{c}\">&#9632;</span> {n}" for n, c in zip(coastal_layers.SUSC_CLASS_NAMES, coastal_layers.SUSC_COLORS))
    html_path = os.path.join(AOI3D_CACHE, f"coastal_susc_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(html_path, "Coastal susceptibility", list(shapely_shape(geom).bounds), geom, MAPBOX_TOKEN,
                                 water=water, legend_html=legend)
        with open(html_path) as f:
            html = f.read()
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>", mimetype="text/html")
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


@app.route("/area/coastal-susceptibility-map.png", methods=["POST"])
def area_coastal_susceptibility_map_png():
    body = request.json
    geom = body["geometry"]
    try:
        header, dem, valid, dem_label, susc = _coastal_susceptibility_for(geom, bool(body.get("use_global_dem", False)))
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    cls = np.where(susc["classes"] > 0, susc["classes"].astype(int) - 1, -2)
    cl = coastal_layers
    rng = ["< 0.2", "0.2 - 0.4", "0.4 - 0.6", "0.6 - 0.8", "> 0.8"]
    mg = cl.mangrove_stats(header, np.zeros(dem.shape), valid, header["cellsize"])
    kf = [("Factors in the index", f"{len(susc['factors_used'])} of 4")]
    if mg:
        kf.append(("Mangrove area in AOI", f"{mg['mangrove_area_km2']:.2f} km\u00b2"))
    notes = ("Notes: screening index (0-1), not a flood simulation. Weights are expert defaults, not fitted; elevation is measured "
             "above mean high water approximated as MSL + half the Karachi tidal range; the SRTM and sea-level datums are not "
             "reconciled. Mangroves enter the index only. Source layers: SRTM, 30 m sea mask, Global Mangrove Watch 2025.")
    out = os.path.join(AOI3D_CACHE, f"coastal_susc_{uuid.uuid4().hex}.png")
    try:
        susceptibility_map_utils.render_class_map(
            out, "Coastal flood susceptibility map",
            f"Weighted index of elevation, distance to sea, slope and mangrove buffer \u2022 {header['cellsize']:.0f} m grid",
            header, geom, dem, valid, cls, cl.SUSC_CLASS_NAMES, rng, cl.SUSC_COLORS, legend_title="COASTAL SUSCEPTIBILITY",
            legend_sub="class by weighted index (0-1)", unclassed_label="", key_figures=kf, notes=notes, show_roads=True, show_dams=False,
            sub2="Factors: " + ", ".join(susc["factors_used"]).replace("_", " ") + ". Higher classes: lower, flatter ground close to the sea with little mangrove buffer.")
        with open(out, "rb") as f:
            png = f.read()
    except Exception as e:
        return jsonify({"error": f"map render failed: {e}"}), 500
    finally:
        if os.path.exists(out):
            os.remove(out)
    return Response(png, mimetype="image/png", headers={"Content-Disposition": "inline; filename=coastal_susceptibility.png"})


_COASTAL_LIBRARY_CACHE = {}


@app.route("/area/coastal-level-library", methods=["POST"])
def area_coastal_level_library():
    """Area / depth / volume of connected-bathtub flooding at 0.25 m still-water steps, for the instant water-level
    slider, plus the published return levels of BOTH sources at this AOI (Khan Karachi gauge; nearest GTSM station)."""
    body = request.json
    geom = body["geometry"]
    key = hashlib.sha1((json.dumps(geom, sort_keys=True) + "|lib").encode()).hexdigest()
    try:
        header, dem, dem_label = _resolve_coastal_dem(geom, bool(body.get("use_global_dem", False)))
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": f"DEM crop failed: {e}"}), 400
    valid = dem != -9999
    poly = shapely_shape(geom)
    ok, reason = is_plausibly_coastal(dem, valid, poly.centroid.y)
    if not ok:
        return jsonify({"error": f"This AOI doesn't look coastal: {reason}"}), 400
    if key not in _COASTAL_LIBRARY_CACHE:
        sm = coastal_layers.sea_mask_for_grid(header)
        _COASTAL_LIBRARY_CACHE[key] = coastal_layers.level_library(dem, valid, np.arange(0.0, 8.01, 0.25), sm, header["cellsize"])
    refs = {"khan": {str(t): total_water_level_m(t)["total_water_level_m"] for t in (50, 100, 1000)}}
    if coastal_layers.gtsm_available():
        st = coastal_layers.nearest_gtsm_station(poly.centroid.x, poly.centroid.y)
        refs["gtsm"] = {"station": {k: st[k] for k in ("station_index", "lon", "lat", "distance_km")},
                        "levels": {str(t): round(v, 3) for t, v in st["levels"].items()}}
    return jsonify({"dem_source": dem_label, "library": _COASTAL_LIBRARY_CACHE[key], "reference_levels": refs,
                    "datum_note": coastal_layers.GTSM_DATUM_NOTE})


@app.route("/runs/<run_id>/susceptibility-map.png")
def run_susceptibility_map_png(run_id):
    run_dir = os.path.join(FIS_CACHE, run_id)
    summ = os.path.join(run_dir, "summary.json")
    if not os.path.exists(summ) or not os.path.exists(os.path.join(run_dir, "results", "res.max")):
        return jsonify({"error": "run not found or not finished"}), 404
    out = os.path.join(run_dir, "susceptibility_map.png")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(summ):
        try:
            susceptibility_map_utils.render_run_susceptibility_map(run_dir, out)
        except Exception as e:
            return jsonify({"error": f"map render failed: {e}"}), 500
    return send_file(out, mimetype="image/png", download_name=f"flood_susceptibility_{run_id}.png",
                     as_attachment=request.args.get("download") == "1")


def _coastal_instant_preview(geom, allow_global, anchor_mode="static", return_period_years=None,
                              target_year=None, custom_surge_m=None, window_days=5, n_frames=12,
                              water_level_source="khan", extra_slr_m=0.0, tide_phase=None):
    """Real, fast ANIMATED coastal preview -- the coastal equivalent of
    Tier 2's HAND-based instant flood preview for pluvial/fluvial/GLOF:
    something fast to look at before committing to the real multi-minute
    dynamic HVAR physics run (run_coastal_dynamic_flood), sitting between
    that and the single-snapshot static bathtub method.

    Builds the exact same real GTSR-episode-shaped, magnitude-anchored
    water-level time series the real dynamic run uses
    (coastal_utils.build_dynamic_water_level_series) -- but instead of
    running LISFLOOD-FP at each moment, applies the EXISTING fast static
    bathtub-with-connectivity method (coastal_utils.static_coastal_
    inundation) independently at each of `n_frames` real, evenly-spaced,
    linearly-interpolated points along that real series. Real, stated
    approximation: each frame is computed as if the water level had
    already reached full equilibrium at that instant -- there is no real
    flow routing, momentum, or inflow lag carried between frames the way
    the actual dynamic physics run has, which is exactly why this can run
    in a fraction of a second instead of real minutes. Wave run-up and
    the tide+surge combination (both real options on the dynamic run) are
    deliberately not included here, to keep this a small, fast, additive
    preview rather than a second full feature surface to keep in sync."""
    header, dem, dem_label = _resolve_coastal_dem(geom, allow_global)
    nodata = header.get("nodata_value", -9999.0)
    valid_mask = dem != nodata

    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lon_c, lat_c = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2

    coastal, reason = is_plausibly_coastal(dem, valid_mask, lat_c)
    if not coastal:
        raise RuntimeError(f"This AOI doesn't look coastal: {reason}")
    if not gtsr_available():
        raise RuntimeError("Real GTSR data is not available on this server -- use the static coastal method instead.")

    station = nearest_gtsr_station(lon_c, lat_c)
    if station is None:
        raise RuntimeError("GTSR station lookup failed even though gtsr_available() was true.")

    wl = total_water_level_m(
        return_period_years=return_period_years if custom_surge_m is None else None,
        target_year=target_year, custom_surge_m=custom_surge_m,
        source=water_level_source, lon=lon_c, lat=lat_c, extra_slr_m=extra_slr_m)

    if anchor_mode == "gtsr":
        win = real_surge_window(station["index"], window_days=window_days)
        if win is None or win["peak_surge_m"] <= 0:
            raise RuntimeError(f"Station '{station['name']}' has no usable real GTSR surge record to anchor to.")
        anchor_peak_m = win["peak_surge_m"]
        anchor_basis = (f"real recorded GTSR historical peak surge at '{station['name']}' "
                         f"({station['distance_km']} km away)")
    else:
        anchor_peak_m = wl["total_water_level_m"]
        anchor_basis = f"static method's own published total water level ({wl['basis']})"

    tide_info = None
    if tide_phase:
        import tide_utils
        if not tide_utils.available():
            raise RuntimeError("the Karachi tide-gauge data is not available on this server, so the astronomical tide cannot be added")
        kw = ({"surge_peak_m": anchor_peak_m} if anchor_mode == "gtsr" else {"total_peak_target_m": anchor_peak_m})
        series = build_tide_surge_series(station["index"], window_days=window_days, phase=tide_phase, **kw)
        n_frames = max(n_frames, 36)       # the tide oscillates every ~12.4 h, so 12 frames over days would alias it
        if series is not None:
            tide_info = {"phase": tide_phase, "tide_at_surge_peak_m": series["tide_at_surge_peak_m"],
                         "max_total_level_m": round(max(series["levels_m"]), 3)}
            anchor_basis += (f"; astronomical tide added ({tide_utils.TIDE_PHASES[tide_phase]}), highest total level "
                              f"{tide_info['max_total_level_m']:.2f} m above MSL")
    else:
        series = build_dynamic_water_level_series(station["index"], anchor_peak_water_level_m=anchor_peak_m,
                                                   window_days=window_days)
    if series is None:
        raise RuntimeError(f"Station '{station['name']}' has no usable real GTSR window for a time series.")

    times_hr = np.array(series["times_hr"])
    levels_m = np.array(series["levels_m"])
    t_interp = np.linspace(times_hr[0], times_hr[-1], n_frames)
    # Always show the highest level of the event: with an oscillating tide the evenly spaced frames can straddle it.
    t_interp[int(np.argmin(np.abs(t_interp - times_hr[int(np.argmax(levels_m))])))] = times_hr[int(np.argmax(levels_m))]
    levels_interp = np.interp(t_interp, times_hr, levels_m)

    cellsize = header["cellsize"]
    depth_stack = np.zeros((n_frames, dem.shape[0], dem.shape[1]), dtype="float32")
    frame_stats = []
    for i, level in enumerate(levels_interp):
        result = static_coastal_inundation(dem, valid_mask, float(level), nodata=nodata)
        depth_stack[i] = np.where(valid_mask, result["depth_m"], -9999.0)
        area_km2 = result["flooded_cells"] * (cellsize ** 2) / 1e6
        frame_stats.append({
            "time_hr": round(float(t_interp[i]), 2), "water_level_m": round(float(level), 3),
            "flooded_area_km2": round(area_km2, 4),
            "max_depth_m": round(float(result["depth_m"].max()), 3) if result["flooded_cells"] else 0.0,
        })

    return {
        "header": header, "dem_label": dem_label, "station": station,
        "anchor_peak_water_level_m": round(anchor_peak_m, 3), "anchor_basis": anchor_basis,
        "depth_stack": depth_stack, "times_hr_interp": t_interp.tolist(), "frame_stats": frame_stats,
        "astronomical_tide": tide_info,
    }


@app.route("/area/coastal-flood-instant-preview", methods=["POST"])
def area_coastal_flood_instant_preview():
    """Real stats-only response for the fast animated coastal preview
    (see _coastal_instant_preview) -- the 3D animation itself is served
    by the sibling -mapbox3d route below."""
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))
    try:
        preview = _coastal_instant_preview(
            geom, allow_global, anchor_mode=body.get("anchor_mode", "static"),
            return_period_years=body.get("return_period_years"), target_year=body.get("target_year"),
            custom_surge_m=body.get("custom_surge_m"), window_days=int(body.get("window_days", 5)),
            water_level_source=body.get("water_level_source", "khan"), extra_slr_m=float(body.get("extra_slr_m") or 0.0),
            tide_phase=(body.get("tide_phase") or None))
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "dem_source": preview["dem_label"], "station": preview["station"],
        "anchor_peak_water_level_m": preview["anchor_peak_water_level_m"], "anchor_basis": preview["anchor_basis"],
        "frames": preview["frame_stats"], "astronomical_tide": preview.get("astronomical_tide"),
        "method": "Fast animated preview: a real GTSR episode-shaped, magnitude-anchored water-level time series, "
                  "with each frame computed independently via the static bathtub-with-connectivity method (no "
                  "real flow routing/momentum carried between frames) -- an approximation to look at before "
                  "committing to the real multi-minute dynamic HVAR physics run, not a replacement for it.",
    })


@app.route("/area/coastal-flood-instant-preview-mapbox3d", methods=["POST"])
def area_coastal_flood_instant_preview_mapbox3d():
    """Real animated 3D view of the fast coastal preview, via the exact
    same per-timestep water-overlay path Tier 3's real animated flood
    scene uses (build_mapbox_flood_scene) -- just fed this preview's own
    real (approximate) per-frame depth stack instead of a genuine
    LISFLOOD-FP per-timestep output."""
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))
    try:
        preview = _coastal_instant_preview(
            geom, allow_global, anchor_mode=body.get("anchor_mode", "static"),
            return_period_years=body.get("return_period_years"), target_year=body.get("target_year"),
            custom_surge_m=body.get("custom_surge_m"), window_days=int(body.get("window_days", 5)),
            water_level_source=body.get("water_level_source", "khan"), extra_slr_m=float(body.get("extra_slr_m") or 0.0),
            tide_phase=(body.get("tide_phase") or None))
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>",
                        mimetype="text/html")

    n_frames = len(preview["frame_stats"])
    t = preview["times_hr_interp"]
    save_int_min = (t[1] - t[0]) * 60.0 if n_frames > 1 else 60.0
    max_depth = max((f["max_depth_m"] for f in preview["frame_stats"]), default=0.0) or 0.1
    bbox = list(shapely_shape(geom).bounds)
    water = {"depth_stack": preview["depth_stack"], "save_int_min": save_int_min,
             "dry_thresh": 0.05, "max_depth": max_depth}
    legend = (f"<b>Fast coastal preview (approximate)</b><br>"
              f"Station: {preview['station']['name']} ({preview['station']['distance_km']} km) &middot; "
              f"anchor {preview['anchor_peak_water_level_m']:.2f} m<br>"
              f"Each frame: static bathtub method, no real flow routing between frames -- "
              f"see the real dynamic surge run for genuine physics.")
    html_path = os.path.join(AOI3D_CACHE, f"coastalpreview_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(html_path, "Coastal instant preview (Mapbox)", bbox, geom, MAPBOX_TOKEN,
                                  water=water, legend_html=legend)
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


def _start_climate_job(fn, **kwargs):
    job_id = uuid.uuid4().hex
    with CLIMATE_JOBS_LOCK:
        CLIMATE_JOBS[job_id] = {"status": "running"}

    def _worker():
        try:
            result = fn(**kwargs)
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "done", "result": result}
        except Exception as e:
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed", "error": str(e)}

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


@app.route("/area/design-storm", methods=["POST"])
def area_design_storm():
    """Real return-period design storm (climate_utils.py) -- a real Gumbel
    fit to real CMIP6-historical (BCSD bias-corrected) annual-max daily
    rainfall at the AOI's centroid. Async: reads dozens of real netCDF
    files uncached (~1-3 real minutes first time), instant on a cache hit."""
    body = request.json
    geom = body["geometry"]
    return_period_years = int(body.get("return_period_years", 100))
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2
    job_id = _start_climate_job(climate_utils.design_storm_for_return_period,
                                 lat=lat_c, lon=lon_c, return_period_years=return_period_years)
    return jsonify({"job_id": job_id})


@app.route("/area/climate-factor", methods=["POST"])
def area_climate_factor():
    """Real climate-change rainfall-intensification factor (climate_utils.py)
    -- real delta-change method on real local CMIP6 historical+SSP data.
    Async for the same reason as /area/design-storm, roughly 2x the cost
    (reads both a historical and a future period per model)."""
    body = request.json
    geom = body["geometry"]
    return_period_years = int(body.get("return_period_years", 100))
    scenario = body.get("scenario", "ssp585")
    future_period = body.get("future_period", "2050 (mid-century)")
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2
    job_id = _start_climate_job(climate_utils.climate_intensification_factor,
                                 lat=lat_c, lon=lon_c, return_period_years=return_period_years,
                                 scenario=scenario, future_period=future_period)
    return jsonify({"job_id": job_id})


JOB_CANCEL = set()      # ids of background jobs (scenarios, optimizers, ...) the user asked to cancel


class JobCancelled(Exception):
    pass


def _kill_job_processes(job_id):
    """SIGTERM every process of this user whose command line mentions the job id (its flood_model.py and solver
    both carry the job's work directory). Returns how many were signalled."""
    import signal
    n = 0
    me = os.getpid()
    for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
        if int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            if job_id in cmd and ("flood_model" in cmd or "lisflood" in cmd):
                os.kill(int(pid), signal.SIGTERM)
                n += 1
        except Exception:
            continue
    return n


@app.route("/climate-jobs/<job_id>/progress.json")
def climate_job_progress(job_id):
    if job_id in JOB_CANCEL:
        return jsonify({"status": "cancelled"})
    with CLIMATE_JOBS_LOCK:
        job = CLIMATE_JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/jobs/<job_id>/cancel", methods=["POST"])
def job_cancel(job_id):
    """Cancels a background job (protection / nature-based scenario, optimizer, calibration, coastal surge ...):
    stops its solver processes now and makes the job stop starting new ones."""
    with CLIMATE_JOBS_LOCK:
        job = CLIMATE_JOBS.get(job_id)
    if job is None:
        abort(404)
    if job.get("status") != "running":
        return jsonify({"status": job.get("status"), "note": "already finished"})
    JOB_CANCEL.add(job_id)
    return jsonify({"status": "cancelling", "processes_stopped": _kill_job_processes(job_id)})


@app.route("/area/idf-design-storm", methods=["POST"])
def area_idf_design_storm():
    """Real, near-instant (direct NetCDF slice read, no live multi-file
    fetch) design-storm intensity for a specific duration, from this
    AOI's own nearest real grid cell in the local ERA5-Land/IMERG IDF
    dataset (idf_utils.py) -- a real, location-specific alternative to
    the CMIP6-Gumbel-fit design storm above, giving the real recorded
    maximum intensity rather than a statistical extrapolation."""
    body = request.json
    geom = body["geometry"]
    duration_hr = float(body.get("duration_hr", 2.0))
    season = body.get("season", "monsoon")
    year = body.get("year")
    if not idf_utils.idf_data_available():
        return jsonify({"error": "the real local IDF dataset is not available on this server"}), 400
    poly = shapely_shape(geom)
    lon_c, lat_c = poly.centroid.x, poly.centroid.y
    result = idf_utils.real_design_intensity_for_duration(lat_c, lon_c, duration_hr, year=year, season=season)
    if result is None:
        return jsonify({"error": "no real IDF data available for this location/season/year"}), 400
    return jsonify(result)


@app.route("/area/idf-curve", methods=["POST"])
def area_idf_curve():
    """The full real recorded IDF curve (every real duration point, not
    just one) for this AOI's own nearest grid cell -- see idf_utils.py."""
    body = request.json
    geom = body["geometry"]
    season = body.get("season", "monsoon")
    year = body.get("year")
    if not idf_utils.idf_data_available():
        return jsonify({"error": "the real local IDF dataset is not available on this server"}), 400
    poly = shapely_shape(geom)
    lon_c, lat_c = poly.centroid.x, poly.centroid.y
    curve = idf_utils.real_idf_curve_for_point(lat_c, lon_c, year=year, season=season)
    if curve is None:
        return jsonify({"error": "no real IDF data available for this location/season/year"}), 400
    return jsonify(curve)


@app.route("/tehsils/search")
def tehsils_search():
    """Real tehsil-name search (578 real Pakistan tehsils) -- lets a user
    set the AOI to a real named administrative area directly, instead of
    only manually drawing one. See idf_utils.py's own tehsil section."""
    q = request.args.get("q", "")
    if not idf_utils.tehsils_available():
        return jsonify({"error": "the real local tehsil boundary dataset is not available on this server",
                        "results": []}), 400
    return jsonify({"results": idf_utils.search_tehsils(q)})


@app.route("/tehsils/<int:tehsil_id>")
def tehsil_get(tehsil_id):
    """The real polygon geometry for one tehsil, to set as the AOI."""
    if not idf_utils.tehsils_available():
        return jsonify({"error": "the real local tehsil boundary dataset is not available on this server"}), 400
    result = idf_utils.tehsil_geometry(tehsil_id)
    if result is None:
        return jsonify({"error": f"tehsil id {tehsil_id} not found"}), 404
    return jsonify(result)


@app.route("/rainfall-hotspots")
def rainfall_hotspots():
    """Real national discovery view: the N highest individual real grid
    cells for a chosen extreme-rainfall metric, each labeled with its
    real tehsil/district/province (idf_utils.top_cells) -- turns "you
    must already know where to look" into "show me where the real risk
    actually is", using the same real dataset the IDF design-storm/
    pluvial-reference features above already read."""
    if not idf_utils.idf_data_available():
        return jsonify({"error": "the real local IDF dataset is not available on this server", "results": []}), 400
    metric = request.args.get("metric", "rx1day")
    season = request.args.get("season", "monsoon")
    year = request.args.get("year")
    year = int(year) if year else None
    n = min(max(int(request.args.get("n", 15)), 1), 50)
    results = idf_utils.top_cells(metric, year=year, season=season, n=n)
    return jsonify({"results": results, "metric": metric, "season": season,
                    "metrics": idf_utils.metric_catalogue()})


@app.route("/basins/search")
def basins_search():
    """Real river-basin discovery: the real HydroSHEDS Level 08 dataset
    (basin_utils.py) has no human-readable name at all (unlike the tehsil
    dataset) -- so this mirrors the real workflow already used by hand to
    find a real basin for this app's own steep-terrain investigation:
    filter to a named region (or an explicit bbox) and rank by each
    basin's own real drainage area (HydroSHEDS' SUB_AREA), biggest first."""
    if not basin_utils.basins_data_available():
        return jsonify({"error": "the real local HydroSHEDS basin dataset is not available on this server",
                        "results": []}), 400
    region = request.args.get("region")
    bbox_arg = request.args.get("bbox")
    bbox = None
    if bbox_arg:
        try:
            bbox = tuple(float(x) for x in bbox_arg.split(","))
            if len(bbox) != 4:
                raise ValueError
        except ValueError:
            return jsonify({"error": "bbox must be 'lon_min,lat_min,lon_max,lat_max'"}), 400
    n = min(max(int(request.args.get("n", 15)), 1), 50)
    min_area_km2 = float(request.args.get("min_area_km2", 0.0))
    results = basin_utils.basins_in_region(region=region, bbox=bbox, min_area_km2=min_area_km2, limit=n)
    return jsonify({"results": results, "region": region, "bbox": list(bbox) if bbox else None,
                    "regions_available": sorted(basin_utils.REGION_BBOXES.keys())})


@app.route("/basins/nearest")
def basins_nearest():
    """Real nearest-basin-by-centroid-distance lookup -- e.g. "which real
    basin is closest to this spot on the map", complementing the region+
    area browse above for a user who already knows roughly where they
    want to look rather than which named region it falls in."""
    if not basin_utils.basins_data_available():
        return jsonify({"error": "the real local HydroSHEDS basin dataset is not available on this server",
                        "results": []}), 400
    try:
        lon = float(request.args.get("lon"))
        lat = float(request.args.get("lat"))
    except (TypeError, ValueError):
        return jsonify({"error": "real lon/lat query params are required"}), 400
    n = min(max(int(request.args.get("n", 5)), 1), 20)
    results = basin_utils.nearest_basin(lon, lat, max_results=n)
    return jsonify({"results": results})


@app.route("/basins/<int:hybas_id>")
def basin_by_id(hybas_id):
    """Real single basin's full polygon geometry, for setting it directly
    as the AOI -- the same real GeoJSON geometry dict every other AOI-
    setting path in this app already accepts."""
    geom = basin_utils.basin_geometry(hybas_id)
    if geom is None:
        return jsonify({"error": f"no real basin found with HYBAS_ID {hybas_id}"}), 404
    return jsonify({"hybas_id": hybas_id, "geometry": geom})


@app.route("/glacier/baseline", methods=["POST"])
def glacier_baseline():
    """Real Part A of the Rasuwa glacier-monitoring methodology (see
    glacier_monitoring_utils.py), applied to a Pakistan AOI -- a real
    GLIMS best-date-matched baseline glacier inventory. Fast enough
    (~6-10s) to run synchronously, unlike the SAR displacement piece."""
    body = request.json
    geom = body["geometry"]
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    if not glacier_monitoring_utils.is_in_pakistan(lon_min, lat_min, lon_max, lat_max):
        return jsonify({"error": "This tool is scoped to Pakistan only -- this AOI is outside Pakistan's bounds."}), 400
    result = glacier_monitoring_utils.glims_baseline_for_aoi(lon_min, lat_min, lon_max, lat_max)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/glacier/displacement", methods=["POST"])
def glacier_displacement():
    """Real Parts B-D (SAR offset-tracking + cumulative displacement),
    async since a real multi-month run takes real minutes (see
    glacier_monitoring_utils.py's own docstring) -- now real ascending+
    descending-combined and stable-ground-bias-corrected, roughly double
    the original single-orbit runtime.

    `lake_key`: optional, one of glof_utils.KNOWN_GB_LAKES (e.g.
    "shishper_2022") -- when given, that lake's own real coordinates are
    marked on the result thumbnails, purely a visual aid connecting this
    monitoring context back to a specific real GLOF site."""
    body = request.json
    geom = body["geometry"]
    start_date = body.get("start_date", "2026-01-01")
    end_date = body.get("end_date", "2026-08-31")
    confidence_threshold = float(body.get("confidence_threshold", glacier_monitoring_utils.SAR_CONFIDENCE_THRESHOLD))
    lake_lat, lake_lon = None, None
    lake_key = body.get("lake_key")
    if lake_key and lake_key in glof_utils.KNOWN_GB_LAKES:
        lake = glof_utils.KNOWN_GB_LAKES[lake_key]
        lake_lat, lake_lon = lake["lake_lat"], lake["lake_lon"]
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    if not glacier_monitoring_utils.is_in_pakistan(lon_min, lat_min, lon_max, lat_max):
        return jsonify({"error": "This tool is scoped to Pakistan only -- this AOI is outside Pakistan's bounds."}), 400
    job_id = _start_climate_job(glacier_monitoring_utils.sar_cumulative_displacement,
                                 lon_min=lon_min, lat_min=lat_min, lon_max=lon_max, lat_max=lat_max,
                                 start_date=start_date, end_date=end_date,
                                 confidence_threshold=confidence_threshold, lake_lat=lake_lat, lake_lon=lake_lon)
    return jsonify({"job_id": job_id})


@app.route("/glacier/itslive-velocity", methods=["POST"])
def glacier_itslive_velocity():
    """Recent vs same-season glacier surface speed from NASA ITS_LIVE (no Earth Engine, no login). Async: downloads
    a handful of ~2-30 MB granules on first use (cached afterwards). See itslive_utils.py for method and limits."""
    body = request.json
    geom = body["geometry"]
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    if not glacier_monitoring_utils.is_in_pakistan(lon_min, lat_min, lon_max, lat_max):
        return jsonify({"error": "This tool is scoped to Pakistan only -- this AOI is outside Pakistan's bounds."}), 400
    job_id = _start_climate_job(itslive_utils.analyse_aoi, geometry=geom,
                                 recent_days=int(body.get("recent_days", 45)), baseline_years=int(body.get("baseline_years", 2)),
                                 max_per_window=int(body.get("max_per_window", 6)))
    return jsonify({"job_id": job_id})


@app.route("/itslive/thumb/<name>")
def itslive_thumb(name):
    if not name.endswith(".png") or "/" in name or ".." in name:
        abort(404)
    path = os.path.join(itslive_utils.THUMB_DIR, name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="image/png")


@app.route("/area/hydrolakes-nearby", methods=["POST"])
def area_hydrolakes_nearby():
    """Real nearby HydroLAKES lakes (Messager et al. 2016) for the drawn
    AOI's own centroid -- broadens GLOF scenarios beyond the small
    curated KNOWN_GB_LAKES list to any real lake in Pakistan with a real
    reported volume. See glof_utils.find_hydrolakes_near's own docstring
    for why this is additive to, not a replacement for, the curated list
    (HydroLAKES' static snapshot predates newly-formed lakes like
    Shishper)."""
    body = request.json
    geom = body.get("geometry")
    if not geom:
        return jsonify({"error": "no area selected"}), 400
    poly = shapely_shape(geom)
    lon_c, lat_c = poly.centroid.x, poly.centroid.y
    radius_km = float(body.get("radius_km", 100.0))
    lakes = glof_utils.find_hydrolakes_near(lon_c, lat_c, radius_km=radius_km)
    return jsonify({"lakes": lakes, "search_center": [lon_c, lat_c], "radius_km": radius_km})


@app.route("/area/pmd-lakes-nearby", methods=["POST"])
def area_pmd_lakes_nearby():
    """PMD 2024 glacial-lake inventory lakes near the AOI centroid (or a given lon/lat) with dam type, glacier
    relation and nearest village -- see glof_utils.find_pmd_lakes_near for the volume basis and its limits."""
    body = request.json
    if body.get("lon") is not None and body.get("lat") is not None:
        lon_c, lat_c = float(body["lon"]), float(body["lat"])
    elif body.get("geometry"):
        c = shapely_shape(body["geometry"]).centroid
        lon_c, lat_c = c.x, c.y
    else:
        return jsonify({"error": "no area selected"}), 400
    radius_km = float(body.get("radius_km", 40.0))
    lakes = glof_utils.find_pmd_lakes_near(lon_c, lat_c, radius_km=radius_km, max_results=int(body.get("max_results", 12)))
    return jsonify({"lakes": lakes, "search_center": [lon_c, lat_c], "radius_km": radius_km})


@app.route("/glof/historical-events", methods=["POST"])
def glof_historical_events():
    """Documented outburst events (HMAGLOFDB) near the AOI centroid / a lon-lat, plus the Popov-vs-observed check."""
    if not glof_events_utils.events_available():
        return jsonify({"error": "the GLOF event catalogue is not installed on this server"}), 400
    body = request.json
    if body.get("lon") is not None and body.get("lat") is not None:
        lon_c, lat_c = float(body["lon"]), float(body["lat"])
    elif body.get("geometry"):
        c = shapely_shape(body["geometry"]).centroid
        lon_c, lat_c = c.x, c.y
    else:
        return jsonify({"error": "no area selected"}), 400
    radius_km = float(body.get("radius_km", 100.0))
    events = glof_events_utils.events_near(lon_c, lat_c, radius_km=radius_km, max_results=int(body.get("max_results", 25)))
    return jsonify({"events": events, "search_center": [lon_c, lat_c], "radius_km": radius_km,
                    "catalogue": glof_events_utils.catalogue_summary(),
                    "source": "HMAGLOFDB (High Mountain Asia GLOF database) -- a compilation, not a complete census."})


@app.route("/glof/popov-validation")
def glof_popov_validation():
    v = glof_events_utils.popov_validation()
    if v is None:
        return jsonify({"error": "the GLOF event catalogue is not installed on this server"}), 400
    return jsonify(v)


@app.route("/glof/lakes")
def glof_lakes():
    """Real, curated Gilgit-Baltistan glacial lakes with documented GLOF
    event data -- see glof_utils.py for why this is a short curated list
    rather than an automated ICIMOD inventory lookup (that inventory's
    2005 imagery predates Shishper Lake's own 2018 formation)."""
    return jsonify({k: v for k, v in glof_utils.KNOWN_GB_LAKES.items()})


@app.route("/glof/discharge", methods=["POST"])
def glof_discharge():
    """Real GLOF peak-discharge computation -- see glof_utils.py for the
    cited formulas (Popov 1991 for volume->discharge, Huggel et al. 2002
    for the optional area->volume fallback) and their real, stated
    uncertainty. A pure, fast calculation -- no async job needed."""
    body = request.json
    lake_key = body.get("lake_key")
    hydrolakes_id = body.get("hydrolakes_id")
    volume_m3 = body.get("volume_m3")
    area_m2 = body.get("area_m2")
    # "moraine_breach" (default, Popov 1991 -- volume only) or
    # "landslide_breach" (Froehlich 1995 -- needs a real breach/dam height
    # too; the right real choice when a landslide/rock-ice avalanche
    # impacting the lake is what actually triggers or accelerates the
    # breach). See glof_utils.froehlich_breach_discharge_m3s's own
    # docstring for the real, stated scope (breach discharge only, not
    # the landslide's own impulse-wave height).
    trigger = body.get("trigger", "moraine_breach")
    breach_height_m = body.get("breach_height_m")
    if trigger == "landslide_breach" and not breach_height_m:
        return jsonify({"error": "landslide/breach trigger needs a real breach (dam) height in metres"}), 400

    def _band_for(peak_m3s, lake_type=None):
        """Empirical spread of the Popov estimate from documented events (only meaningful for the Popov branch)."""
        if trigger == "landslide_breach":
            return None
        try:
            return glof_events_utils.empirical_band(peak_m3s, lake_type)
        except Exception:
            return None

    def _discharge_from_volume(v_m3):
        if trigger == "landslide_breach":
            return (glof_utils.froehlich_breach_discharge_m3s(v_m3, float(breach_height_m)),
                    f"Froehlich (1995) landslide/breach discharge regression: "
                    f"Qmax = 0.607 * V^0.295 * h^1.24 (h = {float(breach_height_m):.1f} m real breach height) "
                    f"-- estimates peak BREACH discharge only, not the landslide's own impulse-wave height.")
        return (glof_utils.popov_peak_discharge_m3s(v_m3),
                "Popov (1991) empirical moraine-dam-breach regression: Qmax = 0.0048 * V^0.896")

    pmd_lake_id = body.get("pmd_lake_id")
    if pmd_lake_id is not None:
        lake = glof_utils.pmd_lake_by_id(pmd_lake_id)
        if not lake:
            return jsonify({"error": f"PMD lake id {pmd_lake_id} not found"}), 400
        peak_m3s, method = _discharge_from_volume(lake["volume_m3"])
        return jsonify({
            "peak_discharge_m3s": round(peak_m3s, 1), "volume_m3": lake["volume_m3"],
            "method": f"PMD 2024 lake inventory polygon ({lake['area_m2']:,.0f} m2) -> volume by {lake['volume_basis']} -> {method}",
            "note": f"PMD lake #{pmd_lake_id}: dam type {lake['dam_type']}, glacier relation {lake['glacier_relation']}, "
                    f"lake kind {lake['lake_kind']}" + (f", nearest village {lake['nearest_village']}" if lake["nearest_village"] else "") +
                    ". Volume is area-derived (Cook & Quincey 2015 found order-of-magnitude differences against surveyed volumes); "
                    "not validated against a documented event for this lake.",
            "lake_lat": lake["lake_lat"], "lake_lon": lake["lake_lon"], "lake_info": lake,
            "downstream_lat": None, "downstream_lon": None,
            "empirical_band": _band_for(peak_m3s, lake["dam_type"]),
            "suggested_duration_hr": glof_utils.GLOF_DEFAULT_DURATION_HR,
            "suggested_rise_frac": glof_utils.GLOF_RISE_FRAC,
        })

    if hydrolakes_id is not None:
        lake = glof_utils.hydrolakes_lake_by_id(hydrolakes_id)
        if not lake:
            return jsonify({"error": f"HydroLAKES lake id {hydrolakes_id} not found or has no usable "
                                      f"real reported volume"}), 400
        peak_m3s, method = _discharge_from_volume(lake["volume_m3"])
        return jsonify({
            "peak_discharge_m3s": round(peak_m3s, 1), "volume_m3": lake["volume_m3"],
            "method": f"Real HydroLAKES-reported lake volume (Messager et al. 2016) -> {method}",
            "note": f"HydroLAKES lake #{hydrolakes_id}" + (f" ({lake['name']})" if lake["name"] else "") +
                    " -- a real lake, not individually validated against a documented GLOF event the way "
                    "the curated Shishper preset is.",
            "lake_lat": lake["lake_lat"], "lake_lon": lake["lake_lon"],
            "downstream_lat": None, "downstream_lon": None,
            "empirical_band": _band_for(peak_m3s, body.get("lake_type")),
            "suggested_duration_hr": glof_utils.GLOF_DEFAULT_DURATION_HR,
            "suggested_rise_frac": glof_utils.GLOF_RISE_FRAC,
        })

    if lake_key:
        lake = glof_utils.KNOWN_GB_LAKES.get(lake_key)
        if not lake:
            return jsonify({"error": f"unknown lake '{lake_key}'"}), 400
        peak_m3s = lake["documented_peak_discharge_m3s"]
        volume_used = lake["documented_volume_m3"]
        method = f"Real documented event data for {lake['name']} -- not a formula estimate."
        return jsonify({
            "peak_discharge_m3s": round(peak_m3s, 1), "volume_m3": volume_used, "method": method,
            "note": lake["note"], "lake_lat": lake["lake_lat"], "lake_lon": lake["lake_lon"],
            "downstream_lat": lake["downstream_lat"], "downstream_lon": lake["downstream_lon"],
            "suggested_duration_hr": glof_utils.GLOF_DEFAULT_DURATION_HR,
            "suggested_rise_frac": glof_utils.GLOF_RISE_FRAC,
        })

    uncertainty_note = None
    if volume_m3 is None and area_m2 is not None:
        volume_m3 = glof_utils.huggel_volume_from_area_m2(float(area_m2))
        uncertainty_note = ("Volume estimated from area via Huggel et al. (2002) -- a REAL published "
                             "relationship, but genuinely uncertain: Cook & Quincey (2015) found "
                             "order-of-magnitude differences between this formula's estimate and real "
                             "surveyed lake volume for lakes of similar area. Use a real volume estimate "
                             "instead whenever one is available.")
    if volume_m3 is None:
        return jsonify({"error": "provide either lake_key, volume_m3, or area_m2"}), 400

    volume_m3 = float(volume_m3)
    peak_m3s, method = _discharge_from_volume(volume_m3)
    return jsonify({
        "peak_discharge_m3s": round(peak_m3s, 1), "volume_m3": round(volume_m3, 0),
        "method": method,
        "note": uncertainty_note,
        "empirical_band": _band_for(peak_m3s, body.get("lake_type")),
        "suggested_duration_hr": glof_utils.GLOF_DEFAULT_DURATION_HR,
        "suggested_rise_frac": glof_utils.GLOF_RISE_FRAC,
    })


@app.route("/area/snowmelt-discharge", methods=["POST"])
def area_snowmelt_discharge():
    """Real SRM-style degree-day snowmelt discharge for the drawn AOI --
    see srm_utils.py for the real, cited method (temperature lapse rate +
    elevation-band/hypsometric melt) and its one stated simplification
    (a snow-line elevation standing in for a true per-cell snow-covered-
    area product). Real current temperature + the real elevation Open-
    Meteo answered for come from the same free, no-key vendor already
    used for rainfall (rainfall_rivers_utils.fetch_temperature) -- a
    pure, fast calculation once the AOI's own DEM is cropped, no async
    job needed."""
    body = request.json
    geom = body["geometry"]
    snow_line_elev_m = body.get("snow_line_elev_m")
    if snow_line_elev_m is None:
        return jsonify({"error": "provide a real snow-line elevation (m)"}), 400
    ddf = float(body.get("ddf_mm_per_c_per_day", srm_utils.DEFAULT_DDF_MM_PER_C_PER_DAY))
    lapse_rate = float(body.get("lapse_rate_c_per_m", srm_utils.DEFAULT_LAPSE_RATE_C_PER_M))
    allow_global = bool(body.get("use_global_dem", False))

    tmp_ascii = os.path.join(AOI3D_CACHE, f"snowmelt_{uuid.uuid4().hex}.dem.ascii")
    try:
        dem_source, dem_label = current_dem_source(geom, allow_global=allow_global)
        header, active_cells, total_cells = crop_dem_to_polygon(
            dem_source, geom, tmp_ascii)
        _, dem = _read_ascii(tmp_ascii)
        nodata = header.get("nodata_value", -9999.0)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    finally:
        try:
            os.remove(tmp_ascii)
        except OSError:
            pass

    poly = shapely_shape(geom)
    lon_c, lat_c = poly.centroid.x, poly.centroid.y
    try:
        temp = fetch_temperature(lat_c, lon_c)
    except Exception as e:
        return jsonify({"error": f"real temperature fetch failed: {e}"}), 502
    if temp.get("current_temp_c") is None or temp.get("elevation_m") is None:
        return jsonify({"error": "Open-Meteo returned no usable real temperature/elevation for this AOI"}), 502

    result = srm_utils.snowmelt_discharge_m3s(
        dem, nodata, header["cellsize"], t_ref_c=temp["current_temp_c"], ref_elev_m=temp["elevation_m"],
        snow_line_elev_m=float(snow_line_elev_m), ddf_mm_per_c_per_day=ddf, lapse_rate_c_per_m=lapse_rate)
    result["dem_source"] = dem_label
    result["ref_temp_source"] = temp["source"]
    result["suggested_duration_hr"] = 24.0  # a real, roughly steady daily meltwater contribution, not a sharp GLOF-style peak
    result["suggested_rise_frac"] = 0.5     # symmetric/near-constant over that period, unlike a real GLOF's fast rise
    return jsonify(result)


@app.route("/data-status")
def data_status():
    """Every external dataset path the app depends on, and whether it
    exists right now -- see data_paths.py. A missing required path (e.g.
    the Pakistan DEM) is reported here instead of surfacing as silently
    wrong results."""
    rows = data_paths.check_data_paths()
    return jsonify({
        "all_required_present": not data_paths.missing_required(),
        "paths": [{"name": n, "path": p, "exists": ok, "required": req} for n, p, ok, req in rows],
    })


@app.route("/dams/geojson")
def dams_geojson():
    """Real, nationwide dam point + reservoir polygon locations (Global
    Dam Watch/GOODD schema, Dams data/Barriers.shp + Reservoirs.shp) as
    one GeoJSON FeatureCollection -- for the 2D area-selection map's own
    optional "Dams & reservoirs" overlay toggle. Small, static, real
    data (101 dams + 86 reservoirs) -- fetched once by the page, not
    per-AOI, since this isn't bbox-limited the way a flood run's own
    conditioning lookup is (see dams_utils.condition_dem_with_dams for
    that AOI-scoped, always-on use)."""
    if not dams_utils.dams_data_available():
        return jsonify({"type": "FeatureCollection", "features": []})
    features = []
    for d in dams_utils.fetch_dams_for_bounds(-180, -90, 180, 90):
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
            "properties": {"kind": "dam", "name": d["name"], "river": d["river"],
                           "height_m": d["height_m"], "height_is_real": d["height_is_real"], "year": d["year"]},
        })
    for r in dams_utils.fetch_reservoirs_for_bounds(-180, -90, 180, 90):
        features.append({
            "type": "Feature", "geometry": mapping(r["geometry"]),
            "properties": {"kind": "reservoir", "name": r["name"], "depth_m": r["depth_m"],
                           "cap_mcm": r["cap_mcm"], "area_skm": r["area_skm"]},
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/dams/failure-list")
def dams_failure_list():
    """Every real dam and whether an honest failure scenario is possible for
    it (real height / estimated height / unusable) -- see dams_utils.
    load_dam_failure_table."""
    return jsonify({"dams": dams_utils.load_dam_failure_table()})


@app.route("/dams/failure-scenario", methods=["POST"])
def dams_failure_scenario():
    """Real Froehlich (1995) dam-failure peak discharge for one real dam --
    see dams_utils.dam_failure_scenario for the volume-conserving duration
    and the stated height-estimate uncertainty."""
    gdw_id = (request.json or {}).get("gdw_id")
    if gdw_id is None:
        return jsonify({"error": "provide gdw_id"}), 400
    res = dams_utils.dam_failure_scenario(gdw_id)
    if res is None:
        return jsonify({"error": f"unknown dam id {gdw_id}"}), 404
    if res.get("peak_m3s") is None:
        return jsonify({"error": f"No honest failure estimate possible for {res['name']}: {res['reason']}."}), 400
    res["method"] = ("Froehlich (1995) breach peak discharge Qp = 0.607 V^0.295 h^1.24 from this dam's real "
                     "stored volume; hydrograph duration from volume conservation (T = 2V/Qp, so the whole "
                     "reservoir drains -- an upper bound). Froehlich's regression was fitted mostly to much "
                     "smaller embankment dams, so for the very largest dams this is an extrapolation and "
                     "should be read as order-of-magnitude.")
    return jsonify(res)


@app.route("/dams/spillway-outlook")
def dams_spillway_outlook():
    """Forecast inflow vs the level at which a dam has historically opened its
    spillways -- see dams_utils.spillway_outlook for what was validated (only
    Tarbela) and why Mangla is refused."""
    dam = request.args.get("dam", "")
    try:
        return jsonify(dams_utils.spillway_outlook(dam, forecast_days=int(request.args.get("days", 7))))
    except Exception as e:
        return jsonify({"status": "error", "dam": dam, "reason": f"forecast fetch failed: {e}"}), 502


@app.route("/dams/spillway-events")
def dams_spillway_events():
    """Real, documented historical dam spillway opening/release events
    (dam_spillway_opening_events.csv -- see dams_utils.load_spillway_
    events's own docstring for the real cited sources and how outflow is
    computed/validated). Merges in each real event's own dam location
    (Barriers.shp) so the frontend can site it as a boundary inflow
    without the user entering coordinates by hand."""
    events = dams_utils.load_spillway_events()
    for e in events:
        loc = dams_utils.dam_location_by_name(e["dam"])
        e["lon"] = loc["lon"] if loc else None
        e["lat"] = loc["lat"] if loc else None
    return jsonify({"events": events})


@app.route("/area/snowmelt-hydrograph", methods=["POST"])
def area_snowmelt_hydrograph():
    """Multi-day SRM snowmelt hydrograph from a real hourly temperature
    FORECAST -- see srm_utils.snowmelt_hydrograph. Returns a real time
    series (times_hr, q_m3s) the frontend can add as a time-varying inflow
    (flood_model.py's inflow `series` support)."""
    body = request.json
    geom = body["geometry"]
    if body.get("snow_line_elev_m") is None:
        return jsonify({"error": "provide a real snow-line elevation (m)"}), 400
    days = int(body.get("forecast_days", 7))
    ddf = float(body.get("ddf_mm_per_c_per_day", srm_utils.DEFAULT_DDF_MM_PER_C_PER_DAY))
    lapse = float(body.get("lapse_rate_c_per_m", srm_utils.DEFAULT_LAPSE_RATE_C_PER_M))
    tmp_ascii = os.path.join(AOI3D_CACHE, f"snowmelt_h_{uuid.uuid4().hex}.dem.ascii")
    try:
        dem_source, dem_label = current_dem_source(geom, allow_global=bool(body.get("use_global_dem", False)))
        header, _a, _t = crop_dem_to_polygon(dem_source, geom, tmp_ascii)
        _, dem = _read_ascii(tmp_ascii)
        nodata = header.get("nodata_value", -9999.0)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    finally:
        try:
            os.remove(tmp_ascii)
        except OSError:
            pass
    c = shapely_shape(geom).centroid
    try:
        fc = fetch_temperature_forecast(c.y, c.x, forecast_days=days)
    except Exception as e:
        return jsonify({"error": f"real temperature forecast fetch failed: {e}"}), 502
    if fc.get("elevation_m") is None or len(fc["hourly_temp_c"]) < 12:
        return jsonify({"error": "Open-Meteo returned too little forecast data for this AOI"}), 502
    try:
        h = srm_utils.snowmelt_hydrograph(dem, nodata, header["cellsize"], fc["hourly_temp_c"], fc["elevation_m"],
                                          float(body["snow_line_elev_m"]), ddf_mm_per_c_per_day=ddf,
                                          lapse_rate_c_per_m=lapse)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    h.update({"dem_source": dem_label, "temp_source": fc["source"], "forecast_start": fc["start_label"],
              "ref_elev_m": fc["elevation_m"]})
    return jsonify(h)



@app.route("/area/melt-model", methods=["POST"])
def area_melt_model():
    """Distributed snow + glacier melt model (hydro_model.py) over the drawn AOI, treated as ONE basin, driven by
    local hourly ERA5 -- see melt_hydrograph_utils.py. Async (prepare ~1 min + run ~0.5-2 min, cached after)."""
    body = request.json
    geom = body["geometry"]
    start, end = body.get("start"), body.get("end")
    if not start or not end:
        return jsonify({"error": "provide start and end dates (YYYY-MM-DD)"}), 400
    try:
        import pandas as _pd
        n_days = (_pd.Timestamp(end) - _pd.Timestamp(start)).days
    except Exception:
        return jsonify({"error": "invalid dates"}), 400
    if n_days < 30 or n_days > 800:
        return jsonify({"error": "choose a period of 30-800 days (include the preceding winter so snowpack builds up)"}), 400
    extra = {}
    if body.get("lapse_rate_c_per_km") is not None:
        extra["lapse_rate_c_per_m"] = -abs(float(body["lapse_rate_c_per_km"])) / 1000.0
    for k_ in ("ddf_snow", "ddf_ice", "precip_correction", "default_glacier_thickness_m", "temp_bias_c"):
        if body.get(k_) is not None:
            extra[k_] = float(body[k_])
    for name in ("PAKISTAN_DEM", "PMD_GLACIER_SHP", "ERA5_T2M_ZARR", "ERA5_TP_ZARR"):
        if not os.path.exists(data_paths.get(name)):
            return jsonify({"error": f"required dataset missing on this server: {name}"}), 400
    job_id = _start_climate_job(melt_hydrograph_utils.run_for_geometry, geom=geom, start=start, end=end,
                                params_extra=extra)
    return jsonify({"job_id": job_id})


@app.route("/area/melt-model/calibrate", methods=["POST"])
def area_melt_model_calibrate():
    """Per-basin calibration of precipitation/degree-day factors against GloFAS with a hold-out period. Async,
    slow (15+ model runs, cached)."""
    body = request.json
    for k_ in ("cal_start", "cal_end", "val_start", "val_end"):
        if not body.get(k_):
            return jsonify({"error": f"provide {k_} (YYYY-MM-DD)"}), 400
    for name in ("PAKISTAN_DEM", "PMD_GLACIER_SHP", "ERA5_T2M_ZARR", "ERA5_TP_ZARR"):
        if not os.path.exists(data_paths.get(name)):
            return jsonify({"error": f"required dataset missing on this server: {name}"}), 400
    job_id = _start_climate_job(melt_hydrograph_utils.calibrate_against_glofas, geom=body["geometry"],
                                cal_start=body["cal_start"], cal_end=body["cal_end"],
                                val_start=body["val_start"], val_end=body["val_end"])
    return jsonify({"job_id": job_id})


@app.route("/area/melt-model/inflow-series", methods=["POST"])
def area_melt_model_inflow_series():
    """Daily melt-model discharge -> the time-varying inflow `series` (window around the peak day)."""
    body = request.json
    ser = melt_hydrograph_utils.flood_window_series(body["dates"], body["q_m3s"], body.get("center_day"),
                                                    int(body.get("half_window_days", 3)))
    return jsonify(ser)

@app.route("/area/geomorphology", methods=["POST"])
def area_geomorphology():
    """Real bedrock lithology + landslide/debris-flow susceptibility for
    the drawn AOI -- see geomorphology_utils.py for the real, cited GLiM
    (Hartmann & Moosdorf 2012) source and the real, stated scope of its
    two-factor (lithology erodibility x real DEM slope) susceptibility
    simplification. A pure, fast calculation once the AOI's own DEM is
    cropped -- no async job needed."""
    body = request.json
    geom = body["geometry"]
    allow_global = bool(body.get("use_global_dem", False))

    if not geomorphology_utils.glim_pakistan_available():
        return jsonify({"error": "Real GLiM lithology data is not available on this server -- see "
                                  "global_data/fetch_glim_pakistan.py."}), 400

    tmp_ascii = os.path.join(AOI3D_CACHE, f"geomorph_{uuid.uuid4().hex}.dem.ascii")
    try:
        dem_source, dem_label = current_dem_source(geom, allow_global=allow_global)
        header, active_cells, total_cells = crop_dem_to_polygon(
            dem_source, geom, tmp_ascii)
        _, dem = _read_ascii(tmp_ascii)
        nodata = header.get("nodata_value", -9999.0)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    finally:
        try:
            os.remove(tmp_ascii)
        except OSError:
            pass

    mean_slope_pct, max_slope_pct = flood_model.mean_terrain_slope_pct(dem, header["cellsize"], nodata)
    erod_grid = geomorphology_utils.lithology_erodibility_grid(header, header["src_crs"])
    valid = dem != nodata
    valid_erod = erod_grid[valid]
    valid_erod = valid_erod[~np.isnan(valid_erod)]
    mean_erod = float(valid_erod.mean()) if valid_erod.size else None
    susceptibility = geomorphology_utils.landslide_susceptibility(mean_erod, mean_slope_pct or 0.0)

    lon_min, lat_min, lon_max, lat_max = geomorphology_utils._dem_bounds_wgs84(header, header["src_crs"])
    features = geomorphology_utils.fetch_lithology_for_bounds(lon_min, lat_min, lon_max, lat_max)
    class_counts = {}
    for f in features:
        class_counts[f["label"]] = class_counts.get(f["label"], 0) + 1

    try:
        landslide_dam = geomorphology_utils.landslide_dam_assessment(geom)
    except Exception as e:  # real-map layer must never break the original lithology result
        landslide_dam = {"ml": None, "valley": None, "verdict": None, "error": str(e)}

    return jsonify({
        "landslide_dam": landslide_dam,
        "dem_source": dem_label,
        "mean_slope_pct": round(mean_slope_pct, 1) if mean_slope_pct is not None else None,
        "max_slope_pct": round(max_slope_pct, 1) if max_slope_pct is not None else None,
        "mean_erodibility_1to5": round(mean_erod, 2) if mean_erod is not None else None,
        "susceptibility": susceptibility,
        "lithology_classes_present": sorted(class_counts.keys()),
        "method": ("Real GLiM (Hartmann & Moosdorf 2012) lithology, rasterized onto this AOI's own real DEM "
                   "and combined with this AOI's own real mean DEM slope via a real, deliberately simplified "
                   "two-factor susceptibility matrix -- see geomorphology_utils.py for the real erodibility "
                   "rating per rock type and the method's stated limitations versus a full field-surveyed "
                   "landslide hazard assessment."),
    })


@app.route("/area/detect-boundaries", methods=["POST"])
def area_detect_boundaries():
    """Real, automatic, 360-degree multi-inflow/multi-outflow detection --
    supersedes the app's original single-best-crossing /area/river:
    finds EVERY real river/stream/canal crossing the drawn AOI's TRUE
    boundary (not just its bbox), classifies each as a real inflow or
    outflow from the waterway's own digitized flow direction, and
    auto-fills a real discharge estimate (Open-Meteo Flood API / GloFAS)
    for each real inflow. See rainfall_rivers_utils.detect_all_boundary_
    crossings for the full real method and its one stated limitation
    (depends on correct real OSM digitization direction)."""
    geom = request.json["geometry"]
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    try:
        crossings = detect_all_boundary_crossings(poly, lon_min, lat_min, lon_max, lat_max)
    except Exception as e:
        return jsonify({"error": f"boundary detection failed: {e}"}), 400
    return jsonify({"crossings": crossings})


def _lonlat_to_rowcol(lon, lat, header, src_crs):
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    x, y = to_src(lon, lat)
    col = int((x - header["xllcorner"]) / header["cellsize"])
    row = int(header["nrows"] - 1 - (y - header["yllcorner"]) / header["cellsize"])
    return row, col


def _rowcol_in_bounds(row, col, header):
    return 0 <= row < header["nrows"] and 0 <= col < header["ncols"]


def _lonlat_path_to_segments(lonlat_coords, header, src_crs, aoi_polygon_wgs84=None):
    """Converts a real-world polyline to grid row/col, split into separate
    segments wherever the path leaves the AOI -- filtering out-of-bounds
    points into one flat list instead would silently reconnect the
    surviving points across the gap, drawing a spurious long line straight
    across the domain (caught visually: looked like giant vertical walls
    cutting through the 3D scene).

    aoi_polygon_wgs84: optional shapely Polygon/MultiPolygon (lon/lat) of
    the ACTUAL drawn/uploaded AOI. The rectangular row/col bounds check
    alone only excludes points outside the DEM's bounding-box grid, which
    can disagree with the terrain's own EXACT vector clip
    (viewer3d_utils.clipGeometryToAoi) for an irregular (non-rectangular)
    polygon -- a road can run through a bbox corner that's inside the grid
    but outside the true drawn shape, rendering as a line extending past
    the visible terrain edge. Same root cause and same fix pattern as the
    buildings-floating-outside-the-AOI bug (buildings_utils.
    get_building_footprints's aoi_polygon_wgs84 check) -- tested here
    per-vertex, in real lon/lat, before any row/col conversion."""
    return _lonlat_paths_to_segments_batch([lonlat_coords], header, src_crs, aoi_polygon_wgs84)[0]


def _lonlat_paths_to_segments_batch(paths, header, src_crs, aoi_polygon_wgs84=None):
    """Same result as running _lonlat_path_to_segments on each path, but one coordinate transform and one
    inside-the-AOI test for ALL vertices (the per-vertex version built a pyproj Transformer for every vertex).
    Returns one list of (rows, cols) segments per input path, in order."""
    lens = [len(p) for p in paths]
    total = sum(lens)
    if total == 0:
        return [[] for _ in paths]
    flat = np.array([pt for p in paths for pt in p], dtype="float64").reshape(-1, 2)
    lon, lat = flat[:, 0], flat[:, 1]
    inside = np.ones(total, dtype=bool) if aoi_polygon_wgs84 is None else shapely.contains_xy(aoi_polygon_wgs84, lon, lat)
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    x, y = to_src(lon, lat)
    col = np.trunc((np.asarray(x) - header["xllcorner"]) / header["cellsize"]).astype("int64")
    row = np.trunc(header["nrows"] - 1 - (np.asarray(y) - header["yllcorner"]) / header["cellsize"]).astype("int64")
    keep = inside & (row >= 0) & (row < header["nrows"]) & (col >= 0) & (col < header["ncols"])
    out, pos = [], 0
    for n in lens:
        segs, cur_r, cur_c = [], [], []
        for k in range(pos, pos + n):
            if keep[k]:
                cur_r.append(int(row[k])); cur_c.append(int(col[k]))
            else:
                if len(cur_r) >= 2:
                    segs.append((cur_r, cur_c))
                cur_r, cur_c = [], []
        if len(cur_r) >= 2:
            segs.append((cur_r, cur_c))
        out.append(segs)
        pos += n
    return out


AREA3D_ASSUMED_HEIGHT_M = 6.0


@app.route("/area/buildings", methods=["POST"])
def area_buildings():
    """Area 3D tool (osm_buildings_3d.py): real building footprints for a
    drawn polygon, anywhere in Pakistan.

    Deliberately Google Open Buildings (fetch_buildings_for_aoi, already
    proven elsewhere in this app), NOT raw OSM way["building"] data --
    checked directly before switching: the same ~1km x 1km bbox this tool
    was tested against has 1,420 real Google Open Buildings footprints vs
    only ~100 from OSM's own building ways, a ~14x undercount. This is the
    exact same real coverage gap this app's main pipeline already worked
    around (see buildings_utils.py's own docstring: OSM height/level TAG
    coverage is separately sparse too, ~2%) -- OSM is used here only to
    supplement real height where it happens to be tagged, never as the
    primary footprint source, consistent with get_building_footprints's
    own approach."""
    geom = request.json.get("geometry")
    if not geom:
        return jsonify({"error": "no area selected"}), 400
    aoi_polygon = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = aoi_polygon.bounds

    key = hashlib.sha1(json.dumps(geom, sort_keys=True).encode()).hexdigest()
    parquet_path = os.path.join(AREA3D_BUILDINGS_CACHE, f"{key}.parquet")
    result_path = os.path.join(AREA3D_BUILDINGS_CACHE, f"{key}.json")
    if os.path.exists(result_path):
        with open(result_path) as f:
            return jsonify(json.load(f))

    try:
        fetch_buildings_for_aoi(lon_min, lat_min, lon_max, lat_max, parquet_path)
    except Exception as e:
        return jsonify({"error": f"building fetch failed: {e}"}), 500
    if not os.path.exists(parquet_path):
        return jsonify({"error": "building fetch failed (no data for this quadkey)"}), 500

    try:
        osm_heights = fetch_osm_building_heights(lon_min, lat_min, lon_max, lat_max)
    except Exception as e:
        osm_heights = []
        print(f"[area3d] OSM height supplement failed, using estimate only: {e}")

    footprints = building_footprints_lonlat(aoi_polygon, parquet_path, osm_heights=osm_heights,
                                             assumed_height_m=AREA3D_ASSUMED_HEIGHT_M)
    features = [
        {"type": "Feature", "properties": {"height_m": fp["height_m"], "height_source": fp["height_source"]},
         "geometry": {"type": "Polygon", "coordinates": [fp["coords"]]}}
        for fp in footprints
    ]
    n_real = sum(1 for f in features if f["properties"]["height_source"] == "osm_height")
    result = {"type": "FeatureCollection", "features": features,
              "n_total": len(footprints), "n_rendered": len(features), "n_real_height": n_real}

    with open(result_path, "w") as f:
        json.dump(result, f)
    return jsonify(result)


def _read_ascii(path):
    with open(path) as f:
        header = {}
        for _ in range(6):
            k, v = f.readline().split()
            header[k.lower()] = float(v)
        data = np.loadtxt(f)
    return header, data


def _write_ascii(path, header, data, nodata=-9999):
    with open(path, "w") as f:
        f.write(f"ncols         {int(header['ncols'])}\n")
        f.write(f"nrows         {int(header['nrows'])}\n")
        f.write(f"xllcorner     {header['xllcorner']}\n")
        f.write(f"yllcorner     {header['yllcorner']}\n")
        f.write(f"cellsize      {header['cellsize']}\n")
        f.write(f"NODATA_value  {nodata}\n")
        np.savetxt(f, data, fmt="%.4f")


def _snap_inflow_to_valid_edge(header, dem, inflow_edge, lon, lat, src_crs, nodata=-9999):
    """Converts a real river-crossing lon/lat into the DEM-local metres
    flood_model.py's --inflow-pos-m expects, SNAPPED to the nearest cell
    on that edge that's actually valid terrain.

    Real finding: the AOI's true polygon (after reprojection to the DEM's
    UTM CRS) routinely does NOT reach the rectangular grid's own edge in a
    straight line -- a WGS84 rectangle isn't a rectangle after UTM
    reprojection, and an arbitrary drawn polygon even less so. A test AOI
    here had its ENTIRE west boundary column NODATA (0 of 201 cells valid)
    with the true valid terrain only starting several columns in. Placing
    the inflow segment at the naive projected position (which assumes the
    boundary column is always valid) silently landed it entirely on NODATA
    cells -- LISFLOOD-FP has nothing to inject flow into there, so the
    whole run produced exactly zero depth everywhere despite a real,
    correctly-formed QVAR hydrograph. Snapping to the nearest actually-
    valid cell on the same edge fixes this; returns None (caller falls
    back to the original whole-edge behaviour) if the entire edge is
    NODATA, which can happen for a genuinely tiny/degenerate AOI."""
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    x_c, y_c = to_src(lon, lat)
    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]
    if inflow_edge in ("N", "S"):
        edge_row = 0 if inflow_edge == "N" else nrows - 1
        valid_cols = np.where(dem[edge_row, :] != nodata)[0]
        if valid_cols.size == 0:
            return None
        target_col = (x_c - xll) / cs
        best_col = valid_cols[np.argmin(np.abs(valid_cols - target_col))]
        return (best_col + 0.5) * cs
    else:
        edge_col = 0 if inflow_edge == "W" else ncols - 1
        valid_rows = np.where(dem[:, edge_col] != nodata)[0]
        if valid_rows.size == 0:
            return None
        target_row = nrows - 1 - (y_c - yll) / cs
        best_row = valid_rows[np.argmin(np.abs(valid_rows - target_row))]
        return (nrows - best_row - 0.5) * cs


def _resolve_boundary_item(header, dem, src_crs, item, log_path):
    """Resolves one real inflow/outflow request item (edge + either a real
    lon/lat crossing point, a manual position percentage, or neither) into
    the {"edge","pos_m","width_m","name"} shape flood_model.py's boundary
    JSON expects. Returns None (item skipped, logged) if the edge is
    missing/invalid or a real lon/lat snap fails entirely (whole edge
    NODATA)."""
    edge = item.get("edge")
    if edge not in ("N", "E", "S", "W"):
        with open(log_path, "a") as logf:
            logf.write(f"[boundary] skipping item with invalid/missing edge: {item}\n")
        return None
    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    span = (ncols * cs) if edge in ("N", "S") else (nrows * cs)

    pos_m = None
    if item.get("lon") is not None and item.get("lat") is not None:
        pos_m = _snap_inflow_to_valid_edge(header, dem, edge, item["lon"], item["lat"], src_crs)
        if pos_m is None:
            with open(log_path, "a") as logf:
                logf.write(f"[boundary] '{item.get('name', edge)}': detected crossing's edge has no valid "
                           f"terrain at all -- skipping this item\n")
            return None
    elif item.get("pos_pct") is not None:
        pos_m = max(0.0, min(100.0, float(item["pos_pct"]))) / 100.0 * span

    return {"edge": edge, "pos_m": pos_m, "width_m": item.get("width_m", 30.0),
            "name": item.get("name", edge)}


def execute_fis_run(run_id, run_dir, dem_ascii_path, rain_mm_hr, duration_hr,
                     include_buildings, src_crs, lon_min, lat_min, lon_max, lat_max,
                     friction=0.05, cfl=0.7, infiltration_mm_hr=0.0, amc=50.0,
                     inflows=None, outflows=None, geom=None,
                     use_soil_infiltration=False, use_channel_conditioning=False,
                     enable_arf=False, storm_center_lon=None, storm_center_lat=None,
                     storm_velocity_lon_per_hr=None, storm_velocity_lat_per_hr=None,
                     storm_motion_message=None,
                     use_green_ampt=False, rainfall_series=None, imported=None, manning_scale=1.0):
    inflows = inflows or []
    outflows = outflows or []
    if manning_scale != 1.0:
        friction = max(friction * manning_scale, 0.01)   # fallback roughness follows the per-cell grid's multiplier
    with SIM_SLOT:
        with RUN_STATE_LOCK:
            if run_id in RUN_QUEUE:
                RUN_QUEUE.remove(run_id)
            if run_id in RUN_CANCEL:
                RUN_STATE[run_id]["status"] = "cancelled"
                return
            RUN_STATE[run_id]["status"] = "running"
            RUN_STATE[run_id]["stage"] = "buildings"
            RUN_STATE[run_id]["start"] = time.time()

        log_path = os.path.join(run_dir, "run.log")
        n_buildings_used = 0

        # Roads/infra (OSM Overpass) and buildings (Google Open Buildings)
        # are two independent real-data fetches -- the ONLY reason roads
        # used to be fetched much later (after the flood model run) was
        # that its RISK CLASSIFICATION needs the run's own depth/hazard
        # output, not that the raw fetch itself needs anything from the
        # physics run. Kicked off in a background thread right here,
        # overlapping with the buildings fetch AND the physics run itself,
        # instead of only starting once both of those are already done --
        # real wall-clock savings, not just reordering. The actual risk
        # sampling (assess_infrastructure_risk, which DOES need the run's
        # real depth/hazard arrays) still happens after the model run, via
        # a plain .join() on this thread that returns immediately if the
        # fetch already finished during that overlap window.
        infra_result = {}

        def _fetch_infra_bg():
            try:
                infra_result["infra"] = fetch_osm_infrastructure(lon_min, lat_min, lon_max, lat_max)
            except Exception as e:
                infra_result["error"] = e

        infra_thread = threading.Thread(target=_fetch_infra_bg, daemon=True)
        infra_thread.start()

        bare_dem_path = os.path.join(run_dir, "dem_bare.ascii")
        header0, dem0 = _read_ascii(dem_ascii_path)

        # Real, PERMANENT dam/reservoir conditioning -- applied
        # unconditionally, before anything else, to EVERY real run whose
        # AOI overlaps real existing Pakistani (or upstream) dam/
        # reservoir infrastructure (Dams data/Barriers.shp + Reservoirs.
        # shp, real Global Dam Watch/GOODD data -- Tarbela, Mangla,
        # Chashma, etc.). Unlike protection_utils.py's hypothetical
        # user-drawn structures, these are real, already-built features
        # that genuinely alter today's terrain -- so they're conditioned
        # into dem0 itself, BEFORE the bare-DEM copy below is saved,
        # meaning both the real 3D terrain (which uses the bare copy) and
        # every downstream physics stage (channel/buildings conditioning,
        # the solver itself) see the real reservoir depressions and real
        # dam barriers as part of the AOI's own real terrain, not an
        # optional overlay. See dams_utils.py for the real data fields
        # used and the real, stated approximation (a dam's exact as-built
        # footprint isn't in this dataset, only its real point location
        # and real height).
        dams_summary = {"n_reservoirs_conditioned": 0, "n_dams_conditioned": 0}
        try:
            dem0, dams_summary = dams_utils.condition_dem_with_dams(dem0, header0, src_crs)
            if dams_summary["n_reservoirs_conditioned"] or dams_summary["n_dams_conditioned"]:
                with open(log_path, "a") as logf:
                    logf.write(f"[dams] permanent conditioning applied: "
                               f"{dams_summary['n_reservoirs_conditioned']} real reservoir(s) "
                               f"{dams_summary['reservoir_names']}, "
                               f"{dams_summary['n_dams_conditioned']} real dam(s) "
                               f"{dams_summary['dam_names']}\n")
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[dams] permanent conditioning failed, continuing without it: {e}\n")
        try:
            meta_path_dams = os.path.join(run_dir, "run_meta.json")
            with open(meta_path_dams) as f:
                meta_dams = json.load(f)
            meta_dams["dams_conditioning"] = dams_summary
            with open(meta_path_dams, "w") as f:
                json.dump(meta_dams, f)
        except Exception:
            pass

        _write_ascii(dem_ascii_path, header0, dem0)
        # Pre-conditioning (of BUILDINGS specifically -- real dams/
        # reservoirs above are already baked in) copy, kept for 3D
        # terrain -- rendering the building-raised DEM instead shows
        # buildings as raised terrain "blobs" baked into the mesh rather
        # than proper extruded shapes.
        _write_ascii(bare_dem_path, header0, dem0)

        # Real SoilGrids-derived infiltration rate, Pakistan only (the
        # only AOI this app has locally cached soil data for -- see
        # global_data/fetch_soilgrids_pakistan.py). Confirmed directly
        # from LISFLOOD-FP 8.2's own source that its ACC/CUDA solver (the
        # only one this app runs) has no code path for a per-cell
        # distributed infiltration file at all, unlike Manning's n, which
        # it genuinely does support per-cell -- so this computes a single,
        # real-soil-informed AREA-WEIGHTED MEAN rate for the whole AOI
        # instead of a per-cell grid the solver would silently ignore
        # (see soil_utils.mean_infiltration_mm_hr_for_aoi's own docstring).
        # Only overrides infiltration_mm_hr when explicitly requested and
        # the real data is actually available -- otherwise the user's own
        # manually-entered flat rate is used unchanged, same as before.
        soil_infiltration_source = "user-specified flat rate"
        if use_green_ampt:
            # Real Green-Ampt (Rawls, Brakensiek & Miller 1983, Table 2):
            # a genuinely time-varying infiltration physics model, reduced
            # here to its real equivalent AVERAGE rate over this run's own
            # storm duration (LISFLOOD-FP's solver only ever accepts one
            # flat scalar -- see soil_utils.green_ampt_average_rate_mm_hr's
            # own docstring). Antecedent moisture (this run's own AMC
            # slider, 0-100) drives the real moisture-deficit term
            # DIRECTLY, replacing the older AMC_INFILTRATION_MULTIPLIER
            # heuristic for this path -- so `amc` is forced to 50 (a
            # neutral 1.0x) below, to avoid double-applying antecedent
            # moisture through both the real physics here AND the old
            # heuristic multiplier flood_model.py's own --amc flag would
            # otherwise still apply on top.
            try:
                valid_mask0 = dem0 != -9999
                ga_rate = soil_utils.mean_green_ampt_rate_mm_hr_for_aoi(
                    header0, src_crs, duration_hr, saturation_frac=amc / 100.0, valid_mask=valid_mask0)
                if ga_rate is not None:
                    infiltration_mm_hr = ga_rate
                    soil_infiltration_source = (
                        f"Green-Ampt (Rawls, Brakensiek & Miller 1983; SoilGrids texture, "
                        f"AMC={amc:.0f}/100 antecedent saturation; {ga_rate:.2f} mm/hr real storm-average)")
                    amc = 50.0  # neutralize flood_model.py's own AMC multiplier -- already applied above
                else:
                    with open(log_path, "a") as logf:
                        logf.write("[soil] Green-Ampt requested but local SoilGrids data not found "
                                   "(run global_data/fetch_soilgrids_pakistan.py) -- using flat rate\n")
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[soil] Green-Ampt infiltration failed, using flat rate: {e}\n")
        elif use_soil_infiltration:
            try:
                valid_mask0 = dem0 != -9999
                soil_rate = soil_utils.mean_infiltration_mm_hr_for_aoi(header0, src_crs, valid_mask=valid_mask0)
                if soil_rate is not None:
                    infiltration_mm_hr = soil_rate
                    soil_infiltration_source = (
                        f"SoilGrids-derived (area-weighted mean Ksat, Rawls et al. 1983, "
                        f"{soil_rate:.2f} mm/hr)")
                else:
                    with open(log_path, "a") as logf:
                        logf.write("[soil] SoilGrids infiltration requested but local data not found "
                                   "(run global_data/fetch_soilgrids_pakistan.py) -- using flat rate\n")
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[soil] SoilGrids-derived infiltration failed, using flat rate: {e}\n")
        try:
            meta_path0 = os.path.join(run_dir, "run_meta.json")
            with open(meta_path0) as f:
                meta0 = json.load(f)
            meta0["infiltration_mm_hr"] = infiltration_mm_hr
            meta0["infiltration_source"] = soil_infiltration_source
            with open(meta_path0, "w") as f:
                json.dump(meta0, f)
        except Exception:
            pass

        # Per-cell Manning's n from the SAME real ESA WorldCover land cover
        # Tab 5 already uses for damage -- floodplain friction now varies by
        # real land cover instead of one uniform guess for the whole AOI,
        # per explicit request. A third independent network fetch, kicked
        # off in the background alongside buildings/infra for the same real
        # wall-clock-overlap reason as those two.
        manning_path = os.path.join(run_dir, "manning.ascii")
        manning_result = {}

        def _fetch_manning_bg():
            try:
                lulc0 = fetch_lulc_for_bounds(lon_min, lat_min, lon_max, lat_max, header0, src_crs)
                manning_grid = lulc_to_manning_grid(lulc0)
                if manning_scale != 1.0:   # calibrated roughness multiplier (calibration_utils), floored like the solver wrapper
                    manning_grid = np.where(manning_grid > 0, np.maximum(manning_grid * manning_scale, 0.01), manning_grid)
                _write_ascii(manning_path, header0, manning_grid)
                np.save(os.path.join(run_dir, "lulc.npy"), lulc0)  # reused for damage/hotspot + NbS scenarios
                manning_result["ok"] = True
                manning_result["lulc"] = lulc0  # reused for real damage/hotspot analysis after the run
            except Exception as e:
                manning_result["error"] = e

        manning_thread = threading.Thread(target=_fetch_manning_bg, daemon=True)
        manning_thread.start()

        # --- Pseudo-SGC: real channel bathymetry conditioned into the DEM
        # (see channel_utils.py's own docstring for exactly what this is
        # and is not -- LISFLOOD-FP's real SGC solver has zero GPU support,
        # confirmed directly from source, so this achieves the same real
        # physical goal, a channel with genuine conveyance capacity below
        # the DEM surface, via DEM conditioning instead, staying fully
        # compatible with the GPU solver this app already runs). Only
        # meaningful for a fluvial run (a real design discharge to size
        # the channel against) and only applied when explicitly requested.
        channel_summary = {"applied": False, "reason": "not requested"}
        max_inflow_m3s = max((i.get("peak_m3s", 0.0) for i in inflows), default=0.0)
        if use_channel_conditioning and max_inflow_m3s > 0:
            try:
                waterways = fetch_waterway_lines(lon_min, lat_min, lon_max, lat_max)
                header, dem = _read_ascii(dem_ascii_path)
                # Sized against the largest of any real simultaneous
                # inflows -- a real, stated simplification when more than
                # one inflow is active (the channel isn't individually
                # sized per waterway), same honest-approximation spirit
                # already documented for this feature's single-inflow case.
                conditioned, channel_mask, channel_summary = condition_dem_with_channel(
                    header, dem, waterways, max_inflow_m3s, src_crs)
                if channel_summary.get("applied"):
                    _write_ascii(dem_ascii_path, header, conditioned)
                    np.save(os.path.join(run_dir, "channel_mask.npy"), channel_mask)
                with open(log_path, "a") as logf:
                    logf.write(f"[channel] pseudo-SGC conditioning: {channel_summary}\n")
            except Exception as e:
                channel_summary = {"applied": False, "reason": f"failed: {e}"}
                with open(log_path, "a") as logf:
                    logf.write(f"[channel] pseudo-SGC conditioning failed, continuing without it: {e}\n")
        try:
            meta_path_ch = os.path.join(run_dir, "run_meta.json")
            with open(meta_path_ch) as f:
                meta_ch = json.load(f)
            meta_ch["channel_conditioning"] = channel_summary
            with open(meta_path_ch, "w") as f:
                json.dump(meta_ch, f)
        except Exception:
            pass

        # --- Tab 4, part 1: buildings conditioning (affects the flow) ---
        buildings_parquet_path = None
        imported_heights = None
        if imported and imported.get("drains"):
            try:
                header, dem = _read_ascii(dem_ascii_path)
                structs = [{"type": "drainage", "line": d["line"], "height_m": min(2.5, 1.0 + 0.25 * float(d.get("width", 2))),
                            "half_width_m": max(1.5, float(d.get("width", 2)) / 2)} for d in imported["drains"]]
                _write_ascii(dem_ascii_path, header, protection_utils.apply_structures_to_dem(dem, header, src_crs, structs))
                with open(log_path, "a") as logf:
                    logf.write(f"[import] {len(structs)} imported drains dug into the DEM\n")
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[import] drains failed, continuing without them: {e}\n")
        if include_buildings:
            try:
                to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
                buildings_cache = os.path.join(run_dir, "buildings.parquet")
                if imported and imported.get("buildings"):
                    # the plan's own footprints replace the real buildings of the area
                    import pandas as _pd
                    from shapely.geometry import Polygon as _P
                    _rows, imported_heights = [], []
                    for _b in imported["buildings"]:
                        _poly = _P(_b["coords"])
                        if _poly.is_valid and not _poly.is_empty:
                            _rows.append({"area_in_meters": 0.0, "confidence": 1.0, "wkt": _poly.wkt})
                            if _b.get("h"):
                                imported_heights.append({"height_m": float(_b["h"]), "geometry": list(_poly.exterior.coords)})
                    _pd.DataFrame(_rows).to_parquet(buildings_cache)
                    bpath = buildings_cache
                else:
                    bpath = fetch_buildings_for_aoi(lon_min, lat_min, lon_max, lat_max, buildings_cache)
                if bpath:
                    header, dem = _read_ascii(dem_ascii_path)
                    conditioned, mask, n_buildings_used = burn_buildings_into_dem(header, dem, bpath, to_src)
                    _write_ascii(dem_ascii_path, header, conditioned)
                    np.save(os.path.join(run_dir, "building_mask.npy"), mask)
                    buildings_parquet_path = bpath
                    with open(log_path, "a") as logf:
                        logf.write(f"[buildings] {n_buildings_used} buildings burned into DEM\n")
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[buildings] fetch/burn failed, continuing bare-earth: {e}\n")

        with RUN_STATE_LOCK:
            RUN_STATE[run_id]["stage"] = "model"

        cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_ascii_path,
               "--rain-mm-hr", str(rain_mm_hr), "--duration-hr", str(duration_hr),
               "--friction", str(friction), "--cfl", str(cfl),
               "--infiltration-mm-hr", str(infiltration_mm_hr), "--amc", str(amc),
               "--site-name", "FIS area", "--workdir", run_dir]
        if enable_arf:
            cmd += ["--enable-arf"]
        if rainfall_series:
            # Real uploaded/observed rainfall series (see
            # rainfall_csv_utils.py) -- drives the physics directly via
            # flood_model.py's --rain-series-json instead of the
            # triangular design storm --rain-mm-hr/--duration-hr would
            # build. Mutually exclusive with storm-centering (a real
            # point observation has no spatial pattern of its own to
            # combine with an exponential-decay field), so this branch
            # takes priority and the storm-centering blocks below are
            # skipped when a real series is present.
            series_path = os.path.join(run_dir, "rain_series.json")
            with open(series_path, "w") as f:
                json.dump(rainfall_series, f)
            cmd += ["--rain-series-json", series_path]
            with open(log_path, "a") as logf:
                logf.write(f"[rain] real uploaded rainfall series "
                           f"({rainfall_series.get('label', 'unlabeled')}): "
                           f"{len(rainfall_series['times_hr'])} points\n")
        has_storm_velocity = (storm_velocity_lon_per_hr not in (None, 0.0) or
                              storm_velocity_lat_per_hr not in (None, 0.0))
        if rainfall_series:
            pass  # real uploaded series already handled above; skip storm-centering entirely
        elif storm_center_lon is not None and storm_center_lat is not None and has_storm_velocity:
            # Real MOVING storm center -- see storm_utils.write_rain_netcdf_moving.
            # The velocity here either came from a real LSTM prediction
            # (storm_motion_lstm.predict_storm_velocity_for_aoi, called by the
            # /run route before this thread started) or a manually-entered
            # value; either way it's applied via the SAME solver mechanism
            # (LISFLOOD_BIN_NETCDF's DynamicRain), just recomputing the
            # spatial weight field at every timestep instead of once.
            cmd += ["--storm-center-lon0", str(storm_center_lon),
                    "--storm-center-lat0", str(storm_center_lat),
                    "--storm-velocity-lon-per-hr", str(storm_velocity_lon_per_hr or 0.0),
                    "--storm-velocity-lat-per-hr", str(storm_velocity_lat_per_hr or 0.0),
                    "--dem-src-crs", str(src_crs)]
            with open(log_path, "a") as logf:
                logf.write(f"[storm] real MOVING storm center from ({storm_center_lon:.5f},"
                           f"{storm_center_lat:.5f}) at ({storm_velocity_lon_per_hr:.4f},"
                           f"{storm_velocity_lat_per_hr:.4f}) deg/hr\n")
                if storm_motion_message:
                    logf.write(f"[storm] {storm_motion_message}\n")
        elif storm_center_lon is not None and storm_center_lat is not None:
            # Real storm-centered, exponentially-decaying spatial rainfall
            # -- see storm_utils.py. Uses the BARE (pre-building) DEM's own
            # valid-cell mask/extent (header0/dem0), since the spatial
            # weight field only depends on real AOI shape/scale, not
            # whether buildings were burned in afterward.
            valid0 = dem0 != header0.get("nodata_value", -9999)
            try:
                weights, decay_len_m = storm_utils.exponential_decay_weights(
                    header0, src_crs, valid0, storm_center_lon, storm_center_lat)
                weights_path = os.path.join(run_dir, "rain_weights.ascii")
                _write_ascii(weights_path, header0, weights)
                cmd += ["--rain-weights-file", weights_path]
                with open(log_path, "a") as logf:
                    logf.write(f"[storm] real storm-centered rainfall at ({storm_center_lon:.5f},"
                               f"{storm_center_lat:.5f}), decay length {decay_len_m:.0f}m\n")
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[storm] storm-centering failed, falling back to uniform rainfall: {e}\n")
        manning_thread.join()
        if manning_result.get("ok"):
            cmd += ["--manning-file", manning_path]
        else:
            with open(log_path, "a") as logf:
                logf.write(f"[manning] real ESA WorldCover-based friction fetch failed, "
                           f"falling back to uniform friction n={friction}: {manning_result.get('error')}\n")

        boundary_inflows, boundary_outflows = [], []
        for item in inflows:
            resolved = _resolve_boundary_item(header0, dem0, src_crs, item, log_path)
            if resolved is None:
                continue
            resolved.update({"peak_m3s": item.get("peak_m3s", 0.0), "duration_hr": item.get("duration_hr", 0.0),
                              "rise_frac": item.get("rise_frac", 0.5),
                              "series": item.get("series")})
            boundary_inflows.append(resolved)
        for item in outflows:
            resolved = _resolve_boundary_item(header0, dem0, src_crs, item, log_path)
            if resolved is None:
                continue
            boundary_outflows.append(resolved)
        if boundary_inflows or boundary_outflows:
            boundary_path = os.path.join(run_dir, "boundary.json")
            with open(boundary_path, "w") as bf:
                json.dump({"inflows": boundary_inflows, "outflows": boundary_outflows}, bf)
            cmd += ["--boundary-json", boundary_path]
        t0 = time.time()
        with open(log_path, "a") as logf:
            proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
            RUN_PROCS[run_id] = proc
            try:
                proc.wait()
            finally:
                RUN_PROCS.pop(run_id, None)
            elapsed = time.time() - t0
            logf.write(f"\n=== EXIT_CODE:{proc.returncode} elapsed={elapsed:.1f}s ===\n")

        if run_id in RUN_CANCEL:
            with RUN_STATE_LOCK:
                RUN_STATE[run_id]["status"] = "cancelled"
            return
        if proc.returncode != 0:
            with open(log_path) as fh:
                tail = fh.read()[-6000:]
            with RUN_STATE_LOCK:
                RUN_STATE[run_id].update(status="failed", log_tail=tail)
            return

        with RUN_STATE_LOCK:
            RUN_STATE[run_id]["status"] = "postprocessing"
            RUN_STATE[run_id]["stage"] = "postprocess"

        summary_path = os.path.join(run_dir, "summary.json")
        if not os.path.exists(summary_path):
            with RUN_STATE_LOCK:
                RUN_STATE[run_id].update(status="failed", log_tail="no summary.json")
            return
        with open(summary_path) as fh:
            summary = json.load(fh)
        summary["buildings_used"] = n_buildings_used
        summary["sailab_version"] = APP_VERSION
        summary["dams_conditioning"] = dams_summary
        if storm_motion_message:
            # The technical (center/velocity/decay-length) description is
            # already in summary["storm_motion"] (written by flood_model.py
            # itself) -- this appends the real LSTM inference's OWN honest
            # explanation of where that velocity came from (how many real
            # recent IMERG displacement steps it was predicted from, or why
            # it fell back to zero), same transparency principle as
            # d50_source/infiltration_source elsewhere in this app.
            summary["storm_motion_source"] = storm_motion_message

        # --- Single Flood 3D view: bare terrain (not the building-raised
        # DEM -- otherwise buildings show as raised terrain "blobs" instead
        # of proper shapes) + real building footprint extrusion (colored by
        # submersion severity) + real roads (colored by hazard band) +
        # animated flood-depth water, all in ONE scene via viewer3d_utils.
        # There used to be a separate "Buildings & Infra" 3D tab for the
        # colored buildings/roads -- removed at the user's explicit
        # request; everything now lives in the same view as the flood
        # animation itself instead of a second view showing the same
        # buildings over again. ---
        footprints = []
        submersion = None
        risk = {}
        infra = {}
        try:
            _pt = time.time()
            bare_header, bare_dem = _read_ascii(bare_dem_path)
            water = _read_water_stack(run_dir, rain_mm_hr=rain_mm_hr, duration_hr=duration_hr,
                                       rainfall_series=rainfall_series)
            _pt = _tlog(log_path, "depth/velocity stack read", _pt)
            # Defined unconditionally (not just when buildings are fetched)
            # -- the road-clipping code further down needs this real AOI
            # polygon regardless of whether building conditioning ran.
            aoi_polygon_wgs84 = shapely_shape(geom) if geom else None

            if buildings_parquet_path:
                to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
                try:
                    osm_heights = imported_heights if imported_heights is not None else fetch_osm_building_heights(lon_min, lat_min, lon_max, lat_max)
                except Exception as e:
                    osm_heights = []
                    with open(log_path, "a") as logf:
                        logf.write(f"[buildings] OSM height fetch failed, using estimate only: {e}\n")
                footprints = get_building_footprints(bare_header, bare_dem, buildings_parquet_path, to_src,
                                                      osm_heights=osm_heights, aoi_polygon_wgs84=aoi_polygon_wgs84)
                _pt = _tlog(log_path, f"building footprints ({len(footprints):,})", _pt)

            # Building risk color now ANIMATES with the run's own real
            # per-timestep depth (water["depth_stack"]) instead of a single
            # static verdict from the run's peak depth -- a building's
            # color rises toward red while it's actually underwater and
            # fades back toward green as the storm drains, at explicit
            # user request ("as duration goes on and water runs off, its
            # color turns back near green"). The whole-run MAX-based
            # classification (assess_building_submersion_risk) is still
            # computed and saved for the text panel's summary stats (a
            # single "how bad did it get" number is still useful there),
            # but the 3D view gets a real per-frame depth trajectory per
            # building instead of one baked-in color.
            colored_footprints = footprints
            if footprints:
                try:
                    _, max_depth_for_sub = _read_ascii(os.path.join(run_dir, "results", "res.max"))
                    submersion = assess_building_submersion_risk(footprints, max_depth_for_sub, bare_header)
                    with open(os.path.join(run_dir, "building_submersion.json"), "w") as f:
                        json.dump(submersion, f)
                    summary["building_submersion"] = {k: v for k, v in submersion.items() if k != "features"}
                    # Real estimated structure+content loss (Huizinga et al.
                    # 2017/JRC Asia-region max damage), a monetary read of
                    # the SAME per-building damage_fraction/height/footprint
                    # data just computed above -- no second depth-sampling
                    # pass, see loss_utils.py's own docstring for the real
                    # source/caveats (Asia-region average, not Pakistan-
                    # specific; 2010-EUR price level, not converted).
                    try:
                        summary["estimated_building_loss"] = loss_utils.estimate_building_loss_pkr(
                            submersion["features"], cellsize_m=float(bare_header["cellsize"]))
                    except Exception as e:
                        with open(log_path, "a") as logf:
                            logf.write(f"[loss] building loss estimate failed: {e}\n")
                        summary["estimated_building_loss"] = None
                except Exception as e:
                    with open(log_path, "a") as logf:
                        logf.write(f"[buildings] submersion risk assessment failed: {e}\n")
                    summary["building_submersion"] = None
                    summary["estimated_building_loss"] = None
                if water is not None:
                    colored_footprints = _attach_building_depth_series(footprints, water, bare_header)
                    _pt = _tlog(log_path, "per-building depth series", _pt)
            else:
                summary["building_submersion"] = None
                summary["estimated_building_loss"] = None

            # Roads/critical infra/power/trees risk -- also computed here
            # (used to run after the 3D view was already built, for the
            # now-removed separate tab) so real hazard-colored road lines
            # can go into the SAME scene as the buildings and water. Same
            # per-frame animation treatment as buildings: each road's own
            # centroid gets a real depth+speed trajectory sampled from the
            # run's own saved timesteps, so its color animates with the
            # real DEFRA hazard rating (depth x (speed+1.5)) as the storm
            # actually progresses, instead of one static band from the
            # run's peak.
            road_lines = []
            try:
                results_dir = os.path.join(run_dir, "results")
                _, max_depth_for_infra = _read_ascii(os.path.join(results_dir, "res.max"))
                haz_path = os.path.join(results_dir, "res.maxHaz")
                if os.path.exists(haz_path):
                    max_haz = _read_ascii(haz_path)[1]
                else:
                    # Real bug fixed here: the CUDA solver path (ACC and DG2
                    # alike) never writes res.maxHaz at all (see CLAUDE.md),
                    # so this silently passed None straight into
                    # assess_infrastructure_risk on every real CUDA run --
                    # classify_hazard_value(None) then reports "outside
                    # domain" for every genuinely-flooded road/infra feature,
                    # even though the run's own whole-domain hazard_breakdown
                    # (computed via this exact same fallback inside
                    # flood_model.py's own report step) was already correct.
                    # Reuse the identical real per-timestep depth/velocity
                    # analytical fallback here instead of a second, silently
                    # degraded code path.
                    max_haz = flood_model.compute_max_hazard_from_timesteps(
                        results_dir, bare_header.get("nodata_value", -9999.0))
                to_src2 = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform

                # Real time saved here, not just moved: this join returns
                # immediately (0s wait) whenever the background fetch
                # already finished during the buildings-fetch/physics-run
                # window above -- only actually blocks if Overpass was
                # unusually slow and outlasted both.
                infra_thread.join()
                if "error" in infra_result:
                    raise infra_result["error"]
                infra = infra_result["infra"]
                with open(os.path.join(run_dir, "infra_raw.json"), "w") as f:
                    json.dump(infra, f)
                risk = assess_infrastructure_risk(infra, max_depth_for_infra, max_haz, bare_header, to_src2)
                risk_light = {cat: {k: v for k, v in d.items() if k != "features"} for cat, d in risk.items()}
                with open(os.path.join(run_dir, "infra_risk.json"), "w") as f:
                    json.dump(risk, f)
                summary["infra_risk"] = risk_light
                _pt = _tlog(log_path, "infrastructure risk", _pt)

                road_lines = _build_road_lines(infra, water, bare_header, src_crs, aoi_polygon_wgs84)
                _pt = _tlog(log_path, f"road segments ({len(road_lines):,})", _pt)
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[infra] risk assessment failed: {e}\n")
                summary["infra_risk"] = None

            # Real damage % by land use + real Getis-Ord Gi* hotspot
            # analysis is intentionally NOT computed here. It used to run
            # synchronously in this exact spot and was the real cause of a
            # reported "everything got slower" regression: Getis-Ord's own
            # 99-permutation significance test over a full-resolution grid
            # is genuinely expensive (grows with cell count, independent of
            # the physics run itself) and was silently added to the
            # critical path of EVERY run, whether or not anyone ever looked
            # at the Damage panel. Moved to a real lazy, cached, on-demand
            # endpoint instead -- see /runs/<run_id>/damage.json -- so a
            # plain flood run is never slower than before, and the
            # (still real, still genuinely computed) damage/hotspot
            # analysis only costs time when actually requested.

            # No satellite basemap here (unlike the other 3D views) -- shows
            # the plain DEM/hillshade the same way the flood model itself
            # sees the terrain, at the user's explicit request.
            with RUN_STATE_LOCK:
                RUN_STATE[run_id]["stage"] = "scene"
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[3d] pre-scene setup failed: {e}\n")

        # Mapbox-native 3D scene (mapbox3d_utils.py) -- the app's ONLY 3D
        # visualization now (the Three.js viewer_3d.html, Damage, and
        # Protection 3D views were removed at explicit request; real
        # Mapbox global terrain, fill-extrusion buildings, line roads).
        # Reuses the EXACT SAME real per-timestep depth/speed trajectories
        # already computed above for the Three.js view's own animated
        # building/road coloring (colored_footprints, road_lines) --
        # converted from the DEM's row/col grid back to real lon/lat via
        # the header's own inverse transform -- so both 3D views of the
        # same run animate identically, never disagreeing about which
        # buildings/roads are at risk at a given moment.
        try:
            _write_mapbox_scene(run_dir, log_path, bare_header, src_crs, [lon_min, lat_min, lon_max, lat_max], geom,
                                colored_footprints, road_lines, water)
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[mapbox3d] scene build failed: {e}\n")

        # Real, previously-shipped bug fixed here: `summary` is loaded from
        # summary.json (flood_model.py's own physics-only output) early in
        # this function, then enriched in memory with everything computed
        # in Python afterward (buildings_used, building_submersion,
        # estimated_building_loss, infra_risk, ...) -- but the ORIGINAL
        # file on disk was never re-written with any of it. RUN_STATE (the
        # only place the enriched dict lived) is purely in-process, so the
        # very first cache-hit `/run` request after a server restart (or
        # any OTHER code path reading summary.json fresh from disk, e.g.
        # compare-runs/batch-report) silently got back the physics-only
        # summary again -- buildings/infra/loss data genuinely computed
        # once, then quietly lost. Confirmed directly: a real completed
        # run's own summary.json on disk had zero of these keys even
        # though the live completion response had all of them. Fixed by
        # writing the real, fully-enriched summary back to the same file.
        try:
            with open(summary_path, "w") as f:
                json.dump(summary, f)
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[summary] failed to persist enriched summary.json: {e}\n")

        # Run QA (ops_agents.assess_run): rule-based check of the finished
        # result; recorded in run_qa.json and summarised on the summary.
        try:
            qa = ops_agents.qa_run_dir(run_dir, notifier=ops_agents.notify, run_id=run_id)
            if qa:
                summary["qa"] = {"verdict": qa["verdict"], "issues": len(qa["issues"]), "fallbacks": qa["fallbacks"]}
        except Exception as e:
            print(f"[ops] run QA failed for {run_id}: {e}")

        with RUN_STATE_LOCK:
            RUN_STATE[run_id].update(status="done", stage="done", summary=summary, elapsed=elapsed)


def _attach_building_depth_series(footprints, water, bare_header):
    """Adds each footprint's own real depth trajectory ("depths", one value per saved timestep) sampled at its
    centroid cell. The water arrays may be spatially downsampled (see _read_water_stack), so the full-resolution
    row/col is divided by water["stride"] first. Vectorised: one fancy-index for all buildings."""
    depth_stack = water["depth_stack"]
    nrows_, ncols_ = int(bare_header["nrows"]), int(bare_header["ncols"])
    stride_ = water.get("stride", 1)
    n_frames, d_nrows, d_ncols = depth_stack.shape
    if not footprints:
        return []
    rows = np.empty(len(footprints), dtype="int64"); cols = np.empty(len(footprints), dtype="int64")
    for i, fp in enumerate(footprints):
        pts = fp["poly_rc"]
        rows[i] = int(round(sum(p[0] for p in pts) / len(pts))); cols[i] = int(round(sum(p[1] for p in pts) / len(pts)))
    d_rows, d_cols = rows // stride_, cols // stride_
    ok = (rows >= 0) & (rows < nrows_) & (cols >= 0) & (cols < ncols_) & (d_rows >= 0) & (d_rows < d_nrows) & (d_cols >= 0) & (d_cols < d_ncols)
    series = np.zeros((len(footprints), n_frames))
    series[ok] = depth_stack[:, d_rows[ok], d_cols[ok]].T
    series_list = series.tolist()
    return [{**fp, "depths": series_list[i]} for i, fp in enumerate(footprints)]


def _build_road_lines(infra, water, bare_header, src_crs, aoi_polygon_wgs84):
    """Road segments (grid row/col paths clipped to the AOI) each carrying its road's real depth and speed series."""
    to_src2 = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    roads = infra.get("roads", [])
    nrows_, ncols_ = int(bare_header["nrows"]), int(bare_header["ncols"])
    depth_stack = water["depth_stack"] if water is not None else None
    speed_stack = water.get("speed_stack") if water is not None else None
    stride_ = water.get("stride", 1) if water is not None else 1
    d_nrows = depth_stack.shape[1] if depth_stack is not None else 0
    d_ncols = depth_stack.shape[2] if depth_stack is not None else 0
    depth_lists = speed_lists = None
    ok = None
    if depth_stack is not None and roads:
        lon_c = np.array([r["centroid"][0] for r in roads]); lat_c = np.array([r["centroid"][1] for r in roads])
        x_c, y_c = to_src2(lon_c, lat_c)
        col = ((np.asarray(x_c) - bare_header["xllcorner"]) / bare_header["cellsize"]).astype("int64")
        row = (nrows_ - 1 - (np.asarray(y_c) - bare_header["yllcorner"]) / bare_header["cellsize"]).astype("int64")
        d_row, d_col = row // stride_, col // stride_
        ok = (row >= 0) & (row < nrows_) & (col >= 0) & (col < ncols_) & (d_row >= 0) & (d_row < d_nrows) & (d_col >= 0) & (d_col < d_ncols)
        depth_lists = depth_stack[:, d_row[ok], d_col[ok]].T.tolist()
        speed_lists = speed_stack[:, d_row[ok], d_col[ok]].T.tolist() if speed_stack is not None else None
        slot = np.cumsum(ok) - 1
    road_lines = []
    all_segments = _lonlat_paths_to_segments_batch([r["geometry"] for r in roads], bare_header, src_crs, aoi_polygon_wgs84)
    for i, road in enumerate(roads):
        depths = speeds = None
        if ok is not None and ok[i]:
            depths = depth_lists[slot[i]]
            speeds = speed_lists[slot[i]] if speed_lists is not None else None
        for rows_, cols_ in all_segments[i]:
            road_lines.append({"rows": rows_, "cols": cols_, "depths": depths, "speeds": speeds})
    return road_lines


def _write_mapbox_scene(run_dir, log_path, bare_header, src_crs, bbox, geom, colored_footprints, road_lines, water):
    """Builds run_dir/mapbox_3d.html from already-computed buildings (with depth series), road lines and water."""
    lon_min, lat_min, lon_max, lat_max = bbox
    to_wgs84_mb = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform

    _t = time.time()
    scene_fps, n_fp_total = _select_scene_buildings(colored_footprints)
    if n_fp_total > len(scene_fps):
        with open(log_path, "a") as logf:
            logf.write(f"[mapbox3d] {n_fp_total:,} buildings in this run; the 3D view draws {len(scene_fps):,} "
                       f"(every building that ever floods, deepest first, then the largest dry ones) so the page stays loadable.\n")
    _round = lambda seq: None if seq is None else [round(float(v), 3) for v in seq]
    if scene_fps:
        _counts = [len(fp["poly_rc"]) for fp in scene_fps]
        _rr = np.fromiter((p[0] for fp in scene_fps for p in fp["poly_rc"]), dtype="float64", count=sum(_counts))
        _cc = np.fromiter((p[1] for fp in scene_fps for p in fp["poly_rc"]), dtype="float64", count=sum(_counts))
        _lon, _lat = _rc_to_lonlat_batch(_rr, _cc, bare_header, to_wgs84_mb)
        _ll = np.stack([_lon, _lat], axis=1).tolist(); _off = 0; mapbox_buildings = []
        for fp, n_ in zip(scene_fps, _counts):
            mapbox_buildings.append({"coords_lonlat": _ll[_off:_off + n_], "height_m": fp["height_m"], "depths": _round(fp.get("depths"))})
            _off += n_
    else:
        mapbox_buildings = []
    _rl = [rl for rl in road_lines if len(rl["rows"]) >= 2]
    if _rl:
        _rc_counts = [len(rl["rows"]) for rl in _rl]
        _rlon, _rlat = _rc_to_lonlat_batch([v for rl in _rl for v in rl["rows"]], [v for rl in _rl for v in rl["cols"]], bare_header, to_wgs84_mb)
        _rll = np.stack([_rlon, _rlat], axis=1).tolist(); _off = 0; mapbox_roads = []
        for rl, n_ in zip(_rl, _rc_counts):
            mapbox_roads.append({"coords_lonlat": _rll[_off:_off + n_], "depths": _round(rl.get("depths")), "speeds": _round(rl.get("speeds"))})
            _off += n_
    else:
        mapbox_roads = []
    _t = _tlog(log_path, f"scene geometry ({len(mapbox_buildings):,} buildings, {len(mapbox_roads):,} road segments)", _t)

    mapbox_legend = (
        "<b>Buildings</b> by CURRENT damage severity (Huizinga, animated) &middot; "
        "<b>Roads</b> shown only while wet, by CURRENT DEFRA hazard rating<br>"
        "<span style=\"color:#ece6da\">&#9632;</span> dry &nbsp;"
        "<span style=\"color:#f2d94e\">&#9632;</span> minor &nbsp;"
        "<span style=\"color:#f0883e\">&#9632;</span> &nbsp;"
        "<span style=\"color:#c81e1e\">&#9632;</span> severe<br>"
        "<b>Flood depth</b> (blue overlay, animated) &middot; terrain: real Mapbox global elevation "
        "(independent of this app's own uploaded DEM -- see mapbox3d_utils.py)")
    build_mapbox_flood_scene(os.path.join(run_dir, "mapbox_3d.html"), "Flood 3D (Mapbox)",
                              [lon_min, lat_min, lon_max, lat_max], geom, MAPBOX_TOKEN,
                              building_footprints=mapbox_buildings, road_lines=mapbox_roads,
                              water=water, legend_html=mapbox_legend)
    _tlog(log_path, "scene html written", _t)

def rebuild_run_scene(run_id):
    """Rebuilds a finished run's mapbox_3d.html with the CURRENT viewer, from the files already on disk
    (depth/velocity rasters, building_submersion.json, infra_raw.json) -- no re-simulation. Used to refresh scenes
    written by an older viewer (cached runs keep the script they were built with)."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    meta_path = os.path.join(run_dir, "run_meta.json")
    if not os.path.exists(meta_path) or not os.path.isdir(os.path.join(run_dir, "results")):
        raise RuntimeError("run not found or has no results")
    with open(meta_path) as f:
        meta = json.load(f)
    log_path = os.path.join(run_dir, "run.log")
    t0 = time.time()
    bare_header, _ = _read_ascii(os.path.join(run_dir, "dem_bare.ascii"))
    water = _read_water_stack(run_dir, rain_mm_hr=meta.get("rain_mm_hr"), duration_hr=meta.get("duration_hr"),
                               rainfall_series=meta.get("rainfall_series"))
    if water is None:
        raise RuntimeError("this run has no per-timestep results to animate")
    footprints = []
    sub_path = os.path.join(run_dir, "building_submersion.json")
    if os.path.exists(sub_path):
        with open(sub_path) as f:
            footprints = [{"poly_rc": [tuple(p) for p in ft["poly_rc"]], "height_m": ft["height_m"]}
                          for ft in json.load(f).get("features", [])]
    colored = _attach_building_depth_series(footprints, water, bare_header) if footprints else []
    infra = {}
    infra_path = os.path.join(run_dir, "infra_raw.json")
    if os.path.exists(infra_path):
        with open(infra_path) as f:
            infra = json.load(f)
    geom = meta["geometry"]
    aoi = shapely_shape(geom) if geom else None
    road_lines = _build_road_lines(infra, water, bare_header, meta["src_crs"], aoi) if infra else []
    _write_mapbox_scene(run_dir, log_path, bare_header, meta["src_crs"], meta["bbox"], geom, colored, road_lines, water)
    _tlog(log_path, "scene rebuilt (no re-simulation)", t0)
    return {"run_id": run_id, "buildings": len(colored), "road_segments": len(road_lines)}


def _pdf_response(html, filename):
    import io
    buf = io.BytesIO()
    weasyprint.HTML(string=html).write_pdf(buf)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/report/coastal.pdf", methods=["POST"])
def report_coastal_pdf():
    """PDF of the coastal result already on screen (the browser posts back {result, request})."""
    import result_report_utils as rr
    d = request.get_json(silent=True) or {}
    if not isinstance(d.get("result"), dict):
        return jsonify({"error": "no coastal result to report; run the coastal flood first"}), 400
    return _pdf_response(rr.coastal_report_html(d["result"], d.get("request") or {}, REPORT_CSS, MODEL_NAME, APP_VERSION, MODEL_TAGLINE),
                         "sailab_coastal_summary.pdf")


@app.route("/report/glof.pdf", methods=["POST"])
def report_glof_pdf():
    """PDF of the GLOF discharge result already on screen (the browser posts back {result, label})."""
    import result_report_utils as rr
    d = request.get_json(silent=True) or {}
    if not isinstance(d.get("result"), dict) or "peak_discharge_m3s" not in d["result"]:
        return jsonify({"error": "no GLOF result to report; compute a GLOF scenario first"}), 400
    return _pdf_response(rr.glof_report_html(d["result"], str(d.get("label") or ""), REPORT_CSS, MODEL_NAME, APP_VERSION, MODEL_TAGLINE),
                         "sailab_glof_summary.pdf")


@app.route("/solver-status")
def solver_status():
    """Who holds the single solver slot and who is waiting (any job type), plus queued /run ids."""
    snap = SIM_SLOT.snapshot()
    snap["queued_runs"] = list(RUN_QUEUE)
    now = time.time()
    with RUN_STATE_LOCK:
        runs = []
        for rid, st in RUN_STATE.items():
            if st.get("status") in ("queued", "running", "postprocessing"):
                runs.append({"id": rid, "kind": "run", "status": st["status"], "stage": st.get("stage"),
                             "position": (RUN_QUEUE.index(rid) + 1) if rid in RUN_QUEUE else None,
                             "age_s": round(now - st.get("queued_at", now)) if st.get("queued_at") else None})
    with CLIMATE_JOBS_LOCK:
        jobs = [{"id": jid, "kind": "job", "status": "running", "label": j.get("label")}
                for jid, j in CLIMATE_JOBS.items() if j.get("status") == "running" and jid not in JOB_CANCEL]
    snap["runs"] = runs
    snap["jobs"] = jobs
    return jsonify(snap)


@app.route("/runs/<run_id>/validate-observed", methods=["POST"])
def run_validate_observed(run_id):
    """Scores a finished run's modelled wet extent against an uploaded OBSERVED flood extent (polygons or GeoTIFF)."""
    import observed_validation_utils as ov
    run_dir = os.path.join(FIS_CACHE, run_id)
    if "/" in run_id or ".." in run_id or not os.path.isdir(run_dir):
        abort(404)
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    try:
        with open(os.path.join(run_dir, "run_meta.json")) as mf:
            meta = json.load(mf)
        header, max_depth = _read_ascii(os.path.join(run_dir, "results", "res.max"))
        _, dem = _read_ascii(os.path.join(run_dir, "dem_bare.ascii"))
        if dem.shape != max_depth.shape:
            return jsonify({"error": "this run's saved terrain and result grids differ in size; cannot validate"}), 400
        factor = 1.0
        try:
            with open(os.path.join(run_dir, "summary.json")) as sf:
                sm = json.load(sf)
            if sm.get("acc_depth_correction_applied"):
                factor = float(sm.get("acc_depth_correction_factor", 1.0)) or 1.0
        except (OSError, ValueError):
            pass
        thr = float(request.form.get("wet_threshold_m", 0.05))
        valid = dem != -9999
        model_wet = (max_depth / factor) > thr
        path = os.path.join(UPLOADS, f"{uuid.uuid4().hex}_{os.path.basename(f.filename or 'observed')}")
        f.save(path)
        try:
            obs_wet, covered = ov.observed_mask_on_grid(path, header, meta["src_crs"])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        scored = valid & covered
        res = ov.score_extent(model_wet, obs_wet, scored, (header["cellsize"] ** 2) / 1e6)
        res["wet_threshold_m"] = thr
        res["observed_cells_outside_run_area"] = int((obs_wet & ~valid).sum())
        if res["observed_wet_km2"] == 0:
            res["warning"] = "No observed flooding falls inside this run's area; check the file's location and coordinate system."
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": f"could not validate: {e}"}), 400



# --------------------------------------------------------------------------
# Custom DEM upload, run files (save / load), and roughness + infiltration calibration
# --------------------------------------------------------------------------
CALIBRATION_CACHE = os.path.join(BASE_DIR, "fis_calibration_cache")
os.makedirs(CALIBRATION_CACHE, exist_ok=True)


@app.route("/area/upload-dem", methods=["POST"])
def area_upload_dem():
    """Accepts a user's own DEM (GeoTIFF) and stores a solver-ready copy; the returned dem_id goes into /run."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    path = os.path.join(UPLOADS, f"{uuid.uuid4().hex}_{os.path.basename(f.filename or 'dem.tif')}")
    f.save(path)
    try:
        info = custom_dem_utils.prepare_upload(path)
    except custom_dem_utils.CustomDemError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"could not process this elevation file: {e}"}), 400
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return jsonify(info)


@app.route("/runs/<run_id>/runfile")
def run_runfile(run_id):
    """Downloads the run's inputs as a .sailab.json run file (reload it with 'Load run file')."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    if "/" in run_id or ".." in run_id or not os.path.isdir(run_dir):
        abort(404)
    rf_path = os.path.join(run_dir, "runfile.json")
    if os.path.exists(rf_path):
        with open(rf_path) as f:
            doc = json.load(f)
    else:
        try:
            with open(os.path.join(run_dir, "run_meta.json")) as f:
                doc = runfile_utils.runfile_from_meta(json.load(f), APP_VERSION, run_id)
        except OSError:
            abort(404)
    resp = Response(json.dumps(doc, indent=1), mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="sailab_run_{run_id[:8]}.sailab.json"'
    return resp


@app.route("/runfile/parse", methods=["POST"])
def runfile_parse():
    """Validates an uploaded run file and returns its request, plus a note when its custom DEM is missing here."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    try:
        req = runfile_utils.parse_runfile(f.read().decode("utf-8", "replace"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    warnings = []
    cid = req.get("custom_dem_id")
    if cid and custom_dem_utils.path_for(cid) is None:
        warnings.append("This run used an uploaded elevation model that is not on this server. Upload the same DEM "
                        "file again before running (its content id will match).")
    return jsonify({"request": req, "warnings": warnings})


def execute_calibration(job_id, run_id, obs_path, max_evals):
    """Real search for the roughness multiplier and infiltration rate that best reproduce an observed flood extent."""
    scratch = os.path.join(CALIBRATION_CACHE, job_id)
    os.makedirs(scratch, exist_ok=True)
    try:
        run_dir = os.path.join(FIS_CACHE, run_id)
        with open(os.path.join(run_dir, "run_meta.json")) as f:
            meta = json.load(f)
        with open(os.path.join(run_dir, "summary.json")) as f:
            summary = json.load(f)
        header = meta["header"]
        src_crs = meta["src_crs"]
        dem_path = os.path.join(run_dir, "aoi.dem.ascii")
        _, dem = _read_ascii(dem_path)
        valid = dem != -9999
        obs_wet, covered = ov_mod.observed_mask_on_grid(obs_path, header, src_crs)
        scored = valid & covered
        if not (obs_wet & scored).any():
            raise ValueError("no observed flooding falls inside this run's area; check the file's location and coordinate system")
        factor = 1.0
        if summary.get("acc_depth_correction_applied"):
            factor = float(summary.get("acc_depth_correction_factor", 1.0)) or 1.0
        log_path = os.path.join(scratch, "run.log")
        open(log_path, "w").close()
        run_params = _protection_run_params_and_boundary(run_dir, meta, header, src_crs, dem, log_path)
        run_params["friction"] = float(meta.get("friction", 0.05)) * float(meta.get("manning_scale", 1.0) or 1.0)
        if meta.get("rainfall_series"):
            rs = meta["rainfall_series"]
            sp = os.path.join(scratch, "rain_series.json")
            with open(sp, "w") as f:
                json.dump({"times_hr": rs["times_hr"], "rates_mm_hr": rs["rates_mm_hr"], "label": rs.get("label", "observed")}, f)
            run_params["rain_series_path"] = sp
        ctx = {"run_params": run_params, "venv_py": VENV_PY, "flood_model_path": FLOOD_MODEL, "dem_path": dem_path,
               "sim_slot": SIM_SLOT, "scratch_dir": scratch, "obs_wet": obs_wet, "scored": scored,
               "acc_factor": factor, "cell_km2": (header["cellsize"] ** 2) / 1e6, "seed_key": run_id}

        def progress(i, total, sc):
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "running", "trial": i, "max_evals": total,
                                        "last_csi": (sc or {}).get("CSI")}

        res = calibration_utils.calibrate(ctx, max_evals, progress_cb=progress)
        if res is None:
            raise ValueError("no trial run produced a result; see calibration_errors.log in the job folder")
        prev = float(meta.get("manning_scale", 1.0) or 1.0)
        res["apply"] = {"manning_scale": round(prev * res["best"]["manning_scale"], 4),
                        "infiltration_mm_hr": res["best"]["infiltration_mm_hr"]}
        res["source_run_id"] = run_id
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "done", "result": res}
    except Exception as e:
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "failed", "error": str(e)}
    finally:
        try:
            os.remove(obs_path)
        except OSError:
            pass


@app.route("/runs/<run_id>/calibrate", methods=["POST"])
def run_calibrate(run_id):
    run_dir = os.path.join(FIS_CACHE, run_id)
    if "/" in run_id or ".." in run_id or not os.path.isfile(os.path.join(run_dir, "summary.json")):
        return jsonify({"error": "run not found or not finished"}), 404
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "upload an observed flood extent (polygons or GeoTIFF) first"}), 400
    try:
        max_evals = max(8, min(60, int(request.form.get("max_evals", 24))))
    except ValueError:
        max_evals = 24
    obs_path = os.path.join(UPLOADS, f"{uuid.uuid4().hex}_{os.path.basename(f.filename or 'observed')}")
    f.save(obs_path)
    job_id = uuid.uuid4().hex
    with CLIMATE_JOBS_LOCK:
        CLIMATE_JOBS[job_id] = {"status": "running", "trial": 0, "max_evals": max_evals + 1}
    threading.Thread(target=execute_calibration, args=(job_id, run_id, obs_path, max_evals), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/runs/<run_id>/rebuild-scene", methods=["POST"])
def run_rebuild_scene(run_id):
    if not os.path.isdir(os.path.join(FIS_CACHE, run_id)) or "/" in run_id or ".." in run_id:
        abort(404)
    return jsonify({"job_id": _start_climate_job(rebuild_run_scene, run_id=run_id)})


def _tlog(log_path, label, t0):
    """Appends '[timing] <label>: <seconds>' to the run log so a slow post-processing step is visible, not silent."""
    try:
        with open(log_path, "a") as logf:
            logf.write(f"[timing] {label}: {time.time() - t0:.1f}s\n")
    except OSError:
        pass
    return time.time()


MAX_SCENE_BUILDINGS = 60000  # buildings drawn in the 3D scene; more than this makes the page too large to load


def _rc_to_lonlat_batch(rows, cols, header, to_wgs84):
    """Vectorised grid (row, col) -> (lon, lat) for arrays of points (one pyproj call for all of them)."""
    rows = np.asarray(rows, dtype="float64"); cols = np.asarray(cols, dtype="float64")
    x = header["xllcorner"] + cols * header["cellsize"]
    y = header["yllcorner"] + (int(header["nrows"]) - rows) * header["cellsize"]
    lon, lat = to_wgs84(x, y)
    return np.asarray(lon), np.asarray(lat)


def _select_scene_buildings(colored_footprints, cap=MAX_SCENE_BUILDINGS, wet_threshold=0.03):
    """Chooses which buildings go into the 3D scene. Every building that ever floods is kept (deepest first) up to
    the cap, then the largest dry buildings fill the rest. Dry buildings carry no depth series. Returns
    (selected, n_total)."""
    n_total = len(colored_footprints)
    wet, dry = [], []
    for fp in colored_footprints:
        d = fp.get("depths")
        mx = max(d) if d else 0.0
        (wet if mx > wet_threshold else dry).append((mx, fp))
    wet.sort(key=lambda t: -t[0])
    chosen = [fp for _, fp in wet[:cap]]
    room = cap - len(chosen)
    if room > 0 and dry:
        def _area(fp):
            pts = fp["poly_rc"]; a = 0.0
            for (r1, c1), (r2, c2) in zip(pts, pts[1:] + pts[:1]):
                a += r1 * c2 - r2 * c1
            return abs(a)
        dry_sorted = sorted((fp for _, fp in dry), key=_area, reverse=True)[:room]
        chosen += [{**fp, "depths": None} for fp in dry_sorted]
    return chosen, n_total


def _pick_wettest_cell_per_block(depth_stack, stride):
    """For each (stride x stride) spatial block (independently per frame),
    returns the (row, col) of the real cell with that block's own highest
    depth -- used to downsample a huge domain's animation payload without
    ever silently dropping a real, if sparse, flooded cell the way naive
    corner-sampling ([::stride,::stride]) can (see _read_water_stack's own
    comment for the real, live-confirmed bug this fixes). Returns two
    (n_frames, out_rows, out_cols) integer index arrays, broadcastable
    against depth_stack's own first axis for fancy indexing."""
    n_frames, nrows, ncols = depth_stack.shape
    pad_r = (-nrows) % stride
    pad_c = (-ncols) % stride
    padded = depth_stack
    if pad_r or pad_c:
        padded = np.pad(depth_stack, ((0, 0), (0, pad_r), (0, pad_c)), constant_values=-np.inf)
    out_rows = padded.shape[1] // stride
    out_cols = padded.shape[2] // stride
    blocks = padded.reshape(n_frames, out_rows, stride, out_cols, stride)
    blocks = blocks.transpose(0, 1, 3, 2, 4).reshape(n_frames, out_rows, out_cols, stride * stride)
    flat_argmax = blocks.argmax(axis=-1)
    local_row = flat_argmax // stride
    local_col = flat_argmax % stride
    row_base = (np.arange(out_rows) * stride)[None, :, None]
    col_base = (np.arange(out_cols) * stride)[None, None, :]
    row_idx = np.clip(row_base + local_row, 0, nrows - 1)
    col_idx = np.clip(col_base + local_col, 0, ncols - 1)
    return row_idx, col_idx


def _read_water_stack(run_dir, save_int_min_default=10.0, rain_mm_hr=None, duration_hr=None,
                       rainfall_series=None):
    """Reads a finished run's per-timestep depth rasters (and the solver's own velocity rasters, de-staggered
    onto the depth grid) into the arrays the Mapbox 3D scene animates.

    Streams ONE frame at a time and downsamples each frame as soon as it is read, instead of materialising the
    full (frames x rows x cols) float64 stacks first. The old all-at-once version needed about 5 GB and took
    680 s on a 2.35M-cell, 43-frame run (the cause of a "post-processing stalls for hours" report); this
    version does the same work frame by frame in seconds and gives identical results.

    Real, load-bearing details kept from the earlier version:
    * the ACC steep-terrain depth correction (summary.json) is applied to every frame so the animation agrees
      with the reported peak depth;
    * `max_depth` is the true full-resolution peak, taken BEFORE any downsampling;
    * when frames x rows x cols exceeds TARGET_EMBEDDED_ELEMENTS the grid is downsampled by an integer stride,
      keeping each block's own wettest cell (never dropping a real, sparse flooded cell) and sampling vx/vy/speed
      at that same cell;
    * Vx has one extra column and Vy one extra row on the ACC staggered grid and are averaged back to cell
      centres; DG2 velocity is already cell-centred."""
    results_dir = os.path.join(run_dir, "results")
    wd_files = sorted(glob.glob(os.path.join(results_dir, "res-*.wd")))
    if not wd_files:
        return None
    n_frames = len(wd_files)

    acc_correction_factor = 1.0
    summary_path = os.path.join(run_dir, "summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path) as f:
                _summary = json.load(f)
            if _summary.get("acc_depth_correction_applied"):
                acc_correction_factor = float(_summary.get("acc_depth_correction_factor", 1.0))
        except Exception:
            acc_correction_factor = 1.0

    vx_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vx")))
    vy_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vy")))
    have_vel = len(vx_files) == n_frames and len(vy_files) == n_frames

    def _clean(fp):
        d = _read_ascii(fp)[1]
        return np.where(d == -9999, 0.0, d)

    wd_header, d0 = _read_ascii(wd_files[0])
    src_nrows, src_ncols = d0.shape
    total_elements = n_frames * src_nrows * src_ncols
    TARGET_EMBEDDED_ELEMENTS = 5_000_000
    stride = 1
    if total_elements > TARGET_EMBEDDED_ELEMENTS:
        stride = int(np.ceil(np.sqrt(total_elements / TARGET_EMBEDDED_ELEMENTS)))
    out_dtype = np.float64 if stride == 1 else np.float32
    out_r, out_c = (src_nrows, src_ncols) if stride == 1 else (-(-src_nrows // stride), -(-src_ncols // stride))

    depth_out = np.zeros((n_frames, out_r, out_c), dtype=out_dtype)
    speed_out = vx_out = vy_out = None
    if have_vel:
        speed_out = np.zeros_like(depth_out); vx_out = np.zeros_like(depth_out); vy_out = np.zeros_like(depth_out)
    real_max_depth = 0.0

    for i, fp in enumerate(wd_files):
        d = np.where(d0 == -9999, 0.0, d0) if i == 0 else _clean(fp)
        if acc_correction_factor != 1.0:
            d = d / acc_correction_factor
        real_max_depth = max(real_max_depth, float(d.max()))
        vx_c = vy_c = None
        if have_vel:
            try:
                vx_raw, vy_raw = _clean(vx_files[i]), _clean(vy_files[i])
                if vx_raw.shape == d.shape and vy_raw.shape == d.shape:
                    vx_c, vy_c = vx_raw, vy_raw  # DG2: already cell-centred
                else:
                    vx_c = 0.5 * (vx_raw[:, :-1] + vx_raw[:, 1:])   # ACC: staggered -> cell-centred
                    vy_c = 0.5 * (vy_raw[:-1, :] + vy_raw[1:, :])
                if vx_c.shape != d.shape or vy_c.shape != d.shape:
                    raise ValueError("velocity grid does not match depth grid")
            except Exception:
                have_vel = False
                speed_out = vx_out = vy_out = None
                vx_c = vy_c = None
        if stride == 1:
            depth_out[i] = d
            if have_vel:
                vx_out[i], vy_out[i] = vx_c, vy_c
                speed_out[i] = np.sqrt(vx_c ** 2 + vy_c ** 2)
        else:
            ri, ci = _pick_wettest_cell_per_block(d[None], stride)
            ri, ci = ri[0], ci[0]
            depth_out[i] = d[ri, ci]
            if have_vel:
                vxs, vys = vx_c[ri, ci], vy_c[ri, ci]
                vx_out[i], vy_out[i] = vxs, vys
                speed_out[i] = np.sqrt(vxs ** 2 + vys ** 2)

    save_int_min = save_int_min_default
    par_files = glob.glob(os.path.join(run_dir, "*.par"))
    if par_files:
        with open(par_files[0]) as f:
            for line in f:
                if line.strip().startswith("saveint"):
                    save_int_min = float(line.split()[1]) / 60.0
                    break

    if stride > 1:
        with open(os.path.join(run_dir, "run.log"), "a") as logf:
            logf.write(f"[mapbox3d] real domain too large to embed at full resolution for animation "
                       f"({n_frames} frames x {src_nrows}x{src_ncols} cells = {total_elements:,} elements) -- "
                       f"downsampled by a real stride of {stride} for the ANIMATED view only, keeping each "
                       f"block's own wettest real cell so real (if sparse) flooding is never silently dropped "
                       f"({n_frames} frames x {out_r}x{out_c} cells); "
                       f"the scientific summary/report figures are unaffected, read from the full-resolution data.\n")

    # Ground elevation of the run's own bare DEM on the same (possibly strided) grid, for the 3D viewer's
    # click-to-pin probe ("ground here") -- the physics terrain, not Mapbox's display terrain.
    ground = None
    try:
        dem_path = os.path.join(run_dir, "dem_bare.ascii")
        if os.path.exists(dem_path):
            _gh, g = _read_ascii(dem_path)
            g = np.where(g == -9999, np.nan, g)[::stride, ::stride]
            g = g[:out_r, :out_c]
            if g.shape == depth_out.shape[1:]:
                ground = g
    except Exception:
        ground = None
    cellsize_m = float(wd_header["cellsize"]) * stride if wd_header else None

    return {"depth_stack": depth_out, "speed_stack": speed_out,
            "vx_stack": vx_out, "vy_stack": vy_out, "save_int_min": save_int_min,
            "ground": ground, "cellsize_m": cellsize_m,
            "dry_thresh": 0.03, "max_depth": real_max_depth,
            # Any caller that samples these arrays at a row/col computed from the FULL-resolution DEM header
            # (building/road risk sampling) must divide that row/col by this stride first. Always 1 for
            # normal-sized runs.
            "stride": stride,
            "rain_mm_hr": rain_mm_hr, "duration_hr": duration_hr,
            # Real uploaded rainfall series metadata (see rainfall_csv_utils.py) -- real clock time and
            # rain intensity in the 3D view. None for a manually entered triangular storm.
            "start_time_iso": (rainfall_series or {}).get("start_time_iso"),
            "rain_times_hr": (rainfall_series or {}).get("times_hr"),
            "rain_rates_mm_hr": (rainfall_series or {}).get("rates_mm_hr")}


def _scenario_building_loss(baseline_dir, header, scenario_depth):
    """Real before/after estimated building loss (PKR) for an NbS or
    protection scenario -- reuses the baseline run's own already-saved
    real building footprints (building_submersion.json's "features":
    poly_rc/height_m don't change between baseline and scenario, since
    it's the SAME real buildings; only which ones are wet, and how
    deeply, changes). Re-classifies those same footprints against the
    scenario's own real depth grid (assess_building_submersion_risk only
    reads poly_rc/height_m from its input, so the baseline's own output
    features are valid input here too -- no second buildings fetch, no
    duplicated geometry). Returns (loss_before, loss_after,
    loss_avoided_pkr) or (None, None, None) if the baseline run has no
    saved building data (e.g. buildings were disabled for that run)."""
    sub_path = os.path.join(baseline_dir, "building_submersion.json")
    if not os.path.exists(sub_path):
        return None, None, None
    try:
        with open(sub_path) as f:
            baseline_sub = json.load(f)
        baseline_features = baseline_sub["features"]
        scenario_sub = assess_building_submersion_risk(baseline_features, scenario_depth, header)
        cellsize_m = float(header["cellsize"])
        loss_before = loss_utils.estimate_building_loss_pkr(baseline_features, cellsize_m)
        loss_after = loss_utils.estimate_building_loss_pkr(scenario_sub["features"], cellsize_m)
        loss_avoided = round(loss_before["total_loss_pkr"] - loss_after["total_loss_pkr"], 2)
        return loss_before, loss_after, loss_avoided
    except Exception:
        return None, None, None


def execute_nbs_run(job_id, baseline_run_id, intervention_key, pathway):
    """Real nature-based-solution scenario: takes an already-finished
    Tier 3 baseline run, changes real ESA WorldCover land cover across the
    WHOLE AOI (a deliberate scope simplification -- no separate hand-drawn
    sub-area tool, unlike the earlier removed grey-infrastructure
    Protection scenario's line-drawing; "what if this whole area were
    reforested" is still a real, useful question), re-derives real
    per-cell Manning's friction AND a real area-weighted mean infiltration
    rate from the changed land cover (see nbs_utils.py for why infiltration
    can only be a single mean, not per-cell), re-runs the EXACT SAME real
    physics (same conditioned DEM -- buildings/channel included -- same
    rain/duration/AMC/inflow/solver as baseline) and does a real cell-by-
    cell before/after comparison."""
    try:
        baseline_dir = os.path.join(FIS_CACHE, baseline_run_id)
        with open(os.path.join(baseline_dir, "run_meta.json")) as f:
            meta = json.load(f)
        with open(os.path.join(baseline_dir, "summary.json")) as f:
            baseline_summary = json.load(f)
        lulc = np.load(os.path.join(baseline_dir, "lulc.npy"))
        header = meta["header"]
        _, baseline_depth = _read_ascii(os.path.join(baseline_dir, "results", "res.max"))
        valid_mask = baseline_depth != header.get("nodata_value", -9999)

        modified_lulc = nbs_utils.apply_nbs_scenario(
            lulc, np.ones_like(lulc, dtype=bool), intervention_key, pathway)
        manning_grid = nbs_utils.lulc_to_manning_grid(modified_lulc)
        new_infiltration_mm_hr = nbs_utils.mean_infiltration_mm_hr(modified_lulc, valid_mask)

        scenario_dir = os.path.join(NBS_CACHE, job_id)
        os.makedirs(scenario_dir, exist_ok=True)
        manning_path = os.path.join(scenario_dir, "manning.ascii")
        _write_ascii(manning_path, header, manning_grid)

        cmd = [VENV_PY, FLOOD_MODEL, "--dem", os.path.join(baseline_dir, "aoi.dem.ascii"),
               "--rain-mm-hr", str(meta["rain_mm_hr"]), "--duration-hr", str(meta["duration_hr"]),
               "--friction", str(meta["friction"]), "--cfl", str(meta["cfl"]),
               "--infiltration-mm-hr", str(new_infiltration_mm_hr), "--amc", str(meta["amc"]),
               "--manning-file", manning_path,
               "--site-name", "NbS scenario", "--workdir", scenario_dir]
        log_path = os.path.join(scenario_dir, "run.log")
        with open(log_path, "w"):
            pass  # so _resolve_boundary_item has a real file to append warnings to
        baseline_inflows = meta.get("inflows", [])
        baseline_outflows = meta.get("outflows", [])
        if baseline_inflows or baseline_outflows:
            _, baseline_dem = _read_ascii(os.path.join(baseline_dir, "aoi.dem.ascii"))
            b_inflows, b_outflows = [], []
            for item in baseline_inflows:
                resolved = _resolve_boundary_item(header, baseline_dem, header["src_crs"], item, log_path)
                if resolved is None:
                    continue
                resolved.update({"peak_m3s": item.get("peak_m3s", 0.0), "duration_hr": item.get("duration_hr", 0.0),
                                  "rise_frac": item.get("rise_frac", 0.5),
                              "series": item.get("series")})
                b_inflows.append(resolved)
            for item in baseline_outflows:
                resolved = _resolve_boundary_item(header, baseline_dem, header["src_crs"], item, log_path)
                if resolved is not None:
                    b_outflows.append(resolved)
            if b_inflows or b_outflows:
                boundary_path = os.path.join(scenario_dir, "boundary.json")
                with open(boundary_path, "w") as bf:
                    json.dump({"inflows": b_inflows, "outflows": b_outflows}, bf)
                cmd += ["--boundary-json", boundary_path]

        with SIM_SLOT.labelled("NbS scenario"), open(log_path, "a") as logf:
            proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed", "error": "flood model run failed -- see run.log"}
            return

        _, scenario_depth = _read_ascii(os.path.join(scenario_dir, "results", "res.max"))
        comparison = nbs_utils.compare_flood_results(baseline_depth, scenario_depth, valid_mask)
        loss_before, loss_after, loss_avoided_pkr = _scenario_building_loss(baseline_dir, header, scenario_depth)

        # Real 3-category diff array for the map view (see NBS_DIFF_COLOR_STOPS):
        # 0=no change, 1/3=protected (was wet, now dry), 2/3=displaced (was
        # dry, now wet), 1.0=still flooded either way -- saved to disk so
        # the map route doesn't need to recompute or re-run anything.
        base_wet = (baseline_depth > 0.05) & valid_mask
        scen_wet = (scenario_depth > 0.05) & valid_mask
        diff = np.zeros_like(baseline_depth, dtype="float32")
        diff[base_wet & ~scen_wet] = 1.0 / 3.0       # protected
        diff[~base_wet & scen_wet] = 2.0 / 3.0       # displaced
        diff[base_wet & scen_wet] = 1.0              # still flooded
        np.save(os.path.join(scenario_dir, "diff.npy"), diff)
        with open(os.path.join(scenario_dir, "bbox_geom.json"), "w") as f:
            json.dump({"bbox": meta["bbox"], "geometry": meta["geometry"]}, f)

        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "done", "result": {
                "intervention": nbs_utils.NBS_INTERVENTIONS[intervention_key]["label"],
                "pathway": pathway,
                "new_infiltration_mm_hr_baseline_amc2": round(new_infiltration_mm_hr, 2),
                "comparison": comparison,
                "baseline_flooded_area_km2": baseline_summary.get("flooded_area_km2"),
                "loss_before": loss_before, "loss_after": loss_after, "loss_avoided_pkr": loss_avoided_pkr,
                "job_id": job_id,
            }}
    except Exception as e:
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "failed", "error": str(e)}


@app.route("/area/nbs-scenario", methods=["POST"])
def area_nbs_scenario():
    """Starts a real nature-based-solutions scenario re-run against an
    already-finished Tier 3 baseline run (see execute_nbs_run)."""
    body = request.json
    baseline_run_id = body.get("run_id")
    intervention_key = body.get("intervention")
    pathway = body.get("pathway", "active")
    if not baseline_run_id or not os.path.exists(os.path.join(FIS_CACHE, baseline_run_id, "summary.json")):
        return jsonify({"error": "run the Tier 3 flood model first"}), 400
    if intervention_key not in nbs_utils.NBS_INTERVENTIONS:
        return jsonify({"error": f"unknown intervention '{intervention_key}'"}), 400

    job_id = uuid.uuid4().hex
    with CLIMATE_JOBS_LOCK:
        CLIMATE_JOBS[job_id] = {"status": "running", "label": "Nature-based scenario"}
    threading.Thread(target=execute_nbs_run, args=(job_id, baseline_run_id, intervention_key, pathway),
                      daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/nbs/<job_id>/mapbox3d")
def nbs_mapbox3d(job_id):
    """Real NbS before/after diff overlay on the map (see NBS_DIFF_COLOR_STOPS
    for what protected/displaced/still-flooded map to) -- same canvas-
    overlay path as every other map view in this app."""
    scenario_dir = os.path.join(NBS_CACHE, job_id)
    diff_path = os.path.join(scenario_dir, "diff.npy")
    bbox_path = os.path.join(scenario_dir, "bbox_geom.json")
    if not (os.path.exists(diff_path) and os.path.exists(bbox_path)):
        return Response("<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>"
                        "No NbS scenario result found for this id.</p>", mimetype="text/html")
    diff = np.load(diff_path)
    with open(bbox_path) as f:
        bg = json.load(f)

    water = {"depth_stack": diff[np.newaxis, :, :], "save_int_min": 60.0,
             "dry_thresh": 0.1, "max_depth": 1.0, "color_stops": NBS_DIFF_COLOR_STOPS}
    html_path = os.path.join(AOI3D_CACHE, f"nbsdiff_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "NbS scenario diff (Mapbox)", bg["bbox"], bg["geometry"], MAPBOX_TOKEN, water=water,
            legend_html="<b>Nature-based solution: before/after</b><br>"
                        "green=protected (now dry) &middot; red=displaced (newly wet) &middot; blue=still flooded")
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


# Every real (intervention, pathway) combination NBS_INTERVENTIONS supports
# -- small and discrete enough (3 interventions x 2 pathways = 6) to just
# evaluate ALL of them with a real re-run each, rather than a search
# algorithm (mystic's real value, as used for protection-structure
# placement above, is searching a large CONTINUOUS geometry space --
# there's no equivalent continuous space here to search).
NBS_RECOMMEND_COMBOS = [(k, p) for k in nbs_utils.NBS_INTERVENTIONS for p in ("active", "passive")]


def execute_nbs_recommend(job_id, baseline_run_id):
    """Real auto-recommendation: runs EVERY real (intervention, pathway)
    combination against the baseline (execute_nbs_run itself, called
    synchronously here rather than backgrounded per-trial, since this
    function already runs in its own background thread) and reports them
    ranked by the same real score already used for protection-structure
    placement -- real estimated monetary loss avoided (PKR) when the
    baseline run has real building data on hand, falling back to
    -reduction_pct + 0.5*displacement_pct otherwise -- "best" is
    whichever real trial's own re-run actually performed best, never a
    guess. Only the winning trial's own real scenario directory (and
    hence its real /nbs/<id>/mapbox3d view) is kept; every other trial's
    scratch files are discarded once scored."""
    try:
        baseline_dir = os.path.join(FIS_CACHE, baseline_run_id)
        if not os.path.exists(os.path.join(baseline_dir, "summary.json")):
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed", "error": "run the Tier 3 flood model first"}
            return

        trials = []
        total = len(NBS_RECOMMEND_COMBOS)
        for i, (intervention_key, pathway) in enumerate(NBS_RECOMMEND_COMBOS):
            if job_id in JOB_CANCEL:
                return
            trial_job_id = f"{job_id}_t{i}"
            execute_nbs_run(trial_job_id, baseline_run_id, intervention_key, pathway)
            with CLIMATE_JOBS_LOCK:
                trial_result = CLIMATE_JOBS.get(trial_job_id, {})
            if trial_result.get("status") == "done":
                c = trial_result["result"]["comparison"]
                loss_before = trial_result["result"].get("loss_before")
                loss_avoided_pkr = trial_result["result"].get("loss_avoided_pkr")
                # Real ranking by estimated monetary loss avoided when the
                # baseline run has real building data -- same principle as
                # protection_optimizer.py's own search objective: which
                # real option saves the most real value, not just the most
                # raw area regardless of what's on it. Falls back to the
                # original cell-based score when no real building loss
                # figure is available (e.g. buildings disabled).
                if loss_before and loss_before.get("total_loss_pkr", 0) > 0:
                    reduction_pct_of_loss = 100.0 * loss_avoided_pkr / loss_before["total_loss_pkr"]
                    score = -reduction_pct_of_loss + 0.5 * c["displacement_pct"]
                else:
                    score = -c["reduction_pct"] + 0.5 * c["displacement_pct"]
                trials.append({"intervention": intervention_key, "pathway": pathway, "comparison": c,
                                "new_infiltration_mm_hr_baseline_amc2":
                                    trial_result["result"]["new_infiltration_mm_hr_baseline_amc2"],
                                "loss_avoided_pkr": loss_avoided_pkr,
                                "score": score, "trial_job_id": trial_job_id})
            else:
                shutil.rmtree(os.path.join(NBS_CACHE, trial_job_id), ignore_errors=True)
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "running", "trial": i + 1, "max_evals": total}

        if not trials:
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed", "error": "every real (intervention, pathway) trial failed"}
            return

        best = min(trials, key=lambda t: t["score"])
        for t in trials:
            if t["trial_job_id"] != best["trial_job_id"]:
                shutil.rmtree(os.path.join(NBS_CACHE, t["trial_job_id"]), ignore_errors=True)

        ranked = sorted(trials, key=lambda t: t["score"])
        for t in ranked:
            t["label"] = nbs_utils.NBS_INTERVENTIONS[t["intervention"]]["label"]
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "done", "result": {"ranked": ranked, "best_job_id": best["trial_job_id"]}}
    except Exception as e:
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "failed", "error": str(e)}


@app.route("/area/nbs-recommend", methods=["POST"])
def area_nbs_recommend():
    """Starts a real auto-recommendation search across every NbS
    (intervention, pathway) combination (see execute_nbs_recommend)."""
    body = request.json
    baseline_run_id = body.get("run_id")
    if not baseline_run_id or not os.path.exists(os.path.join(FIS_CACHE, baseline_run_id, "summary.json")):
        return jsonify({"error": "run the Tier 3 flood model first"}), 400

    job_id = uuid.uuid4().hex
    with CLIMATE_JOBS_LOCK:
        CLIMATE_JOBS[job_id] = {"status": "running", "trial": 0, "max_evals": len(NBS_RECOMMEND_COMBOS)}
    threading.Thread(target=execute_nbs_recommend, args=(job_id, baseline_run_id), daemon=True).start()
    return jsonify({"job_id": job_id})


# --------------------------------------------------------------------------
# Grey-infrastructure protection scenarios (dam/barrage/canal/drainage) --
# rebuilt against today's simplified NbS-scenario job pattern (see
# protection_utils.py's own docstring for why the earlier Tab 6 version
# isn't being restored verbatim). A manual scenario re-runs the real
# physics once with the user's own drawn structure(s); the auto-optimize
# path (protection_optimizer.py) searches placements via real repeated
# LISFLOOD-FP re-runs, then hands its winning structures to the exact
# same manual-scenario function below for the final answer.
# --------------------------------------------------------------------------
def _protection_run_params_and_boundary(baseline_dir, meta, header, src_crs, baseline_dem, log_path):
    """Real shared setup for both a manual protection run and every trial
    the auto-optimizer scores: the baseline's own real rain/friction/AMC/
    Manning's-friction-grid/boundary-condition params, unchanged --
    a protection scenario changes terrain/structures, not the storm or
    land cover, so the baseline's own physics inputs apply exactly."""
    manning_path = os.path.join(baseline_dir, "manning.ascii")
    run_params = {
        "rain_mm_hr": meta["rain_mm_hr"], "duration_hr": meta["duration_hr"],
        "friction": meta["friction"], "cfl": meta["cfl"],
        "infiltration_mm_hr": meta.get("infiltration_mm_hr", 0.0), "amc": meta.get("amc", 50.0),
        "manning_path": manning_path if os.path.exists(manning_path) else None,
        "boundary_path": None,
    }
    baseline_inflows = meta.get("inflows", [])
    baseline_outflows = meta.get("outflows", [])
    if baseline_inflows or baseline_outflows:
        b_inflows, b_outflows = [], []
        for item in baseline_inflows:
            resolved = _resolve_boundary_item(header, baseline_dem, src_crs, item, log_path)
            if resolved is None:
                continue
            resolved.update({"peak_m3s": item.get("peak_m3s", 0.0), "duration_hr": item.get("duration_hr", 0.0),
                              "rise_frac": item.get("rise_frac", 0.5),
                              "series": item.get("series")})
            b_inflows.append(resolved)
        for item in baseline_outflows:
            resolved = _resolve_boundary_item(header, baseline_dem, src_crs, item, log_path)
            if resolved is not None:
                b_outflows.append(resolved)
        if b_inflows or b_outflows:
            boundary_path = os.path.join(os.path.dirname(log_path), "boundary.json")
            with open(boundary_path, "w") as bf:
                json.dump({"inflows": b_inflows, "outflows": b_outflows}, bf)
            run_params["boundary_path"] = boundary_path
    return run_params


def execute_protection_run(job_id, baseline_run_id, structures, extra_buildings=None):
    """Real manual (or auto-optimizer-final) protection scenario: burns
    the given structures into the baseline's own conditioned DEM
    (protection_utils.apply_structures_to_dem), re-runs the real physics
    with the baseline's own storm/friction/boundary params, and does a
    real cell-by-cell before/after comparison -- the exact same
    nbs_utils.compare_flood_results already used for NbS scenarios."""
    try:
        baseline_dir = os.path.join(FIS_CACHE, baseline_run_id)
        with open(os.path.join(baseline_dir, "run_meta.json")) as f:
            meta = json.load(f)
        with open(os.path.join(baseline_dir, "summary.json")) as f:
            baseline_summary = json.load(f)
        header = meta["header"]
        src_crs = meta["src_crs"]
        _, baseline_dem = _read_ascii(os.path.join(baseline_dir, "aoi.dem.ascii"))
        _, baseline_depth = _read_ascii(os.path.join(baseline_dir, "results", "res.max"))
        valid_mask = baseline_dem != header.get("nodata_value", -9999)

        affected = protection_utils.structures_affected_cells(baseline_dem, header, src_crs, structures) if structures else 1
        if affected == 0:
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed",
                                         "error": "structure(s) fall entirely outside the AOI's valid terrain"}
            return

        modified_dem = protection_utils.apply_structures_to_dem(baseline_dem, header, src_crs, structures)
        scenario_dir = os.path.join(PROTECT_CACHE, job_id)
        os.makedirs(scenario_dir, exist_ok=True)
        extra_pq, extra_burned = None, 0
        if extra_buildings:
            import pandas as pd
            from shapely.geometry import Polygon as _Poly
            rows = []
            for eb in extra_buildings:
                poly = _Poly(eb["coords"])
                if poly.is_valid and not poly.is_empty:
                    rows.append({"area_in_meters": 0.0, "confidence": 1.0, "wkt": poly.wkt})
            extra_pq = os.path.join(scenario_dir, "extra_buildings.parquet")
            pd.DataFrame(rows).to_parquet(extra_pq)
            _to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
            modified_dem, _, extra_burned = burn_buildings_into_dem(header, modified_dem, extra_pq, _to_src)
        dem_path = os.path.join(scenario_dir, "scenario.dem.ascii")
        protection_utils.write_ascii_grid(dem_path, header, modified_dem)

        log_path = os.path.join(scenario_dir, "run.log")
        with open(log_path, "w"):
            pass  # so _resolve_boundary_item has a real file to append warnings to
        run_params = _protection_run_params_and_boundary(baseline_dir, meta, header, src_crs, baseline_dem, log_path)

        cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_path,
               "--rain-mm-hr", str(run_params["rain_mm_hr"]), "--duration-hr", str(run_params["duration_hr"]),
               "--friction", str(run_params["friction"]), "--cfl", str(run_params["cfl"]),
               "--infiltration-mm-hr", str(run_params["infiltration_mm_hr"]), "--amc", str(run_params["amc"]),
               "--site-name", "Protection scenario", "--workdir", scenario_dir]
        if run_params["manning_path"]:
            cmd += ["--manning-file", run_params["manning_path"]]
        if run_params["boundary_path"]:
            cmd += ["--boundary-json", run_params["boundary_path"]]

        with SIM_SLOT.labelled("Protection scenario"):
            with open(log_path, "a") as logf:
                proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed", "error": "flood model run failed -- see run.log"}
            return

        _, scenario_depth = _read_ascii(os.path.join(scenario_dir, "results", "res.max"))
        comparison = nbs_utils.compare_flood_results(baseline_depth, scenario_depth, valid_mask)
        loss_before, loss_after, loss_avoided_pkr = _scenario_building_loss(baseline_dir, header, scenario_depth)
        storage_volume_m3 = protection_utils.catchment_storage_volume_m3(
            baseline_dem, header, src_crs, structures) if structures else 0
        extra_result = None
        if extra_pq:
            # SAILAB's own submersion + loss scoring of the proposed buildings against this scenario's depth
            _to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
            fps = get_building_footprints(header, baseline_dem, extra_pq, _to_src,
                                          aoi_polygon_wgs84=shapely_shape(meta["geometry"]) if meta.get("geometry") else None)
            sub = assess_building_submersion_risk(fps, scenario_depth, header)
            with open(os.path.join(scenario_dir, "building_submersion.json"), "w") as f:
                json.dump(sub, f)
            extra_result = {"supplied": len(extra_buildings), "burned_into_terrain": extra_burned, "scored": len(fps),
                            "submersion": {k: v for k, v in sub.items() if k != "features"},
                            "loss": loss_utils.estimate_building_loss_pkr(sub["features"], cellsize_m=float(header["cellsize"])),
                            "per_building": [{"poly_rc": f["poly_rc"], "depth_m": f.get("depth_m"), "band": f.get("band"),
                                              "damage_fraction": f.get("damage_fraction")} for f in sub["features"]]}
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "done", "result": {
                "structures": structures, "comparison": comparison,
                "baseline_flooded_area_km2": baseline_summary.get("flooded_area_km2"),
                "loss_before": loss_before, "loss_after": loss_after, "loss_avoided_pkr": loss_avoided_pkr,
                "storage_volume_m3": storage_volume_m3 if storage_volume_m3 > 0 else None,
                "extra_buildings": extra_result,
                "job_id": job_id,
            }}
    except Exception as e:
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "failed", "error": str(e)}


@app.route("/protect/run", methods=["POST"])
def area_protect_run():
    """Starts a real manual protection scenario re-run against an
    already-finished Tier 3 baseline run (see execute_protection_run)."""
    body = request.json
    baseline_run_id = body.get("run_id")
    structures = body.get("structures", [])
    # Optional proposed buildings: [{"coords": [[lon, lat], ...], "height_m": float?}, ...]. They are burned into
    # the scenario terrain as flow obstacles exactly like real footprints (buildings_utils.burn_buildings_into_dem)
    # and scored with SAILAB's own submersion + loss code.
    extra_buildings = body.get("extra_buildings") or []
    if not baseline_run_id or not os.path.exists(os.path.join(FIS_CACHE, baseline_run_id, "summary.json")):
        return jsonify({"error": "run the Tier 3 flood model first"}), 400
    if not structures and not extra_buildings:
        return jsonify({"error": "draw at least one protection structure or supply extra_buildings"}), 400
    for s in structures:
        if s.get("type") not in protection_utils.STRUCTURE_TYPES:
            return jsonify({"error": f"unknown structure type '{s.get('type')}'"}), 400

    job_id = uuid.uuid4().hex
    with CLIMATE_JOBS_LOCK:
        CLIMATE_JOBS[job_id] = {"status": "running", "label": "Protection scenario"}
    threading.Thread(target=execute_protection_run, args=(job_id, baseline_run_id, structures, extra_buildings),
                     daemon=True).start()
    return jsonify({"job_id": job_id})


def execute_protection_optimize(job_id, baseline_run_id, types, n_structures, max_evals):
    """Real mystic-driven placement search (protection_optimizer.py), then
    hands the winning structures to the exact same execute_protection_run
    the manual flow uses for the final answer -- the search only
    searches, it never duplicates the render/comparison path."""
    try:
        baseline_dir = os.path.join(FIS_CACHE, baseline_run_id)
        with open(os.path.join(baseline_dir, "run_meta.json")) as f:
            meta = json.load(f)
        header = meta["header"]
        src_crs = meta["src_crs"]
        _, baseline_dem = _read_ascii(os.path.join(baseline_dir, "aoi.dem.ascii"))
        _, baseline_depth = _read_ascii(os.path.join(baseline_dir, "results", "res.max"))
        valid_mask = baseline_dem != header.get("nodata_value", -9999)

        # Real building data, when the baseline run has it on hand -- lets
        # the search optimize for real estimated monetary loss avoided
        # (PKR) instead of raw cell counts (see protection_optimizer.
        # optimize_structures' own docstring). None when unavailable, which
        # that function already degrades gracefully from.
        baseline_building_features = None
        sub_path = os.path.join(baseline_dir, "building_submersion.json")
        if os.path.exists(sub_path):
            try:
                with open(sub_path) as f:
                    baseline_building_features = json.load(f)["features"]
            except Exception:
                baseline_building_features = None

        scratch_dir = os.path.join(PROTECT_CACHE, f"{job_id}_search")
        os.makedirs(scratch_dir, exist_ok=True)
        log_path = os.path.join(scratch_dir, "boundary_resolve.log")
        with open(log_path, "w"):
            pass
        run_params = _protection_run_params_and_boundary(baseline_dir, meta, header, src_crs, baseline_dem, log_path)

        def progress_cb(i, total, comparison):
            if job_id in JOB_CANCEL:
                raise JobCancelled()
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {
                    "status": "running", "label": "Protection search", "trial": i, "max_evals": total,
                    "last_reduction_pct": comparison.get("reduction_pct") if comparison else None,
                    "last_displacement_pct": comparison.get("displacement_pct") if comparison else None,
                    "last_loss_avoided_pkr": comparison.get("loss_avoided_pkr") if comparison else None,
                }

        best = protection_optimizer.optimize_structures(
            header=header, src_crs=src_crs, baseline_dem=baseline_dem, baseline_depth=baseline_depth,
            valid_mask=valid_mask, bbox=meta["bbox"], run_params=run_params,
            types=types, n_structures=n_structures, max_evals=max_evals,
            venv_py=VENV_PY, flood_model_path=FLOOD_MODEL, sim_slot=SIM_SLOT,
            scratch_dir=scratch_dir, progress_cb=progress_cb,
            baseline_building_features=baseline_building_features)
        for fn in (scratch_dir,):
            if os.path.isdir(fn):
                import shutil as _shutil
                _shutil.rmtree(fn, ignore_errors=True)

        if job_id in JOB_CANCEL:
            return
        if best is None:
            with CLIMATE_JOBS_LOCK:
                CLIMATE_JOBS[job_id] = {"status": "failed", "error": "no valid candidate found in the search budget"}
            return

        # Reuse the exact manual-scenario pipeline for the final answer --
        # SIM_SLOT was only held per-trial above (released between trials),
        # so this confirmation run acquires it fresh, not re-entrantly.
        final_job_id = uuid.uuid4().hex
        execute_protection_run(final_job_id, baseline_run_id, best["structures"])
        with CLIMATE_JOBS_LOCK:
            final = CLIMATE_JOBS.get(final_job_id, {})
            if final.get("status") != "done":
                CLIMATE_JOBS[job_id] = {"status": "failed",
                                         "error": final.get("error", "final confirmation run failed")}
                return
            CLIMATE_JOBS[job_id] = {"status": "done", "result": {
                **final["result"], "search_score": round(best["score"], 3),
                "trials_evaluated": best.get("n_trials"), "protect_job_id": final_job_id,
            }}
    except Exception as e:
        with CLIMATE_JOBS_LOCK:
            CLIMATE_JOBS[job_id] = {"status": "failed", "error": str(e)}


@app.route("/protect/optimize", methods=["POST"])
def area_protect_optimize():
    """Starts a real mystic-driven search for the best protection
    placement (see execute_protection_optimize/protection_optimizer.py).
    Progress polls via the existing /climate-jobs/<id>/progress.json
    route -- no new polling endpoint needed."""
    body = request.json
    baseline_run_id = body.get("run_id")
    if not baseline_run_id or not os.path.exists(os.path.join(FIS_CACHE, baseline_run_id, "summary.json")):
        return jsonify({"error": "run the Tier 3 flood model first"}), 400
    types = [t for t in (body.get("types") or ["dam", "canal"]) if t in protection_utils.STRUCTURE_TYPES]
    if not types:
        return jsonify({"error": "select at least one real structure type"}), 400
    n_structures = max(1, min(3, int(body.get("n_structures", 1))))
    max_evals = max(6, min(60, int(body.get("max_evals", 12))))

    job_id = uuid.uuid4().hex
    with CLIMATE_JOBS_LOCK:
        CLIMATE_JOBS[job_id] = {"status": "running"}
    threading.Thread(target=execute_protection_optimize,
                      args=(job_id, baseline_run_id, types, n_structures, max_evals), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/protect/<job_id>/mapbox3d")
def protect_mapbox3d(job_id):
    """Real protection-scenario before/after diff overlay on the map --
    same canvas-overlay path (and the same NBS_DIFF_COLOR_STOPS) already
    used for the NbS scenario diff view."""
    scenario_dir = os.path.join(PROTECT_CACHE, job_id)
    diff_path = os.path.join(scenario_dir, "diff.npy")
    bbox_path = os.path.join(scenario_dir, "bbox_geom.json")
    if not (os.path.exists(diff_path) and os.path.exists(bbox_path)):
        return Response("<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>"
                        "No protection scenario result found for this id.</p>", mimetype="text/html")
    diff = np.load(diff_path)
    with open(bbox_path) as f:
        bg = json.load(f)

    water = {"depth_stack": diff[np.newaxis, :, :], "save_int_min": 60.0,
             "dry_thresh": 0.1, "max_depth": 1.0, "color_stops": NBS_DIFF_COLOR_STOPS}
    html_path = os.path.join(AOI3D_CACHE, f"protect_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "Protection scenario diff (Mapbox)", bg["bbox"], bg["geometry"], MAPBOX_TOKEN, water=water,
            legend_html="<b>Protection scenario: before/after</b><br>"
                        "green=protected (now dry) &middot; red=displaced (newly wet) &middot; blue=still flooded")
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


@app.route("/run", methods=["POST"])
def run():
    body = request.json
    geom = body.get("geometry")
    if not geom:
        return jsonify({"error": "no area selected"}), 400
    rain_mm_hr = float(body.get("rain_mm_hr", 40))
    duration_hr = float(body.get("duration_hr", 2))
    # Real uploaded rainfall time series (see rainfall_csv_utils.py),
    # from the /area/rainfall-csv preview -- when present, this drives
    # the actual physics directly (flood_model.py's --rain-series-json),
    # overriding the triangular-design-storm rain_mm_hr/duration_hr above.
    # Those two are still set to the series' own real peak/span here
    # (rather than left at whatever stray value the UI's manual fields
    # happened to hold) purely for informational display consistency --
    # every other part of this app that reads rain_mm_hr/duration_hr for
    # a report/summary sees real numbers describing what actually ran.
    rainfall_series = body.get("rainfall_series")
    if rainfall_series and rainfall_series.get("times_hr"):
        rain_mm_hr = round(max(rainfall_series["rates_mm_hr"], default=0.0), 3)
        duration_hr = round(max(rainfall_series["times_hr"], default=0.0), 3)
    else:
        rainfall_series = None
    include_buildings = bool(body.get("include_buildings", True))
    import_id = body.get("import_id")
    imported = _load_import(import_id) if import_id else None
    if import_id and imported is None:
        return jsonify({"error": "the imported plan was not found (it may have expired); open the link from SimCity again"}), 400
    # Same defaults as flood_model.py's own CLI flags -- leaving any of
    # these blank in the UI reproduces exactly what a bare `--rain-mm-hr
    # --duration-hr` run would do.
    friction = float(body.get("friction", 0.05))
    cfl = float(body.get("cfl", 0.7))
    infiltration_mm_hr = float(body.get("infiltration_mm_hr", 0.0))
    use_soil_infiltration = bool(body.get("use_soil_infiltration", False))
    use_green_ampt = bool(body.get("use_green_ampt", False))
    use_channel_conditioning = bool(body.get("use_channel_conditioning", False))
    amc = max(0.0, min(100.0, float(body.get("amc", 50.0))))
    enable_arf = bool(body.get("enable_arf", False))
    storm_center_lon = body.get("storm_center_lon")
    storm_center_lat = body.get("storm_center_lat")
    storm_center_lon = float(storm_center_lon) if storm_center_lon not in (None, "") else None
    storm_center_lat = float(storm_center_lat) if storm_center_lat not in (None, "") else None
    predict_storm_motion = bool(body.get("predict_storm_motion", False))
    # Manual velocity override (no UI control for this -- exposed for direct API/testing use, e.g.
    # verifying the moving-center NetCDF path end-to-end without depending on real rain actually
    # existing near the AOI right now): only used when NOT predicting, so an explicit "predict" request
    # can't be silently overridden by stray leftover fields.
    manual_vel_lon = body.get("storm_velocity_lon_per_hr")
    manual_vel_lat = body.get("storm_velocity_lat_per_hr")
    storm_velocity_lon_per_hr = float(manual_vel_lon) if manual_vel_lon not in (None, "") else None
    storm_velocity_lat_per_hr = float(manual_vel_lat) if manual_vel_lat not in (None, "") else None
    storm_motion_message = ("Manually-specified storm velocity (API/testing override, not an LSTM "
                             "prediction)." if (storm_velocity_lon_per_hr or storm_velocity_lat_per_hr) else None)
    if predict_storm_motion and storm_center_lon is not None and storm_center_lat is not None:
        # Real LSTM storm-motion inference (storm_motion_lstm.py, trained
        # on real IMERG-derived Pakistan monsoon storm-day tracks -- see
        # CLAUDE.md's own entry for this feature) -- a real, quick (~1-2s)
        # Earth Engine call, done synchronously here (not in the
        # background run thread) since the /run route needs its result to
        # build the correct cache key (a different predicted velocity is a
        # genuinely different run, same principle as every other run
        # parameter this app's cache key already covers).
        try:
            import storm_motion_lstm
            pred = storm_motion_lstm.predict_storm_velocity_for_aoi(storm_center_lon, storm_center_lat)
            storm_velocity_lon_per_hr = pred.get("velocity_lon_per_hr", 0.0)
            storm_velocity_lat_per_hr = pred.get("velocity_lat_per_hr", 0.0)
            storm_motion_message = pred.get("message")
        except Exception as e:
            storm_motion_message = f"Storm-motion prediction failed ({e}) -- falling back to a static storm center."
    # Real multiple, independently-sited inflows/outflows -- replaces the
    # app's original single-inflow-edge design. Each real crossing point
    # (lon/lat, from /area/detect-boundaries or a manual pick) is resolved
    # to a DEM edge+position only once the DEM itself is cropped (below),
    # since that needs the real header/src_crs. Both default to [] (real
    # pluvial-only behaviour, no boundary change from before) unless the
    # user supplied some or asked to auto-detect (handled client-side --
    # by the time this route is called, `inflows`/`outflows` already
    # reflect whatever the user did or didn't override).
    inflows_req = body.get("inflows", [])
    outflows_req = body.get("outflows", [])

    allow_global = bool(body.get("use_global_dem", False))
    custom_dem_id = body.get("custom_dem_id") or None
    try:
        manning_scale = max(0.2, min(5.0, float(body.get("manning_scale", 1.0) or 1.0)))
    except (TypeError, ValueError):
        return jsonify({"error": "manning_scale must be a number"}), 400
    try:
        dem_source_for_key, _ = current_dem_source(geom, allow_global=allow_global, custom_dem_id=custom_dem_id)
    except GlobalDemNotEnabled as e:
        return jsonify({"error": str(e), "needs_global_dem_consent": True}), 400
    except Exception as e:
        return jsonify({"error": f"No terrain data for this area: {e}"}), 400
    key = polygon_geometry_hash({"geometry": geom, "rain": rain_mm_hr, "dur": duration_hr,
                                  "dem": dem_source_for_key, "buildings": include_buildings, "import": import_id,
                                  "friction": friction, "cfl": cfl, "infiltration": infiltration_mm_hr,
                                  "amc": amc, "inflows": inflows_req, "outflows": outflows_req,
                                  "use_soil_infiltration": use_soil_infiltration,
                                  "use_green_ampt": use_green_ampt,
                                  "use_channel_conditioning": use_channel_conditioning,
                                  "enable_arf": enable_arf, "storm_center_lon": storm_center_lon,
                                  "storm_center_lat": storm_center_lat,
                                  "storm_velocity_lon_per_hr": storm_velocity_lon_per_hr,
                                  "storm_velocity_lat_per_hr": storm_velocity_lat_per_hr,
                                  "rainfall_series": rainfall_series,
                                  # added only when used, so every pre-existing cached run keeps its key
                                  **({"manning_scale": manning_scale} if manning_scale != 1.0 else {}),
                                  "pipeline": pipeline_signature()})
    run_dir = os.path.join(FIS_CACHE, key)
    cached_summary = os.path.join(run_dir, "summary.json")

    run_id = key
    if os.path.exists(cached_summary):
        with open(cached_summary) as f:
            summary = json.load(f)
        with RUN_STATE_LOCK:
            RUN_STATE[run_id] = {"status": "done", "stage": "done", "summary": summary, "elapsed": summary.get("wall_clock_seconds", 0)}
        return jsonify({"run_id": run_id, "cached": True})

    os.makedirs(run_dir, exist_ok=True)
    dem_source, _ = current_dem_source(geom, allow_global=allow_global, custom_dem_id=custom_dem_id)
    dem_ascii_path = os.path.join(run_dir, "aoi.dem.ascii")
    try:
        header, active_cells, total_cells = crop_dem_to_polygon(
            dem_source, geom, dem_ascii_path)
    except Exception as e:
        return jsonify({"error": f"DEM crop failed: {e}"}), 400

    src_crs = header["src_crs"]
    to_wgs84 = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform
    lon_min, lat_min = to_wgs84(header["xllcorner"], header["yllcorner"])
    lon_max, lat_max = to_wgs84(header["xllcorner"] + header["ncols"] * header["cellsize"],
                                 header["yllcorner"] + header["nrows"] * header["cellsize"])

    with open(os.path.join(run_dir, "run_meta.json"), "w") as f:
        json.dump({"header": header, "src_crs": src_crs, "geometry": geom,
                   "bbox": [lon_min, lat_min, lon_max, lat_max],
                   "rain_mm_hr": rain_mm_hr, "duration_hr": duration_hr,
                   "friction": friction, "cfl": cfl, "infiltration_mm_hr": infiltration_mm_hr,
                   "amc": amc, "inflows": inflows_req, "outflows": outflows_req,
                   "use_soil_infiltration": use_soil_infiltration,
                   "use_green_ampt": use_green_ampt,
                   "use_channel_conditioning": use_channel_conditioning,
                   "enable_arf": enable_arf, "storm_center_lon": storm_center_lon,
                   "storm_center_lat": storm_center_lat,
                   "predict_storm_motion": predict_storm_motion,
                   "storm_velocity_lon_per_hr": storm_velocity_lon_per_hr,
                   "storm_velocity_lat_per_hr": storm_velocity_lat_per_hr,
                   "storm_motion_message": storm_motion_message,
                   "manning_scale": manning_scale, "custom_dem_id": custom_dem_id,
                   "rainfall_series": rainfall_series}, f)
    # The request itself, kept so the run can be saved as a run file and reloaded later.
    try:
        _rf = runfile_utils.build_runfile(dict(body, rainfall_series=rainfall_series, manning_scale=manning_scale,
                                               custom_dem_id=custom_dem_id), APP_VERSION, run_id)
        with open(os.path.join(run_dir, "runfile.json"), "w") as f:
            json.dump(_rf, f)
    except Exception:
        pass

    with RUN_STATE_LOCK:
        RUN_STATE[run_id] = {"status": "queued", "stage": "buildings", "queued_at": time.time()}
        RUN_QUEUE.append(run_id)
    threading.Thread(target=execute_fis_run,
                      args=(run_id, run_dir, dem_ascii_path, rain_mm_hr, duration_hr,
                            include_buildings, src_crs, lon_min, lat_min, lon_max, lat_max),
                      kwargs=dict(friction=friction, cfl=cfl, infiltration_mm_hr=infiltration_mm_hr, amc=amc,
                                  inflows=inflows_req, outflows=outflows_req, geom=geom,
                                  use_soil_infiltration=use_soil_infiltration,
                                  use_green_ampt=use_green_ampt,
                                  use_channel_conditioning=use_channel_conditioning,
                                  enable_arf=enable_arf, storm_center_lon=storm_center_lon,
                                  storm_center_lat=storm_center_lat,
                                  storm_velocity_lon_per_hr=storm_velocity_lon_per_hr,
                                  storm_velocity_lat_per_hr=storm_velocity_lat_per_hr,
                                  storm_motion_message=storm_motion_message,
                                  rainfall_series=rainfall_series, imported=imported,
                                  manning_scale=manning_scale),
                      daemon=True).start()
    return jsonify({"run_id": run_id, "cached": False, "grid": f"{header['ncols']}x{header['nrows']}",
                    "active_cells": active_cells})


def _compute_or_load_damage(run_id):
    """Real depth-damage (Huizinga et al. 2017) + Getis-Ord Gi* hotspot
    analysis, computed LAZILY on first request and cached to disk (both
    the JSON stats and the raw arrays, so the 3D map view never re-runs
    the expensive 99-permutation Getis-Ord test a second time) --
    deliberately NOT part of execute_fis_run's own critical path (see the
    comment left in its place there): it was previously slowing down
    every single run whether or not anyone ever opened this panel.
    Returns (result_dict, damage_array, hotspot_mask, coldspot_mask) or
    raises."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    cache_path = os.path.join(run_dir, "damage_result.json")
    arrays_path = os.path.join(run_dir, "damage_arrays.npz")
    if os.path.exists(cache_path) and os.path.exists(arrays_path):
        with open(cache_path) as f:
            result = json.load(f)
        arrs = np.load(arrays_path)
        return result, arrs["damage"], arrs["hotspot"], arrs["coldspot"]

    lulc_path = os.path.join(run_dir, "lulc.npy")
    max_path = os.path.join(run_dir, "results", "res.max")
    if not (os.path.exists(lulc_path) and os.path.exists(max_path)):
        raise RuntimeError("no land-cover or depth data for this run")

    lulc = np.load(lulc_path)
    header, max_depth = _read_ascii(max_path)
    valid_mask = max_depth != -9999
    damage_array, class_summary = compute_damage(lulc, max_depth, valid_mask=valid_mask)
    hotspots = getis_ord_hotspots(damage_array, valid_mask)
    try:
        estimated_agricultural_loss = loss_utils.estimate_agricultural_loss_pkr(
            damage_array, lulc, valid_mask, cellsize_m=float(header["cellsize"]))
    except Exception:
        estimated_agricultural_loss = None
    result = {
        "method": "Huizinga et al. 2017 (JRC) global depth-damage curves, Asia region, "
                  "per real ESA WorldCover land-use class",
        "class_breakdown": class_summary,
        "hotspot_method": "Getis-Ord Gi* (esda/libpysal), 99 permutations, "
                          "|z|>1.96 & p<0.05 for significance",
        "hotspot_cells": hotspots["hotspot_cells"],
        "coldspot_cells": hotspots["coldspot_cells"],
        "mean_damage_fraction_overall": float(damage_array[valid_mask].mean()) if valid_mask.any() else 0.0,
        "estimated_agricultural_loss": estimated_agricultural_loss,
    }
    with open(cache_path, "w") as f:
        json.dump(result, f)
    np.savez(arrays_path, damage=damage_array, hotspot=hotspots["hotspot_mask"], coldspot=hotspots["coldspot_mask"])
    return result, damage_array, hotspots["hotspot_mask"], hotspots["coldspot_mask"]


@app.route("/runs/<run_id>/damage.json")
def run_damage(run_id):
    try:
        result, _, _, _ = _compute_or_load_damage(run_id)
    except Exception as e:
        return jsonify({"error": f"damage/hotspot analysis failed: {e}"}), 400
    return jsonify(result)


# Real, shared color ramps for the map overlays below -- damage reuses the
# exact same green->yellow->orange->red palette mapbox3d_utils.py's own
# riskColorFromFraction already uses for buildings/roads, for visual
# consistency across every risk-colored thing this app renders.
DAMAGE_COLOR_STOPS = [[0.0, [0x2e, 0xcc, 0x71]], [0.3, [0xf2, 0xd9, 0x4e]],
                      [0.55, [0xf0, 0x88, 0x3e]], [1.0, [0xc8, 0x1e, 0x1e]]]
# Categorical stops (see the color-stop reuse in mapbox3d_utils.py's own
# docstring for why placing two identical-color stops on either side of a
# discrete value renders it as a flat color, not a gradient): 0=not
# significant (hidden via dry_thresh), 0.5=coldspot (blue), 1.0=hotspot (red).
HOTSPOT_COLOR_STOPS = [[0.0, [0, 0, 0]], [0.49, [0, 0, 0]],
                       [0.50, [0x3f, 0x8c, 0xe0]], [0.99, [0x3f, 0x8c, 0xe0]],
                       [1.0, [0xc8, 0x1e, 0x1e]]]
# NbS diff overlay: 0=no change (hidden), 1/3=protected (green),
# 2/3=displaced (red), 1.0=still flooded (neutral blue) -- same
# categorical-via-flat-stop-pairs technique.
NBS_DIFF_COLOR_STOPS = [[0.0, [0, 0, 0]], [0.32, [0, 0, 0]],
                        [0.333, [0x2e, 0xcc, 0x71]], [0.65, [0x2e, 0xcc, 0x71]],
                        [0.666, [0xc8, 0x1e, 0x1e]], [0.98, [0xc8, 0x1e, 0x1e]],
                        [1.0, [0x3f, 0x8c, 0xe0]]]


def _bbox_geom_for_run(run_id):
    with open(os.path.join(FIS_CACHE, run_id, "run_meta.json")) as f:
        meta = json.load(f)
    return meta["bbox"], meta["geometry"]


@app.route("/runs/<run_id>/damage-mapbox3d")
def run_damage_mapbox3d(run_id):
    """Real damage-fraction overlay on the map, via the exact same
    canvas-overlay path Tier 2/3/Coastal already use for water depth --
    see mapbox3d_utils.build_mapbox_flood_scene's color_stops support."""
    try:
        result, damage_array, _, _ = _compute_or_load_damage(run_id)
        bbox, geom = _bbox_geom_for_run(run_id)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>", mimetype="text/html")

    water = {"depth_stack": damage_array[np.newaxis, :, :].astype("float32"), "save_int_min": 60.0,
             "dry_thresh": 0.005, "max_depth": max(float(damage_array.max()), 0.05),
             "color_stops": DAMAGE_COLOR_STOPS}
    html_path = os.path.join(AOI3D_CACHE, f"damage_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "Damage (Mapbox)", bbox, geom, MAPBOX_TOKEN, water=water,
            legend_html=f"<b>Real depth-damage fraction</b> (Huizinga et al. 2017)<br>"
                        f"Mean {result['mean_damage_fraction_overall']*100:.1f}% &middot; green=low, red=high")
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


@app.route("/runs/<run_id>/hotspot-mapbox3d")
def run_hotspot_mapbox3d(run_id):
    """Real Getis-Ord Gi* hotspot/coldspot overlay on the map -- same
    canvas-overlay path, categorical color stops (see HOTSPOT_COLOR_STOPS)."""
    try:
        result, _, hotspot_mask, coldspot_mask = _compute_or_load_damage(run_id)
        bbox, geom = _bbox_geom_for_run(run_id)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>", mimetype="text/html")

    coded = np.where(hotspot_mask > 0, 1.0, np.where(coldspot_mask > 0, 0.5, 0.0)).astype("float32")
    water = {"depth_stack": coded[np.newaxis, :, :], "save_int_min": 60.0,
             "dry_thresh": 0.1, "max_depth": 1.0, "color_stops": HOTSPOT_COLOR_STOPS}
    html_path = os.path.join(AOI3D_CACHE, f"hotspot_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "Hotspots (Mapbox)", bbox, geom, MAPBOX_TOKEN, water=water,
            legend_html=f"<b>Real Getis-Ord Gi* clusters</b> (99 permutations, p&lt;0.05)<br>"
                        f"{result['hotspot_cells']} hotspot cells (red) &middot; {result['coldspot_cells']} coldspot cells (blue)")
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


def _resolve_sediment_d50_mm(run_id, run_dir, meta, requested_d50_mm):
    """Real, automatic grain-size default: `requested_d50_mm` of None/""/
    "auto" means derive it from real SoilGrids sand/clay texture for this
    AOI (soil_utils.mean_d50_mm_for_aoi) rather than making the user type
    a number -- same "real-data-informed default the user can still
    override" pattern already used for infiltration (useSoilInfiltration)
    and AMC elsewhere in this app. Cached into run_meta.json after the
    first real computation so repeat "auto" requests don't re-run the
    SoilGrids reprojection every time -- only the first request per run
    pays that (small) real cost."""
    if requested_d50_mm not in (None, "", "auto"):
        return float(requested_d50_mm), "user-specified"
    if "sediment_auto_d50_mm" in meta:
        return meta["sediment_auto_d50_mm"], meta.get(
            "sediment_auto_d50_source", "real SoilGrids-derived (area-weighted mean texture, Pakistan)")
    d50 = None
    try:
        _, max_depth = _read_ascii(os.path.join(run_dir, "results", "res.max"))
        valid_mask = max_depth != -9999
        d50 = soil_utils.mean_d50_mm_for_aoi(meta["header"], meta["src_crs"], valid_mask=valid_mask)
    except Exception:
        d50 = None
    if d50 is None:
        return sediment_utils.DEFAULT_D50_MM, "default fallback (no local SoilGrids data for this area -- see global_data/fetch_soilgrids_pakistan.py)"
    source = "real SoilGrids-derived (area-weighted mean sand/clay texture -> published grain-size-by-texture-class, Pakistan)"
    try:
        meta["sediment_auto_d50_mm"] = d50
        meta["sediment_auto_d50_source"] = source
        with open(os.path.join(run_dir, "run_meta.json"), "w") as f:
            json.dump(meta, f)
    except Exception:
        pass
    return d50, source


def _compute_or_load_sediment(run_id, d50_mm=None):
    """Real Meyer-Peter & Muller bedload-transport-capacity post-process
    (see sediment_utils.py's own docstring for why this, not CAESAR-
    Lisflood), computed LAZILY on first request per d50 value and cached
    -- same "don't slow down the main run" discipline already established
    for damage/hotspot (_compute_or_load_damage): this reads the run's
    ALREADY-SAVED per-timestep depth/velocity rasters straight off disk
    (no re-simulation), so it's cheap (plain numpy over a handful of
    saved timesteps), but it's still not something every run needs, so it
    stays out of execute_fis_run's own critical path exactly like damage.
    d50_mm=None (or "auto") derives a real soil-informed grain size for
    this AOI instead of requiring the user to type one -- see
    _resolve_sediment_d50_mm."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    with open(os.path.join(run_dir, "run_meta.json")) as f:
        meta = json.load(f)
    d50_mm, d50_source = _resolve_sediment_d50_mm(run_id, run_dir, meta, d50_mm)
    d50_mm = max(sediment_utils.D50_MIN_MM, min(sediment_utils.D50_MAX_MM, float(d50_mm)))
    d50_key = f"{d50_mm:.2f}"
    cache_path = os.path.join(run_dir, f"sediment_result_{d50_key}.json")
    arrays_path = os.path.join(run_dir, f"sediment_arrays_{d50_key}.npz")
    if os.path.exists(cache_path) and os.path.exists(arrays_path):
        with open(cache_path) as f:
            result = json.load(f)
        arrs = np.load(arrays_path)
        return result, arrs["qb_peak"], arrs["risk_band"]

    water = _read_water_stack(run_dir, rain_mm_hr=meta.get("rain_mm_hr"), duration_hr=meta.get("duration_hr"))
    if water is None:
        raise RuntimeError("no per-timestep output for this run -- cannot compute sediment transport")
    if water.get("speed_stack") is None:
        raise RuntimeError("no per-timestep velocity output for this run -- cannot compute real bed shear stress")
    if water.get("stride", 1) != 1:
        # _read_water_stack spatially downsamples depth_stack/speed_stack
        # for extremely large domains (see its own real size-cap fix,
        # added for the animated 3D view specifically) -- manning_n/
        # elevation below are read at FULL resolution, so combining them
        # would silently mismatch shapes for a run this large. Refuse
        # cleanly rather than crash or (worse) silently sample the wrong
        # cell -- sediment transport on a domain this large isn't
        # currently supported, not degraded.
        raise RuntimeError(
            f"this run's domain is too large for sediment-transport analysis at full resolution "
            f"(the animated-view fix downsamples it by a real stride of {water['stride']}x, which "
            f"this calculation cannot use) -- not currently supported for domains this size")

    manning_path = os.path.join(run_dir, "manning.ascii")
    if os.path.exists(manning_path):
        _, manning_n = _read_ascii(manning_path)
        manning_n = np.where(manning_n <= 0, meta.get("friction", 0.05), manning_n)
        manning_source = "per-cell (real ESA WorldCover Manning's n, same grid used for the physics run)"
    else:
        manning_n = float(meta.get("friction", 0.05))
        manning_source = "uniform fallback friction (no manning.ascii for this run)"

    # Real per-cell elevation (the SAME conditioned DEM -- buildings/
    # channel burned in -- LISFLOOD-FP actually solved depth on, not a
    # separate/approximate terrain) so the depth-slope-product shear
    # estimate below is computed from the exact water-surface the solver
    # itself produced, not a mismatched grid.
    dem_path = os.path.join(run_dir, "aoi.dem.ascii")
    elevation, cellsize = None, None
    if os.path.exists(dem_path):
        try:
            dem_header, elevation = _read_ascii(dem_path)
            cellsize = meta["header"]["cellsize"]
        except Exception:
            elevation = None

    qb_peak, theta_peak, risk_band, band_counts, wet_mask, shear_method = sediment_utils.peak_sediment_transport(
        water["depth_stack"], water["speed_stack"], manning_n, d50_mm=d50_mm, dry_thresh=water["dry_thresh"],
        elevation=elevation, cellsize=cellsize)
    moving = wet_mask & (qb_peak > 0)

    result = {
        "method": "Meyer-Peter & Muller (1948) bedload transport capacity, from the real solver's own "
                  "per-timestep depth+velocity output (peak over all saved timesteps, not just peak depth)",
        "shear_stress_method": shear_method,
        "note": "Transport CAPACITY / erosion-deposition risk indicator only -- no DEM feedback "
               "(terrain does not actually change mid-run). LISFLOOD-FP itself has no sediment "
               "capability; CAESAR-Lisflood was evaluated and declined (separate CPU-only model, "
               "own old flow solver, built for multi-storm/geomorphic timescales, not this app's "
               "single-event runs). See CLAUDE.md.",
        "d50_mm": d50_mm,
        "d50_source": d50_source,
        "manning_source": manning_source,
        "wet_cells": int(wet_mask.sum()),
        "actively_transporting_cells": int((risk_band > 0).sum()),
        "risk_band_counts": band_counts,
        # 99th-percentile among actively-transporting cells, NOT a literal
        # max -- a real, previously-observed boundary-injection artifact
        # (see CLAUDE.md) can leave one physically-absurd-velocity cell
        # that would otherwise send a true max into the billions and make
        # this headline number meaningless. See sediment_utils.robust_peak.
        "max_bedload_transport_m2s": sediment_utils.robust_peak(qb_peak, moving),
        "max_shields_number": sediment_utils.robust_peak(theta_peak, moving),
        "critical_shields_number": sediment_utils.SHIELDS_CRITICAL,
    }
    with open(cache_path, "w") as f:
        json.dump(result, f)
    np.savez(arrays_path, qb_peak=qb_peak.astype("float32"), risk_band=risk_band)
    return result, qb_peak, risk_band


@app.route("/runs/<run_id>/sediment.json")
def run_sediment(run_id):
    d50_mm = request.args.get("d50_mm")  # None/absent -> real soil-derived auto default
    try:
        result, _, _ = _compute_or_load_sediment(run_id, d50_mm)
    except Exception as e:
        return jsonify({"error": f"sediment transport analysis failed: {e}"}), 400
    return jsonify(result)


# Categorical stops (same flat-stop-pair technique as HOTSPOT_COLOR_STOPS):
# 0=none/not transporting (hidden), then 4 real risk bands low->severe,
# yellow->red, consistent with this app's other risk-color ramps.
SEDIMENT_COLOR_STOPS = [
    [0.0, [0, 0, 0]], [0.09, [0, 0, 0]],
    [0.10, [0xf2, 0xd9, 0x4e]], [0.29, [0xf2, 0xd9, 0x4e]],
    [0.30, [0xf0, 0x88, 0x3e]], [0.49, [0xf0, 0x88, 0x3e]],
    [0.50, [0xe0, 0x55, 0x2e]], [0.69, [0xe0, 0x55, 0x2e]],
    [0.70, [0xc8, 0x1e, 0x1e]], [1.0, [0xc8, 0x1e, 0x1e]],
]


@app.route("/runs/<run_id>/sediment-mapbox3d")
def run_sediment_mapbox3d(run_id):
    """Real bedload-transport-risk overlay on the map, same canvas-overlay
    path as damage/hotspot -- categorical low/moderate/high/severe bands."""
    d50_mm = request.args.get("d50_mm")  # None/absent -> real soil-derived auto default
    try:
        result, _, risk_band = _compute_or_load_sediment(run_id, d50_mm)
        bbox, geom = _bbox_geom_for_run(run_id)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>", mimetype="text/html")

    # risk_band is 0=none,1=low,2=moderate,3=high,4=severe -- map onto the
    # exact mid-points of SEDIMENT_COLOR_STOPS's own flat-color bands.
    band_to_coded = np.array([0.0, 0.15, 0.35, 0.55, 0.85], dtype="float32")
    coded = band_to_coded[risk_band]
    water = {"depth_stack": coded[np.newaxis, :, :], "save_int_min": 60.0,
             "dry_thresh": 0.05, "max_depth": 1.0, "color_stops": SEDIMENT_COLOR_STOPS}
    counts = result["risk_band_counts"]
    html_path = os.path.join(AOI3D_CACHE, f"sediment_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "Sediment risk (Mapbox)", bbox, geom, MAPBOX_TOKEN, water=water,
            legend_html=f"<b>Real bedload transport risk</b> (Meyer-Peter &amp; Muller, d50={result['d50_mm']:.1f}mm)<br>"
                        f"low={counts.get('low',0)} moderate={counts.get('moderate',0)} "
                        f"high={counts.get('high',0)} severe={counts.get('severe',0)} cells &middot; "
                        f"yellow&rarr;red, capacity only (no DEM feedback)")
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


def _resolve_run_dir_and_kind(run_id):
    """Any run this app can compare/report on -- generalized beyond
    standard Tier 3 runs (FIS_CACHE) to also cover a real coastal
    dynamic-surge job (COASTAL_DYN_CACHE): both produce a real
    results/res.max the same real ARC ASCII way LISFLOOD-FP always
    writes it, just under a different directory/metadata layout (a
    coastal job has no run_meta.json -- its own real bbox/geometry live
    in bbox_geom.json instead). GLOF scenarios need no special handling
    here at all: a GLOF boundary inflow is applied through the exact
    same standard /run route as any other Tier 3 run, so a GLOF-driven
    run already lands in FIS_CACHE with a real run_meta.json like every
    other Tier 3 run. Returns (run_dir, kind) or raises if neither cache
    has this id."""
    tier3_dir = os.path.join(FIS_CACHE, run_id)
    if os.path.exists(os.path.join(tier3_dir, "run_meta.json")):
        return tier3_dir, "tier3"
    coastal_dir = os.path.join(COASTAL_DYN_CACHE, run_id)
    if os.path.exists(os.path.join(coastal_dir, "bbox_geom.json")):
        return coastal_dir, "coastal_dyn"
    raise RuntimeError(f"run '{run_id}' not found or not finished")


def _load_run_for_compare(run_id):
    """Real data a "compare"/"batch report" request needs from one
    finished run, generalized across every real run kind this app
    produces (see _resolve_run_dir_and_kind) -- its own real summary
    stats (summary.json) and its own real per-cell peak depth grid
    (results/res.max, whose own real 6-line ARC ASCII header IS the
    grid-compatibility check's real data, so no separate DEM read is
    needed for either run kind). `meta`'s shape differs slightly by
    kind: a Tier 3 run's real run_meta.json has `rain_mm_hr`/
    `duration_hr`; a coastal job instead gets a real `scenario_label`
    built from its own saved `coastal_hvar` station/anchor -- callers
    that display a "scenario" description should prefer
    `scenario_label` when `rain_mm_hr` is None, not assume it's always
    a rain-driven run."""
    run_dir, kind = _resolve_run_dir_and_kind(run_id)
    summary_path = os.path.join(run_dir, "summary.json")
    res_path = os.path.join(run_dir, "results", "res.max")
    if not (os.path.exists(summary_path) and os.path.exists(res_path)):
        raise RuntimeError(f"run '{run_id}' not found or not finished")
    with open(summary_path) as f:
        summary = json.load(f)
    header, depth = _read_ascii(res_path)

    if kind == "tier3":
        with open(os.path.join(run_dir, "run_meta.json")) as f:
            meta = json.load(f)
    else:
        with open(os.path.join(run_dir, "bbox_geom.json")) as f:
            bg = json.load(f)
        hvar = summary.get("coastal_hvar") or {}
        meta = {
            "bbox": bg.get("bbox"), "geometry": bg.get("geometry"), "rain_mm_hr": None, "duration_hr": None,
            "scenario_label": f"Coastal surge -- {hvar.get('name', 'station n/a')}, "
                              f"peak {hvar.get('peak_water_level_m', '?')} m",
        }
    return meta, summary, header, depth


def _run_scenario_label(meta):
    """Shared by compare-runs and the batch report: a human-readable
    description of what drove a run, regardless of real run kind (a
    Tier 3 rain-driven run vs. a real coastal surge job)."""
    if meta.get("rain_mm_hr") is not None:
        return f"{meta.get('rain_mm_hr')} mm/hr, {meta.get('duration_hr')}h"
    return meta.get("scenario_label", "n/a")


def _compare_run_headers_compatible(header_a, header_b):
    """Two runs are only directly comparable cell-by-cell if they share
    the EXACT same grid -- same AOI polygon (hence same crop), same DEM
    source, same resolution. Two different AOIs would need real
    reprojection/resampling to align, which this comparison deliberately
    doesn't attempt (out of scope -- see the route's own docstring)."""
    keys = ("ncols", "nrows", "xllcorner", "yllcorner", "cellsize")
    return all(abs(header_a.get(k, 0.0) - header_b.get(k, 0.0)) < 1e-6 for k in keys)


def _compute_run_diff(depth_a, depth_b, valid_mask, dry_thresh=0.05):
    """Real, symmetric 3-category cell-by-cell comparison -- deliberately
    the same categorical technique already used for the NbS/protection
    before/after diffs (only-A / only-B / both flooded), but reframed
    generically for ANY two runs rather than presuming one is a
    "baseline" the other improves on -- a run comparison could equally be
    two different storm intensities, two different friction values, etc.,
    where neither side is inherently "better"."""
    wet_a = (depth_a > dry_thresh) & valid_mask
    wet_b = (depth_b > dry_thresh) & valid_mask
    only_a = wet_a & ~wet_b
    only_b = ~wet_a & wet_b
    both = wet_a & wet_b
    diff = np.zeros_like(depth_a, dtype="float32")
    diff[only_a] = 1.0 / 3.0
    diff[only_b] = 2.0 / 3.0
    diff[both] = 1.0
    comparison = {
        "cells_only_a": int(only_a.sum()), "cells_only_b": int(only_b.sum()), "cells_both": int(both.sum()),
        "mean_depth_diff_where_both_m": round(float(np.mean(depth_b[both] - depth_a[both])), 4) if both.any() else 0.0,
    }
    return diff, comparison


@app.route("/compare-runs", methods=["POST"])
def compare_runs():
    """Real cell-by-cell comparison of two already-finished Tier 3 runs
    -- e.g. two different storm/friction/AMC values run against the
    IDENTICAL drawn AOI (same polygon -> same DEM crop -> same grid).
    Deliberately does NOT support comparing two different AOIs: their
    grids won't align cell-for-cell without real reprojection/resampling,
    which this route doesn't attempt -- refuses with a clear, honest
    error instead of silently comparing misaligned cells."""
    body = request.json
    run_id_a = (body or {}).get("run_id_a")
    run_id_b = (body or {}).get("run_id_b")
    if not run_id_a or not run_id_b:
        return jsonify({"error": "two run ids are required"}), 400
    if run_id_a == run_id_b:
        return jsonify({"error": "pick two different runs to compare"}), 400
    try:
        meta_a, summary_a, header_a, depth_a = _load_run_for_compare(run_id_a)
        meta_b, summary_b, header_b, depth_b = _load_run_for_compare(run_id_b)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if not _compare_run_headers_compatible(header_a, header_b):
        return jsonify({"error": "these two runs don't share the exact same AOI/grid -- comparison requires two "
                                  "runs against the identical drawn area (e.g. different storm/param values on "
                                  "the same polygon), not two different AOIs."}), 400

    nodata = header_a.get("nodata_value", -9999.0)
    valid_mask = depth_a != nodata
    diff, comparison = _compute_run_diff(depth_a, depth_b, valid_mask)
    cellsize2_km2 = (header_a["cellsize"] ** 2) / 1e6
    comparison["area_only_a_km2"] = round(comparison["cells_only_a"] * cellsize2_km2, 4)
    comparison["area_only_b_km2"] = round(comparison["cells_only_b"] * cellsize2_km2, 4)
    comparison["area_both_km2"] = round(comparison["cells_both"] * cellsize2_km2, 4)

    def _brief(run_id, meta, summary):
        loss = summary.get("estimated_building_loss")
        return {"run_id": run_id, "scenario": _run_scenario_label(meta),
                "flooded_area_km2": summary.get("flooded_area_km2"), "max_depth_m": summary.get("max_depth_m"),
                "peak_hazard_band": summary.get("peak_hazard_band"),
                "estimated_building_loss_pkr": loss.get("total_loss_pkr") if loss else None}

    brief_a, brief_b = _brief(run_id_a, meta_a, summary_a), _brief(run_id_b, meta_b, summary_b)
    # Real loss DIFFERENCE, only when both runs actually have a real
    # estimate on hand (e.g. both had buildings enabled) -- same "only
    # report what's actually there" principle as every other optional
    # field in this comparison, never a fabricated 0 standing in for
    # "unknown".
    loss_diff_pkr = None
    if brief_a["estimated_building_loss_pkr"] is not None and brief_b["estimated_building_loss_pkr"] is not None:
        loss_diff_pkr = round(brief_b["estimated_building_loss_pkr"] - brief_a["estimated_building_loss_pkr"], 2)
    comparison["building_loss_diff_pkr"] = loss_diff_pkr

    return jsonify({
        "run_a": brief_a, "run_b": brief_b,
        "comparison": comparison,
        "method": "Real cell-by-cell comparison of each run's own res.max depth grid -- only valid for two runs "
                  "sharing the identical AOI/grid.",
    })


@app.route("/compare-runs/mapbox3d", methods=["POST"])
def compare_runs_mapbox3d():
    """Real diff overlay for two compared runs, via the exact same
    categorical canvas-overlay path (and the same NBS_DIFF_COLOR_STOPS)
    already used for the NbS/protection before/after diffs -- green/red
    reframed here as "only run A" / "only run B" rather than "protected"/
    "displaced", since neither run is presumed to be a baseline."""
    body = request.json
    run_id_a = (body or {}).get("run_id_a")
    run_id_b = (body or {}).get("run_id_b")
    try:
        meta_a, summary_a, header_a, depth_a = _load_run_for_compare(run_id_a)
        meta_b, summary_b, header_b, depth_b = _load_run_for_compare(run_id_b)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>", mimetype="text/html")
    if not _compare_run_headers_compatible(header_a, header_b):
        return Response("<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>These two runs don't "
                        "share the exact same AOI/grid.</p>", mimetype="text/html")

    nodata = header_a.get("nodata_value", -9999.0)
    valid_mask = depth_a != nodata
    diff, _ = _compute_run_diff(depth_a, depth_b, valid_mask)

    water = {"depth_stack": diff[np.newaxis, :, :], "save_int_min": 60.0,
             "dry_thresh": 0.1, "max_depth": 1.0, "color_stops": NBS_DIFF_COLOR_STOPS}
    html_path = os.path.join(AOI3D_CACHE, f"compareruns_mb_{uuid.uuid4().hex}.html")
    try:
        build_mapbox_flood_scene(
            html_path, "Compare runs (Mapbox)", meta_a["bbox"], meta_a["geometry"], MAPBOX_TOKEN, water=water,
            legend_html="<b>Run comparison (real cell-by-cell)</b><br>"
                        "green=only run A flooded &middot; red=only run B flooded &middot; blue=both flooded")
        with open(html_path) as f:
            html = f.read()
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)
    return Response(html, mimetype="text/html")


def _compare_runs_diff_image_b64(diff, valid_mask):
    """Real static PNG of the categorical run-diff array, for the
    printable/shareable comparison report -- a PDF can't embed the live
    interactive Mapbox view the "Show diff on map" button uses, so this
    renders the same real per-cell classification as a plain image
    instead, via matplotlib (already a real dependency of this app,
    flood_model.py's own report uses it for its map plates)."""
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    # Real 5-category classification: 0=no change (both dry), 1=only A,
    # 2=only B, 3=both flooded, 4=outside the AOI's valid terrain
    # (rendered distinctly, not lumped in with "no change").
    cat = np.zeros(diff.shape, dtype="uint8")
    cat[np.isclose(diff, 1.0 / 3.0)] = 1
    cat[np.isclose(diff, 2.0 / 3.0)] = 2
    cat[np.isclose(diff, 1.0)] = 3
    cat[~valid_mask] = 4

    cmap = ListedColormap(["#e8ecec", "#2ecc71", "#c81e1e", "#3f8ce0", "#0B1417"])
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5, 4.5], cmap.N)

    aspect = cat.shape[0] / max(cat.shape[1], 1)
    fig, ax = plt.subplots(figsize=(6.0, max(6.0 * aspect, 2.0)))
    ax.imshow(cat, cmap=cmap, norm=norm, interpolation="nearest")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Shared branded CSS for this app's own real, self-contained shareable
# reports (compare-runs, batch) -- a plain (non-f) string since it needs
# no per-report variable interpolation, factored out so both reports
# share one definition instead of two independently-drifting copies.
# Deliberately a real, print-friendly LIGHT layout (not the interactive
# app's own dark glass/glow theme) -- a PDF/printed report needs good
# ink economy and legibility on paper, the same reasoning already applied
# to the SAILAB one-pager Word document elsewhere in this app: a light
# document with a dark-teal brand accent band, not a dark-mode report.
REPORT_CSS = """
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; color: #17242A; background: #F4F8F9; }
.report-band { background: linear-gradient(120deg, #0B2A2E 0%, #114044 55%, #0E3438 100%); color: #EAF6F6;
  padding: 26px 36px 22px; }
.report-band h1 { margin: 0; font-size: 23px; font-weight: 700; letter-spacing: 0.02em; }
.report-band .tagline { color: #9FE0DC; margin: 4px 0 0; font-size: 12.5px; letter-spacing: 0.01em; }
.report-body { max-width: 900px; margin: 0 auto; padding: 28px 36px 40px; background: #FFFFFF; }
h2 { font-size: 15px; color: #0E4448; margin: 30px 0 10px; padding-left: 11px; border-left: 3px solid #1FB6AE;
  letter-spacing: 0.01em; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 4px; font-size: 13px; }
th, td { text-align: left; padding: 8px 11px; border-bottom: 1px solid #E1E8E9; }
th { color: #4C6367; font-weight: 600; background: #EEF5F5; font-size: 11.5px; text-transform: uppercase;
  letter-spacing: 0.03em; }
tr:nth-child(even) td { background: #FAFCFC; }
.legend span { display: inline-block; width: 11px; height: 11px; margin-right: 5px; vertical-align: middle;
  border-radius: 3px; }
img { max-width: 100%; border: 1px solid #E1E8E9; border-radius: 8px; margin: 10px 0; display: block; }
.note { color: #6C8286; font-size: 12px; line-height: 1.6; margin-top: 14px; }
"""


def _build_compare_runs_report_html(run_id_a, run_id_b):
    """Real, self-contained (no external file references, so weasyprint
    and a plain browser both render it identically) comparison report --
    the same real numbers /compare-runs returns over JSON, formatted as a
    single shareable document instead of two separate live links a
    recipient without access to this app's own UI couldn't use anyway."""
    meta_a, summary_a, header_a, depth_a = _load_run_for_compare(run_id_a)
    meta_b, summary_b, header_b, depth_b = _load_run_for_compare(run_id_b)
    if not _compare_run_headers_compatible(header_a, header_b):
        raise RuntimeError("these two runs don't share the exact same AOI/grid -- comparison requires two runs "
                            "against the identical drawn area, not two different AOIs.")

    nodata = header_a.get("nodata_value", -9999.0)
    valid_mask = depth_a != nodata
    diff, comparison = _compute_run_diff(depth_a, depth_b, valid_mask)
    cellsize2_km2 = (header_a["cellsize"] ** 2) / 1e6
    comparison["area_only_a_km2"] = round(comparison["cells_only_a"] * cellsize2_km2, 4)
    comparison["area_only_b_km2"] = round(comparison["cells_only_b"] * cellsize2_km2, 4)
    comparison["area_both_km2"] = round(comparison["cells_both"] * cellsize2_km2, 4)
    img_b64 = _compare_runs_diff_image_b64(diff, valid_mask)

    loss_a = summary_a.get("estimated_building_loss")
    loss_b = summary_b.get("estimated_building_loss")
    loss_row = ""
    if loss_a and loss_b:
        loss_diff = round(loss_b["total_loss_pkr"] - loss_a["total_loss_pkr"], 2)
        loss_row = (f"<tr><td>Est. building loss</td><td>Rs {loss_a['total_loss_pkr']:,.0f}</td>"
                    f"<td>Rs {loss_b['total_loss_pkr']:,.0f}</td></tr>"
                    f"<tr><td>Est. building loss diff (B&minus;A)</td><td colspan='2'>Rs {loss_diff:,.0f}</td></tr>")

    def row(label, a_val, b_val):
        return f"<tr><td>{label}</td><td>{a_val}</td><td>{b_val}</td></tr>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{MODEL_NAME} run comparison</title>
<style>{REPORT_CSS}</style></head>
<body>
<div class="report-band"><h1>{MODEL_NAME} v{APP_VERSION} &mdash; Run Comparison</h1><p class="tagline">{MODEL_TAGLINE}</p></div>
<div class="report-body">
<table>
<tr><th></th><th>Run A ({run_id_a})</th><th>Run B ({run_id_b})</th></tr>
{row("Scenario", _run_scenario_label(meta_a), _run_scenario_label(meta_b))}
{row("Flooded area", f"{summary_a.get('flooded_area_km2'):.4f} km&sup2;", f"{summary_b.get('flooded_area_km2'):.4f} km&sup2;")}
{row("Max depth", f"{summary_a.get('max_depth_m')} m", f"{summary_b.get('max_depth_m')} m")}
{row("Peak hazard band", summary_a.get('peak_hazard_band'), summary_b.get('peak_hazard_band'))}
{loss_row}
</table>
<h2>Real cell-by-cell comparison</h2>
<table>
<tr><td>Only run A flooded</td><td>{comparison['area_only_a_km2']} km&sup2; ({comparison['cells_only_a']} cells)</td></tr>
<tr><td>Only run B flooded</td><td>{comparison['area_only_b_km2']} km&sup2; ({comparison['cells_only_b']} cells)</td></tr>
<tr><td>Both flooded</td><td>{comparison['area_both_km2']} km&sup2; ({comparison['cells_both']} cells)</td></tr>
<tr><td>Mean depth diff (B&minus;A) where both wet</td><td>{comparison['mean_depth_diff_where_both_m']} m</td></tr>
</table>
<p class="legend"><span style="background:#2ecc71;"></span>Only run A flooded &nbsp;
<span style="background:#c81e1e;"></span>Only run B flooded &nbsp;
<span style="background:#3f8ce0;"></span>Both flooded &nbsp;
<span style="background:#e8ecec;"></span>Neither flooded</p>
<img src="data:image/png;base64,{img_b64}" alt="Run diff map">
<p class="note">Real cell-by-cell comparison of each run's own res.max depth grid -- only valid for two runs sharing the identical AOI/grid.</p>
</div>
</body></html>"""


@app.route("/compare-runs/<run_id_a>/<run_id_b>/report.html")
def compare_runs_report_html(run_id_a, run_id_b):
    try:
        html = _build_compare_runs_report_html(run_id_a, run_id_b)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>",
                        mimetype="text/html"), 400
    return Response(html, mimetype="text/html")


@app.route("/compare-runs/<run_id_a>/<run_id_b>/report.pdf")
def compare_runs_report_pdf(run_id_a, run_id_b):
    """Real PDF export of the comparison report, same real weasyprint
    render-the-actual-HTML approach as the per-run report.pdf route --
    cached once per (run_id_a, run_id_b) pair rather than regenerated on
    every download, since both source runs are immutable once finished."""
    cache_dir = os.path.join(COMPARE_REPORT_CACHE, f"{run_id_a}__{run_id_b}")
    pdf_path = os.path.join(cache_dir, "report.pdf")
    if not os.path.exists(pdf_path):
        try:
            html = _build_compare_runs_report_html(run_id_a, run_id_b)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        os.makedirs(cache_dir, exist_ok=True)
        try:
            weasyprint.HTML(string=html).write_pdf(pdf_path)
        except Exception as e:
            return jsonify({"error": f"PDF export failed: {e}"}), 500
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=True,
                      download_name=f"SAILAB_compare_{run_id_a}_vs_{run_id_b}.pdf")


def _batch_report_trend_image_b64(labels, flooded_area_km2, max_depth_m):
    """Real matplotlib flooded-area/max-depth-vs-label trend chart for the
    batch report -- genuinely different from the pairwise compare-runs
    report above: a TREND across many runs (e.g. flooded area rising with
    return period), not a cell-by-cell diff between exactly two."""
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(6.4, 4.0))
    x = range(len(labels))
    ax1.plot(x, flooded_area_km2, color="#2e86cc", marker="o", label="Flooded area (km²)")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("Flooded area (km²)", color="#2e86cc")
    ax1.tick_params(axis="y", labelcolor="#2e86cc")
    ax2 = ax1.twinx()
    ax2.plot(x, max_depth_m, color="#c81e1e", marker="s", linestyle="--", label="Max depth (m)")
    ax2.set_ylabel("Max depth (m)", color="#c81e1e")
    ax2.tick_params(axis="y", labelcolor="#c81e1e")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _build_batch_report_html(run_ids, labels):
    """Real, self-contained batch summary report: real stats for each of
    N already-finished runs (no requirement that they share a grid --
    unlike the pairwise compare-runs report, this only ever reads each
    run's own scalar summary.json numbers, never diffs raster cells) plus
    a real trend chart across them. The natural real use is a batch
    return-period sweep (the existing "Run all selected return periods"
    panel), but works for any list of finished run ids."""
    if len(run_ids) < 2:
        raise RuntimeError("at least 2 runs are needed for a batch report")
    if labels and len(labels) != len(run_ids):
        raise RuntimeError("labels, when given, must match the number of runs")
    labels = labels or [str(i + 1) for i in range(len(run_ids))]

    rows = []
    for run_id, label in zip(run_ids, labels):
        meta, summary, _header, _depth = _load_run_for_compare(run_id)
        loss = summary.get("estimated_building_loss")
        rows.append({
            "run_id": run_id, "label": label, "scenario": _run_scenario_label(meta),
            "flooded_area_km2": summary.get("flooded_area_km2", 0.0),
            "max_depth_m": summary.get("max_depth_m", 0.0), "peak_hazard_band": summary.get("peak_hazard_band"),
            "estimated_building_loss_pkr": loss.get("total_loss_pkr") if loss else None,
        })

    img_b64 = _batch_report_trend_image_b64(
        [r["label"] for r in rows], [r["flooded_area_km2"] for r in rows], [r["max_depth_m"] for r in rows])

    # Real loss column, only shown when at least one row actually has a
    # real figure (a run made before the estimated_building_loss field
    # existed, or with buildings disabled, leaves its own cell blank
    # rather than a fabricated 0 -- same "only report what's actually
    # there" principle as the compare-runs report's own loss row).
    any_loss = any(r["estimated_building_loss_pkr"] is not None for r in rows)
    loss_header = "<th>Est. building loss</th>" if any_loss else ""
    table_rows = "".join(
        f"<tr><td>{r['label']}</td><td>{r['scenario']}</td>"
        f"<td>{r['flooded_area_km2']:.4f} km&sup2;</td><td>{r['max_depth_m']:.2f} m</td>"
        f"<td>{r['peak_hazard_band']}</td>"
        + (f"<td>{'Rs ' + format(r['estimated_building_loss_pkr'], ',.0f') if r['estimated_building_loss_pkr'] is not None else '&mdash;'}</td>"
           if any_loss else "")
        + "</tr>" for r in rows)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{MODEL_NAME} batch report</title>
<style>{REPORT_CSS}</style></head>
<body>
<div class="report-band"><h1>{MODEL_NAME} v{APP_VERSION} &mdash; Batch Run Summary</h1><p class="tagline">{MODEL_TAGLINE}</p></div>
<div class="report-body">
<table>
<tr><th>Label</th><th>Scenario</th><th>Flooded area</th><th>Max depth</th><th>Peak hazard</th>{loss_header}</tr>
{table_rows}
</table>
<h2>Flooded area &amp; max depth trend</h2>
<img src="data:image/png;base64,{img_b64}" alt="Batch trend chart">
<p class="note">Real per-run stats from each run's own summary.json -- {len(rows)} runs. Run ids: {", ".join(run_ids)}.</p>
</div>
</body></html>"""


@app.route("/batch-report/report.html")
def batch_report_html():
    run_ids = [r for r in request.args.get("run_ids", "").split(",") if r]
    labels = [l for l in request.args.get("labels", "").split(",") if l] or None
    try:
        html = _build_batch_report_html(run_ids, labels)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>{e}</p>",
                        mimetype="text/html"), 400
    return Response(html, mimetype="text/html")


@app.route("/batch-report/report.pdf")
def batch_report_pdf():
    """Real PDF export of the batch summary report -- same real
    render-the-actual-HTML weasyprint approach as the other report
    routes, cached by a hash of the exact run-id+label list since an
    arbitrary-length list can't safely form a directory name on its own."""
    run_ids = [r for r in request.args.get("run_ids", "").split(",") if r]
    labels = [l for l in request.args.get("labels", "").split(",") if l] or None
    cache_key = hashlib.sha256(f"{run_ids}|{labels}".encode()).hexdigest()[:24]
    cache_dir = os.path.join(COMPARE_REPORT_CACHE, f"batch_{cache_key}")
    pdf_path = os.path.join(cache_dir, "report.pdf")
    if not os.path.exists(pdf_path):
        try:
            html = _build_batch_report_html(run_ids, labels)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        os.makedirs(cache_dir, exist_ok=True)
        try:
            weasyprint.HTML(string=html).write_pdf(pdf_path)
        except Exception as e:
            return jsonify({"error": f"PDF export failed: {e}"}), 500
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=True,
                      download_name="SAILAB_batch_report.pdf")


@app.route("/area/compute-aal", methods=["POST"])
def area_compute_aal():
    """Real Average Annual Loss (AAL) -- the standard catastrophe-risk
    summary metric (see aal_utils.py), computed from a real set of
    already-finished physics runs at different return periods (typically
    the same runs the "Run all selected return periods" batch panel just
    produced). Deliberately a separate, explicit, user-triggered action
    (not automatic) -- it needs real building-loss data from EVERY point,
    so it refuses with a clear, specific error naming which run(s) are
    missing it rather than silently computing a partial/misleading
    number from whichever runs happen to have real data."""
    body = request.json or {}
    items = body.get("points", [])
    if len(items) < 2:
        return jsonify({"error": "at least 2 real return-period runs are needed to compute a real AAL"}), 400

    aal_points = []
    missing = []
    for item in items:
        run_id = item.get("run_id")
        rp = item.get("return_period_years")
        run_dir = os.path.join(FIS_CACHE, run_id or "")
        summary_path = os.path.join(run_dir, "summary.json")
        if not run_id or not os.path.exists(summary_path):
            missing.append(f"{rp}yr (run not found)")
            continue
        with open(summary_path) as f:
            summary = json.load(f)
        loss = summary.get("estimated_building_loss")
        if not loss or loss.get("total_loss_pkr") is None:
            missing.append(f"{rp}yr (no real building-loss data for this run -- was \"Include buildings\" on?)")
            continue
        aal_points.append({"return_period_years": rp, "loss_pkr": loss["total_loss_pkr"], "run_id": run_id})

    if missing:
        return jsonify({"error": "Can't compute a real AAL -- missing real building-loss data for: "
                                  + "; ".join(missing)}), 400

    try:
        result = aal_utils.compute_aal(aal_points)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(result)


@app.route("/runs/<run_id>/infra.json")
def run_infra(run_id):
    path = os.path.join(FIS_CACHE, run_id, "infra_risk.json")
    if not os.path.exists(path):
        abort(404)
    with open(path) as f:
        return jsonify(json.load(f))


@app.route("/runs/<run_id>/progress.json")
def run_progress(run_id):
    with RUN_STATE_LOCK:
        st = dict(RUN_STATE.get(run_id, {}))
    if not st:
        abort(404)
    resp = {"status": st["status"], "stage": st.get("stage", "dem"), "stages": RUN_STAGES}
    run_dir = os.path.join(FIS_CACHE, run_id)
    if st["status"] == "queued":
        with RUN_STATE_LOCK:
            pos = RUN_QUEUE.index(run_id) + 1 if run_id in RUN_QUEUE else None
            ahead = [(rid, dict(v)) for rid, v in RUN_STATE.items() if v.get("status") in ("running", "postprocessing")]
        resp.update(solver=SIM_SLOT.snapshot(), queue_position=pos, queued_for=time.time() - st.get("queued_at", time.time()),
                    waiting_on=[{"run_id": rid, "stage": v.get("stage"), "status": v.get("status"),
                                 "elapsed": time.time() - v.get("start", time.time())} for rid, v in ahead])
    if st["status"] in ("running", "postprocessing"):
        sim_min = read_sim_minutes(run_dir)
        log_path = os.path.join(run_dir, "run.log")
        model_min = None
        if os.path.exists(log_path):
            with open(log_path) as fh:
                model_min, _, _ = latest_progress(fh.read())
        pct = 3
        if sim_min and model_min:
            pct = min(99, int(model_min / sim_min * 100))
        resp.update(pct=pct, model_min=model_min, sim_min=sim_min, elapsed=time.time() - st.get("start", time.time()))
    elif st["status"] == "done":
        resp.update(pct=100, summary=st["summary"], elapsed=st.get("elapsed", 0))
    elif st["status"] == "failed":
        resp.update(log_tail=st.get("log_tail", ""))
    resp["can_cancel"] = st["status"] in ("queued", "running")
    return jsonify(resp)


@app.route("/runs/<run_id>/cancel", methods=["POST"])
def run_cancel(run_id):
    """Cancel a queued run (it never starts) or a running solver (the flood_model.py subprocess is terminated).
    Post-processing cannot be interrupted safely and is refused."""
    with RUN_STATE_LOCK:
        st = RUN_STATE.get(run_id)
        if not st:
            abort(404)
        status = st["status"]
        if status in ("done", "failed", "cancelled"):
            return jsonify({"status": status, "note": "already finished"})
        if status == "postprocessing":
            return jsonify({"error": "this run is already post-processing and cannot be cancelled"}), 409
        RUN_CANCEL.add(run_id)
        if status == "queued":
            st["status"] = "cancelled"
            if run_id in RUN_QUEUE:
                RUN_QUEUE.remove(run_id)
    proc = RUN_PROCS.get(run_id)
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return jsonify({"status": "cancelling" if status == "running" else "cancelled"})


@app.route("/runs/<run_id>/report.pdf")
def run_report_pdf(run_id):
    """Real PDF export of the run's own report.html -- weasyprint (pure
    Python, uses this machine's already-installed system cairo/pango libs,
    no root needed) renders the exact same HTML report a user would open
    in a browser, not a separately-maintained PDF template that could
    drift out of sync with it. Cached alongside the HTML (built once per
    run, not regenerated per download)."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    html_path = os.path.join(run_dir, "report.html")
    pdf_path = os.path.join(run_dir, "report.pdf")
    if not os.path.exists(html_path):
        abort(404)
    if not os.path.exists(pdf_path):
        try:
            weasyprint.HTML(html_path).write_pdf(pdf_path)
        except Exception as e:
            return jsonify({"error": f"PDF export failed: {e}"}), 500
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=True,
                      download_name=f"SAILAB_report_{run_id}.pdf")


@app.route("/runs/<run_id>/<path:filename>")
def serve_run_file(run_id, filename):
    run_dir = os.path.join(FIS_CACHE, run_id)
    full = os.path.abspath(os.path.join(run_dir, filename))
    if not full.startswith(os.path.abspath(run_dir) + os.sep) or not os.path.exists(full):
        abort(404)
    with open(full, "rb") as fh:
        data = fh.read()
    return Response(data, mimetype="text/html" if full.endswith(".html") else "application/octet-stream")


@app.route("/monitor/watch", methods=["POST"])
def monitor_add_watch():
    """Saves a new real live-monitoring watch (see monitoring_utils.py's
    own docstring for the full real design/limitations). Just persists
    the request -- the actual periodic checking happens in the
    background thread started at process startup, not here."""
    body = request.json
    geom = body.get("geometry")
    if not geom:
        return jsonify({"error": "no area given"}), 400
    poly = shapely_shape(geom)
    lon_c, lat_c = poly.centroid.x, poly.centroid.y
    watch_id = uuid.uuid4().hex
    watches = monitoring_utils.load_watches()
    watches[watch_id] = {
        "id": watch_id,
        "name": f"AOI @ {lat_c:.3f},{lon_c:.3f}",
        "geometry": geom, "lon": lon_c, "lat": lat_c,
        "threshold_mm_hr": float(body.get("threshold_mm_hr", 10.0)),
        "check_interval_hours": float(body.get("check_interval_hours", 3.0)),
        "include_buildings": bool(body.get("include_buildings", True)),
        "friction": float(body.get("friction", 0.05)),
        "cfl": float(body.get("cfl", 0.7)),
        "webhook_url": (body.get("webhook_url") or "").strip() or None,
        "created": time.time(), "last_checked": None, "last_peak_mm_hr": None,
        "alerts": [],
    }
    monitoring_utils.save_watches(watches)
    return jsonify({"watch_id": watch_id})


@app.route("/monitor/list")
def monitor_list():
    return jsonify({"watches": list(monitoring_utils.load_watches().values())})


@app.route("/monitor/<watch_id>", methods=["DELETE"])
def monitor_remove(watch_id):
    watches = monitoring_utils.load_watches()
    watches.pop(watch_id, None)
    monitoring_utils.save_watches(watches)
    return jsonify({"ok": True})


def _check_one_watch(watch_id, watch, base_url):
    """Real check for one watched AOI: fetches the real Open-Meteo
    forecast for its centroid, extracts the real peak+duration (same
    logic as the manual "Run on the forecast" button), and if the real
    peak crosses the watch's own threshold, triggers a real Tier 3 run
    via a self-loopback HTTP call to this app's own `/run` route -- reuses
    100% of that route's real caching/DEM-crop/execute_fis_run logic
    instead of duplicating it, since a Flask route needs a real request
    context this background thread doesn't have."""
    try:
        rainfall = fetch_rainfall(watch["lat"], watch["lon"])
    except Exception as e:
        print(f"[monitor] {watch_id}: rainfall fetch failed: {e}")
        return
    peak_mm_hr, duration_hr, peak_time = monitoring_utils.real_forecast_peak_and_duration(rainfall)
    try:
        ops_agents.record_forecast(watch_id, watch.get("name"), watch["lat"], watch["lon"], rainfall,
                                   watch["threshold_mm_hr"])
    except Exception as e:
        print(f"[ops] forecast ledger write failed: {e}")
    watches = monitoring_utils.load_watches()
    if watch_id not in watches:
        return  # removed while we were checking
    watches[watch_id]["last_checked"] = time.time()
    watches[watch_id]["last_peak_mm_hr"] = peak_mm_hr
    if peak_mm_hr is not None and peak_mm_hr >= watch["threshold_mm_hr"]:
        run_id = None
        try:
            resp = requests.post(f"{base_url}/run", json={
                "geometry": watch["geometry"], "rain_mm_hr": peak_mm_hr, "duration_hr": duration_hr,
                "include_buildings": watch["include_buildings"], "friction": watch["friction"], "cfl": watch["cfl"],
            }, timeout=30)
            run_id = resp.json().get("run_id")
        except Exception as e:
            print(f"[monitor] {watch_id}: auto-run trigger failed: {e}")
        report_url = f"{PUBLIC_BASE_URL}/runs/{run_id}/report.html" if (run_id and PUBLIC_BASE_URL) else None
        webhook_sent = monitoring_utils.send_webhook_alert(
            watch.get("webhook_url"), watch["name"], peak_mm_hr, duration_hr, watch["threshold_mm_hr"], run_id,
            report_url=report_url)
        watches[watch_id]["alerts"].append({
            "timestamp": time.time(), "peak_mm_hr": peak_mm_hr, "duration_hr": duration_hr,
            "peak_time": peak_time, "run_id": run_id, "webhook_sent": webhook_sent,
        })
        print(f"[monitor] {watch['name']}: real forecast peak {peak_mm_hr:.1f} mm/hr crossed "
              f"{watch['threshold_mm_hr']} mm/hr threshold -- triggered run {run_id}"
              + (f", webhook {'sent' if webhook_sent else 'failed/not configured'}" if watch.get("webhook_url") else ""))
    monitoring_utils.save_watches(watches)


@app.route("/monitor/<watch_id>/check-now", methods=["POST"])
def monitor_check_now(watch_id):
    watches = monitoring_utils.load_watches()
    watch = watches.get(watch_id)
    if not watch:
        abort(404)
    base_url = f"http://127.0.0.1:{os.environ.get('FIS_APP_PORT', '8000')}"
    threading.Thread(target=_check_one_watch, args=(watch_id, watch, base_url), daemon=True).start()
    return jsonify({"ok": True, "checking": True})


def _monitor_background_loop():
    """Real background thread, started once at process startup: wakes
    every 10 minutes (far finer than any individual watch's own real
    interval) and checks whichever watched AOIs are actually due, so
    each watch's own real check_interval_hours is honored independently
    rather than everything running on one fixed global cadence."""
    base_url = f"http://127.0.0.1:{os.environ.get('FIS_APP_PORT', '8000')}"
    while True:
        try:
            watches = monitoring_utils.load_watches()
            now = time.time()
            for watch_id, watch in watches.items():
                due = watch["last_checked"] is None or (now - watch["last_checked"]) >= watch["check_interval_hours"] * 3600
                if due:
                    _check_one_watch(watch_id, watch, base_url)
        except Exception as e:
            print(f"[monitor] background loop error (continuing): {e}")
        time.sleep(600)


@app.route("/ops/health")
def ops_health():
    """Last stored result of every data-health check (see ops_agents.py)."""
    return jsonify(ops_agents._load_json(ops_agents.STATE_PATH, {"checks": {}}))


@app.route("/ops/health/run", methods=["POST"])
def ops_health_run():
    """Runs every health check now (takes several seconds; network probes)."""
    return jsonify(ops_agents.run_health_checks())


@app.route("/ops/forecast-skill")
def ops_forecast_skill():
    """How well the live-monitoring forecasts matched what was later
    reported for the same hours (optionally ?watch_id=)."""
    return jsonify(ops_agents.forecast_skill(watch_id=request.args.get("watch_id")))


@app.route("/runs/<run_id>/qa")
def run_qa(run_id):
    """Rule-based QA record for a finished run (computed on demand for runs made before QA existed)."""
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        abort(404)
    run_dir = os.path.join(FIS_CACHE, run_id)
    qa = ops_agents._load_json(os.path.join(run_dir, "run_qa.json"), None)
    if qa is None:
        qa = ops_agents.qa_run_dir(run_dir, run_id=run_id)
    if qa is None:
        abort(404)
    return jsonify(qa)


@app.route("/ops/run-qa-scan", methods=["POST"])
def ops_run_qa_scan():
    body = request.get_json(silent=True) or {}
    return jsonify(ops_agents.scan_recent_runs(FIS_CACHE, since_days=float(body.get("since_days", 2)),
                                               limit=int(body.get("limit", 200)), notifier=ops_agents.notify))


def _ops_background_loop():
    """Health checks every 15 minutes, forecast scoring in the same pass,
    and a QA scan of recent runs once a day."""
    last_scan = 0.0
    while True:
        try:
            ops_agents.run_health_checks()
            n = ops_agents.score_due_forecasts()
            if n:
                print(f"[ops] scored {n} monitoring forecast(s)")
            if time.time() - last_scan > 86400:
                last_scan = time.time()
                ops_agents.scan_recent_runs(FIS_CACHE, notifier=ops_agents.notify)
        except Exception as e:
            print(f"[ops] background loop error (continuing): {e}")
        time.sleep(900)


@app.route("/osm-buildings-3d")
def osm_buildings_3d():
    """Standalone tool, deliberately separate from the main single-page app
    and from viewer3d_utils.py's custom Three.js scene pipeline: draw a
    polygon, get real OSM buildings extruded to their real/estimated
    heights via Mapbox GL JS's native fill-extrusion layer -- a mature,
    battle-tested 3D rendering path with no custom camera or terrain-
    sampling code of this app's own to get wrong. See osm_buildings_3d.py."""
    return Response(render_osm_buildings_3d_page(MAPBOX_TOKEN), mimetype="text/html")


if __name__ == "__main__":
    # 5060 is on Chrome/Edge's hardcoded unsafe-port blocklist (SIP port) --
    # the browser refuses to even attempt the connection, independent of
    # any network/firewall reachability. 8080 was already in use by another
    # service on this shared machine. 8000 is free and not blocklisted.
    #
    # FIS_APP_PORT (env var, default 8000): lets the automated test suite
    # (tests/conftest.py's live_server fixture) launch its own dedicated,
    # isolated instance on a different port instead of depending on the
    # shared production instance at 8000 -- this machine is heavily shared
    # (other users' sessions, leftover background jobs from prior work) and
    # the production instance can be restarted/killed by someone else at
    # any moment, which would otherwise silently take down an in-progress
    # test run with it (a real ConnectionError observed during this
    # session's own verification run).
    _port = int(os.environ.get("FIS_APP_PORT", "8000"))
    for _name, _path in data_paths.missing_required():
        print(f"[{MODEL_NAME}] WARNING: required dataset missing: {_name} -> {_path} "
              f"(fix data_paths.py or set SAILAB_{_name}); runs needing it will fail loudly.")
    for _name, _path, _ok, _req in data_paths.check_data_paths():
        if not _ok and not _req:
            print(f"[{MODEL_NAME}] note: optional dataset missing: {_name} -> {_path}")
    threading.Thread(target=_monitor_background_loop, daemon=True).start()
    if os.environ.get('SAILAB_DISABLE_OPS_LOOP') != '1':
        threading.Thread(target=_ops_background_loop, daemon=True).start()
    print(f"[{MODEL_NAME}] running at http://0.0.0.0:{_port}")
    app.run(host="0.0.0.0", port=_port, debug=False, threaded=True)
