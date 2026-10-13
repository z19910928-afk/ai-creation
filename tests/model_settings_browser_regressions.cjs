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
const sources=['app.js','author-workspace.js','moyu_settings.py'];
const fingerprint=()=>Object.fromEntries(sources.map(file=>[file,
 require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]));
const results={passed:false,scope:'电脑端模型设置、隔离SQLite、免费本机模拟提供方',
 started_at:new Date().toISOString(),cases:[],artifacts:temporary,source_files_sha256_before:fingerprint()};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,provider,browser,page,base,mode='normal',release,lastProbe,serverLog='';
const calls=[],pageErrors=[];

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
  await page.locator('#modelSettings [type="submit"]').click();
  await page.waitForFunction(()=>authorSettings?.model==='requested-fixture'&&authorSettings.parameters?.thinking?.type==='disabled');
  assert.equal(calls.length,0);assert.match(await page.locator('#modelProbeStatus').textContent(),/尚未验证/);
  for(const width of [1440,1280]){
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   const box=await page.locator('#modelSettings').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);
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
  await page.screenshot({path:path.join(temporary,'model-settings-success.png'),fullPage:true});
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
  await page.screenshot({path:path.join(temporary,'model-settings-failed.png'),fullPage:true});
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
  await page.locator('#modelSettings [type="submit"]').click();
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
   model:'stale-save-A-fixture',daily_limit:100},'default-project');
  await page.locator('[data-view="settings"]').click();await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='stale-save-A-fixture');
  let resume,observed;const held=new Promise(resolve=>observed=resolve);let captured=false;
  await page.route('**/api/ai/settings',async route=>{
   if(captured||route.request().method()!=='PUT')return route.continue();
   captured=true;const response=await route.fetch();observed();
   await new Promise(resolve=>{resume=resolve});await route.fulfill({response});
  });
  await page.locator('#modelDefault').fill('stale-save-A-fixture');
  await page.evaluate(()=>{window.pendingStaleSave=document.querySelector('#modelSettings').requestSubmit()});
  await held;
  await api('/api/ai/settings','PUT',{model:'stale-save-B-fixture'},'default-project');
  await page.locator('[data-view="editor"]').click();await page.locator('[data-view="settings"]').click();
  await page.locator('#modelSettings').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modelDefault')?.value==='stale-save-B-fixture');
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'stale-save-B-fixture',status:'stale-save-B-fixture',input:'stale-save-B-fixture'});
  resume();await page.evaluate(()=>window.pendingStaleSave);await page.unroute('**/api/ai/settings');
  await page.waitForFunction(()=>document.querySelector('#modelSettings [type="submit"]')&&!document.querySelector('#modelSettings [type="submit"]').disabled);
  assert.deepEqual(await page.evaluate(()=>({model:authorSettings.model,status:aiStatus.model,input:document.querySelector('#modelDefault').value})),
   {model:'stale-save-B-fixture',status:'stale-save-B-fixture',input:'stale-save-B-fixture'});
  const count=calls.length;
  await page.locator('#modelSettings [type="submit"]').click();
  await page.waitForFunction(()=>authorSettings?.model==='stale-save-B-fixture'&&
   !document.querySelector('#modelSettings [type="submit"]')?.disabled);
  const saved=await api('/api/ai/settings','GET',undefined,'default-project');
  assert.equal(saved.settings.model,'stale-save-B-fixture');
  assert.equal(await page.evaluate(()=>authorSettings.model),'stale-save-B-fixture');
  assert.equal(await page.evaluate(()=>aiStatus.model),'stale-save-B-fixture');
  assert.equal(await page.locator('#modelDefault').inputValue(),'stale-save-B-fixture');
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
  await page.evaluate(()=>{window.pendingQueuedSettings=document.querySelector('#modelSettings').requestSubmit()});
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
   model:'other-project-fixture',daily_limit:100},other);
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
  assert.match(await page.locator('#modelProbeStatus').textContent(),/尚未验证/);
  assert.doesNotMatch(await page.locator('#modelProbeStatus').textContent(),/实测成功/);
  assert.equal(calls.length,count+1);
 });
 await page.screenshot({path:path.join(temporary,'desktop-settings.png'),fullPage:true});
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
