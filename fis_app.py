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
import subprocess
import threading
import time
import uuid

import imageio_ffmpeg
from flask import Flask, request, jsonify, Response, abort, send_file
from dotenv import load_dotenv

from polygon_utils import (polygon_geometry_hash, load_polygon_from_upload,
                            polygon_area_km2, crop_dem_to_polygon)
from progress_utils import read_sim_minutes, latest_progress
from buildings_utils import (fetch_buildings_for_aoi, burn_buildings_into_dem, get_building_footprints,
                              building_footprints_lonlat)
from infra_utils import (fetch_osm_infrastructure, assess_infrastructure_risk,
                          fetch_osm_building_heights, assess_building_submersion_risk)
from rainfall_rivers_utils import fetch_rainfall, fetch_river_discharge, fetch_waterway_crossing
from damage_utils import fetch_lulc_for_bounds, compute_damage, getis_ord_hotspots, WORLDCOVER_CLASSES, lulc_to_manning_grid
from protection_utils import apply_structures_to_dem, compare_flood_results, structure_footprints, STRUCTURE_TYPES
from protection_optimizer import optimize_structures
from osm_buildings_3d import render_osm_buildings_3d_page
from viewer3d_utils import build_3d_scene
from mapbox3d_utils import build_mapbox_flood_scene
import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import shape as shapely_shape, Point as ShapelyPoint, Polygon as ShapelyPolygon

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
FLOOD_MODEL = os.path.join(BASE_DIR, "flood_model.py")
FIS_CACHE = os.path.join(BASE_DIR, "fis_cache")
UPLOADS = os.path.join(BASE_DIR, "fis_uploads")
AOI3D_CACHE = os.path.join(BASE_DIR, "fis_aoi3d_cache")
PROTECT_CACHE = os.path.join(BASE_DIR, "fis_protect_cache")
AREA3D_BUILDINGS_CACHE = os.path.join(BASE_DIR, "fis_area3d_buildings_cache")
os.makedirs(FIS_CACHE, exist_ok=True)
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(AOI3D_CACHE, exist_ok=True)
os.makedirs(PROTECT_CACHE, exist_ok=True)
os.makedirs(AREA3D_BUILDINGS_CACHE, exist_ok=True)

# Fallback DEM until the Pakistan-wide SRTM is uploaded and converted.
# Real Pakistan-wide SRTM (EPSG:32642, ~29m, ~3.37 billion cells, 13.48GB).
# Left as GeoTIFF -- crop_dem_to_polygon reads only the small windowed area
# per request, so there's no need to (and no reason to) convert the whole
# country to ASCII up front.
PAKISTAN_DEM = "/home/aliyan/DGX-Shared/iliyan-llm/flood/Pak_srtm.tif"
FALLBACK_DEM = os.path.join(BASE_DIR, "bahawalpur.dem.ascii")

app = Flask(__name__)
RUN_STATE = {}
RUN_STATE_LOCK = threading.Lock()
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
PROTECT_STATE = {}
PROTECT_STATE_LOCK = threading.Lock()
OPTIMIZE_STATE = {}
OPTIMIZE_STATE_LOCK = threading.Lock()
SIM_SLOT = threading.Semaphore(1)  # shared-machine concurrency guard, same as gui_app.py

ACCENT = "#0E7A8C"
MODEL_NAME = "SAILAB"
MODEL_FULL_NAME = "Simulation &amp; Analytics for Inundation, Loss, Assets &amp; Built Environment"
MODEL_TAGLINE = "From rainfall to risk, modeled."


def current_dem_source():
    if os.path.exists(PAKISTAN_DEM):
        return PAKISTAN_DEM, "Pakistan-wide SRTM (uploaded)"
    return FALLBACK_DEM, "Bahawalpur FABDEM sample (Pakistan SRTM not yet uploaded)"




PAGE = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{MODEL_NAME}</title>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.css" rel="stylesheet">
<link href="https://unpkg.com/@mapbox/mapbox-gl-draw@1.4.3/dist/mapbox-gl-draw.css" rel="stylesheet">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --page:#F3F6F8; --surface:#FFFFFF; --surface-2:#E9F1F3; --ink:#142430; --ink-2:#46606B;
  --muted:#7C929B; --line:#D7E2E5; --accent:{ACCENT}; --accent-soft:#E3F2F4;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; height:100%; font-family:"Public Sans",system-ui,sans-serif; background:var(--page); color:var(--ink); }}
.app {{ display:flex; flex-direction:column; height:100vh; }}
header {{ display:flex; align-items:center; justify-content:space-between; padding:12px 20px;
  border-bottom:2px solid var(--ink); background:var(--surface); }}
.brand {{ font-family:"Barlow Condensed",sans-serif; font-weight:700; font-size:22px; text-transform:uppercase; }}
.brand span {{ color:var(--accent); }}
.main {{ display:flex; flex:1; overflow:hidden; }}
.left-col {{ display:flex; flex-direction:column; flex:1; min-width:320px; }}
#mapWrap {{ position:relative; flex:1; display:flex; min-height:120px; }}
#map {{ flex:1; }}
.vresizer {{ height:6px; cursor:row-resize; background:var(--line); flex:0 0 auto; }}
.vresizer:hover, .vresizer.active {{ background:var(--accent); }}
.view3d-wrap {{ position:relative; flex:1; display:flex; flex-direction:column; min-height:120px; }}
.view3d-tabs {{ display:flex; gap:2px; background:var(--line); flex:0 0 auto; }}
.view3d-tabs button, .view3d-tabs a {{ flex:1; padding:8px 4px; border:none; background:var(--surface); color:var(--ink-2);
  font-family:"Barlow Condensed",sans-serif; font-weight:600; text-transform:uppercase; letter-spacing:0.03em;
  font-size:11.5px; cursor:pointer; text-align:center; box-sizing:border-box; }}
