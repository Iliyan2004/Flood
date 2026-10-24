"""
Fast tests for infra_utils.py's local (no-Overpass) roads fetch. Uses the
real, already-converted local HOTOSM roads GeoPackage
(infra_utils.ROADS_PK_GPKG) -- skips (not fails) if that real local file
isn't present on this machine, since it's a real ~420MB derived dataset,
not something bundled with the repo/tests.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import infra_utils  # noqa: E402

# A real, small Islamabad-area test bbox already used elsewhere this
# session to verify this exact dataset -- known to contain real roads.
TEST_BBOX = (73.052, 33.683, 73.062, 33.693)

_has_roads_gpkg = os.path.exists(infra_utils.ROADS_PK_GPKG)
_roads_skip = pytest.mark.skipif(not _has_roads_gpkg,
                                  reason="local HOTOSM roads GeoPackage not present on this machine")


@_roads_skip
def test_fetch_local_roads_returns_real_roads_for_a_known_bbox():
    roads = infra_utils.fetch_local_roads(*TEST_BBOX)
    assert len(roads) > 0


@_roads_skip
def test_fetch_local_roads_schema_matches_the_original_overpass_shape():
    """Real, load-bearing check: downstream code (risk sampling, the
    Mapbox 3D scene) expects this exact schema regardless of which real
    source (Overpass or this local file) produced it."""
    roads = infra_utils.fetch_local_roads(*TEST_BBOX)
    r = roads[0]
    assert set(r.keys()) == {"id", "name", "highway", "centroid", "geometry"}
    assert isinstance(r["highway"], str) and r["highway"]
    assert isinstance(r["centroid"], tuple) and len(r["centroid"]) == 2
    assert isinstance(r["geometry"], list) and len(r["geometry"]) >= 2
    assert all(isinstance(p, tuple) and len(p) == 2 for p in r["geometry"])


@_roads_skip
def test_fetch_local_roads_centroid_is_real_mean_of_vertices():
    roads = infra_utils.fetch_local_roads(*TEST_BBOX)
    r = roads[0]
    expected_lon = sum(p[0] for p in r["geometry"]) / len(r["geometry"])
    expected_lat = sum(p[1] for p in r["geometry"]) / len(r["geometry"])
    assert r["centroid"][0] == pytest.approx(expected_lon)
    assert r["centroid"][1] == pytest.approx(expected_lat)


@_roads_skip
def test_fetch_local_roads_empty_bbox_over_open_ocean_returns_nothing():
    """A real bbox with no roads at all (open Arabian Sea, well south of
    Pakistan's coast) must return an empty list, not error out."""
    roads = infra_utils.fetch_local_roads(65.0, 20.0, 65.05, 20.05)
    assert roads == []


def test_fetch_local_roads_missing_file_returns_empty_list_not_raises(monkeypatch):
    monkeypatch.setattr(infra_utils, "ROADS_PK_GPKG", "/nonexistent/path/roads.gpkg")
    assert infra_utils.fetch_local_roads(*TEST_BBOX) == []


# --------------------------------------------------------------------------
# Local hospitals/schools/power-line fetches -- separate real local
# shapefiles from the roads GPKG above, so these get their own skip
# conditions rather than inheriting the module-wide roads-only marker.
# --------------------------------------------------------------------------
_has_hospitals = os.path.exists(infra_utils.HOSPITALS_SHP)
_has_schools = os.path.exists(infra_utils.SCHOOLS_SHP)
_has_power = os.path.exists(infra_utils.POWER_LINES_SHP)

# A real, larger bbox (Multan area) already confirmed during development
# to contain real critical-infra and power-line features -- the small
# Islamabad TEST_BBOX above is too small to reliably hit a power line.
BIG_TEST_BBOX = (70.5, 29.5, 71.5, 30.5)


@pytest.mark.skipif(not (_has_hospitals and _has_schools),
                     reason="local Hospitals/Schools shapefiles not present on this machine")
def test_fetch_local_critical_infra_returns_real_features():
    critical = infra_utils.fetch_local_critical_infra(*BIG_TEST_BBOX)
    assert len(critical) > 0
    types = {c["type"] for c in critical}
    assert types <= {"hospital", "school"}


@pytest.mark.skipif(not (_has_hospitals and _has_schools),
                     reason="local Hospitals/Schools shapefiles not present on this machine")
def test_fetch_local_critical_infra_schema_matches_original_overpass_shape():
    critical = infra_utils.fetch_local_critical_infra(*BIG_TEST_BBOX)
    c = critical[0]
    assert {"id", "name", "type", "centroid"} <= set(c.keys())
    assert isinstance(c["centroid"], tuple) and len(c["centroid"]) == 2


@pytest.mark.skipif(not _has_power, reason="local power-line shapefile not present on this machine")
def test_fetch_local_power_lines_returns_real_features():
    power = infra_utils.fetch_local_power_lines(*BIG_TEST_BBOX)
    assert len(power) > 0
    p = power[0]
    assert set(p.keys()) == {"id", "type", "centroid", "geometry"}
    assert p["type"] == "line"
    assert len(p["geometry"]) >= 2


def test_fetch_local_critical_infra_missing_files_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(infra_utils, "HOSPITALS_SHP", "/nonexistent/hospitals.shp")
    monkeypatch.setattr(infra_utils, "SCHOOLS_SHP", "/nonexistent/schools.shp")
    assert infra_utils.fetch_local_critical_infra(*TEST_BBOX) == []


def test_fetch_local_power_lines_missing_file_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(infra_utils, "POWER_LINES_SHP", "/nonexistent/power.shp")
    assert infra_utils.fetch_local_power_lines(*TEST_BBOX) == []


# --------------------------------------------------------------------------
# Local strategic infrastructure (airports/railway stations/sea ports) --
# a new risk category, not present in the original live-Overpass version
# at all.
# --------------------------------------------------------------------------
_has_airports = os.path.exists(infra_utils.AIRPORTS_SHP)
_has_rail_stations = os.path.exists(infra_utils.RAILWAY_STATIONS_SHP)
_has_sea_ports = os.path.exists(infra_utils.SEA_PORTS_SHP)

# A real, known-good bbox around Karachi -- confirmed during development
# to contain a real airport (Jinnah International), real railway
# stations, AND the real Karachi sea port all at once, exercising all
# three real local sources in a single test.
KARACHI_TEST_BBOX = (66.8, 24.7, 67.2, 25.0)


@pytest.mark.skipif(not (_has_airports and _has_rail_stations and _has_sea_ports),
                     reason="local Airports/Railway-stations/Sea-ports shapefiles not present on this machine")
def test_fetch_local_strategic_infra_finds_all_three_real_source_types():
    strategic = infra_utils.fetch_local_strategic_infra(*KARACHI_TEST_BBOX)
    types = {s["type"] for s in strategic}
    assert types == {"airport", "railway_station", "sea_port"}
    names = {s["name"] for s in strategic}
    assert "Jinnah International Airport" in names
    assert any("KARACHI" in n.upper() for n in names)  # the real sea port


@pytest.mark.skipif(not (_has_airports and _has_rail_stations and _has_sea_ports),
                     reason="local Airports/Railway-stations/Sea-ports shapefiles not present on this machine")
def test_fetch_local_strategic_infra_schema_matches_critical_infra_shape():
    """Real, load-bearing check: infra_utils.assess_infrastructure_risk
    is generic over any category's features, requiring only a real
    `centroid` -- confirming the schema here matches what that function
    (and the Mapbox 3D scene) already expects from every other category."""
    strategic = infra_utils.fetch_local_strategic_infra(*KARACHI_TEST_BBOX)
    s = strategic[0]
    assert {"id", "name", "type", "centroid"} <= set(s.keys())
    assert isinstance(s["centroid"], tuple) and len(s["centroid"]) == 2


def test_fetch_local_strategic_infra_sea_port_reprojected_to_real_wgs84_coords():
    """Sea_ports.shp's own real CRS is Web Mercator (metres), confirmed
    directly from its .prj -- a real, load-bearing reprojection bug here
    would put the reported centroid literally thousands of km off (raw
    Mercator coordinates, not lon/lat), so this checks the real Karachi
    sea port's returned centroid is a real, plausible WGS84 lon/lat
    instead."""
    if not _has_sea_ports:
        pytest.skip("local Sea-ports shapefile not present on this machine")
    strategic = infra_utils.fetch_local_strategic_infra(*KARACHI_TEST_BBOX)
    ports = [s for s in strategic if s["type"] == "sea_port"]
    assert len(ports) >= 1
    lon, lat = ports[0]["centroid"]
    assert 60 < lon < 78 and 20 < lat < 38  # real Pakistan's own real lon/lat range


def test_fetch_local_strategic_infra_missing_files_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(infra_utils, "AIRPORTS_SHP", "/nonexistent/airports.shp")
    monkeypatch.setattr(infra_utils, "RAILWAY_STATIONS_SHP", "/nonexistent/rail.shp")
    monkeypatch.setattr(infra_utils, "SEA_PORTS_SHP", "/nonexistent/ports.shp")
    assert infra_utils.fetch_local_strategic_infra(*TEST_BBOX) == []


# --------------------------------------------------------------------------
# Local railway LINES (distinct from the point-only railway stations
# already covered by fetch_local_strategic_infra above) -- deferred when
# strategic infra was first built, added once the same real, already-
# indexed local shapefile was confirmed to exist right alongside stations.
# --------------------------------------------------------------------------
_has_railway_lines = os.path.exists(infra_utils.RAILWAY_LINES_SHP)

# A real bbox around Karachi known (during development) to contain real,
# NAMED railway line segments (the real Karachi-Lahore-Peshawar Main Line),
# not just unnamed ones -- exercises the real `name` field, not just
# geometry.
KARACHI_RAILWAY_LINE_BBOX = (66.8, 24.7, 67.2, 25.0)


@pytest.mark.skipif(not _has_railway_lines, reason="local Railway_lines shapefile not present on this machine")
def test_fetch_local_railway_lines_returns_real_named_line():
    lines = infra_utils.fetch_local_railway_lines(*KARACHI_RAILWAY_LINE_BBOX)
    assert len(lines) > 0
    names = {l["name"] for l in lines}
    assert any("ریلوے" in n for n in names)  # real Urdu-script name, confirmed present in this bbox


@pytest.mark.skipif(not _has_railway_lines, reason="local Railway_lines shapefile not present on this machine")
def test_fetch_local_railway_lines_schema_matches_power_lines_shape():
    """Same real generic-risk-category schema as fetch_local_power_lines
    (assess_infrastructure_risk needs only `centroid`, confirmed generic
    over any category)."""
    lines = infra_utils.fetch_local_railway_lines(*KARACHI_RAILWAY_LINE_BBOX)
    line = lines[0]
    assert {"id", "name", "type", "centroid", "geometry"} <= set(line.keys())
    assert isinstance(line["centroid"], tuple) and len(line["centroid"]) == 2
    assert len(line["geometry"]) >= 2


@pytest.mark.skipif(not _has_railway_lines, reason="local Railway_lines shapefile not present on this machine")
def test_fetch_local_railway_lines_explodes_multilinestring_into_real_parts():
    """Real, load-bearing check: unlike roads/power lines (always plain
    LineString), a real fraction of this dataset's features are
    MultiLineString -- must be exploded into one entry per real part, not
    silently dropped by a stricter LineString-only filter."""
    lines = infra_utils.fetch_local_railway_lines(72.9, 33.5, 73.2, 33.8)
    assert len(lines) > 0
    # every returned entry is itself a real, single, simple line (>=2 pts)
    for l in lines:
        assert len(l["geometry"]) >= 2


def test_fetch_local_railway_lines_missing_file_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(infra_utils, "RAILWAY_LINES_SHP", "/nonexistent/railway_lines.shp")
    assert infra_utils.fetch_local_railway_lines(*TEST_BBOX) == []


@pytest.mark.skipif(not _has_railway_lines, reason="local Railway_lines shapefile not present on this machine")
def test_fetch_osm_infrastructure_includes_real_railway_category(monkeypatch):
    """Real end-to-end check that the new category actually reaches
    fetch_osm_infrastructure's own combined dict, not just the standalone
    fetch function."""
    def _no_overpass(query, timeout=40):
        return []
    monkeypatch.setattr(infra_utils, "query_overpass", _no_overpass)
    infra = infra_utils.fetch_osm_infrastructure(*KARACHI_RAILWAY_LINE_BBOX)
    assert "railway" in infra
    assert len(infra["railway"]) > 0


def test_road_detail_splits_major_local_counts_bridges_and_length():
    import numpy as np
    import infra_utils as iu
    hdr = {"ncols": 4, "nrows": 4, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 1.0}
    depth = np.zeros((4, 4)); depth[:, 0] = 1.0                                  # only the first column is wet
    ident = lambda lon, lat: (lon, lat)
    roads = [
        {"id": 1, "highway": "primary", "centroid": (0.5, 1.5), "geometry": [(0.1, 1.5), (0.9, 1.5)], "bridge": True},
        {"id": 2, "highway": "residential", "centroid": (0.5, 2.5), "geometry": [(0.1, 2.5), (0.9, 2.5)], "bridge": False},
        {"id": 3, "highway": "secondary", "centroid": (2.5, 1.5), "geometry": [(2.1, 1.5), (2.9, 1.5)], "bridge": True},
    ]
    r = iu.assess_infrastructure_risk({"roads": roads}, depth, None, hdr, ident)["roads"]["road_detail"]
    assert r["major"]["in_domain"] == 2 and r["major"]["flooded"] == 1 and r["local"]["flooded"] == 1
    assert r["bridges"] == {"in_domain": 2, "flooded": 1}
    assert 0 < r["flooded_length_km"] < r["total_length_km"]
    assert r["major"]["flooded_km"] > 0 and r["local"]["flooded_km"] > 0
