# 独立验收：当前字节（断点续写 + 谓词 + 槽位语义 + 文档）

**日期**：2026-10-24
**任务**：t29（kind=verification，替代 t27 的「实现者自验」）
**验收人**：frontend-engineer —— 未参与 `backend/moyu_consistency.py`、`backend/moyu_continuation.py`、`backend/server.py` 本轮一致性/续写改动的实现，也未参与 t27 的探针编写。
**结论**：**pass**，附 2 条 low 级文档类 finding（不影响代码行为，见 §八）。

---

## 零、被测字节

两次采集：验收开始（跑任何命令之前）与验收结束（8 条命令 + A/B/C 三个探针 + 全量 sweep 之后）。采集命令 `shasum -a 256 <file>` 与 `wc -l <file>`；原始记录在 `/tmp/t29-hash-before.txt` 与 `/tmp/t29-hash-after.txt`。

| 文件 | sha256（开始 = 结束） | 行数 |
| --- | --- | --- |
| `backend/server.py` | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` | 8111 |
| `backend/moyu_consistency.py` | `39dee239a96ab9495659c9a3ca6f4f9fdedda85cf1c81178684203f25b55b732` | 1165 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 |
| `tests/consistency_fixtures.py` | `040303f5b452bd1a7f3e514baf6be6a04db1a86b8e4b55291b80b6195769e052` | 566 |
| `tests/consistency_http_fixtures.py` | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` | 199 |
| `docs/consistency-engine.md` | `8e7d324d033f89b0a988552a3b738f604c27f16befe0f940811fb80a55d23203` | 374 |

**判定：6/6 完全一致**（脚本逐字段比对输出 `ALL_IDENTICAL= True`）。本报告只对这六个字节负责；验收期间我只新增了本文件，未改动上表任何文件。

被验收的六字节与 t27 报告 §零 记录的六个 sha256 逐字相同——即 t27 之后到本次验收开始，实现没有再变动。

---

## 一、执行环境与方式

- 机器：macOS（darwin），仓库根 `/Users/zhaozhen/Downloads/神经网络写作`，解释器 `.venv/bin/python`（Python 3，venv）。
- macOS 无 GNU `timeout`，全部命令不使用 `timeout`；每条命令单独执行并记录退出码。
- 探针源码放在 `/tmp`，不进入仓库：
  - `/tmp/t29_probe_a.py`（直接驱动续写引擎，假 `generate` 记录调用次数与 prompt_context）
  - `/tmp/t29_probe_b.py`（直接调 `backend/server.py` 纯函数 + 临时 SQLite 验证落库与事件）
  - `/tmp/t29_probe_c.py`（直接调 `_classify_object_conflict` + `analyze()` 复现评分）
- 命令日志：`/tmp/t29-c1.log` … `/tmp/t29-c7.log`；全量 sweep：`/tmp/t29-sweep-results.txt`、`/tmp/t29-sweep-last.log`。
- 探针自建临时 SQLite（`tests/consistency_fixtures.setup()` 的 `:memory:` + schema），复用 `tests/consistency_fixtures.py` 的建表与数据构造，不修改仓库内任何测试。

---

## 二、命令层结果（必跑 8 条）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py` | 0 | 无输出（0.08s） |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.385s` / `OK` |
| 4 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `OK {'cases': 56}` |
| 5 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.692s` / `OK` |
| 6 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 3.006s` / `OK` |
| 7 | `.venv/bin/python tests/memory_assertion_predicate_fixtures.py` | 0 | `Ran 24 tests in 22.029s` / `OK` |
| 8 | 全量 sweep（逐文件执行 `tests/*_fixtures.py` 并记录每个退出码） | 0 | **92/92 退出码 0**，`=== SWEEP DONE in 646s ===`，零 FAIL |

第 8 条的实际执行形态（与任务要求的语义一致，但额外记录每个文件的退出码与耗时）：

```bash
: > /tmp/t29-sweep-results.txt; start_all=$(date +%s)
for f in tests/*_fixtures.py; do
  s=$(date +%s)
  .venv/bin/python "$f" >/tmp/t29-sweep-last.log 2>&1
  code=$?
  printf '%s\t%s\t%ss\n' "$code" "$f" "$(( $(date +%s)-s ))" >> /tmp/t29-sweep-results.txt
  if [ "$code" -ne 0 ]; then cp /tmp/t29-sweep-last.log "/tmp/t29-fail-$(basename "$f").log"; fi
done
printf '=== SWEEP DONE in %ss ===\n' "$(( $(date +%s)-start_all ))" >> /tmp/t29-sweep-results.txt
```