.view3d-tabs button.active {{ background:var(--accent); color:#06181c; }}
.view3d-tabs button:disabled {{ color:var(--muted); cursor:not-allowed; opacity:0.5; }}
iframe.frame3d {{ width:100%; flex:1; border:1px solid var(--line); border-top:none; background:#0B1417; display:block; }}
.resizer {{ width:6px; cursor:col-resize; background:var(--line); flex:0 0 auto; }}
.resizer:hover, .resizer.active {{ background:var(--accent); }}
.right-col {{ width:400px; min-width:280px; max-width:70vw; background:var(--surface); border-left:1px solid var(--line);
  overflow-y:auto; display:flex; flex-direction:column; }}
.panel-section {{ padding:16px 18px; border-bottom:1px solid var(--line); display:flex; flex-direction:column; gap:12px; }}
.panel-section h3 {{ font-family:"Barlow Condensed",sans-serif; text-transform:uppercase; font-size:14px;
  color:var(--ink-2); margin:0 0 2px; letter-spacing:0.04em; }}
label {{ font-size:12.5px; color:var(--ink-2); display:block; margin-bottom:4px; }}
input[type=number], input[type=file] {{ width:100%; padding:8px; border:1px solid var(--line); border-radius:6px;
  font-family:"IBM Plex Mono",monospace; font-size:12.5px; background:var(--surface); color:var(--ink); }}
select {{ padding:7px; border:1px solid var(--line); border-radius:6px; font-size:12.5px; background:var(--surface); color:var(--ink); }}
button.action {{ padding:11px; border:none; border-radius:8px; background:var(--accent); color:#06181c;
  font-family:"Barlow Condensed",sans-serif; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;
  cursor:pointer; font-size:14px; }}
button.action:disabled {{ opacity:0.4; cursor:not-allowed; }}
button.secondary {{ padding:9px; border:1px solid var(--line); border-radius:8px; background:var(--surface-2);
  color:var(--ink); font-size:12.5px; cursor:pointer; }}
.pill {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:11px; background:var(--accent-soft);
  color:var(--accent); text-transform:uppercase; letter-spacing:0.04em; }}
.progress-track {{ width:100%; height:12px; background:var(--surface-2); border-radius:8px; overflow:hidden; border:1px solid var(--line); }}
.progress-fill {{ height:100%; background:var(--accent); width:2%; transition:width 0.5s; }}
.note {{ font-size:11.5px; color:var(--muted); line-height:1.5; }}
.stat-row {{ display:flex; justify-content:space-between; font-family:"IBM Plex Mono",monospace; font-size:12.5px; padding:4px 0; border-bottom:1px solid var(--line); }}
a.btn {{ display:inline-block; background:var(--surface-2); color:var(--ink); text-decoration:none;
  padding:9px 14px; border-radius:8px; border:1px solid var(--line); font-size:12.5px; text-align:center; }}
a.btn.primary {{ background:var(--accent); color:#06181c; border-color:var(--accent); font-weight:600; }}
.placeholder {{ margin:auto; text-align:center; color:var(--muted); font-size:14px; max-width:420px; }}
.layer-switch {{ position:absolute; top:12px; left:56px; z-index:5; background:var(--surface);
  border-radius:8px; border:1px solid var(--line); overflow:hidden; display:flex; box-shadow:0 1px 4px rgba(0,0,0,0.15); }}
.layer-switch button {{ border:none; background:var(--surface); padding:7px 12px; font-size:12px; cursor:pointer;
  color:var(--ink-2); font-family:"Barlow Condensed",sans-serif; font-weight:600; text-transform:uppercase; letter-spacing:0.03em; }}
.layer-switch button.active {{ background:var(--accent); color:#06181c; }}
.struct-row {{ display:flex; gap:6px; align-items:center; justify-content:space-between; font-size:12px; padding:6px 0; border-bottom:1px solid var(--line); }}
</style>
</head>
<body>
<div class="app">
  <header>
    <div>
      <div class="brand"><span title="{MODEL_FULL_NAME}">{MODEL_NAME}</span></div>
      <div class="note" style="margin-top:2px;">{MODEL_TAGLINE}</div>
    </div>
    <div class="note" id="demSource"></div>
  </header>
  <div class="main">
    <div class="left-col">
      <div id="mapWrap">
        <div id="map" style="flex:1;"></div>
        <div class="layer-switch">
          <button id="layerSat" class="active" onclick="setMapLayer('satellite')">Satellite</button>
          <button id="layerStreets" onclick="setMapLayer('streets')">Streets</button>
          <button onclick="exportMapPng(map, 'map')">Export PNG</button>
        </div>
      </div>
      <div class="vresizer" id="vResizer"></div>
      <div class="view3d-wrap">
        <div class="view3d-tabs">
          <button class="v3d-btn active" data-view="terrain" onclick="show3DView('terrain')">Terrain</button>
          <button class="v3d-btn" data-view="flood" id="v3dFloodBtn" disabled onclick="show3DView('flood')">Flood</button>
          <button class="v3d-btn" data-view="floodmb" id="v3dFloodMbBtn" disabled onclick="show3DView('floodmb')">Flood (Mapbox)</button>
          <a class="v3d-btn" id="v3dFloodMbFullBtn" style="display:none;text-decoration:none;" target="_blank" href="#">Full Page &#8599;</a>
          <button class="v3d-btn" data-view="damage" id="v3dDamageBtn" disabled onclick="show3DView('damage')">Damage</button>
          <button class="v3d-btn" data-view="protect" id="v3dProtectBtn" disabled onclick="show3DView('protect')">Protection</button>
        </div>
        <iframe id="view3dFrame" class="frame3d" allowfullscreen></iframe>
      </div>
    </div>
    <div class="resizer" id="resizerMain"></div>
    <div class="right-col" id="rightCol">

      <div class="panel-section">
        <h3>Draw or upload an area</h3>
        <p class="note">Use the polygon tool on the map (top-left), or upload a shapefile (.zip) / GeoJSON below. No size limit — larger areas simply take longer to solve.</p>
        <input type="file" id="uploadFile" accept=".zip,.geojson,.json">
        <button class="secondary" onclick="uploadShape()">Upload as area</button>
        <div id="areaInfo" style="display:none;">
          <div class="stat-row"><span>Area</span><span id="areaKm2">-</span></div>
        </div>
        <h3 style="margin-top:8px;">Storm</h3>
        <label>Peak rainfall (mm/hr)</label>
        <input type="number" id="rainMmHr" value="40">
        <label>Duration (hr)</label>
        <input type="number" id="durationHr" value="2">
        <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="includeBuildings" checked style="width:auto;"> Include real buildings (affects flow)</label>
        <details style="margin:2px 0;">
          <summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Advanced solver parameters (optional -- sensible defaults used if left as-is)</summary>
          <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px;">
            <div>
              <label>Fallback friction (Manning's n)</label>
              <input type="number" id="friction" value="0.05" step="0.005" min="0.01" title="Floodplain friction is now determined per-cell automatically from real ESA WorldCover land cover (forest, cropland, built-up, water, etc. each get their own real published roughness value) as the model runs. This value is only used as a fallback for any cell the land-cover data doesn't cover, or if the land-cover fetch fails. LISFLOOD-FP's ACC solver can become numerically unstable at very low Manning's friction -- the backend also enforces a floor and adds numerical diffusion (theta) if you get close to it.">
              <p class="note" style="margin-top:4px;">Per-cell friction is determined automatically from real land cover as the model runs (forest, cropland, built-up, water, etc. each get their own roughness) -- this is only the fallback value for cells with no land-cover data.</p>
            </div>
            <div>
              <label>CFL number (inertial solver stability, 0-1)</label>
              <input type="number" id="cfl" value="0.7" step="0.05" min="0.05" max="1">
            </div>
            <div>
              <label>Infiltration rate (mm/hr, subtracted from rainfall -- treated as the AMC-II/average baseline below)</label>
              <input type="number" id="infiltrationMmHr" value="0" step="1" min="0">
            </div>
            <div>
              <label>Antecedent Moisture Condition (soil saturation before this storm)</label>
              <input type="range" id="amc" min="0" max="100" step="1" value="50" oninput="updateAmcLabel()">
              <div class="stat-row" style="margin-top:2px;">
                <span class="note">Dry (AMC-I)</span>
                <span class="note" id="amcLabel">Average (AMC-II) &middot; 50/100</span>
                <span class="note">Wet / saturated (AMC-III)</span>
              </div>
            </div>
            <div>
              <label>Fluvial: peak river/channel inflow (m&sup3;/s, 0 = pluvial-only)</label>
              <input type="number" id="inflowM3s" value="0" step="1" min="0">
            </div>
            <div>
              <label>Inflow edge</label>
              <select id="inflowEdge" onchange="detectedRiver=null;">
                <option value="N">North</option><option value="E">East</option>
                <option value="S">South</option><option value="W">West</option>
              </select>
            </div>
            <div>
              <label>Peak inflow duration (hr, 0 = constant inflow instead of a real rising/receding hydrograph)</label>
              <input type="number" id="inflowDurationHr" value="0" step="0.5" min="0">
            </div>
            <button class="secondary" id="detectRiverBtn" disabled onclick="detectRiver()">Detect river in this area &rarr;</button>
            <p class="note" id="riverNote"></p>
          </div>
        </details>
        <button class="action" id="runBtn" disabled onclick="runModel()">Run flood model &rarr;</button>
        <p class="note" id="statusNote"></p>
      </div>

      <div class="panel-section">
        <h3>Rainfall</h3>
        <div id="contextBox"><p class="note">Draw or upload an area above first.</p></div>
      </div>

      <div class="panel-section">
        <h3>Flood model status</h3>
        <div id="runStatusBox">
          <p class="note">Draw/upload an area and run the model above first.</p>
        </div>
      </div>

      <div class="panel-section">
        <h3>Buildings &amp; infrastructure risk</h3>
        <div id="infraBox"><p class="note">Run the flood model first.</p></div>
      </div>

      <div class="panel-section">
        <h3>Damage &amp; hotspot analysis</h3>
        <div id="damageBox"><p class="note">Run the flood model first.</p></div>
      </div>

      <div class="panel-section">
        <h3>Protection scenario</h3>
        <p class="note">Use the line tool on the map to draw a proposed dam, barrage, canal or drainage channel within your AOI, set a type/height for it below, then re-run the flood model with it in place.</p>
        <div id="structList"><p class="note">No structures added yet.</p></div>
        <label>New structure type</label>
        <select id="structType" onchange="updateStructHeightLabel()">
          <option value="dam">Dam / embankment</option>
          <option value="barrage">Barrage</option>
          <option value="canal">Diversion canal</option>
          <option value="drainage">Drainage pipe</option>
        </select>
        <label id="structHeightLabel">Height / depth (m)</label>
        <input type="number" id="structHeight" value="3.0" step="0.5">
        <button class="secondary" id="addStructureBtn" disabled onclick="addStructure()">Add drawn line as structure</button>
        <button class="action" id="protectRunBtn" disabled onclick="runProtection()">Run protection scenario &rarr;</button>
        <p class="note" id="protectStatusNote"></p>
        <div id="protectResultBox"></div>

        <hr style="margin:14px 0;border-color:#333;">
        <p class="note">Or let the app search for a placement itself: it tries real candidate positions/sizes (each one a genuine flood re-run) and keeps the one that removes the most flooding for the least water displaced elsewhere.</p>
        <label>Structure type(s) to consider</label>
        <div id="optTypeChecks" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
          <label style="font-weight:normal;"><input type="checkbox" value="drainage" checked> Drainage pipe</label>
          <label style="font-weight:normal;"><input type="checkbox" value="canal"> Diversion canal</label>
          <label style="font-weight:normal;"><input type="checkbox" value="dam"> Dam / embankment</label>
          <label style="font-weight:normal;"><input type="checkbox" value="barrage"> Barrage</label>
        </div>
        <label>Number of structures to place</label>
        <select id="optNStruct"><option value="1">1</option><option value="2">2</option><option value="3">3</option></select>
        <label>Search effort</label>
        <select id="optEffort">
          <option value="12">Quick (~12 trials)</option>
          <option value="24" selected>Balanced (~24 trials)</option>
          <option value="48">Thorough (~48 trials, slow)</option>
        </select>
        <button class="action" id="optimizeBtn" disabled onclick="runOptimize()">Auto-optimize placement &rarr;</button>
        <p class="note" id="optimizeStatusNote"></p>
        <div id="optimizeResultBox"></div>
      </div>

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

const MAP_STYLES = {{ satellite: 'mapbox://styles/mapbox/satellite-streets-v12', streets: 'mapbox://styles/mapbox/streets-v12' }};
function setMapLayer(kind) {{
  document.getElementById('layerSat').classList.toggle('active', kind === 'satellite');
  document.getElementById('layerStreets').classList.toggle('active', kind === 'streets');
  const savedData = draw.getAll();
  map.setStyle(MAP_STYLES[kind]);
  map.once('style.load', () => {{
    map.addControl(draw, 'top-left');
    if (savedData.features.length) draw.set(savedData);
  }});
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
  style: MAP_STYLES.satellite,
  center: [69.3451, 30.3753],  // geographic center of Pakistan -- whole country in view by default
  zoom: 4.6,
  preserveDrawingBuffer: true
}});
map.addControl(new mapboxgl.NavigationControl(), 'top-right');

// One shared MapboxDraw instance for BOTH the AOI polygon and protection-
// scenario structure lines -- which one a drawn feature becomes is decided
// by its geometry type (Polygon -> the AOI; LineString -> a pending
// structure), so the user never has to switch "modes" and both can be
// drawn/visible over the same area at once.
const draw = new MapboxDraw({{
  displayControlsDefault: false,
  controls: {{ polygon: true, line_string: true, trash: true }}
}});
map.addControl(draw, 'top-left');

let currentGeoJSON = null;
let detectedRiver = null;

function setArea(geojsonGeometry, areaKm2) {{
  currentGeoJSON = geojsonGeometry;
  // A river detected against a PREVIOUS polygon shape is no longer valid
  // once the AOI changes -- its crossing point could now sit anywhere
  // relative to the new shape/edges, so it must be re-detected, not
  // silently carried over.
  detectedRiver = null;
  document.getElementById('riverNote').textContent = '';
  document.getElementById('areaInfo').style.display = 'block';
  document.getElementById('areaKm2').textContent = areaKm2.toFixed(3) + ' km²';
  document.getElementById('runBtn').disabled = false;
  document.getElementById('detectRiverBtn').disabled = false;
  show3DView('terrain');
  loadContext();
}}

function detectRiver() {{
  if (!currentGeoJSON) return;
  const note = document.getElementById('riverNote');
  const btn = document.getElementById('detectRiverBtn');
  btn.disabled = true;
  note.textContent = 'Looking for a real river/stream crossing this area...';
  fetch('/area/river', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      btn.disabled = false;
      if (d.error) {{ note.textContent = 'River lookup failed: ' + d.error; return; }}
      if (!d.river) {{ note.textContent = 'No real river/stream/canal found crossing this area (pluvial-only is fine).'; return; }}
      const r = d.river;
      document.getElementById('inflowEdge').value = r.nearest_edge;
      detectedRiver = {{lon: r.lon, lat: r.lat, width_m: r.channel_width_m}};
      const disc = r.discharge || {{}};
      let discText = '';
      if (disc.error) {{
        discText = ' Real discharge lookup failed: ' + disc.error + '.';
      }} else if (disc.recent_m3s != null) {{
        discText = ' Real discharge context (Open-Meteo Flood API, GloFAS reanalysis/forecast -- not a live gauge): ' +
          'recent ' + disc.recent_m3s.toFixed(1) + ' m³/s, ' + (disc.daily_time ? disc.daily_time.length : 0) + '-day range ' +
          disc.min_m3s.toFixed(1) + '-' + disc.max_m3s.toFixed(1) + ' m³/s (mean ' + disc.mean_m3s.toFixed(1) + ').';
      }}
      note.innerHTML = 'Found <b>' + r.name + '</b> (' + r.waterway_type + '), nearest to the <b>' + r.nearest_edge +
        '</b> edge -- set as inflow edge above. Inflow will be concentrated over ~' + r.channel_width_m.toFixed(0) +
        'm of real channel width at the detected crossing (not spread across the whole edge).' + discText +
        ' Use this to pick a sensible peak inflow (m³/s) and duration above for a compound fluvial+pluvial run.';
    }})
    .catch(e => {{ btn.disabled = false; note.textContent = 'River lookup failed: ' + e; }});
}}

map.on('draw.create', onDrawChange);
map.on('draw.update', onDrawChange);
let pendingStructureId = null;
let drawDebounceTimer = null;
function onDrawChange(e) {{
  const f = e.features[0];
  if (!f) return;
  if (f.geometry.type === 'Polygon') {{
    // only one AOI polygon at a time -- remove any other polygon already
    // in the shared draw set (structure lines are left untouched)
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
  }} else if (f.geometry.type === 'LineString') {{
    pendingStructureId = f.id;
    document.getElementById('addStructureBtn').disabled = false;
  }}
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

// ---- single 3D view pane, switchable between every tab's own overlay ----
let currentRunId = null, currentProtectId = null;
// Monotonic token guarding the terrain 3D fetch -- if the user re-draws
// or re-selects the view again before an in-flight request resolves,
// that older response is discarded instead of overwriting the iframe
// with a now-stale (possibly differently-shaped) AOI's render.
let view3dToken = 0;
function show3DView(kind) {{
  document.querySelectorAll('.v3d-btn').forEach(b => b.classList.toggle('active', b.dataset.view === kind));
  const frame = document.getElementById('view3dFrame');
  const myToken = ++view3dToken;  // invalidate any in-flight terrain fetch regardless of the new kind
  if (kind === 'terrain') {{
    if (!currentGeoJSON) return;
    fetch('/area/terrain3d', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
      .then(r => r.text()).then(html => {{ if (myToken === view3dToken) frame.srcdoc = html; }});
  }} else if (kind === 'flood' && currentRunId) {{
    frame.removeAttribute('srcdoc'); frame.src = '/runs/' + currentRunId + '/viewer_3d.html';
  }} else if (kind === 'floodmb' && currentRunId) {{
    frame.removeAttribute('srcdoc'); frame.src = '/runs/' + currentRunId + '/mapbox_3d.html';
  }} else if (kind === 'damage' && currentRunId) {{
    frame.removeAttribute('srcdoc'); frame.src = '/runs/' + currentRunId + '/damage3d.html';
  }} else if (kind === 'protect' && currentProtectId) {{
    frame.removeAttribute('srcdoc'); frame.src = '/protect/' + currentProtectId + '/protect3d.html';
  }}
}}

let contextToken = 0;
function loadContext() {{
  const box = document.getElementById('contextBox');
  if (!currentGeoJSON) {{ box.innerHTML = '<p class="note">Draw or upload an area above first.</p>'; return; }}
  box.innerHTML = '<p class="note">Loading real rainfall data...</p>';
  const myToken = ++contextToken;
  fetch('/area/context', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{geometry: currentGeoJSON}})}})
    .then(r => r.json()).then(d => {{
      if (myToken !== contextToken) return;
      const rf = d.rainfall;
      const rainRows = rf.error ? '<p class="note">Rainfall fetch failed: ' + rf.error + '</p>' :
        '<div class="stat-row"><span>Source</span><span>' + rf.source + '</span></div>' +
        '<div class="stat-row"><span>Hourly points (6 days)</span><span>' + rf.hourly_precipitation_mm.length + '</span></div>' +
        '<div class="stat-row"><span>Peak hourly (mm)</span><span>' + Math.max(...rf.hourly_precipitation_mm).toFixed(1) + '</span></div>';
      box.innerHTML = '<h3 style="margin-top:0;">Rainfall (real, Open-Meteo)</h3>' + rainRows;
    }});
}}

function runModel() {{
  document.getElementById('runBtn').disabled = true;
  document.getElementById('statusNote').textContent = 'Submitting...';
  fetch('/run', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      geometry: currentGeoJSON,
      rain_mm_hr: parseFloat(document.getElementById('rainMmHr').value),
      duration_hr: parseFloat(document.getElementById('durationHr').value),
      include_buildings: document.getElementById('includeBuildings').checked,
      friction: parseFloat(document.getElementById('friction').value),
      cfl: parseFloat(document.getElementById('cfl').value),
      infiltration_mm_hr: parseFloat(document.getElementById('infiltrationMmHr').value),
      amc: parseFloat(document.getElementById('amc').value),
      inflow_m3s: parseFloat(document.getElementById('inflowM3s').value),
      inflow_edge: document.getElementById('inflowEdge').value,
      inflow_duration_hr: parseFloat(document.getElementById('inflowDurationHr').value),
      inflow_lon: detectedRiver ? detectedRiver.lon : null,
      inflow_lat: detectedRiver ? detectedRiver.lat : null,
      inflow_width_m: detectedRiver ? detectedRiver.width_m : null
    }})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ document.getElementById('statusNote').textContent = 'Error: ' + d.error; document.getElementById('runBtn').disabled = false; return; }}
    currentRunId = d.run_id;
    pollStatus();
  }});
}}

