# 评测规范 v1

## 固定输入

每个 fixture 固定 schema_version、fixture_id、seed、model、prompt_version、branch graph、gold labels 和容差。禁止用未记录的人工修正改变 gold。

## 核心指标

- 证据召回 Recall@K = 命中 gold evidence 的候选数 / gold evidence 数。
- 证据精确率 Precision@K = 命中 gold evidence 的候选数 / 返回候选数。
- citation precision = 可由引用原句支持的结论数 / 带引用结论数。
- branch leakage = 从非祖先分支召回的事实数 / 全部事实召回数；门槛 0。
- stale citation rate = 引用已失效 span 的默认结果数 / 全部默认结果数；门槛 0。
- conflict recall = 命中的标注冲突数 / 全部标注冲突数；硬冲突最低 0.95。
- merge/split F1：实体合并与拆分分别计算 precision、recall、F1。
- revision distance：作者达到满意文本所需的最小编辑量；记录中位数和 p95。
- freshness SLA：正文提交到 FTS/结构化索引可读的 p50/p95 时间。
- recovery RPO/RTO：故障后可丢失数据时间和恢复时间，发布前各至少演练一次。

## 回归分层

S0 数据恢复、S1 正典/分支/证据、S2 检索/连续性、S3 交互/成本。任何 S0/S1 失败阻止发布；S2 必须达到门槛；S3 记录趋势，不得冒充可靠性证明。

## 可复现计算约定（v1.1）

- 默认 K=5；评测同时报告 K∈{1,5,10}，不得只报最优 K。
- 分母为 0 时指标记为 `NA`，不计入宏平均；发布报告必须同时给出样本数和 NA 数。
- branch leakage、stale citation、无证据 canon 写入、损坏导入接受率目标均为 0；硬冲突 recall≥0.95；可撤销 AI patch 率=1。
- freshness SLA 基线：p50≤5 秒、p95≤30 秒；RPO≤5 分钟、RTO≤15 分钟。未实测时标记 `unmeasured`，不得当作通过。
- 每个 fixture 必须由 runner 返回非零退出码表示失败，并生成 JSON 指标快照，包含 fixture_id、schema_version、model/prompt_version、seed、样本数、K、指标和阈值结果。
