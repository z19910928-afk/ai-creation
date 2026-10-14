'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const auditOption = process.argv.indexOf('--audit-dir');
const auditRelative = auditOption < 0 ? 'docs/audits/2026-10-13' : process.argv[auditOption + 1];
if (!auditRelative) throw new Error('--audit-dir needs a directory');
const audit = path.resolve(root, auditRelative);
const auditScope = path.relative(path.join(root, 'docs/audits'), audit);
if (!auditScope || auditScope.startsWith('..') || path.isAbsolute(auditScope)) {
  throw new Error('Only public docs/audits subdirectories can be published');
}
const privateRelative = path.posix.join('artifacts/private-audit-originals', auditScope.split(path.sep).join('/'));
const privateRoot = path.join(root, privateRelative);
const manifestPath = path.join(audit, 'evidence/archive-redactions.json');
const apply = process.argv.includes('--apply');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const home = os.homedir();
const runtime = path.join(home, '.cache/codex-runtimes/codex-primary-runtime/dependencies');
const replacements = [
  [path.join(runtime, 'python/bin/python3'), 'python3'],
  [path.join(runtime, 'node/bin/node'), 'node'],
  [path.join(runtime, 'node/node_modules/playwright'), '/path/to/node_modules/playwright'],
  [root, '.'],
  ['/tmp/codex-ai-writing-audit-20261013', './.audit-cache/2026-10-13'],
  ['/tmp/codex-neural-network-ai-creation-20261013', './.audit-cache/moyu-baseline'],
];
const email = /\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}\b/g;
const macTemporaryPrefix = /(?:\/private)?\/var\/folders\/[^"'\\\s]+?\/T\//g;
const macTemporaryAny = /(?:\/private)?\/var\/folders\/[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*/g;

function collect(directory) {
  return fs.readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
    if (entry.isSymbolicLink() || entry.name.startsWith('literary-')) return [];
    const location = path.join(directory, entry.name);
    if (entry.isDirectory()) return collect(location);
    if (!/\.(?:md|json|py|cjs|mjs)$/.test(entry.name)) return [];
    if (location === manifestPath || entry.name === 'archive-privacy.md') return [];
    return [location];
  });
}

function redactText(value, categories) {
  let result = value;
  for (const [original, replacement] of replacements) {
    if (result.includes(original)) {
      result = result.replaceAll(original, replacement);
      categories.add('local_absolute_path');
    }
  }
  const withPortableMacTemp = result.replace(macTemporaryPrefix, './.audit-cache/runtime-artifacts/');
  if (withPortableMacTemp !== result) {
    result = withPortableMacTemp;
    categories.add('local_macos_temp_path');
  }
  const withoutMacTemp = result.replace(macTemporaryAny, '<private-macos-temp>');
  if (withoutMacTemp !== result) {
    result = withoutMacTemp;
    categories.add('local_macos_temp_path');
  }
  if (result.includes(home + '/')) {
    result = result.replaceAll(home + '/', '<private-home>/');
    categories.add('local_home_path');
  }
  const withoutTemporaryRuntime = result.replace(/\/(?:private\/)?tmp\/[^"'\\\s]+/g, '<private-temporary-path>');
  if (withoutTemporaryRuntime !== result) {
    result = withoutTemporaryRuntime;
    categories.add('local_temporary_runtime_path');
  }
  return result;
}

function redactMetadata(value, categories, parentKey = '') {
  if (Array.isArray(value)) return value.map(item => redactMetadata(item, categories, parentKey));
  if (value && typeof value === 'object') {
    const result = {};
    for (const [key, child] of Object.entries(value)) {
      if (key === 'email' && typeof child === 'string' && child && child !== '[redacted-email]') {
        result[key] = '[redacted-email]';
        categories.add('personal_email');
      } else {
        result[key] = redactMetadata(child, categories, key);
      }
    }
    if (parentKey === 'verification' && typeof value.payload === 'string' &&
        result.payload !== value.payload) {
      result.original_verified = Object.hasOwn(value, 'original_verified') ? value.original_verified : value.verified;
      result.original_reason = Object.hasOwn(value, 'original_reason') ? value.original_reason : value.reason;
      result.verified = false;
      result.reason = 'public_archive_redacted';
      result.signature = null;
      categories.add('signed_payload_not_publicly_verifiable');
    }
    return result;
  }
  if (typeof value !== 'string') return value;
  let result = redactText(value, categories);
  if (parentKey === 'payload' || parentKey === 'message') {
    result = result.replace(email, () => {
      categories.add('personal_email');
      return '[redacted-email]';
    });
  }
  return result;
}

const prior = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : null;
const records = new Map((prior?.files || []).map(item => [item.path, item]));
const changed = [];
const refreshed = [];
for (const location of collect(audit)) {
  const current = fs.readFileSync(location);
  const relative = path.relative(audit, location).split(path.sep).join('/');
  const previous = records.get(relative);
  const privateLocation = path.join(privateRoot, relative);
  const privateExists = fs.existsSync(privateLocation);
  let original = current;
  if (privateExists) {
    original = fs.readFileSync(privateLocation);
    if (previous ? previous.original_sha256 !== sha(original) : !original.equals(current)) {
      throw new Error(`Private original differs: ${relative}; verify the retained original before publishing.`);
    }
  } else if (previous) {
    throw new Error(`Private original is missing: ${relative}; recover it before publishing.`);
  }
  const categories = new Set();
  let published;
  if (location.endsWith('.json')) {
    const data = redactMetadata(JSON.parse(current.toString('utf8')), categories);
    if (relative.startsWith('evidence/source-metadata/') && categories.size) {
      data.archive_redaction = {
        original_artifact_sha256: sha(original),
        categories: [...new Set([...(previous?.categories || []), ...categories])].sort(),
        commit_ids_unchanged: true,
        public_bytes_are_original: false,
      };
    }
    published = categories.size ? JSON.stringify(data, null, 2) + '\n' : current.toString('utf8');
  } else {
    published = redactText(current.toString('utf8'), categories);
  }
  const output = Buffer.from(published);
  if (output.equals(current)) {
    if (previous && previous.published_sha256 !== sha(output)) {
      records.set(relative, {...previous, published_sha256: sha(output),
        editorial_updates_after_redaction: true});
      refreshed.push(relative);
    }
    continue;
  }
  const record = {
    ...previous,
    path: relative,
    original_sha256: sha(original),
    published_sha256: sha(output),
    categories: [...new Set([...(previous?.categories || []), ...categories])].sort(),
  };
  if (previous && previous.published_sha256 !== sha(current)) record.editorial_updates_after_redaction = true;
  changed.push(record);
  records.set(relative, record);
  if (apply) {
    fs.mkdirSync(path.dirname(privateLocation), {recursive: true, mode: 0o700});
    if (!privateExists) {
      fs.writeFileSync(privateLocation, original, {flag: 'wx', mode: 0o600});
    }
    fs.writeFileSync(location, output);
  }
}

const manifest = {
  scope: 'Public audit archive only; literary samples, frozen requests, ledgers, and their hashes are unchanged.',
  private_originals: privateRelative + '/ (Git ignored)',
  originals_are_required_for_byte_or_signature_verification: true,
  files: [...records.values()].sort((left, right) => left.path.localeCompare(right.path)),
};
if (apply) fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify({
  applied: apply,
  changed: changed.length,
  files: changed.map(item => item.path),
  manifest_refreshed: refreshed.length,
  manifest_files: refreshed,
}, null, 2));
