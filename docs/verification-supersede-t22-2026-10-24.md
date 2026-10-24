# 断点续写 content-bound 复验（替代 t22）

- 任务：t33 / attempt `b095a61d-be86-4572-b08e-a2e7a31efe48`
- 验收员：verifier2（独立验收，未参与 t20/t21 实现）
- 时间：2026-10-24（命令 start 15:34:04 / end 15:46:21，探针 15:47–15:52，Asia/Shanghai）
- 工作目录：`/Users/zhaozhen/Downloads/神经网络写作`
- 结论：**pass**（无 high / blocker；1 条 low 记录为「HTTP 端到端场景不可复现，已用接线层等价证明」，不影响交付）

> 为什么要有这份报告：t22 的 pass 绑定的 `backend/server.py` 是 `2bd6e253…`，当前是 `a70a25ce…`；t18 绑定的是 `5024a6a5…`。旧结论不再指向当前字节，本报告把 t22 的四条断点续写声明重新绑定到当前 sha256 上（其余三块——谓词、槽位语义、文档——由 t27 报告绑定，本报告不重复）。质量闸门（quality gate）的核心是「结论指向哪一份字节」，所以先有字节表，再有结论。

## 零、被测字节

| 文件 | sha256（验收开始） | 行数 |
| --- | --- | --- |
| `backend/server.py` | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` | 8111 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 |
| `tests/budgeted_continuation_fixtures.py` | `fda39caa50b3fe8b6e06ef4555e1a69bd048eaee768addc72089362475cc5429` | 1283 |
| `tests/budgeted_continuation_http_fixtures.py` | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` | 937 |

验收结束时（15:46:21）重算，四个文件 **sha256 与行数逐字一致**（命令见第二节 1–4 条，输出 a70a25ce… / 583059fe… / 67e0aa21… / fda39caa…，`wc -l` 8111 / 896 / 937 / 1283）。

- 工作树本轮只新增本文件（`git status --porcelain` 里 backend/tests 的 M 均为本轮之前的既有改动，探针全部落 `/tmp`）。
- 被测字节在验收期间未被改写：4 个文件 start/end 哈希相同，且本轮没有对 backend/tests 执行任何写操作。

## 一、结论摘要

| # | t22 原声明 | 当前字节上的证据 | 判定 |
| --- | --- | --- | --- |
| 1 | 重试的种子来自 `app_ai_chunk` 已落盘正文，且第 1 轮提示词里带上一轮正文 | A5（改库后重试，提示词里出现只存在于库里的标记）+ A2 + A6 | 成立 |
| 2 | 无种子时退回「重放冻结请求」，逐字节不变 | A4（`payload equal` + `prompt_hash` 不变 + consent 无续写键） | 成立 |
| 3 | 种子已达 `ceil(0.9×target)` 时零次 provider 调用、`TARGET_REACHED` | A3 引擎层 + A3 接线层（`create_ai_job` + `process_ai_job`，与 retry 同一函数），`provider_calls=0`、`rounds=[]` | 成立（接线层，非 HTTP 端点；原因见 finding V1） |
| 4 | 种子不计一次调用（`dispatched_calls` 只数真实新派发） | A2 `dispatched=2 new_calls=2`、A6 `seed_characters=200` 与库一致 | 成立 |

## 二、命令（逐条退出码）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `shasum -a 256 backend/server.py` | 0 | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` |
| 2 | `shasum -a 256 tests/budgeted_continuation_http_fixtures.py` | 0 | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` |
| 3 | `shasum -a 256 backend/moyu_continuation.py tests/budgeted_continuation_fixtures.py` | 0 | `583059fe…` / `fda39caa…` |
| 4 | `wc -l` 四文件 | 0 | `8111 / 896 / 937 / 1283` |
| 5 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py` | 0 | 无输出 |
| 6 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 56}` |
| 7 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.588s` / `OK` |
| 8 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 3.035s` / `OK` |
| 9 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" \|\| echo "FAIL $f"; done` | 0 | `total=92 fails=0 failfiles=[]`（无 FAIL 行） |

