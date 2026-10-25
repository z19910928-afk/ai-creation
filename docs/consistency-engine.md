# 一致性引擎

墨屿一致性引擎（`backend/moyu_consistency.py`，`ENGINE_VERSION = 1`）对当前作品的故事状态投影做一次**只读、确定性、可解释**的矛盾检测。它不写数据库、不改任何稿件或 projection；每条结论都带 `evidence`，可以回到具体实体、断言、原句片段或章节。

引擎有四条设计约束（模块 docstring）：

- **只读**：分析过程不写数据库，不改任何 projection。
- **确定性**：同样的数据必须得到同样的 finding id 与分数，便于回归与发布基准。
- **可解释**：每条 finding 都带 `category` / `severity` / `detail` / `evidence` / `suggestion`。
- **可降级**：缺少某张表或某一列时跳过对应检测，而不是让整个分析崩溃。

`CATEGORY_LABELS` 定义了七类问题：`fact` 事实矛盾、`timeline` 时间线矛盾、`knowledge` 知识越界、`attribute` 属性漂移、`relation` 关系/称谓矛盾、`foreshadow` 伏笔断线、`evidence` 来源失效。前六类是故事矛盾，`evidence` 服务于"每句话有来路"的产品承诺；但报告里的 `counts`、`categories` 与分数**包含全部七类**，`evidence` 同样扣分。引擎模块 docstring 仍写作"六类矛盾 + evidence 单独计分、不混入六类统计"，但 `analyze()` 的实现把 `evidence` 计入 `counts`、`categories` 与分数；以代码实现为准。

## 入口

### 工作台面板

工作台左侧导航的「一致性检查」（`frontend/index.html` 的 `data-view="consistency"`，面板实现见 `frontend/consistency-panel.js`）展示同一份报告：

- 0–100 分与 A/B/C/D 等级、按严重度统计、七类分类统计；
- 问题清单（标题、详情、建议、证据定位），章节引用可直接跳回章节；
- 按严重度**累计**筛选：选「高危」会同时显示阻断与高危；筛选只影响清单，分数和统计始终来自全量检查；
- 「重新检查」重新请求 `GET /api/consistency`。面板只读，不改任何稿件。

### HTTP 接口

```http
GET /api/consistency
GET /api/consistency?min_severity=high
```

- 作用域由 `X-Moyu-Project` / `X-Moyu-Branch` 请求头指定；不带时使用服务当前的默认作品与分支。请求头作用域由 `resolve_scope` 提前校验，不存在时返回 404；默认作用域在分析阶段缺失时由 `ConsistencyScopeError`（`ValueError` 子类）报出，HTTP 层映射为 400（`scoped_request()` 与 `/api/consistency` 路由）。
- `min_severity` 可选，取值 `blocker` / `high` / `medium` / `low`；同一个参数只能出现一次，重复或未知取值返回 400（`/api/consistency` 路由，错误文案分别是「每个筛选条件只能填写一次。」与「未知的严重度筛选：<x>」）。
- 响应是 `analyze()` 报告去掉 `all_findings` 后的版本，只包含 `findings`（裁剪后清单）；HTTP 响应不提供全量清单，CLI `--json` 与直接调用 `analyze()` 仍可拿到 `all_findings`（`/api/consistency` 路由的响应构造）。
- 报告本身只读；服务在处理请求前仍会执行既有的 `ensure_seed` 初始化（幂等，由 `/api/consistency` 路由调用），这不属于一致性分析写入。

### 命令行

```bash
.venv/bin/python -m backend.moyu_consistency --db moyu.sqlite3 --project <作品ID> --branch <分支ID>
```

详见下文「CLI」。

## 报告字段

`analyze()` 返回：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `engine` | string | 固定 `"moyu-consistency"` |
| `version` | int | 引擎版本，当前 `1` |
| `generated_at` | string | UTC ISO 8601 时间，精确到秒 |
| `project_id` | string | 本次检查的作品 |
| `branch_id` | string | 本次检查的分支 |
| `score` | number | 0–100，保留 1 位小数 |
| `grade` | string | `A` / `B` / `C` / `D`，受最严重问题封顶（见下文） |
| `counts` | object | 全量问题的 `blocker` / `high` / `medium` / `low` 数量 |
| `categories` | object | 七类各自的 `label`、`count` 与四级数量 |
| `examined` | object | 七类各自参与核对的记录数 |
| `total_examined` | int | `examined` 各项之和 |
| `findings` | array | 问题清单（会被 `min_severity` 裁剪） |
| `all_findings` | array | 完整问题清单，不受 `min_severity` 影响；CLI `--json` 输出包含，HTTP 响应会移除（`/api/consistency` 路由的响应构造） |
| `returned` | int | `findings` 的实际条数 |
| `filtered_by` | string \| null | 本次生效的 `min_severity`，未筛选为 `null` |
| `summary` | string | 中文摘要，含分数、等级、检查数与各级数量 |

单条 finding：

