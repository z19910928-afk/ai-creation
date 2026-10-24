# 当前字节终验：断点续写 + 记忆断言谓词 + 槽位语义 + 文档

**结论：pass。** 当前字节上 t21/t23/t24/t25/t26 的验收点全部可复现，未发现 blocker/high 缺陷；两条过程观察 O1（low，口径）/ O2（medium，验收期间文档被外部会话改动），均不构成对交付的阻断。

验收人：verifier（attempt `c72243f0-359c-4b85-a7d0-43a1a835a632`）。本报告是本次唯一写出的仓库文件；被测代码只读。

---

## 零、被测字节

开工时用 `shasum -a 256` + `wc -l` 采集，验收结束后重算（§七）逐字节一致。

| 文件 | sha256（验收开始） | 行数 | 验收后 |
| --- | --- | --- | --- |
| `backend/server.py` | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` | 8111 | 一致 |
| `backend/moyu_consistency.py` | `39dee239a96ab9495659c9a3ca6f4f9fdedda85cf1c81178684203f25b55b732` | 1165 | 一致 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 | 一致 |
| `tests/consistency_fixtures.py` | `040303f5b452bd1a7f3e514baf6be6a04db1a86b8e4b55291b80b6195769e052` | 566 | 一致 |
| `tests/consistency_http_fixtures.py` | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` | 199 | 一致 |
| `docs/consistency-engine.md` | `a07808b29656ee3cbbded011a388ecd050d32791e7e7f61783ad0c6ff166b3ad` | 376 | 窗口内未变（窗口开始前被外部会话从 374 行改到 376 行，见 O2） |

> 说明：captain 在任务描述里给过同样的前缀值，本表数值是我自己 `shasum -a 256` 跑出来的；两者一致只说明描述没写错，不作为证据来源。
>
> 字节窗口：最初取基线时 `docs/consistency-engine.md` 是 `8e7d324d…` / 374 行；15:05:56 被另一个会话追加「维护约定」段变成 `a07808b2…` / 376 行（非我所为）。我随即以 376 行版本重跑 D 段（44 checks / 0 fail），并在探针跑完后再次重算 6 个哈希，与上表逐字节相同（`STABLE_ACROSS_PROBE`）；5 个代码/测试文件全程未变。

---

## 一、命令表（全部本机实跑）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.360s` / `OK` |
| 4 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 56}` |
| 5 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.449s` / `OK` |
| 6 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 3.211s` / `OK` |
| 7 | `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests in 2.186s` / `OK` |
| 8 | `.venv/bin/python tests/memory_assertion_predicate_fixtures.py` | 0 | `Ran 24 tests in 24.572s` / `OK` |
| 9 | `.venv/bin/python tests/analysis_candidate_reuse_http_fixtures.py` | 0 | `OK`；`source_unchanged: true`，`checks: 7` |
| 10 | `.venv/bin/python tests/analysis_candidate_typed_http_fixtures.py` | 0 | `OK`；`source_unchanged: true`，`checks: 13` |
| 11 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" || echo "FAIL $f"; done` | 0 | `11 total=92 fail=0`（详见 §七） |

命令 5/8/9/10 的完整日志在 `/tmp/t27c/*.log`；命令 9/10 自己也会核对 `backend/server.py` 前后 hash，均为 `a70a25ce…`。

---

## 二、A. 断点续写（t21）

探针 `/tmp/probe_t27_A.py`（只读；复用 `tests/budgeted_continuation_http_fixtures.py` 的假 SSE provider 与 HTTP harness，自建三个场景）：**38 checks / 0 fail / exit 0**。

### A1 第 2 轮已派发、用量已上报后取消 → 重试从落盘正文接着写

- 造法：`gate3`（第 2 轮挂住），等 provider 收到 2 次调用后取消，`Handler.release.set()` 放行。
- 实测：作业 `canceled`；`app_ai_chunk` 已落第 1 轮正文；`POST /api/ai/jobs/{id}/retry` 返回 202（`retry_of` 指向旧 run）；重试第 1 次 provider 请求的 user message **含第 1 轮正文前缀、含「从断点继续」、不含「从零」**；重试后的 chunk `seq` 从 1 连续。
- 说明：这条路径要求 `billing_state == usage_reported`（用量帧已上报），否则重试被计费守卫拒绝（见 A1b）。

### A1b 第 2 轮 500 失败 → 正文保住但重试被计费守卫拦住（设计行为）

