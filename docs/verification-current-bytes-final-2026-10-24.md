# 当前字节合并终验（t34）— 2026-10-24

> **结论：pass。** 在当前字节上复现 P12（失败轮计账）、P13（报告键集恒定）、断点续写（种子来自 `app_ai_chunk`）、谓词回落与槽位语义，全部成立；8 条必跑命令退出码全 0，全量 fixture 92/92 无 FAIL；5 个被测文件验收前后 sha256 与行数逐字节一致。无 high / blocker 问题。
>
> 本报告绑定的字节：`backend/server.py` `a70a25ce…`、`backend/moyu_consistency.py` `39dee239…`、`backend/moyu_continuation.py` `583059fe…`、`tests/consistency_fixtures.py` `040303f5…`、`tests/budgeted_continuation_http_fixtures.py` `67e0aa21…`（完整值见下表）。字节一旦变化，本结论不再适用。

## 零、被测字节（A）

`shasum -a 256` + `wc -l`，验收开始（`/tmp/t34_pre.txt`）与验收结束（`/tmp/t34_post.txt`）各采一次：

| 文件 | sha256（开始 = 结束） | 行数（开始 = 结束） | 一致 |
| --- | --- | --- | --- |
| `backend/server.py` | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` | 8111 | ✅ |
| `backend/moyu_consistency.py` | `39dee239a96ab9495659c9a3ca6f4f9fdedda85cf1c81178684203f25b55b732` | 1165 | ✅ |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 | ✅ |
| `tests/consistency_fixtures.py` | `040303f5b452bd1a7f3e514baf6be6a04db1a86b8e4b55291b80b6195769e052` | 566 | ✅ |
| `tests/budgeted_continuation_http_fixtures.py` | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` | 937 | ✅ |

`diff /tmp/t34_pre.txt /tmp/t34_post.txt` → 无输出，`BYTES_UNCHANGED=true`。本轮我只新建本报告，未修改任何被测文件。

## 一、必跑命令（F）

日志在 `/tmp/t34_cmds/`（1–7）与 `/tmp/t34_sweep/`（全量）。

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.352s` / `OK` |
| 4 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 56}` |
| 5 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.522s` / `OK` |
| 6 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 2.992s` / `OK` |
| 7 | `.venv/bin/python tests/memory_assertion_predicate_fixtures.py` | 0 | `Ran 24 tests in 21.816s` / `OK` |
| 8 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" \|\| echo "FAIL $f"; done` | 0 | `TOTAL=92 FAIL=0`（`/tmp/t34_sweep/codes.txt` 92 行、非零 0 行） |

## 二、P12：失败轮计账（B）

假 provider `boom`（第 2 轮 `fail_with(500)`），`/tmp/probe_t32.py` → `CHECKS=77 FAILS=0`：

- provider 实收 2 次：request_id `airun_cbf5…` 与 `airun_cbf5…-r2`；
- `report["dispatched_calls"] = 2` == provider 实收 2；
- `report["rounds"] = 1` == `len(report["round_metrics"]) = 1`；
- 第 1 轮正文保留（200 字）、`stop_reason=failed`、`characters=200`、`usage_state=partial`、`budget_plan/retries/extended_rounds` 均为 `null`、`target_reached=false`、`within_tolerance=false`；
- 取消路径（`gate2` 第 2 轮挂起后取消）同样 provider 实收 2、`dispatched_calls=2`、`rounds=1`、`len(round_metrics)=1`；
- 成功路径 5 次调用 → 5/5/5（`dispatched_calls`、`rounds`、`metrics` 长度）。

## 三、P13：报告键集恒定（C）

`set()` 直接比较，探针输出差异均为 `[]`（严格相等）：

- 无 `round_target_ratio`：**19 键** —— `candidate_only, characters, counting, dispatched_calls, extended_rounds, hard_max_rounds, max_rounds, resumed_from_run, retries, round_metrics, rounds, seed_characters, stop_reason, target_characters, target_range, target_reached, task, usage_state, within_tolerance`
- 带 `round_target_ratio`：**21 键** —— 上述 19 键 + `budget_plan, round_target_ratio`
- 失败（带 ratio）== 成功（带 ratio）：`[]`；失败（无 ratio）== 成功（无 ratio）：`[]`；取消（带 ratio）== 成功（带 ratio）：`[]`

19/21 是 t21 在 t18 的 17/19 上扩展（`seed_characters`、`resumed_from_run`），旧键一个不少；非续写路径这两键写 `0` / `None`，键集恒定。

## 四、断点续写（D）

### D1 种子来自 `app_ai_chunk`（改库标记串证伪）

`/tmp/probe_t34_D.py` → `CHECKS=24 FAILS=0`。步骤与证据：

1. 用 fixture 的合法中断前提（第 2 轮已派发、usage 已上报、随后取消）造父运行，chunk 已落盘 1 段；
2. **直接 UPDATE `app_ai_chunk`**，给父运行每段追加标记串 `T34SEEDMARKER-鲸`（模拟「重启/另一进程读到库里的正文」）；
3. retry → 202 → success；重试首请求的 payload 里**命中标记串**，`resumed_from_run == airun_8b644f1e…`（直接父 run），`resume_rebuilt_prompt=true`；
4. `seed_characters=215`（= 改库后正文的非空白字符数），`characters=615`，正文以改库后的种子开头，新写 404 字；`dispatched_calls=2` == provider 实收 2；重试 chunk `seq=[1]` 从 1 连续。

