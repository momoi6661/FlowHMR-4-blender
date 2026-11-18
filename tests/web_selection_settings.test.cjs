// Real settings UI in an isolated browser: no backend, jobs or model changes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const web = path.join(root, 'tools/web');
const defaults = { manual_select: true, select_single_person: false, static_camera: true,
  postprocess: false, hamer: false, sam_chunk_size: null, sam_hand_stride: 1 };
const harness = fs.readFileSync(path.join(web, 'index.html'), 'utf8')
  .replace(/<script\b[\s\S]*?<\/script>/gi, '')
  .replace('</body>', `<script type="module">
    import {initParams} from '/static/js/params.js';
    import {catalogs} from '/static/js/i18n.js';
    window.params=initParams(${JSON.stringify(defaults)},1);
    window.catalogs=catalogs;window.ready=true;
  </script></body>`);

(async () => {
  const browserServer = await chromium.launchServer({ headless: true,
    executablePath: process.env.FLOWHMR_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--disable-gpu', '--enable-unsafe-swiftshader'] });
  const browser = await chromium.connect(browserServer.wsEndpoint());
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: harness });
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve(web, `.${url.pathname}`);
        assert(file.startsWith(path.join(web, 'static') + path.sep));
        if (fs.existsSync(file)) return route.fulfill({
          contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream',
          body: fs.readFileSync(file) });
      }
      return route.fulfill({ status: 404 });
    });
    await page.goto('http://flowhmr.fixture/');
    const ready = () => page.waitForFunction(() => window.ready);
    const values = () => page.evaluate(() => {
      const p = params.read();
      return { manual: p.manual_select, single: p.select_single_person,
        disabled: document.getElementById('optSinglePerson').disabled };
    });
    await ready();
    assert.deepEqual(await values(), { manual: true, single: false, disabled: false });
    await page.locator('#optSinglePerson').click();
    assert.deepEqual(await values(), { manual: true, single: true, disabled: false });
    await page.locator('#optManual').click();
    assert.deepEqual(await values(), { manual: false, single: true, disabled: true });
    await page.locator('#optSinglePerson').evaluate(button => button.click());
    assert.deepEqual(await values(), { manual: false, single: true, disabled: true });
    await page.reload(); await ready();
    assert.deepEqual(await values(), { manual: false, single: true, disabled: true });
    await page.locator('#optManual').click();
    assert.deepEqual(await values(), { manual: true, single: true, disabled: false });
    await page.locator('#optSinglePerson').click();
    assert.deepEqual(await values(), { manual: true, single: false, disabled: false });
    await page.evaluate(() => params.openJob('history', {manual_select:false,select_single_person:true}));
    assert.deepEqual(await values(), { manual: false, single: true, disabled: true });
    await page.evaluate(() => params.openJob('legacy', {manual_select:true}));
    assert.deepEqual(await values(), { manual: true, single: false, disabled: false });
    await page.evaluate(() => params.newSource());
    assert.deepEqual(await values(), { manual: true, single: false, disabled: false });
    await page.locator('#optSinglePerson').click();
    await page.evaluate(() => params.reset());
    assert.deepEqual(await values(), { manual: true, single: false, disabled: false });
    assert(await page.evaluate(() => Object.values(catalogs).every(catalog =>
      ['person.manual','person.manual_hint','person.single','person.single_hint'].every(key => catalog[key]))));
    assert.deepEqual(errors, []);
    console.log('WEB_SELECTION_PARENT_CHILD_DISABLED_PREFERENCE_RELOAD_HISTORY_AND_DEFAULTS_OK');
  } finally {
    const closed = browser.close();
    const killed = browserServer.kill();
    for (const pipe of browserServer.process().stdio) pipe?.destroy?.();
    // Windows can retain the browser's inherited handles after Chrome exits.
    // Bound teardown of this standalone fixture after terminating its browser.
    let timer;
    try {
      await Promise.race([Promise.allSettled([closed, killed]), new Promise(resolve => { timer = setTimeout(resolve, 3000); })]);
    } finally { clearTimeout(timer); }
  }
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