- 造法：`boom`（第 2 轮返回 500）。
- 实测：作业 `failed`；`app_ai_chunk` 里**仍有第 1 轮正文**；`POST .../retry` 返回 **409**，响应含「计费」。原因：5xx 后 `billing_state=unknown`，`retry_ai_job` 的守卫要求先人工核对计费。
- 口径观察 O1：任务描述 A 段的字面链路「500 → 立即 retry → 带正文」在当前实现里**不可达**（被计费守卫挡住，且这是 t22 已验证的正确行为）。可续写的中断形态是「带账的失败/取消」，A1 用取消路径覆盖了同一机制。这不是缺陷，但文档/任务描述若写成「500 后重试会续写」会误导。

### A2 chunk 已够长 → 重试零次 provider 调用，直接 TARGET_REACHED

- 造法：取消后把已落盘 chunk 拼长到 ≥ `ceil(0.9×600)=540`（种子仍只从 `app_ai_chunk` 读），再 retry。
- 实测：`stop_reason=target_reached`、`dispatched_calls=0`、`rounds=0`、`round_metrics=[]`、provider 实收 0 次、正文 hash 等于种子 hash。

### A3 无种子的普通重试 → 逐字节重放冻结请求

- 造法：无 budget 的 `trunc` 失败（无 chunk）→ retry。
- 实测：第 2 次 provider payload 与第 1 次**逐字节相同**；`prompt_hash` 相同；consent 中 `resume_rebuilt_prompt` / `resumed_from_run` / `seed_characters` **三个键都不存在**。

---

## 三、B. 记忆断言谓词（t23）

探针 `/tmp/probe_t27.py`（只读；直接走 `ensure_memory_assertion()` 与 `event_log` 查库）覆盖 B 段 9 项，全部通过：

- 带 `attribute` 的记忆 → `assertion.predicate == "has_attribute"`，`object_json` 含 `attribute` 与 `value`，且 `value` 的 dict/list 结构原样保留（不被字符串化）。
- 不带 `attribute` 的记忆 → `predicate == "has_story_fact"`，`object_json` 恰为 `name` / `type` / `note` 三键。
- 白名单外谓词（`mood_swing`）+ `attribute` → 落库为 `has_attribute`；`event_log` 出现 `memory.assertion_predicate_fallback`，payload 恰为 `{requested, used, reason}`，`requested == "mood_swing"`。

---

## 四、C. 槽位语义（t24/t25）

同一探针 C 段：直接调 `_classify_object_conflict()` 9 项 + 真实临时库跑 `analyze()` 8 组，全部通过。

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 同一实体 10 条互不矛盾事实（无槽位） | 0 / 100.0 / A | 0 / 100.0 / A |
| 同一实体 5 个不同 `attribute` 槽位 | 0 / 100.0 / A | 0 / 100.0 / A |
| 再加同槽位不同 `value` | 1 条 high / 92.0 / B、标题含槽位名 | 1 条 high / 92.0 / B |
| 同槽位同值（`note` 不同） | 0 | 0 |
| `located_in` 两个取值 | high | high（classify 与库内各 1 条） |
| 从未见过的谓词名 + `attribute`（同槽位不同值） | 仍按槽位 → high、标题含槽位 | high、标题含「心情」 |
| 有槽位 vs 未分类（`""`） | 跳过 | 跳过 |
| 一侧不是 JSON 对象 | high | high |

补充：t25 把 `if left_attribute or right_attribute:` 放在谓词名分支之前，判定依据是数据形状而非谓词名；上面的「未见过的谓词」用例专门锁这一点。

---

## 五、D. 文档（t26）

- 全仓 grep「两条不同描述」「互不包含」：`docs/consistency-engine.md` 与 `docs/consistency-engine-spec.md` 均**无匹配**（唯一命中的是 `.agent-teams/moyu-q1-production/team.json` 的任务元数据，不是文档）。
- `docs/consistency-engine.md` §「其余情况按 `_classify_object_conflict()` 判定」表共六行，逐行对照 `backend/moyu_consistency.py` 的真实分支顺序：

| 文档行 | 代码分支 | 一致 |
| --- | --- | --- |
| ① 任一侧不是 JSON 对象 → high | `if not isinstance(left, dict) or not isinstance(right, dict)`（先于槽位） | ✅ |
| 至少一侧带非空 `attribute` 但槽位不同 → 跳过 | `if not (left_attribute and left_attribute == right_attribute): return None` | ✅ |
| 同槽位、任一侧有 `value`、取值不同 → high | `if "value" in left or "value" in right:` + `_same_value` | ✅ |
| 同槽位、同值或都无 `value` → 继续看名字漂移 | 该分支不 return，落到名字漂移 | ✅ |
| 无槽位且谓词非 `has_story_fact` → high | `elif predicate != "has_story_fact":` | ✅ |
| 两侧 `name` 都非空且不同 → high | 名字漂移分支 | ✅ |

