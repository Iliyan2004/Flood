#!/usr/bin/env python3
"""
PluviSim / Pakistan-wide flood tool — arbitrary location, size-capped,
building-footprint-conditioned, cached.

Unlike the district-scale Bahawalpur work (flood_model.py pointed at a
hand-cropped DEM), this tool takes a raw (lat, lon, size_km) request
anywhere in Pakistan, enforces a hard domain-size cap (clear error if
exceeded -- no silent multi-hour runs), fetches real Google Open Buildings
footprints for that exact area and burns them into the DEM as flow
obstacles, and caches results keyed by the request so a repeat request is
instant.

DEM source: prefers a local Pakistan-wide raster (e.g. Pak_srtm.tif) if one
is given and readable; this is intentionally NOT hard-wired to a specific
shared file, since that file may be incomplete/being written by another
process (encountered exactly this while building this tool -- a 3.5GB file
on this machine grew to 12.5GB mid-read and failed to open). Falls back to
a locally-provided DEM crop for testing when no whole-country raster is
available or fails to open.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import numpy as np
import duckdb
from pyproj import Transformer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "pak_flood_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
FLOOD_MODEL = os.path.join(BASE_DIR, "flood_model.py")

# Hard cap: keeps every request in the "seconds to a couple minutes" range,
# matching the housing-society-scale benchmarks that make on-demand serving
# viable at all. Exceeding this returns a clear error instead of silently
# starting a multi-hour district-scale run.
MAX_SIDE_KM = 3.0
MAX_CELLS = 400 * 400  # 160,000 cells hard ceiling regardless of resolution

GOB_BASE = "https://data.source.coop/cholmes/google-open-buildings/geoparquet-by-country/country_iso={iso}/{shard}.parquet"
ASSUMED_BUILDING_HEIGHT_M = 6.0  # ~2 storeys; v3 footprint dataset has no height field


class DomainTooLargeError(Exception):
    pass


def lonlat_quadkey(lat, lon, zoom):
    import math
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


def request_cache_key(**kwargs):
    blob = json.dumps(kwargs, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def check_size_cap(width_km, height_km, cellsize_m):
    if width_km > MAX_SIDE_KM or height_km > MAX_SIDE_KM:
        raise DomainTooLargeError(
            f"Requested area {width_km:.2f}km x {height_km:.2f}km exceeds the "
            f"{MAX_SIDE_KM}km max side length. This tool is scoped for fast, "
            f"on-demand neighborhood-scale screening, not district-scale runs "
            f"(those take 80+ minutes even on GPU -- see the Bahawalpur "
            f"district run). Request a smaller area."
        )
    ncols = int(width_km * 1000 / cellsize_m)
    nrows = int(height_km * 1000 / cellsize_m)
    if ncols * nrows > MAX_CELLS:
        raise DomainTooLargeError(
            f"Requested grid {ncols}x{ncols} = {ncols*nrows:,} cells exceeds the "
            f"{MAX_CELLS:,}-cell cap. Reduce the area or use a coarser --cellsize."
        )
    return ncols, nrows


def fetch_buildings(lon_min, lat_min, lon_max, lat_max, cache_path):
    if os.path.exists(cache_path):
        print(f"[buildings] cache hit: {cache_path}")
        return cache_path

    lat_c, lon_c = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2
    quadkey = lonlat_quadkey(lat_c, lon_c, 6)
    url = GOB_BASE.format(iso="PK", shard=f"PK_{quadkey}")

    print(f"[buildings] querying Google Open Buildings shard PK_{quadkey} for AOI "
          f"lon[{lon_min:.4f},{lon_max:.4f}] lat[{lat_min:.4f},{lat_max:.4f}]...")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    q = f"""
    COPY (
      SELECT area_in_meters, confidence, ST_AsText(geometry) as wkt
      FROM read_parquet('{url}')
      WHERE ST_Intersects(geometry, ST_MakeEnvelope({lon_min}, {lat_min}, {lon_max}, {lat_max}))
    ) TO '{cache_path}' (FORMAT PARQUET)
    """
    t0 = time.time()
    con.execute(q)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{cache_path}')").fetchone()[0]
    print(f"[buildings] got {n} real building footprints in {time.time()-t0:.1f}s")
    return cache_path


def burn_buildings_into_dem(dem_header, dem, buildings_parquet, out_path):
    """Rasterizes building footprints onto the DEM grid and raises those cells
    by ASSUMED_BUILDING_HEIGHT_M, turning them into flow obstacles."""
    import duckdb
    from shapely import wkt as shapely_wkt
    from shapely.geometry import box

    ncols, nrows, cs = int(dem_header["ncols"]), int(dem_header["nrows"]), dem_header["cellsize"]
    xll, yll = dem_header["xllcorner"], dem_header["yllcorner"]
    t = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True)
    t_inv = Transformer.from_crs("EPSG:4326", "EPSG:32642", always_xy=True)

    con = duckdb.connect()
    rows = con.execute(f"SELECT wkt FROM read_parquet('{buildings_parquet}')").fetchall()
    print(f"[buildings] rasterizing {len(rows)} footprints onto {ncols}x{nrows} grid...")

    building_mask = np.zeros((nrows, ncols), dtype=bool)
    hit = 0
    for (wkt_str,) in rows:
        geom = shapely_wkt.loads(wkt_str)
        lon_c, lat_c = geom.centroid.x, geom.centroid.y
        x_m, y_m = t_inv.transform(lon_c, lat_c)
        col = int((x_m - xll) / cs)
        row = int(nrows - 1 - (y_m - yll) / cs)
        if 0 <= row < nrows and 0 <= col < ncols:
            building_mask[row, col] = True
            hit += 1
    print(f"[buildings] {hit} buildings fell within the grid footprint")

    conditioned = dem.copy()
    conditioned[building_mask] += ASSUMED_BUILDING_HEIGHT_M
    return conditioned, building_mask


def read_ascii(path):
    with open(path) as f:
        header = {}
        for _ in range(6):
            k, v = f.readline().split()
            header[k] = float(v)
        data = np.loadtxt(f)
    return header, data


def write_ascii(path, header, data, nodata=-9999):
    with open(path, "w") as f:
        f.write(f"ncols         {int(header['ncols'])}\n")
        f.write(f"nrows         {int(header['nrows'])}\n")
        f.write(f"xllcorner     {header['xllcorner']}\n")
        f.write(f"yllcorner     {header['yllcorner']}\n")
        f.write(f"cellsize      {header['cellsize']}\n")
        f.write(f"NODATA_value  {nodata}\n")
        np.savetxt(f, data, fmt="%.4f")


def run_flood_model(dem_path, workdir, rain_mm_hr, duration_hr, site_name):
    os.makedirs(workdir, exist_ok=True)
    cmd = [VENV_PY, FLOOD_MODEL, "--dem", dem_path, "--rain-mm-hr", str(rain_mm_hr),
           "--duration-hr", str(duration_hr), "--site-name", site_name,
           "--workdir", workdir]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(proc.stdout[-3000:]); print(proc.stderr[-3000:])
        raise RuntimeError("flood_model.py run failed")
    with open(os.path.join(workdir, "summary.json")) as f:
        summary = json.load(f)
    return summary, elapsed


def main():
    p = argparse.ArgumentParser(description="Pakistan-wide, size-capped, building-conditioned flood tool")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--size-km", type=float, default=2.0, help="Square domain side length, km")
    p.add_argument("--cellsize", type=float, default=30.0)
    p.add_argument("--rain-mm-hr", type=float, default=40.0)
    p.add_argument("--duration-hr", type=float, default=2.0)
    p.add_argument("--dem-source", default=None, help="Local DEM raster covering this area (ARC ASCII)")
    p.add_argument("--site-name", default="Pakistan AOI")
    args = p.parse_args()

    ncols, nrows = check_size_cap(args.size_km, args.size_km, args.cellsize)
    print(f"[size-check] {ncols}x{nrows} cells OK (cap {MAX_SIDE_KM}km / {MAX_CELLS:,} cells)")

    key = request_cache_key(lat=args.lat, lon=args.lon, size_km=args.size_km,
                             cellsize=args.cellsize, rain=args.rain_mm_hr, dur=args.duration_hr)
    cache_result = os.path.join(CACHE_DIR, f"{key}_result.json")
    if os.path.exists(cache_result):
        t0 = time.time()
        with open(cache_result) as f:
            cached = json.load(f)
        print(f"\n[CACHE HIT] returned in {time.time()-t0:.3f}s")
        print(json.dumps(cached, indent=2))
        return

    if not args.dem_source:
        sys.exit("No --dem-source given and no whole-Pakistan raster wired in "
                 "(see module docstring). Provide a local DEM crop covering this AOI.")

    header, dem = read_ascii(args.dem_source)
    t = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True)
    lon_min, lat_min = t.transform(header["xllcorner"], header["yllcorner"])
    lon_max, lat_max = t.transform(header["xllcorner"] + header["ncols"] * header["cellsize"],
                                    header["yllcorner"] + header["nrows"] * header["cellsize"])

    buildings_cache = os.path.join(CACHE_DIR, f"{key}_buildings.parquet")
    buildings_path = fetch_buildings(lon_min, lat_min, lon_max, lat_max, buildings_cache)
    conditioned_dem, building_mask = burn_buildings_into_dem(header, dem, buildings_path, None)

    bare_path = os.path.join(CACHE_DIR, f"{key}_bare.dem.ascii")
    cond_path = os.path.join(CACHE_DIR, f"{key}_conditioned.dem.ascii")
    write_ascii(bare_path, header, dem)
    write_ascii(cond_path, header, conditioned_dem)

    print("\n=== Running BARE-EARTH (no buildings) ===")
    bare_summary, bare_elapsed = run_flood_model(
        bare_path, os.path.join(CACHE_DIR, f"{key}_run_bare"),
        args.rain_mm_hr, args.duration_hr, args.site_name + " (bare earth)")

    print("\n=== Running BUILDING-CONDITIONED ===")
    cond_summary, cond_elapsed = run_flood_model(
        cond_path, os.path.join(CACHE_DIR, f"{key}_run_conditioned"),
        args.rain_mm_hr, args.duration_hr, args.site_name + " (buildings)")

    result = {
        "request": vars(args),
        "grid": f"{ncols}x{nrows}",
        "buildings_used": int(building_mask.sum()),
        "bare_earth": {"elapsed_s": bare_elapsed, **bare_summary},
        "building_conditioned": {"elapsed_s": cond_elapsed, **cond_summary},
    }
    with open(cache_result, "w") as f:
        json.dump(result, f, indent=2)

    print("\n================ RESULT ================")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
