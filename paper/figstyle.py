import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
plt.rcParams.update({
    "font.family": "Nimbus Sans", "font.size": 8.5, "axes.titlesize": 9, "axes.labelsize": 8.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#444", "axes.linewidth": 0.7,
    "xtick.color": "#333", "ytick.color": "#333", "figure.dpi": 200, "savefig.dpi": 220, "savefig.bbox": "tight",
    "legend.frameon": False, "legend.fontsize": 8, "axes.grid": False, "figure.facecolor": "white",
})
C = {"sailab": "#1d4e89", "anuga": "#c8742b", "sfincs": "#5c8f6b", "telemac": "#8a5a9e", "base": "#9aa0a6", "terrain": "#555555",
     "hit": "#2c6fbb", "miss": "#e08a1e", "fa": "#c8453b", "dry": "#e9ecef", "ink": "#222222", "soft": "#6b7280"}
MODEL_COL = {"SAILAB": C["sailab"], "ANUGA": C["anuga"], "SFINCS": C["sfincs"], "TELEMAC-2D": C["telemac"]}
FIG = "/home/aliyan/flood/paper/figures"
