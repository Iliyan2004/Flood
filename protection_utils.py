"""
Grey-infrastructure protection scenarios: dam/embankment, barrage,
diversion canal, drainage pipe -- each drawn as a line, buffered into a
real corridor polygon, and rasterized directly onto a baseline run's own
building-conditioned DEM (the same DEM-conditioning approach this app
already uses for buildings elsewhere: raise the ground for a dam/barrage,
lower it for a canal/drainage trench). Re-run through the exact same real
LISFLOOD-FP physics as any other run -- no new solver, no surrogate.

This is a fresh reimplementation, not a restore: an earlier version of
this feature (Tab 6, its own `protection_utils.py`/`protection_optimizer.py`,
a mystic-driven auto-optimizer) was deleted outright in an earlier
explicit "remove Tab 6 completely" pass (see CLAUDE.md). Rebuilt at a
later explicit request, this time reusing the app's existing NbS-scenario
job pattern (`execute_nbs_run`/`nbs_utils.compare_flood_results`) as its
template instead of the old bespoke pipeline -- the real cell-by-cell
before/after comparison (protected vs. displaced) is shared verbatim with
`nbs_utils.compare_flood_results` rather than duplicated here.
"""
import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import LineString

# cap_style=3 (square, shapely) extends past the drawn endpoint by one
# half-width, like a real embankment tying into higher ground on both
# sides -- appropriate for a raised structure. cap_style=2 (flat) ends
# exactly at the endpoint, appropriate for a dug channel.
STRUCTURE_TYPES = {
    "dam": {"label": "Dam / embankment", "raise": True,
            "default_height_m": 3.0, "default_half_width_m": 8.0, "color": "#8a6d3b"},
    "barrage": {"label": "Barrage", "raise": True,
                "default_height_m": 2.5, "default_half_width_m": 12.0, "color": "#6d5a8a"},
    "canal": {"label": "Diversion canal", "raise": False,
              "default_height_m": 2.0, "default_half_width_m": 6.0, "color": "#3f8ce0"},
    "drainage": {"label": "Drainage pipe", "raise": False,
                 "default_height_m": 1.0, "default_half_width_m": 1.5, "color": "#3fb0e0"},
    # Rainwater catchment / retention (detention) basin: a dug depression
    # that captures and temporarily holds runoff, same "lower the DEM"
    # conditioning as canal/drainage -- but drawn wide, not narrow, since
    # it's meant to read as a basin/pond footprint (a buffered-line
    # approximation, same real limitation every other structure here
    # already has) rather than a conveyance channel. Real, deliberate
    # design defaults, not derived from a catchment-area/rainfall sizing
    # calculation this app doesn't perform (a true detention-basin design
    # uses the rational method or TR-55 to size storage against a specific
    # contributing catchment area -- out of scope here): 2.0m depth is a
    # typical urban detention-pond depth (most stormwater-management
    # guidance keeps these shallow, ~1-3m, for safety and easy
    # maintenance access), 20m half-width gives a real, moderate
    # neighborhood-scale basin footprint (~40m across) rather than an
    # oversized reservoir. Real, physically meaningful behavior for free
    # from the existing DEM-conditioning approach: because this is a real
    # depression the actual LISFLOOD-FP shallow-water physics solves over
    # (not a synthetic volume subtracted from the storm), the basin
    # genuinely fills first and only spills into surrounding terrain once
    # full -- the capture-then-overflow behavior a real detention basin
    # has, with no separate storage/release model needed.
    "catchment": {"label": "Rainwater catchment / retention basin", "raise": False,
                  "default_height_m": 2.0, "default_half_width_m": 20.0, "color": "#2ecc9e"},
}


def write_ascii_grid(path, header, data, nodata=-9999):
    """Standalone ARC ASCII writer (no dependency on fis_app.py, so
    protection_optimizer.py's trial loop can write scratch DEMs without a
    circular import)."""
    with open(path, "w") as f:
        f.write(f"ncols         {int(header['ncols'])}\n")
        f.write(f"nrows         {int(header['nrows'])}\n")
        f.write(f"xllcorner     {header['xllcorner']}\n")
        f.write(f"yllcorner     {header['yllcorner']}\n")
        f.write(f"cellsize      {header['cellsize']}\n")
        f.write(f"NODATA_value  {nodata}\n")
        np.savetxt(f, data, fmt="%.4f")


