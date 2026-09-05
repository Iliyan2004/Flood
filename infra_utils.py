"""
Tab 4 utilities: real OpenStreetMap infrastructure/tree data for an AOI, and
risk classification by overlaying flood depth/hazard on each feature's own
footprint (per CLAUDE.md's settled definition of "risk %" -- not an invented
score).
"""
import time

import numpy as np

from overpass_utils import query_overpass

# DEFRA-style hazard bands, reused from flood_model.py for consistency.
RISK_BANDS = [("low", 0.75), ("moderate", 1.25), ("significant", 2.00), ("extreme", float("inf"))]


def fetch_osm_infrastructure(lon_min, lat_min, lon_max, lat_max, timeout=40):
    """Real OSM data for roads, critical infrastructure (hospitals/schools/
    clinics), power infrastructure, and trees/forest, all within the AOI.
    Retry/reliability logic lives in overpass_utils.query_overpass, shared
    with every other real OSM fetch in this app."""
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f"""
    [out:json][timeout:{timeout}];
    (
      way["highway"]({bbox});
      node["amenity"~"^(hospital|school|clinic)$"]({bbox});
      way["amenity"~"^(hospital|school|clinic)$"]({bbox});
      node["power"="tower"]({bbox});
      way["power"="line"]({bbox});
      node["natural"="tree"]({bbox});
      way["natural"="wood"]({bbox});
      way["landuse"="forest"]({bbox});
    );
    out geom;
    """
    t0 = time.time()
    elements = query_overpass(query, timeout=timeout)
    print(f"[infra] Overpass returned {len(elements)} features in {time.time()-t0:.1f}s")

    roads, critical, power, trees = [], [], [], []
    for el in elements:
        tags = el.get("tags", {})
        if "geometry" in el:
            pts = [(g["lon"], g["lat"]) for g in el["geometry"]]
            centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        elif "lon" in el:
            pts = [(el["lon"], el["lat"])]
            centroid = pts[0]
        else:
            continue

        if tags.get("highway"):
            roads.append({"id": el["id"], "name": tags.get("name", "unnamed road"),
                          "highway": tags["highway"], "centroid": centroid, "geometry": pts})
        elif tags.get("amenity") in ("hospital", "school", "clinic"):
            critical.append({"id": el["id"], "name": tags.get("name", tags["amenity"]),
                             "type": tags["amenity"], "centroid": centroid})
        elif tags.get("power") in ("tower", "line"):
            power.append({"id": el["id"], "type": tags["power"], "centroid": centroid, "geometry": pts})
        elif tags.get("natural") == "tree" or tags.get("natural") == "wood" or tags.get("landuse") == "forest":
            trees.append({"id": el["id"], "centroid": centroid, "geometry": pts})

    print(f"[infra] parsed: {len(roads)} roads, {len(critical)} critical (hospital/school/clinic), "
          f"{len(power)} power features, {len(trees)} tree/forest features")
    return {"roads": roads, "critical": critical, "power": power, "trees": trees}


def fetch_osm_building_heights(lon_min, lat_min, lon_max, lat_max, timeout=30):
    """Real building height (or floor-count) tags from OSM, where mapped.

    Checked coverage directly against Overpass before building this: in a
    dense central-Lahore test bbox, only 4 of 215 buildings (~2%) carry a
    `height` or `building:levels` tag at all -- genuinely sparse for
    Pakistan, not a bug. This is used to supplement, never replace, the
    documented single-story height estimate used everywhere else: any
    building without a real OSM tag keeps that estimate, and every
    building that DOES have one uses its real mapped height instead."""
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f"""
    [out:json][timeout:{timeout}];
    (
      way["building"]["height"]({bbox});
      way["building"]["building:levels"]({bbox});
    );
    out geom;
    """
    elements = query_overpass(query, timeout=timeout)
    out = []
    for el in elements:
        tags = el.get("tags", {})
        if "geometry" not in el:
            continue
        height_m = None
        if "height" in tags:
            try:
                height_m = float("".join(c for c in tags["height"] if c.isdigit() or c == "."))
            except ValueError:
                pass
        if height_m is None and "building:levels" in tags:
            try:
                height_m = float(tags["building:levels"]) * 3.0  # ~3m/floor, a documented estimate, not a measurement
            except ValueError:
                pass
        if height_m and height_m > 0:
            pts = [(g["lon"], g["lat"]) for g in el["geometry"]]
            out.append({"id": el["id"], "height_m": height_m, "geometry": pts})
    print(f"[buildings] {len(out)} OSM-tagged real building heights found (of {len(elements)} candidates)")
    return out


def _sample_raster_at_lonlat(raster, header, lon, lat, to_src_crs_transform):
    x, y = to_src_crs_transform(lon, lat)
    col = int((x - header["xllcorner"]) / header["cellsize"])
    row = int(header["nrows"] - 1 - (y - header["yllcorner"]) / header["cellsize"])
    if 0 <= row < header["nrows"] and 0 <= col < header["ncols"]:
        return float(raster[row, col])
    return None


