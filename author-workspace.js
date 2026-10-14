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
let authorBackups=[];
let authorReferences=[];
let authorLoadToken=0;
let authorSwitchChain=Promise.resolve();
const editorUndo=new Map();
const editorRedo=new Map();

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

function authorEditorView(){
 const c=chapter();
 const reviewLabel={passed:'已通过',needs_revision:'需修改',stale:'已过期',pending:'未完成',failed:'失败'}[c?.review_status]||'未审稿';
 return `${authorPageHead('章节写作',state.project.book_status==='complete'?'':`<button class="primary-btn" data-action="newChapter">＋ 章节</button>`)}
 <div class="editor-layout"><aside class="chapter-sidebar">${authorChapterList()}</aside><section class="editor-card">
 ${c?`<div class="editor-toolbar">
 <button data-editor-undo title="撤销最近修改" aria-label="撤销最近修改">↶</button><button data-editor-redo title="重做最近修改" aria-label="重做最近修改">↷</button>
 <button data-format="bold" title="粗体" aria-label="粗体"><b>B</b></button><button data-format="quote" title="引用" aria-label="引用">❞</button>
 <button data-format="scene" title="场景分隔" aria-label="场景分隔">***</button>
 <button class="ghost-btn" data-action="reviewChapter">本章审稿</button><button class="ghost-btn" data-action="chapterAnalysis">章后记忆</button>
 <button class="ghost-btn" data-action="revisionHistory">历史</button><span class="editor-meta" id="selectionState">未选文字</span></div>
 <input class="title-input" id="chapterTitle" value="${esc(c.title)}" aria-label="章节标题">
 <textarea class="chapter-editor" id="chapterBody" aria-label="章节正文">${esc(c.body)}</textarea>
 <div class="editor-footer"><span id="liveCount">${Array.from(c.body.replace(/\s/g,'')).length} 字</span>
 <select id="chapterStatus" aria-label="章节状态">${['草稿','进行中','完成'].map(label=>`<option ${c.status===label?'selected':''}>${label}</option>`).join('')}</select>
 <span class="review-pill">审稿：${reviewLabel}</span></div>
 <details class="chapter-plan" ${c.body?'':'open'}><summary>本章任务与回顾</summary><div class="plan-grid">
 ${[['chapterGoal','chapter_goal','本章目标'],['chapterPov','pov','视角'],['chapterScenePlan','scene_plan','场景计划'],
 ['chapterForeshadowing','foreshadowing_op','伏笔动作'],['chapterRecap','recap','章节回顾'],['chapterNextBrief','next_brief','下一章任务'],
 ['chapterHook','hook','章节出口']].map(([id,key,label])=>`<label>${label}${['chapterGoal','chapterPov'].includes(id)?
 `<input id="${id}" value="${esc(c[key]||'')}">`:`<textarea id="${id}">${esc(c[key]||'')}</textarea>`}</label>`).join('')}</div></details>
 <section class="ai-copilot"><div class="ai-copilot-head"><h3>AI 候选</h3><span class="ai-badge" id="aiBadge">${aiStatus.configured?'已配置':'未配置模型'}</span></div>
 <div class="ai-controls"><select id="aiTask" aria-label="AI 任务">
 <option value="continue">续写</option><option value="polish">润色所选文字</option><option value="scene">本章场景计划</option>
 <option value="outline">故事总纲</option><option value="brainstorm">创意方向</option><option value="summary">回顾与交接</option>
 <option value="check">连续性检查</option></select><input id="aiInstruction" maxlength="2000" aria-label="补充要求" placeholder="补充要求">
 <button class="primary-btn" id="aiGenerate">生成候选</button></div><p class="ai-status" id="aiStatus"></p>
 <textarea class="ai-output" id="aiOutput" aria-label="AI 候选结果">${esc(aiDraft?.text||'')}</textarea>
 <div class="ai-result-meta"><span>${esc(aiStatus.model||'未配置模型')}</span><div class="ai-actions">
 <button class="ghost-btn" id="aiCopy" ${aiDraft?'':'disabled'}>复制</button><button class="ghost-btn" id="aiClear">拒绝 / 停止</button>
 <button class="ghost-btn" id="aiOriginal" hidden>回看原候选</button><button class="ghost-btn" id="aiRevise" disabled>整理候选</button>
 <button class="primary-btn" id="aiAdopt" ${aiDraft?.complete?'':'disabled'}>比较并采纳</button></div></div></section>`:
 '<div class="empty">还没有章节。</div>'}</section></div>`;
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
views.settings=()=>`${authorPageHead('模型与风格')}
 <div id="settingsHost"><div class="empty">正在读取设置…</div></div>`;
views.backups=()=>`${authorPageHead('备份中心','<button class="primary-btn" data-action="createBackup">＋ 完整备份</button>')}
 <div class="backup-scope"><strong>完整作品备份</strong><p>包含正文、历史版本、设定、记忆证据、人物关系、时间线、任务和审计记录。</p>
 <button class="ghost-btn" data-action="exportManuscript">导出稿件 JSON</button><button class="ghost-btn" data-action="importManuscript">导入稿件 JSON</button></div>
 <div id="backupList"><div class="empty">正在读取备份…</div></div>`;
views.references=()=>`${authorPageHead('旧书与参考库','<button class="primary-btn" data-action="importBook">导入 TXT / MD / EPUB</button>')}
 <div id="referenceList"><div class="empty">正在读取参考来源…</div></div>`;
views.assistant=()=>`${authorPageHead('项目助手')}
 <form id="projectAssistant" class="settings-form"><label>问题或创作要求<textarea id="assistantQuery" maxlength="2000" required></textarea></label>
 <div class="settings-actions"><button class="primary-btn" type="submit">查看资料与候选计划</button></div></form>
 <div id="assistantAnswer"></div>`;

function authorBindAssistant(){
 const form=document.querySelector('#projectAssistant');if(!form)return;
 form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;
  const host=document.querySelector('#assistantAnswer');host.innerHTML='<div class="empty">正在查看当前作品…</div>';
  try{
   if(!await syncBackend())throw new Error('请先保存或合并当前草稿');
   const query=form.querySelector('#assistantQuery').value.trim();const c=chapter();
   const guessed=/润色|改写|修改选中/.test(query)?'polish':/续写|接着写|往下写/.test(query)?'continue':'scene';
   const source=c?candidateSource(guessed,c):null;
   const result=await authorJSON('/api/assistant/query',{method:'POST',body:JSON.stringify({query,chapter_id:c?.id,source})});
   const p=result.proposal;
   host.innerHTML=`<p>${esc(result.answer)}</p>${(result.results||[]).map(item=>`<article class="result"><strong>${esc(item.title||item.name||'作品资料')}</strong><p>${esc(item.snippet||item.body||item.text||item.excerpt||'')}</p></article>`).join('')}
   ${p?`<section class="assistant-proposal"><h3>${esc(p.label)}</h3><p>《${esc(p.chapter_title)}》 · ${esc(p.target)}</p><p>${esc(p.instruction)}</p>
   <div class="settings-actions"><button class="ghost-btn" data-assistant-reject>放弃</button><button class="primary-btn" data-assistant-confirm>确认生成候选</button></div><p class="form-error" role="alert"></p></section>`:''}`;
   host.querySelector('[data-assistant-reject]')?.addEventListener('click',()=>{host.querySelector('.assistant-proposal').remove();toast('已放弃，作品保持原稿')});
   host.querySelector('[data-assistant-confirm]')?.addEventListener('click',async()=>{
    const confirm=host.querySelector('[data-assistant-confirm]');confirm.disabled=true;
    try{
     if(!await syncBackend())throw new Error('请先保存或合并当前草稿');
     const data=await durableOperation('/api/assistant/confirm','POST',{token:p.token,confirm:true});
     const job=data.job;jobSources.set(job.id,job.source||p.source);scheduleQueue(0);view='tasks';render();toast('已保存生成任务，完成后可以比较候选');
    }catch(error){host.querySelector('.form-error').textContent=error.message;confirm.disabled=false}
   });
  }catch(error){host.innerHTML=`<div class="empty">${esc(error.message)}</div>`}finally{button.disabled=false}
 };
}

