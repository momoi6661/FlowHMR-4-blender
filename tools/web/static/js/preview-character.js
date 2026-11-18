import { $, el, show } from "./utils.js";
import { appError, message as m, setText, t } from "./i18n.js";
import * as api from "./api.js";
import { readStoredPose, storedPose } from "./pose-offsets.js";
import { interpolateRig } from "./interpolate-rig.js";
import { normalizeRetargetProfile, profileIssue, targetSkeleton } from "./retarget-profile.js";
import { suggestBodyAliases, suggestHandAliases } from "./retarget-hand-names.js";
import { builtinFamily, builtinPresetChoice, builtinPresetKinds, builtinPresetName, makeBuiltinOverride, makeRetargetPreset,
  reusablePresets, skeletonPaths } from "./retarget-presets.js";

const kindOf = (name) => /\.(pmx|fbx|vrm)$/i.exec(name)?.[1]?.toLowerCase();
const emptyLibrary = () => ({ models: [], actors: {}, defaults: {}, presets: {}, retarget_presets: {}, builtin_retarget_presets: {} });
const slot = (actor) => String(actor.number ?? "single");
const editKey = (actor, id) => `flowhmr-character-edit:${actor.id}:${id}`;
const temporaryKey = (entry) => `${entry.asset.poseStorageKey}:${entry.actor.id}`;

