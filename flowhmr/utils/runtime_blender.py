"""Numeric Blender interchange, independent of FBX, Torch and CUDA."""

import io
import json

import numpy as np

from flowhmr.core.bodymodels.smplh_data import load_smplh_data


def blender_motion(path, reference, *, people=None, job_id="", source_start=0, include_mesh=True,
                   source_image=False):
    model = load_smplh_data(reference)
    parents = model["kintree_table"][0].astype(np.int32).copy()
    parents[0] = -1
    payload = {"parents": parents}
    actors = []
    with np.load(path, allow_pickle=False) as source:
        fps = float(np.asarray(source.get("mocap_framerate", 30)).item())
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("Invalid motion FPS")
        records = people or [{"person": 1, "id": job_id, "start": 0}]
        origin = min(p["start"] for p in records)
        for index, person in enumerate(records):
            suffix = f"_{person['person']}" if people else ""
            poses = np.asarray(source[f"poses{suffix}"], dtype=np.float32).reshape(-1, 52, 3)
            trans = np.asarray(source[f"trans{suffix}"], dtype=np.float32)
            betas = np.atleast_2d(np.asarray(source[f"betas{suffix}"], dtype=np.float32))
            if (not len(poses) or trans.shape != (len(poses), 3) or not betas.size
                    or not all(np.isfinite(a).all() for a in (poses, trans, betas))):
                raise ValueError("Invalid Blender motion arrays")
            if people and "end" in person and person["end"] - person["start"] + 1 != len(poses):
                raise ValueError("Actor timeline does not match Blender motion")
            if source_image:
                poses, trans = poses[:1], trans[:1]
            count = min(betas.shape[-1], model["shapedirs"].shape[-1])
            vertices = model["v_template"] + model["shapedirs"][:, :, :count] @ betas[0, :count]
            payload[f"joints_{index}"] = np.asarray(model["J_regressor"] @ vertices, dtype=np.float32)
            payload[f"poses_{index}"] = poses
            payload[f"trans_{index}"] = trans
            if include_mesh:
                payload[f"vertices_{index}"] = np.asarray(vertices, dtype=np.float32)
            actors.append(dict(index=index, person=person["person"], job_id=person.get("id", job_id),
                               start=0 if source_image else person["start"] - origin, frames=len(poses),
                               source_start=person["start"] if people else source_start))
    if include_mesh:
        payload.update(faces=np.asarray(model["f"], dtype=np.int32),
                       weights=np.asarray(model["weights"], dtype=np.float32))
    payload["manifest"] = np.array(json.dumps(dict(version=1, job_id=job_id, fps=fps, actors=actors,
                                                   source_kind="image" if source_image else "video")))
    output = io.BytesIO()
    np.savez_compressed(output, **payload)
    output.seek(0)
    return output
