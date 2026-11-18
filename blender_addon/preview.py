"""Native media playback synchronized with imported motion."""

import math
import json
from pathlib import Path

import bpy
import blf
from bpy.app.handlers import persistent

_DRAW_HANDLES = []


def update_ground(_settings, context):
    for window in context.window_manager.windows:
        if window.scene == context.scene:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def ground_lines(scene):
    """Reference-only grid, expressed in scene units; never touches animation."""
    s = scene.flowhmr
    unit = 1 / max(scene.unit_settings.scale_length, 1e-8)
    z = s.preview_ground_height * unit
    objects = [scene.objects.get(name) for name in json.loads(s.preview_result_roots)]
    obj = next((obj for obj in objects if obj), None)
    center = obj.matrix_world.translation if obj else (0, 0, 0)
    x, y = round(center[0] / unit) * unit, round(center[1] / unit) * unit
    radius = 3 * unit
    lines = []
    for index in range(-6, 7):
        offset = index * unit / 2
        lines.extend(((x - radius, y + offset, z), (x + radius, y + offset, z),
                      (x + offset, y - radius, z), (x + offset, y + radius, z)))
    return lines


def _draw_ground():
    context = bpy.context
    if not hasattr(context.scene, "flowhmr") or context.scene.flowhmr.preview_result_roots == "[]" or not context.space_data.overlay.show_overlays:
        return
    if not context.scene.flowhmr.preview_ground:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": ground_lines(context.scene)})
    old_blend, old_depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        shader.bind()
        shader.uniform_float("color", (0.55, 0.62, 0.68, 0.35))
        batch.draw(shader)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.blend_set(old_blend)


def _draw_label(kind):
    context = bpy.context
    if not context.screen.get("flowhmr_popup") or not hasattr(context.scene, "flowhmr"):
        return
    s = context.scene.flowhmr
    if kind == "video":
        title = "原图片" if s.source_is_image else "原视频"
        detail = ("点击人物框选人" if s.status == "waiting_user" else
                  s.media_resolution if s.source_is_image else
                  f"{max(0, video_time(context.scene)):.2f} 秒" if s.preview_clip else "上传视频或图片后在这里预览")
    else:
        title = "动作预览"
        detail = "与原视频同步播放" if s.preview_result_roots != "[]" else "提取完成后在这里显示角色动作"
    if bpy.app.version < (4, 0, 0):
        blf.size(0, 16, 72)
    else:
        blf.size(0, 16)
    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 5, 0, 0, 0, 0.8)
    blf.shadow_offset(0, 1, -1)
    blf.color(0, 1, 1, 1, 1)
    for i, text in enumerate((title, detail)):
        blf.position(0, 18, context.region.height - 30 - i * 24, 0)
        blf.draw(0, text)
    blf.disable(0, blf.SHADOW)


def _areas(scene=None):
    for window in bpy.context.window_manager.windows:
        if window.screen.get("flowhmr_popup") and (scene is None or window.scene == scene):
            for area in window.screen.areas:
                yield window, area


def video_time(scene):
    s = scene.flowhmr
    fps = scene.render.fps / scene.render.fps_base
    return s.preview_origin_seconds + (scene.frame_current_final - s.preview_frame_start) / fps


@persistent
def sync_video(scene, _depsgraph=None):
    if not hasattr(scene, "flowhmr"):
        return
    s = scene.flowhmr
    clip = s.preview_clip
    if clip is None:
        return
    if s.video_duration:
        s["panel_position"] = max(0, min(1, video_time(scene) / s.video_duration))
    frame = max(1, min(clip.frame_duration, round(video_time(scene) * clip.fps) + 1))
    for _window, area in _areas(scene):
        if area.type == "CLIP_EDITOR" and area.spaces.active.clip == clip:
            area.spaces.active.clip_user.frame_current = frame
            area.tag_redraw()


def fit_video(scene):
    for window, area in _areas(scene):
        if area.type == "CLIP_EDITOR" and area.spaces.active.clip:
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.clip.view_all(fit_view=True)


def _fit_timeline(scene):
    for window, area in _areas(scene):
        if area.type == "DOPESHEET_EDITOR":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.action.view_all()


def attach_video(scene, path, *, reset_timing=True):
    path = bpy.path.abspath(str(path))
    if not Path(path).is_file():
        raise FileNotFoundError("原视频不存在，请重新选择素材")
    clip = bpy.data.movieclips.load(path, check_existing=True)
    s = scene.flowhmr
    s.preview_clip = clip
    if reset_timing:
        s.preview_origin_seconds, s.preview_frame_start = 0, s.import_frame
        duration = 0 if s.source_is_image else (s.video_duration or clip.frame_duration / max(clip.fps, 1e-6))
        first = s.import_frame
        last = first + max(1, math.ceil(duration * scene.render.fps / scene.render.fps_base)) - 1
        scene.frame_end = max(scene.frame_end, last)
        scene.frame_start = min(scene.frame_start, first)
        scene.use_preview_range = True
        scene.frame_preview_start, scene.frame_preview_end = first, last
        scene.frame_set(first)
    for _window, area in _areas(scene):
        if area.type == "CLIP_EDITOR":
            area.spaces.active.clip = clip
    sync_video(scene)
    fit_video(scene)


