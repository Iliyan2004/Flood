"""
Real mystic-driven (differential evolution) search for protection-
structure placement. No surrogate model: every candidate mystic evaluates
is a REAL LISFLOOD-FP re-run on the baseline's own building-conditioned
DEM with the candidate structure(s) burned in
(protection_utils.apply_structures_to_dem), using the exact same physics
CLI a manually-drawn scenario uses -- never a fitted/cheap approximation.
This makes each evaluation expensive (a full flood run), so the search is
a hard, small, user-chosen eval budget, not an open-ended
search-until-converged loop.

Fresh reimplementation (see protection_utils.py's own docstring for why:
the earlier version of this feature was deleted outright in an earlier
"remove Tab 6 completely" pass).
"""
import hashlib
import os
import shutil
import subprocess
import traceback

import numpy as np
from mystic.solvers import diffev2

import infra_utils
import loss_utils
import nbs_utils
import protection_utils

# 7 real decision variables per structure: center position (normalized
# within the AOI's own lon/lat bbox), orientation, length (normalized to
# the bbox diagonal, clamped so it's never degenerate or wildly
# oversized), a height/diameter multiplier and a half-width multiplier
# (both applied to that structure type's own STRUCTURE_TYPES default),
# and which structure type (continuous, floored to an index) -- lets
# diffev2 search a mixed geometry+type space without a separate
# categorical solver.
BOUNDS_PER_STRUCTURE = [
    (0.05, 0.95),   # center_frac_lon
    (0.05, 0.95),   # center_frac_lat
    (0.0, 6.283),   # orientation (radians)
    (0.15, 0.9),    # length_frac of the bbox diagonal
    (0.5, 1.5),     # height/diameter multiplier
    (0.5, 1.5),     # half-width multiplier
    (0.0, 0.999),   # type index (continuous, floored)
]

LARGE_PENALTY = 1e6


def _decode(x, n_structures, types, bbox):
    lon_min, lat_min, lon_max, lat_max = bbox
    diag = ((lon_max - lon_min) ** 2 + (lat_max - lat_min) ** 2) ** 0.5
    structures = []
    for i in range(n_structures):
        cx, cy, theta, length_frac, h_mult, w_mult, type_f = x[i * 7:(i + 1) * 7]
        stype = types[min(int(type_f * len(types)), len(types) - 1)]
        cfg = protection_utils.STRUCTURE_TYPES[stype]
        lon_c = lon_min + cx * (lon_max - lon_min)
        lat_c = lat_min + cy * (lat_max - lat_min)
        half_len = 0.5 * length_frac * diag
        dx, dy = half_len * np.cos(theta), half_len * np.sin(theta)
        structures.append({
            "type": stype,
            "line": [[lon_c - dx, lat_c - dy], [lon_c + dx, lat_c + dy]],
            "height_m": round(cfg["default_height_m"] * h_mult, 3),
            "half_width_m": round(cfg["default_half_width_m"] * w_mult, 3),
        })
    return structures


def _log_error(scratch_dir, context):
    """Real diagnostic logging for the two broad `except Exception` sites
    below -- flagged in an earlier review as a real observability gap: a
    genuine programming bug (a typo, a missing key, an unexpected
    exception unrelated to "this trial's physics failed") was previously
    indistinguishable from an expected real failure (a bad candidate
    structure, a solver crash), since both just produced a silent `None`/
    a silently-empty search with zero diagnostic trace. Appends to a
    persistent `optimizer_errors.log` in `scratch_dir` (NOT `trial_dir`,
    which gets deleted after every trial in `_run_trial`'s own `finally`
    block) -- never raises itself, so a logging failure can't turn into a
    second, worse failure on top of the one being logged."""
    try:
        with open(os.path.join(scratch_dir, "optimizer_errors.log"), "a") as f:
            f.write(f"--- {context} ---\n{traceback.format_exc()}\n")
    except Exception:
        pass


