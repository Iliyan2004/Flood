import re, sys
P = "/home/aliyan/flood/SAILAB_Technical_Reference.html"
s = open("/home/aliyan/flood/SAILAB_Technical_Reference.html.bak_2026-10-12", encoding="utf-8").read()

def rep(old, new, count=1):
    global s
    assert old in s, "anchor missing: " + old[:70]
    s = s.replace(old, new, count)

def before(anchor, block):
    rep(anchor, block + anchor)

# ---- cover and edition notes
rep("Second edition, October 2026 (supersedes the September 2026 edition).",
    "Third edition, 31 October 2026 (supersedes the second edition of 12 October 2026).")

CHANGES3 = """<h1 class="chapter" id="c0">What changed in this edition</h1>
<h2>Changes in the third edition (31 October 2026)</h2>
<table class="changes">
<tr><th>Area</th><th>Change</th></tr>
<tr><td>Coastal datum</td><td>The Khan et al. (2020) Karachi return levels are heights above the tide gauge's own zero, not above mean sea level; they had been applied to the DEM as if above MSL. The gauge puts MSL 1.849 m above its zero, so the 100-year level is 1.91 m above MSL, matching the independent GTSM value (1.79 m). The "2&times; disagreement" reported earlier was this error. Coastal results made before 14 October 2026 with the Khan source overstate flooding (&sect;19.10).</td></tr>
<tr><td>Astronomical tide</td><td>A 23-constituent harmonic fit to the supplied Karachi hourly record, validated on two unseen years (RMSE 0.118 m), adds real tide to the dynamic coastal run and the animated preview, with the surge placed at spring high water, an ordinary high water or a low water (&sect;19.10).</td></tr>
<tr><td>Vector data</td><td>A local PostGIS database is now the first source for roads, hospitals, schools, power and railway lines, airports and stations, with the shapefiles as fallback; inside Islamabad, drivable roads and six extra facility categories are added, and road results gain major/local, bridge and length figures (&sect;22.6&ndash;22.8).</td></tr>
<tr><td>Entry points</td><td><code>/</code> is now a landing page and the application is at <code>/app</code>; an optional access token protects non-local requests; a separate client plugin lets other programs call a running SAILAB (&sect;2.4, &sect;40.2).</td></tr>
<tr><td>Operations agents</td><td>A data-health watchdog, a forecast-validation ledger and automatic run QA run in the background and alert only on change; they detect and suggest and never alter a result (&sect;40.1).</td></tr>
<tr><td>Observed-flood scoring</td><td>Upload a mapped flood extent and score any finished run (CSI, POD, FAR, F1, area ratio); scenes can be rebuilt without re-simulating; a cache-cleanup tool (dry run by default) was added (&sect;35.4&ndash;35.6).</td></tr>
<tr><td>Validation, corrected</td><td>The 2022 Sindh test box (CSI 0.66) was 98% flooded in the observed map, so it showed no skill. A controlled study on three half-flooded areas against ANUGA, SFINCS and TELEMAC-2D found accuracy on par with them, and no model clearly better than a trivial "flood everything" guess under rain-only forcing (&sect;33.1).</td></tr>
<tr><td>LISFLOOD-FP 8.3</td><td>Built and benchmarked against 8.2; identical results, no speed gain on our cases; not adopted (&sect;4.3).</td></tr>
<tr><td>Earth Engine</td><td>Re-authenticated on 13 October 2026 and verified live (glacier baseline, SAR displacement, storm-motion nowcast); the project reports a restricted compute quota. The previous "credential revoked" statements are superseded (Ch. 3, 30, 39).</td></tr>
<tr><td>Tests</td><td>420 fast tests passing at the last full run.</td></tr>
</table>
<h2>Changes in the second edition (12 October 2026)</h2>
"""
rep('<h1 class="chapter" id="c0">What changed in this edition</h1>\n', CHANGES3)
rep("(selectable, disagrees ~2&times; with the gauge anchors at Karachi)",
    "(selectable; the apparent ~2&times; disagreement with the gauge anchors was a datum error, corrected in the third edition, &sect;19.10)")

