'use strict';

const writingInboxKinds={all:'全部',agent:'写作总管',queue:'生成结果',analysis:'记忆确认',review:'故事审稿'};
let writingInboxKind='all';
let writingInboxOffset=0;
let writingInboxSequence=0;
let writingInboxSummarySequence=0;
let writingInboxTimer=null;
let writingInboxSummaryTimer=null;
let writingInboxFocusedAgent=null;

function writingInboxScope(){
 return {project:requestedProject||state.project?.id||'default-project',
  branch:state.branch_id||state.project?.branch_id||'main'};
}
function writingInboxScopeCurrent(scope){
 const current=writingInboxScope();return current.project===scope.project&&current.branch===scope.branch;
}
function writingInboxHeaders(scope){return {'X-Moyu-Project':scope.project,'X-Moyu-Branch':scope.branch}}
function writingInboxAgentQuery(scope){
 if(view!=='assistant'||!writingInboxFocusedAgent)return '';
 const focus=writingInboxFocusedAgent,current=writingInboxScope();
 if(focus.project!==current.project||focus.branch!==current.branch||scope.owner!==requestedProject||scope.branch!==current.branch)return '';
 return '?session_id='+encodeURIComponent(focus.id);
}
function writingInboxCount(data){
 if(!Number.isInteger(data.total_all)||data.total_all<0||!Number.isInteger(data.total)||data.total<0||
  !Array.isArray(data.items)||!['agent','queue','analysis','review'].every(key=>
   Number.isInteger(data.counts?.[key])&&data.counts[key]>=0)||
  Object.values(data.counts).reduce((sum,value)=>sum+value,0)!==data.total_all)
  throw new Error('待办记录格式不完整，请重新读取');
 return data.total_all;
}
function showWritingInboxCount(value,error=false){
 const label=document.querySelector('#writingInboxCount'),button=document.querySelector('#writingInboxBtn');
 if(label)label.textContent=value===null?'…':String(value);
 if(button){
  button.toggleAttribute('data-unavailable',error);
  button.title=error?'全书待办暂未读到':value===null?'正在读取全书待办':`全书待办 · ${value} 项`;
  button.setAttribute('aria-label',button.title);
  button.querySelector('small').textContent=value===null?'…':value>99?'99+':String(value);
 }
}
async function refreshWritingInboxCount(){
 clearTimeout(writingInboxSummaryTimer);
 if(view==='inbox')return;
 const scope=writingInboxScope(),sequence=++writingInboxSummarySequence;
 if(!backendOnline){showWritingInboxCount(null,true);return}
 try{
  const data=await authorJSON('/api/writing-inbox?limit=1',{headers:writingInboxHeaders(scope)});
  if(sequence!==writingInboxSummarySequence||!writingInboxScopeCurrent(scope))return;
  if(data.project_id!==scope.project||data.branch_id!==scope.branch)throw new Error('作品范围不一致，请重新读取');
  showWritingInboxCount(writingInboxCount(data));
 }catch(error){
  if(sequence===writingInboxSummarySequence&&writingInboxScopeCurrent(scope))showWritingInboxCount(null,true);
 }
}
function scheduleWritingInboxCount(){
 clearTimeout(writingInboxSummaryTimer);
 writingInboxSummaryTimer=setTimeout(refreshWritingInboxCount,300);
}
views.inbox=()=>`${authorPageHead('全书待办','<button type="button" class="ghost-btn" data-inbox-refresh>刷新</button>')}
 <div class="inbox-summary"><p>等待你处理的计划、候选、记忆与审稿</p><span id="writingInboxTotal" role="status">正在读取…</span></div>
 <div class="inbox-filters" role="group" aria-label="待办分类">${Object.entries(writingInboxKinds).map(([key,label])=>
  `<button type="button" data-inbox-kind="${key}" aria-pressed="${writingInboxKind===key}">${label}<span data-inbox-kind-count="${key}">…</span></button>`).join('')}</div>
 <p class="inbox-page-state" id="writingInboxPageState" aria-live="polite"></p>
 <div id="writingInboxItems" aria-live="polite"><p class="empty">正在核对全书待办…</p></div>
 <div id="writingInboxPagination" class="inbox-pagination"></div>`;

