"""Optional integration test against the user's installed Auto-Rig Pro human preset."""
from pathlib import Path
import sys
import bpy
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import motion, rigging

addon.register()
try:
    source = motion.import_motion(bpy.context, ROOT / ".cache/blender-test/motion.npz", include_mesh=False)[0]
    arp = Path(bpy.utils.user_resource("SCRIPTS")).parent / "extensions/user_default/auto_rig_pro_master/armature_presets/human.blend"
    with bpy.data.libraries.load(str(arp), link=False) as (src, dst):
        dst.objects = src.objects
    for obj in dst.objects:
        if obj:
            bpy.context.collection.objects.link(obj)
    target = next(obj for obj in dst.objects if obj and obj.type == "ARMATURE")
    mappings = rigging.match_bones(target)
    assert mappings[0] == "c_root_master.x" and mappings[16] == "c_arm_fk.l"
    assert sum(bool(name) for name in mappings) >= 50
    print("ARP_MAPPING_OK", sum(bool(name) for name in mappings))
    rest = {}
    for source_name, target_name in zip(motion.JOINT_NAMES, mappings):
        if not target_name:
            continue
        src_q = source.data.bones[source_name].matrix_local.to_quaternion()
        dst_q = target.data.bones[target_name].matrix_local.to_quaternion()
        if source_name == "pelvis":
            rest[source_name] = src_q.inverted() @ dst_q
        else:
            swing = (dst_q @ Vector((0, 1, 0))).rotation_difference(src_q @ Vector((0, 1, 0)))
            rest[source_name] = src_q.inverted() @ swing @ dst_q
    action = rigging.bake_to_rig(bpy.context, source, target, mappings)
    assert len(motion._curves(target)) >= 207
    for name in ("c_hand_ik.l", "c_hand_ik.r", "c_foot_ik.l", "c_foot_ik.r"):
        assert target.pose.bones[name]["ik_fk_switch"] == 1
    for frame in (1, 13, 24):
        bpy.context.scene.frame_set(frame)
        for source_name, target_name in zip(motion.JOINT_NAMES, mappings):
            if not target_name:
                continue
            expected = source.pose.bones[source_name].matrix.to_quaternion() @ rest[source_name]
            actual = target.pose.bones[target_name].matrix.to_quaternion()
            error = actual.rotation_difference(expected).angle
            assert min(abs(error), abs(error - 2 * np.pi)) < 0.006, (frame, source_name, target_name, error)
    # Check the deform forearm actually responds to the animated FK controller.
    bpy.context.scene.frame_set(1)
    start = target.pose.bones["forearm.l"].matrix.copy()
    bpy.context.scene.frame_set(24)
    end = target.pose.bones["forearm.l"].matrix.copy()
    assert start.to_quaternion().rotation_difference(end.to_quaternion()).angle > 0.1
    print("ARP_FK_SWITCH_ACTION_AND_DEFORM_MOTION_OK", action.name)
    destination = ROOT / ".cache/blender-test/arp-validation.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(destination))
finally:
    addon.unregister()
