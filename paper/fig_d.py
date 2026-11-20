import sys, os, json, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/paper"))
from figstyle import *
import content4 as c4
D = os.path.expanduser("~/flood/paper/data")
def fig_sens():
    raw = json.load(open(f"{D}/sensitivity.json")); t = c4.sens_table(raw)
    names = list(t); vals = [t[k]["csi"] for k in names]
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    cols = [C["sailab"] if k == "base" else ("#6b8fb8" if not k.startswith(("dem", "open", "window")) else "#8a5a9e") for k in names]
    y = np.arange(len(names))[::-1]
    ax.barh(y, vals, color=cols, height=0.66)
    for yy, v in zip(y, vals): ax.text(v + 0.004, yy, f"{v:.3f}", va="center", fontsize=7)
    ax.axvline(0.5, color=C["soft"], ls=":", lw=1); ax.axvline(t["base"]["csi"], color=C["sailab"], ls="--", lw=0.8)
    ax.set_yticks(y); ax.set_yticklabels([c4.LABELS[k].split(" (")[0] for k in names], fontsize=7.4)
    ax.set_xlim(0.2, max(0.62, max(vals) + 0.05)); ax.set_xlabel("mean CSI over the areas (wet above 5 cm)"); ax.tick_params(axis="y", length=0)
    fig.savefig(f"{FIG}/fig_sens.png"); plt.close(fig)
if __name__ == "__main__":
    fig_sens(); print("ok")
