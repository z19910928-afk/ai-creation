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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-planning-browser-'));
const results = {
  passed: false,
  scope: '规划输入保存、采用竞态、规划草稿恢复、第二轮物化；真实浏览器、隔离 SQLite、本机模拟提供方',
  cases: [],
  artifacts: temporary,
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app;
let provider;
let browser;
let base;
let providerPort;
let appPort;
let serverLog = '';
const providerCalls = [];

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function api(route, method = 'GET', body, project = 'default-project') {
  const response = await fetch(base + route, {
    method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);
  return data;
}

async function waitUntil(check, timeout = 15000, interval = 100) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch (error) {
      lastError = error;
    }
    await pause(interval);
  }
  if (lastError) throw lastError;
  throw new Error(`等待条件超时（${timeout}ms）`);
}

async function waitSaved(page) {
  await page.waitForFunction(
    () => backendOnline && confirmedState && !isDirty() && !syncInFlight &&
      !independentWrite && !adopting && !saveError && !mergePending,
    null,
    {timeout: 15000},
  );
}

async function openPage() {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.waitForFunction(() => backendOnline === true, null, {timeout: 15000});
  return {context, page, errors};
}

async function switchProject(page, projectId) {
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${projectId}"]`).waitFor({timeout: 15000});
  await page.locator(`[data-project-open="${projectId}"]`).click();
  await page.waitForFunction(id => requestedProject === id, projectId, {timeout: 15000});
  await waitSaved(page);
}

async function createProject(title) {
  const data = await api('/api/projects', 'POST', {
    title,
    summary: `${title} 的测试简介`,
    genre: '悬疑',
    goal: 50000,
  });
  return (data.project || data.state?.project);
}

function candidateFor(stage, blueprint, suffix = '') {
  if (stage === 'overview') {
    return {
      premise: `${blueprint.premise || '测试故事'}；${suffix || '总纲候选'}`,
      genre: blueprint.genre || '悬疑',
      audience: blueprint.audience || '成年读者',
      tone: blueprint.tone || '克制',
      contract: blueprint.contract || '人物只依据自己知道的事实行动',
    };
  }
  if (stage === 'volumes') {
    return {
      volumes: (blueprint.volumes || []).map((volume, index) => ({
        id: volume.id,
        title: `第${index + 1}卷 雪路`,
        summary: '邮差循着封缄寻找旧驿站。',
      })),
    };
  }
  if (stage === 'chapters') {
    return {
      volumes: (blueprint.volumes || []).map(volume => ({
        id: volume.id,
        chapters: (volume.chapters || []).map((chapter, index) => ({
          id: chapter.id,
          title: `第${index + 1}章 封缄`,
          goal: '找到寄信人',
          pov: '邮差',
          hook: '蜡印露出半个字。',
        })),
      })),
    };
  }
  return {
    volumes: (blueprint.volumes || []).map(volume => ({
      id: volume.id,
      chapters: (volume.chapters || []).map(chapter => ({
        id: chapter.id,
        scenes: (chapter.scenes || []).map((scene, index) => ({
          id: scene.id,
          title: `场景${index + 1} 驿站冲突`,
          goal: '取回包裹',
          location: '旧驿站',
          pov: '邮差',
        })),
      })),
    })),
  };
}

async function completeWorkflow(project, premise, counts = {
  volumes: 1,
  chapters_per_volume: 1,
  scenes_per_chapter: 1,
}) {
  let created = await api('/api/planning-workflows', 'POST', {
    initial: {
      premise,
      genre: '悬疑',
      audience: '成年读者',
      tone: '克制',
      contract: '人物只依据自己知道的事实行动',
      counts,
    },
  }, project);
  let workflow = created.workflow || created;
  for (let index = 0; index < 4; index += 1) {
    const started = await api(
      `/api/planning-workflows/${encodeURIComponent(workflow.id)}/start`,
      'POST',
      {expected_stage: workflow.stage, expected_version: workflow.version, defer: true},
      project,
    );
    workflow = started.workflow || started;
    const jobId = workflow.attempt?.job_id;
    assert.ok(jobId, `规划阶段 ${workflow.stage} 未返回任务身份`);
    await api(`/api/ai/jobs/${encodeURIComponent(jobId)}/process`, 'POST', {}, project);
    const detail = await api(`/api/planning-workflows/${encodeURIComponent(workflow.id)}`, 'GET', undefined, project);
    workflow = detail.workflow || detail;
    const confirmed = await api(
      `/api/planning-workflows/${encodeURIComponent(workflow.id)}/confirm`,
      'POST',
      {
        expected_stage: workflow.stage,
        expected_version: workflow.version,
        attempt_id: workflow.attempt.id,
        candidate_hash: workflow.attempt.candidate_hash,
      },
      project,
    );
    workflow = confirmed.workflow || confirmed;
  }
  assert.equal(workflow.status, 'completed');
  return workflow;
}

