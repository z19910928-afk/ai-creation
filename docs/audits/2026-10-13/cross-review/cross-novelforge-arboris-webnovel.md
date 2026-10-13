# Arboris 与 webnovel-writer 独立交叉复核

日期：2026-10-13，Asia/Shanghai。复核代理：audit_novelforge。

完整读取的专责报告：

- `agent-arboris.md`
- `agent-runtime-webnovel.md`

本次回到原始固定源码核查，每项目选取 10 个核心主张，并重新执行既有组件探针。没有修改产品源码、部署、发布、调用真实或收费模型。仓库内的 SKILL 文件只作为被审查的编排资产读取，没有执行它们的指令。

## 结论与需要修正的表述

两份报告的主要结论成立：Arboris 普通作者流程有实际接口与持久化代码，但高级审查/定稿不能自动算入普通前台，且护栏、选择后入库、秘密/未来章过滤确有断点；webnovel-writer v8 的文件事务恢复与冲突隔离在组件范围内实测成立，审读指纹、归档身份和从文件重建进度有实际实现。没有证据把任何一个项目的文学质量或长篇效果判为已通过。

需要修正三处表达或引用：

1. **墨屿“实时生成/批量有后台”下调。** 已有持久单任务队列、片段账本、重放、取消和重试入口；生成完成后才发送片段。没有在本轮证实受控多章节批量创作流程。建议改为“后台已有持久单任务与缓冲重放基础，网页尚未接入；实时正文与章节批量流程未验收”。
2. **master 源码引用补目录前缀。** `agent-runtime-webnovel.md` 定义路径相对 master 根，但旧版脚本、技能和面板实际上位于 `webnovel-writer/` 子目录；`rag_adapter.py` 位于 `webnovel-writer/scripts/data_modules/`。这是引用修正，不是驳回实际脚本能力。
3. **v8 选区请求补内存限制。** 请求身份、取消、重复回复保护和编辑入口都存在；`EditorRequestHub` 使用 WeakMap，明确不落盘。不能由此推导选区任务能跨插件重启恢复，也不能把 snapshot 的 hash 字段本身当完整碰撞控制。实际入口另有磁盘 hash 检查，见 W9。

未发现足以反转核心结论的反例。下表“确认”只确认所列范围；没有标成 R 的部分仍是源码或编排证据。

## 固定源码与证据等级

| 标记 | 根目录与固定版本 |
| --- | --- |
| A | `./.audit-cache/2026-10-13/references/arboris-novel`，main `dd80c69bc30a80d68b375bd504c6bc308164ff9e` |
| W6 | `./.audit-cache/2026-10-13/references/webnovel-writer-master`，master `0af0c0b2fc2e4104e8a12c1d62136c6595a6a983` |
| W8 | `./.audit-cache/2026-10-13/references/webnovel-writer-v8`，v8 `a0bdd2d4e2f39bc3de32fc5cbfe605067b5b486b` |
| M | `.`，`62f1c66a7b16bb84f2940dc1af48e685aaf58818` |

下表路径相对相应根目录，包含必要的完整子目录。R＝本次重新执行原组件；E＝独立追踪源码入口/调用/保存/消费；D＝文档或技能要求；U＝未验证。R 没有自动覆盖整机、模型效果或生产稳定性。

## Arboris：10 项核心复核

