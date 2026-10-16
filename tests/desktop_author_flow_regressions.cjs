'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-desktop-author-flow-'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = text => crypto.createHash('sha256').update(text).digest('hex');
const sourceFiles = fs.readdirSync(root).filter(file => /\.(py|js)$/.test(file))
  .concat(['index.html', 'styles.css', 'tests/desktop_author_flow_regressions.cjs']).sort();
const fingerprints = () => Object.fromEntries(sourceFiles.map(file =>
  [file, hash(fs.readFileSync(path.join(root, file)))]));
const results = {
  passed: false,
  scope: 'Real desktop Chrome, isolated SQLite, free local provider; all author mutations use visible controls.',
  viewport: {width: 1440, height: 1000},
  started_at: new Date().toISOString(),
  source_files_sha256_before: fingerprints(),
  cases: [],
  artifacts: temporary,
  provider_requests: [],
  analysis_candidate_checks: [],
};
const story = [
  {
    title: '第一章 铜匙',
    quote: '林岚把刻着月牙的铜匙藏进左靴。',
    body: '😀渡口的钟声刚响过三遍，林岚便推开了守灯人的门。\n林岚把刻着月牙的铜匙藏进左靴。\n守灯人只给她一盏没有点亮的灯，催她沿着退潮后的石阶往北走。她没有问铜匙的来历，只把门外那条白色水线记在心里。',
  },
  {
    title: '第二章 石阶',
    body: '😀林岚沿着湿冷的石阶往北走，靴底压出细小的水声。\n桥头卖茶的人认出了灯罩上的缺口，却不肯告诉她昨夜谁走过这里。她把唯一的铜钱压在茶碗旁，带走一张被雨打湿的渡口图，决定先找旧闸门。',
  },
  {
    title: '第三章 月牙',
    quote: '旧闸门的锁眼边刻着同样的月牙，铜匙在靴里碰响了一下。',
    body: '😀潮水退到了最远的礁石后面，旧闸门露出了半张铁面。\n旧闸门的锁眼边刻着同样的月牙，铜匙在靴里碰响了一下。\n林岚蹲下看清那些新划痕，知道有人先她一步试过这道门。她没有立即取出铜匙，而是循着泥里的脚印找到了门后的排水沟。',
  },
  {
    title: '第四章 排水沟',
    body: '😀排水沟里没有人，只有一只刚被拖动的木箱。\n林岚把灯罩靠在石壁上，听见箱底有水慢慢流走。她发现箱盖上的绳结与守灯人系灯绳的动作一样，于是收起渡口图，从墙缝挤过身去。等她回到闸门前，远处已有第一缕潮声。',
  },
  {
    title: '第五章 开闸',
    quote: '林岚取出月牙铜匙打开旧闸门，让困在下游的守灯人走了出来。',
    body: '😀第一道潮水撞上石阶时，林岚终于把脚跟从泥里拔出来。\n林岚取出月牙铜匙打开旧闸门，让困在下游的守灯人走了出来。\n守灯人把灯点亮，光穿过门缝照在那只木箱上。她没有再追问昨夜的脚印，只扶住老人，一起沿着白色水线回到渡口，把铜匙放在已经干透的茶碗旁。',
  },
];
const plotText = '月牙铜匙在旧闸门救出守灯人';
let app, provider, browser, page, base, project;
let serverLog = '', providerError = '';
const browserErrors = [], externalRequests = [], writes = [];

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function readApi(route) {
  const response = await fetch(base + route, {headers: {'X-Moyu-Project': project || 'default-project'}});
  const data = await response.json();
  assert.equal(response.ok, true, `${route}: ${response.status} ${JSON.stringify(data)}`);
  return data;
}

async function test(name, action) {
  console.log(JSON.stringify({test: name, state: 'running'}));
  await action();
  results.cases.push({name, passed: true});
  console.log(JSON.stringify({test: name, state: 'passed'}));
}

async function waitSaved() {
  try {
    await page.waitForFunction(() => backendOnline && confirmedState && !isDirty() &&
      !syncInFlight && !independentWrite && !adopting && !saveError && !mergePending, null, {timeout: 15000});
  } catch (error) {
    const state = await page.evaluate(() => ({
      online: backendOnline, dirty: isDirty(), saving: syncInFlight,
      independentWrite, error: saveError, merge: !!mergePending,
      status: document.querySelector('#saveState')?.innerText,
    }));
    throw new Error(`${error.message}; save state: ${JSON.stringify(state)}`);
  }
}

