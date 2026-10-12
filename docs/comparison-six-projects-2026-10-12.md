# 墨屿与六个参考项目的功能对比

对比日期：2026-10-12（UTC）  
对比对象：墨屿当前工作树，以及 `docs/third-party-audit.md` 记录的六个公开项目快照。  
对比性质：clean-room 产品能力复核，不复制代码、提示词、品牌或素材，也不把单个项目的宣传能力当作生产质量证明。

## 结论

墨屿目前的优势是“作者确认优先、来源可追溯、失败可审计、本地可恢复”的单用户写作闭环：开始创作 → 任务卡 → 生成候选 → 作者采纳 → 记忆确认 → 本章审查 → 终稿门。六个参考项目分别在结构化世界观、分层检索、角色化工作流、材料编排、流式任务控制和章节提交门上更成熟。

墨屿已经吸收了六个项目中最适合当前版本的产品思路，但它不是六个项目能力的简单相加。当前仍低于参考项目集合的成熟度，关键差距集中在中文语义检索、关系/时间模型、角色化工作流、长任务控制、可视化补丁和出版协作。

## 逐项目对照

| 参考项目 | 该项目更强的能力 | 墨屿已经覆盖 | 墨屿仍缺少 | 对墨屿的判断 |
|---|---|---|---|---|
| **NovelForge** | 层级设定卡、关系图、多轮续写预算、审查门 | 任务卡、候选记忆、章节审查、终稿阻断、作者采纳 | 结构化设定层级、实体关系/事件图、关系变化和预算内多轮续写 | 墨屿在来源 span、stale 失效、AI 决策审计和恢复上更完整；世界观可视化较弱 |
| **MuMuAINovel** | RTCO 分层上下文、章节/重要度记忆、SSE、重试、provider 抽象 | provider 抽象、有界上下文、章节片段、失败运行、持久化队列、取消请求 | provider 原生流式、断点续跑、重要度分层、多模型路由 | 墨屿的作者控制和审计更清晰；任务调度和上下文编排仍落后 |
| **arboris-novel** | Planner → Director → Writer、任务卡、时间线、伏笔状态 | 章节任务卡、场景计划、伏笔动作、下一章简报 | 角色化 agent、场景树、时间线约束、伏笔依赖 | 墨屿有闭环骨架，但仍是单一助手工作流 |
| **Ai-Novel** | 摘要/首尾片段、分层上下文、混合检索、重排、输出契约 | 相邻章节召回、来源 ranges、CJK FTS、记忆候选、上下文预览、结构化 JSON 契约 | embedding、dense+sparse、RRF/rerank/MMR、patch 契约 | 墨屿证据链更强；语义检索和增量编辑仍落后 |
| **91Writing** | 材料选择、提示预览、章节状态、时间线方向、备份入口 | 章节状态、候选式 AI、上下文预览、pin/omit/order、JSON/SQLite 备份恢复 | 多工作集版本、完整 prompt 回放、可视编辑历史和差异 | 墨屿后端可恢复性更强；出版编辑能力仍不够细 |
| **webnovel-writer** | Story System、`CHAPTER_COMMIT`、投影索引、prewrite/precommit/postcommit 门、分支/what-if 方向 | 任务卡、章节审查、终稿门、revision restore、提交快照、事件记录 | 投影索引、分支隔离/合并、多人阶段性提交审计 | 墨屿已有可运行门禁，但分支和协作仍缺失 |

## 横向能力矩阵

