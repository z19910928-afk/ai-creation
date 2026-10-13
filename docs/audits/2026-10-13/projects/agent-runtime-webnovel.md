# 墨屿运行可靠性与 webnovel-writer 专责对标

审查日期：2026-10-13。专责代理：audit_runtime。本文是只读审查初稿，待其他专责代理交叉复核。

## 结论

墨屿已经有可使用的小说编辑器、章节修订、证据定位、作者材料选择、AI 候选采纳审计和持久任务基础，不能归类为只有界面的空壳。但本轮稳定复现了日常写作中的服务器保存中断、旧备份不能恢复已删除章节，以及 AI 输出完整性和调用预算问题。应先修复这些问题，再增加创作能力。浏览器仍保留本地草稿，并不等于服务器已保存，也不能据此宣称作品已经丢失或可靠性已经达标。

webnovel-writer 的两个产品线必须分开比较：master 是 Claude Code 插件与只读面板；v8 是依赖 DeepSeek Harness（DSH，负责模型会话和工具运行的桌面宿主）的本地写作工作台。v8 的创作阶段、审读结果指纹、归档收据与崩溃恢复很值得借鉴。本轮真实运行证实了其中两种文件事务恢复行为；没有运行完整 DSH 应用，也没有用真实模型生成或评审小说，不能据此证明它文学质量领先，或墨屿已经达到“第一梯队”。

## 快照、方法与证据边界

- 墨屿快照目录：`./.audit-cache/moyu-baseline`，提交 `62f1c66a7b16bb84f2940dc1af48e685aaf58818`。本代理开始和完成检查时 `git status --short` 均为空。
- webnovel-writer master：`./.audit-cache/2026-10-13/references/webnovel-writer-master`，固定提交 `0af0c0b2fc2e4104e8a12c1d62136c6595a6a983`，分支记录日期 2026-10-07，README 标示 v6.2.1。
- webnovel-writer v8：`./.audit-cache/2026-10-13/references/webnovel-writer-v8`，固定提交 `a0bdd2d4e2f39bc3de32fc5cbfe605067b5b486b`，分支记录日期 2026-10-12，README 标示 Scriptor 8.2.2，配套 DSH 0.2.0-rc.2。这是本次检查的固定快照，不对以后是否仍为最新作保证。
- 参考源码来自 GitHub API 的固定提交归档；网页和 git clone 网络通道受阻。分支元数据留在审查目录，便于复核版本身份。
- 所有墨屿 API 探针运行在临时 SQLite 数据库和本机模拟提供方上，不接触作者真实作品、真实密钥或收费模型。Python 子进程启动真实 `server.py`，通过 HTTP 调用实际端点。
- 前端探针通过 Node VM 运行墨屿原样 `normalizeState()` 和 `syncBackend()`；网络回应为受控模拟。它们验证真实函数行为，但不等于完整浏览器端到端测试。本代理未操作浏览器。
- v8 恢复探针直接加载原始 TypeScript 模块，子进程在第一个目标文件实际 rename 后退出，再调用真实恢复代码。没有安装完整应用，也没有跑参考仓库全部测试。
- 本文源码引用使用“墨屿:相对路径:行号”“v8:相对路径:行号”“master:相对路径:行号”，分别相对于上面三个固定目录。

能力等级：**R**＝本轮运行验证；**E**＝源码存在且调用路径可达；**S**＝只有数据库结构证据；**D**＝只有文档或技能要求；**U**＝证据不足。E、S、D 都不能替代真实模型质量评测。

## 能力矩阵

