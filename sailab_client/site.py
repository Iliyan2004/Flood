"""Site-level helpers over a running SAILAB: storm and boundary inputs, real buildings, roads, and a per-site
hazard response curve. Everything that talks to SAILAB, and its caching, lives here so callers (e.g. SimCity) make
one call each. Results are cached on disk (default ~/.cache/sailab_client), keyed by the request.

Inputs come only from SAILAB's physics-model pathways (no instant tier):
  * real recorded IDF design intensity (ERA5-Land + IMERG)      /area/idf-design-storm
  * return-period design storm (CMIP6 Gumbel)                   /area/design-storm
  * CMIP6 climate intensification factor (delta change)         /area/climate-factor
  * rivers/streams crossing the site, with GloFAS discharge     /area/detect-boundaries
  * real building footprints                                    /area/buildings
  * roads and critical infrastructure of a finished run         /runs/<id>/infra_raw.json
Modules that do not apply to an inland urban site are reported in NOT_USED.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

NOT_USED = [
    {"module": "GLOF", "why": "no glacial lake drains into Islamabad"},
    {"module": "Snow and glacier melt", "why": "no glacier in the catchment"},
    {"module": "Coastal surge and tide", "why": "inland site"},
    {"module": "Dam failure", "why": "not part of a design-storm scenario"},
    {"module": "Soil / Green-Ampt infiltration", "why": "SAILAB computes it, but its GPU solver ignores it (measured)"},
]


class SailabSite:
    def __init__(self, client, cache_dir: "str | Path | None" = None):
        self.client = client
        self.cache = Path(cache_dir or Path.home() / ".cache" / "sailab_client")
        self.cache.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str, key: dict) -> Path:
        return self.cache / f"{name}-{hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]}.json"

    def _cached_raw(self, name: str, key: dict):
        f = self._file(name, key)
        return json.loads(f.read_text()) if f.exists() else None

    def _store(self, name: str, key: dict, val) -> None:
        self._file(name, key).write_text(json.dumps(val))

    def _cached(self, name: str, key: dict, fn):
        val = self._cached_raw(name, key)
        if val is None:
            val = fn()
            self._store(name, key, val)
        return val

    def storm(self, aoi: dict, duration_hr: float = 2.0, season: str = "monsoon", ssp: str | None = "ssp585",
              period: str = "2050 (mid-century)", return_period: int = 100, boundaries: bool = True,
              amc: float = 60.0, arf: bool = False, mode: str = "design", stress: float = 1.0, on_step=None) -> dict[str, Any]:
        """mode "recorded": the real recorded monsoon maximum (IDF). mode "design": the `return_period`-year
        24 h design storm (CMIP6 Gumbel fit), as a triangular hyetograph exactly as SAILAB's own UI builds it."""
        used = []
        step = on_step or (lambda text: None)
        if mode == "design":
            step(f"Design storm ({return_period}-year, CMIP6 Gumbel fit): first time for a new area takes minutes")
            def design_storm():
                job = self.client.post("/area/design-storm", {"geometry": aoi, "return_period_years": return_period})["job_id"]
                return self.client.wait_job(job, poll=5, timeout=900)
            ds = self._cached("designstorm", {"a": aoi, "t": return_period}, design_storm)
            depth = float(ds["design_depth_mm_24hr"])
            duration_hr, rain = 24.0, 2 * depth / 24.0
            used.append({"module": f"Design storm ({return_period}-year)",
                         "value": f"{depth:.0f} mm in 24 h (peak {rain:.1f} mm/hr; models range "
                                  f"{ds['ensemble_min_mm']:.0f}-{ds['ensemble_max_mm']:.0f} mm)",
                         "source": "CMIP6 historical, Gumbel fit"})
        else:
            step("Real recorded rainfall (IDF) for this site")
            idf = self._cached("idf", {"a": aoi, "d": duration_hr, "s": season}, lambda: self.client.post(
                "/area/idf-design-storm", {"geometry": aoi, "duration_hr": duration_hr, "season": season}))
            rain = float(idf["intensity_mm_hr"])
            used.append({"module": "Rainfall (real IDF)", "value": f"{rain:.1f} mm/hr over {duration_hr:g} h, {season}",
                         "source": "ERA5-Land + GPM IMERG recorded maxima"})
        if ssp:
            step(f"Climate change factor (CMIP6 {ssp}, {period}): first time for a new area takes minutes")

            def climate():
                job = self.client.post("/area/climate-factor", {"geometry": aoi, "return_period_years": return_period,
                                                                "scenario": ssp, "future_period": period})["job_id"]
                return self.client.wait_job(job, poll=5, timeout=900)
            cf = self._cached("climate", {"a": aoi, "ssp": ssp, "p": period, "t": return_period}, climate)
            if "intensification_factor" in cf:
                f = float(cf["intensification_factor"])
                rain *= f
                used.append({"module": "Climate change (CMIP6)",
                             "value": f"x{f:.2f} ({ssp}, {period}, {return_period}-yr; "
                                      f"{cf['ensemble_min_factor']:.2f}-{cf['ensemble_max_factor']:.2f} across {cf['n_models_used']} models)",
                             "source": "CMIP6 BCSD delta-change"})
        if stress != 1.0:
            rain *= stress
            used.append({"module": "Stress multiplier", "value": f"x{stress:g} on storm intensity",
                         "source": "user choice (stress test beyond the recorded record)"})
        inflows, outflows = [], []
        if boundaries:
            step("Detecting rivers and streams crossing the site (OSM + GloFAS discharge)")
            found = self._cached("bounds", {"a": aoi}, lambda: self.client.post(
                "/area/detect-boundaries", {"geometry": aoi}, timeout=180))["crossings"]
            for c in found:
                item = {"edge": c["edge"], "lon": c["lon"], "lat": c["lat"], "pos_pct": None,
                        "width_m": c["channel_width_m"], "name": c.get("name"), "rise_frac": 0.5, "series": None}
                if c["direction"] == "inflow":
                    d = c.get("discharge") or {}
                    inflows.append({**item, "peak_m3s": round(float(d.get("recent_m3s") or d.get("mean_m3s") or 0), 1),
                                    "duration_hr": 3})
                else:
                    outflows.append(item)
            used.append({"module": "River boundaries",
                         "value": f"{len(inflows)} inflow(s), {len(outflows)} outflow(s) detected",
                         "source": "OSM waterways + GloFAS discharge"})
        used.append({"module": "Antecedent moisture / ARF", "value": f"AMC {amc:g}/100, ARF {'on' if arf else 'off'}",
                     "source": "SAILAB run options"})
        return {"rain_mm_hr": rain, "duration_hr": duration_hr,
                "run_kw": {"amc": amc, "enable_arf": arf, "inflows": inflows, "outflows": outflows},
                "used": used, "not_used": NOT_USED}

    def existing_buildings(self, aoi: dict) -> dict:
        """SAILAB's real building footprints (Google Open Buildings + OSM heights) for the site. The first call for
        a new area downloads source tiles, which is slow; it also warms the cache the physics runs use."""
        return self._cached("existing", {"a": aoi}, lambda: self.client.post("/area/buildings", {"geometry": aoi}, timeout=1800))

    def roads(self, run_id: str) -> list[dict]:
        """Roads SAILAB loaded for a finished run: [{id, name, highway, geometry: [[lon, lat], ...]}]."""
        raw = self.client.get(f"/runs/{run_id}/infra_raw.json")  # served as a plain file, so it may arrive as bytes
        return (json.loads(raw) if isinstance(raw, (bytes, str)) else raw).get("roads", [])

    def hazard_curve(self, aoi: dict, storm: dict, levels: list[float], on_run=None, on_progress=None) -> "HazardCurve":
        """SAILAB's real physics at each effective-rain level for this site and storm; cached as a whole."""
        key = {"a": aoi, "s": {k: storm[k] for k in ("rain_mm_hr", "duration_hr")}, "kw": storm.get("run_kw", {}), "l": levels}
        cached = self._cached_raw("curve", key)
        if cached is not None:
            return HazardCurve.from_json(cached)
        curve = HazardCurve.build(self.client, aoi, storm, levels, on_run, on_progress)
        self._store("curve", key, curve.to_json())
        return curve



