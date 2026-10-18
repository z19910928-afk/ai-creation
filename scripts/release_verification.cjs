'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const tag = process.env.MOYU_VERIFY_TAG;
if (!tag || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(tag)) throw new Error('Set a new MOYU_VERIFY_TAG for this source snapshot');
const suffix = 'python311-node24-' + tag;
const evidence = path.resolve(root, process.env.MOYU_VERIFY_EVIDENCE || 'docs/audits/2026-10-14/comprehensive/evidence');
const output = path.join(evidence, 'compatibility-' + suffix + '.json');
const logs = path.join(root, 'artifacts/release-verification-' + tag);
if (fs.existsSync(output)) throw new Error('Verification evidence already exists; use a new tag');
if (Number(process.versions.node.split('.')[0]) !== 24) throw new Error('This release check requires Node 24');
const python = process.env.MOYU_VERIFY_PYTHON || 'python3';
const environment = {...process.env, MOYU_TEST_PYTHON: python,
  PATH: [path.dirname(process.execPath), ...(path.isAbsolute(python) ? [path.dirname(python)] : []),
    process.env.PATH || ''].join(path.delimiter),
  MOYU_AI_BASE_URL: '', MOYU_AI_MODEL: '', MOYU_AI_API_KEY: '', MOYU_SECRET_KEY: ''};
