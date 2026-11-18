"""Additional workflow properties and panels, kept separate from backend code."""

import bpy
import json
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup, UIList

from . import operators, preview, media_panel

PARTS = [(key, label, "") for key, label in (("hands", "双手"), ("left_hand", "左手"),
    ("right_hand", "右手"), ("feet", "双脚"), ("left_foot", "左脚"), ("right_foot", "右脚"))]


class FLOWHMR_Person(PropertyGroup):
    person_index: IntProperty()
    selected: BoolProperty(name="选择人物")
    detail: StringProperty()
    icon_id: IntProperty(options={"SKIP_SAVE"})


class FLOWHMR_History(PropertyGroup):
    job_id: StringProperty()
    status: StringProperty()
    selected: BoolProperty(name="选择任务", default=False)
    detail: StringProperty()
    source_detail: StringProperty()
    is_image: BoolProperty()
    icon_id: IntProperty(options={"SKIP_SAVE"})


class FLOWHMR_Preset(PropertyGroup):
    identifier: StringProperty()
    part: StringProperty()


class FLOWHMR_BoneMap(PropertyGroup):
    target: StringProperty(name="目标骨骼")


class FLOWHMR_Media(PropertyGroup):
    source_path: StringProperty(subtype="FILE_PATH")
    prepared_path: StringProperty(subtype="FILE_PATH")
    is_image: BoolProperty()
    fps: FloatProperty()
    frames: IntProperty()
    duration: FloatProperty()
    width: IntProperty()
    height: IntProperty()
    source_width: IntProperty()
    source_height: IntProperty()
    warning: StringProperty()
    image_resolution: StringProperty(default="AUTO")
    preview_json: StringProperty(default="[]")
    start_time: FloatProperty()
    end_time: FloatProperty()
    to_video_end: BoolProperty(default=True)


def _history_opened(settings, context):
    if settings.show_history and not settings.busy:
        bpy.ops.flowhmr.refresh_history()


def _page_changed(settings, context):
    if settings.panel_page == "HISTORY" and not settings.busy and settings.status not in operators.ACTIVE:
        bpy.ops.flowhmr.refresh_history()


def _presets_opened(settings, context):
    if settings.show_presets and not settings.busy and settings.status not in operators.ACTIVE:
        bpy.ops.flowhmr.preset_action(action="refresh")


def _rig_poll(_self, obj):
    return obj.type == "ARMATURE"


def _model_rig(obj):
    return obj if obj and obj.type == "ARMATURE" else obj.find_armature() if obj and obj.type == "MESH" else None


def _model_poll(_self, obj):
    return _model_rig(obj) is not None


def _model_changed(settings, _context):
    rig = _model_rig(settings.target_model)
    if settings.target_rig != rig:
        settings.target_rig = rig


def _target_changed(settings, _context):
    if settings.target_rig:
        settings.target_mode = "TARGET"
        operators._match_rig(settings, settings.target_rig)
    else:
        settings.target_mode = "AUTO"
        settings.bone_maps.clear()
        settings.map_rig = ""
    if _model_rig(settings.target_model) != settings.target_rig:
        settings.target_model = settings.target_rig


def _contact_mode_get(settings):
    # Keep old .blend files and restored Web jobs in sync with this simpler UI.
    return 0 if settings.postprocess else 1


def _contact_mode_set(settings, value):
    settings.postprocess = value == 0


def _output_rig_get(settings):
    if settings.target_mode == "ARP":
        settings.target_mode = "NEW"
        return 0
    return 1 if settings.target_mode == "TARGET" or (settings.target_mode == "AUTO" and settings.target_rig) else 0


def _output_rig_set(settings, value):
    settings.target_mode = ("NEW", "TARGET")[min(int(value), 1)]


PROPERTY_CLASSES = (FLOWHMR_Person, FLOWHMR_History, FLOWHMR_Preset, FLOWHMR_BoneMap, FLOWHMR_Media)
_RESULT_ITEMS = {}


def _result_items(settings, _context):
    key = settings.result_people_json
    if key not in _RESULT_ITEMS:
        _RESULT_ITEMS[key] = [("ALL", "全部人物", "新建标准骨骼时保留多人站位")] + [
            (str(p["person"]), f"人物 {p['person']}", "") for p in json.loads(key)]
    return _RESULT_ITEMS[key]


