"""Build an editable ARP character using the user's enabled ARP installation."""

from pathlib import Path
import sys

import bpy
from mathutils import Vector

from . import motion, rigging


def human_preset(context):
    for entry in context.preferences.addons:
        if entry.module.split(".")[-1] not in {"auto_rig_pro", "auto_rig_pro_master"}:
            continue
        module = sys.modules.get(entry.module)
        if module and getattr(module, "__file__", None):
            path = Path(module.__file__).parent / "armature_presets" / "human.blend"
            if path.is_file():
                try:
                    bpy.ops.arp.match_to_rig.get_rna_type()
                except (AttributeError, RuntimeError):
                    break
                return path
    raise ValueError("请先安装并启用 Auto-Rig Pro，再创建 ARP 可编辑角色")


def source_rig(obj):
    obj = obj.find_armature() if obj and obj.type == "MESH" else obj
    if not obj or obj.type != "ARMATURE" or not obj.get("flowhmr_is_source"):
        raise ValueError("请选中生成的 FlowHMR 标准身体或骨架")
    if not all(name in obj.data.bones for name in motion.JOINT_NAMES):
        raise ValueError("标准动捕骨架缺少关节，请从任务重新添加")
    return obj


def _reference_map():
    result = {"root_ref.x": "pelvis", "spine_01_ref.x": "spine1", "spine_02_ref.x": "spine3",
              "neck_ref.x": "neck", "head_ref.x": "head"}
    for side, suffix in (("left", "l"), ("right", "r")):
        for ref, joint in (("thigh", "hip"), ("leg", "knee"), ("foot", "ankle"), ("toes", "foot"),
                           ("shoulder", "collar"), ("arm", "shoulder"), ("forearm", "elbow"), ("hand", "wrist")):
            result[f"{ref}_ref.{suffix}"] = f"{side}_{joint}"
        for finger in ("index", "middle", "ring", "pinky", "thumb"):
            for segment in (1, 2, 3):
                result[f"{finger}{segment}_ref.{suffix}"] = f"{side}_{finger}{segment}"
    return result


def _fit_references(context, source, rig):
    refs = _reference_map()
    missing = [name for name in refs if name not in rig.data.bones]
    if missing:
        raise ValueError("当前 ARP human 预设不兼容：" + "、".join(missing[:4]))
    rest = source.data.bones
    old_root = rig.data.bones["root_ref.x"].head_local.copy()
    new_root = rest["pelvis"].head_local.copy()
    scale = (rest["head"].head_local - new_root).length / (
        rig.data.bones["head_ref.x"].head_local - old_root).length
    bpy.ops.object.mode_set(mode="EDIT")
    bones = rig.data.edit_bones
    rig.data.use_mirror_x = False
    for bone in bones:
        if "_ref." in bone.name:
            bone.use_connect = False
            bone.head = new_root + (bone.head - old_root) * scale
            bone.tail = new_root + (bone.tail - old_root) * scale
    for name, joint in refs.items():
        bone, src = bones[name], rest[joint]
        # Transform auxiliary heel/bank and finger-base references with their
        # parent segment before replacing the primary anatomical landmarks.
        tail = rest["spine3"].head_local if joint == "spine1" else src.tail_local
        if joint == "pelvis":
            tail = rest["spine1"].head_local
        if joint == "spine3":
            tail = rest["neck"].head_local
        old_head, old_direction = bone.head.copy(), bone.tail - bone.head
        new_direction = tail - src.head_local
        if new_direction.length < 1e-6:
            raise ValueError("源骨架包含零长度关节：" + joint)
        turn = old_direction.rotation_difference(new_direction)
        ratio = new_direction.length / max(old_direction.length, 1e-6)
        if name.startswith(("foot_ref.", "hand_ref.")):
            for child in list(bones):
                if child.parent == bone and child.name not in refs:
                    child.head = src.head_local + turn @ ((child.head - old_head) * ratio)
                    child.tail = src.head_local + turn @ ((child.tail - old_head) * ratio)
        bone.head, bone.tail = src.head_local, tail
    for side, suffix in (("left", "l"), ("right", "r")):
        for finger in ("index", "middle", "ring", "pinky"):
            base = bones.get(f"{finger}1_base_ref.{suffix}")
            if base:
                base.head = rest[f"{side}_wrist"].head_local.lerp(rest[f"{side}_{finger}1"].head_local, .35)
                base.tail = rest[f"{side}_{finger}1"].head_local
    bpy.ops.object.mode_set(mode="OBJECT")