def _line_to_pixel_xy(lonlat_coords, header, src_crs):
    """Real lon/lat -> (col, row) pixel-space conversion, row increasing
    downward (north at row 0) to match this app's own ARC ASCII grid
    convention (see fis_app.py's _lonlat_to_rowcol, the same math)."""
    to_src = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
    pts = []
    for lon, lat in lonlat_coords:
        x, y = to_src(lon, lat)
        col = (x - header["xllcorner"]) / header["cellsize"]
        row = header["nrows"] - (y - header["yllcorner"]) / header["cellsize"]
        pts.append((col, row))
    return pts


def _structure_corridor_mask(structure, header, src_crs, shape):
    """Real buffered-corridor polygon for one structure, rasterized to a
    boolean mask on the DEM's own grid. Returns None for a degenerate
    (<2-point) line."""
    coords = structure.get("line", [])
    if len(coords) < 2:
        return None
    cfg = STRUCTURE_TYPES.get(structure.get("type", "dam"), STRUCTURE_TYPES["dam"])
    half_width_m = float(structure.get("half_width_m", cfg["default_half_width_m"]))
    pix_pts = _line_to_pixel_xy(coords, header, src_crs)
    line_xy = LineString(pix_pts)
    half_width_px = max(half_width_m / header["cellsize"], 0.5)
    cap_style = 3 if cfg["raise"] else 2
    corridor = line_xy.buffer(half_width_px, cap_style=cap_style)
    if corridor.is_empty:
        return None
    return rasterize([(corridor, 1)], out_shape=shape, fill=0,
                      transform=Affine(1, 0, 0, 0, 1, 0), all_touched=True).astype(bool)


def apply_structures_to_dem(dem, header, src_crs, structures, nodata=-9999):
    """Real structure conditioning onto a COPY of the DEM (never mutates
    the input in place): raised for dam/barrage, lowered for canal/
    drainage, restricted to cells that are real valid terrain in the
    baseline DEM."""
    modified = dem.copy()
    valid = dem != nodata
    for s in structures:
        cfg = STRUCTURE_TYPES.get(s.get("type", "dam"), STRUCTURE_TYPES["dam"])
        height_m = float(s.get("height_m", cfg["default_height_m"]))
        mask = _structure_corridor_mask(s, header, src_crs, dem.shape)
        if mask is None:
            continue
        mask &= valid
        if cfg["raise"]:
            modified[mask] = modified[mask] + height_m
        else:
            modified[mask] = modified[mask] - height_m
    return modified


def structures_affected_cells(dem, header, src_crs, structures, nodata=-9999):
    """Real count of valid DEM cells any structure actually rasterizes
    to -- used to catch a candidate that lands entirely outside the
    valid AOI (e.g. the auto-optimizer wandering off the drawn area) as
    a real, explicit failure rather than a silently-accepted 0% result."""
    valid = dem != nodata
    total = np.zeros(dem.shape, dtype=bool)
    for s in structures:
        mask = _structure_corridor_mask(s, header, src_crs, dem.shape)
        if mask is not None:
            total |= (mask & valid)
    return int(total.sum())


def catchment_storage_volume_m3(dem, header, src_crs, structures, nodata=-9999):
    """Real EXCAVATED storage volume (m^3) for every "catchment"-type
    structure in the scenario: real affected-cell area (cellsize^2) times
    the structure's own real excavation depth (height_m). This is an
    upper bound on what the basin could hold, not a claim about how much
    it actually captures in any specific storm -- the real answer to that
    (does it fill, does it overflow) comes from the physics run itself,
    since the basin is a genuine depression in the conditioned DEM that
    LISFLOOD-FP solves over directly (see STRUCTURE_TYPES["catchment"]'s
    own comment). Returns 0.0 if the scenario has no catchment structure,
    or if none of them land on real valid terrain."""
    valid = dem != nodata
    cell_area_m2 = float(header["cellsize"]) ** 2
    total_m3 = 0.0
    for s in structures:
        if s.get("type") != "catchment":
            continue
        cfg = STRUCTURE_TYPES["catchment"]
        height_m = float(s.get("height_m", cfg["default_height_m"]))
        mask = _structure_corridor_mask(s, header, src_crs, dem.shape)
        if mask is None:
            continue
        mask &= valid
        total_m3 += int(mask.sum()) * cell_area_m2 * height_m
    return round(total_m3, 1)
