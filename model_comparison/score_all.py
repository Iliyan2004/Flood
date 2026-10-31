import sys, os, json, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from case_common import *
base = json.load(open(os.path.join(CMP, "results_sailab_baselines.json")))
rows = {}
for a in AOIS:
    d, dem, h, z = prep(a); obs = obs_mask(h, z); valid = z != -9999
    r = {"observed_fraction": base[a]["prevalence_observed_flooded"]}
    r["Flood everything"] = base[a]["baseline_all_wet"]
    r["Lowest terrain (oracle fraction)"] = base[a]["baseline_lowest_terrain_oracle_prevalence"]
    r["SAILAB (LISFLOOD-FP ACC)"] = base[a]["sailab_acc"]
    for label, f in (("ANUGA", "anuga_maxdepth.npy"), ("SFINCS", "sfincs_maxdepth.npy"), ("TELEMAC-2D", "telemac_maxdepth.npy")):
        p = os.path.join(CMP, a, f)
        if os.path.exists(p):
            g = np.load(p); s = score(g, obs, z); s["max_depth_m"] = round(float(g[valid].max()), 2); r[label] = s
    rows[a] = r
json.dump(rows, open(os.path.join(CMP, "results_all.json"), "w"), indent=1)
models = ["Flood everything", "Lowest terrain (oracle fraction)", "SAILAB (LISFLOOD-FP ACC)", "ANUGA", "SFINCS", "TELEMAC-2D"]
for a, r in rows.items():
    print(f"\n{a}  (observed flooded {r['observed_fraction']:.0%})")
    for m in models:
        if m in r:
            s = r[m]; print(f"  {m:34s} CSI {s['CSI']:.3f} POD {s['POD']:.3f} FAR {s['FAR']:.3f} F1 {s['F1']:.3f} area x{s['area_ratio']:.2f}" + (f" maxD {s['max_depth_m']}m" if 'max_depth_m' in s else ""))
