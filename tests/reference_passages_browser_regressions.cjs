'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-reference-browser-'));
const database = path.join(temporary, 'synthetic.sqlite3');
const python = process.env.MOYU_TEST_PYTHON || 'python3';
const reportPath = process.env.MOYU_REFERENCE_BROWSER_REPORT || path.join(temporary, 'results.json');
const screenshotDirectory = process.env.MOYU_REFERENCE_BROWSER_SCREENSHOT_DIR ||
  (process.env.MOYU_REFERENCE_BROWSER_REPORT ? path.dirname(reportPath) : temporary);
if (fs.existsSync(reportPath)) throw new Error('Evidence exists; choose a new report path');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const sourceFiles = [
  'app.js', 'author-workspace.js', 'styles.css', 'server.py', 'moyu_import.py',
  'moyu_context.py', 'moyu_materials.py', 'tests/reference_passages_browser_regressions.cjs',
];
const fingerprints = () => Object.fromEntries(sourceFiles.map(file =>
  [file, hash(fs.readFileSync(path.join(root, file)))]));
const results = {
  passed: false, started_at: new Date().toISOString(), synthetic_only: true,
  scope: '真实电脑Chrome、临时合成作品、隔离SQLite与免费本机模拟提供方；手选参考片段全流程',
  claim_limit: '验证来源、交互和错误保护，不证明文学效果或第一梯队地位',
  paid_provider_calls: 0, cases: [], screenshots: [], artifacts: temporary,
  source_files_sha256_before: fingerprints(),
};
const quoteA = '檐雨原句：林岚用指节推回铜铃，听见屋檐落雨。😀 铜铃没有离开木桌。\n';
const quoteB = '钟楼原句：门外的人停下脚步，钟声穿过石阶，林岚仍守在木桌旁。\n';
const boundary = '边界' + '😀'.repeat(898);
const overlong = '超限' + '长'.repeat(899);
const prose = ('林岚把手留在木桌边，雨声绕过屋檐。门外的人走近一步，铜铃轻响，她抬头望向门口。').repeat(30).slice(0, 250);
const providerRequests = [], queuePayloads = [], previewPayloads = [], contextRequests = [], pageErrors = [];
let app, provider, browser, base, serverLog = '', page, firstChapter, secondChapter, otherProject, referenceId;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort() {
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return port;
}