SETTINGS = dict(
    panel_page=EnumProperty(name="页面", items=[("MEDIA", "素材", "上传素材、选人和识别"),
        ("HISTORY", "任务", "选择、预览和删除任务"), ("RIG", "骨架", "角色和动画"),
        ("MODELS", "设置", "环境、AI 模型与显存")], default="MEDIA", update=_page_changed),
    manual_box_mode=BoolProperty(name="重画人物框", description="暂停预览后拖框，完整包含头、手和脚；确认人物时应用", default=False, options={"SKIP_SAVE"}),
    box_person_index=IntProperty(name="修正人物", default=0, min=0),
    box_edits_json=StringProperty(default="[]", options={"SKIP_SAVE"}),
    media=CollectionProperty(type=FLOWHMR_Media),
    media_index=IntProperty(min=0),
    show_options=BoolProperty(name="更多选项", default=False),
    show_media_library=BoolProperty(name="已导入素材", default=False),
    show_presets=BoolProperty(name="手脚预设", default=False, update=_presets_opened),
    show_history=BoolProperty(name="历史任务", default=False, update=_history_opened),
    show_generation_details=BoolProperty(name="生成参数", default=False),
    show_rig_details=BoolProperty(name="骨骼匹配与导出", default=False),
    panel_frames_json=StringProperty(default="[]"),
    panel_position=FloatProperty(name="预览进度", default=0, min=0, max=1, subtype="FACTOR", update=media_panel.position_changed),
    source_name=StringProperty(),
    source_path=StringProperty(subtype="FILE_PATH"),
    source_is_image=BoolProperty(default=False),
    media_resolution=StringProperty(),
    media_warning=StringProperty(),
    image_resolution=EnumProperty(name="图片尺寸", items=[("AUTO", "自动适配", "等比缩放，兼顾速度与内存"),
        ("ORIGINAL", "保留原图尺寸", "不放大或缩小，内存与处理时间随图片尺寸增加")], default="AUTO"),
    preview_clip=PointerProperty(type=bpy.types.MovieClip),
    preview_origin_seconds=FloatProperty(default=0),
    preview_frame_start=FloatProperty(default=1),
    preview_result_roots=StringProperty(default="[]"),
    preview_show_bones=BoolProperty(name="显示骨骼", default=False, update=preview.update_overlays),
    submitted_video_path=StringProperty(subtype="FILE_PATH"),
    submitted_video_job=StringProperty(),
    manual_select=BoolProperty(name="多人手动选人", description="检测到多人时暂停，选择要生成动作的人物并调整框；关闭后自动选择主体", default=True),
    select_single_person=BoolProperty(name="单人也显示选人框", description="需开启多人手动选人；只有一个人时也暂停，供确认或移动、缩放人物框；图片和视频均适用", default=False),
    postprocess=BoolProperty(name="接触修正 / 减少滑步", default=True),
    contact_mode=EnumProperty(name="地面与接触", items=[
        ("AUTO", "自动接触", "修正支撑脚和滑步，已有跳跃检测保护腾空段；整段浮空请选保留浮空", 0),
        ("AIRBORNE", "保留浮空", "跳过地面和脚掌贴合修正，保留 AI 识别的轨迹；适用于飞行或仅包含腾空的片段", 1),
    ], get=_contact_mode_get, set=_contact_mode_set),
    hamer=BoolProperty(name="HaMeR 手指精修", default=False),
    hamer_stride=EnumProperty(name="手部采样", items=[(str(i), f"每 {i} 帧", "") for i in (1, 2, 3, 4)], default="1"),
    sam_hand_stride=EnumProperty(name="SAM 手部采样", items=[("1", "每帧", "完整采样"),
        ("2", "每 2 帧", "间隔帧插值，减少 SAM 手部处理量"), ("4", "每 4 帧", "更快，手部细节可能降低")], default="1"),
    steps=IntProperty(name="生成步数", default=20, min=1, max=100),
    seed=IntProperty(name="随机种子", default=0, min=0),
    cfg_scale=FloatProperty(name="引导强度", default=1, min=0, max=10),
    smoothing_frames=FloatProperty(name="动作平滑", default=0.12, min=0),
    correction_preset=EnumProperty(name="脚步锁定", items=[("normal", "标准", ""), ("strong", "加强", "")], default="strong"),
    sole_fit_strength=FloatProperty(name="脚掌贴地", default=0.5, min=0, max=1, subtype="FACTOR"),
    auto_floor=BoolProperty(name="自动估计支撑地面", default=True),
    floor_height=FloatProperty(name="支撑地面高度（米）", description="生成时用于接触修正，不会移动预览网格或已生成动画", default=0),
    preview_ground=BoolProperty(name="显示参考地面", description="仅在动捕预览中显示网格，不修改动作或产生碰撞", default=True, update=preview.update_ground),
    preview_ground_height=FloatProperty(name="参考地面高度（米）", description="只移动预览网格，不影响生成、接触修正或导出的动画", default=0, update=preview.update_ground),
    vggt_interval=IntProperty(name="移动机位采样间隔（0 自动）", default=0, min=0, max=120),
    # Generation produces the neutral FlowHMR armature first. Retargeting to
    # ARP/Mixamo/another character remains an explicit later action.
    auto_import=BoolProperty(name="完成后导入标准骨架", default=False),
    import_mesh=BoolProperty(name="附带标准身体预览（可选）", default=False),
    body_variant=EnumProperty(name="标准身体", items=[("source", "跟随任务模型", ""), ("neutral", "中性", ""),
        ("male", "男性", ""), ("female", "女性", "")], default="source"),
    import_frame=IntProperty(name="动画开始帧", default=1),
    skeleton_name=StringProperty(name="骨架名称", description="留空自动命名；多人结果自动添加人物编号，不更改关节名称"),
    action_name=StringProperty(name="动画名称", description="导入或添加动作时使用的新 Action 名称；留空自动命名"),
    stopping_backend=BoolProperty(default=False, options={"SKIP_SAVE"}),
    target_mode=EnumProperty(name="动作添加到", items=[("AUTO", "自动选择目标", "选中已有角色时自动添加动画，未选角色时创建标准骨骼"),
        ("TARGET", "已有角色骨架", "匹配骨骼并烘焙新的 Action"), ("NEW", "新建标准动捕骨骼", "完整手指与位移")], default="NEW"),
    output_rig=EnumProperty(name="生成后骨架", items=[
        ("STANDARD", "标准动捕骨架", "完整手指的标准人体，便于检查和导出", 0),
        ("TARGET", "已有角色骨架", "指定已有角色，将动作添加为新的 Action", 1)], get=_output_rig_get, set=_output_rig_set),
    target_rig=PointerProperty(name="角色骨架", type=bpy.types.Object, poll=_rig_poll, update=_target_changed),
    target_model=PointerProperty(name="目标角色", description="用吸管点击场景中的绑定模型或骨架，自动取得角色骨架",
        type=bpy.types.Object, poll=_model_poll, update=_model_changed),
    root_motion=BoolProperty(name="保留水平位移", description="将识别出的水平移动添加到目标角色；关闭后仍可保留跳跃的上下运动", default=True),
    vertical_motion=BoolProperty(name="保留上下运动", description="将跳跃、升降传给目标骨架；关闭后目标根骨骼高度固定", default=True),
    motion_height_offset=FloatProperty(name="高度偏移（米）", description="导入或重新添加动画时整体升降；相对于源动作或目标角色原始站位，不改变跳跃幅度", default=0),
    bone_maps=CollectionProperty(type=FLOWHMR_BoneMap),
    map_rig=StringProperty(),
    show_mapping=BoolProperty(name="检查 / 调整骨骼对应", default=False),
    imported_job_id=StringProperty(),
    auto_import_pending=BoolProperty(default=False, options={"SKIP_SAVE"}),
    job_detail=StringProperty(options={"SKIP_SAVE"}),
    result_frames=IntProperty(),
    result_people_json=StringProperty(default="[]"),
    result_person=EnumProperty(name="结果人物", items=_result_items),
    people=CollectionProperty(type=FLOWHMR_Person),
    people_signature=StringProperty(options={"SKIP_SAVE"}),
    history=CollectionProperty(type=FLOWHMR_History),
    history_index=IntProperty(min=0),
    history_cursor=StringProperty(options={"SKIP_SAVE"}),
    history_search=StringProperty(name="搜索任务", options={"SKIP_SAVE"}),
    history_filter=EnumProperty(name="任务状态", items=[("ALL", "全部", ""), ("done", "已完成", ""),
        ("ACTIVE", "处理中", ""), ("failed", "失败", ""), ("cancelled", "已停止", "")], options={"SKIP_SAVE"}),
    history_manage=BoolProperty(name="批量管理", default=False, options={"SKIP_SAVE"}),
    presets=CollectionProperty(type=FLOWHMR_Preset),
    preset_index=IntProperty(min=0),
    preset_name=StringProperty(name="预设名称"),
    preset_directory=StringProperty(name="项目预设库", subtype="DIR_PATH", options={"SKIP_SAVE"}),
    preset_part=EnumProperty(name="保存部位", items=PARTS, default="hands"),
    preset_start=IntProperty(name="开始（动作帧）", default=1, min=1),
    preset_end=IntProperty(name="结束（动作帧）", default=1, min=1),
    preset_strength=FloatProperty(name="应用强度", default=1, min=0.01, max=1, subtype="FACTOR"),
    preset_blend=IntProperty(name="边界过渡帧", default=5, min=0, max=120),
)


