"""
Fast, pure-math tests for aal_utils.py's real trapezoidal Average Annual
Loss integration. No physics/network -- feeds synthetic (return period,
loss) points directly.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aal_utils  # noqa: E402


def test_requires_at_least_two_points():
    with pytest.raises(ValueError):
        aal_utils.compute_aal([{"return_period_years": 10, "loss_pkr": 100}])


def test_rejects_non_positive_return_period():
    with pytest.raises(ValueError):
        aal_utils.compute_aal([
            {"return_period_years": 0, "loss_pkr": 100},
            {"return_period_years": 10, "loss_pkr": 200},
        ])


def test_constant_loss_across_all_return_periods_matches_hand_computed_value():
    """A constant real loss across every tested return period does NOT
    give back that same constant AAL -- the high-frequency tail (more
    frequent than the smallest real return period tested) is, by the
    real documented method, assumed to decay linearly to zero by p=1,
    not stay flat. Verified here against the real closed-form result for
    a flat L: AAL = L * 0.5 * (1 + q_max), where q_max = 1/T_min."""
    L = 500.0
    periods = (2, 5, 10, 25, 50, 100)
    points = [{"return_period_years": t, "loss_pkr": L} for t in periods]
    result = aal_utils.compute_aal(points)
    q_max = 1.0 / min(periods)
    expected = L * 0.5 * (1.0 + q_max)
    assert result["aal_pkr"] == pytest.approx(round(expected, 2), rel=1e-9)


def test_aal_increases_with_higher_losses():
    low = [{"return_period_years": t, "loss_pkr": 100.0 * i} for i, t in enumerate((10, 50, 100), start=1)]
    high = [{"return_period_years": t, "loss_pkr": 1000.0 * i} for i, t in enumerate((10, 50, 100), start=1)]
    assert aal_utils.compute_aal(high)["aal_pkr"] > aal_utils.compute_aal(low)["aal_pkr"]


def test_two_point_hand_computed_value():
    """Real hand-computed check on the smallest real case (2 points):
    T=10 (p=0.1, L=1000), T=100 (p=0.01, L=5000).
    AAL = tail-below(L_at_largest_T * p_at_largest_T)
        + trapezoid(between the two real points)
        + tail-above(0.5 * L_at_smallest_T * (1 - p_at_smallest_T))"""
    points = [{"return_period_years": 10, "loss_pkr": 1000.0},
              {"return_period_years": 100, "loss_pkr": 5000.0}]
    result = aal_utils.compute_aal(points)
    q1, q2 = 0.01, 0.1  # ascending probability order: T=100 first, then T=10
    L1, L2 = 5000.0, 1000.0
    expected = L1 * q1 + 0.5 * (L1 + L2) * (q2 - q1) + 0.5 * L2 * (1 - q2)
    assert result["aal_pkr"] == pytest.approx(round(expected, 2), rel=1e-9)


def test_points_sorted_and_exceedance_probability_attached():
    points = [{"return_period_years": 100, "loss_pkr": 5000.0},
              {"return_period_years": 10, "loss_pkr": 1000.0}]
    result = aal_utils.compute_aal(points)
    rps = [p["return_period_years"] for p in result["points"]]
    assert rps == [10, 100]  # sorted ascending regardless of input order
    probs = {p["return_period_years"]: p["annual_exceedance_probability"] for p in result["points"]}
    assert probs[10] == pytest.approx(0.1)
    assert probs[100] == pytest.approx(0.01)


def test_input_order_does_not_affect_result():
    a = [{"return_period_years": 10, "loss_pkr": 1000.0}, {"return_period_years": 100, "loss_pkr": 5000.0}]
    b = list(reversed(a))
    assert aal_utils.compute_aal(a)["aal_pkr"] == aal_utils.compute_aal(b)["aal_pkr"]


def test_zero_loss_everywhere_gives_zero_aal():
    points = [{"return_period_years": t, "loss_pkr": 0.0} for t in (10, 50, 100)]
    assert aal_utils.compute_aal(points)["aal_pkr"] == 0.0
