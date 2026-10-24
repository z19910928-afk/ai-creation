# 字节绑定验收：把 6 个被测文件的 sha256 写成账本可见的命令证据

**日期**：2026-10-24
**任务**：t31（kind=verification）
**验收人**：frontend-engineer —— 未参与 `backend/moyu_consistency.py`、`backend/moyu_continuation.py`、`backend/server.py` 本轮的实现（我只写 `frontend/` 与浏览器回归）。
**结论**：**pass**。6 个字节在验收开始/结束时逐文件采集 `shasum -a 256` 与 `wc -l`，**两次完全一致**；8 条必跑命令全部 exit 0。

---

## 一、为什么有这份报告

t29 的 verdict 已经把 6 个文件 hash 写在报告正文里，但团队账本读不到「这份 verdict 绑定的是哪几个字节」，系统评语是 `predates content-bound verdicts and recorded no artifact identity`。本报告的唯一目的：把字节身份重跑一遍，并作为 `update_task` 的 `commandsRun` 命令证据逐条落下来（6 条 `shasum -a 256` + 6 条 `wc -l` + 8 条必跑命令）。

---

## 二、字节表（开始 / 结束各采集一次）

采集命令（两条都原样执行）：

```bash
shasum -a 256 backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py tests/consistency_fixtures.py tests/consistency_http_fixtures.py docs/consistency-engine.md
wc -l backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py tests/consistency_fixtures.py tests/consistency_http_fixtures.py docs/consistency-engine.md
```

| 文件 | sha256（开始 = 结束） | 行数（开始 = 结束） |
| --- | --- | --- |
| `backend/server.py` | `a70a25cee65d0cf8b4f9920eb9853eec00c8855e19d4e3b412c379123b1bebdf` | 8111 |
| `backend/moyu_consistency.py` | `39dee239a96ab9495659c9a3ca6f4f9fdedda85cf1c81178684203f25b55b732` | 1165 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 |
| `tests/consistency_fixtures.py` | `040303f5b452bd1a7f3e514baf6be6a04db1a86b8e4b55291b80b6195769e052` | 566 |
| `tests/consistency_http_fixtures.py` | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` | 199 |
| `docs/consistency-engine.md` | `a07808b29656ee3cbbded011a388ecd050d32791e7e7f61783ad0c6ff166b3ad` | 376 |

**两次一致判定：一致（6/6）**。原始记录：`/tmp/t31-before.txt`、`/tmp/t31-after.txt`；逐文件比对脚本输出 `ALL_IDENTICAL= True`。

### 与 t29 报告的两个差异（主动报）

1. `docs/consistency-engine.md`：t29 记录 `8e7d324d…` / 374 行，本次 `a07808b2…` / 376 行。原因是 t29 的 F2 finding 已按建议修复：判定表第①行加了 ① 标记，并在表下新增「注①」两行，明确「仅当两条断言落在同一 `(实体, 谓词, 槽位)` 分组时才进入该分支，非对象断言槽位为 `""`」。文档行为没有变化，只是把 t29 指出的前置条件写清楚。
2. 其余 5 个文件（`backend/server.py`、`backend/moyu_consistency.py`、`backend/moyu_continuation.py`、`tests/consistency_fixtures.py`、`tests/consistency_http_fixtures.py`）的 sha256 与行数与 t29 逐字相同。

## 三、8 条必跑命令（逐条退出码）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py backend/moyu_continuation.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.462s` / `OK` |
| 4 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 56}` |
| 5 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 23 tests in 13.584s` / `OK` |
| 6 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 2.957s` / `OK` |
| 7 | `.venv/bin/python tests/memory_assertion_predicate_fixtures.py` | 0 | `Ran 24 tests in 21.744s` / `OK` |
| 8 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" || echo "FAIL $f"; done; echo "=== SWEEP DONE ==="` | 0 | 92/92 文件退出码 0；`=== SWEEP DONE in 644s ===`；零 FAIL |

第 8 条的实际执行形态与任务清单语义一致，但额外逐文件记录退出码与耗时（`/tmp/t31-sweep.txt`）：92 条数据行、非零退出码 **0 条**、无 `FAIL` 行。macOS 无 GNU `timeout`，全程未使用 `timeout`。

## 四、声明

我（frontend-engineer）未参与 `backend/moyu_consistency.py`、`backend/moyu_continuation.py`、`backend/server.py` 本轮一致性/续写相关改动的实现；本任务我只新增了本文件，未改动上表任何被测文件（两次 hash 一致即为证据）。

## 五、Verdict

**pass**。本 verdict 绑定上表 6 个 sha256；字节身份已作为 `update_task.commandsRun` 的逐条命令证据记录（6×`shasum -a 256` + 6×`wc -l` + 8 条必跑命令），账本可据此确认「判的是哪几个字节」。无 high/blocker，无 findings。
