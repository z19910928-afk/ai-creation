# 墨屿严格第三方功能审查

审查日期：2026-10-11（UTC）
审查对象：当前工作树（基线提交 `13afd9f`，版本标签 `v0.4.0-audit`）
审查性质：独立工程复核、运行时接口验证和 clean-room 产品对照；不是外部机构认证、渗透测试、合规审计或真实模型质量认证。

## 总结结论

墨屿已经是一个**可运行的单用户本地原型**：章节、任务卡、候选 AI 输出、记忆候选、来源 span、章节审查、JSON/Markdown 导出和 SQLite 备份都有实际代码路径，现有自动化 fixture 也能通过。

严格结论是：**当前还不能称为第一梯队的 AI 长篇创作系统，也不能称为生产级写作平台。**主要原因不是界面数量，而是正典可信度、AI 运行可追溯性、上下文预算准确性、修订恢复体验和前后端功能闭环仍有关键缺口。

本次没有发现新的 S0 数据恢复故障；发现 8 项 P1 功能/可信度缺口和 8 项 P2 能力缺口。P1 项未关闭前，不建议对外宣传“可靠长篇记忆”“自动连续性保证”或“完整审查门”。

## 审查方法与证据

1. 逐段核对 `server.py`、`app.js`、schema/migration、README、产品蓝图和既有第三方项目对照记录。
2. 运行：
   - `python -m py_compile server.py scripts/*.py tests/*.py`
   - `node --check app.js`
   - `tests/runtime_continuity_fixtures.py`
   - `tests/schema_negative_fixtures.py`
   - `tests/corrupt_import_fixtures.py`
   - `tests/reversible_patch_fixtures.py`
   - `tests/backup_recovery_fixtures.py`
   - `scripts/evaluation_runner.py --fixtures-dir tests/fixtures`
   - `scripts/recovery_drill.py`
3. 启动隔离 SQLite 服务，实际调用 `/api/state`、章节 PATCH、`/api/review`、`/api/context`、离线 `/api/ai`、记忆 POST、设定 POST 和 Origin 负向请求。
4. 对照 NovelForge、MuMuAINovel、arboris-novel、Ai-Novel、91Writing、webnovel-writer 的公开快照；只借鉴产品模式，不复制代码、提示词或素材。许可证边界见 `docs/third-party-audit.md`。

### 运行证据摘要

- 所有语法检查和现有 fixtures：通过。
- 评测 runner：返回 `passed: true`，但样例证据只有 3 条、冲突样本只有 2 条，不能代表真实中文长篇。
- 隔离服务的 `/api/context`：预算字段返回 `max_tokens=24000`，但实际是字符数除以 4 的估算。
- 隔离服务的 `/api/ai`：离线请求返回 `ai_run_id`，记录状态为 success/pending。
- 隔离服务的 `POST /api/memory`：提交 `status=accepted`、无 `source_quote`、无实体时返回 HTTP 201 和“已确认”，数据库没有 source span，也没有 canon assertion。这是可复现的 P1 缺口。
- 带恶意 `Origin` 的写请求返回 403；不带 Origin 的本机脚本请求被接受，这是本地单用户边界的现状，不是身份认证。

## P1：必须优先关闭的缺口

### P1-01：已确认记忆可以没有证据，也没有正典 assertion

证据：`server.py` 的 `/api/memory` 接口接受 `status=accepted`；`ensure_memory_assertion()` 在没有 `entity_id` 或 `source_span_id` 时直接返回 `None`。实际请求中，`POST /api/memory` 返回 `201`、`status=已确认`，但 `source_span_id=null`，数据库没有对应 assertion。

影响：UI/导出会把它显示成已确认记忆，AI 工作集也会把它当作 confirmed memory，但它无法回链原文，也没有正典事实记录。这个状态违反“无证据不得进入正典”的产品原则。

修复验收：接受记忆必须同时满足有效 `source_span`、实体或明确的非实体事实模型；否则只能保持 candidate，并显示缺失证据原因。增加 `unsupported_canon_rate=0` 的 API 回归测试。

### P1-02：AI 运行与作者采纳动作没有闭合

证据：`record_ai_run()` 固定写入 `author_decision='pending'`；前端 `adoptAI()` 只把文本追加/替换到浏览器状态，没有携带 `ai_run_id`，也没有接受、拒绝、编辑后采纳或撤销接口。

