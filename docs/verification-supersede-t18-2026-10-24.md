# 取代 t18：当前字节上重验 P12 / P13

**结论：pass。** `backend/server.py` 当前字节上 P12（`dispatched_calls` 说真话）与 P13（失败/取消/成功三路径报告键集合严格相等）均可复现；8 条必跑命令全绿（全量 92 个 fixture 0 失败），4 个被测文件验收始末逐字节一致。唯一口径更正：t18 探针写死的 17/19 键在 t21 加入 `seed_characters` / `resumed_from_run` 后已是 19/21——属预期的键扩展，不是回退（见 §四）。

验收人：verifier（attempt `8568c7d2-a29c-4e4d-ad7e-0e24e693a311`）。本轮只新增本文件，未修改 backend/ tests/ frontend/ benchmarks/。

## 零、被测字节

`shasum -a 256` + `wc -l`，验收开始与结束各采一次：

| 文件 | sha256 | 行数 | 始末一致 |
| --- | --- | --- | --- |
| `backend/server.py` | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` | 8111 | 是 |
| `tests/budgeted_continuation_http_fixtures.py` | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` | 937 | 是 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 | 是 |
| `tests/budgeted_continuation_fixtures.py` | `fda39caa50b3fe8b6e06ef4555e1a69bd048eaee768addc72089362475cc5429` | 1283 | 是 |

## 一、命令表

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 0a | `shasum -a 256 backend/server.py` | 0 | `a70a25ce…bebdf` |
| 0b | `shasum -a 256 tests/budgeted_continuation_http_fixtures.py` | 0 | `67e0aa21…f308a` |
| 0c | `shasum -a 256 backend/moyu_continuation.py` | 0 | `583059fe…1a87a9` |
| 0d | `shasum -a 256 tests/budgeted_continuation_fixtures.py` | 0 | `fda39caa…5cc5429` |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.352s` / `OK` |
| 4 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `OK {'cases': 56}` |
| 5 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.515s` / `OK` |
| 6 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 3.000s` / `OK` |
| 7 | `.venv/bin/python tests/memory_assertion_predicate_fixtures.py` | 0 | `Ran 24 tests in 21.975s` / `OK` |
| 8 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f"; done` | 0 | `8 total=92 fail=0`（92 份 `sweep_*.log`） |

## 二、P12：`dispatched_calls` == provider 实收请求数

自造探针 `/tmp/probe_t32.py`（只读；复用 `tests/budgeted_continuation_http_fixtures.py` 的假 SSE provider 与 HTTP harness，自己数请求，不采信被测套件断言）：**77 checks / 0 fail / exit 0**。

| 场景 | provider 实收 | `report.dispatched_calls` | `rounds` | `len(round_metrics)` |
| --- | --- | --- | --- | --- |
| 成功、无 ratio（target 900 / max_rounds 6） | 5 | 5 | 5 | 5 |
| 成功、ratio=1.35 | 5 | 5 | 5 | 5 |
| 第 2 轮 500（`boom`，无 ratio） | 2（`request_id` = `<run>`、`<run>-r2`） | 2 | 1 | 1 |
| 第 2 轮 500（`boom`，ratio=1.35） | 2 | 2 | 1 | 1 |
| 第 2 轮中途取消（`gate2`，ratio=1.35） | 2 | 2 | 1 | 1 |

失败场景补充实测：`stop_reason=failed`、`characters=200`、第 1 轮正文保留、`usage_state=partial`、`budget_plan=null`、`retries=null`、`extended_rounds=null`、`target_reached=false`、`within_tolerance=false`；DB 里 `consent.budgeted_continuation_report.dispatched_calls` 也是 2，且键集与 HTTP 返回相同。

## 三、P13：失败/取消/成功键集合严格相等

探针用 `set(report)` 比较，结果（逐键列出，来自探针 `KEYS_*` 输出）：

- 无 ratio（19 键，成功 == 500 失败）：`candidate_only, characters, counting, dispatched_calls, extended_rounds, hard_max_rounds, max_rounds, resumed_from_run, retries, round_metrics, rounds, seed_characters, stop_reason, target_characters, target_range, target_reached, task, usage_state, within_tolerance`
- 带 ratio（21 键，成功 == 500 失败 == 取消）：上述 19 键 + `budget_plan, round_target_ratio`

即失败/取消路径没有少键，也没有多出只在该路径出现的键；`assert_report_shape` 的 17/19 期望在 t21 之后是 19/21（见 §四）。

## 四、口径说明：17/19 → 19/21 是扩展，不是回退

t18 的探针把键数写死为 17（无 ratio）/ 19（带 ratio）。t21（断点续写）在报告里固定加入 `seed_characters` 与 `resumed_from_run`（非续写时写 `0` / `null`），因此当前契约是 19/21，且旧 17 键全部仍在。我把 t18 探针的两处计数改为 19/21 后 77 checks 全过；这是键集合的**追加**，不是删除或改名，故不构成 P13 回归。

## 五、结论与已知边界

- P12、P13 在当前字节上成立；无 blocker / high / medium / low finding。
- 已知边界：P12 只覆盖「第 2 轮 500」与「第 2 轮中途取消」两种中断；第 1 轮即失败、第 3 轮以后失败、并发取消等路径未单独构造（沿用 t18/t22 的边界结论）。
- 本报告只新增本文件；4 个被测文件在验收始末哈希一致。

**本结论绑定字节**：`backend/server.py a70a25ce…`(8111)、`tests/budgeted_continuation_http_fixtures.py 67e0aa21…`(937)、`backend/moyu_continuation.py 583059fe…`(896)、`tests/budgeted_continuation_fixtures.py fda39caa…`(1283)。任一文件再变，本结论作废，需重跑。
