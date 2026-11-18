"""Native addon library/range/UI/lifetime regression, without opening user windows."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import operators, workflow, media_panel, video_ui


def wait(s):
    deadline = time.monotonic() + 60
    while s.busy and time.monotonic() < deadline:
        operators._pump()
        time.sleep(.03)
    assert not s.busy and not s.last_error, s.message


class Layout:
    """Validate registered buttons and RNA fields for every native settings page."""
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
        self.enabled = True

    def child(self, *args, **kwargs):
        return Layout(self.calls)

    row = column = box = split = child

    def operator(self, name, **kwargs):
        group, operation = name.split(".")
        getattr(getattr(bpy.ops, group), operation).get_rna_type()
        icon = kwargs.get("icon")
        if icon:
            icons = bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items
            assert icon in icons, icon
        self.calls.append(("operator", name))
        return SimpleNamespace()

    def prop(self, obj, name, **kwargs):
        assert hasattr(obj, name), name
        self.calls.append(("prop", name))

    def prop_search(self, obj, name, target, search, **kwargs):
        assert hasattr(obj, name) and hasattr(target, search)

    def label(self, **kwargs):
        self.calls.append(("label", kwargs.get("text")))

    def template_list(self, name, _id, obj, prop, active, active_prop, **kwargs):
        assert hasattr(bpy.types, name)
        assert hasattr(obj, prop) and hasattr(active, active_prop)
        self.calls.append(("list", prop))

    def template_icon(self, **kwargs):
        pass

    def separator(self):
        pass

    def progress(self, **kwargs):
        pass


addon.register()
try:
    scene = bpy.context.scene
    s = scene.flowhmr
    s.backend_root = str(ROOT)
    # A remembered path must open the picker; explicit drop paths still import.
    picker_calls = []
    fake_context = SimpleNamespace(window_manager=SimpleNamespace(fileselect_add=lambda op: picker_calls.append(op)))
    fake = SimpleNamespace(filepath="old.mp4", directory="", files=[], media_type="IMAGE",
                           properties=SimpleNamespace(is_property_set=lambda *a, **kw: False))
    assert operators.FLOWHMR_OT_SelectVideo.invoke(fake, fake_context, None) == {"RUNNING_MODAL"}
    assert picker_calls == [fake] and "*.png" in fake.filter_glob and "*.mp4" not in fake.filter_glob
    fake.properties = SimpleNamespace(is_property_set=lambda *a, **kw: True)
    fake.execute = lambda context: {"FINISHED"}
    assert operators.FLOWHMR_OT_SelectVideo.invoke(fake, fake_context, None) == {"FINISHED"}
    windows = set(bpy.context.window_manager.windows)
    areas = [a.type for a in bpy.context.screen.areas]
    directory = ROOT / ".cache/blender-test/native-library"
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / "姿势.png"
    picture = bpy.data.images.new("Library fixture", width=128, height=128)
    picture.generated_color = (.15, .3, .5, 1)
    picture.filepath_raw, picture.file_format = str(image), "PNG"
    picture.save()
    bpy.data.images.remove(picture)
    # Real decoding through the uv project interpreter, including multi-selection.
    bpy.ops.flowhmr.select_video(filepath=str(ROOT / "assets/example/example.mp4"))
    wait(s)
    assert len(s.media) == 1 and len(json.loads(s.panel_frames_json)) == 1
    source = s.source_path
    s.start_time, s.end_time, s.to_video_end = 1, 3, False
    bpy.ops.flowhmr.select_video(directory=str(directory), files=[dict(name=image.name)])
    wait(s)
    assert len(s.media) == 2 and s.source_is_image and s.video_duration == 1
    assert "128 × 128" in s.media_resolution and "256 × 256" in s.media_resolution
    assert "不能恢复" in s.media_warning
    bpy.ops.flowhmr.select_video("INVOKE_DEFAULT", filepath=str(image), image_resolution="ORIGINAL")
    wait(s)
    assert len(s.media) == 2 and s.image_resolution == "ORIGINAL"
    assert s.media[s.media_index].width == 128
    s.media_index = 0
    bpy.ops.flowhmr.open_media()
    assert s.source_path == source and s.start_time == 1 and s.end_time == 3 and not s.to_video_end
    assert not media_panel._PLAYING and not bpy.app.timers.is_registered(media_panel._tick)
    assert set(bpy.context.window_manager.windows) == windows
    assert [a.type for a in bpy.context.screen.areas] == areas
    print("MULTI_MEDIA_LIBRARY_STATIC_COVER_CLIP_RESTORE_NO_WINDOWS_NO_PLAYBACK_TIMER_OK", flush=True)

    # Reimporting the same file updates metadata, without duplicating the library.
    bpy.ops.flowhmr.select_video(filepath=source)
    wait(s)
    assert len(s.media) == 2 and s.start_time == 1 and s.end_time == 3
    s.status = "running"
    assert bpy.ops.flowhmr.open_media() == {"CANCELLED"}
    assert bpy.ops.flowhmr.remove_media() == {"CANCELLED"}
    s.status = "idle"

    item = s.history.add()
    item.name, item.job_id, item.status = "开门视频", "fixture-job", "done"
    s.show_media_library = True
    with patch.object(media_panel, "draw_cover"):
        original_workspace = bpy.context.workspace
        original_windows = set(bpy.context.window_manager.windows)
        for page in ("MEDIA", "HISTORY", "RIG", "MODELS"):
            s.busy = True
            s.panel_page = page
            s.busy = False
            layout = Layout()
            workflow.draw_panel(SimpleNamespace(layout=layout), bpy.context)
            assert ("prop", "panel_page") in layout.calls
            if page == "MEDIA":
                assert ("operator", "flowhmr.submit") in layout.calls
                assert ("prop", "output_rig") in layout.calls
                assert ("operator", "flowhmr.open_workspace") not in layout.calls
            if page == "HISTORY":
                assert ("list", "history") in layout.calls
        assert bpy.context.workspace == original_workspace
        assert set(bpy.context.window_manager.windows) == original_windows
        s.panel_page = "MEDIA"
    print("SINGLE_NATIVE_PANEL_ALL_PAGES_WITHOUT_WORKSPACE_CHANGES_OK", flush=True)


    assert not hasattr(bpy.types, "FLOWHMR_PT_workbench")
    assert not hasattr(bpy.types, "FLOWHMR_PT_workspace_source")
    assert not hasattr(workflow, "draw_workbench")
    print("WORKSPACE_MODE_REMOVED_NATIVE_PLAYER_RETAINED_OK", flush=True)

    # Removing a reference never deletes the user's media or generated Actions.
    action = bpy.data.actions.new("Preserved animation")
    s.media_index = 1  # The highlighted library item can differ from the active source.
    bpy.ops.flowhmr.remove_media(current=True)
    assert len(s.media) == 1 and s.source_is_image and Path(source).is_file()
    bpy.ops.flowhmr.remove_media()
    assert not s.media and not s.video_path and s.preview_clip is None and image.is_file()
    assert action.name in bpy.data.actions
    print("MEDIA_REMOVE_ONLY_REFERENCES_PRESERVES_FILES_AND_ACTIONS_OK", flush=True)

    # Both native eyedropper targets and an imported character resolve to an
    # armature, preserving the already selected animation task.
    rig = bpy.data.objects.new("Existing target", bpy.data.armatures.new("Existing target"))
    scene.collection.objects.link(rig)
    body = bpy.data.objects.new("Pick this character", bpy.data.meshes.new("Character body"))
    scene.collection.objects.link(body)
    modifier = body.modifiers.new("Character skin", "ARMATURE")
    modifier.object = rig
    s.target_model = body
    assert s.target_rig == rig and s.target_mode == "TARGET"
    other = bpy.data.objects.new("Another target", bpy.data.armatures.new("Another target"))
    scene.collection.objects.link(other)
    s.target_rig = other
    assert s.target_model == other
    s.target_model = None
    assert s.target_rig is None
    character = directory / "角色.blend"
    bpy.data.libraries.write(str(character), {rig, body})
    s.job_id, s.status = "Keep-selected-result", "done"
    bpy.ops.flowhmr.import_character(filepath=str(character))
    assert s.target_rig and s.target_rig != rig and s.target_rig.type == "ARMATURE"
    assert s.target_model == s.target_rig and s.target_mode == "TARGET"
    assert s.job_id == "Keep-selected-result" and s.status == "done"
    assert rig.name in bpy.data.objects and body.name in bpy.data.objects
    s.status = "idle"
    print("CHARACTER_PICKER_BOUND_MESH_RESOLUTION_AND_IMPORT_PRESERVE_RESULT_OK", flush=True)

    # Real drop handler accepts only the plugin sidebar/player; regular viewports
    # and active inference jobs keep their normal drop behavior.
    if hasattr(video_ui, "FLOWHMR_FH_Media"):
        context = SimpleNamespace(area=SimpleNamespace(type="VIEW_3D"), scene=scene,
            region=SimpleNamespace(type="UI", active_panel_category="FlowHMR"),
            screen={}, workspace={})
        assert video_ui.FLOWHMR_FH_Media.poll_drop(context)
        context.region.active_panel_category = "Item"
        assert not video_ui.FLOWHMR_FH_Media.poll_drop(context)
        context.area.type = "CLIP_EDITOR"
        context.screen = {"flowhmr_popup": True}
        assert video_ui.FLOWHMR_FH_Media.poll_drop(context)
        context.area.type, context.workspace = "PROPERTIES", {"flowhmr_workspace": True}
        assert not video_ui.FLOWHMR_FH_Media.poll_drop(context)
        context.workspace = {}
        assert not video_ui.FLOWHMR_FH_Media.poll_drop(context)
        context.workspace = {"flowhmr_workspace": True}
        s.status = "running"
        assert not video_ui.FLOWHMR_FH_Media.poll_drop(context)
        s.status = "idle"
    print("DROP_HANDLER_SCOPED_TO_PLUGIN_AND_REJECTS_ACTIVE_TASKS_OK", flush=True)
finally:
    with patch.object(addon.backend, "stop_backend") as stop:
        addon.unregister()
        stop.assert_not_called()
    assert not bpy.app.timers.is_registered(media_panel._tick)
print("BLENDER_NATIVE_PANEL_SMOKE_OK", flush=True)
