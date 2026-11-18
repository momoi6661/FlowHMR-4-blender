import { jointFitsChain } from "./retarget-profile.js";

// Suggestions only. The original stock rig mappings keep their own rules.
const BODY = [
  [0, [["hips","pelvis","骨盤","骨盆","下半身"],["hip","下体","下體"],["cog","腰"]]],
  [3, [["spine","spine0","spine01","上半身","上半身0","腰椎"],["waist","abdomen","腹","腰"],["spine1","上半身1"]]],
  [6, [["spine02","midspine","spine1","上半身1"],["spine2","上半身2"]]],
  [9, [["upperchest","上半身3"],["chest","胸部","胸"],["spine03","spine3","spine2","上半身2"]]],
  [12,[["neck","neck01","首","頸","頚","頸部","颈部"]]],
  [15,[["head","head01","頭","头","頭部","头部"]]],
];
const LIMBS = [
  [13,[["clavicle","鎖骨","锁骨"],["shoulder","肩"]]],
  [16,[["upperarm","上腕","大臂"],["arm","腕"]]],
  [18,[["forearm","lowerarm","前腕","小臂"],["elbow","ひじ","肘"]]],
  [20,[["wrist","手首","手腕"],["hand"]]],
  [1, [["thigh","upperleg","upleg","大腿"],["足","足d","leg"],["hip"]]],
  [4, [["calf","shin","lowerleg","小腿"],["knee","ひざ","膝","ひざd","膝d"],["leg"]]],
  [7, [["ankle","足首","足首d","脚踝"],["foot"]]],
  [10,[["toebase","ball","足先ex"],["toe","toes","つま先","足先","足指","腳尖","脚尖"]]],
];
const DIGITS = [
  ["index","indexfinger","forefinger","fore","pointer","人指","人差指","人差し指","示指","食指"],
  ["middle","middlefinger","midfinger","中指"],
  ["pinky","pinkie","pinkyfinger","little","littlefinger","smallfinger","小指"],
  ["ring","ringfinger","薬指","くすり指","無名指","无名指"],
  ["thumb","親指","おやゆび","拇指","母指"],
];
const WRIST = /^(?:hand|wrist|handwrist|wristjoint|handbone|手首|手関節|手关节|手腕|腕関節|腕关节|掌)$/u;
const SIDE = new Map([["left",0],["l",0],["左",0],["right",1],["r",1],["右",1]]);
const SEGMENTS = new Map([
  ["a","first"],["b","middle"],["c","last"],
  ["proximal","first"],["prox","first"],["base","first"],["基節","first"],["基节","first"],
  ["intermediate","middle"],["medial","middle"],["inter","middle"],["中節","middle"],["中节","middle"],
  ["distal","last"],["dist","last"],["末節","last"],["末节","last"],
  ["metacarpal","metacarpal"],["metacarp","metacarpal"],["中手","metacarpal"],
  ["零",0],["一",1],["二",2],["三",3],
]);
const NAMES = DIGITS.flatMap((aliases,digit)=>aliases.map((name)=>[name,digit]))
  .sort((a,b)=>b[0].length-a[0].length);

function forms(raw) {
  if (!raw) return [];
  const clean=raw.normalize("NFKC").toLowerCase().split(/[:|]/u).at(-1).trim()
    .replace(/^(?:(?:def|cc[\s._-]+base|j[\s._-]+bip|valvebiped|bip(?:ed)?\d*)[\s._-]+|mixamorig\d*[\s._-]*)+/u,"");
  const parts=clean.split(/[\s._-]+/u).filter(Boolean);
  if(parts[0]==="c" && parts.length>1)parts.shift();
  const joined=parts.join("");
  const tagged=parts.filter((n)=>SIDE.has(n));
  if(tagged.length) {
    if(new Set(tagged.map((n)=>SIDE.get(n))).size>1)return [];
    return [{name:parts.filter((n)=>!SIDE.has(n)).join(""),side:SIDE.get(tagged[0])}];
  }
  const out=[];
  for(const [prefix,side] of SIDE) {
    if(joined.startsWith(prefix))out.push({name:joined.slice(prefix.length),side});
    if(joined.endsWith(prefix))out.push({name:joined.slice(0,-prefix.length),side});
  }
  out.push({name:joined,side:null});
  return out;
}

function finger(name) {
  const clean=name.replace(/^(?:hand|finger|f(?=index|middle|ring|pinky|little|fore))+/u,"");
  for(const [base,digit] of NAMES) {
    if(!clean.startsWith(base))continue;
    const end=clean.slice(base.length).replace(/^(?:finger|joint|phalange|bone|第)/u,"").replace(/(?:関節|关节|節|节)$/u,"");
    const segment=/^0?[0-3]$/u.test(end) ? Number(end) : SEGMENTS.get(end);
    if(segment!==undefined)return {digit,segment};
  }
  return null;
}