def _migrate_shape_drivers(rig):
    """Some installed human presets still drive Blender's removed scalar size."""
    if not rig.animation_data or "custom_shape_scale_xyz" not in bpy.types.PoseBone.bl_rna.properties:
        return
    for curve in list(rig.animation_data.drivers):
        if not curve.data_path.endswith(".custom_shape_scale"):
            continue
        path = curve.data_path + "_xyz"
        rig.path_resolve(path)  # Validate the replacement before touching it.
        curve.data_path = path
        for axis in (1, 2):
            new = rig.driver_add(path, axis).driver
            old = curve.driver
            new.type, new.expression, new.use_self = old.type, old.expression, old.use_self
            for variable in list(new.variables):
                new.variables.remove(variable)
            for variable in old.variables:
                value = new.variables.new()
                value.type, value.name = variable.type, variable.name
                for a, b in zip(value.targets, variable.targets):
                    for prop in b.bl_rna.properties:
                        if not prop.is_readonly and prop.identifier != "rna_type":
                            setattr(a, prop.identifier, getattr(b, prop.identifier))


def _skin_map():
    result = {"pelvis": "root.x", "spine1": "spine_01.x", "spine2": "spine_01.x",
              "spine3": "spine_02.x", "neck": "neck.x", "head": "head.x"}
    for side, suffix in (("left", "l"), ("right", "r")):
        for joint, deform in (("hip", "thigh_stretch"), ("knee", "leg_stretch"), ("ankle", "foot"),
                              ("foot", "toes_01"), ("collar", "shoulder"), ("shoulder", "arm_stretch"),
                              ("elbow", "forearm_stretch"), ("wrist", "hand")):
            result[f"{side}_{joint}"] = f"{deform}.{suffix}"
        for finger in ("index", "middle", "ring", "pinky", "thumb"):
            for segment in (1, 2, 3):
                prefix = "" if segment == 1 else "c_"
                result[f"{side}_{finger}{segment}"] = f"{prefix}{finger}{segment}.{suffix}"
    return result


def _copy_body(context, source, rig, collection):
    mapping = _skin_map()
    for name in mapping.values():
        if name not in rig.data.bones or not rig.data.bones[name].use_deform:
            raise ValueError("ARP 变形骨骼不可用：" + name)
    bodies = [obj for obj in context.scene.objects if obj.type == "MESH" and obj.find_armature() == source]
    for original in bodies:
        if original.data.shape_keys:
            raise ValueError("带形态键的身体暂不支持自动转换，请使用标准身体")
        duplicate = original.copy()
        duplicate.data = original.data.copy()
        collection.objects.link(duplicate)
        duplicate.name = original.name + " · ARP"
        duplicate.animation_data_clear()
        world = original.matrix_world.copy()
        duplicate.parent, duplicate.parent_type = rig, "OBJECT"
        duplicate.matrix_parent_inverse = rig.matrix_world.inverted()
        duplicate.matrix_world = world
        duplicate.hide_set(False)
        duplicate.hide_viewport = duplicate.hide_render = False
        for modifier in list(duplicate.modifiers):
            if modifier.type == "ARMATURE":
                duplicate.modifiers.remove(modifier)
        duplicate.vertex_groups.clear()
        groups = {name: duplicate.vertex_groups.new(name=name) for name in dict.fromkeys(mapping.values())}
        original_groups = {group.index: mapping.get(group.name) for group in original.vertex_groups}
        for vertex in original.data.vertices:
            values = {}
            for item in vertex.groups:
                name = original_groups.get(item.group)
                if name:
                    values[name] = values.get(name, 0) + item.weight
            if not values:
                raise ValueError("身体有未绑定到标准骨骼的顶点，未转换")
            for name, weight in values.items():
                groups[name].add([vertex.index], weight, "REPLACE")
        modifier = duplicate.modifiers.new("ARP 骨骼", "ARMATURE")
        modifier.object = rig
    return len(bodies)


