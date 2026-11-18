import { message as m, setAttr, setText } from "./i18n.js";
import { $, el, show } from "./utils.js";

const NS = "http://www.w3.org/2000/svg";
const restArms = {
  pmx: [[4, 23], [38, 23]], vrm: [[4, 10], [38, 10]],
  mixamo: [[4, 10], [38, 10]], metahuman: [[5, 20], [37, 20]],
};
function fallbackArt(family) {
  const [left, right] = restArms[family] ?? [[5, 20], [37, 20]];
  const icon = document.createElementNS(NS, "svg");
  icon.setAttribute("viewBox", "0 0 42 39");
  icon.setAttribute("aria-hidden", "true");
  const links = document.createElementNS(NS, "path");
  links.setAttribute("d", `M21 5v18M21 10L13 11 ${left.join(" ")}M21 10L29 11 ${right.join(" ")}M21 23l-6 3-2 10m8-13 6 3 2 10`);
  icon.append(links);
  for (const [x, y] of [[21, 5], [21, 10], [21, 23], [13, 11], [29, 11], left, right, [15, 26], [27, 26], [13, 36], [29, 36]]) {
    const joint = document.createElementNS(NS, "circle");
    joint.setAttribute("cx", x);
    joint.setAttribute("cy", y);
    joint.setAttribute("r", "1.35");
    icon.append(joint);
  }
  return icon;
}

/**
 * entries: [{id, name, kind, builtin, compatible, modified, thumbnail?}]
 * Callbacks receive the selected entry and may return a localized status.
 * Omit selected in set() to preserve this controller's current selection.
 */
