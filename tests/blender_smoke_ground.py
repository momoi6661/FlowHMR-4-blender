"""Contact-mode transport and real world-space jump/flight baking in Blender."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import sys

import bpy
from mathutils import Matrix, Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import motion, operators, preview


addon.register()
try:
    scene, context = bpy.context.scene, bpy.context
    s = scene.flowhmr
    assert s.contact_mode == "AUTO" and s.postprocess and s.vertical_motion
    s.contact_mode = "AIRBORNE"
    assert not s.postprocess
    s.postprocess = True  # Old files and restored history use this existing field.
    assert s.contact_mode == "AUTO"

    # Execute the real submit operator with an intercepted client. No inference,
    # process starts, model releases or changes to the user's backend.
    s.video_path = str(ROOT / "assets/example/example.mp4")
    s.to_video_end, s.start_time, s.end_time = False, 0, 1
    s.auto_start_backend = False
    s.auto_floor, s.floor_height = False, -0.4
    uploaded = []
    client = SimpleNamespace(
        info=lambda: dict(runtime=dict(ready=True), model_files=dict(items=[]), postprocess_available=True),
        upload_video=lambda path, **options: uploaded.append(options) or "fixture-job",
    )
    def run_immediately(settings, work, done):
        done(settings, work())
    with patch.object(operators, "_client", return_value=client), \
         patch.object(operators, "_launch", side_effect=run_immediately), \
         patch.object(operators, "schedule_poll"):
        for mode in ("AUTO", "AIRBORNE"):
            s.status = "idle"
            s.contact_mode = mode
            assert bpy.ops.flowhmr.submit() == {"FINISHED"}, s.message
    assert uploaded[0]["postprocess"] and uploaded[0]["correction_floor_m"] == s.floor_height
    assert uploaded[1]["postprocess"] is False
    assert all(not options["release_after_job"] for options in uploaded)
    print("GROUND_MODES_REAL_SUBMIT_TRANSPORT_AND_KEEP_MODELS_OK", flush=True)

    # A numeric airborne take: it starts 2 m up, rises 80 cm, then lands back
    # at its original height. The fixture is not a claim of AI video quality.
    with np.load(ROOT / ".cache/blender-test/motion.npz", allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files}
    n = data["trans_0"].shape[0]
    phase = np.linspace(0, 1, n)
    data["trans_0"][:, 1] = 2 + 0.8 * np.sin(np.pi * phase)
    fixture = ROOT / ".cache/blender-test/airborne.npz"
    np.savez_compressed(fixture, **data)
    scene.render.fps = 30
    source = motion.import_motion(context, fixture, include_mesh=True, height_offset_m=1.25)[0]
    assert abs(source.location.z - 1.25) < 1e-6
    s.preview_result_roots = json.dumps([source.name])

    def make_target(name):
        obj = bpy.data.objects.new(name, source.data.copy())
        scene.collection.objects.link(obj)
        # A tilted, translated target detects accidental filtering on local Z.
        obj.matrix_world = Matrix.Translation((1, -2, .4)) @ Matrix.Rotation(.3, 4, "X") @ Matrix.Rotation(.7, 4, "Z")
        return obj

    def position(obj, index):
        frame = source["flowhmr_frame_start"] + index * source["flowhmr_frame_step"]
        scene.frame_set(int(frame), subframe=frame - int(frame))
        return obj.matrix_world @ obj.pose.bones["pelvis"].matrix.translation

    for horizontal, vertical, offset in ((True, True, 0), (False, True, .65), (True, False, 0), (False, False, .3)):
        target = make_target(f"Jump-{horizontal}-{vertical}")
        s.target_rig = target
        s.root_motion, s.vertical_motion, s.motion_height_offset = horizontal, vertical, offset
        context.view_layer.objects.active = source
        assert bpy.ops.flowhmr.bake_selected() == {"FINISHED"}, s.message
        action = target.animation_data.action
        assert action["flowhmr_vertical_motion"] == vertical
        rest = target.matrix_world @ target.data.bones["pelvis"].head_local
        first = position(target, 0)
        assert (first - rest - Vector((0, 0, offset))).length < 3e-5, (first, rest, offset)
        for index in (n // 2, n - 1):
            initial = data["trans_0"][0]
            delta = motion._C @ Vector(data["trans_0"][index] - initial)
            if not horizontal:
                delta.x = delta.y = 0
            if not vertical:
                delta.z = 0
            actual = position(target, index) - first
            assert (actual - delta).length < 3e-5, (horizontal, vertical, index, actual, delta)
    print("IN_PLACE_JUMP_WORLD_AXES_HEIGHT_OFFSET_AND_FROZEN_HEIGHT_OK", flush=True)

    # Reference ground has no effect on curves or poses, including unusual units.
    before = [[tuple(key.co) for key in curve.keyframe_points] for curve in motion._curves(source)]
    scene.unit_settings.scale_length = .01
    s.preview_ground_height = .75
    assert all(abs(vertex[2] - 75) < 1e-5 for vertex in preview.ground_lines(scene))
    after = [[tuple(key.co) for key in curve.keyframe_points] for curve in motion._curves(source)]
    assert before == after
    scene.unit_settings.scale_length = 1
    scene.frame_set(1)
    reference_z = (source.matrix_world @ source.pose.bones["pelvis"].matrix.translation).z
    scene.frame_set(1 + n // 2)
    jump_z = (source.matrix_world @ source.pose.bones["pelvis"].matrix.translation).z - reference_z
    bpy.context.view_layer.objects.active = source.children[0]
    assert bpy.ops.flowhmr.align_pose_ground() == {"FINISHED"}
    body = source.children[0].evaluated_get(context.evaluated_depsgraph_get())
    mesh = body.to_mesh()
    try:
        assert abs(min((body.matrix_world @ v.co).z for v in mesh.vertices)) < 2e-5
    finally:
        body.to_mesh_clear()
    scene.frame_set(1)
    baseline = (source.matrix_world @ source.pose.bones["pelvis"].matrix.translation).z
    scene.frame_set(1 + n // 2)
    assert abs((source.matrix_world @ source.pose.bones["pelvis"].matrix.translation).z - baseline - jump_z) < 2e-5
    assert before == [[tuple(key.co) for key in curve.keyframe_points] for curve in motion._curves(source)]
    print("CURRENT_POSE_GROUND_ALIGNMENT_WITHOUT_CHANGING_JUMP_OR_KEYS_OK", flush=True)
    scene.unit_settings.scale_length = .01
    scaled = motion.import_motion(context, fixture, include_mesh=False, height_offset_m=2)[0]
    assert abs(scaled.location.z - 200) < 1e-5
    print("REFERENCE_GRID_NONDESTRUCTIVE_AND_METRE_OFFSETS_RESPECT_UNITS_OK", flush=True)
finally:
    addon.unregister()
print("BLENDER_GROUND_SMOKE_OK", flush=True)
