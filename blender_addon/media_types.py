"""Identify original images, including cached videos from older image jobs."""
import re


def is_image_job(job):
    if job.get("source_kind") in ("image", "video"):
        return job["source_kind"] == "image"
    return any(bool(re.search(
        r"\.(png|jpe?g|jfif|webp|bmp|tiff?)(?:\.(webm|mp4))?$",
        job.get(key) or "", re.I)) for key in ("source_name", "filename"))
