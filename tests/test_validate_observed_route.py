import io, json, os
import numpy as np
import fis_app

HDR = {"ncols": 4, "nrows": 4, "xllcorner": 500000.0, "yllcorner": 3000000.0, "cellsize": 100.0}


def _make_run(root, rid):
    d = root / rid; (d / "results").mkdir(parents=True)
    fis_app._write_ascii(str(d / "results" / "res.max"), HDR, np.array([[1.0] * 2 + [0.0] * 2] * 4))
    dem = np.full((4, 4), 10.0); dem[:, 3] = -9999
    fis_app._write_ascii(str(d / "dem_bare.ascii"), HDR, dem)
    (d / "run_meta.json").write_text(json.dumps({"src_crs": "EPSG:32642"}))


def test_validate_observed_route(tmp_path, monkeypatch):
    monkeypatch.setattr(fis_app, "FIS_CACHE", str(tmp_path))
    monkeypatch.setattr(fis_app, "UPLOADS", str(tmp_path))
    _make_run(tmp_path, "r1")
    import geopandas as gpd
    from shapely.geometry import box
    gp = tmp_path / "o.geojson"
    gpd.GeoDataFrame(geometry=[box(500000, 3000000, 500200, 3000400)], crs="EPSG:32642").to_file(gp, driver="GeoJSON")
    c = fis_app.app.test_client()
    r = c.post("/runs/r1/validate-observed", data={"file": (io.BytesIO(gp.read_bytes()), "o.geojson")},
               content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and j["CSI"] == 1.0 and j["scored_cells"] == 12
    assert c.post("/runs/missing/validate-observed").status_code == 404
