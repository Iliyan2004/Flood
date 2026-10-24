"""
Glacier surface velocity from NASA ITS_LIVE image-pair velocities (Sentinel-2 / Landsat feature tracking, public
STAC search and AWS granules, no login) -- a replacement for the Earth Engine Sentinel-1 offset-tracking
monitoring while its credentials are unavailable, and an independent check when they are not.

What it does for an AOI: finds recent pairs and the same-season pairs of the previous years, downloads the
granules (about 2 MB each, cached), computes speed statistics over the PMD glacier polygons inside the AOI, and
compares the recent median with the same-season baseline median. A ratio of 1.5 or more is flagged as a marked
speed-up and 1.25 to 1.5 as a possible one. These thresholds are screening values chosen for this app, not
validated against surge or GLOF precursors.

Real limits: pair coverage is cloud- and snow-limited (granules with under 30 % valid pixels are skipped), each pair
carries its own error, 10-16 day pairs are noisy over slow ice, and a speed-up is a possible warning sign, not
proof that an outburst is coming.
"""
import datetime as dt
import hashlib
import json
import os
import time

import numpy as np
import requests

import data_paths

STAC_URL = "https://stac.itslive.cloud"
COLLECTION = "itslive-granules"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "itslive_cache")
GRANULE_DIR = os.path.join(CACHE_DIR, "granules")
THUMB_DIR = os.path.join(CACHE_DIR, "thumbs")
MIN_VALID_PERCENT = 30
MIN_GLACIER_COVERAGE = 0.10  # a pair is only used if at least this fraction of the glacier cells has a valid velocity
MIN_PAIR_DAYS, MAX_PAIR_DAYS = 5, 40
MAX_GRANULE_MB = 60
SPEEDUP_MARKED, SPEEDUP_POSSIBLE = 1.5, 1.25
for _d in (GRANULE_DIR, THUMB_DIR):
    os.makedirs(_d, exist_ok=True)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def search_granules(bbox, start, end, max_items=80):
    """STAC search (POST /search), following 'next' links. Returns feature dicts."""
    body = {"collections": [COLLECTION], "bbox": list(bbox), "datetime": f"{_iso(start)}/{_iso(end)}", "limit": min(100, max_items)}
    feats, url = [], STAC_URL + "/search"
    while url and len(feats) < max_items:
        j = None
        for attempt in range(3):  # the STAC endpoint occasionally times out; retry before giving up
            try:
                r = requests.post(url, json=body, timeout=(20, 60))
                r.raise_for_status()
                j = r.json()
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        feats += j.get("features", [])
        nxt = next((l for l in j.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt.get("href")
        if nxt.get("body"):
            body = nxt["body"] if nxt.get("merge") is not True else {**body, **nxt["body"]}
    return feats[:max_items]


def _usable(f):
    p = f["properties"]
    valid = p.get("percent_valid_pixels")
    dtd = p.get("date_dt")
    if valid is not None and valid < MIN_VALID_PERCENT:
        return False
    if dtd is not None and not (MIN_PAIR_DAYS <= float(dtd) <= MAX_PAIR_DAYS):
        return False
    return "data" in f.get("assets", {})


def download_granule(feature):
    href = feature["assets"]["data"]["href"]
    path = os.path.join(GRANULE_DIR, hashlib.sha1(href.encode()).hexdigest() + ".nc")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    tmp = path + ".part"
    with requests.get(href, stream=True, timeout=120) as r:
        r.raise_for_status()
        size = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                size += len(chunk)
                if size > MAX_GRANULE_MB * 1e6:
                    raise RuntimeError("granule larger than the size cap")
                fh.write(chunk)
    os.replace(tmp, path)
    return path


_GLACIER_CACHE = {}


def glacier_geometry_wgs84(aoi_geom):
    """Union of PMD glacier polygons inside the AOI (WGS84 shapely geometry), or None."""
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import unary_union
    key = hashlib.sha1(json.dumps(aoi_geom, sort_keys=True).encode()).hexdigest()
    if key in _GLACIER_CACHE:
        return _GLACIER_CACHE[key]
    shp = data_paths.get("PMD_GLACIER_SHP")
    if not os.path.exists(shp):
        return None
    aoi = shape(aoi_geom)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    x0, y0 = to_utm.transform(aoi.bounds[0], aoi.bounds[1]); x1, y1 = to_utm.transform(aoi.bounds[2], aoi.bounds[3])
    g = gpd.read_file(shp, bbox=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    if g.empty:
        _GLACIER_CACHE[key] = None
        return None
    g = g.to_crs(4326)
    u = unary_union(list(g.geometry.buffer(0))).intersection(aoi)
    res = None if u.is_empty else u
    _GLACIER_CACHE[key] = res
    return res


def granule_stats(path, glacier_wgs84, aoi_bounds_wgs84, want_grid=False):
    """Speed statistics (m/yr) over the glacier polygons in one granule, restricted to the AOI window."""
    import xarray as xr
    from affine import Affine
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from shapely.ops import transform as shp_transform
    ds = xr.open_dataset(path)
    try:
        epsg = int(ds["mapping"].attrs.get("spatial_epsg", 0) or ds.attrs.get("spatial_epsg", 0) or 0)
    except Exception:
        epsg = 0
    if not epsg:
        zone = int(ds["mapping"].attrs.get("utm_zone_number", 43)); epsg = 32600 + zone
    x, y = ds["x"].values, ds["y"].values
    res = abs(float(x[1] - x[0]))
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    bx0, by0 = tr.transform(aoi_bounds_wgs84[0], aoi_bounds_wgs84[1]); bx1, by1 = tr.transform(aoi_bounds_wgs84[2], aoi_bounds_wgs84[3])
    xmin, xmax, ymin, ymax = min(bx0, bx1), max(bx0, bx1), min(by0, by1), max(by0, by1)
    cols = np.where((x >= xmin - res) & (x <= xmax + res))[0]; rows = np.where((y >= ymin - res) & (y <= ymax + res))[0]
    if len(cols) < 2 or len(rows) < 2:
        return None
    c0, c1, r0, r1 = cols.min(), cols.max() + 1, rows.min(), rows.max() + 1
    def _win(name):
        a = np.squeeze(ds[name].isel(y=slice(r0, r1), x=slice(c0, c1)).values).astype("float64")  # some granule versions carry a length-1 time axis
        return a
    v = _win("v"); ve = _win("v_error") if "v_error" in ds else None
    if v.ndim != 2:
        return None
    xs, ys = x[c0:c1], y[r0:r1]
    transform = Affine(res * (1 if x[1] > x[0] else -1), 0, xs[0] - res / 2 * (1 if x[1] > x[0] else -1),
                       0, (ys[1] - ys[0]), ys[0] - (ys[1] - ys[0]) / 2)
    g_utm = shp_transform(Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform, glacier_wgs84)
    mask = ~geometry_mask([g_utm], out_shape=v.shape, transform=transform, invert=False)
    n_mask = int(mask.sum())
    if n_mask == 0:
        return None
    ok = mask & np.isfinite(v)
    vals = v[ok]
    out = {"epsg": epsg, "cell_m": res, "n_glacier_cells": n_mask, "n_valid": int(ok.sum()),
           "valid_frac": float(ok.sum() / n_mask)}
    if ok.sum() >= 20:
        out.update(mean=float(vals.mean()), median=float(np.median(vals)), p95=float(np.percentile(vals, 95)),
                   mean_error=float(np.nanmean(ve[ok])) if ve is not None else None)
    if want_grid:
        out["grid"] = {"v": v, "mask": mask, "extent": [xs[0] - res / 2, xs[-1] + res / 2, ys[-1] - abs(ys[1] - ys[0]) / 2, ys[0] + abs(ys[1] - ys[0]) / 2],
                       "epsg": epsg, "descending_y": bool(ys[1] < ys[0])}
    ds.close()
    return out


def _thumbnail(grid_info, glacier_wgs84, title, out_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform
    g = grid_info["grid"]
    v = np.where(g["mask"], g["v"], np.nan)
    vmax = np.nanpercentile(v, 98) if np.isfinite(v).any() else 1.0
    fig, ax = plt.subplots(figsize=(6.4, 5.4), dpi=120)
    ax.set_facecolor("#dfe5e8")
    im = ax.imshow(v, extent=g["extent"], origin="upper" if g["descending_y"] else "lower", cmap="inferno", vmin=0, vmax=max(vmax, 1.0))
    gu = shp_transform(Transformer.from_crs("EPSG:4326", f"EPSG:{g['epsg']}", always_xy=True).transform, glacier_wgs84)
    for p in (list(gu.geoms) if gu.geom_type == "MultiPolygon" else [gu]):
        xs, ys = p.exterior.xy; ax.plot(xs, ys, color="white", lw=0.6, alpha=0.8)
    ax.set_xlim(g["extent"][0], g["extent"][1]); ax.set_ylim(g["extent"][2], g["extent"][3]); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([]); ax.set_title(title, fontsize=9)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02); cb.set_label("surface speed (m/yr)", fontsize=8)
    fig.tight_layout(); fig.savefig(out_png); plt.close(fig)


def _shift_year(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def _pair_row(f, st):
    p = f["properties"]
    return {"date": (p.get("mid_datetime") or p.get("datetime") or "")[:10], "pair_days": p.get("date_dt"), "platform": p.get("platform"),
            "percent_valid_scene": p.get("percent_valid_pixels"), **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in st.items() if k != "grid"}}


def analyse_aoi(geometry, end_date=None, recent_days=45, baseline_years=2, max_per_window=10, window_half_days=None):
    """Recent vs same-season ITS_LIVE glacier speed for an AOI. Returns a JSON-able dict (raises on hard failures)."""
    from shapely.geometry import shape
    aoi = shape(geometry)
    glac = glacier_geometry_wgs84(geometry)
    if glac is None:
        return {"error": "No PMD glacier polygons inside this area -- draw the AOI around the glacier itself."}
    bbox = aoi.bounds
    end = end_date or dt.datetime.utcnow()
    if isinstance(end, str):
        end = dt.datetime.fromisoformat(end)
    start = end - dt.timedelta(days=recent_days)
    windows = [("recent", start, end)]
    for k in range(1, baseline_years + 1):
        windows.append((f"baseline-{k}y", _shift_year(start, k), _shift_year(end, k)))
    rows, recent_best, errors = [], None, []
    for label, w0, w1 in windows:
        try:
            feats = [f for f in search_granules(bbox, w0, w1, max_items=100) if _usable(f)]
        except Exception as e:
            errors.append(f"{label}: search failed ({e})"); continue
        feats.sort(key=lambda f: -(f["properties"].get("percent_valid_pixels") or 0))
        got = attempts = 0
        for f in feats:
            if got >= max_per_window or attempts >= 2 * max_per_window:
                break
            attempts += 1
            try:
                path = download_granule(f)
                st = granule_stats(path, glac, bbox, want_grid=(label == "recent"))
            except Exception as e:
                errors.append(f"{label}: {str(e)[:80]}"); continue
            if st is None or "median" not in st:
                continue
            if st["valid_frac"] < MIN_GLACIER_COVERAGE:
                errors.append(f"{label}: {f['properties'].get('platform')} pair {(f['properties'].get('mid_datetime') or '')[:10]} skipped (only {st['valid_frac']:.0%} of the glacier tracked)")
                continue
            row = _pair_row(f, st); row["window"] = label; rows.append(row); got += 1
            if label == "recent" and "grid" in st:
                if recent_best is None or row["date"] > recent_best[0]["date"]:
                    recent_best = (row, st)
    rec = [r["median"] for r in rows if r["window"] == "recent"]
    base = [r["median"] for r in rows if r["window"] != "recent"]
    result = {"pairs": sorted(rows, key=lambda r: r["date"]), "search_bbox": list(bbox), "recent_window": [_iso(start), _iso(end)],
              "n_recent": len(rec), "n_baseline": len(base), "errors": list(dict.fromkeys(errors))[:6],
              "glacier_area_km2": None, "source": "NASA ITS_LIVE image-pair velocities (public STAC + AWS)"}
    try:
        import geopandas as gpd
        result["glacier_area_km2"] = round(float(gpd.GeoSeries([glac], crs=4326).to_crs(32643).area.iloc[0]) / 1e6, 2)
    except Exception:
        pass
    if rec:
        result["recent_median_m_yr"] = round(float(np.median(rec)), 1)
    if base:
        result["baseline_median_m_yr"] = round(float(np.median(base)), 1)
    if rec and base and np.median(base) > 0:
        ratio = float(np.median(rec) / np.median(base))
        result["speedup_ratio"] = round(ratio, 2)
        result["flag"] = "marked speed-up" if ratio >= SPEEDUP_MARKED else ("possible speed-up" if ratio >= SPEEDUP_POSSIBLE else "no speed-up")
        cautions = []
        if min(len(rec), len(base)) < 3:
            cautions.append("fewer than 3 pairs in the recent or baseline set")
        rp = {r["platform"] for r in rows if r["window"] == "recent"}; bp = {r["platform"] for r in rows if r["window"] != "recent"}
        if rp and bp and not (rp & bp):
            cautions.append(f"recent pairs come from {', '.join(sorted(rp))} but the baseline from {', '.join(sorted(bp))} -- different sensors have different noise and tracking coverage")
        rv = np.median([r["valid_frac"] for r in rows if r["window"] == "recent"]); bv = np.median([r["valid_frac"] for r in rows if r["window"] != "recent"])
        if max(rv, bv) > 3 * min(rv, bv):
            cautions.append(f"the share of glacier tracked differs a lot (recent {rv:.0%}, baseline {bv:.0%}) so the medians cover different parts of the ice")
        if cautions:
            result["flag_caution"] = "; ".join(cautions) + " -- treat the flag as uncertain"
        if min(len(rec), len(base)) < 3:
            result["flag_raw"] = result["flag"]
            result["flag"] = "inconclusive (too few pairs)"
    elif not rec:
        result["flag"] = "no recent usable pairs"
    else:
        result["flag"] = "no baseline pairs to compare with"
    result["method"] = (f"ITS_LIVE pairs of 5-40 days with at least {MIN_VALID_PERCENT}% valid pixels; speed statistics over the PMD glacier "
                        f"polygons inside the AOI; recent window ({recent_days} days to {end:%Y-%m-%d}) compared with the same calendar window "
                        f"of the previous {baseline_years} years so the seasonal cycle cancels. Thresholds {SPEEDUP_POSSIBLE} / {SPEEDUP_MARKED} are "
                        "screening values, not validated against surge or outburst precursors.")
    if recent_best:
        row, st = recent_best
        name = hashlib.sha1((json.dumps(geometry, sort_keys=True) + row["date"]).encode()).hexdigest()[:16] + ".png"
        try:
            _thumbnail(st, glac, f"{row['platform']} pair {row['date']} ({row['pair_days']:.0f} d) -- median {row['median']:.0f} m/yr", os.path.join(THUMB_DIR, name))
            result["thumbnail"] = name
        except Exception as e:
            result["errors"].append(f"thumbnail failed: {str(e)[:80]}")
    return result
