"""
Attribution experiments for the 2022 Sindh validation gap.

validate_2022_flood.py scores the model's wet/dry extent against the UNOSAT
VIIRS flood extent of 2022-11-29 and finds it UNDER-predicts (model wet ~47 km2
vs ~108 km2 observed). Several things could explain that, and "river overflow"
was only a hypothesis. This runs controlled one-change-at-a-time experiments on
the SAME real AOI/DEM/land cover, so the gap can be attributed instead of
guessed:

  A  baseline                     (the existing validation, wettest 48 h of ERA5)
  B  closed boundaries            (no outflow: a very flat plain that cannot drain)
  C  longer storm windows         (10-day and 30-day wettest ERA5 windows, same
                                   total-rain-over-window logic as the baseline)
  B+C  both
  D  GloFAS check                 (is there ANY real river discharge near the AOI
                                   that an inflow could represent?)

Honest limits: the ground truth is a SINGLE late date (29 Nov) -- standing water
months after the August peak, not the peak extent; ERA5 under-represents extreme
local rainfall; infiltration is 0. So "closer to observed" is evidence about
which factors matter, not proof the model is calibrated.
"""
import json, os, shutil, subprocess, sys, itertools
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_2022_flood as V

OUT = os.path.join(V.VALDIR, "attribution")


def run_scenario(name, dem_path, manning_path, storm, header, aoi_geom, gt_geom, outlet_edges=None):
    wd = os.path.join(OUT, name)
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd)
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "flood_model.py"),
           "--dem", dem_path, "--rain-mm-hr", str(storm["peak_mm_hr"]), "--duration-hr", str(storm["duration_hr"]),
           "--friction", "0.05", "--infiltration-mm-hr", "0", "--amc", "50", "--solver", "auto",
           "--dry-thresh", str(V.DRY_THRESH_M), "--workdir", wd, "--site-name", f"attribution {name}"]
    if manning_path:
        cmd += ["--manning-file", manning_path]
    if outlet_edges is not None:
        cmd += ["--outlet-edges", outlet_edges]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return {"scenario": name, "error": (p.stdout + p.stderr)[-300:]}
    m = V.compute_metrics(os.path.join(wd, "results", "res.max"), header, aoi_geom, gt_geom, dem_path)
    return {"scenario": name, "rain_total_mm": round(0.5 * storm["peak_mm_hr"] * storm["duration_hr"], 1),   # triangle: 0.5*peak*duration
            "window_hr": storm["duration_hr"], "closed": outlet_edges == "",
            "model_wet_km2": m["model_wet_area_km2"], "gt_wet_km2": m["gt_wet_area_km2"],
            "max_depth_m": m["model_max_depth_m"], "CSI": m["CSI"], "POD": m["POD_hit_rate"], "FAR": m["FAR"]}


def glofas_check():
    """Max GloFAS discharge in a 5x5 neighbourhood around the AOI centre during
    the wettest 48 h -- is there a real river there at all?"""
    cells = {}
    for dlat, dlon in itertools.product([-0.1, -0.05, 0, 0.05, 0.1], repeat=2):
        r = requests.get("https://flood-api.open-meteo.com/v1/flood", params={
            "latitude": V.AOI_CENTER_LAT + dlat, "longitude": V.AOI_CENTER_LON + dlon, "daily": "river_discharge",
            "start_date": "2022-08-17", "end_date": "2022-08-31"}, timeout=40).json()
        if "daily" in r:
            q = [x for x in r["daily"]["river_discharge"] if x is not None]
            cells[(round(r["latitude"], 3), round(r["longitude"], 3))] = max(q) if q else 0.0
    top = sorted(cells.items(), key=lambda kv: -kv[1])[:3]
    return {"n_distinct_cells": len(cells), "top_cells_max_m3s_17_31_aug_2022": [(k, round(v, 1)) for k, v in top]}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list of scenario names to run, e.g. w720h_open")
    ap.add_argument("--skip-glofas", action="store_true")
    args = ap.parse_args()
    only = [x for x in args.only.split(",") if x]
    gt_geom = V.ensure_ground_truth()
    era5 = V.fetch_era5_hourly()
    dem_path, manning_path, header, aoi_geom = V.prepare_dem_and_manning()
    os.makedirs(OUT, exist_ok=True)
    storms = {h: V.derive_design_storm(era5, window_hr=h) for h in (48, 240, 720)}
    results = []
    for h, closed in itertools.product((48, 240, 720), (False, True)):
        name = f"w{h}h_{'closed' if closed else 'open'}"
        if only and name not in only:
            continue
        print(f"\n### running {name}", flush=True)
        results.append(run_scenario(name, dem_path, manning_path, storms[h], header, aoi_geom, gt_geom,
                                    outlet_edges="" if closed else None))
        print(results[-1], flush=True)
    g = {} if args.skip_glofas else glofas_check()
    report = {"results": results, "glofas_near_aoi": g}
    tag = ("_" + "_".join(only)) if only else ""
    json.dump(report, open(os.path.join(OUT, f"attribution_report{tag}.json"), "w"), indent=2, default=str)
    print("\n" + "=" * 90)
    print(f"{'scenario':16s} {'rain mm':>8s} {'model km2':>10s} {'obs km2':>8s} {'maxdepth':>9s} {'CSI':>6s} {'POD':>6s} {'FAR':>6s}")
    for r in results:
        if "error" in r:
            print(r["scenario"], "ERROR", r["error"]); continue
        print(f"{r['scenario']:16s} {r['rain_total_mm']:8.0f} {r['model_wet_km2']:10.1f} {r['gt_wet_km2']:8.1f} "
              f"{r['max_depth_m']:9.2f} {r['CSI']:6.3f} {r['POD']:6.3f} {r['FAR']:6.3f}")
    print("\nGloFAS near AOI:", g)


if __name__ == "__main__":
    main()