# ---- Earth Engine statements
rep("Earth Engine (credential currently revoked)", "Earth Engine (re-authenticated 13 Oct 2026; restricted compute quota)")
rep('<div class="note"><b>Earth Engine.</b> Glacier SAR monitoring (Ch. 30) and storm-motion nowcasting (Ch. 31) need a live Earth Engine OAuth credential, which Google has revoked (<code>invalid_grant</code>, reproduced directly). Both features return a plain "not available" message rather than a fabricated result until the account owner re-runs <code>earthengine authenticate</code>.</div>',
    '<div class="note"><b>Earth Engine.</b> Glacier SAR monitoring (Ch. 30) and storm-motion nowcasting (Ch. 31) need a live Earth Engine OAuth credential. The earlier token was revoked (<code>invalid_grant</code>); it was renewed on 13 October 2026 with the Drive and Storage scopes dropped (the default client blocks them) and verified live: a GLIMS baseline (52 glaciers, 217 km&sup2;), a two-month Shishper SAR job (mean displacement 1.24 m, maximum 11.46 m) and a storm-motion nowcast. The project reports "restricted mode" (non-commercial compute quota exceeded). If the token lapses again, both features return a plain "not available" message instead of a fabricated result, and the health watchdog (&sect;40.1) refreshes the token every 15 minutes so a lapse is reported.</div>')
rep('<div class="note">Currently non-functional: the shared Earth Engine credential has been revoked by Google. The feature returns "not available"; only the account owner can re-authenticate.</div>',
    '<div class="note">Working since the credential was renewed on 13 October 2026 (Ch. 3). If the token lapses the feature returns "not available"; only the account owner can re-authenticate, and the health watchdog (&sect;40.1) reports the lapse.</div>')
rep("<li><b>Earth Engine features are down</b> (glacier SAR, storm-motion nowcasting) until the credential is renewed.</li>",
    "<li><b>Earth Engine features depend on one personal OAuth credential</b> (glacier SAR, storm-motion nowcasting) that has been revoked once; the project is also in restricted compute mode.</li>")

# ---- chapter 1.2
before('<h1 class="chapter" id="c2">', """<h2>1.2 Status of the evidence</h2>
<p>Numerical correctness has been checked more than real-world accuracy. SAILAB's solver gives the same flood as three independent codes (ANUGA, TELEMAC-2D, SFINCS) on identical inputs, with 90&ndash;94% of flooded cells in common (Ch. 33.1), and matches its own CPU build to the displayed digit where tested. Accuracy against an observed flood is much weaker: the only observed-flood test used a rain-only storm on a 2022 flood that came mostly from river overflow, and no model, SAILAB included, scored clearly better than the trivial guess of flooding everything. SAILAB is therefore documented as comparable in accuracy to other open 2D models, faster on its GPU, and far more integrated, not as demonstrably more accurate.</p>
""")

# ---- 2.4
before('<h1 class="chapter" id="c3">', """<h2>2.4 Entry points, vector data, access and the client plugin <span class="new">NEW</span></h2>
<ul>
<li><b>Landing page.</b> <code>/</code> serves <code>landing.html</code> (read from disk on each request): a full-screen WebGL fragment-shader scene of a tsunami wave with the product name and an "Open SAILAB" button. The button plays a 1.5 s surge, then opens <code>/app</code> (the application). Links with <code>?import=&lt;id&gt;</code> skip the landing page so the SimCity hand-over still works. Ctrl-click, middle-click and reduced-motion users navigate directly; without WebGL a CSS gradient is shown.</li>
<li><b>Access token.</b> If <code>SAILAB_ACCESS_TOKEN</code> is set in <code>.env</code>, every request not from <code>127.0.0.1</code> needs <code>?token=...</code> once (it sets a cookie) or a Bearer header. An SSH tunnel to port 8000 arrives from loopback and needs no token.</li>
<li><b>Vector data.</b> A local PostGIS database (the SimCity database; read-only connection from <code>SAILAB_PG_URL</code>, never committed) is tried first for roads, hospitals, schools, power and railway lines, airports and stations. On any failure it backs off for 60 s and the shapefiles take over (&sect;22.6).</li>
<li><b>Solver slot.</b> A tracked slot serialises solver use; <code>/solver-status</code> shows the holder and waiters, queued runs report their position, and a run can be cancelled while queued or while the solver is running (not during post-processing).</li>
<li><b>Client plugin.</b> <code>sailab-client</code> (in <code>sailab-plugin/</code> of the GitHub repository) is a thin HTTP client for a running SAILAB, used by the Islamabad SimCity project. SAILAB must stay reachable because the data and the GPU solver remain on the server.</li>
<li><b>Source control.</b> The whole application, tests, documentation and plugin are published to <code>github.com/Iliyan2004/Flood</code> (branch master). Secrets, data files, caches, the virtual environment and the PostGIS password are never committed.</li>
</ul>
""")

