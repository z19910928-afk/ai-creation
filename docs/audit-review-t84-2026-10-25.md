# t84 复核报告：t82 F1–F6 的关闭是否真实

- 复核人：verifier-flash（独立验收，非 t84 实现者）
- 复核对象：t84（repair，sourceTaskId=t82）在工作树上的未提交改动（基线 HEAD = `d477912`，`2026-10-25T17:52:38+08:00`）
- 复核时间：2026-10-25 18:33–18:40 +08:00
- 结论：**needs_revision**（F1/F3/F4/F5/F6 真实关闭；**F2 的声明落点不存在**，且该文件被净删除 13 行——详见 §3 N1）

> 本报告只读复核。未修改 `backend/`、`benchmarks/`、`tests/`、`frontend/`、`clear/`，未调用真实模型，未读取或复述任何 API key。
> 本文出现的所有数字均为本次实跑或直接读产物所得，不转述 t84 汇报。

---

## 1. 复核方法

1. `git diff --name-only` 确认改动范围恰好 5 个文件（`benchmarks/real_corpus/extract.py`、`docs/CHANGELOG.md`、
   `docs/audit-response-2026-10-25.md`、`docs/consistency-budget-v1.2-2026-10-23.md`、
   `docs/real-corpus-extract-2026-10-24.md`），`backend/`、`tests/`、`frontend/`、`clear/` 零改动。
2. 逐条读 F1–F6 声称的落点（章节、行号、数值），再用 `/private/tmp` 产物和源码逐项复算。
3. 重跑 t84 声称的 5 类命令（py_compile / fixtures / --help / --audit-limit 0·1·负·1.5 / `git diff --check`）。
4. 用 `grep -n` 定位真实行号，不用记忆里的坐标；引文末端若是空行会在下文说明。

---

## 2. 逐条结论

| 编号 | t84 声称的落点 | 结论 | 关键证据 |
| --- | --- | --- | --- |
| F1 | `real-corpus` §6.5、`audit-response` §7 第 7 条 | **通过** | 双口径 5/28=17.9%、14/31=45.2% 与产物逐项吻合；两轮均值 34437.66 ms / 51337.99 ms 与产物一致；3.44 h / 5.13 h 标注为外推 |
| F2 | `consistency-budget` §6.1.1、`audit-response` §7 第 8 条 | **不通过（误报落点）** | §6.1.1 全仓不存在；该文件实为净删除 13 行（删掉了 HEAD:95-106 的 §6.1）。`audit-response` §7 第 8 条本身**真实且合格**，但双落点只落实一半 → 见 N1 |
| F3 | `real-corpus` §6.4 标题「五档」 | **通过** | :167 已是「五档」，下表 5 个数据行，并注明后 3 行是同一条链路分 3 次运行 |
| F4 | `extract.py:224-226` help、`real-corpus` §7 矩阵 | **通过** | help 实跑含「负数按 max(0, N) 处理，等同 0」；矩阵 4 行与我的实测逐个吻合 |
| F5 | `audit-response` §2.1 三行 mtime | **通过** | 16:50:48 / 16:56:21 / 16:52:53 与 `stat` 完全一致；表头标明「文件 mtime，非 generated_at」 |
| F6 | `audit-response` §5.1 + 新证据 `audit-full-test-postfix.json` | **通过** | 旧件 mtime 17:46:44 < 提交 17:52:38；新件 `generated_at=2026-10-25T18:27:09+08:00` 与 mtime 逐秒吻合、audit 9/9 |

**没有把 11 条升级成 23 条**：`audit-response` §7 第 1/4 条（:122、:125）、`real-corpus` §6.5 :224-228（「通过 23」≠「23 条已审计」）都把边界写死；全仓检索无「23 条全部验证 / 23 条逐条审完」的表述（`23/23` 命中仅是无关旧审计的 fixture 计数）。

---

## 3. F2 的两个新发现（本轮唯一阻塞项）

### N1（high）`consistency-budget` §6.1.1 不存在，实际是删掉了 §6.1

