"""Front matter and Sections 1-3."""
from doc import todo
import study as S


def fmt(x, n=3):
    return f"{x:.{n}f}"


def front(R):
    sk = R["skill"]
    auc_mod = sum(sk[a]["SAILAB"]["AUC"] for a in sk) / 3
    auc_ter = sum(sk[a]["Terrain only (-elevation)"]["AUC"] for a in sk) / 3
    sc = {r["cells"]: r for r in R["scaling"]["runs"] if r["device"] == "gpu"}
    big = max(sc)
    gpu07 = [r for r in R["scaling"]["runs"] if r["cells"] == 690557]
    sp = (lambda d: d["cpu"] / d["gpu"])({r["device"]: r["solver_wall_s"] for r in gpu07})
    extra = ""
    import content4 as c4
    if R.get("sens"):
        extra += f" {c4.abstract_sens(R['sens'])}"
    if R.get("case"):
        extra += f" {c4.case_abstract(R['case'])}"
    if R.get("mon"):
        extra += f" {c4.mon_abstract(R['mon'])}"
    abstract = (
        "Flood-risk decisions for assets, infrastructure and cities need four answers at once: where the water will go, what it will cost, "
        "which protection pays back, and whether a warning will arrive in time. In practice these come from separate tools, data formats and teams, so "
        "scenarios are tested rarely and results go stale. We present SAILAB, a platform that answers all four in one workflow for Pakistan. A "
        "two-dimensional shallow-water solver (LISFLOOD-FP 8.2, local-inertial scheme) runs on the GPU for any area drawn on a map, and a terrain-based "
        "instant preview screens scenarios first. Simulated depth and speed are turned into a damage class for every building, a loss in Pakistani rupees "
        "and an annual average loss. A mitigation module tests embankments, drains, retention basins and nature-based options and searches for good "
        "placement; a monitoring module watches rainfall forecasts and starts a run and an alert when a threshold is crossed. A preloaded national data "
        "layer includes about 1.1 million road segments, 65,909 schools, 35,561 hospitals, 101 dams and 3,378 glacial lakes. "
        "We evaluate the platform without flattering it. In a controlled comparison on three 13 km areas of the 2022 Sindh flood, scored against the UNOSAT "
        f"satellite flood extent, SAILAB’s mean critical success index (CSI) is {fmt(S.ROWS[2][4])}, against {fmt(S.ROWS[3][4])} for ANUGA, {fmt(S.ROWS[4][4])} for SFINCS and "
        f"{fmt(S.ROWS[5][4])} for TELEMAC-2D, and its flooded cells overlap those of ANUGA and TELEMAC-2D by {S.OVERLAP[('SAILAB','ANUGA')]:.2f} and {S.OVERLAP[('SAILAB','TELEMAC-2D')]:.2f}, "
        "which supports the correctness of the numerics. But no model, SAILAB included, beats the trivial forecast “flood everything” (CSI 0.500), and ranking cells by "
        f"simulated depth is barely better than ranking them by elevation alone (mean ROC AUC {auc_mod:.2f} against {auc_ter:.2f}). We therefore claim solver agreement and workflow "
        "integration, not predictive skill, for rainfall-only forcing of this event. The solver handled "
        f"{big/1e6:.1f} million cells in {sc[big]['solver_wall_s']:.0f} s on one GPU and was {sp:.1f} times faster than one CPU thread at 0.7 million cells; a complete run for a 13 km area, with "
        f"buildings, roads, risk, loss and a 3D scene, took {min(v[0] for v in S.E2E.values())}–{max(v[0] for v in S.E2E.values())} s.{extra}")
    return f"""
<h1 class="title">SAILAB: A GPU-Accelerated Platform that Unifies Flood Simulation, Damage Quantification, Mitigation Testing and Automated Monitoring for Pakistan</h1>
<p class="authors">Iliyan Javed Hirani<sup>1,*</sup> &nbsp;&nbsp; Mudassir Sohail<sup>2</sup></p>
<p class="affil"><sup>1</sup> Geo AI Developer, AI Geo Navigators, Islamabad, Pakistan. <sup>*</sup>Corresponding author: iliyanhirani72@gmail.com<br>
<sup>2</sup> GIS Lead (supervisor), AI Geo Navigators, Islamabad, Pakistan. {todo("confirm title and affiliation line")}</p>
<div class="status"><b>Draft v0.2, 17 November 2026.</b> Preprint, not peer reviewed. Supersedes draft v0.1: the headline validation figure of v0.1 (CSI 0.659) was withdrawn, see Section [[s:withdrawn]].</div>
<div class="abstract"><h2>Abstract</h2>{abstract}</div>
<p class="keywords"><b>Keywords:</b> flood inundation modelling; shallow-water equations; GPU computing; flood damage; nature-based solutions; early warning; Pakistan; Indus basin.</p>
"""


