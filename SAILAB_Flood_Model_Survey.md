# Flood modelling software: survey and comparison with SAILAB (draft 1, 31 Oct 2026)

Scope: the widely used flood and hydrodynamic modelling tools, not every model in the world (that list has no end). Each row says whether a fact comes from a source I retrieved this session (**S**) or from general knowledge I have not re-verified (**K**). Treat **K** as "to check before quoting".

## 1. Comparison table

| Model | Type / physics | Licence, cost | GPU | Scale seen in literature | Validation evidence found | Basis |
|---|---|---|---|---|---|---|
| **SAILAB** (LISFLOOD-FP 8.2 inside) | 2D local-inertial (ACC) or DG2 shallow-water; plus HAND instant preview, coastal bathtub/HVAR, GLOF, snowmelt, loss | Own app; solver GPL | Yes (CUDA, GB10) | Pakistan-wide, no size cap | Case study in this report: three half-flooded areas, 2022 Sindh, mean CSI 0.50, on par with ANUGA, SFINCS and TELEMAC-2D and the trivial baseline. (An earlier CSI of 0.66 came from a 98%-flooded box and is withdrawn.) | own |
| **LISFLOOD-FP 8.x** | 2D local-inertial / non-uniform grid | GPL v3, non-commercial per a source | Yes; ACC 2-28x faster than CPU, non-uniform up to 320x | Regional | Many papers; 8.3 released 13 Oct 2026 | S |
| **HEC-RAS 2D** | Full SWE or diffusion wave | Free (US Army Corps) | Not verified | Reach to basin | Benchmarked against the UK EA suite (Brunner 2018); compared with LISFLOOD-FP on the Secchia River (both misestimated extent/depth in built-up zones) | S |
| **TUFLOW** (Classic / HPC / FV) | Full SWE; HPC GPU solver | Commercial, by module | Yes (TUFLOW GPU) | Urban to catchment | In the UK EA 2013 benchmark of 2D packages | S |
| **MIKE 21 / MIKE+ / MIKE FLOOD** | Full SWE, flexible mesh | Commercial | Not verified | Coastal to urban | In the EA 2013 benchmark ensemble | S |
| **InfoWorks ICM** | 1D/2D integrated, urban drainage | Commercial (Autodesk) | Not verified | Urban | In the EA ensemble | S |
| **Flood Modeller** | 1D/2D | Commercial | Not verified | River | Not found in this pass | S/K |
| **FLO-2D** | Diffusive / dynamic wave | $995/yr Pro, free Basic tier (2020 source, may be outdated) | No (K) | Floodplain | US FEMA-accepted (K) | S/K |
| **SOBEK** | 1D/2D | Commercial/free versions (K) | No (K) | River | In the EA benchmark | S |
| **ANUGA** | Full SWE, finite volume | Open source | Not verified | Regional | In the EA ensemble | S |
| **TELEMAC-2D** | Full SWE, finite element | Open source | No (K) | River to estuary | Compared with HEC-RAS (Loire; Indonesia flood, 2026 preprint) | S |
| **Delft3D FM / D-Flow FM** | Full SWE, flexible mesh, coastal | Open source (K) | In development (K) | Coast to basin | Used as reference for SFINCS | S/K |
| **SFINCS** | Reduced-physics (inertial), subgrid; compound flooding | Open source (K) | Not verified | Coast to global | About 100x faster than Delft3D-FLOW on a Hurricane Irma case (thesis); tested on 499 global river events (HESS 2026) | S |
| **RIM2D** | Local-inertia, GPU | Open (K) | Yes | Urban to catchment | Compared with SERGHEI on the 2021 Ahr flood: similar accuracy at coarse resolution, differences at fine | S |
| **SERGHEI** | Full SWE, GPU/HPC | Open source | Yes (multi-GPU) | Catchment | Compared with RIM2D (above) | S |
| **TRITON** | Full SWE, first-order upwind, multi-GPU | Open source (ORNL) | Yes | 6,800 km2, 68 M cells, 10-day storm in under 30 min | Published test cases | S |
| **HiPIMS** | Full SWE, GPU | Open source (K) | Yes (K) | Catchment | No result retrieved | K |
| **Fathom** (US and global 30 m) | Terrain + LISFLOOD-FP-based hazard maps | Commercial data product | n/a (precomputed) | Continental / global | Hit rate 86% vs FEMA maps (US, 2017); CSI about 0.75 and water-level error about 0.6 m (global 30 m, 2024) | S |
| **Google Flood Hub** | LSTM river forecast + inundation model | Free service, not a runnable model | n/a | 150+ countries | 5,680 training gauges; compared against GloFAS v4 | S |
| **GloFAS** | Hydrological discharge forecast, not inundation | Free data | n/a | Global | Used as the benchmark in this app's melt checks | S/K |
| **SWMM** (EPA) | 1D urban drainage with 2D coupling options | Free | No | Urban | Widely used (K) | K |

