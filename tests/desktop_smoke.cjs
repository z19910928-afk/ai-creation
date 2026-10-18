'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {_electron: electron} = require(process.env.MOYU_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const title = '桌面验收临时作品';
const manuscript = '林岚把铜钥匙放在门槛上，向后退了一步。守卫低下头，终于看见钥匙齿间没有擦净的红泥。' +
  '\n\n门里传来轻微的脚步声。她没有再解释，只把手里的灯举高，让门内的人看清她身后空荡荡的小路。' +
  '这一次，必须由守卫自己决定是否打开这扇门。';
const chatDraft = '请记住这条尚未发送的讨论草稿：下一章保留林岚视角。';
const copyText = '桌面剪贴板验收：铜钥匙留在门槛上。';
const report = {passed: false, cases: [], downloads: [], page_errors: 0,
  unexpected_external_requests: 0, provider_jobs: 0, synthetic_data_only: true};
const screenshotDirectory = process.env.MOYU_DESKTOP_SCREENSHOTS_DIR ?
  path.resolve(process.env.MOYU_DESKTOP_SCREENSHOTS_DIR) : null;
let temporary, dataDirectory, downloadDirectory, environment, executable, argumentsForApp;
let application, page, trap, trapURL, trapRequests = 0, currentCase = 'initialization', helpRequested = false;
let modelProvider;
let currentStep = 'initialization';
const childServices = [];
const modelProviderRequests = [];

function options() {
  const args = process.argv.slice(2);
  let binary;
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === '--executable' && args[index + 1]) {
      binary = path.resolve(args[++index]);
    } else if (args[index] === '--help') {
      helpRequested = true;
      console.log('Usage: node tests/desktop_smoke.cjs [--executable <native application binary>]');
      return false;
    } else {
      throw new Error('INVALID_DESKTOP_SMOKE_ARGUMENT');
    }
  }
  executable = binary || require('electron');
  assert.ok(fs.statSync(executable).isFile(), 'DESKTOP_EXECUTABLE_NOT_FOUND');
  argumentsForApp = binary ? ['--smoke-test'] : [root, '--smoke-test'];
  report.target = binary ? 'packaged' : 'development';
  return true;
}

async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await pause(100);
  }
  throw new Error('TIMEOUT_' + label);
}

async function bounded(promise, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('TIMEOUT_' + label)), milliseconds);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

function failureDetails(error) {
  const lines = String(error.message || error).split('\n').map(line => line.trim())
    .filter(Boolean);
  const text = [...lines.slice(0, 4), ...lines.slice(-8)].join(' ')
    .replace(/\u001b\[[0-9;]*m/g, '');
  const location = String(error.stack || '').match(/tests[/\\]desktop_smoke\.cjs:\d+:\d+/)?.[0];
  return {case: currentCase, step: currentStep, location, message: text
    .replaceAll(root, '<project>').replaceAll(temporary || '\0', '<temporary>')
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, '<redacted>').slice(0, 1400)};
}

async function test(name, action, {continueOnFailure = false} = {}) {
  currentCase = name;
  currentStep = 'start';
  try {
    await action();
    report.cases.push({name, passed: true});
  } catch (error) {
    report.cases.push({name, passed: false});
    if (!continueOnFailure) throw error;
    (report.failures ||= []).push(failureDetails(error));
    await page.locator('#desktopSmokeCopy').evaluate(element => element.remove()).catch(() => {});
  }
}

async function saved() {
  await page.waitForFunction(() => backendOnline && confirmedState && !isDirty() &&
    !syncInFlight && !independentWrite && !saveError && !mergePending);
}

async function readAPI(route) {
  return page.evaluate(async endpoint => {
    const response = await fetch(endpoint, {headers: {Accept: 'application/json'}});
    if (!response.ok) throw new Error('DESKTOP_API_' + response.status);
    return response.json();
  }, route);
}

