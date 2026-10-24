# t17 修复的 content-bound 独立验收（t18）

**结论：pass。** P12（失败/取消路径 `dispatched_calls` 少报真实调用次数）与 P13（部分报告键集合塌缩）在当前字节上均已修复并有独立证据；六条命令全部实跑通过，6 个被测文件验收前后 sha256 与行数逐字节一致。

验收时间：2026-10-24 10:0x–10:2x（Asia/Shanghai）。本报告只陈述我自己跑出的数字；作者自报数字不作为证据。

---

## 零、被测字节（验收开始 → 验收结束，逐字节一致）

| 文件 | sha256（前 = 后） | 行数 |
| --- | --- | --- |
| `backend/server.py` | `5024a6a542dacbafeef1fa7d58fa66ffeb65cc903f3518afc93e9fa3bc3edc46` | 7909 |
| `backend/moyu_continuation.py` | `40ab1c32802297066f24d6b90a423b511ba86c5581301e9ff58a6e79b24755ff` | 857 |
| `tests/budgeted_continuation_http_fixtures.py` | `e6e6a96fb703f66830f14b50615a42180759a555c350627ad485ae00546f6424` | 761 |
| `tests/budgeted_continuation_fixtures.py` | `46b2d9ad169c1733a8aebf68447369dfa6cf608e53cb8b4bc3764462ee451cfe` | 1115 |
| `tests/continuation_progress_http_fixtures.py` | `815f03a4d03c77dd2bdf51877f4e8007e9e35d45424c54a588573edb03a57182` | 339 |
| `docs/continuation-production.md` | `696f77eea29c4d41155a79d3ae3b6620ba8f30e76415d424ecb1857d179423a7` | 188 |

比对方式：开工时 `shasum -a 256` + `wc -l` 写入 `/tmp/t18_pre_hashes.txt`；六条命令与全部探针跑完后重算（`/tmp/t18_post_hashes.txt`），6 个 hash 全部相同，行数 7909/857/761/1115/339/188 全部相同。运行测试没有改动被测字节，也没有重写文档。