| 字段 | 说明 |
| --- | --- |
| `id` | `cx_` + 16 位十六进制。由 `sha1(category\|severity\|title\|key)` 截断，同一数据稳定复现（`_finding()`） |
| `category` | 七类之一：`fact` / `timeline` / `knowledge` / `attribute` / `relation` / `foreshadow` / `evidence` |
| `category_label` | 中文类别名 |
| `severity` | `blocker` / `high` / `medium` / `low` |
| `title` | 一句话问题标题 |
| `detail` | 具体说明，含实体名、取值或章节 |
| `evidence` | 证据对象，键随检测器不同（见下文各类） |
| `suggestion` | 修改建议；引擎不自动修复 |

`findings` 按严重度、类别、id 排序（`analyze()` 内统一排序）：`blocker` → `high` → `medium` → `low`，同级按类别字符串与 id。

`examined` 是"参与核对的记录数"，不是"发现数"；同一条断言可能同时被 fact、knowledge、evidence 等检测器核对，因此 `total_examined` 会大于实际记录数（`summary` 中的「同一记录会被多类检测重复核对」即指此）。各类口径：

| 类别 | `examined` 口径 |
| --- | --- |
| `fact` | 当前分支参与比较的 canon 断言数（`_detect_fact()` 的 `examined`） |
| `timeline` | 时间线事件数 + 带双端有效期的 canon 断言数（`_detect_timeline()` 的 `examined`） |
| `knowledge` | canon 断言数（含未关联记忆的，`_detect_knowledge()` 的 `examined`） |
| `attribute` | `character_state` 记录数（`_detect_attribute()` 的 `examined`） |
| `relation` | 关系记录数（`_detect_relation()` 的 `examined`） |
| `foreshadow` | 伏笔条目数（含 archived，`_detect_foreshadow()` 的 `examined`） |
| `evidence` | canon 断言数（`_detect_evidence()` 的 `examined`） |

## 分数与等级

严重度权重（`SEVERITY_WEIGHTS`）：

| 严重度 | 权重 |
| --- | --- |
| `blocker` 阻断 | 20 |
| `high` 高 | 8 |
| `medium` 中 | 3 |
| `low` 低 | 1 |

计算方式（`analyze()` 中的 `deduction`/`score`）：

```text
deduction = 20 × blocker 数 + 8 × high 数 + 3 × medium 数 + 1 × low 数
score     = round(max(0, 100 − deduction), 1)
```

分数不会低于 0。基础等级阈值（`_GRADE_THRESHOLDS` 与 `_grade()`）：

| 分数 | 基础等级 |
| --- | --- |
| ≥ 90 | A |
| ≥ 75 | B |
| ≥ 60 | C |
| < 60 | D |

**`grade` 不是纯分数分段**：等级会被最严重问题封顶（`analyze()` 中的 grade 封顶）。存在 `blocker` 时最高 C（A/B 一律降为 C）；没有 blocker 但存在 `high` 时最高 B（A 降为 B）；C/D 不再调整。这样"92 分 + 一条 high 事实矛盾"不会显示成 A。

示例：

| 问题构成 | 分数 | 等级 |
| --- | --- | --- |
| 无问题 | 100 | A |
| 1 个 low | 99 | A |
| 1 个 medium | 97 | A |
| 3 个 medium | 91 | A |
| 1 个 high | 92 | B（high 封顶 B） |
| 2 个 high + 1 个 medium | 81 | B |
| 5 个 high | 60 | C |
| 6 个 high | 52 | D |
| 1 个 blocker | 80 | C（blocker 封顶 C） |
| 1 个 blocker + 1 个 high | 72 | C |
| 2 个 blocker | 60 | C |

没有问题时固定输出 100 分与 A，且 `summary` 为「一致性分数 100（A）：核对 N 项次，未发现矛盾。」（`analyze()` 的无问题 `summary` 分支）。

## `min_severity` 的语义

`min_severity` 是**累计下限**，只裁剪返回的 `findings` 清单，不改变分数与统计（`analyze()` 中的 `visible` 过滤）：

- `min_severity=high` 返回 `blocker` 与 `high`；
- `min_severity=medium` 返回 `blocker`、`high`、`medium`；
- 分数、`counts`、`categories`、`examined`、`total_examined` 始终基于**全量**检查结果；
- `returned` 是裁剪后的条数，`filtered_by` 回显本次筛选；
- `all_findings` 保留全量清单，供门禁与 CLI 调用方核对；
- `summary` 仍描述全量问题数，因此可能与 `findings` 的条数不同。

这条语义是刻意的：否则"只看高危"会把分数抬回 100，等于用筛选器美化体检报告。

## 七类检测器

检测器注册顺序见 `DETECTORS`。每类检测器先检查依赖表，缺表时跳过对应查询；没有可核对记录时 `examined` 为 0（例如 `timeline` 只缺事件表时，仍会检查断言有效期）。

