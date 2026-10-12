# 墨屿 · 小说创作台

一个本地优先的中文小说创作工具第一版。正文与设定通过本地 SQLite 服务保存，可随时导出 JSON 备份；如果只打开静态页面，应用仍会回退到浏览器 `localStorage`。

## 运行

推荐启动 SQLite 本地服务（Python 标准库，无需安装依赖）：

```bash
python3 server.py
```

然后打开 `http://localhost:4173`。

服务首次启动会创建 `moyu.sqlite3`，按顺序应用 `docs/schema-v1.sql` 和
`docs/schema-v1.1-migration.sql`（`PRAGMA user_version=11`）。可以用环境变量指定数据库或端口：

```bash
MOYU_DB=/path/to/story.sqlite3 MOYU_PORT=4173 python3 server.py
```

服务默认只接受同源写请求并拒绝跨站 `Origin`/`Referer`（包括 `Origin: null` 的 `file://` 页面）。

## 本地 API

- `GET /api/health`：服务与 schema 版本
- `GET /api/state`：读取当前作品的完整 UI 状态
- `PUT /api/state`：事务性全量同步（请求必须带 `full_state:true` 和完整数组；省略项会写入删除/归档投影）
- `GET /api/chapters`、`POST /api/chapters`、`PATCH /api/chapters/:id`、`DELETE /api/chapters/:id`：章节 CRUD；删除写入 `deletion_event`，正文修改会追加 `document_revision` 和 `event_log`
- `POST /api/chapters/:id/restore`、`POST /api/records/:id/restore`：恢复章节 tombstone 或归档设定并记录事件
- `GET/POST /api/records`、`PATCH/DELETE /api/records/:id`（记忆、伏笔同样支持 `/api/memory`、`/api/plot`）：最小 CRUD 投影，并为变更写入 `event_log`
- `GET /api/events`、`POST /api/events`：查看或写入幂等事件
- `GET /api/context`、`POST /api/ai/prompt-preview`、`POST /api/ai`：查看有界工作集、预览任务契约并生成候选；支持 `retrieval_profile` 配置预算、章节召回数、近章窗口和记忆/设定上限，并返回分层计数与证据来源；`/api/ai` 支持离线结构化结果和 buffered SSE
- `GET /api/ai/status`、`GET /api/ai/quota`、`GET /api/ai/runs/:id`、`POST /api/ai/runs/:id/retry|cancel`：查看模型/预算/运行审计并处理失败运行
- `POST /api/ai/queue` 或 `/api/ai` 携带 `async:true`：将请求持久化为队列任务；`GET /api/ai/queue`、`GET /api/ai/jobs/:id` 查看状态，`process|cancel|retry` 控制单个任务，`queue/drain` 恢复人工或重启后的积压任务
- `GET /api/search?q=...`：搜索章节、设定、记忆、伏笔和时间线；结果保留来源章节与范围元数据
- `GET /api/story-map`、`POST /api/relationships`、`POST /api/timeline`、`POST /api/foreshadow-links`：读取或写入关系、时间线、伏笔生命周期和冲突投影
- `GET/POST/PATCH/DELETE /api/character-states`、`/api/plot-threads`：管理带时间范围、知识范围和证据链接的人物状态与剧情线
- `GET /api/assertions`、`GET /api/assertions/:id`、`POST /api/assertions/:id/review`、`GET /api/memory/:id/evidence`：审查正典事实、冲突、依赖和失效证据
- `GET /api/chapters/:id/revisions`、`POST /api/chapters/:id/restore-revision`、`GET /api/chapters/:id/commits`、`POST /api/chapters/:id/commit`：查看差异、恢复为新 revision，并执行章节提交阶段
- `GET /api/export/epub`、`GET /api/export/manifest`：导出最小 EPUB 及支持范围/损失清单；EPUB 直连也受终稿门保护，作者覆盖需带 `X-Moyu-Export-Override`

伏笔投影的 `DELETE /api/plot/:id` 是不可恢复的投影删除；章节和设定则使用上面的 restore 路径。

章节正文采用追加式 revision，数据库中的 `document_revision`、`event_log` 和
`candidate_memory` 遵循 schema 的外键、作用域及 append-only 触发器约束。

若只需要静态预览，仍可运行 `python3 -m http.server 4173`；页面会检测 API 不可用并使用原有 localStorage 缓存。

## 当前功能

