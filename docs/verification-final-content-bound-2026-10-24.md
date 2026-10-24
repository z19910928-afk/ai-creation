# 墨屿 Q1 最终验收（content-bound）· 2026-10-24

验收人：verifier（独立验收，只读被测代码，只写本报告）
验收口径：报告里的每个结论只对下面「零、被测字节」列出的 16 个文件负责。字节一变，本报告立即失效，必须重跑，不能复用。

## 零、被测字节（本报告唯一的身份锚点）

`shasum -a 256` 与 `wc -l` 实测（2026-10-24，验收开始时）：

| # | 文件 | sha256 | 行数 |
| --- | --- | --- | --- |
| 1 | `backend/server.py` | `3414715dd2ee0b946a56febcb16403594bc0ef7ae86bb2e0280dab4db447e997` | 7825 |
| 2 | `backend/moyu_continuation.py` | `40ab1c32802297066f24d6b90a423b511ba86c5581301e9ff58a6e79b24755ff` | 857 |
| 3 | `backend/moyu_consistency.py` | `7aa174f2e0df7402cd40290a4d7df0603d651ca1a97e9631583f51b7471cf93c` | 1128 |
| 4 | `backend/moyu_prompt_templates.py` | `eb2cb7a43a45689493dd5e0828be27d2ca802426d7106481a3f9ca58e513c044` | 436 |
| 5 | `frontend/app.js` | `ad405926520b24ca1969ef5179d4eb12a95f506994698c3b773c9de012378a51` | 1895 |
| 6 | `frontend/author-workspace.js` | `4f66f7228a6c134254075b52bbc6882f0c343e8e343c21359794c0888b567adb` | 2834 |
| 7 | `frontend/consistency-panel.js` | `d2121899e20498c4fd618c39a60e39b7d15c7b921322ddad0915936ac263dbfd` | 501 |
| 8 | `frontend/prompt-workshop.js` | `72edac05e7b196787ad7ebf77d0616926eb38c11522153ef7f4cfa80b7be14f5` | 756 |
| 9 | `benchmarks/budgeted/run.py` | `81fc77877d0e169b2a4fa98c68738c15b388f920b32bb4470f27c7de2ab72ff8` | 3778 |
| 10 | `tests/budgeted_continuation_http_fixtures.py` | `40f8006211a47c199be63446c912ac7d2d78412bd86ba7f6be7af41333f9338c` | 653 |
| 11 | `tests/budgeted_continuation_fixtures.py` | `46b2d9ad169c1733a8aebf68447369dfa6cf608e53cb8b4bc3764462ee451cfe` | 1115 |
| 12 | `tests/continuation_progress_http_fixtures.py` | `815f03a4d03c77dd2bdf51877f4e8007e9e35d45424c54a588573edb03a57182` | 339 |
| 13 | `tests/consistency_fixtures.py` | `28fcc741208c3ad4e47411e72979b9dfdf95911191b7e37b3bed83c50595c1c7` | 368 |
| 14 | `tests/consistency_http_fixtures.py` | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` | 199 |
| 15 | `tests/prompt_workshop_browser_regressions.cjs` | `05ae8d90cf75ce57d4f0b2c6ecd7e8e46c6c8abc6eef3c8c41077781f89f85a6` | 300 |
| 16 | `tests/frontend_browser_regressions.cjs` | `65445518aec3e582f60893b0aa22c14e3ea77d0d427932710b4f1215f61c1ce2` | 1921 |

**字节未漂移的证据：** 全部命令与探针跑完后重新执行同一组 `shasum -a 256`，16 个值与本表**逐字节相同**（对照文件 `/tmp/post_hashes.txt`）。也就是说：下面所有绿灯、所有失败，都是在这 16 份字节上得到的，不存在「测试跑的是旧代码」。

本报告是本轮验收唯一新增的文件；`git status` 里的其余修改（`backend/server.py`、`docs/CHANGELOG.md`、`tests/frontend_browser_regressions.cjs` 等）在本次验收开始前就已存在，不是验收过程中产生的。

## 一、结论

**needs_revision。**

命令层面全绿：47 个引擎用例、17 个 HTTP 用例、4 个进度用例、6 个一致性 HTTP 用例、59 个前端浏览器用例、18 个工坊用例、91 个 fixture 文件全量 sweep 全部 exit 0，基准 `--compare` 判定 PASS 且 `body_invariance` 20/20、`candidate_only` 20/20、v1.2 digest 与任务要求一致。

但不放行的原因是「说出来的数和真实不一致」，而且两处都在作者/审计能读到的地方：

- **P12（high）**：多轮请求在「某轮失败」或「轮中取消」时，`result.budgeted_continuation.dispatched_calls` 报 **1**，而 provider 实际收到了 **2** 次调用。文档把这个字段定义为「实际发起的模型调用数」，并明确说「审计要靠它」；测试也只在成功路径断言二者相等。这是审计字段在恰恰最需要审计的失败路径上说少了。
- **P14（medium）**：`docs/continuation-production.md` §7 第 8 条仍写「前端还没有入口，也没有花费面板……工作台『续写』按钮走的仍是单次调用（不发送 `budgeted_continuation`）」，与当前前端（有 `#aiBudgetContinuation` 开关、会发预算、会显示「共 N 轮」与容差判定）矛盾。

