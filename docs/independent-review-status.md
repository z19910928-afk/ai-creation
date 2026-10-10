# 独立 AI 复核状态报告

复核日期：2026-10-09（UTC）

## 结论与边界

这是一份独立 AI 复核报告，不是外部机构认证、渗透测试、合规审计或生产可用性证明。复核对象是产品蓝图、SQLite schema/migration、评测规范和现有负向测试；没有把“蓝图写得完整”当作“运行代码已完成”。

当前结论：P0 数据基线有一小组可复现的 SQL 防护，但整体仍不能标记为 production-ready，也不能宣称已通过完整质量门槛。至少存在可直接复现的跨作用域写入、append-only 绕过、迁移非原子/不可重跑、无证据 canon 写入和未经确认的 export success 路径。

## 审阅对象及 SHA-256

以下哈希是本次复核读取到的文件快照；后续修改后必须重新复核。

- `docs/master-product-blueprint-v2.md`
  - `b6a44462339e4a51ec82c700e044d7e1c71b9cd27c2a293888275d28164d5d5b`
- `docs/schema-v1.sql`
  - `1459c2dc27ff492042116127884f5e8cb401c4b6299fa9bb603a4104a1cc4821`
- `docs/schema-v1.1-migration.sql`
  - `2ec6418c167fef7a7cb9038911fca8c44f84656731cc9511c4ea1848e07f0103`
- `docs/evaluation-spec-v1.md`
  - `b015d9f871e0d9cedddfe590fa7401c7608fe48d744244013be47851963b14f9`
- `tests/schema_negative_fixtures.py`
  - `8ea1556e27c806a78768a9d50086bd983f451a92f4972f7f93f84ef276ff8639`
- `README.md`
  - `0e13774eb81631108b731dfba34fe1a22debd49eff7ee689cd4db9656afa0ca1`
- `app.js`
  - `805874af0c8b0cbdfd06c210c6431912d7161ae5c3d373ec4eca14f305c74d08`

## 复核方法

1. 逐段阅读蓝图 §1–§10.13、schema、migration、评测规范、测试脚本和当前应用入口。
2. 在隔离的内存 SQLite 数据库中执行 `schema-v1.sql`，再执行 `schema-v1.1-migration.sql`。
3. 运行 `python tests/schema_negative_fixtures.py`。
4. 在同一类隔离数据库中执行针对普通 UPDATE/DELETE、`INSERT OR REPLACE`、跨 project/branch 引用、状态越权、JSON/hash/offset、导出确认和迁移重跑的负向探针。
5. 未运行浏览器端到端测试、真实崩溃/恢复演练、加密/密钥删除验证、云模型外发测试、向量检索基准或多模型回放。因此这些项目只能标为“设计待验证”。

环境：Python 3.12.14，SQLite 3.53.1。测试使用内存数据库，不修改持久化数据库。`app.js` 仍以浏览器 `localStorage` 保存 JSON；它没有连接这些 SQLite 表或 migration。

## 已验证

这些结论只表示本次静态检查或隔离 SQL 探针得到证据，不代表完整产品能力通过。

| 项目 | 证据 | 状态 |
|---|---|---|
| v1 schema + v1.1 migration 可在空内存库执行 | Python `executescript` 成功，创建 12 张 v1 表及 v1.1 新表/触发器 | 已验证（仅语法/建库） |
| 普通 revision UPDATE/DELETE 被拦截 | `prevent_revision_update/delete` 触发器；探针 G01/G02 阻止 | 已验证（普通路径） |
| 普通 event_log UPDATE/DELETE 被拦截 | `prevent_event_update/delete`；探针 G03/G04 阻止 | 已验证（普通路径） |
| run_manifest 普通 UPDATE/DELETE 被拦截 | `prevent_run_manifest_update/delete`；探针 G05/G06 阻止 | 已验证（普通路径） |
| 跨项目 branch parent 的 INSERT 被拦截 | 测试脚本第 20 行及探针 G07 | 已验证（仅 INSERT） |
| revision parent 分支/节点不一致的 INSERT 被拦截 | `check_revision_scope`；探针 G08 | 已验证（仅 parent 非空 INSERT） |
| source span 负范围被拦截 | `check_span_range`；探针 G10 | 已验证（只检查 code-point 起止） |
| 普通 source span 跨项目引用被拦截 | `check_span_project`；探针 G09 | 已验证（部分路径） |
| 评测/蓝图已声明“实现未完成” | 蓝图 §10.13 明确 P0 foundation subset 与 P1/P2 planned/experimental | 已验证（文档措辞） |

