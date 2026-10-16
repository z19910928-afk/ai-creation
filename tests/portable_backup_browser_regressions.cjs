'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-portable-browser-'));
const python = process.env.MOYU_TEST_PYTHON || 'python3';
const reportPath = path.resolve(process.env.MOYU_PORTABLE_BROWSER_REPORT ||
  path.join(temporary, 'results.json'));
const screenshotDirectory = path.resolve(process.env.MOYU_PORTABLE_BROWSER_SCREENSHOT_DIR ||
  path.dirname(reportPath));
if (fs.existsSync(reportPath)) throw new Error('Evidence exists; choose a new report path');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const pendingKey = 'mo-yu-novel-v1:portable-restore-pending';
const capturedTableCount = 60;
const chatTableRows = {
  app_agent_chat_session: 2,
  app_agent_chat_turn: 5,
  app_agent_chat_action: 4,
};
const sourceFiles = [
  'app.js', 'author-workspace.js', 'styles.css', 'index.html', 'server.py',
  'moyu_portable.py', 'moyu_backups.py', 'moyu_workspace.py', 'moyu_import.py',
  'moyu_planning.py', 'moyu_revision.py', 'moyu_records.py',
  'moyu_agent_checkpoint.py', 'moyu_agent_service.py', 'moyu_writing_agent.py', 'requirements.txt',
  'tests/portable_backup_fixtures.py', 'tests/portable_backup_browser_regressions.cjs',
];
const fingerprints = () => Object.fromEntries(sourceFiles.map(file =>
  [file, hash(fs.readFileSync(path.join(root, file)))]));
const results = {
  passed: false, started_at: new Date().toISOString(), synthetic_only: true,
  scope: '电脑真实Chrome、临时源与全新目标SQLite、真实ZIP下载和文件选择；跨服务恢复、编辑保存及失败恢复',
  claim_limit: '仅验证电脑恢复交互与数据保护，不证明文学质量或第一梯队地位',
  cases: [], screenshots: [], page_errors: [], external_browser_requests: [],
  provider_calls: 0, worker_calls: 0, outbound_calls: 0, paid_provider_calls: 0,
  source_files_sha256_before: fingerprints(),
};
let browser, sourceService, targetService, base, archivePath, archiveRaw;
let sourceState, sourcePayload, sourceFileHash, currentPage, currentCase = '';
const services = [], openContexts = [], requestLog = [];

// Test control travels over a private pipe. The HTTP server and browser use
// the real product routes; no test endpoint is exposed to a page.
const serverScript = String.raw`
import base64, hashlib, json, os, sqlite3, sys, threading, time, urllib.request
from pathlib import Path
import server
import moyu_backups as backups
import moyu_portable as portable
import moyu_workspace as workspace
sys.path.insert(0, str(Path.cwd() / "tests"))
from portable_backup_fixtures import seed_source, snapshot, SOURCE_PROJECT, SOURCE_BRANCH, SOURCE_ACTOR
role = sys.argv[1]
calls = {"provider": 0, "worker": 0, "outbound": 0}
def forbidden(kind):
    def invoke(*args, **kwargs):
        calls[kind] += 1
        raise AssertionError("portable browser fixture forbids " + kind)
    return invoke
server.call_ai_provider = forbidden("provider")
server.start_ai_worker = forbidden("worker")
urllib.request.urlopen = forbidden("outbound")
server.SEARCH_INDEX_MODE = "none"
server.init_db()
if role == "source":
    with server.connect() as conn:
        seed_source(conn)
else:
    with server.connect() as conn:
        scopes = [workspace.resolve_scope(conn, item["id"], item["branch_id"])
                  for item in workspace.project_rows(conn) if item["branch_id"]]
    for scope in scopes:
        with workspace.use_scope(*scope):
            for job_id in server.recover_ai_jobs():
                server.start_ai_worker(job_id)
def wire(value):
    if isinstance(value, bytes):
        return {"$blob": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(item) for item in value]
    return value
def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)
class Handler(server.Handler):
    def log_message(self, *args):
        pass
http = server.ThreadingHTTPServer(("127.0.0.1", server.PORT), Handler)
thread = threading.Thread(target=http.serve_forever, daemon=True)
thread.start()
emit({"ready": True, "base": "http://127.0.0.1:" + str(http.server_port),
      "role": role, "database_initially_absent": os.environ["MOYU_FIXTURE_FRESH"] == "1"})
for raw in sys.stdin:
    command = json.loads(raw)
    try:
        action = command["action"]
        if action == "stop":
            http.shutdown()
            thread.join(5)
            http.server_close()
            emit({"command_id": command["command_id"], "calls": calls,
                  "thread_stopped": not thread.is_alive()})
            break
        if action == "expire":
            with portable._LOCK:
                entry = portable._PREVIEWS.get(command["preview_id"])
                if entry is None:
                    raise AssertionError("preview to expire is absent")
                entry["created"] = time.monotonic() - portable.PREVIEW_SECONDS - 1
            value = {"expired": True}
        elif action == "status":
            with portable._LOCK:
                value = {"pending": len(portable._PREVIEWS),
                         "tokens": list(portable._PREVIEWS), "calls": dict(calls)}
            with server.connect() as conn:
                value["projects"] = [dict(row) for row in conn.execute(
                    "SELECT id,name FROM project ORDER BY rowid")]
                value["receipt_count"] = conn.execute(
                    "SELECT COUNT(*) FROM event_log WHERE event_type='project.portable_restored'").fetchone()[0]
        elif action == "freeze":
            with server.connect() as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                data = wire(snapshot(conn))
                counts = {name: len(rows) for name, rows in data["tables"].items()}
                encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                value = {"sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(), "counts": counts}
        elif action == "capture":
            with server.connect() as conn:
                value = backups.capture(conn, command["project_id"])
        elif action == "state":
            with server.connect() as conn:
                scope = workspace.resolve_scope(conn, command["project_id"], command.get("branch_id"))
                with workspace.use_scope(*scope):
                    value = server.state_from_db(conn)
        else:
            raise AssertionError("unknown private test command")
        emit({"command_id": command["command_id"], "result": wire(value)})
    except Exception as error:
        emit({"command_id": command["command_id"], "error": type(error).__name__ + ": " + str(error)})
`;

async function freePort() {
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return port;
}

function safe(value) {
  return String(value).split(root).join('<workspace>').split(temporary).join('<isolated>')
    .replace(/\/(?:Users|private\/tmp|tmp)\/[^\s"']+/g, '<private-path>');
}

async function launchService(role, directory, requestedPort) {
  const database = path.join(directory, 'book.sqlite3');
  const fresh = !fs.existsSync(database);
  fs.mkdirSync(directory, {recursive: true});
  const appPort = requestedPort || await freePort();
  const child = spawn(python, ['-u', '-c', serverScript, role], {
    cwd: root,
    env: {...process.env, MOYU_DB: database, MOYU_HOST: '127.0.0.1', MOYU_PORT: String(appPort),
      MOYU_PROJECT_ID: role === 'source' ? 'portable-source-book' : 'default-project',
      MOYU_BRANCH_ID: role === 'source' ? 'portable-main' : 'main',
      MOYU_ACTOR_ID: role === 'source' ? 'portable-author' : 'local-user',
      MOYU_AI_API_KEY: '', MOYU_AI_BASE_URL: '', MOYU_AI_MODEL: '', MOYU_SECRET_KEY: '',
      MOYU_CREDENTIALS_DIR: path.join(directory, 'synthetic-credentials'),
      MOYU_FIXTURE_FRESH: fresh ? '1' : '0'},
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const service = {role, child, database, directory, port: appPort, log: '', sequence: 0,
    waiting: new Map(), stopped: false};
  services.push(service);
  const ready = new Promise((resolve, reject) => {
    service.rejectReady = reject;
    service.resolveReady = resolve;
  });
  child.stderr.on('data', value => {service.log += value.toString();});
  readline.createInterface({input: child.stdout}).on('line', line => {
    let value;
    try {value = JSON.parse(line);} catch {service.log += line + '\n'; return;}
    if (value.ready) {
      service.base = value.base;
      service.initiallyAbsent = value.database_initially_absent;
      service.resolveReady(service);
    } else if (value.command_id) {
      const pending = service.waiting.get(value.command_id);
      if (!pending) return;
      clearTimeout(pending.timer);
      service.waiting.delete(value.command_id);
      if (value.error) pending.reject(new Error(value.error)); else pending.resolve(value.result || value);
    }
  });
  child.once('error', error => service.rejectReady(error));
  child.once('exit', code => {
    service.rejectReady(new Error(`isolated ${role} service exited ${code}: ${safe(service.log)}`));
    for (const pending of service.waiting.values()) {
      clearTimeout(pending.timer); pending.reject(new Error(`isolated ${role} service exited`));
    }
    service.waiting.clear();
  });
  let readyTimer;
  try {
    await Promise.race([ready, new Promise((resolve, reject) => {
      readyTimer = setTimeout(() => reject(new Error(
        `isolated ${role} service startup timeout: ${safe(service.log)}`)), 20000);
    })]);
  } finally {
    clearTimeout(readyTimer);
  }
  return service;
}

async function control(service, action, extra = {}) {
  const commandId = String(++service.sequence);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      service.waiting.delete(commandId);
      reject(new Error(`private ${action} timed out`));
    }, 15000);
    service.waiting.set(commandId, {resolve, reject, timer});
    service.child.stdin.write(JSON.stringify({command_id: commandId, action, ...extra}) + '\n');
  });
}

