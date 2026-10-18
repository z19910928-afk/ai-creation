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
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-analysis-guidance-browser-'));
const database = path.join(temporary, 'synthetic.sqlite3');
const python = process.env.MOYU_TEST_PYTHON || 'python3';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const reportPath = process.env.MOYU_ANALYSIS_GUIDANCE_BROWSER_REPORT || path.join(temporary, 'results.json');
if (fs.existsSync(reportPath)) throw new Error('Evidence exists; use a new report path');
const sourceFiles = [
  'frontend/app.js', 'frontend/author-workspace.js', 'backend/server.py', 'backend/moyu_analysis.py', 'backend/moyu_context.py',
  'backend/moyu_workspace.py', 'backend/moyu_materials.py', 'backend/moyu_records.py', 'frontend/styles.css',
  'tests/analysis_guidance_browser_regressions.cjs',
];
const fingerprints = () => Object.fromEntries(sourceFiles.map(file =>
  [file, hash(fs.readFileSync(path.join(root, file)))]));
const results = {
  passed: false,
  started_at: new Date().toISOString(),
  scope: '真实电脑浏览器、临时合成作品、隔离SQLite与免费本机提供方；章后分析创作设定绑定',
  claim_limit: '验证设定变化后的只读保护和当前分析分步确认，不证明文学质量或第一梯队地位',
  synthetic_only: true,
  cases: [],
  observations: [],
  screenshots: [],
  artifacts: temporary,
  paid_provider_calls: 0,
  source_files_sha256_before: fingerprints(),
};
results.source_files = Object.entries(results.source_files_sha256_before)
  .map(([file, sha256]) => ({path: file, sha256}));

let app;
let provider;
let browser;
let base;
let serverLog = '';
let includeCandidate = false;
const providerRequests = [];
const httpExchanges = [];

function freezeSources(phase, expected) {
  for (const file of sourceFiles) {
    const bytes = fs.readFileSync(path.join(root, file));
    assert.equal(hash(bytes), expected[file], 'Source changed before capture: ' + file);
    const target = path.join(temporary, 'source-' + phase, file);
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, bytes);
  }
}

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function request(project, route, method = 'GET', payload) {
  const response = await fetch(base + route, {
    method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project},
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  httpExchanges.push({project, route, method, payload, status: response.status, data});
  return {status: response.status, data};
}

async function api(project, route, method = 'GET', payload) {
  const response = await request(project, route, method, payload);
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`${route} ${response.status}: ${JSON.stringify(response.data)}`);
  }
  return response.data;
}

async function databaseHash() {
  const program = [
    'import hashlib, sqlite3, sys',
    'conn = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)',
    'print(hashlib.sha256("\\n".join(conn.iterdump()).encode("utf-8")).hexdigest())',
    'conn.close()',
  ].join('\n');
  return new Promise((resolve, reject) => {
    let text = '';
    let errorText = '';
    const child = spawn(python, ['-c', program, database], {
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.on('data', chunk => {text += chunk.toString();});
    child.stderr.on('data', chunk => {errorText += chunk.toString();});
    child.once('error', reject);
    child.once('exit', code => {
      if (code !== 0) reject(new Error('Synthetic database hash failed: ' + errorText));
      else resolve(text.trim());
    });
  });
}

async function test(name, run) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  try {
    await run();
    results.cases.push({name, passed: true});
    console.log(JSON.stringify({test: name, state: 'passed'}));
  } catch (error) {
    results.cases.push({
      name, passed: false,
      error: String(error.stack || error.message).split(root).join('<workspace>')
        .split(temporary).join('<isolated>'),
    });
    console.log(JSON.stringify({test: name, state: 'failed', error: error.message}));
  }
}

async function waitSaved(page) {
  await page.waitForFunction(
    () => backendOnline && confirmedState && !isDirty() && !syncInFlight &&
      !independentWrite && !adopting && !saveError && !mergePending,
    null, {timeout: 15000},
  );
}

async function createProject(title) {
  const created = await api('default-project', '/api/projects', 'POST', {
    title, summary: '电脑页面隔离合成验收作品，不是真实原稿。', genre: '悬疑', goal: 50000,
  });
  const project = created.project || created.state?.project;
  assert.ok(project?.id);
  const state = await api(project.id, '/api/state');
  state.chapters = state.chapters.slice(0, 1);
  Object.assign(state.chapters[0], {
    title: '第一章北窗', status: '草稿',
    body: '林岚把铜扣放在窗台，合成来客伸手取走铜扣。\n雨水沿窗框落下，石阶上只剩一道细小的划痕。'.repeat(4),
    chapter_goal: '看清北窗的铜扣去向', scene_plan: '查看窗边雨纹', pov: '',
    recap: '', next_brief: '',
  });
  state.records = [];
  state.memory = [];
  state.plot = {setup: [], develop: [], payoff: []};
  await api(project.id, '/api/state', 'PUT', state);
  await api(project.id, '/api/records', 'POST', {
    type: '人物', name: '林岚', description: '检查北窗的人。',
  });
  const blueprint = await api(project.id, '/api/blueprint');
  await api(project.id, '/api/blueprint', 'PUT', {
    expected_version: blueprint.version,
    blueprint: {...blueprint.blueprint, premise: '雨夜北窗的等候', contract: '遵守现有叙事视角。'},
  });
  return {...project, chapterId: state.chapters[0].id};
}

async function completeAnalysis(project, candidates = false) {
  includeCandidate = candidates;
  const job = await api(project.id, '/api/ai/queue', 'POST', {
    chapter_id: project.chapterId, task: 'chapter_analysis', response_format: 'json', defer: true,
  });
  const completed = await api(project.id, '/api/ai/jobs/' + job.id + '/process', 'POST', {});
  assert.equal(completed.status, 'success', JSON.stringify(completed));
  const analysis = completed.result?.analysis;
  assert.ok(analysis?.id && analysis.run_id === job.ai_run_id);
  return {job, analysis};
}

async function openProject(project, viewport) {
  const context = await browser.newContext({viewport});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route =>
    route.request().url().startsWith(base) ? route.continue() : route.abort());
  await page.goto(base);
  await page.waitForFunction(() => backendOnline, null, {timeout: 15000});
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${project.id}"]`).click();
  await page.waitForFunction(id => requestedProject === id && backendOnline, project.id);
  await page.locator('[data-view="editor"]').click();
  await page.locator(`[data-chapter="${project.chapterId}"]`).click();
  await waitSaved(page);
  return {context, page, errors};
}