结论：种子读的是 DB 里的 chunk 文本，不是内存缓存、不是 `ai_run` 重算；改库后重试能读到改后的字节——这是最硬的证伪方式。

### D2 种子够长 → 零调用 TARGET_REACHED

把父运行 chunk 改成 600 个「鲸」（target 600，下限 `ceil(0.9×600)=540`）后 retry：

- `stop_reason=target_reached`、`dispatched_calls=0`、provider 实收 0（前后计数不变）、`rounds=0`、`round_metrics=[]`；
- 返回正文 == 600 字种子原样。

### D3 无种子普通重试逐字节不变

`/tmp/probe_t27_A.py` → `CHECKS=38 FAILS=0`：无 budget 的 `trunc` 失败重试，messages 与上一次逐字节相同、`prompt_hash` 不变、consent 无 `resume_rebuilt_prompt/resumed_from_run/seed_characters`。

## 五、谓词与回落（B，原 t23）

`/tmp/probe_t27.py` → `CHECKS=44 FAILS=0`（B 段）：

- 带 `attribute` 的记忆 → `predicate == has_attribute`，`object_json` 保留 `attribute` 与 `value`，复杂结构（dict/list）原样不字符串化；
- 不带 `attribute` → `predicate == has_story_fact`，object 恰 `name/type/note` 三键；
- 白名单外谓词（`mood_swing`）+ `attribute` → 回落 `has_attribute`，`memory.assertion_predicate_fallback` 事件 payload 恰 `{requested, used, reason}` 且 `requested == "mood_swing"`。

## 六、槽位语义（E，原 t24/t25）

同一探针 C-db 段（真实 `analyze()` + 临时库）：

- 同一实体 10 条互不矛盾事实 → `findings=0`、`score=100.0`、`grade=A`；
- 5 个不同 attribute 槽位 → `findings=0`、`grade=A`；
- 同槽位不同 value → `findings=1`、`severity=high`、标题含槽位名、`score=92.0`、`grade=B`；
- 同槽位同值（note 不同）→ `findings=0`；
- `located_in` 两个取值 → `findings=1`、`severity=high`；
- 从未见过的谓词名 + 同槽位不同值 → `findings=1`、`severity=high`、标题含槽位名（按数据形状判定，不靠谓词白名单）。

## 七、观察与已披露的探针自纠

- **O1（低，方法学）**：我第一版 D 探针在一个**刚入队、未落任何 chunk** 的取消运行上重试，得到 `seed_characters=0`、无 `resumed_from_run`——这是预期行为（chunk 只在作业收尾时落盘，未落盘就没有种子），不是缺陷；改用 fixture 的「第 2 轮已派发 + usage 已上报 + 取消」合法前提后 24/24 通过。记录在此，避免后来者把「取消得太早 → 无种子」误判为断点续写失效。
- 本轮未发现 high 或 blocker，故 verdict = pass。O1 不需要修复。

## 八、已知边界（未覆盖，明说）

1. 槽位与谓词验证使用合成库 + 合成事实（fixture 风格），不是真实稿件；真实稿件上的假阳/假阴率未测。
2. D 的「长种子」是把 chunk 直接改写成 600 字构造的边界，不是真实模型产出的长正文；超长种子（> target 上界）的 `within_tolerance=false` 行为未在本轮重测（t22 已记录为既有语义）。
3. 跨项目/跨分支脏 chunk 的隔离只由 `resumable_continuation_seed` 的 `WHERE project_id=? AND branch_id=?` 保证，未做 HTTP 层跨项目重试实验。
4. 并发重试（同一 run 同时两次 retry）未测；本轮只覆盖串行重试。
5. `docs/consistency-engine.md` 属本轮 out of scope，未做文档一致性复核（t27 已覆盖，且该文件此后未再变）。

## 九、账本 `reviewedArtifacts`（update_task 之后实测）

`agent_teams_update_task` 完成后读 `.agent-teams/moyu-q1-production/team.json`（只读诊断）：

- t34 记录里**有** `reviewedArtifacts` 字段，7 条，`present=true`；
- 其 `sha256` 与我在报告零节采集的当前字节逐一对上：`backend/server.py` `a70a25ce…`、`backend/moyu_consistency.py` `39dee239…`、`backend/moyu_continuation.py` `583059fe…`、`tests/consistency_fixtures.py` `040303f5…`、`tests/budgeted_continuation_http_fixtures.py` `67e0aa21…`，另有 `tests/budgeted_continuation_fixtures.py` `fda39caa…` 与 `tests/memory_assertion_predicate_fixtures.py` `10719789…`；
- t34 的 dependencies 为 `t21, t23, t25`。

结论：**`reviewedArtifacts` 确实随 dependencies 自动快照产生**——空 dependencies 的 t29/t31/t32/t33 没有该字段，带 dependencies 的 t18/t22/t27/t34 都有，且本轮快照与实测字节一致。以后建验收单应显式挂 implementation/repair 依赖。

---

验收人：verifier ｜ 任务：t34 ｜ attempt：`f1544ce3-f341-471b-973e-33c71eac6573`
本报告绑定的核心字节：`backend/server.py` `a70a25ce…`、`backend/moyu_consistency.py` `39dee239…`、`backend/moyu_continuation.py` `583059fe…`。字节变化则本报告结论作废。
