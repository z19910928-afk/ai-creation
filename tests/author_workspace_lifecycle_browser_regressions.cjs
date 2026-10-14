'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-author-lifecycle-'));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const results = {
  passed: false,
  scope: '桌面真实浏览器、隔离 SQLite、免费本机提供方；规划归档、已保存作品重开、Unicode 导入及旧审稿作者决定',
  cases: [],
  artifacts: temporary,
  source_files: [
    'app.js', 'author-workspace.js', 'server.py', 'moyu_analysis.py',
    'moyu_workspace.py', 'moyu_context.py', 'styles.css',
    'tests/author_workspace_lifecycle_browser_regressions.cjs',
  ].map(file => ({path: file, sha256: hash(fs.readFileSync(path.join(root, file)))})),
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app;
let provider;
let browser;
let base;
let serverLog = '';
let providerCalls = 0;

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

async function waitSaved(page) {
  await page.waitForFunction(
    () => backendOnline && confirmedState && !isDirty() && !syncInFlight &&
      !independentWrite && !adopting && !saveError && !mergePending,
    null,
    {timeout: 15000},
  );
}

async function createProject(title, count = 1) {
  const created = await api('/api/projects', 'POST', {
    title, summary: '作者桌面流程隔离测试。', genre: '悬疑', goal: 50000,
  });
  const project = created.project || created.state?.project;
  const snapshot = await api('/api/state', 'GET', undefined, project.id);
  const original = snapshot.chapters[0];
  for (let index = 0; index < count; index += 1) {
    const item = index === 0 ? original : {id: `lifecycle_${crypto.randomUUID()}`, title: `第${index + 1}章`};
    Object.assign(item, {
      body: `第${index + 1}章保留原稿。林岚拿起红铜罗盘，先核对刻度，再绕开守门人走向渡口。`.repeat(3),
      chapter_goal: `第${index + 1}章独立目标`,
      scene_plan: `第${index + 1}章独立场景`,
      pov: `第${index + 1}章独立视角`,
      status: '草稿',
      next_brief: index === 0 ? '交接只作上章说明，不替换后续章的目标和场景。' : '',
      recap: '已记录本章经过。',
      hook: '渡口还有一扇锁着的门。',
    });
    if (index > 0) snapshot.chapters.push(item);
  }
  await api('/api/state', 'PUT', snapshot, project.id);
  return {...project, firstId: original.id};
}

async function openProject(project, viewport = {width: 1440, height: 1000}) {
  const context = await browser.newContext({viewport});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.waitForFunction(() => backendOnline, null, {timeout: 15000});
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${project.id}"]`).click();
  await page.waitForFunction(id => requestedProject === id && backendOnline, project.id);
  await page.locator('[data-view="editor"]').click();
  await waitSaved(page);
  return {context, page, errors};
}

async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn();
  results.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, state: 'passed'}));
}

async function finish(page, expectedChapter) {
  await page.locator('[data-action="finishChapter"]').click();
  await page.waitForFunction(
    id => authorFinishing === null && activeChapter === id,
    expectedChapter,
    {timeout: 15000},
  );
  await waitSaved(page);
}

async function importPreview(page, name = '导入恢复.txt', content = '第一章 门口\n导入正文😀。每一句原稿都要完整保留。\n第二章 渡口\n后续章原稿也要完整保留。') {
  await page.locator('#bookFileInput').setInputFiles({
    name, mimeType: 'text/plain', buffer: Buffer.from(content),
  });
  await page.locator('[data-import-confirm]').waitFor();
}