核对：结果文件 92 条数据行、非零退出码 **0 条**、`/tmp/t29-fail-*.log` **0 个**；总耗时 646 秒。`tests/*_fixtures.py` 当前正是 92 个文件。

另外单独复核了 t27 §6.2 提到的 `tests/story_review_publication_http_fixtures.py`：exit 0，`Ran 11 tests in 2.277s` / `OK`（与 t27 所称 11 一致）。

---

## 三、A 块：断点续写（引擎层，不经 HTTP）

探针直接调用 `backend.moyu_continuation.run_budgeted_continuation()`，传入会记录每次调用的假 `generate`。

**结果：17 checks / 0 fail，exit 0。**

| 场景 | 断言 | 实测 |
| --- | --- | --- |
| seed 3200 字，`target_characters=3000`（下限 2700） | provider 调用次数 | **0 次** |
| 同上 | `stop_reason` | `target_reached` |
| 同上 | `rounds` | `[]` |
| 同上 | `text == seed` 逐字 | True（未裁剪） |
| 同上 | `seed_characters` / `characters` | 3200 / 3200 |
| 同上 | `within_tolerance` | True |
| 同上 | `target_range(3000)` | `(2700, 3300)` |
| seed 250 字，`target_characters=1200`，`max_rounds=2` | 首轮 `prompt_context["previous_text"]` | 与 seed 逐字相等 |
| 同上 | `text.startswith(seed)` | True |
| 同上 | `characters >= seed_characters` | True |
| 不传 seed | 首轮 `prompt_context == {}` | True（旧行为不变） |
| 不传 seed | `seed_characters` | 0 |
| `seed_text=123` | 异常 | `ValueError`，消息含 `seed_text` |

核心调用形态（探针片段）：

```python
calls = []
def fake_generate(prompt, max_tokens):
    calls.append(prompt)
    return {"text": "补写正文。", "finish_reason": "stop"}

report = run_budgeted_continuation(
    fake_generate, target_characters=3000, max_rounds=3,
    min_round_gain=50, max_stall_retries=0,
    seed_text="已" * 3200,
)
# report["stop_reason"] == "target_reached"; report["rounds"] == []; calls == []
```

结论：种子的三条语义（只改起点、不裁剪、rounds 只统计新轮次）与「种子已达下限即零调用」在当前字节上成立。

---

## 四、B 块：写入端谓词与 `object_json` 形状

**结果：25 checks / 0 fail，exit 0。**

纯函数（`backend/server.py:4229-4302`）：

- 带 `attribute` 的候选 → `assertion_predicate` 返回 `("has_attribute", None)`；`assertion_object` 含 `attribute` 与 `value`，且保留 `name`/`type`/`note`。
- 无 `attribute` → `("has_story_fact", None)`，`object_json` 恰好 `{name,type,note}` 三键。
- 白名单外谓词 `located_at` → `("has_story_fact", "located_at")`；带 `attribute` 时 → `("has_attribute", "located_at")`。
- `ASSERTION_PREDICATES == {"has_story_fact","has_attribute"}`；`ASSERTION_PREDICATE_FALLBACK_EVENT == "memory.assertion_predicate_fallback"`。
- `same_json_value(1, 1.0) is True`、`same_json_value(True, 1) is False`、`same_json_value({"a":1},{"a":1.0}) is True`。

真实 SQLite（临时库，经 `ensure_memory_assertion()` 写入后直接查表）：

- `assertion.predicate == "has_attribute"`，`object_json` 含 `attribute`、`value` 与 `name`/`type`/`note`。
- 白名单外谓词 + `attribute`：落库谓词为 `has_attribute`，`event_log` 出现一条 `memory.assertion_predicate_fallback`，payload 为 `requested="located_at"`、`used="has_attribute"`、`reason` 含「白名单」。

探针过程中修过两处**探针自身**的问题（与字节无关，记录以便复算）：