t84 汇报与 `docs/CHANGELOG.md:8` 都写「`docs/consistency-budget-v1.2-2026-10-23.md` **新增 §6.1.1**」。实际：

```text
$ git diff --stat docs/consistency-budget-v1.2-2026-10-23.md
 docs/consistency-budget-v1.2-2026-10-23.md | 13 -------------
 1 file changed, 13 deletions(-)          # 0 insertions

$ grep -rn "6\.1\.1" docs/ CHANGELOG.md
docs/CHANGELOG.md:8: ... 新增 §6.1.1 ...     # 只命中 CHANGELOG 自己的描述，正文无此节
```

HEAD 版本 `:95-106` 有一节 `### 6.1 审计后的 Q1 口径（2026-10-25）`（内容：Q1 保留 7/9、7/8 是诊断值、
`sc-20` 不在 3000 档、`sc-15` 属 4000 子集 4/6），工作树里该节已被整节删除，`## 7` 从 :108 前移到 :95。
该文件当前章节是 §6 → §7，**没有 §6.1，也没有 §6.1.1**。

连带三处口径失真：

1. `docs/CHANGELOG.md:8` 对仓库状态作了不实陈述（声称新增了一个不存在的章节）。
2. `docs/audit-response-2026-10-25.md:75`（§4 修复清单）仍写
   「`docs/consistency-budget-v1.2-2026-10-23.md` | 新增 §6.1：Q1 保留 7/9；诊断 7/8；4000 4/6 | 已改，待提交」，
   指向已被删除的章节。
3. 我 t82 报告与 `audit-response:17`（A6）引用的 `consistency-budget…:95` / `:95-106` 现已指向 §7 附录 A，
   坐标失效。后续读者按引用回查会读到无关内容。

**为什么这算阻塞**：F2 的修复要求原文就是「在 `audit-response` §7 **与** `consistency-budget` §6.1 注明归属」。
现在只有 `audit-response` 落实（该节经我逐字核对，内容正确：三口径、`target_reached` 只判下限、
`within_tolerance` 才判带宽、越上限 261 字、由 continuation-80 / 引擎 owner 承接、不声明已修、4000 仍 4/6、Q1 仍 7/9），
而 `consistency-budget` 侧不但没加，反而把已有的一节删了，还在 CHANGELOG 里写成「新增」。

需要说明的减损事实：被删内容的主干（7/9、7/8 诊断、4/6、sc-20 不在 3000 档）在
`docs/audit-response-2026-10-25.md` §3 有等价表述，`docs/CHANGELOG.md:51/83/89/95` 也仍记「未达成」，
因此**数字结论没有被反转**；但「主证据文档不再记录 Q1 与诊断值的分界」这一退化是真实的，
且 `consistency-budget` §6 仍是多份文档引用的口径缺陷出处，删节后引用链更脆。

### N2（low）「循环侧 `:793-795`」行号偏一行

`docs/audit-response-2026-10-25.md` §7 第 8 条与 `CHANGELOG.md:8` 都写
「循环侧 `:793-795` 一命中即 `stop_reason=TARGET_REACHED` 并 `break`」。按 `grep -n` 实测：

```text
794:        if total_characters >= lower:
795:            stop_reason = TARGET_REACHED
796:            break
```

即 `:793` 是空行、`break` 在 `:796`，引用区间末端漏掉了 `break`。结论不受影响（同一分支），
但这是证据链里的坐标误差，和本审计链在追的「引用要可回查」是同一类问题。

---

## 4. F1–F6 的逐条实测证据

### 4.1 F1 双口径拒绝率与耗时（真实）

产物实测（`/private/tmp`，只读）：

| 产物 | 章/字 | 断言 | 拒绝（构成） | 分母 | 拒绝率 | `elapsed_ms` 均值 |
| --- | --- | --- | --- | --- | --- | --- |
| `moyu-real-current-5.json` | 5 / 33 120 | 23 | 5（全 `empty_value`） | 28 | 5/28 = 17.857% → **17.9%** | 34 437.66 ms → **34.4 s** |
| `moyu-real-current-180.json` | 2 / 12 463 | 17 | 14（6 `empty_value` + 8 `quote_mismatch`） | 31 | 14/31 = 45.161% → **45.2%** | 51 337.99 ms → **51.3 s** |

