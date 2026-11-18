from pathlib import Path

import cv2
from PIL import Image
import pytest

from flowhmr.utils.runtime_blender_media import prepare_media


def test_image_becomes_decodable_one_second_clip_and_reuses_cache(tmp_path):
    source = tmp_path / "still.png"
    Image.new("RGBA", (127, 65), (240, 80, 30, 120)).save(source)
    info = prepare_media(source, tmp_path / "cache")
    assert info["is_image"] and info["fps"] == 30 and info["frames"] == 30 and info["duration"] == 1
    path = Path(info["path"])
    modified = path.stat().st_mtime_ns
    again = prepare_media(source, tmp_path / "cache")
    assert again == info and path.stat().st_mtime_ns == modified
    capture = cv2.VideoCapture(str(path))
    try:
        decoded, frame = capture.read()
        assert decoded and frame.shape[:2] == (256, 500)
        assert frame.mean() > 50
    finally:
        capture.release()
    # Already prepared videos remain ordinary videos, with no second conversion.
    video = prepare_media(path, tmp_path / "unused")
    assert video["path"] == str(path) and not video["is_image"]


def test_invalid_image_and_missing_source_fail_before_inference(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        prepare_media(tmp_path / "absent.png", tmp_path / "cache")
    source = tmp_path / "broken.png"
    source.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="无法解码"):
        prepare_media(source, tmp_path / "cache")


def test_photo_orientation_resolution_and_cache_recovery(tmp_path):
    source = tmp_path / "手机照片.JPG"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (4000, 2000), (160, 80, 40)).save(source, exif=exif)
    info = prepare_media(source, tmp_path / "cache")
    assert (info["source_width"], info["source_height"]) == (2000, 4000)
    assert (info["width"], info["height"]) == (960, 1920)
    # Corrupt cache entries must not prevent a valid photo being imported again.
    Path(info["path"]).write_bytes(b"incomplete mp4")
    assert prepare_media(source, tmp_path / "cache") == info


def test_small_photo_upscales_without_claiming_new_detail_and_tiny_is_rejected(tmp_path):
    source = tmp_path / "small.png"
    Image.new("RGB", (320, 80)).save(source)
    info = prepare_media(source, tmp_path / "cache")
    assert (info["width"], info["height"]) == (1024, 256)
    assert "不能恢复" in info["warning"] and info["source_width"] == 320
    original = prepare_media(source, tmp_path / "cache", image_resolution="ORIGINAL")
    assert (original["width"], original["height"]) == (320, 80)
    assert original["path"] != info["path"] and original["image_resolution"] == "ORIGINAL"
    Image.new("RGB", (80, 12)).save(source)
    with pytest.raises(ValueError, match="32 像素"):
        prepare_media(source, tmp_path / "cache")


def test_original_photo_dimensions_are_optional_not_forced_to_1920(tmp_path):
    source = tmp_path / "large.jpg"
    Image.new("RGB", (2401, 600)).save(source)
    original = prepare_media(source, tmp_path / "cache", image_resolution="ORIGINAL")
    automatic = prepare_media(source, tmp_path / "cache")
    assert (original["width"], original["height"]) == (2402, 600)
    assert automatic["width"] == 1920


def test_panel_preview_decodes_frames_reuses_cache_and_tracks_source_changes(tmp_path):
    from flowhmr.utils.runtime_blender_media import prepare_panel_preview
    import numpy as np
    source = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 48))
    try:
        for i in range(30):
            writer.write(np.full((48, 64, 3), i * 8, dtype=np.uint8))
    finally:
        writer.release()
    frames = prepare_panel_preview(source, tmp_path / "cache")
    assert len(frames) == 5
    assert frames[0]["seconds"] == 0 and frames[-1]["seconds"] > .9
    assert all(Path(item["path"]).is_file() for item in frames)
    first = cv2.imread(frames[0]["path"])
    last = cv2.imread(frames[-1]["path"])
    assert last.mean() > first.mean() + 100
    assert prepare_panel_preview(source, tmp_path / "cache") == frames
    assert len(prepare_panel_preview(source, tmp_path / "still-cache", still=True)) == 1
    # A cover and sampled frames for the same video must not collide in cache.
    cover = prepare_panel_preview(source, tmp_path / "cache", still=True)
    assert len(cover) == 1
    assert prepare_panel_preview(source, tmp_path / "cache") == frames
