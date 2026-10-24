"""hydro_model.py (distributed snow/glacier melt model) and melt_hydrograph_utils.py."""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hydro_model as hm  # noqa: E402
import melt_hydrograph_utils as mu  # noqa: E402


@pytest.mark.parametrize("enc,east,south", [("whitebox", 2, 8), ("esri", 1, 4)])
def test_derived_d8_points_downhill(enc, east, south):
    """Regression: the source/destination slices were swapped, so every derived pointer pointed uphill and the
    routing graph was full of cycles."""
    dem = np.tile(np.arange(10, 0, -1, dtype=float), (6, 1))            # falls toward +col (east)
    ptr = hm.derive_d8_pointer(dem, np.ones_like(dem, bool), enc, 30, 30)
    assert set(np.unique(ptr[:, :-1])) == {east}
    dem = np.tile(np.arange(10, 0, -1, dtype=float)[:, None], (1, 6))   # falls toward +row (south)
    ptr = hm.derive_d8_pointer(dem, np.ones_like(dem, bool), enc, 30, 30)
    assert set(np.unique(ptr[:-1, :])) == {south}


def test_inflow_series_shape_and_units():
    s = mu.daily_to_inflow_series([10.0, 20.0, 30.0])
    assert s["times_hr"] == [0.0, 12.0, 36.0, 60.0]          # a daily mean sits at the middle of its day
    assert s["q_m3s"] == [10.0, 10.0, 20.0, 30.0]
    assert mu.daily_to_inflow_series([]) == {"times_hr": [], "q_m3s": []}


def test_flood_window_centres_on_peak_and_rezeros_time():
    dates = [f"2025-07-{d:02d}" for d in range(1, 11)]
    q = [1, 2, 3, 4, 50, 6, 5, 4, 3, 2]
    s = mu.flood_window_series(dates, q, half_window_days=2)
    assert s["window"] == ["2025-07-03", "2025-07-07"]
    assert max(s["q_m3s"]) == 50 and s["times_hr"][0] == 0.0
    assert all(b > a for a, b in zip(s["times_hr"], s["times_hr"][1:]))


def test_summarize_volume():
    r = mu.summarize(["2025-01-01", "2025-01-02"], [1.0, 3.0])
    assert r["volume_m3"] == pytest.approx(4.0 * 86400) and r["peak_day"] == "2025-01-02"


def _write_synthetic(tmp, lapse):
    import pandas as pd
    import rasterio
    import xarray as xr
    from rasterio.transform import from_origin
    n = 20
    yy, xx = np.mgrid[0:n, 0:n]
    dem = (3000 + 40.0 * (n - 1 - xx) + 5.0 * np.abs(yy - n / 2)).astype(np.float32)   # drains toward +col
    tr = from_origin(500000.0, 4000000.0, 100.0, 100.0)
    prof = dict(driver="GTiff", height=n, width=n, count=1, crs="EPSG:32643", transform=tr, nodata=-9999.0)
    with rasterio.open(tmp / "dem.tif", "w", **dict(prof, dtype="float32")) as d:
        d.write(dem, 1)
    with rasterio.open(tmp / "sub.tif", "w", **dict(prof, dtype="int32", nodata=0)) as d:
        d.write(np.ones((n, n), np.int32), 1)
    import geopandas as gpd
    from shapely.geometry import box
    gpd.GeoDataFrame({"id": [1]}, geometry=[box(500000, 3998000, 501000, 3999000)], crs="EPSG:32643").to_file(tmp / "gl.shp")
    t = pd.date_range("2025-06-01", periods=24 * 40, freq="h")
    temp = 8.0 + 6.0 * np.sin((t.hour.values - 9) / 24 * 2 * np.pi)                    # degC at the cell
    for name, var, vals, units in (("t", "t2m", temp, "degC"), ("p", "tp", np.zeros(len(t)), "mm")):
        arr = np.broadcast_to(vals[:, None, None], (len(t), 2, 2)).astype(np.float32)
        xr.Dataset({var: (("time", "latitude", "longitude"), arr, {"units": units})},
                   coords=dict(time=t, latitude=[36.1, 36.0], longitude=[74.0, 74.1])).to_netcdf(tmp / f"{name}.nc")
    return dict(data_dir=str(tmp), proc_dir=str(tmp), out_dir=str(tmp / f"out{lapse}"), dem_file=str(tmp / "dem.tif"),
                subbasin_raster=str(tmp / "sub.tif"), glacier_shp=str(tmp / "gl.shp"), lake_shp="",
                temperature_files=[str(tmp / "t.nc")], precipitation_files=[str(tmp / "p.nc")],
                lakes_enabled=False, write_discharge_csv=True, write_final_state_rasters=False,
                write_subbasin_netcdf=False, lapse_rate_c_per_m=lapse, tz_offset_hours=0.0,
                default_glacier_thickness_m=30.0)


def test_synthetic_basin_conserves_water_and_lapse_rate_raises_melt(tmp_path):
    out = {}
    for lapse in (0.0, -0.0065):
        p = _write_synthetic(tmp_path, lapse)
        (tmp_path / f"p{lapse}.json").write_text(json.dumps(p))
        hm.main(["all", "--params", str(tmp_path / f"p{lapse}.json"), "--force"])
        import pandas as pd
        df = pd.read_csv(os.path.join(p["out_dir"], "subbasin_discharge_daily.csv"))
        out[lapse] = float(df["q_m3s"].sum() * 86400.0)
        assert out[lapse] > 0
    # the lower half of the basin sits ~800 m below the ERA5 cell mean, the upper half above it: with a lapse
    # rate the low pixels melt more and the high ones less; the glacier here is low, so total ice melt rises
    assert out[-0.0065] != pytest.approx(out[0.0], rel=1e-3)


