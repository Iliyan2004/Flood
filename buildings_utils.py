"""
Generalized (not Bahawalpur-specific) Google Open Buildings fetch + DEM
burning, for any AOI in Pakistan. Same approach proven in pak_flood_tool.py,
parameterized for reuse across the Flood Information System.
"""
import math
import os
import time

import duckdb
import numpy as np

GOB_URL = "https://data.source.coop/cholmes/google-open-buildings/geoparquet-by-country/country_iso=PK/PK_{quadkey}.parquet"
ASSUMED_BUILDING_HEIGHT_M = 6.0  # v3 footprint dataset has no height field (documented simplification)

# Real, one-time local cache of Google Open Buildings' own per-quadkey
# Pakistan geoparquet files (16 zoom-6 quadkeys cover Pakistan's real
# bbox; 7 of them are non-empty, ~5.85GB total -- confirmed via direct
# HTTP HEAD requests before downloading, same "check real size before
# committing" discipline used for every other bulk fetch in this app).
# Fetched by fetch_gob_pakistan.sh into this directory. This is the SAME
# real Google Open Buildings data `fetch_buildings_for_aoi` already used
# via a live per-AOI remote DuckDB httpfs query -- real, measured cost
# 74.2s for one small test AOI, entirely network/remote-parquet-scan
# time -- just pre-fetched once so every subsequent AOI lookup is a
# local read instead of a live network round trip. Coverage/accuracy is
# identical either way; only the fetch mechanism changed.
GOB_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "buildings_pk")


def lonlat_quadkey(lat, lon, zoom=6):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    quadkey = ""
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if xtile & mask:
            digit += 1
        if ytile & mask:
            digit += 2
        quadkey += str(digit)
    return quadkey


def fetch_buildings_for_aoi(lon_min, lat_min, lon_max, lat_max, cache_path):
    if os.path.exists(cache_path):
        return cache_path
    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2
    quadkey = lonlat_quadkey(lat_c, lon_c)

    local_path = os.path.join(GOB_LOCAL_DIR, f"PK_{quadkey}.parquet")
    if os.path.exists(local_path):
        source, source_desc = local_path, "local pre-fetched cache"
    else:
        # Real, honest fallback -- an AOI whose centroid lands in one of
        # the 9 real zoom-6 Pakistan-bbox quadkeys that turned out to
        # have zero actual PK buildings (mostly Afghanistan/Iran/India
        # border/ocean area, confirmed via direct HTTP HEAD checks before
        # this local cache was built), or any quadkey fetched after this
        # cache was last refreshed. Falls back to the exact same live
        # remote query this function always used before the local cache
        # existed -- never a silent gap, just slower for this one case.
        source, source_desc = GOB_URL.format(quadkey=quadkey), "live remote fetch (no local cache for this quadkey)"

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    t0 = time.time()
    q = f"""
    COPY (
      SELECT area_in_meters, confidence, ST_AsText(geometry) as wkt
      FROM read_parquet('{source}')
      WHERE ST_Intersects(geometry, ST_MakeEnvelope({lon_min}, {lat_min}, {lon_max}, {lat_max}))
    ) TO '{cache_path}' (FORMAT PARQUET)
    """
    try:
        con.execute(q)
    except Exception as e:
        print(f"[buildings] fetch failed for quadkey {quadkey} ({source_desc}): {e}")
        return None
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{cache_path}')").fetchone()[0]
    print(f"[buildings] {n} real footprints fetched in {time.time()-t0:.1f}s (quadkey {quadkey}, {source_desc})")
    return cache_path