# ---- 4.3 LISFLOOD-FP 8.3
before('<h1 class="chapter" id="c5">', """<h2>4.3 LISFLOOD-FP 8.3 evaluated, not adopted <span class="new">NEW</span></h2>
<p>Version 8.3 (Zenodo 10.5281/zenodo.13121119, 13 October 2026, Rehman and Teferle, University of Luxembourg) is 8.2 with the maximum-depth grid of the FV1/FV2/DG2 solvers kept on the GPU at full size (the same bug fixed by hand in &sect;4.2), no per-step shared mass counter for dry NoData cells, thread-synchronised flux kernels, and a Metal back end for Apple Silicon; the numerical method is unchanged. The archive's MD5 matched. It was built with the local fixes re-applied (its sources use CRLF line endings, so patches need <code>--binary</code>) and run against 8.2 on the same cases:</p>
<table>
<tr><th>Case</th><th>8.2</th><th>8.3</th></tr>
<tr><td>Bahawalpur, ACC, 104 km&sup2;</td><td>5 s</td><td>4 s, byte-identical <code>res.max</code></td></tr>
<tr><td>400&times;400 DG2, steep synthetic</td><td>344 s</td><td>345 s</td></tr>
<tr><td>700&times;700 DG2 (490,000 cells)</td><td>913 s</td><td>1,137 s (confounded by machine load)</td></tr>
</table>
<p>8.3 writes depths to six decimals against three in 8.2; rounded to three decimals the flooded cell count matches exactly (621), while the unrounded files count 666 cells between 0.0495 and 0.05 m as wet. No speed gain appeared at our sizes (the published gain is for grids of about 10 million cells) and its main fix is one already patched locally, so <code>flood_model.LISFLOOD_BIN</code> still points at 8.2. The 8.3 build is kept in <code>~/flood/lisflood-fp-8.3</code> for a future multi-million-cell test.</p>
""")

# ---- 19.1 note, 19.6 rewrite, 19.10
rep("The reference is regional (Karachi), labelled as such.</p>",
    'The reference is regional (Karachi), labelled as such.</p>\n<div class="note"><b>Datum.</b> These levels are heights above the tide gauge\'s own zero (chart datum), the record Khan et al. analysed. They are converted to heights above mean sea level by subtracting the gauge\'s measured MSL offset (1.849 m): the 100-year level is 1.91 m above MSL, not 3.76 m (&sect;19.10).</div>')
