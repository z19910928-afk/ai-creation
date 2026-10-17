'use strict';

const {app, BrowserWindow, Menu, dialog, net, protocol, session, shell} = require('electron');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const runtime = require('./runtime.cjs');

protocol.registerSchemesAsPrivileged([{scheme: 'moyu', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
}}]);

const smoke = process.argv.includes('--smoke-test');
const root = path.resolve(__dirname, '..');
const applicationName = '墨屿';
let backend, backendPort, window, stopping, quitting = false, backendFailed = false;
let startupComplete = false;
const token = runtime.newToken();

app.setName(applicationName);
try {
  app.setPath('userData', smoke ?
    runtime.smokeDataDirectory(process.env.MOYU_DESKTOP_TEST_DATA_DIR) :
    path.join(app.getPath('appData'), 'Moyu'));
} catch {
  process.stderr.write('MOYU_DESKTOP_INVALID_TEST_DIRECTORY\n');
  app.exit(1);
}

function fail(message) {
  if (backendFailed || quitting) return;
  backendFailed = true;
  if (smoke) {
    process.stderr.write('MOYU_DESKTOP_STARTUP_FAILED\n');
    app.exitCode = 1;
  } else {
    dialog.showErrorBox('墨屿暂时无法继续', message);
  }
  app.quit();
}

async function startBackend() {
  const dataDirectory = app.getPath('userData');
  fs.mkdirSync(dataDirectory, {recursive: true, mode: 0o700});
  const command = runtime.backendCommand({packaged: app.isPackaged,
    resourcesPath: process.resourcesPath, root,
    python: process.env.MOYU_DESKTOP_PYTHON ||
      path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')});
  backend = spawn(command.file, command.args, {
    env: runtime.backendEnvironment(process.env, dataDirectory, token),
    cwd: dataDirectory, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
  });
  await new Promise((resolve, reject) => {
    let buffer = '', settled = false;
    const timer = setTimeout(() => complete(new Error('BACKEND_START_TIMEOUT')), 60000);
    const complete = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      backend.stdout.off('data', read);
      error ? reject(error) : resolve();
    };
    const read = chunk => {
      buffer += chunk.toString('utf8');
      if (buffer.length > 65536) return complete(new Error('BACKEND_START_INVALID'));
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        const ready = runtime.readyMessage(line, backend.pid);
        if (ready) {backendPort = ready.port; return complete();}
      }
    };
    backend.stdout.on('data', read);
    backend.once('error', () => complete(new Error('BACKEND_START_FAILED')));
    backend.once('exit', () => complete(new Error('BACKEND_EXITED')));
    // Never forward backend output: an exception may contain a user's text.
    backend.stderr.on('data', () => {});
  });
  backend.on('exit', () => {
    if (startupComplete && !quitting) {
      fail('本机写作服务已停止。请重新打开墨屿，作品和本机草稿会继续保留。');
    }
  });
  const response = await net.fetch('http://127.0.0.1:' + backendPort + '/api/desktop/health',
    {headers: {'X-Moyu-Desktop-Token': token}, redirect: 'error'});
  if (!response.ok || (await response.json()).ok !== true) throw new Error('BACKEND_NOT_HEALTHY');
  if (smoke) app.__moyuSmokeBackend = {pid: backend.pid, port: backendPort};
}

function stopBackend() {
  if (stopping) return stopping;
  stopping = new Promise(resolve => {
    if (!backend || backend.exitCode !== null || backend.signalCode !== null) return resolve();
    const timer = setTimeout(() => {backend.kill(); resolve();}, 5000);
    backend.once('exit', () => {clearTimeout(timer); resolve();});
    backend.stdin.end();
  });
  return stopping;
}

