"""Native GUI integration. No browser or inference needed; --factory-startup."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import video_ui, preview

MARKER = ROOT / ".cache/blender-test/popup-result.txt"
MARKER.unlink(missing_ok=True)
addon.register()
print("POPUP_ADDON_REGISTERED", len(addon.popup._FILTERS), flush=True)
original = None
popup = None


def fail():
    MARKER.write_text(traceback.format_exc(), encoding="utf-8")
    traceback.print_exc()
    bpy.ops.wm.quit_blender()


def begin():
    global original, popup
    try:
        print("POPUP_BEGIN", flush=True)
        original = bpy.context.window
        layout = [(a.type, a.width, a.height) for a in original.screen.areas]
        area = next(a for a in original.screen.areas if a.type == "VIEW_3D")
        with bpy.context.temp_override(area=area):
            bpy.ops.flowhmr.open_popup()
        popup = next(w for w in bpy.context.window_manager.windows if w != original)
        print("POPUP_CREATED", flush=True)
        assert [(a.type, a.width, a.height) for a in original.screen.areas] == layout
        assert popup.screen.get("flowhmr_popup") and len(popup.screen.areas) == 1
        assert not original.screen.get("flowhmr_popup")
        assert popup.screen.areas[0].type == "CLIP_EDITOR"
        assert not popup.screen.areas[0].spaces.active.show_region_ui
        scene = original.scene
        scene.render.fps = 24
        s = scene.flowhmr
        s.video_path = str(ROOT / "assets/example/example.mp4")
        s.video_fps, s.video_duration, s.video_frames = 24, 5.04, 121
        s.start_time, s.end_time, s.to_video_end = 1, 3, False
        preview.attach_video(scene, s.video_path)
        job = dict(candidates=[dict(index=0, frames=[0, 120], boxes=[[200, 120, 700, 950], [220, 140, 720, 970]]),
                               dict(index=1, frames=[0, 120], boxes=[[1000, 120, 1500, 950], [1020, 140, 1520, 970]])])
        video_ui.set_people(scene, job)
        for index in (0, 1):
            item = s.people.add()
            item.person_index, item.name = index, f"Person {index + 1}"
        s.status = "waiting_user"
        bpy.app.timers.register(validate, first_interval=1)
    except Exception:
        fail()


def validate():
    try:
        print("POPUP_VALIDATE", flush=True)
        area = popup.screen.areas[0]
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=popup, area=area, region=region):
            context = bpy.context
            detected = video_ui.boxes(context)
            assert len(detected) == 2
            assert all(r[0] < r[2] and r[1] < r[3] for _i, r in detected)
            # Exercise the actual click handler against normalized clip geometry.
            for index, rect in detected:
                event = SimpleNamespace(mouse_region_x=(rect[0] + rect[2]) / 2,
                                        mouse_region_y=(rect[1] + rect[3]) / 2)
                result = video_ui.FLOWHMR_OT_VideoClick.invoke(None, context, event)
                assert result == {"FINISHED"}, (index, rect, result)
            s = context.scene.flowhmr
            assert [p.selected for p in s.people] == [True, True]
            rect = detected[0][1]
            video_ui.FLOWHMR_OT_VideoClick.invoke(None, context, SimpleNamespace(
                mouse_region_x=(rect[0] + rect[2]) / 2, mouse_region_y=(rect[1] + rect[3]) / 2))
            assert [p.selected for p in s.people] == [False, True]
            # Native header replaces the old GPU buttons and trim strip.
            assert video_ui.controls(context) == []
            assert area.spaces.active.show_region_header
            s.source_is_image = True
            s.box_person_index = 0
            video_ui.seek(context.scene, 0)
            left, bottom, right, top = detected[0][1]
            video_ui.record_box(context, 0, (left - 10, bottom - 15, right + 10, top + 15))
            edits = json.loads(s.box_edits_json)
            assert len(edits) == 1 and edits[0]["whole_track"]
            assert s.people[0].selected
            assert video_ui.box_at(video_ui._PEOPLE[context.scene.as_pointer()][0], 120, edits) == edits[0]["box"]
            bpy.ops.flowhmr.reset_person_boxes()
            assert s.box_edits_json == "[]"
            s.source_is_image = False
            s.status = "idle"
            video_ui.seek(context.scene, 2)
            assert area.spaces.active.clip_user.frame_current == 49
            assert not s.show_options and not s.show_presets and not s.show_history
            assert not hasattr(s, "workflow_page")
            assert not s.auto_release_vram
            panels = [cls for cls in addon.CLASSES if issubclass(cls, bpy.types.Panel)]
            assert {cls.__name__ for cls in panels} == {"FLOWHMR_PT_Main", "FLOWHMR_PT_Popup", "FLOWHMR_PT_Workbench",
                "FLOWHMR_PT_WorkspaceSource", "FLOWHMR_PT_WorkspaceSettings"}
            print("POPUP_NATIVE_HEADER_MULTIPLE_PEOPLE_IMAGE_REDRAW_AND_VIDEO_SYNC_OK", flush=True)
            print("POPUP_UI_CATEGORY", [(r.type, r.active_panel_category) for r in area.regions if r.type == "UI"], flush=True)
            assert addon.popup.FLOWHMR_PT_Popup.bl_category == ""
        addon.unregister()
        assert not video_ui._KEYMAPS and not video_ui._HANDLES
        MARKER.write_text("BLENDER_POPUP_SMOKE_OK", encoding="utf-8")
        bpy.ops.wm.quit_blender()
    except Exception:
        fail()


bpy.app.timers.register(begin, first_interval=1)
