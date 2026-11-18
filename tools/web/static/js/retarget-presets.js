import { appError } from "./i18n.js";
import { normalizePoseOffsets } from "./pose-offsets.js";
import { BODY_REQUIRED, CHAINS, normalizeRetargetProfile, profileIssue } from "./retarget-profile.js";

const ikSlots = Object.keys(CHAINS);
const label = (value) => Array.from(value.trim().replace(/\s+/gu, " ")).slice(0, 80).join("");
const keys = (values) => Object.fromEntries(ikSlots.map((slot) => [slot, values[slot]]));
const nameKey = (name) => name.normalize("NFKC").split(/[:|]/u).at(-1).trim().toLowerCase();

export const builtinPresetKinds = Object.freeze({ pmx:"pmx", vrm:"vrm", mixamo:"fbx", metahuman:"fbx" });
export const builtinPresetName = Object.freeze({ pmx:"PMX", vrm:"VRM", mixamo:"Mixamo", metahuman:"MetaHuman" });

export function builtinFamily(asset) {
  const family=asset.kind==="pmx" ? "pmx" : asset.profile;
  return Object.hasOwn(builtinPresetKinds,family) ? family : null;
}

function stockOffsets(family, nativeIk) {
  return normalizePoseOffsets({
    ikMode:family==="pmx" && nativeIk ? "ik" : "fk",
    mirror:true,
    pmxBaselineVersion:family==="pmx" ? 1 : undefined,
    vrmBaselineVersion:family==="vrm" ? 1 : undefined,
  });
}

// Ignore bone indices, mesh names and proportions. Keep the full bone
// ancestry so identical names on different branches cannot be interchanged.
export function skeletonPaths(bones) {
  const paths = new Array(bones.length), visiting = new Uint8Array(bones.length);
  // Valid imported skeletons are acyclic. Iterating parents first also
  // avoids recursion proportional to the size of a long auxiliary chain.
  for (let i = 0; i < bones.length; i++) {
    const chain = [];
    let j = i;
    while (bones[j] && paths[j] === undefined && !visiting[j]) {
      visiting[j] = 1; chain.push(j); j = bones[j].parent;
    }
    let prefix = j < 0 ? "" : paths[j] ?? null;
    while (chain.length) {
      const k = chain.pop();
      const part = nameKey(bones[k].name).replace(/[%/]/gu, (c) => c === "%" ? "%25" : "%2f");
      const path = prefix !== null && part && !/[\x00-\x1f\x7f]/u.test(part) ? prefix + "/" + part : null;
      prefix = paths[k] = path && path.length <= 384 ? path : null;
      visiting[k]=0;
    }
  }
  const counts = new Map();
  paths.forEach((path) => { if(path) counts.set(path,(counts.get(path) ?? 0)+1); });
  const unique = new Map();
  // Even a uniquely named child below two identically named siblings is
  // ambiguous across variants: either sibling may have acquired that child.
  const ambiguous = new Set();
  paths.filter(Boolean).sort((a,b)=>a.length-b.length).forEach((path)=>{
    if(counts.get(path)>1 || ambiguous.has(path.slice(0,path.lastIndexOf("/"))))ambiguous.add(path);
  });
  paths.forEach((path, i) => { if (path && !ambiguous.has(path)) unique.set(path,i); });
  return { paths, unique };
}

function encoding(profile, skeleton) {
  const pathFor = (index) => {
    if (index < 0) return null;
    const path = skeleton.paths[index];
    if (!path || skeleton.unique.get(path) !== index) throw appError("retarget.preset_ambiguous");
    return path;
  };
  return { links:profile.map.map(pathFor), root:pathFor(profile.root),
    ik:keys(Object.fromEntries(Object.entries(profile.ik).map(([key, i]) => [key, pathFor(i)]))) };
}

export function canSavePreset(profile, skeleton) {
  try { encoding(profile, skeleton); return true; }
  catch { return false; }
}

export function makeRetargetPreset({ name, kind, builtin, profile, offsets, skeleton, previous }) {
  name = label(name);
  if (!name) throw appError("retarget.preset_name_needed");
  const binding = encoding(profile, skeleton);
  if (builtin === "pmx") binding.root = null;
  const pose = normalizePoseOffsets({ ...offsets, boneOffsets: profile, retarget: null });
  // Updating a preset is explicit. A different name creates a separate one.
  const id = previous?.name === name ? previous.id :
    Array.from(crypto.getRandomValues(new Uint8Array(32)), (v) => v.toString(16).padStart(2, "0")).join("");
  return { version:1, id, name, kind, builtin:builtin ?? null, ...binding, offsets:pose };
}