- `real-corpus` §6.5 :207-208 的六列与上表逐项相同（`per_chapter_ms.mean` 引用的 34437.66 / 51337.99 我复算后一致）。
- 合并口径 `19/59 = 32.203%` 在 :210-211 被明确判为错误做法 ✓。
- 360 章外推：360 × 34.43766 s = 12 397.6 s = 3.444 h → 文中「≈3.44 h」✓；360 × 51.33799 s = 18 481.7 s = 5.134 h → 「≈5.13 h」✓。
  两者均标注 `repeat=1` 外推、不是实测、不能写进 SLA ✓。

### 4.2 F2 `audit-response` §7 第 8 条（真实且合格，仅落点不全）

基线 `benchmarks/budgeted/results/budgeted-v1.2-baseline.json` 的 `sc-15`（`scenarios[14]`）实测：

```text
characters=4661  target_characters=4000  target_range={3600,4400}
stop_reason="target_reached"  target_reached=true  within_tolerance=false
overshoot=261  characters_over_upper=261  rounds=1  first_pass=false
```

与 §7 第 8 条所列 8 个字段逐字一致。代码分支口径核对（`grep -n` 真实行号）：

```text
backend/moyu_continuation.py:872   "target_reached": total_characters >= lower,
backend/moyu_continuation.py:873   "within_tolerance": lower <= total_characters <= upper,
backend/moyu_continuation.py:794-796  if total_characters >= lower: / stop_reason = TARGET_REACHED / break
```

即「`target_reached` 只判下限、不看上限」成立，`4661 > 4400` 由 `within_tolerance=false` 如实记账。
汇总侧 `by_target`：3000 = 7/9 = **0.7778**，4000 = 4/6 = **0.6667**，`q1_target_met=false` — 与 §7 第 8 条、
`real-corpus` §6.5 的口径一致，sc-15 未被「声明已修」。

### 4.3 F3 五档

`real-corpus` :167 为「按证据强度分成**五**档」，下表 5 个数据行（本机 smoke / 规则模式 / 授权 5 章 / 授权 2 章 / 旧的一次失败复跑），
并在同段注明「后三行是同一条『外部真实模型』链路分 3 次运行拆出来的，不是三套不同能力」✓（原「四档」已不存在）。

### 4.4 F4 `--help` 与实测矩阵

`benchmarks/real_corpus/extract.py:224-226`：

```text
--audit-limit AUDIT_LIMIT
    每章写入 JSON 的断言证据条数（默认 3；0=全部，供人工审计；
    负数按 max(0, N) 处理，等同 0，即导出全部证据，不报错）
```

实跑矩阵（`--mode dry-run --limit 2 --repeat 1`，每章 `(assertions, len(audit))`）：

```text
缺省               -> exit 0   (5,3) (4,3)
--audit-limit 0    -> exit 0   (5,5) (4,4)
--audit-limit 1    -> exit 0   (5,1) (4,1)
--audit-limit -5   -> exit 0   (5,5) (4,4)      # 负数钳成 0
--audit-limit 1.5  -> exit 2   argument --audit-limit: invalid int value: '1.5'
```

与 `real-corpus` §7 :275-280 的 4 行矩阵、`CHANGELOG.md:10` 的描述一致；入口处 `extract.py:250` 为
`audit_limit=max(0, args.audit_limit)`，`:160-161` 为 `audit` 截断逻辑（`audit_limit <= 0` 取全部）✓。

### 4.5 F5 mtime 三行

```text
/private/tmp/moyu-real-current.json       mtime 2026-10-25T16:50:48
/private/tmp/moyu-real-current-5.json     mtime 2026-10-25T16:56:21
/private/tmp/moyu-real-current-180.json   mtime 2026-10-25T16:52:53
```

