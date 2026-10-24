# LongFormBench

墨屿一致性引擎的**可复现量化基线**。它构造一个规模可控的合成长篇项目，注入已知矛盾，
再跑 `backend/moyu_consistency.py` 的 `analyze()`，输出召回率、误报数、按类召回与耗时。

这不是「跑通测试」，而是对外可信度的证据：数字由命令直接产出，没有手工填数。

## 快速开始

```bash
# 仓库根目录，Python 3.11+
.venv/bin/python benchmarks/longform/run.py --chapters 40
```

一条命令完成：生成合成项目 → 分析干净基线 → 注入已知矛盾 → 再次分析 →
匹配 ground truth → 落盘报告。全部数字见 `results/longform-baseline.json`。

预期输出（本机实测，40 章 / 105,182 汉字）：

```
结论：PASS
总体召回率：100.0%（35/35）
精确率：100.0%，F1：1.000
干净基线：0 findings，score=100.0
分析耗时（中位数 / 5 次）：2.40 ms
```

退出码 `0` 表示全部验收项通过，`1` 表示有验收项未达标（报告仍会落盘），
`2` 表示参数非法（如章节数不足）。

> **章节数下限为 25**：注入集依赖 15 章跨度（伏笔断线规则）与彼此独立的章节锚点，
> 章节过少时锚点会挤在同一章并产生与引擎无关的假误报，因此 `inject()` 直接拒绝。
> 但「正文 ≥ 10 万汉字」是硬验收项，只有 `--chapters 40` 及以上才能满足；
> 25–39 章可用于调试，运行结果会是 `FAIL`（仅字数项不达标，检出指标仍然有效）。

### 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--chapters` | `40` | 章节数；40 章约 10.5 万汉字（**最小 25**，低于 25 直接报错退出） |
| `--seed` | `20261023` | 随机种子，决定正文与数据集内容 |
| `--repeat` | `5` | `analyze()` 重复次数，取中位数 |
| `--work-dir` | `benchmarks/longform/.work` | 临时 SQLite 目录（默认运行后清理） |
| `--out` | `benchmarks/longform/results/longform-baseline.json` | JSON 报告 |
| `--summary` | `benchmarks/longform/results/longform-baseline.md` | Markdown 摘要 |
| `--doc` | `docs/consistency-baseline-2026-10-23.md` | 对外基线文档；传 `""` 不写 |
| `--min-recall` | `0.95` | 召回率验收阈值 |
| `--max-analysis-seconds` | `5.0` | 单次分析耗时验收阈值 |
| `--keep-work` | 关闭 | 保留临时 SQLite 便于排查 |

## 目录

```
benchmarks/longform/
├── generate.py    # 生成合成项目（正文 + 结构化数据）写入 SQLite
├── inject.py      # 注入已知矛盾，产出 ground truth
├── run.py         # 一键流程 + 指标计算 + 报告渲染
├── README.md
├── .work/         # 临时数据库（gitignored，运行后自动清理）
└── results/       # 落盘报告
```

## 数据集构成

`generate.py` 用固定种子生成一部武侠/悬疑长篇，正文由模板化 `ProseWriter` 产出，
同时写入与正文一致的结构化数据：

| 维度 | 40 章规模 |
| --- | ---: |
| 汉字数 | 105,182 |
| 章节数 | 40 |
| 实体（人物/地点/物品/组织/妖兽/已归档） | 36 |
| 断言（canon + candidate） | 292 |
| 角色状态 | 40 |
| 时间线事件 | 50 |
| 关系 | 38 |
| 伏笔条目 / 链接 | 42 / 82 |
| 来源片段 | 80 |
| 文档修订 | 40 |

建库直接执行仓库内的 `docs/schema-v1.sql` 与 `docs/schema-v1.1-migration.sql`，
再补 `backend/server.py` 中 `app_*` 表的 DDL，保证与被测系统同构。

### 干净项目必须零 finding

数据集刻意设计了 **10 条 hard negative**（看似矛盾、实则合法），它们必须不被误报：

| id | 设计意图 |
| --- | --- |
| `hn-fact-01` | 同一谓词两个取值，但有效期不重叠 |
| `hn-fact-02` | 有效期重叠，但 `supersedes` 链明确取代 |
| `hn-fact-03` | `author_plan` 与 `world_truth` 并存（创作计划不算矛盾） |
| `hn-attr-01` | 角色属性变化，但有效期不重叠 |
| `hn-attr-02` | 同一属性同一取值，重叠记录 |
| `hn-relation-01` | 重复关系，但其中一条已 `inactive` |
| `hn-foreshadow-01` | 已归档伏笔只有回收记录 |
| `hn-foreshadow-02` | `develop` 伏笔未回收，但未超过 15 章阈值 |
| `hn-evidence-01` | 证据条目不是 `source_span`（合法外部引用） |
| `hn-timeline-01` | 同一章节两个事件，但故事时间相同 |