def building_footprints_lonlat(aoi_polygon_wgs84, buildings_parquet, osm_heights=None,
                                assumed_height_m=ASSUMED_BUILDING_HEIGHT_M, max_footprints=8000):
    """Real building footprints in plain lon/lat (WGS84), for Mapbox GL JS
    `fill-extrusion` GeoJSON sources -- unlike get_building_footprints
    (below), which converts to the DEM's own row/col grid space for the
    Three.js viewer pipeline. Shared by the Area 3D tool's /area/buildings
    route and the main app's own Mapbox-based flood 3D scene, so both use
    the exact same real fetch/filter/height logic instead of two
    divergent copies.

    aoi_polygon_wgs84: shapely Polygon/MultiPolygon, the real drawn AOI --
    each footprint's own real centroid is tested against it directly
    (Google Open Buildings' source data is already WGS84 lon/lat, no
    reprojection needed).
    osm_heights: optional, from infra_utils.fetch_osm_building_heights --
    same real-data-first height logic as get_building_footprints (real OSM
    height tag wins if a real OSM building polygon contains this
    footprint's centroid, else assumed_height_m).

    Returns a GeoJSON-ready list of {"height_m", "height_source",
    "coords": [[lon,lat],...]} dicts, capped at max_footprints by area
    (largest first) for the same reason get_building_footprints caps --
    a dense AOI can have tens of thousands of tiny footprints that add no
    visible detail at typical browser 3D-view zoom levels."""
    from shapely import wkt as shapely_wkt
    from shapely.geometry import Point, Polygon as ShapelyPolygon

    osm_polys = []
    for oh in (osm_heights or []):
        if len(oh["geometry"]) >= 3:
            try:
                osm_polys.append((ShapelyPolygon(oh["geometry"]), oh["height_m"]))
            except Exception:
                pass

    def real_height_at(lon, lat):
        pt = Point(lon, lat)
        for poly, height_m in osm_polys:
            if poly.contains(pt):
                return height_m
        return None

    con = duckdb.connect()
    rows = con.execute(f"SELECT wkt, area_in_meters FROM read_parquet('{buildings_parquet}')").fetchall()

    candidates = []
    for wkt_str, area_m2 in rows:
        try:
            poly = shapely_wkt.loads(wkt_str)
        except Exception:
            continue
        if poly.geom_type != "Polygon" or not aoi_polygon_wgs84.contains(poly.centroid):
            continue
        lon_c, lat_c = poly.centroid.x, poly.centroid.y
        real_height = real_height_at(lon_c, lat_c)
        height_m = real_height if real_height is not None else assumed_height_m
        source = "osm_height" if real_height is not None else "estimated"
        candidates.append((area_m2 or 0.0, height_m, source, list(poly.exterior.coords)))

    candidates.sort(key=lambda t: -t[0])
    return [
        {"height_m": height_m, "height_source": source, "coords": [[lon, lat] for lon, lat in coords]}
        for _, height_m, source, coords in candidates[:max_footprints]
    ]


def burn_buildings_into_dem(header, dem, buildings_parquet, lonlat_to_src_crs, nodata=-9999):
    """Rasterizes each building's FULL real footprint polygon onto the DEM
    grid (not just its centroid cell), raising every covered cell by
    ASSUMED_BUILDING_HEIGHT_M so they act as flow obstacles. Returns
    (conditioned_dem, building_mask, n_buildings_used).

    Rasterizing only the centroid cell (an earlier version of this function)
    matched the DEM conditioning used by the physics run to only ONE grid
    cell per building, while the 3D view (get_building_footprints, below)
    always drew each building's full real polygon shape -- at ~29m grid
    resolution a real footprint routinely extends into 2-6 neighboring
    cells the physics model never touched, so LISFLOOD-FP only ever
    blocked flow at the single centroid cell. The result: flood water
    could legitimately reach the cells under most of a visible building's
    footprint, because those cells were never actually raised -- it read
    as "water flowing under/through buildings" but was really "the
    physics model's flow obstacle was smaller than the picture of the
    building drawn on top of it." Rasterizing the full polygon (like
    get_building_footprints already did for the visual) makes the two
    consistent.

    Skips any building whose mapped centroid cell is NODATA (outside an
    irregular polygon AOI's actual shape but inside its bounding-box grid)
    -- without this check, adding +6m to -9999 gives -9993, which every
    downstream consumer reads as "valid terrain at -9993m" instead of "no
    data", a ~10,000m false cliff relative to real terrain. Caught by a 3D
    render showing what looked like giant vertical walls cutting through
    the scene."""
    from shapely import wkt as shapely_wkt
    from shapely.ops import transform as shapely_transform
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    con = duckdb.connect()
    rows = con.execute(f"SELECT wkt FROM read_parquet('{buildings_parquet}')").fetchall()

    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]
    transform = from_origin(xll, yll + nrows * cs, cs, cs)

    shapes = []
    hit = 0
    for (wkt_str,) in rows:
        geom = shapely_wkt.loads(wkt_str)
        lon_c, lat_c = geom.centroid.x, geom.centroid.y
        x_m, y_m = lonlat_to_src_crs(lon_c, lat_c)
        col = int((x_m - xll) / cs)
        row = int(nrows - 1 - (y_m - yll) / cs)
        if not (0 <= row < nrows and 0 <= col < ncols) or dem[row, col] == nodata:
            continue
        proj_geom = shapely_transform(lonlat_to_src_crs, geom)
        if proj_geom.is_valid and not proj_geom.is_empty:
            shapes.append(proj_geom)
            hit += 1

    building_mask = np.zeros((nrows, ncols), dtype=bool)
    if shapes:
        building_mask = rasterize([(g, 1) for g in shapes], out_shape=(nrows, ncols),
                                   transform=transform, fill=0, dtype="uint8").astype(bool)
        building_mask &= (dem != nodata)  # never raise a NODATA cell even if a footprint clips into it

    conditioned = dem.copy()
    conditioned[building_mask] += ASSUMED_BUILDING_HEIGHT_M
    return conditioned, building_mask, hit


