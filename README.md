# Pakistan Flood Information System

A single-page web app for drawing or uploading an area anywhere in
Pakistan, running a real physics-based flood simulation on it, and seeing
the results — rainfall context, building & infrastructure risk, land-use
damage & hotspots, and "what if we built a dam here" protection scenarios
— all in synchronized 2D and 3D.

> **Maintenance note**: this file is the user/developer-facing reference
> for what the app does and how to run it. Whenever a feature, endpoint,
> file, or behavior described here changes, update this file in the same
> change — don't let it drift. `CLAUDE.md` is the separate, append-only
> decision/bug log (why things are the way they are, and the history of
> bugs found and fixed); this file is the current-state description. Keep
> both in sync, but don't duplicate CLAUDE.md's narrative history here —
> link to it instead when the "why" matters.

## What it does

Draw a polygon (or upload a shapefile/GeoJSON) anywhere in Pakistan, then:

1. Run a real 2D flood simulation on it (LISFLOOD-FP, CPU or GPU), with
   real buildings conditioned into the terrain as flow obstacles.
2. See real rainfall context for the same area.
3. See which real buildings, roads, hospitals/schools/clinics, power
   infrastructure and trees are at risk, and how badly.
4. See damage-percentage breakdown by land-use class, with statistically
   real hotspot analysis.
5. Sketch proposed protective infrastructure (dams, barrages, canals,
   drainage channels) and see how much flooding they'd prevent — and how
   much they'd displace elsewhere.

Everything is shown on one page: a 2D Mapbox map and a single switchable
3D view on the left, every panel of information stacked on the right.

## Quick start

```bash
cd ~/flood
venv/bin/python fis_app.py
```

Then open `http://<host>:8000/`. The server binds `0.0.0.0:8000` (chosen
because 5060 is on Chrome/Edge's hardcoded unsafe-port blocklist and 8080
was already in use on the original dev machine — see `CLAUDE.md` if you
need to change it). If you're accessing it from a different machine than
the one it's running on and a direct connection doesn't work, tunnel it:

```bash
ssh -L 8000:localhost:8000 user@<host>
```

then open `http://localhost:8000/` on your own machine.

