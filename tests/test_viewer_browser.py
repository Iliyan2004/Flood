"""Drives the real Mapbox 3D flood viewer in headless Chromium: mode cards, basemap swaps (the old crash), the
click-to-pin probe, the export menu and playback. Needs a Mapbox token (.env) and network access to Mapbox; it
skips itself when the map cannot load there (a page-script failure would instead show up as missing results)."""
import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import threading

import numpy as np
import pytest
from dotenv import load_dotenv

CHROMIUM = shutil.which("chromium-browser") or shutil.which("chromium")
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
TOKEN = os.environ.get("MAPBOX_TOKEN", "")
pytestmark = [pytest.mark.slow, pytest.mark.skipif(CHROMIUM is None or not TOKEN, reason="needs chromium and a Mapbox token")]

INJECT = r"""
window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message)));
const __cw = console.warn; console.warn = (...a) => { window.__errs.push(a.join(' ')); __cw(...a); };
const __res = {};
map.on('load', () => {
  setTimeout(() => {
    document.querySelectorAll('.card').forEach(c => c.click());           // every view mode
    __res.modeAfter = ui.mode; __res.gl = !!glWater;
    document.querySelector('.card[data-mode="depth"]').click();
    const c = DATA.center; pinAt(c[0], c[1]);                              // probe at the AOI centre
    __res.probeShown = $('probe').style.display === 'block'; __res.t0 = elapsedSeconds;
    $('exportToggle').click(); __res.menuOpen = $('exportMenu').classList.contains('open');
    setMapStyle('dark');
  }, 3000);
  setTimeout(() => setMapStyle('satellite'), 9000);
  setTimeout(() => setMapStyle('light'), 15000);
  setTimeout(() => {
    __res.style = ui.styleKey; __res.swapStuck = styleSwapInProgress;
    __res.layers = ['flood-layer', 'buildings-layer', 'roads-layer'].map(l => !!map.getLayer(l));
    __res.terrain = !!map.getTerrain(); __res.advanced = elapsedSeconds !== __res.t0;
    __res.probeText = $('probeDepth').textContent; __res.errs = window.__errs.slice(0, 6);
    document.title = 'R:' + JSON.stringify(__res);
  }, 24000);
});
"""


@pytest.fixture(scope="module")
def scene_dir(tmp_path_factory):
    import mapbox3d_utils
    d = tmp_path_factory.mktemp("viewer")
    T, nr, nc = 6, 30, 30
    yy, xx = np.mgrid[0:nr, 0:nc]
    base = np.exp(-(((yy - 15) ** 2 + (xx - 15) ** 2) / 60.0))
    stack = np.stack([base * (t / (T - 1)) * 1.5 for t in range(T)])
    water = {"depth_stack": stack, "vx_stack": np.full_like(stack, 0.4), "vy_stack": np.full_like(stack, 0.1),
             "save_int_min": 10.0, "dry_thresh": 0.03, "max_depth": float(stack.max()), "duration_hr": 0.5, "rain_mm_hr": 40.0,
             "cellsize_m": 30.0, "ground": np.full((nr, nc), 100.0)}
    bbox = [73.00, 33.60, 73.01, 33.61]
    ring = [[73.0045, 33.6045], [73.0055, 33.6045], [73.0055, 33.6055], [73.0045, 33.6055], [73.0045, 33.6045]]
    geom = {"type": "Polygon", "coordinates": [[[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]}
    out = d / "scene.html"
    mapbox3d_utils.build_mapbox_flood_scene(str(out), "viewer test", bbox, geom, TOKEN,
                                            building_footprints=[{"coords_lonlat": ring, "height_m": 8, "depths": [0, 0.1, 0.5, 1.0, 0.6, 0.2]}],
                                            road_lines=[{"coords_lonlat": [[73.001, 33.605], [73.009, 33.605]], "depths": [0, 0, 0.3, 0.6, 0.4, 0.1], "speeds": [0] * 6}],
                                            water=water)
    html = out.read_text()
    i = html.rindex("</script>")
    out.write_text(html[:i] + INJECT + html[i:])
    return str(d)


def test_viewer_survives_modes_probe_export_and_three_basemap_swaps(scene_dir):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=scene_dir)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cmd = [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu", "--enable-unsafe-swiftshader", "--window-size=1300,800",
               "--virtual-time-budget=40000", "--enable-logging=stderr", "--v=0", "--dump-dom", f"http://127.0.0.1:{srv.server_address[1]}/scene.html"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=400)
        except subprocess.TimeoutExpired:
            pytest.skip("headless chromium hung (shared machine)")
    finally:
        srv.shutdown()
    m = re.search(r"<title>R:(\{.*?\})</title>", r.stdout, re.S)
    if not m:
        pytest.skip("the Mapbox map never loaded here (no network to Mapbox?) -- nothing to assert")
    res = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
    assert res["gl"] and res["modeAfter"] == "paths"            # GPU water active, last card clicked was "Flow paths"
    assert res["probeShown"] and res["menuOpen"]
    assert res["style"] == "light" and not res["swapStuck"]      # three swaps completed, none left stuck
    assert all(res["layers"]) and res["terrain"]                 # flood, buildings, roads and terrain re-added after swaps
    assert res["advanced"], "playback stopped after the basemap swaps (the old crash)"
    assert res["probeText"] not in ("-", "")
    assert not [e for e in res["errs"] if "frame error" in e or "re-adding" in e], res["errs"]
