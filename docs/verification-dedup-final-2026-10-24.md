# 独立验收（t58）：同值分桶去重，按可满足合同重验并绑定当前字节

**结论：pass。** 同值副本桶内配对已被消除（10/20/40 条同值均 0 次配对），异值冲突不漏（45/190/780 与跨桶之积逐项成立），语义与复杂度口径经 t56/t57 修正后与实现一致；五条 verify 命令全过，三个被测文件验收前后逐字节一致。

## 零、被测字节（验收开始 / 结束各采集一次）

| 文件 | sha256 | 行数 |
| --- | --- | --- |
| `backend/moyu_consistency.py` | `b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2` | 1222 |
| `backend/server.py` | `319b4346ed418197d225ad63c8b34ecf6070105a07910f0e5677fac2bee3c810` | 8153 |
| `tests/consistency_fixtures.py` | `043b6ac5da07f69e61cd5d8c09f735b5990c7fab7e1af98b4c3b8075549b8f7a` | 640 |

采集命令（开始写 `/tmp/t58_pre.txt`、结束写 `/tmp/t58_post.txt`，`diff` 无输出、退出码 0）：

```bash
shasum -a 256 backend/moyu_consistency.py backend/server.py tests/consistency_fixtures.py
wc -l backend/moyu_consistency.py backend/server.py tests/consistency_fixtures.py
```

**三个文件验收前后逐字节一致（`BYTES_UNCHANGED`），verdict 绑定上表 hash。** 另记录两份被核对文档：`docs/consistency-engine.md` `90e8baf5ec7c4400c155c500e46f7ee7d657415bb0d8239208d440e210fb23f2`/388 行，`docs/real-corpus-acceptance-2026-10-24.md` `ddfef6f2cec712ce48d6b13b6fcbcc218e8436f12eac5968c8bc0b3949c5eb61`/205 行。

工作区未提交（`git status`：`M backend/moyu_consistency.py`、`M tests/consistency_fixtures.py`、`M docs/consistency-engine.md`、`M docs/real-corpus-acceptance-2026-10-24.md`）。

## 一、背景：t52 的失败不洗掉

t52 的验收合同把「同值桶内去重」写成「不同取值也约 n 次比较」。独立探针（本轮复跑）证明：在保留「每一对异值都产出一条 high finding」这一语义下，n 条互不相同的取值必然产生 `C(n,2)` 次配对——10/20/40 条实测 45/190/780，翻倍为 ×4.22 / ×4.11。该口径不可满足，t52 的 `needs_revision` 是正确结论，本报告不重新判定它。

t58 按修正后的合同重验：**同值副本桶内 0 次配对；异值最坏仍 O(n²)，这是保留逐对 finding 的必然下界，不是缺陷。** 本轮同时核对 t56（`_canonical` 注释）与 t57（两份文档口径）是否把这一区分写清。

## 二、验证命令（我自己实跑的退出码）

| # | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_consistency.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 3 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 3.435s` / `OK` |
| 4 | `.venv/bin/python /tmp/probe_t52.py` | 0 | `CHECKS=35 FAILS=0` |
| 5 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" \|\| exit 1; done` | 0 | 94 个文件，`TOTAL= 94 FAILS= 0` |

## 三、配对次数实测（探针包装 `_classify_object_conflict()` 直接计数）

探针：`/tmp/probe_t52.py`（本轮扩展了同值 N 扫描与三桶用例；不改被测代码，只读 import `backend.moyu_consistency`，每场景独立临时库 + `analyze()`）。计数方式是执行期替换模块里的 `_classify_object_conflict`，在真实调用链上累加，不是静态估算。

