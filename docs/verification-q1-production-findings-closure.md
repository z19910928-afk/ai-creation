# Q1 收尾冲刺 · t5 findings 修复闭环账（P1–P11）

**用途**：`docs/verification-q1-production-2026-10-23.md`（t5 独立验收）判 `needs_revision` 后，P1–P11 的修复分散落在 t8（接线额度/余量透传 + 顶层余量）与 t14（`round_target_ratio` 透传）以及 docs-engineer 的文档修订里，系统账上却没有一条任务显式关闭它们。本文件逐条落位：**每条 finding 现在被什么证据关掉、或为什么明确不关**。

**性质**：只做记账，不改代码、不改测试、不改历史验收报告。所有代码引用都取自当前工作树字节，所有输出都来自本机实跑（未转抄）。

**取证时间**：2026-10-24（Asia/Shanghai）。

---

## 一、总表

| Finding | 原 severity | 当前状态 | 关闭证据（命令 + 关键输出） |
|---|---|---|---|
| P1 生产链路丢弃超冲额度提示 | high → 复核后 medium | **closed**（残余转 P7/P9） | `sed -n '1587,1608p' backend/server.py` 显示 `for key in ("max_characters","overshoot_hint")` 写入 material；探针 1 第 2 轮 `max_characters=460`，等于引擎 `round_allocation(200,600)=460` |
| P2 首轮无额度约束，`round_budget()` 生产零调用 | high | **closed** | `grep -n "opening = moyu_continuation.round_budget" backend/server.py` → `1651`；探针 1 首轮 `max_characters=660 = floor(1.1×600)`、`remaining_characters=600` |
| P3 3000 字子集 77.8% < 80% | high | **未达成口径**（不修，captain 已裁决） | `benchmarks/budgeted/results/budgeted-v1.2-baseline.json`：`by_target["3000"]={"total":9,"first_pass":7,"rate":0.7778}`；sc-15 `characters=4661/within_tolerance=false`、sc-18 `characters=46/stop_reason=no_progress` |
| P4 生产接线文档夸大超冲保护范围 | medium | **closed** | `docs/continuation-production.md:168` 已改为「第 1 轮由 `create_ai_job()` 冻进开场提示，第 2 轮起由 `continuation_round_material()` 逐轮透传」；裸行号 `grep` 无输出（见 §四） |
| P5 按钮禁用 bug 无回归断言 | low | **已知边界** | `grep -c "disabled" tests/prompt_workshop_browser_regressions.cjs` → `0`（exit 1）、`grep -c "刷新"` → `0`（exit 1）：确认无断言，见 §二 P5 的不阻断理由 |
| P6 t2 报告用例数与实测不符 | low | **closed** | 本轮实跑 `budgeted continuation fixtures: OK {'cases': 47}`、HTTP `Ran 17 tests`；口径改为引用命令输出，不再写死数字 |
| P7 恢复期三件套未透传 | medium（新）→ low 残余 | **残余留待下轮**（`deficit_characters` / `pace_hint`） | `grep -n "deficit_characters\|pace_hint\|stall_retry_attempt" backend/server.py` → 无输出（exit 1）；已透传的是 `min_characters` / `stall_retry_min_characters` / `minimum_hint` |
| P8 顶层 `remaining_characters` 恒为首轮快照 | medium（新） | **closed** | 探针 1：第 2/3 轮 顶层 400/200 == 嵌套 400/200；探针 2：700/670/470/270 全部相等（≥2 轮） |
| P9 `stall_retry_attempt` 未透传 | low（新）→ nit 残余 | **残余留待下轮** | 同 P7 的 grep；`stall_retry_hint` 中文文本已含「本次至少写 N 字」与重试语义 |
| P10 文档 §3 透传表轮次描述不精确 | low | **closed** | `docs/continuation-production.md:93-94` 两行已逐轮写明「第 1 轮…／第 2 轮起…」 |
| P11 文档顶层余量警告落后于代码 | low（新） | **closed** | `docs/continuation-production.md:102` 已是修正后口径（原文见 §二 P11） |

分类计数：**closed 7**（P1/P2/P4/P6/P8/P10/P11）、**已知边界 1**（P5）、**未达成口径 1**（P3）、**残余留待下轮 2**（P7/P9）。

---

## 二、逐条证据

### P1（closed）超冲两键已进生产提示词

当前实现（`backend/server.py`）：

