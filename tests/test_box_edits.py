from copy import deepcopy
from types import SimpleNamespace
import threading
from unittest.mock import Mock, patch

import numpy as np
import pytest
import torch

from flowhmr.box_edits import validate_edits, box_at, apply_to_tracks
from flowhmr.utils.runtime_generation_origin import generation_origin
from test_bridge_http import server


def candidate():
    return dict(index=0, track_index=0, start=0, end=29, frames=[0, 29],
                boxes=[[10, 10, 60, 90], [20, 20, 70, 95]])


def test_video_manual_anchors_interpolate_without_freezing_the_whole_take():
    c = candidate()
    edits = validate_edits([dict(person=0, frame=5, box=[0, 0, 80, 100]),
                            dict(person=0, frame=15, box=[20, 0, 100, 100])], [c], [100, 100])
    assert box_at(c, 10, edits) == [10, 0, 90, 100]
    assert box_at(c, 0, edits) == c['boxes'][0]
    assert box_at(c, 29, edits) == c['boxes'][1]
    result = SimpleNamespace(tracks=[SimpleNamespace()])
    apply_to_tracks(result, [c], edits)
    assert result.tracks[0].bboxes.shape == (30, 4)
    np.testing.assert_allclose(result.tracks[0].bboxes[10], [10, 0, 90, 100])


def test_image_correction_reaches_all_static_frames_and_rejects_bad_boxes():
    c = {**candidate(), 'static_image': True}
    value = dict(person=0, frame=0, box=[0, 0, 100, 100], whole_track=True)
    edits = validate_edits([value], [c], [100, 100])
    assert box_at(c, 29, edits) == value['box']
    for invalid in ([0, 0, 0, 50], [0, 0, 101, 90], [0, float('nan'), 80, 90]):
        with pytest.raises(ValueError):
            validate_edits([{**value, 'box': invalid}], [c], [100, 100])
    with pytest.raises(ValueError):
        validate_edits([value], [candidate()], [100, 100])


def test_select_boxes_validates_before_resuming_and_preserves_other_people():
    event = threading.Event()
    c = {**candidate(), 'static_image': True}
    job = dict(id='test-box', status='waiting_user', candidates=[c], video_size=[100, 100])
    slot = dict(event=event, people=None)
    with patch.object(server, 'JOBS', {job['id']: job}), patch.object(server, 'SELECTIONS', {job['id']: slot}), \
            patch.object(server, '_persist_job_locked'), server.app.test_client() as client:
        edit = dict(person=0, frame=0, box=[0, 0, 110, 100], whole_track=True)
        response = client.post('/api/jobs/test-box/select-boxes', json={'people': [0], 'box_edits': [edit]})
        assert response.status_code == 400 and not event.is_set()
        edit['box'][2] = 100
        response = client.post('/api/jobs/test-box/select-boxes', json={'people': [0], 'box_edits': [edit]})
        assert response.status_code == 200 and event.is_set()
        assert slot['box_edits'][0]['box'] == [0., 0., 100., 100.]
        assert slot['people'] == [0]
        assert client.post('/api/jobs/test-box/select-boxes', json={'people': [0]}).status_code == 409


def test_airborne_export_keeps_pose_and_height_when_contact_correction_is_disabled():
    output = dict(rot6d=torch.randn(1, 30, 52, 6), shapes=torch.zeros(1, 1, 16),
                  trans=torch.tensor([[[0., 2., 0.], [0., 3., 0.]]]).repeat(1, 15, 1))
    before = deepcopy(output)
    def mesh(_):
        return {'vertices': torch.tensor([[[0., 1.5, 0.], [0., 2., 0.]]])}
    airborne = generation_origin(output, mesh, align_floor=False)
    grounded = generation_origin(output, mesh, align_floor=True)
    torch.testing.assert_close(airborne['trans'], before['trans'])
    torch.testing.assert_close(grounded['trans'][..., 1], before['trans'][..., 1] - 1.5)
    torch.testing.assert_close(airborne['rot6d'], before['rot6d'])
    torch.testing.assert_close(output['trans'], before['trans'])


@pytest.mark.parametrize('detected', [True, False])
@pytest.mark.parametrize('correction', [True, False])
def test_real_pipeline_checks_single_person_boxes_and_infers_one_image_pose(tmp_path, detected, correction):
    from flowhmr.pipeline import v2m_inference as inference
    from flowhmr.utils.runtime_video_processing import VideoProcessor, TrackInfo, TrackingResult
    video = tmp_path / 'input.mp4'
    video.write_bytes(b'fixture')
    track = TrackInfo(track_id=1, frames=np.array([0]), bboxes=np.array([[20., 10., 60., 70.]]),
                      length=1, avg_area=2400.)
    tracking = TrackingResult(tracks=[track] if detected else [], total_frames=30, width=100, height=100, scanned_range=(0, 0))
    proc = SimpleNamespace(transcode=lambda *a, **kw: str(video), detect_and_track=Mock(return_value=tracking),
                           compute_camera_K=lambda *_: (torch.eye(3).repeat(30, 1, 1), None))
    sam = Mock(spec=['extract_video_tokens'])
    sam.extract_video_tokens.return_value = torch.zeros(1, 3072)
    pipeline, contact = Mock(), Mock()
    output = dict(rot6d=torch.randn(1, 1, 52, 6), trans=torch.tensor([[[0., 2., 0.]]]),
                  shapes=torch.zeros(1, 1, 16), foot_contacts=torch.ones(1, 1, 4))
    pipeline.generate.return_value = output
    contact.run.return_value = deepcopy(output)
    engine = inference.V2MInferenceEngine(pipeline, proc, sam, None, None, contact if correction else None, torch.device('cpu'))
    chosen = []
    def select(result, candidates, default):
        assert len(candidates) == 1
        assert candidates[0]['manual_fallback'] is (not detected)
        edits = validate_edits([dict(person=0, frame=0, box=[5, 5, 95, 95])], candidates, [100, 100])
        apply_to_tracks(result, candidates, edits)
        chosen.append(default)
        return 0
    with patch.object(VideoProcessor, 'get_video_meta', return_value=SimpleNamespace(width=100, height=100, total_frames=30)), \
            patch.object(inference, 'save_fbx') as export, \
            patch.object(inference, 'generation_origin', side_effect=lambda output, *a, **kw: deepcopy(output)):
        engine.process_video(str(video), str(tmp_path / 'result'), source_image=True, static_camera=True,
                             person_selector=select, select_single_person=True, end_frame=29)
    assert chosen == [0]
    assert proc.detect_and_track.call_args.kwargs["start_frame"] == 0
    assert proc.detect_and_track.call_args.kwargs["end_frame"] == 0
    assert pipeline.generate.call_args.kwargs['length'] == 1
    assert pipeline.generate.call_args.kwargs['feature']['feature'].shape == (1, 1, 3072)
    assert sam.extract_video_tokens.call_args.kwargs['max_frames'] == 1
    if correction:
        contact.run.assert_called_once_with(output, cancel_check=None, static_pose=True)
    else:
        contact.run.assert_not_called()
    assert export.call_args.kwargs['length'] == 1
    torch.testing.assert_close(export.call_args.args[0]['rot6d'], output['rot6d'])
    torch.testing.assert_close(export.call_args.args[0]['trans'], output['trans'])
    with np.load(tmp_path / 'result/input/input_bbox.npz') as boxes:
        assert boxes['bbox'].shape == (1, 4)
        np.testing.assert_allclose(boxes['bbox'][0], [5, 5, 95, 95])
