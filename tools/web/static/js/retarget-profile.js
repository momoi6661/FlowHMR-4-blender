import { Euler, Quaternion, Vector3 } from "three";
import { appError } from "./i18n.js";

export const JOINT_COUNT = 52;
export const BODY_REQUIRED = [0, 1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 18, 19, 20, 21];
export const CHAINS = { leftFoot:[1,4,7], rightFoot:[2,5,8], leftHand:[16,18,20], rightHand:[17,19,21] };
const deg = Math.PI / 180;
const index = (value) => Number.isInteger(value) && value >= 0 && value < 65536 ? value : -1;
const vector = (value, limit) => [0, 1, 2].map((i) =>
  Number.isFinite(value?.[i]) ? Math.min(limit, Math.max(-limit, value[i])) : 0);
const trimMap = (input, limit) => Object.fromEntries(Array.from({ length:JOINT_COUNT }, (_, j) =>
  [j, vector(input?.[j], limit)]).filter(([, v]) => v.some(Boolean)));

export function normalizeRetargetProfile(value) {
  if (value?.version !== 1 || !Array.isArray(value.map) || value.map.length !== JOINT_COUNT) return null;
  return { version:1, map:value.map.map(index), root:index(value.root),
    ik:Object.fromEntries(Object.keys(CHAINS).map((key) => [key, index(value.ik?.[key])])),
    ...normalizeBoneOffsets(value) };
}

export function normalizeBoneOffsets(value) {
  return { rotations:trimMap(value?.rotations, 180), translations:trimMap(value?.translations, 5) };
}

export function customJointEdits(profile, canTranslate = () => true) {
  const rotation = new Map(), translation = new Map();
  for (const [j, a] of Object.entries(profile?.rotations ?? {})) {
    rotation.set(Number(j), new Quaternion().setFromEuler(new Euler(...a.map((v) => v * deg), "XYZ")));
  }
  for (const [j, a] of Object.entries(profile?.translations ?? {})) {
    if (canTranslate(Number(j))) translation.set(Number(j), new Vector3().fromArray(a));
  }
  return { rotation, translation };
}

export function descends(bones, child, parent) {
  if (child < 0 || parent < 0) return false;
  for (let n = bones[child]?.parent, i = 0; n >= 0 && i < bones.length; n = bones[n]?.parent, i++) {
    if (n === parent) return true;
  }
  return false;
}

const member = (bones, child, parent) => child === parent || descends(bones, child, parent);
const follows = (bones,kind,joint,child,parent) => descends(bones,child,parent) ||
  (kind==="pmx" && (joint===10 || joint===11) && /(?:足先|つま先|toe|ball)/i.test(bones[child].name) &&
    Math.hypot(...bones[child].position.map((p,i)=>p-bones[parent].position[i]))<.32);
const partOrder = [[3, 6, 9, 12, 15], [3, 13, 16, 18, 20], [3, 14, 17, 19, 21],
  [1, 4, 7, 10], [2, 5, 8, 11]];
for (const side of [0, 1]) for (let digit = 0; digit < 5; digit++) {
  const i = 22 + side * 15 + digit * 3;
  partOrder.push([20 + side, i, i + 1, i + 2]);
}

export function jointFitsChain(profile, bones, kind, joint) {
  const map=profile.map, index=map[joint];
  if(!bones[index])return false;
  for(const chain of partOrder) {
    const at=chain.indexOf(joint);
    if(at<0)continue;
    const prev=chain.slice(0,at).findLast((j)=>bones[map[j]]);
    const next=chain.slice(at+1).find((j)=>bones[map[j]]);
    if(prev!=null && !follows(bones,kind,joint,index,map[prev]))return false;
    if(next!=null && !follows(bones,kind,next,map[next],index))return false;
  }
  if(kind!=="pmx") {
    if(joint===0 && [1,2,3].some((j)=>map[j]>=0 && !descends(bones,map[j],index)))return false;
    if([1,2,3].includes(joint) && map[0]>=0 && !descends(bones,index,map[0]))return false;
  }
  return true;
}

export function rootMappingIssue(profile, bones, kind) {
  const {root, map, ik} = profile;
  if (root < 0) return null;
  // A PMX body's Center and its IK handles can inherit from separate
  // branches under the all-parent bone. The VMD solver places both branches.
  if (!bones[root] || (kind === "pmx" && (!bones[root].movable || bones[root].ikEffector >= 0)) ||
      map.some((i, joint) => i >= 0 && (i === root ? joint !== 0 : !member(bones, i, root))) ||
      Object.values(ik).some((i) => i === root || (kind !== "pmx" && i >= 0 && !member(bones, i, root)))) {
    return { key:"retarget.invalid_root" };
  }
  return null;
}

