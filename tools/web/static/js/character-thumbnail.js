import * as THREE from "three";
import { isolateShaderMaterials } from "./character-skeleton.js";

// Size and complexity are bounded by the existing character importer. Capture
// a clone so that a thumbnail never reparents the live character.
let renderer, scene, camera;
function initialize() {
  const options = { alpha: true, antialias: true, preserveDrawingBuffer: true };
  renderer = new THREE.WebGLRenderer(options);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setSize(256, 256, false);
  renderer.setClearColor(0x000000, 0);
  scene = new THREE.Scene();
  const key = new THREE.DirectionalLight(0xffffff, 2);
  key.position.set(1.4, 2.6, 2.8);
  const fill = new THREE.DirectionalLight(0xe4ecff, 1);
  fill.position.set(-2.4, 1.2, 1.8);
  scene.add(key, fill, new THREE.AmbientLight(0xffffff, 0.8));
  camera = new THREE.PerspectiveCamera(32, 1, 0.01, 1000);
}
export function renderCharacterThumbnail(asset) {
  if (!renderer) initialize();
  const root = asset.clone();
  let releaseMaterials;
  try {
    releaseMaterials=isolateShaderMaterials(root);
    scene.add(root);
    root.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(root);
    if (bounds.isEmpty()) throw new Error("Character has no mesh bounds");
    const center = bounds.getCenter(new THREE.Vector3());
    const span = bounds.getSize(new THREE.Vector3()).length();
    if (!Number.isFinite(span) || span <= 0) throw new Error("Invalid character bounds");
    camera.position.copy(center).addScaledVector(new THREE.Vector3(0.23, 0.03, 1).normalize(), span * 1.85);
    camera.near = span * 0.01;
    camera.far = span * 6;
    camera.lookAt(center);
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
    return new Promise((resolve, reject) => renderer.domElement.toBlob(
      (png) => png ? resolve(png) : reject(new Error("Cannot encode thumbnail")), "image/png"));
  } finally {
    root.removeFromParent();
    asset.releaseClone(root);
    releaseMaterials?.();
  }
}