def intro(D, R):
    t = [D.h(2, "intro", "Introduction")]
    t.append("""<p>Pakistan’s 2022 floods affected about 33 million people and caused more than US$30 billion in damage and economic loss, about 70&nbsp;% of it in Sindh [[c:pdna2022]]. The event showed a recurring gap between what decision makers need and what their tools provide. The owner, lender or planner of an asset asks five questions: will water reach the site; how deep and how fast will it be; what will the damage cost; which protection actually pays back; and will we know before it happens.</p>
<p>Each question is normally answered by a different tool. Hazard maps come from a hydraulic model run by a specialist and are usually a handful of fixed return periods. Damage is estimated later, in a GIS or a spreadsheet. Mitigation is assessed by running the hydraulic model again, often weeks later. Forecasts live in a separate meteorological system. Because every hand-off costs time, few scenarios are tested and results are rarely refreshed when conditions change.</p>
<p>This paper describes SAILAB, built at AI Geo Navigators and launched at Bahria University, Islamabad, on 16 November 2026. Its contribution is integration rather than a new numerical scheme: one engine and one data record serve simulation, quantification, protection and monitoring. We also report what the platform does <em>not</em> yet show. Our contributions are:</p>
<ol>
<li>A two-stage workflow in which a terrain-based instant preview screens scenarios and a GPU physics run confirms the ones that matter, with measured runtimes and a measured, and modest, agreement between the two stages.</li>
<li>A building-level damage and rupee-loss model, with annual average loss, driven directly by simulated depth.</li>
<li>A mitigation module that evaluates engineered and nature-based options and searches for placement before capital is committed.</li>
<li>A monitoring module that couples forecast rainfall to automatic simulation and alerting, with measured latencies.</li>
<li>A preloaded national data layer, and a controlled comparison of the solver against three independent flood models on the 2022 Sindh flood, including a threshold-free skill test. The comparison supports the solver’s correctness but shows no predictive skill for rainfall-only forcing, and we say so.</li>
</ol>
<p><b>Paper structure.</b> Section [[s:related]] reviews related work and Section [[s:system]] gives the system overview. Sections [[s:engine]] to [[s:monitor]] describe the engine, exposure and damage, mitigation and monitoring. Section [[s:eval]] reports the evaluation and sensitivity, Section [[s:perf]] reports runtime, Section [[s:case]] presents a protection case study, and Sections [[s:disc]] and [[s:concl]] discuss limits and conclude.</p>""")
    return "\n".join(t)