export function createRetargetPresetGallery({ onLoad, onDelete, onReset, onSaveBuiltin, onSelect, signal }) {
  const host = $("retargetPresetGallery"), status = $("retargetPresetGalleryStatus");
  const load = $("retargetPresetLoad"), remove = $("retargetPresetDelete");
  const reset = $("retargetPresetReset"), save = $("retargetPresetSaveBuiltin");
  const request = $("retargetPresetConfirm"), confirm = $("retargetPresetConfirmButton");
  const message = $("retargetPresetConfirmMessage"), cancel = $("retargetPresetCancel");
  let entries = [], selection = "", externalBusy = false, working = false, allowBuiltin = false;
  let pending = null, notice = null, cards = new Map(), stamp = "";
  const options = { signal };
  const selectedEntry = () => entries.find(({ id }) => id === selection);
  const isBusy = () => externalBusy || working;
  const canSave = (entry) => typeof allowBuiltin === "function" ? allowBuiltin(entry) : allowBuiltin;

  function buttons() {
    const entry = selectedEntry(), busy = isBusy() || !!pending;
    for (const [id, card] of cards) {
      card.disabled = busy;
      card.setAttribute("aria-pressed", String(id === selection));
    }
    load.disabled = busy || !entry?.compatible;
    show(remove, !!entry && !entry.builtin);
    remove.disabled = busy || !entry || entry.builtin;
    show(reset, !!entry?.builtin);
    reset.disabled = busy || !entry?.builtin || !(entry.modified || entry.compatible);
    show(save, !!entry?.builtin);
    save.disabled = busy || !entry?.builtin || !entry.compatible || !canSave(entry);
    show(request, !!pending);
    cancel.disabled = confirm.disabled = isBusy();
    const detail = notice ?? (entry && !entry.compatible ? m("retarget.preset_incompatible") : null);
    setText(status, detail ?? "");
    show(status, !!detail && !pending);
  }

  function select(id, notify = false) {
    selection = entries.some((e) => e.id === id) ? id : "";
    pending = null;
    notice = null;
    buttons();
    if (notify) onSelect?.(selectedEntry());
  }

  function render() {
    const next = JSON.stringify(entries.map(({ id, name, kind, builtin, compatible, modified, thumbnail }) =>
      [id, name, kind, builtin, compatible, modified, thumbnail]));
    if (stamp !== next) {
      stamp = next;
      const focused = document.activeElement?.dataset.presetId;
      cards = new Map(entries.map((entry) => {
        const { id, name, kind, builtin, compatible, modified, thumbnail } = entry;
        const card = el("button", "retarget-preset-card");
        card.type = "button";
        card.dataset.presetId = id;
        card.dataset.kind = builtin ? id : kind;
        if (builtin) card.dataset.builtin = "";
        if (modified) card.dataset.modified = "";
        if (!compatible) card.dataset.incompatible = "";
        setAttr(card, "title", !compatible ? m("retarget.preset_incompatible_name", { name }) :
          builtin ? m(modified ? "retarget.preset_stock_modified" : "retarget.preset_stock", { name }) : name);
        const art = el("span", "retarget-preset-art"), fallback = fallbackArt(builtin ? id : kind);
        art.append(fallback);
        if (thumbnail) {
          const image = el("img");
          image.alt = "";
          image.loading = "lazy";
          image.decoding = "async";
          image.onload = () => { art.dataset.loaded = ""; };
          image.onerror = () => { image.remove(); };
          image.src = thumbnail;
          art.append(image);
        }
        card.append(art, el("span", "retarget-preset-caption", name));
        card.onclick = () => select(id, true);
        card.ondblclick = () => { if (entry.compatible && !isBusy()) void perform(onLoad, entry); };
        return [id, card];
      }));
      host.replaceChildren(...cards.values());
      const restoreFocus = cards.get(focused);
      if (restoreFocus && !isBusy() && !pending) restoreFocus.focus({ preventScroll: true });
    }
    buttons();
  }

  async function perform(action, entry, success) {
    if (!action || !entry || isBusy()) return;
    const focusWasHere = host.contains(document.activeElement) ||
      request.contains(document.activeElement) ||
      [load, remove, reset, save].includes(document.activeElement);
    working = true;
    notice = null;
    buttons();
    try {
      const result = await action(entry);
      const detail = typeof result === "string" || result?.key ? result : null;
      if (!signal?.aborted) notice = detail || (success ? m(success, { name: entry.name }) : null);
    } catch (cause) {
      if (!signal?.aborted) notice = cause?.i18n ?? m("retarget.preset_operation_failed", { detail: cause });
    } finally {
      working = false;
      if (!signal?.aborted) {
        pending = null;
        buttons();
        if (focusWasHere && (document.activeElement === document.body || document.activeElement.disabled ||
          request.contains(document.activeElement))) {
          (cards.get(selection) ?? cards.values().next().value)?.focus({ preventScroll:true });
        }
      }
    }
  }

  function ask(operation) {
    const entry = selectedEntry();
    if (!entry) return;
    pending = { operation, entry };
    const restoring = operation === "reset";
    setText(message, m(restoring ? "retarget.preset_reset_confirm" : "retarget.preset_delete_confirm", { name: entry.name }));
    setText(confirm, m(restoring ? "retarget.preset_reset" : "retarget.preset_delete"));
    confirm.classList.toggle("character-remove", !restoring);
    buttons();
    cancel.focus();
  }

  function dismiss() {
    if (isBusy()) return false;
    const entry = selectedEntry(), restore = pending?.operation === "reset" ? reset : remove;
    pending = null;
    buttons();
    if (!restore.disabled) restore.focus();
    else cards.get(entry?.id)?.focus();
    return true;
  }
  load.addEventListener("click", () => { if (!load.disabled) void perform(onLoad, selectedEntry()); }, options);
  save.addEventListener("click", () => {
    if (!save.disabled) void perform(onSaveBuiltin, selectedEntry(), "retarget.preset_saved_builtin");
  }, options);
  remove.addEventListener("click", () => { if (!remove.disabled) ask("delete"); }, options);
  reset.addEventListener("click", () => { if (!reset.disabled) ask("reset"); }, options);
  cancel.addEventListener("click", dismiss, options);
  confirm.addEventListener("click", () => {
    if (!pending || confirm.disabled) return;
    const { operation, entry } = pending;
    const resetting = operation === "reset";
    void perform(resetting ? onReset : onDelete, entry, resetting ? "retarget.preset_restored" : "retarget.preset_deleted");
  }, options);

  return {
    set(state) {
      entries = state.entries ?? entries;
      externalBusy = !!state.busy;
      allowBuiltin = state.canSaveBuiltin;
      if (state.selected !== undefined && state.selected !== selection) select(state.selected);
      if (selection && !selectedEntry()) selection = "";
      render();
    },
    select,
    dismiss,
    get confirming() { return !!pending; },
    get busy() { return isBusy() || !!pending; },
    get selection() { return selection; },
  };
}
