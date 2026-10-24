# 墨屿一致性引擎 v1 独立验证报告（后端阶段）

- 报告日期：2026-10-23（Asia/Shanghai）
- 验证人：verifier（独立验证；只认命令输出，不采信口头结论）
- 仓库：`/Users/zhaozhen/Downloads/神经网络写作`
- 验证范围：**后端阶段** —— `backend/moyu_consistency.py`、`backend/server.py` 的 `/api/consistency` 分支、两条一致性 fixture、既有 fixture 回归、报告只读性复核。
- 范围修正：按 captain 2026-10-23 15:32 的指令，本轮**不验证**前端面板、LongFormBench、许可证与引擎文档（三条工作流并行开发中，尚未交付），见第 6 节「未覆盖」。
- **第一轮结论：needs_revision**（核心契约全部通过，但静态审查 + 探针复现出 4 个可复现缺陷，其中 2 个已证明会传导到发表导出链路；详见第 4 节）
- **第二轮修复复核（2026-10-23 15:51）**：P1–P10 修复已验证，见第 10 节；第二轮结论 **needs_revision**（仅 P5 单条坏 JSON 边界残留，详见 10.6）。
- **第三轮最终复核（2026-10-23 15:58）**：P11–P14 修复已验证，P16 已验证，P15 记录为已知边界；**最终结论 pass（后端阶段）**，见第 11 节。

---

## 1. 验证基线

本轮所有命令均在下表基线上复跑。验证期间仓库被其他成员并发修改（见第 7 节），若文件再次变化，本报告结论需重新验证。

| 文件 | 大小 (bytes) | mtime | sha256 |
|---|---:|---|---|
| `backend/moyu_consistency.py` | 43197 | 2026-10-23 15:30:07 | `b5e56d52acccb32bb718e24cc6a4db30825eb622d9a5c5500f264dba0e9bfb4f` |
| `backend/server.py` | 475195 | 2026-10-23 15:35:32 | `c2133279d24ceeb466d56e63d6977ecb8bcde30a9ce5a327ee0a633b890b0492` |
| `tests/consistency_fixtures.py` | 14348 | 2026-10-23 15:29:28 | `7ffb4f1873df81dd48e186e3107562f4b0b8ded89707f51d1578e123d90c18ac` |
| `tests/consistency_http_fixtures.py` | 9219 | 2026-10-23 15:36:21 | `4eb2a62b4dcec226516e5844ba0ccba7fce5962ffa84a5ef8bd971628ded0071` |

运行环境：`.venv/bin/python --version` → `Python 3.11.17`。

---

## 2. 必跑命令与结果

### 2.1 引擎 fixture

```bash
.venv/bin/python tests/consistency_fixtures.py
```

- 退出码：**0**
- 关键输出：

```text
consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}
```

覆盖内容（读 fixture 确认）：六类矛盾 + evidence 共 7 类检测、finding id 唯一性与确定性、按 severity 排序、`min_severity` 只裁剪清单不美化分数、干净项目 100 分 A、`render_text` 输出。

### 2.2 HTTP fixture

```bash
.venv/bin/python tests/consistency_http_fixtures.py
```

- 退出码：**0**
- 关键输出：

```text
test_bad_filters_are_rejected ... ok
test_contradiction_is_reported_and_lowers_score ... ok
test_contradiction_reaches_publication_gate ... ok
test_min_severity_filters_without_inflating_score ... ok
test_report_contract ... ok
test_report_is_read_only ... ok
----------------------------------------------------------------------
Ran 6 tests in 3.328s

OK
```

（注：验证开始时该文件为 5 个测试；15:36:21 被并发追加了 `test_contradiction_reaches_publication_gate`，最终基线为 6 个，全部通过。）

### 2.3 语法编译检查

```bash
.venv/bin/python -m py_compile backend/moyu_consistency.py backend/server.py \
  tests/consistency_fixtures.py tests/consistency_http_fixtures.py
```

- 退出码：**0**，无输出（`py_compile: OK`）。

### 2.4 既有 fixture 回归（抽样 5 个）

| # | 命令 | 退出码 | 关键输出 |
|---|---|---|---|
| 1 | `.venv/bin/python tests/assertion_workflow_fixtures.py` | 0 | `assertion workflow fixtures: ok` |
| 2 | `.venv/bin/python tests/character_state_fixtures.py` | 0 | `character state fixtures: ok` |
| 3 | `.venv/bin/python tests/chapter_commit_fixtures.py` | 0 | `chapter commit fixtures: ok` |
| 4 | `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests in 1.804s` / `OK` |
| 5 | `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests in 2.442s` / `OK` |

第 4、5 个是发表/导出链路回归，专门用于确认 15:35 新增的「一致性门禁接入 `export_finalization_issues`」没有破坏既有发表流程——实测 20 个测试全部通过。

### 2.5 报告只读复核（GET 前后表行数不变）

- `tests/consistency_http_fixtures.py::test_report_is_read_only` → **ok**。
- 独立探针（`/tmp/probe_a2.py`，临时库，不写仓库）在 HTTP 层复核：

```text
-- before init_db: db exists? False
-- after init_db: {'project': 1, 'branch': 1, 'actor': 1, 'document_node': 1, 'app_project_meta': 1}
-- after FIRST GET: {'project': 1, 'branch': 1, 'actor': 1, 'document_node': 1, 'app_project_meta': 1} | score: 100.0
-- after SECOND GET: {'project': 1, 'branch': 1, 'actor': 1, 'document_node': 1, 'app_project_meta': 1}
```

