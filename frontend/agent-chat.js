'use strict';

/*
 * Selected Lucide v1.8.0 icon nodes, ISC License.
 * Copyright (c) 2026 Lucide Icons and Contributors.
 * Permission to use, copy, modify, and/or distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notice and this permission notice appear in all copies.
 * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 * WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 * ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 * WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
 * ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
 * OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 *
 * Plus, X and Square derive from Feather, MIT License.
 * Copyright (c) 2013-present Cole Bemis.
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, and to permit persons to whom the Software is furnished to do
 * so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
(() => {
 const host=document.querySelector('#agentChat');if(!host)return;
 const icons={
  plus:'<path d="M5 12h14"/><path d="M12 5v14"/>',
  close:'<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  stop:'<rect width="18" height="18" x="3" y="3" rx="2"/>',
  history:'<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
  settings:'<path d="M14 17H5"/><path d="M19 7h-9"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
  quote:'<path d="M17 5H3"/><path d="M21 12H8"/><path d="M21 19H8"/><path d="M3 12v7"/>',
  chat:'<path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"/>',
  send:'<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/>'
 };
 const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.chat}</svg>`;
 const button=(name,label,attr='')=>`<button type="button" class="agent-chat-icon" title="${label}" aria-label="${label}" ${attr}>${icon(name)}</button>`;
 host.innerHTML=`<header class="agent-chat-head"><div class="agent-chat-heading">${icon('chat')}<div><strong>写作总管</strong><small id="agentChatTitle">新对话</small></div></div>
  <div class="agent-chat-head-actions">${button('plus','新对话','data-chat-new')}${button('history','历史对话','data-chat-history')}${button('settings','写作总管连接设置','data-chat-settings')}</div></header>
  <div class="agent-chat-scope"><span id="agentChatProject"></span><span class="agent-chat-branch" id="agentChatBranch"></span></div>
  <div class="agent-chat-connection" role="status" hidden><span>写作总管还没有连接模型</span><button type="button" data-chat-configure>连接模型</button></div>
  <div id="agentChatContextSummary" class="agent-chat-context-summary" aria-label="上一轮参考范围" hidden></div>
  <nav class="agent-chat-results" aria-label="当前章节结果" hidden><button type="button" data-chat-result="workbenchAgentSection">任务进度</button><button type="button" data-chat-result="workbenchCandidateSection">正文候选</button><button type="button" data-chat-result="workbenchContextSection">本章依据</button></nav>
  <div id="agentChatFeed" class="agent-chat-feed" role="log" aria-label="创作对话" aria-live="polite" aria-relevant="additions text"></div>
  <section class="agent-chat-history" aria-label="历史对话" hidden><div class="agent-chat-history-head"><strong>历史对话</strong>${button('close','关闭历史对话','data-chat-history-close')}</div><div class="agent-chat-history-list"></div></section>
  <form id="agentChatForm" class="agent-chat-composer"><div class="agent-chat-materials"><span>参考范围</span><strong>作品资料</strong><span id="agentChatReferenceCount"></span></div><div class="agent-chat-context"><label title="可额外带入当前章节正文"><input id="agentChatIncludeChapter" type="checkbox" checked><span id="agentChatChapter">当前章节</span></label><button type="button" data-chat-quote title="引用所选正文">${icon('quote')}引用选区</button></div>
  <div id="agentChatReferences" class="agent-chat-references"></div><p id="agentChatNotice" class="agent-chat-notice" role="status"></p>
  <textarea id="agentChatInput" aria-label="给写作总管的消息" maxlength="6000" placeholder="想写什么，或哪里需要调整？" rows="3"></textarea>
  <div class="agent-chat-send-row"><span id="agentChatSaveState" class="agent-chat-save-state">正在连接本机…</span><button class="agent-chat-send" id="agentChatSend" type="submit" title="发送消息" aria-label="发送消息">${icon('send')}</button></div></form>`;
 const $=selector=>host.querySelector(selector),feed=$('#agentChatFeed'),input=$('#agentChatInput');
 let observed='',observedScope=null,epoch=0,sessionEpoch=0,session=null,sessions=[],messages=[],activeRun=null,settings=null,contextSummary=null;
 let timer=null,loading=false,sendOperation=null,actionOperation=null,creating=false,polling=false,bundleRevision=0;
 let references=[],draftRevision=0,pendingSend=null,pendingCreation=null,historyOpen=false,messageSignature='',notice='',loadError='';
 const writingTasks=new Map(),actionErrors=new Map();
 const starters=[
  {id:'idea',label:'构思灵感',prompt:'我想构思一个故事。请结合已有作品资料，先给我三个有区别的故事方向，分别说清主角、核心冲突和独特看点，再和我一起打磨。'},
  {id:'character',label:'打磨人物',prompt:'我想把人物写得更鲜活。请结合已有设定，和我讨论人物的欲望、弱点、秘密和改变；资料没有明确的地方先问我。'},
  {id:'plot',label:'讨论情节',prompt:'我想讨论接下来的情节。请根据作品资料给我三个不同走向，比较人物动机、冲突和代价，先讨论，不直接改正文。'}
 ];
 const writingLabels={awaiting_authorization:'等待启动',awaiting_job:'正在执行',awaiting_adoption:'正文候选待采纳',
  awaiting_memory:'等待章后确认',awaiting_plan_confirmation:'本阶段候选待确认',awaiting_plan_application:'完整规划待采用',
  requires_action:'需要处理',review_ready:'记忆与审稿已就绪',canceled:'已停止',restored:'历史记录',unknown:'任务结果尚未确认'};
 const terminal=new Set(['success','failed','canceled','cancelled','interrupted','unknown','complete','completed']);
 const running=()=>activeRun&&!terminal.has(activeRun.status);
 const scope=()=>({project:requestedProject||state.project?.id||'',branch:state.branch_id||state.project?.branch_id||'main'});
 const scopeKey=(value=scope())=>JSON.stringify([value.project,value.branch]);
 const capture=()=>({...scope(),epoch,sessionEpoch,bundleRevision,sessionId:session?.id||null});
 const current=value=>value.epoch===epoch&&value.sessionEpoch===sessionEpoch&&scopeKey(value)===observed&&scopeKey(value)===scopeKey()&&
  value.sessionId===(session?.id||null);
 const allActions=()=>messages.flatMap(message=>message.context_stale?[]:message.actions||[]);
 const boundAction=action=>allActions().find(item=>item.id===action.id&&item.kind===action.kind&&
  item.chapter_id===action.chapter_id&&item.instruction===action.instruction&&item.result?.writing_session_id===action.result?.writing_session_id);
 const taskCurrent=(action,captured,entry)=>current(captured)&&!!boundAction(action)&&writingTasks.get(action.result?.writing_session_id)===entry;
 const localKey=suffix=>{
  const saved=observedScope||scope();
  return `${KEY}:${saved.project||'default-project'}${suffix}`;
 };
 const draftKey=()=>localKey(`:agent-chat-draft:${(observedScope||scope()).branch}:${session?.id||'new'}`);
 const lastKey=()=>localKey(`:agent-chat-last:${(observedScope||scope()).branch}`);
 const publicError=error=>error?.message||'连接暂时没有回应，请稍后重试';
 function notify(message=''){notice=message;$('#agentChatNotice').textContent=message}
 function openSettings(){view='settings';render();document.querySelector('#agentSettingsHost')?.scrollIntoView()}
 function fillDraft(text){
  if(session?.restored_read_only||sendOperation||creating)return;
  const previous=input.value.trim(),addition=String(text||'').trim();
  if(!addition)return;
  const combined=previous?previous+'\n\n'+addition:addition;
  if(combined.length>input.maxLength){notify('输入草稿较长，请先整理后再添加。');return}
  input.value=combined;pendingSend=null;++draftRevision;rememberDraft();notify('');updateControls();input.focus();
  input.setSelectionRange(input.value.length,input.value.length);
 }
 function inlineMarkdown(text){
  // Only our own formatting tags enter HTML; model text is always escaped.
  return String(text).split(/(`[^`\n]+`)/g).map(part=>{
   if(part.startsWith('`')&&part.endsWith('`'))return '<code>'+esc(part.slice(1,-1))+'</code>';
   return esc(part).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/__([^_\n]+)__/g,'<strong>$1</strong>');
  }).join('');
 }
 function markdown(text){
  const lines=String(text||'').replace(/\r\n?/g,'\n').split('\n'),blocks=[];
  let paragraph=[],list=[],listTag='',quote=[],code=null;
  const flushParagraph=()=>{if(paragraph.length){blocks.push('<p>'+paragraph.map(inlineMarkdown).join('<br>')+'</p>');paragraph=[]}};
  const flushList=()=>{if(list.length){blocks.push('<'+listTag+'>'+list.map(line=>'<li>'+inlineMarkdown(line)+'</li>').join('')+'</'+listTag+'>');list=[];listTag=''}};
  const flushQuote=()=>{if(quote.length){blocks.push('<blockquote>'+quote.map(inlineMarkdown).join('<br>')+'</blockquote>');quote=[]}};
  const flush=()=>{flushParagraph();flushList();flushQuote()};
  for(const line of lines){
   if(code!==null){if(/^\s*```/.test(line)){blocks.push('<pre><code>'+esc(code.join('\n'))+'</code></pre>');code=null}else code.push(line);continue}
   if(/^\s*```/.test(line)){flush();code=[];continue}
   if(!line.trim()){flush();continue}
   const heading=line.match(/^#{1,6}\s+(.+)$/),item=line.match(/^\s*(?:[-+*]|\d+[.)])\s+(.+)$/),quoted=line.match(/^\s*>\s?(.*)$/);
   if(heading){flush();blocks.push('<h4>'+inlineMarkdown(heading[1])+'</h4>');continue}
   if(item){flushParagraph();flushQuote();const tag=/^\s*\d/.test(line)?'ol':'ul';if(listTag&&tag!==listTag)flushList();listTag=tag;list.push(item[1]);continue}
   if(quoted){flushParagraph();flushList();quote.push(quoted[1]);continue}
   flushList();flushQuote();paragraph.push(line);
  }
  flush();if(code!==null)blocks.push('<pre><code>'+esc(code.join('\n'))+'</code></pre>');
  return blocks.join('');
 }
 function messageComplete(message){
  return message.role==='assistant'&&!!String(message.content||'').trim()&&(message.response_recovered||!message.status||['success','complete','completed'].includes(message.status));
 }
 async function copyMessage(message){
  const captured=capture(),content=String(message?.content||'');if(!content)return;
  try{
   if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(content);
   else throw new Error('clipboard unavailable');
   if(current(captured))notify('回复已复制。');
  }catch{
   const previous=document.activeElement,temporary=document.createElement('textarea');
   temporary.value=content;temporary.className='agent-chat-copy-buffer';temporary.setAttribute('aria-label','待复制的回复');document.body.append(temporary);
   temporary.select();let copied=false;try{copied=document.execCommand('copy')}catch{}temporary.remove();previous?.focus();
   if(current(captured))notify(copied?'回复已复制。':'复制暂时不可用，请选中回复文字后复制。');
  }
 }
 function rememberDraft(){
  const value={content:input.value,references,include_chapter:$('#agentChatIncludeChapter').checked,pending:pendingSend,
   session_request_id:pendingCreation};
  const saved=storage.set(draftKey(),JSON.stringify(value));
  if(!saved)notify('浏览器未能保留输入草稿，请保持窗口打开。');
  return saved;
 }
 function restoreDraft(){
  let draft;try{draft=JSON.parse(storage.get(draftKey())||'null')}catch{}
  input.value=typeof draft?.content==='string'?draft.content:'';
  references=Array.isArray(draft?.references)?draft.references.slice(0,3):[];
  pendingSend=draft?.pending&&typeof draft.pending.request_id==='string'?draft.pending:null;
  pendingCreation=typeof draft?.session_request_id==='string'?draft.session_request_id:null;
  $('#agentChatIncludeChapter').checked=draft?.include_chapter!==false;
  ++draftRevision;renderReferences();
 }
 async function request(path,options={},captured=capture()){
  const response=await fetch('/api/agent-chat'+path,{...options,headers:{'Content-Type':'application/json',
   'X-Moyu-Project':captured.project,'X-Moyu-Branch':captured.branch,...options.headers}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||`连接未完成（${response.status}）`);
  return data;
 }
 function savedState(){
  if(loadError)return '对话尚未读取';
  if(!backendOnline)return '本机服务未连接';
  if(sendOperation||creating)return '正在保存消息…';
  if(running())return '已保存 · 总管正在处理';
  if(activeRun?.status==='unknown')return '本次回复结果尚未确认';
  if(pendingSend)return '发送结果尚未确认';
  if(session?.restored_read_only)return '备份中的历史对话';
  if(settings?.configured===false)return '请先连接写作总管模型';
  return session?'对话已保存在本机':'新对话';
 }
 function updateControls(){
  const active=!!running(),readOnly=!!session?.restored_read_only;
  const send=$('#agentChatSend');
  send.innerHTML=icon(active?'stop':'send');send.type=active?'button':'submit';
  send.dataset.stopping=String(active);send.title=active?'停止本次回复':'发送消息';send.setAttribute('aria-label',send.title);
  send.disabled=!backendOnline||!!sendOperation||!!actionOperation||creating||readOnly||(!active&&(settings?.configured===false||!input.value.trim()));
  input.disabled=readOnly;
  input.placeholder=readOnly?'这是恢复的历史对话，请新建对话继续。':'想写什么，或哪里需要调整？';
  $('#agentChatSaveState').textContent=savedState();
  $('.agent-chat-connection').hidden=settings?.configured!==false||!backendOnline;
  const summary=$('#agentChatContextSummary');
  summary.hidden=!contextSummary||!messages.length;
  if(contextSummary){
   const parts=[];
   if(contextSummary.project_materials_included)parts.push('作品资料');
   if(contextSummary.chapter_included)parts.push(contextSummary.chapter_title||'章节正文');
   if(Number(contextSummary.reference_count)>0)parts.push(contextSummary.reference_count+'处正文引用');
   summary.textContent='上一轮参考：'+(parts.length?parts.join(' · '):'对话内容');
  }
  $('#agentChatReferenceCount').textContent=references.length?references.length+'处引用':'';
  host.querySelectorAll('[data-chat-starter],[data-chat-followup]').forEach(element=>element.disabled=readOnly||!!sendOperation||creating);
  $('[data-chat-new]').disabled=creating||!!sendOperation;
  const c=chapter(),included=$('#agentChatIncludeChapter');
  included.disabled=!c||readOnly;
  $('#agentChatChapter').textContent=c?'当前章 · '+c.title:'当前没有章节';
  $('.agent-chat-context label').title=c?c.title:'当前没有章节';
  $('[data-chat-quote]').disabled=!validSelection()||readOnly||references.length>=3;
  document.querySelectorAll('[data-agent-chat-quote]').forEach(element=>element.disabled=!validSelection()||readOnly||references.length>=3);
 }
 function relativeDate(value){
  const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
 }
 function taskEntry(action){
  const id=action.result?.writing_session_id;if(!id)return null;
  if(!writingTasks.has(id))writingTasks.set(id,{row:null,verified:false,error:'',sequence:0,nextRead:0,pending:null,uncertain:false});
  return writingTasks.get(id);
 }
 function taskMatches(action,row){
  return row?.id===action.result?.writing_session_id&&row.chapter_id===action.chapter_id&&row.mode===action.kind&&
   (typeof action.instruction!=='string'||row.query===action.instruction.trim());
 }
 function taskDelay(row){
  if(session?.restored_read_only||row?.restored_read_only)return Infinity;
  if(row?.status==='awaiting_job')return 850;
  return ['awaiting_authorization','awaiting_adoption','awaiting_memory','awaiting_plan_confirmation','awaiting_plan_application']
   .includes(row?.status)?15000:Infinity;
 }
 function taskCanStart(entry,action){
  const row=entry?.row;
  return entry?.verified&&taskMatches(action,row)&&!!chapter(action.chapter_id)&&!entry.uncertain&&!session?.restored_read_only&&!row?.restored_read_only&&!row?.source_stale&&
   row?.status==='awaiting_authorization'&&typeof row.checkpoint_id==='string'&&!!row.checkpoint_id&&
   Number.isInteger(row.calls_used)&&Number.isInteger(row.max_calls)&&row.calls_used>=0&&row.max_calls>row.calls_used;
 }
 function taskMarkup(action){
  const entry=taskEntry(action);if(!entry)return '';
  const row=entry.row,readOnly=session?.restored_read_only||row?.restored_read_only,source=chapter(action.chapter_id);
  const verified=entry.verified&&taskMatches(action,row);
  const label=row?.mode==='plan'&&row.status==='review_ready'?'完整规划已采用':writingLabels[row?.status]||'任务状态尚未核实';
  return `<div class="agent-chat-task" data-chat-writing-session="${esc(action.result.writing_session_id)}" data-chat-task-status="${esc(verified?row.status:'unverified')}">
   <p class="agent-chat-task-source" data-chat-task-source>${source?'来源章 · '+esc(source.title):'来源章节已不存在'}</p>
   <p class="agent-chat-task-state">${esc(entry.uncertain?'启动结果待核对':verified?label:row?label+' · 状态尚未核实':'正在核对任务状态…')}</p>
   ${Number.isInteger(row?.calls_used)&&Number.isInteger(row?.max_calls)?`<p class="agent-chat-task-budget">模型调用 ${row.calls_used} / 上限 ${row.max_calls} 次</p>`:''}
   ${readOnly?'<small>历史只读，保留本次任务记录。</small>':!source?'<small>请在当前作品重新选择章节并讨论。</small>':
    row?.source_stale?'<small>任务依据已变化，请按当前内容重新讨论。</small>':
    taskCanStart(entry,action)?'<small>启动后按当前模型额度计费。</small>':''}
   ${row?.error?`<p class="form-error">${esc(row.error)}</p>`:''}
   ${entry.error?`<p class="form-error" data-chat-task-error>${esc(entry.error)}</p>`:''}</div>`;
 }
 function actionMarkup(action){
  const done=['confirmed','executed','completed','applied'].includes(action.status),dismissed=['dismissed','rejected','canceled'].includes(action.status);
  const confirming=action.status==='confirming',canConfirm=!done&&!dismissed&&!session?.restored_read_only;
  const entry=taskEntry(action),disabled=!!actionOperation||!!sendOperation||creating||!backendOnline;
  const inspiration=action.kind==='save_inspiration',candidateReady=entry?.verified&&entry.row?.status==='awaiting_adoption';
  const resultLabel=inspiration?'查看灵感':action.kind==='open'?'再次打开':candidateReady?'查看正文候选':entry?'查看本次结果':'查看结果';
  return `<section class="agent-chat-action" data-chat-action="${esc(action.id)}"><h4>${esc(action.label||'创作建议')}</h4>
   ${inspiration?`<p class="agent-chat-inspiration-title">${esc(action.title||'故事灵感')}</p>${action.content?`<details class="agent-chat-inspiration"><summary>查看灵感草稿</summary><div class="agent-chat-markdown">${markdown(action.content)}</div></details>`:''}`:action.instruction?`<p>${esc(action.instruction)}</p>`:''}<small>${dismissed?'已放弃':done?inspiration?'已保存为灵感草稿':'已确认，处理结果已保存':confirming?'确认结果待恢复，请恢复此步骤后查看进度':inspiration?'保存到设定库，供后续创作参考':action.kind==='open'?'查看当前作品内容':action.kind==='next_chapter'?'检查通过后归档本章，打开下一章；本步骤不生成正文':'确认后进入相应创作流程'}</small>
   ${taskMarkup(action)}<div class="agent-chat-action-buttons">${canConfirm?`<button type="button" class="primary-btn" data-chat-confirm ${disabled?'disabled':''}>${confirming?'恢复此步骤':inspiration?'保存为灵感':action.kind==='open'?'打开':'确认此步骤'}</button>${action.kind!=='open'&&!confirming?`<button type="button" class="ghost-btn" data-chat-dismiss ${disabled?'disabled':''}>放弃</button>`:''}`:''}
   ${done&&taskCanStart(entry,action)?`<button type="button" class="primary-btn" data-chat-start ${disabled?'disabled':''}>启动本次任务</button>`:''}
   ${done?`<button type="button" class="ghost-btn" data-chat-open-result ${disabled?'disabled':''}>${resultLabel}</button>`:''}
   ${entry?`<button type="button" class="ghost-btn" data-chat-task-refresh ${disabled||entry.pending?'disabled':''}>核对进度</button>`:''}</div><p class="form-error" data-chat-action-error role="alert">${esc(actionErrors.get(action.id)?.message||'')}</p></section>`;
 }
 function renderMessages(force=false){
  const signature=JSON.stringify([messages,activeRun?.id,activeRun?.status,activeRun?.error,session?.restored_read_only,loadError,loading,
   [...writingTasks].map(([id,entry])=>[id,entry.row,entry.verified,entry.error,entry.uncertain,!!entry.pending]),
   [...actionErrors],actionOperation?.actionId,!!sendOperation,!!creating,backendOnline,
   allActions().map(action=>[action.id,chapter(action.chapter_id)?.title])]);
  if(!force&&signature===messageSignature){updateControls();return}
  messageSignature=signature;
  const nearBottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<70,scroll=feed.scrollTop;
  feed.innerHTML=messages.length?messages.map(message=>{
   const role=message.role==='user'?'user':message.role==='tool'?'tool':'assistant';
   const label={user:'你',assistant:'写作总管',tool:'执行记录'}[role];
   return `<article class="agent-chat-message" data-chat-message="${esc(message.id)}" data-role="${role}"><div class="agent-chat-message-head"><strong>${label}</strong><time>${esc(relativeDate(message.created_at))}</time></div>
    <div class="agent-chat-message-body ${role==='assistant'?'agent-chat-markdown':''}">${role==='assistant'?markdown(message.content):esc(message.content||'')}</div>
    ${message.references?.length?`<div class="agent-chat-message-refs">${message.references.map(ref=>`<span>${esc(chapter(ref.chapter_id)?.title||'正文引用')} · ${Array.from(ref.range?.quote||'').length} 字</span>`).join('')}</div>`:''}
    ${message.context_stale?'<p class="agent-chat-message-state">回复依据是发送时的资料；资料已更新，本轮操作建议已取消。</p>':message.response_recovered?'<p class="agent-chat-message-state">已恢复为文字，未执行任何操作</p>':message.status&&['failed','canceled','cancelled','interrupted','unknown'].includes(message.status)?`<p class="agent-chat-message-state">${esc({failed:'这条回复未完成',canceled:'已停止本次回复',cancelled:'已停止本次回复',interrupted:'回复中断，请查看连接状态',unknown:'本次回复结果尚未确认，请先查看任务记录'}[message.status])}</p>`:''}
    ${(message.context_stale?[]:message.actions||[]).map(actionMarkup).join('')}${messageComplete(message)?`<div class="agent-chat-message-tools"><button type="button" data-chat-followup="${esc(message.id)}">继续追问</button><button type="button" data-chat-copy="${esc(message.id)}">复制回复</button></div>`:''}</article>`;
  }).join(''):`<div class="agent-chat-empty">${icon('chat')}<strong>${loadError?'对话暂时没有读取':loading?'正在读取对话…':'故事从一个念头开始'}</strong><p>${esc(loadError||'')}</p>${loadError?'<button type="button" data-chat-retry>重新连接</button>':loading?'':`<div class="agent-chat-starters">${starters.map(item=>`<button type="button" data-chat-starter="${item.id}">${item.label}</button>`).join('')}</div>`}</div>`;
  if(running())feed.insertAdjacentHTML('beforeend','<div class="agent-chat-running" role="status"><span></span><span>消息已保存，正在查阅资料并组织回复…</span></div>');
  if(activeRun?.error&&!messages.filter(message=>message.role==='assistant').at(-1)?.response_recovered)feed.insertAdjacentHTML('beforeend',`<p class="agent-chat-message-state">${esc(activeRun.error)}</p>`);
  feed.querySelector('[data-chat-retry]')?.addEventListener('click',()=>loadSessions(true));
  feed.querySelectorAll('[data-chat-starter]').forEach(element=>element.onclick=()=>fillDraft(starters.find(item=>item.id===element.dataset.chatStarter)?.prompt));
  feed.querySelectorAll('[data-chat-followup]').forEach(element=>element.onclick=()=>{
   const message=messages.find(item=>item.id===element.dataset.chatFollowup);if(!messageComplete(message||{}))return;
   const excerpt=Array.from(String(message.content).replace(/\s+/g,' ')).slice(0,120).join('');
   fillDraft('关于你提到的“'+excerpt+'”，我想继续讨论：');
  });
  feed.querySelectorAll('[data-chat-copy]').forEach(element=>element.onclick=()=>copyMessage(messages.find(item=>item.id===element.dataset.chatCopy)));
  feed.querySelectorAll('[data-chat-action]').forEach(element=>{
   const action=allActions().find(item=>item.id===element.dataset.chatAction);
   element.querySelector('[data-chat-confirm]')?.addEventListener('click',()=>performAction(action,'confirm'));
   element.querySelector('[data-chat-dismiss]')?.addEventListener('click',()=>performAction(action,'dismiss'));
   element.querySelector('[data-chat-open-result]')?.addEventListener('click',()=>performAction(action,'result'));
   element.querySelector('[data-chat-start]')?.addEventListener('click',()=>performAction(action,'start'));
   element.querySelector('[data-chat-task-refresh]')?.addEventListener('click',()=>readWritingTask(action,{force:true}));
  });
  if(nearBottom||force)feed.scrollTop=feed.scrollHeight;else feed.scrollTop=scroll;
  updateControls();
 }
 function applyBundle(data,captured){
  if(!current(captured)||captured.bundleRevision!==bundleRevision)return false;
  if(data.session&&(data.session.project_id!==captured.project||data.session.branch_id!==captured.branch))return false;
  if(data.session&&data.session.id!==captured.sessionId)return false;
  if(data.session){session=data.session;storage.set(lastKey(),session.id)}
  if(Array.isArray(data.messages))messages=data.messages;
  for(const action of allActions())if(action.status==='confirmed'&&actionErrors.get(action.id)?.kind==='confirm')actionErrors.delete(action.id);
  activeRun=data.active_run||null;settings=data.settings||settings;contextSummary=data.context_summary||null;loadError='';loading=false;
  $('#agentChatTitle').textContent=session?.title||'新对话';
  renderMessages();schedule();return true;
 }
 function schedule(delay){
  if(delay===undefined){
   delay=running()?850:3500;
   for(const action of allActions()){
    const entry=taskEntry(action);if(entry&&!entry.pending)delay=Math.min(delay,Math.max(100,entry.nextRead-Date.now()));
   }
  }
  clearTimeout(timer);timer=setTimeout(poll,delay);
 }
 async function poll(){
  if(polling||!session||!backendOnline||sendOperation||actionOperation)return schedule(850);
  const captured=capture(),previous=activeRun?.id;polling=true;
  try{
   const path=running()?'/runs/'+encodeURIComponent(activeRun.id):'/sessions/'+encodeURIComponent(session.id);
   const data=await request(path,{},captured);
   if(applyBundle(data,captured)&&previous&&!running())loadSessions(false);
   if(current(captured)&&captured.bundleRevision===bundleRevision&&!actionOperation){
    const due=new Map();
    for(const action of allActions()){
     const entry=taskEntry(action);
     if(entry&&!entry.pending&&entry.nextRead<=Date.now())due.set(action.result.writing_session_id,action);
    }
    await Promise.all([...due.values()].map(action=>readWritingTask(action,{captured})));
   }
  }catch(error){if(current(captured)&&captured.bundleRevision===bundleRevision){loadError=publicError(error);notify(loadError);updateControls()}}
  finally{polling=false;if(current(captured))schedule()}
 }
 async function loadSessions(selectLast=false){
  if(!backendOnline){loading=false;renderMessages();return}
  const captured=capture();
  try{
   const data=await request('/sessions',{},captured);if(!current(captured))return;
   sessions=Array.isArray(data.sessions)?data.sessions:[];settings=data.settings||settings;loadError='';
   renderHistory();
   if(selectLast&&!session){
    const wanted=storage.get(lastKey()),row=wanted==='__new__'?null:sessions.find(item=>item.id===wanted)||sessions[0];
    if(row)return selectSession(row.id);
   }
   loading=false;renderMessages();
  }catch(error){if(current(captured)){loadError=publicError(error);loading=false;renderMessages()}}
 }
 async function selectSession(id){
  rememberDraft();++sessionEpoch;clearTimeout(timer);session=sessions.find(item=>item.id===id)||{id};
  writingTasks.clear();actionErrors.clear();actionOperation=null;++bundleRevision;
  messages=[];activeRun=null;contextSummary=null;pendingSend=null;pendingCreation=null;loadError='';loading=true;notify('');historyOpen=false;
  $('.agent-chat-history').hidden=true;restoreDraft();renderMessages(true);
  const captured=capture();
  try{applyBundle(await request('/sessions/'+encodeURIComponent(id),{},captured),captured)}
  catch(error){if(current(captured)){loadError=publicError(error);loading=false;renderMessages()}}
 }
 async function newSession(){
  if(creating||sendOperation)return;
  rememberDraft();++sessionEpoch;clearTimeout(timer);session=null;messages=[];activeRun=null;contextSummary=null;pendingSend=null;pendingCreation=null;
  writingTasks.clear();actionErrors.clear();actionOperation=null;++bundleRevision;
  loading=false;loadError='';historyOpen=false;$('.agent-chat-history').hidden=true;notify('');
  references=[];input.value='';++draftRevision;renderReferences();renderMessages(true);
  storage.set(lastKey(),'__new__');rememberDraft();input.focus();
 }
 async function ensureSession(captured){
  if(session)return session;
  const operation={};creating=operation;
  pendingCreation=pendingCreation||crypto.randomUUID();rememberDraft();updateControls();
  try{
   const data=await request('/sessions',{method:'POST',body:JSON.stringify({request_id:pendingCreation})},captured);
   if(!current(captured))return null;
   const created=data.session||data;if(!created.id)throw new Error('本机未返回已保存的对话');
   const oldKey=draftKey();session=created;pendingCreation=null;captured.sessionId=session.id;storage.set(lastKey(),session.id);
   storage.remove(oldKey);rememberDraft();sessions=[session,...sessions.filter(row=>row.id!==session.id)];
   $('#agentChatTitle').textContent=session.title||'新对话';return session;
  }finally{if(creating===operation)creating=false;updateControls()}
 }
 function validSelection(){
  const c=chapter(),selected=savedSelection;
  return !!(view==='editor'&&c&&selected?.chapter_id===c.id&&selected.body===c.body&&selected.end>selected.start);
 }
 function renderReferences(){
  $('#agentChatReferences').innerHTML=references.map((ref,index)=>`<div class="agent-chat-reference">
   <button type="button" data-chat-reference="${index}" title="${esc(ref.range?.quote||'')}">${esc(chapter(ref.chapter_id)?.title||'正文引用')} · ${Array.from(ref.range?.quote||'').length} 字</button>
   ${button('close','移除引用',`data-chat-remove-reference="${index}"`)}</div>`).join('');
  $('#agentChatReferences').querySelectorAll('[data-chat-remove-reference]').forEach(element=>element.onclick=()=>{
   references.splice(Number(element.dataset.chatRemoveReference),1);pendingSend=null;++draftRevision;rememberDraft();renderReferences();updateControls();
  });
  $('#agentChatReferences').querySelectorAll('[data-chat-reference]').forEach(element=>element.onclick=()=>showReference(references[Number(element.dataset.chatReference)]));
 }
 function showReference(ref){
  if(!ref)return;
  const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.chatReferencePreview='';
  wrap.innerHTML=`<div class="modal candidate-modal"><h3>本次引用</h3><p class="modal-help">${esc(chapter(ref.chapter_id)?.title||'原章节')} · ${Array.from(ref.range?.quote||'').length} 字</p>
   <blockquote class="reference-full-quote">${esc(ref.range?.quote||'')}</blockquote><div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button></div></div>`;
  document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 }
 async function quoteSelection(){
  if(!validSelection())return notify('请先在正文里选中要讨论的文字。');
  if(references.length>=3)return notify('每次最多引用三处原文，请先移除一处。');
  const captured=capture(),selected={...savedSelection},body=selected.body,c=chapter();
  try{
   if(!await syncBackend())throw new Error('请先保存或合并稿件，再引用原文。');
   if(!current(captured)||!c||chapter()?.id!==c.id||chapter()?.body!==body)return;
   const source={project_id:captured.project,branch_id:captured.branch,chapter_id:c.id,source_revision_id:chapter(c.id)?.revision_id,
    source_body_hash:await sha256(body),range:{start:MoyuSync.codepointOffset(body,selected.start),
     end:MoyuSync.codepointOffset(body,selected.end),quote:body.slice(selected.start,selected.end)},mode:'replace'};
   if(!current(captured)||chapter()?.body!==body)return;
   if(references.some(ref=>ref.chapter_id===source.chapter_id&&ref.range?.start===source.range.start&&ref.range?.end===source.range.end))return;
   references=references.filter(ref=>ref.chapter_id===source.chapter_id&&ref.source_body_hash===source.source_body_hash);
   references.push(source);pendingSend=null;++draftRevision;rememberDraft();renderReferences();notify('');updateControls();input.focus();
  }catch(error){if(current(captured))notify(publicError(error))}
 }
 async function send(event){
  event?.preventDefault();if(running())return cancel();
  if(sendOperation||actionOperation||creating||!input.value.trim()||session?.restored_read_only)return;
  const operation={},captured=capture(),content=input.value.trim(),revision=draftRevision;
  const refs=references.map(ref=>MoyuSync.copy(ref)),include=$('#agentChatIncludeChapter').checked,cid=chapter()?.id||null;
  sendOperation=operation;captured.bundleRevision=++bundleRevision;notify('');renderMessages();
  try{
   if(!backendOnline)throw new Error('请先连接本机服务。');
   if(settings?.configured===false)throw new Error('请先连接写作总管模型。');
   if(!await syncBackend())throw new Error('请先保存或合并当前稿件。');
   if(!current(captured))return;
   if(!await ensureSession(captured))return;
   const payload={content,chapter_id:cid,include_chapter:include,references:refs};
   const signature=JSON.stringify(payload);
   if(!pendingSend||pendingSend.signature!==signature)pendingSend={request_id:crypto.randomUUID(),signature};
   rememberDraft();
   const data=await request('/sessions/'+encodeURIComponent(session.id)+'/messages',{method:'POST',
    body:JSON.stringify({...payload,request_id:pendingSend.request_id})},captured);
   if(!applyBundle(data,captured))return;
   pendingSend=null;
   if(draftRevision===revision&&input.value.trim()===content){input.value='';references=[];++draftRevision;renderReferences()}
   rememberDraft();notify('');renderMessages();loadSessions(false);
  }catch(error){if(current(captured)){notify(publicError(error));rememberDraft()}}
  finally{if(sendOperation===operation){sendOperation=null;renderMessages();schedule(0)}}
 }
 async function cancel(){
  if(!running()||sendOperation||actionOperation)return;
  const captured=capture(),operation={};sendOperation=operation;captured.bundleRevision=++bundleRevision;renderMessages();
  try{applyBundle(await request('/runs/'+encodeURIComponent(activeRun.id)+'/cancel',{method:'POST',body:'{}'},captured),captured)}
  catch(error){if(current(captured))notify(publicError(error))}
  finally{if(sendOperation===operation){sendOperation=null;renderMessages();schedule(0)}}
 }
 async function writingRequest(path,options,captured){
  const response=await fetch('/api/writing-agent'+path,{signal:AbortSignal.timeout(20000),...options,headers:{'Content-Type':'application/json',
   'X-Moyu-Project':captured.project,'X-Moyu-Branch':captured.branch}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const error=new Error(data.error||`任务状态尚未读取（${response.status}）`);error.status=response.status;throw error}
  return data;
 }
 async function readWritingTask(action,{force=false,captured=capture()}={}){
  const entry=taskEntry(action);if(!entry||!taskCurrent(action,captured,entry)||!backendOnline)return null;
  if(entry.pending&&!force)return entry.pending;
  const sequence=++entry.sequence;
  const valid=()=>taskCurrent(action,captured,entry)&&sequence===entry.sequence;
  const pending=(async()=>{
   try{
    const data=await writingRequest('?session_id='+encodeURIComponent(action.result.writing_session_id),{},captured);
    if(!valid())return null;
    const row=data.sessions?.find(item=>item.id===action.result.writing_session_id);
    if(!taskMatches(action,row))throw new Error('写作任务与这条建议不一致，请重新核对。');
    entry.row=row;entry.verified=true;entry.error='';entry.nextRead=Date.now()+taskDelay(row);
    if(row.status!=='awaiting_authorization')entry.uncertain=false;
    return row;
   }catch(error){
    if(valid()){entry.verified=false;entry.error='任务状态尚未核实：'+publicError(error);entry.nextRead=Date.now()+15000}
    return null;
   }finally{
    if(valid()){entry.pending=null;renderMessages();schedule()}
   }
  })();
  entry.pending=pending;renderMessages();return pending;
 }
 function mergeAction(action,result){
  const updated={...action,...result.action,result:{...action.result,...result.action?.result}};
  if(result.writing_session?.id&&!updated.result.writing_session_id)updated.result.writing_session_id=result.writing_session.id;
  if(result.job?.id&&!updated.result.job_id)updated.result.job_id=result.job.id;
  messages=messages.map(message=>({...message,actions:message.actions?.map(item=>item.id===action.id?updated:item)}));
  return updated;
 }
 async function startWritingTask(action,captured){
  const entry=taskEntry(action);if(!taskCurrent(action,captured,entry)||!taskCanStart(entry,action))return;
  if(authorBlueprintDirty&&!await authorSaveBlueprint())throw new Error('当前规划需要先保存。');
  if(!taskCurrent(action,captured,entry)||session?.restored_read_only)return;
  const row=await readWritingTask(action,{force:true,captured});
  if(!taskCurrent(action,captured,entry)||!row)return;
  if(!taskCanStart(entry,action))throw new Error('任务进度已变化，请按卡片中的当前状态继续。');
  // A fresh read invalidates older progress requests before an authorized write.
  ++entry.sequence;entry.pending=null;entry.verified=false;entry.error='';entry.nextRead=Infinity;renderMessages();
  try{
   const data=await writingRequest('/'+encodeURIComponent(row.id)+'/resume',{method:'POST',
    body:JSON.stringify({action:'authorize',checkpoint_id:row.checkpoint_id})},captured);
   if(!taskCurrent(action,captured,entry))return;
   if(!taskMatches(action,data.session))throw new Error('启动回应与这条建议不一致，请核对本次任务。');
   entry.row=data.session;entry.verified=true;entry.uncertain=false;entry.nextRead=Date.now()+taskDelay(data.session);
   scheduleQueue(0);
  }catch(error){
   if(!taskCurrent(action,captured,entry))return;
   entry.uncertain=!error.status||error.status>=500;
   entry.error=entry.uncertain?'启动结果待核对，正在读取原任务；请勿重复启动。':publicError(error);
   entry.verified=false;renderMessages();
   const problem=entry.error;
   const latest=await readWritingTask(action,{force:true,captured});
   if(taskCurrent(action,captured,entry)&&(!latest||latest.status==='awaiting_authorization'||entry.uncertain))
    entry.error=problem+(entry.error?' '+entry.error:'');
  }
 }
 async function performAction(action,kind){
  if(!action||actionOperation||sendOperation||creating||session?.restored_read_only&&kind!=='result')return;
  const captured=capture(),startingChapter=activeChapter,operation={actionId:action.id};actionOperation=operation;
  captured.bundleRevision=++bundleRevision;actionErrors.delete(action.id);renderMessages();
  try{
   if(!await syncBackend())throw new Error('请先保存或合并当前稿件。');
   if(!current(captured))return;
   if(kind==='result'){
    if(activeChapter!==startingChapter)return;
    await openActionResult(action,captured,startingChapter);return;
   }
   if(kind==='start'){await startWritingTask(action,captured);return}
   const path='/actions/'+encodeURIComponent(action.id)+'/'+kind;
   const result=action.kind==='next_chapter'&&kind==='confirm'?
    await durableOperation('/api/agent-chat'+path,'POST',{confirm:true},()=>current(captured)):
    await request(path,{method:'POST',body:JSON.stringify({confirm:true})},captured);
   if(!current(captured))return;
   ++bundleRevision;
   const updated=mergeAction(action,result);
   if(kind==='confirm'){
    if(result.job?.id){jobSources.set(result.job.id,result.job.source);scheduleQueue(0)}
    if(updated.result.writing_session_id){
     const entry=taskEntry(updated);
     if(result.writing_session&&taskMatches(updated,result.writing_session))entry.row=result.writing_session;
     renderMessages();await readWritingTask(updated,{force:true,captured});
    }else if(updated.kind==='save_inspiration')notify('灵感已保存到设定库。');
    else await openActionResult(updated,captured);
   }
  }catch(error){if(current(captured))actionErrors.set(action.id,{kind,message:publicError(error)})}
  finally{
   if(actionOperation===operation){actionOperation=null;if(current(captured)){++bundleRevision;renderMessages();schedule(0)}}
  }
 }
 async function openActionResult(action,captured=capture(),startingChapter=activeChapter){
  const result=action.result||{},cid=action.chapter_id;
  if(result.writing_session_id){
   const resultCurrent=()=>current(captured)&&activeChapter===startingChapter&&!!boundAction(action);
   const row=await readWritingTask(action,{force:true,captured});
   if(!resultCurrent())return;
   if(!row)throw new Error(taskEntry(action)?.error||'任务状态尚未核实，请先核对进度。');
   await authorOpenAgentSession(result.writing_session_id,cid,resultCurrent,row.status==='awaiting_adoption'?'workbenchCandidateSection':undefined);return;
  }
  if(action.kind==='save_inspiration'&&result.record_id){
   if(!await hydrate())throw new Error('灵感已保存，页面暂未读到，请恢复连接后再查看。');
   if(!current(captured))return;
   if(mergePending)throw new Error('灵感已保存，请先处理当前稿件的合并再查看。');
   view='world';render();return;
  }
  if(action.kind==='next_chapter'&&result.chapter_id){
   if(!await hydrate())throw new Error('章节交接已保存，页面暂未读到。恢复连接后可再查看结果。');
   if(!current(captured))return;
   if(mergePending)throw new Error('交接已保存，请先合并当前稿件，再打开下一章。');
   if(!chapter(result.chapter_id))throw new Error('下一章已不存在，请从目录核对章节。');
   activeChapter=result.chapter_id;storage.set(cacheKey(':active'),activeChapter);
   view='editor';render();toast('本章已归档，下一章已打开');return;
  }
  if(result.job_id){
   if(cid&&chapter(cid)){activeChapter=cid;view='editor';render();authorRevealWorkbenchSection('workbenchCandidateSection')}
   else{view='tasks';render()}
   scheduleQueue(0);return;
  }
  const target=result.view||action.view;
  if(target==='review'&&cid&&chapter(cid)){activeChapter=cid;view='editor';render();actions.reviewChapter();return}
  if(Object.hasOwn(views,target)){if(cid&&chapter(cid))activeChapter=cid;view=target;render()}
 }
 function renderHistory(){
  $('.agent-chat-history-list').innerHTML=sessions.map(row=>`<button type="button" data-chat-session="${esc(row.id)}" aria-current="${row.id===session?.id}"><strong>${esc(row.title||'新对话')}</strong>
   <small>${esc(relativeDate(row.updated_at||row.created_at))}${row.restored_read_only?' · 历史只读':''}</small></button>`).join('')||'<p>还没有保存的对话。</p>';
  $('.agent-chat-history-list').querySelectorAll('[data-chat-session]').forEach(element=>element.onclick=()=>selectSession(element.dataset.chatSession));
 }
 function sync(){
  const next=scopeKey();
  if(next!==observed){
   if(observed)rememberDraft();
   observed=next;observedScope={...scope()};++epoch;++sessionEpoch;clearTimeout(timer);session=null;sessions=[];messages=[];activeRun=null;settings=null;contextSummary=null;
   writingTasks.clear();actionErrors.clear();++bundleRevision;
   loading=backendOnline;loadError='';sendOperation=null;actionOperation=null;creating=false;references=[];pendingSend=null;pendingCreation=null;
   historyOpen=false;$('.agent-chat-history').hidden=true;notify('');restoreDraft();renderMessages(true);loadSessions(true);
  }else if(backendOnline&&!session&&!loading&&!sessions.length){loading=true;loadSessions(true)}
  $('#agentChatProject').textContent=state.project.title;
  $('#agentChatProject').title=state.project.title;
  $('#agentChatBranch').textContent=scope().branch==='main'?'主线':scope().branch;
  $('.agent-chat-results').hidden=view!=='editor';renderMessages();
 }
 $('#agentChatForm').addEventListener('submit',send);
 $('#agentChatSend').addEventListener('click',event=>{if(running()){event.preventDefault();cancel()}});
 input.addEventListener('input',()=>{++draftRevision;pendingSend=null;rememberDraft();updateControls()});
 input.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)&&!event.isComposing){event.preventDefault();if(!running())send()}
 });
 $('#agentChatIncludeChapter').addEventListener('change',()=>{pendingSend=null;++draftRevision;rememberDraft();updateControls()});
 $('[data-chat-quote]').onclick=quoteSelection;
 $('[data-chat-new]').onclick=newSession;
 $('[data-chat-settings]').onclick=openSettings;
 $('[data-chat-configure]').onclick=openSettings;
 $('[data-chat-history]').onclick=()=>{historyOpen=!historyOpen;$('.agent-chat-history').hidden=!historyOpen;if(historyOpen){renderHistory();loadSessions(false)}};
 $('[data-chat-history-close]').onclick=()=>{historyOpen=false;$('.agent-chat-history').hidden=true};
 host.querySelectorAll('[data-chat-result]').forEach(element=>element.onclick=()=>authorRevealWorkbenchSection(element.dataset.chatResult));
 document.addEventListener('pointerdown',event=>{
  if(historyOpen&&!$('.agent-chat-history').contains(event.target)&&!$('[data-chat-history]').contains(event.target)){historyOpen=false;$('.agent-chat-history').hidden=true}
 });
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&historyOpen){historyOpen=false;$('.agent-chat-history').hidden=true;$('[data-chat-history]').focus()}});
 window.addEventListener('moyu:agent-settings-changed',()=>{notify('');loadSessions(false)});
 window.addEventListener('pagehide',rememberDraft);
 globalThis.MoyuAgentChat={sync,quoteSelection,selectionChanged:updateControls,focus:()=>input.focus(),refresh:()=>loadSessions(false)};
 sync();
})();
