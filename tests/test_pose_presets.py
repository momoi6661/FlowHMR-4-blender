"""Local pose libraries and immutable edits, without inference or CUDA."""
from contextlib import ExitStack
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from flowhmr.pose_presets import PARTS, PosePresets, apply_preset, capture_preset, checked_preset, read_motion

spec = spec_from_file_location("pose_presets_web", Path(__file__).resolve().parents[1] / "tools/web/server.py")
server = module_from_spec(spec)
spec.loader.exec_module(server)


def motion(frames=12):
    return dict(poses=np.zeros((frames, 52, 3), np.float32), trans=np.zeros((frames, 3), np.float32),
                betas=np.zeros(16, np.float32), foot_contacts=np.ones((frames, 4), np.float32))


def preset(part="left_hand", angle=.7):
    return dict(version=1, name="握门把手", part=part,
                rotations={str(i): [float(angle), 0, 0] for i in PARTS[part]})


class PoseMathTest(unittest.TestCase):
    def test_edit_only_selected_joints_and_inclusive_frames(self):
        original = motion()
        before = {k: v.copy() for k, v in original.items()}
        for part in PARTS:
            result = apply_preset(original, preset(part), 3, 6, blend_frames=0)
            expected = original["poses"].copy()
            for joint in PARTS[part]:
                expected[3:7, joint, 0] = .7
            np.testing.assert_allclose(result["poses"], expected)
            for k, v in original.items():
                np.testing.assert_array_equal(v, before[k])
            np.testing.assert_array_equal(result["trans"], original["trans"])
            self.assertEqual("foot_contacts" in result, part in ("left_hand", "right_hand", "hands"))

    def test_shortest_arc_interpolation_and_boundary_fades(self):
        data = motion()
        data["poses"][:, 20, 0] = np.deg2rad(179)
        target = preset(angle=np.deg2rad(-179))
        result = apply_preset(data, target, 0, 11, strength=.5)
        matrices = Rotation.from_rotvec(result["poses"][:, 20]).as_matrix()
        np.testing.assert_allclose(matrices[:, 1, 1], -1, atol=1e-6)
        faded = apply_preset(motion(), preset(angle=1), 2, 10, blend_frames=2)
        np.testing.assert_allclose(faded["poses"][2:11, 20, 0], [1/3, 2/3, 1, 1, 1, 1, 1, 2/3, 1/3])
        single = apply_preset(motion(), preset(), 4, 4)
        self.assertAlmostEqual(float(single["poses"][4, 20, 0]), .7)
        integer_strength = apply_preset(motion(), preset("feet"), 2, 10, strength=1, blend_frames=3)
        decimal_strength = apply_preset(motion(), preset("feet"), 2, 10, strength=1.0, blend_frames=3)
        np.testing.assert_array_equal(integer_strength["poses"], decimal_strength["poses"])

    def test_reject_invalid_ranges_and_imported_joints(self):
        for first, last, strength, blend in [(4, 2, 1, 0), (-1, 2, 1, 0), (0, 12, 1, 0),
                                             (True, 2, 1, 0), (0, 2, float("nan"), 0),
                                             (0, 2, 0, 0), (0, 2, 1, -1), (0, 2, 1, 121)]:
            with self.assertRaises(ValueError):
                apply_preset(motion(), preset(), first, last, strength, blend)
        bad = preset(); bad["rotations"]["0"] = [0, 0, 0]
        with self.assertRaises(ValueError):
            checked_preset(bad)
        bad = preset(); bad["rotations"]["20"] = [float("nan"), 0, 0]
        with self.assertRaises(ValueError):
            checked_preset(bad)

    def test_capture_flattened_motion_and_library_survives_restart(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data = motion(); data["poses"][4, 37, 1] = .25
            data["poses"] = data["poses"].reshape(12, 156)
            np.savez(root / "motion.npz", **data)
            captured = capture_preset(root / "motion.npz", "右手", "right_hand", 4)
            self.assertEqual(captured["rotations"]["37"], [0, .25, 0])
            library = PosePresets(root / "library")
            saved = library.save(captured)
            fresh = PosePresets(root / "library")
            self.assertEqual(fresh.get(saved["id"]), saved)
            (root / "library" / "bad.json").write_text("broken")
            self.assertEqual(len(fresh.list()), 1)
            for invalid in ("../motion", "..", "not-a-uuid"):
                with self.assertRaises(ValueError):
                    fresh.get(invalid)
            fresh.delete(saved["id"])
            self.assertEqual(fresh.list(), [])


class PoseRoutesTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack(); self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        self.directory = self.root / "jobs" / "source"
        (self.directory / "mesh").mkdir(parents=True)
        (self.directory / "input").mkdir()
        np.savez(self.directory / "motion.npz", **motion())
        (self.directory / "input.mp4").write_bytes(b"original video")
        (self.directory / "input" / "input_30fps.mp4").write_bytes(b"canonical video")
        (self.directory / "mesh" / "meta.json").write_text(json.dumps(dict(num_frames=12, fps=30,
            track_start=30, track_end=41, body_reference="neutral")))
        self.source = dict(id="source", status="done", filename="door.mp4", ext=".mp4", params=server.default_params())
        stack.enter_context(patch.object(server, "ARGS", SimpleNamespace(work_dir=str(self.root / "jobs"))))
        stack.enter_context(patch.object(server, "JOBS", {"source": self.source}))
        stack.enter_context(patch.object(server, "MEMBER_DIRS", {}))
        self.library = PosePresets(self.root / "presets")
        stack.enter_context(patch.object(server, "POSE_PRESETS", self.library))
        self.enqueue = stack.enter_context(patch.object(server, "enqueue"))
        self.client = server.app.test_client()

    def test_capture_import_export_and_delete_contract(self):
        response = self.client.post("/api/pose-presets", json=dict(job_id="source", name="左手", part="left_hand", frame=2))
        self.assertEqual(response.status_code, 201)
        entry = response.json
        response = self.client.get("/api/pose-presets/" + entry["id"])
        self.assertEqual(response.json["rotations"], entry["rotations"])
        self.assertEqual(len(self.client.get("/api/pose-presets").json["items"]), 1)
        self.assertEqual(self.client.get("/api/pose-presets").json["directory"], str(self.library.root.resolve()))
        imported = self.client.post("/api/pose-presets", json={"preset": entry})
        self.assertEqual(imported.status_code, 201)
        self.assertNotEqual(imported.json["id"], entry["id"])
        self.assertEqual(self.client.delete("/api/pose-presets/" + entry["id"]).status_code, 200)
        self.assertEqual(self.client.get("/api/pose-presets/" + entry["id"]).status_code, 404)

    def test_queued_edit_is_independent_and_does_not_load_engine(self):
        saved = self.library.save(preset())
        before = (self.directory / "motion.npz").read_bytes()
        response = self.client.post("/api/jobs/source/pose-presets", json=dict(
            preset_id=saved["id"], start=3, end=6, blend_frames=0))
        self.assertEqual(response.status_code, 202, response.json)
        job_id = response.json["job_id"]
        root = Path(server.job_dir(job_id))
        self.assertEqual((self.directory / "motion.npz").read_bytes(), before)
        edited = read_motion(root / "motion.npz")
        self.assertAlmostEqual(float(edited["poses"][3, 20, 0]), .7)
        self.assertEqual((root / "input.mp4").read_bytes(), b"original video")
        self.assertEqual(server.JOBS[job_id]["status"], "queued")
        self.enqueue.assert_called_once()
        with patch.object(server, "run_pose_edit") as edit, patch.object(server, "get_engine") as engine:
            server.run_job(job_id)
            edit.assert_called_once_with(job_id)
            engine.assert_not_called()

    def test_invalid_input_publishes_no_jobs_and_remote_requests_blocked(self):
        saved = self.library.save(preset())
        for start, end in [(-1, 3), (4, 2), (0, 100), (True, 2)]:
            result = self.client.post("/api/jobs/source/pose-presets", json=dict(
                preset_id=saved["id"], start=start, end=end))
            self.assertEqual(result.status_code, 400)
        self.assertEqual(set(server.JOBS), {"source"})
        self.assertEqual(self.client.post("/api/pose-presets", json={"preset":preset()},
            headers={"Origin":"https://example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/api/pose-presets", environ_base={"REMOTE_ADDR":"192.0.2.3"}).status_code, 403)
