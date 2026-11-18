"""Native clip-editor overlays: click people, drag a range and generate in place."""

import bisect
import math

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

_PEOPLE = {}
_KEYMAPS = []
_HANDLES = []
_DRAFTS = {}


def _ui_scale(context):
    return max(1.0, context.preferences.system.ui_scale)


def set_people(scene, job):
    _PEOPLE[scene.as_pointer()] = job.get("candidates") or []
    scene.flowhmr.box_edits_json = "[]"
    scene.flowhmr.manual_box_mode = False
    for _window, area in _areas(scene):
        area.tag_redraw()


def _areas(scene):
    from .preview import _areas
    return _areas(scene)


def box_at(candidate, frame, edits=()):
    """Interpolate the same 30 FPS detection boxes used in the Web workspace."""
    frames, boxes = candidate.get("frames", []), candidate.get("boxes", [])
    if not frames or len(frames) != len(boxes) or frame < frames[0] - 2 or frame > frames[-1] + 2:
        return None
    anchors = sorted((e for e in edits if e["person"] == candidate["index"]), key=lambda e: e["frame"])
    whole = next((e for e in reversed(anchors) if e.get("whole_track")), None)
    if whole:
        return whole["box"]
    if anchors and anchors[0]["frame"] <= frame <= anchors[-1]["frame"]:
        high = bisect.bisect_left([e["frame"] for e in anchors], frame)
        if high == 0:
            return anchors[0]["box"]
        a, b = anchors[high - 1], anchors[high]
        t = (frame - a["frame"]) / max(1, b["frame"] - a["frame"])
        return [x + (y - x) * t for x, y in zip(a["box"], b["box"])]
    high = bisect.bisect_left(frames, frame)
    if high == 0:
        return boxes[0]
    if high >= len(frames):
        return boxes[-1]
    low = high - 1
    factor = (frame - frames[low]) / max(1, frames[high] - frames[low])
    return [a + (b - a) * factor for a, b in zip(boxes[low], boxes[high])]


def boxes(context):
    import json
    from .preview import video_time
    s = context.scene.flowhmr
    clip = s.preview_clip
    if not clip or s.status != "waiting_user":
        return []
    width, height = clip.size
    if not width or not height:
        return []
    frame = 0 if s.source_is_image else max(0, video_time(context.scene) * 30)
    result = []
    for candidate in _PEOPLE.get(context.scene.as_pointer(), []):
        box = box_at(candidate, frame, json.loads(s.box_edits_json))
        if box is None:
            continue
        x1, y1, x2, y2 = box
        view = context.region.view2d
        left, top = view.view_to_region(x1 / width, 1 - y1 / height, clip=False)
        right, bottom = view.view_to_region(x2 / width, 1 - y2 / height, clip=False)
        result.append((candidate["index"], (left, bottom, right, top)))
    return result


def _text(x, y, text, size=13, color=(0.92, 0.92, 0.92, 1)):
    if bpy.app.version < (4, 0):
        blf.size(0, size, 72)
    else:
        blf.size(0, size)
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _shape(vertices, color, mode="TRIS"):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, mode, {"pos": vertices})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _rect(x, y, width, height, color):
    _shape([(x, y), (x + width, y), (x + width, y + height),
            (x, y), (x + width, y + height), (x, y + height)], color)


def box_handle(rect, point, tolerance=10):
    """Prefer resize grips, then edges, then moving the box interior."""
    left, bottom, right, top = rect
    x, y = point
    grips = (("nw", left, top), ("ne", right, top), ("se", right, bottom), ("sw", left, bottom),
             ("n", (left + right) / 2, top), ("s", (left + right) / 2, bottom),
             ("w", left, (bottom + top) / 2), ("e", right, (bottom + top) / 2))
    for name, gx, gy in grips:
        if abs(x - gx) <= tolerance and abs(y - gy) <= tolerance:
            return name
    if left <= x <= right and abs(y - top) <= tolerance:
        return "n"
    if left <= x <= right and abs(y - bottom) <= tolerance:
        return "s"
    if bottom <= y <= top and abs(x - left) <= tolerance:
        return "w"
    if bottom <= y <= top and abs(x - right) <= tolerance:
        return "e"
    return "move" if left <= x <= right and bottom <= y <= top else "draw"