| 类别 | 读取的主要表 | 严重度范围 | 实现 |
| --- | --- | --- | --- |
| `fact` 事实矛盾 | `assertion`、`entity` | high / medium / low | `_detect_fact()` |
| `timeline` 时间线矛盾 | `app_timeline_event`、`assertion`、`app_deleted_node` | high / medium | `_detect_timeline()` |
| `knowledge` 知识越界 | `assertion`、`entity`、`candidate_memory`（可选） | high / medium / low | `_detect_knowledge()` |
| `attribute` 属性漂移 | `character_state`、`entity` | high | `_detect_attribute()` |
| `relation` 关系/称谓矛盾 | `app_story_relation`、`entity` | high / medium / low | `_detect_relation()` |
| `foreshadow` 伏笔断线 | `app_plot_item`、`app_foreshadow_link`、`document_node` | high / medium / low | `_detect_foreshadow()` |
| `evidence` 来源失效 | `assertion`、`source_span`、`document_revision` | blocker / high / medium | `_detect_evidence()` |

### 1. 事实矛盾（fact）

读取当前作品、当前分支下 `state='canon'` 的断言（`_detect_fact()` 的 canon 断言查询），按 `(subject_entity_id, predicate, attribute)` 分组，组内再按 **`_canonical(取值)` 分桶**，只枚举跨桶配对（`_detect_fact()` 的 `buckets` / `bucket_list` 两层循环）。`attribute` 来自 `_attribute_slot()`：取 `object.attribute` 去首尾空白；非字符串或缺失记为 `""`（「未分类」是真实槽位，坏 JSON 也落在这里）。**不同槽位之间不比较**——同一实体身上挂多条不同事实是常态，不是矛盾。
分桶只消掉「同值副本」那一半比较：跨桶配对数是 `Σ_{i<j} m_i·m_j = (n² − Σ m_i²) / 2`（`m_i` 是第 i 个桶的条数）。全部同值时为 0；**取值全不同时（每桶 1 条）仍是 `C(n,2)`，最坏情形 O(n²)**——独立验收在 n=10/20/40 条互不相同取值上实测 45 / 190 / 780 次配对（来源：`docs/verification-dedup-2026-10-24.md` §二，探针包装 `_classify_object_conflict()` 直接计数）。

先解析两侧 `object_json`（`_parse_object_json()`）：`NULL` / 空串按空对象处理；非空但解析失败时，报一次 **medium**「断言取值不是合法 JSON」，该断言不参与后续配对，不会按配对数重复（`_detect_fact()` 的坏 JSON 分支）。

以下情况**跳过**（`_detect_fact()` 的跳过条件）：

- 两侧取值语义相同 —— 这一条**大部分**在分桶阶段就完成了：桶键是 `_canonical(取值)`，同桶的行永远不互相配对。跨桶只保证「canonical 文本不同」，不保证「语义不同」：`1` 与 `1.0` 文本不同、落进不同桶，仍会被比较一次，随后由 `_classify_object_conflict()` 开头的 `_same_value()`（`_normalize_for_compare()` 语义）判为无冲突。代价是常数级的多比较，**不误报**；反方向没有反例——canonical 文本等价强于 `_normalize_for_compare`，不会「同桶但语义不同」而漏报。所以循环里不再逐对调用 `_same_value()` 是对**同桶**成立的省法，跨桶那一对仍要过语义判断；
- 一条的 `supersedes_id` 指向另一条（新旧取代关系）；
- 两条断言的有效期不重叠（`_overlap()`）；
- 任一条的 `epistemic_mode` 为 `author_plan`（作者计划不算矛盾）。

其余情况按 `_classify_object_conflict()` 判定：

| 条件（按 `_classify_object_conflict()` 的实际判定顺序） | 严重度 | 标题 |
| --- | --- | --- |
| ① 任一侧不是 JSON 对象（标量、数组、字符串） | high | `<predicate> 存在两个不同取值` |
| 至少一侧带非空 `attribute`，但两侧槽位不同（含「有槽位 vs 未分类」） | — | 跳过，不构成矛盾 |
| 槽位相同，任一侧有 `value`，且两侧 `value` 语义不同 | high | `属性「<attribute>」存在两个不同取值` |
| 槽位相同，取值相同或双方都未记录 `value` | — | 继续看名字漂移 |
| ② 两侧都没有 `attribute` 槽位，且 `_has_slot_semantics(predicate)` 为真（`has_attribute` / `has_story_fact`） | — | 跳过配对，继续看名字漂移 |
| 两侧都没有槽位，且谓词不带槽位语义（`located_in`、`age`、`owns_item` 等单值谓词） | high | `<predicate>` 存在冲突取值 |
| 两侧 `name` 都非空且不同 | high | 同一实体被记成两个名字 |

注①：仅当两条断言落在同一 `(实体, 谓词, 槽位)` 分组时才进入该分支——`_detect_fact()` 先按 `(subject_entity_id, predicate, attribute)` 分组、非对象断言的槽位记为 `""`，所以「带 `attribute` 的对象」与「字符串」永远不同组、不会被配对。

