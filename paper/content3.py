"""Sections 8-12 and back matter."""
from doc import todo
import study as S


def evaluation(D, R):
    t = [D.h(2, "eval", "Evaluation: solver verification and a skill test on the 2022 Sindh flood")]
    t.append("""<p>We ask two separate questions. First, does SAILAB’s solver give the same answer as independent flood models fed the same inputs? That tests correctness of the numerics. Second, does the simulated extent match what a satellite saw? That tests predictive skill. The two have different answers.</p>""")
    t.append(D.h(3, "design", "Design"))
    t.append("""<p>We simulated the 2022 Sindh flood and scored the result against the flood extent that UNOSAT derived from VIIRS imagery of 29 November 2022 (product FL20221121PAK) [[c:unosat2022]]. The product is a binary flood-water polygon layer with no depths, about 4,663 km² within a 46,150 km² analysis extent, observed three months after the August peak, so it shows water that persisted. VIIRS pixels are about 375 m, so the observed mask is blocky next to a 29 m model ([[f:maps]]).</p>
<ul>
<li><b>Areas.</b> Three 13 km boxes placed on the edge of the observed flood so that each is about half flooded (49, 50.5 and 51&nbsp;%; [[f:study]]). Placing them on the edge is what makes the test discriminating: a box that is almost entirely flooded or dry cannot show skill.</li>
<li><b>Inputs, identical for every model.</b> SRTM terrain (29 m), 253.5 mm of rain over 48 hours (the wettest 48-hour window of the 2022 monsoon in ERA5 at a site in the Manchar–Dadu belt [[c:hersbach2020]], applied to all three areas as a triangular storm with peak 10.56 mm/h), Manning’s <i>n</i> = 0.05, no infiltration, closed boundaries, no buildings. <b>Nothing was calibrated</b> to the observed extent: these values were fixed in advance.</li>
<li><b>Scoring.</b> A cell is wet above 5 cm of peak depth. We report CSI, POD and FAR (Equations below), and threshold-free measures (Section [[s:skill]]).</li>
<li><b>Baselines.</b> “Flood everything”, and “lowest terrain”, which floods the lowest cells up to the observed flooded fraction (it is told the answer’s size).</li>
<li><b>Models.</b> SAILAB (LISFLOOD-FP 8.2, local-inertial, GPU); ANUGA 4.1 (full equations, finite volume, 90 m mesh) [[c:anuga]]; SFINCS (local-inertial, 29 m) [[c:leijnse2021]]; TELEMAC-2D (full equations, finite element, 90 m mesh, one core) [[c:hervouet2007]].</li>
</ul>""")
    t.append(D.fig("study", "fig_study_area.png", "The three test areas on the observed flood extent used for scoring (UNOSAT, VIIRS, 29 November 2022).", "64%"))
    t.append(D.h(3, "withdrawn", "A withdrawn result"))
    t.append("""<p>Draft v0.1 of this paper reported CSI 0.659, POD 0.669 and FAR 0.021 for one 11 km box centred on the largest contiguous flooded polygon, and described it as good agreement. We withdraw that interpretation. In that box 98.1&nbsp;% of the area is flooded in the observation, so a forecast of “flood everywhere” would have scored POD 1.0 and CSI 0.98; SAILAB’s 0.659 was <em>worse</em> than the trivial forecast. A box that is nearly all flooded cannot show skill (Appendix [[s:metrics]]). The same run also used half the intended rainfall (127 mm, because the average rate had been passed as the triangle’s peak); the figures below use the corrected storm.</p>""")
    t.append(D.h(3, "metr", "Metrics"))
    t.append("""<p>With <i>TP</i>, <i>FP</i> and <i>FN</i> the numbers of correctly flooded, wrongly flooded and missed cells,</p>""")
    t.append(D.eq("csi", r"\mathrm{POD}=\frac{TP}{TP+FN},\qquad \mathrm{FAR}=\frac{FP}{TP+FP},\qquad \mathrm{CSI}=\frac{TP}{TP+FP+FN}"))
    t.append("""<p>CSI ignores the dry-in-both class, which would otherwise dominate, but it depends strongly on how much of the area is flooded: a forecast of flooding everywhere scores CSI equal to the flooded fraction. We therefore also report the Peirce and Heidke skill scores, which are zero for any forecast without information, and the area under the ROC curve (AUC) of the continuous peak depth as a predictor of the observed extent, which needs no depth threshold (0.5 is chance).</p>""")
    t.append(D.h(3, "results", "Solver agreement and scores"))
    rows = [(r[0], f"{r[1]:.3f}", f"{r[2]:.3f}", f"{r[3]:.3f}", f"{r[4]:.3f}", f"{r[5]:.2f}", f"{r[6]:.2f}", f"{r[7]:.2f}×", ("–" if r[8] is None else f"{r[8]} s"), r[9]) for r in S.ROWS]
    t.append(D.table("study", "Scores against the UNOSAT extent, three 13 km areas (A Manchar edge, B north Dadu, C Shikarpur).",
                     ["Model", "CSI A", "CSI B", "CSI C", "Mean CSI", "POD", "FAR", "Wet area / observed", "Mean run time", "Hardware"], rows,
                     numcols=(1, 2, 3, 4, 5, 6, 7, 8), hl=(2,),
                     note="Wet = peak depth above 5 cm. Observed flooded fraction: 0.486, 0.505, 0.509. Wet area / observed above 1 means over-prediction. SFINCS scores as “flood everything” because it keeps more than 5 cm of water in every cell. Run time is solver time on different hardware and meshes, so it is not a like-for-like speed test. Source: study of 31 October 2026."))
    t.append(D.fig("csi", "fig_csi_models.png", "CSI by area for each model and for the two baselines. The dotted line is the “flood everything” score.", "100%"))
    ov = S.OVERLAP
    t.append(f"""<p><b>The solver agrees with independent codes.</b> SAILAB, ANUGA and TELEMAC-2D, which solve different equations on different meshes with different numerics, flood nearly the same cells: the overlap (intersection over union of the wet cells) is {ov[('SAILAB','ANUGA')]:.2f} between SAILAB and ANUGA and {ov[('SAILAB','TELEMAC-2D')]:.2f} between SAILAB and TELEMAC-2D; SFINCS differs most ({ov[('SAILAB','SFINCS')]:.2f} with SAILAB) because it holds a thin film of water everywhere. Maximum depths are 4.1 to 4.5 m for all four. This is the strongest finding of the study: the numerics are consistent with accepted solvers.</p>
<p><b>None of them beats the trivial forecast.</b> SAILAB’s mean CSI of {S.ROWS[2][4]:.3f} exceeds “flood everything” ({S.ROWS[0][4]:.3f}) by 0.004, which is within the spread between areas (it wins in A and B and loses in C), and every model falls below “flood everything” once the wet threshold reaches 10 cm and below both baselines from 20 cm ([[f:cutoff]]). The differences between models (0.497 to 0.504) are smaller than the differences between areas.</p>""")
    t.append(D.fig("maps", "fig_validation_maps.png", "Observed flood (top) and SAILAB against it (bottom) for the three areas. The model floods the lowest ground as water would, including a long strip along the western edge of A that the satellite map shows dry; observed flooding also lies on ground the model keeps dry.", "100%"))
    t.append(D.fig("cutoff", "fig_cutoff.png", "Mean CSI as the depth above which a cell counts as wet is raised. A model whose deepest water sat where the observed water stood would hold up better.", "70%"))
    t.append(D.h(3, "skill", "Skill beyond CSI"))
    sk = R["skill"]
    keys = [("SAILAB", "SAILAB"), ("ANUGA", "ANUGA"), ("TELEMAC-2D", "TELEMAC-2D"), ("SFINCS", "SFINCS"), ("Terrain only (-elevation)", "Elevation alone")]
    rows = []
    for k, lab in keys:
        row = [lab] + [f"{sk[a][k]['AUC']:.3f}" for a in "A_manchar_edge B_north_dadu C_shikarpur".split()]
        row.append(f"{sum(sk[a][k]['AUC'] for a in sk) / 3:.3f}")
        pss = [sk[a][k].get("PSS") for a in sk]
        row.append("–" if pss[0] is None else f"{sum(pss) / 3:.2f}")
        rows.append(row)
    t.append(D.table("auc", "Threshold-free skill of simulated peak depth against the observed extent. “Elevation alone” ranks cells by how low they lie.",
                     ["Predictor", "AUC A", "AUC B", "AUC C", "Mean AUC", "Mean Peirce score at 5 cm"], rows, numcols=(1, 2, 3, 4, 5), hl=(0,),
                     note="AUC 0.5 is chance. The Peirce score is 0 for “flood everything”, which is why SFINCS scores 0.00."))
    t.append(D.fig("skill", "fig_skill_auc.png", "ROC AUC of each model’s peak depth against the observed extent, with elevation alone as a predictor.", "88%"))
    t.append(f"""<p>Ranking cells by simulated depth gives AUC {sk['A_manchar_edge']['SAILAB']['AUC']:.2f}, {sk['B_north_dadu']['SAILAB']['AUC']:.2f} and {sk['C_shikarpur']['SAILAB']['AUC']:.2f}; ranking them by elevation alone gives {sk['A_manchar_edge']['Terrain only (-elevation)']['AUC']:.2f}, {sk['B_north_dadu']['Terrain only (-elevation)']['AUC']:.2f} and {sk['C_shikarpur']['Terrain only (-elevation)']['AUC']:.2f}. In area B there is real information (0.73) but elevation alone carries all of it; in A it is weak; in C every model, and elevation, is below chance, meaning that lower ground was <em>more</em> often dry than flooded in the observation. With rainfall as the only forcing, the simulated depth pattern is essentially the terrain’s elevation pattern, and it adds no information about where the observed water stood. The Peirce scores of the three models that resolve the extent (0.02 to 0.24 at 5 cm) say the same.</p>""")
    t.append(D.h(3, "misses", "Where the model misses, and why we think so"))
    t.append("""<p>We have not isolated the cause, and list candidates in order of how much we suspect them. (1) <em>Forcing.</em> Studies attribute the 2022 event mainly to multiday extreme rainfall over a very large area [[c:nanditha2023]]; much of that water reached Sindh as river flow and standing water rather than as local rain, and a simulation with local rain only cannot represent water that arrives down the Indus system or through breaches. (This is our reading, not a finding of those studies.) The reanalysis rainfall of 253.5 mm is also a coarse ERA5 value. In the GloFAS reanalysis the nearest river cells to our earlier test area carry up to about 17,000 m³/s on 17–31 August 2022 [[c:harrigan2020]]. We did not impose any inflow here, so this is untested. (2) <em>Observation.</em> The extent was seen three months after the peak, at 375 m, and can include irrigated or ponded land that no storm model would flood. (3) <em>Terrain.</em> SRTM is a surface model with metre-scale errors in flat delta land, enough to decide whether land floods; the dry strip in area A is one candidate. (4) <em>Missing structures.</em> Canals, embankments and barrages are not in the terrain. Because the models agree with each other, none of these points to a defect in one solver; they point to the inputs.</p>""")
    t.append(D.h(3, "sens", "Sensitivity analysis"))
    s = R.get("sens")
    if s:
        import content4 as c4
        t.append(c4.sens_block(D, s))
    else:
        t.append(f"""<p>The wet-depth threshold is the one sensitivity available to us at the time of writing ([[f:cutoff]]): scores fall steeply as it rises. {todo('rainfall scale, Manning n, infiltration, open boundaries and a longer storm window: runs prepared in paper/run_sensitivity.py; not yet run because the shared drive holding the solver and terrain became unavailable')}</p>""")
    t.append(D.h(3, "pvf", "Preview against the full run"))
    t.append("""<p>[[t:prev]] and [[f:preview]] in Section [[s:preview]] give the cost of speed measured on 315 terrain windows: the preview finds most flooded cells but flags too much and overstates depth. We did not score the preview against UNOSAT on the three Sindh areas.</p>""")
    t.append(D.h(3, "events", "Additional events"))
    t.append("""<p>This is one event, one observed product and one forcing dataset. We did not validate on a second event. The 2010 Indus flood and the 2022 Balochistan flash floods are candidates, and the absence of a second event is the main gap in the evidence.</p>""")
    return "\n".join(t)


