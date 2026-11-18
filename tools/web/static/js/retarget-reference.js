import { Vector3 } from "three";

const UP = new Vector3(0,1,0);
const DEFAULT_DROP = 35;

// The switch changes the joint picker only. Replacing the actual SMPL-H
// rest joints would double-correct a target whose bind pose is already used.
export function targetRestPose(bones, map, suggested) {
  const point = (j) => {
    const p = bones[map[j]]?.position ?? bones[suggested[j]]?.position;
    return p ? new Vector3().fromArray(p) : null;
  };
  const left = point(16), right = point(17), center = point(0);
  if (!left || !right) return null;
  const mid = left.clone().add(right).multiplyScalar(.5);
  const up = center ? mid.clone().sub(center).normalize() : UP.clone();
  if (up.dot(UP) < .65) up.copy(UP);
  const across = left.clone().sub(right);
  across.addScaledVector(up, -across.dot(up));
  if (across.lengthSq() < 1e-8) return null;
  across.normalize();
  const drops = [];
  for (const side of [0,1]) {
    const shoulder = side ? right : left;
    const elbow = point(18+side), wrist = point(20+side);
    const drop = (end) => {
      if (!end) return NaN;
      const delta = end.clone().sub(shoulder);
      const out = (side ? -1 : 1)*delta.dot(across);
      const down = -delta.dot(up);
      return out > .012 && Math.hypot(out,down) > delta.length()*.7
        ? Math.atan2(down,out)*180/Math.PI : NaN;
    };
    const upper = drop(elbow), full = drop(wrist);
    // A bent forearm alone does not make the bind pose an A pose.
    if (!Number.isFinite(upper) || (wrist && (!Number.isFinite(full) || Math.abs(upper-full)>22))) return null;
    drops.push(Number.isFinite(full) ? .65*upper+.35*full : upper);
  }
  if (Math.abs(drops[0]-drops[1]) > 17) return null;
  if (drops.every((deg)=>deg>=19 && deg<=68)) return { type:"a", drops };
  if (drops.every((deg)=>deg>=-15 && deg<=15)) return { type:"t" };
  return null;
}

export function chooseRestPose(mode, detected) {
  if (mode === "t") return { type:"t" };
  if (mode === "a") return { type:"a", drops:[DEFAULT_DROP,DEFAULT_DROP] };
  return detected ?? { type:"t" };
}

export function sourceBodyReference(rest, reference) {
  if (rest?.length !== 156 || !rest.every(Number.isFinite)) return null;
  let lx = rest[3*16]-rest[3*17], lz = rest[3*16+2]-rest[3*17+2];
  const span = Math.hypot(lx,lz);
  if (span < 1e-5) return null;
  lx /= span; lz /= span;
  const points = Array.from({length:22}, (_, i) => [rest[3*i]*lx+rest[3*i+2]*lz,rest[3*i+1]]);
  const ys = points.map(([,y])=>y), height = Math.max(...ys)-Math.min(...ys);
  if (height < .1) return null;
  if (reference.type === "a") for (const side of [0,1]) {
    const shoulder=16+side, elbow=18+side, wrist=20+side, sign=side?-1:1;
    const [x,y]=points[shoulder];
    const existing=Math.atan2(y-points[elbow][1],sign*(points[elbow][0]-x));
    const angle=reference.drops[side]*Math.PI/180-existing;
    const c=Math.cos(angle), s=Math.sin(angle);
    for (const id of [elbow,wrist]) {
      const out=sign*(points[id][0]-x), up=points[id][1]-y;
      points[id]=[x+sign*(c*out+s*up),y+c*up-s*out];
    }
  }
  const scale = 228/height;
  const offsetX = points[0][0], offsetY = Math.max(...ys);
  const drawing = points.map(([x,y])=>[(x-offsetX)*scale,(offsetY-y)*scale]);
  // Separate the toes from the ankles in this front-facing selector.
  for (const id of [10,11]) {
    drawing[id][0] += id===10 ? 8 : -8;
    drawing[id][1] = Math.max(drawing[id][1],drawing[id-3][1]+12);
  }
  return drawing;
}
