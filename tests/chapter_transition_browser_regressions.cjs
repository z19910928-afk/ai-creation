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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-chapter-transition-browser-'));
const report = {passed: false, cases: [], screenshots: [], desktop_layout: [], page_errors: [],
  scope: '真实电脑Chrome、真实聊天及章节接口、隔离数据库和免费回环模型；不评价文学质量。',
  paid_provider_calls: 0, artifacts: temporary};
const body = '林岚握住旧门的把手，冰冷的金属让她停了一瞬。\n'
  + '门里传来两下轻响，她没有立刻推门，而是把油灯放在了台阶上。\n'
  + '窗边的影子随即消失，潮湿的门缝中落下一张写着日期的船票。\n'
  + '她捡起船票，看见背面的字迹正是失踪兄长的笔迹。';
const plan = {chapter_goal: '辨认门里的声音', scene_plan: '门外试探，拿到船票，发现兄长线索',
  pov: '林岚', next_brief: '沿着船票上的日期寻找兄长', recap: '林岚在门外发现了兄长留下的船票。'};
const providerRequests = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, provider, browser, base, port, providerPort, log = '', projectIndex = 0;

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
    const result = await fn();
    if (result) return result;
    await pause(100);
  }
  throw new Error('Timed out: ' + label + '\n' + log.slice(-2000));
}

async function api(route, method = 'GET', data, project = 'default-project') {
  const response = await fetch(base + route, {method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json();
  assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(result)}`);
  return result;
}

async function saved(page) {
  await page.waitForFunction(() => backendOnline && !isDirty() && !syncInFlight && !saveError && !mergePending);
}

async function selectChapter(page, id) {
  await page.locator('[data-view="editor"]').click();
  await page.locator(`.workbench-library [data-chapter="${id}"]`).click();
  await page.waitForFunction(id => activeChapter === id, id);
  await saved(page);
}

async function bootstrap(page, chapters, title) {
  const index = ++projectIndex;
  const created = await api('/api/projects', 'POST', {title});
  const project = created.project?.id || created.state?.project?.id;
  assert.ok(project);
  const state = await api('/api/state', 'GET', undefined, project);
  const template = state.chapters[0] || {};
  state.chapters = chapters.map((chapter, position) => ({
    ...template, id: `transition_${index}_chapter_${position + 1}`,
    title: `第${position + 1}章 ${position ? '渡口' : '门外'}`,
    body, status: '进行中', ...plan, ...chapter
  }));
  state.memory = [];
  const stored = await api('/api/state', 'PUT', state, project);
  assert.notEqual(stored.saved, false);
  await page.evaluate(async metadata => {
    authorRememberProject(metadata); await authorSwitchProject(metadata.id); view = 'editor'; render();
  }, created.project || created.state.project);
  await page.waitForFunction(project => backendOnline && requestedProject === project, project);
  await selectChapter(page, state.chapters[0].id);
  return {project, chapters: (await api('/api/state', 'GET', undefined, project)).chapters};
}

async function proposal(page, kind = 'next_chapter') {
  const previous = await page.locator('.agent-chat-message[data-role="assistant"]').count();
  await page.locator('#agentChatInput').fill(kind === 'next_chapter' ?
    '请检查归档当前章并进入下一章。' : '请安排续写，给我一个确认步骤。');
  await page.locator('#agentChatSend').click();
  await until(async () => (await page.locator('.agent-chat-message[data-role="assistant"]').count()) > previous,
    'chat proposal');
  await page.locator('#agentChatSend[data-stopping="false"]').waitFor();
  const element = page.locator('[data-chat-action]').last();
  await element.waitFor();
  return {id: await element.getAttribute('data-chat-action'), element};
}

async function commits(project, id) {
  return (await api(`/api/chapters/${id}/commits`, 'GET', undefined, project)).commits;
}

async function screenshot(page, name) {
  const file = path.join(temporary, name);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await pause(150);
  const layout = await page.evaluate(() => ({
    width: innerWidth, height: innerHeight, scroll_width: document.documentElement.scrollWidth,
    panels: [...document.querySelectorAll('.modal, #agentChat')].map(element => {
      const rect = element.getBoundingClientRect();
      return {kind: element.matches('.modal') ? 'dialog' : 'chat', x: rect.x, y: rect.y,
        width: rect.width, height: rect.height};
    })
  }));
  assert.ok(layout.scroll_width <= layout.width + 1, JSON.stringify(layout));
  for (const panel of layout.panels) {
    assert.ok(panel.x >= -1 && panel.y >= -1 && panel.x + panel.width <= layout.width + 1 &&
      panel.y + panel.height <= layout.height + 1, JSON.stringify(panel));
  }
  report.desktop_layout.push({screenshot: name, ...layout});
  await page.screenshot({path: file, animations: 'disabled'});
  report.screenshots.push(file);
}

async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn();
  report.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, passed: true}));
}

async function startApp() {
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {cwd: root,
    env: {...process.env, MOYU_DB: path.join(temporary, 'story.sqlite3'), MOYU_HOST: '127.0.0.1',
      MOYU_PORT: String(port), MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`,
      MOYU_AI_MODEL: 'chapter-transition-local', MOYU_AI_API_KEY: '', MOYU_AI_DAILY_QUOTA: '100',
      MOYU_SECRET_KEY: '', MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials')},
    stdio: ['ignore', 'ignore', 'pipe']});
  app.stderr.on('data', chunk => {log += chunk.toString();});
  await until(async () => {try {return (await fetch(base + '/api/health')).ok;} catch {return false;}}, 'startup');
}

