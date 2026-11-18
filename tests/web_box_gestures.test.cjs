// Isolated real UI modules: no backend, AI, user jobs, or generated files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const harness = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/workspace.css">
<style>
body{padding:20px} #previewBox{display:block;position:relative;width:800px;height:500px}
#previewBox video,#previewBox img{width:100%;height:100%;object-fit:contain}
#previewCanvas{position:absolute;inset:0;width:100%;height:100%}
#personCard{width:320px}.source-editor{container-type:inline-size}
</style></head><body><div id="previewTitle"></div><div id="previewIssue"></div>
<div id="previewBox"><video id="previewVideo" muted playsinline></video>
<img id="previewImage" class="hidden"><canvas id="previewCanvas"></canvas></div>
<div id="boxEditHint"></div><div id="personCard" class="source-editor">
<div id="personTitle"></div><div id="personCount"></div><div id="personList"></div>
<button id="boxReset">Reset</button><button id="personConfirm"></button>
<button id="personReselect"></button><div id="personError"></div></div>
<script type="module">
import {initPreview} from '/static/js/preview.js';
import {initPersonSelect} from '/static/js/person-select.js';
import {createBus} from '/static/js/utils.js';
window.bus=createBus();window.preview=initPreview(bus);window.people=initPersonSelect(bus);
window.edits=[];window.editEvents=0;
bus.on('preview:pick',index=>people.select(index));
bus.on('preview:box-edits',value=>{window.edits=structuredClone(value.edits);window.editEvents++;});
bus.on('person:reset-boxes',()=>{window.edits=[];});
window.fixture=async(video=false)=>{
 people.reset();edits=[];editEvents=0;
 if(video){
  preview.showJob('/example.mp4','video.mp4');
  await Promise.race([new Promise(resolve=>preview.video.addEventListener('loadeddata',resolve,{once:true})),
   new Promise((_,reject)=>setTimeout(()=>reject(new Error('Video fixture load timed out')),10000))]);
 }else{
  const surface=document.createElement('canvas');surface.width=640;surface.height=480;
  surface.getContext('2d').fillRect(0,0,640,480);
  const blob=await new Promise(resolve=>surface.toBlob(resolve));
  preview.showLocal(new File([blob],'pose.png',{type:'image/png'}));
  await document.getElementById('previewImage').decode();
 }
 const w=video?preview.video.videoWidth:640,h=video?preview.video.videoHeight:480;
 window.job={id:video?'fixture-video':'fixture-image',filename:video?'video.mp4':'pose.png',
  source_kind:video?'video':'image',status:'waiting_user',default_person:0,
  candidates:[{index:0,frames:[0,59],start:0,end:59,num_frames:60,static_image:!video,
   boxes:[[.2*w,.2*h,.6*w,.8*h],[.2*w,.2*h,.6*w,.8*h]]},
   {index:1,frames:[0,59],start:0,end:59,num_frames:60,static_image:!video,
   boxes:[[.65*w,.1*h,.95*w,.4*h],[.65*w,.1*h,.95*w,.4*h]]}]};
 people.update(job);return{w,h};
};window.ready=true;
</script></body></html>`;

async function run(browser,dpr,origin){
 const page=await browser.newPage({viewport:{width:1200,height:1000},deviceScaleFactor:dpr});
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 let supported=false,submitted=null;
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:harness});
  if(url.pathname.startsWith('/static/')){
   const file=path.resolve(root,'tools/web',`.${url.pathname}`);
   assert(file.startsWith(path.join(root,'tools/web/static')+path.sep));
   return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file)});
  }
  if(url.pathname==='/example.mp4')return route.continue();
  if(url.pathname==='/api/info')return route.fulfill({json:{blender_bridge:{manual_boxes:supported}}});
  if(/\/select(?:-boxes)?$/.test(url.pathname)){
   submitted=route.request().postDataJSON();return route.fulfill({json:{ok:true,people:submitted.people}});
  }
  if(/\/person\/\d+.jpg$/.test(url.pathname))return route.fulfill({status:404});
  throw new Error(`Unexpected request ${url.href}`);
 });
 try{
  await page.goto(origin);await page.waitForFunction(()=>window.ready);
  await page.evaluate(()=>fixture());
  async function edit(person=0){await page.locator('.person-edit').nth(person).click();}
  async function geometry(){return page.evaluate(()=>{
   const image=preview.isImage?document.getElementById('previewImage'):preview.video;
   const w=preview.isImage?image.naturalWidth:image.videoWidth,h=preview.isImage?image.naturalHeight:image.videoHeight;
   const r=document.getElementById('previewCanvas').getBoundingClientRect();
   const s=Math.min(image.clientWidth/w,image.clientHeight/h);
   return{s,x:r.left+(image.clientWidth-w*s)/2,y:r.top+(image.clientHeight-h*s)/2};
  });}
  async function move(point){const g=await geometry();await page.mouse.move(g.x+point[0]*g.s,g.y+point[1]*g.s);}
  async function drag(from,to){await move(from);await page.mouse.down();await move(to);await page.mouse.up();}
  async function check(expected,person=0,frame=0){
   const values=await page.evaluate(()=>window.edits),value=values.find(e=>e.person===person&&e.frame===frame);
   assert(value,`Missing edit for ${person} at ${frame}: ${JSON.stringify(values)}; ${JSON.stringify(await page.evaluate(()=>({time:preview.video.currentTime,frame:preview.clock.frameAt(preview.video.currentTime),
    editing:document.getElementById('previewCanvas').className,bounds:document.getElementById('previewCanvas').getBoundingClientRect().toJSON()})))}`);
   expected.forEach((n,i)=>assert(Math.abs(n-value.box[i])<2,`${value.box} != ${expected}`));return value;
  }
  for(const width of[420,300,240,180]){
   const b=await page.locator('#personCard').evaluate((card,width)=>{
    card.style.width=`${width}px`;const list=card.querySelector('#personList'),button=list.querySelector('.person-edit');
    return{scroll:list.scrollWidth,width:list.clientWidth,left:button.getBoundingClientRect().left-list.getBoundingClientRect().left,
     right:button.getBoundingClientRect().right-list.getBoundingClientRect().left};
   },width);assert(b.scroll<=b.width+1&&b.left>=0&&b.right<=b.width+1);
  }
  await page.locator('#personCard').evaluate(card=>card.style.width='320px');
  await edit();await move([256,240]);
  assert.equal(await page.locator('#previewCanvas').evaluate(c=>c.style.cursor),'grab');
  await move([128,96]);assert.equal(await page.locator('#previewCanvas').evaluate(c=>c.style.cursor),'nwse-resize');
  await page.evaluate(()=>new Promise(requestAnimationFrame));
  const grip=await page.evaluate(()=>{
   const c=document.getElementById('previewCanvas'),i=document.getElementById('previewImage');
   const s=Math.min(i.clientWidth/640,i.clientHeight/480),d=devicePixelRatio;
   const x=(i.clientWidth-640*s)/2+128*s,y=(i.clientHeight-480*s)/2+96*s;
   return[...c.getContext('2d').getImageData(Math.round(x*d),Math.round(y*d),1,1).data];
  });assert(grip[0]>230&&grip[1]>230&&grip[2]>230&&grip[3]>230,'Resize grip must be visible');
  await drag([256,240],[320,208]);await check([192,64,448,352]);
  await drag([192,64],[160,32]);await check([160,32,448,352]);
  await move([448,192]);assert.equal(await page.locator('#previewCanvas').evaluate(c=>c.style.cursor),'ew-resize');
  await drag([448,192],[512,192]);await check([160,32,512,352]);
  assert.equal((await page.evaluate(()=>edits)).length,1,'Repeated edits replace the image anchor');
  const before=await page.evaluate(()=>editEvents);await move([300,200]);await page.mouse.down();await page.mouse.up();
  assert.equal(await page.evaluate(()=>editEvents),before,'A click must not rewrite a box');
  await move([300,200]);await page.mouse.down();await move([340,240]);await page.keyboard.press('Escape');await page.mouse.up();
  await check([160,32,512,352]);assert(!(await page.locator('#previewCanvas').evaluate(c=>c.classList.contains('editing-box'))));
  await edit();await drag([300,200],[-300,-300]);await check([0,0,352,320]);
  await page.locator('#boxReset').click();assert.equal((await page.evaluate(()=>edits)).length,0);
  await edit();await drag([64,48],[576,432]);assert((await check([64,48,576,432])).whole_track);
  await edit(1);await drag([512,120],[480,144]);await check([384,72,576,216],1);
  assert.equal((await page.evaluate(()=>edits)).length,2,'Other people retain their corrections');
  await page.locator('#personConfirm').click();await page.waitForFunction(()=>document.getElementById('personError').textContent.includes('重启'));
  assert.equal(submitted,null,'An old backend must not silently discard corrections');
  supported=true;await page.locator('#personConfirm').click();
  await page.waitForFunction(()=>document.getElementById('personConfirm').textContent.includes('已确认'));
  assert.deepEqual(submitted.people,[0,1]);assert.equal(submitted.box_edits.length,2);
  assert(submitted.box_edits.every(e=>e.frame===0&&e.whole_track));
  await page.evaluate(()=>bus.emit('person:locked',{candidate:job.candidates[0],index:0}));
  assert(!(await page.locator('#previewCanvas').evaluate(c=>c.classList.contains('editing-box'))));
  const {w,h}=await page.evaluate(()=>fixture(true));await page.waitForFunction(()=>preview.clock.ready);await edit();
  async function seek(frame){await page.evaluate(async frame=>{
   preview.video.pause();await Promise.race([new Promise(resolve=>{preview.video.addEventListener('seeked',resolve,{once:true});
    preview.video.currentTime=preview.clock.timeOf(frame+.5);}),
    new Promise((_,reject)=>setTimeout(()=>reject(new Error('Video seek timed out')),8000))]);
  },frame);}
  await seek(15);await move([.4*w,.5*h]);
  assert(!(await page.locator('#previewVideo').evaluate(v=>v.controls)),'Native controls must not cover resize grips');
  await drag([.4*w,.5*h],[.45*w,.55*h]);assert(!(await check([.25*w,.25*h,.65*w,.85*h],0,15)).whole_track);
  await seek(45);await drag([.6*w,.8*h],[.7*w,.9*h]);await check([.2*w,.2*h,.7*w,.9*h],0,45);
  assert.equal((await page.evaluate(()=>edits)).length,2,'Video corrections retain separate frame anchors');
  await move([.4*w,.5*h]);await page.mouse.down();await move([.45*w,.5*h]);
  const eventsBeforeJobChange=await page.evaluate(()=>editEvents);
  await page.evaluate(()=>{people.reset();people.update({...job,id:'another-job'});});await page.mouse.up();
  await edit();assert(await page.locator('#personConfirm').isDisabled(),'A replaced job must not inherit a drag');
  assert.equal(await page.evaluate(()=>editEvents),eventsBeforeJobChange,'A cancelled drag must not submit a correction');
  const g=await geometry();
  await page.locator('#previewCanvas').dispatchEvent('pointerdown',{pointerId:99,button:0,
   clientX:g.x+.4*w*g.s,clientY:g.y+.5*h*g.s});
  await page.locator('#previewCanvas').dispatchEvent('pointercancel',{pointerId:99});
  await page.keyboard.press('Escape');await move([.4*w,.5*h]);await page.mouse.down();await page.mouse.up();
  assert(!(await page.locator('#personConfirm').isDisabled()),'A fresh click must still select after pointer cancellation');
  assert.deepEqual(errors,[]);console.log(`WEB_BOX_MOVE_RESIZE_GRIPS_REDRAW_RESET_IMAGE_VIDEO_MULTI_PERSON_DPR_${dpr}_OK`);
 }finally{
  await page.evaluate(()=>{people.reset();preview.clear();}).catch(()=>{});
  await page.close();
 }
}
(async()=>{
 const file=path.join(root,'assets/example/example.mp4'),size=fs.statSync(file).size;
 const server=http.createServer((req,res)=>{
  if(req.url!=='/example.mp4'){res.writeHead(404);res.end();return;}
  const range=req.headers.range?.match(/bytes=(\d+)-(\d*)/);
  const first=range?Number(range[1]):0,last=range&&range[2]?Math.min(Number(range[2]),size-1):size-1;
  res.writeHead(range?206:200,{'Content-Type':'video/mp4','Accept-Ranges':'bytes','Content-Length':last-first+1,
   ...(range?{'Content-Range':`bytes ${first}-${last}/${size}`}:{})});
  const stream=fs.createReadStream(file,{start:first,end:last});
  res.on('close',()=>stream.destroy());stream.pipe(res);
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browserServer=await chromium.launchServer({headless:true,executablePath:process.env.FLOWHMR_CHROME||'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args:['--disable-gpu','--enable-unsafe-swiftshader']});
 const browser=await chromium.connect(browserServer.wsEndpoint());
 try{for(const dpr of[1,2])await run(browser,dpr,`http://127.0.0.1:${server.address().port}/`);}
 finally{
  const stopped=new Promise(resolve=>server.close(resolve));server.closeAllConnections();await stopped;
  // Dispose only this isolated fixture browser, including its media processes.
  const killed=browserServer.kill();
  // Windows Chrome subprocesses can retain inherited pipe handles after exit.
  for(const pipe of browserServer.process().stdio)pipe?.destroy?.();
  await killed;
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
