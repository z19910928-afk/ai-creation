# 墨屿 · 小说创作台

一个本地优先的中文长篇小说创作工具。作品、正文版本、设定、记忆证据和任务通过 SQLite（保存在本机的数据库文件）保存，作者在页面中完成规划、写作、局部改稿、记忆确认、备份和导出。

## 当前状态

2026-10-13 的审查已进入产品修复，最新交付记录更新至上海时间 2026-10-14；审查目录继续沿用 `2026-10-13`。本轮及后续产品验收只以电脑端为范围，手机端不在当前目标内。保存冲突、候选身份、证据失效、任务分片、普通正文章后记忆、四阶段规划、作品备份和导入导出均有实现与指定快照的本机回归证据。七项目对照仍属于源码和组件审查，**第一梯队是持续目标，尚未证明达到**。

最新[候选整理电脑端完整复验](docs/audits/2026-10-13/evidence/compatibility-python311-node24-candidate-revision-final.json)在上海时间2026-10-14 02:03:08至02:06:19通过51个执行项，包含36份Python测试、8项JavaScript语法、固定评测和12项编辑器检查；候选整理保护19项、正文HTTP流程15项通过。真实浏览器43个基础、5个规划、7个五章场景合计55个电脑场景通过，包含20轮连续写作和五章10次专用候选检查。66份源码前后指纹相同，4份JSON和9张截图共13份附件指纹逐一核验；仅使用临时数据库与免费本机模拟模型。这份新快照覆盖本次整理，历史失败和旧成功均保留。

历史[电脑端交付复验报告](docs/audits/2026-10-13/evidence/compatibility-python311-node24-desktop-release-final.json)在上海时间 2026-10-14 00:11:10 至 00:13:55 完整执行：Python 3.11.17 / Node 24.19.0 的50个执行项全部通过，包含35份Python测试脚本、8项JavaScript语法、固定评测和12项编辑器合并检查；提供方协议45项、队列54项、文学工具62项、输出检查34项通过。真实浏览器的37个基础场景、5个规划场景和7个五章作者场景合计49个场景通过，另含连续20轮写作和五章10次专用候选检查。64份源码在运行前后指纹相同；三份浏览器结果、固定评测和八张截图使用真实 `desktop-release-final` 路径，无需改名映射。撤销与重做保留另一窗口改稿，同起点插入和删改保留双方供选择；作品、分支和章节历史隔离，输入法组合输入及冲突时拒绝不安全撤销。范围与历史证据见[验证说明](docs/audits/2026-10-13/evidence/README.md)；该快照不覆盖之后新增的候选整理代码。

实际文学试评仍没有有效对照成绩。[第十三轮](docs/audits/2026-10-14/literary-pilot-v13/diagnosis.md)只实际执行首个悬疑题的一对候选：基线1176字、墨屿5320字；两份均返回 `glm-5.2`，基线超出600至850字上限，墨屿以 `length` 截断且正文开头泄漏 `Let me understand the task`，因此输出合格0/12、有效评审0/24，没有扩展其余故事或发送盲评。本轮新增2条、4495个可信完整 tokens；全局累计37条请求预留、73118个可信完整 tokens，另有10条缺完整用量，实际金额未知。原始候选、请求和账本保持不变，详见[文学评测说明](docs/evaluations/README.md)与[v13诊断](docs/audits/2026-10-14/literary-pilot-v13/diagnosis.md)。

随后四轮新的电脑端 MiniMax-M3 探针继续验证输出协议：v14 第一条基线为1197字且有非空思考字段；v15 两条为1327/1201字；v16 在 `max_tokens=900` 下为783/1114字；v17 在 `max_tokens=560` 下为825字截断/859字完整超限。四轮新增7条请求、10954个可信完整 tokens，截至v17累计44条请求预留、84072个可信完整 tokens；没有一轮形成双方合格候选或盲评，第一梯队仍未证明。评测说明见[最新真实探针记录](docs/evaluations/README.md)。

