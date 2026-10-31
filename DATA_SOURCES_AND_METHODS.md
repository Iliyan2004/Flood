# SAILAB — Technical Reference: Data Sources, Paths, Methods and Validation

Rebuilt on 8 October 2026 from the code in `~/flood/` (read module by module, constants copied from source).
The previous edition (12 September 2026) is kept as `DATA_SOURCES_AND_METHODS.md.bak_2026-09-12`.
This file says **where each dataset lives, what is computed from it, and how far the result has been checked**.
For the history of bugs and decisions, see `CLAUDE.md`.

Conventions used below:
- "Measured" means the number came from an actual run on this machine; the run is named.
- Paths written `<app>/` are relative to the app directory (`~/flood/` on the server).
- Every shared-drive path can be overridden per machine with an environment variable `SAILAB_<NAME>` (§1.1).

---

## 0. System at a glance

| Layer | What it is |
|---|---|
| Web app | Flask, `fis_app.py`, port 8000. No auto-reload: restart the process after any backend edit. |
| Flood physics | LISFLOOD-FP 8.2 (Bristol), built from source with CUDA-13/ARM64 build fixes and two local kernel patches (§4.7). Called as a subprocess by `flood_model.py`. |
| Hardware | NVIDIA GB10 (DGX Spark), CUDA 13, shared with other users. The app applies no GPU throttling or temperature caps. |
| Map / 3D | Mapbox GL JS v3.6.0 (2D map and native 3D terrain); token from `.env`, never in code. |
| UI tiers | Tier 1 rainfall/discharge context → Tier 2 instant HAND flood preview → Tier 3 LISFLOOD-FP physics run (buildings, roads, damage, loss, NbS, protection, comparisons) → Coastal/tidal tab. |

---

## 1. Data on disk

### 1.1 Shared-drive datasets (`data_paths.py`, one place for every path)

`SHARE = ~/DGX-Shared` (override `SAILAB_SHARE`); `VECTOR = SHARE/4. Vector Data`.
`check_data_paths()` runs at start-up and is served at `/data-status`; a missing required path stops runs with a clear error instead of a silent fallback.

| Name | Default path and contents | Required | Used by |
|---|---|---|---|
| `PAKISTAN_DEM` | `SHARE/3. Raster Data/DEM_SRTM/pakDEM/Pak_srtm.tif` — SRTM, EPSG:32642, ~29 m, 13.48 GB | yes | every DEM crop |
| `HOSPITALS_SHP` | `VECTOR/Hospital/Hospitals.shp` (35,561 points) | no | `infra_utils.fetch_local_critical_infra` |
| `SCHOOLS_SHP` | `VECTOR/Schools/Schools.shp` (65,909 points) | no | same |
| `POWER_LINES_SHP` | `VECTOR/Electricity Transmission Line/Electricity_transmission_line.shp` (419 lines) | no | `infra_utils.fetch_local_power_lines` |
| `AIRPORTS_SHP` | `VECTOR/National and International Airports/Airports.shp` (136) | no | `infra_utils.fetch_local_strategic_infra` |
| `SEA_PORTS_SHP` | `VECTOR/Sea Ports/Sea_ports.shp` (3; stored in Web Mercator, reprojected on load) | no | same |
| `RAILWAY_STATIONS_SHP` | `VECTOR/Railway data/Railway_stationpoint_shp/Railway_stationpoint.shp` (655) | no | same |
| `RAILWAY_LINES_SHP` | `VECTOR/Railway data/Railway_lines_shp/Railway_lines.shp` (659; MultiLineStrings exploded) | no | `infra_utils.fetch_local_railway_lines` |
| `HYDROLAKES_POINTS_SHP` | `VECTOR/Hydrolakes/Hydrolakes_points.shp` (953 Pakistan lakes; `Vol_total` in 10⁶ m³) | no | `glof_utils` |
| `PMD_GLACIER_SHP` | `SHARE/Glof data/PMD_Glacier_Lake_Inventory/.../Glacier_2024_PMD.shp` | no | melt-model glacier mask |
| `PMD_LAKES_SHP` | same folder, `Lakes_2024_PMD.shp` | no | melt-model lake nodes |
| `ERA5_T2M_ZARR` | `SHARE/0. ERA-5 Variables/t2m_zarr_space_opt` — ERA5-Land hourly 2 m temperature, 0.1°, 1980 → May 2026, °C | no | melt model |
| `ERA5_TP_ZARR` | `SHARE/0. ERA-5 Variables/tp_zarr_space_opt` — ERA5-Land hourly precipitation | no | melt model |
| `GLACIER_THICKNESS_TIF` | `<app>/global_data/glacier_inputs/Glacier thickness/glacier_thickness.tif` — IceBoost v2.0 modelled ice thickness (RGI v7 region 14, Zenodo 21220985), m, 26.6 m, EPSG:32643, northern Pakistan | no | melt model (§9.2) |
| `DEBRIS_FACTOR_TIF` | `<app>/global_data/glacier_inputs/Debris/debris_factor.tif` — dimensionless ice-melt multiplier on the same grid | no | melt model (§9.2) |
| `LSM_ENSEMBLE_CLASS` / `_PROB` | `SHARE/MERGED LANDSLIDE LAYERS/LSM_CNN_DNN/outputs/04_maps/susceptibility_{class,prob}_Ensemble.tif` — ML landslide susceptibility, KPK/GB/AJK only | no | `geomorphology_utils` |
| `CONFINEMENT_TIF` / `VALLEY_DEPTH_TIF` | `SHARE/TopograhicVariables/05_confinement/confinement.tif`, `.../06_valley_depth/valley_depth.tif` | no | `geomorphology_utils` |
| `CMIP6_ROOT` | `~/Desktop/DGX-Shared/CMIP6` — NASA NEX-GDDP-CMIP6, 34 models, daily precipitation | no | `climate_utils` |

