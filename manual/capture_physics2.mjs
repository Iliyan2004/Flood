import { launch, sleep, snap, setAreaJS, tag, scrollTo, quit } from './lib.mjs';
const S = 'shots/'; const { b, pg } = await launch();
try {
  await pg.goto('http://localhost:8000/app', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000);
  await pg.evaluate(() => document.querySelectorAll('[id*=welcome]').forEach(e => e.style.display = 'none'));
  // too-large area, corrected message
  await pg.evaluate(setAreaJS(66.8, 27.6, 95)); await sleep(4500); await scrollTo(pg, '#areaInfo'); await sleep(600);
  await snap(pg, S + 'p_toolarge.png', [['#areaLimitNote', 1, 'tl'], ['#areaKm2', 2, 'tr']]);
  // a real run, watched
  await pg.evaluate(setAreaJS(73.03, 33.70, 2.4)); await sleep(7000);
  await pg.evaluate(() => { document.getElementById('rainMmHr').value = 42; });
  await pg.click('#runBtn');
  let rid = null; for (let i = 0; i < 40 && !rid; i++) { await sleep(3000); rid = await pg.evaluate(() => currentRunId); }
  console.log('RUNID', rid);
  await sleep(5000); await snap(pg, S + 'p_running.png', [['#runStatusBox', 1, 'tl']]);
  const t0 = Date.now(); let st = '';
  while (Date.now() - t0 < 280000) { st = await pg.evaluate(async id => (await fetch('/runs/' + id + '/progress.json').then(r => r.json())).status, rid); if (st === 'done' || st === 'error') break; await sleep(4000); if (Date.now() - t0 > 30000 && Date.now() - t0 < 36000) await snap(pg, S + 'p_running2.png', [['#runStatusBox', 1, 'tl']]); }
  console.log('status', st, Math.round((Date.now() - t0) / 1000) + 's');
  await sleep(15000); await snap(pg, S + 'p_done.png', [['#runStatusBox', 1, 'tl'], ['#view3dFrame', 2, 'tl']]);
  await pg.evaluate(() => setViewMode('main')); await sleep(10000); await snap(pg, S + 'p_view_expanded.png');
  await pg.evaluate(() => setViewMode('card')); await sleep(1500);
  for (const [pane, name] of [['runs', 'p_runs'], ['impact', 'p_impact'], ['mitigate', 'p_mitigate']]) {
    await pg.evaluate(p => showPane(p), pane); await sleep(2000); await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 0; }); await snap(pg, S + name + '.png');
  }
  await pg.evaluate(() => showPane('runs')); await sleep(800);
  await tag(pg, 'button', 'Run all selected', 'batch'); await tag(pg, 'button', 'Compute Average Annual Loss', 'aal'); await tag(pg, 'button', 'Compare', 'cmp');
  await scrollTo(pg, '[data-m=batch]'); await sleep(800);
  await snap(pg, S + 'p_runs2.png', [['[data-m=batch]', 1, 'tl'], ['[data-m=aal]', 2, 'tl'], ['[data-m=cmp]', 3, 'tl']]);
  console.log('DONE_PHYSICS', rid);
} catch (e) { console.log('ERR', e.message); } finally { await quit(b); }