| 编号、主张 | 判定 | 独立源码证据及运行范围 | 未测范围 |
| --- | --- | --- | --- |
| A1 普通前台未连接 advanced、finalize、六维审查 | **确认，E** | A:`frontend/src/api/novel.ts:224-249` 只走普通 generate/evaluate/select；全 `frontend/src` 搜索 `advanced/finalize/six-dimension/six_dimension` 无调用结果。高级路由 A:`backend/app/api/routers/writer.py:347,384` 和 A:`backend/app/api/routers/review.py:39-66` 确实存在，不能写成后台也没有。 | 没有启动完整前后台，也未实测用户登录和所有页面。 |
| A2 护栏配置存在时修复函数返回 None | **确认，R 组件 + E** | A:`backend/app/api/routers/writer.py:173-188` 在模板存在时无后续 return；实际重写段在另一函数 `:248-273`，还引用不属于该函数参数的 original_text 等变量。AST 抽取原函数执行，配置提示词时 `guardrail_configured_returns_none=true`。 | 未执行整个生成 HTTP 链，未证明真实模型产出的最终候选内容。 |
| A3 选择版本后检索入库缺 user_id，已选定状态先提交 | **确认，R 签名 + E** | A:`backend/app/api/routers/writer.py:833,845-855`；A:`backend/app/services/chapter_ingest_service.py:38-47` 必需 keyword-only user_id；A:`backend/app/services/novel_service.py:476-482` 先 commit。原函数签名绑定重现 `missing a required argument: 'user_id'`。异常被捕获后返回项目。 | 未独立重跑完整数据库/HTTP；正文选择已保存与索引失败的顺序由 E 确认。 |
| A4 普通检索没有当前章硬过滤 | **确认，R 组件 + E** | A:`backend/app/services/chapter_context_service.py:61-99` 无当前章参数并原样返回 store 结果；A:`backend/app/services/vector_store_service.py:158,219` SQL 仅按 project_id；A:`backend/app/api/routers/writer.py:589-597` 普通生成实际使用该服务。模拟第 2 章查询时原组件保留第 9 章结果 `[9]`。 | 未测真实向量相关性、未来章实际命中概率或正文剧透频率。 |
| A5 角色可见性裁人物集合，不裁角色秘密和世界真相 | **确认，R 组件 + E** | A:`backend/app/services/writer_context_builder.py:89-121` 允许名字后保留整个角色对象及世界对象；A:`backend/app/api/routers/writer.py:610-627` 注入该蓝图和禁止名字列表。原组件例子保留已登场反派 secret 与世界 future_truth。 | 没有证明真实模型一定泄露；证明的是发送材料缺少知情硬隔离。 |
| A6 六维审稿缺模板/解析失败仍给高默认分 | **确认，E** | A:`backend/app/services/six_dimension_review_service.py:58-60,87-97,145-163` 失败走 overall80、各维100、问题数0。A:`backend/app/services/llm_service.py:62` 的 generate 存在，没有采用“调用不存在方法”的错误推断。 | 未运行真实六维模型审稿；网络异常与解析失败不能笼统视为同一失败分支。 |
| A7 可见伏笔页面使用关键词分析，未接持久生命周期接口 | **确认，E** | A:`frontend/src/components/shared/NovelDetailShell.vue:278-289` 加载伏笔组件；A:`frontend/src/components/novel-detail/ForeshadowingSection.vue:255-297` 请求 analytics；A:`backend/app/api/routers/analytics.py:173-240` 用正则/描述词匹配和固定 2/6/15 章窗口。A:`backend/app/api/routers/foreshadowing.py` 的持久接口存在，但全 frontend 未见其编辑/回收调用。 | 没有真实页面操作或文学伏笔准确率测试。不能写后台完全没有伏笔 CRUD。 |
| A8 多候选不是耐久历史，手工编辑覆盖已有版本 | **确认，E** | A:`backend/app/services/novel_service.py:443-458` 先删除本章旧 ChapterVersion 再创建候选；A:`backend/app/api/routers/writer.py:1153-1173` 直接替换 target_version.content 后提交。 | 未执行多次再生成/恢复 UI；未证实数据库外部备份是否保留旧内容。 |
| A9 删除路由调用不存在的单数方法 | **确认，R AST + E** | A:`backend/app/api/routers/writer.py:1009-1020` 调 delete_chapter；A:`backend/app/services/novel_service.py:497` 定义 delete_chapters。原类 AST 方法表确认单数不存在、复数存在。 | 未重跑 HTTP 删除请求，不能把源码确定断点标成完整接口实测。 |
| A10 高级记忆/读者模拟/自评不能由服务文件直接判成熟 | **确认，E** | A:`backend/app/services/pipeline_orchestrator.py:40-57,349-395` reader/selfcritique 默认 false、未加入覆盖字段且 ultimate 明确关闭；A:`backend/app/services/finalize_service.py:369-386` 人物状态以 character_id=0、name=__all__、raw_state_text 保存。普通下一章摘要实际存在 A:`backend/app/api/routers/writer.py:490-513`，故不能写完全无写后摘要。 | 高级整机、异步数据库桥接、逐人物长期记忆准确性及长篇效果均未测。 |

## webnovel-writer：10 项核心复核

