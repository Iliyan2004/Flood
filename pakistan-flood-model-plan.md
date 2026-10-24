# Pakistan Flood Prediction System — Free-Tools-Only Build Plan
### Narrowed scope: Pakistan first, maximum achievable resolution/accuracy within a zero-budget stack, global expansion later

*Companion to `global-flood-prediction-research.md` — this document replaces "global" with "Pakistan" and replaces "whatever it costs" with "$0."*

---

## 0. What "highest resolution possible, free only" actually means for Pakistan

Good news: narrowing to one country changes the math completely. The global report showed that *global* 1–10 m hydrodynamic simulation is infeasible for anyone. Pakistan is ~881,900 km² — about **0.6% of global land area**. A free-tier GPU (Colab/Kaggle T4 or P100, 16 GB VRAM) can hold a shallow-water simulation of a Pakistani city at 30 m resolution using well under 1 GB of memory, with headroom to spare. So the ceiling for you isn't compute anymore — **it's data**, specifically:

- **No free global DEM is finer than ~30 m for Pakistan.** There's no Pakistani national LiDAR program with open data (unlike the US/UK/EU), and the only sub-10 m option (Airbus WorldDEM Neo, 5 m) is commercial. So **30 m (Copernicus DEM / FABDEM / MERIT Hydro) is your real terrain-resolution ceiling** for a free build, nationwide. That's still 3–15× finer than what GloFAS or WRI Aqueduct deliver, and matches Fathom's/JBA's global commercial baseline resolution — for free.
- **No confirmed free bulk historical discharge (gauge) archive exists for Pakistan.** GRDC shows no confirmed Pakistani stations; WAPDA/IRSA/PMD publish live dashboards and daily PDF bulletins, not downloadable historical time series. This is the single biggest practical obstacle and is addressed directly in §2 and §5.
- **Precipitation, land cover, soil, and flood-extent validation data are all genuinely free and good for Pakistan** — no gaps there.

So the honest target: **a 30 m-resolution, multi-hazard (riverine + urban pluvial + coastal + GLOF) system for Pakistan, built entirely on free/open data and free/open-source software, running on free-tier compute.** That is a real, achievable, and — for Pakistan specifically — currently non-existent product. Nobody has built a unified, high-resolution, all-Pakistan-hazards flood system in the open; PMD/FFD's public tools are bulletin-based, not a queryable predictive model.

---

## 1. Why Pakistan is actually a good first target (not just "smaller")

Pakistan gives you three distinct, well-documented flood regimes to build for — which is a better systems-design exercise than a generic single-hazard model, and maps directly onto real, high-stakes need:

1. **Riverine monsoon flooding on the Indus and tributaries** — the 2010 and 2022 floods are among the best-documented flood disasters on Earth (multiple free satellite-derived ground-truth datasets exist — see §4). This is your primary training/validation target.
2. **Urban pluvial + coastal flooding in Karachi** (and secondarily Lahore) — dense population, poor drainage, and (for Karachi) coastal storm-surge exposure (Cyclone Biparjoy, 2023) make this a compound-flood case, which most global products don't handle.
3. **Glacial Lake Outburst Floods (GLOFs) in Gilgit-Baltistan/Karakoram** — you already have direct prior experience here (your earlier GLOF detection/monitoring work in GEE for a Gilgit-Baltistan lake). This is a genuinely underserved hazard type globally, and Pakistan is one of the highest-GLOF-risk countries on Earth.

A system that handles all three, even at modest fidelity, is more useful and more novel than a higher-resolution model that only does one.

---

## 2. Data sources — confirmed free, Pakistan-specific