- 作品总览、字数目标和章节进度
- 章节编辑器、自动保存、草稿/进行中/完成状态
- 章节任务卡（目标、视角、场景计划、伏笔动作、回顾、下一章简报）与完成前质量审查
- 人物、地点、势力、物品等设定卡片
- 情节与伏笔看板
- 全文搜索
- JSON 导入/导出备份
- Markdown/EPUB 终稿导出（含 frontmatter、章节/设定/伏笔/记忆元数据和 loss manifest）与只读终稿检查
- 有界上下文预览（估算 token、可配置检索层、来源 ID/范围）与 AI 运行记录
- AI 材料 pin/omit/order、完整 Prompt 契约预览与结构化 JSON 任务结果
- 人物关系、时间线、伏笔生命周期和冲突查询
- 章节 `prewrite → precommit → postcommit` 提交快照、revision diff 与恢复前预览
- buffered SSE 候选传输、失败运行重试和已完成运行取消保护
- 全书完稿状态：终稿检查后可标记完成，状态持久化并支持恢复写作
- 移动端适配

## 设计原则

当前版本仍采用单用户、本地优先、Markdown 思维的工作流。AI 是显式触发的可选能力：没有配置模型时保持离线，配置后只按任务发送有界工作集，不暗中调用模型。当前已有本地持久化 AI 队列、CJK FTS5 检索、人物状态和剧情线 API；后续重点是 embedding/RRF 重排、真实 tokenizer、外部 worker 租约、DOCX/PDF、分支合并和协作权限；EPUB 当前仅提供最小导出，尚未支持完整格式往返。

## 长篇记忆设计（原型）

记忆中枢采用“候选捕获 → 人工确认 → 相关章节回忆”的三步流程。正文里用 `【人物】名字`、`【物品】名称`、`【地点】名称`、`【伏笔】内容` 标记关键事实，应用会记录来源章节、原文引句、范围和确认状态；确认后才会建立正典 assertion，避免把模型猜测直接写进正典。上下文接口提供有界的章节召回、材料选择、故事结构和来源预览，并报告估算 token；当前不是百万 token 上下文，也没有宣称完整向量检索。后续可在此基础上加入 embedding、重排、实体时间硬过滤和真实长篇评测。

开源设计参考见 `docs/design-references.md`；与六个参考项目的逐项功能对比见
[`docs/comparison-six-projects-2026-10-12.md`](docs/comparison-six-projects-2026-10-12.md)。

## AI 创作助手（可选）

启动服务后，打开“章节 + AI 写作”即可使用候选式创作助手。它支持续写、场景卡、大纲、发散、润色、总结和连续性检查；生成结果先显示在候选区，只有点击“采纳”才会写入正文。未配置模型时也能使用离线写作卡，不会暗中联网。

第一次使用从首页“开始创作”进入：填写一句话故事设定、类型、主角和眼前阻力，墨屿会建立第一章和初始设定。之后沿着“填写任务卡 → 写作 → 生成候选 → 人工采纳 → 确认记忆 → 审查本章 → 完成本章 → 自动准备下一章”的路径推进，首页会持续显示下一步行动。写完后从“终稿检查”标记全书完稿，作品会进入收尾入口；需要修改时可以恢复写作。

具体操作：进入“章节 + AI 写作”后，先填本章目标、视角、场景计划和章节出口；写作过程中可点“上下文预览”查看本次工作集的估算 token、近章/召回章节和记忆来源。点击“生成候选”后先编辑结果，只有点“采纳”才会追加或替换正文。每次生成都会得到 `ai_run_id`，采纳或清空候选会尝试写入后端决策接口；旧服务没有该接口时会写入本机审计记录，接口失败不会丢失候选。正文中的 `【人物】`、`【物品】`、`【地点】`、`【伏笔】` 标记会出现在“记忆中枢”，卡片显示原文摘录、章节/字符范围和来源状态，可确认、拒绝或重新审查；来源缺失时不要把候选当成正典。编辑器的“历史版本”可以查看 revision 并恢复为新版本，恢复后旧来源片段会要求重新确认。写完后点“审查本章”，再点“完成本章，写下一章”。

顶部“终稿检查”是导出的硬门：未完成章节、未通过审查、未确认记忆、失效证据、未回收伏笔和明显空稿会阻断 Markdown/EPUB 终稿导出，直接调用 EPUB API 也会再次检查。作者确实要带着问题交付时，可以点击“作者覆盖并记录原因”，填写至少 8 个字符的理由；理由与当前稿件指纹一起保存在本机，EPUB 包内也会写入覆盖记录，稿件发生变化后需要重新覆盖。JSON 备份仍可用于保留工作稿，不绕过终稿门。