async function applyWorkflowInBrowser(page, project, workflow) {
  await switchProject(page, project);
  await page.locator('[data-view="blueprint"]').click();
  await page.locator('[data-blueprint-field="premise"]').waitFor({timeout: 15000});
  await page.evaluate(async id => {
    authorPlanningSelected = id;
    await authorLoadPlanning();
  }, workflow.id);
  await page.locator('[data-planning-apply]').waitFor({timeout: 15000});
  await page.locator('[data-planning-apply]').click();
  await page.waitForFunction(() => authorPlanning?.applied === true, null, {timeout: 15000});
}

async function materializeInBrowser(page, project) {
  const version = (await api('/api/state', 'GET', undefined, project)).version;
  await page.locator('[data-action="materializePlan"]').click();
  await page.waitForFunction(
    before => Number(state.version) > Number(before) && state.chapters.some(chapter => chapter.plan_id),
    version,
    {timeout: 15000},
  );
  await waitSaved(page);
  return api('/api/state', 'GET', undefined, project);
}

async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn();
  results.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, state: 'passed'}));
}

async function startServices() {
  providerPort = await freePort();
  provider = http.createServer((request, response) => {
    let raw = '';
    request.on('data', chunk => { raw += chunk; });
    request.on('end', () => {
      try {
        const input = JSON.parse(raw || '{}');
        const user = input.messages?.find(message => message.role === 'user')?.content || '';
        const contextText = user.split('<manuscript>\n')[1]?.split('\n</manuscript>')[0] || '{}';
        const context = JSON.parse(contextText);
        const planning = context.planning_workflow || {};
        providerCalls.push({stage: planning.stage, context, payload: input});
        const value = candidateFor(planning.stage, context.blueprint || {});
        const body = Buffer.from(JSON.stringify({
          model: 'planning-browser-fixture',
          choices: [{message: {content: JSON.stringify(value)}, finish_reason: 'stop'}],
          usage: {prompt_tokens: 80, completion_tokens: 60, total_tokens: 140},
        }));
        response.writeHead(200, {'Content-Type': 'application/json', 'Content-Length': body.length});
        response.end(body);
      } catch (error) {
        const body = Buffer.from(JSON.stringify({error: {message: error.message}}));
        response.writeHead(500, {'Content-Type': 'application/json', 'Content-Length': body.length});
        response.end(body);
      }
    });
  });
  await new Promise(resolve => provider.listen(providerPort, '127.0.0.1', resolve));
  appPort = await freePort();
  base = `http://127.0.0.1:${appPort}`;
  const db = path.join(temporary, 'planning-browser.sqlite3');
  app = spawn(
    process.env.MOYU_TEST_PYTHON || 'python3',
    ['server.py'],
    {
      cwd: root,
      env: {
        ...process.env,
        MOYU_DB: db,
        MOYU_HOST: '127.0.0.1',
        MOYU_PORT: String(appPort),
        MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`,
        MOYU_AI_MODEL: 'planning-browser-fixture',
        MOYU_AI_API_KEY: '',
        MOYU_AI_DAILY_QUOTA: '1000',
        MOYU_SECRET_KEY: '',
        MOYU_BACKUP_DIR: path.join(temporary, 'backups'),
        MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials'),
      },
      stdio: ['ignore', 'ignore', 'pipe'],
    },
  );
  app.stderr.on('data', chunk => { serverLog += chunk.toString(); });
  for (let index = 0; index < 250; index += 1) {
    try {
      if ((await fetch(base + '/api/health')).ok) return;
    } catch {}
    if (app.exitCode !== null) throw new Error(`服务退出：${serverLog.slice(-3000)}`);
    await pause(40);
  }
  throw new Error(`本机服务未启动：${serverLog.slice(-3000)}`);
}

async function main() {
  await startServices();
  const seed = await api('/api/state');
  const defaultProject = seed.project.id;
  await api('/api/ai/settings', 'PUT', {
    base_url: `http://127.0.0.1:${providerPort}/v1`,
    model: 'planning-browser-fixture',
    api_key: '',
    daily_limit: 1000,
    parameters: {temperature: 0, max_tokens: 4000},
  }, defaultProject);
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.MOYU_CHROME_PATH || undefined,
  });
  const workspace = await openPage();

  await test('同一规划输入框连续两次自动保存不会丢第二次修改', async () => {
    const project = await createProject('规划保存竞态测试');
    await api('/api/ai/settings', 'PUT', {
      base_url: `http://127.0.0.1:${providerPort}/v1`,
      model: 'planning-browser-fixture',
      api_key: '',
      daily_limit: 1000,
      parameters: {temperature: 0, max_tokens: 4000},
    }, project.id);
    await switchProject(workspace.page, project.id);
    await workspace.page.locator('[data-view="blueprint"]').click();
    await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
    const first = '第一次自动保存的规划哨兵。';
    const second = '第二次同框修改，应该真正保存的规划哨兵。';
    await workspace.page.locator('[data-blueprint-field="premise"]').fill(first);
    await workspace.page.waitForFunction(() => !authorBlueprintDirty && !authorBlueprintPromise, null, {timeout: 15000});
    await workspace.page.locator('[data-blueprint-field="premise"]').fill(second);
    await workspace.page.waitForFunction(() => !authorBlueprintDirty && !authorBlueprintPromise, null, {timeout: 15000});
    await waitUntil(async () => (await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise === second);
    assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').inputValue(), second);
    assert.equal(await workspace.page.evaluate(() => authorBlueprint.premise), second);
    assert.equal((await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise, second);
  });

  await test('采用规划等待期间切页返回仍锁定输入，结束后新输入正常保存', async () => {
    const project = await createProject('规划采用竞态测试');
    await api('/api/ai/settings', 'PUT', {
      base_url: `http://127.0.0.1:${providerPort}/v1`,
      model: 'planning-browser-fixture',
      api_key: '',
      daily_limit: 1000,
      parameters: {temperature: 0, max_tokens: 4000},
    }, project.id);
    const workflow = await completeWorkflow(project.id, '模型生成的旧规划');
    await switchProject(workspace.page, project.id);
    await workspace.page.locator('[data-view="blueprint"]').click();
    await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
    await workspace.page.evaluate(async id => {
      authorPlanningSelected = id;
      await authorLoadPlanning();
    }, workflow.id);
    await workspace.page.locator('[data-planning-apply]').waitFor();
    let held;
    let release;
    const responseReady = new Promise(resolve => { release = resolve; });
    await workspace.page.route('**/api/planning-workflows/*/apply', async route => {
      if (held) return route.continue();
      const response = await route.fetch();
      held = {route, response};
      release();
    });
    await workspace.page.locator('[data-planning-apply]').click();
    await responseReady;
    await workspace.page.locator('[data-view="editor"]').click();
    await workspace.page.locator('[data-view="blueprint"]').click();
    await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
    const late = '采用等待重返页面后新写的规划，不得被迟到回应覆盖。';
    assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').isEnabled(), false);
    assert.equal(await workspace.page.locator('[data-action="addVolume"]').isEnabled(), false);
    assert.equal(await workspace.page.locator('[data-action="materializePlan"]').isEnabled(), false);
    assert.equal(await workspace.page.locator('[data-action="saveBlueprint"]').isEnabled(), false);
    const locked = await workspace.page.evaluate(() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(locked, true);
    await held.route.fulfill({response: held.response});
    await workspace.page.waitForFunction(() => !authorPlanningBusy, null, {timeout: 15000});
    assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').isEnabled(), true);
    await workspace.page.locator('[data-blueprint-field="premise"]').fill(late);
    await waitUntil(async () => (await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise === late);
    assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').inputValue(), late);
    assert.equal(await workspace.page.evaluate(() => authorBlueprint.premise), late);
    assert.equal((await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise, late);
    await workspace.page.unroute('**/api/planning-workflows/*/apply');
  });

  await test('规划保存失败后刷新仍能恢复本地规划草稿并阻止静默离开', async () => {
    const project = await createProject('规划草稿恢复测试');
    await switchProject(workspace.page, project.id);
    await workspace.page.locator('[data-view="blueprint"]').click();
    await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
    await workspace.page.route('**/api/blueprint', route =>
      route.request().method() === 'PUT' ? route.abort('failed') : route.continue());
    const draft = '断网后待保存的规划哨兵，不得刷新后消失。';
    await workspace.page.locator('[data-blueprint-field="premise"]').fill(draft);
    await pause(1200);
    const beforeReload = await workspace.page.evaluate(() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return {dirty: authorBlueprintDirty, prevented: event.defaultPrevented};
    });
    assert.equal(beforeReload.dirty, true);
    assert.equal(beforeReload.prevented, true);
    await workspace.page.unroute('**/api/blueprint');
    const acceptReload = dialog => dialog.accept();
    workspace.page.on('dialog', acceptReload);
    await workspace.page.reload();
    workspace.page.off('dialog', acceptReload);
    await workspace.page.waitForFunction(() => backendOnline === true, null, {timeout: 15000});
    await workspace.page.locator('[data-view="blueprint"]').click();
    await workspace.page.locator('[data-blueprint-field="premise"]').waitFor({timeout: 15000});
    assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').inputValue(), draft);
    assert.equal(await workspace.page.evaluate(() => authorBlueprint.premise), draft);
    await workspace.page.locator('[data-action="saveBlueprint"]').click();
    await workspace.page.waitForFunction(() => !authorBlueprintDirty && !authorBlueprintPromise, null, {timeout: 15000});
    assert.equal((await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise, draft);
  });

  await test('两标签同字段规划冲突保留双方并按作者选择保存', async () => {
    const project = await createProject('两标签规划冲突测试');
    await switchProject(workspace.page, project.id);
    await workspace.page.locator('[data-view="blueprint"]').click();
    await workspace.page.locator('[data-blueprint-field="premise"]').waitFor();
    const other = await workspace.context.newPage();
    const errors = [];
    other.on('pageerror', error => errors.push(error.message));
    await other.goto(base);
    await other.waitForFunction(() => backendOnline === true, null, {timeout: 15000});
    await other.locator('[data-view="blueprint"]').click();
    await other.locator('[data-blueprint-field="premise"]').waitFor();
    for (const [index, choice] of ['local', 'remote'].entries()) {
      await workspace.page.evaluate(() => authorLoadPage());
      await other.evaluate(() => authorLoadPage());
      const local = `第${index + 1}轮本标签规划哨兵。`;
      const remote = `第${index + 1}轮另一标签规划哨兵。`;
      await workspace.page.route('**/api/blueprint', route =>
        route.request().method() === 'PUT' ? route.abort('failed') : route.continue());
      await workspace.page.locator('[data-blueprint-field="premise"]').fill(local);
      await pause(1100);
      assert.equal(await workspace.page.evaluate(() => authorBlueprintDirty), true);
      await other.locator('[data-blueprint-field="premise"]').fill(remote);
      await other.waitForFunction(() => !authorBlueprintDirty && !authorBlueprintPromise, null, {timeout: 15000});
      assert.equal((await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise, remote);
      await workspace.page.unroute('**/api/blueprint');
      await workspace.page.locator('[data-action="saveBlueprint"]').click();
      await workspace.page.locator('[data-blueprint-merge]').waitFor({timeout: 15000});
      assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').inputValue(), local);
      const conflict = await workspace.page.evaluate(() => authorBlueprintConflict);
      assert.ok(conflict.conflicts.some(item => item.path === 'premise' && item.local === local && item.remote === remote));
      await workspace.page.locator('[data-blueprint-merge]').click();
      const dialog = workspace.page.locator('.author-form');
      assert.match(await dialog.innerText(), new RegExp(local));
      assert.match(await dialog.innerText(), new RegExp(remote));
      await dialog.locator('[data-blueprint-choice="0"]').selectOption(choice);
      await dialog.locator('[type="submit"]').click();
      await dialog.waitFor({state: 'detached', timeout: 15000});
      const selected = choice === 'local' ? local : remote;
      assert.equal(await workspace.page.locator('[data-blueprint-field="premise"]').inputValue(), selected);
      assert.equal((await api('/api/blueprint', 'GET', undefined, project.id)).blueprint.premise, selected);
    }
    assert.deepEqual(errors, []);
    await other.close();
  });

  await test('第二套规划物化只新增章节并保留第一轮章节全部内容', async () => {
    const project = await createProject('两轮规划旧章保护测试');
    await api('/api/ai/settings', 'PUT', {
      base_url: `http://127.0.0.1:${providerPort}/v1`,
      model: 'planning-browser-fixture',
      api_key: '',
      daily_limit: 1000,
      parameters: {temperature: 0, max_tokens: 4000},
    }, project.id);
    const first = await completeWorkflow(project.id, '第一轮规划：旧驿站封缄', {
      volumes: 1,
      chapters_per_volume: 1,
      scenes_per_chapter: 1,
    });
    await applyWorkflowInBrowser(workspace.page, project.id, first);
    let state = await materializeInBrowser(workspace.page, project.id);
    const firstChapter = state.chapters.find(chapter => chapter.plan_id);
    assert.ok(firstChapter, '第一轮物化未建立章节');
    await workspace.page.locator('[data-view="editor"]').click();
    await workspace.page.locator(`[data-chapter="${firstChapter.id}"]`).click();
    await workspace.page.locator('#chapterBody').fill('第一轮已经写好的旧正文，第二轮规划不能覆盖。');
    await workspace.page.locator('#chapterTitle').fill('第一轮旧章真实标题');
    await workspace.page.locator('details.chapter-plan').evaluate(element => { element.open = true; });
    await workspace.page.locator('#chapterGoal').fill('守住第一轮旧章目标');
    await workspace.page.locator('#chapterPov').fill('林岚');
    await workspace.page.locator('#chapterScenePlan').fill('旧驿站门口的冲突');
    await workspace.page.locator('#chapterForeshadowing').fill('第一轮旧章伏笔');
    await workspace.page.locator('#chapterRecap').fill('第一轮旧章回顾');
    await workspace.page.locator('#chapterNextBrief').fill('第一轮旧章交接');
    await workspace.page.locator('#chapterHook').fill('第一轮旧章出口');
    await workspace.page.locator('#chapterStatus').selectOption('进行中');
    await waitSaved(workspace.page);
    state = await api('/api/state', 'GET', undefined, project.id);
    const oldChapters = state.chapters;
    const oldSnapshot = state.chapters.find(chapter => chapter.id === firstChapter.id);
    const oldIds = new Set(state.chapters.map(chapter => chapter.id));
    const second = await completeWorkflow(project.id, '第二轮规划：新增封缄线', {
      volumes: 1,
      chapters_per_volume: 1,
      scenes_per_chapter: 1,
    });
    await applyWorkflowInBrowser(workspace.page, project.id, second);
    state = await materializeInBrowser(workspace.page, project.id);
    const preserved = state.chapters.find(chapter => chapter.id === oldSnapshot.id);
    assert.deepEqual(preserved, oldSnapshot);
    for (const old of oldChapters) assert.deepEqual(state.chapters.find(chapter => chapter.id === old.id), old);
    const added = state.chapters.filter(chapter => !oldIds.has(chapter.id));
    assert.equal(added.length, 1);
    assert.equal(new Set(state.chapters.map(chapter => chapter.id)).size, state.chapters.length);
    const planned = state.chapters.filter(chapter => chapter.plan_id);
    assert.equal(new Set(planned.map(chapter => chapter.plan_id)).size, planned.length);
    const afterFirstMaterialization = state;
    state = await materializeInBrowser(workspace.page, project.id);
    assert.deepEqual(state.chapters.map(chapter => chapter.id), afterFirstMaterialization.chapters.map(chapter => chapter.id));
    for (const old of oldChapters) assert.deepEqual(state.chapters.find(chapter => chapter.id === old.id), old);
    await workspace.page.screenshot({path: path.join(temporary, 'planning-second-round.png'), fullPage: true});
  });

  assert.deepEqual(workspace.errors, []);
  await workspace.context.close();
  results.passed = true;
}

main().catch(error => {
  results.failure = {message: error.message, stack: error.stack, server_log: serverLog.slice(-5000)};
  process.exitCode = 1;
}).finally(async () => {
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  if (browser) await browser.close();
  if (app && app.exitCode === null) {
    const stopped = new Promise(resolve => app.once('exit', resolve));
    app.kill('SIGTERM');
    await Promise.race([stopped, pause(2000)]);
    if (app.exitCode === null) app.kill('SIGKILL');
  }
  if (provider) {
    await new Promise(resolve => {
      provider.close(resolve);
      provider.closeAllConnections?.();
    });
  }
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify(results, null, 2));
});