影响：无法回答“哪一次模型输出进入了哪一版正文”“作者修改了多少”“被拒绝的候选是什么”。这削弱 provenance、成本统计、回放、AI 使用披露和问题追责。

修复验收：生成结果返回 run；采纳/拒绝/手动编辑保存 activity、revision、response hash、author decision、accepted span；失败也要生成 failed run 和错误原因。

### P1-03：终稿“检查”是提示，不是质量门

证据：`showFinalizationCheck()` 即使 `finalizationIssues()` 返回未完成章节、未通过审查、未确认记忆或未回收伏笔，`data-export` 仍然直接调用 `markdownExport()`；“标记全书完稿”也允许作者确认后绕过问题。

影响：产品文案会让作者以为终稿经过门禁，但实际可以导出未审查、未确认或有未回收伏笔的作品。可以保留“强制导出”选项，但必须明确标为作者覆盖，并把覆盖原因写入导出日志。

修复验收：默认导出阻断 P1/P0 问题；覆盖导出需要逐项确认、理由和审计事件；导出包包含门禁快照。

### P1-04：有 revision，但作者没有可用的版本回滚/差异界面

证据：章节正文修改会追加 `document_revision`，但前端没有 revision 列表、diff、恢复按钮；`/api/chapters/:id/restore` 只恢复删除 tombstone，不恢复旧正文。AI `polish` 直接替换当前正文，提示“原文可从数据库 revision 恢复”，但用户没有产品路径完成恢复。

影响：长篇写作中一次错误润色或误粘贴可能让作者无法在 UI 中找回上一版，违背作者控制和可恢复性承诺。

修复验收：章节历史、逐版 diff、恢复为新 revision、恢复前预览、恢复后 source span stale 处理全部可用；AI patch 采用范围化 diff，不再整章替换。

### P1-05：记忆后端有状态机，前端只有“确认”按钮

证据：服务端支持 candidate/accepted/rejected/stale/superseded 及 source span；前端记忆中枢只提供确认，不能查看原文引句、范围、revision、assertion、矛盾、拒绝原因、合并或重新定位。

影响：作者无法审查证据，也无法修复“同名角色”“重复引句”“正文改写后 stale”的记忆。候选→正典的核心可信工作流在 UI 上是不完整的。

修复验收：记忆详情面板显示原文和版本；支持确认、拒绝、编辑、合并/拆分、标记 stale、重新定位、查看依赖 assertion，并可跳回章节位置。

### P1-06：来源范围和 token 预算的可解释性不准确

证据：`ai_context()` 对检索章节把“首 700 字 + 尾 1800 字”的拼接摘录标成 `source_range: {start: 0, end: len(excerpt)}`；这不是原文连续范围。预算用 `len(JSON)/4` 估算，中文 token 化、系统提示、`<manuscript>` 包装和作者指令没有纳入实际 provider tokenizer 计数。

影响：模型引用的范围可能指向错误原文；中文长篇在“估算 24,000 tokens”下实际超过模型上下文，产生截断、失败或隐性丢记忆。当前 24k 是字符近似上限，不是可靠 token hard cap。

修复验收：每个 excerpt 返回真实 source spans（允许多个不连续 span）；按 provider tokenizer 或保守语言模型估算，并把系统消息、上下文、输出预算分别计量；超预算时明确裁剪原因和被省略项。

### P1-07：检索和连续性检查仍是原型级

证据：检索实现是章节全文上的简单词项命中 + 新近度；没有中文 FTS/别名、dense+sparse、RRF/rerank/MMR、实体/时间/分支硬过滤。`/api/review` 是长度、状态、任务卡、标记实体、候选记忆和重复段落规则，不能发现人物状态、时间线、角色知识越界或伏笔提前揭示。

影响：书一长，关键事实可能召回不到；“连续性检查”名称会让用户误以为已完成语义事实核查。

修复验收：先实现 SQLite FTS5/中文分词或可替换索引，再加入实体/时间/分支过滤；连续性检查输出 claim/evidence/conflict 类型、置信度和原文跳转；在真实中文 gold set 上报告 Recall/Precision 和硬冲突召回。

### P1-08：模型任务没有结构化输出契约、取消、重试和失败记录

证据：所有任务共用一个自由文本 chat completion；没有 JSON schema/patch schema、流式响应、取消、持久任务队列、重试/断点、并发租约和费用配额。provider 失败会直接返回错误，不写 failed `ai_run`。

