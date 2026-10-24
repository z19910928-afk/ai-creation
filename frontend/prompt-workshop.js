'use strict';
/* 墨屿提示词工坊面板（原生脚本，无构建，无框架）。
 *
 * 依赖 app.js 暴露的全局：views / esc / toast。
 * 数据面：
 *   GET    /api/prompt-templates          列表
 *   POST   /api/prompt-templates          新建自定义模板
 *   PUT    /api/prompt-templates/{id}     改名称/正文（仅自定义）
 *   DELETE /api/prompt-templates/{id}     删除（仅自定义）
 *   POST   /api/prompt-templates/{id}/reset  恢复出厂正文
 *
 * 内置模板只读：正文与名称不可改、不可删，只能「另存为自定义」或「重置为默认」。
 * 自定义模板与内置模板同动作时，就是该动作的覆盖版本（后端按 is_builtin 优先
 * 应用内置、再让自定义覆盖）。同一动作可以有多条自定义，但渲染时只有 updated_at
 * 最新、rowid 最大的那条生效：列表在内置行标「被覆盖」，并在真正生效的自定义行
 * 标「生效中」，其余自定义行提示本条不生效。
 *
 * 渲染约定：views.prompts 先同步返回骨架，MutationObserver 监听 #main 出现
 * [data-prompt-root] 后再异步取数并重绘。正文以 draftBody 为唯一来源，输入时
 * 只更新 draft 与脏标记，不重建 DOM——否则光标会被打断。
 */
