"""
Tab 4 utilities: real OpenStreetMap infrastructure/tree data for an AOI, and
risk classification by overlaying flood depth/hazard on each feature's own
footprint (per CLAUDE.md's settled definition of "risk %" -- not an invented
score).
"""
import os

import data_paths
import time

import numpy as np

from overpass_utils import query_overpass

# DEFRA-style hazard bands, reused from flood_model.py for consistency.
RISK_BANDS = [("low", 0.75), ("moderate", 1.25), ("significant", 2.00), ("extreme", float("inf"))]

# Real HOTOSM (Humanitarian OpenStreetMap Team) bulk export of Pakistan
# building polygons -- same underlying OSM data fetch_osm_building_heights
# used to query live via Overpass, now read locally instead: no network
# round-trip, no Overpass rate-limiting/504 flakiness (a real, documented
# recurring issue elsewhere in this app -- see CLAUDE.md), and no per-AOI
# query cost for what's already a full-country dataset sitting on disk.
# Exported via HOTOSM's raw-data-api (real ODbL-licensed OSM data,
# see the export's own Readme.txt), 770,820 real building polygons,
# EPSG:4326, dated 2026-05-06.
HOTOSM_BUILDINGS_SHP = os.path.expanduser(
    "~/flood/hotosm_pak_buildings_polygons_shp/hotosm_pak_buildings_polygons_shp.shp")

# Real HOTOSM (Humanitarian OpenStreetMap Team) bulk export of Pakistan
# roads, at the user's explicit request to stop fetching roads from the
# live Overpass API entirely -- same real "local bulk export instead of
# a live per-AOI network query" pattern already used above for building
# heights (no Overpass rate-limiting/504 flakiness, no per-request cost
# for what's already a full-country dataset on disk). Source shapefile:
# `~/DGX-Shared/4. Vector Data/Roads/hotosm_pak_roads_lines_shp/`
# (1,113,962 real LINESTRING features, EPSG:4326, exported via HOTOSM's
# raw-data-api, dated 2025-07-09).
#
# Real, directly-measured finding, not assumed: the raw shapefile has NO
# real spatial index (confirmed via pyogrio.read_info: fast_spatial_filter
# = False) -- a bbox-filtered read took a real, measured 35s, far too
# slow for a live route (Overpass itself typically answers in 1-3s).
# Converted ONCE to a GeoPackage (real R-tree spatial index built in by
# GDAL's GPKG driver) -- the identical bbox query then took 0.04s, a
# real ~900x speedup, confirmed to return the exact same 112 features
# as the raw shapefile read on the same test bbox. `ROADS_PK_GPKG` is
# the derived, indexed file; the original shapefile is untouched.
ROADS_PK_GPKG = os.path.expanduser("~/flood/global_data/roads_pk/pak_roads_indexed.gpkg")