async function savedOpenRecovery(operation, failureMode) {
  const owner = await createProject(`${operation}-${failureMode}-原作品`);
  const before = await api('/api/state', 'GET', undefined, owner.id);
  const workspace = await openProject(owner);
  const {page} = workspace;
  let saved = false;
  let fail = true;
  let targetId = null;
  let posts = 0;
  let originReads = 0;
  let targetReads = 0;
  const routePath = operation === 'new' ? '/api/projects' :
    operation === 'copy' ? `/api/projects/${owner.id}/duplicate` : '/api/import/apply';
  await page.route('**/api/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === routePath && request.method() === 'POST') {
      posts += 1;
      const response = await route.fetch();
      const data = await response.json();
      assert.ok(response.ok(), JSON.stringify(data));
      targetId = (data.project || data.state?.project).id;
      saved = true;
      return route.fulfill({response});
    }
    if (saved && pathname === '/api/state' && request.method() === 'GET') {
      const projectId = request.headers()['x-moyu-project'];
      if (projectId === owner.id) originReads += 1;
      if (projectId === targetId) targetReads += 1;
      const shouldFail = failureMode === 'origin' ? projectId === owner.id :
        failureMode === 'target' ? projectId === targetId : [owner.id, targetId].includes(projectId);
      if (fail && shouldFail) {
        return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: '已保存后的读取模拟失败'})});
      }
    }
    return route.continue();
  });
  if (operation === 'new') {
    await page.locator('[data-view="library"]').click();
    await page.locator('[data-action="createProject"]').click();
    await page.locator('#newBookTitle').fill(`${operation}-${failureMode}-新作品`);
    await page.locator('.author-form [type="submit"]').click();
  } else if (operation === 'copy') {
    await page.locator('[data-view="library"]').click();
    await page.locator(`[data-project-copy="${owner.id}"]`).click();
    await page.locator('.author-form [type="submit"]').click();
  } else {
    await importPreview(page);
    await page.locator('#importBookTitle').fill(`${operation}-${failureMode}-新作品`);
    await page.locator('[data-import-confirm]').click();
  }
  if (failureMode === 'origin') {
    await page.waitForFunction(() => !document.querySelector('.modal-backdrop'), null, {timeout: 15000});
  } else {
    await page.locator('.form-error').filter({hasText: /已经保存.*暂时无法打开/}).waitFor();
    const button = page.locator(operation === 'import' ? '[data-import-confirm]' : '.author-form [type="submit"]');
    assert.equal(await button.innerText(), '重新打开作品');
    assert.equal(await page.locator(operation === 'import' ? '#importBookTitle' : operation === 'new' ? '#newBookTitle' : '#copyTitle').isDisabled(), true);
    assert.equal(posts, 1);
    assert.ok(await page.locator('.modal-backdrop').count());
    fail = false;
    await button.click();
    await page.waitForFunction(() => !document.querySelector('.modal-backdrop'), null, {timeout: 15000});
  }
  await waitSaved(page);
  assert.equal(await page.evaluate(() => requestedProject), targetId);
  assert.equal(await page.evaluate(() => state.project.id), targetId);
  assert.equal(posts, 1, '重新打开不能再次创建作品');
  assert.ok(targetReads >= (failureMode === 'origin' ? 1 : 2));
  if (failureMode !== 'target') assert.ok(originReads >= 1);
  const after = await api('/api/state', 'GET', undefined, owner.id);
  assert.deepEqual(after.chapters, before.chapters, '原作品不能被恢复流程改写');
  assert.deepEqual(workspace.errors, []);
  results.recovery = [...(results.recovery || []), {operation, failureMode, posts, originReads, targetReads, opened: true}];
  await workspace.context.close();
}

async function startServices() {
  const providerPort = await freePort();
  provider = http.createServer((request, response) => {
    let raw = '';
    request.on('data', chunk => {raw += chunk;});
    request.on('end', () => {
      try {
        providerCalls += 1;
        const input = JSON.parse(raw || '{}');
        const user = input.messages?.find(message => message.role === 'user')?.content || '';
        const context = JSON.parse(user.split('<manuscript>\n')[1]?.split('\n</manuscript>')[0] || '{}');
        const chapter = context.current_chapter || {};
        const quote = String(chapter.body || '').split(/\r?\n/).find(line => line.trim()) || '';
        const content = JSON.stringify({
          findings: [{
            category: 'motivation', severity: 'possible', message: '动机需要作者核对。',
            suggestion: '用已有动作解释选择。', evidence: [{source_chapter_id: chapter.id, source_quote: quote}],
          }],
          uncertainties: ['本机提供方仅验证处理规则。'],
        });
        response.writeHead(200, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({
          model: 'lifecycle-local-fixture',
          choices: [{message: {content}, finish_reason: 'stop'}],
          usage: {prompt_tokens: 20, completion_tokens: 20, total_tokens: 40},
        }));
      } catch (error) {
        response.writeHead(500, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({error: {message: error.message}}));
      }
    });
  });
  await new Promise(resolve => provider.listen(providerPort, '127.0.0.1', resolve));
  const appPort = await freePort();
  base = `http://127.0.0.1:${appPort}`;
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {
    cwd: root,
    env: {
      ...process.env,
      MOYU_DB: path.join(temporary, 'lifecycle.sqlite3'),
      MOYU_HOST: '127.0.0.1', MOYU_PORT: String(appPort),
      MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`,
      MOYU_AI_MODEL: 'lifecycle-local-fixture', MOYU_AI_API_KEY: '',
      MOYU_AI_DAILY_QUOTA: '1000', MOYU_SECRET_KEY: '',
      MOYU_BACKUP_DIR: path.join(temporary, 'backups'),
      MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials'),
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  app.stderr.on('data', chunk => {serverLog += chunk.toString();});
  for (let index = 0; index < 250; index += 1) {
    try {
      if ((await fetch(base + '/api/health')).ok) {
        return `http://127.0.0.1:${providerPort}/v1`;
      }
    } catch {}
    if (app.exitCode !== null) throw new Error(`服务退出：${serverLog.slice(-3000)}`);
    await pause(40);
  }
  throw new Error(`本机服务未启动：${serverLog.slice(-3000)}`);
}