注②：这一支是数据形状与**槽位语义**的合流点，也是「白名单不等于可以空放行」的地方——`ASSERTION_PREDICATES` 只保证谓词名合法，不保证 `attribute` 槽位存在。模型显式写 `has_attribute` 却不给槽位时，落库对象只有 `{name,type,note}`，分组键里的槽位是 `""`，于是同一实体名下 N 条互不相干的属性事实会被两两判成「取值冲突」，这正是修复前的假阳来源。现在两道防线：写入端 `assertion_predicate()` 在「请求 `has_attribute` 但 `assertion_attribute()` 为空」时回落成 `has_story_fact` 并把原值作为被拒谓词回传（`ensure_memory_assertion()` 据此记 `memory.assertion_predicate_fallback` 事件，`reason` 为「谓词缺少 attribute 槽位，已回落到结构化默认值」）；引擎端这一支兜住库里已有的历史行。

判定依据是**数据形状 + 谓词是否带槽位语义**，不是「谓词名 → 严重度」的硬编码表：只要 `object` 里带 `attribute` 槽位，`has_attribute`、`has_story_fact` 与将来新增的带槽位谓词一律按「同槽位才比较」处理；两侧都没有槽位时，再按谓词是否带槽位语义分流（注②）。带槽位语义的谓词集合由 `_SLOT_SEMANTIC_PREDICATES = frozenset({"has_attribute", "has_story_fact"})` 定义，**目前只有这两个成员**，判定入口是 `_has_slot_semantics()`；将来新增带槽位谓词时必须同步该常量，否则新谓词会被当成单值谓词、两侧无槽位时直接判 high，假阳会重新出现。`note` 不参与 fact 判定——同槽位、同值、`note` 不同不报，两条不同事实本来就可以有各自的描述。

`evidence` 含 `entity_id`、`assertion_ids`（冲突的两条）、`attribute`（无槽位时为 `null`）与 `objects`（两侧取值）。

**为什么分桶而不是两两比较**：逐对比较时，同一槽位里重复抽出的同值断言会两两进循环、每次都要算一遍规范值（`_canonical()` → `json.dumps`），成本是组内条数的平方。实测在 20,111 条同槽位副本上耗时 7.31 s，而同一批数据分散到独立实体时只要 0.46 s——差别全在组内条数。分桶后同值行根本不进配对循环，复杂度从「组内条数²」降到「桶大小之积」`Σ_{i<j} m_i·m_j`，20,111 条同槽位副本降到 0.186 s，倍数关系也从每次翻倍 ×3.9 变成 ×2.2。

**这个说法有边界，不能读成「同槽位 N 条事实变成线性」**：桶大小之积只有在同值副本占多数时才近似等于 finding 数；取值全不同时每桶 1 条，配对次数仍是 `C(n,2)`，最坏情形仍是 O(n²)（n=10/20/40 → 45/190/780，来源同上）。7.31 s → 0.186 s 是「同槽位塞满同值副本」这一输入形态的实测，不是所有输入的结论。另外分桶键 `_canonical()` 与语义等价 `_same_value()` 不是同一套关系：`1` 与 `1.0` 跨桶、多比较一对，但不会多报冲突（`_classify_object_conflict()` 开头仍走 `_same_value()`）。等价性不是靠推理保证的：新旧实现在 21 个（库, 分支）组合与一个「3 个取值 × 50/40/30 条重复」的最坏用例上逐条比对，finding id 集合、分数、`examined` 全部一致。
回归证据（`tests/consistency_fixtures.py`，直接断言 `analyze()` 与 `_classify_object_conflict()`）：同一实体 10 条互不相干的事实 → 0 findings / 100 分 / A；同一实体 5 个不同槽位 → 0 findings / 100 分 / A；同槽位不同取值 → 恰好 1 条 high / 92 分 / B；名字漂移 → 1 条 high；历史无槽位 `has_attribute` 行（3 条、`note` 各不相同）不产生任何 finding——纯净情形为 0 findings / 100 分 / A；同槽位 50/40/30 条三取值 → 恰好 4700 条 high（`50×40 + 50×30 + 40×30`），同一实体 2000 条完全相同的取值 → 0 findings。最后一条对应 `tests/consistency_fixtures.py` 的 p8 用例：它把这同样的 3 条与一对 `located_in` 单值谓词并列，报告整体是 92 分 / B，那 1 条 high 来自 `located_in 存在冲突取值`；用例同时断言「没有任何以 `has_attribute` 命名的 finding」，锁住的正是「无槽位带槽位语义谓词跳过配对」这一支。

写入端与此配套：`ensure_memory_assertion()` 在记忆带属性槽位时写 `has_attribute`，否则写 `has_story_fact`（谓词白名单与回落见「已知局限」第 14 条）。

### 2. 时间线矛盾（timeline）

`app_timeline_event` 中状态为 `abandoned` 或 `cancelled` 的事件跳过后续检查（`_detect_timeline()` 的 abandoned/cancelled 分支），但仍计入 `examined`（`_detect_timeline()` 的 `examined`）。规则：

