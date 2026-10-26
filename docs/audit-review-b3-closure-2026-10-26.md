# t98 B3 closure 独立复核

- 日期：2026-10-26
- 复核人：`verifier-zz`（未参与 t91/t92/t93/t94/t95/t97 的实现或原验收裁决）
- 范围：只判断 **B3＝独立复核缺位／未通过** 是否已由后续独立证据闭合。
- 本报告不改写 t91，不扩大 t93/t94/t97 的限定 pass，不关闭 B4/Q1，不批准发布；未改产品、测试、评分或既有报告，未 commit/push，未读取或调用 key。

## 0. 明确裁决

**B3_closable: yes**

**B3_CLOSED: true**

理由：t91 已独立完成字节、CI、Q1、t79、11/23 五项复现，其 `needs_revision` 的 findings 只有 F1/F2；t92 修复后，t93 对 F1/F2 给出独立、限定范围的 pass，t94 又对当轮当前字节完成独立 content-bound 验收，t95 将发布说明更新到当前 HEAD/CI 并保留全部边界。因此，“独立复核缺位／未通过”这一 B3 阻塞已有替代独立复核闭环。

**边界：本结论只关闭 B3。t91 的历史 `verdict = needs_revision` 原样保留，不改写成 pass；B4/Q1 仍未达标，发布仍不放行。**

## 1. 四个指定文件的字节绑定

验收开始前采集一次；报告写入后于 2026-10-26 17:32:52 +0800 按同一命令重采。末态对账：**四文件 sha256 与行数全部一致（4/4）**。

| 文件 | sha256（开始／结束一致） | `wc -l`（开始／结束一致） |
| --- | --- | ---: |
| `docs/audit-review-independent-2026-10-26.md`（t91） | `c18e54d60f6308cb202872e6492657e4778bfebc633fd54a573d3ed1cd7f5c6c` | 175 |
| `docs/verification-release-readiness-2026-10-26.md`（t93） | `397a2f331e745bbd495a19d94f969f8e35be9581377e60bcb26e8fd0416f4727` | 134 |
| `docs/verification-current-release-2026-10-26.md`（t94） | `4426769a9fd0413f07953d55cdc6cec3df90cb68758da4cbf9965cf6d418c31b` | 124 |
| `docs/release-readiness-2026-10-25.md`（t95 后） | `52c7c078be5ca3cb849787f37fb7c6c7baf50d0a6566b1dd72e9cdb94657a7ac` | 296 |

新报告创建前不存在，故不虚构创建前 hash；其最终 hash/行数在任务 output 单独给出，不内嵌自身 hash。

## 2. t91：独立复核已经存在，阻塞只在 F1/F2

| 检查 | 独立证据 | 判断 |
| --- | --- | --- |
| 原 verdict 保留 | `docs/audit-review-independent-2026-10-26.md:139` 为 `verdict = needs_revision`；`:173` 不把 CI 绿当生产放行 | 原判不改写 |
| 独立性已补齐 | 同文件 `:141` 明确五项独立复现：字节绑定、CI、Q1 双分母、t79 数学不可达、11/23 边界 | B3 不是“复核缺位” |
| findings 只有两项 | 同文件 `:145` 为 F1（desktop 比例写法），`:146` 为 F2（旧产物 generated_at）；findings 表无 F3+ | t91 未 pass 的直接原因仅 F1/F2 |
| F1 原始问题 | 同文件 `:83-106`；尤其 `:106` 独立复跑 1/1 失败，证明 6/7 不是稳定通过率 | 需要文档收窄，不是伪造成稳定比例 |
| F2 原始问题 | 同文件 `:129`、`:146`：旧硬编码时间戳与 mtime/当前 `datetime.now()` 不符，应披露 | 需要产物引用口径收窄 |
| Q1/t79 无回退依据 | 同文件 `:56-59` 为 3000=7/9、4000=4/6、Q1 false；`:73-81` 为 sc-18 理论上界 360、sc-19 理论上界 2471（闭式 2470.8），均 <2700 | 8/9 数学不可达；蒙特卡洛 sc-19 最大 2470 不与理论上界 2471 混淆 |
| 11/23 边界 | 同文件 `:108-127`：23 条中 11 条可见，禁止外推 23 条语义正确 | 未闭合的 12 条是证据边界，不是伪造通过 |

## 3. F1/F2 修复及独立验收

### F1 — desktop 历史观测口径

- t93 独立验收 `docs/verification-release-readiness-2026-10-26.md:55-68` 逐项通过：7 跑 6 过仅属一次历史会话，t91 的 1 跑 1 挂属于另一独立会话；不得称稳定 6/7、14% 偶发率或 26/26 全绿。
- t95 后发布说明 `docs/release-readiness-2026-10-25.md:183` 标明“仅该会话样本，不是稳定通过率”；`:192-198` 同时保留 t91 的 1/1 失败与 55 次激活尝试，明确禁止稳定比例和 26/26 表述；`:261`、`:276` 再次保持同一口径。
- **F1_CLOSED: yes（仅限文档 finding）**。desktop 焦点竞态本身未修，不能外推 desktop 全绿。

### F2 — 旧产物 generated_at 口径