async function openAnalysis(page) {
  await page.locator('[data-action="chapterAnalysis"]').click();
  await page.locator('.analysis-modal').waitFor();
}

async function staleScenario(field, viewport, filename) {
  const project = await createProject(`创作设定只读-${field}-${viewport.width}`);
  const {job, analysis} = await completeAnalysis(project);
  const chapterBefore = (await api(project.id, '/api/state')).chapters;
  const current = await api(project.id, '/api/blueprint');
  const blueprint = {...current.blueprint, [field]: field === 'premise' ?
    '合成新前提：等待改为明确拒绝来客。' : '合成新约定：禁止提前揭示来客的去向。'};
  const saved = await api(project.id, '/api/blueprint', 'PUT', {
    expected_version: current.version, blueprint,
  });
  assert.equal(saved.version, current.version + 1);
  assert.deepEqual((await api(project.id, '/api/state')).chapters, chapterBefore);
  const workspace = await openProject(project, viewport);
  try {
    const {page} = workspace;
    await openAnalysis(page);
    await page.screenshot({path: path.join(temporary, filename), fullPage: false});
    results.screenshots.push(filename);
    const history = await api(project.id, '/api/ai/jobs/' + job.id);
    const listed = (await api(project.id, '/api/chapter-analysis?chapter_id=' + project.chapterId)).analyses
      .find(item => item.id === analysis.id);
    const controls = {
      save_disabled: await page.locator('[data-confirm-analysis]').isDisabled(),
      reextract_enabled: await page.locator('[data-extract]').isEnabled(),
      readonly_message: /仅供阅读/.test(await page.locator('.analysis-modal').innerText()),
      summary_preserved: await page.locator('#analysisSummary').inputValue() === analysis.summary.text,
      handoff_preserved: await page.locator('#analysisHandoff').inputValue() === analysis.handoff.text,
    };
    const widths = await page.evaluate(() => ({
      viewport: innerWidth, document: document.documentElement.scrollWidth,
      modal: document.querySelector('.analysis-modal').getBoundingClientRect().width,
    }));
    const state = await api(project.id, '/api/state');
    const beforeHash = await databaseHash();
    const calls = providerRequests.length;
    const forced = await request(project.id, '/api/chapter-analysis/' + analysis.id + '/confirm', 'POST', {
      source_revision_id: analysis.source_revision_id, chapter_hash: analysis.chapter_hash,
      decisions: [], confirm_summary: true, confirm_handoff: true,
    });
    const stateUnchanged = JSON.stringify(await api(project.id, '/api/state')) === JSON.stringify(state);
    const databaseUnchanged = await databaseHash() === beforeHash;
    results.observations.push({
      scenario: 'blueprint_only_stale_analysis', field, viewport,
      actual_saved_job_and_run: true, extra_materialize_calls: 0,
      chapter_body_revision_and_plan_unchanged: true,
      history_materials_read_only: history.materials_read_only,
      analysis_status: listed.status, analysis_stale: !!listed.stale,
      controls, widths, forced_confirm_http_status: forced.status,
      state_unchanged: stateUnchanged, database_unchanged: databaseUnchanged,
      extra_provider_calls: providerRequests.length - calls,
    });
    assert.equal(listed.status, 'stale');
    assert.equal(listed.stale, true);
    assert.equal(history.materials_read_only, true);
    assert.deepEqual(controls, {
      save_disabled: true, reextract_enabled: true, readonly_message: true,
      summary_preserved: true, handoff_preserved: true,
    });
    assert.equal(forced.status, 409, JSON.stringify(forced.data));
    assert.equal(stateUnchanged, true);
    assert.equal(databaseUnchanged, true);
    assert.equal(providerRequests.length, calls);
    assert.ok(widths.document <= widths.viewport + 2, JSON.stringify(widths));
    assert.ok(widths.modal < widths.viewport, JSON.stringify(widths));
    assert.deepEqual(workspace.errors, []);
  } finally {
    await workspace.context.close();
  }
}