按验收契约「发现 blocker 或 high 必须 needs_revision」，P12 为 high，故本报告给出 needs_revision，而不是 pass。P13（low，部分报告形状缺键）一并列出。

## 二、命令表（全部本次实跑，退出码为终端实测）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/*.py` | 0 | 无输出（编译通过） |
| 2 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 47}` |
| 3 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 17 tests in 9.702s` · `OK` |
| 4 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 2.971s` · `OK` |
| 5 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 6 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.320s` · `OK` |
| 7 | `.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` | 0 | `verdict：PASS`，`real 0.145s`；v1.2 digest `sha256:9a6a72f0…b98ac`；`body_invariance 20/20`；`candidate_only 20/20` |
| 8 | `ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/Electron.app/Contents/MacOS/Electron tests/frontend_browser_regressions.cjs`（`MOYU_TEST_PYTHON`/`MOYU_PLAYWRIGHT_MODULE` 已设） | 0 | `passed=true`，`cases=59`，59/59 全 passed，0 fail，耗时约 1m23s |
| 9 | 同上跑 `tests/prompt_workshop_browser_regressions.cjs` | 0 | `passed=true`，`desktop_scenario_count=18`，18/18 passed |
| 10 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" > /tmp/final_sweep/<name>.log 2>&1; echo "$? $name" >> _codes.txt; done` | 0 | 91 个文件、`_codes.txt` 91 行、非零 **0** 行；全部日志无 `FAILED`/`Traceback`/`FAIL:`；`reference_passages_http_fixtures` 含 `"passed": true`、`"source_unchanged": true`（36 checks） |
| 11 | `.venv/bin/python /tmp/probe_final_t15.py`（自造探针，见第四节） | 1 | `CHECKS=43 FAILS=3`：A/B 两条 `dispatched_calls` 少报，A 一条报告缺 `usage_state` |
| 12 | 验收前后两次 `shasum -a 256` 对比（16 个被测文件） | 0 | 两次逐字节一致 |

基准细节（本次运行写出的 `benchmarks/budgeted/results/budgeted-v1.2-baseline.json` 实读，不抄他人数字）：

- `summary`: `total=20`、`first_pass=16`、`failed=4`、`rate=0.8`；`by_target`: 2500 → 5/5（1.0）、3000 → 7/9（0.7778）、4000 → 4/6（0.6667）。
- `q1_target_met=false`。
- `engine`: `variant=v1.2`、`max_stall_retries=1`、`closing_hint=true`、`overshoot_guard=true`、`stall_strategy=true`。
- `acceptance` 全 `passed`：`body`（20/20 哈希一致）、`candidate`（20 个场景）、`accounting`（20 个场景引擎计数与产出长度一致）、`stop_reasons`（5/5）、`v12_flags_pinned`（60 个场景开关全对，v1 臂提示注入 0）、`v12_envelope_hint_delivered`（57 轮/14 场景）、`v12_stall_strategy_switched`（sc-18）、`v12_appendix_non_counting`、`v12_retry_default_justified`、`baseline_untouched`（四个产物指纹未变）。
- 终端打印的重复性 digest `sha256:847d620f…` 是 v1.1 臂；v1.2 的 digest 必须以 JSON 的 `determinism.detail` 为准，即 `sha256:9a6a72f079fe2b9c49a7619dd09cbec6bd77863c6773b94b6827dfc84e6b98ac`。两者不是矛盾，是打印口径只打了先算完的那一臂。

