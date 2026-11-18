"""Nonblocking Blender operators for the source backend bridge."""

from pathlib import Path
import json
import queue
import threading
from types import SimpleNamespace

import bpy
import bpy.utils.previews
from bpy.props import StringProperty, CollectionProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper

from .backend import base_url, probe_video, prepare_media, start_backend, stop_backend, wait_ready
from .client import FlowHMRClient
from .ranges import frame_range
from .media_types import is_image_job
from . import motion, rigging, preview, video_ui

ACTIVE = {"queued", "running", "waiting_user", "cancel_requested", "uploading"}
_EVENTS = queue.Queue()
_ENABLED = False
_WATCHED_SCENE = None
_CLEANUP_POLLS = 0
_PREVIEWS = None
_HISTORY_PREVIEWS = None
_REQUESTS = {}


def _settings(context):
    settings = context.scene.flowhmr
    settings.auto_release_vram = False
    # Keep the normal generation flow on the neutral FlowHMR skeleton. Older
    # .blend files may still contain the removed ARP output mode.
    if settings.target_mode == "ARP":
        settings.target_mode = "NEW"
    prefs_entry = context.preferences.addons.get(__package__)
    prefs = prefs_entry.preferences if prefs_entry else None
    if not settings.backend_root:
        root = (prefs.backend_root if prefs else "")
        # The add-on is distributed independently from the source project.
        # Never infer a backend checkout from the ZIP's build path. An already
        # running backend only needs host/port; a backend started by the add-on
        # must be configured explicitly in Preferences.
        if root:
            settings.backend_root = root
    if not settings.python_executable and prefs:
        settings.python_executable = prefs.python_executable
    return settings


def _snapshot(settings):
    return SimpleNamespace(**{name: getattr(settings, name) for name in
        ("backend_root", "python_executable", "host", "port", "device")})


def _client(config, timeout=10):
    return FlowHMRClient(config.host, config.port, timeout=timeout)


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "CLIP_EDITOR", "PROPERTIES"}:
                area.tag_redraw()


def _launch(settings, work, done, *, replace=False):
    if settings.busy and not replace:
        raise RuntimeError("请等待当前请求完成")
    key, token = settings.as_pointer(), object()
    _REQUESTS[key] = token
    settings.busy = True

    def run():
        try:
            _EVENTS.put((settings, key, token, done, work(), None))
        except Exception as error:
            _EVENTS.put((settings, key, token, done, None, str(error)))

    threading.Thread(target=run, name="FlowHMR request", daemon=True).start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1)


def _pump():
    if not _ENABLED:
        return None
    while True:
        try:
            settings, key, token, done, result, error = _EVENTS.get_nowait()
        except queue.Empty:
            break
        try:
            if _REQUESTS.get(key) is not token:
                continue  # A shutdown superseded this in-flight request.
            _REQUESTS.pop(key, None)
            settings.busy = False
            if error:
                settings.message = error
                settings.last_error = True
            else:
                settings.last_error = False
                done(settings, result)
        except ReferenceError:
            pass
        except Exception as failure:
            settings.busy = False
            settings.last_error = True
            settings.message = str(failure)
    _redraw()
    # Keep this inexpensive pump running while the add-on is enabled.
    return 0.2


def _show_info(settings, info):
    import json
    settings.connected = True
    settings.runtime_ready = bool(info.get("runtime", {}).get("ready"))
    items = info.get("model_files", {}).get("items", [])
    settings.models_ready = bool(items) and not any(item.get("blocking") for item in items)
    settings.model_summary = json.dumps(
        [(item["name"], item["status"], item.get("blocking", False)) for item in items],
        ensure_ascii=False)
    settings.message = ("后端已连接，可以提交任务" if settings.runtime_ready and settings.models_ready
                        else "后端已连接；请补齐模型或运行环境后生成")
    if not settings.job_id:
        settings.status = "ready"
    _show_gpu(settings, {"models_loaded": info.get("models_loaded"), "memory": info.get("gpu_memory", {})})


def _show_gpu(settings, result):
    memory = result.get("memory") or {}
    settings.vram_released = not result.get("models_loaded", False)
    if memory.get("initialized") and "free" in memory:
        settings.gpu_summary = f"GPU 可用 {memory['free']/1024**3:.2f} / {memory['total']/1024**3:.2f} GiB"
    else:
        settings.gpu_summary = "后端尚未创建 CUDA 上下文"
    settings.models_loaded = bool(result.get("models_loaded"))


def _show_job(settings, job):
    if is_image_job(job):
        settings.source_is_image = True
    settings.status = job.get("status", "unknown")
    settings.progress = min(1.0, max(0.0, float(job.get("progress") or 0) / 100.0))
    settings.message = job.get("error") or job.get("message") or ""
    settings.last_error = settings.status == "failed"
    settings.result_frames = (1 if settings.source_is_image and job.get("num_frames")
                              else int(job.get("num_frames") or 0))
    settings.job_detail = json.dumps(job, ensure_ascii=False)
    settings.models_loaded = bool(job.get("models_loaded", settings.models_loaded))
    track = job.get("track_range")
    if settings.source_is_image:
        settings.actual_range = "图片 · 单帧姿势"
    elif track:
        settings.actual_range = f"实际轨迹 {track[0]}–{track[1]}（30 FPS）"
    if settings.status == "done":
        scene_people = (job.get("scene") or {}).get("people", [])
        people_json = json.dumps(scene_people, ensure_ascii=False)
        if people_json != settings.result_people_json:
            settings.result_people_json = people_json
            settings.result_person = "ALL"
        settings.message = "图片姿势已提取，可添加到角色" if settings.source_is_image else "动作已生成，可直接导入 Blender"
        if settings.models_loaded:
            settings.message += "；AI 模型已保留，可复用"
        if settings.auto_import_pending:
            settings.auto_import_pending = False
            if settings.auto_import:
                if settings.target_rig and len(scene_people) > 1:
                    settings.message = "多人动作已生成，请选择结果人物，再添加到角色"
                else:
                    _start_import(settings)
    elif settings.status in {"failed", "cancelled"}:
        settings.auto_import_pending = False
    if settings.status == "waiting_user" and job.get("candidates"):
        _show_people(settings, job)


def _poll_timer():
    global _WATCHED_SCENE, _CLEANUP_POLLS
    if not _ENABLED or _WATCHED_SCENE is None:
        return None
    try:
        settings = _WATCHED_SCENE.flowhmr
        cleanup_pending = (settings.status in {"done", "failed", "cancelled"}
                           and settings.auto_release_vram and not settings.vram_released
                           and _CLEANUP_POLLS < 15)
        if not settings.job_id or (settings.status not in ACTIVE and not cleanup_pending):
            _WATCHED_SCENE = None
            return None
        if not settings.busy:
            config, job_id = _snapshot(settings), settings.job_id
            if cleanup_pending:
                _CLEANUP_POLLS += 1
                _launch(settings, lambda: _client(config, timeout=3).gpu(), _show_gpu)
            else:
                _launch(settings, lambda: _client(config, timeout=3).job(job_id), _show_job)
    except (ReferenceError, AttributeError):
        _WATCHED_SCENE = None
        return None
    return 1.0