def create_character(context, obj):
    source = source_rig(obj)
    preset = human_preset(context)
    if source.library or max(source.scale) - min(source.scale) > 1e-4 or min(source.scale) <= 0:
        raise ValueError("请先将标准身体本地化，并应用非均匀缩放")
    old_active, old_selected = context.view_layer.objects.active, list(context.selected_objects)
    old_mode = old_active.mode if old_active else "OBJECT"
    scene = context.scene
    frame, subframe = scene.frame_current, scene.frame_subframe
    snapshots = {kind: set(getattr(bpy.data, kind)) for kind in ("objects", "collections", "meshes", "armatures", "actions")}
    hidden = {o: (o.hide_get(), o.hide_render) for o in [source, *source.children]}
    try:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        collection = bpy.data.collections.new(source.name + " · ARP 可编辑角色")
        scene.collection.children.link(collection)
        rig_collection = bpy.data.collections.new(source.name + "_grp_rig")
        collection.children.link(rig_collection)
        with bpy.data.libraries.load(str(preset), link=False) as (_src, dst):
            dst.objects = ["rig"]
        rig = dst.objects[0]
        if rig is None or rig.type != "ARMATURE":
            raise ValueError("ARP human 预设没有可用骨架")
        rig_collection.objects.link(rig)
        rig.name = source.name + " · ARP"
        rig.parent = None
        rig.matrix_world = source.matrix_world.copy()
        # ARP stores FK/IK blending and stretch on drivers in animation_data.
        # Detach only the preset Action; clearing animation_data breaks skinning.
        if rig.animation_data:
            rig.animation_data.action = None
        _migrate_shape_drivers(rig)
        rig.hide_set(False)
        for selected in context.selected_objects:
            selected.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        _fit_references(context, source, rig)
        if not bpy.ops.arp.match_to_rig.poll():
            raise ValueError("ARP 无法识别刚创建的 human 参考骨架")
        result = bpy.ops.arp.match_to_rig('EXEC_DEFAULT')
        if 'FINISHED' not in result or not rig.data.get("has_match_to_rig"):
            raise ValueError("ARP 匹配装备未完成，请检查 ARP 安装")
        bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.update()
        bodies = _copy_body(context, source, rig, collection)
        mappings = rigging.match_bones(rig)
        rigging.bake_to_rig(context, source, rig, mappings)
        # Unlike retargeting to an unrelated character, this duplicate keeps the
        # generated person's absolute first-frame position, including floating.
        first = source["flowhmr_frame_start"]
        scene.frame_set(int(first), subframe=first - int(first))
        offset = source.pose.bones["pelvis"].matrix.translation - rig.pose.bones["c_root_master.x"].matrix.translation
        for curve in motion._curves(rig):
            if curve.data_path == 'pose.bones["c_root_master.x"].location':
                # This controller's location is expressed in its parent frame.
                parent = rig.pose.bones["c_root_master.x"].parent
                basis = (parent.matrix @ parent.bone.matrix_local.inverted() @ rig.data.bones["c_root_master.x"].matrix_local
                         if parent else rig.data.bones["c_root_master.x"].matrix_local)
                shift = basis.to_3x3().inverted() @ offset
                for key in curve.keyframe_points:
                    key.co.y += shift[curve.array_index]
                curve.update()
        rig["flowhmr_arp_character"] = True
        rig["flowhmr_source_object"] = source.name
        rig["flowhmr_arp_body_count"] = bodies
        for original in hidden:
            original.hide_set(True)
            original.hide_render = True
        scene.frame_set(frame, subframe=subframe)
        context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        return rig
    except Exception:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for kind in ("objects", "collections", "meshes", "armatures", "actions"):
            blocks = getattr(bpy.data, kind)
            for block in list(set(blocks) - snapshots[kind]):
                blocks.remove(block, do_unlink=True)
        for original, (value, render) in hidden.items():
            original.hide_set(value)
            original.hide_render = render
        scene.frame_set(frame, subframe=subframe)
        for selected in context.selected_objects:
            selected.select_set(False)
        for selected in old_selected:
            selected.select_set(True)
        context.view_layer.objects.active = old_active
        if old_active and old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode=old_mode)
        raise


class FLOWHMR_OT_CreateARPCharacter(bpy.types.Operator):
    bl_idname = "flowhmr.create_arp_character"
    bl_label = "创建 ARP 可编辑角色"
    bl_description = "使用已启用的 Auto-Rig Pro 创建控制骨架、复制身体并转移动作；原动捕结果保留，可撤销"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.flowhmr
        if s.busy:
            return {"CANCELLED"}
        try:
            rig = create_character(context, context.active_object)
            s.target_model = rig
            s.message = "已创建 ARP 可编辑角色，选中控制器即可编辑；原结果已隐藏，仍保留在原集合"
            s.last_error = False
        except Exception as error:
            s.message, s.last_error = str(error), True
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (FLOWHMR_OT_CreateARPCharacter,)
