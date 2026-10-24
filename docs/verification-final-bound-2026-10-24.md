# 最终绑定验收：断点续写 + 谓词/槽位 + 计费键集 + 文档（2026-10-24）

**结论：pass。** 当前冻结字节上，P12/P13、断点续写、谓词回落与槽位语义、文档一致性四类事实全部独立复现通过；无 high/blocker。

验收人：verifier（独立于实现者；未修改任何被测文件，唯一写入本报告）。
验收任务：t48，attempt `b0f9cf93-f476-4013-8116-2962194ff9dd`。

## 零、被测字节

验收开始时（`shasum -a 256` + `wc -l`）与结束时各采一次，逐字节一致（`diff` 输出 `ALL_IDENTICAL`）。

| 文件 | sha256 | 行数 |
| --- | --- | --- |
| `backend/server.py` | `d16a964c6d7548c64df88a1676ed92ad8f99139be8d52f7eae2b6f01e29f4c9f` | 8140 |
| `backend/moyu_consistency.py` | `a5a88b06a4c12da307a8a0358e37c1ac52d6053a9bf7ace69e56d3ee3ba24327` | 1190 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 |
| `tests/memory_assertion_predicate_fixtures.py` | `f6eeff0dede105e6e76ea4bef14538eb6534ab65e1e382fed7727723a9c4b3c8` | 355 |
| `tests/consistency_fixtures.py` | `05ef1cf6c07cb06ca22f92c141a91e80d594a8417b5a8fd60cf16e658c3795ef` | 597 |
| `docs/consistency-engine.md` | `1f409fe11a26c448f204ddee4f613874c8aff0a048d0a7c7eaa5bf59510852ee` | 382 |

验收期间未发现被测文件变动（`ALL_IDENTICAL`，且全量测试跑完后再次采集仍相同）。

## 一、命令与退出码

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests ... OK` |
| 4 | `.venv/bin/python tests/memory_assertion_predicate_fixtures.py` | 0 | `Ran 28 tests ... OK` |
| 5 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 56}` |
| 6 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests ... OK` |
| 7 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests ... OK` |
| 8 | `.venv/bin/python tests/analysis_candidate_reuse_http_fixtures.py` | 0 | `OK`，`{"passed": true, "checks": 7, ... "source_unchanged": true}` |
| 9 | 全量 `for f in tests/*_fixtures.py; do .venv/bin/python "$f" ...; done` | 0 | `TOTAL=92 FAILS=0` |
| 10 | `shasum -a 256` / `wc -l` 六个被测文件 | 0 | 见「零」；前后 `ALL_IDENTICAL` |
| 11 | `grep -nE '(server\|moyu_continuation\|moyu_consistency\|run)\.py:[0-9]+' docs/consistency-engine.md` | 1 | 无匹配（无裸行号） |
| 12 | `grep -n "两条不同描述\|互不包含" docs/consistency-engine.md docs/consistency-engine-spec.md` | 1 | 无匹配（已删规则无残留） |
| 13 | `git diff --check` | 0 | 干净 |

## 二、P12/P13：计费口径与报告键集

探针 `/tmp/probe_t32.py`（复用仓库 fake SSE provider，真实 loopback HTTP + 临时 SQLite）→ **77 checks，0 fail，exit 0**。

- 第 2 轮 provider 500（case `boom`）：provider 实收 **2** 次（`airun_…` 与 `airun_…-r2`），`report.dispatched_calls == 2`、`rounds == 1`、`len(round_metrics) == 1`，第 1 轮正文保留，`stop_reason == "failed"`，`job.status == "failed"`。
- 第 2 轮中途取消（case `gate2`）：provider 实收 **2**，`dispatched_calls == 2`、`rounds == 1 == len(round_metrics)`，不再派发第 3 轮，正文保留，`stop_reason == "canceled"`。
- 键集：成功/失败/取消三路径在无 ratio 档均为 **17 键**、带 ratio 档均为 **19 键**，逐键相等（探针输出 `shape.failure_plain==success_plain []`、`shape.failure_ratio==success_ratio []`、`shape.cancel==success_ratio []`）。
- 不可得值：`budget_plan` / `retries` / `extended_rounds` 为 `null`，失败路径 `usage_state == "partial"`（已有轮次上报），不冒充 `reported`。

## 三、断点续写：种子来自 `app_ai_chunk`

探针 `/tmp/probe_t34_D.py` → **24 checks，0 fail**。

- 先造「第 2 轮已派发、usage 已上报、随后取消」的父运行（`canceled_after_usage()`），确认 chunk 已落盘。
- 直接 `UPDATE app_ai_chunk SET text = text || ? WHERE ai_run_id=? AND seq=?` 给每段追加随机标记 `T34SEEDMARKER-鲸`（模拟另一个进程/重启后改库）。
- 重试后**首个 provider 请求**的 messages 含该标记；`seed_characters == 215 == 库内各 chunk 拼接的非空白字符数`；`resumed_from_run ==` 父 run id；正文以种子开头。
- 长种子场景（父运行正文 ≥ `ceil(0.9×target)`）：重试 **0 次 provider 调用**，`stop_reason == "target_reached"`，`rounds == 0`，正文即种子。

## 四、谓词：无槽位回落 + fallback 事件 + value 保结构

探针 `/tmp/probe_t44.py` → **31 checks，0 fail**。