async function registerLocalProtocol() {
  const local = session.fromPartition('persist:moyu');
  const clipboardWriteAllowed = (contents, permission, origin, details) =>
    contents === window?.webContents && permission === 'clipboard-sanitized-write' &&
    details.isMainFrame === true && runtime.isAppURL(contents.getURL()) &&
    runtime.isAppURL(origin) && runtime.isAppURL(details.requestingUrl);
  local.setPermissionRequestHandler((contents, permission, callback, details) =>
    callback(clipboardWriteAllowed(contents, permission, details.requestingUrl, details)));
  local.setPermissionCheckHandler((contents, permission, origin, details) =>
    clipboardWriteAllowed(contents, permission, origin, details));
  local.webRequest.onBeforeRequest((details, callback) => {
    const allowed = runtime.isAppURL(details.url) ||
      details.url.startsWith('blob:' + runtime.APP_ORIGIN + '/');
    callback({cancel: !allowed});
  });
  await local.protocol.handle('moyu', async request => {
    if (!runtime.isAppURL(request.url)) return new Response(null, {status: 403});
    const sourceOrigin = request.headers.get('Origin');
    if (sourceOrigin && sourceOrigin !== runtime.APP_ORIGIN) return new Response(null, {status: 403});
    try {
      const options = {method: request.method,
        headers: runtime.proxyHeaders(request.headers, backendPort, token),
        redirect: 'error', cache: 'no-store'};
      if (!['GET', 'HEAD'].includes(request.method)) options.body = await request.arrayBuffer();
      const response = await net.fetch(runtime.proxyURL(request.url, backendPort), options);
      const headers = new Headers(response.headers);
      headers.set('Content-Security-Policy', runtime.CSP);
      headers.set('X-Content-Type-Options', 'nosniff');
      headers.set('Cache-Control', 'no-store');
      return new Response(response.body, {status: response.status,
        statusText: response.statusText, headers});
    } catch {
      return Response.json({error: '本机服务暂时不可用，草稿仍保存在本机。'}, {status: 503});
    }
  });
  local.on('will-download', (_event, item, contents) => {
    if (!contents || contents !== window?.webContents || !runtime.isDownloadURL(item.getURL())) {
      item.cancel();
      return;
    }
    item.setSaveDialogOptions({title: '保存作品文件',
      defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename()))});
    item.once('done', (_doneEvent, state) => {
      if (state === 'interrupted' && window && !window.isDestroyed()) {
        dialog.showMessageBox(window, {type: 'error', title: '文件没有保存成功',
          message: '请重新导出，并选择可以写入的文件夹。'});
      }
    });
  });
}

function createWindow() {
  window = new BrowserWindow({
    title: applicationName, width: 1440, height: 1000, minWidth: 1100, minHeight: 760,
    backgroundColor: '#ffffff', show: false,
    webPreferences: {partition: 'persist:moyu', nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true,
      allowRunningInsecureContent: false, spellcheck: false},
  });
  window.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  window.webContents.on('will-navigate', (event, url) => {
    if (!runtime.isAppURL(url)) event.preventDefault();
  });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.on('will-prevent-unload', event => {
    const decision = dialog.showMessageBoxSync(window, {type: 'warning',
      title: '还有未保存的修改', message: '当前修改尚未保存，要继续编辑吗？',
      detail: '未保存的内容可能无法恢复。建议继续编辑，等待显示“已保存到本机”或导出备份后再退出。',
      buttons: ['继续编辑', '仍然退出'], defaultId: 0, cancelId: 0, noLink: true});
    if (decision === 1) event.preventDefault();
  });
  window.webContents.on('render-process-gone', () => {
    fail('创作窗口意外关闭。请重新打开墨屿，作品和已缓存草稿会继续保留。');
  });
  window.once('ready-to-show', () => {if (!smoke) window.show();});
  window.on('closed', () => {window = null; app.quit();});
  return window.loadURL(runtime.APP_ORIGIN + '/');
}

function installMenu() {
  const template = [];
  if (process.platform === 'darwin') {
    template.push({label: applicationName, submenu: [
      {role: 'about', label: '关于墨屿'}, {type: 'separator'},
      {role: 'hide', label: '隐藏墨屿'}, {role: 'hideOthers', label: '隐藏其他'},
      {role: 'unhide', label: '显示全部'}, {type: 'separator'}, {role: 'quit', label: '退出墨屿'},
    ]});
  }
  template.push({label: '编辑', submenu: [
    {role: 'undo', label: '撤销'}, {role: 'redo', label: '重做'}, {type: 'separator'},
    {role: 'cut', label: '剪切'}, {role: 'copy', label: '复制'},
    {role: 'paste', label: '粘贴'}, {role: 'selectAll', label: '全选'},
  ]}, {label: '窗口', submenu: [
    {role: 'resetZoom', label: '实际大小'}, {role: 'zoomIn', label: '放大'},
    {role: 'zoomOut', label: '缩小'}, {type: 'separator'},
    {role: 'togglefullscreen', label: '全屏'}, {role: 'minimize', label: '最小化'},
  ]}, {label: '帮助', submenu: [
    {label: '打开作品数据文件夹', click: () => shell.openPath(app.getPath('userData'))},
    {label: '关于墨屿', click: () => dialog.showMessageBox(window, {type: 'info',
      title: '墨屿', message: '墨屿 ' + app.getVersion(),
      detail: '作品保存在本机。模型连接请在“设置”中填写。'})},
  ]});
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

if (!app.requestSingleInstanceLock()) {
  app.exit(0);
} else {
  app.on('second-instance', () => {
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });
  app.on('before-quit', event => {
    event.preventDefault();
    if (quitting) return;
    if (window && !window.isDestroyed()) {
      window.close();
      return;
    }
    quitting = true;
    stopBackend().finally(() => app.exit(backendFailed ? 1 : 0));
  });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(async () => {
    installMenu();
    await startBackend();
    await registerLocalProtocol();
    await createWindow();
    startupComplete = true;
  }).catch(() => fail('墨屿没有成功启动。请确认有足够磁盘空间，并重新打开软件。'));
}
