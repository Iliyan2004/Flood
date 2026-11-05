import numpy as np
import hand_utils as h


def _bowl(n=21, depth=1.0):
    y, x = np.mgrid[0:n, 0:n]
    r = np.hypot(x - n // 2, y - n // 2)
    raw = 10 + np.where(r < 5, -depth * (1 - r / 5), 0.0) + 0.0
    filled = raw.copy()
    filled[r < 5] = 10.0                      # depression filled up to its rim
    return {"dem_raw": raw.astype("float32"), "filled": filled.astype("float32"),
            "accumulation": np.full(raw.shape, 400.0), "cellsize_m": 10.0}


def test_no_rain_no_ponding_and_dry_outside_bowl():
    hr = _bowl()
    assert h.pluvial_ponding_depth(hr, 0.0).max() == 0.0
    d = h.pluvial_ponding_depth(hr, 50.0)
    assert d[0, 0] == 0.0 and d[10, 10] > 0


def test_small_rain_partly_fills_and_large_rain_fills_to_rim():
    hr = _bowl()
    small = h.pluvial_ponding_depth(hr, 1.0)
    big = h.pluvial_ponding_depth(hr, 500.0)
    assert 0 < small.max() < big.max()
    assert abs(big.max() - 1.0) < 0.05          # filled to the rim, never above it
    assert big.max() <= 1.0 + 1e-3


def test_more_rain_never_less_water():
    hr = _bowl()
    vols = [float(h.pluvial_ponding_depth(hr, r).sum()) for r in (1, 5, 20, 100)]
    assert vols == sorted(vols)
