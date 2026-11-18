// Source provenance survives history, page reload and video/image transitions.
// This uses an isolated browser; every mutation is blocked.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const browser = await chromium.launch({ headless: true,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--disable-gpu', '--enable-unsafe-swiftshader'] });
  const baseUrl = process.env.FLOWHMR_URL || 'http://127.0.0.1:60550';
  try {
    const page = await browser.newPage({ viewport: { width: 1450, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/api/**', route => ['GET', 'HEAD'].includes(route.request().method())
      ? route.continue() : route.abort('blockedbyclient'));
    const history = await (await page.request.get(`${baseUrl}/api/jobs`)).json();
    const imageJob = history.jobs.find(job => job.status === 'done' && job.source_kind === 'image');
    assert(imageJob, 'Generate one image pose before running this integration test');
    const actualId = imageJob.id;
    const base = await (await page.request.get(`${baseUrl}/api/jobs/${actualId}`)).json();
    const metadata = await (await page.request.get(`${baseUrl}/api/jobs/${actualId}/meta`)).json();
    const legacy = { ...base, id: 'legacy-image', filename: 'input.mp4', status: 'done' };
    delete legacy.source_kind; delete legacy.source_name;
    const video = { ...legacy, id: 'one-second-video' };
    const videoMeta = { ...metadata };
    delete videoMeta.source_kind; delete videoMeta.source_name;
    await page.route('**/api/jobs?**', route => route.fulfill({ json: {
      jobs: [legacy, video], next_cursor: null, deletable_count: 2 } }));
    await page.route(/\/api\/jobs\/(legacy-image|one-second-video)(?:\/|$)/, async route => {
      const url = new URL(route.request().url());
      const [, id, suffix = ''] = url.pathname.match(/\/api\/jobs\/([^/]+)(.*)/);
      if (!suffix) return route.fulfill({ json: id === legacy.id ? legacy : video });
      if (suffix === '/meta') return route.fulfill({ json: id === legacy.id
        ? { ...metadata, source_kind: 'image', source_name: '原图姿势.png' } : videoMeta });
      const response = await route.fetch({ url: `${baseUrl}/api/jobs/${actualId}${suffix}${url.search}` });
      return route.fulfill({ response });
    });
    async function checkImage(name) {
      await page.waitForFunction(() => document.body.classList.contains('image-source') &&
        document.getElementById('previewImage').naturalWidth > 0);
      assert(await page.locator('#previewImage').isVisible());
      assert(!(await page.locator('#previewVideo').isVisible()));
      assert(!(await page.locator('#previewVideo').evaluate(video => video.controls)));
      assert(!(await page.locator('#sourceClip').isVisible()));
      assert(!(await page.locator('.timeline-editor').isVisible()));
      assert((await page.locator('#previewTitle').textContent()).includes(name));
    }
    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    await checkImage('原图姿势.png');
    await page.locator('[data-job="one-second-video"] .run-open').click();
    await page.waitForFunction(() => !document.body.classList.contains('image-source'));
    await page.locator('#previewBox').hover();
    await page.waitForFunction(() => document.getElementById('previewVideo').controls);
    assert(await page.locator('#previewVideo').isVisible());
    assert(await page.locator('#sourceClip').isVisible());
    assert(await page.locator('.timeline-editor').isVisible());
    await page.locator('[data-job="legacy-image"] .run-open').click();
    await checkImage('原图姿势.png');
    await page.reload({ waitUntil: 'networkidle' });
    await checkImage('原图姿势.png');
    assert.deepEqual(errors, []);
    console.log('LEGACY_IMAGE_HISTORY_RELOAD_AND_REAL_VIDEO_SWITCH_OK');

    // Confirm the user's existing task on the actual running backend as well.
    const live = await browser.newPage({ viewport: { width: 1450, height: 1000 } });
    await live.route('**/api/**', route => ['GET', 'HEAD'].includes(route.request().method())
      ? route.continue() : route.abort('blockedbyclient'));
    await live.goto(baseUrl, { waitUntil: 'networkidle' });
    await live.locator(`[data-job="${actualId}"] .run-open`).click();
    await live.waitForFunction(() => document.body.classList.contains('image-source') &&
      document.getElementById('previewImage').naturalWidth > 0);
    assert(!(await live.locator('#previewVideo').isVisible()));
    assert(!(await live.locator('.timeline-editor').isVisible()));
    assert((await live.locator('#previewTitle').textContent()).includes(imageJob.source_name));
    await live.screenshot({ path: path.join(root, 'logs/web-current-image-fixed.png'), fullPage: true });
    console.log('CURRENT_USER_IMAGE_TASK_STATIC_PREVIEW_WITHOUT_BACKEND_RESTART_OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