探针退出码：`probe_a_continuation.py` → 1（32 项中 2 项失败，均为同一时序问题，见 A3）；`probe_a3_longseed.py` → 0；`probe_a5_db_source.py` → 0；`probe_a6_material_keys.py` → 0。

## 三、探针 A：假 SSE provider 下的四条主路径

文件：`/tmp/probe_a_continuation.py`（照 `tests/budgeted_continuation_http_fixtures.py` 的假 provider 写法：`http.server` 返回 SSE，按 `Handler.gate` / `Handler.release` 控制轮次时序，`Handler.calls` 记录 provider 真实收到的请求）。

复跑输出（`/tmp/t33_probe_a.out`）关键行：

```
PASS A1 作业失败（500） | failed
PASS A1 provider 实收 2 次 | calls=2
PASS A1 dispatched_calls==provider 实收 | dispatched=2 calls=2
PASS A1 第 1 轮正文已落盘 app_ai_chunk | chunk_len=200 text_start='第1段。文文文文'
PASS A1 计费状态 unknown | unknown
PASS A1 500 后重试被 409 拒绝（计费不明守卫） | 409 {'error': '上次请求是否完成或计费尚不明确，请先核对服务商记录，再手动发起新任务'}
PASS A1 被拒绝的重试零派发 | 2->2
PASS A1 被拒绝的重试不建新 run | 1
PASS A2 取消后落盘正文含第 1 轮 | seed_len=200
PASS A2 取消 run billing=usage_reported | usage_reported
PASS A2 重试入队 202 | 202 …
PASS A2 重试成功 | success
PASS A2 重试首轮 messages 含第 1 轮正文（种子） | seed_len=200 prompt_len=3453
PASS A2 种子不是调用：dispatched 只算新派发 | dispatched=2 new_calls=2
PASS A2 seed_characters 与落盘一致 | 200 vs 200
PASS A2 resumed_from_run 指回原 run | airun_c92522747cd148bb88137df6c622e959
PASS A2 consent 留痕 resume_rebuilt_prompt | True
PASS A2 提示词确实重拼（prompt_hash 改变） | hash same/diff
FAIL A3 重试入队 202 | 409 {'error': '只能重试已失败或已取消的任务。'}
PASS A4 无预算截断 → 失败 | failed
PASS A4 重试成功 | success
PASS A4 第二次请求逐字节等于冻结请求 | payload equal
PASS A4 prompt_hash 一字不改 | hash compare
PASS A4 consent 无续写键 | []
PROBE_A checks=32 failed=2
```

### A1 500 失败 → 正文已落盘，但重试被计费守卫拦下（不是回归）

第 2 轮 provider 返回 500，作业 `failed`、`consent.billing_state=unknown`、第 1 轮 200 字已进 `app_ai_chunk`；随后重试返回 409「上次请求是否完成或计费尚不明确」，且 provider 调用数不变（2→2）、不建新 run。

这是 t21 明确保留的纪律（`backend/server.py:1908-1911`）：

```python
if old_run["status"] == "unknown" or consent.get("billing_state") == "unknown":
    raise RuntimeError("上次请求是否完成或计费尚不明确，请先核对服务商记录，再手动发起新任务")
if row["status"] not in {"failed", "canceled"} or old_run["status"] not in {"failed", "canceled"}:
    raise RuntimeError("只能重试已失败或已取消的任务。")
```

`docs/continuation-production.md` §8.6「重试守卫照旧」与 §8.7 红线 2 已如实写明，因此这一条不构成 finding：500 属于「可能已计费但结果不明」，按设计不可重试；能走断点续写的是「取消 + 用量已上报」路径（A2）。

### A2 取消（usage_reported）→ 重试从落盘正文接着写

时序：`gate3` 让第 1 轮返回 200 字并提交、第 2 轮阻塞在假 provider 里；此时 `POST /api/ai/jobs/{id}/cancel`，再放行 → 作业 `canceled`、`billing_state=usage_reported`、`app_ai_chunk` 只含第 1 轮 200 字。随后 `POST /api/ai/jobs/{id}/retry`：