async function authorLoadPage(){
 const token=++authorLoadToken;const owner=requestedProject;const which=view;
 let settingsSequence=null;
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
   await loadAIStatus();
   if(token!==authorLoadToken||owner!==requestedProject||settingsSequence!==authorSettingsSeq||view!=='settings')return;
   authorSettings=data.settings||data;authorRenderSettings();
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
 if(id===requestedProject){view='editor';render();return}
 if(backendOnline&&!await syncBackend())return toast('当前草稿需要先保存或合并，切换已暂停');
 if(authorBlueprintDirty&&!await authorSaveBlueprint())return;
 cacheDraft();persistCandidates();clearTimeout(syncTimer);++authorLoadToken;
 for(const controller of streamReaders.values())controller.abort();
 streamReaders.clear();jobSources.clear();clearTimeout(queueTimer);
 backendOnline=false;requestedProject=id;storage.set(CURRENT_PROJECT_KEY,id);
 state=load();state.branch_id=authorProjects.find(p=>p.id===id)?.branch_id||state.branch_id;
 confirmedState=loadConfirmed();candidateStore=loadCandidates();activeChapter=storage.get(cacheKey(':active'))||state.chapters[0]?.id;
 authorBlueprint=null;authorBlueprintVersion=null;authorBlueprintDirty=false;authorBlueprintBase=null;authorBlueprintConflict=null;authorSettings=null;++authorSettingsSeq;queueJobs=[];
 clearTimeout(authorPlanningTimer);authorPlanning=null;authorPlanningRows=[];authorPlanningSelected=null;authorPlanningBusy=false;
 aiDraft=null;aiRunId=null;mergePending=null;saveError='';savedSelection=null;storyLoaded=false;storyLoadedChapter=null;
 storyMap={relationships:[],timeline:[],foreshadowing:[],conflicts:[],character_states:[],knowledge_scope:{}};
 view='dashboard';render();await hydrate();await loadAIStatus();scheduleQueue(0);
}

actions.createProject=()=>authorForm('新建作品',`<label>作品名<input id="newBookTitle" maxlength="200" required></label>
 <label>一句话故事<textarea id="newBookSummary" maxlength="50000"></textarea></label><label>题材<input id="newBookGenre" maxlength="100"></label>
 <label>目标字数<input id="newBookGoal" type="number" value="50000" min="0" max="1000000000"></label>`,async w=>{
  const title=w.querySelector('#newBookTitle').value.trim();if(!title)throw new Error('请给新作品起个名字');
  if(!await syncBackend())throw new Error('当前作品需要先保存或合并');
  const data=await authorJSON('/api/projects',{method:'POST',body:JSON.stringify({title,summary:w.querySelector('#newBookSummary').value,genre:w.querySelector('#newBookGenre').value,goal:Number(w.querySelector('#newBookGoal').value)})});
  const project=data.project||data.state?.project;authorProjects.push(project);await authorSwitchProject(project.id);
 });
