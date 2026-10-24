# 第二阶段独立验收（2026-10-23）

> 验收对象：Q1-4 提示词工坊（`backend/moyu_prompt_templates.py` + `frontend/prompt-workshop.*`）、
> Q1-5 多轮字数预算续写（`backend/moyu_continuation.py` v1.1 + `benchmarks/budgeted/`）、
> 以及两者之间的生产接线状态。
>
> 验收方式：captain 在团队成员路由（workbuddy 池）连续 402/502 不可用后亲自执行。
> **这是自己验自己，不是独立第三方验收** —— 独立性降级必须如实记录，不能当作 pass 的等价物。

## 0. 结论

| 项 | 结论 |
|---|---|
| 提示词工坊后端 | **通过**（含边界探针） |
| 提示词工坊前端 | **通过**（13/13 浏览器用例，exit 0） |
| 预算续写引擎 v1.1 | **通过**（33 用例 + 基准 11 项验收 PASS） |
| 预算续写 **生产接线** | **未通过 —— 根本没接** |
| Q1「3000 字一次通过率 ≥80%」 | **未达成**（77.8%，差 2.2pt） |

**一句话**：模块都能跑、都有测试，但「作者真的能用到多轮预算续写」这件事没发生。
`backend/moyu_continuation.py` 目前只被基准引用，生产链路一次都没调用它。

## 1. 命令与结果（全部本机实跑）

