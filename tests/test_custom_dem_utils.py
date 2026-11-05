import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import custom_dem_utils as c


def _write(path, data, crs, x0, y0, res, nodata=-9999.0):
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1, dtype="float32",
                       crs=crs, transform=from_origin(x0, y0, res, res), nodata=nodata) as d:
        d.write(data.astype("float32"), 1)


def test_geographic_dem_is_reprojected_to_metres(tmp_path):
    p = str(tmp_path / "geo.tif")
    _write(p, np.random.rand(60, 60) * 20 + 500, "EPSG:4326", 73.0, 33.7, 0.0003)   # ~30 m
    info = c.prepare_upload(p)
    assert info["reprojected"] and info["crs"].startswith("EPSG:326")
    assert 20 < info["cellsize_m"] < 40
    assert c.path_for(info["dem_id"]) is not None


def test_projected_metre_dem_kept_as_is_and_hash_is_stable(tmp_path):
    p = str(tmp_path / "utm.tif")
    _write(p, np.random.RandomState(1).rand(50, 50) * 10 + 300, "EPSG:32643", 400000, 3700000, 10.0)
    a = c.prepare_upload(p)
    b = c.prepare_upload(p)
    assert not a["reprojected"] and a["cellsize_m"] == 10.0
    assert a["dem_id"] == b["dem_id"]


def test_rejects_missing_crs_bad_units_and_empty(tmp_path):
    p = str(tmp_path / "nocrs.tif")
    with rasterio.open(p, "w", driver="GTiff", height=5, width=5, count=1, dtype="float32") as d:
        d.write(np.ones((5, 5), "float32"), 1)
    with pytest.raises(c.CustomDemError, match="coordinate system"):
        c.prepare_upload(p)
    q = str(tmp_path / "cm.tif")
    _write(q, np.full((40, 40), 50000.0), "EPSG:32643", 400000, 3700000, 10.0)   # centimetres by mistake
    with pytest.raises(c.CustomDemError, match="units"):
        c.prepare_upload(q)
    r = str(tmp_path / "empty.tif")
    _write(r, np.full((40, 40), -9999.0), "EPSG:32643", 400000, 3700000, 10.0)
    with pytest.raises(c.CustomDemError, match="elevation values"):
        c.prepare_upload(r)


def test_rejects_unusable_cellsize(tmp_path):
    p = str(tmp_path / "coarse.tif")
    _write(p, np.random.rand(20, 20) * 10 + 100, "EPSG:32643", 400000, 3700000, 2000.0)
    with pytest.raises(c.CustomDemError, match="cell size"):
        c.prepare_upload(p)


def test_covers_bbox_and_bad_ids(tmp_path):
    p = str(tmp_path / "utm.tif")
    _write(p, np.random.rand(50, 50) * 10 + 300, "EPSG:32643", 400000, 3700000, 100.0)
    info = c.prepare_upload(p)
    path = c.path_for(info["dem_id"])
    b = info["bounds_wgs84"]
    inner = (b[0] + (b[2] - b[0]) * 0.3, b[1] + (b[3] - b[1]) * 0.3, b[0] + (b[2] - b[0]) * 0.6, b[1] + (b[3] - b[1]) * 0.6)
    assert c.covers_bbox(path, inner)
    assert not c.covers_bbox(path, (b[0] - 1, b[1], b[2], b[3]))
    assert c.path_for("../../etc/passwd") is None and c.path_for("zzzz") is None and c.path_for(None) is None
