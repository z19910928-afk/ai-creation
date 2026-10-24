# 字节绑定复核（t16，2026-10-23）

> 目的：把 t5（全链路验收 pass）与 t11（第二阶段验收 pass）记录的 hash 基线对到**当前工作区字节**，
> 解除 delivery gate 的两条告警：
> 1. `completed implementation has no passing review`（t7 是 implementation，没有绑定式 review）
> 2. `t5 verdict predates content-bound verdicts`（t5 记录的是旧字节）

## 0. 结论

| 检查 | 结论 |
|---|---|
| t7（提示词工坊后端）当前字节 | **review pass**，无 blocker/high findings |
| t5 记录的 hash 与当前字节 | engine / 两个 fixture / longform run.py **完全一致**；`server.py` **按预期变化**（t7 在其后新增了提示词工坊路由） |
| 当前字节是否仍然通过关键命令 | **是**，5 条 fixture + 1 条浏览器回归全 exit 0 |

## 1. 当前字节 hash

```text
7aa174f2e0df7402cd40290a4d7df0603d651ca1a97e9631583f51b7471cf93c  backend/moyu_consistency.py
29156ddeeb84a18057cbb91d69052b33b474e4ccb4feac60fb8e385d041e2c63  backend/server.py
28fcc741208c3ad4e47411e72979b9dfdf95911191b7e37b3bed83c50595c1c7  tests/consistency_fixtures.py
86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a  tests/consistency_http_fixtures.py
05a08274d76dc374328601486ead48e61dd23305968ac27033f57bd0c29e9cea  benchmarks/longform/run.py
eb2cb7a43a45689493dd5e0828be27d2ca802426d7106481a3f9ca58e513c044  backend/moyu_prompt_templates.py
00f6cc1419abb06f0e3b276180946f5524914d6eef083d9088e46b00ea60ebaf  backend/moyu_continuation.py
```

## 2. 与 t5 记录的基线逐项对账

| 文件 | t5 记录 | 当前 | 判定 |
|---|---|---|---|
| `backend/moyu_consistency.py` | `7aa174f2…` | `7aa174f2…` | ✅ 一致 |
| `backend/server.py` | `28066e13…` | `29156dde…` | ⚠️ **已变化，原因明确**（见下） |
| `tests/consistency_fixtures.py` | `28fcc741…` | `28fcc741…` | ✅ 一致 |
| `tests/consistency_http_fixtures.py` | `86d0ad6d…` | `86d0ad6d…` | ✅ 一致 |
| `benchmarks/longform/run.py` | `05a08274…` | `05a08274…` | ✅ 一致 |

**`server.py` 为什么变了**：t5 通过（15:55 左右）之后，t7 在 `backend/server.py` 上新增了提示词工坊的
HTTP 路由与静态文件登记：

- `:5426`、`:5428` GET `/api/prompt-templates` 与 `/api/prompt-templates/<id>`
- `:5953` PUT
- `:6030`、`:6039` POST 新建与 `/reset`
- `:7184` DELETE
- `:5241-5242` `STATIC_FILES` 增加 `/prompt-workshop.js`、`/prompt-workshop.css`
- AI 渲染链改为 `prompt_templates.render(...)`

`git diff --stat backend/server.py` = **+134 / −22**，与上述改动量相符。
这是**新增功能**，不是对 t5 已验行为（`/api/consistency`、发表门禁、STATIC_FILES 前 18 项）的修改。

## 3. t7 review：当前字节仍然正确

### 3.1 结构和边界（静态）

| 检查 | 结果 |
|---|---|
| 内置模板数 `len(BUILTIN_TEMPLATES)` | **22**，全部 `is_builtin=True` |
| `ai.task.*` 覆盖 | **10 个**，与 `backend/server.py:70` 的 `AI_TASK_IDS`（10 项）逐一对应 |
| 内置 id 前缀 | `pt_builtin_` + `action.replace(".", "_")` |
| 内置保护 | `update_template` / `delete_template` 均抛 `PromptTemplateConflict` |
| 伪造内置前缀 | 允许创建但 `is_builtin=False`，删除**不误伤**内置（内置仍 22 条） |
| 重置 | 内置回出厂；无内置对应的自定义 → `ValueError`；自定义回同动作内置正文 |
| 参数校验 | 名称 1–120 字、正文非空 ≤100000 字、id/action 正则、多余键拒绝 |
| 渲染 | `_refresh(conn)` 重建 `_BODIES`；`render()` 用 `{{name}}` 显式替换，缺变量报错 |

### 3.2 当前字节的重跑证据

| 命令 | exit | 证据 |
|---|---:|---|
| `.venv/bin/python tests/consistency_fixtures.py` | 0 | `OK {'dirty': 13.0, 'clean': 100.0}` |
| `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests` / `OK` |
| `.venv/bin/python tests/prompt_workshop_fixtures.py` | 0 | `Ran 12 tests` / `OK` |
| `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests` / `OK` |
| `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests` / `OK` |
| `Electron tests/prompt_workshop_browser_regressions.cjs` | 0 | `desktop_scenario_count: 13`，全部 `passed: true` |

## 4. Findings

无 blocker、无 high。

| id | severity | 问题 | 建议 |
|---|---|---|---|
| F1 | low | 同一 `action` 允许多条自定义模板，生效版本由 `updated_at` 排序决定；面板只给内置行标「被覆盖」，多条自定义之间**没有「生效中」标识**。作者可能改了非生效那条以为会生效。 | 列表加「生效中」徽章，或同 action 多自定义时禁止新建并引导编辑现有条目。 |
| F2 | low | `docs/consistency-acceptance-2026-10-23.md` 的 hash 基线仍写 `server.py 28066e13…`，已落后于当前字节。 | 本文档作为增量对账，原文保留历史值，不覆盖。 |

## 5. verdict

**pass**。t7 在当前字节上满足契约；t5/t11 的结论对当前字节仍然成立，
唯一变化的 `server.py` 是新增功能而非修改已验行为，且新增部分有 12 个 fixture 用例 + 13 个浏览器用例覆盖。
