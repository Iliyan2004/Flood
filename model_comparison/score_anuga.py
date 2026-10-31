import sys, os, json, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from case_common import *
out = {}
for a in AOIS:
    p = os.path.join(CMP, a, "anuga_maxdepth.npy")
    if not os.path.exists(p): continue
    d, dem, h, z = prep(a); obs = obs_mask(h, z)
    g = np.load(p); s = score(g, obs, z); s["max_depth_m"] = round(float(g[z != -9999].max()), 2)
    out[a] = s; print(a, {k: s[k] for k in ("CSI", "POD", "FAR", "F1", "area_ratio", "max_depth_m")})
json.dump(out, open(os.path.join(CMP, "results_anuga.json"), "w"), indent=1)
