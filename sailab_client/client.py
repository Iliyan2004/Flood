"""HTTP client for a running SAILAB server. All heavy work (terrain, GPU solver, buildings, tide data) stays on the
server; this module only sends requests and waits for results."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

DEFAULT_URL = "http://127.0.0.1:8000"


class SailabError(RuntimeError):
    """The server refused a request or a job failed. `payload` holds the server's own JSON when there was one."""

    def __init__(self, message: str, payload: Optional[dict] = None):
        super().__init__(message)
        self.payload = payload or {}


class SailabUnavailable(SailabError):
    """The server cannot be reached (not running, wrong address, tunnel down)."""


@dataclass
class FloodResult:
    run_id: str
    summary: dict
    base_url: str
    cached: bool = False

    @property
    def flooded_area_km2(self) -> Optional[float]:
        return self.summary.get("flooded_area_km2")

    @property
    def max_depth_m(self) -> Optional[float]:
        return self.summary.get("max_depth_m")

    @property
    def building_loss_pkr(self) -> Optional[float]:
        return (self.summary.get("estimated_building_loss") or {}).get("total_loss_pkr")

    @property
    def report_url(self) -> str:
        return f"{self.base_url}/runs/{self.run_id}/report.html"

    @property
    def scene_url(self) -> str:
        return f"{self.base_url}/runs/{self.run_id}/mapbox_3d.html"


