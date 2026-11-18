import { Matrix4, Quaternion, Vector3 } from "three";
import { createFbxCharacterRetarget } from "./fbx-character-retarget.js";
import { appError } from "./i18n.js";

// Use the preview's solver, then express its poses in the original FBX
// hierarchy. The FBX writer can keep the original bind pose, names and axes.
export async function bakeTargetFbx({asset, libraryId, motion, poseOffsets, offset, yaw}, signal, profile = asset.profile) {
  const root = asset.clone();
  try {
    const rig = createFbxCharacterRetarget(root, asset, motion, {poseOffsets, libraryId});
    const target = asset.getRig(root), manual = poseOffsets?.retarget;
    const bones = manual ? target.manualBones : target.bones;
    const outerBone = (node) => {
      let carrier = node;
      for (let parent = node?.parent; parent && parent !== root; parent = parent.parent) {
        if (parent.isBone) carrier = parent;
      }
      if (!carrier?.isBone || !carrier.userData.fbxModel) throw appError("retarget.export_failed");
      return carrier;
    };
    const body = outerBone(bones[rig.sourceToTarget[0]]);
    const carriers = new Set([body, ...Object.values(manual?.ik ?? {}).filter((i) => i >= 0)
      .map((i) => outerBone(bones[i]))]);
    if (profile === "retarget_fbx") {
      // Preserve detached parts of the original skeleton as well (e.g. an
      // unweighted control or accessory root on a different branch).
      for (const bone of target.manualBones) if (bone.isBone) carriers.add(outerBone(bone));
    }
    const tracked = new Set();
    for (const carrier of carriers) carrier.traverse((node) => {
      if (!node.isBone) return;
      for (let ancestor = node; ancestor !== carrier.parent; ancestor = ancestor.parent) {
        if (!ancestor.userData.fbxModel) throw appError("retarget.export_failed");
        tracked.add(ancestor);
      }
    });
    const path = (node) => {
      const result = [];
      for (let ancestor = node; ancestor !== root; ancestor = ancestor.parent) {
        if (ancestor.userData.fbxModel) result.unshift(ancestor.userData.originalName ?? ancestor.name);
      }
      return result;
    };
    const components = motion.frames * tracked.size * 10;
    if (components > 16_000_000 || tracked.size > 1024) throw appError("retarget.export_limit");
    const samples = new Float32Array(components);
    const tracks = Array.from(tracked, (node) => ({
      node, path:path(node), inverseBindParent:tracked.has(node.parent) ? null : node.parent.matrixWorld.clone().invert(),
    }));
    const paths = tracks.map((track) => track.path);
    const seen = new Set();
    for (const value of paths) {
      const key = JSON.stringify(value);
      if (seen.has(key)) throw appError("server.target_fbx_ambiguous_path", {path:value.join(" / ")});
      seen.add(key);
    }
    const baseY = body.matrixWorld.elements[13];
    // Leave MetaHuman's automatic carrier at its rest height; the vertical
    // motion stays in its children, so body poses match while UE can extract
    // horizontal root motion. Respect an explicitly assigned root.
    const horizontalRoot = profile === "metahuman" && !(manual?.root >= 0) &&
      /(?:^|[:|])root$/i.test(body.userData.originalName ?? body.name);
    const placement = new Matrix4().makeRotationY(yaw).setPosition(new Vector3().fromArray(offset));
    const position = new Vector3(), rotation = new Quaternion(), scale = new Vector3();
    const inv = new Matrix4(), matrix = new Matrix4(), world = new Matrix4();
    const adjusted = new Matrix4(), reconstructed = new Matrix4();
    let nextYield = performance.now() + 25;
    for (let frame = 0; frame < motion.frames; frame++) {
      if (!rig.apply(frame)) throw appError("retarget.export_failed");
      root.updateMatrixWorld(true);
      if (horizontalRoot) {
        adjusted.multiplyMatrices(placement, body.matrixWorld);
        adjusted.elements[13] = baseY;
      }
      for (const [i, {node, inverseBindParent}] of tracks.entries()) {
        if (node === body && horizontalRoot) world.copy(adjusted);
        else world.multiplyMatrices(placement, node.matrixWorld);
        if (inverseBindParent) matrix.multiplyMatrices(inverseBindParent, world);
        else {
          if (node.parent === body && horizontalRoot) inv.copy(adjusted);
          else inv.multiplyMatrices(placement, node.parent.matrixWorld);
          matrix.multiplyMatrices(inv.invert(), world);
        }
        matrix.decompose(position, rotation, scale);
        // FBX channels cannot represent a sheared local transform.
        reconstructed.compose(position, rotation, scale);
        if (matrix.elements.some((value, index) => !Number.isFinite(value) ||
          Math.abs(value - reconstructed.elements[index]) > 1e-5 * Math.max(1, Math.abs(value)))) {
          throw appError("retarget.export_failed");
        }
        const at = 10 * (i * motion.frames + frame);
        position.toArray(samples, at);
        let sign = 1;
        const previous = at - 7; // quaternion offset (+3), one 10-value frame earlier
        if (frame > 0 && rotation.x * samples[previous] + rotation.y * samples[previous + 1] +
            rotation.z * samples[previous + 2] + rotation.w * samples[previous + 3] < 0) sign = -1;
        samples.set([sign*rotation.x, sign*rotation.y, sign*rotation.z, sign*rotation.w], at + 3);
        scale.toArray(samples, at + 7);
      }
      if (performance.now() >= nextYield) {
        await new Promise((resume) => setTimeout(resume, 0));
        signal.throwIfAborted();
        nextYield = performance.now() + 25;
      }
    }
    signal.throwIfAborted();
    return {paths, samples};
  } finally {
    asset.releaseClone(root);
  }
}
