"""
Standalone tool (separate from the main SAILAB single-page app, at explicit
user request), built up step by step rather than all at once.

Step 1 (this version): draw a polygon anywhere, see real 3D terrain for
that area. Uses Mapbox GL JS's own native `map.setTerrain(...)` on
Mapbox's global raster-DEM tileset -- real, worldwide SRTM-derived
elevation, not this app's own uploaded Pakistan SRTM and not this app's
own custom Three.js terrain mesh (viewer3d_utils.py), which has been the
source of several camera-angle and terrain-sampling bugs this session
(see CLAUDE.md). Mapbox's terrain renderer is a mature, battle-tested
path with no custom camera or terrain-height-sampling code of this app's
own to get wrong.

Step 2 (this version): real building footprints, extruded to their real
(or estimated) height, restricted to just inside the drawn polygon (each
footprint's real centroid is tested against the true drawn ring server-
side, in fis_app.py's /area/buildings route). Footprints come from Google
Open Buildings (fetch_buildings_for_aoi), NOT raw OSM `way["building"]`
data -- checked directly first: the same real test bbox used to verify
this tool has 1,420 real Google Open Buildings footprints vs. only ~100
from OSM's own building ways, a ~14x undercount, so an OSM-only version
of this feature showed "only a few buildings" even in genuinely dense
areas. This is the exact same footprint source the main SAILAB app
already uses for this exact reason. OSM height/`building:levels` tags are
still used, server-side, purely to supplement real height where mapped
(sparse, ~2% coverage in prior testing) -- see /area/buildings in
fis_app.py for the fetch/filter/height logic itself.

Step 3 (this version): real OSM roads for the drawn area, fetched
client-side (unlike buildings, OSM road-network coverage is genuinely
good even where its building-footprint coverage is sparse -- this is the
same real data source `infra_utils.fetch_osm_infrastructure` already uses
for roads elsewhere in this app, just queried directly from the browser
here instead of proxied through the backend). Clipped to the TRUE drawn
polygon, not just its bounding box -- the same real bug (and fix pattern)
already documented in CLAUDE.md for the main app's own road rendering
(`fis_app._lonlat_path_to_segments`): a road can run through a bbox
corner that's outside the drawn shape, so each road is split into
separate segments wherever it leaves the polygon (`splitLineToSegments`)
instead of naively keeping or dropping the whole way by one test.

Later steps (not yet built, per explicit instruction to go step by step):
a real flood simulation via this app's own /run backend.
"""
import json

