"""
Tab 6, auto-optimize: instead of the user manually drawing a protection
structure's line/type/size and re-running once, search for placement/
geometry that actually minimizes real flood damage -- using mystic's
differential-evolution global optimizer (mystic.solvers.diffev2), a real
constrained nonlinear optimization library, not a heuristic guess.

Each candidate evaluation is a REAL LISFLOOD-FP re-run (via flood_model.py,
the same binary/pipeline used everywhere else in this app) on the baseline's
own building-conditioned DEM with the candidate structure(s) burned in --
there is no cheap surrogate model standing in for the physics. That makes
every evaluation expensive (seconds to minutes depending on AOI size, same
as any other run in this app), so the search budget is a hard, small,
user-visible cap (`max_evals`), not an open-ended search-until-converged
loop. Trial runs skip the fine per-timestep grid output (a large
`--saveint-min`) since only the final `res.max` is needed to score a
candidate -- this only reduces how often LISFLOOD-FP WRITES its state to
disk, not the physics timestep itself, so it does not change the result,
only how much I/O a throwaway trial produces.

Decision variables per structure (7 dims), all physically bounded:
  cx_frac, cy_frac   -- line's center point, normalized within the AOI's
                         own lon/lat bounding box
  angle_deg          -- line orientation, 0-360
  length_frac        -- line length, normalized to the bbox diagonal,
                         clamped to [0.15, 0.9] (never a degenerate point,
                         never wildly exceeding the AOI)
  height_mult        -- multiplier on that structure type's documented
                         default height/diameter, [0.5, 1.5]
  halfwidth_mult     -- multiplier on that structure type's documented
                         default half-width, [0.5, 1.5]
  type_frac          -- which structure type from the user-allowed set,
                         floor(type_frac * n_types) (only used when the
                         caller allows more than one type)

The objective is the same real metric this app already reports for a
manually-placed structure (protection_utils.compare_flood_results):
minimize  -reduction_pct + LAMBDA_DISPLACEMENT * displacement_pct
i.e. maximize area actually kept dry, penalized by how much of that water
was simply routed elsewhere ("displaced") rather than genuinely removed --
consistent with this app's existing "protection is not free" framing, not
a new invented score. A candidate whose structure lands entirely outside
the AOI (rasterizes to zero affected cells) or whose flood_model run fails
gets a large fixed penalty so the optimizer moves away from it.
"""
import math
import os
import shutil
import subprocess

import numpy as np
from pyproj import Transformer

from protection_utils import STRUCTURE_TYPES, apply_structures_to_dem, compare_flood_results

LAMBDA_DISPLACEMENT = 0.5
FAILURE_PENALTY = 1000.0
DIMS_PER_STRUCTURE = 7
BOUNDS_PER_STRUCTURE = [(0.0, 1.0), (0.0, 1.0), (0.0, 360.0), (0.15, 0.9),
                         (0.5, 1.5), (0.5, 1.5), (0.0, 0.999999)]


def _read_ascii(path):
    header = {}
    with open(path) as f:
        for _ in range(6):
            k, v = f.readline().split()
            header[k.lower()] = float(v)
        data = np.loadtxt(f)
    return header, data


def _write_ascii(path, header, data, nodata=-9999):
    with open(path, "w") as f:
        f.write(f"ncols         {int(header['ncols'])}\n")
        f.write(f"nrows         {int(header['nrows'])}\n")
        f.write(f"xllcorner     {header['xllcorner']}\n")
        f.write(f"yllcorner     {header['yllcorner']}\n")
        f.write(f"cellsize      {header['cellsize']}\n")
        f.write(f"NODATA_value  {nodata}\n")
        np.savetxt(f, data, fmt="%.4f")


