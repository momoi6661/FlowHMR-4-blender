"""Real Blender FK/import validation. Fixture prepared by test_blender_motion.py."""
from pathlib import Path
import json
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import motion, rigging

addon.register()
try:
    s = bpy.context.scene.flowhmr
    assert not s.auto_import and not s.import_mesh and s.manual_select and s.postprocess
    assert len(addon.CLASSES) > 25
    scene = bpy.context.scene
    scene.render.fps = 24
    fixture = ROOT / ".cache/blender-test/motion.npz"
    source = motion.import_motion(bpy.context, fixture, frame_start=13, include_mesh=True, skeleton_name="测试骨架", action_name="开门动作")[0]
    assert source.name == source.data.name == "测试骨架"
    assert source.animation_data.action.name == "开门动作"
    assert sum(b.use_deform for b in source.data.bones) == 52 and len(source.children) == 1
    assert len(motion._curves(source)) == 211
    assert source.animation_data.action_slot is not None
    # Rest T-pose stands on the floor; posed motion below retains original FK.
    body = source.children[0]
    assert abs(min(v.co.z for v in body.data.vertices)) < 1e-6
    assert source.data.bones["pelvis"].head_local.z > .5
    source.data.pose_position = "REST"
    bpy.context.view_layer.update()
    evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
    rest_mesh = evaluated.to_mesh()
    try:
        assert abs(min((evaluated.matrix_world @ v.co).z for v in rest_mesh.vertices)) < 1e-5
    finally:
        evaluated.to_mesh_clear()
    source.data.pose_position = "POSE"
    with np.load(fixture, allow_pickle=False) as data:
        parents, joints, poses, trans = data["parents"], data["joints_0"], data["poses_0"], data["trans_0"]
        for frame in (0, 12, 29):
            t = 13 + frame * 24 / 30
            scene.frame_set(int(t), subframe=t - int(t))
            rotations, positions = [], []
            for j in range(52):
                v = Vector(poses[frame, j])
                q = Quaternion(v.normalized(), v.length) if v.length > 1e-8 else Quaternion()
                if parents[j] < 0:
                    rotations.append(q)
                    positions.append(Vector(joints[j]) + Vector(trans[frame]))
                else:
                    p = int(parents[j])
                    rotations.append(rotations[p] @ q)
                    positions.append(positions[p] + rotations[p] @ (Vector(joints[j]) - Vector(joints[p])))
                actual = source.pose.bones[motion.JOINT_NAMES[j]].matrix.translation
                expected = motion._C @ positions[j]
                assert (actual - expected).length < 2e-5, (j, frame, actual, expected)
            for name in motion.JOINT_NAMES[1:]:
                child = source.pose.bones[name]
                parent = child.parent
                if (parent.tail - child.head).length > 2e-5:
                    link = source.pose.bones['flowhmr_link_' + name]
                    assert not link.bone.use_deform and link.bone.hide_select
                    assert (link.head - parent.head).length < 2e-5
                    assert (link.tail - child.head).length < 2e-5
        assert motion.ensure_joint_connections(bpy.context, source) == 0
        assert len(motion._curves(source)) == 211
    print('WEB_JOINT_TREE_BRANCH_DISPLAY_CONNECTIONS_PRESERVE_SKIN_AND_FK_OK')
    print("SMPL_FK_52_BONES_FINGERS_TRANSLATION_AND_24FPS_OK")
    print("REST_T_POSE_SOLES_ON_ZERO_PLANE_WITH_ORIGINAL_POSED_WORLD_GEOMETRY_OK")
    # Repair a pre-update source selected through its mesh, without inference.
    links = {b.name for b in source.data.bones if b.name.startswith('flowhmr_link_')}
    assert links
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode='EDIT')
    for name in links:
        source.data.edit_bones.remove(source.data.edit_bones[name])
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = body
    body.select_set(True)
    assert bpy.ops.flowhmr.repair_bone_display() == {'FINISHED'}
    assert all(name in source.data.bones for name in links)
    assert sum(b.use_deform for b in source.data.bones) == 52
    assert len(motion._curves(source)) == 211
    print('EXISTING_SOURCE_REPAIR_FROM_SELECTED_BODY_PRESERVES_DEFORMS_AND_ACTION_OK')
    # Retarget to a separate real Blender armature, preserving its previous Action.
    target = source.copy()
    target.data = source.data.copy()
    target.animation_data_clear()
    bpy.context.collection.objects.link(target)
    target.name = "Existing Character"
    mappings = rigging.match_bones(target)
    assert len([x for x in mappings if x]) == 52
    s.target_mode = "NEW"
    s.target_rig = target
    assert s.target_mode == "TARGET" and len(s.bone_maps) == 52
    s.target_rig = None
    assert s.target_mode == "AUTO" and not s.bone_maps
    target.pose.bones[0].keyframe_insert("rotation_quaternion", frame=1)
    old_action = target.animation_data.action
    new_action = rigging.bake_to_rig(bpy.context, source, target, mappings, action_name="角色开门")
    assert new_action.name == "角色开门" and target.name == "Existing Character"
    assert new_action != old_action and old_action.use_fake_user
    assert len(motion._curves(target)) == 520
    for frame in (0, 12, 29):
        t = 13 + frame * 24 / 30
        scene.frame_set(int(t), subframe=t - int(t))
        for name in motion.JOINT_NAMES:
            angle = source.pose.bones[name].matrix.to_quaternion().rotation_difference(target.pose.bones[name].matrix.to_quaternion()).angle
            assert min(abs(angle), abs(angle - 2 * np.pi)) < 0.002, (name, angle)
    print("EXISTING_RIG_NEW_ACTION_AND_WORLD_ROTATIONS_OK")
    # Rig type detection uses exact aliases rather than guessing a fuzzy match.
    names = ["mixamorig:" + aliases[0] for aliases in rigging._BODY]
    for i in range(22):
        target.data.bones[mappings[i]].name = names[i]
    for i, name in enumerate(motion.JOINT_NAMES[22:], 22):
        side, finger = name.split("_")
        target.data.bones[mappings[i]].name = "mixamorig:" + side + "Hand" + finger
    assert all(rigging.match_bones(target)), rigging.match_bones(target)
    print("MIXAMO_MATCH_52_OK")
    # Two tracks keep real-time offsets, world placement and their visible lifetime.
    with np.load(fixture, allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files}
    manifest = json.loads(data["manifest"].item())
    actor = manifest["actors"][0]
    manifest["actors"].append(dict(actor, index=1, person=2, job_id="second", start=15))
    for key in ("poses", "trans", "joints", "vertices"):
        data[f"{key}_1"] = data[f"{key}_0"].copy()
    data["trans_1"][:, 0] += 2
    data["manifest"] = np.array(json.dumps(manifest))
    multi_path = fixture.with_name("multi.npz")
    np.savez_compressed(multi_path, **data)
    actors = motion.import_motion(bpy.context, multi_path, frame_start=13, include_mesh=True, skeleton_name="多人骨架", action_name="多人动作")
    assert all(a.name.startswith("多人骨架 · 人物 ") and a.animation_data.action.name.startswith("多人动作 · 人物 ") for a in actors)
    assert [a["flowhmr_frame_start"] for a in actors] == [13, 25]
    assert scene.render.fps == 24
    for frame, hidden in ((12, (True, True)), (13, (False, True)),
                          (25, (False, False)), (37, (True, False)), (49, (True, True))):
        scene.frame_set(frame)
        for obj, expected in zip(actors, hidden):
            assert obj.hide_viewport == expected and obj.hide_render == expected, (frame, obj.name)
            assert len(obj.children) == 1
            assert all(c.hide_viewport == expected and c.hide_render == expected for c in obj.children)
    scene.frame_set(25)
    # Actor 2 starts two metres from actor 1's initial position, while actor 1 is halfway through.
    difference = actors[1].pose.bones["pelvis"].matrix.translation - actors[0].pose.bones["pelvis"].matrix.translation
    assert abs(difference.x - (2 - data["trans_0"][15, 0])) < 2e-5
    assert motion.actor_context(bpy.context)[1] == 0
    print("MULTI_ACTOR_TIMING_PLACEMENT_AND_VISIBILITY_OK")
    print("BLENDER_MOTION_SMOKE_OK")
finally:
    addon.unregister()
