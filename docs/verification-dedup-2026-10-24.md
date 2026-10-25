# t52 独立验收：一致性引擎「同槽位按取值分桶」去重（content-bound）

**结论：needs_revision。** 代码本身没有引入判定回归（同槽位两个不同取值仍报 high，不同槽位/跨实体仍不比较，同值重复确实不再配对）；但它**没有**把「同槽位 N 条事实」的比较次数从 O(n²) 降到 O(n)——同一槽位放 n 条**不同取值**时，实测配对次数仍是 C(n,2)（10/20/40 条 = 45/190/780），与本次验收要求「约为 n」直接冲突。详见 P1。

## 零、被测字节

`git status --porcelain` 显示 `M backend/moyu_consistency.py`，即被测对象是**工作区未提交内容**，不是某个 commit。

验收开始（`/tmp/t52_pre.txt`）：

| 文件 | sha256 | 行数 |
| --- | --- | --- |
| `backend/moyu_consistency.py` | `6358c672265a352f1b6a5b82e3d1f0eb4f650bebecef56d8799c302fcb15d422` | 1206 |
| `backend/server.py` | `319b4346ed418197d225ad63c8b34ecf6070105a07910f0e5677fac2bee3c810` | 8153 |
| `tests/consistency_fixtures.py` | `043b6ac5da07f69e61cd5d8c09f735b5990c7fab7e1af98b4c3b8075549b8f7a` | 640 |

验收结束（`/tmp/t52_post.txt`）：三个 sha256 与行数**逐字节相同**（`diff` 无输出，`BYTES_IDENTICAL`）。

**覆盖范围**：`_detect_fact` 的分桶去重逻辑（`backend/moyu_consistency.py` 内 `buckets` / 跨桶双重循环）及其判定语义；`tests/consistency_fixtures.py` 的既有断言与新增分桶用例。
**不覆盖**：`docs/consistency-engine.md` 与 `docs/real-corpus-acceptance-2026-10-24.md` 的表述（不在 inScope）；20,111 条真实语料的耗时复测；并发与多进程；`backend/server.py` 只参与编译与全量回归，未针对本次改动逐行审阅。

另：sweep 期间工作区新出现未跟踪文件 `tests/extract_fixtures.py`（`??`，mtime 20:49），不在本次三个绑定文件内；我单独跑过，exit 0（`extract fixtures: OK {'cases': 33}`）。

## 一、验证命令（我自己实跑的退出码）

| 命令 | 退出码 | 关键输出 |
| --- | --- | --- |
| `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py` | 0 | 无输出 |
| `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.330s` / `OK` |
| 全量 `tests/*_fixtures.py`（逐文件记录退出码） | 0 | 本轮 93 个文件 93 个 exit 0、0 FAIL；另补跑 sweep 后新增的 `tests/extract_fixtures.py` 亦 exit 0 |
| 自造探针 `/tmp/probe_t52.py` | 0 | `CHECKS=27 FAILS=0` |

## 二、配对比较次数实测（核心）

探针方法：包装 `backend.moyu_consistency._classify_object_conflict`（`_detect_fact` 经模块全局名调用），用真 SQLite（`server.init_db()`）+ 同实体/同谓词/同槽位 `has_attribute` 断言，直接数实际进入配对分类的次数。不引用实现者自述。

| 场景（同实体、同谓词、同槽位） | 实测配对次数 | 旧实现 C(n,2) | 结论 |
| --- | --- | --- | --- |
| n=10 条**不同**取值 | **45** | 45 | 无下降 |
| n=20 条**不同**取值 | **190** | 190 | 无下降 |
| n=40 条**不同**取值 | **780** | 780 | 无下降 |
| n=40 条**完全相同**取值 | **0** | 780 | 降为 0 |
| 2 种取值 × 各 20 条（共 40） | **400**（=20×20） | 780 | 降为跨桶积 |

不同取值时 n 翻倍，次数分别 ×4.22、×4.11——是二次增长，不是线性。

**算法解释**：实现按 `_canonical(value)`（`json.dumps(sort_keys=True)` 文本）分桶（`backend/moyu_consistency.py` `_detect_fact` 内 `buckets.setdefault(_canonical(value), []).append(row)` 与随后的 `for first in range(len(bucket_list))` / `for second ...`）。跨桶配对总数为

    Σ_{i<j} m_i·m_j = (n² − Σ m_i²) / 2

- 全部同值（桶数 k=1）：0；
- 取值全不同（k=n，每个 m_i=1）：n(n−1)/2，与旧实现完全相同；
- k 个取值各 n/k 条：C(k,2)·(n/k)² = Θ(n²)（k≥2 固定时），只是常数变小。

因此「O(n²) → O(n)」只在**所有取值相同**时成立；这不是「N 条事实」的一般结论。代码注释本身也只承诺「降到桶大小之积」，与实测一致；与任务验收写的「不同取值 10/20/40 → 约 n」不一致的是**验收口径**，不是代码行为。

## 三、判定语义回归（逐条实测）