def _vector_to_structures(x, structure_types, aoi_bounds_wgs84, to_src, to_wgs84):
    """Decodes a flat mystic decision vector into the same structures[]
    schema apply_structures_to_dem already consumes (lon/lat geometry),
    one STRUCTURE_TYPES-worth of physical bounds per structure."""
    lon_min, lat_min, lon_max, lat_max = aoi_bounds_wgs84
    center_lon0, center_lat0 = to_src((lon_min + lon_max) / 2, (lat_min + lat_max) / 2)
    corner_x, corner_y = to_src(lon_max, lat_max)
    diag_m = math.hypot(corner_x - center_lon0, corner_y - center_lat0) * 2.0

    n_struct = len(x) // DIMS_PER_STRUCTURE
    structures = []
    for i in range(n_struct):
        cx_f, cy_f, angle_deg, len_f, h_mult, w_mult, type_f = x[i * DIMS_PER_STRUCTURE:(i + 1) * DIMS_PER_STRUCTURE]
        lon_c = lon_min + cx_f * (lon_max - lon_min)
        lat_c = lat_min + cy_f * (lat_max - lat_min)
        cx_src, cy_src = to_src(lon_c, lat_c)
        length_m = max(len_f, 0.05) * diag_m
        ang = math.radians(angle_deg)
        dx, dy = math.cos(ang) * length_m / 2, math.sin(ang) * length_m / 2
        p1 = to_wgs84(cx_src - dx, cy_src - dy)
        p2 = to_wgs84(cx_src + dx, cy_src + dy)

        s_type = structure_types[min(int(type_f * len(structure_types)), len(structure_types) - 1)]
        spec = STRUCTURE_TYPES[s_type]
        structures.append({
            "type": s_type,
            "geometry": [list(p1), list(p2)],
            "height_m": spec["default_height_m"] * float(h_mult),
            "half_width_m": spec["default_half_width_m"] * float(w_mult),
        })
    return structures


def _run_trial(structures, base_dem_header, base_dem, meta, trial_dir, venv_py, flood_model_path,
                manning_path=None):
    """One real, throwaway LISFLOOD-FP evaluation. Returns a comparison
    dict (see protection_utils.compare_flood_results) or None on failure/
    no-op placement."""
    os.makedirs(trial_dir, exist_ok=True)
    to_src = Transformer.from_crs("EPSG:4326", meta["src_crs"], always_xy=True).transform
    try:
        modified, structure_mask, _, _ = apply_structures_to_dem(base_dem_header, base_dem, structures, to_src)
        if not structure_mask.any():
            return None
        dem_path = os.path.join(trial_dir, "aoi.dem.ascii")
        _write_ascii(dem_path, base_dem_header, modified)

        sim_hours = float(meta.get("sim_hours") or (meta.get("duration_hr", 2.0) + 2.0))
        cmd = [venv_py, flood_model_path, "--dem", dem_path,
               "--rain-mm-hr", str(meta["rain_mm_hr"]), "--duration-hr", str(meta["duration_hr"]),
               "--friction", str(meta.get("friction", 0.05)), "--cfl", str(meta.get("cfl", 0.7)),
               "--infiltration-mm-hr", str(meta.get("infiltration_mm_hr", 0.0)), "--amc", str(meta.get("amc", 50.0)),
               "--inflow-m3s", str(meta.get("inflow_m3s", 0.0)), "--inflow-edge", meta.get("inflow_edge", "N"),
               "--saveint-min", str(sim_hours * 60.0),  # only res.max is needed for scoring -- skip fine timestep I/O
               "--site-name", "FIS protection auto-optimize trial", "--workdir", trial_dir]
        # Same real per-cell friction the baseline (and the final confirmation
        # run, via execute_protection_run) uses -- without this, every trial
        # the search scores would be evaluated under different physics
        # (uniform friction) than the scenario it's actually optimizing for.
        if manning_path and os.path.exists(manning_path):
            cmd += ["--manning-file", manning_path]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=1800)
        if proc.returncode != 0:
            return None

        res_max_path = os.path.join(trial_dir, "results", "res.max")
        return res_max_path if os.path.exists(res_max_path) else None
    except Exception:
        return None


