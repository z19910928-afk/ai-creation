'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.MOYU_PLAYWRIGHT_MODULE||'playwright');

const root=path.resolve(__dirname,'..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'moyu-search-browser-'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let app,browser,base,serverLog='';
const results={passed:false,scope:'电脑端全文搜索、隔离 SQLite、本机模拟数据',cases:[],artifacts:temporary};

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
 if(!response.ok)throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);
 return data;
}
async function test(name,fn){
 console.log(JSON.stringify({test:name,state:'running'}));
 await fn();
 results.cases.push({name,passed:true});
 console.log(JSON.stringify({test:name,state:'passed'}));
}
async function search(page,query){
 await page.locator('#searchInput').fill(query);
 await page.locator('[data-action="search"]').click();
}

async function main(){
 const port=await freePort();
 base=`http://127.0.0.1:${port}`;
 app=spawn(process.env.MOYU_TEST_PYTHON||'python3',['server.py'],{cwd:root,
  env:{...process.env,MOYU_DB:path.join(temporary,'search.sqlite3'),MOYU_HOST:'127.0.0.1',
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
 const seeded=await api('/api/state');
 const chapter=await api('/api/chapters','POST',{
  title:'钟楼来信',body:'雨夜😀钟楼里，林岚把旧钥匙藏进左靴。她听见北塔的钟声。',status:'进行中',
 });
 const record=await api('/api/records','POST',{
  type:'物品',name:'旧钥匙',aliases:['月牙密钥'],description:'能打开北塔侧门。',
 });
 browser=await chromium.launch({headless:true,executablePath:process.env.MOYU_CHROME_PATH||undefined});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage();
 const pageErrors=[];
 page.on('pageerror',error=>pageErrors.push(error.message));
 await page.goto(base);
 await page.waitForFunction(()=>backendOnline===true);
 await page.locator('[data-view="search"]').click();

 await test('后端搜索失败时实际回退到本地草稿并可搜别名',async()=>{
  await page.route('**/api/search*',route=>{
   if(new URL(route.request().url()).searchParams.get('q')==='月牙密钥')return route.abort('failed');
   return route.continue();
  });
  await search(page,'月牙密钥');
  const hit=page.locator(`[data-search-result-id="${record.id}"]`);
  await hit.waitFor();
  assert.equal(await hit.getAttribute('data-search-result-type'),'setting');
  await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('已使用本地草稿'));
  await page.unroute('**/api/search*');
 });

 await test('后端返回损坏响应时回退到本地草稿',async()=>{
  await page.route('**/api/search*',route=>{
   if(new URL(route.request().url()).searchParams.get('q')==='月牙密钥')
    return route.fulfill({status:200,contentType:'application/json',body:'{"broken":true}'});
   return route.continue();
  });
  await search(page,'月牙密钥');
  const hit=page.locator(`[data-search-result-id="${record.id}"]`);
  await hit.waitFor();
  assert.equal(await hit.getAttribute('data-search-result-type'),'setting');
  await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('搜索响应格式无效'));
  await page.unroute('**/api/search*');
 });

 await test('较早的搜索响应不会覆盖后来提交的关键词',async()=>{
  let release,observed;
  const delayed=new Promise(resolve=>release=resolve);
  const firstRequest=new Promise(resolve=>observed=resolve);
  await page.route('**/api/search*',async route=>{
   const query=new URL(route.request().url()).searchParams.get('q');
   if(query==='钟声'){
    observed();
    await delayed;
   }
   await route.continue();
  });
  await search(page,'钟声');
  await firstRequest;
  await search(page,'旧钥匙');
  await page.locator(`[data-search-result-id="${record.id}"]`).waitFor();
  release();
  await pause(250);
  assert.equal(await page.locator(`[data-search-result-id="${record.id}"]`).count(),1);
  await page.unroute('**/api/search*');
 });

 await test('搜索结果显示精确原文并按 Unicode 字符范围定位到章节',async()=>{
  await search(page,'钟楼里');
  const button=page.locator('[data-search-jump][data-search-quote="钟楼里"]');
  await button.waitFor();
  assert.equal(await button.getAttribute('data-search-quote'),'钟楼里');
  await button.click();
  await page.waitForFunction(id=>view==='editor'&&activeChapter===id,chapter.id);
  const selected=await page.locator('#chapterBody').evaluate(input=>input.value.slice(input.selectionStart,input.selectionEnd));
  assert.equal(selected,'钟楼里');
  assert.equal(await page.locator('#chapterBody').inputValue(),chapter.body);
 });

 await test('章节正文改变后，旧搜索证据不能定位到错误位置',async()=>{
  await page.locator('[data-view="search"]').click();
  await search(page,'钟楼里');
  const button=page.locator('[data-search-jump][data-search-quote="钟楼里"]');
  await button.waitFor();
  await page.evaluate(id=>{
   const source=state.chapters.find(item=>item.id===id);
   source.body='前缀改变了原文位置。'+source.body;
  },chapter.id);
  await button.click();
  await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('原文已经变化'));
  assert.equal(await page.evaluate(()=>view),'search');
 });

 assert.deepEqual(pageErrors,[]);
 results.passed=true;
 results.desktop_scenario_count=results.cases.length;
 results.seeded_chapter_id=chapter.id;
 results.seeded_project_version=seeded.version;
 console.log(JSON.stringify(results,null,2));
 await context.close();
 await browser.close();
 app.kill('SIGTERM');
 await new Promise(resolve=>app.once('exit',resolve));
}

main().catch(async error=>{
 console.error(JSON.stringify({passed:false,error:error.stack,server_log:serverLog.slice(-5000)},null,2));
 try{await browser?.close()}catch{}
 try{app?.kill('SIGTERM')}catch{}
 process.exitCode=1;
});