```python
# continuation_round_material()（backend/server.py:1545 起）
    # 第 1 轮的额度由 create_ai_job 冻进开场提示，同名字段会随 job context 继承
    # 到每一轮；第 2 轮起引擎在 prompt_context 里给出实时额度，必须用它覆盖，
    # 否则每一轮都在复读"本轮最多再写 <开场额度> 字"。
    for key in ("max_characters", "overshoot_hint"):     # backend/server.py:1587
        value = context.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) or (isinstance(value, str) and value):
            material[key] = value
```

- 写入 material 的行为 `1587` 行的白名单循环（`max_characters`、`overshoot_hint`）。
- 另外两个 v1.2 恢复期配额键在 `1597` 行的循环写入（`min_characters`、`stall_retry_min_characters`），`1604` 行合成 `minimum_hint`。
- 第 2 轮起叠加材料前会先 `pop` 掉陈旧开场值（`backend/server.py:2097` 的 `for stale in (...)`），避免继承错额度。

真实路径实测（假 provider 记录 messages，`POST /api/ai/queue`，`task=continue`、`target_characters=600`、`max_rounds=3`，每轮 200 字）：

```
== 探针1 target=600 默认口径 status=success rounds=3 characters=600
  round1: top_remaining=600 nested_remaining=None 相等=首轮无嵌套 | max_characters=660 | overshoot_hint=本轮最多再写 660 字，累计不要超过 660 字（目标 +10% 上限），超出即越界。
  round2: top_remaining=400 nested_remaining=400 相等=True | max_characters=460 | overshoot_hint=本轮最多再写 460 字，累计不要超过 660 字（目标 +10% 上限），超出即越界。
  round3: top_remaining=200 nested_remaining=200 相等=True | max_characters=260 | overshoot_hint=本轮最多再写 260 字，累计不要超过 660 字（目标 +10% 上限），超出即越界。
  首轮 floor(1.1*600)=660 / material max_characters=660 / remaining=600
  引擎 round_allocation(200,600) = 460 | 第2轮 material = 460
```

**结论**：第 2 轮 material 里 `max_characters` / `overshoot_hint` 都在，且数值等于引擎 `round_allocation()` 在同一 `characters_done` 上算出的值（460 == 460），不是接线层自己编的数。

停滞场景补充（`target=900`，第 2 轮只写 30 字逼出恢复期）：

```
== 探针2 target=900 停滞场景 status=success rounds=5
  round3: top=670 nested=670 相等=True | min_characters=58 | stall_retry_min_characters=58 | minimum_hint=本轮至少写 58 字：低于这个数仍算停滞，请换一种展开方式把缺口补回来。
  round4: top=470 nested=470 相等=True | min_characters=50 | stall_retry_min_characters=None | minimum_hint=本轮至少写 50 字：低于这个数仍算停滞，请换一种展开方式把缺口补回来。
  round5: top=270 nested=270 相等=True | min_characters=50 | stall_retry_min_characters=None | minimum_hint=本轮至少写 50 字：低于这个数仍算停滞，请换一种展开方式把缺口补回来。
  引擎 round_allocation(230,900)= 760 | 第3轮 material max_characters= 760
```

可复跑：`.venv/bin/python tests/budgeted_continuation_http_fixtures.py -k test_first_round_prompt_carries_opening_budget` → `Ran 1 test ... OK`；`.venv/bin/python tests/budgeted_continuation_http_fixtures.py -k test_stall_recovery_quota_reaches_the_prompt` → `Ran 1 test ... OK`（后者用引擎复算作 oracle，断言 58 不是硬编码常量）。

### P2（closed）首轮额度已由接线层注入

- `backend/server.py:1651` `opening = moyu_continuation.round_budget(0, budget["target_characters"], budget.get("round_target_ratio"))`，随后写入三个普通键（`1653-1655`：`remaining_characters` / `max_characters` / `overshoot_hint`），位置在 `ai_result_metrics` / `ai_prompt_preview` / 冻结之前，因此可从 `consent_json.request_audit` 事后审计。
- 实测：首轮 material `max_characters=660`，`floor(1.1×600)=660`；`remaining_characters=600`（= target，第 1 轮尚未写）。
- 可复跑：`.venv/bin/python tests/budgeted_continuation_http_fixtures.py -k test_first_round_prompt_carries_opening_budget`（同时断言首轮真实 messages 与 `consent.request_audit.payload.messages` 逐字相等）。

