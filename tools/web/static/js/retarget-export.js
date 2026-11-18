import { AnimationClip, Group, InterpolateDiscrete, MeshStandardMaterial, QuaternionKeyframeTrack, VectorKeyframeTrack } from "three";
import { createFbxCharacterRetarget } from "./fbx-character-retarget.js";
import { GLTFExporter } from "../vendor/three/vrm/GLTFExporter.js";
import { appError } from "./i18n.js";

// Bake the same target transforms evaluated in the viewport, including
// positional offsets and the assigned IK controller tracks. Use independent
// clones so exports are unaffected by the playhead.
export async function bakeRetargetGlb(actors, stem, signal) {
  if (!actors.length) throw appError("retarget.export_failed");
  const scene=new Group(), clips=[], applications=[];
  const materials=new Map();
  const maxComponents=16_000_000;
  let allocated=0;
  try {
    for (const actor of actors) {
      signal?.throwIfAborted();
      const { asset, motion, poseOffsets, offset, yaw, start, frames, fps }=actor;
      if (!Number.isInteger(start) || start < 0 || !Number.isInteger(frames) || frames < start+motion.frames ||
          !Number.isFinite(fps) || fps <= 0) throw appError("retarget.export_failed");
      const root=asset.clone();
      applications.push([asset,root]);
      const placement=new Group();
      placement.position.fromArray(offset);
      placement.rotation.y=yaw;
      placement.add(root);scene.add(placement);
      const rig=createFbxCharacterRetarget(root,asset,motion,{poseOffsets});
      const targets=asset.getRig(root);
      const chosen=new Set([root, ...(targets.manualBones ?? targets.bones)]);
      root.traverse((node)=>{ if(node.isBone)chosen.add(node); });
      if (asset.kind === "vrm") root.traverse((node)=>{
        if (!node.isMesh) return;
        // glTF cannot store a VRM shader program. Preserve its base color,
        // texture and alpha on an export-only standard material.
        const exportMaterial=(material)=>{
          if (!material?.isShaderMaterial) return material;
          if(!materials.has(material))materials.set(material,new MeshStandardMaterial({
            name:material.name,color:material.color??0xffffff,map:material.map??null,
            opacity:material.opacity,transparent:material.transparent,alphaTest:material.alphaTest,
            side:material.side,emissive:material.emissive??0x000000,emissiveMap:material.emissiveMap??null,
            normalMap:material.normalMap??null,roughness:.9,metalness:0,
          }));
          return materials.get(material);
        };
        node.material=Array.isArray(node.material)?node.material.map(exportMaterial):exportMaterial(node.material);
      });
      const nodes=[...chosen];
      const count=frames*nodes.length*7;
      if (allocated + count > maxComponents) throw appError("retarget.export_limit");
      allocated+=count;
      const time=Float32Array.from({length:frames},(_,i)=>i/fps);
      const data=nodes.map((node)=>({node, position:new Float32Array(frames*3), rotation:new Float32Array(frames*4)}));
      const visibilityFrames = Array.from(new Set([0,Math.max(0,start),
        Math.min(frames-1,start+motion.frames),frames-1])).sort((a,b)=>a-b);
      // glTF has no visibility track. Scale to zero at the same entry/exit
      // frames for rigs that are not active throughout a group shot.
      const scale=new VectorKeyframeTrack(placement.uuid+".scale",visibilityFrames.map((f)=>f/fps),
        visibilityFrames.flatMap((f)=>Array(3).fill(f>=start && f<start+motion.frames ? 1 : 0)),InterpolateDiscrete);
      const last=motion.frames-1;
      let nextYield=performance.now()+25;
      for(let f=0;f<frames;f++){
        const local=Math.max(0,Math.min(last,f-start));
        if(!rig.apply(local))throw appError("retarget.export_failed");
        for(const {node,position,rotation} of data){
          node.position.toArray(position,f*3);
          const q=node.quaternion, at=f*4;
          const sign=f>0 && (q.x*rotation[at-4]+q.y*rotation[at-3]+q.z*rotation[at-2]+q.w*rotation[at-1])<0 ? -1 : 1;
          rotation[at]=q.x*sign;rotation[at+1]=q.y*sign;rotation[at+2]=q.z*sign;rotation[at+3]=q.w*sign;
        }
        if(performance.now()>=nextYield){
          await new Promise((resume)=>setTimeout(resume,0));
          signal?.throwIfAborted();nextYield=performance.now()+25;
        }
      }
      rig.apply(0);
      clips.push(scale,...data.flatMap(({node,position,rotation})=>[
        new VectorKeyframeTrack(node.uuid+".position",time,position).optimize(),
        new QuaternionKeyframeTrack(node.uuid+".quaternion",time,rotation).optimize(),
      ]));
    }
    signal?.throwIfAborted();
    const result=await new GLTFExporter().parseAsync(scene,{
      animations:[new AnimationClip("FlowHMR",-1,clips)],binary:true,onlyVisible:true,maxTextureSize:4096,
    });
    signal?.throwIfAborted();
    return {blob:new Blob([result],{type:"model/gltf-binary"}),filename:`${stem}_retarget.glb`};
  } finally {
    for(const material of materials.values())material.dispose();
    for(const [asset,root] of applications)asset.releaseClone(root);
  }
}