def adjust_box(rect, anchor, point, mode, bounds, minimum=(4, 4)):
    left, bottom, right, top = rect
    bx1, by1, bx2, by2 = bounds
    x = max(bx1, min(bx2, point[0]))
    y = max(by1, min(by2, point[1]))
    if mode == "draw":
        return (min(anchor[0], x), min(anchor[1], y), max(anchor[0], x), max(anchor[1], y))
    if mode == "move":
        width, height = right - left, top - bottom
        left = max(bx1, min(bx2 - width, left + x - anchor[0]))
        bottom = max(by1, min(by2 - height, bottom + y - anchor[1]))
        return left, bottom, left + width, bottom + height
    if "w" in mode:
        left = max(bx1, min(right - minimum[0], x))
    if "e" in mode:
        right = min(bx2, max(left + minimum[0], x))
    if "s" in mode:
        bottom = max(by1, min(top - minimum[1], y))
    if "n" in mode:
        top = min(by2, max(bottom + minimum[1], y))
    return left, bottom, right, top


def _box_grips(rect, scale):
    left, bottom, right, top = rect
    for x in (left, (left + right) / 2, right):
        for y in (bottom, (bottom + top) / 2, top):
            if x == (left + right) / 2 and y == (bottom + top) / 2:
                continue
            _rect(x - 5 * scale, y - 5 * scale, 10 * scale, 10 * scale, (.15, .4, .9, 1))
            _rect(x - 3 * scale, y - 3 * scale, 6 * scale, 6 * scale, (1, 1, 1, 1))


def controls(context):
    # Native header buttons handle playback; never paint buttons on the footage.
    return []


def bar(context):
    scale = _ui_scale(context)
    return (24 * scale, 40 * scale, max(1, context.region.width - 48 * scale), 8 * scale)


def _range(s):
    from .ranges import frame_range
    try:
        first, last = frame_range(s)
        return first / 30, (last + 1) / 30 if last is not None else s.video_duration
    except ValueError:
        return 0, s.video_duration


def _enabled(s, action):
    from .operators import ACTIVE
    if action == "play":
        return bool(s.preview_clip)
    if action == "confirm":
        return not s.busy and any(p.selected for p in s.people)
    return not s.busy and s.status not in ACTIVE


def mark_range(scene, action, seconds):
    """Mark canonical 30 FPS frames. The selected end includes that frame."""
    s = scene.flowhmr
    if s.source_is_image or not _enabled(s, action) or s.video_duration <= 0:
        raise ValueError("请在开始识别前选择视频片段")
    duration = s.video_duration
    frame = min(math.ceil(duration * 30 - 1e-8) - 1, max(0, math.floor(seconds * 30 + 1e-6)))
    if action == "start":
        end = duration if s.to_video_end else min(s.end_time, duration)
        s.start_time = max(0, min(frame / 30, (math.ceil(end * 30 - 1e-8) - 1) / 30))
    elif action == "end":
        s.to_video_end = False
        s.end_time = min(duration, max((frame + 1) / 30, s.start_time + 1 / 30))
    else:
        raise ValueError("未知片段标记")
    s.use_time_range = True
    from .operators import _remember_media, _redraw
    _remember_media(s)
    _redraw()


def draw_timeline(context):
    s = context.scene.flowhmr
    if s.source_is_image or not s.video_duration or not s.preview_clip:
        return
    from .preview import video_time
    x, y, width, height = bar(context)
    scale = _ui_scale(context)
    first, last = _range(s)
    start_x = x + width * first / s.video_duration
    end_x = x + width * last / s.video_duration
    play_x = x + width * min(1, max(0, video_time(context.scene) / s.video_duration))
    _rect(x, y, width, height, (0.10, 0.12, 0.15, .95))
    _rect(start_x, y, max(1, end_x - start_x), height, (.12, .62, .46, .95))
    for handle in (start_x, end_x):
        _rect(handle - 4 * scale, y - 5 * scale, 8 * scale, height + 10 * scale, (.22, .87, .64, 1))
    _rect(play_x - scale, y - 6 * scale, 2 * scale, height + 12 * scale, (.95, .96, 1, 1))
    _text(x, y + 24 * scale, f"选段 {first:.2f}–{last:.2f} 秒 · 拖动两端选择片段，点击横条定位", 12 * scale)


