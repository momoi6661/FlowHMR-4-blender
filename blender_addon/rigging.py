"""Bone matching and conservative FK baking onto an existing character rig."""

import re
import unicodedata

import bpy
from mathutils import Matrix, Quaternion, Vector
import numpy as np

from .motion import JOINT_NAMES, _curves, _keys

_BODY = (
    ("hips", "pelvis", "センター"), ("leftupleg", "thigh_l", "thigh_fk.L", "左足"),
    ("rightupleg", "thigh_r", "thigh_fk.R", "右足"), ("spine", "spine1"),
    ("leftleg", "calf_l", "shin_fk.L", "左ひざ"), ("rightleg", "calf_r", "shin_fk.R", "右ひざ"),
    ("spine1", "spine_02", "spine2"), ("leftfoot", "foot_l", "foot_fk.L", "左足首"),
    ("rightfoot", "foot_r", "foot_fk.R", "右足首"), ("spine2", "spine_03", "chest"),
    ("lefttoebase", "ball_l", "toe.L", "左つま先"), ("righttoebase", "ball_r", "toe.R", "右つま先"),
    ("neck", "neck_01", "首"), ("leftshoulder", "clavicle_l", "shoulder.L", "左肩"),
    ("rightshoulder", "clavicle_r", "shoulder.R", "右肩"), ("head", "頭"),
    ("leftarm", "upperarm_l", "upper_arm_fk.L", "左腕"), ("rightarm", "upperarm_r", "upper_arm_fk.R", "右腕"),
    ("leftforearm", "lowerarm_l", "forearm_fk.L", "左ひじ"), ("rightforearm", "lowerarm_r", "forearm_fk.R", "右ひじ"),
    ("lefthand", "hand_l", "hand_fk.L", "左手首"), ("righthand", "hand_r", "hand_fk.R", "右手首"),
)


def _normal(name):
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", name.split(":")[-1])).lower()


_VRM = ("hips", "leftUpperLeg", "rightUpperLeg", "spine", "leftLowerLeg", "rightLowerLeg", "chest",
        "leftFoot", "rightFoot", "upperChest", "leftToes", "rightToes", "neck", "leftShoulder",
        "rightShoulder", "head", "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand")
_ARP = ("c_root_master.x", "c_thigh_fk.l", "c_thigh_fk.r", "c_spine_01.x", "c_leg_fk.l", "c_leg_fk.r", "",
        "c_foot_fk.l", "c_foot_fk.r", "c_spine_02.x", "c_toes_fk.l", "c_toes_fk.r", "c_neck.x",
        "c_shoulder.l", "c_shoulder.r", "c_head.x", "c_arm_fk.l", "c_arm_fk.r", "c_forearm_fk.l",
        "c_forearm_fk.r", "c_hand_fk.l", "c_hand_fk.r")
_RIGIFY = ("torso", "thigh_fk.L", "thigh_fk.R", "spine_fk", "shin_fk.L", "shin_fk.R", "spine_fk.001",
           "foot_fk.L", "foot_fk.R", "chest", "toe_fk.L", "toe_fk.R", "neck", "shoulder.L", "shoulder.R",
           "head", "upper_arm_fk.L", "upper_arm_fk.R", "forearm_fk.L", "forearm_fk.R", "hand_fk.L", "hand_fk.R")


def _vrm_bones(target):
    extension = getattr(target.data, "vrm_addon_extension", None)
    result = {}
    if extension is None:
        return result
    humanoid = getattr(getattr(extension, "vrm1", None), "humanoid", None)
    bones = getattr(humanoid, "human_bones", None)
    if bones:
        for name in _VRM:
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            value = getattr(getattr(getattr(bones, snake, None), "node", None), "bone_name", "")
            if value:
                result[name] = value
        for side in ("left", "right"):
            for finger in ("Index", "Middle", "Little", "Ring", "Thumb"):
                for segment in ("Metacarpal", "Proximal", "Intermediate", "Distal"):
                    name = side + finger + segment
                    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
                    value = getattr(getattr(getattr(bones, snake, None), "node", None), "bone_name", "")
                    if value:
                        result[name] = value
    old = getattr(getattr(extension, "vrm0", None), "humanoid", None)
    for bone in getattr(old, "human_bones", ()):
        value = getattr(getattr(bone, "node", None), "bone_name", "")
        if value:
            result.setdefault(bone.bone, value)
    return result


