# 墨屿 · 小说创作台

一个本地优先的中文长篇小说创作工具。作品、正文版本、设定、记忆证据和任务通过 SQLite（保存在本机的数据库文件）保存，作者在页面中完成规划、写作、局部改稿、记忆确认、备份和导出。

## 当前状态

2026-10-13 的审查已进入产品修复，最新交付记录更新至上海时间 2026-10-14；审查目录继续沿用 `2026-10-13`。本轮产品验收以电脑端为范围，已有手机证据作为历史补充。保存冲突、候选身份、证据失效、任务分片、普通正文章后记忆、四阶段规划、作品备份和导入导出均有实现与指定快照的本机回归证据。七项目对照仍属于源码和组件审查，**第一梯队是持续目标，尚未证明达到**。

最新[电脑端交付复验报告](docs/audits/2026-10-13/evidence/compatibility-python311-node24-desktop-release-final.json)在上海时间 2026-10-14 00:11:10 至 00:13:55 完整执行：Python 3.11.17 / Node 24.19.0 的50个执行项全部通过，包含35份Python测试脚本、8项JavaScript语法、固定评测和12项编辑器合并检查；提供方协议45项、队列54项、文学工具62项、输出检查34项通过。真实浏览器的37个基础场景、5个规划场景和7个五章作者场景合计49个场景通过，另含连续20轮写作和五章10次专用候选检查。64份源码在运行前后指纹相同；三份浏览器结果、固定评测和八张截图使用真实 `desktop-release-final` 路径，无需改名映射。撤销与重做保留另一窗口改稿，同起点插入和删改保留双方供选择；作品、分支和章节历史隔离，输入法组合输入及冲突时拒绝不安全撤销。范围与历史证据见[验证说明](docs/audits/2026-10-13/evidence/README.md)；后续修改仍须新快照。

实际文学试评仍没有有效对照成绩。最新[第十二轮](docs/audits/2026-10-13/literary-pilot-v12/report.md)仅实际执行首个悬疑题的一对候选：基线942字、墨屿868字，均返回 `glm-5.1`、正常结束且未检出明显思考标记，但均超出未改变的600至850字要求。输出合格0/6、有效评审0/12，未扩其余两题、未发送盲评；计划/实际返回/输出合格/完成评审题材为3/1/0/0。相比第十一轮只改墨屿系统要求中的一句收束提示，基线请求字节、作者要求、素材和参数均保持相同，单对差异不能证明文学改善。含独立协议诊断累计31条请求预留、49642个可信完整 tokens（模型用量计数单位），已知下界同为49642；10条缺少可信完整用量，实际金额未知。原始候选、请求和账本保持不变，详见[文学评测说明](docs/evaluations/README.md)。

远程 GitHub Actions（GitHub 自动运行的检查）在基线 `62f1c66` 的旧版配置上已有成功记录；本轮增强版使用 Node 24，等待推送后的实际运行结果，本机通过不能代替远程通过。当前没有公网部署验收结论。修复和剩余验收见[路线图](docs/audits/2026-10-13/roadmap.md)，原始失败与当前入口见[审查报告](docs/audits/2026-10-13/README.md)。

## 运行

推荐使用已验证的 Python 3.11 启动本地服务。应用运行只用 Python 标准库，无需安装第三方运行依赖：

```bash
python3 server.py
```

然后打开 `http://127.0.0.1:4173`。保持服务运行即可在浏览器中使用。

服务首次启动会在项目目录创建 `moyu.sqlite3`，按顺序应用 `docs/schema-v1.sql` 和
`docs/schema-v1.1-migration.sql`（`PRAGMA user_version=11`），再安装工作台扩展表。可以用环境变量指定数据库或端口：

```bash
MOYU_DB=/path/to/story.sqlite3 MOYU_PORT=4173 python3 server.py
```

默认监听 `127.0.0.1`，供单人本机使用。浏览器写请求检查同源 `Origin`/`Referer`，跨站和 `Origin: null` 被拒绝；只允许访问列出的页面资源，不直接提供数据库和源码。**当前没有登录、用户权限或公网部署保护**；作品隔离不能代替账号权限。改为局域网或公网监听前需要另行完成访问控制和部署验收。

