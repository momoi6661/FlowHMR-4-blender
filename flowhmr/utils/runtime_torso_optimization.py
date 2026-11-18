"""Torso-motion optimization: compose base-checkpoint torso with the standard motion.

The base checkpoint reproduces hip/shoulder swing better, but its local leg angles
cannot simply replace the standard result: copying leg locals under a different
pelvis rotation moves the whole leg chain, which makes the character float. This
module keeps the base pelvis/spine/neck/head local rotations and restores the
standard result's world-space ankles (positions and foot orientation) with a
two-bone leg IK. A smoothed, minimal pelvis drop is used only when a standard
ankle target is unreachable under the new pelvis. Shape and root translation stay
from the standard result, and bone lengths never change.
"""

import numpy as np

TORSO_JOINTS = (0, 3, 6, 9, 12, 15)
LEGS = ((1, 4, 7, 10), (2, 5, 8, 11))


def _stats(value):
    value = np.asarray(value)
    return {"mean": float(value.mean()), "p95": float(np.quantile(value, .95)), "max": float(value.max())}


def _mats(pose):
    from scipy.spatial.transform import Rotation
    return Rotation.from_rotvec(pose[:, :22].reshape(-1, 3)).as_matrix().reshape(-1, 22, 3, 3)


def _quat(matrix):
    from scipy.spatial.transform import Rotation
    return Rotation.from_matrix(matrix.reshape(-1, 3, 3)).as_quat().reshape(-1, 22, 4)[..., [3, 0, 1, 2]]


def _normalize(x):
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def _align(source, target):
    """Minimal world rotation taking source onto target; keeps the original twist."""
    from scipy.spatial.transform import Rotation
    a, b = _normalize(source), _normalize(target)
    axis = np.cross(a, b)
    sine = np.linalg.norm(axis, axis=-1)
    cosine = np.clip(np.sum(a * b, axis=-1), -1, 1)
    vector = axis / np.maximum(sine[:, None], 1e-12) * np.arctan2(sine, cosine)[:, None]
    opposite = (sine < 1e-9) & (cosine < 0)
    if opposite.any():
        basis = np.eye(3)[np.argmin(np.abs(a[opposite]), axis=-1)]
        vector[opposite] = _normalize(np.cross(a[opposite], basis)) * np.pi
    return Rotation.from_rotvec(vector).as_matrix()


def _motion(data, key, shape):
    value = np.asarray(data[key], dtype=np.float32)
    return value.reshape(shape)


