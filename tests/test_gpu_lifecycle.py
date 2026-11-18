from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

_spec = spec_from_file_location("gpu_lifecycle_web", Path(__file__).resolve().parents[1] / "tools/web/server.py")
server = module_from_spec(_spec)
_spec.loader.exec_module(server)


class GpuLifecycleTest(unittest.TestCase):
    def test_busy_release_does_not_touch_engine(self):
        for state in ("running", "queued", "cancel_requested", "waiting_user"):
            with self.subTest(state=state), patch.object(server, "JOBS", {"job": {"status": state}}), \
                    patch.object(server, "release_engine") as release, server.app.test_client() as client:
                self.assertEqual(client.post("/api/gpu/release").status_code, 409)
                release.assert_not_called()
        with server.ENGINE_LOCK, patch.object(server, "JOBS", {}), server.app.test_client() as client:
            self.assertEqual(client.post("/api/gpu/release").status_code, 409)

    def test_release_closes_hooks_drops_weights_and_clears_cuda(self):
        class Engine:
            pass

        cuda = SimpleNamespace(is_initialized=lambda: True, empty_cache=Mock(), ipc_collect=Mock(),
                               current_device=lambda: 0, mem_get_info=lambda _: (1024, 2048),
                               memory_allocated=lambda _: 0, memory_reserved=lambda _: 0)
        closed = []
        with patch.object(server, "JOBS", {}), patch.object(server, "ENGINE", None), \
                patch.object(server, "_POSTPROCESS", object()), \
                patch.dict(server.sys.modules, {"torch": SimpleNamespace(cuda=cuda)}), \
                server.app.test_client() as client:
            server.ENGINE = Engine()
            server.ENGINE.sam_extractor = SimpleNamespace(close=lambda: closed.append(True))
            reference = weakref.ref(server.ENGINE)
            response = client.post("/api/gpu/release")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["memory"]["free"], 1024)
            self.assertIsNone(reference())
            self.assertIsNone(server._POSTPROCESS)
            self.assertEqual(closed, [True])
            cuda.empty_cache.assert_called_once()
            cuda.ipc_collect.assert_called_once()

    def test_release_rejects_foreign_origin_and_remote_clients(self):
        with server.app.test_client() as client:
            self.assertEqual(client.post("/api/gpu/release", headers={"Origin": "https://example.org"}).status_code, 403)
            self.assertEqual(client.post("/api/gpu/release", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)

    def test_cleanup_runs_on_success_cancellation_and_failure(self):
        class EndLoop(Exception):
            pass

        for error in (None, server.JobCancelled(), RuntimeError("inference failed")):
            with self.subTest(error=error):
                job = dict(id="job", status="queued", params={"release_after_job": True}, stages=[])
                job_queue = Mock()
                job_queue.get.side_effect = ["job", EndLoop()]
                with patch.object(server, "JOB_QUEUE", job_queue), patch.object(server, "JOBS", {"job": job}), \
                        patch.object(server, "run_job", side_effect=error), \
                        patch.object(server, "_persist_job_locked"), \
                        patch.object(server, "release_engine") as release, \
                        patch.object(server.traceback, "print_exc"), self.assertRaises(EndLoop):
                    server.worker_loop()
                release.assert_called_once()
                job_queue.task_done.assert_called_once()
                self.assertFalse(server.ENGINE_LOCK.locked())
