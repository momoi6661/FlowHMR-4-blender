"""Run with Blender --background --factory-startup --python-exit-code 1 --python ..."""

from pathlib import Path
import socket
import sys
import time
import threading

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import blender_addon as addon


def wait_request(settings):
    deadline = time.monotonic() + 45
    while settings.busy and time.monotonic() < deadline:
        addon.operators._pump()
        time.sleep(0.1)
    assert not settings.busy, "Async request timed out"
    assert not settings.last_error, settings.message


addon.register()
try:
    settings = bpy.context.scene.flowhmr
    assert addon.FLOWHMR_Settings.bl_rna.properties.get("start_time") is not None
    assert not settings.auto_release_vram and settings.to_video_end
    settings.backend_root = str(ROOT)
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        settings.port = port_probe.getsockname()[1]
    print("BLENDER_REGISTER_OK", bpy.app.version_string)
    bpy.ops.flowhmr.start_backend()
    wait_request(settings)
    assert settings.connected and settings.runtime_ready, settings.message
    print("BACKEND_CONNECTION_OK", settings.models_ready)
    bpy.ops.flowhmr.select_video(filepath=str(ROOT / "assets/example/example.mp4"))
    wait_request(settings)
    assert settings.video_fps > 0 and settings.video_duration > 0
    settings.to_video_end = False
    settings.start_time, settings.end_time = 0, 1
    assert addon.frame_range(settings) == (0, 29)
    addon.operators._show_job(settings, dict(status="running", progress=55, message="in progress"))
    assert abs(settings.progress - 0.55) < 1e-6
    addon.operators._show_job(settings, dict(status="cancel_requested", progress=55))
    assert settings.status in addon.operators.ACTIVE
    print("VIDEO_RANGE_AND_PROGRESS_OK", settings.video_duration, settings.video_fps)
    settings.status = "cancelled"
    bpy.ops.flowhmr.release_vram()
    wait_request(settings)
    assert settings.vram_released
    bpy.ops.flowhmr.stop_backend()
    wait_request(settings)
    assert not settings.connected
    print("GPU_RELEASE_AND_OWNED_PROCESS_STOP_OK")
    # An existing local backend must stop even while an earlier poll is busy.
    bpy.ops.flowhmr.start_backend()
    wait_request(settings)
    external = addon.backend._PROCESS
    addon.backend._PROCESS = addon.backend._ENDPOINT = None
    other_scene = bpy.data.scenes.new('FlowHMR stop observer')
    other = other_scene.flowhmr
    other.host, other.port = settings.host, settings.port
    settings.status = other.status = 'running'
    settings.connected = other.connected = True
    finished = threading.Event()
    addon.operators._launch(settings, lambda: (finished.wait(15), {'status': 'running'})[1],
                            lambda state, job: addon.operators._show_job(state, job))
    try:
        bpy.ops.flowhmr.stop_backend()
        wait_request(settings)
        external.wait(timeout=10)
        assert not addon.backend._port_open(settings)
        assert settings.status == other.status == 'backend_stopped'
        assert not settings.connected and not other.connected
        finished.set()
        time.sleep(.1)
        addon.operators._pump()
        assert settings.status == 'backend_stopped' and not settings.busy
        print('EXTERNAL_BACKEND_BUSY_REQUEST_STOP_AND_STALE_POLL_REJECTION_OK')
    finally:
        finished.set()
        bpy.data.scenes.remove(other_scene)
finally:
    addon.unregister()
assert not hasattr(bpy.types.Scene, "flowhmr")
assert not bpy.app.timers.is_registered(addon.operators._pump)
assert not bpy.app.timers.is_registered(addon.operators._poll_timer)
print("BLENDER_UNREGISTER_OK")
