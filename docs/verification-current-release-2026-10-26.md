# 当前发布候选 content-bound 独立验收

**日期**：2026-10-26  
**任务**：t94（kind=verification，deps=无）  
**验收人**：verifier-zz（独立验收，只读产品/测试/历史报告）  
**结论**：**pass（仅 t94 当前字节验收）**。HEAD、CI、全量 Python fixture、evaluation、py_compile、文档事实探针和前后字节对账均通过；**不代表 Q1 达标、不代表发布放行、不改写 t91 的 `needs_revision`**。

## 零、被测提交与工作树

| 项 | 实测 |
| --- | --- |
| HEAD | `81267784857e78b7732ef0e14ff05184bf69cf06` |
| 验收开始 `git status --porcelain` | 空（exit 0） |
| CI run | `40290743348` |
| CI headSha | `81267784857e78b7732ef0e14ff05184bf69cf06` |
| CI 状态 | `status=completed`、`conclusion=success` |
| HEAD 与 CI headSha | 逐字符一致 |
| CI URL | https://github.com/z19910928-afk/ai-creation/actions/runs/40290743348 |

起始状态：`/tmp/t94-git-before.txt`。验收期间没有修改产品代码、测试、benchmark、脚本或历史报告；本报告是唯一新增的仓库文件。

## 一、前后 sha256 与 wc -l

- 起始采集：2026-10-26 16:41:50 +0800（`/tmp/t94-before.txt`）。
- 结束采集：2026-10-26 16:55:04 +0800（`/tmp/t94-after.txt`）。
- 对账：`diff` 无差异，`BYTES_UNCHANGED`；**TOTAL=11 files, PASS=11, FAIL=0**。
- `backend/server_bind` 独立配置文件未发现；回环绑定实现位于 `backend/server.py`，对应测试 `tests/server_bind_fixtures.py` 一并纳入字节清单。