| 维度 | 墨屿 | webnovel-writer master / v8 | 对墨屿的含义 |
| --- | --- | --- | --- |
| 产品入口 | E：独立本地网页与 Python 服务 | master D/E：Claude Code 插件、只读面板；v8 D/E：DSH 插件工作台 | 不能把 master 当作独立网页编辑器横比 |
| 日常正文保存 | R：继续输入时旧版本号导致第二次请求冲突；采纳审计后也冲突 | v8 E：编辑快照/碰撞控制；R：底层多文件事务恢复 | 先修实际保存，再谈新能力 |
| 创作设计与阶段推进 | E：项目、章节目标、场景计划、视角、伏笔、章末钩子 | v8 E/D：作品契约→世界书→卷章纲→细纲→备料→起草→审读→修改→作者裁决→归档 | 墨屿需要显式阶段、前置条件与可继续的进度 |
| 分角色 AI | E：同一个提供方与模型的任务提示词 | master E/D：Context、Reviewer、DataAgent；v8 E/D：主控/起草/润色/审读，经宿主派发 | 需要写、审、改分工；并非单纯多起几条线程 |
| 审读输入身份 | E：审读字段和局部检查；整章改稿失效策略需其他代理复核 | v8 E：正文、细纲、材料、篇幅组成指纹，过期回写拒绝 | 不让旧稿审读结果证明新稿合格 |
| 多维审读 | E：本地规则及 check 任务，不是完整八维语义闭环 | v8 E/D：8 个语义模块＋确定性检查；默认一次子代理合并跑八维 | 不要误称默认 8 个代理并发 |
| 事实沉淀 | E：候选记忆、证据范围、正式确认、人物/关系/时间线等端点 | master E：accepted CHAPTER_COMMIT 驱动状态/索引/摘要/记忆投影；v8 E：定稿包与账本/本书记忆 | 事实更新应与章归档形成可恢复的事务 |
| 正文改稿后证据 | R：旧 accepted 记忆的定位变化会阻止整份正文保存 | v8 E：来源/版本/指纹核验 | 先保留正文，再让记忆进入待复核状态 |
| 检索与材料控制 | E：词项、近章、证据、手动 pin/omit 和有界裁剪 | master E：vector/BM25/graph；v8 E：关键词/向量融合、重排、回退和来源二次校验 | 墨屿手动材料控制值得保留；语义检索可按需增加 |
| AI 流、取消、恢复 | R：缓冲后再推送；running 取消不中断模拟提供方；E：持久队列 | v8 E：宿主子代理、可恢复索引 worker；完整正文生成队列与收费请求恢复 U | 不把对方的索引队列说成正文生成万能队列 |
| 调用预算和成本 | R：每日限额 1 可执行 2 次；E：字符估算成本 | v8 D：明确无统一价格/账单保证，建议提供方限额 | 不能说对方成本控制完美；墨屿需真实预留与用量 |
| 崩溃恢复与外部编辑 | E：SQLite revisions、备份与恢复脚本；本轮此项未跑全套 | v8 R：半写入后回滚；外部作者编辑保留、隔离冲突目标、其他目标仍可写 | 借鉴可执行的恢复规则，而不是复制宣传语 |
| 导入与导出 | R：旧 JSON 恢复删除章节失败；E：最小 Markdown/EPUB 输出 | v8 E：安全 TXT/EPUB 参考小说导入；最小定稿 Markdown 合集和来源清单 | 墨屿 EPUB 输出有价值，但“JSON 备份”不是完整数据库备份 |
| 模型创作质量、长期一致性、无人值守整本书 | U：本轮未调用真实模型 | U：本轮未调用真实模型；v8 README 明确不承诺无人值守整本书 | 需要同一书纲、同一模型与盲评基准，不能从测试数量推出质量排名 |

## 墨屿必须先修的保存问题

### P0-1：保存尚未返回时继续打字，会使下一次自动保存使用旧版本

- 触发：第一次保存发出 version=1；请求等待期间作者继续输入；服务返回 version=2；第二次请求仍发送 version=1。
- 本轮实际运行原样 `syncBackend()` 得到请求版本 `[1,1]`、本地 version=1、`backendOnline=false`，新文字只留在本地缓存。原始记录：`webnovel-runtime-probe-c.json` 的 `inflight_edit_token_conflict_actual`。
- 根因：墨屿:app.js:15 仅在 `mutationSeq===sentSeq` 时接收完整回应。继续输入时，正文不能被旧回应覆盖是合理的，但服务器的新 version/head 也被丢弃。后续请求因墨屿:server.py:3513–3521 的乐观版本校验而冲突。任意异常又把后端标为离线；数字版冲突无直接用户提示。
- 修复方向：正文草稿与已确认版本分别保存；回应只推进已确认版本，不覆盖新输入；按实际回应串行续传；真实冲突展示合并入口；保存状态必须由成功回应驱动。
- 等级边界：函数行为已实测；完整浏览器和两标签页交互仍需父代理验证。

### P0-2：采纳 AI 候选先写审计，随后的正文保存必然落后一个版本