old196 = re.search(r"<p>A second water-level source is selectable beside the Karachi tide-gauge anchors:.*?Neither is validated against an observed coastal flood\.</p>", s, re.S)
assert old196
s = s.replace(old196.group(0), "<p>A second water-level source is selectable beside the Karachi tide-gauge anchors: GTSM-ERA5-E v2 return levels (2, 5, 10, 25, 50, 100 years) at 42 stations along the Pakistan coast. The nearest station to the AOI centroid is used, log-interpolated in return period and extended log-linearly beyond 100 years (flagged as extrapolated). The 100-year level varies from about 1.44 m in the west to 1.90 m near Karachi. At Karachi the corrected gauge anchor (1.91 m above MSL) and GTSM (1.79 m) agree closely; the factor-of-two gap reported earlier was the datum error described in &sect;19.10. On a Karachi test area the corrected gauge level floods 31.1 km&sup2; (peak depth 2.85 m) and GTSM 28.1 km&sup2;. GTSM is modelled and coarse near the coast, and the SRTM DEM is referenced to a geoid that only approximates local MSL. Neither is validated against an observed coastal flood.</p>")
before('<h1 class="chapter" id="c20">', """<h2>19.10 Tide gauge, datum correction and astronomical tide <span class="new">NEW</span></h2>
<p>An hourly sea-level record for Karachi (file <code>h147b.csv</code>, 31 January 2007 to 15 December 2016, 86,547 hours of which 541 are flagged missing, millimetres above the gauge zero) was added as <code>global_data/tide/karachi_hourly_h147b.csv</code>.</p>
<ul>
<li><b>Datum correction.</b> Khan et al. analysed this same record, so their return levels are above the gauge zero. The record's mean sea level is 1.849 m above that zero. <code>karachi_anchors_above_msl()</code> subtracts it. The 50/100/1,000-year levels become 1.88 / 1.91 / 2.07 m above MSL (from 3.73 / 3.76 / 3.92). On the Karachi test box the static 100-year flood fell from about 80 km&sup2; (4.7 m) to 31.1 km&sup2; (2.85 m). Custom surge heights and the GTSM source were never affected.</li>
<li><b>Gauge facts.</b> Mean daily range 2.29 m (Khan: 2.3 m); largest constituents M2 0.80 m, K1 0.40, S2 0.31, O1 0.21, N2 0.19; highest observed level +1.79 m above MSL; annual maxima 3.42&ndash;3.64 m on the gauge scale.</li>
<li><b>Harmonic fit (<code>tide_utils.py</code>).</b> Least-squares fit of 23 constituents without nodal correction, stored in <code>karachi_tide_constants.json</code>. Fitted on 2007&ndash;2014 and used to predict the final two unseen years: RMSE 0.118 m, correlation 0.987 (fit RMSE 0.096 m).</li>
<li><b>Tide plus surge.</b> <code>build_tide_surge_series</code> builds an hourly series: the real GTSR surge episode shape plus the predicted tide, with the surge peak placed at spring high water (worst case), an ordinary high water or a low water. With the GTSR anchor the total is surge plus tide; with the static anchor the surge scale is solved so the highest total level equals the return-period level, so tide changes only when it happens. It replaces the older flat "add mean tidal range" box when chosen, in both the dynamic run and the animated preview.</li>
</ul>
<div class="note"><b>Limits.</b> One gauge for the whole coast (the tidal range is smaller toward Gwadar); the gauge file's own clock (assumed UTC) means only tidal phase at the surge peak is offered, not a calendar date; tide&ndash;surge interaction, wave set-up and Coriolis are not modelled (the last is negligible on 30 m grids and already inside the tide and surge data). EOT20, an open global tide model, was considered for spatial variation and not downloaded. A zip of 27 GRDC daily discharge gauges (1978&ndash;1982) was also supplied; only 1980&ndash;82 overlaps the ERA5 forcing, and a Hunza test would need the union of upstream sub-basins, so it has not been used.</div>
""")

# ---- 22.6-22.8
before('<h1 class="chapter" id="c23">', """<h2>22.6 PostGIS as the primary vector source <span class="new">NEW</span></h2>
<p>The SimCity PostGIS database (PostGIS 3.6, about 926 MB, GiST indexes on every table) holds national tables (<code>pk</code>: roads 1.1 million, hospitals 35,561, schools 65,909, power and railway lines, railway stations, airports, tehsils, HydroLAKES) and an Islamabad subset (<code>isb</code>). <code>pg_vector_utils.py</code> returns the same record shapes as the shapefile readers and is tried first; any failure returns nothing and backs off for 60 s so the files take over. Compared on two boxes it was a strict superset of the files (every file feature at the same place plus one or two extras from multi-part lines) and faster: roads 0.2 s against 3.1 s on a small box, 2.4 s against 41 s on all of Islamabad and Rawalpindi. Sea ports are not in the database and still come from their shapefile. The connection is read-only and the password lives only in <code>.env</code>.</p>
<p>The first live run exposed a silent failure: PostGIS returns numeric columns as <code>Decimal</code>, which <code>json.dump</code> rejects, so infrastructure risk finished empty with zero roads. Values are now coerced at the source and a regression test covers it.</p>
<h2>22.7 Six Islamabad-only facility categories <span class="new">NEW</span></h2>
<p>For areas that intersect the Islamabad extent, <code>islamabad_layers_utils.py</code> reads the Islamabad SmartCity GeoPackage (104 layers, spatially indexed) and adds <b>shelters, police, water facilities</b> (supply, storage, wastewater), <b>fuel and chemical, governance</b> and <b>industry</b> (plants, waste processing, quarries) to the infrastructure risk, sampled at each feature's centroid against the run's depth and hazard. Waterways and canals are deliberately excluded, since they are where the water is meant to be. On the saved Islamabad run: shelters 3, police 4, water 17, fuel/chemical 5, governance 8, industry 6; one of two in-domain shelters and five of 15 water facilities flooded. Outside Islamabad nothing is added. Facility status (open or damaged) is not available.</p>
<h2>22.8 Drivable roads and road detail <span class="new">NEW</span></h2>
<p>For a box completely inside the Islamabad study area, roads come from <code>isb.roads_drivable</code>, a materialised view without footways, paths, steps, pedestrian ways, cycleways, tracks, bridleways, corridors and construction or proposed roads (76,867 of 82,451 lines). Anywhere else, or when the box extends past the study area, the national table is used unfiltered, so national numbers are unchanged. Records carry <code>lanes</code>, <code>surface</code>, <code>oneway</code>, <code>bridge</code> and <code>width</code>. New <code>road_detail</code> results report major roads (motorway to tertiary and links) against local roads, bridges, and total against flooded length. On a central-Islamabad run: 11 of 169 major roads, 36 of 767 local roads, 5 of 18 bridges and 4.13 of 168.3 km flooded. A flooded bridge means the depth at its centroid, because the DEM has no deck, so bridge figures are an upper bound. Lanes, width and surface are carried but not yet used in any calculation.</p>
""")

