import sys, os, json, time
import numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
ANUGA_PY = True
import anuga
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree

def read_asc(p):
    hdr = {}
    with open(p) as f:
        for _ in range(6):
            k, v = f.readline().split(); hdr[k.lower()] = float(v)
    z = np.loadtxt(p, skiprows=6)
    return hdr, z

def run(name, mesh_m=90.0, peak=10.5625, hours=48, total=50):
    d = os.path.expanduser(f"~/flood/cmp/{name}")
    h, z = read_asc(os.path.join(d, "aoi.dem.ascii"))
    nodata = h["nodata_value"]; cs = h["cellsize"]; nr, nc = z.shape
    zz = np.where(z == nodata, np.nan, z)
    if np.isnan(zz).any():
        zz = np.where(np.isnan(zz), np.nanmean(zz), zz)
    xs = (np.arange(nc) + 0.5) * cs; ys = (np.arange(nr) + 0.5) * cs   # y measured from south edge
    zf = zz[::-1]                                                        # row 0 = south
    interp = RegularGridInterpolator((ys, xs), zf, bounds_error=False, fill_value=None)
    W, H = nc * cs, nr * cs
    m, n = int(round(W / mesh_m)), int(round(H / mesh_m))
    dom = anuga.rectangular_cross_domain(m, n, len1=W, len2=H)
    dom.set_name("anuga_" + name); dom.set_datadir(d); dom.set_store(False)
    def elev(x, y): return interp(np.column_stack([y, x]))
    dom.set_quantity("elevation", elev, location="centroids")
    dom.set_quantity("friction", 0.05); dom.set_quantity("stage", expression="elevation")
    R = anuga.Reflective_boundary(dom)
    dom.set_boundary({"left": R, "right": R, "top": R, "bottom": R})
    dom.set_flow_algorithm("DE0")
    def rate(t):                       # triangular storm, mm/hr -> m/s
        th = t / 3600.0
        mm = peak * (th / (hours / 2) if th <= hours / 2 else max(0.0, 2 - th / (hours / 2))) if th <= hours else 0.0
        return mm / 1000.0 / 3600.0
    anuga.Rate_operator(dom, rate=rate)
    maxd = np.zeros(len(dom.centroid_coordinates))
    t0 = time.time()
    for t in dom.evolve(yieldstep=600, finaltime=total * 3600):
        hgt = dom.quantities["stage"].centroid_values - dom.quantities["elevation"].centroid_values
        maxd = np.maximum(maxd, hgt)
    rt = time.time() - t0
    cc = dom.centroid_coordinates
    tree = cKDTree(cc)
    gx, gy = np.meshgrid(xs, ys); _, idx = tree.query(np.column_stack([gx.ravel(), gy.ravel()]))
    grid_s = maxd[idx].reshape(nr, nc)
    grid = grid_s[::-1]                                                  # back to north-up rows
    np.save(os.path.join(d, "anuga_maxdepth.npy"), grid)
    return grid, rt, len(maxd)

if __name__ == "__main__":
    name = sys.argv[1]
    g, rt, ntri = run(name)
    print(json.dumps({"area": name, "runtime_s": round(rt, 1), "triangles": int(ntri), "max_depth_m": float(g.max())}))
