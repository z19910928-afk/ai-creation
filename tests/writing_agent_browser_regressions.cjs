'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-agent-browser-'));
const report = {passed:false, scope:'真实桌面 Chrome、隔离数据库、免费本机模拟模型', cases:[], screenshots:[], page_errors:[], paid_provider_calls:0, artifacts:temporary};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, provider, browser, base, appPort, providerPort, firstId, fault = '', delay = 80;
let log = '';
const requests = [];
const prose = '林岚将手收回来，门里的脚步声也停了。她把铜钥匙平放在门槛上，向后退开一步。\n\n门开了一道缝。守卫没有看钥匙，只盯着她袖口的血迹。';
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
async function api(route, method='GET', body, project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},body:body===undefined?undefined:JSON.stringify(body)});
 const value=await response.json();assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(value)}`);return value;
}
async function session(id){return (await api('/api/writing-agent')).sessions.find(item=>item.id===id)}
async function until(fn, description){for(let i=0;i<160;i++){const value=await fn();if(value)return value;await pause(100)}throw new Error('Timed out: '+description+'\n'+log.slice(-2000))}
async function waitStatus(id,status){return until(async()=>{const row=await session(id);if(row?.status==='requires_action'&&status!=='requires_action')throw new Error(row.error);return row?.status===status&&row},status)}
async function startApp(){
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,env:{...process.env,
  MOYU_DB:path.join(temporary,'story.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(appPort),
  MOYU_AI_BASE_URL:`http://127.0.0.1:${providerPort}/v1`,MOYU_AI_MODEL:'agent-local-fixture',MOYU_AI_API_KEY:'',
  MOYU_AI_DAILY_QUOTA:'100',MOYU_SECRET_KEY:'',MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>log+=chunk.toString());
 await until(async()=>{if(app.exitCode!==null)throw new Error(log);try{return (await fetch(base+'/api/health')).ok}catch{return false}},'server startup');
}
async function stopApp(){if(app&&app.exitCode===null){const stopped=new Promise(r=>app.once('exit',r));app.kill('SIGTERM');await stopped}}
function output(input){
 const system=input.messages[0].content;
 const raw=input.messages.find(m=>m.role==='user').content;
 const material=JSON.parse(raw.split('<manuscript>\n')[1].split('\n</manuscript>')[0]);
 if(material.planning_workflow){
  const stage=material.planning_workflow.stage,b=material.blueprint;
  if(stage==='overview')return JSON.stringify({premise:b.premise,genre:b.genre||'悬疑',audience:'长篇小说读者',tone:'克制',contract:'保留人物认知边界。'});
  if(stage==='volumes')return JSON.stringify({volumes:b.volumes.map((v,index)=>({id:v.id,title:`第${index+1}卷 雾门`,summary:'林岚追查钥匙的主人。'}))});
  if(stage==='chapters')return JSON.stringify({volumes:b.volumes.map(v=>({id:v.id,chapters:v.chapters.map((c,index)=>({
   id:c.id,title:`第${index+1}章 铜钥匙`,goal:'查清旧门的来历',pov:'林岚',hook:'守卫认出了钥匙。'}))}))});
  return JSON.stringify({volumes:b.volumes.map(v=>({id:v.id,chapters:v.chapters.map(c=>({id:c.id,scenes:c.scenes.map((s,index)=>({
   id:s.id,title:`场景${index+1} 门外对峙`,goal:'说服守卫',location:'旧门',pov:'林岚'}))}))}))});
 }
 if(system.includes('完整普通正文'))return JSON.stringify({summary:{text:'林岚仍在门外。',evidence:[{source_quote:material.current_chapter.body.split('\n')[0]}]},handoff:{text:'',evidence:[]},candidates:[]});
 if(system.includes('人物动机、因果、时间'))return JSON.stringify({findings:[],uncertainties:['本机固定回复，仅验证产品流程。']});
 return prose;
}
async function test(name,fn){await fn();report.cases.push({name,passed:true});console.log(JSON.stringify({test:name,passed:true}))}
async function screenshot(page,name,fullPage=true){const file=path.join(temporary,name);await page.screenshot({path:file,fullPage});report.screenshots.push(file)}
async function createFromUI(page,query,mode='continue'){
 await page.locator('[data-view="assistant"]').click();
 await page.locator('#agentQuery').fill(query);
 await page.locator(`[name="agentMode"][value="${mode}"]`).check();
 await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
 await until(async()=>{const rows=(await api('/api/writing-agent')).sessions;return rows.find(r=>r.query===query)},'saved plan');
 const row=(await api('/api/writing-agent')).sessions.find(r=>r.query===query);
 await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).waitFor();return row;
}
async function main(){
 providerPort=await freePort();appPort=await freePort();base=`http://127.0.0.1:${appPort}`;
 provider=http.createServer((req,res)=>{let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{
  const input=JSON.parse(raw),text=output(input),currentFault=fault,currentDelay=delay;requests.push(input);
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  res.write(`data: ${JSON.stringify({model:'agent-local-fixture',choices:[{delta:{content:text.slice(0,10)},finish_reason:null}]})}\n\n`);
  setTimeout(()=>{if(res.destroyed)return;if(currentFault==='unknown'){res.destroy();return}
   res.write(`data: ${JSON.stringify({model:'agent-local-fixture',choices:[{delta:{content:text.slice(10)},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:40,total_tokens:140}})}\n\n`);
   res.end('data: [DONE]\n\n');},currentDelay);
 })});await new Promise(r=>provider.listen(providerPort,'127.0.0.1',r));await startApp();
 const initial=await api('/api/state');firstId=initial.chapters[0].id;
 initial.chapters[0].body='林岚握住门外的铜钥匙。她听见里面有人走近。';await api('/api/state','PUT',initial);
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
 page.on('pageerror',error=>report.page_errors.push(error.message));await page.goto(base);await page.waitForFunction(()=>backendOnline);
 let mainSession;
 await test('桌面三栏工作台接入真实章节、人物资料、上下文和专注模式',async()=>{
  await page.locator('[data-view="editor"]').click();
  await page.locator('#workbenchContext [data-context-state]').waitFor();
  const sizes=await page.evaluate(()=>{
   const rect=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,top:r.top,bottom:r.bottom,height:r.height}};
   return {library:rect('.workbench-library'),editor:rect('.workbench-manuscript'),agent:rect('.workbench-assistant'),
    viewport:innerWidth,scroll:document.documentElement.scrollWidth,height:innerHeight,pageHeight:document.documentElement.scrollHeight};
  });
  assert.ok(sizes.library.right<=sizes.editor.left&&sizes.editor.right<=sizes.agent.left,JSON.stringify(sizes));
  assert.ok(sizes.editor.width>500&&sizes.agent.width>=300&&sizes.scroll<=sizes.viewport,JSON.stringify(sizes));
  assert.ok([sizes.library,sizes.editor,sizes.agent].every(rect=>rect.bottom<=sizes.height+.5)&&sizes.pageHeight<=sizes.height,JSON.stringify(sizes));
  const body=await page.locator('#chapterBody').inputValue();
  await page.locator('[data-library-tab="knowledge"]').click();await page.locator('.knowledge-select').first().click();
  await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal(await page.locator('#chapterBody').inputValue(),body);
  await page.locator('#workbenchFilter').fill('找不到的资料');await page.locator('.workbench-filter-empty').waitFor({state:'visible'});
  await page.locator('#workbenchFilter').fill('');await page.locator('[data-library-tab="chapters"]').click();
  await page.locator('[data-workbench-focus]').click();await page.locator('.workbench-assistant').waitFor({state:'hidden'});
  assert.equal(await page.locator('#chapterBody').inputValue(),body);await page.locator('[data-workbench-focus]').click();
  await page.locator('.workbench-assistant').waitFor({state:'visible'});
  for(const id of ['workbenchCandidateSection','workbenchContextSection','workbenchAgentSection']){
   await page.locator(`[data-rail-section="${id}"]`).click();
   await page.waitForFunction(id=>{
    const rail=document.querySelector('.workbench-rail-scroll').getBoundingClientRect(),section=document.getElementById(id).getBoundingClientRect();
    return section.top>=rail.top-2&&section.top<rail.bottom;
   },id);
  }
  await screenshot(page,'writing-workbench-1440x1000.png');
 });
 await test('在正文旁直接创建总管计划不调用模型，刷新后保留同一进度',async()=>{
  await page.locator('#agentQuery').fill('继续写门口的冲突，保留林岚视角。');
  await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  await until(async()=>{mainSession=(await api('/api/writing-agent')).sessions.find(row=>row.query==='继续写门口的冲突，保留林岚视角。');return mainSession},'workbench plan');
  await page.locator(`[data-agent-session="${mainSession.id}"] [data-agent-action="authorize"]`).waitFor();assert.equal(requests.length,0);
  await page.locator(`[data-agent-session="${mainSession.id}"] .agent-context-evidence`).waitFor();
  await screenshot(page,'writing-agent-plan-1440x1000.png');
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  await page.locator(`[data-agent-session="${mainSession.id}"]`).waitFor();assert.equal((await api('/api/writing-agent')).sessions.length,1);
 });
 await test('启动后后台生成正文，重复确认只复用原任务',async()=>{
  await page.locator(`[data-agent-session="${mainSession.id}"] [data-agent-action="authorize"]`).click();
  const ready=await waitStatus(mainSession.id,'awaiting_adoption');
  const repeated=await api(`/api/writing-agent/${mainSession.id}/resume`,'POST',{action:'authorize',checkpoint_id:mainSession.checkpoint_id});
  assert.equal(repeated.idempotent,true);assert.equal(requests.length,1);assert.equal(ready.calls_used,1);
 });
 await test('现有比较采纳接通总管，自动整理记忆与审稿共三次',async()=>{
  await page.locator(`[data-agent-session="${mainSession.id}"] [data-agent-open]`).click();
  await page.locator('#aiAdopt:not([disabled])').waitFor();await page.locator('#aiAdopt').click();await page.locator('[data-apply]').click();
  const ready=await waitStatus(mainSession.id,'review_ready');assert.equal(ready.calls_used,3);assert.equal(requests.length,3);
  assert.ok((await api('/api/state')).chapters.find(c=>c.id===firstId).body.endsWith(prose));
  assert.equal((await api(`/api/chapter-analysis?chapter_id=${firstId}`)).analyses.length,1);
  await page.locator(`[data-agent-session="${mainSession.id}"] [data-agent-review]`).waitFor();
  for(const selector of ['[data-agent-review]','[data-agent-analysis]','[data-agent-open]']){
   await page.evaluate(()=>{
    window.__workbenchBody=document.querySelector('#chapterBody');
    window.__workbenchBody.setSelectionRange(3,8);document.querySelector('.workbench-manuscript').scrollTop=80;
   });
   const scroll=await page.locator('.workbench-manuscript').evaluate(element=>element.scrollTop);
   await page.locator(`[data-agent-session="${mainSession.id}"] ${selector}`).click();
   await page.locator('[data-author-chapter-report]').waitFor();
   const position=await page.evaluate(()=>({same:window.__workbenchBody===document.querySelector('#chapterBody'),
    start:document.querySelector('#chapterBody').selectionStart,end:document.querySelector('#chapterBody').selectionEnd,
    scroll:document.querySelector('.workbench-manuscript').scrollTop}));
   assert.deepEqual(position,{same:true,start:3,end:8,scroll});
   await page.locator('[data-author-chapter-report] [data-close]').click();
  }
  await page.locator('[data-view="assistant"]').click();await page.locator(`[data-agent-session="${mainSession.id}"] [data-agent-review]`).waitFor();
  await screenshot(page,'writing-agent-complete-1440x1000.png');
 });
 await test('检查模式仅调用两次，服务重启后沿已存计划继续',async()=>{
  const row=await createFromUI(page,'核对人物动作、因果和章后记忆。','inspect');await stopApp();await startApp();
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  const ready=await waitStatus(row.id,'review_ready');assert.equal(ready.calls_used,2);assert.equal(requests.length,5);
 });
 await test('已启动任务停止后不追加调用，迟到内容不进入正文',async()=>{
  delay=1500;const before=(await api('/api/state')).chapters[0].body;
  const row=await createFromUI(page,'停止任务的本机验证。');
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  await until(()=>requests.length===6,'provider entered');
  const current=await session(row.id);await api(`/api/writing-agent/${row.id}/resume`,'POST',{action:'cancel',checkpoint_id:current.checkpoint_id});
  await pause(1800);assert.equal((await session(row.id)).status,'canceled');assert.equal(requests.length,6);
  assert.equal((await api('/api/state')).chapters[0].body,before);delay=80;
 });
 await test('模型连接断开后保留任务，不自动重发',async()=>{
  fault='unknown';const row=await createFromUI(page,'连接中断的本机验证。');
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  const stopped=await waitStatus(row.id,'requires_action');assert.equal(requests.length,7);assert.equal(stopped.calls_used,1);
  await api(`/api/writing-agent/${row.id}/resume`,'POST',{action:'advance',checkpoint_id:stopped.checkpoint_id});
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();await pause(300);
  assert.equal(requests.length,7);fault='';
 });
 await test('计划请求完成时不清除用户后来输入的草稿',async()=>{
  let release,seen=false;const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/writing-agent',async route=>{
   if(!seen&&route.request().method()==='POST'){
    seen=true;const response=await route.fetch();await held;await route.fulfill({response});return;
   }
   return route.continue();
  });
  await page.locator('#agentQuery').fill('先提交的计划要求。');
  const submit=page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  await until(()=>seen,'plan request entered');
  await page.locator('#agentQuery').fill('用户后来输入的新计划要求。');
  release();await submit;await page.unroute('**/api/writing-agent');
  assert.equal(await page.locator('#agentQuery').inputValue(),'用户后来输入的新计划要求。');
  assert.equal((await api('/api/writing-agent')).sessions.filter(r=>r.query==='先提交的计划要求。').length,1);
 });
 await test('旧计划请求失败后新草稿使用新身份并可保存',async()=>{
  let release,seen=false;const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/writing-agent',async route=>{
   if(!seen&&route.request().method()==='POST'){
    seen=true;await route.fetch();await held;await route.abort('failed');return;
   }
   return route.continue();
  });
  await page.locator('#agentQuery').fill('先提交后失败的计划。');
  const submit=page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  await until(()=>seen,'failed plan request entered');
  await page.locator('#agentQuery').fill('失败后输入的新计划。');
  release();await submit;await page.unroute('**/api/writing-agent');
  assert.equal(await page.locator('#agentQuery').inputValue(),'失败后输入的新计划。');
  await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  await until(()=>page.locator('#agentQuery').inputValue().then(v=>v===''),'new draft saved');
  const rows=(await api('/api/writing-agent')).sessions;
  assert.equal(rows.filter(r=>r.query==='先提交后失败的计划。').length,1);
  assert.equal(rows.filter(r=>r.query==='失败后输入的新计划。').length,1);
 });
 await test('网络失败保留创作要求，重试不会创建两条计划',async()=>{
  await page.locator('#agentQuery').fill('重试请求必须使用同一身份。');let interrupted=false;
  await page.route('**/api/writing-agent',async route=>{if(route.request().method()==='POST'&&!interrupted){interrupted=true;await route.fetch();return route.abort('failed')}return route.continue()});
  await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  await until(()=>page.locator('#writingAgent .form-error').textContent(),'network error');
  assert.equal(await page.locator('#agentQuery').inputValue(),'重试请求必须使用同一身份。');
  await page.unroute('**/api/writing-agent');await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  await until(()=>page.locator('#agentQuery').inputValue().then(v=>v===''),'retry completed');
  assert.equal((await api('/api/writing-agent')).sessions.filter(r=>r.query==='重试请求必须使用同一身份。').length,1);
 });
 await test('紧凑电脑宽度和长文本无溢出，页面没有脚本错误',async()=>{
  await page.setViewportSize({width:1280,height:900});await page.locator('#agentQuery').fill('长创作要求'.repeat(100));
  await screenshot(page,'writing-agent-1280x900.png');
  const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(dimensions.scroll<=dimensions.width);
  assert.deepEqual(report.page_errors,[]);
 });
 await test('1280电脑正文、资料和总管仍保持三栏，当前稿改动使依据过期',async()=>{
  await page.locator('[data-view="editor"]').click();await page.locator('#workbenchContext [data-context-state]').waitFor();
  const before=await page.locator('#chapterBody').inputValue();await page.locator('#chapterBody').fill(before+'\n\n她没有越过那道门槛。');
  assert.match(await page.locator('#workbenchContext [data-context-state]').textContent(),/已变化/);
  await page.locator('[data-workbench-context-refresh]').click();
  await until(()=>page.locator('#workbenchContext [data-context-state]').textContent().then(value=>value.includes('已保存稿件资料')),'updated context');
  const dimensions=await page.evaluate(()=>{
   const editor=document.querySelector('.workbench-manuscript').getBoundingClientRect(),agent=document.querySelector('.workbench-assistant').getBoundingClientRect();
   return {width:innerWidth,scroll:document.documentElement.scrollWidth,editor:editor.width,overlap:editor.right>agent.left,
    height:innerHeight,pageHeight:document.documentElement.scrollHeight,editorBottom:editor.bottom,agentBottom:agent.bottom};
  });
  assert.ok(dimensions.scroll<=dimensions.width&&dimensions.editor>=400&&!dimensions.overlap,JSON.stringify(dimensions));
  assert.ok(dimensions.pageHeight<=dimensions.height&&dimensions.editorBottom<=dimensions.height+.5&&dimensions.agentBottom<=dimensions.height+.5,JSON.stringify(dimensions));
  await screenshot(page,'writing-workbench-1280x900.png');assert.deepEqual(report.page_errors,[]);
  await page.locator('[data-view="assistant"]').click();
 });
 await test('章后记忆和审稿延迟回应在页面或作品切换后不弹出旧报告',async()=>{
  const owner=await page.evaluate(()=>requestedProject);
  const other=await api('/api/projects','POST',{title:'延迟报告隔离作品'}),otherId=other.project?.id||other.state?.project?.id;
  for(const item of [{action:'chapterAnalysis',route:'chapter-analysis'},{action:'reviewChapter',route:'story-reviews'}]){
   for(const switchBook of [false,true]){
    await page.evaluate(id=>authorSwitchProject(id),owner);await page.locator('[data-view="editor"]').click();
    let release,observed,finished,seen=false;const held=new Promise(r=>release=r),ready=new Promise(r=>observed=r),done=new Promise(r=>finished=r);
    const pattern=`**/api/${item.route}?*`;
    await page.route(pattern,async route=>{
     if(!seen&&route.request().method()==='GET'){
      seen=true;assert.equal(route.request().headers()['x-moyu-project'],owner);
      const response=await route.fetch();observed();await held;await route.fulfill({response});finished();return;
     }
     return route.continue();
    });
    await page.evaluate(action=>{void actions[action]()},item.action);await ready;
    if(switchBook)await page.evaluate(id=>authorSwitchProject(id),otherId);
    else await page.locator('[data-view="assistant"]').click();
    release();await done;await pause(100);await page.unroute(pattern);
    assert.equal(await page.locator('[data-author-chapter-report]').count(),0);
   }
  }
  await page.evaluate(id=>authorSwitchProject(id),owner);await page.locator('[data-view="editor"]').click();
  await page.locator('[data-action="chapterAnalysis"]').click();await page.locator('[data-author-chapter-report]').waitFor();
  await page.evaluate(()=>{view='assistant';render()});
  assert.equal(await page.locator('[data-author-chapter-report]').count(),0);assert.deepEqual(report.page_errors,[]);
 });
 await test('作品切换后迟到的旧任务列表不能覆盖新作品',async()=>{
  let release,observed,fulfilled,intercepted=false;const held=new Promise(r=>release=r),ready=new Promise(r=>observed=r),finished=new Promise(r=>fulfilled=r);
  await page.route('**/api/writing-agent',async route=>{if(!intercepted&&route.request().method()==='GET'&&route.request().headers()['x-moyu-project']==='default-project'){
   intercepted=true;const response=await route.fetch();observed();await held;await route.fulfill({response});fulfilled();return}return route.continue()});
  await page.evaluate(()=>{authorLoadAgent()});await ready;
  const other=await api('/api/projects','POST',{title:'隔离作品'});const id=other.project?.id||other.state?.project?.id;
  assert.ok(id,JSON.stringify(other));await page.evaluate(id=>authorSwitchProject(id),id);await page.locator('[data-view="assistant"]').click();
  release();await finished;await page.unroute('**/api/writing-agent');await pause(300);
  assert.equal(await page.locator('[data-agent-session]').count(),0);assert.deepEqual(report.page_errors,[]);
 });
 await test('整章修订忽略编辑选区，明确采纳后替换全章并继续记忆审稿',async()=>{
  await page.evaluate(()=>authorSwitchProject('default-project'));await page.locator('[data-view="editor"]').click();
  const before=(await api('/api/state')).chapters.find(c=>c.id===firstId).body,beforeCalls=requests.length;
  await page.locator('#chapterBody').evaluate(element=>element.setSelectionRange(2,7));
  await page.locator('[name="agentMode"][value="polish"]').check();
  assert.match(await page.locator('[data-agent-mode-scope]').textContent(),/整章正文/);
  await page.locator('#agentQuery').fill('整章修订，保留门外冲突和人物视角。');
  await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  const row=await until(async()=>(await api('/api/writing-agent')).sessions.find(r=>r.query==='整章修订，保留门外冲突和人物视角。'),'polish plan');
  assert.equal(row.mode,'polish');assert.equal(requests.length,beforeCalls);
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  const candidate=await waitStatus(row.id,'awaiting_adoption');
  assert.equal(candidate.current_job.source.range.start,0);
  assert.equal(candidate.current_job.source.range.end,Array.from(before).length);
  assert.equal((await api('/api/state')).chapters.find(c=>c.id===firstId).body,before);
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-open]`).click();
  await page.locator('#aiAdopt:not([disabled])').waitFor();await page.locator('#aiAdopt').click();
  const comparison=page.locator('.modal-backdrop:has(.candidate-columns)');
  await comparison.waitFor();assert.deepEqual(page.viewportSize(),{width:1280,height:900});
  assert.deepEqual(await comparison.locator('.candidate-columns h4').allTextContents(),['修改前','候选内容']);
  const versions=comparison.locator('.candidate-columns pre');assert.equal(await versions.count(),2);
  for(const [index,text] of [before,prose].entries()){
   assert.equal(await versions.nth(index).textContent(),text);assert.equal(await versions.nth(index).isVisible(),true);
  }
  for(const selector of ['[data-close]','[data-apply]']){
   assert.equal(await comparison.locator(selector).isVisible(),true);
   assert.equal(await comparison.locator(selector).isEnabled(),true);
  }
  const layout=await comparison.evaluate(element=>({
   viewport:{width:innerWidth,height:innerHeight},pageWidth:document.documentElement.scrollWidth,
   elements:[...element.querySelectorAll('.modal,.candidate-columns,.candidate-columns section,.candidate-columns pre,[data-close],[data-apply]')]
    .map(item=>{const rect=item.getBoundingClientRect();return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,
     scrollWidth:item.scrollWidth,clientWidth:item.clientWidth}})
  }));
  assert.ok(layout.pageWidth<=layout.viewport.width,JSON.stringify(layout));
  assert.ok(layout.elements.every(rect=>rect.left>=0&&rect.right<=layout.viewport.width&&rect.top>=0&&rect.bottom<=layout.viewport.height
   &&rect.scrollWidth<=rect.clientWidth+1),JSON.stringify(layout));
  assert.equal((await api('/api/state')).chapters.find(c=>c.id===firstId).body,before);
  await screenshot(page,'writing-agent-polish-compare-1280x900.png',false);
  await comparison.locator('[data-close]').click();await comparison.waitFor({state:'hidden'});
  assert.equal((await api('/api/state')).chapters.find(c=>c.id===firstId).body,before);
  assert.equal((await session(row.id)).status,'awaiting_adoption');assert.equal(requests.length,beforeCalls+1);
  await page.locator('#aiAdopt:not([disabled])').click();await comparison.waitFor();
  await page.locator('[data-apply]').click();const ready=await waitStatus(row.id,'review_ready');
  assert.equal(ready.calls_used,3);assert.equal(requests.length,beforeCalls+3);
  assert.equal((await api('/api/state')).chapters.find(c=>c.id===firstId).body,prose);
  await screenshot(page,'writing-agent-polish-1280x900.png');
 });
 let planningSession;
 await test('全书规划模式草稿刷新可恢复，任务不依赖当前打开的章节',async()=>{
  await page.locator('[data-view="assistant"]').click();await page.locator('[name="agentMode"][value="plan"]').check();
  await page.locator('#agentQuery').fill('林岚追查铜钥匙的来历，从旧门冲突展开悬疑长篇。');
  assert.equal(await page.locator('[data-agent-chapter-field]').isVisible(),false);
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  assert.equal(await page.locator('[name="agentMode"][value="plan"]').isChecked(),true);
  assert.equal(await page.locator('[data-agent-chapter-field]').isVisible(),false);
  const beforeCalls=requests.length;
  await page.getByRole('button',{name:'准备写作计划',exact:true}).click();
  planningSession=await until(async()=>(await api('/api/writing-agent')).sessions.find(r=>r.mode==='plan'),'book plan');
  assert.equal(planningSession.max_calls,4);assert.equal(requests.length,beforeCalls);
  await page.locator('[data-view="editor"]').click();
  await page.locator(`[data-agent-session="${planningSession.id}"]`).waitFor();
  assert.match(await page.locator(`[data-agent-session="${planningSession.id}"] h3`).textContent(),/全书规划/);
  await page.locator('[data-view="assistant"]').click();
 });
 await test('四阶段规划逐次确认，完整采用前不改变蓝图或正文',async()=>{
  const beforeCalls=requests.length,before=await api('/api/state'),original=await api('/api/blueprint');
  const id=planningSession.id,section=page.locator(`[data-agent-session="${id}"]`);
  await section.locator('[data-agent-action="authorize"]').click();
  for(const [index,stage] of ['overview','volumes','chapters','scenes'].entries()){
   const row=await until(async()=>{const current=await session(id);if(current.status==='requires_action')throw new Error(current.error);
    return current.status==='awaiting_plan_confirmation'&&current.stage===stage&&current},'planning stage '+stage);
   assert.equal(row.calls_used,index+1);assert.equal(requests.length,beforeCalls+index+1);
   await until(()=>section.locator('.agent-planning-candidate').textContent().then(text=>text&&text.includes(
    stage==='overview'?'故事核心':stage==='volumes'?'雾门':stage==='chapters'?'铜钥匙':'门外对峙')),'planning preview '+stage);
   assert.equal(await section.locator('[data-agent-open]').count(),0);
   assert.equal(await page.locator('#chapterBody').count(),0);
   await pause(250);assert.equal(requests.length,beforeCalls+index+1);
   if(stage==='overview'){
    await section.evaluate(element=>element.scrollIntoView({block:'start'}));await page.evaluate(()=>window.scrollBy(0,-80));
    await screenshot(page,'writing-agent-overview-confirm-1280x900.png',false);
   }
   await section.locator('[data-agent-action="confirm"]').click();
  }
  const complete=await waitStatus(id,'awaiting_plan_application');assert.equal(complete.calls_used,4);
  await section.locator('[data-agent-action="apply"]').waitFor();
  const preview=await section.locator('.agent-complete-planning').textContent();
  assert.match(preview,/故事核心/);assert.match(preview,/雾门/);assert.match(preview,/铜钥匙/);assert.match(preview,/门外对峙/);
  assert.deepEqual((await api('/api/blueprint')).blueprint,original.blueprint);
  assert.deepEqual((await api('/api/state')).chapters,before.chapters);
  await section.evaluate(element=>element.scrollIntoView({block:'start'}));await page.evaluate(()=>window.scrollBy(0,-80));
  await screenshot(page,'writing-agent-full-plan-1280x900.png',false);
  await section.locator('[data-agent-action="apply"]').click();const applied=await waitStatus(id,'review_ready');
  assert.equal(applied.planning_workflow.applied,true);assert.equal(requests.length,beforeCalls+4);
  assert.deepEqual((await api('/api/state')).chapters,before.chapters);
  await section.locator('[data-agent-planning]').click();await page.locator('#blueprintHost .plan-volume').waitFor();
  await page.locator('[data-action="materializePlan"]').click();
  await until(async()=>(await api('/api/state')).chapters.some(c=>c.plan_id),'materialized agent plan');
  const chapters=(await api('/api/state')).chapters;
  for(const old of before.chapters)assert.deepEqual(chapters.find(c=>c.id===old.id),old);
  assert.equal(chapters.filter(c=>c.plan_id).length,3);
 });
 await test('蓝图变化后旧规划停止执行，恢复只读任务没有启动或采纳按钮',async()=>{
  const row=await createFromUI(page,'根据当前蓝图继续规划。','plan'),beforeCalls=requests.length;
  const current=await api('/api/blueprint');current.blueprint.premise+=' 后来的作者修改。';
  await api('/api/blueprint','PUT',{blueprint:current.blueprint,expected_version:current.version});
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  const section=page.locator(`[data-agent-session="${row.id}"]`);await section.locator('.agent-source-stale').waitFor();
  assert.match(await section.locator('.agent-source-stale').textContent(),/创作规划已变化/);
  await section.locator('[data-agent-action="authorize"]').click();await waitStatus(row.id,'requires_action');
  assert.equal(requests.length,beforeCalls);
  for(const status of ['awaiting_authorization','awaiting_job','awaiting_adoption','awaiting_plan_confirmation','awaiting_plan_application','review_ready']){
   await page.evaluate(({id,status})=>{
    clearTimeout(authorAgentTimer);const row=authorAgentRows.find(item=>item.id===id);
    authorAgentRows=[{...row,status,restored_read_only:true}];authorRenderAgent(authorAgentScope(),document.querySelector('#agentSessions'));
   },{id:row.id,status});
   assert.equal(await page.locator('[data-agent-session] button').count(),0);
  }
  assert.deepEqual(report.page_errors,[]);
 });
 await test('全书完稿后工作台和空章节状态均隐藏新建入口，直接操作也不新增',async()=>{
  const saved=await api('/api/state');saved.project.book_status='complete';await api('/api/state','PUT',saved);
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="editor"]').click();
  assert.equal(await page.locator('[data-action="newChapter"]').count(),0);
  assert.equal(await page.locator('[data-action="finishChapter"]').count(),0);
  const count=(await api('/api/state')).chapters.length;
  await page.evaluate(()=>actions.newChapter());assert.equal((await api('/api/state')).chapters.length,count);
  await page.locator('[data-view="dashboard"]').click();assert.equal(await page.locator('[data-action="newChapter"]').count(),0);
  await page.evaluate(()=>{state.chapters=[];activeChapter=null;view='editor';render()});
  assert.equal(await page.locator('[data-action="newChapter"]').count(),0);
  assert.deepEqual(report.page_errors,[]);
 });
 report.provider_calls=requests.length;report.passed=true;await context.close();
}
main().catch(error=>{report.error=error.stack;report.server_log=log.slice(-3000);process.exitCode=1}).finally(async()=>{
 if(browser)await browser.close();await stopApp();if(provider)await new Promise(r=>provider.close(r));
 fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
});
