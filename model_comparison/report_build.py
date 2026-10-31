import sys, os, json, base64, io, re, glob
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from case_common import *

MODELS = ["SAILAB (LISFLOOD-FP ACC)", "ANUGA", "SFINCS", "TELEMAC-2D"]
SHORT = {"SAILAB (LISFLOOD-FP ACC)": "SAILAB", "ANUGA": "ANUGA", "SFINCS": "SFINCS", "TELEMAC-2D": "TELEMAC-2D",
         "Flood everything": "Flood everything", "Lowest terrain (oracle fraction)": "Lowest terrain"}
COL = {"SAILAB": "#0b7a75", "ANUGA": "#c0662b", "SFINCS": "#5b5ea6", "TELEMAC-2D": "#b8423f",
       "Flood everything": "#9aa0a6", "Lowest terrain": "#c9ccd1"}
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 130})

def b64(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight"); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()

def read_asc(p): return np.loadtxt(p, skiprows=6)
def grids(a):
    g = {}
    g["SAILAB (LISFLOOD-FP ACC)"] = read_asc(os.path.join(CMP, a, "sailab_acc", "results", "res.max"))
    for m, f in (("ANUGA", "anuga"), ("SFINCS", "sfincs"), ("TELEMAC-2D", "telemac")):
        g[m] = np.load(os.path.join(CMP, a, f + "_maxdepth.npy"))
    return g

rows = json.load(open(os.path.join(CMP, "results_all.json")))
areas = list(AOIS)
AL = {"A_manchar_edge": "A: Manchar edge", "B_north_dadu": "B: north Dadu", "C_shikarpur": "C: Shikarpur"}
# runtimes
rt = {}
for a in areas:
    r = {}
    r["SAILAB"] = rows[a]["SAILAB (LISFLOOD-FP ACC)"]["runtime_s"]
    for key, fn in (("ANUGA", f"anuga_{a}.log"), ("SFINCS", f"sfincs_{a}.log"), ("TELEMAC-2D", f"telemac_{a}.log")):
        t = open(os.path.join(CMP, fn)).read()
        r[key] = json.loads(re.findall(r"\{.*\}", t)[-1])["runtime_s"]
    rt[a] = r

figs = {}
# F1 CSI bars
fig, ax = plt.subplots(figsize=(7.2, 3.2)); order = ["Flood everything", "Lowest terrain (oracle fraction)"] + MODELS
w = 0.13
for i, m in enumerate(order):
    ax.bar(np.arange(3) + (i - 2.5) * w, [rows[a][m]["CSI"] for a in areas], w, label=SHORT[m], color=COL[SHORT[m]])
ax.set_xticks(range(3)); ax.set_xticklabels([AL[a] for a in areas]); ax.set_ylabel("CSI (higher is better)"); ax.set_ylim(0.25, 0.6)
ax.legend(ncol=3, fontsize=7, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.22)); figs["csi"] = b64(fig)
# F2 POD vs FAR
fig, axs = plt.subplots(1, 3, figsize=(7.6, 2.8), sharey=True)
for ax, a in zip(axs, areas):
    for m in order:
        s = rows[a][m]; ax.scatter(s["FAR"], s["POD"], s=55, color=COL[SHORT[m]], label=SHORT[m], zorder=3)
    ax.set_title(AL[a], fontsize=8); ax.set_xlabel("False-alarm ratio (lower is better)"); ax.grid(alpha=.25)
    ax.set_xlim(0.3, 0.56)
axs[0].set_ylabel("Probability of detection (higher is better)"); axs[0].set_ylim(0.45, 1.03)
axs[-1].legend(fontsize=6.5, frameon=False, loc="lower right"); figs["podfar"] = b64(fig)
# F3 threshold sensitivity
TH = [0.05, 0.1, 0.2, 0.3, 0.5]; sens = {m: [] for m in order}
data = {}
for a in areas:
    d, dem, h, z = prep(a); data[a] = (obs_mask(h, z), z, grids(a))
for t in TH:
    for m in order:
        vals = []
        for a in areas:
            obs, z, g = data[a]; valid = z != -9999
            if m == "Flood everything": dep = np.where(valid, 9.0, 0.0)
            elif m.startswith("Lowest"):
                k = int((obs & valid).sum()); zz = np.where(valid, z, np.inf); thr = np.sort(zz.ravel())[k - 1]; dep = np.where(valid & (z <= thr), 9.0, 0.0)
            else: dep = g[m]
            vals.append(score(dep, obs, z, dry=t)["CSI"])
        sens[m].append(float(np.mean(vals)))