def editable(panel, settings):
    panel.layout.use_property_split = False
    panel.layout.use_property_decorate = False
    col = panel.layout.column()
    col.enabled = not settings.busy and settings.status not in operators.ACTIVE
    return col


def draw_capture(panel, context):
    s, layout = context.scene.flowhmr, panel.layout
    ready = not s.busy and s.status not in operators.ACTIVE
    source = layout.column()
    source.enabled = ready
    row = source.row(align=True)
    draw_media_import(row, s)
    if s.video_path:
        from pathlib import Path
        source.label(text=s.source_name or Path(s.source_path or s.video_path).name)
        media_panel.draw_cover(layout, context)
        player = layout.row()
        player.operator("flowhmr.open_popup", text="播放器 · 预览 / 选人 / 裁剪", icon="PLAY")
        source = layout.column()
        source.enabled = ready
        if s.source_is_image:
            source.label(text="图片作为 1 秒静态素材提取姿势")
        else:
            row = source.row(align=True)
            row.prop(s, "start_time", text="起点秒")
            end = row.row(align=True)
            end.enabled = not s.to_video_end
            end.prop(s, "end_time", text="终点秒")
            source.prop(s, "to_video_end", text="到片尾")
    if s.status in operators.ACTIVE or s.busy:
        if hasattr(layout, "progress"):
            layout.progress(factor=s.progress, type="BAR", text=f"{s.progress:.0%}")
        else:
            layout.label(text=f"处理中 {s.progress:.0%}")
        if s.status in operators.ACTIVE:
            row = layout.row()
            row.enabled = not s.busy
            row.operator("flowhmr.cancel", text="停止处理", icon="CANCEL")
    draw_people(layout, s)
    layout.separator()
    target = layout.column()
    target.enabled = not s.busy
    row = target.row(align=True)
    row.prop(s, "target_rig", text="目标骨架")
    row.operator("flowhmr.use_selected_rig", text="", icon="EYEDROPPER")
    if s.target_rig:
        target.label(text=f"已自动匹配 {sum(bool(m.target) for m in s.bone_maps)} / 52 根骨骼")
    if not s.target_rig:
        target.label(text="未指定角色：生成标准骨骼预览")
    row = target.row(align=True)
    row.prop(s, "hamer", text="手指精修")
    draw_contact(target, s)
    if ready and s.status != "waiting_user":
        row = layout.row()
        row.enabled = bool(s.video_path)
        row.scale_y = 1.5
        row.operator("flowhmr.submit", text="识别人物" if s.manual_select else "生成动作", icon="TRACKER")
    if s.status == "done" and not s.busy:
        result = layout.box()
        result.label(text="动作已生成", icon="CHECKMARK")
        if s.result_people_json != "[]":
            result.prop(s, "result_person", text="人物")
        apply = result.row()
        apply.enabled = not (s.target_rig and len(json.loads(s.result_people_json)) > 1 and s.result_person == "ALL")
        apply.operator("flowhmr.import_motion", text="映射并添加到目标骨架" if s.target_rig else "显示动作", icon="ACTION")
        if not apply.enabled:
            result.label(text="请先选择一个结果人物", icon="INFO")
        row = result.row(align=True)
        row.operator("screen.animation_play", text="暂停" if context.screen.is_animation_playing else "播放", icon="PLAY")
        row.operator("flowhmr.preview_range", text="回到开头", icon="REW").action = "home"