**Requirements**: a `.env` file next to `fis_app.py` with
`MAPBOX_TOKEN=<your Mapbox access token>` (never commit this — it's
already gitignored). Without it the 2D map silently does nothing (every
3D view is plain DEM/hillshade and doesn't need it — see below). The
`imageio-ffmpeg` Python package (bundles a static `ffmpeg` binary, no
system/root install needed) is required for the video-export feature —
already in the venv.

## Layout

- **Left column**: a 2D Mapbox map on top (draw/upload your area here, or
  draw protection-scenario structures once you have a completed run), and
  a single 3D view below it, switchable between five overlays via the row
  of buttons above it — Terrain, Flood, Buildings, Damage, Protection.
  Each button is disabled until the data behind it actually exists (e.g.
  "Damage" stays disabled until a run finishes and the damage analysis
  completes), and the view auto-switches to whichever is most relevant as
  you progress (e.g. to "Flood" the instant a run finishes).
- **Right column**: every panel's information stacked and always visible
  — Area & Storm controls, Rainfall, Flood Model status, Buildings &
  Infrastructure risk, Damage & Hotspot analysis, Protection scenario
  controls. Nothing is gated behind a tab click; panels populate
  reactively as their data becomes available.
- Both the map/3D split and the left/right split have a draggable resizer.
- Every 2D map and every 3D view has an **Export PNG** button. Every 3D
  view also has a **fullscreen** button (bottom-right) and a **Reset
  View** button (top-left) that snaps the camera back to the default
  overview angle — orbit/zoom is bounded (can't get inside the terrain
  or absurdly far away) but if a view ever looks disorienting, Reset View
  always recovers it. Any 3D view that animates water (Flood, Protection)
  also has an **Export Video** button that records one full real-time
  animation loop and downloads it as an MP4 (see "Exporting the flood
  animation as video" below); exporting always resets to the default
  camera angle first, so the downloaded video is never at the mercy of
  whatever angle the view happened to be left at.

### Drawing on the map

One Mapbox Draw instance handles both the AOI and protection-scenario
structures on the same map — which one a drawn shape becomes depends on
its geometry type: draw a **polygon** to (re)define the area of interest;
draw a **line** to stage a protection structure (pick its type and
height/depth in the right panel, then "Add drawn line as structure"). No
mode toggle needed, and both can be visible over the AOI at once.

Editing a polygon is debounced (settles ~400ms after you stop moving
vertices) before re-fetching terrain/context, and each of those fetches
is tagged with a request token so a slow, stale response can never
overwrite what a newer one already showed — see `CLAUDE.md` for the bug
this fixed if you're touching that code.

## The flood model

Physics engine is `flood_model.py`: LISFLOOD-FP's local-inertial
("acceleration") solver (Bates et al. 2010), CPU (OpenMP) or GPU (CUDA,
built separately — see `CLAUDE.md` for the build notes), driven by:

- **Peak rainfall** (mm/hr) and **duration** (hr) — the storm itself.
- **Real buildings** (Google Open Buildings), on by default — each
  building's *full real footprint* (not just its centroid cell) is
  rasterized onto the DEM and raised so it acts as a flow obstacle,
  matching exactly what's drawn in the 3D view.
- **Advanced solver parameters** (optional, sensible defaults if left
  alone): friction (Manning's n, default 0.05), CFL number (default
  0.7), infiltration rate (mm/hr subtracted from rainfall, default 0,
  treated as the AMC-II/average baseline), **Antecedent Moisture
  Condition** — a continuous 0–100 slider (0=dry/AMC-I, 50=average/
  AMC-II, default, 100=wet-saturated/AMC-III) for the real SCS/NRCS
  classification of how saturated the ground already was before this
  storm; linearly interpolates between the same three real reference
  multipliers rather than forcing a pick among exactly three presets,
  since actual antecedent soil moisture is a continuum. Scales the
  infiltration rate by that multiplier — this solver has a single flat
  loss rate rather than a full Curve-Number soil model, so this is a
  documented simplification, not a full SCS-CN recalculation (see
  `CLAUDE.md`). The slider's live label always shows the exact
  multiplier it currently implies. And an extra inflow (m³/s on a chosen
  edge, e.g. an upstream river feeding in, default 0).

A run is cached by a hash of the drawn/uploaded polygon's exact geometry
plus every one of the above parameters — an identical request returns in
milliseconds. **Changing any parameter (or drawing a different shape)
always produces a fresh cache entry**, but note that fixes to how a run
is *computed* (e.g. the building-footprint fix above) don't retroactively
change already-cached results — only a fresh run picks up such fixes.

### 3D flood view

The animated water in the 3D "Flood" view continuously interpolates
between the solver's real saved timesteps (never fabricating new
physics) at a fixed ~9-second loop regardless of how many timesteps were
saved, so it's always smooth and always fast. A bottom bar gives you
play/pause, a scrubber (labeled in real simulated time), and a 0.25×–4×
speed control. The same scene also shows real buildings and real roads,
each colored by their CURRENT risk — not a single fixed verdict for the
whole video. A building's color comes from the real Huizinga damage
curve applied fresh every frame to its own sampled depth at that instant;
a road's comes from a real DEFRA hazard rating (depth × (speed + 1.5))
recomputed every frame from its own depth and velocity at that instant.
Both ride the same green→yellow→orange→red gradient: green while
genuinely dry or barely wet, rising toward red while actually underwater,
and fading back toward green as that spot's own floodwater recedes —
there is no separate "Buildings" 3D view; everything lives in this one
scene alongside the water.

Every 3D view in the app (not just this one) shows the plain DEM/
hillshade terrain, no satellite imagery — the same view of the ground
the flood model itself computes on, and its shape is clipped exactly to
your drawn/uploaded AOI polygon (real geometric clipping against the
polygon's own edges, not just a coarse per-cell approximation — see
`CLAUDE.md` if a boundary ever looks off). A purely visual (never
changes the actual simulated depth, which was already fully solved by
LISFLOOD-FP) falling-rain layer is shown only while the run's own storm
duration is actually active (a run that also has drain-down time after
the storm shows dry rain-free draining once it's over). The water
surface itself rises as one continuous, smoothly-fading sheet — its
height and opacity both track the real interpolated depth continuously,
so the waterline never pops in blocky cell-by-cell. The legend includes
a real gradient bar for the water depth ramp (matching the actual render
colors, labeled with the run's own 0–max depth in metres), not just a
text label.

### Exporting the flood animation as video

Click **Export Video** on any water-animation 3D view. It records the
live WebGL canvas for exactly one full real-time loop (~9s) via the
browser's native `MediaRecorder`, uploads the result to `POST
/export-video`, which transcodes it to MP4 with `ffmpeg` (via the
`imageio-ffmpeg` package's bundled static binary — no system-level ffmpeg
install needed) and downloads it. `MediaRecorder` only ever produces
WebM client-side; the server-side transcode is what actually makes the
downloaded file an MP4. Requires a browser that supports
`canvas.captureStream()` (all current desktop browsers do).

## The other panels

- **Rainfall**: real forecast + recent rainfall (Open-Meteo, no key
  needed), independent of whether a flood run exists yet. (River/stream/
  lake context was removed at the user's request — see `CLAUDE.md`.)
- **Buildings & Infrastructure**: real OSM roads, hospitals/schools/
  clinics, power infrastructure, and trees, each risk-classified by
  sampling the run's own depth/hazard rasters at its footprint — not an
  invented score. Buildings additionally get a **damage severity** rating
  from the same published Huizinga et al. 2017 (JRC) depth-damage curve
  used for Tab 5's land-use damage analysis (real, absolute-depth
  calibrated — not a guess); a `submersion_fraction` (depth vs. the
  building's own height) is still reported alongside it as a separate
  stat. A building's height is its real OSM `height`/`building:levels`
  tag where one exists (checked directly against Overpass: only ~2% of
  buildings in a dense Pakistani urban test area are tagged, so this is
  real data used where mapped, not the norm) and a documented
  single-story estimate (6m) otherwise. The text panel's band/hazard
  breakdowns are still based on each run's peak depth (a single "how bad
  did it get" summary); the 3D view's own building/road color instead
  animates continuously with the real per-timestep data — see "3D flood
  view" above. There is no separate "Buildings" 3D view — real building
  footprint polygons (not boxes; each wall follows the real terrain
  height at its own two corners, so it doesn't float or sink on sloped
  ground) and roads are baked directly into the same Flood 3D view as the
  water animation. Only buildings whose real centroid falls inside the
  actual drawn/uploaded AOI polygon are included — tested directly against
  the true polygon, not just the coarser per-cell terrain mask, so a
  building can't render floating next to the terrain right at the AOI's
  edge (see `CLAUDE.md`).
- **Damage & Hotspots**: real ESA WorldCover land-use (10m, global, free)
  run through published Huizinga et al. 2017 (JRC) depth-damage curves
  per class, plus real Getis-Ord Gi* hotspot analysis (`esda`/`libpysal`)
  on the resulting damage surface — not an ad-hoc heuristic. Computed
  lazily on first request (heavier than the core run) and cached after.
- **Protection Scenarios**: draw proposed dams/barrages (raise the
  ground along their footprint) or canals/drainage pipes (lower it) and
  re-run the flood model with them in place, on the *same*
  building-conditioned DEM the baseline run used. A drainage structure's
  size parameter is a pipe **diameter** (its trench is cut roughly as
  deep as the pipe is wide), not a dam-style height — the UI's input
  label and default value switch automatically when you pick "Drainage
  pipe". Reports both the area protected (kept dry) **and** the
  displacement — cells that became newly flooded because the structure
  routed water there instead. Protection is never reported as free.

## Data sources

| Data | Source | Notes |
|---|---|---|
| Elevation (DEM) | User-supplied Pakistan-wide SRTM | ~29m, EPSG:32642; falls back to a bundled Bahawalpur sample if not present |
| Buildings | Google Open Buildings (via source.coop geoparquet) | Real footprint polygons, no height field (assumed 6m) |
| Roads, critical infra, power, trees, waterways | OpenStreetMap (Overpass API) | Requires explicit `Accept`/`User-Agent` headers — Overpass 406s requests without them |
| Rainfall | Open-Meteo | Free, no key, real forecast + recent observed |
| Land use (LULC) | ESA WorldCover 10m | Public COG, windowed read |
| Depth-damage curves | Huizinga et al. 2017 (JRC), Asia region | Digitized at published curve points |

## API reference

All endpoints are on the single Flask app (`fis_app.py`).

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | The single-page app |
| `/dem-source` | GET | Which DEM is currently active |
| `/export-video` | POST | Transcodes an uploaded WebM recording of a 3D animation to MP4 |
| `/area/polygon` | POST | Compute area (km²) of a drawn polygon |
| `/area/upload` | POST | Parse an uploaded shapefile (.zip) or GeoJSON |
| `/area/context` | POST | Real rainfall for a polygon, independent of any run |
| `/area/terrain3d` | POST | 3D terrain-only view for a polygon (no run needed) |
| `/run` | POST | Kick off (or return cached) a flood run |
| `/runs/<id>/progress.json` | GET | Poll run status; includes the full summary once done |
| `/runs/<id>/infra.json` | GET | Full per-feature infrastructure risk detail |
| `/runs/<id>/damage.json` | GET | Damage/hotspot analysis (computed lazily, then cached) |
| `/runs/<id>/damage3d.html` | GET | 3D LULC + hotspot view |
| `/runs/<id>/<filename>` | GET | Any other file in that run's directory (e.g. `report.html`, `viewer_3d.html`) |
| `/protect/run` | POST | Kick off (or return cached) a protection scenario |
| `/protect/<id>/progress.json` | GET | Poll protection-scenario status |
| `/protect/<id>/protect3d.html` | GET | 3D protected-terrain + before/after diff view |

## File reference

| File | Role |
|---|---|
| `fis_app.py` | Flask app: routes, page template, async run/protection orchestration |
| `flood_model.py` | LISFLOOD-FP CLI wrapper — DEM/rain/bci/par generation, run, 2D HTML report |
| `viewer3d_utils.py` | Shared Three.js 3D scene builder used by every 3D view in the app |
| `polygon_utils.py` | Polygon geometry hashing, upload parsing, area calc, polygon-masked DEM cropping |
| `buildings_utils.py` | Google Open Buildings fetch, full-footprint DEM conditioning, footprint extraction for 3D |
| `infra_utils.py` | OSM roads/critical/power/trees fetch + risk classification |
| `rainfall_rivers_utils.py` | Open-Meteo rainfall |
| `damage_utils.py` | ESA WorldCover fetch, Huizinga/JRC damage curves, Getis-Ord Gi* hotspots |
| `protection_utils.py` | Protective-structure DEM conditioning, before/after comparison |
| `progress_utils.py` | Parses LISFLOOD-FP's own log output for live progress |
| `build_3d_viewer.py` | Older standalone 3D-viewer generator, superseded by `viewer3d_utils.py` for this app (kept for the unrelated PluviSim CLI tool) |
| `gui_app.py`, `pak_flood_tool.py` | Earlier, simpler tools this app grew out of — not part of the FIS app's request path |
| `CLAUDE.md` | Decision log: settled scope, and every bug found + fixed, with root causes |

## Known limitations

- A building's walls follow the real terrain height at each of its own
  footprint corners individually (not a single flat base value), so it
  never floats above or sinks into sloped ground; its flat roof sits at
  the highest corner's height plus the building's own height.
- Building footprints are capped at 6,000 per 3D view purely for browser
  rendering performance (largest-by-area first) — a very dense AOI's 3D
  view may not show every last small structure, though all of them are
  still used in the actual physics.
- A code fix to how a run is computed only affects *new* runs — cached
  results already on disk keep whatever behavior was current when they
  were generated (see `CLAUDE.md` for concrete cases this has mattered).
- Video export requires a browser with `canvas.captureStream()` and
  `MediaRecorder` support (all current desktop Chrome/Firefox/Edge/Safari
  do; some embedded/older WebViews may not).
