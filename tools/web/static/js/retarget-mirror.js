import { Euler, Quaternion, Vector3 } from "three";
import { CHAINS, descends, jointFitsChain } from "./retarget-profile.js";

const SIDES = [[1,2],[4,5],[7,8],[10,11],[13,14],[16,17],[18,19],[20,21]];
const FINGERS = Array.from({length:15},(_,i)=>[22+i,37+i]);
const MIRROR_ORDER = [...SIDES.slice(4),...SIDES.slice(0,4),...FINGERS];
export const oppositeJoint = (joint) => {
  if (joint >= 22 && joint < 52) return joint < 37 ? joint+15 : joint-15;
  const pair=SIDES.find((ids)=>ids.includes(joint));
  return pair ? (pair[0]===joint?pair[1]:pair[0]) : undefined;
};
const leftJoints = new Set([...SIDES.map(([j])=>j), ...Array.from({length:15},(_,i)=>22+i)]);
const degree = Math.PI / 180;
const Y = new Vector3(0,1,0);
const targetSideNames=(name,toLeft)=>{
  const a=name.normalize("NFKC").toLowerCase(), result=new Set();
  const from=toLeft?"right":"left", to=toLeft?"left":"right", start=from[0], end=to[0];
  if (a.includes(from)) result.add(a.replaceAll(from,to));
  if (a.includes(toLeft?"右":"左")) result.add(a.replaceAll(toLeft?"右":"左",toLeft?"左":"右"));
  const delimited=new RegExp("(^|[ ._:-])"+start+"(?=$|[ ._:-])","g");
  const tokens=a.replace(delimited,"$1"+end);
  if (a!==tokens) result.add(tokens);
  return [...result];
};

