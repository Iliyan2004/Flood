"""Learned flood-depth estimate for SAILAB Instant: a small U-Net trained on LISFLOOD-FP runs (see surrogate/).

It reads the terrain in 128 x 128-cell tiles (about 3.7 km at 29 m), with the same inputs it was trained on, and
predicts the peak flood depth a real solver run would give for a rain-only storm. Tiles overlap and are blended.
It is an estimate: it was trained on rain-driven runs over Pakistan SRTM only, with no buildings and no river
inflow. Check surrogate/pilot2_report.md for its measured accuracy against the real solver.
"""
import os
import tempfile
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATHS = [os.path.join(HERE, "surrogate", "unet_final.pt"), os.path.join(HERE, "surrogate", "unet_pilot2.pt")]
TILE, STRIDE, WET = 128, 96, 0.05
MAX_TILES = 150
_lock = threading.Lock()
_net = {"model": None, "dev": None, "path": None}


def available():
    return any(os.path.exists(p) for p in MODEL_PATHS)


def _build():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    def block(cin, cout):
        return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.GELU(),
                             nn.Conv2d(cout, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.GELU())

    class UNet(nn.Module):
        def __init__(self, cin=10, w=32):
            super().__init__()
            self.e1, self.e2, self.e3 = block(cin, w), block(w, 2 * w), block(2 * w, 4 * w)
            self.mid = block(4 * w, 4 * w)
            self.d3, self.d2, self.d1 = block(8 * w, 2 * w), block(4 * w, w), block(2 * w, w)
            self.out = nn.Conv2d(w, 1, 1)

        def forward(self, x):
            e1 = self.e1(x); e2 = self.e2(F.max_pool2d(e1, 2)); e3 = self.e3(F.max_pool2d(e2, 2))
            m = self.mid(F.max_pool2d(e3, 2))
            d3 = self.d3(torch.cat([F.interpolate(m, scale_factor=2), e3], 1))
            d2 = self.d2(torch.cat([F.interpolate(d3, scale_factor=2), e2], 1))
            d1 = self.d1(torch.cat([F.interpolate(d2, scale_factor=2), e1], 1))
            return self.out(d1)[:, 0]
    return UNet()


def _load():
    import torch
    if _net["model"] is not None:
        return _net["model"], _net["dev"]
    path = next((p for p in MODEL_PATHS if os.path.exists(p)), None)
    if path is None:
        raise RuntimeError("learned estimate is not installed on this server")
    net = _build(); net.load_state_dict(torch.load(path, map_location="cpu")); net.eval()
    dev = torch.device("cpu")
    try:
        torch.zeros(1).cuda(); dev = torch.device("cuda")
    except Exception:
        pass
    _net.update(model=net.to(dev), dev=dev, path=os.path.basename(path))
    return _net["model"], dev


def _tile_features(dem, filled, hand, acc, cs, rain, dur, friction, infil):
    gy, gx = np.gradient(dem, cs); slope = np.clip(np.hypot(gx, gy), 0, 2) / 2
    m = dem.mean()
    ch = [np.clip((dem - m) / 50, -3, 3), np.clip((filled - m) / 50, -3, 3), slope,
          np.clip(np.nan_to_num(hand, nan=20.0, posinf=20.0), 0, 20) / 20, np.log1p(acc) / 10,
          np.full_like(dem, np.log1p(rain * dur) / 6), np.full_like(dem, np.log(rain) / 5), np.full_like(dem, dur / 6),
          np.full_like(dem, np.log(friction / 0.05) / 1.5), np.full_like(dem, infil / 20)]
    return np.stack(ch).astype("float32")


def _starts(n):
    s = list(range(0, max(n - TILE, 0) + 1, STRIDE))
    if s[-1] + TILE < n:
        s.append(n - TILE)
    return s


def predict_depth(dem_raw, header, rain_mm_hr, duration_hr, friction=0.05, infiltration_mm_hr=5.0, valid=None):
    """Peak depth (m) on the same grid as dem_raw. header holds xllcorner, yllcorner, cellsize, src_crs."""
    import rasterio
    import torch
    from rasterio.transform import from_origin
    import hand_utils
    nrows, ncols = dem_raw.shape
    if nrows < TILE or ncols < TILE:
        raise ValueError(f"area too small for the learned estimate (needs at least {TILE} x {TILE} cells, about 3.7 km)")
    rs, cs_ = _starts(nrows), _starts(ncols)
    if len(rs) * len(cs_) > MAX_TILES:
        raise ValueError(f"area too large for the learned estimate ({len(rs) * len(cs_)} tiles, limit {MAX_TILES})")
    cs = float(header["cellsize"]); x0 = float(header["xllcorner"]); ytop = float(header["yllcorner"]) + nrows * cs
    win = np.outer(np.hanning(TILE + 2)[1:-1], np.hanning(TILE + 2)[1:-1]).astype("float32") + 1e-3
    acc_d = np.zeros((nrows, ncols), "float32"); acc_w = np.zeros((nrows, ncols), "float32")
    with _lock:
        net, dev = _load()
        for r in rs:
            for c in cs_:
                dem = dem_raw[r:r + TILE, c:c + TILE].astype("float32")
                ok = dem > -9000
                if ok.mean() < 0.2:
                    continue
                if not ok.all():
                    dem = np.where(ok, dem, np.nanmin(np.where(ok, dem, np.nan)))
                with tempfile.TemporaryDirectory() as td:
                    tif = os.path.join(td, "t.tif")
                    with rasterio.open(tif, "w", driver="GTiff", height=TILE, width=TILE, count=1, dtype="float32",
                                       crs=header["src_crs"], transform=from_origin(x0 + c * cs, ytop - r * cs, cs, cs), nodata=-9999.0) as dst:
                        dst.write(dem, 1)
                    hr = hand_utils.compute_hand(tif)
                x = _tile_features(dem, np.asarray(hr["filled"], "float32"), np.asarray(hr["hand"], "float32"),
                                   np.asarray(hr["accumulation"], "float32"), cs, rain_mm_hr, duration_hr, friction, infiltration_mm_hr)
                with torch.no_grad():
                    y = net(torch.tensor(x[None]).to(dev))[0].float().cpu().numpy()
                d = np.maximum(np.expm1(y) * WET, 0)
                acc_d[r:r + TILE, c:c + TILE] += d * win; acc_w[r:r + TILE, c:c + TILE] += win
    out = np.where(acc_w > 0, acc_d / np.maximum(acc_w, 1e-6), 0).astype("float32")
    out[out < WET] = 0
    if valid is not None:
        out[~valid] = 0
    return out


def model_name():
    return _net["path"]
