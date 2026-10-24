"""Vector infrastructure from the SimCity PostGIS (schema `pk`, national), as a faster, richer source than the local
shapefiles/GeoPackage. Every function returns exactly what the matching `infra_utils.fetch_local_*` returns, or None
when PostGIS is not configured or not reachable, so the caller falls back to the files and a run never breaks.

Configure with SAILAB_PG_URL (e.g. in .env), `postgresql://user:password@host:port/db`. The connection is read-only.
Spatial filter: `geom && envelope` uses the table's GiST index.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

_ENV = "SAILAB_PG_URL"
_failed_until = 0.0     # after a failure, skip PostGIS for a short while instead of retrying on every call


def enabled() -> bool:
    return bool(os.environ.get(_ENV, "").strip()) and time.time() >= _failed_until


def _query(sql: str, params: tuple):
    """Rows for a query, or None (and a 60 s back-off) when PostGIS cannot be used."""
    global _failed_until
    if not enabled():
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ[_ENV], connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=30000")
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        _failed_until = time.time() + 60
        print(f"[postgis] unavailable, using local files instead ({e.__class__.__name__}: {str(e)[:120]})")
        return None


def _plain(v):
    """JSON-safe value: PostGIS hands back Decimal for numeric columns, which the app cannot save to summary.json."""
    from decimal import Decimal
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _env(lon_min, lat_min, lon_max, lat_max):
    return (lon_min, lat_min, lon_max, lat_max)


def _pts(geojson: str):
    return [(float(p[0]), float(p[1])) for p in json.loads(geojson)["coordinates"]]


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _num(v, as_int=False):
    """Text column to a number: '2' -> 2, '2;3' or '2, 3' -> 2 (first value), '7.5 m' -> 7.5, junk -> None."""
    import re
    if v is None:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(v))
    if not m:
        return None
    x = float(m.group(1))
    return int(x) if as_int else x


def inside_study_area(lon_min, lat_min, lon_max, lat_max) -> Optional[bool]:
    """True when the box lies completely inside the Islamabad study area (where `isb.*` is complete)."""
    rows = _query("select st_covers(geom, st_makeenvelope(%s,%s,%s,%s,4326)) from isb.study_area limit 1", _env(lon_min, lat_min, lon_max, lat_max))
    return None if rows is None else bool(rows and rows[0][0])


def fetch_roads(lon_min, lat_min, lon_max, lat_max) -> Optional[list]:
    """Roads for the box. Completely inside Islamabad: `isb.roads_drivable` (footways, paths, tracks and roads under
    construction removed, so flooded-road numbers are about roads vehicles can use). Anywhere else, or partly outside:
    the national `pk.roads`, unfiltered, as before. Each road also carries lanes (int), surface, oneway, bridge, width."""
    t0 = time.time()
    inside = inside_study_area(lon_min, lat_min, lon_max, lat_max)
    if inside is None:
        return None
    table = "isb.roads_drivable" if inside else "pk.roads"
    width_col = "null::text" if inside else "r.width"
    rows = _query(f"""select r.osm_id, r.name, r.highway, st_asgeojson(d.geom), r.lanes, r.surface, {width_col}, r.oneway, r.bridge
                      from {table} r, lateral st_dump(r.geom) d
                      where r.geom && st_makeenvelope(%s,%s,%s,%s,4326) and r.highway is not null""", _env(lon_min, lat_min, lon_max, lat_max))
    if rows is None:
        return None
    out = []
    for osm_id, name, highway, gj, lanes, surface, width, oneway, bridge in rows:
        pts = _pts(gj)
        if len(pts) < 2:
            continue
        out.append({"id": _plain(osm_id), "name": name or "unnamed road", "highway": highway, "centroid": _centroid(pts), "geometry": pts,
                    "lanes": _num(lanes, as_int=True), "surface": surface, "width": _num(width), "oneway": oneway,
                    "bridge": bridge not in (None, "", "no")})
    print(f"[roads] {len(out)} roads read from PostGIS ({table}) in {time.time()-t0:.2f}s")
    return out


def fetch_critical(lon_min, lat_min, lon_max, lat_max) -> Optional[list]:
    b = _env(lon_min, lat_min, lon_max, lat_max)
    hosp = _query("""select objectid, name, category, beds, functional, status, st_x(st_centroid(geom)), st_y(st_centroid(geom))
                     from pk.hospitals where geom && st_makeenvelope(%s,%s,%s,%s,4326)""", b)
    sch = _query("""select objectid, schoolname, enrollment, status, st_x(st_centroid(geom)), st_y(st_centroid(geom))
                    from pk.schools where geom && st_makeenvelope(%s,%s,%s,%s,4326)""", b)
    if hosp is None or sch is None:
        return None
    out = []
    for oid, name, cat, beds, functional, status, x, y in hosp:
        out.append({"id": f"hospital_{oid}", "name": name or "hospital", "type": "hospital", "subtype": cat,
                    "centroid": (_plain(x), _plain(y)), "beds": _plain(beds), "functional": functional, "status": status})
    for oid, name, enrol, status, x, y in sch:
        out.append({"id": f"school_{oid}", "name": name or "school", "type": "school", "subtype": None,
                    "centroid": (_plain(x), _plain(y)), "enrollment": _plain(enrol), "status": status})
    print(f"[critical] {len(out)} hospitals+schools read from PostGIS")
    return out


def fetch_power(lon_min, lat_min, lon_max, lat_max) -> Optional[list]:
    rows = _query("""select p.objectid, st_asgeojson(d.geom) from pk.power_lines p, lateral st_dump(p.geom) d
                     where p.geom && st_makeenvelope(%s,%s,%s,%s,4326)""", _env(lon_min, lat_min, lon_max, lat_max))
    if rows is None:
        return None
    out = []
    for oid, gj in rows:
        pts = _pts(gj)
        if len(pts) >= 2:
            out.append({"id": f"power_{oid}", "type": "line", "centroid": _centroid(pts), "geometry": pts})
    print(f"[power] {len(out)} transmission lines read from PostGIS")
    return out


def fetch_railway(lon_min, lat_min, lon_max, lat_max) -> Optional[list]:
    rows = _query("""select r.id, r.name, r.railway, st_asgeojson(d.geom), d.path[1]
                     from pk.railway_lines r, lateral st_dump(r.geom) d where r.geom && st_makeenvelope(%s,%s,%s,%s,4326)""",
                  _env(lon_min, lat_min, lon_max, lat_max))
    if rows is None:
        return None
    out = []
    for rid, name, kind, gj, j in rows:
        pts = _pts(gj)
        if len(pts) >= 2:
            out.append({"id": f"railway_{rid}_{j - 1}", "name": name or "railway", "type": kind or "rail",
                        "centroid": _centroid(pts), "geometry": pts})
    print(f"[railway] {len(out)} railway line parts read from PostGIS")
    return out


def fetch_airports_and_stations(lon_min, lat_min, lon_max, lat_max) -> Optional[list]:
    """Airports and railway stations only; sea ports are not in PostGIS and stay with the caller."""
    b = _env(lon_min, lat_min, lon_max, lat_max)
    ap = _query("""select id, name, st_x(geom), st_y(geom) from pk.airports where geom && st_makeenvelope(%s,%s,%s,%s,4326)""", b)
    st = _query("""select id, name, st_x(geom), st_y(geom) from pk.railway_stations where geom && st_makeenvelope(%s,%s,%s,%s,4326)""", b)
    if ap is None or st is None:
        return None
    out = [{"id": f"airport_{i}", "name": n or "airport", "type": "airport", "centroid": (_plain(x), _plain(y))} for i, n, x, y in ap]
    out += [{"id": f"railway_station_{i}", "name": n or "railway_station", "type": "railway_station", "centroid": (_plain(x), _plain(y))} for i, n, x, y in st]
    return out