Read directly rather than through `data_paths.py`:
- GloFAS historical reanalysis: `SHARE/7. GLOFAS Variables/GLOFAS_Historical/river_discharge/river_discharge_pak_{1979..2025}.nc` (`dis24`, m³/s, 0.05°) and `.../runoff_water/runoff_water_pak_{1979..2012}.nc` (`rowe`, kg m⁻² day⁻¹). Used by the melt-model check.
- Earth Engine credentials: `~/.config/earthengine/credentials` (`glacier_monitoring_utils`, `storm_motion_lstm`). **At the last check the refresh token was expired or revoked**, so both features return "Earth Engine is not available" until someone re-runs `earthengine authenticate`.

### 1.2 App-local datasets (`<app>/global_data/` and app root)

| Path | Contents | Used by |
|---|---|---|
| `global_data/soil_data/` | SoilGrids 2.0 sand %, clay %, bulk density, 0–5 cm, Pakistan box | `soil_utils` |
| `global_data/buildings_pk/PK_{quadkey}.parquet` | Local mirror of Google Open Buildings tiles (tried before the network) | `buildings_utils` |
| `global_data/roads_pk/pak_roads_indexed.gpkg` | HOTOSM Pakistan roads (1,113,962 lines), converted once to GeoPackage for its R-tree index (bbox query 35 s → 0.05 s) | `infra_utils.fetch_local_roads` |
| `hotosm_pak_buildings_polygons_shp/` (app root) | HOTOSM OSM building polygons, used only for height tags (`height`, `building:levels`) | `infra_utils.fetch_osm_building_heights` |
| `global_data/rainfall_idf/` | ERA5-Land (1980–2026) and IMERG V07 (2000–2026) per-cell extreme-rainfall cubes, `manifest.json`, `tehsils.geojson` (578 tehsils); symlink to a sibling task | `idf_utils` |
| `global_data/gtsr/` | GTSR daily-maxima surge 1979–2014 (16,406 stations) + Gumbel parameters (~1.3 GB) | `coastal_utils` |
| `global_data/hydrosheds_basins_pk/` + `hydrosheds_basins_pk_index.json` | HydroSHEDS level-08 basins, 1,131 polygons | `basin_utils` |
| `global_data/glim/glim_pakistan.gpkg` | GLiM lithology (Hartmann & Moosdorf 2012), Pakistan clip | `geomorphology_utils` |
| `global_data/glofas_clim/glofas_mean_2003_2012.npz` | 2003–2012 mean GloFAS `dis24` and `rowe` grids, built by `global_data/build_glofas_climatology.py` (~20 s) | melt-model comparison-cell choice (§9.3) |
| `global_data/storm_tracks_pakistan.json`, `storm_motion_lstm.pt`, `storm_motion_lstm_metrics.json` | 72 IMERG monsoon storm-day tracks (2021–2023) and the trained storm-motion LSTM | `storm_motion_lstm` |
| `Dams data/Barriers.shp`, `Reservoirs.shp` | Global Dam Watch: 101 dams, 86 reservoirs | `dams_utils` |
| `dam_spillway_opening_events.csv` | 10 documented Mangla/Tarbela spillway openings (4 with a numeric outflow) | `dams_utils` |
| `numa_stub/`, `netcdf_local/` | Build/runtime shims for LISFLOOD-FP (no root access for system packages) | `flood_model` |
| `lisflood_patches/cuda_acc_solver.cu` (working copy) | Patched CUDA ACC kernel source (§4.7); re-apply after any LISFLOOD-FP re-extract | build |

### 1.3 Caches (derived, safe to delete; a cache hit never regenerates old output)

| Path | Key | Contents |
|---|---|---|
| `fis_cache/<run_id>/` | hash of AOI geometry + all run parameters + DEM source + `pipeline_signature()` (pipeline version + dam-data fingerprint) | One flood run: inputs, `results/`, `summary.json` (all post-processing persisted), `mapbox_3d.html`, `report.html`/`.pdf` |
| `fis_nbs_cache/`, `fis_protect_cache/`, `fis_coastal_dyn_cache/` | job id | NbS, protection and dynamic-surge runs |
| `fis_aoi3d_cache/`, `fis_area3d_buildings_cache/` | AOI hash | 3D scene inputs |
| `hand_cache/` | AOI geometry + DEM source | HAND rasters (`.pkl`) and discharge library (`.library.json`) |
| `global_dem_cache/` | rounded bbox | Copernicus GLO-30 mosaics (opt-in only) |
| `climate_cache/` | parameters | CMIP6 design-storm and intensification results |
| `melt_cache/<key>/` | AOI geometry + period + parameters + `MODEL_VERSION` (3) + `hydro_model.py` modification time | One melt-model run: inputs, `out/`, `result.json`, `run.log` |
| `fis_uploads/` | — | Uploaded AOI shapefiles/GeoJSON |
| `watch_list.json`, `geocode_cache.json` | — | Monitoring watches; Mapbox geocoder results |

---

## 2. External services

