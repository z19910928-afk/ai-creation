'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const {spawn, spawnSync} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-knowledge-inbox-'));
const database = path.join(temporary, 'story.sqlite3');
const python = process.env.MOYU_TEST_PYTHON || 'python3';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const report = {passed: false, cases: [], artifacts: temporary, screenshots: [], page_errors: [],
  paid_provider_calls: 0, model_request_posts: 0, browser_mutations: [], seed_agent_plans: 0,
  scope: '真实桌面 Chrome、隔离 SQLite；知识出入链、原句与全书待办；不授权模型任务',
  source_files: ['server.py', 'moyu_knowledge.py', 'moyu_inbox.py',
    'app.js', 'author-workspace.js', 'knowledge-workspace.js', 'writing-inbox.js',
    'knowledge-workspace.css',
    'tests/knowledge_inbox_browser_regressions.cjs'].map(file => ({
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
  for (let index = 0; index < 180; index += 1) {
    if (app && app.exitCode !== null) throw new Error('Server exited: ' + serverLog.slice(-2500));
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
  await page.locator('[data-view="editor"]').click();
  await saved(page);
}
async function openKnowledge(page, id = 'browser_lan') {
  await page.locator('[data-library-tab="knowledge"]').click();
  await page.locator(`[data-record-knowledge="${id}"]`).click();
  await page.locator('[data-knowledge-tab="outgoing"]').waitFor();
}
async function knowledgeTab(page, tab) {
  await page.locator(`[data-knowledge-tab="${tab}"]`).click();
  await page.locator(`[data-knowledge-tab="${tab}"][aria-selected="true"]`).waitFor();
}
async function openInbox(page, kind = 'all') {
  await page.locator('#writingInboxBtn').click();
  await page.waitForFunction(() => document.querySelector('[data-inbox-kind-count="all"]')?.textContent !== '…');
  await page.locator(`[data-inbox-kind="${kind}"]`).click();
  await page.waitForFunction(() => !!document.querySelector('[data-inbox-next]'));
}
async function findInboxRow(page, id) {
  for (let index = 0; index < 20; index += 1) {
    await page.waitForFunction(() => !!document.querySelector('[data-inbox-next]') &&
      !!document.querySelector('[data-inbox-row]'));
    const row = page.locator(`[data-inbox-row="${id}"]`);
    if (await row.count()) return row;
    assert.equal(await page.locator('[data-inbox-next]').isEnabled(), true, `Missing inbox item: ${id}`);
    await page.locator('[data-inbox-next]').click();
  }
  throw new Error('Inbox item was not found within twenty pages: ' + id);
}
async function position(page) {
  return page.locator('#chapterBody').evaluate(input => ({
    start: input.selectionStart, end: input.selectionEnd, direction: input.selectionDirection,
    top: input.scrollTop, panel: input.closest('.workbench-manuscript').scrollTop,
  }));
}
async function screenshot(page, name) {
  const file = path.join(temporary, name);
  await page.screenshot({path: file});
  report.screenshots.push(file);
}
async function test(name, fn) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  try {await fn();} catch (error) {
    report.cases.push({name, passed: false, error: error.message});
    console.log(JSON.stringify({test: name, passed: false, error: error.message}));
    if (browser) {
      const page = browser.contexts()[0]?.pages()[0];
      if (page && !page.isClosed()) {
        await screenshot(page, `failure-${report.cases.length}.png`);
        await page.evaluate(() => {
          closeRecordKnowledge(false); dismissMemoryEvidence();
          document.querySelectorAll('[data-author-chapter-report],[data-inbox-job-dialog]').forEach(node => node.remove());
        });
      }
    }
    return;
  }
  report.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, passed: true}));
}
async function holdNext(page, pattern, transform) {
  let release, entered, responseReady, used = false;
  const gate = new Promise(resolve => {release = resolve;});
  const reached = new Promise(resolve => {entered = resolve;});
  const ready = new Promise(resolve => {responseReady = resolve;});
  const handler = async route => {
    if (used) return route.continue();
    used = true;
    const response = await route.fetch();
    responseReady();
    entered();
    await gate;
    try {
      await route.fulfill(transform ? {response, json: transform(await response.json())} : {response});
    } catch {}
  };
  await page.route(pattern, handler);
  return {entered: reached, ready, async finish() {
    release(); await pause(150); await page.unroute(pattern, handler);
  }};
}

