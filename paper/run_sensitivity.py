"""SAILAB sensitivity on the controlled 2022 Sindh case (three 13 km areas, closed boundaries unless stated), scored against the
UNOSAT VIIRS flood extent of 29 Nov 2022. Reuses model_comparison/case_common.py (same DEM, storm, scoring as the multi-model study)."""
import json, os, subprocess, sys, time, shutil
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from case_common import *
OUT = os.path.expanduser("~/flood/paper/data/sensitivity.json")
NPY = os.path.expanduser("~/flood/paper/data/sens_maps"); os.makedirs(NPY, exist_ok=True)
THRESH = [0.05, 0.10, 0.20, 0.30, 0.50, 1.00]
res = json.load(open(OUT)) if os.path.exists(OUT) else {"runs": {}}
# wait for the scaling benchmark to finish so its timings stay clean
log = os.path.expanduser("~/flood/paper/data/bench_scaling.log")
while os.path.exists(log) and "DONE" not in open(log).read():
    time.sleep(10)

def peak_for(total_mm, hours): return 2 * total_mm / hours
VARIANTS = [
    ("base",            dict()),
    ("rain_x0.5",       dict(total=RAIN_TOTAL_MM * 0.5)),
    ("rain_x0.75",      dict(total=RAIN_TOTAL_MM * 0.75)),
    ("rain_x1.25",      dict(total=RAIN_TOTAL_MM * 1.25)),
    ("rain_x1.5",       dict(total=RAIN_TOTAL_MM * 1.5)),
    ("rain_x2",         dict(total=RAIN_TOTAL_MM * 2.0)),
    ("n_0.03",          dict(n=0.03)),
    ("n_0.08",          dict(n=0.08)),
    ("n_0.12",          dict(n=0.12)),
    ("infil_2",         dict(infil=2)),
    ("infil_5",         dict(infil=5)),
    ("infil_10",        dict(infil=10)),
    ("open_edges",      dict(outlet="N,E,S,W")),
    ("window_10d",      dict(total=424.6, hours=240)),
]
for name in AOIS:
    d, dem, h, z = prep(name)
    for vname, v in VARIANTS:
        key = f"{name}|{vname}"
        if key in res["runs"]: continue
        dem_use, h_use, z_use = dem, h, z
        hours = v.get("hours", HOURS); total = v.get("total", RAIN_TOTAL_MM)
        wd = os.path.join(d, "sens_" + vname); shutil.rmtree(wd, ignore_errors=True)
        cmd = [sys.executable, os.path.expanduser("~/flood/flood_model.py"), "--dem", dem_use, "--rain-mm-hr", str(peak_for(total, hours)),
               "--duration-hr", str(hours), "--friction", str(v.get("n", 0.05)), "--infiltration-mm-hr", str(v.get("infil", 0)), "--amc", "50",
               "--outlet-edges", v.get("outlet", ""), "--solver", "acc", "--dry-thresh", "0.05", "--workdir", wd, "--saveint-min", "720"]
        t0 = time.time(); p = subprocess.run(cmd, capture_output=True, text=True); dt = time.time() - t0
        if p.returncode != 0 or not os.path.exists(os.path.join(wd, "results", "res.max")):
            res["runs"][key] = {"error": (p.stderr or p.stdout)[-300:]}; json.dump(res, open(OUT, "w"), indent=1); print(key, "FAILED", flush=True); continue
        _, md = flood_model.read_ascii(os.path.join(wd, "results", "res.max"))
        valid = z_use != -9999; obs = obs_mask(h_use, z_use)
        sc = {f"{t:.2f}": score(md, obs, z_use, dry=t) for t in THRESH}
        res["runs"][key] = {"variant": vname, "area": name, "runtime_s": round(dt, 1), "max_depth_m": round(float(md[valid].max()), 2),
                            "obs_frac": round(float((obs & valid).sum() / valid.sum()), 3), "scores": sc}
        if vname in ("base", "open_edges"):
            np.save(os.path.join(NPY, f"{name}_{vname}_maxdepth.npy"), md.astype("float32"))
            np.save(os.path.join(NPY, f"{name}_obs.npy"), obs); np.save(os.path.join(NPY, f"{name}_dem.npy"), z.astype("float32"))
        json.dump(res, open(OUT, "w"), indent=1)
        shutil.rmtree(wd, ignore_errors=True)
        print(key, "CSI@5cm", sc["0.05"]["CSI"], "t", round(dt), flush=True)
print("DONE")
