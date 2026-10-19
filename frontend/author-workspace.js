'use strict';

let authorProjects=[];
let authorBlueprint=null;
let authorBlueprintVersion=null;
let authorBlueprintDirty=false;
let authorBlueprintBase=null;
let authorBlueprintConflict=null;
let authorBlueprintApply=null;
const authorBlueprintFrozen=new WeakMap();
let authorBlueprintTimer=null;
let authorBlueprintPromise=null;
let authorPlanning=null;
let authorPlanningRows=[];
let authorPlanningSelected=null;
let authorPlanningTimer=null;
let authorPlanningBusy=false;
const authorPlanningStages=[['overview','总纲'],['volumes','卷'],['chapters','章'],['scenes','场景']];
let authorSettings=null;
let authorSettingsSeq=0;
let authorSettingsDispose=null;
let authorBackups=[];
let authorReferences=[];
let authorLoadToken=0;
let authorReferenceDialogSeq=0;
let authorPortableSeq=0;
let authorPortableDialog=null;
let authorPortableObservedScope=null;
const authorPortablePendingKey=KEY+':portable-restore-pending';
const authorPortableUnconfirmedKey=KEY+':portable-restore-unconfirmed';
const authorPortableUnconfirmedMemory=new Map();
const authorPortableCanceling=new Map();
let authorPortableReleasePromise=null;
let authorEditorBlueprintRequest=null;
let authorSwitchChain=Promise.resolve();
const editorUndo=new Map();
const editorRedo=new Map();
const authorChapterFinishes=new Map();
let authorFinishing=null;
let authorAgentRows=[];
let authorAgentTimer=null;
let authorAgentBusy=false;
let authorAgentSequence=0;
let authorAgentFocus=null;
let authorAgentExpandedScope=null;
let authorWorkbenchContextSequence=0;
let authorWorkbenchLibraryTab='chapters';
let authorWorkbenchResultsOpen=false;
let authorWorkbenchResultsSection='workbenchAgentSection';
const authorEditorPreferenceKey=KEY+':editor-preferences:v1';
const authorEditorPositionKey=KEY+':editor-positions:v1';
const authorEditorPreferenceDefaults={font_size:16,line_height:2.05,content_width:880};
const authorEditorPreferenceLimits={font_size:[14,24,1],line_height:[1.5,2.6,.05],content_width:[480,1000,40]};
let authorEditorPreferences=authorReadEditorPreferences();
const authorEditorPositions=authorReadEditorPositions();
let authorEditorPositionTimer=null;
let authorEditorPositionPending=null;
let authorEditorStorageMessage='';

function authorNormalizeEditorPreferences(raw){
 const next={...authorEditorPreferenceDefaults};
 for(const [key,[min,max,step]] of Object.entries(authorEditorPreferenceLimits)){
  const value=Number(raw?.[key]);
  if(Number.isFinite(value))next[key]=Number(Math.max(min,Math.min(max,min+Math.round((value-min)/step)*step)).toFixed(2));
 }
 return next;
}
function authorReadEditorPreferences(){
 try{return authorNormalizeEditorPreferences(JSON.parse(storage.get(authorEditorPreferenceKey)||'null'))}
 catch{return {...authorEditorPreferenceDefaults}}
}
function authorEditorPositionIdentity(binding){
 return binding?.project_id&&binding?.branch_id&&binding?.chapter_id?
  JSON.stringify([binding.project_id,binding.branch_id,binding.chapter_id]):null;
}
function authorReadEditorPositions(){
 const positions=new Map();
 try{
  const rows=JSON.parse(storage.get(authorEditorPositionKey)||'[]');
  if(!Array.isArray(rows))return positions;
  for(const row of rows.slice(-1000)){
   if(!Array.isArray(row)||row.length!==2||typeof row[0]!=='string'||!row[1]||typeof row[1]!=='object')continue;
   const identity=JSON.parse(row[0]),position=row[1];
   if(!Array.isArray(identity)||identity.length!==3||!identity.every(part=>typeof part==='string'&&part.length>0&&part.length<=200))continue;
   if(typeof position.text!=='string'||!/^\d+:[a-f0-9]+$/.test(position.text)||
    !['start','end','scroll_top','scroll_left','panel_top'].every(key=>Number.isFinite(position[key])&&position[key]>=0)||
    !['forward','backward','none'].includes(position.direction))continue;
   positions.set(row[0],position);
  }
 }catch{}
 return positions;
}
function authorEditorTextIdentity(text){
 // This local view signature only prevents restoring a caret into changed text.
 let hash=2166136261;
 for(let index=0;index<text.length;index++)hash=Math.imul(hash^text.charCodeAt(index),16777619);
 return text.length+':'+(hash>>>0).toString(16);
}
function authorEditorStorageStatus(message=''){
 authorEditorStorageMessage=message;
 const status=document.querySelector('[data-editor-preferences-status]');
 if(status){status.textContent=message;status.hidden=!message}
}
function authorRememberEditorPosition(input=document.querySelector('#chapterBody')){
 clearTimeout(authorEditorPositionTimer);authorEditorPositionPending=null;
 const binding=input&&editorInputBindings.get(input),identity=authorEditorPositionIdentity(binding);
 if(!identity||input.id!=='chapterBody'||!input.isConnected||document.querySelector('#chapterBody')!==input)return;
 const panel=input.closest('.workbench-manuscript');
 authorEditorPositions.delete(identity);
 authorEditorPositions.set(identity,{text:authorEditorTextIdentity(input.value),start:input.selectionStart,
  end:input.selectionEnd,direction:input.selectionDirection||'none',scroll_top:input.scrollTop,
  scroll_left:input.scrollLeft,panel_top:panel?.scrollTop||0,focused:document.activeElement===input});
 while(authorEditorPositions.size>1000)authorEditorPositions.delete(authorEditorPositions.keys().next().value);
 if(!storage.set(authorEditorPositionKey,JSON.stringify([...authorEditorPositions])))
  authorEditorStorageStatus('浏览器未能记住阅读位置，当前窗口仍会保留位置');
}
function authorScheduleEditorPosition(input){
 authorEditorPositionPending=input;clearTimeout(authorEditorPositionTimer);
 authorEditorPositionTimer=setTimeout(()=>{
  const pending=authorEditorPositionPending;
  if(pending?.isConnected)authorRememberEditorPosition(pending);
 },120);
}
function authorRestoreEditorPosition(){
 const input=document.querySelector('#chapterBody'),binding=input&&editorInputBindings.get(input);
 const position=authorEditorPositions.get(authorEditorPositionIdentity(binding));
 if(!input||!position||position.text!==authorEditorTextIdentity(input.value))return;
 if(position.focused)input.focus({preventScroll:true});
 input.setSelectionRange(Math.min(input.value.length,position.start),Math.min(input.value.length,position.end),position.direction);
 input.scrollTop=position.scroll_top;input.scrollLeft=position.scroll_left;
 const panel=input.closest('.workbench-manuscript');if(panel)panel.scrollTop=position.panel_top;
 captureSelection();
}
function authorEditorPreferenceControls(){
 return `<details class="editor-reading-settings"><summary title="阅读设置" aria-label="阅读设置"><span aria-hidden="true">Aa</span></summary>
 <div class="editor-reading-panel" aria-label="阅读设置">
 <div class="editor-reading-heading"><strong>阅读设置</strong><button type="button" data-editor-preferences-reset title="恢复默认阅读设置" aria-label="恢复默认阅读设置">↺</button></div>
 <label for="editorFontSize">字号<output data-editor-preference-value="font_size"></output></label><input id="editorFontSize" data-editor-preference="font_size" type="range" min="14" max="24" step="1" aria-label="正文字号">
 <label for="editorLineHeight">行距<output data-editor-preference-value="line_height"></output></label><input id="editorLineHeight" data-editor-preference="line_height" type="range" min="1.5" max="2.6" step=".05" aria-label="正文行距">
 <label for="editorContentWidth">正文宽度<output data-editor-preference-value="content_width"></output></label><input id="editorContentWidth" data-editor-preference="content_width" type="range" min="480" max="1000" step="40" aria-label="正文宽度">
 <p data-editor-preferences-status role="status" ${authorEditorStorageMessage?'':'hidden'}>${esc(authorEditorStorageMessage)}</p></div></details>`;
}
function authorApplyEditorPreferences(){
 const panel=document.querySelector('.workbench-manuscript');if(!panel)return;
 const input=panel.querySelector('#chapterBody'),scroll=input?{top:input.scrollTop,left:input.scrollLeft,panel:panel.scrollTop}:null;
 panel.style.setProperty('--editor-font-size',authorEditorPreferences.font_size+'px');
 panel.style.setProperty('--editor-line-height',String(authorEditorPreferences.line_height));
 panel.style.setProperty('--editor-content-width',authorEditorPreferences.content_width+'px');
 for(const control of panel.querySelectorAll('[data-editor-preference]'))control.value=authorEditorPreferences[control.dataset.editorPreference];
 for(const output of panel.querySelectorAll('[data-editor-preference-value]')){
  const key=output.dataset.editorPreferenceValue;
  output.value=authorEditorPreferences[key]+(key==='line_height'?' 倍':' px');
 }
 if(scroll){input.scrollTop=scroll.top;input.scrollLeft=scroll.left;panel.scrollTop=scroll.panel}
}
function authorSaveEditorPreferences(){
 authorApplyEditorPreferences();
 authorEditorStorageStatus(storage.set(authorEditorPreferenceKey,JSON.stringify(authorEditorPreferences))?'':'浏览器未能记住阅读设置，当前窗口已应用');
 authorRememberEditorPosition();
}
function authorBindEditorPreferences(input){
 authorApplyEditorPreferences();
 document.querySelectorAll('[data-editor-preference]').forEach(control=>control.addEventListener('input',()=>{
  authorEditorPreferences=authorNormalizeEditorPreferences({...authorEditorPreferences,[control.dataset.editorPreference]:control.value});
  authorSaveEditorPreferences();
 }));
 document.querySelector('[data-editor-preferences-reset]')?.addEventListener('click',()=>{
  authorEditorPreferences={...authorEditorPreferenceDefaults};authorSaveEditorPreferences();
 });
 if(!input)return;
 for(const name of ['select','keyup','mouseup','input','scroll'])input.addEventListener(name,()=>authorScheduleEditorPosition(input));
 input.addEventListener('blur',()=>authorScheduleEditorPosition(input));
 input.closest('.workbench-manuscript')?.addEventListener('scroll',()=>authorScheduleEditorPosition(input));
}

function authorBlueprintLocked(){
 return authorBlueprintApply?.owner===requestedProject&&authorBlueprintApply.branch===(state.branch_id||'main');
}
function authorBlueprintDraftKey(){
 return cacheKey(`:blueprint:${state.branch_id||'main'}`);
}
function authorCacheBlueprint(){
 if(!authorBlueprint)return;
 if(!authorBlueprintDirty&&!authorBlueprintConflict){storage.remove(authorBlueprintDraftKey());return}
 if(!storage.set(authorBlueprintDraftKey(),JSON.stringify({blueprint:authorBlueprint,base:authorBlueprintConflict?.base||authorBlueprintBase,
  version:authorBlueprintVersion})))toast('浏览器无法保留规划草稿，请保持页面打开并重试保存');
}
function authorBlueprintChanged(){
 authorBlueprintDirty=true;authorCacheBlueprint();clearTimeout(authorBlueprintTimer);
 authorBlueprintTimer=setTimeout(authorSaveBlueprint,700);
}
function authorReceiveBlueprint(data,draft){
 const remote=data.blueprint||{premise:state.project.summary,volumes:[]};
 const local=draft?.blueprint;
 const merged=local?MoyuSync.merge(draft.base||remote,local,remote):{value:remote,conflicts:[]};
 authorBlueprint=merged.value;authorBlueprintVersion=data.version;authorBlueprintBase=MoyuSync.copy(remote);
 authorBlueprintDirty=!MoyuSync.equal(authorBlueprint,remote);
 authorBlueprintConflict=merged.conflicts.length?{base:draft.base||remote,remote,conflicts:merged.conflicts}:null;
 authorCacheBlueprint();authorRenderBlueprint();
 const sidebar=view==='editor'&&document.querySelector('#authorChapterTree');
 if(!sidebar)return;
 const scroll=sidebar.closest('.workbench-library')?.scrollTop||0;
 sidebar.innerHTML=authorChapterList();
 sidebar.querySelectorAll('[data-chapter]').forEach(button=>button.onclick=()=>{
  if(activeChapter!==button.dataset.chapter)aiDraft=null;
  activeChapter=button.dataset.chapter;storage.set(cacheKey(':active'),activeChapter);view='editor';render();
 });
 authorRestoreChapterNavigation(scroll);
}

async function authorJSON(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
 const value=await response.json().catch(()=>({}));
 if(!response.ok)throw new Error(value.error||`请求未完成（${response.status}）`);
 return value;
}

function authorPageHead(title,actions=''){
 return `<div class="view-head"><div><h2>${title}</h2></div><div class="view-head-actions">${actions}</div></div>`;
}

function authorChapterList(){
 const groups=new Map();
 for(const c of state.chapters){
  const volume=c.volume_title||authorBlueprint?.volumes?.find(v=>v.id===c.volume_id)?.title||'章节';
  if(!groups.has(volume))groups.set(volume,[]);
  groups.get(volume).push(c);
 }
 return [...groups].map(([volume,chapters])=>`<div class="chapter-group"><h3>${esc(volume)}</h3>${chapters.map(c=>
  `<button class="chapter-select ${c.id===activeChapter?'active':''}" data-chapter="${safeId(c.id)}">${esc(c.title)}
  <small>${Array.from(c.body.replace(/\s/g,'')).length} 字 · ${esc(c.status)}</small></button>`).join('')}</div>`).join('');
}

function authorWorkbenchLibrary(){
 const records=state.records;
 return `<aside class="chapter-sidebar workbench-library" aria-label="作品资料" data-project-id="${esc(requestedProject)}" data-branch-id="${esc(state.branch_id||'main')}">
 <div class="workbench-library-head"><strong>作品资料</strong>${state.project.book_status==='complete'?'':'<button class="icon-btn" data-action="newChapter" title="新建章节" aria-label="新建章节">＋</button>'}</div>
 <div class="workbench-library-tabs" role="tablist" aria-label="作品资料分类">
 <button type="button" role="tab" id="workbenchChaptersTab" aria-controls="workbenchChapters" aria-selected="${authorWorkbenchLibraryTab==='chapters'}" data-library-tab="chapters">章节目录</button>
 <button type="button" role="tab" id="workbenchKnowledgeTab" aria-controls="workbenchKnowledge" aria-selected="${authorWorkbenchLibraryTab==='knowledge'}" data-library-tab="knowledge">人物与知识</button></div>
 <input class="workbench-filter" id="workbenchFilter" type="search" aria-label="筛选章节或知识" placeholder="查找章节、人物、设定">
 <section id="workbenchChapters" role="tabpanel" aria-labelledby="workbenchChaptersTab" ${authorWorkbenchLibraryTab==='chapters'?'':'hidden'}>
 <div id="authorChapterTree">${authorChapterList()}</div>
 <button class="workbench-link" type="button" data-workbench-view="blueprint">打开创作规划</button></section>
 <section id="workbenchKnowledge" role="tabpanel" aria-labelledby="workbenchKnowledgeTab" ${authorWorkbenchLibraryTab==='knowledge'?'':'hidden'}>
 ${['人物',...new Set(records.map(r=>r.type).filter(type=>type!=='人物'))].map(type=>{
  const rows=records.filter(r=>r.type===type);if(!rows.length)return '';
  return `<div class="knowledge-group"><h3>${esc(type)} <span>${rows.length}</span></h3>${rows.map(r=>
   `<div class="knowledge-item"><button type="button" class="knowledge-select" data-record="${safeId(r.id)}"><strong>${esc(r.name)}</strong><small>${esc(r.description)}</small></button><button type="button" class="icon-btn" data-record-knowledge="${safeId(r.id)}" title="查看关联与原文" aria-label="查看关联与原文">↗</button></div>`).join('')}</div>`;
 }).join('')||'<p class="workbench-empty">还没有人物或设定。</p>'}
 <button class="workbench-link" type="button" data-action="newRecord">＋ 新建人物或设定</button>
 <button class="workbench-link" type="button" data-workbench-view="memory">记忆中枢 · ${state.memory.filter(m=>['待确认','candidate'].includes(m.status)).length} 条待确认</button>
 <button class="workbench-link" type="button" data-workbench-view="story">人物关系与时间线</button></section>
 <p class="workbench-filter-empty" hidden>没有匹配的章节或资料。</p></aside>`;
}

function authorRestoreChapterNavigation(scroll=0){
 const library=document.querySelector('.workbench-library');
 if(!library)return;
 library.scrollTop=scroll;
 const selected=library.querySelector('.chapter-select.active');
 if(!selected?.getClientRects().length)return;
 const bounds=library.getBoundingClientRect(),item=selected.getBoundingClientRect();
 const top=bounds.top+library.clientTop,bottom=top+library.clientHeight;
 if(item.top<top)library.scrollTop+=item.top-top;
 else if(item.bottom>bottom)library.scrollTop+=Math.min(item.bottom-bottom,item.top-top);
}

function authorAgentForm(compact=false){
 return `<form id="writingAgent" class="settings-form agent-form ${compact?'agent-form-compact':''}">
 <label data-agent-chapter-field>章节<select id="agentChapter" ${compact?'disabled':''}>${state.chapters.map(c=>`<option value="${safeId(c.id)}" ${c.id===activeChapter?'selected':''}>${esc(c.title)}</option>`).join('')}</select></label>
 <fieldset class="agent-modes"><legend>本次任务</legend><label><input type="radio" name="agentMode" value="continue" checked>续写</label><label><input type="radio" name="agentMode" value="polish">整章修订</label><label><input type="radio" name="agentMode" value="inspect">章节检查</label><label><input type="radio" name="agentMode" value="plan">全书规划</label></fieldset>
 <label>创作要求<textarea id="agentQuery" maxlength="2000" required placeholder="这一章，你希望发生什么？"></textarea></label>
 <p class="agent-mode-scope" data-agent-mode-scope></p>
 <div class="settings-actions"><button class="primary-btn" type="submit" ${state.chapters.length?'':'disabled'}>准备写作计划</button></div>
 <p class="form-error" role="alert"></p></form>`;
}

function authorRevealWorkbenchSection(id,behavior='auto'){
 const panel=document.querySelector('#workbenchResults'),section=document.getElementById(id);
 if(!panel||!section)return;
 authorWorkbenchResultsOpen=true;authorWorkbenchResultsSection=id;panel.hidden=false;
 panel.querySelectorAll('.workbench-rail-section').forEach(item=>item.hidden=item!==section);
 panel.querySelectorAll('[data-rail-section]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.railSection===id)));
 panel.querySelector('.workbench-rail-scroll')?.scrollTo({top:0,behavior});
}

function authorWorkbenchRail(){
 return `<section id="workbenchResults" class="workbench-results" aria-label="写作结果" ${authorWorkbenchResultsOpen?'':'hidden'}>
 <div class="workbench-results-head"><nav class="workbench-rail-nav" role="tablist" aria-label="写作结果">
 ${[['workbenchAgentSection','任务进度'],['workbenchCandidateSection','正文候选'],['workbenchContextSection','本章依据']].map(([id,label])=>`<button type="button" role="tab" aria-selected="${authorWorkbenchResultsSection===id}" aria-controls="${id}" data-rail-section="${id}">${label}</button>`).join('')}</nav>
 <button type="button" class="icon-btn" data-workbench-results-close title="收起写作结果" aria-label="收起写作结果">×</button></div>
 <div class="workbench-rail-scroll">
 <section id="workbenchAgentSection" class="workbench-rail-section" ${authorWorkbenchResultsSection==='workbenchAgentSection'?'':'hidden'}><div class="workbench-section-title"><h3>本章任务</h3><button type="button" class="workbench-link" data-workbench-view="assistant">全部任务</button></div>
 <div id="agentSessions" aria-live="polite"><p class="workbench-empty">正在读取本章进度…</p></div></section>
 <section id="workbenchCandidateSection" class="ai-copilot workbench-rail-section" ${authorWorkbenchResultsSection==='workbenchCandidateSection'?'':'hidden'}><div class="ai-copilot-head"><h3>正文候选</h3><span class="ai-badge" id="aiBadge">${aiStatus.configured?'已配置':'未配置模型'}</span></div>
 <div class="ai-controls"><select id="aiTask" aria-label="AI 任务">
 <option value="continue">续写</option><option value="polish">润色所选文字</option><option value="scene">本章场景计划</option>
 <option value="outline">故事总纲</option><option value="brainstorm">创意方向</option><option value="summary">回顾与交接</option>
 <option value="check">连续性检查</option></select><input id="aiInstruction" maxlength="2000" aria-label="补充要求" placeholder="补充要求">
 <button class="primary-btn" id="aiGenerate">生成候选</button></div><div class="ai-reference-row" id="aiReferenceTools" aria-live="polite"></div><p class="ai-status" id="aiStatus"></p>
 <textarea class="ai-output" id="aiOutput" aria-label="AI 候选结果">${esc(aiDraft?.text||'')}</textarea>
 <div class="ai-result-meta"><span>${esc(aiStatus.model||'未配置模型')}</span><div class="ai-actions">
 <button class="ghost-btn" id="aiCopy" ${aiDraft?'':'disabled'}>复制</button><button class="ghost-btn" id="aiClear">拒绝 / 停止</button>
 <button class="ghost-btn" id="aiOriginal" hidden>回看原候选</button><button class="ghost-btn" id="aiRevise" disabled>整理候选</button>
 <button class="primary-btn" id="aiAdopt" ${aiDraft?.complete?'':'disabled'}>比较并采纳</button></div></div></section>
 <section id="workbenchContextSection" class="workbench-rail-section" ${authorWorkbenchResultsSection==='workbenchContextSection'?'':'hidden'}><div class="workbench-section-title"><h3>本章依据</h3><button type="button" class="icon-btn" data-workbench-context-refresh title="保存并刷新本章依据" aria-label="保存并刷新本章依据">↻</button></div>
 <div id="workbenchContext" aria-live="polite"><p class="workbench-empty">正在读取已保存稿件的资料…</p></div>
 <button type="button" class="workbench-link" data-action="contextPreview">完整上下文与材料控制</button></section>
 </div></section>`;
}

function authorWorkbenchContextStale(){
 const host=document.querySelector('#workbenchContext');if(!host)return;
 ++authorWorkbenchContextSequence;
 let note=host.querySelector('[data-context-state]');
 if(!note){note=document.createElement('p');note.dataset.contextState='';host.prepend(note)}
 note.className='workbench-context-state context-stale';note.textContent='稿件或要求已变化，请保存并刷新依据';
}

function authorRefreshWorkbenchKnowledge(){
 const library=document.querySelector('.workbench-library');if(view!=='editor'||!library)return false;
 const filter=library.querySelector('#workbenchFilter').value,scroll=library.scrollTop;
 const template=document.createElement('template');template.innerHTML=authorWorkbenchLibrary();
 library.querySelector('#workbenchKnowledge').innerHTML=template.content.querySelector('#workbenchKnowledge').innerHTML;
 library.querySelectorAll('[data-record]').forEach(button=>button.onclick=()=>editRecord(button.dataset.record));
 authorBindRecordKnowledgeLinks(library);
 library.querySelectorAll('#workbenchKnowledge [data-action]').forEach(button=>button.onclick=()=>actions[button.dataset.action]?.(button));
 library.querySelectorAll('#workbenchKnowledge [data-workbench-view]').forEach(button=>button.onclick=async()=>{
  if(authorBlueprintDirty&&!await authorSaveBlueprint())return;
  view=button.dataset.workbenchView;render();
 });
 library.querySelector('#workbenchFilter').value=filter;
 library.querySelector('#workbenchFilter').dispatchEvent(new Event('input'));library.scrollTop=scroll;
 authorWorkbenchContextStale();return true;
}

async function authorLoadWorkbenchContext(saveFirst=false){
 const host=document.querySelector('#workbenchContext'),c=chapter();if(!host||!c)return;
 const scope={...authorAgentScope(),chapter:c.id},sequence=++authorWorkbenchContextSequence;
 const current=()=>sequence===authorWorkbenchContextSequence&&scope.owner===requestedProject&&
  scope.branch===(state.branch_id||'main')&&scope.chapter===activeChapter&&view==='editor'&&document.querySelector('#workbenchContext')===host;
 try{
  if(saveFirst&&!await syncBackend())throw new Error('请先保存或合并当前稿件');
  if(!current())return;
  const task=document.querySelector('#aiTask')?.value||'continue',instruction=document.querySelector('#aiInstruction')?.value||'';
  const query=new URLSearchParams({chapter_id:c.id,task,instruction,materials:JSON.stringify(materialControls()),retrieval_profile:JSON.stringify(retrievalProfile())});
  const pack=await authorJSON('/api/context?'+query,{headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch}});
  if(!current())return;
  const budget=pack.budget||{},knowledge=budget.knowledge_scope||{},structure=pack.story_structure||{},conflicts=structure.conflicts||[];
  const sources=[...(pack.previous_chapters||[]).map(item=>({...item,label:'近章'})),...(pack.retrieved_chapters||[]).map(item=>({...item,label:'召回'}))];
  const stale=isDirty();
  host.innerHTML=`<p class="workbench-context-state ${stale?'context-stale':''}" data-context-state>${stale?'当前有未保存改动 · 以下为已保存稿件资料':'已保存稿件资料 · '+esc(AI_TASK_NAMES[task]||task)}</p>
   <div class="workbench-context-counts"><span><strong>${(pack.records||[]).length}</strong>设定</span><span><strong>${(pack.memory||[]).length}</strong>确认记忆</span><span><strong>${sources.length}</strong>来源章节</span></div>
   <dl class="workbench-goals"><div><dt>本章目标</dt><dd>${esc(c.chapter_goal||'尚未填写')}</dd></div><div><dt>当前视角</dt><dd>${esc(c.pov||'尚未指定')}</dd></div></dl>
   ${sources.map(item=>`<details class="workbench-evidence"><summary>${esc(item.label)} · ${esc(item.title)}</summary><blockquote>${esc(item.evidence?.source_quote||'此来源没有可展示的摘录，请查看完整上下文。')}</blockquote><small>来源版本 ${esc(item.evidence?.source_revision_id||'未提供')} · ${item.source_range?.start??'?'}–${item.source_range?.end??'?'}</small></details>`).join('')}
   ${(pack.records||[]).map(item=>`<button type="button" class="workbench-source-link" data-workbench-record="${safeId(item.id)}">${esc(item.type||'设定')} · ${esc(item.name)}</button>`).join('')}
   ${(pack.memory||[]).map(item=>`<button type="button" class="workbench-source-link" data-workbench-memory="${safeId(item.id)}">已确认 · ${esc(item.name)}</button>`).join('')}
   <div class="workbench-context-check"><strong>范围与冲突</strong><p>${Number(knowledge.filtered_count||0)} 条超出本章范围的资料已过滤 · ${conflicts.length} 条记忆冲突</p>
   ${(knowledge.warnings||[]).slice(0,4).map(item=>`<p class="check-bad">${esc(typeof item==='string'?item:item.message||item.reason||'资料范围需要核对')}</p>`).join('')}
   ${conflicts.slice(0,4).map(item=>`<p class="check-bad">${esc(item.message||item.description||item.entity_id||'记忆之间存在冲突，请核对原句')}</p>`).join('')}</div>`;
  host.querySelectorAll('[data-workbench-record]').forEach(button=>button.onclick=()=>editRecord(button.dataset.workbenchRecord));
  host.querySelectorAll('[data-workbench-memory]').forEach(button=>button.onclick=()=>showMemoryEvidence(button.dataset.workbenchMemory));
 }catch(error){if(current())host.innerHTML=`<p class="form-error" role="alert">${esc(error.message)}</p><p class="workbench-empty">依据未读取，不能据此判断没有冲突。</p>`}
}