现有负向脚本的实际覆盖很小：它只验证了跨项目 branch parent、revision UPDATE、event UPDATE 三个失败断言，不能证明蓝图 §322 的全套发布门槛。脚本没有 `test_*` 用例或 fixture runner；直接用 Python 执行时会打印成功，但没有发布报告、指标快照或 CI 映射。

## 设计待验证

以下是合理的方案方向，但当前没有足够实现或测试证据，不能写成“已通过”。

- Human-Primary、candidate → author review → canon 的产品原则；当前 `app.js` 只有标记捕获和本地状态，没有完整 candidate/assertion 状态机。
- source span 的 NFC、UTF-8、code-point、grapheme、node path 设计；SQL 未保存 grapheme map/node path，也未测试富文本映射、Unicode 规范化漂移和锚点歧义。
- W3C PROV 风格的 entity/activity/agent 链；表已部分补出，但 edge 没有 subject/object 外键、标准 predicate 约束或跨 project 约束。
- 分支可见集合、祖先继承、merge_base 和 CAS；文档没有说明继承是否在分叉点截断，评测只定义“非祖先”泄漏，不能检测父分支后续事实是否错误流入子分支。
- 中文 FTS、dense/sparse、RRF、rerank/MMR、索引过期和 read-your-writes；没有 tokenizer/embedding 实现、gold corpus、freshness 数值阈值或性能基准。
- 本地 WAL、Online Backup/VACUUM INTO、损坏恢复和 RPO/RTO；只写了架构边界，未做真实备份、故障注入、恢复和完整性校验。
- 隐私、BYOK、云外发范围、供应商留存和删除外发副本；没有密钥管理、E2EE、供应商删除证明或恶意 DOCX/EPUB/PDF 解析测试。
- 所有引用项目和研究 benchmark 的能力边界；§10.10–§10.12 已纠偏方向，但仍没有逐项的访问日期、commit/version、许可证和可重复的对照记录。

## 未关闭阻塞项

### 1. append-only 仍可被绕过

普通 UPDATE/DELETE 触发器有效，但探针 U13/U14 的 `INSERT OR REPLACE` 可以替换已有 `document_revision` 和 `event_log` 行并返回成功。SQLite 官方文档说明 REPLACE 会先删除冲突行；相关删除触发器是否触发还受 `recursive_triggers` 影响。当前连接的 `recursive_triggers` 为 0，schema 没有把这类写入固定为禁止路径。

最小闭环：事件源表和 revision 使用只允许普通 INSERT 的写入 API/authorizer；拒绝 OR REPLACE/UPDATE/DELETE；启用并验证触发器策略；为重复 idempotency 和替换攻击增加 CI 负向 fixture。

### 2. 跨 project/branch 作用域仍不闭合

单列外键只证明“ID 存在”，不证明它属于同一个 project/branch。以下探针均被接受：

- 更新 `branch b1` 的 parent 为另一个 project 的 `b2`（U01）。
- 创建 branch `b2` 下、但挂在 `p1` 的 document node（U02）。
- 创建 `n1/p1` 的 revision，却声明 branch `b2`（U03）。
- 把 p1 candidate 的 source span 改成 p2 的 span（U04）。
- 在 p1 branch b2 中引用 p1 branch b1 的 span（U05）。
- 修改已有 source span 的 project_id（U06）。

最小闭环：为 `(project_id,id)` 建唯一键并使用复合外键，或对 INSERT 和 UPDATE 都建立一致性触发器；对 revision/node/span/candidate/assertion/deletion/provenance/merge_conflict 全部覆盖；增加跨 project 和跨 branch 的负向测试。

### 3. canon 与 export confirmation 没有强制门

- 探针 U08 可以直接 INSERT `assertion(state='canon')`，没有 source/evidence、reviewer 或 author decision 要求，违反蓝图 §5、§10.10 和 §322 的“无证据 canon 写入=0”。
- 探针 U11 可以直接 INSERT `export_job(status='success', confirmed_by=NULL)`。现有触发器只检查 UPDATE 到 success 的旧状态，不检查 INSERT，也不检查 confirmed_by；从 `awaiting_confirmation` 更新到 success 在 U15 也成功。

最小闭环：将 canon 转换和 export success 设计成事件状态机；要求可验证 evidence/reviewer/confirmation；禁止直接 INSERT 终态；覆盖 INSERT、UPDATE、UPSERT、REPLACE、批量导入路径。

### 4. migration 不具备原子性、版本标记或安全重跑

`schema-v1.1-migration.sql` 没有事务、迁移版本表、前置检查、完成标记或回滚。注入中途失败后，`created_by` 列已经存在但 `assertion` 表尚未创建；重跑立即失败 `duplicate column name: created_by`。`project.schema_version` 仍为 1，`PRAGMA user_version` 仍为 0。