def draw():
    context = bpy.context
    if (not context.screen.get("flowhmr_popup") or not hasattr(context.scene, "flowhmr")
            or context.region.type != "WINDOW"):
        return
    s = context.scene.flowhmr
    scale = _ui_scale(context)
    gpu.state.blend_set("ALPHA")
    try:
        draw_timeline(context)
        selected = {p.person_index for p in s.people if p.selected}
        draft = _DRAFTS.get(context.scene.as_pointer())
        active_rect = None
        for index, rect in boxes(context):
            editing = s.manual_box_mode and index == s.box_person_index
            left, bottom, right, top = draft if editing and draft else rect
            if editing:
                active_rect = (left, bottom, right, top)
            color = (0.12, 0.84, 0.55, 1) if index in selected else (1, 0.62, 0.2, 1)
            if editing:
                color = (.3, .7, 1, 1)
            points = [(left, bottom), (right, bottom), (right, top), (left, top), (left, bottom)]
            gpu.state.line_width_set(2)
            _shape([p for pair in zip(points, points[1:]) for p in pair], color, "LINES")
            _rect(left, top, 105 * scale, 23 * scale, (*color[:3], 0.95))
            _text(left + 5 * scale, top + 5 * scale,
                  f"人物 {index + 1}" + ("  已选" if index in selected else "  点选"), 12 * scale, (0.06, 0.06, 0.06, 1))
        if draft and active_rect is None:
            x1, y1, x2, y2 = draft
            points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
            _shape([p for pair in zip(points, points[1:]) for p in pair], (0.3, 0.7, 1, 1), "LINES")
        if active_rect:
            _box_grips(active_rect, scale)
        if s.manual_box_mode:
            _text(16, 20, "拖框内移动，拖角点 / 边缘缩放，框外重画；Esc 取消当前拖动", 13 * scale)
        elif s.source_is_image and s.status == "waiting_user":
            _text(16, 20, "单人也可点选或重画人物框，确认后提取一帧姿势", 13 * scale)

    finally:
        gpu.state.line_width_set(1)
        gpu.state.blend_set("NONE")


def seek(scene, seconds):
    s = scene.flowhmr
    fps = scene.render.fps / scene.render.fps_base
    value = s.preview_frame_start + (seconds - s.preview_origin_seconds) * fps
    whole = math.floor(value)
    scene.frame_set(whole, subframe=value - whole)


def select_person(context, index):
    s = context.scene.flowhmr
    for person in s.people:
        if person.person_index == index:
            person.selected = not person.selected
            s.box_person_index = index
    for _window, area in _areas(context.scene):
        area.tag_redraw()


