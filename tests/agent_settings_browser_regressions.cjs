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
const secret='synthetic-agent-browser-private-key';
const draftSecret='synthetic-unsaved-agent-browser-private-key';
const report={passed:false,cases:[],artifacts:temporary,paid_provider_calls:0,page_errors:[]};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,provider,browser,base,providerBase,serverLog='',modelCalls=0;

async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',payload){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':'default-project'},
  body:payload===undefined?undefined:JSON.stringify(payload)});
 const data=await response.json();
 assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(data)}`);return data;
}
async function test(name,run){
 await run();report.cases.push({name,passed:true});console.log(JSON.stringify({name,passed:true}));
}
async function registerConnection(page,model){
 const before=modelCalls;
 await page.locator('#connectionReset').click();
 await page.locator('#connectionName').fill(model);
 await page.locator('#connectionBaseUrl').fill(providerBase);
 await page.locator('#connectionModel').fill(model);
 assert.equal(await page.locator('#connectionSave').isDisabled(),true);
 await page.locator('#connectionProbe').click();
 await page.locator('#connectionProbeStatus[data-state="success"]').waitFor();
 await page.waitForFunction(()=>!document.querySelector('#connectionSave').disabled);
 assert.equal(modelCalls,before+1,'登记连接只进行一次真实本机测试');
 await page.locator('#connectionSave').click();
 await page.locator('#connectionProbeStatus[data-state="saved"]').waitFor();
 await page.waitForFunction(()=>!document.querySelector('#connectionProbe').disabled);
 assert.equal(modelCalls,before+1,'保存已测试连接不能重复调用模型');
 const connection=(await api('/api/ai/connections')).connections.find(item=>item.name===model);
 assert.ok(connection?.verified);
 return connection;
}
async function assignMain(page,connection){
 const before=modelCalls;
 await page.locator('#modelDefault').selectOption(connection.id);
 await page.locator('#modelSettings button[type="submit"]').click();
 await page.waitForFunction(id=>authorSettings?.default_connection_id===id&&
  document.querySelector('#modelSettings button[type="submit"]')?.disabled===false,connection.id);
 assert.equal((await api('/api/ai/settings')).settings.model,connection.model);
 assert.equal(modelCalls,before,'保存用途分配不能发送模型请求');
}
async function main(){
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',()=>{
   const input=JSON.parse(raw);modelCalls++;
   response.writeHead(200,{'Content-Type':'application/json'});
   response.end(JSON.stringify({model:input.model,
    choices:[{message:{content:'连接正常'},finish_reason:'stop'}],
    usage:{prompt_tokens:10,completion_tokens:4,total_tokens:14}}));
  });
 });
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
 providerBase=`http://127.0.0.1:${provider.address().port}/v1`;
 const port=await freePort();base=`http://127.0.0.1:${port}`;
 const environment={...process.env};for(const key of Object.keys(environment))if(key.startsWith('MOYU_'))delete environment[key];
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,env:{...environment,
  MOYU_DB:path.join(temporary,'synthetic.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(port),
  MOYU_AI_BASE_URL:providerBase,MOYU_AI_MODEL:'main-browser-fixture',MOYU_AI_API_KEY:'',
  MOYU_AI_DAILY_QUOTA:'100',MOYU_SECRET_KEY:'',MOYU_BACKUP_DIR:path.join(temporary,'backups'),
  MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let attempt=0;attempt<200;attempt++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  if(app.exitCode!==null||attempt===199)throw new Error('isolated server unavailable');
  await pause(30);
 }
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>report.page_errors.push(error.message));
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline);
 await page.locator('[data-view="settings"]').click();
 await page.locator('#agentConnectionSettings').waitFor();
 await page.locator('#settingsHost .settings-form').waitFor();

 await test('总管默认继承主模型，真实探测后主设置变化立即撤销旧实测状态',async()=>{
  const initial=await registerConnection(page,'main-browser-fixture');
  await assignMain(page,initial);
  assert.equal(await page.locator('[name="agentConnectionMode"][value="inherit"]').isChecked(),true);
  const before=modelCalls;
  await page.locator('#agentConnectionProbe').click();
  await page.locator('#agentProbeStatus').filter({hasText:'实测成功'}).waitFor();
  assert.equal(modelCalls,before+1);
  const replacement=await registerConnection(page,'main-replacement-browser-fixture');
  await assignMain(page,replacement);
  await page.locator('[data-agent-inherit-summary]').filter({hasText:'main-replacement-browser-fixture'}).waitFor();
  assert.match(await page.locator('#agentProbeStatus').innerText(),/尚未验证/);
  assert.equal(modelCalls,before+2);
 });
 await test('保存独立模型不发送请求，密钥只在本机保存且公共设置不泄露凭据',async()=>{
  const before=modelCalls;
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  await page.locator('#agentBaseUrl').fill(providerBase);
  await page.locator('#agentModel').fill('independent-browser-fixture');
  await page.locator('#agentApiKey').fill(secret);
  await page.locator('#agentTemperature').fill('0');
  await page.locator('#agentConnectionSettings button[type="submit"]').click();
  await page.locator('#agentProbeStatus').filter({hasText:'总管设置已保存'}).waitFor();
  assert.equal(await page.locator('#agentApiKey').inputValue(),'');
  const settings=(await api('/api/agent-chat/settings')).settings;
  assert.equal(settings.mode,'independent');assert.equal(settings.independent_settings.api_key_configured,true);
  assert.equal(JSON.stringify(settings).includes(secret),false);
  assert.equal(JSON.stringify(settings).includes('credential_ref'),false);
  assert.equal(JSON.stringify(settings).includes('credential_fingerprint'),false);
  assert.equal((await api('/api/ai/settings')).settings.model,'main-replacement-browser-fixture');
  assert.equal(modelCalls,before);
 });
 await test('主连接保存和检测事件只刷新总管摘要，保留尚未保存的独立密钥及模型',async()=>{
  const before=modelCalls;
  await page.locator('#agentModel').fill('unsaved-independent-draft');
  await page.locator('#agentApiKey').fill(draftSecret);
  await page.evaluate(()=>{globalThis.__agentForm=document.querySelector('#agentConnectionSettings')});
  const final=await registerConnection(page,'main-final-browser-fixture');
  await assignMain(page,final);
  await page.waitForFunction(id=>document.querySelector('#modelDefault')?.value===id&&
   document.querySelector('[data-agent-connection-state]')?.textContent==='已配置',final.id);
  assert.equal(await page.evaluate(()=>globalThis.__agentForm===document.querySelector('#agentConnectionSettings')),true);
  assert.equal(await page.locator('#agentModel').inputValue(),'unsaved-independent-draft');
  assert.equal(await page.locator('#agentApiKey').inputValue(),draftSecret);
  await page.locator(`[data-connection-edit="${final.id}"]`).click();
  await page.locator('#connectionProbe').click();
  await page.locator('#connectionProbeStatus[data-state="success"]').waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-agent-connection-state]')?.textContent==='已配置');
  assert.equal(await page.locator('#agentModel').inputValue(),'unsaved-independent-draft');
  assert.equal(await page.locator('#agentApiKey').inputValue(),draftSecret);
  assert.equal((await api('/api/agent-chat/settings')).settings.model,'independent-browser-fixture');
  assert.equal(modelCalls,before+2);
 });
 await test('切换模式保留独立模型，浏览器存储和服务日志不含保存及未保存密钥',async()=>{
  const before=modelCalls;
  await page.locator('[name="agentConnectionMode"][value="inherit"]').check();
  await page.locator('#agentConnectionSettings button[type="submit"]').click();
  await page.locator('#agentProbeStatus').filter({hasText:'总管设置已保存'}).waitFor();
  assert.equal((await api('/api/agent-chat/settings')).settings.model,'main-final-browser-fixture');
  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  assert.equal(await page.locator('#agentModel').inputValue(),'independent-browser-fixture');
  const storage=await page.evaluate(()=>[...Object.values(localStorage),...Object.values(sessionStorage)].join('\n'));
  for(const value of [secret,draftSecret]){
   assert.equal(storage.includes(value),false);assert.equal(serverLog.includes(value),false);
  }
  assert.equal(modelCalls,before);
 });
 assert.deepEqual(report.page_errors,[]);
 await page.screenshot({path:path.join(temporary,'agent-settings-desktop.png')});
 report.screenshots=['agent-settings-desktop.png'];report.local_mock_model_requests=modelCalls;report.passed=true;
}
main().catch(error=>{report.failure={message:error.stack,server_log:serverLog.slice(-3000)};process.exitCode=1})
 .finally(async()=>{
  if(browser)await browser.close();
  if(app&&app.exitCode===null){
   const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill('SIGTERM');
   await Promise.race([stopped,pause(2000)]);if(app.exitCode===null)app.kill('SIGKILL');await stopped;
  }
  if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
  fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
 });
