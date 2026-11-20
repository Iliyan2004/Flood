"""Render display equations to SVG with matplotlib mathtext (no LaTeX installation needed)."""
import os, hashlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["svg.fonttype"] = "path"
matplotlib.rcParams["mathtext.fontset"] = "stix"
OUT = os.path.expanduser("~/flood/paper/build/eq")
os.makedirs(OUT, exist_ok=True)
def render(tex, size=13):
    key = hashlib.md5((tex + str(size)).encode()).hexdigest()[:12]
    path = f"{OUT}/{key}.svg"
    if not os.path.exists(path):
        fig = plt.figure(figsize=(0.1, 0.1)); fig.text(0, 0, f"${tex}$", fontsize=size, color="#1b1b1b")
        fig.savefig(path, format="svg", bbox_inches="tight", pad_inches=0.04, transparent=True); plt.close(fig)
    return path