1. `tests/consistency_fixtures.setup()` 的连接返回 tuple 行，最初按列名取值导致 3 条误判；改为 `row[0]`/`row[1]` 后通过。
2. `append_event()` 会 `UPDATE app_state_version`，而夹具 schema 不含该表；探针按 `backend/server.py:2905-2907` 的 DDL 自建并插入 `('default-project', 0)` 后通过。这同时反向验证了 `append_event()` 对 `app_state_version` 的依赖。

---

## 五、C 块：槽位语义（`_classify_object_conflict`）与评分复现

**结果：40 checks / 0 fail，exit 0。**

| 检查 | 期望 | 实测 |
| --- | --- | --- |
| 5 个不同槽位（home_city/age/weapon/rank/health）两两 10 组 | 全部 `(None,"","")` | 10/10 通过 |
| 同槽位、不同值 | `high`，标题含 `home_city`，detail 含两侧取值 | 通过 |
| 同槽位、同值（note 不同） | `(None,"","")` | 通过 |
| 一侧有槽位、另一侧只有 note | `(None,"","")` | 通过 |
| `located_in` 两个取值 | `high` | 通过 |
| 任一侧不是 JSON 对象 | `high`（在槽位判定之前） | 通过 |
| 名字漂移（林昭 vs 林朝） | `high`，标题「同一实体被记成两个名字」 | 通过 |
| 同名字、note 不同 | `(None,"","")` | 通过 |
| 数值/键序归一化 | 1/1.0、键序视为相同 | 通过 |
| `SEVERITY_WEIGHTS` / `_GRADE_THRESHOLDS` 常量 | 精确相等（float） | 通过（`_GRADE_THRESHOLDS` 为 tuple） |

`analyze()` 端到端复现（真实 SQLite + 夹具构造断言）：

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 同一实体 5 个不同槽位 | 0 findings / 100.0 / A | 一致 |
| 同槽位不同取值 | 1 条 high / 92.0 / B，标题含 home_city | 一致 |
| 同槽位同值 | 0 findings | 一致 |
| `located_in` 两取值 | 1 条 high | 一致 |
| 未知谓词 + 不同槽位 | 0 findings | 一致 |
| `has_story_fact` 无槽位对象 vs 字符串 | 1 条 high | 一致 |
| 名字漂移 | 1 条 high | 一致 |

评分链核对：`score = round(max(0.0, 100.0 - 8.0), 1) = 92.0`，`_grade(92.0)` = A，因有 high 降为 B——与 `_GRADE_THRESHOLDS`、封顶规则一致。

**一处需要写清的语义细节（不构成缺陷）**：`_detect_fact()` 先按 `(subject_entity_id, predicate, attribute_slot)` 分组（`backend/moyu_consistency.py:369-372`），非对象断言的槽位记为 `""`。因此「带 attribute 的对象 vs 字符串」永远不会被配对，`_classify_object_conflict()` 的非对象分支在 `analyze()` 中只有在两侧同为 `""` 槽位时才可达。文档 §1 正文已写明「不同槽位之间不比较」，已知局限第 3 条也写明「`attribute` 缺失记为 `""`，与有槽位的断言之间不比较」——文字上自洽；只是判定表第①行本身没重复这个前置条件（见 §八 F2）。

---

## 六、D 块：文档与仓库卫生

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 旧规则残留 | `grep -rn "两条不同描述\|互不包含\|note 不同" docs/` | **有 3 行匹配，均来自 t27 报告自身**：`docs/verification-consistency-current-bytes-2026-10-24.md:112`（C8 行）、`:126`（结果表引用）、`:192`（复现命令原文）。排除该报告后 0 匹配 |
| 裸行号引用 | `grep -rnE "(moyu_consistency\|server\|moyu_continuation)\.py:[0-9]+" docs/consistency-engine.md docs/consistency-engine-spec.md` | exit 1，无输出（无匹配） |
| 空白错误 | `git diff --check` | exit 0 |
| 判定表顺序 | 人工对照 `docs/consistency-engine.md:184-191` 与 `_classify_object_conflict()` | 6 行顺序逐行一致：非对象 → 槽位不同跳过 → 同槽位不同值 → 同槽位同值继续 → 无槽位且非 `has_story_fact` → 名字漂移 |
| `analyze()` 生产调用点 | `grep -rn "analyze(" backend/server.py backend/*.py` | 只有 2 处：`backend/server.py:4023`（`export_finalization_issues()`）与 `:6220`（`GET /api/consistency`），与文档「与续写引擎的关系」一致 |
| 已知局限第 14 条 | 对照 `ASSERTION_PREDICATES` 与 `ensure_memory_assertion()` | 一致：白名单两值、回落与事件名均正确 |