**结论：数据层面成立。** 说明：`init_db()` 在启动时已调用 `ensure_seed()`（`backend/server.py:2409`），所以首次 GET 时 seed 已存在；`/api/consistency` 不在 `read_only_workspace` 白名单（`backend/server.py:5399-5409`），每次 GET 仍会执行 `ensure_seed` 的 `INSERT OR IGNORE` 语句，但实测行数不变。若要求严格只读（不执行任何写语句），建议把该路径加入白名单（观察项，非缺陷）。

---

## 3. 静态审查结论

### 3.1 语法

`py_compile` 通过（见 2.3）。引擎与 `/api/consistency` 分支无语法错误。

### 3.2 只读性

- 对 `backend/moyu_consistency.py` 全文检索 `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM|ATTACH`：**0 处**。模块只执行 `SELECT` / `PRAGMA table_info` / `sqlite_master` 查询，无 `commit()`。
- `/api/consistency` 的 400 分支不写库；正常分支只调用 `analyze()`。
- 数据层面 GET 前后行数不变（2.5 实测）。

### 3.3 异常路径

- `min_severity` 非法值、重复参数 → 400，测试覆盖（`test_bad_filters_are_rejected`）。
- 引擎内部 `ValueError` 会被路由的 `except ValueError`（`backend/server.py:5422`）误映射为 400 —— 见问题 P4。
- 引擎内部 `AttributeError` 等未捕获异常 → `do_GET` 外层 `except Exception` → 500 —— 见问题 P1。
- 缺表降级：仅 v1 schema（无 `app_*` 表）时 `analyze()` 正常返回 100 分、7 类 examined 全 0，不崩溃（探针 E1）。**符合模块 docstring 的「可降级」约束。**

### 3.4 分数语义

- 实现：`score = round(max(0.0, 100.0 - Σ SEVERITY_WEIGHTS), 1)`（`backend/moyu_consistency.py:857-858`）；权重 `blocker=20 / high=8 / medium=3 / low=1`（第 28 行）；等级阈值 `90→A / 75→B / 60→C / else D`（第 44 行）。
- `min_severity` 只裁剪 `findings` 清单，`counts` / `score` / `categories` 永远基于全量（第 851-877 行）——「筛选不得美化分数」成立，测试覆盖。
- 分数与筛选的交互在 HTTP 层同样成立（`test_min_severity_filters_without_inflating_score`）。
- 观察：`total_examined` 是各检测器计数之和，同一 assertion 会被 fact/knowledge/timeline/evidence 重复计数（问题 P6）；单条 high 扣 8 分 → 92 分 → 仍为 A（问题 P9）；文档公式与实现不一致（问题 P8）。

---

## 4. 问题清单（按严重度）

### P1【高】`knowledge_scope` 类型畸形会让整份报告崩溃，并传导到导出预览

- 位置：`backend/moyu_consistency.py:456`

```python
is_private = bool(isinstance(private, dict) and (private.get("private") or private.get("knowledge_scope", {}).get("private")))
```

- 复现：`candidate_memory.payload_json = {"known_by": ["乙"], "knowledge_scope": "全局"}`（`knowledge_scope` 为字符串），并让一条 canon assertion 的 evidence 引用该 `candidate_memory_id`：

```text
CRASH: AttributeError : 'str' object has no attribute 'get'
```

- HTTP 层：`GET /api/consistency` → **500**；`server.export_finalization_issues(conn)` 同样抛出 `AttributeError`（导出预览/导出接口 500）。
- 影响：单条畸形 AI 候选记忆（AI 生成 JSON 时把对象写成字符串是现实风险）即可让一致性报告和发表导出链路整体不可用。`export_finalization_issues` 在 `backend/server.py:3313-3321` 直接调用同一函数，缺陷被放大到导出链路。
- 建议修法：

```python
scope = private.get("knowledge_scope")
scope = scope if isinstance(scope, dict) else {}
is_private = bool(private.get("private") or scope.get("private"))
```

并给单条 finding 的判定加 `try/except` 降级（记录数据问题而不是整份报告崩溃）。

### P2【高】语义相同、仅 JSON 空白不同的两条断言被误报为 high 事实矛盾，并**阻断导出**

- 位置：`backend/moyu_consistency.py:260`（`if left["object_json"] == right["object_json"]: continue` 用原始字符串比较）+ `backend/moyu_consistency.py:207`（`predicate != "has_story_fact"` 时无条件返回 high 冲突，未做 canonical 比较）
- 复现：同一实体、同一谓词、重叠有效期，两条 canon 断言：

```text
object_json = '{"value": "青云城"}'   # 带空格
object_json = '{"value":"青云城"}'    # 不带空格
```

输出：

```text
score: 92.0
findings: [('fact', 'high', 'located_in 存在冲突取值',
            '乙：结构化断言取值不一致：{"value":"青云城"} vs {"value":"青云城"}。')]
```

- 下游影响（实测）：`server.export_finalization_issues(conn)` 返回

```text
一致性阻断：located_in 存在冲突取值——新人物：结构化断言取值不一致：{"value":"青云城"} vs {"value":"青云城"}。
```

`/api/export/epub|markdown` 会因此返回 409 `export blocked by finalization gate`（`backend/server.py:5777-5780`）。
- 影响：导入数据或不同序列化来源的等价断言会被误判为矛盾，并在发表前被错误阻断；作者只能靠 override 放行。
- 建议修法：比较 `_canonical(_loads(left["object_json"], None))` 与右侧；在 `_classify_object_conflict` 开头增加 `if _canonical(left) == _canonical(right): return None, "", ""`；补一条「空白差异不报错」的 fixture。