const filesIn = (directory, pattern) => fs.readdirSync(path.join(root, directory))
  .filter(name => pattern.test(name)).sort().map(name => path.posix.join(directory, name).replace(/^\.\//, ''));
const pythonFiles = [...filesIn('.', /\.py$/), ...filesIn('scripts', /\.py$/), ...filesIn('tests', /\.py$/),
  ...filesIn('desktop', /\.py$/)];
const javascriptFiles = [...filesIn('.', /\.js$/), ...filesIn('scripts', /\.cjs$/), ...filesIn('tests', /\.cjs$/),
  ...filesIn('desktop', /\.cjs$/)];
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const safeText = text => text.replaceAll(root, '.').replaceAll(process.env.HOME || '\u0000', '<private-home>')
  .replaceAll(os.tmpdir(), '<temporary-root>')
  .replace(/\/(?:private\/)?tmp\/[^"'\\\s]+/g, '<temporary-path>')
  .replace(/\/(?:private\/)?var\/folders\/[^"'\\\s]+/g, '<temporary-path>');
const safeValue = value => {
  if (typeof value === 'string') return safeText(value);
  if (Array.isArray(value)) return value.map(safeValue);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key, safeValue(child)]));
  return value;
};
const fingerprints = () => {
  const currentFiles = [...pythonFiles, ...javascriptFiles, ...filesIn('.', /\.css$/),
    ...filesIn('desktop', /(?:\.spec|\.txt)$/), '.github/workflows/ci.yml',
    '.github/workflows/desktop.yml', 'requirements.txt', 'index.html', 'package.json',
    'package-lock.json', 'electron-builder.yml'];
  return Object.fromEntries(currentFiles.map(file => [file, hash(fs.readFileSync(path.join(root, file)))]));
};
const report = {passed: false, scope: 'Python3.11/Node24 release; macOS arm64; isolated desktop tests and free local providers.',
  started_at: new Date().toISOString(), platform: `${process.platform}/${process.arch}`,
  runtimes: {node: process.version}, source_files_sha256_before: fingerprints(), checks: []};
fs.mkdirSync(logs, {recursive: true});
fs.mkdirSync(evidence, {recursive: true});
const save = () => fs.writeFileSync(output, JSON.stringify(safeValue(report), null, 2) + '\n');

async function run(name, command, arguments_, timeout = 180000, overrides = {}) {
  console.log(JSON.stringify({check: name, status: 'running'}));
  const started = Date.now();
  const result = await new Promise(resolve => {
    let text = '', expired = false, spawnError;
    const child = spawn(command, arguments_, {cwd: root, env: {...environment, ...overrides}, stdio: ['ignore', 'pipe', 'pipe']});
    const timer = setTimeout(() => {expired = true; child.kill('SIGTERM');}, timeout);
    child.stdout.on('data', chunk => text += chunk.toString());
    child.stderr.on('data', chunk => text += chunk.toString());
    child.on('error', error => spawnError = error.message);
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      resolve({code, signal, expired, spawnError, text});
    });
  });
  const logPath = path.join(logs, String(report.checks.length + 1).padStart(2, '0') + '.log');
  fs.writeFileSync(logPath, result.text + (result.spawnError || ''));
  report.checks.push({name, exit_code: result.code, signal: result.signal, timeout: result.expired,
    duration_seconds: Math.round((Date.now() - started) / 10) / 100,
    output_tail: safeText(result.text).slice(-5000), local_log: path.relative(root, logPath),
    passed: result.code === 0 && !result.expired && !result.spawnError});
  save();
  console.log(JSON.stringify({check: name, status: report.checks.at(-1).passed ? 'passed' : 'failed'}));
  return result;
}

function saveArtifact(name, filename, bytes) {
  const artifact = path.join(evidence, filename);
  if (fs.existsSync(artifact)) throw new Error('Evidence already exists: ' + filename);
  fs.writeFileSync(artifact, bytes);
  report.artifacts ||= [];
  report.artifacts.push({name, file: path.relative(root, artifact), sha256: hash(bytes)});
}

function saveStructuredEvidence(name, filename, value) {
  saveArtifact(name, filename, Buffer.from(JSON.stringify(safeValue(value), null, 2) + '\n'));
}

const requiredScreenshots = {
  chapter_transition_browser_regressions: ['chapter-transition-1440x1000.png',
    'chapter-handoff-1280x900.png', 'chapter-transition-new-1280x900.png',
    'chapter-handoff-confirm-1440x1000.png', 'chapter-chat-precise-result-1440x1000.png'],
  agent_chat_browser_regressions: ['agent-chat-1280x900.png', 'agent-chat-1440x1000.png',
    'agent-chat-1920x1080.png', 'agent-chat-results-1280x900.png',
    'agent-chat-results-1440x1000.png', 'agent-chat-results-1920x1080.png'],
  agent_chat_flow_browser_regressions: ['agent-chat-discussion-1440x1000.png',
    'agent-chat-compare-1440x1000.png', 'agent-chat-selection-applied-1440x1000.png',
    'agent-chat-inline-task-1440x1000.png', 'agent-chat-inline-task-1280x900.png'],
  agent_settings_browser_regressions: ['agent-settings-1100.png', 'agent-settings-1280.png',
    'agent-settings-1440.png'],
  knowledge_inbox_browser_regressions: ['knowledge-links-1440x1000.png',
    'knowledge-sources-1280x800.png', 'writing-inbox-1440x1000.png', 'writing-inbox-1280x800.png'],
  editor_preferences_browser_regressions: ['editor-preferences-1440x1000.png',
    'editor-preferences-1280x800.png'],
  writing_agent_browser_regressions: ['writing-agent-plan-1440x1000.png',
    'writing-agent-complete-1440x1000.png', 'writing-agent-1280x900.png',
    'writing-workbench-1440x1000.png', 'writing-workbench-1280x900.png',
    'writing-agent-polish-1280x900.png', 'writing-agent-polish-compare-1280x900.png',
    'writing-agent-overview-confirm-1280x900.png',
    'writing-agent-full-plan-1280x900.png',
    'writing-agent-memory-waiting-1440x1000.png', 'writing-agent-memory-partial-1440x1000.png',
    'writing-agent-memory-rejected-1440x1000.png', 'writing-agent-memory-waiting-1280x900.png',
    'writing-agent-memory-partial-1280x900.png', 'writing-agent-memory-rejected-1280x900.png'],
  analysis_guidance_browser_regressions: ['analysis-guidance-stale-desktop.png',
    'analysis-guidance-stale-1280x900.png'],
  frontend_browser_regressions: ['desktop-editor.png', 'compact-desktop-editor.png', 'desktop-planning.png',
    'desktop-revised-candidate.png', 'desktop-interrupted-fragment.png', 'desktop-interrupted-task.png',
    'desktop-backup-open-failure.png', 'desktop-backup-restored.png', 'desktop-long-title-navigation.png',
    'compact-desktop-long-title-navigation.png', 'short-desktop-long-title-navigation.png',
    'desktop-record-knowledge-scope.png', 'desktop-review-repair.png'],
  planning_browser_regressions: ['planning-second-round.png'],
  model_settings_browser_regressions: ['model-library-1100.png', 'model-library-1280.png',
    'model-library-1440.png', 'model-routing-1100.png', 'model-routing-1280.png', 'model-routing-1440.png'],
  desktop_author_flow_regressions: ['desktop-five-chapter-author.png', 'compact-desktop-five-chapter-author.png',
    'desktop-stale-payoff-blocked.png', 'desktop-final-complete.png'],
  instruction_draft_browser_regressions: ['instruction-draft-desktop.png', 'instruction-draft-desktop-1280x900.png'],
  reference_passages_browser_regressions: ['reference-passages-1440x1000.png', 'reference-passages-1280x900.png',
    'reference-passages-original-1440x1000.png', 'reference-passages-original-1280x900.png'],
  portable_backup_browser_regressions: ['portable-preview-1440x1000.png', 'portable-editor-1440x1000.png',
    'portable-preview-1280x900.png', 'portable-editor-1280x900.png', 'portable-long-title-1280x800.png',
    'portable-saved-reopen.png', 'portable-pending-reloaded.png', 'portable-status-saved.png'],
  author_workspace_lifecycle_browser_regressions: ['planned-next-chapter.png',
    'planned-next-chapter-1280x900.png', 'stale-review-author-explanation.png'],
  export_override_browser_regressions: ['export-override-desktop.png'],
  material_dependency_browser_regressions: ['material-stale-candidate-desktop.png',
    'material-stale-candidate-1280x900.png', 'material-stale-analysis-desktop.png'],
};
const optionalScreenshots = {
  planning_browser_regressions: ['planning-browser-failure.png'],
  desktop_author_flow_regressions: ['desktop-author-flow-failure.png'],
  reference_passages_browser_regressions: ['reference-passages-failure.png'],
  portable_backup_browser_regressions: ['portable-browser-failure.png'],
};
const fixtureReports = {
  'tests/agent_memory_confirmation_http_fixtures.py': {
    environment: 'MOYU_AGENT_MEMORY_REPORT', name: 'agent-memory-confirmation-http',
  },
  'tests/agent_chapter_flow_fixtures.py': {
    environment: 'MOYU_AGENT_CHAPTER_REPORT', name: 'agent-chapter-flow-http',
  },
  'tests/agent_chat_fixtures.py': {
    environment: 'MOYU_AGENT_CHAT_REPORT', name: 'agent-chat-http',
  },
  'tests/portable_backup_fixtures.py': {
    environment: 'MOYU_PORTABLE_BACKUP_REPORT', name: 'portable-backup-helpers',
  },
  'tests/portable_backup_http_fixtures.py': {
    environment: 'MOYU_PORTABLE_HTTP_REPORT', name: 'portable-backup-http',
  },
  'tests/chapter_deletion_dependency_http_fixtures.py': {
    environment: 'MOYU_CHAPTER_DELETION_REPORT', name: 'chapter-deletion-dependencies-http',
  },
  'tests/legacy_deleted_dependency_http_fixtures.py': {
    environment: 'MOYU_LEGACY_DELETED_DEPENDENCY_REPORT', name: 'legacy-deleted-dependencies-http',
  },
  'tests/task_parameter_http_fixtures.py': {
    environment: 'MOYU_TASK_PARAMETER_REPORT', name: 'task-parameters-http',
  },
  'tests/reference_passages_http_fixtures.py': {
    environment: 'MOYU_REFERENCE_PASSAGES_REPORT', name: 'reference-passages-http',
  },
  'tests/reference_passages_integrity_http_fixtures.py': {
    environment: 'MOYU_REFERENCE_INTEGRITY_REPORT', name: 'reference-passages-integrity-http',
  },
};

async function runFixture(script) {
  const evidenceType = fixtureReports[script];
  const localReport = evidenceType && path.join(logs, evidenceType.name + '.json');
  const overrides = evidenceType ? {[evidenceType.environment]: localReport} : {};
  await run('Python fixtures: ' + script, python, [script], 180000, overrides);
  if (!evidenceType) return;
  const check = report.checks.at(-1);
  if (!fs.existsSync(localReport)) {
    check.passed = false;
    check.missing_structured_fixture_result = true;
  } else {
    const value = JSON.parse(fs.readFileSync(localReport, 'utf8'));
    saveStructuredEvidence(evidenceType.name, evidenceType.name + '-' + suffix + '.json', value);
    check.fixture_count = value.checks ?? value.tests ?? value.methods;
    if (value.passed !== true || value.source_unchanged !== true) {
      check.passed = false;
      check.structured_fixture_result_failed = true;
    }
  }
  save();
}

function structuredBrowserResult(text) {
  const starts = [...text.matchAll(/(?:^|\n)(?=\{)/g)].map(match =>
    match.index + (match[0] === '\n' ? 1 : 0));
  for (const start of starts.reverse()) {
    try {
      const value = JSON.parse(text.slice(start));
      if (value && typeof value.passed === 'boolean') return value;
    } catch {}
  }
  return null;
}

async function runBrowser(script) {
  const name = path.basename(script, '.cjs');
  const portableDirectory = name === 'portable_backup_browser_regressions' ? path.join(logs, name) : null;
  const portableReport = portableDirectory && path.join(portableDirectory, 'results.json');
  const overrides = portableDirectory ? {
    MOYU_PORTABLE_BROWSER_REPORT: portableReport,
    MOYU_PORTABLE_BROWSER_SCREENSHOT_DIR: portableDirectory,
  } : {};
  const browser = await run('Browser: ' + name, process.execPath, [script], 300000, overrides);
  const browserResult = structuredBrowserResult(browser.text);
  if (portableReport) {
    const check = report.checks.at(-1);
    if (!fs.existsSync(portableReport)) {
      check.passed = false;
      check.missing_structured_browser_report = true;
    } else {
      const savedResult = JSON.parse(fs.readFileSync(portableReport, 'utf8'));
      if (!browserResult || JSON.stringify(savedResult) !== JSON.stringify(browserResult)) {
        check.passed = false;
        check.structured_browser_output_mismatch = true;
      }
    }
  }
  if (browserResult) {
    const localArtifacts = portableDirectory || browserResult.artifacts;
    browserResult.artifacts = 'Private temporary DB and logs retained locally; screenshots are listed in this report.';
    saveStructuredEvidence(name, name + '-' + suffix + '.json', browserResult);
    report.checks.at(-1).scenario_count = Array.isArray(browserResult.cases) ? browserResult.cases.length : 0;
    if (browserResult.passed !== true || browserResult.source_unchanged === false) {
      report.checks.at(-1).passed = false;
      report.checks.at(-1).structured_browser_result_failed = true;
    }
    for (const filename of [...(requiredScreenshots[name] || []), ...(optionalScreenshots[name] || [])]) {
      const source = localArtifacts && path.join(localArtifacts, filename);
      if (source && fs.existsSync(source)) {
        saveArtifact(name, tag + '-' + filename, fs.readFileSync(source));
      } else if ((requiredScreenshots[name] || []).includes(filename)) {
        report.checks.at(-1).passed = false;
        (report.checks.at(-1).missing_screenshots ||= []).push(filename);
      }
    }
  } else {
    report.checks.at(-1).passed = false;
    report.checks.at(-1).missing_structured_browser_result = true;
  }
  save();
}

async function main() {
  const version = await run('Python runtime', python, ['--version']);
  report.runtimes.python = version.text.trim();
  if (!/^Python 3\.11\./.test(report.runtimes.python)) throw new Error('This release check requires Python 3.11');
  await run('Python syntax', python, ['-m', 'py_compile', ...pythonFiles]);
  for (const file of javascriptFiles) await run('JavaScript syntax: ' + file, process.execPath, ['--check', file]);
  for (const file of filesIn('tests', /_fixtures\.py$/)) await runFixture(file);
  const fixedOutput = path.join(logs, 'fixed-evaluation.json');
  await run('Fixed evaluation', python, ['scripts/evaluation_runner.py', '--fixtures-dir', 'tests/fixtures', '--output', fixedOutput]);
  if (fs.existsSync(fixedOutput)) {
    saveStructuredEvidence('Fixed evaluation', 'fixed-evaluation-' + suffix + '.json', JSON.parse(fs.readFileSync(fixedOutput, 'utf8')));
  }
  await run('Editor merge regressions', process.execPath, ['tests/editor_sync_fixtures.cjs']);
  for (const script of filesIn('tests', /(?:_browser_regressions|desktop_author_flow_regressions)\.cjs$/)) await runBrowser(script);
  report.source_files_sha256_after = fingerprints();
  report.source_unchanged = JSON.stringify(report.source_files_sha256_before) === JSON.stringify(report.source_files_sha256_after);
  report.finished_at = new Date().toISOString();
  report.desktop_scenario_count = report.checks.reduce((count, check) => count + (check.scenario_count || 0), 0);
  report.passed = report.source_unchanged && report.checks.every(check => check.passed);
  save();
  console.log(JSON.stringify({passed: report.passed, checks: report.checks.length,
    source_unchanged: report.source_unchanged, report: path.relative(root, output)}));
  if (!report.passed) process.exitCode = 1;
}

main().catch(error => {
  report.fatal_error = safeText(error.message);
  report.finished_at = new Date().toISOString();
  save();
  console.error(report.fatal_error);
  process.exitCode = 1;
});