function pollStatus() {{
  fetch('/runs/' + currentRunId + '/progress.json').then(r => r.json()).then(d => {{
    const box = document.getElementById('runStatusBox');
    if (d.status === 'done') {{
      box.innerHTML = '<h3>Complete</h3>' +
        '<div class="stat-row"><span>Flooded area</span><span>' + d.summary.flooded_area_km2.toFixed(3) + ' km²</span></div>' +
        '<div class="stat-row"><span>Max depth</span><span>' + d.summary.max_depth_m.toFixed(2) + ' m</span></div>' +
        '<div class="stat-row"><span>Peak hazard</span><span>' + d.summary.peak_hazard_band + '</span></div>' +
        '<div class="stat-row"><span>Time taken</span><span>' + d.elapsed.toFixed(1) + ' s</span></div>' +
        '<div style="margin-top:14px; display:flex; gap:8px;">' +
        '<a class="btn primary" href="/runs/' + currentRunId + '/report.html" target="_blank">Full 2D report</a></div>' +
        (d.summary.buildings_used ? '<p class="note" style="margin-top:10px;">' + d.summary.buildings_used + ' real buildings conditioned into this run.</p>' : '');
      renderInfra(d.summary);
      loadDamage();
      document.getElementById('v3dFloodBtn').disabled = false;
      document.getElementById('v3dFloodMbBtn').disabled = false;
      const fullBtn = document.getElementById('v3dFloodMbFullBtn');
      fullBtn.href = '/runs/' + currentRunId + '/mapbox_3d.html';
      fullBtn.style.display = 'inline-block';
      document.getElementById('protectRunBtn').disabled = false;
      document.getElementById('optimizeBtn').disabled = false;
      document.getElementById('addStructureBtn').disabled = (pendingStructureId === null);
      show3DView('flood');
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
      box.innerHTML = '<h3>Running</h3>' + stepsHtml +
        (curIdx >= 0 && stages[curIdx][0] === 'model'
          ? '<div class="progress-track" style="margin-top:8px;"><div class="progress-fill" style="width:' + pct + '%"></div></div>'
          : '');
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
  html += '</div>';
  if (!risk) {{ box.innerHTML = html + '<p class="note">No infrastructure data for this run.</p>'; return; }}
  const catLabels = {{roads:'Roads', critical:'Hospitals / Schools / Clinics', power:'Power infrastructure', trees:'Trees / forest'}};
  for (const cat of Object.keys(risk)) {{
    const d = risk[cat];
    let bandsHtml = Object.entries(d.band_breakdown).map(([band,count]) =>
      '<span class="pill" style="margin-right:4px;">' + band + ': ' + count + '</span>').join('');
    html += '<div style="margin-bottom:16px;"><h3>' + (catLabels[cat]||cat) + '</h3>' +
      '<div class="stat-row"><span>Total features found</span><span>' + d.total_features + '</span></div>' +
      '<div class="stat-row"><span>In model domain</span><span>' + d.in_domain + '</span></div>' +
      '<div class="stat-row"><span>Flooded</span><span>' + d.flooded_count + ' (' + d.flooded_pct_of_in_domain.toFixed(1) + '%)</span></div>' +
      '<div style="margin-top:6px;">' + bandsHtml + '</div></div>';
  }}
  box.innerHTML = html;
}}

function loadDamage() {{
  const box = document.getElementById('damageBox');
  box.innerHTML = '<p class="note">Computing LULC + damage curves + hotspot analysis (first time is slower, then cached)...</p>';
  fetch('/runs/' + currentRunId + '/damage.json').then(r => r.json()).then(d => {{
    if (d.error) {{ box.innerHTML = '<p class="note">Damage analysis failed: ' + d.error + '</p>'; return; }}
    let rows = '';
    for (const [cls, s] of Object.entries(d.class_summary)) {{
      rows += '<div style="margin-bottom:10px;"><b>' + cls + '</b>' +
        '<div class="stat-row"><span>Flooded</span><span>' + s.flooded_pct.toFixed(1) + '% of class area</span></div>' +
        '<div class="stat-row"><span>Mean damage (flooded cells)</span><span>' + (s.mean_damage_fraction_in_flooded*100).toFixed(1) + '%</span></div></div>';
    }}
    box.innerHTML = '<h3 style="margin-top:0;">Damage % by land use (Huizinga/JRC curves)</h3>' + rows +
      '<h3>Hotspot analysis (Getis-Ord Gi*)</h3>' +
      '<div class="stat-row"><span>Statistically significant hotspots</span><span>' + d.hotspot_cells + ' cells</span></div>' +
      '<div class="stat-row"><span>Statistically significant coldspots</span><span>' + d.coldspot_cells + ' cells</span></div>';
    document.getElementById('v3dDamageBtn').disabled = false;
  }});
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

// ---- protection scenario (uses the same map/draw as the AOI) ----
let structures = [];

// A drainage structure lowers the ground to model a buried pipe, not an
// open channel -- its physical size parameter is a pipe DIAMETER, not a
// dam-style height, and typical values are far smaller (tens of cm to a
// couple of metres) than an embankment's.
function updateStructHeightLabel() {{
  const type = document.getElementById('structType').value;
  const label = document.getElementById('structHeightLabel');
  const input = document.getElementById('structHeight');
  if (type === 'drainage') {{
    label.textContent = 'Pipe diameter (m)';
    if (parseFloat(input.value) >= 3.0) input.value = '0.6';
  }} else {{
    label.textContent = 'Height / depth (m)';
    if (parseFloat(input.value) <= 0.6) input.value = '3.0';
  }}
}}

function addStructure() {{
  if (pendingStructureId === null) {{ alert('Draw a line on the map first.'); return; }}
  const f = draw.get(pendingStructureId);
  if (!f || f.geometry.type !== 'LineString') {{ alert('Draw a line on the map first.'); return; }}
  const type = document.getElementById('structType').value;
  const height = parseFloat(document.getElementById('structHeight').value);
  structures.push({{type, geometry: f.geometry.coordinates, height_m: height}});
  draw.delete(pendingStructureId);
  pendingStructureId = null;
  document.getElementById('addStructureBtn').disabled = true;
  renderStructList();
}}

function renderStructList() {{
  const labels = {{dam:'Dam / embankment', barrage:'Barrage', canal:'Diversion canal', drainage:'Drainage pipe'}};
  const unit = {{drainage: 'm diameter'}};
  document.getElementById('structList').innerHTML = structures.map((s, i) =>
    '<div class="struct-row"><span>' + labels[s.type] + ' &middot; ' + s.height_m + (unit[s.type] ? ' ' + unit[s.type] : 'm') + '</span>' +
    '<button class="secondary" style="padding:2px 8px;" onclick="removeStructure(' + i + ')">Remove</button></div>').join('') ||
    '<p class="note">No structures added yet.</p>';
}}
function removeStructure(i) {{ structures.splice(i, 1); renderStructList(); }}

function runProtection() {{
  if (!structures.length) {{ alert('Add at least one structure first.'); return; }}
  document.getElementById('protectRunBtn').disabled = true;
  document.getElementById('protectStatusNote').textContent = 'Submitting...';
  fetch('/protect/run', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{run_id: currentRunId, structures}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ document.getElementById('protectStatusNote').textContent = 'Error: ' + d.error; document.getElementById('protectRunBtn').disabled = false; return; }}
    currentProtectId = d.protect_id;
    pollProtectStatus();
  }});
}}

function pollProtectStatus() {{
  fetch('/protect/' + currentProtectId + '/progress.json').then(r => r.json()).then(d => {{
    const box = document.getElementById('protectResultBox');
    document.getElementById('protectRunBtn').disabled = (d.status !== 'done' && d.status !== 'failed');
    if (d.status === 'done') {{
      const c = d.comparison;
      box.innerHTML = '<h3>Scenario result</h3>' +
        '<div class="stat-row"><span>Baseline flooded cells</span><span>' + c.baseline_flooded_cells + '</span></div>' +
        '<div class="stat-row"><span>Protected flooded cells</span><span>' + c.protected_flooded_cells + '</span></div>' +
        '<div class="stat-row"><span>Area protected (kept dry)</span><span>' + c.reduction_pct.toFixed(1) + '%</span></div>' +
        '<div class="stat-row"><span>Water displaced elsewhere (newly flooded)</span><span>' + c.displacement_pct.toFixed(1) + '%</span></div>' +
        '<div class="stat-row"><span>Total flooded-volume change</span><span>' + c.depth_change_pct.toFixed(1) + '%</span></div>' +
        '<p class="note" style="margin-top:8px;">"Displaced" cells are areas that became newly flooded because the structure routed water there instead -- protection is rarely free.</p>';
      document.getElementById('v3dProtectBtn').disabled = false;
      show3DView('protect');
    }} else if (d.status === 'failed') {{
      box.innerHTML = '<h3 style="color:#d03b3b;">Failed</h3><pre style="font-size:11px;white-space:pre-wrap;">' + (d.log_tail||'') + '</pre>';
    }} else {{
      box.innerHTML = '<p class="note">' + (d.status||'queued') + '...</p>';
      setTimeout(pollProtectStatus, 1500);
    }}
  }});
}}

let currentOptId = null;
const optLabels = {{dam:'Dam / embankment', barrage:'Barrage', canal:'Diversion canal', drainage:'Drainage pipe'}};

function runOptimize() {{
  const structure_types = Array.from(document.querySelectorAll('#optTypeChecks input:checked')).map(el => el.value);
  if (!structure_types.length) {{ alert('Pick at least one structure type to consider.'); return; }}
  const n_structures = parseInt(document.getElementById('optNStruct').value);
  const max_evals = parseInt(document.getElementById('optEffort').value);
  document.getElementById('optimizeBtn').disabled = true;
  document.getElementById('optimizeStatusNote').textContent = 'Submitting...';
  document.getElementById('optimizeResultBox').innerHTML = '';
  fetch('/protect/optimize', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{run_id: currentRunId, structure_types, n_structures, max_evals}})
  }}).then(r => r.json()).then(d => {{
    if (d.error) {{ document.getElementById('optimizeStatusNote').textContent = 'Error: ' + d.error; document.getElementById('optimizeBtn').disabled = false; return; }}
    currentOptId = d.opt_id;
    pollOptimizeStatus();
  }});
}}

