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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-editor-preferences-'));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const report = {passed: false, cases: [], artifacts: temporary, page_errors: [],
  scope: '真实桌面 Chrome、隔离数据库；逐章位置、阅读偏好、来源定位和浏览器存储失败',
  branch_identity_test: 'Synthetic frontend branch identity; no branch-management UI claim.',
  paid_provider_calls: 0, model_request_posts: 0, browser_mutations: [],
  source_files: ['author-workspace.js', 'editor-preferences.css',
    'tests/editor_preferences_browser_regressions.cjs'].map(file => ({
    path: file, sha256: hash(fs.readFileSync(path.join(root, file))),
  }))};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, browser, base, serverLog = '';

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}
async function until(fn, label) {
  for (let index = 0; index < 150; index += 1) {
    if (app?.exitCode !== null) throw new Error('Server exited: ' + serverLog.slice(-2000));
    const value = await fn();
    if (value) return value;
    await pause(100);
  }
  throw new Error('Timed out: ' + label);
}
async function api(route, method = 'GET', body, project = 'default-project') {
  const response = await fetch(base + route, {method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(data)}`);
  return data;
}
async function saved(page) {
  await page.waitForFunction(() => backendOnline && confirmedState && !isDirty() &&
    !syncInFlight && !independentWrite && !mergePending && !saveError);
}
async function selectChapter(page, id) {
  await page.locator('[data-library-tab="chapters"]').click();
  await page.locator(`[data-chapter="${id}"]`).click();
  await page.waitForFunction(value => activeChapter === value && !!document.querySelector('#chapterBody'), id);
}
async function openProject(page, id) {
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${id}"]`).click();
  await page.waitForFunction(value => requestedProject === value && backendOnline, id);
  await page.locator('[data-view="editor"]').click(); await saved(page);
}
async function position(page) {
  return page.evaluate(() => {
    const input = document.querySelector('#chapterBody');
    return {start: input.selectionStart, end: input.selectionEnd, direction: input.selectionDirection,
      scroll_top: input.scrollTop, scroll_left: input.scrollLeft,
      panel_top: input.closest('.workbench-manuscript').scrollTop};
  });
}
async function setPosition(page, start, end, top, panel = 80) {
  await page.locator('#chapterBody').evaluate((input, value) => {
    input.focus({preventScroll: true}); input.setSelectionRange(value.start, value.end, 'backward');
    input.scrollTop = value.top; input.closest('.workbench-manuscript').scrollTop = value.panel;
    input.dispatchEvent(new Event('select'));
  }, {start, end, top, panel});
  await pause(180);
  return position(page);
}
function assertPosition(actual, expected) {
  for (const key of ['start', 'end', 'direction']) assert.equal(actual[key], expected[key], key);
  for (const key of ['scroll_top', 'scroll_left', 'panel_top']) {
    assert.ok(Math.abs(actual[key] - expected[key]) <= 1, `${key}: ${JSON.stringify({actual, expected})}`);
  }
}
async function preference(page, key, value) {
  await page.locator('[data-editor-preference="' + key + '"]').evaluate((input, next) => {
    input.value = String(next); input.dispatchEvent(new Event('input', {bubbles: true}));
  }, value);
}
async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn(); report.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, passed: true}));
}

