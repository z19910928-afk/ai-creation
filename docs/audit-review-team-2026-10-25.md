# 独立复核：d477912「真实语料审计证据与 Q1 口径」

复核方：verifier-flash（独立验收员，未参与 d477912 的实现）
复核日期：2026-10-25
复核对象：`codex/repository-layout` @ `d477912`（`fix: make real-corpus audit evidence and q1 scope verifiable`，父提交 `c7d2841`）
复核性质：**只读复核**。未修改 `backend/`、`benchmarks/`、`tests/`、`frontend/`、`clear/` 下任何文件；未调用任何真实模型接口；未读取、未复述任何 API key 或凭据；`/private/tmp` 的真实模型产物只读不改（本次自建的复现产物另存 `vf-*` 前缀文件）。

结论：**needs_revision（不予放行）**。提交对工单的核心事实纠正成立，六项核查中的 2、3、4、5、6 项全部通过（含一次独立复现）；但工单 §2.2 后半段与 §3(b) 的两条实质诉求既未处理、也未进入回复的「仍未关闭」清单，而这两条正是本工单要防的那类「文档与事实脱节」。findings 见 §5，修复成本均为文档级。

---

## 1 复核方法

不把任何一方的自述当证据。判定顺序固定为：工单原文 → 提交 diff → 源码分支顺序 → `/private/tmp` 产物字段 → 独立重跑 → 文档表述。工单与提交冲突处以可复算产物为准；文档与代码冲突处以代码为准（按 `grep -n`/`sed -n` 的真实行号引用）。

环境事实：HEAD = `d477912`；复核开始时工作树只有未跟踪的 `docs/audit-request-2026-10-25.md`，本报告是本次唯一新增文件（现 `git status --porcelain` 为 `?? docs/audit-request-2026-10-25.md` 与 `?? docs/audit-review-team-2026-10-25.md`）；`shasum -a 256 backend/moyu_extract.py` = `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8`，与三份真实产物 `engine.sha256` **逐字相同**。

---

## 2 六项核查逐条结论

| # | 核查项 | 结论 | 关键证据 |
| --- | --- | --- | --- |
| 1 | 工单诉求是否逐条回应 | **部分不通过** | A1–A8 覆盖了 §1.1/§1.2/§1.3/§2.1/§3/§4.4/`generated_at`；**§2.2 后半段（拒绝率口径 + 3.4 小时可用性）与 §3(b) 的「溢出不可回收显式失败」未被回应**（findings F1/F2） |
| 2 | `--audit-limit` 语义与 `generated_at` 真实性 | **通过（1 处文档缺口）** | 默认 3、`0`=全部、非整数 `exit 2`、`audit_total` 均为实测；`generated_at` 已是运行时刻。负数静默 clamp 到「全部」未写进 `--help`（F4） |
| 3 | §6.4/§8 与 `/private/tmp` 真实产物一致 | **通过（1 处标题数不符）** | 三份产物全部字段逐项对齐；标题写「四档」而表为 5 行（F3） |
| 4 | 23 条 vs 11 条边界；是否仍有「23 条全部验证」 | **通过** | 边界在 3 处写死；全仓 grep 无该虚假表述 |
| 5 | F1 三 case；Q1 6.1 保留不可达结论 | **通过** | 三 case 独立复现一致；不可达论证原文仍在 |
| 6 | `tests/extract_fixtures.py` | **通过** | `extract fixtures: OK {'cases': 67}`，`exit 0` |

---

## 3 逐项核验细节

### 3.1 工单 §1.1：三份真实产物为真、且绑定当前字节（工单成立，提交纠正正确）

独立读取 `/private/tmp/moyu-real-current*.json` 的 `totals`/`determinism`/`chapters`/`engine`，与工单描述**逐字吻合**：

| 产物 | 章/字 | 断言/拒绝 | `wall_s` | `repeat` | mtime |
| --- | --- | --- | --- | --- | --- |
| `moyu-real-current-5.json` | 5 / 33 120 | 23 / 5（全 `empty_value`） | 172.192 | 1 | 2026-10-25 16:56:21 |
| `moyu-real-current-180.json` | 2 / 12 463 | 17 / 14（6 `empty_value` + 8 `quote_mismatch`） | 102.677 | 1 | 2026-10-25 16:52:53 |
| `moyu-real-current.json` | 2 / 12 463 | 2 / 2（1 `provider_error` + 1 `quote_mismatch`） | 84.524 | 1 | 2026-10-25 16:50:48 |

