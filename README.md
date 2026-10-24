# SAILAB (Simulation & Analytics for Inundation, Loss, Assets & Built Environment)

A web app for flood risk in Pakistan: draw an area, run the LISFLOOD-FP 2D flood model on the GPU, see buildings,
roads and critical facilities at risk, estimated loss, mitigation scenarios, coastal surge with tide, GLOF and more.
`fis_app.py` is the Flask server (port 8000). The full engineering log is `CLAUDE.md`; every dataset, constant and
formula is in `DATA_SOURCES_AND_METHODS.md` and `SAILAB_Technical_Reference.pdf`.

## This repository

| Path | What |
|---|---|
| `*.py`, `mapbox3d_*` | The server, physics wrapper (`flood_model.py`), data and analysis modules, 3D viewer assets |
| `tests/` | Fast tests (`pytest tests -m "not slow"`); slow tests run the real solver and need the data |
| `lisflood_patches/` | Patched LISFLOOD-FP CUDA source (coastal stage boundary); re-apply after re-extracting LISFLOOD-FP 8.2 |
| `global_data/*.py` | Scripts that download or build the derived datasets |
| `sailab-plugin/` | `sailab-client`, a thin Python client so other projects can call a running SAILAB server |

## Not in git (too large or private)

Terrain (Pakistan SRTM, 13 GB), Google Open Buildings tiles, the road/hospital/school/power/rail datasets, soil, land
cover, rainfall (ERA5, IMERG, IDF cubes), the tide-gauge record, run caches and uploads. `data_paths.py` lists every
external path (override with `SAILAB_<NAME>`), and `/data-status` on a running server reports what is missing.
Copy `.env.example` to `.env` for the Mapbox token and optional PostGIS URL.

## Run

    python -m venv venv && venv/bin/pip install -r requirements.txt
    venv/bin/python fis_app.py          # http://localhost:8000

Needs the LISFLOOD-FP 8.2 binary built for your GPU (see `CLAUDE.md`, "Solver upgrade") and the datasets above.

---

# SAILAB — Pakistan Flood Information System + global flood prediction layer

A single-page web app for drawing or uploading an area **anywhere on
Earth** (fast, offline-fast for Pakistan; on-demand global elevation
everywhere else, opt-in — see "Global coverage" below), organized into
three tabs matching the tiered global-flood-prediction architecture from
`global-flood-prediction-research.md`: **Tier 1** (a trained global
rainfall→runoff LSTM estimates discharge for your AOI), **Tier 2** (a
near-instant HAND-based discharge→inundation lookup), and **Tier 3** (a
real physics-based flood simulation, LISFLOOD-FP, with real buildings/
infrastructure risk, visualized in real-time 3D via Mapbox). See "Global
flood prediction layer" below for what's real, what's a documented
approximation, and what's an honestly-stated limitation.

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

1. **Tier 1**: get a real discharge estimate for the AOI from the trained
   global streamflow LSTM.
2. **Tier 2**: preview an approximate flood extent for any discharge
   instantly (HAND + rating curve, no solver run).
3. **Tier 3**: run a real 2D flood simulation (LISFLOOD-FP, GPU), with
   real buildings conditioned into the terrain as flow obstacles, and see
   which real buildings, roads, hospitals/schools/clinics, power
   infrastructure and trees are at risk (and how badly) — visualized in
   real-time 3D via Mapbox.

Everything is shown on one page: a 2D Mapbox map and a single 3D
(Mapbox flood) view on the left, three tier tabs on the right.

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

- **Left column**: a 2D Mapbox map on top (draw/upload your area here),
  and a single 3D view below it that shows whichever tier tab is
  currently active, each at its own real capability — no view-switcher
  buttons, since which one to show is already implied by the tab:
  - **Tier 1** shows real Mapbox-native terrain for the drawn AOI (a
    discharge estimate has no flood extent of its own to show).
  - **Tier 2** shows the real HAND-based flood depth overlay for
    whichever discharge is currently set, refreshed immediately after
    clicking "Preview instant flood extent."
  - **Tier 3** shows the real animated LISFLOOD-FP flood scene
    (`mapbox_3d.html`) once a run finishes, or a plain placeholder before
    that.
