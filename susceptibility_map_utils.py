"""
Static, publication-style classified maps ("susceptibility map" look): hillshade base, coloured class
overlay, graticule, scale bar, north arrow, class legend with ranges / areas / percentages, key-figure
panel and notes. Used for (a) a finished flood run, classed by peak flood depth, and (b) the coastal
susceptibility index.
"""
import os
import textwrap

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource, to_rgb
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import shape

FLOOD_BOUNDS = [0.05, 0.25, 0.5, 1.0, 2.0, 1e9]
FLOOD_NAMES = ["Very low", "Low", "Moderate", "High", "Very high"]
FLOOD_RANGES = ["0.05 - 0.25 m", "0.25 - 0.50 m", "0.50 - 1.0 m", "1.0 - 2.0 m", "> 2.0 m"]
FLOOD_COLORS = ["#fff2a8", "#fdc35c", "#f4803a", "#d7301f", "#7f0000"]


def classify_flood_depth(depth, bounds=FLOOD_BOUNDS):
    """0-based class per cell (-1 = below the first bound / dry, -2 = outside the domain where depth is NaN)."""
    cls = np.full(depth.shape, -1, dtype=int)
    for i in range(len(bounds) - 1):
        cls[(depth >= bounds[i]) & (depth < bounds[i + 1])] = i
    return np.where(np.isnan(depth), -2, cls)