def draw_names(layout, s):
    if s.target_mode == "NEW" or not s.target_rig:
        layout.prop(s, "skeleton_name")
    layout.prop(s, "action_name")


def draw_result(panel, context):
    s = context.scene.flowhmr
    col = editable(panel, s)
    # Output destination must be configured before starting the task.
    col.prop(s, "auto_import")
    col.operator("flowhmr.import_character", text="导入自己的角色", icon="IMPORT")
    col.prop(s, "target_mode")
    if s.target_mode in {"AUTO", "TARGET"}:
        col.prop(s, "target_model", text="目标角色")
        if s.target_model and s.target_model != s.target_rig:
            col.label(text=f"绑定骨架：{s.target_rig.name}")
        row = col.row(align=True)
        row.operator("flowhmr.use_selected_rig", icon="EYEDROPPER")
        row.operator("flowhmr.match_bones", icon="FILE_REFRESH")
        col.prop(s, "root_motion")
        col.prop(s, "vertical_motion")
        if s.bone_maps:
            col.label(text=f"已匹配 {sum(bool(m.target) for m in s.bone_maps)} / 52 根骨骼")
        col.prop(s, "show_mapping")
        if s.show_mapping and s.target_rig:
            for item in s.bone_maps:
                col.prop_search(item, "target", s.target_rig.data, "bones", text=item.name)
        col.label(text="ARP / Rigify 自动切 FK；可调整骨骼对应")
        row = col.row()
        row.enabled = bool(s.target_rig)
        row.operator("flowhmr.bake_selected", icon="ACTION")
    else:
        col.prop(s, "import_mesh")
    if s.import_mesh:
        col.prop(s, "body_variant")
    col.prop(s, "motion_height_offset")
    col.label(text="位移 / 高度修改后，重新添加动画生效", icon="INFO")
    active = context.active_object
    source = active.find_armature() if active and active.type == "MESH" else active
    if source and source.type == "ARMATURE" and source.get("flowhmr_is_source"):
        col.operator("flowhmr.align_pose_ground", icon="IMPORT")
        col.operator("flowhmr.repair_bone_display", icon="BONE_DATA")
        col.label(text="当前流程只生成标准骨骼；不会自动匹配 ARP 装备", icon="INFO")
    draw_names(col, s)
    col.prop(s, "import_frame")
    col.operator("flowhmr.use_current_frame", icon="TIME")
    result = col.column()
    result.enabled = s.status == "done"
    result.scale_y = 1.4
    result.operator("flowhmr.import_motion", text="给角色骨骼添加动画" if s.target_mode == "TARGET" else "导入骨骼动画", icon="ACTION")
    col.label(text="写入新 Action，沿用当前场景 FPS")
    col.prop(s, "output_path")
    row = col.row()
    row.enabled = s.status == "done"
    row.operator("flowhmr.download_npz", icon="EXPORT")


