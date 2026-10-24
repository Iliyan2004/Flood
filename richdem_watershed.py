#!/usr/bin/env python3
"""
Uses RichDEM to (1) condition the Bahawalpur DEM (fill depressions), (2)
compute D8 flow accumulation to see the real drainage network, and (3)
delineate the actual watershed draining to Bahawalpur city center, to check
whether that gives a smaller/smarter simulation domain than the naive square
crop used earlier.
"""
import time
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import richdem as rd

DEM_PATH = "/home/aliyan/DGX-Shared/iliyan-llm/flood/DEM_FABDEM_30m_32642.asc"

t0 = time.time()
dem = rd.LoadGDAL(DEM_PATH) if False else None  # LoadGDAL needs GDAL driver; use manual load instead
print("Loading DEM (full file, 57.4M cells)...")

# Manual ARC-ASCII load into an rdarray (avoids needing GDAL bindings)
with open(DEM_PATH) as f:
    header = {}
    for _ in range(6):
        k, v = f.readline().split()
        header[k] = float(v)
    ncols, nrows = int(header['ncols']), int(header['nrows'])
    nodata = header['NODATA_value']
    arr = np.loadtxt(f, dtype='float32')
print(f"Loaded {ncols}x{nrows} = {ncols*nrows/1e6:.1f}M cells in {time.time()-t0:.1f}s")

dem_rd = rd.rdarray(arr, no_data=nodata)
dem_rd.geotransform = [header['xllcorner'], header['cellsize'], 0,
                        header['yllcorner'] + nrows * header['cellsize'], 0, -header['cellsize']]

t1 = time.time()
print("Filling depressions...")
filled = rd.FillDepressions(dem_rd, epsilon=False, in_place=False)
print(f"  done in {time.time()-t1:.1f}s")

t2 = time.time()
print("Computing D8 flow accumulation...")
accum = rd.FlowAccumulation(filled, method='D8')
print(f"  done in {time.time()-t2:.1f}s")

np.save("/home/aliyan/flood/richdem_accum.npy", np.array(accum))
np.save("/home/aliyan/flood/richdem_filled_dem.npy", np.array(filled))
with open("/home/aliyan/flood/richdem_header.txt", "w") as f:
    for k, v in header.items():
        f.write(f"{k} {v}\n")

print(f"\nTotal RichDEM processing time: {time.time()-t0:.1f}s for {ncols*nrows/1e6:.1f}M cells")
print(f"Max flow accumulation (main channel signature): {np.array(accum).max():.0f} cells")
