"""Regression coverage for the Windows runtime and the character library API."""

from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from random import Random
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from flowhmr import runtime_environments as env, runtime_rocm as rocm, runtime_xpu as xpu
from flowhmr.device_support import DeviceError, backend_summary, plan_device, resolve_device

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("review_web", ROOT / "tools/web/server.py")
server = module_from_spec(spec)
spec.loader.exec_module(server)


class DeviceSelectionTest(unittest.TestCase):
    def test_portable_device_planning_never_probes_torch_at_startup(self):
        with TemporaryDirectory() as tmp:
            python = Path(tmp) / "runtime/python.exe"
            python.parent.mkdir()
            python.touch()
            runtime = SimpleNamespace(root=Path(tmp), active=None, default_device=Mock(return_value="cuda"), system=None)
            with patch.dict(sys.modules, {"torch": None}), patch("flowhmr.device_support.find_spec", side_effect=AssertionError):
                self.assertEqual(plan_device("auto", runtime).device, "cuda")
                for name, arg, backend in [
                    ("cu128", "cuda", "nvidia"), ("rocm714-gfx1032", "cuda", "rocm"),
                    (xpu.PROFILE, "xpu", "xpu"), ("cpu", "cpu", "cpu"),
                ]:
                    runtime.active = name
                    runtime.default_device.return_value = arg
                    plan = plan_device("auto", runtime)
                    self.assertEqual(backend_summary(plan, runtime), {"kind": backend, "device": arg, "runtime": name})
            for unsupported in ("mps", "dml", "cuda:-1", "cuda:0.5", "cpu:1"):
                with self.assertRaises(ValueError):
                    plan_device(unsupported, runtime)

    def test_explicit_rocm_ordinal_cannot_override_the_installed_architecture(self):
        class Device:
            def __init__(self, where, index=None):
                self.type, _, ordinal = where.partition(":")
                self.index = int(ordinal) if ordinal else index

        torch = SimpleNamespace(
            version=SimpleNamespace(hip="7.14", cuda=None),
            device=Device, cuda=Mock(),
        )
        torch.cuda.device_count.return_value = 2
        cards = [SimpleNamespace(gcnArchName=arch, total_memory=memory)
                 for arch, memory in (("gfx1036", 80 << 30), ("gfx1032", 8 << 30))]
        torch.cuda.get_device_properties.side_effect = cards.__getitem__
        runtime = SimpleNamespace(active="rocm714-gfx1032", cuda_variant=lambda: None)
        with patch.dict(sys.modules, {"torch": torch}), patch.object(rocm.sys, "platform", "win32"), \
                patch.object(rocm, "configure_windows_hip"):
            self.assertEqual(resolve_device(plan_device("rocm", runtime), runtime).index, 1)
            torch.cuda.set_device.assert_called_once_with(1)
            with self.assertRaises(DeviceError) as exc:
                resolve_device(plan_device("rocm:0", runtime), runtime)
            self.assertEqual(exc.exception.code, "runtime_rocm_gpu_required")
            with self.assertRaises(DeviceError) as exc:
                resolve_device(plan_device("nvidia:1", runtime), runtime)
            self.assertEqual(exc.exception.code, "runtime_device_mismatch")

    def test_rdna2_exact_matching_downloads_and_experimental_label(self):
        with (TemporaryDirectory() as tmp, patch.object(env, "SUPPORTED", True),
              patch.object(env, "_system_runtime", return_value=None), patch.object(env, "nvidia_adapters", return_value=()),
              patch.object(xpu, "windows_adapters", return_value=()), patch.object(rocm, "windows_adapters") as detect):
            runtime = env.RuntimeEnvironments(tmp)
            for code, arch in (("73AF", "gfx1030"), ("73DF", "gfx1031"), ("73FF", "gfx1032"), ("743F", "gfx1034")):
                with self.subTest(arch=arch):
                    gpu = rocm._amd_adapter("AMD Radeon", f"PCI\\VEN_1002&DEV_{code}&SUBSYS_00000000")
                    self.assertEqual(gpu["capability"], arch)
                    self.assertFalse(gpu["integrated"])
                    detect.return_value = (gpu,)
                    options = [item for item in runtime.details()["options"] if item["id"].startswith("rocm714-")]
                    self.assertEqual([item["id"] for item in options if item["compatible"]], [f"rocm714-{arch}"])
                    self.assertFalse(any(item["recommended"] for item in options))