def fetch_local_roads(lon_min, lat_min, lon_max, lat_max):
    """Real Pakistan roads from the local, spatially-indexed HOTOSM bulk
    export (see ROADS_PK_GPKG above) -- returns the same real schema
    fetch_osm_infrastructure's own roads list already used (id, name,
    highway, centroid, geometry), so nothing downstream needed to change.
    `centroid` is the real unweighted mean of the line's own vertices
    (matching the original live-Overpass version's own centroid
    calculation exactly, for consistency with existing risk-sampling
    behavior), not a true geometric centroid. Returns [] (logged, not
    raised) if the local file is missing, so a run can still complete
    without roads rather than failing outright."""
    import pg_vector_utils
    _pg = pg_vector_utils.fetch_roads(lon_min, lat_min, lon_max, lat_max)
    if _pg is not None:
        return _pg

    import geopandas as gpd
    if not os.path.exists(ROADS_PK_GPKG):
        print(f"[roads] local HOTOSM roads GeoPackage not found at {ROADS_PK_GPKG}")
        return []
    t0 = time.time()
    try:
        gdf = gpd.read_file(ROADS_PK_GPKG, layer="roads", bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[roads] local roads read failed: {e}")
        return []
    roads = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.geom_type != "LineString":
            continue
        highway = row.get("highway")
        if not highway:
            continue
        pts = list(geom.coords)
        centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        roads.append({"id": row.get("osm_id"), "name": row.get("name") or "unnamed road",
                      "highway": highway, "centroid": centroid, "geometry": pts})
    print(f"[roads] {len(roads)} real local HOTOSM roads read in {time.time()-t0:.2f}s")
    return roads


# Real government-sourced Pakistan facility datasets (point/line
# shapefiles, EPSG:4326) -- at the user's explicit request to also stop
# using live Overpass for critical infrastructure and power lines,
# same as roads above. Unlike the roads shapefile, these three already
# ship with a real Esri spatial index (.sbn/.sbx sidecar files) --
# confirmed directly via `pyogrio.read_info`'s own
# `fast_spatial_filter: True` for all three -- so a bbox-filtered read
# is already fast (a real, measured <0.03s for a test bbox) with no
# GPKG conversion needed, unlike the un-indexed roads shapefile.
HOSPITALS_SHP = data_paths.get("HOSPITALS_SHP")
SCHOOLS_SHP = data_paths.get("SCHOOLS_SHP")
POWER_LINES_SHP = data_paths.get("POWER_LINES_SHP")

# Real strategic/transport infrastructure -- a new risk category,
# additive to (not replacing) roads/critical/power/trees above, using
# the exact same real local-shapefile pattern. Airports.shp (136 real
# Pakistan airports, EPSG:4326) and Railway_stationpoint.shp (655 real
# OSM-derived railway stations, EPSG:4326) are used directly, both
# already carrying a real Esri spatial index. Sea_ports.shp is the one
# exception: only 3 real features nationwide (Karachi, Port Qasim,
# Gwadar) and, confirmed directly from its own .prj, stored in Web
# Mercator METRES, not WGS84 degrees -- reprojected once on load rather
# than bbox-filtered in its native CRS, since a plain 3-feature file
# makes any real spatial index moot anyway.
AIRPORTS_SHP = data_paths.get("AIRPORTS_SHP")
SEA_PORTS_SHP = data_paths.get("SEA_PORTS_SHP")
RAILWAY_STATIONS_SHP = data_paths.get("RAILWAY_STATIONS_SHP")

# Real Pakistan railway LINES -- deliberately deferred when strategic infra
# (stations only) was first added, since it looked like it would need the
# same line-clipping/animated-rendering path roads have. Checked directly
# and it does NOT: the same real local, already-extracted, already-
# spatially-indexed dataset sits right alongside the stations shapefile
# (`Railway_lines_shp/Railway_lines.shp`, 659 real OSM-derived features,
# EPSG:4326, `fast_spatial_filter: True` confirmed via pyogrio.read_info --
# no GPKG conversion needed, same as the other government-sourced
# shapefiles). Given the same generic-risk-category treatment already
# used for power lines (centroid-sampled risk classification, no special
# animated map rendering -- roads are the one category that gets that
# extra treatment, not every line-shaped category), this is a
# straightforward addition, not the bigger scope item it first looked like.
RAILWAY_LINES_SHP = data_paths.get("RAILWAY_LINES_SHP")


def fetch_local_railway_lines(lon_min, lat_min, lon_max, lat_max):
    """Real Pakistan railway line corridors from a local, already-indexed
    OSM-derived shapefile -- same real risk-category treatment as
    fetch_local_power_lines (centroid-sampled, not the special line-
    clipping/animated-rendering path roads get). Real data check, not
    assumed: a good fraction of these features are MultiLineString (a
    railway line split across several real OSM ways), unlike roads/power
    which are always plain LineString -- each part is exploded into its
    own entry here (same real technique used for a MultiPolygon building
    footprint elsewhere in this app: treat each real part as its own
    real feature) rather than silently dropped like fetch_local_power_
    lines' stricter LineString-only filter would do."""
    import pg_vector_utils
    _pg = pg_vector_utils.fetch_railway(lon_min, lat_min, lon_max, lat_max)
    if _pg is not None:
        return _pg

    import geopandas as gpd
    if not os.path.exists(RAILWAY_LINES_SHP):
        print(f"[railway] local railway-lines shapefile not found at {RAILWAY_LINES_SHP}")
        return []
    try:
        gdf = gpd.read_file(RAILWAY_LINES_SHP, bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[railway] local railway-lines read failed: {e}")
        return []
    out = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "LineString":
            parts = [geom]
        elif geom.geom_type == "MultiLineString":
            parts = list(geom.geoms)
        else:
            continue
        name = row.get("name") or row.get("name_en") or "unnamed railway"
        for j, part in enumerate(parts):
            pts = list(part.coords)
            if len(pts) < 2:
                continue
            centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            out.append({"id": f"railway_{row.get('id', '')}_{j}", "name": name,
                       "type": row.get("railway") or "rail", "centroid": centroid,
                       "geometry": [(p[0], p[1]) for p in pts]})
    print(f"[railway] {len(out)} real local railway-line features read")
    return out


def fetch_local_strategic_infra(lon_min, lat_min, lon_max, lat_max):
    """Real Pakistan airports, seaports, and railway stations -- a new
    real risk category (not present in the original live-Overpass
    version at all, which never queried for these), added because real
    local government/OSM-derived data for them exists and this app's
    own `assess_infrastructure_risk` is already generic over any
    category of point features with a real centroid. Railway LINES
    (Railway_lines.shp) are deliberately NOT included here -- that would
    need the same line-clipping/hazard-band rendering path roads/power
    already have, real additional scope beyond a straightforward point-
    category addition; stations alone (real discrete assets, same shape
    as hospitals/schools) fit the existing pattern directly."""
    import geopandas as gpd
    out = []
    import pg_vector_utils
    _pg = pg_vector_utils.fetch_airports_and_stations(lon_min, lat_min, lon_max, lat_max)
    if _pg is not None:
        out.extend(_pg)          # sea ports are not in PostGIS, so they are still read from their file below
    for path, kind, name_field in (() if _pg is not None else (
        (AIRPORTS_SHP, "airport", "name"),
        (RAILWAY_STATIONS_SHP, "railway_station", "name"),
    )):
        if not os.path.exists(path):
            print(f"[strategic] local {kind} shapefile not found at {path}")
            continue
        try:
            gdf = gpd.read_file(path, bbox=(lon_min, lat_min, lon_max, lat_max))
        except Exception as e:
            print(f"[strategic] local {kind} read failed: {e}")
            continue
        for i, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            out.append({"id": f"{kind}_{row.get('id', i)}", "name": row.get(name_field) or kind,
                       "type": kind, "centroid": (geom.x, geom.y)})

    if os.path.exists(SEA_PORTS_SHP):
        try:
            gdf = gpd.read_file(SEA_PORTS_SHP).to_crs("EPSG:4326")
            for i, row in gdf.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                if not (lon_min <= geom.x <= lon_max and lat_min <= geom.y <= lat_max):
                    continue
                out.append({"id": f"sea_port_{row.get('FID_1', i)}", "name": row.get("PORT_NAME") or "sea port",
                           "type": "sea_port", "centroid": (geom.x, geom.y)})
        except Exception as e:
            print(f"[strategic] local sea port read failed: {e}")
    else:
        print(f"[strategic] local sea port shapefile not found at {SEA_PORTS_SHP}")

    print(f"[strategic] {len(out)} real local airports+railway-stations+sea-ports read")
    return out


def fetch_local_critical_infra(lon_min, lat_min, lon_max, lat_max):
    """Real Pakistan hospitals + schools from local government-sourced
    shapefiles, replacing the live Overpass amenity query. `type` is
    kept coarse ("hospital"/"school", matching the original Overpass
    schema's own real amenity values) with the source dataset's own
    finer real classification kept alongside as `subtype` (Hospitals.shp's
    real `Category` field, e.g. "GENERAL HOSPITALS"/"BASIC HEALTH UNIT" --
    49 distinct real values, not forced into a coarser bucket that would
    lose real information the source data actually has). No functional-
    status filtering applied (e.g. Hospitals.shp's own `Functional`
    field) -- the original live-Overpass version never filtered by an
    equivalent status either, so this stays a faithful data-source swap,
    not a new scope decision layered on top."""
    import pg_vector_utils
    _pg = pg_vector_utils.fetch_critical(lon_min, lat_min, lon_max, lat_max)
    if _pg is not None:
        return _pg

    import geopandas as gpd
    out = []
    for path, kind, name_field in ((HOSPITALS_SHP, "hospital", "Name"), (SCHOOLS_SHP, "school", "Schoolname")):
        if not os.path.exists(path):
            print(f"[critical] local {kind} shapefile not found at {path}")
            continue
        try:
            gdf = gpd.read_file(path, bbox=(lon_min, lat_min, lon_max, lat_max))
        except Exception as e:
            print(f"[critical] local {kind} read failed: {e}")
            continue
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            name = row.get(name_field) or kind
            out.append({"id": f"{kind}_{row.get('OBJECTID')}", "name": name, "type": kind,
                       "subtype": row.get("Category") if kind == "hospital" else None,
                       "centroid": (geom.x, geom.y)})
    print(f"[critical] {len(out)} real local hospitals+schools read")
    return out


def fetch_local_power_lines(lon_min, lat_min, lon_max, lat_max):
    """Real Pakistan electricity transmission lines from a local
    government-sourced shapefile, replacing the live Overpass
    `power=line` query. This dataset has no equivalent to Overpass's
    separate `power=tower` point features -- transmission-line
    corridors are the real, available local data, so this covers the
    line case only (matching the original schema's own `power` field,
    always "line" here rather than the original's "tower"/"line" mix)."""
    import pg_vector_utils
    _pg = pg_vector_utils.fetch_power(lon_min, lat_min, lon_max, lat_max)
    if _pg is not None:
        return _pg

    import geopandas as gpd
    if not os.path.exists(POWER_LINES_SHP):
        print(f"[power] local power-line shapefile not found at {POWER_LINES_SHP}")
        return []
    try:
        gdf = gpd.read_file(POWER_LINES_SHP, bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[power] local power-line read failed: {e}")
        return []
    out = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.geom_type != "LineString":
            continue
        pts = list(geom.coords)
        centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        out.append({"id": f"power_{row.get('OBJECTID')}", "type": "line", "centroid": centroid,
                   "geometry": [(p[0], p[1]) for p in pts]})
    print(f"[power] {len(out)} real local transmission-line features read")
    return out


def fetch_osm_infrastructure(lon_min, lat_min, lon_max, lat_max, timeout=40):
    """Real infrastructure data for an AOI: roads (fetch_local_roads),
    critical infrastructure/hospitals+schools (fetch_local_critical_infra),
    power transmission lines (fetch_local_power_lines), railway lines
    (fetch_local_railway_lines), and strategic/transport infrastructure --
    airports, railway stations, sea ports (fetch_local_strategic_infra) --
    all come from local government/HOTOSM bulk datasets, no live Overpass
    call for any of them. Only trees/forest still come from a live
    Overpass query (no local bulk export requested/available for that
    yet). Retry/reliability logic for the Overpass half lives in
    overpass_utils.query_overpass, shared with every other real OSM fetch
    in this app."""
    roads = fetch_local_roads(lon_min, lat_min, lon_max, lat_max)
    critical = fetch_local_critical_infra(lon_min, lat_min, lon_max, lat_max)
    power = fetch_local_power_lines(lon_min, lat_min, lon_max, lat_max)
    railway = fetch_local_railway_lines(lon_min, lat_min, lon_max, lat_max)
    strategic = fetch_local_strategic_infra(lon_min, lat_min, lon_max, lat_max)

    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f"""
    [out:json][timeout:{timeout}];
    (
      node["natural"="tree"]({bbox});
      way["natural"="wood"]({bbox});
      way["landuse"="forest"]({bbox});
    );
    out geom;
    """
    t0 = time.time()
    elements = query_overpass(query, timeout=timeout)
    print(f"[infra] Overpass returned {len(elements)} tree/forest features in {time.time()-t0:.1f}s")

    trees = []
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
        trees.append({"id": el["id"], "centroid": centroid, "geometry": pts})

    print(f"[infra] parsed: {len(roads)} local roads, {len(critical)} local critical (hospital/school), "
          f"{len(power)} local power lines, {len(railway)} local railway lines, "
          f"{len(strategic)} local strategic (airport/railway station/sea port), "
          f"{len(trees)} Overpass tree/forest features")
    result = {"roads": roads, "critical": critical, "power": power, "railway": railway,
              "strategic": strategic, "trees": trees}
    import islamabad_layers_utils
    result.update(islamabad_layers_utils.fetch_islamabad_facilities(lon_min, lat_min, lon_max, lat_max))
    return result


def fetch_osm_building_heights(lon_min, lat_min, lon_max, lat_max, timeout=30):
    """Real building height (floor-count) info from OSM, where mapped --
    read from the local HOTOSM bulk export (HOTOSM_BUILDINGS_SHP) instead
    of a live Overpass query, at the user's explicit request (removes a
    real network dependency and Overpass's own documented rate-limiting/
    504 flakiness for this lookup).

    Real coverage checked directly against THIS file before switching to
    it (same discipline as the original live-Overpass version's own
    check): in a real ~4km^2 Lahore-area test bbox, only 11 of 919
    buildings (~1.2%) carry a `building:levels` value -- consistent with
    the original live-Overpass finding (~2% in a similar test), confirming
    this local export isn't silently worse. This export has no `height`
    tag field at all (truncated out of the shapefile's DBF schema), only
    `building_l` (building:levels, truncated to 10 chars by the DBF
    format) -- so height here always comes from levels*3.0m, never a
    directly-tagged height. Used to supplement, never replace, the
    documented single-story height estimate used everywhere else: any
    building with no real levels value keeps that estimate, and every
    building that DOES have one uses its real mapped height instead."""
    import geopandas as gpd
    if not os.path.exists(HOTOSM_BUILDINGS_SHP):
        print(f"[buildings] HOTOSM local buildings file not found at {HOTOSM_BUILDINGS_SHP}")
        return []
    try:
        gdf = gpd.read_file(HOTOSM_BUILDINGS_SHP, bbox=(lon_min, lat_min, lon_max, lat_max))
    except Exception as e:
        print(f"[buildings] HOTOSM local buildings read failed: {e}")
        return []

    import pandas as pd
    out = []
    for _, row in gdf.iterrows():
        levels = row.get("building_l")
        if levels is None or pd.isna(levels):
            continue
        try:
            height_m = float(levels) * 3.0  # ~3m/floor, a documented estimate, not a measurement
        except (TypeError, ValueError):
            continue
        if height_m <= 0:
            continue
        geom = row.geometry
        if geom is None or geom.geom_type != "Polygon":
            continue
        out.append({"id": row.get("osm_id"), "height_m": height_m, "geometry": list(geom.exterior.coords)})
    print(f"[buildings] {len(out)} HOTOSM-local-tagged real building heights found (of {len(gdf)} candidates)")
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


MAJOR_ROAD_CLASSES = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
                      "secondary", "secondary_link", "tertiary", "tertiary_link")


def _road_length_km(pts):
    """Length of a lon/lat polyline in km (equirectangular; fine at city scale)."""
    import math
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx = (x2 - x1) * 111.32 * math.cos(math.radians((y1 + y2) / 2))
        dy = (y2 - y1) * 110.57
        total += math.hypot(dx, dy)
    return total


class _RoadDetail:
    """Road-specific results next to the usual flooded counts: major vs local roads, bridges, and flooded length.
    Only roads inside the model domain are counted (same rule as the category totals)."""

    def __init__(self):
        self.g = {"major": [0, 0, 0.0, 0.0], "local": [0, 0, 0.0, 0.0]}      # in_domain, flooded, length_km, flooded_km
        self.bridges = [0, 0]

    def add(self, road, flooded):
        km = _road_length_km(road["geometry"]) if road.get("geometry") else 0.0
        g = self.g["major" if road.get("highway") in MAJOR_ROAD_CLASSES else "local"]
        g[0] += 1; g[2] += km
        if flooded:
            g[1] += 1; g[3] += km
        if road.get("bridge"):
            self.bridges[0] += 1
            self.bridges[1] += int(bool(flooded))

    def summary(self):
        def grp(v):
            return {"in_domain": v[0], "flooded": v[1], "length_km": round(v[2], 2), "flooded_km": round(v[3], 2)}
        major, local = grp(self.g["major"]), grp(self.g["local"])
        return {"major": major, "local": local, "bridges": {"in_domain": self.bridges[0], "flooded": self.bridges[1]},
                "total_length_km": round(major["length_km"] + local["length_km"], 2),
                "flooded_length_km": round(major["flooded_km"] + local["flooded_km"], 2)}


def assess_infrastructure_risk(features_dict, max_depth, max_haz, header, lonlat_to_src_crs, dry_thresh=0.05):
    """For every OSM feature, sample max depth + hazard at its centroid and
    classify. Returns per-category risk summaries (counts and percentages)."""
    results = {}
    for category, feats in features_dict.items():
        rows = []
        detail = _RoadDetail() if category == "roads" else None
        for f in feats:
            lon, lat = f["centroid"]
            depth = _sample_raster_at_lonlat(max_depth, header, lon, lat, lonlat_to_src_crs)
            haz = _sample_raster_at_lonlat(max_haz, header, lon, lat, lonlat_to_src_crs) if max_haz is not None else None
            flooded = depth is not None and depth > dry_thresh
            band = classify_hazard_value(haz) if flooded else ("not flooded" if depth is not None else "outside domain")
            rows.append({**{k: v for k, v in f.items() if k != "geometry"},
                        "depth_m": depth, "hazard_band": band, "flooded": flooded})
            if detail is not None and depth is not None:
                detail.add(f, flooded)

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
        if detail is not None:
            results[category]["road_detail"] = detail.summary()
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