function bindWritingInboxPage(){
 document.querySelector('[data-inbox-refresh]')?.addEventListener('click',()=>loadWritingInbox());
 document.querySelectorAll('[data-inbox-kind]').forEach(button=>button.onclick=()=>{
  writingInboxKind=button.dataset.inboxKind;writingInboxOffset=0;
  document.querySelectorAll('[data-inbox-kind]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  loadWritingInbox();
 });
 loadWritingInbox();
}
async function loadWritingInbox(){
 const host=document.querySelector('#writingInboxItems');if(view!=='inbox'||!host)return;
 clearTimeout(writingInboxSummaryTimer);++writingInboxSummarySequence;
 const scope=writingInboxScope(),sequence=++writingInboxSequence,kind=writingInboxKind,offset=writingInboxOffset;
 const current=()=>sequence===writingInboxSequence&&view==='inbox'&&writingInboxScopeCurrent(scope)&&
  host===document.querySelector('#writingInboxItems');
 const stateHost=document.querySelector('#writingInboxPageState'),pagination=document.querySelector('#writingInboxPagination');
 host.innerHTML='<p class="empty" role="status">正在核对全书待办…</p>';pagination.innerHTML='';stateHost.textContent='';
 try{
  if(!backendOnline)throw new Error('本机连接尚未恢复');
  const data=await authorJSON('/api/writing-inbox?'+new URLSearchParams({kind,limit:'25',offset:String(offset)}),
   {headers:writingInboxHeaders(scope)});
  if(!current())return;
  const count=writingInboxCount(data);
  if(data.project_id!==scope.project||data.branch_id!==scope.branch)throw new Error('作品范围不一致，请重新读取');
  if(offset>=data.total&&offset>0){
   writingInboxOffset=Math.max(0,Math.ceil(data.total/25)-1)*25;return loadWritingInbox();
  }
  showWritingInboxCount(count);
  document.querySelector('#writingInboxTotal').textContent=`共 ${count} 项`;
  for(const [key] of Object.entries(writingInboxKinds)){
   document.querySelector(`[data-inbox-kind-count="${key}"]`).textContent=String(key==='all'?count:data.counts[key]);
  }
  stateHost.textContent=isDirty()?'当前有未保存改动，以下待办按已保存稿件核对。':'';
  host.innerHTML=data.items.map((row,index)=>`<article class="inbox-row" data-inbox-row="${esc(row.id)}">
   <div><p class="inbox-chapter">${esc(writingInboxKinds[row.kind]||'待办')} · ${esc(row.chapter_title||'全书规划')}</p>
   <h3>${esc(row.title||'待处理事项')}</h3><p>${esc(row.reason||row.status_label||'等待作者处理')}</p>
   ${row.stale?'<p class="check-bad">依据有变化，处理前需要核对当前稿。</p>':''}
   <p class="form-error" data-inbox-item-error role="alert"></p></div>
   <button type="button" class="ghost-btn" data-inbox-open="${index}">${esc(({open_agent:'查看任务',open_job:'查看结果',
    open_analysis:'确认记忆',open_memory:'核对记忆',open_review:'查看审稿'})[row.action]||'查看详情')}</button></article>`).join('')||
   `<p class="empty">${kind==='all'?'目前没有等待作者处理的事项。':'这一类目前没有待处理事项。'}</p>`;
  host.querySelectorAll('[data-inbox-open]').forEach(button=>button.onclick=()=>
   openWritingInboxItem(data.items[Number(button.dataset.inboxOpen)],scope,button,current));
  pagination.innerHTML=`<span>${data.total?`${offset+1}–${offset+data.items.length} / ${data.total}`:'0 项'}</span>
   <button type="button" class="icon-btn" data-inbox-prev title="上一页" aria-label="上一页" ${offset?'':'disabled'}>←</button>
   <button type="button" class="icon-btn" data-inbox-next title="下一页" aria-label="下一页" ${data.has_more?'':'disabled'}>→</button>`;
  pagination.querySelector('[data-inbox-prev]').onclick=()=>{writingInboxOffset=Math.max(0,offset-25);loadWritingInbox()};
  pagination.querySelector('[data-inbox-next]').onclick=()=>{writingInboxOffset=offset+25;loadWritingInbox()};
 }catch(error){
  if(!current())return;
  showWritingInboxCount(null,true);document.querySelector('#writingInboxTotal').textContent='尚未核实';
  document.querySelectorAll('[data-inbox-kind-count]').forEach(item=>item.textContent='…');
  host.innerHTML='<p class="form-error" role="alert"></p><p class="modal-help">没有读到待办，不能据此判断已全部处理。</p>';
  host.querySelector('.form-error').textContent=error.message;
 }
}
function writingInboxOpenChapter(chapterId){
 if(!chapter(chapterId))throw new Error('来源章节已不存在，请刷新全书待办');
 activeChapter=chapterId;storage.set(cacheKey(':active'),chapterId);view='editor';render();
}
async function openWritingInboxItem(row,scope,button,current){
 if(!current())return;
 button.disabled=true;
 const errorHost=button.closest('.inbox-row').querySelector('[data-inbox-item-error]');
 errorHost.textContent='';
 try{
  if(!await syncBackend())throw new Error('请先保存或合并当前稿件，再处理待办');
  if(!current())return;
  const headers=writingInboxHeaders(scope);
  if(row.action==='open_agent'){
   const data=await authorJSON('/api/writing-agent?session_id='+encodeURIComponent(row.ref_id),{headers});
   if(!current())return;
   if(!data.sessions?.some(item=>item.id===row.ref_id))throw new Error('这条写作任务已不存在');
   writingInboxFocusedAgent={...scope,id:row.ref_id};view='assistant';render();return;
  }
  if(row.action==='open_memory'){
   const base=MoyuSync.copy(confirmedState),remote=await readServerState(scope.project,scope.branch);
   if(!current())return;
   receiveState(remote,base);
   if(mergePending)throw new Error('当前稿件存在待合并的改动，请先完成合并');
   const id=row.memory_id||row.ref_id;
   if(!state.memory.some(item=>item.id===id))throw new Error('这条记忆已不存在，请刷新全书待办');
   view='memory';render();
   const card=[...document.querySelectorAll('[data-memory-evidence]')]
    .find(item=>item.dataset.memoryEvidence===id)?.closest('.memory-card');
   if(card){
    card.dataset.inboxMemoryFocus='';card.tabIndex=-1;
    card.scrollIntoView({block:'center'});card.focus({preventScroll:true});
   }
   return;
  }
  if(row.action==='open_job'){
   const job=await authorJSON('/api/ai/jobs/'+encodeURIComponent(row.ref_id),{headers});
   if(!current())return;
   if(job.status==='success'&&['continue','polish','scene','outline','brainstorm','summary','check'].includes(job.task)){
    const candidate=receiveCandidateJob(job);
    if(candidate?.source&&chapter(candidate.source.chapter_id)){
     rememberCandidateView(candidate);writingInboxOpenChapter(candidate.source.chapter_id);
     displayCandidate(candidate);authorRevealWorkbenchSection('workbenchCandidateSection');return;
    }
   }
   showWritingInboxJob(job,scope);return;
  }
  if(row.action==='open_analysis'||row.action==='open_review'){
   if(!chapter(row.chapter_id))throw new Error('来源章节已不存在，请刷新全书待办');
   const analysis=row.action==='open_analysis';
   const data=await authorJSON((analysis?'/api/chapter-analysis':'/api/story-reviews')+
    '?chapter_id='+encodeURIComponent(row.chapter_id),{headers});
   if(!current())return;
   const items=analysis?data.analyses:authorReviewRows(data);
   const id=analysis?(row.analysis_id||row.ref_id):row.review_id;
   const report=id?items?.find(item=>item.id===id):items?.[0];
   if(id&&!report)throw new Error('这份记录已不存在，请刷新待办');
   writingInboxOpenChapter(row.chapter_id);
   const reportScope=authorChapterReportScope(row.chapter_id);
   if(analysis)authorShowAnalysis(report,row.chapter_id,reportScope);
   else authorShowStoryReview(report,row.chapter_id,reportScope);
   return;
  }
  throw new Error('这类记录还没有可用的处理入口');
 }catch(error){if(current())errorHost.textContent=error.message}
 finally{if(button.isConnected)button.disabled=false}
}
function showWritingInboxJob(job,scope){
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.inboxJobDialog='';
 const text=String(job.result?.text||job.result?.output||job.text||'');
 const status=job.execution_status==='unknown'||job.result?.completion_state==='unknown'?'unknown':job.status;
 wrap.innerHTML=`<section class="modal candidate-modal" role="dialog" aria-modal="true" aria-labelledby="inboxJobTitle">
  <h3 id="inboxJobTitle">生成记录 · ${esc(AI_TASK_NAMES[job.task]||'写作任务')}</h3>
  <p>${esc(taskStatus(status))}</p>${job.error?`<p class="form-error">${esc(job.error)}</p>`:''}
  ${['unknown','running','interrupted'].includes(status)?'<p class="check-bad">执行结果尚未确定。保留这条记录，避免重复生成。</p>':''}
  ${text?`<pre class="prompt-preview">${esc(text)}</pre>`:'<p class="modal-help">没有可采纳的完整正文候选。</p>'}
  <div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button>
  ${chapter(job.chapter_id)?'<button type="button" class="ghost-btn" data-inbox-job-chapter>打开章节</button>':''}</div></section>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-inbox-job-chapter]')?.addEventListener('click',()=>{
  if(!writingInboxScopeCurrent(scope))return;
  wrap.remove();writingInboxOpenChapter(job.chapter_id);
 });
}
function installWritingInboxButton(){
 if(document.querySelector('#writingInboxBtn'))return;
 const button=document.createElement('button');button.type='button';button.id='writingInboxBtn';button.className='icon-btn inbox-top-button';
 button.innerHTML='☷<small>…</small>';button.title='全书待办';button.setAttribute('aria-label','全书待办');
 button.onclick=()=>{writingInboxOffset=0;view='inbox';render()};
 document.querySelector('.top-actions')?.prepend(button);
}
function renderWritingInboxFocus(){
 const focus=writingInboxFocusedAgent;if(view!=='assistant'||!focus||!writingInboxScopeCurrent(focus))return;
 const note=document.createElement('div');note.className='inbox-focus-note';
 note.innerHTML='<span>来自全书待办的写作任务</span><button type="button" class="ghost-btn">查看全部任务</button>';
 note.querySelector('button').onclick=()=>{writingInboxFocusedAgent=null;render()};
 document.querySelector('#writingAgent')?.before(note);
}

const writingInboxOriginalRender=render;
render=function(){
 ++writingInboxSequence;
 document.querySelectorAll('[data-inbox-job-dialog]').forEach(dialog=>dialog.remove());
 writingInboxOriginalRender();installWritingInboxButton();
 if(view==='inbox')bindWritingInboxPage();
 else{renderWritingInboxFocus();scheduleWritingInboxCount()}
};
const writingInboxNav=document.querySelector('[data-view="inbox"]');
if(writingInboxNav){
 writingInboxNav.title='全书待办';writingInboxNav.setAttribute('aria-label','全书待办');
 writingInboxNav.onclick=async()=>{
  if(authorBlueprintDirty&&!await authorSaveBlueprint())return;
  writingInboxOffset=0;view='inbox';render();
 };
}
installWritingInboxButton();scheduleWritingInboxCount();
writingInboxTimer=setInterval(()=>{
 if(document.hidden)return;
 if(view==='inbox')loadWritingInbox();else refreshWritingInboxCount();
},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleWritingInboxCount()});