class SailabClient:
    """`SailabClient()` reads SAILAB_URL (default http://127.0.0.1:8000) and SAILAB_ACCESS_TOKEN from the environment.
    The token is only needed when the server is on another machine (the server exempts requests from itself)."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, timeout: float = 60.0):
        self.base_url = (base_url or os.environ.get("SAILAB_URL") or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("SAILAB_ACCESS_TOKEN", "")
        self.timeout = timeout
        self._s = requests.Session()
        if self.token:
            self._s.headers["Authorization"] = f"Bearer {self.token}"

    # ---- plumbing -------------------------------------------------------------------------------------------
    def _request(self, method: str, path: str, **kw) -> Any:
        kw.setdefault("timeout", self.timeout)
        try:
            r = self._s.request(method, self.base_url + path, **kw)
        except requests.ConnectionError as e:
            raise SailabUnavailable(f"cannot reach SAILAB at {self.base_url} ({e.__class__.__name__}); is the server running?") from e
        except requests.Timeout as e:
            raise SailabUnavailable(f"SAILAB at {self.base_url} did not answer within {kw['timeout']} s") from e
        if r.status_code == 401:
            raise SailabError("SAILAB rejected the access token (set SAILAB_ACCESS_TOKEN)")
        ctype = r.headers.get("content-type", "")
        data = r.json() if "json" in ctype else None
        if r.status_code >= 400:
            msg = (data or {}).get("error") if isinstance(data, dict) else None
            raise SailabError(msg or f"{method} {path} failed with HTTP {r.status_code}", data if isinstance(data, dict) else None)
        return data if data is not None else r.content

    def get(self, path: str, **kw) -> Any:
        return self._request("GET", path, **kw)

    def post(self, path: str, json: Optional[dict] = None, **kw) -> Any:
        return self._request("POST", path, json=json, **kw)

    def health(self) -> bool:
        """True when the server answers. Never raises."""
        try:
            self.get("/solver-status", timeout=5)
            return True
        except SailabError:
            return False

    def solver_status(self) -> dict:
        """Who holds the single physics solver and who is waiting."""
        return self.get("/solver-status")

    # ---- jobs -----------------------------------------------------------------------------------------------
    def wait_job(self, job_id: str, poll: float = 3.0, timeout: float = 3600.0) -> dict:
        """Waits for a background job (/climate-jobs/<id>) and returns its `result`."""
        end = time.time() + timeout
        while True:
            d = self.get(f"/climate-jobs/{job_id}/progress.json")
            st = d.get("status")
            if st == "done":
                return d.get("result", d)
            if st in ("failed", "error", "cancelled") or d.get("error"):
                raise SailabError(d.get("error") or f"job {job_id} {st}", d)
            if time.time() > end:
                raise SailabError(f"job {job_id} still running after {timeout:.0f} s")
            time.sleep(poll)

    # ---- the physics run ------------------------------------------------------------------------------------
    def start_flood(self, geometry: dict, rain_mm_hr: float = 40.0, duration_hr: float = 2.0, **params) -> dict:
        """Starts a full LISFLOOD-FP run and returns {"run_id", "cached"} at once. `params` are any /run fields
        (friction, infiltration_mm_hr, amc, include_buildings, inflows, rainfall_series, use_global_dem, ...)."""
        body = {"geometry": geometry, "rain_mm_hr": rain_mm_hr, "duration_hr": duration_hr, **params}
        return self.post("/run", body)

    def wait_flood(self, run_id: str, poll: float = 5.0, timeout: float = 6 * 3600.0, on_progress=None) -> FloodResult:
        end = time.time() + timeout
        while True:
            d = self.get(f"/runs/{run_id}/progress.json")
            st = d.get("status")
            if on_progress:
                on_progress(d)
            if st == "done":
                return FloodResult(run_id, d.get("summary", {}), self.base_url)
            if st in ("failed", "cancelled"):
                raise SailabError(f"run {run_id} {st}: {(d.get('log_tail') or '')[-400:]}", d)
            if time.time() > end:
                raise SailabError(f"run {run_id} still running after {timeout:.0f} s")
            time.sleep(poll)

    def run_flood(self, geometry: dict, rain_mm_hr: float = 40.0, duration_hr: float = 2.0, *,
                  wait: bool = True, poll: float = 5.0, timeout: float = 6 * 3600.0, on_progress=None, **params):
        """Runs the full physics model. With wait=True (default) returns a FloodResult; otherwise the run id."""
        started = self.start_flood(geometry, rain_mm_hr, duration_hr, **params)
        run_id = started["run_id"]
        if not wait:
            return run_id
        res = self.wait_flood(run_id, poll, timeout, on_progress)
        res.cached = bool(started.get("cached"))
        return res

    def cancel_flood(self, run_id: str) -> dict:
        return self.post(f"/runs/{run_id}/cancel")

    # ---- fast tiers and other pathways ----------------------------------------------------------------------
    def instant_flood(self, geometry: dict, discharge_m3s: float, **params) -> dict:
        """Sub-second terrain-index (HAND) flood extent for a given river discharge. An approximation, not the full model."""
        return self.post("/area/instant-flood", {"geometry": geometry, "discharge_m3s": discharge_m3s, **params})

    def coastal_flood(self, geometry: dict, return_period_years: int = 100, **params) -> dict:
        """Static coastal flood for the Pakistan coast (levels are above mean sea level)."""
        return self.post("/area/coastal-flood", {"geometry": geometry, "return_period_years": return_period_years, **params})

    def coastal_dynamic(self, geometry: dict, *, wait: bool = True, **params):
        """Physics-based storm-surge run. `tide_phase` may be spring_high, typical_high or low."""
        job = self.post("/area/coastal-flood-dynamic", {"geometry": geometry, **params})["job_id"]
        return self.wait_job(job) if wait else job

    def glof_discharge(self, **body) -> dict:
        """Peak discharge of a glacial-lake outburst: pass lake_key="shishper_2022", volume_m3=..., hydrolakes_id=..., or pmd_lake_id=..."""
        return self.post("/glof/discharge", body)

    def rainfall_idf(self, geometry: dict, **params) -> dict:
        return self.post("/area/idf-curve", {"geometry": geometry, **params})

    def validate_observed(self, run_id: str, path: str, wet_threshold_m: float = 0.05) -> dict:
        """Scores a finished run against an observed flood extent file (polygons or GeoTIFF): CSI, POD, FAR, F1, bias."""
        with open(path, "rb") as fh:
            return self._request("POST", f"/runs/{run_id}/validate-observed", files={"file": fh},
                                 data={"wet_threshold_m": str(wet_threshold_m)})

    # ---- discovery ------------------------------------------------------------------------------------------
    def search_tehsils(self, q: str, limit: int = 10) -> list:
        return self.get("/tehsils/search", params={"q": q, "limit": limit})

    def tehsil_geometry(self, tehsil_id: int) -> dict:
        return self.get(f"/tehsils/{tehsil_id}")

    def basins(self, region: str = "gilgit_baltistan", **params) -> Any:
        return self.get("/basins/search", params={"region": region, **params})

    def download_report(self, run_id: str, dest: str, pdf: bool = True) -> str:
        data = self.get(f"/runs/{run_id}/report.{'pdf' if pdf else 'html'}", timeout=300)
        with open(dest, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else str(data).encode())
        return dest