| 场景（同实体、同谓词 `has_attribute`、同槽位） | 实测配对次数 | 实测 findings | 旧实现 C(n,2) |
| --- | --- | --- | --- |
| 10 条完全同值 | 0 | 0（100/A） | 45 |
| 20 条完全同值 | 0 | 0（100/A） | 190 |
| 40 条完全同值 | 0 | 0（100/A） | 780 |
| 10 条互不相同 | 45 | 45（全 high） | 45 |
| 20 条互不相同 | 190 | 190 | 190 |
| 40 条互不相同 | 780 | 780 | 780 |
| 两桶 20/20 | 400 | 400 | 780 |
| 三桶 20/15/5 | **475** | 475 | 780 |

- 同值 N=10/20/40 的配对次数都是 **0**，桶内不再两两进循环——这是本轮要证明的修复本身。
- 异值 45/190/780，且 n 翻倍时 ×4.22 / ×4.11（探针断言区间 3.8–4.5），是二次增长。**worst-case O(n²)，不能宣称所有输入约 n。**
- 多桶场景等于跨桶大小之积：20×20=400；20×15 + 20×5 + 15×5 = **475**，与实测逐项相等。

## 四、判定语义回归（同一次探针运行）

| 检查 | 实测 |
| --- | --- |
| 同槽位两个不同取值 | 比较 1 次 → 1 条 high，`92.0 / B` |
| 40 条同值 | 0 findings，`100.0 / A` |
| 不同槽位 | 0 次比较、0 findings |
| 跨实体 | 0 次比较、0 findings |
| `located_in` 两条标量取值 | 仍 1 条 high |
| 名字漂移（同槽位、两侧 name 不同且都非空） | 仍 1 条 high，标题含「两个名字」 |
| `1` 与 `1.0` | 落在不同 canonical 桶、比较 1 次，但 `_same_value` 判同 → **0 findings（不误报）** |
| dict 键序不同 | 同一桶、0 次比较、0 findings |
| `True` 与 `1` | 不同桶、比较 1 次、仍判 high（布尔不归一成数字） |

结论：分桶只改变「谁和谁比」，没有改变「什么算冲突」。异值冲突、`located_in`、名字漂移的 finding 数不变；`1`/`1.0` 只多一次比较，不产生误报。

## 五、t56：只改注释的机械核对

t56 声称只给 `_canonical` 加 docstring、可执行逻辑零变更（1206 → 1222 行，+16）。我的核对方式与结果：

1. **相对 HEAD 的函数体 AST 比对**（剥离 docstring 后 `ast.dump`，`include_attributes=False`）：
   - `_canonical` → **SAME**
   - `_same_value` → **SAME**
   - `_classify_object_conflict` → **SAME**
   - `_detect_fact` → **DIFF**（预期：t52 的分桶去重就改在这里，早于 t56）
2. `_canonical` 当前实现仍是 `try: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` / `except (TypeError, ValueError): return repr(value)`——与 HEAD 逐字相同，新增内容全在 docstring。
3. 行数 1206（t52 采集）→ 1222（本轮采集）= +16；docstring 实测 15 行、488 字符，正好解释增量。
4. 行为等价：本轮探针的 45/190/780、400、475、0 与 t52 轮的数字逐项相同。

docstring 内容核对（不是「写了注释」就算）：明确写了 `_canonical` 是「分桶键 + 展示文本」而非语义等价、`1` 与 `1.0` 因 `json.dumps` 落不同桶、跨桶只多走一次比较且 `_classify_object_conflict()` 开头 `_same_value()` 会判同，并反向警告「不要把本函数输出当成是否矛盾的依据」。与实现层次一致。

## 六、t57：两份文档的复杂度口径

- `docs/consistency-engine.md`
  - `:171-178`：分桶键写的是 `_canonical(取值)`（纠正了原来的「规范化取值」用词），并明确「跨桶只保证 canonical 文本不同」，`1`/`1.0` 仍过一次 `_same_value()`。
  - `:203-205`：「复杂度从组内条数² 降到桶大小之积 `Σ_{i<j} m_i·m_j`」之后紧跟边界段：取值全不同仍是 `C(n,2)`、最坏 O(n²)、n=10/20/40 → 45/190/780，且 7.31 s → 0.186 s 只对「同值副本密集」成立。
