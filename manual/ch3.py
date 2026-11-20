from m_core import *


def ch8():
    t = [chap("Understanding your results", "c8")]
    t.append("""<p class="lead">A flood run produces many numbers. This chapter explains each one in plain words, how it is worked out, and how much to trust it.</p>""")
    t.append(sec("Depth, speed and time", "s81"))
    t.append("""<table><tr><th style="width:24%">Result</th><th>What it means</th></tr>
<tr><td><b>Flooded area</b></td><td>Land that has more than 5 cm of water at some moment during the run. Shallower films are not counted.</td></tr>
<tr><td><b>Maximum depth</b></td><td>The deepest water anywhere in your area. The <b>Maximum depth</b> map in the 3D view shows the deepest water at every point, whenever it happened.</td></tr>
<tr><td><b>Depth over time</b></td><td>How the water rises and falls. Press play in the 3D view to watch it.</td></tr>
<tr><td><b>Flow speed</b></td><td>How fast the water moves, in metres per second. Fast shallow water can be as dangerous as slow deep water.</td></tr>
<tr><td><b>Arrival and duration</b></td><td>When water first arrives at a point, and how long it stays. Use the time slider and the click-to-pin probe.</td></tr>
<tr><td><b>Volume and water on surface</b></td><td>The total water lying on the ground at that moment, in m³.</td></tr>
</table>""")
    t.append(sec("Hazard to people", "s82"))
    t.append("""<p>The <b>hazard rating</b> combines depth and speed: <b>depth × (speed + 1.5)</b>, taken at the worst moment. It is a simplified form of the rating used in UK flood guidance. SAILAB groups it into four bands:</p>
<table><tr><th>Band</th><th>Rating</th><th>Meaning</th></tr>
<tr><td><span class="pill" style="background:#0ca30c">Low</span></td><td>below 0.75</td><td>Caution: deep or fast enough to be a nuisance or to cause some danger.</td></tr>
<tr><td><span class="pill" style="background:#d99a0b">Moderate</span></td><td>0.75 to 1.25</td><td>Dangerous for some people.</td></tr>
<tr><td><span class="pill" style="background:#ec835a">Significant</span></td><td>1.25 to 2.0</td><td>Dangerous for most people.</td></tr>
<tr><td><span class="pill" style="background:#d03b3b">Extreme</span></td><td>above 2.0</td><td>Dangerous for everyone, and a threat to buildings.</td></tr>
</table>""")
    t.append(sec("Damage classes for buildings", "s83"))
    t.append("""<p>SAILAB takes the peak depth at each building and reads the share of the building's value that would be lost from a standard depth-damage curve (the Asia residential curve of the JRC global flood damage database). The class follows from that share.</p>""")
    t.append(fig("damage_curve", "Depth-damage curve and the four damage classes. A building is dry below 5 cm; minor damage is under half a metre of water, severe damage is over three metres.", "90%"))
    t.append("""<table><tr><th style="width:18%">Class</th><th style="width:24%">Water depth</th><th>Share of value lost</th></tr>
<tr><td>Dry</td><td>5 cm or less</td><td>none</td></tr><tr><td>Minor</td><td>up to 0.5 m</td><td>up to 30 %</td></tr>
<tr><td>Moderate</td><td>0.5 to 1.5 m</td><td>30 to 55 %</td></tr><tr><td>Major</td><td>1.5 to 3 m</td><td>55 to 72 %</td></tr>
<tr><td>Severe</td><td>over 3 m</td><td>over 72 %</td></tr></table>""")
    t.append(sec("Loss in rupees", "s84"))
    t.append("""<p>For every building SAILAB multiplies three things: its <b>floor area</b> (the footprint times the number of storeys), the <b>maximum damage value</b> per square metre, and the <b>damage share</b> from the curve. Contents add a further 50 % of the structure loss. The total is the <b>estimated building loss</b> in Pakistani rupees.</p>
<ul>
<li>The damage value is the Asia average for residential buildings from the JRC database (111 euro per m² at 2010 prices), converted at a current exchange rate (322.5 rupees per euro). It is <b>not</b> adjusted for inflation since 2010.</li>
<li>Where the building height is not known (most buildings), SAILAB assumes 6 m, which is two storeys of 3 m. This doubles the floor area compared with one storey.</li>
<li>Cropland adds a small value per square metre of flooded crops. Built-up land is not counted twice.</li>
</ul>""")
    t.append(warn("Use loss figures to compare scenarios and protection options with each other, not as a forecast of repair cost. The result is a model of an average building, not of any particular one."))
    t.append(sec("Average annual loss", "s85"))
    t.append("""<p>One storm's loss does not tell you how often to expect such losses. <b>Average annual loss</b> (AAL) does: it is the loss you would expect per year, on average, over a long time. SAILAB runs storms of several return periods (for example 10, 50 and 100 years), takes the loss of each, and adds them up weighted by how often each storm happens. A 100-year storm has a 1 % chance each year; a 10-year storm 10 %. Protection that lowers the AAL pays back every year, which is why AAL is the number to compare options and costs with.</p>""")
    t.append(sec("How far to trust the numbers", "s86"))
    t.append("""<ul>
<li>The flood model agrees closely with other accepted flood models given the same inputs. Differences between models are smaller than differences between places.</li>
<li>Results depend on the storm you choose, on the terrain (about 29 m cells), and on missing items such as canals, embankments and river inflow. Check the area on satellite imagery for such features.</li>
<li>In a test on the 2022 Sindh flood, rainfall-only simulation did not reproduce the observed flood extent better than a simple guess, because much of that flood was river water. Add inflows for river floods.</li>
<li>If you have an observed flood map, use <b>Compare with observed flood</b> (Chapter 9) to see how well a run matches reality.</li>
</ul>""")
    return "\n".join(t)