class HazardCurve:
    """A site's real SAILAB response to rainfall: full physics runs at a few rain levels, linearly interpolated.
    Built per site and storm; nothing is learned or transferred between sites."""

    def __init__(self, points: list[tuple[float, dict]]):
        self.points = sorted(points, key=lambda p: p[0])

    @classmethod
    def build(cls, client, aoi: dict, storm: dict, levels: list[float], on_run=None, on_progress=None) -> "HazardCurve":
        """on_run(i, n, rain, run_id) fires when a run starts; on_progress(i, n, d) with SAILAB's own progress
        dict (status, stage, pct, model_min, sim_min, queue_position...) on every poll; on_run is also called
        with run_id=None never, so callers can use run_id to open SAILAB's live scene."""
        pts = []
        for i, rain in enumerate(levels):
            started = client.start_flood(aoi, rain, storm["duration_hr"], include_buildings=True, **storm.get("run_kw", {}))
            rid = started["run_id"]
            if on_run:
                on_run(i, len(levels), rain, rid)
            r = client.wait_flood(rid, poll=2.0, on_progress=(lambda d, i=i: on_progress(i, len(levels), d)) if on_progress else None)
            pts.append((rain, {**r.summary, "run_id": rid}))
        return cls(pts)

    @property
    def last_run_id(self) -> str:
        return self.points[-1][1]["run_id"]

    def at(self, rain: float) -> dict:
        """Depth, flooded area and existing-building loss at `rain` mm/hr (clamped to the sampled range)."""
        pts = self.points
        if rain <= pts[0][0]:
            return dict(pts[0][1])
        if rain >= pts[-1][0]:
            return dict(pts[-1][1])
        loss = lambda s: float((s.get("estimated_building_loss") or {}).get("total_loss_pkr") or 0.0)
        for (r0, s0), (r1, s1) in zip(pts, pts[1:]):
            if r0 <= rain <= r1:
                t = (rain - r0) / (r1 - r0)
                lin = lambda a, b: a + t * (b - a)
                return {"max_depth_m": lin(float(s0.get("max_depth_m") or 0), float(s1.get("max_depth_m") or 0)),
                        "flooded_area_km2": lin(float(s0.get("flooded_area_km2") or 0), float(s1.get("flooded_area_km2") or 0)),
                        "estimated_building_loss": {"total_loss_pkr": lin(loss(s0), loss(s1))}}
        raise AssertionError("unreachable")

    def to_json(self) -> list:
        keep = ("max_depth_m", "flooded_area_km2", "estimated_building_loss", "wall_clock_seconds", "run_id")
        return [[r, {k: s.get(k) for k in keep}] for r, s in self.points]

    @classmethod
    def from_json(cls, data: list) -> "HazardCurve":
        return cls([(r, s) for r, s in data])
