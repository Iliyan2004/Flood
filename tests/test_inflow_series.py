import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model


def test_normalize_sets_duration_and_peak_from_series():
    inf = {"series": {"times_hr": [1, 2, 3], "q_m3s": [1.0, 5.0, 2.0]}}
    t, q = flood_model.normalize_inflow_series(inf)
    assert inf["duration_hr"] == 3.0 and inf["peak_m3s"] == 5.0
    assert (t, q) == ([1, 2, 3], [1.0, 5.0, 2.0])


def test_normalize_ordinary_triangular_inflow_untouched():
    inf = {"peak_m3s": 10, "duration_hr": 4}
    assert flood_model.normalize_inflow_series(inf) is None
    assert inf == {"peak_m3s": 10, "duration_hr": 4}


@pytest.mark.parametrize("series", [
    {"times_hr": [1], "q_m3s": [1]},                    # too short
    {"times_hr": [1, 2], "q_m3s": [1]},                 # length mismatch
    {"times_hr": [2, 1], "q_m3s": [1, 1]},              # not increasing
    {"times_hr": [1, 1], "q_m3s": [1, 1]},              # duplicate time
    {"times_hr": [1, 2], "q_m3s": [1, -1]},             # negative discharge
])
def test_normalize_rejects_invalid_series(series):
    with pytest.raises(ValueError):
        flood_model.normalize_inflow_series({"series": series})


def test_bdy_block_series_flux_is_discharge_over_span_and_holds_last_value():
    txt = flood_model.bdy_block_series("r1", [1.0, 2.0], [10.0, 20.0], sim_hours=5.0, span_m=10.0)
    lines = txt.strip().splitlines()
    assert lines[1] == "r1" and lines[2].startswith("4 ")           # 0h head point + 2 pts + held tail
    pts = [tuple(map(float, l.split())) for l in lines[3:]]
    assert pts[0] == (1.0, 0.0)        # series starting after t=0 is extended back to 0 at its first value
    assert pts[1] == (1.0, 1.0) and pts[2] == (2.0, 2.0)
    assert pts[3] == (2.0, 5.0)        # last value held until the end of the run
    assert [p[1] for p in pts] == sorted(set(p[1] for p in pts))    # strictly increasing times


def test_bdy_block_series_no_tail_point_when_series_reaches_sim_end():
    txt = flood_model.bdy_block_series("r1", [0.0, 3.0], [1.0, 1.0], sim_hours=3.0, span_m=1.0)
    assert txt.strip().splitlines()[2].startswith("2 ")