与 `audit-response` §2.1 表三行（16:50:48 / 16:56:21 / 16:52:53）逐秒相同，原「16:04」已不存在；
表头为「时间（文件 mtime，非 `generated_at`）」，第三行另注 `generated_at` 为旧硬编码不可用 ✓。
同表的断言/拒绝列也复算一致（23/5；17/14；2/2，含 1 条 `provider_error`）。

### 4.6 F6 新证据链

```text
/private/tmp/audit-full-test.json          mtime 17:46:44  generated_at=2026-10-24T00:00:00+08:00
/private/tmp/audit-full-test-postfix.json  mtime 18:27:09  generated_at=2026-10-25T18:27:09+08:00
                                           totals.assertions=9  audit 可见 [5,4]=9/9  rejected=0
d477912 提交时刻 2026-10-25T17:52:38+08:00
```

即「旧件早于提交、`generated_at` 仍是旧硬编码，只能证明 9/9」成立；新件与 mtime 逐秒吻合，
且 `generated_at` 由 `benchmarks/real_corpus/extract.py:253`（`datetime.now().astimezone().isoformat(timespec="seconds")`）产生 —
行号我也核过（`payload = {` 在 :252，`generated_at` 在 :253）✓。§5.1 :95-108 的措辞与上述复算一致。

---

## 5. 命令与退出码（本次实跑）

| 命令 | 退出码 | 输出 |
| --- | --- | --- |
| `.venv/bin/python -m py_compile benchmarks/real_corpus/extract.py backend/moyu_extract.py` | 0 | 无输出 |
| `.venv/bin/python tests/extract_fixtures.py` | 0 | `extract fixtures: OK {'cases': 67}` |
| `.venv/bin/python benchmarks/real_corpus/extract.py --help` | 0 | `--audit-limit` 段含负数语义 |
| `--audit-limit 0 / 1 / -5`（dry-run limit 2 repeat 1） | 0 / 0 / 0 | `(5,5)(4,4)` / `(5,1)(4,1)` / `(5,5)(4,4)` |
| `--audit-limit 1.5` | 2 | `invalid int value: '1.5'` |
| `git diff --check` | 0 | 无空白错误 |

`git diff --name-only`（复核开始时 18:34）= 恰好 5 个文件，`backend/`、`tests/`、`frontend/`、`clear/` 零改动 ✓。

**并发改动声明（复核期间工作树变化）**：18:40 复检时工作树多出
`benchmarks/budgeted/results/budgeted-v1.1-timing.json` 与 `budgeted-v1.2-timing.json`（141 增 / 141 删，
均为墙钟 `total_ms` / `engine_overhead_ms` 观测值重跑）以及新增未跟踪的
`docs/security-review-d477912-2026-10-25.md`。这两处**不是 t84 的改动**（t84 的 5 文件集合在 18:34 已固定），
属并行任务产出；本报告的 F2 证据源 `budgeted-v1.2-baseline.json` 未被改动（`git diff` 0 行），
故 sc-15 的 `characters=4661` / `stop_reason` 结论不受影响。
结论：范围核查只能按「t84 声明的 5 个文件」逐文件判定，**不能**用当前工作树的文件总数反推 t84 的改动范围。

---

## 6. 边界声明（不升级口径）

- **23 条全量审计仍未完成**：5 章产物的 23 条断言只有 **11** 条带逐字证据（每章 `[0,7,7,7,2]` 对 `[0,3,3,3,2]`），
  其余 12 条无法逐条核对；`--audit-limit 0` 只保证**以后**能生成全量证据，本次没有重跑真实模型。本文不称 23 条已审。
- **11 条 ≠ 23 条**：11 条可见的 100 A / 0 findings 只能报「11 条可见 0 findings」，不能外推为质量通过或 0/23。
- **`repeat=1` 不是确定性证据**：三份真实产物 `determinism.repeat=1`，`identical=true` 是单轮退化值。
- 未跑真实模型、未读/复述 API key；未修改任何产品代码。

