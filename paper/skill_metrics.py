import sys, os, json
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from case_common import *
from scipy.stats import rankdata
def auc(depth, obs, valid):
    x = depth[valid]; y = obs[valid]
    n1 = y.sum(); n0 = (~y).sum()
    r = rankdata(x)               # average ranks handle the many ties at zero
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
out = {}
for name in AOIS:
    d, dem, h, z = prep(name); valid = z != -9999; obs = obs_mask(h, z) & valid
    maps = {"SAILAB": flood_model.read_ascii(os.path.join(d, "sailab_acc", "results", "res.max"))[1]}
    for m in ("anuga", "sfincs", "telemac"):
        p = os.path.join(d, f"{m}_maxdepth.npy")
        if os.path.exists(p): maps[m.upper() if m != "telemac" else "TELEMAC-2D"] = np.load(p)
    # terrain-only predictor: lower elevation = wetter
    maps["Terrain only (-elevation)"] = -z.astype("float64")
    out[name] = {"obs_frac": float(obs.sum() / valid.sum())}
    for k, m in maps.items():
        m = np.asarray(m, dtype="float64")
        if m.shape != z.shape: out[name][k] = {"error": f"shape {m.shape} vs {z.shape}"}; continue
        w = (m > 0.05) & valid if not k.startswith("Terrain") else None
        row = {"AUC": round(auc(m, obs, valid), 3)}
        if w is not None:
            tp = int((w & obs).sum()); fp = int((w & ~obs & valid).sum()); fn = int((~w & obs & valid).sum()); tn = int((~w & ~obs & valid).sum())
            pod = tp / max(tp + fn, 1); pofd = fp / max(fp + tn, 1)
            row["PSS"] = round(pod - pofd, 3)                      # Peirce skill score (0 = no skill, includes flood-everything)
            n = tp + fp + fn + tn; e = ((tp + fn) * (tp + fp) + (tn + fn) * (tn + fp)) / n
            row["HSS"] = round((tp + tn - e) / (n - e), 3)         # Heidke skill score vs chance
        out[name][k] = row
    print(name, json.dumps(out[name]), flush=True)
json.dump(out, open(os.path.expanduser("~/flood/paper/data/skill_metrics.json"), "w"), indent=1)
