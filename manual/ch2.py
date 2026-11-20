from m_core import *


def ch5a():
    t = [chap("SAILAB Physics", "c5")]
    t.append("""<p class="lead">Physics runs the full flood model. You describe a storm (or a river flow), SAILAB moves water over the terrain of your area, and then measures what the water does to buildings, roads and people.</p>""")
    t.append(sec("How a Physics run works", "s51"))
    t.append(fig("pipeline", "The five stages of a Physics run. You can watch them in the Runs tab.", "96%"))
    t.append(steps("<b>Choose an area</b> (Chapter 3).", "<b>Describe the scenario</b> in the Scenario tab: storm, boundaries, soil.", "<b>Click Run flood model.</b> SAILAB switches to the Runs tab and shows progress.",
                   "<b>Explore the result</b> in the 3D view, then open <b>Impact</b> for damage and loss, <b>Runs</b> for comparisons and reports, and <b>Mitigation</b> to test protection."))
    t.append(sec("The Scenario tab", "s52"))
    t.append("""<p>The Scenario tab has everything you set before a run. From top to bottom: <b>Area</b>, <b>Storm</b>, <b>Boundary conditions</b>, <b>Advanced settings</b>, <b>Soil &amp; infiltration</b>, and the <b>Run flood model</b> button.</p>""")
    t.append(h3("Storm"))
    t.append(shot("p_storm", "The Storm section.",
                  ["<b>Peak rainfall</b> in mm per hour (default 40).", "<b>Duration</b> in hours (default 2).", "<b>Design storm by return period</b>: a standard storm for a chosen return period.",
                   "<b>Recorded rainfall curve (IDF)</b>: an intensity actually recorded in your area.", "<b>Upload rain-gauge data (CSV)</b>: use your own measured rain."]))
    t.append(fig("storm_shape", "SAILAB shapes the storm as a triangle that peaks half-way through. The total rain is half of peak times duration.", "96%"))
    t.append("""<p>You have four ways to set the rain. Use one of them.</p>
<table><tr><th style="width:27%">Way</th><th>How to use it</th></tr>
<tr><td><b>Type it in</b></td><td>Enter the peak rainfall and duration. A 40 mm/hr peak over 2 hours delivers 40 mm in total. Good for what-if tests.</td></tr>
<tr><td><b>Design storm by return period</b></td><td>Open the section, choose a return period (2 to 200 years) and a climate scenario (current climate or a future one), then click <b>Fetch real design storm</b>. The first request for an area can take one to three minutes; later ones are instant. SAILAB fills in a 24-hour storm whose total equals the design rainfall for that return period (for example about 130 mm for the 100-year storm in Rawalpindi).</td></tr>
<tr><td><b>Recorded rainfall curve (IDF)</b></td><td>Choose a duration and a season, then click <b>Use real recorded intensity</b>. SAILAB fills in an intensity that was actually recorded near your area (from satellite and reanalysis records).</td></tr>
<tr><td><b>Upload rain-gauge data (CSV)</b></td><td>Choose a file of rain measurements, for example 15-minute gauge data. If the file has coordinates, SAILAB picks the station nearest your area; if it has only place names, it shows a list and you pick the station. The measured series then drives the run instead of the triangle.</td></tr>
</table>""")
    t.append(note("If you choose a CSV file and it cannot be read, SAILAB asks before running with the typed-in values, so you never run the wrong storm by accident."))
    t.append(h3("Boundary conditions: rivers in, water out"))
    t.append("""<p>Rain falls on your whole area. Rivers and canals bring in water that fell somewhere else, so a rain-only run can miss much of a river flood. Add an <b>inflow</b> where a river enters your area. An <b>outflow</b> says where water is allowed to leave.</p>""")
    t.append(shot("p_boundary", "The Boundary conditions section.",
                  ["<b>Detect rivers automatically</b> finds rivers, streams and canals that cross your area and proposes inflows.", "<b>Add manual inflow</b>: you choose edge, position, width and flow.", "<b>Add manual outflow</b>: you choose where water leaves.", "<b>Advanced settings</b> (see below)."]))
    t.append(fig("edges", "The model runs on the rectangle around your area. Inflows and outflows sit on its north, east, south or west edge.", "62%"))
    t.append("""<table><tr><th style="width:30%">Field</th><th>Meaning</th></tr>
<tr><td>Edge</td><td>North, East, South or West edge of the rectangle around your area.</td></tr>
<tr><td>Position along edge (%)</td><td>Where on that edge: 0 is one end, 100 the other. SAILAB moves it to the nearest edge cell that has terrain.</td></tr>
<tr><td>Width (m)</td><td>Width of the channel. Detected rivers use typical widths: river 40 m, stream 8 m, canal 15 m.</td></tr>
<tr><td>Peak discharge (m³/s)</td><td>The largest flow entering. Inflows rise to this peak and fall again over the duration.</td></tr>
<tr><td>Duration (hr)</td><td>How long the inflow lasts. 0 means a constant flow.</td></tr>
</table>
<p>By default water can leave across all four edges. As soon as you add an outflow, only the outflows you add are open and the rest of the edge is closed. Each item has a tick box so you can switch it off without deleting it.</p>""")
    t.append(warn("SAILAB has no river-bed depth data. Forcing a very large flow into a narrow channel can give unrealistically deep water close to the inlet. Use realistic flows, and treat the area right at the inlet with care."))
    t.append(tip("A run with no rain (0 mm/hr) and a large inflow is allowed: it models a pure river flood. If the inflow edge has no terrain, SAILAB cuts a short flat feeder channel to the nearest terrain, and tells you if no inflow can be placed at all."))
    t.append(h3("Advanced settings"))
    t.append(shot("p_advanced", "Advanced settings, opened.", None))
    t.append("""<table><tr><th style="width:30%">Setting</th><th>What it does</th></tr>
<tr><td>Fallback friction (Manning's n)</td><td>Roughness of the ground (default 0.05). SAILAB takes roughness cell by cell from land cover (forest, crops, buildings, water). This number is used only where land cover is missing. Higher means water flows more slowly.</td></tr>
<tr><td>CFL number</td><td>Stability of the solver, between 0 and 1 (default 0.7). Leave it unless a run is reported unstable.</td></tr>
<tr><td>Areal Reduction Factor</td><td>Reduces point rainfall for large areas, because storms are not equally heavy everywhere.</td></tr>
<tr><td>Storm centering</td><td>Places the heaviest rain at a point you choose and lets it fade with distance. Optional storm motion prediction.</td></tr>
<tr><td>Condition river channel depth</td><td>Cuts a river channel into the terrain at inflows. Only useful with at least one inflow.</td></tr>
</table>""")
    t.append(h3("Soil &amp; infiltration"))
    t.append(shot("p_soil", "Soil, buildings and the Run button.",
                  ["<b>Infiltration rate</b> in mm per hour: water that soaks into the ground (default 0).", "<b>Use soil-derived rate</b>: take the rate from soil data (SoilGrids).", "<b>Soil moisture</b>: how wet the ground is before the storm.",
                   "<b>Include buildings</b>: add real buildings as obstacles and count their damage.", "<b>Run flood model</b>."]))
    t.append("""<p>Dry or sandy soil absorbs rain; saturated or clay soil does not. With the default of 0 mm/hr, all the rain stays on the surface, which is the cautious choice for a heavy storm on wet ground. Options:</p>
<ul>
<li><b>Infiltration rate.</b> Type a rate; typical values are 0 for paved or saturated ground and 5 to 20 mm/hr for loose soils.</li>
<li><b>Use soil-derived rate.</b> SAILAB reads the sand and clay content of your area and sets a single average rate.</li>
<li><b>Soil moisture slider.</b> Slide towards <b>Wet</b> for ground already soaked by earlier rain (less absorption) and towards <b>Dry</b> for dry ground (more absorption). The middle is average.</li>
</ul>""")
    t.append(h3("Buildings and the Run button"))
    t.append("""<p>With <b>Include buildings</b> ticked (the default), SAILAB fetches the real building outlines of your area, uses them as obstacles for the water, and counts their damage and loss. Untick it for a faster run on open land or to see the water without buildings. Then click <b>Run flood model</b>.</p>""")
    return "\n".join(t)


def h3(x): return f"<h3>{x}</h3>"
