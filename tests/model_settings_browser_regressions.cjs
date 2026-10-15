'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.MOYU_PLAYWRIGHT_MODULE||'playwright');

const root=path.resolve(__dirname,'..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-settings-browser-'));
const sources=['app.js','author-workspace.js','styles.css','moyu_settings.py','server.py'];
const fingerprint=()=>Object.fromEntries(sources.map(file=>[file,
 require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]));
const results={passed:false,scope:'电脑端模型设置、隔离SQLite、免费本机模拟提供方',
 started_at:new Date().toISOString(),cases:[],artifacts:temporary,source_files_sha256_before:fingerprint()};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,provider,browser,page,base,mode='normal',release,lastProbe,serverLog='';
const calls=[],pageErrors=[];
const tasks=['continue','polish','outline','scene','summary','check','brainstorm','chapter_analysis','semantic_review'];

async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',body,project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);return data;
}
async function test(name,fn){
 console.log(JSON.stringify({test:name,state:'running'}));await fn();
 results.cases.push({name,passed:true});console.log(JSON.stringify({test:name,state:'passed'}));
}
async function settingsPage(){
 await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
}
async function submitTracked(key){
 await page.evaluate(key=>{
  const form=document.querySelector('#modelSettings'),submit=form.onsubmit;
  form.onsubmit=event=>{
   form.onsubmit=submit;window[key]=submit.call(form,event);
  };
  form.requestSubmit();
  if(!(window[key] instanceof Promise))throw new Error('设置表单未发起可追踪的保存');
 },key);
}
function taskField(task,parameter){
 return page.locator(`[data-task-row="${task}"] [data-task-parameter="${parameter}"]`);
}
async function saveTaskSettings(key){
 const before=calls.length;
 await submitTracked(key);await page.evaluate(key=>window[key],key);
 await page.waitForFunction(()=>document.querySelector('#modelSettings [type="submit"]')?.disabled===false);
 assert.equal(await page.locator('#modelSettings .form-error').textContent(),'');
 assert.equal(calls.length,before,'保存用途设置不能外发模型请求');
 return (await api('/api/ai/settings')).settings;
}
async function failTaskSettingsSave(key){
 let intercepted=0;
 await page.route('**/api/ai/settings',async route=>{
  if(route.request().method()!=='PUT')return route.continue();
  intercepted++;
  await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'fixture settings failure'})});
 });
 await submitTracked(key);await page.evaluate(key=>window[key],key);
 await page.waitForFunction(()=>document.querySelector('#modelSettings .form-error')?.textContent.includes('fixture settings failure'));
 assert.equal(intercepted,1);
 assert.equal(await page.locator('#modelSettings [type="submit"]').isDisabled(),false);
 assert.equal(await page.locator('#modelSettings input,#modelSettings textarea,#modelSettings select,#modelSettings button').evaluateAll(
  controls=>controls.every(control=>!control.disabled)),true);
 await page.unroute('**/api/ai/settings');
}
async function readTaskPreview(task){
 const state=await api('/api/state');
 const preview=await api('/api/ai/prompt-preview','POST',{task,chapter_id:state.chapters[0].id});
 assert.equal(preview.request_audit.dispatched,false);
 return preview.request;
}
async function reopenSettings(){
 await page.locator('[data-view="editor"]').click();await settingsPage();
}
async function settingsScreenshot(filename){
 await page.waitForFunction(()=>{
  const toast=document.querySelector('#toast');
  return !toast||!toast.classList.contains('show')&&Number(getComputedStyle(toast).opacity)===0;
 });
 await page.evaluate(async()=>{
  window.scrollTo(0,0);await document.fonts.ready;
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 });
 await page.mouse.move(1200,100);await page.waitForTimeout(150);
 await page.screenshot({path:path.join(temporary,filename),fullPage:true,animations:'disabled'});
}
async function probe(){
 lastProbe=null;await page.locator('#modelProbe').click();
 await page.waitForFunction(()=>!document.querySelector('#modelProbe')?.disabled,null,{timeout:15000});
 assert.ok(lastProbe);return lastProbe;
}
async function holdProbe(){
 mode='held';release=null;lastProbe=null;await page.locator('#modelProbe').click();
 for(let i=0;i<100&&!release;i++)await pause(20);
 assert.ok(release,'本机提供方没有收到连接检测');
 assert.equal(await page.locator('#modelDefault').isDisabled(),true);
 assert.equal(await page.locator('#modelSettings [type="submit"]').isDisabled(),true);
}

