"""An inflow whose entry point has no valid terrain used to be dropped without a word, so a run with 0 rain and a
large discharge showed no flood. _extend_inflow_corridors cuts a short feeder channel instead."""
import os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fis_app

H = {'ncols': 40, 'nrows': 40, 'xllcorner': 500000.0, 'yllcorner': 3500000.0, 'cellsize': 30.0}
CRS = 'EPSG:32642'


def _terrain_lower_left():
    dem = np.full((40, 40), -9999.0)
    dem[20:, :25] = 100.0
    return dem


def _item(edge='N', pos=30, width=60):
    return {'edge': edge, 'lon': None, 'lat': None, 'pos_pct': pos, 'width_m': width, 'peak_m3s': 100, 'name': 't'}


def test_edge_without_terrain_gets_a_feeder_channel():
    dem = _terrain_lower_left()
    out = fis_app._extend_inflow_corridors(H, dem.copy(), [_item()], CRS, tempfile.mktemp())
    assert (out != -9999).sum() > (dem != -9999).sum()
    assert (out[0] != -9999).any(), "the inflow edge must now have valid cells"
    res = fis_app._resolve_boundary_item(H, out, CRS, _item(), tempfile.mktemp())
    assert res is not None and res["edge"] == "N"


def test_valid_cells_are_never_changed():
    dem = _terrain_lower_left()
    out = fis_app._extend_inflow_corridors(H, dem.copy(), [_item()], CRS, tempfile.mktemp())
    valid = dem != -9999
    assert np.array_equal(out[valid], dem[valid])


def test_channel_uses_the_nearest_terrain_level():
    dem = _terrain_lower_left()
    dem[20:, :25] = 137.5
    out = fis_app._extend_inflow_corridors(H, dem.copy(), [_item()], CRS, tempfile.mktemp())
    added = (out != -9999) & (dem == -9999)
    assert added.any() and np.all(out[added] == 137.5)


def test_nothing_changes_when_terrain_reaches_the_entry_point():
    dem = np.full((40, 40), 100.0)
    out = fis_app._extend_inflow_corridors(H, dem.copy(), [_item()], CRS, tempfile.mktemp())
    assert np.array_equal(out, dem)


def test_all_four_edges_work():
    for edge in "NESW":
        dem = np.full((40, 40), -9999.0)
        dem[15:25, 15:25] = 100.0
        out = fis_app._extend_inflow_corridors(H, dem.copy(), [_item(edge, 50)], CRS, tempfile.mktemp())
        e = {"N": out[0, :], "S": out[-1, :], "W": out[:, 0], "E": out[:, -1]}[edge]
        assert (e != -9999).any(), edge
