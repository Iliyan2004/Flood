import re, sys
p = '/home/aliyan/flood/fis_app.py'
s = open(p, encoding='utf-8').read()

def die(m): sys.exit('FAILED: ' + m)
def once(old, new):
    global s
    if s.count(old) != 1: die('%d hits: %s' % (s.count(old), old[:100]))
    s = s.replace(old, new, 1)

def block(text, start, open_re, close_str):
    """index just after the element that opens at `start` (nesting-aware)"""
    depth, i = 0, start
    pat = re.compile(open_re + '|' + re.escape(close_str))
    while True:
        m = pat.search(text, i)
        if not m: die('unbalanced ' + close_str)
        if m.group(0) == close_str:
            depth -= 1
            if depth == 0: return m.end()
        else:
            depth += 1
        i = m.end()

def cut_details(summary_prefix):
    """remove a nested <details> by its summary text; return (summary_inner, body_html)"""
    global s
    m = re.search(r'[ \t]*<details[^>]*>\s*<summary[^>]*>' + re.escape(summary_prefix), s)
    if not m: die('details ' + summary_prefix)
    end = block(s, m.start() + len(m.group(0)) - len(m.group(0)) + s[m.start():].index('<details'), r'<details\b', '</details>')
    whole = s[m.start():end]
    s = s[:m.start()] + s[end:].lstrip('\n') if False else s[:m.start()] + s[end:]
    inner = re.search(r'<details[^>]*>\s*<summary[^>]*>.*?</summary>(.*)</details>\s*$', whole, re.S).group(1)
    return inner

# ---- 1. pull the pieces out of the Storm section ---------------------------------------------------------
m = re.search(r'\n          <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="includeBuildings"[^\n]*</label>', s)
if not m: die('includeBuildings')
include_buildings = m.group(0).strip('\n'); s = s[:m.start()] + s[m.end():]

m = re.search(r'\n      <div class="bc-block".*?\n      </div>', s, re.S)
if not m: die('bc-block')
bc_inner = re.search(r'<h3[^>]*>Boundary conditions</h3>(.*)\n      </div>$', m.group(0), re.S).group(1)
s = s[:m.start()] + s[m.end():]

glof_inner = cut_details('GLOF scenario')
geo_inner = cut_details('Geomorphology')
glac_inner = cut_details('Glacier monitoring')

m = re.search(r'          <details style="margin:2px 0;">\n            <summary[^>]*>Advanced settings</summary>', s)
if not m: die('advanced')
adv_end = block(s, m.start() + s[m.start():].index('<details'), r'<details\b', '</details>')
advanced = s[m.start():adv_end]
s = s[:m.start()] + s[adv_end:].lstrip('\n')

# ---- 2. new sections after the Storm section ----------------------------------------------------------------
soil_anchor = '        </div>\n\n        <div class="panel-section" data-pane="scenario">\n          <h3>Soil &amp; infiltration</h3>'
if s.count(soil_anchor) != 1: die('soil anchor %d' % s.count(soil_anchor))
new_secs = ('        </div>\n\n'
 '        <div class="panel-section" data-pane="scenario" data-modes="glof">\n          <h3>GLOF scenario</h3>' + glof_inner + '        </div>\n\n'
 '        <div class="panel-section" data-pane="scenario" data-modes="physics glof">\n          <h3>Boundary conditions</h3>' + bc_inner + '\n        </div>\n\n'
 '        <div class="panel-section" data-pane="scenario" data-modes="physics glof">\n' + advanced + '\n        </div>\n\n'
 '        <div class="panel-section" data-pane="glacier" data-modes="glof">\n          <h3>Glacier monitoring</h3>' + glac_inner + '        </div>\n\n'
 '        <div class="panel-section" data-pane="glacier" data-modes="glof">\n          <h3>Geomorphology</h3>' + geo_inner + '        </div>\n\n'
 '        <div class="panel-section" data-pane="scenario" data-modes="physics">\n          <h3>Soil &amp; infiltration</h3>')
s = s.replace(soil_anchor, new_secs, 1)

# ---- 3. Run section carries the buildings checkbox and serves physics + glof ---------------------------------
once('        <div class="panel-section" data-pane="scenario">\n          <button class="action" id="runBtn"',
     '        <div class="panel-section" data-pane="scenario" data-modes="physics glof">\n          ' + include_buildings.strip() + '\n          <button class="action" id="runBtn"')

# ---- 4. modes on the existing physics sections ---------------------------------------------------------------
modes = {'Area': 'physics glof coastal', 'Run status': 'physics glof', 'Return-period runs': 'physics', 'Average annual loss': 'physics',
         'Compare two runs': 'physics', 'Buildings &amp; infrastructure': 'physics glof', 'Damage': 'physics glof', 'Sediment': 'physics',
         'Nature-based solutions': 'physics', 'Protection structures': 'physics', 'Storm': 'physics'}