# ---- 33: caveat rows + 33.1
rep("<td>CSI 0.659, POD 0.669, FAR 0.021 (73.5 vs 107.7 km²)</td>", "<td>CSI 0.659, POD 0.669, FAR 0.021 (73.5 vs 107.7 km²). <b>The test box was 98% flooded in the observed map, so this does not demonstrate skill</b> (&sect;33.1).</td>")
rep("<td>CSI 0.694, POD 0.705, FAR 0.022 (77.6 vs 107.7 km²)</td>", "<td>CSI 0.694, POD 0.705, FAR 0.022 (77.6 vs 107.7 km²). Same caveat.</td>")
rep("</table>\n<p>The 2022 validation was re-run after a bug", """<tr><td>Multi-model study, three half-flooded 2022 Sindh areas</td><td>Mean CSI: SAILAB 0.504, ANUGA 0.503, SFINCS 0.500, TELEMAC-2D 0.497, "flood everything" 0.500 (&sect;33.1)</td></tr>
<tr><td>Wet-cell overlap between codes (mean of three areas)</td><td>SAILAB&ndash;ANUGA 0.94, SAILAB&ndash;TELEMAC-2D 0.90, SAILAB&ndash;SFINCS 0.82</td></tr>
<tr><td>LISFLOOD-FP 8.3 against 8.2</td><td>Identical results; no speed gain on our cases (&sect;4.3)</td></tr>
<tr><td>Tide harmonic fit, two unseen years</td><td>RMSE 0.118 m, correlation 0.987 (&sect;19.10)</td></tr>
</table>
<p>The 2022 validation was re-run after a bug""")
before("<!-- ===================================================== PART X -->", """<h2>33.1 Multi-model case study <span class="new">NEW</span></h2>
<p>The Sindh test above used a box inside the largest flooded patch, 98% flooded in the observed map, where a model that floods everything would score a CSI near 0.98. A controlled study replaced it. Full report: <code>model_comparison/SAILAB_Model_Comparison_Report.pdf</code> (code and raw results alongside; published in the repository).</p>
<ul>
<li><b>Design.</b> Three 13 km boxes on the edge of the observed flood (49%, 50% and 51% flooded, UNOSAT VIIRS map of 29 November 2022). Every model got the same SRTM terrain, 253.5 mm of rain over 48 h (the wettest ERA5 window at the site), Manning n = 0.05, no infiltration, closed boundaries and no calibration. Scoring used a 5 cm cut-off, plus two baselines: flood everything, and flood the lowest terrain up to the true flooded fraction.</li>
<li><b>Models run.</b> SAILAB (LISFLOOD-FP 8.2 ACC, GPU); ANUGA 4.1 (finite volume, 90 m mesh); SFINCS v2.5.0-beta (Deltares, built from source); TELEMAC-2D v9.2-dev (built from source, serial, 90 m mesh).</li>
</ul>
<table>
<tr><th>Model</th><th>Mean CSI</th><th>Mean POD</th><th>Mean FAR</th><th>Mean run time</th></tr>
<tr><td>Flood everything</td><td>0.500</td><td>1.00</td><td>0.50</td><td>&ndash;</td></tr>
<tr><td>Lowest terrain (told the true fraction)</td><td>0.415</td><td>0.58</td><td>0.42</td><td>&ndash;</td></tr>
<tr><td>SAILAB (ACC, GPU)</td><td>0.504</td><td>0.89</td><td>0.46</td><td>58 s</td></tr>
<tr><td>ANUGA (CPU)</td><td>0.503</td><td>0.86</td><td>0.45</td><td>477 s</td></tr>
<tr><td>SFINCS (CPU)</td><td>0.500</td><td>1.00</td><td>0.50</td><td>528 s</td></tr>
<tr><td>TELEMAC-2D (1 core)</td><td>0.497</td><td>0.84</td><td>0.45</td><td>141 s</td></tr>
</table>
<ul>
<li><b>Findings.</b> The three physics codes agree closely with each other (90&ndash;94% of wet cells in common) and over-predict wet area by 1.5&ndash;1.7&times;. SFINCS scores like "flood everything" because it keeps more than 5 cm of water in every cell; its maximum depths match the others. As the wet-depth cut-off rises every model falls below both baselines from 20 cm. The likely reason, untested, is that rain alone cannot reproduce a flood that came mainly from river overflow.</li>
<li><b>Meaning.</b> SAILAB's solver is numerically consistent with independent codes; no model, SAILAB included, shows skill above the trivial guess on this test. Speed comparisons mix a GPU with CPU codes and different meshes.</li>
<li><b>Time to result.</b> Through the live app, a drawn 13 km polygon to a finished result with 6,000&ndash;10,400 buildings, roads, risk, loss and a 3D scene took 81&ndash;187 s. The three open models each needed a hand-written converter (59, 69 and 114 lines).</li>
<li><b>Not run.</b> HEC-RAS, TUFLOW, MIKE, InfoWorks, Flood Modeller and FLO-2D need a licence or Windows/x86, and this server is ARM with no emulator; Delft3D-FM ships x86 binaries; TRITON, SERGHEI, RIM2D and HiPIMS were not attempted. DG2 on these flat rainy domains stalled with its timestep collapsing to about 0.01 s and was dropped. No GPU support for the HEC-RAS 2D solver was found in the public release notes read (unconfirmed against the official notes).</li>
<li><b>Published comparisons.</b> Checked against the papers' own text: on the River Lis (Portugal) HEC-RAS and LISFLOOD-FP inundated areas overlapped by 65% and HEC-RAS ran much faster; on the Thessaly plain (Greece) run times were about 3 h (LISFLOOD-FP), 2 h (FLO-2D) and 10 min (HEC-RAS). Others were read from abstracts only (see the report). No study comparing SWAT with LISFLOOD-FP was found; SWAT is a catchment water-balance model, not a flood-extent model.</li>
</ul>
""")