function authorBindWorkbench(){
 authorBindRecordKnowledgeLinks(document.querySelector('.workbench-library'));
 document.querySelectorAll('.workbench-library [data-chapter]').forEach(button=>button.onclick=()=>{
  if(activeChapter!==button.dataset.chapter)aiDraft=null;
  activeChapter=button.dataset.chapter;storage.set(cacheKey(':active'),activeChapter);view='editor';render();
 });
 document.querySelectorAll('[data-workbench-view]').forEach(button=>button.onclick=async()=>{
  if(authorBlueprintDirty&&!await authorSaveBlueprint())return;
  view=button.dataset.workbenchView;render();
 });
 document.querySelectorAll('[data-library-tab]').forEach(button=>button.onclick=()=>{
  authorWorkbenchLibraryTab=button.dataset.libraryTab;
  document.querySelectorAll('[data-library-tab]').forEach(tab=>tab.setAttribute('aria-selected',String(tab===button)));
  document.querySelector('#workbenchChapters').hidden=authorWorkbenchLibraryTab!=='chapters';
  document.querySelector('#workbenchKnowledge').hidden=authorWorkbenchLibraryTab!=='knowledge';
  document.querySelector('#workbenchFilter')?.dispatchEvent(new Event('input'));
 });
 const filter=document.querySelector('#workbenchFilter');
 if(filter)filter.oninput=()=>{
  const term=filter.value.trim().toLocaleLowerCase(),panel=document.querySelector(authorWorkbenchLibraryTab==='chapters'?'#workbenchChapters':'#workbenchKnowledge');
  panel.querySelectorAll('.chapter-select,.knowledge-select').forEach(button=>button.hidden=!button.textContent.toLocaleLowerCase().includes(term));
  panel.querySelectorAll('.knowledge-item').forEach(item=>item.hidden=!!item.querySelector('.knowledge-select')?.hidden);
  panel.querySelectorAll('.chapter-group,.knowledge-group').forEach(group=>group.hidden=![...group.querySelectorAll('.chapter-select,.knowledge-select')].some(button=>!button.hidden));
  document.querySelector('.workbench-filter-empty').hidden=!term||!!panel.querySelector('.chapter-select:not([hidden]),.knowledge-select:not([hidden])');
 };
 document.querySelectorAll('[data-rail-section]').forEach(button=>button.onclick=()=>{
  authorRevealWorkbenchSection(button.dataset.railSection,'smooth');
 });
 document.querySelector('[data-workbench-results-close]')?.addEventListener('click',()=>{
  authorWorkbenchResultsOpen=false;document.querySelector('#workbenchResults').hidden=true;
 });
 document.querySelector('[data-workbench-results-open]')?.addEventListener('click',()=>authorRevealWorkbenchSection(authorWorkbenchResultsSection));
 document.querySelector('[data-agent-chat-quote]')?.addEventListener('click',()=>globalThis.MoyuAgentChat?.quoteSelection());
 document.querySelector('[data-workbench-focus]')?.addEventListener('click',event=>{
  const focused=document.body.classList.toggle('workbench-focus');
  event.currentTarget.setAttribute('aria-pressed',String(focused));event.currentTarget.textContent=focused?'退出专注':'专注写作';
 });
 document.querySelector('[data-workbench-project]')?.addEventListener('click',()=>document.querySelector('#editProjectBtn').click());
 document.querySelector('[data-workbench-context-refresh]')?.addEventListener('click',()=>authorLoadWorkbenchContext(true));
 document.querySelector('#aiInstruction')?.addEventListener('input',authorWorkbenchContextStale);
 document.querySelector('#aiTask')?.addEventListener('change',authorWorkbenchContextStale);
 Object.keys(CHAPTER_PLAN_FIELDS).forEach(id=>document.getElementById(id)?.addEventListener('input',authorWorkbenchContextStale));
 authorBindWritingAgent();authorLoadWorkbenchContext();
 if(aiDraft)authorRevealWorkbenchSection('workbenchCandidateSection');
}

function authorBindRecordKnowledgeLinks(host){
 if(!host)return;
 if(typeof bindRecordKnowledgeLinks==='function'){bindRecordKnowledgeLinks(host);return}
 host.querySelectorAll('[data-record-knowledge]').forEach(button=>button.onclick=()=>{
  if(typeof showRecordKnowledge==='function')showRecordKnowledge(button.dataset.recordKnowledge);
 });
}

function authorEditorView(){
 const c=chapter();
 const previous=c?state.chapters[state.chapters.findIndex(item=>item.id===c.id)-1]:null;
 const reviewLabel={passed:'已通过',needs_revision:'需修改',stale:'已过期',pending:'未完成',failed:'失败'}[c?.review_status]||'未审稿';
 return `<div class="workbench-path"><div><span title="${esc(state.project.title)}">${esc(state.project.title)}</span><button type="button" class="workbench-project-edit" data-workbench-project title="编辑作品信息" aria-label="编辑作品信息">⚙</button><span class="workbench-path-divider">/</span><strong>${esc(c?.title||'章节写作')}</strong></div><span class="workbench-branch" title="当前作品版本">${esc((state.branch_id||'main')==='main'?'主线':state.branch_id)}</span><button type="button" class="ghost-btn" data-workbench-focus aria-pressed="false">专注写作</button></div>
 <div class="editor-layout desktop-workbench">${authorWorkbenchLibrary()}<section class="editor-card workbench-manuscript" aria-label="章节编辑区">
 ${c?`<div class="editor-toolbar">
 <button data-editor-undo title="撤销最近修改" aria-label="撤销最近修改">↶</button><button data-editor-redo title="重做最近修改" aria-label="重做最近修改">↷</button>
 <button data-format="bold" title="粗体" aria-label="粗体"><b>B</b></button><button data-format="quote" title="引用" aria-label="引用">❞</button>
 <button data-format="scene" title="场景分隔" aria-label="场景分隔">***</button>
 <button class="ghost-btn" data-action="reviewChapter">本章审稿</button><button class="ghost-btn" data-action="chapterAnalysis">章后记忆</button>
 <button class="ghost-btn" data-action="revisionHistory">历史</button><button class="ghost-btn" data-agent-chat-quote title="将所选原文加入写作总管">引用选区</button>
 <button class="ghost-btn" data-workbench-results-open>结果</button>${authorEditorPreferenceControls()}<span class="editor-meta" id="selectionState">未选文字</span></div>
 <input class="title-input" id="chapterTitle" value="${esc(c.title)}" aria-label="章节标题">
 <textarea class="chapter-editor" id="chapterBody" aria-label="章节正文">${esc(c.body)}</textarea>
 <div class="editor-footer"><span id="liveCount">${Array.from(c.body.replace(/\s/g,'')).length} 字</span>
 <select id="chapterStatus" aria-label="章节状态">${['草稿','进行中','完成'].map(label=>`<option ${c.status===label?'selected':''}>${label}</option>`).join('')}</select>
 <span class="review-pill">审稿：${reviewLabel}</span>
 ${state.project.book_status==='complete'?'':`<button class="ghost-btn" data-action="finishChapter" ${authorFinishing?'disabled':''}>完成本章，写下一章</button>`}</div>
 <details class="chapter-plan" ${c.body?'':'open'}><summary>本章任务与回顾</summary>
 ${previous?.next_brief?`<label>上章交接<textarea id="previousChapterHandoff" readonly>${esc(previous.next_brief)}</textarea></label>
 ${state.project.book_status!=='complete'&&c.status!=='完成'?'<div class="plan-actions"><button type="button" class="ghost-btn" data-action="prepareChapterHandoff">承接上章，准备本章</button></div>':''}`:''}<div class="plan-grid">
 ${[['chapterGoal','chapter_goal','本章目标'],['chapterPov','pov','视角'],['chapterScenePlan','scene_plan','场景计划'],
 ['chapterForeshadowing','foreshadowing_op','伏笔动作'],['chapterRecap','recap','章节回顾'],['chapterNextBrief','next_brief','下一章任务'],
 ['chapterHook','hook','章节出口']].map(([id,key,label])=>`<label>${label}${['chapterGoal','chapterPov'].includes(id)?
 `<input id="${id}" value="${esc(c[key]||'')}">`:`<textarea id="${id}">${esc(c[key]||'')}</textarea>`}</label>`).join('')}</div></details>
 `:`<div class="empty">还没有章节。${state.project.book_status==='complete'?'':'<button class="primary-btn" data-action="newChapter">新建章节</button>'}</div>`}</section>${c?authorWorkbenchRail():''}</div>`;
}

function authorEditorHistoryKey(c=chapter()){
 return JSON.stringify([requestedProject,state.branch_id||state.project?.branch_id||'main',c?.id]);
}

function authorUpdateUndoControls(){
 const c=chapter(),key=authorEditorHistoryKey(c);
 for(const [selector,history] of [['[data-editor-undo]',editorUndo],['[data-editor-redo]',editorRedo]]){
  const button=document.querySelector(selector);if(button)button.disabled=!c||!(history.get(key)?.length);
 }
}

function authorRememberUndo(c,nextBody){
 if(c.body===nextBody)return;
 const key=authorEditorHistoryKey(c),stack=editorUndo.get(key)||[];
 stack.push({before:c.body,after:nextBody});if(stack.length>80)stack.shift();
 editorUndo.set(key,stack);editorRedo.set(key,[]);authorUpdateUndoControls();
}

function authorEditBody(value,{history=true}={}){
 const c=chapter();if(!c)return;
 if(history)authorRememberUndo(c,value);
 c.body=value;c.updated=Date.now();c.review_status='stale';c.review={...c.review,status:'stale'};c.reviewed_at=null;
 const body=document.querySelector('#chapterBody');if(body)setEditorInputValue(body,value);
 save();const count=document.querySelector('#liveCount');if(count)count.textContent=`${Array.from(value.replace(/\s/g,'')).length} 字`;
 document.querySelector('#projectMeta').textContent=`${state.chapters.length} 章 · ${words()} 字`;
 const pill=document.querySelector('.review-pill');if(pill)pill.textContent='审稿：已过期';captureSelection();updateCandidateRevisionControls();
 authorWorkbenchContextStale();
}

function authorUndo(reverse=false){
 const c=chapter();if(!c)return;
 if(mergePending)return toast('请先处理两窗口的改稿冲突，再撤销或重做');
 if(editorInputBindings.get(document.querySelector('#chapterBody'))?.composing)return toast('请先完成当前输入，再撤销或重做');
 const key=authorEditorHistoryKey(c),from=(reverse?editorRedo:editorUndo).get(key)||[];if(!from.length)return;
 const change=from.at(-1),result=MoyuSync.mergeText(change.after,change.before,c.body);
 if(result.conflict)return toast(`${reverse?'重做':'撤销'}位置已被另一窗口修改，当前正文已保留，请手动整理这一处`);
 const to=(reverse?editorUndo:editorRedo).get(key)||[];
 to.push({before:c.body,after:result.value});if(to.length>80)to.shift();
 from.pop();(reverse?editorUndo:editorRedo).set(key,to);authorEditBody(result.value,{history:false});authorUpdateUndoControls();
 document.querySelector('#chapterBody')?.focus();
}

function authorFormat(kind){
 const input=document.querySelector('#chapterBody');if(!input)return;
 const start=input.selectionStart,end=input.selectionEnd,text=input.value.slice(start,end);
 const replacement=kind==='bold'?`**${text||'文字'}**`:kind==='quote'?(text||'引用').split('\n').map(line=>`> ${line}`).join('\n'):'\n\n***\n\n';
 const next=input.value.slice(0,start)+replacement+input.value.slice(end);
 authorEditBody(next);input.focus();input.setSelectionRange(start,start+replacement.length);captureSelection();
}

views.editor=authorEditorView;
views.library=()=>`${authorPageHead('作品书架','<button class="primary-btn" data-action="createProject">＋ 新作品</button>')}
 <div id="projectList" class="book-list"><div class="empty">正在读取作品…</div></div>`;
views.tasks=()=>`${authorPageHead('生成任务','<button class="ghost-btn" data-action="refreshTasks">↻</button>')}
 <div id="taskList" class="task-list"><div class="empty">正在读取任务…</div></div>`;
views.blueprint=()=>`${authorPageHead('创作规划','<button class="ghost-btn" data-action="addVolume">＋ 卷</button><button class="ghost-btn" data-action="materializePlan">建立章节</button><button class="primary-btn" data-action="saveBlueprint">保存规划</button>')}
 <section class="planning-workflow"><div class="planning-heading"><h3>分阶段规划</h3><button class="ghost-btn" data-action="beginPlanning">新规划</button></div>
 <div id="planningWorkflowHost"><div class="empty">正在读取阶段…</div></div></section>
 <div id="blueprintHost"><div class="empty">正在读取规划…</div></div>`;
views.settings=()=>`${authorPageHead('模型与风格')}<div id="settingsHost"><div class="empty">正在读取设置…</div></div>
 <section id="agentSettingsHost" class="agent-settings-section"></section>`;
views.backups=()=>`${authorPageHead('备份中心','<button class="ghost-btn" data-action="importBackup">从电脑恢复 ZIP</button><button class="primary-btn" data-action="createBackup">＋ 完整备份</button>')}
 <div class="backup-scope"><strong>完整作品备份</strong><p>包含正文、历史版本、设定、记忆证据、人物关系、时间线、任务和审计记录。</p>
 <button class="ghost-btn" data-action="exportManuscript">导出稿件 JSON</button><button class="ghost-btn" data-action="importManuscript">导入稿件 JSON</button></div>
 <div id="portableRestorePending"></div>
 <div id="backupList"><div class="empty">正在读取备份…</div></div>`;
views.references=()=>`${authorPageHead('旧书与参考库','<button class="primary-btn" data-action="importBook">导入 TXT / MD / EPUB</button>')}
 <div id="referenceList"><div class="empty">正在读取参考来源…</div></div>`;
views.assistant=()=>`${authorPageHead('写作总管')}
 <nav class="agent-overview-actions" aria-label="写作总管管理"><button type="button" class="ghost-btn" data-agent-focus-chat>继续对话</button>
 <button type="button" class="ghost-btn" data-agent-open-settings>连接设置</button><button type="button" class="ghost-btn" data-agent-open-tasks>生成任务</button></nav>
 <section class="agent-overview-results"><h3>创作进度与待处理结果</h3>
 <div id="agentSessions" aria-live="polite"><div class="empty">正在读取写作进度…</div></div></section>`;

function authorAgentDraftKey(){return cacheKey(`:writing-agent:${state.branch_id||'main'}${view==='editor'?':'+activeChapter:''}`)}
function authorAgentScope(){return {owner:requestedProject,branch:state.branch_id||'main'}}
function authorAgentCurrent(scope,host){return scope.owner===requestedProject&&scope.branch===(state.branch_id||'main')&&['assistant','editor'].includes(view)&&document.querySelector('#agentSessions')===host}
function authorAgentEditorScopeCurrent(scope){
 return !!scope&&view==='editor'&&scope.owner===requestedProject&&scope.branch===(state.branch_id||'main')&&scope.chapter_id===activeChapter;
}
function authorAgentCurrentFocus(){
 if(authorAgentFocus&&!authorAgentEditorScopeCurrent(authorAgentFocus))authorAgentFocus=null;
 return authorAgentFocus;
}
async function authorOpenAgentSession(sessionId,chapterId,chatCurrent=()=>true,targetSection='workbenchAgentSection'){
 const scope=authorAgentScope(),startingChapter=activeChapter;
 const current=()=>scope.owner===requestedProject&&scope.branch===(state.branch_id||'main')&&activeChapter===startingChapter&&chatCurrent();
 if(!current())return false;
 if(!chapter(chapterId))throw new Error('来源章节已不存在，请从当前作品重新选择任务。');
 if(!await syncBackend())throw new Error('请先保存或合并当前稿件。');
 if(!current())return false;
 const data=await authorJSON('/api/writing-agent?session_id='+encodeURIComponent(sessionId),
  {headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch}});
 if(!current())return false;
 const row=data.sessions?.find(item=>item.id===sessionId);
 if(!row||row.chapter_id!==chapterId)throw new Error('写作任务与来源章节不一致，请重新打开。');
 authorAgentFocus={...scope,id:sessionId,chapter_id:chapterId};
 activeChapter=chapterId;storage.set(cacheKey(':active'),chapterId);view='editor';render();
 const host=document.querySelector('#agentSessions');
 if(authorAgentEditorScopeCurrent(authorAgentFocus)&&host){
  authorAgentRows=[row];authorRenderAgent(scope,host);
  const candidate=row.status==='awaiting_adoption'&&targetSection==='workbenchCandidateSection'&&row.current_job?
   receiveCandidateJob(row.current_job):null;
  if(candidate)displayCandidate(candidate);
  authorRevealWorkbenchSection(candidate?'workbenchCandidateSection':'workbenchAgentSection');
 }
 return true;
}
function authorBindWritingAgent(){
 const form=document.querySelector('#writingAgent');if(!form){authorLoadAgent();return}
 const scope=authorAgentScope(),key=authorAgentDraftKey(),host=document.querySelector('#agentSessions');
 let draft;let draftRevision=0,inFlightRequestId='';try{draft=JSON.parse(storage.get(key)||'null')}catch{}
 if(draft){form.querySelector('#agentQuery').value=draft.query||'';
  if(view!=='editor'&&state.chapters.some(c=>c.id===draft.chapter_id))form.querySelector('#agentChapter').value=draft.chapter_id;
  const mode=form.querySelector(`[name="agentMode"][value="${['continue','polish','inspect','plan'].includes(draft.mode)?draft.mode:'continue'}"]`);if(mode)mode.checked=true}
 const payload=()=>({query:form.querySelector('#agentQuery').value.trim(),chapter_id:form.querySelector('#agentChapter').value,mode:form.querySelector('[name="agentMode"]:checked').value});
 const reflectMode=()=>{
  const mode=payload().mode;
  form.querySelector('[data-agent-chapter-field]').hidden=mode==='plan';
  form.querySelector('#agentQuery').placeholder=mode==='plan'?'这部小说，你想写怎样的故事？':mode==='polish'?'这一整章，你希望怎样修改？':'这一章，你希望发生什么？';
  form.querySelector('[data-agent-mode-scope]').textContent=mode==='polish'?'整章正文生成修订候选，采纳后替换本章。':
   mode==='plan'?'总纲、卷、章和场景逐阶段确认，最后采用完整规划。':mode==='inspect'?'先整理章后资料，由你逐项决定后继续故事审稿。':'正文候选采纳后整理章后资料，由你逐项决定后继续故事审稿。';
 };
 reflectMode();
 form.oninput=()=>{reflectMode();draftRevision++;const value=payload(),changed=!draft||JSON.stringify(value)!==JSON.stringify({query:draft.query,chapter_id:draft.chapter_id,mode:draft.mode});if(changed)draft={...value,request_id:draft?.request_id&&draft.request_id!==inFlightRequestId?draft.request_id:crypto.randomUUID()};storage.set(key,JSON.stringify(draft))};
 form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;
  const errorHost=form.querySelector('.form-error');errorHost.textContent='';
  try{
   if(!await syncBackend())throw new Error('请先保存或合并当前稿件');
   if(!authorAgentCurrent(scope,host))return;
   const value=payload(),submitRevision=draftRevision;draft={...value,request_id:draft?.request_id||crypto.randomUUID()};
   if(!storage.set(key,JSON.stringify(draft)))throw new Error('浏览器暂时无法保留本次请求，请恢复存储后重试');
   inFlightRequestId=draft.request_id;
   await authorJSON('/api/writing-agent',{method:'POST',headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch},body:JSON.stringify(draft)});
   if(!authorAgentCurrent(scope,host))return;
   const current=payload();
   if(draftRevision===submitRevision&&JSON.stringify(current)===JSON.stringify(value)){
    storage.remove(key);draft=null;form.querySelector('#agentQuery').value='';
   }else{
    draft={...current,request_id:draft?.request_id&&draft.request_id!==inFlightRequestId?draft.request_id:crypto.randomUUID()};storage.set(key,JSON.stringify(draft));
   }
   await authorLoadAgent();
  }catch(error){if(authorAgentCurrent(scope,host))errorHost.textContent=error.message}
  finally{inFlightRequestId='';if(form.isConnected)button.disabled=false}
 };
 authorLoadAgent();
}

async function authorLoadAgent(){
 clearTimeout(authorAgentTimer);
 const scope=authorAgentScope(),host=document.querySelector('#agentSessions');if(!host)return;
 const focus=authorAgentCurrentFocus();
 const sequence=++authorAgentSequence;
 try{
  const query=focus?'?session_id='+encodeURIComponent(focus.id):
   typeof writingInboxAgentQuery==='function'?writingInboxAgentQuery(scope):'';
  const data=await authorJSON('/api/writing-agent'+query,{headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch}});
  if(sequence!==authorAgentSequence||!authorAgentCurrent(scope,host))return;
  if(focus&&(!authorAgentEditorScopeCurrent(focus)||authorAgentFocus!==focus))return;
  if(focus&&!data.sessions?.some(row=>row.id===focus.id&&row.chapter_id===focus.chapter_id))
   throw new Error('写作任务与来源章节不一致，请重新打开。');
  authorAgentRows=data.sessions||[];if(!authorAgentBusy)authorRenderAgent(scope,host);
 }catch(error){if(sequence===authorAgentSequence&&authorAgentCurrent(scope,host)){
  let alert=host.querySelector('[data-agent-error]');if(!alert){alert=document.createElement('p');alert.dataset.agentError='';alert.className='form-error';alert.setAttribute('role','alert');host.prepend(alert)}alert.textContent=error.message;
 }}finally{if(sequence===authorAgentSequence&&authorAgentCurrent(scope,host))authorAgentTimer=setTimeout(authorLoadAgent,2500)}
}

function authorRenderAgent(scope,host){
 const labels={awaiting_authorization:'等待启动',awaiting_job:'正在执行',awaiting_adoption:'正文候选待采纳',awaiting_memory:'等待章后确认',awaiting_plan_confirmation:'本阶段候选待确认',awaiting_plan_application:'完整规划待采用',requires_action:'需要处理',review_ready:'记忆与审稿已就绪',canceled:'已停止',restored:'历史记录'};
 const focus=authorAgentCurrentFocus(),expanded=authorAgentEditorScopeCurrent(authorAgentExpandedScope);
 const matching=view==='editor'?authorAgentRows.filter(row=>row.mode==='plan'||row.chapter_id===activeChapter)
  .sort((a,b)=>Number(['review_ready','canceled','restored'].includes(a.status))-Number(['review_ready','canceled','restored'].includes(b.status))):authorAgentRows;
 const rows=focus?matching.filter(row=>row.id===focus.id&&row.chapter_id===focus.chapter_id):view==='editor'&&!expanded?matching.slice(0,3):matching;
 const navigation=focus?'<div class="inbox-focus-note"><span>正在查看这次写作任务</span><button type="button" class="ghost-btn" data-agent-return-all>返回本章全部任务</button></div>':
  view==='editor'&&!expanded&&matching.length>3?'<div class="plan-actions"><button type="button" class="ghost-btn" data-agent-return-all>查看本章全部任务</button></div>':'';
 host.innerHTML=navigation+(rows.map(row=>`<article class="agent-session" data-agent-session="${safeId(row.id)}" data-agent-status="${esc(row.status)}" data-agent-mode="${esc(row.mode)}">
 <div class="planning-heading"><h3>${row.mode==='plan'?'全书规划':`${row.mode==='polish'?'整章修订 · ':''}${esc(chapter(row.chapter_id)?.title||'原章节')}`}</h3><span>${esc(row.mode==='plan'&&row.status==='review_ready'?'完整规划已采用':labels[row.status]||'准备中')}</span></div>
 <p class="agent-query">${esc(row.query)}</p><ol class="agent-steps">${row.steps.map(step=>`<li><strong>${esc(step.label)}</strong><span>${esc(({pending:'尚未开始',complete:'候选已完成',confirmed:'已确认'})[step.status]||taskStatus(step.status))}</span></li>`).join('')}</ol>
 <p class="agent-budget">已生成 ${Number(row.calls_used)} / 最多 ${Number(row.max_calls)} 次</p>
 ${row.source_stale?`<p class="agent-source-stale">${row.mode==='plan'?'创作规划已变化，请核对现有规划后再处理候选。':'任务依据与当前稿件不同，请核对后再处理结果。'}</p>`:''}
 ${row.context_evidence?.source_revision_id?`<details class="agent-context-evidence"><summary>本次任务的冻结依据</summary><p>近章 ${Number(row.context_evidence.counts?.previous_chapters||0)} · 召回 ${Number(row.context_evidence.counts?.retrieved_chapters||0)} · 确认记忆 ${Number(row.context_evidence.counts?.confirmed_memory||0)} · 设定 ${Number(row.context_evidence.counts?.story_cards||0)}</p><small>来源版本 ${esc(row.context_evidence.source_revision_id||'未提供')}<br>材料指纹 ${esc(row.context_evidence.context_hash||'未提供')}</small></details>`:''}
 ${row.error?`<p class="form-error" role="alert">${esc(row.error)}</p>`:''}
 ${row.status==='awaiting_authorization'&&!row.restored_read_only?`<p>启动后最多执行 ${Number(row.max_calls)} 次模型调用，按当前模型额度计费。${row.mode==='plan'?'每阶段确认后生成下一阶段，最后由你采用完整规划。':['continue','polish'].includes(row.mode)?'正文采纳后整理章后资料，由你逐项决定后继续审稿。':'章后资料由你逐项决定后继续审稿，审稿结果保留待确认。'}</p>`:''}
 ${row.status==='awaiting_memory'&&!row.restored_read_only?'<p>章后资料已生成。每项事实、章节回顾和下一章任务都由你决定采用或不采用，未处理完可以分次保存。</p>':''}
 ${authorAgentPlanningPreview(row)}
 <div class="agent-actions">
 ${!row.restored_read_only&&row.status==='awaiting_authorization'?'<button class="primary-btn" data-agent-action="authorize">启动本次任务</button>':''}
 ${!row.restored_read_only&&row.status==='awaiting_job'?'<button class="ghost-btn" data-agent-action="advance">更新进度</button>':''}
 ${!row.restored_read_only&&row.mode==='plan'&&row.status==='awaiting_plan_confirmation'?'<button class="primary-btn" data-agent-action="confirm">确认本阶段</button>':''}
 ${!row.restored_read_only&&row.mode==='plan'&&row.status==='awaiting_plan_application'?'<button class="primary-btn" data-agent-action="apply">采用完整规划</button>':''}
 ${!row.restored_read_only&&row.mode!=='plan'&&row.current_job&&row.status!=='awaiting_memory'?'<button class="primary-btn" data-agent-open>查看当前结果</button>':''}
 ${!row.restored_read_only&&row.status==='awaiting_adoption'?'<button class="ghost-btn" data-agent-action="applied">核对采纳结果</button>':''}
 ${!row.restored_read_only&&row.status==='awaiting_memory'?'<button class="primary-btn" data-agent-analysis>确认章后资料</button><button class="ghost-btn" data-agent-action="advance">核对确认结果</button>':''}
 ${!row.restored_read_only&&row.status==='review_ready'?(row.mode==='plan'?'<button class="ghost-btn" data-agent-planning>打开规划建立章节</button>':'<button class="ghost-btn" data-agent-analysis>章后记忆</button><button class="ghost-btn" data-agent-review>故事审稿</button>'):''}
 ${!row.restored_read_only&&['awaiting_authorization','awaiting_job','awaiting_adoption','awaiting_memory','awaiting_plan_confirmation','awaiting_plan_application'].includes(row.status)?'<button class="ghost-btn" data-agent-action="cancel">停止任务</button>':''}
 </div><p class="form-error" data-agent-action-error role="alert"></p></article>`).join('')||'<div class="empty">还没有写作任务。</div>');
 host.querySelector('[data-agent-return-all]')?.addEventListener('click',()=>{
  if(!authorAgentCurrent(scope,host))return;
  authorAgentFocus=null;authorAgentExpandedScope={...scope,chapter_id:activeChapter};
  host.innerHTML='<div class="empty">正在读取本章写作任务…</div>';authorLoadAgent();
 });
 host.querySelectorAll('[data-agent-session]').forEach(section=>{
  const row=authorAgentRows.find(item=>item.id===section.dataset.agentSession);
  section.querySelectorAll('[data-agent-action]').forEach(button=>button.onclick=async()=>{
   if(authorAgentBusy||!authorAgentCurrent(scope,host))return;
   const action=button.dataset.agentAction,actionIdentity={...scope};
   authorAgentBusy=true;section.querySelectorAll('button').forEach(b=>b.disabled=true);
   try{
    if(!await syncBackend())throw new Error('请先保存或合并当前稿件');
    if(!authorAgentCurrent(scope,host))return;
    if(row.mode==='plan'&&authorBlueprintDirty&&!await authorSaveBlueprint())throw new Error('当前规划需要先保存');
    if(!authorAgentCurrent(scope,host))return;
    if(row.mode==='plan'&&action==='apply'){authorBlueprintApply=actionIdentity;clearTimeout(authorBlueprintTimer);authorRenderBlueprint()}
    await authorJSON(`/api/writing-agent/${encodeURIComponent(row.id)}/resume`,{method:'POST',headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch},body:JSON.stringify({action,checkpoint_id:row.checkpoint_id})});
    if(row.mode==='plan'&&action==='apply'&&scope.owner===requestedProject&&scope.branch===(state.branch_id||'main')){
     const data=await authorJSON('/api/blueprint',{headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch}});
     if(scope.owner===requestedProject&&scope.branch===(state.branch_id||'main'))authorReceiveBlueprint(data);
    }
    if(authorAgentCurrent(scope,host)){authorAgentBusy=false;await authorLoadAgent();scheduleQueue(0)}
   }catch(error){if(authorAgentCurrent(scope,host))section.querySelector('[data-agent-action-error]').textContent=error.message}
   finally{
    if(authorBlueprintApply===actionIdentity){authorBlueprintApply=null;authorRenderBlueprint()}
    authorAgentBusy=false;if(section.isConnected)section.querySelectorAll('button').forEach(b=>b.disabled=false);
   }
  });
  const openChapter=()=>{
   if(!authorAgentCurrent(scope,host))return false;
   if(!chapter(row.chapter_id)){toast('来源章节已不存在');return false}
   if(view==='editor'&&activeChapter===row.chapter_id)return true;
   activeChapter=row.chapter_id;view='editor';render();return true;
  };
  const openAnalysis=()=>{
   if(!openChapter())return;
   const analysisId=row.result_refs?.chapter_analysis?.id||row.current_job?.result?.analysis?.id;
   if(!analysisId){toast('本次章后资料身份缺失，请刷新任务后重试');return}
   authorOpenChapterAnalysis(row.chapter_id,analysisId);
  };
  section.querySelector('[data-agent-open]')?.addEventListener('click',()=>{
   const job=row.current_job;if(!job||!authorAgentCurrent(scope,host))return;
   if(job.status==='success'&&job.task==='chapter_analysis'){openAnalysis();return}
   const candidate=receiveCandidateJob(job);if(!openChapter())return;
   if(job.status==='success'&&job.task==='semantic_review')actions.reviewChapter();
   else if(candidate){displayCandidate(candidate);authorRevealWorkbenchSection('workbenchCandidateSection')}
  });
  section.querySelector('[data-agent-analysis]')?.addEventListener('click',openAnalysis);
  section.querySelector('[data-agent-review]')?.addEventListener('click',()=>{if(openChapter())actions.reviewChapter()});
  section.querySelector('[data-agent-planning]')?.addEventListener('click',()=>{
   if(!authorAgentCurrent(scope,host))return;
   authorPlanningSelected=row.planning_workflow?.id||null;view='blueprint';render();
  });
 });
}

