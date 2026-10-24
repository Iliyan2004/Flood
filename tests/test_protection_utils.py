"""
Fast, pure-geometry tests for protection_utils.py's DEM conditioning
(dam/barrage raise, canal/drainage lower) -- no network/physics
dependency, uses a synthetic DEM/header.
"""
import os
import sys

import numpy as np
import pytest
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protection_utils  # noqa: E402

HEADER = {"ncols": 20, "nrows": 20, "xllcorner": 0.0, "yllcorner": 0.0,
          "cellsize": 30.0, "src_crs": "EPSG:32642"}


def _line_lonlat(x1, y1, x2, y2):
    to_wgs = Transformer.from_crs("EPSG:32642", "EPSG:4326", always_xy=True).transform
    lon1, lat1 = to_wgs(x1, y1)
    lon2, lat2 = to_wgs(x2, y2)
    return [[lon1, lat1], [lon2, lat2]]


def _flat_dem(value=100.0):
    return np.full((HEADER["nrows"], HEADER["ncols"]), value)


def test_dam_raises_dem_by_exact_height():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "dam", "line": line, "height_m": 5.0, "half_width_m": 30.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    assert modified.max() == pytest.approx(105.0)
    assert (modified > 100.0).sum() > 0


def test_canal_lowers_dem_by_exact_height():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "canal", "line": line, "height_m": 2.0, "half_width_m": 15.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    assert modified.min() == pytest.approx(98.0)
    assert (modified < 100.0).sum() > 0


def test_apply_structures_never_mutates_input_dem():
    dem = _flat_dem()
    original = dem.copy()
    line = _line_lonlat(300, 300, 300, 0)
    protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642",
                                              [{"type": "dam", "line": line, "height_m": 5.0}])
    assert np.array_equal(dem, original)


def test_nodata_cells_are_never_conditioned():
    dem = _flat_dem()
    dem[:, 10] = -9999  # a real NODATA column right where the structure crosses
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "dam", "line": line, "height_m": 5.0, "half_width_m": 60.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    assert (modified[:, 10] == -9999).all()


def test_structures_affected_cells_matches_real_conditioned_count():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "dam", "line": line, "height_m": 5.0, "half_width_m": 30.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    affected = protection_utils.structures_affected_cells(dem, HEADER, "EPSG:32642", structures)
    assert affected == int((modified > 100.0).sum())


def test_structures_affected_cells_zero_for_structure_far_outside_grid():
    dem = _flat_dem()
    # A line entirely off the 20x20/30m grid (well beyond its real extent).
    line = _line_lonlat(100000, 100000, 100010, 100010)
    structures = [{"type": "dam", "line": line, "height_m": 5.0, "half_width_m": 10.0}]
    assert protection_utils.structures_affected_cells(dem, HEADER, "EPSG:32642", structures) == 0


def test_degenerate_single_point_line_is_ignored_not_raised():
    dem = _flat_dem()
    structures = [{"type": "dam", "line": [[73.0, 33.0]], "height_m": 5.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    assert np.array_equal(modified, dem)


def test_unknown_structure_type_falls_back_to_dam_defaults():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "not_a_real_type", "line": line}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    assert modified.max() == pytest.approx(100.0 + protection_utils.STRUCTURE_TYPES["dam"]["default_height_m"])


def test_write_ascii_grid_round_trips_header_and_data(tmp_path):
    dem = _flat_dem(123.456)
    path = str(tmp_path / "test.dem.ascii")
    protection_utils.write_ascii_grid(path, HEADER, dem)
    with open(path) as f:
        lines = [f.readline() for _ in range(6)]
        data = np.loadtxt(f)
    assert "ncols" in lines[0] and str(HEADER["ncols"]) in lines[0]
    assert np.allclose(data, dem)


def test_structure_types_all_have_required_fields():
    for stype, cfg in protection_utils.STRUCTURE_TYPES.items():
        assert "label" in cfg and "raise" in cfg
        assert cfg["default_height_m"] > 0
        assert cfg["default_half_width_m"] > 0


def test_dam_and_barrage_raise_canal_and_drainage_lower():
    assert protection_utils.STRUCTURE_TYPES["dam"]["raise"] is True
    assert protection_utils.STRUCTURE_TYPES["barrage"]["raise"] is True
    assert protection_utils.STRUCTURE_TYPES["canal"]["raise"] is False
    assert protection_utils.STRUCTURE_TYPES["drainage"]["raise"] is False


def test_catchment_lowers_dem_by_exact_height():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "catchment", "line": line, "height_m": 2.0, "half_width_m": 30.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    assert modified.min() == pytest.approx(98.0)
    assert protection_utils.STRUCTURE_TYPES["catchment"]["raise"] is False


def test_catchment_storage_volume_matches_real_conditioned_cells():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "catchment", "line": line, "height_m": 2.0, "half_width_m": 30.0}]
    modified = protection_utils.apply_structures_to_dem(dem, HEADER, "EPSG:32642", structures)
    affected = int((modified < 100.0).sum())
    vol = protection_utils.catchment_storage_volume_m3(dem, HEADER, "EPSG:32642", structures)
    assert vol == pytest.approx(affected * HEADER["cellsize"] ** 2 * 2.0)
    assert vol > 0


def test_catchment_storage_volume_zero_for_non_catchment_structures():
    dem = _flat_dem()
    line = _line_lonlat(300, 300, 300, 0)
    structures = [{"type": "dam", "line": line, "height_m": 5.0, "half_width_m": 30.0},
                  {"type": "canal", "line": line, "height_m": 2.0, "half_width_m": 15.0}]
    vol = protection_utils.catchment_storage_volume_m3(dem, HEADER, "EPSG:32642", structures)
    assert vol == 0.0


def test_catchment_storage_volume_excludes_nodata_cells():
    dem = _flat_dem()
    dem[:, 10] = -9999
    line = _line_lonlat(300, 300, 300, 0)
    structures_full = [{"type": "catchment", "line": line, "height_m": 2.0, "half_width_m": 60.0}]
    dem_no_gap = _flat_dem()
    vol_with_gap = protection_utils.catchment_storage_volume_m3(dem, HEADER, "EPSG:32642", structures_full)
    vol_without_gap = protection_utils.catchment_storage_volume_m3(dem_no_gap, HEADER, "EPSG:32642", structures_full)
    assert vol_with_gap < vol_without_gap  # the real NODATA column must be excluded, lowering the total