关于 D1：t27 报告声称该 grep「无匹配」。该命令**在写报告之后**复跑必然命中报告自身（报告里引用了这条命令和 `note 不同` 字样），这是自引用造成的，不是引擎文档残留。按「排除验收报告本身」的口径，引擎文档与 spec 均无残留。作为报告可复现性问题记入 §八 F1。

---

## 七、对 t27 报告（`docs/verification-consistency-current-bytes-2026-10-24.md`）的逐项复核

| t27 的断言 | 本次独立结果 | 判定 |
| --- | --- | --- |
| §零 6 个 sha256 | 与本次开始/结束完全一致 | 一致 |
| §一 命令 1-7 退出码 0 与关键输出 | 全部复现（测试耗时略有差异，属正常） | 一致 |
| §一 命令 8 全量零 FAIL | 92/92 exit 0、零 FAIL 日志、646s | 一致 |
| §二 A 块：零调用、target_reached、rounds=[]、text==seed、seed_characters=3200、within_tolerance=True | 全部复现（另加 target_range、characters 断言，共 17 checks） | 一致 |
| §二 A 块自述「首轮 5 条假红是 seed 写错」 | 无法从当前字节复现该错误路径，但修好后行为与我的探针一致 | 不冲突 |
| §三 B 块 12 checks（含 attribute/value 顶层陷阱） | 复现并扩展为 25 checks，含落库与 fallback 事件 payload | 一致 |
| §四 C 块 19 checks（含常量 float/tuple） | 复现并扩展为 40 checks + analyze 评分复现 | 一致 |
| §六 D 块「grep 无匹配」 | 复跑命中报告自身 3 行；排除后无匹配 | **部分不成立**（自引用，见 F1） |
| §六 D 块「判定表 6 行全部对应实际分支顺序」 | 逐行对照一致 | 一致 |
| §六.1 独立性降级（实现者自验） | 本报告即为独立复验，不依赖 t27 的探针代码 | 已补足 |
| §八 verdict=pass | 本次独立验收同样为 pass（附 2 条 low） | 一致 |

---

## 八、findings

| id | severity | problem | requiredFix |
| --- | --- | --- | --- |
| F1 | low | t27 §六 D 块称 `grep -rn "两条不同描述\|互不包含\|note 不同" docs/` 无匹配；在报告落盘后复跑必然命中报告自身（`:112`、`:126`、`:192`），命令按原样不可复现。引擎文档与 spec 本身无残留规则。 | 复现命令限定到 `docs/consistency-engine*.md`，或在报告中注明「排除验收报告自身」并给出实际命令。 |
| F2 | low | `docs/consistency-engine.md:186` 判定表第①行「任一侧不是 JSON 对象 → high」未写前置条件：`analyze()` 里该分支只有在两条断言同属 `(实体,谓词,"")` 分组时才可达，带槽位对象与非对象不会配对。正文 §1 与已知局限第 3 条已说明分组规则，表格单独阅读会误以为任意「对象 vs 字符串」都会报 high。 | 在第①行加脚注：「仅当两条断言落在同一 `(实体, 谓词, 槽位)` 分组时进入该分支；有槽位对象与非对象不同组，不会被配对。」 |

无 high、无 blocker；两条均为文档/报告表述精度问题，不改变代码行为，也不影响本 verdict。

---

## 九、Verdict

**pass**（content-bound：仅对 §零 所列 6 个 sha256 负责）。

**已知边界（未覆盖，不隐瞒）**：

1. 探针用假 `generate` / 临时 SQLite，未接真实模型；真实模型的字数分布与 `finish_reason` 组合未探测。
2. HTTP 端到端由 `tests/consistency_http_fixtures.py`（6 tests）与 `tests/story_review_publication_http_fixtures.py`（11 tests）覆盖，我执行了它们并看到全绿，但未逐条阅读每个用例的断言细节。
3. 未做并发/多分支压力验证；`analyze()` 的确定性由夹具覆盖，未额外重复运行 100 次比对 finding id。
4. 本次验收期间只写了本文件；§零 的 hash 前后一致即为「未改动被测字节」的证据。
