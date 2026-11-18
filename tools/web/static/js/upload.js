// "Video" card: drag & drop / browse, shows the chosen file.
import { $, show, formatBytes } from "./utils.js";
import { localize, message as m, setText } from "./i18n.js";
import { isImageFile, imageSize, loadImage, prepareImageVideo } from "./image-media.js";

const VIDEO_RE = /\.(mp4|mov|avi|mkv|webm|m4v)$/i;

export function initUpload(bus) {
  const dz = $("dropzone");
  const input = $("fileInput");
  let file = null;
  let dimensions = null, version = 0, busy = false;
  const resolution = $("imageResolution");

  function describeImage() {
    if (!dimensions) return;
    const size = imageSize(...dimensions, resolution.value);
    setText($("imageInfo"), m("image.dimensions", { width: dimensions[0], height: dimensions[1],
      outputWidth: size.width, outputHeight: size.height }));
    if (size.lowResolution) setText($("imageInfo"), () => {
      const text = m("image.dimensions", { width: dimensions[0], height: dimensions[1], outputWidth: size.width, outputHeight: size.height });
      return [text, m("image.low_resolution")].map(localize).join(" · ");
    });
  }

  const pick = async (f) => {
    if (!f || busy) return;
    const request = ++version;
    if (!isImageFile(f) && !f.type.startsWith("video/") && !VIDEO_RE.test(f.name)) {
      setError(m("source.format"));
      return;
    }
    let nextDimensions = null;
    try {
      if (isImageFile(f)) {
        const image = await loadImage(f);
        nextDimensions = [image.naturalWidth, image.naturalHeight];
        imageSize(...nextDimensions, resolution.value);
      }
    } catch (error) { if (request === version) setError(error); return; }
    if (request !== version) return;
    setError("");
    file = f;
    dimensions = nextDimensions;
    show("imageOptions", !!dimensions);
    describeImage();
    $("fileName").textContent = f.name;
    $("fileSize").textContent = formatBytes(f.size);
    show("fileRow", true);
    dz.classList.add("compact");
    bus.emit("file:selected", f);
  };

  const clear = () => {
    version++;
    file = null;
    dimensions = null;
    show("imageOptions", false);
    input.value = "";
    show("fileRow", false);
    dz.classList.remove("compact");
    bus.emit("file:cleared");
  };

  const setError = (msg) => {
    setText($("uploadError"), msg);
    show("uploadError", !!msg);
  };

  dz.addEventListener("click", () => { if (!busy) input.click(); });
  dz.addEventListener("keydown", (e) => { if (!busy && (e.key === "Enter" || e.key === " ")) input.click(); });
  input.addEventListener("change", (e) => { pick(e.target.files[0]); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  $("fileRemove").addEventListener("click", (e) => { e.stopPropagation(); clear(); });
  resolution.addEventListener("change", describeImage);

  return { getFile: () => file, clear, setError,
    prepare: (source) => isImageFile(source) ? prepareImageVideo(source, resolution.value) : Promise.resolve(source),
    setBusy(value) {
      busy = value;
      input.disabled = resolution.disabled = $("fileRemove").disabled = value;
      dz.setAttribute("aria-disabled", String(value));
    },
  };
}
