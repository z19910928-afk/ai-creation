# t97 发布说明与 t94 报告当前字节独立验收

- 任务：t97（t96 替代验收，独立重做，不继承 t96 的 pass 消息）
- 执行人：verifier-zz
- 验收时间：2026-10-26 17:16:33 +0800
- 报告：`docs/verification-release-readiness-current-t97-2026-10-26.md`
- 裁决：**pass，仅覆盖 t97 指定的文档内容、当前字节、HEAD/CI 与文件存在性**
- 边界：不关闭 B3/B4，不改写 t91 `needs_revision`，不宣称 Q1 达标或发布放行

## 1. 文件存在性与历史异常

验收前实测：

| 文件 | 存在性 | 行数 | sha256 |
| --- | ---: | ---: | --- |
| `docs/release-readiness-2026-10-25.md` | 存在 | 296 | `52c7c078be5ca3cb849787f37fb7c6c7baf50d0a6566b1dd72e9cdb94657a7ac` |
| `docs/verification-current-release-2026-10-26.md` | 存在 | 124 | `4426769a9fd0413f07953d55cdc6cec3df90cb68758da4cbf9965cf6d418c31b` |
| `docs/verification-release-readiness-current-2026-10-26.md` | **不存在** | N/A | N/A |
| `docs/verification-release-readiness-current-t97-2026-10-26.md` | 验收前不存在，本任务新建 | N/A | N/A |

**T96-A1（历史异常）**：t96 的完成消息声称旧路径报告有 134 行及对应 hash，但旧路径在本任务验收前不存在，`git status` 也无该文件。该 pass 消息不能作为落盘证据；本报告独立重验，不复用其结论。

captain 中途修订输出路径后，t97 仅写带 `-t97-` 的新路径；旧路径始终未创建、未覆盖。

## 2. HEAD 与 CI

实测命令：

```bash
git rev-parse HEAD
gh run view 40290743348 --json headSha,conclusion,status
```

结果：

- HEAD：`81267784857e78b7732ef0e14ff05184bf69cf06`
- CI run：`40290743348`
- `status=completed`
- `conclusion=success`
- CI `headSha=81267784857e78b7732ef0e14ff05184bf69cf06`，与当前 HEAD 逐字一致。

发布说明第 21 行及第 56 行均把 `8126778` 标为当前 HEAD，并绑定 `40290743348`；与实测一致。

## 3. 五文件磁盘 hash 与行数

以下值由 `shasum -a 256` 与 `wc -l` 直接读取当前磁盘，并与发布说明第 83–87 行逐项比对：

| 文件 | 行数 | sha256 | 判定 |
| --- | ---: | --- | --- |
| `backend/server.py` | 8198 | `f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263` | PASS |
| `backend/moyu_consistency.py` | 1222 | `b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2` | PASS |
| `backend/moyu_continuation.py` | 896 | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | PASS |
| `backend/moyu_extract.py` | 1041 | `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8` | PASS |
| `tests/desktop_smoke.cjs` | 784 | `c6d13a0ea450a8eb14ba2450f6de6d61ba0c706c70be06e9821c3ba4d81ffaf4` | PASS |

五文件行数合计 12141；磁盘值、发布说明记录值、工单预期值三者一致。

## 4. 文档口径与 B3/B4 边界

独立纯读探针 `/tmp/t97_probe.py`：**TOTAL=44，PASS=44，FAIL=0，exit=0**。

覆盖并确认：

1. 当前 HEAD/CI 为 `8126778` / `40290743348`，且 CI 状态为 `completed/success`。
2. 旧 `bc4709e`、`40278484458`、`10cff13` 的每次出现均为历史口径，不再写成当前 HEAD/CI。
3. 发布说明第 243 行明确写出 B3（独立复核未通过）与 B4（Q1 未达标）均未关闭。
4. t91 `needs_revision` 在发布说明和 t94 报告中均保留；t93 的限定 pass 没有被扩张成 B3 通过。
5. 发布说明仍如实记录 Q1 3000 字 `7/9`、`q1_target_met=false`；本任务不重跑、不改写该结论。
6. t94 报告自身为 124 行、hash `4426769a…c31b`；发布说明第 241 行与第 292–293 行的绑定值与实测一致。

### t94 的历史字节范围

t94 报告第 39 行绑定的是其当轮发布说明：267 行、hash `31e498e0…f172`。t95 随后把发布说明更新为 296 行、hash `52c7c078…a7ac`。

因此，t94 的 pass 只证明 t94 当轮字节，不被冒充为对 t95 后文档内容的预先验收。本 t97 才针对当前两份文档重新核对；两者时间顺序与 hash 范围没有混写。

## 5. 合同命令

报告落盘后按修订后的输出路径执行：

| 命令 | 实测结果 |
| --- | --- |
| `test -f docs/verification-release-readiness-current-t97-2026-10-26.md` | 文件存在，exit 0 |
| `git diff --check` | exit 0，无输出 |
| `.venv/bin/python tests/repository_privacy_fixtures.py` | `repository privacy fixtures: ok (Git index only)`，exit 0 |
| `gh run view 40290743348 --json headSha,conclusion,status` | `completed/success`，headSha 与 HEAD 一致，exit 0 |

上述命令的真实退出码是本任务提交 `commandsRun` 的依据；不以报告文字替代执行。

## 6. 三文件前后字节对账

- 发布说明与 t94 报告：验收前已记录 hash 与 `wc -l`，完成全部检查后再次测量，必须保持一致。
- 新报告：创建前不存在，不虚构“创建前 hash”。采用“报告落盘后基线 → 合同命令与探针复核 → 末态”的两点 manifest 对账；复核期间不再编辑报告。
- 新报告最终 `ls -l`、`wc -l`、`shasum -a 256` 在任务 output 原样给出。报告不内嵌自身最终 hash，避免自引用导致 hash 失效。
- 三文件末态一致性的机器对账与报告回读是最终提交的硬门槛；任一不一致即不得 pass。

## 7. 独立观察

**T97-F1（low，文档措辞精度）**：发布说明第 13 行仍把“工作树 clean”列为技术预览的当前可验证性依据；本任务实测 `git status --porcelain` 非空，至少有 t95 修改的发布说明和 t94 未跟踪报告，落盘后还增加本 t97 报告。

CI success 绑定 HEAD `8126778`，不自动覆盖这些未提交文档字节。本任务改用逐文件 hash/行数绑定当前证据，不把 CI 结果扩张为“工作树 clean”。该观察不影响指定五文件 hash、HEAD/CI、历史口径与 B3/B4 探针结果，但后续发布材料应收窄或删除该 clean 表述。

## 8. 最终结论

**t97 = pass（限定范围）**：两个被测文件真实存在且初始 hash/行数符合工单；当前 HEAD/CI、五文件磁盘 hash、旧值历史口径、t94 hash 绑定、B3/B4 未关闭与 t91 `needs_revision` 均通过独立探针；t96 缺文件异常已记录且未继承其 pass。

本裁决只证明上述文档与当前字节证据自洽，不关闭 B3/B4，不批准 Q1，不批准发布，不替代 t98 的 B3 closure review。
