import re, sys
p = '/home/aliyan/flood/fis_app.py'
s = open(p, encoding='utf-8').read()

def once(old, new):
    global s
    if s.count(old) != 1:
        sys.exit('FAILED (%d hits): %s' % (s.count(old), old[:100]))
    s = s.replace(old, new, 1)

# ---- header: brand + search + map style + queue + theme only ---------------------------------------
m = re.search(r'      <button id="damsToggleBtn"[^\n]*\n', s); s = s.replace(m.group(0), '      <button id="damsToggleBtn" class="phys-off" onclick="toggleDamsLayer()">Dams</button>\n', 1)
once("      <button onclick=\"exportMapPng(map, 'map')\">Export PNG</button>\n", '')
once('    <div class="hdr-nav"><a href="/" title="Home">Home</a></div>\n', '')
once('<div class="note" id="demSource"></div>', '<div class="note phys-off" id="demSource"></div>')
b = re.search(r'<div class="brand"[^>]*>.*?</div>|<h1[^>]*>.*?</h1>', s[s.index('<header>'):s.index('</header>')], re.S)
print('brand markup:', b.group(0)[:160] if b else None)

# ---- area panel -------------------------------------------------------------------------------------
once('<h3>Draw or upload an area</h3>', '<h3>Area</h3>')
once('<p class="note">Use the polygon tool on the map (top-left), or upload a shapefile (.zip) / GeoJSON below. No size limit — larger areas simply take longer to solve.</p>',
     '<p class="note">Draw a polygon on the map, or upload a shapefile (.zip) or GeoJSON.</p>')
once('<label style="display:flex;align-items:center;gap:6px;margin-top:8px;font-weight:normal;">\n          <input type="checkbox" id="useGlobalDem"',
     '<label class="phys-off" style="display:flex;align-items:center;gap:6px;margin-top:8px;font-weight:normal;">\n          <input type="checkbox" id="useGlobalDem"')
once('        <div style="margin-top:10px;">\n          <label style="font-weight:600;">Use my own elevation model (optional)</label>',
     '        <details style="margin:8px 0;"><summary style="cursor:pointer;font-size:12.5px;color:var(--ink-2);">Own elevation model or saved run</summary>\n        <div style="margin-top:10px;">\n          <label style="font-weight:600;">Your elevation model (GeoTIFF)</label>')
once('        <div id="globalDemPrompt" style="display:none;">', '        </details>\n        <div id="globalDemPrompt" style="display:none;">')
once('<label style="font-weight:600;">Run file</label>', '<label style="font-weight:600;">Saved run file</label>')

# ---- strip the long explanatory paragraphs from the visible physics panels ----------------------------------
start = s.index('<div class="right-col" id="rightCol"')
end = s.index('id="welcomeOverlay"', start)
seg = s[start:end]
removed = 0
def drop(m):
    global removed
    tag, txt = m.group(1), re.sub(r'<[^>]+>', '', m.group(2))
    if ' id=' in tag or len(txt.strip()) <= 75:
        return m.group(0)
    removed += 1
    return ''
seg = re.sub(r'[ \t]*(<p class="note"[^>]*>)(.*?)</p>[ \t]*\n?', drop, seg, flags=re.S)
s = s[:start] + seg + s[end:]
print('long notes removed:', removed)

# ---- shorter titles -------------------------------------------------------------------------------------------
ren = [
 ('Auto-fill from a real return-period design storm (CMIP6, local data)', 'Design storm by return period'),
 ('Or use a real recorded IDF curve instead (ERA5-Land/IMERG, local data)', 'Recorded rainfall curve (IDF)'),
 ('Or upload real rainfall data instead (CSV, e.g. a rain-gauge network export)', 'Upload rain-gauge data (CSV)'),
 ('Advanced solver parameters (optional -- sensible defaults used if left as-is)', 'Advanced settings'),
 ('<h3>Batch return-period runs</h3>', '<h3>Return-period runs</h3>'),
 ('<h3>Average Annual Loss (AAL)</h3>', '<h3>Average annual loss</h3>'),
 ('<h3>Flood model status</h3>', '<h3>Run status</h3>'),
 ('<h3>Buildings &amp; infrastructure risk</h3>', '<h3>Buildings &amp; infrastructure</h3>'),
 ('<h3>Damage &amp; hotspots</h3>', '<h3>Damage</h3>'),
 ('<h3>Sediment transport risk</h3>', '<h3>Sediment</h3>'),
 ('<h3>Nature-based solution scenario</h3>', '<h3>Nature-based solutions</h3>'),
 ('<h3>Protection scenario (grey infrastructure)</h3>', '<h3>Protection structures</h3>'),
 ('Run flood model &rarr;', 'Run flood model'),
]
for a, b2 in ren:
    if s.count(a) < 1: sys.exit('RENAME MISSING: ' + a)
    s = s.replace(a, b2)

# ---- css: no overlaps ------------------------------------------------------------------------------------------
css = '''
/* ---- no overlaps: map controls clear of the toolbar, flood card clear of the map logo ---- */
.mapboxgl-ctrl-top-right {{ top:68px !important; }}
.view3d-wrap {{ bottom:34px !important; }}
body.view-main .view3d-wrap {{ bottom:34px !important; }}
#viewShowBtn {{ bottom:34px !important; }}
header .brand {{ text-decoration:none; color:inherit; }}
'''
once('.tier-tabs {{ padding:12px 12px 0; gap:6px; }}', css + '.tier-tabs {{ padding:12px 12px 0; gap:6px; }}')
open(p, 'w', encoding='utf-8').write(s)
print('ok')