- 重试作业 `success`，provider 新收 2 次调用（`dispatched_calls=2` 与 provider 实收一致，种子不计入）；
- 重试的第一个 provider 请求 messages 里 `seed_len=200` 的上一轮正文原样出现（`prompt_len=3453`）；
- `consent.resume_rebuilt_prompt=True`、`resumed_from_run` 指回原 run、`seed_characters=200` 与 `moyu_output.count_characters(seed)` 一致；
- 两次 run 的 `prompt_hash` 不同——提示词是重建的，不是重放冻结请求。

对应接线点：

- 取种子：`backend/server.py:1948-1959`，只有拿到种子时才 `frozen_request=None if resume_seed is not None else frozen_request`；
- 种子入 context：`backend/server.py:1709-1717`（`previous_text` / `continuation_requirement` / 下划线键）；
- 提示词分叉：`backend/server.py:1723-1731`（`if frozen_request is not None:` 用冻结请求，否则 `ai_prompt_preview()` 重建）。

### A3 零次 provider 调用：HTTP 端到端的时序结论 + 接线层决定性证明

HTTP 变体（A3）两次复跑都失败，失败原因稳定且可解释：假 provider 的第 2 轮在 `Handler.gate` 上阻塞时，第 1 轮正文（900 字）已经让引擎在**下一轮循环顶**判定 `total ≥ lower` 并 `TARGET_REACHED` 结束，于是作业 `success`，随后的重试被「只能重试已失败或已取消的任务」拒绝：

```
PASS A3 取消 run 已落盘正文达到下限 | chars=900 lower=900 status=success
FAIL A3 重试入队 202 | 409 {'error': '只能重试已失败或已取消的任务。'}
```

根因在引擎停止顺序（`backend/moyu_continuation.py:794-796`）：每轮文本入账后立刻

```python
if total_characters >= lower:
    stop_reason = TARGET_REACHED
    break
```

也就是说，凡 `total ≥ lower` 的运行都会以 `target_reached` 结束；而 `success` 的运行不允许重试。**「重试时种子已够长」这一状态在正常状态机里不可达**，它是防御性守卫（后置证据失效、崩溃、人工修库等异常状态才会出现）。

因此改用与 retry 完全相同的生产函数（`create_ai_job()` + `process_ai_job()`）做接线层证明：`/tmp/probe_a3_longseed.py` 把真实 `app_ai_chunk` 里 604 字正文当作 `resume_seed` 传进去（与 `retry_ai_job()` 传给 `create_ai_job()` 的载荷同形），输出（`/tmp/t33_probe_a3.out`）：

```
A3-engine {"seed==lower": {"seed": 540, "lower": 540, "provider_calls": 0, "stop_reason": "target_reached",
           "rounds": [], "text_equals_seed": true, "seed_characters": 540, "within_tolerance": true},
           "seed>upper": {"seed": 604, "lower": 540, "provider_calls": 0, "stop_reason": "target_reached",
           "rounds": [], "text_equals_seed": true, "seed_characters": 604, "within_tolerance": true}}
A3-wiring {"first_run_chunk_characters": 604, "seed_characters_from_db": 604, "new_job_status": "success",
           "provider_calls_delta": 0, "stop_reason": "target_reached", "rounds": 0, "dispatched_calls": 0,
           "characters": 600, "seed_characters": 600, "target_reached": true, "billing_state": "not_dispatched"}
```

- 引擎层：两种种子各测一次——恰好等于下限（540 = ceil(0.9×600)）与真 run 落盘的 604 字（落在 [540, 660] 区间内）——都是 `provider_calls=0`、`rounds=[]`、返回文本逐字等于种子。（探针里第二个用例的 key 名叫 `seed>upper`，是脚本标签写错：604 并未超过上限 660；数值与判定本身正确，见 `/tmp/probe_a3_longseed.py` 第 36-37 行 `for label, size in (("seed==lower", lower), ("seed>upper", 604))`。）
- 接线层：跑完一个真实的多轮 run（落盘 604 字）→ 用它的 `app_ai_chunk` 拼出的种子建新 job → **provider 调用增量 0，`stop_reason=target_reached`，`rounds=0`，`dispatched_calls=0`，`billing_state=not_dispatched`**（一次都没预留额度）。
- 引擎的零调用判据在循环顶、且在取消与预算检查之前（`backend/moyu_continuation.py:734`）：`if seed_characters >= lower: stop_reason = TARGET_REACHED; break`——已写成正文不会因为「要不要写」的检查再多发一次请求。