function pollOptimizeStatus() {{
  fetch('/protect/optimize/' + currentOptId + '/progress.json').then(r => r.json()).then(d => {{
    const note = document.getElementById('optimizeStatusNote');
    const box = document.getElementById('optimizeResultBox');
    if (d.status === 'done') {{
      document.getElementById('optimizeBtn').disabled = false;
      note.textContent = 'Done -- ' + d.n_trials + ' real candidates tried.';
      const s = d.search_summary || {{}};
      box.innerHTML = '<h3>Best placement found</h3>' +
        '<div class="stat-row"><span>Area protected (kept dry)</span><span>' + (s.reduction_pct||0).toFixed(1) + '%</span></div>' +
        '<div class="stat-row"><span>Water displaced elsewhere</span><span>' + (s.displacement_pct||0).toFixed(1) + '%</span></div>' +
        '<div id="optStructList">' + (d.chosen_structures||[]).map(st =>
          '<div class="stat-row"><span>' + optLabels[st.type] + '</span><span>' +
          st.height_m.toFixed(2) + 'm x ' + (st.half_width_m*2).toFixed(1) + 'm wide</span></div>').join('') + '</div>' +
        '<p class="note" style="margin-top:8px;">This scenario has been run and rendered exactly like a manually-drawn one -- view it below or click "Protection" in the 3D view switcher.</p>';
      currentProtectId = d.protect_id;
      document.getElementById('v3dProtectBtn').disabled = false;
      show3DView('protect');
    }} else if (d.status === 'failed') {{
      document.getElementById('optimizeBtn').disabled = false;
      box.innerHTML = '<h3 style="color:#d03b3b;">Search failed</h3><pre style="font-size:11px;white-space:pre-wrap;">' + (d.log_tail||'') + '</pre>';
    }} else {{
      const trialTxt = d.trial ? ('trial ' + d.trial + '/' + d.total) : (d.status || 'queued');
      const scoreTxt = (d.last_reduction_pct != null)
        ? (' -- last trial: ' + d.last_reduction_pct.toFixed(1) + '% protected, ' + d.last_displacement_pct.toFixed(1) + '% displaced')
        : '';
      note.textContent = trialTxt + scoreTxt + ' (each trial is a real flood re-run, this can take a while)...';
      setTimeout(pollOptimizeStatus, 1500);
    }}
  }});
}}

renderStructList();
fetch('/dem-source').then(r => r.json()).then(d => {{
  document.getElementById('demSource').textContent = 'DEM: ' + d.label;
}});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return PAGE


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


@app.route("/area/context", methods=["POST"])
def area_context():
    """Real rainfall for whatever polygon is selected -- independent of
    whether the flood model has been run yet. (River/stream/lake context
    was removed at the user's request -- see CLAUDE.md.)"""
    from shapely.geometry import shape
    geom = request.json["geometry"]
    poly = shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2

    try:
        rainfall = fetch_rainfall(lat_c, lon_c)
    except Exception as e:
        rainfall = {"error": str(e)}

    return jsonify({"rainfall": rainfall})


@app.route("/area/river", methods=["POST"])
def area_river():
    """Fluvial inflow helper: finds where a real river/stream/canal (OSM
    waterway geometry) crosses the drawn AOI and which domain edge it's
    nearest, then fetches real discharge context (Open-Meteo Flood API,
    real GloFAS reanalysis+forecast) at that point -- to help the user set
    an informed peak inflow (m^3/s) and edge, the same way real rainfall
    context already informs (without silently driving) the rain_mm_hr
    input. Returns river:null if no waterway is found in the AOI (a
    genuinely pluvial-only area, not an error)."""
    geom = request.json["geometry"]
    poly = shapely_shape(geom)
    lon_min, lat_min, lon_max, lat_max = poly.bounds

    try:
        crossing = fetch_waterway_crossing(lon_min, lat_min, lon_max, lat_max)
    except Exception as e:
        return jsonify({"river": None, "error": f"waterway lookup failed: {e}"})

    if crossing is None:
        return jsonify({"river": None})

    try:
        discharge = fetch_river_discharge(crossing["lat"], crossing["lon"])
    except Exception as e:
        discharge = {"error": str(e)}

    # Documented typical real-world channel widths (not measured for this
    # specific waterway) -- used to concentrate the inflow discharge into a
    # realistic channel width instead of smearing it across the whole
    # domain edge (see flood_model.py's --inflow-pos-m/--inflow-width-m).
    channel_width_m = {"river": 40.0, "stream": 8.0, "canal": 15.0}.get(crossing["waterway_type"], 20.0)

    return jsonify({"river": {**crossing, "discharge": discharge, "channel_width_m": channel_width_m}})


