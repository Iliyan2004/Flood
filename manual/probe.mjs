import { launch, sleep, snap, setAreaJS } from './lib.mjs';
const { b, pg } = await launch();
try {
  await pg.goto('http://localhost:8000/app', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000);
  console.log(await pg.evaluate(() => [...document.querySelectorAll('[id*=welcome]')].map(e => e.id + ':' + e.className).join(' | ')));
  await snap(pg, 'shots/p_welcome.png');
  const a = await pg.evaluate(setAreaJS(73.03, 33.70, 2.4)); console.log(JSON.stringify(a).slice(0, 200)); await sleep(6000);
  await snap(pg, 'shots/p_area.png', [['#geoInput', 1, 'tl'], ['.mapbox-gl-draw_polygon', 2, 'r'], ['#areaKm2', 3, 'tr'], ['.ptab', 4, 'tl']]);
} catch (e) { console.log('ERR', e.message); } finally { await b.close(); }