async function stopApp() {
  if (app && app.exitCode === null) {
    const exited = new Promise(resolve => app.once('exit', resolve));
    app.kill('SIGTERM');
    await exited;
  }
}

async function main() {
  port = await freePort(); providerPort = await freePort(); base = `http://127.0.0.1:${port}`;
  provider = http.createServer((request, response) => {
    let raw = ''; request.on('data', chunk => {raw += chunk.toString();});
    request.on('end', () => {
      try {
        const input = JSON.parse(raw);
        assert.ok(input.messages[0].content.includes('你是墨屿的写作总管'), 'only discussion calls are permitted');
        const user = input.messages.at(-1).content;
        const material = JSON.parse(user.split('<manuscript>\n')[1].split('\n</manuscript>')[0]);
        const kind = user.includes('请安排续写') ? 'continue' : 'next_chapter';
        const text = JSON.stringify({reply: kind === 'next_chapter' ?
          '可以先核对本章，再归档并打开下一章。' : '这一步先保留为待启动任务。',
        actions: [{kind, label: kind === 'next_chapter' ? '进入下一章' : '安排续写',
          instruction: kind === 'next_chapter' ? '检查当前章并打开下一章。' : '保持林岚视角，继续门外冲突。'}]});
        providerRequests.push({kind, chapter_id: material.current_chapter?.id});
        response.writeHead(200, {'Content-Type': 'text/event-stream'});
        response.write(`data: ${JSON.stringify({model: 'chapter-transition-local',
          choices: [{delta: {content: text}, finish_reason: 'stop'}],
          usage: {prompt_tokens: 100, completion_tokens: 70, total_tokens: 170}})}\n\n`);
        response.end('data: [DONE]\n\n');
      } catch (error) {
        response.writeHead(500, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({error: error.message}));
      }
    });
  });
  await new Promise(resolve => provider.listen(providerPort, '127.0.0.1', resolve));
  await startApp();
  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  await page.goto(base);
  await page.waitForFunction(() => backendOnline && globalThis.MoyuAgentChat);

  await test('聊天确认归档后打开已有下一章，原稿与全部任务字段保持一致', async () => {
    const fixture = await bootstrap(page, [{}, {
      body: '下一章已经写下的原文。', chapter_goal: '原有目标', scene_plan: '原有场景',
      pov: '兄长', hook: '原有悬念', next_brief: '原有交接', recap: '原有回顾'
    }], '已有下一章的作品');
    const [first, successor] = fixture.chapters;
    const action = await proposal(page);
    const calls = providerRequests.length;
    await action.element.locator('[data-chat-confirm]').click();
    await page.waitForFunction(id => activeChapter === id, successor.id);
    const state = await api('/api/state', 'GET', undefined, fixture.project);
    assert.equal(state.chapters[0].status, '完成');
    assert.equal(state.chapters[0].body, first.body);
    assert.equal(state.chapters[0].revision_id, first.revision_id);
    assert.deepEqual(state.chapters[1], successor);
    const history = await commits(fixture.project, first.id);
    assert.equal(history.length, 3);
    assert.ok(history.every(item => item.status === 'passed'));
    assert.equal(providerRequests.length, calls);
    await screenshot(page, 'chapter-transition-1440x1000.png');
  });

  await test('目录末章只建立一个空白新章，重复查看结果不重复归档或建章', async () => {
    const fixture = await bootstrap(page, [{}], '目录末章的作品');
    const action = await proposal(page);
    const calls = providerRequests.length;
    await action.element.locator('[data-chat-confirm]').click();
    await page.waitForFunction(id => activeChapter !== id && state.chapters.length === 2, fixture.chapters[0].id);
    const first = await api('/api/state', 'GET', undefined, fixture.project);
    const next = first.chapters[1];
    assert.equal(next.body, '');
    assert.equal(next.chapter_goal, '');
    assert.equal(next.scene_plan, '');
    assert.equal(next.pov, plan.pov);
    const history = await commits(fixture.project, fixture.chapters[0].id);
    for (let index = 0; index < 2; index += 1) {
      await page.locator(`[data-chat-action="${action.id}"] [data-chat-open-result]`).click();
      await page.waitForFunction(id => activeChapter === id, next.id);
    }
    assert.deepEqual((await api('/api/state', 'GET', undefined, fixture.project)).chapters, first.chapters);
    assert.deepEqual(await commits(fixture.project, fixture.chapters[0].id), history);
    assert.equal(providerRequests.length, calls);
    await page.setViewportSize({width: 1280, height: 900});
    await screenshot(page, 'chapter-transition-new-1280x900.png');
    await page.setViewportSize({width: 1440, height: 1000});
  });

  await test('本章记忆仍待决定时明确阻断，零半归档和零额外模型调用', async () => {
    const fixture = await bootstrap(page, [{}], '记忆待确认的作品');
    await api('/api/memory', 'POST', {type: '线索', name: '船票日期', status: 'candidate',
      source_chapter_id: fixture.chapters[0].id, source_quote: body.split('\n')[0],
      source_start: 0, source_end: Array.from(body.split('\n')[0]).length}, fixture.project);
    await page.evaluate(async () => {await hydrate(); render();});
    const action = await proposal(page);
    const before = await api('/api/state', 'GET', undefined, fixture.project);
    const calls = providerRequests.length;
    await action.element.locator('[data-chat-confirm]').click();
    await until(async () => /待决定|记忆/.test(await action.element.locator('[data-chat-action-error]').textContent()),
      'pending memory blocker');
    const after = await api('/api/state', 'GET', undefined, fixture.project);
    assert.deepEqual(after.chapters, before.chapters);
    assert.deepEqual(after.memory, before.memory);
    assert.deepEqual(await commits(fixture.project, fixture.chapters[0].id), []);
    assert.equal(providerRequests.length, calls);
  });

  await test('聊天建议后改稿会阻断旧步骤，原稿保留且不产生归档记录', async () => {
    const fixture = await bootstrap(page, [{}], '旧建议过期的作品');
    const action = await proposal(page);
    await page.locator('#chapterBody').fill(body + '\n作者补充了新的动作。');
    await saved(page);
    const before = await api('/api/state', 'GET', undefined, fixture.project);
    const calls = providerRequests.length;
    await action.element.locator('[data-chat-confirm]').click();
    await until(async () => /变化|当前正文/.test(await action.element.locator('[data-chat-action-error]').textContent()),
      'stale source blocker');
    assert.deepEqual((await api('/api/state', 'GET', undefined, fixture.project)).chapters, before.chapters);
    assert.deepEqual(await commits(fixture.project, fixture.chapters[0].id), []);
    assert.equal(providerRequests.length, calls);
  });

  await test('承接上章只补空字段，作者可编辑确认，取消与过期提交均保留原稿', async () => {
    const fixture = await bootstrap(page, [{}, {
      body: '渡口有作者已经写好的正文。', chapter_goal: '作者原定目标',
      scene_plan: '作者原定场景', pov: '船工'
    }], '承接上章的作品');
    const [previous, target] = fixture.chapters;
    await selectChapter(page, target.id);
    const open = async () => {
      if (!await page.locator('.chapter-plan').evaluate(element => element.open))
        await page.locator('.chapter-plan > summary').click();
      await page.locator('[data-action="prepareChapterHandoff"]').click();
      const modal = page.locator('[data-chapter-handoff]');
      await modal.waitFor();
      return modal;
    };
    let modal = await open();
    assert.equal(await modal.locator('#handoffGoal').inputValue(), target.chapter_goal);
    assert.equal(await modal.locator('#handoffScenes').inputValue(), target.scene_plan);
    assert.equal(await modal.locator('#handoffPov').inputValue(), target.pov);
    const beforeCancel = await api('/api/state', 'GET', undefined, fixture.project);
    await modal.locator('[data-close]').click();
    assert.deepEqual((await api('/api/state', 'GET', undefined, fixture.project)).chapters, beforeCancel.chapters);
    await api('/api/chapters/' + target.id, 'PATCH', {
      chapter_goal: '', scene_plan: '', pov: ''
    }, fixture.project);
    await page.evaluate(async () => {await hydrate(); render();});
    modal = await open();
    assert.equal(await modal.locator('#handoffGoal').inputValue(), previous.next_brief);
    assert.equal(await modal.locator('#handoffScenes').inputValue(), '');
    assert.equal(await modal.locator('#handoffPov').inputValue(), previous.pov);
    await modal.locator('#handoffGoal').fill('作者确认后改为寻找船工。');
    await modal.locator('#handoffScenes').fill('先观察渡口，再与船工交谈。');
    await modal.locator('#handoffPov').fill('林岚');
    await screenshot(page, 'chapter-handoff-confirm-1440x1000.png');
    await page.setViewportSize({width: 1280, height: 900});
    await screenshot(page, 'chapter-handoff-1280x900.png');
    await page.setViewportSize({width: 1440, height: 1000});
    await modal.locator('[type="submit"]').click();
    await modal.waitFor({state: 'detached'});
    await saved(page);
    const accepted = (await api('/api/state', 'GET', undefined, fixture.project)).chapters;
    assert.equal(accepted[1].chapter_goal, '作者确认后改为寻找船工。');
    assert.equal(accepted[1].scene_plan, '先观察渡口，再与船工交谈。');
    assert.equal(accepted[1].pov, '林岚');
    assert.equal(accepted[1].body, target.body);
    assert.equal(accepted[0].body, previous.body);
    modal = await open();
    await api('/api/chapters/' + previous.id, 'PATCH', {
      next_brief: '上章交接已在另一个窗口改变。'
    }, fixture.project);
    await modal.locator('#handoffGoal').fill('过期窗口里的目标，不能保存。');
    await modal.locator('[type="submit"]').click();
    await until(async () => /已经变化|重新打开/.test(await modal.locator('.form-error').textContent()), 'handoff conflict');
    assert.equal((await api('/api/state', 'GET', undefined, fixture.project)).chapters[1].chapter_goal,
      accepted[1].chapter_goal);
    await modal.locator('[data-close]').click();
    await page.evaluate(async () => {await hydrate(); render();});
    modal = await open();
    await page.evaluate(id => {activeChapter = id; view = 'editor'; render();}, previous.id);
    await modal.locator('[type="submit"]').click();
    await until(async () => /已切换/.test(await modal.locator('.form-error').textContent()), 'handoff scope guard');
    assert.equal((await api('/api/state', 'GET', undefined, fixture.project)).chapters[1].chapter_goal,
      accepted[1].chapter_goal);
    await modal.locator('[data-close]').click();
  });

  let focusFixture, focusAction, focusedTask;
  await test('同章超过三项任务后，旧聊天准确打开原任务，任务不存在时显示明确错误', async () => {
    focusFixture = await bootstrap(page, [{}, {body: '其他章节。'}], '聊天任务精确定位');
    focusAction = await proposal(page, 'continue');
    await focusAction.element.locator('[data-chat-confirm]').click();
    await page.locator('[data-agent-session]').waitFor();
    focusedTask = (await api('/api/writing-agent', 'GET', undefined, focusFixture.project)).sessions[0].id;
    for (let index = 0; index < 3; index += 1) {
      await api('/api/writing-agent', 'POST', {request_id: `newer_browser_task_${index}`,
        chapter_id: focusFixture.chapters[0].id, mode: 'continue', query: `更晚任务${index}`
      }, focusFixture.project);
    }
    await page.locator('[data-agent-return-all]').click();
    await until(async () => (await page.locator('[data-agent-session]').count()) === 4, 'all chapter tasks');
    const requests = [];
    const record = request => {
      if (request.url().includes('/api/writing-agent?session_id=')) requests.push(request.url());
    };
    page.on('request', record);
    await page.locator(`[data-chat-action="${focusAction.id}"] [data-chat-open-result]`).click();
    await until(async () => (await page.locator('[data-agent-session]').count()) === 1, 'precise task focus');
    assert.equal(await page.locator('[data-agent-session]').getAttribute('data-agent-session'), focusedTask);
    assert.ok(requests.some(url => new URL(url).searchParams.get('session_id') === focusedTask));
    page.off('request', record);
    await screenshot(page, 'chapter-chat-precise-result-1440x1000.png');
    await page.locator('[data-agent-return-all]').click();
    await until(async () => (await page.locator('[data-agent-session]').count()) === 4, 'leave focused task');
    const targetUrl = `**/api/writing-agent?session_id=${focusedTask}`;
    await page.route(targetUrl, route => route.fulfill({
      status: 404, contentType: 'application/json', body: JSON.stringify({error: '这条写作任务已经不存在，请重新打开。'})
    }));
    await page.locator(`[data-chat-action="${focusAction.id}"] [data-chat-open-result]`).click();
    await until(async () => /已经不存在/.test(await page.locator(
      `[data-chat-action="${focusAction.id}"] [data-chat-action-error]`).textContent()), 'missing task error');
    assert.equal(await page.evaluate(() => authorAgentFocus), null);
    await page.unroute(targetUrl);
  });

  await test('打开旧聊天结果期间切换章节或作品，不带入旧任务焦点', async () => {
    const targetUrl = `**/api/writing-agent?session_id=${focusedTask}`;
    let release, started;
    let gate = new Promise(resolve => {release = resolve;});
    let captured = new Promise(resolve => {started = resolve;});
    const delayed = async route => {started(); await gate; await route.continue();};
    await page.route(targetUrl, delayed);
    await page.locator(`[data-chat-action="${focusAction.id}"] [data-chat-open-result]`).click();
    await captured;
    await selectChapter(page, focusFixture.chapters[1].id);
    release(); await pause(350);
    assert.equal(await page.evaluate(() => activeChapter), focusFixture.chapters[1].id);
    assert.equal(await page.evaluate(() => authorAgentFocus), null);
    assert.equal(await page.locator(`[data-agent-session="${focusedTask}"]`).count(), 0);
    await page.unroute(targetUrl, delayed);
    await selectChapter(page, focusFixture.chapters[0].id);
    gate = new Promise(resolve => {release = resolve;});
    captured = new Promise(resolve => {started = resolve;});
    await page.route(targetUrl, delayed);
    await page.locator(`[data-chat-action="${focusAction.id}"] [data-chat-open-result]`).click();
    await captured;
    const created = await api('/api/projects', 'POST', {title: '焦点切换目标作品'});
    const other = created.project?.id || created.state?.project?.id;
    await page.evaluate(async metadata => {
      authorRememberProject(metadata); await authorSwitchProject(metadata.id); view = 'editor'; render();
    }, created.project || created.state.project);
    release(); await pause(350);
    assert.equal(await page.evaluate(() => requestedProject), other);
    assert.equal(await page.evaluate(() => authorAgentFocus), null);
    assert.equal(await page.locator(`[data-agent-session="${focusedTask}"]`).count(), 0);
    await page.unroute(targetUrl, delayed);
  });

  assert.deepEqual(report.page_errors, []);
  report.provider_calls = providerRequests.length;
  report.passed = true;
  await context.close();
}

main().catch(error => {
  report.error = error.stack; report.server_log = log.slice(-5000); process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  await stopApp();
  if (provider) await new Promise(resolve => provider.close(resolve));
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