async function openChapter(id) {
  await page.locator('[data-view="editor"]').click();
  await page.locator(`[data-chapter="${id}"]`).click();
  await page.waitForFunction(value => activeChapter === value, id);
}

async function basicReview(id) {
  await openChapter(id);
  await page.locator('[data-action="reviewChapter"]').click();
  await page.locator('[data-base-review]').click();
  await page.locator('.modal h3').filter({hasText: '本章审查'}).waitFor();
  assert.match(await page.locator('.modal').innerText(), /满足基础归档条件/);
  await page.locator('.modal [data-close]').click();
  await waitSaved();
  const snapshot = await readApi('/api/state');
  assert.equal(snapshot.chapters.find(chapter => chapter.id === id).review_status, 'passed');
}

async function reviewAll(ids) {
  for (const id of ids) await basicReview(id);
  const state = await readApi('/api/state');
  assert.ok(ids.every(id => state.chapters.find(chapter => chapter.id === id).review_status === 'passed'));
}

async function openAnalysisCandidate(id, task, label, body) {
  await page.waitForFunction(value => aiDraft?.task === value.task &&
    aiDraft.source?.chapter_id === value.id && aiDraft.complete, {id, task});
  await page.locator('[data-chat-result="workbenchCandidateSection"]').click();
  await page.locator('#aiTask').selectOption('continue');
  assert.equal(await page.locator('.ai-copilot h3').innerText(), label);
  assert.match(await page.locator('#aiStatus').innerText(), new RegExp(`${label}已生成`));
  const output = await page.locator('#aiOutput').inputValue();
  assert.ok(output.startsWith(label));
  assert.equal(await page.locator('#aiOutput').evaluate(element => element.readOnly), true);
  assert.equal(await page.locator('#aiOutput').getAttribute('aria-label'), `${label}摘要`);
  assert.doesNotMatch(output, /"findings"\s*:|"candidates"\s*:|"uncertainties"\s*:|^\s*[{\[]/);
  assert.throws(() => JSON.parse(output));
  assert.equal(await page.locator('#chapterBody').inputValue(), body);
  assert.equal(await page.locator('#aiAdopt').innerText(), `查看${label}`);
  assert.equal(await page.locator('#aiAdopt').isEnabled(), true);
  if (task === 'chapter_analysis') {
    assert.match(output, /章节回顾：/);
    assert.match(output, /下一章任务：/);
    assert.match(output, /记忆候选：1 条/);
  } else {
    assert.match(output, /仍需作者复核/);
    assert.match(output, /本机固定返回只验证作者工作流，不评判文学质量/);
  }
  const writeStart = writes.length;
  await page.locator('#aiAdopt').click();
  await page.locator('.analysis-modal h3').filter({hasText: task === 'chapter_analysis' ? '章后记忆确认' : '本章审稿'}).waitFor();
  const title = await page.evaluate(value => chapter(value).title, id);
  assert.ok((await page.locator('.analysis-modal').innerText()).includes(title));
  assert.equal(await page.locator('.candidate-modal, [data-apply]').count(), 0);
  assert.equal(await page.locator('#chapterBody').inputValue(), body);
  assert.equal((await readApi('/api/state')).chapters.find(chapter => chapter.id === id).body, body);
  assert.equal(writes.slice(writeStart).filter(write => /\/ai\/runs\/[^/]+\/apply$/.test(write.route)).length, 0);
  results.analysis_candidate_checks.push({
    chapter_id: id, task, title: label, readonly: true, raw_json_hidden: true,
    next_generation_task: 'continue', dedicated_dialog: true, manuscript_unchanged: true,
    preview_sha256: hash(output), manuscript_sha256: hash(body),
  });
}

async function analysisAndReview(id) {
  const body = await page.locator('#chapterBody').inputValue();
  const before = await page.evaluate(() => queueJobs.filter(job => job.task === 'chapter_analysis' && job.status === 'success').length);
  await page.locator('[data-action="chapterAnalysis"]').click();
  await page.locator('[data-extract]').click();
  await page.waitForFunction(count => queueJobs.filter(job => job.task === 'chapter_analysis' &&
    job.status === 'success').length > count, before, {timeout: 15000});
  await openChapter(id);
  await openAnalysisCandidate(id, 'chapter_analysis', '章后记忆', body);
  await page.locator('[data-analysis-decision]').waitFor();
  await page.locator('[data-analysis-decision]').selectOption('accepted');
  await page.locator('#analysisSummaryDecision').selectOption('accepted');
  await page.locator('#analysisHandoffDecision').selectOption('accepted');
  await page.locator('[data-confirm-analysis]').click();
  await page.waitForFunction(() => !document.querySelector('[data-confirm-analysis]'));
  await waitSaved();
  const snapshot = await readApi('/api/state');
  const chapter = snapshot.chapters.find(row => row.id === id);
  assert.ok(chapter.recap);
  assert.ok(chapter.next_brief);
  assert.ok(snapshot.memory.some(memory => memory.source_chapter_id === id && memory.status === '已确认'));
  assert.equal(chapter.body, body);

  const previous = await page.evaluate(() => queueJobs.filter(job => job.task === 'semantic_review' && job.status === 'success').length);
  await page.locator('[data-action="reviewChapter"]').click();
  await page.locator('[data-semantic-review]').click();
  await page.waitForFunction(count => queueJobs.filter(job => job.task === 'semantic_review' &&
    job.status === 'success').length > count, previous, {timeout: 15000});
  await openChapter(id);
  await openAnalysisCandidate(id, 'semantic_review', '故事审稿', body);
  await page.waitForFunction(() => document.querySelector('.analysis-modal')?.textContent.includes('已生成故事审稿报告'));
  const reviews = (await readApi(`/api/story-reviews?chapter_id=${encodeURIComponent(id)}`)).reviews;
  assert.equal(reviews[0].findings.length, 0);
  assert.equal(reviews[0].status, 'no_findings_unverified');
  assert.notEqual(reviews[0].stale, true);
  assert.equal(reviews[0].source_revision_id, chapter.revision_id);
  await page.locator('.analysis-modal [data-close]').click();
}

async function lifecycle(plotId, chapterId, stage, quote, existingLinkId) {
  await page.locator('[data-view="plot"]').click();
  await page.locator(`[data-plot-move="${plotId}"]`).click();
  await page.locator('#plotStage').selectOption(stage);
  await page.locator('#plotSourceChapter').selectOption(chapterId);
  const evidenceLink = page.locator('#plotEvidenceLink');
  if (existingLinkId) {
    await evidenceLink.selectOption(existingLinkId);
    assert.equal(await evidenceLink.inputValue(), existingLinkId);
  } else if (await evidenceLink.count()) {
    await evidenceLink.selectOption('');
  }
  await page.locator('#plotSourceQuote').fill(quote);
  await page.locator('#plotSourceNote').fill(`${stage}阶段由作者按当前正文确认。`);
  await page.locator('.author-form [type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('.author-form'));
  await waitSaved();
  const kind = {setup: 'plant', develop: 'develop', payoff: 'payoff'}[stage];
  const link = (await readApi('/api/story/foreshadow-links')).foreshadowing
    .find(item => item.plot_id === plotId && item.kind === kind && item.chapter_id === chapterId && !item.stale);
  assert.ok(link, `${kind} source was not saved`);
  assert.equal(link.source_quote, quote);
  assert.equal(link.evidence_status, 'valid');
  return link;
}

async function finalDialog() {
  await page.locator('#finalCheckBtn').click();
  await page.locator('.modal h3').filter({hasText: '终稿检查'}).waitFor();
  return page.locator('.modal').filter({has: page.locator('h3', {hasText: '终稿检查'})});
}

async function sourceBodies() {
  const state = await readApi('/api/state');
  return state.chapters.filter(chapter => chapter.body.trim()).map(chapter => [chapter.id, chapter.body]);
}

function providerOutput(input) {
  const system = input.messages?.[0]?.content || '';
  const user = input.messages?.find(message => message.role === 'user')?.content || '';
  assert.ok(user.includes('<manuscript>'));
  const material = JSON.parse(user.split('<manuscript>\n')[1].split('\n</manuscript>')[0]);
  const current = material.current_chapter;
  assert.ok(current?.id && current.body);
  const quote = String(current.body).split(/\r?\n/).find(line => line.trim());
  let task, output;
  if (system.includes('完整普通正文')) {
    task = 'chapter_analysis';
    output = {
      summary: {text: `${current.title}中林岚沿渡口线索继续行动。`, evidence: [{source_quote: quote}]},
      handoff: {text: '沿当前线索继续寻找铜匙与旧闸门的关系。', evidence: [{source_quote: quote}]},
      candidates: [{
        kind: 'event', name: `${current.title}的渡口行动`, note: quote, source_quote: quote,
        epistemic_mode: 'world_truth', known_by: [],
      }],
    };
  } else if (system.includes('人物动机、因果、时间')) {
    task = 'semantic_review';
    output = {findings: [], uncertainties: ['本机固定返回只验证作者工作流，不评判文学质量。']};
  } else {
    throw new Error('Unexpected paid-task shape sent to the local author-flow provider');
  }
  results.provider_requests.push({
    model: input.model, task, chapter_id: current.id,
    input_sha256: hash(JSON.stringify(input)), output_sha256: hash(JSON.stringify(output)),
    local_only: true,
  });
  return JSON.stringify(output);
}

async function main() {
  const providerPort = await freePort();
  provider = http.createServer((request, response) => {
    let raw = '';
    request.on('data', chunk => raw += chunk);
    request.on('end', () => {
      try {
        assert.equal(request.url, '/v1/chat/completions');
        const input = JSON.parse(raw);
        const output = providerOutput(input);
        assert.equal(input.stream, true);
        response.writeHead(200, {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'});
        const middle = Math.floor(output.length / 2);
        response.write(`data: ${JSON.stringify({model: 'desktop-author-mock', choices: [{delta: {content: output.slice(0, middle)}, finish_reason: null}]})}\n\n`);
        setTimeout(() => {
          if (response.destroyed) return;
          response.write(`data: ${JSON.stringify({model: 'desktop-author-mock', choices: [{delta: {content: output.slice(middle)}, finish_reason: 'stop'}], usage: {prompt_tokens: 120, completion_tokens: 100, total_tokens: 220}})}\n\n`);
          response.end('data: [DONE]\n\n');
        }, 80);
      } catch (error) {
        providerError = error.message;
        response.writeHead(500, {'Content-Type': 'application/json'});
        response.end(JSON.stringify({error: error.message}));
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
      MOYU_DB: path.join(temporary, 'test.sqlite3'), MOYU_HOST: '127.0.0.1', MOYU_PORT: String(appPort),
      MOYU_AI_BASE_URL: `http://127.0.0.1:${providerPort}/v1`, MOYU_AI_MODEL: 'desktop-author-mock',
      MOYU_AI_API_KEY: '', MOYU_AI_DAILY_QUOTA: '100', MOYU_SECRET_KEY: '',
      MOYU_BACKUP_DIR: path.join(temporary, 'backups'), MOYU_CREDENTIALS_DIR: path.join(temporary, 'credentials'),
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  app.stderr.on('data', chunk => serverLog += chunk.toString());
  for (let attempt = 0; attempt < 200; attempt++) {
    try { if ((await fetch(base + '/api/health')).ok) break; } catch {}
    if (app.exitCode !== null) throw new Error('server exited: ' + serverLog.slice(-3000));
    if (attempt === 199) throw new Error('server did not start: ' + serverLog.slice(-3000));
    await delay(40);
  }
  browser = await chromium.launch({headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined});
  const context = await browser.newContext({viewport: results.viewport, acceptDownloads: true});
  page = await context.newPage();
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) externalRequests.push(request.url());
    if (!['GET', 'HEAD'].includes(request.method())) writes.push({
      method: request.method(), route: url.pathname, project: request.headers()['x-moyu-project'],
    });
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) ? route.continue() : route.abort('blockedbyclient');
  });
  await page.goto(base);
  await page.waitForFunction(() => backendOnline === true);
  results.default_project_before = (await readApi('/api/state')).chapters.map(chapter => [chapter.id, hash(chapter.body)]);

  const ids = [];
  let plotId, originalLinks;
  await test('作者通过书架创建作品并真实填写五章、记忆确认、审稿和归档', async () => {
    await page.locator('[data-view="library"]').click();
    await page.locator('[data-action="createProject"]').click();
    await page.locator('#newBookTitle').fill('铜匙与白色水线');
    await page.locator('#newBookSummary').fill('林岚沿渡口线索寻找旧闸门，用月牙铜匙救出守灯人。');
    await page.locator('#newBookGenre').fill('悬疑');
    await page.locator('.author-form [type="submit"]').click();
    await page.waitForFunction(() => state.project.title === '铜匙与白色水线');
    await waitSaved();
    project = await page.evaluate(() => requestedProject);
    assert.notEqual(project, 'default-project');
    await page.locator('[data-view="editor"]').click();
    for (const [index, chapter] of story.entries()) {
      const id = await page.evaluate(() => activeChapter);
      ids.push(id);
      await page.locator('#chapterTitle').fill(chapter.title);
      await page.locator('#chapterBody').fill(chapter.body);
      if (!await page.locator('details.chapter-plan').evaluate(element => element.open)) {
        await page.locator('details.chapter-plan > summary').click();
      }
      await page.locator('#chapterGoal').fill(`林岚完成${chapter.title}的具体行动。`);
      await page.locator('#chapterScenePlan').fill('观察渡口环境，循线索行动，承担选择的后果。');
      await page.locator('#chapterPov').fill('林岚');
      await page.locator('#chapterHook').fill('当前行动把她带到下一处线索。');
      await page.locator('#chapterForeshadowing').fill(index === 0 ? '埋下月牙铜匙' : index === 2 ? '推进锁眼月牙' : index === 4 ? '回收铜匙并救人' : '');
      await waitSaved();
      await analysisAndReview(id);
      if (index === 0) {
        await page.locator('[data-view="plot"]').click();
        await page.locator('[data-action="newPlot"]').click();
        await page.locator('#plotText').fill(plotText);
        await page.locator('.author-form [type="submit"]').click();
        await page.waitForFunction(() => !document.querySelector('.author-form'));
        await waitSaved();
        plotId = (await readApi('/api/state')).plot.setup[0].id;
      }
      if ([0, 2, 4].includes(index)) {
        await lifecycle(plotId, id, {0: 'setup', 2: 'develop', 4: 'payoff'}[index], chapter.quote);
      }
      await openChapter(id);
      await page.locator('[data-action="finishChapter"]').click();
      await page.waitForFunction(previous => activeChapter !== previous, id, {timeout: 15000});
      await waitSaved();
      const saved = await readApi('/api/state');
      const current = saved.chapters.find(row => row.id === id);
      assert.equal(current.body, chapter.body);
      assert.equal(current.status, '完成');
      const commits = (await readApi(`/api/chapters/${id}/commits`)).commits;
      for (const stage of ['prewrite', 'precommit', 'postcommit']) {
        assert.ok(commits.some(commit => commit.stage === stage && commit.status === 'passed'));
      }
    }
    assert.equal(new Set(ids).size, 5);
    assert.equal(results.analysis_candidate_checks.length, 10);
    for (const id of ids) {
      assert.deepEqual(results.analysis_candidate_checks.filter(check => check.chapter_id === id)
        .map(check => check.task), ['chapter_analysis', 'semantic_review']);
    }
    const state = await readApi('/api/state');
    assert.equal(state.chapters.filter(chapter => chapter.body.trim()).length, 5);
    assert.equal(state.chapters.length, 6);
    assert.equal(state.chapters[5].body, '');
    assert.equal(state.chapters[5].status, '草稿');
    results.chapter_ids = ids;
  });

  await test('第1章埋、第3章推进、第5章回收原句可定位且刷新后原稿完整', async () => {
    originalLinks = (await readApi('/api/story/foreshadow-links')).foreshadowing.filter(item => item.plot_id === plotId);
    assert.equal(originalLinks.length, 3);
    for (const [kind, index] of [['plant', 0], ['develop', 2], ['payoff', 4]]) {
      const link = originalLinks.find(item => item.kind === kind);
      assert.equal(link.chapter_id, ids[index]);
      assert.equal(link.source_quote, story[index].quote);
      assert.equal(link.evidence_status, 'valid');
      await page.locator('[data-view="plot"]').click();
      await page.locator(`[data-plot-move="${plotId}"]`).click();
      await page.locator(`[data-plot-source="${link.id}"]`).click();
      assert.equal(await page.locator('#chapterBody').evaluate(element =>
        element.value.slice(element.selectionStart, element.selectionEnd)), story[index].quote);
    }
    await page.reload();
    await page.waitForFunction(value => backendOnline && requestedProject === value, project);
    assert.deepEqual((await sourceBodies()).map(([, body]) => body), story.map(chapter => chapter.body));
    await reviewAll(ids);
    const manifest = await readApi('/api/export/manifest');
    assert.equal(manifest.finalization.allowed, true);
    assert.equal(manifest.chapter_count, 5);
    const dialog = await finalDialog();
    assert.match(await dialog.innerText(), /已通过基础终稿检查/);
    assert.equal(await dialog.locator('[data-export]').isEnabled(), true);
    await dialog.locator('[data-close]').click();
    await page.screenshot({path: path.join(temporary, 'desktop-five-chapter-author.png'), fullPage: true});
  });

  await test('删除回收原句后真实终稿和导出拒绝，其他四章逐字保留', async () => {
    await openChapter(ids[4]);
    await page.locator('#chapterBody').fill(story[4].body.replace(story[4].quote, ''));
    await waitSaved();
    const link = (await readApi('/api/story/foreshadow-links')).foreshadowing
      .find(item => item.id === originalLinks.find(row => row.kind === 'payoff').id);
    assert.equal(link.stale, true);
    const manifest = await readApi('/api/export/manifest');
    assert.equal(manifest.finalization.allowed, false);
    assert.ok(manifest.finalization.issues.some(issue => issue.includes('回收原句需要重新确认')));
    const downloads = [];
    page.on('download', download => downloads.push(download.suggestedFilename()));
    await page.locator('#markdownExportBtn').click();
    await page.locator('.modal h3').filter({hasText: '终稿检查'}).waitFor();
    const refused = await fetch(base + '/api/export/markdown', {headers: {'X-Moyu-Project': project}});
    assert.equal(refused.status, 409);
    assert.equal(downloads.length, 0);
    const dialog = page.locator('.modal').filter({has: page.locator('h3', {hasText: '终稿检查'})});
    assert.match(await dialog.innerText(), /回收原句需要重新确认/);
    assert.equal(await dialog.locator('[data-export]').isEnabled(), false);
    assert.equal(await dialog.locator('[data-complete]').isEnabled(), false);
    await page.screenshot({path: path.join(temporary, 'desktop-stale-payoff-blocked.png'), fullPage: true});
    await dialog.locator('[data-close]').click();
    assert.deepEqual((await sourceBodies()).slice(0, 4).map(([, body]) => body), story.slice(0, 4).map(chapter => chapter.body));
    results.deleted_payoff_link = {id: link.id, stale: link.stale, evidence_status: link.evidence_status};
  });

  await test('恢复原句仍需作者重新确认原链接，不能靠恢复文字自动放行', async () => {
    await page.locator('#chapterBody').fill(story[4].body);
    await waitSaved();
    await reviewAll(ids);
    const payoff = originalLinks.find(item => item.kind === 'payoff');
    const before = await readApi('/api/export/manifest');
    assert.equal(before.finalization.allowed, false);
    assert.ok(before.finalization.issues.some(issue => issue.includes('回收原句需要重新确认')));
    await lifecycle(plotId, ids[4], 'payoff', story[4].quote, payoff.id);
    const current = (await readApi('/api/story/foreshadow-links')).foreshadowing.filter(item => item.plot_id === plotId);
    assert.equal(current.length, 3, 'author re-confirmation must preserve the original link rather than add a second payoff');
    const restored = current.find(item => item.id === payoff.id);
    assert.equal(restored.stale, false);
    assert.equal(restored.evidence_status, 'valid');
    assert.equal(restored.source_quote, story[4].quote);
    await reviewAll(ids);
    const manifest = await readApi('/api/export/manifest');
    assert.equal(manifest.finalization.allowed, true);
    assert.deepEqual((await sourceBodies()).map(([, body]) => body), story.map(chapter => chapter.body));
    results.reconfirmed_payoff_link = {
      id: restored.id, same_id: restored.id === payoff.id,
      source_span_id: restored.source_span_id, source_quote: restored.source_quote,
    };
  });

  await test('1280电脑窗口保留五章原稿与终稿入口，编辑区没有横向溢出', async () => {
    await page.setViewportSize({width: 1280, height: 900});
    await openChapter(ids[4]);
    assert.equal(await page.locator('#chapterBody').inputValue(), story[4].body);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
    for (const selector of ['#chapterBody', '#finalCheckBtn', '#markdownExportBtn', '#epubExportBtn']) {
      const bounds = await page.locator(selector).boundingBox();
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 1281, `${selector} extends beyond the compact desktop`);
    }
    const dialog = await finalDialog();
    assert.equal(await dialog.locator('[data-complete]').isEnabled(), true);
    await dialog.locator('[data-close]').click();
    await page.screenshot({path: path.join(temporary, 'compact-desktop-five-chapter-author.png'), fullPage: true});
    assert.deepEqual((await sourceBodies()).map(([, body]) => body), story.map(chapter => chapter.body));
    results.compact_viewport = {width: 1280, height: 900};
    await page.setViewportSize(results.viewport);
  });

  await test('终稿通过后完稿状态持久保存，完稿期间不能再归档创建章节', async () => {
    const dialog = await finalDialog();
    assert.equal(await dialog.locator('[data-complete]').isEnabled(), true);
    await dialog.locator('[data-complete]').click();
    await page.waitForFunction(() => !document.querySelector('[data-complete]'));
    await waitSaved();
    assert.equal((await readApi('/api/state')).project.book_status, 'complete');
    await openChapter(ids[4]);
    assert.equal(await page.locator('[data-action="newChapter"]').count(), 0);
    const finish = page.locator('[data-action="finishChapter"]');
    if (await finish.count() && await finish.isEnabled()) {
      await finish.click();
      await waitSaved();
    }
    const complete = await readApi('/api/state');
    assert.equal(complete.chapters.length, 6);
    assert.deepEqual(complete.chapters.filter(chapter => chapter.body.trim()).map(chapter => chapter.body), story.map(chapter => chapter.body));
    await page.reload();
    await page.waitForFunction(value => backendOnline && requestedProject === value &&
      state.project.book_status === 'complete', project);
    const reloaded = await finalDialog();
    assert.equal(await reloaded.locator('[data-reopen]').count(), 1);
    assert.equal(await reloaded.locator('[data-export]').isEnabled(), true);
    await page.screenshot({path: path.join(temporary, 'desktop-final-complete.png'), fullPage: true});
    await reloaded.locator('[data-close]').click();
  });

  await test('真实下载Markdown和EPUB均含五章原稿，空白交接章不进入出版文件', async () => {
    const markdownWaiting = page.waitForEvent('download');
    await page.locator('#markdownExportBtn').click();
    const markdown = await markdownWaiting;
    const markdownPath = path.join(temporary, 'five-chapter-final.md');
    await markdown.saveAs(markdownPath);
    const text = fs.readFileSync(markdownPath, 'utf8');
    const matches = [...text.matchAll(/<!-- moyu-manuscript-v1 ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) -->/g)];
    assert.equal(matches.length, 1);
    const match = matches[0];
    const raw = Buffer.from(match[2], 'base64');
    assert.equal(hash(raw), match[1]);
    const manuscript = JSON.parse(raw);
    assert.equal(manuscript.format, 'moyu-manuscript-v1');
    assert.equal(manuscript.version, 1);
    assert.equal(manuscript.project.title, '铜匙与白色水线');
    assert.deepEqual(manuscript.chapters, story.map(({title, body}) => ({title, body})));
    const epubWaiting = page.waitForEvent('download');
    await page.locator('#epubExportBtn').click();
    const epub = await epubWaiting;
    const epubPath = path.join(temporary, 'five-chapter-final.epub');
    await epub.saveAs(epubPath);
    assert.ok(fs.statSync(epubPath).size > 1000);
    const epubSource = JSON.parse(execFileSync(process.env.MOYU_TEST_PYTHON || 'python3', [
      '-c',
      'import base64,hashlib,json,sys,zipfile\n' +
      'import xml.etree.ElementTree as ET\n' +
      'expected=json.load(sys.stdin)\n' +
      'with zipfile.ZipFile(sys.argv[1]) as archive:\n' +
      ' assert archive.read("mimetype")==b"application/epub+zip"\n' +
      ' assert len(archive.namelist())==len(set(archive.namelist()))\n' +
      ' source=archive.read("META-INF/moyu-manuscript.json")\n' +
      ' manifest=json.loads(archive.read("META-INF/moyu-export-manifest.json"))\n' +
      ' manuscript=json.loads(source)\n' +
      ' digest=hashlib.sha256(source).hexdigest()\n' +
      ' assert manifest["manuscript_source"]["path"]=="META-INF/moyu-manuscript.json"\n' +
      ' assert manifest["manuscript_source"]["sha256"]==digest\n' +
      ' assert source==base64.b64decode(expected["markdown_source_base64"],validate=True)\n' +
      ' assert len(manifest["chapters"])==5\n' +
      ' ns={"x":"http://www.w3.org/1999/xhtml","o":"http://www.idpf.org/2007/opf"}\n' +
      ' package=ET.fromstring(archive.read("OEBPS/package.opf"))\n' +
      ' assert [e.attrib["idref"] for e in package.findall("o:spine/o:itemref",ns)]==[f"chapter-{i}" for i in range(1,6)]\n' +
      ' for i,(chapter,entry) in enumerate(zip(manuscript["chapters"],manifest["chapters"],strict=True),1):\n' +
      '  assert entry["title"]==chapter["title"]\n' +
      '  assert entry["body_sha256"]==hashlib.sha256(chapter["body"].encode("utf-8")).hexdigest()\n' +
      '  assert entry["body_codepoints"]==len(chapter["body"])\n' +
      '  doc=ET.fromstring(archive.read(f"OEBPS/chapter-{i}.xhtml"))\n' +
      '  assert doc.find("x:body/x:h1",ns).text==chapter["title"]\n' +
      '  assert doc.find("x:body/x:pre",ns).text==chapter["body"].replace("\\r\\n","\\n").replace("\\r","\\n")\n' +
      ' print(json.dumps({"manuscript":manuscript,"source_sha256":digest,"manifest":manifest},ensure_ascii=False))\n',
      epubPath,
    ], {encoding: 'utf8', input: JSON.stringify({markdown_source_base64: raw.toString('base64')})}));
    assert.equal(epubSource.manuscript.format, 'moyu-manuscript-v1');
    assert.equal(epubSource.manuscript.version, 1);
    assert.equal(epubSource.manuscript.project.title, '铜匙与白色水线');
    assert.deepEqual(epubSource.manuscript.chapters, story.map(({title, body}) => ({title, body})));
    assert.equal(epubSource.source_sha256, match[1]);
    assert.equal(epubSource.manifest.chapter_count, 5);
    assert.deepEqual(epubSource.manifest.chapters.map(chapter => chapter.body_sha256), story.map(chapter => hash(chapter.body)));
    const state = await readApi('/api/state');
    assert.deepEqual(state.chapters.filter(chapter => chapter.body.trim()).map(chapter => chapter.body), story.map(chapter => chapter.body));
    const defaultResponse = await fetch(base + '/api/state', {headers: {'X-Moyu-Project': 'default-project'}});
    assert.equal(defaultResponse.ok, true);
    assert.deepEqual((await defaultResponse.json()).chapters.map(chapter => [chapter.id, hash(chapter.body)]), results.default_project_before);
    results.exports = [
      {format: 'markdown', file: 'five-chapter-final.md', sha256: hash(fs.readFileSync(markdownPath)), chapters: 5, exact_source_checked: true},
      {format: 'epub', file: 'five-chapter-final.epub', sha256: hash(fs.readFileSync(epubPath)),
        source_sha256: epubSource.source_sha256, chapters: 5, exact_source_checked: true},
    ];
  });
  assert.deepEqual(browserErrors, []);
  assert.deepEqual(externalRequests, []);
  assert.equal(providerError, '');
  results.author_write_requests = writes;
  results.source_files_sha256_after = fingerprints();
  results.source_unchanged = JSON.stringify(results.source_files_sha256_before) === JSON.stringify(results.source_files_sha256_after);
  results.passed = results.source_unchanged;
  if (!results.passed) throw new Error('Product source changed during the author-flow verification');
}

main().catch(async error => {
  results.failure = {message: error.message, stack: error.stack, server_log: serverLog.slice(-5000), provider_error: providerError};
  if (page && !page.isClosed()) {
    try {
      await page.screenshot({path: path.join(temporary, 'desktop-author-flow-failure.png'), fullPage: true});
      results.failure.ui = await page.evaluate(() => ({
        view, activeChapter, project: requestedProject, toast: document.querySelector('#toast')?.innerText,
        modal: document.querySelector('.modal')?.innerText, save: document.querySelector('#saveState')?.innerText,
        jobs: queueJobs.map(job => ({id: job.id, task: job.task, status: job.status, error: job.error})),
      }));
    } catch {}
  }
  process.exitCode = 1;
}).finally(async () => {
  results.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(temporary, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  if (browser) await browser.close();
  if (app && app.exitCode === null) {
    const stopped = new Promise(resolve => app.once('exit', resolve));
    app.kill('SIGTERM');
    await Promise.race([stopped, delay(2000)]);
    if (app.exitCode === null) app.kill('SIGKILL');
    await stopped;
  }
  if (provider) await new Promise(resolve => {provider.close(resolve); provider.closeAllConnections();});
  console.log(JSON.stringify(results, null, 2));
});
