"""Adapt the SAILAB_UI_v2 landing page to the real product: two modes (Instant, Physics),
real data sources, no invented example figures. Fails loudly if a replacement target is missing."""
import re, sys
SRC = '/home/aliyan/flood/ui_ref/src/landing.html'
DST = '/home/aliyan/flood/landing.html'
h = open(SRC, encoding='utf-8').read()

def sub(old, new, count=1):
    global h
    n = h.count(old)
    if n < 1 or (count and n != count):
        sys.exit('REPLACE FAILED (%d hits, wanted %s): %s' % (n, count, old[:80]))
    h = h.replace(old, new)

def sub_re(pat, new, flags=re.S):
    global h
    h, n = re.subn(pat, lambda m: new, h, count=1, flags=flags)
    if n != 1:
        sys.exit('REGEX FAILED: ' + pat[:80])

FULL = 'Simulation &amp; Analytics for Inundation, Loss, Assets &amp; Built Environment'
ARROW = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8h10M9 4l4 4-4 4"/></svg>'

# --- head ---------------------------------------------------------------------------------------
sub('<title>SAILAB — Flood intelligence</title>', '<title>SAILAB: Flood intelligence for Pakistan</title>')
sub('for six flood hazards across Pakistan.', 'with a fast estimate in seconds and a full 2D physics run when it matters.')
sub('SAILAB v2.2 · interface study', 'SAILAB v__APP_VERSION__')

# --- nav ----------------------------------------------------------------------------------------
sub('<a class="btn p" href="modes.html">Open the console</a>\n  </nav>', '<a class="btn p" href="/app">SAILAB Physics</a>\n  </nav>')
sub('<a href="#modes" data-go>Modes</a>', '<a href="#modes" data-go>Two ways in</a>')

# --- hero ---------------------------------------------------------------------------------------
sub('<p class="acro-full">[SAILAB full form goes here]</p>', '<p class="acro-full">' + FULL + '</p>')
sub('<span>[SAILAB full form goes here]</span>', '<span>' + FULL + '</span>')
sub_re(r'<p class="lede" id="heroLede">.*?</p>',
       '<p class="lede" id="heroLede">Draw a district. Pick a storm. SAILAB returns the flooded area and depth, the buildings and roads in the water, '
       'the facilities at risk and an estimated loss in rupees, with the source behind every figure.</p>')
sub_re(r'<div class="cta" id="heroCta">.*?</div>\n    </div>\n    <div class="ticker"',
       '<div class="cta" id="heroCta">\n        <a class="btn p" id="open" href="/app">SAILAB Physics ' + ARROW + '</a>\n'
       '        <a class="btn" id="openInstant" href="/instant">SAILAB Instant</a>\n        <a class="btn" href="#y2022" data-go>Why it exists</a>\n      </div>\n    </div>\n    <div class="ticker"')
ticker_items = ['LISFLOOD-FP 2D shallow-water', 'GPU solver', '30 m SRTM terrain', 'Google Open Buildings', 'ESA WorldCover roughness',
                'ERA5-Land and IMERG rainfall curves', 'Huizinga et al. depth-damage', 'Loss in PKR']
t = ''.join('<span>%s</span>' % i for i in ticker_items)
sub_re(r'<div class="ticker" aria-hidden="true"><div class="tr">.*?</div></div>',
       '<div class="ticker" aria-hidden="true"><div class="tr">' + t + t + '</div></div>')

# --- capabilities: only what the product really does -----------------------------------------------
cards = '''<div class="cards" id="cards">
      <article class="card"><span class="ix">01</span>
        <div class="cv"><div class="cv-type"><span>Draw it, upload it,</span><br><span>or search a tehsil.</span></div></div>
        <h3>Any area in Pakistan</h3><p>Draw a polygon, upload a shapefile or GeoJSON, or pick one of 578 tehsil boundaries. There is no size cap; bigger areas just take longer.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">02</span>
        <div class="cv"><div class="cv-bars"><div>INSTANT · HAND<s></s></div><div>2D PHYSICS<s></s></div></div></div>
        <h3>Seconds first, physics next</h3><p>Instant gives a terrain-based estimate at once. Physics runs the full LISFLOOD-FP shallow-water solver as a background job with live progress.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">03</span>
        <div class="cv"><div class="cv-impact"><div><small>Buildings</small>by damage</div><div><small>Roads</small>by hazard</div><div><small>Facilities</small>at risk</div><div><small>Loss</small>in PKR</div></div></div>
        <h3>Impact, not only water</h3><p>Buildings coloured by flood damage, roads and critical facilities by hazard, and an estimated loss in rupees from published depth-damage curves.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">04</span>
        <div class="cv"><svg viewBox="0 0 220 96" aria-hidden="true">
          <path d="M0 80C40 78 60 70 80 52S120 14 150 18 200 50 220 62V96H0Z" fill="#6FBCE0" opacity=".35"/>
          <path d="M0 80C40 78 60 70 80 52S120 14 150 18 200 50 220 62" fill="none" stroke="#1E6E9C" stroke-width="2"/>
          <circle cx="150" cy="18" r="5" fill="#0F6E73"/><circle cx="150" cy="18" r="11" fill="none" stroke="#0F6E73" opacity=".5"><animate attributeName="r" values="5;16;5" dur="2.4s" repeatCount="indefinite"/></circle>
          <text x="160" y="14" style="font-family:var(--mono);font-size:12px;fill:#4E5551">depth</text></svg></div>
        <h3>Depth at any point</h3><p>Click the 3D map for the water depth, speed and peak at that spot, and how they change through the storm.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">05</span>
        <div class="cv"><div class="cv-src"><span>SRTM terrain 30 m</span><span>ERA5-Land / IMERG IDF</span><span>ESA WorldCover</span></div></div>
        <h3>Every figure has a source</h3><p>Terrain, rainfall, roughness, soil, buildings and facilities come from named datasets, listed in the technical reference.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">06</span>
        <div class="cv"><div class="cv-run">run file<span class="v"><i>.sailab.json</i></span></div></div>
        <h3>A run you can repeat</h3><p>Save the run file and load it later with the same area, storm and settings. Results are cached by their inputs, so a repeat is instant.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">07</span>
        <div class="cv"><div class="cv-bars"><div>RUN A<s></s></div><div>RUN B<s></s></div></div></div>
        <h3>Compare and report</h3><p>Compare two runs cell by cell, sweep return periods, estimate average annual loss, and export a PDF.</p><i class="waterline"></i></article>
      <article class="card"><span class="ix">08</span>
        <div class="cv"><div class="cv-words"><span>Moderate</span><span>Extreme</span><span>1-in-25</span><span>1-in-100</span></div></div>
        <h3>Storms in plain words</h3><p>Instant offers moderate and extreme rain. Physics takes a design storm by return period, a recorded rainfall curve or your own rain-gauge file.</p><i class="waterline"></i></article>
    </div>'''
