from m_core import *


def cover(version, date):
    return f"""<div class="cover">
<div class="brand">SAILAB</div>
<h1>User Manual</h1>
<div class="sub">From rainfall to risk, modeled. A step-by-step guide to simulating floods, measuring damage and testing protection anywhere in Pakistan.</div>
<svg class="wave" viewBox="0 0 600 280" xmlns="http://www.w3.org/2000/svg"><path d="M0 170 C80 110 160 230 260 170 S440 110 600 170 L600 280 L0 280Z" fill="#4fd1c5" opacity=".35"/><path d="M0 205 C90 150 170 255 270 205 S450 150 600 205 L600 280 L0 280Z" fill="#4fd1c5" opacity=".5"/><path d="M0 240 C100 200 190 275 290 240 S460 200 600 240 L600 280 L0 280Z" fill="#fff" opacity=".35"/></svg>
<div class="meta"><b>SAILAB version {version}</b> &nbsp;·&nbsp; {date}<br>Simulation &amp; Analytics for Inundation, Loss, Assets &amp; Built Environment<br>AI Geo Navigators, Islamabad, Pakistan</div>
</div>"""


def ch1():
    t = [chap("Welcome to SAILAB", "c1")]
    t.append("""<p class="lead">SAILAB answers four questions about floods in one place: where will the water go, what will it cost, which protection pays back, and will a warning arrive in time. You draw an area on a map of Pakistan, choose a storm, and SAILAB does the rest.</p>""")
    t.append(sec("What SAILAB does", "s11"))
    t.append("""<ul>
<li><b>Simulates floods.</b> A GPU flood model moves water over the real terrain of your area, second by second, and records how deep and how fast it flows.</li>
<li><b>Counts the damage.</b> Every building is given a damage class and an estimated loss in Pakistani rupees. Roads, schools, hospitals and other facilities are checked too.</li>
<li><b>Tests protection.</b> Add an embankment, a drain or a retention basin, or change the land cover, and see how much loss is avoided.</li>
<li><b>Works from real data.</b> Terrain, buildings, roads, land cover, soils and rainfall are already loaded, so you start from a drawn polygon, not from data preparation.</li>
</ul>""")
    t.append(sec("The four modes", "s12"))
    t.append("""<p>SAILAB has four pages, called <b>modes</b>. You switch between them with the buttons at the top of the screen. They share the same map, the same area and the same way of working.</p>""")
    t.append("""<div class="modecard"><h3><span class="pill">Instant</span> &nbsp;A quick look</h3><p>Pick a place, choose moderate or extreme rain, and see in seconds where water is likely to collect. Best for screening many places quickly. It is an estimate from the terrain alone.</p></div>
<div class="modecard" style="border-top-color:#14284b"><h3><span class="pill navy">Physics</span> &nbsp;The full flood model</h3><p>Runs the real flood model for a storm or river flow you choose. Gives depth, speed and arrival over time, damage and loss for every building, mitigation tests and reports. A typical run takes one to five minutes.</p></div>
<div class="modecard" style="border-top-color:#7a4aa0"><h3><span class="pill purple">GLOF</span> &nbsp;Glacial lakes and dams</h3><p>For floods that come from a glacial lake outburst, a dam release or a dam failure. Finds nearby lakes, estimates the peak flow, lists past outbursts, and runs the same flood model.</p></div>
<div class="modecard" style="border-top-color:#1b6fb8"><h3><span class="pill blue">Coastal</span> &nbsp;Tide and storm surge</h3><p>For flooding from the sea along the Sindh and Balochistan coast: a still-water level for a chosen return period, an animated surge preview, and a full surge simulation.</p></div>""")
    t.append(sec("Which mode should I use?", "s13"))
    t.append(fig("chooser", "Choosing a mode. When in doubt, start with Instant to find the places that matter, then run Physics on them.", "92%"))
    t.append(sec("What SAILAB can and cannot tell you", "s14"))
    t.append("""<p>SAILAB is a decision-support tool. It is good at comparing scenarios, ranking places and options, and showing how a flood could develop. Please keep these limits in mind:</p>
<ul>
<li><b>Results are scenarios, not forecasts.</b> A run shows what happens for the storm and settings you chose. It does not predict a specific future event.</li>
<li><b>Terrain is about 29 m resolution.</b> Small features such as walls, ditches and kerbs are not in the terrain. Buildings are added as obstacles.</li>
<li><b>Rain-only runs do not include river water that arrives from upstream</b> unless you add an inflow (Section 5.2.2). In large river floods, this can be most of the water.</li>
<li><b>Loss figures compare options.</b> They use a regional damage table and a current exchange rate, so read them as an indicator and not as a repair bill.</li>
<li><b>Instant is a screening aid.</b> It tends to show more flooded area and deeper water than a full run.</li>
<li><b>SAILAB does not replace official warnings</b> from the national authorities.</li>
</ul>""")
    t.append(sec("Before you start", "s15"))
    t.append("""<ul>
<li>A modern web browser (Chrome, Edge, Firefox or Safari) and an internet connection for the map.</li>
<li>If your organisation protects SAILAB with an access link, open that link once; your browser remembers it.</li>
<li>An area inside <b>Pakistan</b>. SAILAB has data for Pakistan only (Section 3.4).</li>
</ul>""")
    return "\n".join(t)