def _crop_dem_array_for_geom(geom):
    """Same crop as the flood-run path, but returns the array in memory
    (for on-the-fly 3D terrain previews on Tabs 1/2 that don't need a full
    flood run first)."""
    dem_source, _ = current_dem_source()
    fallback_crs = "EPSG:32642" if dem_source == FALLBACK_DEM else None
    tmp_path = os.path.join(AOI3D_CACHE, f"tmp_{uuid.uuid4().hex}.dem.ascii")
    header, active_cells, total_cells = crop_dem_to_polygon(
        dem_source, geom, tmp_path, fallback_src_crs=fallback_crs)
    _, dem = _read_ascii(tmp_path)
    os.remove(tmp_path)
    return header, dem


def _lonlat_to_rowcol(lon, lat, header, src_crs):
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    x, y = to_src(lon, lat)
    col = int((x - header["xllcorner"]) / header["cellsize"])
    row = int(header["nrows"] - 1 - (y - header["yllcorner"]) / header["cellsize"])
    return row, col


def _polygon_to_grid_rc(geom, header, src_crs):
    """The AOI polygon's own exterior ring, in the same continuous (row,
    col) grid space as the DEM header -- passed to build_3d_scene so the 3D
    view can cut its terrain/water/overlay meshes against the TRUE drawn
    shape, not just the coarse per-cell NODATA mask crop_dem_to_polygon
    already baked into the DEM. Cutting on the raster mask alone drops
    every mesh triangle touching any invalid vertex, which is biased by
    PlaneGeometry's fixed per-quad diagonal split direction -- verified on
    a small hand-drawn triangle (a 7x11 grid, 26 active cells) rendering as
    a lopsided sawtooth on one edge while the opposite edge came out a
    clean straight line, purely because of which way each quad happened to
    be split, not anything about the real shape. Real geometric clipping
    against this exact polygon removes that bias."""
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    xll, yll, cs, nrows = header["xllcorner"], header["yllcorner"], header["cellsize"], header["nrows"]
    ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
    pts = []
    for lon, lat in ring:
        x, y = to_src(lon, lat)
        col = (x - xll) / cs
        row = (nrows - 1) - (y - yll) / cs
        pts.append([row, col])
    return pts


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
    segments = []
    current_rows, current_cols = [], []
    for lon, lat in lonlat_coords:
        inside = aoi_polygon_wgs84 is None or aoi_polygon_wgs84.contains(ShapelyPoint(lon, lat))
        r, c = _lonlat_to_rowcol(lon, lat, header, src_crs)
        if inside and _rowcol_in_bounds(r, c, header):
            current_rows.append(r); current_cols.append(c)
        else:
            if len(current_rows) >= 2:
                segments.append((current_rows, current_cols))
            current_rows, current_cols = [], []
    if len(current_rows) >= 2:
        segments.append((current_rows, current_cols))
    return segments


@app.route("/area/terrain3d", methods=["POST"])
def area_terrain3d():
    """Tab 1: 3D terrain for whatever's currently drawn/uploaded, before any
    flood run exists. Plain DEM/hillshade, no satellite basemap -- see
    CLAUDE.md for the removal note (applies to every 3D view now)."""
    geom = request.json["geometry"]
    try:
        header, dem = _crop_dem_array_for_geom(geom)
    except Exception as e:
        return Response(f"<p style='color:#d03b3b;font-family:sans-serif;padding:20px;'>"
                        f"Terrain preview failed: {e}</p>", mimetype="text/html")
    aoi_poly_rc = _polygon_to_grid_rc(geom, header, header["src_crs"])
    out_path = os.path.join(AOI3D_CACHE, f"terrain_{uuid.uuid4().hex}.html")
    build_3d_scene(out_path, "Area terrain", header, dem, aoi_poly_rc=aoi_poly_rc)
    with open(out_path) as f:
        html = f.read()
    os.remove(out_path)
    return Response(html, mimetype="text/html")


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


def execute_fis_run(run_id, run_dir, dem_ascii_path, rain_mm_hr, duration_hr,
                     include_buildings, src_crs, lon_min, lat_min, lon_max, lat_max,
                     friction=0.05, cfl=0.7, infiltration_mm_hr=0.0, amc=50.0, inflow_m3s=0.0, inflow_edge="N",
                     inflow_duration_hr=0.0, inflow_lon=None, inflow_lat=None, inflow_width_m=None, geom=None):
    with SIM_SLOT:
        with RUN_STATE_LOCK:
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
        # Pre-conditioning copy, kept for 3D terrain -- rendering the
        # building-raised DEM instead shows buildings as raised terrain
        # "blobs" baked into the mesh rather than proper extruded shapes.
        _write_ascii(bare_dem_path, header0, dem0)

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
                _write_ascii(manning_path, header0, manning_grid)
                manning_result["ok"] = True
            except Exception as e:
                manning_result["error"] = e

        manning_thread = threading.Thread(target=_fetch_manning_bg, daemon=True)
        manning_thread.start()

        # --- Tab 4, part 1: buildings conditioning (affects the flow) ---
        buildings_parquet_path = None
        if include_buildings:
            try:
                to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
                buildings_cache = os.path.join(run_dir, "buildings.parquet")
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
               "--inflow-m3s", str(inflow_m3s), "--inflow-edge", inflow_edge,
               "--inflow-duration-hr", str(inflow_duration_hr),
               "--site-name", "FIS area", "--workdir", run_dir]
        manning_thread.join()
        if manning_result.get("ok"):
            cmd += ["--manning-file", manning_path]
        else:
            with open(log_path, "a") as logf:
                logf.write(f"[manning] real ESA WorldCover-based friction fetch failed, "
                           f"falling back to uniform friction n={friction}: {manning_result.get('error')}\n")
        if inflow_m3s > 0 and inflow_lon is not None and inflow_lat is not None:
            inflow_pos_m = _snap_inflow_to_valid_edge(header0, dem0, inflow_edge, inflow_lon, inflow_lat, src_crs)
            if inflow_pos_m is not None:
                cmd += ["--inflow-pos-m", str(inflow_pos_m),
                        "--inflow-width-m", str(inflow_width_m or 30.0)]
            else:
                with open(log_path, "a") as logf:
                    logf.write("[inflow] detected river's crossing edge has no valid terrain at all -- "
                               "falling back to whole-edge inflow\n")
        t0 = time.time()
        with open(log_path, "a") as logf:
            proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
            elapsed = time.time() - t0
            logf.write(f"\n=== EXIT_CODE:{proc.returncode} elapsed={elapsed:.1f}s ===\n")

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
            bare_header, bare_dem = _read_ascii(bare_dem_path)
            water = _read_water_stack(run_dir, rain_mm_hr=rain_mm_hr, duration_hr=duration_hr)
            # Defined unconditionally (not just when buildings are fetched)
            # -- the road-clipping code further down needs this real AOI
            # polygon regardless of whether building conditioning ran.
            aoi_polygon_wgs84 = shapely_shape(geom) if geom else None

            if buildings_parquet_path:
                to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
                try:
                    osm_heights = fetch_osm_building_heights(lon_min, lat_min, lon_max, lat_max)
                except Exception as e:
                    osm_heights = []
                    with open(log_path, "a") as logf:
                        logf.write(f"[buildings] OSM height fetch failed, using estimate only: {e}\n")
                footprints = get_building_footprints(bare_header, bare_dem, buildings_parquet_path, to_src,
                                                      osm_heights=osm_heights, aoi_polygon_wgs84=aoi_polygon_wgs84)

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
                except Exception as e:
                    with open(log_path, "a") as logf:
                        logf.write(f"[buildings] submersion risk assessment failed: {e}\n")
                    summary["building_submersion"] = None
                if water is not None:
                    nrows_, ncols_ = int(bare_header["nrows"]), int(bare_header["ncols"])
                    depth_stack = water["depth_stack"]
                    colored_footprints = []
                    for fp in footprints:
                        cr = sum(p[0] for p in fp["poly_rc"]) / len(fp["poly_rc"])
                        cc = sum(p[1] for p in fp["poly_rc"]) / len(fp["poly_rc"])
                        row, col = int(round(cr)), int(round(cc))
                        if 0 <= row < nrows_ and 0 <= col < ncols_:
                            depths = depth_stack[:, row, col].tolist()
                        else:
                            depths = [0.0] * depth_stack.shape[0]
                        colored_footprints.append({**fp, "depths": depths})
            else:
                summary["building_submersion"] = None

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
                max_haz = _read_ascii(haz_path)[1] if os.path.exists(haz_path) else None
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

                nrows_, ncols_ = int(bare_header["nrows"]), int(bare_header["ncols"])
                depth_stack = water["depth_stack"] if water is not None else None
                speed_stack = water.get("speed_stack") if water is not None else None
                for road in infra.get("roads", []):
                    depths = speeds = None
                    if depth_stack is not None:
                        lon_c, lat_c = road["centroid"]
                        x_c, y_c = to_src2(lon_c, lat_c)
                        col = int((x_c - bare_header["xllcorner"]) / bare_header["cellsize"])
                        row = int(nrows_ - 1 - (y_c - bare_header["yllcorner"]) / bare_header["cellsize"])
                        if 0 <= row < nrows_ and 0 <= col < ncols_:
                            depths = depth_stack[:, row, col].tolist()
                            if speed_stack is not None:
                                speeds = speed_stack[:, row, col].tolist()
                    for rows_, cols_ in _lonlat_path_to_segments(road["geometry"], bare_header, src_crs,
                                                                   aoi_polygon_wgs84=aoi_polygon_wgs84):
                        road_lines.append({"rows": rows_, "cols": cols_, "depths": depths, "speeds": speeds})
            except Exception as e:
                with open(log_path, "a") as logf:
                    logf.write(f"[infra] risk assessment failed: {e}\n")
                summary["infra_risk"] = None

            # No satellite basemap here (unlike the other 3D views) -- shows
            # the plain DEM/hillshade the same way the flood model itself
            # sees the terrain, at the user's explicit request.
            legend = ""
            if footprints:
                legend += ("<b>Real buildings</b> by CURRENT damage severity (Huizinga curve, animated -- "
                           "not the run's peak): <span style=\"color:#2ecc71\">&#9632;</span> dry/low risk &nbsp; "
                           "<span style=\"color:#f2d94e\">&#9632;</span> &nbsp; "
                           "<span style=\"color:#f0883e\">&#9632;</span> &nbsp; "
                           "<span style=\"color:#c81e1e\">&#9632;</span> severe<br>")
            if road_lines:
                legend += ("<b>Roads</b> by CURRENT DEFRA hazard rating (animated): "
                           "<span style=\"color:#2ecc71\">&#9632;</span> low/dry &nbsp; "
                           "<span style=\"color:#f2d94e\">&#9632;</span> &nbsp; "
                           "<span style=\"color:#f0883e\">&#9632;</span> &nbsp; "
                           "<span style=\"color:#c81e1e\">&#9632;</span> extreme<br>")
            legend += '<b>Water depth</b> (blue ramp, animated) &middot; <b>Rainfall</b> (falling during the storm)'
            with RUN_STATE_LOCK:
                RUN_STATE[run_id]["stage"] = "scene"
            aoi_poly_rc = _polygon_to_grid_rc(geom, bare_header, src_crs) if geom else None
            build_3d_scene(os.path.join(run_dir, "viewer_3d.html"), "Flood 3D", bare_header, bare_dem,
                            building_footprints=colored_footprints, lines=road_lines, water=water,
                            legend_html=legend, aoi_poly_rc=aoi_poly_rc)
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[3d] viewer build failed: {e}\n")

        # Mapbox-native 3D scene (mapbox3d_utils.py) -- the same real
        # rendering approach proven in the standalone Area 3D tool (real
        # Mapbox global terrain, fill-extrusion buildings, line roads),
        # shown as an alternative "Flood 3D (Mapbox)" view alongside the
        # existing Three.js one rather than replacing it outright --
        # Protection/Damage still use the Three.js pipeline for now.
        # Reuses the EXACT SAME real per-timestep depth/speed trajectories
        # already computed above for the Three.js view's own animated
        # building/road coloring (colored_footprints, road_lines) --
        # converted from the DEM's row/col grid back to real lon/lat via
        # the header's own inverse transform -- so both 3D views of the
        # same run animate identically, never disagreeing about which
        # buildings/roads are at risk at a given moment.
        try:
            to_wgs84_mb = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform
            nrows_b, cs_b = int(bare_header["nrows"]), bare_header["cellsize"]
            xll_b, yll_b = bare_header["xllcorner"], bare_header["yllcorner"]

            def rc_to_lonlat(row, col):
                x_m = xll_b + col * cs_b
                y_m = yll_b + (nrows_b - row) * cs_b
                return list(to_wgs84_mb(x_m, y_m))

            mapbox_buildings = [
                {"coords_lonlat": [rc_to_lonlat(r, c) for r, c in fp["poly_rc"]],
                 "height_m": fp["height_m"], "depths": fp.get("depths")}
                for fp in colored_footprints
            ]
            mapbox_roads = [
                {"coords_lonlat": [rc_to_lonlat(r, c) for r, c in zip(rl["rows"], rl["cols"])],
                 "depths": rl.get("depths"), "speeds": rl.get("speeds")}
                for rl in road_lines if len(rl["rows"]) >= 2
            ]

            mapbox_legend = (
                "<b>Buildings</b> by CURRENT damage severity (animated) &middot; "
                "<b>Roads</b> by CURRENT DEFRA hazard rating (animated)<br>"
                "<span style=\"color:#2ecc71\">&#9632;</span> dry/low risk &nbsp;"
                "<span style=\"color:#f2d94e\">&#9632;</span> &nbsp;"
                "<span style=\"color:#f0883e\">&#9632;</span> &nbsp;"
                "<span style=\"color:#c81e1e\">&#9632;</span> severe<br>"
                "<b>Flood depth</b> (blue overlay, animated) &middot; terrain: real Mapbox global elevation "
                "(independent of this app's own uploaded DEM -- see mapbox3d_utils.py)")
            build_mapbox_flood_scene(os.path.join(run_dir, "mapbox_3d.html"), "Flood 3D (Mapbox)",
                                      [lon_min, lat_min, lon_max, lat_max], geom, MAPBOX_TOKEN,
                                      building_footprints=mapbox_buildings, road_lines=mapbox_roads,
                                      water=water, legend_html=mapbox_legend)
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[mapbox3d] scene build failed: {e}\n")

        with RUN_STATE_LOCK:
            RUN_STATE[run_id].update(status="done", stage="done", summary=summary, elapsed=elapsed)


