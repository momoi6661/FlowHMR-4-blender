"""Expanded native plugin panel and a separate, uncluttered video player."""

import bpy
from . import workflow, preview

_FILTERS = []
_ABSENT = object()
_HEADER_DRAW = None


def _player_header(self, context):
    if not is_popup(context):
        return _HEADER_DRAW(self, context)
    s, layout = context.scene.flowhmr, self.layout
    layout.use_property_decorate = False
    row = layout.row(align=True)
    if s.source_is_image:
        row.label(text="图片 · 姿势预览", icon="IMAGE_DATA")
    else:
        row.operator("flowhmr.preview_range", text="", icon="REW").action = "home"
        row.operator("screen.animation_play", text="", icon="PAUSE" if context.screen.is_animation_playing else "PLAY")
        progress = row.row()
        progress.ui_units_x = 9
        progress.prop(s, "panel_position", text="", slider=True)
        edit = layout.row(align=True)
        from .video_ui import _enabled
        edit.enabled = _enabled(s, "start")
        edit.operator("flowhmr.preview_range", text="起点", icon="PREV_KEYFRAME").action = "start"
        edit.operator("flowhmr.preview_range", text="终点", icon="NEXT_KEYFRAME").action = "end"
        edit.popover(panel="FLOWHMR_PT_clip_range", text="选段", icon="PREVIEW_RANGE")
    layout.separator_spacer()
    if s.status == "waiting_user":
        row = layout.row(align=True)
        row.operator("flowhmr.edit_person_box", text="修正框", icon="SELECT_SET", depress=s.manual_box_mode).person = s.box_person_index
        row.operator("flowhmr.reset_person_boxes", text="", icon="LOOP_BACK")
        confirm = row.row()
        confirm.enabled = not s.busy and any(p.selected for p in s.people)
        confirm.operator("flowhmr.confirm_people", text="确认人物", icon="CHECKMARK")
    layout.operator("clip.view_all", text="", icon="VIEWZOOM")


def _refresh_panels(classes):
    ordered = []
    remaining = list(classes)
    while remaining:
        pending_ids = {cls.__name__ for cls in remaining}
        available = [cls for cls in remaining if getattr(cls, "bl_parent_id", "") not in pending_ids]
        if not available:
            raise RuntimeError("无法刷新视频窗口面板")
        ordered.extend(available)
        remaining = [cls for cls in remaining if cls not in available]
    for cls in reversed(ordered):
        bpy.utils.unregister_class(cls)
    for cls in ordered:
        bpy.utils.register_class(cls)


def is_popup(context):
    return bool(context.screen and context.screen.get("flowhmr_popup"))


class FLOWHMR_OT_OpenPopup(bpy.types.Operator):
    bl_idname = "flowhmr.open_popup"
    bl_label = "打开素材预览"
    bl_description = "放大图片或播放视频、检查和修正人物框；参数在插件面板设置"

    def execute(self, context):
        if bpy.app.background:
            self.report({"ERROR"}, "动捕小窗需要 Blender 图形界面")
            return {"CANCELLED"}
        existing = next((w for w in context.window_manager.windows
                         if w.screen.get("flowhmr_popup") and w.scene == context.scene), None)
        if existing:
            preview.fit_video(context.scene)
            self.report({"INFO"}, "播放器已打开")
            return {"FINISHED"}
        windows = set(context.window_manager.windows)
        # Blender's own duplicate-area operator opens a native utility window.
        # The current area and the user's original screen stay intact.
        result = bpy.ops.screen.area_dupli("INVOKE_DEFAULT")
        created = [w for w in context.window_manager.windows if w not in windows]
        if not created:
            self.report({"ERROR"}, "无法打开动捕小窗，请从 3D 视图重试")
            return {"CANCELLED"}
        window = created[0]
        window.screen["flowhmr_popup"] = True
        window.scene = context.scene
        area = window.screen.areas[0]
        area.type = "CLIP_EDITOR"
        space = area.spaces.active
        space.show_region_toolbar = False
        space.show_region_header = True
        space.show_region_ui = False
        space.show_track_path = space.show_names = False
        space.show_gizmo = False
        space.clip = context.scene.flowhmr.preview_clip
        context.scene.sync_mode = "FRAME_DROP"
        if context.scene.flowhmr.video_path and context.scene.flowhmr.status != "done":
            bpy.ops.flowhmr.preview_range()
        def ready():
            if window not in bpy.context.window_manager.windows[:]:
                return None
            with bpy.context.temp_override(window=window, area=area):
                preview.fit_video(window.scene)
                preview.sync_video(window.scene)
            return None
        bpy.app.timers.register(ready, first_interval=0.3)
        return {"FINISHED"}


