import { AmbientLight, DirectionalLight, Euler, Group, MathUtils, Matrix4, Quaternion, Scene,
  SRGBColorSpace, Vector3, WebGLRenderer } from "three";
import { makePmxNativeIk } from "./pmx-native-ik.js";
import { isolateShaderMaterials } from "./character-skeleton.js";

function pmxFollowers(mesh, data) {
  const nodes=mesh.skeleton.bones, parents=data.map((b)=>b.parent);
  const counts=new Map();
  for (const b of data) counts.set(b.name,(counts.get(b.name)??0)+1);
  const ik=makePmxNativeIk(mesh,data,parents,new Set(),-1,counts);
  const binds=data.map((b)=>new Quaternion().fromArray(b.quaternion));
  const world=data.map((b)=>new Quaternion().fromArray(b.worldQuaternion));
  const grants=data.map(({pmx},i)=>{
    const g=pmx?.grant, driver=g?.parentIndex;
    if (!g?.affectRotation || g.isLocal || !Number.isFinite(g.ratio) || !world[driver] || driver===i) return null;
    const basis=world[i].clone().invert().multiply(world[driver]);
    return {driver, ratio:g.ratio, basis, inverse:basis.clone().invert()};
  });
  const pending=new Uint8Array(nodes.length), delta=new Quaternion(), mix=new Quaternion();
  function follow(i) {
    if (pending[i]) return;
    pending[i]=1;
    const grant=grants[i];
    if (grant) {
      const {driver,ratio,basis,inverse}=grant;
      if(pending[driver]!==1) {
        follow(driver);
        delta.copy(binds[driver]).invert().multiply(nodes[driver].quaternion);
        mix.identity().slerp(delta,ratio);
        nodes[i].quaternion.multiply(delta.copy(basis).multiply(mix).multiply(inverse)).normalize();
      }
    }
    pending[i]=2;
  }
  return (id, mode)=>{
    if (mode==="move" && data[id].pmx?.ik) ik.solve(id);
    pending.fill(0);
    for(let n=0;n<grants.length;n++)follow(n);
  };
}