### P3（未达成口径，明确不修）

- `benchmarks/budgeted/results/budgeted-v1.2-baseline.json` 实测：`summary.by_target["3000"] = {"total": 9, "first_pass": 7, "failed": 2, "rate": 0.7778}`，即 **7/9 = 77.8% < 80%**。
- 两个被点名的场景仍未通过：`sc-15` 目标 4000、实际 `characters=4661`、`within_tolerance=false`、`stop_reason=target_reached`（超上界 4400 共 261 字）；`sc-18` 目标 3000、实际 `characters=46`、`stop_reason=no_progress`、`first_pass=false`。
- `q1_target_met=false` 的判定来自「3000 字子集 ≥80%」这条验收阈值（该 JSON 本身不含 `q1_target_met` 键，判定由阈值给出）。
- **口径不动**：这是「指标未达成」，不是「缺陷已修」。14→16 的增益来自 sc-16/sc-17，不来自 sc-15/sc-18。任何报告不得把本条写成已达成。

### P4（closed）文档不再夸大超冲保护范围

`docs/continuation-production.md:168` 当前原文（§7 已知边界第 2 条）：

> 2. **超冲不可裁剪，`overshoot_guard` 保证的是「说到」不是「做到」。** 它承诺的上限只到「把本轮额度 `max_characters` 与 `overshoot_hint` 写进 `prompt_context`」这一步——第 1 轮由 `create_ai_job()` 冻进开场提示，第 2 轮起由 `continuation_round_material()` 逐轮透传（见第 3 节的透传表）；至于模型是否服从，接线层管不了，`backend/moyu_continuation.py` 也**不做正文裁剪**。

即：文档已按 t5 的要求区分「引擎产出 / 接线层透传 / 模型是否服从」三段责任，不再暗示「写了 `prompt_context` 就等于生产生效」。§3 透传表对应行见 P10。

### P5（已知边界，不阻断）

- 现状：`grep -c "disabled" tests/prompt_workshop_browser_regressions.cjs` → `0`（exit 1）；`grep -c "刷新"` → `0`（exit 1）。t3 修掉的「`mutate()` 在 busy=true 后未复位按钮导致刷新永久禁用」仍无回归断言。
- 不阻断的理由：该 bug 是前端状态复位问题，影响的是「提示词工坊工具栏按钮可点性」，与本次交付的「多轮预算续写接入生产」链路无数据/额度/正文交集；且现有 18 个浏览器用例覆盖了创建/保存/删除的正向路径，回归面积有限。
- 风险交代：后续重构 `frontend/app.js` 的 `mutate()` 时该 bug 可能静默复发，复发现象是「刷新按钮变灰」，属人工可感知问题，不会污染正文或额度。

### P6（closed）用例数改由命令输出驱动

- 本轮实跑：`tests/budgeted_continuation_fixtures.py` → `budgeted continuation fixtures: OK {'cases': 47}`；`tests/budgeted_continuation_http_fixtures.py` → `Ran 17 tests ... OK`。
- 引擎用例数在 t5 之后又增长（41 → 43 → 47，随引擎能力增加），因此本轮起报告一律引用命令输出，不写死数字；t5 报告里那处 41/43 的差异保留为历史事实，不回改。

### P7（残余留待下轮）`deficit_characters` / `pace_hint` 仍未透传

- 已闭合部分：`min_characters` / `stall_retry_min_characters` 已透传，并合成 `minimum_hint`（「本轮至少写 N 字」），探针 2 第 3 轮 `min_characters=58` 可证。
- 仍未透传：`deficit_characters`（距达标下限还差多少字）、`pace_hint`（引擎给的进度提示文本）。
- 为什么可以留到下一轮：`minimum_hint` + `continuation_requirement`（「已写 X 字，目标 Y 字，还差约 Z 字」）已经用自然语言表达了同一信息——模型要据此决定写多少，缺的是「距下限的缺口」这个冗余表达，不是决策依据；且 `deficit_characters` 只影响提示词措辞，不影响额度计算、chunk、记账与判定。
- 下一轮的验收方式：在 `continuation_round_material()` 增加两键透传 + fixture 断言停滞轮 material 里出现 `deficit_characters`，并把 `pace_hint` 并入 `minimum_hint` 同一条提示。

### P8（closed）顶层余量每轮跟住嵌套值

`backend/server.py:1573`：`material["remaining_characters"] = remaining`（无条件覆盖首轮快照，方案 A）。实测（见 P1 探针输出）：