async function authorDuplicateProject(id){
 const p=authorProjects.find(p=>p.id===id);
 authorForm('复制作品',`<label>新作品名<input id="copyTitle" value="${esc(p?.title||'作品')} · 副本" maxlength="200"></label>`,async w=>{
  if(!await syncBackend())throw new Error('当前作品需要先保存或合并');
  const data=await authorJSON(`/api/projects/${encodeURIComponent(id)}/duplicate`,{method:'POST',body:JSON.stringify({title:w.querySelector('#copyTitle').value.trim()})});
  const project=data.project||data.state?.project;authorProjects.push(project);await authorSwitchProject(project.id);
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

function authorPlanningPreview(stage,candidate){
 if(!candidate)return '';
 if(stage==='overview')return `<dl class="planning-overview">${[['premise','故事核心'],['genre','题材'],['audience','读者'],['tone','基调'],['contract','创作约定']].map(([key,label])=>
  `<div><dt>${label}</dt><dd>${esc(candidate[key]||'')}</dd></div>`).join('')}</dl>`;
 return (candidate.volumes||[]).map(volume=>`<section class="planning-volume"><h4>${esc(volume.title||
  authorPlanning?.blueprint?.volumes?.find(item=>item.id===volume.id)?.title||'卷')}</h4>
  ${stage==='volumes'?`<p>${esc(volume.summary)}</p>`:(volume.chapters||[]).map(chapter=>`<div class="planning-chapter"><strong>${esc(chapter.title||
   authorPlanning?.blueprint?.volumes?.flatMap(item=>item.chapters||[]).find(item=>item.id===chapter.id)?.title||'章节')}</strong>
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

function authorProbeDescription(probe,model=''){
 if(!probe)return '';
 const requested=probe.requested_model||probe.model||model;
 const identity=requested?` · 请求 ${requested}${probe.returned_model?` · 回传 ${probe.returned_model}`:' · 未回传模型名称'}`:'';
 const time=probe.checked_at?` · ${probe.checked_at}`:'';
 if(probe.configuration_stale)return `本次检测已失效：检测期间设置发生变化，不能验证当前设置${identity}${time}`;
 return probe.success?`最近默认模型实测成功（连接回复符合要求）${identity}${time}`:
  `最近连接检测未通过：${probe.error||'模型未返回符合要求的回复'}${identity}${time}`;
}

function authorBeginSettingsOperation(){
 ++aiStatusSeq;
 return ++authorSettingsSeq;
}

function authorRenderSettings(){
 const host=document.querySelector('#settingsHost');if(!host||!authorSettings)return;
 const s=authorSettings,owner=requestedProject;const parameters=s.parameters||{},styles=s.styles||{},models=s.models||{};
 host.innerHTML=`<form id="modelSettings" class="settings-form"><div class="settings-fields">
 <label>服务地址<input id="modelBaseUrl" type="url" value="${esc(s.base_url||'')}" placeholder="https://example.com/v1"></label>
 <label>默认模型<input id="modelDefault" value="${esc(s.model||'')}"></label>
 <label>访问密钥<input id="modelKey" type="password" autocomplete="new-password" placeholder="${esc(s.api_key_masked||s.key_masked||'填写密钥')}" value=""></label>
 <label>创作风格<input id="modelTone" value="${esc(styles.tone||'')}"></label>
 <label class="field-wide">写作要求<textarea id="modelStyleInstructions">${esc(styles.instructions||'')}</textarea></label>
 <label>变化程度<input id="modelTemperature" type="number" min="0" max="2" step="0.05" value="${parameters.temperature??0.65}"></label>
 <label>每次输出上限<input id="modelMaxTokens" type="number" min="1" max="128000" step="1" value="${parameters.max_tokens??3000}"></label>
 <label>模型思考程度<select id="modelReasoning">${[['','服务默认'],['none','关闭（通用）'],['minimal','最少'],['low','较少'],['medium','适中'],['high','较多'],['xhigh','充分'],['deepseek_disabled','关闭（DeepSeek）'],['deepseek_enabled','开启（DeepSeek）']].map(([value,label])=>
 `<option value="${value}" ${(parameters.thinking?.type?'deepseek_'+parameters.thinking.type:parameters.reasoning_effort||'')===value?'selected':''}>${label}</option>`).join('')}</select></label>
 <label>每日生成上限<input id="modelDailyLimit" type="number" min="0" max="100000" value="${s.daily_limit??50}"></label>
 ${[['continue','续写模型'],['polish','润色模型'],['outline','规划模型'],['summary','回顾模型'],['check','审稿模型']].map(([key,label])=>
 `<label>${label}<input data-task-model="${key}" value="${esc(models[key]||'')}" placeholder="使用默认模型"></label>`).join('')}</div>
 <p id="modelProbeStatus" class="modal-help">${esc(authorProbeDescription(s.last_probe,s.model)||
  (s.configured||String(s.base_url||'').trim()&&String(s.model||'').trim()?'设置已保存，尚未验证默认模型响应':'尚未配置模型'))}</p>
 <p class="form-error" role="alert"></p><div class="settings-actions"><button class="ghost-btn" type="button" id="modelProbe">实测连接（调用模型）</button>
 <button class="primary-btn" type="submit">保存设置</button></div></form>`;
 const form=host.querySelector('#modelSettings');
 const payload=()=>{
  const value=id=>form.querySelector('#'+id).value;
  const nextModels={};form.querySelectorAll('[data-task-model]').forEach(input=>nextModels[input.dataset.taskModel]=input.value.trim());
  const result={base_url:value('modelBaseUrl').trim(),model:value('modelDefault').trim(),models:nextModels,
   styles:{tone:value('modelTone'),instructions:value('modelStyleInstructions')},
   parameters:{...parameters,temperature:Number(value('modelTemperature')),max_tokens:Number(value('modelMaxTokens'))},daily_limit:Number(value('modelDailyLimit'))};
  const reasoning=value('modelReasoning');
  delete result.parameters.thinking;delete result.parameters.reasoning_effort;
  if(reasoning.startsWith('deepseek_'))result.parameters.thinking={type:reasoning.slice('deepseek_'.length)};
  else if(reasoning)result.parameters.reasoning_effort=reasoning;
  if(value('modelKey').trim())result.api_key=value('modelKey').trim();return result;
 };
 const saveSettings=async sequence=>{
  if(owner!==requestedProject||sequence!==authorSettingsSeq||!form.isConnected)
   throw new Error('设置页面已更新，请重新打开设置');
  const result=await durableOperation('/api/ai/settings','PUT',payload(),
   ()=>owner===requestedProject&&sequence===authorSettingsSeq&&form.isConnected);
  if(owner!==requestedProject||sequence!==authorSettingsSeq||!form.isConnected)
   throw new Error('设置页面已更新，请重新打开设置');
  authorSettings=result.settings||result;
  const status=form.querySelector('#modelProbeStatus');
  if(status)status.textContent=authorProbeDescription(authorSettings.last_probe,authorSettings.model)||
   (authorSettings.configured||String(authorSettings.base_url||'').trim()&&String(authorSettings.model||'').trim()
    ?'设置已保存，尚未验证默认模型响应':'尚未配置模型');
  form.querySelector('#modelKey').value='';await loadAIStatus();
  if(owner!==requestedProject||sequence!==authorSettingsSeq||!form.isConnected)
   throw new Error('设置页面已更新，请重新打开设置');
  return result;
 };
 form.onsubmit=async e=>{
  e.preventDefault();const sequence=authorBeginSettingsOperation();
  const button=form.querySelector('[type="submit"]');button.disabled=true;
  try{await saveSettings(sequence);toast('模型与风格设置已经保存');authorRenderSettings()}
  catch(error){if(owner===requestedProject&&sequence===authorSettingsSeq&&form.isConnected)form.querySelector('.form-error').textContent=error.message}finally{button.disabled=false}
 };
 form.querySelector('#modelProbe').onclick=async()=>{
  let sequence=authorBeginSettingsOperation();
  const controls=[...form.querySelectorAll('input,textarea,select,button')];
  const disabled=controls.map(control=>control.disabled);controls.forEach(control=>control.disabled=true);
  try{await saveSettings(sequence);
   if(owner!==requestedProject||sequence!==authorSettingsSeq)return;
   sequence=authorBeginSettingsOperation();
   form.querySelector('#modelProbeStatus').textContent='正在验证真实模型响应…';
   const result=await durableOperation('/api/ai/probe','POST',{},
    ()=>owner===requestedProject&&sequence===authorSettingsSeq&&form.isConnected);
   if(owner!==requestedProject||sequence!==authorSettingsSeq||!form.isConnected)return;
   if(result.settings)authorSettings=result.settings;
   await loadAIStatus();
   if(owner!==requestedProject||sequence!==authorSettingsSeq||!form.isConnected)return;
   if(result.configuration_stale&&form.isConnected)authorRenderSettings();
   if(view==='settings'){
    const status=document.querySelector('#modelProbeStatus');
    if(status)status.textContent=authorProbeDescription(result,authorSettings.model);
   }
  }catch(error){if(owner===requestedProject&&sequence===authorSettingsSeq&&form.isConnected)form.querySelector('#modelProbeStatus').textContent=`连接检测未完成：${error.message}`}
  finally{controls.forEach((control,index)=>control.disabled=disabled[index])}
 };
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
 authorForm('从备份恢复作品',`<p class="modal-help">将用 ${esc(b?.created_at||'所选时间')} 的完整备份建立一份独立作品。当前作品和历史继续保留。</p>
 <label class="confirmation-check"><input id="restoreConfirm" type="checkbox" required>我确认建立恢复副本</label>`,async w=>{
  if(!w.querySelector('#restoreConfirm').checked)throw new Error('请先确认恢复');
  const result=await durableOperation(`/api/backups/${encodeURIComponent(id)}/restore`,'POST',{confirm:true});
  authorProjects.push(result.project);await authorSwitchProject(result.project.id);toast('恢复副本已打开，原作品继续保留');
 },'建立恢复副本');
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
 const wrap=document.createElement('div');wrap.className='modal-backdrop';
 wrap.innerHTML=`<div class="modal candidate-modal"><h3>分章预览</h3><p class="modal-help">${esc(filename)} · ${chapters.length} 章 · ${esc(preview.metadata?.encoding||'')}</p>
 <label>名称<input id="importBookTitle" value="${esc(preview.metadata?.suggested_title||filename.replace(/\.[^.]+$/,''))}" maxlength="200"></label>
 <label>放到哪里<select id="importBookMode"><option value="manuscript">建立独立作品</option><option value="reference">当前作品的参考来源</option></select></label>
 ${(preview.warnings||[]).map(item=>`<p class="modal-help check-bad">${esc(typeof item==='string'?item:item.message||JSON.stringify(item))}</p>`).join('')}
 <div class="import-chapters"></div>
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" data-close>取消</button><button class="primary-btn" data-import-confirm>确认导入所见章节</button></div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 const captureTitles=()=>{for(const input of wrap.querySelectorAll('[data-import-title]'))chapters[Number(input.dataset.importTitle)].title=input.value};
 const showChapters=()=>{
  wrap.querySelector('.import-chapters').innerHTML=chapters.map((c,index)=>`<details class="analysis-item" ${index===0?'open':''}><summary>${index+1}. ${esc(c.title)} · ${Array.from(c.body||'').length.toLocaleString()} 字</summary>
  <label>标题<input data-import-title="${index}" value="${esc(c.title)}" maxlength="500"></label><label>正文<textarea data-import-body="${index}" readonly>${esc(c.body)}</textarea></label>
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
   captureTitles();
   const mode=wrap.querySelector('#importBookMode').value;const title=wrap.querySelector('#importBookTitle').value.trim();
   if(!title)throw new Error('请填写名称');
   const result=await durableOperation('/api/import/apply','POST',{preview_id:preview.preview_id,source_sha256:preview.source_sha256,title,mode,chapters});
   wrap.remove();
   if(mode==='manuscript'){authorProjects.push(result.project);await authorSwitchProject(result.project.id);view='editor';render()}
   else{view='references';render()}
   toast('所见章节已经完整导入');
  }catch(error){wrap.querySelector('.form-error').textContent=error.message;button.disabled=false}
 };
}
function authorRenderReferences(){
 const host=document.querySelector('#referenceList');if(!host)return;
 host.innerHTML=authorReferences.map(r=>`<article class="book-row"><div><h3>${esc(r.title)}</h3><p>${esc(r.filename||'')} · ${Number(r.metadata?.chapter_count||0)} 章 · ${Number(r.metadata?.characters||0).toLocaleString()} 字</p></div>
 <button class="ghost-btn" data-reference-open="${safeId(r.id)}">查看来源</button></article>`).join('')||'<div class="empty">还没有参考来源。</div>';
 host.querySelectorAll('[data-reference-open]').forEach(button=>button.onclick=async()=>{
  try{
   const data=await authorJSON(`/api/references/${encodeURIComponent(button.dataset.referenceOpen)}`);
   const r=data.reference||data;const chapters=data.chapters||r.chapters||[];
   const wrap=document.createElement('div');wrap.className='modal-backdrop';
   wrap.innerHTML=`<div class="modal candidate-modal"><h3>${esc(r.title||'参考来源')}</h3><div class="search-box"><input id="referenceQuery" placeholder="搜索此来源中的原句"><button class="primary-btn" data-reference-search>搜索</button></div>
   <div id="referenceResults">${chapters.map(c=>`<details class="analysis-item"><summary>${esc(c.title)} · ${Array.from(c.body||'').length} 字</summary><pre class="prompt-preview">${esc(c.body||'')}</pre></details>`).join('')}</div>
   <div class="modal-actions"><button class="ghost-btn" data-close>关闭</button></div></div>`;
   document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
   wrap.querySelector('[data-reference-search]').onclick=async()=>{
    const q=wrap.querySelector('#referenceQuery').value.trim();if(!q)return;
    try{const result=await authorJSON(`/api/references/${encodeURIComponent(button.dataset.referenceOpen)}/search?q=${encodeURIComponent(q)}`);
     wrap.querySelector('#referenceResults').innerHTML=(result.results||[]).map(item=>`<article class="result"><strong>${esc(item.title||'')}</strong><p>${esc(item.source_quote||item.body||'')}</p><small>${esc(item.filename||r.filename||'')}</small></article>`).join('')||'<div class="empty">此来源没有匹配原句。</div>';
    }catch(error){toast(error.message)}
   };
  }catch(error){toast(error.message)}
 });
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
 wrap?.remove();activeChapter=chapterId;view='editor';render();
 const input=document.querySelector('#chapterBody');input.focus();
 input.setSelectionRange(MoyuSync.utf16Offset(c.body,start),MoyuSync.utf16Offset(c.body,end));
 input.scrollTop=Math.max(0,Math.floor(start/40)*26-100);captureSelection();input.scrollIntoView({block:'center'});
}

async function authorQueueTask(task){
 let c=chapter();if(!c)throw new Error('请先选择章节');const owner=requestedProject,id=c.id;
 if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
 c=chapter(id);if(!c||owner!==requestedProject)throw new Error('作品已切换，请重新发起任务');
 const source=candidateSource(task,c);source.source_revision_id=c.revision_id;source.source_body_hash=await sha256(c.body);
 const data=await durableOperation('/api/ai/queue','POST',{task,chapter_id:c.id,source,response_format:'json',stream:true,material_controls:materialControls(),retrieval_profile:retrievalProfile()});
 const job=data.job||data;jobSources.set(job.id,source);scheduleQueue(0);toast('任务已保存，可以继续写作');
 return job;
}

actions.chapterAnalysis=async()=>{
 const c=chapter();if(!c)return;
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  const data=await authorJSON(`/api/chapter-analysis?chapter_id=${encodeURIComponent(c.id)}`);
  const analyses=data.analyses||data.analysis?[...(data.analyses||[]),...(data.analysis?[data.analysis]:[])]:Array.isArray(data)?data:[];
  const latest=analyses[0];
  authorShowAnalysis(latest,c.id);
 }catch(error){toast(`章后记忆读取失败：${error.message}`)}
};
function authorShowAnalysis(analysis,chapterId){
 const payload=analysis?.structured||analysis?.payload||analysis||{};
 const candidates=analysis?.candidates||payload.candidates||payload.items||[];
 const summarySection=analysis?.summary||payload.summary||'';const handoffSection=analysis?.handoff||payload.handoff||payload.next_brief||'';
 const summary=typeof summarySection==='string'?summarySection:summarySection.text||'';
 const handoff=typeof handoffSection==='string'?handoffSection:handoffSection.text||'';
 const stale=analysis?.stale||analysis?.status==='stale';
 const wrap=document.createElement('div');wrap.className='modal-backdrop';
 wrap.innerHTML=`<div class="modal analysis-modal"><h3>章后记忆确认</h3><p class="modal-help">${esc(chapter(chapterId)?.title||'')}
 ${stale?' · 原稿已变，此批候选需要重新提取':''}</p>
 ${analysis?`<label>章节回顾<textarea id="analysisSummary" ${summarySection.status==='accepted'?'readonly':''}>${esc(summary)}</textarea></label>
 <label class="confirmation-check"><input id="confirmAnalysisSummary" type="checkbox" ${summary.trim()&&summarySection.status!=='accepted'?'checked':''} ${summarySection.status==='accepted'?'disabled':''}>确认章节回顾</label>
 <label>下一章任务<textarea id="analysisHandoff" ${handoffSection.status==='accepted'?'readonly':''}>${esc(handoff)}</textarea></label>
 <label class="confirmation-check"><input id="confirmAnalysisHandoff" type="checkbox" ${handoff.trim()&&handoffSection.status!=='accepted'?'checked':''} ${handoffSection.status==='accepted'?'disabled':''}>确认下一章任务</label>`:
 '<p class="modal-help">还没有本章的待确认事实。</p>'}
 <div>${candidates.map((item,index)=>`<section class="analysis-item" data-analysis-index="${index}">
 <strong>${esc(item.type||item.kind||'事实')} · ${esc(item.name||item.title||item.attribute||'')}</strong>
 <label>名称<input data-analysis-name value="${esc(item.name||'')}" maxlength="500"></label>
 <label>内容<textarea data-analysis-edit>${esc(item.note||item.description||item.summary||item.value||'')}</textarea></label>
 <div class="plan-grid"><label>资料性质<select data-analysis-mode>${[['world_truth','已发生事实'],['character_belief','人物认知'],['narrator','叙述者已知'],['inference','推测'],['author_plan','作者计划']].map(([value,label])=>`<option value="${value}" ${item.epistemic_mode===value?'selected':''}>${label}</option>`).join('')}</select></label>
 <label>哪些人物知道<select data-analysis-known multiple size="3">${storyEntityMultiOptions(item.known_by||[])}</select></label></div>
 <blockquote>${esc(item.source_quote||item.quote||item.source?.quote||'无原句证据')}</blockquote>
 <div class="analysis-actions"><button class="ghost-btn" data-analysis-source="${index}">定位原句</button>
 <select data-analysis-decision aria-label="记忆决定" ${['accepted','rejected'].includes(item.status)?'disabled':''}><option value="">待决定</option><option value="accepted" ${item.status==='accepted'?'selected':''}>确认</option><option value="rejected" ${item.status==='rejected'?'selected':''}>拒绝</option></select>
 <span>${esc(item.status||'候选')}</span></div></section>`).join('')||''}</div>
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" data-close>关闭</button><button class="ghost-btn" data-extract>重新提取</button>
 ${analysis?`<button class="primary-btn" data-confirm-analysis ${stale?'disabled':''}>保存所选决定与回顾</button>`:''}</div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-extract]').onclick=async()=>{
  try{await authorQueueTask('chapter_analysis');wrap.remove();view='tasks';render()}
  catch(error){wrap.querySelector('.form-error').textContent=error.message}
 };
 wrap.querySelectorAll('[data-analysis-source]').forEach(button=>button.onclick=()=>authorJumpToEvidence({source_chapter_id:chapterId,...candidates[Number(button.dataset.analysisSource)]},wrap));
 wrap.querySelector('[data-confirm-analysis]')?.addEventListener('click',async()=>{
  const button=wrap.querySelector('[data-confirm-analysis]');button.disabled=true;
  try{
   const decisions=[];
   for(const row of wrap.querySelectorAll('[data-analysis-index]')){
    const item=candidates[Number(row.dataset.analysisIndex)];const decision=row.querySelector('[data-analysis-decision]').value;
    if(decision&&!['accepted','rejected'].includes(item.status))decisions.push({candidate_id:item.id||item.candidate_id,decision,
     edited:{name:row.querySelector('[data-analysis-name]').value,note:row.querySelector('[data-analysis-edit]').value,
      epistemic_mode:row.querySelector('[data-analysis-mode]').value,known_by:[...row.querySelector('[data-analysis-known]').selectedOptions].map(option=>option.value)}});
   }
   await durableOperation(`/api/chapter-analysis/${encodeURIComponent(analysis.id||analysis.analysis_id)}/confirm`,'POST',{
    source_revision_id:analysis.source_revision_id,chapter_hash:analysis.chapter_hash,decisions,
    confirm_summary:!!wrap.querySelector('#confirmAnalysisSummary')?.checked,summary_text:wrap.querySelector('#analysisSummary')?.value||'',
    confirm_handoff:!!wrap.querySelector('#confirmAnalysisHandoff')?.checked,handoff_text:wrap.querySelector('#analysisHandoff')?.value||''});
   wrap.remove();render();toast('回顾与记忆决定已经保存');
  }catch(error){wrap.querySelector('.form-error').textContent=error.message;button.disabled=false}
 });
}

actions.reviewChapter=async()=>{
 const c=chapter();if(!c)return;
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  const data=await authorJSON(`/api/story-reviews?chapter_id=${encodeURIComponent(c.id)}`);
  const reviews=data.reviews||data.story_reviews||data.story_review?[...(data.reviews||data.story_reviews||[]),...(data.story_review?[data.story_review]:[])]:Array.isArray(data)?data:[];
  authorShowStoryReview(reviews[0],c.id);
 }catch(error){toast(`审稿读取失败：${error.message}`)}
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
function authorReviewSelectSource(source,wrap){
 const c=chapter(source.chapter_id);wrap?.remove();activeChapter=c.id;storage.set(cacheKey(':active'),c.id);view='editor';render();
 const input=document.querySelector('#chapterBody');input.focus();
 input.setSelectionRange(MoyuSync.utf16Offset(c.body,source.range.start),MoyuSync.utf16Offset(c.body,source.range.end));
 input.scrollTop=Math.max(0,Math.floor(source.range.start/40)*26-100);captureSelection();input.scrollIntoView({block:'center'});
}
function authorReviewRepairInstruction(item){
 const excerpt=(value,limit)=>{const chars=Array.from(String(value||''));return chars.length>limit?chars.slice(0,limit).join('')+'（节选）':chars.join('')};
 return ['仅修订选中的原句，保持范围外的事件、人物动机、事实和视角。只返回可以替换该原句的候选正文。',
  '审稿问题：'+excerpt(item.description||item.message||'',750),
  item.suggestion?'修订建议：'+excerpt(item.suggestion,1000):'请针对这个问题给出克制、具体的局部修订，不添加无依据的新事实。'].join('\n');
}
function authorShowStoryReview(review,chapterId){
 const payload=review?.structured||review?.payload||review||{};const issues=review?.findings||review?.issues||payload.findings||payload.issues||[];
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main';
 const stale=!!(review?.stale||review?.status==='stale');let preparing=false;
 const wrap=document.createElement('div');wrap.className='modal-backdrop';
 wrap.innerHTML=`<div class="modal analysis-modal"><h3>本章审稿</h3><p class="modal-help">${esc(chapter(chapterId)?.title||'')} ·
 ${stale?'当前稿已经变更，需要重新审稿':review?'已生成故事审稿报告':'尚未完成故事审稿'}</p>
 ${issues.map((item,index)=>{
  const evidence=item.evidence||[];const targets=item.repair_targets||[];const disabled=stale||item.stale||!targets.length;
  return `<section class="analysis-item" data-review-issue="${index}"><strong>${esc(item.category||item.kind||'审稿项')} · ${esc(item.title||item.message||'')}</strong>
 <p class="modal-help">${esc(item.description||item.message||'')}</p>${item.suggestion?`<p class="modal-help">修订建议：${esc(item.suggestion)}</p>`:''}
 ${evidence.map((source,evidenceIndex)=>`<div data-review-evidence="${evidenceIndex}"><small>${esc(chapter(source.source_chapter_id)?.title||'来源章节')} · 原句 ${evidenceIndex+1}</small>
 <blockquote>${esc(source.source_quote||source.quote||'')}</blockquote><button class="ghost-btn" data-review-source="${index}" data-review-evidence-index="${evidenceIndex}" ${disabled?'disabled':''}>定位原句 ${evidenceIndex+1}</button></div>`).join('')||'<p class="modal-help">缺少可定位原句，请重新故事审稿。</p>'}
 <div class="analysis-actions">${evidence.length>1?`<label>修订位置<select data-review-target="${index}" aria-label="修订位置" ${disabled?'disabled':''}>
 <option value="">选择要修订的原句</option>${targets.map(target=>`<option value="${target.evidence_index}">原句 ${target.evidence_index+1} · ${esc(target.chapter_title||'来源章节')}</option>`).join('')}</select></label>`:
 `<input type="hidden" data-review-target="${index}" value="${targets[0]?.evidence_index??''}">`}
 <button class="primary-btn" data-review-repair="${index}" ${disabled?'disabled':''}>生成局部修订候选</button></div>
 <div class="analysis-actions"><select data-review-choice="${index}" ${stale||item.stale||item.decision?'disabled':''}><option value="">待处理</option><option value="accepted" ${item.decision==='accepted'?'selected':''}>需要修订</option>
 <option value="ignored" ${item.decision==='ignored'?'selected':''}>忽略建议</option><option value="explained" ${item.decision==='explained'?'selected':''}>补充解释</option></select>
 <input data-review-reason="${index}" placeholder="处理理由" value="${esc(item.reason||'')}" maxlength="2000" ${stale||item.stale||item.decision?'readonly':''}></div>
 <p class="modal-help" data-review-decision-note="${index}" ${item.decision==='accepted'?'':'hidden'}>已记为需要修订，正文尚未因此修改；改稿后需要重新审稿。</p></section>`;
 }).join('')||'<p class="modal-help">尚无可处理的故事问题。</p>'}
 ${(review?.uncertainties||payload.uncertainties||[]).map(text=>`<p class="modal-help">${esc(text)}</p>`).join('')}
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" data-close>关闭</button><button class="ghost-btn" data-base-review>基础归档检查</button>
 <button class="primary-btn" data-semantic-review>${review?'重新故事审稿':'生成故事审稿'}</button></div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-semantic-review]').onclick=async()=>{
  try{
   if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已经切换，请重新打开本章审稿');
   activeChapter=chapterId;await authorQueueTask('semantic_review');wrap.remove();view='tasks';render();
  }
  catch(error){wrap.querySelector('.form-error').textContent=error.message}
 };
 wrap.querySelector('[data-base-review]').onclick=async()=>{
  try{
   if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已经切换，请重新打开本章审稿');
   const result=await durableOperation('/api/review','POST',{chapter_id:chapterId});wrap.remove();render();showReviewReport(result.report||result);
  }
  catch(error){wrap.querySelector('.form-error').textContent=error.message}
 };
 const prepare=async(index,evidenceIndex)=>{
  const current=await authorReviewCurrent(review,chapterId,owner,branch);
  const item=current.findings?.find(row=>row.id===(issues[index].id||issues[index].issue_id));
  const target=item?.repair_targets?.find(row=>row.evidence_index===evidenceIndex);
  if(!item||item.stale||!target)throw new Error('请明确选择这条问题中要修订的原句，无法确定时请重新审稿');
  const source=await authorReviewSourceCurrent(target.source,owner,branch);
  return {item,source,review_source:{review_id:current.id||current.review_id,issue_id:item.id,evidence_index:evidenceIndex}};
 };
 wrap.querySelectorAll('[data-review-source]').forEach(button=>button.onclick=async()=>{
  if(preparing)return;preparing=true;button.disabled=true;
  try{const ready=await prepare(Number(button.dataset.reviewSource),Number(button.dataset.reviewEvidenceIndex));authorReviewSelectSource(ready.source,wrap)}
  catch(error){wrap.querySelector('.form-error').textContent=error.message}
  finally{preparing=false;button.disabled=stale}
 });
 wrap.querySelectorAll('[data-review-repair]').forEach(button=>button.onclick=async()=>{
  if(preparing||aiBusy)return;const index=Number(button.dataset.reviewRepair);const chosen=wrap.querySelector(`[data-review-target="${index}"]`).value;
  if(chosen===''){wrap.querySelector('.form-error').textContent='这条问题有多处原句，请先选择要修订的位置';return}
  preparing=true;button.disabled=true;
  try{
   const ready=await prepare(index,Number(chosen));authorReviewSelectSource(ready.source,wrap);
   document.querySelector('#aiTask').value='polish';document.querySelector('#aiInstruction').value=authorReviewRepairInstruction(ready.item);
   await generateAI({source:ready.source,review_source:ready.review_source});
  }catch(error){wrap.querySelector('.form-error').textContent=error.message;toast(error.message)}
  finally{preparing=false;button.disabled=stale}
 });
 wrap.querySelectorAll('[data-review-choice]').forEach(select=>select.onchange=async()=>{
  const index=Number(select.dataset.reviewChoice);const item=issues[index];const reason=wrap.querySelector(`[data-review-reason="${index}"]`).value.trim();
  if(!select.value)return;
  if(!reason){wrap.querySelector('.form-error').textContent='请填写简短处理理由，再选择处理结果';select.value=item.decision||'';return}
  select.disabled=true;
  try{
   await authorReviewCurrent(review,chapterId,owner,branch);
   await durableOperation(`/api/story-reviews/${encodeURIComponent(review.id||review.review_id)}/issues/${encodeURIComponent(item.id||item.issue_id)}/decision`,
    'POST',{decision:select.value,reason});item.decision=select.value;item.reason=reason;wrap.querySelector('.form-error').textContent='';
   wrap.querySelector(`[data-review-reason="${index}"]`).readOnly=true;
   wrap.querySelector(`[data-review-decision-note="${index}"]`).hidden=select.value!=='accepted';
   toast(select.value==='accepted'?'已记为需要修订，正文仍需改稿并重新审稿':'审稿处理决定已保存');
  }catch(error){wrap.querySelector('.form-error').textContent=error.message;select.value=item.decision||''}finally{select.disabled=!!item.decision||stale}
 });
}

