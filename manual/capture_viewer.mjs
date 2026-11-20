import { launch, sleep, snap, quit } from './lib.mjs';
const rid = process.argv[2]; const S = 'shots/'; const { b, pg } = await launch(1440, 900, 1.5);
try {
  await pg.goto(`http://localhost:8000/runs/${rid}/mapbox_3d.html`, { waitUntil: 'domcontentloaded', timeout: 90000 }); await sleep(22000);
  await pg.evaluate(() => { const s = document.getElementById('timeSlider'); if (s) { s.value = Math.round(s.max * 0.42); s.dispatchEvent(new Event('input', { bubbles: true })); } const p = document.getElementById('playPauseBtn'); if (p && p.textContent.trim().length) p.click(); });
  await sleep(5000);
  await snap(pg, S + 'v_main.png', [['#modeSection', 1, 'tl'], ['#legendBar', 2, 'tl'], ['.cards .c-max', 3, 'tr'], ['#pillBuildings', 4, 'tl'], ['#fineBox', 5, 'tl'], ['#playPauseBtn', 6, 'tl'], ['#stArea', 7, 'tl'], ['#timeSlider', 8, 'tl'], ['#speedSelect', 9, 'tl'], ['#exportToggle', 10, 'tl'], ['#rainBtn', 11, 'tl'], ['#resetBtn', 12, 'tr']]);
  // pin a probe at the centre of the area
  const box = await pg.evaluate(() => { const r = document.getElementById('map').getBoundingClientRect(); return [r.left + r.width * 0.55, r.top + r.height * 0.38]; });
  await pg.mouse.click(box[0], box[1]); await sleep(3500);
  await snap(pg, S + 'v_probe.png', [['#probe', 1, 'tl']]);
  await pg.evaluate(() => { const c = document.querySelector('.cards .c-max'); if (c) c.click(); }); await sleep(5000); await snap(pg, S + 'v_max.png');
  await pg.evaluate(() => { const c = document.querySelector('.cards .c-speed'); if (c) c.click(); }); await sleep(5000); await snap(pg, S + 'v_speed.png');
  await pg.evaluate(() => { const c = document.querySelector('.cards .c-depth'); if (c) c.click(); const e = document.getElementById('exportToggle'); if (e) e.click(); }); await sleep(2000);
  await snap(pg, S + 'v_export.png', [['#exportMenu', 1, 'tl']]);
  console.log('DONE_VIEWER');
} catch (e) { console.log('ERR', e.message); } finally { await quit(b); }