## 注入的已知矛盾

`inject.py` 注入 **35 条**矛盾，覆盖引擎的全部七个类别：

| 类别 | 条数 | 覆盖的检测分支 |
| --- | ---: | --- |
| `fact` 事实矛盾 | 6 | 同一 subject+predicate 重叠有效期取值冲突；`has_story_fact` 改名 |
| `timeline` 时间线矛盾 | 5 | 事件挂已删除章节；`valid_start > valid_end`；同章多时间；同序位多章节 |
| `knowledge` 知识越界 | 4 | 人物相信自己不知道的事（high）；私密记忆升为世界事实（medium）；非人物实体持有信念（low） |
| `attribute` 属性漂移 | 4 | 重叠时间内同属性不同取值 |
| `relation` 关系矛盾 | 5 | 端点缺失 / 端点已归档 / 自指 / 重复 active |
| `foreshadow` 伏笔断线 | 5 | 只有 payoff 无 plant；`stage=payoff` 无链接；埋设 ≥15 章未回收；`setup` 无链接 |
| `evidence` 来源失效 | 6 | 空证据（blocker）；缺复核人（blocker）；span 不存在 / stale / 旧 revision |

### ground truth 与匹配

每条注入带稳定 id（如 `inj-fact-01`）、类别、期望严重度和**匹配锚点**。
锚点是 finding `evidence` 里的业务主键（`assertion_id` / `plot_id` / `relation_id` /
`event_id` / `character_state_id`）——因为引擎的 finding id 是内容哈希，无法预知。

匹配规则：对每条 finding，在同类别 ground truth 中找锚点命中数最高的一条；命中即算检出，
否则计入误报。每条注入期望**恰好 1 条** finding。

## 指标定义

| 指标 | 定义 |
| --- | --- |
| 召回率 recall | 被检出的注入数 / 注入总数 |
| 精确率 precision | 命中注入的 finding 数 / finding 总数 |
| 误报数 | 未匹配到任何 ground truth 的 finding 数 |
| 按类召回 | 每个 category 的 detected / injected |
| 干净基线 | 未注入时 `analyze()` 的 finding 数，必须为 0 |
| hard negative 触发数 | 干净基线上的 finding 数（与上同源） |
| 分析耗时 | `analyze()` 墙钟时间，重复 `--repeat` 次取中位数，首次单列 |
| 规模归一化指标 | 每万字 findings、每 10 万汉字耗时、每实体断言数——与进程内存无关，可跨数据集比较 |

## 输出

`results/longform-baseline.json` 顶层结构：

```
benchmark / benchmark_version / generated_at / verdict
environment    Python、SQLite、平台、引擎文件 SHA-256
config         chapters、seed、repeat、阈值
dataset        字数、各表行数、hard negatives
injection      35 条注入的规格与分类计数
clean_baseline 干净库 findings / score / hard negative / 耗时
analysis       脏库 score、severity 计数、各类 examined、耗时
detection      recall / precision / f1 / 按类 / 漏检 / 误报明细 / 逐条 ground truth
scale_metrics  规模与归一化指标
memory         峰值 RSS（仅参考）
acceptance     逐项验收结果
```

## 确定性

固定 `--seed` 时数据集内容完全确定；两次运行除 `generated_at`、耗时、RSS 外应逐字节一致。
可用下面的方式自检：

```bash
.venv/bin/python benchmarks/longform/run.py --chapters 40 --out /tmp/a.json --summary /tmp/a.md --doc "" --keep-work
.venv/bin/python benchmarks/longform/run.py --chapters 40 --out /tmp/b.json --summary /tmp/b.md --doc ""
.venv/bin/python - <<'PY'
import json
a, b = (json.load(open(p)) for p in ("/tmp/a.json", "/tmp/b.json"))
# 数据集、注入规格与检出结果必须逐字节一致；耗时/RSS 等测量值天然有抖动，不参与比较。
keys = ("dataset", "injection", "detection")
print("deterministic:", all(a[k] == b[k] for k in keys))
PY
```

## 扩展

* **加注入**：在 `inject.py` 的 `INJECTION_SPECS` 里加一条规格，再在 `inject()` 对应
  类别段落写入数据并调用 `record(...)` 登记锚点。注意 `character_state.source_assertion_id`
  有外键，需先建（可为 `candidate` 状态的）断言。
* **加 hard negative**：在 `generate.py` 里构造"看似矛盾实则合法"的数据，并追加到
  `hard_negatives` 列表；干净库仍须 0 findings。
* **加章节**：`--chapters 80` 即可，字数线性增长；注意注入里依赖 15 章跨度的伏笔规则。

## 已知局限

完整讨论见 `docs/consistency-baseline-2026-10-23.md` 的「已知局限」一节。要点：
合成数据非真实稿件、生成器与引擎同源、注入是规则级而非抽取级、匹配依赖 evidence 结构、
每条注入假设单 finding、仅覆盖 10 万字量级与中文。
