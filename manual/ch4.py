from m_core import *
from ch2 import h3


def ch5b():
    t = []
    t.append(sec("Running the flood model", "s53"))
    t.append(steps("Check the area, storm and settings in the Scenario tab.", "Click <b>Run flood model</b>. SAILAB switches to the <b>Runs</b> tab.",
                   "Watch <b>Run status</b>. A bar shows overall progress and a checklist shows the stage you are in.",
                   "When the status says <b>Complete</b>, the 3D view opens and the numbers appear."))
    t.append(shot("p_running2", "Run status while a run is in progress.",
                  ["The checklist: Preparing DEM, Fetching real buildings, Running flood simulation, Fetching roads &amp; processing results, Building 3D visualization, Done. A tick means the stage is finished; the filled dot is the current stage."]))
    t.append("""<ul>
<li><b>Queue.</b> The flood model runs one job at a time so everyone gets full speed. The <b>Queue</b> label in the top bar shows <i>idle</i> or how many jobs are waiting. If your run has to wait, Run status shows your place in the queue and what is running.</li>
<li><b>Cancel.</b> While a run is waiting or running, a <b>Cancel this run</b> button appears under the status. A run cannot be cancelled once it is building results.</li>
<li><b>Same inputs, same answer.</b> If you repeat a run with exactly the same area and settings, SAILAB returns the saved result in a moment instead of running again.</li>
<li><b>Too large.</b> If your area and storm would take too long, the run is refused at once with an explanation (Section 3.4).</li>
</ul>""")
    t.append(sec("Reading the result", "s54"))
    t.append(shot("v_main", "The finished run in the 3D view, with the Runs summary on the right.",
                  ["<b>What to show</b>: four ways to colour the flood.", "<b>Depth scale</b>: the legend for the current view.", "<b>Maximum depth</b> card: the deepest water at every point.", "<b>Buildings</b> and other map layers on and off.",
                   "<b>Fine-tune water and terrain</b>: hide very shallow water, change opacity and flow lines.", "<b>Play / pause</b> the animation.", "Live totals: <b>flooded area, deepest water, water on surface</b>.",
                   "<b>Rain and flooded-area chart</b> over the whole run.", "<b>Time slider</b>: drag to any moment.", "<b>Playback speed</b>.", "<b>Export</b>: PNG picture or MP4 video.", "<b>Rain</b>: show or hide falling rain.", "<b>Reset view</b>: return to the standard camera."]))
    t.append(h3("The four views"))
    t.append("""<table><tr><th style="width:24%">View</th><th>Shows</th></tr>
<tr><td><b>Depth over time</b></td><td>The animated flood. Blue gets darker as water gets deeper; the water rises and falls with the time slider.</td></tr>
<tr><td><b>Maximum depth</b></td><td>One still picture: the deepest water that ever occurred at each point. Best for a report.</td></tr>
<tr><td><b>Flow speed</b></td><td>How fast the water moves at each point: yellow and orange is faster.</td></tr>
<tr><td><b>Flow paths</b></td><td>White streaks that follow the water; brighter streaks are faster. They show where water is channelled between buildings and along roads.</td></tr>
</table>""")
    t.append(h3("Moving around the 3D view"))
    t.append("""<table><tr><th style="width:34%">Do this</th><th>To get this</th></tr>
<tr><td>Drag with the left mouse button</td><td>Move the map.</td></tr>
<tr><td>Right-drag, or <span class="kbd">Ctrl</span> + drag</td><td>Rotate and tilt. The tilt is limited so the view never becomes an edge-on sliver.</td></tr>
<tr><td>Scroll wheel</td><td>Zoom in and out.</td></tr>
<tr><td><b>Reset view</b></td><td>Return to the standard angle. Pictures and videos are always taken from it.</td></tr>
<tr><td><b>Map style</b> tiles</td><td>Dark, Light, Satellite, Streets, Terrain or Night base map. The flood and buildings stay on top.</td></tr>
<tr><td><b>Terrain</b> pill</td><td>Switch the 3D terrain on or off; click the number to exaggerate the relief.</td></tr>
<tr><td><b>Full page</b> (top of the card) / <b>Expand</b></td><td>Make the 3D view fill the page.</td></tr>
</table>""")
    t.append(h3("Click to pin a depth probe"))
    t.append(shot("v_probe", "A probe pinned at point A. Here the point is dry at this moment; on wet ground it shows the depth.", ["The probe: the state at the current time, the flow speed, the highest depth reached there, the ground height, and a chart of depth over the whole event."]))
    t.append("""<p>Click any wet point to pin a <b>probe</b>. It shows the depth at the current time, when the water first arrived, the flow speed, the highest depth reached and the ground height there. Use the close button on the probe to remove it.</p>""")
    t.append(h3("The summary on the right"))
    t.append("""<table><tr><th style="width:30%">Item</th><th>Meaning</th></tr>
<tr><td>Flooded area, Max depth</td><td>Land with more than 5 cm of water, and the deepest water anywhere.</td></tr>
<tr><td>Max flow velocity, Mean velocity (flooded)</td><td>The fastest flow anywhere and the average over flooded cells, in m/s.</td></tr>
<tr><td>Peak hazard</td><td>The worst hazard band reached: low, moderate, significant or extreme (Section 8.2).</td></tr>
<tr><td>Solver, Time taken</td><td>Which solver ran, and how long the flood model itself took.</td></tr>
<tr><td><b>What this depth means</b></td><td>A person drawn next to a depth you choose with the slider, with plain safety advice for ankle, knee, waist, chest and head height.</td></tr>
</table>""")
    t.append(sec("The Runs tab", "s55"))
    t.append(shot("p_runs_c", "The lower part of the Runs tab.", None))
    t.append("""<table><tr><th style="width:34%">Section</th><th>What it is for</th></tr>
<tr><td><b>Full 2D report, Export PDF, Susceptibility map, Save run file</b></td><td>Take the result away (Chapter 9).</td></tr>
<tr><td><b>Compare with observed flood</b></td><td>Score the run against a mapped real flood (Chapter 9).</td></tr>
<tr><td><b>Calibrate roughness and infiltration</b></td><td>With an observed flood map, find the ground settings that fit it best. Choose how thorough the search is (for example Balanced, 24 runs).</td></tr>
<tr><td><b>Rebuild 3D view</b></td><td>Redraws the 3D scene from the saved results without running the model again.</td></tr>
<tr><td><b>Return-period runs</b></td><td>Runs several design storms in a row (2, 5, 10, 25, 50, 100 or 200 years) on your area and lists flooded area, depth, hazard and loss for each. Tick the periods and click <b>Run all selected return periods</b>. A bar shows progress. Afterwards you can open a batch report.</td></tr>
<tr><td><b>Average annual loss</b></td><td>Runs the return periods with buildings on and combines their losses into one yearly figure (Section 8.5). It takes as long as the runs it needs.</td></tr>
<tr><td><b>Compare two runs</b></td><td>Paste two run identifiers to see what changed (Section 9.3). Your latest run's identifier is filled in for you.</td></tr>
</table>""")
    t.append(shot("p_runs2", "Return-period runs, annual loss and comparison.", ["Run the selected return periods.", "Compute the average annual loss.", "Compare two runs."]))
    t.append(sec("The Impact tab", "s56"))
    t.append(shot("p_impact", "Buildings and infrastructure in the Impact tab.", None))
    t.append("""<p>The Impact tab reports what the flood does to the things people depend on, using the peak depth at each one.</p>
<ul>
<li><b>Buildings.</b> The number of real buildings in the run, a count by damage class (dry, minor, moderate, major, severe), and the <b>estimated structure, content and total building loss</b> in rupees. Section 8.4 explains how it is worked out.</li>
<li><b>Hospitals and schools, fuel and chemical sites, power lines, railways, airports, ports.</b> For each category: how many exist in your area, how many are flooded, and how they split by hazard band. Inside Islamabad, shelters, police posts, and water and wastewater facilities are listed too.</li>
<li><b>Roads.</b> Roads flooded by hazard band, split into major and local roads, with kilometres flooded and the number of flooded bridges (a bridge counts as flooded when the ground under it is, so this is an upper estimate).</li>
<li><b>Damage and hotspots.</b> Damage by land-use class and where damage is concentrated.</li>
<li><b>Sediment.</b> A first estimate of where the flow is strong enough to move soil, from grain size (which you can take from soil data).</li>
</ul>""")
    t.append(tip("Click any category to see its details. If a figure looks surprising, check the area on satellite imagery: a school may sit just outside the flooded part, or a building may be a shed."))
    t.append(sec("The Mitigation tab", "s57"))
    t.append(shot("p_mitigate", "The Mitigation tab: nature-based options and protection structures.", None))
    t.append("""<p>Test protection before spending money on it. Every test re-runs the same flood on a changed terrain or land cover and compares the result with your baseline run, which must be finished first.</p>""")
    t.append(h3("Protection structures"))
    t.append(steps("Run your baseline flood first.", "Use the <b>line tool</b> on the map to draw a line where the structure would go (for example across a flow path).",
                   "In the structure list, choose the type and adjust height or width. Types: <b>Dam / embankment</b> (raises the ground), <b>Barrage</b> (raises the ground), <b>Diversion canal</b> and <b>Drainage pipe</b> (lower the ground to carry water), and <b>Rainwater catchment / retention basin</b> (a dug basin that holds water).",
                   "Click <b>Run protection scenario</b>. When it finishes you see the area protected, the area newly flooded because water was pushed elsewhere, and the building loss avoided in rupees."))
    t.append(note("Protection is not free: a wall that keeps one street dry can push water onto its neighbours. SAILAB reports that as <b>displaced</b> flooding next to the benefit, so you see both."))
    t.append(h3("Find the best placement automatically"))
    t.append(steps("Tick the structure types you are willing to build.", "Choose the <b>number of structures</b> (1 to 3) and the <b>search effort</b> (Quick, Balanced or Thorough).",
                   "Click <b>Auto-optimize placement</b>. SAILAB tries many placements, running the real flood model for each, and reports the best one. The result opens like a manual scenario."))
    t.append("""<p>The search maximises the share of building loss avoided and penalises water displaced elsewhere. More effort tries more placements and takes longer: Quick tries about 12, Balanced 24 and Thorough 48. The result is a good placement, not a proven best, and dimensions are defaults, not engineering designs.</p>""")
    t.append(h3("Nature-based solutions"))
    t.append(steps("Choose an <b>intervention</b>: reforestation, wetland restoration or grassland restoration.",
                   "Choose a <b>pathway</b>: <b>active</b> (the land becomes that cover straight away) or <b>passive</b> (one natural step of recovery).",
                   "Click <b>Run NbS scenario</b>, or click <b>Auto-recommend best NbS option</b> to try all six combinations and rank them."))
    t.append("""<p>These change the whole area's land cover, which changes ground roughness and the average infiltration rate. The result reports the flooding change and the loss avoided in rupees, in the same way as a structure.</p>""")
    t.append(tip("To weigh a protection option against its cost, run the return-period batch for the baseline and the option, and compare their average annual loss. The difference is the yearly benefit."))
    return "\n".join(t)