def compose_torso_optimization(base, latest, smpl_model_path, *, body_reference="neutral"):
    """Return (composed motion dict, metrics) from two FlowHMR NPZ results."""
    import torch
    from scipy.ndimage import gaussian_filter1d, maximum_filter1d
    from scipy.spatial.transform import Rotation
    from flowhmr.core.bodymodels.smpl_skeleton import SMPLMesh
    from rgbmocap_correction.correction import fk
    from rgbmocap_correction.flow_correction import SoleGeometry

    latest_poses = _motion(latest, "poses", (-1, 52, 3))
    base_poses = _motion(base, "poses", (-1, 52, 3))
    frames = min(len(latest_poses), len(base_poses))
    latest_poses, base_poses = latest_poses[:frames], base_poses[:frames]
    latest_trans = np.asarray(latest["trans"], dtype=np.float64).reshape(frames, 3)
    shapes = np.asarray(latest["betas"], dtype=np.float32).reshape(1, -1)

    body = SMPLMesh(model_path=str(smpl_model_path)).cpu().float().eval()
    geometry = SoleGeometry(body, torch.from_numpy(shapes))
    rest = geometry.rest[:22].astype(np.float64)

    def fk_np(local, roots):
        return fk(rest, roots, _quat(local))

    poses = latest_poses.astype(np.float32).copy()
    poses[:, TORSO_JOINTS] = base_poses[:, TORSO_JOINTS]
    local = _mats(poses)
    ref_local = _mats(latest_poses)
    ref_roots = latest_trans + rest[0]
    ref_pos, ref_world = fk_np(ref_local, ref_roots)
    old_pos, _ = fk_np(_mats(base_poses), np.asarray(base["trans"], dtype=np.float64).reshape(frames, 3) + rest[0])
    old_probes, _ = geometry.sample(np.asarray(base["trans"], dtype=np.float64).reshape(frames, 3) + rest[0],
                                    _quat(_mats(base_poses)))
    ref_probes, _ = geometry.sample(ref_roots, _quat(ref_local))

    hip_ids = [1, 2]
    ankle_ids = [7, 8]
    hips = ref_roots[:, None, :] + np.einsum("tij,kj->tki", local[:, 0], rest[hip_ids] - rest[0])
    targets = ref_pos[:, ankle_ids]
    lengths = np.array([[np.linalg.norm(rest[k] - rest[h]), np.linalg.norm(rest[a] - rest[k])]
                        for h, k, a, _ in LEGS])
    radius = lengths.sum(axis=1) * .9998
    horizontal = np.sum((hips[..., [0, 2]] - targets[..., [0, 2]]) ** 2, axis=-1)
    vertical = hips[..., 1] - targets[..., 1]
    reachable = (horizontal < radius ** 2) & (vertical > 0)
    raw_drop = np.where(reachable, np.maximum(vertical - np.sqrt(np.maximum(radius ** 2 - horizontal, 0)), 0),
                        0).max(axis=1)
    drop = np.maximum(raw_drop, gaussian_filter1d(maximum_filter1d(raw_drop, size=7, mode="nearest"),
                                                  1.5, mode="nearest"))
    if drop.max() >= .05:
        raise RuntimeError(f"Unexpected pelvis correction: {drop.max():.3f} m")
    roots = ref_roots.copy()
    roots[:, 1] -= drop

    clamped_any = np.zeros(frames, dtype=bool)
    for foot, (hip, knee, ankle, _toe) in enumerate(LEGS):
        hip_pos = roots + np.einsum("tij,j->ti", local[:, 0], rest[hip] - rest[0])
        target = ref_pos[:, ankle]
        axis = _normalize(target - hip_pos)
        distance = np.linalg.norm(target - hip_pos, axis=-1)
        l1, l2 = lengths[foot]
        low, high = abs(l1 - l2) + 1e-4, l1 + l2 - 1e-4
        clamped = (distance < low) | (distance > high)
        clamped_any |= clamped
        distance = np.clip(distance, low, high)
        along = (l1 * l1 - l2 * l2 + distance * distance) / (2 * distance)
        height = np.sqrt(np.maximum(l1 * l1 - along * along, 0))
        pole = ref_pos[:, knee] - hip_pos
        pole -= np.sum(pole * axis, axis=-1, keepdims=True) * axis
        singular = np.linalg.norm(pole, axis=-1) < 1e-7
        if singular.any():
            fallback = ref_world[singular, hip, :, 2]
            fallback -= np.sum(fallback * axis[singular], axis=-1, keepdims=True) * axis[singular]
            pole[singular] = fallback
        desired_knee = hip_pos + along[:, None] * axis + height[:, None] * _normalize(pole)
        w_hip = _align(ref_pos[:, knee] - ref_pos[:, hip], desired_knee - hip_pos) @ ref_world[:, hip]
        w_knee = _align(ref_pos[:, ankle] - ref_pos[:, knee], target - desired_knee) @ ref_world[:, knee]
        local[:, hip] = np.swapaxes(local[:, 0], -1, -2) @ w_hip
        local[:, knee] = np.swapaxes(w_hip, -1, -2) @ w_knee
        local[:, ankle] = np.swapaxes(w_knee, -1, -2) @ ref_world[:, ankle]
        for joint in (hip, knee, ankle):
            poses[:, joint] = Rotation.from_matrix(local[:, joint]).as_rotvec().astype(np.float32)

    trans = latest_trans.astype(np.float32).copy()
    trans[:, 1] -= drop.astype(np.float32)
    new_local = _mats(poses)
    new_pos, new_world = fk_np(new_local, trans.astype(np.float64) + rest[0])
    new_probes, _ = geometry.sample(trans.astype(np.float64) + rest[0], _quat(new_local))

    def joint_metric(a, b):
        return _stats(np.linalg.norm(np.asarray(a) - np.asarray(b), axis=-1) * 1000)

    foot_angles = Rotation.from_matrix((np.swapaxes(ref_world[:, [7, 8, 10, 11]], -1, -2)
                                        @ new_world[:, [7, 8, 10, 11]]).reshape(-1, 3, 3)).magnitude()
    leg_ids = [1, 2, 4, 5, 7, 8]
    untouched = [j for j in range(52) if j not in TORSO_JOINTS + tuple(leg_ids)]
    metrics = {
        "frames": int(frames),
        "ankle_target_error_mm": joint_metric(new_pos[:, ankle_ids], ref_pos[:, ankle_ids]),
        "sole_target_error_mm": joint_metric(new_probes, ref_probes),
        "foot_world_orientation_error_deg": _stats(np.degrees(foot_angles)),
        "pelvis_drop_mm": _stats(drop * 1000),
        "unreachable_frames_without_pelvis_adjustment": int((raw_drop > 0).sum()),
        "clamped_frames": int(clamped_any.sum()),
        "torso_matches_base": bool(np.array_equal(poses[:, TORSO_JOINTS], base_poses[:, TORSO_JOINTS])),
        "other_local_rotations_match_standard": bool(np.array_equal(poses[:, untouched], latest_poses[:, untouched])),
        "bone_lengths_max_error_mm": max(
            float(np.max(np.abs(np.linalg.norm(new_pos[:, k] - new_pos[:, h], axis=-1) - lengths[f, 0]))) * 1000
            for f, (h, k, _a, _toe) in enumerate(LEGS)),
    }
    if not metrics["torso_matches_base"] or not metrics["other_local_rotations_match_standard"]:
        raise RuntimeError("Torso optimization did not preserve the source rotations.")
    # Frames with an unreachable target are clamped along the ankle direction; the
    # foot orientation still matches, and the residual stays reported in the metrics.
    limit = 2.0 if metrics["clamped_frames"] else .1
    if metrics["ankle_target_error_mm"]["max"] >= limit or metrics["foot_world_orientation_error_deg"]["max"] >= .01:
        raise RuntimeError("Torso optimization could not restore the standard foot targets.")

    fps = int(np.asarray(latest["mocap_framerate"]).item()) if "mocap_framerate" in latest else 30
    reference = str(np.asarray(latest["body_reference"]).item()) if "body_reference" in latest else body_reference
    motion = {
        "poses": poses.astype(np.float32),
        "betas": shapes.copy(),
        "trans": trans,
        "Rh": poses[:, 0].copy(),
        "mocap_framerate": np.int64(fps),
        "num_frames": np.int64(frames),
        "gender": np.array(reference),
        "body_reference": np.array(reference),
    }
    if "foot_contacts" in latest:
        motion["foot_contacts"] = np.asarray(latest["foot_contacts"], dtype=np.float32).reshape(frames, 4)
    return motion, metrics


def save_composed_motion(motion, path_base, smpl_model_path):
    """Write the composed NPZ (and FBX when the FBX SDK is installed) in publish convention."""
    np.savez(path_base + ".npz", **motion)
    try:
        from flowhmr.core.export.smplh2fbx import SMPLH2FBX
    except ImportError:
        return False
    with SMPLH2FBX(smplh_model_path=str(smpl_model_path)) as writer:
        writer.convert_npz_to_fbx(motion, path_base + ".fbx", export_mesh=True)
    return True
