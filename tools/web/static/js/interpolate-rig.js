import { Quaternion, Vector3 } from "three";

// Playback only: integer evaluation and all export samplers stay independent.
export function interpolateRig(rig, root, frames) {
  const nodes = [];
  // Include secondary deform rigs and VRM constraint targets, which need
  // not be in the primary skin's list of bones.
  root.traverse((node) => nodes.push(node));
  const a = nodes.map(() => [new Vector3(), new Quaternion(), new Vector3()]);
  const b = nodes.map(() => [new Vector3(), new Quaternion(), new Vector3()]);
  let lower = -1, upper = -1, mixed = false;
  function readPose(dest) {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i], [p, q, s] = dest[i];
      p.copy(node.position); q.copy(node.quaternion); s.copy(node.scale);
    }
  }
  function writePose(pose, next, t) {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i], [p, q, s] = pose[i], [p2, q2, s2] = next[i];
      if (t) {
        node.position.lerpVectors(p, p2, t);
        node.quaternion.slerpQuaternions(q, q2, t);
        node.scale.lerpVectors(s, s2, t);
      } else {
        node.position.copy(p);
        node.quaternion.copy(q);
        node.scale.copy(s);
      }
      if (!node.matrixAutoUpdate) node.updateMatrix();
    }
    root.updateMatrixWorld(true);
  }
  return {
    ...rig,
    get vmd() { return rig.vmd; },
    setPoseOffsets(settings) {
      lower = upper = -1;
      mixed = false;
      return rig.setPoseOffsets(settings);
    },
    apply(frame) {
      if (!Number.isFinite(frame) || frame < 0 || frame >= frames) return false;
      const lo = Math.floor(frame), hi = Math.min(lo + 1, frames - 1);
      const t = frame - lo;
      if (t < 1e-5 || hi === lo) {
        // Some solvers cache the last integer; restore the exact endpoint if
        // an interpolated pose currently occupies it.
        if (mixed && (lo === lower || lo === upper)) {
          const exact = lo === lower ? a : b;
          writePose(exact, exact, 0);
          return true;
        }
        if (mixed) {
          writePose(b, b, 0);
          mixed = false;
        }
        lower = upper = -1;
        return rig.apply(lo);
      }
      if (lower !== lo || upper !== hi) {
        // Restore the solver's last integer pose before it can skip a repeat.
        if (mixed) writePose(b, b, 0);
        mixed = false;
        lower = upper = -1;
        if (!rig.apply(lo)) return false;
        readPose(a);
        if (!rig.apply(hi)) return false;
        readPose(b);
        lower = lo; upper = hi;
      }
      writePose(a, b, t);
      mixed = true;
      return true;
    },
  };
}