async function main() {
  const port = await freePort(); base = `http://127.0.0.1:${port}`;
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {cwd: root,
    env: {...process.env, MOYU_DB: path.join(temporary, 'story.sqlite3'), MOYU_HOST: '127.0.0.1',
      MOYU_PORT: String(port), MOYU_AI_BASE_URL: 'http://127.0.0.1:1/v1', MOYU_AI_API_KEY: '',
      MOYU_SECRET_KEY: '', MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials'),
      MOYU_BACKUP_DIR: path.join(temporary, 'backups')},
    stdio: ['ignore', 'ignore', 'pipe']});
  app.stderr.on('data', chunk => {serverLog += chunk.toString();});
  await until(async () => {try {return (await fetch(base + '/api/health')).ok;} catch {return false;}}, 'startup');

  const firstState = await api('/api/state'), first = firstState.chapters[0];
  const quote = '他把铜钥匙交给守门人，换来一张空白的船票。';
  first.body = '第一章原稿😀。\n\n' + '雨落在渡口，林岚沿着石阶走向码头。\n'.repeat(16) +
    quote + '\n\n' + '他沿着河岸走，逐一查看系在木桩上的船只。\n'.repeat(170);
  const secondId = 'editor_position_second';
  firstState.chapters.push({...first, id: secondId, title: '第二章 独立阅读位置',
    body: '第二章原稿。\n\n' + '风掠过钟楼，信纸上写着另一条路线。\n'.repeat(180)});
  await api('/api/state', 'PUT', firstState);
  const created = await api('/api/projects', 'POST', {title: '阅读位置隔离作品'});
  const otherId = created.project?.id || created.state?.project?.id;
  const otherState = await api('/api/state', 'GET', undefined, otherId);
  otherState.chapters[0].body = '另一部作品的正文。\n' + '这条河向东流，另一座城还没有开门。\n'.repeat(180);
  await api('/api/state', 'PUT', otherState, otherId);
  const baselines = new Map();
  for (const id of ['default-project', otherId]) baselines.set(id, await api('/api/state', 'GET', undefined, id));

  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) {
      report.browser_mutations.push({path: pathname, method: request.method()});
      if (pathname.startsWith('/api/ai/') || pathname.startsWith('/api/writing-agent')) report.model_request_posts += 1;
    }
  });
  await page.goto(base); await page.waitForFunction(() => backendOnline);
  await page.evaluate(() => authorSwitchProject('default-project'));
  await page.locator('[data-view="editor"]').click(); await selectChapter(page, first.id); await saved(page);
  await page.waitForFunction(() => [...document.styleSheets].some(sheet =>
    sheet.href && new URL(sheet.href).pathname === '/editor-preferences.css'));

  await test('1440x1000 与 1280x800 阅读偏好即时应用，不替换正文节点或修改稿件', async () => {
    for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 800}]) {
      await page.setViewportSize(viewport);
      await page.evaluate(() => {globalThis.__originalEditor = document.querySelector('#chapterBody');});
      await setPosition(page, 520, 550, 390);
      await page.locator('.editor-reading-settings>summary').click();
      const before = await position(page);
      await preference(page, 'font_size', 19); await preference(page, 'line_height', 2.3);
      await preference(page, 'content_width', 600);
      const actual = await page.evaluate(() => {
        const input = document.querySelector('#chapterBody'),style = getComputedStyle(input);
        const panel = document.querySelector('.editor-reading-panel').getBoundingClientRect();
        return {same: input === globalThis.__originalEditor, font: style.fontSize, line: style.lineHeight,
          width: parseFloat(style.maxWidth), overflow: document.documentElement.scrollWidth > innerWidth,
          panel_visible: panel.left >= 0 && panel.right <= innerWidth && panel.bottom <= innerHeight,
          dirty: isDirty(), revision: chapter().revision_id};
      });
      assert.equal(actual.same, true); assert.equal(actual.font, '19px');
      assert.ok(Math.abs(parseFloat(actual.line) - 43.7) < .1);
      assert.equal(actual.width, 600); assert.equal(actual.overflow, false); assert.equal(actual.panel_visible, true);
      assert.equal(actual.dirty, false);
      assertPosition(await position(page), before);
      const screenshot = path.join(temporary, `editor-preferences-${viewport.width}x${viewport.height}.png`);
      await page.screenshot({path: screenshot}); report.screenshots = [...(report.screenshots || []), screenshot];
      await page.keyboard.press('Escape');
    }
  });

  let firstPosition, secondPosition;
  await test('切章分别恢复选区方向、正文滚动和编辑区滚动，按旧编辑框身份保存', async () => {
    firstPosition = await setPosition(page, 620, 650, 470, 105);
    await selectChapter(page, secondId);
    assert.equal((await position(page)).start, 0);
    secondPosition = await setPosition(page, 1200, 1230, 810, 155);
    await selectChapter(page, first.id); assertPosition(await position(page), firstPosition);
    await selectChapter(page, secondId); assertPosition(await position(page), secondPosition);
    const identities = await page.evaluate(() => [...authorEditorPositions.keys()].map(value => JSON.parse(value)));
    assert.ok(identities.some(row => row[0] === 'default-project' && row[1] === 'main' && row[2] === first.id));
    assert.ok(identities.some(row => row[0] === 'default-project' && row[1] === 'main' && row[2] === secondId));
  });

  await test('同章重新绘制保留光标、选区与滚动，刷新后恢复本机偏好与位置', async () => {
    await page.evaluate(() => {document.querySelector('#chapterBody').focus({preventScroll: true}); render();});
    assertPosition(await position(page), secondPosition);
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'chapterBody');
    await page.reload(); await page.waitForFunction(() => backendOnline);
    await page.locator('[data-view="editor"]').click(); await saved(page);
    assert.equal(await page.evaluate(() => activeChapter), secondId);
    assertPosition(await position(page), secondPosition);
    const styles = await page.locator('#chapterBody').evaluate(input => ({
      font: getComputedStyle(input).fontSize, line: getComputedStyle(input).lineHeight,
      width: getComputedStyle(input).maxWidth,
    }));
    assert.equal(styles.font, '19px'); assert.equal(styles.width, '600px');
    assert.ok(Math.abs(parseFloat(styles.line) - 43.7) < .1);
  });

  await test('切换真实作品后各自恢复位置，切换前的位置不会写到另一作品', async () => {
    await openProject(page, otherId);
    assert.equal((await position(page)).start, 0);
    const otherPosition = await setPosition(page, 380, 410, 350, 65);
    await openProject(page, 'default-project');
    assertPosition(await position(page), secondPosition);
    await openProject(page, otherId);
    assertPosition(await position(page), otherPosition);
    await openProject(page, 'default-project');
  });

  await test('同一章节编号在不同前端分支隔离，恢复主线不套用另一分支位置', async () => {
    const mainPosition = await setPosition(page, 1000, 1030, 700, 95);
    await page.evaluate(() => {
      clearTimeout(syncTimer); backendOnline = false; state.branch_id = 'editor-position-synthetic';
      confirmedState = MoyuSync.copy(state); render();
    });
    assert.equal((await position(page)).start, 0);
    const branchPosition = await setPosition(page, 220, 240, 220, 45);
    await page.evaluate(() => {state.branch_id = 'main'; confirmedState = MoyuSync.copy(state); render();});
    assertPosition(await position(page), mainPosition);
    await page.evaluate(() => {state.branch_id = 'editor-position-synthetic'; confirmedState = MoyuSync.copy(state); render();});
    assertPosition(await position(page), branchPosition);
    await page.evaluate(() => {state.branch_id = 'main'; confirmedState = MoyuSync.copy(state); backendOnline = true; render();});
    secondPosition = mainPosition; await saved(page);
  });

  await test('删除身份或同名新章不继承其他章位置，正文变化不恢复旧选区', async () => {
    await page.evaluate(() => {
      globalThis.__positionOriginalState = MoyuSync.copy(state);
      globalThis.__positionOriginalConfirmed = MoyuSync.copy(confirmedState);
      clearTimeout(syncTimer); backendOnline = false;
      const previous = chapter(); state.chapters = state.chapters.filter(row => row.id !== previous.id);
      state.chapters.push({...previous, id: 'editor_position_recreated'});
      activeChapter = 'editor_position_recreated'; render();
    });
    assert.equal((await position(page)).start, 0); assert.equal((await position(page)).scroll_top, 0);
    await page.evaluate(() => {
      state = MoyuSync.copy(globalThis.__positionOriginalState);
      confirmedState = MoyuSync.copy(globalThis.__positionOriginalConfirmed);
      activeChapter = state.chapters[1].id; render();
    });
    assertPosition(await position(page), secondPosition);
    await page.evaluate(() => {chapter().body = '同一章节已换成较短的新稿。'; render();});
    assert.equal((await position(page)).start, 0); assert.equal((await position(page)).scroll_top, 0);
    await page.evaluate(() => {
      state = MoyuSync.copy(globalThis.__positionOriginalState);
      confirmedState = MoyuSync.copy(globalThis.__positionOriginalConfirmed);
      backendOnline = true; activeChapter = state.chapters[1].id; render();
    });
    await saved(page);
  });

  await test('证据、审稿和全文搜索的明确原句定位优先于历史阅读位置', async () => {
    await selectChapter(page, first.id); await setPosition(page, 2400, 2430, 1800, 115);
    await selectChapter(page, secondId);
    const range = await page.evaluate(({id, value}) => {
      const text = chapter(id).body, start = MoyuSync.codepointOffset(text, text.indexOf(value));
      return {start, end: start + Array.from(value).length};
    }, {id: first.id, value: quote});
    await page.evaluate(({id, value, range}) => authorJumpToEvidence({
      source_chapter_id: id, source_quote: value, source_start: range.start, source_end: range.end,
    }), {id: first.id, value: quote, range});
    const selected = () => page.locator('#chapterBody').evaluate(input => input.value.slice(input.selectionStart, input.selectionEnd));
    assert.equal(await selected(), quote); await pause(250); assert.equal(await selected(), quote);
    await page.evaluate(() => render()); assert.equal(await selected(), quote);
    await selectChapter(page, secondId);
    await page.evaluate(({id, range}) => authorReviewSelectSource({chapter_id: id, range}), {id: first.id, range});
    assert.equal(await selected(), quote);
    await selectChapter(page, secondId);
    await page.evaluate(async ({id, value, range}) => {
      const c = chapter(id),button = document.createElement('button');
      Object.assign(button.dataset, {searchJump: id, searchStart: String(range.start), searchEnd: String(range.end),
        searchQuote: value, searchRevision: c.revision_id, searchBodyHash: await sha256(c.body)});
      await openSearchEvidence(button);
    }, {id: first.id, value: quote, range});
    assert.equal(await selected(), quote); await pause(250); assert.equal(await selected(), quote);
    await page.evaluate(() => render()); assert.equal(await selected(), quote);
  });

  await test('存储写入失败时设置与切章仍可用，并明确提示未能记住', async () => {
    await page.evaluate(() => {
      globalThis.__originalStorageSet = Storage.prototype.setItem;
      Storage.prototype.setItem = function() {throw new DOMException('Blocked for fixture', 'QuotaExceededError');};
    });
    await page.locator('.editor-reading-settings>summary').click(); await preference(page, 'font_size', 21);
    assert.equal(await page.locator('#chapterBody').evaluate(input => getComputedStyle(input).fontSize), '21px');
    assert.match(await page.locator('[data-editor-preferences-status]').textContent(), /未能记住/);
    await page.keyboard.press('Escape');
    const before = await setPosition(page, 100, 120, 160, 30);
    await selectChapter(page, secondId); await selectChapter(page, first.id);
    assertPosition(await position(page), before);
    await page.evaluate(() => {Storage.prototype.setItem = globalThis.__originalStorageSet;});
    await page.locator('.editor-reading-settings>summary').click();
    await page.locator('[data-editor-preferences-reset]').click();
    assert.equal(await page.locator('#chapterBody').evaluate(input => getComputedStyle(input).fontSize), '16px');
    await page.keyboard.press('Escape');
  });

  await test('存储读取和写入都被禁止时能够打开稿件并调整阅读设置', async () => {
    const blockedContext = await browser.newContext({viewport: {width: 1280, height: 800}});
    await blockedContext.addInitScript(() => {
      for (const method of ['getItem', 'setItem']) Storage.prototype[method] = function() {
        throw new DOMException('Storage denied for fixture', 'SecurityError');
      };
    });
    const blocked = await blockedContext.newPage();
    blocked.on('pageerror', error => report.page_errors.push(error.message));
    await blocked.goto(base); await blocked.waitForFunction(() => backendOnline && !!confirmedState);
    await blocked.locator('[data-view="editor"]').click();
    assert.equal(await blocked.locator('#chapterBody').evaluate(input => getComputedStyle(input).fontSize), '16px');
    await blocked.locator('.editor-reading-settings>summary').click();
    await preference(blocked, 'font_size', 18);
    assert.equal(await blocked.locator('#chapterBody').evaluate(input => getComputedStyle(input).fontSize), '18px');
    assert.match(await blocked.locator('[data-editor-preferences-status]').textContent(), /未能记住/);
    await blockedContext.close();
  });

  await test('阅读操作不产生正文版本、写接口或模型调用', async () => {
    await saved(page);
    for (const [id, before] of baselines) {
      const after = await api('/api/state', 'GET', undefined, id);
      assert.equal(after.version, before.version); assert.equal(after.head_revision, before.head_revision);
      assert.deepEqual(after.chapters, before.chapters);
    }
    assert.deepEqual(report.browser_mutations, []);
    assert.equal(report.model_request_posts, 0); assert.deepEqual(report.page_errors, []);
  });
  report.source_unchanged = report.source_files.every(file => hash(fs.readFileSync(path.join(root, file.path))) === file.sha256);
  assert.equal(report.source_unchanged, true); report.passed = true; await context.close();
}

main().catch(error => {report.error = error.stack; report.server_log = serverLog.slice(-4000); process.exitCode = 1;})
  .finally(async () => {
    if (browser) await browser.close();
    if (app && app.exitCode === null) {
      const exited = new Promise(resolve => app.once('exit', resolve)); app.kill('SIGTERM'); await exited;
    }
    fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  });