def perf(D, R):
    t = [D.h(2, "perf", "Performance")]
    sc = R["scaling"]["runs"]
    by = {}
    for r in sc:
        by.setdefault(r["cells"], {})[r["device"]] = r
    rows = []
    for c in sorted(by):
        g = by[c].get("gpu"); cpu = by[c].get("cpu")
        km = g["km"]
        rows.append((f"{km} × {km}", f"{c:,}", f"{g['solver_wall_s']:.1f}", ("–" if not cpu else f"{cpu['solver_wall_s']:.1f}"),
                     ("–" if not cpu else f"{cpu['solver_wall_s'] / g['solver_wall_s']:.1f}×"), f"{g['total_wall_s']:.1f}", f"{g['peak_rss_mb']:.0f}"))
    t.append("""<p>The two-stage design rests on timing ([[t:scale]], [[f:runtime]]). We timed the solver alone on square crops of the real terrain around Lahore, the default storm of the platform (40 mm/h peak, 2 h; 4 h simulated), with per-step output off, on the GPU and on one CPU thread.</p>""")
    t.append(D.table("scale", "Solver runtime against domain size, GPU against one CPU thread.", ["Box (km)", "Cells", "GPU solver (s)", "CPU solver (s)", "GPU speed-up", "GPU total incl. start-up (s)", "Peak host memory (MB)"], rows,
                     numcols=(1, 2, 3, 4, 5, 6), note="One run per cell, on a shared server with load average about 30. “Peak host memory” is the resident set of the process; GPU memory use was not measured. CPU runs above 0.7 million cells were skipped because one thread would take too long."))
    t.append(D.fig("runtime", "fig_runtime.png", "Solver runtime against number of cells (left) and GPU speed-up over one CPU thread (right).", "100%"))
    big = max(by); g = by[big]["gpu"]
    per = g["solver_wall_s"] / (big * 4.0) * 1e6
    t.append(f"""<p>On small domains (12,000 cells) the GPU is no faster than one CPU thread, since start-up dominates. Its advantage grows with size, from {by[47306]['cpu']['solver_wall_s'] / by[47306]['gpu']['solver_wall_s']:.1f}× at 47,000 cells to {by[690557]['cpu']['solver_wall_s'] / by[690557]['gpu']['solver_wall_s']:.1f}× at 0.7 million, and above about 0.2 million cells the GPU time grows roughly linearly: {big / 1e6:.1f} million cells took {g['solver_wall_s']:.0f} s, about {per:.1f} µs per cell per simulated hour. We did not time a multi-threaded CPU run, because in our test more threads were slower (Section [[s:solver]]), so the speed-up is against a single thread. On the original 104 km² benchmark the GPU was about 10 to 12 times faster (2 s against 24 s, Section [[s:solver]]).</p>""")
    rows = [(f"{k} ({n})", f"{v[2]:,}", f"{v[1]}", f"{v[0]}") for (k, n), v in zip([("A", "Manchar edge"), ("B", "north Dadu"), ("C", "Shikarpur")], [S.E2E["A"], S.E2E["B"], S.E2E["C"]])]
    t.append(D.table("e2e", "Complete runs for a 13 km area (48 h storm), from a drawn polygon to a finished result with buildings, roads, facility risk, loss and a 3D scene, on the live server, 31 October 2026.",
                     ["Area", "Buildings used", "Flood solver (s)", "Total (s)"], rows, numcols=(1, 2, 3)))
    t.append("""<p>A typical complete run therefore takes 1.4 to 3.1 minutes, of which the solver is 52 to 81 s; the rest is fetching and processing buildings, roads and land cover and building the 3D scene. Times are from single fresh runs without cache. Large domains take longer. A whole-city run (2.1 million cells, 447,000 buildings) once spent over ten minutes in post-processing because of a quadratic search for building heights; that was fixed, but we did not re-time the whole run.</p>""")
    return "\n".join(t)


