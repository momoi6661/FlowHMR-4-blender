import { Bone, BufferAttribute, BufferGeometry, Euler, Group, Quaternion, Vector3 } from "three";
import { createPmxRetarget } from "./pmx-retarget.js";
import { PMX_BASELINE_VERSION, normalizePoseOffsets } from "./pose-offsets.js";

const UNIT = 0.08;
const ZERO = [0, 0, 0];
const IDENTITY = [0, 0, 0, 1];
const LEFT = ["左足ＩＫ", "左つま先ＩＫ"];
const RIGHT = ["右足ＩＫ", "右つま先ＩＫ"];
const ikAliases = (name) => [name, name.replace("ＩＫ", "IK")];

// A standard MMD bone layout built in memory, with no model file or mesh
// required. Fit the source geometry at an A pose and use the source scale.
export function genericVmdRig(motion, offsets) {
  const mesh = new Group();
  const world = (j) => new Vector3().fromArray(motion.rest, j * 3);
  const hip = world(0);
  const restFloor = Number.isFinite(motion.restFloor) ? motion.restFloor : Math.min(world(10).y, world(11).y) - .02;
  const floor = Number.isFinite(motion.floor) ? motion.floor : 0;
  const ground = new Vector3(hip.x, restFloor, hip.z);
  const center = hip.clone().sub(ground);
  const point = (j) => world(j).sub(ground);
  const sideAxis = world(1).sub(world(2)).setY(0).normalize();
  const forward = new Vector3().crossVectors(sideAxis, new Vector3(0, 1, 0));
  if (world(10).sub(world(7)).dot(forward) < 0) forward.negate();
  mesh.scale.setScalar(UNIT);
  const nodes = [], bones = [], positions = [], weights = [], indices = [], iks = [];
  const add = (name, parent, at, movable = true) => {
    const bone = new Bone(), index = nodes.length;
    const bind = at.clone().divideScalar(UNIT);
    bone.name = name;
    bone.position.copy(bind);
    if (parent >= 0) bone.position.sub(new Vector3().fromArray(bones[parent].worldPosition).divideScalar(UNIT));
    (nodes[parent] ?? mesh).add(bone);
    nodes.push(bone);
    bones.push({ name, index, parent, position:bone.position.toArray(), quaternion:IDENTITY, scale:[1, 1, 1],
      worldPosition:at.toArray(), worldQuaternion:IDENTITY, pmx:{flags:movable ? 6 : 2, transformationClass:0} });
    return index;
  };
  const bind = add("センター", -1, center);
  const lower = add("下半身", bind, point(0));
  const spine = add("上半身", bind, point(3));
  // The second standard torso bone spans the remaining two SMPL-H joints.
  const chest = add("上半身2", spine, point(6));
  const neck = add("首", chest, point(12));
  add("頭", neck, point(15));
  function paint(bone, at) {
    positions.push(...at.clone().divideScalar(UNIT).toArray());
    indices.push(bone, 0, 0, 0);
    weights.push(1, 0, 0, 0);
  }
  function controller(name, parent, at, effector, links) {
    const target = add(name, parent, at);
    // The target is kept on a separate branch. A toe follows the foot handle.
    const definition = {target, effector, links:links.map((index) => ({index})), iteration:24, maxAngle:Math.PI / 4};
    bones[target].pmx.ik = definition;
    iks.push(definition);
  }
  for (const s of [0,1]) {
    const side = s ? "右" : "左", sign = s ? -1 : 1;
    const thigh = add(side + "足", lower, point(1+s));
    const knee = add(side + "ひざ", thigh, point(4+s));
    const ankle = add(side + "足首", knee, point(7+s));
    const toe = add(side + "つま先", ankle, point(10+s));
    const [footName, toeName] = s ? RIGHT : LEFT;
    if (!offsets?.retarget || offsets.retarget.ik?.[s ? "rightFoot" : "leftFoot"] >= 0) {
      const footControl = nodes.length;
      controller(footName, -1, point(7+s), ankle, [knee, thigh]);
      controller(toeName, footControl, point(10+s), toe, [ankle]);
    }
    // Nominal soles let the same contact-aware retargeter use the capture's
    // foot contact timing, without depending on a particular character's shoes.
    for (const shift of [-.03,-.01,.01,.03]) {
      paint(ankle, point(7+s).setY(0).addScaledVector(sideAxis,shift).addScaledVector(forward,-.04));
      paint(toe, point(10+s).setY(0).addScaledVector(sideAxis,shift).addScaledVector(forward,.04));
    }
    const shoulder = add(side + "肩", chest, point(13+s));
    const armRoot = point(16+s);
    const arm = add(side + "腕", shoulder, armRoot);
    const restArm = new Quaternion().setFromAxisAngle(forward, -sign * Math.PI * 32 / 180);
    const armPoint = (j) => point(j).sub(armRoot).applyQuaternion(restArm).add(armRoot);
    const elbow = add(side + "ひじ", arm, armPoint(18+s));
    const wrist = add(side + "手首", elbow, armPoint(20+s));
    for (const [digit, label] of ["人指","中指","小指","薬指","親指"].entries()) {
      let parent = wrist;
      for (const [segment, number] of Array.from(digit === 4 ? "０１２" : "１２３").entries()) {
        parent = add(side + label + number, parent, armPoint(22 + 15*s + 3*digit + segment));
      }
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  geometry.setAttribute("skinIndex", new BufferAttribute(new Uint16Array(indices), 4));
  geometry.setAttribute("skinWeight", new BufferAttribute(new Float32Array(weights), 4));
  geometry.userData.MMD = {iks};
  mesh.geometry = geometry;
  mesh.skeleton = {bones:nodes};
  mesh.updateMatrixWorld(true);
  const rest = {bones, unitScale:UNIT};
  // Reuse the numeric corrections in their semantic SMPL-H slots. A named
  // PMX bone, helper or custom IK chain has no identity on another model.
  const saved = normalizePoseOffsets(offsets);
  const correction = saved.retarget ?? saved.boneOffsets;
  const mapCorrections = (values, compose) => {
    const output = {...values};
    if (values?.[6] || values?.[9]) output[9] = compose(values[6] ?? ZERO, values[9] ?? ZERO);
    delete output[6];
    return output;
  };
  const combineAngles = (a, b) => {
    const from = (degrees) => new Quaternion().setFromEuler(new Euler(...degrees.map((angle) => angle * Math.PI / 180)));
    return new Euler().setFromQuaternion(from(a).multiply(from(b))).toArray().slice(0,3).map((angle) => angle * 180 / Math.PI);
  };
  const pose = { ...saved, retarget:null, pmxBaselineVersion:PMX_BASELINE_VERSION,
    ikMode:offsets?.ikMode === "ik" ? "ik" : "fk",
    rotations:mapCorrections(saved.rotations, combineAngles),
    boneOffsets:{rotations:mapCorrections(correction?.rotations, combineAngles),
      translations:mapCorrections(correction?.translations, (a,b) => a.map((x,i) => x+b[i]))} };
  try {
    const rig = createPmxRetarget(mesh, rest, {...motion, floor}, {poseOffsets:pose, independentIk:true});
    const aliases = [...LEFT, ...RIGHT].map((name) => name.replace("ＩＫ","IK"));
    const outputBones = bones.slice();
    const byName = new Map(outputBones.map(({name,index}) => [name,index]));
    const sourceTracks = rig.vmd.tracks;
    const extraTracks = [];
    for (const side of ["左","右"]) {
      const source = sourceTracks.findIndex(({name}) => name === side + "つま先");
      if (source >= 0) extraTracks.push({name:side+"足先EX", source});
    }
    for (const alias of aliases) {
      const source = sourceTracks.findIndex(({name}) => name === alias.replace("IK","ＩＫ"));
      if (source >= 0) extraTracks.push({name:alias, source});
    }
    for (const {name} of extraTracks) {
      byName.set(name, outputBones.length);
      outputBones.push({name, index:outputBones.length});
    }
    const ikNames = [...LEFT,...RIGHT].flatMap(ikAliases);
    for (const name of ikNames) if (!byName.has(name)) {
      byName.set(name, outputBones.length);
      outputBones.push({name, index:outputBones.length});
    }
    const qs = new Float32Array(sourceTracks.length * 4), ts = new Float32Array(sourceTracks.length * 3);
    return {
      rest:{ ...rest, bones:outputBones },
      dispose() { geometry.dispose(); },
      vmd:{
        tracks:[...sourceTracks,...extraTracks.map(({name}) => ({name,index:byName.get(name)}))],
        ikNames,
        ikEnabledNames:rig.vmd.ikEnabledNames.flatMap(ikAliases),
        rootBoneIndex:bind,
        sample(frame, rotations, translations, placement) {
          if (!rig.vmd.sample(frame, qs, ts, placement)) return false;
          rotations.set(qs); translations.set(ts);
          extraTracks.forEach(({source}, index) => {
            const dest = sourceTracks.length + index;
            rotations.set(qs.subarray(4*source, 4*source+4), 4*dest);
            translations.set(ts.subarray(3*source, 3*source+3), 3*dest);
          });
          return true;
        },
      },
    };
  } catch (error) {
    geometry.dispose();
    throw error;
  }
}