### P3【中】CLI `--fail-on` 被 `--min-severity` 绕过，CI 门禁可假绿

- 位置：`backend/moyu_consistency.py:990-993`

```python
if args.fail_on:
    cutoff = SEVERITY_ORDER[args.fail_on]
    if any(SEVERITY_ORDER[item["severity"]] <= cutoff for item in report["findings"]):
        return 1
```

`report["findings"]` 是**已被 `min_severity` 裁剪**的清单。
- 复现（dirty fixture 落盘为临时库）：

```text
--- plain --fail-on high ---                         exit: 1   （正确）
--- filtered --min-severity blocker --fail-on high --- exit: 0 （错误：库中存在 high 问题）
```

- 影响：CI 同时使用 `--min-severity` 与 `--fail-on` 时门禁失效，高危问题被静默放过。
- 建议修法：在裁剪前保留全量清单（如 `all_findings = findings`），`--fail-on` 用全量判断；补 CLI 回归测试。

### P4【中】内部 `ValueError` 被 `/api/consistency` 误映射为 HTTP 400

- 位置：`backend/server.py:5412-5422`（`except ValueError` 覆盖整个 `analyze()` 调用）+ `backend/moyu_consistency.py:325`（`int(row["ordinal"])`）
- 复现：`app_timeline_event.ordinal = 'abc'`（SQLite 允许 TEXT 写入 INTEGER 列）：

```text
E2-HTTP ordinal=abc: HTTP 400 {"error": "invalid literal for int() with base 10: 'abc'"}
```

- 影响：服务端数据问题被报告成客户端请求错误，调用方无法区分；同时把内部异常文本原样返回。
- 建议修法：把 `try/except ValueError` 收窄到「`min_severity` 校验」这一小段（校验通过后再调用 `analyze`），其余异常交给外层 500；`ordinal` 转换加 `try/except (TypeError, ValueError)` 并跳过/单独报告数据问题。

### P5【低】`object_json` 非法 JSON 被当作 `{}` 参与冲突判定，产生误报

- 位置：`backend/moyu_consistency.py:207,260`
- 复现：一条 `object_json='{"value":"A"}'`，另一条 `object_json='not-json'`：

```text
score: 92.0
findings: [('fact', 'high', 'located_in 存在冲突取值', '甲：结构化断言取值不一致：{"value":"A"} vs {}。')]
```

- 影响：数据损坏被伪装成业务矛盾，且 severity 为 high。
- 建议修法：`_loads` 失败时区分「解析失败」，跳过该对或单独产出「数据损坏」类 finding。

### P6【低】`total_examined` / `summary` 重复计数，语义误导

- 位置：`backend/moyu_consistency.py:877-886`
- 复现（dirty fixture）：

```text
examined per detector: {'fact': 8, 'timeline': 6, 'knowledge': 8, 'attribute': 3,
                        'relation': 4, 'foreshadow': 5, 'evidence': 8}  sum: 42
summary: 一致性分数 13.0（D）：检查 42 条记录，发现 17 个问题（阻断 0、高 8、中 7、低 2）。
```

同一批 8 条 assertion 被 fact / knowledge / timeline / evidence 四个检测器重复计数，实际记录数远小于 42。
- 建议修法：改称「检查项次」，或按去重后的记录数统计并在文档中说明口径。

### P7【低】不存在的 project/branch 返回 100 分 A

- 位置：`backend/moyu_consistency.py:829`（`analyze` 未校验 project/branch 是否存在）
- 复现：

```text
score: 100.0 grade: A total_examined: 0 summary: 一致性分数 100（A）：检查 0 条记录，未发现矛盾。
```

- 影响：引擎层会给出误导性的满分；HTTP 层因 `scoped_request` 对 `X-Moyu-Project` 做 `resolve_scope` 校验（`backend/server.py:135-150`，不存在 → 404），当前不可从 HTTP 触达。
- 建议修法：`analyze` 内校验 project/branch 存在，不存在时抛 `ValueError` 或返回 `valid: false` 的报告。

### P8【低】分数公式文档与实现不一致

- 文档：`docs/strategy-beat-novelforge-2026-10-23.md:103` 写的是

```text
一致性分数：100 × (1 - 未解决 blocker 加权数 / 已确认断言数)
```

- 实现：`backend/moyu_consistency.py:28,857-858` 是绝对扣分 `100 - Σ(severity 权重)`，且分母不是断言数。
- 影响：对外口径与实现不一致，验收/宣传/引擎文档会各说各话。
- 建议修法：统一口径。若以实现为准，引擎文档写明权重表、阈值与「绝对扣分」语义；若以文档为准，改实现并补测试。

### P9【低】grade A 可与 high 级矛盾共存

- 位置：`backend/moyu_consistency.py:44,858,861-862`
- 复现：P2 场景 score 92.0 → `grade` 为 `A`，但报告中存在 high 级事实矛盾。
- 影响：若 grade 被 UI 当作质量信号，会出现「A 级但发表被阻断」的矛盾体验。
- 建议修法：按最高 severity 设上限（如存在 blocker → 最高 C，存在 high → 最高 B），或在文档中明确 grade 只是分数分段。

### P10【提示】`CORE_CATEGORIES` 定义未使用

- 位置：`backend/moyu_consistency.py:41`
- `grep` 仅命中定义本身，属于死代码；建议删除或实际用于核心 6 类统计。

---

## 5. 已排除的假设（避免误报）

