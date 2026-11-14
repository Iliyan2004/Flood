import re, sys
p = '/home/aliyan/flood/fis_app.py'
s = open(p, encoding='utf-8').read()
log = []
def rep(old, new, count=1):
    global s
    n = s.count(old)
    if n < 1 or (count and n != count):
        sys.exit('REPLACE FAILED (%d hits, wanted %s): %s' % (n, count, old[:110]))
    s = s.replace(old, new)
def drop_line(pattern, expect=1):
    """delete whole lines (a complete concatenation element ending with '+') matching pattern"""
    global s
    rx = re.compile(r'^[ \t]*' + pattern + r'[ \t]*\+[ \t]*\n', re.M)
    s, n = rx.subn('', s)
    if n != expect: sys.exit('DROP FAILED (%d, wanted %d): %s' % (n, expect, pattern[:90]))

# ---------------- GLOF labels ----------------
rep('Shishper Glacier Lake (Hassanabad, Hunza) -- real 2022 event</option>', 'Shishper Glacier Lake, Hunza (2022 event)</option>')
rep('Real HydroLAKES lake near this AOI (any Pakistan lake, auto-detected)', 'HydroLAKES lake near the area')
rep('PMD 2024 glacial-lake inventory lake near this AOI (dam type + nearest village)', 'PMD glacial lake near the area')
rep("Custom: I know the lake's real volume (m&sup3;)", 'Known lake volume')
rep("Custom: I only know the lake's real area (km&sup2;, less reliable)", 'Known lake area')
rep('<label>Trigger / breach mechanism</label>', '<label>Trigger</label>')
rep('Gradual moraine failure (Popov 1991)', 'Moraine failure')
rep('Landslide / rock-ice avalanche impact (Froehlich 1995 breach discharge)', 'Landslide or ice avalanche')
rep('<label>Real breach / dam height (m)</label>', '<label>Breach height (m)</label>')
rep('Find nearby real lakes &rarr;', 'Find nearby lakes')
rep('Find nearby inventory lakes &rarr;', 'Find nearby lakes')
rep('Apply GLOF discharge &rarr;', 'Apply discharge')
rep('<label>Documented outbursts near this area</label>\n', '')
rep('Show documented events &rarr;', 'Past outbursts nearby')
rep('Get real glacier inventory (GLIMS) &rarr;', 'Glacier outlines')
rep('Mark a known real GLOF lake on the result (optional)', 'Mark a lake (optional)')
rep('Real confidence threshold (lower = more coverage, more noise)', 'Confidence threshold')
rep('Run SAR displacement analysis (Jan-Aug 2026, real, ~2-12 min) &rarr;', 'Run glacier movement analysis')
rep('Check recent glacier speed (ITS_LIVE) &rarr;', 'Check recent glacier speed')
rep('Check real geomorphology &amp; susceptibility &rarr;', 'Check landslide susceptibility')
rep('Include real buildings (affects flow)', 'Include buildings')
rep('<p class="note">Draw/upload an area and run the model above first.</p>', '<p class="note">No run yet.</p>')

