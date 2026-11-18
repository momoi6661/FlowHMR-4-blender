import { $, el, show } from "./utils.js";
import { appError, message as m, setText, t } from "./i18n.js";
import * as api from "./api.js";

export function initPosePresets(viewer, openJob) {
  const dialog = $("posePresetDialog"), actorSelect = $("presetActor"), list = $("presetList");
  const name = $("presetName"), frame = $("presetFrame"), start = $("presetStart"), end = $("presetEnd");
  const fields = $("presetFields"), apply = $("presetApply"), open = $("posePresetOpen");
  let actors = [], items = [], busy = false, version = 0;
  const actor = () => actors.find((item) => item.id === actorSelect.value);
  const error = (value = "") => { setText($("presetError"), value); show("presetError", !!value); };
  function sync() {
    fields.disabled = actorSelect.disabled = busy;
    apply.disabled = busy || !actor() || !list.value;
    $("presetSave").disabled = busy || !actor();
    $("presetDelete").disabled = $("presetExport").disabled = busy || !list.value;
  }
  async function refresh(selected = list.value) {
    const result = await api.getPosePresets();
    items = result.items;
    $("presetDirectory").textContent = result.directory || "";
    list.replaceChildren(...items.map((item) => {
      const option = el("option", "", () => `${item.name} · ${t(`preset.${item.part}`)}`);
      option.value = item.id;
      return option;
    }));
    if (!items.length) {
      const empty = el("option", "", m("preset.empty"));
      empty.value = "";
      list.append(empty);
    }
    if (items.some((item) => item.id === selected)) list.value = selected;
    sync();
  }
  async function run(action) {
    if (busy) return;
    const stamp = version;
    busy = true; error(); sync();
    setText($("presetStatus"), m("preset.working"));
    try { await action(stamp); }
    catch (failure) { if (stamp === version) error(failure); }
    finally {
      busy = false;
      if (stamp === version) { setText($("presetStatus"), ""); sync(); }
    }
  }
  const integer = (input) => {
    const value = input.valueAsNumber;
    if (!Number.isSafeInteger(value) || value < Number(input.min) || value > Number(input.max)) {
      throw appError("preset.invalid_range");
    }
    return value;
  };
  function selectActor() {
    const value = actor();
    if (!value) return;
    for (const input of [frame, start, end]) input.max = value.frames;
    frame.value = Math.min(value.frames, Math.max(1, value.frame + 1));
    start.value = 1; end.value = value.frames;
    sync();
  }
  function currentFrame() {
    const current = viewer.getPoseContext().find((item) => item.id === actorSelect.value);
    if (!current || current.frame < 0 || current.frame >= current.frames) throw appError("preset.actor_absent");
    return current.frame + 1;
  }
  open.onclick = () => {
    actors = viewer.getPoseContext();
    actorSelect.replaceChildren(...actors.map((item) => {
      const option = el("option", "", m("person.number", { number: item.number }));
      option.value = item.id;
      return option;
    }));
    selectActor(); error(); dialog.showModal();
    void run(() => refresh());
  };
  actorSelect.onchange = selectActor;
  list.onchange = sync;
  $("presetRefresh").onclick = () => void run(() => refresh());
  $("presetClose").onclick = () => dialog.close();
  $("presetCurrent").onclick = () => { try { frame.value = currentFrame(); error(); } catch (e) { error(e); } };
  $("presetOneFrame").onclick = () => {
    try { start.value = end.value = currentFrame(); error(); } catch (e) { error(e); }
  };
  $("presetAllFrames").onclick = () => { start.value = 1; end.value = actor()?.frames ?? 1; };
  $("presetSave").onclick = () => void run(async () => {
    if (!name.value.trim()) throw appError("preset.name_required");
    const item = await api.savePosePreset({ job_id: actor().id, name: name.value.trim(),
      part: $("presetPart").value, frame: integer(frame) - 1 });
    await refresh(item.id);
    name.value = "";
  });
  $("presetDelete").onclick = () => {
    const item = items.find((entry) => entry.id === list.value);
    if (item && window.confirm(t("preset.delete_confirm", { name: item.name }))) {
      void run(async () => { await api.deletePosePreset(item.id); await refresh(); });
    }
  };
  function download(blob, filename) {
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("presetExport").onclick = () => void run(async () => {
    const preset = await api.getPosePreset(list.value);
    download(new Blob([JSON.stringify(preset, null, 2)], { type: "application/json" }), `pose-${preset.part}.json`);
  });
  $("presetImport").onclick = () => $("presetFile").click();
  $("presetFile").onchange = () => {
    const file = $("presetFile").files[0];
    $("presetFile").value = "";
    if (file) void run(async () => {
      if (file.size > 16384) throw appError("preset.invalid_file");
      let preset;
      try { preset = JSON.parse(await file.text()); } catch { throw appError("preset.invalid_file"); }
      const saved = await api.savePosePreset({ preset });
      await refresh(saved.id);
    });
  };
  apply.onclick = () => void run(async (stamp) => {
    const first = integer(start) - 1, last = integer(end) - 1;
    if (last < first) throw appError("preset.invalid_range");
    const result = await api.applyPosePreset(actor().id, { preset_id: list.value, start: first, end: last,
      strength: integer($("presetStrength")) / 100, blend_frames: integer($("presetBlend")) });
    if (stamp === version) { dialog.close(); await openJob(result.job_id); }
  });
  return {
    clear() { version++; dialog.close(); open.disabled = true; },
    setReady() { open.disabled = false; },
  };
}