| 假设 | 验证方式 | 结论 |
|---|---|---|
| `/api/consistency` 用相对导入 `from .moyu_consistency`，脚本启动会 ImportError | 读入口：根 `server.py` 以 `from backend import server as service` 包导入；`python backend/server.py` 在模块导入阶段就会因 `from . import moyu_workspace` 失败，本就不是受支持入口 | **不成立** |
| 首次 GET 会因 `ensure_seed` 写库，导致「GET 前后行数不变」不成立 | 探针 A2：`init_db()` 末尾已调用 `ensure_seed`（`backend/server.py:2409`）；首次/第二次 GET 前后行数完全一致 | **不成立**（数据不变；仅执行了 `INSERT OR IGNORE` 写语句，见观察项） |
| 缺少 `app_*` 表时引擎会崩溃 | 探针 E1：仅 v1 schema 时 `analyze` 正常返回 100 分、examined 全 0 | **不成立**（降级正常） |
| `assertion.evidence_json` 非法 JSON 会让 `json_each` 崩溃 | 尝试写入非法值被 DB trigger `check_assertion_canon_insert/update`（`docs/schema-v1.1-migration.sql`）直接 ABORT：`canon evidence and reviewer required` | **不成立**（DB 约束兜底） |
| `candidate_memory.payload_json` 非法 JSON 会崩溃 | 探针 E6：`_loads` 返回 `{}`，`_known_by_of` 返回 `[]`，跳过该行，不崩溃 | **不成立** |

---

## 6. 未覆盖（并行开发中）

按 captain 的范围修正，以下三项**不纳入本轮判定**，统一记为「未覆盖（并行开发中）」：

| 工作流 | 状态（验证时） | 说明 |
|---|---|---|
| 前端一致性面板 | `frontend/consistency-panel.js` / `.css` 于 15:37:28 / 15:38:02 出现；`frontend/index.html` 被修改；`backend/server.py:5224-5228` 已加入 `STATIC_FILES` | 未做 `node --check` / 静态审查 / 浏览器联调；待全链路验收任务 |
| LongFormBench | `benchmarks/` 目录出现，但 `benchmarks/longform/run.py` 在验证时**不存在**；`.gitignore` 新增 `benchmarks/longform/.work/` | 未运行任何 benchmark 参数；无法给出基线 |
| 许可证与引擎文档 | `docs/consistency-*.md` 在验证时**不存在**；`LICENSE` 未核对 | 未验证 |

---

## 7. 风险与观察

1. **验证期间仓库被并发修改（重要）**：
   - `backend/server.py` 15:35:32 被修改：新增 `export_finalization_issues` 对一致性引擎的调用（`backend/server.py:3313-3321`），以及 `STATIC_FILES` 增加前端面板资源（`backend/server.py:5224-5228`）。
   - `tests/consistency_http_fixtures.py` 15:36:21 新增 `test_contradiction_reaches_publication_gate`。
   - `frontend/consistency-panel.js|css` 15:37/15:38 出现；`frontend/index.html` 被修改；`benchmarks/` 目录出现。
   - 本报告所有命令均在最终基线（第 1 节 hash）上复跑；并发修改意味着**结论只对基线负责**。建议 captain 在合并前冻结后端文件并重跑本报告第 2 节。
2. **新增导出联动放大了 P1/P2**：`export_finalization_issues` 在导出预览/导出时同步调用一致性引擎，P1 会让导出 500，P2 会让导出 409。修复 P1/P2 前不建议对外发布「一致性门禁」能力。
3. `/api/consistency` 每次 GET 都执行 `ensure_seed` 的 `INSERT OR IGNORE` 写语句（数据不变）。若要严格只读语义，可把该路径加入 `read_only_workspace` 白名单（`backend/server.py:5399-5409`）。
4. `test_report_is_read_only` 的注释「让 seed 先落库」在当前实现下已被 `init_db()` 的 seed 覆盖，注释略过时（仅文档问题）。
5. 新增的发表门禁只拦 `fact / knowledge / evidence` 的 blocker/high（`backend/server.py:3316-3320`），时间线/属性/关系/伏笔仍为提示级——与注释一致，属产品取舍，未判为缺陷。

---

## 8. 复现方式汇总

以下探针均使用临时数据库，不修改仓库。可复制到 `/tmp` 下执行（`ROOT` 指向仓库根）。

**P1（knowledge_scope 崩溃）**

```python
import importlib.util, json, sys
from pathlib import Path
ROOT = Path("/Users/zhaozhen/Downloads/神经网络写作"); sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("cfx", ROOT/"tests/consistency_fixtures.py")
cfx = importlib.util.module_from_spec(spec); spec.loader.exec_module(cfx)
conn = cfx.setup(); cfx.add_project(conn, "pb", "bb"); cfx.add_chapters(conn, "pb", "bb", 2)
cfx.add_revision(conn, "n1", "rb1", "bb", "测试。"); cfx.add_span(conn, "sb1", "pb", "rb1", "n1", "测试。")
conn.execute("INSERT INTO entity VALUES('eb1','pb','character','甲','[]',NULL,'active')")
conn.execute("INSERT INTO candidate_memory VALUES('cmb','pb','bb','sb1','eb1','人物',?,'accepted',0.9,?,?,'作者')",
             (json.dumps({"known_by": ["乙"], "knowledge_scope": "全局"}, ensure_ascii=False), cfx.T, cfx.T))
cfx.add_assertion(conn, "ab1", "pb", "bb", "eb1", "has_story_fact", {"name": "甲"},
                  evidence=[{"candidate_memory_id": "cmb"}])
cfx.analyze(conn, "pb", "bb")   # AttributeError: 'str' object has no attribute 'get'
```