### 2.1 Terrain (30 m ceiling, as established above)
| Dataset | Res. | Use |
|---|---|---|
| Copernicus DEM GLO-30 | 30 m | Primary surface elevation |
| **FABDEM** | 30 m | Bare-earth (buildings/canopy removed) — free for non-commercial use, which covers this project |
| **MERIT Hydro** | 90 m | Pre-computed flow direction/accumulation/drainage/**HAND layers** — huge time-saver, avoids you having to hydrologically condition the DEM yourself |

### 2.2 Precipitation & forecast forcing (all free, all cover Pakistan fully)
| Dataset | Res. | Role |
|---|---|---|
| CHIRPS | 5.5 km, daily | Long historical record for model training (covers 50°S–50°N — Pakistan at 24–37°N is fully in range) |
| ERA5-Land | 9 km, hourly | Reanalysis forcing, finer temporal resolution |
| GPM IMERG | 10 km, 30-min | Near-real-time precipitation for live inference |
| ECMWF open data (IFS ENS / AIFS ENS) or NOAA GFS | 25–28 km, ensemble, 15-day | Forecast forcing to drive predictions forward in time |

### 2.3 River discharge / calibration data — the real gap, and how to work around it
No confirmed free bulk historical archive exists. Three workable paths, use all three:
1. **Build your own scraper against IRSA's daily data** (`pakirsa.gov.pk`, `DailyData.aspx` + dated PDF reports) and Sindh Irrigation Department's water bulletins (`irrigation.sindh.gov.pk/Flows/bulletins`) — these publish current rim-station flow (Tarbela, Kalabagh, Chashma, Taunsa, Guddu, Sukkur, Kotri) going forward. Start this scraper running now; every day it runs, your real gauge record grows.
2. **Academic reconstructed-streamflow datasets** as a training substitute for the historical period you can't scrape retroactively — e.g. the *Scientific Data* "Hydrological model-based streamflow reconstruction for Indian sub-continental river basins, 1951–2021" (Nature, open) likely includes Indus sub-basins; also check **Caravan / GRDC-Caravan** (open, ML-ready hydrology dataset) for any Indus stations that made it in.
3. **PMD FFD's "Historical Flood Peaks" page** (via their floodupdates.com-affiliated site) for at least peak-flow validation points, even without full time series.

Treat discharge data as the resource you're actively building, not something you can just download once — this is honest, and it's also exactly the kind of first-party data asset that would make your system genuinely differentiated over time.

### 2.4 Historical flood ground truth for validation (all confirmed free)
| Event | Dataset | Res. | License |
|---|---|---|---|
| **2022 floods** | TU Wien Sentinel-1 flood masks (Aug 10–Sep 23 2022, full event) | 20 m | **CC BY 4.0**, ~458 MB, direct download |
| 2022 floods (Sindh) | Copernicus EMS **EMSR629** (Larkana/Shikarpur/Jacobabad) | Vector, high-res | Free, no registration |
| 2022 floods | UNOSAT/UNITAR satellite water extents (Aug 2022) | Vector | Free via HDX |
| **2010 floods** | UNOSAT/UNITAR flood analysis time series (Jul–Sep 2010) | Vector | Free |
| 2010 floods | NASA MODIS-derived imagery | 250 m | Free (coarser — Sentinel didn't exist yet) |
| Both | Dartmouth Flood Observatory archive (via HDX) | Event records | Free |

This is a genuinely strong validation set — the TU Wien 2022 dataset in particular gives you a full-event, 20 m, freely-licensed ground truth to benchmark your fluvial model against, which is better validation data than what several commercial vendors disclose publicly.

### 2.5 GLOF-specific data (Gilgit-Baltistan/Karakoram)
| Dataset | Notes |
|---|---|
| Landsat/Sentinel-derived glacial lake dataset, China–Pakistan Economic Corridor 1990–2020 (ESSD) | Directly covers your target region |
| Annual 30 m Glacial Lakes dataset, High Mountain Asia 2008–2017 (Zenodo, free download) | Karakoram subset usable |
| **GLOFsusceptibility** (GitHub, open-source code) | Himalaya-focused susceptibility model, directly adaptable to Karakoram — reuse rather than build from scratch |
| Sentinel-2, 10 m, free via GEE | For your own ongoing lake monitoring, building on your prior GEE GLOF work |

### 2.6 Everything else (all confirmed free, no Pakistan-specific gaps)
ESA WorldCover (10 m land cover), SoilGrids (250 m, infiltration parameters), WorldPop + GHSL (population/exposure), Google Open Buildings (strong South Asia coverage — good building-footprint source for Pakistan specifically), HydroSHEDS/HydroATLAS (river network/basins), Copernicus Sentinel-1 Global Flood Monitoring (continuous, ongoing validation).

---

## 3. Software stack — verified genuinely free/open-source (not just free-tier)

| Tool | License | Role |
|---|---|---|
| **LISFLOOD-FP 8.x** | GPL v2.0, GPU included in the open release | Your primary hydrodynamic solver — compiles and runs on Colab/Kaggle Linux GPU runtimes |
| **ANUGA** | Apache 2.0 | Alternative/cross-check hydrodynamic solver, `pip install anuga` |
| **SFINCS** | GPL v3.0 | Best fit for **Karachi specifically** — purpose-built for compound fluvial+pluvial+coastal flooding |
| **CaMa-Flood** | Apache 2.0 | Optional — basin-scale river routing if you want Indus-basin-wide context beyond Pakistan's borders (Afghanistan/China/India headwaters) |
| **Google Research flood-forecasting code** | Apache 2.0 | Methodology reference only — it's illustrative excerpt code, not a deployable model or trained weights; use it to understand the LSTM/HAND approach, then build your own |
| **GLOFsusceptibility** | Check repo license | Reusable GLOF susceptibility codebase to adapt |
| Google Earth Engine | Free (research/non-commercial) | DEM/precip processing, HAND computation, Sentinel-1/2 time series — you already know this tool |
| PyTorch + NeuralHydrology-style LSTM/GNN | Open source | Rainfall-runoff model training |

**Important stack correction to your existing plan**: your usual frontend stack uses **Mapbox GL JS**, which has a paid tier beyond a free usage quota. Since you said "only free tools," swap to **MapLibre GL JS** — a fully open-source fork with no usage costs or API key requirements, API-compatible enough that migration is minor. Keep PostGIS/pgvector, FastAPI, Next.js as-is — all free/open-source already.

**Compute reality check** (confirmed): Colab free tier gives a T4 (16 GB VRAM); Kaggle gives 30 GPU-hours/week (P100 or 2×T4). A 30–90 m LISFLOOD-FP run over Karachi (~3,780 km²) needs well under 1 GB VRAM and minutes-to-low-hours of wall time — both platforms have enormous headroom for this. Training a small LSTM/GNN on a few hundred basin time series is a sub-hour job. **HEC-RAS is free but Windows-only with no Linux/CUDA build — it won't run on Colab/Kaggle**, so skip it for this cloud-free-tier workflow despite its zero cost; LISFLOOD-FP is the right GPU tool here.

---

## 4. Architecture, adapted to Pakistan's three hazard types

```
STATIC LAYERS (computed once, GEE + open DEM/soil/landcover)
  → HAND index nationwide from MERIT Hydro (30-90m) — instant fluvial inundation lookup
  → SFINCS-ready compound-flood grid for Karachi (fluvial+pluvial+coastal)
  → GLOF susceptibility layer for Karakoram glacial lakes

RIVERINE (Indus + tributaries)
  → LSTM/GNN rainfall-runoff model, trained on scraped IRSA data +
    reconstructed streamflow datasets, forced by CHIRPS/ERA5-Land/IMERG
  → Validated against TU Wien 2022 Sentinel-1 flood masks + Copernicus EMS EMSR629
  → HAND lookup → national inundation extent, 30-90m

URBAN PLUVIAL + COASTAL (Karachi first, Lahore second)
  → SFINCS compound model, storm forcing from IMERG + ECMWF ensemble forecasts
  → GPU LISFLOOD-FP runs for deeper validation/training-data generation
  → Free building footprints (Google Open Buildings) for exposure overlay

GLOF (Gilgit-Baltistan)
  → GLOFsusceptibility model adapted to Karakoram lake inventory
  → Sentinel-2 (GEE) ongoing lake-area monitoring, building on your prior work
  → Downstream HAND-based inundation routing for a lake-breach scenario
```

---

## 5. Roadmap

**Step 1 (weeks):** Stand up the IRSA/Sindh Irrigation scraper now — it's the resource that only gets more valuable the earlier you start it, and it's the biggest real gap in the whole plan.

**Step 2 (weeks):** National HAND-based fluvial inundation layer from MERIT Hydro + a first-pass LSTM trained on whatever discharge proxy you have (reconstructed streamflow dataset), validated against the TU Wien 2022 Sentinel-1 masks. This alone is a working, demoable riverine flood model for Pakistan.

**Step 3 (weeks–month):** Karachi SFINCS compound model — your most novel piece, since nothing free/open currently covers Karachi's compound fluvial+pluvial+coastal risk at this fidelity.

**Step 4 (ongoing):** GLOF module, leveraging your existing GLOF/GEE experience directly.

**Step 5 (later, per your original ask):** once Pakistan is working end-to-end and validated, the architecture generalizes directly — the global report already gives you the full data/method catalog for that expansion.

---

## Sources
Builds on `global-flood-prediction-research.md`'s citation list, plus: TU Wien Sentinel-1 Pakistan flood dataset (CC BY 4.0, researchdata.tuwien.at/records/zvvmh-nan78) and companion NHESS paper; Copernicus EMS EMSR629; UNOSAT/UNITAR 2010 & 2022 Pakistan flood products; IRSA (pakirsa.gov.pk) and Sindh Irrigation Department bulletins; PMD Flood Forecasting Division (ffd.pmd.gov.pk); LISFLOOD-FP/SFINCS/CaMa-Flood/ANUGA license files (Zenodo/GitHub); ESSD Karakoram glacial lake dataset and HMA glacial lakes Zenodo archive; GLOFsusceptibility (GitHub).

---

## 6. Implementation status (as of this build)

What actually got built into the live app (`fis_app.py` + the tiered
global-flood-prediction modules), mapped honestly against this plan —
matches, deliberate divergences, and what's still open. Full narrative
detail (bugs found/fixed, real verification numbers) is in `CLAUDE.md`;
this section is the plan-vs-reality summary.

### §4's architecture — riverine piece: built, one tier short of the plan's own design

| Plan's design | What's actually running | Match? |
|---|---|---|
| MERIT Hydro **precomputed** HAND, nationwide | HAND computed **on demand per drawn AOI** (`hand_utils.py`, `pysheds` D8 flow routing on the existing Pakistan SRTM), cached after first computation (`hand_cache/`) | Different mechanism, same effect — a genuine national precompute needs the full MERIT Hydro conditioned DEM (a separate download this build didn't pull); per-AOI on-demand is real, verified fast (0.35s first compute, ~24x faster cached), and needs no new data source |
| LSTM trained on scraped IRSA discharge + reconstructed-streamflow datasets | LSTM trained on **Caravan** (Kratzert et al. 2023) — 630 real basins across 7 world regions (US/GB/Australia/Brazil/Chile/Canada/Central Europe), **none in Pakistan** | Real divergence, not a shortcut taken lightly: the IRSA/Sindh Irrigation scraper this plan's own Step 1 calls "the biggest real gap" was never stood up this session. Applied to a Pakistan AOI via nearest-analog-basin regionalization — honestly reports the analog is often 1,000s of km away (verified: 4,785km for an Islamabad-area test) rather than hiding that gap |
| Validate against TU Wien 2022 Sentinel-1 flood masks / EMSR629 | **Not done.** No validation against real historical Pakistan flood extents was run this session | Open — this is the single most valuable next step for making the Tier 1 discharge numbers actually trustworthy for Pakistan specifically, per this plan's own §2.4 |
| Instant national inundation lookup | `/area/instant-flood` + `/area/instant-flood-library` (Tier 2) — real Manning's-equation rating curve from HAND, verified physically sensible (monotonic area/depth vs. discharge) | Matches in spirit, per-AOI not precomputed-nationwide (same reasoning as the HAND row above) |

### §4's architecture — urban pluvial + coastal (Karachi): not built

SFINCS was never installed or wired in. What exists instead is Tier 3's
existing general-purpose LISFLOOD-FP pluvial+fluvial run (already proven
on any Pakistan AOI, including Karachi) plus Tier 4's pluvial/flash-flood
risk tier (real rainfall vs. real 20-year per-point climatology,
`pluvial_utils.py`) — genuinely useful, but not the purpose-built compound
fluvial+pluvial+coastal solver this plan specifically recommends for
Karachi's coastal-surge exposure. **No coastal/storm-surge modeling of
any kind was attempted.** This remains the plan's own "most novel piece"
(§5 Step 3) and is still fully open.

### §4's architecture — GLOF (Gilgit-Baltistan): not built

No GLOF susceptibility layer, no glacial-lake monitoring integration.
Fully open, unstarted this session.

### Software stack — one real divergence from the plan's own explicit correction

The plan explicitly flags (§3): *"swap to MapLibre GL JS... since you
said 'only free tools'"* — the live app instead uses **Mapbox GL JS**
throughout (already the case before this session; not changed by this
work). Worth restating plainly since the plan calls this out by name: if
the zero-cost-stack constraint is still active, this is the one
concrete place the running app doesn't meet it — Mapbox's free tier
(50k loads/month) covers this app's real usage today, but it is not the
open-source/no-usage-cost tool this plan specifies. Everything else
software-wise matches or exceeds the plan: **LISFLOOD-FP 8.x GPU** is
exactly the solver recommended (§3's own top pick), now upgraded further
than the plan anticipated (CUDA 13/Blackwell build, ~10-11x faster than
the CPU path, see `CLAUDE.md`); PyTorch for the LSTM matches; SoilGrids,
ESA WorldCover, Google Open Buildings all match §2.6 exactly and are
wired in for real (soil-derived infiltration, land-cover-derived
friction, real building footprints).

### DEM — matches the plan's own stated ceiling, sourced differently

The plan's real ceiling ("30m (Copernicus DEM/FABDEM/MERIT Hydro) is
your real terrain-resolution ceiling") holds exactly: the app's primary
DEM is a user-supplied ~29m Pakistan-wide SRTM (already in place before
this session), and the newly-added global fallback (opt-in, outside
Pakistan only) is Copernicus DEM GLO-30 at the same ~30m — the plan's
own first-choice dataset, just fetched on-demand per-AOI rather than
bulk-downloaded nationwide as FABDEM/bare-earth. FABDEM itself was
checked and deliberately not used (CC BY-NC-SA license doesn't fit a
shipped product — see `CLAUDE.md`).

### Net summary

Real progress on the riverine piece (§4's first block) and the general-
purpose Tier 0/2/3 machinery every hazard type would reuse (HAND
computation, instant lookup, per-cell soil/land-cover data, the GPU
solver itself). The three hazard-specific pieces this plan's §1 argues
make Pakistan a good first target — riverine, urban-pluvial+coastal, and
GLOF — are **1 of 3 started, 2 of 3 fully open**: riverine has a real
(if Pakistan-ungauged) discharge model and instant inundation lookup;
Karachi compound-flood (SFINCS) and GLOF are both untouched. The
single highest-leverage next step per this plan's own Step 1/§2.4 is
standing up real Pakistani discharge data (the IRSA/Sindh Irrigation
scraper) and validating against the TU Wien/EMSR629 ground truth —
without that, Tier 1's discharge numbers for Pakistan AOIs remain an
honest but distant regionalization, not a Pakistan-calibrated model.
