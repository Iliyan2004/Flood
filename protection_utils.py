"""
Tab 6: user-placed artificial protective infrastructure (dams, barrages,
canals, drainage pipes) -- a "what if we built this" scenario tool. Not
real engineering design; a documented, physically-motivated DEM-conditioning
proxy consistent with how buildings/rivers already condition the DEM
elsewhere in this app: a levee-type structure (dam/barrage) raises the
ground along its footprint so LISFLOOD-FP's local-inertial solver routes
flow around/over it; a channel-type structure (canal/drainage) lowers the
ground so the solver routes flow into and along it. The flood model itself
is not modified -- only its DEM input is, exactly like buildings.

Drainage is modeled as a buried PIPE, not an open channel like a canal --
its size parameter is the pipe's diameter (the "height_m" field is reused
for this; a pipe's trench is cut roughly as deep as the pipe is wide), not
a dam-style height, hence the much smaller default and half-width below.
"""
import numpy as np

# (raise_dem: True for a barrier, False for a channel that lowers the ground)
STRUCTURE_TYPES = {
    "dam":       {"label": "Dam / embankment", "raise_dem": True,  "default_height_m": 4.0, "default_half_width_m": 15.0, "color": "#8a6d3b"},
    "barrage":   {"label": "Barrage",           "raise_dem": True,  "default_height_m": 3.0, "default_half_width_m": 20.0, "color": "#7d7d7d"},
    "canal":     {"label": "Diversion canal",   "raise_dem": False, "default_height_m": 2.5, "default_half_width_m": 10.0, "color": "#2fa3a3"},
    "drainage":  {"label": "Drainage pipe",     "raise_dem": False, "default_height_m": 0.6, "default_half_width_m": 1.0, "color": "#3fae5c"},
}


def apply_structures_to_dem(header, dem, structures, lonlat_to_src_crs, nodata=-9999):
    """structures: [{"type": "dam"|"barrage"|"canal"|"drainage",
                      "geometry": [[lon,lat], ...],  # line, >=2 points
                      "height_m": float, "half_width_m": float}, ...]

    Returns (modified_dem, structure_mask, structure_kind) where
    structure_kind is a uint8 grid (0=none, 1=dam,2=barrage,3=canal,4=drainage)
    for 3D visualization coloring."""
    from shapely.geometry import LineString
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    transform = from_origin(header["xllcorner"], header["yllcorner"] + nrows * cs, cs, cs)
    kind_code = {"dam": 1, "barrage": 2, "canal": 3, "drainage": 4}

    modified = dem.copy()
    structure_mask = np.zeros((nrows, ncols), dtype=bool)
    structure_kind = np.zeros((nrows, ncols), dtype="uint8")
    applied = []

    for s in structures:
        spec = STRUCTURE_TYPES.get(s["type"])
        if spec is None or len(s.get("geometry", [])) < 2:
            continue
        height_m = float(s.get("height_m", spec["default_height_m"]))
        half_w = float(s.get("half_width_m", spec["default_half_width_m"]))
        line = LineString([lonlat_to_src_crs(lon, lat) for lon, lat in s["geometry"]])
        footprint = line.buffer(half_w)
        mask = rasterize([(footprint, 1)], out_shape=(nrows, ncols),
                          transform=transform, fill=0, dtype="uint8").astype(bool)
        mask &= (dem != nodata)
        if not mask.any():
            continue
        if spec["raise_dem"]:
            modified[mask] += height_m
        else:
            modified[mask] -= height_m
        structure_mask |= mask
        structure_kind[mask] = kind_code[s["type"]]
        applied.append({"type": s["type"], "label": spec["label"], "height_m": height_m,
                         "half_width_m": half_w, "cells_affected": int(mask.sum())})

    return modified, structure_mask, structure_kind, applied


STRUCTURE_TAPER = 0.45  # crest/floor ring inset toward centroid, as a fraction of the base half-width


