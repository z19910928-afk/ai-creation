'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-agent-chat-ui-'));
const report = {passed: false, cases: [], screenshots: [], page_errors: [], paid_provider_calls: 0,
  artifacts: temporary,
  scope: '真实桌面 Chrome 与隔离作品数据库；聊天与写作任务响应由本机受控接口模拟，仅验证前端交互、请求身份与隔离，不证明模型或聊天后端可用。',
  branch_identity_test: 'Synthetic frontend branch identity; no branch-management UI claim.'};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const timestamp = () => new Date().toISOString();
const store = new Map(), requests = [], writingRequests = [], routeGates = [], rejectedWrites = new Set();
let app, browser, base, log = '', interruptCreation = false, interruptMessage = false;
let holdMessage = null, delayedReply = false, failAction = false;
let interruptAction = '', interruptStart = '';
const copy = value => JSON.parse(JSON.stringify(value));

function gateOnce(matches, outcome = 'success') {
  let entered, release, finished;
  const gate = {id: crypto.randomUUID(), matches, outcome, used: false,
    entered: new Promise(resolve => {entered = resolve;}),
    waiting: new Promise(resolve => {release = resolve;}),
    finished: new Promise(resolve => {finished = resolve;})};
  gate.enter = entered; gate.release = release; gate.finish = finished;
  routeGates.push(gate);
  return gate;
}
async function respond(route, observation, value, status = 200) {
  const snapshot = copy(value);
  const gate = routeGates.find(item => !item.used && item.matches(observation));
  const headers = {};
  if (gate) {
    gate.used = true; gate.observation = copy(observation); gate.snapshot = snapshot;
    gate.enter(gate.observation);
    await gate.waiting;
    headers['X-Moyu-UI-Gate'] = gate.id;
  }
  try {
    if (gate?.outcome === 'abort') await route.abort('failed');
    else await route.fulfill({status: gate?.outcome === '503' ? 503 : status,
      contentType: 'application/json', headers,
      body: JSON.stringify(gate?.outcome === '503' ? {error: '旧对话读取失败。'} : snapshot)});
  } finally {
    if (gate) gate.finish();
  }
}
async function bounded(promise, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Timed out: ' + label)), 12000);
    })]);
  } finally {
    clearTimeout(timer);
  }
}
async function releaseGate(page, gate) {
  gate.release();
  await bounded(gate.finished, 'held route finished');
  await page.waitForFunction(id => globalThis.__moyuConsumedRequests?.includes(id), gate.observation.request_id);
}

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}
async function until(fn, label) {
  for (let index = 0; index < 180; index += 1) {
    if (app && app.exitCode !== null) throw new Error('Server exited: ' + log.slice(-2000));
    const value = await fn();
    if (value) return value;
    await pause(75);
  }
  throw new Error('Timed out: ' + label);
}
async function api(route, method = 'GET', body, project = 'default-project') {
  const response = await fetch(base + route, {method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}
function workspace(project, branch) {
  const key = JSON.stringify([project, branch]);
  if (!store.has(key)) store.set(key, {sessions: [], messages: new Map(), runs: new Map(),
    creations: new Map(), sends: new Map(), writing: new Map(), writingFaults: new Map(),
    settings: {configured: true, mode: 'inherit', model: 'chat-ui-local'},
    summaries: new Map(), savedInspirations: new Map()});
  return store.get(key);
}
function bundle(data, session) {
  const runs = [...data.runs.values()].filter(row => row.session_id === session.id);
  return {session, messages: data.messages.get(session.id) || [],
    settings: data.settings, context_summary: data.summaries.get(session.id) || null,
    active_run: runs.find(row => ['queued', 'running'].includes(row.status)) || runs.at(-1) || null};
}
async function chatRoute(route) {
  const request = route.request(), headers = request.headers();
  const project = headers['x-moyu-project'], branch = headers['x-moyu-branch'];
  const data = workspace(project, branch), pathname = new URL(request.url()).pathname;
  const suffix = pathname.slice('/api/agent-chat'.length), method = request.method();
  const body = method === 'GET' ? null : request.postDataJSON();
  const observation = {project, branch, suffix, method, body, request_id: headers['x-moyu-ui-request']};
  requests.push(observation);
  const reply = value => respond(route, observation, value);
  if (suffix === '/settings') return reply({settings: data.settings});
  if (suffix === '/sessions' && method === 'GET') return reply({sessions: data.sessions, settings: data.settings});
  if (suffix === '/sessions' && method === 'POST') {
    let session = data.creations.get(body.request_id);
    if (!session) {
      session = {id: crypto.randomUUID(), project_id: project, branch_id: branch,
        title: '新对话', created_at: timestamp(), updated_at: timestamp()};
      data.sessions.unshift(session); data.messages.set(session.id, []);
      data.creations.set(body.request_id, session);
    }
    if (interruptCreation) {interruptCreation = false; return route.abort('failed');}
    return reply({session});
  }
  const segments = suffix.split('/').filter(Boolean);
  if (segments[0] === 'sessions') {
    const session = data.sessions.find(row => row.id === segments[1]);
    assert.ok(session, suffix);
    if (segments.length === 2 && method === 'GET') return reply(bundle(data, session));
    if (segments[2] === 'messages' && method === 'POST') {
      let result = data.sends.get(body.request_id);
      if (!result) {
        const messages = data.messages.get(session.id);
        messages.push({id: crypto.randomUUID(), role: 'user', content: body.content,
          references: body.references, created_at: timestamp()});
        data.summaries.set(session.id, {project_materials_included: true,
          chapter_included: !!body.include_chapter && !!body.chapter_id, chapter_title: '本轮章节正文',
          reference_count: body.references?.length || 0});
        session.title = body.content.slice(0, 24); session.updated_at = timestamp();
        const run = {id: crypto.randomUUID(), session_id: session.id,
          status: delayedReply ? 'running' : 'success'};
        data.runs.set(run.id, run);
        if (!delayedReply) messages.push({id: crypto.randomUUID(), role: 'assistant',
          content: '已结合当前作品整理建议：' + body.content, created_at: timestamp()});
        result = {session, run}; data.sends.set(body.request_id, result);
      }
      observation.session_id = session.id; observation.run_id = result.run.id;
      if (holdMessage) {const wait = holdMessage; holdMessage = null; await wait;}
      if (interruptMessage) {interruptMessage = false; return route.abort('failed');}
      return reply(bundle(data, session));
    }
  }
  if (segments[0] === 'runs') {
    const run = data.runs.get(segments[1]);
    assert.ok(run, suffix);
    const session = data.sessions.find(row => row.id === run.session_id);
    if (segments[2] === 'cancel') {
      run.status = 'canceled';
      data.messages.get(session.id).push({id: crypto.randomUUID(), role: 'assistant',
        content: '', status: 'canceled', created_at: timestamp()});
    }
    return reply(bundle(data, session));
  }
  if (segments[0] === 'actions') {
    const message = [...data.messages.values()].flat().find(row =>
      row.actions?.some(action => action.id === segments[1]));
    assert.ok(message, suffix);
    const action = message.actions.find(row => row.id === segments[1]);
    if (failAction) return route.fulfill({status: 503, contentType: 'application/json',
      body: JSON.stringify({error: '本次操作尚未保存，请重试。'})});
    action.status = segments[2] === 'dismiss' ? 'dismissed' : 'confirmed';
    let writingSession;
    if (segments[2] === 'confirm' && ['continue', 'polish', 'inspect', 'plan'].includes(action.kind)) {
      writingSession = writingTask(data, action);
      if (interruptAction === action.id) {interruptAction = ''; return route.abort('failed');}
    } else if (segments[2] === 'confirm' && action.kind === 'save_inspiration') {
      if (!data.savedInspirations.has(action.id)) data.savedInspirations.set(action.id, {
        id: 'ui_inspiration_' + action.id, title: action.title, content: action.content,
      });
      action.result = {record_id: data.savedInspirations.get(action.id).id, view: 'world'};
    }
    return reply({action, writing_session: writingSession});
  }
  throw new Error('Unexpected simulated chat route: ' + suffix);
}
function writingTask(data, action, patch = {}) {
  assert.ok(action.chapter_id, '模拟写作建议必须绑定真实来源章节');
  assert.equal(typeof action.instruction, 'string', '模拟写作建议必须包含独立指令');
  const id = action.result?.writing_session_id || 'ui_task_' + action.id;
  action.result = {...action.result, writing_session_id: id, chapter_id: action.chapter_id};
  if (!data.writing.has(id)) data.writing.set(id, {
    id, chapter_id: action.chapter_id, mode: action.kind, query: action.instruction.trim(),
    status: 'awaiting_authorization', calls_used: 0, max_calls: action.kind === 'plan' ? 4 :
      action.kind === 'inspect' ? 2 : 3, checkpoint_id: 'checkpoint_' + crypto.randomUUID(),
    stage: 'preflight', steps: taskSteps(action.kind), current_job: null, result_refs: {}, context_evidence: {},
    source_stale: false, restored_read_only: false, error: '', created_at: timestamp(), updated_at: timestamp(),
  });
  const row = data.writing.get(id); Object.assign(row, patch); return row;
}
function taskSteps(mode, states = {}) {
  const labels = mode === 'plan' ?
    {overview: '故事总纲', volumes: '分卷规划', chapters: '章节规划', scenes: '场景规划'} :
    {...(mode === 'continue' ? {continue: '生成正文候选'} : mode === 'polish' ? {polish: '整章修订候选'} : {}),
      chapter_analysis: '整理章后记忆', semantic_review: '故事审稿'};
  return Object.entries(labels).map(([task, label]) => {
    const value = states[task] || 'pending';
    return {task, label, status: typeof value === 'string' ? value : value.status,
      ...(typeof value === 'object' ? value : {})};
  });
}
async function writingRoute(route) {
  const request = route.request(), headers = request.headers(), url = new URL(request.url());
  const project = headers['x-moyu-project'] || 'default-project', branch = headers['x-moyu-branch'] || 'main';
  const data = workspace(project, branch), method = request.method();
  const body = method === 'GET' ? null : request.postDataJSON();
  const id = url.searchParams.get('session_id') ||
    (url.pathname.endsWith('/resume') ? url.pathname.split('/').at(-2) : null);
  const observation = {project, branch, method, id, body, pathname: url.pathname, request_id: headers['x-moyu-ui-request'],
    exact: method === 'GET' && url.searchParams.has('session_id')};
  writingRequests.push(observation);
  if (method === 'GET' && url.pathname === '/api/writing-agent') {
    if (!observation.exact) return respond(route, observation, {sessions: [...data.writing.values()].reverse().slice(0, 50)});
    assert.deepEqual(url.searchParams.getAll('session_id'), [id]);
    const fault = data.writingFaults.get(id), row = data.writing.get(id);
    if (fault?.status) return respond(route, observation, {error: '任务读取暂时不可用。'}, fault.status);
    if (!row) return respond(route, observation, {error: '当前作品中没有这条写作任务。'}, 404);
    return respond(route, observation, {sessions: [{...row, ...fault?.patch}]});
  }
  if (method === 'POST' && url.pathname === '/api/writing-agent/' + id + '/resume') {
    if (rejectedWrites.delete(id)) {
      observation.not_delivered = true;
      return route.abort('failed');
    }
    const row = data.writing.get(id);
    assert.ok(row, '操作必须命中当前作品及分支中的原任务');
    assert.ok(['authorize', 'confirm', 'apply'].includes(body.action), '读取与核对不得自动推进任务');
    assert.equal(body.checkpoint_id, row.checkpoint_id, '操作必须使用最近一次精确读取的检查点');
    if (body.action === 'authorize') {
      assert.equal(row.status, 'awaiting_authorization');
      Object.assign(row, {status: 'awaiting_job', calls_used: 1,
        checkpoint_id: 'started_' + crypto.randomUUID(), updated_at: timestamp()});
      row.steps[0] = {...row.steps[0], status: 'running',
        job_id: 'job_' + id, ai_run_id: 'run_' + id};
      if (interruptStart === id) {interruptStart = ''; return route.abort('failed');}
    } else if (body.action === 'confirm') {
      assert.equal(row.mode, 'plan'); assert.equal(row.status, 'awaiting_plan_confirmation');
      const step = row.steps.find(item => item.task === row.stage);
      assert.ok(step); step.status = 'confirmed';
      const next = row.steps[row.steps.indexOf(step) + 1];
      if (next) {
        next.status = 'running';
        Object.assign(row, {stage: next.task, status: 'awaiting_job', calls_used: row.calls_used + 1});
      } else row.status = 'awaiting_plan_application';
      row.planning_workflow.attempt = null;
      row.checkpoint_id = 'confirmed_' + crypto.randomUUID(); row.updated_at = timestamp();
    } else {
      assert.equal(row.mode, 'plan'); assert.equal(row.status, 'awaiting_plan_application');
      Object.assign(row, {status: 'review_ready', checkpoint_id: 'applied_' + crypto.randomUUID(), updated_at: timestamp()});
      row.planning_workflow.applied = true;
    }
    return respond(route, observation, {session: row});
  }
  throw new Error('Unexpected simulated writing route: ' + method + ' ' + url.pathname);
}
function fixtureSession(data, chapterId, name, options = {}) {
  const session = {id: crypto.randomUUID(), project_id: options.project || 'default-project',
    branch_id: options.branch || 'main', title: name, created_at: timestamp(), updated_at: timestamp(),
    restored_read_only: !!options.readOnly};
  const action = {id: crypto.randomUUID(), kind: options.mode || 'continue', label: name,
    instruction: '按本次建议处理门外冲突：' + name, chapter_id: chapterId,
    status: options.proposed ? 'proposed' : 'confirmed', result: {}};
  data.sessions.unshift(session);
  data.messages.set(session.id, [{id: crypto.randomUUID(), role: 'assistant', content: name,
    created_at: timestamp(), actions: [action]}]);
  const row = options.proposed ? null : writingTask(data, action, options.task || {});
  return {session, action, row};
}
async function selectChat(page, fixture) {
  await page.locator('[data-chat-history]').click();
  await page.locator('[data-chat-session="' + fixture.session.id + '"]').waitFor();
  await page.locator('[data-chat-session="' + fixture.session.id + '"]').click();
  await page.locator('[data-chat-action="' + fixture.action.id + '"]').waitFor();
  return page.locator('[data-chat-action="' + fixture.action.id + '"]');
}
const exactReads = id => writingRequests.filter(row => row.exact && row.id === id);
const taskPosts = (id, action) => writingRequests.filter(row => row.method === 'POST' && row.id === id &&
  (!action || row.body.action === action));
const starts = id => taskPosts(id, 'authorize');
async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn(); report.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, passed: true}));
}
async function sent(page, text) {
  await page.locator('#agentChatInput').fill(text);
  await page.locator('#agentChatSend').click();
  await until(() => page.locator('#agentChatInput').inputValue().then(value => value === ''), 'message saved');
  await until(() => page.locator('#agentChatSaveState').textContent().then(value =>
    value.includes('已保存')), 'saved label');
}
async function main() {
  const port = await freePort(); base = `http://127.0.0.1:${port}`;
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {cwd: root,
    env: {...process.env, MOYU_DB: path.join(temporary, 'story.sqlite3'), MOYU_HOST: '127.0.0.1',
      MOYU_PORT: String(port), MOYU_AI_BASE_URL: 'http://127.0.0.1:1/v1', MOYU_AI_API_KEY: '',
      MOYU_SECRET_KEY: '', MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials')},
    stdio: ['ignore', 'ignore', 'pipe']});
  app.stderr.on('data', chunk => {log += chunk.toString();});
  await until(async () => {try {return (await fetch(base + '/api/health')).ok;} catch {return false;}}, 'startup');
  const initial = await api('/api/state');
  const firstId = initial.chapters[0].id;
  const firstTitle = initial.chapters[0].title, secondId = 'chat_inline_other_chapter', secondTitle = '第二章 渡口的灯';
  initial.chapters[0].body = '林岚站在门外。😀她把铜钥匙留在门槛上。\n\n守卫没有回答。';
  initial.chapters.push({...initial.chapters[0], id: secondId, title: secondTitle, body: '船工正在渡口收起灯笼。'});
  await api('/api/state', 'PUT', initial);
  const created = await api('/api/projects', 'POST', {title: '常驻聊天隔离作品'});
  const otherId = created.project?.id || created.state?.project?.id;
  const otherState = await api('/api/state', 'GET', undefined, otherId);
  const otherBranch = otherState.branch_id || otherState.project.branch_id;
  const baseline = await api('/api/state');
  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  await page.addInitScript(() => {
    const originalFetch = globalThis.fetch;
    globalThis.__moyuConsumedRequests = [];
    let sequence = 0;
    globalThis.fetch = async function(...args) {
      const url = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.href);
      const token = String(++sequence), options = {...args[1],
        headers: new Headers(args[1]?.headers || (typeof args[0] === 'object' ? args[0].headers : undefined))};
      if (url.pathname.startsWith('/api/agent-chat') || url.pathname.startsWith('/api/writing-agent')) {
        options.headers.set('X-Moyu-UI-Request', token); args[1] = options;
      }
      if (globalThis.__moyuInlineOrderId && url.pathname.startsWith('/api/writing-agent') &&
          (url.searchParams.get('session_id') === globalThis.__moyuInlineOrderId ||
           url.pathname === '/api/writing-agent/' + globalThis.__moyuInlineOrderId + '/resume')) {
        globalThis.__moyuInlineOrder.push(args[1]?.method === 'POST' ? 'post' : 'get');
      }
      let response;
      try {
        response = await originalFetch.apply(this, args);
      } catch (error) {
        setTimeout(() => globalThis.__moyuConsumedRequests.push(token), 0);
        throw error;
      }
      const originalJSON = response.json.bind(response);
      response.json = async function(...jsonArgs) {
        const result = await originalJSON(...jsonArgs);
        // Mark the next task so the application's JSON continuation settles first.
        setTimeout(() => globalThis.__moyuConsumedRequests.push(token), 0);
        return result;
      };
      return response;
    };
  });
  await page.route('**/api/agent-chat/**', chatRoute);
  await page.route('**/api/writing-agent**', writingRoute);
  await page.goto(base); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
  await page.evaluate(() => authorSwitchProject('default-project'));

  await test('灵感起问只填写草稿，保留作者输入且不会自动发送', async () => {
    const before = requests.filter(row => row.method === 'POST').length;
    for (const id of ['idea', 'character', 'plot']) {
      await page.locator('#agentChatInput').fill('保留我自己的构思。');
      await page.locator('[data-chat-starter="' + id + '"]').click();
      const text = await page.locator('#agentChatInput').inputValue();
      assert.ok(text.startsWith('保留我自己的构思。\n\n'));
      assert.ok(text.length > 30);
    }
    assert.equal(requests.filter(row => row.method === 'POST').length, before);
    assert.equal(workspace('default-project', 'main').sessions.length, 0);
    assert.match(await page.locator('.agent-chat-materials').textContent(), /参考范围.*作品资料/);
    await page.locator('[data-chat-new]').click();
  });

  await test('总管未连接时给出设置入口，保留草稿并阻止发送', async () => {
    const data = workspace('default-project', 'main');
    data.settings = {...data.settings, configured: false};
    await page.evaluate(() => MoyuAgentChat.refresh());
    await page.locator('.agent-chat-connection').waitFor();
    await page.locator('#agentChatInput').fill('还没有发出的故事念头。');
    assert.equal(await page.locator('#agentChatSend').isDisabled(), true);
    const before = requests.filter(row => row.method === 'POST').length;
    await page.locator('#agentChatInput').press('Control+Enter');
    assert.equal(requests.filter(row => row.method === 'POST').length, before);
    await page.locator('[data-chat-configure]').click();
    assert.equal(await page.evaluate(() => view), 'settings');
    assert.equal(await page.locator('#agentChatInput').inputValue(), '还没有发出的故事念头。');
    data.settings = {...data.settings, configured: true};
    await page.evaluate(() => MoyuAgentChat.refresh());
    await page.locator('.agent-chat-connection').waitFor({state: 'hidden'});
    assert.equal(await page.locator('#agentChatSend').isEnabled(), true);
    await page.locator('[data-view="dashboard"]').click();
    await page.locator('[data-chat-new]').click();
  });

  await test('切换功能页保留同一个聊天面板和未发送草稿', async () => {
    await page.locator('#agentChatInput').fill('讨论林岚为什么没有进门。');
    await page.evaluate(() => {globalThis.__chatNode = document.querySelector('#agentChatInput');});
    for (const view of ['editor', 'blueprint', 'world', 'memory', 'plot', 'story', 'dashboard']) {
      await page.locator(`[data-view="${view}"]`).click();
      assert.equal(await page.evaluate(() => globalThis.__chatNode === document.querySelector('#agentChatInput')), true);
      assert.equal(await page.locator('#agentChatInput').inputValue(), '讨论林岚为什么没有进门。');
    }
  });

  await test('1440、1280、1920电脑布局无横向溢出，正文结果按需展开', async () => {
    for (const size of [{width: 1280, height: 900}, {width: 1440, height: 1000}, {width: 1920, height: 1080}]) {
      await page.setViewportSize(size);
      for (const view of ['dashboard', 'blueprint', 'world', 'memory', 'settings', 'editor']) {
        await page.locator(`[data-view="${view}"]`).click();
        if (view === 'settings') await page.locator('#settingsHost .settings-form').waitFor();
        const dimensions = await page.evaluate(() => ({
          width: innerWidth, scroll: document.documentElement.scrollWidth,
          height: innerHeight, pageHeight: document.documentElement.scrollHeight,
          chat: document.querySelector('#agentChat').getBoundingClientRect().toJSON(),
        }));
        assert.ok(dimensions.scroll <= dimensions.width, JSON.stringify({view, ...dimensions}));
        assert.ok(dimensions.chat.right <= dimensions.width && dimensions.chat.height > 500,
          JSON.stringify({view, ...dimensions}));
        if (view === 'editor') {
          assert.equal(await page.locator('#workbenchResults').isVisible(), false);
          const layout = await page.evaluate(() => {
            const library = document.querySelector('.workbench-library').getBoundingClientRect();
            const editor = document.querySelector('.workbench-manuscript').getBoundingClientRect();
            const chat = document.querySelector('#agentChat').getBoundingClientRect();
            return {aligned: library.right <= editor.left && editor.right <= chat.left,
              editor: editor.width, bottom: editor.bottom, height: innerHeight};
          });
          assert.ok(layout.aligned && layout.editor >= 550 && layout.bottom <= layout.height, JSON.stringify(layout));
          for (const id of ['workbenchAgentSection', 'workbenchCandidateSection', 'workbenchContextSection']) {
            await page.locator(`[data-chat-result="${id}"]`).click();
            assert.equal(await page.locator('#' + id).isVisible(), true);
            assert.equal(await page.locator('.workbench-rail-section:visible').count(), 1);
            const summary = page.locator('details.chapter-plan > summary');
            await summary.scrollIntoViewIfNeeded();
            await summary.click();
            const resultsLayout = await page.evaluate(() => {
              const editor = document.querySelector('.workbench-manuscript').getBoundingClientRect();
              const results = document.querySelector('#workbenchResults').getBoundingClientRect();
              const plan = document.querySelector('details.chapter-plan > summary').getBoundingClientRect();
              return {editorBottom: editor.bottom, resultTop: results.top, planBottom: plan.bottom};
            });
            assert.ok(resultsLayout.editorBottom <= resultsLayout.resultTop &&
              resultsLayout.planBottom <= resultsLayout.resultTop, JSON.stringify(resultsLayout));
            if (id === 'workbenchCandidateSection') {
              const file = path.join(temporary, `agent-chat-results-${size.width}x${size.height}.png`);
              await page.screenshot({path: file}); report.screenshots.push(file);
            }
          }
          await page.locator('[data-workbench-results-close]').click();
          await page.locator('[data-workbench-focus]').click();
          assert.equal(await page.locator('#agentChat').isVisible(), false);
          await page.locator('[data-workbench-focus]').click();
          const file = path.join(temporary, `agent-chat-${size.width}x${size.height}.png`);
          await page.screenshot({path: file}); report.screenshots.push(file);
        }
      }
    }
    await page.setViewportSize({width: 1440, height: 1000});
  });

  await test('切换作品与前端分支分别恢复各自草稿，不把旧稿写入新作品', async () => {
    await page.locator('#agentChatInput').fill('主作品的未发送草稿。');
    await page.evaluate(id => authorSwitchProject(id), otherId);
    assert.equal(await page.locator('#agentChatInput').inputValue(), '');
    await page.locator('#agentChatInput').fill('另一作品的未发送草稿。');
    await page.evaluate(() => authorSwitchProject('default-project'));
    assert.equal(await page.locator('#agentChatInput').inputValue(), '主作品的未发送草稿。');
    await page.evaluate(() => {state.branch_id = 'chat-ui-synthetic'; render();});
    assert.equal(await page.locator('#agentChatInput').inputValue(), '');
    await page.locator('#agentChatInput').fill('另一分支的未发送草稿。');
    await page.evaluate(() => {state.branch_id = 'main'; render();});
    assert.equal(await page.locator('#agentChatInput').inputValue(), '主作品的未发送草稿。');
    await page.evaluate(id => authorSwitchProject(id), otherId);
    assert.equal(await page.locator('#agentChatInput').inputValue(), '另一作品的未发送草稿。');
    await page.evaluate(() => authorSwitchProject('default-project'));
  });

  await test('会话创建回应丢失时复用原身份，消息保存丢失后重试不重复发送', async () => {
    interruptCreation = true;
    await page.locator('#agentChatInput').fill('第一次持续讨论。');
    await page.locator('#agentChatSend').click();
    await until(() => page.locator('#agentChatNotice').textContent(), 'creation interrupted');
    assert.equal(await page.locator('#agentChatInput').inputValue(), '第一次持续讨论。');
    await page.locator('#agentChatSend').click();
    await until(() => page.locator('#agentChatInput').inputValue().then(value => value === ''), 'creation retried');
    const creations = requests.filter(row => row.suffix === '/sessions' && row.method === 'POST');
    assert.equal(creations.length, 2); assert.equal(creations[0].body.request_id, creations[1].body.request_id);
    assert.equal(workspace('default-project', 'main').sessions.length, 1);
    interruptMessage = true;
    await page.locator('#agentChatInput').fill('保存回应丢失时重试同一消息。');
    await page.locator('#agentChatSend').click();
    await until(() => page.locator('#agentChatNotice').textContent(), 'message interrupted');
    assert.equal(await page.locator('#agentChatInput').inputValue(), '保存回应丢失时重试同一消息。');
    await page.locator('#agentChatSend').click();
    await until(() => page.locator('#agentChatInput').inputValue().then(value => value === ''), 'message retried');
    const posts = requests.filter(row => row.suffix.endsWith('/messages'));
    assert.equal(posts.at(-1).body.request_id, posts.at(-2).body.request_id);
    assert.equal([...workspace('default-project', 'main').messages.values()].flat().filter(row =>
      row.content === '保存回应丢失时重试同一消息。').length, 1);
  });

  await test('新对话未发送草稿刷新后恢复，旧会话历史仍可打开', async () => {
    await page.locator('[data-chat-new]').click();
    await page.locator('#agentChatInput').fill('新话题的未发送草稿。');
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    await pause(200);
    assert.equal(await page.locator('#agentChatInput').inputValue(), '新话题的未发送草稿。');
    assert.equal(await page.locator('[data-chat-message]').count(), 0);
    await page.locator('[data-chat-history]').click();
    await page.locator('[data-chat-session]').first().click();
    await until(() => page.locator('[data-chat-message]').count().then(value => value >= 4), 'history restored');
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    await until(() => page.locator('[data-chat-message]').count().then(value => value >= 4), 'last saved conversation');
    await page.locator('[data-view="editor"]').click();
    await pause(1200);
    assert.equal(await page.evaluate(() => aiDraft?.task || null), null);
    assert.equal(await page.locator('#aiOutput').inputValue(), '');
  });

  await test('正文选区附带已保存版本与Unicode范围，可预览和移除', async () => {
    await page.locator('[data-view="editor"]').click();
    const quote = '😀她把铜钥匙留在门槛上。';
    await page.locator('#chapterBody').evaluate((input, text) => {
      const start = input.value.indexOf(text);
      input.focus(); input.setSelectionRange(start, start + text.length);
      input.dispatchEvent(new Event('select'));
    }, quote);
    await page.locator('[data-agent-chat-quote]').click();
    await page.locator('[data-chat-reference]').waitFor();
    await page.locator('[data-chat-reference]').click();
    assert.equal(await page.locator('[data-chat-reference-preview] blockquote').textContent(), quote);
    await page.locator('[data-chat-reference-preview] [data-close]').click();
    await sent(page, '请讨论这处动作。');
    const payload = requests.filter(row => row.suffix.endsWith('/messages')).at(-1).body;
    assert.equal(payload.chapter_id, firstId); assert.equal(payload.include_chapter, true);
    const reference = payload.references[0], body = baseline.chapters[0].body;
    assert.equal(reference.project_id, 'default-project'); assert.equal(reference.branch_id, 'main');
    assert.equal(reference.chapter_id, firstId); assert.equal(reference.range.quote, quote);
    assert.ok(reference.source_revision_id); assert.equal(reference.source_body_hash,
      crypto.createHash('sha256').update(body).digest('hex'));
    assert.equal(Array.from(body).slice(reference.range.start, reference.range.end).join(''), quote);
  });

  await test('请求延迟时新输入和切换作品的草稿不被旧回应清除', async () => {
    let release;
    holdMessage = new Promise(resolve => {release = resolve;});
    await page.locator('#agentChatInput').fill('已经提交的旧消息。');
    await page.locator('#agentChatSend').click();
    await until(() => requests.some(row => row.body?.content === '已经提交的旧消息。'), 'held request');
    await page.locator('#agentChatInput').fill('发送期间输入的新草稿。');
    await page.evaluate(id => authorSwitchProject(id), otherId);
    await page.locator('#agentChatInput').fill('另一作品仍在编辑的草稿。');
    release(); await pause(250);
    assert.equal(await page.locator('#agentChatInput').inputValue(), '另一作品仍在编辑的草稿。');
    assert.equal(await page.locator('[data-chat-message]').count(), 0);
    await page.evaluate(() => authorSwitchProject('default-project'));
    await until(() => page.locator('[data-chat-message]').count().then(value => value >= 8), 'first conversation resumed');
    assert.equal(await page.locator('#agentChatInput').inputValue(), '发送期间输入的新草稿。');
  });

  await test('停止与动作确认通过真实请求入口，失败时不跳转或改稿', async () => {
    await page.locator('[data-view="editor"]').click();
    delayedReply = true;
    await sent(page, '这次先停止回复。');
    await page.locator('#agentChatSend[data-stopping="true"]').waitFor();
    await page.locator('#agentChatSend').click();
    await until(() => page.locator('#agentChatSend').getAttribute('data-stopping').then(value => value === 'false'), 'canceled');
    assert.ok(requests.some(row => row.suffix.endsWith('/cancel')));
    const data = workspace('default-project', 'main'), messages = [...data.messages.values()].at(0);
    messages.push({id: crypto.randomUUID(), role: 'assistant', content: '可以打开规划继续处理。',
      created_at: timestamp(), actions: [{id: 'ui-open-blueprint', kind: 'open', label: '打开创作规划',
        status: 'proposed', result: {view: 'blueprint'}}]});
    await pause(3700);
    await page.locator('[data-chat-confirm]').waitFor();
    failAction = true; await page.locator('[data-chat-confirm]').click();
    await until(() => page.locator('[data-chat-action-error]').textContent(), 'action failure');
    assert.equal(await page.evaluate(() => view), 'editor');
    failAction = false; await page.locator('[data-chat-confirm]').click();
    await page.waitForFunction(() => view === 'blueprint');
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
  });
  await test('未知回复结束等待，确认中步骤可恢复且不能误放弃', async () => {
    const data = workspace('default-project', 'main'), messages = [...data.messages.values()].at(0);
    [...data.runs.values()].at(-1).status = 'unknown';
    messages.push({id: crypto.randomUUID(), role: 'assistant', content: '', status: 'unknown',
      created_at: timestamp(), actions: [{id: 'ui-recover-confirmation', kind: 'continue',
        label: '恢复已确认的续写步骤', instruction: '继续描写门外的冲突。',
        chapter_id: firstId, status: 'confirming', result: {}}]});
    const before = requests.filter(row => row.suffix.endsWith('/confirm')).length;
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    const row = page.locator('[data-chat-action="ui-recover-confirmation"]');
    await row.getByText('恢复此步骤', {exact: true}).waitFor();
    assert.equal(await row.locator('[data-chat-dismiss]').count(), 0);
    assert.equal(await page.locator('#agentChatSend').getAttribute('data-stopping'), 'false');
    assert.match(await page.locator('#agentChatSaveState').textContent(), /尚未确认/);
    assert.equal(requests.filter(item => item.suffix.endsWith('/confirm')).length, before);
    await row.locator('[data-chat-confirm]').click();
    await row.getByText(/任务已建立|任务进度与成果保存在本机/).first().waitFor();
    assert.equal(requests.filter(item => item.suffix === '/actions/ui-recover-confirmation/confirm').length, 1);
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
  });

  await test('原卡精确绑定任务，迟到聊天和任务读取不回滚确认或手动启动结果', async () => {
    delayedReply = false;
    const data = workspace('default-project', 'main'), observations = [];
    for (const outcome of ['success', '503', 'abort']) {
      await page.locator('[data-view="memory"]').click();
      const fixture = fixtureSession(data, firstId, '原卡继续门外冲突 ' + outcome, {proposed: true});
      const card = await selectChat(page, fixture);
      const oldBundle = gateOnce(row => row.method === 'GET' && row.suffix === '/sessions/' + fixture.session.id, outcome);
      let oldTask;
      try {
        await bounded(oldBundle.entered, 'chat bundle held before confirmation: ' + outcome);
        const draft = '旧读取结束后仍保留的问题 ' + outcome;
        await page.locator('#agentChatInput').fill(draft);
        await card.locator('[data-chat-confirm]').click();
        await card.locator('[data-chat-start]:not([disabled])').waitFor();
        const id = fixture.action.result.writing_session_id, row = data.writing.get(id);
        assert.ok(id);
        assert.equal(await page.evaluate(() => view), 'memory');
        assert.equal(starts(id).length, 0, '确认建议不得自动启动任务');
        assert.equal(await card.locator('[data-chat-writing-session]').getAttribute('data-chat-writing-session'), id);
        assert.equal(await card.locator('[data-chat-task-source]').textContent(), '来源章 · ' + firstTitle);
        assert.equal(await card.locator('.agent-chat-task-budget').textContent(), '模型调用 0 / 上限 3 次');
        await releaseGate(page, oldBundle);
        assert.equal(await card.locator('[data-chat-confirm]').count(), 0);
        assert.equal(await card.locator('[data-chat-task-status]').getAttribute('data-chat-task-status'), 'awaiting_authorization');
        assert.equal(await card.locator('[data-chat-start]').isEnabled(), true);
        assert.equal(await page.locator('#agentChatNotice').textContent(), '');
        assert.equal(await page.locator('#agentChatSaveState').textContent(), '对话已保存在本机');
        assert.equal(await page.locator('#agentChatInput').inputValue(), draft);
        const observation = {outcome, task_id: id,
          confirmation_snapshot: oldBundle.snapshot.messages[0].actions[0].status};
        if (outcome === 'success') {
          oldTask = gateOnce(item => item.exact && item.id === id);
          await card.locator('[data-chat-task-refresh]').click();
          await bounded(oldTask.entered, 'old task progress held');
          row.checkpoint_id = 'fresh_' + crypto.randomUUID();
          const checkpoint = row.checkpoint_id;
          await card.locator('[data-chat-start]').evaluate(button => {button.click(); button.click();});
          await card.locator('[data-chat-task-status="awaiting_job"]').waitFor();
          assert.equal(starts(id).length, 1, '双击只允许一次明确启动');
          assert.equal(starts(id)[0].body.checkpoint_id, checkpoint);
          await releaseGate(page, oldTask);
          assert.equal(await card.locator('[data-chat-task-status]').getAttribute('data-chat-task-status'), 'awaiting_job');
          assert.equal(await card.locator('.agent-chat-task-budget').textContent(), '模型调用 1 / 上限 3 次');
          assert.equal(await card.locator('[data-chat-start]').count(), 0);
          assert.equal(await page.evaluate(() => view), 'memory');

          for (let index = 0; index < 51; index += 1) writingTask(data, {
            id: 'distractor_' + index + '_' + crypto.randomUUID(), chapter_id: index % 2 ? firstId : secondId,
            kind: index % 2 ? 'inspect' : 'continue', instruction: '其他任务 ' + index, result: {},
          });
          assert.ok(![...data.writing.keys()].reverse().slice(0, 50).includes(id));
          const reads = exactReads(id).length;
          await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
          await card.locator('[data-chat-writing-session="' + id + '"][data-chat-task-status="awaiting_job"]').waitFor();
          assert.ok(exactReads(id).length > reads, '刷新后按准确编号读取，不能使用最近五十条猜测');
          assert.equal(starts(id).length, 1, '刷新不能自动重新启动');
          observation.stale_task_status = oldTask.snapshot.sessions[0].status;
        }
        assert.ok(exactReads(id).every(item => item.project === 'default-project' && item.branch === 'main'));
        observations.push({...observation, start_requests: starts(id).length, exact_reads: exactReads(id).length});
      } finally {
        oldBundle.release(); oldTask?.release();
      }
    }
    report.inline_task_races = observations;
  });

  await test('确认和启动回应丢失后读取原绑定，不自动重发或清除作者草稿', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, firstId, '回应丢失的原任务', {proposed: true});
    const card = await selectChat(page, fixture), draft = '回应丢失期间仍保留的问题。';
    await page.locator('#agentChatInput').fill(draft);
    interruptAction = fixture.action.id;
    await card.locator('[data-chat-confirm]').click();
    await card.locator('[data-chat-start]:not([disabled])').waitFor();
    const id = fixture.action.result.writing_session_id;
    assert.equal(requests.filter(row => row.suffix === '/actions/' + fixture.action.id + '/confirm').length, 1);
    assert.equal([...data.writing.values()].filter(row => row.id === id).length, 1);
    assert.equal(starts(id).length, 0);
    assert.equal(await page.locator('#agentChatInput').inputValue(), draft);
    const reads = exactReads(id).length;
    interruptStart = id;
    await card.locator('[data-chat-start]').click();
    await card.locator('[data-chat-task-status="awaiting_job"]').waitFor();
    assert.ok(exactReads(id).length >= reads + 2, '启动前和丢失回应后都精确核对原任务');
    await until(() => exactReads(id).length > reads + 2, 'a progress poll after lost startup reply');
    assert.equal(starts(id).length, 1, '恢复核对与后续轮询不得自动再次POST');
    assert.equal(await card.locator('[data-chat-start]').count(), 0);
    assert.equal(await page.locator('#agentChatInput').inputValue(), draft);
    assert.equal(await page.evaluate(() => view), 'memory');
    const recoveredReads = exactReads(id).length;
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    await card.locator('[data-chat-task-status="awaiting_job"]').waitFor();
    assert.ok(exactReads(id).length > recoveredReads);
    assert.equal(starts(id).length, 1);
    assert.equal(await page.locator('#agentChatInput').inputValue(), draft);
  });

  await test('原聊天任务卡区分任务类型与真实节点，候选完成不冒充作者采纳', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixtures = [
      {mode: 'continue', label: '续写正文', status: 'awaiting_adoption', calls: 1,
        states: {continue: {status: 'success', job_id: 'ui_generated_prose', ai_run_id: 'ui_generated_run'}},
        task: {current_job: {id: 'ui_generated_prose', task: 'continue', status: 'success',
          result: {text: '她没有推门，只把铜钥匙轻轻转了半圈。'}}}},
      {mode: 'polish', label: '整章修订', status: 'awaiting_memory', calls: 2,
        states: {polish: {status: 'success', job_id: 'ui_polished_prose', ai_run_id: 'ui_polished_run'},
          chapter_analysis: {status: 'success', job_id: 'ui_chapter_memory', ai_run_id: 'ui_memory_run'}},
        task: {current_job: {id: 'ui_chapter_memory', task: 'chapter_analysis', status: 'success',
          result: {text: '章后记忆的内部文字不能作为正文候选预览。'}},
          result_refs: {chapter_analysis: {id: 'ui_memory_record'}}}},
      {mode: 'inspect', label: '章后检查', status: 'awaiting_job', calls: 1,
        states: {chapter_analysis: {status: 'running', job_id: 'ui_inspection', ai_run_id: 'ui_inspection_run'}}},
      {mode: 'plan', label: '全书规划', status: 'awaiting_plan_confirmation', calls: 1,
        states: {overview: {status: 'success', job_id: 'ui_overview', ai_run_id: 'ui_overview_run'}},
        task: {stage: 'overview', planning_workflow: {stage: 'overview',
          attempt: {id: 'ui_overview_attempt', candidate_hash: 'ui_overview_hash',
            candidate: {premise: '女主从铜钥匙里听见一座失踪城池的回声。'}},
          blueprint: {premise: '', volumes: []}, final_blueprint: null}}},
    ];
    const observations = [];
    for (const [index, item] of fixtures.entries()) {
      const fixture = fixtureSession(data, firstId, '核对任务卡 ' + index, {mode: item.mode,
        task: {status: item.status, calls_used: item.calls, steps: taskSteps(item.mode, item.states), ...item.task}});
      const card = await selectChat(page, fixture), id = fixture.row.id;
      await card.locator('[data-chat-task-status="' + item.status + '"]').waitFor();
      assert.ok((await card.textContent()).includes(item.label), '任务卡应明确显示类型：' + item.label);
      assert.equal(await card.locator('[data-chat-task-source]').textContent(), '来源章 · ' + firstTitle);
      assert.equal(await card.locator('.agent-chat-task-budget').textContent(),
        `模型调用 ${item.calls} / 上限 ${item.mode === 'plan' ? 4 : item.mode === 'inspect' ? 2 : 3} 次`);
      for (const step of fixture.row.steps) {
        const node = card.locator('[data-chat-task-step="' + step.task + '"]');
        assert.equal(await node.count(), 1, '应展示本次服务返回的真实步骤：' + step.task);
        assert.equal(await node.getAttribute('data-chat-step-status'), step.status);
        assert.ok((await node.textContent()).includes(step.label));
      }
      assert.ok(!await card.getByText('已确认，处理结果已保存', {exact: true}).count());
      const primaryCount = await card.locator('.agent-chat-action-buttons .primary-btn').count();
      assert.ok(primaryCount <= 1, '同一卡片至多提供一个主要操作');
      if (item.status !== 'awaiting_job') assert.equal(primaryCount, 1, '等待作者决定时应提供明确的主要操作');
      const adoption = card.locator('[data-chat-task-step="adoption"]');
      if (['continue', 'polish'].includes(item.mode)) {
        assert.equal(await adoption.count(), 1, '正文流程单独显示作者采纳节点');
        const state = await adoption.getAttribute('data-chat-step-status');
        if (item.status === 'awaiting_adoption') {
          assert.ok(!['success', 'complete', 'completed', 'accepted', 'confirmed', 'applied'].includes(state),
            '正文生成成功不能显示为已采纳');
          assert.equal(await card.locator('[data-chat-start]').count(), 0);
          assert.equal(await card.locator('[data-chat-open-result].primary-btn').count(), 1);
        } else {
          assert.ok(['success', 'complete', 'completed', 'accepted', 'confirmed', 'applied'].includes(state),
            '仅流程进入章后资料阶段后才能显示已采纳');
          const preview = card.locator('[data-chat-task-preview]');
          assert.ok(!(await preview.count() ? await preview.textContent() : '').includes(
            '章后记忆的内部文字不能作为正文候选预览'));
        }
      } else assert.equal(await adoption.count(), 0);
      assert.equal(taskPosts(id).length, 0, '阅读任务卡和候选不能启动任何模型操作');
      observations.push({mode: item.mode, status: item.status,
        steps: await card.locator('[data-chat-task-step]').evaluateAll(nodes =>
          nodes.map(node => ({task: node.dataset.chatTaskStep, status: node.dataset.chatStepStatus})))});
    }
    report.inline_task_steps = observations;
  });

  await test('正文成果安全折叠，在手动核对和轮询重绘后保持展开且不会多启动', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const longParagraphs = Array.from({length: 30}, (_, index) =>
      `第${index + 1}段候选正文：雨停在门外，渡口的灯亮着，林岚要把这封来信读完再决定下一步。`).join('\n\n');
    const malicious = longParagraphs + '\n\n<img src=x onerror="globalThis.__moyuTaskXss=1">' +
      '\n<script>globalThis.__moyuTaskXss=2</script>\n[点击](javascript:globalThis.__moyuTaskXss=3)';
    const fixture = fixtureSession(data, firstId, '可核对的正文成果', {mode: 'continue',
      task: {status: 'awaiting_adoption', calls_used: 1,
        steps: taskSteps('continue', {continue: {status: 'success', job_id: 'ui_preview_prose', ai_run_id: 'ui_preview_run'}}),
        current_job: {id: 'ui_preview_prose', task: 'continue', status: 'success', result: {text: malicious}}}});
    const card = await selectChat(page, fixture), id = fixture.row.id;
    const preview = card.locator('details[data-chat-task-preview]');
    await preview.waitFor();
    assert.equal(await preview.evaluate(node => node.open), false, '正文成果默认折叠');
    assert.ok((await preview.textContent()).includes('<img src=x onerror="globalThis.__moyuTaskXss=1">'));
    assert.equal(await preview.locator('img,script,iframe,a').count(), 0, '模型成果不产生可执行HTML或链接');
    assert.equal(await page.evaluate(() => globalThis.__moyuTaskXss || 0), 0);
    await preview.locator('summary').click();
    assert.equal(await preview.evaluate(node => node.open), true);
    const previewBody = preview.locator('.agent-chat-task-preview-body');
    await previewBody.evaluate(node => {node.scrollTop = 96;});
    const previewScroll = await previewBody.evaluate(node => node.scrollTop);
    assert.ok(previewScroll > 0, '正文预览内容超出可见高度时应可滚动');
    fixture.row.updated_at = timestamp(); fixture.row.context_evidence = {ui_revision: 1};
    const before = exactReads(id).length;
    await card.locator('[data-chat-task-refresh]').click();
    await until(() => exactReads(id).length > before, 'manual task preview refresh');
    await card.locator('[data-chat-task-refresh]:not([disabled])').waitFor();
    assert.equal(await preview.evaluate(node => node.open), true, '核对进度重绘保留作者的展开状态');
    assert.equal(await previewBody.evaluate(node => node.scrollTop), previewScroll,
      '核对进度重绘保留正文预览的内部滚动位置');

    const message = data.messages.get(fixture.session.id)[0];
    message.content = '候选已生成，等待作者阅读后决定是否采纳。';
    await until(() => page.locator('[data-chat-message="' + message.id + '"] .agent-chat-message-body')
      .textContent().then(text => text === message.content), 'chat polling redraw with original task preview');
    assert.equal(await preview.evaluate(node => node.open), true, '轮询重绘仍保留展开状态');
    assert.equal(taskPosts(id).length, 0, '预览、核对和轮询只能读取原任务');
    assert.equal(await page.evaluate(() => globalThis.__moyuTaskXss || 0), 0);
    assert.equal(await page.evaluate(() => view), 'memory');
    const screenshot = path.join(temporary, 'agent-chat-inline-prose-preview-1440x1000.png');
    await card.scrollIntoViewIfNeeded(); await page.screenshot({path: screenshot}); report.screenshots.push(screenshot);

    fixture.row.current_job = {id: 'ui_preview_prose', task: 'continue', status: 'success',
      result: {structured: {content: '结构化正文结果仍可在原聊天阅读。'}}};
    await card.locator('[data-chat-task-refresh]').click();
    await until(() => preview.textContent().then(text => text.includes('结构化正文结果仍可在原聊天阅读。')),
      'structured prose content preview');
    assert.equal(await preview.evaluate(node => node.open), true);
    assert.equal(taskPosts(id).length, 0);
  });

  await test('规划卡内确认与完整采用先核对最新检查点，轮询和双击不会重复执行', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const candidate = {premise: '铜钥匙指向一座被所有人遗忘的城。', genre: '悬疑奇幻',
      core_conflict: '<img src=x onerror="globalThis.__moyuPlanXss=1">', protagonist: '林岚'};
    const fixture = fixtureSession(data, firstId, '在原聊天确认规划', {mode: 'plan',
      task: {status: 'awaiting_plan_confirmation', stage: 'overview', calls_used: 1,
        steps: taskSteps('plan', {overview: 'success'}),
        planning_workflow: {id: 'ui_planning_workflow', stage: 'overview',
          attempt: {id: 'ui_plan_attempt', candidate_hash: 'ui_plan_hash', candidate},
          blueprint: {premise: '', volumes: []}, final_blueprint: null, applied: false}}});
    const card = await selectChat(page, fixture), id = fixture.row.id;
    const preview = card.locator('details[data-chat-task-preview]');
    await preview.waitFor();
    assert.equal(await preview.evaluate(node => node.open), false);
    assert.ok((await preview.textContent()).includes(candidate.premise));
    assert.ok((await preview.textContent()).includes(candidate.core_conflict));
    assert.equal(await preview.locator('img,script,iframe').count(), 0);
    assert.equal(await page.evaluate(() => globalThis.__moyuPlanXss || 0), 0);
    assert.equal(await card.locator('[data-chat-plan-confirm].primary-btn').count(), 1);
    assert.match(await card.locator('[data-chat-plan-confirm]').textContent(), /确认.*生成.*下一阶段/);
    assert.equal(await card.locator('[data-chat-start]').count(), 0);
    await preview.locator('summary').click();
    assert.equal(await preview.evaluate(node => node.open), true, '展开规划候选预览');
    const confirmCheckpoint = fixture.row.checkpoint_id = 'confirm_fresh_' + crypto.randomUUID();
    const before = exactReads(id).length;
    await card.locator('[data-chat-plan-confirm]').evaluate(button => {button.click(); button.click();});
    await card.locator('[data-chat-task-status="awaiting_job"]').waitFor();
    assert.ok(exactReads(id).length > before, '确认前重新读取精确任务');
    assert.equal(taskPosts(id, 'confirm').length, 1, '双击只确认并生成一次下一阶段');
    assert.equal(taskPosts(id, 'confirm')[0].body.checkpoint_id, confirmCheckpoint);
    assert.equal(starts(id).length, 0, '后续规划确认不重新authorize');
    assert.equal(fixture.row.calls_used, 2);
    const polls = exactReads(id).length;
    await until(() => exactReads(id).length > polls, 'planning poll after confirmation');
    assert.equal(taskPosts(id).length, 1, '后续轮询不会继续生成或重复确认');
    assert.equal(await card.locator('[data-chat-task-preview]').count(), 0,
      '生成下一阶段期间暂不显示候选预览，展开状态留给完整规划');

    const finalBlueprint = {premise: candidate.premise, volumes: [{id: 'ui_volume_1', title: '第一卷 遗忘之城',
      chapters: [{id: 'ui_plan_chapter_1', title: '第一章 铜钥匙',
        scenes: [{id: 'ui_scene_1', title: '门外', summary: '林岚第一次听见城池的回声。'}]}]}]};
    Object.assign(fixture.row, {status: 'awaiting_plan_application', stage: 'scenes', calls_used: 4,
      checkpoint_id: 'application_visible_' + crypto.randomUUID(),
      steps: taskSteps('plan', {overview: 'confirmed', volumes: 'confirmed', chapters: 'confirmed', scenes: 'confirmed'}),
      planning_workflow: {...fixture.row.planning_workflow, stage: 'scenes', attempt: null,
        blueprint: finalBlueprint, final_blueprint: finalBlueprint}});
    await card.locator('[data-chat-task-refresh]:not([disabled])').click();
    await card.locator('[data-chat-task-status="awaiting_plan_application"]').waitFor();
    assert.equal(await preview.evaluate(node => node.open), true, '阶段候选切换到完整规划后仍保留展开状态');
    assert.equal(await card.locator('[data-chat-plan-confirm]').count(), 0);
    assert.equal(await card.locator('[data-chat-plan-apply].primary-btn').count(), 1);
    assert.match(await card.locator('[data-chat-plan-apply]').textContent(), /采用.*完整规划|采用.*规划/);
    assert.ok((await preview.textContent()).includes('第一卷 遗忘之城'));
    assert.ok((await preview.textContent()).includes('林岚第一次听见城池的回声。'));
    const applyCheckpoint = fixture.row.checkpoint_id = 'apply_fresh_' + crypto.randomUUID();
    const applicationReads = exactReads(id).length;
    await card.locator('[data-chat-plan-apply]').evaluate(button => {button.click(); button.click();});
    await card.locator('[data-chat-task-status="review_ready"]').waitFor();
    assert.ok(exactReads(id).length > applicationReads, '采用完整规划前重新核对任务');
    assert.equal(taskPosts(id, 'apply').length, 1, '完整规划只采用一次');
    assert.equal(taskPosts(id, 'apply')[0].body.checkpoint_id, applyCheckpoint);
    assert.equal(fixture.row.calls_used, 4, '采用完整规划不增加模型调用');
    assert.equal(await card.locator('[data-chat-plan-apply],[data-chat-plan-confirm],[data-chat-start]').count(), 0);
    assert.equal(await page.evaluate(() => view), 'memory');
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    await card.locator('[data-chat-task-status="review_ready"]').waitFor();
    assert.equal(taskPosts(id).length, 2, '重新打开不会再次确认、采用或启动');
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
    report.inline_plan_operations = taskPosts(id).map(row => ({action: row.body.action, checkpoint_id: row.body.checkpoint_id}));
  });

  await test('规划确认与采用保留来源保护，迟到核对不能操作其他聊天', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main'), observations = [];
    for (const action of ['confirm', 'apply']) {
      const status = action === 'confirm' ? 'awaiting_plan_confirmation' : 'awaiting_plan_application';
      const fixture = fixtureSession(data, firstId, '规划身份保护 ' + action, {mode: 'plan',
        task: {status, stage: action === 'confirm' ? 'overview' : 'scenes', calls_used: action === 'confirm' ? 1 : 4,
          steps: taskSteps('plan', action === 'confirm' ? {overview: 'success'} :
            {overview: 'confirmed', volumes: 'confirmed', chapters: 'confirmed', scenes: 'confirmed'}),
          planning_workflow: {attempt: action === 'confirm' ? {id: 'ui_identity_attempt',
            candidate_hash: 'ui_identity_hash', candidate: {premise: '本任务的规划候选。'}} : null,
            blueprint: {premise: '本任务的完整规划。', volumes: []},
            final_blueprint: action === 'apply' ? {premise: '本任务的完整规划。', volumes: []} : null}}});
      const card = await selectChat(page, fixture), id = fixture.row.id, selector = '[data-chat-plan-' + action + ']';
      await card.locator(selector + ':not([disabled])').waitFor();
      for (const patch of [{source_stale: true}, {restored_read_only: true}, {chapter_id: secondId}]) {
        data.writingFaults.set(id, {patch});
        await card.locator('[data-chat-task-refresh]:not([disabled])').click();
        await card.locator('[data-chat-task-refresh]:not([disabled])').waitFor();
        assert.equal(await card.locator(selector + ':not([disabled])').count(), 0,
          '来源已变、历史只读或章节不符均不能确认/采用规划');
        assert.equal(taskPosts(id).length, 0);
        data.writingFaults.delete(id);
        await card.locator('[data-chat-task-refresh]:not([disabled])').click();
        await card.locator(selector + ':not([disabled])').waitFor();
      }
      const gate = gateOnce(row => row.exact && row.id === id);
      try {
        await card.locator(selector).click();
        await bounded(gate.entered, 'plan action fresh read held: ' + action);
        const target = fixtureSession(data, secondId, '保留新的创作讨论 ' + action,
          {task: {status: 'requires_action'}});
        const targetCard = await selectChat(page, target);
        await targetCard.locator('[data-chat-task-status="requires_action"]').waitFor();
        const draft = '切换聊天后仍保留的提问 ' + action;
        await page.locator('#agentChatInput').fill(draft);
        await releaseGate(page, gate);
        assert.equal(await page.locator('[data-chat-action="' + fixture.action.id + '"]').count(), 0);
        assert.equal(await page.locator('#agentChatInput').inputValue(), draft);
        assert.equal(taskPosts(id).length, 0, '旧聊天核对返回后不得执行确认或采用');
        assert.equal(taskPosts(target.row.id).length, 0);
        assert.equal(await page.evaluate(() => view), 'memory');
        observations.push({action, task_id: id, target_task: target.row.id, posts_after_switch: taskPosts(id).length});
      } finally {
        gate.release(); data.writingFaults.delete(id);
      }
    }
    report.inline_plan_identity = observations;
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
  });

  await test('任务读取失败或编号章节方式指令不符时撤销启动资格，恢复后重新核实', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, firstId, '严格核对来源的任务');
    const card = await selectChat(page, fixture), id = fixture.row.id, observations = [];
    await card.locator('[data-chat-start]:not([disabled])').waitFor();
    const faults = [{name: '503', status: 503}, {name: 'id', patch: {id: 'another_task'}},
      {name: 'chapter', patch: {chapter_id: secondId}}, {name: 'mode', patch: {mode: 'inspect'}},
      {name: 'query', patch: {query: '另一条不相关的创作要求'}}];
    for (const fault of faults) {
      data.writingFaults.set(id, fault);
      await card.locator('[data-chat-task-refresh]:not([disabled])').click();
      await card.locator('[data-chat-task-status="unverified"]').waitFor();
      await until(() => card.locator('[data-chat-task-error]').textContent(), 'failed task identity: ' + fault.name);
      assert.equal(await card.locator('[data-chat-start]').count(), 0, fault.name);
      assert.equal(starts(id).length, 0, fault.name);
      assert.equal(await card.locator('[data-chat-task-source]').textContent(), '来源章 · ' + firstTitle);
      observations.push({fault: fault.name, status: await card.locator('[data-chat-task-status]').getAttribute('data-chat-task-status')});
      data.writingFaults.delete(id);
      await card.locator('[data-chat-task-refresh]:not([disabled])').click();
      await card.locator('[data-chat-start]:not([disabled])').waitFor();
      assert.equal(await card.locator('[data-chat-task-error]').count(), 0);
    }
    assert.equal(await page.evaluate(() => view), 'memory');
    try {
      const renamed = await page.evaluate(({chapterId, actionId}) => {
        globalThis.__moyuInlineOriginalChapters = state.chapters;
        state.chapters = state.chapters.map(row => row.id === chapterId ? {...row, title: '当前来源章新标题'} : row);
        MoyuAgentChat.sync();
        const card = document.querySelector('[data-chat-action="' + actionId + '"]');
        return {source: card.querySelector('[data-chat-task-source]').textContent,
          starts: card.querySelectorAll('[data-chat-start]').length};
      }, {chapterId: firstId, actionId: fixture.action.id});
      assert.deepEqual(renamed, {source: '来源章 · 当前来源章新标题', starts: 1});
      const missing = await page.evaluate(({chapterId, actionId}) => {
        state.chapters = state.chapters.filter(row => row.id !== chapterId);
        MoyuAgentChat.sync();
        const card = document.querySelector('[data-chat-action="' + actionId + '"]');
        return {source: card.querySelector('[data-chat-task-source]').textContent,
          starts: card.querySelectorAll('[data-chat-start]').length};
      }, {chapterId: firstId, actionId: fixture.action.id});
      assert.deepEqual(missing, {source: '来源章节已不存在', starts: 0});
      assert.equal(starts(id).length, 0);
    } finally {
      await page.evaluate(() => {
        if (globalThis.__moyuInlineOriginalChapters) state.chapters = globalThis.__moyuInlineOriginalChapters;
        delete globalThis.__moyuInlineOriginalChapters; MoyuAgentChat.sync();
      });
    }
    assert.equal(await card.locator('[data-chat-task-source]').textContent(), '来源章 · ' + firstTitle);
    assert.equal(await card.locator('[data-chat-start]').isEnabled(), true);
    const otherChapter = fixtureSession(data, secondId, '另一章的独立任务');
    const otherCard = await selectChat(page, otherChapter);
    await otherCard.locator('[data-chat-start]:not([disabled])').waitFor();
    assert.equal(await otherCard.locator('[data-chat-task-source]').textContent(), '来源章 · ' + secondTitle);
    assert.equal(await otherCard.locator('[data-chat-writing-session]').getAttribute('data-chat-writing-session'), otherChapter.row.id);
    assert.equal(await page.locator('[data-chat-writing-session="' + id + '"]').count(), 0);
    assert.equal(starts(id).length + starts(otherChapter.row.id).length, 0);
    report.inline_task_identity_failures = observations;
  });

  await test('续写先保存稿件和未保存规划，保存失败不读取或启动，成功使用最新检查点', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, firstId, '续写前保留作者规划');
    const card = await selectChat(page, fixture), id = fixture.row.id;
    await card.locator('[data-chat-start]:not([disabled])').waitFor();
    const reads = exactReads(id).length;
    await page.evaluate(taskId => {
      globalThis.__moyuInlineOriginalSave = authorSaveBlueprint;
      globalThis.__moyuInlineOriginalSync = syncBackend;
      globalThis.__moyuInlineOriginalDirty = authorBlueprintDirty;
      globalThis.__moyuInlineOrderId = taskId; globalThis.__moyuInlineOrder = [];
      syncBackend = async function(...args) {
        globalThis.__moyuInlineOrder.push('sync');
        return globalThis.__moyuInlineOriginalSync.apply(this, args);
      };
      authorBlueprintDirty = true;
      authorSaveBlueprint = async function() {globalThis.__moyuInlineOrder.push('save-failed'); return false;};
    }, id);
    try {
      await card.locator('[data-chat-start]').click();
      await until(() => card.locator('[data-chat-action-error]').textContent(), 'unsaved blueprint refusal');
      assert.deepEqual(await page.evaluate(() => globalThis.__moyuInlineOrder), ['sync', 'save-failed']);
      assert.equal(exactReads(id).length, reads);
      assert.equal(starts(id).length, 0);
      const checkpoint = 'after_save_' + crypto.randomUUID();
      fixture.row.checkpoint_id = checkpoint;
      await page.evaluate(() => {
        globalThis.__moyuInlineOrder = [];
        authorSaveBlueprint = async function() {
          globalThis.__moyuInlineOrder.push('save'); authorBlueprintDirty = false; return true;
        };
      });
      await card.locator('[data-chat-start]:not([disabled])').click();
      await card.locator('[data-chat-task-status="awaiting_job"]').waitFor();
      assert.deepEqual(await page.evaluate(() => globalThis.__moyuInlineOrder), ['sync', 'save', 'get', 'post']);
      assert.equal(starts(id).length, 1);
      assert.equal(starts(id)[0].body.checkpoint_id, checkpoint);
    } finally {
      await page.evaluate(() => {
        authorSaveBlueprint = globalThis.__moyuInlineOriginalSave;
        syncBackend = globalThis.__moyuInlineOriginalSync;
        authorBlueprintDirty = globalThis.__moyuInlineOriginalDirty;
        delete globalThis.__moyuInlineOrderId; delete globalThis.__moyuInlineOrder;
        delete globalThis.__moyuInlineOriginalSave; delete globalThis.__moyuInlineOriginalSync;
        delete globalThis.__moyuInlineOriginalDirty;
      });
    }
  });

  await test('历史只读未知结果和待处理任务不能启动，终态经过聊天轮询仍不高频读取', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, firstId, '保留终态任务', {task: {status: 'unknown'}});
    const variants = [
      {name: '需要处理', status: 'requires_action', error: '原任务费用状态未知，未重新发送。'},
      {name: '历史任务', status: 'restored', restored_read_only: true, checkpoint_id: ''},
      {name: '只读任务', status: 'awaiting_authorization', restored_read_only: true, checkpoint_id: ''},
      {name: '已停止', status: 'canceled'}, {name: '审稿就绪', status: 'review_ready'},
      {name: '来源变化', status: 'awaiting_authorization', source_stale: true},
    ];
    const actions = data.messages.get(fixture.session.id)[0].actions, ids = [fixture.row.id];
    for (const variant of variants) {
      const action = {id: crypto.randomUUID(), kind: 'continue', label: variant.name,
        chapter_id: firstId, instruction: '核对原任务：' + variant.name, status: 'confirmed', result: {}};
      actions.push(action); ids.push(writingTask(data, action, variant).id);
    }
    await selectChat(page, fixture);
    for (const id of ids) {
      await page.locator('[data-chat-writing-session="' + id + '"]:not([data-chat-task-status="unverified"])').waitFor();
    }
    assert.equal(await page.locator('[data-chat-start]').count(), 0);
    const counts = ids.map(id => exactReads(id).length);
    const chatReads = () => requests.filter(row => row.method === 'GET' && row.suffix === '/sessions/' + fixture.session.id).length;
    const previous = chatReads();
    await until(() => chatReads() >= previous + 2, 'two chat polls with terminal tasks');
    assert.deepEqual(ids.map(id => exactReads(id).length), counts);
    assert.ok(ids.every(id => starts(id).length === 0));

    const restored = fixtureSession(data, firstId, '备份中的只读聊天', {readOnly: true});
    const card = await selectChat(page, restored);
    await card.locator('[data-chat-task-status="awaiting_authorization"]').waitFor();
    assert.equal(await card.locator('[data-chat-start]').count(), 0);
    assert.equal(await page.locator('#agentChatInput').isDisabled(), true);
    assert.equal(starts(restored.row.id).length, 0);
    const missing = fixtureSession(data, 'ui_missing_chapter', '来源章节缺失的保留任务');
    const missingCard = await selectChat(page, missing);
    await missingCard.locator('[data-chat-task-status="awaiting_authorization"]').waitFor();
    assert.equal(await missingCard.locator('[data-chat-task-source]').textContent(), '来源章节已不存在');
    assert.equal(await missingCard.locator('[data-chat-start]').count(), 0);
    assert.equal(starts(missing.row.id).length, 0);
    report.inline_task_terminal_reads = ids.map((id, index) => ({id, reads_before: counts[index], reads_after: exactReads(id).length}));
  });

  await test('历史规划或缺失规划记录的任务不跳转，也不会打开其他规划', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const other = await api('/api/planning-workflows', 'POST',
      {initial: {premise: '另一份绝不能被历史任务打开的规划'}});
    assert.ok(other.id, '先建立另一份真实规划，确认误回退会被发现');
    const fixture = fixtureSession(data, firstId, '备份中的规划任务', {mode: 'plan', task: {
      status: 'review_ready', restored_read_only: true, planning_workflow: null, checkpoint_id: '',
      source_stale: true, calls_used: 4,
      steps: taskSteps('plan', {overview: 'confirmed', volumes: 'confirmed', chapters: 'confirmed', scenes: 'confirmed'})}});
    const card = await selectChat(page, fixture), id = fixture.row.id;
    await card.locator('[data-chat-task-status="review_ready"]').waitFor();
    assert.equal(await card.locator('[data-chat-task-preview]').count(), 0, '历史任务没有可打开的规划内容');
    assert.ok((await card.textContent()).includes('历史任务没有保留对应的创作规划'), '卡片明确说明历史任务未保留规划');
    assert.equal(await card.locator('[data-chat-start]').count(), 0);
    const button = card.locator('[data-chat-open-result]');
    assert.equal(await button.textContent(), '查看任务记录');
    await button.click();
    await until(() => card.locator('[data-chat-action-error]').textContent().then(text =>
      text.includes('没有保留对应的创作规划')), 'historical plan guard');
    assert.equal(await page.evaluate(() => view), 'memory', '不跳转到创作规划页面');
    assert.equal(await page.evaluate(() => authorPlanningSelected), null, '不选中其他规划');
    assert.equal(taskPosts(id, 'confirm').length, 0, '只读核对不会推进任务');
    assert.equal(taskPosts(id, 'apply').length, 0, '只读核对不会采用规划');

    const broken = fixtureSession(data, firstId, '规划记录缺失的任务', {mode: 'plan', task: {
      status: 'review_ready', restored_read_only: false, planning_workflow: null, checkpoint_id: 'chk_missing_plan',
      source_stale: false, calls_used: 4,
      steps: taskSteps('plan', {overview: 'confirmed', volumes: 'confirmed', chapters: 'confirmed', scenes: 'confirmed'})}});
    const brokenCard = await selectChat(page, broken), brokenId = broken.row.id;
    await brokenCard.locator('[data-chat-task-status="review_ready"]').waitFor();
    assert.equal(await brokenCard.locator('[data-chat-task-preview]').count(), 0);
    assert.ok((await brokenCard.textContent()).includes('本次任务的规划记录缺失'), '缺失规划记录的任务不冒充历史任务');
    const brokenButton = brokenCard.locator('[data-chat-open-result]');
    assert.equal(await brokenButton.textContent(), '查看任务记录');
    await brokenButton.click();
    await until(() => brokenCard.locator('[data-chat-action-error]').textContent().then(text =>
      text.includes('本次任务缺少规划记录')), 'missing plan record guard');
    assert.equal(await page.evaluate(() => view), 'memory', '缺失规划记录时也不跳转');
    assert.equal(await page.evaluate(() => authorPlanningSelected), null, '缺失规划记录时不选中其他规划');
    assert.equal(taskPosts(brokenId, 'confirm').length, 0);
    assert.equal(taskPosts(brokenId, 'apply').length, 0);
    await page.evaluate(() => {
      authorPlanningSelected='ui_missing_planning_history';view='blueprint';render();
    });
    await page.locator('[data-planning-latest]').waitFor();
    assert.equal(await page.evaluate(() => authorPlanningSelected), 'ui_missing_planning_history');
    await page.evaluate(() => authorLoadPlanning());
    assert.equal(await page.evaluate(() => authorPlanning), null, '再次读取也不回退到另一份规划');
    assert.equal(await page.evaluate(() => authorPlanningSelected), 'ui_missing_planning_history');
    await page.locator('[data-view="memory"]').click();
    await page.locator('[data-view="blueprint"]').click();
    await page.locator('[data-planning-latest]').waitFor();
    assert.equal(await page.evaluate(() => authorPlanning), null, '切页返回仍保留显式缺失记录');
    await page.locator('[data-planning-latest]').click();
    await page.waitForFunction(id => authorPlanning?.id === id, other.id);
    assert.equal(await page.locator('[data-planning-latest]').count(), 0, '作者明确选择才打开当前规划');
    await page.locator('[data-view="memory"]').click();
    report.inline_historical_plan_guard = {historical: id, missing: brokenId, other_planning: other.id,
      reads: exactReads(id).length + exactReads(brokenId).length};
  });

  await test('规划确认与采用回应丢失后不重复写入，刷新和切会话仍保持', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main'), observations = [];
    for (const action of ['confirm', 'apply']) {
      const status = action === 'confirm' ? 'awaiting_plan_confirmation' : 'awaiting_plan_application';
      const fixture = fixtureSession(data, firstId, '回应丢失的规划 ' + action, {mode: 'plan',
        task: {status, stage: action === 'confirm' ? 'overview' : 'scenes', calls_used: action === 'confirm' ? 1 : 4,
          steps: taskSteps('plan', action === 'confirm' ? {overview: 'success'} :
            {overview: 'confirmed', volumes: 'confirmed', chapters: 'confirmed', scenes: 'confirmed'}),
          planning_workflow: {attempt: action === 'confirm' ? {id: 'ui_lost_attempt',
            candidate_hash: 'ui_lost_hash', candidate: {premise: '回应丢失时的规划候选。'}} : null,
            blueprint: {premise: '回应丢失时的完整规划。', volumes: []},
            final_blueprint: action === 'apply' ? {premise: '回应丢失时的完整规划。', volumes: []} : null}}});
      const card = await selectChat(page, fixture), id = fixture.row.id;
      const selector = '[data-chat-plan-' + action + ']:not([disabled])';
      await card.locator(selector).waitFor();
      const gate = gateOnce(row => row.method === 'POST' && row.id === id &&
        row.body?.action === action, 'abort');
      await card.locator(selector).click();
      await bounded(gate.entered, 'plan response loss POST: ' + action);
      gate.release(); await bounded(gate.finished, 'plan response loss abort: ' + action);
      const expected = action === 'confirm' ? 'awaiting_job' : 'review_ready';
      await card.locator('[data-chat-task-status="' + expected + '"]').waitFor();
      assert.equal(taskPosts(id, action).length, 1, action + ' 只写入一次');
      await pause(600);
      assert.equal(taskPosts(id, action).length, 1, action + ' 轮询不重复写入');
      await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
      await page.locator('[data-view="memory"]').click();
      await card.locator('[data-chat-task-status="' + expected + '"]').waitFor();
      assert.equal(taskPosts(id, action).length, 1, action + ' 刷新后不重复写入');
      const other = fixtureSession(data, secondId, '回应丢失后的另一段讨论');
      await selectChat(page, other);
      await selectChat(page, fixture);
      assert.equal(taskPosts(id, action).length, 1, action + ' 切会话后不重复写入');
      observations.push({action, task_id: id, posts: taskPosts(id, action).length,
        checkpoint: fixture.row.checkpoint_id});
    }
    report.inline_plan_response_loss = observations;
    assert.deepEqual(report.page_errors, []);
  });

  await test('操作前重新读取发现候选变化时放弃确认，恢复后可正常继续', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, firstId, '候选变化的规划', {mode: 'plan',
      task: {status: 'awaiting_plan_confirmation', stage: 'overview', calls_used: 1,
        steps: taskSteps('plan', {overview: 'success'}),
        planning_workflow: {attempt: {id: 'ui_stale_attempt', candidate_hash: 'ui_stale_hash',
          candidate: {premise: '尚未读取的新候选。'}}, blueprint: {premise: '', volumes: []},
          final_blueprint: null}}});
    const card = await selectChat(page, fixture), id = fixture.row.id;
    await card.locator('[data-chat-plan-confirm]:not([disabled])').waitFor();
    data.writingFaults.set(id, {patch: {planning_workflow: {...fixture.row.planning_workflow,
      attempt: {...fixture.row.planning_workflow.attempt, candidate_hash: 'ui_fresh_hash'}}}});
    await card.locator('[data-chat-plan-confirm]').click();
    await until(() => card.locator('[data-chat-action-error]').textContent().then(text =>
      text.includes('规划阶段或候选已变化')), 'stale candidate refusal');
    assert.equal(taskPosts(id, 'confirm').length, 0, '候选变化时不能写入确认');
    data.writingFaults.delete(id);
    await card.locator('[data-chat-task-refresh]:not([disabled])').click();
    await card.locator('[data-chat-plan-confirm]:not([disabled])').waitFor();
    await card.locator('[data-chat-plan-confirm]').click();
    await card.locator('[data-chat-task-status="awaiting_job"]').waitFor();
    assert.equal(taskPosts(id, 'confirm').length, 1, '恢复后只确认一次');
    report.inline_plan_stale_candidate = {task_id: id, posts: taskPosts(id, 'confirm').length};
  });

  await test('原步骤未收到请求时可手动恢复，刷新切会话不自动重试或丢失原检查点', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main'), observations = [];
    for (const action of ['authorize', 'confirm', 'apply']) {
      const fixture = fixtureSession(data, firstId, '请求未送达的步骤 ' + action,
        action === 'authorize' ? {} : {mode: 'plan', task: {
          status: action === 'confirm' ? 'awaiting_plan_confirmation' : 'awaiting_plan_application',
          stage: action === 'confirm' ? 'overview' : 'scenes', calls_used: action === 'confirm' ? 1 : 4,
          steps: taskSteps('plan', action === 'confirm' ? {overview: 'success'} :
            {overview: 'confirmed', volumes: 'confirmed', chapters: 'confirmed', scenes: 'confirmed'}),
          planning_workflow: {id: 'ui_not_delivered_' + action, version: 2, source_version: 1,
            attempt: action === 'confirm' ? {id: 'ui_not_delivered_attempt', candidate_hash: 'ui_not_delivered_hash',
              candidate: {premise: '断网前已读的同一份候选。'}} : null,
            blueprint: {premise: '断网前已读的同一份规划。', volumes: []}, applied: false}}});
      const card = await selectChat(page, fixture), id = fixture.row.id;
      const checkpoint = fixture.row.checkpoint_id, status = fixture.row.status;
      const selector = action === 'authorize' ? '[data-chat-start]' : '[data-chat-plan-' + action + ']';
      await card.locator(selector + ':not([disabled])').waitFor();
      rejectedWrites.add(id);
      await card.locator(selector).click();
      const retry = card.locator('[data-chat-task-retry]:not([disabled])');
      await retry.waitFor();
      assert.equal(fixture.row.status, status, '请求未到达，服务端原任务保持原状');
      assert.equal(fixture.row.checkpoint_id, checkpoint);
      assert.equal(taskPosts(id, action).length, 1);
      assert.equal(taskPosts(id, action)[0].not_delivered, true);
      assert.equal(await card.locator(selector).count(), 0, '未知结果期间只提供明确的恢复入口');
      assert.match(await card.textContent(), action === 'apply' ? /不新增模型调用/ : /按当前模型额度计费/);
      await until(() => card.locator('[data-chat-task-error]').textContent().then(text =>
        text.includes('未自动重发')), 'manual recovery message');
      assert.ok(!(await card.textContent()).includes('请勿重复'), '恢复提示与手动恢复按钮不能互相矛盾');
      if(action==='confirm'){
        const screenshot = path.join(temporary, 'agent-chat-manual-recovery-1440x1000.png');
        await card.scrollIntoViewIfNeeded();await page.screenshot({path:screenshot});report.screenshots.push(screenshot);
      }
      await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
      await retry.waitFor();
      const other = fixtureSession(data, secondId, '断网后继续保留的讨论 ' + action);
      await selectChat(page, other); await selectChat(page, fixture);
      await retry.waitFor();
      assert.equal(taskPosts(id, action).length, 1, '刷新和切会话只读取，不自动重发');
      const beforeReads = exactReads(id).length;
      await retry.evaluate(button => {button.click(); button.click();});
      await card.locator('[data-chat-task-status="' +
        (action === 'apply' ? 'review_ready' : 'awaiting_job') + '"]').waitFor();
      assert.ok(exactReads(id).length > beforeReads, '手动重试前再精确读取原任务');
      assert.equal(taskPosts(id, action).length, 2, '双击只新增一次手动重试');
      assert.deepEqual(taskPosts(id, action).map(row => row.body), [
        {action, checkpoint_id: checkpoint}, {action, checkpoint_id: checkpoint}]);
      assert.equal(taskPosts(id, action).filter(row => !row.not_delivered).length, 1,
        '只有一次请求交付给模拟服务处理');
      assert.equal(await card.locator('[data-chat-task-retry]').count(), 0);
      observations.push({action, attempts: 2, delivered: 1});
    }
    report.inline_not_delivered_recovery = observations;
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
  });

  await test('手动恢复前发现原候选或任务身份变化时不重发请求', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, firstId, '恢复前必须再核对候选', {mode: 'plan', task: {
      status: 'awaiting_plan_confirmation', stage: 'overview', calls_used: 1,
      steps: taskSteps('plan', {overview: 'success'}),
      planning_workflow: {id: 'ui_retry_identity', version: 1, source_version: 1,
        attempt: {id: 'ui_retry_attempt', candidate_hash: 'ui_retry_hash', candidate: {premise: '原候选。'}},
        blueprint: {premise: '', volumes: []}}}});
    const card = await selectChat(page, fixture), id = fixture.row.id;
    await card.locator('[data-chat-plan-confirm]:not([disabled])').waitFor();
    rejectedWrites.add(id); await card.locator('[data-chat-plan-confirm]').click();
    const retry = card.locator('[data-chat-task-retry]:not([disabled])');
    await retry.waitFor();
    const original = copy(fixture.row.planning_workflow);
    fixture.row.planning_workflow.attempt.candidate_hash = 'changed_after_original_send';
    await retry.click();
    await until(() => card.locator('[data-chat-action-error]').textContent().then(text =>
      text.includes('规划阶段或候选已变化')), 'manual retry rejects replaced candidate');
    assert.equal(taskPosts(id, 'confirm').length, 1, '候选不一致时不得重发');
    assert.equal(await card.locator('[data-chat-task-retry]').count(), 0);
    fixture.row.planning_workflow = original;
    await card.locator('[data-chat-task-refresh]:not([disabled])').click(); await retry.waitFor();
    for (const patch of [{source_stale: true}, {restored_read_only: true}, {chapter_id: secondId}]) {
      data.writingFaults.set(id, {patch});
      await retry.click();
      await card.locator('[data-chat-task-refresh]:not([disabled])').waitFor();
      assert.equal(taskPosts(id, 'confirm').length, 1, '来源变化、只读或章节不符不能恢复');
      assert.equal(await card.locator('[data-chat-task-retry]').count(), 0);
      data.writingFaults.delete(id);
      await card.locator('[data-chat-task-refresh]:not([disabled])').click(); await retry.waitFor();
    }
    const gate = gateOnce(row => row.exact && row.id === id);
    try {
      await retry.click(); await bounded(gate.entered, 'manual retry preflight held');
      const other = fixtureSession(data, secondId, '恢复期间切换的讨论');
      await selectChat(page, other);
      await releaseGate(page, gate);
      assert.equal(taskPosts(id, 'confirm').length, 1, '切换会话后旧恢复操作不得继续');
      assert.equal(taskPosts(other.row.id).length, 0);
    } finally {gate.release();}
    await selectChat(page, fixture);await retry.waitFor();
    await page.evaluate(taskId => {
      const key=Object.keys(localStorage).find(key=>key.endsWith(':'+taskId)&&key.includes(':agent-chat-task-uncertain:'));
      if(!key)throw new Error('Synthetic uncertain record missing');
      const value=JSON.parse(localStorage.getItem(key));delete value.identity;
      localStorage.setItem(key,JSON.stringify(value));
    },id);
    await page.reload();await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    await until(() => card.textContent().then(text=>text.includes('旧版记录缺少原候选校验')),
      'legacy uncertain identity warning');
    assert.equal(await card.locator('[data-chat-task-retry],[data-chat-plan-confirm]').count(),0,
      '没有原候选校验的旧记录不能猜造身份重试');
    assert.equal(taskPosts(id,'confirm').length,1);
    report.inline_retry_identity_guard = {task_id: id, attempts: taskPosts(id, 'confirm').length, delivered: 0};
  });

  await test('场景候选显示所属分卷和章节标题', async () => {
    await page.locator('[data-view="memory"]').click();
    const data = workspace('default-project', 'main');
    const blueprint = {premise: '铜钥匙指向遗忘之城。', volumes: [
      {id: 'ui_title_volume', title: '第一卷 遗忘之城',
        chapters: [{id: 'ui_title_chapter', title: '第一章 铜钥匙',
          scenes: [{id: 'ui_title_scene', title: '门外'}]}]}]};
    const fixture = fixtureSession(data, firstId, '场景候选标题', {mode: 'plan',
      task: {status: 'awaiting_plan_confirmation', stage: 'scenes', calls_used: 3,
        steps: taskSteps('plan', {overview: 'confirmed', volumes: 'confirmed',
          chapters: 'confirmed', scenes: 'success'}),
        planning_workflow: {stage: 'scenes', applied: false,
          attempt: {id: 'ui_title_attempt', candidate_hash: 'ui_title_hash', candidate: {
            volumes: [{id: 'ui_title_volume', chapters: [{id: 'ui_title_chapter', scenes: [
              {id: 'ui_title_scene', title: '门外', goal: '听见城池的回声',
                location: '渡口', pov: '林岚'}]}]}]}},
          blueprint, final_blueprint: null}}});
    const card = await selectChat(page, fixture);
    const preview = card.locator('details[data-chat-task-preview]');
    await preview.waitFor();
    await preview.locator('summary').click();
    const text = await preview.textContent();
    assert.ok(text.includes('第一卷 遗忘之城'), '场景候选显示所属分卷标题');
    assert.ok(text.includes('第一章 铜钥匙'), '场景候选显示所属章节标题');
    assert.ok(text.includes('门外') && text.includes('听见城池的回声'));
    report.inline_plan_scene_titles = {excerpt: text.slice(0, 160)};
  });

  await test('任务与查看结果的迟到读取跨聊天作品分支均不串线或导航', async () => {
    const observations = [];
    report.inline_task_scope_races = observations;
    await page.locator('[data-view="library"]').click();
    await page.locator('[data-project-open="' + otherId + '"]').waitFor();
    const syntheticBranch = 'inline-task-synthetic';
    const syntheticState = async route => {
      const request = route.request(), headers = request.headers();
      if (headers['x-moyu-branch'] !== syntheticBranch) return route.continue();
      assert.equal(request.method(), 'GET', '合成分支只用于读取身份隔离，不允许写入正式作品');
      const value = await api('/api/state', 'GET', undefined, headers['x-moyu-project']);
      value.branch_id = syntheticBranch; value.project.branch_id = syntheticBranch;
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(value)});
    };
    await page.route('**/api/state', syntheticState);
    try {
    for (const kind of ['refresh', 'result']) for (const transition of ['chat', 'project', 'branch']) {
      await page.evaluate(() => authorSwitchProject('default-project'));
      await page.locator('[data-view="memory"]').click();
      const data = workspace('default-project', 'main');
      const source = fixtureSession(data, firstId, '迟到来源 ' + kind + ' ' + transition);
      const sourceCard = await selectChat(page, source);
      await sourceCard.locator('[data-chat-start]:not([disabled])').waitFor();
      let reads = 0;
      const gate = gateOnce(row => row.exact && row.id === source.row.id &&
        ++reads === (kind === 'result' ? 2 : 1));
      try {
        await sourceCard.locator(kind === 'result' ? '[data-chat-open-result]' : '[data-chat-task-refresh]').click();
        await bounded(gate.entered, 'late ' + kind + ' request before ' + transition);
        let target;
        if (transition === 'project') {
          target = fixtureSession(workspace(otherId, otherBranch), otherState.chapters[0].id,
            '目标作品聊天', {project: otherId, branch: otherBranch, task: {status: 'requires_action'}});
          const switched = await page.evaluate(id => authorSwitchProject(id), otherId);
          assert.equal(switched, true, JSON.stringify(await page.evaluate(() => ({
            project: requestedProject, branch: state.branch_id, backendOnline, saveError,
          }))));
        } else if (transition === 'branch') {
          target = fixtureSession(workspace('default-project', syntheticBranch), firstId,
            '目标分支聊天', {branch: syntheticBranch, task: {status: 'requires_action'}});
          await page.evaluate(value => {
            state.branch_id = value; state.project.branch_id = value;
            confirmedState = MoyuSync.copy(state); render();
          }, syntheticBranch);
        } else target = fixtureSession(data, firstId, '目标聊天', {task: {status: 'requires_action'}});
        const targetCard = await selectChat(page, target);
        await page.locator('[data-view="memory"]').click();
        const draft = '保留目标输入 ' + kind + ' ' + transition;
        await page.locator('#agentChatInput').fill(draft);
        const identity = () => page.evaluate(() => ({project: requestedProject, branch: state.branch_id || 'main',
          view, chapter: activeChapter, focus: authorAgentFocus?.id || null}));
        const before = await identity();
        await releaseGate(page, gate);
        await targetCard.locator('[data-chat-task-status="requires_action"]').waitFor();
        assert.deepEqual(await identity(), before, kind + ' / ' + transition);
        assert.equal(await page.locator('[data-chat-action="' + source.action.id + '"]').count(), 0);
        assert.equal(await targetCard.locator('[data-chat-task-error]').count(), 0);
        assert.equal(await page.locator('#agentChatInput').inputValue(), draft);
        assert.equal(starts(source.row.id).length, 0);
        assert.ok(exactReads(target.row.id).every(row => row.project === target.session.project_id && row.branch === target.session.branch_id));
        observations.push({kind, transition, source_task: source.row.id, target_task: target.row.id,
          source_reads_before_switch: reads, final_identity: before});
      } finally {
        gate.release();
        await page.evaluate(() => {
          if(requestedProject==='default-project'&&state.branch_id==='inline-task-synthetic'){
            state.branch_id = 'main'; state.project.branch_id = 'main';
            confirmedState = MoyuSync.copy(state); render();
          }
        });
        await page.evaluate(() => authorSwitchProject('default-project'));
      }
    }
    } finally {
      await page.unroute('**/api/state', syntheticState);
    }
    report.inline_task_scope_races = observations;
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
  });
  await test('灵感回复安全排版，继续追问保留草稿且复制原文不触发执行', async () => {
    delayedReply = false;
    const data = workspace('default-project', 'main');
    const fixture = fixtureSession(data, null, '暗河中的来信', {proposed: true, mode: 'save_inspiration'});
    const message = data.messages.get(fixture.session.id)[0];
    message.content = '# 暗河中的来信\n\n**核心冲突**\n\n1. 寻找失踪父亲。\n2. 保护渡口的秘密。\n\n> 守住人物动机。\n\n`<img src=x onerror="globalThis.__moyuChatXss=1">`\n\n<script>globalThis.__moyuChatXss=2</script>\n\n[危险链接](javascript:globalThis.__moyuChatXss=3)';
    Object.assign(fixture.action, {title: '暗河来信的故事方向',
      content: '## 灵感草稿\n\n一封来自失踪父亲的信，指向没有出现在地图上的渡口。'});
    await page.locator('[data-view="memory"]').click();
    await selectChat(page, fixture);
    const article = page.locator('[data-chat-message="' + message.id + '"]');
    const body = article.locator('.agent-chat-message-body');
    assert.equal(await body.locator('h4').textContent(), '暗河中的来信');
    assert.equal(await body.locator('strong').textContent(), '核心冲突');
    assert.equal(await body.locator('ol li').count(), 2);
    assert.equal(await body.locator('blockquote').textContent(), '守住人物动机。');
    assert.equal(await body.locator('code').textContent(), '<img src=x onerror="globalThis.__moyuChatXss=1">');
    assert.equal(await body.locator('script,img,a,iframe').count(), 0);
    assert.equal(await page.evaluate(() => globalThis.__moyuChatXss || 0), 0);
    const before = requests.filter(row => row.method === 'POST').length;
    await page.locator('#agentChatInput').fill('保留我自己的追问。');
    await article.locator('[data-chat-followup]').click();
    assert.ok((await page.locator('#agentChatInput').inputValue()).startsWith('保留我自己的追问。\n\n关于你提到的'));
    assert.equal(requests.filter(row => row.method === 'POST').length, before);
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {configurable: true,
        value: {writeText: async text => {globalThis.__moyuCopiedReply = text;}}});
    });
    await article.locator('[data-chat-copy]').click();
    await until(() => page.evaluate(() => typeof globalThis.__moyuCopiedReply === 'string'), 'copied reply');
    assert.equal(await page.evaluate(() => globalThis.__moyuCopiedReply), message.content);
    assert.equal(requests.filter(row => row.method === 'POST').length, before);
    report.inspiration_fixture = {session_id: fixture.session.id, message_id: message.id, action_id: fixture.action.id};
  });

  await test('灵感保存无须来源章节，原卡显示保存状态并保留当前页面', async () => {
    const data = workspace('default-project', 'main'), current = report.inspiration_fixture;
    const card = page.locator('[data-chat-action="' + current.action_id + '"]');
    assert.equal(await card.locator('[data-chat-confirm]').textContent(), '保存为灵感');
    await card.locator('.agent-chat-inspiration summary').click();
    assert.match(await card.locator('.agent-chat-inspiration .agent-chat-markdown').textContent(), /失踪父亲/);
    const before = writingRequests.filter(row => row.method === 'POST').length;
    await card.locator('[data-chat-confirm]').click();
    await card.getByText('已保存为灵感草稿', {exact: true}).waitFor();
    assert.equal(data.savedInspirations.size, 1);
    assert.equal(await page.evaluate(() => view), 'memory');
    assert.equal(writingRequests.filter(row => row.method === 'POST').length, before);
    assert.equal(await card.locator('[data-chat-start]').count(), 0);
    assert.equal(await card.locator('[data-chat-open-result]').textContent(), '查看灵感');
    await card.locator('[data-chat-open-result]').click();
    await page.waitForFunction(() => view === 'world');
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
  });

  await test('上一轮参考范围准确区分作品资料、整章正文和正文引用', async () => {
    await page.locator('#agentChatIncludeChapter').uncheck();
    await sent(page, '只根据作品资料继续讨论，不带入整章。');
    const summary = page.locator('#agentChatContextSummary');
    await summary.waitFor();
    assert.equal(await summary.textContent(), '上一轮参考：作品资料');
    const payload = requests.filter(row => row.suffix.endsWith('/messages')).at(-1).body;
    assert.equal(payload.include_chapter, false);
    assert.deepEqual(payload.references, []);
    const data = workspace('default-project', 'main'), current = report.inspiration_fixture;
    data.summaries.set(current.session_id, {project_materials_included: true, chapter_included: true,
      chapter_title: firstTitle, reference_count: 2});
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    await until(() => summary.textContent().then(value => value.includes(firstTitle) && value.includes('2处正文引用')), 'reference summary');
    await page.locator('[data-chat-new]').click();
    assert.equal(await summary.isHidden(), true);
  });

  await test('历史格式错误恢复为文字后可阅读追问，不再重复展示格式错误', async () => {
    const data = workspace('default-project', 'main'), current = report.inspiration_fixture;
    const session = data.sessions.find(row => row.id === current.session_id);
    const message = {id: crypto.randomUUID(), role: 'assistant', content: '## 人物灵感\n\n主角最想保护的，恰好是她最大的弱点。',
      status: 'failed', response_recovered: true, created_at: timestamp(), actions: []};
    data.messages.get(session.id).push(message);
    data.runs.set('ui_recovered_reply', {id: 'ui_recovered_reply', session_id: session.id,
      status: 'failed', error: '模型回复不是要求的 JSON 格式'});
    await page.locator('[data-chat-history]').click();
    await page.locator('[data-chat-session="' + session.id + '"]').click();
    const article = page.locator('[data-chat-message="' + message.id + '"]');
    await article.waitFor();
    assert.equal(await article.locator('.agent-chat-message-state').textContent(), '已恢复为文字，未执行任何操作');
    assert.equal(await article.locator('[data-chat-followup]').count(), 1);
    assert.equal(await article.locator('[data-chat-copy]').count(), 1);
    assert.equal(await article.locator('[data-chat-action]').count(), 0);
    assert.equal(await page.locator('#agentChatFeed').getByText('模型回复不是要求的 JSON 格式', {exact: true}).count(), 0);
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
  });

  await test('发送后资料已更新仍保留完整回复，取消所有操作建议并可复制追问', async () => {
    const data = workspace('default-project', 'main'), current = report.inspiration_fixture;
    const session = data.sessions.find(row => row.id === current.session_id);
    const message = {id: crypto.randomUUID(), role: 'assistant',
      content: '## 渡口的秘密\n\n让主角在救出父亲与保护渡口之间选择。\n\n1. 先追查来信。\n2. 再确认船工的动机。',
      status: 'success', context_stale: true, created_at: timestamp(), actions: [
        {id: 'ui-stale-continue', kind: 'continue', label: '旧资料续写',
          instruction: '按发送时的资料续写。', chapter_id: firstId, status: 'confirmed',
          result: {writing_session_id: 'ui-stale-writing-task'}},
        {id: 'ui-stale-inspiration', kind: 'save_inspiration', label: '旧资料灵感',
          title: '已经过时的设定', content: '保留在回复中，不能直接执行。', status: 'proposed'},
      ]};
    data.messages.set(session.id, [message]);
    data.runs.set('ui_stale_reply', {id: 'ui_stale_reply', session_id: session.id, status: 'success'});
    const beforePosts = requests.filter(row => row.method === 'POST').length;
    const beforeWriting = writingRequests.length;
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    const article = page.locator('[data-chat-message="' + message.id + '"]');
    await article.waitFor();
    assert.equal(await article.locator('.agent-chat-message-state').textContent(),
      '回复依据是发送时的资料；资料已更新，本轮操作建议已取消。');
    assert.equal(await article.getByText('这条回复未完成', {exact: true}).count(), 0);
    assert.equal(await article.locator('.agent-chat-message-body h4').textContent(), '渡口的秘密');
    assert.equal(await article.locator('[data-chat-action],[data-chat-confirm],[data-chat-start]').count(), 0);
    assert.equal(await article.locator('[data-chat-followup]').count(), 1);
    assert.equal(await article.locator('[data-chat-copy]').count(), 1);
    await page.locator('#agentChatInput').fill('请依据最新人物设定继续。');
    await article.locator('[data-chat-followup]').click();
    assert.ok((await page.locator('#agentChatInput').inputValue()).startsWith('请依据最新人物设定继续。\n\n关于你提到的'));
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {configurable: true,
        value: {writeText: async text => {globalThis.__moyuCopiedStaleReply = text;}}});
    });
    await article.locator('[data-chat-copy]').click();
    await until(() => page.evaluate(() => typeof globalThis.__moyuCopiedStaleReply === 'string'), 'copied stale reply');
    assert.equal(await page.evaluate(() => globalThis.__moyuCopiedStaleReply), message.content);
    assert.equal(requests.filter(row => row.method === 'POST').length, beforePosts);
    assert.equal(writingRequests.length, beforeWriting);
    await page.setViewportSize({width: 1280, height: 900});
    await article.scrollIntoViewIfNeeded();
    const screenshot = path.join(temporary, 'agent-chat-context-updated-1280x900.png');
    await page.screenshot({path: screenshot}); report.screenshots.push(screenshot);
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
  });

  assert.deepEqual(report.page_errors, [], '全部用例的页面异常都必须为空');
  report.passed = true;
  await context.close();
}
main().catch(error => {
  report.error = error.stack; report.server_log = log.slice(-3000); process.exitCode = 1;
}).finally(async () => {
  for (const gate of routeGates) gate.release();
  await bounded(Promise.allSettled(routeGates.filter(gate => gate.used).map(gate => gate.finished)),
    'held route cleanup').catch(() => {});
  if (browser) await browser.close();
  if (app && app.exitCode === null) {
    const exited = new Promise(resolve => app.once('exit', resolve)); app.kill('SIGTERM'); await exited;
  }
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