- 无槽位 `has_attribute` 候选经 HTTP 采纳后：`assertion.predicate == "has_story_fact"`，`object_json == {"name","note","type"}`（无空槽位）；`event_log` 有 `memory.assertion_predicate_fallback`，payload 为 `{"requested": "has_attribute", "used": "has_story_fact", "reason": "谓词缺少 attribute 槽位，已回落到结构化默认值"}`。
- 带槽位候选：`predicate == "has_attribute"`，`object_json.value` 保持 **dict 原结构**（`{"动作": "抵着窗扣", "手": "左手"}`），未被字符串化。
- 单测级返回值：`has_story_fact`（无槽位）、`has_attribute`（有槽位）、白名单外回落并回传 `rejected`；`located_at` 带槽位 → `has_attribute` + `rejected="located_at"`。
- 候选复用：原样重提 **200**、`canonical_unchanged == true`、无额外调用；改落库槽位后重提 **409**（改的是库里已落库断言，不改候选内容）。

## 五、槽位语义（引擎侧）

探针 `/tmp/probe_t27.py` 的 C 段（真实库 `analyze()`）+ `/tmp/probe_t48_extra.py`，全部通过。

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 同一实体 10 条互不矛盾事实 | 0 findings / 100 / A | 0 / 100.0 / A |
| 同一实体 5 个不同槽位 | 0 / 100 / A | 0 / 100.0 / A |
| 同槽位不同 `value` | 1 条 high / 92 / B，标题含槽位名 | 1 / high / 92.0 / B，标题含「属性「…」」 |
| 同槽位同值（`note` 不同） | 0 | 0 |
| `located_in` 两取值（对象/标量两种形状） | high | 1 条 high |
| 未见过谓词名 + `attribute` | 按槽位判 | 同槽位不同值 → 1 条 high，标题含槽位 |
| 两侧无槽位的 `has_attribute`（历史行） | 0 findings / 100 / A | 0 / 100.0 / A |
| 名字漂移（无槽位） | 1 条 high | 1 条 high，标题含「两个名字」 |
| 非对象一侧（标量） | high | 1 条 high |

## 六、文档

`docs/consistency-engine.md`（382 行）判定表共 **7 行**，逐行对 `_classify_object_conflict()` 真实分支顺序：

1. 任一侧非 JSON 对象 → high（对应 `not isinstance(...)` 分支）；
2. 至少一侧带非空 `attribute` 但槽位不同（含「有槽位 vs 未分类」）→ 跳过；
3. 同槽位、任一侧有 `value` 且取值不同 → high「属性「<attribute>」存在两个不同取值」；
4. 同槽位、同值或双方无 `value` → 继续名字漂移；
5. 两侧无槽位且 `_has_slot_semantics(predicate)` 为真（`has_attribute` / `has_story_fact`）→ 跳过配对；
6. 两侧无槽位且谓词不带槽位语义（`located_in` 等）→ high「`<predicate>` 存在冲突取值」；
7. 两侧 `name` 非空且不同 → high「同一实体被记成两个名字」。

与代码逐支一致（`_classify_object_conflict()` 的 `_same_value` → 非对象 → 槽位分支 → `_has_slot_semantics` → 单值谓词 → 名字漂移）；`note` 比较已删且文档明确写「`note` 不参与 fact 判定」；无裸行号（grep 无匹配）；`git diff --check` 干净。文档同时写清两道防线（写入端回落 + 引擎端历史行兜底）与 `_SLOT_SEMANTIC_PREDICATES` 的同步约定。

## 七、已知边界

1. 全部数据库场景都是临时合成 SQLite（固定几行实体/断言），**未跑真实稿件**；真实稿件上的 fallback 事件增长量与假阳率本轮不覆盖。
2. 无槽位历史行依赖引擎侧 `_has_slot_semantics()` 跳过；写入端与引擎端两份谓词名单靠人工同步（文档已把「漏改任一侧」列为已知限制 16），本轮只验证当前集合 `{has_attribute, has_story_fact}` 一致，未验证未来新增谓词的自动一致性。
3. `/tmp/probe_t27.py` 的 D 段两条断言仍按 t42 之前的 6 行表解析（标记缺「无槽位 + 槽位语义跳过」一行），因此该探针报 2 条 FAIL；这是探针自身过期，不是文档缺陷——本轮改用人工逐行核对 + `grep`（见第六节），文档实际 7 行且顺序正确。
4. `backend/moyu_continuation.py` 本轮未被 t41/t42/t47 触碰（hash 与前几轮相同），断点续写只做回归复现，不重复其设计评审。
5. 未覆盖前端、benchmark 数值与真实 provider 计费（本轮契约不含）。

## 八、结论与独立性声明

**Verdict：pass。** 六文件验收前后逐字节一致；四类事实全部用自己的探针在当前字节上复现；命令 1–9 全绿（含 92 个 `tests/*_fixtures.py` 0 FAIL）；未发现 high/blocker，因此不需要 needs_revision。

独立性：本轮所有 hash、命令输出、探针结果均由 verifier 自己运行采集，未引用实现者或 captain 给出的数字；未修改 `backend/`、`tests/`、`frontend/`、`benchmarks/` 任何文件，唯一写入是本报告。

字节绑定：本结论只对 `backend/server.py` `d16a964c…`、`backend/moyu_consistency.py` `a5a88b06…`、`backend/moyu_continuation.py` `583059fe…`、`tests/memory_assertion_predicate_fixtures.py` `f6eeff0d…`、`tests/consistency_fixtures.py` `05ef1cf6…`、`docs/consistency-engine.md` `1f409fe1…` 这一组字节有效；任一文件变动，本 verdict 即过期，需要重新绑定。