class FLOWHMR_OT_VideoClick(bpy.types.Operator):
    bl_idname = "flowhmr.video_click"
    bl_label = "视频片段与人物选择"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return (context.area and context.area.type == "CLIP_EDITOR" and context.screen.get("flowhmr_popup")
                and hasattr(context.scene, "flowhmr"))

    def invoke(self, context, event):
        s = context.scene.flowhmr
        if context.region.type != "WINDOW":
            return {"PASS_THROUGH"}
        x, y = event.mouse_region_x, event.mouse_region_y
        if not s.source_is_image and s.preview_clip and s.video_duration:
            bx, by, width, height = bar(context)
            scale = _ui_scale(context)
            if bx - 10 * scale <= x <= bx + width + 10 * scale and by - 10 * scale <= y <= by + height + 12 * scale:
                if context.screen.is_animation_playing:
                    bpy.ops.screen.animation_play()
                first, last = _range(s)
                handles = {"start": bx + width * first / s.video_duration, "end": bx + width * last / s.video_duration}
                closest = min(handles, key=lambda key: abs(x - handles[key]))
                self.drag_range = closest if _enabled(s, closest) and abs(x - handles[closest]) < 12 * scale else "seek"
                self.original_range = (s.start_time, s.end_time, s.to_video_end, s.use_time_range)
                from .preview import video_time
                self.original_time = video_time(context.scene)
                self._drag_timeline(context, x)
                context.window_manager.modal_handler_add(self)
                return {"RUNNING_MODAL"}
        if s.manual_box_mode and s.status == "waiting_user" and not s.busy:
            if not s.preview_clip or min(s.preview_clip.size) <= 0:
                return {"PASS_THROUGH"}
            if context.screen.is_animation_playing:
                bpy.ops.screen.animation_play()
            current = next((rect for index, rect in boxes(context) if index == s.box_person_index), None)
            view = context.region.view2d
            a, b = view.view_to_region(0, 0, clip=False), view.view_to_region(1, 1, clip=False)
            self.box_bounds = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            if not (self.box_bounds[0] <= x <= self.box_bounds[2] and self.box_bounds[1] <= y <= self.box_bounds[3]):
                return {"PASS_THROUGH"}
            self.drag_box = True
            self.box_mode = box_handle(current, (x, y), 10 * _ui_scale(context)) if current else "draw"
            self.original_box = current or (x, y, x, y)
            self.box_person = s.box_person_index
            from .preview import video_time
            self.box_frame = 0 if s.source_is_image else math.floor(video_time(context.scene) * 30 + 1e-4)
            self.box_moved = False
            self.anchor = (x, y)
            _DRAFTS[context.scene.as_pointer()] = self.original_box
            context.window.cursor_modal_set("SCROLL_XY" if self.box_mode == "move" else "CROSSHAIR")
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        if s.status == "waiting_user" and not s.busy:
            hit = [(i, r) for i, r in boxes(context) if r[0] <= x <= r[2] and r[1] <= y <= r[3] + 23]
            if hit:
                index = min(hit, key=lambda pair: (pair[1][2] - pair[1][0]) * (pair[1][3] - pair[1][1]))[0]
                select_person(context, index)
                return {"FINISHED"}
        return {"PASS_THROUGH"}

    def modal(self, context, event):
        s = context.scene.flowhmr
        if getattr(self, "drag_range", None):
            if event.type in {"ESC", "RIGHTMOUSE"}:
                s.start_time, s.end_time, s.to_video_end, s.use_time_range = self.original_range
                seek(context.scene, self.original_time)
                from .operators import _remember_media, _redraw
                _remember_media(s)
                _redraw()
                return {"CANCELLED"}
            if event.type == "MOUSEMOVE":
                self._drag_timeline(context, event.mouse_region_x)
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self._drag_timeline(context, event.mouse_region_x)
                return {"FINISHED"}
            return {"RUNNING_MODAL"}
        from .preview import video_time
        current_frame = 0 if s.source_is_image else math.floor(video_time(context.scene) * 30 + 1e-4)
        if (event.type in {"ESC", "RIGHTMOUSE"} or s.status != "waiting_user" or s.busy
                or s.box_person_index != self.box_person or current_frame != self.box_frame or not s.manual_box_mode):
            _DRAFTS.pop(context.scene.as_pointer(), None)
            context.window.cursor_modal_restore()
            context.area.tag_redraw()
            return {"CANCELLED"}
        if event.type in {"MOUSEMOVE", "LEFTMOUSE"}:
            point = (event.mouse_region_x, event.mouse_region_y)
            self.box_moved |= math.dist(self.anchor, point) >= 2 * _ui_scale(context)
            width, height = s.preview_clip.size
            minimum = (max(4 * _ui_scale(context), 4 * (self.box_bounds[2] - self.box_bounds[0]) / width),
                       max(4 * _ui_scale(context), 4 * (self.box_bounds[3] - self.box_bounds[1]) / height))
            _DRAFTS[context.scene.as_pointer()] = adjust_box(
                self.original_box, self.anchor, point, self.box_mode, self.box_bounds, minimum)
            context.area.tag_redraw()
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            rect = _DRAFTS.pop(context.scene.as_pointer(), (*self.anchor, event.mouse_region_x, event.mouse_region_y))
            context.window.cursor_modal_restore()
            if not self.box_moved:
                context.area.tag_redraw()
                return {"FINISHED"}
            try:
                record_box(context, self.box_person, rect)
            except ValueError as error:
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            # Keep the grips visible for another move or resize of the same box.
            context.area.tag_redraw()
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _drag_timeline(self, context, x):
        s = context.scene.flowhmr
        bx, _by, width, _height = bar(context)
        seconds = max(0, min(1, (x - bx) / width)) * s.video_duration
        if self.drag_range != "seek":
            mark_range(context.scene, self.drag_range, seconds)
        seek(context.scene, min(seconds, max(0, s.video_duration - 1 / 30)))
        context.area.tag_redraw()