| 用例 | 实测 |
| --- | --- |
| 同槽位两个不同取值（`左臂: 疤` vs `无疤`） | 比较 1 次；1 条 `high`；`92.0` / `B` |
| 同槽位 40 条同值 | 0 次比较；0 findings；`100.0` / `A` |
| 不同槽位（`左手` vs `右手`） | 0 次比较；0 findings |
| 跨实体（两个实体同谓词同槽位各一条，取值不同） | 0 次比较；0 findings |
| `located_in` 两个标量取值 | 1 条 `high` |
| 名字漂移（`沈砚` vs `沈墨`，同组） | 1 条 `high`，标题含「两个名字」 |
| `1` 与 `1.0`（语义同值、JSON 文本不同） | 比较 1 次（不同桶），0 findings——不误报，只多一次常数开销 |
| dict 键序不同（`{"a":1,"b":2}` vs `{"b":2,"a":1}`） | 同一桶；0 次比较；0 findings |
| `True` 与 `1` | 不同桶；1 次比较；1 条 `high`（布尔不等于数字） |

未发现「同桶但语义不同」导致的漏报：`_canonical` 的 JSON 文本等价性强于 `_normalize_for_compare`（后者把 1 与 1.0 视为同值），所以最坏情况只是多比较一对、由 `_classify_object_conflict` 内部的 `_same_value` 判为无冲突。我没有构造出「同一 canonical 文本、不同规范化取值」的反例。

## 四、问题清单

### P1 · high · 宣称的 O(n) 未达成，验收口径与实现能力冲突

- **文件/符号**：`backend/moyu_consistency.py` · `_detect_fact()` 内 `buckets` 分桶与跨桶双重循环；`tests/consistency_fixtures.py` 新增「同槽位分桶」用例（其 `expected_pairs = 50*40 + 50*30 + 40*30 = 4700` 正是二次项）。
- **复现**：`.venv/bin/python /tmp/probe_t52.py`，看第 1 节：n=10/20/40 不同取值 → 45/190/780。
- **问题**：验收要求「同一实体同一槽位放 10/20/40 条不同取值事实，配对次数从 n(n-1)/2 降到 ~n」。实测没有降。这不是实现缺陷——只要还要枚举「同槽位里任意两条不同取值」的全部 high，配对就至少是不同取值对数；若按槽位只保留一个代表，就会漏报真实矛盾。**该验收要求在保持现有 finding 语义的前提下不可满足。**
- **要求怎么修**：①把验收口径和所有对外表述改为「同值重复不再放大；不同取值仍按不同取值数二次（最坏 C(n,2)），本次只消除同值桶内部配对」；②如果产品确实要求 O(n log n)，需要先决定语义变更（例如每个槽位只报一条代表冲突、finding 数量从「每对一条」变为「每槽位一条」），并同步改测试与文档，不能只改性能叙述。
- **影响面**：对外宣称「一致性引擎已是线性」会误导容量评估；真实语料若同槽位取值高度重复，收益成立（20,111 条同值副本 → 0 次配对），但一旦取值分散就退回平方。

### P2 · low · 两套等价关系不同步，易被误用

- **文件/符号**：`backend/moyu_consistency.py` · `_canonical()` 与 `_same_value()` / `_normalize_for_compare()`。
- **问题**：`_canonical` 用原始 JSON 文本（`1` 与 `1.0` 不同桶），`_same_value` 语义上视为同值。当前用法安全（多比较、不误报），但两套等价关系并存且没有强制约束；后续若有人用 `_canonical` 直接判「是否同值」，会把 `1` 与 `1.0` 判成冲突。
- **复现**：探针第 5 节「1 与 1.0」用例：比较 1 次、0 findings。
- **要求怎么修**：在 `_canonical` docstring 明确「仅用于分桶/展示，语义等价一律走 `_same_value`」；或让分桶键改用 `(_normalize_for_compare(value))` 的稳定序列化，从根上消除两套等价关系。

## 五、已知边界

1. 只在 40 条量级做了计数复现；没有重跑 20,111 条真实语料的耗时（文档称 7.31 s → 0.186 s，属实现者自报，我未复核）。
2. 未审阅 `docs/consistency-engine.md` 与 `docs/real-corpus-acceptance-2026-10-24.md` 的口径（不在 inScope）；若它们写「线性」，需按 P1 一并修正。
3. 工作区存在他人未提交改动（`M backend/moyu_consistency.py`、`M tests/consistency_fixtures.py`、`M docs/*`），本报告只绑定上述三个文件的 sha256；提交后需重新绑定。
4. 未覆盖并发写入、跨项目串数据（本次改动是 `_detect_fact` 内的纯局部结构，跨项目由查询条件隔离，风险低但未实测）。

**字节绑定**：本结论只对 `backend/moyu_consistency.py` `6358c672…`（1206 行）、`backend/server.py` `319b4346…`（8153 行）、`tests/consistency_fixtures.py` `043b6ac5…`（640 行）负责。
