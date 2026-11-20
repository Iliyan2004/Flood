import { launch, sleep, snap, setAreaJS, tag, scrollTo } from './lib.mjs';
const S = 'shots/';
const { b, pg } = await launch();
try {
  await pg.goto('http://localhost:8000/app', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000);
  await pg.evaluate(() => document.querySelectorAll('[id*=welcome]').forEach(e => e.style.display = 'none')); await sleep(2500);
  await snap(pg, S + 'p_pakistan_only.png');
  // outside Pakistan: refused
  await pg.evaluate(setAreaJS(77.2, 28.6, 5)); await sleep(2500);
  await snap(pg, S + 'p_outside.png', [['#areaLimitNote', 1, 'tl']]);
  // too large: shown but cannot run
  await pg.evaluate(setAreaJS(66.8, 27.6, 95)); await sleep(5000);
  await scrollTo(pg, '#areaInfo'); await sleep(800);
  await snap(pg, S + 'p_toolarge.png', [['#areaLimitNote', 1, 'tl'], ['#areaKm2', 2, 'tr']]);
  // real area, storm
  await pg.evaluate(setAreaJS(73.03, 33.70, 2.4)); await sleep(7000);
  await tag(pg, 'summary', 'Design storm by return period', 'dstorm'); await tag(pg, 'summary', 'Recorded rainfall curve', 'idf'); await tag(pg, 'summary', 'Upload rain-gauge data', 'csv');
  await scrollTo(pg, '#rainMmHr'); await sleep(800);
  await snap(pg, S + 'p_storm.png', [['#rainMmHr', 1, 'tl'], ['#durationHr', 2, 'tl'], ['[data-m=dstorm]', 3, 'l'], ['[data-m=idf]', 4, 'l'], ['[data-m=csv]', 5, 'l']]);
  await pg.evaluate(() => document.querySelector('[data-m=dstorm]').parentElement.open = true); await sleep(600);
  await snap(pg, S + 'p_storm_design.png');
  await pg.evaluate(() => document.querySelector('[data-m=dstorm]').parentElement.open = false);
  await tag(pg, 'button', '+ Add manual inflow', 'addin'); await tag(pg, 'button', '+ Add manual outflow', 'addout'); await tag(pg, 'summary', 'Advanced settings', 'adv');
  await scrollTo(pg, '#detectBoundariesBtn'); await sleep(800);
  await snap(pg, S + 'p_boundary.png', [['#detectBoundariesBtn', 1, 'tl'], ['[data-m=addin]', 2, 'bl'], ['[data-m=addout]', 3, 'bl'], ['[data-m=adv]', 4, 'l']]);
  await pg.evaluate(() => { const d = document.querySelector('[data-m=adv]'); d.parentElement.open = true; }); await sleep(600);
  await snap(pg, S + 'p_advanced.png');
  await pg.evaluate(() => { const d = document.querySelector('[data-m=adv]'); d.parentElement.open = false; });
  await scrollTo(pg, '#infiltrationMmHr'); await sleep(800);
  await snap(pg, S + 'p_soil.png', [['#infiltrationMmHr', 1, 'tl'], ['#useSoilInfiltration', 2, 'l'], ['#amc', 3, 'tl'], ['#includeBuildings', 4, 'l'], ['#runBtn', 5, 'tl']]);
  // run
  await pg.click('#runBtn'); await sleep(16000);
  await snap(pg, S + 'p_running.png', [['#runStatusBox', 1, 'tl']]);
  const t0 = Date.now(); let st = '';
  while (Date.now() - t0 < 300000) { st = await pg.evaluate(async () => { const id = window.currentRunId || (typeof currentRunId !== 'undefined' ? currentRunId : null); if (!id) return 'noid'; return (await fetch('/runs/' + id + '/progress.json').then(r => r.json())).status; }); if (st === 'done' || st === 'error') break; await sleep(5000); }
  console.log('run status', st, 'after', Math.round((Date.now() - t0) / 1000), 's');
  const rid = await pg.evaluate(() => currentRunId); console.log('RUNID', rid);
  await sleep(14000);
  await snap(pg, S + 'p_done.png');
  await pg.evaluate(() => setViewMode('main')); await sleep(9000);
  await snap(pg, S + 'p_view_expanded.png');
  await pg.evaluate(() => setViewMode('card')); await sleep(1000);
  for (const [pane, name] of [['runs', 'p_runs'], ['impact', 'p_impact'], ['mitigate', 'p_mitigate']]) {
    await pg.evaluate(p => showPane(p), pane); await sleep(1800);
    await pg.evaluate(() => { const rc = document.getElementById('rightCol'); rc.scrollTop = 0; });
    await snap(pg, S + name + '.png');
  }
  await pg.evaluate(() => showPane('runs')); await sleep(800);
  await tag(pg, 'button', 'Run all selected', 'batch'); await tag(pg, 'button', 'Compute Average Annual Loss', 'aal'); await tag(pg, 'button', 'Compare', 'cmp');
  await scrollTo(pg, '[data-m=batch]'); await sleep(800);
  await snap(pg, S + 'p_runs2.png', [['[data-m=batch]', 1, 'tl'], ['[data-m=aal]', 2, 'tl'], ['[data-m=cmp]', 3, 'tl']]);
  await pg.evaluate(() => showPane('impact')); await sleep(800); await scrollTo(pg, '#rightCol .panel-section:nth-of-type(3)');
  await snap(pg, S + 'p_impact2.png');
  require_done: {
    console.log('DONE_PHYSICS', rid);
  }
} catch (e) { console.log('ERR', e.message); } finally { await b.close(); }