| 能力 | 墨屿当前状态 | 六项目中可对照的强项 | 差距级别 |
|---|---|---|---|
| 写作起步与主流程 | 首页五问建项目和第一章；任务卡→候选→采纳→记忆→审查→下一章 | arboris 的规划分工、webnovel-writer 的提交阶段 | 中 |
| 章节规划 | 有目标、视角、场景计划、伏笔动作、回顾、下一章简报、hook | arboris 的 Planner/Director/Writer | 中 |
| 世界观与人物 | 人物/地点/势力/物品设定卡，标记捕获后确认 | NovelForge 的层级卡和关系图 | 高 |
| 记忆可信度 | candidate→author review→canon；source quote/range、entity、revision stale | NovelForge 的预览写入；MuMu 的重要度记忆 | 墨屿优势 |
| 长上下文 | 有界召回，当前 24,000 上限，token 为字符除 4 的保守估算 | MuMu 的分层上下文、Ai-Novel 的摘要与混合检索 | 高 |
| 百万上下文 | 未实现 | 六项目也不能据此证明真实百万 token 质量 | 未完成 |
| AI 生成控制 | provider、离线模式、候选区、ai_run、结构化契约、持久队列、接受/编辑/拒绝/撤销、失败记录 | MuMu 的 SSE/重试、91Writing 的材料选择、Ai-Novel 的输出契约 | 高 |
| 连续性审查 | 长度、状态、任务卡、实体标记、待确认记忆、重复段落 | NovelForge/webnovel-writer 的审查/提交方向 | 高 |
| 时间线与关系 | 有时间线/关系/人物状态/剧情线 API 和故事结构页；仍无可视图谱、知识范围审查和冲突合并 | NovelForge、arboris、webnovel-writer | 中高 |
| 伏笔生命周期 | setup/develop/payoff 看板，终稿检查未回收 | NovelForge/webnovel-writer 的状态与依赖方向 | 中高 |
| 版本与恢复 | revision 列表、恢复为新 revision、SQLite 备份/恢复 | 91Writing 的备份入口、webnovel-writer 的 commit/回滚方向 | 中 |
| AI 变更粒度 | 候选采纳和整段正文更新；没有范围级 patch/逐字符 diff | webnovel-writer 的 commit 思路、编辑器 tracked changes 方向 | 高 |
| 出版与协作 | JSON/Markdown，单用户本地 | 参考项目也未形成统一出版级协作闭环 | 高 |

## 目前真正领先的部分

墨屿在六个项目对照中形成了四个明确的产品优势：

1. **作者确认是强制路径**：模型输出先进入候选区，记忆必须带当前来源和实体证据才能成为 accepted/canon。
2. **证据不会静默失效**：章节 revision 变化后，旧 source span 会标记 stale，避免继续把旧文字当成当前事实。
3. **AI 行为可追踪**：生成、失败、接受、编辑、拒绝和撤销都有 `ai_run_id` 与决策历史。
4. **本地可恢复**：章节恢复会创建新 revision，SQLite 备份、损坏拒绝和恢复演练已有运行证据。

这些优势属于可信度和可恢复性，不能替代中文语义召回、长任务调度或出版协作能力。

## 需要优先追平的能力

按对长篇写作影响排序，建议先补这五项：

1. **检索层**：SQLite FTS5/中文分词、别名索引、embedding 可选层、RRF/rerank/MMR，以及实体、时间、分支硬过滤。
2. **章节提交层**：实现 prewrite、precommit、postcommit 和 `CHAPTER_COMMIT` 类状态机，把审查、记忆变更、revision 和导出快照绑定起来。
3. **AI 任务层**：SSE 流式输出、取消、重试、断点续跑、队列租约、费用/配额和结构化 JSON/patch schema。
4. **故事结构层**：实体关系图、人物状态/知识范围、时间线、伏笔依赖 DAG、冲突合并与原文跳转。
5. **编辑与出版层**：范围级 AI patch、可视 diff、pin/omit/order 材料选择、DOCX/PDF、EPUB 往返和完整导出损失报告。

## 诚实的成熟度判断

| 结论 | 当前判断 |
|---|---|
| 可运行的本地小说编辑工具 | 是 |
| 已把六个项目的产品思路接成最小闭环 | 是，已覆盖关键主线，但深度不齐 |
| 功能总量超过六个项目 | 否，六项目各自的强项仍有明显缺口 |
| 真实百万上下文 | 否 |
| 自动保证长篇事实一致 | 否 |
| 生产级多人协作/出版平台 | 否 |
| 可信的单用户本地 AI 写作原型 | 是 |

参考项目版本、许可证和借鉴边界见 [`docs/third-party-audit.md`](third-party-audit.md)；运行级缺口与测试证据见 [`docs/strict-feature-audit-2026-10-11.md`](strict-feature-audit-2026-10-11.md)。

## 2026-10-12 实现更新