随后 v18-v23 又进行了六轮只做电脑端的首题参数探针：v18（520 tokens）两份均截断；v19（560 tokens、目标约620字）墨屿762字完整但基线截断；v20（600 tokens）两份超限；v21（580 tokens、温度0.3）基线841字合格但墨屿截断超限；v22（580 tokens、目标约600字）两份截断超限；v23（560 tokens、温度0.3）两份均截断。按原始账本复核，v23 前累计55条请求预留、101017个已知 tokens；仍未形成双方合格候选、盲评或第一梯队证据。每轮完整账本与报告见[文学评测记录](docs/evaluations/README.md)。

随后产品提示新增“达到目标字数附近立即收束”的短篇续写要求。新的[v24 电脑端真实探针](docs/audits/2026-10-14/literary-pilot-v24-live-probe/report.md)在同一新冻结方案中实际发送两条非流式请求：基线1000字、墨屿879字，均为完整 `stop`、关闭思考且无非空思考字段，但都超过600至850字硬门；因此输出合格0/12、盲评0/24，按首对失败保护停止后续请求。v24 新增3012个可信完整 tokens；全目录账本复核为57条请求、104029个已知 tokens、10条用量未知，实际金额仍未知。新提示已完成真实验证但没有修复输出收束，第一梯队仍未证明。

后续[原候选整理诊断](docs/audits/2026-10-13/literary-revision-v1/diagnosis.md)为这两份失败首稿各追加一次整理，完整保留父稿并沿用相同冻结模型、参数、3000 tokens输出上限和600至850字要求。两份回复均为“铁”1字，均返回 `glm-5.1`、`stop`、完整协议，整理输出合格0/2；没有修复首稿，没有新增独立故事或发送盲评。新增4740和4747、合计9487 tokens，当前累计33条请求预留、59129个可信完整tokens与已知下界，仍有10条缺完整用量、实际金额未知。文学质量、事实安全、作者效率和第一梯队均未证明。

随后[回复模式诊断](docs/audits/2026-10-13/literary-transport-v1/report.md)在同一份墨屿整理输入上只切换为 `stream=false`；第一阶段返回正文“铁”1字，并观察到非空 `reasoning_content`。虽为 HTTP 200、`stop`、完整用量4747 tokens，仍因正文长度和非空思考字段失败，第二阶段未执行，也没有重试、截断或盲评。新增1条后累计34条请求预留、63876个可信完整tokens及已知下界，仍有10条缺完整用量，费用未知；该结果只说明协议异常复现，文学质量、事实安全、作者效率和第一梯队均未证明。

随后[相邻用户消息合并诊断](docs/audits/2026-10-13/literary-message-shape-v1/report.md)只把同一固定输入的两条相邻 `user` 消息合并为两个换行，实际执行第一阶段非流式请求一次。接口仍返回正文“铁”1字，并在关闭思考时返回非空 `reasoning_content`（计数1次、字符数5621）；HTTP 200、模型名、`stop` 和用量4747 tokens完整，但字数与思考一致性闸门失败，第二阶段未执行、没有重试或盲评。累计35条请求预留、68623个可信完整tokens及已知下界，仍有10条缺完整用量，费用未知；消息边界变化未消除异常，文学质量、事实安全、作者效率和第一梯队仍未证明。

