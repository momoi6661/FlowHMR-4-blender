"""Run with Blender GUI --factory-startup --python; writes a completion marker."""
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import motion, preview, workspace, video_ui

MARKER = ROOT / ".cache/blender-test/preview-result.txt"
MARKER.unlink(missing_ok=True)
addon.register()


def validate():
    if preview._BUILDERS or bpy.context.scene.flowhmr.busy:
        return 0.2
    try:
        context, scene = bpy.context, bpy.context.scene
        s = scene.flowhmr
        assert context.workspace.get("flowhmr_layout_ready"), s.message
        assert sorted(a.type for a in context.screen.areas) == ["CLIP_EDITOR", "DOPESHEET_EDITOR", "PROPERTIES", "PROPERTIES", "VIEW_3D"]
        assert context.workspace["flowhmr_layout_version"] == preview.LAYOUT_VERSION
        properties = sorted((a for a in context.screen.areas if a.type == "PROPERTIES"), key=lambda a: a.x)
        video_area = next(a for a in context.screen.areas if a.type == "CLIP_EDITOR")
        viewport_area = next(a for a in context.screen.areas if a.type == "VIEW_3D")
        assert video_area.x == properties[0].x and abs(video_area.width - properties[0].width) < 2
        assert viewport_area.x > video_area.x and viewport_area.height > video_area.height
        for area, side in zip(properties, ("SOURCE", "SETTINGS")):
            with context.temp_override(area=area):
                assert workspace.control_side(context) == side
                assert not area.spaces.active.use_pin_id
                assert area.spaces.active.context == "SCENE"
                assert workspace.FLOWHMR_PT_WorkspaceSource.poll(context) == (side == "SOURCE")
                assert workspace.FLOWHMR_PT_WorkspaceSettings.poll(context) == (side == "SETTINGS")
                assert not bpy.types.SCENE_PT_scene.poll(context)
        assert len(bpy.data.workspaces["Layout"].screens[0].areas) == 4
        preview.attach_video(scene, ROOT / "assets/example/example.mp4")
        clip = s.preview_clip
        assert clip.frame_duration > 1 and clip.size[0] > 0
        s.video_fps, s.video_frames = clip.fps, clip.frame_duration
        s.video_duration = clip.frame_duration / clip.fps
        video = next(a.spaces.active for a in context.screen.areas if a.type == "CLIP_EDITOR")
        area = next(a for a in context.screen.areas if a.type == "CLIP_EDITOR")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(area=area, region=region):
            assert video_ui.controls(context) == []
        for frame in (1, 13, 25):
            scene.frame_set(frame)
            assert video.clip_user.frame_current == round((frame - 1) / 24 * clip.fps) + 1
        print("NATIVE_VIDEO_WORKSPACE_AND_24FPS_SYNC_OK", clip.size[:], clip.fps, flush=True)
        s.start_time, s.end_time, s.to_video_end = 2, 3, False
        bpy.ops.flowhmr.preview_range()
        assert (scene.frame_preview_start, scene.frame_preview_end) == (49, 72)
        bpy.ops.flowhmr.preview_range(action="start")
        assert s.start_time == 2
        scene.frame_set(60)
        bpy.ops.flowhmr.preview_range(action="home")
        assert scene.frame_current == 49
        sources = motion.import_motion(context, ROOT / ".cache/blender-test/motion.npz", frame_start=13)
        sources[0]["flowhmr_source_start"] = 60
        preview.show_result(context, sources, sources)
        assert s.preview_origin_seconds == 2 and s.preview_frame_start == 13
        assert (scene.frame_preview_start, scene.frame_preview_end) == (13, 37)
        scene.frame_set(25)
        assert video.clip_user.frame_current == round(2.5 * clip.fps) + 1
        viewport = next(a.spaces.active for a in context.screen.areas if a.type == "VIEW_3D")
        assert viewport.local_view is not None
        assert sources[0].local_view_get(viewport)
        assert not bpy.data.objects["Cube"].local_view_get(viewport)
        assert not viewport.overlay.show_overlays
        assert scene.render.fps == 24
        existing = context.workspace
        preview.open_workspace(context)
        assert context.workspace == existing and len(context.screen.areas) == 5
        print("TRIMMED_RESULT_VIDEO_SYNC_AND_ISOLATED_PREVIEW_OK", flush=True)
        bpy.app.timers.register(after_redraw, first_interval=0.3)
        return None
    except Exception:
        MARKER.write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
    bpy.ops.wm.quit_blender()
    return None


def after_redraw():
    try:
        context, scene = bpy.context, bpy.context.scene
        video = next(a.spaces.active for a in context.screen.areas if a.type == "CLIP_EDITOR")
        assert video.clip_user.frame_current == round(2.5 * scene.flowhmr.preview_clip.fps) + 1, video.clip_user.frame_current
        area = next(a for a in context.screen.areas if a.type == "CLIP_EDITOR")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(area=area, region=region):
            s = scene.flowhmr
            s.status = "waiting_user"
            video_ui.set_people(scene, dict(candidates=[dict(index=0, frames=[0, 150],
                boxes=[[200, 120, 700, 950], [200, 120, 700, 950]])]))
            person = s.people.add()
            person.person_index, person.name = 0, "人物 1"
            rect = video_ui.boxes(context)[0][1]
            assert video_ui.FLOWHMR_OT_VideoClick.invoke(None, context, SimpleNamespace(
                mouse_region_x=(rect[0] + rect[2]) / 2, mouse_region_y=(rect[1] + rect[3]) / 2)) == {"FINISHED"}
            assert person.selected
            s.people.clear()
            s.status = "idle"
        print("WORKSPACE_CLEAN_VIDEO_AND_PERSON_CLICK_OK", flush=True)
        area = next(a for a in context.screen.areas if a.type == "VIEW_3D")
        with context.temp_override(area=area):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
            bpy.ops.screen.screenshot(filepath=str(ROOT / "logs/blender-workspace-070.png"))
        context.window.workspace = bpy.data.workspaces["Layout"]
        bpy.app.timers.register(after_switch, first_interval=.3)
        return None
    except Exception:
        MARKER.write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
    bpy.ops.wm.quit_blender()
    return None


def after_switch():
    try:
        context = bpy.context
        area = next(a for a in context.screen.areas if a.type == "PROPERTIES")
        with context.temp_override(area=area):
            assert workspace.control_side(context) is None
            assert bpy.types.SCENE_PT_scene.poll(context)
        addon.unregister()
        assert preview.sync_video not in bpy.app.handlers.frame_change_post
        MARKER.write_text("BLENDER_PREVIEW_SMOKE_OK", encoding="utf-8")
        print("VIDEO_SYNC_AFTER_REDRAW_AND_CLEANUP_OK", flush=True)
    except Exception:
        MARKER.write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
    bpy.ops.wm.quit_blender()
    return None


def begin():
    bpy.context.scene.render.fps = 24
    bpy.context.scene.flowhmr.backend_root = str(ROOT)
    bpy.ops.flowhmr.select_video(filepath=str(ROOT / "assets/example/example.mp4"))
    preview.open_workspace(bpy.context)
    bpy.app.timers.register(validate, first_interval=0.3)
    return None


bpy.app.timers.register(begin, first_interval=1)