def ch2():
    t = [chap("A tour of the screen", "c2")]
    t.append("""<p class="lead">Every mode uses the same layout: a map, a toolbar at the top, a panel on the right, and a flood view card at the bottom left. Learn it once and the other modes feel familiar.</p>""")
    t.append(sec("The main screen", "s21"))
    t.append(shot("p_area", "The Physics screen with an area chosen.",
                  ["<b>Search box.</b> Find a place, address, tehsil or coordinates.",
                   "<b>Draw tool.</b> Click to draw your own area on the map.",
                   "<b>Area size</b> of the area you chose, in km².",
                   "<b>Tabs</b> of the right-hand panel. Each mode has its own tabs."]))
    t.append("""<table><tr><th style="width:30%">Part of the screen</th><th>What it does</th></tr>
<tr><td><b>Top toolbar</b></td><td>Search box; <b>Satellite / Streets</b> map switch; buttons for the four modes; <b>Queue</b> indicator showing whether the flood model is free or busy; light/dark theme button.</td></tr>
<tr><td><b>Drawing tools</b> (left edge of the map)</td><td>Draw a polygon, edit it, or delete it.</td></tr>
<tr><td><b>Right panel</b></td><td>Everything you set and read: area, storm, boundaries, soil, results. It is split into tabs. Click the small arrow on its edge to hide or show it.</td></tr>
<tr><td><b>Flood visualization card</b></td><td>The 3D flood view. It appears when you choose an area. Use <b>Expand</b> to fill the screen and <b>Hide</b> to put it away.</td></tr>
</table>""")
    t.append(sec("A map of Pakistan only", "s22"))
    t.append(shot("p_pakistan_only", "Everything outside Pakistan is hidden, and the map cannot be moved away from the country."))
    t.append("""<p>SAILAB has terrain and data for Pakistan only, so the map shows Pakistan only. Other countries are covered by a dark layer, place names outside the border are hidden, and the map cannot be panned away from the country. The thin teal line is the border SAILAB uses.</p>""")
    t.append(sec("Satellite, streets and theme", "s23"))
    t.append("""<p>Use <b>Satellite</b> to see buildings and land cover, and <b>Streets</b> to read road and place names. The <b>sun/moon button</b> switches between a light and a dark look. None of these change your results.</p>""")
    return "\n".join(t)


