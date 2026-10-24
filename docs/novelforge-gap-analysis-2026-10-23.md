# 墨屿 vs NovelForge：12 维差距分析与追赶路线

- 分析日期：2026-10-23
- 对象 A：NovelForge（https://github.com/RhythmicWave/NovelForge，v0.9.8，AGPL-3.0，本地浅克隆 `/tmp/novelforge-analysis`）
- 对象 B：墨屿（`/Users/zhaozhen/Downloads/神经网络写作`，v0.1.3）
- 方法：12 个并行子智能体分维读源码 + 文档，逐条给文件级证据；关键量化指标由主控在本机复核。

---

## 0. 一句话结论

**功能覆盖面约 60%，还差 40%。但这不是"全面落后"，而是"偏科"：**

- 写作可信度、工程质量、桌面交付三块，墨屿**已经反超** NovelForge；
- 缺的 40% 高度集中在**平台化能力**——通用工作流引擎（缺 75%）、扩展生态（缺 76%）、系统架构（缺 55%）；
- 生态影响力差 2–3 个数量级（1256 stars vs 1 star），这部分**不是写代码能补的**。

按"用户可感知差距"排序，**20–27 人周（约 5–6 个月全职）可补掉约 80%**；要连通用工作流引擎一起追平，再加 5–7 个月。

---

## 1. 量化硬指标（已本机核验）

| 指标 | NovelForge (A) | 墨屿 (B) | 倍数 |
|---|---|---|---|
| 版本 | v0.9.8 | v0.1.3 | — |
| 后端代码 | 31,005 行 / 174 个 py | 21,947 行 / 28 个 py | A 1.41× |
| 前端代码 | 44,277 行（62 Vue + TS） | 7,506 行（原生 JS/CSS） | A 5.90× |
| 单文件最大 | services 分层，无巨石 | server.py 7,216 行（占后端 33%） | — |
| 测试代码 | **1 个文件 / 336 行 / 9 个用例** | **111 个文件 / 57,864 行 / 1,348 个用例** | **B 172×** |
| CI | **无 .github/workflows** | ci.yml + desktop.yml（三平台矩阵） | B 独有 |
| 文档 | 6 个 md / 约 48KB，无 docs/ | 193 个 md / 10,979 行 | B 32× |
| 运行时依赖 | 15 个 Python + 52 个 npm，锁 1,096 包 | 2 个 Python + 3 个 devDeps，锁 288 包 | B 极轻 |
| 提交 / 时间 | 257 commits / 13.5 个月（0.63/天） | 65 commits / 10 天（6.5/天） | B 快 10× |
| 发布 | 29 个 release，跨 v0.5.0→v0.9.8 | 4 个 tag，10 天内 | A 7× |
| 社区 | 1,256 stars / 224 forks / 8 贡献者 | 1 star / 0 fork / 2 贡献者 | A 数量级领先 |
| 许可证 | AGPL-3.0，口径一致 | **LICENSE=MIT，package.json=UNLICENSED** | 需修 |

> 注：A 的 stars/commits/release 数据经 GitHub API 核实（本地为 shallow clone）；B 的 stars 对应公开仓 `z19910928-afk/ai-creation`（2026-10-12 创建），与本地 65 commits 无法互证。

---

## 2. 12 维差距矩阵

| # | 维度 | 墨屿覆盖度 | 差距等级 | 一句话判断 |
|---|---|---:|---|---|
| 1 | 产品定位与功能全景 | 70% | 中等 | 核心写作闭环齐，缺工作流/提示词工坊/自定义卡片/灵感工作台 |
| 2 | 系统架构与技术栈 | 45% | 大 | 无前端框架、server.py 巨石、无 API 契约；但依赖与测试反超 |
| 3 | AI 编排与提示词工程 | 64% | 中等 | 上下文装配更强，提示词全硬编码、无 ReAct 工具循环 |
| 4 | 数据模型与一致性 | ~60%（估算） | 中等 | append-only/证据链代际领先；缺 schema 驱动卡片 |
| 5 | 编辑器与前端交互 | 58% | 中等 | 自动保存/无障碍/专注模式更强；textarea vs CodeMirror 是代差 |
| 6 | 工程质量与测试 | 85% | **墨屿领先** | 测试量 172×、CI 完整；缺 lint/typecheck/结构化日志 |
| 7 | 扩展性与生态 | 24% | 大 | 无自定义卡片类型、无输出模型、无模板市场、无插件 |
| 8 | 文档与社区成熟度 | 45% | 中等 | 文档量 32×、安装包更全；社区信号≈0、无贡献者设施 |
| 9 | 工作流 / 自动化引擎 | 25% | 大 | A 有 DSL+26 节点+触发器+Workflow Agent；B 全硬编码 |
| 10 | 长篇创作与上下文管理 | 85% | 小 | 检索/记忆/预算治理更强；缺 @DSL 模板与多轮预算续写 |
| 11 | 桌面分发与跨平台 | 92% | **墨屿领先** | 三平台原生 CI、真安装器、双击即用；缺 Linux/自动更新 |
| 12 | 量化指标与声明核验 | 61% | 中等 | 工程指标反超，规模/生态/发布节奏落后 |

