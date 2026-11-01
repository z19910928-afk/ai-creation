# 预算族当前字节重验 — 2026-11-01

状态：**仅覆盖 continuation / budget 引擎与测试**，不是 Q1 达标、不是发布放行。

## 1. 绑定字节

| 文件 | 行数 | sha256 |
| --- | ---: | --- |
| `backend/moyu_continuation.py` | 899 | `4486b78398b6c826946083df08c8b888575ae8c3bfb1ca3a1219b4aaca71a874` |
| `backend/server.py` | 8198 | `f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263` |
| `benchmarks/budgeted/run.py` | 3782 | `549a744afbdf49811826af258f2873d694b2cc06a265562f8fa8e4bc64ffa2c6` |
| `tests/budgeted_continuation_fixtures.py` | 1284 | `a8cb9cf7070fa425f091c3ea956e4f40ff69ba093ef56e554abc3edc63d47165` |
| `tests/budgeted_continuation_http_fixtures.py` | 937 | `67e0aa21f6dfe3d9e296f47515f9d07871c31f7f6907bebd3c24d67330cf308a` |
| `tests/q1_capable_fixtures.py` | 67 | `b2557da7cb5a54e547998659e29f307ef9d3992d0892e76ae94716aeac6c49ac` |

## 2. 命令与结果

```text
.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py benchmarks/budgeted/run.py
# py_compile ok

.venv/bin/python tests/budgeted_continuation_fixtures.py
# budgeted continuation fixtures: OK {'cases': 56}

.venv/bin/python tests/budgeted_continuation_http_fixtures.py
# Ran 23 tests in 21.915s
# OK

.venv/bin/python tests/q1_capable_fixtures.py
# q1 capable fixtures: 24 checks ok

.venv/bin/python tests/current_bytes_fixtures.py
# current bytes fixtures: 5 checks ok (22 files)

.venv/bin/python scripts/verify_manifest.py
# current bytes manifest: ok (current-bytes-manifest-2026-10-30.json)
```

`q1_capable_fixtures` 的 24 项检查明确保留了以下结果：

- `q1_engine` 3000 字 **8/8=100.0%**；
- `q1_capable_all` **8/9=88.9%**（未到 90%）；
- 全部目标 **18/20=90.0%**；
- `sc-15`、`sc-18` 通过；`sc-19` 外部预算、`sc-20` 取消仍失败；
- legacy adversarial **7/9=77.8%、`q1_target_met=false`** 原样保留。

## 3. 预算基准重跑

```text
.venv/bin/python benchmarks/budgeted/run.py --compare --repeat 2
```

结果：**verdict PASS**。

- 20 个场景全部执行：20/20；
- 同 seed 重复 digest：`sha256:8c813e04224358a4cc4485c70e58955f3f952853d704356bec62b1ab4e017443`；
- 引擎未改写正文：20/20 哈希一致；
- v1.2 相对 v1.0 提升 +10.0pt，pass→fail 回归 0；
- 6 类停止原因全部出现：`target_reached`、`no_progress`、`max_rounds`、`budget_exhausted`、`canceled`、`overshoot`；
- 敏感性上界 18/20 标注为不计入 headline。

## 4. 基线元数据修正

重跑发现 `benchmarks/budgeted/results/budgeted-v1.1-baseline.json` 与 `budgeted-v1.2-baseline.json`
仍记录旧引擎 hash `61d170e5…`；当前引擎为 `4486b783…`。确定性 digest 未变，因此只把
`engine_sha256` 和两份预算文档里的引擎 hash 更新为当前值，没有修改场景结果或判定。

`budgeted-v1.1-timing.json`、`budgeted-v1.2-timing.json` 的墙钟耗时属于观测值，已从本次提交中排除，不伪造性能结论。

## 5. 边界

- 本报告绑定的是当前预算族字节，不是全仓库当前字节；没有重写 `t79`、`t88`、`t91`、`t96` 的历史状态。
- 这是 fake-provider 的确定性基准，不是真实模型 Q1 验收；真实模型 3000 字仍没有 3/3 稳定口径。
- `q1_capable_all` 仍是 88.9%，不能写成“Q1 90–100 已达成”；旧 7/9 仍是 77.8%。
- 本次没有改样本、provider、评分或中断语义，也没有把预算基准通过冒充发布放行。
