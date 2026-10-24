import json
import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon
import islamabad_layers_utils as il

ISB = (73.00, 33.70, 73.10, 33.76)


@pytest.fixture()
def gpkg(tmp_path):
    p = str(tmp_path / "isb.gpkg")
    gpd.GeoDataFrame({"name": ["Fire Station 1", None]}, geometry=[Point(73.05, 33.72), Point(73.06, 33.73)], crs="EPSG:4326").to_file(p, layer="emergency_shelters_point", driver="GPKG")
    gpd.GeoDataFrame({"name": ["Plant"]}, geometry=[Polygon([(73.02, 33.71), (73.04, 33.71), (73.04, 33.73), (73.02, 33.73)])], crs="EPSG:4326").to_file(p, layer="fuel_chemical_polygon", driver="GPKG")
    gpd.GeoDataFrame({"name": ["Far away"]}, geometry=[Point(73.30, 33.60)], crs="EPSG:4326").to_file(p, layer="police_point", driver="GPKG")
    return p


def test_reads_points_and_polygon_centroids_into_categories(gpkg):
    r = il.fetch_islamabad_facilities(*ISB, path=gpkg)
    assert set(r) == {"shelters", "fuel_chemical"}                    # police point is outside the box; missing layers are skipped
    assert [f["name"] for f in r["shelters"]] == ["Fire Station 1", "shelters"]    # a missing name falls back to the category
    assert r["fuel_chemical"][0]["centroid"] == pytest.approx((73.03, 33.72))
    assert all(f["type"] == c for c, fs in r.items() for f in fs)
    json.dumps(r)


def test_nothing_outside_islamabad_and_nothing_without_the_file(gpkg, tmp_path):
    assert il.fetch_islamabad_facilities(67.0, 24.8, 67.1, 24.9, path=gpkg) == {}        # Karachi
    assert il.fetch_islamabad_facilities(*ISB, path=str(tmp_path / "missing.gpkg")) == {}


def test_islamabad_extent_check():
    assert il.intersects_islamabad(73.0, 33.7, 73.1, 33.8)
    assert not il.intersects_islamabad(74.0, 31.0, 74.1, 31.1)                           # Lahore
    assert il.intersects_islamabad(72.0, 33.0, 74.0, 34.0)                               # box that contains Islamabad


def test_waterways_are_never_a_risk_category():
    assert not any("waterway" in l or "canal" in l for ls in il.CATEGORIES.values() for l in ls)
