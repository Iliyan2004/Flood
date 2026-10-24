"""
Fast, pure-math tests for protection_optimizer.py's decision-variable
decoding -- no real physics run (that's covered by a live end-to-end
verification, not a fast test).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protection_optimizer  # noqa: E402
import protection_utils  # noqa: E402

BBOX = [73.0, 33.0, 73.1, 33.1]


def test_decode_produces_one_structure_per_requested_count():
    x = [0.5] * (7 * 2)
    structures = protection_optimizer._decode(x, 2, ["dam", "canal"], BBOX)
    assert len(structures) == 2


def test_decode_center_within_bbox():
    x = [0.5, 0.5, 0.0, 0.3, 1.0, 1.0, 0.0]
    structures = protection_optimizer._decode(x, 1, ["dam"], BBOX)
    line = structures[0]["line"]
    for lon, lat in line:
        assert BBOX[0] - 0.05 <= lon <= BBOX[2] + 0.05
        assert BBOX[1] - 0.05 <= lat <= BBOX[3] + 0.05


def test_decode_type_index_selects_correct_type():
    types = ["dam", "canal", "barrage"]
    x_dam = [0.5, 0.5, 0.0, 0.3, 1.0, 1.0, 0.0]
    x_canal = [0.5, 0.5, 0.0, 0.3, 1.0, 1.0, 0.4]
    x_barrage = [0.5, 0.5, 0.0, 0.3, 1.0, 1.0, 0.99]
    assert protection_optimizer._decode(x_dam, 1, types, BBOX)[0]["type"] == "dam"
    assert protection_optimizer._decode(x_canal, 1, types, BBOX)[0]["type"] == "canal"
    assert protection_optimizer._decode(x_barrage, 1, types, BBOX)[0]["type"] == "barrage"


def test_decode_height_and_width_scale_by_multiplier():
    x = [0.5, 0.5, 0.0, 0.3, 1.5, 0.5, 0.0]  # h_mult=1.5, w_mult=0.5
    structures = protection_optimizer._decode(x, 1, ["dam"], BBOX)
    cfg = protection_utils.STRUCTURE_TYPES["dam"]
    assert structures[0]["height_m"] == pytest.approx(cfg["default_height_m"] * 1.5)
    assert structures[0]["half_width_m"] == pytest.approx(cfg["default_half_width_m"] * 0.5)


def test_decode_length_scales_with_length_frac():
    import math
    x_short = [0.5, 0.5, 0.0, 0.15, 1.0, 1.0, 0.0]
    x_long = [0.5, 0.5, 0.0, 0.9, 1.0, 1.0, 0.0]
    s_short = protection_optimizer._decode(x_short, 1, ["dam"], BBOX)[0]["line"]
    s_long = protection_optimizer._decode(x_long, 1, ["dam"], BBOX)[0]["line"]

    def length(line):
        (x1, y1), (x2, y2) = line
        return math.hypot(x2 - x1, y2 - y1)

    assert length(s_long) > length(s_short)


def test_bounds_per_structure_has_seven_entries():
    assert len(protection_optimizer.BOUNDS_PER_STRUCTURE) == 7


def test_run_trial_adds_real_loss_fields_when_baseline_building_data_given(monkeypatch):
    """_run_trial should compute a real reduction_pct_of_loss/
    loss_avoided_pkr from the SAME baseline building features re-
    classified against the trial's own depth grid -- verified here by
    monkeypatching the real subprocess/file-read machinery with
    controlled fake data, not a live physics run."""
    import numpy as np
    import infra_utils

    header = {"ncols": 10, "nrows": 10, "cellsize": 1.0}
    baseline_depth = np.zeros((10, 10))
    valid_mask = np.ones((10, 10), dtype=bool)

    def fake_structures_affected_cells(*a, **kw):
        return 5  # non-zero -- structure lands inside the AOI

    def fake_apply_structures_to_dem(dem, *a, **kw):
        return dem

    def fake_compare_flood_results(baseline_depth, scenario_depth, valid_mask, **kw):
        return {"baseline_flooded_cells": 10, "scenario_flooded_cells": 8,
                "protected_cells": 2, "displaced_cells": 1,
                "reduction_pct": 20.0, "displacement_pct": 1.0, "mean_depth_reduction_m": 0.1}

    import protection_utils
    import nbs_utils
    monkeypatch.setattr(protection_utils, "structures_affected_cells", fake_structures_affected_cells)
    monkeypatch.setattr(protection_utils, "apply_structures_to_dem", fake_apply_structures_to_dem)
    monkeypatch.setattr(protection_optimizer.protection_utils, "write_ascii_grid", lambda *a, **kw: None)
    monkeypatch.setattr(nbs_utils, "compare_flood_results", fake_compare_flood_results)

    class FakeProc:
        returncode = 0

    def fake_run(cmd, **kw):
        # Write a real, minimal res.max file the function will read back.
        os.makedirs(os.path.join(cmd[cmd.index("--workdir") + 1], "results"), exist_ok=True)
        res_path = os.path.join(cmd[cmd.index("--workdir") + 1], "results", "res.max")
        with open(res_path, "w") as f:
            f.write("ncols 10\nnrows 10\nxllcorner 0\nyllcorner 0\ncellsize 1\nNODATA_value -9999\n")
            for _ in range(10):
                f.write(" ".join(["0.0"] * 10) + "\n")
        return FakeProc()

    monkeypatch.setattr(protection_optimizer.subprocess, "run", fake_run)

    # A real building fully dry at baseline (depth 0.0 everywhere) and
    # scored via a fixed damage_fraction on the baseline side so
    # loss_before_pkr > 0.
    baseline_features = [{"poly_rc": [(1, 1), (1, 3), (3, 3), (3, 1)], "height_m": 3.0, "damage_fraction": 0.5}]

    import threading
    ctx = {"header": header, "src_crs": "EPSG:32642", "baseline_dem": baseline_depth,
           "baseline_depth": baseline_depth, "valid_mask": valid_mask,
           "run_params": {"rain_mm_hr": 10, "duration_hr": 1, "friction": 0.05, "cfl": 0.7,
                           "infiltration_mm_hr": 0, "amc": 50},
           "venv_py": "python3", "flood_model_path": "flood_model.py",
           "sim_slot": threading.Semaphore(1), "scratch_dir": "/tmp/test_protect_opt_scratch",
           "baseline_building_features": baseline_features,
           "loss_before_pkr": 12345.0}

    structures = [{"type": "dam", "line": [[73.0, 33.0], [73.01, 33.0]], "height_m": 3.0, "half_width_m": 8.0}]
    result = protection_optimizer._run_trial(structures, ctx, 0)
    assert result is not None
    assert "loss_avoided_pkr" in result
    assert "reduction_pct_of_loss" in result
    # depth is all-zero in the fake res.max -> the re-classified building
    # is dry -> its damage_fraction drops to 0 -> loss_after is 0 ->
    # loss_avoided == the full loss_before.
    assert result["loss_avoided_pkr"] == pytest.approx(12345.0, abs=0.01)
    assert result["reduction_pct_of_loss"] == pytest.approx(100.0, abs=0.01)


def test_run_trial_logs_a_real_exception_instead_of_silently_swallowing_it(tmp_path, monkeypatch):
    """Real regression test for a previously-flagged observability gap:
    `_run_trial`'s broad `except Exception` used to return None on ANY
    failure with zero diagnostic trace, making a genuine programming bug
    indistinguishable from an expected real failure (a bad candidate
    structure, a solver crash). Confirms a real exception now gets
    appended to a persistent optimizer_errors.log in scratch_dir (which,
    unlike trial_dir, survives the trial's own cleanup)."""
    import threading
    import protection_utils

    def boom(*a, **kw):
        raise RuntimeError("a real, deliberate synthetic bug for this test")

    monkeypatch.setattr(protection_utils, "structures_affected_cells", boom)

    ctx = {"header": {"ncols": 10, "nrows": 10, "cellsize": 1.0}, "src_crs": "EPSG:32642",
           "baseline_dem": None, "run_params": {"rain_mm_hr": 10, "duration_hr": 1, "friction": 0.05,
                                                  "cfl": 0.7, "infiltration_mm_hr": 0, "amc": 50},
           "venv_py": "python3", "flood_model_path": "flood_model.py",
           "sim_slot": threading.Semaphore(1), "scratch_dir": str(tmp_path),
           "baseline_building_features": None, "loss_before_pkr": 0.0}

    structures = [{"type": "dam", "line": [[73.0, 33.0], [73.01, 33.0]], "height_m": 3.0, "half_width_m": 8.0}]
    result = protection_optimizer._run_trial(structures, ctx, 0)
    assert result is None  # unchanged existing behavior -- callers still get a clean None

    log_path = tmp_path / "optimizer_errors.log"
    assert log_path.exists()
    log_text = log_path.read_text()
    assert "_run_trial" in log_text
    assert "a real, deliberate synthetic bug for this test" in log_text
    assert "RuntimeError" in log_text


def test_log_error_never_raises_even_if_scratch_dir_is_unwritable():
    """_log_error must never turn a logging failure into a second,
    worse failure on top of the one it's trying to record."""
    try:
        raise ValueError("dummy")
    except ValueError:
        protection_optimizer._log_error("/nonexistent/no/such/directory", "test context")
    # no exception propagated -- that's the whole point of this test