- 本轮 API 链实际结果：生成 200，decision 201，state.version 从 1 升到 2，继续使用前端保存前的 version=1 时 PUT 409；候选正文未进入数据库。
- 根因：墨屿:app.js:31–39 不刷新 state.version；app.js:73 先 `recordAIDecision()` 再 `save()`；server.py:2649 对任何事件都增加同一个全项目版本。记忆确认/拒绝/重新定位等独立 API 也有类似版本协调缺口（app.js:46–51、68–69）。
- 修复方向：候选采纳应当使用一个同时记录决策、应用补丁并返回新版本的原子端点；请求有幂等键，连续点击不会重复追加；独立 API 必须统一返回和更新版本。
- 原始记录：`runtime-probe-c.json` 的 `ai_adoption_version_conflict`。不是已跑过鼠标点击采纳的浏览器验证。

### P0-3：改稿移动已确认记忆的摘录位置，会拒绝整章新正文

- 本轮实测：原 accepted 记忆保存 200；在正文开头插入一句后 PUT 400，错误 `accepted memory requires a current source quote and an active entity`；新正文未持久化。
- 根因：server.py:2993 正确把旧 source_span 设为 stale；但 full-state 保存又在 server.py:3103–3106 用旧范围重新构造定位并要求 accepted 必须已有有效证据，一处失配回滚整次正文保存。
- 修复方向：保留作者新正文，受影响记忆改成 stale/待重新定位，停止进入正式上下文；摘录可唯一定位时重锚，否则请作者重新确认。不能让旧记忆成为正文保存的障碍。
- 定位与哈希严格校验本身是优点（server.py:2272–2313），应保留，调整事务和状态流转。
- 原始记录：`accepted_memory_anchor_edit`。

### P0-4：拒绝一条记忆后，前端认为整份作品状态无效

- 本轮后端创建 201、拒绝 200，state 返回 `已拒绝`；直接运行真实 `normalizeState()` 返回 null。
- 根因：server.py:2738 把 rejected 映射为中文 `已拒绝`；app.js:11 接受英文 rejected，却漏中文值。app.js:14 因 invalid state 走“后端不可用”；缓存同样无效时 app.js:13 回到 starter。
- 修复方向：API 内统一稳定英文枚举，中文只用于显示；覆盖全状态回读和旧缓存兼容；无效一条记录不能悄然让用户看到初始作品。
- 原始记录：`rejected_memory_frontend_contract`、`rejection_normalizer_actual`。

### P0/P1-5：导入旧 JSON 无法恢复已经删除的章节，且备份范围不完整

- 本轮实测：章节 DELETE 200；导入旧备份 PUT 409 `UNIQUE constraint failed: document_node.id`；数据库仍是 0 章。
- 根因：server.py:3052–3060 的 existing 不包含 tombstone，但底层 document_node 仍保留同一 ID；恢复旧 ID 走 create，撞唯一键。app.js:255 还删除 numeric version 而保留旧 head，实际交互可另遇分支头冲突。
- JSON 只导出 state 的章节/设定/剧情卡/记忆，不包括关系、时间线、人物状态、完整 revisions/events、AI jobs、运行记录和提交等（server.py:2701–2780）。UI 的“JSON 备份”不能等同全量恢复。
- 修复方向：导入先进行预检与恢复计划，明确还原被删除对象、处理分支头、显示差异和缺失资产，验证后单次提交；提供真正全量备份。数据库备份/恢复脚本是另一条已有路径，不应抹去这个优点。
- 原始记录：`deleted_chapter_json_restore`。

## AI 执行与预算的 P1 问题