async function api(route, method = 'GET', body, project = 'default-project') {
  const response = await fetch(base + route, {
    method, headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  assert.equal(response.ok, true, `${route}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}

async function databaseFingerprint() {
  const script = [
    'import hashlib, json, sqlite3, sys',
    'conn=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)',
    'conn.execute("PRAGMA query_only=ON")',
    'conn.execute("BEGIN")',
    'tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=\\\'table\\\' ORDER BY name")]',
    'counts={name:conn.execute(\'SELECT COUNT(*) FROM "\'+name+\'"\').fetchone()[0] for name in tables}',
    'value={"dump_sha256":hashlib.sha256("\\n".join(conn.iterdump()).encode()).hexdigest(),"counts":counts}',
    'print(json.dumps(value,sort_keys=True))',
    'conn.close()',
  ].join('\n');
  return new Promise((resolve, reject) => {
    let output = '', errorOutput = '';
    const child = spawn(python, ['-c', script, database], {stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.on('data', value => {output += value;});
    child.stderr.on('data', value => {errorOutput += value;});
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve(JSON.parse(output)) :
      reject(new Error('Synthetic database fingerprint failed: ' + errorOutput)));
  });
}

async function test(name, action) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await action();
  results.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, state: 'passed'}));
}

async function waitHeld(read, label) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (read()) return;
    await pause(25);
  }
  throw new Error(label + ' was not captured');
}

async function openCandidate() {
  await page.locator('[data-chat-result="workbenchCandidateSection"]').click();
  await page.locator('#aiReferenceTools').waitFor();
}

async function editor() {
  await page.locator('[data-view="editor"]').click();
  await openCandidate();
}

async function closeModals() {
  await page.evaluate(() => document.querySelectorAll('.modal-backdrop').forEach(wrap => wrap.remove()));
}

async function pick(query, quote) {
  await closeModals();
  await openCandidate();
  await page.locator('[data-reference-pick]').click();
  await page.locator('#referenceDocument').waitFor();
  await page.waitForFunction(() => document.querySelector('#referenceDocument')?.disabled === false);
  await page.locator('#referenceDocument').selectOption(referenceId);
  await page.locator('#referenceQuery').fill(query);
  await page.locator('[data-reference-search]').click();
  const result = page.locator('.reference-search-result').filter({hasText: quote}).first();
  await result.waitFor();
  await result.locator('[data-reference-use]').click();
  await page.locator('[data-reference-editor]').click();
  await openCandidate();
  await page.locator('[data-reference-inspect]').waitFor();
}

async function selectedQuote() {
  return page.evaluate(() => referenceChoice?.quote || null);
}

async function openProject(id) {
  await closeModals();
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${id}"]`).click();
  await page.waitForFunction(project => requestedProject === project && backendOnline && state.project.id === project, id);
  await editor();
}

async function assertNoSelection() {
  assert.equal(await selectedQuote(), null);
  await openCandidate();
  await page.locator('#aiReferenceTools').filter({hasText: '未选择'}).waitFor();
}

function assertSelectionPayload(payload, expectedQuote) {
  assert.equal(payload.reference_selections.length, 1);
  const selection = payload.reference_selections[0];
  assert.deepEqual(Object.keys(selection).sort(), [
    'document_id', 'section_id', 'section_start', 'section_end',
    'source_sha256', 'section_hash', 'source_hash',
  ].sort());
  assert.equal(selection.source_hash, hash(expectedQuote));
  assert.equal(selection.section_end - selection.section_start, Array.from(expectedQuote).length);
  assert.equal(selection.document_id, referenceId);
}

function promptMaterial(payload) {
  const user = payload.messages.find(message => message.role === 'user')?.content;
  const prefix = '<manuscript>\n', suffix = '\n</manuscript>';
  assert.ok(typeof user === 'string' && user.startsWith(prefix));
  const end = user.indexOf(suffix, prefix.length);
  assert.ok(end > prefix.length);
  return JSON.parse(user.slice(prefix.length, end));
}

async function waitCandidate(previousRun) {
  await page.waitForFunction(id => !aiBusy && aiDraft?.ai_run_id && aiDraft.ai_run_id !== id &&
    aiDraft.complete && document.querySelector('#aiAdopt')?.disabled === false, previousRun);
}

async function adoptCandidate() {
  await openCandidate();
  const runId = await page.evaluate(() => aiDraft.ai_run_id);
  await page.locator('#aiAdopt').click();
  await page.locator('[data-apply]').click();
  await page.waitForFunction(id => !adopting && candidateStore.get(id)?.decision === 'accepted', runId);
}

async function screenshot(name) {
  const destination = path.resolve(screenshotDirectory, name);
  if (fs.existsSync(destination)) throw new Error('Screenshot evidence exists; choose a new output directory');
  fs.mkdirSync(path.dirname(destination), {recursive: true});
  await page.screenshot({path: destination, fullPage: false});
  results.screenshots.push({
    file: destination.startsWith(root + path.sep) ? path.relative(root, destination) : path.basename(destination),
    sha256: hash(fs.readFileSync(destination)),
  });
}

async function main() {
  assert.equal(Array.from(boundary).length, 900);
  assert.equal(Array.from(overlong).length, 901);
  const providerPort = await freePort(), appPort = await freePort();
  provider = http.createServer((request, response) => {
    let raw = '';
    request.on('data', value => {raw += value;});
    request.on('end', () => {
      try {
        assert.equal(request.url, '/v1/chat/completions');
        const payload = JSON.parse(raw);
        providerRequests.push(payload);
        response.writeHead(200, {'Content-Type': 'text/event-stream'});
        response.end(`data: ${JSON.stringify({
          model: 'reference-browser-fixture', choices: [{delta: {content: prose}, finish_reason: 'stop'}],
          usage: {prompt_tokens: 30, completion_tokens: 30, total_tokens: 60},
        })}\n\ndata: [DONE]\n\n`);
      } catch (error) {response.writeHead(500);response.end(error.message);}
    });
  });
  await new Promise(resolve => provider.listen(providerPort, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${appPort}`;
  const environment = {...process.env};
  for (const key of Object.keys(environment)) if (key.startsWith('MOYU_')) delete environment[key];
  app = spawn(python, ['server.py'], {
    cwd: root, env: {...environment, MOYU_DB: database, MOYU_HOST: '127.0.0.1', MOYU_PORT: String(appPort),
      MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`, MOYU_AI_MODEL: 'reference-browser-fixture',
      MOYU_AI_API_KEY: '', MOYU_AI_DAILY_QUOTA: '20', MOYU_SECRET_KEY: '',
      MOYU_BACKUP_DIR: path.join(temporary, 'backups'), MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials')},
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  app.stderr.on('data', value => {serverLog += value;});
  for (let attempt = 0; attempt < 200; attempt++) {
    try {if ((await fetch(base + '/api/health')).ok) break;} catch {}
    if (app.exitCode !== null) throw new Error('Isolated server exited');
    if (attempt === 199) throw new Error('Isolated server unavailable');
    await pause(40);
  }
  const state = await api('/api/state');
  firstChapter = state.chapters[0].id;secondChapter = 'reference_second_chapter';
  state.project.title = '参考片段合成甲';
  state.chapters[0].body = '林岚把铜铃留在木桌边。门外脚步声停下来。'.repeat(20);
  state.chapters.push({id: secondChapter, title: '第二章合成稿', body: '第二章保留自己的原稿。', status: '草稿'});
  await api('/api/state', 'PUT', state);
  otherProject = (await api('/api/projects', 'POST', {title: '参考片段合成乙', summary: '', genre: '测试', goal: 50000})).project.id;
  const text = ['# 第一章 合成参考手记', quoteA, quoteB, boundary, overlong].join('\n\n');
  const preview = await api('/api/import/preview', 'POST', {
    filename: 'synthetic-reference.md', data_base64: Buffer.from(text).toString('base64'),
  });
  await api('/api/import/apply', 'POST', {
    preview_id: preview.preview_id, source_sha256: preview.source_sha256,
    title: '合成参考手记', mode: 'reference', chapters: preview.chapters,
  });
  referenceId = (await api('/api/references')).references[0].id;
  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url()), pathname = url.pathname;
    if (request.method() === 'POST' && pathname === '/api/ai/queue') queuePayloads.push(request.postDataJSON());
    if (request.method() === 'POST' && pathname === '/api/ai/prompt-preview') previewPayloads.push(request.postDataJSON());
    if (pathname === '/api/context' && url.searchParams.has('source')) contextRequests.push({
      request_line_bytes: Buffer.byteLength(`GET ${url.pathname}${url.search} HTTP/1.1\r\n`),
      source: JSON.parse(url.searchParams.get('source')),
    });
  });
  await page.route('**/*', route => {
    const hostname = new URL(route.request().url()).hostname;
    return ['127.0.0.1', 'localhost'].includes(hostname) ? route.continue() : route.abort('blockedbyclient');
  });
  await page.goto(base);await page.waitForFunction(() => backendOnline);await editor();
  await page.locator(`[data-chapter="${firstChapter}"]`).click();
  await openCandidate();
  await page.locator('#aiInstruction').fill('生成240至300字，保留当前事件。');

  await test('选择和两种预览只读取参考原句且不写持久存储或数据库', async () => {
    const baseline = await databaseFingerprint(), calls = providerRequests.length;
    await pick('檐雨原句', quoteA);
    assert.equal(await selectedQuote(), quoteA);
    await page.locator('[data-reference-inspect]').click();
    assert.equal(await page.locator('.reference-full-quote').innerText(), quoteA);
    await closeModals();
    await page.getByRole('button', {name: '任务契约', exact: true}).click();
    await page.locator('.ai-contract-preview').waitFor();
    assert.match(await page.locator('.ai-contract-preview').innerText(), /檐雨原句/);
    assert.equal(await page.locator('.reference-preview-section blockquote').innerText(), quoteA);
    assertSelectionPayload(previewPayloads.at(-1), quoteA);
    await closeModals();
    await page.locator('[data-context-preview]').click();
    await page.locator('.context-modal').waitFor();
    assert.equal(await page.locator('.reference-preview-section blockquote').innerText(), quoteA);
    await page.locator('[data-prompt-preview]').click();
    await page.locator('.modal').filter({has: page.getByRole('heading', {name: '完整 Prompt 预览', exact: true})}).waitFor();
    assertSelectionPayload(previewPayloads.at(-1), quoteA);
    await closeModals();
    const persisted = await page.evaluate(() => [...Object.keys(localStorage).map(key => localStorage.getItem(key)),
      ...Object.keys(sessionStorage).map(key => sessionStorage.getItem(key))].join('\n'));
    assert.equal(persisted.includes(quoteA), false);assert.equal(persisted.includes(referenceId), false);
    assert.deepEqual(await databaseFingerprint(), baseline);
    assert.equal(providerRequests.length, calls);
  });
  await test('单段替换撤回保持完整原句900字符表情边界可选且901字符合成响应禁选', async () => {
    await pick('钟楼原句', quoteB);assert.equal(await selectedQuote(), quoteB);
    await page.locator('[data-reference-remove]').click();await assertNoSelection();
    await pick('边界', boundary);assert.equal(Array.from(await selectedQuote()).length, 900);
    await page.locator('[data-reference-remove]').click();
    await page.route('**/api/references/*/search?*', async route => {
      if (new URL(route.request().url()).searchParams.get('q') !== '超限') return route.continue();
      const response = await route.fetch(), data = await response.json();
      const hit = data.results.find(item => item.source_quote.startsWith('超限'));
      assert.ok(hit, 'The synthetic overlong paragraph must exist in the source');
      data.results = [{...hit, body: overlong, source_quote: overlong,
        section_end: hit.section_start + 901, source_end: hit.source_start + 901,
        source_hash: hash(overlong)}];
      return route.fulfill({response, json: data});
    });
    await page.locator('[data-reference-pick]').click();
    await page.waitForFunction(() => !document.querySelector('#referenceDocument').disabled);
    await page.locator('#referenceQuery').fill('超限');await page.locator('[data-reference-search]').click();
    const row = page.locator('.reference-search-result').filter({hasText: '超限'}).first();
    await row.waitFor();assert.equal(await row.locator('[data-reference-use]').isEnabled(), false);
    assert.match(await row.innerText(), /901 字|超过900字/);
    await closeModals();await assertNoSelection();
    await page.unroute('**/api/references/*/search?*');
    results.overlong_ui_response = 'Synthetic complete 901-codepoint response; natural search uses bounded source ranges.';
  });
  await test('刷新与换作品分支章节清空同章重绘保留且没有跨书引用', async () => {
    await pick('檐雨原句', quoteA);await page.evaluate(() => render());assert.equal(await selectedQuote(), quoteA);
    await page.locator(`[data-chapter="${secondChapter}"]`).click();await assertNoSelection();
    await page.locator(`[data-chapter="${firstChapter}"]`).click();await assertNoSelection();
    await pick('檐雨原句', quoteA);
    await page.evaluate(() => {globalThis.__referenceOriginalBranch = state.branch_id;state.branch_id = 'synthetic-reference-branch';render();});
    await assertNoSelection();
    await page.evaluate(() => {state.branch_id = globalThis.__referenceOriginalBranch;render();});
    await pick('檐雨原句', quoteA);
    await openProject(otherProject);await assertNoSelection();
    await page.locator('[data-reference-pick]').click();
    await page.locator('#referenceResults').filter({hasText: '还没有参考来源'}).waitFor();
    assert.equal(await page.locator('[data-reference-use]').count(), 0);
    await openProject('default-project');await pick('檐雨原句', quoteA);
    await page.reload();await page.waitForFunction(() => backendOnline);await editor();await assertNoSelection();
  });
  await test('参考库搜索结果可直接选用并返回原写作章', async () => {
    await page.locator('[data-view="references"]').click();
    await page.locator(`[data-reference-open="${referenceId}"]`).click();
    await page.waitForFunction(() => !document.querySelector('#referenceDocument').disabled);
    await page.locator('#referenceQuery').fill('檐雨原句');await page.locator('[data-reference-search]').click();
    await page.locator('.reference-search-result').filter({hasText: quoteA}).locator('[data-reference-use]').click();
    await page.locator('[data-reference-editor]').click();
    await openCandidate();
    assert.equal(await selectedQuote(), quoteA);
    assert.equal(await page.evaluate(() => activeChapter), firstChapter);
  });
  await test('旧搜索迟到不能覆盖新查询关闭弹窗或切走再返回不能复活', async () => {
    let held;
    await page.route('**/api/references/*/search?*', async route => {
      const query = new URL(route.request().url()).searchParams.get('q');
      if (query === '檐雨原句' && !held) {
        held = {route, response: await route.fetch()};
        return;
      }
      return route.continue();
    });
    await page.locator('[data-reference-pick]').click();
    await page.waitForFunction(() => !document.querySelector('#referenceDocument').disabled);
    await page.locator('#referenceQuery').fill('檐雨原句');await page.locator('[data-reference-search]').click();
    await waitHeld(() => held, 'Delayed first search');
    await page.locator('#referenceQuery').fill('钟楼原句');await page.locator('[data-reference-search]').click();
    await page.locator('.reference-source-quote').filter({hasText: quoteB}).waitFor();
    const currentResults = await page.locator('.reference-source-quote').allTextContents();
    await held.route.fulfill({response: held.response});
    await pause(50);assert.deepEqual(await page.locator('.reference-source-quote').allTextContents(), currentResults);
    await page.unroute('**/api/references/*/search?*');
    await closeModals();
    let secondHeld;
    await page.route('**/api/references/*/search?*', async route => {
      secondHeld = {route, response: await route.fetch()};
    });
    await page.locator('[data-reference-pick]').click();
    await page.waitForFunction(() => !document.querySelector('#referenceDocument').disabled);
    await page.locator('#referenceQuery').fill('檐雨原句');await page.locator('[data-reference-search]').click();
    await waitHeld(() => secondHeld, 'Delayed search after scope change');
    await page.evaluate(id => {activeChapter = id;render();}, secondChapter);
    await page.evaluate(id => {activeChapter = id;render();}, firstChapter);
    await secondHeld.route.fulfill({response: secondHeld.response});
    await pause(50);assert.equal(await page.locator('[data-reference-dialog]').count(), 0);
    await assertNoSelection();await page.unroute('**/api/references/*/search?*');
  });
  await test('参考预览失效不会落到离线材料或发送模型任务并保留选择', async () => {
    await pick('檐雨原句', quoteA);
    const baseline = await databaseFingerprint(), requests = providerRequests.length;
    await page.route('**/api/context?*', route => route.fulfill({
      status: 409, contentType: 'application/json', body: JSON.stringify({error: '参考来源已变化，请重新选择'}),
    }));
    await page.locator('[data-context-preview]').click();
    await page.locator('#toast').filter({hasText: '参考来源已变化'}).waitFor();
    assert.equal(await page.locator('.context-modal').count(), 0);
    assert.equal(await selectedQuote(), quoteA);await page.unroute('**/api/context?*');
    await page.route('**/api/ai/prompt-preview', route => route.fulfill({
      status: 400, contentType: 'application/json', body: JSON.stringify({error: '参考片段超出本次预算'}),
    }));
    await page.getByRole('button', {name: '任务契约', exact: true}).click();
    await page.locator('#toast').filter({hasText: '超出本次预算'}).waitFor();
    assert.equal(await page.locator('.ai-contract-preview').count(), 0);
    await page.unroute('**/api/ai/prompt-preview');
    assert.deepEqual(await databaseFingerprint(), baseline);assert.equal(providerRequests.length, requests);
  });
  await test('旧完整预览迟到不能覆盖后来新选择或重新弹出旧材料', async () => {
    let held;
    await page.route('**/api/ai/prompt-preview', async route => {held = {route, response: await route.fetch()};});
    await page.getByRole('button', {name: '任务契约', exact: true}).click();
    await waitHeld(() => held, 'Delayed prompt preview');
    await pick('钟楼原句', quoteB);
    await held.route.fulfill({response: held.response});
    await pause(50);
    assert.equal(await page.locator('.ai-contract-preview').count(), 0);
    assert.equal(await selectedQuote(), quoteB);
    await page.unroute('**/api/ai/prompt-preview');
    await pick('檐雨原句', quoteA);
  });
  await test('不支持的写作任务阻止引用提交网络失败保留本次引用', async () => {
    await page.locator('#aiTask').selectOption('scene');
    const sends = queuePayloads.length, baseline = await databaseFingerprint();
    await page.locator('#aiGenerate').click();
    await page.locator('#toast').filter({hasText: '当前任务不支持参考片段'}).waitFor();
    assert.equal(queuePayloads.length, sends);assert.equal(await selectedQuote(), quoteA);
    await page.locator('#aiTask').selectOption('continue');
    await page.route('**/api/ai/queue', route => route.fulfill({
      status: 503, contentType: 'application/json', body: JSON.stringify({error: '合成网络失败'}),
    }));
    await page.locator('#aiGenerate').click();
    await page.locator('#toast').filter({hasText: '合成网络失败'}).waitFor();
    assert.equal(await selectedQuote(), quoteA);
    await page.unroute('**/api/ai/queue');assert.deepEqual(await databaseFingerprint(), baseline);
  });
  await test('续写预览与模型完整参考一致202成功清本次引用原稿仅确认后增加', async () => {
    const before = (await api('/api/state')).chapters.find(c => c.id === firstChapter).body;
    const previous = await page.evaluate(() => aiDraft?.ai_run_id || null);
    await page.locator('#aiInstruction').fill('生成240至300字，保留当前事件。');
    await page.getByRole('button', {name: '任务契约', exact: true}).click();
    await page.locator('.ai-contract-preview').waitFor();assertSelectionPayload(previewPayloads.at(-1), quoteA);
    await closeModals();await page.locator('#aiGenerate').click();await waitCandidate(previous);
    await assertNoSelection();assertSelectionPayload(queuePayloads.at(-1), quoteA);
    assert.deepEqual(queuePayloads.at(-1).reference_selections, previewPayloads.at(-1).reference_selections);
    const material = promptMaterial(providerRequests.at(-1));
    assert.equal(material.reference_passages.length, 1);
    assert.equal(material.reference_passages[0].source_quote, quoteA);
    assert.equal((await api('/api/state')).chapters.find(c => c.id === firstChapter).body, before);
    await adoptCandidate();
    assert.equal((await api('/api/state')).chapters.find(c => c.id === firstChapter).body, before + '\n\n' + prose);
  });
  await test('先选正文再选参考返回仍保留润色选区三个入口一致且仅替换选区', async () => {
    await page.locator('#aiTask').selectOption('polish');
    await page.locator('#chapterBody').evaluate(input => {input.focus();input.setSelectionRange(0, 40);captureSelection();});
    await pick('钟楼原句', quoteB);
    assert.deepEqual(await page.locator('#chapterBody').evaluate(input => [input.selectionStart, input.selectionEnd]), [0, 40]);
    assert.equal(await page.locator('#selectionState').innerText(), '已选 40 字');
    const before = (await api('/api/state')).chapters.find(c => c.id === firstChapter).body;
    await page.locator('[data-context-preview]').click();await page.locator('.context-modal').waitFor();
    await page.locator('[data-prompt-preview]').click();
    await page.locator('.modal').filter({has: page.getByRole('heading', {name: '完整 Prompt 预览', exact: true})}).waitFor();
    const fullPreview = previewPayloads.at(-1);assert.equal(fullPreview.source.range.end, 40);
    await closeModals();await page.getByRole('button', {name: '任务契约', exact: true}).click();
    await page.locator('.ai-contract-preview').waitFor();
    assert.deepEqual(previewPayloads.at(-1).source.range, fullPreview.source.range);
    const previous = await page.evaluate(() => aiDraft?.ai_run_id || null);
    await closeModals();await page.locator('#aiGenerate').click();await waitCandidate(previous);
    assert.deepEqual(queuePayloads.at(-1).source.range, fullPreview.source.range);
    assertSelectionPayload(queuePayloads.at(-1), quoteB);await assertNoSelection();
    await adoptCandidate();
    assert.equal((await api('/api/state')).chapters.find(c => c.id === firstChapter).body, prose + before.slice(40));
  });
  await test('长整章润色材料预览避免请求地址超限且完整提示保留全部原稿和参考', async () => {
    const before = await page.locator('#chapterBody').inputValue();
    const longBody = '汉'.repeat(7500);
    const calls = providerRequests.length, sends = queuePayloads.length;
    const originalProfile = await page.evaluate(() => retrievalProfile());
    await page.evaluate(() => saveRetrievalProfile({...retrievalProfile(), max_tokens: 64000}));
    await page.locator('#chapterBody').fill(longBody);
    assert.equal(await page.evaluate(() => syncBackend()), true);
    await page.evaluate(() => {savedSelection = null;});
    await pick('檐雨原句', quoteA);
    await page.locator('#aiTask').selectOption('polish');
    await page.locator('[data-context-preview]').click();
    await page.locator('.context-modal').waitFor();
    assert.equal(await page.locator('.reference-preview-section blockquote').innerText(), quoteA);
    const contextSource = contextRequests.at(-1);
    assert.ok(contextSource.request_line_bytes < 4096, JSON.stringify(contextSource));
    assert.deepEqual(contextSource.source.range, {start: 0, end: 7500});
    assert.equal(contextSource.source.source_body_hash, hash(longBody));
    await page.locator('[data-prompt-preview]').click();
    await page.getByRole('heading', {name: '完整 Prompt 预览', exact: true}).waitFor();
    const fullPreview = previewPayloads.at(-1);
    assert.equal(fullPreview.source.range.quote, longBody);
    assert.equal(fullPreview.source.range.end, 7500);
    const prompt = await page.locator('.candidate-modal .prompt-preview').innerText();
    assert.ok(prompt.includes(longBody));assert.ok(prompt.includes(JSON.stringify(quoteA).slice(1, -1)));
    await closeModals();
    await page.locator('#chapterBody').fill(before);
    assert.equal(await page.evaluate(() => syncBackend()), true);
    await page.locator('[data-reference-remove]').click();
    await page.evaluate(profile => saveRetrievalProfile(profile), originalProfile);
    assert.equal(providerRequests.length, calls);assert.equal(queuePayloads.length, sends);
    results.long_manuscript_preview = {codepoints: 7500,
      get_request_line_bytes: contextSource.request_line_bytes, full_quote_in_post: true, model_requests: 0};
  });
  await test('202后状态读取失败仍清当次参考并恢复已保存候选', async () => {
    await page.locator('#aiTask').selectOption('continue');await pick('檐雨原句', quoteA);
    let committed = false, fail = true;
    await page.route('**/api/**', async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      if (pathname === '/api/ai/queue' && request.method() === 'POST') {
        const response = await route.fetch();committed = true;return route.fulfill({response});
      }
      if (committed && fail && pathname === '/api/state' && request.method() === 'GET') {
        fail = false;return route.fulfill({status: 503, contentType: 'application/json',
          body: JSON.stringify({error: '合成已入队后的状态读取失败'})});
      }
      return route.continue();
    });
    const previous = await page.evaluate(() => aiDraft?.ai_run_id || null);
    await page.locator('#aiGenerate').click();await waitCandidate(previous);
    assert.equal(fail, false);await assertNoSelection();await page.unroute('**/api/**');
  });
  await test('迟到202仅消费发送快照而不清后来新选参考', async () => {
    await pick('檐雨原句', quoteA);let held;
    await page.route('**/api/ai/queue', async route => {held = {route, response: await route.fetch()};});
    const previous = await page.evaluate(() => aiDraft?.ai_run_id || null);
    await page.locator('#aiGenerate').click();await waitHeld(() => held, 'Delayed queue response');
    await pick('钟楼原句', quoteB);assert.equal(await selectedQuote(), quoteB);
    await held.route.fulfill({response: held.response});await waitCandidate(previous);
    assertSelectionPayload(queuePayloads.at(-1), quoteA);assert.equal(await selectedQuote(), quoteB);
    await page.unroute('**/api/ai/queue');
  });
  await test('切章再返回后迟到202不会重开旧候选或消费新参考', async () => {
    await pick('檐雨原句', quoteA);
    let held;
    await page.route('**/api/ai/queue?*', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({jobs: []}),
    }));
    await page.route('**/api/ai/queue', async route => {held = {route, response: await route.fetch()};});
    await page.locator('#aiGenerate').click();
    await waitHeld(() => held, 'Delayed queue response during chapter round trip');
    const queuedResponse = await held.response.json();
    const queuedJob = queuedResponse.job || queuedResponse;
    assert.ok(queuedJob.ai_run_id && queuedJob.id);
    await page.locator(`[data-chapter="${secondChapter}"]`).click();
    await page.locator(`[data-chapter="${firstChapter}"]`).click();
    await pick('钟楼原句', quoteB);
    const beforeRun = await page.evaluate(() => aiDraft?.ai_run_id || null);
    await held.route.fulfill({response: held.response});
    await page.waitForFunction(() => !aiBusy);
    assert.equal(await selectedQuote(), quoteB);
    assert.equal(await page.evaluate(() => aiDraft?.ai_run_id || null), beforeRun);
    assert.equal(await page.evaluate(id => candidateStore.has(id), queuedJob.ai_run_id), false);
    await page.unroute('**/api/ai/queue');
    await page.unroute('**/api/ai/queue?*');
  });
  await test('两种电脑尺寸引用工具和原生成清空控件完整可见互不遮挡', async () => {
    results.desktop_viewports = [];
    await openCandidate();
    for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 900}]) {
      await page.setViewportSize(viewport);
      for (const selector of ['.ai-controls', '#aiReferenceTools']) {
        await page.locator(selector).scrollIntoViewIfNeeded();
        const controls = await page.locator(selector).evaluate(host =>
          [...host.querySelectorAll('button,input,select')].map(element => {
            const rect = element.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
            return {label: element.id || element.textContent.trim(), x: rect.x, y: rect.y,
              width: rect.width, height: rect.height,
              within: rect.x >= 0 && rect.right <= innerWidth && rect.y >= 0 && rect.bottom <= innerHeight,
              unobstructed: hit === element || element.contains(hit)};
          }));
        assert.ok(controls.every(item => item.width > 0 && item.height > 0 && item.within && item.unobstructed), JSON.stringify(controls));
        results.desktop_viewports.push({viewport, selector, controls});
      }
      await screenshot(`reference-passages-${viewport.width}x${viewport.height}.png`);
      await page.locator('[data-reference-inspect]').click();
      assert.equal(await page.locator('.reference-full-quote').innerText(), quoteB);
      await screenshot(`reference-passages-original-${viewport.width}x${viewport.height}.png`);
      await closeModals();
    }
  });
  assert.deepEqual(pageErrors, []);
  results.local_mock_model_requests = providerRequests.length;
  results.queue_request_count = queuePayloads.length;results.preview_request_count = previewPayloads.length;
  results.source_files_sha256_after = fingerprints();
  results.source_unchanged = JSON.stringify(results.source_files_sha256_before) === JSON.stringify(results.source_files_sha256_after);
  assert.equal(results.source_unchanged, true);
  results.passed = true;await context.close();
}

