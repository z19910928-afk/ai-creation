'use strict';

/* 提示词工坊面板的真实浏览器回归。
 *
 * 覆盖：列表分组渲染、内置模板只读与按钮禁用、另存为自定义、编辑保存、
 * 删除回退、新建、筛选、后端 409 保护、重置恢复出厂正文、同动作多条自定义时
 * 只有最新一条「生效中」（徽章/提示/编辑区状态）、保存非生效条后徽章转移、
 * 删除生效条后徽章回落、删光自定义后内置恢复基础状态、无 console/page 错误、
 * favicon 不再 404。
 *
 * 跑法（本机没有全局 node 时用 Electron 当 node）：
 *   ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/Electron.app/Contents/MacOS/Electron \
 *     tests/prompt_workshop_browser_regressions.cjs
 */

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.MOYU_PLAYWRIGHT_MODULE||'playwright');

const root=path.resolve(__dirname,'..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-prompts-browser-'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,browser,context,page,base,serverLog='';
const results={passed:false,scope:'提示词工坊面板、隔离 SQLite、本机模拟数据',cases:[],artifacts:temporary};
const pageErrors=[],consoleErrors=[],faviconRequests=[];

async function freePort(){
 const socket=net.createServer();
 await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;
 await new Promise(resolve=>socket.close(resolve));
 return port;
}

async function api(route,method='GET',body){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json'},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();
 return {status:response.status,data};
}

async function test(name,fn){
 console.log(JSON.stringify({test:name,state:'running'}));
 await fn();
 results.cases.push({name,passed:true});
 console.log(JSON.stringify({test:name,state:'passed'}));
}

async function rows(){return page.locator('[data-prompt-root] .prompt-row').count()}
async function waitRows(count){
 await page.waitForFunction(n=>document.querySelectorAll('[data-prompt-root] .prompt-row').length===n,count);
}

async function main(){
 const port=await freePort();
 base=`http://127.0.0.1:${port}`;
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,
  env:{...process.env,MOYU_DB:path.join(temporary,'prompts.sqlite3'),MOYU_HOST:'127.0.0.1',
   MOYU_PORT:String(port),MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',
   MOYU_SECRET_KEY:'',MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let i=0;i<200;i++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  if(app.exitCode!==null)throw new Error(`server exited: ${serverLog.slice(-2500)}`);
  if(i===199)throw new Error(`server unavailable: ${serverLog.slice(-2500)}`);
  await pause(40);
 }

 browser=await chromium.launch();
 context=await browser.newContext();
 page=await context.newPage();
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text())});
 page.on('request',request=>{if(new URL(request.url()).pathname==='/favicon.ico')faviconRequests.push(request.url())});

 await page.goto(base+'/',{waitUntil:'networkidle'});
 await page.locator('[data-view="prompts"]').click();
 await page.locator('[data-prompt-root] .prompt-row').first().waitFor();

 await test('列表按动作分组渲染内置模板',async()=>{
  assert.equal(await rows(),22);
  const groups=await page.locator('[data-prompt-root] .prompt-group-head span').allTextContents();
  assert.deepEqual(groups,['AI 任务指令','AI 通用片段','写作总管对话','创作规划','候选整理','连接测试']);
  // 编辑区头部也会渲染一个徽章，统计列表里的即可。
  assert.equal(await page.locator('.prompt-list .prompt-badge--builtin').count(),22);
 });

 await test('默认选中续写模板，内置正文只读且危险按钮禁用',async()=>{
  assert.equal(await page.locator('.prompt-subtitle code').first().textContent(),'ai.task.continue');
  assert.equal(await page.locator('[data-prompt-body]').getAttribute('readonly'),'');
  assert.equal(await page.locator('[data-prompt-name]').getAttribute('readonly'),'');
  assert.equal(await page.locator('[data-prompt-save]').isDisabled(),true);
  assert.equal(await page.locator('[data-prompt-delete]').isDisabled(),true);
  assert.ok((await page.locator('[data-prompt-protect]').textContent()).includes('内置模板只读'));
 });

 await test('初始化时没有任何覆盖徽章',async()=>{
  assert.equal(await page.locator('.prompt-list .prompt-badge--override').count(),0);
 });

 await test('另存为自定义后进入可编辑状态并标记被覆盖',async()=>{
  await page.locator('[data-prompt-clone]').click();
  await page.waitForFunction(()=>{
   const body=document.querySelector('[data-prompt-body]');
   return body&&!body.hasAttribute('readonly')&&document.querySelectorAll('[data-prompt-root] .prompt-row').length===23;
  });
  assert.equal(await page.locator('[data-prompt-name]').inputValue(),'续写任务指令（我的版本）');
  assert.equal(await page.locator('.prompt-list .prompt-badge--override').count(),1);
 });

 await test('编辑正文后出现脏标记，保存按钮启用',async()=>{
  await page.locator('[data-prompt-name]').fill('续写任务指令（定制）');
  await page.locator('[data-prompt-body]').fill('请续写 {{chapter_text}}，保持克制，不要展开新情节。');
  assert.equal(await page.locator('[data-prompt-dirty]').textContent(),'有未保存的修改');
  assert.equal(await page.locator('[data-prompt-save]').isDisabled(),false);
 });

 await test('保存后正文与名称落库，脏标记清空',async()=>{
  await page.locator('[data-prompt-save]').click();
  await page.waitForFunction(()=>document.querySelector('[data-prompt-dirty]')?.textContent==='');
  await page.waitForFunction(()=>document.querySelector('[data-prompt-name]')?.value==='续写任务指令（定制）');
  assert.equal(await page.locator('[data-prompt-body]').inputValue(),'请续写 {{chapter_text}}，保持克制，不要展开新情节。');
  const list=await api('/api/prompt-templates');
  const custom=list.data.templates.find(item=>!item.is_builtin);
  assert.equal(custom.body,'请续写 {{chapter_text}}，保持克制，不要展开新情节。');
 });

 await test('重置把自定义模板恢复为出厂正文',async()=>{
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-prompt-reset]').click();
  await page.waitForFunction(()=>{
   const body=document.querySelector('[data-prompt-body]');
   return body&&body.value.indexOf('不要展开新情节')===-1&&body.value.length>0;
  });
  const list=await api('/api/prompt-templates');
  const builtin=list.data.templates.find(item=>item.is_builtin&&item.action==='ai.task.continue');
  const custom=list.data.templates.find(item=>!item.is_builtin);
  assert.equal(custom.body,builtin.body);
 });

 await test('删除自定义模板后回退到 22 条且覆盖徽章消失',async()=>{
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-prompt-delete]').click();
  await waitRows(22);
  assert.equal(await page.locator('.prompt-list .prompt-badge--override').count(),0);
 });

 await test('新建表单在动作/名称/正文齐全前不可提交',async()=>{
  await page.locator('[data-prompt-new]').click();
  await page.locator('[data-prompt-action]').waitFor();
  assert.equal(await page.locator('[data-prompt-save]').isDisabled(),true);
  await page.locator('[data-prompt-action]').fill('ai.task.custom_note');
  await page.locator('[data-prompt-name]').fill('自定义备注');
  await page.locator('[data-prompt-body]').fill('这是自定义模板正文。');
  assert.equal(await page.locator('[data-prompt-save]').isDisabled(),false);
 });

 await test('新建后自动选中新模板并出现在列表',async()=>{
  await page.locator('[data-prompt-save]').click();
  await waitRows(23);
  assert.equal(await page.locator('.prompt-subtitle code').first().textContent(),'ai.task.custom_note');
 });

 await test('筛选框按动作过滤列表',async()=>{
  await page.locator('[data-prompt-filter]').fill('custom_note');
  await page.waitForFunction(()=>document.querySelectorAll('[data-prompt-root] .prompt-row').length===1);
  await page.locator('[data-prompt-filter]').fill('');
  await waitRows(23);
 });

 await test('内置模板的后端 PUT / DELETE 都返回 409',async()=>{
  const list=await api('/api/prompt-templates');
  const builtin=list.data.templates.find(item=>item.is_builtin&&item.action==='ai.task.continue');
  const put=await api('/api/prompt-templates/'+builtin.id,'PUT',{body:'x'});
  assert.equal(put.status,409);
  assert.ok(put.data.error.includes('内置模板不可修改'));
  const remove=await api('/api/prompt-templates/'+builtin.id,'DELETE');
  assert.equal(remove.status,409);
  assert.ok(remove.data.error.includes('内置模板不可删除'));
 });

 let polishFirst=null,polishSecond=null;

 await test('同一动作两条自定义只有 updated_at 最新的一条带生效中徽章',async()=>{
  // 行数用相对值，避免和前面用例累积的行数写死耦合。
  const baseRows=await rows();
  const first=await api('/api/prompt-templates','POST',
   {action:'ai.task.polish',name:'润色·甲版',body:'甲版正文：润色 {{chapter_text}}。'});
  assert.equal(first.status,201);
  polishFirst=first.data;
  // updated_at 精度是秒：隔开一秒，保证两条的时间戳有确定先后。
  await pause(1100);
  const second=await api('/api/prompt-templates','POST',
   {action:'ai.task.polish',name:'润色·乙版',body:'乙版正文：润色 {{chapter_text}}。'});
  assert.equal(second.status,201);
  polishSecond=second.data;
  await page.locator('.prompt-toolbar [data-prompt-refresh]').click();
  await waitRows(baseRows+2);
  assert.equal(await page.locator('.prompt-list .prompt-badge--effective').count(),1);
  const secondRow=page.locator(`[data-prompt-id="${polishSecond.id}"]`);
  assert.ok((await secondRow.textContent()).includes('生效中'));
  assert.equal(await secondRow.locator('[data-prompt-inactive]').count(),0);
  const firstRow=page.locator(`[data-prompt-id="${polishFirst.id}"]`);
  assert.equal((await firstRow.textContent()).includes('生效中'),false);
  assert.ok((await firstRow.textContent()).includes('同动作已有生效版本，本条不会生效'));
  assert.equal(await firstRow.locator('[data-prompt-inactive]').count(),1);
 });

 await test('编辑区头部同步显示当前条目是否生效',async()=>{
  await page.locator(`[data-prompt-id="${polishFirst.id}"]`).click();
  await page.waitForFunction(()=>document.querySelector('[data-prompt-effective-state]')?.dataset.promptEffective==='no');
  assert.ok((await page.locator('[data-prompt-effective-state]').textContent()).includes('本条不会生效'));
  await page.locator(`[data-prompt-id="${polishSecond.id}"]`).click();
  await page.waitForFunction(()=>document.querySelector('[data-prompt-effective-state]')?.dataset.promptEffective==='yes');
  assert.ok((await page.locator('[data-prompt-effective-state]').textContent()).includes('正在生效'));
 });

 await test('保存非生效条后生效徽章转移到刚保存的模板',async()=>{
  await page.locator(`[data-prompt-id="${polishFirst.id}"]`).click();
  assert.equal(await page.locator('.prompt-editor-head h3').textContent(),'润色·甲版');
  await page.locator('[data-prompt-body]').fill('甲版正文：第二版润色 {{chapter_text}}。');
  await pause(1100);
  await page.locator('[data-prompt-save]').click();
  await page.waitForFunction(()=>document.querySelector('[data-prompt-dirty]')?.textContent==='');
  await page.waitForFunction(id=>Boolean(document.querySelector(`[data-prompt-id="${id}"] .prompt-badge--effective`)),
   polishFirst.id);
  assert.equal(await page.locator('.prompt-list .prompt-badge--effective').count(),1);
  const firstRow=page.locator(`[data-prompt-id="${polishFirst.id}"]`);
  assert.equal(await firstRow.locator('[data-prompt-inactive]').count(),0);
  const secondRow=page.locator(`[data-prompt-id="${polishSecond.id}"]`);
  assert.ok((await secondRow.textContent()).includes('同动作已有生效版本，本条不会生效'));
  assert.equal(await secondRow.locator('[data-prompt-inactive]').count(),1);
  assert.equal(await page.locator('[data-prompt-effective-state]').getAttribute('data-prompt-effective'),'yes');
 });

 await test('删除生效条后生效徽章回落到剩下的自定义模板',async()=>{
  assert.equal(await page.locator('.prompt-editor-head h3').textContent(),'润色·甲版');
  const beforeDelete=await rows();
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-prompt-delete]').click();
  await waitRows(beforeDelete-1);
  assert.equal(await page.locator('.prompt-list .prompt-badge--effective').count(),1);
  assert.equal(await page.locator(`[data-prompt-id="${polishFirst.id}"]`).count(),0);
  const secondRow=page.locator(`[data-prompt-id="${polishSecond.id}"]`);
  assert.ok((await secondRow.textContent()).includes('生效中'));
  assert.equal(await secondRow.locator('[data-prompt-inactive]').count(),0);
 });

 await test('删光自定义模板后没有生效中徽章，内置行回到基础状态',async()=>{
  const beforeCleanup=await rows();
  assert.ok(beforeCleanup>22,'本用例要求此时至少有一条自定义模板');
  const list=await api('/api/prompt-templates');
  const customs=list.data.templates.filter(item=>!item.is_builtin);
  assert.ok(customs.length>=1,'本用例要求此时至少有一条自定义模板');
  for(const item of customs){
   const removed=await api('/api/prompt-templates/'+item.id,'DELETE');
   assert.equal(removed.status,200);
  }
  await page.locator('.prompt-toolbar [data-prompt-refresh]').click();
  await waitRows(22);
  assert.equal(await page.locator('.prompt-list .prompt-badge--effective').count(),0);
  assert.equal(await page.locator('.prompt-badge--effective').count(),0);
  assert.equal(await page.locator('.prompt-list .prompt-badge--override').count(),0);
  assert.equal(await page.locator('.prompt-list .prompt-badge--custom').count(),0);
  assert.equal(await page.locator('[data-prompt-inactive]').count(),0);
  assert.equal(await page.locator('[data-prompt-effective-state]').getAttribute('data-prompt-effective'),'yes');
  assert.ok((await page.locator('[data-prompt-effective-state]').textContent()).includes('内置模板正在生效'));
  const polishBuiltin=page.locator('[data-prompt-id="pt_builtin_ai_task_polish"]');
  assert.ok((await polishBuiltin.textContent()).includes('内置'));
  assert.equal((await polishBuiltin.textContent()).includes('被覆盖'),false);
 });

 await test('没有 console / page 错误，且页面不再请求 favicon.ico',async()=>{
  assert.deepEqual(pageErrors,[]);
  assert.deepEqual(consoleErrors,[]);
  assert.deepEqual(faviconRequests,[],'head 里的 data: 图标应让浏览器不再请求 /favicon.ico');
 });

 assert.deepEqual(pageErrors,[]);
 results.passed=true;
 results.desktop_scenario_count=results.cases.length;
 console.log(JSON.stringify(results,null,2));
 await context.close();
 await browser.close();
 app.kill('SIGTERM');
 await new Promise(resolve=>app.once('exit',resolve));
}

main().catch(async error=>{
 console.error(JSON.stringify({passed:false,error:error.stack,server_log:serverLog.slice(-5000)},null,2));
 try{await context?.close()}catch{}
 try{await browser?.close()}catch{}
 try{app?.kill('SIGTERM')}catch{}
 process.exitCode=1;
});
