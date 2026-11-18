"""Real ARP creation in an isolated user-resource directory, without user GUI effects."""
from pathlib import Path
from unittest.mock import patch
import shutil
import sys

import addon_utils
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import arp_character, motion, operators


def positions(body):
    bpy.context.view_layer.update()
    evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return np.asarray([evaluated.matrix_world @ v.co for v in mesh.vertices])
    finally:
        evaluated.to_mesh_clear()


assert "arp-character-runtime" in bpy.utils.user_resource('SCRIPTS'), "Run only with isolated BLENDER_USER_RESOURCES"
installed = Path(r'C:\Users\liuyuquan1.LIUYUQUAN\AppData\Roaming\Blender Foundation\Blender\5.3\extensions\user_default\auto_rig_pro_master')
runtime = Path(bpy.utils.user_resource('SCRIPTS', create=True)) / 'addons' / 'auto_rig_pro_master'
if not runtime.exists():
    shutil.copytree(installed, runtime, ignore=shutil.ignore_patterns('__pycache__'))
sys.path.insert(0, str(runtime.parent))
arp = addon_utils.enable('auto_rig_pro_master', default_set=True)
assert arp and arp.__addon_enabled__, 'ARP registration failed'
addon.register()
try:
    source = motion.import_motion(bpy.context, ROOT / '.cache/blender-test/motion.npz')[0]
    body, action = source.children[0], source.animation_data.action
    original = positions(body)
    rig = arp_character.create_character(bpy.context, source)
    assert bpy.ops.arp.match_to_rig.poll()
    assert rig.data.get('has_match_to_rig') and rig.get('flowhmr_arp_character')
    assert rig['flowhmr_arp_body_count'] == 1
    assert not any(d.data_path.endswith('.custom_shape_scale') for d in rig.animation_data.drivers)
    assert source.hide_get() and source.hide_render
    assert source.animation_data.action == action
    np.testing.assert_allclose(positions(body), original, atol=1e-6)
    newbody = next(obj for obj in rig.children if obj.type == 'MESH')
    assert newbody.data != body.data and newbody.find_armature() == rig
    assert all(group.name in rig.data.bones for group in newbody.vertex_groups)
    assert all(rig.data.bones[group.name].use_deform for group in newbody.vertex_groups)
    assert all(np.isfinite(positions(newbody)).flat)
    print('ARP_NATIVE_MATCH_SKIN_TRANSFER_SOURCE_ACTION_AND_MESH_PRESERVED_OK', flush=True)
    bpy.context.scene.frame_set(1)
    first = positions(newbody)
    bpy.context.scene.frame_set(20)
    assert np.max(np.linalg.norm(positions(newbody) - first, axis=1)) > .01
    bpy.context.scene.frame_set(1)
    rig.animation_data.action = None
    before = positions(newbody)
    control = rig.pose.bones['c_forearm_fk.l']
    control.rotation_mode = 'XYZ'
    control.rotation_euler.z += .6
    rig.update_tag()
    movement = np.max(np.linalg.norm(positions(newbody) - before, axis=1))
    print('ARP_CONTROL_EDIT_DISPLACEMENT', movement, flush=True)
    assert movement > .01
    print('ARP_ANIMATED_BODY_AND_EDITABLE_FK_CONTROLLER_DEFORM_OK', flush=True)

    image = motion.import_motion(bpy.context, ROOT / '.cache/blender-test/airborne.npz', source_image=True)[0]
    action = image.animation_data.action
    expected_root = image.matrix_world @ image.pose.bones['pelvis'].matrix.translation
    rig = arp_character.create_character(bpy.context, image)
    assert rig['flowhmr_frames'] == 1 and rig['flowhmr_source_kind'] == 'image'
    assert all(len(curve.keyframe_points) == 1 for curve in motion._curves(rig))
    actual_root = rig.matrix_world @ rig.pose.bones['c_root_master.x'].matrix.translation
    assert (actual_root - expected_root).length < .002, (expected_root, actual_root)
    assert image.animation_data.action == action
    print('ARP_IMAGE_SINGLE_FRAME_AND_ABSOLUTE_AIRBORNE_ROOT_OK', flush=True)

    failed_source = motion.import_motion(bpy.context, ROOT / '.cache/blender-test/motion.npz')[0]
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = failed_source
    old_objects, old_actions = set(bpy.data.objects), set(bpy.data.actions)
    with patch.object(arp_character, '_copy_body', side_effect=ValueError('fixture binding failure')):
        try:
            arp_character.create_character(bpy.context, failed_source)
            raise AssertionError('Failed conversion was accepted')
        except ValueError as error:
            assert str(error) == 'fixture binding failure'
    assert set(bpy.data.objects) == old_objects and set(bpy.data.actions) == old_actions
    assert not failed_source.hide_get() and not failed_source.hide_render
    assert bpy.context.active_object == failed_source
    print('ARP_FAILED_CONVERSION_ROLLBACK_SOURCE_SELECTION_AND_ACTIONS_OK', flush=True)
    # Exercise the actual completed-task import branch, not only manual conversion.
    s = bpy.context.scene.flowhmr
    s.backend_root = str(ROOT)
    s.job_id, s.status, s.target_mode = 'arp-import-fixture', 'done', 'ARP'
    s.result_people_json, s.source_is_image = '[]', False
    s.submitted_video_job, s.submitted_video_path = s.job_id, str(ROOT / 'assets/example/example.mp4')
    s.import_frame = 7

    class FixtureClient:
        def save(self, route, destination):
            assert 'mesh=1' in route
            shutil.copyfile(ROOT / '.cache/blender-test/motion.npz', destination)
            return str(destination)

    existing = set(bpy.data.objects)
    with patch.object(operators, '_client', return_value=FixtureClient()), \
         patch.object(operators, '_launch', side_effect=lambda settings, work, done: done(settings, work())):
        operators._start_import(s)
    created = set(bpy.data.objects) - existing
    editable = [obj for obj in created if obj.get('flowhmr_arp_character')]
    assert len(editable) == 1 and s.imported_job_id == 'arp-import-fixture'
    assert editable[0]['flowhmr_frame_start'] == 7
    assert editable[0].animation_data.action is not None
    assert bpy.context.active_object == editable[0]
    assert 'ARP 可编辑角色' in s.message
    print('ARP_OUTPUT_CHOICE_AUTOMATIC_TASK_IMPORT_BODY_AND_ACTION_OK', flush=True)
finally:
    addon.unregister()
print('BLENDER_ARP_CHARACTER_SMOKE_OK', flush=True)
