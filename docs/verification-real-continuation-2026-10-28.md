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
- **小样本探针**：脚本直接读取本机应用库并重绑临时凭据，未入库，不作为 CI 基准；本报告记录的是本次 job 的实证，不是可重复的 Q1 基准。job id 与 `app_ai_job` 结果保存在本机临时库，未提交。

## 5. 结论

- 真实模型 3000 字链路已验证可用；单次 `ratio=1.0` 带内到达，但同一 `1.0` 重复 3 次只有 2/3 带内（见 §6）。
- `ratio=0.9` 无法通过 HTTP 配置：接口返回 400，额度系数下限是 `1.0`；`ratio=2.0` 的三次尝试也只有 2/3 成功且带内，另 1 次是提供方中途报错（见 §7）。
- sc-15 的 `overshoot` 显式标记在真实模型上也被触发，说明停止原因语义与真实超冲一致。
- Q1 保持未达标；本轮不发布、不 bump、不把真实模型单次探针写成发布放行。

## 6. `ratio=1.0` 重复 3 次：2/3 带内

2026-10-28 21:19–21:21，在同一临时库、同一 `default-project`、同一模型与同一目标区间下，用同一生产 HTTP 探针重复 `round_target_ratio=1.0` 三次：

| 次 | job id | 字数 | `stop_reason` | `within_tolerance` | 轮数 | 12-gram 重复率 | tokens | 耗时 |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | `aijob_cc79e1530a62412f8fa6329038ca7c2f` | 2856 | `target_reached` | `true` | 2 | 0.004218 | 12,704 | 44.3 s |
| 2 | `aijob_c422455f2a2e418a836273bceef1c612` | 3134 | `target_reached` | `true` | 2 | 0.000640 | 12,679 | 38.3 s |
| 3 | `aijob_7963cfb9bcb74177a4ef4d5c2d7e0eec` | 3714 | `overshoot` | `false` | 2 | 0.000270 | 13,522 | 32.3 s |

三个 job 均 `status=success`、`model=returned_model=deepseek-v4.1-flash`、`completion_state=complete`、`protocol_complete=true`、`issues=[]`、`semantic_verified=false`、`safe_to_apply=true`。

- 带内：**2/3 = 66.7%**；第 3 次 3714 字超出上界 414 字，`overshoot` 如实标记，未裁剪正文。
- 因此 `ratio=1.0` 只能证明“本次/本模型链路可选口径有时能带内”，不能写成稳定通过率，更不能据此改默认值。
- 样本只有 3 次，不能外推为统计分布；重复结果进一步说明 3000 字目标仍受真实模型波动影响。

## 7. `ratio=0.9` 不可配置与 `ratio=2.0` 对照（2026-10-28 22:00–22:03）

### 7.1 `0.9` 被 HTTP 契约拒绝

在真实 HTTP 队列上分别提交三次 `round_target_ratio=0.9`，均返回：

```text
400 每轮额度系数必须在 1 到 10 之间。
```

三次都未创建 job，因此没有 `0.9` 的字数结果，也不存在“降到 1.0 以下减少超冲”的可配置路径。`1.0` 是当前 HTTP 下限；这一点必须保留，不能把 `0.9` 写成待验证臂。

### 7.2 `2.0` 三次尝试：2/3 带内 + 1 次提供方错误

| 次 | job id | 字数 | `stop_reason` | `within_tolerance` | 轮数 | 12-gram 重复率 | tokens | 耗时 |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | `aijob_99929df79b7949458cce375c25e6f4af` | 2721 | `target_reached` | `true` | 3 | 0.004428 | 26,353 | 40.4 s |
| 2 | `aijob_0935a3fc6a7d4567ae1205cbe32207c5` | 907 | `failed` | `false` | 1 | 0.000000 | — | 52.5 s |
| 3 | `aijob_dabbddc21a1e4d22ba2fbc96da17bef4` | 2778 | `target_reached` | `true` | 2 | 0.000361 | 19,894 | 44.4 s |

- 第 2 次 job 是 `status=failed`，`error=提供方在生成途中返回错误。`，只生成 907 字；这不是 `overshoot`，也不是 Q1 失败样本，不能把 `usage=null` 当 0 tokens。
- 按全部尝试算：**2/3 成功且带内**；按成功 job 算：2/2 带内。两种口径必须分别写明，不能只报 2/2。
- 结论：`1.0` 是 2/3 带内 + 1 次超冲，`2.0` 是 2/3 成功带内 + 1 次提供方中途错误；**没有 3/3 的稳定口径**，本轮停止在系数上刷样本，不改默认、不改 Q1 分母。
