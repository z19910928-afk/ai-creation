'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-agent-chat-flow-'));
const report = {passed: false, scope: '真实桌面 Chrome、真实聊天和写作接口、隔离数据库、免费回环模拟模型；验证作者操作链路，不评价文学质量。',
  cases: [], screenshots: [], page_errors: [], paid_provider_calls: 0, artifacts: temporary};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const providerRequests = [];
const prose = '林岚把手从门缝前收回，将铜钥匙平放在门槛上。她向后退了一步，等守卫自己作出选择。\n\n门里响起金属落地的轻声。守卫低下头，终于看见了钥匙齿间没有擦净的红泥。';
const revised = '她把铜钥匙留在门槛上，掌心收拢，退到了守卫能够看清的地方。';
const multilineReply = '## 方向三：渡口的最后一盏灯\n\n林岚决定先向船工问清铜钥匙的来历。\n\n- 保留守卫的秘密\n- 让第一次选择带来代价';
const multilineInspiration = '林岚带着铜钥匙来到渡口，船工却认出了门槛上的红泥。\n\n- 核心冲突：救出失踪者，还是守住唯一的开门机会。\n- 开篇动作：先灭掉渡口最后一盏灯。';
let app, provider, browser, base, port, providerPort, log = '', chatDelay = 50;
let holdChat = false, chatRelease = null;
let firstId, selectedQuote;

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const result = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return result;
}
async function until(fn, label) {
  for (let index = 0; index < 240; index += 1) {
    if (app && app.exitCode !== null) throw new Error('Server exited: ' + log.slice(-2500));
    const value = await fn();
    if (value) return value;
    await pause(100);
  }
  throw new Error('Timed out: ' + label + '\n' + log.slice(-2000));
}
async function api(route, method = 'GET', body, project = 'default-project') {
  const response = await fetch(base + route, {method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}
async function startApp() {
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {cwd: root,
    env: {...process.env, MOYU_DB: path.join(temporary, 'story.sqlite3'), MOYU_HOST: '127.0.0.1',
      MOYU_PORT: String(port), MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`,
      MOYU_AI_MODEL: 'agent-chat-flow-local', MOYU_AI_API_KEY: '', MOYU_AI_DAILY_QUOTA: '100',
      MOYU_SECRET_KEY: '', MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials')},
    stdio: ['ignore', 'ignore', 'pipe']});
  app.stderr.on('data', chunk => {log += chunk.toString();});
  await until(async () => {try {return (await fetch(base + '/api/health')).ok;} catch {return false;}}, 'startup');
}
async function stopApp() {
  if (app && app.exitCode === null) {
    const exited = new Promise(resolve => app.once('exit', resolve)); app.kill('SIGTERM'); await exited;
  }
}
function output(input) {
  const system = input.messages[0].content;
  const user = [...input.messages].reverse().find(row => row.role === 'user').content;
  const material = JSON.parse(user.split('<manuscript>\n')[1].split('\n</manuscript>')[0]);
  if (system.includes('你是墨屿的写作总管')) {
    const question = user.split('作者消息：')[1];
    if (question.includes('自然语言回复')) return {text: '## 人物的选择\n\n让林岚先决定是否相信守卫。\n\n- 保留疑点\n- 给出代价', chat: true};
    if (question.includes('多行方案')) {
      const text = JSON.stringify({reply: multilineReply,
        actions: [{kind: 'save_inspiration', label: '保存方向三的创作方案',
          instruction: '保留为作者灵感，等待后续创作确认。',
          title: '渡口的最后一盏灯', content: multilineInspiration}]}).replace(/\\n/g, '\n');
      assert.throws(() => JSON.parse(text), SyntaxError, '模拟模型必须包含未转义的字符串换行');
      return {text, chat: true};
    }
    if (question.includes('保存为灵感')) return {text: JSON.stringify({
      reply: '这个方向可以先存成灵感，后续再接入创作规划。',
      actions: [{kind: 'save_inspiration', label: '保存铜钥匙的秘密',
        instruction: '把讨论方案保留为作者灵感，不写入已发生事实。',
        title: '铜钥匙的秘密', content: '铜钥匙只能开门一次。林岚必须先选择救人，还是保住最后一次开门的机会。'}]
    }), chat: true};
    const actions = question.includes('选区改写') ?
      [{kind: 'polish', label: '改写这处动作', instruction: '只改写引用中的动作，保留位置和人物意图。',
        reference_index: 0}] : question.includes('建议续写') ?
        [{kind: 'continue', label: '继续门外冲突', instruction: '续写门外冲突，保留林岚视角。'}] : [];
    return {text: JSON.stringify({reply: actions.length ? '我会保留门外的位置关系。你可以确认下面这一步。' :
      '林岚还站在门外，钥匙位于门槛上。当前原稿尚未说明守卫知道钥匙来历。', actions}), chat: true};
  }
  if (system.includes('完整普通正文')) return {text: JSON.stringify({
    summary: {text: '林岚仍在门外等待守卫。', evidence: [{source_quote: material.current_chapter.body.split('\n')[0]}]},
    handoff: {text: '', evidence: []}, candidates: []})};
  if (system.includes('人物动机、因果、时间')) return {text: JSON.stringify({
    findings: [], uncertainties: ['本机固定回复，仅验证作者流程。']})};
  return {text: material.current_chapter.body === selectedQuote ? revised : prose};
}
async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn(); report.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, passed: true}));
}
async function screenshot(page, name) {
  const file = path.join(temporary, name); await page.screenshot({path: file});
  report.screenshots.push(file);
}
async function saved(page) {
  await page.waitForFunction(() => backendOnline && !isDirty() && !syncInFlight && !saveError && !mergePending);
}
async function send(page, text) {
  const previous = await page.locator('.agent-chat-message[data-role="assistant"]').count();
  await page.locator('#agentChatInput').fill(text); await page.locator('#agentChatSend').click();
  await until(() => page.locator('.agent-chat-message[data-role="assistant"]').count().then(count =>
    count > previous), 'assistant reply');
  await page.locator('#agentChatSend[data-stopping="false"]').waitFor();
}
async function action(page, kind) {
  await send(page, kind === 'polish' ? '请给出选区改写步骤。' : '请建议续写门外冲突。');
  const element = page.locator('[data-chat-action]').last(); await element.waitFor();
  return {id: await element.getAttribute('data-chat-action'), element};
}
async function main() {
  port = await freePort(); providerPort = await freePort(); base = `http://127.0.0.1:${port}`;
  provider = http.createServer((req, res) => {
    let raw = ''; req.on('data', chunk => {raw += chunk.toString();});
    req.on('end', () => {
      try {
        const input = JSON.parse(raw), result = output(input), delay = result.chat ? chatDelay : 40;
        providerRequests.push({chat: !!result.chat, input});
        res.writeHead(200, {'Content-Type': 'text/event-stream'});
        res.write(`data: ${JSON.stringify({model: 'agent-chat-flow-local',
          choices: [{delta: {content: result.text.slice(0, 12)}, finish_reason: null}]})}\n\n`);
        const finish = () => {
          if (res.destroyed) return;
          res.write(`data: ${JSON.stringify({model: 'agent-chat-flow-local',
            choices: [{delta: {content: result.text.slice(12)}, finish_reason: 'stop'}],
            usage: {prompt_tokens: 140, completion_tokens: 80, total_tokens: 220}})}\n\n`);
          res.end('data: [DONE]\n\n');
        };
        if (result.chat && holdChat) chatRelease = finish;
        else setTimeout(finish, delay);
      } catch (error) {
        res.writeHead(500, {'Content-Type': 'application/json'}); res.end(JSON.stringify({error: error.message}));
      }
    });
  });
  await new Promise(resolve => provider.listen(providerPort, '127.0.0.1', resolve)); await startApp();
  const initial = await api('/api/state'); firstId = initial.chapters[0].id;
  initial.chapters[0].body = '林岚站在门外，她把铜钥匙留在门槛上。\n\n守卫没有回答。';
  const secondId = 'chat_flow_other_chapter';
  initial.chapters.push({...initial.chapters[0], id: secondId, title: '第二章 远处的渡口',
    body: '渡口的船工正在收起灯笼。'});
  await api('/api/state', 'PUT', initial);
  const initialBody = initial.chapters[0].body;
  const other = await api('/api/projects', 'POST', {title: '真实聊天隔离作品'});
  const otherId = other.project?.id || other.state?.project?.id;
  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  await page.goto(base); await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);
  await page.evaluate(() => authorSwitchProject('default-project'));
  await page.locator('[data-view="editor"]').click();

  await test('持续对话通过真实聊天服务保存，普通讨论不创建写作任务', async () => {
    await send(page, '林岚现在在哪里，守卫知道什么？');
    assert.match(await page.locator('.agent-chat-message[data-role="assistant"]').last().textContent(), /门外/);
    assert.equal((await api('/api/agent-chat/sessions')).sessions.length, 1);
    assert.equal((await api('/api/writing-agent')).sessions.length, 0);
    assert.equal((await api('/api/state')).chapters[0].body, initialBody);
    const history = (await api('/api/ai/queue')).jobs;
    const chatJob = history.find(job => job.task === 'assistant_chat');
    assert.ok(chatJob, '聊天任务真实存在于持久队列中');
    assert.equal(chatJob.status, 'success');
    await page.evaluate(async () => {
      await refreshQueue();
      if (queueJobs.some(job => job.task === 'assistant_chat')) throw new Error('聊天进入通用任务列表');
      if ([...candidateStore.values()].some(item => item.task === 'assistant_chat')) throw new Error('聊天进入正文候选');
    });
    await page.locator('[data-view="memory"]').click();
    assert.equal(await page.locator('.agent-chat-message').count(), 2);
    await page.reload(); await page.waitForFunction(() => backendOnline);
    await until(() => page.locator('.agent-chat-message').count().then(count => count === 2), 'saved chat after reload');
    await page.locator('[data-view="editor"]').click();
    await pause(1200);
    assert.equal(await page.evaluate(() => aiDraft?.task || null), null);
    assert.equal(await page.locator('#aiOutput').inputValue(), '');
    await screenshot(page, 'agent-chat-discussion-1440x1000.png');

    await page.evaluate(job => {
      const legacy = {...job.result, ai_run_id: job.ai_run_id, job_id: job.id, task: job.task,
        source: job.source, text: JSON.stringify({reply: '旧版聊天缓存', actions: []}),
        complete: true, decision: 'pending'};
      localStorage.setItem(cacheKey(':candidates'), JSON.stringify([legacy]));
      localStorage.setItem(cacheKey(':candidate-open'), JSON.stringify({run_id: legacy.ai_run_id, ...legacy.source}));
    }, chatJob);
    await page.reload(); await page.waitForFunction(() => backendOnline);
    await page.locator('[data-view="editor"]').click();
    await page.evaluate(async () => {await refreshQueue();});
    assert.equal(await page.evaluate(() => aiDraft?.task || null), null);
    assert.equal(await page.locator('#aiOutput').inputValue(), '');
    assert.equal(await page.locator('#aiAdopt').isDisabled(), true);
    await page.evaluate(() => {
      if ([...candidateStore.values()].some(item => item.task === 'assistant_chat')) throw new Error('旧聊天缓存未清理');
      const cached = JSON.parse(localStorage.getItem(cacheKey(':candidates')) || '[]');
      if (cached.some(item => item.task === 'assistant_chat')) throw new Error('旧聊天缓存仍保存在浏览器中');
    });
    for (const id of [secondId, firstId]) {
      await page.locator(`.workbench-library [data-chapter="${id}"]`).click();
      await page.evaluate(async () => {await refreshQueue();});
      assert.equal(await page.locator('#aiOutput').inputValue(), '');
      assert.equal(await page.evaluate(() => aiDraft?.task || null), null);
    }
    assert.equal((await api('/api/state')).chapters[0].body, initialBody);
  });

  await test('真实模型接口返回普通文字时正常排版，不产生虚构操作', async () => {
    await send(page, '请用自然语言回复，给我一个人物选择。');
    const reply = page.locator('.agent-chat-message[data-role="assistant"]').last();
    assert.equal(await reply.locator('.agent-chat-message-state').count(), 0);
    assert.equal(await reply.locator('h4').first().textContent(), '人物的选择');
    assert.equal(await reply.locator('li').count(), 2);
    assert.equal(await reply.locator('[data-chat-action]').count(), 0);
    assert.equal((await api('/api/ai/queue')).jobs.find(job => job.task === 'assistant_chat').status, 'success');
    assert.equal((await api('/api/state')).chapters[0].body, initialBody);
  });

  await test('不带章节也能讨论作品并确认保存灵感，重复点击只保存一次', async () => {
    await page.locator('#agentChatIncludeChapter').uncheck();
    await send(page, '请把铜钥匙只能开一次门这个方向保存为灵感。');
    const proposal = page.locator('[data-chat-action]').last();
    const actionId = await proposal.getAttribute('data-chat-action');
    const before = (await api('/api/state')).records.length;
    assert.equal((await api('/api/state')).chapters[0].body, initialBody);
    const lastRequest = providerRequests.filter(row => row.chat).at(-1).input;
    const lastMaterial = JSON.parse(lastRequest.messages.at(-1).content.split('<manuscript>\n')[1].split('\n</manuscript>')[0]);
    assert.equal(lastMaterial.project.title, initial.project.title);
    assert.equal(lastMaterial.chapter_context_included, false);
    assert.ok(!JSON.stringify(lastMaterial).includes(initialBody), '不勾选正文时没有暗中发送章节内容');
    await proposal.locator('[data-chat-confirm]').click();
    await until(async () => (await api('/api/state')).records.length === before + 1, 'inspiration saved');
    const record = (await api('/api/state')).records.find(row => row.name === '铜钥匙的秘密');
    assert.equal(record.type, '灵感');
    assert.equal(record.epistemic_mode, 'author_plan');
    assert.equal(await page.evaluate(() => view), 'editor', '保存后保留当前创作页面');
    const calls = providerRequests.length;
    const repeated = await api('/api/agent-chat/actions/' + actionId + '/confirm', 'POST', {confirm: true});
    assert.equal(repeated.idempotent, true);
    assert.equal(repeated.action.result.record_id, record.id);
    assert.equal((await api('/api/state')).records.length, before + 1);
    assert.equal(providerRequests.length, calls);
    assert.equal((await api('/api/state')).chapters[0].body, initialBody);
    assert.equal((await api('/api/writing-agent')).sessions.length, 0);
    await screenshot(page, 'agent-chat-inspiration-saved-1440x1000.png');
    await proposal.locator('[data-chat-open-result]').click();
    await page.waitForFunction(() => view === 'world');
    await page.getByText('铜钥匙的秘密', {exact: true}).first().waitFor();
    await page.locator('[data-view="editor"]').click();
    await page.locator('#agentChatIncludeChapter').check();
  });

  await test('完整多行JSON中的原始换行正常排版并保存灵感，确认与刷新不重复生成', async () => {
    const before = await api('/api/state'), callsBefore = providerRequests.length;
    const tasksBefore = (await api('/api/writing-agent')).sessions.length;
    await send(page, '请根据方向三开始我的创作，先给我多行方案。');
    assert.equal(providerRequests.length, callsBefore + 1, '作者消息只生成一轮回复');
    const reply = page.locator('.agent-chat-message[data-role="assistant"]').last();
    const body = reply.locator('.agent-chat-message-body');
    assert.equal(await body.locator('h4').textContent(), '方向三：渡口的最后一盏灯');
    assert.equal(await body.locator('li').count(), 2);
    assert.match(await body.textContent(), /林岚决定先向船工问清铜钥匙的来历/);
    assert.equal(await reply.locator('.agent-chat-message-state').count(), 0);
    assert.equal(await reply.locator('[data-chat-copy]').count(), 1);
    assert.equal(await reply.locator('[data-chat-followup]').count(), 1);
    const proposal = reply.locator('[data-chat-action]');
    const actionId = await proposal.getAttribute('data-chat-action');
    const session = (await api('/api/agent-chat/sessions')).sessions[0];
    const bundle = await api('/api/agent-chat/sessions/' + session.id);
    assert.equal(bundle.active_run.status, 'success');
    assert.equal(bundle.messages.at(-1).content, multilineReply);
    assert.equal(bundle.messages.at(-1).actions[0].content, multilineInspiration);
    assert.equal((await api('/api/state')).records.length, before.records.length);
    await proposal.locator('[data-chat-confirm]').click();
    await until(async () => (await api('/api/state')).records.length === before.records.length + 1,
      'raw multiline inspiration saved');
    const record = (await api('/api/state')).records.find(row => row.name === '渡口的最后一盏灯');
    assert.equal(record.type, '灵感');
    assert.equal(record.epistemic_mode, 'author_plan');
    assert.equal(record.description, multilineInspiration, '灵感原文保留段落和换行');
    await proposal.getByText('已保存为灵感草稿', {exact: true}).waitFor();
    const repeated = await api('/api/agent-chat/actions/' + actionId + '/confirm', 'POST', {confirm: true});
    assert.equal(repeated.idempotent, true);
    assert.equal(repeated.action.result.record_id, record.id);
    assert.equal((await api('/api/state')).records.length, before.records.length + 1);
    assert.equal((await api('/api/writing-agent')).sessions.length, tasksBefore);
    assert.equal((await api('/api/state')).chapters[0].body, before.chapters[0].body);
    assert.equal(providerRequests.length, callsBefore + 1, '保存和重复确认不会再调用模型');
    await page.reload(); await page.waitForFunction(() => backendOnline);
    await page.locator('[data-view="editor"]').click();
    const restored = page.locator('[data-chat-action="' + actionId + '"]');
    await restored.getByText('已保存为灵感草稿', {exact: true}).waitFor();
    await restored.locator('summary').click();
    assert.equal(await restored.locator('.agent-chat-inspiration li').count(), 2);
    assert.equal(await restored.locator('[data-chat-confirm]').count(), 0);
    assert.equal(await page.locator('.agent-chat-message[data-role="assistant"]').last()
      .locator('.agent-chat-message-state').count(), 0);
    assert.equal(providerRequests.length, callsBefore + 1, '刷新只恢复已保存回复');
    await restored.locator('[data-chat-open-result]').scrollIntoViewIfNeeded();
    await screenshot(page, 'agent-chat-raw-multiline-inspiration-1440x1000.png');
  });

  await test('聊天原卡显示准确任务与调用上限，作者手动启动和采纳后再接续章后流程', async () => {
    await page.locator('[data-view="editor"]').click();
    const proposal = await action(page, 'continue');
    assert.equal((await api('/api/writing-agent')).sessions.length, 0);
    await page.locator('[data-view="memory"]').click();
    const callsBefore = providerRequests.length;
    await proposal.element.locator('[data-chat-confirm]').click();
    const card = page.locator('[data-chat-action="' + proposal.id + '"]');
    await card.locator('[data-chat-start]:not([disabled])').waitFor();
    const row = (await api('/api/writing-agent')).sessions[0];
    assert.equal(row.mode, 'continue'); assert.equal(row.status, 'awaiting_authorization');
    assert.equal(await page.evaluate(() => view), 'memory');
    assert.equal(await card.locator('[data-chat-writing-session]').getAttribute('data-chat-writing-session'), row.id);
    assert.equal(await card.locator('[data-chat-task-status]').getAttribute('data-chat-task-status'), 'awaiting_authorization');
    assert.equal(await card.locator('[data-chat-task-source]').textContent(), '来源章 · ' + initial.chapters[0].title);
    assert.match(await card.locator('.agent-chat-task-budget').textContent(), /0\s*\/\s*上限 3 次/);
    assert.equal(providerRequests.length, callsBefore, '确认与读取状态不会调用模型');
    assert.equal(await page.locator('#workbenchAgentSection').count(), 0, '确认后保留原页面');
    for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 900}]) {
      await page.setViewportSize(viewport);
      await card.locator('[data-chat-start]').scrollIntoViewIfNeeded();
      const dimensions = await card.evaluate(element => {
        const rect = element.getBoundingClientRect();
        return {width: innerWidth, scroll: document.documentElement.scrollWidth, left: rect.left, right: rect.right,
          content: element.scrollWidth, available: element.clientWidth};
      });
      assert.ok(dimensions.scroll <= dimensions.width && dimensions.left >= 0 && dimensions.right <= dimensions.width &&
        dimensions.content <= dimensions.available, JSON.stringify(dimensions));
      await screenshot(page, `agent-chat-inline-task-${viewport.width}x${viewport.height}.png`);
    }
    await page.setViewportSize({width: 1440, height: 1000});
    await card.locator('[data-chat-start]').click();
    assert.equal(await page.evaluate(() => view), 'memory', '手动启动后继续留在聊天');
    await until(async () => (await api('/api/writing-agent')).sessions.find(item => item.id === row.id)?.status ===
      'awaiting_adoption', 'prose candidate');
    await card.locator('[data-chat-task-status="awaiting_adoption"]').waitFor();
    assert.equal(providerRequests.length, callsBefore + 1, '手动启动仅生成当前正文候选');
    assert.equal((await api('/api/state')).chapters[0].body, initialBody);
    await card.locator('[data-chat-open-result]').click();
    await page.locator('#workbenchCandidateSection:visible').waitFor();
    assert.equal(await page.locator('#agentSessions [data-agent-session]').count(), 1);
    assert.equal(await page.locator('#agentSessions [data-agent-session]').getAttribute('data-agent-session'), row.id);
    assert.equal(await page.locator('#aiOutput').inputValue(), prose);
    await page.locator('#aiAdopt:not([disabled])').waitFor(); await page.locator('#aiAdopt').click();
    await page.locator('.candidate-columns').waitFor(); await screenshot(page, 'agent-chat-compare-1440x1000.png');
    await page.locator('[data-apply]').click(); await saved(page);
    const waiting = await until(async () => {
      const current = (await api('/api/writing-agent')).sessions.find(item => item.id === row.id);
      return current?.status === 'awaiting_memory' && current;
    }, 'chapter memory author decisions');
    assert.equal(waiting.calls_used, 2);
    assert.equal((await api('/api/story-reviews?chapter_id=' + firstId)).reviews.length, 0);
    await page.locator('[data-chat-action="' + proposal.id + '"] [data-chat-open-result]').click();
    await page.locator(`[data-agent-session="${row.id}"] [data-agent-analysis]`).click();
    assert.equal(await page.locator('[data-author-chapter-report]').getAttribute('data-analysis-id'),
      waiting.result_refs.chapter_analysis.id);
    assert.equal(await page.locator('#analysisSummaryDecision').inputValue(), '');
    await page.locator('#analysisSummaryDecision').selectOption('accepted');
    await page.locator('[data-confirm-analysis]').click();
    await page.locator('.analysis-modal').waitFor({state: 'hidden'});
    await until(async () => (await api('/api/writing-agent')).sessions.find(item => item.id === row.id)?.status ===
      'review_ready', 'chapter memory and review');
    assert.ok((await api('/api/state')).chapters[0].body.endsWith(prose));
    assert.equal((await api('/api/chapter-analysis?chapter_id=' + firstId)).analyses.length, 1);
    assert.equal((await api('/api/story-reviews?chapter_id=' + firstId)).reviews.length, 1);
    const calls = providerRequests.length;
    const repeated = await api('/api/agent-chat/actions/' + proposal.id + '/confirm', 'POST', {confirm: true});
    assert.equal(repeated.idempotent, true); assert.equal((await api('/api/writing-agent')).sessions.length, 1);
    assert.equal(providerRequests.length, calls);
  });

  await test('选区修订回到原章比较，仅替换引用内容并保留其余正文', async () => {
    const current = (await api('/api/state')).chapters[0].body;
    selectedQuote = '她把铜钥匙留在门槛上。';
    await page.locator('[data-workbench-results-close]').click();
    await page.locator('#chapterBody').evaluate((input, quote) => {
      const start = input.value.indexOf(quote); input.focus(); input.setSelectionRange(start, start + quote.length);
      input.dispatchEvent(new Event('select'));
    }, selectedQuote);
    await page.locator('[data-agent-chat-quote]').click();
    await page.locator('[data-chat-reference]').waitFor();
    const proposal = await action(page, 'polish');
    await proposal.element.locator('[data-chat-confirm]').click();
    await page.locator('#workbenchCandidateSection:visible').waitFor();
    await page.waitForFunction(() => aiDraft?.task === 'polish' && aiDraft.complete);
    await page.evaluate(async () => {await refreshQueue();});
    assert.equal((await api('/api/state')).chapters[0].body, current);
    assert.equal(await page.locator('#aiOutput').inputValue(), revised);
    const runId = await page.evaluate(() => aiDraft.ai_run_id);
    await page.locator(`.workbench-library [data-chapter="${secondId}"]`).click();
    assert.equal(await page.locator('#aiOutput').inputValue(), '');
    await page.locator(`.workbench-library [data-chapter="${firstId}"]`).click();
    assert.equal(await page.evaluate(() => aiDraft?.ai_run_id), runId);
    assert.equal(await page.locator('#aiOutput').inputValue(), revised);
    await page.reload(); await page.waitForFunction(() => backendOnline);
    await page.locator('[data-view="editor"]').click();
    await page.waitForFunction(id => aiDraft?.ai_run_id === id && aiDraft.complete, runId);
    await page.evaluate(async () => {await refreshQueue();});
    assert.equal(await page.locator('#aiOutput').inputValue(), revised);
    await page.locator('[data-rail-section="workbenchCandidateSection"]').click();
    await page.locator('#aiAdopt:not([disabled])').click();
    assert.equal(await page.locator('.candidate-columns pre').first().textContent(), selectedQuote);
    await page.locator('[data-apply]').click(); await saved(page);
    await page.evaluate(async () => {await refreshQueue();});
    assert.notEqual(await page.evaluate(() => aiDraft?.task), 'assistant_chat');
    assert.doesNotMatch(await page.locator('#aiOutput').inputValue(), /"reply"|"actions"/);
    assert.equal((await api('/api/state')).chapters[0].body, current.replace(selectedQuote, revised));
    await screenshot(page, 'agent-chat-selection-applied-1440x1000.png');
  });

  await test('作者改稿后旧聊天建议被拒绝，不产生额外写作任务', async () => {
    await page.locator('[data-workbench-results-close]').click();
    const proposal = await action(page, 'continue');
    const before = await page.locator('#chapterBody').inputValue();
    await page.locator('#chapterBody').fill(before + '\n\n后来，作者加上了新的动作。'); await saved(page);
    const count = (await api('/api/writing-agent')).sessions.length;
    await proposal.element.locator('[data-chat-confirm]').click();
    await until(() => proposal.element.locator('[data-chat-action-error]').textContent(), 'stale proposal refused');
    assert.match(await proposal.element.locator('[data-chat-action-error]').textContent(), /变化|重新讨论/);
    assert.equal((await api('/api/writing-agent')).sessions.length, count);
  });

  await test('等待聊天时改稿仍保留完整回复，并取消过期建议和保留旧参考提示', async () => {
    const replies = await page.locator('.agent-chat-message[data-role="assistant"]').count();
    const beforeBody = await page.locator('#chapterBody').inputValue();
    const taskCount = (await api('/api/writing-agent')).sessions.length;
    holdChat = true;
    await page.locator('#agentChatInput').fill('请建议续写门外冲突，我会同时修改稿件。');
    await page.locator('#agentChatSend').click();
    await until(() => chatRelease, 'held chat provider entered');
    const editedBody = beforeBody + '\n\n作者写下：守卫已经把钥匙拾起。';
    await page.locator('#chapterBody').fill(editedBody);
    await saved(page);
    chatRelease(); chatRelease = null; holdChat = false;
    await until(() => page.locator('.agent-chat-message[data-role="assistant"]').count()
      .then(count => count === replies + 1), 'stale-source complete reply');
    await page.locator('#agentChatSend[data-stopping="false"]').waitFor();
    const reply = page.locator('.agent-chat-message[data-role="assistant"]').last();
    assert.match(await reply.locator('.agent-chat-message-body').textContent(), /保留门外的位置关系/);
    assert.match(await reply.locator('.agent-chat-message-state').textContent(), /发送时的资料.*操作建议已取消/);
    assert.equal(await reply.locator('[data-chat-action]').count(), 0);
    assert.equal(await reply.locator('[data-chat-copy]').count(), 1);
    assert.equal(await reply.locator('[data-chat-followup]').count(), 1);
    const session = (await api('/api/agent-chat/sessions')).sessions[0];
    const bundle = await api('/api/agent-chat/sessions/' + session.id);
    assert.equal(bundle.active_run.status, 'success');
    assert.equal(bundle.messages.at(-1).context_stale, true);
    assert.deepEqual(bundle.messages.at(-1).actions, []);
    assert.equal((await api('/api/writing-agent')).sessions.length, taskCount);
    assert.equal((await api('/api/state')).chapters[0].body, editedBody);
    await page.reload(); await page.waitForFunction(() => backendOnline);
    await page.locator('.agent-chat-message[data-role="assistant"]').last()
      .getByText('回复依据是发送时的资料；资料已更新，本轮操作建议已取消。', {exact: true}).waitFor();
    await screenshot(page, 'agent-chat-reply-after-manuscript-edit-1440x1000.png');
  });

  await test('停止迟到回复、重启会话恢复与切作品隔离均由真实服务处理', async () => {
    const baseline = (await api('/api/state')).chapters[0].body;
    chatDelay = 1800;
    const before = providerRequests.length;
    await page.locator('#agentChatInput').fill('这次先停止回复。'); await page.locator('#agentChatSend').click();
    await until(() => providerRequests.length > before, 'chat provider entered');
    await page.locator('#agentChatSend[data-stopping="true"]').click();
    await until(() => page.locator('#agentChatSend').getAttribute('data-stopping').then(value => value === 'false'),
      'chat stopped');
    await pause(2000); chatDelay = 50;
    assert.equal((await api('/api/state')).chapters[0].body, baseline);
    const sessions = (await api('/api/agent-chat/sessions')).sessions;
    const bundle = await api('/api/agent-chat/sessions/' + sessions[0].id);
    assert.equal(bundle.active_run.status, 'canceled');
    assert.equal(bundle.messages.at(-1).content, '');
    await stopApp(); await startApp(); await page.reload(); await page.waitForFunction(() => backendOnline);
    await until(() => page.locator('[data-chat-message]').count().then(count => count === bundle.messages.length),
      'chat restored after service restart');
    await page.evaluate(id => authorSwitchProject(id), otherId);
    assert.equal(await page.locator('[data-chat-message]').count(), 0);
    assert.equal((await api('/api/agent-chat/sessions', 'GET', undefined, otherId)).sessions.length, 0);
    await page.evaluate(() => authorSwitchProject('default-project'));
    await until(() => page.locator('[data-chat-message]').count().then(count => count === bundle.messages.length),
      'original project chat restored');
    assert.deepEqual(report.page_errors, []);
  });
  report.provider_calls = providerRequests.length;
  report.chat_calls = providerRequests.filter(row => row.chat).length;
  report.passed = true; await context.close();
}
main().catch(error => {
  report.error = error.stack; report.server_log = log.slice(-5000); process.exitCode = 1;
}).finally(async () => {
  if (chatRelease) chatRelease();
  if (browser) await browser.close(); await stopApp();
  if (provider) await new Promise(resolve => provider.close(resolve));
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
