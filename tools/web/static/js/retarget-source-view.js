import { el } from "./utils.js";
import { message as m, setAttr } from "./i18n.js";
import { sourceBodyReference } from "./retarget-reference.js";

// The diagram faces the viewer: the character's left is on screen right.
const BODY = [
  [15,90,24,"head"], [12,90,51,"neck"], [9,90,74,"chest"],
  [6,90,98,"spine"], [3,90,122,"waist"], [0,90,147,"pelvis"],
  [13,111,60,"clavicle","left"], [14,69,60,"clavicle","right"],
  [16,130,75,"upper_arm","left"], [17,50,75,"upper_arm","right"],
  [18,149,109,"elbow","left"], [19,31,109,"elbow","right"],
  [20,161,145,"wrist","left"], [21,19,145,"wrist","right"],
  [1,111,155,"hip","left"], [2,69,155,"hip","right"],
  [4,114,200,"knee","left"], [5,66,200,"knee","right"],
  [7,115,241,"ankle","left"], [8,65,241,"ankle","right"],
  [10,127,263,"toes","left"], [11,53,263,"toes","right"],
  ["root",90,266,"root"],
];
const BODY_LINKS = [[15,12,9,6,3,0], [9,13,16,18,20], [9,14,17,19,21], [0,1,4,7,10], [0,2,5,8,11]];
const HAND = [
  [22,56,128,"index",1], [23,50,90,"index",2], [24,47,53,"index",3],
  [25,82,119,"middle",1], [26,81,77,"middle",2], [27,80,34,"middle",3],
  [28,132,142,"pinky",1], [29,143,117,"pinky",2], [30,151,89,"pinky",3],
  [31,108,123,"ring",1], [32,111,83,"ring",2], [33,114,46,"ring",3],
  [34,47,182,"thumb",1], [35,29,161,"thumb",2], [36,17,135,"thumb",3],
];
const HAND_LINKS = [[22,23,24],[25,26,27],[28,29,30],[31,32,33],[34,35,36],[34,22,25,31,28]];
const FINGERS_SHAPE = "M58 255l-3-34q-14-14-23-33L8 147q-6-12 0-17 7-5 15 1l27 29-13-103q-2-14 7-17 9-3 13 12l13 65-1-83q0-13 11-13 9 0 10 13l4 82 8-72q1-12 11-12 10 2 9 14l-4 87 23-47q4-11 13-8 8 4 5 15l-17 67q-8 33-24 51l-2 44z";
const byId = new Map([...BODY, ...HAND].map((node) => [node[0], node]));
const SVG = "http://www.w3.org/2000/svg";

const delta = (a,b) => [a[0]-b[0],a[1]-b[1]];
const distance = (a,b) => Math.hypot(...delta(a,b));
const unit = (v) => { const n=Math.hypot(...v) || 1; return [v[0]/n,v[1]/n]; };
const shiftPoint = (p, v, n) => [p[0]+v[0]*n,p[1]+v[1]*n];
const midpoint = (a,b) => [(a[0]+b[0])/2,(a[1]+b[1])/2];
const pstr = (p) => p.map((v)=>Number(v.toFixed(2))).join(" ");
function extend(a,b,length) { return shiftPoint(b,unit(delta(b,a)),length); }

// Rounded, tapered limb shapes use the same screen-space joints as the picker.
// Compositing the complete body as one SVG group keeps the joins seamless.
function limbShape(points, radii) {
  const side = (sign) => points.map((point,i)=>{
    const tangent=unit(delta(points[Math.min(i+1,points.length-1)],points[Math.max(i-1,0)]));
    return shiftPoint(point,[-tangent[1],tangent[0]],sign*radii[i]);
  });
  const outline=[...side(1),...side(-1).reverse()];
  return "M"+pstr(midpoint(outline.at(-1),outline[0]))+outline.map((point,i)=>
    " Q"+pstr(point)+" "+pstr(midpoint(point,outline[(i+1)%outline.length]))).join("")+" Z";
}

function bodySilhouette(layout) {
  const silhouette=document.createElementNS(SVG,"g");
  silhouette.setAttribute("class","retarget-source-silhouette");
  const at=(id)=>layout.get(id).slice(1,3);
  const head=at(15), neck=at(12), chest=at(9), waist=at(3), pelvis=at(0);
  const shoulders=[at(16),at(17)], across=unit(delta(shoulders[0],shoulders[1]));
  const armWidth=Math.max(13,Math.min(30,distance(...shoulders)*.5));
  const hipWidth=Math.max(12,Math.min(24,distance(at(1),at(2))*.5+7));
  const neckWidth=armWidth*.24, ribWidth=armWidth*.73, waistWidth=armWidth*.59;
  const s=(p,n)=>pstr(shiftPoint(p,across,n));
  const torsoBase=midpoint(at(1),at(2));
  function path(d) {
    const node=document.createElementNS(SVG,"path");
    node.setAttribute("d",d);
    silhouette.append(node);
  }
  path(`M${s(neck,-neckWidth)} Q${s(midpoint(neck,shoulders[1]),-2)} ${pstr(shoulders[1])}
    Q${s(chest,-ribWidth)} ${s(waist,-waistWidth)} Q${s(pelvis,-hipWidth)} ${s(torsoBase,-hipWidth)}
    Q${s(shiftPoint(torsoBase,[0,1],6),-hipWidth*.75)} ${pstr(shiftPoint(torsoBase,[0,1],7))}
    Q${s(shiftPoint(torsoBase,[0,1],6),hipWidth*.75)} ${s(torsoBase,hipWidth)}
    Q${s(pelvis,hipWidth)} ${s(waist,waistWidth)} Q${s(chest,ribWidth)} ${pstr(shoulders[0])}
    Q${s(midpoint(neck,shoulders[0]),2)} ${s(neck,neckWidth)} Z`);
  path(limbShape([head,neck,chest],[neckWidth,neckWidth,neckWidth*.95]));
  const skull=document.createElementNS(SVG,"ellipse");
  const skullCenter=extend(neck,head,4);
  skull.setAttribute("cx",skullCenter[0]);skull.setAttribute("cy",skullCenter[1]);
  skull.setAttribute("rx","11.6");skull.setAttribute("ry","14");
  silhouette.append(skull);
  for (const i of [0,1]) {
    const shoulder=shoulders[i], elbow=at(18+i), wrist=at(20+i);
    const bicep=midpoint(shoulder,elbow);
    const armStart=extend(elbow,shoulder,5);
    path(limbShape([armStart,bicep,elbow,wrist,extend(elbow,wrist,9)],[7.5,7.3,5.8,4.3,3.8]));
    const hip=at(1+i), knee=at(4+i), ankle=at(7+i), toe=at(10+i);
    path(limbShape([extend(knee,hip,7),midpoint(hip,knee),knee,midpoint(knee,ankle),ankle,toe,
      extend(ankle,toe,4)],[10,9,6.8,6.7,4.6,5.2,3.4]));
  }
  return silhouette;
}

