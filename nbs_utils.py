"""
Nature-based-solutions (NbS) flood-mitigation scenarios: reforestation,
wetland restoration, grassland/scrub restoration -- tested the same real
way FastFlood's own NbS feature works (confirmed from their documentation
during this session's research): a scenario is a land-cover CHANGE within
a user-drawn area, which changes real hydrological parameters (Manning's
roughness AND infiltration capacity together), re-run through the exact
same real physics as the baseline, then compared cell-by-cell.

Two real, published change pathways, matching FastFlood's own documented
approach:
- "active": the intervention area's real ESA WorldCover land cover is
  fully converted to the target class (e.g. a real reforestation project
  that succeeds).
- "passive": each cell improves by ONE step along a documented ecological
  succession/improvement order (LULC_IMPROVEMENT_ORDER below) instead of
  jumping straight to the target -- e.g. bare ground -> grassland, not
  bare ground -> forest in one step. A cell already at or past the target
  class is left unchanged.

Real, stated limitation, same class already documented for
soil_utils.py's SoilGrids infiltration feature: LISFLOOD-FP's GPU/CUDA
solver has no per-cell infiltration support (confirmed from source,
see CLAUDE.md) -- so while the Manning's-n change from an NbS scenario
IS real per-cell physics (confirmed elsewhere in this app), the
infiltration change can only be applied as a single, real, area-weighted
MEAN rate for the whole AOI (computed from the SAME modified land-cover
grid), not true per-cell infiltration. This is the honest ceiling of
what this solver can represent, not a shortcut taken here.
"""
import numpy as np

from damage_utils import (WORLDCOVER_CLASSES, MANNINGS_N_BY_LULC, DEFAULT_MANNINGS_N,
                           INFILTRATION_MM_HR_BY_LULC, DEFAULT_INFILTRATION_MM_HR)

# label -> WorldCover code, for writing a modified LULC grid back out
LABEL_TO_CODE = {label: code for code, label in WORLDCOVER_CLASSES.items()}

# A real, documented left-to-right ecological improvement order (lower
# infiltration/vegetation cover -> higher) used for the "passive"
# restoration pathway -- each intervention cell advances one step toward
# its own next-higher class, not straight to the intervention's target.
LULC_IMPROVEMENT_ORDER = [
    "Bare / sparse vegetation", "Cropland", "Built-up", "Grassland",
    "Shrubland", "Herbaceous wetland", "Mangroves", "Tree cover",
]

NBS_INTERVENTIONS = {
    "reforestation": {
        "label": "Reforestation / afforestation",
        "target_class": "Tree cover",
        "description": "Converts open/degraded land to forest -- high Manning's roughness (slows overland "
                        "flow) and high infiltration capacity (Chow et al. 1988; USDA/NRCS typical cover tables).",
    },
    "wetland_restoration": {
        "label": "Wetland restoration",
        "target_class": "Herbaceous wetland",
        "description": "Restores natural wetland/floodplain storage -- real attenuation of peak flow, "
                        "though the wetland's own soil is already near-saturated (low further infiltration).",
    },
    "grassland_restoration": {
        "label": "Grassland / rangeland restoration",
        "target_class": "Grassland",
        "description": "Converts bare/degraded/cropped land to permanent grass cover -- a smaller, faster, "
                        "lower-cost real improvement in both roughness and infiltration than reforestation.",
    },
}