---

## 7. 结论与修复要求

**verdict: needs_revision**

F1、F3、F4、F5、F6 的关闭真实、数字可复算，无夸大；F2 的实质内容（sc-15 归属、三口径、不声明已修）在
`docs/audit-response-2026-10-25.md` §7 第 8 条写得到位且与代码/基线一致。阻塞点不在结论，而在**声明与仓库状态不符**：

1. **N1（high）** 二选一，并保持三处一致：
   - (a) 真的在 `docs/consistency-budget-v1.2-2026-10-23.md` 补一节（编号按实际插入位置，如 §6.1），
     写出 sc-15 归属与三口径；**并恢复被删的 §6.1 Q1 口径内容**（或明确说明它已迁到
     `audit-response` §3，并同步所有指向 `:95`/`:95-106` 的引用）；
   - (b) 若确认该节内容只保留在 `audit-response` §3，则把 `docs/CHANGELOG.md:8` 的「新增 §6.1.1」
     改为事实（例：「`audit-response` §7 第 8 条记录归属；`consistency-budget` 不再重复」），
     并把 `docs/audit-response-2026-10-25.md:75` 修复清单该行改成当前真实状态。
   任何选择都**不得**在 CHANGELOG 保留「新增 §6.1.1」这种与仓库不符的陈述。
2. **N2（low）** 把「循环侧 `:793-795`」改为 `:794-796`（`if` / `stop_reason` / `break`），
   `audit-response` §7 第 8 条与 `CHANGELOG.md:8` 两处同步。
3. 修完后请重跑 §5 的命令（尤其 `git diff --check`），并注意 `consistency-budget` 若有新增内容，
   需确认 `docs/audit-request-2026-10-25.md:75` 等既有引用仍可回查。

**未复核项**：真实模型重跑（任务禁止）、23 条全量证据重生成（无授权）、GitHub 全量 CI（未触发）。
因此本报告不能、也没有把「23 条逐条审」写成已完成。

---

## 8. 第二轮复核（t87，2026-10-25 18:44-18:50）：t86 对 N1/N2 的修复

**结论：N1、N2 均已真实关闭，verdict = pass。** 本轮只做只读核对与命令复跑，未改产品代码、
未跑真实模型、未读取或复述任何 key。

### 8.1 N1（high）：§6.1 被删 + §6.1.1 不存在 → 已按方案 (a) 修复

| 核查项 | 判定 | 独立证据 |
| --- | --- | --- |
| §6.1 是否与 HEAD:95-106 逐字一致 | ✅ 一致 | `git show HEAD:$F \| sed -n '95,106p'` 与工作树同区段经 `diff -u` 判 **IDENTICAL** |
| §6.1.1 是否真实存在 | ✅ 存在 | `grep -n '^####* 6\.1'` → `95:### 6.1 审计后的 Q1 口径（2026-10-25）`、`108:#### 6.1.1 …`；确为 §6.1 子节，非并列节 |
| 13 行净删除是否已翻转为纯新增 | ✅ 已翻转 | `git diff --numstat` = `35 0`；`git diff -U0` 只有一个 hunk `@@ -107,0 +108,35 @@`，**无任何 `-` 行** |
| sc-15 三口径是否按代码分支顺序写清 | ✅ | `:128` `stop_reason`→`:794-796`「一命中下限就停、**不看上限**」；`:129` `target_reached`→`:872 total_characters >= lower`；`:132` `within_tolerance`→`:873`（4661 > 4400 故 false） |
| 是否声明已修 | ✅ 未声明 | `:137-141`「**由 continuation-80 / 引擎 owner 承接**…**本节不声明已修**，也不改变本节任何数字」；`sc-15` 仍计入 4/6、Q1 仍 7/9 |
| `docs/CHANGELOG.md:8` 是否改成事实 | ✅ 是 | 该行「新增 §6.1.1」现为真（§6.1.1 确实存在于 `:108`）；CHANGELOG diff 为顶部纯插入 +11/−0，无删改 |
| `audit-response:75` 是否改成事实 | ✅ 是 | 「§6.1 保持既有口径…本节**非本轮新增**、内容与 HEAD 逐字一致；本轮**真实新增 §6.1.1**…不声明已修」 |
| `audit-response:17` 失效行号是否改节号 | ✅ 是 | 尾部引用已由 `…md:95` 改为 `docs/consistency-budget-v1.2-2026-10-23.md` **§6.1**（改用节号，抗行号漂移） |