| 文件 | 起始=结束 sha256 | 起始=结束行数（wc -l） | 对账 |
| --- | --- | ---: | --- |
| `backend/server.py` | `f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263` | 8198 | 一致 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9` | 896 | 一致 |
| `backend/moyu_extract.py` | `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8` | 1041 | 一致 |
| `backend/moyu_consistency.py` | `b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2` | 1222 | 一致 |
| `benchmarks/real_corpus/extract.py` | `2e046994280e26beaa89c5b9f194c992c341ae057efeb64ed0674f5a5de8e201` | 301 | 一致 |
| `tests/extract_fixtures.py` | `f48b4f191d7fa390962539dd69c1a49453354351c3615350bf2246db1f2f71b5` | 1372 | 一致 |
| `tests/server_bind_fixtures.py` | `92ab9535d83f179e15b2935079382d8628240a00d2e0d521ab2cc5d62be75292` | 32 | 一致 |
| `package.json` | `a8ea4f320c8916c9ca856bb3320d9f5c79e95f5ecb7ae2658788a98b5c1a6288` | 23 | 一致 |
| `docs/release-readiness-2026-10-25.md` | `31e498e0106e0c801675f791a01e574582f4b8cf1fbeace7bb72c2766a88f172` | 267 | 一致 |
| `docs/verification-release-readiness-2026-10-26.md` | `397a2f331e745bbd495a19d94f969f8e35be9581377e60bcb26e8fd0416f4727` | 134 | 一致 |
| `docs/audit-review-independent-2026-10-26.md` | `c18e54d60f6308cb202872e6492657e4778bfebc633fd54a573d3ed1cd7f5c6c` | 175 | 一致 |

## 二、全量 Python fixture

合同命令：

```bash
.venv/bin/python tests/repository_privacy_fixtures.py
for fixture in tests/*_fixtures.py; do .venv/bin/python "$fixture" || exit 1; done
```

- 隐私 fixture 单独执行：exit 0，输出 `repository privacy fixtures: ok (Git index only)`。
- 全量循环：exit 0，日志 `/tmp/t94-fixtures.log`。
- **TOTAL=95 fixture files, PASS=95, FAIL=0**；日志含 95 个唯一文件头，循环以 `|| exit 1` 执行并最终 exit 0，因此任一非零都会阻断、全部 95 个均为 0。
- 日志中 unittest 的 `Ran N tests` 合计 **1426 tests**；不使用该子集替代 95 个文件的文件级计数。
- 所有测试均使用本地/隔离 fixture；未记录评分，未读取或复述任何密钥。

## 三、evaluation_runner

```bash
.venv/bin/python scripts/evaluation_runner.py --fixtures-dir tests/fixtures --output /tmp/moyu-evaluation-current.json
```

- exit 0；JSON 顶层 `passed=true`。
- **TOTAL=6 evaluation fixtures, PASS=6, FAIL=0**。
- fixture_id：corrupt_import, evaluation_pass, longform_knowledge_scope, recovery_drill, reversible_patch, schema_scope。
- 六条均满足 `passed=true`、`fixture_passed=true`、`errors=[]`；存在 executable 的记录其 `returncode=0`。

## 四、py_compile

```bash
.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py backend/moyu_extract.py backend/moyu_consistency.py benchmarks/real_corpus/extract.py tests/extract_fixtures.py tests/server_bind_fixtures.py
```

- exit 0，输出 `PY_COMPILE_OK files=7`。
- **TOTAL=7 files, PASS=7, FAIL=0**。

## 五、发布文档只读探针

探针 `/tmp/t94_release_probe.py` 直接读取当前 JSON、源码和三份指定文档；日志 `/tmp/t94-release-probe.log`。

```bash
.venv/bin/python /tmp/t94_release_probe.py
```

- exit 0；**TOTAL=33 checks, PASS=33, FAIL=0**。
- Q1：3000 字 `7/9=77.8%`，4000 字 `4/6=66.7%`，`q1_target_met=false`；两个分母未合并。
- 版本：`package.json` 为 `0.1.3`，发布说明保持不 bump、不打 tag。
- t91-F1：desktop 仅为历史会话 7 跑 6 过与 t91 独立 1 跑 1 挂（55 次激活尝试），没有写成稳定通过率或 26/26 全绿。
- t91-F2：旧 `/private/tmp` 产物的硬编码 `generated_at=2026-10-24T00:00:00+08:00` 已披露为不可作生成时刻；当前提取实现使用 `datetime.now()`，旧产物以 mtime/hash 为准。
- B3：仍未关闭；t91 保持 `needs_revision`；t93 的 pass 只覆盖 t92 的 F1/F2 文档收窄，**没有把 t91 改写成通过**。
- 真实模型证据边界保持 23 条断言、11 条逐字可见、12 条未落盘；t93 的 `TOTAL=48 FAIL=0` 原文仍在。

## 六、独立 finding：发布文档 HEAD/CI 漂移

**T94-F1｜low｜文档陈旧，不是产品测试失败。**

`docs/release-readiness-2026-10-25.md` 仍保留历史 HEAD/CI 口径：

- §0 第 22 行、§6.1 B2 第 233 行、§7 第 251 行仍写 `bc4709e` / run `40278484458`；
- §2 第 57 行仍写 `10cff13` 为“当前 HEAD”且 CI“未宣布通过/等待中”；
- 本任务独立实测当前 HEAD 为 `81267784857e78b7732ef0e14ff05184bf69cf06`，CI `40290743348` 已 `completed/success` 且 headSha 完全一致。

判级理由：这是发布文档时效性漂移，不影响本轮代码与 fixture 的通过事实；但后续若复用发布说明，必须先更新这些标签，不能把旧 CI 证据贴到当前字节。按任务约束只记录，未修改该文档。

## 七、逐条验收证据

| 验收条件 | 证据 | 结论 |
| --- | --- | --- |
| HEAD、起始干净工作树、CI 一致且 success | §零；`git rev-parse`、porcelain、`gh run view` 均 exit 0 | PASS |
| 起始/结束 hash 与行数逐项一致 | §一；11/11，`BYTES_UNCHANGED` | PASS |
| 全部 fixture、evaluation、py_compile exit 0 | §二～四；95/95、6/6、7/7 | PASS |
| t91/t92/t93 的 F1/F2/B3、Q1、版本、旧时间戳口径一致 | §五；33/33；t91 未改写 | PASS |
| 当前报告、命令 exit code、TOTAL/PASS/FAIL、剩余风险 | 本报告 §二～八 | PASS |

合同 verify 命令汇总：`git diff --check` exit 0；隐私 fixture exit 0；95 文件循环 exit 0；evaluation exit 0。**TOTAL=4 contract commands, PASS=4, FAIL=0**。

## 八、独立性声明与已知边界

- 本任务由 verifier-zz 独立执行，未参与当前 HEAD 的产品实现及 t91/t92/t93 的修复；此前 t79 只读诊断未产生代码改动，不属于实现者自验。
- 按 `clear/ontology/verification-loop.md`，这是 L3 独立候选 verdict，不替代发布负责人的最终决定。
- 本轮范围是 Python fixture/evaluation 与指定文档探针；未重跑 frontend CJS 或 desktop 原生焦点测试。t88 环境级焦点风险、sc-15 超冲、sc-18/sc-19 与 Q1 未达标均保持原结论。
- 本报告的 pass 不关闭 B3/B4，不发布 v0.1.4-rc.1，不 bump、不打 tag。
- 回读对账：11/11 文件的 hash 与行数均为 `ALL_MATCH`；`git status --porcelain` 实测只新增本报告，`git diff --check` exit 0；除此之外没有其他仓库写入。