`characters=600` 与 `seed_characters=600` 而库里文本长度 604，是字数口径差异而非误差：引擎按 `moyu_output.count_characters`（**只数非空白字符**，`backend/moyu_output.py:134-138`）计数，多轮正文用 `"\n\n"` 拼接，4 个换行符不计入。报告里 `seed_characters` 与作者看到的字节长度本来就不是同一个口径。

### A4 无种子重试 → 逐字节重放冻结请求

无预算的截断请求（`finish_reason=length`）失败后重试：

```
PASS A4 重试成功 | success
PASS A4 第二次请求逐字节等于冻结请求 | payload equal
PASS A4 prompt_hash 一字不改 | hash compare
PASS A4 consent 无续写键 | []
```

即 `frozen_request=None if resume_seed is not None else frozen_request` 的 else 分支确实把原 payload 原样送回去：messages 逐字节相同、`prompt_hash` 完全不变、consent 里没有任何 `resume_*` / `seed_characters` 键。无种子路径行为未变。

## 四、探针 A5：种子来自 DB，而不是内存（决定性证伪）

只读代码不够——`resumable_continuation_seed()` 的 SQL 读的是 `app_ai_chunk`（`backend/server.py:1636-1642`，`ORDER BY seq` 原样拼接、不裁剪），但「真的从库读」要用「只有库里才有的内容」来证。做法（`/tmp/probe_a5_db_source.py`）：取消一个已落盘 run（200 字）→ 直接改库给该分片追加唯一标记 `【DB-ONLY-MARKER-9f3a】` → 重试：

```
PASS A5 落盘分片可枚举 | rows=1 seqs=[1]
PASS A5 DB 正文已追加标记 | before=200 after=221
PASS A5 resumable_continuation_seed == DB 拼接 | seed=221 db=221
PASS A5 重试入队 202 | 202
PASS A5 重试首轮提示含 DB 标记（种子来自库） | marker_in_prompt=True prompt_len=3474
PASS A5 seed_characters 跟随 DB 文本 | 221 vs 221
PASS A5 重试成功 | success
PROBE_A5 checks=7 failed=0
```

标记只存在于数据库里（内存中的 `produced` 早已随取消结束），而它出现在重试的第一个 provider 请求里、且 `seed_characters` 从 200 变成 221。**这直接排除了「种子从内存或重新生成」的可能**，也验证了文档 §8.2「必须读库」与红线 3。

## 五、探针 A6：首轮材料键与审计留痕

`/tmp/probe_a6_material_keys.py`（解析 `ai_run.consent_json.request_audit.payload.messages`）：

```
PASS A6 重试入队 202 | 202
PASS A6 首轮请求含 previous_text 键
PASS A6 previous_text 值逐字等于落盘种子 | seed_len=200
PASS A6 首轮请求含 continuation_requirement
PASS A6 requirement 文案含已写字数 |
     "continuation_requirement":"本次是从断点继续的续写：上一次已经写了 200 字，目标 600 字，还差约 400 字。请紧接上文继续写，不要重复已经写过的内容，不要重新开头，不要复述上文。"
PASS A6 consent.resume_rebuilt_prompt=True
PASS A6 consent.seed_characters 与 DB 一致 | 200 vs 200
PASS A6 consent.resumed_from_run 指回原 run
PASS A6 首轮请求里 max_characters/remaining_characters 存在
PROBE_A6 checks=9 failed=0
```

