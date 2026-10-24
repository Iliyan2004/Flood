"""
Pseudo-SGC: real river channel bathymetry conditioned directly into the
DEM before a Tier 3 physics run -- achieving LISFLOOD-FP's real Sub-Grid
Channel (SGC) solver's actual physical goal (a channel with genuine
conveyance capacity below the DEM's own water-surface elevation)
WITHOUT needing the SGC solver itself, which has zero support in the
CUDA/ACC GPU path this app uses. Confirmed directly from source: grepping
every `.cu`/`.cuh` file under LISFLOOD-FP 8.2's `cuda/` for "SGC" returns
zero hits -- matching an independent finding from a sibling project's own
investigation of this exact same LISFLOOD-FP build (see CLAUDE.md).

Real technique, not invented: reuses the EXACT SAME real hydraulic-
geometry width scaling and Manning's-equation depth solve already built
and verified for Tier 2's HAND rating curve (`hand_utils.
_manning_depth_for_discharge`) -- applied here as a one-time DEM
correction (the same "condition the DEM, then run the standard 2D solver
on it" technique this app already uses for buildings) instead of a live
sub-grid channel/floodplain volume exchange. This keeps Tier 2 and Tier 3
using the same real channel-geometry assumptions rather than two
unrelated guesses.

Deliberately NOT the same as real SGC -- stated plainly, not glossed over:
- Real SGC represents a channel NARROWER than the grid cell and tracks
  channel/floodplain water volume separately within one cell. This
  technique instead deepens whole grid cells along a buffered channel
  corridor -- a coarser, full-cell approximation, not a sub-grid one.
- Depth comes from a width/Manning's regression, not a surveyed cross-
  section. A sibling project's own literature review (Neal et al. 2021,
  WRR, 10.1029/2020WR028301) found this exact class of regression-based
  bathymetry carries a real wet bias relative to gradually-varied-flow
  bathymetry (up to 40% flood-extent over-prediction in their
  comparison). This technique inherits that same caveat; it does not
  fix it.
- Only conditions waterways actually present in OSM data for the AOI --
  an unmapped or misclassified channel gets no correction at all.

What this DOES fix: with NO channel conditioning at all (this app's
state before this feature), a river has zero conveyance capacity below
the DEM's own surface, so real river inflow spills onto the floodplain
almost immediately -- a bigger, cruder gap than even the regression-
bathymetry case above. This is a real, if imperfect, improvement over
that, and stays fully GPU-compatible since it is pure DEM conditioning --
no new LISFLOOD-FP par-file keywords, no SGC solver invocation at all.
"""
import numpy as np
import rasterio.features
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString
from shapely.ops import transform as shapely_transform

from hand_utils import _manning_depth_for_discharge, MANNING_N_CHANNEL, CHANNEL_SIDE_SLOPE

# Same documented per-type real-world channel widths already used
# elsewhere in this app for siting fluvial inflow (fis_app.py's
# channel_width_m dict, from /area/river) -- kept consistent rather than
# inventing new numbers for this feature.
CHANNEL_WIDTH_M = {"river": 40.0, "stream": 8.0, "canal": 15.0}
DEFAULT_CHANNEL_WIDTH_M = 20.0
MIN_CHANNEL_SLOPE = 1e-3
MAX_CHANNEL_DEPTH_M = 25.0  # sanity cap -- a regression depth blowing up on a bad slope shouldn't carve a canyon


def condition_dem_with_channel(header, dem, waterway_features_lonlat, discharge_m3s, src_crs, nodata=-9999.0):
    """
    header: dict with xllcorner/yllcorner/cellsize/nrows/ncols (the same
        header dict crop_dem_to_polygon already returns everywhere else
        in this app).
    dem: 2D float array (row, col) -- NOT modified in place; a
        conditioned copy is returned.
    waterway_features_lonlat: [{"type": "river"|"stream"|"canal",
        "coords": [(lon,lat), ...]}, ...] -- real OSM waterway
        centerlines for this AOI, WGS84.
    discharge_m3s: the SAME real design discharge this run's own fluvial
        inflow already uses (flood_model.py's --inflow-m3s) -- the
        channel is sized for the same flow the physics run will actually
        push through it, not an unrelated guess.
    src_crs: the DEM's own projected CRS (header["src_crs"] elsewhere in
        this app), needed to buffer the channel by a real metre width.

    Returns (conditioned_dem, channel_mask, summary) -- channel_mask and
    summary are for transparency (recorded in run_meta.json / the UI),
    not a silent correction.
    """
    nrows, ncols, cs = int(header["nrows"]), int(header["ncols"]), header["cellsize"]
    channel_mask = np.zeros((nrows, ncols), dtype=bool)

    if discharge_m3s <= 0 or not waterway_features_lonlat:
        return dem, channel_mask, {"applied": False, "reason": "no fluvial discharge or no waterway data for this AOI"}

    transform = from_origin(header["xllcorner"], header["yllcorner"] + nrows * cs, cs, cs)
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform

    def elev_at_xy(x, y):
        col = int((x - header["xllcorner"]) / cs)
        row = int(nrows - 1 - (y - header["yllcorner"]) / cs)
        if 0 <= row < nrows and 0 <= col < ncols and dem[row, col] != nodata:
            return float(dem[row, col])
        return None

    conditioned = dem.astype("float64").copy()
    applied = []

    for feat in waterway_features_lonlat:
        coords = feat.get("coords") or []
        if len(coords) < 2:
            continue
        try:
            line_src = shapely_transform(to_src, LineString(coords))
        except Exception:
            continue
        if line_src.length <= 0:
            continue

        wtype = feat.get("type")
        width_m = CHANNEL_WIDTH_M.get(wtype, DEFAULT_CHANNEL_WIDTH_M)

        # Real local slope from the DEM's own elevation at this specific
        # line's two endpoints -- not assumed, and specific to this
        # waterway rather than a generic AOI-wide guess.
        (x0, y0), (x1, y1) = line_src.coords[0], line_src.coords[-1]
        e0, e1 = elev_at_xy(x0, y0), elev_at_xy(x1, y1)
        slope = max(abs(e0 - e1) / line_src.length, MIN_CHANNEL_SLOPE) if (e0 is not None and e1 is not None) else MIN_CHANNEL_SLOPE

        depth_m = min(
            _manning_depth_for_discharge(discharge_m3s, width_m, CHANNEL_SIDE_SLOPE, MANNING_N_CHANNEL, slope),
            MAX_CHANNEL_DEPTH_M,
        )
        applied.append({"type": wtype, "width_m": width_m, "depth_m": round(depth_m, 3), "slope": round(slope, 5)})
        if depth_m <= 0:
            continue

        corridor = line_src.buffer(width_m / 2.0, cap_style=2)  # flat caps -- a channel, not a rounded pill
        mask = rasterio.features.rasterize(
            [(corridor, 1)], out_shape=(nrows, ncols), transform=transform, fill=0, dtype="uint8"
        ).astype(bool)
        mask &= (dem != nodata)
        channel_mask |= mask
        conditioned = np.where(mask, np.minimum(conditioned, dem - depth_m), conditioned)

    conditioned = np.where(dem == nodata, dem, conditioned).astype(dem.dtype)
    summary = {
        "applied": bool(channel_mask.any()),
        "n_waterways_conditioned": len(applied),
        "channels": applied,
        "n_cells_conditioned": int(channel_mask.sum()),
    }
    return conditioned, channel_mask, summary