class FLOWHMR_OT_OpenControls(bpy.types.Operator):
    bl_idname = "flowhmr.open_controls"
    bl_label = "打开 FlowHMR 插件面板"
    bl_description = "在 3D 视图侧栏打开动捕面板，所有操作集中在分页中"

    def execute(self, context):
        from . import operators
        s = operators._settings(context)
        if not s.busy and not s.history:
            bpy.ops.flowhmr.refresh_history()
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
        if area is None:
            self.report({"ERROR"}, "请在主窗口的 3D 视图侧栏打开 FlowHMR")
            return {"CANCELLED"}
        area.spaces.active.show_region_ui = True
        # Blender exposes active_panel_category as read-only. Keep the native
        # sidebar navigation instead of raising from a deferred callback.
        area.tag_redraw()
        self.report({"INFO"}, "已打开 3D 视图侧栏，请点击 FlowHMR 标签")
        return {"FINISHED"}


class FLOWHMR_PT_Popup(bpy.types.Panel):
    bl_idname = "FLOWHMR_PT_popup"
    bl_label = "FlowHMR 动捕"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "UI"
    # An uncategorized panel is visible immediately, including when Blender's
    # native Metadata panel keeps the Footage category selected.
    bl_category = ""
    bl_order = -100

    @classmethod
    def poll(cls, context):
        return is_popup(context)

    def draw(self, context):
        layout = self.layout
        layout.operator("flowhmr.open_controls", text="主窗口插件面板", icon="MENU_PANEL")


class FLOWHMR_PT_ClipRange(bpy.types.Panel):
    bl_idname = "FLOWHMR_PT_clip_range"
    bl_label = "提取的视频片段"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "HEADER"
    bl_ui_units_x = 18

    def draw(self, context):
        s, layout = context.scene.flowhmr, self.layout
        layout.prop(s, "start_time")
        end = layout.column()
        end.enabled = not s.to_video_end
        end.prop(s, "end_time")
        layout.prop(s, "to_video_end")
        layout.operator("flowhmr.preview_range", text="定位选中片段", icon="PREVIEW_RANGE")
        layout.operator("flowhmr.preview_range", text="恢复完整视频", icon="LOOP_BACK").action = "reset"
        layout.label(text="起点包含当前帧，终点包含当前帧")


CLASSES = (FLOWHMR_OT_OpenPopup, FLOWHMR_OT_OpenControls, FLOWHMR_PT_Popup, FLOWHMR_PT_ClipRange)


def register():
    global _HEADER_DRAW
    _HEADER_DRAW = bpy.types.CLIP_HT_header.draw
    bpy.types.CLIP_HT_header.draw = _player_header
    # Clip Editor normally opens its built-in Footage/Track/Solve tabs. Hide
    # those only in our utility screen, so FlowHMR becomes the visible tab.
    for name in dir(bpy.types):
        if not name.startswith("CLIP_PT_"):
            continue
        cls = getattr(bpy.types, name)
        if getattr(cls, "bl_space_type", "") != "CLIP_EDITOR" or getattr(cls, "bl_region_type", "") != "UI":
            continue
        original = cls.__dict__.get("poll", _ABSENT)
        descriptor = next((base.__dict__["poll"] for base in cls.__mro__ if "poll" in base.__dict__), None)
        previous = descriptor.__func__ if isinstance(descriptor, classmethod) else descriptor
        # Read the Python descriptor directly: RNA bound methods dispatch back
        # through the class and would recursively call the replacement.
        def make_poll(old):
            def visible(panel_cls, context):
                if is_popup(context):
                    return False
                return old(panel_cls, context) if old else True
            return visible
        replacement = classmethod(make_poll(previous))
        cls.poll = replacement
        _FILTERS.append((cls, original, replacement))
    _refresh_panels([cls for cls, _original, _replacement in _FILTERS])


def unregister():
    global _HEADER_DRAW
    if bpy.types.CLIP_HT_header.draw is _player_header:
        bpy.types.CLIP_HT_header.draw = _HEADER_DRAW
    _HEADER_DRAW = None
    restored = []
    for cls, original, replacement in reversed(_FILTERS):
        if cls.__dict__.get("poll") is replacement:
            if original is _ABSENT:
                delattr(cls, "poll")
            else:
                cls.poll = original
            restored.append(cls)
    _refresh_panels(restored)
    _FILTERS.clear()