- 探针 1：第 2 轮 顶层 400 == 嵌套 400；第 3 轮 顶层 200 == 嵌套 200。
- 探针 2：第 2–5 轮 顶层 700 / 670 / 470 / 270 与嵌套逐轮相等。

可复跑：`.venv/bin/python tests/budgeted_continuation_http_fixtures.py -k test_top_level_remaining_tracks_each_round` → `Ran 1 test ... OK`（断言顶层 == 嵌套、逐轮递减 600→400→200、第 2 轮不再出现 `"remaining_characters":600`）。

### P9（残余留待下轮）`stall_retry_attempt` 仍未透传

- 现状：`grep -n "stall_retry_attempt" backend/server.py` 无输出（exit 1）。
- 为什么可以留到下一轮：`stall_retry_hint` 文本已含重试语义与「本次至少写 N 字」，模型能据此换展开方式；`stall_retry_attempt` 是引擎内部计数，对生成决策是冗余信息。
- 下一轮验收方式：与 P7 合并处理，作为同一次材料补全的附项。

### P10（closed）文档 §3 透传表轮次精度已修

`docs/continuation-production.md:93-94` 当前原文：

> | `remaining_characters`（顶层） | 第 1 轮由 `create_ai_job()` 冻结成 `target_characters`；第 2 轮起引擎给实时余量 | 每轮都在；第 2 轮起与 `continuation_round.remaining_characters` 逐轮相等 |
> | `max_characters`、`overshoot_hint` | 第 1 轮由 `create_ai_job()` 调 `moyu_continuation.round_budget(0, target_characters)` 冻进开场提示；第 2 轮起引擎给实时值 | 第 1 轮 = 开场注入值；第 2 轮起用实时值**覆盖**同名字段（不覆盖就会每轮复读开场额度） |

即两行都已写明「第 1 轮 / 第 2 轮起」的分界，与当前代码一致。

### P11（closed）顶层余量警告已按修正后口径重写

`docs/continuation-production.md:102` 当前原文（节选）：

> - **顶层 `remaining_characters` 与嵌套值同源，第 2 轮起逐轮相等。** `create_ai_job()` 冻结 job context 时它等于 `target_characters`（第 1 轮还没写一个字，此时它就是「本次目标」）；第 2 轮起 `continuation_round_material()` 无条件用引擎给出的实时余量覆盖它（`material["remaining_characters"] = remaining`），`_run_budgeted_continuation_job()` 再把返回值 `round_context.update(...)` 合进提示词。实测 target=1500、5 轮真实 HTTP：顶层 1500 / 1300 / 1100 / 900 / 700，嵌套第 2 轮起同为 1300 / 1100 / 900 / 700——**第 2 轮起两者逐轮相等且严格递减**，取哪个都不矛盾；只有第 1 轮的顶层值语义不同（是目标而非余量）。

旧版「8 轮恒为 1500，要引用余量就引嵌套值」的警告已不存在；新口径与 P8 的实测一致。

---

## 三、剩余与下轮边界（不进本轮交付）

1. **P3 指标未达成**：3000 字子集 77.8% < 80%，`q1_target_met=false`。这是产品指标，不是接线缺陷；下轮若要冲 80%，需在引擎策略或基准模型服从度假设上做决策（captain 裁决），不通过改判定条件达成。
2. **P7/P9 残余键**：`deficit_characters` / `pace_hint` / `stall_retry_attempt` 三个材料键。下一轮一次性补齐并加 fixture 断言。
3. **P5 已知边界**：提示词工坊的按钮禁用无回归断言，建议随下一次前端改动补一条浏览器用例。

---

## 四、复跑清单（本轮实际执行，exit code 均为 0）

| 命令 | 关键输出 |
|---|---|
| `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | `Ran 17 tests in 9.777s` — `OK`（exit 0） |
| `.venv/bin/python tests/budgeted_continuation_fixtures.py` | `budgeted continuation fixtures: OK {'cases': 47}`（exit 0） |
| `grep -nE 'moyu_continuation\.py:[0-9]+\|server\.py:[0-9]+' docs/continuation-production.md; test $? -eq 1` | 无输出，`test` 通过（exit 0） |

---

**本文件只用于关闭 t5 的 findings，不改变 t5 的原始 verdict（`needs_revision`）——历史记录保留，本文件是 append-only 的关账说明。**
