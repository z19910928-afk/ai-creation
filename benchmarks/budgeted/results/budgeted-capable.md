# Q1 capable-provider arm

> seed=20261023；provider 服从 `max_characters` / `min_characters`，调用方把首轮额度合入开场提示。

## 结论

| 口径 | 通过 | 总数 | 比率 | 判定 |
| --- | ---: | ---: | ---: | --- |
| Q1 engine（3000 字，排除外部中断） | 8 | 8 | 100.0% | 达成 |
| Q1 capable all（3000 字，含外部中断） | 8 | 9 | 88.9% | 未达成 |
| 全部目标（含外部中断） | 18 | 20 | 90.0% | — |

## 关键场景

- sc-15（4000 / volatile）：**PASS**，4400 字 / 1 轮 / `target_reached`。
- sc-18（3000 / stalling）：**PASS**，2700 字 / 12 轮 / `target_reached`。
- sc-19（3000 / budget_limited）：**FAIL**，2318 字 / `budget_exhausted`；外部预算中断，不计入 Q1 engine。

## 不能混用的旧口径

- legacy adversarial 3000：7/9（77.8%），`q1_target_met=False`；它是“模型不服从提示”的下界，不是本臂。
- 本臂不修改旧 baseline、不改变旧结论，也不把 `q1_engine` 的数字写成 legacy Q1。

确定性摘要：`sha256:eca0f33fb4295a4d2e286186cecd1d097aa837de7ee28db9ad4e8281affeebe3`
