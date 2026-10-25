# 审计回复：真实模型证据、证据可见性与 Q1 口径

审计日期：2026-10-25
审计对象：`docs/audit-request-2026-10-25.md`
审计方式：先逐条读工单，再直接核验 `/private/tmp` 产物、当前工作树、运行器源码和可复算命令；不把文档自述当证据。
结论：**工单指出的主要矛盾成立；但工单里有两处事实前提错误，且“23 条真实断言全量可审”在旧产物上不成立。** 已修复文档与工具，仍有三项不能宣称已关闭。

## 1. 结论表

| ID | 工单点 | 核验结果 | 处理 |
| --- | --- | --- | --- |
| A1 | §1.1 文档说真实模型没跑，/tmp 却有产物 | 成立。`/private/tmp/moyu-real-current-5.json` 实际存在：5 章、33 120 字、23 条断言、5 条 `empty_value`、0 条 `provider_error`、172.19s；旧文档 §6.4/§8 仍写“没有跑/未完成” | 已改 `docs/real-corpus-extract-2026-10-24.md:6`、`:173-174`、`:234`，区分“链路已进入模型”和“质量未验收” |
| A2 | §1.2 `repeat=1` 的确定性结论不可用 | 成立。三份旧 JSON 都是 `repeat=1`；`determinism.identical=true` 是单轮退化的值，不能证明重复一致 | 文档新增 `repeat=1` 警告（`:177-179`）；未把旧 JSON 写成确定性通过 |
| A3 | §1.3 F1 证据只证“引文存在”，不证语义 | 成立。真实样本 `封肃` 的 `real_name=封` 引文 `小人姓封` 逐字正确；无竞争值时一致性引擎给 100 A、0 findings，补上 `real_name=封肃` 后才报 high 冲突 | 文档 §8 第 8 条写死边界；没有伪造语义校验器 |
| A4 | §2.1 “23 条断言可逐条审” | **不成立**。5 章 JSON 的 `assertions=23`，但每章 `audit` 只存前 3 条，实际可见 **11 条**（3+3+3+2），其余 12 条无逐字证据 | 新增 `--audit-limit`（默认 3，`0`=全部）与 `audit_total`；旧产物仍只有 11 条，未重跑 23 条 |
| A5 | §2.2 真实世界误报率 | 在**11 条可见**真实断言上，按“每章一个 revision”重建后为 **100 A、0 findings**；不能外推为 23 条或生产零误报。第一次探针每断言一个 revision，制造了 7 条 `old_revision` medium，已确认是探针假象，不是真实数据误报 | 证据 `/private/tmp/audit-realworld-visible.json`；报告保留 11/23 的覆盖边界 |
| A6 | §3 Q1 口径二选一 | **工单前提有误**：3000 字 9 场景是 `sc-02/03/07/08/12/14/17/18/19`，不含 `sc-20`（`sc-20` 是 4000 字取消）；`sc-15` 是 4000 字场景，已计入 4000 的失败。按原工单 (a)/(b) 都会得到不存在的分母或错位指标 | 不照抄 (a)/(b)；保留 Q1 `7/9=77.8%`、`q1_target_met=false`，另列明确标注的 7/8=87.5% 诊断值（剔除 `sc-19` 外部预算中断），不替代 Q1（`docs/consistency-budget-v1.2-2026-10-23.md:95`） |
| A7 | §4.4 “当前工作树 12 个修改 + 15 个未跟踪” | **不成立**。审计开始时 `git status --porcelain` 只有 `?? docs/audit-request-2026-10-25.md`；HEAD `c7d2841`，没有 12+15 个改动 | 未按该段提交任何幽灵改动；审计自身的改动在 §6 单独列出 |
| A8 | JSON `generated_at` 时间戳 | 成立（工单未提，但自审发现）：旧脚本硬编码 `2026-10-24T00:00:00+08:00`，实际 10-25 的产物也写旧日期 | 已改为运行时刻 `datetime.now().astimezone()`（`benchmarks/real_corpus/extract.py:253`）；旧产物时间戳不可信，报告注明以 mtime/hash 为准 |