def render_class_map(out_png, title, subtitle, header, geometry, dem, valid, cls, class_names, class_ranges, colors,
                     legend_title="FLOOD SUSCEPTIBILITY", legend_sub="class by peak flood depth", unclassed_label="Not flooded",
                     unclassed_range="< 0.05 m", key_figures=None, notes="", hotspot_values=None, show_roads=True,
                     show_dams=True, footer="", sub2=""):
    """cls: int array, 0..n-1 = classes, -1 = unclassed (shown as plain hillshade), -2 = outside domain."""
    cs = header["cellsize"]; nr, nc = cls.shape
    src = header["src_crs"]
    x0, y0 = header["xllcorner"], header["yllcorner"]
    ext = [x0, x0 + nc * cs, y0, y0 + nr * cs]
    to_ll = Transformer.from_crs(src, "EPSG:4326", always_xy=True)
    to_m = Transformer.from_crs("EPSG:4326", src, always_xy=True)
    n = len(class_names)

    dm = np.where(valid, dem, np.nan)
    fill = np.where(np.isnan(dm), np.nanmean(dm) if valid.any() else 0.0, dm)
    hs = LightSource(azdeg=315, altdeg=40).hillshade(fill, vert_exag=3, dx=cs, dy=cs)
    hs = np.where(valid, hs, np.nan)

    fig = plt.figure(figsize=(16, 11.2), dpi=150, facecolor="white")
    ax = fig.add_axes([0.04, 0.07, 0.62, 0.80]); ax.set_facecolor("#e9edf0")
    ax.imshow(hs, extent=ext, origin="upper", cmap="gray", vmin=0.15, vmax=1.0, interpolation="bilinear")
    ax.imshow(np.where(valid, dem, np.nan), extent=ext, origin="upper", cmap="Greys", alpha=0.10, interpolation="bilinear")
    rgba = np.zeros(cls.shape + (4,))
    for i, c in enumerate(colors):
        m = cls == i; rgba[m, :3] = to_rgb(c); rgba[m, 3] = 0.88
    ax.imshow(rgba, extent=ext, origin="upper", interpolation="nearest")

    g = shape(geometry)
    for p in (list(g.geoms) if g.geom_type == "MultiPolygon" else [g]):
        xs, ys = zip(*[to_m.transform(*c) for c in p.exterior.coords]); ax.plot(xs, ys, color="#111", lw=1.6)
    if show_roads:
        try:
            import geopandas as gpd
            rp = os.path.expanduser("~/flood/global_data/roads_pk/pak_roads_indexed.gpkg")
            if os.path.exists(rp):
                r = gpd.read_file(rp, bbox=g.bounds)
                r = r[r["highway"].isin(["motorway", "trunk", "primary"])].to_crs(src)
                r.plot(ax=ax, color="#2b2b2b", linewidth=0.7, alpha=0.8, zorder=5)
        except Exception as e:
            print("roads skipped:", e)
    if hotspot_values is not None:
        sm = ndimage.maximum_filter(np.nan_to_num(hotspot_values), size=int(2500 / cs) | 1)
        pk = sorted(np.argwhere((np.nan_to_num(hotspot_values) == sm) & (np.nan_to_num(hotspot_values) > 1.0)),
                    key=lambda rc: -hotspot_values[rc[0], rc[1]])
        kept = []
        for r_, c_ in pk:
            X = x0 + (c_ + .5) * cs; Y = y0 + (nr - r_ - .5) * cs
            if all(np.hypot(X - a, Y - b) > 4500 for a, b, _ in kept): kept.append((X, Y, hotspot_values[r_, c_]))
            if len(kept) >= 8: break
        for X, Y, v in kept:
            ax.plot(X, Y, "o", ms=4.5, mfc="white", mec="black", mew=0.9, zorder=8)
            ax.annotate(f"{v:.1f} m", (X, Y), xytext=(6, 6), textcoords="offset points", fontsize=7.5, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#444", lw=0.5, alpha=0.9), zorder=9)
    if show_dams:
        try:
            from dams_utils import fetch_dams_for_bounds
            for d in fetch_dams_for_bounds(*g.bounds):
                X, Y = to_m.transform(d["lon"], d["lat"])
                if ext[0] < X < ext[1] and ext[2] < Y < ext[3]:
                    ax.plot(X, Y, "s", ms=6, mfc="#1f78b4", mec="white", zorder=9)
                    ax.annotate(d.get("name", ""), (X, Y), xytext=(6, -10), textcoords="offset points", fontsize=7,
                                color="#0b3d6b", fontweight="bold", zorder=9)
        except Exception as e:
            print("dams skipped:", e)
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3]); ax.set_aspect("equal")

    lo, la = to_ll.transform(ext[0], ext[2]); hlo, hla = to_ll.transform(ext[1], ext[3])
    step = 0.1 if (hlo - lo) < 1.2 else 0.5
    lons = np.arange(np.ceil(min(lo, hlo) / step) * step, max(lo, hlo), step)
    lats = np.arange(np.ceil(min(la, hla) / step) * step, max(la, hla), step)
    midlat, midlon = (la + hla) / 2, (lo + hlo) / 2
    ax.set_xticks([to_m.transform(L, midlat)[0] for L in lons]); ax.set_xticklabels([f"{L:.1f}°E" for L in lons], fontsize=8)
    ax.set_yticks([to_m.transform(midlon, B)[1] for B in lats])
    ax.set_yticklabels([f"{B:.1f}°N" for B in lats], fontsize=8, rotation=90, va="center")
    ax.grid(True, color="#555", alpha=0.25, lw=0.5, ls="--")

    span = ext[1] - ext[0]
    u = max([v for v in (100, 200, 500, 1000, 2000, 5000, 10000, 20000) if v <= span * 0.28] or [100])
    lab_u = f"{u / 1000:g} km" if u >= 1000 else f"{u} m"
    sbx = ext[0] + span * 0.04; sby = ext[2] + (ext[3] - ext[2]) * 0.05; bh = (ext[3] - ext[2]) * 0.05
    ax.add_patch(Rectangle((sbx - span * 0.01, sby - bh * 0.35), u + span * 0.06, bh * 1.5, fc="white", ec="none", alpha=0.8, zorder=9))
    ax.plot([sbx, sbx + u], [sby, sby], color="k", lw=3, zorder=10, solid_capstyle="butt")
    ax.text(sbx, sby + bh * 0.3, "0", fontsize=7.5, ha="center", zorder=10)
    ax.text(sbx + u, sby + bh * 0.3, lab_u, fontsize=7.5, ha="center", zorder=10)
    nx = ext[1] - span * 0.05; ny = ext[3] - (ext[3] - ext[2]) * 0.14; al = span * 0.05
    ax.annotate("", xy=(nx, ny + al), xytext=(nx, ny), arrowprops=dict(arrowstyle="-|>", color="k", lw=2), zorder=10)
    ax.text(nx, ny + al * 1.15, "N", ha="center", fontsize=13, fontweight="bold", zorder=10)

    tot_valid = float(valid.sum()) * cs * cs / 1e6
    area = [float((cls == i).sum()) * cs * cs / 1e6 for i in range(n)]
    fig.text(0.04, 0.945, title, fontsize=21, fontweight="bold", color="#10202b")
    fig.text(0.04, 0.915, subtitle, fontsize=10.2, color="#44525c")
    if sub2: fig.text(0.04, 0.893, sub2, fontsize=9, color="#667782")

    lx = 0.685
    fig.text(lx, 0.86, legend_title, fontsize=12.5, fontweight="bold", color="#10202b")
    fig.text(lx, 0.84, legend_sub, fontsize=9, color="#667782")
    y = 0.795
    for x, t, kw in [(0.018, "Class", {}), (0.095, "Range", {}), (0.175, "Area km²", {"ha": "right"}), (0.225, "% of AOI", {"ha": "right"})]:
        fig.text(lx + x, y + 0.03, t, fontsize=8.5, fontweight="bold", **kw)
    for i in range(n - 1, -1, -1):
        fig.patches.append(Rectangle((lx, y - 0.0035), 0.012, 0.022, transform=fig.transFigure, fc=colors[i], ec="#333", lw=0.5))
        fig.text(lx + 0.018, y, class_names[i], fontsize=9.5, va="bottom"); fig.text(lx + 0.095, y, class_ranges[i], fontsize=9, va="bottom", color="#333")
        fig.text(lx + 0.175, y, f"{area[i]:.2f}", fontsize=9.5, va="bottom", ha="right", fontweight="bold")
        fig.text(lx + 0.225, y, f"{100 * area[i] / tot_valid:.1f}%" if tot_valid else "-", fontsize=9, va="bottom", ha="right")
        y -= 0.034
    if unclassed_label:
        un = tot_valid - sum(area)
        fig.patches.append(Rectangle((lx, y - 0.0035), 0.012, 0.022, transform=fig.transFigure, fc="#9aa3a8", ec="#333", lw=0.5))
        fig.text(lx + 0.018, y, unclassed_label, fontsize=9.5, va="bottom"); fig.text(lx + 0.095, y, unclassed_range, fontsize=9, va="bottom", color="#333")
        fig.text(lx + 0.175, y, f"{un:.2f}", fontsize=9.5, va="bottom", ha="right", fontweight="bold")
        fig.text(lx + 0.225, y, f"{100 * un / tot_valid:.1f}%" if tot_valid else "-", fontsize=9, va="bottom", ha="right")
        y -= 0.04
    else:
        y -= 0.01
    fig.add_artist(Line2D([lx, lx + 0.235], [y + 0.02, y + 0.02], color="#aab", lw=0.8))
    fig.text(lx, y, "SYMBOLS", fontsize=10, fontweight="bold", color="#10202b"); y -= 0.03
    syms = [("Analysis boundary", dict(color="#111", lw=1.6))] + ([("Primary roads", dict(color="#2b2b2b", lw=0.9))] if show_roads else [])
    for lab, kw in syms:
        fig.add_artist(Line2D([lx, lx + 0.012], [y + 0.008] * 2, **kw)); fig.text(lx + 0.018, y, lab, fontsize=9, va="bottom"); y -= 0.026
    if hotspot_values is not None:
        fig.add_artist(Line2D([lx + 0.006], [y + 0.008], marker="o", ms=5, mfc="white", mec="k", ls="")); fig.text(lx + 0.018, y, "Peak depth label (m)", fontsize=9, va="bottom"); y -= 0.026
    if show_dams:
        fig.add_artist(Line2D([lx + 0.006], [y + 0.008], marker="s", ms=6, mfc="#1f78b4", mec="white", ls="")); fig.text(lx + 0.018, y, "Dam / reservoir", fontsize=9, va="bottom"); y -= 0.026
    y -= 0.014
    if key_figures:
        fig.add_artist(Line2D([lx, lx + 0.235], [y + 0.02, y + 0.02], color="#aab", lw=0.8))
        fig.text(lx, y, "KEY FIGURES", fontsize=10, fontweight="bold", color="#10202b"); y -= 0.03
        for k, v in key_figures:
            fig.text(lx, y, k, fontsize=9, va="bottom", color="#44525c"); fig.text(lx + 0.235, y, v, fontsize=9, va="bottom", ha="right", fontweight="bold"); y -= 0.026
        y -= 0.012
    if notes:
        fig.text(lx, y, "\n".join(textwrap.wrap(notes, 62)), fontsize=7.6, va="top", color="#667782")
    fig.text(0.04, 0.02, footer or "SAILAB • Simulation & Analytics for Inundation, Loss, Assets & Built Environment", fontsize=8, color="#667782")
    fig.savefig(out_png, dpi=150, facecolor="white"); plt.close(fig)
    return {"areas_km2": area, "total_km2": tot_valid}


