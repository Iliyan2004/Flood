"""
Fast, pure-math tests for damage_utils.py's Huizinga et al. 2017 (JRC)
depth-damage curves and the real valid_mask dilution bug this session
found and fixed (see CLAUDE.md's "Four findings from an independent
errors review" entry, finding #1).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import damage_utils  # noqa: E402


def _built_up_code():
    for code, label in damage_utils.WORLDCOVER_CLASSES.items():
        if label == "Built-up":
            return code
    pytest.skip("no Built-up class in WORLDCOVER_CLASSES")


def test_valid_mask_regression_matches_claude_md_numbers():
    """Reproduces the exact synthetic case documented in CLAUDE.md: a 4x4
    grid all classified 'Built-up', where only the top-left 2x2 quadrant
    is the TRUE drawn AOI (valid_mask) and the rest is dead out-of-AOI
    terrain that happens to read as 0-depth. Without valid_mask,
    flooded_pct is silently diluted to the documented 25% (4 of 16 cells
    are both valid AND actually flooded... using the exact CLAUDE.md
    setup: all 4 real AOI cells flooded, 12 dead cells read as dry) --
    WITH valid_mask, flooded_pct must read the true 100%. A regression
    that drops (or ignores) valid_mask again would silently fail this
    exact assertion."""
    code = _built_up_code()
    lulc = np.full((4, 4), code, dtype="uint8")

    max_depth = np.zeros((4, 4), dtype="float64")
    valid_mask = np.zeros((4, 4), dtype=bool)
    valid_mask[0:2, 0:2] = True
    max_depth[0:2, 0:2] = 1.0  # every real AOI cell is genuinely flooded 1m deep

    damage_diluted, summary_diluted = damage_utils.compute_damage(lulc, max_depth, valid_mask=None)
    damage_fixed, summary_fixed = damage_utils.compute_damage(lulc, max_depth, valid_mask=valid_mask)

    assert summary_diluted["Built-up"]["flooded_pct"] == pytest.approx(25.0)
    assert summary_fixed["Built-up"]["flooded_pct"] == pytest.approx(100.0)
    assert summary_fixed["Built-up"]["total_cells"] == 4
    assert summary_diluted["Built-up"]["total_cells"] == 16


def test_valid_mask_never_makes_stats_worse_than_true_signal():
    """A more general property test (not just the one pinned CLAUDE.md
    case): for any random valid/invalid split, mean_damage_fraction_overall
    computed WITH the correct valid_mask must equal the true mean over
    just the real AOI cells -- not diluted by any dead cells outside it."""
    code = _built_up_code()
    rng = np.random.default_rng(3)
    lulc = np.full((10, 10), code, dtype="uint8")
    max_depth = rng.uniform(0, 2.0, size=(10, 10))
    valid_mask = rng.uniform(size=(10, 10)) > 0.4
    # Zero out depth at invalid cells, simulating dead/out-of-AOI terrain
    # reading as "0 = dry" the way the real DEM crop does.
    max_depth_with_dead = np.where(valid_mask, max_depth, 0.0)

    _, summary = damage_utils.compute_damage(lulc, max_depth_with_dead, valid_mask=valid_mask)
    expected_mean = float(
        np.interp(np.clip(max_depth[valid_mask], 0, None),
                  damage_utils.DEPTH_DAMAGE_CURVES_ASIA["Built-up"]["depths"],
                  damage_utils.DEPTH_DAMAGE_CURVES_ASIA["Built-up"]["damage"]).mean()
    )
    assert summary["Built-up"]["mean_damage_fraction_overall"] == pytest.approx(expected_mean, rel=1e-6)


def test_compute_damage_per_class_cell_counts_sum_to_total():
    """Same discipline CLAUDE.md documents verifying manually: per-class
    cell counts must sum exactly to the number of valid cells -- no
    gap/double-count in the classification."""
    rng = np.random.default_rng(5)
    codes = list(damage_utils.WORLDCOVER_CLASSES.keys())
    lulc = rng.choice(codes, size=(15, 15)).astype("uint8")
    max_depth = rng.uniform(0, 1.5, size=(15, 15))
    valid_mask = np.ones((15, 15), dtype=bool)
    valid_mask[0, :] = False  # one dead row

    _, summary = damage_utils.compute_damage(lulc, max_depth, valid_mask=valid_mask)
    total_counted = sum(c["total_cells"] for c in summary.values())
    assert total_counted == int(valid_mask.sum())