def record_box(context, person, rect):
    import json
    from .preview import video_time
    s = context.scene.flowhmr
    candidate = next((c for c in _PEOPLE.get(context.scene.as_pointer(), []) if c["index"] == person), None)
    if candidate is None or not s.preview_clip:
        raise ValueError("请先检测人物")
    frame = 0 if s.source_is_image else max(0, math.floor(video_time(context.scene) * 30 + 1e-4))
    frames = candidate["frames"]
    if not frames or not frames[0] <= frame <= frames[-1]:
        raise ValueError("当前帧没有该人物，请定位到人物出现的位置")
    width, height = s.preview_clip.size
    a = context.region.view2d.region_to_view(rect[0], rect[1])
    b = context.region.view2d.region_to_view(rect[2], rect[3])
    x1, x2 = sorted(max(0, min(width, p[0] * width)) for p in (a, b))
    y1, y2 = sorted(max(0, min(height, (1 - p[1]) * height)) for p in (a, b))
    if x2 - x1 < 4 or y2 - y1 < 4:
        raise ValueError("人物框太小，请完整框住头、手和脚")
    edits = json.loads(s.box_edits_json)
    edits = [e for e in edits if e["person"] != person or (not s.source_is_image and e["frame"] != frame)]
    edits.append(dict(person=person, frame=frame, box=[x1, y1, x2, y2], whole_track=s.source_is_image))
    s.box_edits_json = json.dumps(edits)
    for item in s.people:
        if item.person_index == person:
            item.selected = True
    s.message = "图片人物框已修正，确认后提取姿势" if s.source_is_image else "已修正当前帧；多个修正帧之间插值，范围外保留原检测框"


class FLOWHMR_OT_EditPersonBox(bpy.types.Operator):
    bl_idname = "flowhmr.edit_person_box"
    bl_label = "修正人物框"
    bl_description = "拖动框体移动、拉角点或边缘缩放，框外拖动重画；图片修正整张，视频修正当前帧"
    person: bpy.props.IntProperty(default=0, min=0)

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "flowhmr", None)
        return s is not None and not s.busy and s.status == "waiting_user"

    def execute(self, context):
        s = context.scene.flowhmr
        if s.manual_box_mode and s.box_person_index == self.person:
            s.manual_box_mode = False
            for _window, area in _areas(context.scene):
                area.tag_redraw()
            return {"FINISHED"}
        s.box_person_index, s.manual_box_mode = self.person, True
        if context.area.type != "CLIP_EDITOR":
            bpy.ops.flowhmr.open_popup()
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
        return {"FINISHED"}


class FLOWHMR_OT_ResetPersonBoxes(bpy.types.Operator):
    bl_idname = "flowhmr.reset_person_boxes"
    bl_label = "撤销人物框修正"

    def execute(self, context):
        context.scene.flowhmr.box_edits_json = "[]"
        context.scene.flowhmr.manual_box_mode = False
        for _window, area in _areas(context.scene):
            area.tag_redraw()
        return {"FINISHED"}


def register():
    _HANDLES.append(bpy.types.SpaceClipEditor.draw_handler_add(draw, (), "WINDOW", "POST_PIXEL"))
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        keymap = keyconfig.keymaps.new(name="Clip", space_type="CLIP_EDITOR", region_type="WINDOW")
        item = keymap.keymap_items.new("flowhmr.video_click", "LEFTMOUSE", "PRESS", head=True)
        _KEYMAPS.append((keymap, item))


def unregister():
    for handle in _HANDLES:
        bpy.types.SpaceClipEditor.draw_handler_remove(handle, "WINDOW")
    _HANDLES.clear()
    for keymap, item in _KEYMAPS:
        keymap.keymap_items.remove(item)
    _KEYMAPS.clear()
    _PEOPLE.clear()
    _DRAFTS.clear()


CLASSES = (FLOWHMR_OT_VideoClick, FLOWHMR_OT_EditPersonBox, FLOWHMR_OT_ResetPersonBoxes)

if hasattr(bpy.types, "FileHandler"):
    class FLOWHMR_FH_Media(bpy.types.FileHandler):
        bl_idname = "FLOWHMR_FH_media"
        bl_label = "FlowHMR 素材"
        bl_import_operator = "flowhmr.select_video"
        bl_file_extensions = ".mp4;.mov;.avi;.mkv;.webm;.m4v;.jpg;.jpeg;.jfif;.png;.webp;.bmp;.tif;.tiff"

        @classmethod
        def poll_drop(cls, context):
            from .operators import ACTIVE
            if not context.area or not hasattr(context.scene, "flowhmr"):
                return False
            if context.scene.flowhmr.busy or context.scene.flowhmr.status in ACTIVE:
                return False
            player = (context.area.type == "CLIP_EDITOR"
                      and context.screen.get("flowhmr_popup"))
            sidebar = (context.area.type == "VIEW_3D" and context.region and context.region.type == "UI"
                       and context.region.active_panel_category == "FlowHMR")
            return bool(player or sidebar)

    CLASSES += (FLOWHMR_FH_Media,)