`/private/tmp/moyu-chat-smoke.json` 的 `created=1792849347` 换算为 `2026-10-24T21:42:27+08:00`，`model=deepseek-v4.1-flash`，与工单一致。三份 JSON 的 `generated_at` 均为旧硬编码 `2026-10-24T00:00:00+08:00`，故提交「旧产物时间戳不可信、以 mtime/hash 为准」的判断成立。

### 3.2 工单 §1.2：`repeat=1` 的 `identical` 无信息量（工单成立，提交处理正确）

`benchmarks/real_corpus/extract.py:182` 输出 `"repeat": repeat`；`determinism.identical` 由 digest 集合基数得出，`repeat=1` 时恒真。三份真实产物实测 `determinism.repeat=1`、`identical=true`。文档 `docs/real-corpus-extract-2026-10-24.md:177-178` 已写死「`repeat=1` 不是确定性证据，要证明必须用默认 `--repeat 2`」，且全仓无把该值当确定性通过的表述。

### 3.3 工单 §1.3：F1 语义边界（提交选择「文档写死」，边界成立）

实测 `chapters[1].audit[0]`：`subject="封肃"`、`attribute="real_name"`、`value="封"`、`evidence_quote="小人姓封"`、`char_start=67`、`char_end=71`；`len(quote)=4`、`end-start=4`，`text[67:71] == "小人姓封"` 逐字成立。即：**坐标闸门通过，语义错误放行**。

三种 case 全部成立，并已独立复现（见 §4.2）：

1. **正常 case**：11 条可见真实断言按「每章一个 revision」入库 → `score=100.0`、`grade=A`、`findings=0`。
2. **控制 case**：同库补 `封肃 real_name=封肃` 竞争值 → `score=92.0`、`grade=B`、`high=1`（「属性「real_name」存在两个不同取值」）。引擎只能发现冲突，不能判断哪个值对。
3. **反例 case**：`封肃 → real_name=封` 本身通过闸门，是「引文对、取值语义错」的实物反例；`docs/real-corpus-extract-2026-10-24.md:247-255`（§8 第 8 条）与 `:53-61`（§3「保证什么/不保证什么」表，`subject`/`value` 两行在 `:59-60`）均写死「不保证 `subject` 指代正确」「不保证 `value`/`attribute` 语义相符」，并有可执行记录 `tests/extract_fixtures.py:571 test_quote_gate_does_not_verify_semantics`。

提交选择的路径是「文档写死 + 控制实验」，未伪称已实现语义校验器；这与工单「二选一并写明选了哪个」的要求一致。

### 3.4 工单 §2.1：23 条 vs 11 条（提交对工单的纠正是对的）

实测 5 章产物每章 `(assertions, 可见 audit)` = `(0,0) / (7,3) / (7,3) / (7,3) / (2,2)`，断言合计 **23**，可见合计 **11**；三份旧产物 `audit_total` 均为 `None`（早于本次修复）。提交的 A4 判断与文档 `:179-181`、`:234-238` 一致：**只有 11 条带逐字证据可复核，其余 12 条无证据，不得写成已审**。

`/private/tmp/audit-realworld-visible.json` 记录 `visible_assertions=11`、`total_assertions_in_artifact=23`，与上述独立计数一致。

### 3.5 工单 §3：Q1 口径（提交不照抄 (a)/(b) 的论证成立，但 (b) 的能力诉求被漏掉）

`benchmarks/budgeted/run.py:225-246` 的场景表是硬证据：3000 字档 = `sc-02/03/07/08/12/14/17/18/19`（9 个，**无 sc-20**）；`sc-20` 是 4000 字 `canceled`；`sc-15` 是 4000 字 `volatile`。基线 `budgeted-v1.2-baseline.json` 实测 `by_target["3000"] = {total:9, first_pass:7, rate:0.7778}`、`["4000"] = {total:6, first_pass:4, rate:0.6667}`、`q1_target_met=false`。

因此工单原文「分母剔除 sc-19/sc-20」的前提确实错误（sc-20 不在 3000 档），提交 A6 的纠正是对的；`docs/consistency-budget-v1.2-2026-10-23.md:93` 的「≥80% 在模型不服从提示的假设下不可达」结论**原样保留**，`:95-106`（§6.1）新增的诊断值 7/8=87.5% 已明确标注「不替代 Q1、不改变 `q1_target_met`」，4000 仍独立为 4/6=66.7%。

