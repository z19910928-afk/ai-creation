# t104 验收报告：desktop 焦点发布说明与 HEAD 自引用修正

- 任务：`t104`（captain 接管；独立性降级，见 §0）
- 日期：2026-10-26
- 结论：**PASS（仅限本任务范围）**
- 被测基线：`8126778`（代码基线）+ `605b895`（docs-only 提交）
- 报告路径：`docs/verification-desktop-focus-release-2026-10-26.md`

## 0. 独立性声明（不得略过）

`t104` 原计划由独立 verifier 完成。实际过程：

1. `verifier-current`（attempt 2，`4baa4cae-…`）收到唤醒消息 `78f4a5b5-…`，180 秒后仍无任何报告文件；
2. `verifier-zz`（attempt 3，`30a6eda6-…`）收到唤醒消息 `6e8b38be-…`，240 秒后仍无 `docs/verification-desktop-focus-release-2026-10-26.md`；
3. 两次 `agent_teams_status` 均显示成员 idle/ready，但 `ls` 与 `git status` 证明没有实际产出；
4. 最终由 captain 接管（attempt 4）完成本报告。

**因此本报告不是独立第三方 verdict。** 它可以证明当前文档与 evidence 的一致性，但**不能**用来关闭 B3/B4、不能替代 t91 的 `needs_revision`、也不能把 Delivery 从 blocked 改成通过。t104 的 PASS 只覆盖“发布说明的 desktop 焦点段与 HEAD 口径”。

## 1. 被测字节（跑前 → 跑后）

| 文件 | 跑前 | 跑后 | 结论 |
| --- | --- | --- | --- |
| `docs/release-readiness-2026-10-25.md` | 377 行 / `d3c1801762b87aba3c3bff6d3fb048aaf141c18a9d1c491d0535741e2fefcb4f` | **382 行 / `eb072915bafb0c68146fb56cc99891ce3555b1c576c82854029367c4ee3109f9`** | 本任务修正 HEAD 口径 + 修正 t104 自证；`79 insertions / 36 deletions` |
| `tests/desktop_smoke.cjs` | 810 行 / `b409533e73bddf8e66195ac73dc3e942be5b99db6657b5bbb5a31999a356e22d` | 同左，**未改动** | t101 字节；本任务不碰测试 |
| `docs/verification-desktop-smoke-focus-2026-10-26.md` | 90 行 / `1405a3a7c474ebf952a657b11681b7ca1cf2531de774f61343918c1162556659` | 同左，**未改动** | t102 报告；只读引用 |
| 本报告 | 不存在（`ls` 返回 No such file） | 写后见 §5 回读 | 新建，仅本任务写域 |

跑前工作树：

```text
 M docs/release-readiness-2026-10-25.md
 M tests/desktop_smoke.cjs
?? docs/verification-desktop-smoke-focus-2026-10-26.md
```

其中 `tests/desktop_smoke.cjs` 的修改是 **t101 的既有改动**（t104 合同允许的 pre-existing 字节），不是 t104 引入。

> 注：文档在首次写入本报告后又做了一次自证修正（新增 t104 报告引用），因此 §1 的“跑后”最终值为 382 行 / `eb072915…`；本报告正文的其它结论仍然成立，唯一被改写的旧句子是 t104 的“未改其他报告”，已在 §8 更正。

## 2. 验收结果

### 2.1 t103 的 desktop 更新（通过）

| 检查 | 结果 |
| --- | --- |
| §2.1 的 `tests/desktop_smoke.cjs` 行绑定 `b409533e…` / 810 | PASS |
| 旧 `c6d13a0e…` / 784 只以历史/旧字节/“改为”口径出现 | PASS |
| §5.2 保留 t88 历史失败（7 跑 6 过、另一次 1 跑 1 挂、`activation_attempts=55`） | PASS |
| §5.2 写入 t101 修测试辅助、t102 独立 2/2 与 10/10、`native_activation.ok=true` | PASS |
| §5.2/§6.2/§7 明确同机 GUI + Automation 边界，不宣称 26/26 | PASS |
| 全文没有正向“26/26 全绿/桌面端全部通过”声明 | PASS |
| 任务描述提到的 §10 不存在；文档实际为 §0–§8，发布措辞模板在 §7 | 观察项，不构成缺陷 |

### 2.2 HEAD 自引用（发现并修正）

**发现（T104-F1）**：t103 后文档多处写“当前 HEAD `8126778`”，但实际 `git rev-parse HEAD` 已是 docs-only `605b895`。这是真实的自我指涉漂移：文档提交会不断推进 HEAD，若把 HEAD 写成固定 hash，文档永远无法自洽。

**修正**：

