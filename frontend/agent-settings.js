'use strict';

(() => {
 let sequence=0,refreshCurrent=null,disposeCurrent=null;
 const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const scope=()=>({project:requestedProject||state.project?.id||'',branch:state.branch_id||state.project?.branch_id||'main'});
 const sameScope=(a,b)=>a.project===b.project&&a.branch===b.branch;
 async function request(path,options,captured){
  const response=await fetch('/api/agent-chat/settings'+path,{...options,headers:{
   'Content-Type':'application/json','X-Moyu-Project':captured.project,'X-Moyu-Branch':captured.branch}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||`设置暂时没有保存（${response.status}）`);
  return data;
 }
 function probeText(settings,override=null){
  const probe=override||settings.last_probe;
  if(probe?.configuration_stale)return '检测期间连接发生变化，请重新测试。';
  const models=probe?.requested_model?` 请求 ${probe.requested_model}${probe.returned_model?`，回传 ${probe.returned_model}`:''}。`:'';
  if(probe?.identity_status==='mismatch'||probe?.returned_model&&probe.model_identity_match===false)
   return `连接未通过：请求与回传模型名称不一致。${models}`;
  if(probe?.success)return probe.identity_status==='unreported'||!probe.returned_model?
   `连接可用，服务未回传模型名称，仅验证可调用。${models}`:`写作总管实测成功。${models}`;
  if(probe)return `连接未通过：${probe.error||'模型回复不符合测试要求。'}${models}`;
  if(!settings.configured)return settings.mode==='inherit'?'请先选择默认写作模型。':
   settings.mode==='library'?'所选模型已失效，请重新选择。':'独立连接尚未验证，请测试后保存。';
  return '当前模型已配置。';
 }
 function draw(host,settings,captured,token,{suppressDiscovery=false}={}){
  disposeCurrent?.();disposeCurrent=null;
  const current=()=>token===sequence&&host.isConnected&&sameScope(captured,scope());
  const independent=settings.independent_settings||{},parameters=independent.parameters||{};
  let library=Array.isArray(settings.available_connections)?settings.available_connections:[];
  const selected=['inherit','library','independent'].includes(settings.mode)?settings.mode:'inherit';
  const providers=MoyuModelPicker.providers;
  host.innerHTML=`<form id="agentConnectionSettings" class="agent-connection-settings">
   <div class="agent-settings-heading"><h3>写作总管模型</h3><span data-agent-connection-state>${settings.configured?'可用':'待配置'}</span></div>
   <fieldset class="agent-connection-modes"><legend>对话使用</legend>
    <label><input type="radio" name="agentConnectionMode" value="inherit" ${selected==='inherit'?'checked':''}>默认写作模型</label>
    <label><input type="radio" name="agentConnectionMode" value="library" ${selected==='library'?'checked':''}>模型库中的其他模型</label>
    <label><input type="radio" name="agentConnectionMode" value="independent" ${selected==='independent'?'checked':''}>独立自定义连接</label>
   </fieldset>
   <p class="agent-connection-summary" data-agent-inherit-summary></p>
   <div class="agent-library-fields" hidden><label for="agentLibraryConnection">已验证模型</label>
    <select id="agentLibraryConnection"></select><p class="agent-library-empty" hidden>模型库暂无可用模型。</p></div>
   <fieldset class="agent-independent-fields"><legend>独立连接</legend><div class="agent-settings-grid">
    <label>服务商<select id="agentProvider">${providers.map(item=>`<option value="${item.id}" ${item.id===(independent.provider||'openai_compatible')?'selected':''}>${escape(item.label)}</option>`).join('')}</select></label>
    <label>接入方式<select id="agentApiProtocol"><option value="chat_completions" ${independent.api_protocol!=='responses'?'selected':''}>通用兼容（Chat）</option><option value="responses" ${independent.api_protocol==='responses'?'selected':''}>Responses</option></select></label>
    <label class="agent-settings-wide">服务地址<input id="agentBaseUrl" type="url" maxlength="2048" autocomplete="off" spellcheck="false" value="${escape(independent.base_url||'')}" placeholder="https://example.com/v1"></label>
    <label class="agent-settings-wide">访问密钥<input id="agentApiKey" type="password" maxlength="4096" autocomplete="new-password" spellcheck="false" placeholder="${independent.api_key_configured?'已保存，留空保留':'本机免密模型可留空'}"></label>
    <div class="moyu-model-field"><label for="agentModel">模型名称</label><div class="moyu-model-row"><div class="moyu-model-combobox">
     <input id="agentModel" maxlength="200" autocomplete="off" spellcheck="false" value="${escape(independent.model||'')}" placeholder="搜索、选择或手动填写模型名称" aria-describedby="agentModelListStatus">
     <button type="button" class="ghost-btn" id="agentModelToggle" aria-label="显示总管模型列表">选择模型</button>
     <div id="agentModelList" class="moyu-model-options" hidden></div></div><button type="button" class="ghost-btn" id="agentFetchModels">获取模型列表</button></div>
     <p id="agentModelListStatus" class="moyu-model-status" role="status" hidden></p></div>
   </div>
   <details class="agent-settings-compatibility"><summary>高级与兼容设置</summary><div class="agent-settings-grid">
    <label>温度<input id="agentTemperature" type="number" min="0" max="2" step="0.05" value="${escape(parameters.temperature??0.35)}" required></label>
    <label>输出长度上限<input id="agentMaxTokens" type="number" min="1" max="128000" step="1" value="${escape(parameters.max_tokens??3000)}" required></label>
    <label class="agent-settings-wide">思考设置<select id="agentThinking">
     ${[['default','模型默认'],['disabled','关闭思考（DeepSeek）'],['enabled','开启思考（DeepSeek）'],['none','通用：关闭'],['minimal','通用：极低'],['low','通用：低'],['medium','通用：中'],['high','通用：高'],['xhigh','通用：极高']].map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label>
    <label class="agent-settings-wide">自定义请求路径<input id="agentCustomRequestPath" maxlength="2048" value="${escape(independent.custom_request_path||'')}" placeholder="/v1/chat/completions"></label>
    <label class="agent-settings-wide">模型列表路径<input id="agentModelsPath" maxlength="2048" value="${escape(independent.models_path||'')}" placeholder="/v1/models"></label>
    <label class="agent-settings-wide">请求标识（User-Agent）<input id="agentUserAgent" maxlength="256" value="${escape(independent.user_agent||'')}" placeholder="Moyu/1"></label>
   </div><div class="agent-settings-grid agent-chat-compatibility">
    <label>输出长度参数<select id="agentOutputTokenParameter"><option value="max_tokens" ${independent.output_token_parameter!=='max_completion_tokens'?'selected':''}>常规（max_tokens）</option><option value="max_completion_tokens" ${independent.output_token_parameter==='max_completion_tokens'?'selected':''}>兼容（max_completion_tokens）</option></select></label>
    <label class="agent-settings-checkbox"><input id="agentIncludeUsage" type="checkbox" ${independent.include_usage===true?'checked':''}>请求流式用量统计</label>
   </div><p id="agentEndpointPreview" class="agent-endpoint-preview"></p>
   <label class="agent-key-clear"><input id="agentClearApiKey" type="checkbox">清除已保存的独立密钥</label></details>
   <p id="agentProtocolWarning" class="settings-protocol-warning" role="status" hidden></p></fieldset>
   <p id="agentProbeStatus" class="agent-settings-status" role="status">${escape(probeText(settings))}</p>
   <p class="form-error" role="alert"></p>
   <div class="agent-settings-actions"><button type="button" class="ghost-btn" id="agentConnectionProbe">测试连接</button><button type="submit" class="primary-btn" id="agentConnectionSave">保存总管设置</button></div>
  </form>`;
  const form=host.querySelector('form'),field=id=>form.querySelector('#'+id);
  field('agentThinking').value=parameters.thinking?.type||parameters.reasoning_effort||'default';
  let busy=false,receipt=null,refreshSequence=0,savedKeyReusable=true;
  const mode=()=>form.querySelector('[name="agentConnectionMode"]:checked').value;
  function options(){
   return {provider:field('agentProvider').value,api_protocol:field('agentApiProtocol').value,
    output_token_parameter:field('agentOutputTokenParameter').value,include_usage:field('agentIncludeUsage').checked,
    custom_request_path:field('agentCustomRequestPath').value.trim(),models_path:field('agentModelsPath').value.trim(),
    user_agent:field('agentUserAgent').value.trim()};
  }
  function discoverySnapshot(){
   const body={base_url:field('agentBaseUrl').value.trim(),...options()};
   const key=field('agentApiKey').value.trim(),clear=field('agentClearApiKey').checked;
   const reusable=savedKeyReusable&&independent.api_key_configured&&
    body.provider===(independent.provider||'openai_compatible')&&
    MoyuModelPicker.sameService(body.base_url,independent.base_url);
   if(key||!reusable||clear)body.api_key=key;if(clear)body.clear_api_key=true;
   const result=MoyuModelPicker.connection(body,!!reusable);
   return independent.api_key_configured&&!reusable&&!key&&!clear?
    {...result,ready:false,hint:'服务商或地址已变更，请重新填写该服务的密钥。'}:result;
  }
  const modelPicker=MoyuModelPicker.mount({
   input:field('agentModel'),list:field('agentModelList'),toggle:field('agentModelToggle'),
   refresh:field('agentFetchModels'),status:field('agentModelListStatus'),snapshot:discoverySnapshot,
   current:()=>current()&&form.isConnected&&mode()==='independent',
   request:(body,signal)=>request('/models',{method:'POST',body:JSON.stringify(body),signal},captured),
   onDispose:()=>{field('agentApiKey').value=''},
  });
  disposeCurrent=()=>modelPicker.dispose();
  function payload(){
   if(mode()==='inherit')return {mode:'inherit'};
   if(mode()==='library')return {mode:'library',connection_id:field('agentLibraryConnection').value};
   const nextParameters={...parameters,temperature:Number(field('agentTemperature').value),
    max_tokens:Number(field('agentMaxTokens').value)};
   delete nextParameters.thinking;delete nextParameters.reasoning_effort;
   const thinking=field('agentThinking').value;
   if(['enabled','disabled'].includes(thinking))nextParameters.thinking={type:thinking};
   else if(thinking!=='default')nextParameters.reasoning_effort=thinking;
   return {mode:'independent',model:field('agentModel').value.trim(),parameters:nextParameters,...discoverySnapshot().body};
  }
  function libraryOptions(selectedId=settings.connection_id||''){
   const verified=library.filter(item=>item.verified===true);
   field('agentLibraryConnection').innerHTML='<option value="">选择已验证模型</option>'+
    (selectedId&&!verified.some(item=>item.id===selectedId)?`<option value="${escape(selectedId)}" selected disabled>原模型已失效</option>`:'')+
    verified.map(item=>`<option value="${escape(item.id)}" ${item.id===selectedId?'selected':''}>${escape(item.name||item.model)} · ${escape(item.model)}</option>`).join('');
   form.querySelector('.agent-library-empty').hidden=verified.length>0;
  }
  function updateProtocol(){
   const responses=field('agentApiProtocol').value==='responses';
   form.querySelector('.agent-chat-compatibility').hidden=responses;
   field('agentMaxTokens').min=responses?'16':'1';
   const thinking=field('agentThinking'),invalid=responses&&['enabled','disabled'].includes(thinking.value);
   for(const option of thinking.options)if(['enabled','disabled'].includes(option.value))option.hidden=responses&&option.value!==thinking.value;
   thinking.setCustomValidity(invalid?'Responses 不支持 DeepSeek 专用思考设置，请选择通用思考强度。':'');
   field('agentProtocolWarning').hidden=!invalid;
   field('agentProtocolWarning').textContent=invalid?'当前 DeepSeek 专用思考设置不适用于 Responses，请调整后测试。':'';
   const userAgent=field('agentUserAgent');
   userAgent.setCustomValidity(userAgent.value.length>256||/[^\x20-\x7e]/.test(userAgent.value)?
    '请求标识请使用 256 字以内的英文、数字或标点。':'');
   const endpoints=MoyuModelPicker.endpoints(discoverySnapshot().body);
   field('agentEndpointPreview').textContent=endpoints?`请求地址：${endpoints.request}\n模型列表：${endpoints.models}`:'';
  }
  function updateSave(){
   field('agentConnectionSave').disabled=busy||(mode()==='independent'&&(!receipt||receipt.tuple!==JSON.stringify(payload())))||
    (mode()==='library'&&!library.some(item=>item.id===field('agentLibraryConnection').value&&item.verified===true));
  }
  function setMode(){
   const selected=mode();
   form.querySelector('.agent-independent-fields').hidden=selected!=='independent';
   form.querySelector('.agent-independent-fields').disabled=selected!=='independent'||busy;
   form.querySelector('.agent-library-fields').hidden=selected!=='library';
   field('agentLibraryConnection').disabled=selected!=='library'||busy;
   form.querySelector('[data-agent-inherit-summary]').hidden=selected!=='inherit';
   form.querySelector('[data-agent-inherit-summary]').textContent=settings.mode==='inherit'&&settings.model?
    `当前模型：${settings.model}`:'沿用主写作设置中的默认模型。';
   field('agentConnectionProbe').hidden=selected!=='independent';
   modelPicker.setDisabled(selected!=='independent'||busy,{clear:selected!=='independent'});
   updateProtocol();updateSave();if(selected==='independent'&&!busy)modelPicker.changed();
  }
  function lock(value){busy=value;form.querySelectorAll('input,select,button').forEach(element=>element.disabled=value);setMode()}
  function invalidate(){
   receipt=null;updateProtocol();updateSave();
   field('agentProbeStatus').textContent='当前修改尚未保存。'+(mode()==='independent'?'请先测试连接。':'');
  }
  form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
  form.addEventListener('invalid',event=>{const details=event.target.closest('details');if(details)details.open=true},true);
  form.querySelectorAll('[name="agentConnectionMode"]').forEach(element=>element.addEventListener('change',setMode));
  for(const id of ['agentBaseUrl','agentApiKey','agentApiProtocol','agentClearApiKey',
   'agentCustomRequestPath','agentModelsPath','agentUserAgent']){
   field(id).addEventListener('input',()=>modelPicker.changed());
   field(id).addEventListener('change',()=>modelPicker.changed());
   field(id).addEventListener('blur',()=>modelPicker.changed({immediate:true}));
  }
  field('agentProvider').addEventListener('change',()=>{
   const provider=providers.find(item=>item.id===field('agentProvider').value);
   if(!provider)return;
   savedKeyReusable=false;field('agentApiKey').value='';field('agentClearApiKey').checked=false;
   field('agentApiKey').placeholder='本机免密模型可留空';
   field('agentBaseUrl').value=provider.base_url;field('agentModel').value='';
   for(const id of ['agentCustomRequestPath','agentModelsPath','agentUserAgent'])field(id).value='';
   field('agentApiProtocol').value='chat_completions';field('agentThinking').value='default';
   field('agentOutputTokenParameter').value='max_tokens';field('agentIncludeUsage').checked=false;
   invalidate();modelPicker.changed({reset:true});
  });
  field('agentConnectionProbe').addEventListener('click',async()=>{
   if(busy||mode()!=='independent'||!form.reportValidity()||!current())return;
   const connection=discoverySnapshot();
   if(!connection.ready){receipt=null;updateSave();field('agentProbeStatus').textContent='连接测试未完成：'+connection.hint;return}
   const submitted=payload(),tuple=JSON.stringify(submitted);
   lock(true);receipt=null;form.querySelector('.form-error').textContent='';
   field('agentProbeStatus').textContent='正在测试，原有连接继续保留…';
   try{
    const result=await request('/probe',{method:'POST',body:JSON.stringify(submitted)},captured);
    if(!current()||!form.isConnected||tuple!==JSON.stringify(payload()))return;
    if(result.probe?.success===true&&result.verification_token){
     receipt={token:result.verification_token,tuple};
     field('agentProbeStatus').textContent=probeText(settings,result.probe)+' 保存后生效。';
    }else field('agentProbeStatus').textContent=probeText(settings,result.probe);
   }catch(error){if(current())field('agentProbeStatus').textContent=`连接测试未完成：${error.message}`}
   finally{if(current()&&form.isConnected)lock(false)}
  });
  form.addEventListener('submit',async event=>{
   event.preventDefault();if(busy||!current()||!form.reportValidity())return;
   const sent=payload();
   if(sent.mode==='independent'&&(!receipt||receipt.tuple!==JSON.stringify(sent))){invalidate();return}
   const body=sent.mode==='independent'?{mode:'independent',verification_token:receipt.token}:sent;
   lock(true);form.querySelector('.form-error').textContent='';
   try{
    const data=await request('',{method:'POST',body:JSON.stringify(body)},captured);
    field('agentApiKey').value='';if(!current())return;
    draw(host,data.settings||data,captured,token,{suppressDiscovery:true});
    host.querySelector('#agentProbeStatus').textContent='总管设置已保存。'+probeText(data.settings||data);
    window.dispatchEvent(new CustomEvent('moyu:agent-settings-changed'));
   }catch(error){if(current()){receipt=null;form.querySelector('.form-error').textContent=error.message||'总管设置未保存，请重新测试。'}}
   finally{if(current()&&form.isConnected)lock(false)}
  });
  refreshCurrent=async target=>{
   if((target&&target!==host)||busy||!current()||!form.isConnected)return;
   const revision=++refreshSequence,selectedId=field('agentLibraryConnection').value;
   try{
    const data=await request('',{method:'GET'},captured);
    if(revision!==refreshSequence||busy||!current()||!form.isConnected)return;
    settings=data.settings||data;library=settings.available_connections||[];
    libraryOptions(selectedId);form.querySelector('[data-agent-connection-state]').textContent=settings.configured?'可用':'待配置';
    if(mode()==='inherit')form.querySelector('[data-agent-inherit-summary]').textContent=
     settings.mode==='inherit'&&settings.model?`当前模型：${settings.model}`:'沿用主写作设置中的默认模型。';
    if(mode()===settings.mode&&mode()!=='independent')field('agentProbeStatus').textContent=probeText(settings);
    updateSave();
   }catch(error){if(current())form.querySelector('.form-error').textContent=error.message}
  };
  libraryOptions();setMode();modelPicker.changed({suppress:suppressDiscovery});
 }
 async function mount(host){
  if(!host)return;
  const token=++sequence,captured=scope();disposeCurrent?.();disposeCurrent=null;refreshCurrent=null;
  const current=()=>token===sequence&&host.isConnected&&sameScope(captured,scope());
  host.innerHTML='<p class="agent-settings-loading" role="status">正在读取总管模型…</p>';
  try{const data=await request('',{method:'GET'},captured);if(current())draw(host,data.settings||data,captured,token)}
  catch(error){
   if(!current())return;
   const message=location.protocol==='file:'?'请从墨屿桌面软件打开模型设置。':error.message||'总管设置暂时无法读取，请重新连接。';
   host.innerHTML=`<p class="form-error" role="alert">${escape(message)}</p><button type="button" class="ghost-btn" data-agent-settings-retry>重新连接</button>`;
   host.querySelector('[data-agent-settings-retry]').addEventListener('click',()=>mount(host));
  }
 }
 async function refresh(host){if(refreshCurrent)await refreshCurrent(host)}
 window.addEventListener('moyu:main-settings-changed',event=>{
  const current=scope(),detail=event.detail||{};
  if((detail.projectId&&detail.projectId!==current.project)||(detail.branchId&&detail.branchId!==current.branch))return;
  window.dispatchEvent(new CustomEvent('moyu:agent-settings-changed',{detail}));
  void refresh(document.querySelector('#agentSettingsHost'));
 });
 globalThis.MoyuAgentSettings={mount,refresh};
})();
