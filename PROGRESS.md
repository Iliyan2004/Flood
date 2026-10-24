# Fluvial Flood Scenario Dashboard — Progress Log

Plan: `/home/mudassir/.claude/plans/precious-hugging-jellyfish.md` (approved 2026-09-04)

A LISFLOOD-FP flood-scenario dashboard: the user draws/uploads an AOI and picks
a rainfall return period + duration, and gets a real depth/hazard map back. As
of Phase 7 a single RP + duration drives BOTH forcings — rain-on-grid over the
AOI **and** river inflow at the boundary — in one combined run.

## Status: working end-to-end (Phase 7 complete); QA/calibration ongoing

| Phase | Status |
|---|---|
| 0. Build LISFLOOD-FP binary | ✅ done |
| 1. Manning's-n / infiltration tables from land cover | ✅ done (planning-level defaults, confirmed OK by user) |
| 2. Watershed catalog | ⬜ **descoped** — no hydrologic role in rain-on-grid, revisit only as a UX nicety later |
| 3. AOI → LISFLOOD-FP input pipeline | ✅ done, verified on real data |
| 4. Backend API (FastAPI) | ✅ done, verified end-to-end via curl |
| 5. Dashboard wiring | ✅ done, verified in a real browser |
| 6. QA | 🔄 ongoing (see caveats below) |
| 7. Combined pluvial + fluvial forcing | ✅ done, verified end-to-end |

## Log

- **2026-09-04** — Unzipped LISFLOOD-FP source into `LISFLOOD_SimEngine/`. Read
  the existing `LISFLOOD_FP_Modeling_Plan.md`, surveyed `Source_Data/` (DEM,
  land cover, rainfall RP rasters, streamflow RP data — all ready) and
  `WebGIS Dashboard/index.html` (static mock UI, no backend).
- Decided with user: pure 2D rain-on-grid model (no sub-grid channels, no
  discharge boundary) — simpler input set, confirmed technically supported
  by the LISFLOOD-FP source (`bcifile`/`bdyfile` fully optional).
- Found "GB" in the modeling plan = **Gilgit-Baltistan** (prior regional
  project), not Great Britain — its "proven tables" (Manning's-n,
  infiltration, hydraulic-geometry regression) don't actually exist on disk;
  building them fresh in Phase 1.
- Found sibling project `Urban-LISFLOOD/urban-flood-solver/backend/` — a
  working FastAPI job-queue backend for a different (urban pluvial) solver;
  using its architecture as a template for Phase 4, not its code.
- Plan approved. Starting Phase 0: build the LISFLOOD-FP binary
  (CMake/OpenMP, `_NETCDF=1` already set in `config.default.cmake`). GPU on
  this machine is an NVIDIA GB10 (Blackwell, cc 12.1) — CMake's default
  `CMAKE_CUDA_ARCHITECTURES=61` will need overriding later for a CUDA build;
  starting with CPU/OpenMP only.
- **Phase 0 complete.** `/mnt/external-ssd/.../LISFLOOD_SimEngine` sits on a
  FUSE-mounted (NTFS-style, root-owned) external SSD that doesn't support
  executable permissions — so the build directory lives on the local ext4
  disk instead: `/home/mudassir/lisflood_build/lisflood` (source stays on
  the SSD, only the build/output dir moved). `libnuma-dev`/`netcdf-dev`
  weren't installed system-wide and there's no passwordless sudo, so both
  were installed into a dedicated conda env `lisflood_build`
  (`conda create -n lisflood_build -c conda-forge libnuma libnetcdf`,
  no system changes) and the binary's RUNPATH points at it — runs standalone,
  no `LD_LIBRARY_PATH` needed. CUDA hidden from PATH for this configure
  (CPU/OpenMP build only, as planned). Ran a from-scratch smoke test (no
  such fixture existed in the repo): 20×20 synthetic sloped DEM, closed
  boundary (no `.bci`/`.bdy`), `dynamicrainfile` NetCDF (2× 20mm/hr hours +
  2h recession), `acceleration` + `routing` + `hazard`. Result: stable mass
  balance (error terms ~0.0000 throughout), completed in 1.52 min compute
  time, produced `.max`/`.maxHaz`/`.inittm`/`.maxtm`/`.totaltm` — max depth
  0.26 m, plausible for the forcing. Confirms the rain-on-grid, no-SGC,
  no-boundary-file approach works end-to-end.

- Starting Phase 1: building the land-cover → floodplain-Manning's-n and
  land-cover → infiltration lookup tables (don't exist anywhere on disk,
  confirmed in planning). Next: inspect `lc2025Pakistan.tif`'s class legend
  (ESRI 10-class scheme expected) and build the reclassify step.