1. **没有实际边生成边送达。** 模拟提供方第一个片段发出后等待 1.25 秒才发第二个；客户端收到回应头需要 1.277 秒，生成完整结束才送 SSE。server.py:3933–3950 先完整生成、记录、持久化，再 `send_sse()`；app.js:71 甚至只用同步 JSON，不使用 stream/queue/cancel/resume。后端已有回放账本，不等于实时流。server.py:3168–3174 的注释准确说明 buffered stream，README 和状态字段应同样诚实。验收须记录首字延迟，生成未结束时浏览器就能看到第一段；provider 断流前的片段已经落盘且可恢复。
2. **无密钥的本地兼容提供方会污染流文本。** `_chunks` 在 server.py:853–854 无条件 `.replace(config["api_key"], marker)`；api_key="" 时会把 marker 插到每个字符前后。本轮 delta 和 ledger 均含多余 `[密钥已隐藏]`；正常非流 `result.text` 有单独非空判断。应统一非空秘密脱敏并核对流/重播/最终文本一致。记录：`buffered_stream_and_empty_key`。
3. **截断输出被当作完整。** parser 实测 finish_reason=length 加 `[DONE]` 得到 `truncated=false`，直接 EOF 无 DONE/finish_reason 同样 false。server.py:795 的条件把“收到传输结束标记”误当作“内容正常完成”。采纳润色时 app.js:73 只看 truncated，可能用不完整候选覆盖正文。JSON 结构输出还只检查 required 存在，不校验类型、枚举、范围（server.py:689–692、790–793）；字段类型全错仍通过。记录：`output_completion_and_schema`。
4. **每日调用限制可以绕过。** daily quota=1，在实际第一次调用前 enqueue 两个 defer jobs，两个都 202；drain 后模拟提供方真实被调用 2 次，used_runs=2。server.py:218–231 只数 success，没有 queued/running 原子预留；初次 enqueue 检查在 server.py:3911–3913，process/drain 和 retry 没同样强制预留。失败/取消请求可能有实际费用也未计入。验收应在配额 1 时并发只允许一份收费执行，失败重试有明示费用边界。记录：`queued_daily_quota_bypass`。
5. **当前网页使用的同步生成长时间占 SQLite 写锁。** 模拟模型延迟 1.25 秒时，并发保存延迟 1.372 秒，两个请求最终 200。队列实现已经把生成放到 claim 锁外（server.py:1057–1091），但 UI 没接它；同步 handler 在已有事务内做网络等待。应短事务读取快照、锁外生成、短事务落盘；不能使写作保存等待完整模型响应。记录：`synchronous_ai_holds_save_lock`。
6. **running 取消只丢弃结果，不能停止提供方执行。** 本轮 running cancel 返回 202，等待约 1.248 秒到 terminal canceled，模拟 provider 正常工作结束，调用次数为 1。server.py:3983–4003 只设 flag，process 在整个 generate 结束后 finish。应把可中断连接、partial ledger、费用/结果状态和用户提示串起来；提供方不支持取消时诚实提示剩余费用。记录：`queued_cancel_discards_after_provider_finishes`。
7. **队列有基础，生产级恢复仍不完整。** 每任务开 daemon thread，无全局并发上限（server.py:1108–1110）；异常被 worker 吞掉（1094–1105）；启动把所有 running 重新 queued（1113–1120），没有 lease/heartbeat/提供方执行身份，对真实收费请求可能重复调用。取消中崩溃后被重置 queued 的任务又因 flag 不被调度，存在悬挂风险。上述恢复风险为 E 推导，未做收费请求 crash 实测。需要持久状态机、次数上限、诊断、取消终结和“执行未知”状态；不能把无法确认的请求自动重发。
8. **成本与用量只能算粗估。** server.py:919–925、933–946 用字符数÷4，命名却写 upper_bound；对中文不构成可靠 token 上界，且未含完整 system/作者 instruction。provider usage、实际 upstream model、缓存 token 和模型分价没有保留；SSE usage-only 事件被忽略（732–735）。统一单价不等于账单。应保存 provider 原始用量、请求与返回模型、有效价格快照，缺用量时标估算。`run_manifest` 和 PROV 等表只有结构证据 S（schema:34–39、686；server.py 除启动要求未写入），不能宣称完整可复现运行清单已经生效。
9. **两个 retry 入口的契约不一致。** jobs/retry 保存了 context/instruction/max_tokens（4004–4015），runs/retry 则以当前默认上下文重建、丢失原材料选择/作者 instruction，max_tokens 未像初始入口校验，且两者没有一致配额检查（4037–4059）。需要明确“重放同一次请求”与“基于当前作品重做”。

## webnovel-writer 可借鉴的真实机制

### v8 当前产品线

