"""Validated person-box corrections, shared by the server and Blender UI.

Image edits cover the whole static track. Video anchors interpolate only between
edited frames; detections outside those anchors remain unchanged.
"""

import bisect
import math


def validate_edits(edits, candidates, size):
    if not isinstance(edits, list) or len(edits) > 2048:
        raise ValueError("人物框修正必须是列表，最多 2048 个关键帧")
    width, height = size
    normalized = {}
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("无效的人物框修正")
        person, frame, box = edit.get("person"), edit.get("frame"), edit.get("box")
        if type(person) is not int or not 0 <= person < len(candidates):
            raise ValueError("请指定已检测的人物")
        candidate = candidates[person]
        if type(frame) is not int or not candidate["start"] <= frame <= candidate["end"]:
            raise ValueError("修正帧不在人物轨迹范围内")
        if not isinstance(box, list) or len(box) != 4 or any(
                type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError("人物框需要四个有限坐标")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height and x2 - x1 >= 4 and y2 - y1 >= 4):
            raise ValueError("请在画面内框住完整人物，宽高至少 4 像素")
        whole = edit.get("whole_track", False)
        if type(whole) is not bool:
            raise ValueError("无效的整段修正选项")
        if whole and not candidate.get("static_image"):
            raise ValueError("视频只能按帧修正，不能将静止人物框覆盖整段")
        normalized[(person, frame)] = dict(person=person, frame=frame, box=[float(v) for v in box], whole_track=whole)
    return list(normalized.values())


def box_at(candidate, frame, edits=()):
    frames, boxes = candidate.get("frames", []), candidate.get("boxes", [])
    if not frames or len(frames) != len(boxes) or frame < frames[0] - 2 or frame > frames[-1] + 2:
        return None
    anchors = sorted((e for e in edits if e["person"] == candidate["index"]), key=lambda e: e["frame"])
    whole = next((e for e in reversed(anchors) if e.get("whole_track")), None)
    if whole:
        return whole["box"][:]
    if anchors and anchors[0]["frame"] <= frame <= anchors[-1]["frame"]:
        high = bisect.bisect_left([e["frame"] for e in anchors], frame)
        if high == 0:
            return anchors[0]["box"][:]
        a, b = anchors[high - 1], anchors[high]
        t = (frame - a["frame"]) / max(1, b["frame"] - a["frame"])
        return [x + (y - x) * t for x, y in zip(a["box"], b["box"])]
    high = bisect.bisect_left(frames, frame)
    if high == 0:
        return boxes[0][:]
    if high == len(frames):
        return boxes[-1][:]
    a, b = high - 1, high
    t = (frame - frames[a]) / max(1, frames[b] - frames[a])
    return [x + (y - x) * t for x, y in zip(boxes[a], boxes[b])]


def apply_to_tracks(result, candidates, edits):
    """Apply only after selection validation, before any features are extracted."""
    import numpy as np
    for person in sorted({e["person"] for e in edits}):
        candidate = candidates[person]
        track = result.tracks[candidate["track_index"]]
        # Dense frames allow a manual anchor between sparse detections.
        frames = np.arange(candidate["start"], candidate["end"] + 1)
        boxes = np.asarray([box_at(candidate, int(f), edits) for f in frames], dtype=np.float32)
        track.frames, track.bboxes, track.length = frames, boxes, len(frames)
        track.avg_area = float(np.mean((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])))