| 命令 | 退出码 | 证据 |
|---|---|---|
| `.venv/bin/python -m py_compile backend/moyu_prompt_templates.py backend/moyu_continuation.py backend/server.py backend/moyu_agent_chat.py backend/moyu_agent_settings.py backend/moyu_planning.py backend/moyu_revision.py backend/moyu_settings.py benchmarks/budgeted/run.py benchmarks/longform/run.py` | 0 | 无输出 |
| `.venv/bin/python tests/prompt_workshop_fixtures.py` | 0 | `Ran 12 tests ... OK` |
| `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `OK {'cases': 33}` |
| `Electron --check frontend/prompt-workshop.js` | 0 | JS 语法通过 |
| `Electron tests/prompt_workshop_browser_regressions.cjs` | **0** | `desktop_scenario_count=13`，13/13 passed |
| `.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` | **0** | 11 项验收全 PASS，`verdict：PASS` |
| `.venv/bin/python tests/consistency_fixtures.py` | 0 | `OK {'dirty': 13.0, 'clean': 100.0}` |
| `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests OK` |
| `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests OK` |
| `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests OK` |
| `.venv/bin/python tests/legacy_deleted_dependency_http_fixtures.py` | 0 | 49 methods，`compared_tables: 73`，`source_unchanged: true` |
| `.venv/bin/python tests/portable_backup_fixtures.py` | 0 | 43 tests / 532 subtests，0 failed |

全量契约 fixture 另跑一轮：**89 个文件 89 ok / 0 FAIL**。

## 2. 提示词工坊：边界探针（自造，非 fixture）

在临时库上直接调 `moyu_prompt_templates` 模块，逐条验证保护边界：

| 探针 | 结果 |
|---|---|
| 内置数量 | 22 条，全部 `is_builtin=True` |
| 用 `pt_builtin_` 前缀伪造自定义 id | 允许创建，但 `is_builtin=False`，删除不误伤内置（内置仍 22 条，渲染表未变） |
| 删除内置 | `PromptTemplateConflict`「内置模板不可删除，请使用重置恢复出厂内容。」 |
| 修改内置 | `PromptTemplateConflict`「内置模板不可修改，请先克隆为自定义模板。」 |
| 内置被写脏后 `reset` | 恢复出厂正文（`续写当前章节，推`） |
| 无内置对应的自定义 `reset` | `ValueError`「自定义模板没有对应的内置模板，无法重置。」 |
| 正文 100001 字符 | `ValueError`「模板正文不能超过100000个字符。」 |
| 空名 / 空 action / 非法 action 字符 | 均 `ValueError` |
| 多余键（`is_builtin: true`） | `ValueError`「自定义模板包含不支持的选项。」（不能越权改内置标） |
| `{{instruction}}` 占位符 | 原样入库、渲染时显式替换，不用 `str.format` |
| 自定义覆盖内置后删除 | 渲染回落内置正文，`builtin_body` 未被污染 |

**发现的口径问题（低，不阻断）**：同一 `action` 允许存在多条自定义模板，且「后写覆盖先写」由
`updated_at` 排序决定。因此编辑**较早**那条会让它瞬间变成生效版本 —— 探针实测：
版本 A、B 并存时渲染 B，改 A 之后渲染变成 A。

这不是 bug（`_refresh` 的 docstring 就是这么定义的），但**面板没有把「哪条正在生效」显示出来**：
列表只给内置行标「被覆盖」，多条自定义里谁是生效版没有标识。作者可能在不知情下改了非生效的那条，
以为改动会生效。**建议下一轮**：列表里给生效版本加「生效中」徽章，或在同 action 多自定义时禁止新建并引导编辑现有条目。

## 3. 预算续写：引擎通过，指标未达标

`--compare --seed 20261023 --repeat 2`，11 项验收全 PASS：

| 指标 | v1.0 | v1.1 | 变化 |
|---|---|---|---|
| 全部场景 | 14/20 = 70.0% | 16/20 = **80.0%** | +10.0pt |
| **3000 字（Q1 目标）** | 6/9 = 66.7% | 7/9 = **77.8%** | +11.1pt，**仍差 2.2pt** |
| 2500 字 | — | 5/5 = 100% | — |
| 4000 字 | — | 4/6 = 66.7% | — |
| 合格档 | 13/13 | 13/13 | 不变 |
| 劣质档 | 1/5 | 3/5 | +2 |
| 中断档 | 0/2 | 0/2 | 不变 |
| 平均轮数 | 3.20 | 3.85 | +0.65 |

翻转：`fail→pass` = sc-16、sc-17（均 underpowered，6→12 轮，`max_rounds`→`target_reached`，`extended_rounds=6`）；
`pass→fail` = **0 个**。仍未通过 4 个：sc-15（volatile 超冲 4000→4661）、sc-18（stalling，重试 1 次仍只有 46 字）、
sc-19（budget_limited）、sc-20（canceled）。

**v1.1 的提升只来自「弹性轮数」这一个机制**：`retries=1`（只触发 sc-18，且没救活），
`stall_hint_rounds=1`（同样没救活）。诚实结论：停滞重试在当前场景集上**没有产出**。

## 4. 最严重的问题：续写引擎没有接进生产

```
$ grep -rn "moyu_continuation\|run_budgeted_continuation" backend/ frontend/ desktop/
backend/moyu_continuation.py:25        (docstring 示例)
backend/moyu_continuation.py:86        (__all__)
backend/moyu_continuation.py:209       (定义)
```

生产链路里**零调用**。作者点「续写」实际走的是：

- 前端 `frontend/author-workspace.js:2288` → `POST /api/ai/queue`
- 后端 `backend/server.py:6690` 起 → `create_ai_job` → `process_ai_job` → **单次** `generate_ai`
- 轮数上限来自 `backend/moyu_writing_agent.py`：`max_calls` 被硬编码校验为
  `3 if mode in PROSE_MODES else 2`（`moyu_writing_agent.py:616`），**按调用次数算，不按字数算**

也就是说：**多轮已经在跑，但驱动变量是「调了几次模型」，不是「写够多少字」。**
`moyu_output.py` 里单次的 `target_characters`（`:377`、`:425`、`:522`、`:579`）只在
`output_prompt_constraints` 里变成一句「正文长度目标约 N 字」，模型可以不听，没有第二轮补救。

**这是 Q1-5 的真实缺口**：引擎被基准验证过，但作者用不上。下一轮必须做的接线是：

1. `process_ai_job` 里对 `task == "continue"` 且 `_output_contract` 带 `target_characters` 的作业，
   用 `run_budgeted_continuation` 驱动多轮，而不是单次 `generate_ai`；
2. 逐轮生成的正文要能落进同一份候选（现有 `persist_ai_chunks` 是流式追加，语义可复用）；
3. 轮数预算要接进 `app_quota_reservation`，否则一次「续写」可能悄悄花掉 N 次模型额度；
4. 前端要能显示「已写 1,850 / 3,000 字，第 2 轮」。

风险提示：这会改动 `server.py` 的作业主路径，必须配 HTTP 级回归（干净库 + 假 provider），
不能只靠单元 fixture。

## 5. 文档一致性抽查

| 检查 | 结果 |
|---|---|
| 策略文档 Q1 表「内置 22 个模板可编辑」 | 与 `len(BUILTIN_TEMPLATES)` 一致 ✅（原为 31，已改） |
| `docs/consistency-engine.md` 裸行号 | 无残留（grep `moyu_consistency.py:[0-9]+` 无输出）✅ |
| `docs/consistency-engine-spec.md` 裸行号 | 无残留 ✅ |
| `docs/consistency-budget-v1.1-2026-10-23.md` 数字 | 与 JSON 一致（验收项 9 自动核对通过）✅ |
| `benchmarks/budgeted/README.md` 验收项数 | 已写「8 项 + compare 模式追加 3 项」✅ |

## 6. 已知边界（本轮不修）

1. **验收独立性降级**：团队成员路由全部 402/502，本报告由实现者本人执行，不是第三方验收。
2. `stall_hint_rounds=1` 与 `retries=1` 在 20 个场景上都没有带来翻转，等于 v1.1 的「停滞重试」还没被验证到有效。
3. 基准用合成生成器，生成器与引擎同源，无法证明对真实模型输出有效。
4. 续写未接线（第 4 节），是功能缺口不是测试缺口。
5. 同 action 多自定义时无「生效中」标识（第 2 节）。

## 7. 复现

```bash
cd /Users/zhaozhen/Downloads/神经网络写作

# 引擎与工坊
.venv/bin/python tests/prompt_workshop_fixtures.py
.venv/bin/python tests/budgeted_continuation_fixtures.py

# 浏览器（必须绝对路径 + 指定 python）
ELECTRON_RUN_AS_NODE=1 \
  MOYU_PLAYWRIGHT_MODULE="$PWD/node_modules/playwright" \
  MOYU_TEST_PYTHON="$PWD/.venv/bin/python" \
  ./node_modules/electron/dist/Electron.app/Contents/MacOS/Electron \
  tests/prompt_workshop_browser_regressions.cjs

# 基准
.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2
```