| 条件 | 严重度 | 标题 |
| --- | --- | --- |
| 事件绑定的章节在 `app_deleted_node` 中 | high | 时间线事件挂在已删除章节上 |
| 事件的 `ordinal` 不是整数 | medium | 时间线事件的序位不是整数 |
| 同一 `ordinal` 上出现多个不同章节的事件 | medium | 同一序位挂着多个章节的事件 |
| 同一章节的事件标注了多个不同的 `event_time` | medium | 同一章节出现多个故事时间 |
| `assertion` 的 `valid_start` 与 `valid_end` 都是 ISO 日期且起点晚于终点 | high | 事实的有效期起点晚于终点 |

实现见 `_detect_timeline()`。非整数 `ordinal` 不会进入同序位比较，但若事件带 `chapter_id` 与 `event_time`，仍会参与同章节多时间检查。证据字段分别为 `event_id`/`chapter_id`、`event_id`/`ordinal`、`ordinal`/`event_ids`、`chapter_id`/`times`、`assertion_id`。

### 3. 知识越界（knowledge）

读取 canon 断言，并通过断言 `evidence_json` 中第一个带 `candidate_memory_id` 的对象关联 `candidate_memory`（表不存在时该关联为空，检测器不产生结果）。`known_by` 从来源记忆 payload 的 `known_by` 或 `knowledge_scope.known_by` 读取（`_known_by_of()`）；没有记忆或 `known_by` 为空时跳过该断言（见 `_detect_knowledge()` 的 `known_by` 判空）。

| 条件 | 严重度 | 标题 |
| --- | --- | --- |
| `epistemic_mode='character_belief'`，且实体名与实体 id 都不在 `known_by` 中 | high | 角色相信自己不知道的事 |
| `epistemic_mode='world_truth'`，但来源记忆 `private` 或 `knowledge_scope.private` 为真 | medium | 局部认知被提升为世界事实 |
| `epistemic_mode='character_belief'`，且实体 `kind` 不是 `character` / `人物` / 空 | low | 非人物实体被标记为持有信念 |

实现见 `_detect_knowledge()`。第三条是独立的 `if`，可与第一条同时命中同一条断言。`world_truth` 的 payload 不是对象时按空对象处理，不会让整份报告失败。`evidence` 含 `assertion_id`、`memory_id`、`entity_id`、`known_by`（按规则略有差异）。

### 4. 属性漂移（attribute）

读取 `character_state`，按 `(entity_id, attribute)` 分组后两两比较（`_detect_attribute()`）。以下情况跳过（`_detect_attribute()` 的跳过条件）：

- 两条 `value_json` 原始字符串相同；
- 两条来自同一条非空 `source_assertion_id`；
- 两条有效期不重叠。

其余情况为 **high**：`属性「<attribute>」在重叠时间内出现两个取值`。`evidence` 含 `entity_id`、`attribute`、`character_state_ids`。检测器不核对两次变化是否由同一事件产生；有意变更如果没有有效期区分，会被报为 high。注意这里比较的是 `value_json` 原始字符串，键序或空白不同也可能触发（详情里的取值会先做规范化展示）。

### 5. 关系/称谓矛盾（relation）

读取 `app_story_relation`（`_detect_relation()`）：

| 条件 | 严重度 | 标题 |
| --- | --- | --- |
| 关系起点或终点在实体表中不存在 | high | 关系<起点/终点>指向不存在的实体 |
| 起点或终点实体 `status != 'active'` | high | 关系<起点/终点>指向已归档实体 |
| 起点与终点相同且关系 `status='active'` | medium | 实体与自己建立了关系 |
| 同一 `(from_entity_id, to_entity_id, relation_type)` 有多条 `active` 关系 | low | 同一对人物存在重复关系 |

缺失实体与归档实体按"起点/终点"各报一条。`evidence` 含 `relation_id`、`missing_entity_id` 或 `entity_id`/`status`，自指关系含 `relation_type`，重复关系为 `relation_ids`。

### 6. 伏笔断线（foreshadow）

读取 `app_plot_item` 与 `app_foreshadow_link`；`app_plot_item.archived` 列存在且为真时跳过该条目（`_detect_foreshadow()` 的 archived 跳过）。链接按 `kind` 分为 `plant`（埋设）与 `payoff`（回收）。

| 条件 | 严重度 | 标题 |
| --- | --- | --- |
| 有 payoff、没有 plant | high | 伏笔只有回收、没有埋设 |
| `stage='payoff'` 但没有任何 payoff 链接 | medium | 标记为回收却没有回收记录 |
| `stage!='payoff'`、有 plant 无 payoff，且当前最大章节序位 − 最晚 plant 序位 ≥ 15 | medium | 伏笔埋设超过 15 章仍未回收 |
| 没有任何链接、`stage='setup'` 且文本非空 | low | 伏笔没有任何章节链接 |

当前最大章节序位 `max_ordinal` 不包含已删除章节（`_build_context()` 的 `max_ordinal`），避免幽灵章节把伏笔误判为超期。15 章阈值是写死的常量。`evidence` 含 `plot_id`、`payoff_ids` 或 `plant_ids`。

### 7. 来源失效（evidence）

