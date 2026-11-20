import { launch, sleep, snap, setAreaJS, tag, scrollTo, quit } from './lib.mjs';
const rid = process.argv[2]; const S = 'shots/'; const { b, pg } = await launch();
try {
  await pg.goto('http://localhost:8000/app', { waitUntil: 'domcontentloaded', timeout: 60000 }); await sleep(9000);
  await pg.evaluate(() => document.querySelectorAll('[id*=welcome]').forEach(e => e.style.display = 'none'));
  await pg.evaluate(setAreaJS(73.03, 33.70, 2.4)); await sleep(5000);
  // restore the finished run into the page without the heavy 3D view
  await pg.evaluate(id => { currentRunId = id; setViewMode('hidden'); pollStatus(); }, rid); await sleep(9000);
  await pg.evaluate(() => showPane('runs')); await sleep(1500); await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 0; });
  await snap(pg, S + 'p_runs.png');
  await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 520; }); await sleep(800); await snap(pg, S + 'p_runs_b.png');
  await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 1100; }); await sleep(800); await snap(pg, S + 'p_runs_c.png');
  await tag(pg, 'button', 'Run all selected', 'batch'); await tag(pg, 'button', 'Compute Average Annual Loss', 'aal'); await tag(pg, 'button', 'Compare', 'cmp');
  await scrollTo(pg, '[data-m=batch]'); await sleep(800);
  await snap(pg, S + 'p_runs2.png', [['[data-m=batch]', 1, 'tl'], ['[data-m=aal]', 2, 'tl'], ['[data-m=cmp]', 3, 'tl']]);
  for (const [pane, name] of [['impact', 'p_impact'], ['mitigate', 'p_mitigate']]) {
    await pg.evaluate(p => showPane(p), pane); await sleep(2500); await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 0; }); await snap(pg, S + name + '.png');
    await pg.evaluate(() => { document.getElementById('rightCol').scrollTop = 600; }); await sleep(700); await snap(pg, S + name + '_b.png');
  }
  console.log('DONE_PANES');
} catch (e) { console.log('ERR', e.message); } finally { await quit(b); }
