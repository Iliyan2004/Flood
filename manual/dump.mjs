import P from '/home/aliyan/.local/share/gis-workflow-node_modules/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js';
const url = process.argv[2];
const b = await P.launch({ executablePath: '/usr/bin/chromium-browser', headless: 'new', args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--window-size=1440,900'] });
const pg = await b.newPage(); await pg.setViewport({ width: 1440, height: 900 });
await pg.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }); await new Promise(r => setTimeout(r, 6000));
const out = await pg.evaluate(() => {
  const tabs = [...document.querySelectorAll('.tier-tabs button, #tierTabs button, [data-pane-tab], .pane-tabs button')].map(b => b.textContent.trim());
  const secs = [...document.querySelectorAll('.panel-section')].map(s => ({ pane: s.dataset.pane || '', modes: s.dataset.modes || '', off: s.classList.contains('phys-off'), h: (s.querySelector('h3')||{}).textContent || '',
     labels: [...s.querySelectorAll('label')].map(l => l.textContent.trim().replace(/\s+/g,' ').slice(0,70)).filter(Boolean).slice(0,40),
     buttons: [...s.querySelectorAll('button')].map(l => (l.id||'')+'|'+l.textContent.trim().replace(/\s+/g,' ').slice(0,50)).slice(0,30) }));
  return { mode: document.body.dataset.mode, tabs, secs };
});
console.log(JSON.stringify(out, null, 0)); await b.close();