最小闭环：事务化 migration；单独 migration ledger/user_version；每步可检测、可重试、可回滚；故障注入后恢复并验证 schema hash；在默认关闭 foreign keys 的 SQLite 连接上显式设置并检查 `PRAGMA foreign_keys=ON`。

### 5. 新表契约和现有蓝图仍不一致

§10.10–§10.12 声称最小闭合，但 SQL 仍缺完整的 assertion/provenance 约束、character knowledge/plot lifecycle、外部资料定位、index checkpoint 重建细节、export manifest 状态规则等。`prov_edge` 的 object/subject 没有外键；`predicate` 允许任意字符串；`ai_run` 与 `run_manifest` 的项目、branch、上下文引用一致性没有约束。

最小闭环：要么补齐并测试完整 P0 表契约，要么把这些项目明确降为 planned/experimental，并将发布门槛从“完整能力”收窄到已实现 subset。

### 6. 蓝图内部仍有旧契约与新契约并存

§4 没有标记为 superseded，但和后续审计修订冲突：`document_revision` 仍列 `deleted`（§4.1），`source_span` 仍列通用 `start_offset/end_offset`（§4.2），`assertion`/`provenance` 仍是旧字段集合（§4.3–§4.4）；§10.10–§10.12 又分别要求删除事件、code-point/UTF-8/grapheme 坐标、扩展后的 assertion 和 PROV 映射。§3/§10.8 的“metadata、ID、provenance 往返”也需要服从 §10.9 的 loss manifest 限制。

最小闭环：直接同步 §4 的规范字段，或在每个旧契约标题旁明确 `superseded by §10.10–§10.12`，并让 schema/fixture 只引用一个版本。

### 7. 评测规范不能支撑蓝图发布门槛

`docs/evaluation-spec-v1.md` 没有固定 K、证据去重/匹配、零分母处理、Revision Distance 的编辑单位和满意度判定；freshness SLA、RPO、RTO 没有数值目标。`citation precision` 只统计“带引用结论”，不覆盖应当引用却没有引用的结论。§322 声称的无证据 canon、损坏导入、恢复、patch 可撤销率没有对应 fixture。现有测试也没有这些路径。

最小闭环：为每个指标给出输入/匹配/zero-case/阈值/样本数/置信区间；把 §322 每一项映射到可运行 fixture 和 CI；增加 unsupported-answer、uncited claim、prompt injection、PII 外发、延迟/成本和恢复演练。

### 8. 运行代码与方案层没有接合证据

`README.md` 和 `app.js` 明确当前应用是浏览器 localStorage + JSON 备份。应用没有 SQLite 打开、schema migration、revision 事件、provenance、AI provider、FTS5、向量索引或 export_job 实现。因此 SQL 复核只能说明“数据库设计的一部分可建库/部分防护存在”，不能说明产品工作流已实现。

## 证据索引

- 蓝图实现状态声明：`docs/master-product-blueprint-v2.md:346-351`。
- 蓝图旧数据契约与后续修订：`docs/master-product-blueprint-v2.md:30-60`、`:269-351`。
- 现有基础 schema：`docs/schema-v1.sql:1-19`。
- v1.1 migration、触发器和新增表：`docs/schema-v1.1-migration.sql:1-37`。
- 现有测试仅 3 个负向断言：`tests/schema_negative_fixtures.py:19-27`。
- 当前应用 localStorage、JSON 导入/导出和候选标记：`app.js:1-24`，`README.md:3-33`。
- SQLite `INSERT OR REPLACE` 删除冲突行及触发器行为：<https://www.sqlite.org/lang_conflict.html>。
- SQLite 外键默认关闭且需要每个连接显式启用：<https://www.sqlite.org/foreignkeys.html>。
- SQLite WAL/备份边界：<https://www.sqlite.org/wal.html>、<https://www.sqlite.org/backup.html>。
- W3C PROV-DM 核心和派生关系：<https://www.w3.org/TR/prov-dm/>。

## 交付判断

本复核不授予“通过”印章。可交付的诚实状态是：

- 方案层：原则和风险分类较完整；关键语义仍有设计待验证项。
- SQL P0 foundation：空库建表、部分普通写入保护和部分 branch/span 负向路径已验证；跨作用域、终态门、迁移可靠性和全量 append-only 未关闭。
- 评测层：规范草案已存在，但尚不足以证明蓝图 §322 的发布门槛。
- 应用层：当前仍是 localStorage 原型，SQLite/AI/provenance/检索闭环未实现。

