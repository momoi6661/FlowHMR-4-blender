// Prepare static poses through the same video API, without restarting loaded AI.
import { appError } from "./i18n.js";

export const isImageFile = (file) => !!file && (file.type?.startsWith("image/") || /\.(png|jpe?g|jfif|webp|bmp)$/i.test(file.name));
export const isImageJob = (job) => ["image", "video"].includes(job?.source_kind) ? job.source_kind === "image"
  : [job?.source_name, job?.filename].some(name => /\.(png|jpe?g|jfif|webp|bmp|tiff?)(?:\.(webm|mp4))?$/i.test(name ?? ""));
export const sourceName = (job) => job?.source_name || (isImageJob(job) ? job?.filename?.replace(/\.(webm|mp4)$/i, "") : job?.filename);

export function imageSize(width, height, mode = "AUTO") {
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || Math.min(width, height) < 32) throw appError("image.too_small");
  if (width * height > 64_000_000) throw appError("image.too_large");
  if (!["AUTO", "ORIGINAL"].includes(mode)) throw appError("image.invalid_mode");
  const scale = mode === "ORIGINAL" ? 1 : Math.min(1920 / Math.max(width, height), Math.max(1, Math.min(4, 256 / Math.min(width, height))));
  const contentWidth = Math.max(2, Math.round(width * scale)), contentHeight = Math.max(2, Math.round(height * scale));
  return { width: contentWidth + contentWidth % 2, height: contentHeight + contentHeight % 2,
    contentWidth, contentHeight, sourceWidth: width, sourceHeight: height, enlarged: scale > 1, lowResolution: Math.min(width, height) < 256 };
}

export async function loadImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } catch { throw appError("image.decode_failed"); }
  finally { URL.revokeObjectURL(url); }
}

export async function prepareImageVideo(file, mode = "AUTO") {
  if (!globalThis.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) throw appError("image.browser_unsupported");
  const image = await loadImage(file);
  const size = imageSize(image.naturalWidth, image.naturalHeight, mode);
  const canvas = document.createElement("canvas");
  canvas.width = size.width; canvas.height = size.height;
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw appError("image.prepare_failed");
  const draw = () => {
    context.fillStyle = "#f0f0f0";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, size.contentWidth, size.contentHeight);
  };
  draw();
  const mimeType = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find(type => MediaRecorder.isTypeSupported(type));
  if (!mimeType) throw appError("image.browser_unsupported");
  const stream = canvas.captureStream(30);
  let timer, timeout;
  try {
    const chunks = [];
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 12_000_000 });
    await new Promise((resolve, reject) => {
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => reject(appError("image.prepare_failed"));
      recorder.onstop = resolve;
      recorder.start();
      timer = setInterval(draw, 1000 / 30);
      timeout = setTimeout(() => { if (recorder.state !== "inactive") recorder.stop(); }, 1100);
    });
    if (!chunks.length) throw appError("image.prepare_failed");
    return new File(chunks, file.name + ".webm", { type: "video/webm" });
  } catch (error) { throw error.i18n ? error : appError("image.prepare_failed"); }
  finally {
    clearInterval(timer); clearTimeout(timeout);
    for (const track of stream.getTracks()) track.stop();
    canvas.width = canvas.height = 0;
  }
}