def ch6():
    t = [chap("SAILAB GLOF", "c6")]
    t.append("""<p class="lead">GLOF mode is for floods released by a glacial lake outburst or a dam. It helps you find the lake or dam, estimate the peak flow it could release, and run the flood that follows down your valley.</p>""")
    t.append(sec("Step by step", "s61"))
    t.append(steps("Open <b>GLOF</b> and choose your area <b>downstream</b> of the lake or dam (Chapter 3). Valleys of Gilgit-Baltistan and Khyber Pakhtunkhwa are typical.",
                   "In the <b>Scenario</b> tab, open <b>GLOF scenario</b> and choose a <b>Source</b>.", "Click <b>Apply discharge</b>. SAILAB estimates the peak flow and adds it as an inflow.",
                   "Set the inflow position under <b>Boundary conditions</b> (<b>Detect rivers automatically</b> helps).", "Click <b>Run GLOF flood</b> and use the tabs exactly as in Physics."))
    t.append(shot("g_scenario", "The GLOF scenario section.",
                  ["<b>Source</b> of the lake information.", "<b>Apply discharge</b>: estimate the peak flow and add it as an inflow.", "<b>Past outbursts nearby</b>: documented events around your area."]))
    t.append(sec("Choosing the source", "s62"))
    t.append("""<table><tr><th style="width:34%">Source</th><th>Use it when</th></tr>
<tr><td><b>Shishper Glacier Lake, Hunza (2022 event)</b></td><td>You study the Hasanabad and Hunza reach. This uses the documented 2022 outburst: peak flow about 3,737 m³/s from about 39 million m³ of water.</td></tr>
<tr><td><b>PMD glacial lake near the area</b></td><td>You want a lake from the national glacial lake inventory. Click <b>Find nearby lakes</b> and pick one from the list. The volume is estimated from its area.</td></tr>
<tr><td><b>HydroLAKES lake near the area</b></td><td>The lake is in the global lake database. Pick it from the list of nearby lakes.</td></tr>
<tr><td><b>Known lake volume / area</b></td><td>You know the lake's volume (m³) or surface area (km²). Type it in; SAILAB estimates the peak flow from it.</td></tr>
</table>""")
    t.append(shot("g_result", "After Apply discharge.", ["The estimate: peak discharge, the volume used and the basis, with the range seen in documented events."]))
    t.append(warn("A lake-volume estimate is an order-of-magnitude figure: across documented events it can be several times too high or too low. SAILAB shows the typical range next to it. Where a documented event exists for your lake, prefer it."))
    t.append(sec("Past outbursts nearby", "s63"))
    t.append(shot("g_events", "Documented outbursts near the area.", ["A list of past events, nearest first, with date, lake and any recorded volume or peak flow. Use one to add its documented flow as an inflow."]))
    t.append("""<p>Click <b>Past outbursts nearby</b> to see recorded glacial lake outburst events within a chosen distance of your area. Events with a recorded peak flow, or a recorded volume, have a button to use them as the inflow, so you can replay a real event over your valley.</p>""")
    t.append(sec("Placing the inflow and running", "s64"))
    t.append(shot("g_boundary", "Place the outburst as an inflow.", ["Detect rivers automatically finds the river at the edge of your area and sets the inflow there."]))
    t.append("""<p>An outburst enters your area along the river below the lake. Use <b>Detect rivers automatically</b>, or <b>Add manual inflow</b> on the edge where the river enters. Then click <b>Run GLOF flood</b>. The run, the 3D view, Impact and Runs tabs work exactly as described in Chapter 5. Add <b>no rain</b> (0 mm/hr) to see the outburst on its own.</p>""")
    t.append(sec("The Glacier tab", "s65"))
    t.append(shot("g_glacier", "Glacier monitoring and landslide susceptibility.",
                  ["<b>Glacier outlines</b>: show glaciers in the area.", "<b>Run glacier movement analysis</b>: measure how fast the ice moved over a period of satellite images.", "<b>Check recent glacier speed</b>: compare recent speed with earlier years.", "<b>Check landslide susceptibility</b>."]))
    t.append("""<p>The Glacier tab gives early-warning information that can tell you whether a glacial hazard deserves a flood run. <b>Glacier outlines</b> shows the glaciers in your area. <b>Run glacier movement analysis</b> and <b>Check recent glacier speed</b> use satellite images to see whether ice is speeding up, which can come before a surge or a lake outburst. <b>Check landslide susceptibility</b> rates the slopes of your area from rock type and a trained model. These are screening tools: a flag means <i>look closer</i>, not <i>a flood is coming</i>.</p>""")
    t.append(note("Some of these checks use satellite services and can take a few minutes. They need the service to be available when you click."))
    return "\n".join(t)


