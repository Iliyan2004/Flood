"""Regenerate every figure that has its data and rebuild the paper.  python paper/make_all.py
Experiments (slow, need the solver and terrain): bench_scaling.py, run_sensitivity.py, case_study.py, monitoring_latency.py, skill_metrics.py."""
import os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__)); py = sys.executable
D = os.path.join(H, "data")
steps = [("fig_a.py", None), ("fig_c.py", ["scaling.json", "preview_vs_full_windows.json"]), ("fig_d.py", ["sensitivity.json"])]
if os.path.exists(os.path.join(D, "skill_metrics.json")):
    steps.insert(1, ("fig_b.py", ["skill_metrics.json"]))     # needs the multi-model study arrays under ~/flood/cmp
for f, need in steps:
    if need and not all(os.path.exists(os.path.join(D, n)) for n in need):
        print("skip", f, "(data missing)"); continue
    print("run", f); subprocess.run([py, os.path.join(H, f)], check=False)
subprocess.run([py, os.path.join(H, "build_paper.py")], check=True)
