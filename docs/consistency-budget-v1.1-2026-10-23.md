# 多轮字数预算续写 · 引擎 v1.1 复测与前后对照（2026-10-23）

> 任务 t15 / Q1-5d。同一个基准（20 个场景、seed 20261023、判定口径不变），分别锁定 v1.0 语义与 v1.1 语义各跑一遍，给出前后对比、翻转明细与诚实结论。本文件由 `benchmarks/budgeted/run.py --compare` 自动生成，数字与 `benchmarks/budgeted/results/budgeted-v1.1-baseline.json` 同源。

## 1. 一页结论

| 口径 | v1.0（基线） | v1.1（本次） | 变化 |
| --- | --- | --- | --- |
| 一次通过率（全部 20 个场景） | 70.0%（14/20） | 80.0%（16/20） | +10.0pt |
| 一次通过率（3000 字子集，Q1 口径） | 66.7%（6/9） | 77.8%（7/9） | +11.1pt |
| 生成侧合格档 | 100.0%（13/13） | 100.0%（13/13） | — |
| 生成侧劣质档 | 20.0%（1/5） | 60.0%（3/5） | — |
| 外部中断档 | 0.0%（0/2） | 0.0%（0/2） | — |
| 平均轮数 | 3.20 | 3.85 | +0.65 |

**Q1 目标（3000 字一次通过率 ≥80.0%）：仍未达成。**本次 77.8%（7/9），差 2.2 个百分点。缺口来源见 §5，不要把「比 v1.0 好」当成「达标」。

翻转明细：fail→pass **2 个**（sc-16、sc-17），pass→fail **0 个**（无）。

## 2. v1.1 改了什么

| 机制 | 参数 | 作用 | 本次触发 |
| --- | --- | --- | --- |
| 停滞重试 | `max_stall_retries=1` | 某轮增量低于 `min_round_gain` 时先重试一轮，仍不足才判 `no_progress` | 1 次，涉及 sc-18 |
| 弹性轮数 | `hard_max_rounds=12` | 跑满 `max_rounds` 后若进度 ≥60% 且最近一轮仍在增长，最多续到 12 轮 | 12 轮，涉及 sc-16、sc-17 |
| 收束提示 | `closing_hint` | 剩余不足 600 字时注入「只差约 N 字，不要展开新情节」 | 18 轮，涉及 12 个场景 |
| 停滞提示 | `stall_hint` | 重试轮附带「只补足缺口」的提示 | 1 轮，涉及 sc-18 |

v1.0 对照侧把这些参数显式钉死为 `max_stall_retries=0`、`hard_max_rounds=None`（取各场景的 `max_rounds`），因此两列是同一份代码、同一批场景、两套语义的严格对照，不是「旧版本跑的结果」与「新版本跑的结果」之间的模糊对比。

## 3. 逐场景变化

| 场景 | 档位 | 目标 | v1.0（字数 / 轮数 / 停止原因） | v1.1（字数 / 轮数 / 停止原因） | 判定 | 重试 / 续轮 |
| --- | --- | --- | --- | --- | --- | --- |
| sc-16 | 产出不足（underpowered） | 2500 | 1719 / 6轮 / max_rounds | 2279 / 12轮 / target_reached | ✅ 转通过 | 0 / 6 |
| sc-17 | 产出不足（underpowered） | 3000 | 2069 / 6轮 / max_rounds | 2741 / 12轮 / target_reached | ✅ 转通过 | 0 / 6 |
| sc-18 | 停滞（stalling） | 3000 | 30 / 1轮 / no_progress | 46 / 2轮 / no_progress | — | 1 / 0 |

未列出的场景在两套语义下字数、轮数、停止原因与判定完全一致。

## 4. 停止原因分布变化

| 停止原因 | 含义 | v1.0 | v1.1 | 变化 |
| --- | --- | --- | --- | --- |
| target_reached | 达标停止 | 14 | 16 | +2 |
| no_progress | 产出停滞 | 1 | 1 | +0 |
| max_rounds | 轮数耗尽 | 2 | 0 | -2 |
| budget_exhausted | 预算耗尽 | 1 | 1 | +0 |
| canceled | 作者取消 | 1 | 1 | +0 |
| overshoot | 超冲停止 | 1 | 1 | +0 |

`max_rounds` 归零、`target_reached` 上升，说明弹性轮数把原本「轮数耗尽」的欠量场景真的写到了目标区间，而不是放宽判定把它们算成通过——两列用的是同一个 `within_tolerance` 口径。

## 5. 仍未通过的场景与原因

| 场景 | 档位 | 目标 | 停止原因 | 失败分类 | 字数 | 欠量 | 超冲 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sc-15 | 无视预算（volatile） | 4000 | overshoot | 超冲（越过 +10% 上限） | 4661 | 0 | 261 |
| sc-18 | 停滞（stalling） | 3000 | no_progress | 产出停滞（单轮增量 < min_round_gain） | 46 | 2654 | 0 |
| sc-19 | 预算受限（budget_limited） | 3000 | budget_exhausted | 预算中断（外部原因） | 2318 | 382 | 0 |
| sc-20 | 作者取消（canceled） | 4000 | canceled | 作者取消（外部原因） | 2157 | 1443 | 0 |