[`.env.example`](.env.example)列出可用的启动配置，不含真实密钥；服务不会自动加载 `.env`。一般作者可直接在“模型与风格”页面配置，不必编辑环境文件。

## 数据保存

| 内容 | 默认位置与范围 |
| --- | --- |
| 作品、正文历史、设定、规划、证据、任务和审计 | 项目目录的 `moyu.sqlite3`；`MOYU_DB` 可指定其他文件 |
| 完整作品 ZIP 备份 | 数据库所在目录的 `backups/`；包含选中作品，不含其他作品和密钥 |
| 页面保存的模型密钥 | `~/.moyu/credentials/`；目录权限 `0700`，文件权限 `0600`，SQLite 只存随机引用 |
| 浏览器草稿和界面缓存 | 浏览器本地存储，按作品隔离；不能代替完整作品备份 |
| 导入原文件与来源记录 | 作品数据库中保存原始字节、内容指纹和解析损失说明 |

密钥可用 `MOYU_CREDENTIALS_DIR` 指定受控目录。换设备、复制或恢复为独立作品后需重新配置密钥；备份不会携带密钥和旧连接测试结论。数据库和备份目录已被 Git 忽略，不随源码推送。

## 本地 API

API（页面与本机服务交换数据的入口）由页面使用，作者无需手动调用。作品请求携带 `X-Moyu-Project` 和分支范围；这些范围只隔离作品数据，不是用户身份认证。

