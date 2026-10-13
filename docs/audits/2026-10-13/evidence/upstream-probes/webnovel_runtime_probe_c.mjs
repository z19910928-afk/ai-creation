import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const root = './.audit-cache/2026-10-13/references/webnovel-writer-v8';
const loader = path.join(root, 'scripts/strip-types-loader.mjs');
const moduleUrl = pathToFileURL(path.join(root, 'packages/core/src/repo/atomic.ts')).href;
const atomic = await import(moduleUrl);
const transaction = await import(pathToFileURL(path.join(root, 'packages/core/src/repo/transaction.ts')).href);
const probeResults = {};
const roots = [];
const makeRoot = () => {
  const value = fs.mkdtempSync(path.join(os.tmpdir(), 'webnovel-audit-c-'));
  roots.push(value);
  return value;
};
const ops = [
  { relPath: 'body/a.md', content: 'new-a' },
  { relPath: 'body/b.md', content: 'new-b' },
  { relPath: 'body/c.md', content: 'new-c' },
];
const childSource = `
import fs from 'node:fs';
import path from 'node:path';
import { syncBuiltinESMExports } from 'node:module';
const [url, root, rawOps] = process.argv.slice(1);
const realRename = fs.renameSync;
let applied = 0;
fs.renameSync = (from, to) => {
  const result = realRename(from, to);
  if (path.basename(String(from)).startsWith('.webnovel-txn-') && ++applied === 1) process.exit(88);
  return result;
};
syncBuiltinESMExports();
const { writeBatchAtomic } = await import(url);
writeBatchAtomic(root, JSON.parse(rawOps), { provenance: { operationId: 'audit-crash' } });
`;
function crashFixture() {
  const value = makeRoot();
  fs.mkdirSync(path.join(value, 'body'));
  fs.writeFileSync(path.join(value, 'body/a.md'), 'old-a');
  fs.writeFileSync(path.join(value, 'body/b.md'), 'old-b');
  const child = spawnSync(process.execPath, ['--experimental-transform-types', '--loader', loader, '--input-type=module', '-e', childSource, moduleUrl, value, JSON.stringify(ops)], { encoding: 'utf8', timeout: 8000 });
  if (child.status !== 88) throw new Error(`Expected injected exit88; got ${child.status}: ${child.stderr}`);
  return value;
}

try {
  const value = crashFixture();
  const before = fs.readFileSync(path.join(value, 'body/a.md'), 'utf8');
  atomic.withBookWrite(value, () => {});
  probeResults.real_process_crash_recovery = {
    before_recovery_a: before,
    recovered_a: fs.readFileSync(path.join(value, 'body/a.md'), 'utf8'),
    recovered_b: fs.readFileSync(path.join(value, 'body/b.md'), 'utf8'),
    unintended_c_exists: fs.existsSync(path.join(value, 'body/c.md')),
    transaction_count_after: fs.readdirSync(path.join(value, '.webnovel/transactions')).length,
  };

  const conflict = crashFixture();
  fs.writeFileSync(path.join(conflict, 'body/a.md'), 'author-edit');
  let recoveryError = '';
  try { atomic.withBookWrite(conflict, () => {}); } catch (error) { recoveryError = error.message; }
  let overwriteError = '';
  try { atomic.writeFileAtomic(conflict, 'body/a.md', 'overwrite'); } catch (error) { overwriteError = error.message; }
  atomic.writeFileAtomic(conflict, 'body/other.md', 'unrelated');
  probeResults.conflict_quarantine = {
    recovery_blocked: /恢复冲突/.test(recoveryError),
    target_overwrite_blocked: /隔离/.test(overwriteError),
    author_edit_preserved: fs.readFileSync(path.join(conflict, 'body/a.md'), 'utf8') === 'author-edit',
    quarantine_records: transaction.listQuarantined(conflict).length,
    unrelated_write_succeeded: fs.readFileSync(path.join(conflict, 'body/other.md'), 'utf8') === 'unrelated',
  };

  const appSource = fs.readFileSync('./.audit-cache/moyu-baseline/app.js', 'utf8');
  const normalizer = appSource.split('\n').find(line => line.startsWith('function normalizeState'));
  const safeId = appSource.split('\n').find(line => line.startsWith('function safeId'));
  const validState = { project: { title: '探针', summary: '', genre: '', goal: 5000 }, chapters: [{ id: 'ch1', title: '一', body: '原稿', status: '草稿' }], records: [], memory: [{ id: 'm1', name: '角色', status: '已拒绝', confidence: '候选' }], plot: { setup: [], develop: [], payoff: [] } };
  const normalizeContext = vm.createContext({ probe: validState });
  vm.runInContext(`${safeId}\n${normalizer}\nresult=normalizeState(probe)`, normalizeContext);
  probeResults.rejection_normalizer_actual = { normalized_rejected_state_is_null: normalizeContext.result === null };

  const syncBackend = appSource.split('\n').find(line => line.startsWith('async function syncBackend'));
  let firstResolve;
  let called = 0;
  const versions = [];
  const syncContext = vm.createContext({
    API_BASE: '/api', KEY: 'audit', DIRTY_KEY: 'dirty',
    state: { project: { title: '探针' }, chapters: [], version: 1, head_revision: 'r1' },
    backendOnline: true, syncInFlight: false, syncQueued: false, mutationSeq: 0,
    console: { info: () => {} }, localStorage: { setItem: () => {}, removeItem: () => {} },
    fetch: async (_url, options) => {
      versions.push(JSON.parse(options.body).version);
      if (++called === 1) return new Promise(resolve => { firstResolve = resolve; });
      return { ok: false, status: 409, json: async () => ({ error: 'state version conflict', current_version: 2 }) };
    },
  });
  vm.runInContext(`${syncBackend}\nfirst=syncBackend()`, syncContext);
  syncContext.mutationSeq = 1;
  syncContext.state.project.title = '继续打字';
  firstResolve({ ok: true, json: async () => ({ project: { title: '探针' }, chapters: [], memory: [], version: 2, head_revision: 'r2' }) });
  await syncContext.first;
  await new Promise(resolve => setTimeout(resolve, 10));
  probeResults.inflight_edit_token_conflict_actual = {
    transmitted_versions: versions,
    current_local_version: syncContext.state.version,
    backend_marked_offline: syncContext.backendOnline === false,
    typed_text_retained_locally: syncContext.state.project.title === '继续打字',
  };
  const artifact = './.audit-cache/2026-10-13/webnovel-runtime-probe-c.json';
  fs.writeFileSync(artifact, JSON.stringify(probeResults, null, 2));
  console.log(JSON.stringify(probeResults, null, 2));
} finally {
  for (const value of roots.reverse()) fs.rmSync(value, { recursive: true, force: true });
}