def _run_trial(structures, ctx, trial_idx):
    """Real single flood-model re-run for one candidate. Returns
    nbs_utils.compare_flood_results' real dict, or None on any real
    failure (structure lands outside the AOI, or the solver itself
    fails) -- callers apply LARGE_PENALTY for None so the search moves
    away from it."""
    trial_dir = os.path.join(ctx["scratch_dir"], f"trial_{trial_idx}")
    os.makedirs(trial_dir, exist_ok=True)
    try:
        affected = protection_utils.structures_affected_cells(
            ctx["baseline_dem"], ctx["header"], ctx["src_crs"], structures)
        if affected == 0:
            return None

        modified_dem = protection_utils.apply_structures_to_dem(
            ctx["baseline_dem"], ctx["header"], ctx["src_crs"], structures)
        dem_path = os.path.join(trial_dir, "trial.dem.ascii")
        protection_utils.write_ascii_grid(dem_path, ctx["header"], modified_dem)

        rp = ctx["run_params"]
        cmd = [ctx["venv_py"], ctx["flood_model_path"], "--dem", dem_path,
               "--rain-mm-hr", str(rp["rain_mm_hr"]), "--duration-hr", str(rp["duration_hr"]),
               "--friction", str(rp["friction"]), "--cfl", str(rp["cfl"]),
               "--infiltration-mm-hr", str(rp["infiltration_mm_hr"]), "--amc", str(rp["amc"]),
               # A trial only needs res.max to score it -- a large
               # saveint just reduces how often the solver WRITES state
               # to disk (I/O only), never its physics timestep.
               "--saveint-min", "100000",
               "--site-name", "Protection trial", "--workdir", trial_dir]
        if rp.get("manning_path"):
            cmd += ["--manning-file", rp["manning_path"]]
        if rp.get("boundary_path"):
            cmd += ["--boundary-json", rp["boundary_path"]]

        with (ctx["sim_slot"].labelled("Protection optimizer trial") if hasattr(ctx["sim_slot"], "labelled") else ctx["sim_slot"]):
            with open(os.path.join(trial_dir, "run.log"), "w") as logf:
                proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True, timeout=600)
        if proc.returncode != 0:
            return None

        res_path = os.path.join(trial_dir, "results", "res.max")
        if not os.path.exists(res_path):
            return None
        with open(res_path) as f:
            for _ in range(6):
                f.readline()
            depth = np.loadtxt(f)
        comparison = nbs_utils.compare_flood_results(ctx["baseline_depth"], depth, ctx["valid_mask"])

        # Real per-trial monetary loss, when the baseline run has real
        # building data on hand -- reuses the SAME real footprints/heights
        # from the baseline's own building_submersion.json (poly_rc/
        # height_m don't change between baseline and any candidate
        # structure placement; only which buildings are wet, and how
        # deeply, does), re-classified against THIS trial's own real
        # depth grid via the same real Huizinga curve every other loss
        # figure in this app uses. No new buildings fetch per trial.
        if ctx.get("baseline_building_features") and ctx.get("loss_before_pkr", 0.0) > 0:
            trial_sub = infra_utils.assess_building_submersion_risk(
                ctx["baseline_building_features"], depth, ctx["header"])
            loss_after = loss_utils.estimate_building_loss_pkr(
                trial_sub["features"], float(ctx["header"]["cellsize"]))
            loss_avoided_pkr = ctx["loss_before_pkr"] - loss_after["total_loss_pkr"]
            comparison["loss_avoided_pkr"] = round(loss_avoided_pkr, 2)
            comparison["reduction_pct_of_loss"] = round(
                100.0 * loss_avoided_pkr / ctx["loss_before_pkr"], 2)
        return comparison
    except Exception:
        _log_error(ctx["scratch_dir"], f"_run_trial (trial {trial_idx})")
        return None
    finally:
        shutil.rmtree(trial_dir, ignore_errors=True)


def optimize_structures(header, src_crs, baseline_dem, baseline_depth, valid_mask, bbox,
                         run_params, types, n_structures, max_evals,
                         venv_py, flood_model_path, sim_slot, scratch_dir, progress_cb=None,
                         baseline_building_features=None):
    """Runs the real search, returns the best ACTUALLY-EVALUATED trial's
    own {"structures", "comparison", "score"} -- diffev2's own returned
    solution vector isn't guaranteed to be an exact point the loop
    scored (it can return a blended/clamped variant), so re-decoding it
    would risk reporting metrics that don't match the applied geometry.
    Returns None if every trial in the budget failed.

    `baseline_building_features`, when given (the baseline run's own real
    building_submersion.json "features"), makes the search optimize for
    real estimated MONETARY loss avoided (PKR) instead of raw cell counts
    -- directly answering "which placement saves the most real value",
    not just "which placement keeps the most area dry regardless of
    what's on it". Falls back to the original cell-based objective when
    no real building data is available (e.g. buildings were disabled for
    the baseline run) -- same real degrade-gracefully principle already
    used throughout this app when a real data source is missing."""
    loss_before_pkr = 0.0
    if baseline_building_features:
        loss_before_pkr = loss_utils.estimate_building_loss_pkr(
            baseline_building_features, float(header["cellsize"]))["total_loss_pkr"]

    ctx = {"header": header, "src_crs": src_crs, "baseline_dem": baseline_dem,
           "baseline_depth": baseline_depth, "valid_mask": valid_mask,
           "run_params": run_params, "venv_py": venv_py, "flood_model_path": flood_model_path,
           "sim_slot": sim_slot, "scratch_dir": scratch_dir,
           "baseline_building_features": baseline_building_features, "loss_before_pkr": loss_before_pkr}

    trial_log = []

    def objective(x):
        structures = _decode(x, n_structures, types, bbox)
        idx = len(trial_log)
        comparison = _run_trial(structures, ctx, idx)
        if comparison is None:
            score = LARGE_PENALTY
        elif "reduction_pct_of_loss" in comparison:
            score = -comparison["reduction_pct_of_loss"] + 0.5 * comparison["displacement_pct"]
        else:
            score = -comparison["reduction_pct"] + 0.5 * comparison["displacement_pct"]
        trial_log.append({"structures": structures, "comparison": comparison, "score": score})
        if progress_cb:
            progress_cb(len(trial_log), max_evals, comparison)
        return score

    seed_key = f"{n_structures}:{sorted(types)}:{max_evals}:{bbox}"
    seed = int(hashlib.sha256(seed_key.encode()).hexdigest(), 16) % (2 ** 31)
    np.random.seed(seed)

    bounds = BOUNDS_PER_STRUCTURE * n_structures
    x0 = [(lo + hi) / 2.0 for lo, hi in bounds]
    npop = max(4, min(10, max_evals // 3))
    maxiter = max(1, max_evals // max(npop, 1))

    try:
        diffev2(objective, x0=x0, bounds=bounds, npop=npop, maxiter=maxiter,
                disp=False, full_output=False, ftol=1e-3)
    except Exception:
        # A real solver-internal error still leaves trial_log with whatever
        # real trials completed before it -- logged (not silently passed)
        # so a genuine bug in `objective`/`_decode` itself is distinguishable
        # from diffev2 legitimately running out of budget.
        _log_error(scratch_dir, "optimize_structures (diffev2)")

    valid_trials = [t for t in trial_log if t["comparison"] is not None]
    if not valid_trials:
        return None
    best = min(valid_trials, key=lambda t: t["score"])
    best["n_trials"] = len(trial_log)
    return best
