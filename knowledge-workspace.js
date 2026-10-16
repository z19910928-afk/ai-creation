'use strict';

let recordKnowledgeDialog=null;
let recordKnowledgeEpoch=0;

function recordKnowledgeScope(){
 return {project:requestedProject,branch:state.branch_id||state.project?.branch_id||'main',
  chapter:activeChapter||'',page:view,host:document.querySelector('#main')?.firstElementChild};
}
function recordKnowledgeCurrent(dialog){
 const scope=dialog.scope;
 return recordKnowledgeDialog===dialog&&dialog.wrap.isConnected&&scope.project===requestedProject&&
  scope.branch===(state.branch_id||state.project?.branch_id||'main')&&scope.chapter===(activeChapter||'')&&
  scope.page===view&&scope.host===document.querySelector('#main')?.firstElementChild;
}
function closeRecordKnowledge(restoreFocus=true){
 const dialog=recordKnowledgeDialog;recordKnowledgeDialog=null;recordKnowledgeEpoch++;
 if(!dialog)return;
 dialog.abort?.abort();dialog.wrap.remove();
 if(restoreFocus&&dialog.invoker?.isConnected)dialog.invoker.focus();
}
function bindRecordKnowledgeLinks(host=document){
 host.querySelectorAll('[data-record-knowledge]').forEach(button=>{
  button.onclick=()=>showRecordKnowledge(button.dataset.recordKnowledge);
 });
}
function recordKnowledgeMessages(value){
 return [...new Set([...(value?.reason_messages||[]),...(value?.warning_messages||[])])];
}
function recordKnowledgeAvailability(value){
 return `<div class="knowledge-availability ${value?.allowed?'':'is-restricted'}">
  <strong>${esc(value?.status_label||'范围尚未核实')}</strong>
  ${recordKnowledgeMessages(value).map(message=>`<p>${esc(message)}</p>`).join('')}</div>`;
}
function recordKnowledgeEvidenceEntries(evidence){
 return evidence?.entries?.length?evidence.entries:[evidence||{}];
}
function recordKnowledgeCanJump(evidence){
 return evidence?.status==='valid'&&safeId(evidence.source_chapter_id)&&safeId(evidence.source_revision_id)&&
  typeof evidence.source_quote==='string'&&evidence.source_quote.length>0&&
  Number.isInteger(evidence.source_start)&&Number.isInteger(evidence.source_end)&&evidence.source_start>=0&&
  evidence.source_end>evidence.source_start&&
  Array.from(evidence.source_quote).length===evidence.source_end-evidence.source_start;
}
function recordKnowledgeEvidenceMarkup(evidence,rowIndex){
 return recordKnowledgeEvidenceEntries(evidence).map((entry,index)=>`
  <div class="knowledge-source-entry"><small>${esc(entry.source_chapter_title||'作者资料')} ·
  ${esc(entry.status_label||evidence?.status_label||'来源尚未核实')}</small>
  ${entry.source_quote?`<blockquote>${esc(entry.source_quote)}</blockquote>`:''}
  ${(entry.reason_messages||[]).map(message=>`<p class="check-bad">${esc(message)}</p>`).join('')}
  ${recordKnowledgeCanJump(entry)?`<button type="button" class="ghost-btn" data-knowledge-source="${rowIndex}" data-source-index="${index}">定位原句</button>`:''}</div>`).join('');
}
function recordKnowledgeRows(data,tab){
 return Array.isArray(data?.[tab])?data[tab]:[];
}
function renderRecordKnowledge(dialog,data){
 if(!recordKnowledgeCurrent(dialog))return;
 dialog.data=data;const content=dialog.wrap.querySelector('[data-knowledge-content]'),record=data.record||{};
 dialog.wrap.querySelector('[data-knowledge-name]').textContent=record.name||'资料关联';
 dialog.wrap.querySelector('[data-knowledge-type]').textContent=[record.type,...(record.aliases||[])].filter(Boolean).join(' · ');
 const rows=recordKnowledgeRows(data,dialog.tab),page=data.pagination?.[dialog.tab]||{};
 const labels={outgoing:'关联了谁',incoming:'谁关联了它',sources:'原文来源'};
 content.innerHTML=`${record.description?`<p class="knowledge-description">${esc(record.description)}</p>`:''}
  ${recordKnowledgeAvailability(data.availability)}
  <p class="modal-help">${esc(data.scope?.chapter_title||'尚未选择章节')}${data.scope?.chapter_title?' · 按已保存稿件核对可用范围':''}
  ${isDirty()?' · 当前还有未保存改动':''}</p>
  <div class="knowledge-tabs" role="tablist" aria-label="资料关联类型">${Object.entries(labels).map(([key,label])=>
   `<button type="button" role="tab" id="knowledge-tab-${key}" aria-selected="${dialog.tab===key}" aria-controls="recordKnowledgeRows" data-knowledge-tab="${key}">${label} ${Number(data.summary?.[key]||0)}</button>`).join('')}</div>
  <div id="recordKnowledgeRows" role="tabpanel" aria-labelledby="knowledge-tab-${dialog.tab}">
  ${rows.map((row,index)=>{
   const related=row.other_record;
   return `<article class="knowledge-row" data-knowledge-row="${index}"><h4>${related?
    `<button type="button" class="knowledge-record-link" data-knowledge-open="${safeId(related.id)}" ${related.status==='missing'?'disabled':''}>${esc(related.type||'设定')} · ${esc(related.name)}</button> <span>${esc(row.relation_type||'相关')}</span>`:
    `${esc(row.kind_label||'来源')} · ${esc(row.name||'未命名')}`}</h4>
    ${row.note?`<p>${esc(row.note)}</p>`:''}
    <small>${esc(row.status_label||(row.epistemic_mode==='author_plan'?'作者计划':'关系资料'))} · ${esc(row.availability?.status_label||'范围尚未核实')}</small>
    ${recordKnowledgeMessages(row.availability).map(message=>`<p class="modal-help">${esc(message)}</p>`).join('')}
    ${recordKnowledgeEvidenceMarkup(row.evidence,index)}</article>`;
  }).join('')||'<p class="empty">此项还没有记录。</p>'}</div>
  <div class="knowledge-pagination"><span>${Number(page.total||0)?`${dialog.offsets[dialog.tab]+1}–${dialog.offsets[dialog.tab]+rows.length} / ${Number(page.total)}`:'0 条'}</span>
  <button type="button" class="icon-btn" data-knowledge-prev title="上一页" aria-label="上一页" ${dialog.offsets[dialog.tab]?'':'disabled'}>←</button>
  <button type="button" class="icon-btn" data-knowledge-next title="下一页" aria-label="下一页" ${page.has_more?'':'disabled'}>→</button></div>`;
 content.querySelectorAll('[data-knowledge-tab]').forEach(button=>button.onclick=()=>{
  dialog.tab=button.dataset.knowledgeTab;loadRecordKnowledge(dialog);
 });
 content.querySelectorAll('[data-knowledge-open]').forEach(button=>button.onclick=()=>{
  if(recordKnowledgeCurrent(dialog))showRecordKnowledge(button.dataset.knowledgeOpen);
 });
 content.querySelector('[data-knowledge-prev]').onclick=()=>{
  dialog.offsets[dialog.tab]=Math.max(0,dialog.offsets[dialog.tab]-25);loadRecordKnowledge(dialog);
 };
 content.querySelector('[data-knowledge-next]').onclick=()=>{
  dialog.offsets[dialog.tab]+=25;loadRecordKnowledge(dialog);
 };
 content.querySelectorAll('[data-knowledge-source]').forEach(button=>button.onclick=()=>
  jumpRecordKnowledgeSource(dialog,Number(button.dataset.knowledgeSource),Number(button.dataset.sourceIndex),button));
 dialog.wrap.querySelector('[data-knowledge-edit]').disabled=false;
}
async function requestRecordKnowledge(dialog){
 const query=new URLSearchParams({limit:'25',offset:String(dialog.offsets[dialog.tab])});
 if(dialog.scope.chapter)query.set('chapter_id',dialog.scope.chapter);
 return authorJSON(`/api/records/${encodeURIComponent(dialog.id)}/knowledge?${query}`,{
  signal:dialog.abort.signal,headers:{'X-Moyu-Project':dialog.scope.project,'X-Moyu-Branch':dialog.scope.branch}});
}
async function loadRecordKnowledge(dialog){
 if(!recordKnowledgeCurrent(dialog))return;
 dialog.abort?.abort();dialog.abort=new AbortController();const sequence=++dialog.sequence;
 dialog.wrap.querySelector('[data-knowledge-content]').innerHTML='<p class="empty" role="status">正在读取关联与原文…</p>';
 dialog.wrap.querySelector('[data-knowledge-error]').textContent='';
 try{
  const data=await requestRecordKnowledge(dialog);
  if(sequence!==dialog.sequence||!recordKnowledgeCurrent(dialog))return;
  const page=data.pagination?.[dialog.tab],offset=dialog.offsets[dialog.tab];
  if(Number.isInteger(page?.total)&&offset>0&&offset>=page.total){
   dialog.offsets[dialog.tab]=Math.max(0,Math.ceil(page.total/25)-1)*25;
   return loadRecordKnowledge(dialog);
  }
  renderRecordKnowledge(dialog,data);
 }catch(error){
  if(sequence!==dialog.sequence||!recordKnowledgeCurrent(dialog)||error.name==='AbortError')return;
  const content=dialog.wrap.querySelector('[data-knowledge-content]');
  content.innerHTML='<p class="form-error" role="alert"></p><button type="button" class="ghost-btn" data-knowledge-retry>重新读取</button>';
  content.querySelector('.form-error').textContent=`关联未读到，不能判断没有来源：${error.message}`;
  content.querySelector('[data-knowledge-retry]').onclick=()=>loadRecordKnowledge(dialog);
 }
}
async function jumpRecordKnowledgeSource(dialog,rowIndex,sourceIndex,button){
 if(!recordKnowledgeCurrent(dialog))return;
 const sequence=dialog.sequence,tab=dialog.tab,offset=dialog.offsets[tab];
 const current=()=>recordKnowledgeCurrent(dialog)&&sequence===dialog.sequence&&dialog.tab===tab&&
  dialog.offsets[tab]===offset&&button.isConnected;
 const displayed=recordKnowledgeRows(dialog.data,tab)[rowIndex];
 const oldEvidence=recordKnowledgeEvidenceEntries(displayed?.evidence)[sourceIndex];
 const report=message=>{if(current())dialog.wrap.querySelector('[data-knowledge-error]').textContent=message};
 button.disabled=true;
 try{
  if(!await syncBackend())throw new Error('请先保存或合并当前稿件，再定位原句。');
  if(!current())return;
  const fresh=await requestRecordKnowledge(dialog);
  if(!current())return;
  const row=recordKnowledgeRows(fresh,tab).find(item=>item.id===displayed?.id&&item.kind===displayed?.kind);
  const evidence=recordKnowledgeEvidenceEntries(row?.evidence)[sourceIndex];
  if(!recordKnowledgeCanJump(evidence)||JSON.stringify(evidence)!==JSON.stringify(oldEvidence))
   throw new Error('来源已经变化，请重新读取后核对。');
  const c=chapter(evidence.source_chapter_id);
  if(!c||c.revision_id!==evidence.source_revision_id||
   Array.from(c.body).slice(evidence.source_start,evidence.source_end).join('')!==evidence.source_quote)
   throw new Error('当前稿件与来源版本不同，请先重新读取稿件。');
  closeRecordKnowledge(false);authorJumpToEvidence(evidence);
 }catch(error){report(error.message)}
 finally{if(button.isConnected)button.disabled=false}
}
async function showRecordKnowledge(id){
 if(!safeId(id))return toast('资料编号不正确');
 if(!backendOnline)return toast('恢复本机连接后才能核对关联与来源');
 const invoker=document.activeElement;closeRecordKnowledge(false);
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.recordKnowledgeDialog='';
 wrap.innerHTML=`<section class="modal knowledge-dialog" role="dialog" aria-modal="true" aria-labelledby="recordKnowledgeTitle">
  <header class="knowledge-dialog-head"><div><h3 id="recordKnowledgeTitle" data-knowledge-name>资料关联</h3><p data-knowledge-type></p></div>
  <button type="button" class="icon-btn" data-knowledge-close title="关闭" aria-label="关闭资料关联">×</button></header>
  <div class="knowledge-dialog-content" data-knowledge-content></div>
  <footer class="knowledge-dialog-footer"><button type="button" class="ghost-btn" data-knowledge-edit disabled>编辑这张资料</button>
  <button type="button" class="ghost-btn" data-knowledge-refresh>刷新关联</button><p class="form-error" data-knowledge-error role="alert"></p></footer></section>`;
 const dialog={id,wrap,invoker,scope:recordKnowledgeScope(),epoch:recordKnowledgeEpoch,sequence:0,
  tab:'outgoing',offsets:{outgoing:0,incoming:0,sources:0},abort:new AbortController()};
 recordKnowledgeDialog=dialog;document.body.append(wrap);
 wrap.querySelector('[data-knowledge-close]').onclick=()=>closeRecordKnowledge();
 wrap.querySelector('[data-knowledge-refresh]').onclick=()=>loadRecordKnowledge(dialog);
 wrap.querySelector('[data-knowledge-edit]').onclick=()=>{
  if(!recordKnowledgeCurrent(dialog))return;
  closeRecordKnowledge(false);editRecord(id);
 };
 wrap.addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();closeRecordKnowledge();return}
  if(event.key!=='Tab')return;
  const focusable=[...wrap.querySelectorAll('button:not(:disabled),[href],input,select,textarea,[tabindex="0"]')]
   .filter(node=>node.getClientRects().length);
  const first=focusable[0],last=focusable.at(-1);
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
 });
 wrap.querySelector('[data-knowledge-close]').focus();await loadRecordKnowledge(dialog);
}

const recordKnowledgeOriginalRender=render;
render=function(){
 closeRecordKnowledge(false);recordKnowledgeOriginalRender();bindRecordKnowledgeLinks();
};
bindRecordKnowledgeLinks();