def _read_asc(p):
    with open(p) as f:
        h = {}
        for _ in range(6):
            k, v = f.readline().split(); h[k.lower()] = float(v)
        a = np.loadtxt(f)
    return h, a


def render_run_susceptibility_map(run_dir, out_png):
    """Peak-flood-depth susceptibility map for a finished run (uses the app's ACC steep-terrain depth correction)."""
    import json
    meta = json.load(open(f"{run_dir}/run_meta.json")); summ = json.load(open(f"{run_dir}/summary.json"))
    h, dmax = _read_asc(f"{run_dir}/results/res.max"); _, dem = _read_asc(f"{run_dir}/dem_bare.ascii")
    nod = -9999; valid = dem != nod
    cf = float(summ.get("acc_depth_correction_factor", 1.0)) if summ.get("acc_depth_correction_applied") else 1.0
    dmax = np.where(dmax == nod, 0.0, dmax) / cf
    dmax = np.where(valid, dmax, np.nan)
    h["src_crs"] = meta["src_crs"]
    cls = classify_flood_depth(dmax)
    cs = h["cellsize"]
    fl = float(np.sum(cls >= 0)) * cs * cs / 1e6; tot = float(valid.sum()) * cs * cs / 1e6
    bs = (summ.get("building_submersion") or {}).get("band_breakdown", {}) or {}
    loss = summ.get("estimated_building_loss") or {}
    rain, dur = meta["rain_mm_hr"], meta["duration_hr"]
    kf = [("Flooded area", f"{fl:.2f} km² ({100 * fl / tot:.1f}% of AOI)"),
          ("Peak depth", f"{np.nanmax(dmax):.2f} m"),
          ("Mean depth where flooded", f"{np.nanmean(dmax[dmax >= 0.05]):.2f} m" if fl else "-"),
          ("Peak hazard (DEFRA)", str(summ.get("peak_hazard_band", "-")))]
    if summ.get("buildings_used"): kf.append(("Buildings in model", f"{int(summ['buildings_used']):,}"))
    if bs: kf.append(("Buildings flooded", f"{sum(v for k, v in bs.items() if k != 'dry'):,}"))
    if loss.get("total_loss_pkr"): kf.append(("Est. building loss", f"Rs {loss['total_loss_pkr'] / 1e9:.2f} bn"))
    kf.append(("Mass balance error", f"{summ.get('final_mass_error_m3', 0):.1e} m³"))
    solver = summ.get("solver_used", "ACC")
    notes = (f"Notes: {solver} solver" + (f" with the app's calibrated steep-terrain depth correction (÷ {cf:.2f}); depths on steep ground are approximate. " if cf != 1.0 else ". ") +
             "Rainfall is a design storm, not a forecast. Buildings burned in as obstacles. Coordinates: WGS84 graticule over " + str(meta["src_crs"]) + ".")
    render_class_map(out_png, "Flood susceptibility map",
                     f"Physics simulation (LISFLOOD-FP, {solver}) • {rain:.0f} mm/h peak, {dur:.0f} h triangular storm, {rain * dur / 2:.0f} mm total • {cs:.0f} m grid",
                     h, meta["geometry"], dem, valid, cls, FLOOD_NAMES, FLOOD_RANGES, FLOOD_COLORS,
                     key_figures=kf, notes=notes, hotspot_values=dmax,
                     sub2="Classes are the maximum flood depth reached in each cell over the whole event. Labelled points are the deepest local maxima.")
    return out_png