本文件的初始矩阵记录了上一轮对照状态。本轮已经把以下差距从“没有”提升为“可运行基础能力”：人物关系/时间线/伏笔生命周期 API 与故事结构页、人物状态/剧情线 API、材料 pin/omit/order、Prompt 契约预览、结构化 JSON 任务、buffered SSE、AI retry/取消保护、持久化 AI 队列、revision diff、CJK FTS 检索，以及 `prewrite → precommit → postcommit` 章节提交快照。上下文还新增了受限的 `retrieval_profile`，可配置预算、章节/近章窗口和记忆/设定上限，并在预览中返回 lexical/recency/evidence 分层与来源元数据。它们仍不是参考项目全部深度：关系图还不是可视化图谱，SSE 还不是 provider 原生 token stream；队列虽可重启恢复，但仍缺租约/heartbeat/进程外可靠取消；检索仍缺 embedding/RRF/rerank，出版和多人协作仍未完成。

## 2026-10-12 可复现快照与代码证据

以下记录来自独立只读 clone，而不是项目 README 的功能宣称。复核命令是：`git clone` 后固定分支，再以 `git rev-parse HEAD` 记录 commit；路径是该 commit 中实际存在的文件。功能证据表示“代码/测试中能看到相应路径”，不表示我们替上游做了生产质量认证。

| 项目 | 分支 / commit | 许可证核对 | 代码级证据（路径） | 对墨屿的直接启示 |
|---|---|---|---|---|
| NovelForge | `main` / `a485814411546b3980df0a1696a0fb6a043d56ce`（v0.9.8） | `LICENSE` 为 AGPL-3.0 | `backend/app/services/workflow/engine/scheduler.py` 有持久化运行队列、优先级和取消；`backend/app/api/endpoints/ai.py` 使用 `StreamingResponse`/SSE；`backend/app/api/endpoints/knowledge.py` 为知识库 API；`backend/app/api/endpoints/foreshadow.py` 为伏笔路由 | 需要把“任务卡”继续推进为可观察的运行状态、节点进度和恢复；不能复制其实现或提示词 |
| MuMuAINovel | `main` / `99f0aec6ce3b1c65652d20007be930ebba6a6d20` | `LICENSE` 为 GPL-3.0 | `backend/app/api/chapters.py` 引入 SSE、memory、foreshadow 和 partial regenerate 相关服务；`backend/app/api/foreshadows.py` 提供伏笔 plant/resolve 路由；`backend/scripts/export_embedding_onnx.py` 固化 embedding 导出流程 | 需要 provider 原生流、局部重写和可选 embedding；GPL 代码不可移植 |
| arboris-novel | `main` / `dd80c69bc30a80d68b375bd504c6bc308164ff9e` | clone 中没有 `LICENSE` 文件；README 的 MIT badge/文字不能替代授权文件 | `backend/app/models/memory_layer.py` 定义 knowledge、timeline event、因果/关系数据；`docs/RAG.md` 展示 embedding→检索→Prompt 拼装链路；README/服务层描述 concept→blueprint→chapter→evaluate→summary | 需要把章节蓝图、时间线、知识范围和伏笔跟踪做成可审查对象；未获书面授权前不复制代码 |
| Ai-Novel | `dev` / `8b85b2b6f29af4c69241be7c42417dbe4795db30` | README 的 License 节明确后续补 `LICENSE`；本快照视为未授权 | `backend/app/services/search_index_service.py` 覆盖多种故事来源与 story memory；`backend/tests/test_streaming_support.py` 验证 OpenAI SSE delta/fallback；迁移中有 pgvector/vector chunks 和 rerank 配置 | 需要把 sparse 检索升级成可选 dense+sparse、RRF/rerank，并保留解释信息；测试应覆盖 provider 流式协议 |
| 91Writing | `main` / `df3de0362e8e4028b76dffa78c5bba7b8bbd20a1` | `LICENSE` 为 MIT | `api.js` 的 `generateTextStream` 和 prompt/billing 路径；README/前端保存材料、章节状态、备份和统计入口 | 材料选择、Prompt 预览、预算提示和本地备份是轻量但高频的作者体验；概念可借鉴，代码仍不复制 |
| webnovel-writer | `master` / `0af0c0b2fc2e4104e8a12c1d62136c6595a6a983` | `LICENSE` 为 GPL-3.0 | `README.md` 与 `docs/architecture/overview.md` 明确 accepted `CHAPTER_COMMIT` 为写后真源，`state/index/summary/memory` 为投影，`preflight`/`doctor`/dashboard 暴露运行健康；README 还记录 Embedding/Rerank 配置 | 需要将提交、投影、索引和运行健康绑定为可重试的链路；当前墨屿已有提交快照，但还没有完整 projection ledger |

