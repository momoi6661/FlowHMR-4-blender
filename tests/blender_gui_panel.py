"""Regression: choosing media stays in the add-on panel, with no new window."""
from pathlib import Path
import json, sys, time, traceback
import bpy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import blender_addon as addon
MARKER=ROOT/".cache/blender-test/panel-result.txt"
MARKER.unlink(missing_ok=True)
addon.register()
original=None
areas=None
started=None

def fail():
    MARKER.write_text(traceback.format_exc(),encoding="utf-8")
    traceback.print_exc()
    bpy.ops.wm.quit_blender()

def begin():
    global original,areas,started
    try:
        original=set(bpy.context.window_manager.windows)
        areas=[a.type for a in bpy.context.screen.areas]
        s=bpy.context.scene.flowhmr
        s.backend_root=str(ROOT)
        bpy.ops.flowhmr.select_video(filepath=str(ROOT/"assets/example/example.mp4"))
        started=time.monotonic()
        bpy.app.timers.register(validate,first_interval=.2)
    except Exception:
        fail()

def validate():
    try:
        s=bpy.context.scene.flowhmr
        if s.busy:
            assert time.monotonic()-started<60
            return .2
        assert not s.last_error,s.message
        assert set(bpy.context.window_manager.windows)==original
        assert [a.type for a in bpy.context.screen.areas]==areas
        assert len(json.loads(s.panel_frames_json))==1
        assert s.preview_clip and not s.show_options
        area=next(a for a in bpy.context.screen.areas if a.type=="VIEW_3D")
        area.spaces.active.show_region_ui=True
        s.panel_position=.2
        with bpy.context.temp_override(area=area):
            bpy.ops.flowhmr.panel_preview(action="start")
            assert abs(s.start_time-s.video_duration*.2)<.05
            s.panel_position=.6
            bpy.ops.flowhmr.panel_preview(action="end")
            assert not s.to_video_end and abs(s.end_time-s.video_duration*.6)<.05
            bpy.ops.flowhmr.panel_preview(action="play")
            assert not bpy.app.timers.is_registered(addon.media_panel._tick)
            player=next(w for w in bpy.context.window_manager.windows if w not in original)
            assert not player.screen.areas[0].spaces.active.show_region_ui
            assert s.id_data.sync_mode=="FRAME_DROP"
        addon.unregister()
        assert not bpy.app.timers.is_registered(addon.media_panel._tick)
        assert addon.media_panel._ICONS is None
        MARKER.write_text("BLENDER_PANEL_COVER_AND_PLAYER_SMOKE_OK",encoding="utf-8")
        bpy.ops.wm.quit_blender()
    except Exception:
        fail()

bpy.app.timers.register(begin,first_interval=1)
