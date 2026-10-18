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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-material-browser-'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const canary = 'BROWSER_MATERIAL_DEPENDENCY_CANARY';
const prose = '雨声忽然停了。她松开铜扣，沿窗框摸到一道细小的划痕，随后把手收回袖中。';
const reportPath = process.env.MOYU_MATERIAL_BROWSER_REPORT || path.join(temporary, 'results.json');
if (fs.existsSync(reportPath)) throw new Error('Evidence exists; use a new report path');

const results = {
  passed: false,
  scope: '真实电脑浏览器、临时作品、隔离SQLite与免费本机提供方；过期资料候选回看和章后确认边界',
  claim_limit: '产品操作与材料依赖保护；不证明文学质量或第一梯队竞争力',
  cases: [],
  artifacts: temporary,
  paid_provider_calls: 0,
  source_files: [
    'frontend/app.js', 'frontend/author-workspace.js', 'backend/server.py', 'backend/moyu_analysis.py', 'backend/moyu_context.py',
    'backend/moyu_workspace.py', 'backend/moyu_materials.py', 'backend/moyu_records.py', 'frontend/styles.css',
    'tests/material_dependency_browser_regressions.cjs',
  ].map(file => ({path: file, sha256: hash(fs.readFileSync(path.join(root, file)))})),
};