# ---- 35.4-35.6
before('<h1 class="chapter" id="c36">', """<h2>35.4 Comparing a run with an observed flood <span class="new">NEW</span></h2>
<p><code>POST /runs/&lt;id&gt;/validate-observed</code> (button "Compare with observed flood" on a finished run) accepts a mapped extent as polygons (GeoJSON, GeoPackage, shapefile or zipped shapefile) or a GeoTIFF where values above zero mean flooded, and returns CSI, POD, FAR, F1 and the modelled and observed wet areas. Only valid cells inside the AOI are scored, the ACC depth correction is applied, and the wet threshold is 0.05 m. On the corrected 48 h Sindh run against the UNOSAT extent it reproduced <code>validate_2022_flood.py</code> exactly (CSI 0.6592, POD 0.6686, FAR 0.0209). Interpret with the observed fraction in mind (&sect;33.1).</p>
<h2>35.5 Rebuilding a scene without re-simulating <span class="new">NEW</span></h2>
<p><code>POST /runs/&lt;id&gt;/rebuild-scene</code> redraws a run's 3D page from its saved results. A rebuild of the Islamabad run (6,000 buildings, 30,931 road segments) took about 3.5 minutes on a healthy machine; one took 7 hours only because the machine was swapping.</p>
<h2>35.6 Coastal and GLOF PDFs; cache cleanup <span class="new">NEW</span></h2>
<p><code>POST /report/coastal.pdf</code> and <code>/report/glof.pdf</code> render the result the browser already holds (HTML-escaped) as a one-page PDF. <code>cache_cleanup.py</code> is a dry run by default; <code>--apply</code> deletes runs older than <code>--days</code> (30) outside the newest <code>--keep-newest</code> (20) and not listed in <code>keep_runs.txt</code>. The first dry run found 125 runs and 4.5 GB reclaimable; nothing has been deleted.</p>
""")

