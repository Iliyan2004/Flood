"""Pilot: build a training set of (terrain, storm) -> LISFLOOD-FP max depth, plus the current Instant estimate
for the same inputs. Each sample is a 128x128 window of the Pakistan SRTM at a random place, a random storm, and one
real solver run (GPU ACC, no buildings/roads). Resumable: re-run to add more samples."""
import json, os, subprocess, sys, tempfile, time, shutil
import numpy as np, rasterio
from rasterio.windows import Window
sys.path.insert(0, os.path.expanduser("~/flood"))
import hand_utils
from protection_utils import write_ascii_grid

SRTM = os.path.expanduser("~/DGX-Shared/3. Raster Data/DEM_SRTM/pakDEM/Pak_srtm.tif")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data2")
FAILLOG = os.path.join(OUT, "failures.jsonl")
VENV_PY = os.path.expanduser("~/flood/venv/bin/python")
FLOOD = os.path.expanduser("~/flood/flood_model.py")
N_TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 320
SIZE = 128
RUNOFF_C = 0.5   # what the Instant page assumes


def valid_cells(src, step=64):
    """Coarse map of where the raster really has elevation (most of its footprint lies outside Pakistan)."""
    a = src.read(1, out_shape=(src.height // step, src.width // step))
    ok = np.isfinite(a) & (a > -100) & (a < 7000)
    rr, cc = np.nonzero(ok)
    keep = (rr * step + SIZE < src.height) & (cc * step + SIZE < src.width)
    return rr[keep] * step, cc[keep] * step


def lhs_design(n, src, seed=2026):
    """Latin hypercube over (location along the valid territory, log rain, duration, log roughness, infiltration): each of the
    n strata of every variable is used once. Location is the rank among the valid terrain cells sorted by row then column."""
    from scipy.stats import qmc
    rr, cc = valid_cells(src)
    order = np.lexsort((cc, rr)); rr, cc = rr[order], cc[order]
    rng = np.random.default_rng(seed)
    u = qmc.LatinHypercube(d=5, seed=seed).random(n)
    loc = np.minimum((u[:, 0] * len(rr)).astype(int), len(rr) - 1)
    rows = np.clip(rr[loc] + rng.integers(0, 64, n), 0, src.height - SIZE); cols = np.clip(cc[loc] + rng.integers(0, 64, n), 0, src.width - SIZE)
    rain = np.exp(np.log(8) + u[:, 1] * (np.log(120) - np.log(8))); dur = 1 + u[:, 2] * 5
    fric = np.exp(np.log(0.02) + u[:, 3] * (np.log(0.15) - np.log(0.02))); infil = u[:, 4] * 20.0
    return rows.astype(int), cols.astype(int), rain, dur, fric, infil


def pick_window(src, rng, r0, c0):
    """The design point's own window if usable; otherwise the nearest usable one (jittered around it, up to ~45 km)."""
    for k in range(400):
        if k == 0:
            r, c = r0, c0
        else:
            r = int(np.clip(r0 + rng.integers(-1500, 1500), 0, src.height - SIZE)); c = int(np.clip(c0 + rng.integers(-1500, 1500), 0, src.width - SIZE))
        a = src.read(1, window=Window(c, r, SIZE, SIZE)).astype("float32")
        valid = np.isfinite(a) & (a > -100) & (a < 7000)
        if valid.mean() < 0.98:
            continue
        a = np.where(valid, a, np.nanmedian(a[valid]))
        if float(a.max() - a.min()) < 3.0:
            continue
        return r, c, a
    raise RuntimeError("no usable window")


def main():
    os.makedirs(OUT, exist_ok=True)
    src = rasterio.open(SRTM)
    rows, cols, rains, durs, frics, infils = lhs_design(N_TARGET, src)
    rng = np.random.default_rng(7)
    t0 = time.time(); made = 0; failed = 0
    for idx in range(N_TARGET):
        if os.path.exists(os.path.join(OUT, f"s{idx:04d}.npz")):
            continue
        try:
            r, c, dem = pick_window(src, rng, int(rows[idx]), int(cols[idx]))
        except RuntimeError:
            failed += 1; continue
        rain = float(rains[idx]); dur = float(durs[idx]); FRICTION = float(frics[idx]); INFIL = float(infils[idx])
        tmp = tempfile.mkdtemp(prefix="surr_", dir=OUT)
        try:
            xll = src.transform.c + c * src.res[0]; yll = src.transform.f - (r + SIZE) * src.res[1]
            header = {"ncols": SIZE, "nrows": SIZE, "xllcorner": xll, "yllcorner": yll, "cellsize": float(src.res[0])}
            dem_path = os.path.join(tmp, "dem.ascii"); write_ascii_grid(dem_path, header, dem)
            cmd = [VENV_PY, FLOOD, "--dem", dem_path, "--rain-mm-hr", f"{rain:.3f}", "--duration-hr", f"{dur:.3f}",
                   "--friction", f"{FRICTION:.4f}", "--cfl", "0.7", "--infiltration-mm-hr", f"{INFIL:.3f}", "--amc", "50",
                   "--saveint-min", "100000", "--site-name", "surrogate", "--workdir", tmp]
            with open(os.path.join(tmp, "run.log"), "w") as lf:
                p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=200)
            resmax = os.path.join(tmp, "results", "res.max")
            if p.returncode != 0 or not os.path.exists(resmax):
                failed += 1
                gy, gx = np.gradient(dem, float(src.res[0]))
                tail = open(os.path.join(tmp, "run.log")).read()[-600:]
                with open(FAILLOG, "a") as fl:
                    fl.write(json.dumps(dict(idx=idx, rc=p.returncode, rain=rain, dur=dur, fric=FRICTION, infil=INFIL, relief=float(dem.max() - dem.min()),
                                             mean_slope_pct=float(np.hypot(gx, gy).mean() * 100), max_slope_pct=float(np.hypot(gx, gy).max() * 100), tail=tail)) + "\n")
                continue
            depth = np.loadtxt(resmax, skiprows=6).astype("float32")
            factor = 1.0
            sj = os.path.join(tmp, "summary.json")
            if os.path.exists(sj):
                s = json.load(open(sj))
                if s.get("acc_depth_correction_applied"):
                    factor = float(s.get("acc_depth_correction_factor", 1.0)) or 1.0
            depth = depth / factor
            from rasterio.transform import from_origin
            tif = os.path.join(tmp, "dem.tif")
            with rasterio.open(tif, "w", driver="GTiff", height=SIZE, width=SIZE, count=1, dtype="float32", crs="EPSG:32642",
                               transform=from_origin(xll, yll + SIZE * header["cellsize"], header["cellsize"], header["cellsize"]),
                               nodata=-9999.0) as dst:
                dst.write(dem.astype("float32"), 1)
            hr = hand_utils.compute_hand(tif)
            area_km2 = (SIZE * header["cellsize"] / 1000) ** 2
            q = RUNOFF_C * rain * area_km2 / 3.6
            channel, _ = hand_utils.discharge_to_inundation(hr, q)
            ponding = hand_utils.pluvial_ponding_depth(hr, RUNOFF_C * rain * dur)
            base = np.maximum(channel, ponding).astype("float32")
            np.savez_compressed(os.path.join(OUT, f"s{idx:04d}.npz"), dem=dem, filled=np.asarray(hr["filled"], "float32"),
                                hand=np.asarray(hr["hand"], "float32"), acc=np.asarray(hr["accumulation"], "float32"), baseline=base,
                                truth=depth, rain=rain, dur=dur, friction=FRICTION, infil=INFIL, cell=float(header["cellsize"]), block=np.array([r // 2000, c // 2000]),
                                factor=factor)
            made += 1
            print(f"[{idx+1}/{N_TARGET}] rain {rain:.0f} dur {dur:.1f} max {depth.max():.2f} m wet {(depth>0.05).mean()*100:.0f}% "
                  f"elapsed {time.time()-t0:.0f}s failed {failed}", flush=True)
        except subprocess.TimeoutExpired:
            failed += 1
            with open(FAILLOG, "a") as fl:
                fl.write(json.dumps(dict(idx=idx, rc="timeout", rain=rain, dur=dur, fric=FRICTION, infil=INFIL, relief=float(dem.max() - dem.min()))) + "\n")
        except Exception as e:
            failed += 1; print("sample failed:", repr(e)[:200], flush=True)
            if "Rasterio" in repr(e):
                time.sleep(30); src = rasterio.open(SRTM)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("DONE", len([f for f in os.listdir(OUT) if f.endswith(".npz")]), "samples,", failed, "failed", flush=True)


if __name__ == "__main__":
    main()