def case(D, R):
    t = [D.h(2, "case", "Case study: protection testing")]
    c = R.get("case")
    if c:
        import content4 as c4
        t.append(c4.case_block(D, c))
    else:
        t.append(f"""<p>{todo('case study pending: it needs new baseline and scenario runs, and the shared drive holding the solver and terrain became unavailable. The study is designed as follows and implemented in paper/case_study.py.')}</p>
<p><b>Design.</b> One dense urban site (a 3.2 km box on Nullah Lai, Rawalpindi/Islamabad, centre 33.607° N, 73.062° E) with preloaded buildings, roads and facilities. Baseline runs at 10-, 50- and 100-year design storms (the 100-year 24-hour depth is 130.2 mm, ensemble median of five models, range 103.9 to 141.6 mm). Interventions: an embankment, a drainage line and a retention basin, each placed by the optimiser at the 100-year baseline, and a nature-based option (grassland or reforestation, active). Outputs per option: flooded buildings, event loss in rupees at each return period, annual average loss and break-even capital.</p>""")
    t.append(D.h(3, "access", "Exposure and access outputs"))
    t.append("""<p>The same run reports schools and hospitals flooded, kilometres of road in each hazard band, bridges and facilities by category, as listed in Section [[s:damage]].</p>""")
    t.append(D.h(3, "ui", "Platform interface"))
    if R.get("has_ui"):
        t.append("""<p>The interface is one page per flood type that shares the same map, area selection and run controls, with tabs for the scenario, runs, impact and mitigation. [[f:ui]] shows the area-selection and result views.</p>""")
        t.append(D.fig("ui", "fig_ui.png", "SAILAB interface: area selection and an animated flood view.", "100%"))
    else:
        t.append("""<p>The interface is one page per flood type that shares the same map, area selection and run controls, with tabs for the scenario, runs, impact and mitigation. """ + todo("interface screenshots pending") + "</p>")
    return "\n".join(t)