function authorAgentPlanningPreview(row){
 const workflow=row.mode==='plan'&&row.planning_workflow;if(!workflow)return '';
 const counts=workflow.initial?.counts;
 const scope=counts?`<p class="agent-plan-size">${Number(counts.volumes)} 卷 · 每卷 ${Number(counts.chapters_per_volume)} 章 · 每章 ${Number(counts.scenes_per_chapter)} 场</p>`:'';
 if(['awaiting_plan_application','review_ready'].includes(row.status)){
  return `${scope}<details class="agent-complete-planning" ${row.status==='awaiting_plan_application'?'open':''}><summary>${row.status==='review_ready'?'已采用的完整规划':'查看完整规划'}</summary>
   <div class="planning-candidate agent-planning-candidate">${authorPlanningPreview('overview',workflow.blueprint,workflow.blueprint)}
   ${authorPlanningStages.slice(1).map(([stage,label])=>`<h4>${label}规划</h4>${authorPlanningPreview(stage,workflow.blueprint,workflow.blueprint)}`).join('')}</div></details>`;
 }
 const candidate=workflow.attempt?.candidate;
 return scope+(candidate?`<div class="planning-candidate agent-planning-candidate"><h4>${esc(authorPlanningStages.find(([key])=>key===workflow.stage)?.[1]||'规划')}候选</h4>${authorPlanningPreview(workflow.stage,candidate,workflow.blueprint)}</div>`:'');
}

function authorBindAssistant(){
 authorLoadAgent();
 document.querySelector('[data-agent-focus-chat]')?.addEventListener('click',()=>globalThis.MoyuAgentChat?.focus());
 for(const [selector,target] of [['[data-agent-open-settings]','settings'],['[data-agent-open-tasks]','tasks']])
  document.querySelector(selector)?.addEventListener('click',()=>{view=target;render()});
}

async function authorLoadPage(){
 const token=++authorLoadToken;const owner=requestedProject;const which=view;
 let settingsSequence=null;
 if(which==='settings'&&location.protocol==='file:'){
  const host=document.querySelector('#settingsHost');
  if(host)host.innerHTML='<div class="settings-unavailable" role="status"><h3>请在墨屿软件中配置模型</h3><p>当前打开的是网页文件，无法安全保存密钥或连接模型。打开已安装的墨屿，进入“模型与风格”即可配置；使用网页版时，请从正在运行的本机创作工作台进入。</p></div>';
  return;
 }
 try{
  if(which==='library'){
   const data=await authorJSON('/api/projects');if(token!==authorLoadToken)return;
   authorProjects=data.projects||[];authorRenderProjects();
  }
  if(which==='blueprint'){
   if(authorBlueprintDirty||authorBlueprintLocked()){authorRenderBlueprint();await authorLoadPlanning(owner);return}
   const data=await authorJSON('/api/blueprint');if(token!==authorLoadToken||owner!==requestedProject)return;
   let draft;try{draft=JSON.parse(storage.get(authorBlueprintDraftKey())||'null')}catch{}
   authorReceiveBlueprint(data,draft);
   await authorLoadPlanning(owner);
  }
  if(which==='settings'){
   settingsSequence=authorBeginSettingsOperation();
   const data=await authorJSON('/api/ai/settings');
   if(token!==authorLoadToken||owner!==requestedProject||settingsSequence!==authorSettingsSeq)return;
   const settings=data.settings||data;
   if(!Array.isArray(settings.connections)){
    const library=await authorJSON('/api/ai/connections');
    if(token!==authorLoadToken||owner!==requestedProject||settingsSequence!==authorSettingsSeq)return;
    settings.connections=library.connections||[];settings.legacy=library.legacy||null;
    settings.connection_workflow=library.connection_workflow===true;
   }
   await loadAIStatus();
   if(token!==authorLoadToken||owner!==requestedProject||settingsSequence!==authorSettingsSeq||view!=='settings')return;
   authorSettings=settings;authorRenderSettings();
  }
  if(which==='backups'){
   const data=await authorJSON('/api/backups');if(token!==authorLoadToken||owner!==requestedProject)return;
   authorBackups=data.backups||[];authorRenderBackups();
  }
  if(which==='references'){
   const data=await authorJSON('/api/references');if(token!==authorLoadToken||owner!==requestedProject)return;
   authorReferences=data.references||[];authorRenderReferences();
  }
 }catch(error){if(token===authorLoadToken&&owner===requestedProject&&
   (which!=='settings'||settingsSequence===authorSettingsSeq)){
  const host=document.querySelector({library:'#projectList',blueprint:'#blueprintHost',settings:'#settingsHost',backups:'#backupList',references:'#referenceList'}[which]||'#taskList');if(host)host.innerHTML=`<div class="empty">${esc(error.message)}<button class="ghost-btn" data-author-reload>重新连接</button></div>`;host?.querySelector('[data-author-reload]')?.addEventListener('click',authorLoadPage)
 }}
}

function authorLoadEditorBlueprint(){
 if(authorBlueprint||!backendOnline||!state.chapters.some(c=>c.volume_id&&!c.volume_title))return;
 const owner=requestedProject,branch=state.branch_id||'main';
 if(authorEditorBlueprintRequest?.owner===owner&&authorEditorBlueprintRequest.branch===branch)return;
 const request={owner,branch};authorEditorBlueprintRequest=request;
 request.promise=(async()=>{
  try{
   const data=await authorJSON('/api/blueprint',{headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch}});
   if(authorEditorBlueprintRequest!==request||owner!==requestedProject||branch!==(state.branch_id||'main')||
    authorBlueprint||authorBlueprintDirty||authorBlueprintLocked())return;
   let draft;try{draft=JSON.parse(storage.get(authorBlueprintDraftKey())||'null')}catch{}
   authorReceiveBlueprint(data,draft);
  }catch{}
  finally{if(authorEditorBlueprintRequest===request)authorEditorBlueprintRequest=null}
 })();
}

function authorRenderProjects(){
 const host=document.querySelector('#projectList');if(!host)return;
 host.innerHTML=authorProjects.map(p=>`<article class="book-row ${p.id===requestedProject?'current-book':''}"><div><h3>${esc(p.title)}</h3>
 <p>${esc(p.genre||'未分类')} · ${Number(p.chapter_count||0)} 章 · ${Number(p.characters||0).toLocaleString()} 字${p.archived?' · 已归档':''}</p>
 <p>${esc(p.summary||'')}</p></div><div class="book-actions"><button class="primary-btn" data-project-open="${safeId(p.id)}">${p.id===requestedProject?'当前作品':'打开'}</button>
 <button class="ghost-btn" data-project-copy="${safeId(p.id)}">复制</button><button class="ghost-btn" data-project-archive="${safeId(p.id)}" data-archived="${p.archived?'1':'0'}">${p.archived?'恢复':'归档'}</button></div></article>`).join('')||'<div class="empty">还没有作品。</div>';
 host.querySelectorAll('[data-project-open]').forEach(button=>button.onclick=()=>authorSwitchProject(button.dataset.projectOpen));
 host.querySelectorAll('[data-project-copy]').forEach(button=>button.onclick=()=>authorDuplicateProject(button.dataset.projectCopy));
 host.querySelectorAll('[data-project-archive]').forEach(button=>button.onclick=()=>authorArchiveProject(button.dataset.projectArchive,button.dataset.archived==='1'));
}

function authorSwitchProject(id){
 const switching=authorSwitchChain.catch(()=>{}).then(()=>authorPerformProjectSwitch(id));
 authorSwitchChain=switching.catch(()=>{});return switching;
}

async function authorPerformProjectSwitch(id){
 if(id===requestedProject){
  const loaded=backendOnline&&state.project.id===id||await hydrate();
  view='editor';render();return !!loaded&&backendOnline&&requestedProject===id&&state.project.id===id;
 }
 if(backendOnline&&!await syncBackend()){toast('当前草稿需要先保存或合并，切换已暂停');return false}
 if(authorBlueprintDirty&&!await authorSaveBlueprint())return false;
 cacheDraft();persistCandidates();clearTimeout(syncTimer);++authorLoadToken;
 invalidateReferenceScope();
  authorInvalidatePortablePreview();
 for(const controller of streamReaders.values())controller.abort();
 streamReaders.clear();jobSources.clear();clearTimeout(queueTimer);
 backendOnline=false;requestedProject=id;storage.set(CURRENT_PROJECT_KEY,id);
 state=load();state.branch_id=authorProjects.find(p=>p.id===id)?.branch_id||state.branch_id;
 confirmedState=loadConfirmed();candidateStore=loadCandidates();activeChapter=storage.get(cacheKey(':active'))||state.chapters[0]?.id;
 authorBlueprint=null;authorBlueprintVersion=null;authorBlueprintDirty=false;authorBlueprintBase=null;authorBlueprintConflict=null;authorSettings=null;++authorSettingsSeq;queueJobs=[];
 clearTimeout(authorPlanningTimer);authorPlanning=null;authorPlanningRows=[];authorPlanningSelected=null;authorPlanningBusy=false;
 aiDraft=null;aiRunId=null;mergePending=null;saveError='';savedSelection=null;storyLoaded=false;storyLoadedChapter=null;
 storyMap={relationships:[],timeline:[],foreshadowing:[],conflicts:[],character_states:[],knowledge_scope:{}};
 view='dashboard';render();const loaded=await hydrate();if(loaded)await loadAIStatus();scheduleQueue(0);
 return !!loaded&&backendOnline&&requestedProject===id&&state.project.id===id;
}

function authorRememberProject(project){
 if(!project?.id)throw new Error('作品已经保存，但服务没有返回作品身份，请从书架重新打开');
 const existing=authorProjects.findIndex(item=>item.id===project.id);
 if(existing<0)authorProjects.push(project);else authorProjects[existing]={...authorProjects[existing],...project};
 return project;
}
function authorLockCommittedForm(wrap){
 wrap.querySelectorAll('input,textarea,select,[data-import-split],[data-import-merge]').forEach(control=>control.disabled=true);
}
async function authorOpenCommittedProject(project,wrap,label){
 authorRememberProject(project);authorLockCommittedForm(wrap);
 let opened=false;
 try{opened=await authorSwitchProject(project.id)}catch{}
 if(opened)return;
 wrap.querySelector('[type="submit"],[data-import-confirm]').textContent='重新打开作品';
 throw new Error(`${label}已经保存，但暂时无法打开。请重新打开这份作品，原作品继续保留。`);
}
actions.createProject=()=>{
 let createdProject=null;
 return authorForm('新建作品',`<label>作品名<input id="newBookTitle" required></label>
 <label>一句话故事<textarea id="newBookSummary" maxlength="50000"></textarea></label><label>题材<input id="newBookGenre" maxlength="100"></label>
 <label>目标字数<input id="newBookGoal" type="number" value="50000" min="0" max="1000000000"></label>`,async w=>{
  if(!createdProject){
   const title=w.querySelector('#newBookTitle').value.trim();if(!title)throw new Error('请给新作品起个名字');
   if(Array.from(title).length>200)throw new Error('作品名不能超过200字');
   try{
    await durableOperation('/api/projects','POST',{title,summary:w.querySelector('#newBookSummary').value,
     genre:w.querySelector('#newBookGenre').value,goal:Number(w.querySelector('#newBookGoal').value)},null,data=>{
      createdProject=authorRememberProject(data.project||data.state?.project);
     });
   }catch(error){if(!createdProject)throw error}
  }
  await authorOpenCommittedProject(createdProject,w,'新作品');toast('新作品已打开');
 });
};
async function authorDuplicateProject(id){
 const p=authorProjects.find(p=>p.id===id);
 const title=Array.from(p?.title||'作品').slice(0,195).join('')+' · 副本';
 let copiedProject=null;
 authorForm('复制作品',`<label>新作品名<input id="copyTitle" value="${esc(title)}"></label>`,async w=>{
  if(!copiedProject){
   const title=w.querySelector('#copyTitle').value.trim();if(!title)throw new Error('请给副本起个名字');
   if(Array.from(title).length>200)throw new Error('作品名不能超过200字');
   try{
    await durableOperation(`/api/projects/${encodeURIComponent(id)}/duplicate`,'POST',{title},null,data=>{
     copiedProject=authorRememberProject(data.project||data.state?.project);
    });
   }catch(error){if(!copiedProject)throw error}
  }
  await authorOpenCommittedProject(copiedProject,w,'作品副本');toast('作品副本已打开');
 });
}
async function authorArchiveProject(id,archived){
 try{
  if(!await syncBackend())throw new Error('当前作品需要先保存或合并');
  await authorJSON(`/api/projects/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({archived:!archived})});
  await authorLoadPage();toast(archived?'作品已恢复到书架':'作品已归档，可以随时恢复');
 }catch(error){toast(error.message)}
}

function authorForm(title,body,submit,label='保存'){
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.innerHTML=`<form class="modal author-form"><h3>${esc(title)}</h3>${body}
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" type="button" data-close>取消</button><button class="primary-btn" type="submit">${label}</button></div></form>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('form').onsubmit=async event=>{
  event.preventDefault();const button=wrap.querySelector('[type="submit"]');button.disabled=true;
  try{await submit(wrap);wrap.remove()}catch(error){wrap.querySelector('.form-error').textContent=error.message;button.disabled=false}
 };
 return wrap;
}

function authorPlanningPreview(stage,candidate,blueprint=authorPlanning?.blueprint){
 if(!candidate)return '';
 if(stage==='overview')return `<dl class="planning-overview">${[['premise','故事核心'],['genre','题材'],['audience','读者'],['tone','基调'],['contract','创作约定']].map(([key,label])=>
  `<div><dt>${label}</dt><dd>${esc(candidate[key]||'')}</dd></div>`).join('')}</dl>`;
 return (candidate.volumes||[]).map(volume=>`<section class="planning-volume"><h4>${esc(volume.title||
  blueprint?.volumes?.find(item=>item.id===volume.id)?.title||'卷')}</h4>
  ${stage==='volumes'?`<p>${esc(volume.summary)}</p>`:(volume.chapters||[]).map(chapter=>`<div class="planning-chapter"><strong>${esc(chapter.title||
   blueprint?.volumes?.flatMap(item=>item.chapters||[]).find(item=>item.id===chapter.id)?.title||'章节')}</strong>
   ${stage==='chapters'?`<p>${esc(chapter.goal)}<br>视角：${esc(chapter.pov)}<br>章节出口：${esc(chapter.hook)}</p>`:
    `<ol>${(chapter.scenes||[]).map(scene=>`<li><strong>${esc(scene.title)}</strong><p>${esc(scene.goal)} · ${esc(scene.location)} · ${esc(scene.pov)}</p></li>`).join('')}</ol>`}</div>`).join('')}</section>`).join('');
}

async function authorLoadPlanning(owner=requestedProject){
 clearTimeout(authorPlanningTimer);
 const branch=state.branch_id||'main';const data=await authorJSON('/api/planning-workflows');
 if(owner!==requestedProject||branch!==(state.branch_id||'main')||view!=='blueprint')return;
 authorPlanningRows=data.workflows||[];
 authorPlanning=authorPlanningRows.find(row=>row.id===authorPlanningSelected)||authorPlanningRows[0]||null;
 authorPlanningSelected=authorPlanning?.id||null;authorRenderPlanning();
 const attempt=authorPlanning?.attempt;
 if(authorPlanning?.status==='active'&&['queued','running'].includes(attempt?.status)){
  authorPlanningTimer=setTimeout(()=>authorLoadPlanning(owner).catch(error=>{
   if(owner!==requestedProject||branch!==(state.branch_id||'main')||view!=='blueprint')return;
   const host=document.querySelector('#planningWorkflowHost');if(host)host.innerHTML=`<p class="form-error">${esc(error.message)}</p><button class="ghost-btn" data-planning-refresh>重新连接</button>`;
   host?.querySelector('[data-planning-refresh]')?.addEventListener('click',()=>authorLoadPlanning());
  }),800);
 }
}

function authorRenderPlanning(){
 const host=document.querySelector('#planningWorkflowHost');if(!host)return;
 const workflow=authorPlanning;
 if(!workflow){host.innerHTML='<div class="empty">暂无分阶段规划。</div>';return}
 const attempt=workflow.attempt;const completed=workflow.status==='completed';const canceled=workflow.status==='canceled';
 const running=['queued','running'].includes(attempt?.status);const unknown=attempt?.status==='unknown';
 const candidateReady=attempt?.status==='complete'&&!completed&&!canceled;
 const stageLabel=authorPlanningStages.find(([key])=>key===workflow.stage)?.[1]||'';
 const status=workflow.applied?'已采用':canceled?'已停止':completed?'四阶段已确认':candidateReady?'候选待确认':
  ({queued:'排队中',running:'生成中',unknown:'请求结果未知',failed:'当前阶段未完成'})[attempt?.status]||'待生成';
 host.innerHTML=`${authorPlanningRows.length>1?`<label class="planning-history">规划记录<select data-planning-history aria-label="规划记录">
  ${authorPlanningRows.map(row=>`<option value="${safeId(row.id)}" ${row.id===workflow.id?'selected':''}>${esc(row.initial?.premise?.slice(0,40)||'规划')} · ${esc(row.created_at)}</option>`).join('')}</select></label>`:''}
  <ol class="planning-steps">${authorPlanningStages.map(([key,label],index)=>
  `<li class="${index<workflow.stage_index?'confirmed':key===workflow.stage&&!completed?'current':''}">${index+1}. ${label}</li>`).join('')}</ol>
  <div class="planning-heading"><span data-planning-status>${esc(status)}</span><div class="plan-actions">
  ${!completed&&!canceled?`<button class="ghost-btn" data-planning-cancel>停止规划</button>`:''}
  ${!completed&&!canceled&&!running&&!unknown&&!candidateReady?`<button class="primary-btn" data-planning-start>${attempt?.status==='failed'?'重做当前阶段':'生成'+stageLabel}</button>`:''}
  ${candidateReady?'<button class="primary-btn" data-planning-confirm>确认本阶段</button>':''}
  ${completed&&!workflow.applied?'<button class="primary-btn" data-planning-apply>采用完整规划</button>':''}
  <button class="ghost-btn" data-planning-refresh title="刷新规划">刷新</button></div></div>
  ${unknown?'<p class="form-error">上次请求是否计费还不明确，已保留记录，未重复发送。</p>':''}
  ${attempt?.error?`<p class="form-error">${esc(attempt.error)}</p>`:''}
  ${canceled&&workflow.cancel_reason?`<p class="modal-help">${esc(workflow.cancel_reason)}</p>`:''}
  <div class="planning-candidate">${authorPlanningPreview(workflow.stage,attempt?.candidate)}</div>
  <p class="form-error" data-planning-error role="alert"></p>`;
 host.querySelector('[data-planning-history]')?.addEventListener('change',event=>{
  authorPlanningSelected=event.target.value;authorLoadPlanning().catch(error=>toast(error.message));
 });
 host.querySelector('[data-planning-refresh]')?.addEventListener('click',()=>authorLoadPlanning().catch(error=>toast(error.message)));
 const action=async(kind,payload)=>{
  if(authorPlanningBusy)return;const owner=requestedProject;const branch=state.branch_id||'main';
  const actionIdentity={owner,branch};
  authorPlanningBusy=actionIdentity;host.querySelectorAll('button').forEach(button=>button.disabled=true);
  try{
   if(authorBlueprintDirty&&!await authorSaveBlueprint())throw new Error('当前规划需要先保存');
   if(owner!==requestedProject||branch!==(state.branch_id||'main'))throw new Error('作品已经切换');
   if(kind==='apply'){
    authorBlueprintApply=actionIdentity;clearTimeout(authorBlueprintTimer);authorRenderBlueprint();
   }
   const result=await authorJSON(`/api/planning-workflows/${encodeURIComponent(workflow.id)}/${kind}`,
    {method:'POST',headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch},body:JSON.stringify(payload)});
   if(owner!==requestedProject||branch!==(state.branch_id||'main'))return;
   if(kind==='apply')authorReceiveBlueprint(result);
   await authorLoadPlanning(owner);scheduleQueue(0);
  }catch(error){
   if(owner===requestedProject){const errorHost=document.querySelector('[data-planning-error]');if(errorHost)errorHost.textContent=error.message}
  }finally{
   if(authorBlueprintApply===actionIdentity){authorBlueprintApply=null;authorRenderBlueprint()}
   if(authorPlanningBusy===actionIdentity){authorPlanningBusy=false;authorRenderPlanning()}
  }
 };
 const identity={expected_stage:workflow.stage,expected_version:workflow.version};
 host.querySelector('[data-planning-start]')?.addEventListener('click',()=>action('start',identity));
 host.querySelector('[data-planning-confirm]')?.addEventListener('click',()=>action('confirm',
  {...identity,attempt_id:attempt.id,candidate_hash:attempt.candidate_hash}));
 host.querySelector('[data-planning-cancel]')?.addEventListener('click',()=>action('cancel',{}));
 host.querySelector('[data-planning-apply]')?.addEventListener('click',()=>action('apply',{expected_source_version:workflow.source_version}));
 if(authorPlanningBusy?.owner===requestedProject&&authorPlanningBusy.branch===(state.branch_id||'main'))
  host.querySelectorAll('button,select').forEach(control=>control.disabled=true);
}

actions.beginPlanning=()=>authorForm('新建分阶段规划',`<label>一句话故事<textarea id="planningPremise" maxlength="10000" required>${esc(authorBlueprint?.premise||state.project.summary||'')}</textarea></label>
 <div class="settings-fields"><label>卷数<input id="planningVolumes" type="number" min="1" max="10" value="2" required></label>
 <label>每卷章节<input id="planningChapters" type="number" min="1" max="20" value="2" required></label>
 <label>每章场景<input id="planningScenes" type="number" min="1" max="10" value="2" required></label></div>`,async wrap=>{
 const owner=requestedProject;if(!await syncBackend())throw new Error('当前稿件需要先保存');
 if(authorBlueprintDirty&&!await authorSaveBlueprint())throw new Error('当前规划需要先保存');
 if(owner!==requestedProject)throw new Error('作品已经切换');
 const existing=authorPlanningRows.find(row=>row.status==='active');
 if(existing?.status==='active')throw new Error('请先停止当前规划，再开始新规划');
 const initial={premise:wrap.querySelector('#planningPremise').value.trim(),genre:authorBlueprint?.genre||state.project.genre||'',
  audience:authorBlueprint?.audience||'',tone:authorBlueprint?.tone||'',contract:authorBlueprint?.contract||'',
  counts:{volumes:Number(wrap.querySelector('#planningVolumes').value),
   chapters_per_volume:Number(wrap.querySelector('#planningChapters').value),scenes_per_chapter:Number(wrap.querySelector('#planningScenes').value)}};
 const created=await authorJSON('/api/planning-workflows',{method:'POST',headers:{'X-Moyu-Project':owner},body:JSON.stringify({initial})});
 authorPlanningSelected=created.id;
 await authorLoadPlanning(owner);
},'建立规划');
actions.openPlanning=id=>{authorPlanningSelected=id;view='blueprint';render()};

