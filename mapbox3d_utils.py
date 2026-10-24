"""
Mapbox-native 3D scene builder for the main SAILAB app's own flood runs --
same rendering approach proven in the standalone Area 3D tool
(osm_buildings_3d.py): real Mapbox global terrain (`map.setTerrain`), real
building/road extrusion via `fill-extrusion`/`line` layers, instead of
this app's own custom Three.js scene (viewer3d_utils.py), which has been
the source of several camera-angle and terrain-sampling illusions/bugs
this session (see CLAUDE.md). Mapbox's renderer has no custom camera or
terrain-height-sampling code of this app's own to get wrong.

Real trade-off, stated plainly rather than hidden: the PHYSICS still runs
on this app's own uploaded Pakistan SRTM (unchanged -- flood_model.py,
buildings_utils.burn_buildings_into_dem, etc. are untouched by this
module). Only the 3D VISUAL terrain uses Mapbox's own global elevation
tileset instead of this app's DEM, the same choice already made for Area
3D -- both are real, independent SRTM-derived elevation sources at
similar resolution, so they read the same for the flood-plain terrain
this app targets, but they are not pixel-identical. Buildings, roads, and
the flood depth overlay are all positioned by real lon/lat (not by the
DEM's own row/col grid), so they align correctly regardless of which
terrain mesh renders underneath them.

Animated over the run's own real per-timestep data (`water["depth_stack"]`
plus each building/road's own real depth trajectory), the same real
principle already used by the Three.js viewer's animated building/road
risk color: a building/road reads as "at risk" only while it's genuinely
wet right now, fading back toward green as the storm actually drains --
not a single static peak-based verdict. The flood depth overlay itself
uses a Mapbox `canvas` source (the correct, performant way to drive a
live-updating raster layer in Mapbox GL -- re-samples a live <canvas>
element every render frame, no per-frame image re-decode), continuously
interpolated between the solver's own saved timesteps exactly like the
Three.js viewer's water surface (never a discrete jump-cut between saved
frames).
"""
import base64
import json
import os

import numpy as np

from damage_utils import DEPTH_DAMAGE_CURVES_ASIA
from infra_utils import RISK_BANDS

RISK_COLOR_DEFAULT = "#8f7a63"

# Real, proactive safeguard threshold -- see build_mapbox_flood_scene's own
# post-write size check for the full real incident this catches (a real
# 2.64GB mapbox_3d.html shipped in production before this existed).
MAPBOX_SCENE_SIZE_WARN_BYTES = 150_000_000


def f32_b64(arr):
    return base64.b64encode(np.asarray(arr, dtype="<f4").tobytes()).decode("ascii")