async function launch() {
  application = await electron.launch({executablePath: executable, args: argumentsForApp,
    cwd: root, env: environment, timeout: 90000});
  page = await application.firstWindow({timeout: 90000});
  page.setDefaultTimeout(20000);
  page.on('pageerror', error => {
    report.page_errors += 1;
    (report.page_error_details ||= []).push(failureDetails(error));
  });
  page.on('dialog', dialog => {
    // Electron's main process owns the native unsaved-changes decision.
    if (dialog.type() === 'beforeunload') {
      report.beforeunload_events = (report.beforeunload_events || 0) + 1;
      return;
    }
    report.unexpected_dialogs = (report.unexpected_dialogs || 0) + 1;
    dialog.dismiss().catch(() => {report.dialog_dismiss_failed = true;});
  });
  application.context().on('request', request => {
    const url = request.url();
    if (!url.startsWith('moyu://app/') && !url.startsWith('blob:moyu://app/') &&
        !url.startsWith('data:') && url !== trapURL) {
      report.unexpected_external_requests += 1;
    }
  });
  await page.waitForURL('moyu://app/', {waitUntil: 'load'});
  await page.waitForFunction(() => typeof backendOnline !== 'undefined' &&
    backendOnline && !!globalThis.MoyuAgentChat);
  await page.locator('[data-view="editor"]').click();
  await page.locator('#chapterBody').waitFor();
  await saved();
  const identity = await application.evaluate(({app}) => ({
    directory: app.getPath('userData'), backend: app.__moyuSmokeBackend,
  }));
  assert.equal(fs.realpathSync(identity.directory), fs.realpathSync(dataDirectory));
  assert.ok(Number.isInteger(identity.backend?.pid) && Number.isInteger(identity.backend?.port),
    'DESKTOP_SMOKE_BACKEND_IDENTITY_MISSING');
  assert.notEqual(identity.backend.port, 4174, 'DESKTOP_MUST_NOT_USE_AUTHOR_SERVICE');
  childServices.push(identity.backend);
  return identity.backend;
}

function processAlive(pid) {
  try {process.kill(pid, 0); return true;} catch (error) {
    if (error.code === 'ESRCH') return false;
    throw error;
  }
}

function portOpen(port) {
  return new Promise(resolve => {
    const socket = net.createConnection({host: '127.0.0.1', port});
    let complete = false;
    const finish = value => {
      if (complete) return;
      complete = true;
      socket.destroy();
      resolve(value);
    };
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    socket.setTimeout(500, () => finish(false));
  });
}

async function serviceStopped(identity) {
  await until(async () => !processAlive(identity.pid) && !await portOpen(identity.port),
    'BACKEND_SHUTDOWN', 15000);
}

async function preserveClipboard() {
  const emptyEntries = await application.evaluate(async ({app, clipboard, ClipboardItem}) => {
    const entries = await clipboard.read();
    // Materialize every format before the synthetic write replaces the system clipboard.
    const populated = entries.filter(item => item.types.length > 0);
    app.__moyuSmokeOriginalClipboard = await Promise.all(populated.map(async item =>
      new ClipboardItem(Object.fromEntries(await Promise.all(item.types.map(async type =>
        [type, await item.getType(type)]))))));
    return entries.length - populated.length;
  });
  report.clipboard_empty_entries = emptyEntries;
}

async function restoreClipboard() {
  if (!application) return false;
  return application.evaluate(async ({app, clipboard}) => {
    const entries = app.__moyuSmokeOriginalClipboard;
    if (!Array.isArray(entries)) return false;
    if (entries.length) await clipboard.write(entries);
    else await clipboard.clear();
    delete app.__moyuSmokeOriginalClipboard;
    return true;
  });
}

async function close() {
  const running = application;
  const child = running.process();
  await restoreClipboard();
  await bounded(running.close(), 20000, 'APPLICATION_CLOSE');
  application = null;
  page = null;
  await until(() => child.exitCode !== null || child.signalCode !== null,
    'APPLICATION_PROCESS_EXIT', 5000);
  assert.equal(child.exitCode, 0, 'DESKTOP_EXIT_MUST_BE_CLEAN');
}

async function prepareDownloadCapture() {
  await application.evaluate(({app, BrowserWindow}, {directory, separator}) => {
    const window = BrowserWindow.getAllWindows()[0];
    app.__moyuSmokeDownloads = [];
    let sequence = 0;
    window.webContents.session.on('will-download', (_event, item, contents) => {
      if (contents !== window.webContents) return;
      const filename = item.getFilename().split(/[/\\]/).pop();
      const output = directory + separator + String(++sequence) + '-' + filename;
      item.setSavePath(output);
      item.once('done', (_done, state) => {
        app.__moyuSmokeDownloads.push({state, filename, output});
      });
    });
  }, {directory: downloadDirectory, separator: path.sep});
}

async function download(kind, extension, action) {
  const count = await application.evaluate(({app}) => app.__moyuSmokeDownloads.length);
  await action();
  let row;
  await until(async () => {
    const rows = await application.evaluate(({app}) => app.__moyuSmokeDownloads);
    row = rows[count];
    return !!row;
  }, 'DOWNLOAD_' + kind);
  assert.equal(row.state, 'completed', 'DOWNLOAD_NOT_COMPLETE_' + kind);
  assert.equal(path.extname(row.filename), extension);
  assert.equal(path.dirname(row.output), downloadDirectory);
  const buffer = fs.readFileSync(row.output);
  assert.ok(buffer.length > 40, 'DOWNLOAD_EMPTY_' + kind);
  report.downloads.push({kind, bytes: buffer.length,
    sha256: crypto.createHash('sha256').update(buffer).digest('hex')});
  return buffer;
}