def draw_people(layout, s):
    if s.status != "waiting_user":
        return
    box = layout.box()
    box.label(text=f"已选 {sum(p.selected for p in s.people)} / {len(s.people)} 人")
    if s.manual_select:
        box.label(text="框内拖动移动，拉角点缩放；视频可多选人物", icon="SELECT_SET")
    else:
        box.label(text="已关闭手动框选：直接使用 AI 检测结果", icon="INFO")
    box.operator("flowhmr.open_popup", text="查看图片人物框" if s.source_is_image else "查看视频人物框", icon="SELECT_SET")
    for item in s.people:
        row = box.row()
        if item.icon_id:
            row.template_icon(icon_value=item.icon_id, scale=2)
        info = row.column()
        info.prop(item, "selected", text=item.name)
        info.label(text=item.detail)
        edit = info.row()
        edit.enabled = not s.busy
        edit.operator("flowhmr.edit_person_box", text="修正人物框", icon="SELECT_SET").person = item.person_index
    if s.box_edits_json != "[]":
        box.label(text="人物框已修改，确认后重新提取", icon="CHECKMARK")
        box.operator("flowhmr.reset_person_boxes", text="撤销框选", icon="LOOP_BACK")
    row = box.row()
    row.enabled = not s.busy and any(p.selected for p in s.people)
    row.operator("flowhmr.confirm_people", text="生成所选人物动作", icon="CHECKMARK")


HISTORY_STATES = {
    "done": ("已完成", "CHECKMARK"), "failed": ("生成失败", "ERROR"),
    "running": ("生成中", "TIME"), "queued": ("排队中", "TIME"),
    "waiting_user": ("待确认人物", "USER"), "cancel_requested": ("正在停止", "CANCEL"),
    "cancelled": ("已停止", "CANCEL"),
}


def history_matches(settings, item):
    query = settings.history_search.strip().casefold()
    return (not query or query in f"{item.name} {item.job_id} {item.source_detail}".casefold()) and (
        settings.history_filter == "ALL" or settings.history_filter == item.status or
        settings.history_filter == "ACTIVE" and item.status in operators.ACTIVE)


class FLOWHMR_UL_History(UIList):
    def filter_items(self, context, data, propname):
        return ([self.bitflag_filter_item if history_matches(data, item) else 0
                 for item in getattr(data, propname)], [])

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if data.history_manage:
            row.prop(item, "selected", text="")
        if item.icon_id:
            row.template_icon(icon_value=item.icon_id, scale=2.8)
        else:
            row.label(text="", icon="IMAGE_DATA" if item.is_image else "FILE_MOVIE")
        info = row.column(align=True)
        info.label(text=item.name)
        status, status_icon = HISTORY_STATES.get(item.status, (item.status, "INFO"))
        meta = info.row(align=True)
        meta.alert = item.status == "failed"
        meta.label(text=f"{status} · {'图片' if item.is_image else '视频'}", icon=status_icon)
        info.label(text=item.detail)


class FLOWHMR_UL_Presets(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="POSE_HLT")
        layout.label(text={p[0]: p[1] for p in PARTS}.get(item.part, item.part))


class FLOWHMR_UL_Media(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="IMAGE_DATA" if item.is_image else "FILE_MOVIE")
        layout.label(text="图片" if item.is_image else f"{item.duration:.1f}s")


def draw_presets(self, context):
    s = context.scene.flowhmr
    col = editable(self, s)
    col.label(text="自动保存在项目内，与 Web 共用", icon="FILE_FOLDER")
    if s.preset_directory:
        path = col.row()
        path.enabled = False
        path.prop(s, "preset_directory", text="位置")
    col.label(text="选中导入的动捕骨骼，定位时间轴")
    col.prop(s, "preset_name")
    col.prop(s, "preset_part")
    col.operator("flowhmr.preset_action", text="保存当前帧原始姿势", icon="ADD").action = "capture"
    col.operator("flowhmr.preset_action", text="刷新预设库", icon="FILE_REFRESH").action = "refresh"
    col.template_list("FLOWHMR_UL_Presets", "", s, "presets", s, "preset_index", rows=3)
    row = col.row(align=True)
    for action, label, icon in (("import", "导入", "IMPORT"), ("export", "导出", "EXPORT"), ("delete", "删除", "TRASH")):
        row.operator("flowhmr.preset_action", text=label, icon=icon).action = action
    col.prop(s, "preset_start")
    col.prop(s, "preset_end")
    row = col.row(align=True)
    row.operator("flowhmr.preset_action", text="当前帧").action = "current"
    row.operator("flowhmr.preset_action", text="整段动作").action = "all"
    col.prop(s, "preset_strength")
    col.prop(s, "preset_blend")
    row = col.row()
    row.enabled = bool(s.presets)
    row.operator("flowhmr.preset_action", text="生成姿势修正版", icon="ACTION").action = "apply"
    col.label(text="动作帧从 1 开始，生成独立修正版")
    col.label(text="保存动捕原始姿势，不含手动改骨骼")