function authorRenderBlueprint(){
 const host=document.querySelector('#blueprintHost');if(!host||!authorBlueprint)return;
 const b=authorBlueprint;host.innerHTML=`${authorBlueprintConflict?'<p class="form-error">另一处也修改了规划，当前草稿已保留。<button class="ghost-btn" data-blueprint-merge>核对并合并</button></p>':''}<div class="blueprint-fields">
 ${[['premise','一句话故事'],['genre','题材'],['audience','读者'],['tone','风格基调'],['contract','创作约定'],['outline','故事总纲']].map(([key,label])=>
 `<label>${label}${['genre','audience','tone'].includes(key)?`<input data-blueprint-field="${key}" value="${esc(b[key]||'')}">`:
 `<textarea data-blueprint-field="${key}">${esc(typeof b[key]==='string'?b[key]:JSON.stringify(b[key]||'',null,2))}</textarea>`}</label>`).join('')}</div>
 <div class="plan-volumes">${(b.volumes||[]).map((v,vi)=>`<section class="plan-volume"><header><div><h3>${esc(v.title)}</h3><p>${esc(v.summary||'')}</p></div>
 <div class="plan-actions"><button class="ghost-btn" data-plan-move="volume" data-index="${vi}" data-direction="-1" title="上移卷" ${vi===0?'disabled':''}>↑</button>
 <button class="ghost-btn" data-plan-move="volume" data-index="${vi}" data-direction="1" title="下移卷" ${vi===b.volumes.length-1?'disabled':''}>↓</button>
 <button class="ghost-btn" data-volume-edit="${safeId(v.id)}">编辑卷</button><button class="ghost-btn" data-plan-add-chapter="${safeId(v.id)}">＋ 章节</button></div></header>
 ${(v.chapters||[]).map((c,ci)=>`<div class="plan-chapter"><div class="plan-chapter-heading"><div><strong>${esc(c.title)}</strong><p>${esc(c.goal||'')}</p></div>
 <div class="plan-actions"><button class="ghost-btn" data-plan-move="chapter" data-volume="${safeId(v.id)}" data-index="${ci}" data-direction="-1" title="上移章节" ${ci===0?'disabled':''}>↑</button>
 <button class="ghost-btn" data-plan-move="chapter" data-volume="${safeId(v.id)}" data-index="${ci}" data-direction="1" title="下移章节" ${ci===v.chapters.length-1?'disabled':''}>↓</button>
 <button class="ghost-btn" data-plan-edit-chapter="${safeId(c.id)}" data-volume="${safeId(v.id)}">编辑</button><button class="ghost-btn" data-plan-add-scene="${safeId(c.id)}" data-volume="${safeId(v.id)}">＋ 场景</button></div></div>
 ${(c.scenes||[]).map((s,si)=>`<div class="plan-scene"><span>${si+1}</span><div><strong>${esc(s.title)}</strong><small>${esc(s.goal||'')} · ${esc(s.pov||c.pov||'')} · ${esc(s.location||'')}</small></div>
 <button class="ghost-btn" data-plan-edit-scene="${safeId(s.id)}" data-chapter="${safeId(c.id)}" data-volume="${safeId(v.id)}">编辑</button></div>`).join('')}</div>`).join('')}</section>`).join('')||'<div class="empty">还没有卷章规划。</div>'}</div>`;
 host.querySelectorAll('[data-blueprint-field]').forEach(input=>input.oninput=()=>{
  if(authorBlueprintLocked())return;
  authorBlueprint[input.dataset.blueprintField]=input.value;authorBlueprintChanged();
 });
 host.querySelectorAll('[data-volume-edit]').forEach(button=>button.onclick=()=>authorVolumeForm(authorBlueprint.volumes.find(v=>v.id===button.dataset.volumeEdit)));
 host.querySelectorAll('[data-plan-add-chapter]').forEach(button=>button.onclick=()=>authorPlannedChapterForm(button.dataset.planAddChapter));
 host.querySelectorAll('[data-plan-edit-chapter]').forEach(button=>button.onclick=()=>authorPlannedChapterForm(button.dataset.volume,button.dataset.planEditChapter));
 host.querySelectorAll('[data-plan-add-scene]').forEach(button=>button.onclick=()=>authorSceneForm(button.dataset.volume,button.dataset.planAddScene));
 host.querySelectorAll('[data-plan-edit-scene]').forEach(button=>button.onclick=()=>authorSceneForm(button.dataset.volume,button.dataset.chapter,button.dataset.planEditScene));
 host.querySelectorAll('[data-plan-move]').forEach(button=>button.onclick=async()=>{
  if(authorBlueprintLocked())return;
  const rows=button.dataset.planMove==='volume'?authorBlueprint.volumes:authorBlueprint.volumes.find(v=>v.id===button.dataset.volume)?.chapters;
  const i=Number(button.dataset.index),j=i+Number(button.dataset.direction);if(!rows||j<0||j>=rows.length)return;
  [rows[i],rows[j]]=[rows[j],rows[i]];authorBlueprintDirty=true;authorCacheBlueprint();if(await authorSaveBlueprint())authorRenderBlueprint();
 });
 host.querySelector('[data-blueprint-merge]')?.addEventListener('click',authorMergeBlueprint);
 document.querySelectorAll('#blueprintHost input,#blueprintHost textarea,#blueprintHost button,[data-action="addVolume"],[data-action="materializePlan"],[data-action="saveBlueprint"],[data-action="beginPlanning"]').forEach(control=>{
  if(authorBlueprintLocked()){if(!authorBlueprintFrozen.has(control))authorBlueprintFrozen.set(control,control.disabled);control.disabled=true}
  else if(authorBlueprintFrozen.has(control)){control.disabled=authorBlueprintFrozen.get(control);authorBlueprintFrozen.delete(control)}
 });
}
function authorMergeBlueprint(){
 const pending=authorBlueprintConflict;if(!pending||authorBlueprintLocked())return;
 authorForm('核对规划修改',pending.conflicts.map((conflict,index)=>`<label>${esc(conflict.path)}<select data-blueprint-choice="${index}">
  <option value="local">保留本机草稿</option><option value="remote">使用另一处修改</option></select></label>
  <div class="plan-grid"><pre class="prompt-preview">${esc(JSON.stringify(conflict.local,null,2))}</pre><pre class="prompt-preview">${esc(JSON.stringify(conflict.remote,null,2))}</pre></div>`).join(''),async wrap=>{
  const choices={};wrap.querySelectorAll('[data-blueprint-choice]').forEach(select=>{
   const conflict=pending.conflicts[Number(select.dataset.blueprintChoice)];choices[conflict.path]=conflict[select.value];
  });
  const result=MoyuSync.merge(pending.base,authorBlueprint,pending.remote,choices);
  if(result.conflicts.length)throw new Error('规划仍有不同修改，请重新核对');
  authorBlueprint=result.value;authorBlueprintConflict=null;authorBlueprintDirty=true;authorCacheBlueprint();
  if(!await authorSaveBlueprint())throw new Error('合并规划尚未保存，草稿已保留');
  authorRenderBlueprint();
 },'保存合并规划');
}
async function authorSaveBlueprint(){
 if(!authorBlueprint||authorBlueprintLocked())return false;
 if(authorBlueprintConflict){authorCacheBlueprint();authorRenderBlueprint();return false}
 clearTimeout(authorBlueprintTimer);
 if(authorBlueprintPromise){
  const pending=authorBlueprintPromise,saved=await pending;
  if(authorBlueprintPromise===pending)authorBlueprintPromise=null;
  if(!saved)return false;if(authorBlueprintDirty)return authorSaveBlueprint();return true;
 }
 const sent=MoyuSync.copy(authorBlueprint);const owner=requestedProject;
 authorBlueprintPromise=(async()=>{
 try{
  const result=await durableOperation('/api/blueprint','PUT',{blueprint:sent,expected_version:authorBlueprintVersion});
  if(owner!==requestedProject)return false;
  authorBlueprint=MoyuSync.merge(sent,authorBlueprint,result.blueprint||sent).value;
  authorBlueprintVersion=result.version??authorBlueprintVersion;authorBlueprintBase=MoyuSync.copy(result.blueprint||sent);
  authorBlueprintDirty=!MoyuSync.equal(authorBlueprint,result.blueprint||sent);authorCacheBlueprint();
  toast('规划已经保存');return true;
 }catch(error){
  authorCacheBlueprint();
  if(owner===requestedProject)try{
   const remote=await authorJSON('/api/blueprint');
   if(owner===requestedProject&&remote.version!==authorBlueprintVersion)
    authorReceiveBlueprint(remote,{blueprint:authorBlueprint,base:authorBlueprintBase,version:authorBlueprintVersion});
  }catch{}
  toast(`规划未保存，草稿已保留：${error.message}`);return false;
 }
 })();
 const pending=authorBlueprintPromise;
 try{
  const saved=await pending;
  if(authorBlueprintPromise===pending)authorBlueprintPromise=null;
  if(saved&&authorBlueprintDirty)return authorSaveBlueprint();
  return saved;
 }finally{if(authorBlueprintPromise===pending)authorBlueprintPromise=null}
}
function authorVolumeForm(item){
 if(authorBlueprintLocked())return toast('正在采用完整规划，请稍候');
 if(!authorBlueprint)return toast('请等待规划读取完成');
 const x=item||{};authorForm(item?'编辑卷':'新增卷',`<label>卷名<input id="volumeTitle" value="${esc(x.title||'')}" maxlength="500" required></label><label>主要变化<textarea id="volumeSummary">${esc(x.summary||'')}</textarea></label>`,async w=>{
  if(authorBlueprintLocked())throw new Error('正在采用完整规划，请稍候');
  const current=authorBlueprint.volumes?.find(v=>v.id===x.id)||x;
  const v={...current,id:x.id||crypto.randomUUID(),title:w.querySelector('#volumeTitle').value.trim(),summary:w.querySelector('#volumeSummary').value,chapters:current.chapters||[]};
  if(!v.title)throw new Error('请填写卷名');
  authorBlueprint.volumes??=[];if(item)authorBlueprint.volumes[authorBlueprint.volumes.findIndex(row=>row.id===item.id)]=v;else authorBlueprint.volumes.push(v);
  authorBlueprintDirty=true;authorCacheBlueprint();if(!await authorSaveBlueprint())throw new Error('规划未保存，请保留当前页面再重试');authorRenderBlueprint();
 });
}
function authorPlannedChapterForm(volumeId,chapterId){
 if(authorBlueprintLocked())return toast('正在采用完整规划，请稍候');
 const v=authorBlueprint?.volumes?.find(v=>v.id===volumeId);if(!v)return;
 const x=v.chapters?.find(c=>c.id===chapterId)||{};
 authorForm(chapterId?'编辑章节规划':'新增章节规划',`<label>章节标题<input id="planChapterTitle" value="${esc(x.title||'')}" required></label>
 <label>本章目标<textarea id="planChapterGoal">${esc(x.goal||'')}</textarea></label><label>视角<input id="planChapterPov" value="${esc(x.pov||'')}"></label>
 <label>章节出口<textarea id="planChapterHook">${esc(x.hook||'')}</textarea></label>`,async w=>{
  if(authorBlueprintLocked())throw new Error('正在采用完整规划，请稍候');
  const volume=authorBlueprint.volumes?.find(v=>v.id===volumeId);if(!volume)throw new Error('卷已变化，请重新打开规划');
  const current=volume.chapters?.find(c=>c.id===chapterId)||x;
  const row={...current,id:x.id||crypto.randomUUID(),title:w.querySelector('#planChapterTitle').value.trim(),goal:w.querySelector('#planChapterGoal').value,pov:w.querySelector('#planChapterPov').value,hook:w.querySelector('#planChapterHook').value,scenes:current.scenes||[]};
  if(!row.title)throw new Error('请填写章节标题');
  volume.chapters??=[];if(chapterId)volume.chapters[volume.chapters.findIndex(c=>c.id===chapterId)]=row;else volume.chapters.push(row);
  authorBlueprintDirty=true;authorCacheBlueprint();if(!await authorSaveBlueprint())throw new Error('规划未保存，请保留当前页面再重试');authorRenderBlueprint();
 });
}
function authorSceneForm(volumeId,chapterId,sceneId){
 if(authorBlueprintLocked())return toast('正在采用完整规划，请稍候');
 const c=authorBlueprint?.volumes?.find(v=>v.id===volumeId)?.chapters?.find(c=>c.id===chapterId);if(!c)return;
 const x=c.scenes?.find(s=>s.id===sceneId)||{};
 authorForm(sceneId?'编辑场景':'新增场景',`<label>场景名<input id="planSceneTitle" value="${esc(x.title||'')}" required></label>
 <label>场景目标<textarea id="planSceneGoal">${esc(x.goal||'')}</textarea></label><label>地点<input id="planSceneLocation" value="${esc(x.location||'')}"></label>
 <label>视角<input id="planScenePov" value="${esc(x.pov||c.pov||'')}"></label>`,async w=>{
  if(authorBlueprintLocked())throw new Error('正在采用完整规划，请稍候');
  const currentChapter=authorBlueprint.volumes?.find(v=>v.id===volumeId)?.chapters?.find(c=>c.id===chapterId);
  if(!currentChapter)throw new Error('章节规划已变化，请重新打开');
  const current=currentChapter.scenes?.find(s=>s.id===sceneId)||x;
  const row={...current,id:x.id||crypto.randomUUID(),title:w.querySelector('#planSceneTitle').value.trim(),goal:w.querySelector('#planSceneGoal').value,location:w.querySelector('#planSceneLocation').value,pov:w.querySelector('#planScenePov').value};
  if(!row.title)throw new Error('请填写场景名');currentChapter.scenes??=[];if(sceneId)currentChapter.scenes[currentChapter.scenes.findIndex(s=>s.id===sceneId)]=row;else currentChapter.scenes.push(row);
  authorBlueprintDirty=true;authorCacheBlueprint();if(!await authorSaveBlueprint())throw new Error('规划未保存，请保留当前页面再重试');authorRenderBlueprint();
 });
}
actions.addVolume=()=>authorVolumeForm();
actions.saveBlueprint=authorSaveBlueprint;
actions.materializePlan=async()=>{
 if(!authorBlueprint||!await authorSaveBlueprint())return;
 try{await durableOperation('/api/blueprint/materialize','POST',{expected_version:authorBlueprintVersion});render();toast('卷章计划已接入正文目录')}
 catch(error){toast(`章节建立未完成：${error.message}`)}
};
actions.refreshTasks=()=>scheduleQueue(0);

function authorProbeIdentityStatus(probe){
 if(!probe)return '';
 if(['exact','mismatch','unreported'].includes(probe.identity_status))return probe.identity_status;
 if(probe.model_identity_match===true)return 'exact';
 if(probe.returned_model&&probe.model_identity_match===false)return 'mismatch';
 return probe.success===true&&!probe.returned_model?'unreported':'';
}

function authorProbeDescription(probe,model=''){
 if(!probe)return '';
 const requested=probe.requested_model||probe.model||model;
 const identity=requested?` · 请求 ${requested}${probe.returned_model?` · 回传 ${probe.returned_model}`:''}`:'';
 const time=probe.checked_at?` · ${probe.checked_at}`:'';
 if(probe.configuration_stale)return `本次检测已失效：检测期间设置发生变化，不能验证当前设置${identity}${time}`;
 if(authorProbeIdentityStatus(probe)==='mismatch')
  return `连接检测未通过：请求模型与服务回传名称不一致${identity}${time}。按服务回传名称重新选择并测试，或向服务商确认别名。`;
 if(probe.success&&authorProbeIdentityStatus(probe)==='unreported')
  return `连接可用，服务未回传模型名称，仅验证可调用${identity}${time}`;
 return probe.success?`最近默认模型实测成功（连接回复符合要求）${identity}${time}`:
  `最近连接检测未通过：${probe.error||'模型未返回符合要求的回复'}${identity}${time}`;
}

function authorBeginSettingsOperation(){
 ++aiStatusSeq;
 return ++authorSettingsSeq;
}

