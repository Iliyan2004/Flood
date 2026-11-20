import { launch, sleep, snap, setAreaJS, tag, scrollTo, quit } from './lib.mjs';
const S = 'shots/'; const { b, pg } = await launch();
const hideWelcome = () => pg.evaluate(() => document.querySelectorAll('[id*=welcome]').forEach(e => e.style.display = 'none'));
try {
  // ---- Instant
  await pg.goto('http://localhost:8000/instant', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000);
  await snap(pg, S + 'i_welcome.png', [['#wSearch', 1, 'tl'], ['#wChips', 2, 'tl'], ['#wGo', 3, 'tl'], ['#barSearch', 4, 'tl'], ['#drawBtn', 5, 'tl']]);
  await pg.goto('http://localhost:8000/instant#lat=33.667&lon=73.07', { waitUntil: 'domcontentloaded' }); await pg.reload({ waitUntil: 'domcontentloaded' }); await sleep(30000);
  await snap(pg, S + 'i_result.png', [['#barSearch', 1, 'tl'], ['#drawBtn', 2, 'tl'], ['#threeD', 3, 'tl'], ['#pChips', 4, 'tl'], ['#rain', 5, 'tl'], ['#dur', 6, 'tl'], ['#sArea', 7, 'tl'], ['#sDepth', 8, 'tl'], ['#legend', 9, 'tl']]);
  await pg.evaluate(() => document.getElementById('threeD').click()); await sleep(9000); await snap(pg, S + 'i_3d.png');
  // ---- GLOF
  await pg.goto('http://localhost:8000/glof', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000); await hideWelcome();
  await pg.evaluate(setAreaJS(74.595, 36.298, 4)); await sleep(6000);
  await tag(pg, 'label', 'Source', 'src');
  await scrollTo(pg, '#glofSource'); await sleep(800);
  await snap(pg, S + 'g_scenario.png', [['#glofSource', 1, 'tl'], ['#glofBtn', 2, 'tl'], ['#glofEventsBtn', 3, 'tl'], ['#glofTrigger', 4, 'tl']]);
  await pg.click('#glofBtn'); await sleep(5000); await scrollTo(pg, '#glofBox'); await sleep(800); await snap(pg, S + 'g_result.png', [['#glofBox', 1, 'tl']]);
  await pg.click('#glofEventsBtn'); await sleep(5000); await scrollTo(pg, '#glofEventsBox'); await sleep(800); await snap(pg, S + 'g_events.png', [['#glofEventsBox', 1, 'tl']]);
  await tag(pg, 'button', 'Detect rivers automatically', 'det'); await scrollTo(pg, '#detectBoundariesBtn'); await sleep(600); await snap(pg, S + 'g_boundary.png', [['#detectBoundariesBtn', 1, 'tl']]);
  await pg.evaluate(() => showPane('glacier')); await sleep(2000); await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 0; }); await snap(pg, S + 'g_glacier.png', [['#glacierBaselineBtn', 1, 'tl'], ['#glacierDispBtn', 2, 'tl'], ['#itsliveBtn', 3, 'tl'], ['#geomorphBtn', 4, 'tl']]);
  // ---- Coastal
  await pg.goto('http://localhost:8000/coastal', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000); await hideWelcome();
  await pg.evaluate(setAreaJS(67.0, 24.86, 6)); await sleep(6000);
  await scrollTo(pg, '#coastalSource'); await sleep(800);
  await snap(pg, S + 'c_static.png', [['#coastalSource', 1, 'tl'], ['#coastalReturnPeriod', 2, 'tl'], ['#coastalSlr', 3, 'tl'], ['#coastalBtn', 4, 'tl'], ['#coastalLevelSlider', 5, 'tl']]);
  await pg.click('#coastalBtn'); await sleep(14000); await scrollTo(pg, '#coastalBox'); await sleep(800); await snap(pg, S + 'c_result.png', [['#coastalBox', 1, 'tl']]);
  await pg.evaluate(() => showPane('surge')); await sleep(2000); await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 0; });
  await snap(pg, S + 'c_surge.png', [['#coastalPreviewAnchor', 1, 'tl'], ['#coastalPreviewTide', 2, 'tl'], ['#coastalPreviewBtn', 3, 'tl'], ['#coastalDynWindow', 4, 'tl'], ['#coastalDynBtn', 5, 'tl'], ['#coastalCompound', 6, 'l']]);
  await pg.click('#coastalPreviewBtn'); await sleep(25000); await snap(pg, S + 'c_preview.png');
  // ---- landing + sections
  await pg.goto('http://localhost:8000/', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000); await snap(pg, S + 'landing.png');
  console.log('DONE_MODES');
} catch (e) { console.log('ERR', e.message); } finally { await quit(b); }
