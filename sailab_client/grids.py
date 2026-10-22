"""Read SAILAB's raster files (ESRI ASCII) and run protection scenarios (structures burned into a finished run's DEM).

Needs numpy. Scenario depth grids are read from the SAILAB install on this machine (SAILAB_HOME, default ~/flood);
SAILAB does not serve them over HTTP."""
from __future__ import annotations

import json
import os
from typing import Optional

from .client import SailabClient, SailabError
from .server import sailab_home


def parse_ascii(data: "bytes | str"):
    """-> (header dict, 2-D numpy array). Row 0 is the northernmost row."""
    import numpy as np
    text = data.decode() if isinstance(data, bytes) else data
    lines = text.splitlines()
    header, i = {}, 0
    while i < len(lines) and lines[i].split()[0].lower() in ("ncols", "nrows", "xllcorner", "yllcorner", "xllcenter",
                                                           "yllcenter", "cellsize", "nodata_value"):
        k, v = lines[i].split()[:2]
        header[k.lower()] = float(v)
        i += 1
    header["ncols"], header["nrows"] = int(header["ncols"]), int(header["nrows"])
    arr = np.array([[float(x) for x in ln.split()] for ln in lines[i:] if ln.strip()], dtype="float64")
    return header, arr.reshape(header["nrows"], header["ncols"])


def run_meta(client: SailabClient, run_id: str) -> dict:
    raw = client.get(f"/runs/{run_id}/run_meta.json")
    return json.loads(raw) if isinstance(raw, (bytes, str)) else raw


def run_grid(client: SailabClient, run_id: str, name: str):
    """A raster of a finished run, e.g. "aoi.dem.ascii" (the DEM it used) or "results/res.max" (max depth, m)."""
    return parse_ascii(client.get(f"/runs/{run_id}/{name}", timeout=120))


def protect(client: SailabClient, run_id: str, structures: list, poll: float = 3.0, timeout: float = 3600.0,
            on_wait=None, extra_buildings: Optional[list] = None) -> dict:
    """Burn `structures` into the finished run's DEM and re-run the physics with the same storm.
    Each: {"type": "dam"|"barrage"|"canal"|"drainage"|"catchment", "line": [[lon, lat], ...],
    "height_m": float, "half_width_m": float}. "dam"/"barrage" raise the ground (use "dam" for a building
    block); the rest dig it. Returns SAILAB's result dict plus "job_id"."""
    import time
    job = client.post("/protect/run", {"run_id": run_id, "structures": structures, "extra_buildings": extra_buildings or []})["job_id"]
    end, t0 = time.time() + timeout, time.time()
    while True:
        d = client.get(f"/climate-jobs/{job}/progress.json")
        if d.get("status") == "done":
            return {**d.get("result", {}), "job_id": job}
        if d.get("status") == "failed" or d.get("error"):
            raise SailabError(d.get("error") or f"protection job {job} failed", d)
        if time.time() > end:
            raise SailabError(f"protection job {job} still running after {timeout:.0f} s")
        if on_wait:
            on_wait(time.time() - t0)
        time.sleep(poll)


def protect_depth(job_id: str, home: Optional[str] = None):
    """(header, max-depth grid) of a finished protection scenario. Local SAILAB install only."""
    path = os.path.join(os.path.expanduser(home) if home else sailab_home(), "fis_protect_cache", job_id, "results", "res.max")
    if not os.path.exists(path):
        raise SailabError(f"scenario depth grid not found at {path} (is SAILAB running on this machine?)")
    with open(path) as fh:
        return parse_ascii(fh.read())
