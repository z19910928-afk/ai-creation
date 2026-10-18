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
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-connections-browser-'));
const secret='synthetic-connection-compatibility-key';
const identityWarning='连接可用，服务未回传模型名称，仅验证可调用';
const calls=[],pageErrors=[],holds=new Set();
const generatedCalls=()=>calls.filter(call=>call.method==='POST').length;
const report={passed:false,scope:'模型列表、连接兼容与身份警示的独立桌面浏览器回归',
 cases:[],artifacts:temporary,synthetic_data_only:true,paid_provider_calls:0};
const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
let application,provider,browser,page,base,providerBase,serverLog='';
let listMode='normal',probeMode='normal',project='default-project',connectionA,connectionB;

async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',body,owner=project){
 const response=await fetch(base+route,{method,headers:{
  'Content-Type':'application/json','X-Moyu-Project':owner},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();
 assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(data)}`);return data;
}
async function test(name,action){
 console.log(JSON.stringify({test:name,state:'running'}));
 await action();report.cases.push({name,passed:true});
 console.log(JSON.stringify({test:name,state:'passed'}));
}
async function settingsPage(){
 await page.locator('[data-view="settings"]').click();
 await page.locator('#modelConnectionForm').waitFor();await page.locator('#modelSettings').waitFor();
}
async function reopenSettings(){
 await page.locator('[data-view="editor"]').click();await settingsPage();
}
async function fillConnection(model,url=providerBase){
 await page.locator('#connectionReset').click();
 await page.locator('#connectionName').fill(model+' 连接');
 await page.locator('#connectionBaseUrl').fill(url);
 await page.locator('#connectionModel').fill(model);
 await page.locator('#connectionKey').fill(secret);
 await page.locator('#connectionKey').blur();
 await page.locator('#connectionModelListStatus[data-state="success"]').waitFor();
}
async function openCompatibility(){
 await page.locator('.settings-compatibility').evaluate(details=>details.open=true);
}
async function waitIdle(){
 await page.waitForFunction(()=>document.querySelector('#connectionProbe')?.disabled===false);
}
async function probeDraft(){
 const response=page.waitForResponse(response=>
  new URL(response.url()).pathname==='/api/ai/connections/probe'&&response.request().method()==='POST');
 await page.locator('#connectionProbe').click();
 const result=await (await response).json();await waitIdle();return result;
}
async function saveConnection(){
 const before=generatedCalls();
 const response=page.waitForResponse(response=>
  new URL(response.url()).pathname==='/api/ai/connections'&&response.request().method()==='POST');
 await page.locator('#connectionSave').click();
 const result=await (await response).json();await waitIdle();
 assert.ok(result.connection?.id,'保存成功必须返回连接身份');
 assert.equal(generatedCalls(),before,'保存模型不能再次调用模型');return result.connection;
}
async function fetchModels(expected='success'){
 await page.waitForFunction(()=>document.querySelector('#connectionFetchModels')?.disabled===false);
 const response=page.waitForResponse(response=>
  new URL(response.url()).pathname==='/api/ai/connections/models'&&response.request().method()==='POST');
 await page.locator('#connectionFetchModels').click();
 const result=await (await response).json();
 await page.locator(`#connectionModelListStatus[data-state="${expected}"]`).waitFor();
 assert.equal(await page.locator('#connectionFetchModels').isEnabled(),true);return result;
}
async function modelChoices(){
 return page.locator('#connectionModelList [role="option"]').evaluateAll(options=>options.map(option=>option.dataset.modelValue));
}
async function saveSettings(){
 const before=generatedCalls();
 await page.evaluate(()=>{
  const form=document.querySelector('#modelSettings'),submit=form.onsubmit;
  form.onsubmit=event=>{form.onsubmit=submit;window.__compatibilitySave=submit.call(form,event)};
  form.requestSubmit();
  if(!(window.__compatibilitySave instanceof Promise))throw new Error('用途设置没有执行保存');
 });
 await page.evaluate(()=>window.__compatibilitySave);
 await page.waitForFunction(()=>document.querySelector('#modelSettings [type="submit"]')?.disabled===false);
 assert.equal(await page.locator('#modelSettings .form-error').textContent(),'');
 assert.equal(generatedCalls(),before,'用途保存不能外发模型请求');
 return (await api('/api/ai/settings')).settings;
}
async function saveAgentSettings(){
 const before=generatedCalls();
 await page.locator('#agentConnectionSettings [type="submit"]').click();
 await page.locator('#agentProbeStatus').filter({hasText:'总管设置已保存'}).waitFor();
 assert.equal(generatedCalls(),before,'保存总管设置不能调用模型');
 return (await api('/api/agent-chat/settings')).settings;
}
async function probeAgent(){
 const response=page.waitForResponse(response=>
  new URL(response.url()).pathname==='/api/agent-chat/settings/probe'&&response.request().method()==='POST');
 await page.locator('#agentConnectionProbe').click();
 const result=await (await response).json();
 await page.waitForFunction(()=>document.querySelector('#agentConnectionProbe')?.disabled===false);
 return result;
}
async function frozenRequest(task){
 const before=generatedCalls(),state=await api('/api/state');
 const queued=await api('/api/ai/queue','POST',{task,chapter_id:state.chapters[0].id,defer:true});
 try{
  assert.equal(queued.auto_started,false);
  const run=await api('/api/ai/runs/'+queued.ai_run_id);
  assert.equal(run.consent.request_audit.dispatched,false);
  assert.equal(generatedCalls(),before,'冻结请求用于检查配置，不应调用模型');
  return run.consent.request_audit.payload;
 }finally{await api('/api/ai/runs/'+queued.ai_run_id+'/cancel','POST',{})}
}
async function switchProject(identifier){
 await page.locator('[data-view="library"]').click();
 await page.locator(`[data-project-open="${identifier}"]`).click();
 await page.waitForFunction(id=>requestedProject===id&&backendOnline===true,identifier);
 project=identifier;await settingsPage();
}
async function screenshot(name){
 await page.locator('#toast.show').waitFor({state:'hidden',timeout:5000});
 await page.evaluate(async()=>{
  await document.fonts.ready;
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 });
 await page.screenshot({path:path.join(temporary,name),fullPage:true,animations:'disabled'});
 (report.screenshots||=[]).push(name);
}
async function withHeldResponse(endpoint,start,action){
 let observe,release,complete,routeError;
 const observed=new Promise(resolve=>observe=resolve);
 const gate=new Promise(resolve=>release=resolve);
 const completed=new Promise(resolve=>complete=resolve);
 holds.add(release);
 const pattern='**'+endpoint;
 const handler=async route=>{
  try{
   const response=await route.fetch();observe();await gate;await route.fulfill({response});
  }catch(error){routeError=error;observe()}
  finally{complete()}
 };
 await page.route(pattern,handler);
 try{
  await start();await observed;
  if(routeError)throw routeError;
  await action();release();await completed;
  if(routeError&&!/Invalid InterceptionId|already handled|aborted/i.test(routeError.message))throw routeError;
 }finally{
  release();holds.delete(release);await page.unroute(pattern,handler);
 }
}