function authorRenderSettings(){
 const host=document.querySelector('#settingsHost');if(!host||!authorSettings)return;
 authorSettingsDispose?.();
 const s=authorSettings,owner=requestedProject,branch=state.branch_id||'main';
 const parameters=s.parameters||{},styles=s.styles||{},taskConnections=s.task_connections||{};
 const taskParameters=s.task_parameters||{},tasks=Object.entries(AI_TASK_NAMES),clearedTasks=new Set();
 let connections=Array.isArray(s.connections)?s.connections:[],legacy=!s.connection_workflow?
  (s.legacy||(s.base_url&&s.model?{base_url:s.base_url,model:s.model,api_key_configured:s.api_key_configured,
   api_protocol:s.api_protocol,output_token_parameter:s.output_token_parameter,include_usage:s.include_usage}:null)):null;
 let editingId=legacy?'legacy':'',sourceConnectionId='',editingKeyConfigured=!!legacy?.api_key_configured,editingBaseUrl=legacy?.base_url||'';
 let receipt=null,busy=false,disposed=false,deletionDialog=null,returnedModelCandidate='';
 let modelPicker=null;
 const frozenControls=new Map();
 const reasoningOptions=[['','服务默认'],['none','关闭（通用）'],['minimal','最少'],['low','较少'],['medium','适中'],
  ['high','较多'],['xhigh','充分'],['deepseek_disabled','关闭（DeepSeek）'],['deepseek_enabled','开启（DeepSeek）']];
 const reasoningValue=value=>value.thinking?.type?'deepseek_'+value.thinking.type:value.reasoning_effort||'';
 const reasoningSelect=(value,emptyLabel)=>reasoningOptions.map(([key,label])=>
  `<option value="${key}" ${reasoningValue(value)===key?'selected':''}>${key?label:emptyLabel}</option>`).join('');
 const aliases={chapter_analysis:'summary',semantic_review:'check'},aliasLabels={summary:'回顾',check:'检查'};
 const connectionIdentity=item=>authorProbeIdentityStatus(item.last_probe||item);
 const identityWarning='连接可用，服务未回传模型名称，仅验证可调用';
 const connectionOptions=(selected='',empty='沿用默认模型')=>{
  const verified=connections.filter(item=>item.verified===true);
  const unavailable=selected&&!verified.some(item=>item.id===selected);
  const previous=unavailable&&connections.find(item=>item.id===selected);
  return `<option value="">${esc(empty)}</option>`+
   (unavailable?`<option value="${esc(selected)}" selected disabled>${esc(previous?.name||previous?.model||'原模型连接')} · 连接失效，请重新测试或重新选择</option>`:'')+
   verified.map(item=>`<option value="${esc(item.id)}" ${selected===item.id?'selected':''}>${esc(item.name||item.model)} · ${esc(item.model)}${connectionIdentity(item)==='unreported'?' · 模型名称未回传':''}</option>`).join('');
 };
 host.innerHTML=`<section class="settings-connection-section" aria-labelledby="modelConnectionHeading">
 <div class="settings-section-heading"><h3 id="modelConnectionHeading">模型配置</h3><div class="settings-library-tools"><span id="modelConnectionCount"></span>
 <button type="button" class="primary-btn" id="connectionAdd">新增模型</button></div></div>
 ${legacy?'<p class="settings-legacy-note">现有连接继续保留，完成测试登记后可选择用途。<button type="button" class="ghost-btn" id="connectionRegisterLegacy">登记现有模型</button></p>':''}
 <div id="modelConnectionList" class="settings-connection-list"></div>
 <dialog id="connectionDialog" class="settings-connection-dialog" aria-labelledby="connectionEditorHeading">
 <form id="modelConnectionForm" class="settings-connection-form" autocomplete="off">
 <div class="settings-connection-form-heading"><h4 id="connectionEditorHeading">新增模型</h4>
 <button type="button" class="ghost-btn" id="connectionReset">清空重填</button></div>
 <div class="settings-fields">
 <label class="field-wide">服务商<select id="connectionProvider">${MoyuModelPicker.providers.map(item=>`<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('')}</select></label>
 <label>服务地址<input id="connectionBaseUrl" type="url" required value="${esc(legacy?.base_url||'')}" placeholder="https://example.com/v1"></label>
 <label>访问密钥<input id="connectionKey" type="password" autocomplete="new-password" value="" placeholder="${legacy?.api_key_configured?'已保存密钥，留空继续使用':'本机免密模型可留空'}"></label>
 <label>接入方式<select id="connectionApiProtocol"><option value="chat_completions" ${legacy?.api_protocol!=='responses'?'selected':''}>通用兼容（Chat）</option>
 <option value="responses" ${legacy?.api_protocol==='responses'?'selected':''}>Responses</option></select></label>
 <label>显示名称（可选）<input id="connectionName" maxlength="100" value="" placeholder="留空时使用模型名称"></label>
 <div class="moyu-model-field"><label for="connectionModel">模型名称</label><div class="moyu-model-row"><div class="moyu-model-combobox">
 <input id="connectionModel" maxlength="200" required value="${esc(legacy?.model||'')}" placeholder="搜索、选择或手动填写模型名称" autocomplete="off" spellcheck="false" aria-describedby="connectionModelListStatus">
 <button type="button" class="ghost-btn" id="connectionModelToggle" aria-label="显示模型列表">选择模型</button>
 <div id="connectionModelList" class="moyu-model-options" hidden></div></div>
 <button type="button" class="ghost-btn" id="connectionFetchModels">获取模型列表</button></div>
 <p id="connectionModelListStatus" class="moyu-model-status" role="status" hidden></p></div></div>
 <details class="settings-transport"><summary>兼容设置</summary><div class="settings-fields">
 <label>自定义请求路径<input id="connectionRequestPath" maxlength="2048" placeholder="留空使用标准路径，例如 /v1/chat/completions"></label>
 <label>模型清单路径<input id="connectionModelsPath" maxlength="2048" placeholder="留空使用标准路径，例如 /custom/models"></label>
 <label class="field-wide">请求标识<input id="connectionUserAgent" maxlength="256" placeholder="留空使用 Moyu/1"></label></div>
 <div class="settings-compatibility"><div class="settings-fields">
 <label>输出长度参数<select id="connectionOutputTokenParameter"><option value="max_tokens" ${legacy?.output_token_parameter!=='max_completion_tokens'?'selected':''}>常规（max_tokens）</option>
 <option value="max_completion_tokens" ${legacy?.output_token_parameter==='max_completion_tokens'?'selected':''}>兼容（max_completion_tokens）</option></select></label>
 <label class="settings-checkbox"><input id="connectionIncludeUsage" type="checkbox" ${legacy?.include_usage===true?'checked':''}>请求流式用量统计</label></div></div></details>
 <dl class="settings-endpoints"><div><dt>模型请求</dt><dd id="connectionRequestEndpoint">填写服务地址后显示</dd></div>
 <div><dt>模型清单</dt><dd id="connectionModelsEndpoint">填写服务地址后显示</dd></div></dl>
 <p id="connectionSourceStatus" class="settings-source-status" role="status" hidden></p>
 <p id="connectionProbeStatus" class="settings-connection-status" role="status">${legacy?'现有配置尚未登记；测试并保存后可分配用途。':'尚未测试连接。'}</p>
 <button class="ghost-btn settings-returned-model-action" type="button" id="connectionUseReturnedModel" aria-describedby="connectionProbeStatus" hidden>使用回传名称</button>
 <p class="form-error" role="alert"></p><div class="settings-actions">
 <button class="ghost-btn" type="button" id="connectionCancel">取消</button>
 <button class="ghost-btn" type="button" id="connectionProbe">测试连接（调用模型）</button>
 <button class="ghost-btn" type="submit" id="connectionSave" disabled>仅保存</button>
 <button class="primary-btn" type="submit" id="connectionSaveDefault" disabled>保存并用于写作</button></div></form></dialog></section>
 <form id="modelSettings" class="settings-form"><div class="settings-section-heading"><h3>写作与风格</h3></div>
 <p id="modelRoutingEmpty" class="settings-routing-empty"></p><div class="settings-fields">
 <label>默认模型<select id="modelDefault">${connectionOptions(s.default_connection_id,'暂不设置默认模型')}</select></label>
 <label>创作风格<input id="modelTone" value="${esc(styles.tone||'')}"></label>
 <label class="field-wide">写作要求<textarea id="modelStyleInstructions">${esc(styles.instructions||'')}</textarea></label>
 </div><details class="settings-more-parameters" id="modelAdvancedParameters"><summary>更多写作参数</summary><div class="settings-fields">
 <label>变化程度<input id="modelTemperature" type="number" min="0" max="2" step="any" value="${parameters.temperature??''}" placeholder="按用途默认"></label>
 <label>每次输出上限<input id="modelMaxTokens" type="number" min="1" max="128000" step="1" value="${parameters.max_tokens??3000}"></label>
 <label>模型思考程度<select id="modelReasoning">${reasoningSelect(parameters,'服务默认')}</select></label>
 <label>每日生成上限<input id="modelDailyLimit" type="number" min="0" max="100000" value="${s.daily_limit??50}"></label></div></details>
 <p id="modelProtocolWarning" class="settings-protocol-warning" role="status" hidden></p>
 <details class="settings-task-section" id="modelTaskSettings"><summary id="modelTaskHeading">按用途选择模型与参数</summary>
 <div class="settings-task-table" role="table" aria-label="用途模型与参数"><div class="settings-task-header" role="row">
 <span role="columnheader">用途</span><span role="columnheader">模型</span><span role="columnheader">变化程度</span>
 <span role="columnheader">输出上限</span><span role="columnheader">思考程度</span><span role="columnheader" aria-label="清除用途参数"></span></div>
 ${tasks.map(([key,label])=>{
  const override=taskParameters[key]||{};
  return `<div class="settings-task-row" data-task-row="${key}" role="row"><strong role="rowheader">${label}</strong>
  <div role="cell" data-column-label="模型"><select data-task-model="${key}" aria-label="${label}模型">${connectionOptions(taskConnections[key])}</select></div>
  <div role="cell" data-column-label="变化程度"><input data-task-parameter="temperature" type="number" min="0" max="2" step="any" aria-label="${label}变化程度" value="${esc(override.temperature??'')}" placeholder="沿用全局"></div>
  <div role="cell" data-column-label="输出上限"><input data-task-parameter="max_tokens" type="number" min="1" max="128000" step="1" aria-label="${label}输出上限" value="${esc(override.max_tokens??'')}" placeholder="沿用全局"></div>
  <div role="cell" data-column-label="思考程度"><select data-task-parameter="reasoning" aria-label="${label}思考程度">${reasoningSelect(override,'沿用全局')}</select></div>
  <div role="cell"><button type="button" class="icon-btn settings-task-clear" data-task-clear="${key}" title="清除${label}用途参数" aria-label="清除${label}用途参数"><span aria-hidden="true">↺</span></button></div></div>`;
 }).join('')}</div></details>
 <p id="modelProbeStatus" class="modal-help"></p>
 <p class="form-error" role="alert"></p><div class="settings-actions">
 <button class="primary-btn" type="submit">保存设置</button></div></form>`;
 const form=host.querySelector('#modelSettings'),connectionForm=host.querySelector('#modelConnectionForm');
 form.addEventListener('invalid',event=>{
  const details=event.target.closest('details');if(details)details.open=true;
 },true);
 const connectionDialog=host.querySelector('#connectionDialog');
 const connectionKey=connectionForm.querySelector('#connectionKey'),connectionStatus=connectionForm.querySelector('#connectionProbeStatus');
 const connectionSave=connectionForm.querySelector('#connectionSave');
 const connectionSaveDefault=connectionForm.querySelector('#connectionSaveDefault');
 const returnedModelButton=connectionForm.querySelector('#connectionUseReturnedModel');
 const fetchModelsButton=connectionForm.querySelector('#connectionFetchModels');
 const current=sequence=>!disposed&&owner===requestedProject&&branch===(state.branch_id||'main')&&
  sequence===authorSettingsSeq&&view==='settings'&&form.isConnected&&connectionForm.isConnected;
 authorSettingsDispose=()=>{
  disposed=true;receipt=null;connectionKey.value='';clearReturnedModel();modelPicker?.dispose();deletionDialog?.remove();
  if(connectionDialog.open)connectionDialog.close();
  authorSettingsDispose=null;
 };
 const draft=()=>{
  const value=id=>connectionForm.querySelector('#'+id).value.trim();
  return {id:editingId,source_connection_id:sourceConnectionId,provider:value('connectionProvider'),
   base_url:value('connectionBaseUrl'),model:value('connectionModel'),api_key:value('connectionKey'),
   api_protocol:value('connectionApiProtocol'),
   custom_request_path:value('connectionRequestPath'),models_path:value('connectionModelsPath'),user_agent:value('connectionUserAgent'),
   output_token_parameter:value('connectionOutputTokenParameter'),include_usage:connectionForm.querySelector('#connectionIncludeUsage').checked};
 };
 const discoverySnapshot=()=>{
  const submitted=draft(),body={base_url:submitted.base_url,api_protocol:submitted.api_protocol,
   provider:submitted.provider,custom_request_path:submitted.custom_request_path,models_path:submitted.models_path,user_agent:submitted.user_agent};
  const reusable=editingKeyConfigured&&MoyuModelPicker.sameService(submitted.base_url,editingBaseUrl);
  if(submitted.id)body.id=submitted.id;
  if(submitted.source_connection_id)body.source_connection_id=submitted.source_connection_id;
  if(submitted.api_key||!reusable)body.api_key=submitted.api_key;
  const result=MoyuModelPicker.connection(body,reusable);
  return editingKeyConfigured&&!reusable&&!submitted.api_key?
   {...result,ready:false,hint:'服务地址已变更，请重新填写该服务的密钥。'}:result;
 };
 modelPicker=MoyuModelPicker.mount({
  input:connectionForm.querySelector('#connectionModel'),list:connectionForm.querySelector('#connectionModelList'),
  toggle:connectionForm.querySelector('#connectionModelToggle'),refresh:fetchModelsButton,
  status:connectionForm.querySelector('#connectionModelListStatus'),snapshot:discoverySnapshot,
  current:()=>connectionDialog.open&&current(authorSettingsSeq),
  request:async(body,signal)=>{
   const response=await fetch('/api/ai/connections/models',{method:'POST',headers:{
    'Content-Type':'application/json','X-Moyu-Project':owner,'X-Moyu-Branch':branch},body:JSON.stringify(body),signal});
   const result=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(result.error||`服务暂时没有返回模型列表（${response.status}）`);
   return result;
  },
 });
 const updateConnectionProtocol=()=>{
  connectionForm.querySelector('.settings-compatibility').hidden=
   connectionForm.querySelector('#connectionApiProtocol').value==='responses';
  const endpoints=MoyuModelPicker.endpoints(draft());
  connectionForm.querySelector('#connectionRequestEndpoint').textContent=endpoints?.request||'请填写有效的服务地址与路径';
  connectionForm.querySelector('#connectionModelsEndpoint').textContent=endpoints?.models||'请填写有效的服务地址与路径';
 };
 const sameReceipt=()=>!!receipt&&receipt.tuple===JSON.stringify(draft());
 const updateSaveButton=()=>{
  connectionSave.disabled=busy||!sameReceipt();connectionSaveDefault.disabled=connectionSave.disabled;
 };
 const clearReturnedModel=()=>{returnedModelCandidate='';returnedModelButton.hidden=true};
 const invalidateProbe=event=>{
  if(['connectionBaseUrl','connectionKey','connectionApiProtocol','connectionRequestPath','connectionModelsPath','connectionUserAgent'].includes(event?.target?.id)){
   updateConnectionProtocol();modelPicker.changed();
  }
  clearReturnedModel();receipt=null;updateSaveButton();connectionForm.querySelector('.form-error').textContent='';
  connectionStatus.textContent='连接信息已修改，需要重新测试。';
  connectionStatus.dataset.state='untested';delete connectionStatus.dataset.identity;
 };
 const updateRoutingStatus=()=>{
  const active=connections.find(item=>item.id===authorSettings.default_connection_id&&item.verified===true);
  form.querySelector('#modelProbeStatus').textContent=active?`已保存的默认模型：${active.name||active.model} · ${active.model}（${connectionIdentity(active)==='unreported'?identityWarning:'已通过连接测试'}）`:
   authorSettings.default_connection_id?'已保存的默认模型连接失效，请重新测试或重新选择。':
   legacy?'现有配置继续生效，完成登记后可分配用途。':'尚未分配默认模型。';
 };
 const renderLibrary=()=>{
  host.querySelector('#modelConnectionCount').textContent=`${connections.filter(item=>item.verified===true).length} 个可用模型`;
  host.querySelector('#modelConnectionList').innerHTML=connections.length?connections.map(item=>{
   const uses=tasks.filter(([key])=>authorSettings.task_connections?.[key]===item.id).map(([,label])=>label);
   if(authorSettings.default_connection_id===item.id)uses.unshift('默认');
   const unreported=item.verified===true&&connectionIdentity(item)==='unreported';
   const provider=MoyuModelPicker.providers.find(provider=>provider.id===(item.provider||'openai_compatible'))?.label||'自定义兼容服务';
   return `<article class="settings-connection-row" data-model-connection="${esc(item.id)}"><div class="settings-connection-info">
   <strong>${esc(item.name||item.model)}<span class="settings-connection-state" data-verified="${item.verified===true}" data-identity="${unreported?'unreported':''}">${item.verified===true?(unreported?'连接可用，名称未核实':'已测试并保存'):'待重新验证'}</span></strong>
   <span>${esc(item.model)} · ${esc(provider)} · ${item.api_protocol==='responses'?'Responses':'Chat'}</span><small>${esc(item.base_url)}</small>${unreported?`<small class="settings-connection-identity-warning">${identityWarning}</small>`:''}${uses.length?`<small>已用于：${esc(uses.join('、'))}</small>`:''}</div>
   <div class="settings-connection-actions"><button type="button" class="ghost-btn" data-connection-edit="${esc(item.id)}" ${busy?'disabled':''}>编辑</button>
   <button type="button" class="ghost-btn" data-connection-copy="${esc(item.id)}" ${busy?'disabled':''}>添加同服务模型</button>
   <button type="button" class="ghost-btn" data-connection-delete="${esc(item.id)}" ${busy?'disabled':''}>删除</button></div></article>`;
  }).join(''):'<p class="settings-library-empty">还没有模型。新增连接，测试通过后即可开始写作。</p>';
  host.querySelectorAll('[data-connection-edit]').forEach(button=>button.onclick=()=>{
   if(busy)return;
   const item=connections.find(row=>row.id===button.dataset.connectionEdit);if(!item)return;
   openConnection(item.id,item);
  });
  host.querySelectorAll('[data-connection-copy]').forEach(button=>button.onclick=()=>{
   if(busy)return;
   const item=connections.find(row=>row.id===button.dataset.connectionCopy);if(!item)return;
   openConnection('',item,{source:item.id});
  });
  host.querySelectorAll('[data-connection-delete]').forEach(button=>button.onclick=()=>{
   if(busy||disposed)return;
   const item=connections.find(row=>row.id===button.dataset.connectionDelete);if(!item)return;
   deletionDialog=authorForm('删除模型连接',`<p class="modal-help">删除“${esc(item.name||item.model)}”的连接记录，作品内容继续保留。正在使用的连接不能删除。</p>
    <label class="confirmation-check"><input type="checkbox" required>确认删除这条模型连接</label>`,async()=>{
    const sequence=authorBeginSettingsOperation();
    if(!current(sequence))throw new Error('设置页面已更新，请重新打开设置');
    receipt=null;setBusy(true);
    try{
     const result=await durableOperation('/api/ai/connections/'+encodeURIComponent(item.id),'DELETE',{},()=>current(sequence));
     if(!current(sequence))return;
     updateLibrary(result.settings,connections.filter(row=>row.id!==item.id));
     if(editingId===item.id)fillConnection('',{});
     toast('模型连接已删除');
    }finally{setBusy(false)}
   },'删除连接');
  });
 };
 const refreshConnectionOptions=()=>{
  const verified=connections.some(item=>item.verified===true);
  for(const select of form.querySelectorAll('#modelDefault,[data-task-model]')){
   const selected=select.value;
   select.innerHTML=connectionOptions(selected,select.id==='modelDefault'?'暂不设置默认模型':'沿用默认模型');
   select.disabled=busy||(!verified&&!selected);
  }
  const empty=form.querySelector('#modelRoutingEmpty');empty.hidden=verified;
  empty.textContent=legacy?'现有模型尚未登记。先测试并保存连接，再选择默认模型。':
   connections.length?'现有模型连接均待重新验证，请重新测试或调整已有用途分配。':'模型库为空，暂无可分配的模型。';
  updateTaskHints();updateRoutingStatus();
 };
 const updateLibrary=(settings,fallback=connections)=>{
  if(settings)authorSettings=settings;
  connections=Array.isArray(settings?.connections)?settings.connections:fallback;
  if(settings)legacy=!settings.connection_workflow?(settings.legacy||(settings.base_url&&settings.model?
   {base_url:settings.base_url,model:settings.model,api_key_configured:settings.api_key_configured,
    api_protocol:settings.api_protocol,output_token_parameter:settings.output_token_parameter,include_usage:settings.include_usage}:null)):null;
  renderLibrary();refreshConnectionOptions();
 };
 const fillConnection=(id,item,{discover=true,source=''}={})=>{
  clearReturnedModel();editingId=id;sourceConnectionId=source;editingKeyConfigured=!!item.api_key_configured;editingBaseUrl=item.base_url||'';receipt=null;connectionKey.value='';
  connectionForm.querySelector('#connectionProvider').value=item.provider||'openai_compatible';
  connectionForm.querySelector('#connectionName').value=source?'':item.name||'';
  connectionForm.querySelector('#connectionBaseUrl').value=item.base_url||'';
  connectionForm.querySelector('#connectionModel').value=source?'':item.model||'';
  connectionForm.querySelector('#connectionApiProtocol').value=item.api_protocol==='responses'?'responses':'chat_completions';
  connectionForm.querySelector('#connectionRequestPath').value=item.custom_request_path||'';
  connectionForm.querySelector('#connectionModelsPath').value=item.models_path||'';
  connectionForm.querySelector('#connectionUserAgent').value=item.user_agent||'';
  connectionForm.querySelector('.settings-transport').open=!!(item.custom_request_path||item.models_path||item.user_agent);
  connectionForm.querySelector('#connectionOutputTokenParameter').value=
   item.output_token_parameter==='max_completion_tokens'?'max_completion_tokens':'max_tokens';
  connectionForm.querySelector('#connectionIncludeUsage').checked=item.include_usage===true;
  connectionKey.placeholder=editingKeyConfigured?'已保存密钥，留空继续使用':'本机免密模型可留空';
  connectionForm.querySelector('#connectionEditorHeading').textContent=source?'添加同服务模型':id==='legacy'?'登记现有模型':id?'编辑模型连接':'新增模型';
  const sourceStatus=connectionForm.querySelector('#connectionSourceStatus');
  sourceStatus.hidden=!source;
  sourceStatus.textContent=source?'沿用这条连接保存在本机的密钥，请选择另一个模型并重新测试。':'';
  connectionForm.querySelector('.form-error').textContent='';
  connectionStatus.textContent=id?'编辑后需要重新测试并保存。':'尚未测试连接。';
  connectionStatus.dataset.state='untested';delete connectionStatus.dataset.identity;updateSaveButton();
  updateConnectionProtocol();modelPicker.changed({suppress:!discover,reset:true});
 };
 const openConnection=(id,item,options={})=>{
  fillConnection(id,item,options);
  if(!connectionDialog.open)connectionDialog.showModal();
  modelPicker.setDisabled(false);modelPicker.changed();
  connectionForm.querySelector(options.source?'#connectionModel':'#connectionProvider').focus();
 };
 const closeConnection=()=>{
  if(busy)return;
  modelPicker.setDisabled(true,{clear:true});
  if(connectionDialog.open)connectionDialog.close();
  fillConnection('',{},{discover:false});
 };
 const setBusy=value=>{
  modelPicker.setDisabled(value);
  busy=value;host.setAttribute('aria-busy',String(value));
  if(value){
   for(const control of host.querySelectorAll('input,textarea,select,button')){
    if(!frozenControls.has(control))frozenControls.set(control,control.disabled);
    control.disabled=true;
   }
  }else{
   for(const [control,disabled] of frozenControls)control.disabled=disabled;
   frozenControls.clear();
   if(!disposed){renderLibrary();refreshConnectionOptions();updateSaveButton();modelPicker.setDisabled(!connectionDialog.open)}
  }
 };
 const setNumber=(target,name,value)=>{
  if(value.trim()==='')delete target[name];else target[name]=Number(value);
 };
 const setReasoning=(target,value)=>{
  delete target.thinking;delete target.reasoning_effort;
  if(value.startsWith('deepseek_'))target.thinking={type:value.slice('deepseek_'.length)};
  else if(value)target.reasoning_effort=value;
 };
 const rowParameters=row=>{
  const task=row.dataset.taskRow,next=clearedTasks.has(task)?{}:{...taskParameters[task]};
  for(const name of ['temperature','max_tokens'])setNumber(next,name,row.querySelector(`[data-task-parameter="${name}"]`).value);
  setReasoning(next,row.querySelector('[data-task-parameter="reasoning"]').value);return next;
 };
 const updateProtocolHints=()=>{
  const defaultId=form.querySelector('#modelDefault').value;
  const protocol=id=>(connections.find(item=>item.id===id)||(id?'':legacy))?.api_protocol||'chat_completions';
  const defaultResponses=protocol(defaultId)==='responses',warnings=[];
  const globalThinking=form.querySelector('#modelReasoning');
  const globalLength=form.querySelector('#modelMaxTokens');
  const markThinking=(control,responses,label,value=control.value)=>{
   for(const option of control.options)if(option.value.startsWith('deepseek_'))
    option.hidden=responses&&option.value!==control.value;
   const invalid=responses&&value.startsWith('deepseek_');
   control.setCustomValidity(invalid?'Responses 不支持 DeepSeek 专用思考设置，请选择通用思考程度。':'');
   if(invalid)warnings.push(`${label}仍使用 DeepSeek 专用思考设置，请调整后保存。`);
  };
  const markLength=(control,responses,label,effective=control.value)=>{
   control.min=responses?'16':'1';
   const invalid=responses&&effective!==''&&Number(effective)<16;
   control.setCustomValidity(invalid?'Responses 的有效输出上限至少为 16；请调整该用途或它沿用的设置。':'');
   if(invalid)warnings.push(`${label}的 Responses ${control.value?'':'沿用'}输出上限至少为 16。`);
  };
  markThinking(globalThinking,defaultResponses,'默认模型');
  markLength(globalLength,defaultResponses,'默认模型');
  for(const row of form.querySelectorAll('[data-task-row]')){
   const task=row.dataset.taskRow,alias=aliases[task];
   const assigned=row.querySelector('[data-task-model]').value;
   const inheritedId=alias?form.querySelector(`[data-task-model="${alias}"]`)?.value:'';
   const responses=protocol(assigned||inheritedId||defaultId)==='responses';
   const control=row.querySelector('[data-task-parameter="reasoning"]');
   const inheritsAlias=alias&&!Object.keys(rowParameters(row)).length;
   const aliasThinking=inheritsAlias?form.querySelector(`[data-task-row="${alias}"] [data-task-parameter="reasoning"]`)?.value:'';
   const aliasLength=inheritsAlias?form.querySelector(`[data-task-row="${alias}"] [data-task-parameter="max_tokens"]`)?.value:'';
   markThinking(control,responses,AI_TASK_NAMES[task],control.value||aliasThinking||globalThinking.value);
   const length=row.querySelector('[data-task-parameter="max_tokens"]');
   markLength(length,responses,AI_TASK_NAMES[task],length.value||aliasLength||globalLength.value);
  }
  const warning=form.querySelector('#modelProtocolWarning');
  warning.hidden=!warnings.length;warning.textContent=warnings.join(' ');
 };
 const updateTaskHints=()=>{
  const rows=[...form.querySelectorAll('[data-task-row]')];
  const overrides=Object.fromEntries(rows.map(row=>[row.dataset.taskRow,rowParameters(row)]));
  for(const row of rows){
   const task=row.dataset.taskRow,alias=aliases[task];
   const inherited=alias&&!Object.keys(overrides[task]).length&&Object.keys(overrides[alias]).length;
   const hint=inherited?'沿用'+aliasLabels[alias]:'沿用全局';
   const title=alias?`未单设时沿用${AI_TASK_NAMES[alias]}用途；单设后空项沿用全局`:'留空沿用全局设置';
   row.querySelectorAll('[data-task-parameter]').forEach(control=>{
    control.title=title;
    if(control.tagName==='SELECT')control.options[0].textContent=hint;else control.placeholder=hint;
   });
   if(alias)row.querySelector('[data-task-model]').options[0].textContent=
    form.querySelector(`[data-task-model="${alias}"]`).value?'沿用'+aliasLabels[alias]+'模型':'沿用默认模型';
  }
  updateProtocolHints();
 };
 form.querySelectorAll('[data-task-clear]').forEach(button=>button.onclick=()=>{
  const row=button.closest('[data-task-row]');clearedTasks.add(button.dataset.taskClear);
  row.querySelectorAll('[data-task-parameter]').forEach(control=>control.value='');
  updateTaskHints();
 });
 form.querySelectorAll('[data-task-parameter],[data-task-model],#modelDefault,#modelReasoning,#modelMaxTokens').forEach(control=>{
  control.addEventListener('input',updateTaskHints);control.addEventListener('change',updateTaskHints);
 });
 const payload=()=>{
  const value=id=>form.querySelector('#'+id).value;
  const nextConnections={};form.querySelectorAll('[data-task-model]').forEach(select=>{
   if(select.value)nextConnections[select.dataset.taskModel]=select.value;
  });
  const nextTaskParameters={...taskParameters};
  form.querySelectorAll('[data-task-row]').forEach(row=>{
   const task=row.dataset.taskRow,next=rowParameters(row);
   if(Object.keys(next).length)nextTaskParameters[task]=next;else delete nextTaskParameters[task];
  });
  const result={default_connection_id:value('modelDefault'),task_connections:nextConnections,
   styles:{tone:value('modelTone'),instructions:value('modelStyleInstructions')},
   parameters:{...parameters},task_parameters:nextTaskParameters,daily_limit:Number(value('modelDailyLimit'))};
  setNumber(result.parameters,'temperature',value('modelTemperature'));
  setNumber(result.parameters,'max_tokens',value('modelMaxTokens'));
  setReasoning(result.parameters,value('modelReasoning'));return result;
 };
 const saveSettings=async sequence=>{
  if(!current(sequence))
   throw new Error('设置页面已更新，请重新打开设置');
  const result=await durableOperation('/api/ai/settings','PUT',payload(),
   ()=>current(sequence));
  if(!current(sequence))
   throw new Error('设置页面已更新，请重新打开设置');
  authorSettings=result.settings||result;
  await loadAIStatus();
  if(!current(sequence))
   throw new Error('设置页面已更新，请重新打开设置');
  window.dispatchEvent(new CustomEvent('moyu:main-settings-changed',
   {detail:{projectId:owner,branchId:state.branch_id||'main'}}));
  return result;
 };
 form.onsubmit=async e=>{
  e.preventDefault();if(busy)return;
  if(legacy&&!connections.some(item=>item.verified===true&&item.id===form.querySelector('#modelDefault').value)){
   form.querySelector('.form-error').textContent='请先测试并保存现有模型，再选择默认模型；当前使用的配置仍然保留。';return;
  }
  const sequence=authorBeginSettingsOperation();receipt=null;setBusy(true);
  try{await saveSettings(sequence);toast('模型与风格设置已经保存');authorRenderSettings()}
  catch(error){if(current(sequence))form.querySelector('.form-error').textContent=error.message}
  finally{setBusy(false)}
 };
 host.querySelector('#connectionAdd').onclick=()=>{if(!busy)openConnection('',{})};
 host.querySelector('#connectionRegisterLegacy')?.addEventListener('click',()=>{if(!busy&&legacy)openConnection('legacy',legacy)});
 connectionForm.querySelector('#connectionCancel').onclick=closeConnection;
 connectionDialog.addEventListener('cancel',event=>{event.preventDefault();closeConnection()});
 connectionForm.querySelector('#connectionReset').onclick=()=>{if(!busy)fillConnection('',{})};
 connectionForm.querySelector('#connectionProvider').addEventListener('change',()=>{
  const provider=MoyuModelPicker.providers.find(item=>item.id===connectionForm.querySelector('#connectionProvider').value);
  if(!provider)return;
  connectionKey.value='';editingKeyConfigured=false;editingBaseUrl='';sourceConnectionId='';
  connectionKey.placeholder='本机免密模型可留空';
  connectionForm.querySelector('#connectionSourceStatus').hidden=true;
  connectionForm.querySelector('#connectionBaseUrl').value=provider.base_url;
  connectionForm.querySelector('#connectionModel').value='';
  for(const id of ['connectionRequestPath','connectionModelsPath','connectionUserAgent'])connectionForm.querySelector('#'+id).value='';
  connectionForm.querySelector('#connectionApiProtocol').value='chat_completions';
  invalidateProbe();updateConnectionProtocol();modelPicker.changed({reset:true});
 });
 connectionForm.querySelectorAll('#connectionBaseUrl,#connectionModel,#connectionKey,#connectionApiProtocol,#connectionOutputTokenParameter,#connectionIncludeUsage,#connectionRequestPath,#connectionModelsPath,#connectionUserAgent').forEach(input=>{
  input.addEventListener('input',invalidateProbe);input.addEventListener('change',invalidateProbe);
 });
 connectionForm.querySelectorAll('#connectionBaseUrl,#connectionKey,#connectionApiProtocol,#connectionRequestPath,#connectionModelsPath,#connectionUserAgent').forEach(input=>{
  input.addEventListener('blur',()=>modelPicker.changed({immediate:true}));
 });
 connectionForm.querySelector('#connectionName').addEventListener('input',updateSaveButton);
 returnedModelButton.onclick=()=>{
  if(busy||disposed||!current(authorSettingsSeq)||!returnedModelCandidate)return;
  const model=returnedModelCandidate;
  connectionForm.querySelector('#connectionModel').value=model;
  invalidateProbe();
  connectionStatus.textContent=`已填入服务回传名称 ${model}，请点击“测试连接”重新验证。`;
  connectionForm.querySelector('#connectionModel').focus();
 };
 connectionForm.querySelector('#connectionProbe').onclick=async()=>{
  if(busy||!connectionForm.reportValidity())return;
  const submitted=draft(),tuple=JSON.stringify(submitted);
  const sequence=authorBeginSettingsOperation();clearReturnedModel();receipt=null;setBusy(true);
  connectionForm.querySelector('.form-error').textContent='';connectionStatus.textContent='正在测试模型真实响应…';
  connectionStatus.dataset.state='testing';delete connectionStatus.dataset.identity;
  try{
   if(!submitted.model)throw new Error('请填写模型名称');
   const snapshot=discoverySnapshot();if(!snapshot.valid)throw new Error(snapshot.hint);
   if(editingKeyConfigured&&!submitted.api_key&&!MoyuModelPicker.sameService(submitted.base_url,editingBaseUrl))
    throw new Error('服务地址已变更，请重新填写该服务的密钥后测试。');
   if(!['http:','https:'].includes(new URL(submitted.base_url).protocol))throw new Error('服务地址需要以 http:// 或 https:// 开头');
   const body={name:connectionForm.querySelector('#connectionName').value.trim(),base_url:submitted.base_url,model:submitted.model,
    provider:submitted.provider,api_protocol:submitted.api_protocol,
    custom_request_path:submitted.custom_request_path,models_path:submitted.models_path,user_agent:submitted.user_agent,
    output_token_parameter:submitted.output_token_parameter,include_usage:submitted.include_usage};
   if(submitted.id)body.id=submitted.id;if(submitted.api_key||!editingKeyConfigured)body.api_key=submitted.api_key;
   if(submitted.source_connection_id)body.source_connection_id=submitted.source_connection_id;
   const result=await durableOperation('/api/ai/connections/probe','POST',body,()=>current(sequence));
   if(!current(sequence)||tuple!==JSON.stringify(draft()))return;
   if(result.probe?.success===true&&typeof result.verification_token==='string'&&result.verification_token){
    receipt={token:result.verification_token,tuple,probe:result.probe};
    const identity=authorProbeIdentityStatus(result.probe);
    connectionStatus.textContent=identity==='unreported'?`${identityWarning}；保存后可分配用途。`:'连接测试成功，保存后可分配用途。';
    connectionStatus.dataset.state='success';connectionStatus.dataset.identity=identity;
   }else{
    connectionStatus.textContent=authorProbeDescription(result.probe,submitted.model)||'连接测试未通过，请检查地址、模型名称和密钥。';
    connectionStatus.dataset.state='failed';
    const returned=result.probe?.returned_model;
    if(authorProbeIdentityStatus(result.probe)==='mismatch'&&typeof returned==='string'&&returned.trim()&&
     returned.length<=200&&!/[\x00-\x1f\x7f]/.test(returned)){
     returnedModelCandidate=returned.trim();returnedModelButton.hidden=false;
    }
   }
  }catch(error){if(current(sequence)){connectionStatus.textContent=`连接测试未完成：${error.message}`;connectionStatus.dataset.state='failed'}}
  finally{setBusy(false)}
 };
 connectionForm.onsubmit=async event=>{
  event.preventDefault();if(busy||!connectionForm.reportValidity())return;
  if(!sameReceipt()){
   receipt=null;updateSaveButton();connectionForm.querySelector('.form-error').textContent='请先成功测试当前连接，再保存模型。';return;
  }
  const approved=receipt,name=connectionForm.querySelector('#connectionName').value.trim();
  const useAsDefault=event.submitter===connectionSaveDefault;
  const sequence=authorBeginSettingsOperation();setBusy(true);connectionForm.querySelector('.form-error').textContent='';
  try{
   const result=await durableOperation('/api/ai/connections','POST',{verification_token:approved.token,name,use_as_default:useAsDefault},()=>current(sequence));
   if(!current(sequence))return;
   receipt=null;connectionKey.value='';
   const updated=result.connection;
   updateLibrary(result.settings,updated?[...connections.filter(item=>item.id!==updated.id),updated]:connections);
   if(useAsDefault&&updated){form.querySelector('#modelDefault').value=updated.id;updateTaskHints();updateRoutingStatus()}
   if(updated)fillConnection(updated.id,updated,{discover:false});
   const identity=authorProbeIdentityStatus(updated?.last_probe||approved.probe);
   connectionStatus.textContent=identity==='unreported'?`模型已保存；${identityWarning}。`:'模型已测试并保存，可在下方选择用途。';
   connectionStatus.dataset.state='saved';connectionStatus.dataset.identity=identity;
   if(connectionDialog.open)connectionDialog.close();
   modelPicker.setDisabled(true,{clear:true});
   await loadAIStatus();if(!current(sequence))return;
   window.dispatchEvent(new CustomEvent('moyu:main-settings-changed',{detail:{projectId:owner,branchId:branch}}));
   toast(useAsDefault?'模型已保存，已设为默认写作模型':'模型已保存到模型库');
  }catch(error){if(current(sequence)){
   receipt=null;connectionForm.querySelector('.form-error').textContent=`模型未保存：${error.message}；请重新测试后重试。`;
   connectionStatus.textContent='保存未完成，请重新测试当前连接。';connectionStatus.dataset.state='failed';
  }}
  finally{setBusy(false)}
 };
 renderLibrary();refreshConnectionOptions();updateSaveButton();updateConnectionProtocol();modelPicker.setDisabled(true);
}

function authorRenderBackups(){
 const host=document.querySelector('#backupList');if(!host)return;
 host.innerHTML=authorBackups.map(b=>`<article class="backup-row"><div><strong>${esc(b.name||b.id)}</strong><p>${esc(b.created_at||'')} · ${Number(b.size_bytes||b.bytes||0).toLocaleString()} 字节</p>
 <small>${esc(b.verified===false?'未通过校验':'校验可用')} · ${esc(b.project_title||state.project.title)}</small></div>
 <div class="backup-actions"><button class="ghost-btn" data-backup-download="${safeId(b.id)}">下载</button>
 <button class="ghost-btn" data-backup-restore="${safeId(b.id)}">恢复预览</button></div></article>`).join('')||'<div class="empty">还没有完整备份。</div>';
 host.querySelectorAll('[data-backup-download]').forEach(button=>button.onclick=async()=>{
  try{const response=await fetch(`/api/backups/${encodeURIComponent(button.dataset.backupDownload)}/download`);if(!response.ok)throw new Error('备份下载失败');const blob=await response.blob();authorDownloadBlob(`${button.dataset.backupDownload}.zip`,blob)}
  catch(error){toast(error.message)}
 });
 host.querySelectorAll('[data-backup-restore]').forEach(button=>button.onclick=()=>authorRestoreBackup(button.dataset.backupRestore));
}
function authorDownloadBlob(name,blob){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
actions.createBackup=async()=>{
 try{await durableOperation('/api/backups','POST',{});await authorLoadPage();toast('完整备份已经创建并校验')}
 catch(error){toast(`备份未创建：${error.message}`)}
};
function authorRestoreBackup(id){
 const b=authorBackups.find(b=>b.id===id);
 let restoredProject=null;
 authorForm('从备份恢复作品',`<p class="modal-help">将用 ${esc(b?.created_at||'所选时间')} 的完整备份建立一份独立作品。当前作品和历史继续保留。</p>
 <label class="confirmation-check"><input id="restoreConfirm" type="checkbox" required>我确认建立恢复副本</label>`,async w=>{
  if(!w.querySelector('#restoreConfirm').checked)throw new Error('请先确认恢复');
  if(!restoredProject){
   try{
    await durableOperation(`/api/backups/${encodeURIComponent(id)}/restore`,'POST',{confirm:true},null,result=>{
     restoredProject=result.project;authorProjects.push(restoredProject);
    });
   }catch(error){if(!restoredProject)throw error}
  }
  if(!await authorSwitchProject(restoredProject.id)){
   w.querySelector('[type="submit"]').textContent='重新打开副本';
   throw new Error('恢复副本已建立，但暂时无法打开。请重新打开这份副本，原作品继续保留。');
  }
  toast('恢复副本已打开，原作品继续保留');
 },'建立恢复副本');
}
function authorPortableScope(){
 return {project_id:requestedProject||state.project?.id||'default-project',
  branch_id:state.branch_id||state.project?.branch_id||'main'};
}
function authorPortableScopeKey(scope=authorPortableScope()){
 return JSON.stringify([scope.project_id,scope.branch_id]);
}
function authorPortableHeaders(scope){
 return {'X-Moyu-Project':scope.project_id,'X-Moyu-Branch':scope.branch_id};
}
function authorPortableUnconfirmed(){
 let stored=[];try{stored=JSON.parse(storage.get(authorPortableUnconfirmedKey)||'[]')}catch{}
 const entries=new Map();
 for(const entry of [...(Array.isArray(stored)?stored:[]),...authorPortableUnconfirmedMemory.values()]){
  if(typeof entry?.preview_id==='string'&&/^restore_preview_[a-f0-9]{32}$/.test(entry.preview_id)&&
   typeof entry.scope?.project_id==='string'&&typeof entry.scope?.branch_id==='string')
   entries.set(entry.preview_id,{preview_id:entry.preview_id,scope:{project_id:entry.scope.project_id,
    branch_id:entry.scope.branch_id},expires_at:typeof entry.expires_at==='string'?entry.expires_at:null});
 }
 return [...entries.values()];
}
function authorWritePortableUnconfirmed(entries){
 if(!storage.set(authorPortableUnconfirmedKey,JSON.stringify(entries)))
  throw new Error('浏览器暂时无法保留备份校验身份，请稍后重试，ZIP 仍保留在电脑上。');
}
function authorRememberPortableUnconfirmed(preview,scope){
 const entry={preview_id:preview.preview_id,scope:{...scope},expires_at:preview.expires_at||null};
 authorPortableUnconfirmedMemory.set(entry.preview_id,entry);
 authorWritePortableUnconfirmed(authorPortableUnconfirmed());
 authorPortableUnconfirmedMemory.delete(entry.preview_id);
}
function authorForgetPortableUnconfirmed(token){
 authorPortableUnconfirmedMemory.delete(token);
 authorWritePortableUnconfirmed(authorPortableUnconfirmed().filter(entry=>entry.preview_id!==token));
}
function authorCancelPortable(preview,scope){
 if(!preview?.preview_id)return Promise.resolve();
 const token=preview.preview_id;
 if(authorPortableCanceling.has(token))return authorPortableCanceling.get(token);
 try{authorRememberPortableUnconfirmed(preview,scope)}catch{}
 const cancel=authorJSON('/api/backups/import/cancel',{method:'POST',headers:authorPortableHeaders(scope),
  body:JSON.stringify({preview_id:token})}).then(()=>{
   authorForgetPortableUnconfirmed(token);authorRenderPortablePending();
  }).finally(()=>authorPortableCanceling.delete(token));
 authorPortableCanceling.set(token,cancel);return cancel;
}
function authorAbandonedPortablePreviews(){
 const pending=authorPortablePending()?.request.preview_id;
 const active=authorPortableDialog?.preview?.preview_id;
 return authorPortableUnconfirmed().filter(entry=>entry.preview_id!==pending&&entry.preview_id!==active);
}
function authorReleasePortablePreviews(){
 if(authorPortableReleasePromise)return authorPortableReleasePromise;
 const release=(async()=>{
  while(true){
   const entries=authorAbandonedPortablePreviews();if(!entries.length)return;
   for(const entry of entries)await authorCancelPortable(entry,entry.scope);
  }
 })();
 authorPortableReleasePromise=release.finally(()=>{authorPortableReleasePromise=null});
 return authorPortableReleasePromise;
}
function authorInvalidatePortablePreview(){
 ++authorPortableSeq;
 const dialog=authorPortableDialog;authorPortableDialog=null;
 if(dialog){
  if(!dialog.request)authorCancelPortable(dialog.preview,dialog.scope).catch(()=>authorRenderPortablePending());
  dialog.wrap.remove();
 }
}
function authorSyncPortableScope(){
 const key=authorPortableScopeKey();
 if(authorPortableObservedScope!==key){
  authorPortableObservedScope=key;authorInvalidatePortablePreview();
 }
}
function authorPortablePending(){
 try{
  const pending=JSON.parse(storage.get(authorPortablePendingKey)||'null');
  if(pending?.scope&&pending?.request?.preview_id&&pending.request.archive_sha256&&
   typeof pending.request.title==='string')return pending;
 }catch{}
 return null;
}
function authorSavePortablePending(dialog){
 const pending={scope:dialog.receiptScope||dialog.scope,request:dialog.request,filename:dialog.filename,project:dialog.project||null};
 if(!storage.set(authorPortablePendingKey,JSON.stringify(pending)))
  throw new Error('浏览器暂时无法保存恢复身份，请稍后重试，文件仍保留在电脑上。');
}
function authorRenderPortablePending(){
 const host=document.querySelector('#portableRestorePending');if(!host)return;
 const pending=authorPortablePending();
 if(!pending){
  if(!authorAbandonedPortablePreviews().length){host.innerHTML='';return}
  host.innerHTML='<section class="portable-recovery"><strong>上次校验的备份预览仍保留</strong><p>释放旧预览后，可以重新选择完整备份。原作品继续保留。</p><p class="form-error" role="alert"></p><button type="button" class="ghost-btn" data-portable-release>释放旧预览</button></section>';
  const button=host.querySelector('[data-portable-release]');
  button.onclick=async()=>{
   button.disabled=true;
   try{await authorReleasePortablePreviews();authorRenderPortablePending()}
   catch(error){
    if(button.isConnected){host.querySelector('.form-error').textContent=error.message;button.disabled=false}
   }
  };
  return;
 }
 const same=Boolean(pending.project)||authorPortableScopeKey(pending.scope)===authorPortableScopeKey();
 host.innerHTML=`<section class="portable-recovery"><strong>${pending.project?'恢复副本已建立':'上次恢复的保存结果尚待确认'}</strong>
  <p>${esc(pending.request.title)}</p><button type="button" class="ghost-btn" data-portable-resume>
  ${same?(pending.project?'重新打开副本':'确认上次恢复结果'):'回到原作品确认恢复'}</button></section>`;
 host.querySelector('[data-portable-resume]').onclick=async()=>{
  if(!same){
   if(!await authorSwitchProject(pending.scope.project_id))return;
   if(authorPortableScopeKey(pending.scope)!==authorPortableScopeKey())return toast('请回到原作品的相同版本确认恢复');
  }
  authorPortablePreview(pending.filename,null,pending);
 };
}
actions.importBackup=async()=>{
 const pending=authorPortablePending();
 if(pending)return authorPortablePreview(pending.filename,null,pending);
 authorInvalidatePortablePreview();
 try{
  await authorReleasePortablePreviews();
  authorWritePortableUnconfirmed(authorPortableUnconfirmed());
  document.querySelector('#backupFileInput').click();
 }catch(error){authorRenderPortablePending();toast(`旧预览尚未释放：${error.message}`)}
};
document.querySelector('#backupFileInput').onchange=async event=>{
 const file=event.target.files?.[0];event.target.value='';if(!file)return;
 const pending=authorPortablePending();
 if(pending)return authorPortablePreview(pending.filename,null,pending);
 authorSyncPortableScope();authorInvalidatePortablePreview();
 const sequence=authorPortableSeq,scope=authorPortableScope();
 const current=()=>sequence===authorPortableSeq&&authorPortableScopeKey(scope)===authorPortableScopeKey();
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.portablePreview='1';
 wrap.innerHTML='<div class="modal portable-restore-modal"><h3>校验完整备份</h3><p class="modal-help portable-filename"></p><p role="status">正在读取并校验 ZIP…</p><p class="form-error" role="alert"></p><div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button></div></div>';
 wrap.querySelector('.portable-filename').textContent=file.name;document.body.append(wrap);
 authorPortableDialog={wrap,scope,filename:file.name};
 wrap.querySelector('[data-close]').onclick=authorInvalidatePortablePreview;
 let preview,requestPreview;
 try{
  if(!backendOnline)throw new Error('请先恢复连接，再校验完整备份');
  if(file.size>32*1024*1024)throw new Error('完整备份 ZIP 超过支持大小，最多 32 MiB');
  await authorReleasePortablePreviews();if(!current())return;
  authorWritePortableUnconfirmed(authorPortableUnconfirmed());
  const data_base64=bytesToBase64(new Uint8Array(await file.arrayBuffer()));if(!current())return;
  requestPreview={preview_id:'restore_preview_'+crypto.randomUUID().replace(/-/g,''),
   expires_at:new Date(Date.now()+30*60*1000).toISOString()};
  authorRememberPortableUnconfirmed(requestPreview,scope);
  authorPortableDialog.preview=requestPreview;
  preview=await authorJSON('/api/backups/import/preview',{method:'POST',headers:authorPortableHeaders(scope),
   body:JSON.stringify({filename:file.name,data_base64,preview_id:requestPreview.preview_id})});
  authorRememberPortableUnconfirmed(preview,scope);
  if(!current()||!wrap.isConnected){await authorCancelPortable(preview,scope);return}
  authorPortableDialog.preview=preview;wrap.remove();authorPortableDialog=null;
  authorPortablePreview(file.name,preview);
 }catch(error){
  if(requestPreview||preview)authorCancelPortable(requestPreview||preview,scope).catch(()=>authorRenderPortablePending());
  if(current()&&wrap.isConnected){
   wrap.querySelector('[role="status"]').textContent='完整备份尚未恢复';
   wrap.querySelector('.form-error').textContent=error.message;
  }
 }
};
function authorPortablePreview(filename,preview,pending=null){
 authorSyncPortableScope();authorInvalidatePortablePreview();
 const sequence=authorPortableSeq,receiptScope=pending?.scope||authorPortableScope();
 const scope=pending?.project?authorPortableScope():receiptScope;
 if(authorPortableScopeKey(scope)!==authorPortableScopeKey()){
  toast('请回到发起恢复的作品和版本，在备份中心确认结果');authorRenderPortablePending();return;
 }
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.portablePreview='1';
 const dialog={wrap,scope,receiptScope,preview,filename,request:pending?.request||null,project:pending?.project||null};
 authorPortableDialog=dialog;
 const current=()=>authorPortableDialog===dialog&&wrap.isConnected&&sequence===authorPortableSeq&&
  authorPortableScopeKey(scope)===authorPortableScopeKey();
 const counts=preview?.counts||{},labels={branches:'分支',chapters:'章节',revisions:'正文版本',
  reference_documents:'参考书',jobs:'历史任务',planning_workflows:'创作规划'};
 const defaults=preview?.compatibility?.absent_known_additive_tables||[];
 wrap.innerHTML=`<form class="modal portable-restore-modal"><h3>从完整备份恢复作品</h3>
  <p class="modal-help portable-filename">${esc(filename||'完整备份 ZIP')}</p>
  ${preview?`<dl class="portable-summary"><dt>原作品</dt><dd>${esc(preview.source?.title||'')}</dd>
   <dt>备份时间</dt><dd>${esc(preview.source?.created_at||'')}</dd></dl>
   <dl class="portable-counts">${Object.entries(labels).map(([key,label])=>`<div><dt>${label}</dt><dd>${Number(counts[key]||0).toLocaleString()}</dd></div>`).join('')}</dl>
   ${defaults.length?'<p class="modal-help">这份旧备份缺少后来新增的空资料表，恢复时会建立空表。</p>':''}`:
   '<p class="modal-help">继续确认上次保存的结果，同一次确认只会建立一份副本。</p>'}
  <label>恢复后的作品名<input id="portableRestoreTitle" maxlength="400" value="${esc(pending?.request.title||preview?.restore_title||'')}" ${pending?'readonly':''} required></label>
  <p class="modal-help">正文、历史和资料会恢复为独立作品。旧任务和规划只供阅读；密钥需要重新配置。</p>
  <label class="confirmation-check"><input id="portableRestoreConfirm" type="checkbox" ${pending?'checked disabled':''}>我确认建立独立作品</label>
  <p class="form-error" role="alert"></p><div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button>
  <button type="button" class="ghost-btn" data-reselect ${dialog.project?'hidden':''}>重新选择 ZIP</button>
  <button type="submit" class="primary-btn">${dialog.project?'重新打开副本':pending?'确认保存结果':'建立独立作品'}</button></div></form>`;
 document.body.append(wrap);
 wrap.querySelector('[data-close]').onclick=()=>{
  authorInvalidatePortablePreview();authorRenderPortablePending();
 };
 wrap.querySelector('[data-reselect]').onclick=async()=>{
  const reselect=wrap.querySelector('[data-reselect]');reselect.disabled=true;
  try{
   const token=dialog.request?.preview_id||preview?.preview_id;
   if(token)await authorCancelPortable({preview_id:token},receiptScope);
   if(dialog.request){
    const result=await authorJSON('/api/backups/import/status',{method:'POST',headers:authorPortableHeaders(receiptScope),
     body:JSON.stringify(dialog.request)});
    if(result.saved){
     dialog.project=authorRememberProject(result.result.project);authorSavePortablePending(dialog);
     if(current())authorPortablePreview(filename,null,{scope:receiptScope,request:dialog.request,project:dialog.project});
     return;
    }
   }
   if(!current())return;
   if(authorPortablePending()?.request.preview_id===token)storage.remove(authorPortablePendingKey);
   authorInvalidatePortablePreview();authorRenderPortablePending();document.querySelector('#backupFileInput').click();
  }catch(error){if(current())wrap.querySelector('.form-error').textContent=error.message}
  finally{if(current())reselect.disabled=false}
 };
 const form=wrap.querySelector('form'),button=form.querySelector('[type="submit"]');
 form.onsubmit=async event=>{
  event.preventDefault();if(button.disabled||!current())return;
  const errorBox=wrap.querySelector('.form-error');errorBox.textContent='';
  button.disabled=true;
  try{
   if(!dialog.request){
    const title=wrap.querySelector('#portableRestoreTitle').value.trim();
    if(!title||Array.from(title).length>200)throw new Error('作品名需要 1 到 200 字');
    if(!wrap.querySelector('#portableRestoreConfirm').checked)throw new Error('请先确认建立独立作品');
    dialog.request={preview_id:preview.preview_id,archive_sha256:preview.archive_sha256,title,confirm:true};
    try{authorSavePortablePending(dialog)}catch(error){dialog.request=null;throw error}
    try{authorForgetPortableUnconfirmed(preview.preview_id)}catch{}
    wrap.querySelector('#portableRestoreTitle').readOnly=true;
    wrap.querySelector('#portableRestoreConfirm').disabled=true;
    button.textContent='正在建立副本…';
    const committed=result=>{
     if(!result.project?.id)throw new Error('恢复结果缺少保存身份，请重新确认结果');
     dialog.project=authorRememberProject(result.project);authorSavePortablePending(dialog);
    };
    try{await durableOperation('/api/backups/import/apply','POST',dialog.request,current,committed)}
    catch(error){if(!dialog.project)throw error}
   }else if(!dialog.project){
    button.textContent='正在确认保存结果…';
    try{
     await durableOperation('/api/backups/import/apply','POST',dialog.request,current,result=>{
      if(!result.project?.id)throw new Error('恢复结果缺少保存身份，请在作品书架查找副本');
      dialog.project=authorRememberProject(result.project);authorSavePortablePending(dialog);
     });
    }catch(error){if(!dialog.project)throw error}
   }
   if(!current())return;
   authorRememberProject(dialog.project);
   if(!backendOnline&&requestedProject===dialog.project.id&&dialog.project.branch_id&&
    state.branch_id!==dialog.project.branch_id)state.branch_id=dialog.project.branch_id;
   button.textContent='正在打开副本…';
   if(!await authorSwitchProject(dialog.project.id)){
    authorPortablePreview(filename,null,{scope:receiptScope,request:dialog.request,project:dialog.project});
    const retry=authorPortableDialog;
    if(retry)retry.wrap.querySelector('.form-error').textContent='副本已经建立，暂时无法打开；请重新打开副本';
    return;
   }
   storage.remove(authorPortablePendingKey);wrap.remove();toast('完整备份已恢复为独立作品');
  }catch(error){
   if(current()){
    errorBox.textContent=error.message;
    button.textContent=dialog.project?'重新打开副本':dialog.request?'确认保存结果':'建立独立作品';
   }
  }finally{if(current())button.disabled=false}
 };
}
actions.exportManuscript=()=>{downloadText(exportFilename('json'),JSON.stringify(state,null,2),'application/json;charset=utf-8');toast('稿件 JSON 已导出，不包含完整历史与任务')};
actions.importManuscript=()=>document.querySelector('#fileInput').click();

actions.importBook=()=>document.querySelector('#bookFileInput').click();
function bytesToBase64(bytes){
 let output='';for(let start=0;start<bytes.length;start+=32768)output+=String.fromCharCode(...bytes.subarray(start,start+32768));
 return btoa(output);
}
document.querySelector('#bookFileInput').onchange=async event=>{
 const file=event.target.files?.[0];event.target.value='';if(!file)return;
 try{
  if(file.size>32*1024*1024)throw new Error('原文件超过 32 MB，请分卷导入');
  const data_base64=bytesToBase64(new Uint8Array(await file.arrayBuffer()));
  const preview=await authorJSON('/api/import/preview',{method:'POST',body:JSON.stringify({filename:file.name,data_base64})});
  authorImportPreview(file.name,preview);
 }catch(error){toast(`旧书预览未完成：${error.message}`)}
};
function authorImportPreview(filename,preview){
 const chapters=MoyuSync.copy(preview.chapters||[]);if(!chapters.length)return toast('未找到可导入正文');
 const owner=requestedProject;let committedResult=null;
 const wrap=document.createElement('div');wrap.className='modal-backdrop';
 wrap.innerHTML=`<div class="modal candidate-modal"><h3>分章预览</h3><p class="modal-help">${esc(filename)} · ${chapters.length} 章 · ${esc(preview.metadata?.encoding||'')}</p>
 <label>名称<input id="importBookTitle" value="${esc(preview.metadata?.suggested_title||filename.replace(/\.[^.]+$/,''))}"></label>
 <label>放到哪里<select id="importBookMode"><option value="manuscript">建立独立作品</option><option value="reference">当前作品的参考来源</option></select></label>
 ${(preview.warnings||[]).map(item=>`<p class="modal-help check-bad">${esc(typeof item==='string'?item:item.message||JSON.stringify(item))}</p>`).join('')}
 <div class="import-chapters"></div>
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" data-close>取消</button><button class="primary-btn" data-import-confirm>确认导入所见章节</button></div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 const captureTitles=()=>{for(const input of wrap.querySelectorAll('[data-import-title]'))chapters[Number(input.dataset.importTitle)].title=input.value};
 const showChapters=()=>{
  wrap.querySelector('.import-chapters').innerHTML=chapters.map((c,index)=>`<details class="analysis-item" ${index===0?'open':''}><summary>${index+1}. ${esc(c.title)} · ${Array.from(c.body||'').length.toLocaleString()} 字</summary>
  <label>标题<input data-import-title="${index}" value="${esc(c.title)}"></label><label>正文<textarea data-import-body="${index}" readonly>${esc(c.body)}</textarea></label>
  <div class="analysis-actions"><button class="ghost-btn" data-import-split="${index}">在光标处拆章</button>${index<chapters.length-1?`<button class="ghost-btn" data-import-merge="${index}">与下一章合并</button>`:''}</div></details>`).join('');
  wrap.querySelectorAll('[data-import-split]').forEach(button=>button.onclick=()=>{
   captureTitles();const index=Number(button.dataset.importSplit);const input=wrap.querySelector(`[data-import-body="${index}"]`);const position=input.selectionStart;
   if(position<=0||position>=chapters[index].body.length){wrap.querySelector('.form-error').textContent='请先在正文中点击需要拆章的位置';return}
   const c=chapters[index];chapters.splice(index,1,{...c,body:c.body.slice(0,position)},{...c,title:c.title+'（续）',body:c.body.slice(position)});
   wrap.querySelector('.form-error').textContent='';showChapters();
  });
  wrap.querySelectorAll('[data-import-merge]').forEach(button=>button.onclick=()=>{
   captureTitles();const index=Number(button.dataset.importMerge);chapters[index].body+=chapters[index+1].body;chapters.splice(index+1,1);
   wrap.querySelector('.form-error').textContent='';showChapters();
  });
 };
 showChapters();
 wrap.querySelector('[data-import-confirm]').onclick=async()=>{
  const button=wrap.querySelector('[data-import-confirm]');button.disabled=true;
  try{
   if(!committedResult){
    captureTitles();
    const mode=wrap.querySelector('#importBookMode').value;const title=wrap.querySelector('#importBookTitle').value.trim();
    if(!title)throw new Error('请填写名称');
    if(Array.from(title).length>200)throw new Error('作品或参考书名称不能超过200字');
    for(const [index,item] of chapters.entries()){
     if(!item.title.trim())throw new Error(`请填写第${index+1}章名称`);
     if(Array.from(item.title).length>500)throw new Error(`第${index+1}章名称不能超过500字`);
    }
    try{
     await durableOperation('/api/import/apply','POST',{preview_id:preview.preview_id,source_sha256:preview.source_sha256,title,mode,chapters},null,result=>{
      committedResult={...result,mode};
      if(mode==='manuscript')authorRememberProject(result.project);
      authorLockCommittedForm(wrap);
     });
    }catch(error){if(!committedResult)throw error}
   }
   if(committedResult.mode==='manuscript'){
    await authorOpenCommittedProject(committedResult.project,wrap,'导入作品');view='editor';render();
   }else{
    button.textContent='重新打开参考库';
    if(!await authorSwitchProject(owner))throw new Error('参考书已经保存，但暂时无法打开。请重新打开参考库，原作品继续保留。');
    const data=await authorJSON('/api/references');
    authorReferences=data.references||[];view='references';render();
   }
   wrap.remove();
   toast('所见章节已经完整导入');
  }catch(error){wrap.querySelector('.form-error').textContent=error.message;button.disabled=false}
 };
}
function authorRenderReferences(){
 const host=document.querySelector('#referenceList');if(!host)return;
 host.innerHTML=authorReferences.map(r=>`<article class="book-row"><div><h3>${esc(r.title)}</h3><p>${esc(r.filename||'')} · ${Number(r.metadata?.chapter_count||0)} 章 · ${Number(r.metadata?.characters||0).toLocaleString()} 字</p></div>
 <button class="ghost-btn" data-reference-open="${safeId(r.id)}">查看来源</button></article>`).join('')||'<div class="empty">还没有参考来源。</div>';
 host.querySelectorAll('[data-reference-open]').forEach(button=>button.onclick=()=>authorReferenceDialog(button.dataset.referenceOpen));
}
function authorChooseReference(){
 if(!chapter())return toast('请先选择写作章节');
 if(!backendOnline)return toast('请先恢复连接，再选择参考片段');
 return authorReferenceDialog();
}
async function authorReferenceDialog(documentId=null){
 const captured=syncReferenceScope(),token=++authorReferenceDialogSeq;
 const headers={'X-Moyu-Project':captured.scope.project_id,'X-Moyu-Branch':captured.scope.branch_id};
 document.querySelector('[data-reference-dialog]')?.remove();
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.referenceDialog='1';
 wrap.innerHTML=`<div class="modal candidate-modal reference-picker"><h3>参考来源</h3>
  <label>参考书<select id="referenceDocument" aria-label="参考书" disabled><option>正在读取…</option></select></label>
  <div class="search-box"><input id="referenceQuery" aria-label="搜索参考原句" placeholder="搜索此来源中的原句">
  <button type="button" class="primary-btn" data-reference-search disabled>搜索</button></div>
  <div id="referenceResults"><div class="empty">正在读取参考来源…</div></div>
  <p class="form-error" data-reference-error role="alert"></p><div class="modal-actions">
  ${captured.scope.chapter_id?'<button type="button" class="ghost-btn" data-reference-editor>返回写作</button>':''}
  <button type="button" class="ghost-btn" data-close>关闭</button></div></div>`;
 document.body.append(wrap);
 const current=()=>wrap.isConnected&&token===authorReferenceDialogSeq&&referenceScopeCurrent(captured);
 wrap.querySelector('[data-close]').onclick=()=>{++authorReferenceDialogSeq;wrap.remove()};
 wrap.querySelector('[data-reference-editor]')?.addEventListener('click',()=>{
  if(!current())return;++authorReferenceDialogSeq;wrap.remove();
  if(view==='editor'&&document.querySelector('#chapterBody')){renderReferenceTools();return}
  view='editor';render();
  const body=document.querySelector('#chapterBody');
  if(body&&savedSelection?.chapter_id===captured.scope.chapter_id&&savedSelection.body===body.value){
   body.setSelectionRange(savedSelection.start,savedSelection.end);captureSelection();
  }
 });
 const select=wrap.querySelector('#referenceDocument'),results=wrap.querySelector('#referenceResults');
 const searchButton=wrap.querySelector('[data-reference-search]'),errorBox=wrap.querySelector('[data-reference-error]');
 let searchSeq=0;
 const displayHits=(hits,warnings=[])=>{
  results.innerHTML=warnings.map(message=>`<p class="modal-help">${esc(message)}</p>`).join('')+
   (hits.length?hits.map((hit,index)=>{
    const reason=referenceSelectionReason(hit,captured.scope),selected=referenceChoice?.selection;
    const chosen=selected?.document_id===hit.source_document_id&&selected?.section_id===hit.source_section_id&&
     selected?.section_start===hit.section_start&&selected?.section_end===hit.section_end&&selected?.source_hash===hit.source_hash;
    return `<article class="result reference-search-result"><strong>${esc(hit.title||'')}</strong>
     <p class="reference-source-quote">${esc(hit.source_quote||'')}</p><div class="reference-hit-footer">
     <small>${esc(hit.document_title||hit.filename||'参考来源')} · ${Array.from(hit.source_quote||'').length} 字</small>
     <button type="button" class="ghost-btn" data-reference-use="${index}" ${reason?'disabled':''} title="${esc(reason||'选择这段完整原句')}">
      ${chosen?'撤回本次参考':'用于本次写作'}</button></div>${reason?`<small class="reference-choice-error">${esc(reason)}</small>`:''}</article>`;
   }).join(''):'<div class="empty">此来源没有匹配原句。</div>');
  results.querySelectorAll('[data-reference-use]').forEach(button=>button.onclick=()=>{
   if(!current())return;
   try{
    const hit=hits[Number(button.dataset.referenceUse)],selected=referenceChoice?.selection;
    if(selected?.document_id===hit.source_document_id&&selected?.section_id===hit.source_section_id&&
     selected?.section_start===hit.section_start&&selected?.section_end===hit.section_end&&selected?.source_hash===hit.source_hash)
     clearReferencePassage();
    else selectReferencePassage(hit,captured);
    errorBox.textContent='';displayHits(hits,warnings);
   }catch(error){errorBox.textContent=error.message}
  });
 };
 const showDocument=async()=>{
  const id=select.value,sequence=++searchSeq;errorBox.textContent='';
  results.innerHTML='<div class="empty">正在读取参考原文…</div>';
  try{
   const data=await authorJSON(`/api/references/${encodeURIComponent(id)}`,{headers});
   if(!current()||sequence!==searchSeq||select.value!==id)return;
   const chapters=data.chapters||data.reference?.chapters||[];
   results.innerHTML=chapters.map(c=>`<details class="analysis-item"><summary>${esc(c.title)} · ${Array.from(c.body||'').length} 字</summary>
    <pre class="prompt-preview">${esc(c.body||'')}</pre></details>`).join('')||'<div class="empty">此来源暂无正文。</div>';
  }catch(error){if(current()&&sequence===searchSeq)errorBox.textContent=error.message}
 };
 const search=async()=>{
  const query=wrap.querySelector('#referenceQuery').value.trim(),id=select.value,sequence=++searchSeq;
  if(!query||!id||!current())return;
  errorBox.textContent='';results.innerHTML='<div class="empty">正在搜索原句…</div>';
  try{
   const data=await authorJSON(`/api/references/${encodeURIComponent(id)}/search?q=${encodeURIComponent(query)}`,{headers});
   if(!current()||sequence!==searchSeq||select.value!==id||wrap.querySelector('#referenceQuery').value.trim()!==query)return;
   if(!Array.isArray(data.results))throw new Error('参考搜索返回格式异常，请重试');
   displayHits(data.results,Array.isArray(data.warnings)?data.warnings:[]);
  }catch(error){if(current()&&sequence===searchSeq)errorBox.textContent=error.message}
 };
 searchButton.onclick=search;
 wrap.querySelector('#referenceQuery').addEventListener('keydown',event=>{
  if(event.key==='Enter'){event.preventDefault();search()}
 });
 select.onchange=showDocument;
 try{
  const data=await authorJSON('/api/references',{headers});if(!current())return;
  const references=Array.isArray(data.references)?data.references:[];
  select.innerHTML=references.map(item=>`<option value="${safeId(item.id)}">${esc(item.title||item.filename||'参考来源')}</option>`).join('')||
   '<option value="">暂无参考来源</option>';
  select.disabled=!references.length;searchButton.disabled=!references.length;
  if(!references.length){results.innerHTML='<div class="empty">还没有参考来源。</div>';return}
  if(documentId&&references.some(item=>item.id===documentId))select.value=documentId;
  await showDocument();
 }catch(error){if(current()){results.innerHTML='<div class="empty">参考来源暂时无法读取。</div>';errorBox.textContent=error.message}}
}

