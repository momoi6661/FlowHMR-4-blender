"""Optional Blender integration test with a generated Rigify human rig."""
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
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.armature_human_metarig_add()
    bpy.ops.pose.rigify_generate()
    target = bpy.context.active_object
    source = motion.import_motion(bpy.context, ROOT / ".cache/blender-test/motion.npz", include_mesh=False)[0]
    mappings = rigging.match_bones(target)
    print("RIGIFY_MAPPING", sum(bool(name) for name in mappings), [(s, t) for s, t in zip(motion.JOINT_NAMES, mappings) if not t])
    assert sum(bool(name) for name in mappings) >= 50
    rest = {}
    for src, dst in zip(motion.JOINT_NAMES, mappings):
        if not dst:
            continue
        a, b = source.data.bones[src].matrix_local.to_quaternion(), target.data.bones[dst].matrix_local.to_quaternion()
        rest[src] = a.inverted() @ b if src == "pelvis" else a.inverted() @ ((b @ Vector((0, 1, 0))).rotation_difference(a @ Vector((0, 1, 0)))) @ b
    action = rigging.bake_to_rig(bpy.context, source, target, mappings)
    for name in ("upper_arm_parent.L", "upper_arm_parent.R", "thigh_parent.L", "thigh_parent.R"):
        assert target.pose.bones[name]["IK_FK"] == 1
    for frame in (1, 13, 24):
        bpy.context.scene.frame_set(frame)
        for src, dst in zip(motion.JOINT_NAMES, mappings):
            if not dst:
                continue
            expected = source.pose.bones[src].matrix.to_quaternion() @ rest[src]
            actual = target.pose.bones[dst].matrix.to_quaternion()
            error = actual.rotation_difference(expected).angle
            assert min(abs(error), abs(error - 2 * np.pi)) < .006, (frame, src, dst, error)
    bpy.context.scene.frame_set(1)
    before = target.pose.bones["DEF-forearm.L"].matrix.to_quaternion()
    bpy.context.scene.frame_set(24)
    after = target.pose.bones["DEF-forearm.L"].matrix.to_quaternion()
    assert before.rotation_difference(after).angle > .1
    print("RIGIFY_FK_NEW_ACTION_AND_DEFORM_MOTION_OK", action.name)
finally:
    addon.unregister()
