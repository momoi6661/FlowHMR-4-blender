import { $, el, show } from "./utils.js";
import { appError, message as m, setAttr, setText, t } from "./i18n.js";
import { drawRetargetSource, jointLabel } from "./retarget-source-view.js";
import { BODY_REQUIRED, CHAINS, descends, jointFitsChain, JOINT_COUNT, normalizeRetargetProfile, profileIssue, rootMappingIssue } from "./retarget-profile.js";
import { createRetargetTargetView } from "./retarget-target-view.js";
import { createRetargetShortcuts, probeAction } from "./retarget-shortcuts.js";
import { fillMirroredBindings, makeRetargetMirror, oppositeJoint } from "./retarget-mirror.js";
import { chooseRestPose, targetRestPose } from "./retarget-reference.js";
import { createHandNamePicker } from "./retarget-hand-names.js";
import { canSavePreset } from "./retarget-presets.js";
import { createRetargetPresetGallery } from "./retarget-preset-gallery.js";

const byName = (text) => text.normalize("NFKC").toLowerCase();
const empty = () => normalizeRetargetProfile({version:1,map:Array(JOINT_COUNT).fill(-1)});
const ikSlots = Object.keys(CHAINS);
const offsets = ["rotations","translations"];
const SKIN_PREF = "flowhmr-retarget-skin";
const zero = () => [0,0,0];
// Hips, then each complete chain from its base to its tip. The root-motion
// binding is separate from the hips and can be assigned or left automatic.
const bodyGuide = [0,3,6,9,12,15, 13,16,18,20, 14,17,19,21, 1,4,7,10, 2,5,8,11, "root"];
const handSections = ["left","right"];
const wristFor = (tab) => tab==="right" ? 21 : 20;
const scopeFor = (id) => typeof id === "number" && id >= 22 ? id >= 37 ? "right" : "left" : "body";
const steps = (tab) => tab === "body" ? bodyGuide : Array.from({length:16}, (_,i) =>
  i ? (tab==="left"?21:36)+i : wristFor(tab));
let lastPosition, lastSize, lastCones = false;
let lastSkin = false;
try { lastSkin=localStorage.getItem(SKIN_PREF)==="on"; } catch { /* use bone view */ }