def draw_history(self, context, *, compact=False):
    s = context.scene.flowhmr
    col = self.layout.column()
    col.enabled = not s.busy
    header = col.row(align=True)
    header.label(text=f"项目任务 · {len(s.history)}", icon="ACTION")
    header.operator("flowhmr.refresh_history", text="", icon="FILE_REFRESH")
    header.prop(s, "history_manage", text="管理", toggle=True)
    search = col.row(align=True)
    search.prop(s, "history_search", text="", icon="VIEWZOOM")
    search.prop(s, "history_filter", text="")
    if not s.history:
        empty = col.box()
        empty.label(text="还没有任务", icon="INFO")
        empty.label(text="在素材页上传视频或图片后开始识别")
        return
    visible = sum(history_matches(s, item) for item in s.history)
    col.template_list("FLOWHMR_UL_History", "", s, "history", s, "history_index", rows=5)
    if not visible:
        col.label(text="当前已加载任务中没有匹配项", icon="INFO")
    if s.history_cursor:
        col.operator("flowhmr.refresh_history", text="加载更多任务", icon="TRIA_DOWN").more = True
    chosen = s.history[s.history_index] if s.history_index < len(s.history) else None
    if chosen and history_matches(s, chosen):
        detail = col.box()
        if chosen.icon_id:
            image = detail.row()
            image.alignment = "CENTER"
            image.template_icon(icon_value=chosen.icon_id, scale=5)
        detail.label(text=chosen.name, icon="IMAGE_DATA" if chosen.is_image else "FILE_MOVIE")
        status, icon = HISTORY_STATES.get(chosen.status, (chosen.status, "INFO"))
        detail.label(text=f"{status} · {chosen.source_detail}", icon=icon)
        detail.label(text=chosen.detail)
        actions = detail.column()
        actions.enabled = s.status not in operators.ACTIVE
        if chosen.status == "done":
            draw_names(actions, s)
            actions.label(text=f"目标：{s.target_rig.name}" if s.target_mode != "NEW" and s.target_rig else "目标：新建标准骨架", icon="ARMATURE_DATA")
            primary = actions.row()
            primary.scale_y = 1.4
            primary.operator("flowhmr.open_history", text="加载姿势" if chosen.is_image else "加载动画", icon="ACTION").import_result = True
        row = actions.row(align=True)
        row.operator("flowhmr.open_history", text="继续选人" if chosen.status == "waiting_user" else "查看素材 / 配置", icon="FILE_FOLDER")
        rerun = row.row(align=True)
        rerun.enabled = chosen.status not in operators.ACTIVE
        rerun.operator("flowhmr.rerun_history", text="重跑", icon="FILE_REFRESH")
        if not s.history_manage:
            delete = actions.row()
            delete.enabled = chosen.status not in operators.ACTIVE
            delete.operator("flowhmr.delete_history", text="删除此任务…", icon="TRASH")
    if s.history_manage:
        count = sum(item.selected for item in s.history)
        col.label(text=f"已勾选 {count} 个任务；删除前会再次确认")
        delete = col.row()
        delete.enabled = bool(count) and s.status not in operators.ACTIVE
        delete.operator("flowhmr.delete_history", text=f"删除勾选任务（{count}）…", icon="TRASH")


def draw_advanced(self, context):
    s = context.scene.flowhmr
    col = editable(self, s)
    col.prop(s, "manual_select")
    row = col.row()
    row.enabled = s.manual_select
    row.separator(factor=1.5)
    row.prop(s, "select_single_person")
    for key in (("hamer",) if s.source_is_image else
                ("static_camera", "hamer", "use_time_range")):
        col.prop(s, key)
    if not s.source_is_image and not s.use_time_range:
        col.prop(s, "start_frame")
        col.prop(s, "end_frame")
    for key in ("steps", "seed", "cfg_scale", "smoothing_frames", "hamer_stride", "sam_hand_stride", "sam_auto_batch"):
        col.prop(s, key)
    if not s.sam_auto_batch:
        col.prop(s, "sam_chunk_size")
    if not s.source_is_image and not s.static_camera:
        col.prop(s, "vggt_interval")
    draw_contact(col, s, details=True)


