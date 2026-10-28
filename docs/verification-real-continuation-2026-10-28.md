# 真实模型 3000 字续写探针（2026-10-28）

> 范围：本机一次性生产 HTTP 探针。不是 Q1 fake-provider 基准，不是发布放行，也不证明语义、文风或事实质量。密钥未打印、未写入仓库。

## 1. 目的

验证已配置的 `zzniu` / `deepseek-v4.1-flash` 在**生产 HTTP 队列**上能否完成 3000 字续写，并观察三档每轮额度口径的实际停止原因：

- 默认口径：`round_target_ratio=None`，每轮额度按上界减已写；
- 旧对照口径：`round_target_ratio=1.35`；
- 收紧口径：`round_target_ratio=1.0`。

## 2. 方法

- 读取本机应用数据库 `~/Library/Application Support/Moyu/moyu.sqlite3` 的 `default-project` 模型设置；复制到临时 SQLite 数据库，并在临时凭据目录重建 `_credential_ref`，不修改原库。
- 用临时库启动生产服务 `python -m backend.server`，`MOYU_HOST=127.0.0.1`，私有端口 8876/8877/8878；`GET /api/ai/status` 返回 `mode=provider`。
- 每个臂新建一个章节，正文使用同一段开篇（约 60 字），提交 `POST /api/ai/queue`：

```json
{
  "task": "continue",
  "response_format": "json",
  "stream": false,
  "budgeted_continuation": {
    "target_characters": 3000,
    "max_rounds": 6,
    "min_round_gain": 50,
    "max_stall_retries": 1,
    "hard_max_rounds": 12
  }
}
```

  第三个臂额外带 `"round_target_ratio": 1.0`，第二个臂带 `1.35`；第一个臂不传该字段。
- 轮询 `GET /api/ai/jobs/<id>` 到终态；只记录 `app_ai_job` 的 `status`、`result_json`、`budgeted_continuation`、`usage`，不保存密钥。

## 3. 结果

| 臂 | `round_target_ratio` | job id | 字数 | `stop_reason` | `within_tolerance` | 轮数 | 12-gram 重复率 | tokens | 耗时 |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| 默认 | `null` | `aijob_eb57da9536c84f21aa769628cd0a0f93` | 3635 | `overshoot` | `false` | 3 | 0.001104 | 14,700 | 62.0 s |
| 旧对照 | `1.35` | `aijob_617b82fb8de64131bfbdb8c0bf217096` | 3571 | `overshoot` | `false` | 2 | 0.002247 | 9,299 | 38.2 s |
| 收紧 | `1.0` | `aijob_5b25dd4824064fddb7500b73fe1fb622` | 3176 | `target_reached` | `true` | 2 | 0.000316 | 10,600 | 40.2 s |

三个 job 的共同字段：

- `status=success`，`model=returned_model=deepseek-v4.1-flash`，`completion_state=complete`，`truncated=false`；
- `output_validation.protocol_complete=true`、`issues=[]`、`eligible=true`、`eligible_for_blind_review=true`；
- `hard_fact_status=requires_independent_review`，`semantic_verified=false`，`safe_to_apply=true`。

目标区间为 `[2700, 3300]`（3000 的 ±10%）：

- 默认臂 3635，超出上界 335 字；停止原因已由 sc-15 修复显式标为 `overshoot`，不再伪装成 `target_reached`；
- 1.35 臂 3571，仍超上界 271 字；
- 1.0 臂 3176，落在区间内，`target_reached` 且 `within_tolerance=true`。

## 4. 解释与边界

- **可以说的**：真实模型在生产 HTTP 链路上能写完 3000 字；在本次单次运行里，`round_target_ratio=1.0` 是唯一带内到达的臂。
- **不能说的**：这不是 Q1 3000 字一次通过率，不改变 fake-provider 的 **7/9 = 77.8% < 80%**；不改变 `q1_target_met=false`、t79 的数学不可达结论，也不关闭 B4。
- **不能说的**：`semantic_verified=false`，本探针没有人工/独立语义审计；低重复率不等于文学质量，`safe_to_apply=true` 只表示协议与机械校验通过。
- **默认值未改**：`round_target_ratio` 仍默认 `None`，因此既有行为与 HTTP 契约不变；本轮未重跑预算套件。`1.0` 只作为“目标字数优先”的可选口径，不擅自改产品默认。
- **一次性探针**：脚本直接读取本机应用库并重绑临时凭据，未入库，不作为 CI 基准；本报告记录的是本次 job 的实证，不是可重复的 Q1 基准。job id 与 `app_ai_job` 结果保存在本机临时库，未提交。

## 5. 结论

- 真实模型 3000 字链路已验证可用，且 `ratio=1.0` 在本次运行中带内到达。
- sc-15 的 `overshoot` 显式标记在真实模型上也被触发，说明停止原因语义与真实超冲一致。
- Q1 保持未达标；本轮不发布、不 bump、不把真实模型单次探针写成发布放行。