def match_bones(target):
    normalized = {_normal(bone.name): bone.name for bone in target.data.bones}
    result = []
    # First determine whether this is our native naming; avoid spine1 and
    # left_shoulder ambiguities between SMPL and Mixamo naming conventions.
    native = all(name in target.data.bones for name in ("pelvis", "left_collar", "left_wrist", "spine3"))
    arp = "c_root_master.x" in target.data.bones and "c_arm_fk.l" in target.data.bones
    rigify = "torso" in target.data.bones and "upper_arm_fk.L" in target.data.bones
    vrm = _vrm_bones(target)
    used = set()
    for i, name in enumerate(JOINT_NAMES):
        aliases = (name,) if native else (_BODY[i] if i < 22 else ())
        if i < 22 and not native:
            aliases = ((_ARP[i],) if arp else ((_RIGIFY[i],) if rigify else (*aliases, _VRM[i])))
            if _VRM[i] in vrm:
                aliases = (vrm[_VRM[i]], *aliases)
        if i >= 22 and not native:
            side, finger_segment = name.split("_", 1)
            finger, segment = finger_segment[:-1], int(finger_segment[-1])
            short = "L" if side == "left" else "R"
            vrm_segment = (("Metacarpal", "Proximal", "Distal") if finger == "thumb"
                           else ("Proximal", "Intermediate", "Distal"))[segment - 1]
            aliases = (f"{side}Hand{finger}{segment}", f"{side}{'Little' if finger == 'pinky' else finger}{vrm_segment}",
                       f"f_{finger}.{segment:02}.{short}", f"{finger}_{segment:02}_{short.lower()}",
                       f"c_{finger}{segment}.{short.lower()}", f"{finger}.{segment:02}.{short}")
            jp = {"index": "人指", "middle": "中指", "ring": "薬指", "pinky": "小指", "thumb": "親指"}[finger]
            aliases += (("左" if side == "left" else "右") + jp + str(segment - 1 if finger == "thumb" else segment),)
            semantic = f"{side}{'Little' if finger == 'pinky' else finger.capitalize()}{vrm_segment}"
            if semantic in vrm:
                aliases = (vrm[semantic], *aliases)
        matched = next((normalized[_normal(alias)] for alias in aliases
                        if _normal(alias) in normalized and normalized[_normal(alias)] not in used), "")
        if matched:
            used.add(matched)
        result.append(matched)
    return result