let app;
let provider;
let browser;
let base;
let serverLog = '';
let providerMode = 'prose';
const providerRequests = [];
let chapterId;
let card;
let body;
let staleRun;
let staleJob;

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function api(route, method = 'GET', payload) {
  const response = await fetch(base + route, {
    method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': 'default-project'},
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${route} ${response.status}: ${JSON.stringify(data)}`);
  return data;
}

async function requestStatus(route, method, payload) {
  const response = await fetch(base + route, {
    method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': 'default-project'},
    body: JSON.stringify(payload),
  });
  return {status: response.status, data: await response.json()};
}

async function test(name, run) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await run();
  results.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, state: 'passed'}));
}

async function waitSaved(page) {
  await page.waitForFunction(
    () => backendOnline && confirmedState && !isDirty() && !syncInFlight &&
      !independentWrite && !adopting && !saveError && !mergePending,
    null, {timeout: 15000},
  );
}

async function openCandidate(page) {
  await page.locator('[data-chat-result="workbenchCandidateSection"]').click();
  await page.locator('#aiInstruction').waitFor();
}

async function open(viewport) {
  const context = await browser.newContext({viewport});
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route =>
    route.request().url().startsWith(base) ? route.continue() : route.abort());
  await page.goto(base);
  await page.waitForFunction(() => backendOnline, null, {timeout: 15000});
  await page.locator('[data-view="editor"]').click();
  await page.locator(`[data-chapter="${chapterId}"]`).click();
  await waitSaved(page);
  await openCandidate(page);
  return {context, page, errors};
}

async function currentBody() {
  return (await api('/api/state')).chapters.find(chapter => chapter.id === chapterId).body;
}

async function openSavedCandidate(page, runId) {
  await page.locator('[data-view="tasks"]').click();
  const button = page.locator(`[data-task-open="${runId}"]`);
  await button.waitFor({timeout: 15000});
  await button.click();
  await page.waitForFunction(id => aiDraft?.ai_run_id === id && aiDraft?.complete, runId);
  await openCandidate(page);
}

async function assertReadableStaleCandidate(page) {
  await page.waitForFunction(id =>
    aiDraft?.ai_run_id === id && aiDraft?.materials_read_only === true, staleRun,
  {timeout: 15000});
  assert.equal(await page.locator('#aiOutput').inputValue(), prose);
  assert.equal(await page.locator('#aiOutput').evaluate(element => element.readOnly), true);
  assert.equal(await page.locator('#aiAdopt').isEnabled(), false);
  assert.equal(await page.locator('#aiRevise').isEnabled(), false);
  assert.equal(await page.locator('#aiCopy').isEnabled(), true);
  assert.match(await page.locator('#aiStatus').innerText(), /资料已变化|资料.*变化/);
  assert.match(await page.locator('#aiRevise').getAttribute('title'), /资料/);
  await page.locator('#aiCopy').click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), prose);
}

async function screenshot(page, filename) {
  await page.locator('.ai-copilot').scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(temporary, filename), fullPage: false});
  const width = await page.evaluate(() => ({
    viewport: innerWidth, document: document.documentElement.scrollWidth,
  }));
  assert.ok(width.document <= width.viewport + 2, `horizontal overflow: ${JSON.stringify(width)}`);
  results.screenshots = [...(results.screenshots || []), filename];
}

async function startServices() {
  const providerPort = await freePort();
  provider = http.createServer((request, response) => {
    let raw = '';
    request.on('data', chunk => {raw += chunk;});
    request.on('end', () => {
      try {
        const payload = JSON.parse(raw);
        providerRequests.push(payload);
        assert.equal(request.url, '/v1/chat/completions');
        const user = payload.messages?.find(message => message.role === 'user')?.content || '';
        const context = JSON.parse(user.split('<manuscript>\n')[1]?.split('\n</manuscript>')[0] || '{}');
        const current = context.current_chapter || {};
        const quote = String(current.body || '');
        const evidence = [{
          source_chapter_id: current.id, source_start: 0,
          source_end: Array.from(quote).length, source_quote: quote,
        }];
        const text = providerMode === 'analysis' ? JSON.stringify({
          summary: {text: '合成章后回顾：她守在北窗听雨。', evidence},
          handoff: {text: '合成下一章任务：沿石阶继续寻找来客。', evidence},
          candidates: [],
        }) : prose;
        response.writeHead(200, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({
          model: 'material-browser-fixture',
          choices: [{message: {content: text}, finish_reason: 'stop'}],
          usage: {prompt_tokens: 80, completion_tokens: 40, total_tokens: 120},
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
  const environment = {...process.env};
  for (const key of Object.keys(environment)) if (key.startsWith('MOYU_')) delete environment[key];
  app = spawn(process.env.MOYU_TEST_PYTHON || 'python3', ['server.py'], {
    cwd: root,
    env: {
      ...environment,
      MOYU_DB: path.join(temporary, 'synthetic.sqlite3'),
      MOYU_HOST: '127.0.0.1', MOYU_PORT: String(appPort),
      MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`,
      MOYU_AI_MODEL: 'material-browser-fixture', MOYU_AI_API_KEY: '',
      MOYU_AI_DAILY_QUOTA: '1000', MOYU_SECRET_KEY: '',
      MOYU_BACKUP_DIR: path.join(temporary, 'backups'),
      MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials'),
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  app.stderr.on('data', chunk => {serverLog += chunk.toString();});
  for (let attempt = 0; attempt < 250; attempt += 1) {
    try {
      if ((await fetch(base + '/api/health')).ok) return;
    } catch {}
    if (app.exitCode !== null) throw new Error('isolated app exited');
    await pause(40);
  }
  throw new Error('isolated app unavailable');
}

async function main() {
  await startServices();
  const state = await api('/api/state');
  state.project.title = '资料回看保护合成作品';
  state.project.summary = '电脑浏览器临时验收作品，不属于真实原稿。';
  state.chapters = state.chapters.slice(0, 1);
  chapterId = state.chapters[0].id;
  body = '合成作者把铜扣抵在窗框，听见雨落在石阶。她看清了来客留下的划痕。'.repeat(5);
  Object.assign(state.chapters[0], {
    title: '第一章北窗', body, status: '草稿', chapter_goal: '在北窗等待来客',
    scene_plan: '看清铜扣上的雨纹。', pov: '', recap: '', next_brief: '',
  });
  state.records = [];
  state.memory = [];
  state.plot = {setup: [], develop: [], payoff: []};
  await api('/api/state', 'PUT', state);
  card = await api('/api/records', 'POST', {
    type: '设定', name: '北窗的材料', description: canary,
  });
  browser = await chromium.launch({
    headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined,
  });
  const desktop = await open({width: 1440, height: 1000});
  const page = desktop.page;

  await test('1440电脑端生成完整候选，尚未采纳时原稿保持不变', async () => {
    await page.locator('#aiTask').selectOption('continue');
    await page.locator('#aiInstruction').fill('沿用北窗材料，续写当前场景。');
    await page.locator('#aiGenerate').click();
    await page.waitForFunction(value => aiDraft?.complete && aiDraft?.text === value, prose,
      {timeout: 15000});
    ({run: staleRun, job: staleJob} = await page.evaluate(() => ({
      run: aiDraft.ai_run_id, job: aiDraft.job_id,
    })));
    assert.equal(await page.locator('#aiAdopt').isEnabled(), true);
    await page.waitForFunction(() => !document.querySelector('#aiRevise')?.disabled);
    assert.equal(await currentBody(), body);
    assert.equal(providerRequests.length, 1);
    assert.ok(JSON.stringify(providerRequests[0]).includes(canary));
  });

  await test('资料变化后真实任务回读只读原因，禁止采纳整理但可复制并刷新保留', async () => {
    await api('/api/records/' + card.id, 'PATCH', {description: canary + '_UPDATED'});
    const job = await api('/api/ai/jobs/' + staleJob);
    assert.equal(job.status, 'success');
    assert.equal(job.materials_read_only, true);
    assert.match(job.material_issue, /资料/);
    const calls = providerRequests.length;
    await page.reload();
    await page.waitForFunction(() => backendOnline);
    await openSavedCandidate(page, staleRun);
    await assertReadableStaleCandidate(page);
    await screenshot(page, 'material-stale-candidate-desktop.png');
    await page.reload();
    await page.waitForFunction(() => backendOnline);
    await openSavedCandidate(page, staleRun);
    await assertReadableStaleCandidate(page);
    assert.equal(providerRequests.length, calls);
    assert.equal(await currentBody(), body);
  });

  await test('1280乘900电脑窗口回看相同旧候选，禁用动作和可复制原文均保留', async () => {
    const compact = await open({width: 1280, height: 900});
    await openSavedCandidate(compact.page, staleRun);
    await assertReadableStaleCandidate(compact.page);
    await screenshot(compact.page, 'material-stale-candidate-1280x900.png');
    assert.equal(await currentBody(), body);
    assert.equal(providerRequests.length, 1);
    assert.deepEqual(compact.errors, []);
    await compact.context.close();
  });

  await test('旧章后分析仍可读报告，资料变化后确认禁用且直接确认接口拒绝写入', async () => {
    providerMode = 'analysis';
    await page.locator('[data-action="chapterAnalysis"]').click();
    await page.locator('[data-extract]').click();
    await page.waitForFunction(() =>
      queueJobs.some(job => job.task === 'chapter_analysis' && job.status === 'success'),
    null, {timeout: 15000});
    const analyses = await api('/api/chapter-analysis?chapter_id=' + chapterId);
    const analysis = analyses.analyses[0];
    assert.ok(analysis);
    assert.equal(analysis.status, 'pending');
    assert.equal(providerRequests.length, 2);
    await api('/api/records/' + card.id, 'PATCH', {description: canary + '_AFTER_ANALYSIS'});
    const before = await api('/api/state');
    await page.reload();
    await page.waitForFunction(() => backendOnline);
    await page.locator('[data-view="editor"]').click();
    await page.locator('[data-action="chapterAnalysis"]').click();
    await page.locator('.analysis-modal').waitFor();
    assert.match(await page.locator('.analysis-modal').innerText(), /重新提取|资料.*变化/);
    assert.equal(await page.locator('[data-confirm-analysis]').isEnabled(), false);
    assert.equal(await page.locator('#analysisSummary').inputValue(), analysis.summary.text);
    assert.equal(await page.locator('#analysisHandoff').inputValue(), analysis.handoff.text);
    const rejected = await requestStatus('/api/chapter-analysis/' + analysis.id + '/confirm', 'POST', {
      source_revision_id: analysis.source_revision_id, chapter_hash: analysis.chapter_hash,
      decisions: [], confirm_summary: true, confirm_handoff: true,
    });
    assert.equal(rejected.status, 409);
    assert.deepEqual(await api('/api/state'), before);
    await page.screenshot({path: path.join(temporary, 'material-stale-analysis-desktop.png'), fullPage: false});
    results.screenshots.push('material-stale-analysis-desktop.png');
    await page.locator('.analysis-modal [data-close]').click();
    assert.equal(providerRequests.length, 2);
  });

  await test('真实材料窗口排除设定后该设定变化不阻断新候选，仍可比较并单次采纳', async () => {
    providerMode = 'prose';
    await openCandidate(page);
    await page.locator('[data-context-preview]').click();
    await page.locator(`[data-material-omit][data-kind="records"][data-id="${card.id}"]`).check();
    await page.locator('[data-save-material]').click();
    await page.locator('#aiTask').selectOption('continue');
    await page.locator('#aiInstruction').fill('只续写当前场景。');
    await page.locator('#aiGenerate').click();
    await page.waitForFunction(old => aiDraft?.complete && aiDraft?.task === 'continue' &&
      aiDraft?.ai_run_id !== old, staleRun, {timeout: 15000});
    const run = await page.evaluate(() => aiDraft.ai_run_id);
    assert.equal(providerRequests.length, 3);
    assert.ok(!JSON.stringify(providerRequests[2]).includes(canary));
    await api('/api/records/' + card.id, 'PATCH', {description: canary + '_OMITTED_CHANGE'});
    await page.reload();
    await page.waitForFunction(() => backendOnline);
    await openSavedCandidate(page, run);
    assert.equal(await page.evaluate(() => aiDraft.materials_read_only), false);
    assert.equal(await page.locator('#aiAdopt').isEnabled(), true);
    await page.waitForFunction(() => !document.querySelector('#aiRevise')?.disabled);
    await page.locator('#aiAdopt').click();
    await page.locator('[data-apply]').click();
    await page.waitForFunction(() => !document.querySelector('[data-apply]'));
    await waitSaved(page);
    assert.equal(await currentBody(), body + '\n\n' + prose);
    assert.equal((await currentBody()).split(prose).length - 1, 1);
    assert.equal(providerRequests.length, 3);
    results.omitted_control = {provider_excludes_card: true, applied_once: true};
  });

  assert.deepEqual(desktop.errors, []);
  results.provider_calls = providerRequests.length;
  results.free_provider_calls = providerRequests.length;
  results.source_unchanged = results.source_files.every(file =>
    file.sha256 === hash(fs.readFileSync(path.join(root, file.path))));
  assert.equal(results.source_unchanged, true, 'Source must stay unchanged during the browser run');
  results.passed = true;
  await desktop.context.close();
}

main().catch(error => {
  results.failure = {
    message: String(error.stack || error.message).split(root).join('<workspace>')
      .split(temporary).join('<isolated>'),
    server_log: serverLog.slice(-5000).split(root).join('<workspace>')
      .split(temporary).join('<isolated>'),
  };
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close().catch(() => {});
  if (app && app.exitCode === null) {
    const stopped = new Promise(resolve => app.once('exit', resolve));
    app.kill('SIGTERM');
    await Promise.race([stopped, pause(2000)]);
    if (app.exitCode === null) app.kill('SIGKILL');
    await stopped;
  }
  if (provider) await new Promise(resolve => provider.close(resolve));
  results.free_provider_calls = providerRequests.length;
  results.provider_calls = providerRequests.length;
  const saved = process.env.MOYU_MATERIAL_BROWSER_REPORT ? {
    ...results, artifacts: 'Private isolated artifacts retained locally; screenshots listed in this report.',
  } : results;
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});
  fs.writeFileSync(reportPath, JSON.stringify(saved, null, 2) + '\n');
  console.log(JSON.stringify(results, null, 2));
});
