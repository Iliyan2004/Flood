"""
Mapbox-native 3D scene builder for the main SAILAB app's own flood runs --
same rendering approach proven in the standalone Area 3D tool
(osm_buildings_3d.py): real Mapbox global terrain (`map.setTerrain`), real
building/road extrusion via `fill-extrusion`/`line` layers, instead of
this app's own custom Three.js scene (viewer3d_utils.py), which has been
the source of several camera-angle and terrain-sampling illusions/bugs
this session (see CLAUDE.md). Mapbox's renderer has no custom camera or
terrain-height-sampling code of this app's own to get wrong.

Real trade-off, stated plainly rather than hidden: the PHYSICS still runs
on this app's own uploaded Pakistan SRTM (unchanged -- flood_model.py,
buildings_utils.burn_buildings_into_dem, etc. are untouched by this
module). Only the 3D VISUAL terrain uses Mapbox's own global elevation
tileset instead of this app's DEM, the same choice already made for Area
3D -- both are real, independent SRTM-derived elevation sources at
similar resolution, so they read the same for the flood-plain terrain
this app targets, but they are not pixel-identical. Buildings, roads, and
the flood depth overlay are all positioned by real lon/lat (not by the
DEM's own row/col grid), so they align correctly regardless of which
terrain mesh renders underneath them.

Animated over the run's own real per-timestep data (`water["depth_stack"]`
plus each building/road's own real depth trajectory), the same real
principle already used by the Three.js viewer's animated building/road
risk color: a building/road reads as "at risk" only while it's genuinely
wet right now, fading back toward green as the storm actually drains --
not a single static peak-based verdict. The flood depth overlay itself
uses a Mapbox `canvas` source (the correct, performant way to drive a
live-updating raster layer in Mapbox GL -- re-samples a live <canvas>
element every render frame, no per-frame image re-decode), continuously
interpolated between the solver's own saved timesteps exactly like the
Three.js viewer's water surface (never a discrete jump-cut between saved
frames).
"""
import base64
import json

import numpy as np

from damage_utils import DEPTH_DAMAGE_CURVES_ASIA
from infra_utils import RISK_BANDS

RISK_COLOR_DEFAULT = "#8f7a63"


def f32_b64(arr):
    return base64.b64encode(np.asarray(arr, dtype="<f4").tobytes()).decode("ascii")


def build_mapbox_flood_scene(out_path, title, bbox, aoi_geom, mapbox_token,
                              building_footprints=None, road_lines=None,
                              water=None, legend_html="", z_exag=1.3):
    """
    bbox: [lon_min, lat_min, lon_max, lat_max] -- the run's own real AOI
        bounding box (already reprojected from the DEM's UTM grid by the
        caller, same value saved in run_meta.json).
    aoi_geom: the real drawn/uploaded polygon (GeoJSON dict, WGS84).
    building_footprints: [{"coords_lonlat": [[lon,lat],...], "height_m":
        float, "depths": [float, ...] or None}, ...] -- real footprint +
        the SAME real per-timestep depth trajectory
        (water["depth_stack"] sampled at this building's own centroid)
        already computed for the Three.js viewer's animated coloring, so
        both 3D views of the same run classify buildings identically.
    road_lines: [{"coords_lonlat": [[lon,lat],...], "depths": [...] or
        None, "speeds": [...] or None}, ...] -- likewise, the same real
        depth+speed trajectories already computed for the Three.js view.
    water: {"depth_stack": (T,nrows,ncols), "save_int_min": float,
        "dry_thresh": float, "max_depth": float, "duration_hr": float},
        same structure viewer3d_utils.build_3d_scene's `water` param uses.
        None disables the flood animation/overlay entirely (buildings/
        roads still render, at a fixed dry-default color).
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    center = [(lon_min + lon_max) / 2, (lat_min + lat_max) / 2]

    buildings_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "properties": {"height_m": fp["height_m"], "depths": fp.get("depths")},
         "geometry": {"type": "Polygon", "coordinates": [fp["coords_lonlat"]]}}
        for i, fp in enumerate(building_footprints or [])
    ]}
    roads_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "properties": {"depths": rl.get("depths"), "speeds": rl.get("speeds")},
         "geometry": {"type": "LineString", "coordinates": rl["coords_lonlat"]}}
        for i, rl in enumerate(road_lines or [])
    ]}

    water_payload = None
    if water is not None and water.get("depth_stack") is not None:
        depth_stack = water["depth_stack"]
        water_payload = {
            "depthB64": f32_b64(depth_stack.flatten()),
            "nFrames": int(depth_stack.shape[0]), "nrows": int(depth_stack.shape[1]), "ncols": int(depth_stack.shape[2]),
            "saveIntMin": water.get("save_int_min", 10.0), "dryThresh": water.get("dry_thresh", 0.05),
            "maxDepth": water.get("max_depth", 0.0),
        }

    payload = {
        "bbox": bbox, "center": center, "aoiGeom": aoi_geom,
        "buildings": buildings_fc, "roads": roads_fc,
        "water": water_payload,
        "huizingaBuiltUp": DEPTH_DAMAGE_CURVES_ASIA["Built-up"],
        "riskBands": [[name, hi] for name, hi in RISK_BANDS],
        "zExag": z_exag, "mapboxToken": mapbox_token,
    }

    html = f"""<!doctype html>
