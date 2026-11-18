"""Backend interchange validation without inference or an FBX SDK."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flowhmr.utils.runtime_blender import blender_motion


def model():
    return dict(v_template=np.zeros((60, 3)), shapedirs=np.zeros((60, 3, 16)),
                J_regressor=np.eye(52, 60), weights=np.ones((60, 52)) / 52,
                f=np.array([[0, 1, 2]]), kintree_table=np.array([[-1] + list(range(51)), list(range(52))]))


def test_single_motion_and_mesh_optional():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "motion.npz"
        poses, trans = np.zeros((4, 52, 3)), np.arange(12).reshape(4, 3)
        np.savez(path, poses=poses, trans=trans, betas=np.ones((1, 10)), mocap_framerate=30)
        with patch("flowhmr.utils.runtime_blender.load_smplh_data", return_value=model()):
            result = blender_motion(path, "reference", job_id="test", source_start=120, include_mesh=False)
        with np.load(result, allow_pickle=False) as data:
            assert "weights" not in data
            np.testing.assert_equal(data["poses_0"], poses)
            np.testing.assert_equal(data["trans_0"], trans)
            info = json.loads(data["manifest"].item())
            assert info["actors"][0]["source_start"] == 120 and info["fps"] == 30


def test_scene_keeps_actor_offsets_and_aligned_positions():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "motion.npz"
        payload = dict(mocap_framerate=30)
        for i in (1, 3):
            payload.update({f"poses_{i}": np.zeros((4, 52, 3)), f"trans_{i}": np.full((4, 3), i),
                            f"betas_{i}": np.zeros(16)})
        np.savez(path, **payload)
        with patch("flowhmr.utils.runtime_blender.load_smplh_data", return_value=model()):
            result = blender_motion(path, "reference", people=[dict(person=1, id="a", start=60), dict(person=3, id="b", start=75)])
        with np.load(result, allow_pickle=False) as data:
            info = json.loads(data["manifest"].item())
            assert [x["start"] for x in info["actors"]] == [0, 15]
            assert info["actors"][1]["job_id"] == "b"
            assert data["weights"].shape == (60, 52)
            np.testing.assert_equal(data["trans_1"], payload["trans_3"])


def test_actor_length_must_match_declared_timeline():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "motion.npz"
        np.savez(path, poses_1=np.zeros((4, 52, 3)), trans_1=np.zeros((4, 3)), betas_1=np.zeros(16))
        with patch("flowhmr.utils.runtime_blender.load_smplh_data", return_value=model()):
            with pytest.raises(ValueError, match="timeline"):
                blender_motion(path, "reference", people=[dict(person=1, start=10, end=14)])


@pytest.mark.parametrize("multiple_people", [False, True])
def test_legacy_image_exports_one_original_pose_per_person(tmp_path, multiple_people):
    path = tmp_path / "legacy-image.npz"
    poses = np.arange(4 * 52 * 3, dtype=np.float32).reshape(4, 52, 3) / 100
    trans = np.arange(12, dtype=np.float32).reshape(4, 3)
    payload = dict(mocap_framerate=30)
    people = [dict(person=1, start=10, end=13), dict(person=3, start=15, end=18)] if multiple_people else None
    for suffix in (["_1", "_3"] if multiple_people else [""]):
        payload.update({f"poses{suffix}": poses, f"trans{suffix}": trans, f"betas{suffix}": np.zeros(16)})
    np.savez(path, **payload)
    original = path.read_bytes()
    with patch("flowhmr.utils.runtime_blender.load_smplh_data", return_value=model()):
        result = blender_motion(path, "reference", people=people, source_image=True)
    with np.load(result, allow_pickle=False) as data:
        manifest = json.loads(data["manifest"].item())
        assert manifest["source_kind"] == "image"
        for actor in manifest["actors"]:
            assert actor["start"] == 0 and actor["frames"] == 1
            np.testing.assert_array_equal(data[f"poses_{actor['index']}"], poses[:1])
            np.testing.assert_array_equal(data[f"trans_{actor['index']}"], trans[:1])
    assert path.read_bytes() == original


@pytest.mark.parametrize("field", ["poses", "trans", "betas", "mocap_framerate"])
def test_invalid_motion_rejected(field):
    with TemporaryDirectory() as directory:
        path = Path(directory) / "motion.npz"
        payload = dict(poses=np.zeros((4, 52, 3)), trans=np.zeros((4, 3)), betas=np.zeros(16), mocap_framerate=30.)
        payload[field] = np.full_like(payload[field], np.nan)
        np.savez(path, **payload)
        with patch("flowhmr.utils.runtime_blender.load_smplh_data", return_value=model()), pytest.raises(ValueError):
            blender_motion(path, "reference")


if __name__ == "__main__":
    from flowhmr.core.bodymodels.body_variants import model_path
    root = Path(__file__).resolve().parents[1] / ".cache/blender-test"
    root.mkdir(parents=True, exist_ok=True)
    poses = np.zeros((30, 52, 3), dtype=np.float32)
    poses[:, 0, 1] = np.linspace(0, 0.5, 30)
    poses[:, 18, 2] = np.linspace(0, 0.9, 30)
    poses[:, 23, 1] = np.linspace(0, 1.1, 30)
    poses[:, 4, 0] = np.linspace(0, -0.5, 30)
    trans = np.zeros((30, 3), dtype=np.float32)
    trans[:, 0] = np.linspace(0, 0.8, 30)
    np.savez(root / "source.npz", poses=poses, trans=trans, betas=np.zeros(16), mocap_framerate=30)
    stream = blender_motion(root / "source.npz", model_path("neutral"), job_id="blender-smoke")
    (root / "motion.npz").write_bytes(stream.read())
    print("BLENDER_FIXTURE_READY", root)