但工单 (b) 的括注是「volatile 档单轮爆字，**需要「溢出不可回收」的显式失败**」——这是引擎能力诉求，不是场景归属问题。基线里 `sc-15` 的记录是 `characters=4661`、`stop_reason="target_reached"`、`rounds=1`：目标 4000 却产出 4661（超 16.5%，已越过 ±10% 预算带），而引擎停止原因仍报 `target_reached`。提交只回应了「sc-15 属 4000 档」，对「是否需要溢出不可回收的显式失败」既未处理也未列入「仍未关闭」（F2）。

### 3.6 `--audit-limit` 与 `generated_at`（实测）

`benchmarks/real_corpus/extract.py:223-224` 定义 `--audit-limit`（`type=int`、`default=3`、help 写「0=全部」）；`:159-161` 写入 `audit_total` 并据 `audit_limit <= 0` 决定是否全量；`:246-249` 调用侧 `audit_limit=max(0, args.audit_limit)`；`:253` `generated_at` 改为 `datetime.now().astimezone().isoformat(timespec="seconds")`。

我以 dry-run 实测（不触网、不需凭据）：

| 参数 | `exit` | 每章 `(assertions, 可见, audit_total)` | `generated_at` |
| --- | --- | --- | --- |
| 缺省 | 0 | `(5,3,5) (4,3,4)` | `2026-10-25T18:02:18+08:00` |
| `--audit-limit 0` | 0 | `(5,5,5) (4,4,4)`，合计 9/9 | 同上（真实运行时刻） |
| `--audit-limit 1` | 0 | `(5,1,5) (4,1,4)` | 同上 |
| `--audit-limit -5` | 0 | `(5,5,5) (4,4,4)`（负数被 clamp 成 0 → 全部） | 同上 |
| `--audit-limit 1.5` | 2 | `error: argument --audit-limit: invalid int value: '1.5'` | — |

结论：默认 3、`0`=全部、非整数报错退出 2、`generated_at` 为真实时间，四项均成立。负数被 `max(0, …)` 静默放大成「导出全部证据」——方向是 fail-open（多留证据而非少留），但 `--help` 只写「0=全部」，未记录负数行为（F4）。

### 3.7 23 条 vs 11 条边界的表述审计（通过）

全仓检索 `23 条全部` / `23/23` / `全量通过`：命中仅出现在与本主题无关的旧审计（`docs/audits/agent-chat-task-cards-20261021.md:47` 的 `writing_agent_planning_fixtures.py 23/23`、`CHANGELOG.md:148` 的旧版本回归计数、以及 `.audit-cache/` 内副本），**没有任何一处把 23 条真实断言写成已验证**。三处边界表述（`:9`、`:179-181`、`:234-238`）与 `docs/audit-response-2026-10-25.md:105-111`（§7 未关闭）互相一致。

### 3.8 §6.4 / §8 与 `/private/tmp` 一致性（通过，1 处标题不符）

行号锚点：`docs/real-corpus-extract-2026-10-24.md:167`（标题）、`:173`、`:174`、`:175`（三行真实模型证据）、`:177-178`（`repeat=1` 警告）、`:179-181`（11/23 可见性）、`:225-228`（`--audit-limit 0` 复现命令）、`:234-238`（§8 第 1 条）、`:247-255`（第 8 条）、`:256-260`（第 9 条）。

表中全部数字与 `§3.1` 实测一致（33 120/23/5/0/172.19s、12 463/17/14/102.68s、12 463/2/2/84.52s），引擎 sha 前缀 `6b5d4313…` 与实测一致。不一致仅一处：`:167` 写「按证据强度分成**四档**」，而表内数据行为 **5 行**（本机 smoke、规则模式、授权 5 章、授权 2 章、历史失败），是本次把原「外部真实模型」一行拆成三行后未同步标题（F3）。

---

## 4 独立复跑记录（本次实际执行）

### 4.1 回归

```text
.venv/bin/python tests/extract_fixtures.py
-> extract fixtures: OK {'cases': 67}   exit 0
```

### 4.2 F1 一致性探针复现（不改动既有证据文件）

以 `/private/tmp/audit_realworld.py` 为源，仅把输出路径替换为 `/private/tmp/vf-realworld-repro.json` 后重跑（原文件与其 JSON 未被覆盖）：

