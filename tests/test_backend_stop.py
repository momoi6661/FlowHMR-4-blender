"""Stopping must verify the service and must not report success on a live port."""
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("stop_fixture_addon")
package.__path__ = [str(ROOT / "blender_addon")]
sys.modules[package.__name__] = package
spec = spec_from_file_location(package.__name__ + ".backend", ROOT / "blender_addon/backend.py")
backend = module_from_spec(spec)
spec.loader.exec_module(backend)


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(backend, "_PROCESS", None)
    monkeypatch.setattr(backend, "_ENDPOINT", None)
    return SimpleNamespace(host="127.0.0.1", port=60550)


def test_unrelated_or_unresponsive_port_is_never_killed(monkeypatch, service):
    monkeypatch.setattr(backend, "_port_open", lambda s: True)
    kill = Mock()
    monkeypatch.setattr(backend, "_stop_pid", kill)
    monkeypatch.setattr(backend, "FlowHMRClient", lambda *a, **kw: SimpleNamespace(info=lambda: {"other": True}))
    with pytest.raises(RuntimeError, match="不是 FlowHMR"):
        backend.stop_backend(service)
    kill.assert_not_called()
    monkeypatch.setattr(backend, "FlowHMRClient", Mock(side_effect=OSError("offline")))
    with pytest.raises(RuntimeError, match="无法确认"):
        backend.stop_backend(service)
    kill.assert_not_called()


def test_existing_flowhmr_is_stopped_and_port_verified(monkeypatch, service):
    monkeypatch.setattr(backend, "_port_open", Mock(side_effect=[True, False]))
    monkeypatch.setattr(backend, "FlowHMRClient", lambda *a, **kw: SimpleNamespace(info=lambda: {"blender_bridge": {"version": 1, "motion": True}}))
    monkeypatch.setattr(backend, "_listening_pid", lambda port: 12345)
    kill = Mock(return_value=True)
    monkeypatch.setattr(backend, "_stop_pid", kill)
    assert backend.stop_backend(service)
    kill.assert_called_once_with(12345)


def test_localized_netstat_and_offline_service(monkeypatch, service):
    monkeypatch.setattr(backend.os, "name", "nt")
    monkeypatch.setattr(backend.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="  TCP 127.0.0.1:60550 0.0.0.0:0 侦听 12345\n"))
    assert backend._listening_pid(60550) == 12345
    monkeypatch.setattr(backend, "_port_open", lambda s: False)
    assert not backend.stop_backend(service)
    service.host = "192.0.2.1"
    with pytest.raises(RuntimeError, match="本机"):
        backend.stop_backend(service)