def related(D, R):
    t = [D.h(2, "related", "Background and related work")]
    t.append(D.h(3, "rel_hyd", "Hydraulic flood inundation modelling"))
    t.append("""<p>Two-dimensional hydraulic models solve the shallow-water equations, or simplified forms of them, on a gridded terrain. Reduced-complexity formulations such as the local-inertial scheme of Bates et al. [[c:bates2010]] made large-domain simulation affordable and underpin continental and global hazard models [[c:sampson2015,wing2017]]. The LISFLOOD-FP code implements that scheme with sub-grid channel options [[c:neal2012]] and, since version 8.1, GPU solvers [[c:sharifian2023]]. SFINCS applies a reduced-physics formulation to compound coastal flooding [[c:leijnse2021]]. Full finite-volume and finite-element solvers such as ANUGA [[c:anuga]] and TELEMAC-2D [[c:hervouet2007]] solve the complete equations at higher cost. Terrain-based screening methods such as Height Above the Nearest Drainage (HAND) [[c:nobre2011]] give fast, depth-only estimates without dynamics; large-scale variants map inundation over whole catchments from a rating curve and a terrain index [[c:zheng2018]]. SAILAB combines the two ends: a terrain-based preview for screening and a full solver for confirmation.</p>""")
    t.append(D.h(3, "rel_glob", "Global and national flood hazard products"))
    t.append("""<p>Global hazard models give consistent maps at tens of metres to kilometre resolution for fixed return periods [[c:sampson2015]]. Machine-learning streamflow forecasting now reaches ungauged basins worldwide [[c:nearing2024]], and the Global Flood Awareness System provides ensemble river-discharge forecasts and a long reanalysis [[c:alfieri2013,harrigan2020]]. Such products are static or coarse, are not tied to the local building stock, and cannot test a user-defined intervention. For 2022 specifically, studies attribute the Pakistan event mainly to multiday extreme monsoon rainfall on wet soil, with glacier melt a smaller factor [[c:nanditha2023]], and attribute the scale of the impacts to high exposure and vulnerability [[c:otto2023]]. Because that rain fell over a very large catchment, how rainfall-only simulations should be read needs care (Section [[s:eval]]).</p>""")
    t.append(D.h(3, "rel_dmg", "Damage and loss"))
    t.append("""<p>Depth–damage functions relate water depth to the fraction of an asset’s value lost. SAILAB uses the Asia-region residential and agricultural functions and maximum damage values of the JRC global database [[c:huizinga2017]]; Section [[s:damage]] gives the details and the limits of using a continental average for Pakistan.</p>""")
    t.append(D.h(3, "rel_ew", "Early warning"))
    t.append("""<p>Operational flood warning chains connect a forecast, a hydrological model and a communication channel. SAILAB’s monitoring module automates the first link, from forecast to simulation, and delivers an alert to the user’s team; it does not replace official warnings (Section [[s:ethics]]).</p>""")
    t.append(D.h(3, "rel_gap", "Gap addressed"))
    rows = [("Instant scenario screening", "no", "no", "yes (depth only)", "yes, as a screening estimate"),
            ("Depth and speed from a physics model", "fixed return periods", "yes", "no", "yes"),
            ("Building-level loss in the same tool", "no", "separate step", "no", "yes"),
            ("Mitigation testing in the same tool", "no", "re-run by a specialist", "limited", "yes"),
            ("Forecast-triggered runs", "no", "typically not", "no", "yes"),
            ("Model set-up by the user (mesh, roughness, boundaries)", "n/a", "yes", "little", "automatic defaults")]
    t.append(D.table("gap", "Characterisation of tool classes in general, to show the gap SAILAB addresses. It is not a feature audit of named products.",
                     ["Capability", "Static hazard maps", "Specialist hydraulic model", "Terrain screening (HAND)", "SAILAB"], rows,
                     note="The claims are general descriptions of how these classes of tool are typically used and were not verified product by product. SAILAB’s own entries are demonstrated in Sections 4–10."))
    t.append("<p>[[t:gap]] summarises the gap qualitatively. Integration is the contribution; the engine is established.</p>")
    return "\n".join(t)


