"""
Fast tests for mapbox3d_utils.build_mapbox_flood_scene's real proactive
size safeguard -- a general check on the ACTUAL final scene file size,
added after a real 2.64GB mapbox_3d.html was found in production (see
CLAUDE.md's own incident writeup). The root cause there (an uncapped
embedded depth/velocity stack) was already fixed at the fis_app.py level
(_read_water_stack's own downsampling); this is a separate, general
safety net that flags ANY future oversized scene regardless of cause,
rather than re-testing that specific fix.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mapbox3d_utils  # noqa: E402

BBOX = [73.0, 33.6, 73.05, 33.65]
AOI_GEOM = {"type": "Polygon", "coordinates": [[[73.0, 33.6], [73.05, 33.6], [73.05, 33.65], [73.0, 33.65], [73.0, 33.6]]]}


def test_normal_sized_scene_does_not_trigger_the_warning(tmp_path):
    water = {"depth_stack": np.random.rand(3, 20, 20).astype("float32"), "max_depth": 1.0}
    out_path = str(tmp_path / "mapbox_3d.html")
    mapbox3d_utils.build_mapbox_flood_scene(out_path, "Test", BBOX, AOI_GEOM, "fake-token", water=water)
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) < mapbox3d_utils.MAPBOX_SCENE_SIZE_WARN_BYTES
    assert not (tmp_path / "mapbox3d_size_warning.log").exists()


def test_oversized_scene_triggers_a_real_logged_warning(tmp_path):
    """A real, deliberately huge synthetic depth stack -- big enough to
    push the final file past the real safety threshold regardless of the
    exact threshold value -- must produce a real warning log entry
    naming the actual byte count, not silently ship."""
    # ~40M float32 elements -> ~160MB raw, ~213MB once base64-encoded,
    # comfortably over any sane real threshold.
    water = {"depth_stack": np.random.rand(4, 3162, 3162).astype("float32"), "max_depth": 5.0}
    out_path = str(tmp_path / "mapbox_3d.html")
    mapbox3d_utils.build_mapbox_flood_scene(out_path, "Test", BBOX, AOI_GEOM, "fake-token", water=water)
    real_size = os.path.getsize(out_path)
    warn_path = tmp_path / "mapbox3d_size_warning.log"
    assert warn_path.exists(), f"expected a real warning log for a {real_size:,}-byte scene"
    log_text = warn_path.read_text()
    assert "mapbox_3d.html" in log_text
    assert str(real_size) in log_text.replace(",", "")


def test_warning_never_raises_even_if_the_scene_directory_is_gone(tmp_path, monkeypatch):
    """A logging failure in the size safeguard must never turn into a
    second, worse failure -- confirmed by pointing os.path.getsize at a
    path that no longer exists after the real write already succeeded."""
    water = {"depth_stack": np.random.rand(2, 10, 10).astype("float32"), "max_depth": 1.0}
    out_path = str(tmp_path / "mapbox_3d.html")
    real_getsize = os.path.getsize

    def flaky_getsize(path):
        if path == out_path:
            raise OSError("simulated real disk failure")
        return real_getsize(path)

    monkeypatch.setattr(mapbox3d_utils.os.path, "getsize", flaky_getsize)
    # must not raise
    mapbox3d_utils.build_mapbox_flood_scene(out_path, "Test", BBOX, AOI_GEOM, "fake-token", water=water)
    assert os.path.exists(out_path)
