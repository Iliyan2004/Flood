"""
Fast tests for polygon_utils.py's real shapefile-zip/geojson upload
handling. Real shapefiles are written to disk via geopandas itself
(a real, standard write path -- not fixture files depending on a
specific machine's own uploaded data) so these are self-contained.
"""
import os
import sys
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import polygon_utils  # noqa: E402

TEST_POLY = Polygon([(73.0, 33.6), (73.1, 33.6), (73.1, 33.7), (73.0, 33.7), (73.0, 33.6)])


def _write_real_shapefile(directory, name="test"):
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[TEST_POLY], crs="EPSG:4326")
    shp_path = os.path.join(directory, f"{name}.shp")
    gdf.to_file(shp_path)
    return shp_path


def _zip_dir_contents(src_dir, zip_path, prefix=""):
    with zipfile.ZipFile(zip_path, "w") as z:
        for fn in os.listdir(src_dir):
            z.write(os.path.join(src_dir, fn), arcname=os.path.join(prefix, fn) if prefix else fn)


def test_load_polygon_from_root_level_zip(tmp_path):
    """The already-working case: .shp/.dbf/... sitting at the zip's own
    top level (no subfolder) -- must keep working after the nested-zip
    fix below."""
    shp_dir = tmp_path / "shpfiles"
    shp_dir.mkdir()
    _write_real_shapefile(str(shp_dir))
    zip_path = str(tmp_path / "root.zip")
    _zip_dir_contents(str(shp_dir), zip_path)

    geom, area_km2 = polygon_utils.load_polygon_from_upload(zip_path)
    assert geom["type"] == "Polygon"
    assert area_km2 > 0


def test_load_polygon_from_nested_subfolder_zip(tmp_path):
    """Real, live-caught regression: a real, common zip export layout
    puts the shapefile inside a named subfolder within the zip (e.g.
    "MyShapefile/MyShapefile.shp") rather than at the zip root -- this
    must be found and loaded, not fail with a GDAL "not recognized"
    error the way the original code did."""
    shp_dir = tmp_path / "shpfiles"
    shp_dir.mkdir()
    _write_real_shapefile(str(shp_dir), name="Nested Area")
    zip_path = str(tmp_path / "nested.zip")
    _zip_dir_contents(str(shp_dir), zip_path, prefix="Nested Area Shapefile")

    geom, area_km2 = polygon_utils.load_polygon_from_upload(zip_path)
    assert geom["type"] == "Polygon"
    assert area_km2 > 0


def test_load_polygon_from_geojson_unaffected(tmp_path):
    import json
    path = str(tmp_path / "test.geojson")
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "Polygon", "coordinates": [list(TEST_POLY.exterior.coords)]}}]}, f)
    geom, area_km2 = polygon_utils.load_polygon_from_upload(path)
    assert geom["type"] == "Polygon"
    assert area_km2 > 0


def test_load_polygon_from_zip_with_no_shapefile_raises_clear_error(tmp_path):
    zip_path = str(tmp_path / "empty.zip")
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("readme.txt", "no shapefile here")
    with pytest.raises(ValueError, match="doesn't contain a real .shp"):
        polygon_utils.load_polygon_from_upload(zip_path)


def test_load_polygon_zip_area_matches_direct_shapefile_read(tmp_path):
    """Real regression check: the zip-loaded area must match reading the
    same real shapefile directly (not off by a reprojection/CRS bug
    introduced by the nested-path fix)."""
    shp_dir = tmp_path / "shpfiles"
    shp_dir.mkdir()
    shp_path = _write_real_shapefile(str(shp_dir))
    direct_geom, direct_area = polygon_utils.load_polygon_from_upload(shp_path)

    zip_path = str(tmp_path / "nested2.zip")
    _zip_dir_contents(str(shp_dir), zip_path, prefix="sub/folder")
    zip_geom, zip_area = polygon_utils.load_polygon_from_upload(zip_path)

    assert zip_area == pytest.approx(direct_area, rel=1e-6)