async function main() {
  const providerUrl = await startServices();
  await api('/api/state');
  browser = await chromium.launch({
    headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined,
  });

  await test('五章规划归档首章仍五章，已有正文目标场景视角卷身份完整保留', async () => {
    const project = await createProject('五章规划归档');
    const blueprint = {
      premise: '四个后续计划章已接入目录。',
      volumes: [0, 1].map(volume => ({
        id: `lifecycle_volume_${volume}`, title: `第${volume + 1}卷`,
        chapters: [0, 1].map(index => ({
          id: `lifecycle_plan_${volume}_${index}`, title: `计划章${volume * 2 + index + 1}`,
          goal: `计划目标${volume}_${index}`, pov: '守灯人', hook: '保留计划出口',
          scenes: [{id: `lifecycle_scene_${volume}_${index}`, title: '旧驿站', goal: '计划场景目标', location: '驿站', pov: '守灯人'}],
        })),
      })),
    };
    await api('/api/blueprint', 'PUT', {expected_version: 0, blueprint}, project.id);
    await api('/api/blueprint/materialize', 'POST', {expected_version: 1}, project.id);
    const before = await api('/api/state', 'GET', undefined, project.id);
    assert.equal(before.chapters.length, 5);
    const original = before.chapters.find(item => item.id === project.firstId);
    before.chapters[0].body = original.body;
    before.chapters[0].next_brief = original.next_brief;
    before.chapters[0].recap = original.recap;
    project.firstId = before.chapters[0].id;
    before.chapters[1].body = '计划第二章已经存在的正文😀，不能因前章归档被覆盖。';
    await api('/api/state', 'PUT', before, project.id);
    const baseline = await api('/api/state', 'GET', undefined, project.id);
    const workspace = await openProject(project);
    await workspace.page.locator('.chapter-group h3').filter({hasText: '第1卷'}).waitFor();
    assert.deepEqual(await workspace.page.locator('.chapter-group h3').allTextContents(), ['第1卷', '第2卷', '章节']);
    await workspace.page.locator('[data-action="finishChapter"]').dblclick();
    await workspace.page.waitForFunction(id => authorFinishing === null && activeChapter === id, baseline.chapters[1].id);
    await waitSaved(workspace.page);
    const after = await api('/api/state', 'GET', undefined, project.id);
    assert.equal(after.chapters.length, 5);
    assert.equal(after.chapters[0].status, '完成');
    assert.deepEqual(after.chapters.slice(1), baseline.chapters.slice(1));
    assert.equal(await workspace.page.locator('#previousChapterHandoff').inputValue(), baseline.chapters[0].next_brief);
    assert.equal(await workspace.page.locator('#chapterGoal').inputValue(), baseline.chapters[1].chapter_goal);
    await workspace.page.locator('details.chapter-plan > summary').click();
    results.desktop_layout = [];
    for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 900}]) {
      await workspace.page.setViewportSize(viewport);
      await workspace.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await workspace.page.locator('#previousChapterHandoff').scrollIntoViewIfNeeded();
      await workspace.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const measureHandoff = input => {
        const rect = input.getBoundingClientRect();
        const parent = input.closest('label').getBoundingClientRect();
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return {
          width: rect.width, height: rect.height, label_width: parent.width,
          x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, scroll_y: scrollY,
          within: rect.x >= 0 && rect.right <= innerWidth && rect.y >= 64 && rect.bottom <= innerHeight,
          unobstructed: hit === input || input.contains(hit), readonly: input.readOnly, value: input.value,
        };
      };
      const initialHandoff = await workspace.page.locator('#previousChapterHandoff').evaluate(measureHandoff);
      if (!initialHandoff.within) {
        await workspace.page.locator('#previousChapterHandoff').evaluate(input =>
          input.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'}));
        await workspace.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      }
      const handoff = await workspace.page.locator('#previousChapterHandoff').evaluate(measureHandoff);
      await workspace.page.screenshot({
        path: path.join(temporary, viewport.width === 1440 ? 'planned-next-chapter.png' : 'planned-next-chapter-1280x900.png'),
        fullPage: false,
      });
      results.desktop_layout.push({viewport, initial_handoff: initialHandoff, centered_for_capture: !initialHandoff.within, handoff});
      assert.ok(handoff.width >= handoff.label_width * 0.95, JSON.stringify(handoff));
      assert.ok(handoff.height >= 60 && handoff.within && handoff.unobstructed && handoff.readonly, JSON.stringify(handoff));
      assert.equal(handoff.value, baseline.chapters[0].next_brief);
      assert.equal(await workspace.page.locator('#chapterBody').inputValue(), baseline.chapters[1].body);
      assert.equal(await workspace.page.locator('#chapterGoal').inputValue(), baseline.chapters[1].chapter_goal);
    }
    await workspace.page.locator(`[data-chapter="${project.firstId}"]`).click();
    await finish(workspace.page, baseline.chapters[1].id);
    assert.equal((await api('/api/state', 'GET', undefined, project.id)).chapters.length, 5);
    await workspace.page.reload();
    await workspace.page.waitForFunction(() => backendOnline);
    await workspace.page.locator('[data-view="editor"]').click();
    await workspace.page.locator(`[data-chapter="${project.firstId}"]`).click();
    await finish(workspace.page, baseline.chapters[1].id);
    assert.equal((await api('/api/state', 'GET', undefined, project.id)).chapters.length, 5);
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  await test('归档跳过已完成后续章，刷新重复归档不会追加空章', async () => {
    const project = await createProject('归档跳过完成章', 3);
    const snapshot = await api('/api/state', 'GET', undefined, project.id);
    snapshot.chapters[1].status = '完成';
    await api('/api/state', 'PUT', snapshot, project.id);
    const workspace = await openProject(project);
    await finish(workspace.page, snapshot.chapters[2].id);
    const after = await api('/api/state', 'GET', undefined, project.id);
    assert.equal(after.chapters.length, 3);
    assert.deepEqual(after.chapters.slice(1), snapshot.chapters.slice(1).map(item => ({...item})));
    await workspace.context.close();
  });

  await test('无后续章仅新建一次，保存失败恢复复用身份且新章目标不覆盖交接', async () => {
    const project = await createProject('末章归档保存失败');
    const workspace = await openProject(project);
    let failed = false;
    let nextId = null;
    await workspace.page.route('**/api/state', route => {
      const request = route.request();
      if (request.method() === 'PUT') {
        const data = request.postDataJSON();
        if (data.chapters.length === 2) {
          nextId = data.chapters[1].id;
          if (!failed) {
            failed = true;
            return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: '下一章保存模拟失败'})});
          }
        }
      }
      return route.continue();
    });
    await workspace.page.locator('[data-action="finishChapter"]').click();
    await workspace.page.waitForFunction(() => authorFinishing === null && saveError, null, {timeout: 15000});
    assert.equal(await workspace.page.evaluate(() => activeChapter), project.firstId);
    assert.equal((await api('/api/state', 'GET', undefined, project.id)).chapters.length, 1);
    await workspace.page.unroute('**/api/state');
    await workspace.page.evaluate(() => syncBackend());
    await waitSaved(workspace.page);
    await finish(workspace.page, nextId);
    const after = await api('/api/state', 'GET', undefined, project.id);
    assert.equal(after.chapters.length, 2);
    assert.equal(after.chapters[1].id, nextId);
    assert.equal(after.chapters[1].chapter_goal, '');
    assert.equal(await workspace.page.locator('#previousChapterHandoff').inputValue(), after.chapters[0].next_brief);
    await workspace.page.locator(`[data-chapter="${project.firstId}"]`).click();
    await finish(workspace.page, nextId);
    assert.equal((await api('/api/state', 'GET', undefined, project.id)).chapters.length, 2);
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  await test('归档新章保存等待切章，迟到成功回应不强行切回', async () => {
    const project = await createProject('末章归档切章竞态', 2);
    const snapshot = await api('/api/state', 'GET', undefined, project.id);
    snapshot.chapters[1].status = '完成';
    await api('/api/state', 'PUT', snapshot, project.id);
    const workspace = await openProject(project);
    let held;
    let notify;
    const ready = new Promise(resolve => {notify = resolve;});
    await workspace.page.route('**/api/state', async route => {
      if (route.request().method() === 'PUT' && route.request().postDataJSON().chapters.length === 3 && !held) {
        held = {route, response: await route.fetch()};
        notify();
        return;
      }
      return route.continue();
    });
    await workspace.page.locator('[data-action="finishChapter"]').click();
    await ready;
    await workspace.page.locator(`[data-chapter="${snapshot.chapters[1].id}"]`).click();
    await held.route.fulfill({response: held.response});
    await workspace.page.waitForFunction(() => authorFinishing === null);
    assert.equal(await workspace.page.evaluate(() => activeChapter), snapshot.chapters[1].id);
    assert.equal((await api('/api/state', 'GET', undefined, project.id)).chapters.length, 3);
    await workspace.page.unroute('**/api/state');
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  for (const operation of ['new', 'copy', 'import']) {
    for (const failure of ['origin', 'target', 'both']) {
      await test(`${operation}保存后的${failure}读取失败重开不重复提交`, () => savedOpenRecovery(operation, failure));
    }
  }

  await test('参考导入保存后回读失败，重试只打开参考库', async () => {
    const project = await createProject('参考书导入恢复');
    const workspace = await openProject(project);
    await importPreview(workspace.page);
    await workspace.page.locator('#importBookMode').selectOption('reference');
    let saved = false;
    let fail = true;
    let posts = 0;
    await workspace.page.route('**/api/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/api/import/apply' && route.request().method() === 'POST') {
        posts += 1;
        const response = await route.fetch();
        saved = true;
        return route.fulfill({response});
      }
      if (saved && fail && pathname === '/api/references') {
        return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: '参考库读取模拟失败'})});
      }
      return route.continue();
    });
    await workspace.page.locator('[data-import-confirm]').click();
    await workspace.page.locator('.form-error').filter({hasText: '参考库读取模拟失败'}).waitFor();
    assert.equal(posts, 1);
    assert.equal(await workspace.page.locator('[data-import-confirm]').innerText(), '重新打开参考库');
    fail = false;
    await workspace.page.locator('[data-import-confirm]').click();
    await workspace.page.waitForFunction(() => view === 'references' && !document.querySelector('.modal-backdrop'));
    assert.equal(posts, 1);
    assert.equal((await api('/api/references', 'GET', undefined, project.id)).references.length, 1);
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  await test('导入名称200和章名500完整字符可键盘编辑，201和501在保存前拒绝', async () => {
    const project = await createProject('Unicode名称导入');
    const workspace = await openProject(project);
    const {page} = workspace;
    await importPreview(page, 'Unicode.txt', '第一章 原章\n正文😀首部，完整保留。\n第二章 原章\n正文尾部，完整保留。');
    let posts = 0;
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/import/apply' && request.method() === 'POST') posts += 1;
    });
    const name = '名'.repeat(198) + '😀' + '尾';
    const title = '章'.repeat(498) + '😀' + '尾';
    await page.locator('#importBookTitle').fill('名'.repeat(197) + '😀' + '尾');
    await page.locator('#importBookTitle').press('Home');
    await page.locator('#importBookTitle').press('ArrowRight');
    await page.keyboard.insertText('名');
    assert.equal(await page.locator('#importBookTitle').inputValue(), name);
    assert.equal(Array.from(await page.locator('#importBookTitle').inputValue()).length, 200);
    await page.locator('[data-import-title="0"]').fill('章'.repeat(497) + '😀' + '尾');
    await page.locator('[data-import-title="0"]').press('Home');
    await page.locator('[data-import-title="0"]').press('ArrowRight');
    await page.keyboard.insertText('章');
    assert.equal(await page.locator('[data-import-title="0"]').inputValue(), title);
    await page.locator('#importBookTitle').press('End');
    await page.keyboard.insertText('超');
    await page.locator('[data-import-confirm]').click();
    await page.locator('.form-error').filter({hasText: '不能超过200字'}).waitFor();
    assert.equal(posts, 0);
    await page.locator('#importBookTitle').fill(name);
    await page.locator('[data-import-title="0"]').press('End');
    await page.keyboard.insertText('超');
    await page.locator('[data-import-confirm]').click();
    await page.locator('.form-error').filter({hasText: '不能超过500字'}).waitFor();
    assert.equal(posts, 0);
    await page.locator('[data-import-title="0"]').fill(title);
    const originalBodies = await page.locator('[data-import-body]').evaluateAll(inputs => inputs.map(input => input.value));
    await page.locator('[data-import-body="0"]').evaluate(input => {
      input.focus();
      input.setSelectionRange(4, 4);
    });
    await page.locator('[data-import-split="0"]').click();
    await page.locator('[data-import-merge="0"]').click();
    assert.deepEqual(await page.locator('[data-import-body]').evaluateAll(inputs => inputs.map(input => input.value)), originalBodies);
    await page.locator('[data-import-title="0"]').fill(title);
    await page.locator('[data-import-confirm]').click();
    await page.waitForFunction(() => !document.querySelector('[data-import-confirm]'), null, {timeout: 15000});
    await waitSaved(page);
    const imported = await api('/api/state', 'GET', undefined, await page.evaluate(() => requestedProject));
    assert.equal(imported.project.title, name);
    assert.equal(imported.chapters[0].title, title);
    assert.deepEqual(imported.chapters.map(item => item.body), originalBodies);
    assert.equal(posts, 1);
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  await test('新建作品200完整字符名称可保存，超限不会发出创建请求', async () => {
    const project = await createProject('Unicode名称新建');
    const workspace = await openProject(project);
    const {page} = workspace;
    await page.locator('[data-view="library"]').click();
    await page.locator('[data-action="createProject"]').click();
    let posts = 0;
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/projects' && request.method() === 'POST') posts += 1;
    });
    const title = '名'.repeat(199) + '😀';
    await page.locator('#newBookTitle').fill(title + '超');
    await page.locator('.author-form [type="submit"]').click();
    await page.locator('.form-error').filter({hasText: '不能超过200字'}).waitFor();
    assert.equal(posts, 0);
    await page.locator('#newBookTitle').fill('名'.repeat(198) + '😀');
    await page.locator('#newBookTitle').press('Home');
    await page.locator('#newBookTitle').press('ArrowRight');
    await page.keyboard.insertText('名');
    await page.locator('.author-form [type="submit"]').click();
    await page.waitForFunction(() => !document.querySelector('.author-form'), null, {timeout: 15000});
    await waitSaved(page);
    assert.equal(await page.evaluate(() => state.project.title), title);
    assert.equal(posts, 1);
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  await test('已确认修订的问题可免费改解释，改稿后的旧问题也可结合当前稿解释归档', async () => {
    const project = await createProject('审稿作者解释', 2);
    await api('/api/ai/settings', 'PUT', {
      base_url: providerUrl, model: 'lifecycle-local-fixture', api_key: '', daily_limit: 1000,
      parameters: {temperature: 0, max_tokens: 2000},
    }, project.id);
    const queued = await api('/api/ai/queue', 'POST', {
      task: 'semantic_review', chapter_id: project.firstId, response_format: 'json', stream: false, defer: true,
    }, project.id);
    await api(`/api/ai/jobs/${queued.id}/process`, 'POST', {}, project.id);
    const workspace = await openProject(project);
    const {page} = workspace;
    await page.locator('[data-action="reviewChapter"]').click();
    await page.locator('[data-review-reason="0"]').fill('我确认先检查这里的动机。');
    await page.locator('[data-review-choice="0"]').selectOption('accepted');
    await page.locator('[data-review-decision-note="0"]').waitFor({state: 'visible'});
    assert.equal(await page.locator('[data-review-choice="0"]').isEnabled(), true);
    await page.locator('[data-review-reason="0"]').fill('前文的守门动作已经说明选择原因，保留这个写法。');
    const callsBefore = providerCalls;
    await page.locator('[data-review-choice="0"]').selectOption('explained');
    await page.waitForFunction(() => document.querySelector('[data-review-choice="0"]')?.disabled &&
      document.querySelector('[data-review-choice="0"]')?.value === 'explained' &&
      document.querySelector('[data-review-reason="0"]')?.readOnly);
    assert.equal(providerCalls, callsBefore);
    await page.locator('.analysis-modal [data-close]').click();
    await page.locator('#chapterBody').fill('她把红铜罗盘塞进衣袋，朝守门人点头，然后踏上开向渡口的空船。'.repeat(4));
    await waitSaved(page);
    await page.locator('[data-action="finishChapter"]').click();
    await page.waitForFunction(() => authorFinishing === null);
    assert.equal(await page.evaluate(() => activeChapter), project.firstId);
    assert.equal((await api('/api/state', 'GET', undefined, project.id)).chapters.length, 2);
    await page.locator('[data-action="reviewChapter"]').click();
    await page.locator('[data-review-reason="0"]').fill('已结合当前稿检查：点头和上船说明她主动离开，不需额外改写。');
    assert.equal(await page.locator('[data-review-choice="0"]').isEnabled(), true);
    assert.equal(await page.locator('[data-review-choice="0"] option[value="accepted"]').isDisabled(), true);
    assert.equal(await page.locator('[data-review-repair="0"]').isDisabled(), true);
    await page.locator('[data-review-choice="0"]').selectOption('explained');
    await page.waitForFunction(() => document.querySelector('[data-review-choice="0"]')?.disabled &&
      document.querySelector('[data-review-choice="0"]')?.value === 'explained' &&
      document.querySelector('[data-review-reason="0"]')?.readOnly);
    assert.equal(providerCalls, callsBefore);
    const review = (await api(`/api/story-reviews?chapter_id=${project.firstId}`, 'GET', undefined, project.id)).reviews[0];
    assert.equal(review.stale, true);
    assert.equal(review.findings[0].author_resolution_current, true);
    await page.screenshot({path: path.join(temporary, 'stale-review-author-explanation.png'), fullPage: false});
    await page.locator('.analysis-modal [data-close]').click();
    const nextId = (await api('/api/state', 'GET', undefined, project.id)).chapters[1].id;
    await finish(page, nextId);
    await page.locator(`[data-chapter="${project.firstId}"]`).click();
    await page.locator('#chapterBody').fill('正文再改一次，之前的解释不能继续自动放行。'.repeat(5));
    await waitSaved(page);
    await page.locator('[data-action="finishChapter"]').click();
    await page.waitForFunction(() => authorFinishing === null);
    assert.equal(await page.evaluate(() => activeChapter), project.firstId);
    const staleAgain = (await api(`/api/story-reviews?chapter_id=${project.firstId}`, 'GET', undefined, project.id)).reviews[0];
    assert.equal(staleAgain.findings[0].author_resolution_current, false);
    assert.equal(providerCalls, callsBefore);
    assert.deepEqual(workspace.errors, []);
    await workspace.context.close();
  });

  results.provider_calls = providerCalls;
  results.source_unchanged = results.source_files.every(file => file.sha256 === hash(fs.readFileSync(path.join(root, file.path))));
  assert.equal(results.source_unchanged, true, '测试期间被测源码不能变化');
  results.passed = true;
}

main().catch(error => {
  results.error = error.stack || error.message;
  results.server_log_tail = serverLog.slice(-5000);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close().catch(() => {});
  if (app && app.exitCode === null) {
    app.kill('SIGTERM');
    await new Promise(resolve => {
      app.once('exit', resolve);
      setTimeout(resolve, 3000);
    });
  }
  if (provider) await new Promise(resolve => provider.close(resolve));
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify(results, null, 2));
});
