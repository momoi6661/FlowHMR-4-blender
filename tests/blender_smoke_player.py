"""Player range gestures, image boxes and current-config rerun transport."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon
from blender_addon import operators, preview, video_ui
from blender_addon.ranges import frame_range

addon.register()
try:
    scene, s = bpy.context.scene, bpy.context.scene.flowhmr
    s.video_path = str(ROOT / 'assets/example/example.mp4')
    s.video_duration, s.video_fps = 10, 60
    s.preview_origin_seconds, s.preview_frame_start = 0, 11
    s.start_time, s.end_time, s.to_video_end = 0, 10, True
    scene.render.fps = 24
    video_ui.seek(scene, 2)
    assert abs(preview.video_time(scene) - 2) < 1e-6
    assert bpy.ops.flowhmr.preview_range(action='start') == {'FINISHED'}
    video_ui.seek(scene, 4)
    assert bpy.ops.flowhmr.preview_range(action='end') == {'FINISHED'}
    assert frame_range(s) == (60, 120)
    assert bpy.ops.flowhmr.preview_range() == {'FINISHED'}
    assert scene.frame_preview_start == 59 and scene.frame_preview_end == 107
    # End cannot precede start; start cannot follow end. Scene FPS and input
    # FPS do not alter the canonical inference frame selection.
    video_ui.mark_range(scene, 'end', .5)
    assert frame_range(s) == (60, 60)
    video_ui.mark_range(scene, 'start', 9)
    assert frame_range(s) == (60, 60)
    assert bpy.ops.flowhmr.preview_range(action='reset') == {'FINISHED'}
    assert frame_range(s) == (0, None)
    s.status = 'waiting_user'
    try:
        video_ui.mark_range(scene, 'start', 2)
        raise AssertionError('A running selection task was changed')
    except ValueError:
        pass
    print('PLAYER_CANONICAL_RANGE_MARK_RESET_CLAMP_AND_ACTIVE_JOB_LOCK_OK', flush=True)

    # A still image remains editable at frame zero even if the user has moved
    # Blender's scene timeline outside the one-second decoding container.
    s.source_is_image = True
    s.preview_clip = bpy.data.movieclips.load(s.video_path, check_existing=True)
    width, height = s.preview_clip.size
    candidate = dict(index=0, frames=[0, 29], boxes=[[0, 0, width, height]] * 2)
    video_ui.set_people(scene, dict(candidates=[candidate]))
    person = s.people.add()
    person.person_index, person.selected = 0, True
    scene.frame_set(1000)
    view = SimpleNamespace(view_to_region=lambda x, y, **kw: (x * 1000, y * 500),
                           region_to_view=lambda x, y: (x / 1000, y / 500))
    context = SimpleNamespace(scene=scene, region=SimpleNamespace(view2d=view))
    assert len(video_ui.boxes(context)) == 1
    video_ui.record_box(context, 0, (100, 50, 900, 450))
    edit = json.loads(s.box_edits_json)[0]
    assert edit['frame'] == 0 and edit['whole_track'] and edit['person'] == 0
    assert all(abs(a-b) < .001 for a, b in zip(edit['box'], [.1*width, .1*height, .9*width, .9*height]))
    print('SINGLE_IMAGE_BOX_VISIBLE_OUTSIDE_TIMELINE_AND_MANUAL_RECT_COORDINATES_OK', flush=True)

    rect = (100, 50, 900, 450)
    for point, mode in (((100, 450), 'nw'), ((900, 50), 'se'), ((500, 450), 'n'),
                        ((900, 250), 'e'), ((500, 250), 'move'), ((10, 10), 'draw')):
        assert video_ui.box_handle(rect, point) == mode
    assert video_ui.adjust_box(rect, (500, 250), (550, 270), 'move', (0, 0, 1000, 500)) == (150, 70, 950, 470)
    assert video_ui.adjust_box(rect, (500, 250), (2000, 2000), 'move', (0, 0, 1000, 500)) == (200, 100, 1000, 500)
    assert video_ui.adjust_box(rect, (100, 450), (50, 480), 'nw', (0, 0, 1000, 500)) == (50, 50, 900, 480)
    assert video_ui.adjust_box(rect, (100, 450), (950, 0), 'nw', (0, 0, 1000, 500)) == (896, 50, 900, 54)
    assert video_ui.adjust_box(rect, (20, 30), (980, 490), 'draw', (0, 0, 1000, 500)) == (20, 30, 980, 490)

    class Screen(dict):
        is_animation_playing = False
    gestures = SimpleNamespace(scene=scene, region=SimpleNamespace(type='WINDOW', view2d=view),
        area=SimpleNamespace(type='CLIP_EDITOR', tag_redraw=lambda: None), screen=Screen(flowhmr_popup=True),
        preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1)),
        window=SimpleNamespace(cursor_modal_set=lambda value: None, cursor_modal_restore=lambda: None),
        window_manager=SimpleNamespace(modal_handler_add=lambda op: None))
    s.manual_box_mode, s.box_person_index, s.busy = True, 0, False
    def gesture(start, end, cancel=False):
        op = SimpleNamespace()
        def event(kind, xy, value='PRESS'):
            return SimpleNamespace(type=kind, value=value, mouse_region_x=xy[0], mouse_region_y=xy[1])
        cls = video_ui.FLOWHMR_OT_VideoClick
        assert cls.invoke(op, gestures, event('LEFTMOUSE', start)) == {'RUNNING_MODAL'}
        assert cls.modal(op, gestures, event('MOUSEMOVE', end)) == {'RUNNING_MODAL'}
        return cls.modal(op, gestures, event('ESC' if cancel else 'LEFTMOUSE', end, 'RELEASE'))
    assert gesture((500, 250), (550, 270)) == {'FINISHED'}
    changed = json.loads(s.box_edits_json)
    assert len(changed) == 1 and changed[0]['whole_track'] and changed[0]['frame'] == 0
    expected = [.15*width, .06*height, .95*width, .86*height]
    assert all(abs(a-b) < .001 for a, b in zip(changed[0]['box'], expected))
    assert s.manual_box_mode, 'Continuous editing must retain the resize grips'
    assert gesture((150, 470), (100, 480)) == {'FINISHED'}
    expected = [.1*width, .04*height, .95*width, .86*height]
    assert all(abs(a-b) < .001 for a, b in zip(json.loads(s.box_edits_json)[0]['box'], expected))
    previous = s.box_edits_json
    assert gesture((500, 250), (600, 300), cancel=True) == {'CANCELLED'}
    assert s.box_edits_json == previous and scene.as_pointer() not in video_ui._DRAFTS
    assert bpy.ops.flowhmr.reset_person_boxes() == {'FINISHED'}
    assert s.box_edits_json == '[]' and not s.manual_box_mode
    print('NATIVE_BOX_MOVE_CORNER_EDGE_RESIZE_BOUNDS_CONTINUOUS_EDIT_AND_ESCAPE_OK', flush=True)

    sent = []
    client = SimpleNamespace(info=lambda: dict(runtime=dict(ready=True), model_files=dict(items=[]),
        postprocess_available=True, hamer_available=True, blender_bridge=dict(image_contact_options=True)),
        json=lambda route, body: sent.append((route, body)) or dict(job_id='new-current-config'))
    s.job_id, s.status, s.auto_start_backend = 'old-job', 'done', False
    s.manual_select, s.postprocess, s.hamer = False, True, True
    s.select_single_person = True
    s.steps, s.seed, s.auto_floor, s.floor_height = 31, 42, False, .25
    s.start_time, s.end_time, s.to_video_end = 2, 4, False
    with patch.object(operators, '_client', return_value=client), \
         patch.object(operators, '_launch', side_effect=lambda settings, work, done: done(settings, work())), \
         patch.object(operators, 'schedule_poll'):
        assert bpy.ops.flowhmr.submit(rerun_current=True) == {'FINISHED'}, s.message
    route, body = sent[0]
    assert route == '/api/jobs/old-job/rerun'
    assert body['steps'] == 31 and body['seed'] == 42 and body['hamer']
    assert body['postprocess'] and body['correction_floor_m'] == .25
    assert not body['manual_select'] and not body['reselect_person'] and not body['release_after_job']
    assert body['select_single_person'], 'The inactive child preference must survive submission'
    assert body['auto_frames']
    assert body['start_frame'] == body['end_frame'] == 0
    assert s.job_id == 'new-current-config'
    print('CURRENT_CONFIG_RERUN_ALL_OPTIONS_IMAGE_ONE_FRAME_MANUAL_SELECT_OPTIONAL_AND_KEEP_MODELS_OK', flush=True)
    # An older running backend must not silently discard image floor options.
    s.status = 'done'
    legacy = SimpleNamespace(info=lambda: dict(runtime=dict(ready=True), model_files=dict(items=[]),
        postprocess_available=True, hamer_available=True),
        json=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Legacy backend received a task')))
    with patch.object(operators, '_client', return_value=legacy), \
         patch.object(operators, '_launch', side_effect=lambda settings, work, done: done(settings, work())):
        try:
            result = bpy.ops.flowhmr.submit(rerun_current=True)
        except RuntimeError as error:
            assert '重启源码后端' in str(error)
        else:
            assert result == {'CANCELLED'}
    assert '重启源码后端' in s.message and s.job_id == 'new-current-config'
    print('LEGACY_IMAGE_CONTACT_REQUIRES_RESTART_BEFORE_TASK_SUBMISSION_OK', flush=True)
finally:
    addon.unregister()
print('BLENDER_PLAYER_SMOKE_OK', flush=True)