- 文首新增「HEAD 口径（2026-10-26 修正，必读）」：所有“当前 HEAD `8126778`”均指**最后一次改变产品/测试字节的代码基线**，不是 `git rev-parse HEAD`；本文由 docs-only `605b895` 推送，CI `40303229735` success；代码/测试字节与 `8126778` 相同；
- §0 维度表：代码基线 run `40290743348` + docs-only run `40303229735` 并列，均 `completed/success`；
- §2 表：新增 `605b895` 行，`8126778` 改为“代码基线”，历史行保留；
- §0/§2.1/§5.5/§6.1 B2 的 `d766492..HEAD` 命令改为静态 `d766492..8126778`，避免命令随文档提交漂移；
- §5.1/§5.5/§6.1/§7/§8 的“当前 HEAD”改为“代码基线”或补充 docs-only 指向；
- §8 末尾新增 t104 自证条目。

**结果**：`**当前 HEAD `8126778`**` 已不存在；全文唯一出现“当前 HEAD `8126778`”的位置是文首定义句。`git diff --check` exit 0。

### 2.3 必须保留的边界（通过）

| 项 | 文档值 | 结果 |
| --- | --- | --- |
| Q1 3000 字 | 7/9 = 77.8%，低于 80%，`q1_target_met=false` | PASS |
| Q1 4000 字 | 4/6 = 66.7% | PASS |
| 版本 | 仍 `0.1.3`，不 bump、不创建 `v0.1.4-rc.1` tag | PASS |
| B3 | `B3_CLOSED: true`，只关闭 B3；t91 `needs_revision` 原样保留 | PASS |
| t79 | `sc-18` 360<2700、`sc-19` 外部预算中断，不重跑洗白 | PASS |
| 真实模型 | 23 条断言中 11 条有逐字证据，`repeat=1`，不是稳定召回率 | PASS |
| sc-15 | 越界 4661/4000 仍 `stop_reason=target_reached`，明确标注“未修” | PASS |

## 3. 命令与输出

### 3.1 `git diff --check`

```text
$ git diff --check
（无输出）
exit=0
```

### 3.2 privacy fixtures

```text
$ .venv/bin/python tests/repository_privacy_fixtures.py
repository privacy fixtures: ok (Git index only)
exit=0
```

### 3.3 文档探针

```text
$ .venv/bin/python /tmp/t104_doc_probe.py
...
T104_DOC_PROBE: PASS=27 FAIL=0 TOTAL=27
exit=0
```

探针覆盖 27 项：文档/测试/报告的行数与 sha256、HEAD 口径、旧 hash 的历史定位、desktop 边界、Q1、版本、B3、t91、t79、11/23、sc-15、无 `d766492..HEAD` 残留、工作树无代码/前端/benchmark 改动。首次运行 26/1，唯一失败是探针对 `c6d13a0e` 的“历史”关键词检查过窄；该三处实际都写作“t101 之前/旧字节/改为”，修正断言后 27/0。**不是放宽产品要求，而是消除关键词误报。**

### 3.4 回读

```text
$ wc -l docs/verification-desktop-focus-release-2026-10-26.md
```

写后回读见任务完成记录；本报告不把“文件存在”当作内容正确，内容由 §2 的探针逐项校验。

## 4. Findings

| id | 级别 | 结论 | 处理 |
| --- | --- | --- | --- |
| T104-F1 | low | 发布说明把 docs-only 提交后的 `git HEAD` 写成固定 `8126778`，属自我指涉漂移 | 已修：新增代码基线口径，静态化 diff 命令；复验 PASS |
| T104-F2 | info | 两次独立 verifier 均未产出；captain 接管导致独立性降级 | 如实披露；本报告不用于关闭 B3/B4 或 Delivery |
| T104-F3 | info | 任务描述提到 §10，但文档实际只有 §0–§8；发布措辞模板在 §7 | 记录为编号观察项，不伪造章节 |
| T104-F4 | low | 报告初稿后，发布说明 §8 仍写“t104 未改其他报告”，而 t104 实际新增了本报告 | 已由最终 382 行修正自证更正；本报告同步更新 §1 最终 hash |

无 blocker，无 needs_revision。

## 5. 边界（不得越过）

- 本 PASS 只证明：发布说明的 desktop 焦点段与 HEAD 口径，在当前字节上自洽，且未引入代码/测试变更。
- 不证明 Q1 达标；不证明 3000 字 8/9 可达；不证明真实模型质量；不证明微信已同步。
- 不关闭 B4、不改变 `0.1.3`/不发布决定、不改变 t91 `needs_revision`、不改变 t79 数学结论。
- 不把 t102 的同机 2/2 扩张成跨机器 26/26。
- 不把本报告的 captain 接管 PASS 当作独立第三方验收。

## 6. 结论

**t104 范围内 PASS。** 发布说明现在用稳定的“代码基线 `8126778` + docs-only `605b895`”口径描述自身，不再出现“`git HEAD` 等于 `8126778`”的错误断言；desktop 焦点段与 t101/t102 证据一致，且所有未达标/未修边界原样保留。

**但这不是发布放行。** B4/Q1、t79、sc-15、真实模型复跑、微信同步与历史 verdict 脱节仍按 §5.3–§6.2 保持开放。