// One authored source joint for each target. Helper/twist bones may appear
// between mapped joints. A PMX pelvis and spine can be siblings under the root.
export function profileIssue(profile, bones, kind, { allowIncomplete = false } = {}) {
  const map = profile.map;
  const firstMissing = BODY_REQUIRED.find((j) => !bones[map[j]]);
  if (!allowIncomplete && firstMissing !== undefined) return { key:"retarget.missing", joint:firstMissing };
  if (map.some((i) => i >= 0 && !bones[i])) return { key:"retarget.invalid_bone" };
  const used = new Set();
  for (let j = 0; j < map.length; j++) {
    if (map[j] < 0) continue;
    if (used.has(map[j])) return { key:"retarget.duplicate", joint:j };
    used.add(map[j]);
  }
  for (const chain of partOrder) {
    const bound = chain.filter((j) => map[j] >= 0);
    for (let t = 1; t < bound.length; t++) {
      const j=bound[t], current=map[j], parent=map[bound[t-1]];
      if (!follows(bones,kind,j,current,parent)) return { key:"retarget.hierarchy", joint:j };
    }
  }
  if (kind !== "pmx" && map[0] >= 0) for (const j of [1, 2, 3]) {
    if (map[j] >= 0 && !descends(bones, map[j], map[0])) return { key:"retarget.hierarchy", joint:j };
  }
  const chosen = new Set();
  for (const [key, chain] of Object.entries(CHAINS)) {
    const id = profile.ik[key];
    if (id < 0) continue;
    const b = bones[id], end = map[chain[2]];
    if (!b || used.has(id) || chosen.has(id) || [...used].some((j) => descends(bones, j, id)) ||
        member(bones, id, map[chain[0]]) || (kind === "pmx" && (!b.movable || b.ikEffector !== end))) {
      return { key:"retarget.invalid_ik", chain:key };
    }
    chosen.add(id);
  }
  return rootMappingIssue(profile, bones, kind);
}

export function assertRetargetProfile(profile, bones, kind) {
  const issue = profileIssue(profile, bones, kind);
  if (issue) throw appError(issue.key);
}

export function targetSkeleton(asset, mesh, { frames = false } = {}) {
  const kind = asset.kind;
  const meshFrame = frames && kind === "pmx" ? mesh.getWorldQuaternion(new Quaternion()).toArray() : null;
  if (kind === "pmx") return asset.rest.bones.map((b) => ({
    name:b.name, nameEn:b.nameEn, parent:b.parent, position:b.worldPosition, movable:!!(b.pmx?.flags & 4),
    ikEffector:b.pmx?.ik?.effector ?? -1,
    ...(frames ? { orientation:b.worldQuaternion,
      translationOrientation:asset.rest.bones[b.parent]?.worldQuaternion ?? meshFrame,
    } : {}),
  }));
  const primary = asset.getRig(mesh);
  const bones = primary.manualBones ?? primary.bones;
  const indexOf = new Map(bones.map((bone, i) => [bone, i]));
  const point = new Vector3();
  mesh.updateWorldMatrix(true, true);
  return bones.map((bone) => {
    let parent = bone.parent;
    while (parent && !indexOf.has(parent)) parent = parent.parent;
    return { name:bone.userData.originalName ?? bone.name, parent:indexOf.get(parent) ?? -1,
      position:bone.getWorldPosition(point).toArray(), movable:true,
      ...(frames ? {
        orientation:bone.getWorldQuaternion(new Quaternion()).toArray(),
        translationOrientation:bone.parent?.getWorldQuaternion(new Quaternion()).toArray(),
      } : {}) };
  });
}

export function measureProportions(source, target, profile, targetFloor, sourceFloor) {
  const point = (j) => target[profile.map[j]];
  const height = point(15).y - targetFloor, sourceHeight = source[15].y - sourceFloor;
  const lengths = Object.fromEntries(Object.entries(CHAINS).map(([key, [a, b, c]]) => {
    const from = source[a].distanceTo(source[b]) + source[b].distanceTo(source[c]);
    const to = point(a).distanceTo(point(b)) + point(b).distanceTo(point(c));
    if (Math.min(from, to) < .002) throw appError("retarget.invalid_length");
    return [key, {from, to, ratio:to / from}];
  }));
  if (!Number.isFinite(height) || !(height > 0.05 && sourceHeight > 0.05)) throw appError("retarget.invalid_length");
  const a = lengths.leftFoot, b = lengths.rightFoot;
  return { height, heightRatio:height / sourceHeight, strideRatio:(a.to+b.to) / (a.from+b.from), lengths };
}
