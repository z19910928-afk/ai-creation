'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-agent-browser-'));
const report = {passed:false, scope:'真实桌面 Chrome、隔离数据库、免费本机模拟模型；通过真实接口建立任务，再由页面验证启动、候选采纳、章后逐项决定、恢复和规划确认。持续聊天入口另由聊天专项覆盖。', cases:[], screenshots:[], page_errors:[], paid_provider_calls:0, artifacts:temporary};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, provider, browser, base, appPort, providerPort, firstId, fault = '', delay = 80, richAnalysis = false;
let log = '';
const requests = [];
const prose = '林岚将手收回来，门里的脚步声也停了。她把铜钥匙平放在门槛上，向后退开一步。\n\n门开了一道缝。守卫没有看钥匙，只盯着她袖口的血迹。';
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
async function api(route, method='GET', body, project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},body:body===undefined?undefined:JSON.stringify(body)});
 const value=await response.json();assert.ok(response.ok,`${route}: ${response.status} ${JSON.stringify(value)}`);return value;
}
async function session(id,project='default-project'){return (await api('/api/writing-agent','GET',undefined,project)).sessions.find(item=>item.id===id)}
async function until(fn, description){for(let i=0;i<160;i++){const value=await fn();if(value)return value;await pause(100)}throw new Error('Timed out: '+description+'\n'+log.slice(-2000))}
async function waitStatus(id,status,project='default-project'){return until(async()=>{const row=await session(id,project);if(row?.status==='requires_action'&&status!=='requires_action')throw new Error(row.error);return row?.status===status&&row},status)}
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
 if(system.includes('完整普通正文')){
  const quote=material.current_chapter.body.split('\n')[0],evidence=[{source_quote:quote}];
  return JSON.stringify({summary:{text:'林岚仍在门外。',evidence},
   handoff:{text:richAnalysis?'核对铜钥匙是否仍在门槛上。':'',evidence:richAnalysis?evidence:[]},
   candidates:richAnalysis?[{kind:'event',name:'铜钥匙留在门外',note:'林岚把铜钥匙留在门槛上。',
    subject_name:'林岚',epistemic_mode:'world_truth',known_by:[],source_quote:quote}]:[]});
 }
 if(system.includes('人物动机、因果、时间'))return JSON.stringify({findings:[],uncertainties:['本机固定回复，仅验证产品流程。']});
 return prose;
}
async function test(name,fn){await fn();report.cases.push({name,passed:true});console.log(JSON.stringify({test:name,passed:true}))}
async function screenshot(page,name,fullPage=true){const file=path.join(temporary,name);await page.screenshot({path:file,fullPage});report.screenshots.push(file)}
async function createSavedPlan(page,query,mode='continue'){
 await page.locator('[data-view="assistant"]').click();
 const response=await api('/api/writing-agent','POST',{request_id:crypto.randomUUID(),query,mode,chapter_id:firstId});
 const row=response.session;await page.evaluate(()=>authorLoadAgent());
 await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).waitFor();return row;
}
async function confirmAgentMemory(page,id,project='default-project'){
 const waiting=await waitStatus(id,'awaiting_memory',project),calls=requests.length;
 assert.equal(waiting.current_job.task,'chapter_analysis');
 await page.evaluate(()=>authorLoadAgent());
 if(await page.locator('[data-chat-result="workbenchAgentSection"]').isVisible()){
  await page.locator('[data-chat-result="workbenchAgentSection"]').click();
 }
 await page.locator(`[data-agent-session="${id}"] [data-agent-analysis]`).click();
 const modal=page.locator('[data-author-chapter-report="analysis"]');await modal.waitFor();
 assert.equal(await modal.getAttribute('data-analysis-id'),waiting.result_refs.chapter_analysis.id);
 assert.equal(await modal.locator('#analysisSummaryDecision').inputValue(),'');
 assert.equal(requests.length,calls);
 await modal.locator('#analysisSummaryDecision').selectOption('accepted');
 await modal.locator('[data-confirm-analysis]').click();await modal.waitFor({state:'hidden'});
 return waitStatus(id,'review_ready',project);
}
async function openAgentMemory(page,id,analysisId){
 await page.evaluate(()=>authorLoadAgent());
 if(await page.locator('[data-chat-result="workbenchAgentSection"]').isVisible()){
  await page.locator('[data-chat-result="workbenchAgentSection"]').click();
 }
 await page.locator(`[data-agent-session="${id}"] [data-agent-analysis]`).click();
 const modal=page.locator('[data-author-chapter-report="analysis"]');await modal.waitFor();
 assert.equal(await modal.getAttribute('data-analysis-id'),analysisId);return modal;
}
async function memoryDecisionLayout(modal){
 const layout=await modal.evaluate(element=>({
  width:innerWidth,height:innerHeight,pageWidth:document.documentElement.scrollWidth,
  elements:[...element.querySelectorAll('.modal,textarea,input,select,.analysis-item,.modal-actions')]
   .map(item=>{const r=item.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,
    scrollWidth:item.scrollWidth,clientWidth:item.clientWidth,isModal:item.matches('.modal')}})
 }));
 assert.ok(layout.pageWidth<=layout.width,JSON.stringify(layout));
 assert.ok(layout.elements.every(item=>item.left>=0&&item.right<=layout.width&&item.scrollWidth<=item.clientWidth+1),JSON.stringify(layout));
 assert.ok(layout.elements.filter(item=>item.isModal).every(item=>item.top>=0&&item.bottom<=layout.height),JSON.stringify(layout));
}
async function createMemoryProject(page,width){
 const created=await api('/api/projects','POST',{title:'章后确认桌面验证-'+width});
 const owner=created.project?.id||created.state?.project?.id;assert.ok(owner);
 const saved=await api('/api/state','GET',undefined,owner);saved.chapters=saved.chapters.slice(0,1);
 Object.assign(saved.chapters[0],{body:'林岚把铜钥匙留在门槛上。\n\n守卫没有回答。',recap:'',next_brief:''});
 saved.records=[];saved.memory=[];saved.plot={setup:[],develop:[],payoff:[]};
 await api('/api/state','PUT',saved,owner);
 await api('/api/records','POST',{type:'人物',name:'林岚',description:'旧门外的来客。'},owner);
 await page.locator('[data-view="library"]').click();
 await page.locator(`[data-project-open="${owner}"]`).click();await page.locator('[data-view="editor"]').click();
 await page.waitForFunction(id=>requestedProject===id&&backendOnline&&!isDirty()&&!syncInFlight,owner);
 const row=(await api('/api/writing-agent','POST',{request_id:crypto.randomUUID(),chapter_id:saved.chapters[0].id,
  query:'核对铜钥匙去向，逐项处理章后资料。',mode:'inspect'},owner)).session;
 await page.evaluate(()=>authorLoadAgent());
 await page.locator('[data-chat-result="workbenchAgentSection"]').click();
 await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
 return {owner,chapterId:saved.chapters[0].id,row};
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
  await page.locator('[data-chat-result="workbenchContextSection"]').click();
  await page.locator('#workbenchContext [data-context-state]').waitFor();
  const sizes=await page.evaluate(()=>{
   const rect=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,top:r.top,bottom:r.bottom,height:r.height}};
   return {library:rect('.workbench-library'),editor:rect('.workbench-manuscript'),agent:rect('.agent-chat'),
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
  await page.locator('[data-workbench-results-close]').click();
  await page.locator('[data-workbench-focus]').click();await page.locator('.agent-chat').waitFor({state:'hidden'});
  assert.equal(await page.locator('#chapterBody').inputValue(),body);await page.locator('[data-workbench-focus]').click();
  await page.locator('.agent-chat').waitFor({state:'visible'});
  for(const id of ['workbenchCandidateSection','workbenchContextSection','workbenchAgentSection']){
   await page.locator(`[data-chat-result="${id}"]`).click();
   await page.waitForFunction(id=>{
    const rail=document.querySelector('.workbench-rail-scroll').getBoundingClientRect(),section=document.getElementById(id).getBoundingClientRect();
    return section.top>=rail.top-2&&section.top<rail.bottom;
   },id);
  }
  await screenshot(page,'writing-workbench-1440x1000.png');
 });
 await test('已保存总管计划在正文旁恢复，不调用模型，刷新后保留同一进度',async()=>{
  mainSession=(await api('/api/writing-agent','POST',{request_id:crypto.randomUUID(),chapter_id:firstId,
   query:'继续写门口的冲突，保留林岚视角。',mode:'continue'})).session;
  await page.evaluate(()=>authorLoadAgent());
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
 await test('正文采纳后等待作者决定章后资料，再继续审稿共三次',async()=>{
  await page.locator(`[data-agent-session="${mainSession.id}"] [data-agent-open]`).click();
  await page.locator('#aiAdopt:not([disabled])').waitFor();await page.locator('#aiAdopt').click();await page.locator('[data-apply]').click();
  const waiting=await waitStatus(mainSession.id,'awaiting_memory');assert.equal(waiting.calls_used,2);assert.equal(requests.length,2);
  assert.equal((await api(`/api/story-reviews?chapter_id=${firstId}`)).reviews.length,0);
  const ready=await confirmAgentMemory(page,mainSession.id);assert.equal(ready.calls_used,3);assert.equal(requests.length,3);
  assert.ok((await api('/api/state')).chapters.find(c=>c.id===firstId).body.endsWith(prose));
  assert.equal((await api(`/api/chapter-analysis?chapter_id=${firstId}`)).analyses.length,1);
  await page.locator('[data-chat-result="workbenchAgentSection"]').click();
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
  const row=await createSavedPlan(page,'核对人物动作、因果和章后记忆。','inspect');await stopApp();await startApp();
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  const waiting=await waitStatus(row.id,'awaiting_memory');assert.equal(waiting.calls_used,1);assert.equal(requests.length,4);
  await stopApp();await startApp();await page.reload();await page.waitForFunction(()=>backendOnline);
  await page.locator('[data-view="assistant"]').click();
  const ready=await confirmAgentMemory(page,row.id);assert.equal(ready.calls_used,2);assert.equal(requests.length,5);
 });
 await test('已启动任务停止后不追加调用，迟到内容不进入正文',async()=>{
  delay=1500;const before=(await api('/api/state')).chapters[0].body;
  const row=await createSavedPlan(page,'停止任务的本机验证。');
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  await until(()=>requests.length===6,'provider entered');
  const current=await session(row.id);await api(`/api/writing-agent/${row.id}/resume`,'POST',{action:'cancel',checkpoint_id:current.checkpoint_id});
  await pause(1800);assert.equal((await session(row.id)).status,'canceled');assert.equal(requests.length,6);
  assert.equal((await api('/api/state')).chapters[0].body,before);delay=80;
 });
 await test('模型连接断开后保留任务，不自动重发',async()=>{
  fault='unknown';const row=await createSavedPlan(page,'连接中断的本机验证。');
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="authorize"]`).click();
  const stopped=await waitStatus(row.id,'requires_action');assert.equal(requests.length,7);assert.equal(stopped.calls_used,1);
  await api(`/api/writing-agent/${row.id}/resume`,'POST',{action:'advance',checkpoint_id:stopped.checkpoint_id});
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();await pause(300);
  assert.equal(requests.length,7);fault='';
 });
 // Message draft races and retry identity are covered by agent_chat_browser_regressions.cjs.
 await test('紧凑电脑宽度和长文本无溢出，页面没有脚本错误',async()=>{
  await page.setViewportSize({width:1280,height:900});await page.locator('#agentChatInput').fill('长创作要求'.repeat(100));
  await screenshot(page,'writing-agent-1280x900.png');
  const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(dimensions.scroll<=dimensions.width);
  assert.deepEqual(report.page_errors,[]);
 });
 await test('1280电脑正文、资料和总管仍保持三栏，当前稿改动使依据过期',async()=>{
  await page.locator('[data-view="editor"]').click();await page.locator('[data-chat-result="workbenchContextSection"]').click();
  await page.locator('#workbenchContext [data-context-state]').waitFor();
  const before=await page.locator('#chapterBody').inputValue();await page.locator('#chapterBody').fill(before+'\n\n她没有越过那道门槛。');
  assert.match(await page.locator('#workbenchContext [data-context-state]').textContent(),/已变化/);
  await page.locator('[data-workbench-context-refresh]').click();
  await until(()=>page.locator('#workbenchContext [data-context-state]').textContent().then(value=>value.includes('已保存稿件资料')),'updated context');
  const dimensions=await page.evaluate(()=>{
   const editor=document.querySelector('.workbench-manuscript').getBoundingClientRect(),agent=document.querySelector('.agent-chat').getBoundingClientRect();
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
  const row=(await api('/api/writing-agent','POST',{request_id:crypto.randomUUID(),chapter_id:firstId,
   query:'整章修订，保留门外冲突和人物视角。',mode:'polish'})).session;
  await page.evaluate(()=>authorLoadAgent());await page.locator('[data-chat-result="workbenchAgentSection"]').click();
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
  await page.locator('[data-apply]').click();const ready=await confirmAgentMemory(page,row.id);
  assert.equal(ready.calls_used,3);assert.equal(requests.length,beforeCalls+3);
  assert.equal((await api('/api/state')).chapters.find(c=>c.id===firstId).body,prose);
  await screenshot(page,'writing-agent-polish-1280x900.png');
 });
 let planningSession;
 await test('全书规划任务刷新可恢复，不依赖当前打开的章节',async()=>{
  const beforeCalls=requests.length;
  planningSession=await createSavedPlan(page,'林岚追查铜钥匙的来历，从旧门冲突展开悬疑长篇。','plan');
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  assert.equal(planningSession.max_calls,4);assert.equal(requests.length,beforeCalls);
  await page.locator('[data-view="editor"]').click();
  await page.locator('[data-chat-result="workbenchAgentSection"]').click();
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
  const row=await createSavedPlan(page,'根据当前蓝图继续规划。','plan'),beforeCalls=requests.length;
  const current=await api('/api/blueprint');current.blueprint.premise+=' 后来的作者修改。';
  await api('/api/blueprint','PUT',{blueprint:current.blueprint,expected_version:current.version});
  await page.reload();await page.waitForFunction(()=>backendOnline);await page.locator('[data-view="assistant"]').click();
  const section=page.locator(`[data-agent-session="${row.id}"]`);await section.locator('.agent-source-stale').waitFor();
  assert.match(await section.locator('.agent-source-stale').textContent(),/创作规划已变化/);
  await section.locator('[data-agent-action="authorize"]').click();await waitStatus(row.id,'requires_action');
  assert.equal(requests.length,beforeCalls);
  for(const status of ['awaiting_authorization','awaiting_job','awaiting_adoption','awaiting_memory','awaiting_plan_confirmation','awaiting_plan_application','review_ready']){
   await page.evaluate(({id,status})=>{
    clearTimeout(authorAgentTimer);const row=authorAgentRows.find(item=>item.id===id);
    authorAgentRows=[{...row,status,restored_read_only:true}];authorRenderAgent(authorAgentScope(),document.querySelector('#agentSessions'));
   },{id:row.id,status});
   assert.equal(await page.locator('[data-agent-session] button').count(),0);
  }
  assert.deepEqual(report.page_errors,[]);
 });
 for(const viewport of [{width:1440,height:1000},{width:1280,height:900}]){
  await test(`${viewport.width}电脑章后逐项决定、部分保存和拒绝均绑定本次资料，处理后才审稿`,async()=>{
   await page.setViewportSize(viewport);richAnalysis=true;
   const beforeCalls=requests.length,{owner,chapterId,row}=await createMemoryProject(page,viewport.width);
   const waiting=await waitStatus(row.id,'awaiting_memory',owner),analysisId=waiting.result_refs.chapter_analysis.id;
   assert.equal(waiting.stage,'memory_confirmation');assert.equal(waiting.calls_used,1);
   assert.equal(requests.length,beforeCalls+1);
   assert.equal((await api(`/api/story-reviews?chapter_id=${chapterId}`,'GET',undefined,owner)).reviews.length,0);
   let modal=await openAgentMemory(page,row.id,analysisId);
   for(const selector of ['#analysisSummaryDecision','#analysisHandoffDecision','[data-analysis-decision]']){
    assert.equal(await modal.locator(selector).inputValue(),'');
   }
   assert.match(await modal.locator('.analysis-item strong').textContent(),/^事件 · /);
   await memoryDecisionLayout(modal);
   await page.locator('.toast.show').waitFor({state:'hidden'});
   await screenshot(page,`writing-agent-memory-waiting-${viewport.width}x${viewport.height}.png`,false);
   await modal.locator('#analysisSummaryDecision').selectOption('accepted');
   await modal.locator('[data-confirm-analysis]').click();await modal.waitFor({state:'hidden'});
   await page.locator('.toast.show').filter({hasText:'决定已保存，还有 2 项待决定'}).waitFor();
   const partial=await session(row.id,owner);
   assert.equal(partial.status,'awaiting_memory');assert.equal(partial.calls_used,1);
   assert.equal(requests.length,beforeCalls+1);
   assert.equal((await api(`/api/story-reviews?chapter_id=${chapterId}`,'GET',undefined,owner)).reviews.length,0);
   modal=await openAgentMemory(page,row.id,analysisId);
   assert.equal(await modal.locator('#analysisSummaryDecision').inputValue(),'accepted');
   assert.equal(await modal.locator('#analysisSummaryDecision').isDisabled(),true);
   assert.equal(await modal.locator('#analysisSummary').getAttribute('readonly'),'');
   assert.equal(await modal.locator('#analysisHandoffDecision').inputValue(),'');
   assert.equal(await modal.locator('[data-analysis-decision]').inputValue(),'');
   await memoryDecisionLayout(modal);
   await page.locator('.toast.show').waitFor({state:'hidden'});
   await screenshot(page,`writing-agent-memory-partial-${viewport.width}x${viewport.height}.png`,false);
   await modal.locator('[data-close]').click();await modal.waitFor({state:'hidden'});
   const newerJob=await api('/api/ai/queue','POST',{
    chapter_id:chapterId,task:'chapter_analysis',response_format:'json',defer:true},owner);
   const newer=await api(`/api/ai/jobs/${newerJob.id}/process`,'POST',{},owner);
   assert.equal(newer.status,'success',JSON.stringify(newer));assert.notEqual(newer.result.analysis.id,analysisId);
   const listed=(await api(`/api/chapter-analysis?chapter_id=${chapterId}`,'GET',undefined,owner)).analyses;
   assert.equal(listed[0].id,newer.result.analysis.id);assert.equal(requests.length,beforeCalls+2);
   modal=await openAgentMemory(page,row.id,analysisId);
   assert.equal(await modal.locator('#analysisSummaryDecision').isDisabled(),true);
   await modal.locator('#analysisHandoffDecision').selectOption('rejected');
   await modal.locator('[data-analysis-decision]').selectOption('rejected');
   await modal.locator('[data-confirm-analysis]').click();await modal.waitFor({state:'hidden'});
   await page.locator('.toast.show').filter({hasText:'章后资料已处理'}).waitFor();
   const complete=await waitStatus(row.id,'review_ready',owner);
   assert.equal(complete.calls_used,2);assert.equal(requests.length,beforeCalls+3);
   const analyses=(await api(`/api/chapter-analysis?chapter_id=${chapterId}`,'GET',undefined,owner)).analyses;
   const original=analyses.find(item=>item.id===analysisId);
   assert.equal(original.status,'reviewed');assert.equal(original.summary.status,'accepted');
   assert.equal(original.handoff.status,'rejected');assert.equal(original.candidates[0].status,'rejected');
   assert.equal(analyses.find(item=>item.id===newer.result.analysis.id).status,'pending');
   const saved=await api('/api/state','GET',undefined,owner),savedChapter=saved.chapters.find(item=>item.id===chapterId);
   assert.equal(savedChapter.recap,'林岚仍在门外。');assert.equal(savedChapter.next_brief,'');
   assert.equal(saved.memory.filter(item=>item.status==='已确认').length,0);
   assert.equal((await api(`/api/story-reviews?chapter_id=${chapterId}`,'GET',undefined,owner)).reviews.length,1);
   modal=await openAgentMemory(page,row.id,analysisId);
   for(const selector of ['#analysisSummaryDecision','#analysisHandoffDecision','[data-analysis-decision]']){
    assert.equal(await modal.locator(selector).isDisabled(),true);
   }
   assert.equal(await modal.locator('#analysisHandoffDecision').inputValue(),'rejected');
   assert.equal(await modal.locator('[data-analysis-decision]').inputValue(),'rejected');
   assert.equal(await modal.locator('[data-analysis-name]').getAttribute('readonly'),'');
   assert.equal(await modal.locator('[data-analysis-edit]').getAttribute('readonly'),'');
   assert.equal(await modal.locator('[data-analysis-mode]').isDisabled(),true);
   assert.equal(await modal.locator('[data-analysis-known]').isDisabled(),true);
   assert.equal(await modal.locator('.analysis-actions span').textContent(),'未采用');
   await modal.locator('[data-analysis-decision]').scrollIntoViewIfNeeded();await memoryDecisionLayout(modal);
   await page.locator('.toast.show').waitFor({state:'hidden'});
   await screenshot(page,`writing-agent-memory-rejected-${viewport.width}x${viewport.height}.png`,false);
   await modal.locator('[data-close]').click();richAnalysis=false;assert.deepEqual(report.page_errors,[]);
  });
 }
 await test('等待章后决定时可从页面停止，后续手动处理资料不再触发该任务审稿',async()=>{
  richAnalysis=true;const {owner,chapterId,row}=await createMemoryProject(page,'停止');
  const waiting=await waitStatus(row.id,'awaiting_memory',owner),beforeCalls=requests.length;
  await page.evaluate(()=>authorLoadAgent());
  await page.locator(`[data-agent-session="${row.id}"] [data-agent-action="cancel"]`).click();
  const canceled=await waitStatus(row.id,'canceled',owner);assert.equal(canceled.calls_used,1);
  await page.locator('[data-action="chapterAnalysis"]').click();
  const modal=page.locator('[data-author-chapter-report="analysis"]');await modal.waitFor();
  assert.equal(await modal.getAttribute('data-analysis-id'),waiting.result_refs.chapter_analysis.id);
  await modal.locator('#analysisSummaryDecision').selectOption('rejected');
  await modal.locator('#analysisHandoffDecision').selectOption('rejected');
  await modal.locator('[data-analysis-decision]').selectOption('rejected');
  await modal.locator('[data-confirm-analysis]').click();await modal.waitFor({state:'hidden'});
  await page.locator('.toast.show').filter({hasText:'章后资料已处理'}).waitFor();
  await pause(300);assert.equal((await session(row.id,owner)).status,'canceled');
  assert.equal(requests.length,beforeCalls);
  assert.equal((await api(`/api/story-reviews?chapter_id=${chapterId}`,'GET',undefined,owner)).reviews.length,0);
  richAnalysis=false;await page.evaluate(()=>authorSwitchProject('default-project'));
  await page.locator('[data-view="editor"]').click();assert.deepEqual(report.page_errors,[]);
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