def bake_to_rig(context, source, target, mappings, *, root_motion=True, vertical_motion=None, height_offset_m=0.0,
                action_name=""):
    # Existing callers keep the original all-or-nothing root_motion behavior.
    # The plugin UI explicitly supplies independent horizontal/vertical choices.
    if vertical_motion is None:
        vertical_motion = root_motion
    if not np.isfinite(height_offset_m):
        raise ValueError("高度偏移必须是有限数值")
    if target == source or target.type != "ARMATURE":
        raise ValueError("请选择另一套角色骨架作为目标")
    mapping = {JOINT_NAMES[i]: name for i, name in enumerate(mappings) if name and name in target.pose.bones}
    if "pelvis" not in mapping or len(mapping) < 5:
        raise ValueError("请先识别骨骼，并至少匹配骨盆和四肢骨骼")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("同一个目标骨骼不能对应多个源骨骼")
    constrained = [name for name in mapping.values() if any(not c.mute and c.influence > 0
                   and c.type not in {"COPY_LOCATION", "COPY_SCALE"} for c in target.pose.bones[name].constraints)]
    if constrained:
        raise ValueError("目标骨骼受约束控制，请切换 FK 或选择可烘焙骨骼：" + "、".join(constrained[:4]))
    if max(target.scale) - min(target.scale) > 1e-4 or min(target.scale) <= 0:
        raise ValueError("目标骨架存在非均匀或负缩放，请先在 Blender 中应用缩放")
    scene = context.scene
    original_frame, subframe = scene.frame_current, scene.frame_subframe
    target.animation_data_create()
    previous_action = target.animation_data.action
    previous_slot = target.animation_data.action_slot if hasattr(target.animation_data, "action_slot") else None
    original_basis = {bone.name: bone.matrix_basis.copy() for bone in target.pose.bones}
    old_modes = {name: target.pose.bones[name].rotation_mode for name in mapping.values()}
    switches = {}
    if "c_root_master.x" in target.data.bones:
        for bone in target.pose.bones:
            if "ik_fk_switch" in bone and bone.name.startswith(("c_hand_ik.", "c_foot_ik.", "c_thumb", "c_index", "c_middle", "c_ring", "c_pinky")):
                switches[(bone.name, "ik_fk_switch")] = bone["ik_fk_switch"]
    if "torso" in target.data.bones and "upper_arm_fk.L" in target.data.bones:
        for bone in target.pose.bones:
            if "IK_FK" in bone and bone.name.startswith(("upper_arm_parent.", "thigh_parent.")):
                switches[(bone.name, "IK_FK")] = bone["IK_FK"]
    action = None
    context.window_manager.progress_begin(0, source["flowhmr_frames"])
    try:
        count = source["flowhmr_frames"]
        frames = source["flowhmr_frame_start"] + np.arange(count) * source["flowhmr_frame_step"]
        rotations = {name: [] for name in mapping.values()}
        locations = {name: [] for name in mapping.values()}
        scales = {name: [] for name in mapping.values()}
        change = target.matrix_world.to_quaternion().inverted() @ source.matrix_world.to_quaternion()
        offsets = {}
        for source_name, target_name in mapping.items():
            src = source.data.bones[source_name].matrix_local.to_quaternion()
            dst = target.data.bones[target_name].matrix_local.to_quaternion()
            src = change @ src
            if source_name == "pelvis":
                offsets[source_name] = src.inverted() @ dst
            else:
                # Match the segment direction while retaining the target's bone roll.
                swing = (dst @ Vector((0, 1, 0))).rotation_difference(src @ Vector((0, 1, 0)))
                offsets[source_name] = src.inverted() @ swing @ dst
        root_name = mapping["pelvis"]
        target_root = target.pose.bones[root_name]
        lengths_source, lengths_target = [], []
        for a, b in (("left_hip", "left_knee"), ("left_knee", "left_ankle"),
                     ("right_hip", "right_knee"), ("right_knee", "right_ankle")):
            if a in mapping and b in mapping:
                lengths_source.append((source.data.bones[a].head_local - source.data.bones[b].head_local).length)
                lengths_target.append((target.data.bones[mapping[a]].head_local - target.data.bones[mapping[b]].head_local).length)
        scale = sum(lengths_target) / max(1e-6, sum(lengths_source)) if lengths_source else 1.
        target_world = target.matrix_world.to_3x3()
        world_to_target = target_world.inverted()
        height_offset = height_offset_m / max(scene.unit_settings.scale_length, 1e-8)
        initial_root = None
        # Control rigs have constraint parents outside their physical parent tree
        # (ARP's head control depends on the neck through a CHILD_OF helper).
        # Follow the source anatomical order so these dependencies are solved first.
        ordered = [target.pose.bones[mapping[name]] for name in JOINT_NAMES if name in mapping]
        reverse = {value: key for key, value in mapping.items()}
        target.animation_data.action = None
        for name, prop in switches:
            target.pose.bones[name][prop] = 1.0
        for frame_index, frame in enumerate(frames):
            context.window_manager.progress_update(frame_index)
            whole = int(np.floor(frame))
            scene.frame_set(whole, subframe=float(frame - whole))
            source_root = source.pose.bones["pelvis"].matrix.translation.copy()
            if initial_root is None:
                initial_root = source_root.copy()
            # Separate movement on WORLD axes, even when the character object is
            # rotated. An in-place jump must keep Z while suppressing X and Y.
            displacement = target_world @ ((change @ (source_root - initial_root)) * scale)
            if not root_motion:
                displacement.x = displacement.y = 0
            if not vertical_motion:
                displacement.z = 0
            displacement.z += height_offset
            root_position = target_root.bone.matrix_local.translation + world_to_target @ displacement
            for bone in target.pose.bones:
                bone.matrix_basis = original_basis[bone.name]
            context.view_layer.update()
            desired_rotations = {bone.name: (change @ source.pose.bones[reverse[bone.name]].matrix.to_quaternion()
                                 @ offsets[reverse[bone.name]]).normalized() for bone in ordered}
            for iteration in range(4):
                for bone in ordered:
                    desired = Matrix.LocRotScale(bone.matrix.translation, desired_rotations[bone.name], bone.matrix.to_scale())
                    if bone.name == root_name and (root_motion or vertical_motion or height_offset_m):
                        desired.translation = root_position
                    # Updating after each control lets real helper/deform constraints
                    # between FK controls participate in the parent transform.
                    bone.matrix = desired
                    context.view_layer.update()
                unsettled = [bone.name for bone in ordered if abs(bone.matrix.to_quaternion().normalized().dot(desired_rotations[bone.name])) < 1 - 2e-7]
                if not unsettled:
                    break
            if unsettled:
                raise ValueError("控制骨骼存在尚未解开的旋转依赖，请检查 FK 和映射：" + "、".join(unsettled[:4]))
            for bone in ordered:
                qlocal = bone.matrix_basis.to_quaternion()
                if rotations[bone.name] and Quaternion(rotations[bone.name][-1]).dot(qlocal) < 0:
                    qlocal.negate()
                rotations[bone.name].append(tuple(qlocal))
                locations[bone.name].append(tuple(bone.matrix_basis.translation))
                scales[bone.name].append(tuple(bone.matrix_basis.to_scale()))
        # Only attach the new Action after all samples and mappings are valid.
        if previous_action:
            previous_action.use_fake_user = True
        target.animation_data.action = None
        target_root.rotation_mode = "QUATERNION"
        target_root.keyframe_insert("rotation_quaternion", frame=frames[0], group=root_name)
        action = target.animation_data.action
        action.name = action_name.strip() or f"FlowHMR · {source['flowhmr_job_id']} · {target.name}"
        curves = _curves(target)
        for curve in list(curves):
            curves.remove(curve)
        for name, values in rotations.items():
            bone = target.pose.bones[name]
            bone.rotation_mode = "QUATERNION"
            _keys(curves, bone.path_from_id("rotation_quaternion"), np.asarray(values), frames, name)
            _keys(curves, bone.path_from_id("location"), np.asarray(locations[name]), frames, name)
            _keys(curves, bone.path_from_id("scale"), np.asarray(scales[name]), frames, name)
        for name, prop in switches:
            target.pose.bones[name][prop] = 1.0
            target.pose.bones[name].keyframe_insert(f'["{prop}"]', frame=frames[0], group=name)
        target["flowhmr_source_job"] = source["flowhmr_job_id"]
        target["flowhmr_is_source"] = False
        target["flowhmr_source_kind"] = source.get("flowhmr_source_kind", "video")
        action["flowhmr_horizontal_motion"] = root_motion
        action["flowhmr_vertical_motion"] = vertical_motion
        action["flowhmr_height_offset_m"] = height_offset_m
        for key in ("flowhmr_job_id", "flowhmr_frame_start", "flowhmr_frame_step", "flowhmr_frames", "flowhmr_person"):
            target[key] = source[key]
        return action
    except Exception:
        target.animation_data.action = previous_action
        if previous_slot and previous_action:
            target.animation_data.action_slot = previous_slot
        if action and action.users == 0:
            bpy.data.actions.remove(action)
        for name, basis in original_basis.items():
            target.pose.bones[name].matrix_basis = basis
        for name, mode in old_modes.items():
            target.pose.bones[name].rotation_mode = mode
        for (name, prop), value in switches.items():
            target.pose.bones[name][prop] = value
        raise
    finally:
        context.window_manager.progress_end()
        scene.frame_set(original_frame, subframe=subframe)