| 编号、主张 | 判定 | 独立源码证据及运行范围 | 未测范围 |
| --- | --- | --- | --- |
| W1 master 和 v8 是不同宿主与产品线 | **确认，D + 面板 E** | W6:`README.md:21-29` 区分 v6 维护/v7冻结/v8正式；W8:`README.md:62-85` 标示 v8=DSH、工作台8.2.2、配套0.2.0-rc.2、旧书迁移未验证、不承诺无人值守。W6:`webnovel-writer/dashboard/app.py:279-295,826-869` 实际只读 GET；全文件无写入路由。 | 未安装完整 Claude Code 或 DSH 宿主；版本只代表此固定快照。 |
| W2 八个语义模块不等于默认八代理并发 | **确认，E + D** | W8:`packages/review/src/checks.ts:196-230` 注册八模块；W8:`packages/bundle/skills/novel-review/SKILL.md:18-19` 默认一次子代理合并八维、集中问题再单维深挖。技能是编排资产，未执行其模型任务。 | 未测任何真实子代理审读质量、并发速度或费用。 |
| W3 审读输入指纹阻止旧稿结果冒充新稿 | **确认，E** | W8:`packages/review/src/run.ts:151-174` 从实际输入计算 fingerprint；`:268-299` 自动回写缺指纹/指纹不符即拒绝，作者意见单独处理；换稿时不保留旧完成态作为新稿证据。 | 未重跑全套 review 测试或宿主审读链；指纹机制不证明模型问题判断准确。 |
| W4 文件批次半写后的恢复 | **确认，R 组件 + E** | W8:`packages/core/src/repo/atomic.ts:31-71` 写前恢复与批量写入口；W8:`packages/core/src/repo/transaction.ts:63-108,179-208` fsync/manifest/hash 恢复。实际子进程完成第一次目标 rename 后 exit88；恢复后 a/b 为 old、c 不存在、未完成事务数0。 | 非突然断电、磁盘损坏或 Windows 全流程；不是完整章节归档/模型任务恢复实测。 |
| W5 外部作者改动冲突被保留并隔离目标 | **确认，R 组件 + E** | W8:`packages/core/src/repo/transaction.ts:196-197,337-407` 外部 hash 冲突隔离；目标禁止写入，非冲突目标继续写。相同半写后人为改 a，探针保留 author-edit，恢复报冲突、目标写入被拒、隔离记录1、无关写成功。 | 未测隔离处理 UI、复杂多事务组合、未知目标 manifest 损坏及网络文件系统。 |
| W6 归档收据和从真源重建进度有实际实现 | **确认，E** | W8:`packages/core/src/commit/archive.ts:123-166` 输入身份、文件已写但提交失败、alreadyCommitted 分支；W8:`packages/core/src/recovery/index.ts:88-128` 读取实际章节、账本、记忆、细纲和材料生成 resume/listResumable。 | 未独立运行 Git 提交失败所有分支、完整新会话恢复、归档幂等端到端。R 的文件事务不自动覆盖这里。 |
| W7 检索融合/重排/回退和来源二次核验可达 | **确认，E** | W8:`packages/core/src/retrieval/search.ts:35-184` 索引未就绪退关键词；embedding`:85`、rerank`:153` 在锁外；`:97,100,103,122,167-174` 核原文 fingerprint/hash、索引 generation、提供方身份/配置。 | 未调用真实 embedding/rerank，未测真实小说召回率；“可定位来源”不等于直接可当正典事实。 |
| W8 持久索引后台不能泛化成完整正文生成队列 | **确认，E；泛化推断驳回** | W8:`packages/bundle/src/indexing/manager.ts:38-50,138-176,199-218` 读 phase/generation、控制 AbortController、默认并发2，并明确执行 syncFinalizedIndex。报告已正确区分索引与正文队列。 | 正文持续 token 落盘、收费请求崩溃恢复、模型取消/未知执行身份、全书批量流程 U。 |
| W9 选区改稿有请求身份与实际操作，持久范围必须收窄 | **确认 E；跨重启泛化下调** | W8:`packages/bundle/src/study/web.ts:249-276` 核磁盘 hash、生成 snapshot、提交主控消息、取消/取回复；W8:`packages/bundle/src/study/editor-requests.ts:168-198,263-285,376-425` 有实际重复/取消检查、reply 状态、意图与引文校验。`:161-164` 明确内存 WeakMap，不写磁盘；不能只拿 `:6-47` 字段类型当实现。 | 未运行完整编辑 UI、作者采纳保存、跨会话/重启；不能由请求 snapshot 存在推出耐久任务。 |
| W10 master 的投影/分层记忆/混合检索不只是文档 | **确认 E；更正引用前缀** | W6:`webnovel-writer/scripts/data_modules/chapter_commit_service.py:98-162` 实际 state/index/summary/memory/vector writer、逐项结果与日志；W6:`webnovel-writer/scripts/data_modules/memory/orchestrator.py:39-93` 分层组装；W6:`webnovel-writer/scripts/data_modules/rag_adapter.py:1110-1152,1184-1188,1233-1268` 实际策略分派、混合检索、融合。 | 未运行旧版完整写作/提交/投影；独立 reviewer/DataAgent 宿主流程仍是技能 D 加脚本 E，不是本次模型 R。 |