function match(bone, parse) {
  for(const raw of [bone.name,bone.nameEn]) for(const form of forms(raw)) {
    const result=parse(form.name);
    if(result)return {...result,side:form.side};
  }
  return null;
}

const descendant = (bones, child, ancestor) => {
  if(!(ancestor>=0))return false;
  for(let i=bones[child]?.parent, count=0; i>=0 && count<bones.length; i=bones[i]?.parent,count++) {
    if(i===ancestor)return true;
  }
  return false;
};

const unique=(items)=>items.length===1 ? items[0] : -1;

export function suggestBodyAliases(bones, mapping, kind) {
  const map=Array.from(mapping), used=new Set(map.filter((i)=>i>=0));
  const names=bones.map((bone)=>[...forms(bone.name),...forms(bone.nameEn)]);
  function fill(joint, groups, side=null) {
    if(map[joint]>=0)return;
    for(const group of groups) {
      const matches=[];
      for(const [id,aliases] of names.entries()) {
        if(used.has(id) || !aliases.some((a)=>a.side===side && group.includes(a.name)))continue;
        map[joint]=id;
        if(jointFitsChain({map},bones,kind,joint))matches.push(id);
        map[joint]=-1;
      }
      if(!matches.length)continue;
      if(matches.length===1) {map[joint]=matches[0];used.add(matches[0]);}
      // Two plausible bones with the same role are a deliberate manual choice.
      return;
    }
  }
  // Bind the hips, low spine and known end of the spine before choosing its
  // intermediate/chest links. An unrelated chest or head cannot win by name.
  for(const j of [0,3,12,15,9,6]) {
    const [,group]=BODY.find(([joint])=>joint===j);
    fill(j,group);
  }
  for(const side of [0,1]) for(const [j,group] of LIMBS)fill(j+side,group,side);
  return map;
}

export function createHandNamePicker(bones) {
  const labels=bones.map((b)=>({
    wrist:match(b,(name)=>WRIST.test(name)?{}:null),
    finger:match(b,finger),
  }));
  return (mapping)=>resolveHandAliases(bones,labels,mapping);
}

export function suggestHandAliases(bones, mapping) {
  return createHandNamePicker(bones)(mapping);
}

function resolveHandAliases(bones, labels, mapping) {
  const map=Array.from(mapping);
  const used=new Set(map.filter((i)=>i>=0));
  for (let side=0;side<2;side++) {
    const wristJoint=20+side;
    if(map[wristJoint]<0) {
      const parent=map[18+side]<0 ? map[16+side] : map[18+side];
      const candidates=[];
      for(const [i,{wrist}] of labels.entries()) {
        if(!wrist || used.has(i) || (wrist.side!=null && wrist.side!==side))continue;
        if(parent>=0 ? descendant(bones,i,parent) : wrist.side===side)candidates.push(i);
      }
      const selected=unique(candidates);
      if(selected>=0) {map[wristJoint]=selected;used.add(selected);}
    }
    const wrist=map[wristJoint];
    if(wrist<0)continue;
    const fingerLabels=labels.flatMap(({finger},i)=>finger && (finger.side==null || finger.side===side) &&
      descendant(bones,i,wrist) ? [{i, ...finger}] : []);
    for(let digit=0;digit<DIGITS.length;digit++) {
      const start=22+side*15+digit*3, names=fingerLabels.filter((f)=>f.digit===digit);
      const numbered=(n)=>names.filter((f)=>f.segment===n).map((f)=>f.i);
      const [zero,one,two]=[0,1,2].map((n)=>unique(numbered(n)));
      const partialThumb = digit===4 && !numbered(2).length;
      const useZero=zero>=0 && one>=0 && !numbered(3).length && descendant(bones,one,zero) &&
        (partialThumb || (two>=0 && descendant(bones,two,one)));
      const thumbMeta=digit===4 && names.some(({segment})=>segment==="metacarpal");
      const segmentIndex=(segment)=>{
        if(typeof segment==="number")return segment-(useZero?0:1);
        return { metacarpal:thumbMeta?0:-1, first:thumbMeta?1:0, middle:1, last:2 }[segment];
      };
      for(let part=0;part<3;part++) {
        if(map[start+part]>=0)continue;
        const prev=map.slice(start,start+part).findLast((id)=>id>=0) ?? wrist;
        const next=map.slice(start+part+1,start+3).find((id)=>id>=0);
        const choices=names.filter(({i,segment})=>segmentIndex(segment)===part && !used.has(i) &&
          descendant(bones,i,prev) && (next===undefined || descendant(bones,next,i))).map(({i})=>i);
        const selected=unique(choices);
        if(selected>=0) {map[start+part]=selected;used.add(selected);}
      }
    }
  }
  return map;
}
