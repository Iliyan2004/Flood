#!/usr/bin/env python3
"""
build_3d_viewer.py — turns a finished PluviSim/LISFLOOD-FP run into a
self-contained interactive 3D flood animation (Three.js, WebGL).

Not MapLibre/GeoLibre: those are web-mercator geographic map libraries built
around real lat/lon tile pyramids. Our domain is a small local Cartesian DEM
with no real-world coordinates and no tile server to talk to (and the
Artifact sandbox blocks external tile/CDN requests anyway), so a raw WebGL
heightfield scene (Three.js) is the correct tool, not a map library.

Usage:
  python3 build_3d_viewer.py --rundir ~/flood/demo_run2 --out ~/flood/demo_run2/viewer_3d.html
"""
import argparse
import base64
import glob
import json
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flood_model import read_ascii, BLUE_RAMP, MODEL_NAME, ACCENT_LIGHT, ACCENT_DARK  # noqa: E402

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def f32_b64(arr):
    return base64.b64encode(np.asarray(arr, dtype="<f4").tobytes()).decode("ascii")


def terrain_ramp_colors(elev, nodata):
    """Elevation -> RGB (0..1) using a low-saturation olive->tan->cream ramp,
    distinct from the water blue ramp so the two never get confused."""
    valid = elev != nodata
    lo, hi = np.nanmin(elev[valid]), np.nanmax(elev[valid])
    t = np.clip((elev - lo) / max(hi - lo, 1e-6), 0, 1)
    stops = np.array([
        [0.36, 0.40, 0.27],
        [0.55, 0.55, 0.38],
        [0.75, 0.70, 0.52],
        [0.90, 0.86, 0.72],
    ])
    idx = t * (len(stops) - 1)
    i0 = np.clip(idx.astype(int), 0, len(stops) - 2)
    frac = (idx - i0)[..., None]
    rgb = stops[i0] * (1 - frac) + stops[i0 + 1] * frac
    return rgb.astype("float32")


def main():
    p = argparse.ArgumentParser(description="Build a 3D WebGL flood-animation viewer from a PluviSim run.")
    p.add_argument("--rundir", required=True, help="A PluviSim run directory (contains results/)")
    p.add_argument("--out", default=None, help="Output HTML path (default: <rundir>/viewer_3d.html)")
    p.add_argument("--z-exaggeration", type=float, default=15.0, help="Vertical exaggeration factor")
    args = p.parse_args()

    rundir = os.path.expanduser(args.rundir)
    results = os.path.join(rundir, "results")
    out_path = args.out or os.path.join(rundir, "viewer_3d.html")

    dem_candidates = glob.glob(os.path.join(rundir, "*.dem.ascii"))
    if not dem_candidates:
        sys.exit(f"No .dem.ascii found in {rundir}")
    dem_header, dem = read_ascii(dem_candidates[0])
    ncols, nrows, cs = int(dem_header["ncols"]), int(dem_header["nrows"]), dem_header["cellsize"]
    nodata = -9999.0

    wd_files = sorted(glob.glob(os.path.join(results, "res-*.wd")))
    if not wd_files:
        sys.exit(f"No res-*.wd timestep files found in {results}")

    depth_stack = []
    for fp in wd_files:
        _, d = read_ascii(fp)
        d = np.where(d == nodata, 0.0, d)
        depth_stack.append(d)
    depth_stack = np.array(depth_stack)  # (T, nrows, ncols)

    # saveint interval, in minutes -- recover from the .par file written alongside
    saveint_min = 10.0
    par_files = glob.glob(os.path.join(rundir, "*.par"))
    if par_files:
        with open(par_files[0]) as f:
            for line in f:
                if line.strip().startswith("saveint"):
                    saveint_min = float(line.split()[1]) / 60.0
                    break

    elev_masked = np.where(dem == nodata, np.nanmin(dem[dem != nodata]), dem)
    elev_min = float(elev_masked.min())
    terrain_h = (elev_masked - elev_min) * args.z_exaggeration
    terrain_rgb = terrain_ramp_colors(dem, nodata)

    max_depth_overall = float(depth_stack.max())
    dry_thresh = 0.03

    # per-timestep summary stats for the HUD (flooded area, max depth) --
    cell_area_km2 = (cs * cs) / 1e6
    area_series = [float((d > dry_thresh).sum() * cell_area_km2) for d in depth_stack]
    maxd_series = [float(d.max()) for d in depth_stack]

    payload = {
        "ncols": ncols, "nrows": nrows, "cellsize": cs,
        "elevMin": elev_min, "zExag": args.z_exaggeration,
        "maxDepthOverall": max_depth_overall, "dryThresh": dry_thresh,
        "nFrames": len(wd_files), "saveIntMin": saveint_min,
        "areaSeries": area_series, "maxdSeries": maxd_series,
        "blueRamp": BLUE_RAMP,
    }

    terrain_h_b64 = f32_b64(terrain_h.flatten())
    terrain_rgb_b64 = f32_b64(terrain_rgb.reshape(-1, 3).flatten())
    depth_b64 = f32_b64(depth_stack.flatten())

    with open(os.path.join(VENDOR_DIR, "three.min.js")) as f:
        three_js = f.read()
    with open(os.path.join(VENDOR_DIR, "OrbitControls.js")) as f:
        orbit_js = f.read()

    html = build_html(payload, terrain_h_b64, terrain_rgb_b64, depth_b64, three_js, orbit_js)
    with open(out_path, "w") as f:
        f.write(html)
    print(f"3D viewer written to {out_path}  ({os.path.getsize(out_path)/1e6:.2f} MB)")