def ch9():
    t = [chap("Reports, exports and sharing", "c9")]
    t.append("""<p class="lead">Everything you see can be taken away: pictures, a video, a PDF report, or a small file that lets you or a colleague run the same scenario again.</p>""")
    t.append(sec("From the 3D view", "s91"))
    t.append("""<ul>
<li><b>Export ▾ menu</b> (bottom of the 3D view): save the current picture as a <b>PNG</b>, or record the animation as an <b>MP4 video</b>. The view first returns to its standard camera angle so the picture is never taken from an awkward angle. A video takes as long as one playback.</li>
<li><b>Full page</b> (top of the flood card): opens the 3D view on its own page, good for sharing a link on your network or presenting on a big screen.</li>
</ul>""")
    t.append(sec("From the Runs tab", "s92"))
    t.append("""<table><tr><th style="width:30%">Button</th><th>What you get</th></tr>
<tr><td><b>Full 2D report</b></td><td>A report page with maps, tables and the settings of the run. Open it in a new tab and print or save it.</td></tr>
<tr><td><b>Export PDF</b></td><td>The same report as a PDF file.</td></tr>
<tr><td><b>Susceptibility map (PNG)</b></td><td>A finished map with classes, legend, scale bar and key figures, ready for a document.</td></tr>
<tr><td><b>Save run file</b></td><td>A small file (<code>.sailab.json</code>) with the area and every setting. Load it under <i>Own elevation model or saved run</i> to repeat or change the run. It does not contain terrain data; a custom elevation model must be uploaded again.</td></tr>
<tr><td><b>Compare with observed flood</b></td><td>Upload a map of a real flood (a polygon file, or a GeoTIFF where values above 0 mean flooded) and SAILAB scores the run against it: how much of the real flood the model found, how much it flooded wrongly, and an overall score.</td></tr>
<tr><td><b>Calibrate roughness and infiltration</b></td><td>With an observed flood map, SAILAB searches for the ground roughness and infiltration that make the run match best. It fits one event, so check the result on a second event before relying on it.</td></tr>
</table>""")
    t.append(sec("Comparing runs", "s93"))
    t.append("""<p>Every run has a run identifier (a 16-character code). To compare two runs on the same area, paste their identifiers into <b>Compare two runs</b> and click <b>Compare</b>: you get the change in flooded area, mean depth and loss, a map of where the two differ, and links to a full comparison report and a PDF. Runs on different areas cannot be compared. After a return-period batch, a <b>Cmp</b> button next to each row compares it with your main run in one click.</p>""")
    return "\n".join(t)


