"""Calibrates a finished run's floodplain roughness and infiltration against an observed flood extent.

Two parameters are searched, because they are the ones flood results are most sensitive to besides terrain:
  * a roughness multiplier applied to every cell of the run's Manning's n grid (and to the fallback value), and
  * a flat infiltration rate in mm/hr.
Every candidate is a REAL solver run on the finished run's own terrain and boundary inputs (no surrogate
model), scored against the observed extent with the Critical Success Index (CSI). The search is
differential evolution (mystic) with a small, user-chosen run budget; the baseline settings are always scored
first so the improvement is measured against what the user already had.

Honest limits, repeated in every result: this fits two numbers to ONE observed event, so a good score on that
event is not evidence the parameters hold for another; keep a second event aside to check. The trial runs
use the finished run's rainfall (the uploaded series if it had one, otherwise the triangular storm) and do not
repeat storm-centering or areal reduction.
"""
import hashlib
import json
import os
import shutil
import subprocess
import traceback

import numpy as np

import observed_validation_utils as ov

MANNING_SCALE_RANGE = (0.4, 3.0)    # multiplier on the whole roughness grid
ABS_MIN_N = 0.01                    # same floor the solver wrapper enforces
WET_THRESHOLD_M = 0.05
LARGE_PENALTY = 1e6


def _read_ascii_grid(path):
    with open(path) as f:
        head = {}
        for _ in range(6):
            k, v = f.readline().split()
            head[k.lower()] = float(v)
        return head, np.loadtxt(f)


def _write_scaled_manning(src_path, dst_path, scale):
    head, grid = _read_ascii_grid(src_path)
    out = np.where(grid > 0, np.maximum(grid * scale, ABS_MIN_N), grid)
    with open(dst_path, "w") as f:
        f.write(f"ncols {int(head['ncols'])}\nnrows {int(head['nrows'])}\nxllcorner {head['xllcorner']}\n"
                f"yllcorner {head['yllcorner']}\ncellsize {head['cellsize']}\nNODATA_value {int(head.get('nodata_value', -9999))}\n")
        np.savetxt(f, out, fmt="%.5f")


def _log_error(scratch_dir, context):
    try:
        with open(os.path.join(scratch_dir, "calibration_errors.log"), "a") as f:
            f.write(f"--- {context} ---\n{traceback.format_exc()}\n")
    except Exception:
        pass


def run_trial(manning_scale, infiltration_mm_hr, ctx, idx):
    """One real solver run; returns the extent score dict or None on failure."""
    trial_dir = os.path.join(ctx["scratch_dir"], f"trial_{idx}")
    os.makedirs(trial_dir, exist_ok=True)
    try:
        rp = ctx["run_params"]
        friction = max(rp["friction"] * manning_scale, ABS_MIN_N)
        cmd = [ctx["venv_py"], ctx["flood_model_path"], "--dem", ctx["dem_path"],
               "--rain-mm-hr", str(rp["rain_mm_hr"]), "--duration-hr", str(rp["duration_hr"]),
               "--friction", str(friction), "--cfl", str(rp["cfl"]),
               "--infiltration-mm-hr", str(infiltration_mm_hr), "--amc", str(rp["amc"]),
               "--saveint-min", "100000",   # only res.max is scored; this only reduces disk writes
               "--site-name", "Calibration trial", "--workdir", trial_dir]
        if rp.get("manning_path"):
            scaled = os.path.join(trial_dir, "manning_scaled.ascii")
            _write_scaled_manning(rp["manning_path"], scaled, manning_scale)
            cmd += ["--manning-file", scaled]
        if rp.get("boundary_path"):
            cmd += ["--boundary-json", rp["boundary_path"]]
        if rp.get("rain_series_path"):
            cmd += ["--rain-series-json", rp["rain_series_path"]]
        slot = ctx["sim_slot"]
        with (slot.labelled("Calibration trial") if hasattr(slot, "labelled") else slot):
            with open(os.path.join(trial_dir, "run.log"), "w") as logf:
                proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True, timeout=ctx.get("timeout_s", 1800))
        if proc.returncode != 0:
            return None
        res = os.path.join(trial_dir, "results", "res.max")
        if not os.path.exists(res):
            return None
        _, depth = _read_ascii_grid(res)
        model_wet = (depth / ctx["acc_factor"]) > WET_THRESHOLD_M
        score = ov.score_extent(model_wet, ctx["obs_wet"], ctx["scored"], ctx["cell_km2"])
        return score
    except Exception:
        _log_error(ctx["scratch_dir"], f"trial {idx}")
        return None
    finally:
        shutil.rmtree(trial_dir, ignore_errors=True)


def calibrate(ctx, max_evals, progress_cb=None):
    """Searches roughness multiplier x infiltration for the best CSI. Returns a result dict, or None if no
    trial (not even the baseline) produced a score."""
    from mystic.solvers import diffev2

    base_infil = float(ctx["run_params"]["infiltration_mm_hr"])
    infil_hi = max(30.0, base_infil * 3.0)
    trials = []

    def evaluate(scale, infil):
        sc = run_trial(scale, infil, ctx, len(trials))
        trials.append({"manning_scale": round(float(scale), 4), "infiltration_mm_hr": round(float(infil), 3),
                       "score": sc})
        if progress_cb:
            progress_cb(len(trials), max_evals + 1, sc)
        return sc

    baseline = evaluate(1.0, base_infil)

    def objective(x):
        scale, infil = float(np.exp(x[0])), float(x[1])
        sc = evaluate(scale, infil)
        if sc is None or sc.get("CSI") is None:
            return LARGE_PENALTY
        return -sc["CSI"]

    seed = int(hashlib.sha256(f"{ctx['seed_key']}:{max_evals}".encode()).hexdigest(), 16) % (2 ** 31)
    np.random.seed(seed)
    bounds = [(float(np.log(MANNING_SCALE_RANGE[0])), float(np.log(MANNING_SCALE_RANGE[1]))), (0.0, infil_hi)]
    x0 = [0.0, min(base_infil, infil_hi)]
    npop = max(4, min(8, max_evals // 3))
    maxiter = max(1, max_evals // npop)
    try:
        diffev2(objective, x0=x0, bounds=bounds, npop=npop, maxiter=maxiter, disp=False, full_output=False, ftol=1e-4)
    except Exception:
        _log_error(ctx["scratch_dir"], "calibrate (diffev2)")

    scored = [t for t in trials if t["score"] and t["score"].get("CSI") is not None]
    if not scored:
        return None
    best = max(scored, key=lambda t: t["score"]["CSI"])
    base_csi = baseline["CSI"] if baseline else None
    return {
        "baseline": {"manning_scale": 1.0, "infiltration_mm_hr": base_infil, "score": baseline},
        "best": best,
        "csi_before": base_csi, "csi_after": best["score"]["CSI"],
        "n_trials": len(trials), "n_failed": sum(1 for t in trials if t["score"] is None),
        "trials": sorted([t for t in trials], key=lambda t: -(t["score"]["CSI"] if t["score"] and t["score"].get("CSI") is not None else -1)),
        "searched": {"manning_scale": list(MANNING_SCALE_RANGE), "infiltration_mm_hr": [0.0, round(infil_hi, 1)]},
        "wet_threshold_m": WET_THRESHOLD_M,
        "caveat": ("Two numbers were fitted to one observed event. A high score on that event does not show the values "
                   "hold for other storms; check them on a second event before relying on them. Trial runs use the "
                   "original run's rainfall but not its storm-centering or areal reduction."),
    }
