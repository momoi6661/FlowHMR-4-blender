"""Install FlowHMR's optional correction from a verified project-local wheel."""

import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[2]
NATIVE_NAME = "_motion_correction.cp310-win_amd64.pyd"
NATIVE_SHA256 = "46fea913e302fafa2281842e8b081ba34cfcbc572e2d2f36ac107bc89ed22638"
WHEEL_TAG = "cp310-cp310-win_amd64"


def prepare_wheel(native, wheel):
    """Package the pinned release extension with the vendored Python sources."""
    binary = native.read_bytes()
    if hashlib.sha256(binary).hexdigest() != NATIVE_SHA256:
        raise ValueError("The native extension does not match the expected build SHA-256.")
    source = ROOT / "third_party/KimodoMotionCorrection"
    dist = "motion_correction-1.0.0.dist-info"
    entries = {
        f"motion_correction/{path.name}": path.read_bytes()
        for path in sorted((source / "python/motion_correction").glob("*.py"))
    }
    entries[f"motion_correction/{NATIVE_NAME}"] = binary
    entries[f"{dist}/METADATA"] = (
        "Metadata-Version: 2.1\nName: motion-correction\nVersion: 1.0.0\n"
        "Summary: Standalone correct_motion function\nLicense: Apache-2.0\n"
        "Requires-Python: >=3.8\nRequires-Dist: torch>=1.10.0\n"
        "Requires-Dist: numpy>=1.19.0\n\n"
    ).encode()
    entries[f"{dist}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: flowhmr-verified-native\n"
        f"Root-Is-Purelib: false\nTag: {WHEEL_TAG}\n\n"
    ).encode()
    for path in [source / "LICENSE", *sorted((source / "notices").iterdir())]:
        if path.is_file():
            entries[f"{dist}/licenses/{path.relative_to(source).as_posix()}"] = path.read_bytes()
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([name, f"sha256={digest}", len(data)])
    writer.writerow([f"{dist}/RECORD", "", ""])
    entries[f"{dist}/RECORD"] = record.getvalue().encode()
    wheel.parent.mkdir(parents=True, exist_ok=True)
    temporary = wheel.with_suffix(".whl.tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, data)
        temporary.replace(wheel)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cache = ROOT / ".cache/correction"
    parser.add_argument("--wheel", type=Path, default=cache / f"motion_correction-1.0.0-{WHEEL_TAG}.whl")
    parser.add_argument("--native", type=Path, help="Verified release .pyd to package into the local wheel")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare/check the wheel without syncing uv")
    args = parser.parse_args()
    if (Path(sys.prefix).resolve() != ROOT / ".venv" or sys.version_info[:2] != (3, 10)
            or sysconfig.get_platform() != "win-amd64"):
        parser.error("Run this installer with FlowHMR's Windows x64 .venv/Scripts/python.exe (CPython 3.10).")

    wheel = args.wheel.resolve()
    if args.native:
        try:
            prepare_wheel(args.native.resolve(), wheel)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if not wheel.is_file():
        parser.error(f"Correction wheel missing: {wheel}. Supply --native PATH to the pinned release {NATIVE_NAME}.")
    with ZipFile(wheel) as archive:
        binary = archive.read(f"motion_correction/{NATIVE_NAME}")
    if hashlib.sha256(binary).hexdigest() != NATIVE_SHA256:
        parser.error("The correction wheel does not match the expected native build SHA-256.")
    print(f"Verified correction wheel: {wheel}", flush=True)
    if args.prepare_only:
        return
    managed_wheel = cache / f"motion_correction-1.0.0-{WHEEL_TAG}.whl"
    if wheel != managed_wheel.resolve():
        managed_wheel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wheel, managed_wheel)
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv is required to sync the source environment.")
    subprocess.run([uv, "sync", "--locked", "--python", sys.executable], check=True, cwd=ROOT)
    subprocess.run([
        sys.executable, "-c", "from rgbmocap_correction.correction import backend; "
        "assert backend().rgb_mocap_anatomical_version == 1; print('Native contact correction is ready.')",
    ], check=True)


if __name__ == "__main__":
    main()