def system(D, R):
    t = [D.h(2, "system", "System overview")]
    t.append(D.h(3, "four", "Four steps on one engine"))
    t.append("""<p>SAILAB organises work in four steps ([[f:workflow]]): simulate, quantify, protect and monitor. The user defines an area (a drawn polygon, an uploaded boundary, a tehsil or a river basin), configures a flood type and size, runs a preview and then a full simulation, and decides using depth, damage and loss outputs. Outputs are an interactive 3D view, maps, an animation, PDF reports and alerts.</p>""")
    t.append(D.fig("workflow", "fig_workflow.png", "The four-step SAILAB workflow over one shared data layer and one engine.", "96%"))
    t.append(D.h(3, "types", "Supported flood types"))
    t.append("""<p>Six forcing types are supported. Each reaches the same solver in one of two ways: water falls on every cell as rain, or it enters through a boundary as a discharge or a stage ([[t:types]]).</p>""")
    rows = [
        ("Intense rainfall", "Rain on every cell: triangular design storm (peak at mid-duration), uploaded gauge series, or a return-period storm", "ERA5-Land/IMERG intensity–duration curves; CMIP6 Gumbel fit; Open-Meteo forecast [[c:munoz2021,thrasher2022,openmeteo]]"),
        ("River overflow", "Inflow hydrograph on a boundary segment sized to the river’s channel width, placed where OpenStreetMap waterways cross the area", "OSM waterways; GloFAS discharge for context [[c:harrigan2020]]"),
        ("Glacial-lake outburst", "Triangular inflow hydrograph with a peak from a documented event or from an empirical volume relation", "PMD 2024 lake inventory, HydroLAKES, documented events [[c:huggel2002,messager2016]]"),
        ("Dam failure or release", "Breach hydrograph (peak from reservoir volume and dam height) or a documented spillway discharge, applied at the dam", "Global Dam Watch [[c:gdw]]; Froehlich [[c:froehlich1995]]"),
        ("Snowmelt", "Daily inflow series from a distributed degree-day snow and glacier melt model", "ERA5, SRTM, glacier outlines"),
        ("Sea (coastal) flooding", "Stage boundary on the sea edge: tide plus surge, optional wave run-up; or a static still-water level", "Karachi tide gauge; GTSR [[c:muis2016]]; Stockdon run-up [[c:stockdon2006]]")]
    t.append(D.table("types", "Flood types and how each forcing is applied to the solver.", ["Type", "How it enters the solver", "Main inputs"], rows))
    t.append(D.h(3, "data", "Preloaded data layer"))
    t.append("""<p>[[t:data]] and [[f:layers]] list the preloaded layers. Having exposure and hazard sources preloaded is what lets a user go from a drawn polygon to a loss figure without data preparation. Large national vector layers sit in a spatially indexed database (road queries took 0.2 s for a small box and 2.4 s for a box covering Islamabad and Rawalpindi, against 3 s and 41 s from the original files); the files remain as a fallback.</p>""")
    rows = [
        ("Terrain", "1 arc-second SRTM mosaic of Pakistan, resampled to a metric UTM grid (about 29 m)", "SRTM [[c:farr2007]]", "Surface model, not bare earth. Mission specification for absolute vertical error is 16 m (90 % confidence). A user-supplied DEM can be used instead; the platform does not fetch terrain from the internet and covers Pakistan only."),
        ("Buildings", "Fetched per area (446,787 footprints in a whole-Islamabad run)", "Google Open Buildings [[c:sirko2021]]", "No heights in the source. Height from OSM tags where present (about 2 % of buildings in a dense test area), otherwise 6 m."),
        ("Roads", "1,113,962 line segments", "OpenStreetMap, HOTOSM export of 9 July 2025 [[c:hotosm]]", "Inside Islamabad a drivable-roads view excludes footpaths and tracks."),
        ("Schools", "65,909", "Government shapefile supplied to the project " + todo("name the provider"), "Point locations."),
        ("Hospitals and health units", "35,561", "Government shapefile supplied to the project " + todo("name the provider"), "Point locations; 49 facility categories."),
        ("Dams and reservoirs", "101 dams, 86 reservoirs", "Global Dam Watch [[c:gdw]]", "23 dams have a recorded height; 64 get a height range estimated from reservoir depth; 14 are refused."),
        ("Glacial lakes", "3,378 lakes (PMD 2024); 953 HydroLAKES points", "PMD inventory; HydroLAKES [[c:messager2016]]", "Dam type and glacier relation recorded; volume from an area relation."),
        ("Land cover", "10 m", "ESA WorldCover [[c:zanaga2021]]", "Drives Manning’s n per cell and the nature-based options."),
        ("Soils", "250 m", "SoilGrids [[c:poggio2021]]", "Sand and clay give a texture class and a saturated conductivity [[c:rawls1983]]."),
        ("River basins and tehsils", "1,131 level-8 basins; 578 tehsils", "HydroSHEDS [[c:lehner2013]]; administrative boundaries", "Used to select an area by name."),
    ]
    t.append(D.table("data", "Preloaded data layers and their sources.", ["Layer", "Count or resolution", "Source", "Notes"], rows,
                     note="Counts are as stored in the platform at the time of writing."))
    t.append(D.fig("layers", "fig_data_layers.png", "Counts of preloaded features (log scale). Buildings are fetched per area and so are not shown.", "88%"))
    t.append(D.h(3, "arch", "Software and compute architecture"))
    t.append("""<p>SAILAB is a Python 3.12 web service built on Flask, with a browser front end on MapLibre/Mapbox GL for 2D and 3D maps. A run is a background job. Jobs share a single solver slot and queue in order; the queue position, the job that holds the slot and a cancel control are visible to the user. Results are cached by a hash of the area geometry, all parameters and a fingerprint of the data used, so an identical request returns in milliseconds and a change in any input produces a fresh run. The flood solver is run as a subprocess; national vector data are read from a PostGIS 3.6 database with the original files as fallback. Reports are rendered to PDF with WeasyPrint. Background agents check the health of the data sources every 15 minutes, score logged forecasts against later analyses and screen each finished run for implausible results (non-finite values, depth above 100 m, large mass-balance error, almost the whole domain flooded). There is no container image: the service runs from a Python virtual environment and is deployed by a patch package. A Python client package exposes the same runs to other systems.</p>
<p>All benchmarks in this paper ran on one NVIDIA GB10 (DGX Spark class, compute capability 12.1; driver 580.178.04, CUDA 13.0) on a 20-core ARM64 server shared with other users. The load average was about 30 during the benchmarks, so timings include that contention. Code state: commit <code>083ed51</code> of the project repository; evaluation scripts are in the <code>paper/</code> folder (Appendix [[s:repro]]).</p>""")
    return "\n".join(t)