## 2. 我实际核验到的证据

### 2.1 三份真实模型产物

| 产物 | 规模 | 断言/拒绝 | 引擎 | 时间 |
| --- | --- | --- | --- | --- |
| `/private/tmp/moyu-real-current-5.json` | 5 章 / 33 120 字 | 23 / 5，全部 `empty_value`；0 `provider_error`；`wall_s=172.192` | `6b5d43136405…` | 2026-10-25 16:56 |
| `/private/tmp/moyu-real-current-180.json` | 2 章 / 12 463 字 | 17 / 14（6 `empty_value`、8 `quote_mismatch`）；0 `provider_error`；`wall_s=102.677` | `6b5d43136405…` | 2026-10-25 16:52 |
| `/private/tmp/moyu-real-current.json` | 2 章 / 12 463 字 | 2 / 2（1 `provider_error`、1 `quote_mismatch`）；`wall_s=84.524` | `6b5d43136405…` | 2026-10-25 16:04（历史失败） |

三份都是 `repeat=1`；`identical=true` 不是确定性证明。5 章产物每章断言数为 `[0, 7, 7, 7, 2]`，`audit` 可见数为 `[0, 3, 3, 3, 2]`，合计 **11/23**。

### 2.2 11 条可见真实断言的一致性结果

探针 `/private/tmp/audit_realworld.py` 读取 5 章产物中的 11 条可见断言，按**每章一个 revision** 建库（避免把“同一章的多条断言”误当成历史修订），调用 `backend.moyu_consistency.analyze()`：

- `visible_assertions=11`，`total_assertions_in_artifact=23`
- `score=100.0`，`grade=A`，`counts={blocker:0, high:0, medium:0, low:0}`，`findings=0`
- examined：`fact=11, knowledge=11, evidence=11`，其余 0

因此，**在 11 条可见真实断言上没有误报**。但这不是“23 条零误报”，更不是生产零误报。

### 2.3 F1 控制实验

在同一库上补一条 `封肃 real_name=封肃`（引文仍用原 `小人姓封` 的 span，仅用于控制变量）：

- `score=92.0`，`grade=B`
- `high=1`：`属性「real_name」存在两个不同取值`，`封` vs `封肃`
- 引擎只能发现冲突，不能判断哪个值对；如果真实库只有 `封`，它不会报

这说明：**F1 是真实边界，不是可以靠“100 A”掩盖的误报**；写入端需要人工或额外语义门。11 条可见断言的 0 findings 不能外推。

## 3. Q1 口径：工单的两条路都不能原样走

`benchmarks/budgeted/run.py:244-246` 的定义是：

- 3000：`sc-18 stalling`、`sc-19 budget_limited` 等 9 个
- 4000：`sc-20 canceled`、`sc-15 volatile` 等 6 个

所以：

- 3000 的 9 个分母**不含 sc-20**；把 sc-20 一起剔除会得到不存在的 9−2 口径。
- sc-15 是 4000 的失败，已在 4/6=66.7% 里；把它补进 3000 会把两个指标混在一起。
- 原 Q1 headline 仍是 **7/9=77.8%，`q1_target_met=false`**；这是未达成，不是笔误。
- 若业务确实要回答“纯模型可控场景”，只能另设诊断值：剔除 `sc-19` 后 **7/8=87.5%**，必须同时写“分母剔除了外部预算中断、不是 Q1”。4000 仍是 **4/6=66.7%**。

这不是替客户改口径，而是把“未达成”与“外部原因诊断”分开；客户 Q1 仍需按 77.8% 面对。

## 4. 修复清单