def draw_contact(layout, s, *, details=False):
    if not details:
        layout.prop(s, "contact_mode")
        if s.contact_mode == "AIRBORNE":
            layout.label(text="保留空中轨迹，不做脚部贴地修正", icon="INFO")
        elif s.source_is_image:
            layout.label(text="按脚底放置到支撑地面，保留单帧姿势", icon="INFO")
        else:
            layout.label(text="支撑脚修正滑步，腾空段由检测判断", icon="INFO")
    if details and s.postprocess:
        for key in (("auto_floor",) if s.source_is_image else ("correction_preset", "sole_fit_strength", "auto_floor")):
            layout.prop(s, key)
        if not s.auto_floor:
            layout.prop(s, "floor_height")
    if details:
        layout.prop(s, "motion_height_offset")
        layout.label(text="高度偏移在重新添加动画时生效")
        layout.label(text="更改接触模式 / 支撑地面后需重新生成", icon="INFO")
        layout.separator()
        layout.prop(s, "preview_ground")
        if s.preview_ground:
            layout.prop(s, "preview_ground_height")
        layout.label(text="参考地面只用于观察，不改变动画")


def draw_rig_settings(self, context):
    draw_result(self, context)


def _foldout(layout, settings, prop):
    row = layout.row(align=True)
    row.prop(settings, prop, icon="TRIA_DOWN" if getattr(settings, prop) else "TRIA_RIGHT", emboss=False)
    return layout.box() if getattr(settings, prop) else None


def draw_panel(panel, context):
    """One persistent native panel; separate pages keep the primary flow short."""
    from types import SimpleNamespace
    s, layout = context.scene.flowhmr, panel.layout
    layout.use_property_decorate = False
    layout.prop(s, "panel_page", expand=True)
    if s.panel_page == "MEDIA":
        draw_source(layout, context)
        target = layout.box()
        target.label(text="动画目标", icon="ARMATURE_DATA")
        target.prop(s, "output_rig")
        if s.output_rig == "TARGET":
            target.prop(s, "target_model", text="角色")
            row = target.row(align=True)
            row.operator("flowhmr.use_selected_rig", text="使用选中角色", icon="EYEDROPPER")
            row.operator("flowhmr.import_character", text="导入", icon="IMPORT")
        else:
            target.label(text="先生成标准骨架；角色匹配在‘骨架’页手动执行", icon="INFO")
        draw_names(target, s)
        target.prop(s, "auto_import")
        target.prop(s, "import_mesh")
        options = _foldout(layout, s, "show_options")
        if options is not None:
            draw_contact(options, s)
            draw_advanced(SimpleNamespace(layout=options), context)
        else:
            draw_contact(layout, s)
    elif s.panel_page == "HISTORY":
        draw_history(SimpleNamespace(layout=layout), context, compact=True)
        _draw_message(layout, s, 26)
    elif s.panel_page == "RIG":
        draw_result(SimpleNamespace(layout=layout), context)
        presets = _foldout(layout, s, "show_presets")
        if presets is not None:
            draw_presets(SimpleNamespace(layout=presets), context)
    else:
        from . import draw_environment
        draw_environment(layout, s)
    layout.separator()
    row = layout.row(align=True)
    row.label(text="AI 模型已保留" if s.models_loaded else "首次生成时加载 AI", icon="MEMORY")
    row.operator("flowhmr.open_web", text="Web 版", icon="URL")


def _draw_message(layout, s, width):
    if s.message:
        import textwrap
        box = layout.box()
        box.alert = s.last_error
        for line in textwrap.wrap(s.message, width=width):
            box.label(text=line)


def _draw_progress(layout, s):
    if hasattr(layout, "progress"):
        layout.progress(factor=s.progress, type="BAR", text=f"{s.progress:.0%}")
    if s.status in operators.ACTIVE:
        row = layout.row()
        row.enabled = not s.busy
        row.operator("flowhmr.cancel", text="停止任务", icon="CANCEL")


def _draw_result_actions(layout, context):
    s = context.scene.flowhmr
    box = layout.box()
    box.label(text="姿势已提取" if s.source_is_image else "动作已生成", icon="CHECKMARK")
    if s.result_people_json != "[]":
        box.prop(s, "result_person", text="结果人物")
    row = box.row()
    row.enabled = not s.busy and not (s.target_rig and len(json.loads(s.result_people_json)) > 1 and s.result_person == "ALL")
    row.scale_y = 1.3
    row.operator("flowhmr.import_motion", text="添加到目标骨架" if s.target_rig else "新建动作骨骼", icon="ACTION")
    if s.target_rig and not row.enabled and not s.busy:
        box.label(text="多人结果请先选择一个人物", icon="INFO")
    if s.preview_result_roots != "[]" and not s.source_is_image:
        row = box.row(align=True)
        row.operator("screen.animation_play", text="播放 / 暂停动作", icon="PLAY")
        row.operator("flowhmr.open_popup", text="原素材", icon="FILE_MOVIE")


def draw_media_import(layout, settings):
    media = layout.operator("flowhmr.select_video", text="上传素材", icon="IMPORT")
    media.media_type, media.image_resolution = "ALL", settings.image_resolution


