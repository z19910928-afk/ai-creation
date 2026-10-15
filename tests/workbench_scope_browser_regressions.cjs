'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-workbench-scope-'));
const report = {passed: false, cases: [], artifacts: temporary, page_errors: [],
  scope: '真实桌面 Chrome、隔离数据库；设定保存、证据弹窗和持久化响应的作品范围',
  branch_identity_test: 'Synthetic frontend branch identity; no branch-management UI claim.',
  paid_provider_calls: 0};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, browser, base, firstChapterId, serverLog = '';

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}
async function until(fn, label) {
  for (let i = 0; i < 150; i += 1) {
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
async function openEditor(page, project = 'default-project') {
  await page.evaluate(id => authorSwitchProject(id), project);
  await page.locator('[data-view="editor"]').click();
  if (project === 'default-project') {
    await page.locator('[data-library-tab="chapters"]').click();
    await page.locator(`[data-chapter="${firstChapterId}"]`).click();
  }
  await saved(page);
}
async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await fn();
  report.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, passed: true}));
}
function gate() {
  let release, entered;
  return {held: new Promise(resolve => {release = resolve;}),
    ready: new Promise(resolve => {entered = resolve;}),
    release: () => release(), entered: () => entered()};
}
async function retainEditor(page) {
  return page.evaluate(() => {
    const body = document.querySelector('#chapterBody');
    body.focus(); body.setSelectionRange(19, 43, 'backward'); body.scrollTop = 330;
    captureSelection(); globalThis.__scopeEditor = body;
    return {body: body.value, start: body.selectionStart, end: body.selectionEnd,
      direction: body.selectionDirection, scroll: body.scrollTop,
      focused: document.body.classList.contains('workbench-focus')};
  });
}
async function editorSnapshot(page) {
  return page.evaluate(() => {
    const body = document.querySelector('#chapterBody');
    return {same: body === globalThis.__scopeEditor, body: body.value, start: body.selectionStart,
      end: body.selectionEnd, direction: body.selectionDirection, scroll: body.scrollTop,
      focused: document.body.classList.contains('workbench-focus')};
  });
}
async function syntheticBranch(page, branch) {
  await page.evaluate(value => {
    clearTimeout(syncTimer); state.branch_id = value; confirmedState = MoyuSync.copy(state); render();
  }, branch);
}
async function main() {
  const port = await freePort(); base = `http://127.0.0.1:${port}`;
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {cwd: root,
    env: {...process.env, MOYU_DB: path.join(temporary, 'story.sqlite3'), MOYU_HOST: '127.0.0.1',
      MOYU_PORT: String(port), MOYU_AI_BASE_URL: 'http://127.0.0.1:1/v1', MOYU_AI_API_KEY: '',
      MOYU_SECRET_KEY: '', MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials')},
    stdio: ['ignore', 'ignore', 'pipe']});
  app.stderr.on('data', chunk => {serverLog += chunk.toString();});
  await until(async () => {try {return (await fetch(base + '/api/health')).ok;} catch {return false;}}, 'startup');

  const initial = await api('/api/state'), first = initial.chapters[0];
  firstChapterId = first.id;
  const quote = '沈砚握住旧钥匙，站在渡口的仓门外。';
  first.body = quote + '\n\n' + '雨水从屋檐坠下。他没有敲门，只把沾泥的信压在门缝前。\n'.repeat(160);
  initial.chapters.push({...first, id: 'workbench_scope_second', title: '第二章 渡口',
    body: '第二章独立正文，切换后应当只查看本章资料。'});
  await api('/api/state', 'PUT', initial);
  const record = await api('/api/records', 'POST', {type: '地点', name: '旧仓库', description: '临近渡口。'});
  await api('/api/records', 'POST', {type: '人物', name: '沈砚', description: '携带旧钥匙。'});
  const memory = await api('/api/memory', 'POST', {type: '人物', name: '沈砚', status: 'accepted',
    source_chapter_id: first.id, source_quote: quote, source_start: 0, source_end: Array.from(quote).length});
  assert.ok(record.id && memory.id && memory.assertion_id);
  const other = await api('/api/projects', 'POST', {title: '隔离作品'});
  const otherId = other.project?.id || other.state?.project?.id;
  assert.ok(otherId);

  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage(); page.on('pageerror', error => report.page_errors.push(error.message));
  await page.goto(base); await page.waitForFunction(() => backendOnline); await openEditor(page);

  await test('1440 与 1280 电脑设定保存只刷新资料，保留正文节点、选区、滚动和专注状态', async () => {
    for (const [index, viewport] of [{width: 1440, height: 1000}, {width: 1280, height: 900}].entries()) {
      await page.setViewportSize(viewport);
      if (index) await page.locator('[data-workbench-focus]').click();
      await page.locator('[data-library-tab="knowledge"]').evaluate(button => button.click());
      const before = await retainEditor(page);
      await page.evaluate(id => editRecord(id), record.id);
      await page.locator('#recordName').fill(index ? '渡口仓库终稿' : '渡口仓库');
      await page.locator('#recordDesc').fill('作者在写作中补全此处门窗与守卫的信息。');
      await page.locator('.author-form [type="submit"]').click();
      await page.locator('.author-form').waitFor({state: 'detached'}); await saved(page);
      assert.deepEqual(await editorSnapshot(page), {same: true, ...before});
      assert.equal(await page.locator(`[data-record="${record.id}"] strong`).textContent(), index ? '渡口仓库终稿' : '渡口仓库');
      assert.equal((await api('/api/state')).records.find(row => row.id === record.id).name, index ? '渡口仓库终稿' : '渡口仓库');
      if (index) await page.locator('[data-workbench-focus]').click();
    }
  });
  await test('设定在知识库页面保存后仍刷新列表', async () => {
    await page.locator('[data-view="world"]').click();
    await page.evaluate(id => editRecord(id), record.id);
    await page.locator('#recordDesc').fill('知识库页面保存后也保留新描述。');
    await page.locator('.author-form [type="submit"]').click();
    await page.locator('.author-form').waitFor({state: 'detached'}); await saved(page);
    assert.match(await page.locator('#main').textContent(), /知识库页面保存后也保留新描述/);
    await openEditor(page);
  });
  await test('先后查看两次证据时只显示最新请求，关闭后不重建正文', async () => {
    const block = gate(); let held = false;
    const routePath = `**/api/memory/${memory.id}`;
    await page.route(routePath, async route => {
      if (!held) {
        held = true; const response = await route.fetch(); block.entered(); await block.held;
        return route.fulfill({response});
      }
      return route.continue();
    });
    await page.evaluate(id => {globalThis.__firstEvidence = showMemoryEvidence(id);}, memory.id);
    await block.ready;
    await page.evaluate(id => showMemoryEvidence(id), memory.id);
    block.release(); await page.evaluate(() => globalThis.__firstEvidence); await page.unroute(routePath);
    assert.equal(await page.locator('[data-memory-evidence-dialog]').count(), 1);
    const before = await retainEditor(page);
    await page.locator('[data-memory-evidence-dialog] [data-close]').click();
    assert.deepEqual(await editorSnapshot(page), {same: true, ...before});
  });
  for (const transition of ['page', 'chapter', 'project', 'branch']) {
    await test(`证据迟到时 ${transition} 切换使旧结果失效`, async () => {
      await openEditor(page);
      const block = gate(), routePath = `**/api/memory/${memory.id}`;
      await page.route(routePath, async route => {
        assert.equal(route.request().headers()['x-moyu-project'], 'default-project');
        assert.equal(route.request().headers()['x-moyu-branch'], 'main');
        const response = await route.fetch(); block.entered(); await block.held; await route.fulfill({response});
      });
      await page.evaluate(id => {globalThis.__lateEvidence = showMemoryEvidence(id);}, memory.id);
      await block.ready;
      if (transition === 'page') await page.locator('[data-view="library"]').click();
      if (transition === 'chapter') await page.locator('[data-chapter="workbench_scope_second"]').click();
      if (transition === 'project') await page.evaluate(id => authorSwitchProject(id), otherId);
      if (transition === 'branch') await syntheticBranch(page, 'scope_synthetic_other');
      block.release(); await page.evaluate(() => globalThis.__lateEvidence); await page.unroute(routePath);
      assert.equal(await page.locator('[data-memory-evidence-dialog]').count(), 0);
      if (transition === 'branch') {await syntheticBranch(page, 'main'); await page.evaluate(() => hydrate());}
    });
  }
  await test('已经打开的旧作品证据失效后不能通过遗留按钮修改正典', async () => {
    await openEditor(page); await page.evaluate(id => showMemoryEvidence(id), memory.id);
    await page.locator('[data-memory-evidence-dialog] [data-assertion-review]').waitFor();
    await page.evaluate(() => {globalThis.__oldEvidenceDialog = document.querySelector('[data-memory-evidence-dialog]');});
    await page.evaluate(id => authorSwitchProject(id), otherId);
    assert.equal(await page.locator('[data-memory-evidence-dialog]').count(), 0);
    let prompts = 0, mutations = 0;
    const dialog = async item => {prompts += 1; await item.dismiss();};
    const request = item => {if (item.method() === 'POST' && item.url().includes('/api/assertions/')) mutations += 1;};
    page.on('dialog', dialog); page.on('request', request);
    await page.evaluate(() => {
      document.body.append(globalThis.__oldEvidenceDialog);
      globalThis.__oldEvidenceDialog.querySelector('[data-assertion-review]').click();
    });
    assert.equal(await page.locator('[data-memory-evidence-dialog]').count(), 0);
    assert.equal(prompts, 0); assert.equal(mutations, 0);
    page.off('dialog', dialog); page.off('request', request);
  });
  await test('同一作品正常证据审查仍保存且只保留一个新弹窗，取消理由不提交', async () => {
    await openEditor(page); await page.evaluate(id => showMemoryEvidence(id), memory.id);
    let promptIndex = 0, mutations = 0;
    const request = item => {if (item.method() === 'POST' && item.url().includes('/api/assertions/')) mutations += 1;};
    const cancel = async item => {promptIndex += 1; await (promptIndex === 1 ? item.accept('candidate') : item.dismiss());};
    page.on('request', request); page.on('dialog', cancel);
    await page.locator('[data-memory-evidence-dialog] [data-assertion-review]').click();
    assert.equal(mutations, 0); page.off('dialog', cancel);
    const accept = item => item.accept(item.message().includes('输入状态') ? 'candidate' : '证据需要再次确认。');
    page.on('dialog', accept);
    await page.locator('[data-memory-evidence-dialog] [data-assertion-review]').click();
    await page.waitForFunction(() => document.querySelector('[data-assertion-review]')?.dataset.assertionState === 'candidate');
    assert.equal(mutations, 1); assert.equal(await page.locator('[data-memory-evidence-dialog]').count(), 1);
    assert.equal((await api(`/api/assertions/${memory.assertion_id}`)).state, 'candidate');
    page.off('dialog', accept); page.off('request', request);
    await page.locator('[data-memory-evidence-dialog] [data-close]').click(); await saved(page);
  });
  await test('持久化请求排队期间切版本时不发送到新版本', async () => {
    await page.evaluate(() => {
      operationChain = new Promise(resolve => {globalThis.__releaseOperation = resolve;});
      globalThis.__queuedOperation = durableOperation('/api/records', 'POST',
        {type: '物品', name: '排队中的旧版本物品', description: ''}).then(() => null, error => error.message);
    });
    await syntheticBranch(page, 'scope_queued_other');
    await page.evaluate(() => globalThis.__releaseOperation());
    assert.match(await page.evaluate(() => globalThis.__queuedOperation), /作品或版本已切换/);
    assert.ok(!(await api('/api/state')).records.some(row => row.name === '排队中的旧版本物品'));
    await syntheticBranch(page, 'main'); await page.evaluate(() => hydrate());
  });
  for (const heldStage of ['mutation', 'state-read']) {
    await test(`持久化 ${heldStage} 响应迟到时不合并进另一版本`, async () => {
      await openEditor(page);
      const block = gate(), routePath = heldStage === 'mutation' ? `**/api/records/${record.id}` : '**/api/state';
      let held = false;
      await page.route(routePath, async route => {
        if (heldStage === 'state-read' && route.request().method() !== 'GET') return route.continue();
        if (held) return route.continue();
        held = true;
        assert.equal(route.request().headers()['x-moyu-branch'], 'main');
        const response = await route.fetch(); block.entered(); await block.held; await route.fulfill({response});
      });
      await page.evaluate(({id, stage}) => {
        globalThis.__scopeOperation = durableOperation('/api/records/' + id, 'PATCH',
          {description: '原版本已保存：' + stage});
      }, {id: record.id, stage: heldStage});
      await block.ready; await syntheticBranch(page, 'scope_late_other');
      const before = await page.evaluate(() => JSON.stringify({state, confirmedState}));
      block.release(); await page.evaluate(() => globalThis.__scopeOperation); await page.unroute(routePath);
      assert.equal(await page.evaluate(() => JSON.stringify({state, confirmedState})), before);
      assert.equal((await api('/api/state')).records.find(row => row.id === record.id).description, '原版本已保存：' + heldStage);
      await syntheticBranch(page, 'main'); await page.evaluate(() => hydrate());
    });
  }
  await test('长篇目录切到后章和前章后保持当前章可见，规划回读保留目录位置', async () => {
    const created = await api('/api/projects', 'POST', {title: '长篇目录位置验证'});
    const owner = created.project?.id || created.state?.project?.id;
    const book = await api('/api/state', 'GET', undefined, owner);
    const template = book.chapters[0];
    book.chapters = Array.from({length: 80}, (_, index) => ({
      ...template, id: 'workbench_directory_' + index, title: `第${index + 1}章 独立目录位置`,
      body: `第${index + 1}章正文保持不变。`,
    }));
    await api('/api/state', 'PUT', book, owner);
    await page.locator('[data-view="library"]').click();
    await page.locator(`[data-project-open="${owner}"]`).click();
    await page.locator('[data-view="editor"]').click(); await saved(page);
    for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 900}]) {
      await page.setViewportSize(viewport);
      for (const index of [59, 79, 0, 78, 1, 32]) {
        const id = book.chapters[index].id;
        await page.locator(`[data-chapter="${id}"]`).click();
        await page.waitForFunction(id => activeChapter === id, id);
        const before = await page.evaluate(() => {
          const library = document.querySelector('.workbench-library');
          const bounds = library.getBoundingClientRect();
          const selected = library.querySelector('.chapter-select.active').getBoundingClientRect();
          return {scroll: library.scrollTop, visible: selected.top >= bounds.top - 1 &&
            selected.bottom <= bounds.bottom + 1, pageScroll: window.scrollY};
        });
        assert.ok(before.visible, JSON.stringify({id, viewport, before}));
        await page.evaluate(async () => {
          const data = await authorJSON('/api/blueprint', {headers: {
            'X-Moyu-Project': requestedProject, 'X-Moyu-Branch': state.branch_id || 'main'}});
          authorReceiveBlueprint(data);
        });
        const after = await page.evaluate(() => ({
          scroll: document.querySelector('.workbench-library').scrollTop, pageScroll: window.scrollY,
        }));
        assert.ok(Math.abs(after.scroll - before.scroll) <= 1, JSON.stringify({before, after}));
        assert.equal(after.pageScroll, before.pageScroll);
        assert.equal(await page.locator('#chapterBody').inputValue(), book.chapters[index].body);
      }
    }
    assert.deepEqual((await api('/api/state', 'GET', undefined, owner)).chapters.map(row => row.body),
      book.chapters.map(row => row.body));
  });
  assert.deepEqual(report.page_errors, []); report.passed = true; await context.close();
}
main().catch(error => {report.error = error.stack; report.server_log = serverLog.slice(-3000); process.exitCode = 1;})
  .finally(async () => {
    if (browser) await browser.close();
    if (app && app.exitCode === null) {
      const exited = new Promise(resolve => app.once('exit', resolve)); app.kill('SIGTERM'); await exited;
    }
    fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  });