function seedReports(chapterId, quote) {
  const script = `
import json, sys
from contextlib import closing
import server, moyu_analysis
scope = {"project_id": server.PROJECT_ID, "branch_id": server.BRANCH_ID}
chapter_id, quote = sys.argv[1:3]
result = {"analyses": []}
with closing(server.connect()) as conn, conn:
    context, chapter = server.ai_context(conn, chapter_id, task="chapter_analysis")
    analysis = moyu_analysis.save_analysis(conn, {**scope, "sent_context": context}, chapter, None, {
        "summary": {"text": "较早的合成回顾：林岚仍在旧门外。", "evidence": [{"source_quote": quote}]},
        "handoff": {"text": "", "evidence": []}, "candidates": []})
    original = conn.execute("SELECT * FROM app_chapter_analysis WHERE id=?", (analysis["id"],)).fetchone()
    newer = json.loads(original["payload_json"])
    newer["id"], newer["analysis_key"] = "browser_analysis_newer", "browser_synthetic_newer"
    newer["summary"]["text"] = "较新的合成回顾：她还没有离开。"
    stamp = server.now()
    conn.execute("INSERT INTO app_chapter_analysis(id,project_id,branch_id,chapter_id,"
        "source_revision_id,chapter_hash,run_id,analysis_key,payload_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (newer["id"], scope["project_id"], scope["branch_id"], chapter_id,
         original["source_revision_id"], original["chapter_hash"], None, newer["analysis_key"],
         server.json_text(newer), stamp, stamp))
    result["analyses"] = [analysis["id"], newer["id"]]
    context, chapter = server.ai_context(conn, chapter_id, task="semantic_review")
    review = moyu_analysis.save_review(conn, {**scope, "sent_context": context}, chapter, None, {
        "findings": [{"category": "continuity", "severity": "possible",
            "message": "合成审稿问题：铜钥匙的位置需要核对。",
            "evidence": [{"source_chapter_id": chapter_id, "source_quote": quote}]}],
        "uncertainties": ["仅验证操作流程，不评价文学质量。"]})
    result["review"] = review["id"]
    context, chapter = server.ai_context(conn, chapter_id, task="continue")
    context["_source"] = server.candidate_source(conn, "continue", chapter)
    raw = server.json_text(context)
    stamp = server.now()
    conn.execute("INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,"
        "system_prompt_hash,prompt_hash,input_hash,started_at,status,author_decision,consent_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("browser_unknown_run", scope["project_id"], scope["branch_id"], "fixture", "local",
         "synthetic", "fixed", "fixed", server.hash_text(raw), stamp, "unknown", "pending", "{}"))
    conn.execute("INSERT INTO app_ai_job(id,project_id,branch_id,ai_run_id,task,chapter_id,"
        "instruction,context_json,response_format,max_tokens,status,result_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("browser_unknown_job", scope["project_id"], scope["branch_id"], "browser_unknown_run",
         "continue", chapter_id, "合成执行未知", raw, "text", 400, "failed",
         server.json_text({"text": "合成的未完整片段。", "safe_to_apply": False,
                           "completion_state": "unknown", "truncated": True}), stamp, stamp))
print(json.dumps(result, ensure_ascii=False))
`;
  const seeded = spawnSync(python, ['-c', script, chapterId, quote], {cwd: root, encoding: 'utf8',
    env: {...process.env, MOYU_DB: database, MOYU_AI_API_KEY: '', MOYU_SECRET_KEY: '',
      MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials')}});
  assert.equal(seeded.status, 0, seeded.stderr);
  return JSON.parse(seeded.stdout.trim());
}

async function main() {
  const port = await freePort(); base = `http://127.0.0.1:${port}`;
  assert.notEqual(port, 4174);
  app = spawn(python, ['server.py'], {cwd: root,
    env: {...process.env, MOYU_DB: database, MOYU_HOST: '127.0.0.1', MOYU_PORT: String(port),
      MOYU_AI_BASE_URL: 'http://127.0.0.1:1/v1', MOYU_AI_API_KEY: '', MOYU_SECRET_KEY: '',
      MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials'),
      MOYU_BACKUP_DIR: path.join(temporary, 'backups')}, stdio: ['ignore', 'ignore', 'pipe']});
  app.stderr.on('data', chunk => {serverLog += chunk.toString();});
  await until(async () => {try {return (await fetch(base + '/api/health')).ok;} catch {return false;}}, 'startup');

  const state = await api('/api/state'), first = state.chapters[0];
  const quote = '林岚把🗝️铜钥匙放回门边。', alternateQuote = '窗外的灯仍然亮着。';
  const prefix = '渡口的雨还没有停。\n'.repeat(32);
  first.body = prefix + quote + '\n' + alternateQuote + '\n' + '她沿着河岸寻找那座旧桥。\n'.repeat(150);
  first.pov = '林岚';
  const sourceStart = Array.from(prefix).length;
  const secondId = 'browser_knowledge_second';
  state.chapters.push({...first, id: secondId, title: '第二章 另一条街',
    body: '第二章的合成正文。\n门外的人尚未发现铜钥匙。', pov: '林岚'});
  state.records = [
    {id: 'browser_lan', name: '林岚', type: '人物', description: '在旧城寻找失散家人的渡船人。'},
    {id: 'browser_key', name: '铜钥匙', type: '物品', description: '门边留下的一把旧钥匙。'},
    {id: 'browser_secret', name: '第九章的秘密', type: '设定', description: '旧桥下藏着第二封信。',
      available_from: 9, private: true, known_by: ['browser_key']},
  ];
  await api('/api/state', 'PUT', state);
  for (let index = 0; index < 28; index += 1) {
    await api('/api/story/relationships', 'POST', {id: `browser_link_${String(index).padStart(2, '0')}`,
      from_entity_id: 'browser_lan', to_entity_id: index === 27 ? 'browser_secret' : 'browser_key',
      relation_type: index === 27 ? '尚未得知' : `关系 ${index + 1}`,
      note: index === 27 ? '作者留到第九章公开的计划。' : '作者暂定的合成关系，尚非已发生事实。'});
  }
  await api('/api/story/relationships', 'POST', {id: 'browser_reverse', from_entity_id: 'browser_key',
    to_entity_id: 'browser_lan', relation_type: '被携带', note: '反向查看应保留同一关系编号。'});
  const accepted = await api('/api/memory', 'POST', {id: 'browser_accepted', type: '人物', name: '林岚',
    status: 'accepted', source_chapter_id: first.id, source_quote: quote,
    source_start: sourceStart, source_end: sourceStart + Array.from(quote).length});
  const candidate = await api('/api/memory', 'POST', {id: 'browser_candidate', type: '人物', name: '林岚',
    status: 'candidate', note: '独立记忆待作者确认。', source_chapter_id: first.id, source_quote: quote,
    source_start: sourceStart, source_end: sourceStart + Array.from(quote).length});
  const agentIds = [];
  for (let index = 0; index < 56; index += 1) {
    const plan = await api('/api/writing-agent', 'POST', {request_id: `browser_inbox_plan_${index}`,
      chapter_id: first.id, query: `合成待办计划 ${index}`, mode: 'inspect'});
    agentIds.push(plan.session.id); report.seed_agent_plans += 1;
  }
  const seeded = seedReports(first.id, quote);
  assert.notEqual(seeded.analyses[0], seeded.analyses[1]);
  const other = await api('/api/projects', 'POST', {title: '知识与待办隔离作品'});
  const otherId = other.project.id;
  const otherBaseline = await api('/api/state', 'GET', undefined, otherId);
  let baseline = await api('/api/state');
  let inbox;
  assert.equal((await api('/api/writing-agent')).sessions.some(row => row.id === agentIds[0]), false);

  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
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

  await test('打开知识关联保留正文 DOM、选区方向与两层滚动', async () => {
    await page.locator('[data-library-tab="knowledge"]').click();
    await page.locator('#chapterBody').evaluate(input => {
      globalThis.__knowledgeEditor = input; input.focus({preventScroll: true});
      input.setSelectionRange(510, 530, 'backward'); input.scrollTop = 420;
      input.closest('.workbench-manuscript').scrollTop = 65;
    });
    const before = await position(page);
    await page.locator('[data-record-knowledge="browser_lan"]').click();
    await page.locator('[data-knowledge-tab="outgoing"]').waitFor();
    assert.equal(await page.evaluate(() => globalThis.__knowledgeEditor === document.querySelector('#chapterBody')), true);
    assert.deepEqual(await position(page), before);
    assert.match(await page.locator('[data-knowledge-tab="outgoing"]').textContent(), /28/);
    assert.match(await page.locator('[data-knowledge-tab="incoming"]').textContent(), /1/);
    assert.equal(await page.locator('[data-knowledge-row]').count(), 25);
    assert.match(await page.locator('[data-knowledge-row]').first().textContent(), /作者计划/);
    await screenshot(page, 'knowledge-links-1440x1000.png');
  });
  await test('知识独立分页、出入链跳转与未来秘密范围清楚可见', async () => {
    await page.locator('[data-knowledge-next]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-knowledge-row]').length === 3);
    assert.match(await page.locator('.knowledge-pagination').textContent(), /26–28 \/ 28/);
    await page.locator('[data-knowledge-open="browser_secret"]').click();
    await page.waitForFunction(() => document.querySelector('[data-knowledge-name]')?.textContent === '第九章的秘密');
    assert.match(await page.locator('.knowledge-availability').textContent(), /尚未|以后|公开|章节|第/);
    assert.equal(await page.locator('.knowledge-availability.is-restricted').count(), 1);
    await knowledgeTab(page, 'incoming');
    assert.equal(await page.locator('[data-knowledge-open="browser_lan"]').count(), 1);
    await page.locator('[data-knowledge-open="browser_lan"]').click();
    await page.locator('[data-knowledge-tab="outgoing"]').waitFor();
    assert.equal(await page.locator('[data-knowledge-row]').count(), 25);
    await knowledgeTab(page, 'incoming');
    assert.match(await page.locator('[data-knowledge-row]').textContent(), /铜钥匙/);
    await page.locator('[data-knowledge-open="browser_key"]').click();
    await page.waitForFunction(() => document.querySelector('[data-knowledge-name]')?.textContent === '铜钥匙');
    await knowledgeTab(page, 'incoming');
    assert.equal(await page.locator('[data-knowledge-row]').count(), 25);
    await page.locator('[data-knowledge-close]').click();
  });
  await test('候选和已确认来源显示完整 emoji 原句，定位按 Unicode 字符验证', async () => {
    await page.setViewportSize({width: 1280, height: 800});
    await openKnowledge(page); await knowledgeTab(page, 'sources');
    const rows = await api(`/api/records/browser_lan/knowledge?chapter_id=${first.id}`);
    assert.ok(rows.sources.some(row => row.id === accepted.id));
    assert.ok(rows.sources.some(row => row.id === candidate.id));
    assert.equal(await page.locator('[data-knowledge-row]').count(), rows.summary.sources);
    assert.ok((await page.locator('[data-knowledge-content]').textContent()).includes(quote));
    assert.match(await page.locator('[data-knowledge-content]').textContent(), /候选|待确认/);
    await screenshot(page, 'knowledge-sources-1280x800.png');
    await page.locator('[data-knowledge-source]').first().click();
    await page.waitForFunction(() => !document.querySelector('[data-record-knowledge-dialog]'));
    const selection = await page.locator('#chapterBody').evaluate(input => input.value.slice(input.selectionStart, input.selectionEnd));
    assert.equal(selection, quote);
    assert.equal(await page.evaluate(() => activeChapter), first.id);
  });
  await test('点击定位前重新读取来源，已变原句阻止跳转且保留当前选区', async () => {
    await openKnowledge(page); await knowledgeTab(page, 'sources');
    const displayed = await api(`/api/records/browser_lan/knowledge?chapter_id=${first.id}`);
    const index = displayed.sources.findIndex(row => row.id === candidate.id);
    assert.ok(index >= 0);
    const before = await position(page);
    const alternateStart = Array.from(first.body.slice(0, first.body.indexOf(alternateQuote))).length;
    await api(`/api/memory/${candidate.id}/reanchor`, 'POST', {source_chapter_id: first.id,
      source_quote: alternateQuote, source_start: alternateStart, source_end: alternateStart + Array.from(alternateQuote).length});
    await page.locator(`[data-knowledge-source="${index}"]`).click();
    await page.waitForFunction(() => document.querySelector('[data-knowledge-error]')?.textContent.includes('来源已经变化'));
    assert.equal(await page.locator('[data-record-knowledge-dialog]').count(), 1);
    assert.deepEqual(await position(page), before);
    await page.locator('[data-knowledge-close]').click();
    await api(`/api/memory/${candidate.id}/reanchor`, 'POST', {source_chapter_id: first.id,
      source_quote: quote, source_start: sourceStart, source_end: sourceStart + Array.from(quote).length});
  });
  await test('原句复核期间切标签、刷新、关闭或切页后，迟到结果不能继续导航', async () => {
    const pattern = '**/api/records/browser_lan/knowledge?**';
    for (const action of ['tab', 'refresh', 'close', 'page']) {
      await openKnowledge(page); await knowledgeTab(page, 'sources');
      const before = await position(page);
      const delayed = await holdNext(page, pattern);
      await page.locator('[data-knowledge-source]').first().click();
      await delayed.entered;
      if (action === 'tab') await knowledgeTab(page, 'outgoing');
      else if (action === 'refresh') {
        await page.locator('[data-knowledge-refresh]').click();
        await page.locator('[data-knowledge-tab="sources"]').waitFor();
      } else if (action === 'close') await page.locator('[data-knowledge-close]').click();
      else await page.evaluate(() => {view = 'memory'; render();});
      await delayed.finish();
      assert.equal(await page.evaluate(() => activeChapter), first.id);
      if (action === 'page') {
        assert.equal(await page.evaluate(() => view), 'memory');
        await page.locator('[data-view="editor"]').click();
      } else {
        assert.deepEqual(await position(page), before);
        if (action !== 'close') {
          assert.equal(await page.locator('[data-record-knowledge-dialog]').count(), 1);
          await page.locator('[data-knowledge-close]').click();
        }
      }
    }
  });
  await test('知识读取失败显示未核实与重试，不伪装为零条来源', async () => {
    const pattern = '**/api/records/browser_lan/knowledge?**';
    await page.route(pattern, route => route.fulfill({status: 503, contentType: 'application/json',
      body: JSON.stringify({error: '合成读取失败'})}));
    await page.locator('[data-record-knowledge="browser_lan"]').click();
    await page.locator('[data-knowledge-retry]').waitFor();
    assert.match(await page.locator('[data-knowledge-content]').textContent(), /不能判断没有来源/);
    assert.equal(await page.locator('.knowledge-pagination').count(), 0);
    await page.unroute(pattern);
    await page.locator('[data-knowledge-retry]').click();
    await page.locator('[data-knowledge-tab="sources"]').waitFor();
    await page.locator('[data-knowledge-close]').click();
  });
  await test('知识迟到响应在关闭、切章和切作品后不复活', async () => {
    const pattern = '**/api/records/browser_lan/knowledge?**';
    for (const action of ['close', 'chapter', 'project']) {
      if (action !== 'close') await selectChapter(page, first.id);
      await page.locator('[data-library-tab="knowledge"]').click();
      const delayed = await holdNext(page, pattern);
      await page.locator('[data-record-knowledge="browser_lan"]').click();
      await delayed.entered;
      if (action === 'close') await page.locator('[data-knowledge-close]').click();
      else if (action === 'chapter') {
        await page.evaluate(() => {activeChapter = 'browser_knowledge_second'; render();});
      } else {
        await page.evaluate(() => {view = 'library'; render();});
        await openProject(page, otherId);
      }
      await delayed.finish();
      assert.equal(await page.locator('[data-record-knowledge-dialog]').count(), 0, action);
      if (action === 'project') await openProject(page, 'default-project');
    }
    await selectChapter(page, first.id);
  });
  await test('关系删除导致页码越界时回到最后有效页，不留下虚假的空页', async () => {
    await openKnowledge(page);
    await page.locator('[data-knowledge-next]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-knowledge-row]').length === 3);
    for (let index = 25; index < 28; index += 1) {
      await api(`/api/story/relationships/browser_link_${String(index).padStart(2, '0')}`, 'DELETE');
    }
    await page.locator('[data-knowledge-refresh]').click();
    await page.waitForFunction(() => document.querySelector('.knowledge-pagination')?.textContent.includes('1–25 / 25'));
    assert.equal(await page.locator('[data-knowledge-row]').count(), 25);
    assert.equal(await page.locator('[data-knowledge-prev]').isDisabled(), true);
    assert.equal(await page.locator('[data-knowledge-next]').isDisabled(), true);
    await page.locator('[data-knowledge-close]').click();
    baseline = await api('/api/state');
  });

  await test('全书待办完整分类计数、三页任务无重漏，桌面布局无横向溢出', async () => {
    inbox = await api('/api/writing-inbox?limit=100');
    report.fixture_counts = inbox.counts;
    assert.equal(inbox.counts.agent, 56);
    await page.setViewportSize({width: 1440, height: 1000});
    await openInbox(page);
    assert.equal(await page.locator('[data-inbox-kind-count="all"]').textContent(), String(inbox.total_all));
    for (const [kind, count] of Object.entries(inbox.counts)) {
      assert.equal(await page.locator(`[data-inbox-kind-count="${kind}"]`).textContent(), String(count));
    }
    await screenshot(page, 'writing-inbox-1440x1000.png');
    await page.locator('[data-inbox-kind="agent"]').click();
    const ids = [];
    for (let offset = 0; offset < 56; offset += 25) {
      await page.waitForFunction(count => document.querySelectorAll('[data-inbox-row]').length === count, Math.min(25, 56 - offset));
      ids.push(...await page.locator('[data-inbox-row]').evaluateAll(rows => rows.map(row => row.dataset.inboxRow)));
      if (offset < 50) {
        await page.locator('[data-inbox-next]').click();
        await page.waitForFunction(start => document.querySelector('#writingInboxPagination')?.textContent.includes(start), `${offset + 26}–`);
      }
    }
    assert.equal(new Set(ids).size, 56);
    await page.setViewportSize({width: 1280, height: 800});
    await page.locator('[data-inbox-kind="all"]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-inbox-row]').length === 25);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await screenshot(page, 'writing-inbox-1280x800.png');
  });
  await test('超过最近五十条的旧写作任务能从待办精确打开且刷新仍只展示目标', async () => {
    await openInbox(page, 'agent');
    const oldTask = await findInboxRow(page, `agent:${agentIds[0]}`);
    await oldTask.locator('[data-inbox-open]').click();
    await page.locator(`[data-agent-session="${agentIds[0]}"]`).waitFor();
    assert.equal(await page.locator('[data-agent-session]').count(), 1);
    assert.match(await page.locator('[data-agent-session]').textContent(), /合成待办计划 0/);
    await page.evaluate(() => authorLoadAgent());
    assert.equal(await page.locator('[data-agent-session]').count(), 1);
    assert.equal(await page.locator(`[data-agent-session="${agentIds[0]}"]`).count(), 1);
  });
  await test('章后记忆按选中分析编号打开，独立记忆和审稿均有真实入口', async () => {
    await openInbox(page, 'analysis');
    await page.locator(`[data-inbox-row="analysis:${seeded.analyses[0]}"] [data-inbox-open]`).click();
    await page.locator('[data-author-chapter-report="analysis"]').waitFor();
    assert.equal(await page.locator('#analysisSummary').inputValue(), '较早的合成回顾：林岚仍在旧门外。');
    await page.locator('[data-author-chapter-report="analysis"] [data-close]').click();
    await openInbox(page, 'analysis');
    await page.locator(`[data-inbox-row="analysis:${candidate.id}"] [data-inbox-open]`).click();
    await page.locator('[data-inbox-memory-focus]').waitFor();
    assert.equal(await page.evaluate(() => view), 'memory');
    assert.equal(await page.locator(`[data-inbox-memory-focus] [data-memory="${candidate.id}"]`).isEnabled(), true);
    assert.equal(await page.locator(`[data-inbox-memory-focus] [data-memory-reject="${candidate.id}"]`).isEnabled(), true);
    await page.locator(`[data-inbox-memory-focus] [data-memory-evidence="${candidate.id}"]`).click();
    await page.locator('[data-memory-evidence-dialog]').waitFor();
    assert.ok((await page.locator('[data-memory-evidence-dialog]').textContent()).includes(quote));
    await page.locator('[data-memory-evidence-dialog] [data-close]').click();
    await openInbox(page, 'review');
    await page.locator(`[data-inbox-row="review:${first.id}"] [data-inbox-open]`).click();
    await page.locator('[data-author-chapter-report="review"]').waitFor();
    assert.match(await page.locator('[data-author-chapter-report="review"]').textContent(), /合成审稿问题：铜钥匙的位置需要核对/);
    await page.locator('[data-author-chapter-report="review"] [data-close]').click();
  });
  await test('执行结果未知只可查看和打开章节，不自动重试或产生模型请求', async () => {
    const before = report.model_request_posts;
    await openInbox(page, 'queue');
    await page.locator('[data-inbox-row="queue:browser_unknown_job"] [data-inbox-open]').click();
    await page.locator('[data-inbox-job-dialog]').waitFor();
    assert.match(await page.locator('[data-inbox-job-dialog]').textContent(), /执行结果尚未确定/);
    assert.match(await page.locator('[data-inbox-job-dialog]').textContent(), /合成的未完整片段/);
    const buttons = await page.locator('[data-inbox-job-dialog] button').allTextContents();
    assert.deepEqual(buttons, ['关闭', '打开章节']);
    await page.locator('[data-inbox-job-chapter]').click();
    assert.equal(await page.evaluate(() => activeChapter), first.id);
    assert.equal(report.model_request_posts, before);
  });
  await test('待办读取失败和坏计数显示尚未核实，恢复后重新呈现完整数量', async () => {
    for (const invalid of [false, true]) {
      const pattern = '**/api/writing-inbox?**';
      await page.route(pattern, async route => {
        if (!invalid) return route.fulfill({status: 503, contentType: 'application/json',
          body: JSON.stringify({error: '合成待办读取失败'})});
        const response = await route.fetch(), body = await response.json();
        body.total_all += 1;
        await route.fulfill({response, json: body});
      });
      await page.locator('#writingInboxBtn').click();
      await page.waitForFunction(() => document.querySelector('#writingInboxTotal')?.textContent === '尚未核实');
      assert.match(await page.locator('#writingInboxItems').textContent(), /不能据此判断已全部处理/);
      assert.equal(await page.locator('[data-inbox-kind-count="all"]').textContent(), '…');
      assert.equal(await page.locator('#writingInboxBtn small').textContent(), '…');
      await page.unroute(pattern);
    }
    await page.locator('[data-inbox-refresh]').click();
    await page.waitForFunction(value => document.querySelector('[data-inbox-kind-count="all"]')?.textContent === String(value), inbox.total_all);
  });
  await test('较早的顶部待办摘要迟到，不能覆盖已经读到的新列表数量', async () => {
    await page.locator('[data-view="editor"]').click();
    const delayed = await holdNext(page, '**/api/writing-inbox?limit=1', body => ({
      ...body, total: 0, total_all: 0, counts: {agent: 0, queue: 0, analysis: 0, review: 0}, items: [],
    }));
    await page.evaluate(() => {void refreshWritingInboxCount();});
    await delayed.entered;
    await openInbox(page);
    assert.equal(await page.locator('#writingInboxBtn small').textContent(), String(inbox.total_all));
    await delayed.finish();
    assert.equal(await page.locator('#writingInboxBtn small').textContent(), String(inbox.total_all));
    assert.equal(await page.locator('[data-inbox-kind-count="all"]').textContent(), String(inbox.total_all));
  });
  await test('离开待办或切作品后迟到列表不会覆盖新页面或另一本书的计数', async () => {
    await openInbox(page);
    let delayed = await holdNext(page, '**/api/writing-inbox?kind=all&limit=25&offset=0');
    await page.locator('[data-inbox-refresh]').click();
    await delayed.entered;
    await page.locator('[data-view="editor"]').click();
    await delayed.finish();
    assert.equal(await page.locator('#writingInboxItems').count(), 0);
    await openInbox(page);
    delayed = await holdNext(page, '**/api/writing-inbox?kind=all&limit=25&offset=0');
    await page.locator('[data-inbox-refresh]').click();
    await delayed.entered;
    await openProject(page, otherId);
    await delayed.finish();
    await openInbox(page);
    assert.equal(await page.locator('[data-inbox-kind-count="agent"]').textContent(), '0');
    assert.equal(await page.locator('[data-inbox-row^="agent:"]').count(), 0);
    assert.equal(await page.evaluate(() => requestedProject), otherId);
    await openProject(page, 'default-project');
  });
  await test('各类待办详情请求未完成时离开页面，迟到结果不能导航或弹出旧记录', async () => {
    const targets = [
      {kind: 'agent', row: `agent:${agentIds[0]}`, pattern: `**/api/writing-agent?session_id=${agentIds[0]}`},
      {kind: 'analysis', row: `analysis:${seeded.analyses[0]}`, pattern: '**/api/chapter-analysis?chapter_id=**'},
      {kind: 'analysis', row: `analysis:${candidate.id}`, pattern: '**/api/state'},
      {kind: 'review', row: `review:${first.id}`, pattern: '**/api/story-reviews?chapter_id=**'},
      {kind: 'queue', row: 'queue:browser_unknown_job', pattern: '**/api/ai/jobs/browser_unknown_job'},
    ];
    for (const target of targets) {
      await openInbox(page, target.kind);
      if (target.kind === 'agent') {
        await findInboxRow(page, target.row);
      }
      const delayed = await holdNext(page, target.pattern);
      await page.locator(`[data-inbox-row="${target.row}"] [data-inbox-open]`).click();
      await delayed.entered;
      await page.locator('[data-view="editor"]').click();
      await delayed.finish();
      assert.equal(await page.evaluate(() => view), 'editor', target.row);
      assert.equal(await page.locator('[data-author-chapter-report],[data-inbox-job-dialog],[data-memory-evidence-dialog]').count(), 0, target.row);
      assert.equal(await page.locator('[data-inbox-memory-focus]').count(), 0, target.row);
    }
  });
  await test('浏览只读操作不修改正文版本，也不派发模型请求或报浏览器错误', async () => {
    await saved(page);
    const after = await api('/api/state');
    assert.equal(after.version, baseline.version);
    assert.equal(after.head_revision, baseline.head_revision);
    assert.deepEqual(after.chapters, baseline.chapters);
    const otherAfter = await api('/api/state', 'GET', undefined, otherId);
    assert.equal(otherAfter.version, otherBaseline.version);
    assert.equal(otherAfter.head_revision, otherBaseline.head_revision);
    assert.deepEqual(otherAfter.chapters, otherBaseline.chapters);
    assert.equal(report.model_request_posts, 0);
    assert.equal(report.paid_provider_calls, 0);
    assert.deepEqual(report.browser_mutations, []);
    assert.deepEqual(report.page_errors, []);
  });
  report.source_unchanged = report.source_files.every(file =>
    hash(fs.readFileSync(path.join(root, file.path))) === file.sha256);
  assert.equal(report.source_unchanged, true);
  assert.equal(report.cases.every(item => item.passed), true, 'Some browser cases failed; inspect cases and screenshots.');
  report.passed = true;
  await context.close();
}

main().catch(error => {
  report.error = error.stack; report.server_log = serverLog.slice(-4000); process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (app && app.exitCode === null) {
    const exited = new Promise(resolve => app.once('exit', resolve));
    app.kill('SIGTERM'); await exited;
  }
  report.source_unchanged = report.source_files.every(file =>
    hash(fs.readFileSync(path.join(root, file.path))) === file.sha256);
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
});