- **Right column**: "Draw or upload an area" (+ the opt-in global-DEM
  checkbox) always visible above three tabs, one per tier of the
  global-flood-prediction architecture:
  - **Tier 1 · Discharge (ML)**: Rainfall (+ pluvial/flash-flood risk)
    and the trained global LSTM's discharge estimate for this AOI.
  - **Tier 2 · Instant Flood**: the HAND-based instant discharge→
    inundation lookup (a "Use this in Tier 2" button on the Tier 1
    result carries its estimate straight into Tier 2's discharge field).
  - **Tier 3 · Physics Run**: Storm/solver controls, the real LISFLOOD-FP
    run, Flood model status, and Buildings & Infrastructure risk — this
    is what drives the Mapbox 3D view on the left.
- Both the map/3D split and the left/right split have a draggable resizer.
- The 2D map and the 3D view each have an **Export PNG** button. The 3D
  view also has a **fullscreen** button (bottom-right), a **Reset View**
  button (top-left) that snaps the camera back to the default overview
  angle (orbit/zoom is bounded — can't get inside the terrain or
  absurdly far away), and an **Export Video** button that records one
  full real-time animation loop and downloads it as an MP4 (see
  "Exporting the flood animation as video" below); exporting always
  resets to the default camera angle first.

### Drawing on the map

Draw a polygon with Mapbox Draw to set the area of interest — editing it
is debounced (settles ~400ms after you stop moving
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

## Global coverage

`current_dem_source()` (`fis_app.py`) picks the right DEM per AOI: the
local Pakistan-wide SRTM (the DEM already downloaded onto this machine)
when the drawn/uploaded polygon falls inside its real coverage (fast, no
network — unchanged from before), and otherwise an on-demand mosaic of
**Copernicus DEM GLO-30** (AWS Open Data, `s3://copernicus-dem-30m`, free,
no auth, no bulk download — GDAL reads only the byte ranges each request
needs) reprojected to the AOI's own local UTM zone and cached to disk
(`global_dem_cache/`, keyed by rounded bbox).

**The global DEM fetch is opt-in, not automatic.** A single checkbox
("Use open-source global DEM...", unchecked by default) sits under the
area-draw controls in the UI — every DEM-needing route (`/run`,
`/area/instant-flood`, `/area/terrain3d`) requires a `use_global_dem: true`
request field to reach outside the downloaded Pakistan SRTM at all; without
it, an AOI outside Pakistan gets a clear, actionable refusal (a
`GlobalDemNotEnabled` error with `needs_global_dem_consent: true`) instead
of silently fetching external data. `/area/predict-discharge` (Tier 1) and
`/area/context` (rainfall/pluvial) are NOT gated by this, since neither
touches the DEM at all. Once enabled for an AOI, the entire existing
pipeline — real physics (LISFLOOD-FP GPU), real buildings, real roads,
real land use, real damage/hotspots — works **anywhere on Earth with
land**, not just Pakistan, since every other data source this app uses
(Google Open Buildings, OSM, Open-Meteo, ESA WorldCover) was already
global. Copernicus DEM GLO-30 is a surface model (includes buildings/
canopy noise), not FABDEM's bare-earth correction — FABDEM's license (CC
BY-NC-SA) doesn't fit a shipped product, and this app already conditions
real buildings into the DEM everywhere else, so this is a documented,
workable limitation, not a blocker.

(This also fixed a real, previously-silent bug: `PAKISTAN_DEM`'s path had
a `/flood/` segment that never existed on this machine, so every existing
Pakistan run had actually been using the small bundled Bahawalpur fallback
sample this whole time, not the real 13.48GB Pakistan-wide SRTM — found
while generalizing this function, not investigated on its own. See
`CLAUDE.md`.)

## Global flood prediction layer

Implements the tiered architecture from `global-flood-prediction-
research.md` — real components, each with its honest scope and
limitations stated plainly rather than oversold, per that document's own
core finding that a single global real-time high-resolution physics
simulation isn't feasible for anyone, including Google.

- **Tier 0/2 — instant HAND-based inundation lookup** (`hand_utils.py`,
  `POST /area/instant-flood`): computes Height Above Nearest Drainage
  (D8 flow direction/accumulation via `pysheds`, real depressions-filled/
  flats-resolved terrain) for the AOI's own DEM, then converts a given
  discharge into a flood depth grid via a synthetic Manning's-equation
  rating curve sized by real hydraulic-geometry scaling (Leopold &
  Maddock 1953) — the same real technique NOAA's own Office of Water
  Prediction uses for CONUS Flood Inundation Mapping (Zheng et al. 2018,
  "GeoFlood"). Milliseconds, no solver run. A documented approximation
  (no surveyed channel cross-sections exist for an arbitrary global AOI)
  — the existing `/run` endpoint (real LISFLOOD-FP physics, Tier 3) is
  still the authoritative result; this exists to make "instant,
  everywhere" possible at all, the UI's own "Instant flood preview"
  panel. The HAND computation itself (not just the DEM crop) is cached
  per-AOI (`hand_cache/`, keyed by geometry+DEM source) — scrubbing the
  discharge value over the same drawn area only pays the flow-direction/
  depression-fill cost once (~24x faster on a cache hit, confirmed live).
  No free, no-registration precomputed HAND dataset actually exists
  (checked directly — MERIT Hydro is registration-gated, HydroSHEDS
  doesn't ship a raw HAND layer, the University of Texas/NFIE HAND
  product is CONUS-only) — see CLAUDE.md for what was checked.
- **Tier 1 — global rainfall→runoff LSTM** (`streamflow_lstm.py`,
  `train_streamflow_model.py`, `streamflow_inference.py`, `POST
  /area/predict-discharge`): a recurrent model trained across ~630 real
  basins spanning 7 regions/continents (US, UK, Australia, Brazil, Chile,
  Canada, Central Europe) from the **Caravan** dataset (Kratzert et al.
  2023) — real ERA5-Land forcing + real streamflow + real HydroATLAS
  static attributes, basin-level train/held-out split so the reported
  accuracy reflects genuine ungauged-basin generalization, not
  memorization. Applied to an arbitrary drawn AOI via nearest-analog-basin
  regionalization (find the closest real Caravan gauge, borrow its static
  attributes, combine with this AOI's own real current ERA5 forcing) — a
  real, published, named technique, honestly distinct from true
  per-location generalization (see `streamflow_inference.py`'s docstring).
  **Substitutes for GRDC** (the brief's primary-recommended gauge source,
  registration-gated — couldn't be completed autonomously this session);
  Caravan is built from the same GRDC-lineage regional archives, not a
  lesser alternative in method, just narrower coverage (see
  `global_data/fetch_caravan_sample.py`'s docstring for the honest gap).
  Already trained (checkpoint at `global_data/streamflow_model/
  streamflow_lstm.pt`) — 12 epochs on all 630 basins, held-out-basin NSE
  0.45 (94 basins never seen in training, spanning all 7 regions — see
  `global_data/streamflow_model/train_meta.json` and CLAUDE.md for the
  full run details). Re-run `venv/bin/python train_streamflow_model.py`
  any time to retrain (e.g. after pulling more basins via
  `fetch_caravan_sample.py`) — always trains on GPU, with the same
  cold-start-retry pattern this app already uses for LISFLOOD-FP on this
  machine.
- **Tier 4 — pluvial/flash-flood risk** (`pluvial_utils.py`, folded into
  `POST /area/context`): compares real forecast/recent rainfall
  (Open-Meteo) against a real 20-year per-point climatology (also
  Open-Meteo, ERA5-reanalysis-backed, no key) to produce a risk tier —
  and states its own honest data-resolution ceiling: no global radar
  network exists (confirmed in the research brief), so this is
  structurally capped at ~10km/hourly satellite-blended precipitation
  everywhere except NOAA MRMS's real CONUS radar footprint (checked
  reachable, `radar_tier_available_here` reports whether an AOI falls in
  it — real MRMS GRIB2 ingestion itself is flagged as a concrete next
  step, not implemented this session; see the module docstring for why).
- **Inundation-extent library per discharge level** (`hand_utils.
  build_inundation_library`, `POST /area/instant-flood-library`): sweeps
  12 log-spaced discharge levels (1–5000 m³/s) through the same instant
  HAND lookup and caches the result — no such library exists as a
  downloadable dataset anywhere (checked directly; every real one, e.g.
  NOAA's own, is a derived product, not distributed), so this builds the
  real thing per-AOI instead. 0.042s cold / 0.014s cached in real testing.
- **Real per-cell soil-informed infiltration** (`soil_utils.py`,
  `global_data/fetch_soilgrids_pakistan.py`, Pakistan only): real sand/
  clay/bulk-density from SoilGrids 2.0 (ISRIC, free, no registration),
  clipped and cached locally (~35–48MB each, not a global bulk download),
  classified via the standard USDA texture triangle and looked up against
  the published Rawls, Brakensiek & Miller (1983) saturated hydraulic
  conductivity table. Checked directly against LISFLOOD-FP 8.2's own
  source that its ACC/CUDA solver (the only one this app runs) has no
  code path for a per-cell distributed infiltration file at all — unlike
  Manning's friction, which it genuinely does support per-cell — so this
  computes one real, area-weighted mean rate for the whole AOI instead of
  per-cell physics the solver would silently ignore. A "Use real
  soil-derived rate" checkbox (off by default) opts a run into it;
  `run_meta.json`'s `infiltration_source` field always says which was
  actually used. See CLAUDE.md for the full investigation.
- **Not implemented, stated rather than hidden**: full global HAND/HydroATLAS
  precompute (this app computes HAND per-AOI on demand, not as a literal
  one-time global batch job — the brief itself says that's a multi-week,
  multi-TB job out of solo-team reach); coastal/storm-surge modeling
  (Tier 2 of the brief's own roadmap, phase 2 — not attempted this
  session); the full Caravan dataset (this session used a genuinely
  global but reduced ~630-basin sample fetched via targeted HTTP range
  requests rather than the full 12.5GB/~15,000-basin archive, for session
  time reasons — `fetch_caravan_sample.py --help` via editing
  `BASINS_PER_REGION` is how to pull more).

## Data sources

| Data | Source | Notes |
|---|---|---|
| Elevation (DEM) | User-supplied Pakistan-wide SRTM inside its coverage; Copernicus DEM GLO-30 (AWS, on-demand) everywhere else | ~29m Pakistan / ~30m global, reprojected to local UTM; falls back to a bundled Bahawalpur sample if neither is available |
| Global streamflow training data | Caravan (Kratzert et al. 2023) via Zenodo | Free, no registration; real GRDC-lineage regional archives + HydroATLAS attributes |
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
| `/area/context` | POST | Real rainfall + pluvial/flash-flood risk tier for a polygon, independent of any run |
| `/area/instant-flood` | POST | Tier 0/2: instant HAND-based discharge→inundation lookup (no LISFLOOD-FP run) |
| `/area/instant-flood-library` | POST | Tier 0/2: cached inundation-extent stats across 12 discharge levels for an AOI |
| `/area/predict-discharge` | POST | Tier 1: trained global LSTM discharge estimate via nearest-analog-basin regionalization |
| `/area/terrain-mapbox3d` | POST | Tier 1's own 3D view: real Mapbox terrain for the AOI, no run needed |
| `/area/instant-flood-mapbox3d` | POST | Tier 2's own 3D view: real Mapbox terrain + the HAND flood overlay for a given discharge |
| `/area/terrain3d` | POST | 3D terrain-only view for a polygon (no run needed) |
| `/run` | POST | Kick off (or return cached) a flood run |
| `/runs/<id>/progress.json` | GET | Poll run status; includes the full summary once done |
| `/runs/<id>/infra.json` | GET | Full per-feature infrastructure risk detail |
| `/runs/<id>/<filename>` | GET | Any other file in that run's directory (e.g. `report.html`, `mapbox_3d.html`) |

## File reference

| File | Role |
|---|---|
| `fis_app.py` | Flask app: routes, page template, async run orchestration |
| `flood_model.py` | LISFLOOD-FP CLI wrapper — DEM/rain/bci/par generation, run, 2D HTML report |
| `viewer3d_utils.py` | Shared Three.js 3D scene builder (still used by Tier 1's Area 3D terrain preview) |
| `mapbox3d_utils.py` | Mapbox-native 3D flood scene builder — the app's one 3D visualization |
| `polygon_utils.py` | Polygon geometry hashing, upload parsing, area calc, polygon-masked DEM cropping |
| `buildings_utils.py` | Google Open Buildings fetch, full-footprint DEM conditioning, footprint extraction for 3D |
| `infra_utils.py` | OSM roads/critical/power/trees fetch + risk classification |
| `rainfall_rivers_utils.py` | Open-Meteo rainfall, river discharge/crossing context, LSTM forcing fetch |
| `damage_utils.py` | ESA WorldCover fetch + Manning's-n-from-land-cover (used for per-cell friction) |
| `progress_utils.py` | Parses LISFLOOD-FP's own log output for live progress |
| `build_3d_viewer.py` | Older standalone 3D-viewer generator, superseded by `viewer3d_utils.py` for this app (kept for the unrelated PluviSim CLI tool) |
| `gui_app.py`, `pak_flood_tool.py` | Earlier, simpler tools this app grew out of — not part of the FIS app's request path |
| `global_dem_utils.py` | On-demand Copernicus DEM GLO-30 mosaic fetch + reprojection for any global AOI (Tier 0/3) |
| `hand_utils.py` | HAND computation (`pysheds`) + discharge→inundation synthetic rating curve lookup (Tier 0/2) |
| `pluvial_utils.py` | Pluvial/flash-flood risk tiering from real rainfall vs. real per-point climatology (Tier 4) |
| `streamflow_lstm.py` | Global streamflow LSTM model + Caravan dataset loader (Tier 1) |
| `train_streamflow_model.py` | Trains the streamflow LSTM on the Caravan sample, basin-level held-out split |
| `streamflow_inference.py` | Applies the trained LSTM to an arbitrary AOI via nearest-analog-basin regionalization |
| `global_data/fetch_caravan_sample.py` | Pulls a real, global, cross-continental basin sample from Caravan via targeted HTTP range requests (no full 12.5GB download) |
| `soil_utils.py` | Real per-cell Ksat (Rawls et al. 1983) from local SoilGrids sand/clay/bdod data; area-weighted mean for the AOI |
| `global_data/fetch_soilgrids_pakistan.py` | Downloads and caches Pakistan-clipped SoilGrids 2.0 sand/clay/bdod layers locally |
| `global-flood-prediction-research.md` | The research brief this whole layer implements — architecture, data catalog, honest feasibility limits |
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