- 裸行号：`grep -nE '(server|moyu_continuation|moyu_consistency|run)\.py:[0-9]+'` 对两份文档均无输出（退出码 1）。
- 文档在验收期间被外部会话从 374 行改到 376 行（新增「维护约定」段）；我在最终 376 行版本上重跑了 D 段与 `_classify_object_conflict()` 对照，六行顺序仍一致（探针 44 checks / 0 fail）。
- 文档写的回归数字（10 条→0/100/A、5 槽位→0/100/A、同槽位不同值→1 条 high/92/B、名字漂移→high）与 `tests/consistency_fixtures.py` 及本报告 §四 的实测一致（§四 的数字是我自己跑出来的，不是抄文档）。

---

## 六、问题清单

**无 blocker / high / medium。** 只有一条 low 级口径观察：

**O2（medium，过程）**：`docs/consistency-engine.md` 在验收窗口开始前被另一个会话从 374 行（`8e7d324d…`）改为 376 行（`a07808b2…`，新增「维护约定」段，mtime 15:05:56，非我所为）。改动本身是补充维护约定、不改变判定表，我已在最终版本重跑 D 段并通过；但它使「验收前后逐字节未变」只能对 5/6 个文件成立。要求：验收期间冻结被测文件；若必须改，先通知验收人重新取基线并在报告登记新旧哈希。

**O1（low，口径）**：任务描述 A 段的「第 2 轮 500 失败 → retry → 首请求带上一轮正文」链路实际不可达——`retry_ai_job` 在 `billing_state=unknown` 时返回 409「上次请求是否完成或计费尚不明确」。这是 t22 已验过的正确守卫，不是本任务引入。建议把 A 段描述改为「带账的失败/取消（`usage_reported`）→ retry」，或明确写「500 后必须先人工核对计费，重试会被 409 挡住」。证据：§二 A1b。

---

## 七、全量回归与字节一致性

- `for f in tests/*_fixtures.py; do .venv/bin/python "$f" || echo "FAIL $f"; done`：`11 total=92 fail=0`（92 个文件逐个执行，无 FAIL 行）。日志在 `/tmp/t27c/sweep_*.log`。
- 验收结束后重算 6 个被测文件 sha256/行数（`shasum -a 256` + `wc -l`）：5 个代码/测试文件与 §零 开工值逐字节相同；`docs/consistency-engine.md` 因外部会话在窗口开始前改动（374→376），已改用最终哈希 `a07808b2…` 并重跑 D 段。探针跑完后的二次重算与 §零 表 6/6 相同（`STABLE_ACROSS_PROBE`）。`docs/verification-consistency-current-bytes-2026-10-24.md` 是本任务唯一新增文件，未修改任何被测代码。
- 说明：并行跑全量时另有一个验收会话（t33）也在跑同一批 fixture，曾导致我第一轮 sweep 中断（非失败）；本报告采用重启后的串行结果 `92/0`。

---

## 八、已知边界（没有覆盖的部分）

1. **真实稿件未跑**：C 段用的是临时合成 SQLite（10 条事实/5 槽位等），不是真实 40 章稿件；`docs/consistency-engine-spec.md` 的合成库口径未被本报告扩大到真实作品。
2. **500 后重试路径**：被计费守卫有意挡住，本报告验证的是「挡住」而不是「续写」（见 O1）。
3. **并发重试**：两个 retry 同时打同一 job 的竞态未测。
4. **跨项目脏 chunk**：`resumable_continuation_seed` 的 project/branch 过滤只做代码核对，未构造跨项目脏数据。
5. **前端**：本任务范围不含 UI，未跑浏览器用例。
6. **超大种子**：没有构造 10 万字级种子看 `seed_characters` 计算与首轮 prompt 体积。
7. **文档可变性**：`docs/consistency-engine.md` 在本次验收期间被外部会话改动一次（O2）；本报告绑定最终 376 行版本，若再变需重跑 D 段。

---

**本结论绑定的字节**：`backend/server.py` `a70a25ce…`(8111)、`backend/moyu_consistency.py` `39dee239…`(1165)、`backend/moyu_continuation.py` `583059fe…`(896)、`tests/consistency_fixtures.py` `040303f5…`(566)、`tests/consistency_http_fixtures.py` `86d0ad6d…`(199)、`docs/consistency-engine.md` `a07808b2…`(376)。上述任一文件变更后本结论即失效。
