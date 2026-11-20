import sys, os, json, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/paper")); sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from figstyle import *
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
D = os.path.expanduser("~/flood/paper/data")

def fig_workflow():
    fig, ax = plt.subplots(figsize=(6.9, 2.7)); ax.set_xlim(0, 100); ax.set_ylim(0, 40); ax.axis("off")
    steps = [("1  Simulate", "area + flood type\ninstant preview,\nthen GPU physics run", C["sailab"]),
             ("2  Quantify", "depth, speed, arrival\nbuilding damage class\nloss in PKR, AAL", "#2f7d6d"),
             ("3  Protect", "embankments, drains,\nbasins, nature-based\nplacement optimiser", "#b4691d"),
             ("4  Monitor", "watch a site forecast\nrain over threshold:\nrun + alert", "#8a4a7b")]
    for i, (t, b, col) in enumerate(steps):
        x = 2 + i * 24.5
        ax.add_patch(FancyBboxPatch((x, 17), 21, 20, boxstyle="round,pad=0.3,rounding_size=1.2", fc="white", ec=col, lw=1.4))
        ax.add_patch(Rectangle((x - 0.3, 33.2), 21.6, 3.8, fc=col, ec=col))
        ax.text(x + 10.5, 35.1, t, ha="center", va="center", color="white", fontsize=9, fontweight="bold")
        ax.text(x + 10.5, 24.5, b, ha="center", va="center", fontsize=7.4, color=C["ink"], linespacing=1.35)
        if i < 3: ax.add_patch(FancyArrowPatch((x + 21.6, 27), (x + 24.3, 27), arrowstyle="-|>", mutation_scale=9, color=C["soft"], lw=1))
    ax.add_patch(FancyBboxPatch((2, 2.2), 96, 11, boxstyle="round,pad=0.3,rounding_size=1.2", fc="#eef2f7", ec="#9db0c8", lw=1))
    ax.text(50, 10.6, "Shared data layer and one engine", ha="center", fontsize=8.6, fontweight="bold", color=C["sailab"])
    ax.text(50, 5.6, "terrain  ·  buildings  ·  roads  ·  schools and hospitals  ·  dams  ·  glacial lakes  ·  land cover  ·  soils  ·  rainfall", ha="center", fontsize=7.4, color=C["ink"])
    for i in range(4):
        x = 12.5 + i * 24.5
        ax.add_patch(FancyArrowPatch((x, 17), (x, 13.6), arrowstyle="<|-|>", mutation_scale=7, color="#9db0c8", lw=0.9))
    fig.savefig(f"{FIG}/fig_workflow.png"); plt.close(fig)

def fig_data_layers():
    rows = [("Road segments (HOTOSM / OSM, 2025-07)", 1113962), ("Schools", 65909), ("Hospitals and health units", 35561),
            ("Glacial lakes (PMD 2024 inventory)", 3378), ("Tehsil boundaries", 578), ("Railway stations", 655), ("Airports", 136),
            ("Dams (Global Dam Watch)", 101), ("Reservoirs (Global Dam Watch)", 86), ("Transmission lines", 419)]
    rows.sort(key=lambda r: r[1])
    fig, ax = plt.subplots(figsize=(6.4, 3.1))
    ax.barh([r[0] for r in rows], [r[1] for r in rows], color=C["sailab"], height=0.62)
    ax.set_xscale("log"); ax.set_xlim(30, 4e6); ax.set_xlabel("features (log scale)")
    for i, r in enumerate(rows): ax.text(r[1] * 1.15, i, f"{r[1]:,}", va="center", fontsize=7.6, color=C["ink"])
    ax.tick_params(axis="y", length=0); fig.savefig(f"{FIG}/fig_data_layers.png"); plt.close(fig)

if __name__ == "__main__":
    fig_workflow(); fig_data_layers(); print("ok")