**算术平均覆盖度 59.6%；按产品重要性加权后约 57%。**

差距等级分布：墨屿领先 2 项、小差距 1 项、中等 6 项、大差距 3 项、代际差距 0 项。

---

## 3. 关键结论：三条"反直觉"的发现

### 3.1 墨屿在"写作可信度"上不是追赶者，是领先者

A 的设定卡、关系图看起来更"结构化"，但它的数据层只有 11 张表、卡片内容覆盖即丢历史、没有 revision、没有 source span、没有认识论状态。B 有 67 张表、74 个触发器强制 append-only、source_span 双偏移 + quote_hash、candidate→accepted→canon 两级确认、epistemic_mode 五态、known_by 的 POV 知情门控。

**这是产品的护城河，不能为了"像 NovelForge"而拆掉。** A 的 Schema 驱动卡片值得借，但要**叠加在** B 的证据链之上，不是替换。

### 3.2 A 的"工作流引擎"是它真正的护城河，也是 B 最大的单点缺口

A 的 workflow 子系统 57 个文件 / 10,806 行，占后端 35%：代码式 DSL、26 个节点 8 类、1,651 行静态校验器、事件触发器、节点级 checkpoint、Workflow Agent 10 个工具。B 只有硬编码的四阶段规划和 11 节点 LangGraph 图，用户无法新增一条流程。

但要看清 A 的引擎也有硬伤：**没有 if/else、没有循环、`get_parallel_groups` 每组只有 1 条（不是真并行）、`engine/scheduler.py` 是死代码、工作流零测试**。所以 B 不需要照抄，应该做一个"更小但真能跑"的版本。

### 3.3 生态差距是时间函数，不是工程量

1,256 stars vs 1 star、224 forks vs 0、8 贡献者 vs 2。这部分**无法用人周压缩**。正确策略是先把"采用门槛"降到最低（许可证口径、贡献模板、README 分层、一键运行），然后靠时间滚。

---

## 4. 追赶路线（按 ROI 排序，不按维度排序）

### P0：4–8 周，立刻做，用户可感知最强

| 动作 | 解决什么 | 工作量 | 依据 |
|---|---|---|---|
| 提示词工坊（模板 CRUD + 内置保护 + 重置 + 预览） | B 提示词 100% 硬编码在 `server.py:67`、`moyu_agent_chat.py:56` | 3–4 人周 | A 有 31 个模板 + PromptWorkshop.vue |
| 多轮字数预算续写 | B 越界只拦截不修复（`server.py:5163` 直接 raise） | 2–3 人周 | A `continuation_budget_runtime.py` 316 行 |
| 许可证口径统一 + CONTRIBUTING/Issue/PR 模板 + README 分层 | LICENSE=MIT 与 package.json=UNLICENSED 冲突，采用者无法判断 | 1–2 人周 | A 的贡献体系 + README 866 行 |

**P0 合计 6–9 人周，直接补掉"AI 可控性"与"采用门槛"两个最刺眼的短板。**

### P1：8–14 周，补产品结构缺口

| 动作 | 解决什么 | 工作量 |
|---|---|---|
| Schema 驱动设定卡（CardType + JSON Schema + 动态表单 + 实例覆写） | B 设定只有 4 个固定字段，type 是自由文本 | 4–6 人周 |
| server.py 拆分 + API 契约化（路由层/服务层/OpenAPI） | 7,216 行巨石，151 处 if/elif 路由，无契约 | 4–6 人周 |
| 编辑器 CodeMirror 6 + 行内 diff 逐条裁决 | textarea vs CodeMirror 是代差；B 只能整段对照 modal | 4–6 人周 |

**P1 合计 12–18 人周。这三项做完，产品域覆盖度从 70% 提到约 85%。**

### P2：20–28 周，平台化（要不要做取决于战略）

| 动作 | 说明 | 工作量 |
|---|---|---|
| 通用工作流引擎（DSL + 节点注册表 + 触发器 + 运行记录 + Agent 改流程） | 建议做"最小可用版"：8–10 个节点、2 个触发器、节点级 checkpoint、可视化只读 | 20–28 人周 |
| 扩展生态（卡片模板 / 项目模板 / 插件 SDK / 卡片市场） | 依赖 P2 第一项，否则没有分发物 | 12–18 人周 |

