#!/usr/bin/env python3
"""
PluviSim GUI — a local web front end for flood_model.py.

Run:
    ~/flood/venv/bin/python gui_app.py
Then open http://127.0.0.1:5050 in a browser.

Fill in the form, hit "Run simulation", and it invokes the real LISFLOOD-FP
pipeline (flood_model.py + build_3d_viewer.py) as a subprocess, then serves
the resulting report / 3D viewer / raw files, plus a one-click zip export.
"""
import glob
import io
import json
import os
import subprocess
import threading
import time
import uuid
import zipfile

from flask import Flask, request, redirect, url_for, send_file, abort, Response, jsonify

from progress_utils import read_sim_minutes, latest_progress

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
FLOOD_MODEL = os.path.join(BASE_DIR, "flood_model.py")
BUILD_3D = os.path.join(BASE_DIR, "build_3d_viewer.py")
RUNS_DIR = os.path.join(BASE_DIR, "gui_runs")
os.makedirs(RUNS_DIR, exist_ok=True)

app = Flask(__name__)
MODEL_NAME = "PluviSim"
ACCENT = "#0E7A8C"

# In-memory run tracking. Lost on server restart, but summary.json/run.log on
# disk survive independently, so history/results still work after a restart.
RUN_STATE = {}
RUN_STATE_LOCK = threading.Lock()

# This machine is shared (DGX box, multiple users) and each simulation spawns
# an OpenMP thread pool across most of the CPU. Cap how many run at once so
# one person's batch of jobs can't starve everyone else; extras simply queue.
MAX_CONCURRENT_SIMS = 1
SIM_SLOT = threading.Semaphore(MAX_CONCURRENT_SIMS)

PAGE_HEAD = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --page:#F3F6F8; --surface:#FFFFFF; --surface-2:#E9F1F3; --ink:#142430; --ink-2:#46606B;
  --muted:#7C929B; --line:#D7E2E5; --accent:{ACCENT}; --accent-soft:#E3F2F4;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --page:#0B1417; --surface:#101B1F; --surface-2:#142327; --ink:#E7EEF0; --ink-2:#A9BEC4;
    --muted:#6E848B; --line:#22343A; --accent:#5FD3E8; --accent-soft:#10333A;
  }}
}}
:root[data-theme="dark"] {{
  --page:#0B1417; --surface:#101B1F; --surface-2:#142327; --ink:#E7EEF0; --ink-2:#A9BEC4;
  --muted:#6E848B; --line:#22343A; --accent:#5FD3E8; --accent-soft:#10333A;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink); font-family:"Public Sans",system-ui,sans-serif; }}
.wrap {{ max-width:760px; margin:0 auto; padding:32px 24px 80px; }}
.brand {{ font-family:"Barlow Condensed",sans-serif; font-weight:700; font-size:30px;
  text-transform:uppercase; letter-spacing:0.01em; border-bottom:2px solid var(--ink);
  padding-bottom:14px; margin-bottom:8px; }}
.brand span {{ color:var(--accent); }}
.tagline {{ color:var(--muted); font-size:13px; margin-bottom:30px; }}
fieldset {{ border:1px solid var(--line); border-radius:10px; padding:18px 20px 20px; margin-bottom:20px; }}
legend {{ font-family:"Barlow Condensed",sans-serif; font-weight:600; text-transform:uppercase;
  letter-spacing:0.05em; font-size:13px; color:var(--ink-2); padding:0 8px; }}
.row {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:14px; }}
.row.single {{ grid-template-columns:1fr; }}
label {{ display:block; font-size:12.5px; color:var(--ink-2); margin-bottom:5px; }}
label .req {{ color:var(--accent); }}
label .hint {{ color:var(--muted); font-weight:400; }}
input[type=text], input[type=number], select {{
  width:100%; padding:9px 10px; border:1px solid var(--line); border-radius:6px;
  background:var(--surface); color:var(--ink); font-family:"IBM Plex Mono",monospace; font-size:13px;
}}
input[type=file] {{ font-size:12.5px; color:var(--ink-2); }}
.checks {{ display:flex; gap:16px; flex-wrap:wrap; }}
.checks label {{ display:flex; align-items:center; gap:6px; font-size:13px; color:var(--ink); }}
.radio-group {{ display:flex; gap:20px; margin-bottom:14px; }}
.radio-group label {{ display:flex; align-items:center; gap:6px; font-size:13px; color:var(--ink); margin:0; }}
.terrain-block {{ display:none; }}
.terrain-block.active {{ display:block; }}
button.submit {{
  width:100%; padding:14px; border:none; border-radius:8px; background:var(--accent);
  color:#06181c; font-family:"Barlow Condensed",sans-serif; font-weight:700; font-size:16px;
  text-transform:uppercase; letter-spacing:0.04em; cursor:pointer;
}}
button.submit:active {{ opacity:0.85; }}
#overlay {{
  display:none; position:fixed; inset:0; background:rgba(11,20,23,0.72); z-index:10;
  align-items:center; justify-content:center; flex-direction:column; gap:14px; color:#fff;
  font-family:"IBM Plex Mono",monospace; text-align:center; padding:20px;
}}
.spinner {{ width:34px; height:34px; border:3px solid rgba(255,255,255,0.25);
  border-top-color:{ACCENT}; border-radius:50%; animation:spin 0.8s linear infinite; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
a.btn {{ display:inline-block; background:var(--surface-2); color:var(--ink); text-decoration:none;
  padding:10px 16px; border-radius:8px; border:1px solid var(--line); font-size:13px; margin:4px 6px 0 0; }}
a.btn.primary {{ background:var(--accent); color:#06181c; border-color:var(--accent); font-weight:600; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
  background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden; margin:20px 0; }}
.tile {{ background:var(--surface); padding:14px 16px; }}
.tile .l {{ font-size:11px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); }}
.tile .v {{ font-family:"IBM Plex Mono",monospace; font-size:20px; margin-top:3px; }}
pre.log {{ background:#0B1417; color:#B8CBCF; padding:14px; border-radius:8px; font-size:11.5px;
  overflow:auto; max-height:320px; }}
nav.top {{ display:flex; justify-content:flex-end; gap:16px; margin-bottom:14px; }}
nav.top a {{ color:var(--muted); font-size:12.5px; text-decoration:none; }}
nav.top a:hover {{ color:var(--accent); }}
.progress-track {{ width:100%; height:14px; background:var(--surface-2); border-radius:8px;
  overflow:hidden; border:1px solid var(--line); }}
.progress-fill {{ height:100%; background:var(--accent); width:2%; transition:width 0.5s ease; }}
.progress-meta {{ display:flex; justify-content:space-between; font-family:"IBM Plex Mono",monospace;
  font-size:12px; color:var(--ink-2); margin-top:8px; }}
.status-pill {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:11px;
  text-transform:uppercase; letter-spacing:0.04em; margin-bottom:14px; }}