- **显式创作工件和作者权责。** README:24–58 描述作品契约、世界书、卷章纲和章节闭环；persona.ts:14–37 的主控适配要求查询实际书仓，正文起草/改稿/审读交子代理，候选与真源分开。阶段推进与专用工具源码存在 E；最终宿主是否完整执行本轮未测。
- **不是默认八个代理并发。** packages/review/src/checks.ts:196–230 注册八个语义模块；packages/bundle/skills/novel-review/SKILL.md:18–19 默认一次派发跑合并八维，发现集中时再按单维深挖。该技能只是参考项目内的编排资产，不是本次审查代理执行过的模型流程。
- **审读结果不能贴到新稿上。** packages/review/src/run.ts:151–160、268–299 把正文、细纲、材料、篇幅做成输入指纹，自动回写缺指纹或过期则拒绝；作者意见单独处理。这比“审读记录存在”更强，也不是要求修改全文后继续沿用旧通过标识。
- **从真源重建进度。** packages/core/src/recovery/index.ts:88–128 扫描实际章节、账本、记忆、材料，重建可继续章节与下一步；不把聊天会话当唯一状态。E，没有运行完整书仓新会话恢复链。
- **崩溃恢复与冲突隔离，本轮 R。** repo/atomic.ts:31–71 是共同写入边界；transaction.ts:63–108 对文件和 manifest 持久化；179–208 按 hash 确认当前目标是否仍是本事务写入；337–407 隔离冲突并禁止再次覆盖该目标。探针真实子进程半写后 exit88，恢复 a/b 为 old、c 不存在、未完成事务清空；若作者另改 a，恢复保留 author-edit，目标再次写入拒绝，无关文件仍可写，隔离记录=1。
- **归档有收据与业务身份。** commit/archive.ts:123–166 将输入做 request fingerprint，识别文件已写/提交失败/已提交，重复收据不重复归档。这项 E；本轮未跑全部 archive/Git 失败分支。墨屿可用 SQLite 事务和幂等请求实现等价目标，无需换成文件仓库。
- **检索尊重原文与失败回退。** retrieval/search.ts:35–184 有关键词/向量融合、重排，外部请求不持书锁，二次核对来源 hash/索引 generation/model 配置；索引未就绪/服务失败明确回退。可达 E；没有调用真实 embedding/rerank，也没验证小说检索质量。
- **索引后台任务与正文任务应区分。** bundle/src/indexing/manager.ts:38–50、138–176、199–218 持久 phase/generation、paused/resumable、AbortController，默认并发 2；embedding-provider/src/http.ts:166–224 有超时、重试、取消、429/5xx 与 Retry-After。这是索引/provider 工作，不证明正文生成已拥有独立完整持久 token 队列。
- **原地改稿有请求身份。** bundle/src/study/editor-requests.ts:6–47 覆盖 polish/expand/condense/review/continue/custom、请求状态、取消、重复保护与 snapshot hash；E。不是本轮完整编辑器 UI 验证。
- **参考小说输入做安全边界。** core/src/reference/epub.ts:48–127 真实解析 EPUB、校验 CRC、解压量、DTD/实体、DRM，配合渐进参考分析流程；E。不要把导入宣传等同实测模型分析准确。
- **有明确限制。** assembly/budget.ts:1–11 是固定条目和尾文字符数，不能称精确 token 预算；export/index.ts:101–204 主要最小 Markdown 定稿合集/来源清单，不是全格式双向互转；docs/user/privacy.md:17 明确无统一价格或账单保证；README:67 不保证旧书仓迁移，79 不承诺无人值守整本书；本代理没有完整 DSH 模型链或文学质量证据。
- **源码复用边界。** 固定快照 LICENSE 为 GPL-3.0-only。借鉴公开设计与能力不同于直接复用代码；若直接分发复用代码，应核查许可证义务。本报告不是具体分发方案的法律审查。

### master 旧 v6 维护线

- master README:21–29 明确 v6 只修致命问题、v7 冻结未发布、v8 正式版；不能拿 master 维护节奏判断整个项目当前产品线停滞。README 里的服务商成本、速度和并发广告不计入项目能力。
- README:94–127 的核心是 Context/Reviewer/DataAgent 及 accepted CHAPTER_COMMIT 唯一事实源；state/index/summary/memory 为派生视图，投影日志辅助定位更新失败。
- webnovel-write/SKILL.md:120–162 要求 reviewer 独立子代理审查；178–240 要求 DataAgent 输出 fulfillment/disambiguation/extraction artifacts 并通过 precommit gate。E 的运行脚本加 D 的宿主编排要求，不能写成此次实际跑了 Claude Code 完整写作。
- scripts/data_modules/memory/orchestrator.py:39–90 是 working/episodic/semantic 分层记忆组装；scripts/data_modules/chapter_commit_service.py:123–158 实际依次运行投影 writer、记录各项成功/失败和 projection log；rag_adapter.py 有 vector/BM25/graph 混合路径，不能说只有文档。
- master 只读 dashboard 不等于墨屿的可编辑网页，也不等于 v8 新编辑器。v6/v8 接口、迁移和宿主要求必须独立描述。

## 墨屿应保留的优点

- 本地单人入口简单、无需复杂宿主；Python 标准库和 SQLite 便于部署和维护。默认 loopback 与同源防护有真实实现（server.py:40–46、3200–3222），但不是公网身份认证。针对本地单人产品不能仅因没有登录就判普遍缺陷；公网发布需单独设计和用户授权。
- 正文 revisions、事件、分支头、证据范围与哈希、作者显式采纳、材料 pin/omit 已有代码，值得修正其跨端契约并贯通，而非全盘重写。
- AI queue 持久行、claim 原子性、锁外队列生成已经存在，不应说“完全无队列”；问题是网页未接入、预算与恢复边界缺失。
- 最小 EPUB 输出与可视化材料选择是实际有价值的差异。参考项目没有在所有维度领先。

