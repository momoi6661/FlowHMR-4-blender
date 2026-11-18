"""Write the viewport's baked transforms on the original FBX bone hierarchy."""

import numpy as np
from scipy.spatial.transform import Rotation
from transforms3d.euler import euler2mat
import fbx

from .fbx_profiles import _PIVOT, _id, _import_template, _is_bone, _property_rotation, _retain_body, _walk, _xyz
from .fbx_utils import (
    animate_rotation_from_rotmats, animate_single_channel, animate_translation,
    clear_animations, create_manager, export_scene_atomic,
)

MAX_TARGET_TRACKS = 1024
MAX_TARGET_VALUES = 16_000_000
TARGET_WIDTH = 10  # translation xyz, quaternion xyzw, scale xyz


class AmbiguousFbxPath(ValueError):
    pass


def _resolve_nodes(root, paths, scene_nodes):
    by_path = {}
    for node in scene_nodes[1:]:
        names = []
        current = node
        while current is not None and _id(current) != _id(root):
            names.append(current.GetName())
            current = current.GetParent()
        key = tuple(reversed(names))
        by_path[key] = node if key not in by_path else None
    nodes = {}
    for path in paths:
        key = tuple(path)
        if key in by_path and by_path[key] is None:
            raise AmbiguousFbxPath(" / ".join(path))
        node = by_path.get(key)
        attr = node.GetNodeAttribute() if node is not None else None
        if node is None or (attr is not None and attr.GetAttributeType() not in
                            (fbx.FbxNodeAttribute.EType.eSkeleton, fbx.FbxNodeAttribute.EType.eNull)):
            raise ValueError("The saved FBX hierarchy no longer matches the retargeted character")
        nodes[key] = node
    if not any(_is_bone(node) for node in nodes.values()) and not any(
        _id(node) in _skin_joint_ids(scene_nodes) for node in nodes.values()
    ):
        raise ValueError("No animated skeleton was found")
    return nodes


def _skin_joint_ids(nodes):
    # FBX can skin to a control node marked Null rather than LimbNode.
    joint_ids = set()
    kind = fbx.FbxDeformer.EDeformerType.eSkin
    for node in nodes:
        mesh = node.GetMesh()
        if mesh is not None:
            for i in range(mesh.GetDeformerCount(kind)):
                skin = mesh.GetDeformer(i, kind)
                for j in range(skin.GetClusterCount()):
                    link = skin.GetCluster(j).GetLink()
                    if link is not None:
                        joint_ids.add(_id(link))
    return joint_ids


def _write_trs(layer, node, samples, duration, start):
    local = node.EvaluateLocalTransform()
    rest_q = local.GetQ()
    rest = Rotation.from_quat([rest_q[i] for i in range(4)]).as_matrix()
    rotations = Rotation.from_quat(samples[:, 3:7]).as_matrix()
    position = samples[:, :3].astype(np.float64)
    scale = samples[:, 7:10]
    authored, axes = _property_rotation(node, rest, rotations)
    # FBX evaluates the pivot offsets in addition to LclTranslation. Use the
    # original pivots but solve the translation channels for each sample.
    roff = _xyz(node.GetRotationOffset(_PIVOT))
    rp = _xyz(node.GetRotationPivot(_PIVOT))
    soff = _xyz(node.GetScalingOffset(_PIVOT))
    sp = _xyz(node.GetScalingPivot(_PIVOT))
    if np.any(roff) or np.any(rp) or np.any(soff) or np.any(sp):
        correction = roff + rp + (rotations @ (soff + sp - rp - scale * sp)[..., None])[..., 0]
        position -= correction
        static = roff + rp + rest @ (soff + sp - rp - _xyz(local.GetS()) * sp)
        if not np.allclose(_xyz(local.GetT()) - _xyz(node.LclTranslation.Get()), static, atol=1e-5):
            raise ValueError(f"Unsupported FBX pivot on {node.GetName()}")
    if not np.allclose(rotations, rest, atol=1e-6, rtol=0):
        animate_rotation_from_rotmats(layer, node, authored, duration, axes=axes, start_frame=start)
    if not np.allclose(position, _xyz(node.LclTranslation.Get()), atol=1e-6, rtol=0):
        animate_translation(layer, node, position, duration, start_frame=start)
    if not np.allclose(scale, _xyz(node.LclScaling.Get()), atol=1e-6, rtol=0):
        for axis in "XYZ":
            animate_single_channel(layer, node.LclScaling, axis, scale, duration, start)


def write_target_fbx(source, output, paths, values, fps, start, profile, *, with_skin=False):
    mgr = create_manager()
    try:
        scene = _import_template(mgr, source, with_skin=with_skin)
        root = scene.GetRootNode()
        scene_nodes = _walk(root)
        nodes = _resolve_nodes(root, paths, scene_nodes)
        if with_skin:
            clear_animations(scene)
            scene.GetAnimationEvaluator().Reset()
        else:
            retained = nodes
            if profile == "retarget_fbx":
                # Keep unanimated and disconnected authored bone branches too.
                # The current-role export is also a counterpart to the original rig.
                skin_joints = _skin_joint_ids(scene_nodes)
                retained = {_id(node): node for node in nodes.values()}
                retained.update({_id(node): node for node in scene_nodes[1:]
                                 if _is_bone(node) or _id(node) in skin_joints})
            _retain_body(scene, scene_nodes, retained)
        # Local evaluation with no time argument uses the original authored pose.
        first, last = fbx.FbxTime(), fbx.FbxTime()
        last.SetSecondDouble((start + values.shape[1] - 1) / fps)
        span = fbx.FbxTimeSpan(first, last)
        settings = scene.GetGlobalSettings()
        settings.SetTimeMode(fbx.FbxTime.ConvertFrameRateToTimeMode(fps))
        settings.SetTimelineDefaultTimeSpan(span)
        stack = fbx.FbxAnimStack.Create(scene, "Motion")
        stack.SetLocalTimeSpan(span)
        stack.SetReferenceTimeSpan(span)
        layer = fbx.FbxAnimLayer.Create(scene, "BaseLayer")
        stack.AddMember(layer)
        scene.SetCurrentAnimationStack(stack)
        for node, samples in zip(nodes.values(), values):
            _write_trs(layer, node, samples, 1. / fps, start)
        export_scene_atomic(mgr, scene, str(output), embed_textures=with_skin,
                            file_version="FBX201400" if profile == "mixamo" else None)
    finally:
        mgr.Destroy()