def ch10():
    t = [chap("Troubleshooting and questions", "c10")]
    t.append(sec("Messages you may see", "s101"))
    t.append("""<table><tr><th style="width:36%">Message or problem</th><th>What to do</th></tr>
<tr><td>This area is outside Pakistan, or crosses the border.</td><td>Draw an area completely inside Pakistan. SAILAB has data for Pakistan only.</td></tr>
<tr><td>This area is too large for a standard run. The run was not started.</td><td>Draw a smaller area, or use a shorter storm. The message tells you roughly how large an area is allowed. For a bigger area, contact the SAILAB team.</td></tr>
<tr><td>No terrain data is available for this area.</td><td>The area is outside the terrain coverage. Choose an area inside Pakistan, or upload your own elevation model that covers it.</td></tr>
<tr><td>The uploaded elevation model does not fully cover the selected area.</td><td>Upload a model that covers the whole area, or draw a smaller area inside it.</td></tr>
<tr><td>Queue: 1 job (or more), and my run is waiting.</td><td>The flood model runs one job at a time. Your run starts when the earlier ones finish. You can cancel a waiting run in Run status.</td></tr>
<tr><td>The run failed.</td><td>Try a smaller area or a lower peak rainfall, and keep roughness at 0.03 or more. If it fails again, contact the team and give the run identifier.</td></tr>
<tr><td>Nothing is flooded.</td><td>The rain may be too light, the infiltration too high, or the area too high above any water. Raise the rain, set infiltration to 0, or add an inflow.</td></tr>
<tr><td>The 3D view is blank or slow.</td><td>It needs a recent browser and a graphics card. Close other heavy tabs, or use <b>Full page</b>. The numbers in the Runs and Impact tabs do not depend on the 3D view.</td></tr>
<tr><td>The map does not load.</td><td>The map needs an internet connection to fetch map tiles. Check your connection and reload.</td></tr>
<tr><td>Instant shows flooding but Physics shows little (or the other way round).</td><td>They use different methods. Instant follows drainage lines and fills low spots from the terrain; Physics moves rain over the surface with roughness, soil, buildings and inflows. For decisions trust Physics.</td></tr>
</table>""")
    t.append(sec("Frequently asked questions", "s102"))
    t.append("""<p><b>Can I use SAILAB outside Pakistan?</b> No. SAILAB is built on Pakistan's terrain and datasets, and the map shows Pakistan only.</p>
<p><b>How long does a run take?</b> A typical area of a few square kilometres takes one to three minutes, including buildings, risk and the 3D scene. The flood model itself takes seconds for small areas and about a minute and a half for four million grid cells.</p>
<p><b>Why is my area not allowed even though it is in Pakistan?</b> It is probably larger than a flood run can handle in about ten minutes. See Section 3.4.</p>
<p><b>Does SAILAB use live data?</b> Rainfall forecasts and design storms come from public data services. SAILAB does not use live river gauges.</p>
<p><b>Can I trust the loss in rupees?</b> Use it to compare options. It uses a regional damage table and today's exchange rate (Section 8.4).</p>
<p><b>Is my data private?</b> The areas you draw and the files you upload stay on the SAILAB server. Ask your administrator about access and sharing.</p>
<p><b>Can I get alerts when heavy rain is forecast?</b> SAILAB can watch an area and start a run and send an alert to a Slack, Discord or similar channel when forecast rain crosses a threshold you choose. This is set up with the team; ask them to enable it for your area.</p>""")
    return "\n".join(t)