def show_result(context, sources, visible):
    scene, s = context.scene, context.scene.flowhmr
    first = min(sources, key=lambda a: a["flowhmr_frame_start"])
    s.preview_origin_seconds = first.get("flowhmr_source_start", 0) / 30
    s.preview_frame_start = first["flowhmr_frame_start"]
    scene.use_preview_range = True
    scene.frame_preview_start = math.floor(s.preview_frame_start)
    scene.frame_preview_end = math.ceil(max(a["flowhmr_frame_start"] +
        (a["flowhmr_frames"] - 1) * a["flowhmr_frame_step"] for a in sources))
    scene.frame_set(scene.frame_preview_start)
    s.preview_result_roots = json.dumps([o.name for o in visible])
    _frame_objects(context)
    # Utility-window users keep working in their existing Blender scene.
    for window in context.window_manager.windows:
        if window.scene != scene or window.screen.get("flowhmr_popup"):
            continue
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    with context.temp_override(window=window, area=area, region=region):
                        bpy.ops.view3d.view_selected(use_all_regions=False)
    _fit_timeline(scene)
    sync_video(scene)


def _frame_objects(context):
    scene, s = context.scene, context.scene.flowhmr
    visible = [context.view_layer.objects.get(name) for name in json.loads(s.preview_result_roots)]
    visible = [obj for obj in visible if obj]
    roots = set(visible)
    objects = set(roots)
    for obj in context.view_layer.objects:
        if obj.type == "MESH" and obj.find_armature() in roots:
            objects.add(obj)
    if visible:
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in objects:
            obj.select_set(True)
    if visible:
        context.view_layer.objects.active = visible[0]
    for window, area in _areas(scene):
        if area.type != "VIEW_3D":
            continue
        space = area.spaces.active
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if not region:
            continue
        with context.temp_override(window=window, area=area, region=region):
            if space.local_view is None:
                if not context.selected_objects:
                    seed = next((o for o in context.view_layer.objects if o.visible_get()), None)
                    if seed:
                        seed.select_set(True)
                    else:
                        continue
                bpy.ops.view3d.localview(frame_selected=False)
            for obj in context.view_layer.objects:
                obj.local_view_set(space, obj in objects)
            space.overlay.show_overlays = s.preview_show_bones or not any(o.type == "MESH" for o in objects)
            if visible:
                for obj in objects:
                    if obj.visible_get():
                        obj.select_set(True)
                bpy.ops.view3d.view_selected(use_all_regions=False)


def update_overlays(settings, context):
    for _window, area in _areas(context.scene):
        if area.type == "VIEW_3D":
            area.spaces.active.overlay.show_overlays = settings.preview_show_bones


def register():
    if sync_video not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(sync_video)
    if not _DRAW_HANDLES:
        _DRAW_HANDLES.append((bpy.types.SpaceView3D,
            bpy.types.SpaceView3D.draw_handler_add(_draw_ground, (), "WINDOW", "POST_VIEW")))
        for kind, space in (("video", bpy.types.SpaceClipEditor), ("motion", bpy.types.SpaceView3D)):
            _DRAW_HANDLES.append((space, space.draw_handler_add(_draw_label, (kind,), "WINDOW", "POST_PIXEL")))


def unregister():
    for space, handle in _DRAW_HANDLES:
        space.draw_handler_remove(handle, "WINDOW")
    _DRAW_HANDLES.clear()
    if sync_video in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(sync_video)


class FLOWHMR_OT_PreviewRange(bpy.types.Operator):
    bl_idname = "flowhmr.preview_range"
    bl_label = "预览片段"
    action: bpy.props.StringProperty(default="play")
    def execute(self, context):
        from .ranges import frame_range
        scene, s = context.scene, context.scene.flowhmr
        if self.action in {"start", "end"}:
            from .video_ui import mark_range
            try:
                mark_range(scene, self.action, video_time(scene))
            except ValueError as error:
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            return {"FINISHED"}
        if self.action == "reset":
            from .video_ui import _enabled
            if s.source_is_image or not _enabled(s, "reset"):
                return {"CANCELLED"}
            s.start_time, s.end_time, s.to_video_end, s.use_time_range = 0, s.video_duration, True, True
            from .operators import _remember_media
            _remember_media(s)
        if self.action == "home":
            scene.frame_set(scene.frame_preview_start if scene.use_preview_range else scene.frame_start)
            return {"FINISHED"}
        fps = scene.render.fps / scene.render.fps_base
        now = max(0, min(s.video_duration, video_time(scene)))
        try:
            first, last = frame_range(s)
            end = (last + 1) / 30 if last is not None else s.video_duration
            start = first / 30
            a = s.preview_frame_start + (start - s.preview_origin_seconds) * fps
            b = s.preview_frame_start + (end - s.preview_origin_seconds) * fps
            scene.use_preview_range = True
            scene.frame_preview_start, scene.frame_preview_end = math.floor(a), max(math.floor(a), math.ceil(b) - 1)
            scene.frame_set(scene.frame_preview_start)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (FLOWHMR_OT_PreviewRange,)
