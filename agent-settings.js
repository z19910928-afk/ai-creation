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
  if(probe?.configuration_stale)return '本次检测已失效：检测期间连接设置发生变化，请重新测试当前设置。';
  if(!settings.configured)return settings.mode==='inherit'?'主写作模型尚未配置。':'独立模型尚未配置。';
  if(!probe)return settings.probe_stale?'连接设置已变化，当前连接尚未验证。':'设置已保存，当前连接尚未验证。';
  const models=probe.requested_model?` 请求 ${probe.requested_model}${probe.returned_model?`，回传 ${probe.returned_model}`:''}。`:'';
  if(probe.identity_status==='mismatch'||probe.returned_model&&probe.model_identity_match===false)
   return `连接检测未通过：请求模型与服务回传名称不一致。${models}按服务回传名称重新选择并测试，或向服务商确认别名。`;
  if(probe.success&&(probe.identity_status==='unreported'||!probe.returned_model))
   return `连接可用，服务未回传模型名称，仅验证可调用。${models}`;
  return probe.success?`写作总管实测成功。${models}`:`连接检测未通过：${probe.error||'模型回复不符合测试要求。'}${models}`;
 }
 function scopeText(settings){
  return '独立连接用于总管对话；续写、润色和创作规划使用主写作设置中的默认模型或对应的用途模型。'+
   (settings.daily_limit?`每日共享上限 ${settings.daily_limit} 次。`:'与主写作共用每日额度。');
 }
 function draw(host,settings,captured,token,{discover=true}={}){
  disposeCurrent?.();disposeCurrent=null;
  const current=()=>token===sequence&&host.isConnected&&sameScope(captured,scope());
  const independent=settings.independent_settings||{};
  const parameters=independent.parameters||{temperature:0.35,max_tokens:3000};
  const selected=settings.mode==='independent'?'independent':'inherit';
  host.innerHTML=`<form id="agentConnectionSettings" class="agent-connection-settings">
   <div class="agent-settings-heading"><h3>写作总管连接</h3><span data-agent-connection-state>${settings.configured?'已配置':'待配置'}</span></div>
   <fieldset class="agent-connection-modes"><legend>对话模型</legend>
    <label><input type="radio" name="agentConnectionMode" value="inherit" ${selected==='inherit'?'checked':''}>沿用主写作连接</label>
    <label><input type="radio" name="agentConnectionMode" value="independent" ${selected==='independent'?'checked':''}>独立连接</label>
   </fieldset>
   <p class="agent-connection-summary" data-agent-inherit-summary>${escape(settings.mode==='inherit'&&settings.model?`当前模型：${settings.model}`:'沿用主写作设置中的默认模型。')}</p>
   <fieldset class="agent-independent-fields"><legend>独立对话模型</legend><div class="agent-settings-grid">
    <label class="agent-settings-wide">服务地址<input id="agentBaseUrl" type="url" maxlength="2048" autocomplete="off" spellcheck="false" value="${escape(independent.base_url||'')}" placeholder="https://…/v1"></label>
    <label class="agent-settings-wide">本地密钥<input id="agentApiKey" type="password" maxlength="4096" autocomplete="new-password" spellcheck="false" placeholder="${independent.api_key_configured?'已保存；留空保留原密钥':'本机免密模型可留空'}"></label>
    <label class="agent-settings-wide">接入方式<select id="agentApiProtocol">
     <option value="chat_completions" ${independent.api_protocol!=='responses'?'selected':''}>通用兼容（Chat）</option>
     <option value="responses" ${independent.api_protocol==='responses'?'selected':''}>Responses</option>
    </select></label>
    <div class="moyu-model-field"><label for="agentModel">模型名称</label><div class="moyu-model-row"><div class="moyu-model-combobox">
     <input id="agentModel" maxlength="200" autocomplete="off" spellcheck="false" value="${escape(independent.model||'')}" placeholder="搜索、选择或手动填写模型名称" aria-describedby="agentModelListStatus">
     <button type="button" class="ghost-btn" id="agentModelToggle" aria-label="显示总管模型列表">选择模型</button>
     <div id="agentModelList" class="moyu-model-options" hidden></div></div>
     <button type="button" class="ghost-btn" id="agentFetchModels">获取模型列表</button></div>
     <p id="agentModelListStatus" class="moyu-model-status" role="status" hidden></p></div>
    <label>温度<input id="agentTemperature" type="number" min="0" max="2" step="0.05" value="${escape(parameters.temperature??0.35)}" required></label>
    <label>输出长度上限<input id="agentMaxTokens" type="number" min="1" max="128000" step="1" value="${escape(parameters.max_tokens??3000)}" required></label>
    <label class="agent-settings-wide">思考设置<select id="agentThinking">
     <option value="default">模型默认</option><option value="disabled">关闭思考</option><option value="enabled">开启思考</option>
     <option value="none">思考强度：关闭</option><option value="minimal">思考强度：极低</option>
     <option value="low">思考强度：低</option><option value="medium">思考强度：中</option>
     <option value="high">思考强度：高</option><option value="xhigh">思考强度：极高</option>
    </select></label>
   </div><p id="agentProtocolWarning" class="settings-protocol-warning" role="status" hidden></p>
   <details class="agent-settings-compatibility"><summary>兼容设置</summary><div class="agent-settings-grid">
    <label class="agent-settings-wide">输出长度参数<select id="agentOutputTokenParameter">
     <option value="max_tokens" ${independent.output_token_parameter!=='max_completion_tokens'?'selected':''}>常规（max_tokens）</option>
     <option value="max_completion_tokens" ${independent.output_token_parameter==='max_completion_tokens'?'selected':''}>兼容（max_completion_tokens）</option>
    </select></label><label class="agent-settings-wide agent-settings-checkbox"><input id="agentIncludeUsage" type="checkbox" ${independent.include_usage===true?'checked':''}>请求流式用量统计</label>
   </div></details><label class="agent-key-clear"><input id="agentClearApiKey" type="checkbox">清除已保存的独立密钥</label></fieldset>
   <p class="agent-settings-scope" data-agent-settings-scope>${escape(scopeText(settings))}</p>
   <p id="agentProbeStatus" class="agent-settings-status" role="status">${escape(probeText(settings))}</p>
   <p class="form-error" role="alert"></p>
   <div class="agent-settings-actions"><button type="button" class="ghost-btn" id="agentConnectionProbe">实测连接（调用模型）</button><button type="submit" class="primary-btn">保存总管设置</button></div>
  </form>`;
  const form=host.querySelector('form'),field=id=>form.querySelector('#'+id);
  const thinking=parameters.thinking?.type||parameters.reasoning_effort||'default';
  field('agentThinking').value=thinking;
  let busy=false,refreshSequence=0;
  const discoverySnapshot=()=>{
   const body={base_url:field('agentBaseUrl').value.trim(),api_protocol:field('agentApiProtocol').value};
   const key=field('agentApiKey').value.trim(),clear=field('agentClearApiKey').checked;
   const reusable=independent.api_key_configured&&MoyuModelPicker.sameService(body.base_url,independent.base_url);
   if(key||!reusable||clear)body.api_key=key;
   if(clear)body.clear_api_key=true;
   const result=MoyuModelPicker.connection(body,!!reusable);
   return independent.api_key_configured&&!reusable&&!key&&!clear?
    {...result,ready:false,hint:'服务地址已变更，请重新填写该服务的密钥。'}:result;
  };
  const modelPicker=MoyuModelPicker.mount({
   input:field('agentModel'),list:field('agentModelList'),toggle:field('agentModelToggle'),
   refresh:field('agentFetchModels'),status:field('agentModelListStatus'),snapshot:discoverySnapshot,
   current:()=>current()&&form.isConnected&&form.querySelector('[name="agentConnectionMode"]:checked')?.value==='independent',
   request:(body,signal)=>request('/models',{method:'POST',body:JSON.stringify(body),signal},captured),
   onDispose:()=>{field('agentApiKey').value=''},
  });
  disposeCurrent=()=>modelPicker.dispose();
  refreshCurrent=async target=>{
   if((target&&target!==host)||busy||!current()||!form.isConnected)return;
   const revision=++refreshSequence;
   const valid=()=>revision===refreshSequence&&!busy&&current()&&form.isConnected;
   form.querySelector('[data-agent-connection-state]').textContent='正在同步';
   field('agentProbeStatus').textContent='正在更新主写作连接对应的总管状态…';
   try{
    const data=await request('',{method:'GET'},captured);
    if(!valid())return;
    settings=data.settings||data;
    form.querySelector('[data-agent-connection-state]').textContent=settings.configured?'已配置':'待配置';
    form.querySelector('[data-agent-inherit-summary]').textContent=
     settings.mode==='inherit'&&settings.model?`当前模型：${settings.model}`:'沿用主写作设置中的默认模型。';
    form.querySelector('[data-agent-settings-scope]').textContent=scopeText(settings);
    field('agentProbeStatus').textContent=JSON.stringify(payload())===initialDraft?probeText(settings):'连接信息已修改，请保存并重新实测。';
   }catch(error){
    if(!valid())return;
    form.querySelector('[data-agent-connection-state]').textContent='状态待核对';
    field('agentProbeStatus').textContent=error.message||'总管连接状态读取失败，请重新打开设置核对。';
   }
  };
  function setMode(){
   const inherited=form.querySelector('[name="agentConnectionMode"]:checked').value==='inherit';
   form.querySelector('.agent-independent-fields').hidden=inherited;
   form.querySelector('.agent-independent-fields').disabled=inherited||busy;
   form.querySelector('[data-agent-inherit-summary]').hidden=!inherited;
   modelPicker.setDisabled(inherited||busy,{clear:inherited});
   updateProtocol();if(!inherited&&!busy)modelPicker.changed();
  }
  function lock(value){
   busy=value;
   form.querySelectorAll('input,select,button').forEach(element=>element.disabled=value);
   setMode();
  }
  function updateProtocol(){
   const responses=field('agentApiProtocol').value==='responses';
   form.querySelector('.agent-settings-compatibility').hidden=responses;
   field('agentMaxTokens').min=responses?'16':'1';
   const thinking=field('agentThinking');
   for(const option of thinking.options)if(['enabled','disabled'].includes(option.value))
    option.hidden=responses&&option.value!==thinking.value;
   const invalid=responses&&['enabled','disabled'].includes(thinking.value);
   thinking.setCustomValidity(invalid?'Responses 不支持 DeepSeek 专用思考设置，请选择通用思考强度。':'');
   const warnings=[];
   if(invalid)warnings.push('当前 DeepSeek 专用思考设置不适用于 Responses，请调整后保存。');
   if(responses&&Number(field('agentMaxTokens').value)<16)warnings.push('Responses 的输出长度上限至少为 16。');
   field('agentProtocolWarning').hidden=!warnings.length;field('agentProtocolWarning').textContent=warnings.join(' ');
  }
  function payload(){
   const mode=form.querySelector('[name="agentConnectionMode"]:checked').value;
   if(mode==='inherit')return {mode};
   const nextParameters={...parameters,temperature:Number(field('agentTemperature').value),
    max_tokens:Number(field('agentMaxTokens').value)};
   delete nextParameters.thinking;delete nextParameters.reasoning_effort;
   const thinkingMode=field('agentThinking').value;
   if(['enabled','disabled'].includes(thinkingMode))nextParameters.thinking={type:thinkingMode};
   else if(thinkingMode!=='default')nextParameters.reasoning_effort=thinkingMode;
   const next={mode,base_url:field('agentBaseUrl').value.trim(),model:field('agentModel').value.trim(),
    api_protocol:field('agentApiProtocol').value,
    parameters:nextParameters,output_token_parameter:field('agentOutputTokenParameter').value,
    include_usage:field('agentIncludeUsage').checked};
   const key=field('agentApiKey').value.trim();
   if(key||!independent.api_key_configured)next.api_key=key;
   if(field('agentClearApiKey').checked)next.clear_api_key=true;
   return next;
  }
  const initialDraft=JSON.stringify(payload());
  const updateDraftStatus=()=>{
   field('agentProbeStatus').textContent=JSON.stringify(payload())===initialDraft?probeText(settings):'连接信息已修改，请保存并重新实测。';
  };
  async function perform(test){
   if(busy||!current()||!form.reportValidity())return;
   if(form.querySelector('[name="agentConnectionMode"]:checked').value==='independent'&&
    independent.api_key_configured&&!field('agentApiKey').value.trim()&&!field('agentClearApiKey').checked&&
    !MoyuModelPicker.sameService(field('agentBaseUrl').value.trim(),independent.base_url)){
    form.querySelector('.form-error').textContent='服务地址已变更，请重新填写该服务的密钥后保存或测试。';return;
   }
   const sent=payload();lock(true);form.querySelector('.form-error').textContent='';
   if(test)field('agentProbeStatus').textContent='正在保存并实测连接…';
   try{
    const saved=await request('',{method:'POST',body:JSON.stringify(sent)},captured);
    field('agentApiKey').value='';
    if(!current())return;
    let latest=saved.settings||saved,override=null;
    window.dispatchEvent(new CustomEvent('moyu:agent-settings-changed'));
    if(test){
     field('agentProbeStatus').textContent='正在等待模型测试回复…';
     const result=await request('/probe',{method:'POST',body:'{}'},captured);
     if(!current())return;
     latest=result.settings||latest;override=result.probe||result;
     window.dispatchEvent(new CustomEvent('moyu:agent-settings-changed'));
    }
    draw(host,latest,captured,token,{discover:false});
    if(override)host.querySelector('#agentProbeStatus').textContent=probeText(latest,override);
    else host.querySelector('#agentProbeStatus').textContent='总管设置已保存。'+probeText(latest);
   }catch(error){
    if(current()){
     form.querySelector('.form-error').textContent=error.message||'连接设置暂时没有保存。';
     if(test)field('agentProbeStatus').textContent='连接检测未完成。';
    }
   }finally{if(current()&&form.isConnected)lock(false)}
  }
  form.querySelectorAll('[name="agentConnectionMode"]').forEach(element=>element.addEventListener('change',setMode));
  form.addEventListener('input',updateDraftStatus);form.addEventListener('change',updateDraftStatus);
  for(const id of ['agentBaseUrl','agentApiKey','agentApiProtocol','agentClearApiKey']){
   field(id).addEventListener('input',()=>modelPicker.changed());
   field(id).addEventListener('change',()=>{updateProtocol();modelPicker.changed()});
   field(id).addEventListener('blur',()=>modelPicker.changed({immediate:true}));
  }
  for(const id of ['agentThinking','agentMaxTokens'])field(id).addEventListener('input',updateProtocol);
  form.addEventListener('submit',event=>{event.preventDefault();perform(false)});
  field('agentConnectionProbe').addEventListener('click',()=>perform(true));
  setMode();if(!discover)modelPicker.changed({suppress:true});
 }
 async function mount(host){
  if(!host)return;
  const token=++sequence,captured=scope();
  disposeCurrent?.();disposeCurrent=null;
  refreshCurrent=null;
  const current=()=>token===sequence&&host.isConnected&&sameScope(captured,scope());
  host.innerHTML='<p class="agent-settings-loading" role="status">正在读取写作总管连接…</p>';
  try{
   const data=await request('',{method:'GET'},captured);
   if(current())draw(host,data.settings||data,captured,token);
  }catch(error){
   if(!current())return;
   host.innerHTML=`<p class="form-error" role="alert">${escape(error.message||'总管连接设置没有读取。')}</p><button type="button" class="ghost-btn" data-agent-settings-retry>重新连接</button>`;
   host.querySelector('[data-agent-settings-retry]').addEventListener('click',()=>mount(host));
  }
 }
 async function refresh(host){
  if(refreshCurrent)await refreshCurrent(host);
 }
 window.addEventListener('moyu:main-settings-changed',event=>{
  const current=scope(),detail=event.detail||{};
  if((detail.projectId&&detail.projectId!==current.project)||(detail.branchId&&detail.branchId!==current.branch))return;
  window.dispatchEvent(new CustomEvent('moyu:agent-settings-changed',{detail}));
  void refresh(document.querySelector('#agentSettingsHost'));
 });
 globalThis.MoyuAgentSettings={mount,refresh};
})();