如需接入 OpenAI 兼容接口，请在启动前配置环境变量：

```bash
MOYU_AI_BASE_URL=https://api.example.com/v1 \
MOYU_AI_MODEL=your-model \
MOYU_AI_API_KEY=your-key \
python3 server.py
```

本地 Ollama 可使用 `http://127.0.0.1:11434/v1`。远程地址必须使用 HTTPS；密钥只从进程环境读取，不写入 SQLite、浏览器缓存或导出 JSON。每次请求只发送当前工作集、相邻章节片段、设定卡和已确认记忆，完整作品仍留在本机。模型返回会先经过大小和完成原因检查，仍需作者人工审阅。当前上下文是有界召回，不是百万 token 窗口；参考项目、许可证边界、第三方审查与剩余缺口见 [`docs/third-party-audit.md`](docs/third-party-audit.md)。

## 可复现评测 runner

评测输入是固定 JSON fixture，runner 会同时计算 K=1、5、10 的证据 Recall/Precision，输出指标快照并执行发布门槛。所有指标分母为 0 时标记 `NA`；未实测门槛不会通过。失败 fixture 返回非零退出码：

```bash
python scripts/evaluation_runner.py \
  --fixtures-dir tests/fixtures \
  --output artifacts/evaluation.json \
  --pretty
```

`tests/fixtures/schema_scope.json` 运行 SQLite schema 负向 fixture，`corrupt_import.json` 和 `reversible_patch.json` 分别覆盖损坏导入拒绝与 AI patch 撤销。单个 JSON 可用 `--fixture path/to/fixture.json` 运行。

严格功能边界回归可运行 `python tests/strict_feature_fixtures.py`：它验证无证据记忆不能确认、带来源记忆会生成正典断言、AI 决策可追溯，以及模型失败会写入 `failed` 运行记录。

## SQLite 备份、恢复与故障演练

`app.js` 的浏览器 JSON 导出仍可用于便携备份；使用 SQLite 的部署请通过 Online Backup API（或 `VACUUM INTO`）生成快照，不要在数据库运行时直接复制 `.sqlite` 文件。仓库提供了可审计的 Python 脚本：

```bash
# 默认使用 sqlite3.Connection.backup，目标文件和 JSON 清单均原子替换
python3 scripts/sqlite_backup.py backup data/app.sqlite backups/app.sqlite

# 需要 VACUUM INTO 时显式选择；--force 才会覆盖已有目标
python3 scripts/sqlite_backup.py backup data/app.sqlite backups/app.sqlite \
  --method vacuum --force

# 检查格式、quick_check、integrity_check、外键、页数以及清单 SHA-256
python3 scripts/sqlite_backup.py verify backups/app.sqlite

# 恢复前先验证清单中的 SHA-256；恢复到临时文件后再次校验，再原子替换目标
python3 scripts/sqlite_backup.py restore backups/app.sqlite data/recovered.sqlite \
  --report artifacts/restore.json
```

每个备份旁边会生成 `*.manifest.json`，包含 `quick_check`、`integrity_check`、`foreign_key_check`、数据库 SHA-256、`user_version`、备份方法以及 RPO 记录。默认 RPO 以源文件 mtime 作为保守估计；应用可通过 `--last-commit-at` 传入最后一次提交时间，清单会将其标记为 `measured`。恢复报告包含 `rto_seconds`。损坏、截断、页数不一致、完整性检查失败或清单摘要不匹配都会在目标文件被触碰前拒绝。

运行可重复的故障演练（生成 fixture、损坏副本、确认拒绝，再恢复有效副本）：

```bash
python3 scripts/recovery_drill.py --output artifacts/recovery-drill.json
# 使用现有数据库并保留演练产物
python3 scripts/recovery_drill.py --database data/app.sqlite \
  --work-dir artifacts/recovery --output artifacts/recovery.json
```

演练退出码为 0 的条件是：损坏备份被拒绝、有效备份恢复成功、恢复后的 schema/user_version/各表行数快照与备份一致，并且报告含有限的非负 `rpo_seconds`/`rto_seconds`（自动生成的 fixture 还会校验哨兵记录）。可运行 fixture：

```bash
python3 tests/backup_recovery_fixtures.py
```

发布门槛与指标格式见 `docs/evaluation-spec-v1.md`（RPO ≤ 300 秒、RTO ≤ 900 秒）。