```text
VISIBLE_ASSERTIONS 11
TOTAL_ACCEPTED_IN_ARTIFACT 23
SCORE 100.0 A
COUNTS {"blocker": 0, "high": 0, "medium": 0, "low": 0}
FINDINGS 0

WITH_CONTROL_REAL_NAME_FENGSU
SCORE 92.0 B COUNTS {"blocker": 0, "high": 1, "medium": 0, "low": 0}
- high 属性「real_name」存在两个不同取值
  封肃：属性「real_name」在重叠有效期内被记为 "封" 与 "封肃"。
```

与 `audit-response-2026-10-25.md:33-41`（§2.2）、`:43-51`（§2.3）及 `/private/tmp/audit-realworld-visible.json` **完全一致**，F1 正常/控制两 case 可复现。

### 4.3 `--audit-limit` 语义矩阵

见 §3.6 表；产物 `/private/tmp/vf-default.json`、`vf-zero.json`、`vf-one.json`、`vf-neg.json`。

### 4.4 未执行（有意为之）

- **未**用真实凭据重跑 5 章（任务明令禁止调用真实模型；也无权读取凭据）。
- **未**重新生成 23 条全量证据：因此「23 条逐条审」在本报告里同样是**未完成**，不能因 `--audit-limit 0` 可用就视作已闭环。

---

## 5 Findings

### F1 · medium · 工单 §2.2 后半段（拒绝率口径 + 3.4 小时可用性）未进任何文档

- 位置：`docs/real-corpus-extract-2026-10-24.md:173-175`（只有条数，无比率与耗时外推）、`docs/audit-response-2026-10-25.md:103-111`（未关闭清单未列此项）
- 问题：工单 §2.2 明确写「要报数必须先固定口径」「单章均值 34.4 s，360 章单线程 ≈ 3.4 小时——这是产品可用性数据，**也要进文档**」。全仓 grep `17.9`/`45.2`/`34.4`/`单章均值`/`3.4 小时`/`拒绝率`，命中**只在工单自身**，产品文档与回复均未落地；回复 A5 把 §2.2 窄化为「误报率」，等于静默丢掉一半诉求。
- 证据：`grep -rn "34\.4\|单章均值\|3.4 小时\|拒绝率" docs/*.md` → 仅 `docs/audit-request-2026-10-25.md:65-68`。
- 修复要求：在 `docs/real-corpus-extract-2026-10-24.md` §6.4 或 §8 补一节「真实模型拒绝率与可用性」：写明两轮口径不同（5 章 `repeat=1` 拒绝 5/28=17.9%；2 章 `repeat=1` 拒绝 14/31=45.2%）**不可合并**、单章均值 34.4 s（实测 5 章 `per_chapter_ms.mean=34437.66`、2 章 `51337.99`，须注明两轮不同）、以及「360 章单线程 ≈ 3.4 小时仅为粗估、非实测」；并把该项状态写进回复 §7。

### F2 · medium · 工单 §3(b) 的「溢出不可回收显式失败」未回应、未列入未关闭

- 位置：`benchmarks/budgeted/results/budgeted-v1.2-baseline.json`（`sc-15`：`characters=4661`、`stop_reason="target_reached"`、`first_pass=false`）；`docs/consistency-budget-v1.2-2026-10-23.md:97-100`；`docs/audit-response-2026-10-25.md:105-111`
- 问题：工单 (b) 的括注是引擎能力诉求（volatile 档单轮爆字需「溢出不可回收」的显式失败）。基线显示 `sc-15` 目标 4000、实际 4661（超 16.5%），停止原因仍是 `target_reached`——即引擎把越界记为达标。提交只论证了「sc-15 属 4000 档、已在 4/6 内」，对「是否需要新增溢出不可回收的显式失败」既未做、也未写进未关闭清单，读者会误以为 (b) 已被覆盖。
- 修复要求：二选一并写明——(i) 若由其他任务承接（团队目标中「修基准 sc-15 超冲」独立成项），在回复 §7 与 `docs/consistency-budget-v1.2-2026-10-23.md:95-106` 写明「sc-15 的溢出显式失败由 <任务号> 承接，本提交不覆盖」；(ii) 若不承接，明确写出「`sc-15` 的 `stop_reason=target_reached` 与 4661 字越界并存属已知缺陷，`q1` 与 4000 档数字不变」，不得让该缺陷停在无主状态。

### F3 · low · §6.4 标题「四档」与表内 5 行不符

