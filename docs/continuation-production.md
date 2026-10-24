# 多轮字数预算续写的生产链路

给后来维护者看的一份接线说明：作者怎么发起、一次请求在服务端到底发生了什么、为什么几个地方必须那么写、钱和进度在哪里看、哪些边界现在还是硬的。

- 引擎：`backend/moyu_continuation.py` 的 `run_budgeted_continuation()`（模块 docstring 自述 v1.2）。
- 接线：`backend/server.py` 的 `normalize_budgeted_continuation()`、`continuation_round_material()`、`create_ai_job()`、`_run_budgeted_continuation_job()`、`_process_ai_job_claimed()`、`_budgeted_usage_summary()`、`finish_ai_job()`。
- 《与续写引擎的关系》见[一致性引擎说明](consistency-engine.md#与续写引擎的关系)：多轮产物是候选，生成过程不经过一致性检查。

## 1. 作者怎么触发

**入口只有 HTTP。** `POST /api/ai/queue`（以及同步入口 `POST /api/ai`，两者共用同一段处理）在请求体里接受一个可选对象字段 `budgeted_continuation`：

```json
{
  "task": "continue",
  "chapter_id": "ch_...",
  "instruction": "接着写，3000 字左右",
  "response_format": "text",
  "budgeted_continuation": {
    "target_characters": 3000,
    "max_rounds": 6,
    "min_round_gain": 50
  }
}
```

规矩（实现见 `normalize_budgeted_continuation()`）：

- **不传就是原来的单次生成。** `budgeted_continuation` 为 `None` 时函数返回 `None`，`_process_ai_job_claimed()` 走 `generate_ai()` 单次路径。`tests/budgeted_continuation_http_fixtures.py` 里的单次路径用例正是靠 `assertNotIn("budgeted_continuation", ...)` 钉住这条默认行为，不能改。
- **只有续写任务能带。** `task` 必须属于 `BUDGETED_CONTINUATION_TASKS`（当前是 `frozenset({"continue"})`），否则 400「多轮字数预算只支持续写任务。」。
- **只认五个键。** `BUDGETED_CONTINUATION_FIELDS` = `target_characters`、`max_rounds`、`min_round_gain`、`max_stall_retries`、`hard_max_rounds`；多一个键就 400「多轮字数预算包含未知字段：…」。
- **只接受整数。** 布尔值会被当成非法整数拒绝；字符串 `"3000"` 也不行（报「…必须是整数。」）。

字段、默认值与取值范围（默认值来自 `moyu_continuation.DEFAULT_*`，范围来自同模块的 `*_LIMITS`）：

| 字段 | 默认 | 取值 | 越界报错 |
| --- | --- | --- | --- |
| `target_characters` | `DEFAULT_TARGET_CHARACTERS` = 3000 | `TARGET_CHARACTERS_LIMITS` = 500–20000 | 「目标字数必须在 500 到 20000 之间。」 |
| `max_rounds` | `DEFAULT_MAX_ROUNDS` = 6 | `MAX_ROUNDS_LIMITS` = 1–12 | 「计划轮数必须在 1 到 12 之间。」 |
| `min_round_gain` | `DEFAULT_MIN_ROUND_GAIN` = 50 | 0 到 `DEFAULT_TARGET_CHARACTERS`（3000） | 「单轮最少新增字数必须在 0 到 3000 之间。」 |
| `max_stall_retries` | `DEFAULT_MAX_STALL_RETRIES`（并行开发中会变，只认符号定义） | `MAX_STALL_RETRIES_LIMITS` = 0–12 | 「停滞重试次数必须在 0 到 12 之间。」 |
| `hard_max_rounds` | `DEFAULT_HARD_MAX_ROUNDS` = 12 | `HARD_MAX_ROUNDS_LIMITS` = 1–24，且不得小于 `max_rounds` | 「硬上限轮数不能小于计划轮数。」 |

注意 `min_round_gain` 的上界是常量 `DEFAULT_TARGET_CHARACTERS`，不是本次的 `target_characters`——这是实现里的既有口径，不是你传 500 字就能把上界压到 500。

**冻结。** 预算随后写进 `context["_budgeted_continuation"]`，同时写入 `ai_run.consent_json` 的 `budgeted_continuation` 与建单返回的 `budgeted_continuation` 字段。重试（`retry_ai_job` → `create_ai_job(..., budgeted_continuation=consent.get("budgeted_continuation"))`）沿用原预算，作者不能在重试时偷偷改轮数；已有落盘正文时还会从断点续写（见第 8 节）。

**现状提醒：界面已有入口，但默认关闭。** 任务下拉选中「续写当前场景」时，`syncBudgetContinuationRow()` 把 `#aiBudgetRow` 显示出来；作者勾选 `#aiBudgetContinuation` 后，`frontend/app.js` 在入队前把 `budgeted_continuation` 按 `BUDGETED_CONTINUATION_DEFAULTS` 放进请求体，不勾选则请求体里根本没有这个字段。也就是说：「多轮会多花额度」这件事由作者显式勾选承担，不是默认行为。

结果回来后，`continuationSummary()` 渲染「共 N 轮 · 字数 · 目标（容差判定）」，运行中的进度看 `continuationProgress()`（数据来自 `ai_run.round` 事件）。

**两个视图共用同一个复选框 id**：`#aiBudgetContinuation` 在章节编辑视图与作者工作台视图里都渲染。显隐由 `frontend/app.js` 的 `syncBudgetContinuationRow()` 维持（工作台视图另有 `authorSyncBudgetContinuationRow()` 兜底，两处条件必须同步：只有任务为 `continue` 时显示）。**字段由谁决定**：`frontend/app.js` 的 `bind()` 把 `#aiGenerate` 绑到 `generateAI()`，`generateAI()` 在发包前读 `#aiBudgetContinuation`，只有「任务为 `continue` 且已勾选」才把 `budgeted_continuation` 放进请求体。两个视图共用这一个按钮与这一个函数——`frontend/author-workspace.js` 的 `authorEditorView()` 通过 `authorWorkbenchRail()` 渲染控件，且 `views.editor` 由它覆盖，所以勾选行为在两边一致。另有一条 `authorQueueTask()` 路径只服务 `chapter_analysis` 与 `semantic_review`，本来就不该带字数预算——读代码时别把它当成续写入口。

**维护注意**：前端这份默认值是独立常量 `BUDGETED_CONTINUATION_DEFAULTS`，与 `moyu_continuation.DEFAULT_*` 目前一致但不会自动同步——改引擎默认值时要顺手改前端，否则界面发出去的预算和文档不符。

## 2. 一次请求内部发生什么

按时间顺序，全部落在 `backend/server.py`：

1. **接收与前置校验**（`POST /api/ai/queue` 路由）。`task` / `instruction` / `response_format` 走 `bounded_text` 与 `ai_contract`；`normalize_budgeted_continuation()` 规范化预算，失败直接 400。随后 `ai_quota_snapshot()` 查今日额度：`enforced` 且 `remaining_runs <= 0` 且配了 `base_url` 时返回 429。上下文由 `ai_context()` 现拼。
2. **建单**（`create_ai_job()`）。冻结 `_output_contract`（`prose_output_contract()`，字数口径与候选校验共用）、写入 `_budgeted_continuation`；在一个 `BEGIN IMMEDIATE` 事务里写 `ai_run`（`status=queued`）、调 `reserve_ai_budget()` 预留 **1** 个名额、写 `app_ai_job`（`queued`），并把 `budgeted_continuation` 存进 `consent_json`。
3. **派发**（`_process_ai_job_claimed()`）。`BEGIN IMMEDIATE` 抢占 `queued → running`；跑 `validate_*_source()` 一串来源校验；用 `prepare_frozen_ai_request()` 与 `validate_frozen_request_audit()` 重建并核对冻结请求（`request_sha256` / `messages_sha256` / `prompt_hash` 必须与建单时一致）；`reserve_ai_budget()`（幂等，已存在直接返回）；把 `app_quota_reservation.status` 置 `dispatched`；把 `consent["request_audit"]["payload"]` 塞进 `context["_frozen_request"]`。
4. **分叉**。`budget = normalize_budgeted_continuation(consent.get("budgeted_continuation"))`；预算非空且 `task in BUDGETED_CONTINUATION_TASKS` 时进 `_run_budgeted_continuation_job()`，否则走原来的 `generate_ai(..., stream=True, on_chunk=on_chunk)`。
5. **多轮循环**（`_run_budgeted_continuation_job()` 把三个回调交给引擎）。
   - `generate(prompt_context, round_index)`：复制 `context`；`round_index > 1` 时删掉 `_frozen_request`；`continuation_round_material()` 合入进度键；调 `generate_ai(..., request_id=run_id if round_index == 1 else f"{run_id}-r{round_index}")`；累计 `moyu_output.count_characters()`；每轮结束 `append_event(..., BUDGETED_CONTINUATION_EVENT, {...})` 记一条 `ai_run.round` 进度。
   - `budget_check(round_index)`：第 1 轮恒真（已被本次预留覆盖）；第 r 轮要求 `ai_quota_snapshot(exclude_run_id=run_id)` 的 `remaining_runs >= r`。
   - `should_cancel()`：读 `app_ai_job.cancel_requested`，并检查 `cancel_event`。
6. **汇总**（`_budgeted_usage_summary()` + 组装 `result`）。把各轮 `usage` 汇总成一份 token 记账；`result` 带 `text`（各轮用空行拼接）、`usage` / `usage_state` / `billing_state`、`budgeted_continuation`（含 `stop_reason`、`rounds`、`dispatched_calls`、`round_metrics`）、`truncated`、`completion_state`、`safe_to_apply`、`output_validation`。`request_audit` 只保留第 1 轮那份（它就是被冻结的那次请求）。
7. **一次性落库**（`finish_ai_job()`）。写 `app_ai_job.result_json`；写 `ai_run` 的 `output_hash` / `token_json` / `cost_json` / `status` / `latency_ms`；把 `completion_state` / `safe_to_apply` / `billing_state` / `budgeted_continuation_report` 写进 `consent_json`；按 `billing_state` 结算预留名额（`not_dispatched → released`、`usage_reported → consumed`、其余 `unknown`）；**只有这里有 output 才调一次** `persist_ai_chunks()`；最后追加 `ai_run.<status>` 事件。

## 3. 为什么第 2 轮起必须清掉 `_frozen_request`

`call_ai_provider()` 一看到 `context["_frozen_request"]` 非空，就走 `prepare_frozen_ai_request()` 把原始 payload 重建成 messages——**逐字重放**，只允许换 `request_id`。而且它会拿重建结果和 `payload` 做全等比较（`prepared["payload"] != payload` 就抛「原任务请求已不符合当前规则」），意思是：你想在冻结请求上改一句提示，改不动。

所以第 2 轮如果还带着 `_frozen_request`，N 轮发的就是同一组 messages：模型被问同一个问题 N 次，产出高度重复的文本，字数可能凑够了，内容却是复读。`_run_budgeted_continuation_job()` 的 `generate()` 因此在 `round_index > 1` 时 `round_context.pop("_frozen_request", None)`。

第 1 轮仍然用冻结请求，有实际好处：单次路径与多轮路径第 1 轮的请求逐字相同，`prompt_hash` 可审计、可复现。

清掉之后靠什么让第 2 轮「接着写」？`continuation_round_material()` 把进度做成**普通键**（不是下划线键）：

- `continuation_round`：`round_index`、`target_characters`、`written_characters`、`remaining_characters`；
- `remaining_characters`（顶层）：第 2 轮起用实时余量覆盖 `create_ai_job()` 冻结的值，与上一条嵌套值同步；
- `previous_text`：已写正文（引擎默认上下文里累积的那份）；
- `continuation_requirement`：一句人话要求——「本次是同一篇续写的第 N 轮：已写 X 字，目标 Y 字，还差约 Z 字。请紧接上文继续写，不要重复已经写过的内容，不要重新开头，不要复述上文。」
- 可选 `closing_hint`（剩余不足 `CLOSING_HINT_THRESHOLD`=600 字时的收束提示）与 `stall_retry_hint`（停滞重试时的换策略提示）。

为什么强调「普通键」：`ai_prompt_preview()` 只把**非下划线**键写进材料 JSON。用 `_previous_text` 之类的下划线键，提示词证据里就看不到，事后无法证明第 2 轮到底看到了什么。

### 哪些引擎提示会到达模型

引擎在 `prompt_context` 里放了什么，不等于模型看到了什么：`call_ai_provider()` 拼提示时只序列化 `ai_prompt_preview()` 认的非下划线键，而多轮路径每轮用的是 `round_context = dict(context)` 再叠加 `continuation_round_material()` 的返回值。**没被 `continuation_round_material()` 搬过去的键，就只在引擎内部有效。**

| 键 | 谁产生 | 什么时候到达模型 |
| --- | --- | --- |
| `previous_text`、`continuation_requirement` | 引擎（第 2 轮起） | 第 2 轮起透传；`previous_text` 为空时两个键都不写（`continuation_requirement` 随之缺席） |
| `continuation_round`（`round_index` / `target_characters` / `written_characters` / `remaining_characters`） | `continuation_round_material()` 自己算 | 第 2 轮起（第 1 轮走冻结请求，提示里没有这组键） |
| `remaining_characters`（顶层） | 第 1 轮由 `create_ai_job()` 冻结成 `target_characters`；第 2 轮起引擎给实时余量 | 每轮都在；第 2 轮起与 `continuation_round.remaining_characters` 逐轮相等 |
| `max_characters`、`overshoot_hint` | 第 1 轮由 `create_ai_job()` 调 `moyu_continuation.round_budget(0, target_characters)` 冻进开场提示；第 2 轮起引擎给实时值 | 第 1 轮 = 开场注入值；第 2 轮起用实时值**覆盖**同名字段（不覆盖就会每轮复读开场额度） |
| `closing_hint` | 引擎（剩余不足 `CLOSING_HINT_THRESHOLD`=600 字） | 满足条件时 |
| `stall_retry_hint` | 引擎（停滞重试轮） | 停滞重试轮 |
| `min_characters`、`stall_retry_min_characters` | 引擎（`stall_strategy` 打开时按配额与进度算出） | 仅停滞场景：`min_characters` 在停滞恢复期每轮出现，`stall_retry_min_characters` 只在停滞重试轮出现（未停滞的第 2 轮没有）；透传时额外合成一句 `minimum_hint`（「本轮至少写 N 字：低于这个数仍算停滞…」）——整数模型未必读得懂，人话才管用 |

**三点容易误判的地方**（都已实测）：

- 引擎在停滞恢复期还会发 `deficit_characters`、`pace_hint`（以及重试轮的 `stall_retry_attempt`），但 `continuation_round_material()` 至今没有透传它们——模型既看不到 deficit 的数值，也看不到剩余轮数，只以 `minimum_hint` 里那句「本轮至少写 N 字」的派生配额形式出现。想让它们生效，得先在接线层加键。
- **顶层 `remaining_characters` 与嵌套值同源，第 2 轮起逐轮相等。** `create_ai_job()` 冻结 job context 时它等于 `target_characters`（第 1 轮还没写一个字，此时它就是「本次目标」）；第 2 轮起 `continuation_round_material()` 无条件用引擎给出的实时余量覆盖它（`material["remaining_characters"] = remaining`），`_run_budgeted_continuation_job()` 再把返回值 `round_context.update(...)` 合进提示词。实测 target=1500、5 轮真实 HTTP：顶层 1500 / 1300 / 1100 / 900 / 700，嵌套第 2 轮起同为 1300 / 1100 / 900 / 700——**第 2 轮起两者逐轮相等且严格递减**，取哪个都不矛盾；只有第 1 轮的顶层值语义不同（是目标而非余量）。

两条可以背下来的结论：

- **第 1 轮的额度只有一个来源**——`create_ai_job()` 在冻结请求之前用 `round_budget(0, target_characters)` 把 `remaining_characters` / `max_characters` / `overshoot_hint` 写进 job context。注意它的 `characters_done` 传的是常量 `0`：额度按「本次续写从零起算」，**不抵扣章节里已经写好的正文**。引擎自己的首轮 `prompt_context` 按契约是空字典（见 `run_budgeted_continuation()` 的 docstring），所以首轮约束只能由接线层提供；第 2 轮起这几项由 `continuation_round_material()` 用实时值覆盖（见本节上两条）。
- **第 2 轮起的额度由 `continuation_round_material()` 逐轮透传**，而且必须用引擎的实时值覆盖从 job context 继承来的开场值；否则每一轮都在对模型复读「本轮最多再写 <开场额度> 字」。


## 4. chunk 记账为什么不能跨轮复用同一个 seq

`persist_ai_chunks()` 的约束是硬编码的：按 `ai_run_id` 读出已有的 `app_ai_chunk`，要求

- `seq` 必须从 1 开始连续（断号抛 `ValueError("stored AI chunk sequence contains a gap")`）；
- 同一个 `seq` 的文本必须一致（不同文本抛 `ValueError("AI chunk sequence already contains different text")`）；
- 新分片的 `seq` 必须等于「已存条数 + 1」（否则 `ValueError("AI chunk sequence must be consecutive")`）。

而 provider 侧的 `append()` 是**每次请求内部从 1 编号**。多轮共用同一个 `ai_run_id`，第 2 轮的第一片又是 `seq=1`，文本却不是第 1 轮那段——必然命中「different text」。

所以多轮路径根本不给 `generate_ai()` 传 `on_chunk`：各轮只在内存里拼接，合并后的完整正文由 `finish_ai_job()` 调一次 `persist_ai_chunks(conn, ai_run_id, output)` 落库。代价与补偿：

- 代价：流式分片只覆盖最终合并文本，看不到「第 1 轮写到第几片」这种中间回放；
- 补偿：每轮一条 `ai_run.round` 事件（`BUDGETED_CONTINUATION_EVENT`）带 `round_index` / `characters` / `target_characters`，前端进度靠它，不靠 chunk。
- 事件写库失败（`sqlite3.Error`）被显式吞掉：进度是展示用的，不能因为写事件失败就把已经花掉的生成作废。

## 5. 额度怎么算：预留 1 次，真实花 N 次

`reserve_ai_budget()` 每个 `ai_run` 只写 **1** 行 `app_quota_reservation`（`ai_run_id` 是主键，`status` 从 `reserved` 走到 `dispatched`）。多轮无论跑几轮，这一行只有一条。

真实花费靠三处暴露，缺一不可：

1. **每轮的明细**：`result.budgeted_continuation` 里的 `rounds`（引擎记账的轮数）、`dispatched_calls`（实际发起的模型调用数）、`round_metrics[]`（每轮 `characters`、`cumulative_characters`、`retried`、`completion_state`、`finish_reason`）。`rounds` 与 `dispatched_calls` 可能不等：停滞重试会让调用数多于有效轮数。
2. **token 汇总**：`_budgeted_usage_summary()` 只有「所有轮都 `usage_state == "reported"`」才合并成 `reported`；部分轮报告降级 `partial` 并给 `reported_tokens_lower_bound`；一个都没有就是 `unknown`；全部离线则 `not_applicable` + `billing_state="not_dispatched"`。**绝不把部分用量当完整用量上报。**
3. **落库与查询**：`finish_ai_job()` 把汇总写进 `ai_run.token_json` / `cost_json`，把 `budgeted_continuation_report` 写进 `consent_json`。作者侧能读到的接口是：
   - `GET /api/ai/quota` → `ai_quota_snapshot()`：`used_runs`、`reserved_runs`、`remaining_runs`、`used_tokens`、`reported_tokens_lower_bound`、`estimated_tokens`、`estimated_cost`、`usage_state`、`cost_state`（固定 `"estimated_not_provider_bill"`，即估算、不是供应商账单）；
   - `GET /api/ai/runs/<run_id>` → 解析后的 `token_json` / `cost_json` / `consent_json`（含 `budgeted_continuation_report`）；
   - `GET /api/ai/runs/<run_id>/stream` → 按 `after_seq` 回放的 `ai_run.round` 进度事件。

两个必须说清的记账事实：

- **额度按 run 计数，不按调用次数计数。** 一次多轮任务消耗 `used_runs` 的 1 个名额，却真实发起了 N 次模型调用。`remaining_runs` 不等于「还能发起多少次模型调用」——这是 `app_quota_reservation` 单行主键的必然结果。
- **第 2 轮起能不能跑，由 `budget_check()` 现查。** 第 1 轮已被本次预留覆盖，直接放行；第 r 轮要求今日 `remaining_runs >= r`，否则引擎按 `budget_exhausted` 停下，而不是偷偷超发。

## 6. 停止原因 5 种，各自含义

引擎的 `STOP_REASONS` 是五元组，值原样透出到 `result.budgeted_continuation.stop_reason`：

| `stop_reason` | 触发条件 | 作者看到什么 |
| --- | --- | --- |
| `target_reached` | 累计非空白字符数达到目标下限（`target_range()` 的 `minimum`，即 `ceil(0.9×目标)`） | 达标；但 `within_tolerance` 可能是假——超冲时不裁剪、如实标记 |
| `no_progress` | 某轮增量低于 `min_round_gain`，且 `max_stall_retries` 次换策略重试后仍没写够 | 候选偏短；`truncated=true` |
| `max_rounds` | 用满 `max_rounds`（含弹性扩展到 `hard_max_rounds`）仍未达标 | 候选偏短；`truncated=true`，`extended_rounds` 说明多跑了几轮 |
| `budget_exhausted` | `budget_check()` 返回假值，即今日剩余名额不敷下一轮 | 候选偏短；`truncated=true`，提示是额度而不是模型不行 |
| `canceled` | `should_cancel()` 返回真值（作者取消 / 任务被取消） | `completion_state="canceled"`、`safe_to_apply=false`；已写部分仍拼成候选供查看 |

停止原因不是「成功/失败」的开关，而是「停在哪」的解释：

- `result.truncated` 判据是 `stop_reason in {max_rounds, no_progress, budget_exhausted}`；`max_tokens` 把某一轮截断是正常形态，不算 `truncated`。
- `completion_state` 只有三档：`canceled` / `complete`（有正文）/ `empty`（一个字都没有）。
- `safe_to_apply` 最初按「有正文且未取消」给值，之后还会被两处改写：`finish_ai_job()` 在取消或出错时置 `False`；`_process_ai_job_claimed()` 末段捕获 `finish_ai_job()` 抛出的 `ValueError` 时，以 `completion_state="invalid_evidence"` 再落一次库（证据不完整也不许采纳）。
- **能看 ≠ 能采纳。** `apply_candidate()` 的门槛比 `safe_to_apply` 更严：要求 `ai_run.status == "success"`、`app_ai_job.status == "success"` 且未请求取消、`safe_to_apply` 为真、**`truncated` 为假**、`completion_state ∈ {complete, offline_complete}`，否则抛「候选尚未完整完成或已取消，不能采纳」。所以 `no_progress` / `max_rounds` / `budget_exhausted` 停下的候选虽然 `safe_to_apply=true`，也只会停在「可读片段」这一步——想拿到正文就得重跑或手动改。
- 某轮中途抛 `ProviderError`（含取消）时，已经产出的轮次不会白丢：`_run_budgeted_continuation_job()` 把它们（加上 `partial_result`）拼成部分候选，并把 `stop_reason` 记成 `canceled` / `failed` 后原样上抛错误。
- 进度侧每轮一条 `ai_run.round` 事件；结束时 `finish_ai_job()` 再追加 `ai_run.<status>`。

## 7. 已知边界

1. **离线 provider 一定产出重复文本。** 没有可用模型连接（`config["base_url"]` 或 `config["model"]` 为空）时 `generate_ai()` 走 `offline_ai()`，而后者只看任务、章节标题、记忆条数与 `instruction`，**不看轮次上下文**——所以每轮返回同一段文字。引擎只按字数累加，于是最终要么撞 `hard_max_rounds`、要么因为单轮增量低于 `min_round_gain` 判 `no_progress`，两种都不是「写出来了」。接线侧识别到「所有轮 `mode == "offline"`」时会把 `usage_state` 置 `not_applicable`、`billing_state` 置 `not_dispatched`，不假装花过钱。离线跑通不等于线上质量。
2. **超冲不可裁剪，`overshoot_guard` 保证的是「说到」不是「做到」。** 它承诺的上限只到「把本轮额度 `max_characters` 与 `overshoot_hint` 写进 `prompt_context`」这一步——第 1 轮由 `create_ai_job()` 冻进开场提示，第 2 轮起由 `continuation_round_material()` 逐轮透传（见第 3 节的透传表）；至于模型是否服从，接线层管不了，`backend/moyu_continuation.py` 也**不做正文裁剪**。模型不服从时引擎如实记 `within_tolerance=false` 与 `overshoot_guard.characters_over_upper`，一个字都不删。基准里的 sc-15（目标 4000，实测 4661）就是这条边界（来源：`docs/consistency-budget-v1.2-2026-10-23.md` §5 的 sc-15 行：目标 4000、实际 4661、`characters_over_upper=261`）。
3. **`target_characters` 只算本次生成。** 引擎不读、不改章节已有正文；要按整章 3000 字算，调用方得自己先扣掉已写字数再传。
4. **去重靠提示词，没有语义查重。** 第 2 轮起靠 `previous_text` + `continuation_requirement` 让模型别复述；模型复述了，引擎只按字数记账，不会有「重复内容」判定。
5. **冻结只在第 1 轮。** 第 2 轮起提示词是 `continuation_round_material()` 现拼的，`request_audit` 也只保留第 1 轮那份。所以「本次请求的提示词证据」等于「第 1 轮的提示词证据 + 逐轮进度事件」，不是每轮一份审计。
6. **一个 reservation 记 N 次调用。** `app_quota_reservation` 不按轮追加，日额度按 run 计数（见第 5 节）。审计要靠 `consent_json.budgeted_continuation_report` 里的 `dispatched_calls`。
7. **进度事件写失败会被吞。** 写事件出错不影响生成结果，但那次进度就丢了，回放里会少一轮。
8. **多轮要作者自己勾，花多少钱界面上不提示。** `#aiBudgetContinuation` 默认不勾选，勾了才会发 `budgeted_continuation`；工作台目前不渲染 `estimated_cost`（`frontend/` 里没有任何 `estimated_cost` 引用），作者只看到「按字数预算多轮续写（约 3,000 字）」这一句，看不到预估花费——要看钱得读 `GET /api/ai/quota` 与任务详情，那里的 `usage_state` / `cost_state` 标明 `estimated_not_provider_bill`（是估算、不是 provider 账单）。
9. **偏短的候选不能直接采纳。** `no_progress` / `max_rounds` / `budget_exhausted` 停下时 `safe_to_apply=true`，但 `apply_candidate()` 会因为 `truncated` 为真而拒绝（见第 6 节）。作者要么重跑、要么把片段当参考手动改；这是当前实现的行为，不是文档可以糊过去的细节。

## 8. 断点续写：重试从已落盘正文继续

续写失败或取消后点「重试」，不再从第 1 轮重写：上一次已经落盘的正文被当作第 0 轮种子，本次只补后面的缺口。这一节讲种子从哪来、提示词为什么必须重建、报告里哪两个数要分开看，以及现在仍然存在的边界。

### 8.1 为什么不做自动重发

续写是长请求，网关超时与连接中断是重复出现的失败类型；而**续写一旦发出去就可能已经计费**，服务商侧无法确认「这次失败到底扣没扣钱」。因此本项目的设计目标是「宁可少发一次，也不重复计费」——provider 层把这条纪律直接写进错误文案（`backend/moyu_provider.py` 的 `ProviderError`）：

- `模型回复超过等待时间；不会自动重复发送。`
- `模型连接中断；不会自动重复可能已计费的请求。`

所以续写只能由作者显式触发重试（`POST /api/ai/jobs/<id>/retry` 与 `POST /api/ai/runs/<id>/retry`），失败与取消都不会自动续。

### 8.2 种子从哪来

`resumable_continuation_seed(conn, run_id)` 只读 `app_ai_chunk`：按 `seq` 把该 `ai_run` 的分片原样拼起来。不读 `ai_run` 的冗余字段（那张表只有 `output_hash`，没有正文），也不重算——`finish_ai_job()` 里的 `persist_ai_chunks()` 是正文唯一落盘的地方，按 `seq` 拼回来就是当初的正文本身。逐段原样相加、不裁剪，否则报告里的 `seed_characters` 和作者看到的正文就对不上账。

三条边界：

- 拼出来去掉首尾空白后为空（典型情况：上一次 `request_dispatched = False`，请求根本没发出去）→ 返回 `None`，重试**退回原来的行为**：重放冻结请求、从第 1 轮重跑，逐字节不变。
- `resumed_from_run` 记的是**直接父 run**（被点的那个）。嵌套重试拿到的种子本身就含更早的正文，所以整体当种子，不回溯到最初祖先——回溯会让字数重复累加。
- 必须读库、不读内存：重试可能发生在服务重启之后，也可能由另一个进程接手，内存里的轮次早就没了。

### 8.3 一次续写重试内部发生什么

1. `retry_ai_job()` 先用旧 audit payload 跑 `validate_frozen_request_audit()`，证明原请求完整、模型设置没变；然后取种子；**只有真拿到种子**才把 `frozen_request` 置空传给 `create_ai_job()`（理由见 8.4）。
2. `create_ai_job()` 把种子冻结进 job context：下划线键 `_seed_text` / `_seed_characters` / `_resumed_from_run`（不进提示词材料），普通键 `previous_text = seed_text` 与 `continuation_requirement`（一句「上次已写 N 字、还差约 R 字、不要重复」）。
3. `_run_budgeted_continuation_job()` 把种子传给 `run_budgeted_continuation(..., seed_text=...)`。引擎把种子当第 0 轮：`characters` 从 `moyu_output.count_characters(seed_text)` 起算，`remaining_characters` 相应扣减，第 1 轮 `prompt_context` 直接带 `previous_text`。
4. 正文是「种子 + 各轮新生成」按空行原样拼接，不裁剪、不润色；中途失败时种子也排在最前面，否则正文等于被砍掉开头。
5. 种子已达 `target_range()` 下限（`ceil(0.9 × target_characters)`）时，引擎不再调度任何轮次：`rounds = 0`、`stop_reason = target_reached`、`dispatched_calls = 0`、`text` 就是种子本身。

### 8.4 有种子时必须丢 `frozen_request` 重建提示词（这不是放宽校验）

**机制上的硬冲突**：`create_ai_job()` 在 `frozen_request is not None` 时用 `prepare_frozen_ai_request()` 把旧 audit payload 原样重建成 messages，context 里的任何键都不会进提示词（`ai_prompt_preview()` 只在 `else` 分支）。所以「有种子 + 沿用 frozen_request」是自相矛盾的组合：种子进不了第 1 轮，`seed_text` 静默失效，续写等于没做。

**实现上的例外**：`retry_ai_job()` 里写成 `frozen_request=None if resume_seed is not None else frozen_request`——有种子就丢冻结请求，改用冻结的 context + 种子重新拼提示词，`continuation_round_material()` 写出的普通键才有机会出现在 messages 里。

**为什么它不是放宽校验**：

- 原请求的完整性照样校验——`validate_frozen_request_audit()` 照跑，比对 `api_protocol`、`endpoint`、`user_agent`、`request_sha256`、`messages_sha256` 与 `prompt_hash`；
- 只有「有种子」的续写走重建；「无种子」的失败重试保持原样重放，一次也不改；
- 新 run 的 `prompt_hash` 与旧 run 不同是**预期结果，不是篡改**：提示词本来就该不同，因为多了「已经写到哪」。consent 里那三个字段就是给这个差异留的解释（见 8.5）。

**谁能看到这件事**：只有审计层。字段落在 `ai_run.consent_json` 里，`ai_job_public()` 会把 `request_audit.payload` 摘掉，作者界面不展示内部字段。

### 8.5 审计字段与报告键集

- **consent（只有续写重试才写）**：`resume_rebuilt_prompt = true`、`resumed_from_run`、`seed_characters`。无种子时这**三个键不出现**——不是写 `null`，是不写，避免被读成「续写了但种子为 0」。
- **报告（`result.budgeted_continuation`）**：`seed_characters` 与 `resumed_from_run` 在成功、失败、取消三条路径都在，非续写时是 `0` / `null`（`_budgeted_continuation_report()` 统一形状）。键集基线 **19 键**，带 `round_target_ratio` 时 **21 键**（额外 `round_target_ratio` 与 `budget_plan`）。这条 supersede 早前记录的 17/19 键——新增两个键是扩展，不是回退；引用旧数字的文档与验收报告按这里更新。
- **作者看不到字段级差异**：作者看到的是「接着写」，不是「提示词重建了」。也没有「断点续写」这个按钮（见 8.6）。

### 8.6 边界

- **前端没有「断点续写」按钮**：入口就是原来的「重试」，语义在下面变了。作者不会看到「从断点继续」这个动作名，所以「重试为什么变便宜了」要靠发布说明解释，不能指望界面自解释。
- **多轮仍然多花额度**：这是产品决定，不是 bug。种子只保证不重写已经写过的部分，不减少新轮次的调用。
- **种子不计一次调用**：种子没有任何 provider 调用；`dispatched_calls` 只数本次真实发出的调用，`round_metrics` 只统计本次新发生的轮次，种子不进。
- **重试守卫照旧**：旧 `ai_run` 状态 `unknown` 或 consent `billing_state == "unknown"` 时直接拒绝（「上次请求是否完成或计费尚不明确…」），t21 一个字没改这道守卫。
- **种子不裁剪**：与「超冲不可裁剪」同一条纪律；宁可如实报 `within_tolerance = false`，也不删字。

### 8.7 红线

1. **不许自动重发**——失败、取消、断连都只报错，重试必须由作者点击。
2. **`billing_state = "unknown"` 或 run 状态 `unknown` 时继续拒绝重试**，既有守卫只许保持，不许为了让「续写更顺手」而放宽。
3. **种子不能从内存取**，必须来自 `app_ai_chunk`——否则重启后断点续写会静默丢字。
4. **种子不得伪装成新生成内容**：报告里 `seed_characters` 与 `dispatched_calls` 必须分开，`round_metrics` 不含种子。
5. **两条路径必须显式二选一**：`frozen_request=None if resume_seed is not None else frozen_request` 这行表达式就是分界线，不许被「简化」成单一路径——一旦合并，`seed_text` 会静默变空且不报错。
6. **种子不裁剪**：与「超冲不可裁剪」同一条纪律。

## 维护约定

- 改请求字段、默认值或范围时，同时改 `BUDGETED_CONTINUATION_FIELDS`、`normalize_budgeted_continuation()` 与本文件第 1 节的表；默认值的事实来源是 `moyu_continuation.DEFAULT_*`，不要在本文件里另抄一份。
- 改引擎的停止原因或返回字段时，同步改 `docs/consistency-budget-*.md` 的基准报告与第 6 节；`stop_reason` 是基准「一次通过」的判据（`target_reached` 且 `within_tolerance`），字符串不能改。
- 本文件只描述接口与约束，不复述引擎内部算法；算法看 `backend/moyu_continuation.py` 的模块 docstring。
- 引擎新增任何提示键（`prompt_context` 里的字段）时，必须同时在 `continuation_round_material()` 里显式透传，否则该键只在引擎内部有效、到不了模型——文档与基准都会「看起来通过」。同理，删键时也要在这里删，别让旧键在提示里继续存在。
