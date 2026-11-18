import { $, el } from "./utils.js";
import { message as m, setAttr, setText } from "./i18n.js";

const STORAGE = "flowhmr-retarget-shortcuts";
const DEFAULTS = Object.freeze({rotate:"KeyR",move:"KeyG",scale:"KeyS"});
const actions={rotate:"retarget.probe_rotate",move:"retarget.probe_move",scale:"retarget.probe_scale"};
export const probeAction=(mode)=>m(actions[mode]);
const modes = Object.keys(DEFAULTS);
const keyAllowed = (code) => /^(?:Key[A-Z]|Digit[0-9])$/.test(code);
const keyLabel = (code) => code.slice(code.startsWith("Digit") ? 5 : 3);
const keyboardCode = ({key,code}) => /^[A-Z]$/i.test(key) ? "Key"+key.toUpperCase() :
  /^[0-9]$/.test(key) ? "Digit"+key : code;

function read() {
  try {
    const saved=JSON.parse(localStorage.getItem(STORAGE));
    const keys=modes.map((mode)=>saved?.[mode]);
    if(keys.every(keyAllowed) && new Set(keys).size===keys.length)
      return Object.fromEntries(modes.map((mode,i)=>[mode,keys[i]]));
  } catch { /* defaults still work */ }
  return {...DEFAULTS};
}

export function createRetargetShortcuts(signal,onChange) {
  const panel=$("retargetShortcuts"), hint=$("retargetShortcutHint"), reset=$("retargetShortcutReset");
  const dialog=panel.closest("dialog");
  const controls=[...panel.querySelectorAll("[data-retarget-shortcut]")];
  let keys=read(), capture, issue, active=false;
  function render() {
    for(const button of controls) {
      const mode=button.dataset.retargetShortcut;
      const key=keyLabel(keys[mode]);
      button.replaceChildren(el("kbd","",capture===mode?"…":key));
      button.toggleAttribute("data-capturing",capture===mode);
      setAttr(button,"aria-label",m("retarget.shortcut_edit",{action:probeAction(mode),key}));
    }
    setText(hint,m(issue??(capture?"retarget.shortcut_wait":"retarget.shortcut_help")));
    reset.disabled=!modes.some((mode)=>keys[mode]!==DEFAULTS[mode]);
  }
  function persist() {
    try {localStorage.setItem(STORAGE,JSON.stringify(keys));} catch { /* works this session */ }
    capture=issue=null;
    render();onChange();
  }
  panel.open=false;
  controls.forEach((button)=>button.onclick=()=>{
    capture=button.dataset.retargetShortcut;issue=null;render();
    button.focus({preventScroll:true});
  });
  reset.onclick=()=>{keys={...DEFAULTS};persist();};
  panel.addEventListener("toggle",()=>{if(!panel.open) {capture=issue=null;render();}},{signal});
  // Choosing a source joint or a list entry redraws its buttons, so the
  // browser sends the next key to <body>. A click on an empty part of the
  // floating editor behaves the same way. Remember the interaction scope
  // without moving keyboard focus off the user's control.
  const trackScope=(event)=>{
    const path=event.composedPath();
    active=dialog.open && path.includes(dialog);
    if(capture && !path.some((node)=>controls.includes(node) && node.dataset.retargetShortcut===capture)) {
      capture=issue=null;render();
    }
  };
  const scopeOptions={capture:true,signal};
  document.addEventListener("pointerdown",trackScope,scopeOptions);
  document.addEventListener("focusin",trackScope,scopeOptions);
  render();
  return {
    owns(event) {
      const {target}=event;
      return event.composedPath().includes(dialog) ||
        (active && (target===document.body || target===document.documentElement));
    },
    mode(event) {
      if(event.repeat||event.isComposing||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return null;
      return modes.find((mode)=>keys[mode]===keyboardCode(event))??null;
    },
    labels() {return Object.fromEntries(modes.map((mode)=>[mode,keyLabel(keys[mode])]));},
    get open() {return panel.open;},
    close() {panel.open=false;capture=issue=null;render();},
    handle(event) {
      if(!panel.open || event.isComposing)return false;
      if(!capture) {
        if(event.key!=="Escape")return false;
        panel.open=false;
      } else if(event.key==="Escape") {capture=issue=null;render();}
      else if(event.key==="Tab"){capture=issue=null;render();return false;}
      else if(!keyAllowed(keyboardCode(event))||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey) {
        issue="retarget.shortcut_invalid";render();
      } else if(modes.some((mode)=>mode!==capture&&keys[mode]===keyboardCode(event))) {
        issue="retarget.shortcut_conflict";render();
      } else {keys[capture]=keyboardCode(event);persist();}
      event.preventDefault();event.stopPropagation();
      return true;
    },
  };
}