async function assertNoModelJobs() {
  const queue = await readAPI('/api/ai/queue');
  assert.equal(queue.jobs.length, 0, 'SMOKE_MUST_NOT_CREATE_MODEL_JOBS');
  const status = await readAPI('/api/ai/status');
  assert.notEqual(status.mode, 'provider', 'SMOKE_MUST_NOT_CONFIGURE_A_MODEL');
  assert.ok(modelProviderRequests.every(request => request.method === 'GET' &&
    request.path === '/v1/models' && request.key_matches),
  'SMOKE_MODEL_DISCOVERY_MUST_ONLY_READ_AUTHENTICATED_LOCAL_INVENTORY');
  report.provider_jobs = queue.jobs.length;
}

async function modelSettingsDiscovery() {
  currentStep = 'open-packaged-model-settings';
  await page.locator('#connectionAdd').click();
  await page.locator('#modelConnectionForm').waitFor();
  await page.locator('#agentConnectionSettings').waitFor();
  const before = {
    main: (await readAPI('/api/ai/settings')).settings,
    agent: (await readAPI('/api/agent-chat/settings')).settings,
    connections: (await readAPI('/api/ai/connections')).connections,
  };
  const secret = 'synthetic-desktop-model-list-key';
  const models = ['desktop-fixture-alpha', 'desktop-fixture-beta', 'desktop-fixture-gamma'];
  modelProvider = http.createServer((request, response) => {
    request.resume();
    const row = {method: request.method, path: request.url,
      key_matches: request.headers.authorization === 'Bearer ' + secret};
    modelProviderRequests.push(row);
    const allowed = row.method === 'GET' && row.path === '/v1/models' && row.key_matches;
    response.writeHead(allowed ? 200 : 405, {'Content-Type': 'application/json'});
    response.end(JSON.stringify(allowed ? {data: models.map(id => ({id}))} :
      {error: {message: 'Synthetic inventory only; generation is disabled.'}}));
  });
  await new Promise((resolve, reject) => {
    modelProvider.once('error', reject);
    modelProvider.listen(0, '127.0.0.1', resolve);
  });
  const providerBase = 'http://127.0.0.1:' + modelProvider.address().port + '/v1';
  const discovered = [];
  async function automaticList(prefix, protocol, edit) {
    currentStep = 'automatic-model-list-' + prefix + '-' + protocol.replaceAll('_', '-');
    const endpoint = prefix === 'connection' ?
      '/api/ai/connections/models' : '/api/agent-chat/settings/models';
    const beforeRequests = modelProviderRequests.length;
    const [response] = await Promise.all([
      page.waitForResponse(value => {
        if (value.url() !== 'moyu://app' + endpoint || value.request().method() !== 'POST') return false;
        const body = value.request().postDataJSON();
        return body?.base_url === providerBase && body.api_protocol === protocol &&
          body.api_key === secret;
      }),
      edit(),
    ]);
    assert.equal(response.status(), 200, 'DESKTOP_MODEL_LIST_API_FAILED');
    assert.deepEqual((await response.json()).models, models);
    await page.locator('#' + prefix + 'ModelListStatus[data-state="success"]').waitFor();
    await page.waitForFunction(id => document.getElementById(id)?.disabled === false,
      prefix + 'FetchModels');
    assert.equal(await page.locator('#' + prefix + 'ModelList [role="option"]').count(), models.length);
    assert.equal(modelProviderRequests.length, beforeRequests + 1,
      'DESKTOP_MODEL_LIST_MUST_BE_AUTOMATIC_AND_SINGLE');
    discovered.push({target: prefix, protocol});
  }
  async function searchAndSelect(prefix, query, model, keyboard) {
    currentStep = 'search-select-model-' + prefix + '-' + (keyboard ? 'keyboard' : 'mouse');
    const input = page.locator('#' + prefix + 'Model');
    await input.fill(query);
    assert.equal(await input.getAttribute('role'), 'combobox');
    assert.equal(await input.getAttribute('aria-expanded'), 'true');
    const options = page.locator('#' + prefix + 'ModelList [role="option"]');
    assert.equal(await options.count(), 1, 'DESKTOP_MODEL_SEARCH_NOT_FILTERED');
    if (keyboard) {
      await input.press('ArrowDown');
      assert.ok(await input.getAttribute('aria-activedescendant'));
      await input.press('Enter');
    } else {
      await options.filter({hasText: model}).click();
    }
    assert.equal(await input.inputValue(), model);
    assert.equal(await input.getAttribute('aria-expanded'), 'false');
  }

  assert.equal(await page.locator('#connectionApiProtocol').inputValue(), 'chat_completions');
  await page.locator('#connectionName').fill('Synthetic desktop inventory');
  await page.locator('#connectionKey').fill(secret);
  await automaticList('connection', 'chat_completions', async () => {
    await page.locator('#connectionBaseUrl').fill(providerBase);
    await page.locator('#connectionBaseUrl').blur();
  });
  assert.equal(await page.locator('#connectionModel').inputValue(), '',
    'DESKTOP_MODEL_LIST_MUST_NOT_SELECT_FOR_AUTHOR');
  await searchAndSelect('connection', 'beta', models[1], true);
  await automaticList('connection', 'responses', async () => {
    await page.locator('#connectionApiProtocol').selectOption('responses');
    await page.locator('#connectionApiProtocol').blur();
  });
  assert.equal(await page.locator('.settings-compatibility').isVisible(), false);
  await searchAndSelect('connection', 'gamma', models[2], false);
  assert.equal(await page.locator('#connectionSave').isDisabled(), true,
    'DESKTOP_MODEL_LIST_MUST_NOT_VERIFY_CONNECTION');
  await page.locator('#connectionCancel').click();
  assert.equal(await page.locator('#connectionDialog').isVisible(), false);

  await page.locator('[name="agentConnectionMode"][value="independent"]').check();
  assert.equal(await page.locator('#agentApiProtocol').inputValue(), 'chat_completions');
  await page.locator('#agentApiKey').fill(secret);
  await automaticList('agent', 'chat_completions', async () => {
    await page.locator('#agentBaseUrl').fill(providerBase);
    await page.locator('#agentBaseUrl').blur();
  });
  assert.equal(await page.locator('#agentModel').inputValue(), '');
  await searchAndSelect('agent', 'alpha', models[0], false);
  await automaticList('agent', 'responses', async () => {
    await page.locator('#agentApiProtocol').selectOption('responses');
    await page.locator('#agentApiProtocol').blur();
  });
  assert.equal(await page.locator('.agent-settings-compatibility').isVisible(), true);
  assert.equal(await page.locator('.agent-chat-compatibility').getAttribute('hidden'), '');
  assert.equal(await page.locator('#agentMaxTokens').getAttribute('min'), '16');
  await searchAndSelect('agent', 'gamma', models[2], true);

  currentStep = 'discard-unsaved-model-settings';
  await page.locator('#connectionAdd').click();
  await page.locator('#connectionReset').click();
  await page.locator('#connectionCancel').click();
  await page.locator('#agentBaseUrl').fill('');
  await page.locator('#agentApiKey').fill('');
  await page.locator('#agentModel').fill('');
  await page.locator('#agentApiProtocol').selectOption('chat_completions');
  await page.locator('[name="agentConnectionMode"][value="inherit"]').check();
  for (const id of ['connectionBaseUrl', 'connectionKey', 'connectionModel',
    'agentBaseUrl', 'agentApiKey', 'agentModel']) {
    assert.equal(await page.locator('#' + id).inputValue(), '');
  }
  assert.deepEqual((await readAPI('/api/ai/settings')).settings, before.main);
  assert.deepEqual((await readAPI('/api/agent-chat/settings')).settings, before.agent);
  assert.deepEqual((await readAPI('/api/ai/connections')).connections, before.connections);
  await assertNoModelJobs();
  report.model_discovery = {passed: true, automatic_lists: discovered,
    local_inventory_requests: modelProviderRequests.length, models_per_list: models.length,
    search_keyboard_and_mouse: true, saved_settings_unchanged: true,
    generation_requests: modelProviderRequests.filter(request => request.method !== 'GET').length};
}

