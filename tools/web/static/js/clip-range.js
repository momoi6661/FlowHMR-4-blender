// Seconds are a half-open interval on the backend's resampled 30 FPS timeline.
export function secondsToFrames(start, end) {
  if (!Number.isFinite(start) || start < 0 ||
      (end !== null && (!Number.isFinite(end) || end <= start))) return null;
  const first = Math.max(0, Math.ceil(start * 30 - 1e-7));
  const last = end === null ? null : Math.ceil(end * 30 - 1e-7) - 1;
  if (!Number.isSafeInteger(first) || (last !== null && (!Number.isSafeInteger(last) || last < first))) return null;
  return { start_frame: first, end_frame: last };
}
