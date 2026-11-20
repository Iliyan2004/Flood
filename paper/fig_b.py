import sys, os, json, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/paper")); sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from figstyle import *
from case_common import *
from matplotlib.patches import Rectangle, Patch
from matplotlib.colors import ListedColormap
D = os.path.expanduser("~/flood/paper/data")
RES = json.load(open(os.path.expanduser("~/flood/cmp/results_all.json")))
EXTRA = json.load(open(os.path.expanduser("~/flood/cmp/results_extra.json")))
SKILL = json.load(open(f"{D}/skill_metrics.json"))
NAMES = {"A_manchar_edge": "A  Manchar edge", "B_north_dadu": "B  North Dadu", "C_shikarpur": "C  Shikarpur"}

def fig_study_area():
    import geopandas as gpd
    g = gpd.read_file(v.GT_SHP).to_crs(4326)
    fig, ax = plt.subplots(figsize=(4.9, 4.2))
    g.plot(ax=ax, color="#9cc3e6", edgecolor="none")
    for name, (lon, lat) in AOIS.items():
        ax.add_patch(Rectangle((lon - HALF, lat - HALF), 2 * HALF, 2 * HALF, fill=False, ec=C["fa"], lw=1.6))
        ax.text(lon + 0.09, lat, NAMES[name][0], color=C["fa"], fontsize=10, fontweight="bold", va="center")
    ax.set_xlim(67.3, 68.9); ax.set_ylim(27.1, 28.5); ax.set_aspect(1 / np.cos(np.radians(27.8)))
    ax.set_xlabel("longitude (°E)"); ax.set_ylabel("latitude (°N)")
    ax.legend(handles=[Patch(fc="#9cc3e6", label="UNOSAT flood water, VIIRS, 29 Nov 2022"), Patch(fc="none", ec=C["fa"], label="13 km test areas A, B, C")], loc="lower left", fontsize=7.4)
    fig.savefig(f"{FIG}/fig_study_area.png"); plt.close(fig)

def fig_csi():
    models = ["Flood everything", "Lowest terrain (oracle fraction)", "SAILAB (LISFLOOD-FP ACC)", "ANUGA", "SFINCS", "TELEMAC-2D"]
    short = {"Flood everything": "Flood everything", "Lowest terrain (oracle fraction)": "Lowest terrain (told the true area)", "SAILAB (LISFLOOD-FP ACC)": "SAILAB", "ANUGA": "ANUGA", "SFINCS": "SFINCS", "TELEMAC-2D": "TELEMAC-2D"}
    cols = [C["base"], "#c9ccd1", C["sailab"], C["anuga"], C["sfincs"], C["telemac"]]
    areas = list(NAMES); fig, ax = plt.subplots(figsize=(6.9, 2.9)); w = 0.13
    for i, m in enumerate(models):
        vals = [RES[a][m]["CSI"] for a in areas]; vals.append(float(np.mean(vals)))
        xs = np.arange(4) + (i - 2.5) * w
        ax.bar(xs, vals, w * 0.92, color=cols[i], label=short[m])
        if i >= 2:
            for x, val in zip(xs, vals): ax.text(x, val + 0.006, f"{val:.2f}", ha="center", fontsize=6.4, color=C["ink"], rotation=90, va="bottom")
    ax.set_xticks(range(4)); ax.set_xticklabels([NAMES[a] for a in areas] + ["mean of three"]); ax.set_ylabel("CSI (wet above 5 cm)")
    ax.set_ylim(0, 0.66); ax.axhline(0.5, color=C["soft"], lw=0.7, ls=":")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.22), fontsize=7.3)
    fig.savefig(f"{FIG}/fig_csi_models.png"); plt.close(fig)

