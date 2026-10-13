# 交叉复核：Ai-Novel 记忆报告与 MuMu 功能报告

复核日期：2026-10-13。复核人：91Writing 专责代理，独立读取相关源码链并重跑 MuMu 隔离探针。

## 范围与证据等级

- 审读对象：`agent-memory-ai-novel.md`、`agent-mumu.md`，均位于本报告目录。
- AN：`./.audit-cache/2026-10-13/references/Ai-Novel`，本轮采集标记为默认 `dev`、`8b85b2b6f29af4c69241be7c42417dbe4795db30`。
- MM：`./.audit-cache/2026-10-13/references/MuMuAINovel`，本轮采集标记为默认 `main`、`99f0aec6ce3b1c65652d20007be930ebba6a6d20`。
- MY：`.`，本轮 `git rev-parse HEAD` 为 `62f1c66a7b16bb84f2940dc1af48e685aaf58818`。AN/MM 是采集的源码目录，无 `.git`；固定提交归属沿用本轮采集记录，没有独立声称验证 Git checkout。
- E：源码入口、处理及消费链可以追到；R：本次实际执行的明确组件样本；S：只有结构；U：未验证。静态 AST 注册检查仍为 E。
- 只读复核。未修改产品、安装大依赖、运行上游整站、调用真实 AI、运行真实 Chroma/PG 向量检索、连接生产或产生费用。

结论：两份报告的重要技术判断基本成立。需要下调的是“写后记忆闭环”等过宽表述，以及把局部组件运行、静态路由检查、标准 JSON 迁移推成整站效果或完整数据库恢复的读法。墨屿的部分差距是作者页面未接已有后台能力，应优先接通，不能重复建设并声称原先完全没有。

## A. Ai-Novel 报告的 10 项复核

