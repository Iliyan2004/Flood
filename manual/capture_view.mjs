import { launch, sleep, setAreaJS, annotateFrame, quit } from './lib.mjs';
const rid = process.argv[2]; const S = 'shots/'; const { b, pg } = await launch();
try {
  await pg.goto('http://localhost:8000/app', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(8000);
  await pg.evaluate(() => document.querySelectorAll('[id*=welcome]').forEach(e => e.style.display = 'none'));
  await pg.evaluate(setAreaJS(73.03, 33.70, 2.4)); await sleep(4000);
  await pg.evaluate(id => { currentRunId = id; pollStatus(); }, rid); await sleep(45000);
  const fr = pg.frames().find(f => f.url().includes('mapbox_3d')); console.log('frame', !!fr);
  await fr.evaluate(() => { const s = document.getElementById('timeSlider'); if (s) { s.value = Math.round(s.max * 0.45); s.dispatchEvent(new Event('input', { bubbles: true })); } }); await sleep(6000);
  const miss = await annotateFrame(fr, [['#modeSection', 1, 'tl'], ['#legendBar', 2, 'tl'], ['.cards .c-max', 3, 'tr'], ['#pillBuildings', 4, 'tl'], ['#fineBox', 5, 'tl'], ['#playPauseBtn', 6, 'tl'], ['#stArea', 7, 'tl'], ['#chartWrap', 8, 'tl'], ['#timeSlider', 9, 'tl'], ['#speedSelect', 10, 'tl'], ['#exportToggle', 11, 'tl'], ['#rainBtn', 12, 'tl'], ['#resetBtn', 13, 'tr']]);
  console.log('missing', miss.join(','));
  await pg.screenshot({ path: S + 'v_main.png' });
  await fr.evaluate(() => document.querySelectorAll('.mbadge').forEach(e => e.remove()));
  // pin a probe on the water
  const r = await pg.evaluate(() => { const f = document.getElementById('view3dFrame').getBoundingClientRect(); return [f.left, f.top, f.width, f.height]; });
  await pg.mouse.click(r[0] + r[2] * 0.62, r[1] + r[3] * 0.42); await sleep(4000);
  const m2 = await annotateFrame(fr, [['#probe', 1, 'tl']]); console.log('missing2', m2.join(','));
  await pg.screenshot({ path: S + 'v_probe.png' }); await fr.evaluate(() => document.querySelectorAll('.mbadge').forEach(e => e.remove()));
  await fr.evaluate(() => { const p = document.getElementById('probeClose'); if (p) p.click(); const c = document.querySelector('.cards .c-max'); if (c) c.click(); }); await sleep(6000); await pg.screenshot({ path: S + 'v_max.png' });
  await fr.evaluate(() => { const c = document.querySelector('.cards .c-speed'); if (c) c.click(); }); await sleep(6000); await pg.screenshot({ path: S + 'v_speed.png' });
  await fr.evaluate(() => { const c = document.querySelector('.cards .c-depth'); if (c) c.click(); const e = document.getElementById('exportToggle'); if (e) e.click(); }); await sleep(2500);
  await annotateFrame(fr, [['#exportMenu', 1, 'tl']]); await pg.screenshot({ path: S + 'v_export.png' });
  console.log('DONE_VIEW');
} catch (e) { console.log('ERR', e.message); } finally { await quit(b); }
