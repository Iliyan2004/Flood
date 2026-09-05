#!/usr/bin/env python3
"""
watch_runs.py — tails ~/flood/gui_runs for new PluviSim GUI submissions and
prints one line per progress milestone / completion / failure. Meant to be
run under the Monitor tool so each stdout line becomes a live notification.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress_utils import read_sim_minutes, PROGRESS_RE  # noqa: E402

RUNS_DIR = os.path.expanduser("~/flood/gui_runs")
EXIT_RE = re.compile(r"=== EXIT_CODE:(\-?\d+) elapsed=([\d.]+)s ===")

os.makedirs(RUNS_DIR, exist_ok=True)


def site_name(run_dir):
    for par in glob.glob(os.path.join(run_dir, "*.par")):
        return os.path.basename(run_dir)
    return os.path.basename(run_dir)


def main():
    print(f"[watch] watching {RUNS_DIR} for new PluviSim GUI runs...", flush=True)
    known = {}   # run_id -> state dict

    while True:
        try:
            run_ids = sorted(d for d in os.listdir(RUNS_DIR)
                              if os.path.isdir(os.path.join(RUNS_DIR, d)))
        except FileNotFoundError:
            run_ids = []

        for run_id in run_ids:
            run_dir = os.path.join(RUNS_DIR, run_id)
            log_path = os.path.join(run_dir, "run.log")

            if run_id not in known:
                known[run_id] = {"offset": 0, "last_pct": -1, "sim_min": None,
                                  "done": False, "start": time.time()}
                print(f"[watch] NEW RUN {run_id} detected", flush=True)

            st = known[run_id]
            if st["done"] or not os.path.exists(log_path):
                continue

            if st["sim_min"] is None:
                st["sim_min"] = read_sim_minutes(run_dir)

            with open(log_path) as f:
                f.seek(st["offset"])
                new_text = f.read()
                st["offset"] = f.tell()

            if not new_text:
                continue

            for line in new_text.splitlines():
                m_exit = EXIT_RE.search(line)
                if m_exit:
                    code, elapsed = int(m_exit.group(1)), float(m_exit.group(2))
                    if code != 0:
                        print(f"[watch] {run_id} FAILED after {elapsed:.1f}s (exit {code}) "
                              f"-- see {log_path}", flush=True)
                        st["done"] = True
                    else:
                        summary_path = os.path.join(run_dir, "summary.json")
                        if os.path.exists(summary_path):
                            with open(summary_path) as sf:
                                s = json.load(sf)
                            print(
                                f"[watch] {run_id} COMPLETE in {elapsed:.1f}s -- "
                                f"flooded {s['flooded_area_km2']:.4f} km^2 "
                                f"({s['flooded_pct_of_domain']:.1f}% of domain), "
                                f"max depth {s['max_depth_m']:.2f} m, "
                                f"hazard: {s['peak_hazard_band']}, "
                                f"mass-balance error {s['final_mass_error_m3']:.2e} m^3",
                                flush=True)
                        else:
                            print(f"[watch] {run_id} solver finished in {elapsed:.1f}s "
                                  f"(post-processing still running...)", flush=True)
                    continue

                if "3D_EXIT_CODE" in line:
                    ok = "3D_EXIT_CODE:0" in line
                    print(f"[watch] {run_id} 3D viewer build {'OK' if ok else 'FAILED'}", flush=True)
                    st["done"] = True
                    continue

                m = PROGRESS_RE.search(line)
                if m and st["sim_min"]:
                    model_min, comp_min, etot = float(m.group(1)), float(m.group(2)), m.group(3)
                    pct = min(99, int(model_min / st["sim_min"] * 100))
                    if pct >= st["last_pct"] + 20:  # report every ~20% to avoid spam
                        st["last_pct"] = pct
                        print(f"[watch] {run_id} progress: {pct}% "
                              f"(model time {model_min:.0f}/{st['sim_min']:.0f} min, "
                              f"{comp_min:.1f}s compute so far, ETA total {etot} min)",
                              flush=True)

        time.sleep(1.0)


if __name__ == "__main__":
    main()
