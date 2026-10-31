import sys, os, json, struct, subprocess, time, shutil
import numpy as np
from scipy.interpolate import RegularGridInterpolator
CMP = os.path.expanduser("~/flood/cmp")

def read_asc(p):
    hdr = {}
    with open(p) as f:
        for _ in range(6):
            k, v = f.readline().split(); hdr[k.lower()] = float(v)
    return hdr, np.loadtxt(p, skiprows=6)

def rec(b): return struct.pack(">i", len(b)) + b + struct.pack(">i", len(b))

def write_slf(path, x, y, ikle, zb, ipobo):
    npoin, nelem = len(x), len(ikle)
    out = rec(b"TELEMAC2D BASIN".ljust(72) + b"SERAFIN ")
    out += rec(struct.pack(">ii", 1, 0))
    out += rec(b"BOTTOM          " + b"M               ")
    out += rec(struct.pack(">10i", *([0] * 10)))
    out += rec(struct.pack(">5i", nelem, npoin, 3, 1, 0)[:16] if False else struct.pack(">4i", nelem, npoin, 3, 1))
    out += rec(np.asarray(ikle + 1, dtype=">i4").tobytes())
    out += rec(np.asarray(ipobo, dtype=">i4").tobytes())
    out += rec(np.asarray(x, dtype=">f4").tobytes()); out += rec(np.asarray(y, dtype=">f4").tobytes())
    out += rec(struct.pack(">f", 0.0)); out += rec(np.asarray(zb, dtype=">f4").tobytes())
    open(path, "wb").write(out)

def read_slf_depths(path):
    b = open(path, "rb").read(); pos = 0
    def nxt():
        nonlocal pos
        n = struct.unpack(">i", b[pos:pos + 4])[0]; d = b[pos + 4:pos + 4 + n]; pos += n + 8; return d
    nxt(); nb = struct.unpack(">2i", nxt())[0]
    names = [nxt()[:16].decode().strip() for _ in range(nb)]
    nxt(); ip = nxt()
    ne, npn, nd, _ = struct.unpack(">4i", nxt()); nxt(); nxt(); nxt(); nxt()
    hidx = [i for i, n in enumerate(names) if n.upper().startswith("WATER DEPTH")]
    hi = hidx[0] if hidx else None
    mx = np.zeros(npn); nt = 0
    while pos < len(b):
        nxt()
        for v in range(nb):
            a = np.frombuffer(nxt(), dtype=">f4")
            if v == hi: mx = np.maximum(mx, a)
        nt += 1
    return mx, names, nt

def run(name, mesh_m=90.0, hours=48, rain_total_mm=253.5):
    d = os.path.join(CMP, name); wd = os.path.join(d, "telemac"); shutil.rmtree(wd, ignore_errors=True); os.makedirs(wd)
    h, z = read_asc(os.path.join(d, "aoi.dem.ascii")); nd = h["nodata_value"]; cs = h["cellsize"]; nr, nc = z.shape
    zz = np.where(z == nd, np.nan, z); zz = np.where(np.isnan(zz), np.nanmean(zz), zz)
    xs = (np.arange(nc) + 0.5) * cs; ys = (np.arange(nr) + 0.5) * cs
    interp = RegularGridInterpolator((ys, xs), zz[::-1], bounds_error=False, fill_value=None)
    W, H = nc * cs, nr * cs; m, n = int(round(W / mesh_m)), int(round(H / mesh_m))
    gx = np.linspace(0, W, m + 1); gy = np.linspace(0, H, n + 1); X, Y = np.meshgrid(gx, gy)
    x, y = X.ravel(), Y.ravel(); zb = interp(np.column_stack([y, x]))
    idx = np.arange((m + 1) * (n + 1)).reshape(n + 1, m + 1)
    a, b_, c, dd = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    ikle = np.vstack([np.column_stack([a, b_, c]), np.column_stack([a, c, dd])])
    ring = list(idx[0, :-1]) + list(idx[:-1, -1]) + list(idx[-1, :0:-1]) + list(idx[:0:-1, 0])
    ipobo = np.zeros(len(x), dtype=int)
    for k, node in enumerate(ring, 1): ipobo[node] = k
    write_slf(os.path.join(wd, "geo.slf"), x, y, ikle, zb, ipobo)
    with open(os.path.join(wd, "geo.cli"), "w") as f:
        for k, node in enumerate(ring, 1):
            f.write(f"2 2 2 0.000 0.000 0.000 0.000 2 0.000 0.000 0.000 {node + 1} {k}\n")
    mm_day = rain_total_mm / hours * 24.0
    cas = f"""BOUNDARY CONDITIONS FILE : geo.cli
GEOMETRY FILE : geo.slf
RESULTS FILE : res.slf
TITLE : 'SAILAB CASE STUDY {name}'
VARIABLES FOR GRAPHIC PRINTOUTS : 'H'
EQUATIONS : 'SAINT-VENANT FE'
TIME STEP : 10.
DURATION : {hours * 3600}.
VARIABLE TIME-STEP : YES
DESIRED COURANT NUMBER : 0.9
GRAPHIC PRINTOUT PERIOD : 360
LISTING PRINTOUT PERIOD : 360
LAW OF BOTTOM FRICTION : 4
FRICTION COEFFICIENT : 0.05
RAIN OR EVAPORATION : YES
RAIN OR EVAPORATION IN MM PER DAY : {mm_day}
INITIAL CONDITIONS : 'ZERO DEPTH'
TIDAL FLATS : YES
OPTION FOR THE TREATMENT OF TIDAL FLATS : 1
TREATMENT OF NEGATIVE DEPTHS : 2
MINIMUM VALUE OF DEPTH : 1.E-3
TURBULENCE MODEL : 1
VELOCITY DIFFUSIVITY : 0.
SOLVER : 7
SOLVER ACCURACY : 1.E-6
"""
    open(os.path.join(wd, "t2d.cas"), "w").write(cas)
    env = dict(os.environ, HOMETEL=f"{CMP}/telemac_src", BUILD_DIR=f"{CMP}/telemac_src/build",
               SYSTELCFG=f"{CMP}/telemac_src/build/systel.cfg")
    env["PYTHONPATH"] = f"{CMP}/telemac_src/scripts/python3:" + env.get("PYTHONPATH", "")
    env["LD_LIBRARY_PATH"] = f"{CMP}/telemac_src/build/lib:" + env.get("LD_LIBRARY_PATH", "")
    t0 = time.time()
    p = subprocess.run([sys.executable, f"{CMP}/telemac_src/scripts/python3/telemac2d.py", "t2d.cas"], cwd=wd, env=env,
                       capture_output=True, text=True)
    rt = time.time() - t0
    open(os.path.join(wd, "run.log"), "w").write(p.stdout + p.stderr)
    if not os.path.exists(os.path.join(wd, "res.slf")):
        return {"error": (p.stdout + p.stderr)[-600:], "runtime_s": round(rt, 1)}
    mx, names, nt = read_slf_depths(os.path.join(wd, "res.slf"))
    tree = __import__("scipy.spatial", fromlist=["cKDTree"]).cKDTree(np.column_stack([x, y]))
    Gx, Gy = np.meshgrid(xs, ys); _, ii = tree.query(np.column_stack([Gx.ravel(), Gy.ravel()]))
    grid = mx[ii].reshape(nr, nc)[::-1]
    np.save(os.path.join(d, "telemac_maxdepth.npy"), grid)
    return {"area": name, "runtime_s": round(rt, 1), "max_depth_m": float(grid.max()), "frames": nt, "vars": names}

if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1])))