读取 canon 断言的 `evidence_json`、`reviewed_by`、`reviewed_at`，并加载 `source_span` 与每个文档节点的最新 revision（`_detect_evidence()`）：

| 条件 | 严重度 | 标题 |
| --- | --- | --- |
| `evidence_json` 不是非空数组 | blocker | 已确认事实没有来源 |
| 缺少 `reviewed_by` 或 `reviewed_at` | blocker | 已确认事实缺少复核人 |
| 证据引用的 `source_span` 不存在 | high | 来源片段已不存在 |
| `source_span.status != 'valid'` | high | 来源片段已失效 |
| `source_span.revision_id` 不是该文档节点的最新 revision | medium | 来源片段属于旧版本正文 |

第一条命中后不再检查该断言的后续证据；缺少复核人不会中断后续 `source_span` 核对，因此同一断言可能同时报"缺少复核人"和来源问题。证据条目必须是对象且带 `source_span_id` 才会继续核对；其他形态的条目跳过。`evidence` 含 `assertion_id`、`source_span_id`，必要时附 `status`、`span_revision`、`latest_revision`。

> blocker 的两条规则在正常写入路径上通常不会触发：schema 触发器 `check_assertion_canon_insert` / `check_assertion_canon_update`（`docs/schema-v1.1-migration.sql:72-73`）已经要求 canon 断言必须有非空证据数组和复核人。历史库、导入库或迁移前的数据仍可能出现，因此发表门禁把 blocker 一并纳入。

## 发表门禁

导出终稿时，`export_finalization_issues()` 会在既有终稿检查（标题、简介、章节正文、本章审查、记忆确认、伏笔回收等）之后追加一致性门禁（`export_finalization_issues()` 末尾的一致性门禁循环）：

```python
for finding in consistency["findings"]:
    if finding["category"] in {"fact", "knowledge", "evidence"} and \
            finding["severity"] in {"blocker", "high"}:
        issues.append("一致性阻断：" + finding["title"] + "——" + finding["detail"])
```

规则：

- **拦截范围**：只有 `fact`、`knowledge`、`evidence` 三类中 `blocker` 或 `high` 的问题进入门禁。典型例子：同一实体被记成两个名字、角色相信自己不知道的事、来源片段失效、已确认事实没有来源。
- **不拦截**：`timeline`、`attribute`、`relation`、`foreshadow` 的 high，以及所有类别的 `medium` / `low`。这些在 v1 仍是提示级，因为误报率还没有压到可以阻断发表的程度。
- **为什么不是只拦 blocker**：blocker 只在 canon 断言缺证据或缺复核人时出现，而数据库触发器已经挡住这两种写入（见上）；只拦 blocker 等于门禁几乎永不触发。把三类 high 纳入，才能覆盖"事实自相矛盾、来源失效"这类真正影响发表可信度的问题。
- **门禁使用全量结果**：`export_finalization_issues` 调用 `analyze()` 时不传 `min_severity`，因此界面上的筛选不会放松门禁。
- **放行**：沿用既有作者覆盖机制。请求头 `X-Moyu-Export-Override` 提供不少于 8 个字符的理由（URL 编码的 UTF-8，解码后上限 2000 字符，`export_override_reason()`）即可放行；覆盖理由会写入导出 manifest 的 `author_override` 与导出事件。

不同导出入口的行为：

| 入口 | 行为 |
| --- | --- |
| `GET /api/export/epub`、`GET /api/export/markdown` | 存在问题且覆盖理由不足 8 个字符时返回 **409**：`{"error": "export blocked by finalization gate", "issues": [...], "override_required": true}`（`/api/export/epub` / `/api/export/markdown` 路由的 409 分支） |
| `GET /api/export/manifest` | 不返回 409；`epub_bytes()` 生成的 manifest 中 `finalization` 字段给出 `{"allowed": bool, "issues": [...], "overridden": bool}`（`export_manifest_for_state()` 的 `finalization`、`epub_bytes()`、`/api/export/manifest` 路由），供调用方在导出前自查 |

## CLI

```bash
.venv/bin/python -m backend.moyu_consistency \
  --db moyu.sqlite3 \
  --project <作品ID> \
  --branch <分支ID> \
  --min-severity high \
  --json
```

参数（`main()` 的 argparse）：

| 参数 | 说明 |
| --- | --- |
| `--db` | SQLite 数据库路径；默认取环境变量 `MOYU_DB`，再默认 `moyu.sqlite3` |
| `--project` | 作品 ID；省略时取数据库中第一个作品（按 rowid） |
| `--branch` | 分支 ID；省略时取该作品第一个 `active` 分支 |
| `--json` | 输出 JSON 报告（含 `all_findings`）；否则输出中文文本报告（`render_text()`） |
| `--min-severity` | `blocker` / `high` / `medium` / `low`，只裁剪 `findings`，不影响分数 |
| `--fail-on` | 存在该等级及以上的问题时以退出码 1 结束，用于 CI 门禁（`main()` 的 `--fail-on` 分支） |

