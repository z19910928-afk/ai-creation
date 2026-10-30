# Q1 capable-provider arm 验收（2026-10-30）

## 0. 结论

在 **capable-provider** 契约下，Q1 的 3000 字 engine 口径为 **8/8 = 100.0%**；
把外部中断也计入的 `q1_capable_all` 为 **8/9 = 88.9%**，仍低于 90%，因此不把“全部尝试 90%+”写成达成。
旧 adversarial 口径 **7/9 = 77.8%**、`q1_target_met=false` **保持不变**，本报告不覆盖它。

本报告是工程能力证据，不是发布放行，也不把旧 verdict 平移到当前字节。

## 1. 为什么旧 Q1 在对抗式臂下不可达

| 场景 | 事实 | 算术结果 |
| --- | --- | --- |
| `sc-18` | 固定每轮 12–30 字；计入基线的 `hard_max_rounds=12`（24 是敏感性变体） | `12 × 30 = 360 < 2700`，到不了 3000 字下限 |
| `sc-19` | 外部预算只允许 2 轮；两轮上限 1740 + 731 = 2471 | `< 2700`；第三轮才可能到 2778，但引擎不能绕过外部预算 |
| `sc-15` | 首轮 4661 字，目标区间 3600–4400 | 超上限 261；不裁剪正文时 `first_pass=false` |

因此旧 `t79` 的 3000 字 8/9 在“provider 忽略所有提示”的样本/评分/中断语义下数学不可达；
要凑数只能改样本、评分、provider 或中断语义，均不做。

## 2. capable-provider 契约（新增，不替代旧口径）

- provider 服从引擎给出的 `max_characters` / `min_characters`；
- 调用方把首轮额度 (`round_budget(0, target)`) 合入开场提示；
- 外部中断 `budget_limited` / `canceled` 如实记录，并从 `q1_engine` 分母排除；
- 旧 adversarial baseline、评分、样本、默认值均不改；
- `q1_engine` 与 `q1_capable_all` 两个数必须同时出现，不能只报 100%。

## 3. 实测结果

运行命令：

```text
.venv/bin/python benchmarks/budgeted/capable.py \
  --out benchmarks/budgeted/results/budgeted-capable.json \
  --md benchmarks/budgeted/results/budgeted-capable.md
```

输出：

```text
q1_engine=8/8 (100.0%) q1_capable_all=8/9 all=18/20 digest=sha256:eca0f33fb429…
```

| 口径 | 通过 / 总数 | 比率 | 判定 |
| --- | ---: | ---: | --- |
| Q1 engine（3000，排除外部中断） | 8/8 | 100.0% | 达到 90% 门槛 |
| Q1 capable all（3000，含外部中断） | 8/9 | 88.9% | 未达到 90% |
| 全部目标（含外部中断） | 18/20 | 90.0% | 参考值 |
| legacy adversarial 3000 | 7/9 | 77.8% | `q1_target_met=false`，保持不变 |

关键场景：

- `sc-15`：PASS，4400 字 / 1 轮 / `target_reached`（恰好落在上限）。
- `sc-18`：PASS，2700 字 / 12 轮 / `target_reached`（恰好落在下限，耗时用满硬上限）。
- `sc-19`：FAIL，2318 字 / `budget_exhausted`；外部预算，不计入 engine。
- `sc-20`：FAIL，2157 字 / `canceled`；外部取消，不计入 engine。

## 4. 当前字节绑定

本报告生成时 HEAD 为 `2a404cc`；下方是当前工作树中本 arm 的字节指纹。
任何历史 verdict 只有在它绑定的文件 sha256 与本表相同，才可按 `content-bound` 复用；
否则一律视为历史证据，必须重跑，不得写成“当前通过”。

| 文件 | 行数 | sha256 |
| --- | ---: | --- |
| `backend/moyu_continuation.py` | 899 | `4486b78398b6c826946083df08c8b888575ae8c3bfb1ca3a1219b4aaca71a874` |
| `backend/server.py` | 8198 | `f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263` |
| `benchmarks/budgeted/run.py` | 3782 | `549a744afbdf49811826af258f2873d694b2cc06a265562f8fa8e4bc64ffa2c6` |
| `benchmarks/budgeted/capable.py` | 284 | `7059326560ee77e20e38f4bae786755fff36c2e0aee6d8b787a2b1105ed949eb` |
| `benchmarks/budgeted/results/budgeted-capable.json` | 815 | `150467607934ad8175e0112371044cb802beaee545c59f6c972afc6cd673c868` |
| `benchmarks/budgeted/results/budgeted-capable.md` | 24 | `c04bb5a1df3716b86da328430a8180b491c78e37aac9415af66b66d068c4c7bf` |
| `tests/q1_capable_fixtures.py` | 67 | `b2557da7cb5a54e547998659e29f307ef9d3992d0892e76ae94716aeac6c49ac` |
| `tests/current_bytes_fixtures.py` | 66 | `1d4562e16ef9724333e8fc31c4f966eddb1d98b8d3405388f423a4fa3501f55d` |
| `scripts/verify_manifest.py` | 85 | `9e383cdf2a5c80e6c1f57d5760eaa803e3b8d2e81e990a45cbd7824be57ee132` |
| `frontend/app.js` | 1906 | `e22c79e857542936b9c272bf36f90b2c142805e61cf009a6297d838291c05b58` |
| `frontend/styles.css` | 550 | `fb2b661f48f80d93218ad29e1306d6ed5c070252c9357b7a816da0c50d4d59b2` |
| `docs/product-contract-soft-target-2026-10-30.md` | 38 | `32841501994469534932924e40a5650faac6102b31e14e14d6efc525810b1016` |

`docs/current-bytes-manifest-2026-10-30.json` 是机器可读版本；
`scripts/verify_manifest.py` 可按该 manifest 重算并拒绝漂移。

## 5. 历史 verdict 的处理规则

| 历史族 | 旧绑定 | 当前处理 |
| --- | --- | --- |
| `t79` / Q1 3000 旧基准 | `budgeted-v1.2-baseline.json` 的 7/9 | 保留 failed；capable arm 不重开、不改写 |
| `sc-15` 旧失败 | `budgeted-baseline.json` 的 `4661/4000` | 保留为 adversarial 失败；capable 臂另记 PASS |
| `t93` / `t94` | 旧文档字节（`8126778`、`4426769a…` 等） | 只绑定当轮字节；不得平移到当前 |
| `t104` / 发布说明 | 旧 docs-only 提交与报告 | 只绑定当轮字节；不得写成当前发布放行 |
| 其他 content-bound verdict | 各自 input sha256 | 只有与上表相同才可复用；否则重跑 |

**结论：当前字节有唯一、可重算的指纹；旧 verdict 不被删除，也不会被冒充成当前通过。**
这是“历史 verdict 不与当前字节脱节”的治理方式，不是全量重跑，也不是发布放行。

## 6. 验证命令与边界

```text
.venv/bin/python tests/q1_capable_fixtures.py
# q1 capable fixtures: 24 checks ok

.venv/bin/python -m py_compile benchmarks/budgeted/capable.py tests/q1_capable_fixtures.py
# exit 0
```

边界：

- 这是 fake-provider 能力臂，不调用真实模型；真实模型 3000 字探针仍只有 2/3 带内，不可当 100%。
- `q1_capable_all=88.9%` 未达到 90%，不能对外写“Q1 全部场景 90%+”。
- 旧 adversarial 仍 77.8%，B4/Q1 未关闭；版本仍 0.1.3，不发布。
- `sc-18` 的 12 轮用满硬上限，属于边界通过，不是宽裕通过。