def ch3():
    t = [chap("Choosing your area", "c3")]
    t.append("""<p class="lead">Every analysis starts with an area. There are four ways to choose one: search, draw, upload, or pick a whole tehsil.</p>""")
    t.append(sec("Search for a place", "s31"))
    t.append("""<p>Type in the search box. Results appear as you type. Places in Pakistan are listed first, and tehsils are labelled <b>Tehsil</b>.</p>
<table><tr><th style="width:36%">You type</th><th>What SAILAB does</th></tr>
<tr><td>A city, neighbourhood or sector, for example <i>Islamabad I-8</i></td><td>Creates a square area around it, from about 1 to 8 km wide depending on the place.</td></tr>
<tr><td>An address, shop or landmark</td><td>Creates a small area of about 600 m around it, good for a single site.</td></tr>
<tr><td>A tehsil name, for example <i>Charsadda</i></td><td>Uses the whole tehsil boundary as your area. This can be large; see Section 3.4.</td></tr>
<tr><td>Coordinates: <code>33.67, 73.07</code> or <code>33.67N 73.07E</code> or <code>33°40'12"N 73°04'12"E</code></td><td>Creates a 6 km area centred on that point. Latitude and longitude can be given in either order if the numbers are clearly in Pakistan.</td></tr>
</table>""")
    t.append(sec("Draw an area", "s32"))
    t.append(steps("Click the <b>polygon tool</b> on the left edge of the map.",
                   "Click once for each corner of your area. Click the first corner again, or double-click, to finish.",
                   "The area is shown in the right panel with its size in km². To change it, drag a corner. To remove it, use the bin tool.",
                   "Draw a new polygon at any time; it replaces the old one."))
    t.append(tip("Draw tightly around what you care about. Smaller areas run faster, and the model only needs the part of the map that water can reach or leave through."))
    t.append(sec("Upload a boundary", "s33"))
    t.append(steps("In the <b>Area</b> section of the Scenario tab, click <b>Choose File</b>.",
                   "Select a zipped shapefile (<code>.zip</code>) or a <code>.geojson</code> file. A zip may keep its shapefile inside a folder.",
                   "Click <b>Upload as area</b>. The boundary appears on the map and the map zooms to it."))
    t.append("""<p>If a file contains several shapes, SAILAB uses the first polygon. The boundary must lie inside Pakistan.</p>""")
    t.append(sec("Area limits", "s34"))
    t.append("""<p>Two limits keep SAILAB reliable for everyone.</p>""")
    t.append("""<h3>Pakistan only</h3><p>Your area must lie completely inside Pakistan. If it is outside, or crosses the border, SAILAB refuses it, removes the drawing and tells you why.</p>""")
    t.append(shot("p_outside", "An area drawn outside Pakistan is refused.", ["The message under the area section explains that SAILAB covers Pakistan only."]))
    t.append("""<h3>Size of a flood run</h3><p>A flood run has to finish in a reasonable time on the shared GPU. Before a run starts, SAILAB estimates how long it would take from the size of your area and the length of your storm. If the estimate is over <b>10 minutes</b>, the run is <b>not started</b> and you are told to reduce the area or to contact the team for a larger-area request. Longer storms need smaller areas.</p>""")
    t.append(fig("size_limit", "The largest area that can be run, by storm length. A 2-hour storm allows up to about 5,100 km²; a 24-hour design storm about 3,000 km²; a two-day storm about 1,600 km².", "88%"))
    t.append(shot("p_toolarge", "A very large area can be shown but not run. The message says what to do.",
                  ["The note explains the size problem and the limit.", "The size of the area you drew."]))
    t.append(warn("If you need a bigger area, contact the SAILAB team at AI Geo Navigators and describe the area and the storm. Larger areas can be run on request."))
    t.append(sec("Your own elevation model and saved runs", "s35"))
    t.append("""<p>Under <b>Own elevation model or saved run</b> in the Area section:</p>
<ul>
<li><b>Your elevation model (GeoTIFF).</b> If you have better terrain than the built-in data (for example a drone survey), upload it. It must carry a coordinate system, have a cell size between 0.5 and 500 m, and fully cover your area. SAILAB uses it instead of the built-in terrain for runs. <b>Remove my DEM</b> goes back to the built-in terrain.</li>
<li><b>Saved run file.</b> Every finished run can be saved as a small file (Section 9). Load it here to restore the area, storm and settings so you can run it again or change one value.</li>
</ul>""")
    return "\n".join(t)


