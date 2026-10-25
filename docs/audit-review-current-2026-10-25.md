# 当前字节复核（captain 接管，独立性降级）

> **独立性声明：本报告由实现方 captain 接管撰写，不是第三方独立验收。** t89 原定由独立成员复核，但连续 9 次因上游 502/503、同账号并发上限失败；用户要求继续后，captain 按恢复手册接管。报告中的命令、hash 和 CI 结果是真实的；**“独立”二字不成立，不能把本报告当作第三方 verdict。**

## 1. 被测对象

| 字段 | 值 |
| --- | --- |
| 分支 | `codex/repository-layout` |
| HEAD | `d766492a2b8687a7bca4888e9cd908ff8fbf0a22` |
| 提交标题 | `test: add desktop focus diagnostics and truthful triage` |
| 工作树 | clean（复核前后 `git status --short` 均为空） |
| CI | run `40188967438`，status=completed，conclusion=success，headSha 与 HEAD 相同，34 步全绿 |
| 产品代码 | 自 `85b13fe` 到 `d766492` 未改；本提交只改 2 个 docs + 1 个测试文件 |

`d766492` 的完整 diff 统计：

```text
docs/audit-review-team-2026-10-25.md    |  43 ++++++++++++
docs/browser-focus-triage-2026-10-25.md | 113 ++++++++++++++++++++++++++++++++
tests/desktop_smoke.cjs                 |  28 +++++---
3 files changed, 175 insertions(+), 9 deletions(-)
```

关键文件 sha256：

```text
backend/server.py                 f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263
backend/moyu_consistency.py       b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2
backend/moyu_continuation.py      583059fe2fe989a9c3f3113fe064f3f1aef4d3bf876fc1412e3de3021b1a87a9
backend/moyu_extract.py           6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8
tests/desktop_smoke.cjs           c6d13a0ea450a8eb14ba2450f6de6d61ba0c706c70be06e9821c3ba4d81ffaf4
```

## 2. 逐项核查

| # | 核查项 | 证据 | 结论 |
| --- | --- | --- | --- |
| 1 | t82 的 F1–F6 是否关闭 | `docs/audit-review-team-2026-10-25.md:218-227` 的 §7.2 逐条列了 F1–F6 关闭落点；t84 修复、t85 复核、t86 修复、t87 复核均在库 | **当前 HEAD 已关闭**；但 `t82` 对 `d477912` 的 `needs_revision` 仍是当时真实状态，不能倒改 |
| 2 | t83 安全结论是否仍成立 | `docs/security-audit-current-2026-10-25.md` 65 行：无新 P0；S1 部署侧 P1 已修；S2/S3 接受为 P2；明确“本地单用户技术预览，不是生产可上线” | **成立，但范围有限**；未验证多用户、公网、依赖 CVE、外部网关 |
| 3 | Q1 3000 字口径 | `docs/consistency-budget-v1.2-2026-10-23.md:46-47`：3000 字 7/9=77.8%，4000 字 4/6=66.7%；`:103` 保留 `q1_target_met=false` | **未达成**；7/8=87.5% 只是剔除 sc-19 的诊断值，不替代 Q1 |
| 4 | sc-15 超冲与 sc-18/19 边界 | `consistency-budget:108-140`：sc-15 `characters=4661`/`target_reached`/`within_tolerance=False`；sc-18 46 字/no_progress；sc-19 2318 字/budget_exhausted | **口径已写清，缺陷未修**；sc-15 仍计入 4000 的 4/6，不能写成已修 |
| 5 | 23 条断言 vs 11 条可见证据 | t82 §3.3：5 章 `(assertions, 可见 audit)` 为 `(0,0)/(7,3)/(7,3)/(7,3)/(2,2)`，合计 23 / 11；`real-corpus-extract:228` 明确不把 23 写成已逐条审计 | **边界写死**；12 条无逐字证据，不能外推 |
| 6 | d766492 是否改变产品行为 | `git show --stat d766492` 只有 2 docs + `tests/desktop_smoke.cjs`；产品代码 hash 与 `85b13fe` 相同 | **未改产品代码**；只增加焦点诊断与 triage 记录 |
| 7 | 当前回归 | `.venv/bin/python tests/extract_fixtures.py` → `extract fixtures: OK {'cases': 67}`；`tests/server_bind_fixtures.py` → 4 tests OK；`tests/repository_privacy_fixtures.py` → ok (Git index only) | **通过**；CI 另有 94 Python + 26 CJS 全绿 |

## 3. 仍然不能宣称的部分

1. **独立 verdict 缺位。** 本报告是 captain 接管，不是第三方；t89 的独立复核目标没有达成。若要把 `Delivery` 从 blocked 解除，仍需一个真正独立的成员或外部账号。
2. **t79 不是“已修”。** 3000 字冻结样本 7/9=77.8%，sc-18 每轮 12–30 字、硬上限 12 轮，sc-19 外部预算中断；8/9 在现有样本/评分/provider 下数学不可达。要 8/9 只能改样本、评分或外部中断语义，均不允许。
3. **t88 不是“已修”。** 同一命令 7 次 6 过 1 挂，卡在 macOS 原生窗口焦点竞态；22 个 CJS 套件批量曾 22/22，但 `desktop_smoke` 单独仍非稳定 26/26。环境级失败，不能写成产品通过。
4. **真实模型只到“部分复跑”。** 5 章/33 120 字、23 条断言、5 条 empty_value、0 provider_error；2 章/12 463 字、17 条、14 条 rejected（6 empty / 8 quote_mismatch）；均为 `repeat=1`，不能证明确定性或召回率。
5. **历史 content-bound verdict 仍绑定旧字节。** 平台要求对当前 `server.py`/`moyu_consistency.py` 等重新绑定；本报告没有重跑全部历史任务，不能把旧 verdict 当当前通过。
6. **安全边界仍是本地单用户。** `MOYU_ALLOW_REMOTE=1` 只是显式放行，不是认证；公网/多租户不放行。

## 4. 结论

- **当前字节证据一致性：通过。** HEAD `d766492` 与 CI `40188967438` 对齐，工作树 clean；F1–F6 在当前 HEAD 已关闭；安全报告、Q1 口径、11/23 边界均与文档一致。
- **独立验收：未通过（不是失败，是没有独立执行者）。** 本报告已声明独立性降级，不能作为第三方 verdict。
- **交付状态：仍 blocked。** 阻塞项是 t79 的数学不可达、t88 的环境级焦点竞态、t89 的独立复核缺位，以及历史 content-bound verdict 未重绑；不是“代码全崩”。
- **不记录评分、不泄露/提交任何 API key。**

## 5. 复现命令

```bash
git rev-parse HEAD
git status --short
git show --stat --oneline d766492
git diff --stat 85b13fe d766492
gh run view 40188967438 --repo z19910928-afk/ai-creation \
  --json status,conclusion,headSha,displayTitle
.venv/bin/python tests/extract_fixtures.py
.venv/bin/python tests/server_bind_fixtures.py
.venv/bin/python tests/repository_privacy_fixtures.py
```
