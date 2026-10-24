"""Optional adapter: exposes SAILAB's flood model as a SimCity `Simulator` (see simcity/core/contract.py there).

It does not import SimCity at module load; it uses SimCity's own `Metric`/`Lever` classes when SimCity is on the path
and plain equivalents otherwise, so this package stays installable on its own."""
from __future__ import annotations

from typing import Any, Optional

from .client import SailabClient

try:  # use SimCity's classes when available
    from simcity.core.contract import Lever, Metric, Simulator as _Base  # type: ignore
except Exception:  # standalone fallback with the same fields
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Metric:  # type: ignore[no-redef]
        key: str
        label: str
        unit: str
        direction: str

    @dataclass(frozen=True)
    class Lever:  # type: ignore[no-redef]
        key: str
        label: str
        kind: str
        default: Any
        low: Optional[float] = None
        high: Optional[float] = None
        choices: tuple = ()

    _Base = object  # type: ignore[assignment,misc]


class SailabFloodSimulator(_Base):  # type: ignore[misc,valid-type]
    """Scores a design by running SAILAB's full 2D flood model over the scenario's area.

    Slow (seconds to minutes per call; identical inputs are cached by the server). For an optimisation loop, use
    `fast=True`, which uses the sub-second terrain-index preview instead, and re-check the finalists with `fast=False`."""

    id = "sailab_flood"
    name = "SAILAB 2D flood model"
    metrics = (
        Metric("flooded_area_km2", "Flooded area", "km2", "min"),
        Metric("max_depth_m", "Maximum flood depth", "m", "min"),
        Metric("building_loss_pkr", "Estimated building loss", "PKR", "min"),
    )
    levers = (
        Lever("rain_mm_hr", "Peak rainfall", "float", 40.0, 1.0, 300.0),
        Lever("duration_hr", "Storm duration", "float", 2.0, 0.5, 72.0),
        Lever("friction", "Fallback Manning roughness", "float", 0.05, 0.01, 0.2),
        Lever("include_buildings", "Buildings block flow", "bool", True),
    )

    def __init__(self, client: Optional[SailabClient] = None, fast: bool = False, discharge_m3s: float = 100.0):
        self.client = client or SailabClient()
        self.fast = fast
        self.discharge_m3s = discharge_m3s

    def evaluate(self, design: dict, scenario) -> dict:
        aoi = scenario.aoi
        if self.fast:
            r = self.client.instant_flood(aoi, float(design.get("discharge_m3s", self.discharge_m3s)))
            return {"flooded_area_km2": float(r.get("flooded_area_km2", r.get("area_km2", 0.0))),
                    "max_depth_m": float(r.get("max_depth_m", 0.0)), "building_loss_pkr": 0.0}
        params = {k: design[k] for k in ("friction", "include_buildings", "infiltration_mm_hr", "amc") if k in design}
        res = self.client.run_flood(aoi, float(design.get("rain_mm_hr", 40.0)), float(design.get("duration_hr", 2.0)), **params)
        return {"flooded_area_km2": float(res.flooded_area_km2 or 0.0), "max_depth_m": float(res.max_depth_m or 0.0),
                "building_loss_pkr": float(res.building_loss_pkr or 0.0)}
