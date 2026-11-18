"""Resolve an inference backend without importing Torch on desktop startup."""

from dataclasses import dataclass
from importlib.util import find_spec
import re
import sys

_DEVICE = re.compile(r"(cuda|nvidia|rocm|xpu)(?::(0|[1-9][0-9]{0,8}))?")
DEVICE_HELP = "auto, cuda[:N], nvidia[:N], rocm[:N], xpu[:N] or cpu"


class DeviceError(RuntimeError):
    def __init__(self, message, code, **params):
        super().__init__(message)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class DeviceRequest:
    device: str
    vendor: str | None = None


def plan_device(value, runtime):
    """Honor the configured slot. Only auto-detect Torch on a source checkout."""
    name = (value or "auto").strip().lower()
    if name == "auto":
        if runtime.active is not None or (runtime.root / "runtime/python.exe").is_file():
            name = runtime.default_device()
        elif find_spec("torch") is None:
            name = "cpu"
        else:
            import torch

            # PyTorch on ROCm also advertises torch.cuda. Apple MPS has not
            # been checked against all operators; keep it off the auto path.
            xpu_available = getattr(getattr(torch, "xpu", None), "is_available", None)
            name = "cuda" if torch.cuda.is_available() else "xpu" if (
                sys.platform == "win32" and xpu_available and xpu_available()) else "cpu"
    if name == "cpu":
        return DeviceRequest(name)
    if name in ("mps", "apple"):
        raise ValueError("The FlowHMR inference pipeline has not been validated on Apple MPS; use CPU.")
    match = _DEVICE.fullmatch(name)
    if not match:
        raise ValueError(f"Expected {DEVICE_HELP}; got {value!r}.")
    token, index = match.groups()
    vendor = token if token in ("nvidia", "rocm") else None
    canonical = "cuda" if vendor else token
    return DeviceRequest(canonical if index is None else f"{canonical}:{index}", vendor)


def backend_summary(request, runtime, engine=None):
    """Describe the configured slot without loading an accelerator for /api/info."""
    variant = runtime.system if runtime.active == "system" else runtime.active
    encoder = getattr(getattr(engine, "sam_extractor", None), "xpu_backbone_device", None)
    device = request.device if engine is None else str(encoder or engine.device)
    kind = device.split(":", 1)[0]
    if kind == "cuda":
        if variant and variant.startswith("rocm"):
            kind = "rocm"
        elif variant and variant.startswith("cu"):
            kind = "nvidia"
        else:
            # The worker has already loaded this runtime if the engine exists.
            version = getattr(sys.modules.get("torch"), "version", None)
            kind = ("rocm" if getattr(version, "hip", None) else
                    "nvidia" if getattr(version, "cuda", None) else request.vendor or "cuda")
    return {"kind": kind, "device": device, "runtime": variant}


def resolve_device(request, runtime):
    """Choose and initialize the actual device on the worker or in the CLI."""
    import torch
    from . import runtime_rocm as rocm, runtime_xpu as xpu
    from .runtime_environments import cuda_ordinal

    device = torch.device(request.device)
    if device.type == "cpu":
        return device
    if device.type == "xpu":
        try:
            index = xpu.xpu_ordinal(torch, ordinal=device.index)
            torch.xpu.set_device(index)
        except (AttributeError, RuntimeError) as error:
            raise DeviceError("Intel XPU 需要 Windows 11 和受支持的 Intel Arc 显卡。请检查驱动。",
                              "runtime_xpu_no_device") from error
        return torch.device("xpu", index)

    hip = bool(getattr(torch.version, "hip", None))
    vendor = "rocm" if hip else "nvidia" if getattr(torch.version, "cuda", None) else None
    if request.vendor and request.vendor != vendor:
        raise DeviceError(f"设备 {request.vendor} 与当前安装的 PyTorch 运行环境不匹配。",
                          "runtime_device_mismatch", device=request.vendor)
    if sys.platform == "win32" and hip:
        profile = rocm.PROFILES.get(runtime.active)
        try:
            index = rocm.gpu_ordinal(torch, profile.arch if profile else None, ordinal=device.index)
        except RuntimeError as error:
            if profile:
                raise DeviceError(f"当前 ROCm 环境需要可用的 {profile.arch} 显卡。",
                                  "runtime_rocm_gpu_required", arch=profile.arch) from error
            raise DeviceError("没有可用于当前 ROCm 环境的显卡。请检查驱动和显卡设置。",
                              "runtime_rocm_no_device") from error
        device = torch.device("cuda", index)
    elif variant := runtime.cuda_variant():
        try:
            device = torch.device("cuda", cuda_ordinal(torch, variant, device.index))
        except RuntimeError as error:
            raise DeviceError("此版本不适用于可用的 NVIDIA 显卡。", "runtime_cuda_arch_mismatch") from error
    elif not torch.cuda.is_available() or (device.index is not None and device.index >= torch.cuda.device_count()):
        raise DeviceError("请求的 GPU 无法在当前运行环境中使用。", "runtime_device_mismatch", device=request.device)
    # cuda and HIP both use the torch.cuda namespace. Set the chosen ordinal
    # before YOLOX creates any tensors.
    torch.cuda.set_device(device.index if device.index is not None else 0)
    rocm.configure_windows_hip(torch, device=device, attention=True)
    return device