在上述阻塞项完成并由 CI、故障恢复演练、真实应用集成和独立复核重新验证之前，不应宣称正式长篇工作流、可靠长篇记忆或 production-ready。

## Addendum 2026-10-09 23:46 UTC

本报告前文是修改前的历史快照；其中关于固定 K、零分母、SLA、fixture 数量和文件 SHA-256 的陈述不适用于本附录之后的版本。当前以 `docs/evaluation-spec-v1.md` v1.1、`tests/schema_negative_fixtures.py`、`scripts/apply_migration.py` 和最新 migration 为准。已新增并验证：K={1,5,10}、NA/样本数约定、freshness/RPO/RTO 基线、JSON 指标方向、OR REPLACE/canon/export 门禁与幂等 runner。完整应用接入、跨作用域组合约束、损坏导入/恢复/隐私/prompt-injection 端到端仍未宣称完成。本文件仍是独立 AI 复核，不是外部机构认证。

## Current verification matrix — 2026-10-09 23:53 UTC

本节优先于上文的历史探针描述，作为本轮复审的现状摘要：

- 迁移：基于 `schema-v1.sql` 的首次迁移、二次幂等执行、ledger 与 `PRAGMA user_version=11` 已通过；事务故障回滚与重试已验证。
- 不可变写入：revision、event、deletion、audit、run_manifest 的普通 UPDATE/DELETE 与 `INSERT OR REPLACE` 负向路径已覆盖。
- 作用域：branch/node/revision/source_span/candidate/assertion/deletion、ai_run、character_state、plot_thread、external_source、PROV、index_checkpoint、export_job、run_manifest、merge_conflict 的跨 project/branch 负向路径已覆盖；INSERT/UPDATE 保护已复测。
- 终态门禁：canon 要求非空 evidence、reviewer、reviewed_at；export success 要求从 awaiting_confirmation 转换且有 confirmed_by；已覆盖直接 INSERT 与替换路径。
- 尚未通过完整生产门：浏览器原型仍未接入 SQLite/AI/检索/PROV/备份恢复/发布器；真实 JSON 评测 runner、损坏导入、恢复演练、隐私外发和 prompt-injection 的端到端证据仍缺失。因此本复审不授予“production-ready”或“可靠长篇工作流”印章。

结论：P0 SQL foundation 可标记为“已复核通过”；产品完整实现与生产发布门仍为“未通过/待实现”。

## Implementation update — 2026-10-10 00:36 UTC

本报告早期章节保留为历史审计快照。当前实现已新增并验证：`server.py` SQLite 本地服务及前端 API 接入、`scripts/evaluation_runner.py` 五组 JSON fixture、损坏导入和可撤销 patch fixture、Online Backup/恢复/损坏拒绝、真实 app SQLite 数据库恢复演练。此前“无真实 JSON runner/无恢复演练”的表述仅适用于早期快照，不适用于本节之后版本。

新的剩余边界是：未接入真实模型供应商、向量检索/重排、真实长篇语料效果评测、BYOK/隐私外发策略、多人协作和发布器；这些仍不能标记 production-ready。

## Final local-closure gate — 2026-10-10

独立复审最终确认本地单机闭环通过：SQLite API、事务/CAS、scope、事件幂等、静态白名单、Origin/Referer/端口 CSRF、输入与导入校验、恢复路由、评测 runner、备份/恢复/损坏拒绝均有运行证据。5 个 JSON fixture、schema/backup/reversible/corrupt fixtures 和真实 app DB recovery drill 均通过。

剩余限制不是本地闭环 blocker，而是尚未实现的产品层：真实模型供应商与 BYOK、向量 embedding/重排、中文 FTS 质量基准、多人协作、发布器、隐私外发证明和真实长篇效果评测。不得把 fixture-model 指标当成真实模型质量证明。

## AI 创作助手预览版 — 2026-10-10

当前版本新增了显式触发的 AI 创作助手：无供应商配置时使用离线模板，配置 `MOYU_AI_BASE_URL`、`MOYU_AI_MODEL` 和 `MOYU_AI_API_KEY` 后可调用 OpenAI 兼容接口。服务端会把项目摘要、已确认记忆、故事卡、情节卡、最近章节片段和当前章节片段组成有界工作集，并阻止重定向以避免密钥转发到其他主机；密钥只从环境变量读取，前端不保存密钥。

助手结果默认进入候选区，作者需要明确采纳后才会改写正文。当前仍未实现 tokenizer 精确预算、embedding/向量检索与重排、引用来源 span、费用/配额控制、流式取消和真实长篇质量基准，因此百万上下文仍是后续架构目标，不能宣称已经实现。
