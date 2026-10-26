# Budgeted Continuation Benchmark（Q1-5b）

多轮字数预算续写引擎 `backend/moyu_continuation.run_budgeted_continuation` 的
**一次通过率**基准。目标是把策略文档 Q1 的验收指标「3000 字章，一次通过率 ≥80%」
变成可复现的数字。

```bash
.venv/bin/python benchmarks/budgeted/run.py --seed 20261023
```

退出码 `0` 表示**基准完整性验收**通过，不表示指标达标。指标是否达标见报告里的
`q1_target_met` 字段与文档 §1。

---

## 1. 一次通过的定义

一个场景记为「一次通过」，当且仅当同时满足：

1. `stop_reason == "target_reached"`；
2. `within_tolerance` 为真，即累计字数落在 `[ceil(0.9×目标), floor(1.1×目标)]`。

引擎在累计字数达到下限（0.9 倍目标）时就停止，所以**单轮产出过大、越过 1.1 倍
上限时 `within_tolerance` 为假，本基准判为失败**。不放宽成只看 `target_reached`——
放宽会让「模型无视预算、一次冲过头」这种真实缺陷变成通过。

## 2. 为什么用 fake provider

基准不调用任何真实模型：真实 LLM 的输出不可复现、有成本、有网络抖动，无法作为
回归基线。这里用内存 fake provider 按「剩余额度 × 档位比例」产出定长中文正文，
比例带固定区间抖动，随机流由 `seed` 派生。

代价是：**基准测量的是「引擎在给定产出模型下的行为」，不是「某真实模型的通过率」。**
引用数字时必须带上这个前提，否则会误导。详见 §6 与基线文档 §7。

## 3. 质量档位

| 档位 | 标识 | 单轮产出模型 | 预期行为 |
| --- | --- | --- | --- |
| 一次到位 | `calibrated` | 剩余额度的 100%±5% | 首轮即达标 |
| 渐进收敛 | `steady` | 剩余额度的 50%±8% | 3–5 轮收敛 |
| 保守欠量 | `cautious` | 剩余额度的 38%±4% | 5–6 轮勉强收敛 |
| 无视预算 | `volatile` | 剩余额度的 75%±50%（可超 100%） | 常首轮超冲 |
| 产出不足 | `underpowered` | 剩余额度的 18%±3% | 6 轮仍不达标 |
| 停滞 | `stalling` | 单轮固定 12–30 字 | 首轮判 `no_progress` |
| 预算受限 | `budget_limited` | 同 steady，预算第 3 轮前耗尽 | `budget_exhausted` |
| 作者取消 | `canceled` | 同 steady，第 2 轮前取消 | `canceled` |

## 4. 场景矩阵（20 个）

20 个场景由「目标字数 × 质量档位」交叉构成，`max_rounds` 与 `min_round_gain`
统一取引擎默认值 `6` 与 `50`：

| 目标字数 | 场景数 | 覆盖档位 |
| --- | --- | --- |
| 2500 | 5 | calibrated、steady×2、cautious、underpowered |
| 3000 | 9 | calibrated×2、steady×2、cautious、volatile、underpowered、stalling、budget_limited |
| 4000 | 6 | calibrated、steady×2、cautious、volatile、canceled |

完整逐场景表由 `run.py` 渲染进基线文档 §3.3。

## 5. 配比是假设，不是测量

20 个场景的档位配比（合格档 13 个 / 劣质档 5 个 / 中断档 2 个）是**人为假设**，
模拟一条已接入生产、但生成质量参差的链路。因此：

- **总体一次通过率是配比的函数**，换一套配比就得到另一个数字；
- 分层包络（每个档位各自的通过率）比总体数字更可信；
- 文档 §6 给出断点条件：非合格场景占比低于多少时总体仍能守住 80%。

改配比就改 `SCENARIO_TABLE`，并在提交说明里写清改动理由。**不要在看到结果之后
回头调配比凑数**——那会让这个基准失去意义。

## 6. 产物

| 文件 | 内容 | 是否确定性 |
| --- | --- | --- |
| `results/budgeted-baseline.json` | 确定性核心报告（场景、汇总、验收） | 是，逐字节可复现 |
| `results/budgeted-baseline.md` | 一页纸摘要 | 是 |
| `results/budgeted-timing.json` | 墙钟耗时观测值 | 否（观测值） |
| `docs/consistency-budget-baseline-2026-10-23.md` | 正式基线文档 | 是 |

`--compare` 额外产出（**不改动上面四个 v1.0 文件**）：