// This rig is only used in the mapping pane. It never becomes an actor or
// reaches the exporter. Bone inspection does not need a second GL context.
export function createBoneInspection(asset, surface) {
  const scene=new Scene(), coordinates=new Group(), matrix=new Matrix4();
  coordinates.matrixAutoUpdate=false;
  scene.add(coordinates);
  const key=new DirectionalLight(0xffffff,1.65), fill=new DirectionalLight(0xd8e9ff,.65);
  key.position.set(1.4,2.6,3); fill.position.set(-2,1.2,2);
  scene.add(key,fill,new AmbientLight(0xffffff,.6));
  const mesh=asset.clone();
  coordinates.add(mesh);
  const rig=asset.kind==="pmx" ? null : asset.getRig(mesh);
  const bones=rig ? rig.manualBones??rig.bones : mesh.skeleton.bones;
  const base=[];
  mesh.traverse((node)=>{
    base.push({node, p:node.position.clone(), q:node.quaternion.clone(), s:node.scale.clone()});
  });
  let renderer, releaseMaterials;
  function openSkin() {
    const view=new WebGLRenderer({alpha:true,antialias:true,powerPreference:"low-power"});
    view.setClearColor(0x000000,0);
    view.outputColorSpace=SRGBColorSpace;
    releaseMaterials=isolateShaderMaterials(mesh);
    mesh.traverse((node)=>{
      if(!node.isMesh)return;
      node.castShadow=node.receiveShadow=false;
    });
    surface.append(view.domElement);
    return view;
  }
  const follow=rig ? ()=>rig.updateConstraints?.() : pmxFollowers(mesh,asset.rest.bones);
  const point=new Vector3(), shift=new Vector3(), parentRotation=new Quaternion(), cameraRotation=new Quaternion();
  const spin=new Quaternion(), euler=new Euler(0,0,0,"YXZ");
  const worldPositions=bones.map(()=>new Vector3()), originPositions=bones.map(()=>new Vector3()), affected=new Set();
  let selection=null, width=0, height=0;

  function reset() {
    for(const {node,p,q,s} of base) {
      node.position.copy(p);node.quaternion.copy(q);node.scale.copy(s);
      if(!node.matrixAutoUpdate)node.updateMatrix();
    }
    scene.updateMatrixWorld(true);
  }
  function calibrate(node, correction) {
    if(!correction)return;
    if(correction.rotation.some(Boolean)) {
      const [x,y,z]=correction.rotation.map(MathUtils.degToRad);
      node.quaternion.multiply(spin.setFromEuler(euler.set(x,y,z,"XYZ"))).normalize();
    }
    if(correction.translation.some(Boolean)) {
      const scale=node.parent.getWorldScale(shift);
      const value=correction.translation;
      node.position.x+=value[0]/(Math.abs(scale.x)||1);
      node.position.y+=value[1]/(Math.abs(scale.y)||1);
      node.position.z+=value[2]/(Math.abs(scale.z)||1);
    }
    node.updateMatrix();
  }
  function updatePositions(id) {
    scene.updateMatrixWorld(true);
    collect(worldPositions);
    affected.clear();
    for(let i=0;i<bones.length;i++) if(i===id || worldPositions[i].distanceToSquared(originPositions[i])>1e-10) affected.add(i);
  }
  function collect(points) {
    for (let i=0;i<bones.length;i++) points[i].setFromMatrixPosition(bones[i].matrixWorld);
  }
  return {
    align(basis, origin) {
      matrix.set(...basis.flatMap((axis)=>[...axis.toArray(),-axis.dot(origin)]),0,0,0,1);
      coordinates.matrix.copy(matrix);
      coordinates.matrixWorldNeedsUpdate=true;
    },
    begin(id, mode, correction=null) {
      const node=bones[id];
      if(!node)return false;
      reset();collect(originPositions);
      selection={id,mode,node,correction};
      if(correction) {
        calibrate(node,correction);
        follow(id,mode);
      }
      updatePositions(id);
      return true;
    },
    drag(x,y,camera,metresPerPixel) {
      if(!selection)return;
      const {node,mode,id,correction}=selection;
      reset();
      calibrate(node,correction);
      camera.getWorldQuaternion(cameraRotation);
      if(mode==="move") {
        // Translations are in the plane of the current camera, then expressed
        // in the bone parent's real units (PMX units, FBX scale or metres).
        shift.set(x*metresPerPixel,-y*metresPerPixel,0).applyQuaternion(cameraRotation)
          .add(node.getWorldPosition(point));
        node.position.copy(node.parent.worldToLocal(shift));
      } else if(mode==="rotate") {
        node.parent.getWorldQuaternion(parentRotation).invert();
        parentRotation.multiply(cameraRotation);
        spin.setFromEuler(euler.set(y*.012,x*.012,0,"YXZ")).premultiply(parentRotation).multiply(parentRotation.invert());
        node.quaternion.premultiply(spin).normalize();
      } else node.scale.multiplyScalar(MathUtils.clamp(Math.exp((x-y)/125),.25,4));
      node.updateMatrix();
      follow(id,mode);
      updatePositions(id);
    },
    pose() { return selection ? {positions:worldPositions, affected} : null; },
    drawSkin(camera,w,h,ratio) {
      renderer ??= openSkin();
      const nextWidth=Math.max(1,Math.round(w*ratio)),nextHeight=Math.max(1,Math.round(h*ratio));
      if(width!==nextWidth || height!==nextHeight) {
        width=nextWidth;height=nextHeight;
        renderer.setSize(width,height,false);
      }
      renderer.render(scene,camera);
    },
    end() {
      if(selection)reset();
      selection=null;
    },
    dispose() {
      asset.releaseClone(mesh);
      releaseMaterials?.();
      if(renderer) {
        renderer.domElement.remove();
        renderer.dispose();renderer.forceContextLoss();
      }
    },
  };
}