# ---- chronicle entries
before("</div>\n\n<h1 class=\"chapter\" id=\"c38\">", """<h3>37.33 Tide-gauge datum error in every Khan-based coastal result</h3><p>Return levels above the gauge zero were applied as above mean sea level; the true 100-year level is 1.91 m, not 3.76 m. Found when the hourly gauge record showed MSL 1.849 m above its zero (&sect;19.10).</p>
<h3>37.34 PostGIS Decimal values silently emptied infrastructure risk</h3><p><code>json.dump</code> rejects <code>Decimal</code>, so a run finished with no roads and no error beyond one log line. Coerced at the source with a regression test (&sect;22.6).</p>
<h3>37.35 A validation box that could not show skill</h3><p>The first Sindh test box was 98% flooded in the observed map, so a CSI of 0.66 and a false-alarm ratio of 0.02 were uninformative. Replaced by half-flooded areas and trivial baselines (&sect;33.1).</p>
<h3>37.36 Wrong package, same name</h3><p>The conda-forge package "sfincs" is a stellarator plasma code, not the Deltares flood model; it failed at once on a missing input file. Removed; the real model was built from source.</p>
<h3>37.37 DG2 timestep collapse on flat rainy terrain</h3><p>On the study areas DG2 stalled for 42 minutes with its timestep near 0.01 s; the existing slope-based choice already selects ACC there (&sect;33.1).</p>
<h3>37.38 Token validity cached for the life of the process</h3><p><code>_ee_init</code> keeps its first Earth Engine result forever, so a token revoked later was invisible. The watchdog now performs a real refresh (&sect;40.1). The same check found that Overpass's <code>/api/status</code> page answers 406 to every client; the probe is now a tiny real query.</p>
<h3>37.39 Test runner stalled by plugin auto-loading</h3><p>pytest hung for tens of minutes loading an installed plugin's heavy imports on the loaded machine; <code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1</code> avoids it.</p>
""")

# ---- declined
rep("<tr><td>Raising depththresh for stability</td><td>Made steep runs worse (§7.3).</td></tr>\n</table>",
    """<tr><td>Raising depththresh for stability</td><td>Made steep runs worse (§7.3).</td></tr>
<tr><td>LISFLOOD-FP 8.3</td><td>Identical results and no speed gain on our cases; its main fix is already patched locally (&sect;4.3).</td></tr>
<tr><td>HEC-RAS, TUFLOW, MIKE, InfoWorks, Flood Modeller, FLO-2D, Delft3D-FM</td><td>Licence, Windows or x86 only; this server is ARM with no emulator. Compared through published results instead (&sect;33.1).</td></tr>
<tr><td>TRITON, SERGHEI, RIM2D, HiPIMS</td><td>Source builds with untested GPU support on this machine; not attempted.</td></tr>
<tr><td>Single-model "better than all" claims</td><td>Not supported: accuracy is on par with other open 2D models (&sect;33.1).</td></tr>
</table>""")

# ---- limitations
rep("<li><b>Validation</b> covers one major event (2022 Sindh); the 30-day window run did not complete.</li>",
    "<li><b>Validation</b> covers one major event (2022 Sindh). No model, SAILAB included, scored clearly above a trivial \"flood everything\" guess on rain-only forcing there; the 30-day window run did not complete; no depths have been validated (&sect;33.1).</li>\n<li><b>Coastal</b> results use one tide gauge (Karachi) for the whole coast; results made with the Khan source before 14 October 2026 overstate flooding (&sect;19.10).</li>\n<li><b>Vector data</b> depend on a local PostGIS database that can be down; the shapefile fallback is then used, which lacks the Islamabad-only categories (&sect;22.6).</li>")

# ---- operations
rep("(348 tests passing at the last run)", "(420 tests passing at the last full run)")
rep("<code>MAPBOX_TOKEN</code> in <code>.env</code>.</li>",
    "<code>MAPBOX_TOKEN</code> in <code>.env</code>, <code>SAILAB_ACCESS_TOKEN</code>, <code>SAILAB_PG_URL</code>, <code>SAILAB_ALERT_WEBHOOK</code>, <code>SAILAB_DISABLE_OPS_LOOP=1</code> (used by the test server), and <code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1</code> when running tests on a loaded machine.</li>")