### 证据的可信度边界

- **许可证和 commit 可复现**：上述字段可由公开仓库在对应日期重新核对；上游 force-push、分支删除或许可证补充都会改变后续结论，发布前必须重新锁定依赖。
- **功能证据不是效果证据**：看到 SSE、embedding 或 workflow 文件，只能证明实现路径存在；没有测量其长篇一致性、失败恢复、延迟、成本或并发 SLA。
- **README 与实现冲突时以实现和测试为准**：例如 arboris-novel README 声称 MIT，但固定快照中没有 `LICENSE` 文件，因此本项目按“未明确授权”处理。
- **比较维度保持对称**：墨屿的“已覆盖”也必须有 API、fixture 或运行记录；只存在 schema、设计文档或 UI 占位的能力继续标为“部分/未完成”。

## 差距优先级与可验收定义

“第一梯队”不等于把六个仓库的功能名全部抄到菜单里，而是让长篇创作的关键失败路径可以被作者发现、撤销和复跑。按影响和依赖关系，差距拆成以下可验收项：

| 优先级 | 差距 | 六项目对照依据 | 验收条件 | 当前状态 |
|---|---|---|---|---|
| P0 | 范围级 AI patch + provider 原生流 | MuMuAINovel/91Writing 的流式与局部重写；NovelForge 的 SSE | 以 `base_revision`/内容哈希做 CAS；返回范围 diff、前后哈希、逐块序号；断线可重连；接受、拒绝、撤销均有事件 | 已补 `POST /api/chapters/:id/patch` 的 preview/apply、revision/hash/CAS、stale 标记和事件；仍缺 provider 原生 token stream/断线重连 |
| P1 | 可解释语义检索 | MuMuAINovel ONNX embedding；Ai-Novel vector chunks/rerank；webnovel-writer Embedding/Rerank | 可选 embedding；dense+sparse RRF→rerank/MMR；实体/时间/分支硬过滤；`search/explain` 返回分层分数、source span、stale/冲突和裁剪原因 | 当前为 CJK FTS + recency/evidence 分层；无 dense/RRF/rerank |
| P1 | 章节提交后的投影健康 | webnovel-writer `CHAPTER_COMMIT`、projection log、preflight/doctor | commit 绑定 revision/review/assertion delta；投影幂等、有状态、可重试；失败能在 UI 定位；重启后不丢队列 | 已有 `prewrite/precommit/postcommit` 快照与持久队列；缺完整 projection ledger/health API |
| P2 | 故事结构和知识范围 | NovelForge 关系/伏笔路由；arboris timeline/knowledge；webnovel-writer 状态投影 | 实体、关系、事件、POV 可见知识范围可视化；冲突合并、原文跳转、未来信息泄漏审查有可复现结果 | 上下文已按显式时间/分支范围过滤人物状态并记录隐藏原因；已有结构 API/UI，仍缺图谱和范围审查 UI |
| P2 | 角色化长任务 | NovelForge workflow scheduler；arboris Planner→Director→Writer；Ai-Novel 多 agent | Planner/Director/Writer/Reviewer 使用版本化契约；节点进度、暂停、重试、恢复、成本和失败原因可审计 | 当前单助手任务卡 + 持久队列；缺多角色运行台 |
| P3 | 出版和多人协作 | 91Writing 备份/统计；MuMu/Ai-Novel 的用户与项目边界 | DOCX/PDF/EPUB 往返、loss manifest；分支合并和权限审计；云部署认证、配额和 BYOK | 本地 JSON/Markdown/最小 EPUB；单用户定位，未完成 |

范围 patch、分支 head CAS、人物状态知识范围过滤和长篇知识范围评测已进入 fixture；其余验收项仍需恢复演练和真实长篇 gold set。在证据补齐前，文档不会把“设计目标”写成“已经实现”。
