import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const storage = new Map()
globalThis.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null },
  setItem(key, value) { storage.set(key, String(value)) },
  removeItem(key) { storage.delete(key) },
}

const source = './.audit-cache/2026-10-13/references/NovelForge/frontend/src/renderer/src/services/versionService.ts'
const module = await import(pathToFileURL(source).href)
for (let index = 1; index <= 25; index += 1) {
  module.addVersion(7, {
    cardId: 11,
    projectId: 7,
    title: `version-${index}`,
    content: { body: `body-${index}` },
  })
}
const retained = module.listVersions(7, 11)
assert.equal(retained.length, 20)
assert.equal(retained[0].title, 'version-25')
assert.equal(retained[19].title, 'version-6')
assert.deepEqual(module.listVersions(8, 11), [])
assert.deepEqual(module.listVersions(7, 12), [])

const persisted = storage.get('nf:v1:versions:7')
assert.ok(persisted)
storage.clear()
assert.deepEqual(module.listVersions(7, 11), [])
storage.set('nf:v1:versions:7', persisted)
assert.equal(module.listVersions(7, 11).length, 20)
storage.set('nf:v1:versions:7', '{broken-json')
assert.deepEqual(module.listVersions(7, 11), [])

const result = {
  source,
  execution: 'Original TypeScript module imported by Node with built-in type stripping; localStorage simulated in memory',
  requests: 0,
  writes_to_product: 0,
  added_versions: 25,
  retained_versions: retained.length,
  latest_title: retained[0].title,
  oldest_title: retained[19].title,
  checked_project_and_card_isolation: true,
  clearing_storage_removes_history: true,
  restoring_storage_recovers_20_items: true,
  malformed_storage_returns_empty: true,
}
const destination = './.audit-cache/2026-10-13/cross-storyforge-version-probe-result.json'
await writeFile(destination, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify(result, null, 2))
