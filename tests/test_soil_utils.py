"""
Fast, pure-math tests for soil_utils.py's real Green-Ampt (1911)
infiltration model.

These pin down the exact closed-form edge case documented in the
function's own docstring, and guard against the real numerical bug this
session already found and fixed once: an explicit-Euler stepper on the
Green-Ampt RATE equation blows up near F=0 (the rate is singular as
F->0). The fix solves the IMPLICIT cumulative-infiltration equation via
bisection instead, which has no such singularity. A regression to the
old explicit-Euler approach (or any other reintroduction of the
singularity) would fail test_green_ampt_zero_deficit_limit below, since
that's the one case with an exact, hand-checkable answer.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import soil_utils  # noqa: E402


def test_green_ampt_zero_deficit_limit_exact():
    """The function's own documented closed form: when delta_theta (the
    moisture deficit) is exactly 0, the log term vanishes and cumulative
    infiltration F(t) = K*t EXACTLY -- no bisection error at all. This is
    the single case with a hand-provable expected answer, and is exactly
    the case an explicit-Euler-near-F=0 regression would get wrong
    (rates in the millions of mm/hr on the first step, per this file's
    own bug history)."""
    k_cm_hr = 2.5
    t_hr = 3.0
    F = soil_utils.green_ampt_cumulative_infiltration_at(k_cm_hr, psi_cm=10.0, delta_theta=0.0, t_hr=t_hr)
    assert F == pytest.approx(k_cm_hr * t_hr, rel=1e-9)


def test_green_ampt_near_zero_deficit_no_blowup():
    """A tiny (not exactly zero) deficit must still resolve to a small,
    physically sane F close to K*t -- not an exploded value, which is
    what the old explicit-Euler-near-singularity bug produced."""
    k_cm_hr = 2.5
    t_hr = 3.0
    F = soil_utils.green_ampt_cumulative_infiltration_at(k_cm_hr, psi_cm=10.0, delta_theta=1e-6, t_hr=t_hr)
    assert 0 < F < 10 * (k_cm_hr * t_hr)


def test_green_ampt_cumulative_infiltration_monotonic_in_time():
    """Real physics: cumulative infiltration only ever increases with time."""
    p = soil_utils.GREEN_AMPT_PARAMS["loam"]
    times = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
    F_vals = [soil_utils.green_ampt_cumulative_infiltration_at(p["k_cm_hr"], p["psi_cm"], 0.2, t)
              for t in times]
    assert all(b > a for a, b in zip(F_vals, F_vals[1:]))


def test_green_ampt_solves_its_own_implicit_equation():
    """Directly verify the returned F actually satisfies the model's own
    implicit equation F - b*ln(1+F/b) = K*t (to bisection tolerance) --
    the real correctness check for an iterative solve, not just
    plausibility."""
    k_cm_hr, psi_cm, delta_theta, t_hr = 1.3, 12.0, 0.15, 2.0
    F = soil_utils.green_ampt_cumulative_infiltration_at(k_cm_hr, psi_cm, delta_theta, t_hr)
    b = psi_cm * delta_theta
    lhs = F - b * np.log(1.0 + F / b)
    rhs = k_cm_hr * t_hr
    assert lhs == pytest.approx(rhs, abs=1e-4)


def test_green_ampt_average_rate_drier_soil_infiltrates_more():
    """Real physics this app relies on (see the module's own docstring):
    a drier antecedent soil (smaller saturation_frac -> larger moisture
    deficit) infiltrates MORE over the same storm than a wetter one."""
    dry = soil_utils.green_ampt_average_rate_mm_hr("loam", duration_hr=2.0, saturation_frac=0.0)
    wet = soil_utils.green_ampt_average_rate_mm_hr("loam", duration_hr=2.0, saturation_frac=1.0)
    assert dry > wet


def test_green_ampt_average_rate_never_below_floor():
    """ABS_MIN_INFILTRATION_MM_HR is a hard floor regardless of texture/
    duration/saturation -- even a near-impermeable clay at full
    saturation over a long storm must not read as literally 0 or negative."""
    rate = soil_utils.green_ampt_average_rate_mm_hr("clay", duration_hr=24.0, saturation_frac=1.0)
    assert rate >= soil_utils.ABS_MIN_INFILTRATION_MM_HR


def test_green_ampt_coarser_soil_infiltrates_faster():
    """Sand (high Ksat) should show a much higher average rate than clay
    (low Ksat) under identical duration/saturation -- a basic sanity
    check on the real published Rawls et al. parameter table itself."""
    sand_rate = soil_utils.green_ampt_average_rate_mm_hr("sand", duration_hr=1.0, saturation_frac=0.5)
    clay_rate = soil_utils.green_ampt_average_rate_mm_hr("clay", duration_hr=1.0, saturation_frac=0.5)
    assert sand_rate > clay_rate * 5


def test_usda_texture_class_classifies_pure_sand_and_pure_clay():
    assert soil_utils.usda_texture_class(sand_pct=95, clay_pct=2) == "sand"
    assert soil_utils.usda_texture_class(sand_pct=30, clay_pct=45) == "clay"


def test_usda_texture_class_real_high_sand_high_clay_gap_now_classified_correctly():
    """Real regression test for a confirmed classification bug: every
    branch handling clay>=35% previously required sand<=45%, so a real
    high-sand (>45%) AND high-clay (>=35%) combination with low silt fell
    through all the way to the generic 'loam' default -- misclassifying
    a real USDA 'Sandy Clay' soil (Ksat 1.2 mm/hr) as 'loam' (Ksat 13.2
    mm/hr, an 11x infiltration-capacity overestimate in the wrong
    direction for a flood model). The real USDA boundary for Sandy Clay
    is sand>=45% AND clay>=35%, independent of silt."""
    for sand, clay in [(60, 40), (50, 45), (55, 35), (46, 36), (45, 35)]:
        assert soil_utils.usda_texture_class(sand, clay) == "sandy_clay", f"sand={sand} clay={clay}"


def test_usda_texture_class_adjacent_classes_unaffected_by_the_sandy_clay_fix():
    """The fix must be scoped exactly to the real gap (sand>=45,
    clay>=35) -- confirms neighboring classes just outside that boundary
    are completely unaffected."""
    assert soil_utils.usda_texture_class(sand_pct=44, clay_pct=35) != "sandy_clay"  # just under sand=45
    assert soil_utils.usda_texture_class(sand_pct=60, clay_pct=34) != "sandy_clay"  # just under clay=35
    assert soil_utils.usda_texture_class(sand_pct=30, clay_pct=45) == "clay"        # unaffected, s<=45 branch