**P2（JSON 空白误报 + 导出阻断）**：两条同实体/同谓词 canon 断言，`object_json` 分别为 `'{"value": "青云城"}'` 与 `'{"value":"青云城"}'`，`analyze()` 得到 score 92 + high fact finding；再调用 `server.export_finalization_issues(conn)` 得到「一致性阻断」。

**P3（CLI 门禁绕过）**：把 `tests/consistency_fixtures.py` 的 `seed_dirty` 写入临时 SQLite 文件后执行：

```bash
.venv/bin/python backend/moyu_consistency.py --db /tmp/book.sqlite3 --project p1 --branch b1 --fail-on high
# → exit 1
.venv/bin/python backend/moyu_consistency.py --db /tmp/book.sqlite3 --project p1 --branch b1 \
  --min-severity blocker --fail-on high
# → exit 0（错误：库中存在 high 问题）
```

**P4（内部 ValueError → 400）**：向临时库 `app_timeline_event` 插入 `ordinal='abc'` 的行，`GET /api/consistency` → `HTTP 400 {"error": "invalid literal for int() with base 10: 'abc'"}`。

**原始日志**：`/tmp/moyu-verify/`（`consistency_fixtures.log`、`consistency_http_fixtures.log`、`probes.log`、`readonly.log`、`degrade.log`）；该目录为验证机临时目录，不入库。

---

## 9. 给 captain 的处理建议

1. **P1 / P2 建议作为发布前必修**（崩溃 + 误阻断导出，且被新增导出联动放大）。
2. P3 / P4 建议本轮一并修（改动小，影响 CI 门禁与错误语义）。
3. P5–P9 可排入 v1.1；若时间紧，至少在引擎文档中写明已知边界。
4. 修复后请冻结 `backend/moyu_consistency.py`、`backend/server.py`、两个 consistency fixture，再重跑第 2 节命令；本报告可作为回归基线。
5. 全链路验收（前端面板 / LongFormBench / 许可证与文档）待三条工作流交付后另开任务。

---

## 10. 第二轮：修复复核（2026-10-23 15:51，后端已冻结）

### 10.1 复核范围与基线

captain 声明 P1–P10 已修复并冻结后端。本轮：逐条复跑首轮 P1–P10 探针（临时库，不写仓库）；专项核对 P2 规范化漏报风险、P7 scope 校验对既有 fixture 的影响、P9 grade 封顶与 HTTP fixture 的兼容性；重跑 4 条测试命令 + LongFormBench 40 章基线。

复核基线（后端文件在复核期间未变化）：