export function openRetargetEditor({ asset, bones, sourceRest, initial, initialPose, native, useBuiltinInitially, nativeIk, skeleton,
  presetOptions, builtinPreset, deletePreset, resetBuiltin, saveBuiltin, suggestedMap, build, accept, cancel, applyLive }) {
  const {name,kind}=asset;
  const dialog=$("retargetDialog"), source=$("retargetSourceDiagram"), canvas=$("retargetTargetCanvas");
  const search=$("retargetSearch"), list=$("retargetBoneList"), axes=$("retargetAxes"), correction=$("retargetOffsets");
  const save=$("retargetSave"), preview=$("retargetPreview"), hint=$("retargetError"), metrics=$("retargetMetrics");
  const referenceSelect=$("retargetReference"), referenceLabel=$("retargetRestPose");
  const reset=$("retargetResetJoint"), next=$("retargetNext"), undoButton=$("retargetUndo");
  const skinButton=$("retargetSkin");
  const confirm=$("retargetConfirm"), discard=$("retargetDiscard"), clear=$("retargetClear");
  const presetCheck=$("retargetSavePreset");
  const presetName=$("retargetPresetName"), presetHint=$("retargetPresetHint"), presetBox=$("retargetPresets");
  const rebuildMap=$("retargetRebuildMap");
  const defaultPresetName=Array.from(name.trim()).slice(0,40).join("");
  if (dialog.open) return;
  const run = new AbortController(), opt = {signal:run.signal};
  let profile = normalizeRetargetProfile(initial) ?? empty();
  let basePose = initialPose ?? {}, nativeSelected=useBuiltinInitially;
  const rootSettings=(pose)=>({rootRotation:[...(pose?.rootRotation??zero())],rootTranslation:[...(pose?.rootTranslation??zero())]});
  let rootOffset = rootSettings(basePose);
  let tab="body", joint=0, ik=null, ikMode=(initial ? basePose.ikMode : null)??"ik", pending=null;
  let measurement=null, busy=false, accepted=false, disposed=false, moving, resizing, targetIndices, needWrist;
  let liveMirror=true, referenceMode="auto", guideDone=false, probe=null, shortcutIssue=null;
  // Explicit unmatches stay empty until the user rebinds them or runs
  // Match by Name. A mirror retry for an unrelated joint must not undo them.
  let mirrorExcluded=new Set();
  let live=!!initial, liveFrame=0, rebuild=0, shownError=null, loadedPreset=null, liveRun=null, managingPresets=false;
  let wantedPreset=!initial;
  const history=[], fields=new Map();
  let guideSkipped=new Set();
  const handParents=new Set(bones.map((bone)=>bone.parent));
  const handNames=createHandNamePicker(bones);
  let suggestionKey, cachedSuggestions;
  function currentSuggestions(map=profile.map) {
    const key=map.slice(20).join(",");
    if(suggestionKey===key)return cachedSuggestions;
    suggestionKey=key;
    const names=[...suggestedMap];
    for(const side of [0,1]) {
      const wrist=map[20+side];
      if(wrist<0)continue;
      names[20+side]=wrist;
      const start=22+15*side;
      for(let j=start;j<start+15;j++) {
        names[j]=map[j]>=0 ? map[j] : descends(bones,names[j],wrist) ? names[j] : -1;
      }
    }
    cachedSuggestions=handNames(names);
    return cachedSuggestions;
  }
  const mirror=makeRetargetMirror(bones,suggestedMap,kind,currentSuggestions);
  const binding = () => ik ? profile.ik[ik] : joint==="root" ? profile.root : profile.map[joint];
  const activeIndex = () => pending?.id ?? binding();
  const selectedBone = () => bones[activeIndex()];
  const slotLabel = () => ik ? m(`retarget.${ik}`) : joint==="root" ? m("retarget.motion_root") : jointLabel(joint);
  const slotType = () => ik ? "ik" : joint==="root" ? "root" : "joint";
  const editable = () => !ik && (joint==="root" || activeIndex()>=0);
  const canMove = () => joint==="root" || !!selectedBone()?.movable;
  const rootKey = (group) => group==="rotations" ? "rootRotation" : "rootTranslation";
  const rootOrJoint = (group) => joint==="root" ? rootOffset[rootKey(group)] : profile[group][joint];
  const valuesFor = (group) => [...(pending?.[group] ?? rootOrJoint(group) ?? zero())];
  const usesNative = () => !!native && (nativeSelected ? profile.root<0 || profile.root===native.root
    : kind==="pmx" && profile.root<0) &&
    profile.map.every((i,j)=>i===native.map[j]) && ikSlots.every((key)=>profile.ik[key]===native.ik[key]);
  const hasIk = () => (usesNative() && nativeIk) || Object.values(profile.ik).some((i)=>i>=0);

  const parentTrail = (id) => {
    const trail=[];
    for(let i=id;i>=0 && trail.length<4 && bones[i];i=bones[i].parent)trail.unshift(bones[i].name);
    return trail.join(" / ");
  };
  const names=bones.map((bone,i)=>({i,label:bone.name||`#${i}`,haystack:byName(`${bone.name} ${bone.nameEn??""} ${parentTrail(i)} #${i}`)}));
  const targetView=createRetargetTargetView(bones,pickBone,parentTrail,run.signal,asset,()=>{
    skinButton.setAttribute("aria-pressed","false");
    shortcutIssue="retarget.probe_unavailable";
    navigation();
  });
  const shortcuts=createRetargetShortcuts(run.signal,navigation);
  const gallery=createRetargetPresetGallery({
    signal:run.signal,
    onLoad:(entry)=>{
      if(!entry.picked || busy)return;
      loadPreset(entry.picked);
      if(entry.picked.skippedOverride)return m("retarget.preset_use_stock");
    },
    onDelete:(entry)=>managePresets(async()=>{
      await deletePreset(entry.id);
      if(run.signal.aborted)return;
      if(loadedPreset?.id===entry.id)loadedPreset=null;
      gallery.select("");
    }),
    onReset:(entry)=>managePresets(async()=>{
      const factory=await resetBuiltin(entry.id);
      if(run.signal.aborted || !factory)return;
      loadPreset(factory);
    }),
    onSaveBuiltin:(entry)=>managePresets(async()=>{
      if(pendingNeedsConfirm())throw appError("retarget.finish_choice");
      const draft=normalizeRetargetProfile(profile), pose=settings();
      await saveBuiltin(entry.id,draft,pose,run.signal);
    }),
  });
  let presetEntries=presetOptions();
  gallery.set({entries:presetEntries,selected:builtinPreset ?? ""});

  function activeSection() {
    return sectionLabel(tab);
  }
  const sectionLabel = (section) => m(section==="body"?"pose.body":section==="left"?"retarget.left_hand":"retarget.right_hand");
  function referenceView() {
    const detected=targetRestPose(bones,profile.map,suggestedMap);
    const reference=chooseRestPose(referenceMode,detected);
    const pose=reference.type==="a"?"A Pose":"T Pose";
    referenceLabel.dataset.pose=reference.type;
    setText(referenceLabel,referenceMode==="auto" ?
      detected ? m("retarget.reference_detected",{pose}) : m("retarget.reference_unknown") :
      m("retarget.reference_manual",{pose}));
    drawRetargetSource(source,tab,(id)=>id==="root"?profile.root:profile.map[id],ik?null:joint,
      (id)=>selectSlot(id,{section:tab}),BODY_REQUIRED,sourceRest,reference);
  }
  function mirrorStatus(value="") {
    show("retargetMirrorStatus",!!value);
    setText($("retargetMirrorStatus"),value);
  }
  function details() {
    const info=measurement;
    setText(metrics, info ? m("retarget.measurements",{
      height:info.height.toFixed(2),stride:info.strideRatio.toFixed(2),
      arms:[info.lengths.leftHand.ratio,info.lengths.rightHand.ratio].map((x)=>x.toFixed(2)).join(" / "),
      legs:[info.lengths.leftFoot.ratio,info.lengths.rightFoot.ratio].map((x)=>x.toFixed(2)).join(" / "),
    }) : m("retarget.calibration"));
  }
  function navigation() {
    const keys=m("retarget.probe_keys",shortcuts.labels());
    setText($("retargetNavigation"),shortcutIssue ? m(shortcutIssue) : probe ? m("retarget.probe_active",{action:probeAction(probe)}) :
      activeIndex()>=0 ? keys : m("retarget.navigation"));
    const help=$("retargetTutorialKeys");
    if(help)setText(help,keys);
  }
  function drawCorrection() {
    const id=activeIndex();
    const rotation=valuesFor("rotations"),translation=canMove()?valuesFor("translations"):zero();
    const enabled=correction.open && !ik && joint!=="root" && id>=0 && (rotation.some(Boolean)||translation.some(Boolean));
    targetView.setCorrection(enabled?{id,values:{rotation,translation}}:null);
  }
  function pendingNeedsConfirm() {
    return !!pending && (!pending.recommended || offsets.some((group)=>pending[group].some(Boolean)));
  }
  function saveAvailability(invalid=validate()) {
    const missing=BODY_REQUIRED.filter((j)=>profile.map[j]<0).length, mustConfirm=pendingNeedsConfirm();
    const wait=$("retargetSaveHint");
    const note=!busy && !shownError && (missing>0 || !invalid && mustConfirm);
    setText(wait, note ? missing ? m("retarget.finish_required",{count:missing}) : m("retarget.finish_choice") : "");
    preview.disabled=busy||managingPresets||!!invalid||mustConfirm;
    save.disabled=preview.disabled || (presetCheck.checked && !presetName.value.trim());
  }

  function snapshot() {
    return {profile:normalizeRetargetProfile(profile),root:structuredClone(rootOffset),ikMode,excluded:new Set(mirrorExcluded),
      guideSkipped:new Set(guideSkipped),basePose,loadedPreset,wantedPreset,nativeSelected,liveMirror,live,
      presetName:presetName.value,presetSelection:gallery.selection};
  }
  function remember(before=snapshot()) {
    history.push(before);
    if(history.length>60)history.shift();
    undoButton.disabled=busy;
  }
  function undo() {
    if(busy || managingPresets || !history.length)return;
    targetView.endInspection();
    const before=history.pop();
    profile=before.profile;rootOffset=before.root;ikMode=before.ikMode;mirrorExcluded=before.excluded;guideSkipped=before.guideSkipped;
    basePose=before.basePose;loadedPreset=before.loadedPreset && presetEntries.some(({id})=>id===before.loadedPreset.id)
      ? before.loadedPreset : null;
    wantedPreset=before.wantedPreset;nativeSelected=before.nativeSelected;
    live=before.live;
    liveMirror=before.liveMirror;$("retargetMirror").setAttribute("aria-pressed",String(liveMirror));
    presetName.value=before.presetName;gallery.select(before.presetSelection);
    pending=null;guideDone=false;mirrorStatus();
    changed(true);
  }
  function setValues(group,values) {
    if(pending)pending[group]=values;
    else if(joint==="root")rootOffset[rootKey(group)]=values;
    else {
      profile[group][joint]=values;
      const other=liveMirror ? oppositeJoint(joint) : undefined;
      const paired=other==null ? null : mirror.reflectEdit(joint,other,values,group==="rotations",profile.map);
      if(paired)profile[group][other]=paired;
    }
  }
  function makeAxes() {
    for(const group of offsets) {
      const section=el("div","retarget-axes-group");
      const label=m(group==="rotations"?"pose.rotation":"pose.translation");
      section.append(el("div","retarget-small",label));
      const controls=[], bound=group==="rotations"?180:5;
      for(const [axis,letter] of [..."XYZ"].entries()) {
        const row=el("div","pose-axis"), range=el("input"), number=el("input","input");
        range.type="range";number.type="number";
        for(const input of [range,number]) {
          input.min=-bound;input.max=bound;input.step=group==="rotations"?".1":".001";
          input.dataset.retargetAxis=group+letter;
          setAttr(input,"aria-label",m("pose.axis",{kind:label,name:letter}));
          let previous;
          input.oninput=()=>{
            if(busy || !editable() || !Number.isFinite(input.valueAsNumber) || (group==="translations" && !canMove()))return;
            const values=valuesFor(group);
            if(values[axis]===input.valueAsNumber)return;
            if(!pending && !previous) {previous=snapshot();remember(previous);}
            values[axis]=Math.min(bound,Math.max(-bound,input.valueAsNumber));
            setValues(group,values);
            (input===range?number:range).value=values[axis];
            reset.disabled=!offsets.some((key)=>valuesFor(key).some(Boolean));
            changed(false);
          };
          input.onchange=input.onblur=()=>{previous=null;refreshAxes();};
        }
        row.append(el("span","",letter),range,number);section.append(row);controls.push([range,number]);
      }
      fields.set(group,{section,controls});axes.append(section);
    }
  }
  function refreshAxes() {
    const allowed=editable(), movable=canMove();
    show(correction,allowed);
    show("retargetMoveHint",allowed && !movable);
    for(const [group,{section,controls}] of fields) {
      show(section,group!=="translations"||movable);
      const value=valuesFor(group);
      controls.forEach((row,axis)=>row.forEach((input)=>{input.value=value[axis];input.disabled=busy;}));
    }
    reset.disabled=busy || !allowed || !offsets.some((group)=>valuesFor(group).some(Boolean));
  }

  function choose(id, recommended=false) {
    pending=id===binding() || id<0 ? null : {id,recommended,
      rotations:joint==="root"?[...rootOffset.rootRotation]:zero(),
      translations:joint==="root"?[...rootOffset.rootTranslation]:zero()};
  }
  function suggestedId() {
    if(ik || joint==="root" || binding()>=0)return -1;
    const id=joint>=22 ? currentSuggestions()[joint] : suggestedMap[joint];
    return id>=0 && !profile.map.includes(id) && (tab==="body" || joint<22 || profile.map[tab==="left"?20:21]>=0) ? id : -1;
  }
  function selectSlot(id,{section=scopeFor(id), suggest=true, ikSlot=null}={}) {
    if(busy)return;
    targetView.endInspection();shortcutIssue=null;
    joint=id;tab=section;ik=ikSlot;pending=null;guideDone=false;
    search.value="";mirrorStatus();
    if(suggest)choose(suggestedId(),true);
    sync();
  }
  function eligible(id) {
    if (!ik && joint>=22 && profile.map[joint<37?20:21]<0)return false;
    if (!ik)return true;
    if (kind==="pmx")return bones[id].ikEffector>=0;
    const first=profile.map[CHAINS[ik][0]];
    return !profile.map.includes(id) && first!==id && !descends(bones,id,first);
  }
  function pickBone(id) {
    if(busy||!eligible(id))return;
    targetView.endInspection();shortcutIssue=null;guideDone=false;mirrorStatus();
    if(pending?.id!==id)choose(id);
    sync();
    canvas.focus({preventScroll:true});
  }
  function searchBones() {
    const query=byName(search.value.trim()), selected=activeIndex();
    const results=names.filter(({i,haystack})=>targetIndices.has(i) && eligible(i) && (!query || haystack.includes(query)));
    if(!query) {
      const relevant=tab==="body"?[suggestedMap[joint]]:currentSuggestions().slice(tab==="left"?22:37,tab==="left"?37:52);
      const suggested=new Set(relevant.filter((i)=>i>=0));
      results.sort((a,b)=>Number(b.i===selected)-Number(a.i===selected) ||
        Number(suggested.has(b.i))-Number(suggested.has(a.i)) || Number(profile.map.includes(b.i))-Number(profile.map.includes(a.i)));
    }
    list.replaceChildren(...results.slice(0,120).map(({i,label})=>{
      const button=el("button","",label);
      button.type="button";button.dataset.targetBone=i;button.title=parentTrail(i);
      button.setAttribute("aria-pressed",String(i===selected));
      button.onclick=()=>pickBone(i);
      return button;
    }));
    if(!results.length)list.append(el("span","retarget-small",m(needWrist && joint>=22?"retarget.choose_wrist":"retarget.no_results")));
  }
  function applyBinding(target,id) {
    if(ik)target.ik[ik]=id;
    else if(joint==="root")target.root=id;
    else {
      target.map[joint]=id;
      for(const group of offsets)delete target[group][joint];
      // A native IK controller targeting the previous endpoint cannot be
      // retained after that wrist or ankle is replaced.
      if(kind==="pmx")for(const [slot,chain] of Object.entries(CHAINS)) {
        const driver=target.ik[slot];
        if(chain[2]===joint && driver>=0 && (id<0 || bones[driver].ikEffector!==id))target.ik[slot]=-1;
      }
    }
  }
  const issueMessage = (issue) => issue ? m(
    issue.key==="retarget.invalid_root" && kind==="pmx" ? "retarget.invalid_root_pmx" : issue.key,
    issue.joint!=null?{joint:jointLabel(issue.joint)}:issue.chain?{chain:m(`retarget.${issue.chain}`)}:{}) : null;
  function candidateIssue() {
    if(!pending)return null;
    if(!eligible(pending.id))return m("retarget.choose_wrist");
    const candidate=normalizeRetargetProfile(profile);
    applyBinding(candidate,pending.id);
    if(joint==="root" && !ik) {
      return issueMessage(rootMappingIssue(candidate,bones,kind));
    }
    if(slotType()==="joint") {
      const occupied=profile.map.indexOf(pending.id);
      if(occupied>=0 && occupied!==joint)return m("retarget.used_by",{joint:jointLabel(occupied)});
      if((joint!==0 && pending.id===profile.root)||Object.values(profile.ik).includes(pending.id))
        return m("retarget.used_control");
      if(!jointFitsChain(candidate,bones,kind,joint))
        return m("retarget.hierarchy",{joint:jointLabel(joint)});
      return issueMessage(rootMappingIssue(candidate,bones,kind));
    }
    const issue=profileIssue(candidate,bones,kind,{allowIncomplete:true});
    if(ik && issue?.key==="retarget.invalid_ik" && issue.chain===ik)
      return issueMessage(issue);
    return issueMessage(rootMappingIssue(candidate,bones,kind));
  }
  const unresolved = (id) => id==="root" ? profile.root<0 : profile.map[id]<0;
  const needsGuide = (id) => unresolved(id) && !guideSkipped.has(id) && !mirrorExcluded.has(id);
  function handStart(section) {
    const wrist=wristFor(section);
    if(unresolved(wrist) && !mirrorExcluded.has(wrist))return wrist;
    if(!handParents.has(profile.map[wrist]))return undefined;
    return steps(section).slice(1).find(needsGuide);
  }
  function guideDestination(skipHand=false) {
    if(ik)return null;
    const all=steps(tab), i=all.indexOf(joint);
    const later=all.slice(i+1);
    let destination;
    if(tab==="body") {
      const rootNext=later.includes("root");
      destination=later.find((id)=>id!=="root" && needsGuide(id) &&
        (!liveMirror || oppositeJoint(id)==null || unresolved(oppositeJoint(id))));
      // Defer opposite-side exceptions until both primary chains have been
      // covered, but ask for still-missing required bones before the root.
      if(destination==null && liveMirror && rootNext) {
        const missing=bodyGuide.filter((id)=>id!==joint && BODY_REQUIRED.includes(id)&&needsGuide(id));
        destination=missing.find((id)=>later.includes(id)) ?? missing[0];
      }
      if(destination==null && rootNext && unresolved("root"))destination="root";
    } else if(!skipHand && handParents.has(profile.map[wristFor(tab)])) destination=later.find(needsGuide);
    if(destination!=null)return {id:destination,section:tab};
    for(const section of handSections.slice(handSections.indexOf(tab)+1)) {
      const id=handStart(section);
      if(id!=null)return {id,section};
    }
    return null;
  }
  function advance(skipHand=false) {
    if(ik){pending=null;sync();return;}
    const route=guideDestination(skipHand);
    if(route) {
      selectSlot(route.id,{section:route.section});canvas.focus({preventScroll:true});return;
    }
    targetView.endInspection();pending=null;guideDone=true;sync();
  }
  // Turning on mirror or confirming a binding can make other existing pairs
  // resolvable too (for example, locating the opposite wrist unlocks fingers).
  // Keep prior user assignments, and advance over the new pairs in one step.
  function completeMirror(preferred=null) {
    if(!liveMirror)return {count:0,note:""};
    const filled=fillMirroredBindings(mirror,profile,preferred,mirrorExcluded);
    const other=preferred?.ik ? otherIk(preferred.ik) : oppositeJoint(preferred?.joint);
    const pair=filled.find((p)=>preferred && p.to===other && p.type===(preferred.ik?"ik":"joint"));
    if(pair && filled.length===1)return {count:1,note:m("retarget.mirror_linked",{name:bones[pair.index].name})};
    if(filled.length)return {count:filled.length,note:m("retarget.mirror_filled",{count:filled.length})};
    if(other==null || mirrorExcluded.has(other))return {count:0,note:""};
    const mapped=(preferred.ik ? profile.ik[other] : profile.map[other])>=0;
    return {count:0,note:m(mapped?"retarget.mirror_kept":"retarget.mirror_missing")};
  }
  function otherIk(slot) {
    return slot.startsWith("left") ? "right"+slot.slice(4) : "left"+slot.slice(5);
  }
  function matchGuide(note) {
    changed(true);
    advance();
    if(note)mirrorStatus(note);
  }
  function confirmMatch() {
    if(busy) return;
    targetView.endInspection();
    const preferred=ik?{ik}:{joint};
    if (!pending) {
      if(binding()<0 && (ik || joint!=="root"))return;
      const before=snapshot(), {count,note}=completeMirror(preferred);
      if(count) {
        remember(before);
        changed(true);
      }
      advance();
      if(note)mirrorStatus(note);
      return;
    }
    if(candidateIssue())return;
    remember(); const choice=pending;
    pending=null;
    guideSkipped.delete(joint);
    mirrorExcluded.delete(ik??joint);
    applyBinding(profile,choice.id);
    const {note}=completeMirror(preferred);
    if(!ik) {
      for(const group of offsets) {
        // A replacement starts at zero. Merely binding it should not clear
        // nonzero corrections on an already-mapped opposite joint.
        if((group==="rotations"||canMove()) && (joint==="root" || choice[group].some(Boolean)))setValues(group,choice[group]);
      }
    }
    matchGuide(note);
  }
  function removeMatch() {
    if(busy || binding()<0)return;
    targetView.endInspection();
    remember();applyBinding(profile,-1);mirrorExcluded.add(ik??joint);pending=null;guideDone=false;mirrorStatus();
    changed(true);
  }
  function validate() {
    if(usesNative())return null;
    return issueMessage(profileIssue(profile,bones,kind));
  }
  function settings() {
    const draft=normalizeRetargetProfile(profile), automatic=usesNative();
    return {...basePose,...rootOffset,retarget:automatic ? null : draft,
      boneOffsets:automatic ? {rotations:draft.rotations,translations:draft.translations} : null,
      mirror:liveMirror,ikMode:hasIk()?ikMode:"fk"};
  }
  function presetControls() {
    const encodable=canSavePreset(profile,skeleton);
    presetCheck.disabled=busy || !encodable;
    presetCheck.checked=wantedPreset && encodable;
    presetName.disabled=busy || !presetCheck.checked;
    show("retargetPresetNameRow",presetCheck.checked);
    gallery.set({entries:presetEntries,busy,
      canSaveBuiltin:!pendingNeedsConfirm() && (usesNative() || encodable && !validate())});
    rebuildMap.disabled=busy || !profile.map.some((i)=>i>=0);
    const ready=encodable && BODY_REQUIRED.every((j)=>profile.map[j]>=0);
    setText($("retargetPresetSummary"),m(wantedPreset && ready?"retarget.presets_marked":"retarget.presets"));
    const note=!encodable?"retarget.preset_ambiguous" :
      presetCheck.checked && loadedPreset?.name===presetName.value.trim() ? "retarget.preset_replaces" :
      presetCheck.checked ? "retarget.preset_reuse_hint" : null;
    setText(presetHint,note ? m(note) : "");
    show(presetHint,!!note);
  }
  async function managePresets(action) {
    managingPresets=true;
    saveAvailability();
    try { return await action(); }
    finally {
      managingPresets=false;
      if(!run.signal.aborted) {
        presetEntries=presetOptions();
        presetControls();saveAvailability();
      }
    }
  }
  function loadPreset(picked) {
    targetView.endInspection();
    remember();
    profile=normalizeRetargetProfile(picked.profile);
    nativeSelected=picked.useNative ?? !!picked.preset?.builtin;
    basePose=picked.offsets;rootOffset=rootSettings(basePose);ikMode=basePose.ikMode;
    loadedPreset=picked.preset ?? null;
    wantedPreset=false;presetName.value=loadedPreset?.name ?? defaultPresetName;
    pending=null;mirrorExcluded.clear();guideSkipped.clear();guideDone=false;mirrorStatus();
    changed(true);
  }
  function changed(mapping) {
    shownError=null;
    clearTimeout(rebuild);
    liveRun?.abort();
    if(mapping){measurement=null;sync();}
    else {
      drawCorrection();
      if(pending){presetControls();saveAvailability();return;}
    }
    if(!live || validate() || pendingNeedsConfirm())return;
    if(!mapping && applyLive) {
      if(!liveFrame)liveFrame=requestAnimationFrame(()=>{
        liveFrame=0;
        if(!busy && !validate() && !applyLive(settings(),run.signal)) void test(false,true);
      });
    } else rebuild=setTimeout(()=>{if(dialog.open && !busy && !probe)void test(false,true);},135);
  }
  function sync() {
    const context=tab+":"+(ik??joint);
    if(list.dataset.context!==context) {list.dataset.context=context;list.scrollTop=0;}
    dialog.querySelectorAll("[data-retarget-tab]").forEach((button)=>button.setAttribute("aria-pressed",String(button.dataset.retargetTab===tab)));
    referenceView();
    const count=BODY_REQUIRED.filter((j)=>profile.map[j]>=0).length, step=steps(tab);
    setText($("retargetProgress"),m("retarget.count",{count,total:BODY_REQUIRED.length}));
    const wrist=wristFor(tab), remaining=BODY_REQUIRED.length-count;
    const pickHelp=tab==="body" ? "retarget.guide_pick" :
      profile.map[wrist]<0 ? "retarget.hand_bind_wrist" : "retarget.hand_find_fingers";
    setText($("retargetStep"),ik?m("retarget.ik"):
      m("retarget.guide_step",{section:activeSection(),current:step.indexOf(joint)+1,total:step.length}));
    setText($("retargetJoint"),slotLabel());
    setText($("retargetBinding"),selectedBone()?.name??m(joint==="root"?"retarget.root_auto":"retarget.unmapped"));
    const issue=candidateIssue();
    setText($("retargetMatchStatus"),issue??m(busy?"retarget.updating":guideDone?(remaining?"retarget.guide_incomplete":"retarget.guide_done"):
      pending ? pending.recommended?"retarget.guide_suggested":binding()>=0?"retarget.guide_replace":"retarget.guide_check" :
      binding()>=0?"retarget.guide_matched":joint==="root"?"retarget.guide_root_auto":pickHelp,{count:remaining}));
    const route=guideDestination();
    setText(confirm,ik ? m("retarget.confirm_match") : joint==="root" && route ? m("retarget.guide_to_hand",{hand:sectionLabel(route.section)}) :
      route ? m("retarget.confirm_next") : m("retarget.finish_guide"));
    confirm.disabled=busy||activeIndex()<0&&(ik||joint!=="root")||!!issue||guideDone;
    next.disabled=busy||guideDone;
    show(next,!ik && joint!=="root");show(discard,!!pending);show(clear,binding()>=0);
    const skipHand=$("retargetSkipHand");
    if(skipHand) {
      show(skipHand,!ik && tab!=="body" && handParents.has(profile.map[wrist]) && steps(tab).slice(1).some(needsGuide));
      skipHand.disabled=busy||guideDone;
    }
    const autoRoot=$("retargetUseAutoRoot");
    if(autoRoot) {
      show(autoRoot,!ik && joint==="root" && (pending || binding()>=0));
      autoRoot.disabled=busy;
    }
    discard.disabled=clear.disabled=busy;
    undoButton.disabled=busy||!history.length;
    const buttons=ikSlots.map((slot)=>{
      const name=bones[profile.ik[slot]]?.name;
      const button=el("button","btn-secondary small",name?()=>`${t(`retarget.${slot}`)} · ${name}`:m(`retarget.${slot}`));
      button.type="button";button.dataset.ikSlot=slot;button.setAttribute("aria-pressed",String(ik===slot));
      button.onclick=()=>selectSlot(ik===slot?0:joint,{section:"body",ikSlot:ik===slot?null:slot,suggest:false});
      return button;
    });
    $("retargetIkSlots").replaceChildren(...buttons);
    const ikAvailable=hasIk(), displayedMode=ikAvailable?ikMode:"fk";
    show("retargetIkModes",true);
    const ikModeHint=$("retargetIkModeHint");
    if(ikModeHint)show(ikModeHint,!ikAvailable);
    for(const [mode,id] of [["ik","retargetModeIk"],["fk","retargetModeFk"]]) {
      const button=$(id);
      button.setAttribute("aria-pressed",String(displayedMode===mode));
      button.disabled=busy || (mode==="ik" && !ikAvailable);
      button.onclick=()=>{
        if(button.disabled || managingPresets || displayedMode===mode)return;
        targetView.endInspection();remember();ikMode=mode;changed(true);
      };
    }
    ({indices:targetIndices,needWrist}=targetView.set({tab,map:profile.map,
      suggested:tab==="body"?suggestedMap:currentSuggestions(),active:activeIndex(),eligible,busy}));
    searchBones();refreshAxes();drawCorrection();details();navigation();presetControls();
    const invalid=validate();
    // Missing joints are already shown in the guide. Surface actual conflicts
    // here without putting a large red error on an ordinary incomplete draft.
    const waitingForBody=count<BODY_REQUIRED.length;
    const cause=shownError??(waitingForBody?null:invalid);
    show(hint,!!cause);setText(hint,cause??"");
    saveAvailability(invalid);
  }
  async function test(final, silent=false) {
    if(busy||managingPresets||gallery.confirming||validate()||pendingNeedsConfirm() || (final && save.disabled))return;
    if(!silent)targetView.endInspection();
    if(liveFrame){cancelAnimationFrame(liveFrame);liveFrame=0;}
    clearTimeout(rebuild);
    liveRun?.abort();
    const attempt=silent ? liveRun=new AbortController() : run;
    const signal=attempt.signal;
    const request=final && presetCheck.checked ?
      {name:presetName.value,previous:loadedPreset,profile:normalizeRetargetProfile(profile)} : null;
    if(!silent)busy=true;
    shownError=null;
    if(!silent)sync();
    try {
      if(!final) {
        const result=await build(settings(),signal);
        signal.throwIfAborted();measurement=result;live=true;
      } else {
        await accept(settings(),run.signal,request);
        run.signal.throwIfAborted();accepted=true;closeEditor("saved");
      }
    } catch(cause) {
      if(!signal.aborted)shownError=cause;
    } finally {
      if(!silent)busy=false;
      if(silent && liveRun===attempt)liveRun=null;
      if(dialog.open && !signal.aborted) {
        if(!silent || shownError || !hint.classList.contains("hidden"))sync();
        else details();
      }
    }
  }
  function autoMatch() {
    if(busy)return;
    targetView.endInspection();shortcutIssue=null;
    const copy=normalizeRetargetProfile(profile);
    const filled=new Set(copy.map);
    // Body joints provide the ancestry used when validating wrist and digit
    // suggestions. Fill them in chain order, then validate each digit.
    const fill=(i,bone)=>{
      if(copy.map[i]>=0 || bone<0 || filled.has(bone) ||
         (i>=22 && !descends(bones,bone,copy.map[i<37?20:21])) ||
         (i!==0 && bone===copy.root) || Object.values(copy.ik).includes(bone))return;
      copy.map[i]=bone;
      if(jointFitsChain(copy,bones,kind,i))filled.add(bone);
      else copy.map[i]=-1;
    };
    bodyGuide.filter((id)=>id!=="root").forEach((i)=>fill(i,suggestedMap[i]));
    const names=currentSuggestions(copy.map);
    for(let i=22;i<JOINT_COUNT;i++)fill(i,names[i]);
    if(kind==="pmx" && !usesNative())for(const [key,id] of [["leftFoot",7],["rightFoot",8]]) {
      if(copy.ik[key]<0 && copy.map[id]>=0)
        copy.ik[key]=bones.findIndex((bone)=>bone.movable && bone.ikEffector===copy.map[id]);
    }
    const before=snapshot();
    profile=copy;
    mirrorExcluded.clear();
    const {count,note}=completeMirror();
    if(before.excluded.size || before.guideSkipped.size || count || JSON.stringify(before.profile)!==JSON.stringify(profile))remember(before);
    pending=null;guideSkipped.clear();guideDone=false;mirrorStatus(note);changed(true);
  }
  function startProbe(mode) {
    const index=activeIndex(), bone=bones[index];
    if(busy || !bone) {shortcutIssue="retarget.probe_pick";navigation();return;}
    if(probe===mode) {targetView.endInspection();return;}
    targetView.endInspection();
    shortcutIssue=null;
    if(mode==="move" && !bone.movable) {shortcutIssue="retarget.probe_no_move";navigation();return;}
    if(targetView.startInspection(mode,index,()=>{
      probe=null;shortcutIssue=null;navigation();
      if(live && !validate() && !pending) changed(false);
    }))probe=mode;
    else shortcutIssue="retarget.probe_unavailable";
    navigation();
  }

  axes.replaceChildren();makeAxes();
  referenceSelect.value=referenceMode;correction.open=true;$("retargetIk").open=false;
  correction.addEventListener("toggle",drawCorrection,opt);
  referenceSelect.addEventListener("change",()=>{referenceMode=referenceSelect.value;referenceView();},opt);
  search.value="";
  search.oninput=()=>{targetView.endInspection();searchBones();};
  search.onkeydown=(e)=>{if(e.key==="Enter"){e.preventDefault();list.querySelector("button")?.click();}};
  $("retargetAuto").onclick=autoMatch;$("retargetAuto").disabled=false;
  confirm.onclick=confirmMatch;
  next.onclick=()=>{
    if(busy)return;
    if(unresolved(joint))guideSkipped.add(joint);
    advance();
  };
  const skipHand=$("retargetSkipHand");
  if(skipHand)skipHand.onclick=()=>{if(!busy)advance(true);};
  const autoRoot=$("retargetUseAutoRoot");
  if(autoRoot)autoRoot.onclick=()=>{
    if(busy || ik || joint!=="root")return;
    if(profile.root>=0) {
      remember();profile.root=-1;pending=null;changed(true);
    }
    advance();
  };
  discard.onclick=()=>{
    if(busy)return;
    targetView.endInspection();pending=null;shortcutIssue=null;sync();canvas.focus({preventScroll:true});
  };
  clear.onclick=removeMatch;
  reset.onclick=()=>{
    if(busy||!editable())return;
    if(!pending)remember();
    setValues("rotations",zero());
    if(canMove())setValues("translations",zero());
    refreshAxes();changed(false);
  };
  undoButton.onclick=undo;
  function setSkin(enabled) {
    skinButton.setAttribute("aria-pressed",String(enabled));
    targetView.setSkin(enabled);
  }
  skinButton.onclick=()=>{
    lastSkin=skinButton.getAttribute("aria-pressed")!=="true";
    try {localStorage.setItem(SKIN_PREF,lastSkin?"on":"off");} catch { /* still remembered in this window */ }
    shortcutIssue=null;
    setSkin(lastSkin);
    navigation();
  };
  setSkin(lastSkin);
  const coneButton=$("retargetCones"), mirrorButton=$("retargetMirror");
  const setCones=()=>{coneButton.setAttribute("aria-pressed",String(lastCones));targetView.setCones(lastCones);};
  coneButton.onclick=()=>{lastCones=!lastCones;setCones();};setCones();
  mirrorButton.setAttribute("aria-pressed",String(liveMirror));
  mirrorButton.onclick=()=>{
    if(busy)return;
    remember();
    liveMirror=!liveMirror;mirrorButton.setAttribute("aria-pressed",String(liveMirror));
    if(!liveMirror || usesNative()){mirrorStatus();sync();return;}
    const wasEmpty=binding()<0, {count,note}=completeMirror();
    if(!count){mirrorStatus();sync();return;}
    if(pending && pending.id===binding())pending=null;
    if(wasEmpty && binding()>=0 && !pending)matchGuide(note);
    else {changed(true);mirrorStatus(note);}
  };
  presetName.value=defaultPresetName;
  presetName.oninput=presetCheck.onchange=()=>{
    wantedPreset=presetCheck.checked;presetControls();saveAvailability();
  };
  rebuildMap.onclick=()=>{
    if(busy)return;
    targetView.endInspection();remember();
    profile=empty();nativeSelected=false;wantedPreset=true;loadedPreset=null;live=false;
    gallery.select("");presetName.value=defaultPresetName;
    pending=null;mirrorExcluded.clear();guideSkipped.clear();mirrorStatus();
    selectSlot(0);
  };
  presetBox.open=true;
  $("retargetClose").onclick=()=>{if(!busy)closeEditor("cancel");};
  preview.onclick=()=>void test(false);
  save.onclick=()=>void test(true);
  function dispose() {
    if(disposed)return;
    disposed=true;run.abort();liveRun?.abort();clearTimeout(rebuild);
    if(liveFrame)cancelAnimationFrame(liveFrame);
    if(!accepted)cancel();
  }
  function closeEditor(result) {
    if(dialog.open)dialog.close(result);
    dispose();
  }
  function escape() {
    if(busy||managingPresets)return;
    if(gallery.confirming){gallery.dismiss();return;}
    if(targetView.endInspection() || targetView.closeMenu(true))return;
    if(shortcuts.open){shortcuts.close();return;}
    if(pending && !pending.recommended){discard.click();return;}
    closeEditor("cancel");
  }
  dialog.addEventListener("close",()=>{if(!dialog.open)dispose();},opt);
  dialog.addEventListener("cancel",(event)=>{event.preventDefault();escape();},opt);
  window.addEventListener("keydown",(event)=>{
    if(!dialog.open || event.defaultPrevented || event.isComposing || document.querySelector("dialog:modal"))return;
    const input=event.target.isContentEditable || event.target.closest?.("input,textarea,select,[role=textbox]");
    if(!shortcuts.owns(event) && (!probe || event.key!=="Escape" || input))return;
    const typing=input && !event.target.matches?.("input:is([type=range],[type=checkbox],[type=radio],[type=button])");
    if(!typing && (!probe || event.key!=="Escape") && shortcuts.handle(event))return;
    if(event.key==="Escape"){event.preventDefault();event.stopPropagation();escape();return;}
    if(typing)return;
    if(!input && (event.ctrlKey||event.metaKey) && !event.shiftKey && event.code==="KeyZ") {
      event.preventDefault();event.stopPropagation();undo();return;
    }
    if(!input && event.key==="Enter" && probe) {
      event.preventDefault();event.stopPropagation();targetView.endInspection();return;
    }
    if(event.key==="Enter" && event.target===canvas && !confirm.disabled) {
      event.preventDefault();event.stopPropagation();confirmMatch();return;
    }
    const mode=shortcuts.mode(event);
    if(mode) {event.preventDefault();event.stopPropagation();startProbe(mode);}
  },{...opt,capture:true});
  dialog.addEventListener("keydown",(event)=>{
    if(event.key==="Home"||event.key.startsWith("Arrow")||event.code?.startsWith("Numpad"))event.stopPropagation();
  },opt);

  const grip=$("retargetGrip"), resizeGrip=$("retargetResize");
  const mainBar=document.querySelector(".topbar");
  const topLimit=()=>Math.max(6,Math.ceil(mainBar?.getBoundingClientRect().bottom??0)+6);
  function size(width,height) {
    const fullW=Math.max(1,innerWidth-12),fullH=Math.max(1,innerHeight-topLimit()-6);
    const roomW=Math.max(1,innerWidth-dialog.offsetLeft-6),roomH=Math.max(1,innerHeight-dialog.offsetTop-6);
    const minW=Math.min(440,fullW),minH=Math.min(315,fullH);
    const w=Math.round(Math.min(Math.max(minW,roomW),Math.max(width,minW)));
    const h=Math.round(Math.min(Math.max(minH,roomH),Math.max(height,minH)));
    dialog.dataset.resized="";dialog.style.width=w+"px";dialog.style.height=h+"px";lastSize=[w,h];
  }
  function place(x,y,rememberPosition=false) {
    const limit=topLimit();
    // Keep the drag handle below the main toolbar even when the panel grows.
    dialog.style.setProperty("--retarget-top-limit",limit+"px");
    const left=Math.max(6,Math.min(x,innerWidth-dialog.offsetWidth-6));
    const top=Math.max(limit,Math.min(y,innerHeight-dialog.offsetHeight-6));
    dialog.style.left=left+"px";dialog.style.top=top+"px";
    if(rememberPosition)lastPosition=[left,top];
  }
  grip.addEventListener("pointerdown",(event)=>{
    if(event.button!==0||event.target.closest("button,details"))return;
    const box=dialog.getBoundingClientRect();
    moving={id:event.pointerId,x:event.clientX-box.left,y:event.clientY-box.top};
    grip.setPointerCapture(event.pointerId);
  },opt);
  grip.addEventListener("pointermove",(e)=>{if(e.pointerId===moving?.id)place(e.clientX-moving.x,e.clientY-moving.y,true);},opt);
  grip.addEventListener("lostpointercapture",()=>{moving=null;},opt);
  resizeGrip.addEventListener("pointerdown",(event)=>{
    if(event.button!==0)return;
    const box=dialog.getBoundingClientRect();
    resizing={id:event.pointerId,w:box.width,h:box.height,x:event.clientX,y:event.clientY};resizeGrip.setPointerCapture(event.pointerId);
  },opt);
  resizeGrip.addEventListener("pointermove",(event)=>{
    if(event.pointerId===resizing?.id)size(resizing.w+event.clientX-resizing.x,resizing.h+event.clientY-resizing.y);
  },opt);
  resizeGrip.addEventListener("lostpointercapture",()=>{resizing=null;},opt);
  resizeGrip.addEventListener("keydown",(event)=>{
    const move={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];
    if(!move)return;
    event.preventDefault();event.stopPropagation();
    const n=event.shiftKey?48:16;
    size(dialog.offsetWidth+n*move[0],dialog.offsetHeight+n*move[1]);
  },opt);
  const withinWindow=new ResizeObserver(()=>{if(dialog.open)place(dialog.offsetLeft,dialog.offsetTop);});
  withinWindow.observe(dialog);
  if(mainBar)withinWindow.observe(mainBar);
  run.signal.addEventListener("abort",()=>withinWindow.disconnect(),{once:true});
  window.addEventListener("resize",()=>{if(dialog.open)place(dialog.offsetLeft,dialog.offsetTop);},opt);
  dialog.querySelectorAll("[data-retarget-tab]").forEach((button)=>button.onclick=()=>{
    const section=button.dataset.retargetTab,wrist=wristFor(section);
    selectSlot(section==="body" ? 0 : profile.map[wrist]<0 ? wrist : section==="left"?22:37,{section});
  });
  setText($("retargetModel"),name);
  setText($("retargetIkHint"),m(kind==="pmx"?"retarget.ik_pmx":"retarget.ik_baked"));
  const first=bodyGuide.find((id)=>BODY_REQUIRED.includes(id) && unresolved(id));
  selectSlot(first??0);
  dialog.show();
  const viewer=$("viewer").getBoundingClientRect();
  place(...(lastPosition??[Math.min(viewer.left+10,innerWidth*.17),Math.max(48,viewer.top+10)]));
  if(lastSize)size(...lastSize);
  canvas.focus({preventScroll:true});
  return {close:()=>closeEditor("cancel")};
}