def discussion(D, R):
    t = [D.h(2, "disc", "Discussion")]
    t.append(D.h(3, "doesnt", "What the evaluation does and does not show"))
    t.append("""<p>It shows that SAILAB’s solver, fed the same terrain, rainfall and roughness, produces the same flooded cells as three independent codes, and that a complete run takes minutes. It does <em>not</em> show that rainfall-only SAILAB predicts where the 2022 Sindh flood stood: no model did, and depth added nothing to elevation. The test is one event, one product and one forcing dataset; satellite extents carry their own error (cloud, timing, 375 m pixels), and a single score hides spatial structure. We therefore treat the maps and the skill measures as part of the result.</p>""")
    t.append(D.h(3, "limits", "Limitations"))
    t.append("""<ul>
<li><b>Terrain.</b> SRTM is a surface model at 29 m with a specified absolute vertical error of 16 m (90 %) [[c:farr2007]]; in flat delta land, decimetre errors decide whether land floods. Buildings are added as obstacles on top of a surface that may already include them.</li>
<li><b>Forcing.</b> Reanalysis rainfall is coarse and can under-represent local convective peaks; a triangular storm applied uniformly is a simplification.</li>
<li><b>Inflow and structures.</b> Rainfall-only runs omit upstream river inflow, canals, breaches and barrages. River and outburst inflows are supported but there is no channel bathymetry, so a large discharge forced into a channel-width segment can give locally implausible depths.</li>
<li><b>Calibration.</b> No parameter was tuned to the observed extent and no observed discharge was used. (A separate calibration tool exists for users with an observed map; it was not used here.)</li>
<li><b>Solver.</b> The local-inertial solver degrades on extreme relief: on a Gilgit-Baltistan basin with a maximum slope of 852 % it produced a maximum depth of 255 m where the full-equation solver gave 0.83 m on a four-times coarser grid. The empirical depth correction for steep terrain fixes magnitude only. The full-equation solver is about 60 times slower. On one 2022 test area the CPU build reported a larger maximum depth than the GPU build at a few sink cells (16.3 m against 8.95 m at one cell) while the wet footprint matched; the cause is not explained.</li>
<li><b>Instant preview.</b> It over-predicts area and depth (Section [[s:preview]]).</li>
<li><b>Damage.</b> Depth–damage functions and values are a continental average at 2010 prices converted at a current exchange rate; heights default to 6 m; centroid sampling. Loss figures compare options and should not be read as forecasts of cost.</li>
<li><b>Dams and glacial lakes.</b> The outburst peak from a volume relation had a median ratio to observed of 1.05 over 16 moraine-dammed events but a 10–90 % range of 0.11 to 14.3, so it is an order of magnitude estimate; documented events are used where they exist. Dam-breach peaks come from a relation fitted to small dams, and for 64 of 101 dams the height is estimated. A spillway-release outlook from GloFAS was checked only for Tarbela (6,576 m³/s against documented 6,881 and 5,947) and is refused for Mangla (GloFAS 2 m³/s against a documented 8,495).</li>
<li><b>Snowmelt.</b> A distributed melt model agrees well with GloFAS for one Hunza basin after calibration (KGE 0.95; 0.87 on a held-out year) but cannot be reconciled for a neighbouring basin; GloFAS is itself a model, not gauges, and parameters do not transfer between basins.</li>
<li><b>Coastal.</b> One tide gauge (Karachi) serves the whole coast. An earlier version applied return levels measured on the gauge scale to the terrain as if they were above mean sea level, overstating flooding; the datum was corrected on 14 October 2026 and results produced before then with that source overstate flooding.</li>
<li><b>Runtime.</b> Times are from a shared server and single runs.</li>
</ul>""")
    t.append(D.h(3, "ethics", "Ethical and operational considerations"))
    t.append(f"""<p>Automated alerts can cause harm if wrong or ignored. SAILAB is a decision-support tool; its alerts do not replace official warnings from the national authorities. A run uses public or supplied data only. {todo('state the intended users, data-sharing arrangements and any agreement with the host organisation')}</p>""")
    t.append(D.h(3, "global", "Towards global coverage"))
    t.append("""<p>The architecture is not specific to Pakistan; the data layer, forcing sources and damage inputs are. The platform is deliberately limited to Pakistan: since 20 November 2026 areas outside the country are refused and no terrain is fetched from the internet. An earlier prototype that fetched Copernicus GLO-30 terrain worked on a Nairobi area, because buildings (Google Open Buildings), roads (OpenStreetMap), land cover (WorldCover), soils (SoilGrids) and the reanalysis forcing are already global; that option was removed. Extending beyond Pakistan again would need, in order: (1) a terrain choice per region with a stated vertical error; (2) regional damage functions and maximum-damage values, which the JRC database provides by continent; (3) region-specific hazard inputs (outburst inventories, dam registers, tide and surge records); (4) a validation event for each region, since the evidence in this paper is for one Pakistani event.</p>""")
    return "\n".join(t)


