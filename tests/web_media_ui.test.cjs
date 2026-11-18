// Run against a local source server in an isolated headless browser. Upload and
// release calls are intercepted; the user's models and jobs remain untouched.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.FLOWHMR_CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe",
    args: ["--disable-gpu", "--enable-unsafe-swiftshader"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    let upload = null, releases = 0, released = false;
    await page.route("**/api/jobs?**", route => route.fulfill({ json: { jobs: [], next_cursor: null } }));
    await page.route("**/api/upload", async route => {
      const request = route.request();
      upload = request.postDataBuffer();
      const boundary = request.headers()["content-type"].split("boundary=")[1];
      const marker = Buffer.from("Content-Type: video/webm\r\n\r\n");
      const first = upload.indexOf(marker);
      assert(first >= 0, "An actual encoded video must be uploaded");
      const start = first + marker.length;
      const end = upload.indexOf(Buffer.from("\r\n--" + boundary), start);
      assert(end > start);
      fs.writeFileSync(path.join(root, ".cache/blender-test/browser-image.webm"), upload.subarray(start, end));
      await route.fulfill({ status: 400, json: { error: "Fixture upload intercepted" } });
    });
    await page.route("**/api/gpu/release", async route => {
      releases++; released = true;
      await route.fulfill({ json: { ok: true, models_loaded: false, memory: {} } });
    });
    await page.route("**/api/info", async route => {
      const response = await route.fetch();
      const info = await response.json();
      info.models_loaded = !released;
      await route.fulfill({ json: info });
    });
    await page.goto(process.env.FLOWHMR_URL || "http://127.0.0.1:60550/", { waitUntil: "networkidle" });
    await page.waitForFunction(() => !document.getElementById("btnReleaseGpu").disabled);
    assert((await page.locator("#fileInput").getAttribute("accept")).includes("image/png"));
    await page.locator("#fileInput").setInputFiles(path.join(root, ".cache/blender-test/native-library/姿势.png"));
    await page.waitForFunction(() => !document.getElementById("imageOptions").classList.contains("hidden"));
    assert(await page.locator("#previewImage").isVisible());
    assert(!(await page.locator("#sourceClip").isVisible()));
    assert((await page.locator("#imageInfo").textContent()).includes("256 × 256"));
    await page.locator("#imageResolution").selectOption("ORIGINAL");
    assert((await page.locator("#imageInfo").textContent()).includes("识别 128 × 128"));
    // The same one-file input also accepts and restores ordinary video controls.
    await page.locator("#fileInput").setInputFiles(path.join(root, "assets/example/example.mp4"));
    await page.waitForFunction(() => document.getElementById("fileName").textContent === "example.mp4");
    assert(await page.locator("#sourceClip").isVisible());
    assert(!(await page.locator("#imageOptions").isVisible()));
    await page.locator("#fileInput").setInputFiles(path.join(root, ".cache/blender-test/web-image-fixture.png"));
    await page.waitForFunction(() => document.getElementById("fileName").textContent === "web-image-fixture.png");
    assert((await page.locator("#btnSubmit").textContent()).includes("提取图片姿势"));
    // Single-person images use the same persistent, optional selection setting.
    await page.locator('#optManual').click();
    assert.equal(await page.locator('#optManual').getAttribute('aria-checked'), 'false');
    await page.locator("#btnSubmit").click();
    await page.waitForFunction(() => !document.getElementById("fileInput").disabled);
    assert(upload, "Image submission must reach the shared upload API");
    const fields = upload.toString("utf8");
    assert(fields.includes("web-image-fixture.png.webm"));
    assert(/name="start_frame"\r\n\r\n0\r\n/.test(fields));
    assert(/name="end_frame"\r\n\r\n0\r\n/.test(fields));
    assert(/name="manual_select"\r\n\r\nfalse\r\n/.test(fields));
    await page.locator('#optManual').click();
    upload = null;
    await page.locator('#btnSubmit').click();
    await page.waitForFunction(() => !document.getElementById('fileInput').disabled);
    assert(upload && /name="manual_select"\r\n\r\ntrue\r\n/.test(upload.toString('utf8')));
    await page.locator("#btnReleaseGpu").click();
    await page.waitForFunction(() => document.getElementById("gpuMessage").textContent.includes("已释放"));
    assert.equal(releases, 1);
    assert(await page.locator("#btnReleaseGpu").isDisabled());
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(root, "logs/web-media-original-release.png"), fullPage: true });
    console.log("WEB_UNIFIED_IMAGE_VIDEO_PREVIEW_ORIGINAL_SIZE_ENCODE_UPLOAD_MANUAL_RELEASE_OK");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
