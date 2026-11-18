"""Explicit photo correction places soles without inventing gait or joint motion."""
from copy import deepcopy
from unittest.mock import patch

import numpy as np
import pytest
import torch

from flowhmr.utils.runtime_v2m_postprocess_simple import PostprocessPipeline


@pytest.mark.parametrize("floor", [None, .7, -.3])
def test_static_contact_preserves_rotations_and_places_lowest_sole(floor):
    identity = torch.tensor([1., 0., 0., 1., 0., 0.])
    output = dict(rot6d=identity.repeat(1, 1, 52, 1), trans=torch.tensor([[[1., 2., 3.]]]),
                  shapes=torch.zeros(1, 1, 16), foot_contacts=torch.ones(1, 1, 4),
                  pelvis_world=torch.ones(1, 1, 3))
    before = deepcopy(output)

    class Geometry:
        rest = np.zeros((52, 3), np.float32)
        def __init__(self, *_):
            pass
        def sample(self, positions, rotations):
            probes = np.repeat(positions[:, None], 4, axis=1)
            probes[..., 1] += np.array([-.8, -.9, -.5, -.6])
            return probes, None

    processor = PostprocessPipeline.__new__(PostprocessPipeline)
    processor._mesh_cpu = None
    processor.options = dict(auto_floor=floor is None, floor=floor, preset="strong")
    with patch('rgbmocap_correction.flow_correction.SoleGeometry', Geometry):
        result = processor.run(output, static_pose=True)
    expected_floor = 0 if floor is None else floor
    assert result['trans'].shape == (1, 1, 3)
    assert float(result['trans'][0, 0, 1]) - .9 == pytest.approx(expected_floor, abs=1e-6)
    torch.testing.assert_close(result['trans'][..., [0, 2]], before['trans'][..., [0, 2]])
    torch.testing.assert_close(result['rot6d'], before['rot6d'])
    torch.testing.assert_close(output['trans'], before['trans'])
    assert processor.last_report['static_pose']
    assert processor.last_report['contacts'] == 0
    assert 'pelvis_world' not in result