- `GET /api/health`：服务与 schema 版本
- `GET/POST /api/projects`、`PATCH /api/projects/:id`、`POST /api/projects/:id/duplicate`：书架、新建、归档/恢复和独立复制
- `GET /api/state`：读取当前作品的完整 UI 状态
- `PUT /api/state`：事务性全量同步（请求必须带 `full_state:true` 和完整数组；省略项会写入删除/归档投影）
- `GET /api/chapters`、`POST /api/chapters`、`PATCH /api/chapters/:id`、`DELETE /api/chapters/:id`：章节 CRUD；删除写入 `deletion_event`，正文修改会追加 `document_revision` 和 `event_log`。创建、更新、恢复、删除可在 JSON 中携带 `expected_head`（或 `X-Moyu-Branch-Expected-Head`）启用分支 head CAS，过期写入返回 409
- `POST /api/chapters/:id/patch`：范围级候选 patch；请求携带 `start/end`（Unicode codepoint）、`replacement`、`base_revision_id` 或 `base_hash`，默认只预览 diff；加 `apply:true` 后在 CAS 校验下创建新 revision、标记旧证据 stale 并追加 `chapter.range_patched` 事件
- `POST /api/chapters/:id/restore`、`POST /api/records/:id/restore`：恢复章节 tombstone 或归档设定并记录事件
- `GET/POST /api/records`、`PATCH/DELETE /api/records/:id`（记忆、伏笔同样支持 `/api/memory`、`/api/plot`）：最小 CRUD 投影，并为变更写入 `event_log`
- `GET /api/events`、`POST /api/events`：查看或写入幂等事件
- `GET/PUT /api/blueprint`、`POST /api/blueprint/materialize`：保存作品蓝图、卷章场景和规划版本，按稳定身份建立章节，重复请求不重复建章
- `GET/POST /api/planning-workflows`、`GET /api/planning-workflows/:id`、`POST /api/planning-workflows/:id/start|confirm|cancel|apply`：四阶段候选规划、作者确认、停止和完整采用；最终采用只更新蓝图
- `GET/PUT /api/ai/settings`、`POST /api/ai/settings/probe`：作品模型、用途模型、风格、参数和每日次数设置；连接测试是真实模型调用，消耗额度
- `GET /api/context`、`POST /api/ai/prompt-preview`、`POST /api/ai`：查看有界工作集、预览任务契约并生成候选；支持 `retrieval_profile` 配置预算、章节召回数、近章窗口和记忆/设定上限，并返回分层计数与证据来源
- `GET /api/ai/status`、`GET /api/ai/quota`、`GET /api/ai/runs/:id`、`GET /api/ai/runs/:id/stream?after_seq=N`、`POST /api/ai/runs/:id/retry|cancel`：查看模型、用量和运行审计；SSE（服务持续分段发送文本）支持实时分片与按序号重放，每块先落盘；非流式返回、离线、截断、取消和未知结果分别标记
- `POST /api/ai/queue` 或 `/api/ai` 携带 `async:true`：将请求持久化为队列任务；`GET /api/ai/queue`、`GET /api/ai/jobs/:id` 查看状态，`process|cancel|retry` 控制单个任务，`queue/drain` 恢复人工或重启后的积压任务
- `POST /api/ai/runs/:id/apply`：在同一事务中验证作品、分支、章节、正文版本、范围与作者决策，应用一次候选；旧稿或错误范围拒绝，重复请求不重复追加正文
- `GET /api/chapter-analysis`、`POST /api/chapter-analysis/:id/confirm`：普通正文章后摘要、交接和记忆候选的回读与逐项确认
- `GET /api/story-reviews`、`POST /api/story-reviews/:id/issues/:issue_id/decision`：故事审稿和作者采纳、忽略、解释记录；接受建议不等于已经修好正文
- `GET /api/search?q=...&explain=1`：搜索章节、设定、记忆、伏笔和时间线；使用 CJK FTS/BM25 与新近度、证据分数的可解释 RRF 融合，支持项目/分支、章节、实体、状态和时间硬过滤，结果保留来源章节与范围元数据；`explain=1` 返回各信号排名和过滤原因
- `GET /api/doctor`（兼容别名 `/api/projection-health`、`/api/health/projections`）：只读检查 state/event、分支 head、搜索索引、AI 队列和章节提交投影的新鲜度、失败与缺口
- `GET /api/story-map`、`POST /api/relationships`、`POST /api/timeline`、`POST /api/foreshadow-links`：读取或写入关系、时间线、伏笔生命周期和冲突投影；故事结构页提供关系 SVG 图谱、时间线总览和当前章节人物状态知识范围审查
- `GET/POST/PATCH/DELETE /api/character-states`、`/api/plot-threads`：管理带时间范围、知识范围和证据链接的人物状态与剧情线
- `GET /api/assertions`、`GET /api/assertions/:id`、`POST /api/assertions/:id/review`、`GET /api/memory/:id/evidence`：审查正典事实、冲突、依赖和失效证据
- `GET /api/chapters/:id/revisions`、`POST /api/chapters/:id/restore-revision`、`GET /api/chapters/:id/commits`、`POST /api/chapters/:id/commit`：查看差异、恢复为新 revision，并执行章节提交阶段
- `GET/POST /api/backups`、`GET /api/backups/:id/download`、`POST /api/backups/:id/restore`：完整作品备份、下载、校验后恢复为独立作品
- `POST /api/import/preview`、`POST /api/import/apply`、`GET /api/references`、`GET /api/references/:id`、`GET /api/references/:id/search?q=...`：TXT/MD/EPUB 分章预览、导入独立稿件或参考库、来源检索；导入不自动发送收费分析
- `POST /api/assistant/query`、`POST /api/assistant/confirm`：有限任务的只读预览与作者确认后创建候选；正文仍需另行采纳
- `GET /api/export/markdown`、`GET /api/export/epub`、`GET /api/export/manifest`：导出当前稿件及范围/损失清单；内嵌原稿与指纹，墨屿可精确回读正文；直连也受终稿门保护，作者覆盖需带 `X-Moyu-Export-Override`

伏笔投影的 `DELETE /api/plot/:id` 会归档条目并记录事件；章节和设定使用上面的专用 restore 路径恢复。

章节正文采用追加式 revision，数据库中的 `document_revision`、`event_log` 和
`candidate_memory` 遵循 schema 的外键、作用域及 append-only 触发器约束。

仅做静态预览时可以运行 `python3 -m http.server 4173`。API 不可用时页面使用浏览器本地缓存；书架、任务、完整备份等服务功能不具备，正式创作请使用 `server.py`。

## 当前功能