async function main(){
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);
  request.on('end',()=>{
   const payload=raw?JSON.parse(raw):null;
   calls.push({method:request.method,path:request.url,payload,
    key_matches:request.headers.authorization==='Bearer '+secret});
   if(request.method==='GET'){
    assert.ok(request.url.endsWith('/models'),'列表请求必须访问模型列表端点');
    if(listMode==='failed'){
     response.writeHead(503,{'Content-Type':'application/json'});
     response.end('{"error":{"message":"fixture model list unavailable"}}');return;
    }
    response.writeHead(200,{'Content-Type':'application/json'});
    response.end(JSON.stringify({data:listMode==='malformed'?[{id:null}]:[
     {id:'listed-fixture-alpha'},{id:'listed-fixture-beta'},{id:'listed-fixture-alpha'},
     {id:'  listed-fixture-trimmed  '},
    ]}));return;
   }
   response.writeHead(probeMode==='unauthorized'?401:200,{'Content-Type':'application/json'});
   if(probeMode==='unauthorized'){
    response.end('{"error":{"message":"fixture authorization denied"}}');return;
   }
   response.end(JSON.stringify({
    ...(probeMode==='unreported'?{}:{model:probeMode==='mismatch'?'wrong-fixture-model':payload.model}),
    choices:[{message:{content:'连接正常'},finish_reason:'stop'}],
    usage:{prompt_tokens:13,completion_tokens:7,total_tokens:20},
   }));
  });
 });
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
 providerBase=`http://127.0.0.1:${provider.address().port}/v1`;
 const port=await freePort();base=`http://127.0.0.1:${port}`;
 const environment={...process.env};
 for(const key of Object.keys(environment))if(key.startsWith('MOYU_'))delete environment[key];
 application=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,env:{
  ...environment,MOYU_DB:path.join(temporary,'book.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(port),
  MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'100',
  MOYU_SECRET_KEY:'',MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials'),
  MOYU_BACKUP_DIR:path.join(temporary,'backups'),
 },stdio:['ignore','ignore','pipe']});
 application.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let attempt=0;attempt<200;attempt++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  assert.ok(application.exitCode===null&&attempt<199,'独立临时服务没有启动');await pause(40);
 }
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>pageErrors.push(error.message));
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline===true);await settingsPage();

 await test('新连接默认不请求用量统计，模型未测试不能保存或用于写作',async()=>{
  await fillConnection('manual-fixture-model');
  assert.equal(await page.locator('#connectionOutputTokenParameter').inputValue(),'max_tokens');
  assert.equal(await page.locator('#connectionIncludeUsage').isChecked(),false);
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(await page.locator('#modelDefault option[value]:not([value=""])').count(),0);
  assert.equal((await api('/api/ai/connections')).connections.length,0);
  assert.equal(generatedCalls(),0);
 });
 await test('获取模型列表只查询清单，不自动改模型、不测试、不保存',async()=>{
  const before=(await api('/api/ai/settings')).settings;
  const result=await fetchModels();
  assert.equal(result.endpoint,providerBase+'/models');
  assert.deepEqual(new Set(await modelChoices()),new Set([
   'listed-fixture-alpha','listed-fixture-beta','listed-fixture-trimmed',
  ]));
  assert.equal(await page.locator('#connectionModel').inputValue(),'manual-fixture-model');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(generatedCalls(),0);assert.ok(calls.some(call=>call.method==='GET'));
  assert.deepEqual((await api('/api/ai/settings')).settings,before);
 });
 await test('列表服务失败仍可手填模型，重新获取可恢复',async()=>{
  for(const failure of ['failed','malformed']){
   listMode=failure;await fetchModels('failed');
   assert.match(await page.locator('#connectionModelListStatus').textContent(),/获取模型列表失败/);
   assert.equal(await page.locator('#connectionModel').isEnabled(),true);
   await page.locator('#connectionModel').fill('manually-entered-fixture');
   assert.equal(await page.locator('#connectionModel').inputValue(),'manually-entered-fixture');
   assert.equal((await api('/api/ai/connections')).connections.length,0);
  }
  listMode='normal';await fetchModels();
  assert.equal(await page.locator('#connectionModel').inputValue(),'manually-entered-fixture');
 });
 await test('浏览器网络失败可重试，不阻断手动填写和连接测试',async()=>{
  const pattern='**/api/ai/connections/models';
  const abort=route=>route.abort('failed');
  await page.route(pattern,abort);
  try{
   await page.locator('#connectionFetchModels').click();
   await page.locator('#connectionModelListStatus[data-state="failed"]').waitFor();
   assert.equal(await page.locator('#connectionFetchModels').isEnabled(),true);
   assert.equal(await page.locator('#connectionProbe').isEnabled(),true);
   assert.equal(await page.locator('#connectionModel').inputValue(),'manually-entered-fixture');
  }finally{await page.unroute(pattern,abort)}
  await fetchModels();assert.ok((await modelChoices()).includes('listed-fixture-alpha'));
 });
 await test('两个兼容选项的任何修改都会撤销已通过的测试资格',async()=>{
  let result=await probeDraft();
  assert.equal(result.probe.success,true);assert.equal(result.probe.identity_status,'exact');
  assert.equal(await page.locator('#connectionSave').isEnabled(),true);
  assert.equal(calls.at(-1).payload.max_tokens>0,true);
  assert.equal('max_completion_tokens' in calls.at(-1).payload,false);
  assert.equal('stream_options' in calls.at(-1).payload,false);
  await openCompatibility();
  await page.locator('#connectionOutputTokenParameter').selectOption('max_completion_tokens');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  result=await probeDraft();assert.equal(result.probe.success,true);
  assert.equal(calls.at(-1).payload.max_completion_tokens>0,true);
  assert.equal('max_tokens' in calls.at(-1).payload,false);
  await page.locator('#connectionIncludeUsage').check();
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  result=await probeDraft();assert.equal(result.probe.success,true);
  assert.equal('stream_options' in calls.at(-1).payload,false,'非流式测试无需流式统计字段');
  await page.locator('#connectionIncludeUsage').uncheck();
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  await page.locator('#connectionIncludeUsage').check();
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal((await probeDraft()).probe.success,true);
  connectionA=await saveConnection();
  assert.equal(connectionA.verified,true);
  assert.equal(connectionA.output_token_parameter,'max_completion_tokens');
  assert.equal(connectionA.include_usage,true);
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
 });
 await test('保存兼容配置后刷新和沿用密钥获取列表均保持原配置',async()=>{
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  await page.locator(`[data-connection-edit="${connectionA.id}"]`).click();
  assert.equal(await page.locator('#connectionOutputTokenParameter').inputValue(),'max_completion_tokens');
  assert.equal(await page.locator('#connectionIncludeUsage').isChecked(),true);
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  await fetchModels();assert.equal(calls.at(-1).key_matches,true);
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  const saved=(await api('/api/ai/connections')).connections[0];
  assert.equal(saved.output_token_parameter,'max_completion_tokens');assert.equal(saved.include_usage,true);
 });
 await test('用途模型使用保存的输出字段和流式用量开关，不额外调用模型',async()=>{
  await page.locator('#modelDefault').selectOption(connectionA.id);
  await page.locator('[data-task-model="continue"]').selectOption(connectionA.id);
  await saveSettings();
  const request=await frozenRequest('continue');
  assert.equal(request.model,connectionA.model);assert.equal(request.stream,true);
  assert.equal(request.max_completion_tokens>0,true);
  assert.equal('max_tokens' in request,false);
  assert.deepEqual(request.stream_options,{include_usage:true});
 });
 await test('未回传模型名称仍可保存，但测试和模型库必须明确提示未核实名称',async()=>{
  await fillConnection('unreported-fixture-model');probeMode='unreported';
  const result=await probeDraft();
  assert.equal(result.probe.success,true);assert.equal(result.probe.identity_status,'unreported');
  assert.equal(result.probe.model_identity_match,null);assert.ok(result.verification_token);
  assert.match(await page.locator('#connectionProbeStatus').textContent(),new RegExp(identityWarning));
  assert.equal(await page.locator('#connectionProbeStatus').getAttribute('data-identity'),'unreported');
  assert.equal(await page.locator('#connectionSave').isEnabled(),true);
  connectionB=await saveConnection();probeMode='normal';
  assert.equal(connectionB.verified,true);
  assert.equal(connectionB.last_probe.identity_status,'unreported');
  assert.match(await page.locator(`[data-model-connection="${connectionB.id}"]`).textContent(),new RegExp(identityWarning));
  await page.locator('#modelDefault').selectOption(connectionB.id);
  await page.locator('[data-task-model="continue"]').selectOption(connectionA.id);
  await saveSettings();await reopenSettings();
  assert.match(await page.locator('#modelProbeStatus').textContent(),new RegExp(identityWarning));
  const standard=await frozenRequest('polish'),compatible=await frozenRequest('continue');
  assert.equal(standard.model,connectionB.model);assert.equal(standard.max_tokens>0,true);
  assert.equal('max_completion_tokens' in standard,false);
  assert.equal('stream_options' in standard,false);
  assert.equal(compatible.model,connectionA.model);
  assert.deepEqual(compatible.stream_options,{include_usage:true});
 });
 await test('名称不匹配与授权失败继续拒绝，不能误显示未回传模型名的成功提示',async()=>{
  await fillConnection('rejected-fixture-model');
  for(const failure of ['mismatch','unauthorized']){
   probeMode=failure;const result=await probeDraft();
   assert.equal(result.probe.success,false);assert.ok(!result.verification_token);
   assert.equal(await page.locator('#connectionSave').isDisabled(),true);
   assert.equal(await page.locator('#connectionProbeStatus').getAttribute('data-state'),'failed');
   assert.equal((await page.locator('#connectionProbeStatus').textContent()).includes(identityWarning),false);
   assert.equal((await api('/api/ai/connections')).connections.length,2);
  }
  probeMode='normal';
 });
 await test('采用服务回传名称只填写草稿，必须重新测试且不能静默切换模型',async()=>{
  await fillConnection('requested-alias-fixture');probeMode='mismatch';
  assert.equal((await probeDraft()).probe.success,false);
  const apply=page.locator('#connectionUseReturnedModel');
  assert.equal(await apply.isVisible(),true);
  const before=generatedCalls();await apply.click();
  assert.equal(await page.locator('#connectionModel').inputValue(),'wrong-fixture-model');
  assert.equal(generatedCalls(),before,'采用回传名称只修改草稿，不应调用模型');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(await apply.isVisible(),false);
  assert.equal((await api('/api/ai/connections')).connections.length,2);
  probeMode='normal';assert.equal((await probeDraft()).probe.success,true);
  assert.equal(await page.locator('#connectionSave').isEnabled(),true);
  await page.locator('#connectionModel').fill('new-alias-draft-fixture');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(await apply.isVisible(),false);
 });
 await test('获取列表期间修改地址，迟到列表不能写入当前草稿',async()=>{
  await fillConnection('address-change-fixture');
  await withHeldResponse('/api/ai/connections/models',
   ()=>page.locator('#connectionFetchModels').click(),async()=>{
    await page.locator('#connectionBaseUrl').fill(providerBase+'/changed');
    assert.equal(await page.locator('#connectionFetchModels').isEnabled(),true);
   });
  assert.deepEqual(await modelChoices(),[]);
  assert.equal(await page.locator('#connectionBaseUrl').inputValue(),providerBase+'/changed');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
 });
 await test('获取列表期间改为编辑另一连接，迟到列表不能污染已保存连接',async()=>{
  await fillConnection('editing-change-fixture');
  await withHeldResponse('/api/ai/connections/models',
   ()=>page.locator('#connectionFetchModels').click(),async()=>{
    await page.locator(`[data-connection-edit="${connectionA.id}"]`).click();
   });
  assert.equal(await page.locator('#connectionModel').inputValue(),connectionA.model);
  assert.equal(await page.locator('#connectionOutputTokenParameter').inputValue(),'max_completion_tokens');
  assert.equal(await page.locator('#connectionIncludeUsage').isChecked(),true);
  assert.deepEqual(await modelChoices(),[]);
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
 });
 await test('离开并重新进入设置后，迟到列表不能恢复旧页面草稿或未保存密钥',async()=>{
  await fillConnection('navigation-change-fixture');
  await withHeldResponse('/api/ai/connections/models',
   ()=>page.locator('#connectionFetchModels').click(),()=>reopenSettings());
  assert.equal(await page.locator('#connectionModel').inputValue(),'');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.deepEqual(await modelChoices(),[]);
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
 });
 await test('切换作品后迟到模型列表不串台，新作品没有旧连接或草稿',async()=>{
  const created=await api('/api/projects','POST',{title:'模型连接隔离合成作品',goal:50000});
  const other=created.project.id;
  await fillConnection('project-change-fixture');
  await withHeldResponse('/api/ai/connections/models',
   ()=>page.locator('#connectionFetchModels').click(),()=>switchProject(other));
  assert.equal(await page.locator('#connectionModel').inputValue(),'');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.deepEqual(await modelChoices(),[]);
  assert.equal((await api('/api/ai/connections')).connections.length,0);
  assert.equal((await api('/api/ai/connections','GET',undefined,'default-project')).connections.length,2);
  await switchProject('default-project');
 });
 await test('连接测试未完成时保护作品切换，完成后切换不继承旧测试凭证',async()=>{
  const created=await api('/api/projects','POST',{title:'连接测试隔离合成作品',goal:50000});
  await fillConnection('late-probe-fixture');
  await withHeldResponse('/api/ai/connections/probe',
   ()=>page.locator('#connectionProbe').click(),async()=>{
    await page.locator('[data-view="library"]').click();
    await page.locator(`[data-project-open="${created.project.id}"]`).click();
    await page.locator('#toast').filter({hasText:'切换已暂停'}).waitFor();
    assert.equal(await page.evaluate(()=>requestedProject),'default-project');
    assert.equal((await api('/api/ai/connections','GET',undefined,created.project.id)).connections.length,0);
   });
  await page.waitForFunction(()=>independentWrite===false);
  await switchProject(created.project.id);
  assert.equal(await page.locator('#connectionModel').inputValue(),'');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal((await api('/api/ai/connections')).connections.length,0);
  await switchProject('default-project');
 });
 await test('总管沿用默认模型时，未回传名称的成功探测保留明确警示',async()=>{
  await page.locator('#agentConnectionSettings').waitFor();
  assert.equal(await page.locator('[name="agentConnectionMode"][value="inherit"]').isChecked(),true);
  probeMode='unreported';const result=await probeAgent();probeMode='normal';
  assert.equal(result.probe.success,true);
  assert.equal(result.probe.identity_status,'unreported');
  assert.equal(result.probe.model_identity_match,null);
  await page.locator('#agentProbeStatus').filter({hasText:identityWarning}).waitFor();
  assert.equal((await api('/api/agent-chat/settings')).settings.model,connectionB.model);
 });
 await test('总管独立连接的兼容选项可保存并实际参与测试，未回传提示持续保留',async()=>{
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  await page.locator('#agentBaseUrl').fill(providerBase);
  await page.locator('#agentModel').fill('independent-compatible-fixture');
  await page.locator('#agentApiKey').fill(secret);
  await page.locator('.agent-settings-compatibility').evaluate(details=>details.open=true);
  await page.locator('#agentOutputTokenParameter').selectOption('max_completion_tokens');
  await page.locator('#agentIncludeUsage').check();
  const saved=await saveAgentSettings();
  assert.equal(saved.mode,'independent');
  assert.equal(saved.independent_settings.output_token_parameter,'max_completion_tokens');
  assert.equal(saved.independent_settings.include_usage,true);
  assert.equal(await page.locator('#agentApiKey').inputValue(),'');
  assert.equal(JSON.stringify(saved).includes(secret),false);
  probeMode='unreported';const result=await probeAgent();probeMode='normal';
  assert.equal(result.probe.success,true);assert.equal(result.probe.identity_status,'unreported');
  assert.equal(calls.at(-1).payload.model,'independent-compatible-fixture');
  assert.equal(calls.at(-1).payload.max_completion_tokens>0,true);
  assert.equal('max_tokens' in calls.at(-1).payload,false);
  assert.equal('stream_options' in calls.at(-1).payload,false);
  await page.locator('#agentProbeStatus').filter({hasText:identityWarning}).waitFor();
  await reopenSettings();
  assert.equal(await page.locator('#agentOutputTokenParameter').inputValue(),'max_completion_tokens');
  assert.equal(await page.locator('#agentIncludeUsage').isChecked(),true);
  assert.match(await page.locator('#agentProbeStatus').textContent(),new RegExp(identityWarning));
 });
 await test('修改总管兼容选项即时撤销旧实测提示，保存后必须重新实测',async()=>{
  await page.locator('.agent-settings-compatibility').evaluate(details=>details.open=true);
  await page.locator('#agentIncludeUsage').uncheck();
  assert.match(await page.locator('#agentProbeStatus').textContent(),/信息已修改/);
  assert.equal((await page.locator('#agentProbeStatus').textContent()).includes(identityWarning),false);
  let saved=await saveAgentSettings();assert.equal(saved.last_probe,null);
  assert.equal(saved.independent_settings.include_usage,false);
  assert.match(await page.locator('#agentProbeStatus').textContent(),/尚未验证/);
  assert.equal((await probeAgent()).probe.success,true);
  await page.locator('.agent-settings-compatibility').evaluate(details=>details.open=true);
  await page.locator('#agentOutputTokenParameter').selectOption('max_tokens');
  assert.match(await page.locator('#agentProbeStatus').textContent(),/信息已修改/);
  saved=await saveAgentSettings();assert.equal(saved.last_probe,null);
  assert.equal(saved.independent_settings.output_token_parameter,'max_tokens');
 });
 await test('总管名称不符或授权失败继续显示检测失败，不混淆为未回传名称的成功',async()=>{
  for(const failure of ['mismatch','unauthorized']){
   probeMode=failure;const result=await probeAgent();
   assert.equal(result.probe.success,false);
   await page.locator('#agentProbeStatus').filter({hasText:'连接检测未通过'}).waitFor();
   assert.equal((await page.locator('#agentProbeStatus').textContent()).includes(identityWarning),false);
   assert.equal((await api('/api/agent-chat/settings')).settings.last_probe.success,false);
  }
  probeMode='normal';
  await page.locator('[name="agentConnectionMode"][value="inherit"]').check();
  await saveAgentSettings();
 });
 await test('三个桌面尺寸下新增控件可见且不重叠，密钥不进入公共响应与浏览器缓存',async()=>{
  await page.locator(`[data-connection-edit="${connectionA.id}"]`).click();
  await openCompatibility();
  for(const viewport of [{width:1100,height:760},{width:1280,height:900},{width:1440,height:1000}]){
   await page.setViewportSize(viewport);
   const issues=await page.locator('#modelConnectionForm').evaluate(form=>{
    const problems=[],controls=[...form.querySelectorAll('input,select,button')]
     .map(element=>element.getBoundingClientRect()).filter(box=>box.width&&box.height);
    controls.forEach((box,index)=>{
     if(box.left<0||box.right>innerWidth+1)problems.push('control-bounds');
     for(const previous of controls.slice(0,index)){
      if(Math.min(previous.right,box.right)-Math.max(previous.left,box.left)>1&&
       Math.min(previous.bottom,box.bottom)-Math.max(previous.top,box.top)>1)problems.push('overlap');
     }
    });
    if(document.documentElement.scrollWidth>innerWidth)problems.push('page-overflow');return problems;
   });
   assert.deepEqual(issues,[]);await screenshot(`model-connections-${viewport.width}.png`);
  }
  for(const value of [await api('/api/ai/settings'),await api('/api/ai/connections')]){
   assert.equal(JSON.stringify(value).includes(secret),false);
   assert.equal(JSON.stringify(value).includes('_credential_ref'),false);
   assert.equal(JSON.stringify(value).includes('_verified_signature'),false);
  }
  const storage=await page.evaluate(()=>[...Object.values(localStorage),...Object.values(sessionStorage)].join('\n'));
  assert.equal(storage.includes(secret),false);assert.equal(serverLog.includes(secret),false);
  assert.equal(calls.filter(call=>call.method==='POST').every(call=>call.key_matches),true);
 });
 assert.deepEqual(pageErrors,[]);report.provider_requests=calls.length;
 report.inventory_queries=calls.filter(call=>call.method==='GET').length;
 report.connection_probes=calls.filter(call=>call.method==='POST').length;
 report.page_errors=pageErrors.length;report.passed=true;
}

main().catch(async error=>{
 report.failure={message:error.message,stack:error.stack,server_log:serverLog.slice(-3000)};
 if(page&&!page.isClosed())try{await screenshot('model-connections-failure.png')}catch{}
 process.exitCode=1;
}).finally(async()=>{
 for(const release of holds)release();
 if(browser)await browser.close();
 if(application&&application.exitCode===null){
  const stopped=new Promise(resolve=>application.once('exit',resolve));application.kill('SIGTERM');
  await Promise.race([stopped,pause(2000)]);if(application.exitCode===null)application.kill('SIGKILL');await stopped;
 }
 if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
 fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
});