for h, md in modes.items():
    pat = re.compile(r'(<div class="panel-section" data-pane="[a-z]+")(>\s*<h3[^>]*>' + re.escape(h) + '</h3>)')
    if len(pat.findall(s)) != 1: die('section ' + h)
    s = pat.sub(lambda m: m.group(1) + ' data-modes="' + md + '"' + m.group(2), s, count=1)

# ---- 5. coastal: two tabs ---------------------------------------------------------------------------------------
once('<div class="tier-content phys-off" data-tier="4">\n        <div class="panel-section">',
     '<div class="tier-content" data-tier="4">\n        <div class="panel-section" data-pane="scenario" data-modes="coastal">')
once('\n          <hr style="margin:16px 0;border-color:#2a3a3e;">\n          <h3>Fast animated preview (approximate)</h3>',
     '\n        </div>\n        <div class="panel-section" data-pane="surge" data-modes="coastal">\n          <h3>Fast animated preview (approximate)</h3>')

# ---- 6. tab bar is built by script ------------------------------------------------------------------------------
m = re.search(r'      <div class="tier-tabs ptabs".*?</div>\n', s, re.S)
s = s.replace(m.group(0), '      <div class="tier-tabs ptabs" id="ptabs" role="tablist" aria-label="Sections"></div>\n', 1)

# ---- 7. css -------------------------------------------------------------------------------------------------------
m = re.search(r'#rightCol\[data-pane="scenario"\] \.panel-section\[data-pane="scenario"\],.*?\{\{ display:block; \}\}\n', s, re.S)
if not m: die('pane css')
s = s.replace(m.group(0), '', 1)
once('.ptabs {{ padding-bottom:10px; }}', '.ptabs {{ padding-bottom:10px; }}\nheader .hdr-nav a.on {{ color:var(--accent); border-color:var(--accent); }}')

# ---- 8. script ----------------------------------------------------------------------------------------------------
m = re.search(r'function showTier\(n\) \{\{.*?\n\}\}\nfunction showPane\(name\) \{\{.*?\n\}\}\ndocument\.addEventListener\(\'DOMContentLoaded\'.*?\n\}\}\);', s, re.S)
if not m: die('script block')
s = s.replace(m.group(0), '''const APP_MODE = document.body.dataset.mode || 'physics';
const MODE_TABS = {{
  physics: [['scenario', 'Scenario'], ['runs', 'Runs'], ['impact', 'Impact'], ['mitigate', 'Mitigation']],
  glof: [['scenario', 'Scenario'], ['runs', 'Runs'], ['impact', 'Impact'], ['glacier', 'Glacier']],
  coastal: [['scenario', 'Static'], ['surge', 'Surge']]
}};
// showTier() is kept so existing callers still work: it shows this page's own 3D view.
function showTier(n) {{
  loadTier3D(APP_MODE === 'coastal' ? 4 : 3);
}}
function showPane(name) {{
  const rc = document.getElementById('rightCol');
  if (!rc) return;
  const tabs = MODE_TABS[APP_MODE] || MODE_TABS.physics;
  if (!tabs.some(t => t[0] === name)) name = tabs[0][0];
  rc.setAttribute('data-pane', name);
  document.querySelectorAll('.ptab').forEach(b => {{
    const on = b.dataset.ptab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  }});
  rc.querySelectorAll('[data-pane]').forEach(el => {{
    const modes = (el.dataset.modes || 'physics').split(' ');
    el.style.display = (modes.includes(APP_MODE) && el.dataset.pane.split(' ').includes(name)) ? 'block' : 'none';
  }});
  rc.scrollTop = 0;
  try {{ sessionStorage.setItem('sailabPane_' + APP_MODE, name); }} catch (e) {{}}
}}
document.addEventListener('DOMContentLoaded', () => {{
  const bar = document.getElementById('ptabs');
  bar.innerHTML = (MODE_TABS[APP_MODE] || MODE_TABS.physics).map(t =>
    '<button class="ptab" data-ptab="' + t[0] + '" role="tab" onclick="showPane(\\\\'' + t[0] + '\\\\')">' + t[1] + '</button>').join('');
  if (APP_MODE === 'glof') {{ document.getElementById('rainMmHr').value = '0'; document.getElementById('durationHr').value = '1'; }}
  let p = '';
  try {{ p = sessionStorage.getItem('sailabPane_' + APP_MODE) || ''; }} catch (e) {{}}
  showPane(p);
}});''', 1)

open(p, 'w', encoding='utf-8').write(s)
print('restructured')
