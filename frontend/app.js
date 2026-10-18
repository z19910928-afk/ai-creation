const KEY='mo-yu-novel-v1';
// When the app is served by server.py, SQLite is the durable source of truth.
// localStorage remains a small offline cache so the prototype still opens
// cleanly when index.html is double-clicked or hosted by a static server.
const API_BASE='/api';
const DIRTY_KEY=KEY+':dirty';
const ACTIVE_KEY=KEY+':active';
const MATERIAL_KEY=KEY+':materials';
const PROFILE_KEY=KEY+':retrieval-profile';
const CURRENT_PROJECT_KEY=KEY+':current-project';
const CHAPTER_PLAN_FIELDS={chapterGoal:'chapter_goal',chapterPov:'pov',chapterScenePlan:'scene_plan',
 chapterForeshadowing:'foreshadowing_op',chapterRecap:'recap',chapterNextBrief:'next_brief',chapterHook:'hook'};
const storage={
 get(key){try{return localStorage.getItem(key)}catch{return null}},
 set(key,value){try{localStorage.setItem(key,value);return true}catch{return false}},
 remove(key){try{localStorage.removeItem(key)}catch{}}
};
let requestedProject=storage.get(CURRENT_PROJECT_KEY)||'';
let draftHasContent=false;
const nativeFetch=globalThis.fetch?.bind(globalThis);
const fetch=(input,options={})=>{
 if(!nativeFetch)return Promise.reject(new Error('当前浏览器无法连接本地服务'));
 const headers=new Headers(options.headers||{});
 if(String(input).startsWith('/api/')){
  if(requestedProject&&!headers.has('X-Moyu-Project'))headers.set('X-Moyu-Project',requestedProject);
  if(!headers.has('X-Moyu-Branch'))headers.set('X-Moyu-Branch',state?.branch_id||state?.project?.branch_id||'main');
 }
 return nativeFetch(input,{...options,headers});
};
function cacheKey(suffix=''){return `${KEY}:${requestedProject||'default-project'}${suffix}`}
const starter={project:{title:'未命名长篇',genre:'',summary:'',goal:50000,book_status:'writing'},chapters:[{id:crypto.randomUUID(),title:'第一章 还没有开始的故事',body:'把故事写在这里。\n\n提示：先写一个你最想看到的场景，不必一次写好。',status:'草稿',updated:Date.now(),chapter_goal:'',scene_plan:'',pov:'',foreshadowing_op:'',hook:'',recap:'',next_brief:'',review_status:'unreviewed',review:{}}],records:[{id:crypto.randomUUID(),type:'人物',name:'新人物',description:'记录人物的欲望、秘密和变化。'},{id:crypto.randomUUID(),type:'地点',name:'新地点',description:'记录这里的气味、规则与冲突。'}],plot:{setup:[],develop:[],payoff:[]},memory:[]};
function normalizeAliases(value){
 if(value===undefined||value===null)return [];
 if(!Array.isArray(value)||value.length>20)return null;
 const seen=new Set(),result=[];
 for(const raw of value){
  if(typeof raw!=='string'||raw.length>100)return null;
  const alias=raw.trim();if(!alias)continue;
  const key=alias.toLocaleLowerCase();if(seen.has(key))continue;
  seen.add(key);result.push(alias);
 }
 return result;
}
function normalizeState(x){
 const goal=Number(x?.project?.goal);
 if(!x||!x.project||typeof x.project.title!=='string'||Array.from(x.project.title).length>200||
  !Number.isFinite(goal)||goal<0||goal>1e9||!Array.isArray(x.chapters)||
  !Array.isArray(x.records)||!Array.isArray(x.memory)||!x.plot||typeof x.plot!=='object')return null;
 const text=(value,limit)=>typeof value==='string'&&(value.length<=limit||Array.from(value).length<=limit);
 if(!text(x.project.summary??'',50000)||!text(x.project.genre??'',100))return null;
 if(x.chapters.some(c=>!c||!safeId(c.id)||!text(c.title,500)||!text(c.body,2000000)||
  !['草稿','进行中','完成'].includes(c.status)))return null;
 if(x.records.some(r=>!r||!safeId(r.id)||!text(r.type,100)||!text(r.name,500)||!text(r.description,20000)||normalizeAliases(r.aliases)===null||
  (r.knowledge_scope!==undefined&&(!r.knowledge_scope||typeof r.knowledge_scope!=='object'||Array.isArray(r.knowledge_scope)))||
  (r.known_by!==undefined&&(!Array.isArray(r.known_by)||r.known_by.some(value=>typeof value!=='string')))))return null;
 const statuses=['待确认','已确认','已拒绝','已撤回','已过期','已替代','待重新定位','candidate','accepted','rejected','stale','superseded','retracted'];
 if(x.memory.some(m=>!m||!safeId(m.id)||!text(m.name,500)||!text(m.note??'',20000)||
  !statuses.includes(m.status)||(typeof m.confidence==='number'&&(!Number.isFinite(m.confidence)||m.confidence<0||m.confidence>1))))return null;
 const plot={};
 for(const stage of ['setup','develop','payoff']){
  if(!Array.isArray(x.plot[stage]))return null;
  if(x.plot[stage].some(v=>typeof v==='string'?!text(v,10000):!v||!safeId(v.id)||!text(v.text,10000)))return null;
  plot[stage]=x.plot[stage].map(v=>typeof v==='string'?v:{...v,stage});
 }
 const chapters=x.chapters.map(c=>({...c,updated:typeof c.updated==='number'?c.updated:Date.parse(c.updated)||0,...Object.fromEntries(
  ['chapter_goal','scene_plan','pov','foreshadowing_op','hook','recap','next_brief'].map(k=>[k,String(c[k]??'')])),
  review_status:['unreviewed','pending','passed','needs_revision','stale','failed'].includes(c.review_status)?c.review_status:'unreviewed',
  review:c.review&&typeof c.review==='object'?c.review:{},reviewed_at:c.reviewed_at??null}));
 const records=x.records.map(record=>({...record,aliases:normalizeAliases(record.aliases)}));
 return {...x,project:{...x.project,summary:String(x.project.summary??''),genre:String(x.project.genre??''),goal,
  book_status:x.project.book_status==='complete'?'complete':'writing'},chapters,records,memory:x.memory,plot};
}
let state=load();let view='dashboard';let activeChapter=storage.get(cacheKey(':active'))||storage.get(ACTIVE_KEY)||state.chapters[0]?.id;
let backendOnline=false;let syncTimer=null;let syncInFlight=false;let syncQueued=false;let mutationSeq=0;
let confirmedState=loadConfirmed();let saveError='';let mergePending=null;let syncPromise=null;let independentWrite=false;
let operationChain=Promise.resolve();let hydrateSeq=0;let aiStatusSeq=0;
let memoryEvidenceSequence=0;const memoryEvidenceScopes=new WeakMap();
let textRequestCancel=null;
let aiBusy=false;let aiDraft=null;let aiRunId=null;let aiStatus={mode:'offline',model:'离线创作助手',configured:false};
let storyMap={relationships:[],timeline:[],foreshadowing:[],conflicts:[],character_states:[],knowledge_scope:{}};
let storyLoaded=false;let storyLoadedChapter=null;
let candidateStore=loadCandidates();let queueJobs=[];let queueTimer=null;let queueLoading=false;
const streamReaders=new Map();const jobSources=new Map();let savedSelection=null;let adopting=false;let searchRequestSeq=0;
const revisionInFlight=new Set();const revisionOpening=new Set();let revisionControlSeq=0;
const editorInputBindings=new WeakMap();
const instructionDraftFallback=new Map();
const instructionDraftTasks=new Set(['continue','polish','scene','outline','brainstorm','summary','check']);
let instructionDraftBinding=null;
let referenceChoice=null;let referenceScopeEpoch=0;let referenceChoiceSeq=0;
let referenceObservedScope=null;let aiPreviewSeq=0;
function referenceScope(c=chapter()){
 return {project_id:requestedProject||state.project?.id||'',branch_id:state.branch_id||state.project?.branch_id||'main',
  chapter_id:c?.id||null};
}
function referenceScopeKey(scope=referenceScope()){
 return JSON.stringify([scope.project_id,scope.branch_id,scope.chapter_id]);
}
function syncReferenceScope(){
 const key=referenceScopeKey();
 if(key!==referenceObservedScope){
  referenceObservedScope=key;referenceChoice=null;++referenceScopeEpoch;++referenceChoiceSeq;++aiPreviewSeq;
  document.querySelectorAll('[data-reference-dialog],[data-reference-preview]').forEach(wrap=>wrap.remove());
 }
 return {scope:referenceScope(),key,epoch:referenceScopeEpoch};
}
function invalidateReferenceScope(){
 referenceChoice=null;referenceObservedScope=null;++referenceScopeEpoch;++referenceChoiceSeq;++aiPreviewSeq;
 document.querySelectorAll('[data-reference-dialog],[data-reference-preview]').forEach(wrap=>wrap.remove());
}
function referenceScopeCurrent(captured){
 const current=syncReferenceScope();
 return captured.key===current.key&&captured.epoch===current.epoch;
}
function referenceSelectionReason(hit,scope=referenceScope()){
 if(!scope.chapter_id)return '请先选择写作章节';
 if(!hit||hit.project_id!==scope.project_id||hit.branch_id!==scope.branch_id)return '这段原句属于其他作品或版本';
 if(!safeId(hit.source_document_id)||!safeId(hit.source_section_id)||hit.offset_unit!=='unicode_codepoint'||
  !Number.isInteger(hit.section_start)||!Number.isInteger(hit.section_end)||
  hit.section_start<0||hit.section_end<=hit.section_start||typeof hit.source_quote!=='string'||
  hit.section_end-hit.section_start!==Array.from(hit.source_quote).length||
  ![hit.source_sha256,hit.section_hash,hit.source_hash].every(value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)))
  return '这段原句缺少完整来源，请重新搜索';
 return Array.from(hit.source_quote).length>900?'这段原句超过900字，请选择较短的完整段落':'';
}
function selectReferencePassage(hit,captured=syncReferenceScope()){
 if(!referenceScopeCurrent(captured))throw new Error('作品或章节已切换，请重新选择参考片段');
 const reason=referenceSelectionReason(hit,captured.scope);if(reason)throw new Error(reason);
 referenceChoice={selection:{document_id:hit.source_document_id,section_id:hit.source_section_id,
  section_start:hit.section_start,section_end:hit.section_end,source_sha256:hit.source_sha256,
  section_hash:hit.section_hash,source_hash:hit.source_hash},
  quote:hit.source_quote,title:String(hit.title||''),document_title:String(hit.document_title||hit.filename||'参考来源')};
 ++referenceChoiceSeq;invalidateAIPreviews();renderReferenceTools();
}
function clearReferencePassage(){
 referenceChoice=null;++referenceChoiceSeq;invalidateAIPreviews();renderReferenceTools();
}
function invalidateAIPreviews(){
 ++aiPreviewSeq;document.querySelectorAll('[data-reference-preview]').forEach(wrap=>wrap.remove());
}
function referenceRequestSnapshot(task){
 const captured=syncReferenceScope();
 if(referenceChoice&&!['continue','polish'].includes(task))throw new Error('当前任务不支持参考片段，请改为续写或润色，或撤回本次参考');
 return {...captured,sequence:referenceChoiceSeq,selections:referenceChoice?[{...referenceChoice.selection}]:[]};
}
function referenceRequestCurrent(captured){
 return referenceScopeCurrent(captured)&&captured.sequence===referenceChoiceSeq;
}
function consumeReferencePassage(captured){
 if(captured.selections.length&&referenceRequestCurrent(captured))clearReferencePassage();
}
function referencePassageMarkup(passages){
 if(!Array.isArray(passages)||!passages.length)return '';
 return `<section class="reference-preview-section"><h4>本次参考原句</h4>${passages.map(item=>
  `<p class="modal-help">${esc(item.document_title||item.filename||'参考来源')} · ${esc(item.title||item.section_title||'')}
  · ${Array.from(String(item.source_quote??item.body??'')).length} 字</p><blockquote>${esc(item.source_quote??item.body??'')}</blockquote>`).join('')}</section>`;
}
function renderReferenceTools(){
 syncReferenceScope();const host=document.querySelector('#aiReferenceTools');if(!host)return;
 const choice=referenceChoice,task=document.querySelector('#aiTask')?.value||'continue';
 host.innerHTML=`<div class="reference-current">${choice?
  `<span><strong>本次参考</strong> ${esc(choice.document_title)} · ${esc(choice.title)} · ${Array.from(choice.quote).length} 字</span>`:
  '<span>本次参考：未选择</span>'}${choice&&!['continue','polish'].includes(task)?'<small class="check-bad">当前任务不支持参考片段</small>':''}</div>
  <div class="reference-tools"><button type="button" class="ghost-btn" data-reference-pick>选择参考片段</button>
  ${choice?'<button type="button" class="ghost-btn" data-reference-inspect>查看原句</button><button type="button" class="ghost-btn" data-reference-remove>撤回</button>':''}</div>`;
 host.querySelector('[data-reference-pick]').onclick=()=>authorChooseReference();
 host.querySelector('[data-reference-remove]')?.addEventListener('click',clearReferencePassage);
 host.querySelector('[data-reference-inspect]')?.addEventListener('click',()=>{
  const captured=syncReferenceScope(),selected=referenceChoice;if(!selected)return;
  const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.referencePreview='1';
  wrap.innerHTML=`<div class="modal candidate-modal"><h3>本次参考原句</h3><p class="modal-help">${esc(selected.document_title)}
   · ${esc(selected.title)} · ${Array.from(selected.quote).length} 字</p><blockquote class="reference-full-quote">${esc(selected.quote)}</blockquote>
   <div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button><button type="button" class="ghost-btn" data-remove>撤回</button></div></div>`;
  document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
  wrap.querySelector('[data-remove]').onclick=()=>{
   if(referenceScopeCurrent(captured)&&referenceChoice===selected)clearReferencePassage();wrap.remove();
  };
 });
}
function aiRequestSnapshot(c,task,instruction,source,controls=materialControls(),profile=retrievalProfile()){
 const references=referenceRequestSnapshot(task);
 const originalSource=source||candidateSource(task,c);
 return {references,body:c.body,task,instruction,source:originalSource,
  payload:{task,chapter_id:c.id,instruction,source:originalSource,
   material_controls:MoyuSync.copy(controls),retrieval_profile:{...profile},reference_selections:references.selections}};
}
function aiRequestCurrent(snapshot){
 return referenceRequestCurrent(snapshot.references)&&chapter()?.id===snapshot.payload.chapter_id&&
  chapter()?.body===snapshot.body;
}
async function sealAIPreviewSnapshot(snapshot){
 const body=snapshot.body;snapshot.payload.source={...snapshot.source,
  source_revision_id:chapter(snapshot.payload.chapter_id)?.revision_id,source_body_hash:await sha256(body)};
 if(!aiRequestCurrent(snapshot))throw new Error('原稿或本次参考已变化，请重新预览');
}
async function readAIPromptPreview(snapshot){
 await sealAIPreviewSnapshot(snapshot);
 const response=await fetch(`${API_BASE}/ai/prompt-preview`,{method:'POST',headers:{'Content-Type':'application/json',
  'X-Moyu-Project':snapshot.references.scope.project_id,'X-Moyu-Branch':snapshot.references.scope.branch_id},
  body:JSON.stringify(snapshot.payload)});
 const result=await response.json().catch(()=>({}));
 if(!response.ok)throw new Error(result.error||`预览读取失败（${response.status}）`);
 if(!aiRequestCurrent(snapshot))throw new Error('原稿或本次参考已变化，请重新预览');
 return result;
}
function instructionDraftKey(c=chapter()){
 if(!c?.id)return null;
 return `${KEY}:instruction-draft:${JSON.stringify([requestedProject||state.project?.id||'default-project',
  state.branch_id||state.project?.branch_id||'main',c.id])}`;
}
function loadInstructionDraft(key){
 const fallback=instructionDraftFallback.get(key);
 if(fallback)return fallback;
 try{
  const draft=JSON.parse(storage.get(key)||'null');
  if(draft&&instructionDraftTasks.has(draft.task)&&typeof draft.instruction==='string'&&draft.instruction.length<=2000)
   return {task:draft.task,instruction:draft.instruction};
 }catch{}
 return {task:'continue',instruction:''};
}
function cacheInstructionDraft(binding=instructionDraftBinding){
 if(!binding||!binding.task.isConnected||!binding.instruction.isConnected)return false;
 const draft={task:instructionDraftTasks.has(binding.task.value)?binding.task.value:'continue',
  instruction:binding.instruction.value};
 if(draft.task===binding.saved.task&&draft.instruction===binding.saved.instruction&&!instructionDraftFallback.has(binding.key))return true;
 instructionDraftFallback.set(binding.key,draft);
 const persisted=storage.set(binding.key,JSON.stringify(draft));
 if(persisted)instructionDraftFallback.delete(binding.key);
 else if(!binding.warned){binding.warned=true;toast('浏览器无法保存创作要求，刷新前请保持此页打开')}
 binding.saved=draft;return persisted;
}
function bindInstructionDraft(){
 const task=document.querySelector('#aiTask'),instruction=document.querySelector('#aiInstruction'),key=instructionDraftKey();
 if(!task||!instruction||!key){instructionDraftBinding=null;return}
 if(instructionDraftBinding?.task===task&&instructionDraftBinding.instruction===instruction)return;
 const saved=loadInstructionDraft(key);task.value=saved.task;instruction.value=saved.instruction;
 const binding={key,task,instruction,saved,warned:false};instructionDraftBinding=binding;
 // A detached editor must not overwrite a newer draft after a page or book switch.
 const capture=()=>{if(instructionDraftBinding===binding)cacheInstructionDraft(binding)};
 task.addEventListener('input',capture);task.addEventListener('change',capture);
 instruction.addEventListener('input',capture);instruction.addEventListener('change',capture);
 const clear=document.createElement('button');clear.type='button';clear.id='aiInstructionClear';clear.className='ghost-btn';
 clear.textContent='清空要求';clear.title='清空本章补充要求，恢复续写';clear.setAttribute('aria-label','清空当前章节任务和补充要求');
 clear.addEventListener('click',()=>{
  if(instructionDraftBinding!==binding||!instruction.isConnected)return;
  task.value='continue';instruction.value='';cacheInstructionDraft(binding);renderReferenceTools();instruction.focus();
 });
 document.querySelector('.ai-controls')?.append(clear);
}
function isWritingCandidate(candidate){return !!candidate&&candidate.task!=='assistant_chat'}
function loadCandidates(){try{const rows=JSON.parse(storage.get(cacheKey(':candidates'))||'[]');return new Map(Array.isArray(rows)?rows.filter(isWritingCandidate).map(row=>[row.ai_run_id,row]):[])}catch{return new Map()}}
function persistCandidates(){return storage.set(cacheKey(':candidates'),JSON.stringify([...candidateStore.values()].filter(isWritingCandidate)))}
function rememberCandidateView(candidate){
 if(candidate)storage.set(cacheKey(':candidate-open'),JSON.stringify({run_id:candidate.ai_run_id,...candidate.source}));
 else storage.remove(cacheKey(':candidate-open'));
}
function activeCandidate(){
 try{
  const selection=JSON.parse(storage.get(cacheKey(':candidate-open'))||'null'),candidate=candidateStore.get(selection?.run_id);
  if(selection?.chapter_id===activeChapter&&isWritingCandidate(candidate)&&candidateScopeCurrent(candidate)&&candidate.source?.chapter_id===activeChapter)return candidate;
 }catch{}
 return [...candidateStore.values()].reverse().find(item=>isWritingCandidate(item)&&item.source?.chapter_id===activeChapter&&
  item.source?.project_id===requestedProject&&item.source?.branch_id===(state.branch_id||state.project?.branch_id||'main')&&
  !['accepted','rejected'].includes(item.decision))||null;
}
const WRITING_META_BOUNDARY='(?=[\\s，,。！？:：；;]|$)';
const WRITING_META_PATTERN=new RegExp('^\\s*(?:'+[
 '我们(?:需要|要|得)(?:先)?分析(?:这个|本次|当前)?(?:写作任务|创作任务|续写任务|润色任务|任务要求|任务)',
 '我们得精心写一段','我们要(?:续写|润色|改写)','让我(?:先)?构思(?:情节|正文)',
 '让我(?:先)?构思(?:一个|一段)?(?:约|大约)?\\d+\\s*字的(?:续写|正文)',
 '我要(?:构造|设计|规划)(?:候选正文|续写段落|段落)',
 '下面我将(?:生成|输出)(?:续写|润色|改写)后的正文'
].map(pattern=>pattern+WRITING_META_BOUNDARY).join('|')+')');
function hasProviderValidationShape(candidate,validation){
 if(candidate?.mode==='offline')return true;
 if(!validation||typeof validation!=='object'||Array.isArray(validation))return false;
 const contract=validation.contract||validation.output_contract;
 const hasOwn=(key)=>Object.prototype.hasOwnProperty.call(contract||{},key);
 const nonNegativeIntegerOrNull=(value)=>value===null||
  (Number.isInteger(value)&&value>=0);
 return typeof validation.eligible==='boolean'&&
  typeof validation.protocol_complete==='boolean'&&
  Number.isInteger(validation.characters)&&validation.characters>=0&&
  Array.isArray(validation.issues)&&contract&&typeof contract==='object'&&!Array.isArray(contract)&&
  hasOwn('minimum_characters')&&nonNegativeIntegerOrNull(contract.minimum_characters)&&
  hasOwn('maximum_characters')&&nonNegativeIntegerOrNull(contract.maximum_characters)&&
  hasOwn('counting')&&typeof contract.counting==='string'&&
  hasOwn('source')&&typeof contract.source==='string';
}
function candidateOutputIssues(candidate){
 const validation=candidate?.output_validation;
 if(!validation||typeof validation!=='object'||Array.isArray(validation)){
  // Offline drafts are intentionally local and do not carry provider checks.
  // A provider (or legacy/unknown) draft must fail closed until the server
  // records the mechanical output validation for this exact run.
  if(['continue','polish'].includes(candidate?.task)&&candidate?.mode!=='offline')
   return ['候选缺少后端输出验证，请重新生成'];
  return [];
 }
 if(!hasProviderValidationShape(candidate,validation))
  return ['候选未通过后端完整性检查，请重新生成后再采纳'];
 const text=candidate.text||'';const chars=Array.from(text).filter(char=>!/\s/u.test(char)).length;
 const contract=validation.contract||validation.output_contract||{};
 const issues=[];
 const seenCodes=new Set();
 const textRecomputedCodes=new Set(['length_out_of_range','planning_contamination','empty_output']);
 const addIssue=(code,message)=>{
  if(!message||seenCodes.has(code))return;
  seenCodes.add(code);issues.push(message);
 };
 if(contract.minimum_characters!=null&&chars<contract.minimum_characters||
  contract.maximum_characters!=null&&chars>contract.maximum_characters){
  const requirement=contract.minimum_characters!=null&&contract.maximum_characters!=null?
   `${contract.minimum_characters}至${contract.maximum_characters}字`:contract.minimum_characters!=null?
    `不少于${contract.minimum_characters}字`:`不超过${contract.maximum_characters}字`;
  addIssue('length_out_of_range',`候选${chars}字，要求${requirement}`);
 }
 if(!text.trim())addIssue('empty_output','候选没有有效正文');
 const tagged=/^[ \t]*<\s*(?:think|analysis|reasoning)\s*>/im.test(text);
 const planning=['continue','polish'].includes(candidate.task)&&text.split(/\r?\n/).some(line=>{
  const trimmed=line.trimStart();if(!trimmed||`"'“”‘’「」『』﹁﹂‹›«»《》〈〉`.includes(trimmed[0]))return false;
  return /^\s*(?:#{1,6}\s*)?(?:思考过程|推理过程|创作思路|写作计划|分析|构思正文|Analysis|Reasoning|Planning)\s*(?:[:：]|$)/i.test(line)||
   /^\s*(?:我们需要(?:回答|创作|撰写|续写|控制字数)|用户要求(?:续写|写作|创作))/.test(line)||
   WRITING_META_PATTERN.test(line)||
   /^\s*(?:We need to|The user (?:asks|wants|requested))\b/i.test(line);
 });
 if(tagged||planning)addIssue('planning_contamination','候选包含明显的模型规划或思考标记，请整理后再采纳');
 const backendIssues=Array.isArray(validation.issues)?validation.issues:[];
 for(const issue of backendIssues){
  const code=typeof issue?.code==='string'&&issue.code?issue.code:`backend_issue_${issues.length}`;
  if(textRecomputedCodes.has(code))continue;
  const description=typeof issue?.description==='string'&&issue.description?
   issue.description:'候选输出未通过后端检查，请整理后再采纳';
  addIssue(code,description);
 }
 if(validation.protocol_complete===false&&!seenCodes.has('protocol_incomplete')){
  addIssue('protocol_incomplete','模型回复未具备完整正文协议证据，请重新生成后再采纳');
 }
 if(typeof validation.eligible!=='boolean'&&!issues.length){
  addIssue('validation_ineligible','候选未通过后端完整性检查，请重新生成后再采纳');
 }else if(validation.eligible===false&&!issues.length&&!backendIssues.length){
  addIssue('validation_ineligible','候选未通过后端完整性检查，请重新生成后再采纳');
 }
 return issues;
}
const AI_TASK_NAMES={continue:'续写',polish:'润色',outline:'故事总纲',scene:'场景计划',summary:'回顾与交接',
 check:'连续性检查',brainstorm:'创意方向',chapter_analysis:'章后记忆',semantic_review:'故事审稿'};
function isAnalysisCandidate(candidate){return ['chapter_analysis','semantic_review'].includes(candidate?.task)}
function terminalCandidateFailure(candidate){return ['failed','canceled','unknown','interrupted'].includes(candidate?.job_status)}
function candidateReadOnly(candidate){
 return !isWritingCandidate(candidate)||!candidate.complete||terminalCandidateFailure(candidate)||candidate.truncated||isAnalysisCandidate(candidate)||
  ['accepted','rejected'].includes(candidate?.decision)||!!candidate?.restored_read_only||!!candidate?.materials_read_only;
}
function editCandidateText(event){
 if(candidateReadOnly(aiDraft)){displayCandidate(aiDraft);return}
 aiDraft.text=event.target.value;persistCandidates();displayCandidate(aiDraft);
}
function analysisCandidatePreview(candidate){
 const label=AI_TASK_NAMES[candidate.task];
 if(!candidate.complete)return candidate.error?`${label}未完成：${candidate.error}`:`${label}正在生成，完成后显示摘要。`;
 let result=candidate.analysis||candidate.story_review||candidate.structured;
 if(!result||typeof result!=='object'||Array.isArray(result)){
  try{result=JSON.parse(candidate.text||'')}catch{result={}}
 }
 if(!result||typeof result!=='object'||Array.isArray(result))result={};
 const payload=result.structured||result.payload||result;
 const text=value=>typeof value==='string'?value:typeof value?.text==='string'?value.text:'';
 if(candidate.task==='chapter_analysis'){
  const summary=text(result.summary||payload.summary),handoff=text(result.handoff||payload.handoff||payload.next_brief);
  const rows=result.candidates||payload.candidates||payload.items||[];
  const candidates=Array.isArray(rows)?rows.filter(item=>item&&typeof item==='object'):[];
  return [label,summary?`章节回顾：\n${summary}`:'',handoff?`下一章任务：\n${handoff}`:'',
   `记忆候选：${candidates.length} 条`,
   ...candidates.map((item,index)=>`${index+1}. ${item.name||item.title||item.attribute||'待确认事实'}`)].filter(Boolean).join('\n\n');
 }
 const rows=result.findings||result.issues||payload.findings||payload.issues||[];
 const findings=Array.isArray(rows)?rows.filter(item=>item&&typeof item==='object'):[];
 const notes=result.uncertainties||payload.uncertainties||[];
 const uncertainties=Array.isArray(notes)?notes.filter(note=>typeof note==='string'&&note.trim()):[];
 return [label,findings.length?`待处理问题：${findings.length} 个`:'暂未检出可处理的问题，仍需作者复核。',
  ...findings.map((item,index)=>`${index+1}. ${[item.title||item.category||item.kind,item.description||item.message].filter(Boolean).join('：')||'待复核问题'}`),
  ...uncertainties.map(note=>`待核实：${note}`)].join('\n\n');
}
function displayCandidate(candidate){
 if(!isWritingCandidate(candidate))candidate=null;
 aiDraft=candidate;aiRunId=candidate?.ai_run_id||null;
 const decided=['accepted','rejected'].includes(candidate?.decision),analysis=isAnalysisCandidate(candidate);
 const taskName=candidate?.candidate_revision?`整理后的${AI_TASK_NAMES[candidate.task]||'正文'}`:AI_TASK_NAMES[candidate?.task]||'AI';
 const incomplete=terminalCandidateFailure(candidate);
 const preview=analysis&&!incomplete?analysisCandidatePreview(candidate):candidate?.text||'';
 const heading=document.querySelector('.ai-copilot h3');if(heading)heading.textContent=candidate?`${taskName}${analysis?'':'候选'}`:'AI 候选';
 const output=document.querySelector('#aiOutput');if(output){
  if(output.value!==preview)output.value=preview;output.readOnly=candidateReadOnly(candidate);
  output.setAttribute('aria-label',analysis?`${taskName}摘要`:'AI 候选结果');
 }
 const status=document.querySelector('#aiStatus');
 const outputIssues=analysis?[]:candidateOutputIssues(candidate);
 if(status)status.textContent=candidate?.restored_read_only?'恢复的候选仅供阅读，请按当前稿件生成新候选':decided?`${taskName} · ${candidate.decision==='accepted'?'已采纳':'已拒绝'} · 历史记录`:candidate?.materials_read_only?candidate.material_issue||'使用的资料已变化，候选仅供阅读，请生成新候选':candidate?(candidate.complete?
  outputIssues.length?`候选需要整理：${outputIssues.join('；')}`:
   `${analysis?`${taskName}已生成`:'候选已准备好'} · ${candidate.model||'创作助手'}${candidate.truncated?' · 输出不完整，仅可阅读':''}`:
  incomplete?`${taskName}${taskStatus(candidate.completion_state==='unknown'?'unknown':candidate.job_status)} · ${candidate.error||'任务未完整结束'} · ${candidate.text?'已保留片段，仅供阅读和复制':'没有已保存的片段'}`:
   candidate.error?`${taskName}未完成：${candidate.error}`:`${analysis?`${taskName}正在生成`:'正在生成'} · 已收到 ${Array.from(candidate.text||'').length} 字`):'';
 const adopt=document.querySelector('#aiAdopt');if(adopt){
  adopt.textContent=analysis?`查看${taskName}`:'比较并采纳';
  adopt.disabled=!candidate?.complete||incomplete||candidate.truncated||outputIssues.length>0||decided||adopting||!!candidate.restored_read_only||!!candidate.materials_read_only&&!analysis;
 }
 const clear=document.querySelector('#aiClear');if(clear)clear.disabled=!candidate||incomplete||decided||!!candidate.restored_read_only;
 const copy=document.querySelector('#aiCopy');if(copy)copy.disabled=!preview||analysis&&!candidate.complete&&!incomplete;
 renderAIProvenance(candidate);
 updateCandidateRevisionControls();
}
async function sha256(text){
 const buffer=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(String(text)));
 return [...new Uint8Array(buffer)].map(byte=>byte.toString(16).padStart(2,'0')).join('');
}
function candidateCharacters(text){return Array.from(String(text||'')).filter(char=>!/\s/u.test(char)).length}
function candidateLengthRequirement(candidate){
 const contract=candidate?.output_validation?.contract||candidate?.output_validation?.output_contract||{};
 const minimum=contract.minimum_characters,maximum=contract.maximum_characters;
 return minimum!=null&&maximum!=null?`${minimum}至${maximum}字`:minimum!=null?`不少于${minimum}字`:
  maximum!=null?`不超过${maximum}字`:'沿用原任务要求';
}
function candidateScopeCurrent(candidate){
 const source=candidate?.source;
 return !!source&&source.project_id===requestedProject&&source.branch_id===(state.branch_id||state.project?.branch_id||'main');
}
function candidateRevisionKey(candidate){return JSON.stringify([candidate?.source?.project_id,candidate?.source?.branch_id,candidate?.ai_run_id])}
function candidateRevisionChild(candidate){
 if(!candidate?.ai_run_id)return null;
 const runId=candidate?.revision_request?.child_run_id;
 const saved=runId&&candidateStore.get(runId);
 return saved?.candidate_revision?.parent_run_id===candidate.ai_run_id&&candidateScopeCurrent(saved)?saved:
  [...candidateStore.values()].reverse().find(item=>
  item.candidate_revision?.parent_run_id===candidate?.ai_run_id&&candidateScopeCurrent(item));
}
function candidateRevisionReason(candidate){
 if(!candidate||!['continue','polish'].includes(candidate.task))return '只支持整理续写或润色正文';
 if(candidate.restored_read_only)return '恢复的候选仅供阅读，请生成新候选';
 if(candidate.materials_read_only)return candidate.material_issue||'使用的资料已变化，请生成新候选';
 if(terminalCandidateFailure(candidate))return '未完成的片段仅供阅读和复制，不能整理';
 const validation=candidate.output_validation;
 if(!hasProviderValidationShape(candidate,validation))
  return '候选缺少后端输出验证，请重新生成';
 if(candidate.reasoning_observation?.requested_disabled&&(
  candidate.reasoning_observation?.nonempty_events>0||candidate.reasoning_observation?.invalid_events>0)){
  return candidate.reasoning_observation?.invalid_events>0?
   '回复与关闭思考设置不一致；思考字段类型无法校验，本候选仅供阅读，不会自动重新发送':
   '回复与关闭思考设置不一致；本候选仅供阅读，不会自动重新发送';
 }
 if(!candidate.complete||candidate.truncated||candidate.mode!=='provider'||candidate.safe_to_apply!==true||
  candidate.completion_state!=='complete'||candidate.finish_reason!=='stop'||!candidate.originalText?.trim()){
  return '请等待模型返回完整正文；离线或不完整候选不能整理';
 }
 if(candidate.decision!=='pending')return '这份候选已有采纳或拒绝记录';
 if(candidate.text!==candidate.originalText)return '候选已被手工编辑，请保留当前修改或生成新候选';
 const source=candidate.source,current=chapter(source?.chapter_id);
 if(!candidateScopeCurrent(candidate)||source.chapter_id!==activeChapter||!current)return '请回到候选的来源章节和版本';
 if(current.revision_id!==source.source_revision_id)return '原稿已修改，请按新稿生成候选';
 return '';
}
async function ensureCandidateRevisionCurrent(candidate){
 const reason=candidateRevisionReason(candidate);if(reason)throw new Error(reason);
 const source=candidate.source,current=chapter(source.chapter_id),body=current.body;
 if(await sha256(body)!==source.source_body_hash||candidateRevisionReason(candidate)||chapter(source.chapter_id)?.body!==body){
  throw new Error('原稿已修改，这份候选保留供参考；请按新稿生成候选');
 }
}
async function updateCandidateRevisionControls(){
 const sequence=++revisionControlSeq,candidate=aiDraft,button=document.querySelector('#aiRevise');
 const original=document.querySelector('#aiOriginal');if(original){original.hidden=!candidate?.candidate_revision;original.disabled=!candidate?.candidate_revision?.parent_run_id}
 const host=document.querySelector('.ai-result-meta');host?.querySelector('[data-ai-revision]')?.remove();
 if(candidate?.candidate_revision&&host){
  const line=document.createElement('span');line.dataset.aiRevision='1';line.className='ai-provenance-line';
  line.textContent=`整理来源：原候选 ${candidate.candidate_revision.parent_characters??'未知'} 字 · 结果仍需作者比较并采纳`;
  host.prepend(line);
 }
 if(!button)return;
 const child=candidateRevisionChild(candidate),request=candidate?.revision_request;
 const recovering=request&&request.status!=='prepared';
 const busy=candidate&&(revisionInFlight.has(candidateRevisionKey(candidate))||revisionOpening.has(candidateRevisionKey(candidate)));
 button.textContent=child?'查看整理候选':recovering?'恢复整理任务':'整理候选';button.disabled=true;
 if(busy){button.title='正在核对整理任务，请稍候';return}
 if(child||recovering){button.disabled=!candidateScopeCurrent(candidate)||!!candidate.restored_read_only||terminalCandidateFailure(candidate);button.title='读取已有整理任务，不会自动再次调用模型';return}
 const reason=candidateRevisionReason(candidate);button.title=reason||'另行生成一份整理候选，单独计入模型用量';
 if(reason||!backendOnline)return;
 try{
  await ensureCandidateRevisionCurrent(candidate);
  if(sequence===revisionControlSeq&&candidate===aiDraft&&button.isConnected)button.disabled=false;
 }catch(error){if(sequence===revisionControlSeq&&candidate===aiDraft)button.title=error.message}
}
function receiveCandidateJob(job){
 if(!isWritingCandidate(job))return null;
 let draft=candidateStore.get(job.ai_run_id);
 const result=job.result||{},source=job.source||result.source||jobSources.get(job.id)||draft?.source;
 if(!source)return draft;
 const savedDecision=job.author_decision||result.author_decision;
 const decision=['accepted','partial','edited'].includes(savedDecision)?'accepted':savedDecision==='rejected'?'rejected':draft?.decision||'pending';
 const metadata={candidate_revision:job.candidate_revision||result.candidate_revision||draft?.candidate_revision,
  restored_read_only:!!(job.restored_read_only||result.restored_read_only),
  materials_read_only:!!(job.materials_read_only||result.materials_read_only),
  material_issue:job.material_issue||result.material_issue||'',job_status:job.status,created_at:job.created_at||draft?.created_at};
 if(!draft)draft={task:job.task,text:'',originalText:'',ai_run_id:job.ai_run_id,job_id:job.id,source,complete:false,last_seq:0};
 const incomplete=['canceled','failed','unknown','interrupted'].includes(job.status);
 if(job.result&&!draft.complete&&(job.status==='success'||incomplete)){
  const prose=['continue','polish'].includes(job.task)&&result.response_format==='json';
  const text=String(prose?result.structured?.content??(incomplete?result.text||'':''):
   result.text??(result.structured?JSON.stringify(result.structured,null,2):''));
  if(job.status==='success'||text){
   draft={...draft,...result,text,originalText:text,complete:job.status==='success',truncated:!!result.truncated,
    terminal_snapshot:incomplete,error:''};
  }else{
   const {text:emptyText,...details}=result;Object.assign(draft,details);
  }
 }
 Object.assign(draft,{...metadata,task:job.task,ai_run_id:job.ai_run_id,job_id:job.id,source,decision});
 if(incomplete){draft.complete=false;draft.safe_to_apply=false;draft.error=job.error||taskStatus(job.status)}
 candidateStore.set(job.ai_run_id,draft);
 const parent=candidateStore.get(metadata.candidate_revision?.parent_run_id);
 if(parent){
  parent.revision_request={...parent.revision_request,parent_run_id:parent.ai_run_id,project_id:source.project_id,branch_id:source.branch_id,
   status:job.status,job_id:job.id,child_run_id:job.ai_run_id};
 }
 return draft;
}
async function recoverCandidateRevision(candidate){
 if(!candidateScopeCurrent(candidate))throw new Error('作品已切换，请回到候选的来源作品恢复任务');
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main';
 const response=await fetch(`${API_BASE}/ai/queue?limit=200`,{headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch}});
 const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||'整理任务暂未读到，请稍后恢复');
 if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已切换，回到原作品后可恢复整理任务');
 const rows=(data.jobs||[]).filter(isWritingCandidate);for(const job of [...rows].reverse())receiveCandidateJob(job);
 let child=candidateRevisionChild(candidate);
 const run=await readCandidateRun(candidate.ai_run_id,owner,branch),job=run.candidate_revision_child;
 if(job&&job.candidate_revision?.parent_run_id===candidate.ai_run_id){child=receiveCandidateJob(job);if(!rows.some(row=>row.id===job.id))rows.unshift(job)}
 persistCandidates();queueJobs=rows;updateTaskCount();
 return child;
}
async function readCandidateRun(runId,owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main'){
 const response=await fetch(`${API_BASE}/ai/runs/${encodeURIComponent(runId)}`,{headers:{'X-Moyu-Project':owner,'X-Moyu-Branch':branch}});
 const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||'原候选记录暂未读到，请稍后恢复');
 if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))throw new Error('作品已切换，请回到来源作品查看');
 if(data.job&&data.job.ai_run_id===runId)receiveCandidateJob(data.job);
 return data;
}
async function reviseAI(){
 const candidate=aiDraft;if(!candidate)return;
 if(terminalCandidateFailure(candidate))return toast('未完成的片段仅供阅读和复制，不能整理');
 const key=candidateRevisionKey(candidate);if(revisionInFlight.has(key)||revisionOpening.has(key))return;
 revisionOpening.add(key);updateCandidateRevisionControls();
 try{
  let child=candidateRevisionChild(candidate);
  if(candidate.revision_request&&candidate.revision_request.status!=='prepared'||child){
   child=await recoverCandidateRevision(candidate);
   if(child){rememberCandidateView(child);displayCandidate(child);scheduleQueue(0);return}
  }
  await ensureCandidateRevisionCurrent(candidate);
  if(!candidate.revision_request){
   const candidateHash=await sha256(candidate.originalText);
   await ensureCandidateRevisionCurrent(candidate);
   candidate.revision_request={project_id:candidate.source.project_id,branch_id:candidate.source.branch_id,
    parent_run_id:candidate.ai_run_id,request_id:crypto.randomUUID(),notes:'',candidate_hash:candidateHash,status:'prepared'};
  }
  if(!persistCandidates())throw new Error('浏览器无法保存整理请求，请保持候选并检查浏览器存储后重试');
  showCandidateRevisionConfirmation(candidate);
 }catch(error){toast(error.message)}
 finally{revisionOpening.delete(key);updateCandidateRevisionControls()}
}
function showCandidateRevisionConfirmation(candidate){
 if(document.querySelector('[data-revise-dialog]'))return;
 const request=candidate.revision_request,source=candidate.source,retry=request.status!=='prepared';
 const range=source.mode==='append'?'原稿末尾续写':`原稿字符 ${source.range.start}至${source.range.end}`;
 const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.reviseDialog=candidate.ai_run_id;
 wrap.innerHTML=`<form class="modal author-form"><h3>${retry?'恢复同一整理请求':'整理候选'}</h3>
 <p class="modal-help">原候选 ${candidateCharacters(candidate.originalText)} 字 · 原要求 ${esc(candidateLengthRequirement(candidate))}<br>
 《${esc(chapter(source.chapter_id)?.title||'来源章节')}》 · ${esc(range)}</p>
 <p class="modal-help">这次整理会单独计入模型用量。原候选继续保留，整理结果需要您再次比较并采纳。</p>
 ${retry?'<p class="modal-help">暂未查到已保存的整理任务。确认后仅重发同一请求；已收到的请求不会重复生成。</p>':''}
 <label for="candidateRevisionNotes">整理要求（可选）</label><textarea id="candidateRevisionNotes" maxlength="2000" ${retry?'readonly':''}
 placeholder="例如：按原范围压缩，纠正人物持有物品，去掉写作思考">${esc(request.notes||'')}</textarea>
 <p class="form-error" role="alert"></p><div class="modal-actions"><button class="ghost-btn" type="button" data-close>取消</button>
 <button class="primary-btn" type="submit" data-revise-submit>${retry?'确认重发同一请求':'确认整理一次'}</button></div></form>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('#candidateRevisionNotes').oninput=event=>{if(request.status==='prepared'){request.notes=event.target.value;persistCandidates()}};
 let submitting=false;
 wrap.querySelector('form').onsubmit=async event=>{
  event.preventDefault();if(submitting)return;submitting=true;
  const button=wrap.querySelector('[data-revise-submit]');button.disabled=true;
  const key=candidateRevisionKey(candidate);revisionInFlight.add(key);updateCandidateRevisionControls();
  let sent=false;
  try{
   const recovered=await recoverCandidateRevision(candidate);
   if(recovered){wrap.remove();rememberCandidateView(recovered);displayCandidate(recovered);scheduleQueue(0);return}
   await ensureCandidateRevisionCurrent(candidate);
   if(!backendOnline)throw new Error('连接恢复后才能整理，原候选继续保留');
   if(request.status==='prepared')request.notes=wrap.querySelector('#candidateRevisionNotes').value;
   if(Array.from(request.notes).length>2000)throw new Error('整理要求最多2000字');
   request.status='submitting';
   if(!persistCandidates()){request.status=retry?'awaiting_result':'prepared';throw new Error('整理请求未能保存在浏览器，本次尚未发送')}
   sent=true;
   const data=await durableOperation(`${API_BASE}/ai/runs/${encodeURIComponent(candidate.ai_run_id)}/revise`,'POST',{
    candidate_hash:request.candidate_hash,request_id:request.request_id,notes:request.notes||''});
   if(!candidateScopeCurrent(candidate))throw new Error('作品已切换，整理任务已经保留；回到原作品后查看');
   const job=data.job;if(!job?.id||!job.ai_run_id)throw new Error('整理任务身份尚未确认，请恢复任务后再操作');
   const child=receiveCandidateJob(job);persistCandidates();wrap.remove();
   if(activeChapter===source.chapter_id){rememberCandidateView(child);displayCandidate(child)}
   scheduleQueue(0);toast(data.idempotent?'已恢复原整理任务':'整理任务已保存，原候选继续保留');
  }catch(error){
   if(sent){request.status='awaiting_result';request.error=error.message;if(candidateScopeCurrent(candidate))persistCandidates();
    wrap.querySelector('#candidateRevisionNotes').readOnly=true;button.textContent='核对并恢复整理任务';
    wrap.querySelector('form').onsubmit=async next=>{
     next.preventDefault();if(submitting)return;submitting=true;button.disabled=true;
     try{const child=await recoverCandidateRevision(candidate);wrap.remove();if(child){rememberCandidateView(child);displayCandidate(child)}else showCandidateRevisionConfirmation(candidate)}
     catch(recoveryError){wrap.querySelector('.form-error').textContent=recoveryError.message;button.disabled=false}
     finally{submitting=false}
    };
   }
   wrap.querySelector('.form-error').textContent=sent?`提交结果尚未确认：${error.message}。请先核对已有任务。`:error.message;
   button.disabled=false;
  }finally{submitting=false;revisionInFlight.delete(key);updateCandidateRevisionControls()}
 };
}
async function showOriginalCandidate(){
 const child=aiDraft,parentId=child?.candidate_revision?.parent_run_id;if(!parentId)return;
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main';
 try{
  let parent=candidateStore.get(parentId);
  if(!parent){await readCandidateRun(parentId,owner,branch);parent=candidateStore.get(parentId);persistCandidates()}
  if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))return;
  if(!parent?.originalText)throw new Error('当前任务列表没有原候选，请从来源作品的历史任务继续查看');
  const wrap=document.createElement('div');wrap.className='modal-backdrop';
  wrap.innerHTML=`<div class="modal candidate-modal" data-original-candidate><h3>原候选</h3>
   <p class="modal-help">${candidateCharacters(parent.originalText)} 字 · 原要求 ${esc(candidateLengthRequirement(parent))} · ${parent.decision==='pending'?'尚未采纳':parent.decision==='accepted'?'已采纳':'已拒绝'}<br>原候选和整理候选各自保留。</p>
   <pre class="prompt-preview" data-original-text>${esc(parent.originalText)}</pre>
   <div class="modal-actions"><button class="ghost-btn" data-close>返回整理候选</button></div></div>`;
  document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 }catch(error){toast(error.message)}
}
function captureSelection(){
 const body=document.querySelector('#chapterBody');if(!body)return;
 savedSelection={chapter_id:activeChapter,start:body.selectionStart,end:body.selectionEnd,body:body.value};
 const line=document.querySelector('#selectionState');
 if(line)line.textContent=body.selectionEnd>body.selectionStart?`已选 ${Array.from(body.value.slice(body.selectionStart,body.selectionEnd)).length} 字`:'未选文字';
 globalThis.MoyuAgentChat?.selectionChanged();
}
function candidateSource(task,c){
 const chars=Array.from(c.body);let start=0,end=chars.length,mode='structure';
 if(task==='continue'){start=end;mode='append'}
 if(task==='polish'){
  const selected=savedSelection?.chapter_id===c.id&&savedSelection.body===c.body&&savedSelection.end>savedSelection.start;
  if(selected){start=MoyuSync.codepointOffset(c.body,savedSelection.start);end=MoyuSync.codepointOffset(c.body,savedSelection.end)}
  else if(chars.length>12000)throw new Error('这一章较长，请先选中需要润色的段落，再生成候选');
  mode='replace';
 }
 return {project_id:requestedProject,branch_id:state.branch_id||state.project?.branch_id||'main',
  chapter_id:c.id,source_revision_id:c.revision_id,range:{start,end,quote:chars.slice(start,end).join('')},mode};
}
function load(){
 draftHasContent=false;
 const cachedValues=[storage.get(cacheKey())||(!requestedProject||requestedProject==='default-project'?storage.get(KEY):null),
  storage.get(cacheKey(':base'))];
 for(const [index,raw] of cachedValues.entries())try{
  const cached=normalizeState(JSON.parse(raw));
  if(cached&&(index===0||!requestedProject||cached.project.id===requestedProject)){
   draftHasContent=true;return cached;
  }
 }catch{}
 return MoyuSync.copy(starter);
}
function loadConfirmed(){try{return normalizeState(JSON.parse(storage.get(cacheKey(':base'))))}catch{return null}}
function isDirty(){return !confirmedState||!MoyuSync.equal(state,confirmedState)}
function cacheDraft(){
 // A failed first read leaves a placeholder, not an offline draft for this book.
 if(!draftHasContent)return true;
 const ok=storage.set(cacheKey(),JSON.stringify(state));
 if(confirmedState)storage.set(cacheKey(':base'),JSON.stringify(confirmedState));
 if(isDirty())storage.set(cacheKey(':dirty'),'1');else storage.remove(cacheKey(':dirty'));
 if(!ok)saveError='浏览器无法保留离线草稿，请保持页面打开并导出稿件';
 return ok;
}
function saveIndicator(){
 const el=document.querySelector('#saveState');if(!el)return;
 el.dataset.status=mergePending?'conflict':saveError?'error':syncInFlight?'saving':isDirty()?'pending':'saved';
 el.textContent=mergePending?'有冲突，草稿已保留':saveError?saveError:syncInFlight?'正在保存…':
  isDirty()?(backendOnline?'有新文字待保存':'离线草稿待同步'):'已保存到本机';
 el.title=el.textContent;el.onclick=()=>mergePending?showMergeConflict():reconnect();
}
async function readServerState(projectId=requestedProject,branchId=state.branch_id||state.project?.branch_id||'main'){
 const response=await fetch(`${API_BASE}/state`,{headers:{Accept:'application/json',
  'X-Moyu-Project':projectId,'X-Moyu-Branch':branchId}});
 const data=await response.json().catch(()=>({}));
 if(!response.ok)throw new Error(data.error||`读取作品失败（${response.status}）`);
 const normalized=normalizeState(data);if(!normalized)throw new Error('作品数据格式异常，当前草稿已保留');
 if(projectId&&normalized.project.id!==projectId)throw new Error('读取的作品与当前选择不一致，当前草稿已保留');
 if((normalized.branch_id||normalized.project.branch_id||'main')!==branchId)throw new Error('读取的作品版本不一致，当前草稿已保留');
 return normalized;
}
function currentEditorInput(input,binding){
 return input.isConnected&&document.querySelector('#'+input.id)===input&&view==='editor'&&
  binding.project_id===requestedProject&&binding.branch_id===(state.branch_id||state.project?.branch_id||'main')&&
  binding.chapter_id===activeChapter;
}
function setEditorInputValue(input,value,binding=editorInputBindings.get(input)){
 if(binding&&!currentEditorInput(input,binding))return;
 const before=input.value;
 if(before===value){if(binding){binding.visible=value;binding.deferred=false}return}
 if(binding?.composing){binding.deferred=true;return}
 const change=MoyuSync.changedRange(before,value),delta=change.replacement.length-(change.end-change.start);
 const position=offset=>{
  const point=MoyuSync.codepointOffset(before,offset);
  const next=point<change.start?point:point>=change.end?point+delta:
   point===change.start?change.start:change.start+change.replacement.length;
  return MoyuSync.utf16Offset(value,next);
 };
 const start=position(input.selectionStart),end=position(input.selectionEnd),direction=input.selectionDirection;
 const scrollTop=input.scrollTop,scrollLeft=input.scrollLeft;
 input.value=value;input.setSelectionRange(start,end,direction);input.scrollTop=scrollTop;input.scrollLeft=scrollLeft;
 if(binding){binding.visible=value;binding.deferred=false}
 if(input.id==='chapterBody')captureSelection();
}
function bindEditorInputSync(input,field){
 if(!input)return;
 const binding={project_id:requestedProject,branch_id:state.branch_id||state.project?.branch_id||'main',
  chapter_id:activeChapter,field,visible:input.value,composing:false,deferred:false};
 editorInputBindings.set(input,binding);
 input.addEventListener('compositionstart',()=>{
  if(!binding.deferred&&currentEditorInput(input,binding))binding.visible=input.value;
  binding.composing=true;
 });
 input.addEventListener('compositionend',()=>{
  binding.composing=false;
  setTimeout(()=>{
   if(!currentEditorInput(input,binding))return;
   if(input.value!==binding.visible)input.dispatchEvent(new Event('input',{bubbles:true}));
   const c=chapter(binding.chapter_id);if(c)setEditorInputValue(input,c[field]||'',binding);
   if(mergePending)showMergeConflict();
  },0);
 });
 input.addEventListener('input',event=>{
  if(!currentEditorInput(input,binding)){event.stopImmediatePropagation();return}
  const c=chapter(binding.chapter_id);if(!c)return;
  const visible=input.value,previous=binding.visible,remote=c[field]||'';
  const merged=binding.deferred?MoyuSync.mergeText(previous,visible,remote):{value:visible};
  if(merged.conflict&&!mergePending){
   const snapshot=MoyuSync.copy(state),base=MoyuSync.copy(snapshot);
   base.chapters.find(row=>row.id===c.id)[field]=previous;
   mergePending={base,remote:snapshot,conflicts:[{path:`chapters/${c.id}/${field}`,base:previous,local:visible,remote}]};
  }
  binding.visible=visible;
  const value=merged.conflict?visible:merged.value;
  // Composition text stays untouched until the input method commits its word.
  if(binding.composing||event.isComposing||merged.conflict){
   event.stopImmediatePropagation();
   if(field==='body'&&typeof authorRememberUndo==='function')authorRememberUndo(c,value);
   c[field]=value;c.updated=Date.now();
   if(field==='body'){c.review_status='stale';c.review={...c.review,status:'stale'};c.reviewed_at=null}
   save();
   const count=document.querySelector('#liveCount');if(count)count.textContent=`${Array.from(c.body.replace(/\s/g,'')).length} 字`;
   document.querySelector('#projectMeta').textContent=`${state.chapters.length} 章 · ${words()} 字`;
   if(merged.conflict&&!binding.composing&&!event.isComposing)showMergeConflict();
   return;
  }
  setEditorInputValue(input,value,binding);binding.visible=input.value;
 },true);
}
function receiveState(remote,base=confirmedState){
 const before=MoyuSync.copy(state);
 const result=base?MoyuSync.merge(base,state,remote):{value:remote,conflicts:[]};
 confirmedState=MoyuSync.copy(remote);
 state=result.value;draftHasContent=true;
 syncReferenceScope();
 const current=chapter();
 if(current)for(const [id,key] of Object.entries({chapterBody:'body',chapterTitle:'title',...CHAPTER_PLAN_FIELDS})){
  const input=document.querySelector('#'+id);const value=current[key]||'';
  if(input)setEditorInputValue(input,value);
 }
 if(result.conflicts.length)mergePending={base:MoyuSync.copy(base),remote:MoyuSync.copy(remote),conflicts:result.conflicts};
 cacheDraft();saveIndicator();updateCandidateRevisionControls();
 return {changed:!MoyuSync.equal(before,state),conflicts:result.conflicts};
}
async function hydrate(){
 const sequence=++hydrateSeq;let owner=requestedProject;
 const branch=state.branch_id||state.project?.branch_id||'main';
 const current=()=>sequence===hydrateSeq&&owner===requestedProject&&
  branch===(state.branch_id||state.project?.branch_id||'main');
 try{
  const remote=await readServerState(owner,branch);if(!current())return false;
  backendOnline=true;saveError='';
  if(!requestedProject){owner=requestedProject=remote.project.id;storage.set(CURRENT_PROJECT_KEY,requestedProject)}
  const dirty=draftHasContent&&(storage.get(cacheKey(':dirty'))==='1'||requestedProject==='default-project'&&storage.get(DIRTY_KEY)==='1');
  if(dirty&&confirmedState)receiveState(remote);
  else if(dirty&&!confirmedState&&state.version===remote.version){
   confirmedState=MoyuSync.copy(remote);state.version=remote.version;state.head_revision=remote.head_revision;cacheDraft();
  }else if(dirty&&!confirmedState){
   mergePending={base:null,remote:MoyuSync.copy(remote),conflicts:[{path:'作品草稿',local:MoyuSync.copy(state),remote:MoyuSync.copy(remote)}]};
  }else{state=remote;draftHasContent=true;confirmedState=MoyuSync.copy(remote);cacheDraft()}
  if(!state.chapters.some(c=>c.id===activeChapter))activeChapter=state.chapters[0]?.id;
  render();saveIndicator();if(mergePending)showMergeConflict();else if(isDirty())await syncBackend();
  return current();
 }catch(error){if(current()){backendOnline=false;saveError=error.message;saveIndicator()}return false}
}
async function reconnect(){if(syncInFlight)return syncPromise;saveError='';await hydrate();loadAIStatus()}
async function syncBackend(){
 clearTimeout(syncTimer);
 if(syncPromise){syncQueued=true;await syncPromise;return !isDirty()&&!mergePending}
 if(!backendOnline||mergePending||independentWrite){saveIndicator();return false}
 syncInFlight=true;saveIndicator();
 syncPromise=(async()=>{
  let attempts=0;
  while(isDirty()&&!mergePending&&backendOnline&&attempts++<12){
   const sent=MoyuSync.copy(state);const projectAtSend=requestedProject;
   const base=MoyuSync.copy(confirmedState);
   const headers={'Content-Type':'application/json'};
   if(base?.version!==undefined)headers['X-Moyu-Expected-Head']=String(base.version);
   if(base?.head_revision)headers['X-Moyu-Branch-Expected-Head']=base.head_revision;
   const payload={...sent,version:base?.version,full_state:true,expected_head:base?.head_revision??null};
   let response;
   try{response=await fetch(`${API_BASE}/state`,{method:'PUT',headers,body:JSON.stringify(payload)})}
   catch(error){backendOnline=false;saveError='连接中断，草稿已保留，恢复连接后继续保存';break}
   const data=await response.json().catch(()=>({}));
   if(projectAtSend!==requestedProject)break;
   if(response.status===409){
    try{receiveState(await readServerState(),base)}catch(error){saveError=error.message;break}
    if(mergePending)showMergeConflict();
    continue;
   }
   if(!response.ok){saveError=data.error||`保存失败（${response.status}），草稿已保留`;break}
   const remote=normalizeState(data);
   if(!remote){saveError='保存回应格式异常，正在保留当前草稿';break}
   receiveState(remote,sent);saveError='';
  }
 })();
 try{await syncPromise}finally{syncPromise=null;syncInFlight=false;syncQueued=false;cacheDraft();saveIndicator()}
 if(isDirty()&&backendOnline&&!saveError&&!mergePending&&!independentWrite)syncTimer=setTimeout(syncBackend,180);
 return !isDirty()&&!mergePending&&!saveError;
}
function showMergeConflict(){
 if(!mergePending||document.querySelector('[data-merge-dialog]'))return;
 const pending=mergePending;const wrap=document.createElement('div');wrap.className='modal-backdrop';
 for(const item of pending.conflicts){
  let value=state;
  if(!pending.base)value=state;
  else for(const part of item.path.split('/'))value=Array.isArray(value)?value.find(row=>row?.id===part):value?.[part];
  item.local=MoyuSync.copy(value);
 }
 wrap.dataset.mergeDialog='1';
 const display=value=>typeof value==='string'?value:JSON.stringify(value,null,2)??'（已删除）';
 wrap.innerHTML=`<div class="modal conflict-modal"><h3>两份修改需要合并</h3>
 <p class="modal-help">两处同时改到了同一内容。两份文字都已保留，选择后再保存。</p>
 <div class="conflict-list">${pending.conflicts.map((item,i)=>`<section class="conflict-item" data-conflict-index="${i}">
 <strong>${esc(item.path)}</strong><div class="conflict-columns"><label>本页草稿<textarea readonly>${esc(display(item.local))}</textarea></label>
 <label>本机已保存稿<textarea readonly>${esc(display(item.remote))}</textarea></label></div>
 <label>合并方式<select data-merge-choice><option value="local">保留本页修改</option><option value="remote">采用已保存修改</option>${typeof item.local==='string'&&typeof item.remote==='string'?'<option value="manual">手动整理文字</option>':''}</select></label>
 <textarea data-merge-text class="ai-hidden">${esc(typeof item.local==='string'?item.local:'')}</textarea></section>`).join('')}</div>
 <div class="modal-actions"><button class="ghost-btn" data-backup>导出本页草稿</button><button class="ghost-btn" data-close>稍后处理</button><button class="primary-btn" data-merge>合并并保存</button></div></div>`;
 document.body.append(wrap);
 wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-backup]').onclick=()=>downloadText(exportFilename('draft.json'),JSON.stringify(state,null,2),'application/json');
 wrap.querySelectorAll('[data-merge-choice]').forEach(input=>input.onchange=()=>input.closest('section').querySelector('[data-merge-text]').classList.toggle('ai-hidden',input.value!=='manual'));
 wrap.querySelector('[data-merge]').onclick=async()=>{
  const choices={};
  for(const row of wrap.querySelectorAll('[data-conflict-index]')){
   const item=pending.conflicts[Number(row.dataset.conflictIndex)];const choice=row.querySelector('select').value;
   choices[item.path]=choice==='remote'?item.remote:choice==='manual'?row.querySelector('[data-merge-text]').value:item.local;
  }
  if(!pending.base){state=MoyuSync.copy(choices['作品草稿']);state.version=pending.remote.version;state.head_revision=pending.remote.head_revision}
  else{const result=MoyuSync.merge(pending.base,state,pending.remote,choices);state=result.value;if(result.conflicts.length){mergePending={...pending,conflicts:result.conflicts};wrap.remove();showMergeConflict();return}}
  confirmedState=MoyuSync.copy(pending.remote);mergePending=null;saveError='';wrap.remove();cacheDraft();render();
  const saved=await syncBackend();if(saved)toast('合并后的文字已经保存');
 };
}
function durableOperation(path,method='POST',body={},guard=null,onCommitted=null){
 const scope={owner:requestedProject,branch:state.branch_id||state.project?.branch_id||'main'};
 const operation=operationChain.catch(()=>{}).then(()=>{
  if(scope.owner!==requestedProject||scope.branch!==(state.branch_id||state.project?.branch_id||'main'))
   throw new Error('作品或版本已切换，请回到原作品重新操作');
  if(guard&&!guard())throw new Error('操作已过期，请重新操作');
  return performDurableOperation(path,method,body,guard,onCommitted,scope);
 });
 operationChain=operation.catch(()=>{});return operation;
}
async function performDurableOperation(path,method='POST',body={},guard=null,onCommitted=null,scope){
 const current=()=>scope.owner===requestedProject&&scope.branch===(state.branch_id||state.project?.branch_id||'main');
 if(!backendOnline)throw new Error('需要恢复连接后完成此操作，当前草稿已保留');
 if(!await syncBackend())throw new Error('请先保存或合并当前草稿');
 if(!current())throw new Error('作品或版本已切换，请回到原作品重新操作');
 if(guard&&!guard())throw new Error('操作已过期，请重新操作');
 const before=MoyuSync.copy(state);independentWrite=true;
 try{
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json',
   'X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch},body:JSON.stringify(body)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||`操作失败（${response.status}）`);
  if(onCommitted)onCommitted(data);
  if(current()){
   const returned=normalizeState(data.state);
   const remote=returned?.project?.id===scope.owner&&
    (returned.branch_id||returned.project.branch_id||'main')===scope.branch?returned:
    await readServerState(scope.owner,scope.branch);
   if(current())receiveState(remote,before);
  }
  return data;
 }finally{independentWrite=false;saveIndicator();if(isDirty()&&!mergePending)syncTimer=setTimeout(syncBackend,180)}
}
async function loadAIStatus(){
 const sequence=++aiStatusSeq,owner=requestedProject;
 let status;
 try{const r=await fetch(`${API_BASE}/ai/status`,{headers:{Accept:'application/json'}});if(r.ok)status=await r.json()}
 catch{status={mode:'offline',model:'离线创作助手',configured:false}}
 if(sequence!==aiStatusSeq||owner!==requestedProject)return;
 if(status)aiStatus=status;
 const badge=document.querySelector('#aiBadge');if(badge)badge.textContent=aiStatus.last_probe?.success?'最近实测成功':aiStatus.configured?'已配置':'未配置模型';
}
function localAI(task){const c=chapter()||{};const title=c.title||'当前章节';const memory=state.memory.filter(m=>m.status==='已确认').length;const note=document.querySelector('#aiInstruction')?.value.trim();const extra=note?`\n作者要求：${note}`:'';const drafts={continue:`【离线候选·${title}】\n让角色先面对一个必须立刻回应的具体阻力，再用一个可观察的动作改变局面。继续沿用当前视角，不新增未经确认的背景事实。${extra}`,polish:`【离线润色建议·${title}】\n当前未配置模型，暂不替换正文。请先检查重复表达、抽象形容词、解释性台词，以及每段是否产生了新的动作或信息。${extra}`,outline:`【离线大纲草案】\n1. 明确主角此刻最想得到什么。\n2. 安排一个会消耗资源或暴露秘密的障碍。\n3. 让一次选择带来不可撤回的代价。\n4. 写作前回看已确认记忆 ${memory} 条。${extra}`,scene:`【离线场景卡·${title}】\n入口：从一个正在发生的动作开始。\n目标：角色在本场景结束前得到或保护一项东西。\n阻力：让对手或环境迫使角色改变计划。\n出口：留下一个下一场景必须回答的具体问题。${extra}`,summary:`【离线总结·${title}】\n请人工记录：发生了什么、谁的状态改变、时间地点是否明确、哪些线索尚未回收。${extra}`,check:`【离线连续性检查·${title}】\n已加载 ${memory} 条已确认记忆。逐项核对人物状态、时间顺序、地点移动和伏笔来源；不要把猜测写成矛盾。${extra}`,brainstorm:`【离线走向卡】\nA 直接升级冲突：代价高、节奏快。\nB 暂时退让：保留资源，但暴露新弱点。\nC 误会扩大：让角色依据不完整信息作出选择。${extra}`};return drafts[task]||drafts.scene}
function aiAuditKey(runId){return `${KEY}:ai-decision:${runId}`}
function materialControls(){try{const value=JSON.parse(storage.get(cacheKey(':materials'))||'{}');return value&&typeof value==='object'?value:{}}catch{return {}}}
function saveMaterialControls(value){storage.set(cacheKey(':materials'),JSON.stringify(value))}
const CONTEXT_PROFILE_DEFAULTS={max_tokens:24000,chapter_top_k:20,recent_window:3,memory_top_k:60,record_top_k:40};
const CONTEXT_PROFILE_LIMITS={max_tokens:[4000,64000],chapter_top_k:[0,50],recent_window:[0,10],memory_top_k:[0,120],record_top_k:[0,80]};
function retrievalProfile(){try{const raw=JSON.parse(storage.get(cacheKey(':retrieval-profile'))||'{}');const value={...CONTEXT_PROFILE_DEFAULTS};for(const [key,[min,max]] of Object.entries(CONTEXT_PROFILE_LIMITS)){const number=Number(raw?.[key]);if(Number.isFinite(number))value[key]=Math.max(min,Math.min(max,Math.round(number)))}return value}catch{return {...CONTEXT_PROFILE_DEFAULTS}}}
function saveRetrievalProfile(value){const next={...CONTEXT_PROFILE_DEFAULTS};for(const [key,[min,max]] of Object.entries(CONTEXT_PROFILE_LIMITS)){const number=Number(value?.[key]);if(Number.isFinite(number))next[key]=Math.max(min,max<min?min:Math.min(max,Math.round(number)))}storage.set(cacheKey(':retrieval-profile'),JSON.stringify(next));return next}
function profileQuery(){return encodeURIComponent(JSON.stringify(retrievalProfile()))}
function materialQuery(){const value=JSON.stringify(materialControls());return encodeURIComponent(value)}
function materialLabel(item){return `${item.title||item.name||'未命名'}${item.chapter?` · 第${item.chapter}章`:''}`}
function aiCandidateHash(text){let h=2166136261;for(const c of String(text||'')){h^=c.codePointAt(0);h=Math.imul(h,16777619)}return (h>>>0).toString(16)}
function aiDecisionPayload(decision,reason=''){return {ai_run_id:aiRunId||`local_${crypto.randomUUID()}`,decision,task:aiDraft?.task||'',project_id:state.project?.id||'project_default',chapter_id:chapter()?.id||null,candidate_hash:aiCandidateHash(aiDraft?.text),candidate_chars:String(aiDraft?.text||'').length,source_version:state.version??null,reason:String(reason||'').slice(0,2000),decided_at:new Date().toISOString()}}
function isMissingEndpoint(error){return /404|not found|未找到|请求失败/i.test(error?.message||'')}
async function recordAIDecision(decision,reason=''){
 const body=aiDecisionPayload(decision,reason);let audit='backend';
 if(!body.ai_run_id)body.ai_run_id=`local_${crypto.randomUUID()}`;aiRunId=body.ai_run_id;
 if(backendOnline){try{const r=await fetch(`${API_BASE}/ai/decision`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.error||`AI 决策接口失败（${r.status}）`)}}catch(error){
   if(!isMissingEndpoint(error))throw error;
   try{const r=await fetch(`${API_BASE}/events`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aggregate_id:body.ai_run_id,event_type:'ai.decision',payload:body})});if(!r.ok)throw new Error(`HTTP ${r.status}`)}catch(eventError){if(!isMissingEndpoint(eventError))throw eventError;audit='local'}
 }}else audit='local';
 if(audit==='local'){try{localStorage.setItem(aiAuditKey(body.ai_run_id),JSON.stringify(body))}catch{}}
 return {audit,body}
}
function memoryStatusLabel(status,stale=false){if(stale)return '证据失效';return status==='已确认'||status==='accepted'?'已确认':status==='已拒绝'||status==='rejected'?'已拒绝':status==='stale'?'待重新定位':status==='superseded'?'已被替代':'待确认'}
function memorySourceText(memory){return String(memory.source||memory.source_chapter_id||'未标注章节')}
function memorySpanText(memory){const start=memory.source_start??memory.source_start_codepoint;const end=memory.source_end??memory.source_end_codepoint;return start!==undefined&&end!==undefined?`字符 ${start}–${end}`:'未提供字符范围'}
function memoryQuoteText(memory){const quote=String(memory.source_quote||'').trim();return quote?`“${quote.slice(0,180)}${quote.length>180?'…':''}”`:'未提供原文摘录'}
function renderAIProvenance(result){const host=document.querySelector('.ai-result-meta');if(!host)return;host.querySelector('[data-ai-provenance]')?.remove();const context=result?.context;if(!context)return;const line=document.createElement('span');line.dataset.aiProvenance='1';line.className='ai-provenance-line';const budget=context.budget?.estimated_tokens?` · 约 ${context.budget.estimated_tokens} tokens`:'';line.textContent=`来源：${context.memory_count||0} 条已确认记忆 · ${context.previous_chapter_count||0} 个近章 · ${context.retrieved_count||0} 个召回章节${budget} · 运行 ${String(aiRunId||'').slice(-8)}`;host.prepend(line)}
async function decideMemory(id,status,reason=''){
 const memory=state.memory.find(m=>m.id===id);if(!memory)return;
 if(status==='accepted'&&(!String(memory.source_quote||'').trim()||!String(memory.source_chapter_id||'').trim()||!state.records.some(r=>r.type===memory.type&&r.name===memory.name)))throw new Error('确认记忆前必须有原文摘录、章节来源和对应设定卡');
 const payload={status:status==='accepted'?'accepted':status==='rejected'?'rejected':'candidate',reason:String(reason||'').slice(0,1000)};
 if(!backendOnline)throw new Error('请先恢复连接再确认事实，草稿仍可继续编辑');
 await durableOperation(`${API_BASE}/memory/${encodeURIComponent(id)}`,'PATCH',payload);
 render();toast(`记忆已${status==='rejected'?'拒绝':status==='accepted'?'确认':'标记为待确认'}`)
}
function dismissMemoryEvidence(){
 ++memoryEvidenceSequence;
 document.querySelectorAll('[data-memory-evidence-dialog]').forEach(wrap=>wrap.remove());
}
function memoryEvidenceScope(){
 return {owner:requestedProject,branch:state.branch_id||state.project?.branch_id||'main',
  chapter:activeChapter,view,host:document.querySelector('#main')?.firstElementChild,sequence:memoryEvidenceSequence};
}
function memoryEvidenceCurrent(scope){
 return scope&&scope.owner===requestedProject&&scope.branch===(state.branch_id||state.project?.branch_id||'main')&&
  scope.chapter===activeChapter&&scope.view===view&&scope.host===document.querySelector('#main')?.firstElementChild&&
  scope.sequence===memoryEvidenceSequence;
}
async function showMemoryEvidence(id){
 if(!backendOnline)return toast('启动 SQLite 服务后才能查看证据链');
 dismissMemoryEvidence();const scope=memoryEvidenceScope();
 try{
  const r=await fetch(`${API_BASE}/memory/${encodeURIComponent(id)}`,{headers:{
   Accept:'application/json','X-Moyu-Project':scope.owner,'X-Moyu-Branch':scope.branch}});
  const data=await r.json();if(!memoryEvidenceCurrent(scope))return;
  if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);
  const m=data.memory||{};
  const rows=(data.evidence||[]).map(e=>{
   const s=e.source_span||{},cm=e.candidate_memory||{};
   return `<article class="record-card"><strong>${esc(s.chapter_title||'来源章节未找到')}</strong>
    <p class="modal-help">${esc(s.revision_id||'无 revision')} · 字符 ${s.start_codepoint??'?'}–${s.end_codepoint??'?'} · ${esc(s.status||'未知')} ${s.current===false?'· 不是当前版本':''}</p>
    <blockquote>${esc(s.quote||'未保存原文摘录')}</blockquote>
    ${e.stale?`<p class="check-bad">证据需要复核：${esc((e.stale_reasons||[]).join('、'))}</p>`:''}
    <p class="modal-help">候选记忆：${esc(cm.payload?.name||m.name||'')} · 状态 ${esc(cm.status||'未知')}</p></article>`;
  }).join('');
  const assertions=(data.assertions||[]).map(a=>`<div class="assertion-review-row">
   <p class="modal-help">正典 assertion：<code>${esc(a.id)}</code> · ${esc(a.state)}${a.stale?' · 证据失效':''}</p>
   <button class="ghost-btn" data-assertion-review="${safeId(a.id)}" data-assertion-state="${esc(a.state)}" data-memory-id="${safeId(id)}">审查状态</button></div>`).join('');
  const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.memoryEvidenceDialog='';
  wrap.innerHTML=`<div class="modal"><h3>事实依赖与证据</h3><p class="modal-help"><strong>${esc(m.name||'未命名记忆')}</strong> · ${esc(m.type||'')} · ${esc(memoryStatusLabel(m.status,m.stale))}</p>
   ${assertions||'<p class="modal-help">尚未生成正典 assertion。</p>'}
   <div class="revision-list">${rows||'<p class="modal-help">没有可定位的证据。</p>'}</div>
   <div class="modal-actions"><button class="ghost-btn" data-close>关闭</button></div></div>`;
  memoryEvidenceScopes.set(wrap,scope);document.body.append(wrap);
  wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 }catch(e){if(memoryEvidenceCurrent(scope))toast(`证据读取失败：${e.message}`)}
}
async function reviewAssertion(id,current,memoryId,scope=memoryEvidenceScope(),wrap=null){
 const stillCurrent=()=>memoryEvidenceCurrent(scope)&&(!wrap||wrap.isConnected);
 if(!stillCurrent()){wrap?.remove();return}
 if(!backendOnline)return toast('启动 SQLite 服务后才能审查 assertion');
 const next=await requestText('输入状态：canon / candidate / contradicted / superseded / retracted',
  String(current||'candidate'),{title:'审查事实状态',current:stillCurrent});
 if(next===null||!stillCurrent())return;
 const reason=await requestText('填写审查理由（建议说明证据或冲突）','',{title:'审查理由',current:stillCurrent});
 if(reason===null||!stillCurrent())return;
 try{
  const data=await durableOperation(`${API_BASE}/assertions/${encodeURIComponent(id)}/review`,'POST',{state:next.trim(),reason},stillCurrent);
  if(!stillCurrent())return;
  toast(`事实已更新为 ${data.state||next.trim()}`);showMemoryEvidence(memoryId);
 }catch(error){if(stillCurrent())toast(`事实审查失败：${error.message}`)}
}
async function reanchorMemory(id){
 const memory=state.memory.find(item=>item.id===id);if(!backendOnline||!memory)return toast('启动 SQLite 服务后才能重新定位证据');
 const scope=memoryEvidenceScope(),original=MoyuSync.copy(memory),source=chapter(memory.source_chapter_id);
 const sourceRevision=source?.revision_id,sourceBody=source?.body;
 const stillCurrent=()=>memoryEvidenceCurrent(scope)&&MoyuSync.equal(state.memory.find(item=>item.id===id),original)&&
  chapter(original.source_chapter_id)?.revision_id===sourceRevision&&chapter(original.source_chapter_id)?.body===sourceBody;
 const quote=await requestText('粘贴当前版本中的原文摘录（重复引文必须提供范围）',
  String(memory.source_quote||''),{title:'重新定位原文',current:stillCurrent});
 if(quote===null||!quote.trim()||!stillCurrent())return;
 const startText=await requestText('可选：原文摘录起点（Unicode 字符位置）',
  memory.source_start==null?'':String(memory.source_start),{title:'原文范围起点',current:stillCurrent});
 if(startText===null||!stillCurrent())return;
 const endText=await requestText('可选：原文摘录终点',
  memory.source_end==null?'':String(memory.source_end),{title:'原文范围终点',current:stillCurrent});
 if(endText===null||!stillCurrent())return;
 const payload={source_chapter_id:original.source_chapter_id,source_quote:quote.trim()};
 if(startText.trim()!=='')payload.source_start=Number(startText);if(endText.trim()!=='')payload.source_end=Number(endText);
 try{
  await durableOperation(`${API_BASE}/memory/${encodeURIComponent(id)}/reanchor`,'POST',payload,stillCurrent);
  if(!memoryEvidenceCurrent(scope))return;
  render();toast('证据已重新定位，请确认记忆');
 }catch(error){if(memoryEvidenceCurrent(scope))toast(`重新定位失败：${error.message}`)}
}
async function generateAI(options={}){
 if(aiBusy)return;
 // Click handlers pass an Event; repair flows pass a fixed review source.
 if(!options||typeof options!=='object'||typeof options.preventDefault==='function')options={};
 const fixedSource=options.source&&typeof options.source==='object'?options.source:null;
 const reviewSource=options.review_source&&typeof options.review_source==='object'?options.review_source:null;
 const c=chapter();if(!c)return toast('请先选择章节');
 cacheInstructionDraft();
 const task=document.querySelector('#aiTask')?.value||'continue';
 const instruction=document.querySelector('#aiInstruction')?.value||'';const requestBody=c.body;
 const status=document.querySelector('#aiStatus');const owner=requestedProject;
 let source,snapshot;
 if(fixedSource){
  const branch=state.branch_id||state.project?.branch_id||'main';
  if(task!=='polish'||fixedSource.project_id!==requestedProject||fixedSource.branch_id!==branch||
   fixedSource.chapter_id!==c.id||fixedSource.mode!=='replace'){
   return toast('这条局部修订已经失去原句范围，请重新打开审稿');
  }
  source={...fixedSource,range:fixedSource.range&&{...fixedSource.range}};
 }else{
  try{source=candidateSource(task,c)}catch(error){return toast(error.message)}
 }
 try{snapshot=aiRequestSnapshot(c,task,instruction,source)}catch(error){return toast(error.message)}
 if(!backendOnline)return toast('请先恢复连接；离线时可以继续写作和保留草稿');
 aiBusy=true;if(status)status.textContent='正在保存原稿并准备候选…';
 try{
  if(!await syncBackend())throw new Error('当前稿件还未保存，请先处理保存提示');
  const original=chapter(source.chapter_id);if(!original||owner!==requestedProject||!aiRequestCurrent(snapshot))
   throw new Error('原稿、作品或本次参考已经变化，请重新生成');
  if(original.body!==requestBody)throw new Error('准备期间正文有变化，请按当前新稿重新生成');
  const currentHash=await sha256(original.body);
  if(fixedSource){
   if(source.source_revision_id!==original.revision_id||source.source_body_hash!==currentHash){
    throw new Error('审稿依据已经变化，请重新故事审稿');
   }
  }else{
   source.source_revision_id=original.revision_id;source.source_body_hash=currentHash;
  }
  const responseFormat=['outline','scene','summary','check','brainstorm','semantic_review'].includes(task)?'json':'text';
  const request={...snapshot.payload,source,stream:true,response_format:responseFormat};
  if(reviewSource)request.review_source=reviewSource;
  let committedJob=null;
  const data=await durableOperation(`${API_BASE}/ai/queue`,'POST',request,
   ()=>aiRequestCurrent(snapshot),committed=>{
    const job=committed.job||committed;
    if(job.ai_run_id&&job.id){committedJob=job;consumeReferencePassage(snapshot.references)}
   }).catch(error=>{
    if(!committedJob)throw error;
    toast('候选任务已保存，页面暂未刷新；任务中心会继续读取');
    return {job:committedJob};
   });
  const job=data.job||data;const runId=job.ai_run_id;
  if(!runId||!job.id)throw new Error('任务未返回保存身份，候选仍未应用');
  if(owner!==requestedProject||!referenceScopeCurrent(snapshot.references))return;
  jobSources.set(job.id,source);
  const draft={task,text:'',originalText:'',model:aiStatus.model,ai_run_id:runId,job_id:job.id,source:job.source||source,
   complete:false,decision:'pending',last_seq:0};
  candidateStore.set(runId,draft);persistCandidates();
  if(owner===requestedProject&&activeChapter===source.chapter_id){rememberCandidateView(draft);displayCandidate(draft)}
  scheduleQueue(0);
 }catch(error){if(status&&owner===requestedProject)status.textContent=error.message;toast(error.message)}
 finally{aiBusy=false}
}
async function aiPromptContract(){
 const c=chapter();if(!c)return toast('请先选择章节');
 syncReferenceScope();
 const sequence=++aiPreviewSeq;
 try{
  const snapshot=aiRequestSnapshot(c,document.querySelector('#aiTask')?.value||'continue',
   document.querySelector('#aiInstruction')?.value||'');
  const p=await readAIPromptPreview(snapshot);if(sequence!==aiPreviewSeq)return;
  const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.referencePreview='1';
  wrap.innerHTML=`<div class="modal candidate-modal"><h3>AI 任务契约预览</h3><p class="modal-help">任务：${esc(p.task)}
   · 输出：${esc(p.contract?.output||'')} · 格式：${esc(p.contract?.response_format||'text')} · 估算 ${p.estimated_tokens||0} tokens</p>
   ${referencePassageMarkup(p.reference_passages)}<pre class="ai-contract-preview">${esc(p.system||'')}\n\n${esc(p.user||'')}</pre>
   <div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button></div></div>`;
  document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 }catch(error){if(sequence===aiPreviewSeq)toast(error.message)}
}
async function adoptAI(){
 const draft=aiDraft;if(draft&&!isWritingCandidate(draft))return toast('聊天讨论不能作为正文采纳，请在写作总管中查看');
 if(terminalCandidateFailure(draft))return toast('未完成的片段仅供阅读和复制，不能采纳');
 if(!draft?.complete||!draft.text.trim())return toast('请等待候选完整生成');
 if(draft.restored_read_only)return toast('恢复的候选仅供阅读，请按当前稿件生成新候选');
 if(draft.materials_read_only&&!isAnalysisCandidate(draft))return toast(draft.material_issue||'使用的资料已变化，请生成新候选');
 if(draft.task==='chapter_analysis')return actions.chapterAnalysis();
 if(draft.task==='semantic_review')return actions.reviewChapter();
 if(adopting||['accepted','rejected'].includes(draft.decision))return;
 if(draft.truncated)return toast('候选输出不完整，不能采纳；请从任务中心重试');
 const outputIssues=candidateOutputIssues(draft);if(outputIssues.length)return toast(outputIssues.join('；'));
 const source=draft.source;const c=chapter();
 if(!source||source.project_id!==requestedProject||source.branch_id!==(state.branch_id||'main')||source.chapter_id!==c?.id)return toast('这份候选属于其他章节或版本，请回到来源章节');
 if(c.revision_id!==source.source_revision_id||await sha256(c.body)!==source.source_body_hash)return toast('原稿已修改，这份候选保留供参考；请按新稿重新生成');
 showCandidateDifference(draft);
}
function showCandidateDifference(draft){
 const source=draft.source;const wrap=document.createElement('div');wrap.className='modal-backdrop';
 const before=source.mode==='append'?'（在原稿末尾添加）':source.range?.quote||'';
 const target={outline:'故事总纲',scene:'本章场景计划',summary:'章节回顾与下一章任务',check:'本章审稿记录',brainstorm:'创意方向'}[draft.task]||'选中正文';
 wrap.innerHTML=`<div class="modal candidate-modal"><h3>确认${source.mode==='structure'?target:'改稿'}</h3>
 <p class="modal-help">《${esc(chapter(source.chapter_id)?.title||'来源章节')}》 · ${source.mode==='structure'?target:`字符 ${source.range.start}—${source.range.end}`}</p>
 <div class="candidate-columns"><section><h4>修改前</h4><pre>${esc(before)}</pre></section><section><h4>候选内容</h4><pre>${esc(draft.text)}</pre></section></div>
 <div class="modal-actions"><button class="ghost-btn" data-close>继续比较</button><button class="primary-btn" data-apply>确认采纳</button></div></div>`;
 document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-apply]').onclick=async()=>{
  if(adopting||draft.decision==='accepted')return;
  adopting=true;wrap.querySelector('[data-apply]').disabled=true;displayCandidate(draft);
  try{
   const current=chapter(source.chapter_id);
   if(source.project_id!==requestedProject||source.branch_id!==(state.branch_id||'main')||!current||
    current.revision_id!==source.source_revision_id||await sha256(current.body)!==source.source_body_hash)throw new Error('原稿在比较期间有变化，请重新生成');
   const replacement=source.mode==='append'?`${current.body?'\n\n':''}${draft.text}`:draft.text;
   let structured=draft.structured;
   if(source.mode==='structure'&&draft.text!==draft.originalText){
    try{structured=JSON.parse(draft.text)}catch{throw new Error('结构候选格式有误，请保持字段完整或重新生成')}
   }
   const result=await durableOperation(`${API_BASE}/ai/runs/${encodeURIComponent(draft.ai_run_id)}/apply`,'POST',{
    ...source,source_body_hash:source.source_body_hash,start:source.range.start,end:source.range.end,range:source.range,
    replacement,structured,candidate_hash:await sha256(draft.text),decision:draft.text===draft.originalText?'accepted':'edited',
    expected_head:confirmedState?.head_revision,reason:'作者查看差异后确认采纳'});
   if(result.applied===false&&!result.idempotent)throw new Error('候选未应用，当前稿件保持原样');
   draft.decision='accepted';persistCandidates();rememberCandidateView(null);wrap.remove();render();toast(result.idempotent?'这份候选已采纳过':'候选已经保存');
  }catch(error){toast(`采纳未完成：${error.message}`);wrap.querySelector('[data-apply]').disabled=false}
  finally{adopting=false;displayCandidate(activeCandidate())}
 };
}
async function clearAI(){
 const draft=aiDraft;if(!draft||['accepted','rejected'].includes(draft.decision))return;
 if(terminalCandidateFailure(draft))return toast('任务已结束，保留的片段仅供阅读和复制');
 if(draft.restored_read_only)return toast('恢复的候选仅供阅读，请按当前稿件生成新候选');
 if(!draft.complete)return cancelJob(draft.job_id);
 try{
  if(backendOnline)await durableOperation(`${API_BASE}/ai/decision`,'POST',{ai_run_id:draft.ai_run_id,decision:'rejected',reason:'作者拒绝候选'});
  draft.decision='rejected';persistCandidates();rememberCandidateView(null);displayCandidate(activeCandidate());toast('候选已拒绝，原稿保持原样');
 }catch(error){toast(`拒绝未保存：${error.message}`)}
}
function scheduleQueue(delay=700){clearTimeout(queueTimer);queueTimer=setTimeout(refreshQueue,delay)}
async function refreshQueue(){
 if(queueLoading)return;
 if(!backendOnline){scheduleQueue(4000);return}
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main';queueLoading=true;
 try{
  const response=await fetch(`${API_BASE}/ai/queue?limit=100`);const data=await response.json();
  if(!response.ok)throw new Error(data.error||'任务列表读取失败');
  if(owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main'))return;
  queueJobs=(data.jobs||[]).filter(isWritingCandidate);
  for(const job of [...queueJobs].reverse()){
   const draft=receiveCandidateJob(job);
   if(draft&&(['queued','running'].includes(job.status)||
    terminalCandidateFailure(draft)&&!draft.terminal_snapshot&&!draft.terminal_replay_complete))startRunStream(job,draft,owner);
  }
  displayCandidate(activeCandidate());
  persistCandidates();
  if(view==='tasks')renderTaskList();
  updateTaskCount();
 }catch(error){if(view==='tasks'){const host=document.querySelector('#taskList');if(host)host.innerHTML=`<div class="empty">${esc(error.message)}</div>`}}
 finally{queueLoading=false;scheduleQueue(queueJobs.some(job=>['queued','running'].includes(job.status))?650:4000)}
}
function parseSSE(block){
 const lines=block.split(/\r?\n/);const event=lines.find(line=>line.startsWith('event:'))?.slice(6).trim()||'message';
 const raw=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
 try{return {event,data:JSON.parse(raw)}}catch{return null}
}
async function startRunStream(job,draft,owner){
 if(streamReaders.has(job.id)||draft.complete||draft.terminal_snapshot||draft.terminal_replay_complete)return;
 const branch=state.branch_id||state.project?.branch_id||'main';
 const controller=new AbortController();streamReaders.set(job.id,controller);
 let lastReplaySequence=null,finished=false;
 const current=()=>owner===requestedProject&&branch===(state.branch_id||state.project?.branch_id||'main')&&
  candidateStore.get(job.ai_run_id)===draft&&draft.job_id===job.id&&draft.ai_run_id===job.ai_run_id&&candidateScopeCurrent(draft);
 const accept=block=>{
  const event=parseSSE(block);if(!event||!current()||event.data?.ai_run_id!==job.ai_run_id)return;
  if(event.event==='run'&&event.data.streaming==='ledger_replay'&&
   Number.isInteger(event.data.last_seq)&&event.data.last_seq>=0){
   lastReplaySequence=event.data.last_seq;
   if(terminalCandidateFailure(draft)&&event.data.status==='unknown'){
    draft.completion_state='unknown';persistCandidates();
    if(aiDraft===draft)displayCandidate(draft);
   }
  }
  if(event.event==='delta'&&!draft.complete&&!draft.terminal_snapshot&&
   (['queued','running'].includes(draft.job_status)||terminalCandidateFailure(draft))&&
   Number.isInteger(event.data.seq)&&event.data.seq>Number(draft.last_seq||0)&&typeof event.data.text==='string'){
   draft.last_seq=event.data.seq;draft.text+=event.data.text;persistCandidates();
   if(activeChapter===draft.source?.chapter_id&&candidateScopeCurrent(draft)&&(!aiDraft||aiDraft.ai_run_id===draft.ai_run_id))displayCandidate(draft);
  }
  if(event.event==='done'){
   if(Number.isInteger(event.data.last_seq)&&event.data.last_seq>=0)lastReplaySequence=event.data.last_seq;
   scheduleQueue(0);
  }
 };
 try{
  const response=await fetch(`${API_BASE}/ai/runs/${encodeURIComponent(job.ai_run_id)}/stream?after_seq=${draft.last_seq||0}`,
   {headers:{Accept:'text/event-stream','X-Moyu-Project':owner,'X-Moyu-Branch':branch},signal:controller.signal});
  if(!response.ok||!response.body)return;
  const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
  while(true){
   const {value,done}=await reader.read();if(done){finished=true;break}
   buffer+=decoder.decode(value,{stream:true});const blocks=buffer.split(/\r?\n\r?\n/);buffer=blocks.pop();
   for(const block of blocks)accept(block);
  }
  if(buffer.trim())accept(buffer);
  if(finished&&current()&&terminalCandidateFailure(draft)&&lastReplaySequence!==null&&
   Number(draft.last_seq||0)>=lastReplaySequence){draft.terminal_replay_complete=true;persistCandidates()}
  else if(finished&&current()&&terminalCandidateFailure(draft))scheduleQueue(0);
 }catch(error){if(error.name!=='AbortError')draft.streamError=terminalCandidateFailure(draft)?
  '已保存的片段暂未读全，稍后可继续查看':'连接暂时中断，任务仍在本机继续'}
 finally{if(streamReaders.get(job.id)===controller)streamReaders.delete(job.id)}
}
function taskStatus(status){return ({queued:'排队中',running:'生成中',success:'候选已完成',failed:'失败',canceled:'已停止',unknown:'执行结果未知',interrupted:'已中断'})[status]||status}
function updateTaskCount(){const el=document.querySelector('#taskCount');if(el){const n=queueJobs.filter(j=>['queued','running'].includes(j.status)).length;el.textContent=n?String(n):''}}
async function cancelJob(id){
 if(!id)return;
 try{await durableOperation(`${API_BASE}/ai/jobs/${encodeURIComponent(id)}/cancel`);streamReaders.get(id)?.abort();scheduleQueue(0);toast('停止请求已保存，迟到结果不会进入正文')}
 catch(error){toast(`停止失败：${error.message}`)}
}
async function retryJob(id){
 try{await durableOperation(`${API_BASE}/ai/jobs/${encodeURIComponent(id)}/retry`,'POST',{mode:'original'});scheduleQueue(0);toast('重试已进入任务列表')}
 catch(error){toast(`重试未开始：${error.message}`)}
}
function renderTaskList(){
 const host=document.querySelector('#taskList');if(!host)return;
 host.innerHTML=queueJobs.filter(isWritingCandidate).map(job=>{
  const c=chapter(job.chapter_id);const draft=candidateStore.get(job.ai_run_id);
  const planning=job.planning_workflow;
  const revision=job.candidate_revision||draft?.candidate_revision;
  const running=['queued','running'].includes(job.status);const error=job.error||draft?.error||'';
  const incomplete=['failed','canceled','unknown','interrupted'].includes(job.status);
  const billing=job.result?.billing_state||draft?.billing_state;
  const retryable=!planning&&!job.restored_read_only&&!job.materials_read_only&&['failed','canceled'].includes(job.status)&&
   ['usage_reported','not_dispatched'].includes(billing)&&job.result?.completion_state!=='unknown'&&
   draft?.completion_state!=='unknown';
  return `<article class="task-row"><div><strong>${esc(revision?'整理候选':({continue:'续写',polish:'润色',outline:'规划',scene:'场景计划',summary:'章节回顾',check:'连续性检查',chapter_analysis:'章后记忆',semantic_review:'故事审稿'})[job.task]||job.task)}</strong>
  <span class="record-tag">${esc(taskStatus(draft?.completion_state==='unknown'?'unknown':job.status))}</span><p>${esc(planning?'创作规划':c?.title||'来源章节')} · ${esc(job.created_at||'')}</p>
  ${revision?`<p>整理原候选 ${revision.parent_characters??'未知'} 字 · ${esc(AI_TASK_NAMES[job.task]||job.task)}</p>`:''}
  ${error?`<p class="check-bad">${esc(error)}</p>`:''}${draft?.text?`<p class="task-preview">${esc(draft.text.slice(0,160))}</p>`:''}</div>
  <div class="task-actions">${running?`<button class="ghost-btn" data-task-cancel="${safeId(job.id)}">停止</button>`:''}
  ${planning?`<button class="primary-btn" data-task-planning="${safeId(planning.id)}">查看创作规划</button>`:
   job.status==='success'||incomplete?`<button class="primary-btn" data-task-open="${safeId(job.ai_run_id)}">${incomplete?(draft?.text?'查看片段':'查看记录'):['accepted','partial','edited','rejected'].includes(job.author_decision)?'查看记录':'查看候选'}</button>`:''}
  ${retryable?`<button class="ghost-btn" data-task-retry="${safeId(job.id)}">按原任务重试</button>`:''}</div></article>`;
 }).join('')||'<div class="empty">还没有生成任务。</div>';
 host.querySelectorAll('[data-task-cancel]').forEach(button=>button.onclick=()=>cancelJob(button.dataset.taskCancel));
 host.querySelectorAll('[data-task-retry]').forEach(button=>button.onclick=()=>retryJob(button.dataset.taskRetry));
 host.querySelectorAll('[data-task-planning]').forEach(button=>button.onclick=()=>actions.openPlanning(button.dataset.taskPlanning));
 host.querySelectorAll('[data-task-open]').forEach(button=>button.onclick=()=>{
  const draft=candidateStore.get(button.dataset.taskOpen);if(!draft?.source)return toast('该旧任务没有原稿身份，请重新生成');
  if(!candidateScopeCurrent(draft))return toast('作品或版本已切换，请回到候选的来源作品');
  if(!chapter(draft.source.chapter_id))return toast('来源章节已不存在，无法打开候选');
  activeChapter=draft.source.chapter_id;rememberCandidateView(draft);view='editor';render();
  if(draft.complete&&draft.task==='chapter_analysis')actions.chapterAnalysis();
  else if(draft.complete&&draft.task==='semantic_review')actions.reviewChapter();else displayCandidate(draft);
 });
}
function captureMemories(){let added=false;const seen=new Set(state.memory.map(m=>m.key)); state.chapters.forEach(c=>{const tags=[...c.body.matchAll(/(?:【|\[)(人物|物品|地点|伏笔|剧情)(?:】|\])\s*([^\n，。]+)/g)]; tags.forEach(t=>{const key=t[1]+':'+t[2].trim(); if(!seen.has(key)){const start=Array.from(c.body.slice(0,t.index??0)).length;const end=start+Array.from(t[0]).length;state.memory.push({id:crypto.randomUUID(),key,type:t[1],name:t[2].trim(),source:c.title,source_chapter_id:c.id,source_quote:t[0],source_start:start,source_end:end,status:'待确认',confidence:'候选',note:'由章节标记自动捕获，确认后才进入正式记忆。'}); seen.add(key);added=true}})});return added}
function save(){
 if(!draftHasContent&&MoyuSync.equal({...state,branch_id:null},{...starter,branch_id:null})){
  saveIndicator();return;
 }
 draftHasContent=true;mutationSeq++;saveError='';cacheDraft();clearTimeout(syncTimer);
 if(backendOnline&&!mergePending&&!independentWrite)syncTimer=setTimeout(syncBackend,180);saveIndicator();
}
function esc(s=''){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function safeId(v){const s=String(v??'');return /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(s)?s:''}
function localSearchResults(query){
 const needle=String(query||'').trim().toLowerCase();
 if(!needle)return [];
 const chapters=state.chapters.map(c=>({type:'chapter',id:c.id,title:c.title,text:c.body,
  source_chapter_id:c.id,source_chapter_title:c.title,searchText:[c.title,c.body,c.chapter_goal,c.recap,c.hook].join(' ')}));
 const settings=state.records.map(r=>({type:'setting',id:r.id,title:`${r.type} · ${r.name}`,text:r.description,
  searchText:[r.type,r.name,r.description,Array.isArray(r.aliases)?r.aliases.join(' '):''].join(' ')}));
 return [...chapters,...settings].filter(item=>
  `${item.title||''} ${item.text||''} ${item.searchText||''}`.toLowerCase().includes(needle));
}
function searchResultEvidence(item){
 const evidence=item?.source_evidence&&typeof item.source_evidence==='object'?item.source_evidence:{};
 const range=Array.isArray(evidence.source_ranges)&&evidence.source_ranges[0]&&typeof evidence.source_ranges[0]==='object'
  ?evidence.source_ranges[0]:null;
 const quote=String(evidence.source_quote||range?.quote||'');
 const start=Number.isInteger(range?.start)?range.start:null;
 const end=Number.isInteger(range?.end)?range.end:null;
 const chapterId=String(evidence.source_chapter_id||item?.source_chapter_id||'');
 return {evidence,range,quote,start,end,chapterId};
}
function searchResultMarkup(item){
 const {evidence,range,quote,start,end,chapterId}=searchResultEvidence(item);
 const canJump=item?.type==='chapter'&&safeId(chapterId)&&quote&&start!==null&&end!==null&&
  !!evidence.source_revision_id&&!!evidence.source_body_hash;
 const evidenceLine=quote?`<div class="search-evidence"><span>原文证据：</span><q>${esc(quote)}</q>${start!==null&&end!==null?`<small>字符 ${start}–${end}</small>`:''}${canJump?`<button class="ghost-btn search-jump" type="button" data-search-jump="${safeId(chapterId)}" data-search-start="${start}" data-search-end="${end}" data-search-quote="${esc(quote)}" data-search-revision="${esc(evidence.source_revision_id)}" data-search-body-hash="${esc(evidence.source_body_hash)}">查看原文</button>`:''}</div>`:'';
 const sourceLine=item.source_chapter_title?`<small>来源章节：${esc(item.source_chapter_title)}</small>`:'';
 const statusLine=item.status?`<small> · ${esc(item.status)}</small>`:'';
 const evidenceState=evidence.status&&evidence.status!=='valid'?`<small class="search-evidence-state"> · 证据${esc(evidence.status)}</small>`:'';
 return `<article class="result" data-search-result-id="${safeId(item.id)}" data-search-result-type="${esc(item.type||'')}"><strong>${esc(item.title)}</strong><span class="record-tag">${esc(item.type||'结果')}</span><p>${esc(item.text||'')}</p>${sourceLine}${statusLine}${evidenceState}${evidenceLine}</article>`;
}
function searchResultsMarkup(results,query){
 return results.length?results.map(searchResultMarkup).join(''):`<div class="empty">没有找到“${esc(query)}”</div>`;
}
async function openSearchEvidence(button){
 const chapterId=button?.dataset.searchJump,c=chapter(chapterId);
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main',body=c?.body;
 const start=Number(button?.dataset.searchStart),end=Number(button?.dataset.searchEnd),quote=button?.dataset.searchQuote||'';
 if(!c||!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>Array.from(body).length||
  Array.from(body).slice(start,end).join('')!==quote||c.revision_id!==button.dataset.searchRevision||
  await sha256(body)!==button.dataset.searchBodyHash||owner!==requestedProject||
  branch!==(state.branch_id||state.project?.branch_id||'main')||chapter(chapterId)!==c||c.body!==body)
  return toast('原文已经变化，请重新搜索后再定位');
 activeChapter=c.id;storage.set(cacheKey(':active'),c.id);view='editor';aiDraft=null;render();
 const input=document.querySelector('#chapterBody');if(!input)return;
 input.focus();input.setSelectionRange(MoyuSync.utf16Offset(c.body,start),MoyuSync.utf16Offset(c.body,end));
 input.scrollTop=Math.max(0,Math.floor(start/40)*26-100);captureSelection();input.scrollIntoView({block:'center'});
}
async function runSearch(){
 const q=document.querySelector('#searchInput')?.value.trim(),box=document.querySelector('#searchResults');
 const sequence=++searchRequestSeq,owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main';
 if(!q){if(box)box.innerHTML='<div class="empty">输入关键词，开始检索。</div>';return}
 let results=[],failure=null;
 if(backendOnline){
  try{
   const response=await fetch(`${API_BASE}/search?q=${encodeURIComponent(q)}&limit=100`,{headers:{Accept:'application/json'}});
   const data=await response.json().catch(error=>{
    if(!response.ok)return {};
    throw new Error(`搜索响应格式无效：${error.message}`);
   });
   if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
   if(!data||typeof data!=='object'||!Array.isArray(data.results))
    throw new Error('搜索响应格式无效');
   results=data.results;
  }catch(error){failure=error;results=localSearchResults(q)}
 }else results=localSearchResults(q);
 if(sequence!==searchRequestSeq||owner!==requestedProject||branch!==(state.branch_id||state.project?.branch_id||'main')||
  view!=='search'||document.querySelector('#searchResults')!==box||
  document.querySelector('#searchInput')?.value.trim()!==q)return;
 if(failure)toast(`后端搜索失败，已使用本地草稿：${failure.message}`);
 if(box)box.innerHTML=searchResultsMarkup(results,q);
}
function headingText(v){return String(v??'').replace(/[\r\n]+/g,' ').trim()||'未命名'}
function commentText(v){return headingText(v).replace(/-->/g,'—>')}
function yamlString(v){return JSON.stringify(String(v??''))}
function downloadText(filename,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function exportFilename(ext){let base=headingText(state.project?.title||'小说项目').replace(/[\u0000-\u001f\\/:*?"<>|]+/g,'_').slice(0,80).replace(/^[. ]+|[. ]+$/g,'')||'小说项目';const stem=base.replace(/\.[^.]*$/,'');if(/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem))base=`作品_${base}`;return `${base}.${ext}`}
function exportableChapters(){const chapters=state.chapters||[];const last=chapters[chapters.length-1];return last&&last.status==='草稿'&&!String(last.body||'').trim()?chapters.slice(0,-1):chapters}
function finalOverrideKey(){const p={project:state.project,chapters:state.chapters,memory:state.memory,plot:state.plot};return `${KEY}:final-override:${aiCandidateHash(JSON.stringify(p))}`}
function finalOverride(){try{const raw=localStorage.getItem(finalOverrideKey());return raw?JSON.parse(raw):null}catch{return null}}
function finalGate(){const issues=finalizationIssues();const override=finalOverride();return {issues,override,allowed:!issues.length||!!override}}
function markdownExport(){
 const gate=finalGate();
 if(!gate.allowed){showFinalizationCheck();toast('终稿导出已阻断：请先处理检查项，或填写作者覆盖理由。');return}
 const p=state.project||{};
 const chapters=exportableChapters();
 const lossManifest=[{feature:'rich_media',status:'unsupported',detail:'当前状态模型只导出纯文本与基本元数据。'},{feature:'docx_pdf_layout',status:'unsupported',detail:'Markdown 不包含分页、版式和字体信息。'},{feature:'ai_provenance',status:'partial',detail:'AI 运行记录保留在本地 SQLite，不嵌入正文。'}];
 const lines=['---',`format: ${yamlString('moyu-markdown-v1')}`,`title: ${yamlString(p.title)}`,`genre: ${yamlString(p.genre)}`,`summary: ${yamlString(p.summary)}`,`goal: ${Number(p.goal)||0}`,`book_status: ${yamlString(p.book_status||'writing')}`,`exported_at: ${yamlString(new Date().toISOString())}`,`chapter_count: ${chapters.length}`,`loss_manifest: ${yamlString(JSON.stringify(lossManifest))}`,'---','',`# ${headingText(p.title)}`,''];
 if(String(p.summary||'').trim())lines.push(String(p.summary).trim(),'');
 lines.push('## 章节','');
 chapters.forEach((c,i)=>{lines.push(`### ${i+1}. ${headingText(c.title)}`,`<!-- moyu-chapter id="${safeId(c.id)}" status="${commentText(c.status)}" review="${commentText(c.review_status||'unreviewed')}" updated="${Number(c.updated)||0}" -->`);if(c.chapter_goal)lines.push(`**本章目标：** ${String(c.chapter_goal).trim()}`);if(c.recap)lines.push(`**章节回顾：** ${String(c.recap).trim()}`);if(c.next_brief)lines.push(`**下一章任务：** ${String(c.next_brief).trim()}`);lines.push('',String(c.body||''),'');});
 lines.push('## 设定卡','');
 state.records.forEach(r=>{lines.push(`### ${headingText(r.type)} · ${headingText(r.name)}`,`<!-- moyu-record id="${safeId(r.id)}" type="${commentText(r.type)}" -->`,'',String(r.description||''),'');});
 lines.push('## 情节与伏笔','');
 for(const [key,label] of [['setup','埋下线索'],['develop','推进中'],['payoff','已回收']]){lines.push(`### ${label}`,'');for(const item of state.plot?.[key]||[]){const text=typeof item==='string'?item:item?.text||'';const id=typeof item==='string'?'':safeId(item?.id);lines.push(`- ${String(text).trim()}${id?` <!-- moyu-plot id="${id}" stage="${key}" -->`:''}`)}lines.push('')}
 lines.push('## 记忆','');
 state.memory.forEach(m=>{lines.push(`- **${headingText(m.type)} · ${headingText(m.name)}**：${String(m.note||'')}`,`  <!-- moyu-memory id="${safeId(m.id)}" status="${commentText(m.status)}" source="${commentText(m.source)}" -->`)});
 downloadText(exportFilename('md'),lines.join('\n'),'text/markdown;charset=utf-8');
 toast('已导出 Markdown 终稿');
}
async function epubExport(){
 let gate;try{gate=await verifiedFinalGate()}catch(error){toast(error.message);return}
 if(!gate.allowed){showFinalizationCheck();toast('EPUB 导出已阻断：请先处理终稿检查项，或填写作者覆盖理由。');return}
 if(!backendOnline){toast('EPUB 导出需要启动 SQLite 服务；当前可先导出 Markdown。');return}
 try{const headers={};if(gate.override?.reason)headers['X-Moyu-Export-Override']=encodeURIComponent(gate.override.reason);const response=await fetch(`${API_BASE}/export/epub`,{headers});if(!response.ok){const detail=await response.json().catch(()=>({}));throw new Error(detail.error||`HTTP ${response.status}`)}const blob=await response.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=exportFilename('epub');document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出 EPUB（包内包含损失清单）')}catch(error){toast(`EPUB 导出失败：${error.message}`)}
}
function finalizationIssues(){
 const issues=[];const p=state.project||{};
 if(!String(p.title||'').trim()||p.title==='未命名长篇')issues.push('作品还没有正式标题。');
 if(!String(p.summary||'').trim())issues.push('还没有填写一句话简介。');
 const chapters=state.chapters||[];const exportChapters=exportableChapters();if(!exportChapters.length)issues.push('作品没有可导出的章节正文。');
 chapters.forEach((c,i)=>{const chars=String(c.body||'').replace(/\s/g,'').length;const placeholder=i===chapters.length-1&&c.status==='草稿'&&!chars;if(placeholder)return;if(!chars)issues.push(`第 ${i+1} 章《${headingText(c.title)}》没有正文。`);if(c.status!=='完成')issues.push(`第 ${i+1} 章《${headingText(c.title)}》状态仍为“${headingText(c.status)}”。`);if(chars>0&&chars<60)issues.push(`第 ${i+1} 章《${headingText(c.title)}》正文少于 60 字，请确认是否完整。`);if(c.status==='完成'&&c.review_status!=='passed')issues.push(`第 ${i+1} 章《${headingText(c.title)}》尚未通过本章审查。`)});
 const pending=state.memory.filter(m=>!['已拒绝','rejected','superseded'].includes(m.status)&&(m.status!=='已确认'||m.stale||m.source_span_status!=='valid'));if(pending.length)issues.push(`有 ${pending.length} 条记忆尚未确认或证据已失效。`);
 const openPlot=['setup','develop'].reduce((n,k)=>n+(state.plot?.[k]?.length||0),0);if(openPlot)issues.push(`还有 ${openPlot} 条伏笔处于未回收状态。`);
 return issues;
}
async function verifiedFinalGate(){
 const owner=requestedProject,branch=state.branch_id||'main';
 if(!backendOnline)throw new Error('连接恢复后才能核验终稿；现在可以先导出稿件 JSON');
 if(!await syncBackend())throw new Error('当前稿件需要先保存或合并');
 const sequence=mutationSeq;
 const response=await fetch('/api/export/manifest',{headers:{Accept:'application/json'}});
 const manifest=await response.json();
 if(!response.ok)throw new Error(manifest.error||'终稿检查未完成');
 if(owner!==requestedProject||branch!==(state.branch_id||'main')||
  manifest.project_id!==owner||manifest.branch_id!==branch)throw new Error('作品已经切换，请重新检查');
 if(sequence!==mutationSeq||isDirty())throw new Error('检查期间稿件发生变化，请重新检查');
 if(!Array.isArray(manifest.finalization?.issues))throw new Error('未取得完整终稿检查结果，请重试');
 const issues=manifest.finalization.issues,storedOverride=finalOverride();
 const override=storedOverride&&MoyuSync.equal(storedOverride.issues,issues)?storedOverride:null;
 return {issues,override,allowed:!issues.length||!!override};
}
async function showFinalizationCheck(){
 let gate;try{gate=await verifiedFinalGate()}catch(error){toast(error.message);return}
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main',overrideKey=finalOverrideKey();
 const issues=gate.issues;const wrap=document.createElement('div');wrap.className='modal-backdrop';
 const body=issues.length?`<p class="modal-help">终稿导出默认被阻断。请处理以下项目；覆盖时必须留下作者理由。</p><ul class="check-list">${issues.map(x=>`<li class="check-bad">阻断 · ${esc(x)}</li>`).join('')}</ul>${gate.override?`<p class="modal-help override-note">已记录作者覆盖：${esc(gate.override.reason||'未填写理由')} · ${esc(gate.override.at||'')}</p>`:''}`:'<p class="modal-help check-ok">已通过基础终稿检查，可以导出并标记全书完稿。</p>';
 const complete=state.project.book_status==='complete'?'<button class="ghost-btn" data-reopen>恢复写作</button>':`<button class="ghost-btn" data-complete ${issues.length&&!gate.override?'disabled':''}>标记全书完稿</button>`;
 const exportButton=`<button class="primary-btn" data-export ${!gate.allowed?'disabled':''}>${!gate.allowed?'处理阻断项后导出':'导出 Markdown'}</button>`;
 const overrideButton=issues.length&&!gate.override?'<button class="ghost-btn" data-override>作者覆盖并记录原因</button>':'';
 wrap.innerHTML=`<div class="modal"><h3>终稿检查</h3>${body}<div class="modal-actions"><button class="ghost-btn" data-close>关闭</button>${overrideButton}${complete}${exportButton}</div></div>`;document.body.append(wrap);
 wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
 wrap.querySelector('[data-override]')?.addEventListener('click',async()=>{
  const stillCurrent=()=>wrap.isConnected&&owner===requestedProject&&
   branch===(state.branch_id||state.project?.branch_id||'main')&&overrideKey===finalOverrideKey();
  if(!stillCurrent()){wrap.remove();return}
  const reason=await requestText('请填写作者覆盖理由（至少 8 个字符）。此理由会和当前稿件指纹一起保存在本机。','',
   {title:'作者覆盖理由',current:stillCurrent});
  if(!stillCurrent()){wrap.remove();return}
  if(reason===null)return;
  if(Array.from(reason.trim()).length<8)return toast('覆盖理由至少需要 8 个字符');
  try{
   localStorage.setItem(overrideKey,JSON.stringify({reason:reason.trim(),at:new Date().toLocaleString(),issues}));
   wrap.remove();showFinalizationCheck();toast('已记录作者覆盖理由；本稿可导出。');
  }catch{toast('无法保存覆盖理由，请先导出 JSON 备份')}
 });
 wrap.querySelector('[data-export]').onclick=()=>{wrap.remove();markdownExport()};
 wrap.querySelector('[data-complete]')?.addEventListener('click',async event=>{
  const button=event.currentTarget;button.disabled=true;
  try{
   if(!(await verifiedFinalGate()).allowed)throw new Error('还有终稿阻断项未处理，请重新检查');
   state.project.book_status='complete';save();
   if(!await syncBackend())throw new Error('完稿状态尚未保存，请保留页面重试');
   wrap.remove();render();toast('已标记全书完稿');
  }catch(error){toast(error.message);button.disabled=false}
 });
 wrap.querySelector('[data-reopen]')?.addEventListener('click',()=>{state.project.book_status='writing';save();wrap.remove();render();toast('已恢复写作状态')});
}
function words(){return state.chapters.reduce((n,c)=>n+c.body.replace(/\s/g,'').length,0)}
function chapter(id=activeChapter){return state.chapters.find(c=>c.id===id)}
function render(){textRequestCancel?.();dismissMemoryEvidence();cacheInstructionDraft();syncReferenceScope();const added=captureMemories();aiDraft=activeCandidate();aiRunId=aiDraft?.ai_run_id||null;
 document.querySelector('#projectTitle').textContent=state.project.title;document.querySelector('#projectTitle').title=state.project.title;document.querySelector('#projectMeta').textContent=`${state.chapters.length} 章 · ${words()} 字`;
 document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 document.querySelector('#main').innerHTML=(views[view]||views.dashboard)();bind();ensureFinishButton();saveIndicator();updateTaskCount();
 if(view==='editor')displayCandidate(aiDraft);if(view==='tasks'){renderTaskList();scheduleQueue(0)}
 if(added)save();if(view==='story'&&backendOnline&&(!storyLoaded||storyLoadedChapter!==activeChapter))actions.refreshStory(true)}
function ensureFinishButton(){if(view!=='editor'||state.project.book_status==='complete')return;const footer=document.querySelector('.editor-footer');if(!footer||footer.querySelector('[data-action="finishChapter"]'))return;const button=document.createElement('button');button.className='ghost-btn';button.dataset.action='finishChapter';button.textContent='完成本章，写下一章';button.onclick=()=>actions.finishChapter();footer.append(button)}
function isFreshProject(){const first=state.chapters[0];return state.project.title==='未命名长篇'&&!state.project.summary&&!state.project.genre&&state.chapters.length===1&&first?.title==='第一章 还没有开始的故事'&&first?.body===starter.chapters[0].body}
function workflowCard(){if(state.project.book_status==='complete')return `<section class="workflow-card workflow-complete"><div><span class="workflow-kicker">创作路径 · 已完成</span><h3>全书已完稿</h3><p>作品已进入收尾状态。你可以先做终稿检查，再导出 Markdown；需要继续修改时可恢复写作。</p></div><button class="primary-btn" data-action="finalCheck">终稿检查</button></section>`;if(isFreshProject())return `<section class="workflow-card workflow-start"><div><span class="workflow-kicker">创作路径 · 第 1 步</span><h3>先把故事说清楚，再开始写第一幕</h3><p>用一分钟填写一句话故事设定、主角和眼前阻力，墨屿会建立故事骨架并把你带进第一章。</p><ol class="workflow-steps"><li>定义故事与主角</li><li>写出第一幕</li><li>确认记忆，再继续下一章</li></ol></div><button class="primary-btn" data-action="startWriting">开始创作</button></section>`;const completed=state.chapters.filter(c=>c.status==='完成').length;const current=state.chapters.find(c=>c.status!=='完成')||state.chapters[state.chapters.length-1];const label=current?.body.trim()?'继续写作':'开始第一幕';const copy=current?.body.trim()?`回到《${current.title}》，把本场景推进到一个不可撤回的选择。`:`《${current?.title||'下一章'}》还没有正文，从一个正在发生的动作开始。`;return `<section class="workflow-card"><div><span class="workflow-kicker">创作路径 · ${completed}/${state.chapters.length} 章完成</span><h3>${completed?`已完成 ${completed} 章，继续保持故事向前`:'故事骨架已建立，开始第一幕'}</h3><p>${copy}</p><ol class="workflow-steps"><li>写作：完成本章任务卡</li><li>审查：确认连续性与记忆</li><li>归档：进入下一章</li></ol></div><button class="primary-btn" data-action="continueWriting">${label}</button></section>`}
// Escape the one workflow summary that is assembled from a user-controlled title.
// The legacy template above predates the centralized `esc` helper.
const _unsafeWorkflowCard=workflowCard;
workflowCard=()=>{const current=state.chapters.find(c=>c.status!=='完成')||state.chapters[state.chapters.length-1];const html=_unsafeWorkflowCard();return current?.title?html.split(current.title).join(esc(current.title)):html};
function storyEntityOptions(selected=''){
  const rows=state.records.filter(x=>x&&x.id);
  return rows.length?rows.map(x=>`<option value="${safeId(x.id)}" ${String(x.id)===String(selected)?'selected':''}>${esc(x.type||'设定')} · ${esc(x.name||'未命名')}</option>`).join(''):'<option value="">请先在设定库创建人物或势力</option>';
}
function storyChapterOptions(selected=''){
  return `<option value="">不绑定章节</option>${state.chapters.map((x,i)=>`<option value="${safeId(x.id)}" ${String(x.id)===String(selected)?'selected':''}>第 ${i+1} 章 · ${esc(x.title)}</option>`).join('')}`;
}
function storyEntityMultiOptions(selected=[]){
  const set=new Set(Array.isArray(selected)?selected:[]);
  return state.records.filter(x=>x&&x.id).map(x=>`<option value="${safeId(x.id)}" ${set.has(x.id)?'selected':''}>${esc(x.type||'设定')} · ${esc(x.name||'未命名')}</option>`).join('');
}
function storyPlotOptions(selected=''){
  const rows=['setup','develop','payoff'].flatMap(stage=>(state.plot?.[stage]||[]).map(x=>typeof x==='string'?{id:'',stage,text:x}:x)).filter(x=>x.id);
  return rows.length?rows.map(x=>`<option value="${safeId(x.id)}" ${String(x.id)===String(selected)?'selected':''}>${esc(x.stage)} · ${esc(x.text)}</option>`).join(''):'<option value="">请先在情节与伏笔中创建伏笔</option>';
}
function storyModal(title,body,onSubmit){
  const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.innerHTML=`<div class="modal story-modal"><h3>${title}</h3>${body}<div class="modal-actions"><button class="ghost-btn" data-close>取消</button><button class="primary-btn" data-ok>保存结构</button></div></div>`;document.body.append(wrap);
  wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
  wrap.querySelector('[data-ok]').onclick=async()=>{const button=wrap.querySelector('[data-ok]');button.disabled=true;try{await onSubmit(wrap);wrap.remove();await actions.refreshStory(true);toast('故事结构已保存')}catch(error){button.disabled=false;toast(`保存失败：${error.message}`)}};
  return wrap;
}
async function storyRequest(path,method,body){
  if(method&&method!=='GET')return durableOperation(path,method,body||{});
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json',Accept:'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
  return data;
}
function readStoryForm(wrap,kind){
  const value=id=>wrap.querySelector(`#${id}`)?.value.trim()||'';
  if(kind==='relationship')return {from_entity_id:value('storyFrom'),to_entity_id:value('storyTo'),relation_type:value('storyRelation')||'相关',note:value('storyRelationNote')};
  if(kind==='timeline')return {title:value('storyEventTitle'),event_time:value('storyEventTime'),chapter_id:value('storyEventChapter')||null,entity_ids:[...wrap.querySelector('#storyEventEntities')?.selectedOptions||[]].map(x=>x.value),summary:value('storyEventSummary'),status:value('storyEventStatus')||'planned',ordinal:Number(value('storyEventOrdinal')||0)};
  return {plot_id:value('storyPlot'),chapter_id:value('storyForeshadowChapter')||null,kind:value('storyForeshadowKind')||'plant',note:value('storyForeshadowNote')};
}
function relationshipModal(item){
  if(!backendOnline)return toast('启动 SQLite 服务后才能编辑故事结构');
  const x=item||{};return storyModal(item?'编辑人物关系':'新增人物关系',`<p class="modal-help">关系用于提醒 AI 谁和谁之间存在稳定的目标、冲突或依赖。</p><label>起点设定<select id="storyFrom">${storyEntityOptions(x.from_entity_id)}</select></label><label>终点设定<select id="storyTo">${storyEntityOptions(x.to_entity_id)}</select></label><label>关系类型<input id="storyRelation" value="${esc(x.relation_type||'相关')}" placeholder="盟友 / 敌对 / 亲属 / 欠债"></label><label>备注<textarea id="storyRelationNote" placeholder="关系如何形成，当前状态是什么？">${esc(x.note||'')}</textarea></label>`,async w=>{const data=readStoryForm(w,'relationship');if(!data.from_entity_id||!data.to_entity_id)throw new Error('请选择两个设定');if(data.from_entity_id===data.to_entity_id)throw new Error('起点和终点不能相同');await storyRequest(item?`/api/story/relationships/${encodeURIComponent(item.id)}`:'/api/story/relationships',item?'PATCH':'POST',item?data:data)});
}
function timelineModal(item){
  if(!backendOnline)return toast('启动 SQLite 服务后才能编辑故事结构');
  const x=item||{};return storyModal(item?'编辑时间线事件':'新增时间线事件',`<p class="modal-help">用事件记录“什么时候发生了什么”，帮助检查章节顺序与角色移动。</p><label>事件标题<input id="storyEventTitle" value="${esc(x.title||'')}" placeholder="例如：旧港停电，主角发现门缝里的信"></label><label>时间标记<input id="storyEventTime" value="${esc(x.event_time||'')}" placeholder="第一周·周三 / 纪元 2048"></label><label>关联章节<select id="storyEventChapter">${storyChapterOptions(x.chapter_id)}</select></label><label>涉及设定<select id="storyEventEntities" multiple size="4">${storyEntityMultiOptions(x.entity_ids)}</select></label><label>状态<select id="storyEventStatus"><option value="planned" ${x.status==='planned'?'selected':''}>计划</option><option value="canon" ${x.status==='canon'?'selected':''}>已发生</option><option value="uncertain" ${x.status==='uncertain'?'selected':''}>待确认</option></select></label><label>排序序号<input id="storyEventOrdinal" type="number" min="0" max="1000000" value="${Number(x.ordinal||0)}"></label><label>摘要<textarea id="storyEventSummary" placeholder="事件造成了什么可观察变化？">${esc(x.summary||'')}</textarea></label>`,async w=>{const data=readStoryForm(w,'timeline');if(!data.title)throw new Error('事件标题不能为空');await storyRequest(item?`/api/story/timeline/${encodeURIComponent(item.id)}`:'/api/story/timeline',item?'PATCH':'POST',data)});
}
async function foreshadowSourcePayload(data,quote){
 if(!quote.trim()){
  if(['develop','payoff'].includes(data.kind))throw new Error('推进或回收伏笔需要填写当前正文中的原句');
  return data;
 }
 const sourceChapter=chapter(data.chapter_id);
 if(!sourceChapter)throw new Error('请选择原句所在章节');
 let start=sourceChapter.body.indexOf(quote);
 if(start<0)throw new Error('该原句不在所选章节中');
 if(sourceChapter.body.indexOf(quote,start+1)>=0){
  if(savedSelection?.chapter_id!==sourceChapter.id||savedSelection.body!==sourceChapter.body||
   savedSelection.body.slice(savedSelection.start,savedSelection.end)!==quote)
   throw new Error('原句重复，请先在正文中选中具体一处，再打开此窗口');
  start=savedSelection.start;
 }
 return {...data,source_quote:quote,source_start:MoyuSync.codepointOffset(sourceChapter.body,start),
  source_end:MoyuSync.codepointOffset(sourceChapter.body,start+quote.length),source_body_hash:await sha256(sourceChapter.body)};
}
function foreshadowModal(item){
  if(!backendOnline)return toast('启动 SQLite 服务后才能编辑故事结构');
  const x=item||{};
  const selectedQuote=savedSelection?.chapter_id===activeChapter?savedSelection.body.slice(savedSelection.start,savedSelection.end):'';
  return storyModal(item?'编辑伏笔链接':'新增伏笔链接',`<p class="modal-help">把一个伏笔卡片绑定到埋下、推进或回收它的章节，形成可追踪生命周期。</p><label>伏笔卡片<select id="storyPlot">${storyPlotOptions(x.plot_id)}</select></label><label>生命周期<select id="storyForeshadowKind"><option value="plant" ${x.kind==='plant'?'selected':''}>埋下</option><option value="develop" ${x.kind==='develop'?'selected':''}>推进</option><option value="payoff" ${x.kind==='payoff'?'selected':''}>回收</option></select></label><label>关联章节<select id="storyForeshadowChapter">${storyChapterOptions(x.chapter_id||activeChapter)}</select></label><label>正文原句<textarea id="storyForeshadowQuote">${esc(x.source_quote||selectedQuote)}</textarea></label><label>备注<textarea id="storyForeshadowNote" placeholder="这一动作让读者知道什么，留下什么问题？">${esc(x.note||'')}</textarea></label>`,async w=>{
   let data=readStoryForm(w,'foreshadow');if(!data.plot_id)throw new Error('请先创建并选择伏笔卡片');
   data=await foreshadowSourcePayload(data,w.querySelector('#storyForeshadowQuote').value);
   await storyRequest(item?`/api/story/foreshadow-links/${encodeURIComponent(item.id)}`:'/api/story/foreshadow-links',item?'PATCH':'POST',data);
  });
}
function storyGraphSvg(relationships){
  const nodes=[];const seen=new Set();
  (relationships||[]).forEach(rel=>{
    [[rel.from_entity_id,rel.from_name,rel.from_kind],[rel.to_entity_id,rel.to_name,rel.to_kind]].forEach(([id,name,kind])=>{
      const key=String(id||name||'');if(!key||seen.has(key))return;seen.add(key);nodes.push({id:key,name:name||'未命名设定',kind:kind||'设定'});
    });
  });
  if(!nodes.length)return '<div class="empty">暂无足够关系生成图谱。先添加一条人物、势力或地点关系。</div>';
  const width=760;const columns=Math.min(4,Math.max(1,nodes.length));const rows=Math.ceil(nodes.length/columns);const height=Math.max(220,rows*116+40);const position=new Map(nodes.map((node,index)=>[node.id,{x:80+(index%columns)*(width-160)/Math.max(1,columns-1),y:58+Math.floor(index/columns)*116}]));
  const lines=(relationships||[]).map(rel=>{const from=position.get(String(rel.from_entity_id||rel.from_name||''));const to=position.get(String(rel.to_entity_id||rel.to_name||''));if(!from||!to)return '';const mid=(from.x+to.x)/2;const label=String(rel.relation_type||'相关').slice(0,18);return `<g class="story-edge"><path d="M ${from.x} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${to.x} ${to.y}" marker-end="url(#story-arrow)"></path><text x="${mid}" y="${(from.y+to.y)/2-6}" text-anchor="middle">${esc(label)}</text></g>`}).join('');
  const cards=nodes.map(node=>{const p=position.get(node.id);return `<g class="story-node" tabindex="0"><title>${esc(node.name)} · ${esc(node.kind)}</title><rect x="${p.x-64}" y="${p.y-25}" width="128" height="50" rx="10"></rect><text x="${p.x}" y="${p.y-2}" text-anchor="middle">${esc(String(node.name).slice(0,16))}</text><text class="story-node-kind" x="${p.x}" y="${p.y+16}" text-anchor="middle">${esc(String(node.kind).slice(0,10))}</text></g>`}).join('');
  return `<div class="story-graph-wrap"><svg class="story-graph" viewBox="0 0 ${width} ${height}" role="img" aria-label="人物关系图"><defs><marker id="story-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"></path></marker></defs>${lines}${cards}</svg><p class="story-graph-note">图谱只展示已保存的关系；线上的文字是关系类型，不代表因果先后。</p></div>`;
}
function storyTimelineHtml(events){
  if(!(events||[]).length)return '<div class="empty">暂无时间线事件。先记录一个改变局面的瞬间。</div>';
  return `<ol class="story-timeline">${events.map((event,index)=>{const chapterTitle=event.chapter_id?state.chapters.find(chapter=>chapter.id===event.chapter_id)?.title||'章节已删除':'未绑定章节';const status=event.status==='canon'?'已发生':event.status==='uncertain'?'待确认':'计划';const entities=(event.entity_ids||[]).map(id=>state.records.find(record=>record.id===id)?.name).filter(Boolean);return `<li class="story-timeline-item"><span class="story-timeline-dot" aria-hidden="true"></span><div class="story-timeline-content"><div class="story-timeline-heading"><span class="story-timeline-time">${esc(event.event_time||`事件 ${index+1}`)}</span><span class="story-scope-badge story-status-${esc(event.status||'planned')}">${status}</span></div><strong>${esc(event.title)}</strong><p>${esc(event.summary||'尚未填写事件摘要。')}</p><small>${esc(chapterTitle)}${entities.length?` · 涉及：${esc(entities.join('、'))}`:''}</small></div></li>`}).join('')}</ol>`;
}
function chapterNumberFromScope(value){if(typeof value==='number'&&Number.isFinite(value))return Math.trunc(value);const match=String(value||'').match(/(?:chapter|ch|第)?\s*(\d+)/i);return match?Number(match[1]):null}
function localKnowledgeReasons(item){
  const current=Math.max(1,state.chapters.findIndex(chapter=>chapter.id===activeChapter)+1);const scope=item?.knowledge_scope&&typeof item.knowledge_scope==='object'?item.knowledge_scope:{};const reasons=[];const start=chapterNumberFromScope(scope.available_from)??chapterNumberFromScope(item.valid_start);const end=chapterNumberFromScope(item.valid_end);if(start&&start>current)reasons.push('future_reveal');if(end&&end<current)reasons.push('state_expired');const branches=scope.branches||scope.branch_ids;if(Array.isArray(branches)&&branches.length&&!branches.map(String).includes('main'))reasons.push('branch_scope');const allowed=scope.chapters;if(Array.isArray(allowed)&&allowed.length&&!allowed.map(chapterNumberFromScope).includes(current))reasons.push('chapter_scope');return reasons}
function knowledgeReasonLabel(reason){return ({future_reveal:'未来章节尚未揭示',state_expired:'状态已过有效章节',branch_scope:'不属于当前分支',chapter_scope:'不属于当前章节范围'})[reason]||reason}
function knowledgeScopeHtml(){
  const states=storyMap.character_states||[];const scope=storyMap.knowledge_scope||{};const filtered=new Map((scope.filtered||[]).map(item=>[String(item.id),Array.isArray(item.reasons)?item.reasons:[]]));const rows=states.map(item=>{const reasons=filtered.get(String(item.id))||localKnowledgeReasons(item);return {...item,reasons,visible:!reasons.length}});const hidden=rows.filter(item=>!item.visible);const visible=rows.length-hidden.length;const chapterIndex=Math.max(1,state.chapters.findIndex(chapter=>chapter.id===activeChapter)+1);if(!rows.length)return '<div class="empty">暂无人物状态。状态写入后，这里会显示它在当前章节是否可被 AI 看见。</div>';return `<div class="scope-summary"><strong>第 ${chapterIndex} 章可见 ${scope.character_states_visible??visible} 条</strong><span>已过滤 ${scope.character_states_filtered??hidden.length} 条</span><small>过滤只影响注入 AI 的当前上下文，不会删除原始状态。</small></div><div class="scope-review-list">${rows.map(item=>`<article class="scope-review-row ${item.visible?'scope-visible':'scope-filtered'}"><div><strong>${esc(item.entity_name||item.entity_id||'未命名人物')}</strong><span class="scope-attribute">${esc(item.attribute||'状态')}：${esc(typeof item.value==='string'?item.value:JSON.stringify(item.value??''))}</span></div><span class="story-scope-badge">${item.visible?'当前可见':'已过滤'}</span>${item.visible?`<p>时间 ${esc(item.valid_start||'未限定')}–${esc(item.valid_end||'未限定')} · 分支 ${esc((item.knowledge_scope?.branches||item.knowledge_scope?.branch_ids||['未限定']).join?.('、')||'未限定')}</p>`:`<p class="check-bad">原因：${esc(item.reasons.map(knowledgeReasonLabel).join('、')||'范围条件不满足')}</p>`}</article>`).join('')}</div>`;
}
async function deleteStoryItem(kind,id){
  if(!backendOnline)return toast('启动 SQLite 服务后才能编辑故事结构');
  if(!confirm('删除这条结构记录？正文和设定卡不会被删除。'))return;
  const base={relationship:'/api/story/relationships',timeline:'/api/story/timeline',foreshadow:'/api/story/foreshadow-links'}[kind];
  try{await storyRequest(`${base}/${encodeURIComponent(id)}`,'DELETE',{reason:'author removed from story map'});await actions.refreshStory(true);toast('结构记录已删除')}catch(error){toast(`删除失败：${error.message}`)}
}
const views={
dashboard:()=>`<div class="view-head"><div><h2>早上好，继续写下去</h2><p>把脑海里的世界，变成可以回看的故事。</p></div>${state.project.book_status==='complete'?'': '<button class="primary-btn" data-action="newChapter">＋ 新建章节</button>'}</div>${workflowCard()}<div class="stats"><div class="stat-card"><div class="stat-label">当前字数</div><div class="stat-value">${words().toLocaleString()}</div><div class="stat-note">目标 ${Number(state.project.goal||0).toLocaleString()} 字</div></div><div class="stat-card"><div class="stat-label">章节</div><div class="stat-value">${state.chapters.length}</div><div class="stat-note">${state.chapters.filter(c=>c.status==='完成').length} 章已完成</div></div><div class="stat-card"><div class="stat-label">设定卡片</div><div class="stat-value">${state.records.length}</div><div class="stat-note">人物、地点与其他设定</div></div><div class="stat-card"><div class="stat-label">写作进度</div><div class="stat-value">${state.project.goal?Math.min(100,Math.round(words()/state.project.goal*100)):0}%</div><div class="stat-note">按作品目标计算</div></div></div><div class="dashboard-grid"><section class="panel"><div class="panel-title"><h3>最近章节</h3><span>${state.chapters.length} 个章节</span></div><div class="chapter-list">${state.chapters.slice().reverse().slice(0,5).map((c,i)=>`<div class="chapter-row" data-chapter="${safeId(c.id)}"><div class="chapter-number">${state.chapters.indexOf(c)+1}</div><div><strong>${esc(c.title)}</strong><small>${new Date(c.updated).toLocaleDateString('zh-CN')} · ${c.body.replace(/\s/g,'').length} 字</small></div><div class="chapter-status">${esc(c.status)}</div></div>`).join('')}</div></section><section class="panel"><div class="panel-title"><h3>写作提示</h3><span>今天</span></div><div class="record-card" style="box-shadow:none"><span class="record-tag">场景练习</span><h4>让角色做一个无法撤回的选择</h4><p>选择前，写出他最害怕失去什么；选择后，让环境立刻产生一个具体变化。</p></div><div class="record-card" style="box-shadow:none;margin-top:10px"><span class="record-tag">小目标</span><h4>今天写 500 字</h4><p>不修改、不查资料，只把一个场景写完。完成比完美更重要。</p></div></section></div>`,
editor:()=>{let c=chapter();return `<div class="view-head"><div><h2>章节 + AI 写作</h2><p>先完成，再让助手给候选。所有 AI 内容都先预览，只有你点击采纳才会进入正文。</p></div>${state.project.book_status==='complete'?'':isFreshProject()?'<button class="primary-btn" data-action="startWriting">从故事设定开始</button>':'<button class="primary-btn" data-action="newChapter">＋ 新建章节</button>'}</div><div class="editor-layout"><aside class="chapter-sidebar"><h3>章节目录</h3>${state.chapters.map((x,i)=>`<button class="chapter-select ${x.id===c?.id?'active':''}" data-chapter="${safeId(x.id)}">第 ${i+1} 章 · ${esc(x.title)}<small>${x.body.replace(/\s/g,'').length} 字 · ${esc(x.status)}</small></button>`).join('')}</aside><section class="editor-card">${c?`<div class="editor-toolbar"><button data-format="bold"><b>粗体</b></button><button data-format="quote">引用</button><button data-format="scene">场景分隔</button><button class="ghost-btn" data-action="reviewChapter">审查本章</button><button class="ghost-btn" data-action="revisionHistory">历史版本</button><span class="editor-meta">Markdown · 本地自动保存</span></div><input class="title-input" id="chapterTitle" value="${esc(c.title)}" aria-label="章节标题"><textarea class="chapter-editor" id="chapterBody" aria-label="章节正文">${esc(c.body)}</textarea><div class="chapter-plan"><div class="panel-title"><h3>本章任务卡</h3><span>写前目标 · 写后回顾</span></div><div class="plan-grid"><label>本章目标<input id="chapterGoal" value="${esc(c.chapter_goal)}" placeholder="角色要得到什么，必须付出什么代价？"></label><label>视角<input id="chapterPov" value="${esc(c.pov)}" placeholder="例如：林岚 / 第一人称"></label><label>场景计划<textarea id="chapterScenePlan" placeholder="入口、阻力、转折、出口">${esc(c.scene_plan)}</textarea></label><label>伏笔动作<textarea id="chapterForeshadowing" placeholder="埋下、推进或回收哪些线索？">${esc(c.foreshadowing_op)}</textarea></label><label>章节回顾<textarea id="chapterRecap" placeholder="发生了什么，谁的状态改变？">${esc(c.recap)}</textarea></label><label>下一章任务<textarea id="chapterNextBrief" placeholder="下一章从哪个未解决的问题开始？">${esc(c.next_brief)}</textarea></label><label>章节出口 / 钩子<textarea id="chapterHook" placeholder="读者必须继续读下去的具体问题或变化">${esc(c.hook)}</textarea></label></div></div><div class="editor-footer"><span>⌘ S 保存</span><span id="liveCount">${c.body.replace(/\s/g,'').length} 字</span><select id="chapterStatus"><option ${c.status==='草稿'?'selected':''}>草稿</option><option ${c.status==='进行中'?'selected':''}>进行中</option><option ${c.status==='完成'?'selected':''}>完成</option></select><span class="review-pill">审查：${esc(c.review_status==='passed'?'通过':c.review_status==='needs_revision'?'需修改':'未审查')}</span></div><div class="ai-copilot"><div class="ai-copilot-head"><div><h3>AI 创作助手</h3><p>召回当前章节、任务卡、相邻章节、设定卡和已确认记忆，生成可审阅候选。</p></div><span class="ai-badge" id="aiBadge">${aiStatus.mode==='provider'?'模型已连接':'离线模式'}</span></div><div class="ai-controls"><select id="aiTask" aria-label="AI 任务"><option value="continue">续写当前场景</option><option value="scene">生成场景卡</option><option value="outline">规划故事走向</option><option value="brainstorm">发散三个走向</option><option value="polish">润色当前章节</option><option value="summary">总结当前章节</option><option value="check">连续性检查</option></select><input id="aiInstruction" maxlength="2000" placeholder="补充要求，例如：保持克制、增加动作细节…"><button class="primary-btn" id="aiGenerate">生成候选</button></div><p class="ai-status" id="aiStatus">${aiDraft?'已保留上次候选。':'候选只会在你点击采纳后进入正文。'}</p><textarea class="ai-output" id="aiOutput" aria-label="AI 候选结果" placeholder="生成结果会显示在这里，你可以先编辑再采纳。">${aiDraft?esc(aiDraft.text):''}</textarea><div class="ai-result-meta"><span>当前助手：${esc(aiStatus.model||'离线创作助手')} · 记忆优先人工确认</span><div class="ai-actions"><button class="ghost-btn" id="aiCopy" ${aiDraft?'':'disabled'}>复制候选</button><button class="ghost-btn" id="aiClear">清空</button><button class="primary-btn" id="aiAdopt" ${aiDraft?'':'disabled'}>采纳</button></div></div></div>`:'<div class="empty">还没有章节，点击右上角开始。</div>'}</section></div>`},
memory:()=>`<div class="view-head"><div><h2>记忆中枢</h2><p>把长篇里的关键事实变成可追溯、可确认的记忆，避免写长后遗忘。</p></div><button class="primary-btn" data-action="refreshMemory">↻ 刷新记忆</button></div><div class="panel"><div class="panel-title"><h3>记忆工作流</h3><span>候选 → 确认 → 回忆</span></div><p style="color:var(--muted);font-size:13px;line-height:1.8">在章节中使用标记：<b>【人物】沈砚</b>、<b>【物品】旧钥匙</b>、<b>【伏笔】门后的脚步</b>。系统会把它们捕获为候选记忆；你确认后，未来的连续性检查才会把它当成事实。每条候选都保留原文摘录、章节和字符范围，来源缺失或版本变化时会标记为证据失效。</p></div><div class="record-grid memory-grid" style="margin-top:17px">${state.memory.map(m=>`<article class="record-card memory-card"><span class="record-tag memory-status">${esc(m.type)} · ${esc(memoryStatusLabel(m.status,m.stale))}</span><h4>${esc(m.name)}</h4><p class="memory-evidence"><strong>来源章节：</strong><span class="memory-source-chapter">${esc(memorySourceText(m))}</span><br><strong>来源范围：</strong><span class="memory-source-span">${esc(memorySpanText(m))}</span><br><strong>原文摘录：</strong><span class="memory-source-quote">${esc(memoryQuoteText(m))}</span><br><strong>证据状态：</strong><span class="memory-source-status">${esc(m.source_span_status||'未验证')}</span>${m.stale?'<br><strong class="check-bad">需要重新定位</strong>':''}${m.assertion_id?'<br><strong>正典 assertion：</strong><span class="memory-source-status">'+esc(m.assertion_id)+'</span>':''}<br>${esc(m.note)}</p><div class="memory-actions"><button class="ghost-btn" data-memory-evidence="${safeId(m.id)}">查看证据</button><button class="ghost-btn" data-memory-reanchor="${safeId(m.id)}">重新定位</button><button class="ghost-btn" data-memory="${safeId(m.id)}" ${m.status==='已确认'&&!m.stale?'disabled':''}>${m.status==='已确认'&&!m.stale?'已确认':'确认记忆'}</button><button class="ghost-btn danger-btn" data-memory-reject="${safeId(m.id)}">拒绝</button><button class="ghost-btn" data-memory-review="${safeId(m.id)}">重新审查</button></div></article>`).join('')||'<div class="empty">还没有捕获到候选记忆。去章节里用【人物】、【物品】或【伏笔】标记关键内容。</div>'}</div>`,
world:()=>`<div class="view-head"><div><h2>设定库</h2><p>把需要连续记住的东西，放在故事外面。</p></div><button class="primary-btn" data-action="newRecord">＋ 新建设定</button></div><div class="record-grid">${state.records.map(r=>`<article class="record-card"><span class="record-tag">${esc(r.type)}</span><h4>${esc(r.name)}</h4>${Array.isArray(r.aliases)&&r.aliases.length?`<small class="record-aliases">别名：${esc(r.aliases.join('、'))}</small>`:''}<p>${esc(r.description)}</p><small class="record-scope">${esc(recordScopeLabel(r))}</small><div class="knowledge-card-actions"><button class="ghost-btn" data-record="${safeId(r.id)}">编辑</button><button class="ghost-btn" data-record-knowledge="${safeId(r.id)}">关联与原文</button></div></article>`).join('')||'<div class="empty">还没有设定卡片。</div>'}</div>`,
plot:()=>`<div class="view-head"><div><h2>情节与伏笔</h2><p>让每一条线索都有去处，让每一次回收都更有力量。</p></div><button class="primary-btn" data-action="newPlot">＋ 新增伏笔</button></div><div class="plot-board">${[['setup','埋下线索'],['develop','推进中'],['payoff','已回收']].map(([k,t])=>`<section class="plot-col"><h4>${t} <span style="color:var(--muted);font-weight:400">${state.plot[k].length}</span></h4>${state.plot[k].map((x,i)=>`<div class="plot-item">${esc(typeof x==='string'?x:x.text)}<small>点击卡片后可在下一版移动状态</small></div>`).join('')||'<div class="empty" style="padding:18px">暂无记录</div>'}</section>`).join('')}</div>`,
story:()=>{const editable=backendOnline;const actionButtons=editable?'<div class="story-actions"><button class="ghost-btn" data-action="addRelationship">＋关系</button><button class="ghost-btn" data-action="addTimeline">＋事件</button><button class="ghost-btn" data-action="addForeshadow">＋伏笔链接</button></div>':'';const edit=(kind,id)=>editable?`<div class="story-card-actions"><button class="ghost-btn" data-action="edit${kind}" data-story-id="${safeId(id)}">编辑</button><button class="ghost-btn danger-btn" data-action="delete${kind}" data-story-id="${safeId(id)}">删除</button></div>`:'';return `<div class="view-head"><div><h2>故事结构</h2><p>把人物关系、时间线和伏笔生命周期变成可编辑的故事地图，写作前先校准，写作后再回看。</p></div><div class="view-head-actions"><button class="ghost-btn" data-action="refreshStory">↻ 刷新结构</button>${actionButtons}</div></div>${editable?'':'<section class="workflow-card"><div><span class="workflow-kicker">故事结构需要后端服务</span><h3>启动 SQLite 后编辑关系与时间线</h3><p>当前仍可在章节和设定库离线写作；启动本地服务后，这里的结构记录会和 AI 上下文、审查结果同步。</p></div><button class="primary-btn" data-action="refreshStory">重新连接</button></section>'}<section class="panel story-panel-wide"><div class="panel-title"><div><h3>关系图谱</h3><p class="panel-subtitle">从结构记录快速查看人物、势力和地点之间的稳定关系。</p></div><span>${storyMap.relationships.length} 条关系</span></div>${storyGraphSvg(storyMap.relationships)}</section><section class="panel story-panel-wide"><div class="panel-title"><div><h3>时间线总览</h3><p class="panel-subtitle">按排序序号和时间标记排列，计划事件与已发生事件一眼可见。</p></div><span>${storyMap.timeline.length} 个事件</span></div>${storyTimelineHtml(storyMap.timeline)}</section><section class="panel story-panel-wide"><div class="panel-title"><div><h3>人物状态知识范围</h3><p class="panel-subtitle">审查当前章节注入 AI 的状态边界，明确每条状态为何可见或被过滤。</p></div><span>${storyMap.character_states?.length||0} 条状态</span></div>${knowledgeScopeHtml()}</section><div class="story-grid"><section class="panel"><div class="panel-title"><h3>人物关系明细</h3><span>${storyMap.relationships.length} 条</span></div>${storyMap.relationships.map(r=>`<article class="result story-card"><strong>${esc(r.from_name)} → ${esc(r.to_name)}</strong><p>${esc(r.relation_type)} · ${esc(r.note||'')}</p>${edit('Relationship',r.id)}</article>`).join('')||'<div class="empty">暂无关系。点击“＋关系”把人物、势力或物品连接起来。</div>'}</section><section class="panel"><div class="panel-title"><h3>时间线明细</h3><span>${storyMap.timeline.length} 个事件</span></div>${storyMap.timeline.map(t=>`<article class="result story-card"><strong>${esc(t.event_time||'未定时间')} · ${esc(t.title)}</strong><p>${esc(t.summary||'')}</p><small>${t.chapter_id?'已绑定章节':'未绑定章节'} · ${esc(t.status||'planned')}</small>${edit('Timeline',t.id)}</article>`).join('')||'<div class="empty">暂无事件。先记录一个改变局面的瞬间。</div>'}</section></div><section class="panel story-panel-wide"><div class="panel-title"><h3>伏笔生命周期</h3><span>${storyMap.foreshadowing.length} 条链接</span></div>${storyMap.foreshadowing.map(f=>`<article class="result story-card"><strong>${esc(f.kind==='plant'?'埋下':f.kind==='develop'?'推进':'回收')} · ${esc(f.text)}</strong><p>${esc(f.chapter_title||'未绑定章节')} · ${esc(f.note||'')}</p>${edit('Foreshadow',f.id)}</article>`).join('')||'<div class="empty">暂无伏笔链接。先在“情节与伏笔”创建卡片，再绑定发生章节。</div>'}</section><section class="panel story-panel-wide"><div class="panel-title"><h3>冲突提示</h3><span>${storyMap.conflicts.length} 个</span></div>${storyMap.conflicts.map(c=>`<article class="result check-bad"><strong>冲突实体：${esc(c.entity_id)}</strong><p>记忆 ${esc(c.memory_id||'')} · ${esc(c.status||'')}，请回到记忆中枢核对证据。</p></article>`).join('')||'<div class="empty">当前没有检测到记忆冲突。</div>'}</section>`},
search:()=>`<div class="view-head"><div><h2>全文搜索</h2><p>搜索章节正文和设定卡片。</p></div></div><div class="search-box"><input id="searchInput" placeholder="搜索人物、句子、关键词…"><button class="primary-btn" data-action="search">搜索</button></div><div id="searchResults"><div class="empty">输入关键词，开始检索。</div></div>`
};
function requestText(label,initial='',{title='填写内容',current=null}={}){
 textRequestCancel?.();
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main';
 const stillCurrent=()=>owner===requestedProject&&branch===(state.branch_id||state.project?.branch_id||'main')&&
  (!current||current());
 if(!stillCurrent())return Promise.resolve(null);
 return new Promise(resolve=>{
  const previousFocus=document.activeElement,dialog=document.createElement('dialog');
  const identifier='text-request-'+crypto.randomUUID(),heading=identifier+'-heading';
  dialog.className='modal text-request-dialog';dialog.dataset.textRequest='';
  dialog.setAttribute('aria-labelledby',heading);
  dialog.innerHTML=`<form><h3 id="${heading}">${esc(title)}</h3><label for="${identifier}">${esc(label)}</label>
   <textarea id="${identifier}" data-text-input rows="4" autocomplete="off" autofocus></textarea>
   <div class="modal-actions"><button type="button" class="ghost-btn" data-text-cancel>取消</button>
   <button type="submit" class="primary-btn" data-text-submit>确认</button></div></form>`;
  const input=dialog.querySelector('[data-text-input]');input.value=String(initial??'');
  let settled=false;
  const finish=value=>{
   if(settled)return;settled=true;
   input.value='';if(dialog.open)dialog.close();dialog.remove();
   if(textRequestCancel===cancel)textRequestCancel=null;
   if(previousFocus?.isConnected)previousFocus.focus({preventScroll:true});
   resolve(value);
  };
  const cancel=()=>finish(null);textRequestCancel=cancel;
  dialog.querySelector('[data-text-cancel]').onclick=cancel;
  dialog.querySelector('form').onsubmit=event=>{event.preventDefault();finish(stillCurrent()?input.value:null)};
  dialog.addEventListener('cancel',event=>{event.preventDefault();cancel()});
  dialog.addEventListener('close',cancel);
  document.body.append(dialog);
  try{dialog.showModal();input.focus();input.select()}
  catch{cancel();toast('填写窗口暂时无法打开，请重新操作')}
 });
}
function modal(title,body,onSave){const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.innerHTML=`<div class="modal"><h3>${title}</h3>${body}<div class="modal-actions"><button class="ghost-btn" data-close>取消</button><button class="primary-btn" data-ok>保存</button></div></div>`;document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();wrap.querySelector('[data-ok]').onclick=()=>{if(onSave(wrap)===false)return;wrap.remove();save();render();toast('已保存')};return wrap}
function bind(){
 document.querySelectorAll('[data-chapter]').forEach(b=>b.onclick=()=>{if(activeChapter!==b.dataset.chapter)aiDraft=null;activeChapter=b.dataset.chapter;view='editor';render()});
 document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>actions[b.dataset.action]?.(b));
 const title=document.querySelector('#chapterTitle'),body=document.querySelector('#chapterBody'),status=document.querySelector('#chapterStatus');
 if(title&&body){
  bindEditorInputSync(title,'title');bindEditorInputSync(body,'body');
  const sync=(key,value)=>{
   const c=chapter();if(!c)return;
   c[key]=value;c.updated=Date.now();
   document.querySelector('#liveCount').textContent=`${c.body.replace(/\s/g,'').length} 字`;
   save();document.querySelector('#projectMeta').textContent=`${state.chapters.length} 章 · ${words()} 字`;
  };
  title.oninput=()=>sync('title',title.value);
  body.oninput=()=>sync('body',body.value);
  Object.entries(CHAPTER_PLAN_FIELDS).forEach(([id,key])=>{
   const input=document.querySelector('#'+id);input?.addEventListener('input',()=>sync(key,input.value));
  });
 }
 if(status)status.onchange=()=>{const c=chapter();if(!c)return;c.status=status.value;c.review_status='unreviewed';c.updated=Date.now();save();toast(`章节状态已更新为“${c.status}”`)};
 document.querySelector('#searchInput')?.addEventListener('keydown',e=>e.key==='Enter'&&actions.search());document.querySelectorAll('[data-record]').forEach(b=>b.onclick=()=>editRecord(b.dataset.record));document.querySelectorAll('[data-memory]').forEach(b=>b.onclick=()=>decideMemory(b.dataset.memory,'accepted').catch(error=>toast(`记忆确认失败：${error.message}`)));document.querySelectorAll('[data-memory-evidence]').forEach(b=>b.onclick=()=>showMemoryEvidence(b.dataset.memoryEvidence));document.querySelectorAll('[data-memory-reanchor]').forEach(b=>b.onclick=()=>reanchorMemory(b.dataset.memoryReanchor));document.querySelectorAll('[data-memory-reject]').forEach(b=>b.onclick=()=>decideMemory(b.dataset.memoryReject,'rejected').catch(error=>toast(`记忆拒绝失败：${error.message}`)));document.querySelectorAll('[data-memory-review]').forEach(b=>b.onclick=()=>decideMemory(b.dataset.memoryReview,'candidate').catch(error=>toast(`记忆更新失败：${error.message}`)));document.querySelector('#aiGenerate')?.addEventListener('click',generateAI);document.querySelector('#aiAdopt')?.addEventListener('click',adoptAI);document.querySelector('#aiCopy')?.addEventListener('click',async()=>{if(!aiDraft)return;try{await navigator.clipboard.writeText(document.querySelector('#aiOutput').value);toast('候选已复制')}catch{document.querySelector('#aiOutput').select()}});document.querySelector('#aiClear')?.addEventListener('click',clearAI);document.querySelector('#aiOutput')?.addEventListener('input',editCandidateText);const generateButton=document.querySelector('#aiGenerate');if(generateButton&&!generateButton.parentElement.querySelector('[data-context-preview]')){const preview=document.createElement('button');preview.className='ghost-btn';preview.dataset.contextPreview='1';preview.textContent='上下文预览';preview.onclick=()=>actions.contextPreview();generateButton.after(preview);const contract=document.createElement('button');contract.className='ghost-btn';contract.textContent='任务契约';contract.onclick=aiPromptContract;preview.after(contract)}
 bindInstructionDraft();
 renderReferenceTools();document.querySelector('#aiTask')?.addEventListener('change',renderReferenceTools);
}
window.addEventListener('beforeunload',()=>cacheInstructionDraft());
document.addEventListener('click',e=>{const button=e.target.closest?.('[data-chapter]');if(button?.dataset.chapter)storage.set(ACTIVE_KEY,button.dataset.chapter)});
document.addEventListener('click',e=>{
 const assertion=e.target.closest?.('[data-assertion-review]');if(!assertion)return;e.preventDefault();e.stopPropagation();
 const wrap=assertion.closest('[data-memory-evidence-dialog]'),scope=memoryEvidenceScopes.get(wrap);
 if(!memoryEvidenceCurrent(scope)){wrap?.remove();return}
 reviewAssertion(assertion.dataset.assertionReview,assertion.dataset.assertionState,assertion.dataset.memoryId,scope,wrap);
},true);
document.addEventListener('click',async e=>{
 const reject=e.target.closest?.('[data-memory-reject]'),review=e.target.closest?.('[data-memory-review]');
 const accept=e.target.closest?.('[data-memory]'),id=reject?.dataset.memoryReject||review?.dataset.memoryReview||accept?.dataset.memory;
 if(!id)return;e.preventDefault();e.stopPropagation();
 if(reject){
  const scope=memoryEvidenceScope(),stillCurrent=()=>memoryEvidenceCurrent(scope)&&reject.isConnected;
  const reason=await requestText('可选：为什么拒绝这条记忆？','',{title:'拒绝记忆',current:stillCurrent});
  if(reason===null||!stillCurrent())return;
  decideMemory(id,'rejected',reason).catch(error=>toast(`记忆拒绝失败：${error.message}`));
 }else if(review)decideMemory(id,'candidate').catch(error=>toast(`记忆重新审查失败：${error.message}`));
 else decideMemory(id,'accepted').catch(error=>toast(`记忆确认失败：${error.message}`));
},true);
document.addEventListener('click',event=>{const button=event.target.closest?.('[data-search-jump]');if(!button)return;event.preventDefault();openSearchEvidence(button).catch(error=>toast(`原文定位失败：${error.message}`))});
const actions={refreshStory:async(silent=false)=>{if(!backendOnline){if(!silent)toast('启动 SQLite 服务后才能读取故事结构');return}try{const r=await fetch('/api/story-map',{headers:{Accept:'application/json'}});if(!r.ok)throw new Error(`HTTP ${r.status}`);const structure=await r.json();structure.knowledge_scope={};try{const context=await fetch(`/api/context?chapter_id=${encodeURIComponent(activeChapter||'')}&retrieval_profile=${profileQuery()}`,{headers:{Accept:'application/json'}});if(context.ok){const contextData=await context.json();structure.knowledge_scope=contextData.budget?.knowledge_scope||{}}}catch{}storyMap={...structure,character_states:Array.isArray(structure.character_states)?structure.character_states:[],knowledge_scope:structure.knowledge_scope||{}};storyLoaded=true;storyLoadedChapter=activeChapter;render();if(!silent)toast('故事结构已刷新')}catch(e){if(!silent)toast(`读取故事结构失败：${e.message}`)}},addRelationship:()=>relationshipModal(),addTimeline:()=>timelineModal(),addForeshadow:()=>foreshadowModal(),editRelationship:button=>relationshipModal(storyMap.relationships.find(x=>x.id===button?.dataset.storyId)),editTimeline:button=>timelineModal(storyMap.timeline.find(x=>x.id===button?.dataset.storyId)),editForeshadow:button=>foreshadowModal(storyMap.foreshadowing.find(x=>x.id===button?.dataset.storyId)),deleteRelationship:button=>deleteStoryItem('relationship',button?.dataset.storyId),deleteTimeline:button=>deleteStoryItem('timeline',button?.dataset.storyId),deleteForeshadow:button=>deleteStoryItem('foreshadow',button?.dataset.storyId),refreshMemory:()=>{captureMemories();save();render();toast('已刷新记忆候选')},newChapter:()=>{if(state.project.book_status==='complete')return toast('全书已完稿，请先在终稿检查中恢复写作');const c={id:crypto.randomUUID(),title:`第 ${state.chapters.length+1} 章`,body:'',status:'草稿',updated:Date.now()};state.chapters.push(c);activeChapter=c.id;localStorage.setItem(ACTIVE_KEY,c.id);view='editor';save();render()},newRecord:()=>editRecord(),newPlot:()=>modal('新增伏笔',`<label>伏笔内容</label><textarea id="plotText" placeholder="例如：第一章出现的旧钥匙……"></textarea>`,w=>{const v=w.querySelector('#plotText').value.trim();if(v)state.plot.setup.push(v)})};
actions.finalCheck=()=>showFinalizationCheck();
actions.search=runSearch;
actions.contextPreview=async()=>{
 const c=chapter();if(!c)return toast('请先选择章节');
 syncReferenceScope();
 const sequence=++aiPreviewSeq;
 try{
  const snapshot=aiRequestSnapshot(c,document.querySelector('#aiTask')?.value||'continue',
   document.querySelector('#aiInstruction')?.value||'');
  const profile=snapshot.payload.retrieval_profile,controls=snapshot.payload.material_controls;let pack;
  if(snapshot.references.selections.length&&!backendOnline)throw new Error('请先恢复连接，再核对本次参考原句');
  if(backendOnline){
   try{
    await sealAIPreviewSnapshot(snapshot);
    const previewSource={...snapshot.payload.source,range:{
     start:snapshot.payload.source.range.start,end:snapshot.payload.source.range.end}};
    const query=new URLSearchParams({chapter_id:c.id,task:snapshot.task,instruction:snapshot.instruction,
     materials:JSON.stringify(controls),retrieval_profile:JSON.stringify(profile),
     source:JSON.stringify(previewSource),reference_selections:JSON.stringify(snapshot.references.selections)});
    const response=await fetch(`${API_BASE}/context?${query}`,{headers:{
     'X-Moyu-Project':snapshot.references.scope.project_id,'X-Moyu-Branch':snapshot.references.scope.branch_id}});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(result.error||`材料预览读取失败（${response.status}）`);
    pack=result;
   }catch(error){if(snapshot.references.selections.length)throw error;pack=null}
  }
  if(sequence!==aiPreviewSeq||!aiRequestCurrent(snapshot))return;
  if(!pack)pack={budget:{retrieved_count:0,previous_count:0,estimated_tokens:Math.ceil((c.body||'').length/4),
   retrieval:'offline',profile},current_chapter:{plan:{}},retrieved_chapters:[],previous_chapters:[],
   memory:state.memory.filter(m=>m.status==='已确认').map(m=>({id:m.id,name:m.name,source_chapter_id:m.source_chapter_id,source_span_id:m.source_span_id}))};
  const b=pack.budget||{},truncation=b.truncation||{},scope=b.knowledge_scope||{};
  const truncationNote=truncation.applied?`已裁剪 ${truncation.removed?.length||0} 项、截短 ${(truncation.clipped||[]).length} 项；固定 ${(truncation.pinned||[]).length} 项。`:'';
  const scopeNote=scope.character_states_filtered?`已隐藏 ${scope.character_states_filtered} 条超出当前章节范围的人物状态。`:'';
  const items=[...(pack.previous_chapters||[]).map(x=>({...x,kind:'chapters',label:`近章：${x.title}`})),
   ...(pack.retrieved_chapters||[]).map(x=>({...x,kind:'chapters',label:`召回：${x.title} · 相关度 ${x.relevance}`})),
   ...(pack.records||[]).map(x=>({...x,kind:'records',label:`设定：${x.type||'卡片'} · ${x.name}`})),
   ...(pack.memory||[]).map(x=>({...x,kind:'memory',label:`记忆：${x.name}`}))];
  const checked=(item,field)=>controls[item.kind]?.[item.id]?.[field]?'checked':'',shownProfile=b.profile||profile;
  const fields=[['max_tokens','预算 tokens',4000,64000],['chapter_top_k','召回章节',0,50],
   ['recent_window','近章窗口',0,10],['memory_top_k','记忆上限',0,120],['record_top_k','设定上限',0,80]];
  const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.dataset.referencePreview='1';
  wrap.innerHTML=`<div class="modal context-modal"><h3>AI 材料与提示预览</h3><p class="modal-help">估算 ${b.estimated_tokens||0} tokens，
   当前预算 ${b.max_tokens||profile.max_tokens}。${esc(truncationNote)} ${esc(scopeNote)}</p>
   ${referencePassageMarkup(pack.reference_passages)}<section class="context-profile"><div class="profile-heading"><strong>上下文配置</strong></div>
   <div class="profile-grid">${fields.map(([key,label,min,max])=>`<label>${label}<input type="number" data-profile="${key}"
    value="${shownProfile[key]??profile[key]}" min="${min}" max="${max}" step="1"></label>`).join('')}</div></section>
   <div class="material-list">${items.length?items.map(x=>`<label class="material-row"><input type="checkbox" data-material-pin data-kind="${x.kind}"
    data-id="${safeId(x.id)}" ${checked(x,'pin')}> 固定 <input type="checkbox" data-material-omit data-kind="${x.kind}" data-id="${safeId(x.id)}"
    ${checked(x,'omit')}> 排除 <input type="number" class="material-order" data-material-order data-kind="${x.kind}" data-id="${safeId(x.id)}"
    value="${controls[x.kind]?.[x.id]?.order??''}" placeholder="顺序" min="0" max="999"><span>${esc(x.label)}</span></label>`).join(''):
    '<p class="modal-help">暂无可召回来源。</p>'}</div><p class="form-error" data-preview-error role="alert"></p>
   <div class="modal-actions"><button type="button" class="ghost-btn" data-profile-reset>恢复默认配置</button>
   <button type="button" class="ghost-btn" data-prompt-preview>完整 Prompt 预览</button><button type="button" class="ghost-btn" data-close>关闭</button>
   <button type="button" class="primary-btn" data-save-material>保存材料选择</button></div></div>`;
  document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();
  const readProfile=()=>{
   const next={};wrap.querySelectorAll('[data-profile]').forEach(input=>{
    const [min,max]=CONTEXT_PROFILE_LIMITS[input.dataset.profile],value=Number(input.value);
    next[input.dataset.profile]=Number.isFinite(value)?Math.max(min,Math.min(max,Math.round(value))):profile[input.dataset.profile];
   });return next;
  };
  const readControls=()=>{
   const next=MoyuSync.copy(controls);
   wrap.querySelectorAll('[data-material-pin],[data-material-omit],[data-material-order]').forEach(input=>{
    const kind=input.dataset.kind,id=input.dataset.id;next[kind]??={};next[kind][id]??={};
    if(input.hasAttribute('data-material-order')){
     if(input.value==='')delete next[kind][id].order;else next[kind][id].order=Number(input.value);
    }else next[kind][id][input.hasAttribute('data-material-pin')?'pin':'omit']=input.checked;
   });return next;
  };
  wrap.querySelector('[data-profile-reset]').onclick=()=>{
   wrap.querySelectorAll('[data-profile]').forEach(input=>input.value=CONTEXT_PROFILE_DEFAULTS[input.dataset.profile]);
  };
  wrap.querySelector('[data-save-material]').onclick=()=>{
   if(!referenceScopeCurrent(snapshot.references))return;
   saveMaterialControls(readControls());saveRetrievalProfile(readProfile());wrap.remove();toast('材料与上下文配置已保存');
  };
  wrap.querySelector('[data-prompt-preview]').onclick=async()=>{
   const button=wrap.querySelector('[data-prompt-preview]');button.disabled=true;
   try{
    if(!backendOnline)throw new Error('请先恢复连接，再查看完整提示');
    const request={...snapshot,payload:{...snapshot.payload,material_controls:readControls(),retrieval_profile:readProfile()}};
    const data=await readAIPromptPreview(request);
    if(!wrap.isConnected||sequence!==aiPreviewSeq)return;
    const preview=document.createElement('div');preview.className='modal-backdrop';preview.dataset.referencePreview='1';
    preview.innerHTML=`<div class="modal candidate-modal"><h3>完整 Prompt 预览</h3><p class="modal-help">${data.prompt_chars||0} 字符，
     约 ${data.estimated_tokens||0} tokens</p>${referencePassageMarkup(data.reference_passages)}
     <pre class="prompt-preview">${esc((data.system||'')+'\n\n'+(data.user||''))}</pre>
     <div class="modal-actions"><button type="button" class="ghost-btn" data-close>关闭</button></div></div>`;
    document.body.append(preview);preview.querySelector('[data-close]').onclick=()=>preview.remove();
   }catch(error){if(wrap.isConnected)wrap.querySelector('[data-preview-error]').textContent=error.message}
   finally{button.disabled=false}
  };
 }catch(error){if(sequence===aiPreviewSeq)toast(error.message)}
};
function localReview(c){const chars=c.body.replace(/\s/g,'').length;const blocking=[];const warnings=[];if(chars<60)blocking.push({code:'too_short',message:'正文少于 60 个非空白字符。'});if(c.status!=='完成')blocking.push({code:'status_incomplete',message:'章节状态还不是“完成”。'});if(!c.chapter_goal)warnings.push({code:'missing_goal',message:'没有填写本章任务卡目标。'});if(!c.hook)warnings.push({code:'missing_hook',message:'没有填写章节出口或钩子。'});if(!c.recap)warnings.push({code:'missing_recap',message:'尚未沉淀章节回顾，长篇召回会变弱。'});const tags=[...c.body.matchAll(/(?:【|\[)(人物|物品|地点|势力)(?:】|\])\s*([^\n，。]+)/g)];tags.forEach(t=>{if(!state.records.some(r=>r.type===t[1]&&r.name===t[2].trim()))warnings.push({code:'unknown_entity',message:`标记实体“${t[2].trim()}”尚未进入设定库。`})});const pending=state.memory.filter(m=>m.source_chapter_id===c.id&&m.status!=='已确认').length;if(pending)warnings.push({code:'pending_memory',message:`本章仍有 ${pending} 条记忆候选未确认。`});return {chapter_id:c.id,status:blocking.length?'needs_revision':'passed',score:Math.max(0,100-blocking.length*35-warnings.length*8),blocking,warnings,checked_at:new Date().toISOString()}}
function showReviewReport(report){const wrap=document.createElement('div');wrap.className='modal-backdrop';const items=[...(report.blocking||[]).map(x=>`<li class="check-bad">阻断 · ${esc(x.message)}</li>`),...(report.warnings||[]).map(x=>`<li>建议 · ${esc(x.message)}</li>`)];wrap.innerHTML=`<div class="modal"><h3>本章审查 · ${report.score} 分</h3><p class="modal-help">审查只生成报告，不会自动改正文。${report.status==='passed'?'当前章节满足基础归档条件。':'请处理阻断项，或由作者确认后继续。'}</p>${items.length?`<ul class="check-list">${items.join('')}</ul>`:'<p class="modal-help check-ok">没有发现基础阻断项或提醒。</p>'}<div class="modal-actions"><button class="ghost-btn" data-close>关闭</button></div></div>`;document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove()}
actions.reviewChapter=async()=>{const c=chapter();if(!c)return toast('请先选择章节');let report;if(backendOnline){try{const r=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chapter_id:c.id})});if(!r.ok)throw new Error(`HTTP ${r.status}`);report=await r.json()}catch{report=localReview(c)}}else report=localReview(c);c.review=report;c.review_status=report.status;c.reviewed_at=report.checked_at;save();render();showReviewReport(report)};actions.revisionHistory=async()=>{const c=chapter();if(!c)return toast('请先选择章节');if(!backendOnline)return toast('启动 SQLite 服务后才能查看历史版本');try{const response=await fetch(`/api/chapters/${encodeURIComponent(c.id)}/revisions?include_body=1&diff=1`);if(!response.ok)throw new Error(`HTTP ${response.status}`);const data=await response.json();const rows=(data.revisions||[]).map((revision,index)=>`<article class="record-card"><strong>版本 ${data.revisions.length-index} · ${esc(revision.operation)}</strong><p class="modal-help">${esc(revision.created_at)} · ${revision.characters} 字<br>${esc(revision.preview||'（空正文）')}</p><details><summary>查看正文与差异</summary><pre class="prompt-preview">${esc(revision.body||'')}</pre>${revision.diff?`<pre class="prompt-preview">${esc(revision.diff)}</pre>`:'<p class="modal-help">当前版本没有上一版可比较。</p>'}</details><button class="ghost-btn" data-restore-revision="${safeId(revision.id)}">预览后恢复为新版本</button></article>`).join('');const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.innerHTML=`<div class="modal"><h3>《${esc(c.title)}》历史版本</h3><p class="modal-help">恢复会先显示目标正文与差异，确认后创建新 revision；旧来源片段会标记为 stale。</p><div class="revision-list">${rows||'<p class="modal-help">暂无历史版本。</p>'}</div><div class="modal-actions"><button class="ghost-btn" data-close>关闭</button></div></div>`;document.body.append(wrap);wrap.querySelector('[data-close]').onclick=()=>wrap.remove();wrap.querySelectorAll('[data-restore-revision]').forEach(button=>button.onclick=async()=>{const rev=(data.revisions||[]).find(x=>x.id===button.dataset.restoreRevision);if(!rev)return;if(!confirm(`确认恢复此版本（${rev.characters} 字）？\n\n${(rev.body||'').slice(0,300)}`))return;button.disabled=true;try{const restore=await fetch(`/api/chapters/${encodeURIComponent(c.id)}/restore-revision`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision_id:button.dataset.restoreRevision})});if(!restore.ok){const error=await restore.json().catch(()=>({}));throw new Error(error.error||`HTTP ${restore.status}`)}const updated=await restore.json();const index=state.chapters.findIndex(item=>item.id===c.id);if(index>=0)state.chapters[index]={...state.chapters[index],...updated};save();wrap.remove();render();toast('已恢复为新版本，请重新审查章节与记忆')}catch(error){button.disabled=false;toast(`恢复失败：${error.message}`)}})}catch(error){toast(`历史版本读取失败：${error.message}`)}};
actions.startWriting=()=>modal('开始一个新故事',`<p class="modal-help">先回答五个问题，墨屿会建立故事骨架并把你带进第一章。之后每一章都沿着“写作 → 记忆确认 → 回顾 → 下一章”推进。</p><label>作品名</label><input id="wTitle" placeholder="例如：雾中的第七码头"><label>一句话故事设定</label><textarea id="wPremise" placeholder="谁，在什么处境下，想得到什么？"></textarea><label>类型</label><input id="wGenre" placeholder="例如：悬疑 / 科幻 / 言情"><label>主角</label><input id="wHero" placeholder="姓名或身份"><label>眼前阻力</label><textarea id="wConflict" placeholder="什么正在阻止主角？本章必须付出什么代价？"></textarea>`,w=>{const title=w.querySelector('#wTitle').value.trim(),premise=w.querySelector('#wPremise').value.trim(),genre=w.querySelector('#wGenre').value.trim(),hero=w.querySelector('#wHero').value.trim(),conflict=w.querySelector('#wConflict').value.trim();if(!title||!premise||!hero||!conflict){toast('请至少填写作品名、故事设定、主角和眼前阻力');return false}state.project={...state.project,title,genre,summary:premise,book_status:'writing'};let first=state.chapters[0];if(!first||first.title==='第一章 还没有开始的故事'){first={id:crypto.randomUUID(),title:'第一章 · 起点',body:'',status:'草稿',updated:Date.now()};state.chapters=[first]}first.title='第一章 · 起点';first.body=`场景目标：让读者看见主角此刻最想得到什么。\n\n眼前阻力：${conflict}\n\n【人物】${hero}\n\n从一个正在发生的动作开始：`;first.status='进行中';first.chapter_goal=conflict;first.review_status='unreviewed';first.review={};first.updated=Date.now();activeChapter=first.id;localStorage.setItem(ACTIVE_KEY,first.id);if(!state.records.some(r=>r.type==='人物'&&r.name===hero))state.records.push({id:crypto.randomUUID(),type:'人物',name:hero,description:`主角。欲望来自：${premise}`});if(!state.plot.setup.some(x=>(typeof x==='string'?x:x.text)===conflict))state.plot.setup.push(conflict);view='editor';aiDraft=null});
actions.continueWriting=()=>{if(state.project.book_status==='complete')return toast('全书已完稿，请先在终稿检查中恢复写作');const current=state.chapters.find(c=>c.status!=='完成')||state.chapters[state.chapters.length-1];if(!current)return actions.startWriting();activeChapter=current.id;view='editor';render()};
actions.finishChapter=()=>{if(state.project.book_status==='complete')return toast('全书已完稿，请先在终稿检查中恢复写作');const c=chapter();if(!c||c.body.replace(/\s/g,'').length<60)return toast('先写出至少一小段正文，再归档本章');const previousStatus=c.status;c.status='完成';captureMemories();const report=localReview(c);if(report.blocking.length&&!confirm(`本章还有 ${report.blocking.length} 个阻断项，仍要归档吗？`)){c.status=previousStatus;return}c.review=report;c.review_status=report.status;c.reviewed_at=report.checked_at;c.updated=Date.now();const next={id:crypto.randomUUID(),title:`第 ${state.chapters.length+1} 章`,body:'',status:'草稿',updated:Date.now()};state.chapters.push(next);activeChapter=next.id;localStorage.setItem(ACTIVE_KEY,next.id);aiDraft=null;view='editor';save();render();toast('本章已归档，下一章已经准备好')};
function recordKnowledgeOwners(record){
 const scope=record.knowledge_scope||{},lists=[record.known_by,scope.known_by,scope.characters,scope.entities]
  .filter(value=>Array.isArray(value)&&(value.length||record.private||scope.private));
 if(!lists.length)return record.private||scope.private?[]:null;
 return lists[0].filter(value=>lists.every(list=>list.includes(value)));
}
function recordScopeLabel(record){
 const scope=record.knowledge_scope||{},owners=recordKnowledgeOwners(record),parts=[];
 const start=chapterNumberFromScope(scope.available_from||record.available_from||record.valid_start||scope.valid_start);
 const end=chapterNumberFromScope(record.valid_end||scope.valid_end);
 if(start)parts.push(`第 ${start} 章起`);if(end)parts.push(`至第 ${end} 章`);
 parts.push(owners===null?'所有视角':owners.length?`知情：${owners.map(id=>state.records.find(item=>item.id===id)?.name||id).join('、')}`:'仅作者可见');
 const mode=record.epistemic_mode||scope.epistemic_mode;
 if(mode&&mode!=='world_truth')parts.push({character_belief:'人物认知',author_plan:'作者计划',narrator:'叙述信息',inference:'推测'}[mode]||mode);
 return parts.join(' · ');
}
function editRecord(id){
 const r=id?state.records.find(x=>x.id===id):{type:'人物',name:'',description:'',aliases:[]};if(!r)return;
 const owner=requestedProject,branch=state.branch_id||state.project?.branch_id||'main',scope=r.knowledge_scope||{},owners=recordKnowledgeOwners(r);
 const current=()=>owner===requestedProject&&branch===(state.branch_id||state.project?.branch_id||'main');
 const start=chapterNumberFromScope(scope.available_from||r.available_from||r.valid_start||scope.valid_start)||'';
 const end=chapterNumberFromScope(r.valid_end||scope.valid_end)||'';
 const visibility=owners===null?'all':owners.length?'people':'author';
 const modes={world_truth:'世界事实',character_belief:'人物认知或误信',author_plan:'尚未发生的作者计划',narrator:'叙述信息',inference:'推测'};
 authorForm(id?'编辑设定':'新建设定',`<label>类型</label><input id="recordType" value="${esc(r.type)}" placeholder="人物 / 地点 / 势力 / 物品">
 <label>名称</label><input id="recordName" value="${esc(r.name)}" required>
 <label>别名</label><input id="recordAliases" value="${esc((Array.isArray(r.aliases)?r.aliases:[]).join('、'))}" placeholder="阿岚、小岚">
 <label>描述</label><textarea id="recordDesc">${esc(r.description)}</textarea>
 <fieldset class="record-scope-fields"><legend>写作可见范围</legend>
 <label>信息类型<select id="recordMode">${Object.entries(modes).map(([value,label])=>`<option value="${value}" ${(r.epistemic_mode||scope.epistemic_mode||'world_truth')===value?'selected':''}>${label}</option>`).join('')}</select></label>
 <label>可用视角<select id="recordVisibility"><option value="all" ${visibility==='all'?'selected':''}>所有视角</option><option value="people" ${visibility==='people'?'selected':''}>仅指定知情人物</option><option value="author" ${visibility==='author'?'selected':''}>仅作者可见</option></select></label>
 <label>知情人物<select id="recordKnown" multiple size="3">${storyEntityMultiOptions(owners||[])}</select></label>
 <div class="settings-fields"><label>第几章起可用<input id="recordAvailable" type="number" min="1" max="10000" value="${start}" placeholder="不限"></label>
 <label>到第几章有效<input id="recordUntil" type="number" min="1" max="10000" value="${end}" placeholder="不限"></label></div></fieldset>`,async w=>{
  if(!current())throw new Error('作品已经切换，请重新打开设定');
  const aliases=normalizeAliases((w.querySelector('#recordAliases').value||'').split(/[、,，\n]/));
  if(!aliases)throw new Error('别名最多20个，每个不超过100字');
  const selected=w.querySelector('#recordVisibility').value;
  const known=selected==='people'?[...w.querySelector('#recordKnown').selectedOptions].map(option=>option.value):[];
  if(selected==='people'&&!known.length)throw new Error('请选择至少一位知情人物');
  const available=w.querySelector('#recordAvailable').value,until=w.querySelector('#recordUntil').value;
  if(available&&until&&Number(until)<Number(available))throw new Error('结束章节不能早于开始章节');
  const nextScope={...scope,known_by:known,private:selected!=='all',epistemic_mode:w.querySelector('#recordMode').value,
   available_from:available,valid_start:available,valid_end:until};
  delete nextScope.characters;delete nextScope.entities;
  const payload={type:w.querySelector('#recordType').value.trim()||'其他',name:w.querySelector('#recordName').value.trim(),
   aliases,description:w.querySelector('#recordDesc').value,knowledge_scope:nextScope,known_by:known,
   private:nextScope.private,epistemic_mode:nextScope.epistemic_mode,available_from:available,valid_start:available,valid_end:until};
  if(!payload.name)throw new Error('请填写设定名称');
  if(Array.from(payload.name).length>500||Array.from(payload.type).length>100||Array.from(payload.description).length>20000)
   throw new Error('设定名称、类型或描述超过支持的字数');
  if(backendOnline)await durableOperation(id?`/api/records/${encodeURIComponent(id)}`:'/api/records',id?'PATCH':'POST',payload,current);
  else{
   const target=id?state.records.find(item=>item.id===id):{id:crypto.randomUUID()};
   Object.assign(target,payload);if(!id)state.records.push(target);save();
  }
  if(!current())return;
  if(typeof authorRefreshWorkbenchKnowledge==='function'&&authorRefreshWorkbenchKnowledge())authorLoadWorkbenchContext();
  else render();
  toast(backendOnline?'设定和可见范围已保存到本机':'设定和可见范围已保留为本页草稿');
 });
}
function toast(t){const x=document.querySelector('#toast');x.textContent=t;x.classList.add('show');setTimeout(()=>x.classList.remove('show'),1500)}
document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>{view=b.dataset.view;render()});document.querySelector('#editProjectBtn').onclick=()=>modal('编辑作品信息',`<label>作品名</label><input id="pTitle" value="${esc(state.project.title)}"><label>一句话简介</label><textarea id="pSummary">${esc(state.project.summary)}</textarea><label>目标字数</label><input id="pGoal" type="number" value="${state.project.goal||50000}">`,w=>{state.project.title=w.querySelector('#pTitle').value||'未命名长篇';state.project.summary=w.querySelector('#pSummary').value;state.project.goal=Number(w.querySelector('#pGoal').value)||50000});document.querySelector('#finalCheckBtn').onclick=showFinalizationCheck;document.querySelector('#markdownExportBtn').onclick=markdownExport;document.querySelector('#epubExportBtn').onclick=epubExport;document.querySelector('#exportBtn').onclick=()=>{downloadText(exportFilename('json'),JSON.stringify(state,null,2),'application/json;charset=utf-8');toast('已导出 JSON 备份')};document.querySelector('#importBtn').onclick=()=>document.querySelector('#fileInput').click();document.querySelector('#fileInput').onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{try{const imported=normalizeState(JSON.parse(rd.result));if(!imported)throw new Error('invalid');delete imported.version;delete imported.full_state;state=imported;activeChapter=state.chapters[0]?.id;save();render();toast('已导入作品')}catch{toast('文件格式不正确')}};rd.readAsText(f)};document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save();toast('已保存')}});render();hydrate();loadAIStatus();