export function initPreviewCharacter(getActors, onChange, onRestoreError) {
  const dialog = $("previewCharacterDialog"), button = $("previewCharacter");
  const folder = $("pmxFolder"), browse = $("pmxBrowse"), model = $("pmxFile");
  const input = $("characterModelFile"), browseFile = $("characterBrowse");
  const actorSelect = $("characterActor"), libraryToggle = $("characterLibraryToggle"), libraryGrid = $("characterLibraryGrid");
  const editCurrent = $("retargetCurrent"), retargetPrompt = $("characterRetargetPrompt");
  const startRetarget = $("characterRetargetStart"), cancelRetarget = $("characterRetargetCancel");
  const actorMenu = $("retargetActorMenu");
  const captures = new Set();
  const apply = $("pmxApply"), restore = $("pmxRestore"), error = $("pmxError");
  const remove = $("characterDelete"), confirm = $("characterDeleteConfirmButton");
  const cancelRemoval = $("characterDeleteCancel"), cancelDialog = $("characterDialogCancel");
  const picker = () => window.pywebview?.api?.choose_character_file;
  let files = [], candidates = [], catalog = emptyLibrary(), catalogReady = false, catalogRead = null;
  let focused = null, operation = null, restoring = null, picking = false, pickVersion = 0, removing = null;
  let selectedId = "", galleryOpen = false, tiles = new Map(), tileStamp = "", session = null, pendingImport = null;
  const refs = new Map(), writes = new Map();
  let remoteRead = null;
  let remotePending = false;
  editCurrent.setAttribute("aria-expanded","false");

  function currentActor() {
    const actors = getActors();
    return actors.find((actor) => actor.id === focused) ?? actors[0];
  }
  function assignment(actor) {
    const record = catalog.actors[actor.id];
    if (record) return { id: record.id, offsets: record.poses?.[record.id] };
    return catalog.defaults[slot(actor)] ?? {};
  }
  function selection() {
    const item = libraryChoice();
    return item ? { kind: item.kind, libraryId: item.id } : candidates[Number(model.value)];
  }
  const libraryChoice = () => catalog.models.find(({ id }) => id === selectedId);
  function setGallery(visible) {
    galleryOpen = visible;
    const expanded = galleryOpen && !!catalog.models.length;
    libraryToggle.setAttribute("aria-expanded", String(expanded));
    show(libraryGrid,expanded);
    if (expanded) for (const img of libraryGrid.querySelectorAll("img[data-src]")) {
      img.src=img.dataset.src;
      delete img.dataset.src;
    }
    if (expanded) void completeGallery();
  }
  libraryToggle.onclick=()=>setGallery(!galleryOpen);

  function fillLibraryThumbnail(entry) {
    const { asset, libraryId: id } = entry;
    if (!id || captures.has(id) || catalog.models.find((item) => item.id === id)?.has_thumbnail) return;
    captures.add(id);
    retain(asset);
    setTimeout(async () => {
      try {
        const { renderCharacterThumbnail } = await import("./character-thumbnail.js");
        const png = await renderCharacterThumbnail(asset);
        await api.saveCharacterThumbnail(id, png);
        const item = catalog.models.find((model) => model.id === id);
        if (item) item.has_thumbnail = true;
        if (dialog.open) renderLibrary(selectedId);
      } catch {
        // A preview image is optional; character selection must still work.
      } finally {
        captures.delete(id);
        release(asset);
      }
    }, 0);
  }
  let fillingGallery=false;
  async function completeGallery() {
    if(fillingGallery || !galleryOpen)return;
    fillingGallery=true;
    const run=new AbortController();
    try {
      // Characters saved before thumbnails existed are rendered one at a
      // time. Opening the gallery does not wait for them or load all at once.
      const missing=catalog.models.filter((item)=>!item.has_thumbnail && !captures.has(item.id)).slice(0,8);
      for(const item of missing) {
        if(!galleryOpen || !dialog.open || operation || picking)break;
        captures.add(item.id);
        let asset;
        try {
          asset=await loadAsset({kind:item.kind,libraryId:item.id},run.signal);
          if(!galleryOpen || !dialog.open)break;
          const {renderCharacterThumbnail}=await import("./character-thumbnail.js");
          const png=await renderCharacterThumbnail(asset);
          await api.saveCharacterThumbnail(item.id,png);
          item.has_thumbnail=true;
          const current=catalog.models.find(({id})=>id===item.id);
          if(current)current.has_thumbnail=true;
          if(dialog.open)renderLibrary(selectedId);
        } catch { /* retain the format placeholder if a model cannot be opened */ }
        finally {
          if(asset)release(asset);
          captures.delete(item.id);
        }
      }
    } finally { fillingGallery=false; }
  }
  function setError(cause = "") {
    setText(error, cause);
    show(error, !!cause);
  }
  function clearPrompt() {
    if (pendingImport) {
      dispose(pendingImport.native);
      release(pendingImport.asset);
    }
    pendingImport = null;
    show(retargetPrompt,false);
  }
  function setActorMenu(visible) {
    show(actorMenu,visible);
    editCurrent.setAttribute("aria-expanded",String(visible));
  }
  window.addEventListener("pointerdown",(event)=>{
    if(!actorMenu.contains(event.target) && !editCurrent.contains(event.target))setActorMenu(false);
  });
  actorMenu.addEventListener("keydown",(event)=>{
    if(event.key==="Escape"){event.stopPropagation();setActorMenu(false);editCurrent.focus();}
  });
  function sync() {
    const actor = currentActor(), busy = !!(operation || restoring || catalogRead || picking || removing);
    browse.disabled = browseFile.disabled = model.disabled = actorSelect.disabled = busy;
    libraryToggle.disabled = busy || !catalog.models.length;
    for (const tile of tiles.values()) tile.disabled = busy;
    restore.disabled = busy || !actor || (!actor.character && !assignment(actor).id);
    apply.disabled = busy || !!pendingImport || !actor || !selection();
    startRetarget.disabled = cancelRetarget.disabled = busy;
    remove.disabled = busy || !libraryChoice();
    cancelDialog.disabled = confirm.disabled = cancelRemoval.disabled = !!(removing && operation);
    show(button, !!actor);
    const characters=getActors().filter((person)=>person.character);
    show(editCurrent, !!characters.length && !session);
    editCurrent.disabled = busy || !!session;
    if(busy || session || !characters.length)setActorMenu(false);
    show(model, candidates.length > 1);
    show(browse, !picker());
    if (remotePending && !remoteRead && !busy && !writes.size) queueMicrotask(syncRemoteCharacters);
  }
  function status() {
    const actor = currentActor(), candidate = candidates[Number(model.value)];
    if (candidate) return candidate.path;
    const asset = actor?.character?.asset;
    if (!asset) return actor && assignment(actor).id ? m("server.character_library_missing") : m("character.current", { name: "SMPL-H" });
    const { name, textureReport: { missing, failed }, rest } = asset;
    return () => [
      actor.character.libraryId === selectedId ? "" : t("character.current", { name }),
      missing + failed ? t("character.textures", { count: missing + failed }) : "",
      rest.skinning?.approximate ? t("character.skinning") : "",
    ].filter(Boolean).join(" · ");
  }
  function renderPeople() {
    const actors = getActors();
    actorSelect.replaceChildren(...actors.map((actor) => {
      const option = el("option", "", () => `${t("person.number", { number: actor.number })} · ${actor.character?.asset.name ?? "SMPL-H"}`);
      option.value = actor.id;
      return option;
    }));
    actorSelect.value = currentActor()?.id ?? "";
    show("characterActorRow", actors.length > 1);
  }
  function renderLibrary(id = currentActor()?.character?.libraryId ?? "") {
    selectedId = catalog.models.some((item) => item.id === id) ? id : "";
    const item = libraryChoice();
    setText($("characterLibraryLabel"),item?.name ?? m(catalog.models.length ? "character.library_choose" : "character.library_empty"));
    const stamp = JSON.stringify(catalog.models.map((item)=>[item.id,item.has_thumbnail,item.name]));
    if (stamp !== tileStamp) {
      tileStamp=stamp;
      tiles=new Map(catalog.models.map(({id,name,filename,has_thumbnail})=>{
        const card=el("button","character-tile"); card.type="button"; card.dataset.characterId=id; card.title=filename;
        const art=el("span","character-tile-preview");
        const placeholder=el("span","",filename.split(".").at(-1).toUpperCase());
        art.append(placeholder);
        if (has_thumbnail) {
          const image=el("img");image.alt="";image.decoding="async";
          image.onload=()=>{ show(placeholder,false);show(image,true); };
          image.onerror=()=>{ show(image,false);show(placeholder,true); };
          show(image,false);image.dataset.src=api.characterThumbnailUrl(id);art.append(image);
        }
        card.append(art,el("span","character-tile-name",name));
        card.onclick=()=>{
          clearPrompt();files=[];resetCandidates();setError();renderLibrary(id);sync();
        };
        return [id,card];
      }));
      libraryGrid.replaceChildren(...tiles.values());
    }
    for (const [i,tile] of tiles) tile.setAttribute("aria-pressed",String(i===selectedId));
    setGallery(galleryOpen);
    setText($("pmxStatus"), status());
  }
  function resetCandidates(items = []) {
    candidates = items;
    model.replaceChildren(...items.map((item, index) => {
      const option = el("option", "", item.path);
      option.value = String(index);
      return option;
    }));
  }
  function refresh() {
    renderPeople();
    renderLibrary();
    sync();
  }
  function notifyChange() {
    if (dialog.open) refresh();
    onChange();
    window.dispatchEvent(new Event("flowhmr:characterchange"));
  }
  function focus(id) {
    if (!getActors().some((actor) => actor.id === id)) return;
    focused = id;
    onChange();
  }

  function retain(asset) {
    refs.set(asset, (refs.get(asset) ?? 0) + 1);
    return asset;
  }
  function release(asset) {
    const count = refs.get(asset) - 1;
    if (count) refs.set(asset, count);
    else {
      refs.delete(asset);
      asset.dispose();
    }
  }
  function dispose(entry) {
    if (!entry) return;
    entry.asset.releaseClone(entry.mesh);
    release(entry.asset);
  }
  function attach(actor, entry) {
    dispose(actor.character);
    actor.character = entry;
    if (entry) actor.group.add(entry.mesh);
    notifyChange();
    if (entry) fillLibraryThumbnail(entry);
  }
  function finishRemoval() {
    removing = null;
    show("characterDeleteConfirm", false);
    sync();
  }

  async function loadAsset(selected, signal) {
    const used = selected.libraryId && getActors().find(({ character }) => character?.libraryId === selected.libraryId);
    if (used) return retain(used.character.asset);
    const load = selected.kind === "fbx" ? (await import("./fbx-character-asset.js")).loadFbxCharacter
      : selected.kind === "vrm" ? (await import("./vrm-asset.js")).loadVrmCharacter
        : (await import("./pmx-asset.js")).loadPmxAsset;
    const options = { signal };
    const native = selected.libraryId ? await api.openLibraryCharacter(selected.libraryId, signal) : selected.native;
    let file = selected.file, resources = files;
    if (native) {
      file = await api.getCharacterFile(native, signal);
      resources = [file];
      options.readTexture = (path) => api.getCharacterTexture(native.ticket, path, signal);
      options.textureUrl = (path) => api.characterResourceUrl(native.ticket, path);
    }
    const asset = await load(resources, file, options);
    if (!native && asset.kind === "fbx") asset.sourceFile = file;
    asset.poseStorageKey = `flowhmr-pose-offsets:${selected.kind}:${file.name}:${file.size}:${asset.name}`;
    return retain(asset);
  }
  function rememberedPose(actor, id, asset) {
    return (actor.character && (actor.character.asset === asset || id && actor.character.libraryId === id) && actor.character.poseOffsets) ||
      ((id ? readStoredPose(editKey(actor,id)) ?? catalog.actors[actor.id]?.poses?.[id] ?? catalog.presets[id]
        : readStoredPose(temporaryKey({asset,actor}))) ?? readStoredPose(asset.poseStorageKey));
  }
  async function buildEntry(actor, asset, motion, id, offsets, signal) {
    signal.throwIfAborted();
    retain(asset);
    let mesh;
    try {
      const isPmx=asset.kind==="pmx";
      const retarget=await (isPmx ? import("./pmx-retarget.js") : import("./fbx-character-retarget.js"));
      signal.throwIfAborted();
      mesh=asset.clone();
      const solver=isPmx ? retarget.createPmxRetarget(mesh,asset.rest,motion,{poseOffsets:offsets})
        : retarget.createFbxCharacterRetarget(mesh,asset,motion,{poseOffsets:offsets,libraryId:id});
      const rig=interpolateRig(solver,mesh,motion.frames);
      mesh.traverse((o)=>{ if(o.isMesh)o.castShadow=o.receiveShadow=true; });
      const entry={actor,asset,mesh,rig,motion,libraryId:id,poseOffsets:null};
      applyPose(entry,offsets);
      return entry;
    } catch (cause) {
      if(mesh)asset.releaseClone(mesh);
      release(asset);
      throw cause;
    }
  }
  async function motionFor(actor, signal) {
    const motion=actor.character?.motion ?? await api.getCharacterMotion(actor.id,signal);
    if (motion.frames !== actor.meta.num_frames) throw appError("server.pmx_motion");
    return motion;
  }
  async function prepare(actor, selected, signal, explicit) {
    const asset = await loadAsset(selected, signal);
    try {
      const motion=await motionFor(actor,signal);
      const id=selected.libraryId ?? null;
      const offsets=explicit === undefined ? rememberedPose(actor,id,asset) : explicit;
      return await buildEntry(actor,asset,motion,id,offsets,signal);
    } finally { release(asset); }
  }

  async function describe(asset, entry, signal, frames = false) {
    const mesh=asset.clone();
    try {
      const bones=targetSkeleton(asset,mesh,{frames});
      let suggested=Array(52).fill(-1);
      const pmx=asset.kind==="pmx";
      const mod=await (pmx ? import("./pmx-retarget.js") : import("./fbx-character-retarget.js"));
      signal.throwIfAborted();
      try { suggested=Array.from(pmx ? mod.mapBones(asset.rest.bones) : mod.suggestedFbxMap(mesh,asset,bones)); }
      catch { /* a valid imported skeleton can still be mapped manually */ }
      const builtIn = entry && !entry.poseOffsets?.retarget;
      const native = builtIn ? normalizeRetargetProfile({version:1,map:suggested,
        root:entry.rig.vmd?.rootBoneIndex,ik:entry.rig.nativeControllers}) : null;
      const fallback=pmx ? suggestBodyAliases(bones,suggested,"pmx") : suggested;
      return { kind:asset.kind, bones, skeleton:skeletonPaths(bones), suggested:suggestHandAliases(bones,fallback), native,
        builtin:builtIn ? builtinFamily(asset) : null, nativeIk:!!entry?.rig.nativeIkAvailable };
    } finally { asset.releaseClone(mesh); }
  }

  async function automaticCharacter(actor, asset, motion, id, signal) {
    const saved=rememberedPose(actor,id,asset);
    let native=null;
    try {
      native=await buildEntry(actor,asset,motion,id,saved,signal);
    } catch (cause) {
      if(cause.i18n?.key!=="retarget.mapping_required")throw cause;
    }
    if(saved && native)return {entry:native};
    const records=catalog.retarget_presets;
    const family=native ? builtinFamily(asset) : null;
    const override=family && catalog.builtin_retarget_presets?.[family];
    if(!override && !Object.keys(records ?? {}).length)return {entry:native, alternatives:0};
    try {
      const metadata=await describe(asset,native,signal);
      const compatible=reusablePresets(records,metadata);
      if(compatible.length===1) {
        try {
          const entry=await buildEntry(actor,asset,motion,id,compatible[0].offsets,signal);
          dispose(native);
          return {entry};
        } catch(cause) {
          // A path match still needs to pass the actual solver and bind checks.
          if(signal.aborted)throw cause;
        }
      }
      if(compatible.length>1)return {entry:null, alternatives:compatible.length, native};
      if(override) {
        const choice=builtinPresetChoice(family,metadata,override);
        if(choice && !choice.skippedOverride) {
          try {
            const entry=await buildEntry(actor,asset,motion,id,choice.offsets,signal);
            dispose(native);
            return {entry};
          } catch(cause) { signal.throwIfAborted(); /* continue with the stock mapping */ }
        }
      }
      return {entry:native, alternatives:0};
    } catch(cause) { dispose(native);throw cause; }
  }

  function writeFor(actor, write) {
    const next = (writes.get(actor.id) ?? Promise.resolve()).catch(() => {}).then(write);
    writes.set(actor.id, next);
    const clear = () => {
      if (writes.get(actor.id) === next) writes.delete(actor.id);
      if (remotePending) void syncRemoteCharacters();
    };
    next.then(clear, clear);
    return next;
  }
  function applyPose(entry, offsets) {
    entry.poseOffsets = entry.rig.setPoseOffsets(offsets);
  }
  function editPose(entry, offsets) {
    applyPose(entry, offsets);
    const { actor, libraryId, poseOffsets } = entry;
    const key = libraryId ? editKey(actor, libraryId) : temporaryKey(entry);
    const record = storedPose(poseOffsets);
    try { localStorage.setItem(key, record); } catch { /* server still saves the pose */ }
    if (!libraryId) return;
    // Draw every slider move, but keep only the newest edit while a write is in flight.
    entry.queuedPose = { poseOffsets, record };
    if (entry.writingPose) return;
    entry.writingPose = true;
    void writeFor(actor, async () => {
      try {
        while (entry.queuedPose) {
          const next = entry.queuedPose;
          entry.queuedPose = null;
          await api.saveActorCharacter(actor, libraryId, next.poseOffsets, false);
          try {
            if (localStorage.getItem(key) === next.record) localStorage.removeItem(key);
          } catch { /* the server copy is already saved */ }
        }
      } finally {
        entry.writingPose = false;
      }
    }).catch((cause) => { if (actor.character === entry) onRestoreError(cause); });
  }

  // MCP edits use the same server preferences as the GUI. Only synchronize
  // idle actors; local pending edits keep ownership until their save completes.
  const remoteSignatures = new Map();
  async function syncRemoteCharacters() {
    const actors = getActors();
    if (!actors.length || remoteRead || operation || session || restoring || catalogRead || picking || writes.size) return;
    remotePending = false;
    const read = remoteRead = new AbortController();
    try {
      const next = await api.getCharacterLibrary(actors, read.signal);
      read.signal.throwIfAborted();
      if (actors.some((actor) => !getActors().includes(actor)) || operation || session || picking || writes.size) return;
      catalog = next;
      if (dialog.open) { renderLibrary(selectedId); sync(); }
      for (const actor of actors) {
        const saved = assignment(actor);
        const signature = JSON.stringify(saved);
        if (remoteSignatures.get(actor.id) === signature) continue;
        if (saved.id && readStoredPose(editKey(actor, saved.id))) continue;
        if (!saved.id) {
          if (actor.character?.libraryId) attach(actor, null);
        } else if (actor.character?.libraryId === saved.id &&
                   JSON.stringify(actor.character.poseOffsets?.retarget) === JSON.stringify(saved.offsets?.retarget)) {
          applyPose(actor.character, saved.offsets);
          notifyChange();
        } else {
          const item = catalog.models.find((model) => model.id === saved.id);
          if (!item) throw appError('server.character_library_missing');
          const entry = await prepare(actor, { kind: item.kind, libraryId: item.id }, read.signal, saved.offsets ?? null);
          if (read.signal.aborted || !getActors().includes(actor) || operation || session || picking || writes.size) {
            dispose(entry);
            return;
          }
          attach(actor, entry);
        }
        remoteSignatures.set(actor.id, signature);
      }
    } catch (cause) {
      if (!read.signal.aborted) onRestoreError(cause);
    } finally {
      if (remoteRead === read) remoteRead = null;
      if (remotePending) void syncRemoteCharacters();
    }
  }
  window.addEventListener('flowhmr:server-update', event => {
    if (['characters', 'resync'].includes(event.detail.kind)) {
      remotePending = true;
      void syncRemoteCharacters();
    }
  });
  window.addEventListener('pagehide', () => remoteRead?.abort());

  async function open(id) {
    session?.close();
    if (dialog.open || session || !currentActor()) return;
    if (id) focus(id);
    setError();
    setGallery(true);
    refresh();
    dialog.showModal();
    if (restoring) return;
    const read = catalogRead = new AbortController();
    sync();
    try {
      await Promise.allSettled(getActors().map((actor) => writes.get(actor.id)));
      read.signal.throwIfAborted();
      catalog = await api.getCharacterLibrary(getActors(), read.signal);
      catalogReady = true;
      refresh();
    } catch (cause) {
      if (!read.signal.aborted && picker()) setError(cause);
    } finally {
      if (catalogRead === read) {
        catalogRead = null;
        sync();
      }
    }
  }
  button.onclick = () => void open();
  browse.onclick = () => folder.click();
  window.addEventListener("pywebviewready", sync);
  model.onchange = () => { clearPrompt();setText($("pmxStatus"), status());sync(); };
  actorSelect.onchange = () => {
    focus(actorSelect.value);
    clearPrompt();
    files = [];
    resetCandidates();
    setError();
    renderLibrary();
    sync();
  };
  remove.onclick = () => {
    if (remove.disabled) return;
    clearPrompt();
    removing = libraryChoice();
    setError();
    setText($("characterDeleteMessage"), m("character.library_confirm", { name: removing.name }));
    show("characterDeleteConfirm");
    sync();
    cancelRemoval.focus();
  };
  cancelRemoval.onclick = () => {
    finishRemoval();
    remove.focus();
  };
  confirm.onclick = async () => {
    if (!removing || operation) return;
    const item = removing, run = operation = new AbortController();
    setText($("pmxStatus"), m("character.library_deleting"));
    sync();
    try {
      await Promise.allSettled(writes.values());
      run.signal.throwIfAborted();
      const result = await api.deleteLibraryCharacter(item.id, getActors());
      run.signal.throwIfAborted();
      catalog = result.catalog;
      try {
        for (const key of Object.keys(localStorage)) {
          if (key.startsWith("flowhmr-character-edit:") && key.endsWith(`:${item.id}`)) localStorage.removeItem(key);
        }
      } catch { /* The server preferences are already updated. */ }
      for (const actor of getActors()) {
        if (actor.character?.libraryId !== item.id) continue;
        dispose(actor.character);
        actor.character = null;
      }
      notifyChange();
      setText($("pmxStatus"), m("character.library_deleted", { name: item.name }));
      if (result.cleanup_pending) setError(m("character.library_cleanup_pending"));
    } catch (cause) {
      if (!run.signal.aborted) {
        setText($("pmxStatus"), status());
        setError(cause);
      }
    } finally {
      if (operation === run) {
        operation = null;
        finishRemoval();
        if (dialog.open) (libraryToggle.disabled ? browseFile : libraryToggle).focus();
      }
    }
  };
  function chooseFiles(control) {
    if (operation || picking || !control.files.length) return;
    clearPrompt();
    files = Array.from(control.files);
    control.value = "";
    resetCandidates(files.flatMap((file) => {
      const kind = kindOf(file.name);
      return kind ? [{ kind, file, path: file.webkitRelativePath || file.name }] : [];
    }).sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true })));
    setError(candidates.length ? "" : m("character.no_model"));
    renderLibrary("");
    sync();
  }
  folder.onchange = () => chooseFiles(folder);
  input.onchange = () => chooseFiles(input);
  browseFile.onclick = async () => {
    const pick = picker();
    if (!pick) {
      input.click();
      return;
    }
    const version = ++pickVersion;
    picking = true;
    sync();
    try {
      const choice = await pick.call(window.pywebview.api);
      if (version !== pickVersion || !dialog.open || !choice) return;
      if (["character_picker_session", "character_picker_invalid", "character_picker_retry"].includes(choice.error_code)) {
        throw appError(`server.${choice.error_code}`);
      }
      const kind = typeof choice.name === "string" ? kindOf(choice.name) : null;
      if (!kind || typeof choice.ticket !== "string") throw appError("server.character_file");
      clearPrompt();
      files = [];
      resetCandidates([{ kind, path: choice.name, native: choice }]);
      setError();
      renderLibrary("");
    } catch (cause) {
      if (version === pickVersion && dialog.open) setError(cause);
    } finally {
      if (version === pickVersion) {
        picking = false;
        sync();
      }
    }
  };
  restore.onclick = async () => {
    const actor = currentActor();
    if (!actor || operation) return;
    const run = operation = new AbortController();
    clearPrompt();
    setError();
    sync();
    try {
      if (catalogReady || actor.character?.libraryId) {
        await writeFor(actor, () => api.saveActorCharacter(actor, null, null, true));
        catalog.actors[actor.id] = { ...catalog.actors[actor.id], id: null };
        catalog.defaults[slot(actor)] = { id: null };
      }
      run.signal.throwIfAborted();
      attach(actor, null);
      dialog.close("restore");
    } catch (cause) {
      if (!run.signal.aborted) setError(cause);
    } finally {
      if (operation === run) operation = null;
      sync();
    }
  };
  dialog.addEventListener("cancel", (event) => {
    if (pendingImport) {
      event.preventDefault();
      if (!operation) cancelRetarget.click();
      return;
    }
    if (!removing) return;
    event.preventDefault();
    if (!operation) cancelRemoval.click();
  });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    if (!session) operation?.abort();
    catalogRead?.abort();
    operation = catalogRead = null;
    clearPrompt();
    pickVersion++;
    picking = false;
    files = [];
    resetCandidates();
    folder.value = input.value = "";
    setGallery(false);
    finishRemoval();
  });
  async function saveEntry(entry, native, signal, preset) {
    let id=entry.libraryId;
    let savedPreset;
    if (native && !id) {
      const item=await api.saveLibraryCharacter(native.ticket,entry.asset.name);
      if(!catalog.models.some(({id})=>id===item.id))catalog.models.push(item);
      entry.libraryId=id=item.id;
      entry.rig.rememberLibraryId?.(id);
    }
    signal.throwIfAborted();
    if (id) {
      const actor=entry.actor;
      const result=await writeFor(actor,()=>api.saveActorCharacter(actor,id,entry.poseOffsets,true,preset));
      savedPreset=result.retarget_preset;
      catalogReady=true;
      catalog.presets[id]=entry.poseOffsets;
      catalog.actors[actor.id] ??= {id,poses:{}};
      catalog.actors[actor.id].id=id;
      catalog.actors[actor.id].poses[id]=entry.poseOffsets;
      catalog.defaults[slot(actor)]={id,offsets:entry.poseOffsets};
      try { localStorage.removeItem(editKey(actor,id)); } catch { /* server holds the committed settings */ }
    } else {
      if(preset)savedPreset=await api.saveRetargetPreset(preset);
      try { localStorage.setItem(temporaryKey(entry),storedPose(entry.poseOffsets)); } catch { /* session still runs */ }
    }
    if(savedPreset) {
      (catalog.retarget_presets??={})[savedPreset.id]=savedPreset;
      // Desktop presets inherit an independent copy from the character
      // thumbnail. Browser imports do not have an on-disk character bundle.
      if(!id && !savedPreset.has_thumbnail) {
        const asset=retain(entry.asset);
        setTimeout(async()=>{
          try {
            const {renderCharacterThumbnail}=await import("./character-thumbnail.js");
            await api.saveRetargetPresetThumbnail(savedPreset.id,await renderCharacterThumbnail(asset));
            if(catalog.retarget_presets?.[savedPreset.id])catalog.retarget_presets[savedPreset.id].has_thumbnail=true;
          } catch { /* thumbnail is optional */ }
          finally { release(asset); }
        },0);
      }
    }
  }

  async function editMapping(actor, selected, signal, loaded = null) {
    let asset, keep=false, trial=null;
    const before=actor.character;
    try {
      asset=retain(loaded?.asset ?? before.asset);
      const motion=loaded?.motion ?? await motionFor(actor,signal);
      const id=selected?.libraryId ?? (selected ? null : before.libraryId);
      const saved=rememberedPose(actor,id,asset);
      const { openRetargetEditor }=await import("./retarget-editor.js");
      if(!selected) {
        try { catalog=await api.getCharacterLibrary(getActors(),signal); }
        catch { signal.throwIfAborted(); /* cached presets remain usable */ }
      }
      let nativeEntry=selected ? loaded?.native : before.poseOffsets?.retarget ? null : before;
      if (!nativeEntry && builtinFamily(asset)) {
        // A manual map can coexist with a stock profile. Keep the stock
        // bindings available for loading or restoring the preset.
        try { nativeEntry=await buildEntry(actor,asset,motion,id,null,signal); }
        catch(cause) { signal.throwIfAborted(); /* manual presets remain available */ }
      }
      let metadata;
      try { metadata=await describe(asset,nativeEntry,signal,true); }
      finally { if(nativeEntry && nativeEntry!==before && nativeEntry!==loaded?.native)dispose(nativeEntry); }
      const { bones, native, suggested, skeleton }=metadata;
      function presetOptions() {
        const overrides=catalog.builtin_retarget_presets ?? {};
        const compatible=new Map(reusablePresets(catalog.retarget_presets,metadata).map((item)=>[item.preset.id,item]));
        const builtin=Object.entries(builtinPresetKinds).map(([id,kind])=>{
          const override=overrides[id];
          const picked=builtinPresetChoice(id,metadata,override);
          return { id, name:builtinPresetName[id], kind, builtin:true, compatible:!!picked, modified:!!override, picked };
        });
        const stored=Object.values(catalog.retarget_presets ?? {}).sort((a,b)=>a.name.localeCompare(b.name));
        return [...builtin,...stored.map((preset)=>({
          id:preset.id, name:preset.name, kind:preset.kind, builtin:false,
          compatible:compatible.has(preset.id), picked:compatible.get(preset.id), preset,
          thumbnail:preset.has_thumbnail ? api.retargetPresetThumbnailUrl(preset.id) : null,
        }))];
      }
      async function deletePreset(presetId) {
        await api.deleteRetargetPreset(presetId);
        delete catalog.retarget_presets?.[presetId];
      }
      async function resetBuiltin(family) {
        await api.resetBuiltinRetargetPreset(family);
        delete catalog.builtin_retarget_presets?.[family];
        return builtinPresetChoice(family,metadata,null);
      }
      async function saveBuiltin(family, draft, offsets, lifetime) {
        if(family!==metadata.builtin)throw appError("retarget.preset_incompatible");
        const tested=await buildEntry(actor,asset,motion,id,offsets,lifetime);
        try {
          const value=makeBuiltinOverride(family,draft,tested.poseOffsets,skeleton);
          lifetime.throwIfAborted();
          (catalog.builtin_retarget_presets??={})[family]=await api.saveBuiltinRetargetPreset(family,value);
        } finally { dispose(tested); }
      }
      const map=normalizeRetargetProfile(saved?.retarget);
      const start=map && !profileIssue(map,bones,asset.kind) ? map : native;
      const editable=before?.asset===asset && saved ? before.rig.editableCorrections?.(saved) : null;
      const initialPose=editable ? {...saved,rotations:{}} : saved ?? nativeEntry?.poseOffsets;
      const corrections=editable ?? (map ? saved?.retarget : saved?.boneOffsets);
      let disposed=false;
      let trialBinding;
      const signature=(profile)=>profile ? JSON.stringify([profile.map,profile.root,profile.ik]) : "builtin";
      const finish=(rollback)=>{
        if(disposed)return;
        disposed=true;
        session=null;
        if(rollback && trial && actor.character===trial){
          dispose(trial);
          actor.character=before;
          if(before)actor.group.add(before.mesh);
        }
        release(asset);
        if(getActors().includes(actor))notifyChange();
        sync();
      };
      function updateTrial(offsets,lifetime) {
        lifetime.throwIfAborted();
        if (trial && actor.character===trial && trialBinding===signature(offsets.retarget)) {
          applyPose(trial,offsets);
          onChange(actor);
          return true;
        }
        return false;
      }
      async function put(offsets, lifetime, final, presetRequest) {
        if(!final && updateTrial(offsets,lifetime)) return trial.rig.proportions;
        const next=await buildEntry(actor,asset,motion,id,offsets,lifetime);
        try {
          lifetime.throwIfAborted();
          if(final) {
            const preset=presetRequest && makeRetargetPreset({...presetRequest,kind:asset.kind,
              builtin:offsets.retarget ? null : metadata.builtin,offsets:next.poseOffsets,skeleton});
            await saveEntry(next,selected?.native,lifetime,preset);
          }
          lifetime.throwIfAborted();
        } catch (cause) { dispose(next);throw cause; }
        if(trial)dispose(trial);
        else before?.mesh.removeFromParent();
        trial=next;
        trialBinding=signature(offsets.retarget);
        actor.character=next;
        actor.group.add(next.mesh);
        notifyChange();
        if(final) {
          if(before)dispose(before);
          fillLibraryThumbnail(next);
          finish(false);
        }
        return next.rig.proportions;
      }
      session=openRetargetEditor({asset,bones,sourceRest:motion.rest, native, skeleton, presetOptions,
        builtinPreset:metadata.builtin, deletePreset, resetBuiltin, saveBuiltin,
        initial:start && {...start,rotations:corrections?.rotations ?? {},translations:corrections?.translations ?? {}},
        initialPose, useBuiltinInitially:!!native && !map,nativeIk:!!nativeEntry?.rig.nativeIkAvailable,
        suggestedMap:suggested,build:(offsets,lifetime)=>put(offsets,lifetime,false),
        applyLive:updateTrial,
        accept:(offsets,lifetime,preset)=>put(offsets,lifetime,true,preset),cancel:()=>finish(true)});
      if(!session)return;
      keep=true;
      if(dialog.open)dialog.close("retarget");
      sync();
      onChange();
    } finally {
      if(asset && !keep)release(asset);
    }
  }

  async function editActor(actor) {
    if(!actor?.character || operation || session)return;
    setActorMenu(false);
    focus(actor.id);
    const run=operation=new AbortController();sync();
    try { await editMapping(actor,null,run.signal); }
    catch (cause) { if(!run.signal.aborted)onRestoreError(cause); }
    finally { if(operation===run)operation=null;sync(); }
  }
  editCurrent.onclick=()=>{
    const actors=getActors().filter((actor)=>actor.character);
    if (actors.length===1) { void editActor(actors[0]);return; }
    actorMenu.replaceChildren(...actors.map((actor)=>{
      const button=el("button","",()=>`${t("person.number",{number:actor.number})} · ${actor.character.asset.name}`);
      button.type="button";button.onclick=()=>void editActor(actor);return button;
    }));
    const opening=actorMenu.classList.contains("hidden");
    setActorMenu(opening);
    if(opening)actorMenu.querySelector("button")?.focus();
  };

  cancelRetarget.onclick=()=>{
    clearPrompt();setText($("pmxStatus"),status());sync();apply.focus();
  };
  startRetarget.onclick=async()=>{
    if(!pendingImport || operation || session)return;
    const pending=pendingImport, run=operation=new AbortController();
    pendingImport=null;show(retargetPrompt,false);sync();
    try { await editMapping(pending.actor,pending.selected,run.signal,pending); }
    catch(cause) { if(!run.signal.aborted)setError(cause); }
    finally {
      dispose(pending.native);release(pending.asset);
      if(operation===run)operation=null;
      setText($("pmxStatus"),status());sync();
    }
  };

  dialog.querySelector("form").addEventListener("submit", async (event) => {
    if (event.submitter!==apply) return;
    event.preventDefault();
    let selected = selection();
    const actor = currentActor();
    if (operation || pendingImport || session || restoring || !selected || !actor) return;
    const run = operation = new AbortController();
    let entry=null, asset=null;
    setError();
    setText($("pmxStatus"), m("character.loading"));
    sync();
    try {
      asset=await loadAsset(selected,run.signal);
      if (selected.native && catalog.models.some(({filename})=>filename===selected.native.name)) {
        const known=await api.lookupLibraryCharacter(selected.native.ticket,run.signal);
        if(known)selected={...selected,libraryId:known.id};
      }
      const motion=await motionFor(actor,run.signal);
      const result=await automaticCharacter(actor,asset,motion,selected.libraryId??null,run.signal);
      entry=result.entry;
      if(run.signal.aborted) { dispose(result.native);run.signal.throwIfAborted(); }
      if(!entry) {
        pendingImport={actor,selected,asset:retain(asset),motion,native:result.native,alternatives:result.alternatives};
        setText($("characterRetargetMessage"),result.alternatives ?
          m("retarget.choose_preset_prompt",{name:asset.name,count:result.alternatives}) :
          m("retarget.mapping_prompt",{name:asset.name}));
        show(retargetPrompt);
        setText($("pmxStatus"),status());
        sync();queueMicrotask(()=>{if(!startRetarget.disabled && pendingImport)startRetarget.focus();});
        return;
      }
      run.signal.throwIfAborted();
      await saveEntry(entry,selected.native,run.signal);
      run.signal.throwIfAborted();
      attach(actor, entry);
      entry = null;
      dialog.close("apply");
    } catch (cause) {
      if (!run.signal.aborted) {
        setText($("pmxStatus"), status());
        const code = ["no_skin", "unsupported_rig", "too_large", "parse"].includes(cause?.code) && selected.kind === "fbx"
          ? `character.fbx_${cause.code}` : "";
        setError(code ? m(code) : m("character.failed", { detail: cause }));
      }
    } finally {
      dispose(entry);
      if(asset)release(asset);
      if (operation === run) operation = null;
      sync();
    }
  });

  return {
    open,
    focus,
    get focused() { return currentActor(); },
    get active() { return currentActor()?.character?.asset ?? getActors().find((actor) => actor.character)?.character.asset; },
    releaseActors(actors) {
      session?.close();
      remoteRead?.abort();
      remoteSignatures.clear();
      operation?.abort();
      restoring?.abort();
      catalogRead?.abort();
      operation = restoring = catalogRead = null;
      clearPrompt();
      setActorMenu(false);
      if (dialog.open) dialog.close("cancel");
      for (const actor of actors) {
        dispose(actor.character);
        actor.character = null;
      }
    },
    async showForJob() {
      const actors = getActors();
      if (!actors.length) return;
      const run = restoring = new AbortController();
      let error;
      sync();
      try {
        await Promise.allSettled(actors.map((actor) => writes.get(actor.id)));
        run.signal.throwIfAborted();
        catalog = await api.getCharacterLibrary(actors, run.signal);
        catalogReady = true;
        for (const actor of actors) {
          const saved = assignment(actor);
          if (!saved.id) continue;
          const model = catalog.models.find(({ id }) => id === saved.id);
          if (!model) {
            error = m("server.character_library_missing");
            continue;
          }
          let entry = null;
          try {
            const unsaved = readStoredPose(editKey(actor, model.id));
            entry = await prepare(actor, { kind: model.kind, libraryId: model.id }, run.signal, unsaved ?? saved.offsets ?? null);
            run.signal.throwIfAborted();
            attach(actor, entry);
            if (unsaved) editPose(entry, unsaved);
            entry = null;
          } catch (cause) {
            if (run.signal.aborted) break;
            error = m("character.failed", { detail: cause });
          } finally {
            dispose(entry);
          }
        }
      } catch (cause) {
        if (picker()) error = cause;
      } finally {
        if (restoring === run) {
          restoring = null;
          if (dialog.open) refresh();
          else sync();
          if (error && !run.signal.aborted) onRestoreError(error);
        }
      }
    },
  };
}
