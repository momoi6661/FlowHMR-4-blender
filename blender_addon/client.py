"""Stdlib HTTP client; videos are streamed and local requests bypass proxies."""

import json
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid


class FlowHMRClient:
    def __init__(self, host="127.0.0.1", port=60550, timeout=30):
        self.base = f"http://{host}:{int(port)}"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._media_hints = {}

    def _request(self, path, *, method="GET", body=None, headers=None):
        request = urllib.request.Request(self.base + path, data=body, headers=headers or {}, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            raise RuntimeError(f"FlowHMR HTTP {error.code}: {detail[:500]}") from error

    def info(self):
        return json.loads(self._request("/api/info"))

    def upload_video(self, video_path, *, start_frame=0, end_frame=None,
                     static_camera=True, sam_chunk_size=None, release_after_job=False, **options):
        path = Path(video_path)
        boundary = uuid.uuid4().hex.encode("ascii")
        fields = {"start_frame": str(int(start_frame)), "auto_frames": "true",
                  "static_camera": str(bool(static_camera)).lower(),
                  "sam_chunk_size": str(int(sam_chunk_size)) if sam_chunk_size else "auto",
                  "manual_select": "true", "postprocess": "true",
                  "release_after_job": str(bool(release_after_job)).lower()}
        allowed = {"manual_select", "select_single_person", "postprocess", "hamer", "hamer_stride", "steps", "seed",
                   "cfg_scale", "smoothing_frames", "correction_preset", "sole_fit_strength",
                   "vggt_interval", "correction_floor_m", "source_kind", "source_name", "sam_hand_stride"}
        for key, value in options.items():
            if key not in allowed:
                raise ValueError(f"Unsupported generation option: {key}")
            if value is not None:
                fields[key] = str(value).lower() if isinstance(value, bool) else str(value)
        if end_frame is not None:
            fields["end_frame"] = str(int(end_frame))
        # Keep the original image suffix on the decoding container as well.
        # Older servers discard source_kind/source_name but retain filename.
        upload_name = str(options.get("source_name") or path.name).replace("\\", "/").rsplit("/", 1)[-1]
        upload_name = "".join("_" if ord(char) < 32 or char == '"' else char for char in upload_name).strip()[:240] or "input"
        if options.get("source_kind") == "image" and not re.search(r"\.(png|jpe?g|jfif|webp|bmp|tiff?)$", upload_name, re.I):
            upload_name += ".png"
        if not upload_name.lower().endswith(path.suffix.lower()):
            upload_name += path.suffix.lower()
        chunks = []
        for key, value in fields.items():
            chunks.extend((b"--" + boundary + b"\r\n",
                           f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                           value.encode(), b"\r\n"))
        chunks.extend((b"--" + boundary + b"\r\n",
                       f'Content-Disposition: form-data; name="video"; filename="{upload_name}"\r\n'.encode(),
                       b"Content-Type: application/octet-stream\r\n\r\n"))
        prefix, suffix = b"".join(chunks), b"\r\n--" + boundary + b"--\r\n"

        def stream():
            yield prefix
            with path.open("rb") as video:
                while chunk := video.read(1024 * 1024):
                    yield chunk
            yield suffix

        return json.loads(self._request(
            "/api/upload", method="POST", body=stream(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
                     "Content-Length": str(len(prefix) + path.stat().st_size + len(suffix))},
        ))["job_id"]

    def job(self, job_id):
        return self._with_source_metadata(json.loads(self._request(f"/api/jobs/{job_id}")))

    def _with_source_metadata(self, job):
        """Read explicit provenance saved with results by pre-update servers."""
        if job.get("source_kind") in ("image", "video") or job.get("status") != "done":
            return job
        job_id = job["id"]
        if job_id not in self._media_hints:
            try:
                meta = json.loads(self._request(f"/api/jobs/{job_id}/meta"))
            except (OSError, RuntimeError, ValueError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            self._media_hints[job_id] = ({key: meta[key] for key in ("source_kind", "source_name") if meta.get(key)}
                                        if meta.get("source_kind") in ("image", "video") else {})
        return {**job, **self._media_hints[job_id]}

    def json(self, path, body=None, method=None):
        result = json.loads(self._request(path, method=method or ("POST" if body is not None else "GET"),
                                       body=json.dumps(body).encode() if body is not None else None,
                                       headers={"Content-Type": "application/json"}))
        if body is None and method in (None, "GET") and path.split("?", 1)[0] == "/api/jobs":
            result["jobs"] = [self._with_source_metadata(job) for job in result["jobs"]]
        return result

    def save(self, path, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Keep a failed transfer from leaving a plausible but incomplete result.
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            request = urllib.request.Request(self.base + path)
            with self.opener.open(request, timeout=self.timeout) as response, temporary.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return str(destination)

    def cancel(self, job_id):
        return json.loads(self._request(f"/api/jobs/{job_id}/cancel", method="POST", body=b"{}",
                                       headers={"Content-Type": "application/json"}))

    def gpu(self):
        return json.loads(self._request("/api/gpu"))

    def release_vram(self):
        return json.loads(self._request("/api/gpu/release", method="POST", body=b"{}",
                                       headers={"Content-Type": "application/json"}))

    def download_npz(self, job_id, destination):
        payload = self._request(f"/api/jobs/{job_id}/npz")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return str(destination)