def build_html(payload, terrain_h_b64, terrain_rgb_b64, depth_b64, three_js, orbit_js):
    payload_json = json.dumps(payload)
    return f"""<!doctype html>
<title>{MODEL_NAME} 3D</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --page:#F3F6F8; --surface:#FFFFFF; --ink:#142430; --ink-2:#46606B; --muted:#7C929B;
  --line:#D7E2E5; --accent:{ACCENT_LIGHT}; --panel: rgba(255,255,255,0.92);
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --page:#0B1417; --surface:#101B1F; --ink:#E7EEF0; --ink-2:#A9BEC4; --muted:#6E848B;
    --line:#22343A; --accent:{ACCENT_DARK}; --panel: rgba(16,27,31,0.88);
    color-scheme: dark;
  }}
}}
:root[data-theme="dark"] {{
  --page:#0B1417; --surface:#101B1F; --ink:#E7EEF0; --ink-2:#A9BEC4; --muted:#6E848B;
  --line:#22343A; --accent:{ACCENT_DARK}; --panel: rgba(16,27,31,0.88);
  color-scheme: dark;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; height:100%; background:var(--page); overflow:hidden;
  font-family:"Public Sans",system-ui,sans-serif; color:var(--ink); }}
#scene {{ position:absolute; inset:0; }}
canvas {{ display:block; }}

.masthead {{
  position:absolute; top:0; left:0; right:0; z-index:3;
  display:flex; justify-content:space-between; align-items:center;
  padding:12px 18px; background:linear-gradient(to bottom, var(--panel), transparent);
  pointer-events:none;
}}
.brand {{ font-family:"Barlow Condensed",sans-serif; font-weight:700; font-size:22px;
  text-transform:uppercase; letter-spacing:0.02em; }}
.brand span {{ color:var(--accent); }}
.hud {{
  display:flex; gap:10px; pointer-events:auto;
}}
.hud .tile {{
  background:var(--panel); border:1px solid var(--line); border-radius:8px;
  padding:6px 12px; font-family:"IBM Plex Mono",monospace; font-size:12px; min-width:108px;
  backdrop-filter: blur(6px);
}}
.hud .tile .l {{ color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:0.04em; }}
.hud .tile .v {{ color:var(--ink); font-size:15px; margin-top:2px; font-variant-numeric:tabular-nums; }}

.controls {{
  position:absolute; left:0; right:0; bottom:0; z-index:3;
  display:flex; align-items:center; gap:14px;
  padding:14px 20px; background:linear-gradient(to top, var(--panel), transparent);
}}
button.play {{
  width:38px; height:38px; border-radius:50%; border:1px solid var(--line);
  background:var(--accent); color:#06181c; font-size:14px; cursor:pointer;
  display:flex; align-items:center; justify-content:center; flex:none;
}}
input[type=range] {{ flex:1; accent-color:var(--accent); }}
.time-label {{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-2);
  min-width:118px; text-align:right; }}
.legend {{
  position:absolute; right:18px; top:64px; z-index:3;
  background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px;
  font-size:11px; color:var(--ink-2); backdrop-filter: blur(6px);
}}
.legend .bar {{ width:120px; height:10px; border-radius:4px; margin:6px 0 4px;
  background: linear-gradient(to right, {", ".join(payload["blueRamp"])}); }}
.legend .row {{ display:flex; justify-content:space-between; font-family:"IBM Plex Mono",monospace; }}
.hint {{ position:absolute; left:18px; bottom:64px; z-index:3; font-size:11px; color:var(--muted);
  background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 10px; }}
</style>

<div id="scene"></div>

<div class="masthead">
  <div class="brand">{MODEL_NAME} <span>3D</span></div>
  <div class="hud">
    <div class="tile"><div class="l">Flooded area</div><div class="v" id="hud-area">–</div></div>
    <div class="tile"><div class="l">Max depth (frame)</div><div class="v" id="hud-depth">–</div></div>
  </div>
</div>

<div class="legend">
  <div>Water depth</div>
  <div class="bar"></div>
  <div class="row"><span>0 m</span><span id="legend-max">– m</span></div>
</div>

<div class="hint">Drag to orbit &middot; scroll to zoom &middot; right-drag to pan</div>

<div class="controls">
  <button class="play" id="playBtn">&#9654;</button>
  <input type="range" id="scrub" min="0" max="{payload['nFrames']-1}" value="0" step="1">
  <div class="time-label" id="timeLabel">T = 0 min</div>
</div>

<script>{three_js}</script>
<script>{orbit_js}</script>
<script>
const PAYLOAD = {payload_json};
const TERRAIN_H = new Float32Array(Uint8Array.from(atob("{terrain_h_b64}"), c => c.charCodeAt(0)).buffer);
const TERRAIN_RGB = new Float32Array(Uint8Array.from(atob("{terrain_rgb_b64}"), c => c.charCodeAt(0)).buffer);
const DEPTH = new Float32Array(Uint8Array.from(atob("{depth_b64}"), c => c.charCodeAt(0)).buffer);

const NX = PAYLOAD.ncols, NY = PAYLOAD.nrows, CS = PAYLOAD.cellsize, ZEX = PAYLOAD.zExag;
const W = (NX - 1) * CS, H = (NY - 1) * CS;
const FRAME_STRIDE = NX * NY;

function blueRampColor(t) {{
  const stops = PAYLOAD.blueRamp.map(h => {{
    const r = parseInt(h.slice(1,3),16)/255, g = parseInt(h.slice(3,5),16)/255, b = parseInt(h.slice(5,7),16)/255;
    return [r,g,b];
  }});
  const idx = Math.min(stops.length - 1.001, Math.max(0, t * (stops.length - 1)));
  const i0 = Math.floor(idx), f = idx - i0;
  const a = stops[i0], b = stops[Math.min(i0+1, stops.length-1)];
  return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f];
}}

// ---------------- Three.js scene ----------------
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xeef3f5);
scene.fog = new THREE.Fog(0xeef3f5, W*1.6, W*4.5);

const camera = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 1, W*10);
camera.position.set(W*0.65, Math.max(W,H)*0.55, H*0.95);

const renderer = new THREE.WebGLRenderer({{antialias:true, powerPreference:'high-performance'}});
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(innerWidth, innerHeight);
document.getElementById('scene').appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0, 0);
controls.enableDamping = true; controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI * 0.49;
controls.update();

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 0.9);
sun.position.set(-Math.sin(THREE.MathUtils.degToRad(315)) * W, W*0.8, Math.cos(THREE.MathUtils.degToRad(315)) * H);
scene.add(sun);

// ---- terrain mesh ----
const terrainGeo = new THREE.PlaneGeometry(W, H, NX - 1, NY - 1);
terrainGeo.rotateX(-Math.PI / 2);
const tPos = terrainGeo.attributes.position;
const tCol = new Float32Array(tPos.count * 3);
for (let j = 0; j < NY; j++) {{
  for (let i = 0; i < NX; i++) {{
    const vi = j * NX + i;          // grid row-major (row 0 = north = +Z back in our flip below)
    const gi = (NY - 1 - j) * NX + i; // PlaneGeometry rows run bottom->top; flip to match DEM north-up
    tPos.setY(vi, TERRAIN_H[gi]);
    tCol[vi*3+0] = TERRAIN_RGB[gi*3+0];
    tCol[vi*3+1] = TERRAIN_RGB[gi*3+1];
    tCol[vi*3+2] = TERRAIN_RGB[gi*3+2];
  }}
}}
terrainGeo.setAttribute('color', new THREE.BufferAttribute(tCol, 3));
terrainGeo.computeVertexNormals();
const terrainMat = new THREE.MeshStandardMaterial({{vertexColors:true, roughness:0.95, metalness:0.0}});
const terrainMesh = new THREE.Mesh(terrainGeo, terrainMat);
terrainMesh.receiveShadow = false;
scene.add(terrainMesh);

// wireframe cage for scale/context
const gridHelper = new THREE.GridHelper(Math.max(W,H)*1.3, 12, 0x9db2b8, 0xd6e2e5);
gridHelper.position.y = -1;
scene.add(gridHelper);

// ---- water mesh (updated per frame) ----
const waterGeo = new THREE.PlaneGeometry(W, H, NX - 1, NY - 1);
waterGeo.rotateX(-Math.PI / 2);
const wPos = waterGeo.attributes.position;
const wCol = new Float32Array(wPos.count * 3);
waterGeo.setAttribute('color', new THREE.BufferAttribute(wCol, 3));
// unlit material: water doesn't need per-frame normal recompute / lighting response,
// which is the main per-frame cost on a mesh this dense -- big win for smoothness
const waterMat = new THREE.MeshBasicMaterial({{
  vertexColors:true, transparent:true, opacity:0.85, side: THREE.DoubleSide,
}});
const waterMesh = new THREE.Mesh(waterGeo, waterMat);
scene.add(waterMesh);

const HIDE_OFFSET = 3.0; // dry vertices sit just under the terrain at THIS SAME cell -- keeps
                          // neighbouring wet/dry vertex heights close, so no degenerate "curtain"
                          // triangles form at the flood boundary (a single huge SUNK constant did)

function setFrame(t) {{
  const base = t * FRAME_STRIDE;
  let maxD = 0;
  for (let j = 0; j < NY; j++) {{
    for (let i = 0; i < NX; i++) {{
      const vi = j * NX + i;
      const gi = (NY - 1 - j) * NX + i;
      const d = DEPTH[base + gi];
      if (d > maxD) maxD = d;
      if (d > PAYLOAD.dryThresh) {{
        wPos.setY(vi, TERRAIN_H[gi] + d * ZEX + 0.4);
        const c = blueRampColor(Math.min(1, d / Math.max(PAYLOAD.maxDepthOverall, 0.05)));
        wCol[vi*3+0]=c[0]; wCol[vi*3+1]=c[1]; wCol[vi*3+2]=c[2];
      }} else {{
        wPos.setY(vi, TERRAIN_H[gi] - HIDE_OFFSET);
      }}
    }}
  }}
  wPos.needsUpdate = true;
  waterGeo.attributes.color.needsUpdate = true;
  // no computeVertexNormals() here -- MeshBasicMaterial is unlit and ignores normals,
  // so recomputing them every frame would be pure wasted work

  document.getElementById('hud-area').textContent = PAYLOAD.areaSeries[t].toFixed(4) + ' km²';
  document.getElementById('hud-depth').textContent = PAYLOAD.maxdSeries[t].toFixed(2) + ' m';
  document.getElementById('timeLabel').textContent = 'T = ' + Math.round(t * PAYLOAD.saveIntMin) + ' min';
  document.getElementById('scrub').value = t;
}}
document.getElementById('legend-max').textContent = PAYLOAD.maxDepthOverall.toFixed(2) + ' m';

const urlFrame = parseInt(new URLSearchParams(location.search).get('frame'));
let frame = Number.isFinite(urlFrame) ? Math.min(Math.max(urlFrame,0), PAYLOAD.nFrames-1) : 0;
let playing = !Number.isFinite(urlFrame), acc = 0;
setFrame(frame);
if (!playing) document.getElementById('playBtn').innerHTML = '&#9654;';

document.getElementById('scrub').addEventListener('input', (e) => {{
  playing = false; document.getElementById('playBtn').innerHTML = '&#9654;';
  frame = parseInt(e.target.value); setFrame(frame);
}});
document.getElementById('playBtn').addEventListener('click', () => {{
  playing = !playing;
  document.getElementById('playBtn').innerHTML = playing ? '&#10073;&#10073;' : '&#9654;';
}});

let last = performance.now();
function animate(now) {{
  requestAnimationFrame(animate);
  const dt = now - last; last = now;
  if (playing) {{
    acc += dt;
    if (acc > 420) {{
      acc = 0;
      frame = (frame + 1) % PAYLOAD.nFrames;
      setFrame(frame);
    }}
  }}
  controls.update();
  renderer.render(scene, camera);
}}
requestAnimationFrame(animate);

addEventListener('resize', () => {{
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}});
</script>
"""


if __name__ == "__main__":
    main()
