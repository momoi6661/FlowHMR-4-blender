"""Torso-motion optimization: torso from one run, feet and shape from another."""

import unittest

import numpy as np

from flowhmr.model_files import smplh_reference_path
from flowhmr.utils.runtime_torso_optimization import (
    LEGS, TORSO_JOINTS, compose_torso_optimization,
)

FRAMES = 8
LEG_JOINTS = [joint for leg in LEGS for joint in leg]


def motion(poses, trans, betas=None):
    return {
        "poses": poses.astype(np.float32),
        "trans": trans.astype(np.float32),
        "betas": (betas if betas is not None else np.zeros((1, 16), np.float32)).astype(np.float32),
        "mocap_framerate": np.int64(30),
        "num_frames": np.int64(len(poses)),
        "gender": np.array("neutral"),
        "body_reference": np.array("neutral"),
        "foot_contacts": np.ones((len(poses), 4), np.float32),
    }


def skeleton_forward(poses, trans, betas):
    import torch
    from flowhmr.core.bodymodels.smpl_skeleton import SMPLSkeleton
    model = SMPLSkeleton(str(smplh_reference_path("neutral"))).cpu().float().eval()
    with torch.no_grad():
        out = model({"poses": torch.from_numpy(poses.astype(np.float32)),
                     "shapes": torch.from_numpy(betas.astype(np.float32)),
                     "trans": torch.from_numpy(trans.astype(np.float32))})
    return out["keypoints3d"].numpy()


class TorsoOptimizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(7)
        latest_poses = rng.normal(0, 0.12, size=(FRAMES, 52, 3)).astype(np.float32)
        latest_poses[:, 0] = rng.normal(0, 0.2, size=(FRAMES, 3))
        latest_trans = np.stack([np.linspace(0, .3, FRAMES), np.full(FRAMES, .95), np.zeros(FRAMES)], axis=1)
        cls.latest = motion(latest_poses, latest_trans)
        base_poses = latest_poses.copy()
        base_poses[:, TORSO_JOINTS] += rng.normal(0, 0.15, size=(FRAMES, len(TORSO_JOINTS), 3))
        cls.base = motion(base_poses, latest_trans)
        cls.composed, cls.metrics = compose_torso_optimization(
            cls.base, cls.latest, smplh_reference_path("neutral"))

    def test_torso_rotations_come_from_base(self):
        self.assertTrue(np.array_equal(self.composed["poses"][:, TORSO_JOINTS], self.base["poses"][:, TORSO_JOINTS]))

    def test_other_rotations_stay_from_standard_run(self):
        untouched = [j for j in range(52) if j not in TORSO_JOINTS + tuple(LEG_JOINTS)]
        self.assertTrue(np.array_equal(self.composed["poses"][:, untouched], self.latest["poses"][:, untouched]))

    def test_feet_return_to_standard_world_positions(self):
        target = skeleton_forward(self.latest["poses"], self.latest["trans"], self.latest["betas"])
        actual = skeleton_forward(self.composed["poses"], self.composed["trans"], self.composed["betas"])
        error = np.linalg.norm(actual[:, [7, 8, 10, 11]] - target[:, [7, 8, 10, 11]], axis=-1)
        self.assertLess(float(error.max()), 1e-3)
        self.assertLess(self.metrics["ankle_target_error_mm"]["max"], .1)

    def test_shape_and_root_come_from_standard_run(self):
        self.assertTrue(np.array_equal(self.composed["betas"], self.latest["betas"]))
        self.assertLess(float(np.abs(self.composed["trans"] - self.latest["trans"]).max()), .05)
        self.assertLess(self.metrics["pelvis_drop_mm"]["max"], 50)

    def test_identity_composition_keeps_motion(self):
        composed, metrics = compose_torso_optimization(
            self.latest, self.latest, smplh_reference_path("neutral"))
        error = np.abs(composed["poses"] - self.latest["poses"]).max()
        self.assertLess(float(error), 1e-4)
        self.assertLess(metrics["ankle_target_error_mm"]["max"], .1)


if __name__ == "__main__":
    unittest.main()
