# 3000 字续写：±10% 软目标契约（2026-10-30）

## 决定

作者接受 **±10% 软目标**：目标 3000 字时，`[2700, 3300]` 为期望区间；超出区间不视为生成失败，**不裁剪正文**，但必须向作者明确提示超冲或未达目标。

这不是 Q1 的一次通过率口径。Q1 仍是 fake-provider 的 **7/9 = 77.8% < 80%**，`q1_target_met=false`；本契约只定义真实模型多轮续写的产品行为。

## 可观察契约

| 字段 | 含义 |
| --- | --- |
| `target_characters` | 作者设定的目标字数 |
| `target_range` | `{minimum, maximum}`，目标 ±10% 的整数闭区间 |
| `characters` | 实际正文的非空白字符数 |
| `within_tolerance` | 是否落在 `target_range` 内 |
| `stop_reason` | 达到下限为 `target_reached`；越过上限为 `overshoot`；未达下限可能是 `max_rounds` / `no_progress` / `failed` |

## 产品行为

1. **不硬裁正文**：超冲时保留模型已生成的全部正文，不用截断伪造“刚好 3000 字”。
2. **超冲明确提示**：`within_tolerance=false` 且 `characters > maximum` 时，界面显示 `⚠ 超出目标区间：X 字 > 上限 Y 字，正文未裁剪`。
3. **未达明确提示**：`characters < minimum` 时显示 `⚠ 未达目标区间：X 字 < 下限 Y 字，正文未裁剪`。
4. **仍可采纳**：软目标不是硬失败；作者看完提示后自行决定保留、继续生成或手工修改。
5. **不把超冲说成达标**：`overshoot` 不得显示为“已完成目标”，也不得从 `within_tolerance` 推断为 true。

## 当前实现

- 前端 `frontend/app.js`：新增 `continuationWarning(plan)`，在 `#aiStatus` 的可见文本追加区间警告，并设置 `data-ai-warning="true"` 供样式使用；`data-ai-progress` 仍只保存轮数/字数/目标/判定，保持机器可读口径。
- `frontend/styles.css`：`.ai-status[data-ai-warning="true"]` 使用琥珀色加粗，避免与普通状态文字混淆。
- 后端不新增字段：`stop_reason`、`within_tolerance`、`target_range`、`characters` 已由多轮续写报告返回。
- 回归：`tests/frontend_browser_regressions.cjs` 断言超冲文案包含“超出目标区间”“正文未裁剪”、`data-ai-warning=true`，并覆盖未达目标警告。

## 边界

- 本契约不改变 `round_target_ratio` 的默认值；HTTP 下限仍为 `1.0`，`0.9` 会被 400 拒绝。
- 真实模型探针 `1.0` 与 `2.0` 都只有 2/3 成功带内；软目标接受这一波动，不等于承诺 3/3 稳定。
- 不改变 Q1 样本、评分、`q1_target_met=false` 或 t79 的数学不可达结论。