# ---------------- Coastal labels ----------------
rep('<h3>Coastal / tidal flood (static inundation)</h3>', '<h3>Static flood level</h3>')
rep('Karachi tide gauge (Khan et al. 2020) &mdash; 50/100/1000 yr', 'Karachi tide gauge (50 to 1000 yr)')
rep('GTSM-ERA5-E, nearest of 42 coastal stations &mdash; 2 to 100 yr', 'GTSM model, nearest station (2 to 100 yr)')
rep('<label>Or use a documented historical event instead</label>', '<label>Or a past event</label>')
rep('(use return period above)', '(none)')
rep('Cyclone Biparjoy 2023 (observed, ~2.0 m)', 'Cyclone Biparjoy 2023 (2.0 m)')
rep('PMD "high surge risk" threshold (~3.25 m)', 'PMD high-surge threshold (3.25 m)')
rep('Cyclone 2A 1999 (modeled, unvalidated, ~4.0 m)', 'Cyclone 2A 1999 (4.0 m)')
rep('Project to future year (adds real Karachi SLR trend, 3.6mm/yr -- optional)', 'Future year (optional)')
rep('Add sea-level rise (m) &mdash; scenario value, not a forecast', 'Sea-level rise (m)')
rep('None (present day)', 'None')
rep('Add real wave run-up (Open-Meteo Marine API + Stockdon et al. 2006)', 'Include wave run-up', count=0)
rep('Run coastal flood analysis &rarr;', 'Run coastal flood')
rep('<h3>Water-level explorer (instant)</h3>', '<h3>Water level explorer</h3>')
rep('<p class="note">Set an area on the coast to load this.</p>', '<p class="note">Choose a coastal area.</p>')
rep('Show this level on map &rarr;', 'Show on map')
rep('<h3>Coastal susceptibility map</h3>', '<h3>Susceptibility map</h3>')
rep('Compute susceptibility &rarr;', 'Compute')
rep('<h3>Fast animated preview (approximate)</h3>', '<h3>Animated preview</h3>')
rep('Anchor magnitude to', 'Water level based on', count=0)
rep("Static method's total water level (above)", 'Static water level', count=0)
rep("Real GTSR station's own recorded historical peak surge", 'Recorded peak surge', count=0)
rep('Astronomical tide (Moon and Sun)', 'Tide', count=0)
rep('Spring high water at the surge peak (worst case)', 'Spring high tide at the peak', count=0)
rep('Ordinary high water at the surge peak', 'Ordinary high tide at the peak', count=0)
rep('Low water at the surge peak (best case)', 'Low tide at the peak', count=0)
rep('Preview animated coastal flood &rarr;', 'Preview')
rep('<h3>Real dynamic surge (GTSR, experimental)</h3>', '<h3>Full surge simulation</h3>')
rep('Add real mean tidal range (Karachi Port, 2.3 m, Khan et al. 2020) on top of the surge peak', 'Add mean tidal range')
rep('Real GTSR episode window (longer = more of the real recede tail, but real hours slower)', 'Surge window')
rep('2 days (fastest, rise+peak only)', '2 days (fastest)')
rep('3 days (rise/peak/partial recede)', '3 days')
rep('5 days (fullest real recede tail, slowest)', '5 days (slowest)')
rep('Add real rainfall at the same time (compound flooding / ocean backwater)', 'Add rainfall (compound flood)')
rep('Rainfall during this event (mm/hr)', 'Rain (mm/hr)')
rep('Run dynamic surge (real physics) &rarr;', 'Run surge simulation')

# ---------------- result boxes: drop explanation lines ----------------
drop_line(r"'<p class=\"note\">' \+ result\.anchor_basis \+ '</p>'")
drop_line(r"\(result\.astronomical_tide \? '<p class=\"note\">Astronomical tide:.*?\) ")
drop_line(r"\(result\.tide_combination_note \? '<p class=\"note\">Tidal range:.*?\) ")
drop_line(r"\(result\.wave_runup \? '<p class=\"note\">Wave run-up:.*?\) ")
drop_line(r"'<p class=\"note\">' \+ result\.method \+ '</p>'", expect=2)
rep("""      '<p class="note">' + d.anchor_basis + '</p>' +\n      '<p class="note">' + d.method + '</p>' +\n""", "")
drop_line(r"'<p class=\"note\">' \+ d\.water_level\.basis.*?</p>'")
drop_line(r"\(d\.wave_runup \? '<p class=\"note\">' \+ d\.wave_runup\.wave_basis.*?\) ")
drop_line(r"'<p class=\"note\">Factors: '.*?</p>'")
rep("""document.getElementById('coastalSourceNote').textContent = src === 'khan'""", """document.getElementById('coastalSourceNote').textContent = '';
  const _unusedSourceNote = src === 'khan'""")
rep("'<p class=\"note\">' + refs + '</p>';", "'';")

# ---------------- GLOF result boxes ----------------
drop_line(r"'<p class=\"note\">' \+ d\.method \+ \(d\.note \? ' ' \+ d\.note : ''\) \+ '</p>'")
s, n = re.subn(r"siteNote = '<p class=\"note\">Draw your AOI around <b>' \+ d\.downstream_lat\.toFixed\(3\) \+ '&deg;N, ' \+\n\s*d\.downstream_lon\.toFixed\(3\) \+ '&deg;E</b>.*?</p>';",
    "siteNote = '<p class=\"note\">Draw your area around <b>' + d.downstream_lat.toFixed(3) + '&deg;N, ' + d.downstream_lon.toFixed(3) + '&deg;E</b>, then detect rivers.</p>';", s, count=1, flags=re.S)
if n != 1: sys.exit('siteNote A')
s, n = re.subn(r"siteNote = '<p class=\"note\">The real lake itself is at <b>' \+ d\.lake_lat\.toFixed\(3\) \+ '&deg;N, ' \+\n\s*d\.lake_lon\.toFixed\(3\) \+ '&deg;E</b>.*?</p>';",
    "siteNote = '<p class=\"note\">The lake is at <b>' + d.lake_lat.toFixed(3) + '&deg;N, ' + d.lake_lon.toFixed(3) + '&deg;E</b>. Draw your area downstream of it.</p>';", s, count=1, flags=re.S)