sub_re(r'<div class="cards" id="cards">.*?</article>\n    </div>', cards)

# --- modes: two real ones -------------------------------------------------------------------------
m = re.search(r'<section id="modes">.*?</section>', h, re.S).group(0)
panels = re.findall(r'    <div class="panel">.*?\n    </div>\n(?=\n    <div class="panel">|  </div>\n  <div class="m-dots")', m, re.S)
assert len(panels) == 6, len(panels)
p2d, pins = panels[0], panels[1]
p2d = p2d.replace('<b>01</b> / 06', '<b>01</b> / 02').replace('<h3>2D Flood <em>modelling</em></h3>', '<h3>SAILAB <em>Physics</em></h3>')
p2d = re.sub(r'<span class="eng">.*?</span>', '<span class="eng">lisflood-fp</span>', p2d, 1)
p2d = re.sub(r'<p>Full shallow-water.*?</p>', '<p>The full 2D shallow-water run on the GPU, with real buildings, roads and facilities, loss in rupees and protection scenarios. This is the reference result.</p>', p2d, 1, re.S)
p2d = re.sub(r'<div class="chips">.*?</div>', '<div class="chips"><span>Design storm</span><span>Rain-gauge file</span><span>River inflow</span><span>Protection</span></div>', p2d, 1, re.S)
p2d = re.sub(r'<div class="m-meta">.*?</div></div>', '<div class="m-meta"><div><span class="micro">Solver</span><b>LISFLOOD-FP</b></div><div><span class="micro">Grid</span><b>~30 m</b></div></div>', p2d, 1, re.S)
p2d = p2d.replace('href="modes.html#2d">Open 2D Flood', 'href="/app">Open SAILAB Physics').replace('swe2d · Charsadda', 'lisflood-fp · example grid')
pins = pins.replace('<b>02</b> / 06', '<b>02</b> / 02').replace('<h3>Instant</h3>', '<h3>SAILAB <em>Instant</em></h3>')
pins = re.sub(r'<span class="eng">.*?</span>', '<span class="eng">hand</span>', pins, 1)
pins = re.sub(r'<p>A height-above-drainage.*?</p>', '<p>A terrain-based estimate (height above nearest drainage) that answers in seconds, for when the call is happening now and a full run would arrive too late. It is an upper estimate, not a physics result.</p>', pins, 1, re.S)
pins = re.sub(r'<div class="chips">.*?</div>', '<div class="chips"><span>Moderate rain</span><span>Extreme rain</span><span>Your own rain</span><span>3D view</span></div>', pins, 1, re.S)
pins = pins.replace('href="modes.html#instant">Open Instant', 'href="/instant">Open SAILAB Instant')
intro = re.search(r'    <div class="panel intro-p">.*?\n    </div>\n', m, re.S).group(0)
intro = intro.replace('<b>04</b> Modes', '<b>04</b> Two ways in').replace('Six hazards.<br><em>One console.</em>', 'Two ways in.<br><em>One map.</em>')
intro = re.sub(r'<p>Each mode has.*?</p>', '<p>Start with Instant when you need an answer now. Switch to Physics when the decision needs a modelled flood, the buildings and roads in it, and a loss figure.</p>', intro, 1, re.S)
new_modes = ('<section id="modes">\n  <div class="m-track" id="mTrack">\n' + intro + '\n' + p2d + '\n' + pins + '  </div>\n'
             '  <div class="m-dots" id="mDots" aria-label="Modes"></div>\n  <div class="m-count micro" id="mCount">Modes</div>\n</section>')
h = h.replace(m, new_modes)

# --- any leftover console links ---------------------------------------------------------------------
left = re.findall(r'modes\.html', h)
if left:
    sys.exit('leftover modes.html links: %d' % len(left))

# --- serve assets from the app ----------------------------------------------------------------------
h = h.replace('assets/', '/landing-assets/')
open(DST, 'w', encoding='utf-8').write(h)
print('wrote', DST, len(h), 'bytes')
