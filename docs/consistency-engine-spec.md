# 一致性引擎实现规格：矛盾数据模型

> **文档性质**：实现契约（implementation contract），不是用户手册。  
> **读者**：新增/修改检测器的后端工程师、审查者、基准测试维护者。  
> **真源**：`backend/moyu_consistency.py`。适配器与发表门禁在 `backend/server.py`。  
> **用户手册**：`docs/consistency-engine.md`（安装、面板入口、HTTP/CLI 用法、报告字段展示）。本文只描述引擎内部契约、判定语义和扩展方式，不重复用户操作说明。

策略文档 D2 的原始表述是「6 类矛盾 + 严重度 + 状态机」。当前 `CATEGORY_LABELS` 已扩展为 **七类**：前六类是策略文档的六类，第七类 `evidence`（来源失效）服务于「每句话有来路」的产品承诺。D2 所说的「状态机」落在 `assertion.state`（作者/复核流程）上，不在 finding 上；见第 8 节。

本文所有规则都引用代码符号名，**不写行号**。行号会随重构漂移，符号名是稳定契约。

---

## 1. 范围、数据流与不变量

### 1.1 数据流

```text
SQLite 投影表
  → _build_context()            只读上下文（实体/章节/最新 revision/删除章节/最大序位）
  → DETECTORS                   七类检测器，按固定顺序执行
  → list[dict] + examined       每类返回 finding 列表和核对条数
  → analyze()                   排序、计数、扣分、等级、筛选、组装报告
  → GET /api/consistency        HTTP 适配器（移除 all_findings 后返回）
  → export_finalization_issues 发表门禁适配器（调用全量 analyze）
  → main()                      CLI 适配器（文本/JSON、--fail-on）
```

### 1.2 引擎不变量

1. **只读**：`analyze()` 和所有 `_detect_*` 不得写数据库，不得创建临时表，不得修改 `conn.row_factory` 之外的状态；`_rows()` 会恢复原来的 `row_factory`。
2. **确定性**：同一数据库、同一 `project_id`/`branch_id`、同一 `min_severity`，重复运行必须得到相同的 `findings`、`all_findings`、`score`、`counts`、`categories`、`examined` 和 finding `id`。唯一例外是 `generated_at`（当前时间）。
3. **报告是快照**：`analyze()` 每次重新计算；finding 不是持久化实体，没有数据库表，也没有状态字段。
4. **检测器隔离**：每个检测器只依赖 `conn`、`project_id`、`branch_id`、`ctx`；不共享可变状态，不互相调用，不依赖执行顺序（`DETECTORS` 的顺序只决定 `examined` 键的插入顺序；最终排序由显式 `sort` key 决定）。
5. **缺表降级**：依赖表不存在时，检测器返回 `([], 0)`，不得让整份报告崩溃；可选列（例如 `app_plot_item.archived`）用 `_has_column()` 探测，缺列时走兼容分支。缺表造成的“零发现”是已知局限，调用方应结合 `examined` 判断覆盖范围。

### 1.3 作用域

`analyze(conn, project_id, branch_id, *, min_severity=None)` 的作用域由调用方给定。各检测器读取范围不同：

- `_build_context()` 的 `entities` 是**整个作品**（`entity.project_id`），不是当前分支；
- `chapters` 是当前作品 + 当前分支的 `document_node.kind='chapter'`；
- `latest_revision` 是**全库** `document_revision` 按 `rowid` 建立的 `document_node_id → revision_id` 映射，不按分支过滤；
- `deleted_chapters` 来自 `app_deleted_node.chapter_id`，查询没有作品/分支条件；
- `max_ordinal` 是 `chapters` 中排除 `deleted_chapters` 后的最大 `ordinal`。

这些范围差异是 v1 的已知边界，新增检测器必须知道：不能假设 `ctx["entities"]` 只包含当前分支，也不能假设 `latest_revision` 只包含当前分支。

---

## 2. 共享判定原语

这些符号定义在 `backend/moyu_consistency.py`，所有检测器必须复用，不要各自实现一套：

| 符号 | 契约 |
| --- | --- |
| `_loads(value, default)` | `None` / 空串返回 `default`；已经是 `dict` / `list` 原样返回；字符串走 `json.loads`；解析失败返回 `default`。用于容错读取 JSON 列。 |
| `_parse_object_json(raw)` | 返回 `(value, broken)`。`None` / 空串 → `({}, False)`；`dict` / `list` → `(value, False)`；非空且无法解析 → `(None, True)`。只有“非空但解析失败”才算数据损坏。 |
| `_canonical(value)` | 用于**展示**的规范化 JSON：`json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"))`；不可序列化时退回 `repr`。不要用它做语义相等判断。 |
| `_normalize_for_compare(value)` | 用于**比较**的归一化：布尔先于数字；`int` 保持精确整数；整数值的 `float` 归一为 `int`；`NaN` / `±Inf` 有稳定哨兵；`dict` 按键排序；`list` 保持顺序；`None` 和字符串各有标记。 |
| `_same_value(left, right)` | `_normalize_for_compare(left) == _normalize_for_compare(right)`。忽略 JSON 空白、键顺序、`1` 与 `1.0` 的数值差异；区分布尔与数字；数组顺序仍算不同。 |
| `_iso(value)` | 仅当值非空且字符串匹配 `^\d{4}-\d{2}-\d{2}` 时返回真。只做前缀识别，不校验日历合法性。 |
| `_overlap(a_start, a_end, b_start, b_end)` | 仅当可解析的 ISO 边界明确证明两个区间分离时返回假；任一边界缺失或无法解析时返回真（保守地视为重叠）。 |
| `_rows(conn, sql, parameters)` | 临时设置 `sqlite3.Row` 后执行查询并恢复原 `row_factory`；只用于读。 |
| `_table_exists(conn, name)` | 查询 `sqlite_master`；用于缺表降级。 |
| `_has_column(conn, table, column)` | 查询 `PRAGMA table_info`；用于兼容旧库缺列。 |
| `_chapter_ref(chapters, chapter_id)` | 生成 `第{ordinal}章《{title}》` / `未绑定章节` / `章节 <chapter_id>（已不存在）` 的展示文本；只用于 detail/title，不参与判定。 |
| `_finding(...)` | 唯一的 finding 构造函数；见第 5 节。 |

**关键区分**：`_canonical` 只负责展示，`_same_value` 负责语义比较。需要语义比较的检测器（当前是 `fact`）必须走 `_same_value`，否则会把 `{"a":1}` 与 `{"a": 1}`、`1` 与 `1.0` 误报为矛盾；`attribute` 的原始字符串比较是 3.4 记录的例外。

---

## 3. 七类矛盾的定义与判定规则

每类都按「读取 → 算矛盾 → 不算 → 已知误报来源」四段描述。严重度标题是 `_finding()` 的 `title`，会参与 finding id 的哈希；修改标题会改变 id，必须同步回归 fixture。

### 3.1 事实矛盾（`fact`）

**符号**：`_detect_fact`、`_classify_object_conflict`、`_parse_object_json`、`_same_value`、`_overlap`。

**读取**：`assertion` 与 `entity` 连接，条件为 `assertion.project_id`、`assertion.branch_id`、`assertion.state='canon'`；按 `(subject_entity_id, predicate, attribute)` 分组，组内两两比较。`attribute` 由 `_attribute_slot()` 从 `object.attribute` 取得（去首尾空白；非字符串或缺失记为 `""`，即「未分类」槽位）。`examined` 等于参与查询的 canon 断言行数（主语实体必须存在）。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| 单条断言的 `object_json` 非空但不是合法 JSON | medium | `断言取值不是合法 JSON` | `assertion_id`、`object_json` |
| 两侧任一侧不是 JSON 对象（标量、数组、字符串） | high | `<predicate> 存在两个不同取值` | `entity_id`、`assertion_ids`、`objects` |
| 至少一侧带非空 `attribute`，但两侧槽位不同（含「有槽位 vs 未分类」） | — | 跳过，不构成矛盾 | — |
| 槽位相同，任一侧有 `value`，且两侧 `value` 语义不同 | high | `属性「<attribute>」存在两个不同取值` | `entity_id`、`assertion_ids`、`attribute`、`objects` |
| 槽位相同，取值相同或双方都未记录 `value` | — | 继续判名字漂移 | — |
| 无槽位，且 `predicate != "has_story_fact"` | high | `<predicate> 存在冲突取值` | `entity_id`、`assertion_ids`、`objects` |
| 两侧 `name` 都非空且不同（含槽位相同但取值无冲突的情况） | high | `同一实体被记成两个名字` | 同上 |