fig, ax = plt.subplots(figsize=(5.2, 3.0))
for m in order: ax.plot(TH, sens[m], marker="o", ms=4, color=COL[SHORT[m]], label=SHORT[m], ls="--" if m in order[:2] else "-")
ax.set_xlabel("Wet-cell depth threshold (m)"); ax.set_ylabel("Mean CSI over 3 areas"); ax.legend(fontsize=7, frameon=False, ncol=2); figs["sens"] = b64(fig)
# F4 runtime
fig, ax = plt.subplots(figsize=(5.2, 2.8)); ms = ["SAILAB", "TELEMAC-2D", "SFINCS", "ANUGA"]
for i, m in enumerate(ms):
    v = [rt[a][m] for a in areas]; ax.bar(np.arange(3) + (i - 1.5) * 0.19, v, 0.19, label=m, color=COL[m])
ax.set_yscale("log"); ax.set_xticks(range(3)); ax.set_xticklabels([AL[a] for a in areas]); ax.set_ylabel("Wall-clock seconds (log)"); ax.legend(fontsize=7, frameon=False, ncol=4)
figs["rt"] = b64(fig)
# F5 maps (categorical)
cm = ListedColormap(["#e9ecef", "#2b7a4b", "#d9a400", "#c0392b"])  # 0 correct dry, 1 hit, 2 miss, 3 false alarm
def cat(dep, obs, z):
    valid = z != -9999; w_ = dep > DRY; c = np.zeros(z.shape, int)
    c[w_ & obs] = 1; c[~w_ & obs] = 2; c[w_ & ~obs] = 3; return np.ma.masked_where(~valid, c)
for a in areas:
    obs, z, g = data[a]; valid = z != -9999
    fig, axs = plt.subplots(1, 5, figsize=(9.6, 2.3))
    axs[0].imshow(np.ma.masked_where(~valid, obs.astype(int)), cmap=ListedColormap(["#e9ecef", "#2f6db3"]), vmin=0, vmax=1); axs[0].set_title("Observed (UNOSAT)", fontsize=8)
    for ax, m in zip(axs[1:], MODELS):
        ax.imshow(cat(g[m], obs, z), cmap=cm, vmin=0, vmax=3); ax.set_title(SHORT[m] + f"  CSI {rows[a][m]['CSI']:.2f}", fontsize=8)
    for ax in axs: ax.axis("off")
    figs["map_" + a] = b64(fig)
# F6 depth maps area A
a = "A_manchar_edge"; obs, z, g = data[a]
fig, axs = plt.subplots(1, 4, figsize=(9.6, 2.5)); vmax = 1.5
for ax, m in zip(axs, MODELS):
    im = ax.imshow(np.ma.masked_where(z == -9999, np.clip(g[m], 0, vmax)), cmap="Blues", vmin=0, vmax=vmax); ax.set_title(SHORT[m], fontsize=8); ax.axis("off")
fig.colorbar(im, ax=axs, fraction=0.02, label="Peak depth (m, capped at 1.5)"); figs["depthA"] = b64(fig)
# F7 inter-model agreement (CSI between wet masks, mean of areas)
mat = np.zeros((4, 4))
for i, m1 in enumerate(MODELS):
    for j, m2 in enumerate(MODELS):
        vs = []
        for a in areas:
            obs, z, g = data[a]; valid = z != -9999; w1, w2 = (g[m1] > DRY) & valid, (g[m2] > DRY) & valid
            vs.append((w1 & w2).sum() / max((w1 | w2).sum(), 1))
        mat[i, j] = np.mean(vs)
fig, ax = plt.subplots(figsize=(3.9, 3.3)); im = ax.imshow(mat, cmap="Greens", vmin=0.4, vmax=1)
ax.set_xticks(range(4)); ax.set_yticks(range(4)); ax.set_xticklabels([SHORT[m] for m in MODELS], rotation=35, ha="right"); ax.set_yticklabels([SHORT[m] for m in MODELS])
for i in range(4):
    for j in range(4): ax.text(j, i, f"{mat[i,j]:.2f}", ha="center", va="center", fontsize=8)
figs["agree"] = b64(fig)
json.dump({"sens_thresholds": TH, "sens_csi": sens, "runtimes": rt, "agreement": mat.tolist()}, open(os.path.join(CMP, "results_extra.json"), "w"), indent=1)
json.dump(figs, open(os.path.join(CMP, "figs.json"), "w"))
print("figs done", list(figs), "\nsens", {SHORT[k]: [round(x, 3) for x in v] for k, v in sens.items()}, "\nagree", np.round(mat, 2).tolist())
