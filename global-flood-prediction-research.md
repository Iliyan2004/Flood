# Building a Global, High-Resolution Flood Prediction System
### Complete research brief: data, models, compute, and a realistic path to a genuinely differentiated product

*Prepared September 2026*

---

## 0. Read this first — the honest reality check

You asked for something "no one has achieved yet — no product has delivered." I researched that claim directly rather than assuming it, and it's **true, but not for lack of trying** — Google, ECMWF/Copernicus, Fathom, JBA, NVIDIA, and academic groups with supercomputer access have all pushed hard on this and hit the same wall. Understanding *why* is the single most important input to designing your system, because it tells you where the real, achievable differentiation is.

**The core finding: a single global, continuously-updated, physically-simulated flood model at 1–10 m resolution is not computationally feasible with any existing algorithm or hardware — not for you, and not for Google.**

The arithmetic (worked through in §4): global land area is ~148 million km². At 10 m resolution that's ~1.5 trillion grid cells. The best published GPU hydrodynamic benchmark we found — a multi-GPU shallow-water solver run over Berlin (892 km², 8×A100 GPUs) — needed 5.5 hours to simulate 48 hours at 2 m resolution, and its authors state that 1 m resolution needs *more GPUs than they had*. Scaling that linearly (optimistically — real scaling is worse than linear) to the whole planet at 10 m implies on the order of **tens of thousands of GPU-years for a single 48-hour scenario**, not once, but repeated every few hours as a probabilistic ensemble. China's newest exascale climate system (23,000+ nodes) needed 512 nodes for 7 days just to run one *regional*, non-ensembled 1 **km** simulation — a resolution 100–1,000× coarser than what "very high resolution" implies for flooding. Nobody — not Google, not ECMWF, not a national supercomputing center — has attempted, let alone delivered, true global street-level physics.

This is why **every existing product uses a hybrid workaround**, and yours should too:

| Layer | What it does | How today's leaders make it cheap |
|---|---|---|
| Global "how much water" | Rainfall → river discharge, everywhere | Small, cheap ML models (LSTMs), not physics |
| Local "where does it go" | Discharge → flood extent/depth | Precomputed terrain-index maps (HAND), computed once, looked up instantly |
| True high-res physics | Full 2D shallow-water simulation | Reserved for individual cities/events on GPUs, never run globally at once |

Nobody has unified fluvial + pluvial + coastal + flash flooding in one real-time, globally-consistent, high-resolution system. Nobody has solved flash-flood/pluvial forecasting globally at all — the finest global product (Google's 2025 flash-flood layer) is a coarse 20×20 km grid, because there is **no global radar network** (the input data itself doesn't exist at the fidelity needed — this is a data problem, not just a compute problem). And every system, without exception, is measurably worse in Africa, South Asia, and other data-sparse, high-vulnerability regions than in the US/Europe. **That gap — equitable accuracy in under-instrumented regions, and any credible global pluvial/flash-flood layer — is where real, currently-unclaimed differentiation exists.** "Beat everyone everywhere at 1 m" is not a realistic target for any team; "be the first to do X well, where X is a genuine unsolved gap" is.