- **Phase 1 complete (first pass).** Confirmed `lc2025Pakistan.tif` uses the
  ESRI 10-class Sentinel-2 legend (sampled pixel values 1/2/5/7/8/11 match
  Water/Trees/Crops/BuiltArea/BareGround/Rangeland). Built
  `pipeline/landcover_params.json` (class → Manning's n + infiltration
  mm/hr, planning-level defaults from Chow's roughness table / SCS-informed
  loss rates — **not field-calibrated, flag for review**) and
  `pipeline/landcover.py` (fast LUT-based reclassify, converts infiltration
  to LISFLOOD-FP's expected m/s). Design choice: reclassify **at AOI-clip
  time** in Phase 3 rather than materializing a nationwide n/infiltration
  raster now — same result, avoids duplicating a large raster nationwide
  before we know the AOI shape. Validated standalone against a real 2000×2000
  window of the Pakistan raster — correct per-class n/infiltration values
  confirmed.
- Checked in with user: (1) Manning's-n/infiltration placeholders confirmed
  OK to proceed with, no calibration event available — revisit once the app
  is testable. (2) User correctly pointed out a watershed catalog has no
  hydrologic role in a pure rain-on-grid model (no channel network/catchment
  routing needs it) — it would only ever have been a UX convenience.
  **Descoping Phase 2** for now; draw/upload AOI is sufficient and is what
  Phase 3 needs anyway. A watershed picker can be bolted on later purely as
  a nicer AOI-selection UX if wanted.
- Starting Phase 3: the AOI → LISFLOOD-FP input pipeline (clip DEM/land
  cover/rainfall to an AOI, build .par + dynamicrainfile NetCDF, run, parse
  outputs).
- **Phase 3 complete, verified end-to-end on real data.** Built
  `pipeline/aoi_pipeline.py`: clips the national DEM (windowed read, never
  loads the full 13.5GB file), resamples land cover + reclassifies via
  Phase 1's LUT, resamples the matching RP rainfall raster onto the same
  grid, writes a closed-boundary `.par` + `dynamicrainfile` NetCDF, runs the
  Phase 0 binary, parses `.mass`/`.max`/`.maxHaz`. Ran a real test: a
  ~4.4km AOI around Charsadda (Kabul/Swat river confluence, historically
  flood-prone), RP25/6hr storm. Result: 169×144 grid, 72.5s wall time,
  mass-balance error terms held at 0.0 throughout (stable), max depth
  2.74m, 11.0 km² inundated (of ~19 km² AOI), max DEFRA hazard 4.99 — all
  physically plausible for this location/storm. Confirms the whole chain
  (clip → forcing → run → parse) works on real Pakistan data, not just the
  synthetic smoke test.
- User wants forward momentum toward something testable ("once I test the
  application, we can make changes later") — proceeding straight to Phase 4
  (backend API) without pausing for another check-in.
- **Phase 4 complete, verified end-to-end via curl.** Built
  `backend/` (FastAPI, following the Urban-LISFLOOD sibling's structural
  pattern — `app.py`/`routes.py`/`schemas.py`/`jobs.py`/`pipeline.py`/
  `config.py`/`rendering.py`/`results_cache.py`): `POST /scenarios` (AOI
  GeoJSON + RP + duration → job id), `GET /scenarios/{id}` (poll status +
  result summary), `GET /scenarios/{id}/depth.png` (rendered overlay,
  classed to the dashboard's exact existing legend colours) and
  `/depth.asc` (raw grid). `ProcessPoolExecutor`-backed `JobManager`,
  disk-backed job records, results cache keyed by (AOI, RP, duration,
  recession). Server started on port 8734 (8123 was already taken by
  another user's process on this shared machine — picked a free one).
  Ran the Charsadda scenario through the real API: submit → poll →
  done, result matched the Phase 3 CLI test exactly (max depth 2.741m,
  11.0 km² inundated, hazard 4.99), fetched `/depth.png` (144×169 RGBA,
  correct), and confirmed a repeat identical request hits the cache
  (~instant, no recompute).
- Starting Phase 5: wiring `WebGIS Dashboard/index.html` to this real API
  (currently still the mock `drawMockFloodExtent()`/`Math.random()` flow).
- **Phase 5 complete, verified in a real headless browser (Puppeteer +
  system Chromium, not just reading the source).** Changes: removed the
  streamflow section (RP chips, baseflow slider) and `hydroChart` entirely
  — no discharge boundary in a rain-on-grid model, those fields were
  meaningless; added `API_BASE` config + a `/health` check that flips the
  header badge live/unreachable; fixed the AOI payload gap (`aoiLayer` was
  never converted to GeoJSON — now `aoiLayer.toGeoJSON()` on submit);
  replaced the mock run handler with a real `POST /scenarios` + 2s-interval
  poll of `GET /scenarios/{id}`; replaced `drawMockFloodExtent()` with a
  real `L.imageOverlay` of the backend's `depth.png`, positioned from the
  real `wgs84_bounds`; `rainChart` now plots the actual uniform-over-duration
  rainfall profile the backend used (computed from real returned stats, not
  fabricated); stat cards now show real inundated area / max depth / max
  hazard / mean rainfall.
- End-to-end browser test (served the dashboard on :8899, backend on :8734,
  drove it with headless Chromium): uploaded a real ~4 km² AOI near
  Charsadda through the actual upload code path, selected 50-yr/3hr,
  clicked Run, polled to completion, got real stats (3.29 km² inundated,
  2.47 m max depth, hazard 3.89, 43 mm mean rainfall) and a correctly
  georeferenced depth overlay — **the flood extent visibly traces the real
  river channel** on the basemap even with no explicit channel network,
  because the DEM's own topography naturally channelizes rain-on-grid flow.
  Screenshot confirms full layout renders correctly.
- Found and fixed two real bugs during this test (not test-harness
  artifacts): (1) a rendering exception (e.g. if the Chart.js CDN fails to
  load) was getting caught by the *polling* error handler and misreported
  as "lost connection" — isolated `showResults()` in its own try/catch so
  a render failure can't masquerade as a network failure; (2) `rainChart`
  creation wasn't guarded, so any Chart.js hiccup would abort the whole
  results render (stats + overlay included) instead of just the chart —
  now wrapped so the rest of the panel still populates. (A third issue,
  Puppeteer's native file-upload hitting a sandbox 0-byte-file quirk, was
  a test-harness-only problem, not an app bug — worked around in the test
  script, no dashboard code change needed.)
- Currently running for live poking: backend `http://127.0.0.1:8734`
  (uvicorn, `Fluvial_Flooding/backend/`), dashboard
  `http://127.0.0.1:8899/index.html` (python http.server over
  `WebGIS Dashboard/`). Both are plain background processes on this
  machine, not managed services — restart per Phase 0/4 notes if killed.
- **QA hardening:** `backend/config.py` had a `MAX_AOI_AREA_KM2` guardrail
  defined but never enforced anywhere — a real gap on a shared multi-user
  machine (a submitted whole-country AOI would try to clip/run against it).
  Added the check to `POST /scenarios` (`routes.py`): computes AOI area in
  EPSG:32642 before submitting, rejects >5000 km² with a 400. Verified: a
  Pakistan-bbox AOI is rejected (`"AOI is 2404161 km2, exceeds..."`), a
  normal AOI still proceeds.
- **Known limitation, not yet fixed:** the results cache key hashes the
  *exact* incoming `aoi_geojson` JSON structure, so a bare `Polygon`
  geometry and a `Feature`-wrapped version of the identical shape produce
  different cache entries (confirmed - the dashboard sends `Feature` via
  `layer.toGeoJSON()`, a raw `curl` test sending bare `Polygon` for the same
  area missed the dashboard's cached run). Not incorrect, just a missed
  cache-hit opportunity - normalize to geometry-only before hashing if this
  matters later.
- **Chart.js CDN dependency removed entirely.** User hit the exact failure
  mode flagged earlier (`ReferenceError: Chart is not defined`) in their own
  browser, not just the sandboxed test network — confirming it was a real,
  reproducible problem, not a test artifact. Root-caused: fixed by removing
  the fragile dependency rather than adding another fallback. Deleted the
  `cdnjs.cloudflare.com/.../Chart.js` script tag and replaced
  `drawRainChart`'s Chart.js call with a small dependency-free Canvas 2D bar
  chart (`drawBarChart` — gridlines, y-axis ticks, bars, x labels,
  devicePixelRatio-aware). Re-verified in the same real-browser harness: no
  console errors, chart renders correctly (3 bars at 14.4 mm/hr + zero tail
  for a 50yr/3hr scenario), rest of the results panel unaffected either way
  now regardless of network conditions.
- **Not yet done (flagged, not silently skipped):** rainfall bias-correction
  against WAPDA/IRSA gauges (modeling plan caveat #1 — this build uses the
  RP rasters as-is); no exposure/building-footprint layer for an "assets at
  risk" stat; `recession_hr` is a fixed per-request value, not adaptive to
  AOI drainage characteristics; backend has no auth/rate-limiting (fine for
  a trusted-network screening tool per Urban-LISFLOOD sibling's same
  stance, revisit if this ever leaves this machine).

---

## Phase 7 — Combined pluvial + fluvial (rain-on-grid + river inflow)

Extended the working rain-on-grid pipeline with fluvial forcing. One RP +
duration selection now drives BOTH forcings in a single LISFLOOD-FP run (they
coexist and sum — verified in the solver loop, `iterateq.cpp:148-161`). The
rain-on-grid path was not rebuilt.

**New/changed files:** `pipeline/fluvial.py` (new), `pipeline/build_streams_cache.py`
(new), `pipeline/aoi_pipeline.py` (`.par` builder, `.mass` parser, orchestration),
`backend/{schemas,pipeline,results_cache}.py`, `WebGIS Dashboard/index.html`.

### Input that did NOT exist (the plan assumed it did)
TDX-Hydro stream line geometry is **not on disk** — the only local reach data is
a point-per-reach lookup (`pakistan_linknos.parquet`: linkno/lon/lat, no lines,
no connectivity). Fetched the authoritative source instead: GEOGLOWS v2 per-VPU
stream GPKGs on S3. Pakistan spans 4 VPUs (412/218/411/415). A remote
`/vsis3` bbox read was tried first and abandoned (the Pakistan bbox covers most
of each VPU, so random-access range requests stalled >20 min); sequential
download at ~7.7 MB/s took ~4 min. Result cached to
`Source_Data/StreamFlows/Input/03_streams_gis/pakistan_streams.gpkg`
(124,448 reaches, 1.06 GB, incl. cross-border upstream reaches); raw downloads
deleted. Runtime has **no network dependency**.

### Three bugs caught before they shipped (each would have been silently wrong)
1. **Stream digitisation direction was the opposite of the usual convention.**
   This data digitises DOWNSTREAM→UPSTREAM (first vertex = downstream node).
   Verified two independent ways on a 414-reach sample: topology (first vertex
   nearer its own DSLINKNO in 407/410) and DEM elevation (last vertex *higher*
   in 138/142; mean rise 11.6 m). The natural assumption would have inverted
   every inflow/outflow classification — injecting discharge at the wrong end
   and putting the outlet upstream. Now guarded at runtime by
   `verify_digitisation_direction()`. Corroborating symptom before the fix: the
   detected "inflow" had a *larger* upstream area than the "outflow", which is
   impossible since area accumulates downstream.
2. **SGC channel type 2 divides by the side-slope parameter** (`pow(SGCbfH,
   -1/sl)/(sl+1)`, `sgc.cpp:462`) — with `s=0` that is a divide-by-zero → NaN.
   Types 3/4 *overwrite* `SGCwidth` from the side slope, discarding the
   hydraulic-geometry widths. Switched to **type 1 (rectangular)**, which uses
   width + bank-full depth only.
3. **Channel width must NOT be capped at the cell size.** `sgc.cpp:436-439`
   and `:1104` explicitly handle "channels wider than a cell". Capping would
   misrepresent large rivers *and*, because depth is derived from width
   (`SGCz = bank - r*w^p`), crush their depths to near nothing.

Also fixed: **the results cache key had no model version**, so a pre-fluvial
cached result was silently served after the model changed (it looked like
fluvial forcing did nothing). Added `MODEL_VERSION` to the key
(`backend/results_cache.py`).

### Units — the GB trap, verified through the model
A `.bci` `P`-type QVAR point source takes **m²/s (flux per unit width)**, not
m³/s, on a projected CRS (`sgc.cpp:1039-1041`; with `latlong` it *would* be
m³/s). Conversion is `Q_m3s / cellsize`, asserted in
`scs_triangular_hydrograph()`. Reproduces the plan's own reference value
(1463 m³/s ÷ 29.16 m = 50.17 m²/s), and confirmed end-to-end by the model:
injected peak **432.614 m³/s** vs `.mass` **peak Qin 432.594** (0.005% agreement).

### File formats (read from the parser source, not the docs)
`.bci` edge `<N|S|E|W> <c1> <c2> FREE`, point `P <x> <y> QVAR <name>` (real-world
coords; no comment lines — token 1 is read with `fscanf("%s")`). `.bdy` line 1 is
always skipped, then `<name>` / `<N> hours` / rows of **`<value> <time>`** (value
first — the reverse of the intuitive order). `.pram` line 1 = region count, then
`<index> <type> <p> <r> <s> <n> <m>` (trailing `a` omitted deliberately: a
non-zero `a` makes the model derive depth from catchment area instead of width).
**`SGCchangroup` is effectively required** — without it `CalcSGCz` silently
discards the `.pram` and substitutes uniform `.par` values (`sgc.cpp:320-336`),
so a single-region group grid is emitted alongside it. `SGCbank` is mandatory
whenever `SGCwidth` is set. `adaptoff` must not appear (aborts with acceleration).

### Acceptance results — Charsadda, RP25/6hr, same AOI as the baseline
| metric | rain-only (closed box) | combined (free outlet + inflow) |
|---|---|---|
| max depth | 2.741 m | **5.918 m** |
| inundated area | 10.957 km² | 11.012 km² |
| max hazard (DEFRA) | 4.994 | **18.122** |
| peak Qin | 0 (trivially) | **366.75 m³/s** (= injected peak) |
| peak Qout | 0 (trapped) | **336.64 m³/s** |
| max abs Verror | ~0 (meaningless, closed) | 7.5e-08, not growing |

Mass balance is **real QA again** now the domain is open. Inundated *area*
barely moved because two effects oppose: the free outlet drains previously
trapped ponding while the upstream wave adds channel-corridor flooding — but
depth and hazard rose sharply and the extent is visibly channelised.

**Pluvial fallback (Task 4) verified** on a Thar-desert AOI with no crossings:
`scenario_type = "pluvial (rain-on-grid) only"`, `.bci` contains only the FREE
outlet, no SGC/`.bdy` files emitted, Qin = 0 but **Qout = 1.67** (drains instead
of trapping). Fluvial forcing is auto-detected and additive — never a mode the
user picks, and no new user-facing discharge control was added.

### Scenario labelling — flagged, not buried
RP-T rainfall over the AOI and RP-T discharge at the boundary are **not the same
storm** (discharge integrates a large upstream catchment; rain is local).
Combining them at a co-incident RP is a **conservative screening scenario, not a
joint-probability T-year flood**. Surfaced in the pipeline summary, the API
(`scenario_type` / `scenario_caveat`), and a prominent amber caveat box in the
dashboard.

### Still planning-level — NOT calibrated (flagged in API `model_notes` + UI)
Hydraulic geometry `w = 7.2·Q^0.5` (rp_2 as bankfull proxy); SGC depth
`d = 0.26·w^0.59`; uniform channel n = 0.035; synthetic SCS triangular
hydrograph (time-to-peak tied to storm duration, 2.67 base ratio). Cross-border
reaches absent from the Pakistan-only RP table resolve by walking DSLINKNO
downstream to the nearest in-table reach.

---

## Phase 8 — Design storm, verified run window, real soil-moisture control

Goal set by user: "perfect physics, nothing showoff or fake" + speed/efficiency.
Calibration explicitly deferred (development stage). GPU explicitly parked.

### Why GPU was NOT adopted (investigated, then set aside)
The CUDA build **compiles and works** (`/home/mudassir/lisflood_build_cuda/`,
`-DCMAKE_CUDA_ARCHITECTURES=121`; nvcc 13.0 supports `compute_121` for this
GB10). But it is not a physics upgrade:
- CUDA `acc` solves the **same** local-inertial equations as the CPU build
  (identical Q-centred scheme + Bates et al. 2010 correction) - same answer,
  faster.
- **No SGC anywhere in `cuda/`** (zero references). Higher DEM resolution does
  NOT remove the need for it: radar/photogrammetric DEMs (SRTM, ALOS 12.5 m)
  measure the water SURFACE, not the bed, so the channel *depth* is still
  missing at 12.5 m. SGC supplies that depth.
- **No hazard output** in `cuda/` (zero references) - the DEFRA stat would be lost.
- **`routing` is inert on GPU**: only the *guard* exists (commented out at
  `cuda/acc/cuda_acc_solver.cu:750,850`); the actual mover (`infevap.cpp:134-188`)
  was never ported. Enabling the guard without the mover would set Q=0 on steep
  faces with nothing to move the water - stranding it. Commenting it out is a
  deliberate fail-safe, not an oversight.
Conclusion: GPU buys throughput, costs physics. Kept CPU. Binary left in place
for if/when throughput becomes the binding constraint.

### Rainfall RP rasters - validated properly (prompted by user challenge)
27,822 valid px, textbook DDF: RP100 depths 15.6/40.6/66.1/94.5/124.4 mm and
intensities 15.56 -> 5.18 mm/hr across 1/3/6/12/24 h. **Monotonic in return
period with zero violations.** Two real defects found, both physically
impossible, both from fitting each duration independently:
- 12 h depth > 24 h depth at **16 px** (RP25:1, RP50:4, RP75:5, RP100:6)
- 3 h intensity > 1 h intensity at up to **150 px at RP100** (288 total)
Small (0.06%-0.54%) but they sit exactly where differencing happens.
**Flagged upstream**: `run_stage7_qa.py` checks monotonicity in return period
(passes) but not across duration. Worth fixing at source.

### 2. Alternating-block design storm (replaces uniform-over-duration)
`build_alternating_block_profile()` in `pipeline/aoi_pipeline.py`. Log-log
interpolation of the DDF onto hourly steps, **monotonicity enforced before
differencing** (otherwise those defect pixels produce NEGATIVE rainfall blocks,
which LISFLOOD would apply as evaporation), then blocks ranked per cell and
placed largest-in-the-middle alternating outwards. Blocks are ranked per cell
but positioned identically everywhere, so the whole domain peaks at the same
hour (one storm, spatially varying magnitude) while each cell keeps its own DDF
shape. Mass preserved exactly (max error 5.7e-14 mm).
**Impact is large**: RP100 peak intensity 19.68 mm/hr vs uniform 6.47 mm/hr at
24 h (**3.04x**), 1.55x at 6 h. The uniform profile was systematically
under-stating peak intensity, hence peak runoff and peak depth.

### 3. Simulation window verified instead of guessed
`.mass` parsing now reports peak-volume time, volume/outflow remaining at end,
and a `window_truncated` flag, surfaced through the API and UI. **LISFLOOD's own
`-steady` auto-stop is unusable here** - it triggers on |Qin-Qout| < tol, but
rain-on-grid contributes to the `Rain` column, not `Qin`, so a pluvial run sits
at Qin=Qout=0 at t=0 and would terminate instantly. Charsadda RP25/6h: window
adequate (outflow at end 9.3% of peak, peak inside window).

### 4. Dead UI controls removed / wired
Confirmed by grep that `soilChips`, `startTime`, `simLength` were read **zero**
times - decorative. `startTime` and `simLength` **deleted** (a design storm has
no absolute calendar time, and the window is now derived + verified rather than
user-set). `soilChips` **wired** to a real infiltration-capacity multiplier
(dry x1.5 / normal x1.0 / saturated x0.25) - crude but a genuine physical lever.

### Bugs found and fixed along the way
1. **Cache key omitted `soil_moisture`** - a "saturated" request was served the
   cached "normal" result, making the new control look broken. Fixed by keying
   off the ENTIRE request (`req.model_dump()`) so future fields can never be
   forgotten again.
2. **ASCII writer destroyed infiltration**: `write_ascii_grid` used `%.4f`.
   Infiltration is m/s (~2.8e-06), so **every cell rounded to 0.0000** -
   infiltration was silently disabled in every run since Phase 3. Now `%.6g`.
   Verified: grid now writes 2.93e-07 mean, correctly scaled by soil moisture.

### OPEN ISSUE - infiltration still not applied in SGC mode
Even with the writer fixed, `Rain-(Inf+Evap)` shows **zero** loss and dry vs
saturated give byte-identical results. Traced it: SGC runs dispatch via
`Fast_MainStart` (`lisflood.cpp:771`) into `lisflood2/`, and the only call to
`SGC2_Infil_floodplain_row` is at `sgm_fast.cpp:3097`, inside
`SGC2_UpdateVolumeHeight_block` - i.e. the **`_SGM_BY_BLOCKS` variant**. Our
build sets `_SGM_BY_BLOCKS=0` (`config.default.cmake`), so that path is never
taken and `infilfile` is loaded, reported in the log, and then ignored.
(The non-SGC `IterateQ` path does apply it, at `iterateq.cpp:153`.)
=> RESOLVED (see below).

### Infiltration - full root cause and resolution
Chased to the bottom; three separate defects stacked:
1. **My writer bug** (fixed): `%.4f` rounded m/s values to zero.
2. **Wrong units in my code** (fixed): `input.cpp:904` divides `infilfile`
   values by `/60/60/1000`, i.e. the file is expected in **mm/hr**, NOT m/s.
   The modeling plan said m/s - the source disagrees, and source wins.
3. **The distributed grid is unusable in this build**: `FPInfiltration`
   (`infevap.cpp:28`) ignores `Arrptr->dist_infiltration` entirely and applies
   only the uniform `Parptr->InfilRate`. The distributed grid is consumed at
   exactly one place, `sgm_fast.cpp:3097`, inside the `_SGM_BY_BLOCKS` variant.
   That variant **does not compile** - genuine upstream typo `FP_xwidth` vs
   `Fp_xwidth` at `lisflood_processing.cpp:1799` (verified by building it).
   And `InfilRate` appears **nowhere** in `lisflood2/`, so the SGC solver path
   has no infiltration by any route.

| mode | uniform `infiltration` | distributed `infilfile` |
|---|---|---|
| pluvial-only (`IterateQ`) | works | ignored |
| combined SGC (`Fast_MainStart`) | not implemented | only in non-compiling path |

**Resolution:** pluvial runs now emit the uniform `infiltration` keyword with
the land-cover area-mean rate x the soil-moisture factor (a real effect, loses
spatial variation); SGC runs emit **no infiltration keyword at all** rather
than an `infilfile` line that silently does nothing, and the result carries an
explicit `infiltration_active: false` plus a note. The UI states the limitation
on the control itself and flags "Infiltration: NOT applied in this run".

**Impact of the fix (Thar pluvial AOI, RP25/6h):**
| soil | infiltration | max depth | inundated |
|---|---|---|---|
| dry | 3.21e-06 m/s | 0.143 m | 0.83 km² |
| saturated | 5.35e-07 m/s | 1.486 m | 6.69 km² |
Previously (infiltration silently zero): 1.586 m / 8.96 km². So **every
rain-on-grid run to date over-predicted flooding** by omitting infiltration
entirely. This is the single largest correctness fix so far.

### Verified end-to-end (browser, Phase 8)
Combined Charsadda RP50/3h: alternating-block profile active (peak 17.5 mm/hr
vs 14.4 uniform), window "adequate (drained)", 0% outflow remaining, and
infiltration correctly flagged as NOT applied for the SGC run. Cache key now
keyed off the whole request; MODEL_VERSION=5 so no pre-fix result is reused.

### Still open / next
- `infilfile` spatial variation needs the upstream `_SGM_BY_BLOCKS` typo fixed
  (one-line vendor patch) before distributed infiltration can work at all, and
  SGC-mode infiltration needs upstream support regardless.
- Upstream rainfall pipeline: add cross-duration monotonicity to
  `run_stage7_qa.py` (16 px and up to 150 px violations found).
- theta=0.9 numerical damping not yet examined.
- GPU build compiled and parked (see Phase 8 header).

---

## Phase 9 — Infiltration solved everywhere + upstream contribution

User directive: fix soil moisture irrespective of river / non-river area, and
contribute the fix upstream.

### CORRECTION to my Phase 8 diagnosis
Phase 8 concluded "the SGC path has no infiltration because the code lives in
the non-compiling `_SGM_BY_BLOCKS` variant". **That was wrong.** I verified the
preprocessor nesting programmatically: `SGC2_UpdateVolumeHeight_block` (which
contains the infiltration call at `sgm_fast.cpp:3097`) is invoked
**unconditionally** - it is not inside any `_SGM_BY_BLOCKS` guard. The `_block`
suffix denotes row-block tiling, not the compile flag.

The real cause was **units alone**: `infilfile` is read as **mm/hr** and
converted internally by `/60/60/1000` (`input.cpp:904`). We were writing m/s, so
values arrived 3.6e6x too small - indistinguishable from zero. The modeling plan
(and the LISFLOOD-FP manual) say m/s for this input; the source disagrees, and
the source wins. Lesson: verify against the parser, not the docs.

### Result: infiltration now works in BOTH paths
- **SGC / river runs**: distributed per-cell grid, no code change needed - just
  correct units. Verified on Charsadda RP25/6h: dry 9.35 km² vs saturated
  11.84 km², net rain 3.65e5 vs 1.01e6 m³. Max depth barely moves (5.90 vs
  5.91 m) because that peak is in the channel driven by the inflow hydrograph,
  not by rain - physically correct.
- **Non-SGC / pluvial runs**: required an upstream code fix (below).

Both paths now report `infiltration_mode: "distributed (per-cell)"`. The UI
shows the actual rate instead of the "NOT applied" warning Phase 8 added.

### UPSTREAM CONTRIBUTION -> `contrib/`
`contrib/lisflood-fp-infiltration-fixes.patch` + `contrib/README.md`.
**Verified to apply cleanly against pristine upstream HEAD** (checked in a
throwaway git tree; the first attempt failed because `--ignore-cr-at-eol`
stripped CRs from context lines in this CRLF codebase - regenerated without it).
Care taken to exclude a pre-existing uncommitted `#ifdef _MSC_VER` hunk in
`lisflood.h` that was already in the working tree and is **not our work**.

**Fix 1 - `infilfile` silently ignored on the non-SGC path** (silent wrong
results, no warning). `FPInfiltration` (`infevap.cpp`) always used the uniform
`Parptr->InfilRate` and never touched `Arrptr->dist_infiltration`. Because
`pars.cpp` sets both `calc_infiltration` and `calc_distributed_infiltration`
when `infilfile` is given, a user supplying only `infilfile` gets
`InfilRate == 0` - infiltration entirely disabled - while the log cheerfully
prints "Loading distributed infiltration data Done." Fixed to use the per-cell
grid when present, falling back to the uniform rate. Signature gains `States*`
(prototype + single call site updated).

A/B on identical inputs (Multan urban-fringe AOI, heterogeneous 0-12 mm/hr land
cover, RP25/6h), pre-patch binary vs patched:
| build | max depth | inundated | net rain after losses |
|---|---|---|---|
| before (uniform 1.8 mm/hr) | 2.451 m | 12.851 km² | 5.819e5 m³ |
| after (per-cell 0-12 mm/hr) | 2.293 m | 12.838 km² | 6.188e5 m³ |
6.4% shift in peak depth. Counter-intuitive but coherent: per-cell infiltrates
*less* overall (built-up dominates below the mean) yet *lowers* the peak,
because the depth maximum sits on a vegetated cell absorbing 8-12 mm/hr that the
uniform average had smeared away.

**Fix 2 - `_SGM_BY_BLOCKS=1` does not compile**: `FP_xwidth` vs `Fp_xwidth` at
`lisflood_processing.cpp:1799` (the same line correctly says `Fp_ywidth`).
One-character fix; that path had evidently never been built in this release.

**Also flagged upstream, not patched**: `infilfile` (mm/hr) and the uniform
`infiltration` keyword (m/s) take *different units* with no documentation of
the difference - a 3.6e6x silent error waiting for the next user.

### State of the model now
MODEL_VERSION=6. Infiltration real and per-cell in every run; alternating-block
design storm; verified simulation window; antecedent soil moisture a genuine
control everywhere. Full stack re-verified in a real browser after the rebuild.

---

## Phase 10 — GPU routing ported (upstream contribution #2)

User asked to resolve routing on GPU. Done, validated against the CPU reference.

### The defect
CPU routing is two halves: a **guard** in the inertial flux kernels zeroing Q on
faces steeper than `RouteSfThresh` (`fp_acc.cpp:85,179`), and a kinematic
**mover** walking water downslope at `Routing_Speed` (`Routing()`,
`infevap.cpp`). On GPU only the guard was ever written, and it was **commented
out** — so `routing` was accepted in the `.par`, echoed at startup, and did
nothing. Commenting it out was correct in isolation: enabling the guard with no
mover zeroes Q on steep faces with nothing to move the water, stranding it.

### What was built
Ported the mover to CUDA in `cuda/acc/cuda_acc_solver.cu`:
- `routing_init_func` — derives `FlowDir` (steepest downslope neighbour) and
  `RouteInt` on-device from the DEM, incl. the `dist_routing` slope-dependent
  speed. Computed on GPU because `Simulation::run` never receives `Arrays*`, so
  the host's `FlowDirDEM()` output is unreachable — recomputing avoids widening
  a signature shared by every CUDA solver.
- `routing_accumulate_func` — exact replica of the CPU condition and flow
  clamping; neighbour scatter uses `atomicAdd` (a race on GPU) into `Route_dH`.
- `routing_apply_func` — applies and clears, preserving the CPU's
  accumulate-then-apply two-phase structure.
Then **re-enabled the guard** at both flux sites, now that the mover exists.
Base-class methods are no-ops so fv1/fv2/dg2/acc_nugrid are untouched.

Useful discovery that simplified the port: `GhostRaster::pitch_ACC() == xsz`, so
for the acc solver GPU flat indices match the CPU's `i + j*xsz` exactly — no
index-space translation needed. (Also confirmed the other session's infiltration
kernel indexing by `xsz` rather than `pitch` is therefore correct, not a bug.)

### Validation — exact agreement
60x60 grid, 20% slope (above the 0.1 threshold), 20 mm/hr for 2 h, 4 h sim:
| run | max depth | mean | wet cells |
|---|---|---|---|
| CPU routing ON (reference) | 2.20000 m | 0.055833 | 3480 |
| GPU routing ON (new) | 2.20000 m | 0.055833 | 3480 |
| GPU routing OFF | 2.17200 m | 0.044333 | 3420 |
CPU vs GPU **max|diff| = 0.000000 m, correlation 1.000000**. Routing ON vs OFF
differs by 0.028 m, proving it is active and not a no-op.

### Repo hygiene finding
The working tree contains modifications from **another agent session on this
shared machine** — GPU distributed-infiltration support in `cuda_simulate.*`,
`cuda_solver.cuh`, `cuda_acc_simulate.cu` (its comments reference a
`lisflood_source_bugs_fixed` memory that does not exist in this project's memory
dir, so it came from a different context). It interleaves with the routing work
in all four CUDA files, so `contrib/lisflood-fp-gpu-routing.patch` ships both,
with attribution stated explicitly in `contrib/README.md` — the infiltration
hunks are NOT our work.

Also noted: `testing/**/*.par` files are absent from the working tree though
present in git HEAD (pre-existing; explains why no `.par` template was findable
back in Phase 0).

### Contribution artifacts
`contrib/` now holds two patches, both verified to apply cleanly to pristine
upstream HEAD:
1. `lisflood-fp-infiltration-fixes.patch` — CPU distributed infiltration + the
   `_SGM_BY_BLOCKS` compile fix.
2. `lisflood-fp-gpu-routing.patch` — GPU routing (+ the other session's GPU
   infiltration, attributed).

GPU status unchanged otherwise: still no SGC and no hazard output on the CUDA
path, so GPU remains usable for pluvial-only AOIs. Routing was the one GPU gap
that was fixable without porting a whole subsystem.

---

## Phase 11 — Critical review (audit + independent code review)

User asked for a critical pass over the whole repo. Combined my own audit with an
independent review agent (fresh eyes on code I wrote). **Nothing below is fixed
yet — this is the finding list.**

### CONFIRMED BUGS (reproduced, not just reported)

**B1. Alternating-block guard crashes the job on the exact defect it exists for.**
`_hourly_cumulative_depth` does `maximum.accumulate(P)` then forces
`monotone[-1] = total_depth_mm` back *down*. Where the DDF is non-monotonic
(12h depth > 24h depth — the 16 px measured in the real rasters), the last
increment goes negative, gets clipped to 0, mass no longer sums, and the assert
kills the run. Reproduced: `AssertionError: alternating-block lost mass: max
error 6.000000 mm`. Never fired in testing because Charsadda has
`ddf_fixes=0` — the defective pixels are elsewhere in Pakistan. Under `python -O`
the assert vanishes and the model silently gets less rain than the design depth.
Fix: clamp the interpolated interior with `np.minimum(P, total)` BEFORE the
accumulate, instead of overwriting the endpoint after.

**B2. Inflow hydrograph volume is wrong at both ends of the duration range.**
Measured (realistic sim = duration + 12 h recession):
| storm | volume delivered |
|---|---|
| 1 h | **2.55x too much** |
| 3 h | 1.00x ok |
| 6 h | 1.00x ok |
| 12 h | 0.90x |
| 24 h | **0.69x (31% short)** |
Two separate causes: (a) short storms — the loop's last computed point still
carries ~0.4x peak, then LISFLOOD linearly interpolates from there to the
`(sim_hours, 0)` terminator, injecting a long spurious tail; (b) long storms —
the SCS base time (2.67 x duration = 64 h at 24 h) exceeds the simulation window
(36 h), so the recession limb is truncated. Note the Phase 8 window check did
NOT catch (b): it verifies the *rain* drained, not that the *inflow hydrograph*
completed. Every combined 1h/12h/24h scenario run so far is materially wrong;
3h and 6h (including the validated Charsadda runs) are unaffected.

### HIGH (reported by review, judged real, not yet reproduced)
- **B3. Off-raster AOI silently shifts georeferencing.** `clip_dem` computes the
  transform from the *unclipped* window while `src.read` returns a clipped
  array, so an AOI overlapping the DEM edge georeferences every layer against a
  grid the data doesn't occupy. The `window.width <= 0` overlap check is dead —
  `from_bounds` returns positive width at a far offset for a fully-outside AOI.
- **B4. Rivers passing straight through the domain are dropped.** `detect_crossings`
  skips reaches with both endpoints outside the rectangle, which is the
  through-flowing case, not just the corner clip the comment claims. A trunk
  river longer than a small AOI yields `has_fluvial=False`.
- **B5. SGC width: `MergeAlg.replace` lets a tributary overwrite a trunk.**
  Rasterization is shape-order, so a 5 m tributary can replace a 400 m trunk in
  shared cells — and since depth is derived from width, it crushes depth too.
  Sort ascending by width so the largest wins.
- **B6. Job race + no in-flight dedupe.** `add_done_callback` is registered before
  `state=running` is written, so a fast/failing future can be overwritten back to
  `running` forever. Separately, two identical concurrent requests both compute
  and both write the same `RESULTS_DIR/<key>` non-atomically.
- **B7. AOI guardrail measures the polygon, not the model domain.** The domain is
  the bounding rectangle; a thin diagonal 100 km2 sliver bounds tens of thousands
  of km2. No minimum-area or `is_valid` check either (a bow-tie passes).
- **B8. Silent nodata fills.** Rainfall resample uses `dst_nodata=0.0` and land
  cover leaves uncovered cells at 0 -> an AOI outside coverage produces a
  plausible-looking run with zero rain / fallback roughness, no warning.

### MEDIUM
- `infiltration_mode` is a hardcoded string regardless of solver path;
  `infiltration_active` derives from the uniform mean.
- `LISFLOOD_TIMEOUT_S` and `DEFAULT_RECESSION_HR` are never read; `run_scenario`'s
  `recession_hr=48` default contradicts the schema's 12.
- `returncode == 0` is treated as "outputs exist"; missing `.max`/`.maxHaz`
  surfaces as an opaque FileNotFoundError. `run_lisflood` replaces the whole
  environment (works only because the binary has RUNPATH baked in).
- `verror_growing` uses an absolute `> 1.0` threshold on a domain-size-dependent
  quantity; our runs sit at ~1e-8, so it is a very blunt canary.
- Unbounded memory: the alternating-block arrays are ~6 x (24, rows, cols)
  float64 — >5 GB resident for a 5000 km2 AOI, x2 workers.
- `.max` grid is parsed twice (pipeline + backend).
- `n_fixed` counts cell-hours but is reported as cells.

### MISSING (process / operability)
1. **Zero automated tests.** The only e2e test lives in `/tmp`. Every bug this
   session was caught by hand.
2. No `requirements.txt`/`pyproject.toml`/README; environment not reproducible.
3. Backend is an unsupervised `nohup` process; twice it silently served stale
   physics because an old process held the port. No `/version` endpoint.
4. No cleanup/eviction for `runs/` or `results/`. Root disk is 92% full on a
   shared machine; ~17 MB/run now, ~90 MB/run at 12.5 m.
5. No provenance sidecars and no structured logging — this breaks the convention
   the Rainfall/StreamFlows pipelines already follow (atomic writes, `.json`
   sidecars, `logs/`).
6. No check that the AOI lies within the DEM; 7 hardcoded absolute paths.

### EFFICIENCY
- ~14 MB/run of hourly snapshot grids (`.wd`/`.Vx`/`.Vy`/`.elev` x 25) that
  nothing reads. Disk saving real (19 MB -> 5 MB/run); a timing A/B was
  inconclusive (shared-machine noise exceeded the effect) so no speed claim.
- Rain NetCDF stores 678,050 values for a field varying 5.4% spatially (source
  rainfall is 0.1 deg ~ 11 km, AOIs ~4 km). LISFLOOD accepts coarser rain grids.
- 2 workers x 8 threads = 16 of 20 cores, with no measurement of where OpenMP
  scaling flattens for these small domains.
- Dead config: both `infilfile` and uniform `infiltration` are emitted, but the
  patched FPInfiltration always prefers distributed.

---

## Phase 12 — Phase 11 findings fixed

All confirmed and high-severity items from the review are fixed, verified, and
now covered by tests. `MODEL_VERSION -> 7` (physics changed; v<=6 results must
not be reused).

### Confirmed bugs
**B1 — alternating-block guard lost mass on a non-monotonic DDF.** Root cause
was ORDER: it clamped upward then forced the endpoint back down, leaving the
last interior value above the endpoint and producing a negative final
increment. Now clamps the interior with `np.minimum(P, total)` BEFORE the
running max. Fixing it exposed a second, smaller bug my own test caught: the
correction counter only counted the running-max step, so a genuinely corrected
cell reported `0 corrections`. Now counts cells changed by either step.

**B2 — inflow hydrograph volume wrong at both ends.** Two causes, both fixed:
- short storms: the series ended on the last whole hour before the base time
  still carrying ~0.4x peak, and LISFLOOD interpolated from there to the
  terminator. Now emits an explicit zero AT the base time.
- long storms: base time (2.67 x duration = 64.1 h at 24 h) exceeded a fixed
  12 h drain-down, truncating the recession limb. `run_scenario` now detects
  crossings BEFORE sizing the window (`detect_fluvial` split out from
  `build_fluvial_forcing`) and extends the drain-down to fit, reporting it.
Verified end-to-end on the previously-broken 24 h case: window auto-extended
12h -> 41h, `.mass` peak Qin 366.8 = injected 366.8 m3/s, Qout 354.6,
Verror 8.9e-08 not growing, `window_truncated=False`.

### High severity
- **B3** `clip_dem` now intersects the window with the raster before reading, so
  an AOI overlapping the DEM edge can no longer produce a correctly-sized array
  paired with an off-raster transform (which silently mis-georeferenced every
  downstream layer). The previously-dead overlap check now actually fires.
- **B4** through-flowing rivers are no longer dropped. Both-endpoints-outside is
  only a corner clip when there is a single boundary crossing; with two or more
  the reach passes through and now yields both an inflow and an outflow.
- **B5** SGC widths rasterise narrowest-first so the widest reach wins a shared
  cell — a 5 m tributary could previously overwrite a 400 m trunk and, since
  depth derives from width, crush its depth too.
- **B6** job record is written as `running` BEFORE the completion callback is
  registered (the old order could strand a fast/failing job in `running`
  forever), and job records + `summary.json` are now written atomically
  (tmp + `os.replace`) so concurrent identical requests cannot expose a
  half-written file.
- **B7** the AOI guardrail measures the MODEL DOMAIN (bounding rectangle), not
  the polygon, and rejects empty / zero-area / self-intersecting geometry.
  Verified: a thin diagonal sliver is now refused at 94,071 km2 (it would
  previously have passed as a small polygon and likely OOM'd); a bow-tie is
  refused; a normal AOI still runs.
- **B8** rainfall resampling raises if the raster covers <50% of the AOI instead
  of silently filling zeros and producing a plausible-looking no-rain flood.

### Medium
- `/version` endpoint reports `model_version` + binary mtime — this is exactly
  what would have caught the stale-server incident in one request.
- `LISFLOOD_TIMEOUT_S` / `DEFAULT_RECESSION_HR` were defined in the backend and
  never read while the pipeline used different hard-coded values; the pipeline
  is now the single source of truth and the backend imports it. The
  `recession_hr=48` vs schema `12` contradiction is gone.
- exit code 0 is no longer taken as proof the outputs exist — a readable error
  replaces a bare FileNotFoundError from inside the worker.
- `verror_growing` scales with domain volume instead of an absolute `>1.0`
  that could never fire on our domains.
- interpolated depth arrays are float32 (were float64), roughly halving the
  peak memory of the alternating-block step.

### Efficiency / operability
- Hourly snapshots (`.wd`/`.Vx`/`.Vy`/`.elev`, ~14 MB/run, read by nothing) are
  now OFF unless a caller passes `saveint_s` — they remain available as the raw
  material for a future animation. Run dirs drop ~19 MB -> ~5 MB.
- Run-dir retention: keeps the 20 most recent, prunes the rest after each job.
  Growth was unbounded on a root filesystem already 92% full and shared.

### Tests — the actual gap
`tests/test_forcing.py`: **18 unit tests, all passing**, each tied to a specific
defect that shipped (mass conservation across all 5 durations, the
non-monotonic DDF case, hydrograph volume at every duration, strictly
increasing `.bdy` times, the m2/s units trap reproducing the plan's own
1463/29.16 = 50.17 reference, widest-channel-wins). They run in 0.3 s and need
no services. The Puppeteer e2e test has been moved out of `/tmp` into
`tests/e2e/` with a README, so it survives a reboot.

Full stack re-verified in a real browser after all changes.

### Still open (unchanged)
Calibration; the upstream DDF cross-duration monotonicity defect in the rainfall
pipeline; rain NetCDF stores 678k values for a field varying 5.4% (coarse rain
grid not yet implemented); no requirements/README at project root; no provenance
sidecars or structured logging; backend still an unsupervised nohup process;
GPU still lacks SGC and hazard.

---

## Phase 13 — Soil-texture infiltration (replaces the land-cover guess)

User pointed at `/home/mudassir/DGX-Shared/Soil Data/`. It holds SoilGrids
sand/silt/clay at 0-5, 5-15, 15-30 cm, already processed into USDA texture
classes by an existing script chain (`0. Soil Scripts/`).

### What changed
`pipeline/soil.py` (new). Infiltration is now:

    rate = Ksat(USDA texture)  x  land-cover modifier  x  antecedent-moisture factor

Ksat values are Rawls, Brakensiek & Miller (1983) Green-Ampt saturated
conductivities - the standard table behind SCS/HEC-HMS. This corrects a
conceptual error: infiltration capacity is primarily a SOIL property that land
cover modulates, but we had been deriving it from land cover alone, so sandy
desert and clay floodplain got identical rates whenever they shared a
land-cover class. Manning's n still comes from land cover, which is correct -
roughness genuinely is a surface-cover property. `MODEL_VERSION -> 8`.

Used `2. RawSoilTexture/Raw_soil_texture.tif` (~250 m, EPSG:4326, Pakistan
extent) rather than `5. Focal Majority` (~10 m): 10 m is far finer than the
29 m model grid, the file is 253k x 258k, and a sample of it contained values
21 and 23, outside the valid 1-12 USDA range - a likely defect in that
derivation, worth checking upstream.

### Effect
| AOI | old land-cover guess | new soil-based |
|---|---|---|
| Charsadda (river plain) | 4.22 mm/hr | 2.38 mm/hr |
| Thar (desert) | 7.70 mm/hr | 0.94 mm/hr |

### DATA-QUALITY WARNING - the soil product looks wrong in arid regions
National composition: Loam 39%, Clay Loam 29%, Sandy Clay Loam 16%,
Sandy Loam 7%, no-data 6.5% ... and **Sand + Loamy Sand together are 0.0%**.
In a country containing the Thar and Cholistan deserts, that cannot be right.
Checked the raw inputs directly at a Thar point: SoilGrids itself reports
sand 29% / silt 37% / clay 33%, which classifies as Clay Loam correctly - so
the CLASSIFICATION is faithful and the problem is in **SoilGrids**, a global ML
product with little training data in arid Pakistan.

Consequence, stated plainly: in sandy areas the new soil-based rate
(~1 mm/hr) may be worse than the old land-cover guess (~8 mm/hr), because true
desert sand is ~118 mm/hr. Under-estimating infiltration OVER-predicts
flooding. The change is a clear improvement on the Indus alluvial plain
(loam/clay loam, plausible) and a possible regression in the deserts.
Texture composition and the unclassified fraction are now returned with every
result so this is visible per run rather than hidden.

Also note SoilGrids has real gaps: 6.5% no-data nationally, but 29% over the
Charsadda test AOI, which falls back to Loam.

Next step for infiltration is not more data but a better MODEL: this is still a
constant loss rate with no soil-moisture store and no capacity decay, so it
absorbs at full rate for the whole storm.

### OSM - identified, not yet built
No OSM data on disk; Geofabrik and Overpass are both reachable. OSM maps
directly onto the largest remaining physics gaps:
| gap | OSM tags |
|---|---|
| levees/bunds (biggest structural gap) | `man_made=dyke`, `embankment=yes` |
| barrages, dams, weirs | `waterway=dam|weir`, `landuse=reservoir` |
| bridges / culverts (constrictions) | `bridge=yes`, `tunnel=culvert` |
| irrigation network (Indus Basin canals) | `waterway=canal|drain` |
| road/rail embankments acting as barriers | `highway`/`railway` + `embankment=yes` |
| exposure - the dead `statExposed` stat | `building=*` |
LISFLOOD already supports `SGClevee` and `weirfile`; both are unused.

---

## Phase 14 — Literature/source review of SGC, and a WET BIAS finding

Searched the literature and public repos for how sub-grid channel geometry is
handled elsewhere. Two findings change how our results should be read.

### *** ISSUE: our flood extents are probably BIASED HIGH ***
Two independent lines now point the same way:

1. **Channel bathymetry method.** Neal et al. 2021, *Estimating River Channel
   Bathymetry in Large Scale Flood Inundation Models* (WRR,
   10.1029/2020WR028301) shows bathymetry derived from uniform-flow /
   downstream hydraulic geometry - i.e. exactly our `d = r*w^p` - is valid only
   for kinematic water-surface profiles and carries a **wet bias**. Replacing it
   with gradually-varied-flow theory cut error vs a target profile by 66%,
   removed backwater bias, and for a Mozambique case **reduced flood extent by
   40%** and floodplain storage by 79% at the 5-year return period.
2. **Soil data** (Phase 13): SoilGrids reports ~0% Sand/Loamy Sand nationally,
   so arid AOIs get infiltration under-estimated, which also over-predicts
   flooding.

=> Until calibration, present extents as CONSERVATIVE, not accurate. Worth
saying so in the UI next to the existing screening caveat.

### How others parameterise SGC (and where we sit)
`LFPtools` (github.com/jsosa/LFPtools, from the Bristol LISFLOOD-FP group)
implements exactly three depth methods: (1) from a measured depth raster,
(2) hydraulic geometry `d = r*w^p`, (3) simplified Manning. Widths come from
**measured satellite datasets (GRWL, GWD-LR)**, not a discharge regression.
Also `lifpy` (github.com/windsor718/lifpy) for large-scale subgrid setups.
So our method is the standard middle option, but we regress width where others
measure it. Both target older LISFLOOD-FP versions - mine for method, do not
drop in.

### RESOLVED: is theta=0.9 needed? YES - do not set 1.0
Previously flagged as unexamined artificial diffusion. Shen et al. 2023 (GMD
16, 3291) shows the ORIGINAL SGC scheme - the one in our 8.1 build - is
numerically unstable below Manning's n ~ 0.03, and their fix is an *adaptive*
theta constrained to **0.7-1.0**, explicitly replacing fixed weighting factors.
Our fixed 0.9 sits inside that band and is guarding a documented instability.
**Related risk:** our land-cover table sets Bare ground n = 0.025, BELOW that
threshold, and bare ground is widespread in Pakistan - watch for divergence on
bare-ground-dominated AOIs.

### GRWL - the actionable width fix
Zenodo record 1297434, CC-BY 4.0:
  - `GRWL_summaryStats_V01.01.zip` 43.2 MB - per-segment width stats (USE THIS)
  - `GRWL_mask_V01.01.zip` 393.8 MB - 830 GeoTIFF water masks, 30 m
  - `GRWL_vector_V01.01.zip` 3.1 GB - full per-point measurements
Attributes include width min/median/mean/max/std, braiding index, segment id,
and a river/lake/tidal/**canal** flag - the canal flag would also give us the
Indus Basin Irrigation System, currently entirely absent from the model.
Limit: GRWL's floor is 30 m, and **65% of our reaches are narrower than 29 m**,
so it is a hybrid (measured big rivers + regression for small ones), not a
replacement. Still width only - depth stays the binding uncertainty.
Algorithm source (if we ever need custom widths): RivWidthCloud,
github.com/seanyx/RivWidthCloudPaper (Google Earth Engine, JS + Python).

### Sentinel: tested the cheap shortcut, it FAILED
Idea: use the 10 m ESRI land-cover Water class already on disk as a water mask.
Measured over the Charsadda AOI: only **55 water pixels in the whole AOI
(0.03%)**, giving widths of ~1 m against a 72 m regression estimate. Cause: ESRI
land cover is an annual composite classified by dominant class, so a
monsoon-driven river reads as bare ground/rangeland most of the year. It is a
land-cover product, not a water-extent product. **Do not use it for widths.**

Proper Sentinel route (not yet built) - `ee` and `geemap` ARE installed in
geo_env, so no bulk download needed:
  - Sentinel-1 SAR (10 m) is the right sensor - C-band penetrates monsoon cloud.
  - The hard part is not resolution but WHICH FLOW STATE the image captures.
    GRWL composites at *mean* discharge; SGC wants *bankfull*.
  - **We can solve that with data we already hold**: the retrospective NetCDF is
    (31412 days x 55334 reaches) of daily Q covering the whole Sentinel era, and
    rp_2 is already computed per reach. So per reach: find dates when daily Q
    was near rp_2, pull only those scenes, measure width there -> *measured
    bankfull width per reach*. Better suited to SGC than GRWL's mean-discharge
    widths.
  - Caveats: practical floor ~20-30 m (a 15 m river is 1-2 mixed pixels, not
    reliable); the Indus is braided so "width" is ambiguous (GRWL carries a
    braiding index for this reason).

### GEOGLOWS resolution limits (measured, Kabul region, n=1791)
Hydrography is TDX-Hydro (TanDEM-X 12 m derived), but the meaningful unit is the
reach: **median length 2.7 km**, median upstream area **86 km2**, minimum ~10 km2
(only 3% below that). Temporal: **daily**. Units confirmed from the file itself:
`units = "m3 s-1"`, `aggregation_method = "mean"`.
Two consequences: (a) a finer DEM cannot add fluvial forcing detail - ~3 km is
the floor; (b) annual maxima of DAILY MEAN flows under-state instantaneous
peaks, biasing fluvial forcing LOW in small flashy catchments (median catchment
here is only 86 km2, so this is likely material).

### Does the 12.5 m DEM fix SGC? Partly, but not the core problem
Sub-grid fraction by width falls 65% -> 33% of reaches. But resolution gives
channel *planform*, NOT *bathymetry*: SRTM and ALOS are radar SURFACE models
recording a flat water ribbon, not the bed. SGC is still required for depth
everywhere. Adopt 12.5 m for floodplain topography and possibly resolving bunds
(the #1 structural gap) - not as a channel fix. **Verify provenance first**:
many distributed "12.5 m ALOS PALSAR" DEMs are resampled from ~30 m sources.

### Known issue not yet fixed: diagonal channel double-counting
`all_touched=True` puts **2 cells per cross-section** on diagonal reaches, each
carrying the FULL channel width - roughly 2x the intended conveyance/volume.
`all_touched=False` gives the correct 1 cell but leaves the path only
8-connected, while SGC's own chan_length accounting uses 4-connectivity. Needs a
thinning step that preserves 4-connectivity; not a one-flag fix.

### Agreed near-term plan
1. GRWL clip (43 MB, ~1 h) - measured widths for >30 m rivers.
2. Keyword-level physics: enable `dist_routing`, set `diffusive_froude_thresh`,
   keep `theta = 0.9`.
3. Channel pre-fill via `startfile`/`ch_start_h` - removes the dry-start bias.
Deferred: Sentinel-1 bankfull widths (after GRWL, using GRWL as the check);
OSM levees (survey rural-Pakistan coverage first).

---

## Phase 15 - AOI upload UX: fly-to-zoom and correct area (dashboard)

**Request:** "When I click the file, the map should add and fly (zoom) to the
specific area."

### What was wrong
`setAoiLayer` ended with:
```js
try { map.fitBounds(layer.getBounds(), { padding:[40,40] }); } catch(err){}
```
Two defects in one line:
1. `fitBounds` is an **instant jump**, not an animated fly - the map teleported,
   so on a large basin it was not obvious the view had moved or to where.
2. The bare `catch(err){}` **silently swallowed** every failure. An AOI that
   produced no bounds just quietly didn't zoom, leaving nothing to debug.

A third, separate defect was found while reading the surrounding code:
`estimateAreaKm2` read `layer.getLatLngs()[0]` - **the first ring of the first
polygon only**. HydroSHEDS basins are frequently MultiPolygon (coastal/delta
ones especially), so the reported area under-counted every extra part and
ignored holes entirely. The number shown next to the filename was wrong.

### Fixes (`WebGIS Dashboard/index.html`)
- New `zoomToLayer(layer)` helper: `map.flyToBounds(b, {padding:[60,60],
  maxZoom:14, duration:1.2})`. Bounds failures are now `console.error`-ed, not
  swallowed. `maxZoom:14` stops a tiny AOI flying to street level.
- `setAoiLayer` (draw *and* upload paths) and the "Zoom to AOI" button both go
  through it, so the animation is consistent everywhere. The no-AOI button case
  uses `map.flyTo(PAKISTAN_CENTER, 5)` instead of `setView` for the same reason.
- `estimateAreaKm2` now walks the **full** geometry via `layer.toGeoJSON()`:
  Feature / FeatureCollection / GeometryCollection / Polygon / MultiPolygon, all
  rings, outer positive and holes subtracted (`geoJsonAreaM2` ->
  `polygonRingsAreaM2` -> `ringAreaM2`, spherical-excess on R=6378137).
- `addGeoJsonAsAoi` now rejects a file that parses but yields zero drawable
  layers. Previously `parts[0]` was `undefined` and Leaflet blew up inside
  `addLayer` with an unhelpful message.

### Verification (headless Chromium against the live stack, two real basins)
Test hooks `L.map` at assignment to reach the map instance (the dashboard script
is an IIFE, so `map` is not global), then reads zoom/centre before, **mid-flight**
and after, and compares the settled centre against bounds computed independently
from the raw GeoJSON coordinates.

| basin | geometry | zoom | centre offset | area shown | HydroSHEDS `SUB_AREA` |
|---|---|---|---|---|---|
| 4080033380 | MultiPolygon | 5 -> 11 | 0.0001 lat, 0.0000 lng | 711.8 km2 | 710.2 km2 |
| 4080666340 | Polygon | 5 -> 11 | 0.0001 lat, 0.0000 lng | 629.7 km2 | 628.2 km2 |

Mid-flight zoom read **7.87** (a fractional, non-final value) on both - direct
evidence the view is animating rather than jumping. Areas agree with the
dataset's own published `SUB_AREA` to **0.22%** and **0.24%**; the consistent
sign is expected, HydroSHEDS computes area on a different projection. The
MultiPolygon case is exactly the one the old first-ring-only code got wrong.

### Phase 15b - the real cause of "nothing happened", plus a visible activity log

User reported the fly-to did nothing **and nothing appeared in the console**.
Investigated before assuming cache: the served copy at :8899 and both on-disk
paths (`/mnt/external-ssd/...` and the `/home/mudassir/DGX-Shared` FUSE view of
the same file) all contained the change, and only one `index.html` exists in the
project. So the code was live - something upstream of it never ran.

**Root cause: `<input type="file">` only fires `change` when its VALUE changes.**
`fileInput.value` was reset in exactly one place - the "clear AOI" button. So
picking the same basin file a second time (the obvious thing to do when
re-testing one file) left the value unchanged, `change` never fired, `handleFile`
was never called, and the app did *literally nothing* - no outline, no zoom, and
no console output, because no code ran at all. That matches the report exactly,
including the silent console. Fix: `e.target.value = ''` at the end of the change
handler, so the same file always re-fires.

**Visible activity log** (user's request - "add task bar to show task done").
A monospace panel under the AOI status reports each step with a timestamp:
`Reading <file> (N KB)` -> `Parsed N geometries` -> `AOI set - ... km2` (green)
-> `Flying to lat, lng` -> `Map centred - zoom N` (green), with failures in red.
Every former `alert()` in the upload path now writes here instead - the modal
alerts interrupted the flow and vanished, leaving no trace of what failed.
`zoomToLayer` logs both its failure branches and confirms arrival via a
one-shot `moveend` handler, so "did the map actually move" is answerable from
the UI alone.

**Build stamp in the header** (`ui 2026-09-05c`, `UI_BUILD` constant). A stale
cached page is now visible at a glance rather than looking like a code change
that did nothing - the same role `/version` plays for the backend, after a stale
server served v7 while the code was v8 back in Phase 11.

### Verification
Headless Chromium, real basin, both picks of the SAME file:
```
build stamp: ui 2026-09-05c
  info | Dashboard ready - UI build 2026-09-05c
  info | Reading hybas_pak_lev08_4080033380.geojson (11 KB)...
  info | Parsed 1 geometry
  ok   | AOI set - hybas_pak_lev08_4080033380.geojson - ~711.8 km2
  info | Flying to 23.872, 68.654...
  ok   | Map centred - zoom 11.0
fileInput.value after handling: "" (cleared - same file can be re-picked)
```
Caveat on the test: it must set `input.files` and dispatch `change` by hand, so
it *cannot* reproduce Chrome's native suppression. It therefore asserts on the
state Chrome actually inspects - `fileInput.value` must be `''` after handling -
rather than on the manually-fired event, which would pass either way.

### Phase 15c - "0 KB / NotFoundError" on upload: the FUSE + document-portal trap

The activity log added in 15b immediately earned its place - it turned a silent
failure into a precise one:
```
Reading hybas_pak_lev08_4080571660.geojson (0 KB)...
Could not read hybas_pak_lev08_4080571660.geojson: NotFoundError
```
`file.size === 0` means the browser could not stat the file at *selection* time,
before any of our code ran.

**Hypotheses tested and discarded** (all falsified by direct experiment, not
reasoning):
1. *Snap confinement blocks /mnt.* Both browsers are snap packages and the data
   is outside `$HOME` (`~/DGX-Shared` -> `/srv/dgx-share` -> `/mnt/external-ssd`).
   Disproved: `snap run --shell chromium -c "wc -c <file>"` read all 4731 bytes.
2. *The FUSE path itself is unreadable by the browser.* Disproved: puppeteer
   `uploadFile()` on the FUSE path read 4731 bytes in both `/usr/bin/chromium-
   browser` and `/snap/bin/chromium`.

**Actual cause: the GUI file chooser, the one path no test exercised.** Snap
browsers route the chooser through **xdg-document-portal** (confirmed live at
`/run/user/1006/doc`). That portal is itself a FUSE filesystem and cannot export
a file that already lives on another FUSE mount - and the project data is on
`fuseblk` (ntfs-3g) at `/mnt/external-ssd`. The browser gets a portal handle that
stats as 0 bytes and fails to read. Every test passed precisely because
`uploadFile()` injects the path straight into the renderer and **bypasses the
portal**; the shell reads bypass it too. Nothing on the server side is broken.

**Resolution** (user's choice: keep upload, copy files locally). All 1131 basins
copied to `/home/mudassir/reselli_basins/` - **ext4 on nvme**, not FUSE, so the
document portal can export them. 12 MB. Upload from there.

**Also fixed:** a zero-byte pick is now caught *before* the read starts, with a
message naming the cause instead of surfacing a bare `NotFoundError`; the size
readout is `.toFixed(1)` KB so a small file no longer rounds to a confusing
"0 KB". UI build `2026-09-05d`.

**Standing lesson:** headless upload tests cannot cover the file chooser. Any
future "upload does nothing" report should be reproduced through the real GUI
dialog before trusting a green puppeteer run.

### Verification (from the new ext4 location)
```
build stamp: ui 2026-09-05d
  info | Reading hybas_pak_lev08_4080571660.geojson (4.6 KB)...
  info | Parsed 1 geometry
  ok   | AOI set - hybas_pak_lev08_4080571660.geojson - ~326.1 km2
  info | Flying to 36.440, 71.950...
  ok   | Map centred - zoom 12.0
```

### Phase 15d - FUSE theory falsified; basin picker removes the file dialog

The 15c fix did NOT work: the user reported the same 0-byte read after picking
from `/home/mudassir/reselli_basins/` (ext4, mode 644, their own uid). An ext4
file in the user's own home cannot be explained by the document-portal/FUSE
story, so **the 15c root cause is wrong and is retracted**. The portal *is*
active and the data *is* on fuseblk, but that is not what breaks the read.

Cause now unknown. Two things were done rather than guessing a third time:

**1. Diagnostics in the activity log.** A 0-byte pick now prints `file.name`,
`file.type`, `file.lastModified` and `navigator.userAgent`. `lastModified` is
the useful one: the FUSE originals are stamped 12:15 and the ext4 copies 12:49,
so the mtime identifies *which copy was actually picked* - the only thing the
browser exposes that pins down the source folder. The user agent settles the
browser/host question independently of recollection.

**2. Basin picker - the fix that does not depend on the diagnosis.**
`basin_index.json` (1131 basins: HYBAS_ID, SUB_AREA, bbox; 147 KB, generated,
sorted largest first) plus a `basins` symlink to the basin folder, both served
by the existing static server. The sidebar has a searchable list; clicking a row
`fetch`es the GeoJSON over HTTP and hands it to the existing `addGeoJsonAsAoi`,
so it reuses the same add + fly-to + area path as upload. **No File object, no
OS file dialog, no filesystem access from the browser** - so whatever breaks the
picker cannot break this, wherever the browser runs. Upload is untouched and
still available for custom AOIs.

Note this is the "searchable basin list" option the user had earlier declined in
favour of "keep upload only" - offered again and built because the premise for
declining it (that copying files to local disk would fix upload) turned out to
be false.

### Verification
```
build: ui 2026-09-05e
catalogue: 1131 basins - showing first 200, refine the search - sorted largest first
search "4080571660" -> 1 hit
  info | Basin catalogue loaded - 1131 basins
  info | Fetching basin 4080571660 (325.4 km2)...
  info | Parsed 1 geometry
  ok   | AOI set - Basin 4080571660 - ~326.1 km2
  info | Flying to 36.440, 71.950...
  ok   | Map centred - zoom 12.0
view: zoom 5 -> 12, centred 36.4397, 71.9497 ; errors: none
```
Catalogue `SUB_AREA` 325.4 km2 vs the dashboard's own geodesic 326.1 km2 (0.2%),
consistent with the two basins checked in Phase 15.

**Still open:** why a browser on this machine reads a local ext4 file as 0 bytes.
The diagnostics above are there to answer it on the next attempt.

### Phase 15e - upload 0-byte read traced to the xdg document portal

The diagnostics from 15d answered it. User's log:
```
Reading hybas_pak_lev08_4080571660.geojson (0.0 KB)...
diag: name=hybas_... type=application/geo+json mtime=2026-09-05 07:55:01
diag: Mozilla/5.0 (X11; Ubuntu; Linux x86_64) ... Chrome/151.0.0.0 Safari/537.36
```
Two facts settle it:
- **`mtime` is 07:55:01 UTC; the log line is 12:55:01 local, and the machine is
  UTC+5. The file's mtime is the exact second of the click.** The file handed to
  the browser did not exist before the pick - it was created empty at selection
  time. No basin file on disk has that mtime (originals 12:15, ext4 copies 12:49).
- The user agent `Chrome/151.0.0.0` matches snap `chromium 151.0.7922.108`
  exactly, confirming a snap browser on this machine (not a remote one).

Snap Chromium routes its file chooser through **xdg-desktop-portal**, which
creates an on-demand entry under the `fuse.portal` mount at `/run/user/1006/doc`.
Checked one minute after the failed pick, three of those entries returned
**"Operation not supported"** on listing; they were gone on a later check,
consistent with short-lived entries created per pick. The portal produced an
empty document, the browser stat'd it as 0 bytes, and `FileReader` then failed.

**So the 15c mechanism (document portal) was right and its trigger was wrong.**
It is not that the *source* is on FUSE - it fails for ext4 files in the user's
own home too. Copying the basins to `~/reselli_basins/` was therefore useless
for this problem; the portal is broken for this session regardless of source.
Why the portal returns "Operation not supported" is still not established.

**Status: the basin picker (15d) is the working path and the user confirmed it
works. Upload through the click-to-choose dialog remains broken** - it is a
desktop-session fault, not dashboard code, and nothing in this repo can fix it.

Two workarounds, neither yet verified (XDND cannot be driven headlessly):
1. **Drag & drop** the file onto the upload box. Drag passes a real path via
   XDND and never touches the portal; the drop handler already exists. Now
   suggested in the 0-byte error message itself.
2. `systemctl --user restart xdg-document-portal` to clear the portal's state.
   Not run - it affects the user's live desktop session, so it is theirs to run.

UI build `2026-09-05f`.

### Phase 15f - document portal restarted (user-authorised)

Second failure reproduced the signature exactly: `mtime=2026-09-05 07:58:48` UTC
against a click at `12:58:48` local (UTC+5) - again the file's mtime is the
precise second of the pick. Two independent occurrences, so the "empty file
created at selection time" mechanism is established, not inferred.

`xdg-document-portal` had been running since **2026-08-27 09:22 - nine days** -
and its FUSE mount had shown "Operation not supported" on entries created around
the failed pick. Its journal was empty (nothing logged either way).

Restarted with the user's authorisation:
```
before: MainPID=702604   ActiveEnterTimestamp=Thu 2026-08-27 09:22:03 PKT
after:  MainPID=2393911  ActiveEnterTimestamp=Sat 2026-09-05 13:00:51 PKT
mount:  portal on /run/user/1006/doc type fuse.portal (rw,nosuid,nodev,...)
doc entries: readable=88  errors=0
```
**Caveat carried to the user:** a snap application joins its mount namespace at
launch, so a running Chromium still references the old, now-dead portal mount.
Chromium must be fully quit and relaunched before this can be tested.

Unverifiable from here either way - the GTK file chooser cannot be driven
headlessly, which is the same blind spot that produced the wrong 15c conclusion.
Reported to the user as "restarted, now test it", not as "fixed".

### Confirmed working meanwhile (user's own log, build 2026-09-05f)
```
Basin catalogue loaded - 1131 basins
Fetching basin 4080707010 (6202.2 km2)...
AOI set - Basin 4080707010 - ~6216.1 km2
Flying to 32.348, 72.806...
Map centred - zoom 9.0
```
Catalogue SUB_AREA 6202.2 km2 vs the dashboard's geodesic 6216.1 km2 (0.22%) -
the same consistent offset seen on the three basins checked earlier, at a basin
20x larger, so the area calculation holds across scales.

## Phase 16 - All-basins map layer with click-to-select

**Request:** "Group all geojson as layer toggle on and off. When I click any
basin it should be selected as final basin to be worked on."

(Context: the portal restart of 15f did NOT fix click-to-upload. Upload is
abandoned as a working path; every AOI route now goes over HTTP.)

### Data
`basins_all.geojson` - all 1131 basins in ONE FeatureCollection, properties
`{id, area_km2, file}`. Geometries simplified with shapely `simplify(0.002,
preserve_topology=True)` - roughly 200 m at this latitude. 14 MB of source
GeoJSON becomes **4.6 MB**, 42% less geometry.

**The simplification is display-only and must stay that way.** Clicking a basin
`fetch`es the untouched full-resolution file from `basins/<file>` and *that* is
what becomes the AOI, so the model never runs on a simplified boundary. The
generator falls back to the original geometry if a simplification comes back
empty or invalid, so no basin can be shipped broken.

### UI
- Checkbox **"Show all basins on map"**. The 4.6 MB layer is fetched lazily on
  first enable, not at page load; toggling off calls `removeLayer`, toggling
  back on reuses the parsed layer rather than re-fetching.
- Rendered with `L.canvas()`. 1131 SVG paths makes panning crawl; canvas does
  not. `bringToBack()` on add so the layer never covers the selected AOI.
- Hover highlights and a sticky tooltip (`Basin <id> - <area> km2`).
- Click selects: highlight turns teal, the full-resolution geometry is fetched
  and set as the AOI, and the map flies to it - reusing the exact
  `addGeoJsonAsAoi` -> `setAoiLayer` -> `zoomToLayer` path the list already uses,
  so there is one definition of "the AOI" rather than two.
- Map click and list click stay in sync in both directions (`highlightOnMap`
  from the list, list-row toggling from the map), so the two controls can never
  show different selected basins.

### Verification (headless, real layer)
```
build: ui 2026-09-05g
layer loaded in 0.2s ; status: 1131 basins shown - click one to select it
basin features on map: 1131
clicked basin ON THE MAP: {"id":4080033770,"area_km2":2270}
AOI now: Basin 4080033770 - ~2275.1 km2
list selection mirrors map: ['4080033770']
basin features after toggle-off: 0
errors: none  -> TEST PASSED
```
Toggle-off leaves zero basin features on the map (no leak). Catalogue SUB_AREA
2270 km2 vs the dashboard's geodesic 2275.1 km2 - 0.22%, the same offset seen on
every basin checked so far (326 / 630 / 712 / 6216 / 2275 km2), now across a 19x
range of sizes.

### Upload: closed as unfixable from this repo
Click-to-upload stays broken after the portal restart. The file chooser hands
the browser a 0-byte file created at the instant of the click; JavaScript sees
`size === 0` before any dashboard code runs and browsers never expose the real
path, so no page-side fix exists. Every AOI route that matters now avoids it:
basin list, map layer, and hand-drawn polygons all work.

## Phase 17 - Live run progress + durable run log

**Request:** "Develop a mechanism for recording simulation run, show live
processing like what is being done. Like which data is being fetched, what
process is being done etc."

### Live progress
`run_scenario` takes an optional `progress(stage, message, pct)` callback,
defaulting to a no-op so the CLI and the 18 unit tests need no plumbing. Stages
report what is actually happening, with real numbers rather than a spinner:

| pct | stage | example message |
|---|---|---|
| 0-1 | start | `AOI parsed - 1 geometry(ies), 50-yr / 3-h storm, normal soil` |
| 2-8 | dem | `DEM ready - 810x949 cells at 29.2 m` |
| 10-18 | landcover | `Reading ESRI land cover + soil texture -> Manning's n and infiltration` |
| 20-32 | rainfall | `Design storm ready - 84 mm mean, peak 30 mm/h at hour 2` |
| 34-38 | fluvial | `6 river inflow(s) detected - combined pluvial + fluvial` |
| 40-50 | inputs | `7647 sub-grid channel cells, peak inflow 79 m3/s` |
| 52-92 | solver | `Solving - simulated 9.5 h of 15.0 h` |
| 94-100 | post/render | `Complete - 12.3 km2 inundated, max depth 1.84 m` |

**Solver progress comes from the `.mass` file the solver already writes** (a row
per `massint` of simulated time), not from parsing stdout and not from any new
work inside the timestep loop. `_mass_progress_time_s` skips a torn final line
rather than mis-parsing it, and events are emitted only per 1% moved, so a long
run does not produce one event every poll.

**`run_lisflood` now uses `Popen` with stdout/stderr redirected to FILES.** The
old `subprocess.run(capture_output=True)` could not be polled; switching to
pipes without draining them would deadlock the solver once it printed ~64 KB.
The full logs are now kept on disk, which also beats the old 3000-char tails.

### IPC
The job runs in a separate process, so progress goes through a file:
`runs/<job_id>/progress.jsonl`, **append-only** - a reader polling mid-write
sees at worst a torn last line (skipped) rather than a truncated document, which
a whole-file rewrite would allow. Every write is exception-guarded: progress
reporting must never be able to fail a run.

`GET /scenarios/{id}/progress` tails it. A cache hit has no progress file, so it
returns one explicit "served from results cache" event instead of an empty list
that would read as "no progress yet" and leave a client polling forever.

### Durable run log
`GET /runs` reads `run_history.jsonl`, appended on every completion INCLUDING
failures (the append sits outside the try, so a crashed run is still recorded).
Written for cache hits too, tagged `cached`. It survives both prunings - job
records and run dirs are both cleaned up - so it carries its own copy of the
inputs rather than pointing at state that may be gone.

New `aoi_label` request field carries display text ("Basin 4080033380 (711.8
km2)"). **It is excluded from the cache key** via a new
`results_cache.NON_PHYSICAL_FIELDS`, or relabelling the same AOI would force a
full re-run for a cosmetic difference. That set is deliberately tiny: the
whole-request key exists because hand-listing once omitted `soil_moisture` and
made a physical control look broken.

### UI
Progress bar under the Run button (stage text + %), and every stage event is
echoed into the existing Activity panel, so a run's history sits with everything
else the app does rather than in a separate widget that vanishes. New "4. Run
Log" section lists past runs newest-first with inputs, flooded area, max depth
and solver seconds; failures in red. Remaining `alert()`s on the run path
replaced with activity-log lines. UI build `2026-09-05h`.

### Better failure reporting (found while testing)
A crashed run reported `lisflood exited -11:` followed by the .par file read
back at us - useless. Two causes, both fixed:
- `-log` makes LISFLOOD redirect its real output to `results/<resroot>.log`, so
  stdout held only the parameter echo. That log's tail is now captured as
  `solver_log_tail` and is what gets reported.
- A negative return code is death by SIGNAL, not an exit status. The message now
  says `killed by signal 11`, and states how far the run got
  (`after reaching 9.5 h of simulated time` / `before completing its first
  output interval`) from the same `.mass` reading that drives progress.

### OPEN BUG: solver segfaults on basin 4080571660
That basin (high-altitude glacier catchment, 810x949 cells, 6 inflows, 7647 SGC
cells) **segfaults at the first timestep** - `.mass` empty, crash right after
`NUMA:` in the solver log. **Not caused by the Phase 17 changes**: re-running
the identical inputs through the exact pre-change invocation
(`subprocess.run(capture_output=True)`, no polling) segfaults identically
(-11, 0.6 s). Pre-existing, cause not yet investigated. Not every AOI is
affected - basin 4080033380 runs fine.

## Phase 18 - Performance investigation (measured, not assumed)

Question: how to expedite runs. Baseline: basin 4080033380, 852x1558 grid,
15 h simulated, 8 threads -> 478 s (442 s solver loop, 37 s output writing).

### Runtime is linear in simulated time
| window | wall | s per simulated hour |
|---|---|---|
| 6 h | 127 s | 21.2 |
| 9 h | 187 s | 20.8 |
| 15 h | 315 s | 21.0 |

So cost is predictable: `seconds ~= 21 x sim_hours` for a grid this size.

### REFUTED: shortening the drain-down window
Hypothesis was that the 12 h drain-down was wasted, since `.mass` showed stored
volume flat at 100% of peak and **Qout = 0.00 m3/s for the whole run**. Tested
against the 15 h reference:

| window | flooded area | vs 15 h |
|---|---|---|
| 15 h | 158.5 km2 | reference |
| 9 h | 129.4 km2 | -29 km2, 5.3% of cells differ >1 cm |
| 6 h | 78.4 km2 | **-80 km2 - half the flood missing** |

**The hypothesis was wrong.** Flat volume with zero outflow does not mean
nothing is happening: the water is *redistributing*, sheet flow concentrating
across a flat delta, so cells keep crossing the 0.1 m threshold hours after the
rain stops. The `.mass` *instantaneous* wet area was falling (236 -> 190 km2)
while the *maximum* map kept growing - two different quantities, conflated.
Implication runs the other way: extent was still growing at 15 h, so the fixed
12 h drain-down may be too SHORT for flat basins.

### Threads: not a lever (all on the 6 h config)
| threads | wall |
|---|---|
| 4 | 198 s |
| 8 | 127 s |
| 16 | 119 s |
| 20 | 136 s |

4->8 gives 1.56x; 8->16 gives 1.07x; 20 is *worse* (oversubscription on a
shared machine, and GB10 is 10 fast X925 + 10 slower A725 cores). The solver is
memory-bandwidth bound. **The existing `OMP_THREADS_PER_JOB = 8` is already
close to optimal** - leave it alone.

### Output writing: not a lever
37 s of 478 s. Source check: `.maxVc/.maxVcd/.maxHaz` are gated by `hazard`
(needed for the hazard stat) and `.maxtm` is written unconditionally
(lis2_output.cpp:537-563). Only a few seconds are actually removable.

### THE lever: the model runs on the BOUNDING BOX, not the basin
`clip_dem` clips to `total_bounds` and never masks to the polygon. Measured
across 10 basins spanning 42-1379 km2, **median bbox/polygon = 2.17x, i.e. ~54%
of computed cells lie outside the selected basin**. Masking them to nodata is
the one large, physics-defensible saving available - a HydroSHEDS boundary is a
drainage divide, so no-flow across it is reasonable for rain-on-grid.
Not yet implemented; two real obstacles first:
1. The FREE outlet is placed on a DOMAIN EDGE (`FREE_EDGE_HALF_SPAN_CELLS`).
   Masking moves the effective boundary to the basin outline, so outlet
   placement needs rework or the domain will trap water.
2. It is only defensible for watershed AOIs. For a user-drawn rectangle, water
   genuinely does cross the boundary, so masking would be wrong there.

## Phase 18b - TWO CORRECTNESS BUGS found during the above

### 1. AOIs extending past the DEM are silently truncated
`clip_dem` intersects the requested window with the DEM extent (deliberately,
for transform consistency) but **never reports that it did**. Basin 4080033380
spans y = 2,622,428 -> 2,657,889; the national SRTM DEM's southern edge is
y = 2,633,266. So 10.8 km of the basin is outside the DEM and **only 79% of the
basin was modelled**, with nothing in the output saying so. Affects any coastal
or border AOI.

### 2. Most of the reported flooding is OUTSIDE the selected basin
For that run the dashboard reported **158.5 km2 inundated**. Split against the
basin polygon:
```
total inundated >0.1 m : 158.5 km2
  inside the basin     :  27.8 km2  (18%)
  OUTSIDE the basin    : 130.6 km2  (82%)
```
The headline number is dominated by land the user did not select - a direct
consequence of modelling the bounding box and then reporting statistics over
the whole grid. **The fix is independent of the masking work above and is
cheaper: compute the reported statistics over the AOI polygon only.**

Caveat on generality: this is a coastal delta basin whose grid is 78.5% DEM
nodata (SRTM voids over water), so the 82% figure is likely an extreme. The
bounding-box geometry (2.17x median) says the effect is real everywhere, but
the magnitude needs confirming on an inland basin before it is quoted as
typical.

## Phase 19 - Results scoped to the AOI (was: bounding rectangle)

**User report:** "I also view the results of outside of my selected basin."
Confirmed, and it was worse than Phase 18b estimated.

### Measured on the user's own run (basin 4080662030, 50-yr / 1 h)
```
AOI polygon               1127.3 km2
modelled rectangle        2732.4 km2   (2.42x the AOI)

inundated area   shown:    609.5 km2  ->  AOI only:  247.9 km2   (59% was outside)
max depth        shown:     12.24 m   ->  AOI only:    5.80 m
```
The headline max depth was in a cell **outside the basin entirely** - the number
was not merely inflated, it described somewhere the user had not selected.

### Fix
The model must still run on the bounding rectangle (the flow field near the AOI
edge would be wrong otherwise), so the change is to REPORTING and DISPLAY:
- `rasterize_aoi()` builds the mask once in `run_scenario`, centre-based
  (`all_touched=False`): all_touched can only ever over-count, adding the
  partial boundary cells (measured 870 -> 900 cells, ~3%, on a test square).
- `inundated_area_km2`, `max_depth_m`, `max_hazard` are now AOI-scoped.
  `domain_*` fields carry the whole-rectangle values alongside, so the gap is
  visible rather than hidden; the dashboard shows them in the stat tooltips.
- The mask is saved as `scenario.aoimask.npy` and `render_depth_png` clips the
  overlay to it, so the map stops painting flood on land outside the AOI.
  `depth.asc` is deliberately still the raw unclipped grid.
- `MODEL_VERSION` 9 -> 10: reported figures change for every AOI, so v9 cached
  results must not be served.

### Also fixed: silent truncation when the AOI leaves the DEM
`aoi_polygon_coverage()` compares the AOI's true area against the grid extent
(not against the rasterised mask, which cannot see what was clipped away). Below
99.5% coverage the run now carries a model note and a progress warning. The
Phase 18b case - basin 4080033380, 21% of it beyond the SRTM southern edge -
would now say so instead of silently modelling 79% of the basin.

### Tests: `tests/test_aoi_scope.py`, 6 new (24 total, all passing)
Includes the exact failure mode: deep water placed outside the AOI must not
appear in either the reported area or the reported max depth.

Two of these tests initially FAILED on assertions I had guessed rather than
measured - an `all_touched` over-count on an edge-aligned box (adds nothing, so
the test was vacuous) and a claimed "much larger bias for thin AOIs" that
measured 1.03x, not the >1.3x asserted. Both replaced with the measured values.

## Phase 20 - One AOI path: basin layer only (upload and list removed)

**Request:** remove the upload option and the pick-basin list, keep layer
toggling, verify the basin selection, correct inputs and event handling,
prevent overlapping.

### Removed
- **Upload box, file input, drag & drop, `handleFile`, `addGeoJsonAsAoi`** and
  the two CDN libraries only that path used (`shpjs`, `@tmcw/togeojson`).
  Upload was broken by an xdg-desktop-portal fault outside this app (Phase 15e)
  and could not be fixed from here.
- **Searchable basin list** (`basinSearch`, `basinList`, `basinCount`,
  `renderBasins`, `highlightOnMap`, the `basin_index.json` fetch).
- Dead CSS for both (`.upload-box`, `.basin-list`, `.basin-item`).

`basin_index.json` is left on disk - it is what generates `basins_all.geojson`
- but nothing fetches it at runtime any more.

### Why removing the list is the "prevent overlapping" fix
Two selection paths meant two "selected" states to keep in sync (list row
highlight and map polygon highlight, mirrored in both directions) plus a fetch
race between them. One path deletes that entire class of bug rather than
managing it. Draw-polygon/rectangle is untouched - it serves custom AOIs, which
the basin layer cannot.

### Selection is now verified, not assumed
`selectBasin` fetches the full-resolution file and **checks the file's own
`HYBAS_ID` against the id of the polygon that was clicked** before setting the
AOI. A mismatch refuses the selection loudly instead of silently modelling a
different basin than the one highlighted. The activity log shows
`Verified basin <id> — N parts` on every selection.

### Event-handling corrections
1. **The default-checked box never loaded the layer.** `change` fires only on
   user interaction, so a fresh page had the checkbox ticked and no basins -
   the only selection path was missing until you toggled it twice. Now an
   explicit `if(showAllBasinsEl.checked) loadBasinLayer()` runs at startup.
   *(Caught by the browser test, not by reading the code.)*
2. **Hover no longer fights the selection highlight.** `mouseout` restores
   `styleFor(layer)` - selected stays teal instead of reverting to the base
   style.
3. **Fetch race guarded.** Two quick clicks resolve out of order; a token
   ensures only the newest click can set the AOI. The superseded one says so.
4. **Toggling off mid-fetch is handled** - the layer is only added if the box
   is still checked when the download lands.
5. **`clearAoi` now clears the basin highlight**, resets `currentAoiLabel`, and
   bumps the request token to abandon a selection still in flight. Previously
   it left a teal-highlighted basin on the map with no AOI behind it.
6. Remaining `alert()` on the run path replaced with an activity-log line.

### Verification (headless, real layer, build 2026-09-05k)
```
controls: upload=false fileInput=false search=false list=false toggle=true draw=true
status  : 1131 basins — click one to select it
click 4080033500 (1198.9 km2) -> AOI: Basin 4080033500 · ~1201.6 km2
rapid clicks 4080033500 then 4080033600 -> AOI: Basin 4080033600 · ~681.7 km2
  (log: "Ignoring basin 4080033500 — a newer selection superseded it")
toggle off -> 0 features on map; on -> 1131; selection survives both
page errors: none   error rows: none   -> TEST PASSED
```
The rapid-click case is the important one: the AOI is the **last-clicked**
basin, not the last-resolved. Areas again agree with the catalogue to ~0.22%
(1198.9/1201.6 and 680.2/681.7), the same offset seen throughout.

## Phase 21 - CUDA build succeeds; GPU path blocked by an upstream init bug

**Request:** "Shift the job to GPU!" User chose "Port SGC to CUDA first" when
shown that the CUDA path lacks sub-grid channels.

### Built the CUDA target for the first time in this project
Never previously built here - only the 515 KB CPU binary existed.
```
cmake -DCMAKE_CUDA_ARCHITECTURES=121 -DCMAKE_PREFIX_PATH=<conda lisflood_build>
      -DNUMA_* -DnetCDF_DIR=...          (NUMA/netCDF live in the conda env)
-> /home/mudassir/lisflood_build_gpu/lisflood   10.6 MB
```
`cuobjdump` confirms `arch = sm_121`, correct for GB10 (compute 12.1, CUDA 13.0).
The binary detects and selects the device: "CUDA supported / Using CUDA device: 0".

### BLOCKER: the CUDA path dies at initialisation
```
CUDA error at cuda/cuda_util.templates.cu:11 code=2(cudaErrorMemoryAllocation)
  "cudaMemcpyToSymbol(symbol, src, count)"
```
Diagnosis, by elimination:
- **Not memory pressure.** A 20-line CUDA test program compiled `-arch=sm_121`
  succeeds at `cudaMemcpyToSymbol`, `cudaMalloc` and `cudaMallocManaged` at the
  same moment LISFLOOD fails, with 20 GB free / 39 GB available and the grid
  needing only ~0.2 GB.
  (An earlier identical failure of the test program WAS real pressure - GB10 has
  integrated memory shared with system RAM, and free RAM dips on this 32-user
  box. That resolved on retry; LISFLOOD's failure does not.)
- **Not the architecture.** Both binaries embed `sm_121`.
- **Not permissions or compute mode.** /dev/nvidia* are 0666, mode Default.
- **The actual pattern:** `CUDA_SEPARABLE_COMPILATION ON` (-rdc=true) plus
  `cudaMemcpyToSymbol` called through a template (`cuda/cuda_util.templates.cu`)
  on symbols defined in OTHER translation units (`cuda::geometry`,
  `cuda::pitch`, `cuda::physical_params`, ... from cuda_acc_simulate.cu:45+).
  Cross-TU symbol resolution under device linking is the fragile part, and this
  is CUDA 13 on Blackwell - much newer than this code targets.

Fixing this is real upstream work and a plausible third contribution alongside
the infiltration and routing patches. Not attempted yet.

### The GPU plan has three sequential blockers, not one
1. **CUDA init bug above** - nothing runs on GPU until it is fixed.
2. **No SGC, no hazard in `cuda/`** - measured: 0 files mention SGC. So even
   once (1) is fixed, the GPU cannot run the current combined pluvial+fluvial
   job, only pure rain-on-grid.
3. **No trustworthy CPU baseline to port against.** GPU ports are validated by
   matching CPU output (the routing port was accepted on correlation 1.000000).
   The CPU SGC path currently has the Phase 20-era mass-balance failure - 1.18e8
   m3 of inflow prescribed, storage rising by roughly the rain volume alone.
   Porting an unvalidated solver copies the defect and doubles the surface area
   it can hide in.

### Scope of the SGC port, measured
| | lines |
|---|---|
| CPU SGC solver (`lisflood2/sgm_fast.cpp` + `sgc.cpp`) | **6,678** |
| The ENTIRE existing CUDA acceleration path (`cuda/acc/`) | **2,200** |

17 core `SGC2_*` functions including a dynamic load balancer
(`SGC2_UpdateLoadBalance`), block-tiled row kernels, weirs, bridges, dams, point
sources and `SGC2_UpdateHazard_row`. The port is roughly 3x the size of the
whole current GPU implementation - a multi-week project, not a patch.

**Recommendation recorded:** do the inflow-volume audit first (hours, gates
everything fluvial), then fix blocker (1), then port SGC against a CPU baseline
that can actually be trusted.

## Phase 22 - Mass-balance audit closed: root cause, fix, blast radius, guard

### 1. ROOT CAUSE (ours, not upstream)
`detect_crossings` placed inflow points at `geom.intersection(domain.exterior)`
- literally ON the domain rectangle's edge. A point at `x == xmax` maps to
column `ncols`, one past the last valid index, and the solver discards that
cell's injected volume silently.

Units were NOT the problem (this was checked first): `.bdy` sums to 70.1655
m2/s, `.mass` reports peak Qin 2046.0, and 70.1655 x 29.159668 = 2046.0. The
boundary file and the `Q_multiplier = Parptr->dx` conversion are both correct.

**The MIN edges are safe, the MAX edges are not.** `int((x-west)/cs)` at
`x == west` gives 0 (valid); at `x == east` gives `ncols` (invalid). Same for
rows: `y == north` -> row 0 (valid), `y == south` -> row `nrows` (invalid).
This **refutes** the "East/West dangerous, North/South safe" hypothesis - run
b05ae2a5 lost 84.5% of its inflow on the **SOUTH** edge.

### 2. EVIDENCE (basin 4080658150, 50-yr/12h, 33 h)
Four component volumes, integrated over the full run:
| component | m3 |
|---|---|
| rain in | +3.1722e7 |
| river inflow (int Qin dt) | +1.1800e8 |
| infiltration+evap out | -7.3861e6 |
| outflow (int Qout dt) | -3.046e5 |
| predicted storage | =1.4203e8 |
| **actual storage** | **2.9023e7** |
| **imbalance** | **1.1301e8 = 79.4% of input** |

Three independent confirmations it was real, not an artifact:
- Sum of LISFLOOD's own per-interval `Verror` = 1.1299e8, matching the
  independent residual 1.1301e8 to **0.02%**.
- Zero-inflow control run: removing 1.18e8 m3 of prescribed inflow changed final
  storage by only 4.99e6 m3 (**4.23%**) and left max flood area **identical**
  at 373.7 km2.
- With inflow zeroed, `max|Verror|` = **1.45e-07 m3** (machine precision), so
  every bit of the error came from point-source injection alone.
- The single inflow that WAS inside the grid carried **4.24%** of total
  discharge; measured storage gain was **4.23%**. Exact match.

Also corrected: the `.mass` final column is `Rain-(Inf+Evap)` - cumulative NET
volume, not a rain rate. `MASS_COLUMNS` called it `Rain`, and Phase 21
integrated it as a rate. Renamed to `NetRainCum`.

### 3. FIX
`_snap_to_interior_cell()` snaps every crossing to the nearest interior cell
**centre**. A centre, not a clamp: a coordinate exactly on a cell boundary is at
the mercy of floating point, which is precisely why the written value
881640.924 landed at column 635 while `east` recomputed from bounds gave 634.

`MODEL_VERSION` 10 -> 11. Every combined pluvial+fluvial result before v11
understates river contribution.

### 4. BLAST RADIUS - which prior runs are affected
Scanned all 21 run directories still on disk (run dirs are pruned at 20, so
older ones are gone; these are what remains).

| job | grid | edge | out/total | % of inflow DISCARDED | verdict |
|---|---|---|---|---|---|
| 143b9ac6 (4080658150) | 816x635 | **E** | 4/5 | **95.76%** | invalid |
| b05ae2a5 (4080571660) | 810x949 | **S** | 3/6 | **84.54%** | invalid (also segfaulted) |
| e0627193 (4080662030) | 1462x2198 | **E** | 3/8 | **1.87%** | minor |
| 29fea2bd | 332x593 | **E** | 2/8 | **0.70%** | minor |
| 58082fbf (re-run, fixed) | 816x635 | - | 0/5 | 0.00% | clean |
| 77f28a42 (4080033380) | 852x1558 | - | 0/5 | 0.00% | unaffected |
| 8ab39a3b | 1882x1564 | - | 0/8 | 0.00% | unaffected |
| fd9560b3 | 1476x1920 | - | 0/8 | 0.00% | unaffected |
| 7c8dc71b, c4e6d7a2 | small | - | 0/1-2 | 0.00% | unaffected |
| **Charsadda** (2cd95c88, 2d9e33b1, 85921a7b, 953592e3, cf40966c, e3d9c71d) | 169x144 | - | **0/1** | **0.00%** | **unaffected** |

**Phase 7 Charsadda is NOT affected.** Identified by its peak Qin of 366.77
m3/s (reported 366.75); its single inflow sits at row 0 - the NORTH edge, a
valid index. The Phase 7 acceptance numbers (max depth 5.918 m, hazard 18.122,
peak Qin 366.75) stand and need no re-run.

Two runs are materially invalid (95.8% and 84.5% of inflow discarded); two are
marginally affected (<2%). Not re-run in this pass, per instruction.

### 5. VERROR REPORTING FIXED (was only flagged, now changed)
`_mass_balance_check()` reports closure against **inflow**, not storage.
Rationale in-code: `Verror` is a PER-MASS-INTERVAL error
(`sgm_fast.cpp:4519`), so its end-of-run value says nothing - it returns to ~0
once inflow stops. What matters is sum|Verror| over the run against the volume
that was meant to enter. The old storage-relative framing showed "5.957% of
volume" on a run that was discarding 96% of its inflow.

New API/result fields: `inflow_volume_m3`, `outflow_volume_m3`,
`net_rain_volume_m3`, `storage_change_m3`, `balance_residual_m3`,
`balance_residual_frac`, `verror_sum_m3`, `verror_frac_of_inflow`,
`mass_balance_suspect`.

**Automatic guard:** `MASS_BALANCE_SUSPECT_FRAC = 0.05`. If sum|Verror| exceeds
5% of prescribed inflow the run is flagged `mass_balance_suspect` and carries a
loud model note; the progress stream emits a WARNING. A correct run sits at
~1e-9 and a broken one at 0.96, so the threshold discriminates without false
alarms. Pluvial-only runs report `verror_frac_of_inflow = None` and are never
flagged (no inflow to be relative to).

Tests: `tests/test_inflow_placement.py` (4) and `tests/test_mass_balance_check.py`
(5, run against the REAL broken and control `.mass` files, not mocks). 36 total,
all passing. One test caught its own bad assumption during writing - `east`
recomputed from `grid.bounds` rounds to 634, so the test now uses the literal
881640.924 from the failing `.bci`.

### 6. RE-RUN CONFIRMS CLOSURE (task 1 acceptance)
Basin 4080658150, 50-yr/12h, 33 h, identical inputs, job 58082fbf:

| component | before fix | AFTER FIX |
|---|---|---|
| rain_vol | 3.1722e7 | 3.1722e7 |
| inflow_vol (int Qin dt) | 1.1800e8 | 1.1800e8 |
| outflow_vol (int Qout dt) | 3.046e5 | **9.0317e7** |
| infil_evap_vol | 7.3861e6 | 7.5391e6 |
| Vol_end - Vol_0 | 2.9023e7 | 5.1832e7 |
| IN = rain+inflow | 1.4972e8 | 1.4972e8 |
| OUT = outflow+storage+infil | 3.6713e7 | **1.4969e8** |
| **RESIDUAL** | **+75.48% of input** | **+0.0223% of input** |
| peak Qin | 2046.0 (only 4.24% delivered) | **2046.0, all 5 delivered** |
| max \|Verror\| | 1.7523e+06 | **2.1525e-07** |
| sum \|Verror\| | 1.1299e8 = 95.76% of inflow | **8.36e-06 = 0.000000%** |

`Verror` is at machine precision across the WHOLE run, not just where inflow is
zero. Outflow rose 300x: the river is now routed through the domain and out the
free outlet. **This retracts the earlier "the outlet is a bathtub plug"
conclusion** - the outlet was working; almost nothing was reaching it.

The residual 0.0223% is limited by the rain term, which is estimated from
`rain_total_mm_mean` x grid area rather than read directly (the .mass gives only
rain NET of infiltration).

### 7. Result unchanged for THIS basin - and why that is not a contradiction
The corrected run adds 9.0e7 m3 of throughflow yet the AOI-scoped headline
figures are identical (76.0 km2, 12.07 m). Checked rather than assumed:
- 7,943 cells changed by >1 cm, up to 11.97 m - the channel corridor.
- **99.9% of those cells (7,933) are OUTSIDE the basin polygon**; only 10 inside.
- **0 of the 5 inflow points lie inside the basin polygon.**

Because crossings are detected against the DOMAIN RECTANGLE (fluvial.py's own
docstring says so - LISFLOOD edge BCs can only live on the rectangle), the
rivers entering the rectangle are not necessarily rivers entering the basin. For
this basin the river corridor runs outside the polygon, so correcting the inflow
changes the domain result but not the AOI-scoped one.

**This is a separate, real design issue** (fluvial forcing is bounding-box
relative while reported statistics are AOI relative) - logged, NOT addressed in
this correctness pass.

## Phase 23 - AUDIT: inflow points sit outside the AOI polygon (no code changed)

Follow-up to the Phase 22 mass-balance fix. Audit only - `detect_crossings` and
the fluvial pipeline were NOT modified in this pass.

### 1. Scope: every crossing, in every basin run, is outside the AOI polygon
Runs were matched to their basin by comparing the domain rectangle against all
1131 basin bounds (+200 m buffer); 6 matched within 50 m, 77f28a42 identified
from its run-history label (its grid is DEM-truncated, Phase 19, so bounds
differ by 11 km).

| run | basin | poly km2 | rect km2 | ratio | crossings | inside AOI | rect-only | median dist | max dist |
|---|---|---|---|---|---|---|---|---|---|
| 29fea2bd | 4080939540 | 85.4 | 167.4 | 1.96x | 8 | **0** | 8 | 1471 m | 4739 m |
| 143b9ac6 / 58082fbf | 4080658150 | 225.6 | 440.6 | 1.95x | 5 | **0** | 5 | 3403 m | 3730 m |
| b05ae2a5 | 4080571660 | 325.8 | 653.6 | 2.01x | 6 | **0** | 6 | 5914 m | 10346 m |
| 77f28a42 | 4080033380 | 708.1 | 1128.7 | 1.59x | 5 | **0** | 5 | 4806 m | 12687 m |
| e0627193 | 4080662030 | 1127.3 | 2732.4 | 2.42x | 8 | **0** | 8 | 9433 m | 23598 m |
| fd9560b3 | 4080678160 | 1274.0 | 2409.6 | 1.89x | 8 | **0** | 8 | 7663 m | 12391 m |

**0 of 40 crossings lie inside the AOI polygon.** Range 426 m - 23.6 km, median
4234 m (145 cells). This is structural, not incidental: crossings are computed
on `domain.exterior`, and the rectangle touches the polygon only at the four
points where the polygon's bbox grazes it, so a crossing is essentially never
inside.

**Correlation (task 1.4):** distance correlates only moderately with the
rect/polygon ratio (**r = +0.51**) but strongly with basin size
(**r = +0.89** vs sqrt(area)). So the Phase 18 ratio is NOT the main driver -
absolute basin scale is. A big basin has a big margin in metres even at a
modest ratio.

Runs with unrecoverable AOIs (Charsadda x6, Thar, other early AOIs) are drawn
rectangles/districts whose geometry was never stored. Note for those: **a
rectangular AOI's polygon IS its bounding rectangle**, so polygon and rectangle
crossings coincide to within the 200 m buffer. This defect is specific to
non-rectangular (basin) AOIs.

### 2. Wrong or imprecise? BOTH - and which is which is measurable
Compared the discharge actually injected against the discharge of reaches that
genuinely ENTER the basin (downstream end inside the polygon, upstream outside;
data digitises downstream->upstream, fluvial.py:127):

| basin | injected m3/s | legitimate inflows | legitimate m3/s | verdict |
|---|---|---|---|---|
| 4080658150 | 2046.0 | 1 | 1543.3 | comparable - IMPRECISE |
| 4080939540 | 8901.8 | 3 | 8772.9 | comparable - IMPRECISE |
| 4080678160 | 4967.8 | 1 | 3373.8 | comparable - IMPRECISE |
| 4080571660 | 79.3 | **0** | 0.0 | **WRONG - no inflow exists** |
| 4080662030 | 25331.1 | **0** | 0.0 | **WRONG - no inflow exists** |
| 4080033380 | 30376.0 | **0** | 0.0 | **WRONG - no inflow exists** |

**Half the audited basins receive entirely spurious inflow**, twice at
25,000-30,000 m3/s. The reason is hydrological: a HydroSHEDS level-8 basin is a
drainage unit, so most of its reaches are *wholly inside* it (self-generated by
rainfall) and few cross its boundary:

| basin | reaches touching | wholly inside | crossing boundary |
|---|---|---|---|
| 4080571660 | 26 | 25 | 1 |
| 4080662030 | 63 | 61 | 2 |
| 4080678160 | 84 | 82 | 2 |
| 4080033380 | 23 | 12 | 11 (all OUTFLOWS - delta distributaries) |

For a headwater/self-contained basin the correct fluvial inflow is **zero**;
the model currently injects the trunk rivers that merely pass through the padded
margin. Separately, **0 of 40 selected inflows sit on a reach that intersects
the basin at all** - so even where the magnitude happens to be comparable, the
water is injected onto a different river.

### 3. A premise in the brief is refuted: LISFLOOD CAN inject mid-domain
The brief assumed a boundary point must sit on the grid edge. It does not
(`input.cpp:1650`):
```c
BCptr->xpi[pi] = (int)((px - Parptr->blx) / Parptr->dx);
BCptr->ypi[pi] = (int)((Parptr->tly - py) / Parptr->dy);
```
A `P` point source is just a (col,row) index with no edge constraint. (That same
truncation is what produced column 635 in the Phase 22 bug.) Placing inflows on
the rectangle edge was never a solver requirement.

### 4. Fix options (for decision - none implemented)
**A. Detect crossings against the AOI POLYGON, inject at that cell.**
Now known to be legal (section 3). Correct river set AND correct location;
self-contained basins would correctly get zero inflow. Cost: the outlet still
needs a rectangle edge (FREE boundaries ARE edge-bound), so outlet and inflow
logic diverge; water entering inside the domain must still find its way out.
Biggest behaviour change - many basins would lose their fluvial forcing
entirely, which is correct but will look like a regression.

**B. Detect on the polygon, inject at the nearest rectangle-edge cell on the
same reach.** Keeps every injection on an edge. But the audit shows the reaches
are frequently not the same river at all, so this preserves the wrong-river
problem while fixing only the anchoring - it addresses the imprecise cases and
none of the wrong ones. Cheapest, weakest.

**C. Shrink the domain to the AOI bounding box with a 1-cell buffer** so
rectangle and polygon crossings nearly coincide. Does not work here: the gap is
not buffer padding (200 m) but the bbox-vs-polygon geometry itself, median 4234 m
and up to 23.6 km. Rejected by the measurements.

**Recommendation: A**, with the spurious-inflow cases (0 legitimate inflows)
treated as the acceptance test. Not implemented pending a decision.

## Phase 24 - Option A implemented: inflows detected on the AOI polygon

### Implementation
- `detect_crossings(..., aoi_geom=)` detects **inflows on the AOI POLYGON**
  (`.boundary`, MultiPolygon-safe) and injects at that interior cell. Legal
  because a `.bci` P source is a plain (col,row) with no edge constraint
  (input.cpp:1650). **Outflows still detected on the domain RECTANGLE** - FREE
  boundaries genuinely are edge-bound.
- `build_fluvial_forcing` REORDERED: the SGC width raster is now built before
  the `.bci` is written, since an injection point cannot be checked against a
  channel raster that does not exist yet.
- `snap_inflows_to_channel()` snaps an off-channel point to the nearest channel
  cell **on its own reach only**, logs the distance, and never moves it to a
  different reach. If the reach has no channel cell it is left in place and
  reported - never silently dropped.
- `check_discharge_plausibility()` flags (never blocks) specific discharge above
  **20 m3/s per km2 of AOI**, surfaced in model notes like the mass-balance guard.
- Module docstring corrected - it still asserted the old rectangle rule.
- `MODEL_VERSION` 11 -> 12: every historic combined result on a basin-polygon
  AOI is invalidated, not only the runs still on disk.

### Detection vs the Phase 23 audit reference
| basin | old m3/s | NEW m3/s | audit reference | |
|---|---|---|---|---|
| 4080658150 | 2046.0 | **1543.3** | 1543.3 | exact |
| 4080939540 | 8901.8 | **8772.9** | 8772.9 | exact |
| 4080678160 | 4967.8 | **3373.8** | 3373.8 | exact |
| 4080571660 | 79.3 | **0.0** | 0.0 | zero, correct |
| 4080033380 | 30376.0 | **0.0** | 0.0 | zero, correct |
| 4080662030 | 25331.1 | 24544.6 | 0.0 | **see below** |

On-channel: **all 5 inflows across the three legitimate basins landed on-channel;
no snap was required.**

### 4080662030: the audit's reference was wrong, not the code
Reach 440451489 has **both ends outside the basin and crosses its boundary
twice** - geometrically a through-flow, which the new code correctly counts. The
Phase 23 reference used a stricter test (*terminates* inside) that excluded
through-flow entirely, so **0.0 was an incomplete reference**.

It is nonetheless marginal: only **290 m of the reach's 3.09 km** lies inside the
basin, and it drains **336,849 km2 - 300x the basin's own 1127 km2**. A trunk
river grazing a corner, not a river flowing through. The plausibility guard fires
on it (21.8 > 20 m3/s/km2). **No minimum-traverse rule was added** - what counts
as "passing through" is a modelling decision, not a bug fix, and is left open.

### Verification runs (v12)
**4080658150, 50-yr/12h - combined, 1 real inflow:**
```
rain 3.1722e7 | inflow 8.9005e7 | outflow 8.9251e7 | infil 7.3741e6 | dS 2.4095e7
IN 1.2073e8  OUT 1.2072e8   residual +0.0065% of input
peak Qin 1543.2  max|Verror| 2.16e-07  sum|Verror| 6.32e-06  (7.1e-14 of inflow)
```
**4080033380, 50-yr/3h - now correctly PLUVIAL-ONLY (was 30,376 m3/s phantom):**
```
rain 7.1107e7 | inflow 0 | outflow 0 | infil 6.8316e7 | dS 2.7908e6
IN 7.1107e7  OUT 7.1107e7   residual +0.0003% of input
scenario_type = "pluvial (rain-on-grid) only", has_fluvial = False
note: "no river inflow crossings detected - pure rain-on-grid scenario"
```
Effect of removing the phantom flood on that basin, AOI-scoped, same 50-yr/3h
scenario: **max depth 5.80 m -> 0.32 m**; area 27.8 -> 26.1 km2. The 12.7 m
domain-wide depths were the fictitious river. Sim window also fell 33 h -> 15 h
(no hydrograph base time to accommodate).

### Charsadda / drawn AOIs: NOT a no-op - my prediction was wrong
I predicted this change would be a no-op for a drawn rectangular AOI because
"the polygon IS the rectangle". Measured: it is not. The domain is AOI **+200 m
buffer**, so the polygon is always inset (ratio 1.067x on a 12 km test box). The
polygon set is a SUPERSET - the same 6 reaches plus 2 (843 m3/s) whose upstream
ends start inside that margin. Small next to basins (1.067x vs ~2x), but real.
The original Charsadda AOI geometry was never stored, so it cannot be re-run
directly; this is the closest equivalent test.

Tests: `tests/test_polygon_crossings.py` (8) including the reconstructed
30,376 m3/s phantom and a decoy-reach snap test. 44 total, all passing.

## Phase 25 - Location search + structured activity log (frontend only)

UI build `2026-09-06a`. No backend, pipeline, solver or fluvial-logic changes.

### 1. OSM Nominatim location search - NAVIGATION ONLY
Search box overlaid on the map. Selecting a result flies the map and drops a
marker; **it never touches AOI state**. Basin-click remains the only AOI path
(Phase 20).

Rate-limit and policy handling:
- debounce 700 ms, hard floor of **1100 ms between requests**, and only ONE
  request in flight at a time; minimum 3 characters. Fires only on human input.
- `countrycodes=pk`, `limit=5`, `format=jsonv2&addressdetails=1`.
- **A browser cannot set User-Agent or Referer**: both are Forbidden header
  names, so an assignment from `fetch()` is silently dropped. Identification
  therefore rests on the Referer the browser sends by itself, which here is only
  `http://127.0.0.1:8899` - weak. Documented in-code with the remedy (proxy via
  our own backend, which CAN set a real User-Agent, or self-host Nominatim) if
  usage ever grows beyond occasional interactive lookups. **This is the one part
  of the brief that cannot be implemented as literally worded.**
- Deliberately NOT sending an `email=` contact parameter: it is the user's
  personal data and theirs to opt into, not ours to add to an outbound URL.
- Zero results and network/HTTP failures both produce a visible note AND an
  activity-log line - never a silent no-op.
- Reuses the Phase 15 fly helper. `zoomToLayer` was refactored to delegate to a
  new `zoomToBounds`, so AOI zoom and search fly through ONE implementation.

### 2. Structured activity log, additive
`emit(type, fields, prose, kind)` is the single event source: it writes the
structured record AND the prose line, so the two cannot drift. `logStep()` is now
a thin wrapper (`emit(null, null, msg, kind)`), which is why ~40 existing prose
call sites needed no edits. An unknown event type is refused and logged to the
console rather than silently accepted - typo guard.

Fixed vocabulary, exactly as specified: `search_query`, `search_select`,
`basin_click`, `basin_select`, `aoi_draw_start`, `aoi_draw_complete`,
`aoi_clear`, `scenario_submit`, `scenario_result`, `map_layer_toggle`. Discrete
actions only - no pan/zoom/mousemove. `scenario_result` reuses backend fields
(`inundated_area_km2`, `max_depth_m`, `mass_balance_suspect`); nothing invented.

"copy json" button copies the structured stream as JSON-lines, with a
`document.execCommand` fallback for when the async clipboard API is unavailable.

### Verification (headless, real Nominatim, real scenario)
Full acceptance sequence - one search, one basin click, one scenario submit:
```
{"ts":"2026-09-06T08:43:09.298Z","event":"search_query","query":"Charsadda","results_count":2}
{"ts":"2026-09-06T08:43:09.599Z","event":"search_select","query":"Charsadda","chosen_name":"Charsadda, ... Khyber Pakhtunkhwa, 24000, Pakistan","lat":34.150946,"lon":71.7592937}
{"ts":"2026-09-06T08:43:12.107Z","event":"basin_click","basin_id":4080801610,"area_km2":26.4,"centroid_lat":28.872633,"centroid_lon":72.458512}
{"ts":"2026-09-06T08:43:12.152Z","event":"basin_select","basin_id":4080801610,"verified":true}
{"ts":"2026-09-06T08:43:14.595Z","event":"scenario_submit","aoi_label":"Basin 4080801610 (26.5 km2)","return_period":50,"duration_h":1,"soil_moisture":"normal","job_id":"671a1210-..."}
{"ts":"2026-09-06T08:43:44.602Z","event":"scenario_result","job_id":"671a1210-...","status":"done","inundated_km2":16.93770190379247,"max_depth_m":7.736,"mass_balance_suspect":false}
```
Search moved the map from (30.3753, 69.3451) z5 to (34.1509, 71.7593) z14 while
**AOI state was byte-identical before and after** (`shown:false, detail:"—"`).
Prose panel still rendering (13 rows). No page errors.

A first test run reported "no search results" - that was a bug in the TEST, not
the app: it waited for `.note` to appear, which the "Searching..." placeholder
already satisfied. Nominatim was verified reachable independently (HTTP 200 in
0.59 s, `access-control-allow-origin: *`) before touching the app code.

## Phase 26 - End-to-end run: Charsadda (basin 4080640980), 50-yr / 6 h

Driven through the dashboard UI headlessly (search -> basin click -> submit),
since the user could not reach the page from Windows.

### Journey
Nominatim returned two "Charsadda" hits - a railway station and the city. Chose
the **city** (34.14615, 71.73375); the map flew there and the basin under it was
clicked. Verified afterwards that basin **4080640980 genuinely contains that
point**. (An earlier note in this session said basin 4080643260 contains
Charsadda - that was computed from the *station* coordinate, which lies in a
different basin. Both are correct for their own point.)

The browser automation later timed out and exited before capturing results;
the job was unaffected and completed server-side. Harness flaw, not app.

### Result (job 3222478f, 949 s solver, grid 1761x967 = 1.70 M cells)
| | AOI-scoped | whole rectangle |
|---|---|---|
| area | 597 km2 | 1448 km2 (2.43x) |
| inundated | **212.72 km2 (36% of the basin)** | 556.75 km2 |
| max depth | **16.98 m** | 33.76 m |
| max hazard (DEFRA) | 83.02 | |

Storm: alternating block, 67 mm mean (max 74), peak **17.1 mm/h at hour 3**,
uniform-equivalent 11.1 mm/h. Infiltration 8.57e-07 m/s distributed per-cell.
AOI 100% covered by the DEM (no Phase 19 truncation here).

Fluvial: **2 inflows, 4263 m3/s total** - inflow_1 is reach 440499612 at
4232.6 m3/s draining **15,966 km2** (the Kabul/Swat system), inflow_2 a local
30.5 m3/s tributary. 21,464 SGC channel cells, FREE outlet on the S edge at a
river crossing.

### Mass balance - CLOSES
```
inflow 1.2293e8 | outflow 7.4082e7 | net rain 6.8930e7 | storage change 1.1779e8
balance residual 0.002184% of input
verror/inflow    1.753e-05        mass_balance_suspect = False
```
The Phase 22/24 fixes hold on a fresh basin: peak Qin 4262.9 m3/s matches the
prescribed hydrograph, peak Qout 2346.6 m3/s - the river now genuinely routes
through and out.

### Caveats on these numbers
- **Window truncated**: 35.5% of peak outflow still leaving at t=18 h. The peak
  IS inside the window, so max depth is sound, but drain-down is cut short.
- ~~**DDF monotonicity corrections: 1,344,241 cells = 79% of the grid** - the
  same upstream rainfall-dataset defect...~~ **RETRACTED - see Phase 27.** That
  figure was float32 round-trip noise in a badly-thresholded counter, not a data
  defect. The genuine violation count for this run is **ZERO**. Corrected in
  Phase 28: the counter now uses a relative tolerance and reports 0 here.
- 16.98 m max depth / DEFRA hazard 83 are almost certainly in-channel SGC cells,
  not floodplain values a person would stand in - the "report floodplain and
  channel depths separately" item from the Phase 18 suggestions is still open.
- Channel widths, SGC depths and hydrograph shape remain uncalibrated
  planning-level defaults, as the model's own note says.

## Phase 27 - AUDIT: the "79% DDF correction" figure is FLOAT NOISE, not bad data

Diagnosis only - no change to the clamping/correction logic.

### 1. What the 79% is actually counting
Reproduced `_hourly_cumulative_depth` step by step on the exact Charsadda inputs
(basin 4080640980, RP50, 6 h; grid 1761x967 = 1,702,887 cells; knots 1, 3, 6 h).
The shipped counter's figure reproduces exactly - 1,344,241 cells (78.9%) - and
decomposes as:

| step | cells changed (counter's own 1e-6 tol) |
|---|---|
| clamp `min(P, total)` | 670,259 (39.36%) |
| endpoint `P[-1] = total` | 673,982 (39.58%) |
| **running max (the actual monotonicity enforcement)** | **0 (0.00%)** |
| **reported total** | **1,344,241 (78.9%)** |

**The monotonicity enforcement fired on ZERO cells.** Every flagged cell comes
from the clamp and the endpoint assignment.

Why those two fire nearly everywhere: at hour 6 the interpolation is evaluated
AT the 6 h knot, so `raw[-1]` should equal `total` exactly. It does not, because
`P = exp(log(P))` in float32 loses ~1 ulp:

```
endpoint |raw[-1] - total| : median 7.629e-06 mm, max 1.526e-05 mm
median RELATIVE difference : 1.143e-07     (float32 eps = 1.19e-07)
cells with |diff| > 1e-6 mm (counter's tol) : 1,344,241  (78.9%)
cells with RELATIVE diff > 1e-4             :         0  (0.000%)
```

**Root cause of the metric: an ABSOLUTE tolerance of 1e-6 mm applied to depths of
order 10-100 mm - below float32 round-trip precision.** The counter cannot
distinguish "input defect corrected" from "exp(log(x)) != x", because at that
threshold the second case is indistinguishable from the first. That inability is
itself the root cause, exactly as the audit brief anticipated.

### 2. Genuine violations, Phase 8 definition, this basin: ZERO
| check | cells |
|---|---|
| depth 1h > 3h | 0 |
| depth 3h > 6h | 0 |
| intensity 3h > 1h | 0 |

Largest depth change across durations anywhere in the basin is **-20.57 mm**,
i.e. depth strictly INCREASES with duration everywhere. There is no
non-monotonic DDF anywhere in this footprint.

### 3. Reconciliation with the Phase 8 national finding - it holds
Re-scanned the national 0.1 deg source rasters (27,822 valid px per RP):

| RP | 1h>3h | 3h>6h | 6h>12h | 12h>24h | 3h>1h intensity |
|---|---|---|---|---|---|
| 2 | 0 | 0 | 0 | 0 | 0 |
| 10 | 0 | 0 | 0 | 0 | 0 |
| 25 | 0 | 0 | 0 | 1 | 2 |
| 50 | 0 | 0 | 0 | 4 | 36 |
| 75 | 0 | 0 | 0 | 5 | 92 |
| 100 | 0 | 0 | 0 | 6 | 144 |

**12h>24h totals 16 px across all RPs - Phase 8's original number exactly**, and
3h>1h peaks at 144 px at RP100 (Phase 8 said "up to 150"). Phase 8 was right and
remains right.

Crucially the genuine defect lives ONLY in the **12h/24h depth pair** and the
**1h/3h intensity relation**. A 6-hour run loads knots 1, 3, 6 and therefore
**cannot touch the 12h/24h defect at all**. At RP50 the 1h/3h intensity defect
is 36 px of 27,822 nationally (0.13%); the Charsadda domain spans roughly 12
native pixels, so zero hits is the expected outcome, not a surprise.

### 4. VERDICT
**The Phase 26 caveat is WRONG and is retracted.** "1,344,241 cells = 79% of the
grid - the same upstream rainfall-dataset defect" is not correct: it is
float32 round-trip noise in a badly-thresholded counter, and the genuine
violation count for that run is **zero**. Phase 17's identical claim (402,015 of
518,160 = 78% on basin 4080658150) is the same artifact and is retracted with it.

This is NOT a new national data-quality finding. The real defect remains what
Phase 8 measured: 16 px nationally (12h>24h) plus up to 144 px at RP100
(3h>1h intensity), i.e. <=0.5% of the raster, affecting only runs of 12 h or
24 h duration.

**No code changed.** The counter's threshold is the thing to fix - an absolute
1e-6 mm test should become a relative one (rel > 1e-4 gives 0 here, which is the
truth) - but that is a change to the correction/reporting logic and was
explicitly out of scope for this pass.

## Phase 28 - Fixed the DDF correction counter; cleaned up the false alarm

### 1. Relative tolerance
`_hourly_cumulative_depth` compared `|monotone - raw| > 1e-6` **millimetres** -
below float32 round-trip precision on depths of 10-100 mm. Now compares
`|monotone - raw| / max(|raw|, 1e-9) > 1e-4`, the threshold measured in Phase 27
to give zero false positives while keeping every genuine detection. Correction
LOGIC is untouched; only the counting comparison changed.

Verified in both directions:

**False positives eliminated** - Charsadda basin 4080640980, RP50 / 6 h,
1,702,887 cells:
```
corrections reported : 0        (was 1,344,241 = 78.9%)
curve still non-decreasing : True
endpoint == design depth   : True
```

**True positives preserved** - the known Phase 8 defect (12h > 24h at RP100):
```
defective pixels nationally      : 6   (px[55,191] 12h=683.61 vs 24h=671.19, etc.)
5x5 tile with 2 genuine violations -> counter reports 2
same tile at RP2 (no defect)       -> counter reports 0
NATIONAL RP100 / 24 h through the fixed counter -> 6 of 27,822 (0.0216%)
Phase 8 reference for that pair    : 6 px                          MATCH
```

### 2. Cleanup of the retracted claim
- **Source / UI / docs**: no hardcoded "79%/78%" claim existed anywhere. The
  dashboard renders the live `ddf_monotonicity_corrections` field, which is now
  correct by construction. The only code mention is the new comment in
  `_hourly_cumulative_depth` explaining why the tolerance is relative.
- **PROGRESS.md**: Phase 26's caveat corrected IN PLACE (struck through with a
  pointer to Phase 27, not deleted, so the error stays visible). Phase 27 is the
  retraction itself and stays as written. Phase 17's number was never written
  into PROGRESS.md - it only ever appeared in a pasted dashboard panel.
- **Cached results**: 10 entries carried inflated counts. 6 predate the
  MODEL_VERSION 11->12 bump and are already unreachable (the version is part of
  the cache key), so they cannot be served. The **4 still-reachable entries were
  INVALIDATED** (deleted: 3033461885ad the Charsadda run, 98671a47b912,
  a7ded9a3cbaf, eeb761e2c326 - 23.8 MB total) rather than hand-edited. Deleting
  is preferable to patching a stored scientific result in place: they regenerate
  correctly and completely on the next request, and no partially-corrected
  artifact is left behind.
- **False alarm inside the cleanup**: one cached entry initially matched the
  "79%" search. Its note reads *"only 79% of the AOI is inside the DEM extent"* -
  the legitimate Phase 19 DEM-truncation warning, unrelated to DDF. Checked
  before touching it; left intact.

### 3. The genuine Phase 8 caveat is untouched
Still stated in `pipeline/aoi_pipeline.py:235-236` ("12h depth exceeds 24h depth
at 16 px, and 3h intensity exceeds 1h intensity at up to 150 px at RP100") and
in PROGRESS.md Phase 8 (lines 338-339, 440, 601). That finding was correct, was
re-verified in Phase 27, and remains the real, small (<=0.5%) data-quality issue.

Tests: `tests/test_ddf_correction_count.py` (6 new) covering the clean case
across four realistic depth magnitudes, a genuine 12h>24h violation, counting
only the violating cells, behaviour either side of the 1e-4 threshold, and an
end-to-end national RP100 check asserting exactly Phase 8's 6 px. 53 total,
all passing.
