"""
Fast, self-contained tests for fis_app._read_water_stack's real spatial
downsampling of the animated-scene payload -- the fix for a real,
directly-confirmed production bug: mapbox3d_utils.build_mapbox_flood_scene
embeds the ENTIRE depth/vx/vy stack as base64 inline in its own HTML with
no size cap, and a real full-scale Gilgit-Baltistan basin run (6.6M cells,
25 saved timesteps) produced a genuine 2.64GB mapbox_3d.html -- confirmed
directly against that real run's own file on disk -- that no browser can
load ("the model ran but nothing shows on the map" was this file silently
failing to load, not a rendering bug). Fixed by striding the embedded
arrays down to a real target element budget when a run's own full
resolution would exceed it; the true scientific max depth is read BEFORE
downsampling and is unaffected.

No real physics run needed here -- writes small/large synthetic ASCII
".wd" rasters directly (fis_app._write_ascii, the same real writer this
app's own physics pipeline uses) into a fake run directory.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fis_app  # noqa: E402


def _write_stack(run_dir, n_frames, nrows, ncols, peak_value, cellsize=30.0):
    results_dir = os.path.join(run_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    header = {"ncols": ncols, "nrows": nrows, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": cellsize}
    for t in range(n_frames):
        data = np.full((nrows, ncols), 0.1 * (t + 1))
        if t == n_frames - 1:
            data[0, 0] = peak_value  # the real overall peak, only in the last frame's corner cell
        fis_app._write_ascii(os.path.join(results_dir, f"res-{t:04d}.wd"), header, data)


def test_small_domain_is_not_downsampled(tmp_path):
    run_dir = str(tmp_path)
    _write_stack(run_dir, n_frames=3, nrows=20, ncols=25, peak_value=5.0)
    water = fis_app._read_water_stack(run_dir)
    assert water["depth_stack"].shape == (3, 20, 25)
    assert water["max_depth"] == pytest.approx(5.0)


def test_large_domain_is_downsampled_below_the_real_target(tmp_path):
    run_dir = str(tmp_path)
    # 30 frames x 900x900 cells = 24.3M elements, above the 5M-element
    # real embedding target -- large enough to force real downsampling
    # without needing gigabytes of synthetic data on disk for a fast test.
    _write_stack(run_dir, n_frames=30, nrows=900, ncols=900, peak_value=123.456)
    water = fis_app._read_water_stack(run_dir)
    n_frames, nrows, ncols = water["depth_stack"].shape
    assert n_frames * nrows * ncols <= 5_000_000
    assert nrows < 900 and ncols < 900  # real downsampling actually happened


def test_real_full_resolution_peak_survives_downsampling(tmp_path):
    """The scientific max_depth reported must be the true full-resolution
    peak, not diluted or missed by the striding that only affects the
    embedded ANIMATION arrays."""
    run_dir = str(tmp_path)
    _write_stack(run_dir, n_frames=30, nrows=900, ncols=900, peak_value=254.508)
    water = fis_app._read_water_stack(run_dir)
    assert water["max_depth"] == pytest.approx(254.508)
    # The peak cell (0,0 in the last frame) may or may not survive a given
    # stride, but the reported max_depth must reflect it regardless.
    assert water["depth_stack"].max() <= 254.508 + 1e-6


def test_downsampling_applies_consistently_to_vx_vy_when_present(tmp_path):
    run_dir = str(tmp_path)
    results_dir = os.path.join(run_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    header = {"ncols": 900, "nrows": 900, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 30.0}
    for t in range(30):
        fis_app._write_ascii(os.path.join(results_dir, f"res-{t:04d}.wd"), header, np.full((900, 900), 0.5))
        fis_app._write_ascii(os.path.join(results_dir, f"res-{t:04d}.Vx"), header, np.full((900, 900), 1.0))
        fis_app._write_ascii(os.path.join(results_dir, f"res-{t:04d}.Vy"), header, np.full((900, 900), 1.0))
    water = fis_app._read_water_stack(run_dir)
    assert water["vx_stack"] is not None
    assert water["vx_stack"].shape == water["depth_stack"].shape
    assert water["vy_stack"].shape == water["depth_stack"].shape


def test_scene_building_selection_keeps_wet_first_and_drops_dry_depth_series():
    import fis_app
    def fp(depths, size):
        return {"poly_rc": [(0, 0), (0, size), (size, size), (size, 0)], "height_m": 6.0, "depths": depths}
    fps = [fp([0, 0, 0], 3), fp([0, 0.5, 0.2], 1), fp([0, 1.5, 0.1], 1), fp([0, 0, 0], 9), fp([0, 0.01, 0], 5)]
    chosen, total = fis_app._select_scene_buildings(fps, cap=4)
    assert total == 5 and len(chosen) == 4
    assert [max(c["depths"]) for c in chosen[:2]] == [1.5, 0.5]            # wet ones first, deepest first
    assert chosen[2]["depths"] is None and chosen[2]["poly_rc"][2][0] == 9  # then the LARGEST dry building, no depth series
    assert fis_app._select_scene_buildings(fps, cap=10)[0].__len__() == 5


def test_rc_to_lonlat_batch_matches_scalar_conversion():
    import fis_app
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True).transform
    h = {"xllcorner": 500000.0, "yllcorner": 3700000.0, "cellsize": 30.0, "nrows": 100}
    lon, lat = fis_app._rc_to_lonlat_batch([0, 10, 99], [0, 20, 50], h, tf)
    for r, c, lo, la in zip([0, 10, 99], [0, 20, 50], lon, lat):
        e = tf(500000.0 + c * 30.0, 3700000.0 + (100 - r) * 30.0)
        assert (lo, la) == pytest.approx(e)
