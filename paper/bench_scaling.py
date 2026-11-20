"""Runtime scaling of the SAILAB flood solver (LISFLOOD-FP 8.2 ACC) against domain size, GPU vs CPU, same inputs.
Real SRTM crops of square boxes around Lahore, the app's default storm (40 mm/hr triangular peak, 2 h), nothing but the solver
(no buildings, roads, report scoring or 3D scene; per-step output switched off)."""
import json, math, os, subprocess, sys, time, shutil
sys.path.insert(0, os.path.expanduser("~/flood"))
import numpy as np, polygon_utils, data_paths, flood_model
from shapely.geometry import box, mapping
OUT = os.path.expanduser("~/flood/paper/data/scaling.json")
LON, LAT = 74.33, 31.52
SIZES_KM = [3, 6, 12, 23, 35, 46, 58]
res = json.load(open(OUT)) if os.path.exists(OUT) else {"runs": []}
done = {(r["km"], r["device"]) for r in res["runs"]}
def load_avg(): return os.getloadavg()[0]
for km in SIZES_KM:
    dlat = km / 2 / 111.0; dlon = km / 2 / (111.0 * math.cos(math.radians(LAT)))
    geom = mapping(box(LON - dlon, LAT - dlat, LON + dlon, LAT + dlat))
    d = f"/tmp/bench_{km}"; os.makedirs(d, exist_ok=True); dem = f"{d}/dem.ascii"
    if not os.path.exists(dem):
        polygon_utils.crop_dem_to_polygon(data_paths.get("PAKISTAN_DEM"), geom, dem)
    h, z = flood_model.read_ascii(dem); cells = int(h["ncols"]) * int(h["nrows"])
    for device in ("gpu", "cpu"):
        if (km, device) in done: continue
        if device == "cpu" and cells > 700_000: continue      # a single CPU thread would take far too long
        wd = f"{d}/{device}"; shutil.rmtree(wd, ignore_errors=True)
        env = dict(os.environ); 
        if device == "cpu": env["SAILAB_FORCE_CPU"] = "1"
        cmd = [sys.executable, os.path.expanduser("~/flood/flood_model.py"), "--dem", dem, "--rain-mm-hr", "40", "--duration-hr", "2",
               "--friction", "0.05", "--infiltration-mm-hr", "0", "--amc", "50", "--solver", "acc", "--workdir", wd, "--saveint-min", "100000"]
        la = load_avg(); t0 = time.time()
        p = subprocess.run(["/usr/bin/time", "-v"] + cmd, capture_output=True, text=True, env=env)
        wall = time.time() - t0
        rss = None
        for line in p.stderr.splitlines():
            if "Maximum resident set size" in line: rss = int(line.split(":")[1]) / 1024.0
        solver_s = None
        sj = f"{wd}/summary.json"
        if os.path.exists(sj):
            s = json.load(open(sj)); solver_s = s.get("wall_clock_seconds")
        row = {"km": km, "ncols": int(h["ncols"]), "nrows": int(h["nrows"]), "cells": cells, "device": device, "ok": p.returncode == 0,
               "total_wall_s": round(wall, 1), "solver_wall_s": round(solver_s, 1) if solver_s else None, "peak_rss_mb": round(rss, 0) if rss else None,
               "load_avg_at_start": round(la, 1)}
        res["runs"].append(row); print(row, flush=True)
        json.dump(res, open(OUT, "w"), indent=1)
        shutil.rmtree(wd, ignore_errors=True)
res["machine"] = {"gpu": "NVIDIA GB10 (compute 12.1), driver 580.178.04, CUDA 13.0", "cpu_cores": os.cpu_count(), "note": "shared server; other users' jobs were running (load average shown per run)"}
json.dump(res, open(OUT, "w"), indent=1)
print("DONE")
