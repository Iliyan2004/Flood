"""Area limits for SAILAB.

1. Pakistan only. SAILAB has terrain (SRTM) and its datasets for Pakistan only, so an area must lie inside the Pakistan outline
   (pakistan_outline.geojson, the union of the 578 tehsil boundaries held in the project, simplified; it includes Azad Kashmir and
   Gilgit-Baltistan as the project's administrative data does). A tolerance of about 2 km absorbs the simplification at the border.
2. Run size. A physics run must finish in a reasonable time on the shared GPU. The time is estimated before the run starts from the
   number of grid cells and the simulated hours, using the solver benchmark of the paper (paper/data/scaling.json): about 5.6 microseconds
   per cell per simulated hour on the GPU, plus a fixed allowance for fetching buildings, roads and building the 3D scene. A run whose estimate
   exceeds MAX_RUN_SECONDS (10 minutes by default, env SAILAB_MAX_RUN_SECONDS) is not started and the user is told to reduce the area or to
   contact the team for a larger-area request.
"""
import json
import math
import os
import threading

from shapely.geometry import shape
from shapely.prepared import prep

HERE = os.path.dirname(os.path.abspath(__file__))
OUTLINE_PATH = os.path.join(HERE, "pakistan_outline.geojson")
BORDER_TOLERANCE_DEG = 0.02          # about 2 km
PAKISTAN_BOUNDS = (60.5, 23.3, 79.6, 37.4)   # lon_min, lat_min, lon_max, lat_max, with a little padding, for map limits

CELL_M = 29.2                        # grid cell of the Pakistan terrain after projection to UTM
SOLVER_S_PER_CELL_HOUR = 6.0e-6      # measured 5.6e-6 on the NVIDIA GB10 (4.4 million cells, 98.5 s, 4 simulated hours)
OVERHEAD_S = 40.0                    # buildings, roads, risk, loss, 3D scene for a typical area
HARD_MAX_CELLS = 6_000_000
MAX_RUN_SECONDS = float(os.environ.get("SAILAB_MAX_RUN_SECONDS", "600"))
CONTACT = os.environ.get("SAILAB_CONTACT", "the SAILAB team at AI Geo Navigators")

_lock = threading.Lock()
_pak = None


def pakistan_polygon():
    """The Pakistan outline as a shapely geometry, widened by the border tolerance, with a prepared version for fast tests."""
    global _pak
    if _pak is None:
        with _lock:
            if _pak is None:
                with open(OUTLINE_PATH) as f:
                    g = shape(json.load(f)["geometry"]).buffer(BORDER_TOLERANCE_DEG)
                _pak = (g, prep(g))
    return _pak


def outline_geojson():
    with open(OUTLINE_PATH) as f:
        return json.load(f)


def check_in_pakistan(geom):
    """(True, "") when the whole area lies inside Pakistan, else (False, message for the user)."""
    try:
        g = shape(geom)
        if g.is_empty:
            return False, "The selected area is empty."
        if not g.is_valid:
            g = g.buffer(0)
    except Exception:
        return False, "The selected area is not a valid polygon."
    _, pg = pakistan_polygon()
    if pg.covers(g):
        return True, ""
    return False, ("This area is outside Pakistan, or crosses the border. SAILAB covers Pakistan only, because terrain and other data "
                   "are available for Pakistan only. Choose an area that lies entirely inside Pakistan.")


def estimate_cells(geom, cell_m=CELL_M):
    """Grid cells of the rectangle the solver runs on (the bounding box of the area)."""
    minx, miny, maxx, maxy = shape(geom).bounds
    lat = (miny + maxy) / 2.0
    w = (maxx - minx) * 111320.0 * math.cos(math.radians(lat))
    h = (maxy - miny) * 110574.0
    return max(1, int(round((w / cell_m) * (h / cell_m))))


def estimate_run_seconds(cells, sim_hours):
    return OVERHEAD_S + cells * max(sim_hours, 0.1) * SOLVER_S_PER_CELL_HOUR


def max_cells_for(sim_hours):
    by_time = (MAX_RUN_SECONDS - OVERHEAD_S) / (max(sim_hours, 0.1) * SOLVER_S_PER_CELL_HOUR)
    return int(max(0, min(HARD_MAX_CELLS, by_time)))


def sim_hours_for_request(duration_hr, inflows=None, rainfall_series=None):
    """Simulated hours of a run: the longest driver (storm, an inflow hydrograph, an uploaded series) plus a 2 h drain-down, as the solver sets it."""
    longest = float(duration_hr or 0.0)
    for it in (inflows or []):
        try:
            longest = max(longest, float(it.get("duration_hr") or 0.0))
            s = it.get("series") or {}
            if s.get("times_hr"):
                longest = max(longest, float(max(s["times_hr"])))
        except (TypeError, ValueError):
            continue
    if rainfall_series and rainfall_series.get("times_hr"):
        longest = max(longest, float(max(rainfall_series["times_hr"])))
    return longest + 2.0


def check_physics_size(cells, sim_hours):
    """dict(ok, cells, est_seconds, limit_seconds, max_cells, max_area_km2, message)."""
    est = estimate_run_seconds(cells, sim_hours)
    mc = max_cells_for(sim_hours)
    ok = cells <= mc
    out = {"ok": ok, "cells": int(cells), "est_seconds": round(est), "limit_seconds": int(MAX_RUN_SECONDS), "max_cells": mc,
           "max_area_km2": round(mc * CELL_M ** 2 / 1e6)}
    if not ok:
        if cells > HARD_MAX_CELLS:
            why = (f"It covers about {cells / 1e6:.1f} million grid cells, more than the {HARD_MAX_CELLS / 1e6:.0f} million that can be run at once.")
        else:
            why = (f"It covers about {cells / 1e6:.2f} million grid cells and, for this storm, would take an estimated "
                   f"{est / 60.0:.0f} minutes; the limit is {MAX_RUN_SECONDS / 60:.0f} minutes.")
        out["message"] = (f"This area is too large for a standard run. {why} The run was not started. "
                          f"Please reduce the area (roughly {out['max_area_km2']:,} km² or less for this storm length), or contact {CONTACT} "
                          f"to request a run on a larger area.")
    return out