// Reflect through the model's sagittal plane, fitted from known paired joints.
// A nearest neighbor is accepted only when separated from alternatives.
export function makeRetargetMirror(bones, suggested, kind, currentSuggestions = () => suggested) {
  const points=bones.map((b)=>new Vector3().fromArray(b.position));
  const height=Math.max(.1,points.reduce((v,p)=>Math.max(v,p.y),-Infinity)-points.reduce((v,p)=>Math.min(v,p.y),Infinity));
  const labels=bones.map((b)=>[b.name,b.nameEn].filter(Boolean).map((name)=>name.normalize("NFKC").toLowerCase()));
  const matchesOtherSide=(id,toLeft)=>{
    const named=new Set(labels[id].flatMap((name)=>targetSideNames(name,toLeft)));
    return (index)=>labels[index].some((name)=>named.has(name));
  };
  function plane(map, skip) {
    const normal = new Vector3(), center=new Vector3();
    let count=0;
    const ref=(j) => j!==skip ? points[map[j]] : null;
    for (const [l,r] of [[16,17],[1,2],[18,19],[20,21],[4,5],[7,8]]) {
      const left=ref(l) || points[suggested[l]], right=ref(r) || points[suggested[r]];
      if (!left || !right || left===right) continue;
      const delta=left.clone().sub(right).setY(0);
      if (delta.length()<height*.025 || (normal.lengthSq() && normal.dot(delta)<0)) continue;
      normal.add(delta.normalize());
      center.add(left).add(right);
      count++;
    }
    if(count) {
      normal.addScaledVector(Y,-normal.dot(Y)).normalize();
      return {axis:normal, origin:center.multiplyScalar(.5/count), estimated:false};
    }
    // With only one side mapped, use the known midline in preview space.
    // Treat this plane as provisional and require a close mutual match.
    const midline=[0,3,6,9,12,15].map((id)=>ref(id) ?? points[suggested[id]]).filter(Boolean);
    if(!midline.length)return null;
    const xs=midline.map((p)=>p.x).sort((a,b)=>a-b);
    if(xs.at(-1)-xs[0]>height*.045)return null;
    const mid=(xs[(xs.length-1)>>1]+xs[xs.length>>1])*.5;
    return {axis:new Vector3(1,0,0), origin:new Vector3(mid,0,0), estimated:true};
  }
  function counterpart(joint, id, profile) {
    const other=oppositeJoint(joint);
    if (other==null || !bones[id]) return -1;
    const side=leftJoints.has(other);
    const used=new Set([...profile.map,profile.root,...Object.values(profile.ik)]);
    const jointMap=[...profile.map];
    const allowed=(i)=>{
      if(i===id || used.has(i) || (profile.root>=0 && !descends(bones,i,profile.root)))return false;
      if(other>=22 && !descends(bones,i,jointMap[other<37?20:21]))return false;
      jointMap[other]=i;
      return jointFitsChain({map:jointMap},bones,kind,other);
    };
    const named=matchesOtherSide(id,side);
    const byName=labels.flatMap((_,i)=>named(i)&&allowed(i)?[i]:[]);
    const fitted=plane(profile.map,other);
    const sideSign=side?1:-1;
    const validSide = (i) => !fitted || fitted.estimated ||
      points[i].clone().sub(fitted.origin).dot(fitted.axis)*sideSign>height*.012;
    const matches=byName.filter(validSide);
    if (matches.length===1) return matches[0];
    if (matches.length>1) return -1;
    const proposals=joint>=22 ? currentSuggestions(profile.map) : suggested;
    if (proposals[joint]===id && proposals[other]>=0 && allowed(proposals[other]) && validSide(proposals[other])) return proposals[other];
    if (!fitted) return -1;
    const {axis,origin,estimated}=fitted;
    const end=points[id];
    const sideDistance=end.clone().sub(origin).dot(axis);
    if (Math.abs(sideDistance)<height*.018 || !estimated && sideDistance*sideSign>=0) return -1;
    const targetSign=-Math.sign(sideDistance);
    const reflect=end.clone().addScaledVector(axis,-2*sideDistance);
    const wrist=joint>=22? (side?20:21) : null;
    const tolerance=height*(estimated ? wrist==null?.035:.018 : wrist==null?.08:.025);
    const candidates=[];
    for (const [i,p] of points.entries()) {
      const distance=p.distanceTo(reflect);
      if (distance<=tolerance && p.clone().sub(origin).dot(axis)*targetSign>height*.018 && allowed(i)) {
        candidates.push([i,distance]);
      }
    }
    candidates.sort((a,b)=>a[1]-b[1]);
    if(!candidates.length || candidates[1] && candidates[0][1]+height*.004>=candidates[1][1])return -1;
    const [result,distance]=candidates[0];
    if(estimated) {
      const p=points[result];
      const back=p.clone().addScaledVector(axis,-2*p.clone().sub(origin).dot(axis));
      // On a provisional plane, reject ambiguous twist or helper bones
      // sitting nearly on top of the reflected pair.
      if(points.some((point,i)=>i!==id && point.distanceTo(back)<=distance+height*.004))return -1;
    }
    return result;
  }
  function ikCounterpart(slot,id,profile) {
    if (!bones[id]) return -1;
    const left=slot.startsWith("left");
    const other=(left?"right":"left")+slot.slice(left?4:5);
    const end=profile.map[CHAINS[other][2]], start=profile.map[CHAINS[other][0]];
    if (end<0 || start<0) return -1;
    const used=new Set([...profile.map,profile.root,...Object.values(profile.ik)]);
    const valid=(b,i)=>!used.has(i) && b.movable && !descends(bones,i,start) &&
      profile.map.every((index)=>!descends(bones,index,i)) &&
      (kind==="pmx" || profile.root<0 || descends(bones,i,profile.root)) &&
      (kind!=="pmx" || b.ikEffector===end);
    const named=matchesOtherSide(id,!left);
    const matches=bones.flatMap((b,i)=>valid(b,i)&&named(i)?[i]:[]);
    if (matches.length===1) return matches[0];
    if (kind==="pmx") {
      const usable=bones.flatMap((b,i)=>valid(b,i)?[i]:[]);
      if (usable.length===1) return usable[0];
    }
    return -1;
  }

  function reflectEdit(joint, to, values, isRotation, map) {
    const a=bones[map[joint]], b=bones[map[to]];
    const field=isRotation?"orientation":"translationOrientation";
    if (!a?.[field] || !b?.[field] || (!isRotation && !b.movable)) return null;
    const fitted=plane(map);
    const sides=leftJoints.has(joint)?[a,b]:[b,a];
    const axis=fitted && !fitted.estimated ? fitted.axis :
      new Vector3().fromArray(sides[0].position).sub(new Vector3().fromArray(sides[1].position)).setY(0).normalize();
    if (axis.lengthSq()<1e-8) return null;
    const from = new Quaternion().fromArray(a[field]).normalize();
    const into = new Quaternion().fromArray(b[field]).normalize();
    if (!isRotation) {
      const point=new Vector3().fromArray(values).applyQuaternion(from);
      point.addScaledVector(axis,-2*point.dot(axis)).applyQuaternion(into.invert());
      return point.toArray().map((x)=>Number(x.toFixed(6)));
    }
    // A quaternion's vector part is an axial vector. Reflect it in model
    // space, then convert it back through the opposite bone's rest axes.
    const euler=new Euler(...values.map((n)=>n*degree),"XYZ");
    const q=from.clone().multiply(new Quaternion().setFromEuler(euler)).multiply(from.clone().invert());
    const n=new Vector3(q.x,q.y,q.z);
    const mirrored=axis.clone().multiplyScalar(2*n.dot(axis)).sub(n);
    q.set(mirrored.x,mirrored.y,mirrored.z,q.w);
    euler.setFromQuaternion(into.clone().invert().multiply(q).multiply(into).normalize(),"XYZ");
    const main=[euler.x,euler.y,euler.z].map((a)=>a/degree);
    const wrap=(angle)=>(angle+540)%360-180, alternate=[wrap(main[0]+180),wrap(180-main[1]),wrap(main[2]+180)];
    const norm=(v)=>v.reduce((sum,n)=>sum+n*n,0);
    return (norm(main)<=norm(alternate)?main:alternate).map((a)=>Number(a.toFixed(4)));
  }

  return {counterpart, ikCounterpart, reflectEdit};
}

