"""Multi-person selection and optional single-person correction are independent."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
import torch

from flowhmr.pipeline import v2m_inference as inference
from flowhmr.utils.runtime_video_processing import TrackInfo, TrackingResult, VideoProcessor


@pytest.mark.parametrize('source_image', [False, True])
@pytest.mark.parametrize('people', [1, 2])
@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('include_single', [False, True])
def test_selection_switches_control_pause_and_selected_subject(tmp_path, source_image, people, manual, include_single):
    video = tmp_path / 'input.mp4'
    video.write_bytes(b'fixture')
    boxes = [[5., 5., 55., 95.], [60., 10., 90., 90.]]
    tracks = [TrackInfo(track_id=i + 1, frames=np.array([0]), bboxes=np.array([box]),
                        length=1, avg_area=(box[2] - box[0]) * (box[3] - box[1]))
              for i, box in enumerate(boxes[:people])]
    tracking = TrackingResult(tracks=tracks, total_frames=1, width=100, height=100, scanned_range=(0, 0))
    proc = SimpleNamespace(transcode=lambda *a, **kw: str(video), detect_and_track=Mock(return_value=tracking),
                           compute_camera_K=lambda *_: (torch.eye(3)[None], None))
    sam = Mock(spec=['extract_video_tokens'])
    sam.extract_video_tokens.return_value = torch.zeros(1, 3072)
    pipeline = Mock()
    pipeline.generate.return_value = dict(rot6d=torch.randn(1, 1, 52, 6), shapes=torch.zeros(1, 1, 16),
                                          trans=torch.zeros(1, 1, 3), foot_contacts=torch.ones(1, 1, 4))
    engine = inference.V2MInferenceEngine(pipeline, proc, sam, None, None, None, torch.device('cpu'))
    selector = Mock(side_effect=lambda result, candidates, default: len(candidates) - 1)
    with patch.object(VideoProcessor, 'get_video_meta', return_value=SimpleNamespace(width=100, height=100, total_frames=1)), \
            patch.object(inference, 'save_fbx'), \
            patch.object(inference, 'generation_origin', side_effect=lambda output, *a, **kw: deepcopy(output)):
        engine.process_video(str(video), str(tmp_path / 'result'), static_camera=True, source_image=source_image,
                             person_selector=selector if manual else None, select_single_person=include_single)
    pauses = manual and (people > 1 or include_single)
    assert selector.call_count == int(pauses)
    expected = people - 1 if pauses else 0
    with np.load(tmp_path / 'result/input/input_bbox.npz') as saved:
        np.testing.assert_allclose(saved['bbox'][0], boxes[expected])
    assert pipeline.generate.call_count == 1