- 多作品书架、新建、切换、复制、归档/恢复，作品总览、字数目标和章节进度
- 蓝图、卷/章/场景规划、四阶段模型候选与逐阶段确认、自动保存、移动和按规划建立章节
- 章节编辑器、草稿与已确认快照、自动保存、两窗口冲突合并、按作品/分支/章隔离的历史版本，以及保留另一窗口改稿的撤销和重做
- 章节任务卡（目标、视角、场景计划、伏笔动作、回顾、下一章简报）与完成前质量审查
- 任务中心、最多两个并行模型任务、逐块保存、取消、重连和显式重试
- 选区润色、候选差异、范围采纳；中文和 emoji 按字符范围转换，长章未选范围不会整章覆盖
- 人物、地点、势力、物品等设定卡片
- 情节与伏笔看板
- 全文搜索
- 普通正文的章后摘要、交接、事件、人物状态、关系与伏笔候选，原句预览和作者确认
- 故事审稿及作者处理记录，正文或材料变更使旧结果过期
- JSON 稿件导入/导出、完整作品 ZIP 备份、校验和独立恢复
- TXT/MD/EPUB 导入预览、拆分/合并/改名、原始来源留存和参考库搜索
- Markdown/EPUB 终稿导出、原稿指纹、正文回读及损失清单
- 有界上下文预览（估算 token、可配置检索层、来源 ID/范围）与 AI 运行记录
- AI 材料 pin/omit/order、完整 Prompt 契约预览与结构化 JSON 任务结果
- 上下文预算会记录裁剪原因和被省略来源；固定材料不会被静默裁剪，固定集合超出预算时请求会明确阻断
- 人物关系、时间线、伏笔生命周期和冲突查询
- 章节 `prewrite → precommit → postcommit` 提交快照、revision diff 与恢复前预览
- 模型与风格设置、用途模型、真实连接测试、私密凭据保存和每日生成次数限制
- 模型原生 SSE 解析、带序号的输出账本、断点重放、结束原因检查和已完成运行取消保护
- 有限自然语言项目助手：只读搜索与任务预览，确认后创建候选
- 全书完稿状态：终稿检查后可标记完成，状态持久化并支持恢复写作
- 已有移动端适配；本轮验收和继续开发以电脑端为范围

## 设计原则

采用单用户、本地优先、作者确认的工作流。未配置模型时保持离线，配置后只在作者触发任务时发送有界材料。所有正文修改追加版本，候选不能越作品或替换已经变化的原稿；模型猜测不会自动写成正典。

一般全文搜索使用 SQLite CJK FTS/BM25 和多信号排序；实际 AI 材料由 `moyu_context.assemble_context` 使用本地段落检索统一组装，并检查作品、分支、故事时点、视角、事实与作者计划边界。两者不是同一持久化全文索引。片段保留版本、字符范围和指纹；固定材料超预算时阻断，不静默裁掉。

目前没有向量检索、真实模型分词计数、外部多进程任务调度、定时自动备份、提示模板版本库、DOCX/PDF、团队权限或完整 EPUB 版式往返。四阶段蓝图与五章伏笔作者链已有指定快照的真实电脑端回归；真实模型规划质量和审稿准确率仍未正式量测。项目助手是有限任务预览，不是自主修改作品的开放式代理。

## 长篇记忆

“记忆中枢”的“提取当前章记忆”直接分析普通正文，不要求作者插入标签。返回摘要、下一章交接和有原句范围的事件、状态、关系、伏笔等候选；作者可修改候选说明并逐项确认或拒绝。确认后的有效事实进入后续章节材料，同一正文版本重复提取和确认不重复建立事实。正文改变、来源删除或旧任务迟到会让相关材料失效，不能继续作为正典使用；作者手写的交接不会被过期自动摘要覆盖。

原有 `【人物】`、`【物品】`、`【地点】`、`【伏笔】` 标记仍可使用。章后提取和故事审稿都需要作者复核，空报告、失败或过期不算通过。