views.plot=()=>`${authorPageHead('情节与伏笔','<button class="primary-btn" data-action="newPlot">＋ 伏笔</button>')}
 <div class="plot-board">${[['setup','埋下线索'],['develop','推进中'],['payoff','已回收']].map(([stage,label])=>
 `<section class="plot-col"><h4>${label} · ${state.plot[stage].length}</h4>${state.plot[stage].map((raw,index)=>{
  const x=typeof raw==='string'?{text:raw,id:'',stage}:raw;
  return `<article class="plot-item"><strong>${esc(x.text)}</strong>${x.stale?'<p class="check-bad">来源已变，需要复核</p>':''}
  <div class="plot-actions"><button class="ghost-btn" data-plot-edit="${safeId(x.id)}" data-plot-stage="${stage}" data-plot-index="${index}">编辑</button>
  <button class="ghost-btn" data-plot-move="${safeId(x.id)}" data-plot-stage="${stage}" data-plot-index="${index}">状态与原句</button></div></article>`;
 }).join('')||'<div class="empty">暂无伏笔</div>'}</section>`).join('')}</div>`;
actions.newPlot=()=>authorPlotForm();
views.memory=()=>`${authorPageHead('记忆中枢','<button class="primary-btn" data-action="chapterAnalysis">提取当前章记忆</button>')}
 <div class="record-grid memory-grid">${state.memory.map(m=>`<article class="record-card memory-card">
 <span class="record-tag">${esc(m.type)} · ${esc(memoryStatusLabel(m.status,m.stale))}</span><h4>${esc(m.name)}</h4>
 <p>${esc(m.note||'')}</p><blockquote class="memory-source-quote">${esc(m.source_quote||'无可定位原句')}</blockquote>
 <small>${esc(m.source||chapter(m.source_chapter_id)?.title||'来源章节')} · ${esc(memorySpanText(m))}</small>
 <div class="memory-actions"><button class="ghost-btn" data-memory-evidence="${safeId(m.id)}">证据详情</button>
 <button class="ghost-btn" data-memory-source="${safeId(m.id)}">定位原句</button><button class="ghost-btn" data-memory-reanchor="${safeId(m.id)}">重新定位</button>
 <button class="ghost-btn" data-memory="${safeId(m.id)}" ${['已确认','accepted'].includes(m.status)&&!m.stale?'disabled':''}>确认</button>
 <button class="ghost-btn" data-memory-reject="${safeId(m.id)}">拒绝</button><button class="ghost-btn" data-memory-review="${safeId(m.id)}">重新审查</button></div>
 </article>`).join('')||'<div class="empty">还没有待确认记忆。</div>'}</div>`;
