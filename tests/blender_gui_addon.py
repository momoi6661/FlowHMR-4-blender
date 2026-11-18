"""Draw delivered native controls in a separate factory-startup GUI process.

No backend calls, user preferences, inference jobs or existing Blender windows.
"""
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import operators, preview, video_ui, workflow

MARKER = ROOT / '.cache/blender-test/addon-gui-result.txt'
MARKER.unlink(missing_ok=True)
stage = 0
main = player = None
drawn_pages = set()


class FLOWHMR_TEST_PT_draw(bpy.types.Panel):
    bl_label = 'FlowHMR isolated UI test'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'
    bl_order = -100

    def draw(self, context):
        workflow.draw_panel(self, context)
        drawn_pages.add(context.scene.flowhmr.panel_page)


def finish(error=None):
    if error:
        traceback.print_exc()
    MARKER.write_text(error or 'BLENDER_ADDON_GUI_SMOKE_OK', encoding='utf-8')
    print(error or 'BLENDER_ADDON_GUI_SMOKE_OK', flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    # Exit this owned process only; avoid third-party shutdown hooks.
    os._exit(1 if error else 0)


def tick():
    global main, player, stage
    try:
        s = bpy.context.scene.flowhmr
        if stage == 0:
            main = bpy.context.window
            s.backend_root, s.auto_start_backend = str(ROOT), False
            properties = next(a for a in main.screen.areas if a.type == 'PROPERTIES')
            properties.spaces.active.context = 'SCENE'
            operators._fill_history(s, dict(jobs=[dict(
                id='isolated-cover', filename='gesture.mp4', status='done', created=1700000000,
                num_frames=30, params=dict(start_frame=30, end_frame=59),
                local_thumbnail=str(ROOT / '.cache/blender-test/history-cover.jpg'))]))
            assert len(s.history) == 1
            assert s.history[0].icon_id > 0, 'GUI preview icon missing'
            s.panel_page = 'HISTORY'
            s.video_path = str(ROOT / 'assets/example/example.mp4')
            preview.attach_video(main.scene, s.video_path)
            s.video_fps, s.video_frames = s.preview_clip.fps, s.preview_clip.frame_duration
            s.video_duration = s.video_frames / s.video_fps
            s.start_time, s.end_time, s.to_video_end = .5, 2.0, False
            area = next(a for a in main.screen.areas if a.type == 'VIEW_3D')
            with bpy.context.temp_override(window=main, area=area):
                assert bpy.ops.flowhmr.open_popup() == {'FINISHED'}
            player = next(w for w in bpy.context.window_manager.windows if w != main)
        elif stage == 1:
            assert 'HISTORY' in drawn_pages
            assert s.history[0].icon_id > 0
            area = player.screen.areas[0]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=player, area=area, region=region):
                video_ui.draw_timeline(bpy.context)
                assert area.spaces.active.show_region_header
                video_ui.seek(main.scene, 1)
                bpy.ops.flowhmr.preview_range(action='start')
                assert abs(s.start_time - 1) < .001
            print('GUI_HISTORY_REAL_COVER_ICON_AND_PLAYER_RANGE_DRAW_OK', flush=True)
            s.panel_page = 'MEDIA'
        elif stage == 2:
            assert 'MEDIA' in drawn_pages
            s.source_is_image, s.status = True, 'waiting_user'
            s.people.clear()
            p = s.people.add()
            p.person_index, p.name, p.selected = 0, 'Single person', True
            w, h = s.preview_clip.size
            video_ui.set_people(main.scene, dict(candidates=[dict(index=0, frames=[0],
                boxes=[[w * .2, h * .1, w * .8, h * .9]])]))
            main.scene.frame_set(1000)
            s.panel_page = 'RIG'
        elif stage == 3:
            assert 'RIG' in drawn_pages
            area = player.screen.areas[0]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=player, area=area, region=region):
                rect = video_ui.boxes(bpy.context)[0][1]
                video_ui.draw()
                result = video_ui.FLOWHMR_OT_VideoClick.invoke(None, bpy.context,
                    SimpleNamespace(mouse_region_x=(rect[0] + rect[2]) / 2,
                                    mouse_region_y=(rect[1] + rect[3]) / 2))
                assert result == {'FINISHED'} and not s.people[0].selected
            print('GUI_SINGLE_IMAGE_BOX_DRAW_CLICK_AND_TIMELINE_INDEPENDENCE_OK', flush=True)
            s.status, s.panel_page = 'idle', 'MODELS'
        else:
            assert drawn_pages == {'MEDIA', 'HISTORY', 'RIG', 'MODELS'}, drawn_pages
            bpy.utils.unregister_class(FLOWHMR_TEST_PT_draw)
            addon.unregister()
            assert operators._HISTORY_PREVIEWS is None
            finish()
        for window in (main, player):
            for area in window.screen.areas:
                area.tag_redraw()
        stage += 1
        return .7
    except BaseException:
        finish(traceback.format_exc())


addon.register()
bpy.utils.register_class(FLOWHMR_TEST_PT_draw)
bpy.app.timers.register(tick, first_interval=1)
