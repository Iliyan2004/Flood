"""
Fast, pure-math tests for sediment_utils.py's real Meyer-Peter & Muller
(1948) bedload-transport formula and the robust_peak percentile fix.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sediment_utils  # noqa: E402


def test_mpm_below_critical_shields_no_transport():
    """Below the critical Shields number, real incipient-motion physics
    says NOTHING moves -- phi (and hence qb) must be exactly 0, not just
    small."""
    d50_m = 0.01  # 10mm gravel
    tiny_tau = 0.01  # far below critical shear for this grain size
    qb, theta = sediment_utils.mpm_bedload_transport(tiny_tau, d50_m)
    assert qb == 0.0
    assert theta < sediment_utils.SHIELDS_CRITICAL


def test_mpm_transport_increases_with_shear():
    """Real physics: above critical shear, transport capacity increases
    monotonically with applied shear stress."""
    d50_m = 0.01
    taus = [5.0, 10.0, 20.0, 40.0]
    qbs = [sediment_utils.mpm_bedload_transport(t, d50_m)[0] for t in taus]
    assert all(b > a for a, b in zip(qbs, qbs[1:]))


def test_mpm_finer_sediment_transports_more_at_same_shear():
    """Finer material (smaller d50) is easier to entrain at a given shear
    stress than coarser material -- a basic sanity check on the formula's
    real physical behavior, not just that it runs."""
    tau = 15.0
    qb_fine, _ = sediment_utils.mpm_bedload_transport(tau, d50_m=0.001)
    qb_coarse, _ = sediment_utils.mpm_bedload_transport(tau, d50_m=0.02)
    assert qb_fine > qb_coarse


def test_robust_peak_regression_single_outlier_cell():
    """Real known-bug regression: a single boundary-injection-artifact
    cell with a physically absurd value must not dominate the reported
    'how bad did this get' headline the way a plain np.max() would.
    robust_peak (99th percentile among transporting cells) should stay
    close to the bulk of real values even with one extreme outlier,
    while still being far below the true max -- pinning down exactly the
    behavior this function exists for."""
    rng = np.random.default_rng(0)
    values = rng.uniform(1.0, 5.0, size=1000)
    values[500] = 1e9  # single absurd artifact cell
    moving_mask = np.ones(1000, dtype=bool)

    true_max = values.max()
    robust = sediment_utils.robust_peak(values, moving_mask, pct=99.0)

    assert robust < 10.0  # nowhere near the true max
    assert robust < true_max / 1000.0
    # And it should be a real, non-degenerate percentile of the bulk data,
    # not just clamped to 0.
    assert robust > 0.0


def test_robust_peak_no_moving_cells_returns_zero():
    values = np.array([1.0, 2.0, 3.0])
    moving_mask = np.zeros(3, dtype=bool)
    assert sediment_utils.robust_peak(values, moving_mask) == 0.0


def test_classify_transport_risk_bands_are_ordered_and_complete():
    """band_counts must always account for every cell exactly once
    (no gap/double-count), same discipline CLAUDE.md documents for the
    LULC per-cell classification elsewhere in this app."""
    rng = np.random.default_rng(1)
    qb_peak = rng.uniform(0, 10, size=(20, 20))
    qb_peak[qb_peak < 3] = 0.0  # some cells transport nothing
    wet_mask = np.ones((20, 20), dtype=bool)
    band, counts = sediment_utils.classify_transport_risk(qb_peak, wet_mask)
    assert sum(counts.values()) == qb_peak.size
    assert band.min() >= 0 and band.max() <= 4