def _read_water_stack(run_dir, save_int_min_default=10.0, rain_mm_hr=None, duration_hr=None):
    """Reads a finished run's per-timestep depth rasters into the shape
    build_3d_scene's water animation expects (same files build_3d_viewer.py
    used to read directly). Also reads the solver's own per-timestep
    velocity rasters (res-*.Vx/.Vy -- real output, already being written
    for every run regardless) and reduces them to a speed MAGNITUDE stack
    -- not the flow-direction arrows removed earlier (that was direction,
    shown as arrows; this is a scalar, used only to compute a real,
    animated DEFRA hazard rating for roads as the storm actually
    progresses -- see the building/road color-animation feature)."""
    results_dir = os.path.join(run_dir, "results")
    wd_files = sorted(glob.glob(os.path.join(results_dir, "res-*.wd")))
    if not wd_files:
        return None
    depth_stack = []
    for fp in wd_files:
        _, d = _read_ascii(fp)
        depth_stack.append(np.where(d == -9999, 0.0, d))
    depth_stack = np.array(depth_stack)

    speed_stack = None
    vx_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vx")))
    vy_files = sorted(glob.glob(os.path.join(results_dir, "res-*.Vy")))
    if len(vx_files) == len(depth_stack) and len(vy_files) == len(depth_stack) and vx_files:
        def _clean(fp):
            d = _read_ascii(fp)[1]
            return np.where(d == -9999, 0.0, d)
        # LISFLOOD-FP writes Vx/Vy on a staggered (Arakawa C) grid, not the
        # same cell-centered grid as depth -- Vx has one EXTRA COLUMN (it's
        # defined on the vertical faces between/around cells), Vy has one
        # EXTRA ROW (horizontal faces). Averaging each pair of adjacent
        # face values back onto the cell-centered depth grid is the
        # standard way to recover a cell-centered velocity from a
        # staggered one -- confirmed by comparing the real file headers
        # directly (Vx: ncols+1, offset half a cell west; Vy: nrows+1,
        # offset half a cell south) rather than assuming.
        try:
            vx_face = np.array([_clean(fp) for fp in vx_files])  # (T, nrows, ncols+1)
            vy_face = np.array([_clean(fp) for fp in vy_files])  # (T, nrows+1, ncols)
            vx_c = 0.5 * (vx_face[:, :, :-1] + vx_face[:, :, 1:])   # -> (T, nrows, ncols)
            vy_c = 0.5 * (vy_face[:, :-1, :] + vy_face[:, 1:, :])   # -> (T, nrows, ncols)
            if vx_c.shape == depth_stack.shape and vy_c.shape == depth_stack.shape:
                speed_stack = np.sqrt(vx_c ** 2 + vy_c ** 2)
        except Exception:
            speed_stack = None

    save_int_min = save_int_min_default
    par_files = glob.glob(os.path.join(run_dir, "*.par"))
    if par_files:
        with open(par_files[0]) as f:
            for line in f:
                if line.strip().startswith("saveint"):
                    save_int_min = float(line.split()[1]) / 60.0
                    break

    return {"depth_stack": depth_stack, "speed_stack": speed_stack, "save_int_min": save_int_min,
            "dry_thresh": 0.03, "max_depth": float(depth_stack.max()),
            "rain_mm_hr": rain_mm_hr, "duration_hr": duration_hr}


@app.route("/run", methods=["POST"])
def run():
    body = request.json
    geom = body.get("geometry")
    if not geom:
        return jsonify({"error": "no area selected"}), 400
    rain_mm_hr = float(body.get("rain_mm_hr", 40))
    duration_hr = float(body.get("duration_hr", 2))
    include_buildings = bool(body.get("include_buildings", True))
    # Same defaults as flood_model.py's own CLI flags -- leaving any of
    # these blank in the UI reproduces exactly what a bare `--rain-mm-hr
    # --duration-hr` run would do.
    friction = float(body.get("friction", 0.05))
    cfl = float(body.get("cfl", 0.7))
    infiltration_mm_hr = float(body.get("infiltration_mm_hr", 0.0))
    amc = max(0.0, min(100.0, float(body.get("amc", 50.0))))
    inflow_m3s = float(body.get("inflow_m3s", 0.0))
    inflow_edge = body.get("inflow_edge", "N")
    inflow_duration_hr = float(body.get("inflow_duration_hr", 0.0))
    # A real detected river crossing point (from /area/river) -- when
    # present, the inflow is concentrated into a real channel-width
    # segment there instead of smeared across the whole domain edge (see
    # flood_model.py's --inflow-pos-m/--inflow-width-m and the CLAUDE.md
    # entry on why the whole-edge behaviour under-delivers a realistic
    # overflow). None/None keeps the original whole-edge behaviour for a
    # manually-picked edge with no detected crossing.
    inflow_lon = body.get("inflow_lon")
    inflow_lat = body.get("inflow_lat")
    inflow_width_m = body.get("inflow_width_m")

    dem_source_for_key, _ = current_dem_source()
    key = polygon_geometry_hash({"geometry": geom, "rain": rain_mm_hr, "dur": duration_hr,
                                  "dem": dem_source_for_key, "buildings": include_buildings,
                                  "friction": friction, "cfl": cfl, "infiltration": infiltration_mm_hr,
                                  "amc": amc, "inflow": inflow_m3s, "inflow_edge": inflow_edge,
                                  "inflow_duration": inflow_duration_hr,
                                  "inflow_lon": inflow_lon, "inflow_lat": inflow_lat,
                                  "inflow_width_m": inflow_width_m})
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
    dem_source, _ = current_dem_source()
    dem_ascii_path = os.path.join(run_dir, "aoi.dem.ascii")
    try:
        # ARC ASCII sample DEM has no embedded CRS (unlike a real GeoTIFF
        # upload, which will) -- known to be UTM 42N from prior work.
        fallback_crs = "EPSG:32642" if dem_source == FALLBACK_DEM else None
        header, active_cells, total_cells = crop_dem_to_polygon(
            dem_source, geom, dem_ascii_path, fallback_src_crs=fallback_crs)
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
                   "amc": amc, "inflow_m3s": inflow_m3s, "inflow_edge": inflow_edge,
                   "inflow_duration_hr": inflow_duration_hr, "inflow_lon": inflow_lon,
                   "inflow_lat": inflow_lat, "inflow_width_m": inflow_width_m}, f)

    with RUN_STATE_LOCK:
        RUN_STATE[run_id] = {"status": "queued", "stage": "buildings"}
    threading.Thread(target=execute_fis_run,
                      args=(run_id, run_dir, dem_ascii_path, rain_mm_hr, duration_hr,
                            include_buildings, src_crs, lon_min, lat_min, lon_max, lat_max),
                      kwargs=dict(friction=friction, cfl=cfl, infiltration_mm_hr=infiltration_mm_hr, amc=amc,
                                  inflow_m3s=inflow_m3s, inflow_edge=inflow_edge,
                                  inflow_duration_hr=inflow_duration_hr, inflow_lon=inflow_lon,
                                  inflow_lat=inflow_lat, inflow_width_m=inflow_width_m, geom=geom),
                      daemon=True).start()
    return jsonify({"run_id": run_id, "cached": False, "grid": f"{header['ncols']}x{header['nrows']}",
                    "active_cells": active_cells})


