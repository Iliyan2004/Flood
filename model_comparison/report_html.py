import json, os, sys, numpy as np, datetime
CMP = os.path.expanduser("~/flood/cmp")
R = json.load(open(f"{CMP}/results_all.json")); X = json.load(open(f"{CMP}/results_extra.json")); F = json.load(open(f"{CMP}/figs.json"))
areas = list(R); M = ["SAILAB (LISFLOOD-FP ACC)", "ANUGA", "SFINCS", "TELEMAC-2D"]
S = {"SAILAB (LISFLOOD-FP ACC)": "SAILAB"}
import re as _re
try: E2E = json.load(open(f"{CMP}/results_e2e_sailab.json"))
except Exception: E2E = None
def survey_table():
    L = open(f"{CMP}/../SAILAB_Flood_Model_Survey.md").read().split("\n"); out = []; on = False
    for l in L:
        if l.startswith("| Model |"): on = True
        if on:
            if not l.startswith("|"): break
            out.append(l)
    rows = [[c.strip() for c in r.strip("|").split("|")] for r in out if not _re.match(r"^\|[-| ]+\|$", r)]
    h = "<table><tr>" + "".join(f"<th>{c}</th>" for c in rows[0]) + "</tr>"
    for r in rows[1:]: h += "<tr>" + "".join("<td>" + _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", c) + "</td>" for c in r) + "</tr>"
    return h + "</table>"
def loc(f): return sum(1 for _ in open(f"{CMP}/{f}"))
def mean(m, k): return float(np.mean([R[a][m][k] for a in areas]))
rt = X["runtimes"]
def img(k, w="100%"): return f'<img src="data:image/png;base64,{F[k]}" style="width:{w};max-width:100%">'
rows = ""
base = ["Flood everything", "Lowest terrain (oracle fraction)"]
hw = {"SAILAB": "GPU (NVIDIA GB10)", "ANUGA": "CPU, 90 m mesh", "SFINCS": "CPU, 20 threads, 29 m", "TELEMAC-2D": "CPU, 1 core, 90 m mesh"}
for m in base + M:
    nm = S.get(m, m); mm = nm in hw
    rtm = f"{np.mean([rt[a][nm] for a in areas]):.0f} s" if mm else "-"
    rows += f"<tr class={'base' if not mm else ''}><td>{nm}</td>" + "".join(f"<td>{R[a][m]['CSI']:.3f}</td>" for a in areas) + \
        f"<td><b>{mean(m,'CSI'):.3f}</b></td><td>{mean(m,'POD'):.2f}</td><td>{mean(m,'FAR'):.2f}</td><td>{mean(m,'area_ratio'):.2f}x</td><td>{rtm}</td><td>{hw.get(nm,'-')}</td></tr>"
untried = [
 ("HEC-RAS 2D", "Only Windows and x86 Linux builds exist; this server is ARM. A 2D case also needs a mesh/geometry file normally built in its Windows GUI.", "Free, but cannot run here. SAILAB runs on this machine with no licence or Windows."),
 ("TUFLOW (Classic/HPC/FV)", "Commercial licence required, Windows/x86.", "No licence cost for SAILAB's solver (GPL)."),
 ("MIKE 21 / MIKE+", "Commercial licence (DHI), Windows.", "Same."),
 ("InfoWorks ICM", "Commercial licence (Autodesk), Windows.", "Same."),
 ("Flood Modeller", "Commercial licence, Windows.", "Same."),
 ("FLO-2D", "Windows desktop program; Pro tier is paid.", "Same."),
 ("Delft3D-FM / D-Flow FM", "Official binaries are x86; no ARM package found.", "SAILAB is built from source for this ARM/GPU machine."),
 ("TRITON, SERGHEI, RIM2D, HiPIMS", "Need source builds; GPU support on this ARM GPU is untested. Not attempted for time.", "Not a SAILAB advantage: untested."),
 ("Fathom, Google Flood Hub, GloFAS", "Data products or services, not runnable models.", "Published scores only (see survey)."),
]
ut = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in untried)
NEW78 = f"""
<h2 class="pb">7. Time from question to result</h2>
<p>Solver run time is only part of the work. The tools differ more in what a user must prepare before the solver can start.</p>
<h3>What SAILAB took, measured</h3>
<p>Drawing a 13 km area and pressing run, with everything fetched and processed automatically (elevation, land-cover friction, buildings burned in as obstacles, roads, facilities, flood solver, building and road risk, estimated loss, 3D scene), on the live server:</p>
<table><tr><th>Area</th><th>Total, polygon to finished result</th><th>Of which flood solver</th><th>Buildings used</th></tr>
{"".join(f"<tr><td>{n}</td><td><b>{v['total_s']:.0f} s</b></td><td>{v['wall_clock_solver_s']:.0f} s</td><td>{v['buildings_used']:,}</td></tr>" for n, v in E2E.items())}
</table>
<p class="cap">Measured 31 Oct 2026, fresh runs with no cache, 48 h storm. No files were prepared by hand.</p>
<h3>What the other models needed from me, measured</h3>
<p>For the three open models I ran, I had to write the converters myself, because none of them reads a DEM and a rainfall total directly. Script lengths, excluding the shared scoring code:</p>
<table><tr><th>Model</th><th>Lines of code I wrote</th><th>What it had to do</th></tr>
<tr><td>SAILAB</td><td><b>0</b></td><td>Polygon in, result out.</td></tr>
<tr><td>ANUGA</td><td>{loc('run_anuga.py')}</td><td>Build a triangular mesh, interpolate elevation onto it, set friction, boundaries and a rain function, collect maximum depth.</td></tr>
<tr><td>SFINCS</td><td>{loc('run_sfincs.py')}</td><td>Write elevation and mask grids, a rainfall table and the input file; read the result back.</td></tr>
<tr><td>TELEMAC-2D</td><td>{loc('run_telemac.py')}</td><td>Write a mesh file in its binary format and a boundary file by hand, write the steering file, read the result back.</td></tr></table>
<p class="cap">Each also needed its own installation, and I hit and fixed a wrong-package install (SFINCS), a parallel-build race, a missing output keyword and a wrong output setting along the way. That is not a fair measure of an expert's time, but it shows these tools start from a terrain file and little else.</p>
<h3>What the commercial tools typically require (general knowledge, not measured here)</h3>
<table><tr><th>Input</th><th>HEC-RAS 2D, TUFLOW, MIKE and similar</th><th>SAILAB</th></tr>
<tr><td>Terrain</td><td>User sources, clips and loads a DEM</td><td>Built in (Pakistan SRTM); cropped to the drawn polygon</td></tr>
<tr><td>Mesh or grid</td><td>User builds and checks it</td><td>Automatic</td></tr>
<tr><td>Roughness</td><td>User maps land cover to Manning values</td><td>Automatic from ESA WorldCover</td></tr>
<tr><td>Buildings, roads, facilities</td><td>Separate datasets, collected and joined by the user</td><td>Built in (Google Open Buildings, road and facility layers)</td></tr>
<tr><td>Rainfall and inflow</td><td>User prepares time series</td><td>Entered, uploaded, or taken from forecasts and design-storm data</td></tr>
<tr><td>Boundary conditions</td><td>User defines them</td><td>Defaults, or river detection and inflow tools</td></tr>
<tr><td>Damage and loss</td><td>Separate tool or spreadsheet</td><td>Built in</td></tr></table>
<p class="cap">I did not time these workflows and have no measured figure for how long they take; the point is the number of separate steps, not hours.</p>

<h2>8. Hardware and GPU support</h2>
<table><tr><th>Model</th><th>GPU support</th><th>Basis</th></tr>
<tr><td><b>SAILAB (LISFLOOD-FP)</b></td><td><b>Yes, CUDA.</b> Runs on the NVIDIA GB10 in this server.</td><td>Measured: 51 to 71 s per area on the GPU</td></tr>
<tr><td>LISFLOOD-FP (published)</td><td>Yes; the uniform solver is 2 to 28 times faster than its CPU version, the non-uniform solver up to 320 times</td><td><a href="https://gmd.copernicus.org/articles/16/2391/2023/">LISFLOOD-FP 8.1 paper, Geoscientific Model Development 2023</a></td></tr>
<tr><td>HEC-RAS 2D</td><td>No GPU support found in the public release notes I could access (not confirmed against the official HEC notes)</td><td>Search, 31 Oct 2026</td></tr>
<tr><td>TUFLOW</td><td>Yes (TUFLOW GPU / HPC)</td><td>Listed in the UK Environment Agency 2013 benchmark</td></tr>
<tr><td>TRITON, SERGHEI, RIM2D, HiPIMS</td><td>Yes (multi-GPU)</td><td>Published papers; not run here</td></tr>
<tr><td>SRH-2D</td><td>Yes, new in 2026; average speedup about 7.7x in one deck</td><td><a href="https://aquaveo.com/media/wysiwyg/transportation-hydraulics/Webinar-Resources/2026-05-13%20Accelerating%202D%20Hydraulic%20Modeling%20GPU%20enabled%20SRH-2D%20in%20SMS.pdf">Aquaveo webinar</a></td></tr>
<tr><td>ANUGA, TELEMAC-2D, SFINCS (as run here)</td><td>CPU in this study (TELEMAC and ANUGA builds are CPU)</td><td>Measured: 89 to 767 s per area</td></tr></table>
<p>SAILAB's speed in section 3 therefore compares a GPU solver with CPU solvers. That is a real advantage of the product on the hardware it was built for, not proof that the underlying numerical scheme is faster. Models that were not run (HEC-RAS and others) were not timed, so no speed claim is made against them.</p>
"""
NEW11 = f"""<h2 class="pb">11. Full survey table</h2>
<p>All the models considered, with whether each fact came from a source retrieved during this work (S) or from general knowledge not re-verified (K).</p>
{survey_table()}
"""
html = f"""<!doctype html><html><head><meta charset="utf-8"><title>SAILAB multi-model flood case study</title>
<style>
body{{font-family:'DejaVu Sans',Arial,sans-serif;color:#1b2a2f;max-width:900px;margin:24px auto;padding:0 18px;line-height:1.45;font-size:13px}}
h1{{font-size:24px;margin:0 0 2px}} h2{{font-size:17px;margin:28px 0 8px;border-bottom:2px solid #0b7a75;padding-bottom:3px}} h3{{font-size:14px;margin:18px 0 6px}}
.band{{background:#0b3d40;color:#fff;padding:18px 22px;border-radius:4px}} .band p{{margin:4px 0;color:#cfe8e6}}
table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:12px}} th,td{{border:1px solid #cfd8d7;padding:5px 7px;text-align:left}} th{{background:#e8f1f0}}
tr.base td{{color:#69757a;font-style:italic}} .box{{background:#f2f7f6;border-left:4px solid #0b7a75;padding:10px 14px;margin:10px 0}}
.warn{{background:#fdf3e3;border-left:4px solid #d9a400;padding:10px 14px;margin:10px 0}} .cap{{font-size:11px;color:#59676b;margin:2px 0 10px}}
.key span{{display:inline-block;width:12px;height:12px;margin:0 4px -2px 10px}} .small{{font-size:11px;color:#59676b}}
@media print{{.pb{{page-break-before:always}}}}
</style></head><body>
<div class="band"><h1>SAILAB against other flood models: a controlled case study</h1>
<p>Same terrain, rainfall, friction and scoring for every model, scored against the UNOSAT satellite flood map of the 2022 Sindh floods.</p>
<p>Generated {datetime.date.today():%d %B %Y}. SAILAB v6.1, LISFLOOD-FP 8.2.</p></div>

<h2>1. What this shows, in plain terms</h2>
<div class="box"><b>SAILAB's accuracy is on par with the other open 2D flood models, not better.</b>
Across three test areas SAILAB's mean CSI is {mean(M[0],'CSI'):.3f}, against {mean('ANUGA','CSI'):.3f} (ANUGA), {mean('TELEMAC-2D','CSI'):.3f} (TELEMAC-2D) and {mean('SFINCS','CSI'):.3f} (SFINCS). The differences are about 0.01 to 0.03 and the three areas disagree on the ordering, so there is no winner on accuracy.
SAILAB and ANUGA flooded almost the same cells (94% overlap), and TELEMAC-2D matched SAILAB at 90%. Independent codes agreeing this closely is good evidence that SAILAB's solver is correct.</div>
<div class="warn"><b>No model, including SAILAB, scored clearly better than the trivial "flood everything" guess</b> (mean CSI {mean('Flood everything','CSI'):.3f}). Raising the wet-depth cut-off makes every model score worse (section 5). The most likely reason is the forcing, not the models: the 2022 Sindh flood was mostly river overflow and upstream water, and this test applies only local rainfall. This has not been tested directly.</div>
<p><b>Where SAILAB does have a measured advantage:</b></p>
<ul>
<li><b>Speed.</b> On the same cases SAILAB's GPU solver took {np.mean([rt[a]['SAILAB'] for a in areas]):.0f} s on average, against {np.mean([rt[a]['TELEMAC-2D'] for a in areas]):.0f} s (TELEMAC-2D), {np.mean([rt[a]['SFINCS'] for a in areas]):.0f} s (SFINCS) and {np.mean([rt[a]['ANUGA'] for a in areas]):.0f} s (ANUGA). The comparison is GPU against CPU, ANUGA and TELEMAC used a coarser 90 m mesh, and TELEMAC ran on one core, so it shows what a user gets, not a like-for-like solver speed test.</li>
<li><b>Time from question to result.</b> From a drawn polygon to a finished result with buildings, roads, risk and loss took SAILAB {min(v['total_s'] for v in E2E.values()):.0f} to {max(v['total_s'] for v in E2E.values()):.0f} seconds with nothing prepared by hand (section 7). The other open models needed hand-written converters of 59 to 114 lines each.</li>
<li><b>GPU.</b> SAILAB's solver runs on the GPU. No GPU support for HEC-RAS 2D was found in the public release notes I could read (section 8).</li>
<li><b>Integration and cost.</b> SAILAB runs the whole workflow (rainfall, buildings, roads, loss, scenarios, monitoring) on one machine with no licence fee. None of the other models do that on their own.</li>
<li><b>Portability.</b> SAILAB's solver runs on this ARM GPU server; the commercial and Windows tools in section 9 could not be run here at all.</li>
</ul>
<p><b>Where it is not ahead:</b> accuracy (parity), depth validation (none for any model here), and the volume of published validation behind HEC-RAS, TUFLOW, MIKE and others.</p>

<h2>2. Study design</h2>
<ul>
<li><b>Event and observed map:</b> the 2022 Pakistan floods, Sindh. Observed flood extent from UNOSAT (VIIRS satellite, 29 November 2022). The map is from three months after the August peak, so it shows water that persisted, not the rain-driven peak.</li>
<li><b>Areas:</b> three 13 km boxes placed along the edge of the observed flood so each is about half flooded (49%, 50%, 51%). An earlier test box that was 98% flooded was dropped because it cannot show skill.</li>
<li><b>Same inputs for every model:</b> SRTM elevation (29 m), 253.5 mm of rain over 48 hours (the wettest 48-hour ERA5 window at the site), Manning n = 0.05, no infiltration, closed boundaries, nothing calibrated.</li>
<li><b>Scoring:</b> cells counted wet above 5 cm peak depth. CSI = hits / (hits + misses + false alarms); POD = share of observed flooded cells found; FAR = share of predicted wet cells that were dry in the observation.</li>
<li><b>Baselines:</b> "Flood everything", and "lowest terrain", which floods the lowest cells up to the true flooded fraction (it is told the answer's size).</li>
<li><b>Models run:</b> SAILAB (LISFLOOD-FP 8.2, ACC solver, GPU); ANUGA 4.1 (full shallow-water, finite volume, 90 m mesh); SFINCS (Deltares, local-inertial, built from source); TELEMAC-2D (full shallow-water, finite element, built from source, 90 m mesh, constant rain at the same total).</li>
</ul>

<h2>3. Results</h2>
<table><tr><th>Model</th><th>CSI A</th><th>CSI B</th><th>CSI C</th><th>Mean CSI</th><th>POD</th><th>FAR</th><th>Wet area / observed</th><th>Mean run time</th><th>Hardware</th></tr>{rows}</table>
<p class="cap">Areas: A Manchar edge, B north Dadu, C Shikarpur. Italic rows are the trivial baselines. Wet area / observed above 1 means over-prediction.</p>
{img('csi','88%')}<p class="cap">Figure 1. CSI by area. The grey bars are the baselines.</p>
{img('podfar')}<p class="cap">Figure 2. Detection against false alarms. All four models find 83 to 100% of the flooded cells; SFINCS reaches 100% only because it reports more than 5 cm of water in every cell.</p>

<h2 class="pb">4. Maps</h2>
<p class="key">Key: <span style="background:#2b7a4b"></span>correctly wet &nbsp;<span style="background:#d9a400"></span>missed &nbsp;<span style="background:#c0392b"></span>false alarm &nbsp;<span style="background:#e9ecef;border:1px solid #bbb"></span>correctly dry</p>
<h3>Area A: Manchar edge</h3>{img('map_A_manchar_edge')}
<h3>Area B: north Dadu</h3>{img('map_B_north_dadu')}
<h3>Area C: Shikarpur</h3>{img('map_C_shikarpur')}
<p class="cap">Figure 3. Observed flooding (left, blue) and each model's result against it. All four models flood the low western strip of area A, which the satellite map shows as dry.</p>
<h3>Peak depth, area A</h3>{img('depthA')}<p class="cap">Figure 4. Peak depth from each model (same scale). The patterns are alike; maximum depths are 4.1 to 4.5 m.</p>

<h2 class="pb">5. How robust is the result</h2>
{img('sens','60%')}<p class="cap">Figure 5. Mean CSI as the wet-depth cut-off rises. Every model falls below "flood everything" at every cut-off from 10 cm up, and below both baselines from 20 cm up (SAILAB beats "flood everything" only at 5 cm, by 0.004), so the models' depth patterns do not line up with the observed extent. A model that is right for the right reasons would hold up better as the cut-off rises.</p>
{img('agree','46%')}<p class="cap">Figure 6. How much the models' flooded cells overlap each other (1 = identical). SAILAB, ANUGA and TELEMAC-2D agree at 0.90 to 0.94; SFINCS differs most because it keeps a thin layer of water everywhere.</p>
{img('rt','60%')}<p class="cap">Figure 7. Run times (log scale). Hardware differs between models, see section 1.</p>

<h2>6. What the evidence supports</h2>
<ul>
<li>SAILAB's solver gives the same flood as independent codes on the same inputs: this is the strongest finding, and it supports the correctness of the numerics.</li>
<li>It cannot yet claim better real-world accuracy than the other models, and this test alone cannot show whether any of them has real skill on this flood.</li>
<li>SAILAB ran about 2 to 9 times faster than the CPU models on these cases (TELEMAC-2D 2.4x, ANUGA 8x, SFINCS 9x on average), with the hardware caveat above.</li>
</ul>
<div class="warn"><b>Limits of this study.</b> One event; three small areas; observed map three months after the peak; rain-only forcing that cannot represent river overflow; no depth data to compare against; meshes of 29 m and 90 m differ between models; each model's own numerical settings were left at defaults; the area-A west strip may reflect SRTM elevation error. None of these favour or disadvantage SAILAB specifically.</div>

{NEW78}<h2>9. Models not run, and why</h2>
<table><tr><th>Model</th><th>Why it was not run</th><th>What that means for SAILAB</th></tr>{ut}</table>
<p>Not running a model is not evidence that it is worse or better than SAILAB. What it does show is a practical difference: SAILAB's solver is open and runs on this ARM GPU server without a licence or Windows, while most of the established tools need a licence, Windows or x86 hardware. For these models the survey (SAILAB_Flood_Model_Survey.md) lists published validation results instead; HEC-RAS, for example, has been benchmarked against LISFLOOD-FP (the engine inside SAILAB) on the Secchia River flood, where both mis-estimated extent and depth in built-up areas.</p>


<h2>10. What published studies found when they compared HEC-RAS, LISFLOOD-FP and SWAT</h2>
<p>SAILAB's flood engine is LISFLOOD-FP, so published HEC-RAS versus LISFLOOD-FP studies are the most relevant outside evidence. They use different events, metrics and settings, so their numbers cannot be compared with this report's CSI. "Checked" means I read the paper's own text; the rest rest on the abstract or a search summary and should be checked before quoting.</p>
<table><tr><th>Study</th><th>Models and site</th><th>What it found</th><th>Checked</th></tr>
<tr><td><a href="https://research-information.bris.ac.uk/en/publications/comparing-2d-capabilities-of-hec-ras-and-lisflood-fp-on-complex-t/">Shustikova et al. 2019</a>, Hydrological Sciences Journal</td><td>HEC-RAS 5.0.3 (2D) vs LISFLOOD-FP, Secchia River, Italy, 25 to 100 m grids</td><td>25 m grids were best for both; 50 and 100 m performed nearly the same. Both mis-estimated extent and depth in built-up areas.</td><td>Abstract</td></tr>
<tr><td><a href="https://www.aprh.pt/rh/pdf/rh34_n1-5.pdf">Fernandez and Mourato</a>, River Lis, Portugal</td><td>HEC-RAS vs LISFLOOD-FP, 4 km reach, calibrated on the October 2006 flood, driven by HEC-HMS</td><td>For the 100-year flood the two models' inundated areas overlapped by 65%. HEC-RAS flood-wave travel time was about half LISFLOOD-FP's, and LISFLOOD-FP took much longer to run.</td><td>Yes</td></tr>
<tr><td><a href="https://www.itia.ntua.gr/en/getfile/1337/1/documents/FloodplainMapping_pr.pdf">Oikonomou et al.</a>, Thessaly, Greece</td><td>HEC-RAS (1D), LISFLOOD-FP and FLO-2D (quasi-2D), Penios basin</td><td>Run times were about 3 h (LISFLOOD-FP), 2 h (FLO-2D) and 10 min (HEC-RAS). Outflow depth in HEC-RAS varied from 6.05 to 19.47 m as floodplain roughness went from 0.03 to 0.3, against 5.65 to 8.31 m for LISFLOOD-FP.</td><td>Yes</td></tr>
<tr><td>Gorgan Plain, Iran (via <a href="https://mmust.academia.edu/MicahMukunguMukolwe">author page</a>)</td><td>1D HEC-RAS vs 2D LISFLOOD-FP against aerial imagery</td><td>F-statistic 0.69 (LISFLOOD-FP) vs 0.67 (HEC-RAS); LISFLOOD-FP slightly better in validation.</td><td>No</td></tr>
<tr><td><a href="https://repository.geologyscience.ru/items/fba9c55e-7e5c-476c-9985-86c0a3586af3/full">Horritt and Bates 2002</a>, River Severn, UK</td><td>HEC-RAS, TELEMAC-2D, LISFLOOD-FP</td><td>HEC-RAS and TELEMAC-2D could be calibrated to give good predictions; LISFLOOD-FP needed independent inundated-area data to perform acceptably.</td><td>No</td></tr>
<tr><td><a href="https://meetingorganizer.copernicus.org/EGU2009/EGU2009-7900-2.pdf">EGU 2009 abstract</a>, River Alzette, Luxembourg</td><td>HEC-RAS vs LISFLOOD-FP</td><td>LISFLOOD-FP represented complex floodplain flow well but ran slower; HEC-RAS described in-channel flow in more detail and ran much faster.</td><td>Abstract</td></tr>
<tr><td>HEC-RAS on the Indus: <a href="https://doaj.org/article/817855fa175a44ae9fa803ad31eecafc">Naeem et al. 2021</a> (Tori levee breach, 2010) and other studies</td><td>HEC-RAS only, no LISFLOOD-FP</td><td>2D HEC-RAS matched observed stages within about 0.13 to -0.54 m and 84% of maximum inundation area for the 2010 breach. A 1D model of the Chashma to Kot Mithon reach reported NSE 0.92 to 0.95.</td><td>No</td></tr>
</table>
<h3>SWAT</h3>
<p>I found no study comparing SWAT with LISFLOOD or LISFLOOD-FP. They answer different questions: SWAT is a catchment water-balance model that predicts river flow, not flood extent, and "LISFLOOD" (the JRC hydrological model) is a different program from LISFLOOD-FP. Related results found: SWAT against HEC-HMS on the Kalu River, Sri Lanka (SWAT NSE 0.87 calibration and 0.66 validation; HEC-HMS 0.91 and 0.56); SWAT on the Upper Indus Basin (calibration NSE 0.81 to 0.86, doing worse than a neural-network model); and JRC LISFLOOD on the Wei River, China (daily NSE 0.69, monthly 0.79). All of these come from search summaries and are unchecked. SAILAB's closest counterpart to a catchment model is its snowmelt module, which scored KGE 0.86 against GloFAS on the Hunza basin with default settings; GloFAS is itself a model, not gauge data.</p>
<div class="box"><b>What the literature adds.</b> Published comparisons tend to find HEC-RAS much faster and more detailed in the channel, and LISFLOOD-FP better at spreading water across the floodplain, with HEC-RAS showing larger sensitivity to floodplain roughness in one study. None found one clearly more accurate than the other overall. That fits this report's own result: SAILAB's LISFLOOD-FP engine performs about like other 2D models, and nothing published or measured here shows it ahead on accuracy.</div>

{NEW11}<h2>12. Recommended next steps to make the accuracy case</h2>
<ol>
<li>Score an event where the flood can be reproduced from its drivers: known river inflow (for example the 2010 Indus flood with gauge hydrographs), or a rain-driven urban flood with a mapped extent.</li>
<li>Add observed depths (high-water marks, gauges) so depth can be scored, not just extent.</li>
<li>Run SAILAB with river inflow from GloFAS on the 2022 areas, to test the river-overflow explanation.</li>
<li>Run HEC-RAS or TUFLOW on the same three areas on a licensed x86 machine, and score with the same code (inputs are available).</li>
<li>Try 8.3 of LISFLOOD-FP on a multi-million-cell basin; on our test cases it matched 8.2 and showed no speed gain.</li>
</ol>

<h2>13. Reproducibility</h2>
<p class="small">All code and inputs are in <code>~/flood/cmp</code>: <code>case_common.py</code> (shared DEM, observed map and scoring), <code>run_baselines_sailab.py</code>, <code>run_anuga.py</code>, <code>run_sfincs.py</code>, <code>run_telemac.py</code>, <code>score_all.py</code>, <code>report_build.py</code>. Raw numbers: <code>results_all.json</code>, <code>results_extra.json</code>. Models: SAILAB/LISFLOOD-FP 8.2; ANUGA 4.1.0 (pip); SFINCS v2.5.0-beta (Deltares, built from source 31 Oct 2026); TELEMAC v9.2-dev (built from source, serial). The first SFINCS attempt used a conda package with the same name that turned out to be a plasma-physics code; it was removed and no result came from it. The earlier SAILAB test reporting CSI 0.66 used a box that was 98% flooded and should not be quoted as accuracy.</p>
</body></html>"""
open(f"{CMP}/SAILAB_Model_Comparison_Report.html", "w").write(html)
print("html bytes", len(html))