class ThumbnailTest(unittest.TestCase):
    def test_retarget_thumbnail_and_role_lifetimes_are_independent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = server.CharacterFiles()
            library = server.CharacterLibrary(root / "library")
            role_ids = []
            for i in range(2):
                file = root / f"role{i}.vrm"
                file.write_bytes(b"model")
                role_ids.append(library.save(files, files.register(str(file))["ticket"], file.stem)["id"])
            source, other = role_ids
            preset_id = "1" * 64
            preset = {"id": preset_id, "version": 1, "name": "Preview", "kind": "vrm",
                      "links": [f"/joint{i}" for i in range(52)], "ik": {}}
            base = "/api/character-library"
            path = f"{base}/retarget-presets/{preset_id}"
            thumbnail = f"{path}/thumbnail"
            png = BytesIO()
            Image.new("RGBA", (224, 224), (13, 27, 44, 255)).save(png, format="PNG")
            jobs = {"source": {"status": "done"}, "other": {"status": "done"}}
            with (patch.object(server, "CHARACTER_LIBRARY", library), patch.object(server, "CHARACTER_FILES", files),
                  patch.object(server, "JOBS", jobs), server.app.test_client() as client):
                saved = client.put(f"{base}/actors/source", json={"id": source, "offsets": {}, "select": True,
                                                                 "retarget_preset": preset})
                self.assertEqual(saved.status_code, 200, saved.get_json())
                self.assertEqual(saved.json["retarget_preset"]["thumbnail_source"], source)
                self.assertFalse(saved.json["retarget_preset"]["has_thumbnail"])
                assigned = client.put(f"{base}/actors/other", json={"id": other, "select": True, "remember": True,
                                                                   "offsets": {"rootTranslation": [1, 0, 0]}})
                self.assertEqual(assigned.status_code, 200)
                self.assertEqual(client.get(thumbnail).status_code, 404)
                self.assertEqual(client.post(f"{base}/{source}/thumbnail", data=png.getvalue(),
                                             content_type="image/png").status_code, 200)
                self.assertTrue(client.get(base).json["retarget_presets"][preset_id]["has_thumbnail"])
                self.assertEqual(client.delete(f"{base}/{source}").status_code, 200)
            with (patch.object(server, "CHARACTER_LIBRARY", server.CharacterLibrary(library.root)),
                  server.app.test_client() as client):
                reply = client.get(thumbnail)
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
                with Image.open(BytesIO(reply.data)) as picture:
                    self.assertEqual(picture.size, (192, 192))
                    self.assertEqual(picture.getpixel((7, 9)), (13, 27, 44, 255))
                self.assertEqual(client.delete(path, headers={"Origin": "https://example.org"}).status_code, 403)
                self.assertEqual(client.delete(path).status_code, 200)
                self.assertEqual(client.get(thumbnail).status_code, 404)
                self.assertFalse((library.root / ".retarget-thumbnails" / f"{preset_id}.png").exists())
                state = client.get(base, query_string={"actor": "other"}).json
                self.assertEqual(state["retarget_presets"], {})
                self.assertEqual(state["models"][0]["id"], other)
                self.assertEqual(state["actors"]["other"]["poses"][other]["rootTranslation"], [1, 0, 0])

    def test_builtin_retarget_save_and_restore_are_isolated_by_profile(self):
        with TemporaryDirectory() as tmp:
            library = server.CharacterLibrary(tmp)
            base = "/api/character-library"
            prefix = f"{base}/retarget-presets/builtin"
            profiles = ("pmx", "vrm", "mixamo", "metahuman")
            links = [f"/joint{i}" for i in range(52)]
            binding = {"id": "a" * 64, "version": 1, "name": "Mixamo mapping", "kind": "fbx",
                       "links": links, "ik": {}}
            with patch.object(server, "CHARACTER_LIBRARY", library), server.app.test_client() as client:
                self.assertEqual(client.get(base).json["builtin_retarget_presets"], {})
                for i, profile in enumerate(profiles, 5):
                    payload = {"version": 1, "id": profile, "offsets": {"boneOffsets": {"rotations": {"15": [i, 0, 0]}}}}
                    if profile == "mixamo":
                        payload["binding"] = binding
                    saved = client.put(f"{prefix}/{profile}", json=payload)
                    self.assertEqual(saved.status_code, 200)
                    self.assertEqual(saved.json["id"], profile)
                    self.assertEqual(saved.json["offsets"]["boneOffsets"]["rotations"]["15"], [i, 0, 0])
                self.assertEqual(client.put(f"{prefix}/vrm", json={"version": 1, "offsets": {}, "binding": binding})
                                 .status_code, 400)
                self.assertEqual(client.delete(f"{prefix}/unknown").status_code, 400)
            with (patch.object(server, "CHARACTER_LIBRARY", server.CharacterLibrary(tmp)),
                  server.app.test_client() as client):
                state = client.get(base).json["builtin_retarget_presets"]
                self.assertEqual(set(state), set(profiles))
                self.assertEqual(state["mixamo"]["binding"]["links"], links)
                self.assertEqual(client.delete(f"{base}/retarget-presets/mixamo").status_code, 404)
                self.assertEqual(client.delete(f"{prefix}/mixamo",
                                               headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
                self.assertEqual(client.delete(f"{prefix}/mixamo").status_code, 200)
                restored = client.get(base).json["builtin_retarget_presets"]
                self.assertEqual(restored, {key: state[key] for key in profiles if key != "mixamo"})

    def test_group_actor_calibration_is_not_rewritten_by_preset_management(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = server.CharacterFiles()
            library = server.CharacterLibrary(root / "library")
            model = root / "shared.fbx"
            model.write_bytes(b"rig used by both people")
            role = library.save(files, files.register(str(model))["ticket"], model.stem)["id"]
            first, second = "actor-1", "actor-2"
            jobs = {first: {"status": "done", "selected_person": 0},
                    second: {"status": "done", "selected_person": 1}}
            base = "/api/character-library"
            presets = f"{base}/retarget-presets"
            builtin = f"{presets}/builtin/mixamo"
            user_id = "2" * 64
            mapping = {"version": 1, "map": list(range(52)), "root": 0}
            reusable = {"id": user_id, "version": 1, "name": "Group rig", "kind": "fbx",
                        "links": [f"/joint{i}" for i in range(52)], "root": "/joint0", "ik": {}}
            query = [("actor", first), ("actor", second)]
            with (patch.object(server, "CHARACTER_LIBRARY", library), patch.object(server, "JOBS", jobs),
                  server.app.test_client() as client):
                for number, actor in enumerate((first, second), 1):
                    payload = {"id": role, "person": number, "select": True, "remember": True,
                               "offsets": {"rootTranslation": [number / 2, 0, 0], "retarget": mapping}}
                    if number == 1:
                        payload["retarget_preset"] = reusable
                    self.assertEqual(client.put(f"{base}/actors/{actor}", json=payload).status_code, 200)
                override = {"id": "mixamo", "version": 1, "offsets": {"rotations": {"15": [15, 0, 0]}},
                            "binding": {**reusable, "id": "3" * 64}}
                self.assertEqual(client.put(builtin, json=override, headers={"Origin": "https://example.org"}).status_code, 403)
                self.assertEqual(client.put(builtin, json=override).status_code, 200)
                original = client.get(base, query_string=query).json
                for number, actor in enumerate((first, second), 1):
                    pose = original["actors"][actor]["poses"][role]
                    self.assertEqual(pose["retarget"]["map"], list(range(52)))
                    self.assertEqual(pose["rootTranslation"], [number / 2, 0, 0])
                for route in (f"{presets}/{user_id}", builtin):
                    self.assertEqual(client.delete(route, headers={"Origin": "https://example.org"}).status_code, 403)
                self.assertEqual(client.get(base, query_string=query).json, original)
                self.assertEqual(client.delete(f"{presets}/{user_id}").status_code, 200)
                self.assertEqual(client.delete(builtin).status_code, 200)
            restarted = server.CharacterLibrary(library.root).snapshot([first, second])
            self.assertEqual(restarted["retarget_presets"], {})
            self.assertEqual(restarted["builtin_retarget_presets"], {})
            for key in ("actors", "models", "defaults", "presets"):
                self.assertEqual(restarted[key], original[key])

    def test_png_larger_than_role_json_cap_survives_reload_and_keeps_vrm(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "character.vrm"
            file.write_bytes(b"model")
            files = server.CharacterFiles()
            ticket = files.register(str(file))["ticket"]
            library = server.CharacterLibrary(root / "library")
            path = "/api/character-library"
            png = BytesIO()
            Image.frombytes("RGBA", (108, 108), Random(41).randbytes(108 * 108 * 4)).save(png, format="PNG")
            self.assertGreater(png.tell(), 45_000)
            with patch.object(server, "CHARACTER_FILES", files), patch.object(server, "CHARACTER_LIBRARY", library), \
                    server.app.test_client() as client:
                model = client.post(path, json={"ticket": ticket, "name": file.stem}).get_json()
                self.assertEqual(model["kind"], "vrm")
                self.assertFalse(model["has_thumbnail"])
                thumbnail = f"{path}/{model['id']}/thumbnail"
                self.assertEqual(client.get(thumbnail).status_code, 404)
                self.assertEqual(client.post(path, json={"ticket": ticket, "name": "x" * 20000}).status_code, 413)
                self.assertEqual(client.post(thumbnail, data=png.getvalue(), content_type="image/png").status_code, 200)
                self.assertEqual(client.post(thumbnail, data=png.getvalue(), content_type="image/jpeg").status_code, 415)
                self.assertEqual(client.post(thumbnail, data=b"x" * (512 * 1024 + 1),
                                             content_type="image/png").status_code, 413)
                self.assertEqual(client.post(thumbnail, content_type="image/png", environ_overrides={
                    "CONTENT_LENGTH": "", "wsgi.input_terminated": True,
                    "wsgi.input": BytesIO(b"x" * (512 * 1024 + 1)),
                }).status_code, 413)
                self.assertEqual(client.post(thumbnail, data=png.getvalue()[:500], content_type="image/png").status_code, 400)
                wide = BytesIO()
                Image.new("RGB", (513, 5)).save(wide, format="PNG")
                self.assertEqual(client.post(thumbnail, data=wide.getvalue(), content_type="image/png").status_code, 400)
                self.assertEqual(client.post(thumbnail, data=png.getvalue(), content_type="image/png",
                                             headers={"Origin": "https://example.org"}).status_code, 403)
                self.assertEqual(client.get(thumbnail, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
                unknown = f"{path}/{'0' * 64}/thumbnail"
                self.assertEqual(client.post(unknown, data=png.getvalue(), content_type="image/png").status_code, 404)
            with patch.object(server, "CHARACTER_LIBRARY", server.CharacterLibrary(library.root)), \
                    server.app.test_client() as client:
                listed = client.get(path).get_json()["models"][0]
                self.assertEqual(listed["id"], model["id"])
                self.assertTrue(listed["has_thumbnail"])
                reply = client.get(thumbnail)
                self.assertEqual(reply.mimetype, "image/png")
                self.assertEqual(reply.headers["Cache-Control"], "no-store")
                self.assertEqual(Image.open(BytesIO(reply.data)).tobytes(), Image.open(png).tobytes())
                self.assertEqual(client.delete(f"{path}/{model['id']}").status_code, 200)
                self.assertEqual(client.get(thumbnail).status_code, 404)
                self.assertTrue(file.is_file())
