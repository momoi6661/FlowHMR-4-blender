// "Video preview" card: plays the local file before upload / the job video afterwards and
// overlays person boxes on a canvas (all candidates while choosing, the tracked person after).
import { $, show, personColor } from "./utils.js";
import { message as m, setText } from "./i18n.js";
import { isImageFile } from "./image-media.js";

export function initPreview(bus) {
  const video = $("previewVideo");
  const image = $("previewImage");
  const canvas = $("previewCanvas");
  const stage = $("previewBox");
  const ctx = canvas.getContext("2d");
  const noHover = window.matchMedia("(any-hover: none)");
  let localUrl = null;
  let candidates = [];      // [{frames, boxes, ...}] while waiting for a pick
  let selected = new Set(), previewIndex = null;
  let tracks = [];         // {start, end, boxes, person} for each reconstructed track
  let locked = null;        // {candidate, index} once the person is confirmed
  let pickable = false;
  let muteBeforePicking = null, candidateLoop = null, previewPending = false;

  let baseSrc = null;       // source without the retry cache-buster
  let retries = 0;
  let retryTimer = null;
  let drawRequest = 0;
  let mediaRequest = null;
  let originRequest = null;
  let origin = null;
  let imageMode = false, stillCaptured = false;
  let boxEdits = [], editPerson = null, draft = null, drag = null;
  const clock = {
    get ready() { return origin !== null; },
    positionAt: (time, fps = 30) => (time - (origin ?? 0)) * fps,
    frameAt: (time, fps = 30) => Math.floor(clock.positionAt(time, fps) + 1e-4),
    timeOf: (frame, fps = 30) => (origin ?? 0) + frame / fps,
  };

  function syncControls() {
    const visible = !imageMode && editPerson === null && !!baseSrc && (stage.matches(":hover") || video.matches(":focus-visible") ||
      noHover.matches || document.fullscreenElement === video);
    if (video.controls !== visible) video.controls = visible;
  }

  for (const event of ["pointerenter", "pointerleave", "focusin", "focusout"]) {
    stage.addEventListener(event, () => requestAnimationFrame(syncControls));
  }
  noHover.addEventListener("change", syncControls);
  document.addEventListener("fullscreenchange", syncControls);

  function resetClock() {
    if (originRequest !== null) video.cancelVideoFrameCallback(originRequest);
    if (mediaRequest !== null) video.cancelVideoFrameCallback(mediaRequest);
    originRequest = mediaRequest = null;
    origin = null;
  }

  function loadSource(src, retry = false) {
    video.dispatchEvent(new CustomEvent("flowhmr:sourcechanging", { detail: { retry } }));
    resetClock();
    video.src = src;
    video.load();
    if (video.requestVideoFrameCallback) {
      // MP4 audio can start before the first video frame. The first presented frame's
      // timestamp is the origin of the 30 fps track used by OpenCV and the motion model.
      originRequest = video.requestVideoFrameCallback((_, first) => {
        originRequest = null;
        origin = first.mediaTime;
        refreshOverlay();
        video.dispatchEvent(new Event("flowhmr:clockready"));
        if (previewPending) playCandidate(true);
      });
    } else origin = 0;
  }

  function setSource(src, { local = false, still = false } = {}) {
    imageMode = still; stillCaptured = false;
    show(image, still); show(video, !still);
    image.removeAttribute("src");
    if (localUrl && src !== localUrl) { URL.revokeObjectURL(localUrl); localUrl = null; }
    if (local) localUrl = src;
    clearTimeout(retryTimer);
    baseSrc = src;
    retries = 0;
    stage.style.removeProperty("--video-ratio");
    showPreviewIssue("");
    loadSource(src);
    show("previewBox", true);
    syncControls();
  }

  // A job video can fail to load if it was requested while the server was still writing
  // it; retry a few times with a cache-buster so the browser does not reuse the bad copy.
  video.addEventListener("error", () => {
    if (!baseSrc) return;
    if (baseSrc === localUrl || retries >= 10) {
      showPreviewIssue(m(baseSrc === localUrl ? "source.preview_convert" : "source.preview_retry"));
      return;
    }
    retries += 1;
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryPlayback();
    }, 1500);
  });
  video.addEventListener("loadedmetadata", () => {
    if (video.videoWidth && video.videoHeight) stage.style.setProperty("--video-ratio", video.videoWidth / video.videoHeight);
  });
  video.addEventListener("loadeddata", () => {
    retries = 0; showPreviewIssue("");
    if (imageMode && !stillCaptured && video.videoWidth && video.videoHeight) {
      const surface = document.createElement("canvas");
      surface.width = video.videoWidth; surface.height = video.videoHeight;
      surface.getContext("2d").drawImage(video, 0, 0);
      image.src = surface.toDataURL("image/jpeg", 0.95);
      stillCaptured = true;
      video.pause();
      surface.width = surface.height = 0;
    }
    if (previewPending) playCandidate(true);
  });

  function showPreviewIssue(message) {
    setText($("previewIssue"), message);
    show("previewIssue", !!message);
  }

  function retryPlayback() {
    if (!baseSrc) return false;
    clearTimeout(retryTimer);
    const src = baseSrc === localUrl ? baseSrc
      : `${baseSrc}${baseSrc.includes("?") ? "&" : "?"}r=${Date.now()}`;
    loadSource(src, true);
    return true;
  }

  function clear() {
    if (localUrl) { URL.revokeObjectURL(localUrl); localUrl = null; }
    clearTimeout(retryTimer);
    baseSrc = null;
    imageMode = false; stillCaptured = false;
    boxEdits = []; editPerson = null; cancelDrag(); syncEditHint();
    stopCandidatePreview();
    resetClock();
    video.removeAttribute("src");
    video.load();
    image.removeAttribute("src");
    show(image, false); show(video, true);
    candidates = []; tracks = []; selected.clear(); previewIndex = null; locked = null;
    show("previewBox", false);
    syncControls();
    showPreviewIssue("");
    setText($("previewTitle"), "");
    refreshOverlay();
  }

  // Box of a candidate at a (30 fps) frame, linearly interpolated between its detections.
  function boxAt(c, f) {
    const anchors = boxEdits.filter(e => e.person === c.index).sort((a, b) => a.frame - b.frame);
    const whole = anchors.findLast(e => e.whole_track);
    if (whole) return whole.box;
    if (anchors.length && f >= anchors[0].frame && f <= anchors.at(-1).frame) {
      const hi = anchors.findIndex(e => e.frame >= f);
      if (!hi) return anchors[0].box;
      const a = anchors[hi - 1], b = anchors[hi], t = (f - a.frame) / Math.max(1, b.frame - a.frame);
      return a.box.map((v, i) => v + (b.box[i] - v) * t);
    }
    const fr = c.frames;
    if (!fr || !fr.length || f < fr[0] - 2 || f > fr[fr.length - 1] + 2) return null;
    if (f <= fr[0]) return c.boxes[0];
    if (f >= fr[fr.length - 1]) return c.boxes[fr.length - 1];
    let lo = 0, hi = fr.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (fr[mid] === f) return c.boxes[mid];
      if (fr[mid] < f) lo = mid + 1; else hi = mid - 1;
    }
    const a = Math.max(0, hi), b = Math.min(fr.length - 1, lo);
    if (a === b) return c.boxes[a];
    const t = (f - fr[a]) / (fr[b] - fr[a]);
    return c.boxes[a].map((v, k) => v + (c.boxes[b][k] - v) * t);
  }

  // Map video pixels to CSS coordinates, accounting for object-fit: contain.
  function layout() {
    const media = imageMode ? image : video;
    const cw = media.clientWidth, ch = media.clientHeight;
    const vw = imageMode ? image.naturalWidth : video.videoWidth, vh = imageMode ? image.naturalHeight : video.videoHeight;
    if (!vw || !vh || !cw || !ch) return null;
    const s = Math.min(cw / vw, ch / vh);
    return { s, ox: (cw - vw * s) / 2, oy: (ch - vh * s) / 2, cw, ch };
  }

  function drawBox(L, box, color, label, strong) {
    const [x1, y1, x2, y2] = box;
    const rx = x1 * L.s + L.ox, ry = y1 * L.s + L.oy, rw = (x2 - x1) * L.s, rh = (y2 - y1) * L.s;
    ctx.strokeStyle = color;
    ctx.lineWidth = strong ? 3 : 2;
    ctx.setLineDash(strong ? [] : [6, 4]);
    ctx.strokeRect(rx, ry, rw, rh);
    ctx.setLineDash([]);
    if (label) {
      ctx.font = "600 12px Inter, -apple-system, sans-serif";
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = color;
      ctx.fillRect(rx - (strong ? 1.5 : 1), Math.max(0, ry - 20), tw, 20);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, rx + 4, Math.max(14, ry - 6));
    }
  }

  function drawHandles(L, box) {
    const [x1, y1, x2, y2] = box;
    const xs = [x1, (x1 + x2) / 2, x2], ys = [y1, (y1 + y2) / 2, y2];
    ctx.fillStyle = "#fff";
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 2;
    xs.forEach((x, i) => ys.forEach((y, j) => {
      if (i === 1 && j === 1) return;
      const left = x * L.s + L.ox - 4, top = y * L.s + L.oy - 4;
      ctx.fillRect(left, top, 8, 8);
      ctx.strokeRect(left, top, 8, 8);
    }));
  }

  function draw() {
    drawRequest = 0;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    const L = layout();
    if (!L) return;
    const f = imageMode ? 0 : Math.max(0, clock.frameAt(video.currentTime));
    if (candidates.length) {
      candidates.forEach((c, i) => {
        const b = i === editPerson && draft ? draft : boxAt(c, f);
        if (b) {
          const checked = selected.has(i);
          drawBox(L, b, i === editPerson ? "#60a5fa" : checked ? "#10b981" : personColor(i),
            `#${i + 1}${checked ? " ✓" : ""}`, checked || i === editPerson);
        }
      });
    } else if (tracks.length) {
      for (const track of tracks) {
        if (f < track.start || f > track.end) continue;
        const b = track.boxes[f - track.start];
        if (b) drawBox(L, b, track.person ? personColor(track.person - 1) : "#10b981",
          track.person ? `#${track.person}` : locked ? `#${locked.index + 1} ✓` : null, true);
      }
    } else if (locked && locked.candidate) {
      const b = boxAt(locked.candidate, f);
      if (b) drawBox(L, b, "#10b981", `#${locked.index + 1} ✓`, true);
    }
    if (editPerson !== null && candidates[editPerson]) {
      const box = draft || boxAt(candidates[editPerson], f);
      if (box) drawHandles(L, box);
    }
  }

  function refreshOverlay() {
    if (!drawRequest) drawRequest = requestAnimationFrame(draw);
    const tracking = candidates.length > 0 || tracks.length > 0 || locked;
    if (tracking && !video.paused && video.requestVideoFrameCallback) {
      if (mediaRequest === null) mediaRequest = video.requestVideoFrameCallback(() => {
        mediaRequest = null;
        loopCandidate();
        refreshOverlay();
      });
    } else if (mediaRequest !== null) {
      video.cancelVideoFrameCallback(mediaRequest);
      mediaRequest = null;
    }
  }

  function playCandidate(seekStart = false) {
    if (imageMode || editPerson !== null) return;
    const candidate = candidates[previewIndex];
    if (!pickable || !candidate) return;
    previewPending = !clock.ready || video.readyState < 2;
    if (previewPending) return;
    candidateLoop = [candidate.preview_start ?? candidate.start, candidate.preview_end ?? candidate.end];
    const frame = clock.frameAt(video.currentTime);
    if (seekStart || frame < candidateLoop[0] || frame > candidateLoop[1]) {
      video.currentTime = clock.timeOf(candidateLoop[0] + 0.5);
    }
    if (muteBeforePicking === null) {
      muteBeforePicking = video.muted;
      video.muted = true;
    }
    video.play().catch(() => {});
  }

  function loopCandidate() {
    if (!candidateLoop || !clock.ready || video.seeking) return;
    if (video.ended || (!video.paused && clock.frameAt(video.currentTime) > candidateLoop[1])) {
      video.currentTime = clock.timeOf(candidateLoop[0] + 0.5);
      if (video.paused) video.play().catch(() => {});
    }
  }

  function stopCandidatePreview() {
    previewPending = false;
    candidateLoop = null;
    if (muteBeforePicking !== null) {
      video.pause();
      video.muted = muteBeforePicking;
      muteBeforePicking = null;
    }
  }
  video.addEventListener("timeupdate", loopCandidate);
  video.addEventListener("ended", loopCandidate);
  for (const event of ["loadeddata", "emptied", "seeked", "timeupdate", "play", "pause"]) {
    video.addEventListener(event, refreshOverlay);
  }
  new ResizeObserver(refreshOverlay).observe(video);
  new ResizeObserver(refreshOverlay).observe(image);
  image.addEventListener("load", refreshOverlay);

  // Click on a box to pick that person.
  canvas.addEventListener("click", (e) => {
    if (editPerson !== null || suppressClick) { suppressClick = false; return; }
    if (!pickable || !candidates.length) return;
    const L = layout();
    if (!L) return;
    const r = canvas.getBoundingClientRect();
    const x = (e.clientX - r.left - L.ox) / L.s, y = (e.clientY - r.top - L.oy) / L.s;
    const f = imageMode ? 0 : Math.max(0, clock.frameAt(video.currentTime));
    let best = null, bestArea = Infinity;
    candidates.forEach((c, i) => {
      const b = boxAt(c, f);
      if (!b || x < b[0] || x > b[2] || y < b[1] || y > b[3]) return;
      const area = (b[2] - b[0]) * (b[3] - b[1]);
      if (area < bestArea) { best = i; bestArea = area; }
    });
    if (best !== null) bus.emit("preview:pick", best);
  });

  let suppressClick = false;
  const boxHint = $("boxEditHint");
  function syncEditHint() {
    setText(boxHint, editPerson !== null ? m(imageMode ? "box.image_hint" : "box.video_hint")
      : boxEdits.length ? m("box.pending", { count: boxEdits.length }) : "");
    show(boxHint, editPerson !== null || boxEdits.length > 0);
    canvas.classList.toggle("editing-box", editPerson !== null);
    canvas.style.removeProperty("cursor");
    syncControls();
  }
  function point(event, clamp = true) {
    const L = layout();
    if (!L) return null;
    const r = canvas.getBoundingClientRect();
    const maxX = imageMode ? image.naturalWidth : video.videoWidth;
    const maxY = imageMode ? image.naturalHeight : video.videoHeight;
    const x = (event.clientX - r.left - L.ox) / L.s, y = (event.clientY - r.top - L.oy) / L.s;
    if (!clamp && (x < 0 || x > maxX || y < 0 || y > maxY)) return null;
    return [Math.max(0, Math.min(maxX, x)), Math.max(0, Math.min(maxY, y))];
  }
  function editFrame() {
    return imageMode ? 0 : Math.max(0, clock.frameAt(video.currentTime));
  }

  function editBoxIndex(person, frame) {
    // Image edits are whole-track anchors. Video edits are exact frame anchors;
    // the rest of the track keeps the detector result or interpolates anchors.
    return boxEdits.findIndex((e) => e.person === person &&
      (imageMode ? e.whole_track : e.frame === frame));
  }

  function clampBox(box) {
    const maxX = imageMode ? image.naturalWidth : video.videoWidth;
    const maxY = imageMode ? image.naturalHeight : video.videoHeight;
    const x1 = Math.max(0, Math.min(maxX, box[0]));
    const y1 = Math.max(0, Math.min(maxY, box[1]));
    const x2 = Math.max(0, Math.min(maxX, box[2]));
    const y2 = Math.max(0, Math.min(maxY, box[3]));
    return [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
  }

  function handleAt(xy, box) {
    if (!box) return null;
    const L = layout();
    const tolerance = 10 / Math.max(L?.s || 1, 1e-6);
    const [x1, y1, x2, y2] = box;
    const nearX1 = Math.abs(xy[0] - x1) <= tolerance;
    const nearX2 = Math.abs(xy[0] - x2) <= tolerance;
    const nearY1 = Math.abs(xy[1] - y1) <= tolerance;
    const nearY2 = Math.abs(xy[1] - y2) <= tolerance;
    if (nearX1 && nearY1) return "nw";
    if (nearX2 && nearY1) return "ne";
    if (nearX2 && nearY2) return "se";
    if (nearX1 && nearY2) return "sw";
    if (Math.abs(xy[0] - (x1 + x2) / 2) <= tolerance && nearY1) return "n";
    if (Math.abs(xy[0] - (x1 + x2) / 2) <= tolerance && nearY2) return "s";
    if (Math.abs(xy[1] - (y1 + y2) / 2) <= tolerance && nearX1) return "w";
    if (Math.abs(xy[1] - (y1 + y2) / 2) <= tolerance && nearX2) return "e";
    if (nearY1 && xy[0] >= x1 && xy[0] <= x2) return "n";
    if (nearX2 && xy[1] >= y1 && xy[1] <= y2) return "e";
    if (nearY2 && xy[0] >= x1 && xy[0] <= x2) return "s";
    if (nearX1 && xy[1] >= y1 && xy[1] <= y2) return "w";
    if (xy[0] >= x1 && xy[0] <= x2 && xy[1] >= y1 && xy[1] <= y2) return "move";
    return null;
  }

  function applyDrag(start, xy, mode) {
    const [x1, y1, x2, y2] = start;
    if (mode === "move") {
      const dx = xy[0] - drag.xy[0], dy = xy[1] - drag.xy[1];
      const w = x2 - x1, h = y2 - y1;
      const maxX = imageMode ? image.naturalWidth : video.videoWidth;
      const maxY = imageMode ? image.naturalHeight : video.videoHeight;
      const nx = Math.max(0, Math.min(maxX - w, x1 + dx));
      const ny = Math.max(0, Math.min(maxY - h, y1 + dy));
      return [nx, ny, nx + w, ny + h];
    }
    if (mode === "draw") return clampBox([drag.xy[0], drag.xy[1], xy[0], xy[1]]);
    const next = [x1, y1, x2, y2];
    const minSize = Math.max(4, 4 / Math.max(layout()?.s || 1, 1e-6));
    if (mode.includes("w")) next[0] = Math.min(xy[0], x2 - minSize);
    if (mode.includes("e")) next[2] = Math.max(xy[0], x1 + minSize);
    if (mode.includes("n")) next[1] = Math.min(xy[1], y2 - minSize);
    if (mode.includes("s")) next[3] = Math.max(xy[1], y1 + minSize);
    return clampBox(next);
  }

  function updateCursor(event) {
    if (editPerson === null) return;
    const xy = point(event, false), candidate = candidates[editPerson];
    const mode = drag?.mode || (xy && candidate ? handleAt(xy, boxAt(candidate, editFrame())) : null);
    const cursors = { move: drag ? "grabbing" : "grab", nw: "nwse-resize", se: "nwse-resize",
      ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };
    canvas.style.cursor = cursors[mode] || (xy ? "crosshair" : "default");
  }

  canvas.addEventListener("pointerdown", event => {
    // Ignore only the click from the cancelled sequence, never a fresh click.
    if (event.button === 0 && !drag) suppressClick = false;
    if (event.button !== 0 || editPerson === null || !pickable) return;
    const xy = point(event, false);
    const candidate = candidates[editPerson];
    if (!xy || !candidate || drag) return;
    event.preventDefault();
    video.pause();
    const frame = editFrame();
    if (frame < candidate.start || frame > candidate.end) return;
    const current = boxAt(candidate, frame);
    const mode = handleAt(xy, current) || "draw";
    drag = { pointer: event.pointerId, xy, frame, mode, moved: false, start: current?.slice() || [...xy, ...xy] };
    draft = mode === "draw" ? [...xy, ...xy] : drag.start.slice();
    try { canvas.setPointerCapture(event.pointerId); } catch (_) { /* Safari/embedded views */ }
    updateCursor(event);
    refreshOverlay();
  });
  canvas.addEventListener("pointermove", event => {
    updateCursor(event);
    if (!drag || drag.pointer !== event.pointerId) return;
    const xy = point(event);
    if (!xy) return;
    drag.moved ||= Math.hypot(xy[0] - drag.xy[0], xy[1] - drag.xy[1]) * layout().s >= 2;
    draft = applyDrag(drag.start, xy, drag.mode);
    refreshOverlay();
  });
  function cancelDrag() {
    const pointer = drag?.pointer;
    // A cancelled pointer sequence can still synthesize a click on mouseup.
    // Do not let that click select a person in a newly loaded job.
    if (drag) suppressClick = true;
    drag = draft = null;
    if (pointer !== undefined && canvas.hasPointerCapture(pointer)) canvas.releasePointerCapture(pointer);
    refreshOverlay();
  }
  canvas.addEventListener("pointercancel", event => { if (drag?.pointer === event.pointerId) cancelDrag(); });
  canvas.addEventListener("lostpointercapture", event => { if (drag?.pointer === event.pointerId) cancelDrag(); });
  video.addEventListener("seeking", cancelDrag);
  canvas.addEventListener("pointerup", event => {
    if (!drag || drag.pointer !== event.pointerId) return;
    suppressClick = true;
    const candidate = candidates[editPerson];
    const xy = point(event);
    if (xy) {
      drag.moved ||= Math.hypot(xy[0] - drag.xy[0], xy[1] - drag.xy[1]) * layout().s >= 2;
      draft = applyDrag(drag.start, xy, drag.mode);
    }
    try {
      if (drag.moved && candidate && draft && draft[2] - draft[0] >= 4 && draft[3] - draft[1] >= 4 &&
          drag.frame >= candidate.start && drag.frame <= candidate.end) {
        const index = editBoxIndex(editPerson, drag.frame);
        if (index >= 0) boxEdits.splice(index, 1);
        boxEdits.push({ person: editPerson, frame: drag.frame, box: draft.slice(), whole_track: imageMode });
        selected.add(editPerson);
        bus.emit("preview:box-edits", { edits: boxEdits, person: editPerson });
      }
    } finally {
      // Keep editPerson set. The next drag can move, resize, or redraw the same
      // box without requiring another click on “修正框”.
      cancelDrag(); syncEditHint(); updateCursor(event);
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && editPerson !== null) { editPerson = null; cancelDrag(); syncEditHint(); }
  });
  bus.on("person:edit-box", index => {
    if (!pickable || !candidates[index]) return;
    stopCandidatePreview(); video.pause();
    cancelDrag(); editPerson = index;
    const candidate = candidates[index], frame = editFrame();
    if (!imageMode && (frame < candidate.start || frame > candidate.end)) video.currentTime = clock.timeOf(candidate.start + 0.5);
    syncEditHint(); refreshOverlay();
  });
  bus.on("person:reset-boxes", () => {
    boxEdits = []; editPerson = null; cancelDrag(); syncEditHint();
  });

  bus.on("person:candidates", ({ candidates: c, selected: s, previewIndex: i }) => {
    cancelDrag(); boxEdits = []; editPerson = null;
    candidates = c || [];
    selected = new Set(s);
    previewIndex = i;
    locked = null;
    pickable = candidates.length > 0;
    canvas.classList.toggle("pickable", pickable);
    if (pickable) playCandidate(true); else stopCandidatePreview();
    syncEditHint();
    refreshOverlay();
  });
  bus.on("person:selection", ({ selected: s, previewIndex: i }) => {
    selected = new Set(s);
    previewIndex = i;
    playCandidate();
    refreshOverlay();
  });
  bus.on("person:locked", (l) => {
    editPerson = null; cancelDrag(); syncEditHint();
    locked = l;
    candidates = [];
    selected.clear();
    previewIndex = null;
    pickable = false;
    stopCandidatePreview();
    canvas.classList.remove("pickable");
    refreshOverlay();
  });

  return {
    showLocal(file) {
      tracks = [];
      if (isImageFile(file)) {
        clear();
        imageMode = true;
        localUrl = URL.createObjectURL(file);
        image.src = localUrl;
        show(video, false); show(image, true); show("previewBox", true);
      } else setSource(URL.createObjectURL(file), { local: true });
      setText($("previewTitle"), file.name);
    },
    showJob(url, name, still = false) {
      if (baseSrc !== url || still !== imageMode) setSource(url, { still });
      setText($("previewTitle"), name);
    },
    setTrack(t) { tracks = t ? [t] : []; refreshOverlay(); },
    setTracks(items) { tracks = items; refreshOverlay(); },
    clear,
    video,
    clock,
    get isImage() { return imageMode; },
    retryPlayback,
    showPreviewIssue,
    get temporaryMute() { return muteBeforePicking !== null; },
  };
}
