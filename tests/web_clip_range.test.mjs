import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../tools/web/static/js/clip-range.js", import.meta.url), "utf8");
const { secondsToFrames } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("seconds select the same inclusive frames sent to the backend", () => {
  assert.deepEqual(secondsToFrames(1, 3), { start_frame: 30, end_frame: 89 });
  assert.deepEqual(secondsToFrames(0, null), { start_frame: 0, end_frame: null });
  assert.deepEqual(secondsToFrames(1.01, 1.1), { start_frame: 31, end_frame: 32 });
});

test("invalid and empty time ranges cannot create a generation job", () => {
  for (const times of [[-1, 2], [2, 2], [3, 2], [NaN, 3], [0, Infinity],
                       [0.001, 0.002], [1e20, null]]) {
    assert.equal(secondsToFrames(...times), null, String(times));
  }
});

test("frame to seconds to frame round trips never add a neighboring frame", () => {
  for (let frame = 0; frame < 1000; frame++) {
    const start = Number((frame / 30).toFixed(9));
    const end = Number(((frame + 1) / 30).toFixed(9));
    assert.deepEqual(secondsToFrames(start, end), { start_frame: frame, end_frame: frame });
  }
});