def ch11():
    t = [chap("Glossary and data sources", "c11")]
    t.append(sec("Glossary", "s111"))
    t.append("""<table><tr><th style="width:24%">Term</th><th>Meaning</th></tr>
<tr><td>Area (AOI)</td><td>The part of the map you choose to study.</td></tr>
<tr><td>Return period</td><td>How often a storm of a given size is expected: a 100-year storm has a 1 % chance in any year.</td></tr>
<tr><td>Design storm</td><td>A standard storm for a return period, with a set total rain and duration.</td></tr>
<tr><td>Hyetograph</td><td>The shape of rain over time. SAILAB uses a triangle.</td></tr>
<tr><td>Terrain / DEM</td><td>A digital elevation model: the height of the ground in every cell of a grid (about 29 m here).</td></tr>
<tr><td>Manning's n</td><td>A roughness number for the ground. Smooth pavement is low, dense forest is high.</td></tr>
<tr><td>Infiltration</td><td>Water soaking into the ground instead of running over it.</td></tr>
<tr><td>Inflow / outflow</td><td>Water entering your area from outside, such as a river, or leaving it.</td></tr>
<tr><td>Discharge</td><td>Flow of water, in cubic metres per second (m³/s).</td></tr>
<tr><td>GLOF</td><td>Glacial lake outburst flood: a lake held by ice or rubble suddenly releases its water.</td></tr>
<tr><td>Storm surge / tide</td><td>Sea level raised by wind and pressure (surge) and by the moon and sun (tide).</td></tr>
<tr><td>Hazard rating</td><td>A number combining depth and speed to show danger to people.</td></tr>
<tr><td>Damage class</td><td>Minor, moderate, major or severe, from the share of a building's value lost.</td></tr>
<tr><td>AAL</td><td>Average annual loss: expected loss per year over the long run.</td></tr>
<tr><td>Nature-based solution</td><td>Changing land cover, such as planting forest or restoring grassland, to slow and soak up water.</td></tr>
<tr><td>CSI, POD, FAR</td><td>Scores for comparing a modelled flood with a mapped one: found share, false-alarm share, and an overall score.</td></tr>
</table>""")
    t.append(sec("Where the data comes from", "s112"))
    t.append("""<table><tr><th style="width:26%">Layer</th><th>Source</th></tr>
<tr><td>Terrain</td><td>SRTM elevation data for Pakistan (about 29 m), or your own uploaded elevation model.</td></tr>
<tr><td>Buildings</td><td>Google Open Buildings, with heights from OpenStreetMap where tagged.</td></tr>
<tr><td>Roads, railways, facilities</td><td>OpenStreetMap (HOTOSM export), national schools and hospitals data, power lines, airports and ports.</td></tr>
<tr><td>Land cover and roughness</td><td>ESA WorldCover (10 m).</td></tr>
<tr><td>Soils</td><td>SoilGrids.</td></tr>
<tr><td>Rainfall</td><td>ERA5-Land and IMERG records for intensity curves; CMIP6 climate data for design storms; Open-Meteo for forecasts.</td></tr>
<tr><td>Rivers</td><td>OpenStreetMap waterways; GloFAS discharge for context.</td></tr>
<tr><td>Dams, glacial lakes</td><td>Global Dam Watch, the PMD glacial lake inventory, HydroLAKES, and a catalogue of past outburst events.</td></tr>
<tr><td>Tide and surge</td><td>Karachi tide gauge, GTSR/GTSM reanalysis, and published wave run-up relations.</td></tr>
<tr><td>Flood model</td><td>LISFLOOD-FP 8.2, running on the GPU.</td></tr>
</table>""")
    t.append(sec("Getting help", "s113"))
    t.append("""<p>For questions, a larger-area request, alerts for your area, or a problem you cannot solve, contact the <b>SAILAB team at AI Geo Navigators, Islamabad</b>. When you write, include the run identifier (shown in the Runs tab) and what you were doing.</p>""")
    return "\n".join(t)