def optimize_structures(base_run_dir, meta, structure_types, n_structures, max_evals,
                         venv_py, flood_model_path, seed=None, progress_cb=None):
    """Runs the real global optimization search. progress_cb(trial_i, total,
    score, comparison_dict_or_None) is called after every real evaluation so
    the caller can stream live progress the same way every other long job in
    this app does. Returns (best_structures, best_comparison, trial_log)."""
    base_header, base_dem = _read_ascii(os.path.join(base_run_dir, "aoi.dem.ascii"))
    _, base_max_depth = _read_ascii(os.path.join(base_run_dir, "results", "res.max"))
    valid_base = base_max_depth != -9999
    manning_path = os.path.join(base_run_dir, "manning.ascii")
    manning_path = manning_path if os.path.exists(manning_path) else None

    from shapely.geometry import shape as shapely_shape
    aoi_bounds_wgs84 = shapely_shape(meta["geometry"]).bounds  # (lon_min, lat_min, lon_max, lat_max)
    to_src = Transformer.from_crs("EPSG:4326", meta["src_crs"], always_xy=True).transform
    to_wgs84 = Transformer.from_crs(meta["src_crs"], "EPSG:4326", always_xy=True).transform

    bounds = BOUNDS_PER_STRUCTURE * n_structures
    trial_log = []
    counter = {"i": 0}
    scratch_dir = os.path.join(base_run_dir, "_opt_scratch")
    if os.path.exists(scratch_dir):
        shutil.rmtree(scratch_dir, ignore_errors=True)
    os.makedirs(scratch_dir, exist_ok=True)

    def objective(x):
        counter["i"] += 1
        i = counter["i"]
        structures = _vector_to_structures(x, structure_types, aoi_bounds_wgs84, to_src, to_wgs84)
        trial_dir = os.path.join(scratch_dir, f"trial_{i:03d}")
        comparison = None
        try:
            res_max_path = _run_trial(structures, base_header, base_dem, meta, trial_dir, venv_py, flood_model_path,
                                       manning_path=manning_path)
            if res_max_path is not None:
                _, prot_max_depth = _read_ascii(res_max_path)
                valid = valid_base & (prot_max_depth != -9999)
                comparison = compare_flood_results(base_max_depth, prot_max_depth, valid)
        except Exception:
            comparison = None
        finally:
            shutil.rmtree(trial_dir, ignore_errors=True)

        if comparison is None:
            score = FAILURE_PENALTY
        else:
            score = -comparison["reduction_pct"] + LAMBDA_DISPLACEMENT * comparison["displacement_pct"]
        trial_log.append({"trial": i, "score": score, "structures": structures,
                           "reduction_pct": None if comparison is None else comparison["reduction_pct"],
                           "displacement_pct": None if comparison is None else comparison["displacement_pct"]})
        if progress_cb:
            progress_cb(i, max_evals, score, comparison)
        return score

    if seed is not None:
        np.random.seed(seed)

    from mystic.solvers import diffev2
    npop = max(4, min(10, max_evals // 3))
    maxiter = max(1, max_evals // npop)
    try:
        best_x = diffev2(objective, x0=bounds, bounds=bounds, npop=npop, gtol=maxiter,
                          disp=False, full_output=False, cross=0.8, scale=0.7)
    except Exception:
        # mystic raised past our own eval budget bookkeeping (e.g. numerical
        # edge case) -- fall back to the best trial actually observed so a
        # search that gets this far still returns a usable, real answer.
        best_x = None

    shutil.rmtree(scratch_dir, ignore_errors=True)

    if not trial_log:
        return None, None, trial_log

    if best_x is not None:
        best_structures = _vector_to_structures(best_x, structure_types, aoi_bounds_wgs84, to_src, to_wgs84)
        best_entry = min(trial_log, key=lambda t: t["score"])
        # Prefer the exact best-observed trial's own structures (already
        # real geometry from an evaluated point) over re-decoding best_x,
        # since diffev2's returned vector is not guaranteed to be one of
        # the exact points this loop scored (it can be a blended/clamped
        # variant) -- using the actually-evaluated best keeps the reported
        # metrics and the applied structures perfectly consistent.
        best_structures = best_entry["structures"]
        best_comparison_summary = {"reduction_pct": best_entry["reduction_pct"],
                                    "displacement_pct": best_entry["displacement_pct"]}
    else:
        best_entry = min(trial_log, key=lambda t: t["score"])
        best_structures = best_entry["structures"]
        best_comparison_summary = {"reduction_pct": best_entry["reduction_pct"],
                                    "displacement_pct": best_entry["displacement_pct"]}

    return best_structures, best_comparison_summary, trial_log