// Finish wrist and ankle chains first. A second pass can use new paired
// joints to resolve earlier suggestions, without overwriting manual bindings.
export function fillMirroredBindings(mirror,profile,preferred=null,excluded=new Set()) {
  const filled=[];
  function joint(from,to) {
    const id=profile.map[from];
    if(id<0 || profile.map[to]>=0 || excluded.has(to))return;
    const index=mirror.counterpart(from,id,profile);
    if(index<0)return;
    profile.map[to]=index;
    for(const [group,rotate] of [["rotations",true],["translations",false]]) {
      const values=profile[group][from];
      const offset=values && mirror.reflectEdit(from,to,values,rotate,profile.map);
      if(offset?.some(Boolean))profile[group][to]=offset;
    }
    filled.push({type:"joint",to,index});
  }
  if(typeof preferred?.joint==="number") {
    const other=oppositeJoint(preferred.joint);
    if(other!=null)joint(preferred.joint,other);
  }
  for(let pass=0;pass<2;pass++) {
    const old=filled.length;
    for(const [left,right] of MIRROR_ORDER) {joint(left,right);joint(right,left);}
    if(filled.length===old)break;
  }
  for(const [left,right] of [["leftFoot","rightFoot"],["leftHand","rightHand"]]) {
    const from=profile.ik[left]>=0?left:right,to=from===left?right:left;
    if(profile.ik[from]<0 || profile.ik[to]>=0 || excluded.has(to))continue;
    const index=mirror.ikCounterpart(from,profile.ik[from],profile);
    if(index>=0) {
      profile.ik[to]=index;
      filled.push({type:"ik",to,index});
    }
  }
  return filled;
}