| 文件 | 改动 | 状态 |
| --- | --- | --- |
| `benchmarks/real_corpus/extract.py` | 新增 `--audit-limit`（默认 3，0=全部）、`audit_total`；`generated_at` 改为运行时刻 | 已改，待提交 |
| `docs/real-corpus-extract-2026-10-24.md` | §1 结论、§6.4 真实模型表、`repeat=1`/11-of-23 警告、§7 全量审计命令、§8 第 1/8/9 条 | 已改，待提交 |
| `docs/consistency-budget-v1.2-2026-10-23.md` | 新增 §6.1：Q1 保留 7/9；诊断 7/8；4000 4/6 | 已改，待提交 |
| `docs/CHANGELOG.md` | 新增 2026-10-25 审计修复条目 | 已改，待提交 |
| `docs/audit-response-2026-10-25.md` | 本报告 | 新增，待提交 |

## 5. 复跑证据（本次实际执行）

```text
.venv/bin/python -m py_compile benchmarks/real_corpus/extract.py   -> PASS
.venv/bin/python tests/extract_fixtures.py                          -> extract fixtures: OK {'cases': 67}
.venv/bin/python benchmarks/real_corpus/extract.py --mode dry-run --limit 2 --repeat 1 \
  --audit-limit 0 --json /private/tmp/audit-full-test.json
  -> totals.assertions=9；audit 可见 9/9；rejected=0
.venv/bin/python /private/tmp/audit_realworld.py
  -> 11 visible；100.0 A；0 findings；控制组 92.0 B；1 high
```

注意：`--audit-limit 0` 的 9/9 是 **dry-run 合成语料**，不是真实模型 23 条；真实模型的 5 章产物仍是旧的默认 3 条采样。

## 6. 自审：有没有重复“只信文档不查 /tmp”

没有。具体做法和一次自我纠正：

1. 先读工单，但不直接采信“文档说没跑”的转述；直接列 `/private/tmp/moyu-real-current*.json`，读 `totals`、`determinism`、`audit` 和引擎 sha。
2. 发现 `audit` 只到前 3 条，纠正了工单“23 条可逐条审”的说法。
3. 第一次写探针时为了省事，给每条断言各建一个 revision；结果出现 7 条 `old_revision` medium。追查后发现是探针制造了“旧 revision 被新 revision 覆盖”的历史，不是真实数据误报；改为每章一个 revision 后为 100 A、0 findings。**这 7 条不再作为误报证据。**
4. 检查工作树：审计开始时只有工单未跟踪，工单 §4.4 的“12+15”不成立；没有为了凑数去提交不存在的改动。
5. 没有把 `generated_at` 的旧硬编码时间当成运行时间；已改成实际时间，并在文档说明旧 JSON 的时间戳不可信。

## 7. 仍未关闭（不能写成全绿）

1. **23 条全量证据未生成**：新增 `--audit-limit 0` 只保证以后能生成；本次没有用授权 key 重跑 5 章，旧 JSON 仍只有 11 条可见。因此“23 条逐条审”仍是未完成项。
2. **真实模型 `repeat=2` 未跑**：当前所有真实产物都是 `repeat=1`，确定性未验收。
3. **F1 语义边界未加写入门**：本次选择“文档写死 + 控制实验”，没有实现 subject/value 语义校验；写入端仍需人工/额外门。
4. **11 条不是 23 条**：真实世界误报率只能报“11 条可见 0 findings”，不能报 0/23。
5. **Q1 仍未达成**：7/9=77.8% < 80%，`q1_target_met=false`；7/8=87.5% 是诊断值，不是 Q1。
6. **代码变更尚未跑 CI**：本文的 dry-run/67 用例通过，不等于 GitHub 全量 CI；提交后需要再跑。

## 8. 结论

工单抓到的核心问题是对的：旧文档确实与 `/tmp` 证据冲突，`repeat=1` 不能证明确定性，F1 不能验语义，Q1 数字有口径问题。
但工单也有两处需要纠正：**23 条并不是 23 条可见证据（实际 11 条）**；**Q1 的 9 个 3000 字场景不含 sc-20，sc-15 也不是 3000 字场景**。
本次已把能修的文档和工具修掉，并把不能宣称的部分明确留在 §7；没有把 11 条可见证据包装成 23 条，也没有把 87.5% 写成 Q1 达成。