影响：续写、场景卡、大纲、总结和连续性审查无法机器校验或稳定回放；长请求卡住时用户无法取消，失败后没有可审计的失败记录。

修复验收：按 Planner/Scene Writer/Memory Extractor/Continuity Auditor/Rewriter/Recap/Publisher 分离 prompt contract；结构化输出先校验再入候选；实现取消/重试/状态机、错误 run、token/cost/latency 记录。

## P2：第一梯队差异化能力仍缺失

| 能力 | 当前状态 | 对照项目中成熟做法 | 缺口 |
|---|---|---|---|
| 设定关系图/事件图 | 只有设定卡 | NovelForge、STARC | 无关系、立场、动态和时态可视化 |
| 规划→场景→写作→审查 | 有任务卡和多任务下拉 | arboris、webnovel-writer | 没有角色化 agent、场景树和 commit 门 |
| 材料选择与 prompt 预览 | 有上下文预览 | 91Writing、Ai-Novel | 作者不能 pin/omit/排序材料，不能查看完整 prompt contract |
| 流式生成 | 无 | MuMuAINovel、Arrows | 无增量输出、暂停、续传和并行候选 |
| 伏笔生命周期 | 三列静态看板 | NovelForge、webnovel-writer | 前端不能移动、编辑、回收证据或依赖排序 |
| 全文搜索 | 浏览器 substring | 成熟编辑器/FTS | 不搜记忆、伏笔、时间线和来源，中文召回弱 |
| 出版互操作 | JSON/Markdown | Scrivener/成熟编辑器 | 无 DOCX/EPUB/PDF、版式预检和往返损失报告 |
| 项目/分支 | 环境变量固定一个 project/branch | webnovel-writer/知识库产品 | 无项目切换、分支创建、合并和 what-if 隔离界面 |
| 协作与权限 | 明确单用户 | Yjs/Tiptap 等 | 无账户、同步、评论、快照权限；只能保持本地产品定位 |
| 长篇质量评测 | fixture 协议通过 | LongMemEval/自建 gold set | 样本过小，没有真实模型、人工 pairwise、成本/延迟趋势 |

## 六个参考项目的吸收判断

- **NovelForge**：已吸收任务卡、候选记忆和审查门；尚缺关系图、结构化卡片和可见工作流状态。
- **MuMuAINovel**：已有 provider 抽象和有界上下文思想；尚缺 SSE、取消、重试和多模型路由。
- **arboris-novel**：已有任务卡概念；尚缺 Planner/Director/Writer 分工、时间线和伏笔依赖。
- **Ai-Novel**：已有近章/召回/记忆层；尚缺真正的分层上下文编排、混合检索、重排和输出契约。
- **91Writing**：已有候选式 AI 和备份思路；尚缺材料选择器、完整 prompt 预览和可见编辑历史。
- **webnovel-writer**：已有章节 commit 的产品方向；尚缺 prewrite/precommit/postcommit 门、投影索引和可审计回滚界面。

## 发布判断

### 当前可以宣称

- 单用户、本地优先的可运行小说编辑原型。
- 有限上下文召回和来源字段原型。
- 候选式 AI 输出、章节基础规则检查、JSON/Markdown 导出。
- SQLite 备份、损坏拒绝和恢复 fixture 已通过。

### 当前不能宣称

- 百万 token 上下文。
- 可靠的长篇事实记忆、自动连续性保证或自动事实核查。
- 完整的 AI provenance、可撤销 patch 或结构化多 agent 工作流。
- 第一梯队出版、协作或生产级公网软件。
- 真实模型长篇质量已通过第三方认证。

## 优先修复顺序

1. P1-01：禁止无证据 accepted memory，补齐 assertion/证据回归测试。
2. P1-02/P1-08：把 AI run、候选、采纳、拒绝、失败和 revision 串成可回放状态机。
3. P1-03/P1-04：把终稿门和 revision/diff/恢复变成真正可执行的产品能力。
4. P1-05/P1-06：做证据详情、真实多 span、准确预算和 stale 修复界面。
5. P1-07：实现中文 FTS + 实体/时间/分支过滤，再做真实中文长篇 gold set。
6. P2 功能按作者价值排序：材料选择器、伏笔 DAG、时间线/关系图、流式取消、多格式出版。

本报告与 `docs/third-party-audit.md` 配套使用。若文档描述与运行证据冲突，以当前代码、可复现请求和测试输出为准。
