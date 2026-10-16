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
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-browser-regression-'));
const results={passed:false,scope:'真实浏览器、隔离SQLite、免费本机模拟提供方',cases:[],artifacts:temporary};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,provider,browser,base,providerText='来源甲章的候选。',providerReasoning=null,providerDelay=600,providerStreamGate=null,reviewMode='single',planningFailure=false;
let providerFinishReason='stop',providerOmitDone=false;
let serverLog='',providerPort;
const providerRequests=[];
async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',body,project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);return data;
}
async function currentBody(id,project){const data=await api('/api/state','GET',undefined,project);return data.chapters.find(c=>c.id===id)?.body}
async function waitSaved(page){
 try{await page.waitForFunction(()=>backendOnline&&confirmedState&&!isDirty()&&!syncInFlight&&!independentWrite&&!adopting&&!saveError&&!mergePending,null,{timeout:15000})}
 catch(error){
  const details=await page.evaluate(()=>({backendOnline,dirty:isDirty(),syncInFlight,independentWrite,adopting,saveError,merge:mergePending?.conflicts,
   stateVersion:state.version,confirmedVersion:confirmedState?.version,status:document.querySelector('#saveState')?.innerText}));
  throw new Error(`${error.message}; save details: ${JSON.stringify(details)}`);
 }
}
async function open(viewport={width:1440,height:1000}){
 const context=await browser.newContext({viewport});const page=await context.newPage();const errors=[];
 page.on('pageerror',error=>errors.push(error.message));await page.goto(base);
 try{await page.waitForFunction(()=>backendOnline===true)}
 catch(error){throw new Error(`${error.message}; page errors: ${errors.join('; ')}; save status: ${await page.locator('#saveState').innerText()}`)}
 await page.locator('[data-view="editor"]').click();
 await page.locator('[data-chat-result="workbenchCandidateSection"]').click();
 return {context,page,errors};
}
async function test(name,fn){
 console.log(JSON.stringify({test:name,state:'running'}));
 await fn();results.cases.push({name,passed:true});
 console.log(JSON.stringify({test:name,state:'passed'}));
}
function providerOutput(input){
 const system=input.messages?.[0]?.content||'';
 const user=input.messages?.find(message=>message.role==='user')?.content||'';
 let material={};
 if(user.includes('<manuscript>'))material=JSON.parse(user.split('<manuscript>\n')[1].split('\n</manuscript>')[0]);
 if(material.planning_workflow){
  const stage=material.planning_workflow.stage,b=material.blueprint;
  if(stage==='overview')return JSON.stringify({premise:b.premise,genre:b.genre||'悬疑',audience:'长篇小说读者',tone:'克制',contract:'保持人物认知边界。'});
  if(stage==='volumes')return JSON.stringify({volumes:b.volumes.map((v,index)=>({id:v.id,title:`第${index+1}卷 雪路`,summary:'邮差循着封缄寻找旧驿站。'}))});
  if(stage==='chapters'&&planningFailure){planningFailure=false;return JSON.stringify({volumes:[]})}
  if(stage==='chapters')return JSON.stringify({volumes:b.volumes.map(v=>({id:v.id,chapters:v.chapters.map((c,index)=>({
   id:c.id,title:`第${index+1}章 封缄`,goal:'找到寄信人',pov:'邮差',hook:'蜡印露出半个字。'}))}))});
  return JSON.stringify({volumes:b.volumes.map(v=>({id:v.id,chapters:v.chapters.map(c=>({id:c.id,scenes:c.scenes.map((s,index)=>({
   id:s.id,title:`场景${index+1} 驿站冲突`,goal:'取回包裹',location:'驿站',pov:'邮差'}))}))}))});
 }
 const current=material.current_chapter||{};const quote=String(current.body||'').split(/\r?\n/).find(line=>line.trim())||'';
 if(system.includes('完整普通正文'))return JSON.stringify({
  summary:{text:'林岚把红铜罗盘交给守灯人。',evidence:[{source_quote:quote}]},
  handoff:{text:'下一章等待守灯人归还罗盘。',evidence:[{source_quote:quote}]},
  candidates:[{kind:'event',name:'交出罗盘',note:'林岚把红铜罗盘交给守灯人。',source_quote:quote,epistemic_mode:'world_truth',known_by:['browser_lan','browser_keeper']}]});
 if(system.includes('人物动机、因果、时间')&&reviewMode==='multiple'){
  const body=String(current.body||'');const repeated='林岚抬头。';const second=Array.from(body.slice(0,body.lastIndexOf(repeated))).length;
  return JSON.stringify({findings:[
   {category:'motivation',severity:'possible',message:'交出罗盘与转身离开的动机需要衔接。',suggestion:'用具体动作承接选择。',
    evidence:[{source_chapter_id:current.id,source_quote:'林岚把红铜罗盘交给守灯人。'},
     {source_chapter_id:current.id,source_quote:'她转身走向空船。'}]},
   {category:'style',severity:'suggestion',message:'第二次抬头可以换成具体动作。',suggestion:'保留后面的钟声。',
    evidence:[{source_chapter_id:current.id,source_quote:repeated,source_start:second,source_end:second+Array.from(repeated).length}]}
  ],uncertainties:['本机模拟仅验证审稿流程。']});
 }
 if(system.includes('人物动机、因果、时间'))return JSON.stringify({
  findings:[{category:'motivation',severity:'possible',message:'可以补充交出罗盘的动机。',suggestion:'让她先看一眼空船。',
   evidence:[{source_chapter_id:current.id,source_quote:quote}]}],uncertainties:['模拟模型只用于验证作者处理流程。']});
 if(system.includes('可写的场景计划'))return JSON.stringify({pov:'林岚',location:'北塔',goal:'找回红铜罗盘',obstacle:'守门人堵住出口',exit:'灯灭了'});
 if(system.includes('规划下一步故事'))return JSON.stringify({goal:'找回罗盘',conflict:'守门人封锁北塔',turn:'潮声突然停止',scenes:['塔门争执'],foreshadowing:['熄灭的灯']});
 if(system.includes('总结'))return JSON.stringify({events:['林岚交出罗盘'],state_changes:['守灯人持有罗盘'],open_threads:['子夜归还罗盘']});
 if(system.includes('相邻章节的矛盾'))return JSON.stringify({findings:['罗盘去向需交代'],evidence:[quote],uncertainties:['模拟结果']});
 return providerText;
}
async function main(){
 providerPort=await freePort();
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',()=>{
   const input=JSON.parse(raw||'{}');providerRequests.push(input);const text=providerOutput(input);
   const finishReason=providerFinishReason,omitDone=providerOmitDone;
   if(input.stream){
    response.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
    if(providerReasoning!==null)response.write(`data: ${JSON.stringify({choices:[{delta:{reasoning_content:providerReasoning},finish_reason:null}]})}\n\n`);
    const middle=Math.max(1,Math.floor(text.length/2));
    response.write(`data: ${JSON.stringify({choices:[{delta:{content:text.slice(0,middle)},finish_reason:null}]})}\n\n`);
    const streamGate=providerStreamGate;
    setTimeout(async()=>{
     if(streamGate)await streamGate;
     if(response.destroyed)return;
     response.write(`data: ${JSON.stringify({choices:[{delta:{content:text.slice(middle)},finish_reason:finishReason}],usage:{prompt_tokens:120,completion_tokens:35,total_tokens:155}})}\n\n`);
     response.end(omitDone?'':'data: [DONE]\n\n');
    },providerDelay);
   }else{
    response.writeHead(200,{'Content-Type':'application/json'});
    const message={content:text};if(providerReasoning!==null)message.reasoning_content=providerReasoning;
    setTimeout(()=>response.end(JSON.stringify({model:'browser-mock',choices:[{message,finish_reason:finishReason}],
     usage:{prompt_tokens:120,completion_tokens:35,total_tokens:155}})),providerDelay);
   }
  });
 });
 await new Promise(resolve=>provider.listen(providerPort,'127.0.0.1',resolve));
 const appPort=await freePort();base=`http://127.0.0.1:${appPort}`;
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,
  env:{...process.env,MOYU_DB:path.join(temporary,'test.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(appPort),
   MOYU_AI_BASE_URL:`http://127.0.0.1:${providerPort}/v1`,MOYU_AI_MODEL:'browser-mock',MOYU_AI_API_KEY:'',
   MOYU_AI_DAILY_QUOTA:'100',MOYU_SECRET_KEY:'',MOYU_BACKUP_DIR:path.join(temporary,'backups'),
   MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let n=0;n<200;n++){try{if((await fetch(base+'/api/health')).ok)break}catch{}if(app.exitCode!==null)throw new Error('server exited: '+serverLog.slice(-2500));if(n===199)throw new Error('server unavailable: '+serverLog.slice(-2500));await pause(40)}
 const seeded=await api('/api/state');const firstId=seeded.chapters[0].id;
 seeded.project.title='浏览器隔离测试作品';seeded.project.summary='灯塔守门人追查遗失的钥匙。';
 seeded.chapters[0].body='林岚😀把钥匙藏在左靴。她走到门口，听见灯塔钟声。'.repeat(4);
 seeded.chapters[0].chapter_goal='守住钥匙';seeded.chapters[0].scene_plan='门口遇见守门人';seeded.chapters[0].pov='林岚';
 seeded.records.push({id:'browser_lan',type:'人物',name:'林岚',description:'她在渡口长大。',aliases:['阿岚']},
  {id:'browser_keeper',type:'人物',name:'守灯人',description:'负责北塔夜航灯。'});
 seeded.chapters.push({id:'browser_second',title:'第二章',body:'另一章的原稿，不接收第一章候选。',status:'草稿'});
 await api('/api/state','PUT',seeded);
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});

 const desktop=await open();
 await test('慢保存继续输入，新文字落库且版本连续推进',async()=>{
  let held,ready;const observed=new Promise(resolve=>ready=resolve);const versions=[];
  await desktop.page.route('**/api/state',async route=>{
   if(route.request().method()!=='PUT')return route.continue();
   versions.push(route.request().postDataJSON().version);
   if(!held){const response=await route.fetch();held={route,response};ready();return}
   await route.continue();
  });
  const text=await desktop.page.locator('#chapterBody').inputValue();
  await desktop.page.locator('#chapterBody').fill(text+'\n第一次输入。');await observed;
  await desktop.page.locator('#chapterBody').fill(text+'\n第一次输入。\n保存等待继续写。');
  await desktop.page.locator('details.chapter-plan > summary').click();
  await desktop.page.locator('#chapterGoal').fill('保存等待期间作者更新的本章目标。');
  await held.route.fulfill({response:held.response});await waitSaved(desktop.page);
  assert.match(await currentBody(firstId),/保存等待继续写/);
  const persisted=(await api('/api/state')).chapters.find(item=>item.id===firstId);
  assert.equal(persisted.chapter_goal,'保存等待期间作者更新的本章目标。');
  assert.equal(await desktop.page.locator('#chapterGoal').inputValue(),persisted.chapter_goal);
  assert.ok(versions.length>=2);assert.ok(Number(versions[1])>Number(versions[0]));
  await desktop.page.unroute('**/api/state');
 });
 await test('断网不会假报已保存，恢复后保留并保存草稿',async()=>{
  await desktop.page.route('**/api/state',route=>route.request().method()==='PUT'?route.abort('failed'):route.continue());
  const text=await desktop.page.locator('#chapterBody').inputValue();await desktop.page.locator('#chapterBody').fill(text+'\n离线新增。');
  await desktop.page.waitForFunction(()=>backendOnline===false);
  assert.match(await desktop.page.locator('#saveState').innerText(),/连接|草稿|同步/);
  assert.ok(!String(await currentBody(firstId)).includes('离线新增。'));
  await desktop.page.unroute('**/api/state');await desktop.page.evaluate(()=>reconnect());await waitSaved(desktop.page);
  assert.match(await currentBody(firstId),/离线新增/);
 });
 await test('粗体、引用、场景分隔和撤销真正修改并保存正文',async()=>{
  const before=await desktop.page.locator('#chapterBody').inputValue();
  await desktop.page.locator('#chapterBody').evaluate(el=>{el.focus();el.setSelectionRange(0,2)});
  await desktop.page.locator('[data-format="bold"]').click();assert.match(await desktop.page.locator('#chapterBody').inputValue(),/^\*\*林岚\*\*/);
  await desktop.page.locator('[data-editor-undo]').click();assert.equal(await desktop.page.locator('#chapterBody').inputValue(),before);
  await desktop.page.locator('#chapterBody').evaluate(el=>{el.focus();el.setSelectionRange(0,2)});
  await desktop.page.locator('[data-format="quote"]').click();assert.match(await desktop.page.locator('#chapterBody').inputValue(),/^> 林岚/);
  await desktop.page.locator('[data-format="scene"]').click();assert.match(await desktop.page.locator('#chapterBody').inputValue(),/\*\*\*/);await waitSaved(desktop.page);
 });
 await test('请求等待时切章，迟到候选保持来源身份且单次采纳',async()=>{
  providerText='第一章专属候选哨兵。';providerDelay=750;
  const originalSecond=await currentBody('browser_second');
  await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(()=>candidateStore.size>0);
  await desktop.page.locator('[data-chapter="browser_second"]').click();
  await desktop.page.waitForFunction(()=>queueJobs.some(j=>j.status==='success'),null,{timeout:15000});
  assert.equal(await currentBody('browser_second'),originalSecond);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  await desktop.page.waitForFunction(()=>aiDraft?.complete===true);
  await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').dblclick();await waitSaved(desktop.page);
  await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));
  const text=await currentBody(firstId);assert.equal(text.split('第一章专属候选哨兵。').length-1,1);
  assert.equal(await currentBody('browser_second'),originalSecond);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
 });
 await test('emoji选区润色只替换选中范围',async()=>{
  const value='首段保留。😀同一句。第二处：😀同一句。尾段保留。';
  await desktop.page.locator('#chapterBody').fill(value);await waitSaved(desktop.page);
  providerText='改好的第二处。';providerDelay=200;
  await desktop.page.locator('#chapterBody').evaluate(el=>{const start=el.value.lastIndexOf('😀同一句。');el.focus();el.setSelectionRange(start,start+'😀同一句。'.length);el.dispatchEvent(new Event('select'))});
  await desktop.page.locator('#aiTask').selectOption('polish');await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.task==='polish',null,{timeout:15000});
  await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').click();
  await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),'首段保留。😀同一句。第二处：改好的第二处。尾段保留。');
 });
 await test('连续20轮写作、切章、实际生成与单次采纳保持完整',async()=>{
  providerDelay=80;
  const rounds=[];
  for(let round=1;round<=20;round++){
   const sentinel=`第${round}轮原稿😀，铜匙留在左靴。`;
   const candidate=`第${round}轮唯一候选。`;providerText=candidate;
   await desktop.page.waitForFunction(()=>!aiDraft||['accepted','rejected'].includes(aiDraft.decision));
   await desktop.page.locator('#chapterBody').fill(sentinel);await waitSaved(desktop.page);
   await desktop.page.locator('[data-chapter="browser_second"]').click();
   await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
   assert.equal(await desktop.page.locator('#chapterBody').inputValue(),sentinel);
   await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiGenerate').click();
   await desktop.page.waitForFunction(value=>aiDraft?.complete&&aiDraft?.text===value,candidate,{timeout:15000});
   const runId=await desktop.page.evaluate(()=>aiDraft.ai_run_id);
   await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').click();
   await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));await waitSaved(desktop.page);
   assert.equal(await currentBody(firstId),sentinel+'\n\n'+candidate);
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
   rounds.push({round,run_id:runId,body_preserved:true,candidate_count:1});
  }
  results.rounds=rounds;
 });
 await test('已采纳候选在无缓存的新浏览器中不会再次出现可采纳状态',async()=>{
  const fresh=await open();
  await fresh.page.locator(`[data-chapter="${firstId}"]`).click();
  await fresh.page.locator('[data-view="tasks"]').click();
  await fresh.page.waitForFunction(()=>queueJobs.some(job=>job.author_decision==='accepted'));
  const runId=await fresh.page.evaluate(()=>queueJobs.find(job=>job.author_decision==='accepted').ai_run_id);
  const body=await currentBody(firstId);
  await fresh.page.locator(`[data-task-open="${runId}"]`).click();
  await fresh.page.waitForFunction(()=>view==='editor');
  assert.equal(await fresh.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await fresh.page.locator('#aiStatus').innerText(),/已采纳/);
  assert.ok(await fresh.page.locator('#aiOutput').inputValue());
  assert.equal(await fresh.page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
  assert.equal(await fresh.page.locator('#aiClear').isEnabled(),false);
  assert.equal(await currentBody(firstId),body);
  await fresh.context.close();
 });
 await test('结构候选更新场景计划并保持正文与作者决定',async()=>{
  const before=await currentBody(firstId);providerDelay=80;
  await desktop.page.locator('#aiTask').selectOption('scene');await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.task==='scene',null,{timeout:15000});
  const runId=await desktop.page.evaluate(()=>aiDraft.ai_run_id);
  await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').click();
  await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));await waitSaved(desktop.page);
  const snapshot=await api('/api/state');
  assert.equal(snapshot.chapters.find(item=>item.id===firstId).body,before);
  assert.match(snapshot.chapters.find(item=>item.id===firstId).scene_plan,/北塔|红铜罗盘/);
  const jobs=(await api('/api/ai/queue')).jobs;
  assert.equal(jobs.find(job=>job.ai_run_id===runId).author_decision,'accepted');
 });
 await test('真实流式任务停止后刷新仍为已停止且迟到内容不落正文',async()=>{
  const before=await currentBody(firstId);providerDelay=4000;providerText='取消任务不准进入正文。';
  await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(()=>aiDraft?.job_id&&!aiDraft.complete&&aiDraft.text,null,{timeout:15000});
  const {jobId,runId}=await desktop.page.evaluate(()=>({jobId:aiDraft.job_id,runId:aiDraft.ai_run_id}));
  assert.equal(await desktop.page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
  await desktop.page.locator('#aiClear').click();
  await desktop.page.waitForFunction(({jobId,runId})=>queueJobs.some(job=>job.id===jobId&&job.status==='canceled')&&
   candidateStore.get(runId)?.text&&(candidateStore.get(runId).terminal_snapshot||candidateStore.get(runId).terminal_replay_complete),
   {jobId,runId},{timeout:15000});
  const partial=await desktop.page.evaluate(id=>candidateStore.get(id).text,runId);
  await desktop.page.reload();await desktop.page.waitForFunction(()=>backendOnline);
  await desktop.page.locator('[data-view="tasks"]').click();
  await desktop.page.waitForFunction(value=>queueJobs.some(job=>job.id===value&&job.status==='canceled'),jobId);
  assert.equal(await currentBody(firstId),before);
  assert.equal(await desktop.page.locator(`[data-task-cancel="${jobId}"]`).count(),0);
  assert.equal(await desktop.page.locator(`[data-task-retry="${jobId}"]`).count(),0);
  await desktop.page.locator(`[data-task-open="${runId}"]`).click();
  assert.equal(await desktop.page.locator('#aiOutput').inputValue(),partial);
  assert.equal(await desktop.page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.equal(await desktop.page.locator('#aiRevise').isEnabled(),false);
  assert.equal(await desktop.page.locator('#aiClear').isEnabled(),false);
  assert.equal(await desktop.page.locator('#aiCopy').isEnabled(),true);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/已停止.*仅供阅读和复制/);
  const fresh=await open(),calls=providerRequests.length;
  await fresh.page.locator('[data-view="tasks"]').click();
  await fresh.page.locator(`[data-task-open="${runId}"]`).click();
  await fresh.page.waitForFunction(text=>aiDraft?.text===text,partial);
  assert.equal(await fresh.page.locator('#aiOutput').inputValue(),partial);
  assert.equal(await fresh.page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
  await fresh.page.evaluate(async()=>{
   const output=document.querySelector('#aiOutput');output.value='不允许修改已停止片段';output.dispatchEvent(new Event('input'));
   await clearAI();await reviseAI();await adoptAI();
  });
  assert.equal(await fresh.page.locator('#aiOutput').inputValue(),partial);
  assert.equal(await fresh.page.locator('[data-apply],[data-revise-dialog]').count(),0);
  assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),before);
  assert.deepEqual(fresh.errors,[]);await fresh.context.close();providerDelay=80;
 });
 await test('截断任务在无缓存窗口完整恢复只读片段，已知用量可手动重试',async()=>{
  const before=await currentBody(firstId),text='截断前收到的内容，只能阅读，不准进入原稿。';
  providerText=text;providerFinishReason='length';providerDelay=80;
  try{
   await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiGenerate').click();
   await desktop.page.waitForFunction(value=>aiDraft?.text===value&&aiDraft.job_status==='failed',text,{timeout:15000});
   const {runId,jobId}=await desktop.page.evaluate(()=>({runId:aiDraft.ai_run_id,jobId:aiDraft.job_id}));
   const calls=providerRequests.length,fresh=await open();
   await fresh.page.locator('[data-view="tasks"]').click();
   await fresh.page.locator(`[data-task-open="${runId}"]`).waitFor();
   assert.equal(await fresh.page.locator(`[data-task-open="${runId}"]`).innerText(),'查看片段');
   assert.equal(await fresh.page.locator(`[data-task-retry="${jobId}"]`).count(),1);
   await fresh.page.locator(`[data-task-open="${runId}"]`).click();
   assert.equal(await fresh.page.locator('#aiOutput').inputValue(),text);
   assert.equal(await fresh.page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
   assert.equal(await fresh.page.locator('#aiAdopt').isEnabled(),false);
   assert.equal(await fresh.page.locator('#aiRevise').isEnabled(),false);
   assert.equal(await fresh.page.locator('#aiClear').isEnabled(),false);
   assert.equal(await fresh.page.locator('#aiCopy').isEnabled(),true);
   assert.equal(await fresh.page.evaluate(()=>aiDraft.complete),false);
   assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),before);
   assert.deepEqual(fresh.errors,[]);await fresh.context.close();
  }finally{providerFinishReason='stop'}
 });
 await test('缺少流式结束标记的未知结果可回看片段，不显示重发且拒绝过期来源',async()=>{
  const before=await currentBody(firstId),text='结束状态未知的完整可见片段。保留钥匙，不写入原稿。';
  providerText=text;providerOmitDone=true;providerDelay=80;
  try{
   await desktop.page.locator('#aiGenerate').click();
   await desktop.page.waitForFunction(value=>aiDraft?.text===value&&aiDraft.job_status==='failed'&&
    aiDraft.completion_state==='unknown',text,{timeout:15000});
   const {runId,jobId}=await desktop.page.evaluate(()=>({runId:aiDraft.ai_run_id,jobId:aiDraft.job_id}));
   const calls=providerRequests.length,fresh=await open();
   await fresh.page.locator('[data-view="tasks"]').click();
   await fresh.page.locator(`[data-task-open="${runId}"]`).waitFor();
   assert.equal(await fresh.page.locator(`[data-task-retry="${jobId}"]`).count(),0);
   await fresh.page.screenshot({path:path.join(temporary,'desktop-interrupted-task.png'),fullPage:true});
   await fresh.page.locator(`[data-task-open="${runId}"]`).click();
   assert.equal(await fresh.page.locator('#aiOutput').inputValue(),text);
   assert.equal(await fresh.page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
   for(const id of ['aiAdopt','aiRevise','aiClear'])assert.equal(await fresh.page.locator('#'+id).isEnabled(),false);
   assert.match(await fresh.page.locator('#aiStatus').innerText(),/执行结果未知.*仅供阅读和复制/);
   await fresh.page.screenshot({path:path.join(temporary,'desktop-interrupted-fragment.png'),fullPage:true});
   await fresh.context.grantPermissions(['clipboard-read','clipboard-write']);
   await fresh.page.locator('#aiCopy').click();
   assert.equal(await fresh.page.evaluate(()=>navigator.clipboard.readText()),text);
   await fresh.page.locator('[data-view="tasks"]').click();
   const source=await fresh.page.evaluate(id=>({...candidateStore.get(id).source}),runId);
   await fresh.page.evaluate(id=>{
    candidateStore.get(id).source={...candidateStore.get(id).source,project_id:'stale-project'};
    document.querySelector(`[data-task-open="${id}"]`).click();
   },runId);
   assert.equal(await fresh.page.evaluate(()=>view),'tasks');
   assert.match(await fresh.page.locator('#toast').innerText(),/来源作品/);
   await fresh.page.evaluate(({id,source})=>{
    candidateStore.get(id).source={...source,chapter_id:'deleted-chapter'};
    document.querySelector(`[data-task-open="${id}"]`).click();
   },{id:runId,source});
   assert.equal(await fresh.page.evaluate(()=>view),'tasks');
   assert.match(await fresh.page.locator('#toast').innerText(),/章节已不存在/);
   await fresh.page.evaluate(({id,source})=>{candidateStore.get(id).source=source},{id:runId,source});
   assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),before);
   assert.deepEqual(fresh.errors,[]);await fresh.context.close();
   results.unknown_fragment={run_id:runId,job_id:jobId,text};
  }finally{providerOmitDone=false}
 });
 await test('只有本机分片账本时只读恢复未知结果，读取中断可续读且不会再次调用模型',async()=>{
  const {run_id:runId,job_id:jobId,text}=results.unknown_fragment;
  const before=await currentBody(firstId),calls=providerRequests.length,context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const queueRoute=/\/api\/ai\/queue(?:\?|$)/,streamRoute=new RegExp(`/api/ai/runs/${runId}/stream(?:\\?|$)`);
  let reads=0,readFailed;const failed=new Promise(resolve=>readFailed=resolve);
  await page.route(queueRoute,async route=>{
   const response=await route.fetch(),data=await response.json();
   for(const job of data.jobs||[])if(job.ai_run_id===runId){
    delete job.result.text;delete job.result.structured;delete job.result.completion_state;
   }
   await route.fulfill({response,json:data});
  });
  await page.route(streamRoute,async route=>{
   reads++;
   if(reads===1){await route.abort('failed');readFailed();return}
   return route.continue();
  });
  await page.goto(base);await page.waitForFunction(()=>backendOnline);await failed;
  await page.waitForFunction(id=>!streamReaders.has(id)&&!queueLoading,jobId);
  assert.equal(await page.evaluate(id=>!!candidateStore.get(id).terminal_replay_complete,runId),false);
  await page.evaluate(()=>refreshQueue());
  await page.waitForFunction(id=>candidateStore.get(id)?.terminal_replay_complete,runId,{timeout:15000});
  assert.equal(reads,2);
  await page.locator('[data-view="tasks"]').click();
  assert.equal(await page.locator(`[data-task-retry="${jobId}"]`).count(),0);
  await page.locator(`[data-task-open="${runId}"]`).click();
  assert.equal(await page.locator('#aiOutput').inputValue(),text);
  assert.equal(await page.locator('#aiOutput').evaluate(el=>el.readOnly),true);
  assert.equal(await page.locator('#aiAdopt').isEnabled(),false);
  assert.equal(await page.locator('#aiRevise').isEnabled(),false);
  assert.equal(await page.evaluate(()=>aiDraft.complete),false);
  assert.match(await page.locator('#aiStatus').innerText(),/执行结果未知.*仅供阅读和复制/);
  await page.waitForFunction(()=>!queueLoading);await page.evaluate(()=>refreshQueue());
  assert.equal(reads,2);assert.equal(await page.locator('#aiOutput').inputValue(),text);
  assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),before);
  assert.deepEqual(errors,[]);await context.close();
 });
 await test('未完成的结构分析和JSON正文保留原始片段，只读复制且无法整理或采纳',async()=>{
  const before=await currentBody(firstId),calls=providerRequests.length;
  await desktop.page.evaluate(()=>{
   const source={...aiDraft.source};
   for(const [task,text] of [['chapter_analysis','{"summary":"未完成的分析片段'],['continue','{"content":"未完成的正文片段']]){
    const id=`browser_fragment_${task}`;
    const draft=receiveCandidateJob({id,ai_run_id:id,task,source,status:'failed',error:'回复被截断',
     result:{response_format:'json',text,finish_reason:'length',safe_to_apply:false,billing_state:'usage_reported'}});
    displayCandidate(draft);
    if(document.querySelector('#aiOutput').value!==text||!document.querySelector('#aiOutput').readOnly||
     document.querySelector('#aiCopy').disabled||!document.querySelector('#aiAdopt').disabled||!document.querySelector('#aiClear').disabled)
     throw new Error('未完成的结构回复没有完整保留只读片段');
    candidateStore.delete(id);
   }
   displayCandidate(activeCandidate());persistCandidates();
  });
  assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),before);
 });
 await test('长章无选区润色不会全文覆盖',async()=>{
  const value='不可删除章首。'+'完整正文。'.repeat(2600);
  await desktop.page.locator('#chapterBody').fill(value);await waitSaved(desktop.page);
  await desktop.page.locator('#chapterBody').evaluate(el=>{el.focus();el.setSelectionRange(0,0);el.dispatchEvent(new Event('select'))});
  await desktop.page.locator('#aiTask').selectOption('polish');const jobsBefore=(await api('/api/ai/queue')).jobs.length;
  await desktop.page.locator('#aiGenerate').click();await pause(200);
  assert.match(await desktop.page.locator('#toast').innerText(),/先选中/);
  assert.equal((await api('/api/ai/queue')).jobs.length,jobsBefore);assert.equal(await currentBody(firstId),value);
 });
 await test('两窗口同句冲突展示双方并可手工合并',async()=>{
  await desktop.page.locator('#chapterBody').fill('同一段基础正文。');await waitSaved(desktop.page);
  const other=await open();await desktop.page.locator('#chapterBody').fill('本页把灯交给林岚。');await waitSaved(desktop.page);
  await other.page.locator('#chapterBody').fill('另页把灯交给守门人。');
  await other.page.locator('[data-merge-dialog]').waitFor();
  const content=await other.page.locator('[data-merge-dialog]').innerText();assert.match(content,/本机已保存稿/);
  const boxes=await other.page.locator('.conflict-columns textarea').allTextContents();assert.ok(boxes.some(x=>x.includes('林岚')));assert.ok(boxes.some(x=>x.includes('守门人')));
  await other.page.locator('[data-merge-choice]').selectOption('manual');await other.page.locator('[data-merge-text]').fill('合并：林岚先接过灯，再交给守门人。');
  await other.page.locator('[data-merge]').click();await waitSaved(other.page);assert.equal(await currentBody(firstId),'合并：林岚先接过灯，再交给守门人。');await other.context.close();
  await desktop.page.reload();await desktop.page.waitForFunction(()=>backendOnline);
  await desktop.page.locator('[data-view="editor"]').click();await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),'合并：林岚先接过灯，再交给守门人。');
 });
 await test('两窗口非重叠改稿同步可见正文，保留emoji选区并继续写而不丢远端段',async()=>{
  const original='😀本页第一段。\n第二段保持原样。\n最后一段。';
  const prefix='另一页新增首段。\n',local=original.replace('最后一段。','本页改好的最后一段。');
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  const other=await open();await other.page.locator('#chapterBody').fill(prefix+original);await waitSaved(other.page);
  let held,ready;const observed=new Promise(resolve=>ready=resolve);
  await desktop.page.route('**/api/state',async route=>{
   if(route.request().method()!=='GET'||held)return route.continue();
   const response=await route.fetch();held={route,response};ready();
  });
  await desktop.page.locator('#chapterBody').fill(local);await observed;
  await desktop.page.locator('#chapterBody').evaluate(input=>{input.focus();input.setSelectionRange(0,2,'backward')});
  await held.route.fulfill({response:held.response});await waitSaved(desktop.page);
  const merged=prefix+local;
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),merged);
  assert.equal(await currentBody(firstId),merged);
  const selection=await desktop.page.locator('#chapterBody').evaluate(input=>({
   start:input.selectionStart,end:input.selectionEnd,direction:input.selectionDirection,
   quote:input.value.slice(input.selectionStart,input.selectionEnd),
  }));
  assert.deepEqual(selection,{start:prefix.length,end:prefix.length+2,direction:'backward',quote:'😀'});
  assert.equal(await desktop.page.locator('[data-merge-dialog]').count(),0);
  await desktop.page.keyboard.insertText('本页继续');await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),merged.replace('😀','本页继续'));
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),merged.replace('😀','本页继续'));
  await desktop.page.unroute('**/api/state');await other.context.close();
 });
 await test('两窗口合并后撤销本页改稿并重做，两次都保留另一页新增段落',async()=>{
  const original='😀本页章首。\n中间段保持原样。\n本页旧末尾。';
  const prefix='另一页保留新增段落。\n',local=original.replace('本页旧末尾。','本页新末尾。');
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  const other=await open();await other.page.locator('#chapterBody').fill(prefix+original);await waitSaved(other.page);
  await desktop.page.locator('#chapterBody').fill(local);await waitSaved(desktop.page);
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),prefix+local);
  await desktop.page.locator('[data-editor-undo]').click();await waitSaved(desktop.page);
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),prefix+original);
  assert.equal(await currentBody(firstId),prefix+original);
  await desktop.page.locator('[data-editor-redo]').click();await waitSaved(desktop.page);
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),prefix+local);
  assert.equal(await currentBody(firstId),prefix+local);
  await other.context.close();
 });
 await test('同起点插入与删除双方向显示双方稿件，选择后保存及撤销重做不丢字',async()=>{
  const original='甲😀乙丙。\n另一段保持。',inserted=original.replace('😀','新😀'),deleted=original.replace('😀','');
  const combined=original.replace('😀','新');
  for(const [local,remote] of [[inserted,deleted],[deleted,inserted]]){
   await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
   const writer=await open(),other=await open();
   await other.page.locator('#chapterBody').fill(remote);await waitSaved(other.page);
   await writer.page.locator('#chapterBody').fill(local);
   const dialog=writer.page.locator('[data-merge-dialog]');await dialog.waitFor();
   const drafts=await dialog.locator('.conflict-columns textarea').evaluateAll(inputs=>inputs.map(input=>input.value));
   assert.deepEqual(drafts,[local,remote]);
   assert.equal(await currentBody(firstId),remote);
   await dialog.locator('[data-merge-choice]').selectOption('manual');
   await dialog.locator('[data-merge-text]').fill(combined);
   await dialog.locator('[data-merge]').click();await waitSaved(writer.page);
   assert.equal(await currentBody(firstId),combined);
   if(local===inserted){
    await writer.page.locator('[data-editor-undo]').click();await waitSaved(writer.page);
    assert.equal(await currentBody(firstId),deleted);
    await writer.page.locator('[data-editor-redo]').click();await waitSaved(writer.page);
    assert.equal(await currentBody(firstId),combined);
   }else{
    const history=await writer.page.evaluate(()=>({undo:editorUndo.get(authorEditorHistoryKey(chapter())).length,
     redo:editorRedo.get(authorEditorHistoryKey(chapter())).length,version:state.version}));
    await writer.page.locator('[data-editor-undo]').click();
    assert.match(await writer.page.locator('#toast').innerText(),/撤销位置已被另一窗口修改/);
    assert.equal(await currentBody(firstId),combined);
    assert.equal(await writer.page.locator('#chapterBody').inputValue(),combined);
    assert.deepEqual(await writer.page.evaluate(()=>({undo:editorUndo.get(authorEditorHistoryKey(chapter())).length,
     redo:editorRedo.get(authorEditorHistoryKey(chapter())).length,version:state.version})),history);
    await other.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(other.page);
    await other.page.locator('#chapterBody').fill(deleted);await waitSaved(other.page);
    await writer.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(writer.page);
    await writer.page.locator('[data-editor-undo]').click();await waitSaved(writer.page);
    assert.equal(await currentBody(firstId),original);
    await writer.page.locator('[data-editor-redo]').click();await waitSaved(writer.page);
    assert.equal(await currentBody(firstId),deleted);
   }
   await writer.context.close();await other.context.close();
   await desktop.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(desktop.page);
  }
 });
 await test('重做插入遇到另一页同起点删字时保留正文和重做记录，恢复后可继续重做',async()=>{
  const original='甲😀乙丙。\n另一段保持。',inserted=original.replace('😀','新😀'),deleted=original.replace('😀','');
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  const writer=await open();
  await writer.page.locator('#chapterBody').fill(inserted);await waitSaved(writer.page);
  await writer.page.locator('[data-editor-undo]').click();await waitSaved(writer.page);
  assert.equal(await currentBody(firstId),original);
  const other=await open();await other.page.locator('#chapterBody').fill(deleted);await waitSaved(other.page);
  await writer.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(writer.page);
  const history=await writer.page.evaluate(()=>({undo:editorUndo.get(authorEditorHistoryKey(chapter())).length,
   redo:editorRedo.get(authorEditorHistoryKey(chapter())).length,version:state.version}));
  await writer.page.locator('[data-editor-redo]').click();
  assert.match(await writer.page.locator('#toast').innerText(),/重做位置已被另一窗口修改/);
  assert.equal(await writer.page.locator('#chapterBody').inputValue(),deleted);
  assert.equal(await currentBody(firstId),deleted);
  assert.deepEqual(await writer.page.evaluate(()=>({undo:editorUndo.get(authorEditorHistoryKey(chapter())).length,
   redo:editorRedo.get(authorEditorHistoryKey(chapter())).length,version:state.version})),history);
  await other.page.locator('#chapterBody').fill(original);await waitSaved(other.page);
  await writer.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(writer.page);
  await writer.page.locator('[data-editor-redo]').click();await waitSaved(writer.page);
  assert.equal(await currentBody(firstId),inserted);
  await writer.page.locator('[data-editor-undo]').click();await waitSaved(writer.page);
  assert.equal(await currentBody(firstId),original);
  await writer.context.close();await other.context.close();
  await desktop.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(desktop.page);
 });
 await test('粗体撤销后接收另一页新改稿，快捷键重做和撤销仅变化粗体',async()=>{
  const original='林岚😀站在门前。\n中间段落保持。\n守灯人在楼下。';
  const remoteEnd='守灯人留在楼上。';
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  const other=await open();await other.page.locator('#chapterBody').fill(original+'\n另一页新增钟声。');await waitSaved(other.page);
  await desktop.page.locator('#chapterBody').evaluate(input=>{input.focus();input.setSelectionRange(0,4)});
  await desktop.page.locator('[data-format="bold"]').click();await waitSaved(desktop.page);
  const bold=original.replace('林岚😀','**林岚😀**')+'\n另一页新增钟声。';
  assert.equal(await currentBody(firstId),bold);
  await desktop.page.locator('[data-editor-undo]').click();await waitSaved(desktop.page);
  const undone=original+'\n另一页新增钟声。';assert.equal(await currentBody(firstId),undone);
  await other.page.evaluate(async()=>{receiveState(await readServerState());await syncBackend()});await waitSaved(other.page);
  await other.page.locator('#chapterBody').fill(undone.replace('守灯人在楼下。',remoteEnd));await waitSaved(other.page);
  await desktop.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(desktop.page);
  await desktop.page.locator('#chapterBody').focus();
  const command=process.platform==='darwin'?'Meta':'Control';
  await desktop.page.keyboard.press(`${command}+Shift+z`);await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),bold.replace('守灯人在楼下。',remoteEnd));
  await desktop.page.keyboard.press(`${command}+z`);await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),undone.replace('守灯人在楼下。',remoteEnd));
  assert.equal(await desktop.page.locator('[data-merge-dialog]').count(),0);
  await other.context.close();
 });
 await test('另一页覆盖本页撤销位置时拒绝操作，保留正文和可重试的撤销记录',async()=>{
  const original='林岚把钥匙留在左靴。\n守灯人不动。';
  const local=original.replace('左靴','帽子'),remote=original.replace('左靴','门外');
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  await desktop.page.locator('#chapterBody').fill(local);await waitSaved(desktop.page);
  const other=await open();await other.page.locator('#chapterBody').fill(remote);await waitSaved(other.page);
  await desktop.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(desktop.page);
  const before=await desktop.page.evaluate(()=>({undo:editorUndo.get(authorEditorHistoryKey(chapter())).length,version:state.version}));
  await desktop.page.locator('[data-editor-undo]').click();
  assert.match(await desktop.page.locator('#toast').innerText(),/撤销位置已被另一窗口修改/);
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),remote);
  assert.equal(await currentBody(firstId),remote);
  assert.deepEqual(await desktop.page.evaluate(()=>({undo:editorUndo.get(authorEditorHistoryKey(chapter())).length,version:state.version})),before);
  assert.equal(await desktop.page.locator('[data-editor-redo]').isEnabled(),false);
  await other.page.locator('#chapterBody').fill(local);await waitSaved(other.page);
  await desktop.page.evaluate(async()=>receiveState(await readServerState()));await waitSaved(desktop.page);
  await desktop.page.locator('[data-editor-undo]').click();await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),original);
  await other.context.close();
 });
 await test('真实中文组合输入遇到远端合并保持候选字，确认后两页改稿完整保存',async()=>{
  const original='章首保持。\n输入位置：。\n远端段落。';
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  const other=await open();
  await other.page.locator('#chapterBody').fill(original.replace('远端段落。','另一页保留段落。'));await waitSaved(other.page);
  let held,ready;const observed=new Promise(resolve=>ready=resolve);
  await desktop.page.route('**/api/state',async route=>{
   if(route.request().method()!=='GET'||held)return route.continue();
   const response=await route.fetch();held={route,response};ready();
  });
  await desktop.page.bringToFront();
  await desktop.page.locator('#chapterBody').evaluate(input=>{
   globalThis.browserCompositionEvents=[];
   for(const name of ['compositionstart','compositionupdate','compositionend','input'])document.addEventListener(name,event=>{
    if(event.target===input)browserCompositionEvents.push({type:event.type,data:event.data,isComposing:!!event.isComposing,trusted:event.isTrusted});
   },true);
   const position=input.value.indexOf('输入位置：')+'输入位置：'.length;
   input.focus();input.setSelectionRange(position,position);
  });
  const cdp=await desktop.context.newCDPSession(desktop.page);
  await cdp.send('Input.imeSetComposition',{text:'lin',selectionStart:3,selectionEnd:3});await observed;
  await held.route.fulfill({response:held.response});await waitSaved(desktop.page);
  assert.match(await desktop.page.locator('#chapterBody').inputValue(),/输入位置：lin。/);
  assert.doesNotMatch(await desktop.page.locator('#chapterBody').inputValue(),/另一页保留段落/);
  assert.match(await desktop.page.evaluate(()=>chapter().body),/另一页保留段落/);
  await cdp.send('Input.imeSetComposition',{text:'linlan',selectionStart:6,selectionEnd:6});await waitSaved(desktop.page);
  assert.match(await desktop.page.locator('#chapterBody').inputValue(),/输入位置：linlan。/);
  assert.match(await currentBody(firstId),/另一页保留段落/);
  await cdp.send('Input.insertText',{text:'林岚'});
  const expected=original.replace('输入位置：','输入位置：林岚').replace('远端段落。','另一页保留段落。');
  await desktop.page.waitForFunction(value=>document.querySelector('#chapterBody').value===value,expected);
  await waitSaved(desktop.page);assert.equal(await currentBody(firstId),expected);
  const events=await desktop.page.evaluate(()=>browserCompositionEvents);
  results.native_composition_events=events;
  assert.ok(events.some(event=>event.type==='compositionstart'&&event.trusted));
  assert.ok(events.some(event=>event.type==='input'&&event.isComposing&&event.trusted));
  // Chrome CDP commits can report compositionend as untrusted; the exact
  // committed candidate and the saved final正文 are still required above.
  assert.ok(events.some(event=>event.type==='compositionend'&&event.data==='林岚'&&!event.isComposing)||
   events.some(event=>event.trusted&&event.type==='input'&&!event.isComposing));
  await desktop.page.keyboard.insertText('继续');await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),expected.replace('林岚','林岚继续'));
  await cdp.detach();await desktop.page.unroute('**/api/state');await other.context.close();
 });
 await test('自动合并回应迟到时切换章节，只更新来源稿且当前编辑框保持另一章',async()=>{
  const original='第一章开头。\n第一章末尾。',prefix='另一页保留首段。\n';
  await desktop.page.locator('#chapterBody').fill(original);await waitSaved(desktop.page);
  const second=await currentBody('browser_second'),other=await open();
  await other.page.locator('#chapterBody').fill(prefix+original);await waitSaved(other.page);
  let held,ready;const observed=new Promise(resolve=>ready=resolve);
  await desktop.page.route('**/api/state',async route=>{
   if(route.request().method()!=='GET'||held)return route.continue();
   const response=await route.fetch();held={route,response};ready();
  });
  const local=original.replace('第一章末尾。','第一章本页改好的末尾。');
  await desktop.page.locator('#chapterBody').fill(local);await observed;
  await desktop.page.locator('[data-chapter="browser_second"]').click();
  await held.route.fulfill({response:held.response});await waitSaved(desktop.page);
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),second);
  assert.equal(await currentBody('browser_second'),second);assert.equal(await currentBody(firstId),prefix+local);
  await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  assert.equal(await desktop.page.locator('#chapterBody').inputValue(),prefix+local);
  await desktop.page.unroute('**/api/state');await other.context.close();
 });
 await test('拒绝记忆后新浏览器仍回读真实作品',async()=>{
  await api('/api/memory','POST',{id:'browser_memory',type:'人物',name:'测试记忆'});await api('/api/memory/browser_memory','PATCH',{status:'rejected'});
  const fresh=await open();assert.equal(await fresh.page.locator('#projectTitle').innerText(),'浏览器隔离测试作品');assert.equal(await fresh.page.evaluate(()=>backendOnline),true);
  await fresh.context.close();
 });
 await test('完整普通正文提取、作者确认摘要交接、原句定位与改稿失效',async()=>{
  await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  const prose='林岚把红铜罗盘交给守灯人。\n守灯人答应在子夜把罗盘送回渡口。';
  await desktop.page.locator('#chapterBody').fill(prose);await waitSaved(desktop.page);
  await desktop.page.locator('[data-action="chapterAnalysis"]').click();await desktop.page.locator('[data-extract]').click();
  await desktop.page.waitForFunction(()=>queueJobs.some(job=>job.task==='chapter_analysis'&&job.status==='success'),null,{timeout:15000});
  await desktop.page.locator('[data-view="editor"]').click();await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  await desktop.page.locator('[data-action="chapterAnalysis"]').click();await desktop.page.locator('[data-analysis-decision]').waitFor();
  assert.match(await desktop.page.locator('.analysis-modal blockquote').innerText(),/红铜罗盘/);
  await desktop.page.locator('[data-analysis-decision]').selectOption('accepted');
  await desktop.page.locator('[data-confirm-analysis]').click();await desktop.page.waitForFunction(()=>!document.querySelector('[data-confirm-analysis]'));
  await waitSaved(desktop.page);
  const saved=await api('/api/state');const current=saved.chapters.find(item=>item.id===firstId);
  assert.match(current.recap,/红铜罗盘/);assert.match(current.next_brief,/归还罗盘/);
  const confirmed=saved.memory.find(item=>item.name==='交出罗盘');assert.ok(confirmed);
  assert.equal(confirmed.status,'已确认');assert.equal(confirmed.source_span_status,'valid');
  await desktop.page.locator('[data-view="memory"]').click();await desktop.page.locator(`[data-memory-source="${confirmed.id}"]`).click();
  const selection=await desktop.page.locator('#chapterBody').evaluate(el=>el.value.slice(el.selectionStart,el.selectionEnd));
  assert.equal(selection,'林岚把红铜罗盘交给守灯人。');
  await desktop.page.locator('#chapterBody').fill('林岚把罗盘留在自己手里，独自离开渡口。');await waitSaved(desktop.page);
  await desktop.page.locator('[data-action="chapterAnalysis"]').click();
  assert.match(await desktop.page.locator('.analysis-modal').innerText(),/原稿已变|重新提取/);
  assert.equal(await desktop.page.locator('[data-confirm-analysis]').isEnabled(),false);
  await desktop.page.locator('.analysis-modal [data-close]').click();
  const revised=await api('/api/state');assert.equal(revised.chapters.find(item=>item.id===firstId).recap,'');
  assert.equal(revised.chapters.find(item=>item.id===firstId).next_brief,'');
  assert.equal(await desktop.page.locator('#chapterRecap').inputValue(),'');
  assert.equal(await desktop.page.locator('#chapterNextBrief').inputValue(),'');
  if(!await desktop.page.locator('details.chapter-plan').evaluate(el=>el.open))await desktop.page.locator('details.chapter-plan > summary').click();
  await desktop.page.locator('#chapterGoal').fill('作者新目标：带着罗盘返回渡口。');await waitSaved(desktop.page);
  await desktop.page.locator('#chapterTitle').fill('改稿后回到渡口');await waitSaved(desktop.page);
  const planned=await api('/api/state');const plannedChapter=planned.chapters.find(item=>item.id===firstId);
  assert.equal(plannedChapter.recap,'');assert.equal(plannedChapter.next_brief,'');
  assert.equal(plannedChapter.chapter_goal,'作者新目标：带着罗盘返回渡口。');
  assert.equal(revised.memory.find(item=>item.id===confirmed.id).status,'stale');
 });
 await test('语义审稿显示原句并保存作者解释，改稿后报告过期',async()=>{
  const body='林岚把红铜罗盘交给守灯人。\n她回头看了一眼空船。';
  await desktop.page.locator('#chapterBody').fill(body);await waitSaved(desktop.page);
  await desktop.page.locator('[data-action="reviewChapter"]').click();await desktop.page.locator('[data-semantic-review]').click();
  await desktop.page.waitForFunction(()=>queueJobs.some(job=>job.task==='semantic_review'&&job.status==='success'),null,{timeout:15000});
  await desktop.page.locator('[data-view="editor"]').click();await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  await desktop.page.locator('[data-action="reviewChapter"]').click();await desktop.page.locator('[data-review-choice]').waitFor();
  assert.match(await desktop.page.locator('.analysis-modal blockquote').innerText(),/红铜罗盘/);
  await desktop.page.locator('[data-review-reason]').fill('下一段已补交代，她以罗盘换取渡船通行。');
  await desktop.page.locator('[data-review-choice]').selectOption('explained');
  await desktop.page.waitForFunction(()=>document.querySelector('[data-review-choice]')?.disabled===true&&
   document.querySelector('[data-review-reason]')?.readOnly===true);
  const report=(await api(`/api/story-reviews?chapter_id=${firstId}`)).reviews[0];
  assert.equal(report.status,'resolved_by_author');assert.equal(report.auto_pass,false);
  assert.match(report.findings[0].reason,/渡船/);
  await desktop.page.locator('.analysis-modal [data-close]').click();
  await desktop.page.locator('[data-action="reviewChapter"]').click();
  assert.equal(await desktop.page.locator('[data-review-choice]').inputValue(),'explained');
  assert.equal(await desktop.page.locator('[data-review-choice]').isEnabled(),false);
  assert.equal(await desktop.page.locator('[data-review-reason]').evaluate(el=>el.readOnly),true);
  await desktop.page.locator('.analysis-modal [data-close]').click();
  await desktop.page.locator('#chapterBody').fill(body+'\n潮声停了。');await waitSaved(desktop.page);
  await desktop.page.locator('[data-action="reviewChapter"]').click();assert.match(await desktop.page.locator('.analysis-modal').innerText(),/重新审稿/);
  await desktop.page.locator('.analysis-modal [data-close]').click();
 });
 await test('多问题审稿明确选择原句，精确修订第二重复句并重新审稿',async()=>{
  const body='😀章首保留。\n林岚把红铜罗盘交给守灯人。\n她转身走向空船。\n林岚抬头。\n风从门缝吹进来。\n林岚抬头。\n她听见北塔的钟声。';
  reviewMode='multiple';await desktop.page.locator('#chapterBody').fill(body);await waitSaved(desktop.page);
  const reviewsBefore=await desktop.page.evaluate(()=>queueJobs.filter(job=>job.task==='semantic_review'&&job.status==='success').length);
  await desktop.page.locator('[data-action="reviewChapter"]').click();await desktop.page.locator('[data-semantic-review]').click();
  await desktop.page.waitForFunction(n=>queueJobs.filter(job=>job.task==='semantic_review'&&job.status==='success').length>n,reviewsBefore,{timeout:15000});
  await desktop.page.locator('[data-view="editor"]').click();await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  await desktop.page.locator('[data-action="reviewChapter"]').click();await desktop.page.locator('[data-review-issue="1"]').waitFor();
  const jobsBefore=(await api('/api/ai/queue')).jobs.length;
  await desktop.page.locator('[data-review-repair="0"]').click();
  assert.match(await desktop.page.locator('.analysis-modal .form-error').innerText(),/先选择/);
  assert.equal((await api('/api/ai/queue')).jobs.length,jobsBefore);
  await desktop.page.locator('[data-review-target="0"]').selectOption('1');
  await desktop.page.locator('[data-review-source="0"][data-review-evidence-index="1"]').click();
  await desktop.page.waitForFunction(()=>!document.querySelector('.analysis-modal'));
  assert.equal(await desktop.page.locator('#chapterBody').evaluate(el=>el.value.slice(el.selectionStart,el.selectionEnd)),'她转身走向空船。');
  await desktop.page.locator('[data-action="reviewChapter"]').click();
  await desktop.page.locator('[data-review-reason="1"]').fill('用手指动作承接她的紧张。');
  await desktop.page.locator('[data-review-choice="1"]').selectOption('accepted');
  await desktop.page.waitForFunction(()=>document.querySelector('[data-review-decision-note="1"]')?.hidden===false);
  assert.equal(await currentBody(firstId),body);
  assert.equal(await desktop.page.locator('[data-review-repair="1"]').isEnabled(),true);
  assert.equal(await desktop.page.locator('[data-review-target="1"]').inputValue(),'0');
  const callsBeforeDecisionRecovery=providerRequests.length;
  let decisionPosts=0,decisionCommitted=false,failedReviewReads=0;
  const holdDecisionResult=async route=>{
   decisionPosts++;const response=await route.fetch();assert.equal(response.status(),200);
   decisionCommitted=true;await route.fulfill({response});
  };
  const failCommittedReviewRead=async route=>{
   if(!decisionCommitted)return route.continue();
   failedReviewReads++;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'合成审稿读取中断'})});
  };
  await desktop.page.route('**/api/story-reviews/*/issues/*/decision',holdDecisionResult);
  await desktop.page.route('**/api/story-reviews?*',failCommittedReviewRead);
  await desktop.page.locator('[data-review-reason="0"]').fill('作者保留这处衔接，后续在渡船动作中说明。');
  await desktop.page.locator('[data-review-choice="0"]').selectOption('ignored');
  await desktop.page.waitForFunction(()=>document.querySelector('.analysis-modal .form-error')?.textContent.includes('理由已保存'));
  assert.equal(decisionPosts,1);assert.equal(failedReviewReads,1);
  assert.equal(await desktop.page.locator('[data-review-choice="0"]').isEnabled(),false);
  assert.equal(await desktop.page.locator('[data-review-reason="0"]').evaluate(el=>el.readOnly),true);
  assert.equal(await desktop.page.locator('[data-review-repair="1"]').isEnabled(),false);
  assert.equal(await desktop.page.locator('[data-semantic-review]').isEnabled(),false);
  await desktop.page.locator('[data-review-choice="0"]').evaluate(el=>el.dispatchEvent(new Event('change',{bubbles:true})));
  await desktop.page.evaluate(()=>operationChain);
  assert.equal(decisionPosts,1);assert.equal(providerRequests.length,callsBeforeDecisionRecovery);
  assert.equal(await currentBody(firstId),body);
  const savedDecision=(await api(`/api/story-reviews?chapter_id=${firstId}`)).reviews.find(report=>report.findings?.length===2);
  assert.equal(savedDecision.findings[0].decision,'ignored');
  assert.equal(savedDecision.findings[1].decision,'accepted');
  await desktop.page.unroute('**/api/story-reviews/*/issues/*/decision',holdDecisionResult);
  await desktop.page.unroute('**/api/story-reviews?*',failCommittedReviewRead);
  await desktop.page.locator('.analysis-modal [data-close]').click();
  await desktop.page.locator('[data-action="reviewChapter"]').click();
  assert.equal(await desktop.page.locator('[data-review-choice="0"]').inputValue(),'ignored');
  assert.equal(await desktop.page.locator('[data-review-choice="0"]').isEnabled(),false);
  assert.equal(await desktop.page.locator('[data-review-choice="1"]').inputValue(),'accepted');
  assert.equal(await desktop.page.locator('[data-review-repair="1"]').isEnabled(),true);
  assert.equal(decisionPosts,1);assert.equal(providerRequests.length,callsBeforeDecisionRecovery);
  results.review_decision_recovery={accepted_repair_available:true,failed_read_retained_window:true,
   decision_posts:decisionPosts,failed_review_reads:failedReviewReads,extra_model_calls:0,
   reopen_restores_saved_decisions:true,manuscript_unchanged:true};
  providerText='林岚用拇指抵住掌心。';
  await desktop.page.locator('[data-review-repair="1"]').click();
  await desktop.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.task==='polish'&&aiDraft?.text==='林岚用拇指抵住掌心。',null,{timeout:15000});
  const source=await desktop.page.evaluate(()=>aiDraft.source);
  assert.equal(source.range.start,Array.from(body.slice(0,body.lastIndexOf('林岚抬头。'))).length);
  assert.equal(source.range.quote,'林岚抬头。');
  const repairRun=await desktop.page.evaluate(()=>aiDraft.ai_run_id);
  const job=(await api('/api/ai/queue')).jobs.find(item=>item.ai_run_id===repairRun);
  assert.ok(job);
  await desktop.page.screenshot({path:path.join(temporary,'desktop-review-repair.png'),fullPage:true});
  await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').click();
  await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));await waitSaved(desktop.page);
  const start=body.lastIndexOf('林岚抬头。');
  assert.equal(await currentBody(firstId),body.slice(0,start)+'林岚用拇指抵住掌心。'+body.slice(start+'林岚抬头。'.length));
  await desktop.page.locator('[data-action="reviewChapter"]').click();
  assert.match(await desktop.page.locator('.analysis-modal').innerText(),/重新审稿/);
  assert.equal(await desktop.page.locator('[data-review-repair="1"]').isEnabled(),false);
  const completed=await desktop.page.evaluate(()=>queueJobs.filter(job=>job.task==='semantic_review'&&job.status==='success').length);
  reviewMode='single';await desktop.page.locator('[data-semantic-review]').click();
  await desktop.page.waitForFunction(n=>queueJobs.filter(job=>job.task==='semantic_review'&&job.status==='success').length>n,completed,{timeout:15000});
  await desktop.page.locator('[data-view="editor"]').click();await desktop.page.locator(`[data-chapter="${firstId}"]`).click();
  await desktop.page.locator('[data-action="reviewChapter"]').click();await desktop.page.locator('[data-review-choice]').waitFor();
  assert.equal(await desktop.page.locator('[data-review-choice]').isEnabled(),true);
  await desktop.page.locator('.analysis-modal [data-close]').click();
 });
 await test('超限完整候选保留并阻止采纳，手工整理合限后才能进入原稿',async()=>{
  const body=await currentBody(firstId);providerText='这份候选超出要求。'.repeat(6);
  await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiInstruction').fill('续写20至30字。');
  await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.output_validation?.eligible===false,null,{timeout:15000});
  assert.equal(await desktop.page.locator('#aiOutput').inputValue(),providerText);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/要求20至30字/);
  assert.equal(await currentBody(firstId),body);
  const edited='😀林岚把灯罩扣紧，沿着潮水留下的白线走向空船。';
  await desktop.page.locator('#aiOutput').fill(edited);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
  await desktop.page.evaluate(()=>{
   aiDraft.output_validation.issues=[];aiDraft.output_validation.eligible=false;aiDraft.output_validation.protocol_complete=true;
   displayCandidate(aiDraft);
  });
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  await desktop.page.evaluate(()=>{
   aiDraft.output_validation.eligible=true;displayCandidate(aiDraft);
  });
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
  const validation=await desktop.page.evaluate(()=>{
   const saved=aiDraft.output_validation;
   delete aiDraft.output_validation;
   displayCandidate(aiDraft);
   return saved;
  });
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/缺少后端输出验证/);
  await desktop.page.evaluate(saved=>{aiDraft.output_validation=saved;displayCandidate(aiDraft)},validation);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
  await desktop.page.evaluate(()=>{
   aiDraft.output_validation={};
   displayCandidate(aiDraft);
  });
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/未通过后端完整性检查/);
  await desktop.page.evaluate(saved=>{aiDraft.output_validation=saved;displayCandidate(aiDraft)},validation);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
  const editedCharacters=Array.from(edited).filter(char=>!/\s/u.test(char)).length;
  await desktop.page.evaluate(characters=>{
   aiDraft.output_validation={eligible:true,protocol_complete:true,characters,
    issues:[],contract:{counting:'non_whitespace_characters_including_punctuation'}};
   displayCandidate(aiDraft);
  },editedCharacters);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/未通过后端完整性检查/);
  await desktop.page.evaluate(characters=>{
   aiDraft.output_validation={eligible:true,protocol_complete:true,characters,issues:[]};
   displayCandidate(aiDraft);
  },editedCharacters);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/未通过后端完整性检查/);
  await desktop.page.evaluate(saved=>{aiDraft.output_validation=saved;displayCandidate(aiDraft)},validation);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
  await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').click();
  await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),body+'\n\n'+edited);
  await desktop.page.locator('#aiInstruction').fill('');
 });
 await test('整理超限候选保留父原文，双击仅一次调用且合限子候选等待明确采纳',async()=>{
  const body=await currentBody(firstId),parentText='😀原候选超过作者要求，林岚守住钥匙。'.repeat(5);
  providerText=parentText;providerDelay=600;
  await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiInstruction').fill('续写20至30字。');
  await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(text=>aiDraft?.complete&&aiDraft.originalText===text,parentText,{timeout:15000});
  await desktop.page.waitForFunction(()=>!document.querySelector('#aiRevise').disabled);
  const parentRun=await desktop.page.evaluate(()=>aiDraft.ai_run_id);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  await desktop.page.locator('#aiRevise').click();await desktop.page.locator('[data-revise-dialog]').waitFor();
  assert.match(await desktop.page.locator('[data-revise-dialog]').innerText(),/20至30字/);
  assert.match(await desktop.page.locator('[data-revise-dialog]').innerText(),/单独计入模型用量/);
  const notes='保留林岚守住钥匙，按原范围重新组织，保留结尾。';
  await desktop.page.locator('#candidateRevisionNotes').fill(notes);
  const requestId=await desktop.page.evaluate(()=>aiDraft.revision_request.request_id);
  await desktop.page.locator('[data-revise-dialog] [data-close]').click();
  assert.equal(await currentBody(firstId),body);
  await desktop.page.locator('#aiRevise').click();await desktop.page.locator('[data-revise-dialog]').waitFor();
  assert.equal(await desktop.page.evaluate(()=>aiDraft.revision_request.request_id),requestId);
  assert.equal(await desktop.page.locator('#candidateRevisionNotes').inputValue(),notes);
  const revised='😀林岚按住钥匙，循着灯塔钟声走向岸边空船。';
  providerText=revised;const calls=providerRequests.length;
  await desktop.page.locator('[data-revise-submit]').dblclick();
  await desktop.page.waitForFunction(parent=>aiDraft?.complete&&aiDraft.candidate_revision?.parent_run_id===parent,parentRun,{timeout:15000});
  const childRun=await desktop.page.evaluate(()=>aiDraft.ai_run_id);
  assert.notEqual(childRun,parentRun);assert.equal(providerRequests.length,calls+1);
  assert.equal(await desktop.page.locator('#aiOutput').inputValue(),revised);
  assert.equal(await desktop.page.evaluate(()=>aiDraft.decision),'pending');
  assert.equal(await desktop.page.evaluate(()=>aiDraft.output_validation.eligible),true);
  assert.equal(await currentBody(firstId),body);
  assert.deepEqual(await desktop.page.evaluate(parent=>{
   const candidate=candidateStore.get(parent);return {text:candidate.text,originalText:candidate.originalText,decision:candidate.decision};
  },parentRun),{text:parentText,originalText:parentText,decision:'pending'});
  const revisionMaterial=JSON.parse(providerRequests.at(-1).messages.at(-1).content);
  assert.equal(revisionMaterial.candidate_text,parentText);assert.equal(revisionMaterial.author_notes,notes);
  assert.equal(revisionMaterial.output_contract.minimum_characters,20);assert.equal(revisionMaterial.output_contract.maximum_characters,30);
  await desktop.page.locator('[data-view="tasks"]').click();
  const row=desktop.page.locator('.task-row').filter({has:desktop.page.locator(`[data-task-open="${childRun}"]`)});
  assert.match(await row.innerText(),/整理候选/);
  await desktop.page.locator(`[data-task-open="${childRun}"]`).click();
  await desktop.page.locator('#aiOriginal').click();await desktop.page.locator('[data-original-text]').waitFor();
  assert.equal(await desktop.page.locator('[data-original-text]').innerText(),parentText);
  await desktop.page.locator('[data-original-candidate] [data-close]').click();
  await desktop.page.screenshot({path:path.join(temporary,'desktop-revised-candidate.png'),fullPage:true});
  await desktop.page.reload();await desktop.page.waitForFunction(()=>backendOnline);
  await desktop.page.locator('[data-view="editor"]').click();
  await desktop.page.waitForFunction(child=>aiDraft?.complete&&aiDraft.ai_run_id===child,childRun);
  assert.equal(await desktop.page.locator('#aiOutput').inputValue(),revised);
  assert.equal(await currentBody(firstId),body);
  const originalReads=[];
  await desktop.page.route('**/api/ai/queue*',async route=>{
   const response=await route.fetch(),data=await response.json();
   data.jobs=(data.jobs||[]).filter(job=>job.ai_run_id!==parentRun&&job.ai_run_id!==childRun);
   await route.fulfill({response,json:data});
  });
  await desktop.page.route(`**/api/ai/runs/${parentRun}`,async route=>{
   originalReads.push(route.request().url());await route.continue();
  });
  await desktop.page.evaluate(parent=>{candidateStore.delete(parent);persistCandidates()},parentRun);
  await desktop.page.locator('#aiOriginal').click();await desktop.page.locator('[data-original-text]').waitFor();
  assert.equal(await desktop.page.locator('[data-original-text]').innerText(),parentText);
  assert.ok(originalReads.length>0);
  await desktop.page.locator('[data-original-candidate] [data-close]').click();
  const recoveredChild=await desktop.page.evaluate(async({parentRun,childRun})=>{
   const parent=candidateStore.get(parentRun);candidateStore.delete(childRun);persistCandidates();
   const child=await recoverCandidateRevision(parent);displayCandidate(child);return child?.ai_run_id;
  },{parentRun,childRun});
  assert.equal(recoveredChild,childRun);assert.equal(providerRequests.length,calls+1);
  const readsWithCache=originalReads.length;
  assert.equal(await desktop.page.evaluate(async parent=>(await recoverCandidateRevision(candidateStore.get(parent)))?.ai_run_id,parentRun),childRun);
  assert.ok(originalReads.length>readsWithCache);
  await desktop.page.unroute('**/api/ai/queue*');
  await desktop.page.unroute(`**/api/ai/runs/${parentRun}`);
  await desktop.page.locator('#aiAdopt').click();await desktop.page.locator('[data-apply]').dblclick();
  await desktop.page.waitForFunction(()=>!document.querySelector('[data-apply]'));await waitSaved(desktop.page);
  assert.equal(await currentBody(firstId),body+'\n\n'+revised);
  assert.equal((await currentBody(firstId)).split(revised).length-1,1);
  assert.equal(await desktop.page.evaluate(parent=>candidateStore.get(parent).decision,parentRun),'pending');
  const childJob=(await api('/api/ai/queue')).jobs.find(item=>item.ai_run_id===childRun);
  assert.equal(childJob.author_decision,'accepted');
  assert.equal(childJob.candidate_revision.parent_run_id,parentRun);
  results.candidate_revision={parent_run_id:parentRun,child_run_id:childRun,provider_calls:1,
   parent_preserved:true,child_explicitly_adopted_once:true,recovered_outside_queue_list:true,
   mechanical_and_workflow_only:true,facts_and_literature_accepted:false};
  await desktop.page.locator('#aiInstruction').fill('');
 });
 await test('手工编辑过的父候选禁止模型整理，正文和原候选均继续保留',async()=>{
  const body=await currentBody(firstId);providerText='林岚握住钥匙，听见远处钟声。';
  await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(text=>aiDraft?.complete&&aiDraft.originalText===text,providerText,{timeout:15000});
  await desktop.page.waitForFunction(()=>!document.querySelector('#aiRevise').disabled);
  const original=providerText,calls=providerRequests.length;
  const edited='林岚手工保留的新句子。';
  await desktop.page.locator('#aiOutput').fill(edited);
  assert.equal(await desktop.page.locator('#aiRevise').isEnabled(),false);
  await desktop.page.evaluate(()=>reviseAI());
  assert.match(await desktop.page.locator('#toast').innerText(),/手工编辑/);
  assert.equal(await desktop.page.locator('[data-revise-dialog]').count(),0);
  assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),body);
  assert.equal(await desktop.page.evaluate(()=>aiDraft.originalText),original);
  assert.equal(await desktop.page.locator('#aiOutput').inputValue(),edited);
  await desktop.page.locator('#aiClear').click();
 });
 await test('整理确认期间原稿变化会阻止发送，过期候选保留供阅读',async()=>{
  providerText='林岚把钥匙压在掌心，望向空船。';
  await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(text=>aiDraft?.complete&&aiDraft.originalText===text,providerText,{timeout:15000});
  await desktop.page.waitForFunction(()=>!document.querySelector('#aiRevise').disabled);
  const original=providerText,calls=providerRequests.length;
  await desktop.page.locator('#aiRevise').click();await desktop.page.locator('[data-revise-dialog]').waitFor();
  await desktop.page.evaluate(()=>authorEditBody(chapter().body+'\n作者在整理确认期间增加的新稿。'));await waitSaved(desktop.page);
  await desktop.page.locator('[data-revise-submit]').click();
  await desktop.page.waitForFunction(()=>/原稿|资料已变化/.test(document.querySelector('[data-revise-dialog] .form-error')?.textContent||''));
  assert.equal(providerRequests.length,calls);
  assert.equal(await desktop.page.evaluate(()=>aiDraft.originalText),original);
  assert.match(await currentBody(firstId),/整理确认期间增加的新稿/);
  await desktop.page.locator('[data-revise-dialog] [data-close]').click();
  assert.equal(await desktop.page.locator('#aiRevise').isEnabled(),false);
  await desktop.page.evaluate(()=>reviseAI());assert.equal(providerRequests.length,calls);
  assert.equal(await desktop.page.locator('[data-revise-dialog]').count(),0);
  await desktop.page.locator('#aiClear').click();
 });
 await test('整理回应丢失后刷新只读取已有任务，保持请求身份且不重复模型调用',async()=>{
  const body=await currentBody(firstId),original='林岚守住钥匙，候选尚未整理。'.repeat(5);
  providerText=original;
  await desktop.page.locator('#aiInstruction').fill('续写20至30字。');await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(text=>aiDraft?.complete&&aiDraft.originalText===text,original,{timeout:15000});
  await desktop.page.waitForFunction(()=>!document.querySelector('#aiRevise').disabled);
  const parentRun=await desktop.page.evaluate(()=>aiDraft.ai_run_id);
  await desktop.page.locator('#aiRevise').click();await desktop.page.locator('[data-revise-dialog]').waitFor();
  await desktop.page.locator('#candidateRevisionNotes').fill('保持钥匙归属，完整整理到原字数范围。');
  const identity=await desktop.page.evaluate(()=>({...aiDraft.revision_request}));
  let submissions=0;
  await desktop.page.route('**/api/ai/runs/*/revise',async route=>{
   submissions++;await route.fetch();await route.abort('failed');
  });
  providerText='林岚攥紧钥匙，沿着钟声找到岸边，守灯人仍未现身。';
  const calls=providerRequests.length;
  await desktop.page.locator('[data-revise-submit]').click();
  await desktop.page.waitForFunction(()=>document.querySelector('[data-revise-dialog] .form-error')?.textContent.includes('提交结果尚未确认'));
  assert.equal(submissions,1);assert.equal(await currentBody(firstId),body);
  await desktop.page.reload();await desktop.page.waitForFunction(()=>backendOnline);
  await desktop.page.locator('[data-view="editor"]').click();
  await desktop.page.waitForFunction(parent=>candidateStore.get(parent)?.revision_request?.child_run_id,parentRun,{timeout:15000});
  assert.equal(await desktop.page.evaluate(parent=>candidateStore.get(parent).revision_request.request_id,parentRun),identity.request_id);
  assert.equal(submissions,1);
  await desktop.page.locator('#aiRevise').click();
  await desktop.page.waitForFunction(parent=>aiDraft?.complete&&aiDraft.candidate_revision?.parent_run_id===parent,parentRun,{timeout:15000});
  assert.equal(providerRequests.length,calls+1);assert.equal(submissions,1);
  assert.equal(await desktop.page.evaluate(()=>aiDraft.decision),'pending');
  assert.equal(await currentBody(firstId),body);
  await desktop.page.locator('#aiOriginal').click();await desktop.page.locator('[data-original-text]').waitFor();
  assert.equal(await desktop.page.locator('[data-original-text]').innerText(),original);
  await desktop.page.locator('[data-original-candidate] [data-close]').click();
  await desktop.page.unroute('**/api/ai/runs/*/revise');
  await desktop.page.locator('#aiClear').click();
  await desktop.page.locator('#aiInstruction').fill('');
 });
 await test('模型写作思考完整保留且不能采纳，普通对白和独白可整理后使用',async()=>{
  const before=await currentBody(firstId);
  providerText='我们需要分析任务。\n林岚把钥匙压在掌心。';
  await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiGenerate').click();
  await desktop.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.text==='我们需要分析任务。\n林岚把钥匙压在掌心。',null,{timeout:15000});
  await desktop.page.waitForFunction(()=>!streamReaders.size);
  assert.equal(await desktop.page.evaluate(()=>aiDraft.complete),true);
  assert.equal(await desktop.page.locator('#aiOutput').inputValue(),providerText);
  assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  assert.match(await desktop.page.locator('#aiStatus').innerText(),/规划|思考/);
  await desktop.page.waitForFunction(()=>!document.querySelector('#aiRevise').disabled);
  const calls=providerRequests.length;
  await desktop.page.locator('#aiRevise').click();await desktop.page.locator('[data-revise-dialog]').waitFor();
  await desktop.page.locator('[data-revise-dialog] [data-close]').click();assert.equal(providerRequests.length,calls);
  for(const text of ['我们得精心写一段。\n林岚把钥匙压在掌心。','让我构思情节。\n林岚把钥匙压在掌心。']){
   await desktop.page.locator('#aiOutput').fill(text);
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
  }
  for(const text of ['“我们需要分析任务。”林岚关上门。','我们需要分析脚印。林岚沿着台阶蹲下。']){
   await desktop.page.locator('#aiOutput').fill(text);
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
  }
  assert.equal(await currentBody(firstId),before);
  await desktop.page.locator('#aiClear').click();
 });
 await test('关闭思考设置遇到非空思考字段，正文保留且采纳和整理均禁用',async()=>{
  const before=await currentBody(firstId),calls=providerRequests.length,settings=await api('/api/ai/settings');
  providerText='林岚握住钥匙，沿着钟声走向空船。';providerReasoning='私密思考正文不能保存或显示';
  try{
   await api('/api/ai/settings','PUT',{parameters:{...settings.parameters,thinking:{type:'disabled'},reasoning_effort:undefined}});
   await desktop.page.locator('#aiTask').selectOption('continue');
   await desktop.page.locator('#aiInstruction').fill('续写10至100字。');
   await desktop.page.locator('#aiGenerate').click();
   await desktop.page.waitForFunction(()=>aiDraft?.complete&&aiDraft.reasoning_observation?.unexpected_reasoning,null,{timeout:15000});
   await desktop.page.waitForFunction(()=>!streamReaders.size);
   assert.equal(await desktop.page.locator('#aiOutput').inputValue(),providerText);
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
   assert.equal(await desktop.page.locator('#aiRevise').isEnabled(),false);
   assert.match(await desktop.page.locator('#aiStatus').innerText(),/回复与关闭思考设置不一致/);
   assert.match(await desktop.page.locator('#aiRevise').getAttribute('title'),/回复与关闭思考设置不一致/);
   await desktop.page.locator('#aiOutput').fill('林岚改走堤岸，把钥匙留在掌心。');
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),false);
   await desktop.page.evaluate(()=>reviseAI());
   assert.equal(await desktop.page.locator('[data-revise-dialog]').count(),0);
   assert.equal(providerRequests.length,calls+1);
   assert.equal(await currentBody(firstId),before);
   assert.equal(await desktop.page.evaluate(()=>JSON.stringify([...candidateStore.values()]).includes('私密思考正文不能保存或显示')),false);
   assert.equal((await desktop.page.locator('body').innerText()).includes(providerReasoning),false);
   await desktop.page.locator('#aiClear').click();
  }finally{
   providerReasoning=null;await api('/api/ai/settings','PUT',{parameters:settings.parameters});
   await desktop.page.locator('#aiInstruction').fill('');
  }
 });
 await test('队列先完成后人工编辑，迟到流式分片不覆盖候选、缓存或原稿',async()=>{
  const before=await currentBody(firstId),original='林岚握住钥匙，沿着钟声走向空船。',edited='林岚保留钥匙，先停在岸边辨认钟声。';
  await desktop.page.waitForFunction(()=>!streamReaders.size);
  providerText=original;providerDelay=80;
  let releaseProvider,streamObserved,streamBuffered,held;
  const heldQueue=[],queueRoute=/\/api\/ai\/queue(?:\?|$)/;
  providerStreamGate=new Promise(resolve=>releaseProvider=resolve);
  const observed=new Promise(resolve=>streamObserved=resolve),buffered=new Promise(resolve=>streamBuffered=resolve);
  const streamRoute=/\/api\/ai\/runs\/[^/]+\/stream(?:\?|$)/;
  await desktop.page.route(streamRoute,async route=>{
   streamObserved();
   const response=await route.fetch(),body=await response.text();
   held={route,response,body};streamBuffered();
  });
  try{
   await desktop.page.locator('#aiTask').selectOption('continue');await desktop.page.locator('#aiGenerate').click();
   await observed;
   await desktop.page.waitForFunction(()=>aiDraft?.job_id&&!aiDraft.complete);
   const identity=await desktop.page.evaluate(()=>({run_id:aiDraft.ai_run_id,job_id:aiDraft.job_id}));
   releaseProvider();providerStreamGate=null;
   await buffered;
   assert.match(held.body,/event: delta/);assert.match(held.body,/event: done/);
   await desktop.page.waitForFunction(({run_id,job_id})=>aiDraft?.ai_run_id===run_id&&aiDraft.complete&&
    queueJobs.some(job=>job.id===job_id&&job.status==='success'),identity,{timeout:15000});
   assert.equal(await desktop.page.evaluate(id=>streamReaders.has(id),identity.job_id),true);
   await desktop.page.locator('#aiOutput').fill(edited);
   assert.equal(await desktop.page.evaluate(()=>aiDraft.originalText),original);
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
   await desktop.page.route(queueRoute,route=>{
    if(route.request().method()==='GET')heldQueue.push(route);else return route.continue();
   });
   await held.route.fulfill({response:held.response,body:held.body});held=null;
   await desktop.page.waitForFunction(id=>!streamReaders.has(id),identity.job_id);
   const candidate=await desktop.page.evaluate(({run_id})=>({
    run_id:aiDraft.ai_run_id,complete:aiDraft.complete,text:aiDraft.text,originalText:aiDraft.originalText,
    decision:aiDraft.decision,stored_text:candidateStore.get(run_id).text,
    cached_text:JSON.parse(storage.get(cacheKey(':candidates'))).find(item=>item.ai_run_id===run_id).text
   }),identity);
   assert.deepEqual(candidate,{run_id:identity.run_id,complete:true,text:edited,originalText:original,
    decision:'pending',stored_text:edited,cached_text:edited});
   assert.equal(await desktop.page.locator('#aiOutput').inputValue(),edited);
   assert.doesNotMatch(await desktop.page.locator('#aiStatus').innerText(),/正在生成/);
   assert.equal(await desktop.page.locator('#aiAdopt').isEnabled(),true);
   assert.equal(await currentBody(firstId),before);
   await desktop.page.unroute(queueRoute);
   for(const route of heldQueue.splice(0))await route.continue();
   await desktop.page.waitForFunction(()=>!queueLoading);
   await desktop.page.evaluate(()=>refreshQueue());
   assert.equal(await desktop.page.locator('#aiOutput').inputValue(),edited);
   assert.equal(await desktop.page.evaluate(()=>aiDraft.complete),true);
   await desktop.page.locator('#aiClear').click();
  }finally{
   releaseProvider();providerStreamGate=null;
   if(held)await held.route.abort();
   await desktop.page.unroute(streamRoute);
   await desktop.page.unroute(queueRoute);
   for(const route of heldQueue.splice(0))await route.abort();
  }
 });
 assert.deepEqual(desktop.errors,[]);await desktop.context.close();
 const compact=await open({width:1280,height:800});
 await test('1280×800电脑窗口无横向溢出，编辑和生成控件可用',async()=>{
  assert.equal(await compact.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const bad=await compact.page.locator('button').evaluateAll(elements=>elements.filter(el=>el.clientHeight>0&&el.scrollHeight>el.clientHeight+2).map(el=>el.innerText));
  assert.deepEqual(bad,[]);assert.ok((await compact.page.locator('#aiInstruction').boundingBox()).width>150);
  await compact.page.screenshot({path:path.join(temporary,'compact-desktop-editor.png'),fullPage:true});
 });
 assert.deepEqual(compact.errors,[]);await compact.context.close();
 const readOnly=await open();
 await test('复制作品的完整历史候选同步只读标记，旧完整缓存也不能整理或采纳',async()=>{
  const sourceBody=await currentBody(firstId),calls=providerRequests.length;
  const copied=await api('/api/projects/default-project/duplicate','POST',{title:'仅供阅读的候选副本'});
  const project=copied.project.id;
  const jobs=(await api('/api/ai/queue','GET',undefined,project)).jobs;
  const child=jobs.find(job=>job.status==='success'&&job.candidate_revision);
  assert.ok(child);assert.equal(child.restored_read_only,true);
  await readOnly.page.evaluate(async copied=>{authorProjects.push(copied);await authorSwitchProject(copied.id)},copied.project);
  await readOnly.page.locator('[data-view="tasks"]').click();
  await readOnly.page.locator(`[data-task-open="${child.ai_run_id}"]`).waitFor();
  await readOnly.page.locator(`[data-task-open="${child.ai_run_id}"]`).click();
  assert.equal(await readOnly.page.locator('#aiOutput').getAttribute('readonly'),'');
  assert.equal(await readOnly.page.locator('#aiAdopt').isEnabled(),false);
  assert.equal(await readOnly.page.locator('#aiRevise').isEnabled(),false);
  assert.equal(await readOnly.page.locator('#aiClear').isEnabled(),false);
  await readOnly.page.evaluate(async()=>{
   aiDraft.restored_read_only=false;persistCandidates();await refreshQueue();
  });
  await readOnly.page.waitForFunction(()=>aiDraft?.restored_read_only===true);
  assert.equal(await readOnly.page.locator('#aiAdopt').isEnabled(),false);
  await readOnly.page.evaluate(async()=>{await reviseAI();await adoptAI()});
  assert.equal(await readOnly.page.locator('[data-revise-dialog]').count(),0);
  assert.equal(await readOnly.page.locator('[data-apply]').count(),0);
  assert.equal(providerRequests.length,calls);assert.equal(await currentBody(firstId),sourceBody);
  await readOnly.page.locator('#aiOriginal').click();await readOnly.page.locator('[data-original-text]').waitFor();
  assert.ok((await readOnly.page.locator('[data-original-text]').innerText()).length>0);
  await readOnly.page.locator('[data-original-candidate] [data-close]').click();
 });
 assert.deepEqual(readOnly.errors,[]);await readOnly.context.close();
 const workspace=await open();
 await test('两作品切换隔离，卷章场景保存后建立目录',async()=>{
  await workspace.page.locator('[data-view="library"]').click();await workspace.page.locator('[data-action="createProject"]').click();
  await workspace.page.locator('#newBookTitle').fill('独立第二部');await workspace.page.locator('#newBookSummary').fill('雪地邮差追查封缄。');
  await workspace.page.locator('.author-form [type="submit"]').click();await workspace.page.waitForFunction(()=>state.project.title==='独立第二部');
  await waitSaved(workspace.page);
  await workspace.page.locator('[data-view="blueprint"]').click();await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
  await workspace.page.locator('[data-action="addVolume"]').click();await workspace.page.locator('#volumeTitle').fill('第一卷 雪路');
  await workspace.page.locator('.author-form [type="submit"]').click();await workspace.page.locator('[data-plan-add-chapter]').waitFor();
  await workspace.page.locator('[data-plan-add-chapter]').click();await workspace.page.locator('#planChapterTitle').fill('第一章 封缄');
  await workspace.page.locator('#planChapterGoal').fill('找回封缄');await workspace.page.locator('#planChapterPov').fill('邮差');
  await workspace.page.locator('.author-form [type="submit"]').click();await workspace.page.locator('[data-plan-add-scene]').waitFor();
  await workspace.page.locator('[data-plan-add-scene]').click();await workspace.page.locator('#planSceneTitle').fill('驿站冲突');
  await workspace.page.locator('#planSceneGoal').fill('取回遗失的包裹');await workspace.page.locator('.author-form [type="submit"]').click();
  await workspace.page.locator('[data-plan-edit-scene]').waitFor();await workspace.page.locator('[data-action="materializePlan"]').click();
  await workspace.page.waitForFunction(()=>state.chapters.some(item=>item.plan_id&&item.title==='第一章 封缄'),null,{timeout:15000});
  await waitSaved(workspace.page);
  await workspace.page.locator('[data-view="editor"]').click();assert.ok((await workspace.page.locator('.chapter-sidebar').innerText()).includes('封缄'));
  const newId=await workspace.page.evaluate(()=>requestedProject);assert.notEqual(newId,'default-project');
  await workspace.page.locator('[data-view="library"]').click();await workspace.page.locator('[data-project-open="default-project"]').waitFor();
  await workspace.page.locator('[data-project-open="default-project"]').click();await workspace.page.waitForFunction(()=>state.project.title==='浏览器隔离测试作品');
  assert.equal((await api('/api/state','GET',undefined,newId)).project.title,'独立第二部');
 });
 await test('电脑端四阶段规划中途刷新和失败重做，最终采用蓝图不改变已写正文',async()=>{
  await workspace.page.locator('[data-view="library"]').click();
  const secondId=(await api('/api/projects')).projects.find(project=>project.title==='独立第二部').id;
  await workspace.page.locator(`[data-project-open="${secondId}"]`).click();
  await workspace.page.waitForFunction(()=>state.project.title==='独立第二部');await waitSaved(workspace.page);
  const owner=await workspace.page.evaluate(()=>requestedProject);
  await workspace.page.locator('[data-view="editor"]').click();
  await workspace.page.locator('#chapterBody').fill('😀先前章节的已写正文。雪地邮差把封缄压在油灯下，这一段不能被新规划覆盖。');
  await waitSaved(workspace.page);
  const before=await api('/api/state','GET',undefined,owner);
  await workspace.page.locator('[data-view="blueprint"]').click();await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
  await workspace.page.locator('[data-action="beginPlanning"]').click();
  await workspace.page.locator('#planningPremise').fill('雪地邮差追查封缄，旧驿站的蜡印藏着寄信人的名字。');
  await workspace.page.locator('#planningVolumes').fill('2');await workspace.page.locator('#planningChapters').fill('2');
  await workspace.page.locator('#planningScenes').fill('1');
  await workspace.page.locator('.author-form [type="submit"]').click();
  await workspace.page.locator('[data-planning-start]').waitFor();
  const workflowId=await workspace.page.evaluate(()=>authorPlanning.id);
  for(const stage of ['overview','volumes','chapters','scenes']){
   assert.equal(await workspace.page.evaluate(()=>authorPlanning.stage),stage);
   if(stage==='chapters')planningFailure=true;
   await workspace.page.locator('[data-planning-start]').click();
   if(stage==='chapters'){
    await workspace.page.waitForFunction(()=>authorPlanning.attempt?.status==='failed',null,{timeout:15000});
    const failures=await workspace.page.evaluate(()=>authorPlanning.attempts.filter(a=>a.status==='failed').length);
    assert.equal(failures,1);assert.equal(await workspace.page.locator('[data-planning-start]').innerText(),'重做当前阶段');
    await workspace.page.locator('[data-planning-start]').click();
   }
   await workspace.page.locator('[data-planning-confirm]').waitFor({timeout:15000});
   assert.equal(await workspace.page.evaluate(()=>authorPlanning.attempt.status),'complete');
   if(stage==='overview'){
    await workspace.page.reload();await workspace.page.waitForFunction(()=>backendOnline);
    await workspace.page.locator('[data-view="blueprint"]').click();await workspace.page.locator('[data-planning-confirm]').waitFor();
    assert.equal(await workspace.page.evaluate(()=>authorPlanning.id),workflowId);
   }
   await workspace.page.locator('[data-planning-confirm]').click();
   await workspace.page.waitForFunction(previous=>authorPlanning.stage!==previous||authorPlanning.status==='completed',stage);
  }
  assert.equal(await workspace.page.evaluate(()=>authorPlanning.attempts.filter(a=>a.status==='confirmed').length),4);
  await workspace.page.screenshot({path:path.join(temporary,'desktop-planning.png'),fullPage:true});
  await workspace.page.locator('[data-planning-apply]').click();
  await workspace.page.waitForFunction(()=>authorPlanning.applied===true);
  const after=await api('/api/state','GET',undefined,owner);
  assert.deepEqual(after.chapters.map(c=>[c.id,c.body]),before.chapters.map(c=>[c.id,c.body]));
  const blueprint=(await api('/api/blueprint','GET',undefined,owner)).blueprint;
  assert.equal(blueprint.volumes.length,2);assert.equal(blueprint.volumes.flatMap(v=>v.chapters).length,4);
  assert.equal(blueprint.volumes.flatMap(v=>v.chapters).flatMap(c=>c.scenes).length,4);
  await workspace.page.locator('[data-view="tasks"]').click();await workspace.page.locator('[data-task-planning]').first().waitFor();
  await workspace.page.locator('[data-task-planning]').first().click();await workspace.page.locator('[data-planning-status]').waitFor();
  assert.match(await workspace.page.locator('[data-planning-status]').innerText(),/已采用/);
  const planIds=blueprint.volumes.flatMap(v=>v.chapters).map(c=>c.id);
  await workspace.page.locator('[data-action="materializePlan"]').click();
  await workspace.page.waitForFunction(({ids,count})=>state.chapters.length===count&&
   ids.every(id=>state.chapters.filter(c=>c.plan_id===id).length===1),
   {ids:planIds,count:before.chapters.length+planIds.length},{timeout:15000});
  await waitSaved(workspace.page);
  const materialized=await api('/api/state','GET',undefined,owner);
  assert.deepEqual(materialized.chapters.slice(0,planIds.length).map(c=>c.plan_id),planIds);
  for(const c of before.chapters)assert.equal(materialized.chapters.find(x=>x.id===c.id).body,c.body);
  const chapterIds=materialized.chapters.map(c=>c.id);
  await workspace.page.locator('[data-action="materializePlan"]').click();
  await workspace.page.waitForFunction(version=>Number(state.version)>Number(version),materialized.version);
  await waitSaved(workspace.page);
  assert.deepEqual((await api('/api/state','GET',undefined,owner)).chapters.map(c=>c.id),chapterIds);
  await workspace.page.locator('[data-view="library"]').click();await workspace.page.locator('[data-project-open="default-project"]').click();
  await workspace.page.waitForFunction(()=>requestedProject==='default-project');await waitSaved(workspace.page);
 });
 const raceProjects=[];
 for(const label of ['甲','乙']){
  const created=await api('/api/projects','POST',{title:`竞态作品${label}`,summary:`独立正文${label}`,goal:50000});
  const id=(created.project||created.state.project).id;
  const snapshot=await api('/api/state','GET',undefined,id);
  snapshot.chapters[0].body=`竞态作品${label}专属原稿。`;await api('/api/state','PUT',snapshot,id);
  await api('/api/ai/settings','PUT',{base_url:`http://127.0.0.1:${providerPort}/v1`,model:`browser-race-${label}`,
   parameters:{temperature:0.65,max_tokens:3000},daily_limit:100},id);
  raceProjects.push({id,title:`竞态作品${label}`,body:`竞态作品${label}专属原稿。`,model:`browser-race-${label}`});
 }
 async function switchThroughLibrary(project){
  await workspace.page.locator('[data-view="library"]').click();
  await workspace.page.locator(`[data-project-open="${project.id}"]`).waitFor();
  await workspace.page.locator(`[data-project-open="${project.id}"]`).click();
  await workspace.page.waitForFunction(id=>backendOnline&&requestedProject===id&&state.project.id===id,project.id);
  await waitSaved(workspace.page);
 }
 async function assertProject(project){
  const snapshot=await workspace.page.evaluate(()=>({id:requestedProject,state_id:state.project.id,
   title:state.project.title,body:state.chapters[0].body,online:backendOnline,error:saveError,
   cached:JSON.parse(storage.get(cacheKey())),confirmed:confirmedState}));
  assert.equal(snapshot.id,project.id);assert.equal(snapshot.state_id,project.id);
  assert.equal(snapshot.title,project.title);assert.equal(snapshot.body,project.body);
  assert.equal(snapshot.online,true);assert.equal(snapshot.error,'');
  assert.equal(snapshot.cached.project.id,project.id);assert.equal(snapshot.cached.chapters[0].body,project.body);
  assert.equal(snapshot.confirmed.project.id,project.id);
  assert.equal((await api('/api/state','GET',undefined,project.id)).chapters[0].body,project.body);
 }
 await test('同一时刻连续点击打开两书，最终作品与缓存完全隔离',async()=>{
  await workspace.page.locator('[data-view="library"]').click();
  await workspace.page.locator(`[data-project-open="${raceProjects[1].id}"]`).waitFor();
  await workspace.page.evaluate(ids=>ids.forEach(id=>document.querySelector(`[data-project-open="${id}"]`).click()),raceProjects.map(item=>item.id));
  await workspace.page.evaluate(()=>authorSwitchChain);await assertProject(raceProjects[1]);
  await workspace.page.locator('[data-view="editor"]').click();
  assert.equal(await workspace.page.locator('#chapterBody').inputValue(),raceProjects[1].body);
 });
 await test('旧作品读取成功与错误迟到，均不改变当前正文、缓存和保存状态',async()=>{
  for(const success of [true,false]){
   await switchThroughLibrary(raceProjects[0]);
   let release,ready;const observed=new Promise(resolve=>ready=resolve);let captured=false;
   await workspace.page.route('**/api/state',async route=>{
    if(captured||route.request().method()!=='GET'||route.request().headers()['x-moyu-project']!==raceProjects[0].id)return route.continue();
    captured=true;const response=await route.fetch();
    await new Promise(resolve=>{release=resolve;ready()});
    await route.fulfill(success?{response}:{status:503,contentType:'application/json',body:JSON.stringify({error:'旧作品迟到错误哨兵'})});
   });
   await workspace.page.evaluate(()=>{window.raceHydrate=hydrate()});await observed;
   await switchThroughLibrary(raceProjects[1]);release();
   await workspace.page.evaluate(()=>window.raceHydrate);await assertProject(raceProjects[1]);
   await workspace.page.unroute('**/api/state');
  }
 });
 await test('旧作品模型设置迟到，当前模型身份保持正确',async()=>{
  await switchThroughLibrary(raceProjects[0]);
  let release,ready;const observed=new Promise(resolve=>ready=resolve);let captured=false;
  await workspace.page.route('**/api/ai/status',async route=>{
   if(captured||route.request().headers()['x-moyu-project']!==raceProjects[0].id)return route.continue();
   captured=true;const response=await route.fetch();
   await new Promise(resolve=>{release=resolve;ready()});await route.fulfill({response});
  });
  await workspace.page.evaluate(()=>{window.raceStatus=loadAIStatus()});await observed;
  await switchThroughLibrary(raceProjects[1]);release();await workspace.page.evaluate(()=>window.raceStatus);
  assert.equal(await workspace.page.evaluate(()=>aiStatus.model),raceProjects[1].model);
  await assertProject(raceProjects[1]);await workspace.page.unroute('**/api/ai/status');
 });
 await test('服务回应作品身份错误时拒绝覆盖，恢复后仍为原书',async()=>{
  const wrong=await api('/api/state','GET',undefined,raceProjects[0].id);let captured=false;
  await workspace.page.route('**/api/state',route=>{
   if(captured||route.request().method()!=='GET')return route.continue();
   captured=true;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(wrong)});
  });
  assert.equal(await workspace.page.evaluate(()=>hydrate()),false);
  const refused=await workspace.page.evaluate(()=>({id:state.project.id,body:state.chapters[0].body,error:saveError,
   online:backendOnline,cached:JSON.parse(storage.get(cacheKey()))}));
  assert.equal(refused.id,raceProjects[1].id);assert.equal(refused.body,raceProjects[1].body);
  assert.equal(refused.cached.project.id,raceProjects[1].id);assert.equal(refused.online,false);
  assert.match(refused.error,/不一致/);await workspace.page.unroute('**/api/state');
  await workspace.page.evaluate(()=>reconnect());await waitSaved(workspace.page);await assertProject(raceProjects[1]);
  await switchThroughLibrary({id:'default-project'});
 });
 await test('设置页保存风格和用途模型，真实本机探测成功且请求使用所选模型',async()=>{
  providerDelay=80;providerText='连接正常';
  await workspace.page.locator('[data-view="settings"]').click();await workspace.page.locator('#modelSettings').waitFor();
  await workspace.page.locator('#modelBaseUrl').fill(`http://127.0.0.1:${providerPort}/v1`);
  await workspace.page.locator('#modelDefault').fill('browser-mock');
  await workspace.page.locator('#modelTone').fill('克制、动作具体');
  await workspace.page.locator('#modelStyleInstructions').fill('保持林岚视角，不补她未知的事实。');
  await workspace.page.locator('#modelMaxTokens').fill('4096');
  await workspace.page.locator('#modelReasoning').selectOption('none');
  await workspace.page.locator('[data-task-model="continue"]').fill('browser-special-continue');
  await workspace.page.locator('#modelSettings [type="submit"]').click();
  await workspace.page.waitForFunction(()=>authorSettings?.styles?.tone==='克制、动作具体');
  await workspace.page.locator('#modelProbe').click();
  await workspace.page.waitForFunction(()=>document.querySelector('#modelProbeStatus')?.textContent.includes('实测成功'),null,{timeout:15000});
  const configured=(await api('/api/ai/settings')).settings;
  assert.equal(configured.models.continue,'browser-special-continue');assert.equal(configured.last_probe.success,true);
  assert.equal(configured.parameters.max_tokens,4096);assert.equal(configured.parameters.reasoning_effort,'none');
  assert.equal(configured.api_key_configured,false);
  await workspace.page.locator('[data-view="editor"]').click();await workspace.page.locator(`[data-chapter="${firstId}"]`).click();
  providerText='用途模型测试唯一候选。';await workspace.page.locator('#aiTask').selectOption('continue');await workspace.page.locator('#aiGenerate').click();
  await workspace.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.text==='用途模型测试唯一候选。',null,{timeout:15000});
  const actual=providerRequests.at(-1);assert.equal(actual.model,'browser-special-continue');
  assert.equal(actual.max_tokens,4096);assert.equal(actual.reasoning_effort,'none');
  assert.match(actual.messages[0].content,/克制、动作具体/);
  await workspace.page.locator('#aiClear').click();
 });
 await test('电脑端明确关闭DeepSeek思考，保存回读和实际请求均保留开关',async()=>{
  await workspace.page.locator('[data-view="settings"]').click();await workspace.page.locator('#modelSettings').waitFor();
  await workspace.page.locator('#modelReasoning').selectOption('deepseek_disabled');
  await workspace.page.locator('#modelSettings [type="submit"]').click();
  await workspace.page.waitForFunction(()=>authorSettings?.parameters?.thinking?.type==='disabled');
  await workspace.page.reload();await workspace.page.waitForFunction(()=>backendOnline);
  await workspace.page.locator('[data-view="settings"]').click();await workspace.page.locator('#modelSettings').waitFor();
  assert.equal(await workspace.page.locator('#modelReasoning').inputValue(),'deepseek_disabled');
  const configured=(await api('/api/ai/settings')).settings;
  assert.deepEqual(configured.parameters.thinking,{type:'disabled'});
  assert.equal(configured.parameters.reasoning_effort,undefined);
  await workspace.page.locator('[data-view="editor"]').click();await workspace.page.locator(`[data-chapter="${firstId}"]`).click();
  providerText='明确思考开关测试候选。';
  await workspace.page.locator('#aiTask').selectOption('continue');await workspace.page.locator('#aiGenerate').click();
  await workspace.page.waitForFunction(()=>aiDraft?.complete&&aiDraft?.text==='明确思考开关测试候选。',null,{timeout:15000});
  const actual=providerRequests.at(-1);
  assert.deepEqual(actual.thinking,{type:'disabled'});assert.equal(actual.reasoning_effort,undefined);
  await workspace.page.locator('#aiClear').click();
  await workspace.page.locator('[data-view="settings"]').click();await workspace.page.locator('#modelSettings').waitFor();
  await workspace.page.locator('#modelReasoning').selectOption('');
  await workspace.page.locator('#modelSettings [type="submit"]').click();
  await workspace.page.waitForFunction(()=>!authorSettings?.parameters?.thinking&&!authorSettings?.parameters?.reasoning_effort);
  await waitSaved(workspace.page);
 });
 await test('旧书预览拆分合并导入独立作品，原作品保持原稿',async()=>{
  const oldBody=await currentBody(firstId);
  const text='短序言。\n第一章 同名\n林岚留住月牙密钥。\n第一章 同名\n\n第二章 渡口\n守灯人带来冷灰药粉。\n';
  await workspace.page.locator('[data-view="references"]').click();
  await workspace.page.locator('#bookFileInput').setInputFiles({name:'old-book.txt',mimeType:'text/plain',buffer:Buffer.from(text)});
  await workspace.page.locator('[data-import-confirm]').waitFor();
  await workspace.page.locator('#importBookTitle').fill('浏览器旧书导入');
  await workspace.page.locator('[data-import-body="0"]').evaluate(el=>{el.focus();el.setSelectionRange(2,2)});
  await workspace.page.locator('[data-import-split="0"]').click();assert.equal(await workspace.page.locator('[data-import-body]').count(),5);
  await workspace.page.locator('[data-import-merge="0"]').click();assert.equal(await workspace.page.locator('[data-import-body]').count(),4);
  await workspace.page.locator('[data-import-confirm]').click();
  await workspace.page.waitForFunction(()=>state.project.title==='浏览器旧书导入');
  await waitSaved(workspace.page);
  const id=await workspace.page.evaluate(()=>requestedProject);const saved=await api('/api/state','GET',undefined,id);
  assert.equal(saved.chapters.length,4);assert.equal(saved.chapters.map(item=>item.body).join(''),text);
  assert.equal(new Set(saved.chapters.map(item=>item.id)).size,4);assert.equal(await currentBody(firstId),oldBody);
  results.imported_project=id;
 });
 await test('作为参考来源导入、阅读和按原句搜索不改变本书正文',async()=>{
  const project=await workspace.page.evaluate(()=>requestedProject);
  const before=await api('/api/state','GET',undefined,project);
  const text='# 资料书\n\n## 第一章 规则\n红铜罗盘只有在子夜才能指向北塔。\n\n## 第二章 药粉\n冷灰药粉不能沾水。\n';
  await workspace.page.locator('[data-view="references"]').click();
  await workspace.page.locator('#bookFileInput').setInputFiles({name:'reference.md',mimeType:'text/markdown',buffer:Buffer.from(text)});
  await workspace.page.locator('[data-import-confirm]').waitFor();await workspace.page.locator('#importBookMode').selectOption('reference');
  await workspace.page.locator('#importBookTitle').fill('浏览器资料书');await workspace.page.locator('[data-import-confirm]').click();
  await workspace.page.locator('[data-reference-open]').waitFor();
  const after=await api('/api/state','GET',undefined,project);
  assert.deepEqual(after.chapters.map(item=>[item.id,item.body]),before.chapters.map(item=>[item.id,item.body]));
  const reference=(await api('/api/references','GET',undefined,project)).references[0];
  assert.equal(reference.use,'reference_not_story_fact');
  assert.equal(reference.filename,'reference.md');
  assert.match(await workspace.page.locator('#referenceList').innerText(),/reference.md/);
  await workspace.page.locator('[data-reference-open]').click();
  await workspace.page.locator('#referenceResults summary').filter({hasText:'第一章 规则'}).click();
  assert.match(await workspace.page.locator('#referenceResults').innerText(),/红铜罗盘只有在子夜/);
  await workspace.page.locator('#referenceQuery').fill('红铜罗盘子夜北塔');await workspace.page.locator('[data-reference-search]').click();
  await workspace.page.waitForFunction(()=>document.querySelector('#referenceResults .result')?.textContent.includes('红铜罗盘'));
  await workspace.page.locator('.candidate-modal [data-close]').click();
  results.reference_document=reference.id;
 });
 await test('完整备份下载与恢复副本保留正文和参考来源',async()=>{
  const sourceId=await workspace.page.evaluate(()=>requestedProject);
  const before=await api('/api/state','GET',undefined,sourceId);
  await workspace.page.locator('[data-view="backups"]').click();await workspace.page.locator('[data-action="createBackup"]').click();
  await workspace.page.locator('[data-backup-download]').waitFor();
  const downloaded=workspace.page.waitForEvent('download');
  await workspace.page.locator('[data-backup-download]').first().click();const download=await downloaded;
  assert.match(download.suggestedFilename(),/\.zip$/);await download.saveAs(path.join(temporary,'complete-backup.zip'));
  assert.ok(fs.statSync(path.join(temporary,'complete-backup.zip')).size>1000);
  await workspace.page.locator('[data-backup-restore]').first().click();await workspace.page.locator('#restoreConfirm').check();
  await workspace.page.locator('.author-form [type="submit"]').click();
  await workspace.page.waitForFunction(previous=>requestedProject!==previous,sourceId);await waitSaved(workspace.page);
  const restoredId=await workspace.page.evaluate(()=>requestedProject);
  const restored=await api('/api/state','GET',undefined,restoredId);
  assert.equal(restored.chapters.map(item=>item.body).join(''),before.chapters.map(item=>item.body).join(''));
  assert.notDeepEqual(restored.chapters.map(item=>item.id),before.chapters.map(item=>item.id));
  const references=(await api('/api/references','GET',undefined,restoredId)).references;assert.equal(references.length,1);
  assert.equal(references[0].title,'浏览器资料书');
  assert.deepEqual((await api('/api/state','GET',undefined,sourceId)).chapters.map(item=>item.body),before.chapters.map(item=>item.body));
  results.restored_project=restoredId;
 });
 await test('200字作品名恢复可读写，读取失败后只重开已建立副本且不提前报成功',async()=>{
  const page=workspace.page,previousProject=await page.evaluate(()=>requestedProject);
  const title='长'.repeat(200),body='长作品名的原稿😀。恢复读取失败也不能改写这段文字。';
  await page.locator('[data-view="library"]').click();await page.locator('[data-action="createProject"]').click();
  await page.locator('#newBookTitle').fill(title);await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(previous=>requestedProject!==previous,previousProject);await waitSaved(page);
  const sourceId=await page.evaluate(()=>requestedProject);
  await page.locator('[data-view="editor"]').click();await page.locator('#chapterBody').fill(body);await waitSaved(page);
  await page.locator('[data-view="backups"]').click();await page.locator('[data-action="createBackup"]').click();
  await page.locator('[data-backup-restore]').waitFor();
  const projectsBefore=(await api('/api/projects')).projects.length,providerBefore=providerRequests.length;
  let restores=0;
  const observe=request=>{if(request.method()==='POST'&&/\/api\/backups\/[^/]+\/restore$/.test(request.url()))restores++};
  page.on('request',observe);
  await page.route('**/api/state',async route=>{
   if(route.request().method()==='GET'&&route.request().headers()['x-moyu-project']!==sourceId)return route.abort('failed');
   return route.continue();
  });
  await page.locator('[data-backup-restore]').first().click();await page.locator('#restoreConfirm').check();
  await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.author-form .form-error')?.textContent.includes('副本已建立'));
  const restoredId=await page.evaluate(()=>requestedProject);
  assert.notEqual(restoredId,sourceId);assert.equal(await page.evaluate(()=>backendOnline),false);
  assert.match(await page.locator('.author-form .form-error').innerText(),/暂时无法打开/);
  assert.equal(await page.locator('.author-form [type="submit"]').innerText(),'重新打开副本');
  assert.doesNotMatch(await page.locator('#toast').innerText(),/恢复副本已打开/);
  assert.equal(restores,1);assert.equal((await api('/api/projects')).projects.length,projectsBefore+1);
  const restoredTitle='长'.repeat(193)+' · 恢复副本';
  assert.equal((await api('/api/state','GET',undefined,restoredId)).project.title,restoredTitle);
  await page.screenshot({path:path.join(temporary,'desktop-backup-open-failure.png'),fullPage:true});
  await page.unroute('**/api/state');
  await page.locator('.author-form [type="submit"]').click();
  await page.locator('.author-form').waitFor({state:'hidden'});await waitSaved(page);
  assert.equal(await page.evaluate(()=>requestedProject),restoredId);
  assert.equal(await page.locator('#projectTitle').innerText(),restoredTitle);
  assert.match(await page.locator('#toast').innerText(),/恢复副本已打开/);
  assert.equal(await page.locator('#chapterBody').inputValue(),body);
  await page.locator('#chapterBody').fill(body+'\n重新打开副本后继续写。');await waitSaved(page);
  await page.reload();await waitSaved(page);await page.locator('[data-view="editor"]').click();
  assert.equal(await page.locator('#chapterBody').inputValue(),body+'\n重新打开副本后继续写。');
  assert.equal((await api('/api/state','GET',undefined,sourceId)).chapters[0].body,body);
  assert.equal((await api('/api/state','GET',undefined,sourceId)).project.title,title);
  assert.equal(restores,1);page.off('request',observe);
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-copy="${restoredId}"]`).click();
  const copyTitle=await page.locator('#copyTitle').inputValue();
  assert.equal(Array.from(copyTitle).length,200);assert.ok(copyTitle.endsWith(' · 副本'));
  await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(previous=>requestedProject!==previous,restoredId);await waitSaved(page);
  const copiedId=await page.evaluate(()=>requestedProject);
  assert.equal((await api('/api/state','GET',undefined,copiedId)).project.title,copyTitle);
  assert.equal(providerRequests.length,providerBefore);
  results.backup_reopen={source_project:sourceId,restored_project:restoredId,copied_project:copiedId,restore_requests:restores,
   project_created_once:true,source_preserved:true,reloaded_and_saved:true,model_requests:0};
 });
 await test('恢复成功回复后原作和副本读取同时失败，重开仍只有一次恢复写入',async()=>{
  const page=workspace.page,sourceId=await page.evaluate(()=>requestedProject),providerBefore=providerRequests.length;
  const source=await api('/api/state','GET',undefined,sourceId);
  await page.locator('[data-view="backups"]').click();await page.locator('[data-action="createBackup"]').click();
  await page.locator('[data-backup-restore]').waitFor();
  const projectsBefore=(await api('/api/projects')).projects.length;
  let restores=0,failedReads=0;
  const observe=request=>{if(request.method()==='POST'&&/\/api\/backups\/[^/]+\/restore$/.test(request.url()))restores++};
  page.on('request',observe);
  await page.route('**/api/state',async route=>{
   if(route.request().method()==='GET'){failedReads++;return route.abort('failed')}
   return route.continue();
  });
  await page.locator('[data-backup-restore]').first().click();await page.locator('#restoreConfirm').check();
  await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.author-form .form-error')?.textContent.includes('副本已建立'));
  const restoredId=await page.evaluate(()=>requestedProject);
  assert.notEqual(restoredId,sourceId);assert.ok(failedReads>=2);
  assert.equal(restores,1);assert.equal((await api('/api/projects')).projects.length,projectsBefore+1);
  assert.doesNotMatch(await page.locator('#toast').innerText(),/恢复副本已打开/);
  await page.unroute('**/api/state');await page.locator('.author-form [type="submit"]').click();
  await page.locator('.author-form').waitFor({state:'hidden'});await waitSaved(page);
  assert.equal(await page.evaluate(()=>requestedProject),restoredId);
  assert.equal(restores,1);assert.equal((await api('/api/projects')).projects.length,projectsBefore+1);
  assert.deepEqual((await api('/api/state','GET',undefined,sourceId)).chapters,source.chapters);
  assert.deepEqual((await api('/api/state','GET',undefined,restoredId)).chapters.map(c=>c.body),source.chapters.map(c=>c.body));
  assert.equal(providerRequests.length,providerBefore);page.off('request',observe);
  results.backup_post_commit_reopen={source_project:sourceId,restored_project:restoredId,failed_state_reads:failedReads,
   restore_requests:restores,project_created_once:true,source_preserved:true,model_requests:0};
 });
 await test('含emoji的名称按完整字符恢复，刷新与默认复制都能打开且保留原稿',async()=>{
  const page=workspace.page,previousProject=await page.evaluate(()=>requestedProject);
  const prefix='书'.repeat(192)+'😀',title=prefix+'卷'.repeat(7),body='含emoji作品名的原稿。副本和原作分别保存。';
  const providerBefore=providerRequests.length;
  await page.locator('[data-view="library"]').click();await page.locator('[data-action="createProject"]').click();
  await page.locator('#newBookTitle').fill(prefix+'卷'.repeat(6));await page.locator('#newBookTitle').press('End');
  await page.keyboard.insertText('卷');assert.equal(await page.locator('#newBookTitle').inputValue(),title);
  await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(previous=>requestedProject!==previous,previousProject);await waitSaved(page);
  const sourceId=await page.evaluate(()=>requestedProject);
  await page.locator('[data-view="editor"]').click();await page.locator('#chapterBody').fill(body);await waitSaved(page);
  await page.locator('[data-view="backups"]').click();await page.locator('[data-action="createBackup"]').click();
  await page.locator('[data-backup-restore]').waitFor();await page.locator('[data-backup-restore]').first().click();
  await page.locator('#restoreConfirm').check();await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(previous=>requestedProject!==previous,sourceId);
  await page.locator('.author-form').waitFor({state:'hidden'});await waitSaved(page);
  const restoredId=await page.evaluate(()=>requestedProject),restoredTitle=prefix+' · 恢复副本';
  assert.equal(Array.from(restoredTitle).length,200);assert.equal(restoredTitle.length,201);
  assert.equal(await page.locator('#projectTitle').innerText(),restoredTitle);
  await page.reload();await waitSaved(page);await page.locator('[data-view="editor"]').click();
  assert.equal(await page.locator('#chapterBody').inputValue(),body);
  await page.locator('#chapterBody').fill(body+'\n恢复后新写的一句。');await waitSaved(page);
  await page.locator('[data-view="library"]').click();await page.locator(`[data-project-copy="${restoredId}"]`).click();
  const initialCopyTitle=await page.locator('#copyTitle').inputValue();
  await page.locator('#copyTitle').focus();
  await page.evaluate(()=>document.querySelector('#copyTitle').setSelectionRange(0,1));
  await page.keyboard.insertText('新');const copyTitle=await page.locator('#copyTitle').inputValue();
  assert.equal(copyTitle,'新'+initialCopyTitle.slice(1));
  assert.equal(Array.from(copyTitle).length,200);assert.ok(copyTitle.includes('😀'));assert.ok(copyTitle.endsWith(' · 副本'));
  const projectsBefore=(await api('/api/projects')).projects.length;
  await page.locator('#copyTitle').fill(copyTitle+'额');await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.author-form .form-error')?.textContent.includes('不能超过200字'));
  assert.equal((await api('/api/projects')).projects.length,projectsBefore);
  await page.locator('#copyTitle').fill(copyTitle);
  await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(previous=>requestedProject!==previous,restoredId);await waitSaved(page);
  const copiedId=await page.evaluate(()=>requestedProject);
  await page.locator('[data-view="editor"]').click();
  assert.equal(await page.locator('#chapterBody').inputValue(),body+'\n恢复后新写的一句。');
  assert.equal(await page.locator('#projectTitle').innerText(),copyTitle);
  assert.equal((await api('/api/state','GET',undefined,sourceId)).chapters[0].body,body);
  assert.equal((await api('/api/state','GET',undefined,sourceId)).project.title,title);
  assert.equal(providerRequests.length,providerBefore);
  await page.screenshot({path:path.join(temporary,'desktop-backup-restored.png'),fullPage:true});
  results.backup_unicode={source_project:sourceId,restored_project:restoredId,copied_project:copiedId,title_characters:200,
   title_utf16_units:201,source_preserved:true,reloaded_and_saved:true,model_requests:0};
 });
 await test('200字作品名在大小电脑窗口中保留写作入口，导航独立滚动且不遮挡正文',async()=>{
  const page=workspace.page,title=await page.locator('#projectTitle').textContent();
  assert.equal(Array.from(title).length,200);
  const measurements=[];
  for(const viewport of [{width:1440,height:1000},{width:1280,height:900},{width:1280,height:800}]){
   await page.setViewportSize(viewport);
   await page.evaluate(async()=>{
    window.scrollTo(0,0);document.querySelector('.nav-list').scrollTop=0;
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   });
   const layout=await page.evaluate(()=>{
    const rect=selector=>{const b=document.querySelector(selector).getBoundingClientRect();return {top:b.top,bottom:b.bottom,left:b.left,right:b.right,height:b.height}};
    const hit=selector=>{
     const element=document.querySelector(selector),b=element.getBoundingClientRect(),x=b.left+b.width/2,y=b.top+b.height/2;
     return b.top>=0&&b.bottom<=innerHeight&&element.contains(document.elementFromPoint(x,y));
    };
    const title=document.querySelector('#projectTitle'),nav=document.querySelector('.nav-list');
    return {title:title.textContent,full_title:title.title,title_height:title.getBoundingClientRect().height,
     title_line_height:parseFloat(getComputedStyle(title).lineHeight),header:rect('.topbar'),sidebar:rect('.sidebar'),
     main:rect('#main'),page_scroll:scrollY,nav_scroll:nav.scrollTop,nav_height:nav.clientHeight,nav_content_height:nav.scrollHeight,
     library_visible:hit('[data-view="library"]'),editor_visible:hit('[data-view="editor"]'),edit_visible:hit('[data-workbench-project]'),
     horizontal_overflow:document.documentElement.scrollWidth>innerWidth};
   });
   assert.equal(layout.title,title);assert.equal(layout.full_title,title);
   assert.ok(layout.title_height<=layout.title_line_height*2+.5);
   assert.equal(layout.page_scroll,0);assert.equal(layout.nav_scroll,0);
   assert.equal(layout.header.top,0);assert.equal(layout.header.bottom,64);assert.equal(layout.sidebar.top,64);
   assert.ok(layout.sidebar.bottom<=viewport.height+.5);
   assert.ok(layout.main.left>=layout.sidebar.right);
   assert.equal(layout.library_visible,true);assert.equal(layout.editor_visible,true);assert.equal(layout.edit_visible,true);
   assert.equal(layout.horizontal_overflow,false);assert.ok(layout.nav_height>200);
   await page.evaluate(async()=>{
    const nav=document.querySelector('.nav-list');nav.scrollTop=nav.scrollHeight;
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   });
   const last=await page.evaluate(()=>{
    const nav=document.querySelector('.nav-list'),element=nav.querySelector('[data-view="references"]'),b=element.getBoundingClientRect(),n=nav.getBoundingClientRect();
    return {page_scroll:scrollY,nav_scroll:nav.scrollTop,visible:b.top>=n.top&&b.bottom<=n.bottom&&b.bottom<=innerHeight,
     hit:element.contains(document.elementFromPoint(b.left+b.width/2,b.top+b.height/2))};
   });
   assert.equal(last.page_scroll,0);assert.equal(last.visible,true);assert.equal(last.hit,true);
   if(layout.nav_content_height>layout.nav_height)assert.ok(last.nav_scroll>0);
   await page.locator('[data-view="references"]').click();
   await page.locator('#referenceList').waitFor();
   await page.locator('[data-view="editor"]').click();await page.locator('#chapterBody').waitFor();
   await page.evaluate(async()=>{
    window.scrollTo(0,220);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   });
   const scrolled=await page.evaluate(()=>{
    const editor=document.querySelector('.workbench-manuscript');editor.scrollTop=220;
    return {page_scroll:scrollY,editor_scroll:editor.scrollTop,sidebar_top:document.querySelector('.sidebar').getBoundingClientRect().top,
     header_top:document.querySelector('.topbar').getBoundingClientRect().top};
   });
   assert.equal(scrolled.page_scroll,0);assert.ok(scrolled.editor_scroll>0);assert.equal(scrolled.sidebar_top,64);assert.equal(scrolled.header_top,0);
   measurements.push({viewport,layout,last,scrolled});
   await page.evaluate(async()=>{
    window.scrollTo(0,0);document.querySelector('.nav-list').scrollTop=0;
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   });
   await page.screenshot({path:path.join(temporary,viewport.height===1000?'desktop-long-title-navigation.png':
    viewport.height===900?'compact-desktop-long-title-navigation.png':'short-desktop-long-title-navigation.png'),fullPage:false});
  }
  results.long_title_navigation={title_characters:200,measurements};
  await page.setViewportSize({width:1440,height:1000});
 });
 await test('设定的揭晓章节和知情人物经页面保存刷新后仍限制实际写作材料',async()=>{
  const page=workspace.page,project=await page.evaluate(()=>requestedProject),providerBefore=providerRequests.length;
  await page.locator('[data-view="world"]').click();
  const createPerson=async name=>{
   await page.locator('[data-action="newRecord"]').click();
   await page.locator('#recordName').fill(name);await page.locator('#recordDesc').fill('范围回归专用人物。');
   await page.locator('.author-form [type="submit"]').click();await page.locator('.author-form').waitFor({state:'hidden'});await waitSaved(page);
   return (await api('/api/state','GET',undefined,project)).records.find(item=>item.name===name).id;
  };
  const hero=await createPerson('范围作者'),villain=await createPerson('范围访客'),secret='范围密语：银窗只在第九夜打开。';
  await page.locator('[data-action="newRecord"]').click();await page.locator('#recordType').fill('秘密');
  await page.locator('#recordName').fill('银窗秘密');await page.locator('#recordDesc').fill(secret);
  await page.locator('#recordAvailable').fill('9');await page.locator('#recordVisibility').selectOption('people');
  await page.locator('#recordKnown').selectOption([villain]);
  await page.locator('.author-form [type="submit"]').click();await page.locator('.author-form').waitFor({state:'hidden'});await waitSaved(page);
  const record=(await api('/api/state','GET',undefined,project)).records.find(item=>item.name==='银窗秘密');
  assert.deepEqual(record.known_by,[villain]);assert.equal(record.knowledge_scope.available_from,'9');
  await page.reload();await waitSaved(page);await page.locator('[data-view="world"]').click();
  const card=page.locator('.record-card').filter({has:page.locator(`[data-record="${record.id}"]`)});
  assert.match(await card.locator('.record-scope').innerText(),/第 9 章起.*范围访客/);
  await page.locator(`[data-record="${record.id}"]`).click();
  assert.equal(await page.locator('#recordAvailable').inputValue(),'9');
  assert.deepEqual(await page.locator('#recordKnown').evaluate(element=>[...element.selectedOptions].map(option=>option.value)),[villain]);
  await page.locator('#recordUntil').fill('1');await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.author-form .form-error')?.textContent.includes('不能早于'));
  assert.equal((await api('/api/state','GET',undefined,project)).records.find(item=>item.id===record.id).knowledge_scope.valid_end,'');
  await page.locator('#recordUntil').fill('');await page.locator('.author-form [data-close]').click();
  await page.locator('[data-view="editor"]').click();await page.locator('details.chapter-plan > summary').click();
  await page.locator('#chapterPov').fill('范围作者');await waitSaved(page);
  const chapterId=await page.evaluate(()=>activeChapter);
  const preview=await api('/api/ai/prompt-preview','POST',{task:'continue',chapter_id:chapterId,instruction:'银窗秘密'},project);
  assert.ok(!JSON.stringify(preview).includes(secret));
  await page.locator('[data-view="world"]').click();await page.locator(`[data-record="${record.id}"]`).click();
  await page.locator('#recordAvailable').fill('1');await page.locator('#recordKnown').selectOption([hero]);
  await page.locator('.author-form [type="submit"]').click();await page.locator('.author-form').waitFor({state:'hidden'});await waitSaved(page);
  const visible=await api('/api/ai/prompt-preview','POST',{task:'continue',chapter_id:chapterId,instruction:'银窗秘密'},project);
  assert.ok(JSON.stringify(visible).includes(secret));
  const downloadEvent=page.waitForEvent('download');await page.locator('#exportBtn').click();const download=await downloadEvent;
  const exportedPath=path.join(temporary,'record-scope-manuscript.json');await download.saveAs(exportedPath);
  const exported=JSON.parse(fs.readFileSync(exportedPath,'utf8')).records.find(item=>item.id===record.id);
  assert.equal(exported.knowledge_scope.available_from,'1');assert.deepEqual(exported.known_by,[hero]);
  await page.locator(`[data-record="${record.id}"]`).click();
  await page.screenshot({path:path.join(temporary,'desktop-record-knowledge-scope.png'),fullPage:false});
  await page.locator('.author-form [data-close]').click();
  assert.equal(providerRequests.length,providerBefore);
  results.record_scope={record_id:record.id,refresh_preserved:true,hidden_before_reveal:true,visible_after_author_change:true,
   json_scope_preserved:true,invalid_range_rejected:true,model_requests:0};
 });
 await workspace.page.locator('[data-view="library"]').click();
 await workspace.page.locator(`[data-project-open="${results.restored_project}"]`).click();await waitSaved(workspace.page);
 await workspace.page.locator('[data-view="editor"]').click();
 await workspace.page.screenshot({path:path.join(temporary,'desktop-editor.png'),fullPage:true});
 assert.deepEqual(workspace.errors,[]);await workspace.context.close();
 results.passed=true;
}

main().catch(error=>{results.failure={message:error.message,stack:error.stack,server_log:serverLog.slice(-5000)};process.exitCode=1})
 .finally(async()=>{
  fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(results,null,2)+'\n');
  if(browser)await browser.close();
  if(app&&app.exitCode===null){const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill('SIGTERM');await Promise.race([stopped,pause(2000)]);if(app.exitCode===null)app.kill('SIGKILL');await stopped}
  if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
  fs.writeFileSync(path.join(temporary,'results.json'),JSON.stringify(results,null,2)+'\n');
  console.log(JSON.stringify(results,null,2));
 });