def ch7():
    t = [chap("SAILAB Coastal", "c7")]
    t.append("""<p class="lead">Coastal mode answers: how far inland would the sea reach for a given storm tide, and how would a surge move over the land? It covers the Sindh and Balochistan coast and uses the same area, map and 3D view as the other modes.</p>""")
    t.append(sec("Static flood level", "s71"))
    t.append(steps("Open <b>Coastal</b> and choose an area that includes the coast (Karachi, Port Qasim and Gwadar are typical).", "On the <b>Static</b> tab choose the <b>Water-level source</b> and a <b>Return period</b>.",
                   "Optionally add <b>Sea-level rise</b> (in metres) or a <b>Future year</b>.", "Click <b>Run coastal flood</b>. The flooded area, depth and water level appear in seconds."))
    t.append(shot("c_static", "The Static tab.",
                  ["<b>Water-level source</b>: the Karachi tide gauge (50 to 1000 years) or a global model (2 to 100 years).", "<b>Return period</b> of the storm tide.", "<b>Sea-level rise</b> in metres on top.", "<b>Run coastal flood</b>.",
                   "<b>Water-level slider</b>: step through levels to see how the flood grows, without a new run."]))
    t.append(shot("c_result", "A static coastal result.", ["The water level used, the area flooded and the deepest water, with the source of the level."]))
    t.append("""<table><tr><th style="width:30%">Setting</th><th>Meaning</th></tr>
<tr><td><b>Water-level source</b></td><td>The <b>Karachi tide gauge</b> gives return levels for 50 to 1,000 years from a long measured record. The <b>GTSM model</b> gives 2 to 100 years at the nearest model station. They can differ, so check which one suits your question.</td></tr>
<tr><td><b>Return period</b></td><td>How rare the storm tide is.</td></tr>
<tr><td><b>Or a past event</b></td><td>Use the level of a documented past surge instead.</td></tr>
<tr><td><b>Future year, Sea-level rise</b></td><td>Raise the water level for a future year or by a fixed amount (for example 0.5 m).</td></tr>
<tr><td><b>Include wave run-up</b></td><td>Adds the extra height that waves climb up the beach.</td></tr>
<tr><td><b>Water-level slider</b></td><td>A table of levels is calculated once for your area, so you can slide the water up and down instantly and use <b>Show on map</b> for the level you like.</td></tr>
</table>""")
    t.append(tip("The static method fills every low area connected to the sea up to the chosen level. It is quick and good for screening, but it does not show how fast water arrives."))
    t.append(sec("Surge: animated preview and full simulation", "s72"))
    t.append(shot("c_surge", "The Surge tab.",
                  ["<b>Water level based on</b>: the return-period level, or the highest surge recorded near your area.", "<b>Tide</b>: ignore it, or add a real tidal cycle with the surge peaking at high or low tide.", "<b>Preview</b>: a fast animation.",
                   "<b>Surge window</b>: how many days of the recorded event to use.", "<b>Run surge simulation</b>: the full flood model.", "<b>Add rainfall</b>: combine rain with the surge (compound flood)."]))
    t.append("""<table><tr><th style="width:26%">Option</th><th>What it gives</th></tr>
<tr><td><b>Preview</b></td><td>A quick animated flood that follows the rise and fall of the surge. It treats the water as being in balance with the sea at each moment, so it is an approximation. Takes a few seconds.</td></tr>
<tr><td><b>Run surge simulation</b></td><td>The real flood model driven by a tide-and-surge sea boundary. It shows how water moves inland over time. A multi-day surge takes several minutes; a shorter surge window runs faster. The result opens in the 3D view.</td></tr>
<tr><td><b>Tide</b></td><td>Adds the predicted tide of the Karachi gauge. You choose when the surge peaks relative to the tide: at spring high tide (the worst case), an ordinary high tide, or a low tide.</td></tr>
<tr><td><b>Add rainfall</b></td><td>Adds rain (mm/hr and duration) on the land during the surge, for a compound flood.</td></tr>
</table>""")
    t.append(warn("The sea enters through one edge of your area. Draw the area so that edge reaches the open sea; if the sea edge is high ground, a full surge simulation can show less flooding than the static method. One tide gauge (Karachi) is used for the whole coast."))
    t.append(shot("c_preview", "The animated surge preview in the 3D view.", None))
    return "\n".join(t)
