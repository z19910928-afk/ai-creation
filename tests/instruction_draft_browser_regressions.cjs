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
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-instruction-draft-'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const results={passed:false,scope:'真实桌面Chrome、隔离SQLite、本机免费模拟提供方',cases:[],artifacts:temporary,
 external_model_calls:0,source_files:['app.js','tests/instruction_draft_browser_regressions.cjs'].map(file=>({
  path:file,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')}))};
const providerRequests=[];
let app,provider,browser,base,serverLog='',releaseProvider;

async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',body,project='default-project'){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':project},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);
 return data;
}
async function test(name,run){
 console.log(JSON.stringify({test:name,state:'running'}));await run();
 results.cases.push({name,passed:true});console.log(JSON.stringify({test:name,state:'passed'}));
}
async function draft(page){
 return page.evaluate(()=>({task:document.querySelector('#aiTask').value,instruction:document.querySelector('#aiInstruction').value}));
}
async function fillDraft(page,task,instruction){
 await page.locator('#aiTask').selectOption(task);await page.locator('#aiInstruction').fill(instruction);
}
async function enterEditor(page){
 await page.locator('[data-view="editor"]').click();await page.locator('#aiInstruction').waitFor();
}
async function openProject(page,id){
 await page.locator('[data-view="library"]').click();await page.locator(`[data-project-open="${id}"]`).click();
 await page.waitForFunction(project=>backendOnline&&requestedProject===project&&state.project.id===project,id);
 await enterEditor(page);
}
async function reloadEditor(page){
 await page.reload();await page.waitForFunction(()=>backendOnline);await enterEditor(page);
}