def build_mapbox_flood_scene(out_path, title, bbox, aoi_geom, mapbox_token,
                              building_footprints=None, road_lines=None,
                              water=None, legend_html="", z_exag=1.3):
    """
    bbox: [lon_min, lat_min, lon_max, lat_max] -- the run's own real AOI
        bounding box (already reprojected from the DEM's UTM grid by the
        caller, same value saved in run_meta.json).
    aoi_geom: the real drawn/uploaded polygon (GeoJSON dict, WGS84).
    building_footprints: [{"coords_lonlat": [[lon,lat],...], "height_m":
        float, "depths": [float, ...] or None}, ...] -- real footprint +
        the SAME real per-timestep depth trajectory
        (water["depth_stack"] sampled at this building's own centroid)
        already computed for the Three.js viewer's animated coloring, so
        both 3D views of the same run classify buildings identically.
    road_lines: [{"coords_lonlat": [[lon,lat],...], "depths": [...] or
        None, "speeds": [...] or None}, ...] -- likewise, the same real
        depth+speed trajectories already computed for the Three.js view.
    water: {"depth_stack": (T,nrows,ncols), "save_int_min": float,
        "dry_thresh": float, "max_depth": float, "duration_hr": float},
        same structure viewer3d_utils.build_3d_scene's `water` param uses.
        None disables the flood animation/overlay entirely (buildings/
        roads still render, at a fixed dry-default color).

        Optional `water["color_stops"]`: [[fraction 0-1, [r,g,b]], ...] --
        reuses this exact same canvas-overlay rendering path for a static
        (single-frame) non-depth raster, e.g. a real damage-fraction
        surface or a categorical protected/displaced diff mask, by giving
        it its own color ramp instead of the default blue-for-water one.
        `dry_thresh`/`max_depth` still control which cells are hidden and
        how the 0-1 fraction is scaled, same as for real depth.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    center = [(lon_min + lon_max) / 2, (lat_min + lat_max) / 2]

    buildings_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "properties": {"height_m": fp["height_m"], "depths": fp.get("depths")},
         "geometry": {"type": "Polygon", "coordinates": [fp["coords_lonlat"]]}}
        for i, fp in enumerate(building_footprints or [])
    ]}
    roads_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "properties": {"depths": rl.get("depths"), "speeds": rl.get("speeds")},
         "geometry": {"type": "LineString", "coordinates": rl["coords_lonlat"]}}
        for i, rl in enumerate(road_lines or [])
    ]}

    water_payload = None
    if water is not None and water.get("depth_stack") is not None:
        depth_stack = water["depth_stack"]
        water_payload = {
            "depthB64": f32_b64(depth_stack.flatten()),
            "nFrames": int(depth_stack.shape[0]), "nrows": int(depth_stack.shape[1]), "ncols": int(depth_stack.shape[2]),
            "saveIntMin": water.get("save_int_min", 10.0), "dryThresh": water.get("dry_thresh", 0.05),
            "maxDepth": water.get("max_depth", 0.0),
            "colorStops": water.get("color_stops"),
            "cellsizeM": water.get("cellsize_m"),
            "groundB64": (f32_b64(np.nan_to_num(water["ground"], nan=-9999.0).flatten())
                          if water.get("ground") is not None and water["ground"].shape == depth_stack.shape[1:] else None),
            "durationHr": water.get("duration_hr"),
            "peakMmHr": water.get("rain_mm_hr"),
            # Real absolute calendar start time + real per-interval rain
            # rate series (see rainfall_csv_utils.py) -- present only for
            # a run driven by a real uploaded rainfall CSV. When absent,
            # the client derives an equivalent triangular rate curve from
            # peakMmHr/durationHr instead (see the JS rain-intensity code
            # below) -- either way the rain animation's own intensity
            # tracks a real (or real-equivalent) rate, never a constant.
            "startTimeIso": water.get("start_time_iso"),
            "rainTimesHr": water.get("rain_times_hr"),
            "rainRatesMmHr": water.get("rain_rates_mm_hr"),
        }
        # Real per-timestep velocity vectors (already de-staggered onto
        # the depth grid's own cell centers -- see fis_app.py's
        # _read_water_stack), for the optional "Flow" particle overlay
        # below. Off by default, user-toggled -- additive, never forced
        # on the animation the way the removed static arrows once were.
        vx_stack, vy_stack = water.get("vx_stack"), water.get("vy_stack")
        if vx_stack is not None and vy_stack is not None and vx_stack.shape == depth_stack.shape:
            water_payload["vxB64"] = f32_b64(vx_stack.flatten())
            water_payload["vyB64"] = f32_b64(vy_stack.flatten())


    # Real "Export Video" -- exists only when there's a real animation to
    # record (a run with per-timestep water). No native client-side path
    # to MP4 exists, so this records the live map canvas via
    # captureStream()+MediaRecorder (real-only WebM) and uploads it to the
    # existing /export-video route for a real ffmpeg transcode -- that
    # backend route already existed (built for the old, now-deleted
    # Three.js viewer) but had no caller left in this Mapbox-only app,
    # confirmed by grepping for MediaRecorder/captureStream/export-video
    # anywhere in this file before assuming it needed reviving. Whatever
    # the user currently has toggled on (Flow, satellite/streets) is
    # exactly what gets recorded, same principle as Export PNG.

    payload = {
        "bbox": bbox, "center": center, "aoiGeom": aoi_geom,
        "buildings": buildings_fc, "roads": roads_fc,
        "water": water_payload,
        "huizingaBuiltUp": DEPTH_DAMAGE_CURVES_ASIA["Built-up"],
        "riskBands": [[name, hi] for name, hi in RISK_BANDS],
        "zExag": z_exag, "mapboxToken": mapbox_token,
    }

    here = os.path.dirname(os.path.abspath(__file__))

    def _asset(name):
        with open(os.path.join(here, name), encoding="utf-8") as fh:
            return fh.read()

    # JSON in a <script>: neutralise any "</" so a string can't close the tag.
    payload_json = json.dumps(payload).replace("</", "<\\/")
    body = (_asset("mapbox3d_body.html")
            .replace("__TITLE__", title)
            .replace("__ZEXAG__", f"{z_exag:.1f}")
            .replace("__LEGEND__", legend_html or "")
            .replace("__ABOUT_STYLE__", "" if legend_html else "display:none"))
    html = ("<!doctype html>\n<title>" + title + "</title>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<link href=\"https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.css\" rel=\"stylesheet\">\n"
            "<style>\n" + _asset("mapbox3d_style.css") + "\n</style>\n" + body + "\n"
            "<script src=\"https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.js\"></script>\n<script>\n"
            + _asset("mapbox3d_app.js").replace("__PAYLOAD__", payload_json) + "\n</script>\n")
    with open(out_path, "w") as f:
        f.write(html)

    # Real, proactive safeguard -- a general size check, not a re-fix of
    # the specific 2.6GB embedded-depth-stack bug already fixed above
    # (that fix caps the biggest single contributor, but this checks the
    # ACTUAL final file regardless of cause, so a future oversized payload
    # from any source -- a huge building/road vector count, a new field
    # added here later, etc. -- gets flagged instead of silently shipping
    # another "ran fine, nothing visible on the map" incident). Logged
    # next to the scene itself (same directory, easy to find alongside a
    # run's own run.log), never raised -- a large file should still be
    # written and usable if a browser CAN handle it; this only makes the
    # failure mode visible instead of silent when one can't.
    try:
        real_size = os.path.getsize(out_path)
        if real_size > MAPBOX_SCENE_SIZE_WARN_BYTES:
            warn_path = os.path.join(os.path.dirname(out_path) or ".", "mapbox3d_size_warning.log")
            with open(warn_path, "a") as wf:
                wf.write(f"{os.path.basename(out_path)}: {real_size:,} bytes -- exceeds the real "
                         f"{MAPBOX_SCENE_SIZE_WARN_BYTES:,}-byte safety threshold and may fail to load "
                         f"in a real browser (see CLAUDE.md's own 2.64GB incident for the class of bug "
                         f"this is meant to catch early). Real, largest known contributors: an animated "
                         f"depth/velocity stack over a huge domain (already downsampled above a real "
                         f"element-count target, but a future different payload could reintroduce this), "
                         f"or an unusually large building/road feature count for this AOI.\n")
    except OSError:
        pass