## 三、未达成项（如实记录，不折算、不改口径）

1. **3000 字子集未达 80%**：7/9 = 77.8%，`q1_target_met=false`。20 场景整体 16/20 = 80.0%，但 Q1 的目标数字按 3000 字子集计。这一条与 `docs/CHANGELOG.md`、`docs/strategy-beat-novelforge-2026-10-23.md`、`docs/consistency-budget-v1.2-2026-10-23.md` 的写法一致，且三处都明写「未达成」。
2. **一致性引擎「30 万字 × 3 部真实作品」验收未执行**：当前基线是单部 40 章、105,182 汉字的合成库，注入 35 条矛盾召回 35/35、干净库 0 误报、单次分析中位 6.72 ms；生成器与检测引擎同源，属自测。`docs/strategy-beat-novelforge-2026-10-23.md:199` 已如实写明「尚未执行」，本报告确认这一条没有被包装成已达成。
3. **`round_target_ratio` 默认关闭**：不传该字段时 `normalize_budgeted_continuation({"target_characters":600})` 的键恰为 5 个（无 ratio）；首轮 `max_characters=660`；成功报告恰 16 键（无 `round_target_ratio`/`budget_plan`）；consent 里也无该键。显式传 1.35 才出现系数与 `budget_plan`。这是「默认行为不变」的证据，也意味着 Q1 默认交付不含每轮系数能力。

## 四、自造探针 A–D（只读，未改被测代码）

探针 `/tmp/probe_final_t15.py` 复用 `tests/budgeted_continuation_http_fixtures.py` 的 HTTP harness 与假 SSE provider，子类化其 `BudgetedContinuationTests`，自建 handler 记录每次 provider 请求的 `request_id` 与 `round`。结果：**43 checks，40 pass，3 fail（均为产品侧问题，非探针自身）**。

| 组 | 场景 | 实测 |
| --- | --- | --- |
| A | `round_target_ratio=1.35`、target 900、第 2 轮 provider 返回 500 | job `status=failed`；正文只保留第 1 轮 200 字；`safe_to_apply=false`；report=`{stop_reason: failed, rounds: 1, dispatched_calls: 1, characters: 200, target_characters: 900, round_target_ratio: 1.35}`；provider 实际收到 **2** 次（`airun_…`、`airun_…-r2`，round 1/2）；顶层 `usage_state=unknown`、`billing_state=unknown`、`usage=null`；report 无 `usage_state`/`within_tolerance`/`budget_plan`/`round_metrics`；consent 里的 `budgeted_continuation_report` 与 report 同形（6 键），系数已回显 |
| B | 同上，第 2 轮进行中取消 | `status=canceled`；正文保留第 1 轮 200 字；provider 实际 **2** 次；report `stop_reason=canceled`、`characters=200`，但 `dispatched_calls=1` |
| C | 默认路径（只传 `target_characters=600`） | `normalize` 返回 5 键（无 ratio）；`normalize(None)=None`；报告 16 键；首轮 `max_characters=660`；consent 无 ratio |
| D | `round_target_ratio` 边界 | 1.0 接受、首轮 `max=600`、report 回显 1.0；10.0 接受、首轮 `max=60`、`plan[0].planned_max_characters=60`（极紧，但属用户显式选择）；0.99/10.01/NaN/Infinity 全部 **400**「每轮额度系数必须在 1 到 10 之间。」；`False`/`"1.35"` 400「必须是 1 到 10 之间的数字。」；被拒请求不新增 `ai_run`（2→2） |

探针自身修过两处：早期用 `len(calls_of(...))` 取到了别的 handler 的调用记录；排序断言写错。修正后剩余 3 个 fail 全部是产品侧，已在第五节立案。

## 五、问题清单

### P12 · high · 失败/取消路径 `dispatched_calls` 少报真实调用次数