| Endpoint | Provider | Used for | Called from |
|---|---|---|---|
| `https://api.open-meteo.com/v1/forecast` | Open-Meteo | Hourly rainfall forecast (`timezone=auto`; labels are local time, `utc_offset_seconds` returned) | `rainfall_rivers_utils.fetch_rainfall` |
| `https://archive-api.open-meteo.com/v1/archive` | Open-Meteo (ERA5-backed) | 20-year daily precipitation climatology | `pluvial_utils`, `validate_2022_flood.py` |
| `https://flood-api.open-meteo.com/v1/flood` | Open-Meteo (GloFAS) | River discharge context; dam spillway outlook | `rainfall_rivers_utils.fetch_river_discharge`, `dams_utils` |
| `https://marine-api.open-meteo.com/v1/marine` | Open-Meteo Marine | Significant wave height and peak period | `coastal_utils.fetch_real_wave_conditions` |
| `https://overpass-api.de/api/interpreter` | OpenStreetMap | Trees/forest, waterway crossings (roads, hospitals, schools, power are now local) | `overpass_utils.query_overpass` (3 attempts, back-off 3 s / 6 s, retries only on timeout or 5xx) |
| `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/...` | ESA WorldCover 10 m (AWS) | Land cover → Manning's n, damage, NbS | `damage_utils.fetch_lulc_for_bounds` |
| `https://data.source.coop/cholmes/google-open-buildings/...country_iso=PK/PK_{quadkey}.parquet` | Google Open Buildings (source.coop) | Footprints, only when the local tile is missing | `buildings_utils.fetch_buildings_for_aoi` |
| `/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/...` | Copernicus DEM GLO-30 | Elevation outside Pakistan, opt-in only (`use_global_dem`) | `global_dem_utils` |
| Earth Engine `COPERNICUS/S1_GRD`, `GLIMS/current`, `NASA/GPM_L3/IMERG_V07` | Google Earth Engine | Glacier SAR displacement; storm tracks | `glacier_monitoring_utils`, `storm_motion_lstm` |
| `https://api.mapbox.com/mapbox-gl-js/v3.6.0/`, `/geocoding/v5/mapbox.places/` | Mapbox | Basemap, 3D terrain (`mapbox.mapbox-terrain-dem-v1`), search, CSV station geocoding (query suffixed ", Pakistan") | `fis_app`, `mapbox3d_utils`, `rainfall_csv_utils` |
| `https://unosat.org/.../FL20221121PAK_SHP.zip`, HDX flood-depth dataset | UNOSAT / HDX | 2022 observed flood extent, validation only | `validate_2022_flood.py` |
| One-time downloads (now stored locally) | 4TU/figshare (GTSR), ISRIC (SoilGrids), Univ. Hamburg (GLiM) | — | `global_data/fetch_*.py` |

---

## 3. Area of interest, terrain and the run pipeline

### 3.1 AOI input (`polygon_utils.py`, `idf_utils`, `basin_utils`)
- A drawn polygon; an uploaded `.geojson`, `.shp` or `.zip` (a shapefile inside a sub-folder of the zip is found by scanning the entries); a tehsil by name (578 tehsils); or a HydroSHEDS basin (browse by region, nearest to a point, or nearest to a GLOF site).
- `crop_dem_to_polygon` crops the DEM to the polygon's bounding box and sets cells outside the true polygon to NODATA (LISFLOOD-FP only simulates rectangles). Typical polygons fill ~60% of their box.

### 3.2 Run pipeline (`fis_app.execute_fis_run`), stages `dem → buildings → model → postprocess → scene → done`
1. DEM crop; dams and reservoirs burned in (§3.3).
2. In parallel background threads: Google buildings fetch, infrastructure fetch (local files + Overpass trees), ESA WorldCover → `manning.ascii`.
3. Building footprints rasterized onto the DEM as obstacles (+6 m, full footprint, valid cells only); a bare copy `dem_bare.ascii` is kept for 3D.
4. `flood_model.py` subprocess (§4).
5. Post-processing: per-timestep depth/velocity stack (`_read_water_stack`; Vx/Vy de-staggered from the Arakawa C grid; spatially strided when frames × cells > 5,000,000 so the 3D page stays loadable), building submersion and loss, infrastructure hazard. `summary.json` is rewritten with every enrichment.
6. `mapbox_3d.html`: GPU (WebGL2) animated depth with flow-map ripples and flow lines, rain, building/road colour by per-frame risk, view modes (depth, maximum depth, flow speed, flow paths), six map styles, a timeline card with flooded-area/rain chart and live stats, and a click-to-pin probe (depth, velocity, peak, ground). Built from `mapbox3d_style.css`, `mapbox3d_body.html`, `mapbox3d_app.js` (deploy together). A size check logs to `mapbox3d_size_warning.log` above 150 MB.

### 3.3 Dams and reservoirs, burned into every run (`dams_utils.py`)
- Reservoir polygons are lowered by their mean depth (`DEFAULT_RESERVOIR_DEPTH_M = 10` when missing). Dam points are raised by their height as a 60 m-radius patch (`DAM_RADIUS_M`; `DEFAULT_DAM_HEIGHT_M = 15` when missing), because GDW has no as-built alignment.
- Spillway events: cusec × `0.0283168466` → m³/s, added as an inflow at the dam; the 48 h duration is an estimate.
- Dam failure: Froehlich (1995) `Qp = 0.607 · V^0.295 · h^1.24`. Only 23 of 101 dams have a recorded height. 64 get a height estimated from reservoir depth, using the ratio measured on the 22 dams that have both (`HEIGHT_TO_DEPTH_RATIO` low/central/high = 1.14 / 2.30 / 3.17), and are reported as a range. 14 are refused. The formula is fitted to small dams, so Tarbela-class results are order-of-magnitude only.
- Spillway outlook (GloFAS forecast vs the lowest documented spill inflow) is validated only for Tarbela: GloFAS 6,576 m³/s vs documented 6,881 and 5,947 (−4% / +11%). Mangla is refused: GloFAS gives 2.2 m³/s at the dam vs a documented 8,495.

---

## 4. Flood physics engine (`flood_model.py` → LISFLOOD-FP 8.2)

### 4.1 Binaries
- `LISFLOOD_BIN = ~/flood/lisflood-fp-8.2/LISFLOOD-FP/build/lisflood` (double precision, NetCDF off); `build_netcdf/lisflood` for NetCDF (storm-centred) rainfall.
- A single-precision build (`build_single/`) exists but is **not used**: 2.73× faster, but max depth differed by 44% on high-altitude terrain (float32 cancellation between elevation and water surface).