def conclusion(D, R):
    t = [D.h(2, "concl", "Conclusion")]
    t.append("""<p>SAILAB puts flood simulation, building-level loss, mitigation testing and forecast-triggered monitoring on one engine and one data record, and a complete run for a 13 km area takes minutes. Its solver agrees closely with three independent flood models on the same inputs, which is good evidence that the numerics are right. On the 2022 Sindh flood, however, rainfall-only simulation, by SAILAB or by any of the other models, did not reproduce the observed extent better than flooding everything, and its depth pattern carried no information beyond elevation. We attribute this, without having tested it, to missing river inflow and to terrain and observation limits. The next steps are therefore clear: impose upstream inflow, test a second event, calibrate against observed extents with a held-out event, and complete the sensitivity and case-study runs reported as pending here. The platform’s value today is in making those experiments, and the scenario testing that decisions need, quick to run.</p>""")
    t.append("<h2>Data and code availability</h2>")
    t.append(f"""<p>Evaluation scripts, figure scripts and result tables (<code>paper/data/*.json</code>) are in the project repository, commit <code>083ed51</code>. Third-party data keep their own licences and are not redistributed. The flood solver is GPL-3.0 and our patches to it inherit that licence. {todo('confirm what is released: repository visibility, platform code, sample inputs')}</p>""")
    t.append("<h2>Acknowledgements</h2>")
    t.append(f"""<p>{todo('funding, host institution, Bahria University launch if appropriate')} We thank the data providers: ECMWF and the Copernicus programme (ERA5), UNOSAT and the Humanitarian Data Exchange, OpenStreetMap contributors, the Global Dam Watch consortium, and the LISFLOOD-FP, ANUGA, SFINCS and TELEMAC developers.</p>""")
    return "\n".join(t)