**P2 合计 32–46 人周。这是"从写作工具变成创作平台"的门票，但不是当前阶段的必要项。**

### 明确不追

| 不追项 | 理由 |
|---|---|
| 前端全量 Vue/TS 重写 | 44,277 行 vs 7,506 行，迁移成本 10–12 人周，用户零感知；只做"新页面用组件化"渐进式 |
| 社区规模对标 | 1256 stars 是 13.5 个月积累，无法用工程量压缩 |
| Linux 全量支持 | 1.5–2 人周可做，但当前用户群以 Mac/Win 为主，ROI 低于 P0 |
| 自动更新全链路 | 先做"检查新版本 + 跳转下载"（0.5–1 人周），electron-updater 全自动放后面 |

---

## 5. 总量估算

| 范围 | 人周 | 全职 1 人耗时 |
|---|---|---|
| 只做 P0 | 6–9 | 1.5–2 个月 |
| P0 + P1（补 80% 可感知差距） | 18–27 | 4–6 个月 |
| P0 + P1 + P2（全面追平功能面） | 50–73 | 12–16 个月 |
| 12 维全部原始估算相加（含重复项，仅供上界参考） | 126–181 | 2.5–3.5 年 |

**建议：锁定 P0+P1，用 5–6 个月把覆盖度从 60% 拉到 85%，P2 等工作流需求被真实用户喊出来再做。**

---

## 6. 墨屿不该丢的 6 张牌

1. **不可篡改正文**：document_revision + 74 个触发器，A 完全没有 revision 概念。
2. **原句证据链**：source_span 双偏移 + quote_hash + stale 失效，A 的 known_by 只是展示字段。
3. **认识论一致性**：epistemic_mode 五态 + POV 知情门控 + valid_start/end，支撑"谁在何时知道什么"。
4. **测试与 CI**：111 个文件 / 57,864 行 / 三平台桌面矩阵，A 只有 1 个测试文件且无 CI。
5. **桌面交付**：三平台原生构建 + 真安装器 + 双击即用 + SHA256 清单，A 只有 Windows 便携包且需手工起后端。
6. **本地优先**：2 个 Python 依赖 + 标准库 http.server，A 依赖 FastAPI/SQLModel/LangChain 一整套。

---

## 7. 证据索引（关键文件）

**NovelForge**
- 工作流引擎：`backend/app/services/workflow/`（57 文件 / 10,806 行）、`validator.py`（1,651 行）、`triggers.py`
- 上下文 DSL：`frontend/src/renderer/src/services/contextResolver.ts`（765 行）
- 多轮预算：`backend/app/services/ai/generation/continuation_budget_runtime.py`（316 行）
- 卡片类型：`backend/app/bootstrap/card_types.py`、`frontend/src/renderer/src/components/shared/SchemaStudio.vue`
- 提示词：`backend/app/bootstrap/prompts/`（31 个模板）、`backend/app/services/prompt_service.py`
- 测试现状：`backend/tests/test_reset_and_bootstrap.py`（唯一测试文件）

**墨屿**
- 数据契约：`docs/schema-v1.sql`、`docs/schema-v1.1-migration.sql`
- 上下文与记忆：`backend/moyu_context.py`（1,177 行）
- 字数契约：`backend/moyu_output.py`（703 行）
- 写作状态机：`backend/moyu_writing_agent.py`（649 行）、`backend/moyu_agent_checkpoint.py`
- 规划流：`backend/moyu_planning.py`（1,020 行）
- 桌面分发：`desktop/electron-builder.yml`、`scripts/build_desktop.py`、`.github/workflows/desktop.yml`
- 巨石：`backend/server.py`（7,216 行）

---

## 8. 待核验 / 存疑项

1. **维度 4（数据模型）的 coverage_pct 原始返回值被截断**，表中 60% 为按 key_gaps 严重度与 moyu_strengths 条数推算的估算值，非子智能体原值。
2. 墨屿 GitHub 仓库 `z19910928-afk/ai-creation` 仅 1 star，与本地 65 commits 无法互证；若对外发布仓不是这个，社区指标需重测。
3. 历史文档 `docs/ai-writing-system-gap-audit.md:10` 把 NovelForge 指向 `LunaRime/novelforge`（13 stars），与本次分析的 `RhythmicWave/NovelForge`（1256 stars）不是同一项目，旧结论不能直接采信。
4. A 的 stars/forks/release 数来自 GitHub API 实时查询，会随时间变化。