- `docs/real-corpus-acceptance-2026-10-24.md`
  - 概述表 `:15` 已加「取值全不同时仍是 C(n,2)」限定；§5 标题 `:94` 改成「只消掉同值副本那一半，取值全不同仍是 O(n²)」。
  - `:109` 的「回到近似线性」限定为「在这张表所测的输入形态下（每组 181 份取值完全相同的副本）」，并明写不能外推。
  - `:111` 给出未解决边界 45 / 190 / 780 与 ×4.22 / ×4.11，来源指到 t52 报告 §二；`:135-138` 用分桶公式说明同值 0、全不同 C(n,2)、k≥2 取值 Θ(n²)。
- 两份文档均**没有**笼统的「N 条同槽位事实变线性」宣称；`grep -nE '(backend|tests|frontend|benchmarks)/[A-Za-z0-9_./-]+\.py:[0-9]+'` 两文件无输出（退出码 1），无裸行号。

## 七、问题清单

### P1 · low · `docs/real-corpus-acceptance-2026-10-24.md:150` 引用错节号与错 P 编号

- 原文：「t52 探针实测：`1` 与 `1.0` 比较 1 次、0 findings（来源：`docs/verification-dedup-2026-10-24.md` §三 与 §五 P3）」。
- 实际：t52 报告中该证据在 **§三「判定语义回归」** 与 **§四 P2**（`§五` 是「已知边界」，且 t52 只有 P1/P2，没有 P3）。
- 复现：`grep -n '^## \|^### ' docs/verification-dedup-2026-10-24.md` → 有 §四 P1/P2；`sed -n 150p docs/real-corpus-acceptance-2026-10-24.md`。
- 要求：改成「§三 与 §四 P2」。这是引用精度问题，不影响行为与复杂度结论，故不阻断本轮 pass。

## 八、已知边界

1. **无法在字节级证明「t56 相对 t52 的差量只有注释」**：t52 采集的 `6358c672…/1206` 只存了 hash 与行数，没有保存文件副本，工作区已覆盖。本轮用「HEAD AST（`_canonical` 等三函数体 SAME）+ 行数 +16 与 docstring 15 行吻合 + 行为数字逐项相同」三重证据替代；若需要严格 diff，需要在 t56 改前留快照。
2. **探针计数的是 `_classify_object_conflict()` 的调用次数**，不是 `json.dumps` 次数；分桶键计算发生在配对循环外，故两者在「配对次数」这一层可比，但不代表 CPU 时间的完整模型。
3. **性能数字（7.31 s → 0.186 s）未复测**：本轮只验证复杂度口径与文档表述，未重跑 20,111 条同槽位副本的数据集；该数字仍是 t57/既有报告的口径。
4. **全量 sweep 只证明退出码**：94 个 `tests/*_fixtures.py` 全部 0，未逐个人工阅读断言内容；一致性相关两个 fixture（`consistency_fixtures.py`、`consistency_http_fixtures.py`）另行实跑复核。
5. 未覆盖：`backend/` 其余文件的改动、`docs/CHANGELOG.md` 与前端；均不在 t58 scope。

## 九、结论

**pass。** 证据链：同值 10/20/40 → 0 次配对、0 findings、100/A；异值 45/190/780 与跨桶之积 400/475 逐项成立；语义回归 9 项无变化；t56 注释-only 有三重机械证据；t57 两份文档保留实测加速的同时写明 worst-case O(n²)，无笼统线性宣称；五条 verify 命令全过；三个被测文件验收前后 sha256+行数完全一致。verdict 绑定 `backend/moyu_consistency.py` `b855e910…`/1222、`backend/server.py` `319b4346…`/8153、`tests/consistency_fixtures.py` `043b6ac5…`/640。

唯一 finding 是 P1（low，文档引用错节号/P 编号），不改变本判定。
