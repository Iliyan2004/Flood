// node shot.mjs <url> <out.png> [waitMs] [scriptFile]   -- headless Chromium screenshot with an optional page script run before capture
import P from '/home/aliyan/.local/share/gis-workflow-node_modules/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js';
import fs from 'fs';
const [url, out, wait = '9000', script] = process.argv.slice(2);
const b = await P.launch({ executablePath: '/usr/bin/chromium-browser', headless: 'new',
  args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-angle=swiftshader', '--window-size=1440,900'] });
try {
  const pg = await b.newPage(); await pg.setViewport({ width: 1440, height: 900 });
  pg.on('pageerror', e => console.log('PAGEERROR', e.message.slice(0, 200)));
  await pg.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await new Promise(r => setTimeout(r, parseInt(wait)));
  if (script) { const r = await pg.evaluate(fs.readFileSync(script, 'utf8')); if (r) console.log('RESULT', JSON.stringify(r).slice(0, 400)); await new Promise(r => setTimeout(r, 6000)); }
  await pg.screenshot({ path: out }); console.log('saved', out);
} catch (e) { console.log('ERR', e.message); process.exitCode = 1; } finally { await b.close(); }