| 文件 | 大小 (bytes) | mtime | sha256 |
|---|---:|---|---|
| `backend/moyu_consistency.py` | 47229 | 2026-10-23 15:43:39 | `5a2f0f9f3b2019045add9d46ec6f325919593f2a660be2a262a454d3603df4d8` |
| `backend/server.py` | 475569 | 2026-10-23 15:42:28 | `4abd03b8e378c1ad6ed053536899a6e3757e8eebeef6e55cf0846ecf27641f89` |
| `tests/consistency_fixtures.py` | 14721 | 2026-10-23 15:44:24 | `60ec3cf742c41342b182c3a723b487ad8210e2c955c0f86e2c3c8022769df92f` |
| `tests/consistency_http_fixtures.py` | 9270 | 2026-10-23 15:42:55 | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` |
| `benchmarks/longform/run.py` | 29658 | 2026-10-23 15:49:05 | `05a08274d76dc374328601486ead48e61dd23305968ac27033f57bd0c29e9cea` |

注：首轮 benchmark 启动时 `run.py` 为 15:47:29 版本（sha256 `9708d911…`），运行结束后 15:49:05 被 benchmark 成员并发更新；已用最终版本 `05a08274…` 重跑，两次结果一致（见 10.2）。探针脚本：`/tmp/moyu-verify2/probes2.py`、`probes3.py`。

### 10.2 必跑命令与结果

| 命令 | 退出码 | 关键输出 |
|---|---:|---|
| `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.312s` / `OK` |
| `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests in 2.352s` / `OK` |
| `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests in 1.778s` / `OK` |
| `.venv/bin/python benchmarks/longform/run.py --chapters 40 --seed 20261023` | 0 | `结论：PASS`；105,182 汉字；35/35 检出；精确率 100%；干净基线 0 findings；中位耗时 2.54 ms |

### 10.3 P1–P10 逐条复核

| 编号 | 首轮问题 | 第二轮探针结果 | 判定 |
|---|---|---|---|
| P1 | knowledge_scope 非 dict 崩溃并传导导出 500 | payload `{"known_by":["乙"],"knowledge_scope":"全局"}` + canon 断言：`analyze` 返回 score 100 / 0 findings；`server.export_finalization_issues` 返回 `[]`，无异常 | ✅ 已修复（残留观察见 10.5） |
| P2 | 仅 JSON 空白不同被误报 high 并阻断导出 | 空白差异 / key 顺序差异 → 0 fact、100 A；真正不同 → high、92 B；导出联动：空白差异无「一致性阻断」、真正不同有阻断 | ✅ 已修复（数值边界见 P13） |
| P3 | `--fail-on` 被 `--min-severity` 绕过 | `--fail-on high` → exit 1；`--min-severity blocker --fail-on high` → exit 1；`--fail-on blocker` → exit 0 | ✅ 已修复 |
| P4 | 内部 ValueError 误映射 400 | `ordinal='abc'` → GET 200 + timeline medium「时间线事件的序位不是整数」；非法/重复筛选 400；ghost project/branch header 404 | ✅ 已修复（CLI/宽泛 catch 见 P14/P15） |
| P5 | 非法 object_json 伪装成 high 业务矛盾 | 坏 JSON + 合法断言 → medium「断言取值不是合法 JSON」、score 97；**单条坏 JSON → 0 findings**（P11）；1 坏 + 2 合法 → 2 条重复 medium（P12） | ⚠️ 部分修复 |
| P6 | total_examined 重复计数、口径误导 | summary：`一致性分数 13.0（D）：核对 42 项次（同一记录会被多类检测重复核对），发现 17 个问题…` | ✅ 已修复 |
| P7 | 不存在的 project/branch 返回 100 分 A | `analyze` 抛 `ConsistencyScopeError: 项目不存在：ghost`；HTTP ghost project/branch → 404；既有 fixture 全过 | ✅ 已修复（CLI 见 P14） |
| P8 | 文档公式与实现不一致 | `docs/strategy-beat-novelforge-2026-10-23.md:103` 已改为 `max(0, 100 - Σ 权重)`，写明 blocker=20/high=8/medium=3/low=1 与等级封顶 | ✅ 已修复 |
| P9 | grade A 可与 high 共存 | high-only：92 → B；blocker：80 → C（counts blocker=1）；HTTP fixture 6 tests OK 无冲突 | ✅ 已修复 |
| P10 | `CORE_CATEGORIES` 死代码 | `grep CORE_CATEGORIES` 在引擎中 0 次出现 | ✅ 已修复 |

### 10.4 修复未引入回归的专项核对

1. **P2 规范化不会漏报真正不同取值**：`{"value":"青云城"}` vs `{"value":"西域"}` → high（92 B）；`{"value":{"a":1}}` vs `{"value":{"a":2}}` → high；数组顺序不同 `[1,2]` vs `[2,1]` → high（数组有序，正确）；嵌套 key 顺序不同 → 100 A（正确）。唯一残留：`{"value":1}` vs `{"value":1.0}` 仍报 high（P13）。
2. **P7 scope 校验未让既有 fixture 假失败**：`consistency_fixtures`（含修复后的 `seed_clean` p2）、`consistency_http_fixtures`、`story_review_publication_http_fixtures`、`publication_assistant_fixtures` 全部通过。
3. **P9 grade 封顶与 HTTP fixture 兼容**：fixture 只断言 `grade ∈ {A,B,C,D}`，6 个 HTTP 测试全过。
4. **P1/P2 导出联动**：畸形 knowledge_scope 不再抛异常；空白差异对无「一致性阻断」；真正不同对产生「一致性阻断」。
5. **all_findings 过滤语义**：dirty 库 `min_severity=blocker` → `findings=0` / `all_findings=17` / `score=13.0 D`，筛选不美化分数成立；但 HTTP 响应也带全量 `all_findings`（P16）。

### 10.5 第二轮新发现问题

#### P11【中】单条非法 object_json 静默漏报

- 位置：`backend/moyu_consistency.py:277-307`（`_detect_fact` 只在成对循环里检查 `_parse_object_json`）
- 复现：单条 canon assertion `object_json='not-json'`（无同实体同谓词的第二条）→ `analyze` 返回 `findings=[]`、`score=100.0`、`grade=A`，无任何提示。
- 影响：数据损坏静默通过；P5 的「非法 JSON 产出 medium」只在有配对时成立。
- 建议修法：进入配对循环前先遍历全部 rows，对 `_parse_object_json` 失败的行按 assertion id 去重产出 medium finding；补一条单条坏 JSON 的 fixture。

#### P12【低】同一坏断言与 N 条合法断言配对产生 N 条重复 finding

- 位置：`backend/moyu_consistency.py:286-300`（finding key 含 pair ids）
- 复现：1 条坏 JSON + 2 条合法断言 → 2 条「断言取值不是合法 JSON」（detail 相同、id 不同）。
- 建议修法：broken finding 以 `broken['id']` 为 key 去重，或在扫描阶段一次产出。

#### P13【低】数值表示 `1` vs `1.0` 被 canonical 视为不同

- 位置：`backend/moyu_consistency.py:79-82,299`
- 复现：`{"value":1}` vs `{"value":1.0}` → high 事实矛盾（92 B）；若走导出会「一致性阻断」。
- 影响：年龄/数量等数值字段若一处写 20、一处写 20.0，会被误判并阻断导出。
- 建议修法：`_canonical` 对 int/float 做数值归一化，或明确记录为已知边界。

#### P14【低】CLI `--project ghost` 直接抛 traceback

- 位置：`backend/moyu_consistency.py:1034-1060`（`main` 未捕获 `ConsistencyScopeError`）
- 复现：exit 1，stderr 末行 `ConsistencyScopeError: 项目不存在：ghost`，无友好提示。
- 建议修法：`main` 捕获 `ConsistencyScopeError`，打印 `错误：...` 并 exit 2（或 argparse error）。

#### P15【提示】`scoped_request` 仍以宽泛 `except ValueError` 包裹整个请求

- 位置：`backend/server.py:149-150`
- 说明：本轮 P4 修复把路由内 `try` 收窄了，但装饰器仍会把任何逃逸的 `ValueError` 映射为 400；当前引擎已知 `ValueError` 路径均已处理（ordinal 等），属潜在风险。建议后续按异常类型分流（`ConsistencyScopeError` → 404，参数错误 → 400）。

#### P16【提示】HTTP 响应新增 `all_findings`，过滤时仍返回全量

- 位置：`backend/moyu_consistency.py:976`；`backend/server.py:5423-5425`
- 说明：`min_severity` 时 `findings` 已裁剪，但 `all_findings` 携带全量；CLI 需要它，HTTP 客户端也会收到。若不想暴露，HTTP 层应剔除；若保留，请在 API 文档写明。

#### P1 残留观察（低）

`knowledge_scope` 非 dict 被静默降级为 `{}`；若原意是 private，会漏报知识越界。建议对非 dict 的 `knowledge_scope` 产出 low/medium 数据质量 finding，或保守视为 private。

### 10.6 第二轮结论

**needs_revision**。P1–P4、P6–P10 已确认修复；P5 主路径修复，但「单条坏 JSON 静默漏报」（P11，中）仍未覆盖，另有 P12/P13 低级别问题。4 条测试命令 + LongFormBench 40 章基线全部通过，未发现 P2/P7/P9 修复引入的回归。

发布建议：

1. 发布前修 P11（扫描阶段去重产出）与 P12（同 key 去重），改动小。
2. P13 建议做数值归一化或写入已知边界；P14/P15/P16 可排期。
3. 若 captain 判定 P11 可接受为已知边界并在引擎文档记录，可转 pass。

---

## 11. 第三轮最终复核（2026-10-23 15:58，P11–P16）

### 11.1 复核范围与最终基线

本轮只复核 P11–P16 修复及 P1–P10 回归；除本报告外未手工修改仓库文件。LongFormBench 运行会按其设计重写 `docs/consistency-baseline-2026-10-23.md` 与 `benchmarks/longform/results/longform-baseline.json`，这是 benchmark 的正常输出，不是本轮手工改动。

| 文件 | 大小 (bytes) | mtime | sha256 |
|---|---:|---|---|
| `backend/moyu_consistency.py` | 48890 | 2026-10-23 15:54:48 | `7c6f9a6b65aebdfed8a70eb1f04ec735d58353daaadc72a5260694a387cbc622` |
| `backend/server.py` | 475818 | 2026-10-23 15:55:20 | `28066e132c57d12ad35c6132b080e8fe248e11dc9e9cc6b39a3616a6a5e1b575` |
| `tests/consistency_fixtures.py` | 16831 | 2026-10-23 15:55:56 | `3130744857dde080142a3405db2318c25c66cc2cf0da69b79c117a123afcebe2` |
| `tests/consistency_http_fixtures.py` | 9270 | 2026-10-23 15:42:55 | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` |
| `benchmarks/longform/run.py` | 29658 | 2026-10-23 15:49:05 | `05a08274d76dc374328601486ead48e61dd23305968ac27033f57bd0c29e9cea` |

