import numpy as np
import pytest
import coastal_utils as cu
import tide_utils as tu

pytestmark = pytest.mark.skipif(not (tu.available() and cu.gtsr_available()), reason="needs the tide gauge and GTSR data")


def _station():
    return cu.nearest_gtsr_station(67.0, 24.8)["index"]


def test_total_target_is_reached_exactly_and_never_exceeded():
    for ph in tu.TIDE_PHASES:
        s = cu.build_tide_surge_series(_station(), 3, ph, total_peak_target_m=1.9)
        assert max(s["levels_m"]) == pytest.approx(1.9, abs=0.01), ph
        assert s["times_hr"][1] - s["times_hr"][0] == 1.0                       # hourly


def test_surge_peak_mode_adds_surge_to_tide():
    s = cu.build_tide_surge_series(_station(), 3, "spring_high", surge_peak_m=0.6)
    assert s["surge_peak_used_m"] == 0.6
    assert s["tide_at_surge_peak_m"] > 1.0                                       # spring high water
    low = cu.build_tide_surge_series(_station(), 3, "low", surge_peak_m=0.6)
    assert low["tide_at_surge_peak_m"] < 0 < s["tide_at_surge_peak_m"]


def test_worst_case_phase_needs_less_surge_for_the_same_total():
    hi = cu.build_tide_surge_series(_station(), 3, "spring_high", total_peak_target_m=1.9)
    lo = cu.build_tide_surge_series(_station(), 3, "low", total_peak_target_m=1.9)
    assert hi["surge_peak_used_m"] < lo["surge_peak_used_m"]


def test_exactly_one_anchor_required():
    with pytest.raises(ValueError):
        cu.build_tide_surge_series(_station(), 3, "low")
