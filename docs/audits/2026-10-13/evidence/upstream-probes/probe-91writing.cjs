const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const base = './.audit-cache/2026-10-13/references/91Writing';
const writer = fs.readFileSync(base + '/src/views/Writer.vue', 'utf8');
const backup = fs.readFileSync(base + '/src/components/BackupManager.vue', 'utf8');
const service = fs.readFileSync(base + '/src/services/api.js', 'utf8');
const results = [];
const silent = {log(){}, warn(){}, error(){}};
const msg = {success(){}, info(){}, warning(){}, error(){}};
const ref = value => ({value});
function region(source, start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a);
  return source.slice(a, b);
}
function storage(initial = {}) {
  const data = {...initial};
  return {data, getItem:k => data[k] ?? null, setItem:(k,v) => data[k] = v};
}
async function main() {
  const localStorage = storage();
  const config = {openai:{apiKey:'sandbox-sentinel',baseURL:'https://sandbox.invalid/v1',selectedModel:'sandbox',maxTokens:2000000,temperature:0.7},proxy:{}};
  let sentBody;
  const billing = {estimateTokens:x=>x.length,recordAPICall(){}};
  const ctx = vm.createContext({console:silent,localStorage,apiConfig:config,billingService:billing,ElMessage:msg,AbortSignal,TextDecoder,setTimeout,clearTimeout,
    fetch:async (_url, options) => {
      sentBody = JSON.parse(options.body);
      let done = false;
      return {ok:true,status:200,body:{getReader:()=>({read:async()=>{
        if(done)return {done:true};
        done=true;
        return {done:false,value:new TextEncoder().encode('data: {"choices":[{"delta":{"content":"离线模拟正文"}}]}\n\ndata: [DONE]\n\n')};
      },releaseLock(){}})}};
    }});
  vm.runInContext(service.replace(/^import .*$/gm,'').replace('export default new APIService()','globalThis.api = new APIService()'),ctx);
  await ctx.api.generateTextStream('上文\n下文',{maxTokens:null,temperature:0});
  results.push({case:'AI请求参数',scope:'R-隔离服务组件，fetch全部模拟',prompt:sentBody.messages[0].content,max_tokens:sentBody.max_tokens,temperature:sentBody.temperature,newline_removed:sentBody.messages[0].content==='上文下文',null_limit_fell_back:sentBody.max_tokens===2000000,zero_temperature_ignored:sentBody.temperature===0.7});

  const backupStorage = storage();
  const backupCtx = vm.createContext({localStorage:backupStorage,backups:ref([{id:1,data:{novel:'正文'},size:6}])});
  vm.runInContext(region(backup,'const saveBackups = () => {','const loadBackups = () => {') + '\nsaveBackups()',backupCtx);
  const saved = JSON.parse(backupStorage.getItem('backup_list'));
  assert.equal(saved[0].data,undefined);
  results.push({case:'旧备份组件刷新后数据消失',scope:'R-隔离函数，组件作者入口不可达',saved_metadata_only:true,payload_persisted:false});

  const initStorage = storage({novels:JSON.stringify([{id:1,title:'作品',chapterList:[{id:11,title:'章',content:'原文'}],characters:[{name:'人物A'}],worldSettings:[{title:'世界A'}],corpusData:[{title:'语料A'}],events:[{title:'事件A'}]}])});
  const initCtx = vm.createContext({localStorage:initStorage,route:{query:{novelId:'1'}},currentNovel:ref(null),chapters:ref([]),currentChapter:ref(null),characters:ref([]),novelStore:{worldSettings:[]},corpusData:ref([]),events:ref([]),selectChapter(){},ElMessage:msg,router:{push(){}}});
  vm.runInContext(region(writer,'const saveNovelData = () => {','// 生命周期') + '\ninitNovel()',initCtx);
  const initializedSaved = JSON.parse(initStorage.getItem('novels'))[0];
  results.push({case:'打开作品时先保存后加载材料',scope:'R-隔离初始化函数，未启动Vue界面',stored_characters:initializedSaved.characters.length,stored_worlds:initializedSaved.worldSettings.length,stored_corpus:initializedSaved.corpusData.length,stored_events:initializedSaved.events.length,loaded_in_memory_characters:initCtx.characters.value.length});

  let resolveGeneration;
  const generationPromise = new Promise(resolve=>resolveGeneration=resolve);
  const chapterA = {id:1,title:'A',description:'大纲A',content:'原文A'};
  const chapterB = {id:2,title:'B',description:'大纲B',content:'原文B'};
  const genCtx = vm.createContext({console:silent,ElMessage:msg,checkApiAndBalance:()=>true,buildGenerationContext:()=>({}),getViewpointDescription:x=>x,formatGeneratedContent:x=>x,
    apiService:{generateTextStream:()=>generationPromise},currentChapter:ref(chapterA),currentNovel:ref({title:'书'}),chapters:ref([chapterA,chapterB]),
    isGeneratingContent:ref(false),isStreaming:ref(false),streamingType:ref(''),streamingContent:ref(''),streamingChapter:ref(null),
    generateConfig:ref({wordCount:100,style:'third-person',focus:''}),selectedMaterials:ref({characters:[],worldSettings:[],corpus:[],events:[]}),selectedContextChapters:ref([]),
    content:ref(chapterA.content),hasUnsavedChanges:ref(false),saveCurrentChapter(){},saveNovelData(){},setTimeout(){}});
  vm.runInContext(region(writer,'const generateContentWithPrompt = async (customPrompt) => {','// 使用自定义提示词优化文本')+'\nglobalThis.generate = generateContentWithPrompt',genCtx);
  const inflight = genCtx.generate('仅生成A');
  genCtx.currentChapter.value=chapterB;
  genCtx.content.value=chapterB.content;
  resolveGeneration('A的生成结果');
  await inflight;
  assert.equal(genCtx.content.value,'A的生成结果');
  results.push({case:'生成过程中切换章节，结束时覆盖当前章',scope:'R-隔离作者处理函数，模型模拟',requested_chapter:'A',current_chapter:'B',editor_content:genCtx.content.value,wrong_chapter_overwrite:true});
  fs.writeFileSync('./.audit-cache/2026-10-13/probe-91writing-results.json',JSON.stringify(results,null,2));
  process.stdout.write(JSON.stringify(results,null,2)+'\n');
}
main().catch(e=>{console.error(e);process.exitCode=1});