def classify_hazard_value(haz):
    # A negative/NODATA hazard reading (e.g. -9999) never satisfies any
    # band's "lo <= haz" test, so it used to fall through to the final
    # `return "extreme"` below -- silently painting a feature with no real
    # hazard data as the worst possible band (red) instead of excluding it.
    if haz is None or haz < 0:
        return "outside domain"
    lo = 0.0
    for name, hi in RISK_BANDS:
        if lo <= haz < hi:
            return name
        lo = hi
    return "extreme"


def assess_infrastructure_risk(features_dict, max_depth, max_haz, header, lonlat_to_src_crs, dry_thresh=0.05):
    """For every OSM feature, sample max depth + hazard at its centroid and
    classify. Returns per-category risk summaries (counts and percentages)."""
    results = {}
    for category, feats in features_dict.items():
        rows = []
        for f in feats:
            lon, lat = f["centroid"]
            depth = _sample_raster_at_lonlat(max_depth, header, lon, lat, lonlat_to_src_crs)
            haz = _sample_raster_at_lonlat(max_haz, header, lon, lat, lonlat_to_src_crs) if max_haz is not None else None
            flooded = depth is not None and depth > dry_thresh
            band = classify_hazard_value(haz) if flooded else ("not flooded" if depth is not None else "outside domain")
            rows.append({**{k: v for k, v in f.items() if k != "geometry"},
                        "depth_m": depth, "hazard_band": band, "flooded": flooded})

        total_in_domain = sum(1 for r in rows if r["depth_m"] is not None)
        flooded_count = sum(1 for r in rows if r["flooded"])
        band_counts = {}
        for r in rows:
            band_counts[r["hazard_band"]] = band_counts.get(r["hazard_band"], 0) + 1

        results[category] = {
            "total_features": len(rows),
            "in_domain": total_in_domain,
            "flooded_count": flooded_count,
            "flooded_pct_of_in_domain": (100.0 * flooded_count / total_in_domain) if total_in_domain else 0.0,
            "band_breakdown": band_counts,
            "features": rows,
        }
    return results


# Building damage severity now comes from the SAME published Huizinga et
# al. 2017 (JRC) depth-damage curve already used for Tab 5's land-use
# damage analysis (damage_utils.DEPTH_DAMAGE_CURVES_ASIA["Built-up"]), not
# a depth-as-fraction-of-building-height calculation. The fraction diluted
# real risk against an assumed 6m single-story height: a genuinely
# damaging 0.5-1m-deep flood only reached ~10-15% of that assumed height,
# so buildings visibly standing in floodwater always classified as
# "minor" -- which read as a green, safe-looking color for something that
# was, in reality, already taking ~30-45% structural damage per the same
# real curve. The Huizinga curve is calibrated in absolute depth (not
# relative to height) and matches how a flooded building actually looks.
SUBMERSION_BANDS = [("minor", 0.30), ("moderate", 0.55), ("major", 0.72), ("severe", float("inf"))]


def _huizinga_built_up_damage_fraction(depth_m):
    from damage_utils import DEPTH_DAMAGE_CURVES_ASIA
    curve = DEPTH_DAMAGE_CURVES_ASIA["Built-up"]
    depths, damage = curve["depths"], curve["damage"]
    if depth_m <= depths[0]:
        return damage[0]
    if depth_m >= depths[-1]:
        return damage[-1]
    for i in range(1, len(depths)):
        if depth_m <= depths[i]:
            t = (depth_m - depths[i - 1]) / (depths[i] - depths[i - 1])
            return damage[i - 1] + t * (damage[i] - damage[i - 1])
    return damage[-1]


def classify_submersion(damage_fraction):
    lo = 0.0
    for name, hi in SUBMERSION_BANDS:
        if lo <= damage_fraction < hi:
            return name
        lo = hi
    return "severe"


def assess_building_submersion_risk(footprints, max_depth, header, dry_thresh=0.05):
    """For each real building footprint (buildings_utils.get_building_
    footprints), samples the run's own max-depth raster at the footprint's
    centroid. Severity band comes from the real Huizinga Built-up
    depth-damage curve (absolute depth); `submersion_fraction` (depth vs.
    this building's own real-or-estimated height) is still reported
    alongside it as a genuine, separate stat -- just no longer what
    decides the color."""
    nrows, ncols = int(header["nrows"]), int(header["ncols"])
    results = []
    band_counts = {}
    for fp in footprints:
        poly_rc = fp["poly_rc"]
        cr = sum(p[0] for p in poly_rc) / len(poly_rc)
        cc = sum(p[1] for p in poly_rc) / len(poly_rc)
        row, col = int(round(cr)), int(round(cc))
        depth = None
        if 0 <= row < nrows and 0 <= col < ncols:
            d = max_depth[row, col]
            if d != -9999:
                depth = float(d)
        if depth is None or depth <= dry_thresh:
            band, fraction, damage_fraction = "dry", 0.0, 0.0
        else:
            fraction = min(1.0, depth / fp["height_m"])
            damage_fraction = _huizinga_built_up_damage_fraction(depth)
            band = classify_submersion(damage_fraction)
        band_counts[band] = band_counts.get(band, 0) + 1
        results.append({"poly_rc": poly_rc, "height_m": fp["height_m"],
                         "height_source": fp.get("height_source", "estimated"),
                         "depth_m": depth, "submersion_fraction": fraction,
                         "damage_fraction": damage_fraction, "band": band})
    return {"features": results, "band_breakdown": band_counts, "total": len(footprints)}