actions.finishChapter=async()=>{
 if(state.project.book_status==='complete')return toast('全书已完稿，请先在终稿检查中恢复写作');
 const c=chapter();if(!c||Array.from(c.body.replace(/\s/g,'')).length<60)return toast('先写出一小段完整正文，再归档');
 try{
  if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
  const pending=state.memory.filter(m=>m.source_chapter_id===c.id&&!['accepted','已确认','rejected','已拒绝','retracted','已撤回'].includes(m.status));
  if(pending.length){actions.chapterAnalysis();return toast('本章还有待决定的记忆，请先确认或拒绝')}
  if(!c.chapter_goal.trim()||!c.scene_plan.trim()||!c.pov.trim()){
   document.querySelector('.chapter-plan')?.setAttribute('open','');
   document.querySelector(!c.chapter_goal.trim()?'#chapterGoal':!c.scene_plan.trim()?'#chapterScenePlan':'#chapterPov')?.focus();
   throw new Error('请先填好本章目标、场景计划和视角，再归档本章');
  }
  await durableOperation(`/api/chapters/${encodeURIComponent(c.id)}/commit`,'POST',{stage:'prewrite'});
  chapter(c.id).status='完成';save();if(!await syncBackend())throw new Error('本章状态尚未保存');
  await durableOperation(`/api/chapters/${encodeURIComponent(c.id)}/commit`,'POST',{stage:'precommit'});
  await durableOperation(`/api/chapters/${encodeURIComponent(c.id)}/commit`,'POST',{stage:'postcommit'});
  const next={id:crypto.randomUUID(),title:`第 ${state.chapters.length+1} 章`,body:'',status:'草稿',updated:Date.now(),
   chapter_goal:c.next_brief||'',next_brief:'',scene_plan:'',pov:c.pov||'',recap:'',hook:'',foreshadowing_op:''};
  state.chapters.push(next);activeChapter=next.id;storage.set(cacheKey(':active'),next.id);save();
  if(!await syncBackend())throw new Error('交接已保存，但下一章尚未保存，请保留页面继续重试');
  view='editor';render();toast('本章已归档，下一章已接到交接任务');
 }catch(error){toast(`归档未完成：${error.message}`)}
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
  const override=gate.override;const headers=override?.reason?{'X-Moyu-Export-Override':override.reason}:{};
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
 if(view==='assistant')authorBindAssistant();
 if(view==='editor'){
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
  document.querySelectorAll('[data-format]').forEach(button=>{
   button.onmousedown=event=>event.preventDefault();button.onclick=()=>authorFormat(button.dataset.format);
  });
  document.querySelector('[data-editor-undo]')?.addEventListener('click',()=>authorUndo());
  document.querySelector('[data-editor-redo]')?.addEventListener('click',()=>authorUndo(true));
  document.querySelector('#aiRevise')?.addEventListener('click',reviseAI);
  document.querySelector('#aiOriginal')?.addEventListener('click',showOriginalCandidate);
  authorUpdateUndoControls();
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
 authorOriginalRender();authorBind();
 if(['library','blueprint','settings','backups','references'].includes(view))authorLoadPage();
};
document.querySelectorAll('.nav-item').forEach(button=>button.onclick=async()=>{
 if(authorBlueprintDirty&&!await authorSaveBlueprint())return;
 view=button.dataset.view;render();
});
render();
scheduleQueue(0);
setInterval(()=>{if(!backendOnline&&!syncInFlight)reconnect()},5000);
window.addEventListener('online',()=>reconnect());
window.addEventListener('beforeunload',event=>{cacheDraft();authorCacheBlueprint();if(isDirty()||authorBlueprintDirty||authorBlueprintLocked()){event.preventDefault();event.returnValue=''}});
