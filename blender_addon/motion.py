"""Create editable Blender armatures and actions without an FBX SDK."""

import json
import math

import bpy
from mathutils import Matrix, Quaternion, Vector
import numpy as np

JOINT_NAMES = (
    "pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee", "spine2",
    "left_ankle", "right_ankle", "spine3", "left_foot", "right_foot", "neck",
    "left_collar", "right_collar", "head", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist",
    *(f"{side}_{finger}{segment}" for side in ("left", "right")
      for finger in ("index", "middle", "pinky", "ring", "thumb") for segment in (1, 2, 3)),
)
_C = Matrix.Rotation(math.pi / 2, 3, "X")  # Y-up SMPL to Z-up Blender, metres.
_CQ = _C.to_quaternion()


def _curves(obj):
    action = obj.animation_data.action
    if hasattr(action, "slots"):
        slot = obj.animation_data.action_slot
        for layer in action.layers:
            for strip in layer.strips:
                if strip.type == "KEYFRAME":
                    bag = strip.channelbag(slot)
                    if bag:
                        return bag.fcurves
        raise RuntimeError("Blender action channel bag was not created")
    return action.fcurves


def _keys(curves, path, values, frames, group):
    for component in range(values.shape[1]):
        curve = curves.find(path, index=component)
        if curve is None:
            grouping = {"group_name" if bpy.app.version >= (4, 4, 0) else "action_group": group}
            curve = curves.new(path, index=component, **grouping)
        curve.keyframe_points.add(len(frames))
        xy = np.column_stack((frames, values[:, component])).ravel()
        curve.keyframe_points.foreach_set("co", xy)
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"
        curve.update()