main().catch(async error => {
  results.failure = {message: error.message, server_log: serverLog.slice(-6000).split(root).join('<workspace>').split(temporary).join('<isolated>')};
  if (page && !page.isClosed()) {
    results.failure.frontend = await page.evaluate(() => ({
      activeChapter, backendOnline, aiBusy, adopting,
      draft: aiDraft && {ai_run_id: aiDraft.ai_run_id, decision: aiDraft.decision,
        complete: aiDraft.complete, materials_read_only: aiDraft.materials_read_only},
      queue: queueJobs.map(job => ({id: job.id, status: job.status})),
      toast: document.querySelector('#toast')?.textContent,
    })).catch(() => null);
    await screenshot('reference-passages-failure.png').catch(() => {});
  }
  console.error(error.stack);
}).finally(async () => {
  if (browser) await browser.close().catch(() => {});
  if (app && app.exitCode === null) {
    app.kill('SIGTERM');
    await Promise.race([new Promise(resolve => app.once('exit', resolve)), pause(3000)]);
    if (app.exitCode === null) app.kill('SIGKILL');
  }
  if (provider) await new Promise(resolve => provider.close(resolve));
  results.finished_at = new Date().toISOString();
  results.source_files_sha256_after ||= fingerprints();
  results.source_unchanged = JSON.stringify(results.source_files_sha256_before) === JSON.stringify(results.source_files_sha256_after);
  results.local_mock_model_requests = providerRequests.length;
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});
  const publicReport = process.env.MOYU_REFERENCE_BROWSER_REPORT ? {
    ...results, artifacts: 'Private isolated synthetic artifacts retained locally.',
  } : results;
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  fs.writeFileSync(reportPath, JSON.stringify(publicReport, null, 2) + '\n');
  console.log(JSON.stringify(publicReport));
  process.exitCode = results.passed ? 0 : 1;
});
