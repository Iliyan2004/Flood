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
    url = GOB_URL.format(quadkey=quadkey)

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    t0 = time.time()
    q = f"""
    COPY (
      SELECT area_in_meters, confidence, ST_AsText(geometry) as wkt
      FROM read_parquet('{url}')
      WHERE ST_Intersects(geometry, ST_MakeEnvelope({lon_min}, {lat_min}, {lon_max}, {lat_max}))
    ) TO '{cache_path}' (FORMAT PARQUET)
    """
    try:
        con.execute(q)
    except Exception as e:
        print(f"[buildings] fetch failed for quadkey {quadkey}: {e}")
        return None
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{cache_path}')").fetchone()[0]
    print(f"[buildings] {n} real footprints fetched in {time.time()-t0:.1f}s (quadkey {quadkey})")
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
    typical 3D-view zoom levels."""
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
    rows = con.execute(
        f"SELECT wkt, area_in_meters FROM read_parquet('{buildings_parquet}')").fetchall()

    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]

    def to_rc(lon, lat):
        x_m, y_m = lonlat_to_src_crs(lon, lat)
        col = (x_m - xll) / cs
        row = nrows - 1 - (y_m - yll) / cs
        return row, col

    candidates = []
    for wkt_str, area_m2 in rows:
        geom = shapely_wkt.loads(wkt_str)
        lon_c, lat_c = geom.centroid.x, geom.centroid.y
        crow, ccol = to_rc(lon_c, lat_c)
        r0, c0 = int(crow), int(ccol)
        if not (0 <= r0 < nrows and 0 <= c0 < ncols) or dem[r0, c0] == nodata:
            continue
        if aoi_polygon_wgs84 is not None and not aoi_polygon_wgs84.contains(Point(lon_c, lat_c)):
            continue
        exterior = list(geom.exterior.coords) if geom.geom_type == "Polygon" else None
        if not exterior or len(exterior) < 4:
            continue
        poly_rc = [list(to_rc(lon, lat)) for lon, lat in exterior]
        real_height = real_height_at(lon_c, lat_c) if osm_polys else None
        candidates.append((area_m2 or 0.0, poly_rc, real_height))

    candidates.sort(key=lambda t: -t[0])
    footprints = [
        {"poly_rc": poly_rc,
         "height_m": real_height if real_height is not None else ASSUMED_BUILDING_HEIGHT_M,
         "height_source": "osm" if real_height is not None else "estimated"}
        for _, poly_rc, real_height in candidates[:max_footprints]
    ]
    return footprints