(function () {
  const GROUP_ORDER = ['ai.task', 'ai.common', 'chat', 'planning', 'revision', 'probe', 'other'];
  const GROUP_LABELS = {
    'ai.task': 'AI 任务指令',
    'ai.common': 'AI 通用片段',
    chat: '写作总管对话',
    planning: '创作规划',
    revision: '候选整理',
    probe: '连接测试',
    other: '其他'
  };

  const OFFLINE_TEXT = '无法连接本机服务，提示词工坊暂时不可用。请先启动墨屿本地服务，再点击「刷新」；离线时仍可继续写作，本面板不会改动任何稿件。';
  const BUILTIN_HINT = '内置模板受保护，请另存为自定义模板或重置。';
  const INACTIVE_HINT = '同动作已有生效版本，本条不会生效';

  let templates = [];
  let selectedId = null;
  let creating = false;
  let loading = false;
  let busy = false;
  let errorText = '';
  let actionError = '';
  let filterText = '';
  let draftName = '';
  let draftBody = '';
  let draftAction = '';
  let requestSequence = 0;
  let mountedRoot = null;
  let observer = null;

  /* ---------- 数据读取 ---------- */

  function groupOf(action) {
    const value = String(action || '');
    if (value.indexOf('ai.task.') === 0) return 'ai.task';
    if (value.indexOf('ai.') === 0) return 'ai.common';
    if (value.indexOf('chat.') === 0) return 'chat';
    if (value.indexOf('planning.') === 0) return 'planning';
    if (value.indexOf('revision.') === 0) return 'revision';
    if (value.indexOf('probe.') === 0) return 'probe';
    return 'other';
  }

  function isTemplate(value) {
    return Boolean(value) && typeof value === 'object' &&
      typeof value.id === 'string' && value.id !== '' &&
      typeof value.action === 'string' &&
      typeof value.name === 'string' &&
      typeof value.body === 'string';
  }

  function compareTemplates(left, right) {
    const g1 = GROUP_ORDER.indexOf(groupOf(left.action));
    const g2 = GROUP_ORDER.indexOf(groupOf(right.action));
    if (g1 !== g2) return g1 - g2;
    if (left.action !== right.action) return left.action < right.action ? -1 : 1;
    if (Boolean(left.is_builtin) !== Boolean(right.is_builtin)) return left.is_builtin ? -1 : 1;
    return left.name < right.name ? -1 : left.name > right.name ? 1 : 0;
  }

  function current() {
    return templates.find(item => item.id === selectedId) || null;
  }

  function builtinFor(action) {
    return templates.find(item => item.is_builtin && item.action === action) || null;
  }

  function customsFor(action) {
    return templates.filter(item => !item.is_builtin && item.action === action);
  }

  /* 后端渲染表按 ORDER BY is_builtin DESC, updated_at, rowid 逐条覆盖写入，
   * 所以同一动作真正生效的是 updated_at 最新、同时刻 rowid 最大的自定义模板。
   * 列表接口不返回 rowid，但它按 rowid 升序返回，这里用加载时的下标当代理。 */
  function rowOrder(template) {
    return typeof template._order === 'number' ? template._order : 0;
  }

  function recencyCompare(left, right) {
    const t1 = Date.parse(String(left.updated_at || '')) || 0;
    const t2 = Date.parse(String(right.updated_at || '')) || 0;
    if (t1 !== t2) return t1 - t2;
    return rowOrder(left) - rowOrder(right);
  }

  function effectiveCustomFor(action) {
    let winner = null;
    customsFor(action).forEach(item => {
      if (!winner || recencyCompare(item, winner) > 0) winner = item;
    });
    return winner;
  }

  function isEffective(template) {
    if (!template || template.is_builtin) return false;
    const info = effectiveInfo(template);
    return Boolean(info) && info.active;
  }

  /* 「生效」只在有内置模板的动作上成立：自定义覆盖内置，同动作多条时只有
   * 最新保存的那条参与渲染。没有内置模板的动作不会被任何流程调用，不标生效。 */
  function effectiveInfo(template) {
    if (!template) return null;
    const action = template.action;
    const customs = customsFor(action);
    const winner = effectiveCustomFor(action);
    if (!builtinFor(action)) {
      return { active: false, orphan: true, winner: winner, customs: customs };
    }
    if (template.is_builtin) {
      return { active: customs.length === 0, orphan: false, winner: winner, customs: customs };
    }
    return {
      active: Boolean(winner) && winner.id === template.id,
      orphan: false,
      winner: winner,
      customs: customs
    };
  }

  function effectiveStateHtml(template) {
    const info = effectiveInfo(template);
    if (!info) return '';
    const others = info.customs.length - (info.active && !template.is_builtin ? 1 : 0);
    let text = '';
    let state = info.active ? 'yes' : 'no';
    if (info.orphan) {
      state = 'none';
      text = '该动作没有内置模板：本条自定义不覆盖任何内置版本，只有流程主动调用这个动作时才会用到它。';
    } else if (template.is_builtin) {
      text = info.active
        ? '当前动作没有自定义模板，内置模板正在生效。'
        : '当前动作由自定义模板「' + info.winner.name + '」生效，内置模板不参与渲染。';
    } else if (info.active) {
      text = '本条自定义模板正在生效，渲染时使用它。' +
        (others > 0 ? '同动作其余 ' + others + ' 条自定义不会生效。' : '');
    } else {
      text = '同动作已有生效版本「' + info.winner.name + '」，本条不会生效；保存后本条会成为生效版本。';
    }
    return '<p class="prompt-effective' + (info.active ? ' prompt-effective--active' : '') +
      '" data-prompt-effective-state data-prompt-effective="' + state + '">' +
      esc(text) + '</p>';
  }

  function overriddenActions() {
    const set = new Set();
    templates.forEach(item => {
      if (item.is_builtin) return;
      if (!templates.some(other => other.is_builtin && other.action === item.action)) return;
      set.add(item.action);
    });
    return set;
  }

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    try {
      return date.toLocaleString('zh-CN', { hour12: false });
    } catch (error) {
      return String(value);
    }
  }

  /* ---------- 草稿状态 ---------- */

  function isDirty() {
    if (creating) {
      return draftAction.trim() !== '' || draftName.trim() !== '' || draftBody.trim() !== '';
    }
    const template = current();
    if (!template) return false;
    if (template.is_builtin) return false;
    return draftName !== String(template.name) || draftBody !== String(template.body);
  }

  function syncDraft() {
    const template = current();
    draftName = template ? String(template.name) : '';
    draftBody = template ? String(template.body) : '';
    if (!creating) draftAction = '';
  }

  function pickDefault() {
    const preferred = templates.find(item => item.is_builtin && item.action === 'ai.task.continue') ||
      templates.find(item => item.is_builtin) || templates[0];
    selectedId = preferred ? preferred.id : null;
  }

  /* ---------- 片段渲染 ---------- */

  function loadingHtml(text) {
    return '<div class="prompt-loading" role="status"><span class="prompt-spinner" aria-hidden="true"></span>' +
      esc(text) + '</div>';
  }

  function errorHtml(compact) {
    return '<div class="prompt-state prompt-state--error" role="alert">' +
      '<strong>暂时读不到提示词模板</strong><p>' + esc(errorText) + '</p>' +
      '<button type="button" class="ghost-btn" data-prompt-refresh>重新加载</button>' +
      (compact ? '' : '<p class="prompt-meta">模板保存在本机 SQLite 里，服务恢复后修改仍然在。</p>') +
      '</div>';
  }

  function badgeHtml(template) {
    if (template.is_builtin) {
      let html = '<span class="prompt-badge prompt-badge--builtin">内置</span>';
      if (overriddenActions().has(template.action)) {
        html += '<span class="prompt-badge prompt-badge--override" title="同动作的自定义模板会覆盖它">被覆盖</span>';
      }
      return html;
    }
    let html = '<span class="prompt-badge prompt-badge--custom">自定义</span>';
    if (isEffective(template)) {
      html += '<span class="prompt-badge prompt-badge--effective" data-prompt-effective-badge' +
        ' title="渲染时使用这一条">生效中</span>';
    }
    return html;
  }

  function rowHtml(template) {
    const active = !creating && template.id === selectedId;
    const customs = customsFor(template.action);
    const shadowed = !template.is_builtin && !isEffective(template) && customs.length > 1;
    const shadowNote = builtinFor(template.action)
      ? INACTIVE_HINT
      : '同动作共 ' + customs.length + ' 条自定义模板，渲染时只有最新保存的一条会被调用';
    return '<button type="button" class="prompt-row' + (active ? ' is-active' : '') +
      '" data-prompt-id="' + esc(template.id) + '" aria-pressed="' + (active ? 'true' : 'false') + '">' +
      '<span class="prompt-row-main"><span class="prompt-row-name">' + esc(template.name) + '</span>' +
      '<code class="prompt-row-action">' + esc(template.action) + '</code>' +
      (shadowed ? '<span class="prompt-row-note" data-prompt-inactive>' + esc(shadowNote) + '</span>' : '') +
      '</span>' +
      '<span class="prompt-row-side">' + badgeHtml(template) + '</span></button>';
  }

  function filteredTemplates() {
    const needle = filterText.trim().toLowerCase();
    if (!needle) return templates;
    return templates.filter(item =>
      (item.name + ' ' + item.action).toLowerCase().indexOf(needle) !== -1);
  }

  function listHtml() {
    if (!templates.length) {
      if (loading) return loadingHtml('正在读取提示词模板…');
      if (errorText) return errorHtml(true);
      return '<div class="prompt-state"><strong>还没有任何提示词模板</strong>' +
        '<p>启动后端服务后，墨屿会自动写入内置模板；也可以点「新建模板」自己加一条。</p></div>';
    }
    const items = filteredTemplates();
    if (!items.length) {
      return '<div class="prompt-state"><strong>没有匹配的模板</strong>' +
        '<p>换个关键词，或清空筛选后重试。</p></div>';
    }
    const groups = new Map();
    items.forEach(item => {
      const key = groupOf(item.action);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    return GROUP_ORDER.filter(key => groups.has(key)).map(key => {
      const rows = groups.get(key);
      return '<div class="prompt-group"><div class="prompt-group-head"><span>' +
        esc(GROUP_LABELS[key]) + '</span><small>' + rows.length + '</small></div>' +
        rows.map(rowHtml).join('') + '</div>';
    }).join('');
  }

  function fieldHtml(id, label, inputHtml, hint, hintAttr) {
    return '<div class="prompt-field"><label for="' + id + '">' + esc(label) + '</label>' +
      inputHtml +
      (hint ? '<p class="prompt-hint"' + (hintAttr || '') + '>' + esc(hint) + '</p>' : '') +
      '</div>';
  }

  function createActionHint(rawAction) {
    const action = String(rawAction || '').trim();
    const parts = [];
    const existing = action ? builtinFor(action) : null;
    if (existing) {
      parts.push('该动作已有内置模板「' + existing.name + '」：保存后你的版本会覆盖它，内置正文保持不变。');
    }
    const winner = action ? effectiveCustomFor(action) : null;
    if (winner) {
      parts.push('该动作已有 ' + customsFor(action).length + ' 条自定义模板，当前生效的是「' +
        winner.name + '」；保存后本条会成为新的生效版本。');
    }
    return parts.length ? parts.join('')
      : '动作名是程序调用模板时用的键，例如 ai.task.continue；填错的动作不会被任何流程使用。';
  }

  function createFormHtml() {
    const action = draftAction.trim();
    return '<div class="prompt-editor-inner">' +
      '<div class="prompt-editor-head"><div><h3>新建自定义模板</h3>' +
      '<p class="prompt-subtitle">保存后会立刻参与提示词渲染。</p></div>' +
      '<span class="prompt-badge prompt-badge--custom">未保存</span></div>' +
      fieldHtml('promptAction', '动作',
        '<input id="promptAction" data-prompt-action value="' + esc(draftAction) +
        '" placeholder="例如 ai.task.continue" autocomplete="off" spellcheck="false">',
        createActionHint(action), ' data-prompt-action-hint') +
      fieldHtml('promptName', '名称',
        '<input id="promptName" data-prompt-name value="' + esc(draftName) +
        '" maxlength="120" placeholder="给作者看的名字" autocomplete="off">') +
      fieldHtml('promptBody', '正文',
        '<textarea id="promptBody" data-prompt-body rows="16" spellcheck="false" ' +
        'placeholder="在这里写提示词正文；占位符用双花括号，例如 {{chapter_text}}">' +
        esc(draftBody) + '</textarea>') +
      '<div class="prompt-editor-foot"><span class="prompt-dirty" data-prompt-dirty aria-live="polite"></span>' +
      '<div class="prompt-actions">' +
      '<button type="button" class="ghost-btn" data-prompt-cancel-create>取消</button>' +
      '<button type="button" class="primary-btn" data-prompt-save>创建模板</button>' +
      '</div></div></div>';
  }

  function editorHtml() {
    if (creating) return createFormHtml();
    const template = current();
    if (!template) {
      return '<div class="prompt-state"><strong>从左侧选择一个模板</strong>' +
        '<p>内置模板可以直接查看与复制；自定义模板可以修改正文并保存，保存后立即参与渲染。</p></div>';
    }
    const builtin = Boolean(template.is_builtin);
    const action = String(template.action);
    const canReset = builtin || Boolean(builtinFor(action));
    const coveredBy = builtin ? customsFor(action) : [];
    const coverageWinner = builtin ? effectiveCustomFor(action) : null;
    const protection = builtin
      ? '<p class="prompt-protect" data-prompt-protect>内置模板只读：正文与名称不能直接修改、也不能删除。' +
        (coveredBy.length
          ? '当前已被自定义模板「' + esc(coverageWinner ? coverageWinner.name : coveredBy[0].name) +
            '」覆盖' +
            (coveredBy.length > 1
              ? '（同动作共 ' + coveredBy.length + ' 条自定义，只有最新保存的那条生效）'
              : '') + '，渲染时用它。'
          : '需要改动请「另存为自定义」，或「重置为默认」恢复出厂正文。') + '</p>'
      : '';
    return '<div class="prompt-editor-inner">' +
      '<div class="prompt-editor-head"><div><h3>' + esc(template.name) + '</h3>' +
      '<p class="prompt-subtitle"><code>' + esc(action) + '</code>' +
      (template.updated_at ? ' · 更新于 ' + esc(formatTime(template.updated_at)) : '') +
      ' · v' + esc(String(template.version == null ? 1 : template.version)) + '</p>' +
      effectiveStateHtml(template) + '</div>' +
      badgeHtml(template) + '</div>' + protection +
      fieldHtml('promptName', '名称',
        '<input id="promptName" data-prompt-name value="' + esc(draftName) + '" maxlength="120"' +
        (builtin ? ' readonly' : '') + ' autocomplete="off">') +
      fieldHtml('promptBody', '正文',
        '<textarea id="promptBody" data-prompt-body rows="18" spellcheck="false"' +
        (builtin ? ' readonly' : '') + '>' + esc(draftBody) + '</textarea>',
        builtin ? '只读：复制出来改，或另存为自定义后再改。' : '占位符用双花括号，例如 {{chapter_text}}；渲染时逐个替换。') +
      (actionError ? '<div class="prompt-state prompt-state--error" role="alert"><strong>' +
        esc(actionError) + '</strong></div>' : '') +
      '<div class="prompt-editor-foot"><span class="prompt-dirty" data-prompt-dirty aria-live="polite"></span>' +
      '<div class="prompt-actions">' +
      '<button type="button" class="primary-btn" data-prompt-save' +
      (builtin ? ' disabled title="内置模板不可直接修改，请另存为自定义"' : '') + '>保存</button>' +
      (builtin ? '<button type="button" class="ghost-btn" data-prompt-clone>另存为自定义</button>' : '') +
      '<button type="button" class="ghost-btn" data-prompt-reset' +
      (canReset ? '' : ' disabled title="该动作没有内置模板，无法重置"') + '>重置为默认</button>' +
      '<button type="button" class="ghost-btn danger-btn" data-prompt-delete' +
      (builtin ? ' disabled title="内置模板不可删除"' : '') + '>删除</button>' +
      '</div></div></div>';
  }

  function toolbarHtml() {
    const builtinCount = templates.filter(item => item.is_builtin).length;
    const customCount = templates.length - builtinCount;
    const overrides = overriddenActions().size;
    const parts = [templates.length + ' 个模板', builtinCount + ' 内置', customCount + ' 自定义'];
    if (overrides) parts.push(overrides + ' 个动作被自定义覆盖');
    return '<div class="prompt-toolbar">' +
      '<input class="prompt-search" data-prompt-filter value="' + esc(filterText) +
      '" placeholder="按名称或动作筛选…" aria-label="筛选提示词模板" autocomplete="off">' +
      '<span class="prompt-count" data-prompt-count>' + esc(parts.join(' · ')) + '</span>' +
      '<div class="prompt-toolbar-actions">' +
      '<button type="button" class="ghost-btn" data-prompt-refresh>↻ 刷新</button>' +
      '<button type="button" class="primary-btn" data-prompt-new>＋ 新建模板</button>' +
      '</div></div>';
  }

  function renderPanel() {
    const root = mountedRoot;
    if (!root || !root.isConnected) return;
    root.dataset.promptState = busy ? 'busy' : (loading && !templates.length ? 'loading' : (errorText ? 'error' : 'ready'));
    root.setAttribute('aria-busy', busy || (loading && !templates.length) ? 'true' : 'false');
    root.innerHTML = toolbarHtml() +
      '<div class="prompt-layout">' +
      '<aside class="prompt-list" aria-label="模板列表">' + listHtml() + '</aside>' +
      '<section class="prompt-editor" aria-label="模板编辑">' + editorHtml() + '</section>' +
      '</div>';
    updateControls();
  }

  /* 只改按钮与脏标记，不重建 DOM，避免打断输入。 */
  function updateControls() {
    const root = mountedRoot;
    if (!root) return;
    const dirty = isDirty();
    const dirtyNode = root.querySelector('[data-prompt-dirty]');
    if (dirtyNode) dirtyNode.textContent = dirty ? '有未保存的修改' : '';
    const save = root.querySelector('[data-prompt-save]');
    if (save) {
      if (creating) {
        save.disabled = busy || !draftAction.trim() || !draftName.trim() || !draftBody.trim();
      } else {
        const template = current();
        save.disabled = busy || !template || template.is_builtin || !dirty;
      }
    }
    const refresh = root.querySelector('[data-prompt-refresh]');
    if (refresh) refresh.disabled = busy;
  }

  /* ---------- 请求 ---------- */

  function friendlyError(error) {
    const message = String((error && error.message) || error || '').trim();
    const offline = typeof backendOnline !== 'undefined' && backendOnline === false;
    if (!message) return offline ? OFFLINE_TEXT : '提示词模板操作失败，请稍后重试。';
    if (/failed to fetch|networkerror|load failed|fetch failed|network request failed/i.test(message)) {
      return OFFLINE_TEXT;
    }
    return message;
  }

  function statusMessage(status, payload) {
    const detail = String((payload && payload.error) || '').trim();
    if (status === 409) return detail ? BUILTIN_HINT + '（' + detail + '）' : BUILTIN_HINT;
    if (status === 404) return detail || '模板不存在，可能已被删除，请刷新列表。';
    if (status === 403) return '请求被拒绝：请刷新页面后重试。';
    if (status === 400) return detail || '提交的内容不合法，请检查动作、名称与正文。';
    return detail || ('模板接口返回 ' + status);
  }

  async function request(method, path, body) {
    const options = { method: method, headers: { Accept: 'application/json' } };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const payload = await response.json().catch(() => null);
    return { response: response, payload: payload };
  }

  async function load(options) {
    const settings = options || {};
    const token = ++requestSequence;
    loading = true;
    errorText = '';
    actionError = '';
    renderPanel();
    try {
      const result = await request('GET', '/api/prompt-templates');
      if (token !== requestSequence) return;
      if (!result.response.ok) throw new Error(statusMessage(result.response.status, result.payload));
      const items = result.payload && Array.isArray(result.payload.templates)
        ? result.payload.templates.filter(isTemplate) : null;
      if (!items) throw new Error('模板接口没有返回列表');
      /* 列表接口按 rowid 升序返回，保留下标作为同一 updated_at 时的排序代理。 */
      templates = items.map((item, index) => Object.assign({}, item, { _order: index }))
        .sort(compareTemplates);
      loading = false;
      if (selectedId && !templates.some(item => item.id === selectedId)) selectedId = null;
      if (!selectedId && !creating) pickDefault();
      syncDraft();
      renderPanel();
      if (settings.manual) toast('提示词模板已刷新');
    } catch (error) {
      if (token !== requestSequence) return;
      loading = false;
      errorText = friendlyError(error);
      renderPanel();
    }
  }

  async function mutate(work, successText) {
    if (busy) return false;
    busy = true;
    actionError = '';
    renderPanel();
    try {
      const done = await work();
      busy = false;
      if (done) {
        if (successText) toast(successText);
      }
      /* work() 里的渲染发生在 busy=true 时，按钮是禁用态；这里同步一次，
       * 否则创建/保存/删除后工具栏的「刷新」会一直保持禁用。 */
      updateControls();
      return done;
    } catch (error) {
      busy = false;
      actionError = friendlyError(error);
      renderPanel();
      return false;
    }
  }

  async function saveCurrent() {
    if (creating) return createTemplate();
    const template = current();
    if (!template) return false;
    if (template.is_builtin) {
      actionError = BUILTIN_HINT;
      renderPanel();
      return false;
    }
    if (!isDirty()) return false;
    return mutate(async () => {
      const result = await request('PUT', '/api/prompt-templates/' + encodeURIComponent(template.id), {
        name: draftName.trim(),
        body: draftBody
      });
      if (!result.response.ok) {
        actionError = statusMessage(result.response.status, result.payload);
        return false;
      }
      await load();
      return true;
    }, '模板已保存');
  }

  async function createTemplate() {
    const action = draftAction.trim();
    const name = draftName.trim();
    if (!action || !name || !draftBody.trim()) {
      actionError = '动作、名称与正文都不能为空。';
      renderPanel();
      return false;
    }
    return mutate(async () => {
      const result = await request('POST', '/api/prompt-templates', {
        action: action,
        name: name,
        body: draftBody
      });
      if (!result.response.ok) {
        actionError = statusMessage(result.response.status, result.payload);
        return false;
      }
      creating = false;
      selectedId = result.payload && result.payload.id ? result.payload.id : null;
      await load();
      return true;
    }, '自定义模板已创建');
  }

  async function cloneToCustom() {
    const template = current();
    if (!template || !template.is_builtin) return false;
    const name = template.name + '（我的版本）';
    return mutate(async () => {
      const result = await request('POST', '/api/prompt-templates', {
        action: template.action,
        name: name,
        body: template.body
      });
      if (!result.response.ok) {
        actionError = statusMessage(result.response.status, result.payload);
        return false;
      }
      creating = false;
      selectedId = result.payload && result.payload.id ? result.payload.id : null;
      await load();
      return true;
    }, '已另存为自定义模板，可以开始修改');
  }

  async function resetCurrent() {
    const template = current();
    if (!template) return false;
    if (!confirm('把「' + template.name + '」的正文恢复为出厂内容？当前修改会丢失。')) return false;
    return mutate(async () => {
      const result = await request('POST',
        '/api/prompt-templates/' + encodeURIComponent(template.id) + '/reset', {});
      if (!result.response.ok) {
        actionError = statusMessage(result.response.status, result.payload);
        return false;
      }
      await load();
      return true;
    }, '已恢复出厂正文');
  }

  async function deleteCurrent() {
    const template = current();
    if (!template) return false;
    if (template.is_builtin) {
      actionError = BUILTIN_HINT;
      renderPanel();
      return false;
    }
    if (!confirm('删除自定义模板「' + template.name + '」？该动作会回退到内置模板。')) return false;
    return mutate(async () => {
      const result = await request('DELETE', '/api/prompt-templates/' + encodeURIComponent(template.id));
      if (!result.response.ok) {
        actionError = statusMessage(result.response.status, result.payload);
        return false;
      }
      selectedId = null;
      await load();
      return true;
    }, '自定义模板已删除');
  }

  /* ---------- 交互 ---------- */

  function select(id) {
    if (!creating && id === selectedId) return;
    if (isDirty() && !confirm('当前模板有未保存的修改，切换后将丢失。继续？')) return;
    creating = false;
    selectedId = id;
    actionError = '';
    syncDraft();
    renderPanel();
  }

  function startCreating() {
    if (isDirty() && !confirm('当前模板有未保存的修改，新建后将丢失。继续？')) return;
    creating = true;
    selectedId = null;
    actionError = '';
    draftAction = '';
    draftName = '';
    draftBody = '';
    renderPanel();
    const input = mountedRoot && mountedRoot.querySelector('[data-prompt-action]');
    if (input) input.focus();
  }

  function cancelCreating() {
    creating = false;
    actionError = '';
    if (!selectedId) pickDefault();
    syncDraft();
    renderPanel();
  }

  function handleClick(event) {
    const target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    const row = target.closest('[data-prompt-id]');
    if (row) {
      event.preventDefault();
      select(row.getAttribute('data-prompt-id'));
      return;
    }
    const map = [
      ['[data-prompt-refresh]', () => load({ manual: true })],
      ['[data-prompt-new]', startCreating],
      ['[data-prompt-cancel-create]', cancelCreating],
      ['[data-prompt-save]', saveCurrent],
      ['[data-prompt-clone]', cloneToCustom],
      ['[data-prompt-reset]', resetCurrent],
      ['[data-prompt-delete]', deleteCurrent]
    ];
    for (const [selector, handler] of map) {
      const button = target.closest(selector);
      if (!button) continue;
      event.preventDefault();
      if (button.disabled) return;
      handler();
      return;
    }
  }

  function handleInput(event) {
    const target = event.target;
    if (!target) return;
    if (target.matches('[data-prompt-filter]')) {
      filterText = target.value;
      const list = mountedRoot && mountedRoot.querySelector('.prompt-list');
      if (list) list.innerHTML = listHtml();
      return;
    }
    if (target.matches('[data-prompt-name]')) { draftName = target.value; updateControls(); return; }
    if (target.matches('[data-prompt-body]')) { draftBody = target.value; updateControls(); return; }
    if (target.matches('[data-prompt-action]')) {
      draftAction = target.value;
      const hint = mountedRoot && mountedRoot.querySelector('[data-prompt-action-hint]');
      if (hint) hint.textContent = createActionHint(draftAction);
      updateControls();
    }
  }

  function handleMainChange() {
    const main = document.querySelector('#main');
    if (!main) return;
    const root = main.querySelector('[data-prompt-root]');
    if (!root) { mountedRoot = null; return; }
    if (root === mountedRoot) return;
    mountedRoot = root;
    root.addEventListener('click', handleClick);
    root.addEventListener('input', handleInput);
    errorText = '';
    actionError = '';
    creating = false;
    filterText = '';
    selectedId = null;
    draftName = '';
    draftBody = '';
    draftAction = '';
    loading = true;
    renderPanel();
    load();
  }

  /* 视图骨架：render() 每次重建 #main，这里只做同步占位。 */
  views.prompts = () => '<div class="view-head"><div><h2>提示词工坊</h2>' +
    '<p>把墨屿调用模型时用的提示词变成可编辑模板：内置模板只读、可重置；改自己的版本不会动到出厂内容。</p></div>' +
    '<div class="view-head-actions"><button type="button" class="ghost-btn" data-prompt-refresh>↻ 刷新</button></div></div>' +
    '<div class="prompt-workshop" data-prompt-root aria-live="polite"></div>';

  function init() {
    const main = document.querySelector('#main');
    if (!main) return;
    if (!observer) {
      observer = new MutationObserver(handleMainChange);
      observer.observe(main, { childList: true, subtree: true });
    }
    handleMainChange();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