async function main(){
 const providerPort=await freePort();
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',async()=>{
   const payload=JSON.parse(raw);const current=mode;calls.push({model:payload.model,max_tokens:payload.max_tokens});
   if(current==='held')await new Promise(resolve=>release=resolve);
   response.writeHead(current==='unauthorized'?401:200,{'Content-Type':'application/json'});
   if(current==='unauthorized'){response.end('{"error":"fixture unauthorized"}');return}
   const message={content:current==='one-character'?'铁':'连接正常'};
   if(current==='reasoning')message.reasoning_content='fixture private reasoning';
   response.end(JSON.stringify({model:current==='mismatch'?'actual-different-fixture':payload.model,
    choices:[{message,finish_reason:'stop'}],usage:{prompt_tokens:13,completion_tokens:7,total_tokens:20}}));
  });
 });
 await new Promise(resolve=>provider.listen(providerPort,'127.0.0.1',resolve));
 const appPort=await freePort();base=`http://127.0.0.1:${appPort}`;
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,
  env:{...process.env,MOYU_DB:path.join(temporary,'book.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(appPort),
   MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'100',MOYU_SECRET_KEY:'',
   MOYU_BACKUP_DIR:path.join(temporary,'backups'),MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let i=0;i<200;i++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  if(app.exitCode!==null)throw new Error('server exited: '+serverLog.slice(-2500));
  if(i===199)throw new Error('server unavailable: '+serverLog.slice(-2500));
  await pause(40);
 }
 await api('/api/state');
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('response',async response=>{if(response.url().endsWith('/api/ai/probe'))lastProbe=await response.json()});
 await page.goto(base);await page.waitForFunction(()=>backendOnline===true);await settingsPage();

 await test('只保存设置不调用模型，电脑两种宽度表单均无横向遮挡',async()=>{
  await page.locator('#modelBaseUrl').fill(`http://127.0.0.1:${providerPort}/v1`);
  await page.locator('#modelDefault').fill('requested-fixture');
  await page.locator('#modelReasoning').selectOption('deepseek_disabled');
  await submitTracked('pendingInitialSave');
  await page.evaluate(()=>window.pendingInitialSave);
  await page.waitForFunction(()=>authorSettings?.model==='requested-fixture'&&authorSettings.parameters?.thinking?.type==='disabled');
  assert.equal(calls.length,0);assert.match(await page.locator('#modelProbeStatus').textContent(),/尚未验证/);
  for(const width of [1440,1280]){
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   const box=await page.locator('#modelSettings').boundingBox();
   assert.ok(box,'保存后的设置表单应该可见');assert.ok(box.x>=0&&box.x+box.width<=width);
  }
  await page.setViewportSize({width:1440,height:1000});
 });
 await test('合格默认模型检测即刻更新设置和助手状态，刷新保留同次结果',async()=>{
  const before=calls.length;const result=await probe();assert.equal(calls.length,before+1);
  assert.equal(result.success,true);assert.equal(result.protocol_complete,true);assert.equal(result.response_valid,true);
  assert.deepEqual(await page.evaluate(()=>({settings:authorSettings.last_probe?.success,status:aiStatus.last_probe?.success})),
   {settings:true,status:true});
  assert.match(await page.locator('#modelProbeStatus').textContent(),/默认模型实测成功/);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.match(await page.locator('#modelProbeStatus').textContent(),/默认模型实测成功/);
  assert.equal(calls.length,before+1);
  await settingsScreenshot('model-settings-success.png');
 });
 await test('最近失败替换旧成功，跨页和刷新均显示失败且不重发模型',async()=>{
  mode='unauthorized';const result=await probe();assert.equal(result.success,false);
  assert.match(await page.locator('#modelProbeStatus').textContent(),/未通过.*401/);
  assert.deepEqual(await page.evaluate(()=>({settings:authorSettings.last_probe?.success,status:aiStatus.last_probe?.success})),
   {settings:false,status:false});
  const count=calls.length;await page.locator('[data-view="editor"]').click();await settingsPage();
  assert.match(await page.locator('#modelProbeStatus').textContent(),/未通过.*401/);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.match(await page.locator('#modelProbeStatus').textContent(),/未通过.*401/);
  assert.equal(calls.length,count);
  await settingsScreenshot('model-settings-failed.png');
 });
 await test('回传模型不一致保留双方名称，不能显示所选模型检测成功',async()=>{
  mode='mismatch';const result=await probe();assert.equal(result.success,false);
  assert.equal(result.response_valid,true);assert.equal(result.model_identity_match,false);
  const text=await page.locator('#modelProbeStatus').textContent();
  assert.match(text,/未通过.*名称不一致/);assert.match(text,/请求 requested-fixture/);
  assert.match(text,/回传 actual-different-fixture/);assert.doesNotMatch(text,/实测成功/);
 });
 await test('只有一字的完整stop回复保留完成证据，但页面持续显示不合格',async()=>{
  mode='one-character';const result=await probe();assert.equal(result.protocol_complete,true);
  assert.equal(result.finish_reason,'stop');assert.equal(result.response_valid,false);assert.equal(result.success,false);
  assert.match(await page.locator('#modelProbeStatus').textContent(),/未通过.*回复1字/);
  await page.locator('[data-view="editor"]').click();await settingsPage();
  assert.match(await page.locator('#modelProbeStatus').textContent(),/未通过.*回复1字/);
 });
 await test('关闭思考仍返回思考字段时不通过，页面和记录不展示思考内容',async()=>{
  mode='reasoning';const result=await probe();assert.equal(result.protocol_complete,true);
  assert.equal(result.response_valid,true);assert.equal(result.reasoning_consistent,false);assert.equal(result.success,false);
  assert.match(await page.locator('#modelProbeStatus').textContent(),/未通过.*非空思考字段/);
  assert.doesNotMatch(await page.locator('#modelProbeStatus').textContent(),/fixture private reasoning/);
  assert.doesNotMatch(JSON.stringify(result),/fixture private reasoning/);
 });
 await test('另一窗口修改设置使旧检测失效，页面回读当前模型且不报成功',async()=>{
  await holdProbe();await api('/api/ai/settings','PUT',{model:'replacement-fixture'});release();
  await page.waitForFunction(()=>document.querySelector('#modelProbeStatus')?.textContent.includes('本次检测已失效'));
  assert.equal(lastProbe.configuration_stale,true);assert.equal(lastProbe.success,false);
  assert.equal(lastProbe.protocol_complete,true);assert.equal(lastProbe.response_valid,true);
  assert.equal(await page.locator('#modelDefault').inputValue(),'replacement-fixture');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,probe:authorSettings.last_probe})),
   {model:'replacement-fixture',probe:null});
  assert.doesNotMatch(await page.locator('#modelProbeStatus').textContent(),/实测成功/);
 });
 await test('同一作品保存新设置后，迟到旧检测不能覆盖页面内存或后续保存',async()=>{
  mode='normal';await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,
   model:'requested-fixture',daily_limit:100});
  await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='requested-fixture');
  assert.equal(await page.locator('#modelDefault').inputValue(),'requested-fixture');
  let resume,observed;const held=new Promise(resolve=>observed=resolve);let captured=false;
  await page.route('**/api/ai/probe',async route=>{
   if(captured)return route.continue();
   captured=true;const response=await route.fetch();observed();
   await new Promise(resolve=>{resume=resolve});await route.fulfill({response});
  });
  await page.evaluate(()=>{window.pendingSameProjectProbe=document.querySelector('#modelProbe').onclick()});
  await held;
  await api('/api/ai/settings','PUT',{model:'new-same-project-fixture'},'default-project');
  await page.locator('[data-view="editor"]').click();await page.locator('[data-view="settings"]').click();
  await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='new-same-project-fixture');
  assert.equal(await page.evaluate(()=>authorSettings.model),'new-same-project-fixture');
  resume();await page.evaluate(()=>window.pendingSameProjectProbe);await page.unroute('**/api/ai/probe');
  assert.equal(await page.locator('#modelDefault').inputValue(),'new-same-project-fixture');
  assert.equal(await page.evaluate(()=>authorSettings.model),'new-same-project-fixture');
  assert.equal(await page.evaluate(()=>aiStatus.model),'new-same-project-fixture');
  assert.match(await page.locator('#modelProbeStatus').textContent(),/尚未验证/);
  assert.doesNotMatch(await page.locator('#modelProbeStatus').textContent(),/实测成功|请求 requested-fixture/);
  const count=calls.length;
  await submitTracked('pendingCurrentProbeSave');
  await page.evaluate(()=>window.pendingCurrentProbeSave);
  await page.waitForFunction(()=>authorSettings?.model==='new-same-project-fixture'&&
   !document.querySelector('#modelSettings [type="submit"]')?.disabled);
  const saved=await api('/api/ai/settings','GET',undefined,'default-project');
  assert.equal(saved.settings.model,'new-same-project-fixture');
  assert.equal(calls.length,count);
 });
 await test('同一作品迟到旧保存响应不能覆盖新读取，后续保存不回写旧模型或调用模型',async()=>{
  await page.locator('[data-view="library"]').click();await page.locator('[data-project-open="default-project"]').click();
  await page.waitForFunction(()=>requestedProject==='default-project'&&backendOnline===true);
  await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,
   model:'stale-save-A-fixture',daily_limit:100,models:{scene:'stale-scene-A-fixture'},
   task_parameters:{scene:{temperature:0.2,max_tokens:1100,reasoning_effort:'low'}}},'default-project');
  await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='stale-save-A-fixture');
  let resume,observed;const held=new Promise(resolve=>observed=resolve);let captured=false;
  await page.route('**/api/ai/settings',async route=>{
   if(captured||route.request().method()!=='PUT')return route.continue();
   captured=true;const response=await route.fetch();observed();
   await new Promise(resolve=>{resume=resolve});await route.fulfill({response});
  });
  await page.locator('#modelDefault').fill('stale-save-A-fixture');
  await submitTracked('pendingStaleSave');
  await held;
  const currentTaskParameters={scene:{temperature:0,max_tokens:2200,thinking:{type:'disabled'}}};
  await api('/api/ai/settings','PUT',{model:'stale-save-B-fixture',models:{scene:'stale-scene-B-fixture'},
   task_parameters:currentTaskParameters},'default-project');
  await page.locator('[data-view="editor"]').click();await page.locator('[data-view="settings"]').click();
  await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='stale-save-B-fixture');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'stale-save-B-fixture',status:'stale-save-B-fixture',input:'stale-save-B-fixture'});
  assert.deepEqual(await page.evaluate(()=>authorSettings.task_parameters),currentTaskParameters);
  assert.equal(await page.locator('[data-task-model="scene"]').inputValue(),'stale-scene-B-fixture');
  assert.equal(await taskField('scene','temperature').inputValue(),'0');
  assert.equal(await taskField('scene','max_tokens').inputValue(),'2200');
  assert.equal(await taskField('scene','reasoning').inputValue(),'deepseek_disabled');
  resume();await page.evaluate(()=>window.pendingStaleSave);await page.unroute('**/api/ai/settings');
  await page.waitForFunction(()=>document.querySelector('#modelSettings [type="submit"]')&&!document.querySelector('#modelSettings [type="submit"]').disabled);
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'stale-save-B-fixture',status:'stale-save-B-fixture',input:'stale-save-B-fixture'});
  assert.deepEqual(await page.evaluate(()=>authorSettings.task_parameters),currentTaskParameters);
  assert.equal(await taskField('scene','temperature').inputValue(),'0');
  const count=calls.length;
  await submitTracked('pendingCurrentSettingsSave');
  await page.evaluate(()=>window.pendingCurrentSettingsSave);
  await page.waitForFunction(()=>authorSettings?.model==='stale-save-B-fixture'&&
   !document.querySelector('#modelSettings [type="submit"]')?.disabled);
  const saved=await api('/api/ai/settings','GET',undefined,'default-project');
  assert.equal(saved.settings.model,'stale-save-B-fixture');
  assert.deepEqual(saved.settings.task_parameters,currentTaskParameters);
  assert.equal(saved.settings.models.scene,'stale-scene-B-fixture');
  assert.equal(await page.evaluate(()=>authorSettings.model),'stale-save-B-fixture');
  assert.equal(await page.evaluate(()=>aiStatus.model),'stale-save-B-fixture');
  assert.equal(await page.locator('#modelDefault').inputValue(),'stale-save-B-fixture');
  assert.equal(calls.length,count);
 });
 await test('设置与助手状态同时读完才显示新表单，等待期间切页不会写回旧设置',async()=>{
  const count=calls.length;
  const previous=await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model}));
  await page.locator('[data-view="editor"]').click();
  await api('/api/ai/settings','PUT',{model:'delayed-settings-C-fixture'},'default-project');
  let releaseStatus,observed;const held=new Promise(resolve=>observed=resolve);
  let captured=false;
  await page.route('**/api/ai/status',async route=>{
   if(captured)return route.continue();
   captured=true;const response=await route.fetch();observed();
   await new Promise(resolve=>{releaseStatus=resolve});await route.fulfill({response});
  });
  await page.locator('[data-view="settings"]').click();await held;
  try{
   assert.equal(await page.locator('#modelSettings').count(),0);
   assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model})),previous);
  }finally{releaseStatus()}
  await page.locator('#modelSettings').waitFor();await page.unroute('**/api/ai/status');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'delayed-settings-C-fixture',status:'delayed-settings-C-fixture',input:'delayed-settings-C-fixture'});

  await page.locator('[data-view="editor"]').click();
  await api('/api/ai/settings','PUT',{model:'delayed-settings-D-fixture'},'default-project');
  let releaseNext,observeNext;const nextHeld=new Promise(resolve=>observeNext=resolve);
  captured=false;
  await page.route('**/api/ai/status',async route=>{
   if(captured)return route.continue();
   captured=true;const response=await route.fetch();observeNext();
   await new Promise(resolve=>{releaseNext=resolve});await route.fulfill({response});
  });
  await page.evaluate(()=>{
   const load=authorLoadPage;authorLoadPage=()=>window.pendingSettingsLoad=load();
   try{view='settings';render()}finally{authorLoadPage=load}
  });
  await nextHeld;
  await page.locator('[data-view="editor"]').click();
  releaseNext();await page.evaluate(()=>window.pendingSettingsLoad);await page.unroute('**/api/ai/status');
  assert.equal(await page.evaluate(()=>view),'editor');
  assert.equal(await page.locator('#modelSettings').count(),0);
  assert.equal(await page.evaluate(()=>authorSettings.model),'delayed-settings-C-fixture');
  await settingsPage();
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'delayed-settings-D-fixture',status:'delayed-settings-D-fixture',input:'delayed-settings-D-fixture'});
  assert.equal(calls.length,count);
 });
 await test('已发出的旧保存响应在离开设置页后不写回全局设置或状态',async()=>{
  await page.locator('[data-view="library"]').click();await page.locator('[data-project-open="default-project"]').click();
  await page.waitForFunction(()=>requestedProject==='default-project'&&backendOnline===true);
  await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,
   model:'inflight-save-A-fixture',daily_limit:100},'default-project');
  await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='inflight-save-A-fixture');
  let release,observed;const held=new Promise(resolve=>observed=resolve);let captured=false;
  await page.route('**/api/ai/settings',async route=>{
   if(captured||route.request().method()!=='PUT')return route.continue();
   captured=true;const response=await route.fetch();observed();
   await new Promise(resolve=>{release=resolve});await route.fulfill({response});
  });
  await page.locator('#modelDefault').fill('inflight-save-A-fixture');
  await page.evaluate(()=>{
   const form=document.querySelector('#modelSettings');
   window.pendingInflightSave=form.onsubmit(new Event('submit',{cancelable:true}));
  });
  await held;
  const sequence=await page.evaluate(()=>authorSettingsSeq);
  await api('/api/ai/settings','PUT',{model:'inflight-save-B-fixture'},'default-project');
  await page.locator('[data-view="editor"]').click();
  assert.equal(await page.evaluate(()=>authorSettingsSeq),sequence);
  release();await page.evaluate(()=>window.pendingInflightSave);await page.unroute('**/api/ai/settings');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model})),
   {model:'inflight-save-A-fixture',status:'inflight-save-A-fixture'});
  await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='inflight-save-B-fixture');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'inflight-save-B-fixture',status:'inflight-save-B-fixture',input:'inflight-save-B-fixture'});
 });
 await test('设置PUT和检测在队列等待期间变旧时不发送旧操作',async()=>{
  await page.locator('[data-view="library"]').click();await page.locator('[data-project-open="default-project"]').click();
  await page.waitForFunction(()=>requestedProject==='default-project'&&backendOnline===true);
  await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,
   model:'queued-base-fixture',daily_limit:100},'default-project');
  await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='queued-base-fixture');
  let releaseBlocker,blockerObserved;const blockerHeld=new Promise(resolve=>blockerObserved=resolve);
  let blockerCaptured=false,pagePutRequests=0,probeRequests=0;
  await page.route('**/api/ai/settings',async route=>{
   if(route.request().method()!=='PUT')return route.continue();
   pagePutRequests++;
   if(blockerCaptured)return route.continue();
   blockerCaptured=true;const response=await route.fetch();blockerObserved();
   await new Promise(resolve=>{releaseBlocker=resolve});await route.fulfill({response});
  });
  await page.route('**/api/ai/probe',async route=>{probeRequests++;return route.continue()});
  await page.evaluate(()=>{
   window.pendingSettingsBlocker=durableOperation('/api/ai/settings','PUT',{model:'queued-blocker-fixture'});
  });
  await blockerHeld;
  await page.locator('#modelDefault').fill('queued-stale-A-fixture');
  await submitTracked('pendingQueuedSettings');
  await page.waitForFunction(()=>document.querySelector('#modelSettings [type="submit"]')?.disabled===true);
  const staleSequence=await page.evaluate(()=>authorSettingsSeq);
  await page.evaluate(sequence=>{
   const form=document.querySelector('#modelSettings');
   window.pendingQueuedProbe=durableOperation('/api/ai/probe','POST',{},
    ()=>requestedProject==='default-project'&&sequence===authorSettingsSeq&&form.isConnected);
  },staleSequence);
  await api('/api/ai/settings','PUT',{model:'queued-current-B-fixture'},'default-project');
  await page.locator('[data-view="editor"]').click();
  assert.equal(await page.evaluate(()=>authorSettingsSeq),staleSequence);
  assert.equal(await page.locator('#modelSettings').count(),0);
  releaseBlocker();
  await page.evaluate(()=>Promise.allSettled([
   window.pendingSettingsBlocker,window.pendingQueuedSettings,window.pendingQueuedProbe
  ]));
  await page.unroute('**/api/ai/settings');await page.unroute('**/api/ai/probe');
  await page.locator('[data-view="settings"]').click();
  await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='queued-current-B-fixture');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'queued-current-B-fixture',status:'queued-current-B-fixture',input:'queued-current-B-fixture'});
  assert.equal(pagePutRequests,1);
  assert.equal(probeRequests,0);
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'queued-current-B-fixture',status:'queued-current-B-fixture',input:'queued-current-B-fixture'});
  const saved=await api('/api/ai/settings','GET',undefined,'default-project');
  assert.equal(saved.settings.model,'queued-current-B-fixture');
 });
 await test('保存后的状态读取等待期间切换作品，不向另一作品发送检测',async()=>{
  mode='normal';const created=await api('/api/projects','POST',{title:'设置隔离作品'});
  const other=created.project.id;await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,
   model:'other-project-fixture',daily_limit:100,models:{scene:'other-scene-fixture'},
   task_parameters:{scene:{temperature:0.45,max_tokens:1700,reasoning_effort:'high'}}},other);
  let resume,observed;const held=new Promise(resolve=>observed=resolve);let captured=false;
  await page.route('**/api/ai/status',async route=>{
   if(captured||route.request().headers()['x-moyu-project']!=='default-project')return route.continue();
   captured=true;const response=await route.fetch();await new Promise(resolve=>{resume=resolve;observed()});
   await route.fulfill({response});
  });
  const count=calls.length;await page.evaluate(()=>{window.pendingSettingsProbe=document.querySelector('#modelProbe').onclick()});
  await held;await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${other}"]`).click();
  await page.waitForFunction(id=>requestedProject===id&&backendOnline===true,other);await settingsPage();
  resume();await page.evaluate(()=>window.pendingSettingsProbe);await page.unroute('**/api/ai/status');
  assert.equal(calls.length,count);
  assert.equal(await page.locator('#modelDefault').inputValue(),'other-project-fixture');
  assert.equal(await page.evaluate(()=>authorSettings.model),'other-project-fixture');
  assert.equal(await page.evaluate(()=>aiStatus.model),'other-project-fixture');
  assert.deepEqual(await page.evaluate(()=>authorSettings.task_parameters),
   {scene:{temperature:0.45,max_tokens:1700,reasoning_effort:'high'}});
  assert.equal(await page.locator('[data-task-model="scene"]').inputValue(),'other-scene-fixture');
  assert.equal(await taskField('scene','temperature').inputValue(),'0.45');
  assert.equal(await taskField('scene','max_tokens').inputValue(),'1700');
  assert.equal(await taskField('scene','reasoning').inputValue(),'high');
  assert.match(await page.locator('#modelProbeStatus').textContent(),/尚未验证/);
  assert.equal((await api('/api/ai/settings','GET',undefined,other)).settings.last_probe,null);
  results.other_project=other;
 });
 await test('检测完成后的状态读取迟到，不向另一作品页面回写旧成功',async()=>{
  const other=results.other_project;await page.locator('[data-view="library"]').click();
  await page.locator('[data-project-open="default-project"]').click();
  await page.waitForFunction(()=>requestedProject==='default-project'&&backendOnline===true);await settingsPage();
  mode='normal';lastProbe=null;
  let resume,observed;const held=new Promise(resolve=>observed=resolve);let captured=false;
  await page.route('**/api/ai/status',async route=>{
   if(captured||!lastProbe||route.request().headers()['x-moyu-project']!=='default-project')return route.continue();
   captured=true;const response=await route.fetch();await new Promise(resolve=>{resume=resolve;observed()});
   await route.fulfill({response});
  });
  const count=calls.length;await page.evaluate(()=>{window.pendingSettingsProbe=document.querySelector('#modelProbe').onclick()});
  await held;assert.equal(lastProbe.success,true);assert.equal(calls.length,count+1);
  await page.locator('[data-view="library"]').click();await page.locator(`[data-project-open="${other}"]`).click();
  await page.waitForFunction(id=>requestedProject===id&&backendOnline===true,other);await settingsPage();
  resume();await page.evaluate(()=>window.pendingSettingsProbe);await page.unroute('**/api/ai/status');
  assert.equal(await page.locator('#modelDefault').inputValue(),'other-project-fixture');
  assert.equal(await page.evaluate(()=>authorSettings.model),'other-project-fixture');
  assert.equal(await page.evaluate(()=>aiStatus.model),'other-project-fixture');
  assert.deepEqual(await page.evaluate(()=>authorSettings.task_parameters),
   {scene:{temperature:0.45,max_tokens:1700,reasoning_effort:'high'}});
  assert.equal(await taskField('scene','temperature').inputValue(),'0.45');
  assert.match(await page.locator('#modelProbeStatus').textContent(),/尚未验证/);
  assert.doesNotMatch(await page.locator('#modelProbeStatus').textContent(),/实测成功/);
  assert.equal(calls.length,count+1);
 });
 await page.locator('[data-view="library"]').click();await page.locator('[data-project-open="default-project"]').click();
 await page.waitForFunction(()=>requestedProject==='default-project'&&backendOnline===true);await settingsPage();
 mode='normal';
 await test('九种用途模型与原始参数逐项保存，两种电脑尺寸全部控件互不遮挡',async()=>{
  const seeded=Object.fromEntries(tasks.map((task,index)=>[task,{
   temperature:index/10,max_tokens:600+index*100,top_p:0.4,seed:17+index,
   ...(index%2?{reasoning_effort:'low'}:{thinking:{type:'disabled'}})}]));
  const models=Object.fromEntries(tasks.map(task=>[task,'fixture-'+task]));
  await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,model:'requested-fixture',
   parameters:{temperature:0.65,max_tokens:3000,top_p:0.8,thinking:{type:'disabled'}},
   models,task_parameters:seeded,daily_limit:100});
  await reopenSettings();
  assert.deepEqual(await page.locator('[data-task-row]').evaluateAll(rows=>rows.map(row=>row.dataset.taskRow)),tasks);
  for(const task of tasks){
   assert.equal(await page.locator(`[data-task-model="${task}"]`).inputValue(),models[task]);
   assert.equal(await taskField(task,'temperature').inputValue(),String(seeded[task].temperature));
   assert.equal(await taskField(task,'max_tokens').inputValue(),String(seeded[task].max_tokens));
   const button=page.locator(`[data-task-clear="${task}"]`);
   assert.ok(await button.getAttribute('title'));assert.ok(await button.getAttribute('aria-label'));
  }
  const saved=await saveTaskSettings('pendingAllTaskSettings');
  assert.deepEqual(saved.task_parameters,seeded);assert.deepEqual(saved.models,models);
  assert.equal(saved.parameters.top_p,0.8);
  for(const viewport of [{width:1440,height:1000},{width:1280,height:900}]){
   await page.setViewportSize(viewport);
   const layout=await page.locator('.settings-task-table').evaluate(table=>{
    const box=table.getBoundingClientRect(),issues=[];
    for(const row of table.querySelectorAll('[data-task-row]')){
     const controls=[...row.querySelectorAll('input,select,button')].map(control=>control.getBoundingClientRect());
     controls.forEach((rect,index)=>{
      if(rect.width<30||rect.left<box.left-0.5||rect.right>box.right+0.5)issues.push(row.dataset.taskRow+':bounds');
      if(index&&controls[index-1].right>rect.left+0.5)issues.push(row.dataset.taskRow+':overlap');
     });
    }
    return {issues,overflow:document.documentElement.scrollWidth>innerWidth,table:box.width};
   });
   assert.deepEqual(layout.issues,[]);assert.equal(layout.overflow,false);
   await settingsScreenshot(`task-settings-${viewport.width}x${viewport.height}.png`);
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.deepEqual(await page.evaluate(()=>authorSettings.task_parameters),seeded);
 });
 await test('用途温度零保留，空白删除数值覆盖，刷新后隐藏支持参数保持',async()=>{
  await api('/api/ai/settings','PUT',{task_parameters:{scene:{
   temperature:0,max_tokens:700,top_p:0.4,seed:17,stop:['scene-end'],
   presence_penalty:0.1,frequency_penalty:-0.2}}});
  await reopenSettings();
  const zero=await saveTaskSettings('pendingZeroTaskSettings');
  assert.equal(zero.task_parameters.scene.temperature,0);
  assert.equal((await readTaskPreview('scene')).temperature,0);
  await taskField('scene','temperature').fill('');await taskField('scene','max_tokens').fill('');
  const blank=await saveTaskSettings('pendingBlankTaskSettings');
  assert.deepEqual(blank.task_parameters.scene,{
   top_p:0.4,seed:17,stop:['scene-end'],presence_penalty:0.1,frequency_penalty:-0.2});
  assert.equal((await readTaskPreview('scene')).temperature,0.65);
  assert.equal((await readTaskPreview('scene')).max_tokens,3000);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.equal(await taskField('scene','temperature').inputValue(),'');
  assert.equal(await taskField('scene','max_tokens').inputValue(),'');
  assert.deepEqual((await saveTaskSettings('pendingBlankTaskReload')).task_parameters,blank.task_parameters);
 });
 await test('回顾与检查按整对象供分析审稿继承，显式子用途只覆盖全局且空白不制造对象',async()=>{
  const raw={summary:{temperature:0,max_tokens:1200,reasoning_effort:'low'},
   check:{temperature:0.2,max_tokens:1400,thinking:{type:'enabled'}}};
  await api('/api/ai/settings','PUT',{task_parameters:raw});await reopenSettings();
  assert.equal(await taskField('chapter_analysis','temperature').inputValue(),'');
  assert.equal(await taskField('semantic_review','reasoning').inputValue(),'');
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用回顾');
  assert.equal(await taskField('semantic_review','temperature').getAttribute('placeholder'),'沿用检查');
  assert.equal(await taskField('semantic_review','reasoning').locator('option').first().textContent(),'沿用检查');
  assert.match(await taskField('chapter_analysis','temperature').getAttribute('title'),/未单设时沿用回顾与交接用途；单设后空项沿用全局/);
  await taskField('summary','temperature').fill('');
  await taskField('summary','max_tokens').fill('');
  await taskField('summary','reasoning').selectOption('');
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用全局');
  await page.locator('[data-task-model="summary"]').fill('');
  assert.equal(await page.locator('[data-task-model="chapter_analysis"]').getAttribute('placeholder'),'使用默认模型');
  await taskField('summary','temperature').fill('0');
  await taskField('summary','max_tokens').fill('1200');
  await taskField('summary','reasoning').selectOption('low');
  await page.locator('[data-task-model="summary"]').fill('fixture-summary');
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用回顾');
  assert.equal(await page.locator('[data-task-model="chapter_analysis"]').getAttribute('placeholder'),'沿用回顾模型');
  await page.locator('[data-task-clear="check"]').click();
  assert.equal(await taskField('semantic_review','reasoning').locator('option').first().textContent(),'沿用全局');
  await page.locator('[data-task-model="check"]').fill('');
  assert.equal(await page.locator('[data-task-model="semantic_review"]').getAttribute('placeholder'),'使用默认模型');
  await taskField('check','temperature').fill('0.2');
  await taskField('check','max_tokens').fill('1400');
  await taskField('check','reasoning').selectOption('deepseek_enabled');
  await page.locator('[data-task-model="check"]').fill('fixture-check');
  assert.equal(await taskField('semantic_review','reasoning').locator('option').first().textContent(),'沿用检查');
  assert.equal(await page.locator('[data-task-model="semantic_review"]').getAttribute('placeholder'),'沿用检查模型');
  assert.deepEqual((await saveTaskSettings('pendingRawAliases')).task_parameters,raw);
  const analysis=await readTaskPreview('chapter_analysis'),review=await readTaskPreview('semantic_review');
  assert.equal(analysis.temperature,0);assert.equal(analysis.max_tokens,1200);
  assert.equal(analysis.reasoning_effort,'low');assert.equal('thinking' in analysis,false);
  assert.equal(review.temperature,0.2);assert.equal(review.max_tokens,1400);
  assert.deepEqual(review.thinking,{type:'enabled'});
  await taskField('chapter_analysis','max_tokens').fill('1900');
  await taskField('semantic_review','max_tokens').fill('2100');
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用全局');
  assert.equal(await taskField('semantic_review','reasoning').locator('option').first().textContent(),'沿用全局');
  const explicit=await saveTaskSettings('pendingExplicitAliases');
  assert.deepEqual(explicit.task_parameters.chapter_analysis,{max_tokens:1900});
  assert.deepEqual(explicit.task_parameters.semantic_review,{max_tokens:2100});
  for(const [task,limit] of [['chapter_analysis',1900],['semantic_review',2100]]){
   const request=await readTaskPreview(task);
   assert.equal(request.temperature,0.65);assert.equal(request.max_tokens,limit);
   assert.deepEqual(request.thinking,{type:'disabled'});assert.equal('reasoning_effort' in request,false);
  }
  await page.locator('[data-task-clear="chapter_analysis"]').click();
  await page.locator('[data-task-clear="semantic_review"]').click();
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用回顾');
  assert.equal(await taskField('semantic_review','reasoning').locator('option').first().textContent(),'沿用检查');
  assert.deepEqual((await saveTaskSettings('pendingClearAliases')).task_parameters,raw);
  assert.equal((await readTaskPreview('chapter_analysis')).temperature,0);
  assert.equal((await readTaskPreview('semantic_review')).temperature,0.2);
  await api('/api/ai/settings','PUT',{task_parameters:{...raw,chapter_analysis:{seed:42}}});await reopenSettings();
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用全局');
  const hidden=await saveTaskSettings('pendingHiddenExplicitAlias');
  assert.deepEqual(hidden.task_parameters.chapter_analysis,{seed:42});
  assert.equal((await readTaskPreview('chapter_analysis')).temperature,0.65);
  await page.locator('[data-task-clear="chapter_analysis"]').click();
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用回顾');
  await settingsScreenshot('task-settings-alias-inheritance.png');
  assert.deepEqual((await saveTaskSettings('pendingClearHiddenAlias')).task_parameters,raw);
 });
 await test('单用途恢复图标清除整个参数对象，模型及其余八用途保留且刷新不复活隐藏字段',async()=>{
  const raw=Object.fromEntries(tasks.map((task,index)=>[task,{
   temperature:index/10,max_tokens:800+index*100,top_p:0.4,seed:40+index,reasoning_effort:'low'}]));
  await api('/api/ai/settings','PUT',{task_parameters:raw});await reopenSettings();
  const models=(await api('/api/ai/settings')).settings.models;
  await page.locator('[data-task-clear="scene"]').click();
  assert.equal(await page.locator('[data-task-model="scene"]').inputValue(),models.scene);
  assert.equal(await taskField('scene','temperature').inputValue(),'');
  assert.equal(await taskField('scene','max_tokens').inputValue(),'');
  assert.equal(await taskField('scene','reasoning').inputValue(),'');
  const expected={...raw};delete expected.scene;
  const before=calls.length;
  await failTaskSettingsSave('pendingFailedClearWholeTask');
  assert.equal(calls.length,before);
  assert.deepEqual((await api('/api/ai/settings')).settings.task_parameters,raw);
  assert.equal(await taskField('scene','temperature').inputValue(),'');
  assert.equal(await taskField('scene','max_tokens').inputValue(),'');
  assert.equal(await taskField('scene','reasoning').inputValue(),'');
  const saved=await saveTaskSettings('pendingClearWholeTask');
  assert.deepEqual(saved.task_parameters,expected);assert.deepEqual(saved.models,models);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.deepEqual((await saveTaskSettings('pendingClearWholeReload')).task_parameters,expected);
  await taskField('scene','temperature').fill('0');
  const replaced=await saveTaskSettings('pendingOverrideAfterClear');
  assert.deepEqual(replaced.task_parameters.scene,{temperature:0});
  await page.locator('[data-task-clear="scene"]').click();
  await taskField('scene','max_tokens').fill('900');
  const revised=await saveTaskSettings('pendingOverrideAfterLocalClear');
  assert.deepEqual(revised.task_parameters.scene,{max_tokens:900});
 });
 await test('用途思考开关与强度互换仅保存一种形式，空白继承且其他参数不改',async()=>{
  const original={temperature:0,max_tokens:750,seed:19,top_p:0.5};
  await api('/api/ai/settings','PUT',{task_parameters:{scene:original}});await reopenSettings();
  for(const [selection,expected] of [['deepseek_disabled',{thinking:{type:'disabled'}}],
   ['high',{reasoning_effort:'high'}],['deepseek_enabled',{thinking:{type:'enabled'}}],['',{}]]){
   await taskField('scene','reasoning').selectOption(selection);
   const saved=await saveTaskSettings('pendingThinkingTask'+(selection||'inherit'));
   assert.deepEqual(saved.task_parameters.scene,{...original,...expected});
   assert.equal('thinking' in saved.task_parameters.scene&&'reasoning_effort' in saved.task_parameters.scene,false);
  }
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.equal(await taskField('scene','reasoning').inputValue(),'');
  assert.deepEqual((await readTaskPreview('scene')).thinking,{type:'disabled'});
 });
 await test('用途数值不合格时表单拒绝发送，任意有效小数温度可以保存',async()=>{
  const before=calls.length;let writes=0;
  const track=request=>{if(request.method()==='PUT'&&new URL(request.url()).pathname==='/api/ai/settings')writes++};
  page.on('request',track);
  for(const [parameter,value] of [['temperature','-0.1'],['temperature','2.1'],
   ['max_tokens','0'],['max_tokens','128001'],['max_tokens','1.5']]){
   await taskField('scene',parameter).fill(value);
   assert.equal(await page.locator('#modelSettings').evaluate(form=>form.checkValidity()),false);
   await page.locator('#modelSettings').evaluate(form=>form.requestSubmit());
   assert.equal(writes,0);
   await taskField('scene',parameter).fill('');
  }
  await taskField('scene','temperature').fill('0.375');
  const saved=await saveTaskSettings('pendingFractionalTaskTemperature');
  assert.equal(saved.task_parameters.scene.temperature,0.375);assert.equal(writes,1);
  assert.equal(calls.length,before);page.off('request',track);
  await page.locator('#modelTemperature').fill('0');
  assert.equal((await saveTaskSettings('pendingGlobalZeroTemperature')).parameters.temperature,0);
  assert.equal((await readTaskPreview('polish')).temperature,0);
  await page.locator('#modelTemperature').fill('');
  const cleared=await saveTaskSettings('pendingGlobalBlankTemperature');
  assert.equal('temperature' in cleared.parameters,false);
  await reopenSettings();
  assert.equal(await page.locator('#modelTemperature').inputValue(),'');
  assert.equal(await page.locator('#modelTemperature').getAttribute('placeholder'),'按用途默认');
  assert.equal((await readTaskPreview('continue')).temperature,0.65);
  assert.equal((await readTaskPreview('polish')).temperature,0.35);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.equal(await page.locator('#modelTemperature').inputValue(),'');
  assert.equal('temperature' in (await saveTaskSettings('pendingReloadGlobalBlankTemperature')).parameters,false);
  await api('/api/ai/settings','PUT',{parameters:{}});await reopenSettings();
  assert.equal(await page.locator('#modelTemperature').inputValue(),'');
  assert.equal(await page.locator('#modelMaxTokens').inputValue(),'3000');
  assert.equal('temperature' in (await saveTaskSettings('pendingLegacyBlankGlobalParameters')).parameters,false);
  assert.equal((await readTaskPreview('continue')).temperature,0.65);
  assert.equal((await readTaskPreview('polish')).temperature,0.35);
  assert.equal(calls.length,before);
 });
 await settingsScreenshot('desktop-settings.png');
 assert.deepEqual(pageErrors,[]);
 results.provider_requests=calls.length;results.source_files_sha256_after=fingerprint();
 results.source_unchanged=JSON.stringify(results.source_files_sha256_before)===JSON.stringify(results.source_files_sha256_after);
 assert.equal(results.source_unchanged,true);results.passed=true;
}

main().catch(async error=>{
 results.failure={message:error.message,stack:error.stack,server_log:serverLog.slice(-5000)};
 if(page&&!page.isClosed())try{await page.screenshot({path:path.join(temporary,'desktop-settings-failure.png'),fullPage:true})}catch{}
 process.exitCode=1;
}).finally(async()=>{
 if(release)release();
 if(browser)await browser.close();
 if(app&&app.exitCode===null){
  const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill('SIGTERM');
  await Promise.race([stopped,pause(2000)]);if(app.exitCode===null)app.kill('SIGKILL');await stopped;
 }
 if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
 results.finished_at=new Date().toISOString();fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(results,null,2)+'\n');
 console.log(JSON.stringify(results,null,2));
});