if n != 1: sys.exit('siteNote B')
s, n = re.subn(r"'<p class=\"note\">Added as a new inflow \(Peak=' \+ d\.peak_discharge_m3s \+ ' m&sup3;/s, Duration=' \+ d\.suggested_duration_hr \+\n\s*'h\) with a real GLOF-shaped hydrograph.*?edit its site/edge there\.</p>' \+",
    "'<p class=\"note\">Added to Boundary conditions.</p>' +", s, count=1, flags=re.S)
if n != 1: sys.exit('added note')
rep('Preview instant flood extent (Tier 2, no siting needed) &rarr;', 'Preview instant extent')
drop_line(r"'<p class=\"note\" style=\"margin-top:4px;\">Unlike the Tier 3 run above.*?</p>'")
# past events: one count line, no catalogue sentence, no formula-quality paragraph
rep("""    box.innerHTML = '<p class="note">' + d.events.length + ' event(s) within ' + d.radius_km + ' km. Catalogue: ' + c.pakistan_events + ' Pakistan events, only ' + c.pakistan_with_volume + ' with a volume and ' + c.pakistan_with_peak_discharge + ' with a peak discharge. ' + d.source + '</p>' + rows + val;""",
    """    box.innerHTML = '<p class="note">' + d.events.length + ' event(s) within ' + d.radius_km + ' km</p>' + rows;""")
rep("""No documented event within ' + d.radius_km + ' km. (Absence of a record is not absence of an outburst.)</p>'""", """No documented event within ' + d.radius_km + ' km.</p>'""")
# glacier
rep("""'<p class="note">' + d.method + ' (reference date ' + d.reference_date + ')</p>';""", "'';")
rep("""'<p class="note">' + r.method + ' ' + r.source + '.</p>' + (r.errors""", """(r.errors""")
drop_line(r"\(bc \? '<p class=\"note\">' \+ bc\.method \+ '</p>' : ''\)")
s, n = re.subn(r" \+\n\s*'<p class=\"note\" style=\"margin-top:4px;\">The real displacement/velocity numbers above are context.*?</p>' : ''\);", " : '');", s, count=1, flags=re.S)
if n != 1: sys.exit('sar note')

# ---------------- status words ----------------
for a, b in [
 ("Computing static coastal inundation...", "Computing..."),
 ("Building fast animated preview (real GTSR series + static bathtub per frame, instant)...", "Building the preview..."),
 ("Computing the level library for this area...", "Loading..."),
 ("Running real dynamic GTSR-driven physics (CPU) -- this can take a few real minutes...", "Running the surge simulation (a few minutes)..."),
 ("Computing real GLOF breach discharge...", "Computing..."),
 ("Looking up documented events...", "Searching..."),
 ("Searching real HydroLAKES data near this AOI...", "Searching..."),
 ("Querying real GLIMS glacier inventory (Earth Engine)...", "Loading glacier outlines..."),
 ("Fetching ITS_LIVE pairs and measuring glacier speed (first run downloads a few granules)...", "Measuring glacier speed..."),
 ("Running real ascending+descending Sentinel-1 SAR offset-tracking (Earth Engine) -- this genuinely takes several real minutes...", "Running the analysis (a few minutes)..."),
 ("Enter a real lake volume first.", "Enter the lake volume."),
 ("Enter a real lake area first.", "Enter the lake area."),
 ('Click "Find nearby real lakes" and pick one first.', 'Find nearby lakes and pick one.'),
 ('Click "Find nearby inventory lakes" and pick one first.', 'Find nearby lakes and pick one.'),
]:
    rep(a, b, count=0)

# ---------------- storm centering stays out of the GLOF page ---------------------------------------------------------
rep('<div class="panel-section" style="border:1px solid var(--line);border-radius:8px;padding:10px;margin:4px 0;">\n                <h3 style="margin-top:0;">Storm centering &amp; Areal Reduction Factor</h3>',
    '<div class="panel-section phys-only" style="border:1px solid var(--line);border-radius:8px;padding:10px;margin:4px 0;">\n                <h3 style="margin-top:0;">Storm centering &amp; Areal Reduction Factor</h3>')
rep('#rightCol select {{ max-width:100%; text-overflow:ellipsis; }}', '#rightCol select {{ max-width:100%; text-overflow:ellipsis; }}\nbody:not([data-mode="physics"]) .phys-only {{ display:none !important; }}')
# button wording per mode
rep('''    html = html.replace("<!--MODENAV-->", nav, 1)''', '''    html = html.replace("<!--MODENAV-->", nav, 1)
    if mode == "glof":
        html = html.replace(">Run flood model</button>", ">Run GLOF flood</button>", 1)''')
open(p, 'w', encoding='utf-8').write(s)
print('done')
