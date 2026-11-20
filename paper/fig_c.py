import sys, os, json, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/paper"))
from figstyle import *
D = os.path.expanduser("~/flood/paper/data")

def fig_preview():
    rows = json.load(open(f"{D}/preview_vs_full_windows.json"))
    sl = np.array([r["slope"] for r in rows]); q = np.quantile(sl, [1/3, 2/3])
    groups = [("Flattest third\n(< %.1f %%)" % q[0], sl <= q[0]), ("Middle third", (sl > q[0]) & (sl <= q[1])), ("Steepest third\n(> %.0f %%)" % q[1], sl > q[1])]
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.9, 2.9), gridspec_kw={"width_ratios": [1.05, 1]})
    data = []; labs = []
    for lab, m in groups:
        data.append([r["csi"] for r, k in zip(rows, m) if k and r["csi_defined"]]); labs.append(lab)
    bp = a.boxplot(data, widths=0.5, patch_artist=True, medianprops=dict(color="white", lw=1.6), whiskerprops=dict(color="#666"), capprops=dict(color="#666"), flierprops=dict(marker=".", ms=2, mec="#999"))
    for p in bp["boxes"]: p.set(facecolor=C["sailab"], edgecolor=C["sailab"], alpha=0.9)
    a.set_xticklabels(labs, fontsize=7.4); a.set_ylabel("CSI of preview against full run"); a.set_ylim(0, 1); a.set_title("(a)", loc="left", fontsize=9)
    x = np.array([max(r["truth_frac"], 1e-4) for r in rows]); y = np.array([max(r["prev_frac"], 1e-4) for r in rows])
    cols = [C["miss"] if s > q[1] else (C["fa"] if s <= q[0] else C["hit"]) for s in sl]
    b.scatter(x * 100, y * 100, s=7, c=cols, alpha=0.65, lw=0)
    b.plot([0.01, 100], [0.01, 100], color="#444", lw=0.8, ls="--")
    b.set_xscale("log"); b.set_yscale("log"); b.set_xlim(0.01, 100); b.set_ylim(0.01, 100)
    b.set_xlabel("share of window wet, full run (%)"); b.set_ylabel("share wet, preview (%)"); b.set_title("(b)", loc="left", fontsize=9)
    from matplotlib.lines import Line2D
    b.legend(handles=[Line2D([], [], marker="o", ls="", color=C["fa"], label="flattest third", ms=4), Line2D([], [], marker="o", ls="", color=C["hit"], label="middle", ms=4), Line2D([], [], marker="o", ls="", color=C["miss"], label="steepest", ms=4)], loc="lower right", fontsize=7)
    fig.tight_layout(); fig.savefig(f"{FIG}/fig_preview.png"); plt.close(fig)

def fig_runtime():
    d = json.load(open(f"{D}/scaling.json"))["runs"]
    g = sorted([(r["cells"], r["solver_wall_s"]) for r in d if r["device"] == "gpu"]); c = sorted([(r["cells"], r["solver_wall_s"]) for r in d if r["device"] == "cpu"])
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.9, 2.7))
    a.plot(*zip(*g), "o-", color=C["sailab"], lw=1.8, ms=4, label="GPU (NVIDIA GB10)")
    a.plot(*zip(*c), "s-", color=C["anuga"], lw=1.8, ms=4, label="one CPU thread")
    a.set_xscale("log"); a.set_yscale("log"); a.set_xlabel("grid cells"); a.set_ylabel("solver time, 4 h simulated (s)"); a.legend(fontsize=7.4, loc="upper left"); a.set_title("(a)", loc="left", fontsize=9)
    gd = dict(g); cd = dict(c); sp = [(k, cd[k] / gd[k]) for k in sorted(cd)]
    b.plot(*zip(*sp), "o-", color=C["sfincs"], lw=1.8, ms=4); b.axhline(1, color="#888", lw=0.8, ls=":")
    b.set_xscale("log"); b.set_xlabel("grid cells"); b.set_ylabel("GPU speed-up over one CPU thread"); b.set_ylim(0, 8); b.set_title("(b)", loc="left", fontsize=9)
    for k, v in sp: b.text(k, v + 0.25, f"{v:.1f}×", ha="center", fontsize=7)
    fig.tight_layout(); fig.savefig(f"{FIG}/fig_runtime.png"); plt.close(fig)

if __name__ == "__main__":
    fig_preview(); fig_runtime(); print("ok")