固定合成小说的 30/100/300 章实际 HTTP 测试捕获了最终发给模拟模型的材料。每种规模五条标注查询，Recall@1/5/10（应找出的原句实际被送入的比例）均为 1.0，来源引用准确率为 1.0，非法材料为 0，见[原始结果](docs/audits/2026-10-13/evidence/context-provider-recall-actual.json)。这些数字只适用于该合成数据；延迟包含本机模拟模型，token 为 UTF-8 字节保守估算，不能推断真实小说效果、远程模型延迟或无限上下文能力。

开源设计参考见 `docs/design-references.md`；最新七项目逐项对照见
[能力矩阵](docs/audits/2026-10-13/capability-matrix.md)，早期六项目对比保留在
[`docs/comparison-six-projects-2026-10-12.md`](docs/comparison-six-projects-2026-10-12.md)。

## AI 创作助手（可选）

启动服务后，打开“章节 + AI 写作”即可使用候选式创作助手。它支持续写、场景卡、大纲、发散、润色、总结和连续性检查；生成结果先显示在候选区，只有点击“采纳”才会写入正文。未配置模型时也能使用离线写作卡，不会暗中联网。

第一次使用从首页“开始创作”进入：填写一句话故事设定、类型、主角和眼前阻力，墨屿会建立第一章和初始设定。之后沿着“填写任务卡 → 写作 → 生成候选 → 人工采纳 → 确认记忆 → 审查本章 → 完成本章 → 自动准备下一章”的路径推进，首页会持续显示下一步行动。写完后从“终稿检查”标记全书完稿，作品会进入收尾入口；需要修改时可以恢复写作。

“创作规划”的“分阶段规划”按总纲、卷、章、场景四步生成候选，每步由作者确认后才进入下一步。规划固定原蓝图版本、内容指纹和实际请求；确认后的阶段供下一阶段使用，明确失败时可手动只重做当前阶段。执行状态未知的请求保留记录，不自动或手动重发同一未知阶段；原蓝图变化会阻止旧规划继续写入。四阶段确认后，“采用完整规划”只更新蓝图，建立章节仍由作者另行触发，已写正文不会被规划结果覆盖。重复刷新未变的状态或错误不重复追加记录。

规划输入会持续自动保存。保存失败时保留独立的本地规划草稿，刷新后可继续保存；另一个窗口同时改同一项时保留双方文字，由作者选择后保存。采用规划期间暂时锁定规划输入，切页返回仍保持锁定，回应结束后继续编辑；第二套规划建立章节只增加新章，保留旧章节的标题、正文和任务卡。

具体操作：进入“章节 + AI 写作”，填写本章目标、视角、场景计划和章节出口；可用“上下文预览”检查材料。生成后先比较和编辑候选，再点“比较并采纳”。局部改稿先选中原文再润色，采纳会检查原稿版本和范围；生成期间改稿或切章后，旧结果不能写到错误位置。“生成任务”可查看进度、取消、续接和处理失败，任务结果只采纳一次。

写完当前章，打开“章后记忆”逐项确认原句证据，再打开“本章审稿”处理问题。完成本章后准备下一章；“历史版本”可恢复为新版本，相关旧来源与审稿须重新确认。接受审稿建议只是记录作者决定，正文修改后仍要重新审查。

章后记忆与故事审稿完成后，候选栏显示对应任务名和只读摘要；点击“查看章后记忆”或“查看故事审稿”进入逐项处理，不把分析结果当正文采纳。普通续写和润色仍可编辑候选、比较后采纳。

“情节与伏笔”的“状态与原句”可选择已有来源记录重新确认，保留原记录身份。删除回收原句后，终稿会阻断；恢复文字仍需作者重新确认该来源，再重新进行基础审查。只把卡片移到“回收”而没有有效回收原句，也不能通过终稿。

顶部“终稿检查”向服务读取当前稿件的实际导出条件：未完成章节、未通过审查、未确认记忆、失效证据、未回收伏笔和明显空稿会阻断 Markdown/EPUB 终稿导出，直接调用导出 API 也会再次检查。作者确实要带着问题交付时，可以点击“作者覆盖并记录原因”，填写至少 8 个字符的理由；理由与当前稿件指纹一起保存在本机，EPUB 包内也会写入覆盖记录，稿件发生变化后需要重新覆盖。全书完稿后保存状态并禁止再归档创建下一章，恢复写作后可继续。JSON 备份仍可用于保留工作稿，不绕过终稿门。

