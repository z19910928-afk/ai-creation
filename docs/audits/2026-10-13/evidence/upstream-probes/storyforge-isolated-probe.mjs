import fs from 'node:fs'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { stripTypeScriptTypes } from 'node:module'

const upstream = './.audit-cache/2026-10-13/references/storyforge'
const statisticsSource = fs.readFileSync(`${upstream}/src/lib/evals/long-consistency/h4-statistics.ts`, 'utf8')
const statistics = await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(statisticsSource)).toString('base64')}`)
const interval = statistics.wilsonInterval95V1(9, 10)
assert.equal(interval.samples, 10)
assert.equal(interval.successes, 9)
assert.equal(interval.estimate, 0.9)
assert.ok(interval.lower < 0.9 && interval.upper > 0.9)
assert.throws(() => statistics.wilsonInterval95V1(11, 10))
assert.throws(() => statistics.wilsonInterval95V1(-1, 10))
console.log(JSON.stringify({ component: 'storyforge-h4-statistics', tests: 6, passed: true, interval }))

const app = fs.readFileSync('./app.js', 'utf8')
const source = app.slice(app.indexOf('actions.startWriting='), app.indexOf('actions.continueWriting='))
assert.ok(source.startsWith('actions.startWriting='))
const state = {
  project: { title: '已有作品', summary: '已有设定' },
  chapters: [{ id: 'existing_chapter', title: '已有第一章', body: '作者已经写下且需要保留的正文', status: '完成' }],
  records: [],
  plot: { setup: [] },
}
const inputs = { '#wTitle': '新故事', '#wPremise': '新设定', '#wGenre': '悬疑', '#wHero': '新主角', '#wConflict': '新阻力' }
const context = vm.createContext({
  state,
  actions: {},
  crypto: { randomUUID: () => 'isolated_id' },
  localStorage: { setItem: () => {} },
  ACTIVE_KEY: 'isolated_active',
  toast: () => {},
  modal: (_title, _body, commit) => commit({ querySelector: selector => ({ value: inputs[selector] }) }),
})
vm.runInContext(source, context)
context.actions.startWriting()
assert.equal(state.chapters.length, 1)
assert.equal(state.chapters[0].id, 'existing_chapter')
assert.equal(state.project.title, '新故事')
assert.ok(!state.chapters[0].body.includes('需要保留的正文'))
console.log(JSON.stringify({
  component: 'moyu-startWriting',
  source: 'app.js:250',
  reproduced: '新故事保留同一首章id并覆盖已有作品信息与正文',
  isolated: true,
  networkCalls: 0,
}))

const adoptionSource = app.slice(app.indexOf('async function adoptAI()'), app.indexOf('async function clearAI()'))
const generationSource = app.slice(app.indexOf('async function generateAI()'), app.indexOf('async function aiPromptContract()'))
const twoChapterState = {
  chapters: [{ id: 'chapter_a', body: '甲章原文' }, { id: 'chapter_b', body: '乙章原文' }],
}
let resolveResponse
let resolveDispatched
const dispatched = new Promise(resolve => { resolveDispatched = resolve })
const elements = {
  '#aiTask': { value: 'continue' },
  '#aiInstruction': { value: '' },
  '#aiOutput': { value: '' },
  '#aiStatus': { textContent: '' },
  '#aiAdopt': { disabled: true },
  '#aiCopy': { disabled: true },
}
const adoptionContext = vm.createContext({
  state: twoChapterState,
  aiDraft: null,
  aiBusy: false,
  aiRunId: null,
  backendOnline: true,
  activeChapter: 'chapter_a',
  API_BASE: '/api',
  crypto: { randomUUID: () => 'isolated_run' },
  document: { querySelector: selector => elements[selector] },
  chapter: () => twoChapterState.chapters.find(value => value.id === adoptionContext.activeChapter),
  syncBackend: async () => {},
  materialControls: () => ({}),
  retrievalProfile: () => ({}),
  fetch: async (_url, options) => {
    assert.equal(JSON.parse(options.body).chapter_id, 'chapter_a')
    resolveDispatched()
    return await new Promise(resolve => { resolveResponse = resolve })
  },
  recordAIDecision: async () => ({ audit: 'test' }),
  confirm: () => true,
  save: () => {},
  render: () => {},
  renderAIProvenance: () => {},
  toast: () => {},
})
vm.runInContext(generationSource + adoptionSource, adoptionContext)
const generation = adoptionContext.generateAI()
await dispatched
vm.runInContext("if(activeChapter!=='chapter_b')aiDraft=null;activeChapter='chapter_b';render()", adoptionContext)
resolveResponse({
  ok: true,
  json: async () => ({ mode: 'provider', model: 'mock', text: '这是根据甲章生成的候选', ai_run_id: 'mock_run', context: { current_chapter: { id: 'chapter_a' } } }),
})
await generation
await adoptionContext.adoptAI()
assert.equal(twoChapterState.chapters[0].body, '甲章原文')
assert.ok(twoChapterState.chapters[1].body.includes('根据甲章生成的候选'))
console.log(JSON.stringify({
  component: 'moyu-adoptAI',
  source: 'app.js:71-73',
  reproduced: '甲章请求等待时切换乙章并清空候选；甲章响应随后重新建立候选，采纳写入乙章',
  isolated: true,
  networkCalls: 0,
}))