async function currentScenario() {
  const project = await createProject('当前章后分析分步确认');
  const {analysis} = await completeAnalysis(project, true);
  const original = (await api(project.id, '/api/state')).chapters[0];
  const calls = providerRequests.length;
  const workspace = await openProject(project, {width: 1440, height: 1000});
  try {
    const {page} = workspace;
    const outcomes = [];
    for (const step of ['summary', 'handoff', 'candidate']) {
      await openAnalysis(page);
      assert.equal(await page.locator('[data-confirm-analysis]').isEnabled(), true);
      assert.equal(await page.locator('[data-extract]').isEnabled(), true);
      if (step === 'summary') {
        await page.locator('#analysisSummaryDecision').selectOption('accepted');
        assert.equal(await page.locator('#analysisHandoffDecision').inputValue(), '');
      } else if (step === 'handoff') {
        assert.equal(await page.locator('#analysisSummaryDecision').isDisabled(), true);
        await page.locator('#analysisHandoffDecision').selectOption('accepted');
      } else {
        assert.equal(await page.locator('#analysisSummaryDecision').isDisabled(), true);
        assert.equal(await page.locator('#analysisHandoffDecision').isDisabled(), true);
        await page.locator('[data-analysis-decision]').selectOption('accepted');
      }
      const confirmation = page.waitForResponse(response =>
        response.url().endsWith('/api/chapter-analysis/' + analysis.id + '/confirm') &&
        response.request().method() === 'POST');
      await page.locator('[data-confirm-analysis]').click();
      const response = await confirmation;
      assert.equal(response.status(), 200, await response.text());
      await page.waitForFunction(() => !document.querySelector('.analysis-modal'), null, {timeout: 15000});
      await waitSaved(page);
      const listed = (await api(project.id, '/api/chapter-analysis?chapter_id=' + project.chapterId)).analyses
        .find(item => item.id === analysis.id);
      outcomes.push({
        step, confirm_http_status: response.status(), analysis_status: listed.status,
        stale: !!listed.stale, summary_status: listed.summary.status, handoff_status: listed.handoff.status,
      });
      assert.notEqual(listed.status, 'stale');
      assert.equal(listed.stale, undefined);
    }
    const state = await api(project.id, '/api/state');
    const chapter = state.chapters.find(item => item.id === project.chapterId);
    const listed = (await api(project.id, '/api/chapter-analysis?chapter_id=' + project.chapterId)).analyses
      .find(item => item.id === analysis.id);
    results.observations.push({
      scenario: 'current_analysis_sequential_page_confirmations', viewport: {width: 1440, height: 1000},
      outcomes, final_status: listed.status,
      summary_saved: chapter.recap === analysis.summary.text,
      handoff_saved: chapter.next_brief === analysis.handoff.text,
      new_confirmed_memories: state.memory.filter(item => item.status === '已确认').length,
      original_body_unchanged: chapter.body === original.body,
      original_revision_unchanged: chapter.revision_id === original.revision_id,
      extra_provider_calls: providerRequests.length - calls,
    });
    assert.equal(listed.status, 'reviewed');
    assert.equal(chapter.recap, analysis.summary.text);
    assert.equal(chapter.next_brief, analysis.handoff.text);
    assert.equal(state.memory.filter(item => item.status === '已确认').length, 1);
    assert.equal(chapter.body, original.body);
    assert.equal(chapter.revision_id, original.revision_id);
    assert.equal(providerRequests.length, calls);
    assert.deepEqual(workspace.errors, []);
  } finally {
    await workspace.context.close();
  }
}