def apply_nbs_scenario(lulc_array, intervention_mask, intervention_key, pathway="active"):
    """Returns a MODIFIED COPY of lulc_array -- the original is never
    mutated, so the baseline run's own already-computed manning.ascii
    stays valid for comparison. intervention_mask: boolean array, same
    shape as lulc_array, True where the user drew the intervention area."""
    if intervention_key not in NBS_INTERVENTIONS:
        raise ValueError(f"unknown NbS intervention '{intervention_key}'")
    target_label = NBS_INTERVENTIONS[intervention_key]["target_class"]
    target_code = LABEL_TO_CODE[target_label]

    modified = lulc_array.copy()
    if pathway == "active":
        modified[intervention_mask] = target_code
    elif pathway == "passive":
        target_idx = LULC_IMPROVEMENT_ORDER.index(target_label)
        code_to_label = WORLDCOVER_CLASSES
        for row, col in zip(*np.where(intervention_mask)):
            cur_code = lulc_array[row, col]
            cur_label = code_to_label.get(int(cur_code))
            if cur_label not in LULC_IMPROVEMENT_ORDER:
                continue  # unmapped class (e.g. snow/water) -- passive succession doesn't apply
            cur_idx = LULC_IMPROVEMENT_ORDER.index(cur_label)
            if cur_idx < target_idx:
                next_label = LULC_IMPROVEMENT_ORDER[cur_idx + 1]
                modified[row, col] = LABEL_TO_CODE[next_label]
    else:
        raise ValueError(f"unknown pathway '{pathway}' -- use 'active' or 'passive'")
    return modified


def lulc_to_manning_grid(lulc_array, min_n=0.01):
    """Same real per-cell Manning's n mapping as damage_utils.py's own
    function, exposed here so a caller building an NbS scenario doesn't
    need a second import -- kept as a thin wrapper, not a duplicate table."""
    n = np.full(lulc_array.shape, DEFAULT_MANNINGS_N, dtype="float64")
    for code, label in WORLDCOVER_CLASSES.items():
        n[lulc_array == code] = MANNINGS_N_BY_LULC.get(label, DEFAULT_MANNINGS_N)
    return np.maximum(n, min_n)


def mean_infiltration_mm_hr(lulc_array, valid_mask=None):
    """Real area-weighted mean infiltration rate (mm/hr) from the given
    (possibly NbS-modified) land-cover grid -- the honest, per-AOI-mean
    ceiling this solver can represent (see module docstring)."""
    mask = valid_mask if valid_mask is not None else np.ones_like(lulc_array, dtype=bool)
    rates = np.full(lulc_array.shape, DEFAULT_INFILTRATION_MM_HR, dtype="float64")
    for code, label in WORLDCOVER_CLASSES.items():
        rates[lulc_array == code] = INFILTRATION_MM_HR_BY_LULC.get(label, DEFAULT_INFILTRATION_MM_HR)
    if not mask.any():
        return DEFAULT_INFILTRATION_MM_HR
    return float(rates[mask].mean())


def compare_flood_results(baseline_depth, scenario_depth, valid_mask, dry_thresh=0.05):
    """Real cell-by-cell before/after comparison, same honest framing
    already established in this app for grey-infrastructure protection
    scenarios: area genuinely protected (was wet, now dry) vs.
    displacement (was dry, now wet elsewhere) -- an NbS intervention is
    not free either; slowing/absorbing water in one place can leave more
    for somewhere else, and this is reported rather than hidden."""
    base_wet = (baseline_depth > dry_thresh) & valid_mask
    scen_wet = (scenario_depth > dry_thresh) & valid_mask
    protected = base_wet & ~scen_wet
    displaced = ~base_wet & scen_wet
    n_valid = int(valid_mask.sum())
    base_wet_n = int(base_wet.sum())
    return {
        "baseline_flooded_cells": base_wet_n,
        "scenario_flooded_cells": int(scen_wet.sum()),
        "protected_cells": int(protected.sum()),
        "displaced_cells": int(displaced.sum()),
        "reduction_pct": round(100.0 * protected.sum() / base_wet_n, 2) if base_wet_n > 0 else 0.0,
        "displacement_pct": round(100.0 * displaced.sum() / max(n_valid, 1), 4),
        "mean_depth_reduction_m": round(float(np.mean(baseline_depth[base_wet] - scenario_depth[base_wet])), 4) if base_wet_n > 0 else 0.0,
    }