async function stopService(service) {
  if (!service || service.stopped) return;
  if (service.child.exitCode !== null) throw new Error('isolated service exited unexpectedly');
  const stopped = await control(service, 'stop');
  assert.equal(stopped.thread_stopped, true);
  service.stopped = true;
  service.calls = stopped.calls;
  if (service.child.exitCode === null) {
    let exitTimer;
    try {
      await Promise.race([
        new Promise(resolve => service.child.once('exit', resolve)),
        new Promise((resolve, reject) => {
          exitTimer = setTimeout(() => {
            service.child.kill('SIGKILL'); reject(new Error('isolated service did not exit'));
          }, 5000);
        }),
      ]);
    } finally {
      clearTimeout(exitTimer);
    }
  }
}

async function api(route, method = 'GET', body, project = 'default-project', branch,
  serviceBase = base) {
  const response = await fetch(serviceBase + route, {
    method,
    headers: {'Content-Type': 'application/json', 'X-Moyu-Project': project,
      ...(branch ? {'X-Moyu-Branch': branch} : {})},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json().catch(() => ({}));
  assert.equal(response.ok, true, `${route}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}

async function waitFor(read, label) {
  const end = Date.now() + 12000;
  while (Date.now() < end) {
    if (await read()) return;
    await pause(30);
  }
  throw new Error(label + ' was not observed');
}

async function waitSaved(page) {
  await page.waitForFunction(() => backendOnline && confirmedState && !isDirty() &&
    !syncInFlight && !independentWrite && !adopting && !saveError && !mergePending,
  null, {timeout: 15000});
}

async function openPage(viewport = {width: 1440, height: 1000}, project = 'default-project') {
  const context = await browser.newContext({viewport, acceptDownloads: true});
  openContexts.push(context);
  if (sourceService?.base === base && project === 'portable-source-book') {
    await context.addInitScript(({projectId, liveState}) => {
      localStorage.setItem('mo-yu-novel-v1:current-project', projectId);
      localStorage.setItem('mo-yu-novel-v1:' + projectId, JSON.stringify(liveState));
    }, {projectId: project, liveState: sourceState});
  }
  const page = await context.newPage();
  currentPage = page;
  const errors = [];
  page.on('pageerror', error => {
    const item = {case: currentCase, message: safe(error.message)};
    errors.push(item); results.page_errors.push(item);
  });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['127.0.0.1', 'localhost'].includes(url.hostname)) return route.continue();
    results.external_browser_requests.push({case: currentCase, origin: url.origin});
    return route.abort('blockedbyclient');
  });
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (!pathname.startsWith('/api/backups/import/')) return;
    const input = request.postDataJSON() || {};
    const body = {...input};
    if (typeof body.data_base64 === 'string') {
      body.data_base64_bytes = body.data_base64.length;
      body.data_base64_sha256 = hash(body.data_base64);
      delete body.data_base64;
    }
    requestLog.push({case: currentCase, method: request.method(), pathname, body});
  });
  await page.goto(base);
  await page.waitForFunction(() => backendOnline, null, {timeout: 15000});
  if (project !== await page.evaluate(() => requestedProject)) await switchProject(page, project);
  await waitSaved(page);
  await backupsPage(page);
  return {context, page, errors};
}

async function backupsPage(page) {
  await page.locator('[data-view="backups"]').click();
  await page.locator('[data-action="importBackup"]').waitFor();
  await page.waitForFunction(() => view === 'backups' &&
    document.querySelector('#backupList')?.textContent !== '正在读取备份…');
  await waitSaved(page);
}

async function switchProject(page, project) {
  await page.locator('[data-view="library"]').click();
  await page.locator(`[data-project-open="${project}"]`).click();
  await page.waitForFunction(id => backendOnline && state.project.id === id && requestedProject === id,
    project, {timeout: 15000});
  await waitSaved(page);
}

async function choose(page, filename = '合成完整作品.zip', file = archivePath) {
  await page.locator('#backupFileInput').setInputFiles({
    name: filename, mimeType: 'application/zip',
    buffer: Buffer.isBuffer(file) ? file : fs.readFileSync(file),
  });
}

async function preview(page, filename, file) {
  await choose(page, filename, file);
  await page.locator('[data-portable-preview] #portableRestoreTitle').waitFor();
  return page.evaluate(() => authorPortableDialog.preview);
}

async function closePreview(page) {
  if (await page.locator('[data-portable-preview]').count()) {
    await page.locator('[data-portable-preview] [data-close]').click();
  }
  await page.waitForFunction(() => !document.querySelector('[data-portable-preview]'));
  await waitFor(async () => (await control(targetService, 'status')).pending === 0,
    'canceled preview release');
}

async function submit(page, title) {
  if (title !== undefined) await page.locator('#portableRestoreTitle').fill(title);
  if (!await page.locator('#portableRestoreConfirm').isDisabled()) {
    await page.locator('#portableRestoreConfirm').check();
  }
  await page.locator('[data-portable-preview] [type="submit"]').click();
}

async function opened(page, expectedId) {
  await page.waitForFunction(({id, key}) => !document.querySelector('[data-portable-preview]') &&
    backendOnline && state.project.id === requestedProject &&
    localStorage.getItem(key) === null &&
    (id ? state.project.id === id : state.project.id !== 'default-project'),
  {id: expectedId || null, key: pendingKey}, {timeout: 15000});
  await waitSaved(page);
  return page.evaluate(() => state.project.id);
}

async function pending(page) {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key) || 'null'), pendingKey);
}

async function projectCache(page, project) {
  return page.evaluate(id => Object.fromEntries(['', ':base', ':dirty'].map(suffix =>
    [suffix || 'state', localStorage.getItem('mo-yu-novel-v1:' + id + suffix)])), project);
}

function assertPendingIdentity(value) {
  assert.ok(value?.request?.preview_id);
  assert.deepEqual(Object.keys(value.request).sort(),
    ['preview_id', 'archive_sha256', 'title', 'confirm'].sort());
  assert.equal(value.request.confirm, true);
  assert.ok(value.scope?.project_id && value.scope.branch_id);
  const serialized = JSON.stringify(value);
  assert.equal(serialized.includes('data_base64'), false);
  assert.equal(serialized.includes(sourceState.chapters[0].body), false);
  assert.equal(serialized.includes('"tables"'), false);
}

function postsSince(index, pathname = '/api/backups/import/apply') {
  return requestLog.slice(index).filter(item => item.pathname === pathname && item.method === 'POST');
}

async function assertUnchanged(before) {
  assert.deepEqual(await control(targetService, 'freeze'), before);
}

async function screenshot(page, name) {
  fs.mkdirSync(screenshotDirectory, {recursive: true});
  const destination = path.join(screenshotDirectory, name);
  assert.equal(fs.existsSync(destination), false, 'never overwrite screenshot evidence');
  await page.screenshot({path: destination, fullPage: false});
  results.screenshots.push({file: destination.startsWith(root + path.sep) ?
    path.relative(root, destination) : path.basename(destination), sha256: hash(fs.readFileSync(destination))});
}

async function visibleControls(page, viewport, label) {
  const selectors = ['#portableRestoreTitle', '#portableRestoreConfirm',
    '[data-portable-preview] [data-close]', '[data-portable-preview] [data-reselect]',
    '[data-portable-preview] [type="submit"]'];
  const controls = [];
  for (const selector of selectors) {
    const locator = page.locator(selector);
    if (!await locator.isVisible()) continue;
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return {width: rect.width, height: rect.height, x: rect.x, y: rect.y,
        within: rect.x >= 0 && rect.right <= innerWidth && rect.y >= 0 && rect.bottom <= innerHeight,
        unobstructed: hit === element || element.contains(hit)};
    });
    assert.ok(box.width > 0 && box.height > 0 && box.within && box.unobstructed,
      JSON.stringify({selector, box}));
    controls.push({selector, ...box});
  }
  const layout = await page.locator('[data-portable-preview] .portable-restore-modal').evaluate(element => {
    const rect = element.getBoundingClientRect();
    return {width: rect.width, height: rect.height,
      horizontal_overflow: element.scrollWidth > element.clientWidth + 1,
      within: rect.x >= 0 && rect.right <= innerWidth && rect.y >= 0 && rect.bottom <= innerHeight};
  });
  assert.equal(layout.within, true);
  assert.equal(layout.horizontal_overflow, false);
  results.desktop_layouts ||= [];
  results.desktop_layouts.push({viewport, label, controls, modal: layout});
}

async function editorLayout(page, viewport, expectedDirectory) {
  assert.ok(expectedDirectory.volumes.some(volume => /[A-Za-z0-9_]{25,}/.test(volume.title)),
    'the real restored blueprint must contain the synthetic long volume title');
  await page.waitForFunction(expected => {
    const sidebar = document.querySelector('.chapter-sidebar.workbench-library');
    const headings = [...document.querySelectorAll('#authorChapterTree h3')]
      .map(node => node.textContent);
    return backendOnline && view === 'editor' && requestedProject === expected.project_id &&
      state.project.id === expected.project_id && (state.branch_id || 'main') === expected.branch_id &&
      sidebar?.dataset.projectId === expected.project_id &&
      sidebar?.dataset.branchId === expected.branch_id && !authorEditorBlueprintRequest &&
      authorBlueprintVersion === expected.version && expected.volumes.every(volume =>
        authorBlueprint?.volumes?.some(item => item.id === volume.id && item.title === volume.title) &&
        headings.includes(volume.title));
  }, expectedDirectory, {timeout: 15000});
  const layout = await page.locator('.editor-layout').evaluate(element => {
    const sidebar = element.querySelector('.chapter-sidebar.workbench-library');
    const editor = element.querySelector('.editor-card');
    const chapters = sidebar.querySelector('#workbenchChapters');
    const knowledge = sidebar.querySelector('#workbenchKnowledge');
    const visible = node => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 &&
        style.display !== 'none' && style.visibility !== 'hidden';
    };
    const rectangle = node => {
      const rect = node.getBoundingClientRect();
      return {x: rect.x, width: rect.width, height: rect.height, right: rect.right,
        horizontal_overflow: node.scrollWidth > node.clientWidth + 1};
    };
    const directoryNodes = [...chapters.querySelectorAll('.chapter-group,h3,.chapter-select')]
      .filter(visible);
    return {
      sidebar: rectangle(sidebar), editor: rectangle(editor),
      sidebar_track: Number.parseFloat(getComputedStyle(element).gridTemplateColumns.split(' ')[0]),
      sidebar_controls: [
        '.workbench-library-head', '.workbench-library-tabs', '#workbenchChaptersTab',
        '#workbenchKnowledgeTab', '#workbenchFilter', '#workbenchChapters', '#authorChapterTree',
      ].map(selector => {
        const node = sidebar.querySelector(selector);
        return {selector, visible: !!node && visible(node), ...(node ? rectangle(node) : {})};
      }),
      chapters_selected: sidebar.querySelector('#workbenchChaptersTab')
        .getAttribute('aria-selected') === 'true',
      knowledge_hidden: knowledge.hidden && !visible(knowledge),
      visible_directory_groups: directoryNodes.filter(node => node.matches('.chapter-group')).length,
      visible_directory_headings: directoryNodes.filter(node => node.matches('h3')).length,
      visible_chapter_buttons: directoryNodes.filter(node => node.matches('.chapter-select')).length,
      sidebar_children: directoryNodes.map(node => ({tag: node.tagName, ...rectangle(node)})),
      editor_children: [...editor.querySelectorAll('.editor-toolbar,#chapterTitle,#chapterBody')]
        .map(node => ({tag: node.tagName, visible: visible(node), ...rectangle(node)})),
      long_ascii_heading: directoryNodes.filter(node => node.matches('h3'))
        .some(node => /[A-Za-z0-9_]{25,}/.test(node.textContent)),
      document_horizontal_overflow: document.documentElement.scrollWidth > innerWidth + 1,
    };
  });
  assert.equal(layout.chapters_selected, true, JSON.stringify(layout));
  assert.equal(layout.knowledge_hidden, true, JSON.stringify(layout));
  assert.ok(layout.visible_directory_groups > 0, JSON.stringify(layout));
  assert.ok(layout.visible_directory_headings > 0, JSON.stringify(layout));
  assert.ok(layout.visible_chapter_buttons > 0, JSON.stringify(layout));
  assert.equal(layout.long_ascii_heading, true, 'retain the synthetic long volume title');
  assert.equal(layout.document_horizontal_overflow, false, JSON.stringify(layout));
  assert.equal(layout.sidebar.horizontal_overflow, false, JSON.stringify(layout));
  assert.ok(layout.sidebar.width > 0 && layout.sidebar.height > 0, JSON.stringify(layout));
  assert.ok(layout.editor.width > 0 && layout.editor.height > 0, JSON.stringify(layout));
  assert.ok(layout.sidebar.width <= layout.sidebar_track + 1, JSON.stringify(layout));
  assert.ok(layout.sidebar.right <= layout.editor.x, JSON.stringify(layout));
  for (const child of [...layout.sidebar_controls, ...layout.sidebar_children]) {
    if ('visible' in child) assert.equal(child.visible, true, JSON.stringify(child));
    assert.ok(child.x >= layout.sidebar.x - 1 && child.right <= layout.sidebar.right + 1,
      JSON.stringify({child, sidebar: layout.sidebar}));
    assert.equal(child.horizontal_overflow, false, JSON.stringify(child));
  }
  assert.equal(layout.editor_children.length, 3, JSON.stringify(layout));
  for (const child of layout.editor_children) {
    assert.equal(child.visible, true, JSON.stringify(child));
    assert.ok(child.x >= layout.editor.x - 1 && child.right <= layout.editor.right + 1,
      JSON.stringify({child, editor: layout.editor}));
  }
  results.desktop_editor_layouts ||= [];
  results.desktop_editor_layouts.push({viewport, expected_directory: expectedDirectory, ...layout});
}

async function test(name, action) {
  currentCase = name;
  console.log(JSON.stringify({test: name, state: 'running'}));
  const started = Date.now();
  try {
    await action();
    results.cases.push({name, passed: true, elapsed_ms: Date.now() - started});
    console.log(JSON.stringify({test: name, state: 'passed'}));
  } catch (error) {
    results.cases.push({name, passed: false, elapsed_ms: Date.now() - started,
      failure: safe(error.message)});
    throw error;
  }
}

async function casePage(action, viewport) {
  const workspace = await openPage(viewport);
  try {
    await action(workspace.page);
    assert.deepEqual(workspace.errors, []);
  } catch (error) {
    results.failed_page_state = await workspace.page.evaluate(key => ({
      requestedProject, backendOnline, view, dirty: isDirty(), syncInFlight,
      independentWrite, saveError, mergePending, pending: JSON.parse(localStorage.getItem(key) || 'null'),
      save_indicator: document.querySelector('#saveState')?.textContent,
      modal: document.querySelector('[data-portable-preview]')?.innerText,
      toast: document.querySelector('#toast')?.textContent,
    }), pendingKey).catch(() => null);
    await screenshot(workspace.page, 'portable-case-failure.png').catch(() => {});
    throw error;
  } finally {
    await workspace.page.unrouteAll({behavior: 'ignoreErrors'});
    await workspace.context.close();
  }
}

async function lostApply(page, title) {
  let lost = null;
  const routePattern = '**/api/backups/import/apply';
  const handler = async route => {
    const response = await route.fetch();
    const data = await response.json();
    assert.equal(response.ok(), true, JSON.stringify(data));
    lost = {request: route.request().postDataJSON(), result: data};
    await route.abort('failed');
  };
  await page.route(routePattern, handler);
  const selected = await preview(page);
  await submit(page, title);
  await page.locator('[data-portable-preview] [type="submit"]').filter({hasText: '确认保存结果'}).waitFor();
  assert.ok(lost);
  assert.equal(lost.request.preview_id, selected.preview_id);
  const value = await pending(page);
  assertPendingIdentity(value);
  assert.deepEqual(value.request, lost.request);
  assert.equal(value.project, null);
  await page.unroute(routePattern, handler);
  return lost;
}

function assertRestoredChat(captured, restored, viewport, phase) {
  const tables = captured.tables;
  for (const [table, count] of Object.entries(chatTableRows)) {
    assert.equal(tables[table].length, count, table);
    const originalIds = new Set(sourcePayload.tables[table].map(row => row.id));
    assert.ok(tables[table].every(row =>
      row.project_id === restored.project.id && !originalIds.has(row.id)), table);
  }
  const sessions = tables.app_agent_chat_session;
  const sessionMap = new Map();
  for (const original of sourcePayload.tables.app_agent_chat_session) {
    const matches = sessions.filter(row => row.request_id === original.request_id);
    assert.equal(matches.length, 1);
    const session = matches[0];
    sessionMap.set(original.id, session.id);
    assert.equal(session.title, original.title, 'ID-like session titles are plain text');
    assert.equal(session.restored_read_only, 1);
    assert.equal(session.branch_id, restored.branch_id);
  }
  const targetChapter = restored.chapters[0].id;
  const turns = tables.app_agent_chat_turn;
  const turnMap = new Map();
  const statuses = [];
  for (const original of sourcePayload.tables.app_agent_chat_turn) {
    const matches = turns.filter(row => row.request_id === original.request_id);
    assert.equal(matches.length, 1);
    const turn = matches[0];
    turnMap.set(original.id, turn.id);
    const expectedStatus = ['queued', 'running'].includes(original.status) ? 'failed' : original.status;
    statuses.push(turn.status);
    assert.equal(turn.status, expectedStatus);
    assert.equal(turn.session_id, sessionMap.get(original.session_id));
    assert.equal(turn.branch_id, restored.branch_id);
    assert.equal(turn.chapter_id, targetChapter);
    for (const field of ['content', 'reply', 'request_hash']) {
      assert.equal(turn[field], original[field], `chat turn ${field} is preserved`);
    }
    const originalSource = JSON.parse(original.source_json);
    const turnSource = JSON.parse(turn.source_json);
    const revision = tables.document_revision.find(row => row.id === turnSource.source_revision_id);
    const originalRevision = sourcePayload.tables.document_revision.find(row =>
      row.id === originalSource.source_revision_id);
    assert.ok(revision && originalRevision, 'chat source revision exists in the restored copy');
    assert.notEqual(revision.id, originalRevision.id);
    assert.equal(revision.document_node_id, targetChapter);
    assert.equal(revision.content_hash, originalRevision.content_hash);
    const mapSource = source => ({...source, project_id: restored.project.id,
      branch_id: restored.branch_id, chapter_id: targetChapter, source_revision_id: revision.id});
    assert.deepEqual(turnSource, mapSource(originalSource));
    assert.deepEqual(JSON.parse(turn.references_json),
      JSON.parse(original.references_json).map(mapSource),
      'selection ranges, source hashes, and quoted text survive both restores');
    const context = JSON.parse(original.context_json);
    context.project.id = restored.project.id;
    context.current_chapter.id = targetChapter;
    context.current_chapter.revision_id = revision.id;
    context._agent_chat = {chat_session_id: turn.session_id, chat_turn_id: turn.id};
    assert.deepEqual(JSON.parse(turn.context_json), context);
    const job = tables.app_ai_job.find(row => row.id === turn.job_id);
    assert.ok(job, 'each chat turn keeps its own restored job');
    assert.notEqual(job.id, original.job_id);
    assert.equal(job.task, 'assistant_chat');
    assert.equal(job.status, expectedStatus);
    assert.equal(job.chapter_id, targetChapter);
    assert.deepEqual(JSON.parse(job.context_json), {...context, _restored_read_only: true});
  }
  const actions = tables.app_agent_chat_action;
  assert.deepEqual(actions.map(row => row.status).sort(),
    ['confirmed', 'dismissed', 'proposed', 'proposed']);
  const originalAction = sourcePayload.tables.app_agent_chat_action.find(row =>
    row.status === 'proposed');
  const successTurn = turns.find(row =>
    row.id === turnMap.get('portable-chat-turn-success'));
  const actionSource = JSON.parse(successTurn.source_json);
  for (const action of actions) {
    assert.equal(action.session_id, successTurn.session_id);
    assert.equal(action.turn_id, successTurn.id);
    assert.equal(action.branch_id, restored.branch_id);
    assert.equal(action.chapter_id, targetChapter);
    for (const field of ['kind', 'label', 'instruction']) {
      assert.equal(action[field], originalAction[field], `chat action ${field} is preserved`);
    }
    assert.deepEqual(JSON.parse(action.source_json), actionSource);
    assert.deepEqual(JSON.parse(action.payload_json), JSON.parse(originalAction.payload_json));
    assert.deepEqual(JSON.parse(action.result_json), action.status === 'confirmed' ? {
      writing_session_id: tables.app_agent_session[0].id, chapter_id: targetChapter,
    } : {});
  }
  results.chat_restores ||= [];
  results.chat_restores.push({viewport, phase, table_rows: {...chatTableRows},
    restored_read_only_sessions: sessions.length, turn_statuses: statuses,
    action_statuses: actions.map(row => row.status).sort(),
    original_text_and_selection_preserved: true, all_references_point_to_restored_copy: true});
}

async function main() {
  browser = await chromium.launch({
    headless: true, executablePath: process.env.MOYU_CHROME_PATH || undefined,
    args: ['--disable-background-networking', '--disable-component-update',
      '--disable-default-apps', '--no-first-run'],
  });
  await test('60表合成源服务真实下载完整ZIP后关闭，目标目录启动前为空', async () => {
    const sourceDirectory = path.join(temporary, 'source');
    sourceService = await launchService('source', sourceDirectory);
    assert.equal(sourceService.initiallyAbsent, true);
    sourcePayload = await control(sourceService, 'capture', {project_id: 'portable-source-book'});
    assert.equal(Object.keys(sourcePayload.tables).length, capturedTableCount);
    assert.ok(Object.values(sourcePayload.tables).every(rows => rows.length > 0));
    for (const [table, count] of Object.entries(chatTableRows)) {
      assert.equal(sourcePayload.tables[table].length, count, table);
    }
    assert.equal(sourcePayload.tables.app_ai_job.filter(row => row.task === 'assistant_chat').length, 5);
    assert.equal(sourcePayload.tables.app_ai_job.filter(row => row.task !== 'assistant_chat').length, 7);
    sourceState = await control(sourceService, 'state', {project_id: 'portable-source-book'});
    base = sourceService.base;
    const {context, page} = await openPage({width: 1440, height: 1000}, 'portable-source-book');
    await page.locator('[data-action="createBackup"]').click();
    await page.locator('[data-backup-download]').first().waitFor();
    const downloadEvent = page.waitForEvent('download');
    await page.locator('[data-backup-download]').first().click();
    const download = await downloadEvent;
    archivePath = path.join(temporary, 'downloaded-complete-source.zip');
    await download.saveAs(archivePath);
    archiveRaw = fs.readFileSync(archivePath);
    assert.ok(archiveRaw.length > 1000);
    assert.equal(archiveRaw.subarray(0, 2).toString(), 'PK');
    results.source_download = {bytes: archiveRaw.length, sha256: hash(archiveRaw),
      captured_tables: capturedTableCount, every_captured_table_nonempty: true,
      chat_table_rows: {...chatTableRows}, real_browser_download: true};
    await context.close();
    await stopService(sourceService);
    sourceFileHash = hash(fs.readFileSync(sourceService.database));
    await assert.rejects(fetch(sourceService.base + '/api/health'));
    const targetDirectory = path.join(temporary, 'target');
    assert.equal(fs.existsSync(targetDirectory), false);
    targetService = await launchService('target', targetDirectory);
    assert.equal(targetService.initiallyAbsent, true);
    base = targetService.base;
    results.source_service_closed_before_target = true;
  });
  for (const viewport of [{width: 1440, height: 1000}, {width: 1280, height: 900}]) {
    await test(`${viewport.width}×${viewport.height}文件选择恢复副本、编辑保存、真实再备份与再次恢复`, async () => {
      await casePage(async page => {
        const original = await api('/api/state');
        const start = requestLog.length;
        const before = await control(targetService, 'freeze');
        const chooserEvent = page.waitForEvent('filechooser');
        await page.locator('[data-action="importBackup"]').click();
        const chooser = await chooserEvent;
        await chooser.setFiles(archivePath);
        await page.locator('#portableRestoreTitle').waitFor();
        const selected = await page.evaluate(() => authorPortableDialog.preview);
        assert.equal(selected.counts.reference_documents, 2);
        assert.equal(selected.counts.jobs, 12);
        assert.equal(selected.counts.planning_workflows, 3);
        assert.equal(Object.keys(selected.row_counts).length, capturedTableCount);
        for (const [table, rows] of Object.entries(sourcePayload.tables)) {
          assert.equal(selected.row_counts[table], rows.length, table);
        }
        await assertUnchanged(before);
        await page.locator('#portableRestoreTitle').fill(`电脑恢复${viewport.width}🙂`);
        await visibleControls(page, viewport, '恢复确认');
        await screenshot(page, `portable-preview-${viewport.width}x${viewport.height}.png`);
        await submit(page);
        const restoredId = await opened(page);
        const restored = await api('/api/state', 'GET', undefined, restoredId);
        assert.equal(restored.project.title, `电脑恢复${viewport.width}🙂`);
        assert.deepEqual(restored.chapters.map(chapter => chapter.body),
          sourceState.chapters.map(chapter => chapter.body));
        assert.ok(restored.chapters.every(chapter =>
          !sourceState.chapters.some(originalChapter => originalChapter.id === chapter.id)));
        assertRestoredChat(await control(targetService, 'capture', {project_id: restoredId}),
          restored, viewport, 'first_restore');
        assert.deepEqual((await api('/api/state')).chapters, original.chapters);
        assert.equal(await pending(page), null);
        assert.equal(postsSince(start).length, 1);
        await page.locator('[data-view="editor"]').click();
        const first = restored.chapters[0];
        await page.locator(`[data-chapter="${first.id}"]`).click();
        const body = first.body.replace(/\r\n?/g, '\n') +
          `\n电脑${viewport.width}恢复后继续写作，原字节𠀀🙂仍在。`;
        await page.locator('#chapterBody').fill(body);
        await waitSaved(page);
        const restoredBranch = restored.branch_id || 'main';
        let heldBlueprint, releaseBlueprint;
        const blueprintGate = new Promise(resolve => {releaseBlueprint = resolve;});
        const blueprintPattern = '**/api/blueprint';
        const holdBlueprint = async route => {
          const headers = route.request().headers();
          if (headers['x-moyu-project'] !== restoredId ||
              headers['x-moyu-branch'] !== restoredBranch) return route.continue();
          const response = await route.fetch();
          assert.equal(response.ok(), true, 'read the real restored blueprint before releasing it');
          heldBlueprint = await response.json();
          await blueprintGate;
          await route.fulfill({response}).catch(() => {});
        };
        await page.route(blueprintPattern, holdBlueprint);
        try {
          await page.reload();
          await page.waitForFunction(id => backendOnline && requestedProject === id, restoredId);
          await page.locator('[data-view="editor"]').click();
          await page.locator(`[data-chapter="${first.id}"]`).click();
          assert.equal(await page.locator('#chapterBody').inputValue(), body);
          assert.equal((await api('/api/state', 'GET', undefined, restoredId)).chapters[0].body, body);
          await waitFor(() => heldBlueprint, 'restored blueprint response held before directory sampling');
          const beforeRelease = await page.evaluate(() => ({
            project_id: requestedProject, branch_id: state.branch_id || 'main',
            blueprint_loaded: !!authorBlueprint,
            pending_project: authorEditorBlueprintRequest?.owner,
            pending_branch: authorEditorBlueprintRequest?.branch,
            directory_headings: [...document.querySelectorAll('#authorChapterTree h3')]
              .map(node => node.textContent),
          }));
          assert.equal(beforeRelease.project_id, restoredId);
          assert.equal(beforeRelease.branch_id, restoredBranch);
          assert.equal(beforeRelease.blueprint_loaded, false);
          assert.equal(beforeRelease.pending_project, restoredId);
          assert.equal(beforeRelease.pending_branch, restoredBranch);
          assert.ok(beforeRelease.directory_headings.length > 0);
          assert.equal(beforeRelease.directory_headings.some(title => /[A-Za-z0-9_]{25,}/.test(title)),
            false, 'the old immediate sample must reproduce the missing long volume title');
          const expectedDirectory = {project_id: restoredId, branch_id: restoredBranch,
            version: heldBlueprint.version,
            volumes: heldBlueprint.blueprint.volumes
              .filter(volume => restored.chapters.some(chapter => chapter.volume_id === volume.id))
              .map(({id, title}) => ({id, title}))};
          releaseBlueprint();
          await editorLayout(page, viewport, expectedDirectory);
          results.delayed_editor_blueprints ||= [];
          results.delayed_editor_blueprints.push({viewport, before_release: beforeRelease,
            expected_directory: expectedDirectory, directory_ready_after_release: true});
        } finally {
          releaseBlueprint();
          await page.unroute(blueprintPattern, holdBlueprint);
        }
        await screenshot(page, `portable-editor-${viewport.width}x${viewport.height}.png`);
        await backupsPage(page);
        await page.locator('[data-action="createBackup"]').click();
        await page.locator('[data-backup-download]').first().waitFor();
        const downloadEvent = page.waitForEvent('download');
        await page.locator('[data-backup-download]').first().click();
        const download = await downloadEvent;
        const secondArchive = path.join(temporary, `edited-${viewport.width}.zip`);
        await download.saveAs(secondArchive);
        await preview(page, `编辑后的完整备份${viewport.width}.zip`, secondArchive);
        await submit(page, `再次恢复${viewport.width}`);
        const secondId = await opened(page);
        assert.notEqual(secondId, restoredId);
        const secondState = await api('/api/state', 'GET', undefined, secondId);
        assert.equal(secondState.chapters[0].body, body);
        assert.deepEqual((await api('/api/state')).chapters, original.chapters);
        assert.equal(postsSince(start).length, 2);
        const captured = await control(targetService, 'capture', {project_id: secondId});
        assert.equal(Object.keys(captured.tables).length, capturedTableCount);
        assert.equal(captured.tables.app_reference_document.length, 2);
        assert.equal(captured.tables.app_ai_job.filter(row => row.task === 'assistant_chat').length, 5);
        assert.equal(captured.tables.app_ai_job.filter(row => row.task !== 'assistant_chat').length, 7);
        assert.ok(captured.tables.app_ai_job.every(job =>
          !['queued', 'running'].includes(job.status) &&
          JSON.parse(job.context_json)._restored_read_only === true));
        assert.ok(captured.tables.app_planning_workflow.every(row => row.status === 'canceled'));
        const sessions = captured.tables.app_agent_session;
        assert.equal(sessions.length, 1);
        assert.ok(sessions.every(row => row.status === 'restored' && row.restored_read_only === 1));
        assert.equal(sessions[0].plan_json, sourcePayload.tables.app_agent_session[0].plan_json);
        for (const [table, field] of [['app_agent_checkpoint', 'checkpoint'], ['app_agent_write', 'value']]) {
          assert.deepEqual(captured.tables[table].map(row => row[field]),
            sourcePayload.tables[table].map(row => row[field]));
          assert.ok(captured.tables[table].every(row => row.session_id === sessions[0].id));
        }
        assertRestoredChat(captured, secondState, viewport, 'second_restore');
        results.restored_edits ||= [];
        results.restored_edits.push({viewport, first_project: restoredId, second_project: secondId,
          body_sha256: hash(body), two_real_downloads: true});
      }, viewport);
    });
  }
  await test('1280×800长文件名及200字作品名无横向溢出且确认控件可点击', async () => {
    await casePage(async page => {
      const filename = '长文件名'.repeat(45) + '🙂.zip';
      await preview(page, filename);
      await page.locator('#portableRestoreTitle').fill('书'.repeat(199) + '🙂');
      assert.equal(Array.from(await page.locator('#portableRestoreTitle').inputValue()).length, 200);
      await visibleControls(page, {width: 1280, height: 800}, '长文件名和标题');
      await screenshot(page, 'portable-long-title-1280x800.png');
      await closePreview(page);
    }, {width: 1280, height: 800});
  });
  await test('损坏ZIP只显示错误且数据库零写入，随后正常文件可预览取消', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'freeze');
      const start = requestLog.length;
      await choose(page, '损坏完整备份.zip', Buffer.from('not a ZIP archive'));
      await page.locator('[data-portable-preview] .form-error').filter({hasText: /损坏|无效|ZIP|备份/}).waitFor();
      assert.equal(await page.locator('#portableRestoreTitle').count(), 0);
      assert.equal(postsSince(start).length, 0);
      await assertUnchanged(before);
      await closePreview(page);
      await preview(page);
      await closePreview(page);
      await assertUnchanged(before);
    });
  });
  await test('取消和已知预览重新选择只释放服务器文件，不建立作品', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'freeze');
      const start = requestLog.length;
      const first = await preview(page, '首次预览.zip');
      const chooserEvent = page.waitForEvent('filechooser');
      await page.locator('[data-reselect]').click();
      const chooser = await chooserEvent;
      await chooser.setFiles(archivePath);
      await page.locator('#portableRestoreTitle').waitFor();
      const second = await page.evaluate(() => authorPortableDialog.preview);
      assert.notEqual(second.preview_id, first.preview_id);
      const records = requestLog.slice(start);
      assert.ok(records.some(row => row.pathname.endsWith('/cancel') &&
        row.body.preview_id === first.preview_id));
      assert.equal(postsSince(start).length, 0);
      await closePreview(page);
      await assertUnchanged(before);
    });
  });
  await test('迟到A预览不能替换B，A与B服务器文件最终都释放', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'freeze');
      const start = requestLog.length;
      let held, release;
      const gate = new Promise(resolve => {release = resolve;});
      const pattern = '**/api/backups/import/preview';
      const handler = async route => {
        if (route.request().postDataJSON().filename !== '迟到A.zip') return route.continue();
        const response = await route.fetch();
        held = await response.json();
        await gate;
        await route.fulfill({response}).catch(() => {});
      };
      await page.route(pattern, handler);
      await choose(page, '迟到A.zip');
      await waitFor(() => held, 'late A preview response');
      const second = await preview(page, '当前B.zip');
      release();
      await waitFor(async () => postsSince(start, '/api/backups/import/cancel')
        .some(row => row.body.preview_id === held.preview_id), 'late A cancellation');
      assert.equal(await page.evaluate(() => authorPortableDialog.preview.preview_id),
        second.preview_id);
      assert.equal(await page.locator('.portable-filename').innerText(), '当前B.zip');
      assert.equal(postsSince(start).length, 0);
      await closePreview(page);
      await page.unroute(pattern, handler);
      await assertUnchanged(before);
    });
  });
  for (const interruption of ['close', 'project', 'branch']) {
    await test(`预览响应迟到时${interruption}使窗口失效，不能应用原文件`, async () => {
      await casePage(async page => {
        let other;
        if (interruption === 'project') {
          other = (await api('/api/projects', 'POST', {title: '切换目标作品'})).project;
          await switchProject(page, other.id);
          await switchProject(page, 'default-project');
          await backupsPage(page);
        }
        const before = await control(targetService, 'freeze');
        const start = requestLog.length;
        let held, release;
        const gate = new Promise(resolve => {release = resolve;});
        const pattern = '**/api/backups/import/preview';
        const handler = async route => {
          const response = await route.fetch();
          held = await response.json();
          await gate;
          await route.fulfill({response}).catch(() => {});
        };
        await page.route(pattern, handler);
        await choose(page, `中断${interruption}.zip`);
        await waitFor(() => held, 'interrupted preview response');
        if (interruption === 'close') {
          await page.locator('[data-portable-preview] [data-close]').click();
        } else if (interruption === 'project') {
          assert.equal(await page.evaluate(id => authorSwitchProject(id), other.id), true);
        } else {
          await page.evaluate(() => {state.branch_id = 'synthetic-other-branch'; render();});
        }
        release();
        await waitFor(async () => postsSince(start, '/api/backups/import/cancel')
          .some(row => row.body.preview_id === held.preview_id), 'interrupted preview cancellation');
        await page.waitForFunction(() => !document.querySelector('[data-portable-preview]'));
        assert.equal(postsSince(start).length, 0);
        await waitFor(async () => (await control(targetService, 'status')).pending === 0,
          'interrupted preview release');
        await page.unroute(pattern, handler);
        if (interruption === 'branch') await page.evaluate(() => {state.branch_id = 'main'; render();});
        await assertUnchanged(before);
      });
    });
  }
  await test('双击确认只发一次恢复，正在保存时不能修改标题或重复提交', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'status');
      const start = requestLog.length;
      let held, release;
      const gate = new Promise(resolve => {release = resolve;});
      const pattern = '**/api/backups/import/apply';
      const handler = async route => {
        const response = await route.fetch(); held = await response.json();
        await gate; await route.fulfill({response});
      };
      await page.route(pattern, handler);
      await preview(page);
      await page.locator('#portableRestoreTitle').fill('双击只建立一个副本');
      await page.locator('#portableRestoreConfirm').check();
      await page.locator('[data-portable-preview] [type="submit"]').dblclick();
      await waitFor(() => held, 'held apply response');
      assert.equal(postsSince(start).length, 1);
      assert.equal(await page.locator('#portableRestoreTitle').isEditable(), false);
      assert.equal(await page.locator('#portableRestoreConfirm').isDisabled(), true);
      assert.equal(await page.locator('[data-portable-preview] [type="submit"]').isDisabled(), true);
      const value = await pending(page); assertPendingIdentity(value);
      assert.equal(value.project, null);
      release();
      await opened(page, held.project.id);
      await page.unroute(pattern, handler);
      assert.equal((await control(targetService, 'status')).projects.length, before.projects.length + 1);
      assert.equal(postsSince(start).length, 1);
    });
  });
  await test('保存响应丢失后同一身份重试只返回原副本，浏览器不保存ZIP内容', async () => {
    await casePage(async page => {
      const start = requestLog.length;
      const before = await control(targetService, 'status');
      const lost = await lostApply(page, '丢响应后重试原副本');
      await submit(page);
      await opened(page, lost.result.project.id);
      const attempts = postsSince(start);
      assert.equal(attempts.length, 2);
      assert.deepEqual(attempts[0].body, attempts[1].body);
      assert.equal((await control(targetService, 'status')).projects.length, before.projects.length + 1);
      assert.equal(await pending(page), null);
    });
  });
  await test('页面刷新且服务重启后重新选择先取消再读回执，只能打开已保存副本', async () => {
    await casePage(async page => {
      const start = requestLog.length;
      const before = await control(targetService, 'status');
      const lost = await lostApply(page, '重启后打开原保存身份');
      const savedRequest = (await pending(page)).request;
      await stopService(targetService);
      targetService = await launchService('target', targetService.directory, targetService.port);
      base = targetService.base;
      assert.equal(targetService.initiallyAbsent, false);
      assert.equal((await control(targetService, 'status')).pending, 0);
      await page.reload();
      await page.waitForFunction(() => backendOnline);
      await backupsPage(page);
      await page.locator('[data-portable-resume]').click();
      assert.deepEqual((await pending(page)).request, savedRequest);
      await screenshot(page, 'portable-pending-reloaded.png');
      await page.locator('[data-reselect]').click();
      await page.locator('[data-portable-preview] [type="submit"]').filter({hasText: '重新打开副本'}).waitFor();
      assert.equal(await page.locator('[data-reselect]').isVisible(), false);
      assert.equal((await pending(page)).project.id, lost.result.project.id);
      assertPendingIdentity(await pending(page));
      await screenshot(page, 'portable-status-saved.png');
      const routes = requestLog.slice(start).filter(row =>
        row.pathname.endsWith('/cancel') || row.pathname.endsWith('/status'));
      assert.deepEqual(routes.map(row => row.pathname.split('/').pop()), ['cancel', 'status']);
      assert.equal(routes[0].body.preview_id, savedRequest.preview_id);
      assert.deepEqual(routes[1].body, savedRequest);
      await submit(page);
      await opened(page, lost.result.project.id);
      assert.equal(postsSince(start).length, 1);
      assert.equal((await control(targetService, 'status')).projects.length, before.projects.length + 1);
      results.persisted_receipt_restart_verified = true;
    });
  });
  for (const failureMode of ['origin', 'target', 'both']) {
    await test(`副本已保存后${failureMode}读取失败只重开同一身份且不再次建立`, async () => {
      await casePage(async page => {
        const start = requestLog.length;
        const original = await api('/api/state');
        const originalBrowserChapters = await page.evaluate(() => state.chapters);
        const before = await control(targetService, 'status');
        let committed, fail = true;
        const applyPattern = '**/api/backups/import/apply';
        const applyHandler = async route => {
          const response = await route.fetch();
          committed = await response.json();
          assert.equal(response.ok(), true, JSON.stringify(committed));
          await route.fulfill({response});
        };
        const statePattern = '**/api/state';
        const stateHandler = async route => {
          const request = route.request();
          const project = request.headers()['x-moyu-project'];
          if (committed && fail && request.method() === 'GET' &&
              (failureMode === 'origin' ? project === 'default-project' :
                failureMode === 'target' ? project === committed.project.id :
                  ['default-project', committed.project.id].includes(project))) {
            return route.fulfill({status: 503, contentType: 'application/json',
              body: JSON.stringify({error: '合成已保存后读取失败'})});
          }
          return route.continue();
        };
        await page.route(applyPattern, applyHandler);
        await page.route(statePattern, stateHandler);
        await preview(page);
        await submit(page, `保存后读取失败-${failureMode}`);
        await waitFor(() => committed, 'saved restore response before read recovery');
        if (failureMode !== 'origin') {
          await page.locator('[data-portable-preview] .form-error')
            .filter({hasText: /副本已经建立.*暂时无法打开/}).waitFor();
          assert.ok(committed?.project?.id);
          assert.equal((await pending(page)).project.id, committed.project.id);
          assert.equal(await page.locator('#portableRestoreTitle').isEditable(), false);
          assert.equal(await page.locator('[data-reselect]').isVisible(), false);
          assert.equal(await page.locator('[data-portable-preview] [type="submit"]').innerText(),
            '重新打开副本');
          assert.deepEqual(await projectCache(page, committed.project.id),
            {state: null, ':base': null, ':dirty': null},
            'a failed first load must not cache starter text as a restored draft');
          await page.keyboard.press('Control+s');
          assert.deepEqual(await projectCache(page, committed.project.id),
            {state: null, ':base': null, ':dirty': null},
            'saving an unchanged placeholder must not create a draft for the saved project');
          if (failureMode === 'target') await screenshot(page, 'portable-saved-reopen.png');
          assert.equal(postsSince(start).length, 1);
          fail = false;
          if (failureMode === 'both') {
            const savedIdentity = await pending(page);
            await closePreview(page);
            await switchProject(page, 'default-project');
            assert.deepEqual(await projectCache(page, committed.project.id),
              {state: null, ':base': null, ':dirty': null},
              'returning to the source must not cache starter text under the saved project');
            assert.equal(await page.evaluate(() => state.project.id), 'default-project');
            assert.deepEqual(await page.evaluate(() => state.chapters), originalBrowserChapters);
            await page.reload();
            await page.waitForFunction(() => backendOnline);
            await backupsPage(page);
            await page.locator('[data-portable-resume]').click();
            assert.deepEqual(await pending(page), savedIdentity);
            assert.equal(await page.locator('[data-reselect]').isVisible(), false);
            assert.equal(await page.locator('[data-portable-preview] [type="submit"]').innerText(),
              '重新打开副本');
            assert.equal(await page.evaluate(() => authorProjects.length), 0);
          }
          await submit(page);
        }
        await opened(page, committed.project.id);
        if (failureMode === 'both') results.persisted_known_project_reload_verified = true;
        await page.unroute(applyPattern, applyHandler);
        await page.unroute(statePattern, stateHandler);
        assert.equal(postsSince(start).length, 1);
        assert.equal((await control(targetService, 'status')).projects.length, before.projects.length + 1);
        assert.deepEqual((await api('/api/state')).chapters, original.chapters);
      });
    });
  }
  await test('重新打开已保存副本时保留另一页真实编辑的离线草稿，不把旧稿写到新作品', async () => {
    await casePage(async page => {
      const original = await api('/api/state');
      const before = await control(targetService, 'status');
      const start = requestLog.length;
      let committed, release, fail = true;
      const gate = new Promise(resolve => {release = resolve;});
      const applyPattern = '**/api/backups/import/apply';
      const applyHandler = async route => {
        const response = await route.fetch();
        committed = await response.json();
        assert.equal(response.ok(), true, JSON.stringify(committed));
        await gate;
        await route.fulfill({response});
      };
      const statePattern = '**/api/state';
      const stateHandler = route => committed && fail && route.request().method() === 'GET' &&
        route.request().headers()['x-moyu-project'] === committed.project.id ?
        route.fulfill({status: 503, contentType: 'application/json',
          body: JSON.stringify({error: '合成副本首次打开失败'})}) : route.continue();
      await page.route(applyPattern, applyHandler);
      await page.route(statePattern, stateHandler);
      await preview(page);
      await submit(page, '保留真实离线草稿的恢复副本');
      await waitFor(() => committed, 'held committed restore before second page edits');
      const restored = await api('/api/state', 'GET', undefined, committed.project.id);
      const body = restored.chapters[0].body.replace(/\r\n?/g, '\n') +
        '\n作者在另一页留下的真实离线修改𠀀🙂。';
      const editing = await page.context().newPage();
      editing.on('pageerror', error => results.page_errors.push({
        case: currentCase, message: safe(error.message)}));
      const offlineHandler = route => route.request().method() === 'PUT' ?
        route.abort('failed') : route.continue();
      try {
        await editing.goto(base);
        await editing.waitForFunction(() => backendOnline);
        await waitSaved(editing);
        await switchProject(editing, committed.project.id);
        await editing.locator('[data-view="editor"]').click();
        await editing.locator(`[data-chapter="${restored.chapters[0].id}"]`).click();
        await editing.route(statePattern, offlineHandler);
        await editing.locator('#chapterBody').fill(body);
        await editing.waitForFunction(() => !backendOnline && !syncInFlight && isDirty());
        const cached = await projectCache(editing, committed.project.id);
        assert.equal(cached[':dirty'], '1');
        assert.equal(JSON.parse(cached.state).chapters[0].body, body);
        assert.ok(cached[':base']);
        assert.equal((await api('/api/state', 'GET', undefined, committed.project.id))
          .chapters[0].body, restored.chapters[0].body);
        await editing.unrouteAll({behavior: 'ignoreErrors'});
        await editing.close();
        release();
        await page.locator('[data-portable-preview] .form-error')
          .filter({hasText: /副本已经建立.*暂时无法打开/}).waitFor();
        assert.deepEqual(await projectCache(page, committed.project.id), cached,
          'a real draft and its confirmed base must survive failed saved-project opening');
        assert.equal(await page.evaluate(() => state.chapters[0].body), body);
        assert.equal(postsSince(start).length, 1);
        fail = false;
        await submit(page);
        await opened(page, committed.project.id);
        assert.equal((await api('/api/state', 'GET', undefined, committed.project.id))
          .chapters[0].body, body);
        assert.deepEqual((await api('/api/state')).chapters, original.chapters);
        assert.equal(postsSince(start).length, 1);
        assert.equal((await control(targetService, 'status')).projects.length,
          before.projects.length + 1);
        results.real_dirty_draft_preserved = true;
      } finally {
        release();
        if (!editing.isClosed()) {
          await editing.unrouteAll({behavior: 'ignoreErrors'});
          await editing.close();
        }
      }
      await page.unroute(applyPattern, applyHandler);
      await page.unroute(statePattern, stateHandler);
    });
  });
  await test('过期预览失败零写入，先取消并确认未保存后重新选择ZIP恢复', async () => {
    await casePage(async page => {
      const start = requestLog.length;
      const selected = await preview(page);
      const before = await control(targetService, 'freeze');
      await control(targetService, 'expire', {preview_id: selected.preview_id});
      await submit(page, '预览过期后重新选择');
      await page.locator('[data-portable-preview] .form-error').filter({hasText: /过期|重启/}).waitFor();
      await assertUnchanged(before);
      const oldRequest = (await pending(page)).request;
      assert.equal(oldRequest.preview_id, selected.preview_id);
      const chooserEvent = page.waitForEvent('filechooser');
      await page.locator('[data-reselect]').click();
      const chooser = await chooserEvent;
      assert.equal(await pending(page), null);
      await chooser.setFiles(archivePath);
      await page.locator('#portableRestoreTitle').waitFor();
      const second = await page.evaluate(() => authorPortableDialog.preview);
      assert.notEqual(second.preview_id, selected.preview_id);
      const order = requestLog.slice(start).map(row => row.pathname.split('/').pop());
      assert.deepEqual(order, ['preview', 'apply', 'cancel', 'status', 'preview']);
      const statusRequest = postsSince(start, '/api/backups/import/status')[0];
      assert.deepEqual(statusRequest.body, oldRequest);
      await submit(page, '重新选择成功的独立副本');
      await opened(page);
      assert.equal(postsSince(start).length, 2);
    });
  });
  await test('连续两次未确认预览刷新均释放身份，第三次仍能选择恢复取消', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'freeze');
      const start = requestLog.length;
      for (let index = 0; index < 2; index += 1) {
        const selected = await preview(page, `刷新前未确认${index}.zip`);
        assert.ok(selected.preview_id);
        await page.reload();
        await page.waitForFunction(() => backendOnline);
        await backupsPage(page);
      }
      await preview(page, '第三次仍可选择.zip');
      await closePreview(page);
      assert.equal(postsSince(start).length, 0);
      await assertUnchanged(before);
    });
  });
  await test('预览响应尚未到达时刷新仍释放上传前身份，迟到响应不能占用服务器槽', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'freeze');
      const start = requestLog.length;
      let held, release;
      const gate = new Promise(resolve => {release = resolve;});
      const pattern = '**/api/backups/import/preview';
      const handler = async route => {
        if (route.request().postDataJSON().filename !== '未到响应前刷新.zip') return route.continue();
        const request = route.request().postDataJSON();
        assert.ok(/^restore_preview_[a-f0-9]{32}$/.test(request.preview_id));
        const response = await route.fetch();
        held = await response.json();
        assert.equal(held.preview_id, request.preview_id);
        await gate;
        await route.fulfill({response}).catch(() => {});
      };
      await page.route(pattern, handler);
      await choose(page, '未到响应前刷新.zip');
      await waitFor(() => held, 'preview response held before refresh');
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem(
        'mo-yu-novel-v1:portable-restore-unconfirmed') || '[]'));
      assert.ok(stored.some(row => row.preview_id === held.preview_id));
      assert.equal((await control(targetService, 'status')).pending, 1);
      await page.reload();
      await page.waitForFunction(() => backendOnline);
      await backupsPage(page);
      await waitFor(async () => (await control(targetService, 'status')).pending === 0,
        'unseen response preview released after refresh');
      release();
      await waitFor(async () => postsSince(start, '/api/backups/import/cancel')
        .some(row => row.body.preview_id === held.preview_id), 'preissued identity canceled');
      await preview(page, '刷新后仍可校验.zip');
      await closePreview(page);
      await page.unroute(pattern, handler);
      assert.equal(postsSince(start).length, 0);
      await assertUnchanged(before);
    });
  });
  await test('取消请求网络失败后刷新保留身份，恢复连接能释放并再次选择', async () => {
    await casePage(async page => {
      const before = await control(targetService, 'freeze');
      const start = requestLog.length;
      let fail = true;
      const pattern = '**/api/backups/import/cancel';
      const handler = route => fail ? route.abort('failed') : route.continue();
      await page.route(pattern, handler);
      const selected = await preview(page, '取消断网.zip');
      await page.locator('[data-portable-preview] [data-close]').click();
      await waitFor(async () => postsSince(start, '/api/backups/import/cancel')
        .some(row => row.body.preview_id === selected.preview_id), 'failed cancellation');
      assert.equal((await control(targetService, 'status')).pending, 1);
      await page.reload();
      await page.waitForFunction(() => backendOnline);
      await backupsPage(page);
      await page.locator('[data-portable-release]').waitFor();
      const orphanRecords = await page.evaluate(() => JSON.parse(localStorage.getItem(
        'mo-yu-novel-v1:portable-restore-unconfirmed') || '[]'));
      assert.ok(orphanRecords.some(row => row.preview_id === selected.preview_id));
      for (const row of orphanRecords) {
        assert.deepEqual(Object.keys(row).sort(), ['preview_id', 'scope', 'expires_at'].sort());
      }
      fail = false;
      await page.locator('[data-portable-release]').click();
      await waitFor(async () => (await control(targetService, 'status')).pending === 0,
        'retried orphan cancellation');
      await preview(page, '网络恢复后重新选择.zip');
      await closePreview(page);
      await page.unroute(pattern, handler);
      assert.equal(postsSince(start).length, 0);
      await assertUnchanged(before);
    });
  });
  assert.deepEqual(results.page_errors, []);
  assert.deepEqual(results.external_browser_requests, []);
  assert.equal(hash(fs.readFileSync(sourceService.database)), sourceFileHash);
  results.synthetic_source_database_sha256_before = sourceFileHash;
  results.synthetic_source_database_sha256_after = hash(fs.readFileSync(sourceService.database));
  results.synthetic_source_database_unchanged = true;
  const status = await control(targetService, 'status');
  assert.equal(status.pending, 0);
  results.final_project_count = status.projects.length;
  await stopService(targetService);
  for (const service of services) {
    assert.deepEqual(service.calls, {provider: 0, worker: 0, outbound: 0});
  }
  results.service_runs = services.map(service => ({role: service.role,
    initially_absent: service.initiallyAbsent, stopped: service.stopped, calls: service.calls}));
  results.source_files_sha256_after = fingerprints();
  results.source_unchanged = JSON.stringify(results.source_files_sha256_before) ===
    JSON.stringify(results.source_files_sha256_after);
  assert.equal(results.source_unchanged, true);
  results.passed = true;
}

main().catch(async error => {
  results.failure = {message: safe(error.message), stack: safe(error.stack),
    server_logs: services.map(service => ({role: service.role, log: safe(service.log.slice(-5000))}))};
  if (currentPage && !currentPage.isClosed()) {
    results.failure.frontend = await currentPage.evaluate(key => ({
      requestedProject, backendOnline, view, isDirty: isDirty(), syncInFlight, independentWrite,
      pending: JSON.parse(localStorage.getItem(key) || 'null'),
      modal: document.querySelector('[data-portable-preview]')?.innerText,
      toast: document.querySelector('#toast')?.textContent,
    }), pendingKey).catch(() => null);
    await screenshot(currentPage, 'portable-browser-failure.png').catch(() => {});
  }
  console.error(safe(error.stack));
}).finally(async () => {
  for (const context of openContexts) await context.close().catch(() => {});
  if (browser) await browser.close().catch(() => {});
  for (const service of services) {
    if (!service.stopped && service.child.exitCode === null) {
      await stopService(service).catch(() => service.child.kill('SIGKILL'));
    }
    const calls = service.calls || {};
    results.provider_calls += calls.provider || 0;
    results.worker_calls += calls.worker || 0;
    results.outbound_calls += calls.outbound || 0;
  }
  results.finished_at = new Date().toISOString();
  results.source_files_sha256_after ||= fingerprints();
  results.source_unchanged = JSON.stringify(results.source_files_sha256_before) ===
    JSON.stringify(results.source_files_sha256_after);
  results.request_log = requestLog;
  results.case_count = results.cases.length;
  results.artifacts = 'Private isolated synthetic files retained locally.';
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});
  fs.writeFileSync(reportPath, JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify(results));
  process.exitCode = results.passed ? 0 : 1;
});