默认作用域解析见 `_resolve_scope()`：数据库没有任何作品时报 `数据库里没有任何作品`，作品没有 active 分支时报 `作品 <id> 没有 active 分支`，数据库文件不存在时报 `数据库不存在：<path>`，均以非零状态退出。CLI 调用 `analyze()` 时会捕获 `ConsistencyScopeError`（消息 `项目不存在：<id>` / `分支不存在：<id>`，由 `analyze()` 的作用域校验抛出），打印「一致性检查失败：...」并以退出码 2 结束；若只给不存在的作品而省略分支，`_resolve_scope()` 会先报 `作品 <id> 没有 active 分支`。两种情况都不会返回 100 分 A。

退出码：

- `0`：分析完成，未触发 `--fail-on`；
- `1`：触发 `--fail-on`（存在该等级及以上问题）；
- `2`：显式传入不存在的作品或分支（`ConsistencyScopeError`）；
- 非零（`SystemExit`）：数据库不存在、没有作品或没有 active 分支。

`--fail-on` 检查的是 `all_findings`（完整清单），因此 `--min-severity` 不会放松 CI 门禁：例如 `--min-severity blocker --fail-on high` 仍会因 high 问题退出 1。`--json` 会输出 `all_findings`，HTTP 响应只返回 `findings`，调用方若只需要展示清单，应使用 `findings`。

## 已知局限

1. **规则式检测，没有语义理解。** 只检查已结构化的投影；同义改写、代称、隐含矛盾、跨章节推理都检测不到。
2. **缺表即跳过。** 旧库或裁剪过的库缺少某张表/列时，对应查询会被跳过，相关检查可能静默返回 0，分数可能虚高；用 `examined` 判断覆盖范围。
3. **事实比较依赖 JSON 结构。** `object_json` 按语义归一化比较（`_same_value`）：忽略 JSON 空白与键序，`1` 与 `1.0` 视为同一数值，布尔与数字区分；但数组顺序、大小写差异仍会被视为不同取值。非法 JSON 每条断言只报一次 medium「断言取值不是合法 JSON」，且不参与后续配对。事实只在同一 `(实体, 谓词, attribute)` 槽位内比较；`attribute` 缺失记为 `""`，与有槽位的断言之间不比较（漏报面，见第 14 条）。
4. **日期只做字符串比较。** 只识别 `YYYY-MM-DD` 开头的 ISO 日期；无法解析的边界按"重叠"处理（保守，可能误报），不做时区或历法换算。
5. **时间线没有全局推演。** 只比较同一 `ordinal`、同一章节内的 `event_time`；不推断跨章节的先后关系。`ordinal` 非整数的事件只报格式问题，不参与同序位比较，但仍会参与同章节 `event_time` 检查。
6. **知识越界依赖记忆链接。** 断言没有关联 `candidate_memory` 时不检查；`known_by` 只匹配实体名或实体 id，不匹配别名。
7. **属性变更不看事件。** 只要 `(entity, attribute)` 有效期重叠且 `value_json` 原始字符串不同就报 high；有意变更如果没有用有效期区分，会被误报。
8. **关系检查范围有限。** 只查端点存在/状态、自指和同向重复；反向重复（A→B 与 B→A 同类型）不报。
9. **伏笔阈值写死。** 15 章是硬编码常量；`max_ordinal` 不含已删除章节；没有章节链接的 plant 不参与超期计算；archived 条目跳过但计入 `examined`。
10. **来源最新版本按全库计算。** `latest_revision` 按 `document_revision` 的 rowid 取每个节点的最后一条，未按分支过滤；多分支并行时可能把本分支仍有效的原句标为旧版本。`source_span` 也按作品加载、不按分支过滤。`reviewed_by` / `reviewed_at` 只检查非空，不验证复核人身份或权限。
11. **分数是扣分制，等级另有封顶。** 问题多时分数归零，不同规模的作品不能直接横向比较；等级还受最严重问题封顶（blocker→最多 C，high→最多 B），因此分数相同不代表等级相同。它衡量的是"当前投影里的确定性矛盾"，不是文学质量。
12. **只读，不自动修复。** 引擎不提供一键修正；`suggestion` 需要作者手工处理。CLI 直接打开 SQLite，建议对正在写入的库使用副本。
13. **finding id 依赖文案。** id 由 `category|severity|title|key` 哈希而来；修改标题文案会改变 id，回归断言需要同步更新。
14. **断言谓词来自白名单，但白名单不保证槽位。** 写入端 `ensure_memory_assertion()` 只接受 `ASSERTION_PREDICATES`（当前是 `has_story_fact` 与 `has_attribute`）：记忆带属性槽位时写 `has_attribute`，否则写 `has_story_fact`；显式请求 `has_attribute` 却给不出槽位的，按「缺槽位」回落成 `has_story_fact`，`reason` 与「谓词不在白名单内」分开记，避免审计把缺槽位误读成模型乱写。模型给出的白名单外谓词不会落库，会回落到上面两种之一，并记一条 `memory.assertion_predicate_fallback` 事件（`ASSERTION_PREDICATE_FALLBACK_EVENT`，带 `requested` / `used` / `reason`）。真实稿件跑批时 `event_log` 会因此增长较快。这也是 fact 判定按「数据形状 + 谓词是否带槽位语义」而不是谓词名走的原因：谓词会随写入端演进，槽位与槽位语义不会（新增带槽位谓词时记得同步 `_SLOT_SEMANTIC_PREDICATES`）。
15. **谓词按字符串精确匹配，不做大小写归一。** `_has_slot_semantics()` 只把参数 `str(predicate or "").strip()` 后丢进 `_SLOT_SEMANTIC_PREDICATES` 查表，不调用 `lower()`：首尾空白由 `strip()` 吸收，但大小写不会被归一，所以谓词必须是已规范化的小写形式：`Has_Attribute`、`HAS_ATTRIBUTE` 这类写法查不到集合里，会被当成「不带槽位语义的单值谓词」——两侧都没有槽位时直接判 high，假阳会以「大小写不同」的形式复现。正常写入走 `assertion_predicate()` 的小写白名单不会落库这种值；手工导入、外部脚本或迁移脚本直接写 `assertion` 表时没有这层保护。
16. **两份谓词名单靠人工同步。** 写入端 `ASSERTION_PREDICATES`（决定放行与回落）与引擎端 `_SLOT_SEMANTIC_PREDICATES`（决定两侧无槽位时是否跳过）各维护一份，新增带槽位谓词时漏改任意一侧都会出问题：漏改引擎端，新谓词被当单值谓词、无槽位时判 high（假阳）；漏改写入端，新谓词落不了库、全部回落成结构化默认值。把两份名单抽成公共常量，或在导入期加一条一致性断言（两个集合相等、或引擎端是写入端子集），是可选的后续改进——目前只靠「维护约定」里那条人工纪律兜着。

