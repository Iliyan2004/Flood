import json
import numpy as np
import pytest
import observed_validation_utils as ov

HDR = {"ncols": 10, "nrows": 10, "xllcorner": 500000.0, "yllcorner": 3000000.0, "cellsize": 100.0}


def test_score_hand_computed():
    m = np.zeros((4, 4), bool); o = np.zeros((4, 4), bool)
    m[0, :2] = True; o[0, 1:3] = True            # overlap 1, model-only 1, obs-only 1
    r = ov.score_extent(m, o, np.ones((4, 4), bool), 0.01)
    assert (r["hits"], r["misses"], r["false_alarms"]) == (1, 1, 1)
    assert r["CSI"] == round(1 / 3, 4) and r["POD"] == 0.5 and r["FAR"] == 0.5 and r["bias"] == 1.0


def test_cells_outside_valid_not_scored():
    m = np.ones((2, 2), bool); o = np.ones((2, 2), bool)
    v = np.array([[True, False], [False, False]])
    assert ov.score_extent(m, o, v, 1.0)["scored_cells"] == 1


def test_undefined_ratios_are_none():
    z = np.zeros((2, 2), bool)
    r = ov.score_extent(z, z, np.ones((2, 2), bool), 1.0)
    assert r["CSI"] is None and r["POD"] is None and r["FAR"] is None


def test_vector_polygon_rasterised_onto_grid(tmp_path):
    # EPSG:32642 square covering the left half of the grid, written as geojson in that CRS
    import geopandas as gpd
    from shapely.geometry import box
    g = gpd.GeoDataFrame(geometry=[box(500000, 3000000, 500500, 3001000)], crs="EPSG:32642")
    p = tmp_path / "obs.geojson"; g.to_file(p, driver="GeoJSON")
    wet, cov = ov.observed_mask_on_grid(str(p), HDR, "EPSG:32642")
    assert wet[:, :5].all() and not wet[:, 5:].any() and cov.all()


def test_raster_reprojected_and_nodata_ignored(tmp_path):
    import rasterio
    arr = np.zeros((10, 10), "float32"); arr[:, :3] = 1; arr[0, 9] = -1
    p = str(tmp_path / "o.tif")
    with rasterio.open(p, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32", crs="EPSG:32642",
                       transform=ov.grid_transform(HDR), nodata=-1) as d:
        d.write(arr, 1)
    wet, cov = ov.observed_mask_on_grid(p, HDR, "EPSG:32642")
    assert wet[:, :3].all() and wet.sum() == 30 and not cov[0, 9]


def test_unsupported_type_rejected(tmp_path):
    p = tmp_path / "x.csv"; p.write_text("a")
    with pytest.raises(ValueError):
        ov.observed_mask_on_grid(str(p), HDR, "EPSG:32642")
