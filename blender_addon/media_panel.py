"""Static cover images only. Video playback belongs to the native player."""

import bisect
import json
import time

import bpy
import bpy.utils.previews

_ICONS = None
_FRAMES = {}
_PLAYING = {}


def _frames(settings):
    value = settings.panel_frames_json
    if value not in _FRAMES:
        try:
            _FRAMES[value] = json.loads(value)
        except ValueError:
            _FRAMES[value] = []
    return _FRAMES[value]


def set_frames(scene, frames):
    _PLAYING.pop(scene.as_pointer(), None)
    scene.flowhmr.panel_frames_json = json.dumps(frames)
    scene.flowhmr.panel_position = 0


def position_changed(settings, context):
    if context.scene and settings.video_duration:
        from .video_ui import seek
        seek(context.scene, min(settings.panel_position * settings.video_duration,
                                max(0, settings.video_duration - 1 / 30)))
    from .operators import _redraw
    _redraw()


def _tick():
    now = time.monotonic()
    scenes = {scene.as_pointer(): scene for scene in bpy.data.scenes}
    for key, previous in list(_PLAYING.items()):
        scene = scenes.get(key)
        if scene is None:
            _PLAYING.pop(key, None)
            continue
        s = scene.flowhmr
        end = s.video_duration if s.to_video_end else min(s.end_time, s.video_duration)
        seconds = s.panel_position * s.video_duration + now - previous
        if seconds >= end or seconds < s.start_time:
            seconds = s.start_time
        s.panel_position = seconds / max(s.video_duration, 1 / 30)
        _PLAYING[key] = now
    return 0.2 if _PLAYING else None


def draw_cover(layout, context, *, scale=4):
    global _ICONS
    s = context.scene.flowhmr
    frames = _frames(s)
    if not frames:
        return
    if _ICONS is None:
        _ICONS = bpy.utils.previews.new()
    path = frames[0]["path"]
    if path not in _ICONS:
        try:
            _ICONS.load(path, path, "IMAGE")
        except Exception:
            layout.label(text="预览缓存失效，请重新选择素材", icon="INFO")
            return
    row = layout.row()
    row.alignment = "CENTER"
    row.template_icon(icon_value=_ICONS[path].icon_id, scale=scale)


def draw(layout, context):
    draw_cover(layout, context)
    layout.operator("flowhmr.open_popup", text="打开播放器", icon="PLAY")


class FLOWHMR_OT_PanelPreview(bpy.types.Operator):
    bl_idname = "flowhmr.panel_preview"
    bl_label = "面板素材预览"
    action: bpy.props.StringProperty(default="play")

    def execute(self, context):
        s = context.scene.flowhmr
        if not s.video_duration:
            return {"CANCELLED"}
        if self.action == "play":
            _PLAYING.pop(context.scene.as_pointer(), None)
            return bpy.ops.flowhmr.open_popup()
        elif self.action in {"start", "end"}:
            from .operators import ACTIVE
            if s.busy or s.status in ACTIVE:
                return {"CANCELLED"}
            seconds = s.panel_position * s.video_duration
            if self.action == "start":
                end = s.video_duration if s.to_video_end else s.end_time
                s.start_time = max(0, min(seconds, end - 1 / 30))
            else:
                s.to_video_end = False
                s.end_time = min(s.video_duration, max(seconds, s.start_time + 1 / 30))
            s.use_time_range = True
        context.area.tag_redraw()
        return {"FINISHED"}


def unregister():
    global _ICONS
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _PLAYING.clear()
    _FRAMES.clear()
    if _ICONS is not None:
        bpy.utils.previews.remove(_ICONS)
        _ICONS = None


CLASSES = (FLOWHMR_OT_PanelPreview,)