export function jointLabel(id) {
  if (id >= 37) id -= 15;
  const [, , , part, detail] = byId.get(id);
  const joint = m(`pose.${part}`);
  if (typeof detail === "number") return m("pose.digit_joint", { joint, index: detail });
  if (detail) return m("pose.side_joint", { joint, side: m(`pose.${detail}`) });
  return joint;
}

export function drawRetargetSource(diagram, tab, mapped, selected, pick, required, rest, reference) {
  const isHand = tab !== "body", shift = tab === "right" ? 15 : 0;
  const wrist = tab === "right" ? 21 : 20;
  const nodes = isHand ? [...HAND, [wrist, 83, 236]] : restBody(rest,reference);
  const xs=nodes.map(([,x])=>x),ys=nodes.map(([, ,y])=>y);
  const bounds = isHand ? [0,21,180,244] : [
    Math.min(...xs)-13, Math.min(...ys)-20,
    Math.max(...xs)-Math.min(...xs)+26, Math.max(...ys)-Math.min(...ys)+33,
  ];
  const layout = new Map(nodes.map((node) => [node[0], node]));
  const [minX,minY,width,height]=bounds;
  const layer=el("div","retarget-source-fit");
  diagram.style.setProperty("--source-fit-aspect",String(width/height));
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", bounds.join(" "));
  svg.setAttribute("aria-hidden", "true");
  if (isHand) {
    const shape = document.createElementNS(SVG, "path");
    shape.setAttribute("class", "pose-shape");
    shape.setAttribute("d", FINGERS_SHAPE);
    if (shift) shape.setAttribute("transform", "translate(180 0) scale(-1 1)");
    svg.append(shape);
  } else svg.append(bodySilhouette(layout));
  for (const link of isHand ? HAND_LINKS : BODY_LINKS) {
    const line = document.createElementNS(SVG, "polyline");
    line.setAttribute("class", "pose-links");
    line.setAttribute("points", link.map((id) => layout.get(id).slice(1, 3).join(",")).join(" "));
    if (shift) line.setAttribute("transform", "translate(180 0) scale(-1 1)");
    svg.append(line);
  }
  if (isHand) {
    const palmLine = document.createElementNS(SVG, "polyline");
    palmLine.setAttribute("class", "pose-links");
    palmLine.setAttribute("points", "82,119 83,236");
    if (shift) palmLine.setAttribute("transform", "translate(180 0) scale(-1 1)");
    svg.append(palmLine);
  }
  layer.append(svg);
  for (const [original, x, y] of nodes) {
    const id = isHand && original >= 22 ? original + shift : original;
    const btn = el("button", "pose-node");
    btn.type = "button";
    btn.dataset.sourceBone = id;
    btn.style.left = `${100*((shift ? 180-x : x)-minX)/width}%`;
    btn.style.top = `${100*(y-minY)/height}%`;
    // Leave the centre of every neighbouring joint clickable when the view shrinks.
    const nearest=Math.min(...nodes.filter(([other])=>other!==original).map(([,u,v])=>Math.hypot(x-u,y-v)));
    if (nearest>0) btn.style.setProperty("--node-max-hit",`${130*nearest/width}cqw`);
    btn.classList.toggle("mapped", mapped(id) >= 0);
    btn.classList.toggle("required", required.includes(id));
    btn.setAttribute("aria-pressed", String(selected === id));
    const label=id==="root" ? m("retarget.motion_root") : jointLabel(id);
    setAttr(btn, "aria-label", label);
    setAttr(btn, "title", label);
    btn.onclick = () => pick(id);
    layer.append(btn);
  }
  diagram.replaceChildren(layer);
}

function restBody(rest, reference) {
  const points = sourceBodyReference(rest,reference);
  if (!points) {
    // A missing/invalid rest stream should never break mapping navigation.
    return BODY.map(([id,x,y,...label]) => {
      if(id>=18) {
        const side=id%2, angle=(reference.type==="a" ? reference.drops[side] : 0)*Math.PI/180;
        const shoulder=side?50:130, length=id>=20?58:30;
        return [id,shoulder-90+(side?-1:1)*length*Math.cos(angle),75+length*Math.sin(angle),...label];
      }
      return [id,x-90,y,...label];
    });
  }
  const bottom=Math.max(points[10][1],points[11][1]);
  return BODY.map(([id, , ,...label]) => id==="root" ?
    [id,0,bottom+23,...label] : [id,...points[id],...label]);
}
