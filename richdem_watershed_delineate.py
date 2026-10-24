#!/usr/bin/env python3
"""
Delineates the watershed draining to Bahawalpur city center using simple
steepest-descent D8 on the RichDEM-filled DEM (no local pits, so D8 is
well-defined everywhere), then compares its cell count to the naive 10km
square crop used earlier.
"""
import time
from collections import deque

import numpy as np
from pyproj import Transformer

header = {}
with open("/home/aliyan/flood/richdem_header.txt") as f:
    for line in f:
        k, v = line.split()
        header[k] = float(v)
ncols, nrows = int(header['ncols']), int(header['nrows'])
xll, yll, cs = header['xllcorner'], header['yllcorner'], header['cellsize']
nodata = header['NODATA_value']

filled = np.load("/home/aliyan/flood/richdem_filled_dem.npy")
print(f"Loaded filled DEM: {filled.shape}")

valid = filled != nodata

# --- vectorized steepest-descent D8 direction ---
t0 = time.time()
BIG = 1e9
padded = np.where(valid, filled, BIG).astype('float64')
padded = np.pad(padded, 1, constant_values=BIG)

offsets = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
dists = [np.sqrt(2),1,np.sqrt(2),1,1,np.sqrt(2),1,np.sqrt(2)]
center = padded[1:-1, 1:-1]

best_drop = np.full(center.shape, -np.inf)
best_dir = np.full(center.shape, -1, dtype='int8')
for k, ((dy, dx), dist) in enumerate(zip(offsets, dists)):
    neighbor = padded[1+dy:1+dy+nrows, 1+dx:1+dx+ncols]
    drop = (center - neighbor) / dist
    better = drop > best_drop
    best_drop = np.where(better, drop, best_drop)
    best_dir = np.where(better, k, best_dir)

print(f"D8 direction computed in {time.time()-t0:.1f}s")

# --- build downstream index map ---
downstream = np.full(nrows * ncols, -1, dtype='int64')
ii, jj = np.meshgrid(np.arange(nrows), np.arange(ncols), indexing='ij')
flat_idx = ii * ncols + jj
for k, (dy, dx) in enumerate(offsets):
    mask = (best_dir == k) & valid
    ni, nj = ii + dy, jj + dx
    inb = (ni >= 0) & (ni < nrows) & (nj >= 0) & (nj < ncols)
    m = mask & inb
    downstream[flat_idx[m]] = (ni[m] * ncols + nj[m])

# --- pour point: Bahawalpur city center ---
t = Transformer.from_crs('EPSG:4326', 'EPSG:32642', always_xy=True)
cx, cy = t.transform(71.6836, 29.3956)
top_y = yll + nrows * cs
pour_col = int(round((cx - xll) / cs))
pour_row = int(round((top_y - cy) / cs))
pour_idx = pour_row * ncols + pour_col
print(f"Pour point pixel: row={pour_row}, col={pour_col}")

# --- reverse BFS: find all cells whose flow eventually reaches pour_idx ---
# Build a CSR-style reverse adjacency (vectorized) instead of a 57M-element
# Python list-of-lists, which would be far too slow/memory-heavy.
t1 = time.time()
has_edge = downstream != -1
sources = np.nonzero(has_edge)[0]
targets = downstream[has_edge]
order = np.argsort(targets, kind='stable')
sorted_targets = targets[order]
sorted_sources = sources[order]
unique_targets, start_idx = np.unique(sorted_targets, return_index=True)
end_idx = np.append(start_idx[1:], len(sorted_targets))
target_to_range = dict(zip(unique_targets.tolist(), zip(start_idx.tolist(), end_idx.tolist())))
print(f"Reverse adjacency built in {time.time()-t1:.1f}s")

t2 = time.time()
visited = np.zeros(nrows * ncols, dtype=bool)
q = deque([pour_idx])
visited[pour_idx] = True
while q:
    cur = q.popleft()
    rng = target_to_range.get(cur)
    if rng is None:
        continue
    s, e = rng
    for u in sorted_sources[s:e]:
        if not visited[u]:
            visited[u] = True
            q.append(int(u))
print(f"Watershed BFS traversal done in {time.time()-t2:.1f}s")

watershed_cells = int(visited.sum())
watershed_km2 = watershed_cells * (cs * cs) / 1e6
square_crop_cells = 340 * 340
square_crop_km2 = square_crop_cells * (cs * cs) / 1e6

print(f"\nWatershed draining to Bahawalpur pour point: {watershed_cells} cells ({watershed_km2:.2f} km^2)")
print(f"Naive square crop used earlier:               {square_crop_cells} cells ({square_crop_km2:.2f} km^2)")
print(f"Ratio (watershed / square crop):              {watershed_cells/square_crop_cells:.3f}x")

np.save("/home/aliyan/flood/richdem_watershed_mask.npy", visited.reshape(nrows, ncols))
