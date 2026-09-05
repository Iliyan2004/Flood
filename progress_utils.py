"""Shared progress-parsing helpers used by both watch_runs.py (terminal watcher)
and gui_app.py (in-browser progress bar), so the two never drift apart."""
import glob
import os
import re

PROGRESS_RE = re.compile(r"T\(mins\):\s*M:\s*([\d.]+),\s*C:\s*([\d.]+).*ETot:\s*([\-\d.]+)")


def read_sim_minutes(run_dir):
    """Total simulated duration (minutes) for a run, read from its .par file."""
    for par in glob.glob(os.path.join(run_dir, "*.par")):
        with open(par) as f:
            for line in f:
                if line.strip().startswith("sim_time"):
                    return float(line.split()[1]) / 60.0
    return None


def latest_progress(log_text):
    """Last 'T(mins): M: x, C: y, ..., ETot: z' line in the log, or (None,None,None)."""
    model_min = comp_min = etot = None
    for line in log_text.splitlines():
        m = PROGRESS_RE.search(line)
        if m:
            model_min, comp_min, etot = float(m.group(1)), float(m.group(2)), m.group(3)
    return model_min, comp_min, etot