def schedule_poll(scene):
    global _WATCHED_SCENE, _CLEANUP_POLLS
    _WATCHED_SCENE = scene
    _CLEANUP_POLLS = 0
    if not bpy.app.timers.is_registered(_poll_timer):
        bpy.app.timers.register(_poll_timer, first_interval=0.5)


def shutdown():
    global _ENABLED, _WATCHED_SCENE, _PREVIEWS, _HISTORY_PREVIEWS
    _ENABLED, _WATCHED_SCENE = False, None
    _REQUESTS.clear()
    for timer in (_pump, _poll_timer):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    while not _EVENTS.empty():
        _EVENTS.get_nowait()
    if _PREVIEWS is not None:
        bpy.utils.previews.remove(_PREVIEWS)
        _PREVIEWS = None
    if _HISTORY_PREVIEWS is not None:
        bpy.utils.previews.remove(_HISTORY_PREVIEWS)
        _HISTORY_PREVIEWS = None


class FLOWHMR_OT_SelectVideo(bpy.types.Operator, ImportHelper):
    bl_idname = "flowhmr.select_video"
    bl_label = "选择视频或图片"
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    media_type: EnumProperty(items=[("ALL", "全部素材", ""), ("VIDEO", "视频", ""),
        ("IMAGE", "图片", "")], default="ALL", options={"HIDDEN", "SKIP_SAVE"})
    image_resolution: EnumProperty(name="图片尺寸", items=[("AUTO", "自动适配", "等比缩放，兼顾速度与内存"),
        ("ORIGINAL", "保留原图尺寸", "保留像素尺寸，处理时间与内存占用更高")], default="AUTO", options={"SKIP_SAVE"})
    filter_glob: StringProperty(default="*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v;*.jpg;*.jpeg;*.jfif;*.png;*.webp;*.bmp;*.tif;*.tiff", options={"HIDDEN", "SKIP_SAVE"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        # Only explicit drop/command arguments bypass the picker. Blender can
        # restore a previous operator's filepath as a ghost property.
        if (self.filepath and self.properties.is_property_set("filepath", ghost=False)) or (self.directory and self.files):
            return self.execute(context)
        images = "*.jpg;*.jpeg;*.jfif;*.png;*.webp;*.bmp;*.tif;*.tiff"
        videos = "*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v"
        self.filter_glob = images if self.media_type == "IMAGE" else videos if self.media_type == "VIDEO" else videos + ";" + images
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        if self.media_type != "VIDEO":
            self.layout.prop(self, "image_resolution")

    def execute(self, context):
        settings = _settings(context)
        if settings.busy or settings.status in ACTIVE:
            self.report({"WARNING"}, "请等待或停止当前任务后切换素材")
            return {"CANCELLED"}
        paths = [str(Path(self.directory) / item.name) for item in self.files] if self.files else [self.filepath]
        paths = list(dict.fromkeys(str(Path(bpy.path.abspath(path)).resolve()) for path in paths if path))
        if not paths:
            return {"CANCELLED"}
        config, scene, window = _snapshot(settings), context.scene, context.window
        resolution = self.image_resolution
        _remember_media(settings)

        def work():
            results, errors = [], []
            for path in paths:
                try:
                    results.append((path, prepare_media(config, path, image_resolution=resolution)))
                except Exception as error:
                    errors.append(f"{Path(path).name}: {error}")
            if not results:
                raise ValueError("；".join(errors))
            return results, errors

        def done(s, result):
            results, errors = result
            for path, info in results:
                item = next((entry for entry in s.media if entry.source_path == path), None)
                if item is None:
                    item = s.media.add()
                    item.source_path, item.name = path, Path(path).name
                    item.end_time = info["duration"]
                item.prepared_path, item.is_image = info["path"], info["is_image"]
                item.fps, item.frames, item.duration = info["fps"], info["frames"], info["duration"]
                item.width, item.height = info.get("width", 0), info.get("height", 0)
                item.source_width, item.source_height = info.get("source_width", item.width), info.get("source_height", item.height)
                item.warning = info.get("warning", "")
                item.image_resolution = info.get("image_resolution", "AUTO")
                item.preview_json = json.dumps(info.get("preview_frames", []))
                item.start_time = min(item.start_time, max(0, item.duration - 1 / 30))
                item.end_time = min(item.duration, max(item.end_time, item.start_time + 1 / 30))
            s.media_index = next(i for i, item in enumerate(s.media) if item.source_path == results[0][0])
            _adopt_media(s, s.media[s.media_index], scene, window)
            s.message = f"已导入 {len(results)} 个素材；在播放器中预览，在左侧开始 AI 识别"
            if errors:
                s.message += "；跳过：" + "；".join(errors)

        _launch(settings, work, done)
        settings.message = "正在导入素材和读取封面…"
        return {"FINISHED"}


def _remember_media(settings):
    item = next((entry for entry in settings.media if entry.source_path == settings.source_path), None)
    if item:
        item.start_time, item.end_time, item.to_video_end = settings.start_time, settings.end_time, settings.to_video_end


def _adopt_media(s, item, scene, window):
    from . import media_panel
    # Validate before changing the current source; removing a local file should
    # leave the previously usable material and its task intact.
    if not Path(item.prepared_path).is_file():
        raise FileNotFoundError("素材文件已移动，请重新导入")
    if window:
        with bpy.context.temp_override(window=window, scene=scene):
            preview.attach_video(scene, item.prepared_path, reset_timing=False)
    else:
        preview.attach_video(scene, item.prepared_path, reset_timing=False)
    s.source_name, s.source_path, s.source_is_image = item.name, item.source_path, item.is_image
    s.media_resolution = (f"原图 {item.source_width} × {item.source_height} → 识别 {item.width} × {item.height}"
                          if item.is_image else f"视频 {item.width} × {item.height}") if item.width else ""
    s.media_warning = item.warning
    if item.is_image:
        s.image_resolution = item.image_resolution
    s.video_path = item.prepared_path
    s.video_fps, s.video_duration, s.video_frames = item.fps, item.duration, item.frames
    s.start_time, s.end_time, s.to_video_end = item.start_time, item.end_time, item.to_video_end
    s.use_time_range = True
    s.output_path = ""
    s.job_id, s.status, s.progress = "", "idle", 0
    s.job_detail, s.result_people_json = "", "[]"
    s.result_person = "ALL"
    s.auto_import_pending = False
    s.preview_result_roots = "[]"
    s.preview_origin_seconds, s.preview_frame_start = 0, scene.frame_current
    s.people.clear()
    s.people_signature = ""
    video_ui.set_people(scene, {})
    media_panel.set_frames(scene, json.loads(item.preview_json))
    preview.sync_video(scene)


class FLOWHMR_OT_OpenMedia(bpy.types.Operator):
    bl_idname = "flowhmr.open_media"
    bl_label = "使用所选素材"

    def execute(self, context):
        s = _settings(context)
        if s.busy or s.status in ACTIVE or not s.media or s.media_index >= len(s.media):
            return {"CANCELLED"}
        try:
            _remember_media(s)
            _adopt_media(s, s.media[s.media_index], context.scene, context.window)
            s.message, s.last_error = "素材已切换，可打开播放器或开始 AI 识别", False
        except Exception as error:
            s.message, s.last_error = str(error), True
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWHMR_OT_RemoveMedia(bpy.types.Operator):
    bl_idname = "flowhmr.remove_media"
    bl_label = "从素材列表移除"
    bl_description = "仅移除列表中的引用，保留磁盘原视频和任务历史"
    current: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        s = _settings(context)
        if self.current:
            index = next((i for i, item in enumerate(s.media) if item.source_path == s.source_path), None)
            if index is None:
                return {"CANCELLED"}
            if not s.busy and s.status not in ACTIVE:
                s.media_index = index
        if s.busy or s.status in ACTIVE or not s.media or s.media_index >= len(s.media):
            return {"CANCELLED"}
        removed_current = s.media[s.media_index].source_path == s.source_path
        _remember_media(s)
        s.media.remove(s.media_index)
        s.media_index = min(s.media_index, max(0, len(s.media) - 1))
        if removed_current:
            if s.media:
                try:
                    _adopt_media(s, s.media[s.media_index], context.scene, context.window)
                except (ValueError, OSError):
                    _clear_media(context.scene)
            else:
                _clear_media(context.scene)
        s.message = "已从列表移除；磁盘素材和任务历史保留"
        return {"FINISHED"}


def _clear_media(scene):
    from . import media_panel
    s = scene.flowhmr
    s.source_path = s.source_name = s.video_path = ""
    s.preview_clip = None
    s.source_is_image = False
    s.media_resolution = s.media_warning = ""
    s.video_duration = s.video_fps = 0
    s.video_frames = 0
    s.job_id, s.status, s.progress = "", "idle", 0
    s.job_detail, s.result_people_json = "", "[]"
    s.result_person = "ALL"
    s.auto_import_pending = False
    s.people.clear()
    s.people_signature = ""
    media_panel.set_frames(scene, [])
    video_ui.set_people(scene, {})
    for _window, area in preview._areas(scene):
        if area.type == "CLIP_EDITOR":
            area.spaces.active.clip = None


class FLOWHMR_OT_StartBackend(bpy.types.Operator):
    bl_idname = "flowhmr.start_backend"
    bl_label = "启动 / 连接后端"

    def execute(self, context):
        settings = _settings(context)
        config = _snapshot(settings)

        def work():
            start_backend(config)
            return wait_ready(config)

        _launch(settings, work, _show_info)
        settings.message = "正在连接后端…"
        return {"FINISHED"}


class FLOWHMR_OT_CheckEnvironment(bpy.types.Operator):
    bl_idname = "flowhmr.check_environment"
    bl_label = "刷新环境与模型状态"

    def execute(self, context):
        settings = _settings(context)
        config = _snapshot(settings)
        _launch(settings, lambda: _client(config).info(), _show_info)
        return {"FINISHED"}


class FLOWHMR_OT_Submit(bpy.types.Operator):
    bl_idname = "flowhmr.submit"
    bl_label = "提取所选区间的动作"
    bl_description = "使用素材页当前配置创建新任务；重新生成时保留旧任务和动画，重新检查人物框"
    rerun_current: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        settings = _settings(context)
        try:
            path = bpy.path.abspath(settings.video_path)
            if not settings.video_path or not Path(path).is_file():
                raise ValueError("请先选择有效的视频文件")
            if settings.busy or settings.status in ACTIVE:
                raise ValueError("请先等待或停止当前任务")
            start, end = frame_range(settings)
            if settings.target_mode == "AUTO":
                target = context.active_object
                if target and target.type == "MESH":
                    target = target.find_armature()
                if target and target.type == "ARMATURE" and not target.get("flowhmr_is_source"):
                    settings.target_rig = target
                    _match_rig(settings, target)
            config = _snapshot(settings)
            auto_start = settings.auto_start_backend
            options = dict(start_frame=0 if settings.source_is_image else start, end_frame=0 if settings.source_is_image else end,
                           static_camera=True if settings.source_is_image else settings.static_camera,
                           source_kind="image" if settings.source_is_image else "video", source_name=settings.source_name,
                           sam_chunk_size=None if settings.sam_auto_batch else settings.sam_chunk_size,
                           release_after_job=False,
                           manual_select=settings.manual_select, postprocess=settings.postprocess,
                            select_single_person=settings.select_single_person,
                           sam_hand_stride=int(settings.sam_hand_stride),
                           hamer=settings.hamer, hamer_stride=int(settings.hamer_stride),
                           steps=settings.steps, seed=settings.seed, cfg_scale=settings.cfg_scale,
                           smoothing_frames=settings.smoothing_frames, correction_preset=settings.correction_preset,
                           sole_fit_strength=settings.sole_fit_strength,
                           correction_floor_m=None if settings.auto_floor else settings.floor_height,
                           vggt_interval=settings.vggt_interval)
            previous_job = settings.job_id if self.rerun_current else ""
            if self.rerun_current and not previous_job:
                raise ValueError("请先打开要重新生成的任务")

            def work():
                if auto_start:
                    start_backend(config)
                    info = wait_ready(config)
                else:
                    info = _client(config).info()
                if not info.get("runtime", {}).get("ready"):
                    raise RuntimeError("后端运行环境尚未就绪，请先检查环境")
                missing = [item["name"] for item in info.get("model_files", {}).get("items", []) if item.get("blocking")]
                if missing:
                    raise RuntimeError("缺少模型：" + "、".join(missing))
                if options["postprocess"] and not info.get("postprocess_available"):
                    raise RuntimeError("接触修正组件尚未安装，请先配置组件，或将“地面与接触”设为“保留浮空”后生成")
                if (options["source_kind"] == "image" and options["postprocess"]
                        and not info.get("blender_bridge", {}).get("image_contact_options")):
                    raise RuntimeError("当前后端仍在运行旧代码，不支持图片地面配置；请在任务结束后重启源码后端，再生成图片姿势")
                if options["hamer"] and not info.get("hamer_available"):
                    raise RuntimeError("HaMeR 模型尚未就绪，请先补齐模型或关闭手指精修")
                client = _client(config, timeout=300)
                if previous_job:
                    return client.json(f"/api/jobs/{previous_job}/rerun", {
                        **options, "auto_frames": True,
                        "reselect_person": bool(options["manual_select"])})["job_id"]
                return client.upload_video(path, **options)

            scene = context.scene

            def done(s, job_id):
                s.job_id, s.status, s.progress = job_id, "queued", 0
                s.submitted_video_path, s.submitted_video_job = path, job_id
                s.vram_released = False
                s.auto_import_pending = True
                s.people_signature = ""
                s.people.clear()
                video_ui.set_people(scene, {})
                s.actual_range = ""
                s.message = ("已按当前配置创建新任务；原任务保留。" if previous_job else "任务已提交。") + (
                    "图片：识别人物框后提取一帧姿势" if options["source_kind"] == "image" else
                    f"30 FPS 帧 {start}–{end if end is not None else '片尾'}")
                schedule_poll(scene)

            _launch(settings, work, done)
            settings.message = "正在按当前配置创建新任务…" if previous_job else "正在检查环境并上传素材…"
        except Exception as error:
            self.report({"ERROR"}, str(error))
            settings.message, settings.last_error = str(error), True
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWHMR_OT_Poll(bpy.types.Operator):
    bl_idname = "flowhmr.poll"
    bl_label = "刷新任务"

    def execute(self, context):
        settings = _settings(context)
        if not settings.job_id:
            return {"CANCELLED"}
        config, job_id = _snapshot(settings), settings.job_id
        _launch(settings, lambda: _client(config).job(job_id), _show_job)
        schedule_poll(context.scene)
        return {"FINISHED"}


class FLOWHMR_OT_Cancel(bpy.types.Operator):
    bl_idname = "flowhmr.cancel"
    bl_label = "停止任务"

    def execute(self, context):
        settings = _settings(context)
        config, job_id, scene = _snapshot(settings), settings.job_id, context.scene

        def done(s, _):
            s.status, s.message = "cancel_requested", "等待当前安全步骤结束…"
            schedule_poll(scene)

        _launch(settings, lambda: _client(config).cancel(job_id), done)
        return {"FINISHED"}


class FLOWHMR_OT_ReleaseVRAM(bpy.types.Operator):
    bl_idname = "flowhmr.release_vram"
    bl_label = "立即卸载模型并释放显存"

    def execute(self, context):
        settings = _settings(context)
        config = _snapshot(settings)

        def done(s, result):
            _show_gpu(s, result)
            s.message = "模型已卸载，CUDA 缓存已清理；下次生成会重新加载模型"

        _launch(settings, lambda: _client(config).release_vram(), done)
        return {"FINISHED"}


class FLOWHMR_OT_StopBackend(bpy.types.Operator):
    bl_idname = "flowhmr.stop_backend"
    bl_label = "停止 FlowHMR 后端"
    bl_description = "停止当前地址上已确认的 FlowHMR 后端，包括由插件、桌面程序或终端启动的进程"

    def execute(self, context):
        global _WATCHED_SCENE
        settings = _settings(context)
        config = _snapshot(settings)
        affected = [scene.flowhmr for scene in bpy.data.scenes
                    if scene.flowhmr.host == config.host and scene.flowhmr.port == config.port]
        if any(state.stopping_backend for state in affected):
            return {"CANCELLED"}
        previous = [(state, state.status) for state in affected]
        for state in affected:
            _REQUESTS[state.as_pointer()] = object()
            state.stopping_backend, state.busy = True, True
            state.auto_import_pending = False
            state.status = "stopping"
            state.message = "正在退出后端并中断推理…"
        watched = _WATCHED_SCENE
        if watched and watched.flowhmr in affected:
            _WATCHED_SCENE = None
        def work():
            try:
                return stop_backend(config), ""
            except Exception as error:
                return False, str(error)
        def done(s, result):
            stopped, error = result
            for state, old_status in previous:
                state.stopping_backend, state.busy = False, False
                state.last_error = bool(error)
                if error:
                    state.status, state.message = old_status, error
                    continue
                state.connected = state.models_loaded = state.runtime_ready = False
                state.vram_released = True
                state.gpu_summary = "后端已退出，AI 模型已卸载"
                state.status = "backend_stopped"
                state.message = "后端及推理子进程已退出，任务已中断" if stopped else "后端未运行"
            if error and watched and watched.flowhmr.status in ACTIVE:
                schedule_poll(watched)
        _launch(settings, work, done, replace=True)
        return {"FINISHED"}


class FLOWHMR_OT_DownloadNPZ(bpy.types.Operator):
    bl_idname = "flowhmr.download_npz"
    bl_label = "保存动作 NPZ"

    def execute(self, context):
        settings = _settings(context)
        if settings.status != "done":
            return {"CANCELLED"}
        if not settings.output_path:
            settings.output_path = str(Path.home() / "Documents" / "FlowHMR" / f"{settings.job_id}.npz")
        config, job_id, path = _snapshot(settings), settings.job_id, bpy.path.abspath(settings.output_path)

        def done(s, destination):
            s.message = f"已保存：{destination}"

        _launch(settings, lambda: _client(config).download_npz(job_id, path), done)
        return {"FINISHED"}


def _cache_dir():
    return Path(bpy.utils.user_resource("DATAFILES", path="flowhmr", create=True))


def _scene_for(settings):
    return next(scene for scene in bpy.data.scenes if scene.flowhmr == settings)


def _start_import(settings):
    config, job_id = _snapshot(settings), settings.job_id
    scene = _scene_for(settings)
    include_mesh, start = settings.import_mesh, settings.import_frame
    # This import action creates only the standard FlowHMR skeleton. ARP
    # matching is intentionally a separate workflow and is never automatic.
    if settings.target_mode == "ARP":
        settings.target_mode = "NEW"
    source_image = settings.source_is_image
    skeleton_name, action_name = settings.skeleton_name, settings.action_name
    variant = settings.body_variant
    target = settings.target_rig if settings.target_mode in {"AUTO", "TARGET"} else None
    people = json.loads(settings.result_people_json)
    person = settings.result_person if people else "ALL"
    if target and len(people) > 1 and person == "ALL":
        raise ValueError("请在「人物」中选择一人，再给角色添加动画")
    if target:
        include_mesh = False
    if target or settings.target_mode == "TARGET":
        if not target:
            raise ValueError("请先选择角色骨架，或改为新建动捕骨骼")
        if settings.map_rig != target.name or not settings.bone_maps:
            _match_rig(settings, target)
    mappings = [item.target for item in settings.bone_maps]
    root_motion = settings.root_motion
    vertical_motion, height_offset = settings.vertical_motion, settings.motion_height_offset
    path = _cache_dir() / f"{job_id}-{person}-{variant}-{'mesh' if include_mesh else 'bones'}.npz"
    selection = f"&person={int(person)}" if people and person != "ALL" else ""
    original = settings.submitted_video_path if settings.submitted_video_job == job_id else ""
    video_path = _cache_dir() / f"{job_id}-preview.mp4"
    def work():
        filename = _client(config, timeout=180).save(
            f"/api/jobs/{job_id}/blender-motion?mesh={int(include_mesh)}&body={variant}{selection}", path)
        video = original if original and Path(original).is_file() else _client(config, timeout=180).save(
            f"/api/jobs/{job_id}/video", video_path)
        return filename, video
    def done(s, result):
        filename, video = result
        main = next((w for w in bpy.context.window_manager.windows
                     if w.scene == scene and not w.screen.get("flowhmr_popup")), bpy.context.window)
        with bpy.context.temp_override(window=main, scene=scene, view_layer=scene.view_layers[0]):
            preview.attach_video(scene, video, reset_timing=False)
            s.video_path = video
            s.video_fps, s.video_frames = s.preview_clip.fps, s.preview_clip.frame_duration
            s.video_duration = s.video_frames / max(s.video_fps, 1e-6)
            objects = motion.import_motion(bpy.context, filename, frame_start=start, include_mesh=include_mesh,
                                          height_offset_m=height_offset, source_image=source_image,
                                          skeleton_name=skeleton_name if not target else "", action_name=action_name if not target else "")
            if target:
                if len(objects) == 1:
                    rigging.bake_to_rig(bpy.context, objects[0], target, mappings, root_motion=root_motion,
                                       vertical_motion=vertical_motion, height_offset_m=height_offset, action_name=action_name)
                    for obj in objects:
                        obj.hide_set(True)
                        obj.hide_render = True
                        for child in obj.children:
                            child.hide_set(True)
                            child.hide_render = True
                    bpy.context.view_layer.objects.active = target
                    target.select_set(True)
            preview.show_result(bpy.context, objects, [target] if target and len(objects) == 1 else objects)
        s.imported_job_id = job_id
        if source_image:
            s.message = (f"已给 {target.name} 添加单帧姿势" if target and len(objects) == 1 else
                         f"已显示 {len(objects)} 人的单帧姿势")
        else:
            s.message = (f"已给 {target.name} 添加动画，按空格对照播放" if target and len(objects) == 1 else
                         f"已显示 {len(objects)} 人的动作，按空格对照播放")
    settings.message = "正在准备 Blender 骨骼、手指关键帧和身体…"
    _launch(settings, work, done)


def _show_people(settings, job):
    signature = f"{job['id']}:" + ",".join(str(c['index']) for c in job["candidates"])
    if settings.people_signature == signature:
        return
    settings.people_signature = signature
    settings.box_edits_json = "[]"
    settings.manual_box_mode = False
    scene = _scene_for(settings)
    video_ui.set_people(scene, job)
    settings.people.clear()
    for candidate in job["candidates"]:
        item = settings.people.add()
        item.person_index = candidate["index"]
        item.name = f"人物 {candidate['index'] + 1}"
        item.selected = candidate["index"] == job.get("default_person", 0)
        item.detail = ("未检测到人物，请手动框选" if candidate.get("manual_fallback") else
                       "图片人物 · 检查头、手和脚是否完整" if settings.source_is_image else
                       f"出现 {candidate.get('start', 0) / 30:.1f}–{(candidate.get('end', 0) + 1) / 30:.1f} 秒")
    config, job_id = _snapshot(settings), job["id"]
    indexes = [item.person_index for item in settings.people]
    directory = _cache_dir() / "people" / job_id
    def work():
        paths = []
        for index in indexes:
            try:
                paths.append((index, _client(config).save(f"/api/jobs/{job_id}/person/{index}.jpg", directory / f"{index}.jpg")))
            except OSError:
                continue
        video = _client(config, timeout=180).save(f"/api/jobs/{job_id}/video?source=preview",
                                                directory / "detection.mp4")
        return paths, video
    def done(s, result):
        global _PREVIEWS
        paths, video = result
        if _PREVIEWS is not None:
            bpy.utils.previews.remove(_PREVIEWS)
        _PREVIEWS = bpy.utils.previews.new()
        for index, path in paths:
            icon = _PREVIEWS.load(str(index), path, "IMAGE")
            for item in s.people:
                if item.person_index == index:
                    item.icon_id = icon.icon_id
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            preview.attach_video(scene, video, reset_timing=False)
            # The detections refer to the canonical 30 FPS clip, including rotation.
            candidate = job["candidates"][job.get("default_person", 0)]
            frames = candidate.get("frames", [])
            if frames:
                first = candidate.get("preview_start", frames[0])
                last = candidate.get("preview_end", frames[-1])
                video_ui.seek(scene, 0 if s.source_is_image else (first + last) / 60)
        s.message = "勾选人物缩略图，可多选，然后生成所选人物动作"
    _launch(settings, work, done)


class FLOWHMR_OT_ImportMotion(bpy.types.Operator):
    bl_idname = "flowhmr.import_motion"
    bl_label = "导入骨骼动作到 Blender"
    bl_description = "创建新的骨骼、动作和可选身体，保留完整手指与位移，沿用场景帧率"
    def execute(self, context):
        s = _settings(context)
        if s.busy or s.status != "done":
            return {"CANCELLED"}
        try:
            _start_import(s)
        except Exception as error:
            s.message, s.last_error = str(error), True
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWHMR_OT_ConfirmPeople(bpy.types.Operator):
    bl_idname = "flowhmr.confirm_people"
    bl_label = "确认选人并继续生成"
    def execute(self, context):
        s = _settings(context)
        people = [item.person_index for item in s.people if item.selected]
        if s.busy or s.status != "waiting_user" or not people:
            self.report({"ERROR"}, "请至少勾选一个人物")
            return {"CANCELLED"}
        config, job_id, scene = _snapshot(s), s.job_id, context.scene
        edits = json.loads(s.box_edits_json)
        payload = {"people": people, "box_edits": edits}
        endpoint = "select-boxes" if edits else "select"
        def work():
            client = _client(config)
            if edits and not client.info().get("blender_bridge", {}).get("manual_boxes"):
                raise RuntimeError("当前后端还是更新前的进程，尚不支持人物框修正；请在方便时重启后端后重新识别。AI 模型未被释放。")
            return client.json(f"/api/jobs/{job_id}/{endpoint}", payload)
        def done(s, _):
            s.message, s.status = "已确认人物，正在继续生成…", "running"
            schedule_poll(scene)
        _launch(s, work, done)
        return {"FINISHED"}


def _history_client(config):
    start_backend(config)
    wait_ready(config)
    return _client(config, timeout=180)


def _fill_history(s, result, *, more=False):
    global _HISTORY_PREVIEWS
    from datetime import datetime
    selected = {item.job_id for item in s.history if item.selected}
    active = s.history[s.history_index].job_id if s.history and s.history_index < len(s.history) else ""
    if not more:
        s.history.clear()
    existing = {item.job_id for item in s.history}
    for job in result["jobs"]:
        if job["id"] in existing:
            continue
        item = s.history.add()
        name = job.get("source_name") or job["filename"]
        item.is_image = is_image_job(job)
        if item.is_image:
            import re
            name = re.sub(r"\.(webm|mp4)$", "", name, flags=re.I)
        item.job_id, item.name, item.status = job["id"], name, job["status"]
        path = job.get("local_thumbnail")
        item.icon_id = 0
        if path and Path(path).is_file():
            if _HISTORY_PREVIEWS is None:
                _HISTORY_PREVIEWS = bpy.utils.previews.new()
            try:
                if path not in _HISTORY_PREVIEWS:
                    _HISTORY_PREVIEWS.load(path, path, "IMAGE")
                item.icon_id = _HISTORY_PREVIEWS[path].icon_id
            except (OSError, RuntimeError):
                pass
        item.selected = job["id"] in selected
        created = datetime.fromtimestamp(job["created"]).strftime("%m-%d %H:%M") if job.get("created") else ""
        people = int(job.get("person_count") or 1)
        item.detail = f"{created} · {people} 人" + (" · 图片姿势" if is_image_job(job) else
                      f" · {job['num_frames']} 帧" if job.get("num_frames") else "")
        params = job.get("params") or {}
        first, last = params.get("start_frame", 0), params.get("end_frame")
        item.source_detail = "图片 · 单帧姿势" if item.is_image else (
            f"视频 · {first / 30:.2f}–{(last + 1) / 30:.2f} 秒" if last is not None else f"视频 · {first / 30:.2f} 秒至片尾")
        existing.add(item.job_id)
    s.history_cursor = result.get("next_cursor") or ""
    s.history_index = next((i for i, item in enumerate(s.history) if item.job_id == active), 0)


def _history_media(config, job_id, directory):
    client = _history_client(config)
    job = client.job(job_id)
    path = client.save(f"/api/jobs/{job_id}/video", directory / f"{job_id}-source.mp4")
    media = prepare_media(config, path)
    return job, media


def _adopt_history(s, job, media, scene, *, import_result=False):
    from . import media_panel
    s.people.clear()
    s.people_signature = ""
    s.result_people_json = "[]"
    s.result_person = "ALL"
    s.preview_result_roots = "[]"
    s.job_id, s.auto_import_pending = job["id"], False
    s.submitted_video_job, s.submitted_video_path = job["id"], media["path"]
    s.source_name = job.get("source_name") or job["filename"]
    s.source_path, s.video_path = media["path"], media["path"]
    import re
    s.source_is_image = media["is_image"] or is_image_job(job)
    if s.source_is_image:
        s.source_name = re.sub(r"\.(webm|mp4)$", "", s.source_name, flags=re.I)
    s.media_resolution = f"{'图片' if s.source_is_image else '视频'} {media.get('width', 0)} × {media.get('height', 0)}" if media.get("width") else ""
    s.media_warning = ""
    s.video_fps, s.video_frames, s.video_duration = media["fps"], media["frames"], media["duration"]
    params = job.get("params") or {}
    s.use_time_range = True
    s.start_time = params.get("start_frame", 0) / 30
    end = params.get("end_frame")
    s.to_video_end = end is None
    s.end_time = (end + 1) / 30 if end is not None else media["duration"]
    s.select_single_person = params.get("select_single_person", False)
    for key in ("manual_select", "static_camera", "postprocess", "hamer", "steps", "seed", "cfg_scale",
                "smoothing_frames", "correction_preset", "sole_fit_strength", "vggt_interval"):
        if key in params:
            setattr(s, key, params[key])
    s.hamer_stride = str(params.get("hamer_stride", 1))
    s.sam_hand_stride = str(params.get("sam_hand_stride", 1))
    chunk = params.get("sam_chunk_size")
    s.sam_auto_batch = chunk is None
    if chunk is not None:
        s.sam_chunk_size = int(chunk)
    floor = params.get("correction_floor_m")
    s.auto_floor = floor is None
    if floor is not None:
        s.floor_height = float(floor)
    media_panel.set_frames(scene, media.get("preview_frames", []))
    preview.attach_video(scene, media["path"])
    _show_job(s, job)
    if import_result and s.status == "done":
        _start_import(s)
    schedule_poll(scene)


class FLOWHMR_OT_RefreshHistory(bpy.types.Operator):
    bl_idname = "flowhmr.refresh_history"
    bl_label = "同步项目任务"
    more: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        s = _settings(context)
        if s.busy:
            return {"CANCELLED"}
        config, more = _snapshot(s), self.more
        cover_directory = _cache_dir() / "history-covers"
        cursor = s.history_cursor if more else ""
        def work():
            client = _history_client(config)
            result = client.json("/api/jobs?limit=20" + (f"&before={cursor}" if cursor else ""))
            result["backend_info"] = client.info()
            _fetch_history_thumbnails(client, result, config, cover_directory)
            return result
        def done(s, result):
            _show_info(s, result["backend_info"])
            _fill_history(s, result, more=more)
            s.message = f"已同步 {len(s.history)} / {result['total']} 个项目任务"
        _launch(s, work, done)
        return {"FINISHED"}


def _fetch_history_thumbnails(client, result, config, directory):
    """Fetch small covers on the worker thread; never decode entire videos in UI."""
    from concurrent.futures import ThreadPoolExecutor
    from hashlib import sha256
    def fetch(job):
        token = f"{base_url(config)}:{job['id']}:{job['status']}:{job.get('selected_person')}"
        path = directory / (sha256(token.encode()).hexdigest() + ".jpg")
        try:
            if not path.is_file():
                client.save(f"/api/jobs/{job['id']}/thumbnail.jpg?source=original", path)
            job["local_thumbnail"] = str(path)
        except (OSError, RuntimeError):
            pass  # An unavailable cover must not hide an otherwise usable task.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, result["jobs"]))


class FLOWHMR_OT_OpenHistory(bpy.types.Operator):
    bl_idname = "flowhmr.open_history"
    bl_label = "打开所选任务"
    import_result: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        s = _settings(context)
        if s.status in ACTIVE or s.busy or not s.history or s.history_index >= len(s.history):
            return {"CANCELLED"}
        item = s.history[s.history_index]
        if self.import_result and item.status != "done":
            self.report({"ERROR"}, "此任务尚未生成完成")
            return {"CANCELLED"}
        config, job_id, scene, import_result = _snapshot(s), item.job_id, context.scene, self.import_result
        _remember_media(s)
        directory = _cache_dir()
        def done(s, result):
            _adopt_history(s, *result, scene, import_result=import_result)
        _launch(s, lambda: _history_media(config, job_id, directory), done)
        return {"FINISHED"}


class FLOWHMR_OT_RerunHistory(bpy.types.Operator):
    bl_idname = "flowhmr.rerun_history"
    bl_label = "重新生成所选历史任务"
    bl_description = "沿用原任务素材和参数，重新选择人物，创建新任务"

    def execute(self, context):
        s = _settings(context)
        if s.busy or s.status in ACTIVE or not s.history or s.history_index >= len(s.history):
            return {"CANCELLED"}
        item = s.history[s.history_index]
        if item.status in ACTIVE:
            return {"CANCELLED"}
        config, job_id, scene = _snapshot(s), item.job_id, context.scene
        directory = _cache_dir()
        def work():
            client = _history_client(config)
            result = client.json(f"/api/jobs/{job_id}/rerun", {"release_after_job": False,
                                 "manual_select": True, "select_single_person": True, "reselect_person": True})
            return _history_media(config, result["job_id"], directory)
        def done(s, result):
            _adopt_history(s, *result, scene, import_result=True)
        _launch(s, work, done)
        return {"FINISHED"}


class FLOWHMR_OT_DeleteHistory(bpy.types.Operator):
    bl_idname = "flowhmr.delete_history"
    bl_label = "删除历史任务"
    bl_description = "删除项目任务及对应生成文件；保留已导入 Blender 的角色与动画"
    job_ids: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        s = context.scene.flowhmr
        ids = [item.job_id for item in s.history if item.selected] if s.history_manage else []
        if not ids and s.history and s.history_index < len(s.history):
            ids = [s.history[s.history_index].job_id]
        if not ids or s.busy:
            return {"CANCELLED"}
        self.job_ids = json.dumps(ids)
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        count = len(json.loads(self.job_ids or "[]"))
        self.layout.label(text=f"删除 {count} 条任务及对应生成文件？", icon="TRASH")
        self.layout.label(text="已导入 Blender 的角色和动画会保留。")
        self.layout.label(text="运行中的任务会跳过。")

    def execute(self, context):
        s = _settings(context)
        ids = json.loads(self.job_ids or "[]")
        if s.busy or not ids or not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            return {"CANCELLED"}
        config = _snapshot(s)
        def work():
            client = _history_client(config)
            result = client.json("/api/jobs", {"ids": ids}, method="DELETE")
            try:
                history = client.json("/api/jobs?limit=20")
            except (OSError, RuntimeError):
                history = None
            return result, history
        def done(s, result):
            removed, history = result
            deleted = set(removed.get("deleted", []))
            for scene in bpy.data.scenes:
                state = scene.flowhmr
                if state.job_id in deleted:
                    state.job_id, state.status, state.progress = "", "idle", 0
                    state.result_people_json = "[]"
                    state.result_person = "ALL"
                    state.auto_import_pending = False
                    state.people.clear()
                    state.people_signature = ""
                    state.submitted_video_job = ""
                for index in range(len(state.history) - 1, -1, -1):
                    if state.history[index].job_id in deleted:
                        state.history.remove(index)
                state.history_index = min(state.history_index, max(0, len(state.history) - 1))
            if history is not None:
                _fill_history(s, history)
            else:
                s.history_cursor = ""
            s.message = f"已删除 {len(deleted)} 条任务；跳过 {len(removed.get('skipped', []))} 条"
        _launch(s, work, done)
        return {"FINISHED"}


class FLOWHMR_OT_OpenWeb(bpy.types.Operator):
    bl_idname = "flowhmr.open_web"
    bl_label = "打开 Web 版"
    bl_description = "启动或连接共用后端并打开完整 Web 界面，任务可从插件历史中导入"

    def execute(self, context):
        settings = _settings(context)
        if settings.busy:
            return {"CANCELLED"}
        config = _snapshot(settings)
        def work():
            start_backend(config)
            return wait_ready(config)
        def done(s, info):
            _show_info(s, info)
            bpy.ops.wm.url_open(url=base_url(config))
        _launch(settings, work, done)
        return {"FINISHED"}


class FLOWHMR_OT_UseCurrentFrame(bpy.types.Operator):
    bl_idname = "flowhmr.use_current_frame"
    bl_label = "从当前时间轴位置导入"
    def execute(self, context):
        context.scene.flowhmr.import_frame = context.scene.frame_current
        return {"FINISHED"}


class FLOWHMR_OT_PresetAction(bpy.types.Operator, ImportHelper):
    bl_idname = "flowhmr.preset_action"
    bl_label = "手脚预设"
    action: StringProperty(default="refresh")
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    def invoke(self, context, event):
        if self.action == "delete":
            return context.window_manager.invoke_confirm(self, event)
        if self.action in {"import", "export"}:
            if self.action == "export":
                s = _settings(context)
                if not s.presets or s.preset_index >= len(s.presets):
                    self.report({"ERROR"}, "请先选择预设")
                    return {"CANCELLED"}
                directory = Path(s.preset_directory) if s.preset_directory else Path(s.backend_root) / "output/pose_presets"
                self.filepath = str(directory / "exports" / f"{s.presets[s.preset_index].identifier}.json")
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)
    def execute(self, context):
        s = _settings(context)
        config, action = _snapshot(s), self.action
        selected = s.presets[s.preset_index].identifier if s.presets and s.preset_index < len(s.presets) else ""
        try:
            if s.busy:
                return {"CANCELLED"}
            if action in {"capture", "apply", "current", "all"}:
                obj, frame = motion.actor_context(context)
                job_id, count = obj["flowhmr_job_id"], obj["flowhmr_frames"]
                if action == "current":
                    s.preset_start = s.preset_end = frame + 1
                    return {"FINISHED"}
                if action == "all":
                    s.preset_start, s.preset_end = 1, count
                    return {"FINISHED"}
            body = None
            path, method = "/api/pose-presets", "GET"
            if action == "capture":
                if not s.preset_name.strip():
                    raise ValueError("请先给预设起一个名称")
                path, method = "/api/pose-presets", "POST"
                body = dict(job_id=job_id, name=s.preset_name.strip(), part=s.preset_part, frame=frame)
            elif action == "apply":
                if not selected or not 1 <= s.preset_start <= s.preset_end <= count:
                    raise ValueError(f"请选择预设，并将范围设在 1–{count} 帧内")
                path, method = f"/api/jobs/{job_id}/pose-presets", "POST"
                body = dict(preset_id=selected, start=s.preset_start - 1, end=s.preset_end - 1,
                            strength=s.preset_strength, blend_frames=s.preset_blend)
            elif action in {"delete", "export"}:
                if not selected:
                    raise ValueError("请先选择预设")
                path, method = f"/api/pose-presets/{selected}", "DELETE" if action == "delete" else "GET"
            elif action == "import":
                body = {"preset": json.loads(Path(self.filepath).read_text(encoding="utf-8-sig"))}
                path, method = "/api/pose-presets", "POST"
            export_path = Path(bpy.path.abspath(self.filepath)) if action == "export" else None
            scene = context.scene
            source_start = obj["flowhmr_frame_start"] if action == "apply" else 1
            def work():
                start_backend(config)
                wait_ready(config)
                result = _client(config, timeout=120).json(path, body, method)
                library = _client(config).json("/api/pose-presets") if action != "apply" else {}
                if action == "export":
                    export_path.parent.mkdir(parents=True, exist_ok=True)
                    export_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                return result, library
            def done(s, value):
                result, library = value
                if action == "apply":
                    s.job_id, s.status = result["job_id"], "queued"
                    s.progress, s.auto_import_pending = 0, True
                    s.import_frame = round(source_start)
                    s.vram_released = False
                    s.message = "正在创建姿势修正版，原动作保留；完成后按自动导入设置处理"
                    schedule_poll(scene)
                else:
                    items = library.get("items", [])
                    for scene in bpy.data.scenes:
                        state = scene.flowhmr
                        if state.host != config.host or state.port != config.port:
                            continue
                        active_id = state.presets[state.preset_index].identifier if state.presets and state.preset_index < len(state.presets) else ""
                        state.presets.clear()
                        state.preset_directory = library.get("directory", "")
                        for record in items:
                            item = state.presets.add()
                            item.identifier, item.name, item.part = record["id"], record["name"], record["part"]
                        state.preset_index = next((i for i, item in enumerate(items) if item["id"] == active_id), 0)
                    if action in {"capture", "import"}:
                        s.preset_index = next((i for i, item in enumerate(items) if item["id"] == result["id"]), 0)
                    s.message = (f"预设已导出：{export_path}" if action == "export" else "项目预设库已同步，与 Web 共用；保存和导入的预设会自动留在项目内")
                    if action == "export":
                        bpy.ops.wm.path_open(filepath=str(export_path.parent))
            _launch(s, work, done)
        except Exception as error:
            s.message, s.last_error = str(error), True
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def _match_rig(settings, target):
    settings.bone_maps.clear()
    for name, matched in zip(motion.JOINT_NAMES, rigging.match_bones(target)):
        item = settings.bone_maps.add()
        item.name, item.target = name, matched
    settings.map_rig = target.name


class FLOWHMR_OT_UseSelectedRig(bpy.types.Operator):
    bl_idname = "flowhmr.use_selected_rig"
    bl_label = "使用当前骨架"
    def execute(self, context):
        obj = context.active_object
        if obj and obj.type == "MESH":
            obj = obj.find_armature()
        if not obj or obj.type != "ARMATURE":
            self.report({"ERROR"}, "请先选中角色骨架或绑定的身体")
            return {"CANCELLED"}
        s = _settings(context)
        s.target_rig, s.target_mode = obj, "TARGET"
        _match_rig(s, obj)
        return {"FINISHED"}


class FLOWHMR_OT_MatchBones(bpy.types.Operator):
    bl_idname = "flowhmr.match_bones"
    bl_label = "智能识别骨骼"
    def execute(self, context):
        s = _settings(context)
        if not s.target_rig:
            self.report({"ERROR"}, "请先选择目标骨架")
            return {"CANCELLED"}
        _match_rig(s, s.target_rig)
        s.message = f"已匹配 {sum(bool(m.target) for m in s.bone_maps)} / 52 根骨骼，请检查对应关系"
        return {"FINISHED"}


class FLOWHMR_OT_PreviewVideo(bpy.types.Operator):
    bl_idname = "flowhmr.preview_video"
    bl_label = "预览视频"
    def execute(self, context):
        path = bpy.path.abspath(context.scene.flowhmr.video_path)
        if not Path(path).is_file():
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


class FLOWHMR_OT_AlignPoseGround(bpy.types.Operator):
    bl_idname = "flowhmr.align_pose_ground"
    bl_label = "以当前姿势对齐地面"
    bl_description = "将选中的标准动捕人物整体移到零平面；只改变整体高度，保留动作中的升降"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        try:
            offset = motion.align_current_pose_to_ground(context, context.active_object)
            s.message = f"人物整体高度已调整 {offset:+.3f} 米，可撤销；动作升降保持不变"
            s.last_error = False
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWHMR_OT_RepairBoneDisplay(bpy.types.Operator):
    bl_idname = "flowhmr.repair_bone_display"
    bl_label = "补齐骨骼显示连接"
    bl_description = "为旧动捕骨架补齐骨盆、肩部和手掌的分叉连线；动作和蒙皮保持原样"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            count = motion.ensure_joint_connections(context, context.active_object)
            context.scene.flowhmr.message = f"已补齐 {count} 条骨骼显示连接，52 根变形骨骼保持不变"
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWHMR_OT_BakeSelected(bpy.types.Operator):
    bl_idname = "flowhmr.bake_selected"
    bl_label = "将选中动捕骨骼烘焙到角色"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        s, source = _settings(context), context.active_object
        if source and source.type == "MESH":
            source = source.find_armature()
        try:
            if s.busy or not source or not source.get("flowhmr_is_source") or not s.target_rig:
                raise ValueError("请选择一个标准动捕骨骼，并指定目标角色骨架")
            if s.map_rig != s.target_rig.name:
                _match_rig(s, s.target_rig)
            rigging.bake_to_rig(context, source, s.target_rig, [m.target for m in s.bone_maps], root_motion=s.root_motion,
                               vertical_motion=s.vertical_motion, height_offset_m=s.motion_height_offset, action_name=s.action_name)
            preview.show_result(context, [source], [s.target_rig])
            s.message = f"已给 {s.target_rig.name} 添加所选人物的新 Action"
            s.last_error = False
        except Exception as error:
            s.message, s.last_error = str(error), True
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


EXTRA_CLASSES = (FLOWHMR_OT_OpenMedia, FLOWHMR_OT_RemoveMedia, FLOWHMR_OT_ImportMotion, FLOWHMR_OT_ConfirmPeople, FLOWHMR_OT_RefreshHistory,
                 FLOWHMR_OT_OpenHistory, FLOWHMR_OT_RerunHistory, FLOWHMR_OT_DeleteHistory, FLOWHMR_OT_UseCurrentFrame,
                 FLOWHMR_OT_PresetAction, FLOWHMR_OT_UseSelectedRig, FLOWHMR_OT_MatchBones,
                 FLOWHMR_OT_PreviewVideo, FLOWHMR_OT_AlignPoseGround, FLOWHMR_OT_RepairBoneDisplay, FLOWHMR_OT_BakeSelected)