async function main() {
  if (!options()) return;
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'moyu-desktop-smoke-'));
  fs.chmodSync(temporary, 0o700);
  dataDirectory = path.join(temporary, 'data');
  downloadDirectory = path.join(temporary, 'downloads');
  fs.mkdirSync(dataDirectory, {mode: 0o700});
  fs.mkdirSync(downloadDirectory, {mode: 0o700});
  environment = {...process.env};
  for (const name of Object.keys(environment)) {
    if (/^(MOYU_|OPENAI_|AZURE_OPENAI_|ANTHROPIC_|GEMINI_|GOOGLE_API_|LANGCHAIN_|LANGSMITH_)/i.test(name) ||
        /^(NODE_OPTIONS|ELECTRON_RUN_AS_NODE|PYTHONHOME|PYTHONPATH)$/i.test(name)) delete environment[name];
  }
  environment.MOYU_DESKTOP_TEST_DATA_DIR = dataDirectory;
  environment.MOYU_DESKTOP_PYTHON = process.env.MOYU_TEST_PYTHON ||
    path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  trap = http.createServer((_request, response) => {trapRequests += 1; response.end('unexpected');});
  await new Promise(resolve => trap.listen(0, '127.0.0.1', resolve));
  trapURL = 'http://127.0.0.1:' + trap.address().port + '/must-not-open';
  let firstBackend;

  await test('isolated-launch-fixed-origin-renderer-sandbox', async () => {
    firstBackend = await launch();
    assert.equal(page.url(), 'moyu://app/');
    const renderer = await page.evaluate(() => ({protocol: location.protocol, host: location.host,
      origin: location.origin,
      require: typeof globalThis.require, process: typeof globalThis.process,
      module: typeof globalThis.module, secure: isSecureContext}));
    assert.deepEqual(renderer, {protocol: 'moyu:', host: 'app', origin: 'moyu://app', require: 'undefined',
      process: 'undefined', module: 'undefined', secure: true});
    const preferences = await application.evaluate(({BrowserWindow}) =>
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
    assert.equal(preferences.nodeIntegration, false);
    assert.equal(preferences.contextIsolation, true);
    assert.equal(preferences.sandbox, true);
    await assertNoModelJobs();
  });

  await test('real-editor-save-and-persistent-chat-draft', async () => {
    currentStep = 'open-visible-workbench-project-editor';
    await page.locator('[data-workbench-project]').click();
    currentStep = 'fill-project-title-and-summary';
    await page.locator('#pTitle').fill(title);
    await page.locator('#pSummary').fill('守卫必须决定是否接过门槛上的铜钥匙。');
    currentStep = 'save-project-metadata';
    await page.locator('.modal [data-ok]').click();
    currentStep = 'fill-and-save-manuscript';
    await page.locator('#chapterBody').fill(manuscript);
    await saved();
    currentStep = 'verify-persisted-manuscript-api';
    const state = await readAPI('/api/state');
    assert.equal(state.project.title, title);
    assert.equal(state.chapters.find(item => item.id === state.chapters[0].id).body, manuscript);
    currentStep = 'check-chat-draft-across-real-pages';
    await page.locator('#agentChatInput').fill(chatDraft);
    await page.locator('[data-view="memory"]').click();
    assert.equal(await page.locator('#agentChatInput').inputValue(), chatDraft);
    await page.locator('#agentChatInput').fill(chatDraft + ' 草稿在记忆页仍可编辑。');
    await page.locator('[data-view="settings"]').click();
    assert.equal(await page.locator('#agentChatInput').inputValue(),
      chatDraft + ' 草稿在记忆页仍可编辑。');
    await modelSettingsDiscovery();
    await page.locator('#agentChatInput').fill(chatDraft);
    await page.locator('[data-view="editor"]').click();
    assert.equal(await page.locator('#agentChatInput').inputValue(), chatDraft);
    assert.equal(await page.locator('#agentChatFeed .agent-chat-message').count(), 0);
  });

  await test('desktop-layout-1100-1280-and-1440', async () => {
    for (const size of [{width: 1100, height: 760}, {width: 1280, height: 900},
      {width: 1440, height: 1000}]) {
      currentStep = 'verify-window-size-' + size.width + '-' + size.height;
      const contentSize = await application.evaluate(({BrowserWindow}, dimensions) => {
        const window = BrowserWindow.getAllWindows()[0];
        window.setSize(dimensions.width, dimensions.height);
        return window.getContentSize();
      }, size);
      await page.waitForFunction(([width,height]) => innerWidth === width && innerHeight === height,
        contentSize);
      const layout = await page.evaluate(() => {
        const rect = selector => {
          const box = document.querySelector(selector).getBoundingClientRect();
          return {left: box.left, right: box.right, top: box.top, bottom: box.bottom,
            width: box.width, height: box.height};
        };
        return {width: innerWidth, height: innerHeight, scroll: document.documentElement.scrollWidth,
          editor: rect('#chapterBody'), chat: rect('#agentChat'), input: rect('#agentChatInput')};
      });
      assert.ok(layout.scroll <= layout.width + 1, 'DESKTOP_HORIZONTAL_OVERFLOW');
      assert.ok(layout.editor.width > 100 && layout.chat.width > 200);
      assert.ok(layout.editor.right <= layout.chat.left + 1, 'EDITOR_OVERLAPS_CHAT');
      assert.ok(layout.chat.right <= layout.width + 1 && layout.input.right <= layout.width + 1);
      assert.ok(layout.input.top >= 0 && layout.input.bottom <= layout.height + 1,
        'CHAT_COMPOSER_OUTSIDE_WINDOW');
      if (screenshotDirectory) {
        fs.mkdirSync(screenshotDirectory, {recursive: true, mode: 0o700});
        const filename = 'desktop-' + size.width + '.png';
        const output = path.join(screenshotDirectory, filename);
        await page.screenshot({path: output});
        fs.chmodSync(output, 0o600);
        (report.screenshots ||= []).push(filename);
      }
    }
  });

  await test('clipboard-write-from-user-gesture', async () => {
    currentStep = 'activate-native-desktop-window';
    await application.evaluate(async ({app, BrowserWindow}) => {
      const window = BrowserWindow.getAllWindows()[0];
      if (process.platform === 'darwin') {
        await app.dock?.show();
        app.focus({steal: true});
      }
      window.show();
      window.focus();
    });
    try {
      await until(() => application.evaluate(({BrowserWindow}) =>
        BrowserWindow.getAllWindows()[0].isFocused()), 'NATIVE_WINDOW_FOCUS', 10000);
    } catch (error) {
      report.clipboard_diagnostics = await application.evaluate(({app, BrowserWindow}) => {
        const window = BrowserWindow.getAllWindows()[0];
        return {window_focused: window.isFocused(), window_visible: window.isVisible(),
          window_minimized: window.isMinimized(), application_hidden: app.isHidden?.() ?? null};
      });
      throw error;
    }
    currentStep = 'preserve-all-clipboard-formats';
    await preserveClipboard();
    try {
      await page.evaluate(text => {
        const button = document.createElement('button');
        button.id = 'desktopSmokeCopy';
        button.textContent = '复制验收文字';
        button.style.cssText = 'position:fixed;top:80px;left:20px;z-index:99999';
        button.onclick = async () => {
          try {await navigator.clipboard.writeText(text); window.__desktopSmokeCopied = true;}
          catch (error) {
            window.__desktopSmokeCopied = false;
            window.__desktopSmokeCopyError = error.name;
          }
        };
        document.body.append(button);
      }, copyText);
      await page.locator('#desktopSmokeCopy').click();
      await page.waitForFunction(() => typeof window.__desktopSmokeCopied === 'boolean');
      currentStep = 'clipboard-user-gesture-' +
        (await page.evaluate(() => window.__desktopSmokeCopyError || 'completed'));
      assert.equal(await page.evaluate(() => window.__desktopSmokeCopied), true,
        'RENDERER_CLIPBOARD_WRITE_REJECTED');
      currentStep = 'verify-native-clipboard-content';
      try {
        await until(() => application.evaluate(async ({clipboard}, text) =>
          (await clipboard.readText()) === text, copyText), 'NATIVE_CLIPBOARD_CONTENT', 5000);
      } catch (error) {
        report.clipboard_diagnostics = await application.evaluate(async ({clipboard, BrowserWindow}, text) => {
          await clipboard.writeText(text);
          const entries = await clipboard.read();
          return {native_round_trip: (await clipboard.readText()) === text,
            formats_after_synthetic_write: entries.flatMap(item => item.types),
            window_focused: BrowserWindow.getAllWindows()[0].isFocused()};
        }, copyText + ' 原生回环');
        report.clipboard_diagnostics.renderer_focused = await page.evaluate(() => document.hasFocus());
        throw error;
      }
      assert.equal(await page.evaluate(async () =>
        (await navigator.permissions.query({name: 'clipboard-read'})).state), 'denied',
      'RENDERER_CLIPBOARD_READ_MUST_STAY_DENIED');
      await page.locator('#desktopSmokeCopy').evaluate(element => element.remove());
    } finally {
      report.clipboard_restored = await restoreClipboard();
      assert.equal(report.clipboard_restored, true, 'ORIGINAL_CLIPBOARD_NOT_RESTORED');
    }
  }, {continueOnFailure: true});

  await test('external-navigation-and-popup-rejected', async () => {
    currentStep = 'reject-external-link-navigation';
    await application.evaluate(({app, BrowserWindow}, target) => {
      app.__moyuSmokeNavigationBlocked = false;
      BrowserWindow.getAllWindows()[0].webContents.on('will-navigate', (event, url) => {
        if (url === target) app.__moyuSmokeNavigationBlocked = event.defaultPrevented;
      });
    }, trapURL);
    await page.evaluate(target => {
      const link = document.createElement('a');
      link.id = 'desktopSmokeExternalLink';
      link.href = target;
      link.textContent = '外部导航验收';
      link.style.cssText = 'position:fixed;top:80px;left:20px;z-index:99999';
      document.body.append(link);
    }, trapURL);
    await page.locator('#desktopSmokeExternalLink').click({noWaitAfter: true});
    await until(() => application.evaluate(({app}) => app.__moyuSmokeNavigationBlocked),
      'NAVIGATION_REJECTION');
    assert.equal(page.url(), 'moyu://app/');
    currentStep = 'reject-external-popup';
    const opened = await page.evaluate(target => window.open(target, '_blank') === null, trapURL);
    assert.equal(opened, true);
    assert.equal(await application.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows().length), 1);
    assert.equal(trapRequests, 0);
    currentStep = 'restore-fixed-origin-after-rejected-navigation';
    await page.evaluate(() => document.querySelector('#desktopSmokeExternalLink').remove());
    // Electron cancels this navigation before Playwright receives a completion event.
    await page.goto('moyu://app/');
    await page.waitForFunction(() => backendOnline && !!globalThis.MoyuAgentChat);
    await page.locator('[data-view="editor"]').click();
    await saved();
  });

  await test('four-real-downloads-json-markdown-epub-complete-backup', async () => {
    await prepareDownloadCapture();
    currentStep = 'download-and-verify-json';
    const json = await download('manuscript-json', '.json', () => page.locator('#exportBtn').click());
    const value = JSON.parse(json.toString('utf8'));
    assert.equal(value.project.title, title);
    assert.equal(value.chapters[0].body, manuscript);
    currentStep = 'acknowledge-synthetic-manuscript-export-warning';
    await page.locator('#finalCheckBtn').click();
    await page.locator('.modal [data-override]').waitFor();
    await page.locator('.modal [data-override]').click();
    await page.locator('dialog[data-text-request] textarea[data-text-input]')
      .fill('桌面验收合成作品，仅检查导出文件，不作为正式完稿。');
    await page.locator('dialog[data-text-request] [data-text-submit]').click();
    await page.locator('.modal .override-note').waitFor();
    await page.locator('.modal [data-close]').click();
    currentStep = 'download-and-verify-markdown';
    const markdown = await download('markdown', '.md', () => page.locator('#markdownExportBtn').click());
    const markdownText = markdown.toString('utf8');
    assert.ok(markdownText.includes(manuscript));
    assert.ok(markdownText.includes('format: moyu-markdown-v2'));
    const sources = [...markdownText.matchAll(
      /<!-- moyu-manuscript-v1 ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) -->/g)];
    assert.equal(sources.length, 1, 'MARKDOWN_SOURCE_MANIFEST_MISSING');
    const source = Buffer.from(sources[0][2], 'base64');
    assert.equal(crypto.createHash('sha256').update(source).digest('hex'), sources[0][1]);
    const exported = JSON.parse(source);
    assert.equal(exported.project.title, title);
    assert.equal(exported.chapters[0].body, manuscript);
    currentStep = 'download-and-verify-epub';
    const epub = await download('epub', '.epub', () => page.locator('#epubExportBtn').click());
    assert.equal(epub.readUInt32LE(0), 0x04034b50);
    for (const member of ['mimetype', 'META-INF/container.xml', 'META-INF/moyu-export-manifest.json']) {
      assert.ok(epub.includes(Buffer.from(member)), 'EPUB_REQUIRED_MEMBER_MISSING');
    }
    currentStep = 'create-and-download-complete-backup';
    await page.locator('[data-view="backups"]').click();
    await page.waitForFunction(() => !document.querySelector('#backupList .empty')?.textContent.includes('正在'));
    const backupsBefore = await page.locator('[data-backup-download]').count();
    await page.locator('[data-action="createBackup"]').click();
    await page.waitForFunction(count => document.querySelectorAll('[data-backup-download]').length > count,
      backupsBefore);
    const backup = await download('complete-backup', '.zip',
      () => page.locator('[data-backup-download]').first().click());
    assert.equal(backup.readUInt32LE(0), 0x04034b50);
    assert.ok(backup.includes(Buffer.from('project.json')));
    assert.ok(backup.includes(Buffer.from('manifest.json')));
    await page.locator('[data-view="editor"]').click();
    await saved();
  });

  await test('unsaved-close-cancel-keeps-window-and-draft', async () => {
    await saved();
    await application.evaluate(({app, dialog}) => {
      app.__moyuSmokeOriginalDialog = dialog.showMessageBoxSync;
      app.__moyuSmokeClosePrompts = 0;
      dialog.showMessageBoxSync = () => {app.__moyuSmokeClosePrompts += 1; return 0;};
    });
    // Pause automatic syncing to keep a genuine unsaved edit during the close attempt.
    await page.evaluate(() => {backendOnline = false; clearTimeout(syncTimer);});
    await page.locator('#chapterBody').fill(manuscript + '\n\n取消退出后，这段尚未保存的文字仍应保留。');
    assert.equal(await page.evaluate(() => isDirty()), true);
    await application.evaluate(({BrowserWindow}) => {BrowserWindow.getAllWindows()[0].close();});
    await until(() => application.evaluate(({app}) => app.__moyuSmokeClosePrompts > 0),
      'UNSAVED_CLOSE_DIALOG');
    assert.equal(page.isClosed(), false);
    assert.ok((await page.locator('#chapterBody').inputValue()).endsWith('取消退出后，这段尚未保存的文字仍应保留。'));
    await page.evaluate(async () => {backendOnline = true; await syncBackend();});
    await saved();
    await page.locator('#chapterBody').fill(manuscript);
    await saved();
    await application.evaluate(({app, dialog}) => {
      dialog.showMessageBoxSync = app.__moyuSmokeOriginalDialog;
      delete app.__moyuSmokeOriginalDialog;
    });
  });

  await test('single-instance-reuses-existing-desktop', async () => {
    const secondary = spawn(executable, argumentsForApp,
      {cwd: root, env: environment, stdio: 'ignore', windowsHide: true});
    let launchError;
    secondary.once('error', () => {launchError = true;});
    try {
      await until(() => !!launchError || secondary.exitCode !== null, 'SECOND_INSTANCE_EXIT', 15000);
      assert.equal(launchError, undefined, 'SECOND_INSTANCE_FAILED_TO_START');
      assert.equal(secondary.exitCode, 0, 'SECOND_INSTANCE_DID_NOT_EXIT_CLEANLY');
      assert.equal(await application.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows().length), 1);
      assert.deepEqual(await application.evaluate(({app}) => app.__moyuSmokeBackend), firstBackend);
      assert.equal(await page.locator('#agentChatInput').inputValue(), chatDraft);
    } finally {
      if (secondary.exitCode === null) secondary.kill();
    }
  });

  await test('clean-exit-stops-owned-service', async () => {
    await assertNoModelJobs();
    await close();
    await serviceStopped(firstBackend);
  });

  await test('restart-restores-manuscript-and-chat-draft', async () => {
    const secondBackend = await launch();
    assert.notEqual(secondBackend.pid, firstBackend.pid);
    assert.equal(await page.locator('#chapterBody').inputValue(), manuscript);
    assert.equal(await page.locator('#agentChatInput').inputValue(), chatDraft);
    assert.equal((await readAPI('/api/state')).project.title, title);
    await assertNoModelJobs();
    assert.equal(trapRequests, 0, 'DESKTOP_CONTACTED_EXTERNAL_TRAP');
    assert.equal(report.page_errors, 0, 'DESKTOP_RENDERER_ERRORS');
    assert.equal(report.unexpected_external_requests, 0, 'DESKTOP_EXTERNAL_REQUESTS');
    assert.equal(report.unexpected_dialogs || 0, 0, 'DESKTOP_UNEXPECTED_NATIVE_DIALOGS');
    assert.equal(report.dialog_dismiss_failed || false, false, 'DESKTOP_DIALOG_DISMISS_FAILED');
    await close();
    await serviceStopped(secondBackend);
  });
  if (report.failures?.length) {
    report.failure = report.failures[0];
    process.exitCode = 1;
  } else {
    report.passed = true;
  }
}