def appendices(D, R):
    t = []
    t.append(D.appendix("repro", "A", "Reproducibility checklist"))
    t.append("""<ul>
<li>Repository commit: <code>083ed51</code>. Platform version 6.1.</li>
<li>Solver: LISFLOOD-FP 8.2 built from source with the patches of Section [[s:solver]]; CUDA 13.0, driver 580.178.04, NVIDIA GB10 (compute 12.1), ARM64, Python 3.12.</li>
<li>Container image: none; the service runs from a Python virtual environment.</li>
<li>Random seeds: the placement optimiser is seeded from a hash of the request, so identical requests repeat; we did not measure run-to-run variation of the GPU solver.</li>
<li>Regenerating the figures and this document: <code>python paper/make_all.py</code>. Benchmarks: <code>paper/bench_scaling.py</code>; sensitivity: <code>paper/run_sensitivity.py</code>; skill measures: <code>paper/skill_metrics.py</code>; case study: <code>paper/case_study.py</code>; monitoring: <code>paper/monitoring_latency.py</code>.</li>
<li>Observed data: UNOSAT product FL20221121PAK, from the Humanitarian Data Exchange.</li>
</ul>""")
    t.append(D.appendix("metrics", "B", "Metric identities"))
    t.append("""<p>From the definitions in Equation [[e:csi]], CSI<sup>−1</sup> = POD<sup>−1</sup> + (1 − FAR)<sup>−1</sup> − 1. For a forecast that floods every cell, POD = 1 and FAR = 1 − <i>p</i>, where <i>p</i> is the observed flooded fraction, so CSI = <i>p</i>. In the withdrawn 11 km box <i>p</i> = 0.981, so the trivial forecast scores 0.981 and the reported 0.659 is below it. In the three test areas <i>p</i> is 0.49 to 0.51, so the trivial score is about 0.5 and a score near 0.5 shows no skill. The ROC AUC is the probability that a randomly chosen observed-wet cell has greater simulated depth than a randomly chosen observed-dry cell, with ties counted half; the Peirce score is POD − POFD and the Heidke score is the accuracy gain over chance.</p>""")
    t.append(D.appendix("schema", "C", "Results files"))
    t.append("""<p>Every number in the text is computed from a file in <code>paper/data/</code> when the document is built, except the multi-model study scores (<code>study.py</code>, transcribed from the study report of 31 October 2026 while its source JSON was unreachable). Files: <code>scaling.json</code> (runtime benchmark), <code>skill_metrics.json</code> (AUC, Peirce and Heidke scores), <code>preview_vs_full_windows.json</code> (315 windows), and, when present, <code>sensitivity.json</code>, <code>case_study.json</code> and <code>monitoring_latency.json</code>. Unfilled results appear as red TODO markers so that no number is typed by hand.</p>""")
    return "\n".join(t)