### 11.2 必跑命令与结果

| # | 命令 | 退出码 | 关键输出 |
|---:|---|---:|---|
| 1 | `.venv/bin/python -m py_compile backend/moyu_consistency.py backend/server.py tests/consistency_fixtures.py` | 0 | 无输出（编译通过） |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.306s` / `OK` |
| 4 | `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests in 2.237s` / `OK` |
| 5 | `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests in 1.652s` / `OK` |
| 6 | `.venv/bin/python benchmarks/longform/run.py --chapters 40 --seed 20261023` | 0 | `PASS`；105,182 汉字；35/35；精确率 100%；干净基线 0 findings；中位 2.75 ms |

### 11.3 P11–P16 逐条复核

#### P11【已修复】单条坏 JSON 不再静默漏报

- 代码：`backend/moyu_consistency.py:301-320` 在配对前对全量 rows 单独扫描，每条断言以 `key=f"{row['id']}:broken"` 产出唯一 finding；`328-329` 让坏断言不参与配对。
- 探针：`P11 single broken: [('medium', '断言取值不是合法 JSON')] count= 1 score= 97.0`。
- Fixture：`tests/consistency_fixtures.py:323-354` 用 p3/b3 的 `bad1`/`bad3` 断言恰好 2 条 medium、id 集合精确、`score < 100`，全部通过。

#### P12【已修复】一条坏断言只报一次，不再按配对数重复

- 探针：
  - `P12 same-value: broken= 1 all= [('medium', '断言取值不是合法 JSON')]`（1 坏 + 2 合法同值）
  - `P12 different: broken= 1 high= 1 all= [('high', 'located_in 存在冲突取值'), ('medium', '断言取值不是合法 JSON')]`（1 坏 + 2 合法异值）
- 结论：坏 JSON 只产出一条 medium；合法断言之间的真实冲突仍按 1 条 high 报出，无重复。

#### P13【已修复】`1` 与 `1.0` 按同一取值比较

- 代码：`backend/moyu_consistency.py:86-110` 新增 `_normalize_for_compare` / `_same_value`；`246` 的 `_classify_object_conflict` 早退和 `332` 的 `_detect_fact` 相等判断都改用它。`_canonical`（79-83）保持只用于展示。
- 探针：
  - `P13[int-vs-float] 1 vs 1.0 -> True`
  - `P13[bool-vs-int] True vs 1 -> False`
  - `P13[dict-int-vs-float] {'a': 1} vs {'a': 1.0} -> True`
  - `P13[list-order] [1, 2] vs [2, 1] -> False`
  - `P13[dict-key-order] {'a': 1, 'b': 2} vs {'b': 2, 'a': 1} -> True`
  - `P13[nested-int-vs-float] {'v': {'a': 1}} vs {'v': {'a': 1.0}} -> True`
  - `P13 analyze[flat-1-vs-1.0] fact=[] score=100.0`
  - `P13 analyze[nested-1-vs-1.0] fact=[] score=100.0`
  - `P13 analyze[true-vs-1] fact=[('high', 'level 存在两个不同取值')] score=92.0`（布尔与数字仍区分）
- Fixture：`tests/consistency_fixtures.py:353-354` 断言 1/1.0 不产生 `level 存在` 冲突，通过。

#### P14【已修复】CLI 作用域错误不再抛 traceback

- 代码：`backend/moyu_consistency.py:1094-1097` 捕获 `ConsistencyScopeError`，打印 `一致性检查失败：…` 到 stderr 并返回 2。
- 探针：
  - `P14 ghost branch: exit=2 stderr='一致性检查失败：分支不存在：ghost' traceback=False`
  - `P14 ghost project: exit=2 stderr='一致性检查失败：项目不存在：ghost' traceback=False`

#### P15【已知边界，不修】`scoped_request` 的宽泛 `except ValueError`

- 位置：`backend/server.py:136-151`，第 149 行 `except ValueError as exc:` 会把装饰器内任何逃逸的 `ValueError` 映射为 400。
- captain 2026-10-23 决定：不在本次改动面上修改，按「已知边界」记录。当前 `/api/consistency` 的 ghost project/branch 已实测返回 404（见 P16），未发现实际误映射；建议后续按异常类型分流（`ConsistencyScopeError` → 404，参数错误 → 400）。

#### P16【已修复】HTTP 响应不再重复携带 `all_findings`

- 代码：`backend/server.py:5426-5428` 在 `send_json` 前 `report.pop("all_findings", None)`；CLI 仍通过 `backend/moyu_consistency.py:1111` 使用 `all_findings` 做门禁。
- 探针：
  - `P16 GET status= 200 keys= ['branch_id', 'categories', 'counts', 'engine', 'examined', 'filtered_by', 'findings', 'generated_at', 'grade', 'project_id', 'returned', 'score', 'summary', 'total_examined', 'version']`
  - `P16 all_findings in body: False findings count: 0`
  - `P16 row counts before= {'assertion': 0, 'entity': 2, 'document_node': 1} after= {'assertion': 0, 'entity': 2, 'document_node': 1}`（GET 只读）
  - `P16 bad filter: 400 未知的严重度筛选：bogus`
  - `P16 duplicate filter: 400 每个筛选条件只能填写一次。`
  - `P16 ghost project header: 404 project not found`
  - `P16 ghost branch header: 404 branch not found in this project`
  - `P16 CLI exit= 0 has all_findings= True findings= 17 all= 17`

#### P17【低，已知边界，不阻断】大整数归一为 float 的精度/溢出

- 位置：`backend/moyu_consistency.py:95-98`，`_normalize_for_compare` 把所有 `int`/`float` 统一转成 `float`。
- 复现：
  - `2**53 vs 2**53+1 -> True`（超过 2^53 的整数丢精度，两个不同大整数被误判为同一取值）
  - `10**400 edge EXC: OverflowError int too large to convert to float`（超大整数字面量会抛异常）
- 影响评估：当前小说设定数据的数值量级远小于 2^53，本轮 40 章基线未触发；不阻断发布。建议后续用精确 int 比较或 `Decimal` 处理极端数值，并补一条边界 fixture。

### 11.4 P1–P10 回归核对

- `probes2.py`：P1 analyze OK；P2 whitespace/key-order 不报、genuine 报 high、`1` vs `1.0` 不报；P3 三种 `--fail-on` 组合 exit=1/1/0；P4 ordinal=abc → 200 + medium，HTTP body 无 `all_findings`，bad filter 400、duplicate 400、ghost project/branch 404；P5 broken pair → medium、broken alone → 1 条、broken+2 valid → 1 条 broken + 1 条 high；P6 summary 正常；P7 `ConsistencyScopeError` + CLI exit 2；P9 high-only → B。
- `probes2.py` 在 P9 blocker 段因 fixture trigger 报 `sqlite3.IntegrityError: canon evidence and reviewer required` 中断（探针构造问题，不是引擎失败）；同一场景由 `probes3.py` 绕过 trigger 验证：`P9 blocker: score= 80.0 grade= C`。
- `probes3.py`：`P2[nested-key-order] score=100.0 fact=[]`、`P2[array-order] score=92.0 fact=[('high', 'located_in 存在冲突取值')]`、`P2[nested-genuine] score=92.0`、`P1 export OK, issues= []`、`filter=blocker: findings= 0 all_findings= 17 score= 13.0 grade= D`。
- P10：`CORE_CATEGORIES` 出现次数 0；P8：`docs/strategy-beat-novelforge-2026-10-23.md:103` 的分数公式仍在。
- 结论：P2/P7/P9 的修复未引入回归；P13 的规范化改动未让既有 fixture 假失败。

### 11.5 第三轮结论

**pass（后端阶段）**。P11–P14 全部通过，P16 通过；P15 按 captain 决定记录为已知边界；P17 为低风险残留（当前数据量级不触发），不阻断。4 条测试命令 + `py_compile` + LongFormBench 40 章全部 exit 0，未发现 P11–P16 修复引入的回归。

发布建议：

1. P15/P17 排期处理：P15 按异常类型分流；P17 改用精确整数比较或 `Decimal` 并补边界 fixture。
2. 前端面板、许可证/引擎文档等并行工作流不在本轮后端验证范围，完成后需另行验收。