在“模型与风格”填写兼容接口地址、模型名称和密钥，保存后可执行“连接测试”。保存设置不等于连接健康；连接测试会真实调用模型并可能产生费用。用途模型、温度、输出长度、风格与写作要求可按作品保存，温度 `0` 会按原值发送。也可通过 `MOYU_AI_BASE_URL`、`MOYU_AI_MODEL`、`MOYU_AI_API_KEY` 提供未保存设置时的默认配置。

本地 Ollama 地址可用 `http://127.0.0.1:11434/v1`，远程地址要求 HTTPS。网页保存的密钥进入本机私密文件，公开回读只显示“已保存密钥”，不会放进浏览器缓存、作品数据库正文、任务公开数据或备份。每次只发送本次工作集，完整作品留在本机。

`length`、缺少完成标记、错误结构或未知结束原因不算完整候选，不能直接采纳。取消会关闭本机连接，无法保证远端同步停止或不收费。可能已发送的未知请求不自动重复；每日额度限制生成次数，未知用量和价格不能当作零，也不是人民币费用保证。真实调用的协议兼容性和文学质量仍须分别检验。

参考项目、许可证边界和第三方审查见[第三方审查](docs/third-party-audit.md)。

## 可复现评测 runner

固定 JSON 测试样本由 runner（自动执行测试并整理结果的脚本）计算 K=1、5、10 的证据 Recall/Precision，输出指标快照并执行门槛。分母为 0 标记 `NA`；未实测门槛不会通过。失败样本返回非零退出码：

```bash
python3 scripts/evaluation_runner.py \
  --fixtures-dir tests/fixtures \
  --output artifacts/evaluation.json \
  --pretty
```

`tests/fixtures/schema_scope.json` 运行 SQLite schema 负向 fixture，`corrupt_import.json` 和 `reversible_patch.json` 分别覆盖损坏导入拒绝与 AI patch 撤销。单个 JSON 可用 `--fixture path/to/fixture.json` 运行。

严格功能边界回归可运行 `python3 tests/strict_feature_fixtures.py`：它验证无证据记忆不能确认、带来源记忆会生成正典断言、AI 决策可追溯，以及模型失败会写入 `failed` 运行记录。

文学试评另用 `scripts/literary_evaluation.py`，固定故事、材料、模型、参数和实际请求，并记录发送意图、用量及未知费用；候选匿名双顺序评判。默认六题试评不能证明第一梯队，也不等于运行七竞品。普通命令不调用收费模型，真实执行需要明确的收费执行开关，详见[评测说明](docs/evaluations/README.md)。

## 验证

仓库的[自动检查配置](.github/workflows/ci.yml)使用 Python 3.11 / Node 24，在推送和拉取请求时执行语法、全部 `tests/*_fixtures.py`、固定评测、编辑器合并、基础浏览器回归、规划保稿检查和五章作者流程。Node（执行页面自动检查所需的开发工具）只用于开发验证，不是运行产品的依赖。浏览器回归使用 Playwright 1.62.1（自动点击真实浏览器的测试工具），本机模拟提供方，不使用真实密钥。历史 Node 20 证据保留原运行版本，不能当作 Node 24 或增强版远程检查已经通过。

```bash
python3 -m py_compile *.py scripts/*.py tests/*.py
for fixture in tests/*_fixtures.py; do python3 "$fixture" || exit 1; done
node tests/editor_sync_fixtures.cjs
node tests/frontend_browser_regressions.cjs
node tests/planning_browser_regressions.cjs
node tests/desktop_author_flow_regressions.cjs
```

浏览器测试需安装 Playwright 与浏览器；`MOYU_PLAYWRIGHT_MODULE` 可指定模块路径，`MOYU_TEST_PYTHON` 可指定启动服务的 Python。检查配置存在和本机通过都不能替代远程 GitHub Actions 的执行结果。

## 备份、恢复与导入