function authorPlotForm(item,stage='setup',index){
 authorForm(item?'编辑伏笔':'新增伏笔',`<label>伏笔内容<textarea id="plotText" maxlength="10000" required>${esc(item?.text||'')}</textarea></label>`,async w=>{
  const text=w.querySelector('#plotText').value.trim();if(!text)throw new Error('请填写伏笔内容');
  if(item?.id)await durableOperation(`/api/plot/${encodeURIComponent(item.id)}`,'PATCH',{text,stage});
  else if(item){state.plot[stage][index]={...item,id:crypto.randomUUID(),stage,text};save();if(!await syncBackend())throw new Error('伏笔未保存')}
  else{state.plot.setup.push({id:crypto.randomUUID(),stage:'setup',text});save();if(!await syncBackend())throw new Error('伏笔未保存')}
  render();toast('伏笔已经保存');
 });
}
async function authorPlotLifecycle(item){
 if(!item?.id){await syncBackend();render();return toast('伏笔身份已经保存，请再打开状态')}
 const owner=requestedProject,branch=state.branch_id||'main';
 let links=[];
 try{const result=await authorJSON('/api/story/foreshadow-links');links=(result.links||result.foreshadowing||result||[]).filter?.(row=>row.plot_id===item.id)||[]}
 catch(error){return toast(`伏笔来源读取失败：${error.message}`)}
 if(owner!==requestedProject||branch!==(state.branch_id||'main'))return;
 const kindLabels={plant:'埋下',develop:'推进',payoff:'回收'};
 authorForm('伏笔状态与原句',`<p class="modal-help">${esc(item.text)}</p>
 ${links.map(link=>`<p class="plot-evidence">${kindLabels[link.kind]} · ${esc(link.chapter_title||'')} · ${link.stale?'原句需复核':'已保存'} <button type="button" class="ghost-btn" data-plot-source="${safeId(link.id)}">原句</button></p>`).join('')}
 <label>来源记录<select id="plotEvidenceLink"><option value="">新增来源记录</option>
 ${links.map(link=>`<option value="${safeId(link.id)}">${kindLabels[link.kind]} · ${esc(link.chapter_title||'未绑定章节')} · ${link.stale?'原句需复核':'已保存'}</option>`).join('')}</select></label>
 <label>阶段<select id="plotStage">${[['setup','埋下'],['develop','推进'],['payoff','回收']].map(([value,label])=>`<option value="${value}" ${item.stage===value?'selected':''}>${label}</option>`).join('')}</select></label>
 <label>发生章节<select id="plotSourceChapter">${storyChapterOptions(activeChapter)}</select></label>
 <label>正文原句<textarea id="plotSourceQuote" required>${esc(savedSelection?.chapter_id===activeChapter?savedSelection.body.slice(savedSelection.start,savedSelection.end):'')}</textarea></label>
 <label>说明<textarea id="plotSourceNote"></textarea></label>`,async w=>{
  if(owner!==requestedProject||branch!==(state.branch_id||'main'))throw new Error('作品已经切换，请重新打开伏笔');
  const stage=w.querySelector('#plotStage').value;const id=w.querySelector('#plotSourceChapter').value;
  const quote=w.querySelector('#plotSourceQuote').value;
  if(!id||!quote.trim())throw new Error('请选发生章节，并填写正文中的原句');
  const data=await foreshadowSourcePayload({plot_id:item.id,chapter_id:id,kind:{setup:'plant',develop:'develop',payoff:'payoff'}[stage],
   note:w.querySelector('#plotSourceNote').value},quote);
  const linkId=w.querySelector('#plotEvidenceLink').value;
  await durableOperation(linkId?`/api/story/foreshadow-links/${encodeURIComponent(linkId)}`:'/api/story/foreshadow-links',
   linkId?'PATCH':'POST',data);
  await durableOperation(`/api/plot/${encodeURIComponent(item.id)}`,'PATCH',{stage,text:item.text});storyLoaded=false;render();toast('伏笔阶段和来源已经保存');
 },'确认阶段与原句');
 const wrap=document.querySelector('.modal-backdrop:last-of-type');
 wrap?.querySelectorAll('[data-plot-source]').forEach(button=>button.onclick=()=>authorJumpToEvidence(links.find(link=>link.id===button.dataset.plotSource),wrap));
 const selection=wrap.querySelector('#plotEvidenceLink');
 const chooseExisting=()=>{
  const kind={setup:'plant',develop:'develop',payoff:'payoff'}[wrap.querySelector('#plotStage').value];
  const matches=links.filter(link=>link.kind===kind&&link.chapter_id===wrap.querySelector('#plotSourceChapter').value);
  const stale=matches.filter(link=>link.stale);
  const match=matches.length===1?matches[0]:stale.length===1?stale[0]:null;
  selection.value=match?.id||'';
  if(match){wrap.querySelector('#plotSourceNote').value=match.note||'';
   if(!wrap.querySelector('#plotSourceQuote').value)wrap.querySelector('#plotSourceQuote').value=match.source_quote||''}
 };
 wrap.querySelector('#plotStage').onchange=chooseExisting;wrap.querySelector('#plotSourceChapter').onchange=chooseExisting;
 selection.onchange=()=>{
  const link=links.find(row=>row.id===selection.value);if(!link)return;
  wrap.querySelector('#plotStage').value={plant:'setup',develop:'develop',payoff:'payoff'}[link.kind];
  wrap.querySelector('#plotSourceChapter').value=link.chapter_id||'';
  wrap.querySelector('#plotSourceQuote').value=link.source_quote||'';wrap.querySelector('#plotSourceNote').value=link.note||'';
 };
 chooseExisting();
}

function authorJumpToEvidence(item,wrap){
 const chapterId=item?.source_chapter_id||item?.chapter_id||item?.source?.chapter_id||item?.source_span?.document_node_id;
 const quote=String(item?.source_quote||item?.quote||item?.source?.quote||item?.source_span?.quote||'');
 const c=chapter(chapterId);if(!c)return toast('来源章节已不存在');
 let start=item?.source_start??item?.start??item?.source?.start??item?.source_span?.start_codepoint;
 let end=item?.source_end??item?.end??item?.source?.end??item?.source_span?.end_codepoint;
 if(start!==undefined&&end!==undefined){
  const current=Array.from(c.body).slice(start,end).join('');
  if(quote&&current!==quote)return toast('原句位置已经变化，请先重新定位证据');
 }else{
  const position=quote?c.body.indexOf(quote):-1;
  if(position<0)return toast('原句已变化，不能定位当前稿');
  if(c.body.indexOf(quote,position+1)>=0)return toast('原句重复，需要重新确认具体范围');
  start=MoyuSync.codepointOffset(c.body,position);end=start+Array.from(quote).length;
 }
 wrap?.remove();activeChapter=chapterId;storage.set(cacheKey(':active'),chapterId);view='editor';render();
 const input=document.querySelector('#chapterBody');input.focus();
 input.setSelectionRange(MoyuSync.utf16Offset(c.body,start),MoyuSync.utf16Offset(c.body,end));
 input.scrollTop=Math.max(0,Math.floor(start/40)*26-100);captureSelection();input.scrollIntoView({block:'center'});authorRememberEditorPosition(input);
}

function authorChapterReportScope(chapterId=activeChapter){
 const owner=requestedProject,branch=state.branch_id||'main',page=view,editor=document.querySelector('#chapterBody');
 return {owner,branch,chapterId,headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch},
  current:()=>owner===requestedProject&&branch===(state.branch_id||'main')&&chapterId===activeChapter&&page===view&&
   document.querySelector('#chapterBody')===editor};
}

async function authorQueueTask(task,scope=authorChapterReportScope()){
 let c=chapter(scope.chapterId);if(!c||!scope.current())throw new Error('请先选择章节');const id=c.id;
 if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
 c=chapter(id);if(!c||!scope.current())throw new Error('作品或章节已切换，请重新发起任务');
 const source=candidateSource(task,c);source.source_revision_id=c.revision_id;source.source_body_hash=await sha256(c.body);
 if(!scope.current())throw new Error('作品或章节已切换，请重新发起任务');
 const data=await durableOperation('/api/ai/queue','POST',{task,chapter_id:c.id,source,response_format:'json',stream:true,material_controls:materialControls(),retrieval_profile:retrievalProfile()},scope.current);
 const job=data.job||data;
 if(scope.current()){jobSources.set(job.id,source);scheduleQueue(0);toast('任务已保存，可以继续写作')}
 return job;
}

async function authorOpenChapterAnalysis(chapterId=activeChapter,analysisId=''){
 const c=chapter(chapterId);if(!c)return;const scope=authorChapterReportScope(c.id);
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  if(!scope.current())return;
  const data=await authorJSON(`/api/chapter-analysis?chapter_id=${encodeURIComponent(c.id)}`,{headers:scope.headers});
  if(!scope.current())return;
  const analyses=data.analyses||data.analysis?[...(data.analyses||[]),...(data.analysis?[data.analysis]:[])]:Array.isArray(data)?data:[];
  const analysis=analysisId?analyses.find(item=>item.id===analysisId):analyses[0];
  if(analysisId&&!analysis)throw new Error('本次章后资料已不存在，请刷新任务后重试');
  authorShowAnalysis(analysis,c.id,scope);
 }catch(error){if(scope.current())toast(`章后记忆读取失败：${error.message}`)}
}
actions.chapterAnalysis=()=>authorOpenChapterAnalysis();
function authorShowAnalysis(analysis,chapterId,scope=authorChapterReportScope(chapterId)){
 if(!scope.current())return;
 const payload=analysis?.structured||analysis?.payload||analysis||{};
 const candidates=analysis?.candidates||payload.candidates||payload.items||[];
 const summarySection=analysis?.summary||payload.summary||'';const handoffSection=analysis?.handoff||payload.handoff||payload.next_brief||'';
 const summary=typeof summarySection==='string'?summarySection:summarySection.text||'';
 const handoff=typeof handoffSection==='string'?handoffSection:handoffSection.text||'';
 const sectionDecided=section=>['accepted','rejected'].includes(section?.status);
 const decisionOptions=section=>`<option value="">待决定</option><option value="accepted" ${section.status==='accepted'?'selected':''}>采用</option><option value="rejected" ${section.status==='rejected'?'selected':''}>不采用</option>`;
 const stale=analysis?.stale||analysis?.status==='stale';
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.authorChapterReport='analysis';wrap.dataset.analysisId=analysis?.id||analysis?.analysis_id||'';
 const current=()=>scope.current()&&wrap.isConnected;
 wrap.innerHTML=`<div class="modal analysis-modal"><h3>章后记忆确认</h3><p class="modal-help">${esc(chapter(chapterId)?.title||'')}
 ${stale?' · 原稿或资料需要重新核对，此批候选仅供阅读':''}</p>
 ${analysis?`<label>章节回顾<textarea id="analysisSummary" ${sectionDecided(summarySection)?'readonly':''}>${esc(summary)}</textarea></label>
 <label>回顾决定<select id="analysisSummaryDecision" ${sectionDecided(summarySection)?'disabled':''}>${decisionOptions(summarySection)}</select></label>
 <label>下一章任务<textarea id="analysisHandoff" ${sectionDecided(handoffSection)?'readonly':''}>${esc(handoff)}</textarea></label>
 <label>下一章任务决定<select id="analysisHandoffDecision" ${sectionDecided(handoffSection)?'disabled':''}>${decisionOptions(handoffSection)}</select></label>`:
 '<p class="modal-help">还没有本章的待确认事实。</p>'}
 <div>${candidates.map((item,index)=>`<section class="analysis-item" data-analysis-index="${index}">
 <strong>${esc(({fact:'事实',event:'事件',character_state:'人物状态',relationship:'人物关系',foreshadowing:'伏笔'})[item.type||item.kind]||item.type||item.kind||'事实')} · ${esc(item.name||item.title||item.attribute||'')}</strong>
 <label>名称<input data-analysis-name value="${esc(item.name||'')}" maxlength="500" ${sectionDecided(item)?'readonly':''}></label>
 <label>内容<textarea data-analysis-edit ${sectionDecided(item)?'readonly':''}>${esc(item.note||item.description||item.summary||item.value||'')}</textarea></label>
 <div class="plan-grid"><label>资料性质<select data-analysis-mode ${sectionDecided(item)?'disabled':''}>${[['world_truth','已发生事实'],['character_belief','人物认知'],['narrator','叙述者已知'],['inference','推测'],['author_plan','作者计划']].map(([value,label])=>`<option value="${value}" ${item.epistemic_mode===value?'selected':''}>${label}</option>`).join('')}</select></label>
 <label>哪些人物知道<select data-analysis-known multiple size="3" ${sectionDecided(item)?'disabled':''}>${storyEntityMultiOptions(item.known_by||[])}</select></label></div>
 <blockquote>${esc(item.source_quote||item.quote||item.source?.quote||'无原句证据')}</blockquote>
 <div class="analysis-actions"><button class="ghost-btn" data-analysis-source="${index}">定位原句</button>
 <select data-analysis-decision aria-label="记忆决定" ${sectionDecided(item)?'disabled':''}><option value="">待决定</option><option value="accepted" ${item.status==='accepted'?'selected':''}>采用</option><option value="rejected" ${item.status==='rejected'?'selected':''}>不采用</option></select>
 <span>${esc(({candidate:'待决定',accepted:'已采用',rejected:'未采用'})[item.status]||item.status||'待决定')}</span></div></section>`).join('')||''}</div>
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" data-close>关闭</button><button class="ghost-btn" data-extract>重新提取</button>
 ${analysis?`<button class="primary-btn" data-confirm-analysis ${stale?'disabled':''}>保存所选决定与回顾</button>`:''}</div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-extract]').onclick=async()=>{
  try{if(!current())return;await authorQueueTask('chapter_analysis',scope);if(!current())return;wrap.remove();view='tasks';render()}
  catch(error){if(current())wrap.querySelector('.form-error').textContent=error.message}
 };
 wrap.querySelectorAll('[data-analysis-source]').forEach(button=>button.onclick=()=>{if(current())authorJumpToEvidence({source_chapter_id:chapterId,...candidates[Number(button.dataset.analysisSource)]},wrap)});
 wrap.querySelector('[data-confirm-analysis]')?.addEventListener('click',async()=>{
  if(!current())return;
  const button=wrap.querySelector('[data-confirm-analysis]');button.disabled=true;
  try{
   const decisions=[];
   for(const row of wrap.querySelectorAll('[data-analysis-index]')){
    const item=candidates[Number(row.dataset.analysisIndex)];const decision=row.querySelector('[data-analysis-decision]').value;
    if(decision&&!['accepted','rejected'].includes(item.status))decisions.push({candidate_id:item.id||item.candidate_id,decision,
     edited:{name:row.querySelector('[data-analysis-name]').value,note:row.querySelector('[data-analysis-edit]').value,
      epistemic_mode:row.querySelector('[data-analysis-mode]').value,known_by:[...row.querySelector('[data-analysis-known]').selectedOptions].map(option=>option.value)}});
   }
   const summaryDecision=wrap.querySelector('#analysisSummaryDecision')?.value||'';
   const handoffDecision=wrap.querySelector('#analysisHandoffDecision')?.value||'';
   const result=await durableOperation(`/api/chapter-analysis/${encodeURIComponent(analysis.id||analysis.analysis_id)}/confirm`,'POST',{
    source_revision_id:analysis.source_revision_id,chapter_hash:analysis.chapter_hash,decisions,
    ...(!sectionDecided(summarySection)&&summaryDecision?{summary_decision:summaryDecision,summary_text:wrap.querySelector('#analysisSummary')?.value||''}:{}),
    ...(!sectionDecided(handoffSection)&&handoffDecision?{handoff_decision:handoffDecision,handoff_text:wrap.querySelector('#analysisHandoff')?.value||''}:{})},current);
   if(!current())return;
   const saved=result.analysis||{},remaining=(saved.candidates||[]).filter(item=>!['accepted','rejected'].includes(item.status)).length+
    ['summary','handoff'].filter(name=>saved[name]?.text?.trim()&&!sectionDecided(saved[name])).length;
   wrap.remove();render();toast(remaining?`决定已保存，还有 ${remaining} 项待决定`:'章后资料已处理');
  }catch(error){if(current()){wrap.querySelector('.form-error').textContent=error.message;button.disabled=false}}
 });
}

actions.reviewChapter=async()=>{
 const c=chapter();if(!c)return;const scope=authorChapterReportScope(c.id);
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  if(!scope.current())return;
  const data=await authorJSON(`/api/story-reviews?chapter_id=${encodeURIComponent(c.id)}`,{headers:scope.headers});
  if(!scope.current())return;
  const reviews=data.reviews||data.story_reviews||data.story_review?[...(data.reviews||data.story_reviews||[]),...(data.story_review?[data.story_review]:[])]:Array.isArray(data)?data:[];
  authorShowStoryReview(reviews[0],c.id,scope);
 }catch(error){if(scope.current())toast(`审稿读取失败：${error.message}`)}
};
function authorReviewRows(data){
 return Array.isArray(data)?data:[...(data.reviews||data.story_reviews||[]),...(data.story_review?[data.story_review]:[])];
}
async function authorReviewSourceCurrent(source,owner,branch){
 if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已经切换，请重新打开本章审稿');
 const c=chapter(source?.chapter_id);const range=source?.range;const body=c?.body;
 if(!c||source.project_id!==owner||source.branch_id!==branch||source.mode!=='replace'||
  !source.source_revision_id||source.source_revision_id!==c.revision_id||typeof source.source_body_hash!=='string'||
  !range||!Number.isInteger(range.start)||!Number.isInteger(range.end)||range.start<0||range.start>=range.end||
  range.end>Array.from(body).length||typeof range.quote!=='string'||Array.from(body).slice(range.start,range.end).join('')!==range.quote){
  throw new Error('原句范围或稿件版本已经变化，请重新审稿');
 }
 if(await sha256(body)!==source.source_body_hash||chapter(c.id)?.body!==body||chapter(c.id)?.revision_id!==source.source_revision_id||
  owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main')){
  throw new Error('原句依据已经变化，请重新审稿');
 }
 return source;
}
async function authorReviewCurrent(review,chapterId,owner,branch){
 if(!review||review.stale||review.status==='stale')throw new Error('这份审稿已过期，请先重新故事审稿');
 if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已经切换，请重新打开本章审稿');
 if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
 if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已经切换，请重新打开本章审稿');
 const data=await authorJSON(`/api/story-reviews?chapter_id=${encodeURIComponent(chapterId)}`,
  {headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch}});
 const current=authorReviewRows(data).find(row=>(row.id||row.review_id)===(review.id||review.review_id));
 const c=chapter(chapterId);
 if(!current||current.stale||current.status==='stale'||current.project_id!==owner||current.branch_id!==branch||
  current.chapter_id!==chapterId||!c||current.source_revision_id!==c.revision_id){
  throw new Error('审稿依据已经变化，请重新故事审稿');
 }
 const body=c.body;
 if(await sha256(body)!==current.chapter_hash||chapter(chapterId)?.body!==body||
  owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main')){
  throw new Error('审稿依据已经变化，请重新故事审稿');
 }
 return current;
}
async function authorReviewDecisionCurrent(review,chapterId,owner,branch){
 const sameBook=()=>owner===requestedProject&&branch===(state.branch_id||state.project?.branch_id||'main');
 if(!sameBook())throw new Error('作品已经切换，请重新打开本章审稿');
 if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
 if(!sameBook())throw new Error('作品已经切换，请重新打开本章审稿');
 const data=await authorJSON(`/api/story-reviews?chapter_id=${encodeURIComponent(chapterId)}`,
  {headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch}});
 const current=authorReviewRows(data).find(row=>(row.id||row.review_id)===(review.id||review.review_id));
 const c=chapter(chapterId),body=c?.body;
 if(!current||current.project_id!==owner||current.branch_id!==branch||current.chapter_id!==chapterId||!c){
  throw new Error('审稿问题已变化，请重新打开本章审稿');
 }
 const source=current.author_resolution_source;
 if(current.stale||current.status==='stale'){
  if(!source||source.project_id!==owner||source.branch_id!==branch||source.chapter_id!==chapterId||
   source.source_revision_id!==c.revision_id||await sha256(body)!==source.chapter_hash){
   throw new Error('当前稿件依据已经变化，请重新打开本章审稿');
  }
 }else if(current.source_revision_id!==c.revision_id||await sha256(body)!==current.chapter_hash){
  throw new Error('审稿依据已经变化，请重新打开本章审稿');
 }
 if(!sameBook()||chapter(chapterId)?.body!==body||chapter(chapterId)?.revision_id!==c.revision_id){
  throw new Error('当前稿件依据已经变化，请重新打开本章审稿');
 }
 return {review:current,guard:()=>sameBook()&&chapter(chapterId)?.body===body&&chapter(chapterId)?.revision_id===c.revision_id};
}
function authorReviewSelectSource(source,wrap){
 const c=chapter(source.chapter_id);wrap?.remove();activeChapter=c.id;storage.set(cacheKey(':active'),c.id);view='editor';render();
 const input=document.querySelector('#chapterBody');input.focus();
 input.setSelectionRange(MoyuSync.utf16Offset(c.body,source.range.start),MoyuSync.utf16Offset(c.body,source.range.end));
 input.scrollTop=Math.max(0,Math.floor(source.range.start/40)*26-100);captureSelection();input.scrollIntoView({block:'center'});authorRememberEditorPosition(input);
}
function authorReviewRepairInstruction(item){
 const excerpt=(value,limit)=>{const chars=Array.from(String(value||''));return chars.length>limit?chars.slice(0,limit).join('')+'（节选）':chars.join('')};
 return ['仅修订选中的原句，保持范围外的事件、人物动机、事实和视角。只返回可以替换该原句的候选正文。',
  '审稿问题：'+excerpt(item.description||item.message||'',750),
  item.suggestion?'修订建议：'+excerpt(item.suggestion,1000):'请针对这个问题给出克制、具体的局部修订，不添加无依据的新事实。'].join('\n');
}
function authorShowStoryReview(review,chapterId,scope=authorChapterReportScope(chapterId)){
 if(!scope.current())return;
 const payload=review?.structured||review?.payload||review||{};const issues=review?.findings||review?.issues||payload.findings||payload.issues||[];
 const owner=scope.owner,branch=scope.branch;
 const stale=!!(review?.stale||review?.status==='stale');let preparing=false;
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.authorChapterReport='review';
 const current=()=>scope.current()&&wrap.isConnected;
 wrap.innerHTML=`<div class="modal analysis-modal"><h3>本章审稿</h3><p class="modal-help">${esc(chapter(chapterId)?.title||'')} ·
 ${stale?'审稿依据需要重新核对，可结合当前稿逐项说明忽略或解释理由，也可重新审稿':review?'已生成故事审稿报告':'尚未完成故事审稿'}</p>
 ${issues.map((item,index)=>{
  const evidence=item.evidence||[];const targets=item.repair_targets||[];const disabled=stale||item.stale||!targets.length;
  const resolution=stale&&item.author_resolution_current?item.stale_resolution:null;
  const decision=stale?resolution?.decision||'':item.decision||'';
  const reason=stale?resolution?.reason||'':item.reason||'';
  const decided=stale?!!resolution:!!decision&&decision!=='accepted';
  return `<section class="analysis-item" data-review-issue="${index}"><strong>${esc(item.category||item.kind||'审稿项')} · ${esc(item.title||item.message||'')}</strong>
 <p class="modal-help">${esc(item.description||item.message||'')}</p>${item.suggestion?`<p class="modal-help">修订建议：${esc(item.suggestion)}</p>`:''}
 ${evidence.map((source,evidenceIndex)=>`<div data-review-evidence="${evidenceIndex}"><small>${esc(chapter(source.source_chapter_id)?.title||'来源章节')} · 原句 ${evidenceIndex+1}</small>
 <blockquote>${esc(source.source_quote||source.quote||'')}</blockquote><button class="ghost-btn" data-review-source="${index}" data-review-evidence-index="${evidenceIndex}" ${disabled?'disabled':''}>定位原句 ${evidenceIndex+1}</button></div>`).join('')||'<p class="modal-help">缺少可定位原句，请重新故事审稿。</p>'}
 <div class="analysis-actions">${evidence.length>1?`<label>修订位置<select data-review-target="${index}" aria-label="修订位置" ${disabled?'disabled':''}>
 <option value="">选择要修订的原句</option>${targets.map(target=>`<option value="${target.evidence_index}">原句 ${target.evidence_index+1} · ${esc(target.chapter_title||'来源章节')}</option>`).join('')}</select></label>`:
 `<input type="hidden" data-review-target="${index}" value="${targets[0]?.evidence_index??''}">`}
 <button class="primary-btn" data-review-repair="${index}" ${disabled?'disabled':''}>生成局部修订候选</button></div>
 <div class="analysis-actions"><select data-review-choice="${index}" ${decided?'disabled':''}><option value="">待处理</option><option value="accepted" ${decision==='accepted'?'selected':''} ${stale?'disabled':''}>需要修订</option>
 <option value="ignored" ${decision==='ignored'?'selected':''}>忽略建议</option><option value="explained" ${decision==='explained'?'selected':''}>补充解释</option></select>
 <input data-review-reason="${index}" placeholder="处理理由" value="${esc(reason)}" maxlength="2000" ${decided?'readonly':''}></div>
 <p class="modal-help" data-review-decision-note="${index}" ${decision==='accepted'?'':'hidden'}>已记为需要修订，正文尚未因此修改；可说明解释或忽略理由，改稿后也可重新审稿。</p>
 ${resolution?'<p class="modal-help">已结合当前稿保存作者处理理由。</p>':''}</section>`;
 }).join('')||'<p class="modal-help">尚无可处理的故事问题。</p>'}
 ${(review?.uncertainties||payload.uncertainties||[]).map(text=>`<p class="modal-help">${esc(text)}</p>`).join('')}
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" data-close>关闭</button><button class="ghost-btn" data-base-review>基础归档检查</button>
 <button class="primary-btn" data-semantic-review>${review?'重新故事审稿':'生成故事审稿'}</button></div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-semantic-review]').onclick=async()=>{
  try{
   if(!current())return;
   await authorQueueTask('semantic_review',scope);if(!current())return;wrap.remove();view='tasks';render();
  }
  catch(error){if(current())wrap.querySelector('.form-error').textContent=error.message}
 };
 wrap.querySelector('[data-base-review]').onclick=async()=>{
  try{
   if(!current())return;
   const result=await durableOperation('/api/review','POST',{chapter_id:chapterId},current);
   if(!current())return;wrap.remove();render();showReviewReport(result.report||result);
  }
  catch(error){if(current())wrap.querySelector('.form-error').textContent=error.message}
 };
 const prepare=async(index,evidenceIndex)=>{
  if(!current())throw new Error('作品或章节已切换，请重新打开本章审稿');
  const latest=await authorReviewCurrent(review,chapterId,owner,branch);
  if(!scope.current()||!wrap.isConnected)throw new Error('作品或章节已切换，请重新打开本章审稿');
  const item=latest.findings?.find(row=>row.id===(issues[index].id||issues[index].issue_id));
  const target=item?.repair_targets?.find(row=>row.evidence_index===evidenceIndex);
  if(!item||item.stale||!target)throw new Error('请明确选择这条问题中要修订的原句，无法确定时请重新审稿');
  const source=await authorReviewSourceCurrent(target.source,owner,branch);
  if(!scope.current()||!wrap.isConnected)throw new Error('作品或章节已切换，请重新打开本章审稿');
  return {item,source,review_source:{review_id:latest.id||latest.review_id,issue_id:item.id,evidence_index:evidenceIndex}};
 };
 wrap.querySelectorAll('[data-review-source]').forEach(button=>button.onclick=async()=>{
  if(preparing||!current())return;preparing=true;button.disabled=true;
  try{const ready=await prepare(Number(button.dataset.reviewSource),Number(button.dataset.reviewEvidenceIndex));authorReviewSelectSource(ready.source,wrap)}
  catch(error){if(current())wrap.querySelector('.form-error').textContent=error.message}
  finally{preparing=false;button.disabled=stale}
 });
 wrap.querySelectorAll('[data-review-repair]').forEach(button=>button.onclick=async()=>{
  if(preparing||aiBusy||!current())return;const index=Number(button.dataset.reviewRepair);const chosen=wrap.querySelector(`[data-review-target="${index}"]`).value;
  if(chosen===''){wrap.querySelector('.form-error').textContent='这条问题有多处原句，请先选择要修订的位置';return}
  preparing=true;button.disabled=true;
  try{
   const ready=await prepare(index,Number(chosen));authorReviewSelectSource(ready.source,wrap);
   document.querySelector('#aiTask').value='polish';document.querySelector('#aiInstruction').value=authorReviewRepairInstruction(ready.item);
   await generateAI({source:ready.source,review_source:ready.review_source});
  }catch(error){if(current()){wrap.querySelector('.form-error').textContent=error.message;toast(error.message)}}
  finally{preparing=false;button.disabled=stale}
 });
 wrap.querySelectorAll('[data-review-choice]').forEach(select=>select.onchange=async()=>{
  if(wrap.dataset.reviewRefreshRequired==='1'||!current())return;
  const index=Number(select.dataset.reviewChoice);const item=issues[index];const reason=wrap.querySelector(`[data-review-reason="${index}"]`).value.trim();
  const prior=stale?item.author_resolution_current?item.stale_resolution?.decision||'':'':item.decision||'';
  const decision=select.value;if(!decision)return;
  if(!reason){wrap.querySelector('.form-error').textContent='请填写简短处理理由，再选择处理结果';select.value=prior;return}
  select.disabled=true;let decisionSaved=false,savedReview=null;
  try{
   const ready=await authorReviewDecisionCurrent(review,chapterId,owner,branch);
   if(!current())return;
   const currentStale=!!(ready.review.stale||ready.review.status==='stale');
   if(currentStale&&decision==='accepted')throw new Error('旧审稿只能结合当前稿说明忽略或解释理由，请重新打开本章审稿');
   try{
    await durableOperation(`/api/story-reviews/${encodeURIComponent(review.id||review.review_id)}/issues/${encodeURIComponent(item.id||item.issue_id)}/decision`,
     'POST',{decision,reason,...(currentStale?{resolution_source:ready.review.author_resolution_source}:{})},
     ()=>current()&&ready.guard(),result=>{decisionSaved=true;savedReview=result.review});
   }catch(error){if(!decisionSaved)throw error}
   if(!savedReview)throw new Error('处理回应缺少审稿报告，请重新打开本章审稿核对结果');
   if(!current())return;
   if(!ready.guard())throw new Error('处理理由已经保存，当前稿件随后发生变化，请重新打开本章审稿');
   let currentReview;
   try{
    const data=await authorJSON(`/api/story-reviews?chapter_id=${encodeURIComponent(chapterId)}`,
     {headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch}});
    currentReview=authorReviewRows(data).find(row=>(row.id||row.review_id)===(savedReview.id||savedReview.review_id));
    if(!currentReview)throw new Error('missing saved review');
   }catch{throw new Error('处理理由已保存，但最新审稿暂时无法读取。请重新打开本章审稿核对结果。')}
   if(!current())return;
   if(!ready.guard())throw new Error('处理理由已经保存，当前稿件随后发生变化，请重新打开本章审稿');
   wrap.remove();authorShowStoryReview(currentReview,chapterId,scope);
   toast(decision==='accepted'?'已记为需要修订，正文仍需改稿或说明处理理由':'审稿处理决定已保存');
  }catch(error){
   if(!current())return;
   wrap.querySelector('.form-error').textContent=error.message;
   if(decisionSaved){
    wrap.dataset.reviewRefreshRequired='1';
    wrap.querySelectorAll('[data-review-choice],[data-review-target],[data-review-source],[data-review-repair],[data-semantic-review],[data-base-review]')
     .forEach(control=>control.disabled=true);
    wrap.querySelectorAll('[data-review-reason]').forEach(input=>input.readOnly=true);
   }else select.value=prior;
  }
  finally{if(!decisionSaved)select.disabled=stale?!!item.author_resolution_current:!!item.decision&&item.decision!=='accepted'}
 });
}