def ch4():
    t = [chap("SAILAB Instant", "c4")]
    t.append("""<p class="lead">Instant shows, within seconds, where water is likely to collect in your area for a moderate or extreme rainstorm. Use it to compare places before spending time on a full run.</p>""")
    t.append(sec("Step by step", "s41"))
    t.append(steps("Open <b>Instant</b> from the top buttons (or the address <code>/instant</code>).",
                   "In the welcome card, type a place or tehsil, or click <b>Draw area</b> in the top bar and outline your own.",
                   "Choose <b>Moderate rain</b> or <b>Extreme rain</b>. These come from the rainfall recorded in that part of Pakistan.",
                   "Click <b>Get flood information</b>. After a few seconds the flooded land is coloured blue on the map."))
    t.append(shot("i_welcome", "The Instant welcome card and top bar.",
                  ["Type a place, tehsil or coordinates.", "Choose a rain scenario.", "Start.", "The same search is always in the top bar.", "Draw your own area instead."]))
    t.append(sec("Reading the result", "s42"))
    t.append(shot("i_result", "An Instant result for Islamabad I-8.",
                  ["Search another place.", "Draw a new area.", "Tilt the map into a 3D terrain view.", "Rain scenario buttons: Moderate, Extreme, or Custom once you move the slider.",
                   "Rain intensity, in mm per hour.", "How long the storm lasts.", "Area flooded, in km².", "Deepest water, in metres.", "Depth legend: darker blue means deeper water."]))
    t.append("""<table><tr><th style="width:26%">Control</th><th>What it does</th></tr>
<tr><td><b>Rain scenario</b></td><td>Moderate uses the 3-hour rain intensity of the area, extreme uses the 1-hour intensity. Moving the slider switches to Custom.</td></tr>
<tr><td><b>Rain intensity</b></td><td>1 to 100 mm per hour. Heavier rain floods more land and more deeply.</td></tr>
<tr><td><b>Storm length</b></td><td>The longer the storm, the more water falls in total.</td></tr>
<tr><td><b>Flooded area / Deepest water</b></td><td>Totals for your area. Only water deeper than 5 cm is counted.</td></tr>
<tr><td><b>Depth guide</b></td><td>A figure of a person next to the deepest water, with plain advice: ankle, knee, waist, chest or head height.</td></tr>
<tr><td><b>Legend</b></td><td>The same seven colours are always used: 0.05, 0.1, 0.25, 0.5, 1, 2 and 5 m, so a colour always means the same depth.</td></tr>
</table>""")
    t.append(sec("3D view", "s43"))
    t.append(shot("i_3d", "The 3D view tilts the map so you can see how water sits in the valleys and low ground.", None))
    t.append("""<p>Click <b>3D</b> to tilt the map over real terrain; click it again to go back. Drag with the right mouse button (or hold <span class="kbd">Ctrl</span> and drag) to rotate and tilt.</p>""")
    t.append(sec("How far to trust it", "s44"))
    t.append("""<p>Instant estimates the flood from the shape of the land only: it follows drainage lines and fills low spots. It does not move water over time, and it ignores buildings, soil, roughness and river inflow from upstream. On flat land and small areas it tends to flag too much area, and it overstates depth. Across 315 test areas it found most of the land that a full run flooded, but flagged about 1.6 times too much.</p>""")
    t.append(warn("Use Instant to compare and to choose where to look. For depth you will rely on, for loss, or for any decision, run SAILAB Physics."))
    return "\n".join(t)
