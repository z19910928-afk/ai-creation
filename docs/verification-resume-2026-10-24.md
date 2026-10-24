# 断点续写（t21）content-bound 独立验收 — 2026-10-24

**结论：pass。** 六条命令全绿、六个被测文件字节未动；t21 声称的十件事我用自造探针逐条证伪失败——种子确实来自 `app_ai_chunk`、有种子时确实丢了冻结请求、无种子路径逐字节不变、`resumed_from_run` 指向直接父 run、报告键集是 19/21 的扩展、失败/取消/续写三路径的 `request_audit` 都没有被中途轮污染。本轮未发现 P 级问题。

## 零、被测字节（验收前 → 验收后）

| 文件 | sha256 | 行数 |
| --- | --- | --- |
| `backend/server.py` | `2bd6e25330cbdc504cb3de68dcaad4bde8825c9db64b77df192fe1be6f3ecbe9` | 8034 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 |
| `tests/budgeted_continuation_http_fixtures.py` | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` | 937 |
| `tests/budgeted_continuation_fixtures.py` | `fda39caa50b3fe8b6e06ef4555e1a69bd048eaee768addc72089362475cc5429` | 1283 |
| `tests/continuation_progress_http_fixtures.py` | `815f03a4d03c77dd2bdf51877f4e8007e9e35d45424c54a588573edb03a57182` | 339 |
| `docs/continuation-production.md` | `08fabb00510c0ba954db5375b103e61ed0d2e5864f4fcad8267b79d2560a7ee9` | 254 |

跑完全部命令后重算：6/6 sha256 与开工记录逐字节相同（`bytes_unchanged=true`）；行数也与开工记录一致。`backend/server.py` 的字节与 captain 公布的冻结字节 `2bd6e253…` 相同——本轮所有证据都绑在这一份字节上。

## 一、命令表（我自己的退出码）

| 命令 | 退出码 | 关键输出 |
| --- | --- | --- |
| `.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py` | 0 | 无输出 |
| `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.477s` / `OK` |
| `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 56}` |
| `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 2.961s` / `OK` |
| `for f in tests/*_fixtures.py; do .venv/bin/python "$f" …; done` | 0 | 91 个 fixture 全部 exit 0，`sweep_fail.txt` 未生成，日志里无 `FAILED`/`Traceback` |
| `.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` | 0 | `verdict：PASS`；`repeat=2，digest=sha256:9a6a72f079fe2b9c49a7619dd09cbec6bd77863c6773b94b6827dfc84e6b98ac`；全场景 16/20（80.0%）；3000 子集 7/9（77.8%）；`q1_target_met=false` |

## 二、十条挑刺逐条

### 1. 种子只可能来自 `app_ai_chunk` —— 成立

探针 S1：造一个第 2 轮已派发、用量已上报、随后取消的 run A；把 A 的 `app_ai_chunk` 文本改写成哨兵「断点哨兵」×30（120 个非空白字符）后重试。结果：重试首轮 messages 里出现哨兵（`S1 首轮提示含改后的 chunk 文本`），A 的旧正文没有出现（`S1 旧正文没被当成种子重放`），A 的两次旧调用 payload 都不含哨兵（`S1 旧调用 payload 不含哨兵`）。

代码侧只有一条路：`resumable_continuation_seed()` 只 `SELECT text FROM app_ai_chunk … ORDER BY seq` 再 `join`，`ai_run` 表结构里只有 `output_hash`/`prompt_hash`/`token_json` 等审计列，没有正文列，因此「从 ai_run 或内存另造种子」在实现上不存在；哨兵实验又从行为上排除了这条假设。

### 2. 无种子路径逐字节不变 —— 成立

探针 S2：把 A 的 chunk 全部改成空白（`strip()` 后为空）→ `resumable_continuation_seed()` 返回 `None` → 重试退回冻结请求。实测：新调用 payload 与 A 的第 1 轮 payload 完全相等（`S2 无种子逐字节重放冻结请求`）；`prompt_hash`、`request_audit.request_sha256`、`request_audit.messages_sha256` 三者全部与旧 run 相等（`S2 无种子 prompt_hash/request_sha256/messages_sha256 不变`）；重试 consent 里 `resume_rebuilt_prompt`/`resumed_from_run`/`seed_characters` **三个键都不出现**（不是 `null`）；报告写 `seed_characters=0`、`resumed_from_run=null`。

### 3. 有种子时必须丢 `frozen_request` —— 成立

S1 是行为证据而不是读代码：重试首轮 messages 里出现旧正文哨兵，说明走的是 `prepare_frozen_ai_request` 之外的 else 分支；同时 `prompt_hash` 与原 run 不同、`request_audit.request_sha256` 也变了（`S1 有种子 request_sha256 必变`），证明这次是真的重建了提示词并重新冻结审计。S4 二层重试同样成立。

### 4. `resumed_from_run` 是直接父 run —— 成立

探针 S4：A（取消于第 2 轮，正文 200 字）→ B = retry A（再取消于第 2 轮，正文 400 字）→ C = retry B。C 的报告：`resumed_from_run == B`（不是 A），`seed_characters == 400`（B 的整段正文；不是 600/800，没有回溯累加），C 的正文 `startswith(B 的正文)`。这与文档 §8.2「整体当种子、不回溯祖先」一致。

### 5. 报告键集是扩展不是回退 —— 成立

探针 S5：成功 / 失败 / 取消 / 续写四条路径的报告键集**严格相等**，且都等于 19 键（17 个旧键 + `seed_characters` + `resumed_from_run`）。三条非续写路径都写 `seed_characters=0`、`resumed_from_run=null`，不是省略。S9 验证带 `round_target_ratio` 时键集为 21 键，且旧 17 键一个不少；S10 验证 ratio + 续写组合同样是 21 键且两个新键同时存在。

### 6. 「审计被中途轮覆盖」的连带 bug —— 独立复现通过

这是 captain 要求单独复现的一条。探针 S8：

- A 在第 2 轮被取消后，`consent.request_audit.payload.messages` 仍等于 A 第 1 轮真实发出的 messages，且不等于第 2 轮的 messages；
- B（带种子的重试）第 2 轮再被取消，B 的 `request_audit` 仍等于 B 自己的第 1 轮 messages（含种子），没有被第 2 轮覆盖；
- 紧接着对 B 再重试（C），`validate_frozen_request_audit()` 通过、C 成功——如果审计被 B 的第 2 轮污染，这一步会直接校验失败，所以这条链路就是该 bug 的复现路径；
- `boom`（第 2 轮 500）失败后同样保留第 1 轮审计，失败那轮的 messages 没有污染审计。

结论：修的是「`request_audit` 不随后续轮漂移」，我按失败与取消两条路径各自复现，均通过。

### 7. 种子已达下限 → 0 次 provider 调用 —— 成立

探针 S3：A 的 chunk 改成 620 字（target 600，区间 `[540, 660]`）后重试。实测：provider 零次新调用、`dispatched_calls=0`、`rounds=0`、`round_metrics=[]`、`stop_reason=target_reached`、`text` 就是种子本身、`within_tolerance=true`、`truncated=false`、chunk 单段等于种子。与文档 §8.3 第 5 步逐字一致。

### 8. 额度与轮数口径 —— 成立

- `dispatched_calls` 等于 provider 实收的新调用次数（S1、S6）；
- `rounds == len(round_metrics)`，metrics 的 `round_index` 从 1 连续（S1、S6）；
- `round_metrics[0].cumulative_characters == seed_characters + 200`，种子被算作第 0 轮但不进 metrics（S6）；
- `seed_characters` 单独一列，`characters == 正文非空白字符数`（S1、S4）；
- `output_hash == hash_text(正文)`，chunk 的 seq 从 1 连续且拼接逐字等于正文（S1、S3）。

### 9. docs §8 与实现一致 —— 成立

`docs/continuation-production.md` §8 用现在时，逐条核对：

- 「键集基线 19 键，带 ratio 21 键」——与 S5/S9/S10 一致；
- 「无种子时三个键不出现，不是写 null」——与 S2 一致；
- 「`resumed_from_run` 记直接父 run，嵌套整体当种子不回溯」——与 S4 一致；
- 「种子已达下限 → rounds=0、dispatched=0、text 就是种子」——与 S3 一致；
- 「二选一表达式 `frozen_request=None if resume_seed is not None else frozen_request`」——代码原文一致；
- 「前端没有断点续写按钮，入口是原来的重试」——`frontend/app.js` 里只有 `retryJob` / 「按原任务重试」，没有独立的断点续写入口。

### 10. 已知边界仍是边界 —— 成立

- `scoped_request()` 仍把任何 `ValueError` 映射成 400（宽泛捕获未改）；
- `deficit_characters`、`pace_hint`、`stall_retry_attempt` 在 `backend/server.py` 出现 0 次——引擎里的前两个键依旧到不了模型（P7 残余），`stall_retry_attempt` 仍被丢弃（P9 残余）；
- `request_dispatched=False` 的回退分支仍在（先记账、未派发则回退）；
- 未新增自动重发、未放宽 `billing_state=unknown` 的 409 守卫。

## 三、问题清单

**本轮无 P 级问题（无 blocker/high/medium/low）。**

观察 O1（不计 P，不阻断）：引擎在 `characters >= target_characters` 时停止，最后一轮仍可能把总量推到上限之外。S1 中 target 600、seed 120、每轮 200，最终 720 > upper 660，`within_tolerance=false` 但 `stop_reason=target_reached`。这是引擎既有的停止语义（不是 t21 引入，也不在本次改动范围），故只记录，不建议在本轮修。

## 四、已知边界（我没覆盖到的）

1. 超大种子未压测：只验证了 620 字种子，没有构造几十万字的 chunk 链。
2. 未真的重启进程：代码读库这一点由 S1（改库后重试读到新文本）间接证明，但没有做「进程重启后重试」的实证。
3. 未覆盖跨项目/跨分支的脏 chunk 数据：查询本身带 `project_id`/`branch_id` 过滤，但我没有插入错项目行来验证过滤真的生效。
4. 未覆盖并发重试同一 run：两个 retry 同时点下去时的竞争没有测。
5. `POST /api/ai/runs/<id>/retry` 未单独跑：它与 jobs 路径共用 `retry_ai_job()`，我验证的是 jobs 路径。
6. 失败/取消时的 `round_metrics` 只覆盖到「失败发生在第 2 轮」这一种形态，没有覆盖首轮即失败、以及第 3 轮以后失败。

## 五、字节绑定

本报告的每一条结论都对应 `backend/server.py` `2bd6e253…`、`backend/moyu_continuation.py` `583059fe…`、`tests/budgeted_continuation_http_fixtures.py` `67e0aa21…`、`tests/budgeted_continuation_fixtures.py` `fda39caa…`、`tests/continuation_progress_http_fixtures.py` `815f03a4…`、`docs/continuation-production.md` `08fabb00…` 这一组字节；任何一个文件改动后重跑，结论都要重新给出。