before('<h1 class="chapter" id="c41">', """<h2>40.1 Operations agents <span class="new">NEW</span></h2>
<p><code>ops_agents.py</code> runs three rule-based background checks every 15 minutes. They detect, record, alert and suggest; none alters a model, setting or result. Alerts go to <code>SAILAB_ALERT_WEBHOOK</code> (Slack, Discord, ntfy or a custom receiver) and the log.</p>
<ul>
<li><b>Data-health watchdog</b> (<code>GET /ops/health</code>, <code>POST /ops/health/run</code>; state in <code>ops_state.json</code>). Checks the Earth Engine token (a real refresh), PostGIS, Open-Meteo, GloFAS, Overpass (a tiny real query), disk space, GPU temperature, required datasets and the solver binary. A check must fail a set number of times in a row (1 for local resources, 2&ndash;3 for public services) before one alert, and one "recovered" message follows. Its first run found PostGIS genuinely down (nothing listening on its port; the files were being used) and a transient Overpass 502.</li>
<li><b>Forecast validation</b> (<code>GET /ops/forecast-skill</code>; ledger <code>forecast_ledger.jsonl</code>). Every live-monitoring forecast is logged; once 54 h old, its first 48 h are scored against Open-Meteo's later past-day analysis (a model-blended product, not rain gauges): peak error, total error, peak-timing error and threshold hit, false alarm or miss. After ten forecasts it reports an advisory observed/forecast peak ratio, which is never applied automatically. No scored data existed at the time of writing.</li>
<li><b>Run QA</b> (<code>GET /runs/&lt;id&gt;/qa</code>, <code>POST /ops/run-qa-scan</code>; <code>run_qa.json</code> per run and <code>summary["qa"]</code>). After each run it checks for non-finite results, depth above 100 m (above 40 m from rain alone), mass-balance error above 5% or 20% of the water input, more than 95% of the domain flooded, buildings used but not assessed, and CUDA, FAILED or nan lines in the log; it records which automatic fallbacks were in play (ACC forced by size, CPU forced by a NODATA inlet, CUDA restart) with a suggested action per issue. A daily scan covers earlier runs; 25 existing runs all passed.</li>
</ul>
<h2>40.2 Access, queue and publishing <span class="new">NEW</span></h2>
<ul>
<li><b>Reaching the app.</b> Open an SSH tunnel to port 8000 (<code>ssh -L 8000:localhost:8000 user@host</code>) and browse to <code>http://localhost:8000</code>; direct access from another machine needs the access token. The host has several addresses (Ethernet, Wi-Fi, Tailscale); use the one on your network.</li>
<li><b>Queue and cancel.</b> <code>/solver-status</code> and the queue position in <code>/runs/&lt;id&gt;/progress.json</code> show what is holding the solver; <code>POST /runs/&lt;id&gt;/cancel</code> stops a queued or running job. Protection scenarios, NbS scenarios and coastal surge runs also use the slot.</li>
<li><b>Publishing.</b> Every change to the application, tests, documentation and plugin is copied into the git clone, secret-scanned, committed and pushed to GitHub; secrets, data, caches and the PostGIS password are excluded.</li>
</ul>
""")

# ---- references
i = s.index('<ul class="refs">'); j = s.index("</ul>", i)
s = s[:j] + """<li>Fernandez, M. &amp; Mourato, S. Comparison of HEC-RAS and LISFLOOD-FP flood inundation models on the River Lis, Portugal. <i>Recursos H&iacute;dricos</i> 34(1) (APRH; University of &Eacute;vora).</li>
<li>Oikonomou, A., et al. Floodplain mapping via 1D and quasi-2D numerical models in the valley of Thessaly, Greece (HEC-RAS, LISFLOOD-FP, FLO-2D).</li>
<li>Rehman, H. u. &amp; Teferle, F. N. R. (2026). LISFLOOD-FP 8.3 hydrodynamic model. Zenodo, doi:10.5281/zenodo.13121119.</li>
<li>Roth, F., et al. (2023). Sentinel-1 flood extent maps for the 2022 Pakistan floods. <i>Natural Hazards and Earth System Sciences</i>, 23, 3305.</li>
<li>Shustikova, I., et al. (2019). Comparing 2D capabilities of HEC-RAS and LISFLOOD-FP on complex topography. <i>Hydrological Sciences Journal</i>, 64(14), 1769&ndash;1782.</li>
<li>Leijnse, T., et al. (2021). Modeling compound flooding in coastal systems using a computationally efficient reduced-physics solver (SFINCS). <i>Coastal Engineering</i>.</li>
<li>N&eacute;elz, S. &amp; Pender, G. (2013). Benchmarking the latest generation of 2D hydraulic modelling packages. Environment Agency, Bristol.</li>
""" + s[j:]

open(P, "w", encoding="utf-8").write(s)
print("built", len(s))