### 4.2 Solver choice
- `choose_solver_for_terrain`: DG2 when mean slope > `DG2_SLOPE_THRESHOLD_PCT = 10` %, otherwise ACC. A boundary inflow forces ACC (DG2's timestep collapses under an injected flux). Domains above `DG2_CUDA_MAX_CELLS = 4,000,000` force ACC; that is the largest size tested clean after the crash fix (§4.7).
- **`FORCE_ACC_ONLY = True` is the current setting**: ACC everywhere, for speed. DG2 costs 10–60× more wall-clock (a measured-scaling estimate of ~5 h for a 5,600 km² Karakoram basin). On terrain above 10% mean slope, ACC depth is divided by `acc_depth_correction_factor = 2.9664 + 0.037 × mean_slope_%`. That factor was fitted to a 7-point ACC-vs-DG2 benchmark (15–200% slope, R² = 0.992, max corrected-depth error 4.9%). It corrects depth magnitude only, not flow paths, and was fitted on one storm and one synthetic ramp. Set `FORCE_ACC_ONLY = False` to return to slope-based DG2.

### 4.3 GPU vs CPU
- Default is the GPU (`cuda` keyword). `SAILAB_FORCE_CPU=1` forces the CPU build; `SAILAB_FORCE_CUDA=1` is a test switch.
- **River, GLOF, snowmelt and dam inflow (QVAR)** run on the GPU. Measured equal to the CPU build when the inlet is on valid terrain: stored volume 1.9672e5 / 1.5746e6 m³ at 1 h / 4 h on both, and full runs agree to the displayed digit. A run falls back to the CPU only if `inflow_segment_touches_nodata()` finds NODATA under an inflow segment, because CUDA zeroes water in NODATA cells and the −9999 m pit drives a blow-up.
- **Constant inflow**: CUDA ACC has no `QFIX4` case (it injects zero), so every constant inflow is written as a flat two-point QVAR series on both builds.
- **Coastal stage (HVAR)** runs on the GPU after the local kernel patch (§4.7). Measured on a Karachi SRTM crop: GPU 41.4370 km² / 4.00 m = CPU 41.4370 km² / 4.00 m (34 s vs 97 s).
- Inflow width is snapped to whole cells, because the solver applies a boundary on whole cells. A 40 m channel on a 29 m grid used to deliver 1.46× the requested discharge.
- The CPU path pins `OMP_NUM_THREADS=1`: every multi-threaded setting measured 6–7× slower, because of a `#pragma omp critical` inside the parallel loop.
- The GPU is not always faster: a 41k-cell inflow-only run took 62 s on GPU vs 29 s on CPU; with rain and outlets it was 105 s vs 180 s.

### 4.4 Timestep and stability
- `initial_tstep`: 5.0 s for rain-only runs; 0.2 s for any river or GLOF inflow; 5.0 s for a coastal-HVAR-only run (tested at 0.2, 2 and 5 s with the same result, 13–17× faster); 1.0 s whenever a run would otherwise start at 5.0 s and the mean slope exceeds 10% (5.0 s produced unphysical depths on steep terrain in a controlled bisection).
- Friction floor `ABS_MIN_FPFRIC = 0.01`. Below `MIN_FPFRIC = 0.02` a numerical-diffusion term `theta = 0.8` is written, based on the lowest friction actually in use, including the per-cell Manning grid.
- NODATA is filled by nearest valid neighbour (not the domain mean) wherever slopes are computed.
- **Known open limitation**: on extreme relief (the 6.6M-cell GB basin, max slope 852%) ACC is still unstable at the reduced step (255 m max depth, −4.8M m³ mass error). DG2 at 116 m gave 0.83 m for the same basin. Treat ACC results on such basins as unreliable.

### 4.5 Inputs written per run
- Rainfall: a triangular design hyetograph peaking at half duration; or an uploaded observed series (`--rain-series-json`; duplicate timestamps merged, keeping the larger rate); or storm-centred / moving NetCDF rainfall (§6.3).
- Infiltration: the user's flat rate × an AMC multiplier (a continuous 0–100 slider, linear between `(0, 1.8)`, `(50, 1.0)` and `(100, 0.25)`), or the SoilGrids area-weighted Ksat (§5.2). It is one flat rate because the CUDA ACC solver has no per-cell infiltration.
- Manning's n: a per-cell `manningfile` from ESA WorldCover (§5.1); `fpfric` is the fallback for uncovered cells.
- Boundaries (`.bci`): coordinates are absolute (the DEM's `xllcorner`/`yllcorner` are added). Inflows are QVAR (triangular hydrograph with `rise_frac` 0.5 for rivers and 0.10 for GLOF, or an arbitrary `series` with its last value held); outlets are `FREE 0.001` on the chosen edges and `CLOSED` elsewhere; coastal boundaries are HVAR stage series.
- Optional: `mint_hk` (off by default; ~8.5% faster, ~0.3% depth difference) and `depththresh` (diagnostic only; raising it made steep-terrain runs worse).

### 4.6 Outputs and corrections
- DEFRA (2006) hazard `HR = d × (v + 1.5)`, banded low ≤ 0.75, moderate ≤ 1.25, significant ≤ 2.0, extreme > 2.0. The GPU build never writes `res.maxHaz`, so it is recomputed from the per-timestep depth and velocity rasters. A negative/NODATA hazard is "outside domain", not "extreme".
- `cumulative_rainfall_input_m3` and `cumulative_river_inflow_m3` are computed analytically from the hyetograph/hydrograph, because the GPU build leaves the `res.mass` rain and Qin columns at 0. In this build the CPU rain column is plain m³ (an earlier ×1000 assumption was wrong and has been removed).
- Mass conservation, measured: closed rain-only synthetic domains at 300 m, 2 km and 6 km conserve mass on both builds (stored/analytic 1.000 on CPU, 0.997–0.998 on GPU).

### 4.7 Local changes to LISFLOOD-FP 8.2
1. Build fixes: C++17; `cbrt` macro restricted to MSVC; removed `cudaDeviceProp` fields replaced with `cudaDeviceGetAttribute`; `sync()` renamed `adaptive_cuda_sync`; NUMA stub; NetCDF off for `build`.
2. `MaxField` constructor called without its `acceleration` argument in `cuda_dg2/fv1/fv2_simulate.cu`, giving an unpadded buffer and an out-of-bounds GPU write (found with compute-sanitizer). Fixed; DG2 now runs at every tested size (4,000,000 cells in 606.5 s).
3. CUDA ACC HVAR gate (`cuda/acc/cuda_acc_solver.cu`, all four edges): the stage was applied only to already-wet cells, so a dry coastal edge received nothing (unpatched binary: 0.0 m, 0 wet cells; CPU: 6.18 km²). It now also applies when stage − DEM > `DepthThresh`, as the CPU build does.

---

## 5. Land cover, soil, damage and loss

### 5.1 Land cover (`damage_utils.py`)
- ESA WorldCover 2021 v200, 11 classes, resampled onto the run grid.
- Manning's n (overland flow; Chow 1959, USACE HEC-RAS, FEMA): Tree cover 0.130, Mangroves 0.140, Shrubland 0.070, Cropland 0.037, Grassland 0.035, Herbaceous wetland 0.045, Built-up 0.045, Snow/ice 0.030, Moss/lichen 0.030, Bare/sparse 0.025, Permanent water 0.025; clamped to ≥ 0.01.
- Infiltration by class (mm/hr; USDA/NRCS, Chow–Maidment–Mays): Tree 75, Mangroves 60, Shrubland 40, Grassland 20, Cropland 12, Built-up 8, Bare 5, Moss/lichen 10, Wetland 2, Snow/ice 2, Water 0. Used by NbS scenarios.
- Depth–damage: Huizinga et al. (2017, JRC) Asia curves per class, linearly interpolated (Built-up: 0 m → 0, 0.5 m → 0.30, 1 m → 0.45 … 6 m → 0.90).
- Damage statistics count only cells inside the true AOI polygon (`valid_mask`).
- Hotspots: Getis-Ord Gi* (`esda`/`libpysal`), queen contiguity, 99 permutations; a hotspot needs Z > 1.96 and p < 0.05.

### 5.2 Soil (`soil_utils.py`)
- SoilGrids sand/clay → USDA texture class → Rawls, Brakensiek & Miller (1983) Ksat (mm/hr). Soils with sand ≥ 45% and clay ≥ 35% used to fall through to "loam" (11× too high); they now return "sandy clay".
- Collapsed to one area-weighted mean rate per AOI; an optional Green-Ampt average rate solves the implicit equation for cumulative infiltration F.

### 5.3 Buildings and infrastructure (`buildings_utils.py`, `infra_utils.py`)
- Footprints: Google Open Buildings (local tiles first). Height: OSM `height`, else `building:levels × 3 m`, else 6 m. The OSM height lookup uses a spatial index (`STRtree`); this took a 450,000-building AOI from hours to 3.8 s.
- Building severity band from the Huizinga Built-up curve at the footprint centroid's peak depth: minor ≤ 0.30, moderate ≤ 0.55, major ≤ 0.72, severe above (green is reserved for dry). `submersion_fraction = depth / height` is still reported.
- Infrastructure categories: roads (local HOTOSM), hospitals and schools (local), power lines (local), railway lines (local), strategic assets (airports, railway stations, sea ports; local), trees/forest (Overpass). Each feature is sampled at its centroid against the run's depth and hazard and banded with the DEFRA bands.

### 5.4 Monetary loss (`loss_utils.py`, `aal_utils.py`)
- Huizinga et al. (2017) Table 3-21, Asia: residential 111 EUR/m², agriculture 0.03 EUR/m² (2010 prices; Pakistan has no country value). Converted at `EUR_TO_PKR_RATE = 322.5`, a current exchange rate applied to 2010 prices, not inflation-adjusted.
- Building loss = footprint area × storeys (height / 3 m) × 111 EUR/m² × damage fraction; contents = 50% of structure (FEMA/HAZUS). Agricultural loss = cropland area × damage fraction × 0.03 EUR/m². Natural land cover is not priced, and built-up land is excluded to avoid double counting.
- Average Annual Loss: trapezoidal integration of loss against exceedance probability p = 1/T, with a constant tail beyond the rarest return period and a linear fall to zero below the most frequent. It refuses if any return period lacks a loss figure.

---

## 6. Rainfall and storms

### 6.1 Forecast and monitoring (`rainfall_rivers_utils.py`, `monitoring_utils.py`)
- Peak = the maximum future hourly rate; duration = the contiguous hours above 0.1 mm/hr around it. "Now" is computed in the forecast's own local time (`utc_offset_seconds`); comparing against UTC used to shift the boundary by 5 h.
- Watches are checked every 600 s. A threshold crossing starts a Tier 3 run and sends an optional webhook (Slack `text` + Discord `content`; a report link only when `PUBLIC_BASE_URL` is set).

### 6.2 Design storms
- CMIP6 (`climate_utils.py`): annual-maximum daily rainfall 1985–2014 → Gumbel fit → return-period depth, median across up to 8 of 34 models. Intensification factor = the future Gumbel value (2036–2065 or 2071–2100) / the historical one. Return periods 2, 5, 10, 25, 50, 100 and 200 years.
- Observed IDF (`idf_utils.py`): ERA5-Land 1–24 h and IMERG 30–180 min per-cell curves, IMERG preferred where both exist (duplicates removed), log-log interpolation clamped to the curve's range. The default year is the most recent complete year (2025; 2026 is partial). Also a national ranking of individual cells by RX1day, RX5day, R95p, R99p, SDII, R20mm, PRCPTOT, peak hourly, Theil-Sen trend or peak 30-min.
- Uploaded rain-gauge CSV (`rainfall_csv_utils.py`): one series per station; mm per interval → mm/hr using that interval's length; negatives clamped to 0. With no coordinates and more than one station, the user picks the station, because geocoding collapses Islamabad sectors to one point.

### 6.3 Storm shape (`storm_utils.py`, `storm_motion_lstm.py`)
- Areal Reduction Factor (Keers & Wescott 1977 / FSR-FEH) `ARF = 1 − b·D^−a`, five area bands, 1.0 below 1 km², clamped to (0.05, 1].
- Storm centring `w = exp(−r/L)` normalised to a mean of 1 over the AOI, with `L = √(area/π)` by default; optionally a moving centre. Written as NetCDF dynamic rain.
- Storm-motion LSTM: a 6-step window of 30-min displacements, 32 hidden units, trained on 72 IMERG storm-days with a day-level split. Needs Earth Engine, which is currently unavailable (§1.1).

### 6.4 Pluvial risk tier (`pluvial_utils.py`)
- A 20-year daily climatology at the AOI point gives p90/p95/p99; tiers are watch, warning and severe. Shown alongside, not mixed with, the observed IDF reference (RX1day, peak hourly, R95p/R99p seasonal totals, trend). The resolution ceiling is ~10 km hourly; no radar product covers Pakistan.

---

## 7. Instant flood preview — Tier 2 (`hand_utils.py`)
- pysheds fill → D8 → accumulation; streams where accumulation ≥ 500 cells; HAND per AOI (cached).
- Channel width `= max(0.60 × A_km²^0.50, 0.5)` (Leopold & Maddock 1953); stage from Manning's equation for a trapezoidal channel (side slope 1.5, n = 0.035), solved by bisection; a cell floods where HAND < stage.
- Discharge library at 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2500 and 5000 m³/s, interpolated log-log in the browser for the slider.
- Accepts a discharge from manual entry, a GLOF estimate or the melt model; no siting needed.

---

## 8. Rivers, GLOF, coastal, sediment, geomorphology

### 8.1 Rivers (`rainfall_rivers_utils.py`, `channel_utils.py`)
- OSM waterway crossings → nearest edge and position, snapped to the nearest valid edge cell. Channel widths are documented estimates: river 40 m, stream 8 m, canal 15 m, other 20 m.
- Optional channel conditioning carves a channel along the buffered waterway using the Tier 2 width/depth relations. It approximates the sub-grid channel solver, which the CUDA build lacks.

### 8.2 GLOF (`glof_utils.py`)
- Popov (1991) `Q = 0.0048 · V^0.896`; Huggel et al. (2002) volume from area `V = 0.1217 · A^1.4129` when no volume exists; Froehlich (1995) `Q = 0.607 · V^0.295 · h^1.24` when a breach height is known (also used for landslide-dam triggers).
- Hydrograph peak at 10% of the event (`GLOF_RISE_FRAC`), default duration 3 h.
- Curated Shishper 2022 event: 38,987,000 m³ and 3,737 m³/s, reported values (Popov over-estimates this event ~8×).
- Any HydroLAKES lake, by id or by distance from the AOI. Results link to Tier 2 and to the nearest HydroSHEDS basin.

### 8.3 Coastal (`coastal_utils.py`)
- Static: Khan et al. (2020) Karachi total water level at 50/100/1000 yr = 3.73/3.76/3.92 m (log-interpolated), sea-level rise 3.6 mm/yr, mean tidal range 2.3 m. Bathtub with 4-neighbour connectivity, seeded from boundary cells below the level or cells ≤ 0 m.
- Wave run-up (Stockdon et al. 2006, opt-in): `R2% = 1.1·(0.35·β·√(H₀L₀) + √(H₀L₀·(0.563β² + 0.004))/2)`, `L₀ = gT²/2π`; H₀ and T from Open-Meteo Marine; β from DEM transects, or 0.03 as fallback. Measured on Karachi: 0.565 m run-up on a 3.76 m still-water level.
- Animated preview: the static method at 12 levels along a GTSR-shaped series (no routing between frames; under 1 s).
- Dynamic: a GTSR daily-maxima episode at the nearest station, rescaled to an anchor (the static total water level, or the station's recorded peak, optionally + 2.3 m tide). Applied as HVAR on the lowest edge that has land; runs on the GPU (§4.3). Compound mode adds rain in the same run.

### 8.4 Sediment (`sediment_utils.py`)
- Bed shear `τ = ρ g n² V² / h^(1/3)`; Shields `θ = τ / ((ρs − ρw) g d50)`, θc = 0.047; Meyer-Peter–Müller `qb = 8 (θ − θc)^1.5 √((s−1) g d50³)`; d50 from the SoilGrids texture. A post-processing indicator only. Refused when the depth stack was spatially strided.

### 8.5 Geomorphology (`geomorphology_utils.py`)
- GLiM lithology erodibility (1–5) × DEM slope (breaks at 15% and 35%) → negligible / low / moderate / high.
- ML landslide susceptibility (KPK/GB/AJK): class shares inside the AOI, with a verdict only when at least 50% of the AOI is covered. The simple rating can disagree with the ML map (Hunza: "low" vs "elevated"); prefer the ML map where it exists.
- Valley-depth and confinement statistics from the national rasters.

---

## 9. Snow and glacier melt

### 9.1 Single-day and short forecasts (`srm_utils.py`)
- Snowmelt Runoff Model style (Martinec & Rango 1986): DEM elevation bands, temperature lapsed at 6.5 °C/km, degree-day factor 4.5 mm °C⁻¹ day⁻¹, critical temperature 0 °C. Single day or a multi-day forecast hydrograph, usable as a time-varying inflow.

### 9.2 Distributed melt model (`hydro_model.py`, driven by `melt_hydrograph_utils.py`)
- Daily, per pixel: snow (degree-day with a sub-daily temperature spread, rain/snow transition 0–2 °C, refreeze, liquid water holding), glacier ice limited by remaining thickness, soil, groundwater, fast runoff, lakes with weir outflow and rapid release, and D8 routing with Manning travel time.
- Forcing: ERA5-Land hourly t2m and tp, clipped to the basin + 0.3°. Temperature is lapsed to each pixel from its ERA5 cell's mean DEM height (−6.5 °C/km); `temp_bias_c` adds a uniform offset (default 0).
- Basin preparation: SRTM reprojected to the basin's UTM zone at max(30 m, area/40,000 pixels), typically ~260–270 m; pysheds fill and D8; PMD glacier and lake polygons.
- **Ice thickness and debris (added 8 Oct 2026).** IceBoost v2.0 thickness and the debris factor are averaged onto the model grid over ice-covered source pixels only (thickness > 0), so a half-ice cell gets the thickness of its ice. PMD glacier pixels where IceBoost models no ice are treated as ice-free (they previously got a flat 50 m). Ice melt = `ddf_ice × PDD × terrain factor × debris factor`, capped by the remaining ice.
  - Measured coverage. Hunza 4080566900: 1,016 km² of IceBoost ice (37% of the basin), mean 175 m; PMD polygons cover 44%; 1,439 model cells are PMD-only. Basin 4080575700: 577 km² (20%), mean 81 m; PMD 28%; 1,331 PMD-only cells.
  - The debris factor differs from 1 on only 0.5–1.3% of ice pixels in both basins (mean 0.994–1.000). **It has almost no effect on these basins.** Real debris cover in the Karakoram is far larger, so this product under-represents debris.
  - Thickness changes little in a one-year run, because most ice is far thicker than one season's melt. The change that mattered was treating PMD-only pixels as ice-free: they carried ~15% of all ice melt.
- Outputs: daily outlet discharge (sum of exit cells), a per-pixel summary, and glacier mass balance (m w.e./yr over glacier pixels).

### 9.3 Checking against GloFAS (`glofas_check`, `calibrate_against_glofas`)
- GloFAS is a model, not gauge data, so agreement is a plausibility check.
- The comparison cell is chosen by **water balance**: the cell within ±3 cells of the outlet whose 2003–2012 mean `dis24` matches the basin's own GloFAS runoff (`rowe` × area) within 1.5×; otherwise no comparison is made. The old rule (highest-flow cell inside the polygon) picked a neighbouring river for Hunza: 78 vs 20 m³/s.
- Skill on the melt season (Apr–Sep): KGE, NSE, bias, r. A warning appears when |bias| > 40% or KGE < 0.3.
- Calibration grid: temperature offset −3…+1 °C × precipitation factor 0.5…4 × degree-day pairs (4/8, 7/16) = 50 runs on a calibration year; then the winner and the defaults are run on a hold-out year. A trial is rejected if glacier mass balance is below −1.5 m w.e./yr (the regional geodetic range is −0.1 to −0.5; Hugonnet et al. 2021). `transferable` requires hold-out KGE ≥ 0.3, |bias| ≤ 40% and a plausible mass balance.
- Measured with the new ice inputs (PMD-only cells ice-free), calibration year Oct 2024–Oct 2025, hold-out Oct 2023–Oct 2024:

| Basin | Setting | Calibration year | Hold-out year | Glacier balance (m w.e./yr) | Verdict |
|---|---|---|---|---|---|
| Hunza 4080566900 | defaults | KGE 0.86, bias +9% | KGE 0.83, bias +13% | +0.04 | – |
| Hunza 4080566900 | T −2 °C, precip ×0.5, DDF 7/16 | KGE 0.95, bias −2% | KGE 0.87, bias +3% | −0.40 | transferable (50/50 trials plausible) |
| 4080575700 | best of grid (T −3 °C, precip ×2, DDF 4/8) | KGE −1.50, bias +192% | KGE −1.16, bias +157% | −0.69 | not transferable (11/50 plausible) |

- Basin 4080575700 still overshoots GloFAS by 2–3× under every setting, even with plausible glacier balance; the cause is unresolved (glacier outlines, modelled accumulation, or GloFAS's lack of glacier dynamics). It needs a gauge. GloFAS's own water balance there is consistent (525 mm/yr generated runoff vs 48 m³/s at the chosen cell), so the comparison cell is not the problem.

---

## 10. Scenarios, comparisons and exports

- **NbS** (`nbs_utils.py`): reforestation, grassland restoration or wetland restoration; "active" (full conversion) or "passive" (one step along an ecological order). Re-derives per-cell Manning's n and the area-mean infiltration, re-runs the physics, and compares cell by cell (protected / displaced / mean depth change). Auto-recommend evaluates all 6 combinations and ranks them by % of building loss avoided (a cell-based score when there are no buildings).
- **Protection** (`protection_utils.py`, `protection_optimizer.py`): dam 3.0 m / 8 m half-width, barrage 2.5 / 12, canal −2.0 / 6, drainage pipe −1.0 / 1.5, plus a rainwater retention basin. Drawn lines are buffered (square caps for raised structures, flat caps for lowered ones) and burned onto a copy of the conditioned DEM. Auto-placement uses mystic `diffev2` with 7 variables per structure; every trial is a full LISFLOOD-FP run; the objective is `−loss_reduction_% + 0.5 × displacement_%`; failures are logged to `optimizer_errors.log`.
- **Compare two runs**: the same grid is required; reports only-A / only-B / both wet, mean depth change and building-loss difference, with an HTML/PDF report (`REPORT_CSS`). **Batch report**: a list of runs with a trend chart. Coastal dynamic jobs are supported in both.
- **Exports**: run `report.html`/`.pdf` (WeasyPrint), Export PNG, and Export Video (browser WebM → `/export-video` → H.264 MP4 via the bundled `imageio-ffmpeg`, with dimensions rounded to even).

---

## 11. Validation and accuracy record

| Check | Result |
|---|---|
| Bahawalpur, SRTM vs FABDEM, same polygon | 1.436 vs 1.449 km² flooded |
| v5.9 CPU vs 8.2 CPU vs 8.2 GPU, Bahawalpur 104 km² | v5.9 = 8.2 CPU bit-identical; GPU 2.816 vs CPU 2.818 m max depth; GPU ~10–11× faster |
| GPU vs CPU river inflow (valid inlet, Lahore DEM) | identical to the displayed digit (§4.3) |
| GPU vs CPU coastal HVAR (Karachi) | 41.4370 km² / 4.00 m on both |
| Mass conservation, closed synthetic domains | stored/analytic 1.000 (CPU), 0.997–0.998 (GPU) |
| 2022 Sindh flood vs UNOSAT extent, 48 h ERA5 storm (253.5 mm) | CSI 0.659, POD 0.669, FAR 0.021 (73.5 vs 107.7 km²) |
| same, 10-day window (424.6 mm) | CSI 0.694, POD 0.705, FAR 0.022 (77.6 vs 107.7 km²) |
| same, 30-day window | not completed (no report produced) |
| Melt model vs GloFAS | §9.3 |

Open questions: CPU and GPU max depth differ at a few sink cells on the 2022 AOI (16.3 vs 8.95 m) while wet area matches, so compare wet area / CSI, not max depth; ACC instability on extreme relief (§4.4); the melt-model disagreement on basin 4080575700 (§9.3).

---

## 12. Tests and operations

- Fast suite: `~/flood/venv/bin/python -m pytest tests -q -m "not slow"` (348 tests at the last run, all passing). Slow tests include real LISFLOOD-FP runs (GPU-vs-CPU inflow and HVAR, closed-domain mass balance) and live HTTP routes.
- Front-end guards: `tests/test_page_js_syntax.py` (`node --check` on the rendered page script) and `tests/test_frontend_smoke.py` (headless Chromium: map canvas present, no console errors).
- Restart the server after any backend change: find the PID with `ss -ltnp | grep :8000` and `kill` it. Never `pkill -f` a pattern that appears in your own command line.
- The working copy (`/mnt/external-ssd/DGX SHARED/Tasks/Reselli_AI/flood/`) and the live copy (`~/flood/`) are separate; copy changed files across and confirm with `cmp`.


## Coastal data layers added (Oct 2026)

| Dataset | Local path | Use |
|---|---|---|
| GTSM-ERA5-E v2 return levels, 42 Pakistan coastal stations (Zenodo 10.5281/zenodo.14671593) | `global_data/coastal/pakistan_coast_all_return_levels.csv` | Selectable local still-water level (2-100 yr, log-T interpolation); modelled, coarse near the coast, datum not reconciled with SRTM |
| 30 m sea mask (EPSG:32642) | `global_data/coastal/pakistan_sea_mask.tif` | Seeds the connected-bathtub flood; distance to sea for the susceptibility index |
| GMW 2025 mangrove extent; 0-1 vegetation buffer | `global_data/coastal/mangroves_2025_dem_aligned.tif`, `vegetation_buffer_0to1.tif` | Mangrove area/flooded area; susceptibility factor (no hydraulic attenuation) |

Coastal susceptibility index (0-1, 5 classes at 0.2/0.4/0.6/0.8): elevation above mean high water (MSL + half the 2.3 m Karachi tidal range; weight 0.25, ramp 10 m -> 0), distance to sea (0.10, 20 km -> 0), slope (0.10, 5 deg -> 0), mangrove buffer (0.07, 1 -> 0), renormalised over the factors present. Weights are expert defaults from the supplied model, not fitted.


## Third edition additions (31 October 2026)

The full reference is `SAILAB_Technical_Reference.pdf` (third edition, 64 pages); this file stays the compact index. Added since the 8 October rebuild:
- **Tide and datum (Ch. 19.10).** `global_data/tide/karachi_hourly_h147b.csv` (hourly, 2007-2016); Khan et al. return levels are above the gauge zero, MSL is 1.849 m above it, so 100-yr = 1.91 m above MSL (not 3.76 m). `tide_utils.py` 23-constituent harmonic fit (unseen-years RMSE 0.118 m, r 0.987); `build_tide_surge_series`.
- **Vector data (Ch. 22.6-22.8).** `pg_vector_utils.py` (PostGIS first, files fall back; `SAILAB_PG_URL`), `islamabad_layers_utils.py` (Islamabad GeoPackage: shelters, police, water facilities, fuel/chemical, governance, industry), drivable roads view for boxes inside Islamabad, `road_detail` results.
- **Entry points and operations (Ch. 2.4, 40).** `landing.html` at `/`, app at `/app`; `SAILAB_ACCESS_TOKEN`; `ops_agents.py` (health watchdog, forecast validation, run QA; `SAILAB_ALERT_WEBHOOK`); `cache_cleanup.py`; observed-flood scoring route; scene rebuild route; client plugin and GitHub publishing.
- **Evidence (Ch. 33.1, `model_comparison/`).** Multi-model study: SAILAB mean CSI 0.504 vs ANUGA 0.503, SFINCS 0.500, TELEMAC-2D 0.497, flood-everything 0.500. Parity, not superiority. The earlier Sindh CSI 0.659 used a 98%-flooded box and is not evidence of skill.
- **LISFLOOD-FP 8.3 (Ch. 4.3).** Built, identical to 8.2, no speed gain on our cases, not adopted.
- Earth Engine was re-authenticated on 13 Oct 2026 (the "revoked" note in section 1.1 is superseded). Fast tests: 420.
