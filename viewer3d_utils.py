"""
Shared 3D scene builder for the Flood Information System. Every tab gets its
own 3D view (terrain always shown; each tab adds its own overlay type) built
from this one generator, instead of five divergent implementations:

  Tab 1 (area):      terrain only, drawn polygon outline
  Tab 2 (rainfall):  terrain + river/stream lines (real OSM geometry)
  Tab 3 (flood):     terrain + animated water depth (time series)
  Tab 4 (buildings): terrain + extruded buildings (InstancedMesh) + road risk lines
  Tab 5 (damage):    terrain + LULC/damage color drape + hotspot highlight

Three.js (same proven approach as build_3d_viewer.py), fully self-contained
(vendored three.min.js + OrbitControls, no CDN at view time).
"""
import base64
import json
import os

import numpy as np

from damage_utils import DEPTH_DAMAGE_CURVES_ASIA
from infra_utils import RISK_BANDS

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
ACCENT_LIGHT = "#0E7A8C"
ACCENT_DARK = "#5FD3E8"

# Must match the BLUE_HEX ramp used client-side (the water animation's own
# color ramp) exactly, so the legend bar shown actually matches the colors
# rendered on the water surface itself.
WATER_DEPTH_RAMP = ["#f3f9ff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _water_legend_html(max_depth):
    gradient = ", ".join(WATER_DEPTH_RAMP)
    return (
        '<div style="margin-top:8px;">'
        '<div style="font-size:11px;margin-bottom:3px;">Flood depth (m)</div>'
        f'<div style="height:12px;border-radius:3px;background:linear-gradient(to right, {gradient});"></div>'
        '<div style="display:flex;justify-content:space-between;font-size:10px;margin-top:2px;color:#A9BEC4;">'
        f'<span>0</span><span>{max_depth:.2f}</span></div></div>'
    )


def f32_b64(arr):
    return base64.b64encode(np.asarray(arr, dtype="<f4").tobytes()).decode("ascii")


def hillshade(dem, cellsize, nodata=-9999):
    from matplotlib.colors import LightSource
    valid = dem != nodata
    filled = np.where(valid, dem, np.nanmin(dem[valid]) if valid.any() else 0)
    ls = LightSource(azdeg=315, altdeg=45)
    return ls.hillshade(filled, vert_exag=3, dx=cellsize, dy=cellsize)


def _fill_nodata_nearest(dem, nodata=-9999):
    """Fills NODATA cells (e.g. outside an irregular polygon's shape but
    inside its bounding-box grid) with the elevation of their nearest valid
    cell, instead of flattening them to a single global value. Flattening
    created an artificial cliff at the polygon boundary -- with 15x vertical
    exaggeration even a few real meters of relief became a towering wall
    (caught visually: looked like giant dark planes cutting through the 3D
    scene). Nearest-neighbor fill keeps the terrain looking continuous."""
    from scipy.ndimage import distance_transform_edt
    invalid = dem == nodata
    if not invalid.any():
        return dem
    if invalid.all():
        return np.zeros_like(dem)
    _, (idx_r, idx_c) = distance_transform_edt(invalid, return_indices=True, return_distances=True)
    filled = dem.copy()
    filled[invalid] = dem[idx_r[invalid], idx_c[invalid]]
    return filled


def terrain_base_colors(dem, nodata=-9999):
    valid = dem != nodata
    lo, hi = (np.nanmin(dem[valid]), np.nanmax(dem[valid])) if valid.any() else (0, 1)
    t = np.clip((dem - lo) / max(hi - lo, 1e-6), 0, 1)
    stops = np.array([[0.36, 0.40, 0.27], [0.55, 0.55, 0.38], [0.75, 0.70, 0.52], [0.90, 0.86, 0.72]])
    idx = t * (len(stops) - 1)
    i0 = np.clip(idx.astype(int), 0, len(stops) - 2)
    frac = (idx - i0)[..., None]
    return (stops[i0] * (1 - frac) + stops[i0 + 1] * frac).astype("float32")


def _load_vendor():
    with open(os.path.join(VENDOR_DIR, "three.min.js")) as f:
        three_js = f.read()
    with open(os.path.join(VENDOR_DIR, "OrbitControls.js")) as f:
        orbit_js = f.read()
    return three_js, orbit_js


def build_3d_scene(out_path, title, header, dem, nodata=-9999, z_exag=None,
                    color_overlay=None, buildings=None, building_footprints=None,
                    lines=None, water=None, aoi_poly_rc=None,
                    legend_html="", hud_html=""):
    """
    color_overlay: {"rgb": (nrows,ncols,3) float0-1 array, "opacity": float}
    buildings: {"rows": [...], "cols": [...], "colors": ["#hex", ...], "height_m": float}
        (cheap fallback: one box per grid cell -- used only when real
        footprint polygons aren't available)
    building_footprints: [{"poly_rc": [[row,col], ...], "height_m": float}, ...]
        real per-building footprint polygons, extruded to proper building
        shapes instead of grid-cell boxes.
    lines: [{"rows": [...], "cols": [...], "color": "#hex"}, ...]  (each a polyline)
    water: {"depth_stack": (T,nrows,ncols), "save_int_min": float, "dry_thresh": float, "max_depth": float}
    aoi_poly_rc: [[row,col], ...] -- the actual drawn/uploaded AOI polygon's
        own exterior ring, in the same continuous grid space as
        building_footprints. When given, the terrain and color-overlay
        meshes are clipped exactly to this shape instead of the coarser
        whole-cell NODATA-vertex cut (see clipGeometryToAoi in the
        template below, and CLAUDE.md for the sawtooth-edge bug this
        fixed).

    No satellite basemap texture -- removed at the user's request (every
    3D view now shows the plain DEM/hillshade terrain). See CLAUDE.md.
    """
    if water is not None:
        # A real gradient bar (matching the water's own render colors
        # exactly), not just text saying "blue ramp" -- callers don't need
        # to build this themselves.
        legend_html = (legend_html or "") + _water_legend_html(water["max_depth"])
    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    # Cells outside the actual drawn/uploaded polygon but inside its
    # rectangular bounding-box grid are NODATA (see polygon_utils.
    # crop_dem_to_polygon). Nearest-neighbor filling them (below) is still
    # needed so any *rendered* geometry has a sane, non-cliff elevation to
    # sample -- but rendering every one of those cells as solid ground
    # made every AOI look like a plain rectangle regardless of the real
    # (often irregular/concave) shape the user actually drew, which is
    # also why buildings near a corner outside the true shape looked like
    # they belonged to the AOI. valid_mask lets the mesh-building JS cut
    # those triangles out entirely instead of rendering them.
    valid_mask = (dem != nodata)
    dem_filled = _fill_nodata_nearest(dem, nodata)
    elev_min = float(np.nanmin(dem_filled))
    elev_range = float(np.nanmax(dem_filled)) - elev_min
    if z_exag is None:
        # A single fixed exaggeration (this app used a flat 15x for a long
        # time) looks right for the gentle flood-plain relief most AOIs
        # have -- a few metres of real elevation change over a domain
        # that's kilometres wide needs real inflation to read as anything
        # but flat. It looks absurd in real mountainous terrain (e.g.
        # Gilgit-Baltistan): a genuine few-hundred-metre rise over a
        # similarly-sized AOI is already a dramatic, visible slope on its
        # own, and inflating it 15x further turns a real hillside into
        # what looks like a sheer vertical cliff/wall -- not realistic,
        # and not what "exaggeration for visibility" is supposed to do
        # when the relief was never subtle in the first place.
        #
        # Scaled instead off the DEM's own real relief-to-span ratio: aim
        # for the exaggerated vertical extent to land around a quarter of
        # the horizontal footprint (a normal-looking relief-map
        # proportion), clamped to [1x, 15x] so flat terrain still gets a
        # real visibility boost (capped at the old default) and steep
        # terrain never gets inflated past its own true shape (floor of
        # 1x -- true to life, never exaggerated).
        span_m = max((ncols - 1) * cs, (nrows - 1) * cs, 1.0)
        target_ratio = 0.25
        z_exag = target_ratio * span_m / max(elev_range, 1.0)
        z_exag = max(1.0, min(15.0, z_exag))
    terrain_h = (dem_filled - elev_min) * z_exag
    terrain_rgb = terrain_base_colors(dem_filled, nodata)

    payload = {"ncols": ncols, "nrows": nrows, "cellsize": cs, "zExag": z_exag, "elevMin": elev_min}
    terrain_h_b64 = f32_b64(terrain_h.flatten())
    terrain_rgb_b64 = f32_b64(terrain_rgb.reshape(-1, 3).flatten())
    valid_b64 = base64.b64encode(valid_mask.astype("uint8").tobytes()).decode("ascii")

    overlay_js = "null"
    if color_overlay is not None:
        rgb = color_overlay["rgb"]
        payload["overlayOpacity"] = color_overlay.get("opacity", 0.85)
        overlay_js = json.dumps({"rgb_b64": f32_b64(rgb.reshape(-1, 3).flatten())})

    buildings_js = "null"
    if buildings is not None and len(buildings.get("rows", [])) > 0:
        buildings_js = json.dumps({
            "rows": buildings["rows"], "cols": buildings["cols"],
            "colors": buildings["colors"], "heightM": buildings.get("height_m", 6.0),
        })

    footprints_js = "[]"
    if building_footprints:
        footprints_js = json.dumps([
            {"poly": fp["poly_rc"], "heightM": fp.get("height_m", 6.0),
             "color": fp.get("color"), "depths": fp.get("depths"), "raise": fp.get("raise", True),
             "taper": fp.get("taper", 0.0)}
            for fp in building_footprints
        ])

    lines_js = "[]"
    if lines:
        lines_js = json.dumps([
            {"rows": l["rows"], "cols": l["cols"], "color": l.get("color"),
             "depths": l.get("depths"), "speeds": l.get("speeds")}
            for l in lines
        ])

    water_js = "null"
    if water is not None:
        water_payload = {
            "depth_b64": f32_b64(water["depth_stack"].flatten()),
            "nFrames": water["depth_stack"].shape[0],
            "saveIntMin": water["save_int_min"], "dryThresh": water["dry_thresh"],
            "maxDepth": water["max_depth"],
            "rainMmHr": water.get("rain_mm_hr"), "durationHr": water.get("duration_hr"),
        }
        water_js = json.dumps(water_payload)

    aoi_poly_js = json.dumps(aoi_poly_rc) if aoi_poly_rc else "null"
    # Real reference curves/bands, embedded from their own Python source of
    # truth (damage_utils.py / infra_utils.py) rather than hand-copied into
    # JS -- used to animate building/road risk color with the run's own
    # real per-timestep depth (and, for roads, speed) instead of a single
    # static verdict from the run's peak. See the JS huizingaDamageFraction/
    # defraHazardFraction functions below.
    huizinga_built_up_js = json.dumps(DEPTH_DAMAGE_CURVES_ASIA["Built-up"])
    risk_bands_js = json.dumps([[name, hi] for name, hi in RISK_BANDS])

    three_js, orbit_js = _load_vendor()

    html = f"""<!doctype html>
<title>{title}</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --accent:{ACCENT_LIGHT}; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --accent:{ACCENT_DARK}; }} }}
:root[data-theme="dark"] {{ --accent:{ACCENT_DARK}; }}
html,body{{margin:0;height:100%;overflow:hidden;font-family:system-ui,sans-serif;background:#0B1417;}}
#scene{{position:absolute;inset:0;}}
.legend{{position:absolute;right:12px;top:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border-radius:8px;padding:10px 12px;font-size:11.5px;max-width:240px;}}
.hud{{position:absolute;left:12px;top:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border-radius:8px;padding:8px 12px;font-size:11.5px;font-family:monospace;}}
.hint{{position:absolute;left:12px;bottom:12px;background:rgba(16,27,31,0.85);color:#8fa3a9;
  border-radius:8px;padding:5px 10px;font-size:10.5px;}}
.fs-btn{{position:absolute;right:12px;bottom:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:15px;cursor:pointer;line-height:1;}}
.fs-btn:hover{{background:rgba(16,27,31,0.98);}}
.export-btn{{position:absolute;right:56px;bottom:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:11px;cursor:pointer;line-height:1;
  font-family:system-ui,sans-serif;}}
.export-btn:hover{{background:rgba(16,27,31,0.98);}}
.export-video-btn{{position:absolute;right:132px;bottom:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:11px;cursor:pointer;line-height:1;
  font-family:system-ui,sans-serif;}}
.export-video-btn:hover{{background:rgba(16,27,31,0.98);}}
.export-video-btn:disabled{{opacity:0.5;cursor:not-allowed;}}
.reset-view-btn{{position:absolute;left:12px;top:12px;background:rgba(16,27,31,0.85);color:#E7EEF0;
  border:1px solid #3a4c52;border-radius:8px;padding:6px 10px;font-size:11px;cursor:pointer;line-height:1;
  font-family:system-ui,sans-serif;}}
.reset-view-btn:hover{{background:rgba(16,27,31,0.98);}}
:root.fullscreen-active body, html:fullscreen body {{ background:#0B1417; }}
/* The timeline bar (added for the water animation) is a full-width strip
   pinned to bottom:0 -- it used to render on TOP of these buttons, which
   sit at the same bottom-right corner, hiding all three completely
   whenever a flood/protection view (the ones with a timeline) was open.
   Lift them clear of the bar's own height on those views specifically. */
body.has-timeline .fs-btn, body.has-timeline .export-btn, body.has-timeline .export-video-btn {{
  bottom: 54px;
}}
.timeline{{position:absolute;left:0;right:0;bottom:0;display:flex;align-items:center;gap:10px;
  background:rgba(16,27,31,0.92);padding:8px 14px;font-family:system-ui,sans-serif;color:#E7EEF0;font-size:12px;}}
.timeline button{{background:rgba(255,255,255,0.08);border:1px solid #3a4c52;color:#E7EEF0;border-radius:6px;
  width:30px;height:26px;cursor:pointer;font-size:13px;flex:0 0 auto;}}
.timeline button:hover{{background:rgba(255,255,255,0.18);}}
.timeline input[type=range]{{flex:1;accent-color:{ACCENT_LIGHT};}}
.timeline .tlabel{{font-family:monospace;font-size:11px;color:#A9BEC4;white-space:nowrap;flex:0 0 auto;}}
.timeline select{{background:#16232a;color:#E7EEF0;border:1px solid #3a4c52;border-radius:6px;font-size:11px;
  padding:3px 4px;flex:0 0 auto;}}
</style>
<div id="scene"></div>
{f'<div class="legend">{legend_html}</div>' if legend_html else ''}
{f'<div class="hud">{hud_html}</div>' if hud_html else ''}
<div class="hint">Drag to orbit &middot; scroll to zoom &middot; right-drag to pan</div>
<button class="reset-view-btn" id="resetViewBtn" title="Reset to the default overview angle">Reset View</button>
<button class="export-btn" id="exportBtn" title="Export current view as PNG">Export PNG</button>
{'<button class="export-video-btn" id="exportVideoBtn" title="Export one full animation loop as MP4">Export Video</button>' if water is not None else ''}
<button class="fs-btn" id="fsBtn" title="Full screen">&#9974;</button>
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
</div>''' if water is not None else ''}
<script>{three_js}</script>
<script>{orbit_js}</script>
<script>
document.getElementById('fsBtn').addEventListener('click', () => {{
  const el = document.documentElement;
  if (!document.fullscreenElement) {{ (el.requestFullscreen || el.webkitRequestFullscreen || function(){{}}).call(el); }}
  else {{ (document.exitFullscreen || document.webkitExitFullscreen || function(){{}}).call(document); }}
}});
document.getElementById('resetViewBtn').addEventListener('click', () => resetView());
document.getElementById('exportBtn').addEventListener('click', () => {{
  // Same reasoning as Export Video: a PNG captured from an extreme orbit
  // angle (nearly edge-on to the terrain) reads as an unreadable sliver --
  // easy to mistake for buildings floating or sinking into the ground when
  // it's really just camera foreshortening. Always export from the known-
  // good default overview angle instead of whatever was last on screen.
  resetView();
  renderer.render(scene, camera);
  const a = document.createElement('a');
  a.download = {json.dumps(title.replace(' ', '_'))} + '_3d.png';
  a.href = renderer.domElement.toDataURL('image/png');
  a.click();
}});
const PAYLOAD = {json.dumps(payload)};
const TERRAIN_H = new Float32Array(Uint8Array.from(atob("{terrain_h_b64}"), c => c.charCodeAt(0)).buffer);
const TERRAIN_RGB = new Float32Array(Uint8Array.from(atob("{terrain_rgb_b64}"), c => c.charCodeAt(0)).buffer);
const VALID = new Uint8Array(Uint8Array.from(atob("{valid_b64}"), c => c.charCodeAt(0)).buffer);
const OVERLAY = {overlay_js};
const BUILDINGS = {buildings_js};
const FOOTPRINTS = {footprints_js};
const LINES = {lines_js};
const WATER = {water_js};
const AOI_POLY = {aoi_poly_js};
const HUIZINGA_BUILT_UP = {huizinga_built_up_js};
const RISK_BANDS = {risk_bands_js};

const NX = PAYLOAD.ncols, NY = PAYLOAD.nrows, CS = PAYLOAD.cellsize, ZEX = PAYLOAD.zExag;
const W = (NX - 1) * CS, H = (NY - 1) * CS;
const FRAME_STRIDE = NX * NY;
function gi(row, col) {{ return (NY - 1 - row) * NX + col; }}  // flip to north-up
function worldX(col) {{ return -W/2 + col * CS; }}
function worldZ(row) {{ return -H/2 + (NY - 1 - row) * CS; }}

// ---- animated risk color for buildings/roads, driven by the run's own
// real per-timestep depth (and, for roads, speed) instead of one static
// verdict baked in from the run's peak -- a building/road genuinely reads
// as "safe" again once its own local floodwater actually recedes, and
// "at risk" only while it's genuinely wet right now.
function huizingaDamageFraction(depth) {{
  const d = HUIZINGA_BUILT_UP.depths, f = HUIZINGA_BUILT_UP.damage;
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
// Maps a real DEFRA hazard rating (depth * (speed + 1.5)) onto the same
// 0..1 scale huizingaDamageFraction uses, via the same RISK_BANDS
// thresholds (low/moderate/significant/extreme) this app already
// classifies road hazard with -- so roads and buildings share one
// visual ramp even though they're driven by two different real formulas.
function defraHazardFraction(hr) {{
  const targets = [0, 0.30, 0.55, 0.75, 0.95];  // fraction at each band's upper edge (low..extreme, plus 0 at hr=0)
  let lo = 0, loT = 0;
  for (let i = 0; i < RISK_BANDS.length; i++) {{
    const [, hi] = RISK_BANDS[i];
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
  [0.00, [0x2e, 0xcc, 0x71]],  // green -- dry / minimal risk right now
  [0.30, [0xf2, 0xd9, 0x4e]],  // yellow
  [0.55, [0xf0, 0x88, 0x3e]],  // orange
  [0.90, [0xc8, 0x1e, 0x1e]],  // red -- severe right now
];
function riskColorFromFraction(frac) {{
  frac = Math.min(1, Math.max(0, frac));
  for (let i = 1; i < RISK_COLOR_STOPS.length; i++) {{
    const [f0, c0] = RISK_COLOR_STOPS[i - 1], [f1, c1] = RISK_COLOR_STOPS[i];
    if (frac <= f1 || i === RISK_COLOR_STOPS.length - 1) {{
      const t = f1 > f0 ? (frac - f0) / (f1 - f0) : 0;
      const tt = Math.min(1, Math.max(0, t));
      return [
        (c0[0] + (c1[0] - c0[0]) * tt) / 255,
        (c0[1] + (c1[1] - c0[1]) * tt) / 255,
        (c0[2] + (c1[2] - c0[2]) * tt) / 255,
      ];
    }}
  }}
  return [RISK_COLOR_STOPS[0][1][0] / 255, RISK_COLOR_STOPS[0][1][1] / 255, RISK_COLOR_STOPS[0][1][2] / 255];
}}

// Cuts every triangle that touches a cell outside the actual drawn/
// uploaded polygon (VALID[g]===0) out of a PlaneGeometry entirely, instead
// of rendering it as filled ground. Without this, every AOI -- however
// irregular the user actually drew it -- rendered as a solid rectangle
// (NODATA cells were nearest-filled with a plausible elevation purely so
// nothing cliffed, which had the side effect of making them look like
// real terrain), which also made buildings sitting in a bounding-box
// corner outside the true shape look like part of the selection. Reuses
// the geometry's own already-correct index/winding, just removes entries.
function vertGridIndex(idx) {{
  const j = Math.floor(idx / NX), col = idx % NX;
  return (NY - 1 - j) * NX + col;  // same DEM-grid mapping TERRAIN_H/VALID use elsewhere
}}
function cutInvalidTriangles(geo) {{
  const src = geo.index.array;
  const kept = [];
  for (let t = 0; t < src.length; t += 3) {{
    const i0 = src[t], i1 = src[t + 1], i2 = src[t + 2];
    if (VALID[vertGridIndex(i0)] && VALID[vertGridIndex(i1)] && VALID[vertGridIndex(i2)]) kept.push(i0, i1, i2);
  }}
  geo.setIndex(kept);
}}

// ---- exact polygon clipping (terrain + color-overlay meshes only -- the
// animated water surface stays on a coarser whole-cell cut, defined
// further below as cutInvalidTrianglesToAoi, since its per-frame depth
// update indexes vertices by a fixed regular (row,col) grid layout that
// this clip's re-triangulation would break).
//
// cutInvalidTriangles alone drops a whole PlaneGeometry triangle if ANY of
// its 3 vertices sits on a NODATA cell -- cheap, but biased by which way
// each grid quad's fixed diagonal happens to split: verified on a small
// hand-drawn triangle (a 7x11 grid) rendering as a clean straight line on
// one edge and a lopsided sawtooth on the other, purely from that
// diagonal direction, not the real polygon shape. Clipping each triangle
// against the AOI polygon's own true geometry (passed from Python as
// aoi_poly_rc, the exact ring crop_dem_to_polygon was given) removes that
// bias and hugs the real drawn/uploaded shape instead.
let AOI_XZ = null, AOI_SIGN = 1;
if (AOI_POLY && AOI_POLY.length >= 3) {{
  AOI_XZ = AOI_POLY.map(([r, c]) => [worldX(c), worldZ(r)]);
  let area2 = 0;
  for (let i = 0; i < AOI_XZ.length; i++) {{
    const a = AOI_XZ[i], b = AOI_XZ[(i + 1) % AOI_XZ.length];
    area2 += a[0] * b[1] - b[0] * a[1];
  }}
  AOI_SIGN = area2 >= 0 ? 1 : -1;
}}
function pointInAoi(x, z) {{
  if (!AOI_XZ) return true;
  let inside = false;
  for (let i = 0, j = AOI_XZ.length - 1; i < AOI_XZ.length; j = i++) {{
    const [xi, zi] = AOI_XZ[i], [xj, zj] = AOI_XZ[j];
    if (((zi > z) !== (zj > z)) && (x < (xj - xi) * (z - zi) / (zj - zi) + xi)) inside = !inside;
  }}
  return inside;
}}
// Water keeps a whole-cell index cut (its per-frame depth update indexes
// vertices by a fixed regular row/col layout that the precise re-
// triangulating clip above would break -- see the comment on
// clipGeometryToAoi), and deliberately stays conservative: a triangle is
// only kept if ALL 3 of its vertices are valid AND inside the AOI. This
// can leave water very slightly short of the terrain's own (finer,
// exactly-clipped) edge in a boundary cell, which is the safe direction
// to be wrong in -- water sitting a hair inside real terrain reads fine,
// where water extending even one triangle past the terrain's actual edge
// reads as "sliding out" into empty space (the actual bug report). The
// per-vertex AOI_MASK fade (set up in the water block below) then softens
// the edge of whatever DID survive this cut, without ever loosening it.
function cutInvalidTrianglesToAoi(geo) {{
  const src = geo.index.array;
  const kept = [];
  function ok(idx) {{
    const g = vertGridIndex(idx);
    if (!VALID[g]) return false;
    if (!AOI_XZ) return true;
    const j = Math.floor(idx / NX), col = idx % NX;
    return pointInAoi(worldX(col), worldZ(NY - 1 - j));
  }}
  for (let t = 0; t < src.length; t += 3) {{
    const i0 = src[t], i1 = src[t + 1], i2 = src[t + 2];
    if (ok(i0) && ok(i1) && ok(i2)) kept.push(i0, i1, i2);
  }}
  geo.setIndex(kept);
}}
// Interpolates every numeric field of two vertex records at the point
// where segment p0->p1 crosses the infinite line through (ax,az) with
// direction (ex,ez) -- used to build a new boundary vertex exactly on the
// true polygon edge instead of snapping to a grid point.
function segIntersect(p0, p1, ax, az, ex, ez) {{
  const dx = p1.x - p0.x, dz = p1.z - p0.z;
  const denom = dx * ez - dz * ex;
  let t = 0.5;
  if (Math.abs(denom) > 1e-9) t = ((ax - p0.x) * ez - (az - p0.z) * ex) / denom;
  t = Math.max(0, Math.min(1, t));
  const out = {{}};
  for (const k in p0) out[k] = p0[k] + (p1[k] - p0[k]) * t;
  return out;
}}
function clipHalfPlane(poly, ax, az, ex, ez) {{
  if (poly.length === 0) return poly;
  const out = [];
  for (let i = 0; i < poly.length; i++) {{
    const cur = poly[i], prev = poly[(i + poly.length - 1) % poly.length];
    const curIn = (ex * (cur.z - az) - ez * (cur.x - ax)) * AOI_SIGN >= 0;
    const prevIn = (ex * (prev.z - az) - ez * (prev.x - ax)) * AOI_SIGN >= 0;
    if (curIn) {{
      if (!prevIn) out.push(segIntersect(prev, cur, ax, az, ex, ez));
      out.push(cur);
    }} else if (prevIn) {{
      out.push(segIntersect(prev, cur, ax, az, ex, ez));
    }}
  }}
  return out;
}}
function clipTriangleToAoi(v0, v1, v2) {{
  const in0 = pointInAoi(v0.x, v0.z), in1 = pointInAoi(v1.x, v1.z), in2 = pointInAoi(v2.x, v2.z);
  if (in0 && in1 && in2) return [[v0, v1, v2]];
  if (!in0 && !in1 && !in2) return [];
  const minX = Math.min(v0.x, v1.x, v2.x), maxX = Math.max(v0.x, v1.x, v2.x);
  const minZ = Math.min(v0.z, v1.z, v2.z), maxZ = Math.max(v0.z, v1.z, v2.z);
  const margin = CS * 0.5;
  let poly = [v0, v1, v2];
  for (let i = 0; i < AOI_XZ.length && poly.length > 0; i++) {{
    const a = AOI_XZ[i], b = AOI_XZ[(i + 1) % AOI_XZ.length];
    const eMinX = Math.min(a[0], b[0]) - margin, eMaxX = Math.max(a[0], b[0]) + margin;
    const eMinZ = Math.min(a[1], b[1]) - margin, eMaxZ = Math.max(a[1], b[1]) + margin;
    if (eMaxX < minX || eMinX > maxX || eMaxZ < minZ || eMinZ > maxZ) continue;
    poly = clipHalfPlane(poly, a[0], a[1], b[0] - a[0], b[1] - a[1]);
  }}
  if (poly.length < 3) return [];
  const tris = [];
  for (let i = 1; i < poly.length - 1; i++) tris.push([poly[0], poly[i], poly[i + 1]]);
  return tris;
}}
// Rebuilds geo (a PlaneGeometry, already indexed+normaled+colored) as a
// non-indexed geometry with every triangle exactly clipped to AOI_XZ --
// falls back to the coarser whole-cell cutInvalidTriangles when no AOI
// polygon was supplied (older call sites, or a request with no geometry
// on hand), so this is purely additive.
function clipGeometryToAoi(geo) {{
  if (!AOI_XZ) {{ cutInvalidTriangles(geo); return geo; }}
  const src = geo.index.array;
  const pos = geo.attributes.position, nrm = geo.attributes.normal, col = geo.attributes.color;
  function vertAt(i) {{
    const g = vertGridIndex(i);
    const v = {{x: pos.getX(i), y: pos.getY(i), z: pos.getZ(i),
                nx: nrm.getX(i), ny: nrm.getY(i), nz: nrm.getZ(i), valid: VALID[g] === 1}};
    if (col) {{ v.cr = col.getX(i); v.cg = col.getY(i); v.cb = col.getZ(i); }}
    return v;
  }}
  const newPos = [], newNrm = [], newCol = [];
  for (let t = 0; t < src.length; t += 3) {{
    const v0 = vertAt(src[t]), v1 = vertAt(src[t + 1]), v2 = vertAt(src[t + 2]);
    // Only a cheap early-out for triangles fully inside a NODATA area --
    // requiring ALL 3 vertices raster-valid here (the old cutInvalidTriangles
    // rule) would drop every boundary triangle whole, before the precise
    // polygon clip below ever got a chance to keep its still-inside portion.
    if (!v0.valid && !v1.valid && !v2.valid) continue;
    for (const [a, b, c] of clipTriangleToAoi(v0, v1, v2)) {{
      for (const v of [a, b, c]) {{
        newPos.push(v.x, v.y, v.z);
        newNrm.push(v.nx, v.ny, v.nz);
        if (col) newCol.push(v.cr, v.cg, v.cb);
      }}
    }}
  }}
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.Float32BufferAttribute(newPos, 3));
  out.setAttribute('normal', new THREE.Float32BufferAttribute(newNrm, 3));
  if (col) out.setAttribute('color', new THREE.Float32BufferAttribute(newCol, 3));
  return out;
}}

// Terrain height is pre-exaggerated (z_exag, default 15x) before it ever
// reaches this script, so a steep or mountainous AOI can have a Y extent
// many times larger than its own horizontal W/H -- a default camera
// framing based on W/H alone put the camera partway INSIDE tall terrain
// for exactly that kind of AOI (verified: a 40x40-cell, ~2 degree slope
// rendered as an unreadable, near-ground close-up by default). Scale the
// camera distance and clip planes off the larger of the two extents.
let maxTerrainH = 0;
for (let i = 0; i < TERRAIN_H.length; i++) {{ if (TERRAIN_H[i] > maxTerrainH) maxTerrainH = TERRAIN_H[i]; }}
const CAM_SCALE = Math.max(W, H, maxTerrainH);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1417);
scene.fog = new THREE.Fog(0x0b1417, CAM_SCALE*1.6, CAM_SCALE*5);

const camera = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 1, CAM_SCALE*10);
const DEFAULT_CAM_POS = [W*0.6, CAM_SCALE*0.6 + maxTerrainH*0.3, H*0.9 + CAM_SCALE*0.1];
const DEFAULT_TARGET = [0, maxTerrainH*0.3, 0];
camera.position.set(...DEFAULT_CAM_POS);

// logarithmicDepthBuffer: this scene's camera spans a huge near/far ratio
// (near=1, far=10x the domain width, often 10,000+ world units) because
// the same viewer handles AOIs from a few hundred metres to a whole
// province. A standard (linear) WebGL depth buffer concentrates nearly
// all of its precision within the first few percent of that range and
// has almost none left by the time you're 1000+ units out -- exactly
// where the camera usually sits relative to the terrain. That silently
// broke the water-depth overlay: a raised water surface tens of world
// units above the terrain (a real, deliberate gap, not a rounding error)
// was still resolving as "behind" the terrain for most of the mesh,
// because at that camera distance the linear depth buffer could no
// longer tell the two apart. A logarithmic depth buffer keeps usable
// precision across the whole range instead of only near the camera.
const renderer = new THREE.WebGLRenderer({{antialias:true, preserveDrawingBuffer:true, logarithmicDepthBuffer:true}});
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(innerWidth, innerHeight);
document.getElementById('scene').appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.08;
// Without this, dragging can put the camera almost at ground level,
// nearly edge-on to the terrain -- everything (buildings, water, the
// terrain itself) is technically positioned correctly at that point, but
// the view becomes unreadable: a flat plane viewed edge-on reads as a
// thin, foreshortened, floating sliver with buildings appearing to hover
// above or sink below the ground. That confusing framing is what several
// bug reports turned out to actually be -- including one where the
// previous limit here (0.49*PI = 88.2 degrees from vertical, i.e. only
// 1.8 degrees above dead-horizontal) was so close to fully edge-on that
// it didn't actually prevent the problem it was added for. Tightened to
// stay clearly clear of the grazing zone while still allowing a fairly
// low, dramatic angle for judging relief on a large AOI.
controls.maxPolarAngle = Math.PI * 0.42;
// minDistance keeps the camera from ever getting close enough to be
// inside the terrain; maxDistance keeps a stray scroll from losing the
// scene entirely.
controls.minDistance = CAM_SCALE * 0.12;
controls.maxDistance = CAM_SCALE * 6.0;
controls.target.set(...DEFAULT_TARGET);

function resetView() {{
  controls.enabled = false;
  camera.position.set(...DEFAULT_CAM_POS);
  controls.target.set(...DEFAULT_TARGET);
  camera.updateProjectionMatrix();
  controls.update();
  controls.enabled = true;
}}

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const sun = new THREE.DirectionalLight(0xffffff, 0.85);
sun.position.set(-W*0.3, W*0.8, H*0.3);
scene.add(sun);

// ---- terrain ----
let terrainGeo = new THREE.PlaneGeometry(W, H, NX - 1, NY - 1);
terrainGeo.rotateX(-Math.PI / 2);
const tPos = terrainGeo.attributes.position;
const tCol = new Float32Array(tPos.count * 3);
for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {{
  const vi = j * NX + i, g = (NY - 1 - j) * NX + i;
  tPos.setY(vi, TERRAIN_H[g]);
  tCol[vi*3]=TERRAIN_RGB[g*3]; tCol[vi*3+1]=TERRAIN_RGB[g*3+1]; tCol[vi*3+2]=TERRAIN_RGB[g*3+2];
}}
terrainGeo.setAttribute('color', new THREE.BufferAttribute(tCol, 3));
// Smooth normals are computed on the full, uncut grid (correct
// connectivity everywhere) BEFORE clipping -- clipGeometryToAoi carries
// each vertex's own normal through unchanged, and interpolates it at any
// new boundary vertex the clip creates, so shading stays smooth right up
// to the true polygon edge instead of clipping first and getting flat,
// per-triangle normals from a non-indexed post-clip recompute.
terrainGeo.computeVertexNormals();
terrainGeo = clipGeometryToAoi(terrainGeo);
// Plain DEM/hillshade terrain -- no satellite basemap (removed at the
// user's request across every 3D view; see CLAUDE.md).
const terrainMat = new THREE.MeshStandardMaterial({{vertexColors:true, roughness:0.95}});
const terrainMesh = new THREE.Mesh(terrainGeo, terrainMat);
scene.add(terrainMesh);

const grid = new THREE.GridHelper(Math.max(W,H)*1.3, 12, 0x3a4c52, 0x22343a);
grid.position.y = -1;
scene.add(grid);

// ---- color overlay (LULC / damage / hazard drape) ----
if (OVERLAY) {{
  const rgb = new Float32Array(Uint8Array.from(atob(OVERLAY.rgb_b64), c => c.charCodeAt(0)).buffer);
  let geo = new THREE.PlaneGeometry(W, H, NX - 1, NY - 1);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  const col = new Float32Array(pos.count * 3);
  for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {{
    const vi = j * NX + i, g = (NY - 1 - j) * NX + i;
    pos.setY(vi, TERRAIN_H[g] + 0.6);
    col[vi*3]=rgb[g*3]; col[vi*3+1]=rgb[g*3+1]; col[vi*3+2]=rgb[g*3+2];
  }}
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  geo = clipGeometryToAoi(geo);
  // FrontSide, not DoubleSide: this geometry's winding already faces the
  // camera correctly (same construction as the terrain mesh, always
  // viewed from above) -- see the water-animation comment below for why
  // DoubleSide is avoided everywhere in this scene.
  const mat = new THREE.MeshBasicMaterial({{vertexColors:true, transparent:true, opacity:{color_overlay["opacity"] if color_overlay else 0.85}, side:THREE.FrontSide}});
  scene.add(new THREE.Mesh(geo, mat));
}}

// ---- extruded buildings (InstancedMesh for perf with 1000s of buildings) ----
if (BUILDINGS) {{
  const n = BUILDINGS.rows.length;
  const boxGeo = new THREE.BoxGeometry(CS*0.7, 1, CS*0.7);
  const mat = new THREE.MeshStandardMaterial({{roughness:0.8}});
  const mesh = new THREE.InstancedMesh(boxGeo, mat, n);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  for (let k = 0; k < n; k++) {{
    const row = BUILDINGS.rows[k], col_ = BUILDINGS.cols[k];
    const g = gi(row, col_);
    const h = BUILDINGS.heightM * ZEX;
    dummy.position.set(worldX(col_), TERRAIN_H[g] + h/2, worldZ(row));
    dummy.scale.set(1, h, 1);
    dummy.updateMatrix();
    mesh.setMatrixAt(k, dummy.matrix);
    color.set(BUILDINGS.colors[k]);
    mesh.setColorAt(k, color);
  }}
  scene.add(mesh);
}}

// ---- real building footprints, extruded to proper building shapes
// (not grid-cell boxes) and merged into one geometry for performance ----
// BUILDING_MESH / buildingColorRanges live outside the block below so the
// water animation loop (animateWater, much further down) can still update
// this mesh's vertex colors every frame -- see updateBuildingColors.
let BUILDING_MESH = null;
const buildingColorRanges = [];
function updateBuildingColors(seg, mixFrac) {{
  if (!BUILDING_MESH || !buildingColorRanges.length) return;
  const colorAttr = BUILDING_MESH.geometry.attributes.color;
  for (const br of buildingColorRanges) {{
    const segNext = Math.min(seg + 1, br.depths.length - 1);
    const dA = br.depths[seg], dB = br.depths[segNext];
    const d = Math.max(0, dA + (dB - dA) * mixFrac);
    const frac = d > 0.03 ? huizingaDamageFraction(d) : 0;  // genuinely dry snaps to the pure-green endpoint
    const [r, g, b] = riskColorFromFraction(frac);
    for (let i = br.wallStart; i < br.wallStart + br.wallCount; i++) {{
      colorAttr.array[i * 3] = r * 0.82; colorAttr.array[i * 3 + 1] = g * 0.82; colorAttr.array[i * 3 + 2] = b * 0.82;
    }}
    for (let i = br.roofStart; i < br.roofStart + br.roofCount; i++) {{
      colorAttr.array[i * 3] = r; colorAttr.array[i * 3 + 1] = g; colorAttr.array[i * 3 + 2] = b;
    }}
  }}
  colorAttr.needsUpdate = true;
}}
if (FOOTPRINTS.length) {{
  // Bilinear terrain sample at a fractional (row, col) -- a single
  // flat base (even the best single value, the MINIMUM under the
  // footprint) still either floated part of a building above the
  // ground or buried it, because a flat base can never match sloped
  // terrain on all sides with one number. Each wall's bottom edge now
  // follows the real terrain height at its OWN two corners exactly (no
  // approximation, no single "base" value at all), so the building
  // never floats anywhere -- the only thing that can happen on a slope
  // is a short foundation-like step between corners at different
  // heights, which looks like a building sitting on a slope, not one
  // hovering above or sunk into it.
  function sampleTerrainH(row, col) {{
    const r0 = Math.max(0, Math.min(NY - 1, Math.floor(row))), c0 = Math.max(0, Math.min(NX - 1, Math.floor(col)));
    const r1 = Math.min(NY - 1, r0 + 1), c1 = Math.min(NX - 1, c0 + 1);
    const fr = Math.max(0, Math.min(1, row - r0)), fc = Math.max(0, Math.min(1, col - c0));
    const h00 = TERRAIN_H[gi(r0, c0)], h01 = TERRAIN_H[gi(r0, c1)];
    const h10 = TERRAIN_H[gi(r1, c0)], h11 = TERRAIN_H[gi(r1, c1)];
    return (h00 * (1 - fc) + h01 * fc) * (1 - fr) + (h10 * (1 - fc) + h11 * fc) * fr;
  }}

  const positions = [], normals = [], colors = [];
  const roofColor = [0xC9 / 255, 0xA1 / 255, 0x5A / 255], wallColor = [0xB8 / 255, 0x92 / 255, 0x6A / 255];
  function pushTri(p0, p1, p2, nx, ny, nz, color) {{
    positions.push(p0[0], p0[1], p0[2], p1[0], p1[1], p1[2], p2[0], p2[1], p2[2]);
    normals.push(nx, ny, nz, nx, ny, nz, nx, ny, nz);
    colors.push(color[0], color[1], color[2], color[0], color[1], color[2], color[0], color[1], color[2]);
  }}
  // Orients a triangle so its face normal points along (outX,*,outZ)
  // (horizontal reference) or +Y (roofUp=true) -- real footprint polygon
  // winding varies by source data, so this decides winding per-face
  // geometrically instead of assuming a fixed convention.
  function pushOriented(p0, p1, p2, outX, outZ, roofUp, color) {{
    const ux = p1[0]-p0[0], uy = p1[1]-p0[1], uz = p1[2]-p0[2];
    const vx = p2[0]-p0[0], vy = p2[1]-p0[1], vz = p2[2]-p0[2];
    const cx = uy*vz-uz*vy, cy = uz*vx-ux*vz, cz = ux*vy-uy*vx;
    const flip = roofUp ? (cy < 0) : (cx*outX + cz*outZ < 0);
    const len = Math.hypot(cx, cy, cz) || 1;
    const nx = (flip ? -cx : cx)/len, ny = (flip ? -cy : cy)/len, nz = (flip ? -cz : cz)/len;
    if (flip) pushTri(p0, p2, p1, nx, ny, nz, color); else pushTri(p0, p1, p2, nx, ny, nz, color);
  }}

  for (const fp of FOOTPRINTS) {{
    if (fp.poly.length < 4) continue;
    const n = fp.poly.length;
    // Buildings use a much smaller exaggeration than the terrain's full
    // ZEX -- at typical z_exag=15x a real 6m building would render 90
    // world-units tall relative to its own small footprint (looked like a
    // skyscraper), and even a 4x cap visually bridged the real (narrow)
    // gaps between tightly-packed buildings in dense old-city blocks.
    // Terrain still needs full exaggeration to show subtle relief; a
    // building's own height doesn't. Protection structures (dams/canals/
    // etc, fp.raise != null) reuse the same real-scale cap for the same
    // reason -- they're a similar real-world size to a building.
    const h = fp.heightM * Math.min(ZEX, 1.5);
    // raise === false (canal/drainage: lowers the ground) extrudes DOWN
    // from the lowest corner instead of up from the highest one -- same
    // prism geometry either way, just which corner anchors the flat cap
    // and which direction it offsets from there.
    const sign = (fp.raise === false) ? -1 : 1;
    let thisRoofColor = roofColor, thisWallColor = wallColor;
    if (fp.color) {{
      const c = new THREE.Color(fp.color);
      thisRoofColor = [c.r, c.g, c.b];
      thisWallColor = [c.r * 0.82, c.g * 0.82, c.b * 0.82];
    }}
    const xs = [], zs = [], hs = [];
    let maxH = -Infinity, minH = Infinity, ccx = 0, ccz = 0;
    for (const [row, col] of fp.poly) {{
      const x = worldX(col), z = worldZ(row), th = sampleTerrainH(row, col);
      xs.push(x); zs.push(z); hs.push(th);
      if (th > maxH) maxH = th;
      if (th < minH) minH = th;
      ccx += x; ccz += z;
    }}
    ccx /= n; ccz /= n;
    // Raised: cap clears the highest corner, never dips into the ground.
    // Lowered: cap clears (stays below) the lowest corner, never pokes
    // back up through it.
    const capY = (sign > 0 ? maxH : minH) + sign * h;
    // Real embankments/channels are trapezoidal in cross-section (sloped
    // sides), not a vertical-walled block -- a uniform-width prism read as
    // a giant slab/pill rather than a dam or a dug channel. Approximated
    // by insetting the cap ring toward the footprint's own centroid by
    // `taper` (a real parallel-offset polygon would need re-triangulating
    // a differently-buffered ring with no guaranteed per-vertex
    // correspondence to the base ring -- this centroid-inset keeps exact
    // vertex correspondence, which is what the wall loop below needs, at
    // the cost of the taper being slightly less than a true uniform
    // offset right at sharp corners). taper=0 (buildings) keeps the
    // original vertical-walled behavior exactly.
    const taper = fp.taper || 0;
    const txs = xs.map((x, i) => x + (ccx - x) * taper);
    const tzs = zs.map((z, i) => z + (ccz - z) * taper);

    const wallStart = positions.length / 3;
    for (let i = 0; i < n; i++) {{
      const j = (i + 1) % n;
      const midx = (xs[i]+xs[j])/2, midz = (zs[i]+zs[j])/2;
      const edx = xs[j]-xs[i], edz = zs[j]-zs[i];
      let outX = edz, outZ = -edx;  // candidate outward horizontal normal, 90 deg from the edge
      if (outX*(midx-ccx) + outZ*(midz-ccz) < 0) {{ outX = -outX; outZ = -outZ; }}
      const b0 = [xs[i], hs[i], zs[i]], b1 = [xs[j], hs[j], zs[j]];
      const t0 = [txs[i], capY, tzs[i]], t1 = [txs[j], capY, tzs[j]];
      pushOriented(b0, b1, t1, outX, outZ, false, thisWallColor);
      pushOriented(b0, t1, t0, outX, outZ, false, thisWallColor);
    }}
    const wallCount = positions.length / 3 - wallStart;
    // Flat cap, triangulated the same way ExtrudeGeometry/Shape does
    // internally (earcut) -- built from the (possibly tapered/inset) cap
    // ring, not the original footprint, so the cap matches the narrower
    // crest/floor the walls actually converge to.
    const shape2 = new THREE.Shape(txs.map((x, i) => new THREE.Vector2(x, -tzs[i])));
    let roofGeo;
    try {{ roofGeo = new THREE.ShapeGeometry(shape2); }} catch (e) {{ continue; }}
    const roofStart = positions.length / 3;
    const rpos = roofGeo.attributes.position, ridx = roofGeo.index;
    if (ridx) {{
      for (let t = 0; t < ridx.count; t += 3) {{
        const i0 = ridx.getX(t), i1 = ridx.getX(t+1), i2 = ridx.getX(t+2);
        const p0 = [rpos.getX(i0), capY, -rpos.getY(i0)];
        const p1 = [rpos.getX(i1), capY, -rpos.getY(i1)];
        const p2 = [rpos.getX(i2), capY, -rpos.getY(i2)];
        pushOriented(p0, p1, p2, 0, 0, true, thisRoofColor);
      }}
    }}
    const roofCount = positions.length / 3 - roofStart;
    if (fp.depths) buildingColorRanges.push({{wallStart, wallCount, roofStart, roofCount, depths: fp.depths}});
  }}
  if (positions.length) {{
    const merged = new THREE.BufferGeometry();
    merged.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    merged.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    merged.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    BUILDING_MESH = new THREE.Mesh(merged, new THREE.MeshStandardMaterial({{vertexColors:true, roughness:0.85}}));
    scene.add(BUILDING_MESH);
  }}
}}

// ---- lines (roads draped on terrain) -- color animates with the run's
// own real per-timestep depth+speed (a real DEFRA hazard rating computed
// fresh every frame) when available, instead of one static band from the
// run's peak; falls back to a fixed color when no trajectory was given. ----
const roadColorLines = [];
LINES.forEach(line => {{
  const pts = [];
  for (let k = 0; k < line.rows.length; k++) {{
    const g = gi(line.rows[k], line.cols[k]);
    pts.push(new THREE.Vector3(worldX(line.cols[k]), TERRAIN_H[g] + 1.2, worldZ(line.rows[k])));
  }}
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  const mat = new THREE.LineBasicMaterial({{color: line.color || '#5a6b70', linewidth: 2}});
  scene.add(new THREE.Line(geo, mat));
  if (line.depths) roadColorLines.push({{mat, depths: line.depths, speeds: line.speeds}});
}});
function updateRoadColors(seg, mixFrac) {{
  for (const rl of roadColorLines) {{
    const segNext = Math.min(seg + 1, rl.depths.length - 1);
    const dA = rl.depths[seg], dB = rl.depths[segNext];
    const d = Math.max(0, dA + (dB - dA) * mixFrac);
    let speed = 0;
    if (rl.speeds) {{
      const sA = rl.speeds[seg], sB = rl.speeds[segNext];
      speed = Math.max(0, sA + (sB - sA) * mixFrac);
    }}
    const frac = d > 0.03 ? defraHazardFraction(d * (speed + 1.5)) : 0;
    const [r, g, b] = riskColorFromFraction(frac);
    rl.mat.color.setRGB(r, g, b);
  }}
}}

// ---- water animation (Tab 3): continuously interpolated between the two
// nearest real solver snapshots, updated every render frame (up to 60/s)
// instead of the old ~420ms discrete jump-cut between saved frames -- the
// interpolation itself is cheap (one multiply-add per cell, sub-millisecond
// even for large grids) so this runs at full frame rate; the GPU still does
// all the actual rendering, lighting and alpha blending for every one of
// those frames, exactly like the rest of the scene. Never fabricates new
// physics -- it's a linear blend between the two states LISFLOOD-FP
// actually solved, just shown continuously instead of snapping between
// them.
//
// An earlier version of this tried to push the depth itself into a GPU
// vertex shader via textures (uMix uniform, sampled per-vertex) to move
// the interpolation fully onto the GPU. That silently rendered nothing:
// sampling a texture from a VERTEX shader needs "vertex texture fetch"
// support, which many GPUs -- including software/headless renderers --
// report zero units for, and it fails with no compile error or warning,
// just returning 0 for every sample (read as "totally dry" everywhere).
// Doing the blend on the CPU and uploading plain vertex attributes avoids
// that portability trap entirely while still being fast enough to be
// imperceptible from true GPU-side interpolation.
//
// A SECOND, separate bug found while chasing the first: this mesh's
// material used side:DoubleSide, which on at least one real WebGL
// implementation encountered during testing rendered almost nothing (only
// a razor-thin sliver at the mesh's outer edge) despite the geometry and
// vertex data being verified completely correct -- confirmed by swapping
// to FrontSide alone, which rendered the full raised surface perfectly.
// This geometry's winding already faces the camera correctly (identical
// construction to the terrain mesh, always viewed from above), so
// DoubleSide was never actually needed here -- FrontSide is both correct
// and the more broadly-supported setting.
if (WATER) {{
  document.body.classList.add('has-timeline');  // lifts fs/export/export-video buttons above the timeline bar -- see the CSS comment on body.has-timeline
  const waterGeo = new THREE.PlaneGeometry(W, H, NX - 1, NY - 1);
  waterGeo.rotateX(-Math.PI / 2);
  const wPos = waterGeo.attributes.position;
  // RGBA, not RGB -- per-vertex ALPHA (not just color) is what lets the
  // waterline fade in smoothly cell-by-cell instead of a cell popping
  // fully opaque the instant its own depth crosses dryThresh. The old
  // version hid a dry cell by teleporting its vertex to -3 world units
  // below ground and snapping it back up the moment depth crossed the
  // threshold -- geometrically correct per-cell, but visually looked like
  // the flood advanced in discrete blocky pixels rather than a continuous
  // rising sheet, since neighboring cells cross that threshold at
  // slightly different simulated moments.
  const wCol = new Float32Array(wPos.count * 4);
  waterGeo.setAttribute('color', new THREE.BufferAttribute(wCol, 4));
  cutInvalidTrianglesToAoi(waterGeo);
  // cutInvalidTrianglesToAoi only removes triangles at WHOLE-CELL
  // granularity (it has to -- setFrame below indexes every vertex by a
  // fixed row/col, which a precise re-triangulating clip would break).
  // The terrain/overlay meshes get an exact sub-cell clip via
  // clipGeometryToAoi, so a boundary cell the terrain has partly carved
  // away could still show water filling its full, uncut extent -- visibly
  // "sliding out" past the terrain's real edge. Fixed with a per-vertex
  // AOI membership mask multiplied into this vertex's alpha in setFrame:
  // a vertex outside the true polygon is always fully transparent
  // regardless of depth, and GPU interpolation across a straddling
  // triangle fades it smoothly right at the real boundary instead of
  // rendering it fully opaque up to the cut whole-cell edge.
  let AOI_MASK = null;
  if (AOI_XZ) {{
    const gAt = (r, c) => (NY - 1 - r) * NX + c;
    const raw = new Uint8Array(NX * NY);
    for (let row = 0; row < NY; row++) for (let col = 0; col < NX; col++)
      raw[gAt(row, col)] = pointInAoi(worldX(col), worldZ(row)) ? 1 : 0;
    // A single sample per vertex is exactly the DEM's own grid resolution
    // -- the same resolution that made the terrain's OLD whole-cell cut
    // sawtooth-biased (see clipGeometryToAoi above). Rather than pay a
    // fine supersample everywhere (expensive on a big grid), only refine
    // vertices whose raw sample disagrees with a neighbor -- i.e. only
    // the boundary ring -- into a smooth 0..1 coverage fraction, so the
    // water's edge softens into roughly the true polygon shape near the
    // boundary instead of a coarse, grid-aligned zigzag "sliding out"
    // past the terrain's now-precisely-clipped edge.
    AOI_MASK = new Float32Array(raw);
    const SS = 5;
    for (let row = 0; row < NY; row++) {{
      for (let col = 0; col < NX; col++) {{
        const g = gAt(row, col), v = raw[g];
        const nb = [row > 0 ? raw[gAt(row - 1, col)] : v, row < NY - 1 ? raw[gAt(row + 1, col)] : v,
                    col > 0 ? raw[gAt(row, col - 1)] : v, col < NX - 1 ? raw[gAt(row, col + 1)] : v];
        if (nb.every(n => n === v)) continue;  // interior or exterior, not near the boundary
        let hits = 0;
        for (let sr = 0; sr < SS; sr++) for (let sc = 0; sc < SS; sc++) {{
          const rr = row + (sr / (SS - 1) - 0.5), cc = col + (sc / (SS - 1) - 0.5);
          if (pointInAoi(worldX(cc), worldZ(rr))) hits++;
        }}
        AOI_MASK[g] = hits / (SS * SS);
      }}
    }}
  }}
  const waterMesh = new THREE.Mesh(waterGeo, new THREE.MeshBasicMaterial({{vertexColors:true, transparent:true, opacity:0.9, side:THREE.FrontSide}}));
  scene.add(waterMesh);

  const DEPTH = new Float32Array(Uint8Array.from(atob(WATER.depth_b64), c => c.charCodeAt(0)).buffer);
  const nFrames = WATER.nFrames;
  const maxDepth = Math.max(WATER.maxDepth, 0.05);
  const BLUE = ["#f3f9ff","#cde2fb","#9ec5f4","#6da7ec","#3987e5","#256abf","#184f95","#0d366b"].map(h=>[parseInt(h.slice(1,3),16)/255,parseInt(h.slice(3,5),16)/255,parseInt(h.slice(5,7),16)/255]);
  function rampColor(t) {{
    const idx = Math.min(BLUE.length-1.001, Math.max(0, t*(BLUE.length-1)));
    const i0=Math.floor(idx), f=idx-i0, a=BLUE[i0], b=BLUE[Math.min(i0+1,BLUE.length-1)];
    return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f];
  }}

  function setFrame(seg, mixFrac) {{
    const segNext = Math.min(seg + 1, nFrames - 1);
    const baseA = seg * FRAME_STRIDE, baseB = segNext * FRAME_STRIDE;
    const dryT = WATER.dryThresh;
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {{
      const vi = j * NX + i, g = (NY - 1 - j) * NX + i;
      const dA = DEPTH[baseA + g], dB = DEPTH[baseB + g];
      const d = Math.max(0, dA + (dB - dA) * mixFrac);
      // Surface height tracks the real (continuous, interpolated) depth
      // everywhere, even where it's effectively dry -- never teleports --
      // so the mesh itself is one smooth, continuous sheet at all times;
      // only its opacity fades toward 0 as a cell dries out.
      wPos.setY(vi, TERRAIN_H[g] + d * ZEX + 0.4);
      const c = rampColor(Math.min(1, d / maxDepth));
      const insideAoi = AOI_MASK ? AOI_MASK[g] : 1;
      const alpha = insideAoi * Math.min(1, Math.max(0, (d - dryT * 0.4) / (dryT * 1.4)));
      wCol[vi*4]=c[0]; wCol[vi*4+1]=c[1]; wCol[vi*4+2]=c[2]; wCol[vi*4+3]=alpha;
    }}
    wPos.needsUpdate = true; waterGeo.attributes.color.needsUpdate = true;
  }}
  setFrame(0, 0);
  updateBuildingColors(0, 0);
  updateRoadColors(0, 0);

  // Fixed total loop length regardless of how many snapshots the solver
  // saved, so playback is always this fast and always smooth -- not "one
  // real frame per tick" (which at typical save intervals looked like
  // slides popping every ~400-500ms). User-controllable speed multiplies
  // how fast elapsedSeconds advances, not LOOP_SECONDS itself.
  const LOOP_SECONDS = 9.0;
  const nSeg = Math.max(1, nFrames - 1);
  const totalSimMin = (nFrames - 1) * WATER.saveIntMin;

  // ---- falling rain, visible only during the real storm duration ----
  // Purely visual (no rain physics here -- the actual rainfall-runoff is
  // what LISFLOOD-FP already solved into DEPTH); gated by real simulated
  // time so it only falls while the storm is actually active, not for the
  // whole loop (a run's sim_hours includes drain-down time after the
  // storm ends).
  let rainGroup = null, rainState = null;
  const RAIN_COUNT = 900, RAIN_DROP_LEN = 4.0;
  const terrainMaxH = Math.max.apply(null, TERRAIN_H);
  const rainTopY = terrainMaxH + 60;
  if (WATER.durationHr) {{
    const positions = new Float32Array(RAIN_COUNT * 6);
    rainState = {{ x: new Float32Array(RAIN_COUNT), y: new Float32Array(RAIN_COUNT),
                   z: new Float32Array(RAIN_COUNT), speed: new Float32Array(RAIN_COUNT) }};
    for (let k = 0; k < RAIN_COUNT; k++) {{
      rainState.x[k] = (Math.random() - 0.5) * W;
      rainState.z[k] = (Math.random() - 0.5) * H;
      rainState.y[k] = rainTopY - Math.random() * rainTopY;
      rainState.speed[k] = 85 + Math.random() * 40;
    }}
    const rainGeo = new THREE.BufferGeometry();
    rainGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const rainMat = new THREE.LineBasicMaterial({{color: 0xaed4f0, transparent: true, opacity: 0.55}});
    rainGroup = new THREE.LineSegments(rainGeo, rainMat);
    rainGroup.visible = false;
    scene.add(rainGroup);
  }}
  function terrainHeightAt(x, z) {{
    const col = Math.max(0, Math.min(NX - 1, Math.round((x + W / 2) / CS)));
    const rowFromTop = Math.max(0, Math.min(NY - 1, Math.round((z + H / 2) / CS)));  // == NY-1-row
    return TERRAIN_H[rowFromTop * NX + col] || 0;
  }}
  function updateRain(dt, isRaining) {{
    if (!rainGroup) return;
    rainGroup.visible = isRaining;
    if (!isRaining) return;
    const pos = rainGroup.geometry.attributes.position.array;
    for (let k = 0; k < RAIN_COUNT; k++) {{
      let y = rainState.y[k] - rainState.speed[k] * dt * 3;  // real dt, sped up 3x so it reads clearly as rain
      const groundY = terrainHeightAt(rainState.x[k], rainState.z[k]);
      if (y < groundY) {{ y = rainTopY; rainState.x[k] = (Math.random() - 0.5) * W; rainState.z[k] = (Math.random() - 0.5) * H; }}
      rainState.y[k] = y;
      const b = k * 6;
      pos[b] = rainState.x[k]; pos[b + 1] = y; pos[b + 2] = rainState.z[k];
      pos[b + 3] = rainState.x[k]; pos[b + 4] = y - RAIN_DROP_LEN; pos[b + 5] = rainState.z[k];
    }}
    rainGroup.geometry.attributes.position.needsUpdate = true;
  }}

  const playPauseBtn = document.getElementById('playPauseBtn');
  const timeSlider = document.getElementById('timeSlider');
  const timeLabel = document.getElementById('timeLabel');
  const speedSelect = document.getElementById('speedSelect');

  function fmtMin(min) {{
    const h = Math.floor(min / 60), m = Math.round(min % 60);
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
  timeSlider.addEventListener('input', () => {{
    elapsedSeconds = (timeSlider.value / 1000) * LOOP_SECONDS;
  }});
  const endDrag = () => {{ dragging = false; lastNow = null; }};
  timeSlider.addEventListener('mouseup', endDrag);
  timeSlider.addEventListener('touchend', endDrag);
  timeSlider.addEventListener('change', endDrag);

  // ---- export one full animation loop as an MP4 ----
  // Records the live WebGL canvas via MediaRecorder (browser-native,
  // produces WebM -- there is no reliable client-side path to MP4), then
  // uploads that WebM to the server for a one-shot ffmpeg transcode to
  // MP4 and downloads the result. Forces a clean real-time single loop
  // from t=0 for the recording regardless of whatever the live playback
  // state was, then restores it afterward.
  const exportVideoBtn = document.getElementById('exportVideoBtn');
  if (exportVideoBtn) {{
    exportVideoBtn.addEventListener('click', () => {{
      if (exportVideoBtn.disabled) return;
      if (!renderer.domElement.captureStream) {{
        alert('Video export needs canvas.captureStream, which this browser does not support.');
        return;
      }}
      const stream = renderer.domElement.captureStream(30);
      let mimeType = 'video/webm;codecs=vp9';
      if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'video/webm;codecs=vp8';
      if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'video/webm';
      let recorder;
      try {{ recorder = new MediaRecorder(stream, {{mimeType, videoBitsPerSecond: 6000000}}); }}
      catch (e) {{ alert('Video recording is not supported in this browser.'); return; }}

      const chunks = [];
      recorder.ondataavailable = (e) => {{ if (e.data.size) chunks.push(e.data); }};
      const prevPlaying = playing, prevSpeed = speed, prevElapsed = elapsedSeconds;
      const origLabel = exportVideoBtn.textContent;
      playing = true; speed = 1.0; elapsedSeconds = 0; lastNow = null;
      exportVideoBtn.disabled = true;
      // Recording always starts from the default overview angle, not
      // whatever the orbit camera happened to be left at -- an export
      // from a stray close-up/near-ground angle is unreadable (a flat
      // surface viewed edge-on looks like a thin fin, and the water can
      // look like it's floating separately from the ground), and the
      // whole point of this button is a reliable, always-sane result.
      // Left in place afterward too -- a good default view is never a
      // worse thing to end up looking at than whatever was there before.
      resetView();

      recorder.onstop = () => {{
        playing = prevPlaying; speed = prevSpeed; elapsedSeconds = prevElapsed; lastNow = null;
        exportVideoBtn.textContent = 'Encoding...';
        const blob = new Blob(chunks, {{type: mimeType}});
        const fd = new FormData();
        fd.append('video', blob, 'flood.webm');
        fetch('/export-video', {{method: 'POST', body: fd}})
          .then(r => {{ if (!r.ok) return r.json().then(d => {{ throw new Error(d.error || 'export failed'); }}); return r.blob(); }})
          .then(mp4Blob => {{
            const url = URL.createObjectURL(mp4Blob);
            const a = document.createElement('a');
            a.href = url; a.download = 'flood_animation.mp4';
            a.click();
            URL.revokeObjectURL(url);
            exportVideoBtn.disabled = false;
            exportVideoBtn.textContent = origLabel;
          }})
          .catch(err => {{
            alert('Video export failed: ' + err.message);
            exportVideoBtn.disabled = false;
            exportVideoBtn.textContent = origLabel;
          }});
      }};

      recorder.start();
      let elapsedMs = 0;
      const tick = setInterval(() => {{
        elapsedMs += 200;
        exportVideoBtn.textContent = 'Rec ' + Math.min(LOOP_SECONDS, elapsedMs / 1000).toFixed(1) + 's/' + LOOP_SECONDS.toFixed(0) + 's';
        if (elapsedMs >= LOOP_SECONDS * 1000 + 150) {{ clearInterval(tick); recorder.stop(); }}
      }}, 200);
    }});
  }}

  function animateWater(now) {{
    if (lastNow === null) lastNow = now;
    const dt = (now - lastNow) / 1000;
    lastNow = now;
    if (playing && !dragging) {{
      elapsedSeconds = (elapsedSeconds + dt * speed) % LOOP_SECONDS;
    }}
    const t = elapsedSeconds / LOOP_SECONDS;
    const segF = t * nSeg;
    const seg = Math.min(nSeg - 1, Math.floor(segF));
    const mixFrac = segF - seg;
    setFrame(seg, mixFrac);
    updateBuildingColors(seg, mixFrac);
    updateRoadColors(seg, mixFrac);
    const simMinNow = t * totalSimMin;
    updateRain(dt, WATER.durationHr ? (simMinNow < WATER.durationHr * 60) : false);
    if (!dragging) timeSlider.value = Math.round(t * 1000);
    timeLabel.textContent = fmtMin(simMinNow) + ' / ' + fmtMin(totalSimMin);
    requestAnimationFrame(animateWater);
  }}
  requestAnimationFrame(animateWater);
}}

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
requestAnimationFrame(animate);
addEventListener('resize', () => {{
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}});
</script>
"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path