- **文件与符号**：`backend/server.py` 的 `_run_budgeted_continuation_job()`；失败分支 `:2150` 写 `"dispatched_calls": len(produced)`，成功分支 `:2190` 同式。`produced` 只装「已完成的轮」，失败/取消的那一轮没有产出文本，也就没被计入。
- **为什么是 high**：`docs/continuation-production.md:132` 把这个字段定义为「实际发起的模型调用数」，`:172` 明说「审计要靠 `consent_json.budgeted_continuation_report` 里的 `dispatched_calls`」；`tests/budgeted_continuation_http_fixtures.py:275` 在成功路径断言它等于真实调用数。现在失败与轮中取消这两条路径同时违反文档语义与测试契约，而这两条恰恰是「花的钱去哪了、为什么停」最需要审计的路径。一次 reservation 记 N 次调用的设计里，这个字段是唯一的分母。
- **复现**（约 30 行，无需真实模型）：
  1. 用 `tests/budgeted_continuation_http_fixtures.py` 的 harness 起一个假 SSE provider；
  2. 请求体带 `budgeted_continuation={target_characters:900, max_rounds:3, round_target_ratio:1.35}`；
  3. provider 第 1 轮正常返回 200 字，第 2 轮返回 500；
  4. 读 `GET /api/ai/runs/<id>` 的 `result.budgeted_continuation.dispatched_calls` → **1**；数 provider 侧收到的请求 → **2**。取消路径同理（第 2 轮进行中取消）。
- **要求怎么修**：把「派发次数」与「成功轮数」分开记账。在 `_run_budgeted_continuation_job()` 里维护 `attempted`（每次调用 provider 前 +1，含失败/取消的那次），失败/取消与成功路径统一用它写 `dispatched_calls`（`rounds` 仍记成功轮数）；如担心语义混淆，可在报告里加 `failed_dispatch` 布尔字段。补两条 fixture：`test_failed_round_counts_dispatch`（第 2 轮 500 时 `dispatched_calls == 2`）与 `test_cancel_mid_round_counts_dispatch`（取消时同样计 2）。修完 P12 才能谈 pass。
- **不修的最低替代**：若产品决定该字段只表示「成功轮数」，则必须同时改文档（`:132`/`:172`）与测试断言，并另加一个真正表示派发次数的字段；三者不能各说各话。

### P14 · medium · 生产文档说「前端没有入口」，与当前 UI 矛盾

- **文件与符号**：`docs/continuation-production.md` §7 第 8 条（当前 182 行版本的第 174 行）。
- **证据**：
  - 文档写「前端还没有入口……工作台『续写』按钮走的仍是单次调用（不发送 `budgeted_continuation`）」；
  - `frontend/app.js:348` 定义 `BUDGETED_CONTINUATION_DEFAULTS={target_characters:3000,max_rounds:6,min_round_gain:50}`；
  - `frontend/app.js:388-390` 的 `syncBudgetContinuationRow()` 在 `#aiTask==="continue"` 时显示 `#aiBudgetRow`，`app.js:1706-1707` 在渲染与任务切换时调用；
  - `frontend/app.js:1122-1124` 在作者勾选 `#aiBudgetContinuation` 且任务为 `continue` 时，把 `budgeted_continuation` 放进请求；
  - `frontend/app.js:355-376` 已经用 `within_tolerance`/`target_range` 渲染「容差内 / 超出容差 / 未达目标」，并用 `rounds` 渲染「共 N 轮」；
  - `frontend/author-workspace.js:302` 也有同一个开关。
- **影响**：作者和维护者会以为「要用多轮只能打 HTTP、界面没有入口」，从而重复开发或绕开已上线路径。
- **要求怎么修**：把第 8 条改成当前事实——「工作台续写有『按字数预算多轮续写』开关（默认不勾选），勾选后前端发送 3000 字/6 轮的预算；仍没有 `estimated_cost` 的花费面板，看钱要读 `/api/ai/quota` 与任务详情」。保留「默认不勾选」这一句，因为它是真实的默认行为。改完同步 §1 的触发方式一节。
- **备注**：该文件不在「零、被测字节」的 16 个锚点内，本结论只对当前 182 行版本成立；docs 后续再改需要重新核。

### P13 · low · 失败/取消时的部分报告只有 6 键，形状与成功报告不一致