作者在“备份中心”点击“完整备份”，可查看清单、下载 ZIP，恢复时建立独立作品并保留原稿。完整备份包括正文、版本、设定、规划、人物状态、关系、时间线、伏笔、原句证据、任务、审计及参考来源；通过一致数据库快照、内容指纹、行数和外键检查后恢复。运行中或待执行的外部模型任务会标为失败或执行未知，不自动再次收费。**当前没有定时自动备份**。

规划恢复保留冻结候选、已采用历史、原蓝图与结果版本及作品身份映射；原始规划候选与其内容指纹不改写。恢复副本里的历史规划只供阅读，不继续确认未采用候选或再次调用模型；排队、运行中或执行未知的规划请求均保留为未知。要在副本中继续模型规划，需重新创建规划，历史采用结果不会再次写入正文。

“旧书与参考库”支持 TXT/MD 的 UTF-8/GB18030 编码和 EPUB 可见文字提取，先预览再拆分、合并、改名，导入独立作品或当前作品参考来源。参考来源不写成本书正典。原文件上限 32 MB、最多 2000 章、每章最多 200 万字符；EPUB 解压合计上限 128 MB、单文件 16 MB。第三方 EPUB 的版式、字体、图片等损失会列出；墨屿自身 MD/EPUB 导出的正文原文可精确回读，仍不等于完整版式往返。出版原稿当前上限 16 MB，超限须分卷，尚无 DOCX/PDF。

浏览器 JSON 导出用于便携稿件备份，不能替代完整作品 ZIP。下列 SQLite 脚本供维护和故障演练使用，不要求作者手动执行：

数据库运行时用 Online Backup API 或 `VACUUM INTO` 生成快照，不直接复制正在使用的数据库文件。

```bash
# 默认使用 sqlite3.Connection.backup，目标文件和 JSON 清单均原子替换
python3 scripts/sqlite_backup.py backup moyu.sqlite3 backups/moyu.sqlite

# 需要 VACUUM INTO 时显式选择；--force 才会覆盖已有目标
python3 scripts/sqlite_backup.py backup moyu.sqlite3 backups/moyu.sqlite \
  --method vacuum --force

# 检查格式、quick_check、integrity_check、外键、页数以及清单 SHA-256
python3 scripts/sqlite_backup.py verify backups/moyu.sqlite

# 恢复前先验证清单中的 SHA-256；恢复到临时文件后再次校验，再原子替换目标
python3 scripts/sqlite_backup.py restore backups/moyu.sqlite backups/recovered.sqlite \
  --report artifacts/restore.json
```

每个备份旁边会生成 `*.manifest.json`，包含 `quick_check`、`integrity_check`、`foreign_key_check`、数据库 SHA-256、`user_version`、备份方法以及 RPO 记录。默认 RPO 以源文件 mtime 作为保守估计；应用可通过 `--last-commit-at` 传入最后一次提交时间，清单会将其标记为 `measured`。恢复报告包含 `rto_seconds`。损坏、截断、页数不一致、完整性检查失败或清单摘要不匹配都会在目标文件被触碰前拒绝。

运行可重复的故障演练（生成 fixture、损坏副本、确认拒绝，再恢复有效副本）：

```bash
python3 scripts/recovery_drill.py --output artifacts/recovery-drill.json
# 使用现有数据库并保留演练产物
python3 scripts/recovery_drill.py --database moyu.sqlite3 \
  --work-dir artifacts/recovery --output artifacts/recovery.json
```

演练退出码为 0 的条件是：损坏备份被拒绝、有效备份恢复成功、恢复后的 schema/user_version/各表行数快照与备份一致，并且报告含有限的非负 `rpo_seconds`/`rto_seconds`（自动生成的 fixture 还会校验哨兵记录）。可运行 fixture：

```bash
python3 tests/backup_recovery_fixtures.py
```

完整恢复的逐表核验、损坏包保护和恢复后继续写作见 `tests/import_archive_lifecycle_fixtures.py`；网页恢复见前述浏览器证据。RPO（可能丢失的最近写作时间）≤ 300 秒、RTO（恢复所需时间）≤ 900 秒是[验收目标](docs/evaluation-spec-v1.md)，不是所有真实规模已经达到的承诺。
