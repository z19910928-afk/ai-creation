'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const APP_ORIGIN = 'moyu://app';
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

function isAppURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'moyu:' && url.hostname === 'app' &&
      !url.username && !url.password && !url.port;
  } catch {
    return false;
  }
}

function isDownloadURL(value) {
  return isAppURL(value) || value.startsWith('blob:' + APP_ORIGIN + '/');
}

function proxyURL(value, port) {
  if (!isAppURL(value) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('INVALID_LOCAL_REQUEST');
  }
  const url = new URL(value);
  return 'http://127.0.0.1:' + port + url.pathname + url.search;
}

function proxyHeaders(source, port, token) {
  const headers = new Headers(source);
  for (const key of ['host', 'origin', 'referer', 'authorization', 'cookie',
    'x-moyu-desktop-token', 'x-forwarded-host', 'x-forwarded-proto']) {
    headers.delete(key);
  }
  const origin = 'http://127.0.0.1:' + port;
  headers.set('Origin', origin);
  headers.set('Referer', origin + '/');
  headers.set('X-Moyu-Desktop-Token', token);
  return headers;
}

function backendEnvironment(inherited, dataDirectory, token) {
  const env = {...inherited};
  for (const key of Object.keys(env)) {
    if (/^MOYU_/i.test(key) || /^(PYTHONHOME|PYTHONPATH|NODE_OPTIONS|ELECTRON_RUN_AS_NODE)$/i.test(key)) {
      delete env[key];
    }
  }
  return {...env, PYTHONNOUSERSITE: '1', PYTHONUNBUFFERED: '1',
    MOYU_DESKTOP_DATA_DIR: dataDirectory, MOYU_DESKTOP_TOKEN: token,
    MOYU_DESKTOP_PORT: '0', MOYU_DESKTOP_PARENT_PIPE: '1'};
}

function smokeDataDirectory(input) {
  if (!input || !path.isAbsolute(input)) throw new Error('SMOKE_DATA_DIRECTORY_REQUIRED');
  const directory = fs.realpathSync(input);
  const temporary = fs.realpathSync(os.tmpdir());
  const relative = path.relative(temporary, directory);
  const parts = relative.split(path.sep);
  if (parts.length !== 2 || !/^moyu-desktop-smoke-[A-Za-z0-9_-]+$/.test(parts[0]) ||
      parts[1] !== 'data' || !fs.statSync(directory).isDirectory()) {
    throw new Error('SMOKE_DATA_DIRECTORY_MUST_BE_TEMPORARY');
  }
  return directory;
}

function backendCommand({packaged, resourcesPath, root, python, platform = process.platform}) {
  if (packaged) {
    return {file: path.join(resourcesPath, 'backend',
      platform === 'win32' ? 'moyu-service.exe' : 'moyu-service'), args: []};
  }
  if (!python) throw new Error('DEVELOPMENT_PYTHON_REQUIRED');
  return {file: python, args: [path.join(root, 'desktop', 'service_entry.py')]};
}

function readyMessage(line, expectedPid) {
  let value;
  try {value = JSON.parse(line);} catch {return null;}
  if (value?.type !== 'moyu-ready' || value.pid !== expectedPid ||
      !Number.isInteger(value.port) || value.port < 1 || value.port > 65535) return null;
  return value;
}

function newToken() {
  return crypto.randomBytes(32).toString('hex');
}

module.exports = {APP_ORIGIN, CSP, isAppURL, isDownloadURL, proxyURL, proxyHeaders,
  backendEnvironment, smokeDataDirectory, backendCommand, readyMessage, newToken};
