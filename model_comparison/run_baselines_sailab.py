import sys, json, os, subprocess, time
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from case_common import *
out = {}
for name in AOIS:
    d, dem, h, z = prep(name)
    obs = obs_mask(h, z); valid = z != -9999
    prev = (obs & valid).sum() / valid.sum()
    res = {"prevalence_observed_flooded": round(float(prev), 3), "ncols": int(h["ncols"]), "nrows": int(h["nrows"])}
    # trivial baselines
    res["baseline_all_wet"] = score(np.where(valid, 1.0, 0.0), obs, z)
    # oracle-prevalence terrain baseline: wettest = lowest cells, count set to the true observed count
    k = int((obs & valid).sum()); zz = np.where(valid, z, np.inf); thr = np.sort(zz.ravel())[k - 1]
    res["baseline_lowest_terrain_oracle_prevalence"] = score(np.where(valid & (z <= thr), 1.0, 0.0), obs, z)
    for solver in ("acc",):
        wd = os.path.join(d, f"sailab_{solver}")
        cmd = [sys.executable, os.path.expanduser("~/flood/flood_model.py"), "--dem", dem, "--rain-mm-hr", str(PEAK),
               "--duration-hr", str(HOURS), "--friction", "0.05", "--infiltration-mm-hr", "0", "--amc", "50",
               "--outlet-edges", "", "--solver", solver, "--dry-thresh", str(DRY), "--workdir", wd, "--saveint-min", "720"]
        t0 = time.time(); p = subprocess.run(cmd, capture_output=True, text=True); dt = time.time() - t0
        if p.returncode != 0:
            res[f"sailab_{solver}"] = {"error": p.stderr[-300:] or p.stdout[-300:]}; continue
        _, md = flood_model.read_ascii(os.path.join(wd, "results", "res.max"))
        s = score(md, obs, z); s["runtime_s"] = round(dt, 1); s["max_depth_m"] = round(float(md[valid].max()), 2)
        res[f"sailab_{solver}"] = s
    out[name] = res
    print(name, json.dumps(res, indent=1), flush=True)
json.dump(out, open(os.path.join(CMP, "results_sailab_baselines.json"), "w"), indent=1)
