import { Skeleton } from "three";

// Shader materials store light and shadow uniforms on the material itself.
// Give auxiliary renderers their own uniforms while sharing texture assets.
export function isolateShaderMaterials(root) {
  const copies = new Map();
  function use(material) {
    if (!material?.isShaderMaterial) return material;
    if (!copies.has(material)) {
      const clone = material.clone();
      for (const [name, { value }] of Object.entries(material.uniforms)) {
        if (value?.isTexture && clone.uniforms[name]) clone.uniforms[name].value = value;
      }
      copies.set(material, clone);
    }
    return copies.get(material);
  }
  root.traverse((node) => {
    if (node.isMesh) node.material = Array.isArray(node.material) ? node.material.map(use) : use(node.material);
  });
  return () => { for (const material of copies.values()) material.dispose(); };
}

export function cloneSkinnedHierarchy(source, isSkinned = (node) => node.isSkinnedMesh) {
  const root = source.clone(true);
  const copies = new Map();
  const pair = (original, copy) => {
    copies.set(original, copy);
    original.children.forEach((child, index) => pair(child, copy.children[index]));
  };
  pair(source, root);
  const skeletons = new Map();
  for (const [original, copy] of copies) {
    if (!isSkinned(original)) continue;
    if (!skeletons.has(original.skeleton)) {
      skeletons.set(original.skeleton, new Skeleton(
        original.skeleton.bones.map((bone) => copies.get(bone)),
        original.skeleton.boneInverses.map((matrix) => matrix.clone()),
      ));
    }
    copy.bind(skeletons.get(original.skeleton), original.bindMatrix);
  }
  root.updateMatrixWorld(true);
  return { root, copies, skeletons };
}
