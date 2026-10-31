import sys, os, json, subprocess, time
import numpy as np
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
SF = os.path.expanduser("~/flood/cmp/sfincs_bin/bin/sfincs")   # Deltares SFINCS (flood model); NOT the conda-forge "sfincs", which is a stellarator plasma code

def read_asc(p):
    hdr = {}
    with open(p) as f:
        for _ in range(6):
            k, v = f.readline().split(); hdr[k.lower()] = float(v)
    return hdr, np.loadtxt(p, skiprows=6)

def run(name, peak=10.5625, hours=48, total=50, dem_override=None):
    d = os.path.expanduser(f"~/flood/cmp/{name}"); wd = os.path.join(d, "sfincs"); os.makedirs(wd, exist_ok=True)
    h, z = read_asc(dem_override or os.path.join(d, "aoi.dem.ascii"))
    nodata = h["nodata_value"]; nr, nc = z.shape
    valid = z != nodata
    zz = np.where(valid, z, np.nan)
    if np.isnan(zz).any(): zz = np.where(np.isnan(zz), np.nanmean(zz), zz)
    zs = zz[::-1]                      # row 0 = south, first row written = n=1
    np.savetxt(os.path.join(wd, "sfincs.dep"), zs, fmt="%.3f")
    np.savetxt(os.path.join(wd, "sfincs.msk"), np.ones_like(zs, dtype=int), fmt="%d")
    # triangular storm: mm/hr vs seconds
    ts = np.arange(0, total * 3600 + 1, 1800.0)
    th = ts / 3600.0
    r = np.where(th <= hours / 2, peak * th / (hours / 2), np.where(th <= hours, peak * (2 - th / (hours / 2)), 0.0))
    np.savetxt(os.path.join(wd, "sfincs.prcp"), np.column_stack([ts, r]), fmt="%.1f %.4f")
    inp = f"""mmax = {nc}
nmax = {nr}
dx = {h['cellsize']}
dy = {h['cellsize']}
x0 = {h['xllcorner']}
y0 = {h['yllcorner']}
rotation = 0
epsg = 32642
tref = 20220817 000000
tstart = 20220817 000000
tstop = 20220819 020000
dtmapout = 180000
dtmaxout = 180000
dtrstout = 0
dthisout = 600
inputformat = asc
outputformat = net
depfile = sfincs.dep
mskfile = sfincs.msk
prcfile = sfincs.prcp
manning = 0.05
advection = 1
"""
    open(os.path.join(wd, "sfincs.inp"), "w").write(inp)
    t0 = time.time()
    p = subprocess.run([SF], cwd=wd, capture_output=True, text=True)
    rt = time.time() - t0
    open(os.path.join(wd, "run.log"), "w").write(p.stdout + p.stderr)
    import netCDF4
    ds = netCDF4.Dataset(os.path.join(wd, "sfincs_map.nc"))
    zmax = np.ma.filled(ds["zsmax"][:], np.nan); zb = np.ma.filled(ds["zb"][:], np.nan)
    if zmax.ndim == 3: zmax = np.nanmax(zmax, axis=0)
    depth = np.nan_to_num(zmax - zb, nan=0.0)
    # orientation check vs input (zb should equal south-up DEM)
    err = float(np.nanmax(np.abs(zb - zs))) if zb.shape == zs.shape else float("nan")
    grid = depth[::-1]
    np.save(os.path.join(d, "sfincs_maxdepth.npy"), grid)
    return rt, err, float(grid.max()), p.returncode

if __name__ == "__main__":
    rt, err, mx, rc = run(sys.argv[1])
    print(json.dumps({"area": sys.argv[1], "runtime_s": round(rt, 1), "zb_vs_input_max_err": err, "max_depth_m": mx, "rc": rc}))