## 2. Where SAILAB stands

- **Strengths:** open physics engine on a GPU (measured 51 to 71 s per 13 km area against 89 to 767 s for the CPU models run here); one workflow from rainfall to buildings, roads, loss, coastal, GLOF, snowmelt and protection scenarios; Pakistan-specific datasets; queueing, run QA and monitoring built in.
- **Weaknesses:** no flood-extent skill clearly above a trivial guess on the 2022 Sindh test (rain-only forcing; see the case-study report); no depth validation; damage curves are Asia averages; ACC can fail on extreme slopes; no 1D channel network or sewer system; elevation data at about 29 m.
- **Versus Fathom and Google Flood Hub:** they publish continental validation; SAILAB does not yet.

## 3. Which models can run a case study here

- **Likely runnable now (free, open):** LISFLOOD-FP (have it), HEC-RAS 2D, ANUGA, TELEMAC-2D, SFINCS, Delft3D FM, SWMM, TRITON/SERGHEI/RIM2D (need building; GPU support untested on this GB10).
- **Not runnable without a licence:** TUFLOW, MIKE, InfoWorks ICM, Flood Modeller, FLO-2D Pro. For these, compare against published scores for the same event, or note "no result".
- **Data products (not runnable):** Fathom, Google Flood Hub, GloFAS. Compare their published maps or forecasts with the same observed extent if we can obtain them.

## 4. Proposed case studies

Pick events that have a satellite flood map to score against:
1. **Sindh, Aug-Sep 2022** (have the UNOSAT extent; Sentinel-1 flood maps from Roth et al. 2023 are an independent second reference).
2. **Indus, 2010** (HEC-RAS studies already exist for 2010 and 2015, see Afzal et al.).
3. **One small urban pluvial event** with a mapped extent, to test the 2D models that matter in cities.

Same inputs for every model: one DEM (SRTM, 29 m), the same rainfall or inflow hydrograph, the same friction, the same scoring (CSI, POD, FAR, bias in area, plus depth where observed depths exist).

## 5. Sources

- Shustikova et al. 2019, HEC-RAS vs LISFLOOD-FP: https://www.tandfonline.com/10.1080/02626667.2019.1671982
- Néelz and Pender 2013, EA benchmarking of 2D packages: https://consult.environment-agency.gov.uk/engagement/1a33644d/results/appendix-6---neelz--s.---pender--g.--2013--benchmarking-the-latest-generation-of-2d-hydraulic-modelling-packages.-bristol_environment-agency.pdf
- Hunter et al. 2008 urban benchmark: https://research-information.bris.ac.uk/en/publications/benchmarking-2d-hydraulic-models-for-urban-flood-simulations/
- LISFLOOD-FP 8.1 GPU solvers, GMD 2023: https://gmd.copernicus.org/articles/16/2391/2023/
- RIM2D vs SERGHEI, NHESS 2024: https://nhess.copernicus.org/articles/24/2857/2024/
- TRITON: https://impact.ornl.gov/en/publications/triton-a-multi-gpu-open-source-2d-hydrodynamic-flood-model/
- SFINCS (thesis and HESS 2026): https://research.vu.nl/en/publications/modeling-compound-flooding-in-coastal-systems-using-a-computation/ and https://hess.copernicus.org/articles/30/5491/2026/
- Fathom validation: https://www.fathom.global/academic-papers/validation-of-a-30m-resolution-flood-hazard-model-of-the-conterminous-united-states/
- Google Flood Hub / Nature 2024: https://research.google/blog/using-ai-to-expand-global-access-to-reliable-flood-forecasts/
- Pakistan 2022 satellite flood maps, NHESS 2023: https://nhess.copernicus.org/articles/23/3305/2023/
- Licence overview (UK guide): https://www.unda.co.uk/news/flood-modelling-software-tools-techniques-guide/

## 6. Published HEC-RAS / LISFLOOD-FP / SWAT comparisons (added 31 Oct 2026)

Full table with sources in `model_comparison/SAILAB_Model_Comparison_Report.pdf`, section 8. Summary: checked against the papers' own text, Fernandez and Mourato (River Lis, Portugal) found 65% overlap between HEC-RAS and LISFLOOD-FP inundated areas, HEC-RAS flood-wave travel time about half, LISFLOOD-FP much slower; Oikonomou et al. (Thessaly, Greece) reported run times of 3 h (LISFLOOD-FP), 2 h (FLO-2D) and 10 min (HEC-RAS), with HEC-RAS far more sensitive to floodplain roughness. From abstracts or search summaries only: Shustikova et al. 2019 (Secchia, both mis-estimate built-up areas), Gorgan Plain F-statistic 0.69 vs 0.67, Horritt and Bates 2002, Alzette 2009. No study comparing SWAT with LISFLOOD/LISFLOOD-FP was found (SWAT is a catchment water-balance model, not a flood-extent model).