| 原主张 | 复核结论 | 独立源码证据与完整性边界 | 本次 R 范围 |
|---|---|---|---|
| 当前 dev 退役旧结构化记忆、Worldbook、项目表格及分形记忆 | **确认 E**；不只依据 README | AN `backend/app/api/router.py:43-75` 的实际注册无旧模块；`backend/alembic/versions/b2d4e6f8a0c1_drop_retired_tables.py:24-38` 列出旧表，`:104-107` upgrade 调用 drop。`:50-100` 要求完整旧 schema 且所有旧表为空，非直接无保护删数据。当前页面路由 `frontend/src/App.tsx:155-180` 接人物、设定、大纲、写作等，未接旧模块。遗留 `tables` 配置及 `sys.memory.structured.md` 模板不能恢复为当前能力。 | 无；未执行迁移 |
| 作者本次要求与章节计划参与召回，并实际进入生成提示 | **确认 E**，受记忆开关与模板启用控制 | AN `frontend/src/pages/writing/useChapterGeneration.ts:159-171` 发送 instruction；`chapter_generation/memory_service.py:31-48` 开关与 instruction+plan 查询，`:60-72` 取 memory pack 放入 values；`prepare_service.py:150-167` 调用，`:252-278` 渲染到 prepared prompts；`resources/prompt_presets/chapter_generate_v4/templates/sys.memory.story_memory.md:1-11` 与 `sys.memory.vector_rag.md:1-11` 消费 text_md。MY `server.py:500-507` 的 ai_context 无 instruction，`:3902,3918,3933` 只在建好材料后单独传给生成函数。 | 无；提示链 E，不是实际模型生成 |
| 完整章节分块比墨屿头尾摘录更适于找到中部原句 | **确认 E**；检索质量仍 U | AN `vector_chunk_builder.py:14-32` 滑窗与 overlap；`:180-185` 分块默认参数；`:214-240` 读取整章 content_md 后逐块建索引。MY `server.py:519-527` 长章只保留头700/尾1800，`:350-361` 再裁成头500/尾1000。这足以确认结构差距，不能证明任意关键词一定召回。AN metadata 有 chapter_number/source_id/chunk_index，未提供来源修订 hash 或精确字符 offset。 | 本次未重跑墨屿中部样本；原报告 R 保留其原范围 |
| Ai-Novel 有语义召回、PG 混合检索、重排与可观测日志 | **确认 E，收紧质量表述** | AN `vector_retrieval.py:573-619` 接 embedding/rerank、检查启用；`:670-740` PG 检索路径；`:816-849` 去重、单来源限额及最终文本预算；`:852-882` 计时与日志；Chroma 分支在 `:942-1211`。`memory_retrieval_service.py:383-414` 调用并取 prompt_block.text_md。存在这条链不等于默认配置可用或比词法召回质量高。MY ai_context 自建词法分数 `server.py:507-527`，未调用独立 `search_rows`。 | 无真实 embedding、重排器或数据库运行 |
| 额外 memory/vector 链缺当前章、POV、大纲边界，可能召回后文 | **确认 E，但不能泛化到 smart context** | AN `memory_retrieval_service.py:154-162` 接口无 current chapter/POV/outline；`:194-208` StoryMemory 只按 project、文本词及重要性取；`:260-266,383-385` 向量查询没有故事时点参数。`vector_chunk_builder.py:214-219` 索引作品全部章节；`vector_retrieval.py:573-586` 查询无章上限。相反，`chapter_context_service.py:42-48,63-69` smart context 明确限定相同 outline、过去章节。风险是额外检索层绕过边界，非所有材料都无时间过滤。 | 无未来泄漏整站实验 |
| 近20章摘要、近2章正文、远期间隔骨架形成分层历史 | **确认 E** | AN `chapter_context_service.py:32-121` 分别查询摘要、近章正文、间隔骨架；近章长正文仍为头尾节选 `:81-88`；骨架 `:103-118` 可退回 plan。这是历史压缩策略，不等于摘要必然准确，也不能把 plan 当已发生事实质量已验证。MY `server.py:516-530` 是旧章词法摘录与近期固定窗口。 | 无长篇效果实验 |
| 前台默认 semantic_history=false、手工 query=null；后台能力不等于作者入口 | **确认 E** | AN `frontend/src/pages/writing/useChapterGeneration.ts:166-171` 实际请求指定 story_memory=true、semantic_history=false、memory_query_text=null、vector_rag 按 rag_enabled；`memory_service.py:40-43` 后台接受手工查询。应表述为“当前作者生成请求采用自动查询”，不把手工查询接口写成作者已可操作的独立入口。 | 无页面运行 |
| 生成摘要归入字段，done 调度任务，写后维护已闭环 | **前两项确认 E；完整记忆闭环下调** | AN `useChapterGeneration.ts:245-274` 将正文和 summary 写入编辑 form，并以 expectedContent/baseSummary 避免覆盖等待期人工编辑；这一步不是数据库落盘。`api/routes/chapters.py:209-240` done 调度；`project_task_service.py:280-298` 具体是 vector/search rebuild、characters_auto_update。可说“生成摘要、章节保存和索引/人物更新链已连接”，不能扩写为普通正文全部事实自动提取、逐项证据确认、失效重建均完成。MY `app.js:75` 显式标签提取、`:73` 非正文任务复制输出的差距成立。 | 无上游数据库落盘/派生任务运行 |
| 墨屿 accepted memory、timeline 及人物知情范围存在统一过滤缺口 | **确认 E；原报告 R 不冒充本次 R** | MY `server.py:535` 全量 accepted 有效来源经 bounded_context_memory 入材料；`bounded_context_memory:283-295` 只检查状态/来源，不查当前章；`:564-573` timeline 等直接取前N。人物状态 `:461-497` 只查显式时间、分支、章节，未查 knowledge_scope.entities/当前 POV；旧无范围状态允许通过。普通旧章 `:516` 有过去章过滤。由此应修“所有材料层统一范围”，不能说墨屿完全没有过滤。 | 本次源码复核；原报告隔离 ai_context 实验保留原说明 |
| 当前评测 fixture 不能证明真实召回已通过 | **确认 E** | MY `tests/longform_knowledge_scope_fixtures.py:19-29` 读取固定 JSON 后直接调用 evaluate_fixture；`:38-46` 人工修改 predictions 验证评测器拒绝违规。未调用 ai_context、search_rows 或真实生成器。因此评测器逻辑有效与实际生成边界有效必须分开。 | 本次未执行 evaluator；更未测文学质量 |

## B. MuMu 报告的 9 项复核

