"""Run the snow/glacier melt model on one HydroSHEDS basin and compare its routed outlet discharge with the
local GloFAS historical reanalysis (dis24) at the river cell next to the outlet.

    python validate_melt_vs_glofas.py HYBAS_ID START END [workdir]

GloFAS is itself a model, not observed discharge: agreement is a plausibility check on timing and magnitude,
not a calibration against gauges (none are on the shared drive for these basins).
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import xarray as xr

import basin_utils
import data_paths
import melt_hydrograph_utils as m

GLOFAS_DIR = os.path.join(data_paths.SHARE, "7. GLOFAS Variables", "GLOFAS_Historical", "river_discharge")


def glofas_series(lat, lon, start, end, radius_cells=1, poly=None):
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)
    parts = []
    for y in years:
        f = os.path.join(GLOFAS_DIR, f"river_discharge_pak_{y}.nc")
        if os.path.exists(f):
            parts.append(xr.open_dataset(f)["dis24"])
    if not parts:
        raise FileNotFoundError("no GloFAS yearly files for the period")
    da = xr.concat(parts, dim="valid_time").sel(valid_time=slice(start, end))
    ilat = int(np.abs(da.latitude.values - lat).argmin())
    ilon = int(np.abs(da.longitude.values - lon).argmin())
    win = da.isel(latitude=slice(max(ilat - radius_cells, 0), ilat + radius_cells + 1),
                  longitude=slice(max(ilon - radius_cells, 0), ilon + radius_cells + 1))
    mean = win.mean("valid_time")
    if poly is not None:
        # only cells whose centre lies INSIDE the basin: a cell just outside the outlet can sit on a
        # different (larger) river at a confluence and integrate far more catchment than the basin has
        from shapely.geometry import Point
        inside = np.array([[poly.contains(Point(float(lo), float(la))) for lo in win.longitude.values]
                           for la in win.latitude.values])
        if inside.any():
            mean = mean.where(inside)
    k = np.unravel_index(int(np.nanargmax(mean.values)), mean.shape)
    cell = win.isel(latitude=k[0], longitude=k[1])
    return (pd.DatetimeIndex(cell.valid_time.values), cell.values.astype(float),
            float(cell.latitude), float(cell.longitude))


def metrics(sim, obs):
    sim, obs = np.asarray(sim, float), np.asarray(obs, float)
    ok = np.isfinite(sim) & np.isfinite(obs)
    s, o = sim[ok], obs[ok]
    r = float(np.corrcoef(s, o)[0, 1])
    alpha, beta = s.std() / o.std(), s.mean() / o.mean()
    return dict(n=int(ok.sum()), r=r, nse=float(1 - ((s - o) ** 2).sum() / ((o - o.mean()) ** 2).sum()),
                kge=float(1 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2)),
                bias_pct=float(100 * (s.mean() - o.mean()) / o.mean()), sim_mean=float(s.mean()), obs_mean=float(o.mean()),
                sim_peak=float(s.max()), obs_peak=float(o.max()))


def main(hybas_id, start, end, workdir, params_extra=None):
    geom = basin_utils.basin_geometry(int(hybas_id))
    p = m.prepare_inputs(geom, start, end, workdir, params_extra=params_extra)
    m.run_model(workdir)
    dates, q = m.load_discharge(workdir)
    ps = pd.read_csv(os.path.join(workdir, "out", "pixel_summary.csv"))
    out = ps.loc[ps["max_routed_discharge_m3s"].idxmax()]
    from shapely.geometry import shape as _shape
    gi, gq, glat, glon = glofas_series(float(out["latitude"]), float(out["longitude"]), start, end,
                                       radius_cells=3, poly=_shape(geom))
    sim = pd.Series(q, index=pd.DatetimeIndex(dates))
    both = pd.concat([sim.rename("sim"), pd.Series(gq, index=gi).rename("glofas")], axis=1).dropna()
    # compare the melt season (Apr-Sep); winter flow is tiny in both
    season = both[(both.index.month >= 4) & (both.index.month <= 9)]
    res = dict(hybas_id=int(hybas_id), basin_area_km2=p["_meta"]["basin_area_km2"], meta=p["_meta"],
               outlet=[float(out["latitude"]), float(out["longitude"])], glofas_cell=[glat, glon],
               all_days=metrics(both["sim"], both["glofas"]), melt_season=metrics(season["sim"], season["glofas"]),
               summary=m.summarize(dates, q), limitations=m.LIMITATIONS)
    with open(os.path.join(workdir, "validation_vs_glofas.json"), "w") as f:
        json.dump(res, f, indent=1)
    both.to_csv(os.path.join(workdir, "sim_vs_glofas_daily.csv"))
    return res


if __name__ == "__main__":
    wd = sys.argv[4] if len(sys.argv) > 4 else f"/tmp/melt/{sys.argv[1]}"
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3], wd), indent=1))
