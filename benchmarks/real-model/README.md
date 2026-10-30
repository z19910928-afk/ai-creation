# 真实模型续写对照基准

这是**客户端基准**，走生产 HTTP 队列，不进程内调用引擎。它回答的是：

- 真实模型在 `target_characters` 下实际写了多少字；
- `stop_reason` 是 `target_reached`、`overshoot` 还是失败；
- `within_tolerance` 是否落在 `[target - 10%, target + 10%]`；
- 同一配置重复 `--runs` 次的方差，以及全部尝试（含失败 job）的结果。

它**不是** Q1 的一次通过率基准，也不改变 `q1_target_met=false`。

## 前置条件

1. 服务端已启动，且 `GET /api/ai/status` 返回 `mode=provider`。
2. 服务端已配置模型。两种方式均可：
   - 使用应用内模型设置（脚本不需要 `MOYU_AI_*`）；
   - 用 `MOYU_AI_BASE_URL` / `MOYU_AI_API_KEY` / `MOYU_AI_MODEL` 启动服务端。
3. 使用隔离数据库，不要在真实作者库上跑。服务端的 `MOYU_DB` / `MOYU_PORT` 与模型配置由启动方设置；脚本只连 `--server-url`，不读取、不打印 key。

## 命令

```bash
# 不连服务端，只验纯函数
.venv/bin/python benchmarks/real-model/compare_round_target.py --self-check

# 复算真实模型 3000 字、ratio=1.0 重复 3 次
.venv/bin/python benchmarks/real-model/compare_round_target.py \
  --targets 3000 --runs 3 --ratio 1.0 \
  --out /tmp/real-continuation-3000-ratio100.json

# 基线与按目标摊平两臂对照
.venv/bin/python benchmarks/real-model/compare_round_target.py \
  --targets 1200,3000 --runs 1 --ratio 1.35 --baseline \
  --out /tmp/real-continuation-compare.json
```

`--out` 必填，且脚本拒绝覆盖已有文件；证据不会被静默改写同意。

## 输出口径

- `rows`：每一次尝试都保留；失败 job 也有 `job_status` / `error`，不会从分母消失。
- `summary.<arm>.first_pass`：只统计 `stop_reason == target_reached` 且 `within_tolerance == true`。
- `summary.<arm>.overshoot`：统计没有带内通过、但停止原因是 `target_reached` 或 `overshoot` 的目标；两者都按超冲证据保留。
- `summary` 不是质量结论：`semantic_verified`、文风、事实一致性仍需单独验收。

## 不做什么

- 不修改 Q1 样本、评分或 `q1_target_met`。
- 不把 `round_target_ratio` 的默认值改成探针里的临时值。
- 不把 `0.9` 写成“待验证”：HTTP 下限仍是 `1.0`，`0.9` 会被 400 拒绝。
- 不提交任何 key、临时数据库或 job 日志。
