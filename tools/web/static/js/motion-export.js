import { $, el, personColor, show } from "./utils.js";
import { appError, message as m, setText } from "./i18n.js";
import * as api from "./api.js";

const FORMAT_STORAGE = "flowhmr-export-format";
const SKIN_STORAGE = "flowhmr-fbx-with-skin";
const POSE_STORAGE = "flowhmr-fbx-first-pose";

export function initMotionExport(getBodyVariant, viewer) {
  const dialog = $("fbxExportDialog"), button = $("dlFbx"), menu = $("menuFbx");
  const format = $("motionExportFormat"), confirm = $("fbxConfirm");
  const skin = $("fbxWithSkin"), pose = $("fbxFirstPose"), peopleField = $("fbxPeople");
  const error = $("motionExportError"), openCharacters = $("fbxChooseCharacter");
  const mixamoRig = $("mixamoRig"), mixamoCharacter = $("mixamoRigCharacter");
  const vmdTarget = $("vmdTarget"), characterOption = $("vmdTargetCharacter");
  const chooseCharacter = $("vmdChooseCharacter");
  const formats = [...format.options];
  let preferred = localStorage.getItem(FORMAT_STORAGE);
  if (preferred === "mixamo_iclone") preferred = "mixamo";
  let result = null, pending = null, entries = [], multiPeople = [], single = false;

  skin.checked = localStorage.getItem(SKIN_STORAGE) === "on";
  const savedPose = localStorage.getItem(POSE_STORAGE);
  pose.value = ["t", "a"].includes(savedPose) ? savedPose : "none";

  const selected = () => entries.filter(({ input }) => input.checked).map(({ input }) => input.value);
  const targets = () => viewer.getPmxActors(selected());
  const retargeted = () => viewer.getRetargetActors(selected());
  const fbxActor = (profile) => viewer.getFbxActor(selected(), profile);
  function selectMode(next) {
    if (next === single) return;
    if (next) multiPeople = selected();
    const keep = next ? [multiPeople[0] ?? entries[0]?.input.value] : multiPeople;
    for (const { input } of entries) {
      input.type = next ? "radio" : "checkbox";
      if (next) input.name = "fbx-export-person"; else input.removeAttribute("name");
      input.checked = keep.includes(input.value);
    }
    single = next;
  }
  function showError(value = "") {
    setText(error, value);
    show(error, !!value);
  }
  function sync() {
    const glb = formats.find(({value})=>value==="retarget_glb");
    glb.disabled=glb.hidden=!result || !retargeted().length;
    if (glb.disabled && format.value==="retarget_glb") format.value="flowhmr";
    const profile = format.value;
    const mixamo = profile === "mixamo", metahuman = profile === "metahuman";
    const currentFbx = profile === "retarget_fbx";
    const singlePerson = currentFbx || metahuman || profile === "maya_adv";
    selectMode(singlePerson);
    const actor = mixamo || metahuman || currentFbx ? fbxActor(profile) : null;
    const useActor = actor && (!mixamo || mixamoRig.value !== "generic");
    show("mixamoRigOptions", mixamo && !!actor);
    if (mixamo && actor) setText(mixamoCharacter, m("export.fbx_target", { name: actor.asset.name }));
    show(openCharacters, (metahuman || currentFbx) && !actor);
    show("standardFbxOptions", profile === "flowhmr" || mixamo || (currentFbx && !!actor));
    show("fbxReferenceOptions", profile === "flowhmr" || (mixamo && !useActor));
    setText($("fbxSkinHint"), m(useActor ? "export.fbx_target_skin" : "export.skin_hint"));
    show("vmdOptions", profile === "vmd");
    const roles = targets();
    characterOption.disabled = characterOption.hidden = !roles.length;
    if (roles.length) setText(characterOption, roles.length === 1
      ? m("export.vmd_character", { name: roles[0].asset.name }) : m("export.vmd_characters"));
    else vmdTarget.value = "generic";
    let hint = profile;
    if (profile === "vmd") hint = vmdTarget.value === "target" ? "vmd_target" : "vmd_generic";
    if (mixamo) hint = useActor ? "mixamo_target" : "mixamo";
    if (metahuman && !actor) hint = "metahuman_required";
    if (currentFbx && !actor) hint = "retarget_fbx_required";
    setText($("motionExportHint"), profile === "retarget_glb" ? m("retarget.glb_hint") :
      hint ? m(`export.${hint}_hint`, { name: actor?.asset.name }) : "");
    setText($("exportPeopleLabel"), m(singlePerson ? "export.select_person" : "export.select_people"));
    const busy = !!pending;
    format.disabled = peopleField.disabled = vmdTarget.disabled = chooseCharacter.disabled =
      mixamoRig.disabled = openCharacters.disabled = skin.disabled = busy;
    confirm.disabled = !result || !profile || busy || (entries.length > 0 && !selected().length)
      || ((metahuman || currentFbx) && !actor);
    setText(confirm, m(busy ? "export.preparing" : "common.export"));
  }
  function setPeople(scene) {
    single = false;
    entries = (scene?.people ?? []).map(({ person }) => {
      const label = el("label", "fbx-export-person");
      const input = el("input");
      const dot = el("span", "person-dot");
      dot.style.background = personColor(person - 1);
      input.type = "checkbox";
      input.value = person;
      input.checked = true;
      label.append(input, dot, el("span", "", m("person.number", { number: person })));
      return { label, input };
    });
    multiPeople = selected();
    $("fbxPeopleList").replaceChildren(...entries.map(({ label }) => label));
    show(peopleField, !!entries.length);
  }
  function setButtons(enabled) {
    button.disabled = menu.disabled = !enabled;
    show(button, enabled);
  }
  function download(href, filename = "") {
    const link = document.createElement("a");
    link.href = href;
    link.download = filename;
    link.click();
  }
  function downloadBlob(blob, filename) {
    const href = URL.createObjectURL(blob);
    dialog.close("export");
    download(href, filename);
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
  }

  button.onclick = () => {
    if (!result || dialog.open) return;
    dialog.returnValue = "";
    showError();
    vmdTarget.value = targets().length ? "target" : "generic";
    mixamoRig.value = "target";
    // Resolve the default after preview and character restoration have run.
    const currentFbx = formats.find(({value}) => value === "retarget_fbx");
    if ((!preferred || preferred === "retarget_glb") && !currentFbx.disabled && fbxActor("retarget_fbx")) {
      format.value = "retarget_fbx";
    } else if (preferred === "retarget_glb" && retargeted().length) format.value = preferred;
    sync();
    dialog.showModal();
  };
  menu.onclick = () => button.click();
  peopleField.addEventListener("change", sync);
  vmdTarget.addEventListener("change", sync);
  mixamoRig.addEventListener("change", () => { showError(); sync(); });
  openCharacters.onclick = () => {
    dialog.close("character");
    viewer.openCharacter(selected()[0]);
  };
  chooseCharacter.onclick = () => {
    dialog.close("character");
    viewer.openCharacter();
  };
  window.addEventListener("flowhmr:characterchange", () => {
    if (!dialog.open || pending) return;
    vmdTarget.value = targets().length ? "target" : "generic";
    sync();
  });
  format.addEventListener("change", () => {
    showError();
    sync();
  });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    pending?.abort();
    pending = null;
    sync();
  });
  dialog.querySelector("form").addEventListener("submit", async (event) => {
    if (event.submitter !== confirm) return;
    event.preventDefault();
    if (!result || confirm.disabled || pending) return;
    const { id } = result;
    const profile = format.value;
    const people = selected();
    preferred = profile;
    localStorage.setItem(FORMAT_STORAGE, profile);
    const targetVmd = profile === "vmd" && vmdTarget.value === "target";
    const vmdActors = profile === "vmd" ? viewer.getVmdActors(people) : [];
    const correctedVmd = !targetVmd && vmdActors.some((member) => member.poseOffsets);
    const actor = profile === "retarget_fbx" || profile === "metahuman" || (profile === "mixamo" && mixamoRig.value !== "generic")
      ? fbxActor(profile) : null;
    if ((profile === "metahuman" || profile === "retarget_fbx") && !actor) { sync(); return; }
    if (profile === "vrma" || (profile === "vmd" && !targetVmd && !correctedVmd)) {
      dialog.close("export");
      download(api.motionUrl(id, profile, people));
      return;
    }
    if (!actor && profile !== "vmd" && profile !== "retarget_glb") {
      const advancedSkeleton = profile === "maya_adv";
      if (!advancedSkeleton) {
        localStorage.setItem(SKIN_STORAGE, skin.checked ? "on" : "off");
        localStorage.setItem(POSE_STORAGE, pose.value);
      }
      dialog.close("export");
      download(api.fbxUrl(id, !advancedSkeleton && skin.checked, getBodyVariant(), people,
        advancedSkeleton ? "t" : pose.value, profile));
      return;
    }
    const operation = pending = new AbortController();
    showError();
    sync();
    try {
      let exported;
      if (profile==="retarget_glb") {
        const { bakeRetargetGlb }=await import("./retarget-export.js");
        const stem=(result.filename||"FlowHMR").replace(/\.[^.]+$/,"").replace(/[<>:"/\\|?*\x00-\x1f]/g,"_").slice(0,100);
        exported=await bakeRetargetGlb(retargeted(),stem,operation.signal);
      } else if (profile === "vmd") {
        exported = await makeVmd(vmdActors, result.filename, targetVmd, operation.signal);
      } else {
        const { bakeTargetFbx } = await import("./retarget-fbx.js");
        const baked = await bakeTargetFbx(actor, operation.signal, profile);
        const offersSkin = profile === "mixamo" || profile === "retarget_fbx";
        const withSkin = offersSkin && skin.checked;
        if (offersSkin) localStorage.setItem(SKIN_STORAGE, withSkin ? "on" : "off");
        exported = await api.exportTargetFbx(id, people, profile, actor, baked, withSkin, operation.signal);
      }
      if (pending !== operation || result?.id !== id || !dialog.open) return;
      downloadBlob(exported.blob, exported.filename);
    } catch (cause) {
      if (!operation.signal.aborted && dialog.open) {
        showError(profile === "vmd" ? m("export.pmx_vmd_failed", { detail: cause }) : cause);
      }
    } finally {
      if (pending === operation) {
        pending = null;
        sync();
      }
    }
  });

  return {
    setResult(id, job) {
      result = { id, filename: job.filename };
      const availableByFormat = { vmd: !!job.has_vmd, vrma: !!job.has_vrma };
      for (const option of formats) option.disabled = !(availableByFormat[option.value] ?? job.has_fbx);
      const available = formats.filter((option) => !option.disabled && option.value!=="retarget_glb");
      format.value = (available.find((option) => option.value === preferred) ?? available[0])?.value ?? "";
      setPeople(job.scene);
      setButtons(!!available.length);
      sync();
    },
    clear() {
      pending?.abort();
      pending = null;
      result = null;
      if (dialog.open) dialog.close("cancel");
      setPeople(null);
      setButtons(false);
      sync();
    },
  };
}

export async function makeVmd(actors, filename, target, signal) {
  if (!actors.length) throw appError("export.vmd_character_unavailable");
  const [solverModule, { encodePmxVmd, packPmxVmdZip }] = await Promise.all([
    target ? import("./pmx-retarget.js") : import("./generic-vmd.js"), import("./pmx-vmd.js"),
  ]);
  const stem = (filename || "FlowHMR").replace(/\.[^.]+$/, "").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 100);
  const files = [];
  for (const actor of actors) {
    signal.throwIfAborted();
    const { asset } = actor;
    const motion = actor.motion ?? await api.getCharacterMotion(actor.id, signal);
    const clone = target ? asset.clone() : null;
    let solver;
    try {
      solver = target ? solverModule.createPmxRetarget(clone, asset.rest, motion, { poseOffsets: actor.poseOffsets })
        : solverModule.genericVmdRig(motion, actor.poseOffsets);
      const bytes = await encodePmxVmd({
        solver, rest: target ? asset.rest : solver.rest, modelName: target ? asset.name : "FlowHMR",
        frameCount: motion.frames, fps: actor.fps, startFrame: actor.start, sceneEndFrame: actor.sceneEnd,
        placement: { yaw: actor.yaw, offset: actor.offset }, signal,
      });
      const tag = actor.person == null ? "" : `_person${actor.person}`;
      files.push({ name: `${stem}${tag}_${target ? "pmx" : "vmd"}.vmd`, bytes });
    } finally {
      if (clone) asset.releaseClone(clone);
      else solver?.dispose();
    }
  }
  return files.length === 1
    ? { blob: new Blob([files[0].bytes], { type: "application/octet-stream" }), filename: files[0].name }
    : { blob: packPmxVmdZip(files), filename: `${stem}${target ? "_pmx" : ""}_vmd.zip` };
}
