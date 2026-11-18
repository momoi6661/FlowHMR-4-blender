"""Persistent SMPL-H local poses and CPU-only, partial motion edits."""
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import numpy as np


PARTS = {
    "left_hand": [20, *range(22, 37)],
    "right_hand": [21, *range(37, 52)],
    "hands": [20, 21, *range(22, 52)],
    "left_foot": [7, 10],
    "right_foot": [8, 11],
    "feet": [7, 8, 10, 11],
}


def checked_preset(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported pose preset")
    name, part = value.get("name"), value.get("part")
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
            or not isinstance(part, str) or part not in PARTS):
        raise ValueError("Invalid preset name or body part")
    rotations = value.get("rotations")
    if not isinstance(rotations, dict) or set(rotations) != {str(i) for i in PARTS[part]}:
        raise ValueError("Preset joints do not match its body part")
    clean = {}
    for key, rotation in rotations.items():
        if (not isinstance(rotation, list) or len(rotation) != 3
                or any(type(x) not in (int, float) or not np.isfinite(x) or abs(x) > 100 for x in rotation)):
            raise ValueError("Invalid axis-angle rotation")
        clean[key] = list(rotation)
    return {"version": 1, "name": name.strip(), "part": part, "rotations": clean}


def read_motion(path):
    with np.load(path, allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files}
    poses = data["poses"]
    if (poses.ndim not in (2, 3) or poses.shape[1:] not in ((156,), (52, 3))
            or not len(poses) or not np.isfinite(poses).all()):
        raise ValueError("Expected finite SMPL-H poses")
    return data


def capture_preset(path, name, part, frame):
    if not isinstance(part, str) or part not in PARTS or type(frame) is not int:
        raise ValueError("Invalid part or frame")
    poses = read_motion(path)["poses"].reshape(-1, 52, 3)
    if not 0 <= frame < len(poses):
        raise ValueError("Frame outside this motion")
    return checked_preset({"version": 1, "name": name, "part": part,
                           "rotations": {str(i): poses[frame, i].tolist() for i in PARTS[part]}})


def apply_preset(data, preset, start, end, strength=1.0, blend_frames=5):
    """Replace local rotations within an inclusive range using shortest-arc blending."""
    from scipy.spatial.transform import Rotation

    preset = checked_preset(preset)
    result = {key: value.copy() for key, value in data.items()}
    poses = result["poses"].reshape(-1, 52, 3)
    if (type(start) is not int or type(end) is not int or not 0 <= start <= end < len(poses)
            or type(strength) not in (int, float) or not np.isfinite(strength) or not 0 < strength <= 1
            or type(blend_frames) is not int or not 0 <= blend_frames <= 120):
        raise ValueError("Invalid preset application range, strength or blend")
    count = end - start + 1
    weights = np.full(count, strength, dtype=np.float64)
    # Fade only at internal clip boundaries; a one-frame edit gets full strength.
    ramp = min(blend_frames, (count - 1) // 2)
    if ramp:
        fade = np.arange(1, ramp + 1) / (ramp + 1)
        if start > 0:
            weights[:ramp] *= fade
        if end < len(poses) - 1:
            weights[-ramp:] *= fade[::-1]
    for key, target in preset["rotations"].items():
        joint = int(key)
        original = Rotation.from_rotvec(poses[start:end + 1, joint])
        delta = (original.inv() * Rotation.from_rotvec(target)).as_rotvec()
        poses[start:end + 1, joint] = (original * Rotation.from_rotvec(delta * weights[:, None])).as_rotvec()
    # Old contact labels no longer describe the feet after changing their pose.
    if "foot" in preset["part"] or preset["part"] == "feet":
        result.pop("foot_contacts", None)
    return result


class PosePresets:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()

    def _path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid preset identifier")
        return self.root / f"{identifier}.json"

    def get(self, identifier):
        with self.lock:
            value = json.loads(self._path(identifier).read_text(encoding="utf-8"))
            return {**checked_preset(value), "id": identifier, "created": value["created"]}

    def list(self):
        with self.lock:
            entries = []
            for path in self.root.glob("*.json"):
                try:
                    item = self.get(path.stem)
                    entries.append({k: item[k] for k in ("id", "name", "part", "created")})
                except (OSError, ValueError, KeyError):
                    continue
            return sorted(entries, key=lambda item: item["created"], reverse=True)

    def save(self, value):
        value = checked_preset(value)
        identifier = uuid.uuid4().hex
        value.update(id=identifier, created=time.time())
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(identifier)
            temporary = path.with_suffix(".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return value

    def delete(self, identifier):
        with self.lock:
            self._path(identifier).unlink()