工程修复历史提交 `eaca3586ddd2d8210f1ed62b1d1e22cd2c383886` 已上传GitHub分支 `codex/full-function-audit`。历史增强版运行 `38434003558`、候选整理提交运行 `38447803077` 和前一版运行 `38487730352` 均成功；代码提交 `e96e00c0e7bcebe9e045623a90cf970041bcc1bb` 的 GitHub Actions（GitHub自动运行的检查）运行 `38491492838` 于 UTC 2026-10-14 00:40:24 至 00:45:25 完成并成功，`checks` 作业的语法、契约与固定评测、编辑器合并、基础/规划/模型设置/五章电脑端回归步骤全部成功，见[代码提交远程成功记录](docs/audits/2026-10-13/evidence/github-actions-e96e00c-success.json)。这项远程工程结果仍只证明代码提交的自动检查，不证明文学质量、第一梯队、费用或公网部署；历史基线和中间失败原件继续保留。分支尚未合并至 `main`，没有公网部署验收结论。修复和剩余验收见[路线图](docs/audits/2026-10-13/roadmap.md)，原始失败与当前入口见[审查报告](docs/audits/2026-10-13/README.md)。

## 运行

推荐使用已验证的 Python 3.11 启动本地服务。应用运行只用 Python 标准库，无需安装第三方运行依赖：

```bash
python3 server.py
```

然后在电脑浏览器打开 `http://127.0.0.1:4173`。本次交付已实际启动本机服务，健康检查正常，并查看了真实编辑页面；保持服务运行即可使用。作品保存于下文的本机数据库，模型凭据保存在本机私有文件，不随源码上传。

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
- `POST /api/ai/runs/:id/revise`：作者确认整理未采纳的续写或润色原候选；保留父候选全文，冻结原模型、参数、输出预算、字数与稿件范围，为同一父候选建立一个独立计用量的子候选；重复请求返回已有任务，不重复生成
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
- 未采纳原候选的一次整理确认、父子候选回看、独立用量与重复保护；整理结果仍需作者比较后采纳
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
- 本轮验收和继续开发只覆盖电脑端；手机端不在当前目标内

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

未采纳的续写或润色原候选可点击“整理候选”。确认窗口显示原候选字数、原字数要求、来源章节与范围，并说明这次整理单独计入模型用量；可补充整理要求。整理完整保留父候选，沿用原任务冻结的模型、参数、输出预算、字数和原稿范围，产生独立子候选；每个父候选只建立一个子候选，重复提交返回已有任务。可回看原候选，只有作者明确比较并采纳后才写入正文，整理要求不能放宽原字数或改写已确认事实。

提交失联时先只读核对已有任务；模型执行或费用未知的请求不重发，恢复结果不自动采纳。公开整理关联由 `public_binding` 仅提供 `operation`、`parent_run_id`、`root_run_id`、`parent_characters` 四个字段，不包含候选全文、整理备注或 `request_id`。正文、来源、输出要求与明确采纳仍通过同一保护路径检查；这项功能的存在不代表真实模型能够完成合格整理。

若本次请求明确关闭思考，回复仍含非空思考字段，页面提示“回复与关闭思考设置不一致”，候选保留供阅读，采纳和整理均被阻止，不会自动重试。`reasoning_observation` 同时检查`reasoning_content`和`reasoning`，只保存全程字段次数、非空次数、异常类型次数和字符长度，不保存思考内容；计数不受128条协议形状记录的截断影响。非null、非字符串的思考字段会提示无法校验并阻断，不把未知形状称为已知非空文字。实际正文、结束原因、完成状态和用量保持提供方原值，协议完整仍须另外通过正文检查；空思考字段或明确启用思考不触发这项检查。

写完当前章，打开“章后记忆”逐项确认原句证据，再打开“本章审稿”处理问题。完成本章后准备下一章；“历史版本”可恢复为新版本，相关旧来源与审稿须重新确认。接受审稿建议只是记录作者决定，正文修改后仍要重新审查。

章后记忆与故事审稿完成后，候选栏显示对应任务名和只读摘要；点击“查看章后记忆”或“查看故事审稿”进入逐项处理，不把分析结果当正文采纳。普通续写和润色仍可编辑候选、比较后采纳。

“情节与伏笔”的“状态与原句”可选择已有来源记录重新确认，保留原记录身份。删除回收原句后，终稿会阻断；恢复文字仍需作者重新确认该来源，再重新进行基础审查。只把卡片移到“回收”而没有有效回收原句，也不能通过终稿。

