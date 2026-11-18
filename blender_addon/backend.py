"""Start and stop the verified local FlowHMR backend and its workers."""

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from .client import FlowHMRClient

_PROCESS = None
_ENDPOINT = None


def resolve_python(settings):
    configured = str(settings.python_executable or "").strip()
    if configured:
        if not Path(configured).is_file():
            raise FileNotFoundError("指定的后端 Python 不存在")
        return configured
    root = Path(settings.backend_root).expanduser()
    for candidate in (root / ".venv/Scripts/python.exe", root / ".venv/bin/python"):
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("未找到源码环境，请运行 tools/windows/setup-source.ps1")


def base_url(settings):
    return f"http://{settings.host}:{int(settings.port)}"


def start_backend(settings):
    global _PROCESS, _ENDPOINT
    endpoint = base_url(settings)
    if _PROCESS is not None and _PROCESS.poll() is None:
        if endpoint != _ENDPOINT:
            raise RuntimeError("请先停止插件已启动的后端，再更改连接地址")
        return
    try:
        info = FlowHMRClient(settings.host, settings.port, timeout=1).info()
    except OSError:
        info = None
    if info is not None:
        bridge = info.get("blender_bridge") or {}
        if bridge.get("version") != 1 or not bridge.get("motion"):
            raise RuntimeError("该端口上的服务不支持 FlowHMR Blender 桥接 API")
        return
    if settings.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("自动启动仅支持本机后端")
    if not str(settings.backend_root or "").strip():
        raise RuntimeError("插件未配置后端源码目录；请在偏好设置中选择 FlowHMR 后端，或先启动已有后端")
    root = Path(settings.backend_root).expanduser().resolve()
    if not (root / "app.py").is_file():
        raise FileNotFoundError("后端目录中没有 app.py，请在插件偏好设置中选择源码目录")
    python = resolve_python(settings)
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    for key, value in {"HF_HOME": str(root / ".cache/huggingface"),
                       "TORCH_HOME": str(root / ".cache/torch"),
                       "FLOWHMR_SAM_COMPILE": "0", "FLOWHMR_ATTENTION_BACKEND": "query_batched",
                       "FLOWHMR_ATTENTION_QUERY_CHUNK_SIZE": "256"}.items():
        env.setdefault(key, value)
    with (log_dir / "blender-bridge.log").open("ab") as log:
        _PROCESS = subprocess.Popen(
            [python, "-u", "app.py", "--host", settings.host, "--port", str(settings.port),
             "--device", settings.device], cwd=str(root), stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env,
        )
    _ENDPOINT = endpoint


def wait_ready(settings, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _PROCESS is not None and _PROCESS.poll() is not None:
            raise RuntimeError("后端启动退出，请查看 logs/blender-bridge.log")
        try:
            return FlowHMRClient(settings.host, settings.port, timeout=1).info()
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("后端启动超时，请查看 logs/blender-bridge.log")


def _listening_pid(port):
    """Return the Windows PID listening on a local TCP port.

    The add-on may connect to a backend started by the desktop app, a terminal,
    or an earlier add-on session.  We only use this after validating the
    endpoint as a FlowHMR bridge, so the stop action can manage that backend
    without guessing from a project path.
    """
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                check=True, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    suffix = f":{int(port)}"
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0].upper() == "TCP" \
                and fields[1].endswith(suffix) and (fields[3].upper() == "LISTENING" or fields[2] in {"0.0.0.0:0", "[::]:0"}):
            try:
                return int(fields[4])
            except ValueError:
                continue
    return None


def _stop_pid(pid):
    if not pid or pid == os.getpid():
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)
        return True
    os.kill(int(pid), 15)
    return True


def _port_open(settings):
    try:
        with socket.create_connection((settings.host, int(settings.port)), timeout=0.3):
            return True
    except OSError:
        return False


def stop_backend(settings=None):
    global _PROCESS, _ENDPOINT
    owned = _PROCESS is not None and _PROCESS.poll() is None
    if settings is not None and str(settings.host) not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("退出后端仅支持本机地址，请在远程机器上停止服务")
    if owned and (settings is None or _ENDPOINT == base_url(settings)):
        _stop_pid(_PROCESS.pid)
        _PROCESS.wait(timeout=5)
    elif settings is not None:
        if not _port_open(settings):
            return False
        try:
            info = FlowHMRClient(settings.host, settings.port, timeout=3).info()
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError("该端口仍在运行，但无法确认 FlowHMR 身份；请检查后端日志后重试") from error
        bridge = info.get("blender_bridge", {}) if isinstance(info, dict) else {}
        if bridge.get("version") != 1 or not bridge.get("motion"):
            raise RuntimeError("该端口不是 FlowHMR 后端，未停止任何进程")
        pid = _listening_pid(settings.port)
        if not pid or pid != _listening_pid(settings.port) or not _stop_pid(pid):
            raise RuntimeError("已连接 FlowHMR，但无法找到或停止监听进程，请检查系统权限")
    else:
        return False
    if settings is not None:
        deadline = time.monotonic() + 5
        while _port_open(settings):
            if time.monotonic() >= deadline:
                raise RuntimeError("退出请求已执行，但端口仍在监听，未确认后端退出")
            time.sleep(0.1)
    if not owned or _PROCESS.poll() is not None:
        _PROCESS = _ENDPOINT = None
    return True


def probe_video(settings, path):
    script = """import cv2, json, sys
cap = cv2.VideoCapture(sys.argv[1])
try:
    if not cap.isOpened():
        raise ValueError('Cannot open video')
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(json.dumps(dict(fps=fps, frames=frames, duration=frames/fps if fps else 0)))
finally:
    cap.release()
"""
    result = subprocess.run([resolve_python(settings), "-c", script, path],
                            capture_output=True, text=True, encoding="utf-8", timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("视频信息读取失败：" + result.stderr[-500:])
    return json.loads(result.stdout)


def prepare_media(settings, path, *, image_resolution="AUTO"):
    script = ("import json, sys; from flowhmr.utils.runtime_blender_media import prepare_media, prepare_panel_preview; "
              "info=prepare_media(sys.argv[1], sys.argv[2], image_resolution=sys.argv[3]); "
              "info['preview_frames']=prepare_panel_preview(info['path'], sys.argv[2], still=True); "
              "print(json.dumps(info))")
    root = Path(settings.backend_root).resolve()
    result = subprocess.run([resolve_python(settings), "-c", script, path, str(root / ".cache/blender-media"), image_resolution],
                            cwd=str(root), capture_output=True, text=True, encoding="utf-8", timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("素材读取失败：" + result.stderr[-500:])
    return json.loads(result.stdout)