def get_building_footprints(header, dem, buildings_parquet, lonlat_to_src_crs, nodata=-9999,
                             max_footprints=6000, osm_heights=None, aoi_polygon_wgs84=None):
    """Real per-building footprint polygons (not single-cell boxes), for
    proper-shaped 3D extrusion instead of the grid-cell blobs a DEM-baked
    +6m bump produces. Returns a list of {"poly_rc": [[row,col],...],
    "height_m": float, "height_source": "osm"|"estimated"} for every
    building whose centroid lands on valid terrain (same NODATA-skip rule
    as burn_buildings_into_dem).

    aoi_polygon_wgs84: optional shapely Polygon/MultiPolygon (lon/lat) of
    the ACTUAL drawn/uploaded AOI -- the `dem[row,col] != nodata` check
    alone only excludes buildings at whole-cell (raster) resolution, which
    can disagree with the terrain's own EXACT vector clip (viewer3d_utils.
    clipGeometryToAoi) right at the boundary: a building can sit in a cell
    the coarse raster mask still calls "valid" while the terrain's precise
    clip has already cut that sliver of ground away, rendering as a
    building floating in empty space next to (not on) the terrain. Passing
    the real polygon here tests each building against the SAME exact
    shape the terrain uses, not just the raster approximation of it.

    osm_heights: optional list of {"height_m", "geometry": [(lon,lat),...]}
    from infra_utils.fetch_osm_building_heights -- real OSM height/floor
    tags are sparse in Pakistan (~2% of buildings in a dense test area),
    so this is applied where a real OSM-tagged building polygon actually
    contains the footprint's centroid; every other building keeps the
    documented single-story estimate (ASSUMED_BUILDING_HEIGHT_M).

    Capped at max_footprints (by area, largest first) purely for browser
    rendering performance -- a dense urban AOI can have tens of thousands of
    footprints, most of them tiny huts that don't add visible detail at
    typical 3D-view zoom levels.

    Vectorized (shapely 2's array-level ops + an STRtree for the OSM-height
    lookup), not a per-building Python loop -- a real, measured bottleneck
    found via direct profiling, not assumed: the original per-building
    `real_height_at` linear-scanned every OSM-tagged height candidate for
    EVERY building (a real O(n_buildings * n_osm_candidates) point-in-
    polygon cost), confirmed via cProfile on a synthetic 3,000-building/
    3,000-OSM-candidate case to be 98% of total wall time (19.8s of 20.1s,
    6.86 MILLION individual `.contains()` calls) -- at the real whole-
    Islamabad scale (446,787 buildings) this is exactly consistent with
    the 10+ minutes of post-processing observed on that real run. Fixed by
    building the STRtree ONCE and querying all building centroids against
    it in one batched call (`predicate='within'`), the standard spatial-
    index technique for this exact class of point-in-many-polygons lookup,
    instead of re-scanning every OSM candidate per building."""
    import shapely
    from shapely.strtree import STRtree
    from shapely.geometry import Polygon as ShapelyPolygon

    osm_polys = []
    osm_heights_arr = []
    for oh in (osm_heights or []):
        if len(oh["geometry"]) >= 3:
            try:
                osm_polys.append(ShapelyPolygon(oh["geometry"]))
                osm_heights_arr.append(oh["height_m"])
            except Exception:
                pass
    osm_tree = STRtree(osm_polys) if osm_polys else None

    con = duckdb.connect()
    rows = con.execute(
        f"SELECT wkt, area_in_meters FROM read_parquet('{buildings_parquet}')").fetchall()
    if not rows:
        return []

    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]

    def to_rc_arr(lons, lats):
        x_m, y_m = lonlat_to_src_crs(lons, lats)
        col = (np.asarray(x_m) - xll) / cs
        row = nrows - 1 - (np.asarray(y_m) - yll) / cs
        return row, col

    wkt_arr = np.array([r[0] for r in rows], dtype=object)
    area_arr = np.array([r[1] if r[1] is not None else 0.0 for r in rows], dtype=float)
    geoms = shapely.from_wkt(wkt_arr)

    is_polygon = shapely.get_type_id(geoms) == 3  # 3 == Polygon in shapely's own GeometryType enum
    centroids = shapely.centroid(geoms)
    lons_c = shapely.get_x(centroids)
    lats_c = shapely.get_y(centroids)

    crow, ccol = to_rc_arr(lons_c, lats_c)
    r0 = crow.astype(np.int64)
    c0 = ccol.astype(np.int64)
    in_bounds = (r0 >= 0) & (r0 < nrows) & (c0 >= 0) & (c0 < ncols)
    r0_safe = np.clip(r0, 0, nrows - 1)
    c0_safe = np.clip(c0, 0, ncols - 1)
    valid_dem = dem[r0_safe, c0_safe] != nodata

    keep = is_polygon & in_bounds & valid_dem
    if aoi_polygon_wgs84 is not None:
        centroid_points = shapely.points(lons_c, lats_c)
        keep = keep & shapely.contains(aoi_polygon_wgs84, centroid_points)

    survivor_idx = np.nonzero(keep)[0]
    if survivor_idx.size == 0:
        return []

    surv_geoms = geoms[survivor_idx]
    surv_lons = lons_c[survivor_idx]
    surv_lats = lats_c[survivor_idx]
    surv_areas = area_arr[survivor_idx]

    # Real per-building height lookup, vectorized: one batched STRtree
    # query against every surviving centroid instead of a per-building scan.
    real_heights = np.full(survivor_idx.size, None, dtype=object)
    if osm_tree is not None:
        surv_points = shapely.points(surv_lons, surv_lats)
        pairs = osm_tree.query(surv_points, predicate="within")  # shape (2, k): [point_idx_row, tree_idx_row]
        if pairs.shape[1] > 0:
            # A point could sit inside more than one OSM polygon (rare, but
            # possible for overlapping/adjacent tagged footprints) -- keep
            # the smallest tree index per point, matching the original
            # code's own "first match in list order" tie-break exactly.
            point_idx, tree_idx = pairs[0], pairs[1]
            order = np.lexsort((tree_idx, point_idx))
            point_idx_sorted, tree_idx_sorted = point_idx[order], tree_idx[order]
            first_mask = np.concatenate(([True], point_idx_sorted[1:] != point_idx_sorted[:-1]))
            for pi, ti in zip(point_idx_sorted[first_mask], tree_idx_sorted[first_mask]):
                real_heights[pi] = osm_heights_arr[ti]

    # Real exterior-ring vertex transform, also vectorized: extract every
    # surviving polygon's exterior coordinates in one call (shapely's own
    # get_coordinates groups them by geometry, in order), transform every
    # coordinate at once, then split back into one list per building.
    exterior_ok = shapely.get_type_id(surv_geoms) == 3
    coords, coord_geom_idx = shapely.get_coordinates(surv_geoms, return_index=True)
    if coords.shape[0] > 0:
        t_rows, t_cols = to_rc_arr(coords[:, 0], coords[:, 1])
        coords_rc = np.stack([t_rows, t_cols], axis=1)
    else:
        coords_rc = coords

    # shapely's return_index groups coordinates by geometry in ascending,
    # contiguous blocks -- split on each boundary to recover per-building lists.
    split_points = np.nonzero(np.diff(coord_geom_idx))[0] + 1
    per_geom_coords = np.split(coords_rc, split_points) if coords_rc.shape[0] > 0 else []
    per_geom_owner = np.split(coord_geom_idx, split_points) if coord_geom_idx.shape[0] > 0 else []

    exterior_by_geom = {}
    for owner_idx, block in zip(per_geom_owner, per_geom_coords):
        exterior_by_geom[int(owner_idx[0])] = block

    candidates = []
    for i in range(survivor_idx.size):
        if not exterior_ok[i]:
            continue
        block = exterior_by_geom.get(i)
        if block is None or block.shape[0] < 4:
            continue
        poly_rc = block.tolist()
        real_height = real_heights[i]
        candidates.append((surv_areas[i], poly_rc, real_height))

    candidates.sort(key=lambda t: -t[0])
    footprints = [
        {"poly_rc": poly_rc,
         "height_m": real_height if real_height is not None else ASSUMED_BUILDING_HEIGHT_M,
         "height_source": "osm" if real_height is not None else "estimated"}
        for _, poly_rc, real_height in candidates[:max_footprints]
    ]
    return footprints