def import_motion(context, path, *, frame_start=1, include_mesh=True, height_offset_m=0.0, source_image=False,
                  skeleton_name="", action_name=""):
    """Import into a new collection. Preserve timing at the existing scene FPS."""
    scene = context.scene
    if not np.isfinite(height_offset_m):
        raise ValueError("高度偏移必须是有限数值")
    old_active = context.view_layer.objects.active
    old_selected = list(context.selected_objects)
    old_mode = old_active.mode if old_active else "OBJECT"
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    collection = None
    created = []
    try:
        with np.load(path, allow_pickle=False) as archive:
            data = {key: archive[key] for key in archive.files}
        manifest = json.loads(str(data["manifest"].item()))
        if manifest["version"] != 1 or not manifest["actors"]:
            raise ValueError("不支持的 Blender 动作文件")
        fps = float(manifest["fps"])
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("无效的动作帧率")
        parents = data["parents"].astype(int)
        if parents.shape != (52,) or parents[0] != -1 or any(not 0 <= parents[i] < i for i in range(1, 52)):
            raise ValueError("无效的骨骼层级")
        # Validate every actor before making any Blender data.
        for actor in manifest["actors"]:
            i, count = actor["index"], actor["frames"]
            for key, shape in (("poses", (count, 52, 3)), ("trans", (count, 3)), ("joints", (52, 3))):
                array = data[f"{key}_{i}"]
                if array.shape != shape or not count or not np.isfinite(array).all():
                    raise ValueError("动作数组或骨骼数据损坏")
        source_image = source_image or manifest.get("source_kind") == "image"
        if source_image:
            # Older servers/history may still return 30 inferred poses for an
            # image. Write one original pose, including when baking to a rig.
            for actor in manifest["actors"]:
                i = actor["index"]
                data[f"poses_{i}"] = data[f"poses_{i}"][:1]
                data[f"trans_{i}"] = data[f"trans_{i}"][:1]
                actor["frames"], actor["start"] = 1, 0
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        collection = bpy.data.collections.new(f"FlowHMR · {manifest['job_id']}")
        scene.collection.children.link(collection)
        ratio = (scene.render.fps / scene.render.fps_base) / fps
        last = frame_start
        for actor in manifest["actors"]:
            i, count = actor["index"], actor["frames"]
            start = frame_start + actor["start"] * ratio
            frames = start + np.arange(count) * ratio
            last = max(last, float(frames[-1]))
            joints = [(_C @ Vector(v)) for v in data[f"joints_{i}"]]
            vertices = (data[f"vertices_{i}"] @ np.asarray(_C).T
                        if f"vertices_{i}" in data else None)
            # Give the rest body a floor-based origin. Compensate every root
            # translation below, so posed world geometry and flight stay exact.
            rest_floor = (float(vertices[:, 2].min()) if vertices is not None
                          else min(joints[j].z for j in (7, 8, 10, 11)))
            rest_shift = Vector((0, 0, -rest_floor))
            joints = [joint + rest_shift for joint in joints]
            suffix = f" · 人物 {actor['person']}" if len(manifest["actors"]) > 1 else ""
            custom_skeleton = skeleton_name.strip() + suffix if skeleton_name.strip() else ""
            armature = bpy.data.armatures.new(custom_skeleton or f"FlowHMR_Skeleton_{actor['person']}")
            obj = bpy.data.objects.new(custom_skeleton or f"FlowHMR · 人物 {actor['person']}", armature)
            collection.objects.link(obj)
            created.append(obj)
            obj.location.z = height_offset_m / max(scene.unit_settings.scale_length, 1e-8)
            obj["flowhmr_height_offset_m"] = height_offset_m
            obj["flowhmr_rest_shift_z"] = rest_shift.z
            for other in context.selected_objects:
                other.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
            for j, name in enumerate(JOINT_NAMES):
                bone = armature.edit_bones.new(name)
                bone.head = joints[j]
                children = np.flatnonzero(parents == j)
                # A real child provides the axis; leaf tips extend their incoming segment.
                if len(children):
                    child = (int(children[len(children) // 2]) if j in (20, 21) else int(children[0]))
                    delta = joints[child] - joints[j]
                else:
                    delta = (joints[j] - joints[parents[j]]) * 0.45
                if delta.length < 1e-5:
                    delta = Vector((0, 0.03, 0))
                bone.tail = bone.head + delta
                if parents[j] >= 0:
                    bone.parent = armature.edit_bones[JOINT_NAMES[parents[j]]]
            _add_joint_connections(armature)
            bpy.ops.object.mode_set(mode="OBJECT")
            obj.show_in_front = True
            armature.display_type = "OCTAHEDRAL"
            obj["flowhmr_job_id"] = actor["job_id"]
            obj["flowhmr_is_source"] = True
            obj["flowhmr_frame_start"] = start
            obj["flowhmr_frame_step"] = ratio
            obj["flowhmr_frames"] = count
            obj["flowhmr_person"] = actor["person"]
            obj["flowhmr_source_start"] = actor.get("source_start", 0)
            obj["flowhmr_source_kind"] = "image" if source_image else "video"
            # keyframe_insert creates a correctly bound Action on both legacy and slotted Blender.
            root = obj.pose.bones[0]
            root.rotation_mode = "QUATERNION"
            root.keyframe_insert("rotation_quaternion", frame=start, group=root.name)
            curves = _curves(obj)
            # Remove the seed keys; all channels below are filled in one batch.
            for curve in list(curves):
                curves.remove(curve)
            obj.animation_data.action.name = (action_name.strip() + suffix if action_name.strip() else
                                              f"FlowHMR · {actor['job_id']} · 人物 {actor['person']}")
            for j, name in enumerate(JOINT_NAMES):
                bone = obj.pose.bones[name]
                bone.rotation_mode = "QUATERNION"
                basis = armature.bones[name].matrix_local.to_quaternion()
                values = []
                previous = None
                for vector in data[f"poses_{i}"][:, j]:
                    angle = float(np.linalg.norm(vector))
                    q = Quaternion(Vector(vector) / angle, angle) if angle > 1e-10 else Quaternion()
                    q = basis.conjugated() @ _CQ @ q @ _CQ.conjugated() @ basis
                    if previous is not None and previous.dot(q) < 0:
                        q.negate()
                    values.append(tuple(q))
                    previous = q
                _keys(curves, bone.path_from_id("rotation_quaternion"), np.asarray(values), frames, bone.name)
            root_basis = armature.bones[0].matrix_local.to_3x3().inverted()
            locations = np.asarray([root_basis @ (_C @ Vector(t) - rest_shift) for t in data[f"trans_{i}"]])
            _keys(curves, root.path_from_id("location"), locations, frames, root.name)
            if include_mesh and f"vertices_{i}" in data:
                vertices = vertices + np.asarray(rest_shift)
                mesh = bpy.data.meshes.new(f"FlowHMR_Body_{actor['person']}")
                mesh.from_pydata(vertices.tolist(), [], data["faces"].tolist())
                mesh.update()
                body = bpy.data.objects.new(f"FlowHMR · 身体 {actor['person']}", mesh)
                collection.objects.link(body)
                created.append(body)
                body.parent = obj
                body["flowhmr_job_id"] = actor["job_id"]
                for j, name in enumerate(JOINT_NAMES):
                    group = body.vertex_groups.new(name=name)
                    for v in np.flatnonzero(data["weights"][:, j] > 0.00001):
                        group.add([int(v)], float(data["weights"][v, j]), "REPLACE")
                modifier = body.modifiers.new("FlowHMR 骨骼", "ARMATURE")
                modifier.object = obj
                for polygon in mesh.polygons:
                    polygon.use_smooth = True
                body.color = (0.18, 0.55, 0.85, 1)
            if len(manifest["actors"]) > 1 and not source_image:
                # A track is visible only while this person exists in the video.
                visible_start = start
                visible_end = frame_start + (actor["start"] + count) * ratio
                epsilon = min(0.01, ratio * 0.1)
                keys = [(frame_start - epsilon, True), (visible_start, False), (visible_end, True)]
                for animated in [obj, *obj.children]:
                    for frame, hidden in keys:
                        animated.hide_viewport = animated.hide_render = hidden
                        animated.keyframe_insert("hide_viewport", frame=frame)
                        animated.keyframe_insert("hide_render", frame=frame)
                    for curve in _curves(animated):
                        if curve.data_path in {"hide_viewport", "hide_render"}:
                            for key in curve.keyframe_points:
                                key.interpolation = "CONSTANT"
        scene.frame_end = max(scene.frame_end, math.ceil(last))
        scene.frame_start = min(scene.frame_start, frame_start)
        scene.frame_set(frame_start)
        context.view_layer.update()
        return [obj for obj in created if obj.type == "ARMATURE"]
    except Exception:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in reversed(created):
            datablock, kind = obj.data, obj.type
            action = obj.animation_data.action if obj.animation_data else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if datablock.users == 0:
                (bpy.data.armatures if kind == "ARMATURE" else bpy.data.meshes).remove(datablock)
            if action and action.users == 0:
                bpy.data.actions.remove(action)
        if collection:
            bpy.data.collections.remove(collection)
        scene.frame_set(old_frame, subframe=old_subframe)
        for obj in old_selected:
            obj.select_set(True)
        context.view_layer.objects.active = old_active
        raise
    finally:
        if old_mode != "OBJECT" and old_active and old_active.name in context.view_layer.objects:
            context.view_layer.objects.active = old_active
            bpy.ops.object.mode_set(mode=old_mode)


def _add_joint_connections(armature):
    """Show every joint-tree edge as on the Web, without changing deform bones."""
    bones = armature.edit_bones
    count = 0
    for name in JOINT_NAMES[1:]:
        child = bones.get(name)
        parent = child.parent if child else None
        if parent is None or (parent.tail - child.head).length < 1e-5 or (parent.head - child.head).length < 1e-5:
            continue
        link_name = "flowhmr_link_" + name
        if link_name in bones:
            continue
        link = bones.new(link_name)
        link.head, link.tail, link.parent = parent.head, child.head, parent
        link.use_deform = False
        link.hide_select = True
        count += 1
    return count


def ensure_joint_connections(context, obj):
    if obj and obj.type == "MESH":
        obj = obj.find_armature()
    if not obj or not obj.get("flowhmr_is_source"):
        raise ValueError("请选中标准动捕身体或骨架")
    previous_active, mode = context.view_layer.objects.active, context.object.mode if context.object else "OBJECT"
    try:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        return _add_joint_connections(obj.data)
    finally:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.objects.active = previous_active
        if previous_active and mode != "OBJECT":
            bpy.ops.object.mode_set(mode=mode)


def align_current_pose_to_ground(context, obj):
    """Translate a source as a whole using its evaluated current soles."""
    if obj and obj.type == "MESH":
        obj = obj.find_armature()
    if not obj or obj.type != "ARMATURE" or not obj.get("flowhmr_is_source"):
        raise ValueError("请选中 FlowHMR 标准骨骼或身体")
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    floor = None
    for body in obj.children:
        if body.type != "MESH" or body.find_armature() != obj:
            continue
        evaluated = body.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            z = min((evaluated.matrix_world @ v.co).z for v in mesh.vertices)
            floor = z if floor is None else min(floor, z)
        finally:
            evaluated.to_mesh_clear()
    if floor is None:
        floor = min((obj.matrix_world @ point).z
                    for name in ("left_foot", "right_foot")
                    for point in (obj.pose.bones[name].head, obj.pose.bones[name].tail))
    if not np.isfinite(floor):
        raise ValueError("当前姿势高度无效")
    world = obj.matrix_world.copy()
    world.translation.z -= floor
    obj.matrix_world = world
    scale = max(context.scene.unit_settings.scale_length, 1e-8)
    obj["flowhmr_height_offset_m"] = float(obj.get("flowhmr_height_offset_m", 0)) - floor * scale
    context.view_layer.update()
    return -floor * scale


def actor_context(context):
    obj = context.active_object
    if obj and obj.type == "MESH" and obj.parent:
        obj = obj.parent
    if not obj or obj.type != "ARMATURE" or "flowhmr_job_id" not in obj:
        raise ValueError("请选中已导入的 FlowHMR 骨骼或身体")
    frame = round((context.scene.frame_current_final - obj["flowhmr_frame_start"]) / obj["flowhmr_frame_step"])
    if not 0 <= frame < obj["flowhmr_frames"]:
        raise ValueError("当前时间不在此人物的动作区间内")
    return obj, frame
