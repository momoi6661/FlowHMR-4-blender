"""Real image-history import and target baking, using a varying 30-frame legacy result."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import shutil
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import motion, operators


def one_frame(obj, frame):
    assert obj["flowhmr_frames"] == 1
    assert obj["flowhmr_source_kind"] == "image"
    curves = motion._curves(obj)
    assert len(curves) >= 211
    for curve in curves:
        assert len(curve.keyframe_points) == 1, curve.data_path
        assert abs(curve.keyframe_points[0].co.x - frame) < 1e-5


addon.register()
try:
    scene, context = bpy.context.scene, bpy.context
    s = scene.flowhmr
    s.backend_root = str(ROOT)
    s.import_frame = 17
    s.target_mode = "NEW"
    s.auto_import = False
    job = dict(id="fixture-image", filename="legacy-pose.webp.webm", status="done", progress=100,
               num_frames=30, params={}, track_range=[0, 29])
    fixture = ROOT / ".cache/blender-test/motion.npz"
    video = ROOT / "assets/example/example.mp4"
    media = dict(path=str(video), is_image=False, fps=30, frames=30, duration=1, preview_frames=[])

    def save(route, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture if "blender-motion" in route else video, destination)
        return str(destination)

    def run_immediately(settings, work, done):
        done(settings, work())

    with patch.object(operators, "schedule_poll"), \
         patch.object(operators, "_launch", side_effect=run_immediately), \
         patch.object(operators, "_client", return_value=SimpleNamespace(save=save)):
        operators._adopt_history(s, job, media, scene)
        assert s.source_is_image and s.result_frames == 1
        assert s.actual_range == "图片 · 单帧姿势"
        assert scene.frame_preview_start == scene.frame_preview_end == 17
        operators._start_import(s)
        source = scene.objects[json.loads(s.preview_result_roots)[0]]
        one_frame(source, 17)
        assert scene.frame_preview_start == scene.frame_preview_end == 17
        assert "单帧姿势" in s.message and "播放" not in s.message
        print("LEGACY_IMAGE_HISTORY_IMPORT_WRITES_EXACTLY_ONE_KEY_PER_CHANNEL_OK", flush=True)

        # Baking into an existing character also writes just one frame and
        # preserves the previous Action. No inference, release or live requests.
        target = motion.import_motion(context, fixture, include_mesh=False)[0]
        previous = target.animation_data.action
        s.target_rig = target
        operators._start_import(s)
        one_frame(target, 17)
        assert target.animation_data.action != previous and previous.use_fake_user
        assert "单帧姿势" in s.message
        print("IMAGE_POSE_TARGET_MAPPING_ONE_FRAME_AND_OLD_ACTION_RETAINED_OK", flush=True)

    # A normal video still retains the full motion.
    video_source = motion.import_motion(context, fixture, include_mesh=False)[0]
    assert video_source["flowhmr_frames"] == 30
    assert all(len(curve.keyframe_points) == 30 for curve in motion._curves(video_source))
    print("VIDEO_IMPORT_RETAINS_ALL_30_FRAMES_OK", flush=True)
finally:
    addon.unregister()
print("BLENDER_IMAGE_SINGLE_POSE_SMOKE_OK", flush=True)