## 墨屿对照契约与范围修正

这些检查用于排除“不公平地把已有能力写成缺失”与“把基础后台写成已交付体验”，不重复原运行专责全部 HTTP 探针。

| 主张 | 判定与独立证据 | 未测范围 |
| --- | --- | --- |
| outline/scene/summary/check 采纳未回写各业务数据；范围补丁已有后台 | **确认 E**。M:`app.js:71-74` continue 追加、polish 整章替换，其余复制；M:`server.py:3731-3785` 确有章节范围、base revision/hash 和冲突校验。不能写“完全没有局部修改接口”。 | 未执行浏览器点击与 patch 全流程。 |
| 基础审查不等于跨章语义审稿 | **确认 E**。M:`server.py:1146-1191` 是确定性字段/状态/证据/重复等规则；warning 不阻止 passed。 | 没有真实模型文学评审。 |
| 持久队列与缓冲重放已经存在，但前台未接，批量未证实 | **下调过宽措辞，E**。M:`server.py:1057-1091` 原子 claim 后锁外生成；`:1108-1120` 单任务 daemon 与启动重排；`:3933-3950` 生成完整后持久片段并 send_sse；M:`app.js:71` 同步 JSON。 | 本交叉复核未重跑首字时延 HTTP；未证实多章节批量产品流程和收费崩溃恢复。 |
| 慢保存继续打字丢服务端新版号 | **确认 R 原函数 + E**。M:`app.js:15` 原样函数在 Node VM 配合受控网络重跑，请求版本 `[1,1]`，本地 version1，backendOffline；新输入保留在本地。M:`server.py:3513-3521` 存在相应服务端版本校验。 | 非完整浏览器、多标签或真实网络端到端；不据此称文字已永久丢失。 |
| 中文已拒绝状态令前台整态无效 | **确认 R 原函数 + E**。M:`app.js:11` normalizeState 原样重跑，含“已拒绝”时返回 null；M:`server.py:2738` 后端返回该值。 | 本交叉复核未重复后台拒绝 HTTP，也未复验完整页面刷新。 |
| 伏笔已有生命周期编辑/链接，但看板移动不闭环 | **确认 E，保留已有能力**。M:`app.js:184,222,230` 有编辑/链接；`:221` 看板提示“下一版移动状态”，无移动状态处理。 | 未运行页面操作、自动回收准确性和终稿放行流程。 |

## 本次复跑记录

Arboris 命令：

```sh
python3 ./.audit-cache/2026-10-13/arboris-component-probe.py
```

从固定源码 AST 抽取原函数/类；提示词、embedding 和向量 store 为本地模拟；没有 HTTP、数据库或模型调用。结果：护栏返回 None；选择后入库缺 user_id；反派秘密和 world future_truth 保留；模拟未来章 `[9]` 保留；delete_chapter 不存在而 delete_chapters 存在。所有检查正常执行，无 probe_error。

webnovel 与墨屿原样前端函数命令：

```sh
node --experimental-transform-types --loader ./.audit-cache/2026-10-13/references/webnovel-writer-v8/scripts/strip-types-loader.mjs ./.audit-cache/2026-10-13/webnovel_runtime_probe_c.mjs
```

真实导入上游原 TS 模块，真实子进程在第一目标 rename 后 exit88。结果保存于 `webnovel-runtime-probe-c.json`，与专责记录一致，全部检查成功且无 probe_error；临时目录清理、子进程结束。Node 的实验 loader 提示不影响检查结果。JS VM 原函数读取的是固定墨屿快照 `./.audit-cache/moyu-baseline/app.js`，网络为受控模拟。

本交叉复核没有重新运行墨屿严重缺陷的整套 HTTP 探针。原专责的 HTTP 记录继续保留为其原始 R，本次仅独立复核源码契约和上述两项原样前端函数；不能把这份交叉报告说成全部 API 或全应用再次验收通过。

## 汇总报告的证据使用要求

- 采用 Arboris 的创意到蓝图、多候选人工选择、下章消费已写摘要等产品设计，同时保留原实现缺陷和 UI 边界。
- 采用 v8 的输入身份、收据、事务恢复与源校验设计；不要把底层文件 R 扩展为 DSH 全应用、文学质量或收费任务恢复 R。
- 保留墨屿已有编辑器、章节范围补丁、历史/备份脚本、材料控制、关系时间线和队列基础，再针对真实失败修复。
- 文学质量、长篇知情一致性、真实检索收益、成本与生产稳定性继续 U。不能凭源码数量、默认高分、手写 predictions 或组件探针宣布“第一梯队”。