第 1 轮不只带正文，还带「已写 200 字 / 还差 400 字」的要求与 `max_characters` 额度：这解释了三件事——为什么提示词必须重建（旧请求里没有这些键）、为什么 `prompt_hash` 变化是预期、以及为什么 `remaining_characters` 与种子字数严格对账。

## 六、代码坐标（本报告引用的每个判断都能回指到行）

- `backend/server.py:1610-1622` `_resume_seed_payload()`：种子必须是「非空正文字符串 + 来源 run」。
- `backend/server.py:1625-1642` `resumable_continuation_seed()`：`SELECT text FROM app_ai_chunk WHERE ai_run_id=? AND project_id=? AND branch_id=? ORDER BY seq`，拼接不裁剪。
- `backend/server.py:1645-1651` `_resume_requirement()`：把 `remaining = target - seed` 写成一句要求。
- `backend/server.py:1709-1717`：种子入 context（`_seed_text` / `_seed_characters` / `_resumed_from_run` / `previous_text` / `continuation_requirement`）。
- `backend/server.py:1723-1731`：`if frozen_request is not None:` 走冻结请求，否则重建提示词——两条路径显式二选一。
- `backend/server.py:1754-1757`：`consent.resume_rebuilt_prompt / resumed_from_run / seed_characters` 审计留痕。
- `backend/server.py:1948-1959`：`retry_ai_job()` 取种子并 `frozen_request=None if resume_seed is not None else frozen_request`。
- `backend/server.py:1908-1911`：计费不明 / 非失败非取消时的重试守卫（t21 未放宽）。
- `backend/moyu_continuation.py:656-662`：`seed_characters = count_characters(seed_text)`，空种子按「没有种子」处理。
- `backend/moyu_continuation.py:733-736`：种子 ≥ 下限时在取消/预算检查之前直接 `TARGET_REACHED`。
- `backend/moyu_continuation.py:794-796`：每轮入账后 `total >= lower → TARGET_REACHED`（决定了 A3 的 HTTP 时序）。
- `backend/moyu_output.py:134-138`：字数口径 = 非空白字符数。

## 七、findings

| id | severity | problem | requiredFix |
| --- | --- | --- | --- |
| V1 | low | 「重试时种子 ≥ 0.9×target」在正常状态机下不可达（`total ≥ lower` 必然以 `target_reached`/success 结束，而 success 不允许重试，`backend/server.py:1911`），因此该断言的端到端 HTTP 复现只能靠异常状态（如 `backend/server.py:2555` 的 `invalid_evidence` 后置失效、崩溃、人工修库）。本报告用接线层（`create_ai_job` + `process_ai_job`）证明 0 调用，不是 HTTP 端点级证明。 | 在下一次触碰该主题时二选一并落到测试里：① 在 `tests/budgeted_continuation_http_fixtures.py` 用「失败但正文完整」的合法异常路径（`invalid_evidence`）构造可重试且 `seed ≥ lower` 的 run，把该守卫钉成可复现断言；② 或在 `docs/continuation-production.md` §8.3 第 5 条注明「该场景是防御性守卫，正常取消/失败路径不可达」。两者都不影响当前实现的正确性。 |

补充观察（不构成 finding，仅记录以免后人误判为回归）：

1. **500 后重试被 409 拒绝**是设计（`docs/continuation-production.md` §8.6「重试守卫照旧」、§8.7 红线 2），不是断点续写失效；断点续写的真实入口是「取消 + 用量已上报」。
2. **`seed_characters` 与库里文本字节长度可以不等**（604 vs 600），因为字数口径只数非空白字符，多轮用 `"\n\n"` 拼接；两个数都对，不要按字节对账。

## 八、verdict

**pass**。t22 的四条断点续写声明在当前字节（`backend/server.py` = `a70a25ce…`、`backend/moyu_continuation.py` = `583059fe…`）上全部成立；无 high / blocker；唯一 low 项是「零调用守卫的端到端可复现性」，已在 V1 给出两种收敛方式，不影响交付。

本报告是 `docs/verification-supersede-t22-2026-10-24.md`，也是本轮唯一写入的仓库文件。
