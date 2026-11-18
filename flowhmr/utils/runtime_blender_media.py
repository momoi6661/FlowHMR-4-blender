"""Prepare a still image for the existing video inference pipeline."""

import hashlib
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_IMAGE_SIDE = 1920
MAX_IMAGE_PIXELS = 64_000_000


def _prepare_image(source, cache, image_resolution):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    # Bound decoding before allocating RGBA copies of phone/scanner photographs.
    try:
        with Image.open(source) as original:
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片超过 6400 万像素，请先缩小后导入")
            if min(original.size) < 32:
                raise ValueError("图片短边不足 32 像素，无法可靠识别人，请换一张更清晰的图片")
            image = ImageOps.exif_transpose(original)
            source_width, source_height = image.size
            scale = (min(MAX_IMAGE_SIDE / max(image.size), max(1., min(4., 256 / min(image.size))))
                     if image_resolution == "AUTO" else 1.)
            size = tuple(max(2, round(value * scale)) for value in image.size)
            if size != image.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            # Transparency and EXIF are handled before encoding; do not stretch
            # or crop the person. Only pad a single pixel for odd codec sizes.
            image = image.convert("RGBA")
            background = Image.new("RGBA", image.size, (240, 240, 240, 255))
            background.alpha_composite(image)
            frame = cv2.cvtColor(np.asarray(background.convert("RGB")), cv2.COLOR_RGB2BGR)
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("无法解码图片，请换用有效的 PNG、JPG 或 WebP 文件") from error
    warnings = []
    if scale > 1:
        warnings.append("小图已等比放大；放大不能恢复模糊的手指或面部细节")
    if min(source_width, source_height) < 256:
        warnings.append("原图分辨率偏低，建议使用人物完整、手部清晰的原图")
    height, width = frame.shape[:2]
    frame = cv2.copyMakeBorder(frame, 0, height % 2, 0, width % 2, cv2.BORDER_REPLICATE)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"image-v4:{image_resolution}:1920:256:4".encode())
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    path = cache / (digest.hexdigest() + ".mp4")
    # Rebuild invalid/partial cache entries left by an interrupted import.
    capture = cv2.VideoCapture(str(path)) if path.is_file() else None
    try:
        cached = capture is not None and capture.isOpened() and capture.read()[0]
    finally:
        if capture is not None:
            capture.release()
    if not cached:
        temporary = path.with_suffix(".partial.mp4")
        writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"mp4v"), 30,
                                 (frame.shape[1], frame.shape[0]))
        try:
            if not writer.isOpened():
                raise ValueError("无法准备图片素材，请检查后端视频组件")
            for _ in range(30):
                writer.write(frame)
        finally:
            writer.release()
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path, dict(source_width=source_width, source_height=source_height, warning="；".join(warnings),
                      image_resolution=image_resolution)


def prepare_media(source, cache, *, image_resolution="AUTO"):
    import cv2

    if image_resolution not in {"AUTO", "ORIGINAL"}:
        raise ValueError("请选择自动适配或保留原图尺寸")

    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError("素材文件不存在")
    is_image = source.suffix.lower() in IMAGE_SUFFIXES
    path = source
    metadata = {}
    if is_image:
        path, metadata = _prepare_image(source, cache, image_resolution)
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("无法打开素材，请选择有效的视频或图片")
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or frames < 1:
            raise ValueError("素材中没有可解码的画面")
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return dict(path=str(path), is_image=is_image, fps=fps, frames=frames, duration=frames / fps,
                    width=width, height=height, **metadata)
    finally:
        cap.release()


def prepare_panel_preview(path, cache, *, still=False):
    """Small cached frames for the add-on panel; never load an inference model."""
    import json
    import cv2
    path = Path(path).resolve()
    stat = path.stat()
    digest = hashlib.sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}:panel-v2:{still}".encode()).hexdigest()
    folder = Path(cache) / "panel" / digest
    manifest = folder / "frames.json"
    if manifest.is_file():
        items = json.loads(manifest.read_text(encoding="utf-8"))
        if items and all(Path(item["path"]).is_file() for item in items):
            return items
    folder.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total < 1:
            raise ValueError("无法读取面板预览")
        count = 1 if still else min(90, total, max(2, int(total / fps * 5)))
        indices = [round(i * (total - 1) / max(1, count - 1)) for i in range(count)]
        items = []
        for i, index in enumerate(indices):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            scale = min(1, 480 / max(w, h))
            frame = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))))
            target = folder / f"{i:03}.jpg"
            # imencode/tofile also supports non-ASCII Windows paths.
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if ok:
                encoded.tofile(str(target))
                items.append(dict(path=str(target), seconds=index / fps))
        if not items:
            raise ValueError("素材没有可预览的帧")
        manifest.write_text(json.dumps(items), encoding="utf-8")
        return items
    finally:
        capture.release()