def draw_source(left, context):
    s = context.scene.flowhmr
    left.use_property_decorate = False

    source = left.box()
    header = source.row(align=True)
    header.label(text="素材编辑器", icon="FILE_MOVIE")
    add = source.row()
    add.enabled = not s.busy and s.status not in operators.ACTIVE
    add.scale_y = 1.6
    media = add.operator("flowhmr.select_video", text="选择视频 / 图片", icon="IMPORT")
    media.media_type, media.image_resolution = "ALL", s.image_resolution
    if s.media:
        row = source.row(align=True)
        row.label(text=s.source_name or s.media[min(s.media_index, len(s.media) - 1)].name,
                  icon="IMAGE_DATA" if s.source_is_image else "FILE_MOVIE")
        remove = row.row()
        remove.enabled = not s.busy and s.status not in operators.ACTIVE
        remove.operator("flowhmr.remove_media", text="", icon="TRASH").current = True
        if len(s.media) > 1:
            source.prop(s, "show_media_library", text=f"已导入素材（{len(s.media)}）",
                        icon="TRIA_DOWN" if s.show_media_library else "TRIA_RIGHT", emboss=False)
        if s.show_media_library:
            source.template_list("FLOWHMR_UL_Media", "", s, "media", s, "media_index", rows=3)
            row = source.row(align=True)
            row.enabled = not s.busy and s.status not in operators.ACTIVE
            row.operator("flowhmr.open_media", text="使用素材", icon="CHECKMARK")
            row.operator("flowhmr.remove_media", text="移除", icon="TRASH")
    else:
        source.label(text="支持视频和图片，可一次选择多个文件")
    if s.video_path:
        media_panel.draw_cover(source, context, scale=5)
        source.operator("flowhmr.open_popup", text="放大图片 / 修正人物框" if s.source_is_image else "打开视频播放器", icon="IMAGE_DATA" if s.source_is_image else "PLAY")
        clip = source.column()
        clip.enabled = not s.busy and s.status not in operators.ACTIVE
        if s.source_is_image:
            clip.label(text="图片：提取单张姿势", icon="IMAGE_DATA")
            clip.label(text="先识别人物框，单人也可修正后确认", icon="SELECT_SET")
            row = clip.row(align=True)
            row.prop(s, "image_resolution", text="尺寸")
            apply = row.operator("flowhmr.select_video", text="应用")
            apply.filepath, apply.image_resolution = s.source_path, s.image_resolution
            if s.media_resolution:
                clip.label(text=s.media_resolution)
            if s.media_warning:
                from textwrap import wrap
                for line in wrap(s.media_warning, width=30):
                    clip.label(text=line, icon="INFO")
        else:
            row = clip.row(align=True)
            row.prop(s, "start_time", text="起点秒")
            end = row.row(align=True)
            end.enabled = not s.to_video_end
            end.prop(s, "end_time", text="终点秒")
            clip.prop(s, "to_video_end", text="到片尾")
    if s.busy or s.status in operators.ACTIVE:
        _draw_progress(left, s)
    if s.status == "waiting_user":
        draw_people(left, s)
    else:
        row = left.row()
        row.enabled = bool(s.video_path) and not s.busy and s.status not in operators.ACTIVE
        row.scale_y = 1.5
        rerun = bool(s.job_id) and s.status in {"done", "failed", "cancelled"}
        row.operator("flowhmr.submit", text="按当前配置重新生成" if rerun else
            "提取图片姿势" if s.source_is_image else "开始 AI 识别" if s.manual_select else "生成动作",
            icon="FILE_REFRESH" if rerun else "TRACKER").rerun_current = rerun
        if rerun:
            left.label(text="使用当前片段和设置，保留旧任务并重新选人", icon="INFO")
    if s.status == "done":
        _draw_result_actions(left, context)

    _draw_message(left, s, 38)


def draw_options(panel, context):
    layout = panel.layout
    s = context.scene.flowhmr
    col = editable(panel, s)
    col.prop(s, "static_camera")
    draw_contact(col, s)
    col.prop(s, "auto_import")
    col.prop(s, "import_frame")
    col.prop(s, "root_motion")
    col.prop(s, "vertical_motion")
    col.prop(s, "motion_height_offset")
    col.prop(s, "import_mesh")
    if s.import_mesh:
        col.prop(s, "body_variant")
    from types import SimpleNamespace
    for prop, draw_fn in (("show_generation_details", draw_advanced), ("show_rig_details", draw_rig_settings)):
        content = _foldout(layout, s, prop)
        if content is not None:
            draw_fn(SimpleNamespace(layout=content), context)
    layout.label(text="AI 模型已加载，可复用" if s.models_loaded else "AI 模型待加载", icon="MEMORY")
    release = layout.row()
    release.enabled = not s.busy and s.status not in operators.ACTIVE
    release.operator("flowhmr.release_vram", text="释放 AI 模型", icon="MEMORY")
    layout.operator("flowhmr.open_popup", text="放大视频（独立窗口）", icon="WINDOW")


PANELS = (FLOWHMR_UL_History, FLOWHMR_UL_Presets, FLOWHMR_UL_Media)
