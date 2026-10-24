"""Finds old cached runs and (only with --apply) deletes them.

Default is a DRY RUN that lists what would go. A run is a candidate when its newest file is older than
--days AND it is not among the --keep-newest most recent runs AND its id is not in keep_runs.txt (one id
per line). Usage:  python cache_cleanup.py [--days 30] [--keep-newest 20] [--apply] [--dir fis_cache]
"""
import argparse
import os
import shutil
import time


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def newest_mtime(path):
    newest = os.path.getmtime(path)
    for root, _, files in os.walk(path):
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    return newest


def find_candidates(cache_dir, days=30, keep_newest=20, protected=(), now=None):
    """Returns [(run_id, age_days, size_bytes)] oldest first. Never includes protected ids or the newest runs."""
    now = now or time.time()
    runs = []
    for name in os.listdir(cache_dir):
        p = os.path.join(cache_dir, name)
        if os.path.isdir(p):
            runs.append((name, newest_mtime(p)))
    runs.sort(key=lambda r: r[1], reverse=True)
    out = []
    for name, mt in runs[max(0, keep_newest):]:
        age = (now - mt) / 86400.0
        if name in protected or age < days:
            continue
        out.append((name, round(age, 1), dir_size(os.path.join(cache_dir, name))))
    return sorted(out, key=lambda r: -r[1])


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(here, "fis_cache"))
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--keep-newest", type=int, default=20)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    keep_file = os.path.join(here, "keep_runs.txt")
    protected = set(open(keep_file).read().split()) if os.path.exists(keep_file) else set()
    cands = find_candidates(a.dir, a.days, a.keep_newest, protected)
    total = sum(c[2] for c in cands)
    for rid, age, size in cands:
        print(f"{rid}  {age:6.1f} d  {size / 1e9:6.2f} GB")
    print(f"{len(cands)} runs, {total / 1e9:.1f} GB {'DELETED' if a.apply else '(dry run; add --apply to delete)'}")
    if a.apply:
        for rid, _, _ in cands:
            shutil.rmtree(os.path.join(a.dir, rid), ignore_errors=True)


if __name__ == "__main__":
    main()
