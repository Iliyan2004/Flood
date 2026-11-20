import sys, os, numpy as np
sys.path.insert(0, os.path.expanduser("~/flood")); sys.path.insert(0, os.path.expanduser("~/flood/paper"))
from figstyle import *
import limits_utils as L
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle, Polygon
OUT = os.path.expanduser("~/flood/manual/figs")
NAVY, TEAL, ORG, GREY = "#14284b", "#1aa89b", "#e8590c", "#6b7280"

def box(ax, x, y, w, h, txt, fc="white", ec=NAVY, tc=NAVY, fs=8.4, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.1", fc=fc, ec=ec, lw=1.3))
    ax.text(x + w / 2, y + h / 2, txt, ha="center", va="center", fontsize=fs, color=tc, fontweight="bold" if bold else "normal", linespacing=1.3)
def arrow(ax, a, b, col=GREY):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=10, color=col, lw=1.3))

def chooser():
    fig, ax = plt.subplots(figsize=(7.2, 4.2)); ax.set_xlim(0, 100); ax.set_ylim(0, 58); ax.axis("off")
    box(ax, 33, 48, 34, 8, "What do you want to know?", fc=NAVY, tc="white", bold=True, fs=9.5)
    opts = [("A quick look at\nwhere water\ncould collect", "SAILAB Instant", "seconds", TEAL, 2),
            ("Depth, speed, damage\nand loss for a storm\nor river flood", "SAILAB Physics", "1 to 5 minutes", NAVY, 26),
            ("Flood from a\nglacial lake\nor a dam", "SAILAB GLOF", "minutes", "#7a4aa0", 50),
            ("Flood from the\nsea: tide and\nstorm surge", "SAILAB Coastal", "seconds to minutes", "#1b6fb8", 74)]
    for q, name, t, col, x in opts:
        box(ax, x, 25, 24, 12, q, fc="#f3f5f9", ec=col, tc="#222", fs=7.8)
        arrow(ax, (50, 47.5), (x + 12, 37.5), col)
        box(ax, x, 6, 24, 14, f"{name}\n{t}", fc=col, ec=col, tc="white", fs=8.4, bold=True)
        arrow(ax, (x + 12, 24.5), (x + 12, 20.5), col)
    fig.savefig(f"{OUT}/chooser.png"); plt.close(fig)

def pipeline():
    fig, ax = plt.subplots(figsize=(7.2, 2.5)); ax.set_xlim(0, 100); ax.set_ylim(0, 30); ax.axis("off")
    st = [("1  Buildings\nand roads", "fetch the area's\nbuildings, roads,\nfacilities"), ("2  Flood\nmodel", "the solver runs the\nstorm over your\nterrain"), ("3  Results", "depth, speed, risk,\nloss in rupees"), ("4  3D scene", "builds the animated\nview and report"), ("5  Done", "open the 3D view,\nImpact and Runs")]
    for i, (a, b) in enumerate(st):
        x = 1 + i * 19.6
        box(ax, x, 15.5, 16.5, 11, a, fc=NAVY if i < 4 else TEAL, ec=NAVY if i < 4 else TEAL, tc="white", bold=True, fs=8.2)
        ax.text(x + 8.25, 8.5, b, ha="center", va="center", fontsize=7.1, color="#333", linespacing=1.35)
        if i < 4: arrow(ax, (x + 16.9, 21), (x + 19.2, 21))
    fig.savefig(f"{OUT}/pipeline.png"); plt.close(fig)

def storm_shape():
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.5))
    t = np.array([0, 1, 2]); r = np.array([0, 40, 0])
    a.fill_between(t, r, color="#9cc3e6", alpha=0.8); a.plot(t, r, color="#1d4e89", lw=2)
    a.set_xlabel("hours"); a.set_ylabel("rain rate (mm/hr)"); a.set_title("Peak 40 mm/hr, 2 hours", fontsize=8.5, loc="left"); a.set_ylim(0, 48)
    a.annotate("peak at half-way", (1, 40), (1.15, 44), fontsize=7.2, color="#14284b"); a.text(1, 14, "total rain\n= 40 mm", ha="center", fontsize=8, color="#14284b")
    t2 = np.array([0, 12, 24]); r2 = np.array([0, 10.85, 0])
    b.fill_between(t2, r2, color="#9cc3e6", alpha=0.8); b.plot(t2, r2, color="#1d4e89", lw=2)
    b.set_xlabel("hours"); b.set_title("100-year design storm: 130 mm in 24 h", fontsize=8.5, loc="left"); b.set_ylim(0, 13); b.text(12, 4, "total rain\n= 130 mm", ha="center", fontsize=8, color="#14284b")
    fig.tight_layout(); fig.savefig(f"{OUT}/storm_shape.png"); plt.close(fig)