顶部“终稿检查”向服务读取当前稿件的实际导出条件：未完成章节、未通过审查、未确认记忆、失效证据、未回收伏笔和明显空稿会阻断 Markdown/EPUB 终稿导出，直接调用导出 API 也会再次检查。作者确实要带着问题交付时，可以点击“作者覆盖并记录原因”，填写至少 8 个字符的理由；理由与当前稿件指纹一起保存在本机，EPUB 包内也会写入覆盖记录，稿件发生变化后需要重新覆盖。全书完稿后保存状态并禁止再归档创建下一章，恢复写作后可继续。JSON 备份仍可用于保留工作稿，不绕过终稿门。

在“模型与风格”填写兼容接口地址、模型名称和密钥，保存后可执行“连接测试”。保存设置不等于连接健康；连接测试会真实调用模型并可能产生费用。用途模型、温度、输出长度、风格与写作要求可按作品保存，温度 `0` 会按原值发送。也可通过 `MOYU_AI_BASE_URL`、`MOYU_AI_MODEL`、`MOYU_AI_API_KEY` 提供未保存设置时的默认配置。

连接检测必须完整结束、精确回复“连接正常”、回传名称与所选模型一致且符合思考设置，才能显示默认模型实测成功；名字一致仍不能证明底层模型身份。失败会持续显示双方名称和原因，历史未按现行条件验证的成功记录显示为未验证。检测或保存期间切作品、回读或修改设置，迟到回应不能覆盖当前设置，也不能继续外发旧检测；这些操作不会自动重试模型。

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

浏览器测试需安装 Playwright 与浏览器；`MOYU_PLAYWRIGHT_MODULE` 可指定模块路径，`MOYU_TEST_PYTHON` 可指定启动服务的 Python。检查配置存在和本机通过不能替代实际远程执行；代码提交 `e96e00c` 的[GitHub成功记录](docs/audits/2026-10-13/evidence/github-actions-e96e00c-success.json)已核对，后续源码变动仍应重新运行。

## 备份、恢复与导入

作者在“备份中心”点击“完整备份”，可查看清单、下载 ZIP，恢复时建立独立作品并保留原稿。完整备份包括正文、版本、设定、规划、人物状态、关系、时间线、伏笔、原句证据、任务、审计及参考来源；通过一致数据库快照、内容指纹、行数和外键检查后恢复。运行中或待执行的外部模型任务会标为失败或执行未知，不自动再次收费。**当前没有定时自动备份**。

恢复或复制产生的独立作品保留历史候选、父子整理关系、原请求指纹和用量。副本中的历史候选只供阅读，不能继续采纳、整理或重试，也不会自动调用模型；继续创作须按副本当前稿件生成新候选。原请求和父候选全文不因身份映射而改写。

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

## 最新文学诊断

在同一份冻结墨屿整理输入上，先后完成一次不分段回复和一次仅合并相邻用户消息边界的非流式诊断：`glm-5.1`、`thinking.type=disabled`、3000 输出 tokens、原600至850字门均未改变。两次均为 HTTP 200、模型名一致、`stop`、用量完整，但正文仍只有“铁”1字，并观察到非空 `reasoning_content`；第二次也未通过字数与关闭思考一致性闸门，因此没有继续发送稳定性请求或盲评。完整请求、字段统计和累计账本见[回复模式诊断](docs/audits/2026-10-13/literary-transport-v1/report.md)及[相邻消息合并诊断](docs/audits/2026-10-13/literary-message-shape-v1/report.md)。之后的 v13 诊断改测 `glm-5.2`，但未发送关闭思考参数，不能与此前结果混为同一对照。当前累计37条预留、73118个可信完整tokens，10条缺完整用量，费用未知；文学质量和第一梯队仍未证明。