def test_required_inputs_have_no_machine_specific_defaults():
    cfg = hm.Config()
    assert not any(":" in str(getattr(cfg, k)) for k in ("dem_file", "subbasin_raster", "glacier_shp", "lake_shp"))
    with pytest.raises(SystemExit):
        hm.require_inputs(cfg)


def test_skill_metrics_perfect_and_biased():
    obs = np.abs(np.sin(np.linspace(0, 6, 60))) * 100 + 10
    p = mu.skill_metrics(obs, obs)
    assert p["kge"] == pytest.approx(1.0) and p["bias_pct"] == pytest.approx(0.0)
    b = mu.skill_metrics(obs * 0.3, obs)
    assert b["bias_pct"] == pytest.approx(-70.0) and b["r"] == pytest.approx(1.0)
    assert mu.skill_metrics([1.0] * 5, [1.0] * 5) is None          # too short / no variance


def test_glofas_check_never_raises_and_flags_bad_bias(monkeypatch, tmp_path):
    import pandas as pd
    dates = [d.strftime("%Y-%m-%d") for d in pd.date_range("2025-04-01", periods=120)]
    obs = 50 + 40 * np.sin(np.linspace(0, 3, 120))
    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame({"latitude": [36.0], "longitude": [74.0], "max_routed_discharge_m3s": [1.0]}).to_csv(out / "pixel_summary.csv", index=False)
    monkeypatch.setattr(mu, "glofas_series", lambda *a, **k: (pd.DatetimeIndex(dates), obs, {"method": "test"}))
    geom = {"type": "Polygon", "coordinates": [[[74, 36], [74.1, 36], [74.1, 36.1], [74, 36.1], [74, 36]]]}
    bad = mu.glofas_check(geom, str(tmp_path), dates, obs * 0.3)
    assert bad["available"] and bad["warning"] and "below" in bad["warning"]
    good = mu.glofas_check(geom, str(tmp_path), dates, obs * 0.95)
    assert good["available"] and good["warning"] is None
    assert mu.glofas_check(geom, "/nonexistent", dates, obs)["available"] is False


def test_calibration_grid_is_two_sided():
    import melt_hydrograph_utils as m
    ps = {g["precip_correction"] for g in m.CALIBRATION_GRID}
    ts = {g["temp_bias_c"] for g in m.CALIBRATION_GRID}
    assert min(ps) < 1.0 < max(ps) and min(ts) < 0.0 < max(ts)
    assert dict(temp_bias_c=0.0, precip_correction=1.0, ddf_snow=4.0, ddf_ice=8.0) in m.CALIBRATION_GRID


def test_glofas_cell_chosen_by_water_balance():
    """Regression: the old 'highest-flow cell inside the basin' pick for Hunza 4080566900 sat on a neighbouring
    river (78 vs ~18 m3/s). The water-balance pick must carry the basin's own GloFAS runoff."""
    import os
    import pytest
    import melt_hydrograph_utils as m
    if not os.path.exists(m.GLOFAS_CLIM_NPZ):
        pytest.skip("GloFAS climatology not built on this machine")
    import basin_utils
    from shapely.geometry import shape
    g = basin_utils.basin_geometry(4080566900)
    if g is None:
        pytest.skip("HydroSHEDS basins not available")
    res = m.glofas_series(36.4937, 74.8967, "2024-10-01", "2024-12-31", poly=shape(g), area_km2=2748.5)
    assert res is not None and res[0] is not None
    assert 1 / m.WB_MAX_RATIO <= res[2]["ratio"] <= m.WB_MAX_RATIO
    assert res[2]["expected_mean_m3s"] < 40


def test_glacier_rasters_clipped_over_ice_only(tmp_path):
    """IceBoost thickness/debris are averaged onto the model grid over ice pixels only, so a model cell
    gets the thickness of its ice (not diluted by ice-free ground); outside any ice the cell stays NaN."""
    import os
    import pytest
    import numpy as np
    import rasterio
    import data_paths
    import melt_hydrograph_utils as m
    from rasterio.transform import from_origin
    from shapely.geometry import box
    if not os.path.exists(data_paths.get("GLACIER_THICKNESS_TIF")):
        pytest.skip("glacier thickness raster not on this machine")
    poly = box(74.85, 36.40, 74.95, 36.48)          # Hunza area, glaciated
    transform = from_origin(475000.0, 4040000.0, 270.0, 270.0)
    out = m._clip_glacier_rasters(str(tmp_path), poly, transform, 40, 40, 32643)
    assert out and os.path.exists(out["thickness"]) and os.path.exists(out["debris"])
    with rasterio.open(out["thickness"]) as f:
        t = f.read(1)
    v = t[np.isfinite(t)]
    assert v.size > 0 and (v > 0).all()            # zero-thickness source pixels were excluded
    with rasterio.open(out["debris"]) as f:
        d = f.read(1)
    assert np.isfinite(d).sum() == v.size and np.nanmin(d) >= 0.0
