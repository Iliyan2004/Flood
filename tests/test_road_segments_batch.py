import random
import numpy as np
from shapely.geometry import Point, Polygon
import fis_app

HDR = {"ncols": 50, "nrows": 40, "xllcorner": 500000.0, "yllcorner": 3000000.0, "cellsize": 30.0}
CRS = "EPSG:32642"


def _old(coords, aoi):
    """The original per-vertex implementation, kept as the reference."""
    segs, cr, cc = [], [], []
    for lon, lat in coords:
        inside = aoi is None or aoi.contains(Point(lon, lat))
        r, c = fis_app._lonlat_to_rowcol(lon, lat, HDR, CRS)
        if inside and fis_app._rowcol_in_bounds(r, c, HDR):
            cr.append(r); cc.append(c)
        else:
            if len(cr) >= 2:
                segs.append((cr, cc))
            cr, cc = [], []
    if len(cr) >= 2:
        segs.append((cr, cc))
    return segs


def _paths():
    from pyproj import Transformer
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform
    rng = random.Random(3)
    paths = []
    for _ in range(40):
        x, y = rng.uniform(499800, 501700), rng.uniform(2999900, 3001300)
        pts = []
        for _ in range(rng.randint(0, 12)):
            x += rng.uniform(-150, 150); y += rng.uniform(-150, 150)
            pts.append(to_ll(x, y))
        paths.append(pts)
    return paths


def test_batch_matches_per_path_reference_with_and_without_aoi():
    from pyproj import Transformer
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform
    aoi = Polygon([to_ll(500200, 3000100), to_ll(501400, 3000300), to_ll(501000, 3001100), to_ll(500300, 3000900)])
    paths = _paths()
    for a in (None, aoi):
        got = fis_app._lonlat_paths_to_segments_batch(paths, HDR, CRS, a)
        assert got == [_old(p, a) for p in paths]
        assert any(g for g in got)


def test_single_path_wrapper_and_empty_input():
    p = _paths()[1]
    assert fis_app._lonlat_path_to_segments(p, HDR, CRS) == _old(p, None)
    assert fis_app._lonlat_paths_to_segments_batch([], HDR, CRS) == []
    assert fis_app._lonlat_paths_to_segments_batch([[], []], HDR, CRS) == [[], []]