## 与续写引擎的关系

一致性引擎（`backend/moyu_consistency.py` 的 `analyze()`）与多轮字数预算续写引擎（`backend/moyu_continuation.run_budgeted_continuation()`）**互不调用**：前者只读，后者只产候选。读者容易把两者想成「边写边查」，实际不是。

- **续写产物不是作品数据。** `run_budgeted_continuation()` 的返回值带 `candidate_only: True`，只把各轮正文拼成候选文本，不写正文、不写 canon、不写故事投影。生产链路把它写进 `ai_run` / `app_ai_job` 的结果与 `app_ai_chunk` 分片账本，这些都不是 `analyze()` 的检测对象。
- **多轮生成不经过一致性检查。** `analyze()` 在生产链路上只有两个调用点：`GET /api/consistency`（工作台面板与接口的主动检查，见上文「入口」）与 `export_finalization_issues()`（导出终稿的发表门禁，见上文「发表门禁」）。生成过程中没有任何一处调用它，所以第 2、3 轮产出的正文不会被实时拦截。
- **作者采纳后才进入断言体系。** `apply_candidate()` 通过后正文写入章节，之后的章后资料、断言与投影才能被 `analyze()` 读到。一致性结论永远落在「已经进入作品的数据」上，而不是「模型刚返回的文本」上。

由此有两个必须说清的误读：

1. 面板显示 100 分 A，**不代表刚生成的多轮候选没有矛盾**——候选还没进投影，引擎根本没检查过它。
2. 多轮续写可能在后几轮引入与前文冲突的事实，这些矛盾要等作者采纳并完成章后资料/断言写入后，下一次 `/api/consistency` 或导出时才暴露。要在采纳前发现，只能由作者自己对照设定；采纳后想确认，就手工重跑一次检查。

反方向也不成立：一致性引擎的 `suggestion` 不会回灌给续写引擎当下一轮提示。两条引擎之间没有自动闭环，唯一的串联点是作者。

## 维护约定

- 修改检测规则、严重度、权重或报告字段时，同步更新本文件与 `tests/consistency_fixtures.py` / `tests/consistency_http_fixtures.py`。
- 新增检测器时同时更新 `CATEGORY_LABELS`、`DETECTORS`、`examined` 统计与前端 `CATEGORY_ORDER`，并保持 `analyze()` 的返回字段兼容。
- 不要把 `min_severity` 的过滤逻辑移到分数或统计计算之前；`tests/consistency_http_fixtures.py` 的 `test_min_severity_filters_without_inflating_score` 会检查这一点。
- 保持 `all_findings` 与 `findings` 的区分：`findings` 用于展示，`all_findings` 用于门禁与回归。
- 引擎必须保持只读与确定性：同一数据库重复运行应得到相同 `score`、`counts` 与 finding id。
- 新增或删除带槽位语义的谓词时，必须同时改 `backend/server.py` 的 `ASSERTION_PREDICATES`（写入端放行名单）与 `backend/moyu_consistency.py` 的 `_SLOT_SEMANTIC_PREDICATES`（引擎端跳过依据）；两个常量任何一侧漏改，都会让「无槽位」的同类事实重新被判成取值冲突（假阳）或让真冲突漏报。
