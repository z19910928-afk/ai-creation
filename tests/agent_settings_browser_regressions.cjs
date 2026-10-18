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
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-agent-settings-browser-'));
const secret='synthetic-agent-browser-private-key',draftSecret='synthetic-unsaved-agent-browser-private-key';
const report={passed:false,cases:[],artifacts:temporary,paid_provider_calls:0,page_errors:[]};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,provider,browser,base,providerBase,serverLog='',page;
const calls=[];
const modelCalls=()=>calls.filter(call=>call.method==='POST').length;
async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',payload){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':'default-project'},
  body:payload===undefined?undefined:JSON.stringify(payload)});
 const data=await response.json();assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(data)}`);return data;
}
async function test(name,run){
 await run();report.cases.push({name,passed:true});console.log(JSON.stringify({name,passed:true}));
}
async function registerConnection(model){
 await page.locator('#connectionAdd').click();await page.locator('#connectionDialog').waitFor();
 await page.locator('#connectionName').fill(model);await page.locator('#connectionBaseUrl').fill(providerBase);
 await page.locator('#connectionModel').fill(model);await page.locator('#connectionModel').press('Escape');
 const before=modelCalls();await page.locator('#connectionProbe').click();
 await page.locator('#connectionProbeStatus[data-state="success"]').waitFor();
 await page.waitForFunction(()=>document.querySelector('#connectionSave')?.disabled===false);
 assert.equal(modelCalls(),before+1);
 await page.locator('#connectionSave').click();await page.locator('#connectionDialog').waitFor({state:'hidden'});
 assert.equal(modelCalls(),before+1);
 const item=(await api('/api/ai/connections')).connections.find(row=>row.model===model);
 assert.ok(item?.verified);return item;
}
async function assignMain(connection){
 const before=modelCalls();await page.locator('#modelDefault').selectOption(connection.id);
 await page.locator('#modelSettings [type="submit"]').click();
 await page.waitForFunction(id=>authorSettings?.default_connection_id===id&&
  document.querySelector('#modelSettings [type="submit"]')?.disabled===false,connection.id);
 assert.equal(modelCalls(),before);
}
async function advanced(){
 await page.locator('.agent-settings-compatibility').evaluate(element=>element.open=true);
}
async function probeSuccess(){
 await page.locator('#agentConnectionProbe').click();
 await page.locator('#agentProbeStatus').filter({hasText:'保存后生效'}).waitFor();
 await page.waitForFunction(()=>document.querySelector('#agentConnectionSave')?.disabled===false);
}
async function save(){
 await page.locator('#agentConnectionSave').click();
 await page.locator('#agentProbeStatus').filter({hasText:'总管设置已保存'}).waitFor();
}
async function main(){
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',()=>{
   const input=raw?JSON.parse(raw):{};
   calls.push({method:request.method,path:request.url,model:input.model,has_input:Object.hasOwn(input,'input'),
    user_agent:request.headers['user-agent'],key_matches:request.headers.authorization==='Bearer '+secret,
    draft_key_matches:request.headers.authorization==='Bearer '+draftSecret});
   response.writeHead(200,{'Content-Type':'application/json'});
   if(request.method==='GET'){
    response.end(JSON.stringify({data:[{id:'main-browser-fixture'},{id:'independent-browser-fixture'},{id:'secondary-browser-fixture'}]}));return;
   }
   const text=input.model==='bad-browser-fixture'?'连接不正常':'连接正常。';
   response.end(JSON.stringify(request.url.endsWith('/responses')?{
    id:'resp_browser_fixture',object:'response',status:'completed',model:input.model,
    output:[{id:'msg_browser_fixture',type:'message',role:'assistant',status:'completed',
     content:[{type:'output_text',text,annotations:[]}]}],usage:{input_tokens:10,output_tokens:4,total_tokens:14},
   }:{model:input.model,choices:[{message:{content:text},finish_reason:'stop'}],
    usage:{prompt_tokens:10,completion_tokens:4,total_tokens:14}}));
  });
 });
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
 providerBase=`http://127.0.0.1:${provider.address().port}/v1`;
 const port=await freePort();base=`http://127.0.0.1:${port}`;
 const environment={...process.env};for(const key of Object.keys(environment))
  if(/^(MOYU_|OPENAI_|ANTHROPIC_|GOOGLE_API_|GEMINI_|LANGCHAIN_|LANGSMITH_)/i.test(key))delete environment[key];
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['-B','server.py'],{cwd:root,env:{...environment,
  MOYU_DB:path.join(temporary,'synthetic.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(port),
  MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'100',MOYU_SECRET_KEY:'',
  MOYU_BACKUP_DIR:path.join(temporary,'backups'),MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let attempt=0;attempt<200;attempt++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  assert.ok(app.exitCode===null&&attempt<199,'isolated server unavailable');await pause(40);
 }
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>report.page_errors.push(error.message));
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="settings"]').click();
 await page.locator('#agentConnectionSettings').waitFor();await page.locator('#modelSettings').waitFor();
 let primary,secondary;
 await test('默认总管沿用已验证默认模型，主模型在按需弹窗中测试并保存',async()=>{
  primary=await registerConnection('main-browser-fixture');await assignMain(primary);
  await page.locator('[data-agent-inherit-summary]').filter({hasText:'main-browser-fixture'}).waitFor();
  assert.equal(await page.locator('[name="agentConnectionMode"][value="inherit"]').isChecked(),true);
  assert.equal(await page.locator('.agent-independent-fields').isVisible(),false);
  assert.equal(await page.locator('#agentConnectionProbe').isVisible(),false);
  assert.equal((await api('/api/agent-chat/settings')).settings.model,primary.model);
 });
 await test('总管直接选择模型库的其他已验证模型，不重填地址密钥或重复调用',async()=>{
  secondary=await registerConnection('secondary-browser-fixture');
  await page.waitForFunction(id=>!!document.querySelector(`#agentLibraryConnection option[value="${id}"]`),secondary.id);
  const before=modelCalls();await page.locator('[name="agentConnectionMode"][value="library"]').check();
  await page.locator('#agentLibraryConnection').selectOption(secondary.id);await save();
  const settings=(await api('/api/agent-chat/settings')).settings;
  assert.equal(settings.mode,'library');assert.equal(settings.connection_id,secondary.id);assert.equal(settings.configured,true);
  assert.equal(await page.locator('.agent-independent-fields').isVisible(),false);assert.equal(modelCalls(),before);
 });
 await test('独立连接必须先测试，选择服务商与自动清单不会激活草稿',async()=>{
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  assert.equal(await page.locator('#agentConnectionSave').isDisabled(),true);
  await page.locator('#agentProvider').selectOption('deepseek');
  assert.equal(await page.locator('#agentBaseUrl').inputValue(),'https://api.deepseek.com');
  await page.locator('#agentBaseUrl').fill(providerBase);await page.locator('#agentApiKey').fill(secret);
  await page.locator('#agentApiKey').blur();await page.locator('#agentModelListStatus[data-state="success"]').waitFor();
  await page.locator('#agentModel').fill('independent');await page.locator('#agentModel').press('ArrowDown');
  await page.locator('#agentModel').press('Enter');
  assert.equal(await page.locator('#agentModel').inputValue(),'independent-browser-fixture');
  assert.equal(await page.locator('#agentConnectionSave').isDisabled(),true);
  assert.equal((await api('/api/agent-chat/settings')).settings.mode,'library');
 });
 await test('草稿测试成功后原连接仍生效，保存凭证不重复调用且清除密钥',async()=>{
  const before=modelCalls();await probeSuccess();assert.equal(modelCalls(),before+1);
  assert.equal((await api('/api/agent-chat/settings')).settings.connection_id,secondary.id);
  await save();assert.equal(modelCalls(),before+1);
  assert.equal(await page.locator('#agentApiKey').inputValue(),'');
  const settings=(await api('/api/agent-chat/settings')).settings;
  assert.equal(settings.mode,'independent');assert.equal(settings.configured,true);
  assert.equal(settings.last_probe.success,true);assert.equal(JSON.stringify(settings).includes(secret),false);
  assert.equal(JSON.stringify(settings).includes('credential_ref'),false);
  assert.equal((await api('/api/ai/settings')).settings.model,primary.model);
 });
 await test('失败草稿保留原可用连接与密钥且不可保存',async()=>{
  await page.locator('#agentModel').fill('bad-browser-fixture');await page.locator('#agentModel').press('Escape');
  await page.locator('#agentConnectionProbe').click();
  await page.locator('#agentProbeStatus').filter({hasText:'连接未通过'}).waitFor();
  assert.equal(await page.locator('#agentConnectionSave').isDisabled(),true);
  const settings=(await api('/api/agent-chat/settings')).settings;
  assert.equal(settings.model,'independent-browser-fixture');assert.equal(settings.configured,true);
 });
 await test('测试成功后修改参数会撤销保存许可',async()=>{
  await page.locator('#agentModel').fill('independent-browser-fixture');await page.locator('#agentModel').press('Escape');
  await probeSuccess();await advanced();await page.locator('#agentTemperature').fill('0');
  assert.equal(await page.locator('#agentConnectionSave').isDisabled(),true);
 });
 await test('Responses保留路径与请求标识兼容字段，并传给模型清单和真实测试',async()=>{
  await page.locator('#agentApiProtocol').selectOption('responses');
  await advanced();assert.equal(await page.locator('.agent-chat-compatibility').isVisible(),false);
  assert.equal(await page.locator('#agentCustomRequestPath').isVisible(),true);
  await page.locator('#agentCustomRequestPath').fill('/compatible/responses');
  await page.locator('#agentModelsPath').fill('/compatible/models');
  await page.locator('#agentUserAgent').fill('MoyuBrowserFixture/2');await page.locator('#agentUserAgent').blur();
  await page.locator('#agentModelListStatus[data-state="success"]').waitFor();
  await page.waitForFunction(()=>document.querySelector('#agentFetchModels')?.disabled===false);
  assert.equal(await page.locator('#agentMaxTokens').getAttribute('min'),'16');
  assert.match(await page.locator('#agentEndpointPreview').textContent(),/\/compatible\/responses/);
  await probeSuccess();
  const call=calls.at(-1);assert.equal(call.path,'/compatible/responses');assert.equal(call.has_input,true);
  assert.equal(call.user_agent,'MoyuBrowserFixture/2');assert.equal(call.key_matches,true);
  const listing=calls.filter(item=>item.method==='GET').at(-1);
  assert.equal(listing.path,'/compatible/models');assert.equal(listing.user_agent,'MoyuBrowserFixture/2');
  await save();
 });
 await test('折叠的高级字段无效时自动展开，中文请求标识不会发往提供方',async()=>{
  await advanced();assert.equal(await page.locator('#agentUserAgent').getAttribute('maxlength'),'256');
  await page.locator('#agentUserAgent').fill('不支持的中文标识');
  await page.locator('.agent-settings-compatibility').evaluate(element=>element.open=false);
  const before=modelCalls();await page.locator('#agentConnectionProbe').click();
  assert.equal(await page.locator('.agent-settings-compatibility').evaluate(element=>element.open),true);
  assert.match(await page.locator('#agentUserAgent').evaluate(element=>element.validationMessage),/256/);
  assert.equal(modelCalls(),before);await page.locator('#agentUserAgent').fill('MoyuBrowserFixture/2');
 });
 await test('切换服务商清除旧草稿密钥与兼容设置，填写新密钥前不会向新服务发送请求',async()=>{
  await page.locator('#agentApiKey').fill(secret);await page.locator('#agentClearApiKey').check();
  await page.locator('#agentProvider').selectOption('openai_compatible');
  for(const id of ['agentApiKey','agentCustomRequestPath','agentModelsPath','agentUserAgent','agentModel','agentBaseUrl'])
   assert.equal(await page.locator('#'+id).inputValue(),'');
  assert.equal(await page.locator('#agentClearApiKey').isChecked(),false);
  assert.equal(await page.locator('#agentApiProtocol').inputValue(),'chat_completions');
  assert.equal(await page.locator('#agentConnectionSave').isDisabled(),true);
  await page.locator('#agentBaseUrl').fill(providerBase+'/provider-swap');
  await page.locator('#agentModel').fill('independent-browser-fixture');await page.locator('#agentBaseUrl').blur();
  const before=calls.length;await pause(750);assert.equal(calls.length,before);
  await page.locator('#agentConnectionProbe').click();
  await page.locator('#agentProbeStatus').filter({hasText:'重新填写'}).waitFor();
  assert.equal(calls.length,before);
  await page.locator('#agentApiKey').fill(draftSecret);await page.locator('#agentApiKey').blur();
  await page.locator('#agentModelListStatus[data-state="success"]').waitFor();
  await probeSuccess();
  const changed=calls.filter(item=>item.path.startsWith('/v1/provider-swap/'));
  assert.equal(changed.length,2);
  assert.equal(changed.every(item=>item.draft_key_matches&&!item.key_matches),true);
  assert.equal((await api('/api/agent-chat/settings')).settings.provider,'deepseek');
  await page.reload();await page.waitForFunction(()=>backendOnline);
  await page.locator('[data-view="settings"]').click();await page.locator('#agentConnectionSettings').waitFor();
 });
 await test('跨服务编辑不会自动沿用已保存密钥，失败不影响原连接',async()=>{
  const before=calls.length;await page.locator('#agentBaseUrl').fill(providerBase+'/changed');
  await page.locator('#agentBaseUrl').blur();await pause(750);
  assert.equal(calls.length,before);
  assert.match(await page.locator('#agentModelListStatus').textContent(),/重新填写/);
  await page.locator('#agentConnectionProbe').click();
  await page.locator('#agentProbeStatus').filter({hasText:'重新填写'}).waitFor();
  assert.equal((await api('/api/agent-chat/settings')).settings.base_url,providerBase);
 });
 await test('主模型更新只刷新总管摘要，保留尚未保存的独立草稿',async()=>{
  await page.locator('#agentModel').fill('unsaved-independent-draft');await page.locator('#agentApiKey').fill(draftSecret);
  await page.evaluate(()=>globalThis.__agentForm=document.querySelector('#agentConnectionSettings'));
  const item=await registerConnection('main-final-browser-fixture');await assignMain(item);
  await page.waitForFunction(()=>document.querySelector('[data-agent-connection-state]')?.textContent==='可用');
  assert.equal(await page.evaluate(()=>globalThis.__agentForm===document.querySelector('#agentConnectionSettings')),true);
  assert.equal(await page.locator('#agentModel').inputValue(),'unsaved-independent-draft');
  assert.equal(await page.locator('#agentApiKey').inputValue(),draftSecret);
 });
 await test('切换默认模型保留之前验证过的独立配置，未保存密钥不进浏览器存储',async()=>{
  const before=modelCalls();await page.locator('[name="agentConnectionMode"][value="inherit"]').check();await save();
  assert.equal((await api('/api/agent-chat/settings')).settings.model,'main-final-browser-fixture');
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  assert.equal(await page.locator('#agentModel').inputValue(),'independent-browser-fixture');
  assert.equal(await page.locator('#agentApiProtocol').inputValue(),'responses');
  const storage=await page.evaluate(()=>[...Object.values(localStorage),...Object.values(sessionStorage)].join('\n'));
  for(const key of [secret,draftSecret]){assert.equal(storage.includes(key),false);assert.equal(serverLog.includes(key),false)}
  assert.equal(modelCalls(),before);
 });
 await test('常见桌面宽度下总管设置不横向溢出',async()=>{
  for(const width of [1100,1280,1440]){
   await page.setViewportSize({width,height:1000});
   await page.locator('#agentConnectionSettings').scrollIntoViewIfNeeded();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
   const filename=`agent-settings-${width}.png`;
   await page.screenshot({path:path.join(temporary,filename)});(report.screenshots||=[]).push(filename);
  }
 });
 assert.deepEqual(report.page_errors,[]);report.local_mock_model_requests=modelCalls();report.passed=true;
}
main().catch(error=>{report.failure={message:String(error.stack).replaceAll(secret,'<synthetic-key>').replaceAll(draftSecret,'<synthetic-key>'),server_log:serverLog.slice(-3000)};process.exitCode=1})
 .finally(async()=>{
  if(browser)await browser.close();
  if(app&&app.exitCode===null){
   const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill('SIGTERM');
   await Promise.race([stopped,pause(2000)]);if(app.exitCode===null)app.kill('SIGKILL');await stopped;
  }
  if(provider){provider.closeAllConnections();await new Promise(resolve=>provider.close(resolve))}
  fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
 });
