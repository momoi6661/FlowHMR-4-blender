"""Range semantics shared by the panel and HTTP submission (no Blender imports)."""

import math

PROCESSING_FPS = 30


def frame_range(settings):
    """Seconds use [start, end); explicit 30 FPS frames include both endpoints."""
    if settings.use_time_range:
        start_time, end_time = float(settings.start_time), float(settings.end_time)
        if not math.isfinite(start_time) or start_time < 0:
            raise ValueError("起始时间必须是有效的非负秒数")
        start = math.ceil(start_time * PROCESSING_FPS - 1e-8)
        end = None
        if not settings.to_video_end:
            if not math.isfinite(end_time) or end_time <= start_time:
                raise ValueError("结束时间必须晚于起始时间")
            end = math.ceil(end_time * PROCESSING_FPS - 1e-8) - 1
    else:
        start = int(settings.start_frame)
        end = None if settings.to_video_end else int(settings.end_frame)
    if start < 0 or (end is not None and end < start):
        raise ValueError("该区间不包含可处理的帧，请调整起止位置")
    duration = float(settings.video_duration or 0)
    if duration > 0 and start / PROCESSING_FPS >= duration:
        raise ValueError("起始位置已超出视频时长")
    if duration > 0 and end is not None:
        end = min(end, math.ceil(duration * PROCESSING_FPS - 1e-8) - 1)
    return start, end
