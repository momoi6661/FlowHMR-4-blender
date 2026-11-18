import { Box3, MathUtils, Matrix3, MOUSE, OrthographicCamera, Quaternion, Vector3 } from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { $, el, show } from "./utils.js";
import { createBoneInspection } from "./retarget-inspection.js";

const UP = new Vector3(0, 1, 0);
const X = new Vector3(1, 0, 0);
const EPS = 1e-10;
const TOUCH_SLOP = 5;
const HIT_RADIUS = 9;
const OVERLAP_RADIUS = 5;
const Z = new Vector3(0, 0, 1);
const OCTAHEDRON_FACES = [[0,2,1],[0,3,2],[0,4,3],[0,1,4],[5,1,2],[5,2,3],[5,3,4],[5,4,1]];
const FACE_LIGHT = new Vector3(-.38,.65,1).normalize();

function edgeDistance(p, a, b) {
  const dx=b.x-a.x, dy=b.y-a.y;
  const t=MathUtils.clamp(((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy || 1),0,1);
  return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
}

function insideFace(p, a, b, c) {
  const cross=(v,w)=>(p.x-w.x)*(v.y-w.y)-(v.x-w.x)*(p.y-w.y);
  const s=cross(a,b), t=cross(b,c), u=cross(c,a);
  return (s>=0 && t>=0 && u>=0) || (s<=0 && t<=0 && u<=0);
}

function axes(right, up) {
  right = right.clone().addScaledVector(up, -right.dot(up));
  if (right.lengthSq() < EPS) right = new Vector3().crossVectors(Math.abs(up.z) < .8 ? Z : UP, up);
  right.normalize();
  return [right, up, right.clone().cross(up)];
}

// Give each hand its own camera basis: fingers point up and the viewer faces
// the palm. For unnamed rigs use the wrist branch geometry instead of names.
function palmAxes(points, ids, wrist, digit, side) {
  const origin = points[wrist];
  const middle = points[digit(3)], index = points[digit(0)], pinky = points[digit(6)];
  const distances = ids.filter((i) => i !== wrist).map((i) => points[i].clone().sub(origin));
  const up = new Vector3();
  for (const p of [index, middle, pinky]) if (p) up.add(p.clone().sub(origin));
  if (up.lengthSq() < EPS) {
    for (const delta of distances) up.add(delta);
    if (up.lengthSq() < EPS) {
      for (const delta of distances) if (delta.lengthSq() > up.lengthSq()) up.copy(delta);
    }
  }
  if (up.lengthSq() < EPS) up.copy(UP);
  up.normalize();
  let right;
  if (index && pinky) {
    right = pinky.clone().sub(index);
    if (side) right.negate();
  } else {
    const sideways = new Vector3();
    for (const delta of distances) {
      const span = delta.clone().addScaledVector(up, -delta.dot(up));
      if (span.lengthSq() > sideways.lengthSq()) sideways.copy(span);
    }
    right = sideways.lengthSq() >= EPS ? sideways : X;
  }
  if (right.clone().addScaledVector(up, -right.dot(up)).lengthSq() < EPS) {
    right = Math.abs(up.x) < 0.8 ? X : new Vector3(0, 0, 1);
  }
  return axes(right, up);
}

export function createRetargetTargetView(bones, onPick, trail, signal, asset = null, onSkinError = () => {}) {
  const host = $("retargetTargetDiagram"), canvas = $("retargetTargetCanvas");
  const skin = $("retargetInspectSkin");
  const hover = $("retargetHoverBone"), menu = $("retargetBoneChoices");
  const context = canvas.getContext("2d");
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.001, 30);
  const orbit = new OrbitControls(camera, canvas);
  orbit.mouseButtons = { LEFT: null, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE };
  orbit.rotateSpeed = .68;
  orbit.minZoom = .35;
  orbit.maxZoom = 60;
  orbit.zoomToCursor = true;
  const points = bones.map(({ position }) => new Vector3().fromArray(position));
  const all = bones.map((_, i) => i);
  const children = bones.map(() => []);
  bones.forEach(({ parent }, i) => children[parent]?.push(i));
  let visible = all, frameIds = all, handScope = false, handRoot = -1, tab = "body";
  const basisMatrix=new Matrix3();
  let viewKey, state, basis, origin, coords = [], shown = new Set(), inspection, probe, lastPointer;
  let correction=null, correctionDirty=false, skinVisible=false;
  let width = 1, height = 1, half = 1, drawFrame = 0, projected = [], faces = [], cones = false;
  let cursor = null, hot = -1, down = null, busy = false, manuallyMoved = false;

  function descendants(root) {
    const branch = [root], seen = new Set(branch);
    for (const node of branch) for (const child of children[node]) {
      if (!seen.has(child)) { seen.add(child); branch.push(child); }
    }
    return branch;
  }

  function scope(nextTab, map, suggested) {
    if (nextTab === "body") return { key: "body", indices: all, wrist: -1 };
    const side = Number(nextTab === "right"), first = 22 + 15 * side;
    // Once the user clears a wrist, prompt them to choose it again. The
    // initial suggestions already live in the manual map.
    const wrist = map[20 + side];
    if (wrist < 0) return { key: nextTab + ":unbound", indices: all, wrist };
    const indices = descendants(wrist);
    const inHand = new Set(indices);
    const digit = (i) => {
      for (const n of [map[first + i], suggested[first + i]]) if (inHand.has(n)) return n;
      return -1;
    };
    const core = new Set([wrist]);
    for (let i = 0; i < 15; i++) for (const n of [map[first + i], suggested[first + i]]) if (inHand.has(n)) core.add(n);
    for (const n of indices) if (/(?:thumb|index|middle|ring|pinky|little|finger|親指|人指|人差|中指|小指|薬指|拇指|食指|无名指|無名指)/i.test(bones[n].name)) core.add(n);
    return { key: nextTab + ":" + wrist, indices, wrist, digit, side, framing: core.size >= 4 ? [...core] : indices };
  }

  function updateLayout(s, map, suggested) {
    handScope = s.wrist >= 0;
    handRoot = s.wrist;
    if (handScope) {
      basis = palmAxes(points, s.framing, s.wrist, s.digit, s.side);
    } else {
      const ref = (i) => points[map[i]] ?? points[suggested[i]];
      const left = ref(16) ?? ref(1), right = ref(17) ?? ref(2);
      basis = axes(left && right ? left.clone().sub(right) : X, UP);
    }
    basisMatrix.set(...basis.flatMap((axis)=>axis.toArray()));
    origin = handScope ? points[s.wrist] : points[map[0]] ?? points[0] ?? new Vector3();
    inspection?.align(basis,origin);
    if(correction)correctionDirty=true;
    coords = points.map((p) => {
      return p.clone().sub(origin).applyMatrix3(basisMatrix);
    });
    visible = s.indices;
    shown = new Set(visible);
    frameIds = s.framing ?? visible;
    viewKey = s.key;
  }

  function setFrustum() {
    const aspect = width / height;
    camera.left = -half * aspect; camera.right = half * aspect;
    camera.top = half; camera.bottom = -half;
    camera.updateProjectionMatrix();
  }

  function revealSelection() {
    if (!coords[state.active] || width<80 || height<78) return;
    camera.updateMatrixWorld();
    const p=coords[state.active].clone().project(camera);
    const x=width*(.5+.5*p.x),y=height*(.5-.5*p.y);
    const gapX=Math.min(48,width*.26),gapY=Math.min(46,height*.30);
    const dx=MathUtils.clamp(x,gapX,width-gapX)-x,dy=MathUtils.clamp(y,gapY,height-gapY)-y;
    if(Math.abs(dx)+Math.abs(dy)<.5)return;
    const ratio=2*half/height/camera.zoom;
    const shift=new Vector3().setFromMatrixColumn(camera.matrixWorld,0).multiplyScalar(-dx*ratio)
      .addScaledVector(new Vector3().setFromMatrixColumn(camera.matrixWorld,1),dy*ratio);
    camera.position.add(shift);orbit.target.add(shift);orbit.update();
  }

  function frame() {
    if (!state || !coords.length || width < 2 || height < 2) return;
    closeMenu();
    const box = new Box3();
    for (const i of frameIds) box.expandByPoint(coords[i]);
    const size = box.getSize(new Vector3());
    const middle = box.getCenter(new Vector3());
    const span = Math.max(.015, size.length());
    const distance = span * 2.8;
    half = Math.max(size.y, size.x * height / width, span * .27) * .65;
    camera.near = distance * .005;
    camera.far = distance * 12;
    camera.zoom = 1;
    setFrustum();
    camera.position.copy(middle).addScaledVector(new Vector3(.06, .075, 1).normalize(), distance);
    orbit.target.copy(middle);
    orbit.update();
    manuallyMoved = false;
    invalidate();
  }

  function fit() {
    if (!state) return;
    updateLayout(scope(tab, state.map, state.suggested), state.map, state.suggested);
    frame();
  }

  function resize() {
    const box = canvas.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) return;
    const refit = width === 1 || height === 1 ||
      (camera.zoom === 1 && Math.abs(box.width/box.height - width/height) > .01 && !manuallyMoved);
    width = box.width; height = box.height;
    const pixelRatio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    if (refit) frame();
    else { setFrustum(); invalidate(); }
    if (menu.childElementCount) closeMenu();
  }

  function invalidate() {
    if (!drawFrame) drawFrame = requestAnimationFrame(draw);
  }

  function ranksAt(x, y) {
    const candidates = [];
    for (const p of projected) {
      if (!p.visible || !state.eligible(p.id)) continue;
      const distance = Math.hypot(p.x - x, p.y - y);
      if (distance <= HIT_RADIUS) candidates.push({ ...p, distance });
    }
    if (!candidates.length && cones) {
      const found=new Set();
      for (const face of faces) {
        const {id, vertices:[a,b,c], z}=face;
        if (found.has(id) || !state.eligible(id)) continue;
        if (insideFace({x,y},a,b,c) || edgeDistance({x,y},a,b) <= 2) {
          found.add(id);
          candidates.push({id,x,y,z,distance:0});
        }
      }
    }
    return candidates.sort((a, b) => a.distance - b.distance || a.z - b.z || a.id - b.id);
  }

  function highlight(id) {
    if (id === hot) return;
    hot = id;
    hover.textContent = bones[id]?.name ?? "";
    show(hover, id >= 0 && menu.classList.contains("hidden"));
    canvas.classList.toggle("pickable", id >= 0);
  }

  function drawAxes() {
    const cx = width - 24, cy = 25, unit = 14;
    const directions = [
      [X, "X", "#f88384"], [UP, "Y", "#96d682"], [Z, "Z", "#7eaeef"],
    ].map(([v, name, color]) => {
      const dir = new Vector3(...basis.map((axis) => v.dot(axis))).transformDirection(camera.matrixWorldInverse);
      return { dir, name, color };
    }).sort((a,b) => a.dir.z - b.dir.z);
    context.beginPath();
    context.arc(cx, cy, 18, 0, Math.PI * 2);
    context.fillStyle = "#1c2128dd"; context.fill();
    context.font = "9px system-ui"; context.textAlign = "center"; context.textBaseline = "middle";
    for (const { dir, name, color } of directions) {
      const x = cx + dir.x * unit, y = cy - dir.y * unit;
      context.beginPath(); context.moveTo(cx, cy); context.lineTo(x, y);
      context.strokeStyle = color; context.lineWidth = 1; context.stroke();
      context.beginPath(); context.arc(x, y, 5, 0, Math.PI * 2);
      context.fillStyle = color; context.fill();
      context.fillStyle = "#17202c"; context.fillText(name, x, y + .3);
    }
  }

  function draw() {
    drawFrame = 0;
    if (!context || !state || !coords.length || width < 2 || height < 2) return;
    const outline = getComputedStyle(host).getPropertyValue("--retarget-bone-outline").trim();
    context.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
    context.clearRect(0, 0, width, height);
    camera.updateMatrixWorld();
    const pixel = new Vector3();
    if(!probe && correctionDirty) {
      correctionDirty=false;
      if (!correction || !startVisual("correction",correction.id,correction.values)) inspection?.end();
    }
    // Show the same skinned pose and selection in the two overlaid canvases.
    if(probe?.delta) {
      inspection.drag(...probe.delta,camera,2*half/height/camera.zoom);
      probe.delta=null;
    }
    if(skinVisible && basis) {
      try {
        inspectionView().drawSkin(camera,width,height,Math.min(devicePixelRatio||1,2));
      } catch {
        setSkin(false);
        onSkinError();
      }
    }
    const preview=inspection?.pose();
    const positions=preview?.positions ?? coords;
    const affected=preview?.affected;
    const project = (p) => {
      pixel.copy(p).project(camera);
      const x = width * (.5 + .5 * pixel.x), y = height * (.5 - .5 * pixel.y);
      return { x, y, z: pixel.z, visible: pixel.z >= -1 && pixel.z <= 1 && x >= 0 && x <= width && y >= 0 && y <= height };
    };
    projected = visible.map((id) => ({id,...project(positions[id])}));
    const byId = new Map(projected.map((p) => [p.id, p]));
    faces = [];
    if (cones) {
      const hasMainRig=state.landmarks.size>=10 || state.mapped.size>=10;
      for (const id of visible) {
        // Keep structural/hair/cloth controls as pickable lines and dots. Nine
        // overlapping helper shapes can otherwise obscure the actual limb.
        if (hasMainRig && !state.landmarks.has(id) && !state.mapped.has(id) && id!==state.active) continue;
        const parent = bones[id].parent;
        const p=byId.get(id), a=byId.get(parent);
        if (!a || Math.hypot(p.x-a.x,p.y-a.y) < 4) continue;
        const head=positions[parent], tip=positions[id], axis=tip.clone().sub(head), length=axis.length();
        const turn=new Quaternion().setFromUnitVectors(Z,axis.normalize());
        const center=head.clone().lerp(tip,.20);
        const radius=Math.min(length*.106, (handScope ? 9 : 14) * (2*half/height) / camera.zoom);
        const vertices=[head,...[[-1,-1],[1,-1],[1,1],[-1,1]].map(([x,y])=>
          new Vector3(x*radius,y*radius,0).applyQuaternion(turn).add(center)),tip];
        const screens=vertices.map(project);
        for (const [i,j,k] of OCTAHEDRON_FACES) {
          const [a,b,c] = [screens[i],screens[j],screens[k]];
          const area=(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
          if (area >= -.03 || [a,b,c].some((p)=>p.z < -1 || p.z > 1)) continue;
          const u=vertices[j].clone().sub(vertices[i]), v=vertices[k].clone().sub(vertices[i]);
          const light=u.cross(v).transformDirection(camera.matrixWorldInverse).dot(FACE_LIGHT);
          faces.push({id, vertices:[a,b,c], z:(a.z+b.z+c.z)/3, light});
        }
      }
      faces.sort((a,b)=>b.z-a.z);
    }
    highlight(cursor && !busy && !down?.moved && !probe ? ranksAt(cursor.x,cursor.y)[0]?.id ?? -1 : -1);
    const sorted = [...projected].sort((a, b) => b.z - a.z);
    const limit = (p) => p.z >= -1 && p.z <= 1;
    const { active, mapped, landmarks, eligible } = state;
    const emphasized = (id) => mapped.has(id) || landmarks.has(id) || id === handRoot;
    for (const p of sorted) {
      const parent = bones[p.id].parent;
      const other = byId.get(parent);
      if (!other || !limit(other) || !limit(p)) continue;
      if (Math.hypot(p.x - other.x, p.y - other.y) < .3) continue;
      const main = emphasized(p.id) || (handScope && visible.length <= 28);
      context.beginPath(); context.moveTo(other.x, other.y); context.lineTo(p.x, p.y);
      const moving = affected?.has(p.id);
      context.lineWidth = moving ? 2 : main ? 1.45 : .9;
      context.strokeStyle = moving ? "#ffbf64" : skinVisible ? "#e0e9f4b0" : main ? "#9eafbf" : "#586470";
      context.stroke();
    }
    if (cones) {
      for (const {id, vertices:[a,b,c], light} of faces) {
        const [h,s,l]=id===active || id===hot || affected?.has(id) ? [36,76,55] : mapped.has(id) ? [159,30,53] : [215,15,52];
        context.beginPath(); context.moveTo(a.x,a.y); context.lineTo(b.x,b.y); context.lineTo(c.x,c.y); context.closePath();
        context.fillStyle=`hsl(${h} ${s}% ${l+15*light}%)`;
        context.fill();
        context.strokeStyle=outline; context.lineWidth=.7; context.stroke();
      }
    }
    function node(p) {
      if (!p?.visible || !eligible(p.id)) return;
      const selected = p.id === active, hovered = p.id === hot;
      const used = mapped.has(p.id), major = landmarks.has(p.id) || p.id === handRoot;
      const radius = selected || hovered ? 5.3 : handScope ? 3.7 : major || used ? 3.3 : 2.1;
      if (selected || hovered) {
        context.beginPath(); context.arc(p.x, p.y, radius+3, 0, Math.PI*2);
        context.strokeStyle = hovered ? "#f7e5ba" : "#f4b563"; context.lineWidth = 1.1;
        context.stroke();
      }
      context.beginPath(); context.arc(p.x, p.y, radius, 0, Math.PI*2);
      context.fillStyle = selected || affected?.has(p.id) ? "#ffc074" : hovered ? "#f8e5bd" : used ? "#74c5a3" : major ? "#b0cadd" : "#8596a8";
      context.fill(); context.lineWidth = 1; context.strokeStyle = outline; context.stroke();
    }
    sorted.forEach(node);
    if (active >= 0) node(byId.get(active));
    if (hot >= 0 && hot !== active) node(byId.get(hot));
    drawAxes();
  }

  function endInspection() {
    if(!probe)return false;
    const done=probe.finished;
    probe=null;
    orbit.enabled=true;
    inspection.end();
    correctionDirty=!!correction;
    delete host.dataset.inspecting;
    done();
    invalidate();
    return true;
  }
  function inspectionView() {
    if (!inspection) {
      inspection=createBoneInspection(asset,skin);
      inspection.align(basis,origin);
    }
    return inspection;
  }
  function setSkin(enabled) {
    skinVisible=!!asset && enabled;
    show(skin,skinVisible);
    invalidate();
  }
  function startVisual(mode,id,values) {
    if(!asset || !basis || !coords[id])return false;
    try {
      return inspectionView().begin(id,mode,values);
    } catch {
      return false;
    }
  }
  function startInspection(mode,id,finished) {
    endInspection();
    closeMenu();down=null;
    revealSelection();
    if(!startVisual(mode,id,correction?.id===id ? correction.values : null)) {
      if(correction) {correctionDirty=true;invalidate();}
      return false;
    }
    // If started from the search list, re-anchor at the first motion inside
    // the canvas. This avoids a jump while moving the pointer to the rig.
    const p=lastPointer, box=canvas.getBoundingClientRect();
    const inCanvas=p && p.x>=box.left && p.x<=box.right && p.y>=box.top && p.y<=box.bottom;
    probe={finished, start:inCanvas ? p : null};
    orbit.enabled=false;
    host.dataset.inspecting=mode;
    invalidate();
    return true;
  }
  function setCorrection(next) {
    const previous=correction;
    correction=next;
    if(!next && !previous)return;
    correctionDirty=true;
    invalidate();
  }

  function hit(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  function closeMenu(restoreFocus = false) {
    if (menu.classList.contains("hidden")) return false;
    show(menu, false);
    menu.replaceChildren();
    if (restoreFocus) canvas.focus({ preventScroll:true });
    if (hot >= 0) show(hover, true);
    return true;
  }
  function pickMenu(candidates, location) {
    show(hover, false);
    menu.replaceChildren(...candidates.map(({ id }) => {
      const button = el("button", "", bones[id].name || "#" + id);
      button.type = "button"; button.dataset.targetBone = id;
      button.title = trail(id) + " · #" + id;
      button.setAttribute("aria-pressed", String(id === state.active));
      button.onclick = () => { closeMenu(true); onPick(id); };
      return button;
    }));
    show(menu, true);
    menu.style.left = Math.max(5, Math.min(location.x + 9, width - menu.offsetWidth - 5)) + "px";
    menu.style.top = Math.max(5, Math.min(location.y + 8, height - menu.offsetHeight - 5)) + "px";
    menu.querySelector("button")?.focus({ preventScroll:true });
  }
  menu.addEventListener("keydown", (event) => {
    const buttons = [...menu.querySelectorAll("button")];
    const index = buttons.indexOf(event.target);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      buttons[(index + buttons.length + (event.key === "ArrowDown" ? 1 : -1)) % buttons.length]?.focus();
    }
  }, { signal });

  orbit.addEventListener("change", () => { manuallyMoved = true; closeMenu(); invalidate(); });
  canvas.addEventListener("pointerdown", (event) => {
    if(probe) {
      if(event.button===0 || event.button===2)endInspection();
      event.preventDefault();event.stopImmediatePropagation();return;
    }
    closeMenu();
    canvas.focus({ preventScroll:true });
    if (down) { down.moved = true; return; }
    down = { id:event.pointerId, x:event.clientX, y:event.clientY, moved:false, pan:event.pointerType !== "touch" && event.button === 0,
      pick:event.button === 0 && !event.altKey && !event.ctrlKey && !event.shiftKey && !event.metaKey };
  }, { signal, capture:true });
  canvas.addEventListener("contextmenu",(event)=>event.preventDefault(),{signal});
  window.addEventListener("pointermove",(event)=>{
    lastPointer={x:event.clientX,y:event.clientY};
    if(!probe)return;
    if(!probe.start && event.target!==canvas)return;
    const start=probe.start??=lastPointer, fine=event.shiftKey ? .2 : 1;
    probe.delta=[(event.clientX-start.x)*fine,(event.clientY-start.y)*fine];
    invalidate();
  },{signal,capture:true});
  window.addEventListener("blur",endInspection,{signal});
  window.addEventListener("pointerup", (event) => {
    if (down?.id === event.pointerId && event.target !== canvas) { down = null; invalidate(); }
  }, { signal });
  canvas.addEventListener("pointermove", (event) => {
    cursor = hit(event);
    if (down?.id === event.pointerId) {
      const dx=event.clientX-down.x, dy=event.clientY-down.y;
      if (down.moved || Math.hypot(dx,dy) > TOUCH_SLOP) {
        if (down.pan) {
          const ratio=2*half / height / camera.zoom;
          camera.updateMatrixWorld();
          const shift=new Vector3().setFromMatrixColumn(camera.matrixWorld,0).multiplyScalar(-dx*ratio)
            .addScaledVector(new Vector3().setFromMatrixColumn(camera.matrixWorld,1),dy*ratio);
          orbit.target.add(shift);
          camera.position.add(shift);
          orbit.update();
          manuallyMoved = true;
          down.x=event.clientX; down.y=event.clientY;
        }
        down.moved = true;
      }
    }
    invalidate();
  }, { signal });
  canvas.addEventListener("pointerleave", () => { cursor = null; invalidate(); }, { signal });
  canvas.addEventListener("pointerup", (event) => {
    if (down?.id !== event.pointerId) return;
    const valid = down.pick && !down.moved && !busy;
    down = null;
    const p = hit(event);
    if (state && valid && p.x >= 0 && p.y >= 0 && p.x < width && p.y < height) {
      // An orbit/resize may have queued a paint. Pick against exactly the
      // camera that will draw this click, preferring the nearest visible dot.
      if (drawFrame) { cancelAnimationFrame(drawFrame); draw(); }
      const found = ranksAt(p.x, p.y), first = found[0];
      if (first) {
        const overlapping = first.visible ? found.filter((b) => Math.hypot(b.x - first.x, b.y - first.y) <= OVERLAP_RADIUS) : [first];
        if (overlapping.length > 1) pickMenu(overlapping, p);
        else onPick(first.id);
      }
    }
    invalidate();
  }, { signal });
  for (const type of ["pointercancel", "lostpointercapture"]) canvas.addEventListener(type, (event) => {
    if (down?.id === event.pointerId) { down = null; invalidate(); }
  }, { signal });
  canvas.addEventListener("auxclick", (event) => event.preventDefault(), { signal });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation(); // Keep desktop wheel speed independent of screen DPI.
    if (!state || !event.deltaY || probe) return;
    closeMenu();
    camera.updateMatrixWorld();
    const p = hit(event);
    const anchor = new Vector3(p.x / width * 2 - 1, 1 - p.y / height * 2, 0);
    const before = anchor.clone().unproject(camera);
    const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1;
    const step = MathUtils.clamp(event.deltaY * scale, -360, 360) * .0021;
    camera.zoom = MathUtils.clamp(camera.zoom * Math.exp(-step), orbit.minZoom, orbit.maxZoom);
    camera.updateProjectionMatrix();
    const correction = before.sub(anchor.unproject(camera));
    camera.position.add(correction);
    orbit.target.add(correction);
    orbit.update();
    manuallyMoved = true;
    invalidate();
  }, { capture:true, passive:false, signal });
  document.addEventListener("pointerdown", (event) => {
    if(probe && event.target!==canvas && !event.target.closest?.("#retargetSkin"))endInspection();
    if (!menu.contains(event.target)) closeMenu();
  }, { capture:true, signal });
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "Home") { event.preventDefault(); fit(); }
    if (event.key === "+" || event.key === "=" || event.key === "-") {
      event.preventDefault();
      camera.zoom = MathUtils.clamp(camera.zoom * (event.key === "-" ? .8 : 1.25), orbit.minZoom, orbit.maxZoom);
      manuallyMoved = true; setFrustum(); invalidate();
    }
  }, { signal });
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  signal.addEventListener("abort", () => {
    probe=null;
    delete host.dataset.inspecting;
    show(skin,false);
    inspection?.dispose();
    observer.disconnect();
    cancelAnimationFrame(drawFrame);
    orbit.dispose();
    closeMenu();
    highlight(-1);
    canvas.width = canvas.height = 1;
  }, { once:true });
  return {
    set(next) {
      const { map, suggested, active, eligible } = next;
      tab = next.tab;
      host.dataset.scope = tab;
      const mapped = new Set(map.filter((i) => i >= 0));
      const landmarks = new Set(suggested.filter((i) => i >= 0));
      state = { map,suggested,active,eligible,mapped,landmarks };
      busy = next.busy;
      closeMenu();
      const key = tab === "body" ? tab : tab + ":" + (map[tab === "left" ? 20 : 21] < 0 ?
        "unbound" : map[tab === "left" ? 20 : 21]);
      if (key !== viewKey) {
        updateLayout(scope(tab, map, suggested), map, suggested);
        resize();frame();
      }
      invalidate();
      return { indices:shown, needWrist:tab !== "body" && !handScope };
    },
    setCones(show) { cones=show; invalidate(); },
    setSkin,
    startInspection,
    endInspection,
    setCorrection,
    closeMenu,
  };
}