def damage_curve():
    d = [0, 0.5, 1, 1.5, 2, 3, 4, 5, 6]; f = [0, 0.30, 0.45, 0.55, 0.62, 0.72, 0.80, 0.86, 0.90]
    fig, ax = plt.subplots(figsize=(7.0, 2.9))
    for lo, hi, col, lab in [(0.05, 0.5, "#f2d94e", "minor"), (0.5, 1.5, "#f0a43a", "moderate"), (1.5, 3, "#e0603a", "major"), (3, 6, "#b02a2a", "severe")]:
        ax.axvspan(lo, hi, color=col, alpha=0.28, lw=0); ax.text((lo + hi) / 2, 0.04, lab, ha="center", fontsize=8.4, color="#222", fontweight="bold")
    ax.plot(d, f, "o-", color=NAVY, lw=2, ms=4); ax.set_xlim(0, 6); ax.set_ylim(0, 1)
    ax.set_xlabel("flood depth at the building (m)"); ax.set_ylabel("share of value lost")
    ax.text(0.25, 0.86, "A building is 'dry' below 5 cm.\nThe curve gives the share of its value that is lost.", fontsize=8, color="#333")
    fig.tight_layout(); fig.savefig(f"{OUT}/damage_curve.png"); plt.close(fig)

def edges():
    fig, ax = plt.subplots(figsize=(5.6, 3.6)); ax.set_xlim(0, 100); ax.set_ylim(0, 70); ax.axis("off")
    ax.add_patch(Rectangle((20, 12), 60, 44, fc="#e9eef5", ec=NAVY, lw=1.6)); ax.text(50, 34, "your area\n(the model runs on\nthis rectangle)", ha="center", va="center", fontsize=8.5, color=NAVY)
    for lab, x, y in [("N (north)", 50, 59.5), ("S (south)", 50, 8.5)]: ax.text(x, y, lab, ha="center", fontsize=8.5, fontweight="bold", color=NAVY)
    ax.text(14, 34, "W", ha="center", fontsize=8.5, fontweight="bold", color=NAVY); ax.text(86, 34, "E", ha="center", fontsize=8.5, fontweight="bold", color=NAVY)
    arrow(ax, (8, 44), (20, 44), "#1b6fb8"); ax.text(1, 47, "inflow\n(river in)", fontsize=7.8, color="#1b6fb8")
    arrow(ax, (80, 20), (93, 20), ORG); ax.text(82, 14, "outflow\n(water leaves)", fontsize=7.8, color=ORG)
    ax.add_patch(Rectangle((20, 40), 0.1, 8, fc="#1b6fb8", ec="#1b6fb8", lw=3)); ax.text(24, 51.5, "position along the edge\n+ width of the channel", fontsize=7.4, color="#1b6fb8")
    fig.savefig(f"{OUT}/edges.png"); plt.close(fig)

def size_limit():
    hrs = np.linspace(4, 72, 60); km2 = [L.max_cells_for(h) * L.CELL_M ** 2 / 1e6 for h in hrs]
    fig, ax = plt.subplots(figsize=(6.6, 2.9)); ax.plot(hrs, km2, color=NAVY, lw=2.4); ax.fill_between(hrs, km2, color="#9cc3e6", alpha=0.5)
    ax.set_xlabel("simulated hours (storm duration + 2 hours)"); ax.set_ylabel("largest area (km²)"); ax.set_ylim(0, 5600); ax.set_xlim(4, 72)
    for h, txt in [(4, "2 h storm"), (26, "24 h design storm"), (50, "2-day storm")]:
        v = L.max_cells_for(h) * L.CELL_M ** 2 / 1e6; ax.plot([h], [v], "o", color=ORG); ax.annotate(f"{txt}\n{v:,.0f} km²", (h, v), (h + 2, v + 450), fontsize=7.6, color="#222")
    ax.text(40, 400, "Areas below the line can be run.\nLarger areas: contact the team.", fontsize=8, color="#14284b")
    fig.tight_layout(); fig.savefig(f"{OUT}/size_limit.png"); plt.close(fig)

for f in (chooser, pipeline, storm_shape, damage_curve, edges, size_limit): f(); print(f.__name__)
