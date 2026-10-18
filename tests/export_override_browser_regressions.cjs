'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const net=require('node:net');
const crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require(process.env.MOYU_PLAYWRIGHT_MODULE||'playwright');

const root=path.resolve(__dirname,'..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-export-override-'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const results={passed:false,scope:'真实桌面Chrome、隔离SQLite、实际下载中文例外文稿',cases:[],artifacts:temporary,
 external_model_calls:0,source_files:['frontend/app.js','frontend/author-workspace.js','backend/server.py',
  'tests/export_override_browser_regressions.cjs'].map(file=>({path:file,sha256:hash(fs.readFileSync(path.join(root,file)))}))};
let app,browser,base,serverLog='',markdownSource;

async function freePort(){
 const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
 const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));return port;
}
async function api(route,method='GET',body){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Moyu-Project':'default-project'},
  body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);
 return data;
}
async function test(name,run){
 console.log(JSON.stringify({test:name,state:'running'}));await run();
 results.cases.push({name,passed:true});console.log(JSON.stringify({test:name,state:'passed'}));
}
async function publicationEvents(){
 return (await api('/api/events?limit=200')).events.filter(event=>event.event_type==='publication.exported');
}
async function finalDialog(page){
 const dialog=page.locator('.modal').filter({has:page.locator('h3',{hasText:'终稿检查'})});
 await dialog.waitFor();return dialog;
}
async function overrideInput(page){
 const dialog=await finalDialog(page);await dialog.locator('[data-override]').click();
 const input=page.locator('dialog[data-text-request]');
 await input.waitFor();await input.locator('textarea[data-text-input]').waitFor();
 return input;
}
async function blockedExport(page,format,downloads){
 const before=downloads.length;
 await page.locator(format==='markdown'?'#markdownExportBtn':'#epubExportBtn').click();
 const dialog=await finalDialog(page);
 assert.equal(await dialog.locator('[data-export]').isEnabled(),false);
 const response=await fetch(base+'/api/export/'+format);
 assert.equal(response.status,409);assert.equal(downloads.length,before);
 await dialog.locator('[data-close]').click();
}
async function downloadExport(page,format){
 const pending=page.waitForEvent('download',{timeout:15000});
 await page.locator(format==='markdown'?'#markdownExportBtn':'#epubExportBtn').click();
 const downloaded=await pending;
 assert.equal(await downloaded.failure(),null);
 assert.ok(downloaded.suggestedFilename().endsWith(format==='markdown'?'.md':'.epub'));
 const filename=path.join(temporary,'chinese-override.'+(format==='markdown'?'md':'epub'));
 await downloaded.saveAs(filename);assert.ok(fs.statSync(filename).size>100);
 return filename;
}