- **sc-15（volatile，超冲）**：目标 4000 字，实际 4661 字，超冲 261 字。引擎在累计字数触及下限（0.9×）时按设计立即停止，但单轮产出越过了 1.1× 上限。这属于生成侧不受控，引擎侧只有「按字数硬截断」才能消除——那会改写正文，v1.1 不做。
- **sc-18（stalling，停滞）**：目标 3000 字，实际 46 字。重试一轮后产出仍低于 `min_round_gain`，引擎如实判 `no_progress`。这是正确行为，不是缺陷。
- **sc-19（budget_limited，预算中断）**：目标 3000 字，预算在第 3 轮前耗尽，实际 2318 字。外部约束，引擎无法绕过。
- **sc-20（canceled，作者取消）**：第 2 轮前收到取消信号，实际 2157 字。取消优先于预算与目标，这是设计意图。

## 6. 诚实结论

1. **v1.1 在同一个基准上从 70.0% 提升到 80.0%。** 翻转全部是 fail→pass（2 个：sc-16、sc-17），档位 underpowered，生效机制是 弹性轮数。pass→fail 0 个，没有拿老场景换新场景。
2. **Q1 的 3000 字口径仍未达标：77.8%（7/9）。** 剩余 4 个失败场景分布在 budget_limited、canceled、stalling、volatile。失败原因：budget_limited 是外部预算中断，引擎按设计不该绕过；canceled 是作者主动取消，引擎必须服从；stalling（真正停滞）重试再多也不会变长；volatile（单轮越界超冲）需要生成侧预算约束。逐条说明见 §5。
3. **不能拿「比 v1.0 好」交差。** 若把验收指标写成「相对上版提升」，那么任何一次参数微调都能算达标；Q1 的指标是绝对值，未达成就要如实写未达成。
4. **v1.1 没有放宽任何判定。** `within_tolerance`、`stop_reason == target_reached`、正文不变性、candidate_only 全部沿用 v1.0 口径；两列的唯一差异是引擎参数。

## 7. 已知局限（在 v1.0 基线之上新增）

1. **fake provider 不响应收束提示与停滞提示。** 提示只被记录（`closing_hint_rounds=18`、`stall_hint_rounds=1`），不改变产出长度。因此本次测量到的是**弹性轮数与重试**的净效果，真实模型下提示词的额外收益尚未测量。
2. **弹性轮数把单场景最大轮数从 6 抬到 12，成本翻倍。** underpowered 场景各用了 12 轮；生产环境必须评估 token 成本与延迟。本基准只测字数收敛，不测成本。
3. **`hard_max_rounds=12` 是拍的值。** 没有扫描 7–20 的轮数上限，也没有测「续到第 9 轮才达标」这类边界；12 只是「够用且不至于无限跑」的经验值。
4. **停滞重试只给 1 次。** `max_stall_retries=1` 在 stalling 档上仍然失败（产出 46 字）；给更多次重试只会在真正的停滞模型上烧钱，不会让字数变多。
5. **v1.0 基线已随本次发布重生成。** 为实现对照，v1.0 语义用显式参数（`max_stall_retries=0`、`hard_max_rounds=None`）锁定后重跑，`budgeted-baseline.json` 及其文档因此被覆盖为新 digest；旧字段值与 17:07 首版逐字段核对完全一致（`rate` 仍 0.7），新增的 `engine_features` 等字段导致 digest 变化，属预期变化不是回归。代价是最初那版文件的字节已不可恢复。

## 8. 复现

```bash
.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023
```

`--compare` 会依次跑 v1.0 锁定语义与 v1.1 语义，写出：

- `benchmarks/budgeted/results/budgeted-v1.1-baseline.json`：v1.1 确定性核心报告
- `benchmarks/budgeted/results/budgeted-v1.1-baseline.md`：一页纸摘要
- `benchmarks/budgeted/results/budgeted-v1.1-timing.json`：耗时观测值
- `benchmarks/budgeted/results/budgeted-comparison.json`：前后对照的机器可读版本
- `docs/consistency-budget-v1.1-2026-10-23.md`：本文件

v1.0 基线的四个产物（`budgeted-baseline.*` 与 `docs/consistency-budget-baseline-2026-10-23.md`）不会被覆盖。

环境：Python 3.11.17，macOS-26.6.1-arm64-arm-64bit；引擎 `backend/moyu_continuation.py` sha256 `61d170e5cd7bc951…`；v1.1 确定性摘要 `sha256:7349e53888f9145132e7b7d93b2b2fced5208ebf500371a59042af059ae58391`（repeat=2，一致=是）。
