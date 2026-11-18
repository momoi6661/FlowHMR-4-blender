// Isolated browser fixture: no inference, selection or GPU release on user jobs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const browser = await chromium.launch({ headless: true,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--disable-gpu', '--enable-unsafe-swiftshader'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    let corrected = null, supported = false;
    const source = await (await page.request.get('http://127.0.0.1:60550/api/jobs')).json();
    const base = source.jobs.find(job => job.source_kind === 'image' && job.status === 'done');
    assert(base, 'Generate one image pose before running this integration test');
    const job = { ...base, id: 'fixture-box', filename: 'pose.png.webm', source_name: 'pose.png', source_kind: 'image',
      status: 'waiting_user', progress: 10, num_frames: null, selected_person: null,
      has_preview_video: true, candidates: [{ index: 0, track_id: 1, frames: [0, 29],
        boxes: [[10, 10, 100, 110], [10, 10, 100, 110]], start: 0, end: 29, num_frames: 30, static_image: true }] };
    await page.route('**/api/info', async route => {
      const info = await (await route.fetch()).json();
      info.blender_bridge = { ...info.blender_bridge, manual_boxes: supported };
      await route.fulfill({ json: info });
    });
    await page.route('**/api/jobs?**', route => route.fulfill({ json: { jobs: [job], next_cursor: null } }));
    await page.route('**/api/jobs/fixture-box', route => route.fulfill({ json: job }));
    await page.route('**/api/jobs/fixture-box/video?**', route => route.fulfill({
      contentType: 'video/webm', body: fs.readFileSync(path.join(root, '.cache/blender-test/browser-image.webm')) }));
    await page.route('**/api/jobs/fixture-box/person/*', route => route.fulfill({ status: 404 }));
    await page.route('**/api/jobs/fixture-box/select-boxes', async route => {
      corrected = route.request().postDataJSON();
      await route.fulfill({ json: { ok: true, people: [0] } });
    });
    await page.goto('http://127.0.0.1:60550/', { waitUntil: 'networkidle' });
    try {
      await page.waitForFunction(() => document.getElementById('previewImage').naturalWidth > 0, null, { timeout: 10000 });
    } catch (error) {
      console.error('BROWSER_ERRORS', errors);
      console.error('PREVIEW_STATE', await page.locator('#previewVideo').evaluate(video => ({
        src: video.currentSrc, ready: video.readyState, error: video.error?.message,
        image: document.getElementById('previewImage').src,
      })));
      throw error;
    }
    assert(await page.locator('#previewImage').isVisible());
    assert(!(await page.locator('#previewVideo').isVisible()));
    assert(!(await page.locator('.timeline-editor').isVisible()));
    assert(!(await page.locator('#sourceClip').isVisible()));
    assert(!(await page.locator('#previewVideo').evaluate(video => video.controls)));
    assert((await page.locator('#previewTitle').textContent()).includes('pose.png'));
    assert(!(await page.locator('#previewTitle').textContent()).includes('.webm'));
    assert((await page.locator('.person-meta').textContent()).includes('静态'));
    assert(!(await page.locator('.person-meta').textContent()).includes('34'));
    // The edit button must fit even in a narrow source editor, without scrolling.
    for (const width of [420, 300, 240, 180]) {
      await page.locator('.workspace').evaluate((workspace, width) => {
        workspace.style.setProperty('--source-size', `${width}px`);
      }, width);
      await page.waitForTimeout(40);
      const layout = await page.locator('#personList').evaluate(list => {
        const button = list.querySelector('.person-edit').getBoundingClientRect();
        const bounds = list.getBoundingClientRect();
        return { scroll: list.scrollWidth, width: list.clientWidth,
          left: button.left - bounds.left, right: button.right - bounds.left };
      });
      assert(layout.scroll <= layout.width + 1, `Horizontal overflow at ${width}px: ${JSON.stringify(layout)}`);
      assert(layout.left >= 0 && layout.right <= layout.width + 1, `Hidden edit button at ${width}px`);
    }
    await page.locator('.workspace').evaluate(workspace => workspace.style.setProperty('--source-size', '320px'));
    await page.screenshot({ path: path.join(root, 'logs/web-person-box-responsive.png'), fullPage: true });
    await page.locator('.person-edit').click();
    await page.waitForFunction(() => document.getElementById('previewCanvas').classList.contains('editing-box'));
    const geometry = await page.evaluate(() => {
      const img = document.getElementById('previewImage'), canvas = document.getElementById('previewCanvas');
      const r = canvas.getBoundingClientRect(), w = img.naturalWidth, h = img.naturalHeight;
      const s = Math.min(img.clientWidth / w, img.clientHeight / h);
      return { w, h, x: r.left + (img.clientWidth - w * s) / 2, y: r.top + (img.clientHeight - h * s) / 2, s };
    });
    const { w, h, x, y, s } = geometry;
    await page.mouse.move(x + w * .2 * s, y + h * .15 * s);
    await page.mouse.down();
    await page.mouse.move(x + w * .85 * s, y + h * .9 * s, { steps: 8 });
    await page.mouse.up();
    await page.waitForFunction(() => !document.getElementById('personConfirm').disabled);
    assert(await page.locator('#boxEditHint').isVisible());
    await page.locator('#personConfirm').click();
    await page.waitForFunction(() => document.getElementById('personError').textContent.includes('重启'));
    assert.equal(corrected, null, 'An old server must not silently discard edits');
    supported = true;
    const selectionResponse = page.waitForResponse(response => response.url().endsWith('/api/jobs/fixture-box/select-boxes') && response.request().method() === 'POST');
    await page.locator('#personConfirm').click();
    await selectionResponse;
    assert(corrected && corrected.people[0] === 0);
    const edit = corrected.box_edits[0];
    assert(edit.whole_track && edit.frame === 0 && edit.person === 0);
    [.2 * w, .15 * h, .85 * w, .9 * h].forEach((v, i) => assert(Math.abs(edit.box[i] - v) < 3));
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(root, 'logs/web-image-manual-box-070.png'), fullPage: true });
    console.log('WEB_IMAGE_STATIC_PREVIEW_NO_PLAYER_REDRAW_COORDINATES_AND_SAFE_CONFIRM_OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