async function cleanup() {
  let clipboardRestoreFailed = false;
  if (application) {
    await restoreClipboard().catch(() => {
      clipboardRestoreFailed = true;
      report.clipboard_restore_failed = true;
    });
    await application.evaluate(({app, dialog}) => {
      if (app.__moyuSmokeOriginalDialog) {
        dialog.showMessageBoxSync = app.__moyuSmokeOriginalDialog;
        delete app.__moyuSmokeOriginalDialog;
      }
    }).catch(() => {});
    const process = application.process();
    await bounded(application.close(), 10000, 'CLEANUP_CLOSE').catch(() => {process.kill();});
    application = null;
    await until(() => process.exitCode !== null || process.signalCode !== null,
      'CLEANUP_PROCESS', 5000).catch(() => {});
  }
  for (const identity of childServices) {
    if (processAlive(identity.pid)) {
      try {process.kill(identity.pid);} catch {}
      await until(() => !processAlive(identity.pid), 'CLEANUP_BACKEND', 5000).catch(() => {});
    }
  }
  if (trap) await new Promise(resolve => trap.close(resolve));
  if (modelProvider) {
    modelProvider.closeAllConnections();
    await new Promise(resolve => modelProvider.close(resolve));
  }
  if (temporary) fs.rmSync(temporary, {recursive: true, force: true, maxRetries: 10, retryDelay: 100});
  if (clipboardRestoreFailed) throw new Error('CLIPBOARD_RESTORE_FAILED');
}

main().catch(error => {
  report.failure = failureDetails(error);
  process.exitCode = 1;
}).finally(async () => {
  await cleanup().catch(() => {
    report.passed = false;
    report.cleanup_failed = true;
    process.exitCode = 1;
  });
  if (!helpRequested) console.log(JSON.stringify(report));
});