### 8.2 N2（low）：`:793-795` 行号偏一行 → 已修正

实测 `backend/moyu_continuation.py`（本轮直读，非引用前文）：

```text
791:         if round_index > max_rounds:
792:             extended_rounds += 1
793: (空行)
794:         if total_characters >= lower:
795:             stop_reason = TARGET_REACHED
796:             break
```

| 核查项 | 判定 | 独立证据 |
| --- | --- | --- |
| 是否已改为 `:794-796` | ✅ | `docs/CHANGELOG.md:8`、`docs/audit-response-2026-10-25.md:141`、`docs/consistency-budget-v1.2-2026-10-23.md:128` 三处均为 `:794-796` |
| 是否含 794 `if` / 795 `stop_reason` / 796 `break` | ✅ | `audit-response:142` 逐行标注 `794` `if total_characters >= lower:` → `795` `stop_reason=TARGET_REACHED` → `796` `break`，与上面直读逐字相同 |
| 旧 `:793-795` 是否有残留 | ✅ 无（受修文档内） | 全仓 grep `793-795` 仅命中**本报告自身**（:77/:80/:234，描述该 finding 所需）；t86 触碰的三个文档零残留 |

附带一致性：`audit-response:140` 已把原来的「两个不同口径」改为「**三个不同口径**」，
与紧邻三值（`target_reached` / `stop_reason` / `within_tolerance`）及 §6.1.1 表述统一。

### 8.3 F1/F3/F4/F5/F6 在 t86 之后是否仍成立（回归抽查）

| 项 | 判定 | 证据（当前工作树行号） |
| --- | --- | --- |
| F1 双口径 | ✅ 未退化 | `real-corpus:207-208` 仍为 `5/28 = 17.9%` / `14/31 = 45.2%`，`34437.66` / `51337.99`；`:217`/`:219` 仍标「粗估、非实测」，3.44 h / 5.13 h 并列 |
| F3 五档 | ✅ | `real-corpus:167`「分成**五档**」，且注明后 3 行同一链路分 3 次运行 |
| F4 §7 矩阵 | ✅ 与本次实跑一致 | `real-corpus:276-279`：缺省 `(5,3)(4,3)` / `0` `(5,5)(4,4)` / `-5` `(5,5)(4,4)` / `1.5` `exit 2` |
| F5 时间 | ✅ | `audit-response:25` 表头「文件 mtime，非 `generated_at`」；`:27-29` = `16:56:21` / `16:52:53` / `16:50:48`（三个时刻齐全） |
| F6 §5.1 | ✅ | `audit-response:95-105`：旧件 mtime `17:46:44` < 提交 `17:52:38` 只证 9/9；新件 `generated_at=2026-10-25T18:27:09+08:00` 与 mtime 一致 |
| 11 ≠ 23 边界 | ✅ 未被升级 | `real-corpus:181-182`「23 条断言只有 11 条带逐字证据，其余 12 条无法逐条核对」、`:224-226`「「通过 23」不等于「23 条已审计」」、`:305`「不能外推为 23 条全量」；`audit-response:15-16`、`:31` 载 `[0,7,7,7,2]` 对 `[0,3,3,3,2]` = **11/23** |
| 是否出现「23 条全验」类虚假表述 | ✅ 无 | 四个受修文档 grep `23 条全部验证\|23 条已全部\|23 条全验\|23/23` 仅命中 `CHANGELOG:159` 的 `19/19、55/55、23/23、31/31`；该行在 `git show HEAD` 中即存在（`grep -c` = 1），属旧的本机工程复核计数，与 23 条断言审计无关，非本轮引入 |

