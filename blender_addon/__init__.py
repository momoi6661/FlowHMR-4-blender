"""Blender panels for the independent FlowHMR source backend."""

import json

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Panel, PropertyGroup

from . import backend, operators, workflow, preview, video_ui, popup, media_panel, characters, arp_character
from .ranges import frame_range

bl_info = {
    "name": "FlowHMR 动捕",
    "author": "FlowHMR contributors",
    "version": (0, 8, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > FlowHMR",
    "description": "导入视频或图片，提取动作或姿势并添加到角色骨架",
    "category": "Animation",
}


class FLOWHMR_Settings(PropertyGroup):
    video_path: StringProperty(name="视频", subtype="FILE_PATH")
    video_fps: FloatProperty(name="原始 FPS", default=0, min=0)
    video_duration: FloatProperty(name="时长", default=0, min=0)
    video_frames: IntProperty(name="原始帧数", default=0, min=0)
    backend_root: StringProperty(name="FlowHMR 源码目录", subtype="DIR_PATH")
    python_executable: StringProperty(name="后端 Python（留空自动查找）", subtype="FILE_PATH")
    output_path: StringProperty(name="保存位置", subtype="FILE_PATH")
    host: StringProperty(name="地址", default="127.0.0.1")
    port: IntProperty(name="端口", default=60550, min=1, max=65535)
    device: StringProperty(name="设备", default="cuda")
    use_time_range: BoolProperty(name="按秒选择片段", default=True)
    to_video_end: BoolProperty(name="处理到片尾", default=True)
    start_time: FloatProperty(name="开始（秒）", default=0, min=0, precision=3)
    end_time: FloatProperty(name="结束（秒）", description="不包含结束时间点，例如 2–5 秒提取 90 个处理帧", default=5, min=0, precision=3)
    start_frame: IntProperty(name="起始帧", default=0, min=0)
    end_frame: IntProperty(name="结束帧（含）", default=149, min=0)
    static_camera: BoolProperty(name="固定机位", default=True)
    sam_auto_batch: BoolProperty(name="按可用显存自动决定批次", default=True)
    sam_chunk_size: IntProperty(name="每批帧数", default=16, min=1, max=128)
    job_id: StringProperty(name="任务 ID")
    status: StringProperty(name="状态", default="idle")
    progress: FloatProperty(name="进度", default=0, min=0, max=1, subtype="FACTOR")
    message: StringProperty(name="消息")
    actual_range: StringProperty(name="实际轨迹")
    busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    last_error: BoolProperty(default=False, options={"SKIP_SAVE"})
    connected: BoolProperty(default=False, options={"SKIP_SAVE"})
    runtime_ready: BoolProperty(default=False, options={"SKIP_SAVE"})
    models_ready: BoolProperty(default=False, options={"SKIP_SAVE"})
    models_loaded: BoolProperty(default=False, options={"SKIP_SAVE"})
    model_summary: StringProperty(default="[]", options={"SKIP_SAVE"})
    gpu_summary: StringProperty(default="连接后可查看资源状态", options={"SKIP_SAVE"})
    auto_start_backend: BoolProperty(name="生成时自动启动后端", default=True)
    auto_release_vram: BoolProperty(name="结束后释放显存", description="保留旧工程兼容；当前由用户手动释放模型", default=False)
    vram_released: BoolProperty(default=False, options={"SKIP_SAVE"})


class FLOWHMR_Preferences(AddonPreferences):
    bl_idname = __package__
    backend_root: StringProperty(name="默认源码目录", subtype="DIR_PATH")
    python_executable: StringProperty(name="默认 Python（留空使用 .venv）", subtype="FILE_PATH")

    def draw(self, context):
        draw_environment(self.layout, context.scene.flowhmr)


class FLOWHMR_OT_OpenSettings(bpy.types.Operator):
    bl_idname = "flowhmr.open_settings"
    bl_label = "环境与模型设置"
    bl_description = "打开插件偏好设置，查看环境、模型和显存状态"

    def execute(self, context):
        operators._settings(context)
        bpy.ops.preferences.addon_show(module=__package__)
        return {"FINISHED"}


class FLOWHMR_PT_Main(Panel):
    bl_label = "FlowHMR 动捕"
    bl_idname = "FLOWHMR_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FlowHMR"

    def draw(self, context):
        workflow.draw_panel(self, context)


def draw_environment(layout, s):
    layout.use_property_decorate = False
    connection = layout.column()
    connection.enabled = not s.busy and s.status not in operators.ACTIVE
    connection.prop(s, "backend_root")
    connection.prop(s, "python_executable")
    row = connection.row(align=True)
    row.prop(s, "host")
    row.prop(s, "port")
    connection.prop(s, "device")
    connection.prop(s, "auto_start_backend")
    row = connection.row(align=True)
    row.operator("flowhmr.start_backend", icon="PLAY")
    row.operator("flowhmr.check_environment", icon="FILE_REFRESH")
    if s.connected:
        layout.label(text="运行环境：" + ("已就绪" if s.runtime_ready else "未就绪"))
        layout.label(text="推理模型：" + ("已就绪" if s.models_ready else "需要补齐"))
        try:
            for name, state, blocking in json.loads(s.model_summary):
                layout.label(text=f"{name} · {state}", icon="ERROR" if blocking else "INFO")
        except ValueError:
            pass
    layout.separator()
    layout.label(text=s.gpu_summary)
    layout.label(text="AI 模型：" + ("已加载，可复用" if s.models_loaded else "未加载"))
    layout.label(text="AI 模型常驻显存，手动释放后下次重新加载")
    col = layout.column()
    col.enabled = not s.busy and s.status not in operators.ACTIVE
    col.operator("flowhmr.release_vram", text="手动释放 AI 模型", icon="MEMORY")
    stop = layout.row()
    stop.enabled = not s.stopping_backend
    stop.operator("flowhmr.stop_backend", text="正在退出后端…" if s.stopping_backend else "退出后端 / 中断所有任务", icon="QUIT")


CLASSES = (
    *workflow.PROPERTY_CLASSES,
    FLOWHMR_Settings, FLOWHMR_Preferences, FLOWHMR_OT_OpenSettings,
    operators.FLOWHMR_OT_SelectVideo, operators.FLOWHMR_OT_StartBackend,
    operators.FLOWHMR_OT_CheckEnvironment, operators.FLOWHMR_OT_Submit,
    operators.FLOWHMR_OT_Poll, operators.FLOWHMR_OT_Cancel, operators.FLOWHMR_OT_OpenWeb,
    operators.FLOWHMR_OT_ReleaseVRAM, operators.FLOWHMR_OT_StopBackend,
    operators.FLOWHMR_OT_DownloadNPZ, *operators.EXTRA_CLASSES, *preview.CLASSES, *video_ui.CLASSES, *media_panel.CLASSES,
    FLOWHMR_PT_Main, *popup.CLASSES, *characters.CLASSES, *arp_character.CLASSES,
    *workflow.PANELS,
)


def register():
    FLOWHMR_Settings.__annotations__.update(workflow.SETTINGS)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.flowhmr = PointerProperty(type=FLOWHMR_Settings)
    operators._ENABLED = True
    preview.register()
    video_ui.register()
    popup.register()


def unregister():
    popup.unregister()
    media_panel.unregister()
    video_ui.unregister()
    preview.unregister()
    operators.shutdown()
    # Unregistering UI is also used during addon updates. Model lifetime belongs
    # to the shared backend; only the explicit Stop/Release operators end it.
    if hasattr(bpy.types.Scene, "flowhmr"):
        del bpy.types.Scene.flowhmr
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