def structure_footprints(header, structures, lonlat_to_src_crs, nodata=-9999, dem=None, aoi_polygon_src=None):
    """Real structure footprint polygons (the SAME buffered-corridor shape
    apply_structures_to_dem rasterizes to condition the physics DEM), for
    rendering each dam/barrage/canal/drainage as its own discrete 3D object
    on top of the UNMODIFIED terrain -- instead of the terrain mesh itself
    being permanently bumped up/down along the structure's footprint.

    Previously the 3D view's terrain WAS the structure-conditioned DEM
    (apply_structures_to_dem's output), so a dam rendered as a raised welt
    baked into the ground shape and a canal as a trough cut into it -- the
    same "buildings render as terrain blobs" problem this app already fixed
    for buildings (see CLAUDE.md), just never applied to Tab 6's own
    structures. The physics DEM (what LISFLOOD-FP actually solves on) is
    UNCHANGED by this -- apply_structures_to_dem is still what conditions
    that -- this only changes what geometry the 3D VIEW extrudes.

    Two real-looking-vs-broken-looking issues fixed here (found from a
    rendered screenshot, not guessed): (1) the buffered footprint used
    shapely's default ROUND end caps, which rendered as a rounded pill/
    capsule -- nothing like an engineered embankment or an excavated
    channel. Raised (dam/barrage) structures now use a SQUARE cap that
    extends the footprint past the drawn line's endpoints by one
    half-width, the way a real embankment ties into the higher ground on
    both sides instead of stopping on a rounded point; lowered (canal/
    drainage) structures use a FLAT cap ending exactly at the drawn
    endpoint, since a dug channel doesn't need an "abutment". (2) nothing
    clipped the footprint to the actual AOI -- a structure drawn near the
    AOI edge (or a round cap ballooning past an endpoint) could render
    extending straight off the terrain mesh into empty space, since
    apply_structures_to_dem's OWN rasterize-then-mask-by-nodata step
    already discards anything outside the valid DEM for the PHYSICS
    conditioning, but this function had no equivalent clip for the VISUAL
    footprint. Fixed by intersecting the buffered footprint against the
    DEM's own rectangular grid extent, and against aoi_polygon_src (the
    real drawn polygon, reprojected to this DEM's CRS) when given, so the
    rendered object can never extend past the visible terrain.

    Returns [{"poly_rc": [[row,col],...], "height_m": float, "raise": bool,
    "color": "#hex", "label": str, "taper": float}, ...], in the same
    schema buildings_utils.get_building_footprints uses (plus "taper"),
    so viewer3d_utils can extrude both through the identical (up-or-down,
    optionally tapered) prism code path."""
    from shapely.geometry import LineString, box as shapely_box

    ncols, nrows, cs = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    xll, yll = header["xllcorner"], header["yllcorner"]
    grid_bbox = shapely_box(xll, yll, xll + ncols * cs, yll + nrows * cs)
    clip_region = grid_bbox if aoi_polygon_src is None else grid_bbox.intersection(aoi_polygon_src)

    def to_rc(x_m, y_m):
        col = (x_m - xll) / cs
        row = nrows - 1 - (y_m - yll) / cs
        return [row, col]

    footprints = []
    for s in structures:
        spec = STRUCTURE_TYPES.get(s["type"])
        if spec is None or len(s.get("geometry", [])) < 2:
            continue
        height_m = float(s.get("height_m", spec["default_height_m"]))
        half_w = float(s.get("half_width_m", spec["default_half_width_m"]))
        line = LineString([lonlat_to_src_crs(lon, lat) for lon, lat in s["geometry"]])
        cap_style = 3 if spec["raise_dem"] else 2  # square (ties in) for barriers, flat for dug channels
        footprint = line.buffer(half_w, cap_style=cap_style).intersection(clip_region)
        polys = footprint.geoms if footprint.geom_type == "MultiPolygon" else [footprint]
        for poly in polys:
            if poly.is_empty or poly.geom_type != "Polygon" or len(poly.exterior.coords) < 4:
                continue
            poly_rc = [to_rc(x, y) for x, y in poly.exterior.coords]
            footprints.append({
                "poly_rc": poly_rc, "height_m": height_m, "raise": spec["raise_dem"],
                "color": spec["color"], "label": spec["label"], "taper": STRUCTURE_TAPER,
            })
    return footprints


def compare_flood_results(baseline_max_depth, protected_max_depth, valid_mask, dry_thresh=0.05):
    """Cell-by-cell before/after comparison -- the core "displacement"
    metric: flooding doesn't just vanish, it's routed elsewhere by the
    structure, so this reports both the reduction AND any newly-flooded
    cells, not just a single net percentage that could hide the latter."""
    base_wet = (baseline_max_depth > dry_thresh) & valid_mask
    prot_wet = (protected_max_depth > dry_thresh) & valid_mask

    base_area_cells = int(base_wet.sum())
    prot_area_cells = int(prot_wet.sum())
    protected_dry = base_wet & ~prot_wet     # cells the structure kept dry
    newly_flooded = prot_wet & ~base_wet     # cells now wet that weren't before ("displaced" water)
    still_wet = base_wet & prot_wet

    reduction_pct = (100.0 * len(np.flatnonzero(protected_dry)) / base_area_cells) if base_area_cells else 0.0
    displacement_pct = (100.0 * len(np.flatnonzero(newly_flooded)) / base_area_cells) if base_area_cells else 0.0

    base_depth_sum = float(baseline_max_depth[base_wet].sum()) if base_area_cells else 0.0
    prot_depth_sum = float(protected_max_depth[prot_wet].sum()) if prot_area_cells else 0.0
    depth_change_pct = (100.0 * (prot_depth_sum - base_depth_sum) / base_depth_sum) if base_depth_sum else 0.0

    diff_mask = np.zeros(valid_mask.shape, dtype="uint8")  # 0=unchanged/dry,1=protected(now dry),2=newly flooded,3=still wet
    diff_mask[protected_dry] = 1
    diff_mask[newly_flooded] = 2
    diff_mask[still_wet] = 3

    return {
        "baseline_flooded_cells": base_area_cells,
        "protected_flooded_cells": prot_area_cells,
        "protected_dry_cells": int(protected_dry.sum()),
        "newly_flooded_cells": int(newly_flooded.sum()),
        "reduction_pct": reduction_pct,
        "displacement_pct": displacement_pct,
        "depth_change_pct": depth_change_pct,
        "diff_mask": diff_mask,
    }