## 现有测试没有证明什么

- tests/stream_resume_fixtures.py:87–97 只核 event/seq/metadata，不核准确内容、首字时延或 provider 中断；所以空密钥污染、缓冲流仍通过。
- tests/ai_queue_fixtures.py:61–80 用离线模板、小任务与 defer 取消，未覆盖 running 中断、重启、并发与配额预留。
- tests/strict_feature_fixtures.py:83–87 审计后走 PATCH 章节，没走网页 full-state CAS，漏掉采纳后版本冲突。
- tests/frontend_story_fixtures.py:10–21 是代码字符串存在断言，不能替代实际 DOM、保存和用户交互。
- tests/corrupt_import_fixtures.py:7–18 的自写校验器检查 schema_version/projects，真实 UI normalizeState 检查 project/chapters/records/memory/plot；测试没有跑实际导入路径。
- scripts/evaluation_runner.py:298–333 读取 fixture 里现成 predictions，tests/fixtures/evaluation_pass.json:35–134 预置预测和恢复时间；它能核计算公式，不能证明真实检索召回、模型创作质量或恢复速度。
- 先前 Python/JSON fixture 的通过记录不是本轮这些失败场景的反证，也不构成对参考仓库完整应用的验证。本轮仅本文探针完成，不宣称全项目验证全通过。

## 建议实施顺序与验收

1. **先完成可靠保存。** 统一状态枚举和版本回应；正文与服务器确认版本分离；候选采纳作为原子、幂等操作；正文改稿不被陈旧记忆拒绝；导入恢复 tombstone。用真实浏览器覆盖慢保存时持续输入、采纳后立即编辑、独立记忆操作、两标签页冲突、刷新后正文保留；保存徽标仅在成功回应后显示成功。
2. **让 AI 执行可控。** UI 接持久任务与实时流，先发 run ID、逐片段落盘；length/断流标不完整；原子配额预留、并发上限、真实 usage/模型身份；连接取消与终态；提供方执行未知时不盲目重发；正确保留 retry 上下文。模拟提供方验证首字早于结束、partial 可恢复、quota=1 只能执行一次、running cancel 及时终结、重启不重复收费执行或明确需要人工处置。
3. **建立章节生产闭环。** 作品契约（题材、读者、基调、视角、禁区、文风、篇幅）、卷/章/场景层级、准备材料、写/审/改分工、证据化多维审读、作者裁决、状态与记忆沉淀。结果绑定当前正文 revision 和输入指纹，换稿使旧自动审读失效。默认合并审读而非强制八路并发，避免成本先失控。
4. **补齐长篇与参考分析能力。** 可选语义检索和模型配置；参考小说分批分析、引用与来源；章/卷摘要候选；关系、时间线、伏笔、人物知情边界参与备料和审读。先跑真实代码检索基准，再谈召回领先。
5. **最后证明效果。** 固定相同题材/书纲/模型/预算的多章样本，按前后矛盾、人物动机、情节因果、钩子兑现、文风重复与改稿幅度做盲评；至少覆盖不同体裁和几十章的状态累积。可靠性、成本、文学质量分开报，不以“19 个测试通过”取代真实产品验收。

## 原始复核材料

- `./.audit-cache/2026-10-13/runtime_probe_c.py`
- `./.audit-cache/2026-10-13/runtime-probe-c.json`
- `./.audit-cache/2026-10-13/webnovel_runtime_probe_c.mjs`
- `./.audit-cache/2026-10-13/webnovel-runtime-probe-c.json`
- `./.audit-cache/2026-10-13/webnovel-branch.json`
- `./.audit-cache/2026-10-13/webnovel-v8-branch.json`

两个结果 JSON 都无 `probe_error`。所有探针子进程已结束；临时数据库与模拟提供方已关闭。产品源码未改。

重复运行使用 bundled runtime：

```sh
python3 ./.audit-cache/2026-10-13/runtime_probe_c.py
```

```sh
node --experimental-transform-types --loader ./.audit-cache/2026-10-13/references/webnovel-writer-v8/scripts/strip-types-loader.mjs ./.audit-cache/2026-10-13/webnovel_runtime_probe_c.mjs
```