| 原主张 | 复核结论 | 独立源码证据与完整性边界 | 本次 R 范围 |
|---|---|---|---|
| 独立 polish 页面及服务不可达 | **确认 E** | MM `frontend/src/App.tsx:75-76` route 在注释内；`backend/app/main.py:206-242` 导入与 include_router 清单没有 polish；独立 `api/polish.py` 与 api helper 存在不足以形成入口。不能以泛用聊天能请求润色否认这条独立链缺失。 | 重跑探针的 AST 检查仍记 E，未发整站 HTTP 请求 |
| 最新人物状态可能污染早章，尽管向量记忆限过去章节 | **确认 E+组件 R，限定相关人物被选中时** | MM `chapter_context_service.py:31-45` append 最新 status/current_state，无当前章参数；`:535-560` 人物上下文直接调用。`api/chapters.py:1602-1616` 1→N builder 接当前章，`:1691-1726` 将 chapter_characters 放入真实生成提示。相反，`chapter_context_service.py:752-795` 相关记忆查询有 `(1,current-1)`，不可泛化为全部 MuMu 记忆无章节过滤。 | 本次 helper 原样执行输出第50章死亡与秘密；没有执行完整 builder/模型，整条泄漏链为 E |
| 局部重写应用缺少基稿复核 | **确认 E+组件 R** | MM `PartialRegenerateModal.tsx:145-149` 仅发 new_text/起止位置；`api/chapters.py:5193-5240` 权限后仅验证范围 `:5217`、按当前 content 拼接 `:5224`，没有 source hash/revision/original_text。生成前选区定位不替代应用前检查。 | 原样应用函数，假权限/查询/DB；旧选区甲推门改为甲关门后，仍 success 保存甲猛地推门。非 HTTP/真实数据库测试 |
| 该局部重写页面的取消没有连接请求 signal | **确认 E，限定该页面** | MM `PartialRegenerateModal.tsx:81` 新建 controller；`:84-118` stream options 未传 signal；`:128-134` 只 abort 此 controller。`services/api.ts:812-835` 将 options 原样给 ssePost；`utils/sseClient.ts:14-21,149-167` 接 external signal 才联动内部 fetch abort。库支持取消，页面未接上，不能推广到全部流式请求。 | 无真实浏览器取消运行 |
| 分析直接更新人物/职业，记忆 DB 与向量库分步写入 | **确认 E，残留/失败是风险而非已发生事故** | MM `api/chapters.py:1217-1233` 提取后删除旧关系库记忆并 commit；`:1248-1275` 新记忆再 commit；`:1278-1284` 之后写 Chroma；`:1286-1314` 职业更新；`:1317-1328` 直接调用人物状态更新，未经过作者逐项确认。`memory_service.py:153-217` batch_add 内部实际只有 collection.add，无同章旧向量删除；异常返回0。确认不同步/旧向量风险，不声称真实 Chroma 已残留。 | 无真实 DB/Chroma 故障注入 |
| 标准作品 JSON 导出遗漏独立伏笔及人物最新状态 | **确认 E，限定 JSON，不推成 SQL 备份丢失** | MM `schemas/import_export.py:32-56` 人物 schema 无 status/current_state/状态章号；`:208-226` 作品 schema 无独立 Foreshadow 集合；`import_export_service.py:249-261` 实际人物导出也无最新状态。`:553-600` 的分析确有 foreshadows/character_states 数组，不能说“所有伏笔内容都没导出”，但这些不含作者独立伏笔生命周期/修改后的完整实体状态。 | 未运行完整导出→导入 |
| JSON 导入后关系库记忆存在，不等于向量索引及独立实体恢复 | **确认 E** | MM `import_export_service.py:1207-1253` 只 db.add StoryMemory；`:1256-1325` 导入分析数组并创建 completed 分析任务，没有状态更新或独立 Foreshadow 重建；总入口 `:825-857` 随后仅默认风格与 commit。`api/projects.py:765-772` 导入后直接返回结果，无额外后台 reindex 挂钩。不能把页面“已分析”当索引已重建。 | 无恢复后 Chroma 查询 |
| 批量任务刷新找回/可重做与服务重启自动续跑不同 | **确认 E** | MM `backend/app/main.py:75-103` 将 pending/running 的后台、批量、分析任务全部标 failed 并注明服务重启。此证据不能否定日常任务取消/前端找回能力，但不支持服务重启原位置自动续跑。MY 已有持久单任务工作队列，也不能因此算多章批量作者流程完成。 | 无进程重启实测 |
| TXT 编码拆章可用，但短序言丢失 | **确认组件 R** | 本次重跑 `./.audit-cache/2026-10-13/mumu_isolated_probe.py`，原样 TxtParserService，仅替换 logger，结果 GB18030 两章正确；26字符样本的短前言不在解析结果。对应 MM `txt_parser_service.py:21,47` 编码/拆章逻辑。 | 只证明这两个解析器输入；未测上传页面、所有格式或大文件 |

## C. 墨屿前后台差距的独立校正