@app.route("/runs/<run_id>/infra.json")
def run_infra(run_id):
    path = os.path.join(FIS_CACHE, run_id, "infra_risk.json")
    if not os.path.exists(path):
        abort(404)
    with open(path) as f:
        return jsonify(json.load(f))



LULC_COLORS = {
    "Tree cover": [0.13, 0.40, 0.16], "Shrubland": [0.60, 0.55, 0.25], "Grassland": [0.63, 0.75, 0.40],
    "Cropland": [0.90, 0.75, 0.25], "Built-up": [0.75, 0.15, 0.15], "Bare / sparse vegetation": [0.70, 0.65, 0.55],
    "Snow and ice": [0.9, 0.9, 0.95], "Permanent water bodies": [0.15, 0.35, 0.75],
    "Herbaceous wetland": [0.3, 0.6, 0.55], "Mangroves": [0.1, 0.5, 0.4], "Moss and lichen": [0.55, 0.55, 0.4],
}


@app.route("/runs/<run_id>/damage3d.html")
def run_damage3d(run_id):
    """Tab 5: 3D terrain + LULC color drape + hotspot cells highlighted."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    lulc_path = os.path.join(run_dir, "lulc.npy")
    hotspot_path = os.path.join(run_dir, "hotspot_mask.npy")
    if not os.path.exists(lulc_path):
        abort(404)  # damage.json hasn't been requested yet -- Tab 5 fetches it first
    bare_dem_path = os.path.join(run_dir, "dem_bare.ascii")
    dem_path = bare_dem_path if os.path.exists(bare_dem_path) else os.path.join(run_dir, "aoi.dem.ascii")
    header, dem = _read_ascii(dem_path)
    lulc = np.load(lulc_path)
    hotspot = np.load(hotspot_path) if os.path.exists(hotspot_path) else None
    aoi_poly_rc = None
    meta_path = os.path.join(run_dir, "run_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        if meta.get("geometry"):
            aoi_poly_rc = _polygon_to_grid_rc(meta["geometry"], header, meta["src_crs"])

    rgb = np.zeros((*lulc.shape, 3), dtype="float32")
    for code, label in WORLDCOVER_CLASSES.items():
        rgb[lulc == code] = LULC_COLORS.get(label, [0.5, 0.5, 0.5])
    if hotspot is not None:
        rgb[hotspot == 1] = [1.0, 0.15, 0.85]  # magenta: statistically significant damage hotspot

    legend = ("<b>Land cover (ESA WorldCover)</b><br>" +
              "".join(f'<span style="color:rgb({int(c[0]*255)},{int(c[1]*255)},{int(c[2]*255)})">&#9632;</span> {l} &nbsp;'
                      for l, c in LULC_COLORS.items()) +
              '<br><br><span style="color:#ff26d9">&#9632;</span> Statistically significant damage hotspot (Getis-Ord Gi*)')
    out_path = os.path.join(run_dir, "damage3d.html")
    build_3d_scene(out_path, "Damage & Hotspots 3D", header, dem,
                    color_overlay={"rgb": rgb, "opacity": 0.75}, legend_html=legend,
                    aoi_poly_rc=aoi_poly_rc)
    with open(out_path) as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/runs/<run_id>/damage.json")
def run_damage(run_id):
    """Tab 5: real LULC (ESA WorldCover) + published depth-damage curves
    (Huizinga/JRC) + real Getis-Ord Gi* hotspot analysis. Computed lazily on
    first request and cached to disk, since it's a heavier geoprocessing
    step than the core flood run."""
    run_dir = os.path.join(FIS_CACHE, run_id)
    cache_path = os.path.join(run_dir, "damage.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return jsonify(json.load(f))

    meta_path = os.path.join(run_dir, "run_meta.json")
    max_path = os.path.join(run_dir, "results", "res.max")
    if not os.path.exists(meta_path) or not os.path.exists(max_path):
        abort(404)
    with open(meta_path) as f:
        meta = json.load(f)
    header, max_depth = _read_ascii(max_path)
    valid = max_depth != -9999

    try:
        lulc = fetch_lulc_for_bounds(*meta["bbox"], header, meta["src_crs"])
        damage, class_summary = compute_damage(lulc, np.where(valid, max_depth, 0), valid_mask=valid)
        hotspot = getis_ord_hotspots(damage, valid)
        np.save(os.path.join(run_dir, "lulc.npy"), lulc)
        np.save(os.path.join(run_dir, "damage.npy"), damage)
        np.save(os.path.join(run_dir, "hotspot_mask.npy"), hotspot["hotspot_mask"])
        result = {
            "class_summary": class_summary,
            "hotspot_cells": hotspot["hotspot_cells"],
            "coldspot_cells": hotspot["coldspot_cells"],
            "total_valid_cells": int(valid.sum()),
        }
    except Exception as e:
        result = {"error": str(e)}

    with open(cache_path, "w") as f:
        json.dump(result, f)
    return jsonify(result)


@app.route("/runs/<run_id>/progress.json")
def run_progress(run_id):
    with RUN_STATE_LOCK:
        st = dict(RUN_STATE.get(run_id, {}))
    if not st:
        abort(404)
    resp = {"status": st["status"], "stage": st.get("stage", "dem"), "stages": RUN_STAGES}
    run_dir = os.path.join(FIS_CACHE, run_id)
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
    return jsonify(resp)


def execute_protection_run(protect_id, protect_dir, base_run_dir, structures, meta):
    with SIM_SLOT:
        with PROTECT_STATE_LOCK:
            PROTECT_STATE[protect_id]["status"] = "running"
        log_path = os.path.join(protect_dir, "run.log")
        src_crs = meta["src_crs"]
        to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform

        # Physics runs on the same building-conditioned DEM the baseline run
        # used (so buildings still block flow exactly as in the baseline),
        # with structures added on top.
        header, dem = _read_ascii(os.path.join(base_run_dir, "aoi.dem.ascii"))
        modified, structure_mask, structure_kind, applied = apply_structures_to_dem(
            header, dem, structures, to_src)
        dem_ascii_path = os.path.join(protect_dir, "aoi.dem.ascii")
        _write_ascii(dem_ascii_path, header, modified)
        np.save(os.path.join(protect_dir, "structure_kind.npy"), structure_kind)

        # 3D terrain uses the bare (pre-building), UNMODIFIED DEM -- with
        # structures rendered as their own discrete extruded objects
        # (structure_footprints, below) instead of baked into the terrain's
        # own height. Baking a structure into the terrain mesh made a dam
        # render as a raised welt fused into the ground shape and a canal
        # as a trough cut into it -- the same "buildings render as terrain
        # blobs" problem this app already fixed for buildings (see
        # CLAUDE.md), just never applied to Tab 6's own structures. The
        # PHYSICS DEM (dem_ascii_path, above) is unaffected by this -- it's
        # still fully conditioned, since that's what actually makes the
        # structure block/route flow in the simulation.
        bare_dem_path = os.path.join(base_run_dir, "dem_bare.ascii")
        if os.path.exists(bare_dem_path):
            bare_header, bare_dem = _read_ascii(bare_dem_path)
        else:
            bare_header, bare_dem = header, dem
        aoi_polygon_src = None
        if meta.get("geometry"):
            from shapely.ops import transform as shapely_transform
            aoi_polygon_src = shapely_transform(to_src, shapely_shape(meta["geometry"]))
        struct_footprints = structure_footprints(bare_header, structures, to_src, aoi_polygon_src=aoi_polygon_src)

        cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_ascii_path,
               "--rain-mm-hr", str(meta["rain_mm_hr"]), "--duration-hr", str(meta["duration_hr"]),
               "--friction", str(meta.get("friction", 0.05)), "--cfl", str(meta.get("cfl", 0.7)),
               "--infiltration-mm-hr", str(meta.get("infiltration_mm_hr", 0.0)), "--amc", str(meta.get("amc", 50.0)),
               "--inflow-m3s", str(meta.get("inflow_m3s", 0.0)), "--inflow-edge", meta.get("inflow_edge", "N"),
               "--inflow-duration-hr", str(meta.get("inflow_duration_hr", 0.0)),
               "--site-name", "FIS protection scenario", "--workdir", protect_dir]
        # Reuse the baseline run's own real per-cell Manning's n grid (same
        # DEM grid, same real land cover) instead of re-fetching -- a
        # protection scenario changes the terrain/structures, not the land
        # cover, so the baseline's own friction grid still applies exactly.
        baseline_manning_path = os.path.join(base_run_dir, "manning.ascii")
        if os.path.exists(baseline_manning_path):
            cmd += ["--manning-file", baseline_manning_path]
        # Reuse the baseline run's own detected river-crossing point (if it
        # had one), same conversion as execute_fis_run -- a protection
        # scenario should concentrate its inflow at the same real channel
        # location the baseline did, not silently fall back to whole-edge.
        if meta.get("inflow_m3s", 0.0) > 0 and meta.get("inflow_lon") is not None and meta.get("inflow_lat") is not None:
            inflow_pos_m = _snap_inflow_to_valid_edge(header, dem, meta.get("inflow_edge", "N"),
                                                       meta["inflow_lon"], meta["inflow_lat"], src_crs)
            if inflow_pos_m is not None:
                cmd += ["--inflow-pos-m", str(inflow_pos_m),
                        "--inflow-width-m", str(meta.get("inflow_width_m") or 30.0)]
        with open(log_path, "a") as logf:
            proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)

        if proc.returncode != 0:
            with open(log_path) as fh:
                tail = fh.read()[-6000:]
            with PROTECT_STATE_LOCK:
                PROTECT_STATE[protect_id].update(status="failed", log_tail=tail)
            return

        try:
            _, base_max_depth = _read_ascii(os.path.join(base_run_dir, "results", "res.max"))
            _, prot_max_depth = _read_ascii(os.path.join(protect_dir, "results", "res.max"))
            valid = (base_max_depth != -9999) & (prot_max_depth != -9999)
            comparison = compare_flood_results(base_max_depth, prot_max_depth, valid)
            np.save(os.path.join(protect_dir, "diff_mask.npy"), comparison.pop("diff_mask"))

            water = _read_water_stack(protect_dir, rain_mm_hr=meta.get("rain_mm_hr"), duration_hr=meta.get("duration_hr"))
            legend = ("<b>Structures</b> (shown as real objects on the unmodified terrain): " +
                      "".join(f'<span style="color:{STRUCTURE_TYPES[a["type"]]["color"]}">&#9632;</span> {a["label"]} &nbsp;'
                              for a in applied) +
                      "<br><b>Before/after:</b> <span style=\"color:#2ecc71\">&#9632;</span> protected (now dry) &nbsp;"
                      "<span style=\"color:#e74c3c\">&#9632;</span> newly flooded (displaced)")
            diff_mask = np.load(os.path.join(protect_dir, "diff_mask.npy"))
            rgb = np.zeros((*diff_mask.shape, 3), dtype="float32")
            rgb[diff_mask == 1] = [0.18, 0.80, 0.44]
            rgb[diff_mask == 2] = [0.91, 0.30, 0.24]
            rgb[diff_mask == 3] = [0.22, 0.53, 0.90]
            aoi_poly_rc = _polygon_to_grid_rc(meta["geometry"], bare_header, src_crs) if meta.get("geometry") else None
            build_3d_scene(os.path.join(protect_dir, "protect3d.html"), "Protection scenario 3D",
                            bare_header, bare_dem, color_overlay={"rgb": rgb, "opacity": 0.75},
                            building_footprints=struct_footprints,
                            water=water, legend_html=legend, aoi_poly_rc=aoi_poly_rc)

            with open(os.path.join(protect_dir, "result.json"), "w") as f:
                json.dump({"comparison": comparison, "structures": applied}, f)
            with PROTECT_STATE_LOCK:
                PROTECT_STATE[protect_id].update(status="done", comparison=comparison, structures=applied)
        except Exception as e:
            with open(log_path, "a") as logf:
                logf.write(f"[protect] comparison/3d build failed: {e}\n")
            with PROTECT_STATE_LOCK:
                PROTECT_STATE[protect_id].update(status="failed", log_tail=str(e))


@app.route("/protect/run", methods=["POST"])
def protect_run():
    """Tab 6: re-runs the flood model with user-placed protective
    infrastructure (dams/barrages/canals/drainage) conditioned into the same
    baseline DEM, then compares flooded area/depth before vs after -- per
    CLAUDE.md's DEM-conditioning approach already used for buildings."""
    body = request.json
    base_run_id = body.get("run_id")
    structures = body.get("structures", [])
    if not base_run_id or not structures:
        return jsonify({"error": "missing run_id or structures"}), 400
    base_run_dir = os.path.join(FIS_CACHE, base_run_id)
    meta_path = os.path.join(base_run_dir, "run_meta.json")
    if not os.path.exists(meta_path) or not os.path.exists(os.path.join(base_run_dir, "results", "res.max")):
        return jsonify({"error": "baseline run not found or not finished"}), 400
    with open(meta_path) as f:
        meta = json.load(f)

    key = hashlib.sha1(json.dumps({"base": base_run_id, "structures": structures},
                                   sort_keys=True).encode()).hexdigest()
    protect_dir = os.path.join(PROTECT_CACHE, key)
    result_path = os.path.join(protect_dir, "result.json")
    if os.path.exists(result_path):
        with open(result_path) as f:
            result = json.load(f)
        with PROTECT_STATE_LOCK:
            PROTECT_STATE[key] = {"status": "done", **result}
        return jsonify({"protect_id": key, "cached": True})

    os.makedirs(protect_dir, exist_ok=True)
    with PROTECT_STATE_LOCK:
        PROTECT_STATE[key] = {"status": "queued"}
    threading.Thread(target=execute_protection_run,
                      args=(key, protect_dir, base_run_dir, structures, meta), daemon=True).start()
    return jsonify({"protect_id": key, "cached": False})