- 位置：`docs/real-corpus-extract-2026-10-24.md:167`
- 问题：标题写「把「跑过」按证据强度分成**四档**」，紧随的表有 5 个数据行（smoke / 规则 / 授权 5 章 / 授权 2 章 / 历史失败）；原「外部真实模型」一行被拆成三行后未同步标题，读起来像漏了一档。
- 修复要求：把「四档」改为「五档」，或把三行真实模型证据合并为一档三行并注明「分 3 次运行」。

### F4 · low · `--audit-limit` 负数行为未记录（静默 clamp 成「全部」）

- 位置：`benchmarks/real_corpus/extract.py:223-224`（help 仅写「0=全部」）、`:249`（`max(0, args.audit_limit)`）
- 问题：实测 `--audit-limit -5` 退出 0 且导出全部证据。方向是 fail-open（多留证据）故不构成数据风险，但对审计工具而言「负值等于全量」是隐式约定，`--help` 与文档均未写；依赖它的人会以为负数非法。
- 修复要求：在 `:224` 的 help 补一句「负数等同 0（全部）」，或在 `:249` 对负数直接 `parser.error`；二者择一，并把结论写进 `docs/real-corpus-extract-2026-10-24.md` §7 命令注。

### F5 · low · 回复 §2.1 第三行时间「16:04」与产物 mtime（16:50:48）不符，违背回复自定的判据

- 位置：`docs/audit-response-2026-10-25.md:29`
- 问题：该行标注 `2026-10-25 16:04（历史失败）`，而 `/private/tmp/moyu-real-current.json` 实测 mtime = `16:50:48`，其 `generated_at` 为旧硬编码 `2026-10-24T00:00:00+08:00`，`timing` 内无绝对时间。回复自己在 `:19`（A8 行）与文档 `:181` 定下的判据是「旧产物时间戳不可信，以 mtime 与 hash 为准」，这里却给出了一个没有任何产物支撑的时刻，属自相矛盾；同表另两行（16:56、16:52）与 mtime 吻合。
- 修复要求：改为 `16:50:48（mtime；generated_at 为旧硬编码值，不可用）`，或删去具体时刻只保留「历史失败轮」。

### F6 · low · 回复 §5 引作 `generated_at` 修复证据的 JSON 生成于修复之前

- 位置：`docs/audit-response-2026-10-25.md:79-91`（引用 `/private/tmp/audit-full-test.json`）
- 问题：该文件 mtime = `17:46`，而 `d477912` 提交时刻为 `17:52`，其 `generated_at` 仍是旧硬编码 `2026-10-24T00:00:00+08:00`。因此它能证明 `--audit-limit 0` 的 9/9（实测 `per-chapter (5,5,5) (4,4,4)`，成立），但**不能**证明 `generated_at` 已改为运行时刻。
- 处置：不构成虚假陈述（修复确已生效——我以修复后代码实测得 `2026-10-25T18:02:18+08:00`），仅证据链错位。
- 修复要求：把该证据替换为修复后重跑产物，或在 §5 加一句「该 JSON 早于 `generated_at` 修复，仅用于证明 9/9；`generated_at` 由代码 `extract.py:253` 与修复后复跑共同证明」。

### 不可验证项（不计入 findings，但必须留痕）

1. 回复 A5 称「第一次探针每断言一个 revision，制造了 7 条 `old_revision` medium」——`/private/tmp` 只留有重构后的 `audit-realworld-visible.json`，那 7 条无产物可核。属**不可验证的自述**，方向是自我撤销（保守），不据此加减分。
2. 回复 A7 对「审计开始时 `git status`」的历史陈述无法回溯核对；可核的当前事实是工作树只有 `?? docs/audit-request-2026-10-25.md`，工单 §4.4 声称的「12 改 + 15 未跟踪」确实不存在，A7 方向成立。

---

## 6 复核口径声明

- 本报告所有数字均来自本次实际命令输出，命令与结果写在 §3、§4；未沿用任何一方的自述数字。
- 行号引用以 `grep -n`／`sed -n` 的真实行号为准；区间末端若为分隔行，已在正文说明。
- 未调用真实模型、未读取或复述任何 API key、未修改 `backend/`、`benchmarks/`、`tests/`。复核产物只写入 `docs/audit-review-team-2026-10-25.md`（本文件）与 `/private/tmp/vf-*`（自建复现件）。
- 本报告是独立验收意见，不替代产品方对 F1–F6 的处置决定；F1/F2 未闭合前，`d477912` 的审计回复不应被读作「工单已逐条回应」。
