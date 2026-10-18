'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const net=require('node:net');
const crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.MOYU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-model-discovery-'));
const secret='synthetic-model-discovery-key';
const sourceFiles=['model-picker.js','model-picker.css','author-workspace.js','agent-settings.js','index.html'];
const fingerprints=()=>Object.fromEntries(sourceFiles.map(file=>[file,
 crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]));
const report={passed:false,synthetic_only:true,paid_provider_calls:0,cases:[],page_errors:[],
 artifacts:temporary,source_files_sha256_before:fingerprints()};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const providerCalls=[],inventory=[],holds=new Set();
let application,provider,browser,page,base,providerBase,serverLog='',listMode='normal',saved;
const generated=()=>providerCalls.filter(call=>call.method==='POST');
async function freePort(){
 const listener=net.createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
 const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));return port;
}
async function api(route,method='GET',body,project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(data)}`);return data;
}
async function test(name,run){
 console.log(JSON.stringify({test:name,state:'running'}));
 await run();report.cases.push({name,passed:true});console.log(JSON.stringify({test:name,state:'passed'}));
}
async function settings(){
 await page.locator('[data-view="settings"]').click();
 await page.locator('#modelConnectionForm').waitFor();await page.locator('#agentConnectionSettings').waitFor();
}
async function reopen(){
 await page.locator('[data-view="editor"]').click();await settings();
}
async function success(prefix='connection'){
 await page.locator(`#${prefix}ModelListStatus[data-state="success"]`).waitFor();
 await page.waitForFunction(prefix=>document.querySelector('#'+prefix+'FetchModels')?.disabled===false,prefix);
}
async function refresh(prefix='connection',state='success'){
 const button=page.locator('#'+prefix+'FetchModels');
 await button.waitFor();await page.waitForFunction(id=>document.getElementById(id)?.disabled===false,prefix+'FetchModels');
 await button.click();await page.locator(`#${prefix}ModelListStatus[data-state="${state}"]`).waitFor();
}
async function openList(prefix='connection'){
 const input=page.locator('#'+prefix+'Model');
 await input.press('Escape');await page.locator('#'+prefix+'ModelToggle').click();
 await page.locator('#'+prefix+'ModelList').waitFor({state:'visible'});
 return page.locator('#'+prefix+'ModelList [role="option"]');
}
async function held(endpoint,start,action){
 let observe,release,finished;
 const observed=new Promise(resolve=>observe=resolve),gate=new Promise(resolve=>release=resolve);
 const completed=new Promise(resolve=>finished=resolve);holds.add(release);
 const pattern='**'+endpoint;
 const handler=async route=>{
  observe();await gate;
  try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({models:['late-old-fixture'],endpoint:'local-fixture'})})}
  catch(error){assert.match(error.message,/aborted|closed|handled|Interception|cancel/i)}
  finally{finished()}
 };
 await page.route(pattern,handler);
 try{await start();await observed;await action();release();await completed;}
 finally{release();holds.delete(release);await page.unroute(pattern,handler)}
}
async function screenshot(name){
 const target=path.join(temporary,name);await page.screenshot({path:target,fullPage:true,animations:'disabled'});
 (report.screenshots||=[]).push(target);
}
async function main(){
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);
  request.on('end',()=>{
   const payload=raw?JSON.parse(raw):{};
   providerCalls.push({method:request.method,path:request.url,model:payload.model,
    protocol:request.url.endsWith('/responses')?'responses':'chat_completions',
    has_input:Object.hasOwn(payload,'input'),has_messages:Object.hasOwn(payload,'messages'),
    key_matches:request.headers.authorization==='Bearer '+secret,
    no_key:!request.headers.authorization});
   if(request.method==='GET'){
    response.writeHead(listMode==='failed'?503:200,{'Content-Type':'application/json'});
    response.end(JSON.stringify(listMode==='failed'?{error:{message:'synthetic list unavailable'}}:
     {data:[{id:'fixture-alpha'},{id:'fixture-beta'},{id:'fixture-gamma'},{id:'fixture-alpha'}]}));return;
   }
   response.writeHead(200,{'Content-Type':'application/json'});
   if(request.url.endsWith('/responses'))response.end(JSON.stringify({
    id:'resp_discovery_fixture',object:'response',status:'completed',model:payload.model,
    output:[{id:'msg_discovery_fixture',type:'message',role:'assistant',status:'completed',
     content:[{type:'output_text',text:'连接正常',annotations:[]}]}],
    usage:{input_tokens:12,output_tokens:5,total_tokens:17},
   }));
   else response.end(JSON.stringify({model:payload.model,
    choices:[{message:{content:'连接正常'},finish_reason:'stop'}],
    usage:{prompt_tokens:12,completion_tokens:5,total_tokens:17}}));
  });
 });
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
 providerBase=`http://127.0.0.1:${provider.address().port}/v1`;
 const port=await freePort();base=`http://127.0.0.1:${port}`;
 const environment={...process.env};for(const key of Object.keys(environment))if(key.startsWith('MOYU_'))delete environment[key];
 application=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,env:{...environment,
  MOYU_DB:path.join(temporary,'synthetic.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(port),
  MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'100',
  MOYU_SECRET_KEY:'',MOYU_BACKUP_DIR:path.join(temporary,'backups'),MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 application.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let i=0;i<200;i++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  assert.ok(application.exitCode===null&&i<199,'隔离服务未启动');await pause(40);
 }
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>report.page_errors.push(error.message));
 page.on('request',request=>{
  const url=new URL(request.url());
  if(['/api/ai/connections/models','/api/agent-chat/settings/models'].includes(url.pathname)){
   const body=request.postDataJSON();
   inventory.push({route:url.pathname,owner:request.headers()['x-moyu-project'],protocol:body.api_protocol,
    id:body.id||'',uses_saved_key:!Object.hasOwn(body,'api_key'),has_key:!!body.api_key,
    clear_key:body.clear_api_key===true,key_matches:body.api_key===secret});
  }
 });
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline===true);await settings();

 await test('地址与密钥在模型选择之前，空连接不自动发送请求',async()=>{
  assert.deepEqual(await page.locator('#modelConnectionForm input,#modelConnectionForm select').evaluateAll(elements=>
   elements.slice(0,5).map(element=>element.id)),
  ['connectionBaseUrl','connectionKey','connectionApiProtocol','connectionName','connectionModel']);
  assert.equal(await page.locator('#connectionApiProtocol').inputValue(),'chat_completions');
  await pause(700);assert.equal(inventory.length,0);assert.equal(generated().length,0);
 });
 await test('填写地址密钥后自动获取多个模型，不选择不测试不保存',async()=>{
  const before=(await api('/api/ai/settings')).settings;
  await page.locator('#connectionBaseUrl').fill(providerBase);
  await page.locator('#connectionKey').fill(secret);await page.locator('#connectionKey').blur();
  await success();const options=await openList();assert.equal(await options.count(),3);
  assert.equal(await page.locator('#connectionModel').inputValue(),'');
  assert.equal(generated().length,0);assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.deepEqual((await api('/api/ai/settings')).settings,before);
  assert.ok(inventory.at(-1).key_matches);assert.equal(inventory.at(-1).protocol,'chat_completions');
 });
 await test('模型下拉支持搜索鼠标键盘和手填，改模型及名称不重新获取',async()=>{
  const before=inventory.length;
  await page.locator('#connectionName').fill('发现模型合成连接');
  await page.locator('#connectionModel').fill('beta');
  assert.equal(await page.locator('#connectionModelList [role="option"]').count(),1);
  await page.locator('#connectionModel').press('ArrowDown');
  assert.ok(await page.locator('#connectionModel').getAttribute('aria-activedescendant'));
  await page.locator('#connectionModel').press('Enter');
  assert.equal(await page.locator('#connectionModel').inputValue(),'fixture-beta');
  assert.equal(await page.locator('#connectionModel').getAttribute('aria-expanded'),'false');
  const options=await openList();await options.filter({hasText:'fixture-alpha'}).click();
  assert.equal(await page.locator('#connectionModel').inputValue(),'fixture-alpha');
  await openList();await page.locator('#connectionModel').press('ArrowUp');
  await page.locator('#connectionModel').press('Enter');
  assert.equal(await page.locator('#connectionModel').inputValue(),'fixture-gamma');
  await page.locator('#connectionModel').fill('manual-fixture-model');await page.locator('#connectionModel').press('Escape');
  assert.equal(await page.locator('#connectionModelList').isVisible(),false);
  await pause(750);assert.equal(inventory.length,before);assert.equal(generated().length,0);
 });
 await test('Responses协议进入清单测试与凭证，兼容草稿保留且切协议撤销测试',async()=>{
  await page.locator('.settings-compatibility').evaluate(element=>element.open=true);
  await page.locator('#connectionOutputTokenParameter').selectOption('max_completion_tokens');
  await page.locator('#connectionIncludeUsage').check();
  await page.locator('#connectionApiProtocol').selectOption('responses');await page.locator('#connectionApiProtocol').blur();
  await success();assert.equal(inventory.at(-1).protocol,'responses');
  assert.equal(await page.locator('.settings-compatibility').isVisible(),false);
  await page.locator('#connectionProbe').click();
  await page.locator('#connectionProbeStatus[data-state="success"]').waitFor();
  await page.waitForFunction(()=>document.querySelector('#connectionSave')?.disabled===false);
  assert.equal(generated().at(-1).protocol,'responses');assert.equal(generated().at(-1).has_input,true);
  assert.equal(generated().at(-1).has_messages,false);
  await page.locator('#connectionApiProtocol').selectOption('chat_completions');
  assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  assert.equal(await page.locator('#connectionOutputTokenParameter').inputValue(),'max_completion_tokens');
  assert.equal(await page.locator('#connectionIncludeUsage').isChecked(),true);
  await page.locator('#connectionApiProtocol').selectOption('responses');
  await page.locator('#connectionProbe').click();await page.locator('#connectionProbeStatus[data-state="success"]').waitFor();
  await page.waitForFunction(()=>document.querySelector('#connectionSave')?.disabled===false);
  const before=generated().length;await page.locator('#connectionSave').click();
  await page.locator('#connectionProbeStatus[data-state="saved"]').waitFor();
  await page.waitForFunction(()=>document.querySelector('#connectionProbe')?.disabled===false);
  saved=(await api('/api/ai/connections')).connections[0];assert.equal(saved.api_protocol,'responses');
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  const count=inventory.length;await pause(800);
  assert.equal(inventory.length,count,'保存后清空密钥不能无休止重拉');
  assert.equal(generated().length,before,'保存不能再次生成');
 });
 await test('编辑已保存连接自动获取并沿用密钥，Responses用途输入校验保留旧值',async()=>{
  await page.locator(`[data-connection-edit="${saved.id}"]`).click();await success();
  assert.equal(inventory.at(-1).id,saved.id);assert.equal(inventory.at(-1).uses_saved_key,true);
  assert.equal(inventory.at(-1).protocol,'responses');assert.equal(providerCalls.at(-1).key_matches,true);
  assert.equal(await page.locator('#connectionModel').inputValue(),saved.model);
  await page.locator('#modelDefault').selectOption(saved.id);
  assert.equal(await page.locator('#modelMaxTokens').getAttribute('min'),'16');
  await page.locator('#modelReasoning').selectOption('deepseek_enabled');
  assert.equal(await page.locator('#modelReasoning').evaluate(element=>element.checkValidity()),false);
  assert.match(await page.locator('#modelProtocolWarning').textContent(),/DeepSeek/);
  assert.equal(await page.locator('#modelReasoning').inputValue(),'deepseek_enabled');
  await page.locator('#modelReasoning').selectOption('');
 });
 await test('主连接换地址不自动发送原密钥，清单手动免密且测试必须补新密钥',async()=>{
  const before=inventory.length,probes=generated().length;
  await page.locator('#connectionBaseUrl').fill(providerBase+'/other-service');
  await page.locator('#connectionBaseUrl').blur();await pause(750);
  assert.equal(inventory.length,before);assert.equal(await page.locator('#connectionModelList [role="option"]').count(),0);
  assert.match(await page.locator('#connectionModelListStatus').textContent(),/重新填写/);
  await page.locator('#connectionProbe').click();await page.locator('#connectionProbeStatus[data-state="failed"]').waitFor();
  assert.equal(generated().length,probes);assert.equal(await page.locator('#connectionSave').isDisabled(),true);
  await refresh();assert.equal(inventory.at(-1).has_key,false);assert.equal(inventory.at(-1).uses_saved_key,false);
  assert.equal(providerCalls.at(-1).no_key,true);
  await page.locator('#connectionBaseUrl').fill(providerBase+'/responses/');await page.locator('#connectionBaseUrl').blur();
  await success();assert.equal(inventory.at(-1).uses_saved_key,true);assert.equal(providerCalls.at(-1).key_matches,true);
  await page.locator('#connectionBaseUrl').fill(providerBase);await page.locator('#connectionBaseUrl').blur();await success();
 });
 await test('Responses用途按有效继承长度校验，章后回顾别名不静默改草稿',async()=>{
  await page.locator('#modelDefault').selectOption('');
  await page.locator('#modelMaxTokens').fill('1');
  await page.locator('[data-task-model="continue"]').selectOption(saved.id);
  const continuation=page.locator('[data-task-row="continue"] [data-task-parameter="max_tokens"]');
  assert.equal(await continuation.inputValue(),'');
  assert.equal(await continuation.evaluate(element=>element.checkValidity()),false);
  assert.match(await page.locator('#modelProtocolWarning').textContent(),/沿用输出上限/);
  await continuation.fill('16');assert.equal(await continuation.evaluate(element=>element.checkValidity()),true);
  await page.locator('[data-task-model="summary"]').selectOption(saved.id);
  const summary=page.locator('[data-task-row="summary"] [data-task-parameter="max_tokens"]');
  const analysis=page.locator('[data-task-row="chapter_analysis"] [data-task-parameter="max_tokens"]');
  await summary.fill('1');assert.equal(await analysis.inputValue(),'');
  assert.equal(await analysis.evaluate(element=>element.checkValidity()),false);
  await summary.fill('16');assert.equal(await analysis.evaluate(element=>element.checkValidity()),true);
  await page.locator('[data-task-row="chapter_analysis"] [data-task-parameter="temperature"]').fill('0.2');
  assert.equal(await analysis.evaluate(element=>element.checkValidity()),false,'单设后空长度沿用全局1');
  await page.locator('[data-task-row="chapter_analysis"] [data-task-parameter="temperature"]').fill('');
  await page.locator('[data-task-model="continue"]').selectOption('');
  await page.locator('[data-task-model="summary"]').selectOption('');
  await continuation.fill('');await summary.fill('');await page.locator('#modelMaxTokens').fill('3000');
 });
 await test('同一连接获取中改模型名称不取消清单，返回结果不覆盖手填模型',async()=>{
  await held('/api/ai/connections/models',()=>page.locator('#connectionFetchModels').click(),async()=>{
   await page.locator('#connectionModel').fill('typing-during-list');
   await page.locator('#connectionName').fill('仍是同一连接');
  });
  await success();assert.equal(await page.locator('#connectionModel').inputValue(),'typing-during-list');
  assert.equal(await page.locator('#connectionModelList [role="option"]').textContent(),'late-old-fixture');
 });
 await test('改地址立即取消并清理旧清单，慢返回不能污染新连接',async()=>{
  const before=inventory.length;
  await held('/api/ai/connections/models',()=>page.locator('#connectionFetchModels').click(),async()=>{
   await page.locator('#connectionBaseUrl').fill(providerBase+'/changed');
   assert.equal(await page.locator('#connectionModelList [role="option"]').count(),0);
   assert.equal(await page.locator('#connectionFetchModels').isEnabled(),true);
  });
  await page.locator('#connectionBaseUrl').blur();await pause(750);
  assert.equal(inventory.length,before+1,'换服务后不自动发送已保存密钥');
  assert.equal(await page.locator('#connectionModelList [data-model-value="late-old-fixture"]').count(),0);
  assert.equal(await page.locator('#connectionModel').inputValue(),'typing-during-list');
 });
 await test('连续修改密钥只在停止输入后查询一次，重复失焦不重拉',async()=>{
  const before=inventory.length;
  await page.locator('#connectionKey').fill('synthetic-draft-a');
  await page.locator('#connectionKey').fill('synthetic-draft-b');
  await page.locator('#connectionKey').fill(secret);
  await success();assert.equal(inventory.length,before+1);
  await page.locator('#connectionKey').blur();await pause(750);assert.equal(inventory.length,before+1);
 });
 await test('离开设置会取消旧请求且不恢复旧模型草稿或密钥',async()=>{
  await held('/api/ai/connections/models',()=>page.locator('#connectionFetchModels').click(),()=>reopen());
  assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal(await page.locator('#connectionModel').inputValue(),'');
  assert.equal(await page.locator('#connectionModelList [role="option"]').count(),0);
 });
 await test('列表失败可以重试且模型仍可手填，本机免密连接自动获取',async()=>{
  await page.locator('#connectionBaseUrl').fill(providerBase);await page.locator('#connectionBaseUrl').blur();await success();
  assert.equal(inventory.at(-1).has_key,false);assert.equal(providerCalls.at(-1).no_key,true);
  listMode='failed';await refresh('connection','failed');
  await page.locator('#connectionModel').fill('manual-after-failure');
  assert.equal(await page.locator('#connectionModel').inputValue(),'manual-after-failure');
  assert.equal(await page.locator('#connectionProbe').isEnabled(),true);
  listMode='normal';await refresh();assert.equal(await page.locator('#connectionModel').inputValue(),'manual-after-failure');
 });
 await test('总管独立连接也自动发现多个模型并传递协议，不生成或保存',async()=>{
  const before=generated().length;
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  await page.locator('#agentBaseUrl').fill(providerBase);await page.locator('#agentApiKey').fill(secret);
  await page.locator('#agentApiProtocol').selectOption('responses');await page.locator('#agentApiProtocol').blur();
  await success('agent');
  assert.equal(inventory.at(-1).route,'/api/agent-chat/settings/models');assert.equal(inventory.at(-1).protocol,'responses');
  assert.equal(await page.locator('#agentModel').inputValue(),'');assert.equal(generated().length,before);
  assert.equal((await api('/api/agent-chat/settings')).settings.mode,'inherit');
  const options=await openList('agent');assert.equal(await options.count(),3);
  await page.locator('#agentModel').fill('gamma');await page.locator('#agentModel').press('ArrowDown');
  await page.locator('#agentModel').press('Enter');assert.equal(await page.locator('#agentModel').inputValue(),'fixture-gamma');
  assert.equal(await page.locator('.agent-settings-compatibility').isVisible(),false);
  assert.equal(await page.locator('#agentMaxTokens').getAttribute('min'),'16');
 });
 await test('总管协议保存测试沿用正确接入，保存清密钥不重复查询',async()=>{
  await page.locator('#agentApiProtocol').selectOption('chat_completions');
  await page.locator('#agentThinking').selectOption('enabled');
  await page.locator('#agentApiProtocol').selectOption('responses');
  assert.equal(await page.locator('#agentThinking').inputValue(),'enabled');
  assert.equal(await page.locator('#agentThinking').evaluate(element=>element.checkValidity()),false);
  assert.match(await page.locator('#agentProtocolWarning').textContent(),/DeepSeek/);
  await page.locator('#agentThinking').selectOption('default');
  await page.locator('#agentConnectionSettings [type="submit"]').click();
  await page.locator('#agentProbeStatus').filter({hasText:'总管设置已保存'}).waitFor();
  const stored=(await api('/api/agent-chat/settings')).settings;
  assert.equal(stored.independent_settings.api_protocol,'responses');assert.equal(stored.independent_settings.api_key_configured,true);
  assert.equal(await page.locator('#agentApiKey').inputValue(),'');
  const count=inventory.length;await pause(750);assert.equal(inventory.length,count);
  const before=generated().length;await page.locator('#agentConnectionProbe').click();
  await page.locator('#agentProbeStatus').filter({hasText:'实测成功'}).waitFor();
  assert.equal(generated().length,before+1);assert.equal(generated().at(-1).protocol,'responses');
  assert.equal(generated().at(-1).has_input,true);
 });
 await test('总管换地址不自动沿用原密钥，保存与测试阻止跨地址凭据外发',async()=>{
  const before=inventory.length,probes=generated().length;
  await page.locator('#agentBaseUrl').fill(providerBase+'/other-agent-service');
  await page.locator('#agentBaseUrl').blur();await pause(750);
  assert.equal(inventory.length,before);assert.match(await page.locator('#agentModelListStatus').textContent(),/重新填写/);
  await page.locator('#agentConnectionSettings [type="submit"]').click();
  assert.match(await page.locator('#agentConnectionSettings .form-error').textContent(),/重新填写/);
  await page.locator('#agentConnectionProbe').click();
  assert.equal(generated().length,probes);
  assert.equal((await api('/api/agent-chat/settings')).settings.independent_settings.base_url,providerBase);
  await refresh('agent');assert.equal(inventory.at(-1).uses_saved_key,false);assert.equal(providerCalls.at(-1).no_key,true);
  await page.locator('#agentBaseUrl').fill(providerBase+'/chat/completions');
  await page.locator('#agentBaseUrl').blur();await success('agent');
  assert.equal(inventory.at(-1).uses_saved_key,true);assert.equal(providerCalls.at(-1).key_matches,true);
  await page.locator('#agentBaseUrl').fill(providerBase);await page.locator('#agentBaseUrl').blur();await success('agent');
 });
 await test('填写新总管密钥再勾清除会取消旧清单并提示二选一',async()=>{
  await held('/api/agent-chat/settings/models',()=>page.locator('#agentFetchModels').click(),async()=>{
   await page.locator('#agentApiKey').fill(secret);await page.locator('#agentClearApiKey').check();
  });
  const count=inventory.length;await pause(750);assert.equal(inventory.length,count);
  assert.equal(await page.locator('#agentModelList [role="option"]').count(),0);
  assert.match(await page.locator('#agentModelListStatus').textContent(),/只能选择一项/);
  await page.locator('#agentClearApiKey').uncheck();await page.locator('#agentApiKey').fill('');
  await page.locator('#agentApiKey').blur();await success('agent');
 });
 await test('总管重开自动沿用已存密钥，清除密钥草稿不提前写入',async()=>{
  await reopen();await success('agent');assert.equal(inventory.at(-1).uses_saved_key,true);
  assert.equal(providerCalls.at(-1).key_matches,true);assert.equal(inventory.at(-1).protocol,'responses');
  const count=inventory.length;await page.locator('#agentModel').fill('agent-manual-draft');
  await pause(700);assert.equal(inventory.length,count);
  await page.locator('#agentClearApiKey').check();await page.locator('#agentClearApiKey').blur();await success('agent');
  assert.equal(inventory.at(-1).clear_key,true);assert.equal(providerCalls.at(-1).no_key,true);
  assert.equal((await api('/api/agent-chat/settings')).settings.independent_settings.api_key_configured,true);
 });
 await test('总管切回沿用主连接会取消慢清单，保留独立草稿且不生成',async()=>{
  const before=generated().length;
  await held('/api/agent-chat/settings/models',()=>page.locator('#agentFetchModels').click(),async()=>{
   await page.locator('[name="agentConnectionMode"][value="inherit"]').check();
  });
  assert.equal(await page.locator('.agent-independent-fields').isVisible(),false);
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();await success('agent');
  assert.equal(await page.locator('#agentModel').inputValue(),'agent-manual-draft');
  assert.equal(await page.locator('#agentModelList [data-model-value="late-old-fixture"]').count(),0);
  assert.equal(generated().length,before);
 });
 await test('跨作品的慢清单不带入新作品，桌面下拉不越界且秘密不进入缓存',async()=>{
  await page.locator('#connectionBaseUrl').fill(providerBase);await page.locator('#connectionBaseUrl').blur();await success();
  const created=await api('/api/projects','POST',{title:'模型发现隔离合成作品',goal:50000});
  await held('/api/ai/connections/models',()=>page.locator('#connectionFetchModels').click(),async()=>{
   await page.locator('[data-view="library"]').click();
   await page.locator(`[data-project-open="${created.project.id}"]`).click();
   await page.waitForFunction(id=>requestedProject===id&&backendOnline===true,created.project.id);await settings();
  });
  assert.equal(await page.locator('#connectionModel').inputValue(),'');assert.equal(await page.locator('#connectionKey').inputValue(),'');
  assert.equal(await page.locator('#connectionModelList [role="option"]').count(),0);
  await page.locator('#connectionBaseUrl').fill(providerBase);await page.locator('#connectionBaseUrl').blur();await success();
  for(const width of [1100,1280,1440]){
   await page.setViewportSize({width,height:900});await openList();
   const bounds=await page.locator('#connectionModelList').boundingBox();
   assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await screenshot(`model-discovery-${width}.png`);await page.locator('#connectionModel').press('Escape');
  }
  const storage=await page.evaluate(()=>[...Object.values(localStorage),...Object.values(sessionStorage)].join('\n'));
  assert.equal(storage.includes(secret),false);assert.equal(serverLog.includes(secret),false);
  assert.equal(JSON.stringify(await api('/api/ai/settings')).includes(secret),false);
 });
 assert.deepEqual(report.page_errors,[]);report.inventory_requests=inventory;
 report.local_inventory_calls=providerCalls.filter(call=>call.method==='GET').length;
 report.local_generation_calls=generated();report.source_files_sha256_after=fingerprints();
 report.source_unchanged=JSON.stringify(report.source_files_sha256_before)===JSON.stringify(report.source_files_sha256_after);
 assert.equal(report.source_unchanged,true);report.passed=true;
}
main().catch(async error=>{
 report.failure={message:error.message,stack:error.stack,server_log:serverLog.slice(-3000)};
 if(page&&!page.isClosed())try{await screenshot('model-discovery-failure.png')}catch{}
 process.exitCode=1;
}).finally(async()=>{
 for(const release of holds)release();
 if(browser)await browser.close();
 if(application&&application.exitCode===null){
  const stopped=new Promise(resolve=>application.once('exit',resolve));application.kill('SIGTERM');
  await Promise.race([stopped,pause(2000)]);if(application.exitCode===null)application.kill('SIGKILL');await stopped;
 }
 if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
 const target=process.env.MOYU_MODEL_DISCOVERY_REPORT||path.join(temporary,'results.json');
 fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
});