@app.route("/protect/<protect_id>/progress.json")
def protect_progress(protect_id):
    with PROTECT_STATE_LOCK:
        st = dict(PROTECT_STATE.get(protect_id, {}))
    if not st:
        abort(404)
    return jsonify(st)


def execute_protection_optimize(opt_id, base_run_dir, meta, structure_types, n_structures, max_evals, seed):
    """Real mystic-driven search: each trial re-runs the actual LISFLOOD-FP
    physics (protection_optimizer._run_trial), never a fitted/cheap
    surrogate. Once the search settles on a best candidate, hands its exact
    structures[] off to the SAME execute_protection_run pipeline a manually-
    drawn scenario uses, so the final result gets the real 3D scene, water
    animation, and diff mask -- no separate/duplicated render path."""
    with SIM_SLOT:
        with OPTIMIZE_STATE_LOCK:
            OPTIMIZE_STATE[opt_id]["status"] = "searching"

        def progress_cb(i, total, score, comparison):
            with OPTIMIZE_STATE_LOCK:
                if OPTIMIZE_STATE.get(opt_id, {}).get("status") == "cancelled":
                    return
                OPTIMIZE_STATE[opt_id].update(
                    trial=i, total=total, best_score=min(
                        score, OPTIMIZE_STATE[opt_id].get("best_score", score)),
                    last_reduction_pct=None if comparison is None else comparison["reduction_pct"],
                    last_displacement_pct=None if comparison is None else comparison["displacement_pct"])

        try:
            best_structures, best_summary, trial_log = optimize_structures(
                base_run_dir, meta, structure_types, n_structures, max_evals,
                VENV_PY, FLOOD_MODEL, seed=seed, progress_cb=progress_cb)
        except Exception as e:
            with OPTIMIZE_STATE_LOCK:
                OPTIMIZE_STATE[opt_id].update(status="failed", log_tail=str(e))
            return

        if not best_structures:
            with OPTIMIZE_STATE_LOCK:
                OPTIMIZE_STATE[opt_id].update(
                    status="failed",
                    log_tail="No candidate placement had any effect inside the AOI -- "
                             "every trial either failed or rasterized to zero affected cells.")
            return

        with OPTIMIZE_STATE_LOCK:
            OPTIMIZE_STATE[opt_id].update(status="finalizing", chosen_structures=best_structures,
                                           search_summary=best_summary)

    # Final confirmation run: the SAME manual pipeline, so this scenario
    # shows up in PROTECT_STATE/the 3D view exactly like a hand-drawn one.
    protect_key = hashlib.sha1(json.dumps({"base": os.path.basename(base_run_dir),
                                            "structures": best_structures},
                                           sort_keys=True).encode()).hexdigest()
    protect_dir = os.path.join(PROTECT_CACHE, protect_key)
    result_path = os.path.join(protect_dir, "result.json")
    if os.path.exists(result_path):
        with open(result_path) as f:
            result = json.load(f)
        with PROTECT_STATE_LOCK:
            PROTECT_STATE[protect_key] = {"status": "done", **result}
    else:
        os.makedirs(protect_dir, exist_ok=True)
        with PROTECT_STATE_LOCK:
            PROTECT_STATE[protect_key] = {"status": "queued"}
        execute_protection_run(protect_key, protect_dir, base_run_dir, best_structures, meta)

    with OPTIMIZE_STATE_LOCK:
        OPTIMIZE_STATE[opt_id].update(status="done", protect_id=protect_key,
                                       chosen_structures=best_structures, search_summary=best_summary,
                                       n_trials=len(trial_log))


@app.route("/protect/optimize", methods=["POST"])
def protect_optimize():
    """Auto-placement: searches structure geometry (position/orientation/
    length/size, and type when more than one is allowed) with mystic's real
    differential-evolution global optimizer instead of the user manually
    drawing a line and guessing. See protection_optimizer.py for the full
    method and why each evaluation is a real (not surrogate) physics run."""
    body = request.json
    base_run_id = body.get("run_id")
    structure_types = [t for t in body.get("structure_types", ["drainage"]) if t in STRUCTURE_TYPES]
    n_structures = max(1, min(3, int(body.get("n_structures", 1))))
    max_evals = max(6, min(60, int(body.get("max_evals", 20))))
    if not base_run_id or not structure_types:
        return jsonify({"error": "missing run_id or structure_types"}), 400
    base_run_dir = os.path.join(FIS_CACHE, base_run_id)
    meta_path = os.path.join(base_run_dir, "run_meta.json")
    if not os.path.exists(meta_path) or not os.path.exists(os.path.join(base_run_dir, "results", "res.max")):
        return jsonify({"error": "baseline run not found or not finished"}), 400
    with open(meta_path) as f:
        meta = json.load(f)
    if not meta.get("geometry"):
        return jsonify({"error": "baseline run has no saved AOI geometry (re-run it to pick up auto-optimize support)"}), 400

    opt_id = hashlib.sha1(json.dumps({"base": base_run_id, "types": structure_types,
                                       "n": n_structures, "evals": max_evals},
                                      sort_keys=True).encode()).hexdigest()
    seed = int(opt_id[:8], 16) % (2 ** 31)  # deterministic: identical requests reproduce the same search
    with OPTIMIZE_STATE_LOCK:
        OPTIMIZE_STATE[opt_id] = {"status": "queued", "trial": 0, "total": max_evals}
    threading.Thread(target=execute_protection_optimize,
                      args=(opt_id, base_run_dir, meta, structure_types, n_structures, max_evals, seed),
                      daemon=True).start()
    return jsonify({"opt_id": opt_id})


@app.route("/protect/optimize/<opt_id>/progress.json")
def protect_optimize_progress(opt_id):
    with OPTIMIZE_STATE_LOCK:
        st = dict(OPTIMIZE_STATE.get(opt_id, {}))
    if not st:
        abort(404)
    return jsonify(st)


@app.route("/protect/<protect_id>/protect3d.html")
def protect_3d(protect_id):
    path = os.path.join(PROTECT_CACHE, protect_id, "protect3d.html")
    if not os.path.exists(path):
        abort(404)
    with open(path) as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/runs/<run_id>/<path:filename>")
def serve_run_file(run_id, filename):
    run_dir = os.path.join(FIS_CACHE, run_id)
    full = os.path.abspath(os.path.join(run_dir, filename))
    if not full.startswith(os.path.abspath(run_dir) + os.sep) or not os.path.exists(full):
        abort(404)
    with open(full, "rb") as fh:
        data = fh.read()
    return Response(data, mimetype="text/html" if full.endswith(".html") else "application/octet-stream")


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
    print(f"[{MODEL_NAME}] running at http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
