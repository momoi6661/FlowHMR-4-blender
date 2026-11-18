"""Import a target character with Blender's own importers, keeping inference separate."""

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import operators


class FLOWHMR_OT_ImportCharacter(bpy.types.Operator, ImportHelper):
    bl_idname = "flowhmr.import_character"
    bl_label = "导入目标角色"
    bl_description = "导入自己的绑定角色；单骨架角色会自动设为动画目标，多骨架可手动选择"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: StringProperty(default="*.fbx;*.glb;*.gltf;*.blend;*.vrm", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, "flowhmr") and not context.scene.flowhmr.busy

    def execute(self, context):
        s = operators._settings(context)
        path = Path(bpy.path.abspath(self.filepath)).resolve()
        before = set(bpy.data.objects)
        try:
            if not path.is_file():
                raise ValueError("角色文件不存在")
            suffix = path.suffix.lower()
            if suffix == ".blend":
                # Append data only: do not open the file or replace the user's scene.
                with bpy.data.libraries.load(str(path), link=False) as (_source, data):
                    data.objects = _source.objects
                collection = bpy.data.collections.new(path.stem)
                context.scene.collection.children.link(collection)
                for obj in data.objects:
                    if obj:
                        collection.objects.link(obj)
            else:
                importer = {".fbx": "fbx", ".glb": "gltf", ".gltf": "gltf", ".vrm": "vrm"}.get(suffix)
                if importer is None:
                    raise ValueError("请选择 FBX、GLB、glTF、blend 或 VRM 角色")
                operation = getattr(bpy.ops.import_scene, importer)
                try:
                    operation.get_rna_type()
                except (KeyError, AttributeError, RuntimeError):
                    raise ValueError("请先启用 Blender 的 VRM 导入插件" if suffix == ".vrm" else "Blender 对应格式的导入器未启用")
                if "FINISHED" not in operation(filepath=str(path)):
                    raise ValueError("角色导入未完成")
            context.view_layer.update()
            imported = [obj for obj in bpy.data.objects if obj not in before and obj.name in context.view_layer.objects]
            rigs = [obj for obj in imported if obj.type == "ARMATURE"]
            if len(rigs) == 1:
                target = rigs[0]
                s.target_rig = target
                s.target_mode = "TARGET"
                context.view_layer.objects.active = target
                target.select_set(True)
                s.message = f"已导入角色 {path.stem}，动画目标：{target.name}；已自动匹配 {sum(bool(m.target) for m in s.bone_maps)} / 52 根骨骼"
            else:
                s.target_rig = None
                s.target_mode = "NEW"
                s.message = (f"已导入 {len(rigs)} 套骨架，请在右侧选择目标骨架" if rigs else
                             "角色已导入，但未找到骨架；请先为角色绑定骨骼")
            s.last_error = False
        except Exception as error:
            s.message, s.last_error = str(error), True
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (FLOWHMR_OT_ImportCharacter,)
