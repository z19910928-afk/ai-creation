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
  scope: '真实桌面 Chrome 与隔离作品数据库；聊天响应由本机受控接口模拟，仅验证前端交互、请求身份与隔离，不证明模型或聊天后端可用。',
  branch_identity_test: 'Synthetic frontend branch identity; no branch-management UI claim.'};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const timestamp = () => new Date().toISOString();
const store = new Map(), requests = [];
let app, browser, base, log = '', interruptCreation = false, interruptMessage = false;
let holdMessage = null, delayedReply = false, failAction = false;

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
    creations: new Map(), sends: new Map()});
  return store.get(key);
}
function bundle(data, session) {
  const runs = [...data.runs.values()].filter(row => row.session_id === session.id);
  return {session, messages: data.messages.get(session.id) || [],
    active_run: runs.find(row => ['queued', 'running'].includes(row.status)) || runs.at(-1) || null};
}
async function chatRoute(route) {
  const request = route.request(), headers = request.headers();
  const project = headers['x-moyu-project'], branch = headers['x-moyu-branch'];
  const data = workspace(project, branch), pathname = new URL(request.url()).pathname;
  const suffix = pathname.slice('/api/agent-chat'.length), method = request.method();
  const body = method === 'GET' ? null : request.postDataJSON();
  const observation = {project, branch, suffix, method, body};
  requests.push(observation);
  const respond = value => route.fulfill({status: 200, contentType: 'application/json',
    body: JSON.stringify(value)});
  if (suffix === '/settings') return respond({settings: {mode: 'inherit'}});
  if (suffix === '/sessions' && method === 'GET') return respond({sessions: data.sessions});
  if (suffix === '/sessions' && method === 'POST') {
    let session = data.creations.get(body.request_id);
    if (!session) {
      session = {id: crypto.randomUUID(), project_id: project, branch_id: branch,
        title: '新对话', created_at: timestamp(), updated_at: timestamp()};
      data.sessions.unshift(session); data.messages.set(session.id, []);
      data.creations.set(body.request_id, session);
    }
    if (interruptCreation) {interruptCreation = false; return route.abort('failed');}
    return respond({session});
  }
  const segments = suffix.split('/').filter(Boolean);
  if (segments[0] === 'sessions') {
    const session = data.sessions.find(row => row.id === segments[1]);
    assert.ok(session, suffix);
    if (segments.length === 2 && method === 'GET') return respond(bundle(data, session));
    if (segments[2] === 'messages' && method === 'POST') {
      let result = data.sends.get(body.request_id);
      if (!result) {
        const messages = data.messages.get(session.id);
        messages.push({id: crypto.randomUUID(), role: 'user', content: body.content,
          references: body.references, created_at: timestamp()});
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
      return respond(bundle(data, session));
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
    return respond(bundle(data, session));
  }
  if (segments[0] === 'actions') {
    const message = [...data.messages.values()].flat().find(row =>
      row.actions?.some(action => action.id === segments[1]));
    assert.ok(message, suffix);
    const action = message.actions.find(row => row.id === segments[1]);
    if (failAction) return route.fulfill({status: 503, contentType: 'application/json',
      body: JSON.stringify({error: '本次操作尚未保存，请重试。'})});
    action.status = segments[2] === 'dismiss' ? 'dismissed' : 'confirmed';
    return respond({action});
  }
  throw new Error('Unexpected simulated chat route: ' + suffix);
}
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
  initial.chapters[0].body = '林岚站在门外。😀她把铜钥匙留在门槛上。\n\n守卫没有回答。';
  await api('/api/state', 'PUT', initial);
  const created = await api('/api/projects', 'POST', {title: '常驻聊天隔离作品'});
  const otherId = created.project?.id || created.state?.project?.id;
  const baseline = await api('/api/state');
  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  await page.route('**/api/agent-chat/**', chatRoute);
  await page.goto(base); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
  await page.evaluate(() => authorSwitchProject('default-project'));

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
        label: '恢复已确认的续写步骤', status: 'confirming', result: {}}]});
    const before = requests.filter(row => row.suffix.endsWith('/confirm')).length;
    await page.reload(); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
    const row = page.locator('[data-chat-action="ui-recover-confirmation"]');
    await row.getByText('恢复此步骤', {exact: true}).waitFor();
    assert.equal(await row.locator('[data-chat-dismiss]').count(), 0);
    assert.equal(await page.locator('#agentChatSend').getAttribute('data-stopping'), 'false');
    assert.match(await page.locator('#agentChatSaveState').textContent(), /尚未确认/);
    assert.equal(requests.filter(item => item.suffix.endsWith('/confirm')).length, before);
    await row.locator('[data-chat-confirm]').click();
    await row.getByText('已确认，处理结果已保存', {exact: true}).waitFor();
    assert.equal(requests.filter(item => item.suffix === '/actions/ui-recover-confirmation/confirm').length, 1);
    assert.deepEqual((await api('/api/state')).chapters, baseline.chapters);
    assert.deepEqual(report.page_errors, []);
  });
  report.passed = true;
  await context.close();
}
main().catch(error => {
  report.error = error.stack; report.server_log = log.slice(-3000); process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (app && app.exitCode === null) {
    const exited = new Promise(resolve => app.once('exit', resolve)); app.kill('SIGTERM'); await exited;
  }
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
