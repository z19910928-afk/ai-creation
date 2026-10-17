'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const runtime = require('../desktop/runtime.cjs');

test('only the private app origin can reach the local proxy', () => {
  for (const url of ['https://example.com/', 'http://127.0.0.1:4174/',
    'moyu://app.evil/', 'moyu://user:password@app/', 'moyu://app:8080/', 'file:///tmp/a']) {
    assert.equal(runtime.isAppURL(url), false);
    assert.throws(() => runtime.proxyURL(url, 1234));
  }
  assert.equal(runtime.proxyURL('moyu://app/api/state?name=one', 1234),
    'http://127.0.0.1:1234/api/state?name=one');
  assert.equal(runtime.isDownloadURL('blob:moyu://app/id'), true);
  assert.equal(runtime.isDownloadURL('blob:https://example.com/id'), false);
});

test('the main process replaces untrusted proxy and credential headers', () => {
  const headers = runtime.proxyHeaders({'Origin': 'https://example.com',
    'Host': 'evil.invalid', 'Authorization': 'Bearer fixture-only',
    'X-Moyu-Desktop-Token': 'fixture-wrong', 'X-Moyu-Project': 'project-test',
    'X-Forwarded-Proto': 'https'}, 1234, 'fixture-desktop-token');
  assert.equal(headers.get('Origin'), 'http://127.0.0.1:1234');
  assert.equal(headers.get('Host'), null);
  assert.equal(headers.get('Authorization'), null);
  assert.equal(headers.get('X-Forwarded-Proto'), null);
  assert.equal(headers.get('X-Moyu-Desktop-Token'), 'fixture-desktop-token');
  assert.equal(headers.get('X-Moyu-Project'), 'project-test');
});

test('desktop processes never inherit a developer connection or Python injection', () => {
  const inherited = {PATH: '/fixture/bin', MOYU_AI_API_KEY: 'fixture-local-key',
    MOYU_DB: '/fixture/private.db', PYTHONPATH: '/fixture/python',
    NODE_OPTIONS: '--require=fixture', MOYU_DESKTOP_TOKEN: 'fixture-old'};
  const env = runtime.backendEnvironment(inherited, '/fixture/data', 'fixture-current');
  assert.equal(env.PATH, inherited.PATH);
  assert.equal(env.MOYU_AI_API_KEY, undefined);
  assert.equal(env.MOYU_DB, undefined);
  assert.equal(env.PYTHONPATH, undefined);
  assert.equal(env.NODE_OPTIONS, undefined);
  assert.equal(env.MOYU_DESKTOP_TOKEN, 'fixture-current');
  assert.equal(env.MOYU_DESKTOP_DATA_DIR, '/fixture/data');
  assert.equal(env.MOYU_DESKTOP_PORT, '0');
  assert.equal(env.MOYU_DESKTOP_PARENT_PIPE, '1');
  assert.equal(inherited.MOYU_DB, '/fixture/private.db');
});

test('packaged commands use the bundled backend on both platforms', () => {
  for (const platform of ['darwin', 'win32']) {
    const result = runtime.backendCommand({packaged: true, resourcesPath: '/fixture/resources',
      root: '/fixture/app', platform});
    assert.match(result.file, new RegExp(platform === 'win32' ? 'moyu-service\\.exe$' : 'moyu-service$'));
    assert.deepEqual(result.args, []);
  }
});

test('a readiness message belongs to the spawned process and an actual port', () => {
  assert.equal(runtime.readyMessage('not json', 42), null);
  assert.equal(runtime.readyMessage('{"type":"moyu-ready","port":1234,"pid":41}', 42), null);
  assert.equal(runtime.readyMessage('{"type":"moyu-ready","port":0,"pid":42}', 42), null);
  assert.deepEqual(runtime.readyMessage('{"type":"moyu-ready","port":1234,"pid":42}', 42),
    {type: 'moyu-ready', port: 1234, pid: 42});
  assert.notEqual(runtime.newToken(), runtime.newToken());
  assert.match(runtime.newToken(), /^[a-f0-9]{64}$/);
});