async function main(){
 const providerGate=new Promise(resolve=>releaseProvider=resolve);
 provider=http.createServer((request,response)=>{
  let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',async()=>{
   const input=JSON.parse(raw||'{}');providerRequests.push(input);await providerGate;
   if(response.destroyed)return;
   const text='合成作者把铜钥匙放回桌面，窗外的钟声响起。'.repeat(30);
   response.writeHead(200,{'Content-Type':'text/event-stream'});
   response.end(`data: ${JSON.stringify({choices:[{delta:{content:text},finish_reason:'stop'}],
    model:'instruction-draft-mock',usage:{prompt_tokens:20,completion_tokens:20,total_tokens:40}})}\n\ndata: [DONE]\n\n`);
  });
 });
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
 const providerPort=provider.address().port,appPort=await freePort();base=`http://127.0.0.1:${appPort}`;
 const environment={...process.env};for(const key of Object.keys(environment))if(key.startsWith('MOYU_'))delete environment[key];
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,env:{...environment,
  MOYU_DB:path.join(temporary,'test.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(appPort),
  MOYU_AI_BASE_URL:`http://127.0.0.1:${providerPort}/v1`,MOYU_AI_MODEL:'instruction-draft-mock',
  MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'10',MOYU_SECRET_KEY:'',
  MOYU_BACKUP_DIR:path.join(temporary,'backups'),MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let attempt=0;attempt<200;attempt++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  if(app.exitCode!==null)throw new Error('isolated server exited');
  if(attempt===199)throw new Error('isolated server unavailable');await pause(40);
 }
 const seeded=await api('/api/state'),firstId=seeded.chapters[0].id,secondId='instruction_second';
 seeded.project.title='创作要求隔离测试甲';
 seeded.chapters[0].body='合成作者把铜钥匙放在桌面。窗外钟声响起。'.repeat(6);
 seeded.chapters[0].chapter_goal='保管钥匙';seeded.chapters[0].scene_plan='门口相遇';seeded.chapters[0].pov='合成作者';
 seeded.chapters.push({id:secondId,title:'第二章合成稿',body:'第二章保留自己的原稿。',status:'草稿'});
 await api('/api/state','PUT',seeded);
 const created=await api('/api/projects','POST',{title:'创作要求隔离测试乙',summary:'另一部合成作品',genre:'测试',goal:50000});
 const secondProject=created.project.id;
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage(),pageErrors=[];
 page.on('pageerror',error=>pageErrors.push(error.message));
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline);await enterEditor(page);
 const firstDraft={task:'polish',instruction:'只改对白节奏，保留铜钥匙去向。😀 不增加剧情。'};
 const secondDraft={task:'scene',instruction:'第二章先记录门口相遇的场景出口。'};
 const otherDraft={task:'brainstorm',instruction:'乙书只讨论钟楼，不引用甲书铜钥匙。'};

 await test('切页查看设定与刷新后保留当前章任务及完整要求',async()=>{
  await fillDraft(page,firstDraft.task,firstDraft.instruction);
  await page.locator('[data-view="world"]').click();await enterEditor(page);assert.deepEqual(await draft(page),firstDraft);
  await reloadEditor(page);assert.deepEqual(await draft(page),firstDraft);
 });
 await test('不同章节任务草稿互相隔离，刷新仍回到该章草稿',async()=>{
  await page.locator(`[data-chapter="${secondId}"]`).click();assert.deepEqual(await draft(page),{task:'continue',instruction:''});
  await fillDraft(page,secondDraft.task,secondDraft.instruction);await reloadEditor(page);assert.deepEqual(await draft(page),secondDraft);
  await page.locator(`[data-chapter="${firstId}"]`).click();assert.deepEqual(await draft(page),firstDraft);
 });
 await test('两部作品各自保留要求，离开再回来不会串用',async()=>{
  await openProject(page,secondProject);assert.deepEqual(await draft(page),{task:'continue',instruction:''});
  await fillDraft(page,otherDraft.task,otherDraft.instruction);await reloadEditor(page);assert.deepEqual(await draft(page),otherDraft);
  await openProject(page,'default-project');await page.locator(`[data-chapter="${firstId}"]`).click();
  assert.deepEqual(await draft(page),firstDraft);
  await page.locator(`[data-chapter="${secondId}"]`).click();assert.deepEqual(await draft(page),secondDraft);
  await page.locator(`[data-chapter="${firstId}"]`).click();
 });
 await test('同作品同章的合成分支身份具有独立草稿',async()=>{
  await page.evaluate(()=>{globalThis.__draftOriginalBranch=state.branch_id;state.branch_id='instruction_test_branch';render()});
  assert.deepEqual(await draft(page),{task:'continue',instruction:''});
  await fillDraft(page,'check','只核对合成分支的事实。');
  await page.evaluate(()=>{state.branch_id=globalThis.__draftOriginalBranch;render()});assert.deepEqual(await draft(page),firstDraft);
  await page.evaluate(()=>{state.branch_id='instruction_test_branch';render()});
  assert.deepEqual(await draft(page),{task:'check',instruction:'只核对合成分支的事实。'});
  await page.evaluate(()=>{state.branch_id=globalThis.__draftOriginalBranch;render()});
 });
 await test('旧作品已卸载控件的迟到输入和清空事件不会改写任一作品草稿',async()=>{
  await page.evaluate(()=>{globalThis.__detachedDraftControls={task:document.querySelector('#aiTask'),
   instruction:document.querySelector('#aiInstruction'),clear:document.querySelector('#aiInstructionClear')}});
  await openProject(page,secondProject);assert.deepEqual(await draft(page),otherDraft);
  const before=await page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(key=>key.includes(':instruction-draft:')).map(key=>[key,localStorage.getItem(key)])));
  await page.evaluate(()=>{
   const old=globalThis.__detachedDraftControls;old.instruction.value='迟到事件不得写入任一作品';
   old.instruction.dispatchEvent(new Event('input'));old.instruction.dispatchEvent(new Event('change'));
   old.task.value='summary';old.task.dispatchEvent(new Event('change'));old.clear.click();
  });
  assert.deepEqual(await draft(page),otherDraft);
  assert.deepEqual(await page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(key=>key.includes(':instruction-draft:')).map(key=>[key,localStorage.getItem(key)]))),before);
  await openProject(page,'default-project');await page.locator(`[data-chapter="${firstId}"]`).click();assert.deepEqual(await draft(page),firstDraft);
 });
 const programmatic={task:'summary',instruction:'从审稿入口带入的合成要求也要保留。'};
 await test('程序写入要求后重新渲染或立即刷新仍保存原控件身份',async()=>{
  await page.evaluate(value=>{document.querySelector('#aiTask').value=value.task;document.querySelector('#aiInstruction').value=value.instruction;render()},programmatic);
  assert.deepEqual(await draft(page),programmatic);await reloadEditor(page);assert.deepEqual(await draft(page),programmatic);
  await page.evaluate(()=>{document.querySelector('#aiInstruction').value='没有输入事件也要在刷新前保留。'});
  await reloadEditor(page);assert.deepEqual(await draft(page),{task:'summary',instruction:'没有输入事件也要在刷新前保留。'});
 });
 await test('显式清空恢复续写，刷新不复活旧要求且其他章节和作品不受影响',async()=>{
  await page.locator('#aiInstructionClear').click();assert.deepEqual(await draft(page),{task:'continue',instruction:''});
  await page.locator('[data-view="world"]').click();await reloadEditor(page);assert.deepEqual(await draft(page),{task:'continue',instruction:''});
  await page.locator(`[data-chapter="${secondId}"]`).click();assert.deepEqual(await draft(page),secondDraft);
  await openProject(page,secondProject);assert.deepEqual(await draft(page),otherDraft);
  await openProject(page,'default-project');await page.locator(`[data-chapter="${firstId}"]`).click();
 });
 await test('损坏或不支持的草稿被忽略，不影响编辑器和原稿',async()=>{
  for(const malformed of ['{broken','{"task":"unknown","instruction":"不应加载"}','{"task":"continue","instruction":12}']){
   await page.evaluate(value=>{storage.set(instructionDraftKey(),value);render()},malformed);
   assert.deepEqual(await draft(page),{task:'continue',instruction:''});
  }
  assert.equal(await page.locator('#chapterBody').inputValue(),seeded.chapters[0].body);
 });
 await test('浏览器存储失败时同页保留要求并提示，恢复存储后无需重新输入即可补存',async()=>{
  await page.evaluate(()=>{globalThis.__draftStorageSet=storage.set;storage.set=(key,value)=>key.includes(':instruction-draft:')?false:globalThis.__draftStorageSet(key,value)});
  await fillDraft(page,'outline','存储受限时先在当前浏览器保留。');
  assert.match(await page.locator('#toast').innerText(),/无法保存创作要求/);
  await page.locator('[data-view="world"]').click();await enterEditor(page);
  assert.deepEqual(await draft(page),{task:'outline',instruction:'存储受限时先在当前浏览器保留。'});
  await page.evaluate(()=>{storage.set=globalThis.__draftStorageSet});
  await page.locator('[data-view="world"]').click();await reloadEditor(page);
  assert.deepEqual(await draft(page),{task:'outline',instruction:'存储受限时先在当前浏览器保留。'});
 });
 await test('已保存生成任务继续使用原冻结要求，后改草稿和刷新不重发模型',async()=>{
  const original='已冻结要求：保留铜钥匙，只推进门口冲突。',later='后改草稿只规划下一场景，不改变既有任务。';
  await fillDraft(page,'continue',original);await page.locator('#aiGenerate').click();
  await page.waitForFunction(()=>queueJobs.length>0);
  for(let attempt=0;attempt<200&&!providerRequests.length;attempt++)await pause(25);
  assert.equal(providerRequests.length,1);
  await fillDraft(page,'scene',later);await page.locator('[data-view="world"]').click();await reloadEditor(page);
  assert.deepEqual(await draft(page),{task:'scene',instruction:later});
  const frozen=providerRequests[0].messages.map(item=>item.content).join('\n');
  assert.ok(frozen.includes(original));assert.ok(!frozen.includes(later));
  releaseProvider();
  for(let attempt=0;attempt<200;attempt++){
   const jobs=(await api('/api/ai/queue')).jobs;if(jobs[0]?.status==='success')break;
   if(attempt===199)throw new Error('mock task did not finish');await pause(25);
  }
  await page.waitForFunction(()=>queueJobs.some(job=>job.status==='success')&&document.querySelector('#aiAdopt')?.disabled===false);
  assert.equal(providerRequests.length,1);assert.deepEqual(await draft(page),{task:'scene',instruction:later});
 });
 await test('两种电脑尺寸下清空与生成控件可见且互不遮挡',async()=>{
  results.desktop_viewports=[];
  for(const viewport of [{width:1440,height:1000},{width:1280,height:900}]){
   await page.setViewportSize(viewport);await page.locator('.ai-controls').scrollIntoViewIfNeeded();
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const controls=await page.locator('.ai-controls').evaluate(host=>[...host.querySelectorAll('button,input,select')].map(element=>{
    const rect=element.getBoundingClientRect(),hit=document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
    return {id:element.id,x:rect.x,y:rect.y,width:rect.width,height:rect.height,within:rect.x>=0&&rect.right<=innerWidth&&rect.y>=0&&rect.bottom<=innerHeight,
     unobstructed:hit===element||element.contains(hit)};
   }));
   assert.ok(controls.every(control=>control.width>0&&control.height>0&&control.within&&control.unobstructed),JSON.stringify(controls));
   const desktop={viewport,controls};
   const screenshotBase=process.env.MOYU_INSTRUCTION_DRAFT_SCREENSHOT||path.join(temporary,'instruction-draft-desktop.png');
   const requested=path.parse(screenshotBase);
   const screenshot=viewport.width===1440?screenshotBase:
    path.join(requested.dir,`${requested.name}-${viewport.width}x${viewport.height}${requested.ext||'.png'}`);
   fs.mkdirSync(path.dirname(screenshot),{recursive:true});
   await page.screenshot({path:screenshot,fullPage:false});
   desktop.screenshot=path.resolve(screenshot).startsWith(root+path.sep)?path.relative(root,path.resolve(screenshot)):path.basename(screenshot);
   results.desktop_viewports.push(desktop);
  }
 });
 assert.deepEqual(pageErrors,[]);assert.equal(providerRequests.length,1);
 results.local_mock_model_requests=providerRequests.length;results.branch_identity_test='Synthetic frontend branch identity; no branch-management UI claim.';
 results.source_unchanged=results.source_files.every(file=>file.sha256===crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file.path))).digest('hex'));
 assert.equal(results.source_unchanged,true);results.passed=true;await context.close();
}

main().catch(error=>{
 results.failure={message:error.message,server_log:serverLog.slice(-4000).split(root).join('<workspace>').split(temporary).join('<isolated>')};
 process.exitCode=1;
}).finally(async()=>{
 if(releaseProvider)releaseProvider();
 if(browser)await browser.close();
 if(app&&app.exitCode===null){
  const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill('SIGTERM');
  await Promise.race([stopped,pause(2000)]);if(app.exitCode===null)app.kill('SIGKILL');await stopped;
 }
 if(provider)await new Promise(resolve=>{provider.close(resolve);provider.closeAllConnections()});
 const reportPath=process.env.MOYU_INSTRUCTION_DRAFT_REPORT||path.join(temporary,'results.json');
 const saved=process.env.MOYU_INSTRUCTION_DRAFT_REPORT?{...results,artifacts:'Private isolated artifacts retained locally; screenshots are referenced in this report.'}:results;
 fs.mkdirSync(path.dirname(reportPath),{recursive:true});fs.writeFileSync(reportPath,JSON.stringify(saved,null,2)+'\n');
 console.log(JSON.stringify(results,null,2));
});