The rest of this document gives you the complete data catalog, modeling architecture, and compute plan to build a serious system — organized so you can see exactly which pieces are tractable solo/small-team, and which require Google-scale resources (so you don't burn a year discovering that the hard way).

---

## 1. Competitive landscape — what exists today, and exactly where it stops

| System | Perils covered | Resolution | Lead time | Coverage | Method | Biggest gap |
|---|---|---|---|---|---|---|
| **Google Flood Hub** | Riverine only | Reach-level HAND inundation maps; discharge per sub-basin | 7 days | 80–100 countries, ~460–700M people | Encoder/decoder LSTM (5,680 global gauges) → HAND inundation | No coastal/pluvial (until 2025); weaker on small/flashy basins and in Africa; skill drops sharply for rare/extreme events |
| **Google "Groundsource" flash-flood layer** (2025) | Urban pluvial/flash | 20×20 km grid | 24 hr | Global urban areas >100 people/km² | LSTM trained on news-mined ground truth (no gauges exist) | Extremely coarse; rural areas excluded; ground truth is noisy, worst exactly where risk is highest |
| **Copernicus GloFAS/EFAS** (ECMWF) | Riverine | ~5 km hydrology grid; ~90 m hazard maps | Up to 30 days + seasonal | Global, 90°N–60°S | LISFLOOD model + ECMWF ensemble NWP (AIFS ML model integrated since Sept 2025) | Simplified reservoir ops; systematic bias in Africa/arid regions; no pluvial/coastal/urban capability |
| **Fathom Global Flood Map** | Fluvial + pluvial + coastal (unified) | 30 m global, 10 m in some regions | **Static hazard maps, not a forecast** | All countries | Hydrodynamic simulation on FABDEM/FathomDEM terrain, defended & undefended scenarios | It's a hazard *map* (return-period based), not a real-time forecasting system |
| **JBA Risk Management** | Flood (catastrophe model) | 30 m | Stochastic event set, not real-time | Global | Probabilistic cat modeling for (re)insurance | Same as Fathom — hazard/loss modeling, not live forecasting |
| **WRI Aqueduct Floods** | Riverine + coastal | Coarse (sub-km–km) | Static | Global, free | PCR-GLOBWB + DIVA | Free but low-resolution — a screening tool, not engineering-grade |
| **NOAA FLASH** | Flash flood | 1 km / 5 min | ~0 (nowcast, not forecast) | **US only** | CREST model + NEXRAD radar QPE | Depends entirely on the US radar network — the exact thing the rest of the world lacks |
| **ECMWF Destination Earth pilots** | Compound/flood | Down to 500 m | Short–medium | Netherlands, Italy + 2 pilot countries | ML-blended nowcast + national hydrologic models | Proves high-res is possible per-country, not globalized |
| **Academic GPU urban models** (e.g. Berlin RIM2D study) | Pluvial/urban | 1–10 m | Hours (event-based) | Single city | Multi-GPU shallow-water solver | Proof that fine resolution works locally; explicitly does not scale to "everywhere at once" |

**The pattern to notice:** resolution and real-time-ness trade off against global coverage in every single product that exists. Nobody sits in the top-right corner of "global + real-time + high-resolution + all flood types." That corner is empty because it's currently not fully fillable — but you can get much closer to it than anyone has by being deliberate about *which* corner-adjacent niche you target first.

---

## 2. Proposed system architecture — the only approach that's actually buildable

A tiered, hybrid pipeline, mirroring what the state of the art has converged on (independently, across Google, Deltares, ECMWF, and academia), but designed to be more unified and more equitable than any single existing product:

```
┌─────────────────────────────────────────────────────────────────┐
│ TIER 0 — Static base layers (computed once, refreshed rarely)   │
│  • Global bare-earth DEM (hydrologically conditioned)            │
│  • River network, basins, drainage direction                     │
│  • HAND (Height Above Nearest Drainage) index per reach           │
│  • Land cover, soil infiltration parameters, imperviousness       │
│  • Building footprints, population, exposure layers                │
│  • Precomputed inundation-extent library per discharge level      │
│    (many discharge levels × HAND lookup = instant flood maps)    │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│ TIER 1 — Global "how much water" (cheap ML, runs continuously)  │
│  • LSTM/GNN rainfall→runoff model, trained on global gauges       │
│  • Ingests: NWP ensemble forecasts, satellite precip, reanalysis  │
│  • Outputs: probabilistic discharge forecast per river reach       │
│  • Coastal equivalent: storm surge + tide + sea-level-rise model  │
│    (GTSM-style unstructured mesh, fine only at the coastline)      │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│ TIER 2 — Discharge → local inundation (near-instant, everywhere) │
│  • HAND/terrain-index lookup for fluvial, all reaches globally     │
│  • Subgrid-corrected coarse-grid inundation (SFINCS-style trick:  │
│    fine topography baked into a coarser computational grid)        │
│  • ML super-resolution/emulator trained on hydrodynamic-model      │
│    output to sharpen HAND/subgrid results toward true fine detail │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│ TIER 3 — True high-res physics (selective, GPU, on-demand)       │
│  • Full 2D shallow-water hydrodynamics at 1–10 m                  │
│  • Reserved for: high-value cities, active/imminent events,        │
│    validation/training-data generation for Tier 2's ML emulator   │
│  • Tools: LISFLOOD-FP 8.x (GPU), SFINCS, HEC-RAS 2D (GPU), or a    │
│    from-scratch differentiable solver (à la "Inunda") if you want  │
│    ML-friendly gradients for training Tier 2                       │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│ PLUVIAL/FLASH-FLOOD LAYER (the genuinely open research problem)  │
│  • Where radar exists: near-instant nowcasting (CREST/FLASH-style)│
│  • Where only satellite precip exists (~most of the world):        │
│    ML nowcasting model bias-corrected against sparse gauges/SAR,   │
│    honest uncertainty bounds rather than false precision            │
│  • This is your best shot at a genuinely novel contribution        │
└─────────────────────────────────────────────────────────────────┘
```

The key insight this diagram encodes: **you don't need physics everywhere — you need physics once, offline, to teach an ML model what physics looks like, and then run the cheap ML model everywhere in real time.** That's the only architecture pattern in the entire research base that scales to "global."

---

## 3. Complete data catalog

Everything below was verified against current (2025–2026) sources rather than assumed from memory. For each: resolution, coverage, access, and license/cost.

### 3.1 Terrain / elevation (DEM)

| Dataset | Resolution | Coverage | Type | Access | License/cost |
|---|---|---|---|---|---|
| **Copernicus DEM GLO-30 / GLO-90** | 30 m / 90 m | Near-global | Surface model (incl. buildings/canopy) | AWS S3 open, STAC API | Free |
| **FABDEM** (bare-earth correction of Copernicus DEM) | 30 m | Global | Bare-earth, ML-corrected | Univ. of Bristol repo, Hugging Face | Free non-commercial (CC BY-NC-SA); commercial license via Fathom |
| **MERIT Hydro** | 90 m | 90°N–60°S | Hydrologically conditioned (flow direction/accumulation, HAND layers included) | Registration → Dropbox | Free (CC BY-NC or ODbL) |
| **NASADEM** | 30 m | ~60°N–56°S | Surface model | NASA Earthdata | Free |
| **ALOS AW3D30** | 30 m | Global | Optical stereo DSM | JAXA / Earth Engine | Free |
| **Airbus WorldDEM Neo** | **5 m** | Global (pole-to-pole) | Commercial DSM | Airbus OneAtlas / resellers | Commercial, quote-based (12 m tier from ~$6.25/km²) |
| National LiDAR (USGS 3DEP, UK EA, EU national programs) | 0.5–1 m | **Country-by-country only** | Bare-earth | Mostly free/open per country | Free where available |

**Implication:** a truly global sub-10 m bare-earth DEM does not exist as an open dataset. Your realistic global baseline is **FABDEM/MERIT Hydro at 30 m**; going finer globally means either paying for WorldDEM Neo (5 m, commercial) or doing what Fathom does — blending in national LiDAR wherever it's free (US, UK, much of the EU, parts of Japan/Australia) and falling back to 30 m elsewhere. This single fact — DEM resolution — is the hard ceiling under almost every other resolution claim you'll see in this space.

### 3.2 Bathymetry / coastal

| Dataset | Resolution | Notes |
|---|---|---|
| **GEBCO_2025** | ~450 m | Global bathymetry; <30% is directly surveyed, rest interpolated — real accuracy caveat |
| **GLOSS/PSMSL tide gauges** | ~300 core stations, ~2,000 total | Monthly to sub-hourly; no single API, per-station downloads |
| **Climate Central CoastalDEM v3** | Coastal correction layer | Gated/request-only access, not open bulk |
| **GTSM storm surge model** | 25 km offshore → 2.5 km at coastline | Deltares; unstructured mesh — resolution concentrated where it matters |

### 3.3 Precipitation / meteorological

| Dataset | Resolution | Latency/cadence | Coverage | Access |
|---|---|---|---|---|
| **GPM IMERG** | ~10 km | 30 min native; 4 hr (early), 14 hr (late), 3.5 mo (final, gauge-corrected) | Global | Free, NASA GES DISC / AWS |
| **ERA5 / ERA5-Land** | 31 km / 9 km | Hourly, ~5 day latency | Global | Free, Copernicus CDS |
| **CHIRPS** | 5.5 km | Daily, ~2 day latency | 50°S–50°N only | Free |
| **NOAA MRMS (ground radar)** | ~1 km, 2 min | Near-instant | **CONUS only** | Free, AWS |
| **ECMWF IFS ENS** | 25 km, 51-member ensemble | 15-day lead, 4x/day | Global | Free (open data since 2023) |
| **ECMWF AIFS ENS** (ML model, operational since July 2025) | 28 km, 51-member | 15-day lead, every 6 hr | Global | Free, CC BY 4.0 |
| **Google DeepMind WeatherNext 2** (GraphCast/GenCast successor) | ~30 km | 15-day ensembles in ~8 min on TPU | Global | Free, open weights (Apache 2.0) |
| **Huawei Pangu-Weather** | 25 km, deterministic | — | Global | Open-sourced |

**The critical gap:** there is **no global equivalent of ground radar.** MRMS-class 1 km/2-minute precipitation — the input that makes NOAA's FLASH system possible — exists only in a handful of radar-rich countries. Everywhere else you are stuck with ~10 km/30 min satellite precipitation, which structurally cannot resolve the short, intense, localized convective cells that cause flash floods. **No amount of downstream modeling sophistication fixes an upstream data resolution problem this fundamental** — this is the single biggest reason nobody has solved global flash-flood forecasting, and it constrains what you can promise, honestly, outside radar-covered regions.

### 3.4 River discharge / hydrology observation

| Dataset | Coverage | Notes |
|---|---|---|
| **GRDC** | 10,836 gauge stations, ~44–54 countries | The core global calibration/validation dataset; registration required, free |
| **HydroSHEDS / HydroATLAS / HydroBASINS** | Global (v1, ~90 m–1 km derived layers); v2 (30 m) is Americas-only so far | Free, CC BY 4.0 — river network, basin polygons, ~280 hydro-environmental attributes |
| **SWOT satellite altimetry** | Rivers >50–100 m wide, lakes >250 m² | 21-day repeat orbit, irregular revisit — fills ungauged basins but not continuous time series at a point |

### 3.5 Land cover, soil, imperviousness

| Dataset | Resolution | Notes |
|---|---|---|
| **ESA WorldCover** | 10 m | Global, but only 2020/2021 editions — appears to have stalled |
| **Esri/Impact Observatory Annual LULC** | 10 m | Global, annual through ~2025 — the better-maintained option |
| **SoilGrids 2.0** | 250 m | Global; includes field-capacity/wilting-point/saturation — directly usable for infiltration modeling |
| **GISA-10m** (impervious surface) | 10 m | Best current global option for urban pluvial modeling |

### 3.6 Flood extent validation / ground truth (this is your training and accuracy-checking data)

| Dataset | Notes |
|---|---|
| **Copernicus Sentinel-1 Global Flood Monitoring (GFM)** | Continuous, automatic SAR-based global flood detection (not just activation-based) — the best systematic validation source. ~6–12 day revisit per location. Free. |
| **Copernicus EMS Rapid Mapping** | Activation-based, only for declared major disasters; free |
| **NASA MODIS/VIIRS Near-Real-Time Flood** | 250 m, daily composites, 23-year archive; free |
| **Dartmouth Flood Observatory / Cloud to Street Global Flood Database** | Global event catalog since 1985, MODIS-derived footprints, free (Earth Engine) |
| **EM-DAT** | >5,750 flood events globally since 1900 — event metadata, not gridded extents |

### 3.7 Exposure / population (for impact, not just hazard)

| Dataset | Resolution | Notes |
|---|---|---|
| **WorldPop Global 2** | 100 m | Annual 2015–2030, free |
| **GHSL** | 10 m built-up, 100 m population | Free, JRC |
| **Microsoft + Google + OSM building footprints** | Building-level | Combined dataset covers 58 countries, 2.6B footprints, 28 TB, free (ODbL); no single source is fully global yet — expect to blend three sources |

### 3.8 Rough storage budget

- **Lean operational tier** (static base layers + rolling 30–90 day forecast/observation window): **tens of TB** — genuinely tractable for a small team on standard cloud storage.
- **Full system with multi-decade reanalysis, full NWP ensemble archives, and full SWOT/Sentinel-1 mission history retained**: pushes into the **multi-hundred-TB to low-PB range** — this only matters once you're doing deep retrospective model training/validation at scale, not for an MVP.

---

## 4. Modeling methodology, worked in detail

### 4.1 Riverine (fluvial) flooding — the most tractable piece

This is where the state of the art is most mature and most copyable:

1. **Global rainfall→runoff model**: an encoder/decoder LSTM (Google's published architecture is a good reference point) — a "hindcast" LSTM ingests ~365 days of historical meteorology + static basin attributes (from HydroATLAS), handing state to a "forecast" LSTM that ingests the NWP forecast horizon. Output is a full probability distribution over streamflow (not a point value), trained across thousands of global gauges (GRDC), entirely out-of-sample for both location and time, which is what lets it generalize to *ungauged* basins — the vast majority of the world's rivers.
2. **Training cost is genuinely modest**: the published version trained on a single V100 GPU in a few to ~10 hours. This is a small RNN, not a foundation model — fully within reach of a single researcher with one cloud GPU.
3. **Inundation mapping**: precompute a HAND (Height Above Nearest Drainage) index once per river reach from your DEM — this is a one-time, CPU-bound, embarrassingly parallel geoprocessing job (has been done at scale even inside Google Earth Engine). At inference time, translating a discharge forecast into a flood extent/depth map becomes a near-instant lookup against the precomputed HAND surface — not a live simulation. This is the single mechanism that makes global, real-time riverine inundation mapping computationally possible at all.
4. **Where to improve on the state of the art**: (a) incorporate SWOT satellite altimetry as a real-time constraint on discharge in ungauged basins — this is very recent (2026) research, not yet in any production system; (b) explicit, calibrated uncertainty bounds that are wider in data-sparse regions rather than hiding that uncertainty — directly addressing the equity gap every existing system has; (c) explicit reservoir/dam operating-rule modeling, which Google states is still "pilot research" for them.

### 4.2 Coastal flooding

Follow the GTSM pattern: an unstructured hydrodynamic mesh that is coarse offshore (~25 km) and refines only at the coastline (~2.5 km, finer for priority regions) — this is how a "global" coastal model stays computationally tractable. Couple to tide gauge networks (GLOSS/PSMSL) for calibration and to storm/cyclone-track data for surge event generation. This is a well-trodden, buildable piece.

### 4.3 Pluvial (urban rainfall-driven) flooding — the hardest, and your best differentiation opportunity

Nobody has this solved globally, for a structural reason (no global radar), not a modeling-sophistication reason. Two honest tiers:

- **In radar-covered countries** (~a few dozen, mostly wealthy/mid-latitude): replicate the NOAA FLASH pattern — a fast, physically-based or ML nowcasting model (CREST-style, or a modern GNN/ConvLSTM trained on radar QPE) at ~1 km/minutes resolution, genuinely competitive with the best in the world because the input data supports it.
- **Everywhere else** (most of the world, and the highest-vulnerability populations): be honest that resolution is capped by ~10 km/30-min satellite precipitation. The differentiated move here is not pretending to deliver street-level precision you can't back with data — it's building the best possible model *given* that input ceiling (ML nowcasting bias-corrected against whatever sparse gauge/SAR/news-based ground truth exists, explicit uncertainty communicated clearly to users) and pairing it with locally-run, GPU-accelerated true high-resolution hydrodynamics (LISFLOOD-FP 8.x, SFINCS, or HEC-RAS's GPU solver — all now 10–700× faster than a few years ago) for specific high-value cities where you invest in getting local terrain/drainage data. This "great everywhere realistic, exceptional in target cities" positioning is honestly achievable and is not what any current product does cleanly (Google's Groundsource is coarse everywhere; Fathom's hazard maps aren't real-time; nobody runs true street-level nowcasting outside a handful of nations).

### 4.4 Flash floods

Structurally the same problem as pluvial, sharing the same radar-availability constraint. The genuinely open research contribution here: a global ML model trained on whatever heterogeneous ground truth exists per-region (gauges where present, SAR-derived flood extent, even carefully-debiased news/social-media mining like Google's Groundsource) with a **model architecture that explicitly knows which data regime it's in** and adjusts its own confidence accordingly — rather than a single model pretending uniform skill everywhere.

### 4.5 The ML emulator layer (Tier 2/3 bridge) — where you can genuinely beat the state of the art on resolution

This is the most active and promising 2025–2026 research direction, and where a small, sharp team can plausibly outperform slower-moving incumbents:

- **Graph neural networks** trained on hydrodynamic-model output achieve 100–700× speedups over the physics solver they emulate, with strong accuracy (published results: water-depth MAE ~0.12 m, CSI ~88% against a real Netherlands dike-ring case).
- **Diffusion/flow-matching generative models** conditioned on terrain + rainfall can generate flood-depth maps ~150× faster than a hydrodynamic solver (published: 0.1s vs. 15s), with better accuracy than U-Net/GAN baselines.
- **Super-resolution emulators**: run cheap coarse hydrodynamics, then ML-upsample to fine resolution — directly analogous to AI image super-resolution, and the most direct path to "make the 30 m global hazard map look like a 3 m map" without paying for a 3 m physics run everywhere.
- **The open problem, and your research opportunity**: cross-region transferability. Every published emulator is trained and validated on one region/terrain type and does not reliably generalize to unseen geography — solving this (e.g., via domain-randomized training across many synthetic terrains, or physics-informed regularization that constrains the model to obey conservation laws regardless of location) is a genuinely unclaimed contribution, not incremental work.

---

## 5. Compute and cost — realistic tiers

| Tier | What it buys | Order-of-magnitude cost | Who operates at this tier today |
|---|---|---|---|
| **Solo / small team** | Fine-tune a global LSTM/GNN streamflow model (single GPU, hours); precompute HAND inundation maps from open DEMs (CPU batch job); run GPU hydrodynamics (SFINCS/LISFLOOD-FP-GPU) for specific cities on rented A100/RTX spot instances (minutes–hours per city); ingest global IMERG + open NWP ensemble summaries | ~$1k–$50k/year cloud compute + storage | Achievable by you, now |
| **National meteorological agency** | Continuous GloFAS/EFAS-style global ensemble hydrology; routine global CaMa-Flood-GPU runs at ~2 km resolution (hours per simulated year on 4×A100); regional km-scale coupled runs for high-risk basins | Dedicated HPC allocation, multi-hundred-node access | ECMWF's current posture |
| **Hyperscaler** | Flood-Hub-class continuous global ML service across 80+ countries at negligible marginal cost; massive generative weather ensembles (1,000+ members in ~100 GPU-hours) for stress-testing | Thousands of GPUs/TPUs, dedicated infra teams | Google, NVIDIA/JBA |

**Critically: even the hyperscaler tier has not made global 1–10 m continuous physical simulation tractable.** The gap is algorithmic (explicit shallow-water solvers get more expensive per cell as resolution increases, because timesteps must shrink too — the Courant condition), not just a hardware-spend problem. This means your solo/small-team tier is not as disadvantaged relative to Google as it might seem for the *hardest* part of the problem — nobody has bought their way past it with money either. Where hyperscalers have a real advantage is operational continuity, global data-pipeline engineering, and marginal-cost inference at scale — not the core modeling breakthrough you'd need for genuine differentiation.

---

## 6. Validation strategy

Any credible claim of accuracy needs to be checked against independent ground truth, not the training data:

1. **Held-out GRDC gauges** (never seen in training) for discharge accuracy — report Nash-Sutcliffe Efficiency (NSE) or Kling-Gupta Efficiency (KGE), and Critical Success Index (CSI)/F1 for event detection at multiple return periods (this is the exact metric Google's Nature paper used, so it's directly comparable).
2. **Sentinel-1 Global Flood Monitoring extents** as continuous, non-event-triggered ground truth for inundation mapping — compare your predicted flood extent polygons against SAR-detected extents using CSI/IoU.
3. **Historical event reconstruction**: pick 10–20 well-documented past floods (Dartmouth Flood Observatory catalog + EM-DAT for event selection) across different climates/basin sizes and back-test your full pipeline against them.
4. **Report accuracy stratified by data richness**, not just as a global average — this directly surfaces (and lets you actively close) the equity gap that every existing system currently hides inside an aggregate number.

---

## 7. A realistic phased roadmap

**Phase 1 (buildable solo, 2–4 months):** Global riverine streamflow model (LSTM/GNN on GRDC + HydroATLAS) + HAND-based inundation lookup on FABDEM/MERIT Hydro, covering a handful of pilot countries/basins with good gauge density for validation. This alone would already match the *architecture* of Google Flood Hub, built independently — a strong portfolio/research result on its own.

**Phase 2 (4–8 months):** Add a coastal storm-surge layer (GTSM-style mesh) and expand the riverine model's geographic coverage; start collecting/curating a validation set stratified by data richness to actively measure and report the equity gap.

**Phase 3 (8–14 months, the differentiated part):** Build the ML emulator (GNN or diffusion-based) trained on your own GPU hydrodynamic runs (SFINCS/LISFLOOD-FP-GPU) across a diverse set of synthetic and real terrains, specifically targeting the cross-region-transferability problem that current published work leaves open. Pick 2–5 pilot cities to run true high-resolution (1–10 m) hydrodynamics continuously as your flagship "we do the thing nobody else does, somewhere real" proof point, rather than diluting effort trying to be everywhere at low fidelity.

**Phase 4 (ongoing):** Pluvial/flash-flood layer, honestly tiered by input-data quality (radar-covered vs. satellite-only regions), with calibrated uncertainty as a first-class output rather than an afterthought.

---

## 8. Skills/team this realistically needs

Given your existing background (GEE, Python, FastAPI, PostGIS/pgvector, XGBoost/LSTMs, LangGraph, Docker/AWS), you already cover a meaningful share of this stack. The gaps worth naming honestly:

- **Hydrology/hydraulics domain expertise** — understanding HAND, routing, Manning's equation, return-period statistics, reservoir operations. This is learnable from the papers cited throughout this report, but it's a real gap, not a software gap.
- **GPU numerical methods** — CUDA-level familiarity to work with or extend LISFLOOD-FP/HEC-RAS GPU solvers, or a framework like PyTorch used as a differentiable PDE solver (the "Inunda" approach) if you want end-to-end trainable physics.
- **Large-scale geospatial data engineering** — the actual bottleneck in every real system surveyed is data pipeline/orchestration (ingesting global NWP ensembles, satellite feeds, gauge data continuously), not raw ML — this is squarely in your existing skill set.

---

## Sources

This report synthesizes and cites primary sources throughout (Nature, Water Resources Research, Hydrology and Earth System Sciences, Geoscientific Model Development, NHESS, arXiv, and official documentation from Google Research, ECMWF/Copernicus, Fathom, JBA, WRI, NASA, NOAA, and Deltares). Key references include:

- Nearing et al., "Global prediction of extreme floods in ungauged watersheds," *Nature* 2024 — https://www.nature.com/articles/s41586-024-07145-1
- Wing et al., "A 30 m Global Flood Inundation Model for Any Climate Scenario," *Water Resources Research* 2024 — https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2023WR036460
- ECMWF, "AI takes CEMS flood forecasting into a new era" — https://www.ecmwf.int/en/newsletter/185/news/ai-takes-cems-flood-forecasting-new-era
- Google Research, "Using AI to expand global access to reliable flood forecasts" — https://research.google/blog/using-ai-to-expand-global-access-to-reliable-flood-forecasts/
- Google Research, "Protecting cities with AI-driven flash flood forecasting" — https://research.google/blog/protecting-cities-with-ai-driven-flash-flood-forecasting/
- RIM2D multi-GPU Berlin study, *NHESS* 2026 — https://nhess.copernicus.org/articles/26/85/2026/
- CaMa-Flood-GPU, *Geoscientific Model Development* 2026 — https://gmd.copernicus.org/articles/19/5623/2026/
- LISFLOOD-FP 8.1/8.2 GPU papers, *GMD* 2023/2025 — https://gmd.copernicus.org/articles/16/2391/2023/ , https://gmd.copernicus.org/articles/18/9827/2025/
- CAPES/LineShine exascale system, arXiv 2605.24896 — https://arxiv.org/html/2605.24896v1
- Multi-scale hydraulic GNN, *NHESS* 2025 — https://nhess.copernicus.org/articles/25/335/2025/
- PIFF physics-informed generative flow model, arXiv 2025 — https://arxiv.org/html/2511.09130
- NVIDIA/JBA Earth-2 flood risk case study — https://developer.nvidia.com/blog/powering-flood-risk-assessment-with-nvidia-earth-2/
- FABDEM, MERIT Hydro, Copernicus DEM, GEBCO, GPM IMERG, ERA5, GRDC, HydroSHEDS/HydroATLAS, SWOT, ESA WorldCover, SoilGrids, Sentinel-1 Global Flood Monitoring, WorldPop, GHSL — official documentation pages (full list of ~60 sources available on request; individual sub-agent research transcripts retained this session).