export function reusablePresets(records, { kind, bones, skeleton, builtin, native }) {
  const results = [];
  for (const preset of Object.values(records ?? {})) {
    if (preset?.version !== 1 || preset.kind !== kind || !Array.isArray(preset.links) || preset.links.length !== 52 ||
        !preset.ik || !preset.offsets || (preset.builtin && preset.builtin !== builtin)) continue;
    const find = (path) => path == null ? -1 : skeleton.unique.get(path) ?? -1;
    // A stock PMX preset recorded the automatic root when it was saved.
    // Follow the current default instead of invalidating older presets.
    const automaticPmxRoot = preset.builtin === "pmx";
    const map = preset.links.map(find), root = automaticPmxRoot ? (native?.root ?? -1) : find(preset.root);
    const ik = keys(Object.fromEntries(ikSlots.map((key) => [key, find(preset.ik[key])])));
    const edits=preset.offsets.boneOffsets;
    const edited = new Set([...Object.keys(edits?.rotations ?? {}),...Object.keys(edits?.translations ?? {})].map(Number));
    const available = (path) => !path || skeleton.unique.has(path);
    if (BODY_REQUIRED.some((j) => map[j] < 0) || [...edited].some((j)=>map[j]<0) ||
        (!automaticPmxRoot && !available(preset.root)) || ikSlots.some((key)=>!available(preset.ik[key])) ||
        (preset.builtin && (!native || root !== native.root ||
          map.some((i, j) => i >= 0 && i !== native.map[j]) || ikSlots.some((key) => ik[key] !== native.ik[key])))) continue;
    const profile = normalizeRetargetProfile({ ...edits, version:1, map, root, ik });
    if ((!preset.builtin && profileIssue(profile, bones, kind)) ||
        Object.keys(profile.translations).some((j) => !bones[map[j]]?.movable)) continue;
    const bound = preset.builtin ? { ...profile, map:native.map } : profile;
    results.push({ preset, profile:bound, offsets: normalizePoseOffsets({ ...preset.offsets,
      boneOffsets:preset.builtin ? bound : null, retarget:preset.builtin ? null : bound }) });
  }
  return results.sort((a,b) => a.preset.name.localeCompare(b.preset.name) || a.preset.id.localeCompare(b.preset.id));
}

// Stock profiles already describe semantic joints. Store their corrections
// without model-specific indices. A reassigned bone/IK chain additionally
// needs a portable binding, checked against the actual target when loaded.
export function makeBuiltinOverride(family, draft, offsets, skeleton) {
  const pose=normalizePoseOffsets({ ...offsets,retarget:null,boneOffsets:draft });
  if (!offsets.retarget) return { id:family,version:1,offsets:pose };
  const binding=makeRetargetPreset({ name:builtinPresetName[family],kind:builtinPresetKinds[family],
    builtin:null,profile:draft,offsets,skeleton });
  return { id:family,version:1,offsets:pose,binding };
}

export function builtinPresetChoice(family, metadata, override) {
  if (family!==metadata.builtin || !metadata.native) return null;
  const factory=stockOffsets(family,metadata.nativeIk);
  const stock=(offsets)=>({
    profile:normalizeRetargetProfile({
      ...metadata.native,rotations:offsets.boneOffsets?.rotations,translations:offsets.boneOffsets?.translations,
    }),
    offsets,
    useNative:true,
  });
  if (!override) return stock(factory);
  if (override.binding) {
    const matched=reusablePresets({binding:override.binding},metadata)[0];
    if (matched) return { profile:matched.profile,offsets:matched.offsets,useNative:false };
    return { ...stock(factory), skippedOverride:true };
  }
  const offsets=normalizePoseOffsets({ ...factory,...override.offsets,retarget:null });
  // Different models in the same family can omit optional fingers or
  // disallow translations on a standard bone.
  const corrections=offsets.boneOffsets;
  if(corrections) {
    for(const j of Object.keys(corrections.rotations)) {
      if(metadata.native.map[j]<0)delete corrections.rotations[j];
    }
    for(const j of Object.keys(corrections.translations)) {
      if(!metadata.bones[metadata.native.map[j]]?.movable)delete corrections.translations[j];
    }
  }
  return stock(offsets);
}
