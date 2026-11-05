import numpy as np
import calibration_utils as cu


def test_scaled_manning_scales_valid_cells_and_floors(tmp_path):
    src = tmp_path / "m.ascii"
    with open(src, "w") as f:
        f.write("ncols 3\nnrows 1\nxllcorner 0\nyllcorner 0\ncellsize 30\nNODATA_value -9999\n0.02 0.10 -9999\n")
    dst = tmp_path / "o.ascii"
    cu._write_scaled_manning(str(src), str(dst), 0.4)
    _, g = cu._read_ascii_grid(str(dst))
    assert g[0] == 0.01 and abs(g[1] - 0.04) < 1e-9 and g[2] == -9999     # 0.008 floored to 0.01; NODATA untouched


def test_search_finds_known_optimum(monkeypatch, tmp_path):
    """Synthetic response surface: CSI peaks at roughness x1.7 and infiltration 9 mm/hr."""
    def fake_trial(scale, infil, ctx, idx):
        csi = max(0.0, 1.0 - 0.6 * abs(np.log(scale / 1.7)) - 0.02 * abs(infil - 9.0))
        return {"CSI": round(csi, 4), "POD": 1, "FAR": 0, "F1": 1, "bias": 1, "hits": 1, "misses": 0, "false_alarms": 0,
                "correct_negatives": 0, "model_wet_km2": 1, "observed_wet_km2": 1, "scored_cells": 1}
    monkeypatch.setattr(cu, "run_trial", fake_trial)
    ctx = {"run_params": {"infiltration_mm_hr": 2.0}, "scratch_dir": str(tmp_path), "seed_key": "t"}
    res = cu.calibrate(ctx, 40)
    assert res["csi_after"] > res["csi_before"] + 0.3
    assert abs(np.log(res["best"]["manning_scale"] / 1.7)) < 0.25 and abs(res["best"]["infiltration_mm_hr"] - 9.0) < 4
    assert res["baseline"]["manning_scale"] == 1.0 and res["n_trials"] >= 10


def test_all_failures_return_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cu, "run_trial", lambda *a, **k: None)
    ctx = {"run_params": {"infiltration_mm_hr": 0.0}, "scratch_dir": str(tmp_path), "seed_key": "t"}
    assert cu.calibrate(ctx, 8) is None