function authorFinishSource(c){
 return JSON.stringify(['title','body','chapter_goal','scene_plan','pov','next_brief','recap','hook','foreshadowing_op'].map(key=>c?.[key]||''));
}
actions.prepareChapterHandoff=async()=>{
 const c=chapter();if(!c)return;
 const scope={...authorAgentScope(),chapter_id:c.id};
 const current=()=>scope.owner===requestedProject&&scope.branch===(state.branch_id||'main')&&activeChapter===scope.chapter_id;
 try{
  if(!backendOnline||!await syncBackend())throw new Error('请先保存或合并当前稿件，再承接上章。');
  if(!current())return;
  const path=`/api/chapters/${encodeURIComponent(c.id)}/handoff`;
  const preview=await authorJSON(path,{headers:{'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch}});
  if(!current())return;
  const requestId=crypto.randomUUID();let committed=false;
  const wrap=authorForm('承接上章，准备本章',`<label>《${esc(preview.previous.title)}》的交接
   <textarea id="handoffPrevious" readonly>${esc(preview.previous.next_brief)}</textarea></label>
   ${preview.previous.recap?`<details><summary>上章回顾</summary><pre class="prompt-preview">${esc(preview.previous.recap)}</pre></details>`:''}
   ${(preview.warnings||[]).map(message=>`<p class="modal-help">${esc(message)}</p>`).join('')}
   <label>本章目标<textarea id="handoffGoal" maxlength="5000" required>${esc(preview.draft.chapter_goal)}</textarea></label>
   <label>场景计划<textarea id="handoffScenes" maxlength="20000">${esc(preview.draft.scene_plan)}</textarea></label>
   <label>视角<input id="handoffPov" maxlength="500" value="${esc(preview.draft.pov)}"></label>`,async form=>{
    if(!current())throw new Error('作品或章节已切换，请回到原章节重新承接。');
    if(!committed){
     const data={request_id:requestId,source_token:preview.source_token,
      chapter_goal:form.querySelector('#handoffGoal').value,scene_plan:form.querySelector('#handoffScenes').value,
      pov:form.querySelector('#handoffPov').value};
     await durableOperation(path,'POST',data,current,()=>{
      committed=true;authorLockCommittedForm(form);form.querySelector('[type="submit"]').textContent='重新读取已保存任务';
     });
    }else{
     const before=MoyuSync.copy(state),remote=await readServerState(scope.owner,scope.branch);
     if(!current())throw new Error('本章任务已经保存，请回到来源章节查看。');
     receiveState(remote,before);
    }
    if(current()){render();toast('本章任务已保存，可以继续写作')}
   },'确认本章任务');
  wrap.dataset.chapterHandoff=c.id;
 }catch(error){if(current())toast(error.message)}
};
function authorNextUnfinishedChapter(id){
 return state.chapters.slice(state.chapters.findIndex(item=>item.id===id)+1).find(item=>item.status!=='完成');
}
actions.finishChapter=async()=>{
 if(authorFinishing)return toast('正在保存本章归档，请稍候');
 if(state.project.book_status==='complete')return toast('全书已完稿，请先在终稿检查中恢复写作');
 const c=chapter();if(!c||Array.from(c.body.replace(/\s/g,'')).length<60)return toast('先写出一小段完整正文，再归档');
 const owner=requestedProject,branch=state.branch_id||'main',key=JSON.stringify([owner,branch,c.id]);
 authorFinishing={owner,branch,chapterId:c.id};
 document.querySelector('[data-action="finishChapter"]')?.setAttribute('disabled','');
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  const sameBook=()=>owner===requestedProject&&branch===(state.branch_id||'main');
  if(!sameBook()||activeChapter!==c.id)throw new Error('作品或章节已经切换，请回到原章继续归档');
  const currentChapter=chapter(c.id),alreadyComplete=currentChapter.status==='完成';
  const source=authorFinishSource(currentChapter);
  let finish=authorChapterFinishes.get(key);
  if(!finish||finish.source!==source){finish={source,nextId:null,done:false};authorChapterFinishes.set(key,finish)}
  const sourceCurrent=()=>sameBook()&&activeChapter===c.id&&authorFinishSource(chapter(c.id))===finish.source;
  const pending=state.memory.filter(m=>m.source_chapter_id===c.id&&!['accepted','已确认','rejected','已拒绝','retracted','已撤回'].includes(m.status));
  if(pending.length){actions.chapterAnalysis();return toast('本章还有待决定的记忆，请先确认或拒绝')}
  if(!currentChapter.chapter_goal.trim()||!currentChapter.scene_plan.trim()||!currentChapter.pov.trim()){
   document.querySelector('.chapter-plan')?.setAttribute('open','');
   document.querySelector(!currentChapter.chapter_goal.trim()?'#chapterGoal':!currentChapter.scene_plan.trim()?'#chapterScenePlan':'#chapterPov')?.focus();
   throw new Error('请先填好本章目标、场景计划和视角，再归档本章');
  }
  for(const stage of ['prewrite','precommit','postcommit']){
   if(!sourceCurrent())throw new Error('本章或交接内容已经变化，请回到本章重新核对');
   if(stage==='precommit'&&chapter(c.id).status!=='完成'){
    chapter(c.id).status='完成';save();if(!await syncBackend())throw new Error('本章状态尚未保存');
   }
   const result=await durableOperation(`/api/chapters/${encodeURIComponent(c.id)}/commit`,'POST',{stage},sourceCurrent);
   if(!['passed','overridden'].includes(result.status))throw new Error('本章归档检查未通过，请处理后重试');
  }
  if(!sourceCurrent())throw new Error('本章或交接内容已经变化，请回到本章重新核对');
  let next=(finish.nextId&&chapter(finish.nextId))||authorNextUnfinishedChapter(c.id)||
   (alreadyComplete?state.chapters[state.chapters.findIndex(item=>item.id===c.id)+1]:null);
  if(!next){
   next={id:finish.nextId||crypto.randomUUID(),title:`第 ${state.chapters.length+1} 章`,body:'',status:'草稿',updated:Date.now(),
    chapter_goal:'',next_brief:'',scene_plan:'',pov:chapter(c.id).pov||'',recap:'',hook:'',foreshadowing_op:''};
   finish.nextId=next.id;state.chapters.push(next);save();
   if(!await syncBackend())throw new Error('本章交接已保存，下一章尚未保存，请恢复连接后在本章继续重试');
  }
  if(!sourceCurrent())throw new Error('本章或交接内容已经变化，请回到本章重新核对');
  finish.nextId=next.id;finish.done=true;
  activeChapter=next.id;storage.set(cacheKey(':active'),next.id);
  view='editor';render();toast('本章已归档，下一章已接到交接任务');
 }catch(error){toast(`归档未完成：${error.message}`)}
 finally{authorFinishing=null;document.querySelector('[data-action="finishChapter"]')?.removeAttribute('disabled')}
};

actions.revisionHistory=async()=>{
 const c=chapter();if(!c)return;
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  const data=await authorJSON(`/api/chapters/${encodeURIComponent(c.id)}/revisions?include_body=1&diff=1`);
  const wrap=document.createElement('div');wrap.className='modal-backdrop';
  wrap.innerHTML=`<div class="modal candidate-modal"><h3>《${esc(c.title)}》历史版本</h3>
  <div class="revision-list">${(data.revisions||[]).map((revision,index)=>`<article class="analysis-item"><strong>版本 ${data.revisions.length-index} · ${esc(revision.created_at)}</strong>
  <small>${revision.characters} 字</small><details><summary>正文与差异</summary><pre class="prompt-preview">${esc(revision.body||'')}</pre>
  <pre class="prompt-preview">${esc(revision.diff||'')}</pre></details><button class="ghost-btn" data-author-restore="${safeId(revision.id)}">预览恢复</button></article>`).join('')}</div>
  <div class="modal-actions"><button class="ghost-btn" data-close>关闭</button></div></div>`;
  document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
  wrap.querySelectorAll('[data-author-restore]').forEach(button=>button.onclick=()=>{
   const revision=data.revisions.find(row=>row.id===button.dataset.authorRestore);
   authorForm('恢复为新版本',`<p class="modal-help">原有版本继续保留。恢复后，旧审稿和依赖旧原句的资料需要复核。</p><pre class="prompt-preview">${esc(revision.body||'')}</pre>`,async()=>{
    await durableOperation(`/api/chapters/${encodeURIComponent(c.id)}/restore-revision`,'POST',{revision_id:revision.id});
    wrap.remove();render();toast('已经恢复为新版本');
   },'确认恢复为新版本');
  });
 }catch(error){toast(`历史读取失败：${error.message}`)}
};

markdownExport=async()=>{
 try{
  if(!backendOnline)throw new Error('连接恢复后才能核验当前终稿；现在可以先导出稿件 JSON');
  const gate=await verifiedFinalGate();
  if(!gate.allowed){await showFinalizationCheck();return}
  const override=gate.override;const headers=override?.reason?{'X-Moyu-Export-Override':encodeURIComponent(override.reason)}:{};
  const response=await fetch('/api/export/markdown',{headers});
  if(!response.ok){const error=await response.json();throw new Error(error.issues?.join('；')||error.error||'终稿导出未完成')}
  authorDownloadBlob(exportFilename('md'),await response.blob());toast('已导出经过当前稿核验的 Markdown');
 }catch(error){toast(error.message)}
};

document.querySelector('#markdownExportBtn').onclick=()=>markdownExport();
document.querySelector('#epubExportBtn').onclick=async()=>{if(await syncBackend())epubExport();else toast('当前稿件需要先保存或合并')};
document.querySelector('#exportBtn').onclick=actions.exportManuscript;
document.querySelector('#fileInput').onchange=async event=>{
 const file=event.target.files?.[0];event.target.value='';if(!file)return;
 try{
  if(file.size>50*1024*1024)throw new Error('稿件包超过 50 MB，请使用完整备份恢复');
  const imported=normalizeState(JSON.parse(await file.text()));if(!imported)throw new Error('稿件包格式不正确，原作品保持原样');
  authorForm('导入稿件预览',`<p class="modal-help">《${esc(imported.project.title)}》 · ${imported.chapters.length} 章 ·
  ${imported.chapters.reduce((n,c)=>n+Array.from(c.body).length,0).toLocaleString()} 字</p>
  <p class="modal-help">将替换当前作品的稿件内容。原有完整作品会先创建备份；稿件 JSON 不含完整历史和任务。</p>
  <label class="confirmation-check"><input type="checkbox" id="importConfirm" required>确认替换当前作品稿件</label>`,async w=>{
   if(!w.querySelector('#importConfirm').checked)throw new Error('请先确认替换');
   await durableOperation('/api/backups','POST',{});
   const payload={...imported,project:{...imported.project,id:state.project.id},version:confirmedState.version,
    expected_head:confirmedState.head_revision??null,full_state:true};
   await durableOperation('/api/state','PUT',payload);activeChapter=state.chapters[0]?.id;view='editor';render();toast('稿件已导入并保存');
  },'备份后导入');
 }catch(error){toast(error.message)}
};

function authorBind(){
 authorSyncPortableScope();
 if(view==='backups'){
  authorRenderPortablePending();
  if(!authorPortableDialog&&!authorPortablePending()&&authorAbandonedPortablePreviews().length)
   authorReleasePortablePreviews().then(authorRenderPortablePending).catch(()=>authorRenderPortablePending());
 }
 if(view==='assistant')authorBindAssistant();
 if(view==='settings')globalThis.MoyuAgentSettings?.mount(document.querySelector('#agentSettingsHost'));
 if(view==='editor'){
  authorBindWorkbench();
  const body=document.querySelector('#chapterBody');
  if(body){
   body.oninput=()=>authorEditBody(body.value);
   body.onselect=captureSelection;body.onkeyup=captureSelection;body.onmouseup=captureSelection;
   body.addEventListener('keydown',event=>{
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='z'&&!event.isComposing){
     event.preventDefault();authorUndo(event.shiftKey);
    }
   });
  }
  authorBindEditorPreferences(body);
  document.querySelectorAll('[data-format]').forEach(button=>{
   button.onmousedown=event=>event.preventDefault();button.onclick=()=>authorFormat(button.dataset.format);
  });
  document.querySelector('[data-editor-undo]')?.addEventListener('click',()=>authorUndo());
  document.querySelector('[data-editor-redo]')?.addEventListener('click',()=>authorUndo(true));
  document.querySelector('#aiRevise')?.addEventListener('click',reviseAI);
  document.querySelector('#aiOriginal')?.addEventListener('click',showOriginalCandidate);
  authorUpdateUndoControls();
  if(authorFinishing)document.querySelector('[data-action="finishChapter"]')?.setAttribute('disabled','');
 }
 if(view==='plot'){
  document.querySelectorAll('[data-plot-edit]').forEach(button=>button.onclick=()=>{
   const stage=button.dataset.plotStage,index=Number(button.dataset.plotIndex),raw=state.plot[stage][index];
   authorPlotForm(typeof raw==='string'?{text:raw}:raw,stage,index);
  });
  document.querySelectorAll('[data-plot-move]').forEach(button=>button.onclick=()=>{
   const raw=state.plot[button.dataset.plotStage][Number(button.dataset.plotIndex)];authorPlotLifecycle(typeof raw==='string'?{text:raw}:raw);
  });
 }
 if(view==='memory')document.querySelectorAll('[data-memory-source]').forEach(button=>button.onclick=()=>authorJumpToEvidence(state.memory.find(m=>m.id===button.dataset.memorySource)));
 if(view==='story'){
  const panel=[...document.querySelectorAll('.story-panel-wide')].find(el=>el.querySelector('h3')?.textContent==='人物状态知识范围');
  if(panel){
   const add=document.createElement('button');add.className='ghost-btn';add.textContent='＋ 人物状态';add.onclick=()=>authorCharacterStateForm();
   panel.querySelector('.panel-title')?.append(add);
   const rows=panel.querySelectorAll('.scope-review-row');
   rows.forEach((row,index)=>{const item=storyMap.character_states?.[index];if(!item)return;const button=document.createElement('button');button.className='ghost-btn';button.textContent='编辑';button.onclick=()=>authorCharacterStateForm(item);row.append(button)});
  }
 }
}

function authorCharacterStateForm(item){
 const x=item||{};const scope=x.knowledge_scope||{};
 authorForm(item?'编辑人物状态':'新增人物状态',`<label>人物<select id="stateEntity">${storyEntityOptions(x.entity_id)}</select></label>
 <label>变化项目<input id="stateAttribute" value="${esc(x.attribute||'')}" placeholder="例如：位置、伤势、持有物"></label>
 <label>当前状态<textarea id="stateValue">${esc(typeof x.value==='string'?x.value:JSON.stringify(x.value||''))}</textarea></label>
 <div class="plan-grid"><label>从第几章开始有效<input id="stateStart" type="number" min="1" value="${chapterNumberFromScope(x.valid_start)||Math.max(1,state.chapters.findIndex(c=>c.id===activeChapter)+1)}"></label>
 <label>到第几章结束（可空）<input id="stateEnd" type="number" min="1" value="${chapterNumberFromScope(x.valid_end)||''}"></label>
 <label>资料性质<select id="stateMode">${[['world_truth','已发生事实'],['character_belief','人物认知'],['narrator','叙述者已知'],['inference','推测'],['author_plan','作者计划']].map(([value,label])=>`<option value="${value}" ${scope.epistemic_mode===value?'selected':''}>${label}</option>`).join('')}</select></label>
 <label>哪些人物知道<select id="stateKnown" multiple size="3">${storyEntityMultiOptions(scope.known_by||[])}</select></label></div>`,async w=>{
  const value=id=>w.querySelector('#'+id).value;const known=[...w.querySelector('#stateKnown').selectedOptions].map(option=>option.value);
  if(!value('stateAttribute').trim()||!value('stateValue').trim())throw new Error('请填写变化项目和当前状态');
  if(value('stateMode')==='character_belief'&&!known.length)throw new Error('人物认知需要选出知情人物');
  const start=Number(value('stateStart')),end=value('stateEnd')?Number(value('stateEnd')):null;
  if(end!==null&&end<start)throw new Error('结束章节不能早于开始章节');
  await durableOperation(item?`/api/character-states/${encodeURIComponent(item.id)}`:'/api/character-states',item?'PATCH':'POST',{
   entity_id:value('stateEntity'),attribute:value('stateAttribute').trim(),value_json:JSON.stringify(value('stateValue')),
   valid_start:String(start),valid_end:end===null?null:String(end),
   knowledge_scope_json:{branches:[state.branch_id||'main'],available_from:String(start),epistemic_mode:value('stateMode'),known_by:known}});
  storyLoaded=false;await actions.refreshStory(true);toast('人物状态与知情范围已保存');
 });
}

const authorOriginalRender=render;
render=function(){
 authorRememberEditorPosition();
 authorSettingsDispose?.();
 const library=document.querySelector('.workbench-library');
 const libraryScroll=library?.dataset.projectId===requestedProject&&library.dataset.branchId===(state.branch_id||'main')?library.scrollTop:0;
 document.querySelectorAll('[data-author-chapter-report]').forEach(dialog=>dialog.remove());
 clearTimeout(authorAgentTimer);authorAgentSequence++;
 authorWorkbenchContextSequence++;
 document.body.classList.toggle('is-writing-workbench',view==='editor');
 document.body.classList.remove('workbench-focus');
 authorOriginalRender();authorBind();
 if(view==='editor'){authorRestoreChapterNavigation(libraryScroll);authorRestoreEditorPosition()}
 if(view==='editor')authorLoadEditorBlueprint();
 if(['library','blueprint','settings','backups','references'].includes(view))authorLoadPage();
 globalThis.MoyuAgentChat?.sync();
};
document.querySelectorAll('.nav-item').forEach(button=>button.onclick=async()=>{
 if(authorBlueprintDirty&&!await authorSaveBlueprint())return;
 view=button.dataset.view;render();
});
document.querySelectorAll('.nav-item').forEach(button=>{button.title=button.textContent.trim();button.setAttribute('aria-label',button.textContent.trim())});
render();
scheduleQueue(0);
setInterval(()=>{if(!backendOnline&&!syncInFlight)reconnect()},5000);
window.addEventListener('online',()=>reconnect());
document.addEventListener('pointerdown',event=>{
 const settings=document.querySelector('.editor-reading-settings[open]');
 if(settings&&!settings.contains(event.target))settings.open=false;
});
document.addEventListener('keydown',event=>{
 const settings=document.querySelector('.editor-reading-settings[open]');
 if(event.key==='Escape'&&settings){settings.open=false;settings.querySelector('summary').focus()}
});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')authorRememberEditorPosition()});
window.addEventListener('pagehide',()=>authorRememberEditorPosition());
window.addEventListener('beforeunload',event=>{authorRememberEditorPosition();cacheDraft();authorCacheBlueprint();if(isDirty()||authorBlueprintDirty||authorBlueprintLocked()){event.preventDefault();event.returnValue=''}});
