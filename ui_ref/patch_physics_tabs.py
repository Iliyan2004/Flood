import re, sys
p = '/home/aliyan/flood/fis_app.py'
s = open(p, encoding='utf-8').read()

def once(old, new):
    global s
    n = s.count(old)
    if n != 1:
        sys.exit('FAILED (%d hits): %s' % (n, old[:90]))
    s = s.replace(old, new, 1)

def tag_section(h3, pane=None, off=False):
    """add data-pane (or phys-off) to the panel-section whose first heading is h3"""
    global s
    pat = re.compile(r'<div class="panel-section"( style="[^"]*")?>(\s*)<h3[^>]*>' + re.escape(h3) + r'</h3>')
    m = list(pat.finditer(s))
    if len(m) != 1:
        sys.exit('SECTION %r: %d hits' % (h3, len(m)))
    mm = m[0]
    attr = ' class="panel-section phys-off"' if off else ' class="panel-section" data-pane="%s"' % pane
    rep = '<div' + attr + (mm.group(1) or '') + '>' + mm.group(2) + mm.group(0).split('>', 1)[1].lstrip().split('\n', 1)[-1] if False else None
    # simple rebuild: keep everything after the opening tag
    head_end = mm.group(0).index('>') + 1
    new = '<div' + attr + (mm.group(1) or '') + '>' + mm.group(0)[head_end:]
    s = s[:mm.start()] + new + s[mm.end():]

def off_details(summary_start):
    global s
    pat = re.compile(r'<details style="margin:2px 0;"( id="glofDetails")?>(\s*)<summary[^>]*>' + re.escape(summary_start))
    m = list(pat.finditer(s))
    if len(m) != 1:
        sys.exit('DETAILS %r: %d hits' % (summary_start, len(m)))
    mm = m[0]
    s = s[:mm.start()] + mm.group(0).replace('<details style="margin:2px 0;"', '<details class="phys-off" style="margin:2px 0;"', 1) + s[mm.end():]

# ---- 1. tab bar replaces the tier tabs ---------------------------------------------------------
old_tabs = re.search(r'      <div class="tier-tabs">.*?</div>\n', s, re.S).group(0)
new_tabs = '''      <div class="tier-tabs ptabs" role="tablist" aria-label="Physics workflow">
        <button class="ptab active" data-ptab="scenario" role="tab" onclick="showPane('scenario')">Scenario</button>
        <button class="ptab" data-ptab="runs" role="tab" onclick="showPane('runs')">Runs</button>
        <button class="ptab" data-ptab="impact" role="tab" onclick="showPane('impact')">Impact</button>
        <button class="ptab" data-ptab="mitigate" role="tab" onclick="showPane('mitigate')">Mitigation</button>
      </div>
'''
s = s.replace(old_tabs, new_tabs, 1)

# ---- 2. instant (tier 2) and coastal (tier 4) panels are not shown; physics content always on ----------
once('<div class="tier-content" data-tier="2">', '<div class="tier-content phys-off" data-tier="2">')
once('<div class="tier-content" data-tier="3" style="display:none;">', '<div class="tier-content" data-tier="3">')
once('<div class="tier-content" data-tier="4" style="display:none;">', '<div class="tier-content phys-off" data-tier="4">')

# ---- 3. sections ----------------------------------------------------------------------------------
tag_section('Draw or upload an area', 'scenario')
tag_section('Storm', 'scenario')
for h in ['Flood model status', 'Batch return-period runs', 'Average Annual Loss (AAL)', 'Compare two runs']:
    tag_section(h, 'runs')
for h in ['Buildings &amp; infrastructure risk', 'Damage &amp; hotspots', 'Sediment transport risk']:
    tag_section(h, 'impact')
for h in ['Nature-based solution scenario', 'Protection scenario (grey infrastructure)']:
    tag_section(h, 'mitigate')
for h in ['National rainfall hotspots (discovery)', 'River basins (HydroSHEDS discovery)', 'Rainfall', 'Live Monitoring']:
    tag_section(h, off=True)

# ---- 4. discharge-source / hazard tools inside the advanced box are not shown ---------------------------
for d in ['GLOF scenario', 'Dam spillway release', 'Dam failure scenario', 'Snowmelt contribution',
          'Snow &amp; glacier melt model', 'Geomorphology', 'Glacier monitoring']:
    off_details(d)

# ---- 5. behaviour -------------------------------------------------------------------------------------
once('''function showTier(n) {{
  document.querySelectorAll('.tier-btn').forEach(b => b.classList.toggle('active', parseInt(b.dataset.tier) === n));
  document.querySelectorAll('.tier-content').forEach(c => c.style.display = (parseInt(c.dataset.tier) === n ? '' : 'none'));
  loadTier3D(n);
}}''', '''// SAILAB Physics only: the old tier switcher is gone. showTier() is kept so existing callers still work;
// it always shows the physics view, and showPane() switches the side-panel tabs.
function showTier(n) {{
  loadTier3D(3);
}}
function showPane(name) {{
  const rc = document.getElementById('rightCol');
  if (!rc) return;
  rc.setAttribute('data-pane', name);
  document.querySelectorAll('.ptab').forEach(b => {{
    const on = b.dataset.ptab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  }});
  rc.scrollTop = 0;
  try {{ sessionStorage.setItem('sailabPane', name); }} catch (e) {{}}
}}
document.addEventListener('DOMContentLoaded', () => {{
  let p = 'scenario';
  try {{ p = sessionStorage.getItem('sailabPane') || p; }} catch (e) {{}}
  showPane(['scenario', 'runs', 'impact', 'mitigate'].includes(p) ? p : 'scenario');
}});''')
once("  document.getElementById('statusNote').textContent = 'Submitting...';",
     "  document.getElementById('statusNote').textContent = 'Submitting...';\n  showPane('runs');")

# ---- 6. css -----------------------------------------------------------------------------------------
css = '''
/* ---- SAILAB Physics: tabbed side panel; only the physics workflow is shown ---- */
.phys-off {{ display:none !important; }}
#rightCol .panel-section[data-pane] {{ display:none; }}
#rightCol[data-pane="scenario"] .panel-section[data-pane="scenario"],
#rightCol[data-pane="runs"] .panel-section[data-pane="runs"],
#rightCol[data-pane="impact"] .panel-section[data-pane="impact"],
#rightCol[data-pane="mitigate"] .panel-section[data-pane="mitigate"] {{ display:block; }}
.ptabs {{ padding-bottom:10px; }}
.ptabs .ptab {{ flex:1; }}
'''
anchor = '.tier-tabs {{ padding:12px 12px 0; gap:6px; }}'
once(anchor, css + anchor)
open(p, 'w', encoding='utf-8').write(s)
print('patched')