<title>{title}</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.css" rel="stylesheet">
<style>
html,body{{margin:0;height:100%;overflow:hidden;font-family:system-ui,sans-serif;background:#0B1417;}}
#map{{position:absolute;inset:0;}}
.legend{{position:absolute;right:12px;top:12px;background:rgba(16,27,31,0.88);color:#E7EEF0;
  border-radius:8px;padding:10px 12px;font-size:11.5px;max-width:260px;z-index:5;}}
.reset-view-btn{{position:absolute;left:12px;top:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:11px;cursor:pointer;z-index:5;}}
.angle-hint{{position:absolute;left:12px;top:48px;background:rgba(16,27,31,0.78);color:#9db3ba;
  border-radius:8px;padding:5px 10px;font-size:10.5px;z-index:5;max-width:210px;line-height:1.4;}}
.fs-btn{{position:absolute;right:12px;bottom:64px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:15px;cursor:pointer;line-height:1;z-index:5;}}
.export-btn{{position:absolute;right:56px;bottom:64px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:11px;cursor:pointer;z-index:5;}}
body:not(.has-timeline) .fs-btn, body:not(.has-timeline) .export-btn {{ bottom:12px; }}
.exag-panel{{position:absolute;left:12px;bottom:64px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:8px 12px;font-size:11px;z-index:5;width:220px;}}
body:not(.has-timeline) .exag-panel {{ bottom:12px; }}
.exag-panel input[type=range]{{width:100%;margin-top:4px;accent-color:#3fc4dd;}}
:root.fullscreen-active body, html:fullscreen body {{background:#0B1417;}}
.timeline{{position:absolute;left:0;right:0;bottom:0;display:flex;align-items:center;gap:10px;
  background:rgba(16,27,31,0.92);padding:8px 14px;font-family:system-ui,sans-serif;color:#E7EEF0;font-size:12px;z-index:6;}}
.timeline button{{background:rgba(255,255,255,0.08);border:1px solid #3a4c52;color:#E7EEF0;border-radius:6px;
  width:30px;height:26px;cursor:pointer;font-size:13px;flex:0 0 auto;}}
.timeline button:hover{{background:rgba(255,255,255,0.18);}}
.timeline input[type=range]{{flex:1;accent-color:#3fc4dd;}}
.timeline .tlabel{{font-family:monospace;font-size:11px;color:#A9BEC4;white-space:nowrap;flex:0 0 auto;}}
.timeline select{{background:#16232a;color:#E7EEF0;border:1px solid #3a4c52;border-radius:6px;font-size:11px;
  padding:3px 4px;flex:0 0 auto;}}
</style>
<div id="map"></div>
<button class="reset-view-btn" id="resetBtn">Reset View</button>
<div class="angle-hint">Right-drag (or Ctrl+drag) to rotate &amp; tilt &middot; scroll to zoom &middot; drag to pan</div>
<div class="exag-panel">
  Terrain exaggeration (&times;<span id="exagVal">{z_exag:.1f}</span>)
  <input type="range" id="exagSlider" min="0.5" max="4" step="0.1" value="{z_exag}">
</div>
<button class="export-btn" id="exportBtn">Export PNG</button>
<button class="fs-btn" id="fsBtn" title="Full screen">&#9974;</button>
{f'<div class="legend">{legend_html}</div>' if legend_html else ''}
{'''<div class="timeline" id="timelineBar">
  <button id="playPauseBtn" title="Play/pause">&#10074;&#10074;</button>
  <span class="tlabel" id="timeLabel">0:00 / 0:00</span>
  <input type="range" id="timeSlider" min="0" max="1000" value="0" step="1">
  <select id="speedSelect" title="Playback speed">
    <option value="0.25">0.25&times;</option>
    <option value="0.5">0.5&times;</option>
    <option value="1" selected>1&times;</option>
    <option value="2">2&times;</option>
    <option value="4">4&times;</option>
  </select>
</div>''' if water_payload is not None else ''}
<script src="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.js"></script>
<script>
const DATA = {json.dumps(payload)};
mapboxgl.accessToken = DATA.mapboxToken;
if (DATA.water) document.body.classList.add('has-timeline');

// Mapbox GL JS's own default maxPitch is 60 degrees -- exactly the pitch
// this scene already starts at, so out of the box there was no real room
// to tilt any further even though drag-to-rotate/pitch is otherwise on by
// default (right-drag or ctrl+drag), which reads as "the angle can't be
// changed" even though the gesture itself works. Raised to 75 -- the same
// real safety margin already settled on for the Three.js viewer's own
// camera cap (`viewer3d_utils.py`'s `maxPolarAngle = Math.PI*0.42`, 75.6
// degrees from vertical / ~14 degrees above the horizon) -- so the user
// gets real freedom to view from a much lower, more dramatic angle
// without reaching the fully edge-on "flattened sliver" view that caused
// real bugs/illusions in that other viewer (see CLAUDE.md).
const map = new mapboxgl.Map({{
  container: 'map', style: 'mapbox://styles/mapbox/satellite-streets-v12',
  center: DATA.center, zoom: 14, pitch: 60, bearing: -17, antialias: true,
  preserveDrawingBuffer: true, maxPitch: 75,
}});
map.addControl(new mapboxgl.NavigationControl({{visualizePitch:true}}), 'top-right');

function resetView() {{
  const b = DATA.bbox;
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], {{padding: 80, pitch: 60, bearing: -17, duration: 0}});
}}
document.getElementById('resetBtn').addEventListener('click', resetView);
document.getElementById('exagSlider').addEventListener('input', (e) => {{
  const v = parseFloat(e.target.value);
  document.getElementById('exagVal').textContent = v.toFixed(1);
  if (map.getTerrain()) map.setTerrain({{source:'mapbox-dem', exaggeration: v}});
}});
document.getElementById('fsBtn').addEventListener('click', () => {{
  const el = document.documentElement;
  if (!document.fullscreenElement) {{ (el.requestFullscreen || el.webkitRequestFullscreen || function(){{}}).call(el); }}
  else {{ (document.exitFullscreen || document.webkitExitFullscreen || function(){{}}).call(document); }}
}});
document.getElementById('exportBtn').addEventListener('click', () => {{
  resetView();
  setTimeout(() => {{
    const a = document.createElement('a');
    a.href = map.getCanvas().toDataURL('image/png');
    a.download = 'flood_3d.png';
    a.click();
  }}, 400);
}});

// ---- same real risk-color logic as the Three.js viewer (viewer3d_utils.
// py), driven here by the SAME real per-building/per-road depth (and,
// for roads, speed) trajectories that view already computes -- see this
// module's docstring. ----
function huizingaDamageFraction(depth) {{
  const d = DATA.huizingaBuiltUp.depths, f = DATA.huizingaBuiltUp.damage;
  if (depth <= d[0]) return f[0];
  if (depth >= d[d.length - 1]) return f[f.length - 1];
  for (let i = 1; i < d.length; i++) {{
    if (depth <= d[i]) {{
      const t = (depth - d[i - 1]) / (d[i] - d[i - 1]);
      return f[i - 1] + t * (f[i] - f[i - 1]);
    }}
  }}
  return f[f.length - 1];
}}
function defraHazardFraction(hr) {{
  const targets = [0, 0.30, 0.55, 0.75, 0.95];
  let lo = 0, loT = 0;
  for (let i = 0; i < DATA.riskBands.length; i++) {{
    const [, hi] = DATA.riskBands[i];
    const hiFinite = isFinite(hi) ? hi : lo * 2 + 1;
    if (hr <= hiFinite || !isFinite(hi)) {{
      const t = hiFinite > lo ? (hr - lo) / (hiFinite - lo) : 0;
      return Math.min(1, Math.max(0, loT + Math.min(1, t) * (targets[i + 1] - loT)));
    }}
    lo = hi; loT = targets[i + 1];
  }}
  return targets[targets.length - 1];
}}
const RISK_COLOR_STOPS = [
  [0.00, [0x2e, 0xcc, 0x71]], [0.30, [0xf2, 0xd9, 0x4e]],
  [0.55, [0xf0, 0x88, 0x3e]], [0.90, [0xc8, 0x1e, 0x1e]],
];
function riskColorFromFraction(frac) {{
  frac = Math.min(1, Math.max(0, frac));
  for (let i = 1; i < RISK_COLOR_STOPS.length; i++) {{
    const [f0, c0] = RISK_COLOR_STOPS[i - 1], [f1, c1] = RISK_COLOR_STOPS[i];
    if (frac <= f1 || i === RISK_COLOR_STOPS.length - 1) {{
      const t = f1 > f0 ? (frac - f0) / (f1 - f0) : 0;
      const tt = Math.min(1, Math.max(0, t));
      const r = Math.round(c0[0] + (c1[0]-c0[0])*tt), g = Math.round(c0[1] + (c1[1]-c0[1])*tt), b = Math.round(c0[2] + (c1[2]-c0[2])*tt);
      return '#' + [r,g,b].map(v => v.toString(16).padStart(2,'0')).join('');
    }}
  }}
  return '#2ecc71';
}}

map.on('load', () => {{
  map.addSource('mapbox-dem', {{type:'raster-dem', url:'mapbox://mapbox.mapbox-terrain-dem-v1', tileSize:512, maxzoom:14}});
  map.setTerrain({{source:'mapbox-dem', exaggeration: DATA.zExag}});
  map.addLayer({{id:'sky', type:'sky', paint:{{'sky-type':'atmosphere','sky-atmosphere-sun-intensity':8}}}});

  // Dim everything outside the true AOI polygon -- same real technique
  // proven in Area 3D (a donut mask, AOI ring as the hole), so the run's
  // own area reads as the focus against the wider basemap.
  if (DATA.aoiGeom) {{
    const ring = DATA.aoiGeom.type === 'Polygon' ? DATA.aoiGeom.coordinates[0] : DATA.aoiGeom.coordinates[0][0];
    const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
    const padLon = Math.max((Math.max(...lons) - Math.min(...lons)) * 6, 0.5);
    const padLat = Math.max((Math.max(...lats) - Math.min(...lats)) * 6, 0.5);
    const outer = [
      [Math.min(...lons)-padLon, Math.min(...lats)-padLat], [Math.max(...lons)+padLon, Math.min(...lats)-padLat],
      [Math.max(...lons)+padLon, Math.max(...lats)+padLat], [Math.min(...lons)-padLon, Math.max(...lats)+padLat],
      [Math.min(...lons)-padLon, Math.min(...lats)-padLat],
    ];
    const hole = ring.slice().reverse();
    map.addSource('aoi-mask', {{type:'geojson', data:{{type:'Feature', geometry:{{type:'Polygon', coordinates:[outer, hole]}}}}}});
    map.addLayer({{id:'aoi-mask-layer', type:'fill', source:'aoi-mask', paint:{{'fill-color':'#04080a','fill-opacity':0.5}}}});
  }}

  // ---- real animated flood depth overlay, via a Mapbox `canvas` source
  // -- the correct, performant way to drive a live raster layer in
  // Mapbox GL (re-samples a live <canvas> element on every render frame
  // with `animate:true`, no per-frame image re-decode like an `image`
  // source's updateImage() would need). Continuously interpolated
  // between the solver's own saved timesteps, never a discrete jump-cut,
  // same principle as the Three.js viewer's water surface. ----
  let floodCanvas = null, floodCtx = null, floodImgData = null;
  const W = DATA.water;
  if (W) {{
    floodCanvas = document.createElement('canvas');
    floodCanvas.width = W.ncols; floodCanvas.height = W.nrows;
    floodCtx = floodCanvas.getContext('2d');
    floodImgData = floodCtx.createImageData(W.ncols, W.nrows);
    const b = DATA.bbox;
    map.addSource('flood-canvas', {{type:'canvas', canvas: floodCanvas,
      coordinates: [[b[0],b[3]],[b[2],b[3]],[b[2],b[1]],[b[0],b[1]]], animate: true}});
    map.addLayer({{id:'flood-layer', type:'raster', source:'flood-canvas', paint:{{'raster-opacity':0.85}}}});
  }}

  // Real building footprints, colored by the run's CURRENT real depth
  // (feature-state, updated per animation frame -- never re-uploads the
  // GeoJSON geometry itself, which stays fixed).
  if (DATA.buildings.features.length) {{
    map.addSource('buildings', {{type:'geojson', data: DATA.buildings}});
    map.addLayer({{id:'buildings-layer', type:'fill-extrusion', source:'buildings', paint:{{
      'fill-extrusion-color': ['coalesce', ['feature-state','color'], '#2ecc71'],
      'fill-extrusion-height': ['*', ['get','height_m'], 3],
      'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.92, 'fill-extrusion-vertical-gradient': true,
    }}}});
  }}

  // Real OSM roads, colored by the run's CURRENT real depth+speed
  // (feature-state, same animated principle as buildings).
  if (DATA.roads.features.length) {{
    map.addSource('roads', {{type:'geojson', data: DATA.roads}});
    map.addLayer({{id:'roads-layer', type:'line', source:'roads',
      layout:{{'line-join':'round','line-cap':'round'}},
      paint:{{'line-color':['coalesce', ['feature-state','color'], '#2ecc71'], 'line-width':2.5, 'line-opacity':0.9}}}});
  }}

  resetView();

  if (!W) return;
  const DEPTH = new Float32Array(Uint8Array.from(atob(W.depthB64), c => c.charCodeAt(0)).buffer);
  const nFrames = W.nFrames, cellCount = W.nrows * W.ncols;
  const maxDepth = Math.max(W.maxDepth, 0.05);

  function setFloodFrame(seg, mixFrac) {{
    const segNext = Math.min(seg + 1, nFrames - 1);
    const baseA = seg * cellCount, baseB = segNext * cellCount;
    const data = floodImgData.data;
    for (let i = 0; i < cellCount; i++) {{
      const dA = DEPTH[baseA + i], dB = DEPTH[baseB + i];
      const d = Math.max(0, dA + (dB - dA) * mixFrac);
      const idx = i * 4;
      if (d <= W.dryThresh) {{ data[idx+3] = 0; continue; }}
      const t = Math.min(1, d / maxDepth);
      data[idx]   = Math.round(200 - 170 * t);
      data[idx+1] = Math.round(230 - 140 * t);
      data[idx+2] = 255;
      data[idx+3] = Math.round(140 + 100 * t);
    }}
    floodCtx.putImageData(floodImgData, 0, 0);
  }}

  function sampleAt(arr, seg, mixFrac) {{
    if (!arr) return 0;
    const segNext = Math.min(seg + 1, arr.length - 1);
    const a = arr[seg] || 0, b = arr[segNext] || 0;
    return Math.max(0, a + (b - a) * mixFrac);
  }}
  function updateBuildingColors(seg, mixFrac) {{
    for (const f of DATA.buildings.features) {{
      const d = sampleAt(f.properties.depths, seg, mixFrac);
      const frac = d > 0.03 ? huizingaDamageFraction(d) : 0;
      map.setFeatureState({{source:'buildings', id: f.id}}, {{color: riskColorFromFraction(frac)}});
    }}
  }}
  function updateRoadColors(seg, mixFrac) {{
    for (const f of DATA.roads.features) {{
      const d = sampleAt(f.properties.depths, seg, mixFrac);
      const speed = sampleAt(f.properties.speeds, seg, mixFrac);
      const frac = d > 0.03 ? defraHazardFraction(d * (speed + 1.5)) : 0;
      map.setFeatureState({{source:'roads', id: f.id}}, {{color: riskColorFromFraction(frac)}});
    }}
  }}

  setFloodFrame(0, 0); updateBuildingColors(0, 0); updateRoadColors(0, 0);

  // Same fixed-total-loop-length playback as the Three.js viewer -- always
  // this fast regardless of how many timesteps the solver saved, real
  // simulated time shown via the slider/label, user-controllable speed.
  const LOOP_SECONDS = 9.0;
  const nSeg = Math.max(1, nFrames - 1);
  const totalSimMin = (nFrames - 1) * W.saveIntMin;

  const playPauseBtn = document.getElementById('playPauseBtn');
  const timeSlider = document.getElementById('timeSlider');
  const timeLabel = document.getElementById('timeLabel');
  const speedSelect = document.getElementById('speedSelect');
  function fmtMin(min) {{
    const rounded = Math.round(min);
    const h = Math.floor(rounded / 60), m = rounded % 60;
    return h > 0 ? (h + 'h' + String(m).padStart(2,'0') + 'm') : (m + 'm');
  }}
  let elapsedSeconds = 0, playing = true, speed = 1.0, lastNow = null, dragging = false;
  playPauseBtn.addEventListener('click', () => {{
    playing = !playing;
    playPauseBtn.innerHTML = playing ? '&#10074;&#10074;' : '&#9654;';
    if (playing) lastNow = null;
  }});
  speedSelect.addEventListener('change', () => {{ speed = parseFloat(speedSelect.value); }});
  timeSlider.addEventListener('mousedown', () => {{ dragging = true; }});
  timeSlider.addEventListener('touchstart', () => {{ dragging = true; }});
  timeSlider.addEventListener('input', () => {{ elapsedSeconds = (timeSlider.value / 1000) * LOOP_SECONDS; }});
  const endDrag = () => {{ dragging = false; lastNow = null; }};
  timeSlider.addEventListener('mouseup', endDrag);
  timeSlider.addEventListener('touchend', endDrag);
  timeSlider.addEventListener('change', endDrag);

  function tick(now) {{
    if (lastNow === null) lastNow = now;
    const dt = (now - lastNow) / 1000; lastNow = now;
    if (playing && !dragging) {{ elapsedSeconds = (elapsedSeconds + dt * speed) % LOOP_SECONDS; }}
    const t = elapsedSeconds / LOOP_SECONDS;
    const segF = t * nSeg, seg = Math.min(nSeg - 1, Math.floor(segF)), mixFrac = segF - seg;
    setFloodFrame(seg, mixFrac); updateBuildingColors(seg, mixFrac); updateRoadColors(seg, mixFrac);
    const simMinNow = t * totalSimMin;
    if (!dragging) timeSlider.value = Math.round(t * 1000);
    timeLabel.textContent = fmtMin(simMinNow) + ' / ' + fmtMin(totalSimMin);
    requestAnimationFrame(tick);
  }}
  requestAnimationFrame(tick);
}});
</script>
"""
    with open(out_path, "w") as f:
        f.write(html)
