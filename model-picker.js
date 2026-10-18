'use strict';

(() => {
 const localHost = hostname => ['localhost', '127.0.0.1', '[::1]', '::1'].includes(hostname);
 function service(value) {
  try {
   const address = new URL(value);
   if (!['http:', 'https:'].includes(address.protocol) || address.username || address.password || address.search || address.hash) return '';
   let route = address.pathname.replace(/\/+$/, '');
   route = route.replace(/\/(?:chat\/completions|responses)$/, '');
   return address.origin + route;
  } catch { return ''; }
 }
 const sameService = (left, right) => !!service(left) && service(left) === service(right);

 function connection(body, hasSavedKey = false) {
  let address;
  try { address = new URL(body.base_url); } catch {}
  const conflictingKey = body.clear_api_key && !!body.api_key;
  const valid = !!service(body.base_url) && !conflictingKey;
  const hasKey = !!body.api_key || (hasSavedKey && !body.clear_api_key && !Object.hasOwn(body, 'api_key'));
  return {
   body,
   key: JSON.stringify([body.id || '', body.base_url,
    body.clear_api_key ? {clear: true, key: body.api_key ?? null} : Object.hasOwn(body, 'api_key') ? body.api_key : null,
    body.api_protocol || 'chat_completions']),
   valid,
   ready: valid && (hasKey || localHost(address.hostname)),
   hint: conflictingKey ? '填写新密钥与清除旧密钥只能选择一项。' :
    !body.base_url ? '填写服务地址后获取模型。' : !valid ? '请填写没有密码或查询参数的完整服务地址。' :
    '填写密钥后自动获取模型；免密服务可直接点击获取。',
  };
 }

 function mount({input, list, toggle, refresh, status, snapshot, current, request, onDispose = () => {}}) {
  let models = [], query = '', active = -1, opened = false, disposed = false, disabled = false;
  let revision = 0, controller = null, debounce = null, identity = '', attempted = '';
  const listeners = [];
  const listen = (target, event, action) => {
   target.addEventListener(event, action);
   listeners.push(() => target.removeEventListener(event, action));
  };
  const alive = () => !disposed && input.isConnected && current();
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-haspopup', 'listbox');
  input.setAttribute('aria-controls', list.id);
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '可选模型');
  toggle.setAttribute('aria-controls', list.id);
  toggle.setAttribute('aria-haspopup', 'listbox');

  function close() {
   opened = false;
   active = -1;
   list.hidden = true;
   input.setAttribute('aria-expanded', 'false');
   input.removeAttribute('aria-activedescendant');
   toggle.setAttribute('aria-expanded', 'false');
  }
  function choices() {
   return models.filter(model => !query || model.toLocaleLowerCase().includes(query));
  }
  function render() {
   const filtered = choices();
   list.replaceChildren();
   if (active >= filtered.length) active = filtered.length - 1;
   filtered.forEach((model, index) => {
    const option = document.createElement('div');
    option.id = list.id + '-option-' + index;
    option.className = 'moyu-model-option';
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(index === active));
    option.dataset.modelValue = model;
    option.textContent = model;
    list.append(option);
   });
   if (!filtered.length) {
    const empty = document.createElement('div');
    empty.className = 'moyu-model-empty';
    empty.textContent = models.length ? '没有匹配的模型，可保留手动填写的名称。' : '暂无可选模型，可手动填写名称。';
    list.append(empty);
   }
   list.hidden = !opened;
   input.setAttribute('aria-expanded', String(opened));
   toggle.setAttribute('aria-expanded', String(opened));
   const selected = list.querySelector('[aria-selected="true"]');
   if (selected && opened) {
    input.setAttribute('aria-activedescendant', selected.id);
    selected.scrollIntoView({block: 'nearest'});
   } else input.removeAttribute('aria-activedescendant');
  }
  function open() {
   if (!alive() || disabled || input.disabled) return;
   opened = true;
   render();
  }
  function select(model) {
   input.value = model;
   query = '';
   close();
   input.dispatchEvent(new Event('input', {bubbles: true}));
   input.dispatchEvent(new Event('change', {bubbles: true}));
   close();
   input.focus();
  }
  function abort(clear = false) {
   clearTimeout(debounce);
   debounce = null;
   ++revision;
   controller?.abort();
   controller = null;
   refresh.disabled = disabled;
   input.setAttribute('aria-busy', 'false');
   if (clear) {
    models = [];
    query = '';
    close();
    list.replaceChildren();
   }
  }
  function show(message, state) {
   status.hidden = !message;
   status.textContent = message;
   status.dataset.state = state;
  }
  async function load(force = false) {
   clearTimeout(debounce);
   debounce = null;
   if (!alive() || disabled) return;
   const submitted = snapshot();
   if (!submitted.valid || (!force && !submitted.ready)) {
    show(submitted.hint, 'untested');
    return;
   }
   if (controller && identity === submitted.key) return;
   if (!force && attempted === submitted.key) return;
   abort();
   identity = submitted.key;
   attempted = submitted.key;
   const token = revision, pending = new AbortController();
   controller = pending;
   refresh.disabled = true;
   input.setAttribute('aria-busy', 'true');
   show('正在获取模型列表…', 'loading');
   const valid = () => alive() && token === revision && submitted.key === snapshot().key;
   let timedOut = false;
   const timeout = setTimeout(() => { timedOut = true; pending.abort(); }, 30000);
   try {
    const result = await request(submitted.body, pending.signal);
    if (!valid()) return;
    if (!Array.isArray(result.models)) throw new Error('服务返回的模型列表格式不正确。');
    models = [...new Set(result.models.filter(model => typeof model === 'string')
     .map(model => model.trim()).filter(model => model && model.length <= 200 && !/[\x00-\x1f\x7f]/.test(model)))].slice(0, 1000);
    query = ''; active = -1;
    render();
    show(models.length ? `已获取 ${models.length} 个模型。` : '服务未提供可选模型，请手动填写模型名称。', 'success');
    refresh.textContent = '刷新模型';
   } catch (error) {
    if (valid()) {
     models = [];
     render();
     show(timedOut ? '获取模型列表超时，请重试。' : `获取模型列表失败：${error.message || '连接中断，请重试。'}`, 'failed');
     refresh.textContent = '重新获取';
    }
   } finally {
    clearTimeout(timeout);
    if (token === revision) {
     controller = null;
     input.setAttribute('aria-busy', 'false');
     refresh.disabled = disabled;
    }
   }
  }
  function changed({immediate = false, suppress = false, reset = false} = {}) {
   if (!alive()) return;
   const next = snapshot();
   if (reset || next.key !== identity) {
    abort(true);
    identity = next.key;
    attempted = '';
    refresh.textContent = '获取模型列表';
    show(next.hint, 'untested');
   }
   if (suppress) {
    abort();
    attempted = next.key;
    return;
   }
   if (disabled || !next.ready || attempted === next.key || controller) return;
   clearTimeout(debounce);
   if (immediate) void load();
   else debounce = setTimeout(() => { void load(); }, 600);
  }
  function setDisabled(value, {clear = false} = {}) {
   disabled = value;
   if (value) {
    abort(clear); close();
    if (clear) attempted = '';
   }
   refresh.disabled = value || !!controller;
   toggle.disabled = value;
  }
  function dispose() {
   if (disposed) return;
   disposed = true;
   abort(true);
   identity = '';
   attempted = '';
   observer.disconnect();
   listeners.splice(0).forEach(remove => remove());
   onDispose();
  }
  listen(input, 'input', () => { query = input.value.trim().toLocaleLowerCase(); active = -1; open(); });
  listen(input, 'focus', () => { if (models.length) open(); });
  listen(input, 'keydown', event => {
   if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault();
    const count = choices().length;
    opened = true;
    active = count ? active < 0 ? (event.key === 'ArrowDown' ? 0 : count - 1) :
     (active + (event.key === 'ArrowDown' ? 1 : -1) + count) % count : -1;
    render();
   } else if (event.key === 'Enter' && opened && active >= 0) {
    event.preventDefault();
    const model = choices()[active];
    if (model) select(model);
   } else if (event.key === 'Escape' && opened) {
    event.preventDefault();
    event.stopPropagation();
    close();
   } else if (event.key === 'Tab') close();
  });
  listen(toggle, 'click', () => { if (opened) close(); else { query = ''; input.focus(); open(); } });
  listen(list, 'mousedown', event => { if (event.target.closest('[role="option"]')) event.preventDefault(); });
  listen(list, 'click', event => {
   const option = event.target.closest('[role="option"]');
   if (option && list.contains(option)) select(option.dataset.modelValue);
  });
  listen(refresh, 'click', () => { void load(true); });
  listen(document, 'pointerdown', event => {
   if (!input.parentElement.contains(event.target)) close();
  });
  listen(input.parentElement, 'focusout', event => {
   if (!input.parentElement.contains(event.relatedTarget)) close();
  });
  const observer = new MutationObserver(() => { if (!input.isConnected) dispose(); });
  observer.observe(document.body, {childList: true, subtree: true});
  close();
  return {changed, setDisabled, dispose};
 }
 globalThis.MoyuModelPicker = {connection, mount, sameService};
})();
