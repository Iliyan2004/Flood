"""One-off: 2003-2012 mean GloFAS discharge (dis24, m3/s) and runoff (rowe, mm/day) grids for Pakistan.
Used by melt_hydrograph_utils to pick the GloFAS cell that actually drains a basin (water balance)."""
import os, sys, time
import numpy as np, xarray as xr
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_paths
base = os.path.join(data_paths.SHARE, "7. GLOFAS Variables", "GLOFAS_Historical")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glofas_clim", "glofas_mean_2003_2012.npz")
res = {}
for sub, var, pfx in (("river_discharge", "dis24", "river_discharge_pak"), ("runoff_water", "rowe", "runoff_water_pak")):
    acc, n = None, 0
    for y in range(2003, 2013):
        t = time.time()
        with xr.open_dataset(os.path.join(base, sub, f"{pfx}_{y}.nc")) as ds:
            a = ds[var].values.astype(np.float64)
            lat, lon = ds.latitude.values, ds.longitude.values
        s = np.nansum(a, 0); c = np.isfinite(a).sum(0)
        acc = s if acc is None else acc + s; n = c if isinstance(n, int) else n + c
        print(var, y, f"{time.time()-t:.0f}s", flush=True)
    res[var] = (acc / np.maximum(n, 1)).astype(np.float32)
    res[var + "_lat"], res[var + "_lon"] = lat, lon
np.savez_compressed(out, **res)
print("wrote", out)
