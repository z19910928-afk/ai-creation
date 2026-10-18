'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const http=require('node:http');
const net=require('node:net');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require(process.env.MOYU_PLAYWRIGHT_MODULE||'playwright');

const root=path.resolve(__dirname,'..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-settings-browser-'));
const secret='synthetic-model-library-browser-key';
const tasks=['continue','polish','outline','scene','summary','check','brainstorm','chapter_analysis','semantic_review'];
const sources=['frontend/app.js','frontend/author-workspace.js','frontend/styles.css','backend/moyu_settings.py','backend/moyu_model_connections.py','backend/server.py'];
const fingerprint=()=>Object.fromEntries(sources.map(file=>[file,
 require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]));
const report={passed:false,scope:'电脑模型库先测试后保存、用途分配与参数，免费本机模拟提供方',
 cases:[],artifacts:temporary,synthetic_data_only:true,paid_provider_calls:0,
 source_files_sha256_before:fingerprint()};
const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
const calls=[],pageErrors=[];
let application,provider,browser,page,base,providerBase,mode='normal',release,serverLog='';
let connectionA,connectionB;

async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function request(route,method='GET',body,project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},
  body:body===undefined?undefined:JSON.stringify(body)});
 return {response,data:await response.json()};
}
async function api(route,method='GET',body,project){
 const {response,data}=await request(route,method,body,project);
 assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(data)}`);return data;
}
async function test(name,action){
 console.log(JSON.stringify({test:name,state:'running'}));
 await action();report.cases.push({name,passed:true});console.log(JSON.stringify({test:name,state:'passed'}));
}
async function settingsPage(){
 await page.locator('[data-view="settings"]').click();
 await page.locator('#modelSettings').waitFor();await page.locator('#connectionAdd').waitFor();
}
async function expandSettings(){
 await page.locator('#modelTaskSettings,#modelAdvancedParameters').evaluateAll(details=>details.forEach(item=>item.open=true));
}
async function closeConnection(){
 if(await page.evaluate(()=>document.querySelector('#connectionDialog')?.open))await page.locator('#connectionCancel').click();
}
async function reopenSettings(){
 await closeConnection();await page.locator('[data-view="editor"]').click();await settingsPage();await expandSettings();
}
async function fillConnection(model,url=providerBase){
 await closeConnection();await page.locator('#connectionAdd').click();
 await page.locator('#connectionName').fill(model+' 连接');
 await page.locator('#connectionBaseUrl').fill(url);await page.locator('#connectionModel').fill(model);
 await page.locator('#connectionKey').fill(secret);
}
async function probeDraft(){
 const response=page.waitForResponse(response=>
  new URL(response.url()).pathname==='/api/ai/connections/probe'&&response.request().method()==='POST');
 await page.locator('#connectionProbe').click();
 const result=await (await response).json();
 return result;
}
async function waitProbe(){
 await page.waitForFunction(()=>{
  const control=document.querySelector('#connectionProbe');return control&&!control.disabled;
 });
}
async function saveConnection(){
 const response=page.waitForResponse(response=>
  new URL(response.url()).pathname==='/api/ai/connections'&&response.request().method()==='POST');
 await page.locator('#connectionSave').click();
 const result=await (await response).json();await waitProbe();
 assert.ok(result.connection?.id,'成功保存必须返回连接身份');return result.connection;
}
async function register(model,url=providerBase){
 const probed=await api('/api/ai/connections/probe','POST',{base_url:url,model,api_key:secret});
 assert.equal(probed.probe.success,true);
 return (await api('/api/ai/connections','POST',
  {name:model+' 连接',verification_token:probed.verification_token})).connection;
}
function holdConnectionCredential(connection){
 const reference=execFileSync(process.env.MOYU_TEST_PYTHON||'python3',['-c',
  'import json,sqlite3,sys\n'+
  'with sqlite3.connect(sys.argv[1]) as conn:\n'+
  ' payload=json.loads(conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?",("default-project",)).fetchone()[0])\n'+
  ' print(next(item["_credential_ref"] for item in payload["connections"] if item["id"]==sys.argv[2]))',
  path.join(temporary,'book.sqlite3'),connection.id],{encoding:'utf8'}).trim();
 assert.match(reference,/^[a-f0-9]{64}-[a-f0-9]{32}$/);
 const file=path.join(temporary,'credentials',reference+'.json'),held=file+'.held-fixture';
 assert.equal(fs.existsSync(held),false);fs.renameSync(file,held);
 return ()=>{if(fs.existsSync(held))fs.renameSync(held,file)};
}
function taskField(task,name){
 return page.locator(`[data-task-row="${task}"] [data-task-parameter="${name}"]`);
}
async function submitSettings(key='pendingModelSettings'){
 await page.evaluate(key=>{
  const form=document.querySelector('#modelSettings'),submit=form.onsubmit;
  form.onsubmit=event=>{form.onsubmit=submit;window[key]=submit.call(form,event)};
  form.requestSubmit();if(!(window[key] instanceof Promise))throw new Error('设置表单没有执行保存');
 },key);
 await page.evaluate(key=>window[key],key);
}
async function saveSettings(){
 await closeConnection();const before=calls.length;await submitSettings();
 await page.waitForFunction(()=>document.querySelector('#modelSettings [type="submit"]')?.disabled===false);
 assert.equal(await page.locator('#modelSettings .form-error').textContent(),'');
 assert.equal(calls.length,before,'保存用途和文风不能调用模型');
 await expandSettings();return (await api('/api/ai/settings')).settings;
}
async function preview(task){
 const state=await api('/api/state');
 const result=await api('/api/ai/prompt-preview','POST',{task,chapter_id:state.chapters[0].id});
 assert.equal(result.request_audit.dispatched,false);return result;
}
async function screenshot(name){
 await page.evaluate(async()=>{
  await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 });
 await page.screenshot({path:path.join(temporary,name),fullPage:true,animations:'disabled'});
 (report.screenshots||=[]).push(name);
}

async function main(){
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);
  request.on('end',async()=>{
   if(request.method==='GET'&&request.url.endsWith('/models')){
    response.writeHead(200,{'Content-Type':'application/json'});
    response.end(JSON.stringify({data:[{id:'model-a-fixture'},{id:'model-b-fixture'}]}));return;
   }
   const payload=JSON.parse(raw),current=mode;
   calls.push({model:payload.model,path:request.url,key_matches:request.headers.authorization==='Bearer '+secret});
   if(current==='held')await new Promise(resolve=>release=resolve);
   response.writeHead(current==='unauthorized'?401:200,{'Content-Type':'application/json'});
   if(current==='unauthorized'){response.end('{"error":"fixture unauthorized"}');return}
   response.end(JSON.stringify({model:current==='mismatch'?'wrong-fixture-model':payload.model,
    choices:[{message:{content:current==='one-character'?'铁':'连接正常'},finish_reason:'stop'}],
    usage:{prompt_tokens:13,completion_tokens:7,total_tokens:20}}));
  });
 });
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
 providerBase=`http://127.0.0.1:${provider.address().port}/v1`;
 const port=await freePort();base=`http://127.0.0.1:${port}`;
 const environment={...process.env};
 for(const key of Object.keys(environment))if(key.startsWith('MOYU_'))delete environment[key];
 application=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,
  env:{...environment,MOYU_DB:path.join(temporary,'book.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(port),
   MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'100',
   MOYU_SECRET_KEY:'',MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials'),
   MOYU_BACKUP_DIR:path.join(temporary,'backups')},stdio:['ignore','ignore','pipe']});
 application.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let index=0;index<200;index++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  assert.ok(application.exitCode===null&&index<199,'临时服务没有启动');await pause(40);
 }
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>pageErrors.push(error.message));
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline===true);await settingsPage();

 await test('配置列表为首屏，弹窗取消即清空草稿且参数默认折叠',async()=>{
  assert.equal(await page.locator('#connectionDialog').evaluate(dialog=>dialog.open),false);
  assert.equal(await page.locator('#modelTaskSettings').evaluate(details=>details.open),false);
  assert.equal(await page.locator('#modelAdvancedParameters').evaluate(details=>details.open),false);
  await page.locator('#connectionAdd').click();await page.locator('#connectionKey').fill(secret);
  await page.locator('#connectionCancel').click();
  assert.equal(await page.locator('#connectionDialog').evaluate(dialog=>dialog.open),false);
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal((await api('/api/ai/connections')).connections.length,0);assert.equal(calls.length,0);
 });
 await test('模型库未测试不可保存，用途只能下拉选择已测试并保存的模型',async()=>{
  await page.locator('#modelTone').fill('克制、重动作');
  await fillConnection('model-a-fixture');assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(await page.locator('#modelDefault').evaluate(element=>element.tagName),'SELECT');
  assert.equal(await page.locator('[data-task-model]').evaluateAll(elements=>
   elements.length===9&&elements.every(element=>element.tagName==='SELECT')),true);
  assert.equal(await page.locator('#modelDefault option[value]:not([value=""])').count(),0);
  assert.equal((await api('/api/ai/connections')).connections.length,0);assert.equal(calls.length,0);
 });
 await test('授权失败和回传名称不符均不能入库，不产生可选择项',async()=>{
  for(const failure of ['unauthorized','mismatch','one-character']){
   mode=failure;const result=await probeDraft();await waitProbe();
   assert.equal(result.probe.success,false);assert.ok(!result.verification_token);
   assert.equal(await page.locator('#connectionSave').isDisabled(),true);
   assert.equal((await api('/api/ai/connections')).connections.length,0);
   assert.equal(await page.locator('#modelDefault option[value]:not([value=""])').count(),0);
  }mode='normal';
 });
 await test('测试只验证当前草稿，修改连接撤销保存资格，成功后另行保存才入库',async()=>{
  let result=await probeDraft();await waitProbe();
  assert.equal(result.probe.success,true);assert.ok(result.verification_token);
  assert.equal(await page.locator('#connectionSave').isEnabled(),true);
  assert.equal((await api('/api/ai/connections')).connections.length,0);
  assert.equal((await api('/api/ai/settings')).settings.configured,false);
  await page.locator('#connectionModel').fill('changed-fixture');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  await page.locator('#connectionModel').fill('model-a-fixture');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  result=await probeDraft();await waitProbe();assert.equal(result.probe.success,true);
  connectionA=await saveConnection();
  assert.equal(connectionA.verified,true);assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal(await page.locator('#modelTone').inputValue(),'克制、重动作');
  assert.equal(await page.locator('#modelDefault').inputValue(),'');
  assert.equal((await api('/api/ai/settings')).settings.default_connection_id||'','');
  assert.equal(await page.locator(`#modelDefault option[value="${connectionA.id}"]`).count(),1);
 });
 await test('第二个不同地址的模型可登记，保存用途不外发并且预览路由正确',async()=>{
  connectionB=await register('model-b-fixture',providerBase+'/second');await reopenSettings();
  await page.locator('#modelDefault').selectOption(connectionA.id);
  await page.locator('[data-task-model="continue"]').selectOption(connectionB.id);
  await page.locator('[data-task-model="summary"]').selectOption(connectionB.id);
  await page.locator('[data-task-model="check"]').selectOption(connectionA.id);
  await page.locator('#modelTone').fill('克制、重动作');const saved=await saveSettings();
  assert.equal(saved.default_connection_id,connectionA.id);assert.equal(saved.task_connections.continue,connectionB.id);
  assert.equal((await preview('continue')).request.model,'model-b-fixture');
  assert.equal((await preview('continue')).request_audit.endpoint,providerBase+'/second/chat/completions');
  assert.equal((await preview('polish')).request.model,'model-a-fixture');
  assert.equal((await preview('chapter_analysis')).request.model,'model-b-fixture');
  assert.equal((await preview('semantic_review')).request.model,'model-a-fixture');
  assert.equal(calls.every(call=>call.key_matches),true);
 });
 await test('手工伪造未验证模型身份被服务拒绝，原用途和密钥不变',async()=>{
  const before=(await api('/api/ai/settings')).settings;
  for(const payload of [{default_connection_id:'not-a-verified-connection'},
   {task_connections:{scene:'not-a-verified-connection'}},
   {model:'bypass-model',base_url:providerBase,api_key:secret},{models:{scene:'freeform-unverified'}}]){
   assert.equal((await request('/api/ai/settings','PUT',payload)).response.status,400);
  }
  const after=(await api('/api/ai/settings')).settings;
  assert.equal(after.default_connection_id,before.default_connection_id);
  assert.deepEqual(after.task_connections,before.task_connections);
  for(const value of [after,await api('/api/ai/connections')]){
   assert.equal(JSON.stringify(value).includes(secret),false);
   assert.equal(JSON.stringify(value).includes('_credential_ref'),false);
   assert.equal(JSON.stringify(value).includes('_verified_signature'),false);
  }
 });
 await test('九用途参数及隐藏参数保持，三个桌面尺寸无横向遮挡',async()=>{
  const seeded=Object.fromEntries(tasks.map((task,index)=>[task,{temperature:index/10,max_tokens:600+index*100,
   top_p:0.4,seed:17+index,...(index%2?{reasoning_effort:'low'}:{thinking:{type:'disabled'}})}]));
  const mappings=Object.fromEntries(tasks.map((task,index)=>[task,index%2?connectionA.id:connectionB.id]));
  await api('/api/ai/settings','PUT',{task_connections:mappings,task_parameters:seeded,
   parameters:{temperature:0.65,max_tokens:3000,top_p:0.8,thinking:{type:'disabled'}}});await reopenSettings();
  assert.deepEqual(await page.locator('[data-task-row]').evaluateAll(rows=>rows.map(row=>row.dataset.taskRow)),tasks);
  const saved=await saveSettings();assert.deepEqual(saved.task_parameters,seeded);
  assert.deepEqual(saved.task_connections,mappings);assert.equal(saved.parameters.top_p,0.8);
  for(const size of [{width:1100,height:760},{width:1280,height:900},{width:1440,height:1000}]){
   await page.setViewportSize(size);
   const issues=await page.locator('.settings-task-table').evaluate(table=>{
    const problems=[],box=table.getBoundingClientRect();
    for(const row of table.querySelectorAll('[data-task-row]')){
     const controls=[...row.querySelectorAll('input,select,button')].map(control=>control.getBoundingClientRect());
     controls.forEach((rect,index)=>{
      if(rect.width<24||rect.left<box.left-1||rect.right>box.right+1)problems.push('bounds');
      for(const previous of controls.slice(0,index)){
       if(Math.min(previous.right,rect.right)-Math.max(previous.left,rect.left)>1&&
          Math.min(previous.bottom,rect.bottom)-Math.max(previous.top,rect.top)>1)problems.push('overlap');
      }
     });
    }if(document.documentElement.scrollWidth>innerWidth)problems.push('page-overflow');return problems;
   });
   assert.deepEqual(issues,[]);
   await page.locator('#connectionAdd').scrollIntoViewIfNeeded();
   await screenshot(`model-library-${size.width}.png`);
   await page.locator('#modelSettings').scrollIntoViewIfNeeded();
   await screenshot(`model-routing-${size.width}.png`);
  }await page.setViewportSize({width:1440,height:1000});
 });
 await test('用途温度零保留，空白仅删数值覆盖，隐藏参数不丢失',async()=>{
  await api('/api/ai/settings','PUT',{task_parameters:{scene:{temperature:0,max_tokens:700,top_p:0.4,
   seed:17,stop:['scene-end'],presence_penalty:0.1,frequency_penalty:-0.2}}});await reopenSettings();
  assert.equal((await saveSettings()).task_parameters.scene.temperature,0);
  assert.equal((await preview('scene')).request.temperature,0);
  await taskField('scene','temperature').fill('');await taskField('scene','max_tokens').fill('');
  const saved=await saveSettings();
  assert.deepEqual(saved.task_parameters.scene,{top_p:0.4,seed:17,stop:['scene-end'],
   presence_penalty:0.1,frequency_penalty:-0.2});
  assert.equal((await preview('scene')).request.temperature,0.65);
  assert.equal((await preview('scene')).request.max_tokens,3000);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.deepEqual((await saveSettings()).task_parameters,saved.task_parameters);
 });
 await test('分析审稿继承回顾检查的整组参数，单设子用途只继承全局',async()=>{
  const raw={summary:{temperature:0,max_tokens:1200,reasoning_effort:'low'},
   check:{temperature:0.2,max_tokens:1400,thinking:{type:'enabled'}}};
  await api('/api/ai/settings','PUT',{task_parameters:raw,task_connections:{summary:connectionB.id,check:connectionA.id}});
  await reopenSettings();
  assert.equal(await taskField('chapter_analysis','temperature').getAttribute('placeholder'),'沿用回顾');
  assert.equal(await taskField('semantic_review','temperature').getAttribute('placeholder'),'沿用检查');
  assert.deepEqual((await saveSettings()).task_parameters,raw);
  assert.equal((await preview('chapter_analysis')).request.temperature,0);
  assert.equal((await preview('chapter_analysis')).request.max_tokens,1200);
  assert.deepEqual((await preview('semantic_review')).request.thinking,{type:'enabled'});
  await taskField('chapter_analysis','max_tokens').fill('1900');await taskField('semantic_review','max_tokens').fill('2100');
  const explicit=await saveSettings();assert.deepEqual(explicit.task_parameters.chapter_analysis,{max_tokens:1900});
  for(const [task,limit] of [['chapter_analysis',1900],['semantic_review',2100]]){
   const payload=(await preview(task)).request;assert.equal(payload.temperature,0.65);
   assert.equal(payload.max_tokens,limit);assert.deepEqual(payload.thinking,{type:'disabled'});
   assert.equal('reasoning_effort' in payload,false);
  }
  await page.locator('[data-task-clear="chapter_analysis"]').click();
  await page.locator('[data-task-clear="semantic_review"]').click();
  assert.deepEqual((await saveSettings()).task_parameters,raw);
 });
 await test('清除一个用途参数不动模型及其他用途，保存失败可重试',async()=>{
  const raw=Object.fromEntries(tasks.map((task,index)=>[task,{temperature:index/10,max_tokens:800+index*100,
   top_p:0.4,seed:40+index,reasoning_effort:'low'}]));
  await api('/api/ai/settings','PUT',{task_parameters:raw});await reopenSettings();
  const model=await page.locator('[data-task-model="scene"]').inputValue();
  await page.locator('[data-task-clear="scene"]').click();
  await page.route('**/api/ai/settings',route=>route.request().method()==='PUT'?
   route.fulfill({status:500,contentType:'application/json',body:'{"error":"fixture settings failure"}'}):route.continue());
  await submitSettings();
  assert.match(await page.locator('#modelSettings .form-error').textContent(),/fixture settings failure/);
  assert.equal(await page.locator('#modelSettings [type="submit"]').isEnabled(),true);
  assert.equal(await taskField('scene','temperature').inputValue(),'');
  assert.deepEqual((await api('/api/ai/settings')).settings.task_parameters,raw);
  await page.unroute('**/api/ai/settings');
  const expected={...raw};delete expected.scene;const saved=await saveSettings();
  assert.deepEqual(saved.task_parameters,expected);
  assert.equal(await page.locator('[data-task-model="scene"]').inputValue(),model);
  await page.reload();await page.waitForFunction(()=>backendOnline===true);await settingsPage();
  assert.deepEqual((await saveSettings()).task_parameters,expected);
 });
 await test('思考开关强度互换不留下冲突，空白继承，数值边界由表单拒绝',async()=>{
  const original={temperature:0,max_tokens:750,seed:19,top_p:0.5};
  await api('/api/ai/settings','PUT',{task_parameters:{scene:original}});await reopenSettings();
  for(const [value,extra] of [['deepseek_disabled',{thinking:{type:'disabled'}}],['high',{reasoning_effort:'high'}],
   ['deepseek_enabled',{thinking:{type:'enabled'}}],['',{}]]){
   await taskField('scene','reasoning').selectOption(value);
   assert.deepEqual((await saveSettings()).task_parameters.scene,{...original,...extra});
  }
  let writes=0;const track=request=>{if(request.method()==='PUT'&&request.url()===base+'/api/ai/settings')writes++};
  page.on('request',track);
  for(const [name,value] of [['temperature','-0.1'],['temperature','2.1'],['max_tokens','0'],
   ['max_tokens','128001'],['max_tokens','1.5']]){
   await taskField('scene',name).fill(value);
   assert.equal(await page.locator('#modelSettings').evaluate(form=>form.checkValidity()),false);
   await page.locator('#modelSettings').evaluate(form=>form.requestSubmit());
   assert.equal(writes,0);await taskField('scene',name).fill('');
  }
  await taskField('scene','temperature').fill('0.375');
  assert.equal((await saveSettings()).task_parameters.scene.temperature,0.375);page.off('request',track);
  await page.locator('#modelTemperature').fill('0');assert.equal((await saveSettings()).parameters.temperature,0);
  assert.equal((await preview('polish')).request.temperature,0);
  await page.locator('#modelTemperature').fill('');assert.equal('temperature' in (await saveSettings()).parameters,false);
  assert.equal((await preview('continue')).request.temperature,0.65);
  assert.equal((await preview('polish')).request.temperature,0.35);
 });
 await test('迟到测试响应不能污染重新打开的页面，未保存密钥不缓存',async()=>{
  await fillConnection('late-probe-fixture');mode='held';release=null;
  const pending=probeDraft();for(let index=0;index<100&&!release;index++)await pause(20);assert.ok(release);
  await page.locator('[data-view="editor"]').dispatchEvent('click');release();await pending;mode='normal';await settingsPage();await expandSettings();
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal((await api('/api/ai/connections')).connections.length,2);
  const storage=await page.evaluate(()=>[...Object.values(localStorage),...Object.values(sessionStorage)].join('\n'));
  assert.equal(storage.includes(secret),false);assert.equal(serverLog.includes(secret),false);
 });
 await test('迟到用途保存不能覆盖重新读取的当前设置',async()=>{
  let resume,observed;const held=new Promise(resolve=>observed=resolve);
  await page.route('**/api/ai/settings',async route=>{
   if(route.request().method()!=='PUT')return route.continue();
   const response=await route.fetch();observed();await new Promise(resolve=>resume=resolve);
   await route.fulfill({response});
  });
  await page.locator('#modelTone').fill('旧保存响应');
  const pending=submitSettings('lateSettingsSave');await held;
  await api('/api/ai/settings','PUT',{styles:{tone:'当前最新风格',instructions:''}});
  await reopenSettings();assert.equal(await page.locator('#modelTone').inputValue(),'当前最新风格');
  resume();await pending;await page.unroute('**/api/ai/settings');
  assert.equal(await page.locator('#modelTone').inputValue(),'当前最新风格');
  assert.equal((await saveSettings()).styles.tone,'当前最新风格');
 });
 await test('编辑已保存连接必须重新测试，闲置连接可删除，正在使用的连接保留',async()=>{
  const extra=await register('editable-fixture');await reopenSettings();
  await page.locator(`[data-connection-edit="${extra.id}"]`).click();
  assert.equal(await page.locator('#connectionModel').inputValue(),'editable-fixture');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  await page.locator('#connectionModel').fill('edited-fixture');
  await page.locator('#connectionBaseUrl').fill(providerBase+'/edited');
  await page.locator('#connectionKey').fill(secret);
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal((await probeDraft()).probe.success,true);await waitProbe();
  const updated=await saveConnection();assert.equal(updated.id,extra.id);
  assert.equal(updated.model,'edited-fixture');assert.equal(updated.revision,extra.revision+1);
  assert.ok((await page.locator(`#modelDefault option[value="${extra.id}"]`).textContent()).includes('edited-fixture'));
  await page.locator(`[data-connection-delete="${extra.id}"]`).click();
  await page.locator('.modal .confirmation-check input').check();
  await page.locator('.modal [type="submit"]').click();
  await page.locator(`[data-model-connection="${extra.id}"]`).waitFor({state:'detached'});
  assert.equal(await page.locator(`#modelDefault option[value="${extra.id}"]`).count(),0);
  await page.locator(`[data-connection-delete="${connectionA.id}"]`).click();
  await page.locator('.modal .confirmation-check input').check();
  await page.locator('.modal [type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.modal .form-error')?.textContent.includes('仍用于写作'));
  assert.equal((await api('/api/ai/connections')).connections.length,2);
  await page.locator('.modal [data-close]').click();
 });
 await test('失效用途连接保留身份，只改风格不会静默改用默认模型，明确清除后才继承默认',async()=>{
  const original=(await api('/api/ai/settings','PUT',{default_connection_id:connectionA.id,
   task_connections:{polish:connectionB.id},styles:{tone:'保留原用途',instructions:'保留原写作要求'}})).settings;
  const restore=holdConnectionCredential(connectionB),before=calls.length;
  try{
   await reopenSettings();
   assert.equal(await page.locator('[data-task-model="polish"]').inputValue(),connectionB.id);
   const selected=await page.locator('[data-task-model="polish"] option:checked').evaluate(option=>
    ({value:option.value,disabled:option.disabled,text:option.textContent}));
   assert.equal(selected.value,connectionB.id);assert.equal(selected.disabled,true);
   assert.match(selected.text,/连接失效，请重新测试或重新选择/);
   assert.equal(await page.locator(`[data-task-model="scene"] option[value="${connectionB.id}"]`).count(),0);
   assert.equal(await page.locator('#modelDefault').inputValue(),connectionA.id);
   await page.locator('#modelTone').fill('不能因风格修改切换用途');await submitSettings();
   assert.match(await page.locator('#modelSettings .form-error').textContent(),/凭据有效/);
   let saved=(await api('/api/ai/settings')).settings;
   assert.equal(saved.default_connection_id,original.default_connection_id);
   assert.deepEqual(saved.task_connections,original.task_connections);assert.deepEqual(saved.styles,original.styles);
   assert.equal(await page.locator('[data-task-model="polish"]').inputValue(),connectionB.id);
   const state=await api('/api/state');
   assert.equal((await request('/api/ai/prompt-preview','POST',
    {task:'polish',chapter_id:state.chapters[0].id})).response.status,400);
   assert.equal((await preview('continue')).request.model,'model-a-fixture');
   await page.locator('[data-task-model="polish"]').selectOption('');saved=await saveSettings();
   assert.equal(saved.task_connections.polish,undefined);
   assert.equal(saved.default_connection_id,connectionA.id);
   assert.equal(saved.styles.tone,'不能因风格修改切换用途');
   assert.equal((await preview('polish')).request.model,'model-a-fixture');
   assert.equal(calls.length,before,'失效连接检查和更改用途不得调用模型');
  }finally{restore()}
 });
 await test('失效默认连接保留身份和原设置，必须明确选择有效模型才切换',async()=>{
  const original=(await api('/api/ai/settings','PUT',{default_connection_id:connectionA.id,
   task_connections:{polish:connectionB.id},styles:{tone:'保留原默认',instructions:'默认失效不得隐式清除'}})).settings;
  const restore=holdConnectionCredential(connectionA),before=calls.length;
  try{
   await reopenSettings();
   assert.equal(await page.locator('#modelDefault').inputValue(),connectionA.id);
   assert.equal(await page.locator('#modelDefault option:checked').evaluate(option=>option.disabled),true);
   assert.match(await page.locator('#modelDefault option:checked').textContent(),/连接失效/);
   assert.match(await page.locator('#modelProbeStatus').textContent(),/默认模型连接失效/);
   assert.equal(await page.locator(`[data-task-model="scene"] option[value="${connectionA.id}"]`).count(),0);
   await page.locator('#modelTone').fill('明确变更后的风格');await submitSettings();
   assert.match(await page.locator('#modelSettings .form-error').textContent(),/凭据有效/);
   let saved=(await api('/api/ai/settings')).settings;
   assert.equal(saved.default_connection_id,original.default_connection_id);
   assert.deepEqual(saved.task_connections,original.task_connections);assert.deepEqual(saved.styles,original.styles);
   assert.equal(await page.locator('#modelDefault').inputValue(),connectionA.id);
   assert.equal((await preview('polish')).request.model,'model-b-fixture');
   await page.locator('#modelDefault').selectOption(connectionB.id);saved=await saveSettings();
   assert.equal(saved.default_connection_id,connectionB.id);
   assert.deepEqual(saved.task_connections,original.task_connections);
   assert.equal(saved.styles.tone,'明确变更后的风格');
   assert.equal((await preview('continue')).request.model,'model-b-fixture');
   assert.equal(calls.length,before,'失效默认连接检查和重新分配不得调用模型');
  }finally{restore()}
 });
 await test('所有连接失效时仍可明确清除原分配，保存失败不能先丢失默认或用途',async()=>{
  const original=(await api('/api/ai/settings','PUT',{default_connection_id:connectionA.id,
   task_connections:{polish:connectionB.id}})).settings;
  const restoreA=holdConnectionCredential(connectionA),restoreB=holdConnectionCredential(connectionB),before=calls.length;
  try{
   await reopenSettings();
   assert.equal(await page.locator('#modelDefault').inputValue(),connectionA.id);
   assert.equal(await page.locator('[data-task-model="polish"]').inputValue(),connectionB.id);
   assert.equal(await page.locator('#modelDefault').isEnabled(),true);
   assert.equal(await page.locator('[data-task-model="polish"]').isEnabled(),true);
   assert.equal(await page.locator('[data-task-model="scene"]').isDisabled(),true);
   assert.equal(await page.locator('#modelDefault option:checked').evaluate(option=>option.disabled),true);
   assert.equal(await page.locator('[data-task-model="polish"] option:checked').evaluate(option=>option.disabled),true);
   await submitSettings();assert.match(await page.locator('#modelSettings .form-error').textContent(),/凭据有效/);
   let saved=(await api('/api/ai/settings')).settings;
   assert.equal(saved.default_connection_id,original.default_connection_id);
   assert.deepEqual(saved.task_connections,original.task_connections);
   await page.locator('#modelDefault').selectOption('');
   await page.locator('[data-task-model="polish"]').selectOption('');saved=await saveSettings();
   assert.equal(saved.default_connection_id,'');assert.deepEqual(saved.task_connections,{});
   assert.equal(calls.length,before,'全部失效时清除分配不得调用模型');
  }finally{restoreB();restoreA()}
 });
 await test('旧连接登记不提前切换，迁移后不反复预填旧配置，支持只分配指定用途',async()=>{
  const created=await api('/api/projects','POST',{title:'旧连接迁移合成作品',goal:50000});
  const project=created.project.id;
  await api('/api/ai/settings','PUT',{base_url:providerBase,model:'legacy-fixture',api_key:secret},project);
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${project}"]`).click();
  await page.waitForFunction(id=>requestedProject===id&&backendOnline,project);await settingsPage();
  await page.locator('#connectionRegisterLegacy').click();
  assert.equal(await page.locator('#connectionModel').inputValue(),'legacy-fixture');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal((await probeDraft()).probe.success,true);await waitProbe();
  const migrated=await saveConnection();
  let saved=(await api('/api/ai/settings','GET',undefined,project)).settings;
  assert.equal(saved.connection_workflow,false);assert.equal(saved.model,'legacy-fixture');
  assert.equal(saved.default_connection_id,'');
  await page.locator('#modelDefault').selectOption(migrated.id);await saveSettings();
  await reopenSettings();
  assert.equal(await page.locator('#connectionModel').inputValue(),'');
  assert.equal(await page.locator('#connectionBaseUrl').inputValue(),'');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal(await page.locator('#connectionEditorHeading').textContent(),'新增模型');
  await page.locator('#modelDefault').selectOption('');
  await expandSettings();
  await page.locator('[data-task-model="scene"]').selectOption(migrated.id);
  await submitSettings();
  assert.equal(await page.locator('#modelSettings .form-error').textContent(),'');
  saved=(await api('/api/ai/settings','GET',undefined,project)).settings;
  assert.equal(saved.default_connection_id,'');assert.equal(saved.task_connections.scene,migrated.id);
  assert.equal(saved.connection_workflow,true);
  assert.equal((await api('/api/ai/settings')).settings.connections.length,2);
  assert.equal((await api('/api/ai/connections','GET',undefined,project)).connections.length,1);
 });
 await test('直接打开网页文件时以中文说明正确入口，不显示原生网络错误',async()=>{
  const filePage=await browser.newPage({viewport:{width:1280,height:900}});
  try{
   await filePage.goto(pathToFileURL(path.join(root,'frontend','index.html')).href);
   await filePage.locator('[data-view="settings"]').click();
   const message=filePage.locator('#settingsHost .settings-unavailable');
   await message.waitFor();
   assert.match(await message.textContent(),/请在墨屿软件中配置模型/);
   assert.match(await message.textContent(),/打开已安装的墨屿/);
   assert.doesNotMatch(await message.textContent(),/Failed to fetch|NetworkError|TypeError/i);
   assert.equal(await filePage.locator('#connectionKey').count(),0);
  }finally{await filePage.close()}
 });
 assert.deepEqual(pageErrors,[]);report.provider_requests=calls.length;report.page_errors=pageErrors.length;
 report.source_files_sha256_after=fingerprint();
 assert.deepEqual(report.source_files_sha256_after,report.source_files_sha256_before);report.passed=true;
}
main().catch(async error=>{
 report.failure={message:error.message,stack:error.stack,server_log:serverLog.slice(-3000)};
 if(page&&!page.isClosed())try{await screenshot('model-library-failure.png')}catch{}process.exitCode=1;
}).finally(async()=>{
 if(release)release();if(browser)await browser.close();
 if(application&&application.exitCode===null){
  const stopped=new Promise(resolve=>application.once('exit',resolve));application.kill('SIGTERM');
  await Promise.race([stopped,pause(2000)]);if(application.exitCode===null)application.kill('SIGKILL');await stopped;
 }
 if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
 fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
});