def fig_skill():
    areas = list(NAMES); keys = ["SAILAB", "ANUGA", "TELEMAC-2D", "SFINCS", "Terrain only (-elevation)"]
    cols = [C["sailab"], C["anuga"], C["telemac"], C["sfincs"], C["terrain"]]
    fig, ax = plt.subplots(figsize=(6.4, 2.6)); w = 0.15
    for i, k in enumerate(keys):
        vals = [SKILL[a][k]["AUC"] for a in areas]; xs = np.arange(3) + (i - 2) * w
        ax.bar(xs, vals, w * 0.92, color=cols[i], label="Elevation alone (lower = wetter)" if k.startswith("Terrain") else k)
        for x, val in zip(xs, vals): ax.text(x, val + 0.01, f"{val:.2f}", ha="center", fontsize=6.6)
    ax.axhline(0.5, color=C["fa"], lw=1, ls="--"); ax.text(2.55, 0.505, "chance", color=C["fa"], fontsize=7, va="bottom", ha="right")
    ax.set_xticks(range(3)); ax.set_xticklabels([NAMES[a] for a in areas]); ax.set_ylim(0.3, 0.85); ax.set_ylabel("ROC AUC of peak depth\nagainst the observed extent")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.25), fontsize=7.3); fig.savefig(f"{FIG}/fig_skill_auc.png"); plt.close(fig)

def fig_cutoff():
    t = EXTRA["sens_thresholds"]; fig, ax = plt.subplots(figsize=(5.2, 2.9))
    for k, col, ls, lab in [("Flood everything", C["base"], "-", "Flood everything"), ("Lowest terrain (oracle fraction)", "#c9ccd1", "-", "Lowest terrain (told the true area)"),
                            ("SAILAB (LISFLOOD-FP ACC)", C["sailab"], "-", "SAILAB"), ("ANUGA", C["anuga"], "-", "ANUGA"), ("SFINCS", C["sfincs"], "-", "SFINCS"), ("TELEMAC-2D", C["telemac"], "-", "TELEMAC-2D")]:
        ax.plot([x * 100 for x in t], EXTRA["sens_csi"][k], color=col, ls=ls, lw=2.0 if k.startswith("SAILAB") else 1.3, marker="o", ms=3, label=lab)
    ax.set_xlabel("depth above which a cell counts as wet (cm)"); ax.set_ylabel("mean CSI over areas A, B, C"); ax.set_ylim(0.1, 0.55)
    ax.legend(fontsize=7, ncol=2, loc="lower left"); fig.savefig(f"{FIG}/fig_cutoff.png"); plt.close(fig)

def fig_maps():
    fig, axes = plt.subplots(2, 3, figsize=(6.9, 4.9))
    cm = ListedColormap([C["dry"], C["hit"], C["miss"], C["fa"], "white"])
    for j, name in enumerate(NAMES):
        d, dem, h, z = prep(name); valid = z != -9999; obs = obs_mask(h, z) & valid
        md = flood_model.read_ascii(os.path.join(d, "sailab_acc", "results", "res.max"))[1]; w = (md > 0.05) & valid
        a = np.where(valid, obs.astype(int), 4)
        axes[0, j].imshow(np.where(valid, np.where(obs, 1, 0), 4), cmap=cm, vmin=0, vmax=4, interpolation="nearest")
        cont = np.full(z.shape, 4); cont[valid & ~w & ~obs] = 0; cont[valid & w & obs] = 1; cont[valid & ~w & obs] = 2; cont[valid & w & ~obs] = 3
        axes[1, j].imshow(cont, cmap=cm, vmin=0, vmax=4, interpolation="nearest")
        axes[0, j].set_title(NAMES[name], fontsize=9)
        for r in (0, 1): axes[r, j].axis("off")
    axes[0, 0].text(-0.03, 0.5, "Observed (UNOSAT)", transform=axes[0, 0].transAxes, rotation=90, va="center", ha="right", fontsize=8.5)
    axes[1, 0].text(-0.03, 0.5, "SAILAB against observed", transform=axes[1, 0].transAxes, rotation=90, va="center", ha="right", fontsize=8.5)
    fig.legend(handles=[Patch(fc=C["hit"], label="flooded in both"), Patch(fc=C["miss"], label="missed"), Patch(fc=C["fa"], label="false alarm"), Patch(fc=C["dry"], label="dry in both / observed dry")],
               loc="lower center", ncol=4, fontsize=7.6, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0.03, 0.03, 1, 1)); fig.savefig(f"{FIG}/fig_validation_maps.png"); plt.close(fig)

if __name__ == "__main__":
    for f in (fig_study_area, fig_csi, fig_skill, fig_cutoff, fig_maps):
        f(); print(f.__name__, "ok", flush=True)