| 文件 | 内容 | 是否确定性 |
| --- | --- | --- |
| `results/budgeted-v1.1-baseline.json` | v1.1 确定性核心报告 | 是，逐字节可复现 |
| `results/budgeted-v1.1-baseline.md` | v1.1 一页纸摘要 | 是 |
| `results/budgeted-v1.1-timing.json` | v1.1 墙钟耗时观测值 | 否（观测值） |
| `results/budgeted-comparison.json` | v1.0 → v1.1 前后对照（机器可读） | 是 |
| `docs/consistency-budget-v1.1-2026-10-23.md` | v1.1 复测与前后对照文档 | 是 |

**墙钟耗时为什么单独放？** 同一 seed 两次运行的耗时必然有微秒级抖动。如果把耗时
写进 JSON / Markdown / 文档，它们就不再逐字节一致，「同 seed 两次运行输出一致」
这条验收也就无法成立。所以耗时只落 `budgeted-timing.json`，并在文档里以
「确定性工作量指标」（总轮数、总字数）替代。

## 7. 确定性

- 确定性核心 = `seed` + `config` + `scenarios` + `summary`；
- 默认 `--repeat 2`，在**同一次运行内**把整套场景跑两遍并比对 digest；
- 输出 `determinism.digest`（`sha256:` 前缀）供外部核对；
- 验收 `同 seed 重复运行结果一致` 不通过时，退出码为 1。

跨进程验证：

```bash
.venv/bin/python benchmarks/budgeted/run.py --seed 20261023 > /tmp/a.out
cp benchmarks/budgeted/results/budgeted-baseline.json /tmp/a.json
.venv/bin/python benchmarks/budgeted/run.py --seed 20261023 > /tmp/b.out
diff /tmp/a.out /tmp/b.out && diff /tmp/a.json benchmarks/budgeted/results/budgeted-baseline.json
```

## 8. 正文不变性

引擎的设计约束是「只返回候选文本，不写正文、不写 canon」。基准为每个场景写一段
既有正文到独立文件，记录 sha256，运行结束后重新读取比对。任何写入都会让哈希变化
并被 `正文被改写次数` 这一项捕获。

注意：正文快照默认写在系统临时目录（`tempfile.TemporaryDirectory`），运行结束即
清理，不会污染仓库；需要保留现场时用 `--work-dir` 指定目录。

## 9. 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--seed` | `20261023` | 随机种子；每个场景派生独立随机流 |
| `--repeat` | `2` | 套件重复次数，用于确定性自检 |
| `--out` | `results/budgeted-baseline.json` | 确定性核心报告 |
| `--md` | `results/budgeted-baseline.md` | 摘要 |
| `--timing` | `results/budgeted-timing.json` | 耗时观测 |
| `--doc` | `docs/consistency-budget-baseline-2026-10-23.md` | 正式文档 |
| `--work-dir` | 系统临时目录 | 正文快照目录 |
| `--compare` | 关 | 跑 v1.0 锁定语义 + v1.1 语义各一遍，写 v1.1 基线与前后对照（t15）；此时 `--out/--md/--timing/--doc` 不生效 |
| `--quiet` | 关 | 只输出退出码 |

## 10. 验收项

`run.py` 在退出前自检 8 项，任一不通过即退出码 1：

1. 20 个场景全部执行；
2. 同 seed 重复运行结果一致；
3. 引擎未改写任何正文；
4. 所有结果均为 `candidate_only`；
5. 字数记账自洽（引擎计数 == provider 产出）；
6. 6 类停止原因全部出现（覆盖度）；
7. 文档与 JSON 数字一致（逐 token 比对）；
8. 耗时观测单独落盘且结构完整。

`--compare` 模式下这 8 项改为对 v1.1 侧自检，其中第 6 项按 **v1.0 对照侧**判断
（v1.1 的弹性轮数会把 `max_rounds` 归零，那是改进结果不是基准坏掉），
并额外追加 3 项：

9. 对照文档与前后两份 JSON 数字一致；
10. v1.1 有提升且无 pass→fail 回归；
11. v1.0 基线产物未被覆盖（跑前跑后对四个文件算 SHA-256，
    「文件存在」不算通过——存在但内容被改写同样判失败）。

## 11. 扩展

- **加场景**：往 `SCENARIO_TABLE` 追加 `(id, target, tier)`，同步更新基线文档；
- **加档位**：在 `TIERS` 里加一项（`ratio` 或 `fixed_yield`），并决定它属于
  `QUALIFIED_TIERS` / `DEFICIENT_TIERS` / `INTERRUPTED_TIERS` 哪一组——
  分组会影响分层统计与断点分析；
- **换目标字数**：直接改 `SCENARIO_TABLE` 里的 target，取值范围受引擎限制
  500–20000。

## 12. 非目标

- 不判断内容质量（连贯性、重复、跑题）——只测字数收敛；
- 不测真实模型延迟或成本；
- 不测 `context_builder` 的上下文增长成本；
- 不做跨机器性能比较。
