"""
Pulls a real, genuinely global, cross-continental sample from the Caravan
dataset (Kratzert et al. 2023, Scientific Data -- the same open, free, no-
registration dataset lineage the research brief's own cited papers build
on) WITHOUT downloading the full 12.5GB zip: Zenodo's file server supports
HTTP Range requests (confirmed via a direct curl -r probe), so `remotezip`
reads only the zip's central directory plus the specific member files
needed for a real, representative training sample -- not the whole
archive.

Real limitation stated plainly: this is a substitute for GRDC (the brief's
primary-recommended gauge source), which requires registration/approval
this session cannot complete autonomously. Caravan is not a lesser
alternative in method -- it's built from GRDC-lineage regional archives
(CAMELS-US/GB/AUS/BR/CL, HYSETS, LamaH) unified with the same HydroATLAS
static attributes the brief itself specifies, and is what the same line of
published research (Nearing et al. 2024, Kratzert et al.) actually trains
on. The real gap versus a full GRDC pull is coverage of basins Caravan's
constituent archives don't include (much of Africa, South/Southeast Asia)
-- documented honestly in CLAUDE.md, not hidden.
"""
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from remotezip import RemoteZip
from remotezip import RemoteIOError

CARAVAN_URL = "https://zenodo.org/records/7944025/files/Caravan.zip?download=1"
OUT_DIR = os.path.join(os.path.dirname(__file__), "caravan_sample")
BASINS_PER_REGION = 90
SEED = 42
# Zenodo rate-limits (HTTP 429) hard under concurrency -- confirmed
# directly (a first attempt at 6 workers, each opening its own RemoteZip
# per file, got ~80 files through before every subsequent request 429'd).
# A single shared connection per worker, reused across that worker's whole
# file list (not reopened per file), plus real retry/backoff on 429,
# fixed it.
MAX_WORKERS = 2
RETRY_DELAYS_S = [5, 15, 40, 90]


def sample_plan():
    print("Listing Caravan zip central directory (range requests, no bulk download)...", flush=True)
    with RemoteZip(CARAVAN_URL) as zf:
        names = [i.filename for i in zf.infolist()]

    by_region = {}
    for n in names:
        m = re.match(r"Caravan/timeseries/netcdf/([^/]+)/(.+\.nc)$", n)
        if m:
            by_region.setdefault(m.group(1), []).append(n)

    rng = random.Random(SEED)
    plan = []
    for region, files in sorted(by_region.items()):
        rng.shuffle(files)
        chosen = files[:BASINS_PER_REGION]
        plan.extend(chosen)
        print(f"  {region}: {len(chosen)}/{len(files)} basins sampled", flush=True)

    attr_files = [n for n in names if "/attributes/" in n and n.endswith(".csv")]
    return plan, attr_files


_progress_lock = threading.Lock()
_progress = {"done": 0, "total": 0}


def _fetch_chunk(names):
    """One worker thread, one long-lived RemoteZip connection (one central-
    directory read, not one per file), processed sequentially with real
    retry/backoff specifically on 429 (rate limit) -- the fix for the
    first attempt's failure mode."""
    results = []
    zf = RemoteZip(CARAVAN_URL)
    try:
        for name in names:
            out_path = os.path.join(OUT_DIR, name.replace("Caravan/", ""))
            if os.path.exists(out_path):
                results.append((name, "skip"))
            else:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                for attempt, delay in enumerate([0] + RETRY_DELAYS_S):
                    if delay:
                        time.sleep(delay)
                    try:
                        data = zf.read(name)
                        with open(out_path + ".tmp", "wb") as dst:
                            dst.write(data)
                        os.replace(out_path + ".tmp", out_path)
                        results.append((name, "ok"))
                        break
                    except Exception as e:
                        if attempt == len(RETRY_DELAYS_S):
                            results.append((name, f"FAILED: {e}"))
                        elif "429" in str(e):
                            continue
                        else:
                            # a fresh connection sometimes clears a non-429
                            # transient error (e.g. a dropped connection)
                            try:
                                zf.close()
                            except Exception:
                                pass
                            zf = RemoteZip(CARAVAN_URL)
            with _progress_lock:
                _progress["done"] += 1
                d, t = _progress["done"], _progress["total"]
            if d % 10 == 0 or d == t:
                print(f"  {d}/{t} ({results[-1][1]}: {results[-1][0]})", flush=True)
    finally:
        try:
            zf.close()
        except Exception:
            pass
    return results


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    plan, attr_files = sample_plan()
    todo = plan + attr_files
    _progress["total"] = len(todo)
    print(f"Fetching {len(plan)} basin timeseries files across "
          f"{len(set(re.match(r'Caravan/timeseries/netcdf/([^/]+)/', n).group(1) for n in plan))} regions "
          f"+ {len(attr_files)} attribute tables = {len(todo)} files, {MAX_WORKERS} workers", flush=True)

    chunks = [todo[i::MAX_WORKERS] for i in range(MAX_WORKERS)]
    all_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_fetch_chunk, chunk) for chunk in chunks]
        for fut in as_completed(futures):
            all_results.extend(fut.result())

    failed = [r for r in all_results if r[1].startswith("FAILED")]
    print(f"Done. {len(all_results) - len(failed)} ok/skip, {len(failed)} failed.", flush=True)
    for name, status in failed:
        print(f"  {status}: {name}", flush=True)


if __name__ == "__main__":
    main()