PAGE_TEMPLATE = r"""<!doctype html>
<title>SAILAB &middot; Area 3D</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.css" rel="stylesheet">
<link href="https://unpkg.com/@mapbox/mapbox-gl-draw@1.4.3/dist/mapbox-gl-draw.css" rel="stylesheet">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root { --bg:#0B1417; --panel:#111C20; --line:#233238; --text:#E7EEF0; --muted:#8fa3a9; --accent:#0E7A8C; --accent-light:#3fc4dd; }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; font-family:"Public Sans",system-ui,sans-serif; background:var(--bg); color:var(--text); overflow:hidden; }
#map { position:absolute; inset:0; }
.panel { position:absolute; top:16px; left:16px; width:300px; background:rgba(17,28,32,0.94); border:1px solid var(--line);
  border-radius:10px; padding:16px; z-index:5; }
.brand { font-family:"Barlow Condensed",sans-serif; font-weight:700; font-size:20px; text-transform:uppercase; letter-spacing:0.5px; }
.brand span { color:var(--accent-light); }
.subtitle { font-size:11.5px; color:var(--muted); margin-top:2px; margin-bottom:12px; line-height:1.4; }
.step { font-size:12.5px; color:var(--text); margin:10px 0 4px; }
.step b { color:var(--accent-light); }
label { display:block; font-size:11px; color:var(--muted); margin-top:8px; }
input[type=range] { width:100%; margin-top:4px; accent-color:var(--accent); }
button { width:100%; padding:9px; margin-top:8px; border-radius:7px; border:1px solid var(--line); background:#16232a;
  color:var(--text); font-size:13px; cursor:pointer; font-family:inherit; }
button.primary { background:var(--accent); border-color:var(--accent); font-weight:600; }
button.primary:disabled { opacity:0.4; cursor:not-allowed; }
button:hover:not(:disabled) { filter:brightness(1.15); }
.note { font-size:11.5px; color:var(--muted); margin-top:8px; line-height:1.4; }
.err { color:#e7736a; font-size:12px; margin-top:8px; display:none; }
.layer-switch { position:absolute; top:16px; right:16px; z-index:5; background:rgba(17,28,32,0.94); border:1px solid var(--line);
  border-radius:8px; overflow:hidden; display:flex; }
.layer-switch button { width:auto; margin:0; border:none; border-radius:0; padding:7px 12px; font-size:11.5px; }
.layer-switch button.active { background:var(--accent); }
.pitch-hint { position:absolute; bottom:16px; left:16px; z-index:5; background:rgba(17,28,32,0.85); border-radius:8px;
  padding:6px 10px; font-size:11px; color:var(--muted); }
</style>
<div id="map"></div>
<div class="layer-switch">
  <button id="layerSat" class="active" onclick="setMapLayer('satellite')">Satellite</button>
  <button id="layerStreets" onclick="setMapLayer('streets')">Streets</button>
</div>
<div class="panel">
  <div class="brand">SAILAB <span>&middot; Area 3D</span></div>
  <div class="subtitle">Real 3D terrain and real OSM buildings, for just the area you draw.</div>
  <div class="step"><b>1.</b> Click the polygon tool (top-right) and draw your area.</div>
  <div class="step"><b>2.</b> View real 3D terrain for it.</div>
  <button class="primary" id="genBtn" disabled onclick="viewTerrain()">View 3D terrain for this area &rarr;</button>
  <label>Terrain exaggeration (&times;<span id="exagVal">1.3</span>)</label>
  <input type="range" id="exagSlider" min="0.5" max="4" step="0.1" value="1.3" oninput="updateExaggeration()">
  <div class="note">Mapbox's terrain renderer applies exaggeration to the whole visible surface (a single global raster elevation source can't be scaled differently in just one spot) -- the area outside your polygon is dimmed instead, so your area still reads as the focus.</div>

  <div class="step"><b>3.</b> Generate real OSM buildings, restricted to inside your polygon.</div>
  <button class="primary" id="buildingsBtn" disabled onclick="generateBuildings()">Generate 3D buildings &rarr;</button>
  <label>Building height exaggeration (&times;<span id="buildingExagVal">3.0</span>)</label>
  <input type="range" id="buildingExagSlider" min="1" max="8" step="0.5" value="3" oninput="updateBuildingExaggeration()">
  <div class="note">Real building heights are mostly 1-2 stories (a few metres) -- at the zoom needed to see your whole drawn area, that's nearly invisible against a real-world scale. This exaggerates displayed height only, for visibility; the real height is still what's reported in the stats below.</div>
  <div class="note" id="buildingsStats" style="display:none;"></div>

  <div class="step"><b>4.</b> Generate real OSM roads, clipped to inside your polygon.</div>
  <button class="primary" id="roadsBtn" disabled onclick="generateRoads()">Generate roads &rarr;</button>
  <div class="note" id="roadsStats" style="display:none;"></div>

  <button id="clearBtn" onclick="clearAll()">Clear</button>
  <div class="err" id="errBox"></div>
  <div class="note" id="hint">No area selected yet.</div>
</div>
<div class="pitch-hint">Drag to orbit &middot; scroll to zoom &middot; shift+drag to pan the 3D angle</div>
<script src="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.js"></script>
<script src="https://unpkg.com/@mapbox/mapbox-gl-draw@1.4.3/dist/mapbox-gl-draw.js"></script>
<script>
const MAPBOX_TOKEN = __MAPBOX_TOKEN_JSON__;
mapboxgl.accessToken = MAPBOX_TOKEN;

const MAP_STYLES = { satellite: 'mapbox://styles/mapbox/satellite-streets-v12', streets: 'mapbox://styles/mapbox/streets-v12' };
let currentStyleKey = 'satellite';

const map = new mapboxgl.Map({
  container: 'map', style: MAP_STYLES.satellite, center: [73.0479, 33.6844], zoom: 12,
  pitch: 55, bearing: -17, antialias: true,
});
map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-right');

function addTerrainAndSky() {
  if (!map.getSource('mapbox-dem')) {
    map.addSource('mapbox-dem', { type: 'raster-dem', url: 'mapbox://mapbox.mapbox-terrain-dem-v1', tileSize: 512, maxzoom: 14 });
  }
  map.setTerrain({ source: 'mapbox-dem', exaggeration: parseFloat(document.getElementById('exagSlider').value) });
  if (!map.getLayer('sky')) {
    map.addLayer({ id: 'sky', type: 'sky', paint: { 'sky-type': 'atmosphere', 'sky-atmosphere-sun-intensity': 8 } });
  }
}
map.on('load', addTerrainAndSky);

function updateExaggeration() {
  const v = parseFloat(document.getElementById('exagSlider').value);
  document.getElementById('exagVal').textContent = v.toFixed(1);
  if (map.getTerrain()) map.setTerrain({ source: 'mapbox-dem', exaggeration: v });
}

const draw = new MapboxDraw({ displayControlsDefault: false, controls: { polygon: true, trash: true } });
map.addControl(draw, 'top-right');

let currentPolygonId = null;
let highlightShown = false;
let lastBuildingsGeoJSON = null;
let lastRoadsGeoJSON = null;
const ASSUMED_HEIGHT_M = 6.0;

function setMapLayer(key) {
  if (key === currentStyleKey) return;
  currentStyleKey = key;
  document.getElementById('layerSat').classList.toggle('active', key === 'satellite');
  document.getElementById('layerStreets').classList.toggle('active', key === 'streets');
  const savedPolygon = currentPolygonId ? draw.get(currentPolygonId) : null;
  map.once('style.load', () => {
    addTerrainAndSky();
    ensureAoiOutlineSource();
    ensureHighlightSources();
    ensureBuildingsSource();
    ensureRoadsSource();
    if (savedPolygon) {
      draw.deleteAll();
      currentPolygonId = draw.add(savedPolygon)[0];
      map.getSource('aoi-outline').setData(savedPolygon.geometry ? { type: 'Feature', geometry: savedPolygon.geometry } : null);
      if (highlightShown && savedPolygon.geometry) updateHighlight(savedPolygon.geometry.coordinates[0]);
    }
    if (lastBuildingsGeoJSON) map.getSource('osm-buildings').setData(lastBuildingsGeoJSON);
    if (lastRoadsGeoJSON) map.getSource('osm-roads').setData(lastRoadsGeoJSON);
  });
  map.setStyle(MAP_STYLES[key]);
}

map.on('draw.create', onDrawChange);
map.on('draw.update', onDrawChange);
map.on('draw.delete', () => {
  currentPolygonId = null;
  highlightShown = false;
  document.getElementById('genBtn').disabled = true;
  document.getElementById('buildingsBtn').disabled = true;
  document.getElementById('buildingsStats').style.display = 'none';
  document.getElementById('roadsBtn').disabled = true;
  document.getElementById('roadsStats').style.display = 'none';
  document.getElementById('hint').textContent = 'No area selected yet.';
  if (map.getSource('aoi-outline')) map.getSource('aoi-outline').setData({ type: 'FeatureCollection', features: [] });
  clearHighlight();
  clearBuildings();
  clearRoads();
});

function ensureAoiOutlineSource() {
  if (map.getSource('aoi-outline')) return;
  map.addSource('aoi-outline', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'aoi-outline-line', type: 'line', source: 'aoi-outline',
    paint: { 'line-color': '#3fc4dd', 'line-width': 2.5 } });
  map.addLayer({ id: 'aoi-outline-fill', type: 'fill', source: 'aoi-outline',
    paint: { 'fill-color': '#3fc4dd', 'fill-opacity': 0.08 } });
}

// Mapbox terrain exaggeration is one global value for the whole visible
// raster-DEM surface -- there's no way to scale it differently inside vs.
// outside a drawn polygon with a single stock elevation source. The real
// equivalent used instead: darken everything OUTSIDE the polygon with a
// donut mask (a big outer ring with the AOI ring as a hole, so the fill
// only covers the surrounding area) so the drawn area reads as the focus
// by contrast. (A raised translucent "highlight slab" over the polygon
// was tried too, but dropped at the user's request once the dimming
// alone already looked right -- kept simple rather than adding a second
// visual effect nobody asked to keep.)
function ensureHighlightSources() {
  if (!map.getSource('aoi-mask')) {
    map.addSource('aoi-mask', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'aoi-mask-layer', type: 'fill', source: 'aoi-mask',
      paint: { 'fill-color': '#04080a', 'fill-opacity': 0.55 } });
  }
}

// A big rectangle around the AOI (sized relative to its own span, not a
// fixed size) so the mask reliably covers the whole viewport at whatever
// zoom fitBounds settles on, however small or large the drawn area is.
function buildMaskFeature(ring) {
  const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
  const lonMin = Math.min(...lons), lonMax = Math.max(...lons);
  const latMin = Math.min(...lats), latMax = Math.max(...lats);
  const padLon = Math.max((lonMax - lonMin) * 6, 0.5);
  const padLat = Math.max((latMax - latMin) * 6, 0.5);
  const outer = [
    [lonMin - padLon, latMin - padLat], [lonMax + padLon, latMin - padLat],
    [lonMax + padLon, latMax + padLat], [lonMin - padLon, latMax + padLat],
    [lonMin - padLon, latMin - padLat],
  ];
  // Hole ring needs opposite winding from the outer ring per GeoJSON spec;
  // MapboxDraw's own polygons are already correctly wound, so reversing
  // gives the correct hole orientation without assuming a fixed convention.
  const hole = ring.slice().reverse();
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [outer, hole] } };
}

function updateHighlight(ring) {
  ensureHighlightSources();
  map.getSource('aoi-mask').setData(buildMaskFeature(ring));
}

function clearHighlight() {
  if (map.getSource('aoi-mask')) map.getSource('aoi-mask').setData({ type: 'FeatureCollection', features: [] });
}

// ---- real OSM buildings, restricted to inside the drawn polygon ----
function ensureBuildingsSource() {
  if (map.getSource('osm-buildings')) return;
  map.addSource('osm-buildings', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'osm-buildings-layer', type: 'fill-extrusion', source: 'osm-buildings',
    paint: {
      'fill-extrusion-color': ['interpolate', ['linear'], ['get', 'height_m'], 0, '#c9b896', 15, '#b89a72', 40, '#8f7a63', 80, '#6b6b6f'],
      'fill-extrusion-height': ['*', ['get', 'height_m'], parseFloat(document.getElementById('buildingExagSlider').value)],
      'fill-extrusion-base': 0,
      'fill-extrusion-opacity': 0.92,
      'fill-extrusion-vertical-gradient': true,
    },
  });
}

// A real building's height (a few metres, 1-2 stories) is nearly
// invisible at the zoom level needed to see a whole drawn area at once --
// this scales DISPLAYED height only, purely for visibility, the same
// honest "exaggeration is a display aid, not a data change" approach
// terrain exaggeration already uses. The real height_m is untouched in
// the underlying data/stats.
function updateBuildingExaggeration() {
  const v = parseFloat(document.getElementById('buildingExagSlider').value);
  document.getElementById('buildingExagVal').textContent = v.toFixed(1);
  if (map.getLayer('osm-buildings-layer')) {
    map.setPaintProperty('osm-buildings-layer', 'fill-extrusion-height', ['*', ['get', 'height_m'], v]);
  }
}

function clearBuildings() {
  lastBuildingsGeoJSON = null;
  if (map.getSource('osm-buildings')) map.getSource('osm-buildings').setData({ type: 'FeatureCollection', features: [] });
}

// ---- real OSM roads, clipped to inside the drawn polygon ----
const HIGHWAY_COLORS = {
  motorway: '#e35b5b', trunk: '#e35b5b', primary: '#e8935a',
  secondary: '#e8c15a', tertiary: '#e8c15a',
};
const HIGHWAY_COLOR_DEFAULT = '#3fc4dd';

function ensureRoadsSource() {
  if (map.getSource('osm-roads')) return;
  map.addSource('osm-roads', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'osm-roads-layer', type: 'line', source: 'osm-roads',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': ['match', ['get', 'highway'],
      ['motorway', 'trunk'], 4, ['primary', 'secondary'], 3, 1.8], 'line-opacity': 0.9 },
  });
}

function clearRoads() {
  lastRoadsGeoJSON = null;
  if (map.getSource('osm-roads')) map.getSource('osm-roads').setData({ type: 'FeatureCollection', features: [] });
}

// Real point-in-polygon test (ray casting), in real lon/lat -- Overpass's
// own bbox filter returns every road whose bbox intersects the query
// envelope, a superset of the polygon the user actually drew.
function pointInPolygon(pt, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > pt[1]) !== (yj > pt[1])) &&
      (pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

// Splits a real-world polyline into separate segments wherever it leaves
// the drawn polygon -- the same real bug/fix already documented in
// CLAUDE.md for this app's main road rendering (fis_app._lonlat_path_to_
// segments): naively filtering out-of-bounds points into one flat list
// would silently reconnect the surviving points across the gap, drawing a
// spurious straight line across the whole domain where the road actually
// left and re-entered the polygon.
function splitLineToSegments(coords, ring) {
  const segments = [];
  let current = [];
  for (const pt of coords) {
    if (pointInPolygon(pt, ring)) {
      current.push(pt);
    } else {
      if (current.length >= 2) segments.push(current);
      current = [];
    }
  }
  if (current.length >= 2) segments.push(current);
  return segments;
}

async function generateRoads() {
  if (!currentPolygonId) return;
  const feature = draw.get(currentPolygonId);
  const ring = feature.geometry.coordinates[0];
  const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
  const [lonMin, latMin, lonMax, latMax] = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];

  const btn = document.getElementById('roadsBtn');
  const errBox = document.getElementById('errBox');
  const statsBox = document.getElementById('roadsStats');
  btn.disabled = true; errBox.style.display = 'none';
  statsBox.style.display = 'block'; statsBox.textContent = 'Fetching real OSM roads...';

  try {
    const q = `[out:json][timeout:60];(way["highway"](${latMin},${lonMin},${latMax},${lonMax}););out geom;`;
    const resp = await fetch('https://overpass-api.de/api/interpreter', {
      method: 'POST', body: 'data=' + encodeURIComponent(q),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    if (!resp.ok) throw new Error('Overpass request failed (' + resp.status + ')');
    const data = await resp.json();
    const elements = data.elements || [];

    const features = [];
    let nWays = 0, nSegments = 0;
    for (const el of elements) {
      if (!el.geometry || el.geometry.length < 2) continue;
      const coords = el.geometry.map(g => [g.lon, g.lat]);
      const highway = (el.tags || {}).highway || 'unclassified';
      const color = HIGHWAY_COLORS[highway] || HIGHWAY_COLOR_DEFAULT;
      const segs = splitLineToSegments(coords, ring);
      if (!segs.length) continue;
      nWays++;
      for (const seg of segs) {
        nSegments++;
        features.push({ type: 'Feature', properties: { highway, color },
                         geometry: { type: 'LineString', coordinates: seg } });
      }
    }

    lastRoadsGeoJSON = { type: 'FeatureCollection', features };
    ensureRoadsSource();
    map.getSource('osm-roads').setData(lastRoadsGeoJSON);
    if (!map.getTerrain()) addTerrainAndSky();
    if (!highlightShown) { updateHighlight(ring); highlightShown = true; }
    map.fitBounds(new mapboxgl.LngLatBounds([lonMin, latMin], [lonMax, latMax]), { padding: 100, pitch: 60, bearing: -17, duration: 1000 });

    statsBox.textContent = nWays
      ? (nWays + ' roads (' + nSegments + ' segments after clipping to your polygon)')
      : 'No OSM roads found inside this area.';
  } catch (e) {
    errBox.textContent = 'Failed to generate roads: ' + e.message;
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}


// Building footprints come from the backend's /area/buildings route, which
// uses Google Open Buildings (the same real, already-proven-for-Pakistan
// footprint source the main SAILAB app uses) rather than raw OSM building
// ways -- a real, checked-first coverage comparison found OSM has only
// ~1/14th as many building footprints as Google Open Buildings in a real
// test bbox here, so relying on OSM alone showed "only a few buildings"
// even in genuinely dense areas. OSM is still used, server-side, purely to
// supplement real height where it happens to be tagged (sparse, ~2%
// coverage in prior testing) -- never as the primary footprint source.
async function generateBuildings() {
  if (!currentPolygonId) return;
  const feature = draw.get(currentPolygonId);
  const ring = feature.geometry.coordinates[0];
  const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
  const [lonMin, latMin, lonMax, latMax] = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];

  const btn = document.getElementById('buildingsBtn');
  const errBox = document.getElementById('errBox');
  const statsBox = document.getElementById('buildingsStats');
  btn.disabled = true; errBox.style.display = 'none';
  statsBox.style.display = 'block'; statsBox.textContent = 'Fetching real building footprints (Google Open Buildings)...';

  try {
    const resp = await fetch('/area/buildings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ geometry: feature.geometry }),
    });
    const result = await resp.json();
    if (result.error) throw new Error(result.error);

    lastBuildingsGeoJSON = result;
    ensureBuildingsSource();
    map.getSource('osm-buildings').setData(lastBuildingsGeoJSON);
    if (!map.getTerrain()) addTerrainAndSky();
    if (!highlightShown) { updateHighlight(ring); highlightShown = true; }
    map.fitBounds(new mapboxgl.LngLatBounds([lonMin, latMin], [lonMax, latMax]), { padding: 100, pitch: 60, bearing: -17, duration: 1000 });

    const n = result.n_rendered;
    const realPct = n ? (100 * result.n_real_height / n).toFixed(1) : '0.0';
    const cappedNote = result.n_total > n ? (' (capped from ' + result.n_total + ' by area, largest first)') : '';
    statsBox.innerHTML = n
      ? (n + ' buildings' + cappedNote + ' &middot; ' + result.n_real_height + ' with a real OSM height tag (' + realPct + '%) &middot; '
         + (n - result.n_real_height) + ' estimated at ' + ASSUMED_HEIGHT_M + 'm')
      : 'No buildings found inside this area.';
  } catch (e) {
    errBox.textContent = 'Failed to generate buildings: ' + e.message;
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

function onDrawChange(e) {
  const feature = e.features[e.features.length - 1];
  if (feature.geometry.type !== 'Polygon') return;
  currentPolygonId = feature.id;
  highlightShown = false;  // geometry just changed -- any previous highlight/mask no longer matches it
  document.getElementById('genBtn').disabled = false;
  document.getElementById('buildingsBtn').disabled = false;
  document.getElementById('buildingsStats').style.display = 'none';
  document.getElementById('roadsBtn').disabled = false;
  document.getElementById('roadsStats').style.display = 'none';
  document.getElementById('hint').textContent = 'Area ready -- click "View 3D terrain for this area".';
  ensureAoiOutlineSource();
  map.getSource('aoi-outline').setData({ type: 'Feature', geometry: feature.geometry });
  clearHighlight();
  clearBuildings();
  clearRoads();
}

function clearAll() {
  draw.deleteAll();
  currentPolygonId = null;
  highlightShown = false;
  document.getElementById('genBtn').disabled = true;
  document.getElementById('buildingsBtn').disabled = true;
  document.getElementById('buildingsStats').style.display = 'none';
  document.getElementById('roadsBtn').disabled = true;
  document.getElementById('roadsStats').style.display = 'none';
  document.getElementById('errBox').style.display = 'none';
  document.getElementById('hint').textContent = 'No area selected yet.';
  if (map.getSource('aoi-outline')) map.getSource('aoi-outline').setData({ type: 'FeatureCollection', features: [] });
  clearHighlight();
  clearBuildings();
  clearRoads();
}

function viewTerrain() {
  if (!currentPolygonId) return;
  const errBox = document.getElementById('errBox');
  errBox.style.display = 'none';
  try {
    const feature = draw.get(currentPolygonId);
    const ring = feature.geometry.coordinates[0];
    const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
    const bbox = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
    if (!map.getTerrain()) addTerrainAndSky();
    updateHighlight(ring);
    highlightShown = true;
    map.fitBounds(new mapboxgl.LngLatBounds(bbox.slice(0,2), bbox.slice(2)), { padding: 100, pitch: 60, bearing: -17, duration: 1000 });
    document.getElementById('hint').textContent = 'Real 3D terrain shown for this area -- everything outside your polygon is dimmed.';
  } catch (e) {
    errBox.textContent = 'Failed to show terrain: ' + e.message;
    errBox.style.display = 'block';
  }
}
</script>
"""


def render_osm_buildings_3d_page(mapbox_token):
    return PAGE_TEMPLATE.replace("__MAPBOX_TOKEN_JSON__", json.dumps(mapbox_token))