- **文件与符号**：`backend/server.py` 失败/取消分支的条件报告字典（与 `:2150` 同一段）。
- **证据**：探针 A/B 实读 report 恰 6 键：`stop_reason`、`rounds`、`dispatched_calls`、`characters`、`target_characters`、`round_target_ratio`。成功报告是 16/18 键，含 `stop_reason`、`target_range`、`within_tolerance`、`budget_plan`、`round_metrics`、`usage_state`。
- **为什么只是 low**：顶层 `result.usage_state="unknown"`、`usage=null` 没有冒充 token 数；前端 `continuationVerdict()` 对缺 `within_tolerance` 有降级路径（用区间与 `target_reached` 判断），所以不会显示成「达标」。缺的是形状一致性，不是假数据。
- **要求怎么修**（可与 P12 一起改）：失败/取消报告补 `usage_state`（沿用顶层）、`within_tolerance=null`、`safe_to_apply=false`、`round_metrics`（已写轮次），使消费方不必按 `stop_reason` 分支判断键是否存在；或在 `docs/continuation-production.md` 明确写「部分报告只有这 6 键」，并在测试里锁死这个形状。

## 六、预期值与实测的差异（captain 已确认的两处）

任务描述里的两个「应…」与实测不一致，均已由 captain 确认是**期望值写旧了**，不是产品回退：

| 项 | 描述里的期望 | 本次实测 | 结论 |
| --- | --- | --- | --- |
| `budgeted_continuation_fixtures.py` | 43 cases | **47** cases | 43 是 t13 增加 4 个 ratio 用例之前的数字；47 与 `main()` 注册的 test 函数数一致 |
| `budgeted_continuation_http_fixtures.py` | 14 tests | **17** tests | 14 是 t14 增加 3 个 ratio 用例之前的数字 |

记这一节的目的不是找补，而是留证据：**验收单上的期望值也会过期，被测数字优先**。除此之外，其余期望（consistency `dirty 13.0/clean 100.0`、consistency HTTP 6、`--compare` PASS + v1.2 digest `sha256:9a6a72f0…`、浏览器 59、工坊 18、sweep 无 FAIL）全部命中。

## 七、已知边界（本报告没有覆盖的部分，明说）

1. **没有接真实模型**：基准用合成/对抗式 provider，探针用假 SSE provider。真实模型对长度提示的服从度、真实 token 账单，本报告一概未验证。
2. **一致性引擎的 30 万字 × 3 部验收未执行**（见第三节第 2 条），本报告只能确认文档如实披露了这一点，不能替它背书。
3. **浏览器回归只覆盖 Electron + Playwright 桌面通道**（59 + 18 用例），未覆盖移动端、Firefox/Safari 内核、真机缩放与无障碍读屏。
4. **`dispatched_calls` 的语义按文档定义判定**（实际发起）。若产品改口径为「成功轮数」，P12 应降级，但文档、测试、字段名必须同步改；当前三者互相矛盾，所以按 high 立案。
5. **未做并发压测**：同一账号多个多轮 run 同时争用 `app_quota_reservation` 的日额度，本报告未构造并发场景（t5 阶段覆盖过单 run 的额度边界，不等于并发安全）。
6. **审计范围限于 16 个锚点文件**；`backend/moyu_agent_chat.py` 等本轮之外的改动不在本报告结论内。
7. **P14 的依据是当前工作区文件**（`docs/continuation-production.md` 182 行），该文件不在字节锚点内；若 docs 在报告之后被改，P14 需要重新核。

## 八、结论的字节绑定（末句）

在 `backend/server.py` = `3414715dd2ee0b946a56febcb16403594bc0ef7ae86bb2e0280dab4db447e997`（7825 行）、`backend/moyu_continuation.py` = `40ab1c32802297066f24d6b90a423b511ba86c5581301e9ff58a6e79b24755ff`（857 行）、`benchmarks/budgeted/run.py` = `81fc77877d0e169b2a4fa98c68738c15b388f920b32bb4470f27c7de2ab72ff8`（3778 行）、`frontend/app.js` = `ad405926520b24ca1969ef5179d4eb12a95f506994698c3b773c9de012378a51`（1895 行）这一组字节上，本报告的判定是 **needs_revision**：命令全绿、基准可复现、工坊与页面回归全过，但 P12 让「真实调用次数」在失败/取消路径说谎，P14 让生产文档与前端事实相反。任一锚点字节变化，本报告失效，需按同一流程重跑。
