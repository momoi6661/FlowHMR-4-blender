from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import io
import json

from werkzeug.serving import make_server


def load(name, relative):
    spec = spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load("bridge_http_web", "tools/web/server.py")
client_module = load("bridge_http_client", "blender_addon/client.py")


class BridgeHttpTest(unittest.TestCase):
    def test_image_upload_always_requests_one_pose(self):
        with TemporaryDirectory() as directory, \
                patch.object(server, "ARGS", SimpleNamespace(work_dir=directory)), \
                patch.object(server, "JOBS", {}), patch.object(server, "MEMBER_DIRS", {}), \
                patch.object(server.RUNTIME, "summary", return_value={"ready": True}), \
                patch.object(server, "enqueue") as enqueue, server.app.test_client() as client:
            response = client.post("/api/upload", data={
                "video": (io.BytesIO(b"cached-image-video"), "pose.webp.webm"),
                "source_kind": "image", "source_name": "pose.webp",
                "start_frame": "0", "end_frame": "29", "postprocess": "true",
            })
            self.assertEqual(response.status_code, 200)
            job = server.JOBS[response.json["job_id"]]
            self.assertEqual(job["params"]["end_frame"], 0)
            self.assertEqual(job["params"]["start_frame"], 0)
            self.assertTrue(job["params"]["postprocess"])
            self.assertTrue(job["params"]["manual_select"])
            self.assertFalse(job["params"]["select_single_person"])
            self.assertTrue(job["params"]["static_camera"])
            self.assertFalse(job["params"]["release_after_job"])
            self.assertEqual(job["source_kind"], "image")
            enqueue.assert_called_once()
            response = client.post("/api/upload", data={
                "video": (io.BytesIO(b"cached-image-video"), "airborne.png.webm"),
                "source_kind": "image", "postprocess": "false", "manual_select": "false", "select_single_person": "true",
            })
            job = server.JOBS[response.json["job_id"]]
            self.assertFalse(job["params"]["postprocess"])
            self.assertFalse(job["params"]["manual_select"])
            self.assertTrue(job["params"]["select_single_person"])
            job['status'] = 'done'
            response = client.post(f"/api/jobs/{job['id']}/rerun", json={
                "postprocess": True, "correction_floor_m": .4, "steps": 25,
                "manual_select": False, "start_frame": 60, "end_frame": 120,
                "auto_frames": False, "reselect_person": True,
            })
            self.assertEqual(response.status_code, 200)
            rerun = server.JOBS[response.json['job_id']]
            self.assertNotEqual(rerun['id'], job['id'])
            self.assertFalse(job['params']['postprocess'])
            self.assertTrue(rerun['params']['postprocess'])
            self.assertEqual(rerun['params']['correction_floor_m'], .4)
            self.assertEqual(rerun['params']['steps'], 25)
            self.assertFalse(rerun['params']['manual_select'])
            self.assertTrue(rerun['params']['select_single_person'])
            self.assertTrue(rerun['params']['auto_frames'])
            self.assertEqual((rerun['params']['start_frame'], rerun['params']['end_frame']), (0, 0))

    def test_history_original_cover_uses_selected_time_instead_of_person_crop(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'persons').mkdir()
            (root / 'persons/2.jpg').write_bytes(b'person-crop')
            (root / 'input.mp4').write_bytes(b'video-fixture')
            job = dict(id='cover', ext='.mp4', selected_person=2, params=dict(start_frame=60))

            def extract(video, destination, *, time_seconds):
                self.assertEqual(Path(video), root / 'input.mp4')
                self.assertEqual(time_seconds, 2)
                Path(destination).write_bytes(b'original-frame')
                return True

            with patch.object(server, 'get_job', return_value=job), \
                    patch.object(server, 'job_dir', return_value=str(root)), \
                    patch.object(server, 'ensure_thumbnail', side_effect=extract) as thumbnail, \
                    server.app.test_client() as client:
                response = client.get('/api/jobs/cover/thumbnail.jpg')
                self.assertEqual(response.data, b'person-crop')
                response.close()
                thumbnail.assert_not_called()
                for _ in range(2):
                    response = client.get('/api/jobs/cover/thumbnail.jpg?source=original')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, b'original-frame')
                    response.close()
                thumbnail.assert_called_once()

    def test_streamed_upload_preserves_video_and_interval_fields(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / "动作素材.mp4"
            payload = b"\x00\x01video\r\n" * 250000
            video.write_bytes(payload)
            httpd = make_server("127.0.0.1", 0, server.app)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            with patch.object(server, "ARGS", SimpleNamespace(work_dir=directory)), \
                    patch.object(server, "JOBS", {}), patch.object(server, "MEMBER_DIRS", {}), \
                    patch.object(server.RUNTIME, "summary", return_value={"ready": True}), \
                    patch.object(server, "enqueue") as enqueue:
                thread.start()
                try:
                    client = client_module.FlowHMRClient(port=httpd.server_port)
                    job_id = client.upload_video(video, start_frame=60, end_frame=149,
                                                 hamer=True, hamer_stride=2, postprocess=True,
                                                 manual_select=True, select_single_person=True, steps=15, smoothing_frames=.2)
                    job = client.job(job_id)
                    self.assertEqual(job['filename'], video.name)
                    self.assertEqual(job["params"]["start_frame"], 60)
                    self.assertEqual(job["params"]["end_frame"], 149)
                    self.assertFalse(job["params"]["release_after_job"])
                    self.assertIsNone(job["params"]["sam_chunk_size"])
                    self.assertTrue(job["params"]["manual_select"])
                    self.assertTrue(job["params"]["select_single_person"])
                    self.assertTrue(job["params"]["postprocess"])
                    self.assertTrue(job["params"]["hamer"])
                    self.assertEqual(job["params"]["hamer_stride"], 2)
                    self.assertEqual(job["params"]["steps"], 15)
                    self.assertEqual(Path(server.input_video(job_id)).read_bytes(), payload)
                    enqueue.assert_called_once()
                finally:
                    httpd.shutdown()
                    thread.join(timeout=5)
                    httpd.server_close()

    def test_streamed_image_container_preserves_original_name_without_relying_on_new_backend(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / 'hashed-cache.mp4'
            video.write_bytes(b'encoded-photo')
            submitted = {}

            def accept_upload(path, *, body, headers, method):
                from werkzeug.test import EnvironBuilder
                from werkzeug.wrappers import Request
                builder = EnvironBuilder(method=method, path=path, data=b''.join(body),
                                         content_type=headers['Content-Type'])
                try:
                    request = Request(builder.get_environ())
                    uploaded = request.files['video']
                    submitted.update(filename=uploaded.filename, content=uploaded.read(), form=dict(request.form))
                    request.close()
                finally:
                    builder.close()
                return b'{"job_id":"image-fixture"}'

            client = client_module.FlowHMRClient()
            with patch.object(client, '_request', side_effect=accept_upload):
                self.assertEqual(client.upload_video(video, source_kind='image', source_name='人物姿势.webp'), 'image-fixture')
            self.assertEqual(submitted['filename'], '人物姿势.webp.mp4')
            self.assertEqual(submitted['content'], b'encoded-photo')
            self.assertEqual(submitted['form']['source_name'], '人物姿势.webp')
            self.assertTrue(server.is_image_job(dict(filename=submitted['filename'])))

    def test_legacy_job_identity_comes_from_explicit_result_metadata_and_is_cached(self):
        client = client_module.FlowHMRClient()
        photo = dict(id='old-photo', filename='input.mp4', status='done')
        video = dict(id='real-video', filename='input.mp4', status='done', source_kind='video')

        def response(path, **_kwargs):
            if path == '/api/jobs/old-photo/meta':
                return json.dumps(dict(source_kind='image', source_name='原图.png')).encode()
            if path == '/api/jobs/old-photo':
                return json.dumps(photo).encode()
            if path.startswith('/api/jobs?'):
                return json.dumps(dict(jobs=[photo, video])).encode()
            raise AssertionError(path)

        with patch.object(client, '_request', side_effect=response) as request:
            self.assertEqual(client.job('old-photo')['source_kind'], 'image')
            self.assertEqual(client.job('old-photo')['source_name'], '原图.png')
            rows = client.json('/api/jobs?limit=20')['jobs']
            self.assertEqual([row['source_kind'] for row in rows], ['image', 'video'])
            self.assertEqual(sum(call.args[0].endswith('/meta') for call in request.call_args_list), 1)
        self.assertNotIn('source_kind', photo)
        self.assertFalse(server.is_image_job(dict(source_kind='video', filename='photo.png.mp4')))

    def test_missing_legacy_metadata_does_not_guess_images_from_duration_or_frame_count(self):
        client = client_module.FlowHMRClient()
        job = dict(id='single-frame-video', filename='input.mp4', status='done', num_frames=1, source_frames=30)
        with patch.object(client, '_request', side_effect=[json.dumps(job).encode(), RuntimeError('HTTP 404')]):
            self.assertEqual(client.job(job['id']), job)


class BridgeHistoryDeletionTest(unittest.TestCase):
    def test_deletion_removes_finished_group_files_and_skips_active_jobs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = {
                "group": dict(id="group", status="done", person_batch="group"),
                "member": dict(id="member", status="done", person_batch="group"),
                "active": dict(id="active", status="running"),
            }
            for key in ("group", "active"):
                (root / key).mkdir()
                (root / key / "motion.npz").write_bytes(b"fixture")
            member = root / "group" / "people" / "member"
            member.mkdir(parents=True)
            with patch.object(server, "ARGS", SimpleNamespace(work_dir=directory)), \
                    patch.object(server, "JOBS", jobs), \
                    patch.object(server, "MEMBER_DIRS", {"member": str(member)}), \
                    patch.object(server, "SELECTIONS", {}), \
                    patch.object(server.CHARACTER_LIBRARY, "forget_actors") as forget, server.app.test_client() as client:
                result = client.delete("/api/jobs", json={"ids": ["group", "active"]}).json
                self.assertEqual(set(result["deleted"]), {"group", "member"})
                self.assertEqual(result["skipped"][0]["id"], "active")
                self.assertFalse((root / "group").exists())
                self.assertTrue((root / "active" / "motion.npz").exists())
                self.assertEqual(set(jobs), {"active"})
                forget.assert_called_once()

    def test_deletion_rejects_job_paths_outside_results_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "results"
            work.mkdir()
            outside = root / "keep"
            outside.mkdir()
            (outside / "file.txt").write_text("keep")
            jobs = {"outside": dict(id="outside", status="done")}
            with patch.object(server, "ARGS", SimpleNamespace(work_dir=str(work))), \
                    patch.object(server, "JOBS", jobs), \
                    patch.object(server, "MEMBER_DIRS", {"outside": str(outside)}), \
                    patch.object(server.CHARACTER_LIBRARY, "forget_actors"), server.app.test_client() as client:
                result = client.delete("/api/jobs", json={"ids": ["outside"]}).json
                self.assertEqual(result["deleted"], [])
                self.assertEqual(result["skipped"][0]["id"], "outside")
                self.assertEqual((outside / "file.txt").read_text(), "keep")
                self.assertIn("outside", jobs)