async function startServices() {
  const providerPort = await freePort();
  provider = http.createServer((request, response) => {
    let raw = '';
    request.on('data', chunk => {raw += chunk;});
    request.on('end', () => {
      try {
        assert.equal(request.url, '/v1/chat/completions');
        const payload = JSON.parse(raw);
        providerRequests.push(payload);
        const user = payload.messages?.find(message => message.role === 'user')?.content || '';
        const context = JSON.parse(user.split('<manuscript>\n')[1]?.split('\n</manuscript>')[0] || '{}');
        const current = context.current_chapter || {};
        const quote = String(current.body || '').split('\n')[0];
        assert.ok(current.id && quote);
        const evidence = {
          source_chapter_id: current.id, source_start: 0,
          source_end: Array.from(quote).length, source_quote: quote,
        };
        const content = JSON.stringify({
          summary: {text: '合成回顾：林岚查看北窗，来客取走铜扣。', evidence: [evidence]},
          handoff: {text: '合成下一章任务：沿石阶核对来客留下的划痕。', evidence: [evidence]},
          candidates: includeCandidate ? [{
            kind: 'event', name: '取走铜扣', note: '合成来客从北窗取走铜扣。',
            subject_name: '合成来客', epistemic_mode: 'world_truth', known_by: [], ...evidence,
          }] : [],
        });
        response.writeHead(200, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({
          model: 'analysis-guidance-browser-fixture',
          choices: [{message: {content}, finish_reason: 'stop'}],
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
  app = spawn(python, ['server.py'], {
    cwd: root,
    env: {
      ...environment, PYTHONDONTWRITEBYTECODE: '1',
      MOYU_DB: database, MOYU_HOST: '127.0.0.1', MOYU_PORT: String(appPort),
      MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`,
      MOYU_AI_MODEL: 'analysis-guidance-browser-fixture', MOYU_AI_API_KEY: '',
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
    if (app.exitCode !== null) throw new Error('isolated app exited: ' + serverLog.slice(-3000));
    await pause(40);
  }
  throw new Error('isolated app unavailable');
}

async function main() {
  freezeSources('before', results.source_files_sha256_before);
  await startServices();
  await api('default-project', '/api/state');
  browser = await chromium.launch({
    headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined,
  });
  await test('1440电脑窗口只改蓝图前提，旧章后分析只读且强制确认409无写入', () =>
    staleScenario('premise', {width: 1440, height: 1000}, 'analysis-guidance-stale-desktop.png'));
  await test('1280乘900电脑窗口只改创作约定，旧分析禁用保存并允许重新提取', () =>
    staleScenario('contract', {width: 1280, height: 900}, 'analysis-guidance-stale-1280x900.png'));
  await test('当前分析通过实际页面依次确认回顾交接和记忆，自身保存不使报告过期', currentScenario);
}

main().catch(error => {
  results.failure = String(error.stack || error.message).split(root).join('<workspace>')
    .split(temporary).join('<isolated>');
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
  results.source_files_sha256_after = fingerprints();
  results.source_unchanged = JSON.stringify(results.source_files_sha256_before) ===
    JSON.stringify(results.source_files_sha256_after);
  freezeSources('after', results.source_files_sha256_after);
  results.free_provider_calls = providerRequests.length;
  results.provider_calls = providerRequests.length;
  results.finished_at = new Date().toISOString();
  results.passed = !results.failure && results.cases.length === 3 &&
    results.cases.every(item => item.passed) && results.source_unchanged;
  const saved = process.env.MOYU_ANALYSIS_GUIDANCE_BROWSER_REPORT ? {
    ...results, artifacts: 'Private isolated artifacts retained locally; screenshots listed in this report.',
  } : results;
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});
  fs.writeFileSync(reportPath, JSON.stringify(saved, null, 2) + '\n');
  fs.writeFileSync(path.join(temporary, 'http-and-provider.json'), JSON.stringify({
    synthetic_only: true, paid_provider_calls: 0, http: httpExchanges, provider_requests: providerRequests,
  }, null, 2) + '\n');
  fs.writeFileSync(path.join(temporary, 'server.log'), serverLog);
  if (!results.passed) process.exitCode = 1;
  console.log(JSON.stringify(results, null, 2));
});