这部分防止最终汇总把“没有作者操作入口”误写成“项目完全没有对应技术”。

| 能力 | 后台与页面证据 | 应采用的最终措辞 |
|---|---|---|
| 可恢复的 AI 单任务、取消、重试 | MY `server.py:971` create_ai_job、`:1113` recover_ai_jobs、`:3983` cancel、`:4004` retry；`app.js:71` 作者生成仍请求 `/api/ai` 后等待 JSON，未接 queue/jobs/reader | 后台单任务能力已有 E；作者任务中心、取消/重接和多章受控批量仍有流程缺口。不要从头重建同等队列。 |
| 带基稿检查的局部 patch | MY `server.py:3731-3785`；`:3758-3761` 必需 revision 或 hash，`:3770-3782` 旧基稿409；应用后新 revision/失效链在 `:3814-3818`。`app.js` 无 /patch、base_hash、base_revision 或文本选区调用 | 后台比 MuMu 应用边界更强，作者选区重写、差异预览及采纳入口未接。`app.js:73` 整章 AI 采纳不能替代它。 |
| 人物时点状态 | MY `server.py:3398-3404,3644` 读写路由、`:461-497` 时间/分支过滤；`app.js:208,222,230` 主要展示/读故事结构，未接人物状态编辑写入 | 人物状态结构、路由与部分过滤已有；作者编辑流程与 POV/entities 边界未完成。 |
| 关系、时间线、伏笔 | MY `app.js:174-184` 三种 modal 与 `/api/story/relationships`、timeline、foreshadow-links 真实写请求；`:213,230` 编辑/删除/动作接线 | 作者手工维护已接 E。差距是写后候选、生命周期原句与自动失效、统一时点，不是没有任何图谱编辑。 |
| 材料选择、排序与真实提示预览 | MY `app.js:232-245` contextPreview；可固定/排除/排序，`:72,226` 接任务契约预览 | 这些作者入口已接 E；应改检索与范围，保留已有解释/控制界面。 |
| 历史版本与恢复 | MY `app.js:249` revisions 查询、正文/差异展示、restore-revision 请求 | 已有作者历史恢复链 E；不能笼统说墨屿没历史/撤销基础。真实可靠性仍按根代理运行结果判断。 |
| 导出与完整备份 | MY `app.js:255` JSON 导入导出作者入口；数据库备份是另一层 | 便携作品 JSON 与含 revision/索引等的数据库恢复分别验收；有按钮不等于全部恢复已实测。 |
| 卷/场景树 | schema 有 volume/scene；当前 create_chapter API 实际写 chapter；页面为章节列表/任务卡 | 卷、场景对象主要为 S，不能与作者已可操作的完整分卷细纲流混算。 |

## 本次运行证据

实际执行命令：`python3 ./.audit-cache/2026-10-13/mumu_isolated_probe.py`。退出码0。本次结果与原报告一致：

- GB18030 两章拆分通过；短前言未保留。
- 第50章最新人物状态 helper 直接附加死亡/秘密文本。
- 基稿变更后的旧位置局部候选仍被应用，假 DB commit 后结果为 success。
- polish 注册 AST 判定 false，此项仍为 E。

探针源码此前已经完整审读，无网络或真实 AI 调用；其输出的 `network_requests:0` 是脚本自述，本稿不把它当网络监控仪器读数。全文所述 R 不包含上游整站、生产、真实模型文学质量或真实向量检索效果。

## 供总报告采用的校正

1. Ai-Novel 的优势准确写成“作者要求参与查询、全章分块、分层历史、生成摘要字段、保存后派生任务和检索可观测性”；完整事实候选确认/失效闭环及真实质量尚不能确认。
2. Ai-Novel smart context 已限制过去章；风险集中在 project 范围的额外 memory/vector。旧退役模块和遗留模板不计为作者可用能力。
3. MuMu 普通知识检索有章上限；最新人物状态是另一条未过滤入口。helper R 与整条生成 E 需要分别标注。
4. MuMu polish 不可达是 E；局部应用旧基稿接受是隔离组件 R；取消断链是特定页面 E，不能泛化所有请求。
5. MuMu 标准 JSON 漏独立伏笔/latest人物与导入不重建索引是 E，不是全数据库备份失败的 R。
6. 墨屿应先接通已有队列、局部 patch、人物状态写入，再补普通正文写后候选、统一时点/视角、段落检索和真实流程评测；关系/时间线/伏笔、历史、材料预览已有作者入口应保留。