async function main(){
 const appPort=await freePort();base=`http://127.0.0.1:${appPort}`;
 const environment={...process.env};for(const key of Object.keys(environment))if(key.startsWith('MOYU_'))delete environment[key];
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,env:{...environment,
  MOYU_DB:path.join(temporary,'test.sqlite3'),MOYU_HOST:'127.0.0.1',MOYU_PORT:String(appPort),
  MOYU_AI_BASE_URL:'',MOYU_AI_MODEL:'',MOYU_AI_API_KEY:'',MOYU_AI_DAILY_QUOTA:'0',MOYU_SECRET_KEY:'',
  MOYU_BACKUP_DIR:path.join(temporary,'backups'),MOYU_CREDENTIALS_DIR:path.join(temporary,'credentials')},
  stdio:['ignore','ignore','pipe']});
 app.stderr.on('data',chunk=>serverLog+=chunk.toString());
 for(let attempt=0;attempt<200;attempt++){
  try{if((await fetch(base+'/api/health')).ok)break}catch{}
  if(app.exitCode!==null)throw new Error('isolated server exited');
  if(attempt===199)throw new Error('isolated server unavailable');await pause(40);
 }
 const seeded=await api('/api/state');
 seeded.project.title='中文例外导出合成作品';seeded.project.summary='作者用于检查导出理由与正文，不属于真实作品。';
 seeded.chapters=seeded.chapters.slice(0,1);
 const body='合成作者把铜钥匙放回桌面，窗外的钟声响起。她确认钥匙的位置，然后关好窗。'.repeat(5);
 seeded.chapters[0].title='第一章合成原稿';seeded.chapters[0].body=body;
 seeded.chapters[0].status='草稿';seeded.chapters[0].review_status='unreviewed';
 seeded.records=[];seeded.memory=[];seeded.plot={setup:[],develop:[],payoff:[]};
 await api('/api/state','PUT',seeded);
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
 const page=await context.newPage(),pageErrors=[],downloads=[];
 page.on('pageerror',error=>pageErrors.push(error.message));page.on('download',download=>downloads.push(download.suggestedFilename()));
 await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
 await page.goto(base);await page.waitForFunction(()=>backendOnline);
 await test('没有作者理由时真实页面和下载接口都阻断未审稿文稿',async()=>{
  await blockedExport(page,'markdown',downloads);await blockedExport(page,'epub',downloads);
  assert.equal(downloads.length,0);assert.equal((await publicationEvents()).length,0);
 });
 await test('软件内理由框获得焦点，取消和Esc都不保存理由也不允许导出',async()=>{
  await page.locator('#finalCheckBtn').click();
  for(const action of ['cancel','escape']){
   const input=await overrideInput(page);
   const field=input.locator('textarea[data-text-input]');
   assert.equal(await field.evaluate(element=>element===document.activeElement),true);
   await field.fill('这是一条尚未提交的合成作者覆盖理由。');
   if(action==='cancel')await input.locator('[data-text-cancel]').click();
   else await field.press('Escape');
   await input.waitFor({state:'detached'});
   const dialog=await finalDialog(page);
   assert.equal(await dialog.locator('[data-export]').isEnabled(),false);
   assert.equal(await page.evaluate(()=>finalOverride()),null);
   assert.equal(downloads.length,0);assert.equal((await publicationEvents()).length,0);
  }
  await (await finalDialog(page)).locator('[data-close]').click();
 });
 await test('填写过程中稿件或作品变化会撤销输入，不能把旧理由保存到新范围',async()=>{
  const snapshot=await page.evaluate(()=>({project:requestedProject,body:state.chapters[0].body}));
  const reasonsBefore=await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.includes(':final-override:')).sort());
  for(const change of ['manuscript','project']){
   await page.locator('#finalCheckBtn').click();const input=await overrideInput(page);
   await input.locator('textarea[data-text-input]').fill('此理由只能用于弹窗打开时的合成作品与稿件。');
   await page.evaluate(kind=>{
    if(kind==='manuscript')state.chapters[0].body+='\n等待填写时收到的新稿。';
    else requestedProject='synthetic-switched-project';
   },change);
   await input.locator('[data-text-submit]').click();
   await input.waitFor({state:'detached'});
   assert.equal(await page.evaluate(()=>finalOverride()),null);
   assert.deepEqual(await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.includes(':final-override:')).sort()),reasonsBefore);
   assert.equal(downloads.length,0);
   await page.evaluate(original=>{
    requestedProject=original.project;state.chapters[0].body=original.body;
   },snapshot);
   await page.evaluate(()=>document.querySelectorAll('.modal-backdrop').forEach(element=>element.remove()));
  }
  assert.equal((await publicationEvents()).length,0);
 });
 await test('七个中文字符或七个emoji的理由仍被页面阻断，按完整字符计算',async()=>{
  await page.locator('#finalCheckBtn').click();const dialog=await finalDialog(page);
  results.short_reason_lengths=[];
  for(const short of ['作者已经核对稿','😀'.repeat(7)]){
   assert.equal(Array.from(short).length,7);const input=await overrideInput(page);
   await input.locator('textarea[data-text-input]').fill(short);
   await input.locator('[data-text-submit]').click();
   assert.match(await page.locator('#toast').innerText(),/至少需要 8 个字符/);
   assert.equal(await dialog.locator('[data-export]').isEnabled(),false);
   assert.equal(await page.evaluate(()=>finalOverride()),null);assert.equal(downloads.length,0);
   results.short_reason_lengths.push({codepoints:Array.from(short).length,utf16_units:short.length,blocked:true});
  }
 });
 const reason='作者已核对全文，导出供校对使用，保留原句与 100% 进度。😀';
 await test('从终稿窗口填写中文理由后可实际下载Markdown，审计理由原样保留',async()=>{
  const input=await overrideInput(page);
  await input.locator('textarea[data-text-input]').fill(reason);
  await input.locator('[data-text-submit]').click();
  const verified=await finalDialog(page);
  await verified.locator('.override-note').waitFor();
  assert.match(await verified.locator('.override-note').innerText(),/作者已核对全文/);
  await verified.locator('[data-close]').click();
  const filename=await downloadExport(page,'markdown');
  const text=fs.readFileSync(filename,'utf8');assert.ok(text.includes(body));
  const markers=[...text.matchAll(/<!-- moyu-manuscript-v1 ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) -->/g)];
  assert.equal(markers.length,1);markdownSource=Buffer.from(markers[0][2],'base64');
  assert.equal(hash(markdownSource),markers[0][1]);
  const manuscript=JSON.parse(markdownSource);
  assert.deepEqual(manuscript.chapters,[{title:'第一章合成原稿',body}]);
  const events=await publicationEvents();assert.equal(events.length,1);
  assert.equal(events[0].payload_json.format,'markdown');
  assert.equal(events[0].payload_json.author_override.reason,reason);
  results.markdown={bytes:fs.statSync(filename).size,source_sha256:hash(markdownSource),audit_reason:events[0].payload_json.author_override.reason};
 });
 await test('同一中文理由可实际下载EPUB，包内原稿及审计与Markdown一致',async()=>{
  const filename=await downloadExport(page,'epub');
  const inspected=JSON.parse(execFileSync(process.env.MOYU_TEST_PYTHON||'python3',[
   '-c',
   'import hashlib,json,sys,zipfile\n'+
   'with zipfile.ZipFile(sys.argv[1]) as archive:\n'+
   ' assert archive.read("mimetype")==b"application/epub+zip"\n'+
   ' source=archive.read("META-INF/moyu-manuscript.json")\n'+
   ' manifest=json.loads(archive.read("META-INF/moyu-export-manifest.json"))\n'+
   ' assert manifest["manuscript_source"]["sha256"]==hashlib.sha256(source).hexdigest()\n'+
   ' print(json.dumps({"manuscript":json.loads(source),"source_sha256":hashlib.sha256(source).hexdigest(),"manifest":manifest},ensure_ascii=False))\n',
   filename],{encoding:'utf8'}));
  assert.equal(inspected.source_sha256,hash(markdownSource));
  assert.deepEqual(inspected.manuscript,JSON.parse(markdownSource));
  assert.equal(inspected.manifest.author_override.reason,reason);
  assert.equal(inspected.manifest.finalization.overridden,true);
  const events=await publicationEvents();assert.equal(events.length,2);
  assert.deepEqual(events.map(event=>event.payload_json.format).sort(),['epub','markdown']);
  assert.ok(events.every(event=>event.payload_json.author_override.reason===reason));
  results.epub={bytes:fs.statSync(filename).size,source_sha256:inspected.source_sha256,
   manifest_reason:inspected.manifest.author_override.reason,audit_reason:events[0].payload_json.author_override.reason};
  await page.locator('#finalCheckBtn').click();const dialog=await finalDialog(page);
  await dialog.locator('.override-note').waitFor();
  const screenshot=process.env.MOYU_EXPORT_OVERRIDE_SCREENSHOT||path.join(temporary,'export-override-desktop.png');
  fs.mkdirSync(path.dirname(screenshot),{recursive:true});await page.screenshot({path:screenshot,fullPage:false});
  results.screenshot=path.resolve(screenshot).startsWith(root+path.sep)?path.relative(root,path.resolve(screenshot)):path.basename(screenshot);
  await dialog.locator('[data-close]').click();
 });
 await test('导出后改稿会使旧中文理由失效，两种格式重新要求作者确认',async()=>{
  await page.locator('[data-view="editor"]').click();
  await page.locator('#chapterBody').fill(body+'\n改稿后的合成新句子。');
  await page.waitForFunction(()=>!isDirty()&&!syncInFlight);
  await blockedExport(page,'markdown',downloads);await blockedExport(page,'epub',downloads);
  assert.equal(downloads.length,2);assert.equal((await publicationEvents()).length,2);
  assert.equal((await api('/api/state')).chapters[0].body,body+'\n改稿后的合成新句子。');
 });
 assert.deepEqual(pageErrors,[]);results.downloads=downloads;
 results.source_unchanged=results.source_files.every(file=>file.sha256===hash(fs.readFileSync(path.join(root,file.path))));
 assert.equal(results.source_unchanged,true);results.passed=true;await context.close();
}

main().catch(error=>{
 results.failure={message:error.message.split(root).join('<workspace>').split(temporary).join('<isolated>'),
  server_log:serverLog.slice(-4000).split(root).join('<workspace>').split(temporary).join('<isolated>')};
 process.exitCode=1;
}).finally(async()=>{
 if(browser)await browser.close();
 if(app&&app.exitCode===null){
  const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill('SIGTERM');
  await Promise.race([stopped,pause(2000)]);if(app.exitCode===null)app.kill('SIGKILL');await stopped;
 }
 const reportPath=process.env.MOYU_EXPORT_OVERRIDE_REPORT||path.join(temporary,'results.json');
 const saved=process.env.MOYU_EXPORT_OVERRIDE_REPORT?{...results,artifacts:'Private isolated artifacts retained locally; screenshots are referenced in this report.'}:results;
 fs.mkdirSync(path.dirname(reportPath),{recursive:true});fs.writeFileSync(reportPath,JSON.stringify(saved,null,2)+'\n');
 console.log(JSON.stringify(results,null,2));
});
