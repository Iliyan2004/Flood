import numpy as np
import pytest
import surrogate_utils


def test_too_small_area_is_refused():
    if not surrogate_utils.available():
        pytest.skip("no surrogate weights on this machine")
    hdr = {"xllcorner": 0, "yllcorner": 0, "cellsize": 30.0, "src_crs": "EPSG:32642"}
    with pytest.raises(ValueError):
        surrogate_utils.predict_depth(np.zeros((50, 50), "float32"), hdr, 20, 2)


def test_tile_starts_cover_the_grid():
    for n in (128, 129, 300, 1000):
        s = surrogate_utils._starts(n)
        assert s[0] == 0 and s[-1] + surrogate_utils.TILE >= n