## 一、命令表（全部我自己执行）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/*.py` | 0 | 无输出（全部模块编译通过） |
| 2 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 19 tests in 11.015s` / `OK` |
| 3 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 47}` |
| 4 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 3.003s` / `OK` |
| 5 | `.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` | 0 | `verdict：PASS`；v1.2 digest `sha256:9a6a72f079fe2b9c49a7619dd09cbec6bd77863c6773b94b6827dfc84e6b98ac`；3000 字 6/9 → 7/9（77.8%） |
| 6 | `for f in tests/*_fixtures.py; do …; done` | 0 | 91 个 fixture 全部执行，`sweep_fail.txt` 未生成；全部日志 grep `FAILED|Traceback|^FAIL |FAIL:` 无命中 |

基准产物复核（`benchmarks/budgeted/results/budgeted-v1.2-baseline.json`）：`summary.total=20`、`first_pass=16`、`rate=0.8`；`by_target` 2500=5/5、3000=7/9（0.7778）、4000=4/6；`q1_target_met=false`；`acceptance` 17 条全 `passed`，其中 `body`「20/20 哈希一致」、`candidate`「20 个场景 candidate_only」、`determinism`「repeat=2，digest=sha256:9a6a72f0…b98ac」。终端里另一个 digest `sha256:847d620f…` 是 v1.1 对照臂，不是 v1.2，不构成不一致。

## 二、P12 专项：`dispatched_calls` 是否等于 provider 实收次数

自造探针 `/tmp/probe_t18.py`（只读；子类化 `tests/budgeted_continuation_http_fixtures.BudgetedContinuationTests`，自建假 SSE provider，逐场景重新 `setUp()` 拿独立 DB）。**调用次数由探针自己数 provider 收到的请求**，不读被测断言。结果：`CHECKS=77 FAILS=0`。

| 场景 | provider 实收 | `report.dispatched_calls` | `report.rounds` | `len(round_metrics)` | 结论 |
| --- | --- | --- | --- | --- | --- |
| 成功无 ratio（target 900，max_rounds 6） | N | N | N | N | 一致 |
| 成功带 ratio 1.35 | N | N | N | N | 一致 |
| 第 2 轮 500（case `boom`，ratio 1.35） | **2**（`airun_…`、`airun_…-r2`） | **2** | **1** | **1** | 一致，含失败轮 |
| 第 2 轮 500（无 ratio） | 2 | 2 | 1 | 1 | 一致 |
| 第 2 轮中途取消（case `gate2`，等满 2 次调用后 cancel + release） | **2**，无第 3 次 | **2** | **1** | **1** | 一致 |
| 日额度只够 1 轮（`limit=used+1`） | 1 | 1 | 1 | 1 | 未发出去的轮不虚报 |

失败/取消路径的其余断言（全部通过）：`stop_reason=failed` / `canceled`；第 1 轮 200 字正文保留（`safe_to_apply=false`）；`characters=200`；`usage_state=partial`；`budget_plan`/`retries`/`extended_rounds` 均为 `null`（不删键）；`target_reached=false`、`within_tolerance=false`；`consent_json.budgeted_continuation_report.dispatched_calls` 同为 2，且键集与 HTTP 返回的报告完全相同。

代码依据（符号锚点，供复核）：`backend/server.py` 的 `_run_budgeted_continuation_job.generate()` 在 `generate_ai(...)` 之前 `attempted += 1`；`not exc.request_dispatched` 时回退；失败/取消统一构造 `_budgeted_continuation_report(..., rounds=len(produced), dispatched_calls=attempted, ...)`，成功路径同式传 `attempted`。

## 三、P13 专项：报告键集合

探针逐键比对（`set(report)` 严格相等，不是子集）：

- 成功无 ratio：**17 键**；成功带 ratio：**19 键**（+`round_target_ratio`、`budget_plan`）。
- 第 2 轮 500（带 ratio）的键集 == 成功带 ratio 的键集（19），对称差为空。
- 第 2 轮 500（无 ratio）的键集 == 成功无 ratio 的键集（17）。
- 取消（带 ratio）的键集 == 成功带 ratio 的键集（19）。
- 不可得值语义：失败/取消时 `budget_plan`、`retries`、`extended_rounds` 为 `null`；`usage_state` 为 `partial`（已完成轮有上报、失败轮不可知）；额度未发出的场景为 `reported`。没有用 `0` 或空列表冒充。

## 四、文档与前端（P14 复核）

- `docs/continuation-production.md` 的「额度怎么算」一节把 `dispatched_calls` 定义为「实际发起的模型调用数」，并说明 `rounds` 与它可能不等（重试会让调用数多于有效轮数）；与实现的 `attempted` 语义一致。「已知边界」一节「审计要靠 `consent_json.budgeted_continuation_report` 里的 `dispatched_calls`」与探针实测（consent 与 HTTP 报告同值同键）一致。
- P14「前端已有入口但默认关闭」仍成立：`frontend/app.js` 的 `#aiBudgetContinuation` 复选框没有 `checked` 属性；`#aiBudgetRow` 初始 `hidden`，`syncBudgetContinuationRow()` 只在任务为 `continue` 时显示；入队前只有 `task==='continue' && budgetToggle?.checked` 才把 `budgeted_continuation` 放进请求体。文档「作者怎么触发」一节对入口、默认值与作者工作台无预算路径的描述与代码一致。

## 五、未达成项与已知边界

1. **Q1 ≥80% 仍未达成**：3000 字子集 7/9 = 77.8%，`q1_target_met=false`。本轮验收只看 P12/P13 修复与不回归，不改变该结论。
2. **一致性引擎「30 万字 × 3 部真实作品」验收未执行**（文档已如实写；当前基线是单部 40 章 105,182 汉字合成库，生成器与检测同源属自测）。
3. **未覆盖的边界**：①`request_dispatched=False` 的「回退 attempted」分支没有真实 provider 场景可触发（探针只能读代码确认）；②第 2 轮流中途失败且 provider 已吐部分文本时，`characters` 只算已完成轮、失败轮的半截文本不计入（假 provider 不吐半截，未实测）；③`round_target_ratio` 默认关闭，默认路径（17 键、首轮无 `budget_plan`）只按不传该字段验证。
4. 本轮未复核前序轮次已认定的其他事项（sc-15 超冲、sc-18 停滞、前端徽章语义），它们不属于 t17 的修复面。

---

本报告的全部结论绑定上述字节：`backend/server.py` 为 `5024a6a5…c3edc46`、`tests/budgeted_continuation_http_fixtures.py` 为 `e6e6a96f…546f6424`、`backend/moyu_continuation.py` 为 `40ab1c32…24755ff`、`docs/continuation-production.md` 为 `696f77ee…179423a7`。字节一变，本报告的 pass 失效，必须重跑 P12/P13 探针与六条命令。