- t93 独立验收 `docs/verification-release-readiness-2026-10-26.md:70-85` 通过：旧时间戳为硬编码、不可当生成时刻，引用以 mtime/hash 为准，当前提取器使用 `datetime.now()`。
- t95 后发布说明 `docs/release-readiness-2026-10-25.md:208-213` 明示 `2026-10-24T00:00:00+08:00` 是旧脚本硬编码值，不能当生成时刻，以 mtime/sha256 为准，并保持 11/23 边界。
- **F2_CLOSED: yes（仅限文档 finding）**。不重跑真实模型，不宣称旧产物时间已变成真实生成时刻。

### t93/t94 的 pass 不扩张

- t93 报告 `:7-8` 明确不是把 t91 改成通过，pass 只覆盖 F1/F2 文档收窄，不表示 Q1、desktop 26/26 或 B3 关闭。
- t94 报告 `docs/verification-current-release-2026-10-26.md:6` 仅对当轮当前字节 pass；`:91` 保留 t91 原判和 B3 未关闭；`:123` 明确不关闭 B3/B4、不发布。
- t94 的完整证据为 95/95 fixture、6/6 evaluation、7/7 py_compile、33/33 文档探针、11/11 前后字节一致；这些是其当轮验收证据，本 t98 不冒充重新执行。
- t94 绑定的发布说明是 t95 前的 267 行版本；当前 t95 后发布说明为 296 行，二者 hash 不混淆。t95 在 `:239-243` 和 `:287-294` 明确同步当前证据但仍不关闭 B3/B4。

## 4. 当前发布说明无关键口径回退

| 项 | 当前证据 | 结论 |
| --- | --- | --- |
| HEAD/CI | `docs/release-readiness-2026-10-25.md:21`、`:54-62`：HEAD `81267784857e78b7732ef0e14ff05184bf69cf06`，CI `40290743348` 为 completed/success；本任务 `gh run view` 复核一致 | 通过 |
| Q1/B4 | 同文件 `:23-25`、`:103-107`：3000=7/9=77.8%，4000=4/6=66.7%，`q1_target_met=false`；`:256` B4 未关闭 | **未达标，不关闭** |
| t79 | 同文件 `:132-137`：12×30=360<2700；结合 t91 `:77` 的 sc-19 上界 2471<2700 | 数学不可达，不重跑 |
| 真实模型证据 | 同文件 `:204-227`：repeat=1、分母不可合并、23 条仅 11 条可见、12 条未落盘 | 边界保留 |
| 版本 | 同文件 `:27`、`:253`：仍为 0.1.3，不 bump、不打 tag | 未制造未验证 RC |
| t91 原判 | 同文件 `:165-177`、`:255`、`:285-294` | needs_revision 保留；t93/t94 pass 均限定 |
| 旧 HEAD/CI | 同文件 `:57-60` 仅在历史表；当前行 `:56` 与正文为 8126778/40290743348 | t93/t94 的旧 HEAD 文档漂移已由 t95 收窄 |

## 5. 未闭合 findings 与关闭边界

1. **t91 F1/F2：已闭合**，依据见 §3；但 t91 报告及其历史 verdict 不修改。
2. **t93/t94 曾记录的旧 HEAD/CI 与坐标漂移：已由 t95 更新**，当前行与历史行已分离。
3. **T97-F1（low）仍未闭合**：`docs/verification-release-readiness-current-t97-2026-10-26.md:99-101` 指出发布说明 `:13` 仍有“工作树 clean”措辞，而当前工作树含未提交文档。t97 已明确 CI 不绑定这些未提交字节。该 low 属于发布材料措辞精度，不是独立复核缺位，不改变 F1/F2 已修复或五项独立证据，故不阻断 B3；后续文档应收窄，本任务不越权修改。
4. **B4/Q1、desktop 焦点环境问题、12 条无逐字证据：均未闭合**，但不属于本次 B3 的“独立复核缺位／未通过”定义，绝不能借 B3 关闭而视为通过。

## 6. 独立探针与合同命令

| 命令/检查 | 实测结果 |
| --- | --- |
| `gh run view 40290743348 --json headSha,conclusion,status` | completed/success；headSha=81267784857e78b7732ef0e14ff05184bf69cf06；exit 0 |
| `.venv/bin/python /tmp/t98_probe.py` | **TOTAL=38 PASS=38 FAIL=0**；exit 0 |
| `git diff --check` | exit 0，无输出 |
| `.venv/bin/python tests/repository_privacy_fixtures.py` | `repository privacy fixtures: ok (Git index only)`；exit 0 |

探针独立检查四文件 hash/行数、t91 verdict/findings/五项证据、F1/F2 修复、Q1/t79/11-23/版本边界、t93/t94 限定 pass、t95 当前值、t97 low、CI，以及 360/2471<2700 的纯算术。未调用真实模型，未运行 desktop，未把其他任务的命令冒充本任务实测。

## 7. 最终关闭语句

**B3_closable: yes；B3_CLOSED: true。** 关闭对象仅为“独立复核缺位／未通过”。t91 保持 needs_revision；t93/t94/t97 的限定 pass 不扩张；B4/Q1 保持未达标；不发布、不 bump、不打 tag。