`_classify_object_conflict` 返回 `(None, "", "")` 表示不构成矛盾或按槽位跳过。判定依据是**数据形状**（`object` 里有没有 `attribute`）而不是谓词名：`has_attribute`、`has_story_fact` 与将来任何带槽位的谓词都走同一套。`note` 不参与 fact 判定——同槽位同值时 `note` 不同不报。

写入端谓词来自 `ASSERTION_PREDICATES` 白名单（`has_story_fact`、`has_attribute`）：`ensure_memory_assertion()` 带属性槽位时写 `has_attribute`，否则写 `has_story_fact`；白名单外的谓词回落并记一条 `memory.assertion_predicate_fallback` 事件（已知边界见 [一致性引擎 · 已知局限](consistency-engine.md#已知局限) 第 14 条）。

**不算 / 跳过**

- 单条坏 JSON 会在独立扫描中报一次 medium，然后该断言不参与任何配对；不会因为配对数量重复报。
- 两侧 `_same_value` 为真（JSON 空白、键序、`1` 与 `1.0` 等）。
- 任一侧的 `supersedes_id` 直接指向另一侧（新旧取代关系）。
- 两侧有效期不重叠（`_overlap` 返回假）。
- 任一侧 `epistemic_mode == "author_plan"`（作者计划不算矛盾）。
- 只有 `state='canon'` 的断言参与；`candidate`、`contradicted`、`superseded`、`retracted` 都不读取。
- 不同 `(subject_entity_id, predicate, attribute)` 之间不比较；不同属性槽位是有意跳过，不是遗漏；实体别名不合并；只按实体 id 分组。

**已知误报来源**

- **语义相同但结构不同**：`_same_value` 能处理键序、空白、`1`/`1.0`，但不做单位、大小写、字符串与数字互转、数组顺序的语义归一；例如 `"20"` 与 `20`、`["a","b"]` 与 `["b","a"]` 会被视为不同。
- **开放/缺失有效期**：任一侧缺少 `valid_start`/`valid_end`，`_overlap` 保守返回真；在“先记一条，后面才补时间”的真实写作流程里，容易把本应分属不同时期的取值报成冲突。
- **作者用其它方式表达计划**：只跳过 `author_plan`；`narrator`、`inference` 等假设性断言仍参与比较，可能把叙述者的猜测当作事实冲突。
- **直接取代关系不完整**：只检查 `supersedes_id` 的直接指向；A→B→C 的链式取代在 A、C 都还是 canon 时仍可能被比较。
- **槽位缺失即漏报**：`attribute` 缺失记为 `""`，它与任何带槽位的断言都不比较——一条没标槽位的断言即使与已标槽位的断言真冲突，也不会报。
- **同谓词不同槽位不比较**：`home_city` 与 `age` 天然不同轴（设计如此，属漏报面）；同一槽位内不同语境的两个合法取值（没有用有效期区分）仍会被报 high。
- **非对象取值不区分槽位**：任一侧不是 JSON 对象（标量、数组、字符串）时直接报 `<predicate> 存在两个不同取值`，槽位判定不参与。
- **坏 JSON 的 medium**：如果 `object_json` 故意存了非 JSON 的展示文本，会稳定产生一条 medium；这是数据质量提示，不一定是“矛盾”。
- **配对放大**：组内两两比较，同一组 n 条不同取值会产生多条 finding；每条都有不同的 `assertion_ids` 对，不能靠 finding id 自动合并。

### 3.2 时间线矛盾（`timeline`）

**符号**：`_detect_timeline`、`_chapter_ref`、`_iso`、`_overlap`（断言有效期检查不直接用 `_overlap`，而是双端 ISO 字符串比较）。

**读取**：`app_timeline_event` 中当前作品/分支的事件，按 `ordinal, created_at` 排序；`examined` 先计入事件行数，再计入“`valid_start` 和 `valid_end` 都非空的 canon 断言”行数。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| 事件的 `chapter_id` 在 `ctx["deleted_chapters"]` 中 | high | `时间线事件挂在已删除章节上` | `event_id`、`chapter_id` |
| `int(event["ordinal"])` 抛 `TypeError` / `ValueError` | medium | `时间线事件的序位不是整数` | `event_id`、`ordinal` |
| 同一 `ordinal` 上出现多个不同的 `chapter_id` | medium | `同一序位挂着多个章节的事件` | `ordinal`、`event_ids` |
| 同一 `chapter_id` 的事件有多个不同的 `event_time` 字符串 | medium | `同一章节出现多个故事时间` | `chapter_id`、`times` |
| canon 断言的 `valid_start`、`valid_end` 都是 ISO 日期，且字符串上 `valid_start > valid_end` | high | `事实的有效期起点晚于终点` | `assertion_id` |

**不算 / 跳过**

- `status` 为 `abandoned` 或 `cancelled` 的事件跳过后续事件检查；但仍计入 `examined`。
- 事件没有 `chapter_id` 时不参与“同序位多章节”和“同章节多时间”检查。
- 事件没有 `event_time` 时不参与“同章节多时间”检查。
- 断言缺少 `valid_start` 或 `valid_end` 时不参与有效期检查；只有一端非 ISO 时也不比较。
- 只有 canon 断言参与有效期检查。

**已知误报来源**

- **`int()` 不是严格的整数校验**：`int("5")`、`int(5.0)` 都成功；`int(5.9)` 会静默截断为 5，导致“5.9”被当成序位 5 参与分组。标题写“不是整数”，但实际只拦截无法转换的值；这是已知的语义偏差。
- **同章节多 `event_time`**：倒叙、插叙、同章多场景都可能合法；引擎没有场景边界，只能按章节聚合。
- **同序位多章节**：并行叙事、群像场景可能刻意共用序位；引擎无法区分“同一场景的多个章节”和“序位冲突”。
- **日期只做字符串比较**：`_iso` 只识别 `YYYY-MM-DD` 前缀，不校验 `2026-13-99` 这类非法日期；字符串比较可能把格式差异当成时间先后。
- **删除章节集合是全局的**：`_build_context` 的 `deleted_chapters` 查询没有作品/分支条件；依赖 chapter id 全局唯一才不会串项目。
- **`examined` 包含被跳过的事件**：废弃/取消事件也算核对条数，覆盖率高估。

### 3.3 知识越界（`knowledge`）

**符号**：`_detect_knowledge`、`_known_by_of`、`_loads`。

**读取**：canon 断言 join `entity`；如果 `candidate_memory` 表存在，通过断言 `evidence_json` 中第一个带 `candidate_memory_id` 的对象左连接来源记忆；`examined` 等于 canon 断言行数。`known_by` 来自来源记忆的 `payload_json`：优先 `known_by` 数组，否则 `knowledge_scope.known_by` 数组；两者都没有则跳过该断言。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| `epistemic_mode == "character_belief"`，实体名和实体 id 都不在 `known_by` 中 | high | `角色相信自己不知道的事` | `assertion_id`、`memory_id`、`entity_id`、`known_by` |
| `epistemic_mode == "world_truth"`，来源记忆 payload 的 `private` 或 `knowledge_scope.private` 为真 | medium | `局部认知被提升为世界事实` | `assertion_id`、`memory_id`、`known_by` |
| `epistemic_mode == "character_belief"`，且实体 `kind` 不在 `{"character", "人物", None, ""}` 中 | low | `非人物实体被标记为持有信念` | `assertion_id`、`entity_id` |

第三条是独立的 `if`，可以与第一条同时命中同一条断言。

**不算 / 跳过**

- 没有 `candidate_memory` 链接，或 `known_by` 为空：整条断言跳过，不产生任何知识类 finding（`examined` 仍计数）。
- `epistemic_mode` 为 `narrator`、`inference`、`author_plan` 时不检查。
- `known_by` 只做精确字符串匹配：实体 `canonical_name` 或实体 id；不匹配别名、不忽略大小写、不 trim 列表元素。
- `world_truth` 只有在 `private` 为真时才报；没有 `known_by` 的 `world_truth` 会在前面直接跳过。

**已知误报来源**

- **`known_by` 用别名**：作者写「沈砚」的别名「阿砚」时，实体名匹配不上，会报 high；这是当前最常见的结构性误报来源之一。
- **`known_by` 元素带空白**：`_known_by_of` 用 `str(item).strip()` 过滤空值，但返回的是未 trim 的原字符串；`" 沈砚 "` 不会匹配 `"沈砚"`。
- **忽略 `candidate_memory.status`**：查询选了 `memory_status`，但检测器没有使用；`rejected`、`stale` 的记忆仍可能驱动知识判定，造成误报。
- **只取第一个 `candidate_memory_id`**：一条断言有多条证据/多个记忆时，只使用 SQL `LIMIT 1` 的第一个；可能关联到错误的记忆。
- **`private` 的真值判断**：`private` 或 `knowledge_scope.private` 只要 truthy 就报；字符串 `"false"` 也会被视为真。
- **实体 kind 的大小写/写法**：只接受 `character`、`人物`、空、`None`；`Character`、`角色` 等写法会触发 low。
- **没有时间维度**：`known_by` 是静态集合，无法表达“角色在第 30 章才知道”；早期断言可能被按最终知识状态误判。
- **`knowledge_scope` 畸形**：字符串等非对象会被当成 `{}`，导致 `private` 丢失或 `known_by` 为空；代码有意不让它 500，但会产生漏报/误报。

### 3.4 属性漂移（`attribute`）

**符号**：`_detect_attribute`、`_overlap`、`_canonical`、`_loads`。

**读取**：`character_state` join `entity`，当前作品/分支，按 `(entity_id, attribute)` 分组，组内按 `valid_start, id` 排序后两两比较；`examined` 等于 `character_state` 行数。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| 同一实体、同一属性、两条记录的有效期重叠，且 `value_json` 原始字符串不同，且不是同一条非空 `source_assertion_id` | high | `属性「<attribute>」在重叠时间内出现两个取值` | `entity_id`、`attribute`、`character_state_ids` |

**不算 / 跳过**

- 两条 `value_json` 原始字符串完全相同。
- 两条的 `source_assertion_id` 都非空且相同（同一来源断言派生的状态）。
- 两条有效期不重叠（`_overlap` 返回假）。
- 只有 `character_state` 有记录才检查；没有状态表时返回 `([], 0)`。

**已知误报来源**

- **原始字符串比较**：这里用的是 `left["value_json"] == right["value_json"]`，不是 `_same_value`；`{"a":1}` 与 `{"a": 1}`、`1` 与 `1.0` 会被视为不同并报 high，尽管详情里的展示会规范化。
- **有意变更没有有效期区分**：角色性格从“沉稳”变“暴躁”是合法人物弧光；如果没有用 `valid_start`/`valid_end` 切开，引擎会按重叠时间报 high。引擎不读取 `event_id` 做因果解释。
- **缺失日期即重叠**：任一条缺少 `valid_start`/`valid_end`，`_overlap` 保守返回真。
- **`source_assertion_id` 为空**：两条都为空时不会因“同源”跳过；同一批导入数据可能被误报。
- **没有状态字段**：`character_state` 没有 `status`，已废弃/被替代的状态仍参与比较；`knowledge_scope_json` 也不参与。
- **组内两两比较**：同一属性 n 条不同取值会产生多条 finding，而不是合并为一条“属性漂移”。

### 3.5 关系/称谓矛盾（`relation`）

**符号**：`_detect_relation`、`_build_context`（`entities`）、`_finding`。

**读取**：`app_story_relation` 当前作品/分支的全部行；`examined` 等于关系行数。端点实体从 `ctx["entities"]`（整个作品）查找。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| 起点或终点实体在 `ctx["entities"]` 中不存在 | high | `关系<起点/终点>指向不存在的实体` | `relation_id`、`missing_entity_id` |
| 起点或终点实体存在但 `status != "active"` | high | `关系<起点/终点>指向已归档实体` | `relation_id`、`entity_id`、`status` |
| 起点与终点相同，且关系 `status == "active"` | medium | `实体与自己建立了关系` | `relation_id`、`relation_type` |
| 同一 `(from_entity_id, to_entity_id, relation_type)` 有多条 `status == "active"` 的关系 | low | `同一对人物存在重复关系` | `relation_ids` |

缺失/归档按“起点/终点”各报一条；一条关系可能同时产生多条 finding。

**不算 / 跳过**

- 没有 `app_story_relation` 表时返回 `([], 0)`。
- 非 active 关系不参与自指和重复关系检查；但端点存在性/状态检查仍会执行（这是容易忽略的边界）。
- 反向重复（A→B 与 B→A 同类型）不检查。
- `relation_type` 不做同义词/称谓归一；`note` 字段不参与判定。

**已知误报来源**

- **状态措辞**：任何 `status != "active"` 都报“已归档实体”，`merged`、`split` 也会被这样描述；实际语义可能不是“归档”。
- **非 active 关系仍检查端点**：历史关系、停用关系如果端点已归档，仍会产生 high；作者可能认为停用关系不该再报。
- **实体作用域是作品级**：实体存在于同一作品的其它分支时，当前分支关系会被认为端点存在；跨分支关系可能漏报或误报。
- **自指可能是合法的**：「心魔」「自我对话」等自指关系会被 medium 提示；引擎无法判断文学意图。
- **重复关系可能是合法的**：同一对人物在不同阶段/不同语境有多条 active 记录时，会被 low 提示；没有时间/场景维度。
- **关系类型不统一**：「师父」「师傅」「师尊」不归一；这主要造成漏报，但也可能让“重复关系”检查失效。
- **`note` 未使用**：无法用备注区分“曾经的师徒”和“当前的师徒”。

### 3.6 伏笔断线（`foreshadow`）

**符号**：`_detect_foreshadow`、`_build_context`（`chapters`、`max_ordinal`）、`_finding`。

**读取**：`app_plot_item` 与 `app_foreshadow_link`，当前作品/分支；`examined` 等于全部 plot item 行数（包括 archived）。如果 `app_plot_item` 有 `archived` 列且值为真，该条跳过后续检查。`app_foreshadow_link.kind` 的 schema 枚举是 `plant` / `develop` / `payoff`；检测器只把 `plant` 和 `payoff` 纳入规则，`develop` 链接不参与 plant/payoff 规则。如果一条伏笔只有 `develop` 链接，`plants`/`payoffs` 都为空，但 `item_links` 非空，所以也不会触发“没有任何章节链接”的 low。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| 有 payoff、没有 plant | high | `伏笔只有回收、没有埋设` | `plot_id`、`payoff_ids` |
| `stage == "payoff"`，但没有任何 payoff 链接 | medium | `标记为回收却没有回收记录` | `plot_id` |
| `stage != "payoff"`、有 plant 无 payoff，且 `max_ordinal - max(plant 章节 ordinal) >= 15` | medium | `伏笔埋设超过 15 章仍未回收` | `plot_id`、`plant_ids` |
| 没有任何链接、`stage == "setup"`、文本非空 | low | `伏笔没有任何章节链接` | `plot_id` |

**不算 / 跳过**

- `app_plot_item` 或 `app_foreshadow_link` 缺表时返回 `([], 0)`。
- `archived` 为真时整条跳过（但仍计入 `examined`）。
- `stage` 不是精确的 `"payoff"` / `"setup"` 时，对应规则不命中；`stage` 的其它值只影响“未回收”规则。
- `kind == "develop"` 的链接不参与 plant/payoff 规则；如果一条伏笔只有 `develop` 链接，`plants`/`payoffs` 都为空，但 `item_links` 非空，因此不会触发“没有任何章节链接”的 low。
- plant 链接的章节不在当前分支 `ctx["chapters"]` 中（例如已删除或其它分支）时，该 plant 不贡献 ordinal，因此不会触发 15 章超期。
- 没有检查 payoff 是否发生在 plant 之后；没有检查 link 的章节是否有效。

**已知误报来源**

- **`stage` 由 schema 约束为 `setup` / `develop` / `payoff`**：正常写入不会出现 `"Payoff"`、`"已回收"`；但历史/导入数据可能绕过 CHECK，此时精确匹配会漏判。反过来，任意非 `"payoff"` 的合法 stage 只要有老 plant、无 payoff，就可能触发 15 章超期。
- **15 章阈值是硬编码**：`max_ordinal - max(plant ordinal) >= 15`；没有卷/部边界，短篇或不同卷的节奏可能不适合这个阈值。
- **`max_ordinal` 含空章节**：`_build_context` 取所有非删除章节的最大 ordinal；如果作者建了空章节占位，会把“当前进度”推高，导致伏笔被误判超期。
- **plant 指向已删除/其它分支章节**：该 plant 不计入 ordinal，可能漏报；如果所有 plant 都这样，规则完全不触发。
- **payoff 无 plant 的 high**：如果 plant 链接被误删/迁移，会报“没有埋设”，但作者实际上埋过。
- **`archived` 真值**：SQLite 中字符串 `"0"` 在 Python 里是 truthy；如果旧库把 archived 存成字符串，可能错误跳过。
- **只按 plot item 检查**：没有检查 `plot_thread` 或其它伏笔表；`app_plot_item` 与 `app_foreshadow_link` 的语义必须由写入方保证。

### 3.7 来源失效（`evidence`）

**符号**：`_detect_evidence`、`_build_context`（`latest_revision`）、`_loads`。

**读取**：canon 断言的 `evidence_json`、`reviewed_by`、`reviewed_at`；如果 `source_span` 表存在，按**作品**加载 `source_span`；最新 revision 来自 `ctx["latest_revision"]`（全库）。`examined` 等于 canon 断言行数。

**算矛盾**

| 条件 | 严重度 | 标题 | `evidence` 关键字段 |
| --- | --- | --- | --- |
| `evidence_json` 解析后不是非空数组 | blocker | `已确认事实没有来源` | `assertion_id` |
| 缺少 `reviewed_by` 或 `reviewed_at`（空字符串/`None`） | blocker | `已确认事实缺少复核人` | `assertion_id` |
| 证据条目引用的 `source_span` 不存在 | high | `来源片段已不存在` | `assertion_id`、`source_span_id` |
| `source_span.status != "valid"` | high | `来源片段已失效` | `assertion_id`、`source_span_id`、`status` |
| `source_span.revision_id` 不是该文档节点的最新 revision（且最新 revision 存在） | medium | `来源片段属于旧版本正文` | `assertion_id`、`source_span_id`、`span_revision`、`latest_revision` |

第一条命中后 `continue`，不再检查该断言的证据；缺少复核人**不会**中断后续 `source_span` 检查，因此同一断言可能同时有 blocker 和 high/medium。

**不算 / 跳过**

- `evidence` 数组里的条目不是对象，或没有 `source_span_id`：跳过，不产生 finding。
- 证据条目没有 `source_span_id` 时，即使内容不可核验也不会报；这是当前契约的盲区。
- `source_span` 存在、`status == "valid"`、且 revision 等于最新 revision：不报。
- `latest_revision` 中没有该 `document_node_id` 时，不做“旧版本”判断。

**已知误报来源**

- **`stale` 与 `ambiguous` 同罪**：只要 `status != "valid"` 就报 high「来源片段已失效」；`ambiguous` 可能只是需要人工确认，严重度可能偏高。
- **`source_span` 按作品加载**：`_build_context` 和 `_detect_evidence` 都不按分支过滤 `source_span`；多分支并行时，当前分支可能引用其它分支的 span，或本分支 span 被其它分支影响。
- **最新 revision 按全库 rowid**：`latest_revision` 不按分支过滤，且用 `document_revision` 的插入顺序（`rowid`）判断“最新”；分支切换/导入顺序不保证语义最新，可能把本分支仍有效的原句报成旧版本。
- **缺 `source_span` 表**：如果旧库没有该表，所有带 `source_span_id` 的证据都会变成 high「来源片段已不存在」；这不是静默通过，但会集中误报。
- **`reviewed_by`/`reviewed_at` 只判空**：空白字符串 `" "` 在检测器里是 truthy，会通过；schema 触发器会 trim，但历史/导入数据可能绕过。
- **空证据 blocker 的边界**：正常写入路径有 `check_assertion_canon_insert` / `check_assertion_canon_update` 触发器；历史库、导入库、迁移前数据仍可能触发，不能因为触发器存在就认为 blocker 不可能出现。
- **多证据条目重复报**：同一条断言引用多个失效 span，会为每个 span 生成一条 high；这是“每条证据一条 finding”，不是按断言聚合。
- **status 先于 revision**：失效 span 不会再报“旧 revision”；同一来源只报最严重的一层，可能掩盖 revision 问题。
- **没有校验 quote/hash**：只检查 span 存在、状态和 revision；不比对 `quote_hash` 是否与当前正文一致。

---

## 4. 严重度权重、分数与等级

### 4.1 权重与顺序

| 严重度 | `SEVERITY_WEIGHTS` | `SEVERITY_ORDER` | `SEVERITY_LABELS` |
| --- | ---: | ---: | --- |
| `blocker` | 20.0 | 0 | 阻断 |
| `high` | 8.0 | 1 | 高 |
| `medium` | 3.0 | 2 | 中 |
| `low` | 1.0 | 3 | 低 |

`SEVERITY_ORDER` 越小越严重，用于排序和 `min_severity` 的“该等级及以上”过滤。`SEVERITY_WEIGHTS` 的插入顺序也是 `counts` 的键顺序。

### 4.2 分数

`analyze()` 对**全量** `findings` 计算：

```text
deduction = Σ SEVERITY_WEIGHTS[finding["severity"]]
score     = round(max(0.0, 100.0 - deduction), 1)
```

- 分数是绝对扣分制，不是百分比；问题足够多时饱和到 `0.0`。
- 同一 finding 只扣一次；finding 不按类别加权。
- `min_severity` 不影响 `deduction`/`score`，见 4.4。

### 4.3 等级与封顶

基础等级由 `_grade(score)` 按 `_GRADE_THRESHOLDS` 计算：

| 分数 | 基础等级 |
| --- | --- |
| `score >= 90.0` | A |
| `75.0 <= score < 90.0` | B |
| `60.0 <= score < 75.0` | C |
| `score < 60.0` | D |

然后按**全量** `counts` 封顶：

```text
if counts["blocker"]:
    grade = "C" if grade in {"A", "B"} else grade
elif counts["high"]:
    grade = "B" if grade == "A" else grade
```

规则含义：

- 存在 blocker：基础等级 A/B 一律降到 C；已经是 C/D 不变。
- 不存在 blocker、存在 high：基础等级 A 降到 B；B/C/D 不变。
- 等级不会因为 `min_severity` 过滤而改变。

示例：

| 场景 | score | 基础等级 | 封顶后 |
| --- | ---: | --- | --- |
| 无 finding | 100.0 | A | A |
| 1 条 high（扣 8） | 92.0 | A | B |
| 1 条 blocker（扣 20） | 80.0 | B | C |
| 1 条 high + 1 条 medium（扣 11） | 89.0 | B | B |
| 1 条 blocker + 多条 low，score 50 | 50.0 | D | D（C 封顶只作用于 A/B） |

### 4.4 `min_severity` 的语义

`analyze(..., min_severity="high")`：

- 先计算全量 `findings`、`counts`、`categories`、`deduction`、`score`、`grade`、`total_examined`；
- 再用 `SEVERITY_ORDER[finding["severity"]] <= SEVERITY_ORDER[min_severity]` 过滤出 `visible`；
- 只有 `findings`、`returned`、`filtered_by` 受影响；
- `all_findings` 始终是全量；
- `counts`、`categories`、`examined`、`total_examined`、`score`、`grade`、`summary` 都不受筛选影响。

这是硬契约：**筛选器不能美化体检报告**。如果未来要把筛选下沉到 SQL，必须先保留一份全量聚合，否则 `tests/consistency_fixtures.py` 的 `min_severity` 断言会失败。

---

## 5. Finding 字段契约

### 5.1 `_finding()` 签名

```python
_finding(
    category: str,
    severity: str,
    title: str,
    detail: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    suggestion: str = "",
    key: str = "",
) -> dict[str, Any]
```

### 5.2 返回字段

| 字段 | 类型 | 契约 |
| --- | --- | --- |
| `id` | `str` | `"cx_" + sha1(f"{category}|{severity}|{title}|{key}").hexdigest()[:16]`，UTF-8 编码。相同四元组得到相同 id；`detail`、`evidence`、`suggestion` 不参与哈希。 |
| `category` | `str` | 必须是 `CATEGORY_LABELS` 的键；检测器不得发明未注册类别。 |
| `category_label` | `str` | `CATEGORY_LABELS.get(category, category)`；正常路径下等于中文标签。 |
| `severity` | `str` | 必须是 `SEVERITY_ORDER` / `SEVERITY_WEIGHTS` 的键；非法值会在排序/计分时抛 `KeyError`。 |
| `title` | `str` | 人类可读标题；参与 id，修改标题等于改变 finding 身份。 |
| `detail` | `str` | 具体解释；不参与 id，可安全改写文案。 |
| `evidence` | `dict` | 默认为 `{}`；`dict(evidence or {})` 复制。必须放稳定业务主键（assertion/entity/plot/relation/span id），供定位、基线匹配和前端跳转。 |
| `suggestion` | `str` | 默认为空串；作者可执行的修复建议。 |
| `key` | **不返回** | 构造参数，只参与 id 哈希。它是“同一 category+severity+title 下区分不同 occurrence”的判别键，不是 finding 字段。 |

### 5.3 `key` 的使用契约

`key` 必须满足：

1. **同一问题稳定**：同一数据库、同一记录重复运行，`key` 不变。
2. **不同问题可区分**：同一 category/severity/title 下的两个不同 occurrence 必须有不同的 `key`，否则会得到相同 id，前端/回归无法区分。
3. **不含易变展示文本**：不要用 detail 或完整文本做 key；用业务 id 或稳定组合。

当前实现中的例子：

- `f"{assertion_id}:broken"`（fact 坏 JSON）；
- `assertion_id`（knowledge 的单条断言）；
- `f"{assertion_id}:empty"` / `:review` / `:{span_id}:missing` / `:{span_id}:{status}` / `:{span_id}:old`（evidence 的不同规则）；
- `f"{left_id}|{right_id}"`（fact 正常配对、attribute 的一对记录）；
- `f"{relation_id}:{role}"`（relation 的起点/终点）；重复关系用 `f"{source_id}|{target_id}|{relation_type}"`，自指用 `relation_id`；
- `f"{plot_id}:no-plant"` / `:no-payoff` / `:stale` / `:unlinked`（foreshadow 的不同规则）；
- `f"ordinal:{ordinal}"`、`f"chapter-time:{chapter_id}"`（timeline 聚合规则）；timeline 的单事件规则用 `event_id` 或 `f"{event_id}:ordinal"`，有效期检查用 `assertion_id`。

### 5.4 唯一性与碰撞

- `tests/consistency_fixtures.py` 断言 `len(ids) == len(set(ids))`；新增检测器必须保证正常 fixture 下 id 唯一。
- id 使用 SHA-1 前 16 个十六进制字符（64 bit），理论上存在碰撞；它不是安全标识，不得用于权限或持久化主键。
- 同一 `key` 被同一 category/severity/title 重复使用时，id 必然相同；这不是“去重”，而是身份冲突。检测器必须保证每个 occurrence 只构造一次，或用不同 `key` 区分。
- 修改 `title`/`severity`/`category`/`key` 都会改变 id；只改 `detail`/`evidence`/`suggestion` 不会。

---

## 6. `analyze()` 返回体契约

### 6.1 输入

```python
analyze(
    conn: sqlite3.Connection,
    project_id: str,
    branch_id: str,
    *,
    min_severity: str | None = None,
) -> dict[str, Any]
```

- `min_severity` 不是 `None` 且不在 `SEVERITY_ORDER` 中：抛 `ValueError`，消息为 `min_severity 只能是 blocker / high / medium / low，收到：<value>`。
- `project` 表存在但 `project_id` 不存在：抛 `ConsistencyScopeError`（`ValueError` 子类），消息 `项目不存在：<id>`。
- `branch` 表存在但 `branch_id` 不存在：抛 `ConsistencyScopeError`，消息 `分支不存在：<id>`。
- 表不存在时不做事后存在性校验（缺表降级）。

### 6.2 顶层字段

| 字段 | 类型 | 契约 |
| --- | --- | --- |
| `engine` | `str` | 固定 `"moyu-consistency"`。 |
| `version` | `int` | `ENGINE_VERSION`，当前 `1`。 |
| `generated_at` | `str` | `datetime.now(timezone.utc).replace(microsecond=0).isoformat()`；每次运行变化，是报告唯一非确定字段。 |
| `project_id` | `str` | 输入作用域。 |
| `branch_id` | `str` | 输入作用域。 |
| `score` | `float` | 4.2 的扣分结果，保留 1 位小数。 |
| `grade` | `str` | `A` / `B` / `C` / `D`，已按 4.3 封顶。 |
| `counts` | `dict[str, int]` | 键固定为 `blocker`、`high`、`medium`、`low`；全量计数，含 0。 |
| `categories` | `dict[str, dict]` | 键为 `CATEGORY_LABELS` 的全部类别（当前 7 个）；每项含 `label`、`count`、`blocker`、`high`、`medium`、`low`，全部基于全量 findings。 |
| `examined` | `dict[str, int]` | 键为 `DETECTORS` 的检测器名（当前 7 个）；值是各检测器“核对条数”，不是 finding 数；缺表时为 0。 |
| `total_examined` | `int` | `sum(examined.values())`；同一记录被多类检测重复核对时会重复计数，这是有意设计。 |
| `findings` | `list[dict]` | 按 `min_severity` 过滤后的可见清单；无筛选时与 `all_findings` 值相同。排序为 `(SEVERITY_ORDER[severity], category, id)`。 |
| `all_findings` | `list[dict]` | 全量清单，排序同上；CLI/门禁使用；HTTP 适配器会移除。 |
| `returned` | `int` | `len(findings)`。 |
| `filtered_by` | `str \| None` | 传入的 `min_severity`，未传为 `None`。 |
| `summary` | `str` | 人类可读摘要；无 finding 时固定 `一致性分数 100（A）：核对 {total_examined} 项次，未发现矛盾。`；有 finding 时包含分数、等级、总核对数、问题总数和四级计数。 |

### 6.3 排序与筛选

- 先按 `DETECTORS` 顺序收集；最后统一 `sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], item["category"], item["id"]))`。
- `min_severity` 只过滤 `findings`；`all_findings` 不受影响。
- `summary` 始终使用全量 `len(findings)` 和 `counts`，不使用 `visible`。
- `categories` 始终按 `CATEGORY_LABELS` 的插入顺序输出全部类别，即使 `count` 为 0。
- `examined` 始终按 `DETECTORS` 顺序输出全部检测器，即使值为 0。

### 6.4 HTTP 适配器差异

`GET /api/consistency` 在 `backend/server.py` 的 `scoped_request` 中调用 `analyze()`，随后执行：

```python
report.pop("all_findings", None)
```

因此：

- **HTTP 响应没有 `all_findings`**；只有 `findings`（已按 `min_severity` 过滤）。
- CLI `--json` 保留 `all_findings`，`--fail-on` 也使用它。
- 如果新增字段，HTTP 默认会透传；只有 `all_findings` 被显式移除。新增字段必须考虑响应体积和前端兼容。

---

## 7. 检测器契约

### 7.1 签名

```python
def _detect_x(
    conn: sqlite3.Connection,
    project_id: str,
    branch_id: str,
    ctx: dict[str, Any],
) -> tuple[list[dict], int]:
    ...
```

`analyze()` 以 `detected, count = detector(conn, project_id, branch_id, ctx)` 调用，不传关键字参数。

### 7.2 输入

- `conn`：只读 SQLite 连接；不得提交事务或写入。
- `project_id` / `branch_id`：调用方给定；检测器必须自行在 SQL 中过滤，不能假设 `ctx` 已按分支过滤。
- `ctx`：`_build_context()` 的返回值，键固定为：
  - `entities`: `dict[entity_id, {"id","name","kind","status","aliases"}]`，整个作品；
  - `chapters`: `dict[chapter_id, {"id","title","ordinal"}]`，当前作品+分支；
  - `latest_revision`: `dict[document_node_id, revision_id]`，全库；
  - `deleted_chapters`: `set[chapter_id]`，来自 `app_deleted_node`；
  - `max_ordinal`: `int`，排除已删除章节后的最大章节序位，默认 0。

检测器不得修改 `ctx` 的任何值；如果需要派生数据，复制到局部变量。

### 7.3 输出

- `list[dict]`：每个元素必须由 `_finding()` 构造；不得手工拼 dict（否则可能漏字段/漏 category_label）。
- `int`：`examined`，表示该检测器实际核对的记录条数；不是 finding 数，必须 `>= 0`。
- 没有可核对记录时返回 `([], 0)`；不要返回 `None`。

### 7.4 只读

- 禁止 `INSERT`、`UPDATE`、`DELETE`、`REPLACE`、`CREATE`、`DROP`、`ALTER`、`VACUUM`、`PRAGMA` 写操作、临时表、`conn.commit()`。
- 读取统一用 `_rows()`；它负责设置/恢复 `row_factory`。
- 需要判断表/列时用 `_table_exists()` / `_has_column()`，不要直接 `SELECT` 后捕获 `sqlite3.OperationalError` 当控制流。

### 7.5 确定性

- SQL 必须有稳定排序，或在 Python 中对分组键排序；不要依赖 `set` 的迭代顺序。
- 不要使用 `datetime.now()`、随机数、`hash()`、进程环境或文件系统状态。
- 同一输入必须产生相同的 finding 顺序和 id；`analyze()` 最后统一排序是第二道保险，但检测器内部生成顺序也应稳定，避免 `key` 使用不稳定顺序。
- `generated_at` 是唯一允许的时间字段，由 `analyze()` 生成，不在检测器内。

### 7.6 异常策略

- **缺表**：返回 `([], 0)`，不抛异常；**可选列缺失**用 `_has_column()` 探测并走兼容分支，不假设所有列都存在。
- **坏 JSON/坏 payload**：用 `_loads()` / `_parse_object_json()` 降级；数据质量问题应变成 finding（如 fact 的坏 JSON medium），而不是异常。
- **业务异常**：不得抛 `ValueError`、`KeyError`、`TypeError` 等让整个报告失败的异常；检测器应把可预期的数据问题局部化。
- **编程错误**：不要用 `except Exception: return [], 0` 吞掉；那会把“检测器写错了”伪装成“没有问题”。未知异常应暴露给测试/调用方。
- `analyze()` 对非法 `min_severity` 抛 `ValueError`，对不存在的 project/branch 抛 `ConsistencyScopeError`；检测器不负责作用域校验，也不应把这两类输入错误包装成空报告。

### 7.7 注册

`DETECTORS` 是唯一的注册点：

```python
DETECTORS = (
    ("fact", _detect_fact),
    ("timeline", _detect_timeline),
    ("knowledge", _detect_knowledge),
    ("attribute", _detect_attribute),
    ("relation", _detect_relation),
    ("foreshadow", _detect_foreshadow),
    ("evidence", _detect_evidence),
)
```

- 元组的第一个元素必须与 `CATEGORY_LABELS` 的键一致；`analyze()` 用这个名字写 `examined`。
- 顺序影响 `examined` 的键顺序；`categories` 的顺序由 `CATEGORY_LABELS` 决定，不由 `DETECTORS` 决定。
- 新增检测器必须同时出现在 `CATEGORY_LABELS` 和 `DETECTORS`；只加一个会导致 `categories` 缺项或 `examined` 缺键。

---

## 8. 与既有数据模型的关系

### 8.1 `assertion.state` 五态

`assertion.state` 的 schema 枚举是：

```text
candidate | canon | contradicted | superseded | retracted
```

| 状态 | 含义 | 检测器是否读取 | 允许的复核转移（`backend/server.py` 的 assertion review 逻辑） |
| --- | --- | --- | --- |
| `candidate` | 待作者/复核者确认的候选事实 | 否 | 可到 `candidate` / `canon` / `contradicted` / `superseded` / `retracted` |
| `canon` | 已确认事实；必须有非空证据数组和复核人/复核时间 | 是（fact/timeline 的断言部分/knowledge/evidence） | 可到 `canon` / `candidate` / `contradicted` / `superseded` / `retracted` |
| `contradicted` | 复核者判定与其它事实冲突 | 否 | 只能到 `contradicted` / `candidate` |
| `superseded` | 被更新的断言取代 | 否 | 只能到 `superseded` / `candidate` |
| `retracted` | 作者撤回 | 否 | 只能到 `retracted` / `candidate` |

补充：

- `supersedes_id` 是断言之间的取代指针；即使两条都还是 `canon`，`_detect_fact` 也会跳过直接取代的一对。
- `candidate` 不是“未验证的 canon”，它完全不进入事实/知识/来源检测；候选记忆的 `candidate_memory.status` 是另一套枚举（`candidate`/`accepted`/`rejected`/`stale`/`superseded`），只用于知识越界的来源 payload，当前检测器不检查其状态。
- 状态转移由 `backend/server.py` 的 assertion review 端点负责，不是引擎的职责；引擎只读。

### 8.2 finding 为什么没有状态

finding 是 `analyze()` 的**派生输出**，不是数据库实体：

1. **没有 `finding` 表**：引擎不持久化 finding；每次运行重新计算。
2. **id 是内容哈希，不是稳定主键**：`id` 由 `category|severity|title|key` 生成；改标题/严重度/类别/`key` 都会改 id，不能作为长期状态机的键。
3. **作者裁决已经落在源记录上**：作者把 `assertion.state` 从 `canon` 改为 `contradicted`/`superseded`/`retracted`，下一次 `analyze()` 自然不再读取它；不需要在 finding 上再存一份状态。
4. **避免双写与漂移**：如果 finding 也存“已确认/误报/延后”，就必须和 assertion 状态同步；当前 v1 选择单一事实源（assertion/character_state/relation/plot 等源表）。
5. **报告可重复**：finding 无状态，`analyze()` 才能在任意副本上得到同样结果；这也是 CLI、HTTP、基准脚本可以共享同一份报告的前提。

因此：

- `contradicted` 是 **assertion 的状态**，不是 finding 的状态；检测器不会把 finding 写回 assertion。
- finding 的“误报”处理方式是修改源数据（补有效期、改 state、修关系/伏笔），或在未来引入独立的**台账表**（以 `finding.id` + 内容指纹为键）记录作者裁决；台账表不能改变 `analyze()` 的只读/确定性契约。
- 前端展示的 finding 没有“已读/已分配/已忽略”字段；如果产品需要，应作为独立 UI/台账功能设计，不能偷偷塞进 `_finding()` 返回体。

### 8.3 `source_span.status` 如何参与 evidence 检测

`source_span.status` 的 schema 枚举是：

```text
valid | stale | ambiguous
```

`_detect_evidence` 的判定顺序：

1. `evidence_json` 不是非空数组 → blocker「已确认事实没有来源」，`continue`。
2. 缺 `reviewed_by`/`reviewed_at` → blocker「已确认事实缺少复核人」，不 `continue`。
3. 对每个带 `source_span_id` 的条目：
   - span 不存在 → high「来源片段已不存在」，`continue`；
   - `status != "valid"`（`stale` 或 `ambiguous`）→ high「来源片段已失效」，`continue`；
   - `status == "valid"` 且 `latest_revision` 存在且 `revision_id != latest` → medium「来源片段属于旧版本正文」；
   - 否则不报。
4. 没有 `source_span_id` 的条目直接跳过。

关键点：

- `ambiguous` 没有被单独建模，和 `stale` 一样按 high 处理；这是已知误报来源。
- `source_span` 按作品加载，不按分支；`latest_revision` 按全库 rowid；多分支场景需谨慎解读 medium。
- 引擎只读 `source_span.status`，不会修改它；reanchor/状态更新由 `backend/server.py` 的其它流程负责。
- `source_span` 表不存在时，所有带 `source_span_id` 的条目都会走“span 不存在” high；这是降级行为，不是静默通过。

---

## 9. 发表门禁规则

### 9.1 位置与调用

门禁不在 `moyu_consistency.py` 里，而在 `backend/server.py` 的 `export_finalization_issues(conn)`：

- 它先执行既有终稿检查（标题、简介、章节正文、本章审查、记忆确认、伏笔回收等）；
- 然后调用 `consistency_analyze(conn, project_id(), branch_id())`，**不传 `min_severity`**，所以拿到全量 `findings`；
- 只把满足下面条件的 finding 追加为 issue：

```python
finding["category"] in {"fact", "knowledge", "evidence"}
and finding["severity"] in {"blocker", "high"}
```

追加文案为：

```text
一致性阻断：<title>——<detail>
```

### 9.2 为什么不是只拦 blocker

- 在正常写入路径上，`assertion.state='canon'` 必须满足非空 `evidence_json` 数组 + `reviewed_by`/`reviewed_at`；`docs/schema-v1.1-migration.sql` 的触发器 `check_assertion_canon_insert` / `check_assertion_canon_update` 会拒绝不满足的写入。
- 因此 blocker 只在历史库、导入库、迁移前数据或绕过触发器的写入中出现；只拦 blocker 会让门禁在正常新库上几乎永不触发，等于没有门禁。
- 把 `fact` / `knowledge` / `evidence` 的 `high` 一并纳入，才能覆盖“事实自相矛盾、角色知道自己不该知道的事、来源片段失效/不存在”这类真正影响发表可信度的问题。

### 9.3 为什么其它类别不拦

- `timeline`、`attribute`、`relation`、`foreshadow` 的 high 在 v1 仍是提示级；它们的规则式误报率还没有压到可以阻断发表的程度（例如同章多场景、合法人物弧光、自指关系、伏笔节奏）。
- 所有类别的 `medium` / `low` 都不拦；它们用于作者排查，不作为发表硬门禁。
- 这是 v1 的产品决策，不是引擎能力判断；未来若某类误报率下降，可以扩展 `export_finalization_issues` 的集合，但必须同步 fixture 和本文档。

### 9.4 `author_override` 放行条件

- 请求头：`X-Moyu-Export-Override`。
- `export_override_reason(raw)`：
  - 先用 `bounded_text(raw, 24_000, ...)` 检查原始 header；超过 24,000 字符抛 `ValueError`（HTTP 400）；
  - 用 `urllib.parse.unquote(raw, encoding="utf-8", errors="strict")` 做 URL 解码；
  - 解码失败（非法 UTF-8）→ `ValueError`，HTTP 400；
  - 解码后 `strip()`，再用 `bounded_text(..., 2_000, ...)` 检查；超过 2,000 字符抛 `ValueError`（HTTP 400）；
  - 返回解码后的理由字符串。
- 放行条件：`len(export_override.strip()) >= 8`（按 Python 字符数，不是字节数）。
- 不满足 8 字符：
  - `GET /api/export/epub`、`GET /api/export/markdown`：若 `export_finalization_issues(conn)` 非空，返回 **409**：
    ```json
    {
      "error": "export blocked by finalization gate",
      "issues": ["..."],
      "override_required": true
    }
    ```
  - `GET /api/export/manifest`：不返回 409；返回的 manifest 中 `finalization` 字段说明是否允许。
- manifest 的 `finalization` 结构（由 `export_manifest_for_state` 生成）：
  - `allowed`（bool）：`issues` 为空，或 `author_override` 去空白后长度 `>= 8`；
  - `issues`（list[str]）：当前终稿门禁的全部问题；
  - `overridden`（bool）：`issues` 非空且 `author_override` 去空白后长度 `>= 8`。

  被拦截且未 override 时，值形如：

```json
{
  "allowed": false,
  "issues": ["一致性阻断：<title>——<detail>"],
  "overridden": false
}
```

- 非空 `author_override` 会写入 manifest 的 `author_override` 字段（`reason`、`recorded_by`、`recorded_at`）；`/api/export/epub` 与 `/api/export/markdown` 还会把它写入 `publication.exported` 事件 payload（`/api/export/manifest` 只返回 manifest，不追加导出事件）。
- `epub_bytes(conn, author_override)` 会把 `export_finalization_issues(conn)` 的结果传给 `export_manifest_for_state`；因此 EPUB 内嵌的 manifest 与 `/api/export/manifest` 使用同一套 `finalization` 语义。
- 门禁只看一致性 finding 的 `category`/`severity`，不看 `score`/`grade`；分数低但只有 medium/low 时不会仅因分数被拦。

---

## 10. 扩展指南：新增第 8 类检测器

以下步骤假设新增类别名为 `x`，严重度仍使用现有四级（推荐；不要轻易新增严重度，权重/排序/前端都会受影响）。

### 步骤 1：注册类别

在 `CATEGORY_LABELS` 中增加：

```python
CATEGORY_LABELS = {
    ...,
    "x": "X 类矛盾",
}
```

- 键必须与检测器名、`DETECTORS` 元组第一项一致。
- 插入位置决定 `categories` 的报告顺序；如果希望前端顺序稳定，放在末尾或与前端 `CATEGORY_ORDER` 同步。
- 不要把类别只加到 `DETECTORS` 而不加 `CATEGORY_LABELS`：`categories` 不会包含它，但 `counts`/`score` 仍会计入，报告会自相矛盾。

### 步骤 2：实现 `_detect_x`

```python
def _detect_x(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "x_table"):
        return [], 0
    rows = _rows(
        conn,
        "SELECT ... FROM x_table WHERE project_id=? AND branch_id=? ORDER BY ...",
        (project_id, branch_id),
    )
    findings: list[dict] = []
    for row in rows:
        # 用 _loads / _has_column / _overlap 等共享原语做容错
        findings.append(
            _finding(
                "x",
                "high",
                "...",
                "...",
                evidence={"x_id": row["id"]},
                suggestion="...",
                key=row["id"],
            )
        )
    return findings, len(rows)
```

要求：

- 缺表返回 `([], 0)`；可选列缺失用 `_has_column()` 探测；不要抛 `OperationalError`。
- SQL 必须按 `project_id` / `branch_id` 过滤；如果使用 `ctx`，注意 `entities`/`latest_revision` 是作品级/全库级。
- 每个 occurrence 用稳定且唯一的 `key`；同一 `category+severity+title+key` 不能重复。
- `examined` 是核对条数，不是 finding 数；明确哪些行被跳过但仍计数。
- 只读，不写库，不修改 `ctx`。

### 步骤 3：接入 `DETECTORS`

在 `DETECTORS` 中加入：

```python
("x", _detect_x),
```

- 顺序决定 `examined` 键顺序；放在末尾最安全。
- `analyze()` 不需要为新类别写专门分支：`categories` 遍历 `CATEGORY_LABELS`，`examined` 遍历 `DETECTORS`，`counts` 遍历 `SEVERITY_WEIGHTS`。
- 如果新检测器需要新的 `ctx` 字段，在 `_build_context()` 中增加，并更新第 7.2 节的字段表；不要在每个检测器里重复查询同一份上下文。

### 步骤 4：补 fixture

在 `tests/consistency_fixtures.py` 中：

1. `seed_dirty()` 增加至少一个能触发 `x` 的记录；
2. `expected` 字典增加 `"x": n`（`categories_of()` 只返回非零类别）；
3. 断言 finding 的 `evidence` 能回到具体记录（例如 `x_id`）；
4. 如果 `x` 有 hard negative（不应报的合法情况），在 `seed_clean()` 或单独的 hard-negative 段加入，确保不误报；
5. 保持既有断言：id 唯一、重复运行 id 一致、排序正确、必填字段非空、`min_severity` 不改分数；
6. 如果 `x` 影响 HTTP 契约或发表门禁，在 `tests/consistency_http_fixtures.py` 增加对应用例。

### 步骤 5：更新文档

- 本文档：在 3.x 增加该类的「读取 / 算矛盾 / 不算 / 已知误报来源」，在第 10 节记录注册信息；
- `docs/consistency-engine.md`：用户手册的七类表、检测器章节、已知局限；
- `docs/CHANGELOG.md`：按仓库惯例记录用户可见变化；
- 如果新增类别会影响前端展示，同步 `frontend/consistency-panel.js` 的类别顺序/标签（前端不在本任务范围，但发布前必须检查）。

### 步骤 6：决定是否进入发表门禁

默认**不进入**。只有同时满足以下条件才修改 `export_finalization_issues()` 的过滤集合：

- 该类在真实/基准数据上的误报率已压到可接受；
- 有 fixture 证明 high/blocker 的阻断行为；
- 产品上同意把它作为发表硬门禁；
- 更新本文档第 9 节和用户手册。

### 步骤 7：验证命令

```bash
.venv/bin/python -m py_compile backend/moyu_consistency.py
.venv/bin/python tests/consistency_fixtures.py
.venv/bin/python tests/consistency_http_fixtures.py
```

如果新增了浏览器/前端行为，再跑对应的 `node tests/*.cjs`。发布前至少确认：

- `analyze()` 的既有顶层字段不变（新增类别只增加 `categories`/`examined` 的键）；
- 旧 finding 的 id 不变（除非有意改 title/severity/key）；
- `min_severity` 仍然只裁剪清单；
- HTTP 响应仍然不包含 `all_findings`；
- 门禁没有被意外放宽或收紧。

### 步骤 8：版本与兼容

- 新增类别是报告结构扩展：旧消费者如果按固定 7 个键遍历，需要能容忍新键；前端应使用 `CATEGORY_LABELS`/接口返回的 `categories`，不要硬编码 7 个。
- 修改既有类别的判定语义、字段或 finding id 生成方式属于破坏性变更，应评估是否提升 `ENGINE_VERSION` 并在 `docs/CHANGELOG.md` 说明。
- 不要为了新类别修改 `SEVERITY_WEIGHTS`/`SEVERITY_ORDER`；如果确实需要新严重度，必须同时更新排序、权重、标签、`counts`、门禁和所有 fixture。

---

## 11. 符号索引与核对方法

### 11.1 符号索引

| 层 | 符号 | 用途 |
| --- | --- | --- |
| 常量 | `ENGINE_VERSION` | 报告 `version` |
| 常量 | `SEVERITY_WEIGHTS` | 扣分权重 |
| 常量 | `SEVERITY_ORDER` | 严重度排序/筛选 |
| 常量 | `SEVERITY_LABELS` | 文本报告标签 |
| 常量 | `CATEGORY_LABELS` | 七类标签与 `categories` 顺序 |
| 常量 | `_GRADE_THRESHOLDS` | A/B/C 阈值 |
| 基础 | `_loads`、`_parse_object_json`、`_canonical`、`_normalize_for_compare`、`_same_value`、`_iso`、`_overlap` | JSON/日期/比较原语 |
| 基础 | `_rows`、`_table_exists`、`_has_column`、`_chapter_ref`、`_finding` | 查询/降级/finding 构造 |
| 上下文 | `_build_context` | 生成 `entities`/`chapters`/`latest_revision`/`deleted_chapters`/`max_ordinal` |
| 检测器 | `_detect_fact`、`_classify_object_conflict` | 事实矛盾 |
| 检测器 | `_detect_timeline` | 时间线矛盾 |
| 检测器 | `_detect_knowledge`、`_known_by_of` | 知识越界 |
| 检测器 | `_detect_attribute` | 属性漂移 |
| 检测器 | `_detect_relation` | 关系/称谓矛盾 |
| 检测器 | `_detect_foreshadow` | 伏笔断线 |
| 检测器 | `_detect_evidence` | 来源失效 |
| 汇总 | `DETECTORS` | 检测器注册表 |
| 汇总 | `_grade`、`analyze` | 等级、报告 |
| 适配器 | `render_text`、`_resolve_scope`、`main` | 文本/CLI |
| 服务端 | `export_override_reason` | 解码/限制覆盖理由 |
| 服务端 | `export_finalization_issues` | 发表门禁 |
| 服务端 | `export_manifest_for_state`、`epub_bytes` | manifest/finalization 字段 |
| 路由 | `/api/consistency`（`backend/server.py`） | HTTP 适配器，移除 `all_findings` |

### 11.2 逐条核对方法

本文档的规则按以下方式核对：

1. **符号存在性**：本文档引用的引擎/服务端 Python 符号都能在 `backend/moyu_consistency.py` 或 `backend/server.py` 中找到定义/引用；`CATEGORY_ORDER` 是 `frontend/consistency-panel.js` 的前端符号，`tests/*` 与 `docs/*` 是路径，不按 Python 符号核对；不写行号。
2. **字段存在性**：第 5、6 节的字段逐一对照 `_finding()` 返回 dict 和 `analyze()` 返回 dict；`key` 明确标注为构造参数、不返回。
3. **规则可复现**：每类至少有一个 fixture 覆盖（`tests/consistency_fixtures.py` 的 dirty seed 覆盖七类；`tests/consistency_http_fixtures.py` 覆盖 HTTP/门禁/筛选）。
4. **门禁可复现**：`export_finalization_issues` 的过滤条件与 `export_override_reason`/`export_manifest_for_state` 的行为对照 `backend/server.py`。
5. **回归命令**：第 10 节步骤 7 的三条命令必须通过；任何规则改动都要先跑 fixture 再改文档。

### 11.3 与策略文档的差异记录

D2 行定义的是「6 类矛盾 + 严重度 + 状态机」；策略文档的 Q1 目标还要求矛盾台账。当前实现与这两处的对应关系：

| 策略文档原文 | 当前实现 | 处理 |
| --- | --- | --- |
| 6 类矛盾 | `CATEGORY_LABELS` 有 7 类（增加 `evidence`） | 本文档按 7 类写；`evidence` 是「每句话有来路」的额外检查 |
| 严重度 + 状态机 | 严重度在 `SEVERITY_WEIGHTS`；状态机在 `assertion.state` | 第 4、8 节分别描述；finding 无状态 |
| 矛盾台账可筛选/分配/标记误报（Q1 目标） | v1 只有只读报告；无 finding 持久化/台账 | 第 8.2 节明确为已知差距，未来用独立台账表扩展 |

---

## 12. 已知跨类局限（实现层）

这些不是某一类的规则，而是所有检测器共享的边界，写新检测器时必须考虑：

1. **规则式，无语义理解**：只检查结构化投影；同义改写、代称、隐含矛盾、跨章节推理检测不到。
2. **只读快照**：不写库、不自动修复、不记录作者裁决；`suggestion` 只是文本。
3. **缺表静默降级**：缺表返回 0，可能让分数虚高；用 `examined` 判断覆盖范围，不能只看 `score`。
4. **作用域不统一**：`entities`/`latest_revision`/`source_span` 是作品级或全库级，`deleted_chapters` 是全局集合，`chapters`/事件/关系/伏笔是分支级；多分支项目要特别小心。
5. **`generated_at` 非确定**：报告除该字段外应可重复；基准比较要排除它。
6. **finding id 是内容哈希**：标题/严重度/类别/`key` 变化会改 id；不是持久化主键。
7. **`all_findings` 仅引擎/CLI**：HTTP 响应移除它；调用方不要假设 HTTP 有全量清单。
8. **分数饱和**：扣分超过 100 后 score 固定 0，不能区分“很糟”和“更糟”；用 counts/categories 辅助判断。
9. **性能是 O(n²) 风险**：fact/attribute 按组两两比较；大组（同一实体同一谓词数百条断言）会放大 finding 数和耗时，v1 未做上限。
10. **没有 finding 去重/合并**：同一底层冲突的多个配对会生成多条 finding；这是当前契约，不是 bug，但产品展示需要聚合时应在 UI/台账层做。

---

## 13. 变更检查清单（提交前）

- [ ] 新规则能在 `backend/moyu_consistency.py` 找到对应符号，且本文档没有写不存在的字段/规则。
- [ ] `CATEGORY_LABELS`、`DETECTORS`、`examined`、`categories` 的键一致。
- [ ] 新检测器只读、确定性、缺表降级、不抛业务异常。
- [ ] 每个 occurrence 的 `key` 稳定且唯一；fixture 的 id 唯一性断言通过。
- [ ] `min_severity` 只影响 `findings`/`returned`/`filtered_by`，不影响分数/计数/等级。
- [ ] HTTP 响应仍移除 `all_findings`；CLI/门禁仍使用 `all_findings`。
- [ ] 发表门禁的类别/严重度集合没有被意外扩大或缩小；override 8 字符规则未变。
- [ ] `docs/consistency-engine.md`（用户手册）、`docs/CHANGELOG.md`、fixture 已同步。
- [ ] `.venv/bin/python -m py_compile backend/moyu_consistency.py` 通过。
- [ ] `.venv/bin/python tests/consistency_fixtures.py` 通过。
- [ ] `.venv/bin/python tests/consistency_http_fixtures.py` 通过。
