import P from '/home/aliyan/.local/share/gis-workflow-node_modules/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js';
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export async function launch(w = 1440, h = 900, scale = 1.5) {
  const b = await P.launch({ executablePath: '/usr/bin/chromium-browser', headless: 'new', protocolTimeout: 240000,
    args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-angle=swiftshader', `--window-size=${w},${h}`] });
  const pg = await b.newPage(); await pg.setViewport({ width: w, height: h, deviceScaleFactor: scale });
  pg.on('pageerror', e => console.log('PAGEERROR', e.message.slice(0, 160)));
  return { b, pg };
}
// numbered badges placed on real elements: items = [[selector, number, 'tl'|'tr'|'bl'|'br'|'l'|'r']]
export async function annotate(pg, items) {
  return pg.evaluate(items => {
    document.querySelectorAll('.mbadge').forEach(e => e.remove());
    const miss = [];
    items.forEach(([sel, n, pos = 'tl']) => {
      const el = document.querySelector(sel); if (!el) { miss.push(sel); return; }
      const r = el.getBoundingClientRect(); if (r.width === 0 && r.height === 0) { miss.push(sel + ' (hidden)'); return; }
      const d = document.createElement('div'); d.className = 'mbadge'; d.textContent = n;
      d.style.cssText = 'position:fixed;z-index:99999;width:24px;height:24px;border-radius:50%;background:#e8590c;color:#fff;font:700 13px/24px system-ui,sans-serif;text-align:center;box-shadow:0 1px 5px rgba(0,0,0,.55);border:2px solid #fff;pointer-events:none;';
      const x = pos.includes('r') ? r.right - 12 : (pos === 'l' ? r.left - 26 : r.left - 12), y = pos.includes('b') ? r.bottom - 12 : (pos === 'l' || pos === 'r' ? r.top + r.height / 2 - 12 : r.top - 12);
      d.style.left = Math.max(2, x) + 'px'; d.style.top = Math.max(2, y) + 'px'; document.body.appendChild(d);
      const o = document.createElement('div'); o.className = 'mbadge';
      o.style.cssText = `position:fixed;z-index:99998;left:${r.left - 2}px;top:${r.top - 2}px;width:${r.width + 4}px;height:${r.height + 4}px;border:2px solid #e8590c;border-radius:6px;pointer-events:none;`; document.body.appendChild(o);
    }); return miss;
  }, items);
}
export const clearBadges = pg => pg.evaluate(() => document.querySelectorAll('.mbadge').forEach(e => e.remove()));
export async function snap(pg, path, items) {
  if (items) { const miss = await annotate(pg, items); if (miss.length) console.log('  missing:', miss.join(', ')); }
  await pg.screenshot({ path }); await clearBadges(pg); console.log('saved', path);
}
export const boxJS = (lon, lat, km) => `(() => { const dl = ${km}/2/111.0, dn = dl/Math.cos(${lat}*Math.PI/180); return {type:'Polygon',coordinates:[[[${lon}-dn,${lat}-dl],[${lon}+dn,${lat}-dl],[${lon}+dn,${lat}+dl],[${lon}-dn,${lat}+dl],[${lon}-dn,${lat}-dl]]]}; })()`;
export const setAreaJS = (lon, lat, km) => `(async () => { const g = ${boxJS(lon, lat, km)};
  document.querySelectorAll('[id*=welcome]').forEach(e => e.style.display = 'none');
  draw.getAll().features.forEach(f => draw.delete(f.id)); draw.add({type:'Feature',properties:{},geometry:g});
  const a = await fetch('/area/polygon',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:g})}).then(r=>r.json());
  acceptArea(g, a); const xs=g.coordinates[0].map(p=>p[0]), ys=g.coordinates[0].map(p=>p[1]);
  map.fitBounds([[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]],{padding:{top:90,bottom:90,left:80,right:470},duration:0}); return a; })()`;
export const tag = (pg, sel, text, key) => pg.evaluate((sel, text, key) => { const e = [...document.querySelectorAll(sel)].find(x => x.textContent.trim().replace(/\s+/g, ' ').startsWith(text)); if (e) e.setAttribute('data-m', key); return !!e; }, sel, text, key);
export const scrollTo = (pg, sel) => pg.evaluate(sel => { const e = document.querySelector(sel); if (e) e.scrollIntoView({ block: 'start' }); const rc = document.getElementById('rightCol'); if (rc) rc.scrollTop = Math.max(0, rc.scrollTop - 60); return !!e; }, sel);
export async function quit(b) { try { const s = await b.target().createCDPSession(); await s.send('Browser.close'); } catch (e) {} await sleep(1500); process.exit(0); }
// same as annotate() but inside an iframe's own document (coordinates are local to the frame)
export async function annotateFrame(fr, items) {
  return fr.evaluate(items => {
    document.querySelectorAll('.mbadge').forEach(e => e.remove()); const miss = [];
    items.forEach(([sel, n, pos = 'tl']) => {
      const el = document.querySelector(sel); if (!el) { miss.push(sel); return; }
      const r = el.getBoundingClientRect(); if (r.width === 0 && r.height === 0) { miss.push(sel + ' (hidden)'); return; }
      const d = document.createElement('div'); d.className = 'mbadge'; d.textContent = n;
      d.style.cssText = 'position:fixed;z-index:99999;width:24px;height:24px;border-radius:50%;background:#e8590c;color:#fff;font:700 13px/24px system-ui,sans-serif;text-align:center;box-shadow:0 1px 5px rgba(0,0,0,.55);border:2px solid #fff;pointer-events:none;';
      const x = pos.includes('r') ? r.right - 12 : r.left - 12, y = pos.includes('b') ? r.bottom - 12 : r.top - 12;
      d.style.left = Math.max(2, x) + 'px'; d.style.top = Math.max(2, y) + 'px'; document.body.appendChild(d);
      const o = document.createElement('div'); o.className = 'mbadge';
      o.style.cssText = `position:fixed;z-index:99998;left:${r.left - 2}px;top:${r.top - 2}px;width:${r.width + 4}px;height:${r.height + 4}px;border:2px solid #e8590c;border-radius:6px;pointer-events:none;`; document.body.appendChild(o);
    }); return miss;
  }, items);
}