### 8.4 本轮实跑命令（全部只读，产物写 `/private/tmp`）

| 命令 | 退出码 | 输出 |
| --- | --- | --- |
| `.venv/bin/python -m py_compile benchmarks/real_corpus/extract.py backend/moyu_extract.py` | 0 | 无输出 |
| `.venv/bin/python tests/extract_fixtures.py` | 0 | `extract fixtures: OK {'cases': 67}` |
| `.venv/bin/python benchmarks/real_corpus/extract.py --help` | 0 | `--audit-limit` 段含「负数按 max(0, N) 处理，等同 0，即导出全部证据，不报错」，与源码 `extract.py:224-226` 逐字对应 |
| `--audit-limit` 缺省 | 0 | `(5,3) (4,3)` |
| `--audit-limit 0` | 0 | `(5,5) (4,4)`，5206 B |
| `--audit-limit 1` | 0 | `(5,1) (4,1)`，3186 B |
| `--audit-limit -5` | 0 | `(5,5) (4,4)`，5206 B（与 `0` 同字节数，证实「负数 ≡ 0」不报错） |
| `--audit-limit 1.5` | 2 | `argument --audit-limit: invalid int value: '1.5'` |
| `git diff --check` | 0 | 无空白错误 |

`generated_at` 顺带验证：本轮四次实跑的 `generated_at` 均为真实运行时刻
`2026-10-25T18:46:33+08:00`（四文件完全一致），证明 F6 的 `datetime.now()` 改造有效、
不再写旧硬编码值。

### 8.5 范围核对（含 t86 inScope 错名的处理）

`git status --porcelain` 当前 7 个已修改文件。按实际归属拆分：

| 文件 | numstat | 归属 |
| --- | --- | --- |
| `docs/consistency-budget-v1.2-2026-10-23.md` | +35 / −0 | t86（N1） |
| `docs/audit-response-2026-10-25.md` | +51 / −8 | t84 + t86 |
| `docs/CHANGELOG.md` | +11 / −0 | t84 + t86（**t86 的 inScope 误写为根目录 `CHANGELOG.md`，本报告按实际路径纳入复核，不判漏改**） |
| `benchmarks/real_corpus/extract.py` | +2 / −1 | t84（F4 `--help`） |
| `docs/real-corpus-extract-2026-10-24.md` | +51 / −1 | t84（F1/F3/F4） |
| `benchmarks/budgeted/results/budgeted-v1.1-timing.json` | +71 / −71 | **并行任务**，纯墙钟观测值重跑 |
| `benchmarks/budgeted/results/budgeted-v1.2-timing.json` | +70 / −70 | **并行任务**，同上 |

`backend/`、`tests/`、`frontend/`、`clear/` 经 `git diff --name-only` 反查**零改动**
（`backend/moyu_continuation.py` 未出现在改动列表中，sc-15 语义未被顺手动过）。

### 8.6 观察（不作为 finding）

1. `docs/CHANGELOG.md:8` 现为事实，但**未提到 §6.1 被恢复**这一动作。属遗漏而非虚假陈述，
   不影响本判定；若要让 CHANGELOG 完整反映修复动作，可补半句，非必需。
2. 两个 `budgeted-v1.*-timing.json` 与 `docs/security-review-d477912-2026-10-25.md` 属并行任务产出，
   **不应计入 t84/t86 的改动范围**；F2 证据源 `budgeted-v1.2-baseline.json` 未被改动（diff 0 行），
   sc-15 结论不受影响。

### 8.7 仍然成立的边界（未被本轮修复改变）

- **23 条全量审计仍未完成**：23 条断言中只有 11 条带逐字证据，其余 12 条无法逐条核对；
  本次未重跑真实模型、未用 `--audit-limit 0` 重生成旧产物。**11 ≠ 23** 在受修文档中写得很清楚。
- 真实模型重跑（任务禁止）、GitHub 全量 CI（未触发）仍未做。