.status-pill.queued {{ background:#fab21933; color:#a67400; }}
.status-pill.running {{ background:#0e7a8c22; color:var(--accent); }}
.status-pill.postprocessing {{ background:#0e7a8c22; color:var(--accent); }}
.status-pill.done {{ background:#0ca30c22; color:#0ca30c; }}
.status-pill.failed {{ background:#d03b3b22; color:#d03b3b; }}
table.history {{ width:100%; border-collapse:collapse; font-size:13px; }}
table.history th, table.history td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
table.history th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.04em; }}
table.history td a {{ color:var(--accent); text-decoration:none; }}
</style>
<div class="wrap">
<nav class="top"><a href="/">New run</a><a href="/history">History</a></nav>
"""

PAGE_TAIL = "</div>"


def form_page(error=None):
    err_html = f'<p style="color:#d03b3b;font-size:13px;">{error}</p>' if error else ""
    return PAGE_HEAD + f"""
<div class="brand">{MODEL_NAME} <span>GUI</span></div>
<div class="tagline">Rainfall flood simulation on the LISFLOOD-FP inertial solver — fill in values, run, export.</div>
{err_html}
<form action="/run" method="post" enctype="multipart/form-data" id="runForm">

  <fieldset>
    <legend>Site &amp; storm</legend>
    <div class="row single">
      <div><label>Site name <span class="hint">(label only)</span></label>
        <input type="text" name="site_name" value="Untitled site"></div>
    </div>
    <div class="row">
      <div><label>Peak rainfall intensity <span class="req">*</span> <span class="hint">mm/hr</span></label>
        <input type="number" step="any" name="rain_mm_hr" required value="40"></div>
      <div><label>Storm duration <span class="req">*</span> <span class="hint">hours</span></label>
        <input type="number" step="any" name="duration_hr" required value="2"></div>
    </div>
    <div class="row">
      <div><label>Total simulated time <span class="hint">hours, blank = duration+2</span></label>
        <input type="number" step="any" name="sim_hours" placeholder="auto"></div>
      <div><label>Grid snapshot interval <span class="hint">minutes</span></label>
        <input type="number" step="any" name="saveint_min" value="10"></div>
    </div>
  </fieldset>

  <fieldset>
    <legend>Terrain</legend>
    <div class="radio-group">
      <label><input type="radio" name="terrain_mode" value="synthetic" checked onclick="toggleTerrain()"> Synthetic terrain</label>
      <label><input type="radio" name="terrain_mode" value="dem" onclick="toggleTerrain()"> Upload real DEM</label>
    </div>
    <div class="terrain-block active" id="block-synthetic">
      <div class="row">
        <div><label>Width <span class="hint">m</span></label><input type="number" name="width" value="800"></div>
        <div><label>Height <span class="hint">m</span></label><input type="number" name="height" value="600"></div>
      </div>
      <div class="row">
        <div><label>Cell size <span class="hint">m</span></label><input type="number" name="cellsize" value="10"></div>
        <div><label>Slope <span class="hint">%</span></label><input type="number" step="any" name="slope" value="1.0"></div>
      </div>
    </div>
    <div class="terrain-block" id="block-dem">
      <label>DEM file <span class="hint">ARC/INFO ASCII (.asc / .dem.ascii)</span></label>
      <input type="file" name="dem_file" accept=".asc,.ascii,.dem.ascii,.txt">
    </div>
  </fieldset>

  <fieldset>
    <legend>Physics</legend>
    <div class="row">
      <div><label>Floodplain friction <span class="hint">Manning's n</span></label>
        <input type="number" step="any" name="friction" value="0.05"></div>
      <div><label>CFL number</label><input type="number" step="any" name="cfl" value="0.7"></div>
    </div>
    <div class="row">
      <div><label>Infiltration / ground loss <span class="hint">mm/hr, 0 = impervious</span></label>
        <input type="number" step="any" name="infiltration_mm_hr" value="0"></div>
      <div><label>'Flooded' depth threshold <span class="hint">m</span></label>
        <input type="number" step="any" name="dry_thresh" value="0.05"></div>
    </div>
    <div class="row">
      <div><label>River/channel inflow <span class="hint">m³/s, 0 = pluvial-only</span></label>
        <input type="number" step="any" name="inflow_m3s" value="0"></div>
      <div><label>Inflow edge</label>
        <select name="inflow_edge"><option>N</option><option>E</option><option>S</option><option>W</option></select></div>
    </div>
    <label>Outlet edges <span class="hint">where water may drain out</span></label>
    <div class="checks">
      <label><input type="checkbox" name="outlet_edges" value="N" checked> N</label>
      <label><input type="checkbox" name="outlet_edges" value="E" checked> E</label>
      <label><input type="checkbox" name="outlet_edges" value="S" checked> S</label>
      <label><input type="checkbox" name="outlet_edges" value="W" checked> W</label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Output</legend>
    <label><input type="checkbox" name="make_3d" checked> Also generate interactive 3D viewer</label>
  </fieldset>

  <button class="submit" type="submit">Run simulation</button>
</form>

<div id="overlay">
  <div class="spinner"></div>
  <div>Running LISFLOOD-FP&hellip;<br><span style="color:#8fa3a9">this can take 30s–2min depending on grid size</span></div>
</div>
<script>
function toggleTerrain() {{
  var mode = document.querySelector('input[name=terrain_mode]:checked').value;
  document.getElementById('block-synthetic').classList.toggle('active', mode === 'synthetic');
  document.getElementById('block-dem').classList.toggle('active', mode === 'dem');
}}
document.getElementById('runForm').addEventListener('submit', function() {{
  document.getElementById('overlay').style.display = 'flex';
}});
</script>
""" + PAGE_TAIL


def results_page(run_id, summary, made_3d, elapsed):
    def tile(label, value):
        return f'<div class="tile"><div class="l">{label}</div><div class="v">{value}</div></div>'

    tiles = "".join([
        tile("Flooded area", f'{summary["flooded_area_km2"]:.3f} km²'),
        tile("Max depth", f'{summary["max_depth_m"]:.2f} m'),
        tile("Peak hazard", summary["peak_hazard_band"].title()),
        tile("Mass-balance error", f'{summary["final_mass_error_m3"]:.1e} m³'),
        tile("Run time", f'{elapsed:.0f} s'),
    ])
    viewer_link = (f'<a class="btn" href="/runs/{run_id}/viewer_3d.html" target="_blank">Open 3D viewer</a>'
                   if made_3d else "")
    return PAGE_HEAD + f"""
<div class="brand">{MODEL_NAME} <span>GUI</span></div>
<div class="tagline">Simulation complete.</div>
<div class="tiles">{tiles}</div>
<a class="btn primary" href="/runs/{run_id}/report.html" target="_blank">Open full report</a>
{viewer_link}
<a class="btn" href="/runs/{run_id}/download">Download all (.zip)</a>
<a class="btn" href="/">Run another simulation</a>
""" + PAGE_TAIL


def error_page(message, log=""):
    log_html = f"<pre class='log'>{log}</pre>" if log else ""
    return PAGE_HEAD + f"""
<div class="brand">{MODEL_NAME} <span>GUI</span></div>
<p style="color:#d03b3b;">{message}</p>
{log_html}
<a class="btn" href="/">Back</a>
""" + PAGE_TAIL


def status_page(run_id, site_name):
    return PAGE_HEAD + f"""
<div class="brand">{MODEL_NAME} <span>GUI</span></div>
<div class="tagline">{site_name}</div>
<div id="pill" class="status-pill queued">Queued</div>
<div class="progress-track"><div id="fill" class="progress-fill"></div></div>
<div class="progress-meta">
  <span id="metaLeft">waiting for a free slot&hellip;</span>
  <span id="metaRight"></span>
</div>
<div id="failBox" style="display:none;margin-top:20px;">
  <p style="color:#d03b3b;">The simulation failed. Log below:</p>
  <pre class="log" id="failLog"></pre>
</div>
<script>
const runId = {json.dumps(run_id)};
function poll() {{
  fetch('/runs/' + runId + '/progress.json').then(r => r.json()).then(d => {{
    const pill = document.getElementById('pill');
    pill.className = 'status-pill ' + d.status;
    pill.textContent = d.status.charAt(0).toUpperCase() + d.status.slice(1);
    if (d.status === 'queued') {{
      document.getElementById('metaLeft').textContent = 'Waiting for another simulation to finish (max {MAX_CONCURRENT_SIMS} at a time on this machine)...';
      document.getElementById('fill').style.width = '2%';
    }} else if (d.status === 'running' || d.status === 'postprocessing') {{
      document.getElementById('fill').style.width = Math.max(3, d.pct || 3) + '%';
      const simMin = d.sim_min ? d.sim_min.toFixed(0) : '?';
      const modelMin = d.model_min != null ? d.model_min.toFixed(0) : '0';
      document.getElementById('metaLeft').textContent = d.status === 'postprocessing'
        ? 'Solver finished, building report / 3D viewer...'
        : ('Model time ' + modelMin + ' / ' + simMin + ' min simulated');
      document.getElementById('metaRight').textContent = (d.elapsed || 0).toFixed(0) + 's elapsed';
    }} else if (d.status === 'done') {{
      document.getElementById('fill').style.width = '100%';
      window.location = '/runs/' + runId + '/results';
    }} else if (d.status === 'failed') {{
      document.getElementById('failBox').style.display = 'block';
      document.getElementById('failLog').textContent = d.log_tail || '';
      return;
    }}
    setTimeout(poll, 1000);
  }}).catch(() => setTimeout(poll, 2000));
}}
poll();
</script>
""" + PAGE_TAIL


def history_page():
    run_dirs = sorted(glob.glob(os.path.join(RUNS_DIR, "*")), reverse=True)
    rows = []
    for run_dir in run_dirs:
        run_id = os.path.basename(run_dir)
        meta_path = os.path.join(run_dir, "gui_meta.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        with RUN_STATE_LOCK:
            live = RUN_STATE.get(run_id, {})
        status = live.get("status")
        if status is None:
            status = "done" if os.path.exists(os.path.join(run_dir, "summary.json")) else "unknown"
        links = f'<a href="/runs/{run_id}/status">view</a>'
        if status == "done":
            links = (f'<a href="/runs/{run_id}/report.html" target="_blank">report</a> &middot; '
                     f'<a href="/runs/{run_id}/results">summary</a> &middot; '
                     f'<a href="/runs/{run_id}/download">zip</a>')
        rows.append(
            f"<tr><td>{meta.get('created_at', run_id)}</td>"
            f"<td>{meta.get('site_name','-')}</td>"
            f"<td>{meta.get('rain_mm_hr','-')} mm/hr &middot; {meta.get('duration_hr','-')} h</td>"
            f'<td><span class="status-pill {status}" style="margin:0;">{status}</span></td>'
            f"<td>{links}</td></tr>"
        )
    table = ("<table class='history'><tr><th>When</th><th>Site</th><th>Storm</th>"
              "<th>Status</th><th>Links</th></tr>" + "".join(rows) + "</table>") if rows \
        else "<p style='color:var(--muted);'>No runs yet.</p>"
    return PAGE_HEAD + f"""
<div class="brand">{MODEL_NAME} <span>GUI</span></div>
<div class="tagline">Run history</div>
{table}
""" + PAGE_TAIL


@app.route("/")
def index():
    return form_page()


@app.route("/run", methods=["POST"])
def run_sim():
    f = request.form
    try:
        rain = float(f["rain_mm_hr"]); duration = float(f["duration_hr"])
    except (KeyError, ValueError):
        return form_page(error="Rainfall intensity and storm duration are required numbers.")

    run_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)

    cmd = [VENV_PY, FLOOD_MODEL,
           "--rain-mm-hr", str(rain), "--duration-hr", str(duration),
           "--friction", f.get("friction", "0.05"), "--cfl", f.get("cfl", "0.7"),
           "--saveint-min", f.get("saveint_min", "10"),
           "--dry-thresh", f.get("dry_thresh", "0.05"),
           "--site-name", f.get("site_name", "Untitled site"),
           "--infiltration-mm-hr", f.get("infiltration_mm_hr", "0"),
           "--inflow-m3s", f.get("inflow_m3s", "0"),
           "--inflow-edge", f.get("inflow_edge", "N"),
           "--workdir", run_dir]

    if f.get("sim_hours"):
        cmd += ["--sim-hours", f["sim_hours"]]

    outlet_edges = request.form.getlist("outlet_edges") or ["N", "E", "S", "W"]
    cmd += ["--outlet-edges", ",".join(outlet_edges)]

    if f.get("terrain_mode") == "dem":
        upload = request.files.get("dem_file")
        if not upload or upload.filename == "":
            return form_page(error="Select a DEM file, or switch back to synthetic terrain.")
        dem_path = os.path.join(run_dir, "uploaded.dem.ascii")
        upload.save(dem_path)
        cmd += ["--dem", dem_path]
    else:
        cmd += ["--width", f.get("width", "800"), "--height", f.get("height", "600"),
                "--cellsize", f.get("cellsize", "10"), "--slope", f.get("slope", "1.0")]

    # Record enough to show this run in /history even after a server restart.
    with open(os.path.join(run_dir, "gui_meta.json"), "w") as fh:
        json.dump({
            "site_name": f.get("site_name", "Untitled site"),
            "rain_mm_hr": rain, "duration_hr": duration,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, fh)

    make_3d = bool(f.get("make_3d"))
    with RUN_STATE_LOCK:
        RUN_STATE[run_id] = {"status": "queued", "site_name": f.get("site_name", "Untitled site")}
    threading.Thread(target=execute_run, args=(run_id, run_dir, cmd, make_3d), daemon=True).start()

    return redirect(url_for("run_status_page", run_id=run_id))


def execute_run(run_id, run_dir, cmd, make_3d):
    """Runs in a background thread so the HTTP request returns immediately and
    the browser can poll /progress.json for live status instead of blocking."""
    with SIM_SLOT:  # queues here if MAX_CONCURRENT_SIMS runs are already active
        with RUN_STATE_LOCK:
            RUN_STATE[run_id]["status"] = "running"
            RUN_STATE[run_id]["start"] = time.time()

        log_path = os.path.join(run_dir, "run.log")
        t0 = time.time()
        with open(log_path, "a") as logf:
            logf.write(f"=== PluviSim GUI run started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            logf.flush()
            proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
            elapsed = time.time() - t0
            logf.write(f"=== EXIT_CODE:{proc.returncode} elapsed={elapsed:.1f}s ===\n")

        if proc.returncode != 0:
            with open(log_path) as fh:
                tail = fh.read()[-6000:]
            with RUN_STATE_LOCK:
                RUN_STATE[run_id].update(status="failed", log_tail=tail)
            return

        made_3d = False
        if make_3d:
            with RUN_STATE_LOCK:
                RUN_STATE[run_id]["status"] = "postprocessing"
            with open(log_path, "a") as logf:
                p3 = subprocess.run([VENV_PY, BUILD_3D, "--rundir", run_dir],
                                     stdout=logf, stderr=subprocess.STDOUT, text=True)
                made_3d = p3.returncode == 0
                logf.write(f"=== 3D_EXIT_CODE:{p3.returncode} ===\n")

        summary_path = os.path.join(run_dir, "summary.json")
        if not os.path.exists(summary_path):
            with RUN_STATE_LOCK:
                RUN_STATE[run_id].update(status="failed", log_tail="No summary.json produced.")
            return
        with open(summary_path) as fh:
            summary = json.load(fh)

        with RUN_STATE_LOCK:
            RUN_STATE[run_id].update(status="done", summary=summary, made_3d=made_3d, elapsed=elapsed)


@app.route("/runs/<run_id>/status")
def run_status_page(run_id):
    with RUN_STATE_LOCK:
        st = RUN_STATE.get(run_id, {})
    if st.get("status") == "done":
        return redirect(url_for("run_results_page", run_id=run_id))
    return status_page(run_id, st.get("site_name", run_id))


@app.route("/runs/<run_id>/progress.json")
def run_progress_json(run_id):
    with RUN_STATE_LOCK:
        st = dict(RUN_STATE.get(run_id, {}))
    if not st:
        abort(404)
    run_dir = os.path.join(RUNS_DIR, run_id)
    resp = {"status": st["status"]}

    if st["status"] in ("running", "postprocessing"):
        sim_min = read_sim_minutes(run_dir)
        log_path = os.path.join(run_dir, "run.log")
        model_min = comp_min = None
        if os.path.exists(log_path):
            with open(log_path) as fh:
                model_min, comp_min, _etot = latest_progress(fh.read())
        pct = 3
        if sim_min and model_min:
            pct = min(99, int(model_min / sim_min * 100))
        resp.update(pct=pct, model_min=model_min, sim_min=sim_min,
                    elapsed=time.time() - st.get("start", time.time()))
    elif st["status"] == "done":
        resp.update(pct=100)
    elif st["status"] == "failed":
        resp.update(log_tail=st.get("log_tail", ""))

    return jsonify(resp)


@app.route("/runs/<run_id>/results")
def run_results_page(run_id):
    with RUN_STATE_LOCK:
        st = dict(RUN_STATE.get(run_id, {}))
    run_dir = os.path.join(RUNS_DIR, run_id)
    if st.get("status") == "done":
        return results_page(run_id, st["summary"], st["made_3d"], st["elapsed"])
    # Fallback for a server restart: derive everything from disk instead of memory.
    summary_path = os.path.join(run_dir, "summary.json")
    if not os.path.exists(summary_path):
        abort(404)
    with open(summary_path) as fh:
        summary = json.load(fh)
    made_3d = os.path.exists(os.path.join(run_dir, "viewer_3d.html"))
    return results_page(run_id, summary, made_3d, summary.get("wall_clock_seconds", 0))


@app.route("/history")
def history():
    return history_page()


@app.route("/runs/<run_id>/<path:filename>")
def serve_run_file(run_id, filename):
    run_dir = os.path.join(RUNS_DIR, run_id)
    full = os.path.abspath(os.path.join(run_dir, filename))
    if not full.startswith(os.path.abspath(run_dir) + os.sep) or not os.path.exists(full):
        abort(404)
    with open(full, "rb") as fh:
        data = fh.read()
    mime = "text/html" if full.endswith(".html") else "application/octet-stream"
    return Response(data, mimetype=mime)


@app.route("/runs/<run_id>/download")
def download_run(run_id):
    run_dir = os.path.join(RUNS_DIR, run_id)
    if not os.path.isdir(run_dir):
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(run_dir):
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, run_dir))
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"pluvisim_{run_id}.zip",
                      mimetype="application/zip")


if __name__ == "__main__":
    print(f"[{MODEL_NAME}] GUI running at http://127.0.0.1:5050")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
