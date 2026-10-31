# v0.1.4-rc.1 发布就绪说明（草稿，2026-10-25）

> **文档性质：发布草稿，不是发布公告，也不是验收 verdict。**
> 本文只汇总「当前字节能证明什么」；不创建 tag、不改产品代码。真实模型仅在本机外部 CLI 复跑（见 §5.3），密钥不入仓、不复述。
> 撰写者：repair-flash（t90）。所有数字都指向仓库内可复算的文件或章节，未使用记忆值。

> **HEAD 口径（2026-10-26 sc-15 修订，必读）**：本文「代码/测试基线」指 `68578c9`（`fix: classify continuation overshoot explicitly`），不是 `git rev-parse HEAD`。本次发布说明若随 docs-only 提交更新，HEAD 会前进，但不改变该代码/测试基线；对应 GitHub Actions 以实际推送提交为准。

---

## 0. 一句话结论

**当前状态：本地单用户技术预览可交付；生产上线不通过；多租户不支持。**

判定为「技术预览可交付」的依据是**当前字节的可验证性**（CI 对齐、验收窗口内工作树 clean、关键文件 hash 与复核报告一致），
不是功能完备性。两项硬阻塞仍然开着（B4/Q1 未达标、t79 数学不可达）；
B3（独立复核缺位／未通过）**已由 t98 替代独立复核关闭**，但 t91 报告的 `verdict=needs_revision` 原样保留，见 §5.1、§5.6。

> **工作树口径（sc-15 修订）**：t94 / t97 的 `clean` 只对各自验收窗口成立；sc-15 的代码、测试、预算产物与文档已随代码基线 `68578c9` 提交并推送。若本说明随后发生 docs-only 修订，应单独核对，不把历史验收窗口的 clean 当成当前工作树状态。CI success 也不等于全量发布放行；B4/Q1 等硬阻塞仍按 §3、§5 执行。

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| 交付范围 | **本地单用户技术预览** | §1 |
| 生产可上线 | **否** | §1、§5 |
| 多租户 / 公网 | **不支持** | §1 |
| CI（代码/测试基线 `68578c9`） | run `40319600547`：`completed` / `success`，headSha 与提交一致 | §2 |
| CI（历史基线 `d766492`） | run `40188967438`，34 步全绿（**历史**字节，非当前） | §2 |
| Q1 3000 字 | **7/9 = 77.8%，未达标** | §3 |
| Q1 4000 字 | 4/6 = 66.7% | §3 |
| `q1_target_met` | **`false`** | §3 |
| 独立复核 | **B3（独立复核缺位／未通过）已由 t98 替代独立复核关闭**（`B3_CLOSED: true`，只关闭 B3）；t91 原 `verdict=needs_revision` 原样保留、不改写成通过；t89 仍为 captain 接管、独立性降级 | §5.1、§5.6 |
| 版本号 | **仍为 `0.1.3`；2026-10-26 决定不 bump、不打 tag（B4 未解；B3 虽已于 t98 关闭，Q1 仍未达标）** | §6.1 |

---

## 1. 可交付范围（必须先读）

### 1.1 是「本地单用户技术预览」

- 服务默认只绑定回环地址；`backend/server.py` 的 `resolve_host()` 默认只允许 `127.0.0.1` / `localhost` / `::1`。
- 设置 `MOYU_HOST=0.0.0.0` 等非回环地址时，**必须同时显式设置 `MOYU_ALLOW_REMOTE=1`**，否则启动即拒绝。
  这是一个**显式放行开关，不是认证机制**（见 `docs/security-audit-current-2026-10-25.md`）。
- 无账号体系、无权限模型、无租户隔离、无审计日志留存。

### 1.2 明确**不是**什么

- **不是生产可上线**：没有认证/授权，没有多用户并发保障，没有 SLO/限流/熔断，没有备份与迁移方案。
- **不是多租户**：单进程单用户假设；数据目录是本地单份，没有租户维度。
- **不是公网服务**：`MOYU_ALLOW_REMOTE=1` 只是「允许非回环绑定」，把它当公网安全边界是错误的。
- **不是基准达标版本**：Q1 未达成，见 §3。

> 安全报告的范围限定与本文件一致：未验证多用户、公网暴露、依赖 CVE 全景、外部网关
> （`docs/security-audit-current-2026-10-25.md`，65 行，明确「本地单用户技术预览，不是生产可上线」）。

---

## 2. CI 状态（当前字节与历史字节分开表述，不得合并）

| 字节 | CI run | 结果 | 定位 |
| --- | --- | --- | --- |
| **`68578c9`**（**sc-15 代码/测试基线**，`fix: classify continuation overshoot explicitly`） | **`40319600547`** | `status=completed`、`conclusion=success`、headSha 与 commit 一致 | **当前代码/测试基线**；CI 终态已用 `gh run view` 实测 |
| `605b895`（历史 docs-only 提交） | `40303229735` | `status=completed`、`conclusion=success`、headSha 与 commit 一致 | 历史文档证据；代码字节与 `8126778` 相同 |
| `8126778`（历史代码基线） | `40290743348` | `status=completed`、`conclusion=success`、headSha 与 commit 一致 | 历史代码基线；t95 实测 |
| `5d22d8a`（历史） | `40281317350` | `conclusion=success`，headSha 与 commit 一致 | 历史（t91 独立复核的字节） |
| `bc4709e`（历史） | `40278484458` | `conclusion=success` | 历史 |
| `10cff13`（历史） | `40193598447` | `conclusion=success` | 历史（此前曾写作「等待中」，现已有结果，按历史记录保留） |
| `d766492`（历史，审计报告基线字节） | `40188967438` | `conclusion=success`、**34 步全绿** | 历史（来自 `docs/audit-review-current-2026-10-25.md` §1） |

> 当前代码/测试基线固定为 `68578c9` / `40319600547`；本次文档修订与 `docs/CHANGELOG.md` 同批提交，不内嵌尚未产生的文档提交号或 CI 号。`605b895` / `40303229735`、`8126778` / `40290743348` 均为历史记录，**不得当作当前状态引用**。

以下是 `d766492..8126778` 的**历史差异证据**（`git diff --stat d766492..8126778`，t95 实测），不是当前 `68578c9` 的完整差异：

```text
 docs/CHANGELOG.md                                 |   4 +
 docs/audit-review-current-2026-10-25.md           |  75 ++++++
 docs/audit-review-independent-2026-10-26.md       | 175 ++++++++++++++
 docs/release-readiness-2026-10-25.md              | 267 ++++++++++++++++++++++
 docs/verification-release-readiness-2026-10-26.md | 134 +++++++++++
 5 files changed, 655 insertions(+)
```

这段历史区间只新增/改动 docs 文件；`git diff --stat d766492..8126778 -- backend/ tests/ frontend/ benchmarks/` 当时输出为空（t95 实测）。
因此它只能证明 `d766492` 与历史基线 `8126778` 的产品/测试字节相同，**不能覆盖后续 t101 或 sc-15 改动**。
t101 对 `tests/desktop_smoke.cjs` 的测试辅助修改已随 `1bb9151` 提交；sc-15 的引擎、测试、基准产物与文档已随 `68578c9` 提交。
当前字节以 §2.1 的实测 hash 为准；历史 verdict 不因这段差异证据而平移。
t94 当轮**未关闭** B3/B4；B3 其后仅由 t98 关闭（见 §5.6），B4 仍未关闭。

### 2.1 关键文件 sha256 与行数（**代码基线 `68578c9`** 实测；历史 t95 复核记录另见 §8）

```text
backend/server.py                 f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263   8198 行
backend/moyu_consistency.py       b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2   1222 行
backend/moyu_continuation.py      4486b78398b6c826946083df08c8b888575ae8c3bfb1ca3a1219b4aaca71a874    899 行
backend/moyu_extract.py           6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8   1041 行
tests/desktop_smoke.cjs           b409533e73bddf8e66195ac73dc3e942be5b99db6657b5bbb5a31999a356e22d    810 行（t101 后）
```

以上 5 个 sha256 在**当前磁盘**实测，行数用 `wc -l` 实测；其中 `backend/moyu_continuation.py` 已包含 sc-15 修复，不能沿用旧 `8126778` 的 896 行/hash。
其余 `backend/*.py` 与历史审计报告的对照只用于追踪未变文件，**不把历史 verdict 平移到当前字节**。

`tests/desktop_smoke.cjs` 是这项对照的**例外**，必须单独说明：
该文件在代码基线 `8126778` 之后被 **t101** 修改（单文件 `+32/−6`，只动测试辅助的原生激活路径与诊断输出，
**产品代码零改动**），所以它当前的字节是 `b409533e…` / **810 行（t101 后实测）**；
而 `c6d13a0e…` / 784 行是 **t101 之前**的字节，只以**历史**口径出现在 t88/t91/t94 等报告中，
**不得再当作当前字节**。该文件当前字节的独立验收见 §5.2 与
`docs/verification-desktop-smoke-focus-2026-10-26.md`（90 行）。

---

## 3. Q1 基准结论（未达成，不许美化）

数据源：`benchmarks/budgeted/results/budgeted-v1.2-baseline.json`（20 场景、`repeat=2`）。
判定口径：`first_pass`（一次通过），按 `target_characters` 分档。

| 档位 | 分子/分母 | 比率 | 通过的场景 |
| --- | --- | --- | --- |
| **3000 字（Q1 口径）** | **7/9** | **77.8%** | `sc-02/03/07/08/12/14/17` |
| 4000 字 | 4/6 | 66.7% | `sc-04/09/10/13` |

```text
q1_target_met = false
q1_target_rate = 0.8
```

**Q1 门槛是 9 个场景里至少 8 个通过（≥80%），实际 7 个，差 1 个。**

### 3.1 严禁把 7/8 当 Q1

- 剔除 `sc-19`（纯外部预算中断）后为 **7/8 = 87.5%**，这是一个**明确标注的诊断值**，
  **不替代 Q1、不改变 `q1_target_met`**。
- 任何对外材料若写「Q1 = 87.5%」或「Q1 达成」，都是把诊断值冒充指标，属禁止行为。
- 4000 字子集是**独立**的 4/6，不能套用 77.8% 或 87.5%。

依据：`docs/consistency-budget-v1.2-2026-10-23.md` §6.1（Q1 口径与诊断值分离）。

### 3.2 capable-provider arm（2026-10-30；新增能力口径，不替代 Q1）

旧 Q1 的 provider 故意忽略所有字数提示；它适合当鲁棒性下界，但不能回答“引擎在正常 provider 下能不能做到”。
新增的 capable 臂固定声明：provider 服从 `max_characters` / `min_characters`，调用方把首轮额度
`round_budget(0, target)` 合入开场提示；外部中断 `budget_limited` / `canceled` 如实记录并从 `q1_engine` 分母排除。

| 口径 | 通过 / 总数 | 比率 | 说明 |
| --- | ---: | ---: | --- |
| **Q1 engine**（3000，排除外部中断） | **8/8** | **100.0%** | 达到 90% 门槛；不含外部预算/取消 |
| Q1 capable all（3000，含外部中断） | 8/9 | 88.9% | **未达到 90%**，不得写成“全部通过” |
| 全部目标（含外部中断） | 18/20 | 90.0% | 参考值 |
| legacy adversarial 3000 | 7/9 | 77.8% | `q1_target_met=false`，保持不变 |

在 capable 契约下，`sc-18` 为 **2700 字 / 12 轮 / `target_reached`（PASS）**，`sc-15` 为
**4400 字 / 1 轮 / `target_reached`（PASS）**；`sc-19`/`sc-20` 仍因外部中断失败，不计入 engine。
这只是不同契约下的能力证据：**旧 Q1 7/9=77.8% 仍未达成，B4 不关闭，版本/发布结论不变。**
报告与可复算产物：`docs/verification-q1-capable-2026-10-30.md`、
`benchmarks/budgeted/capable.py`、`benchmarks/budgeted/results/budgeted-capable.json`。

旧 adversarial 的数学边界（`12×30=360<2700`、`sc-19` 两轮上限 2471、`sc-15` 4661）仍见 §4；
capable 臂不是改样本、改评分或绕过外部中断，也不能拿它的 100% 覆盖旧口径。

---

## 4. sc-18 未达与 sc-15 超冲语义边界

### 4.1 sc-18 停滞：数学不可达，不再重跑（t79）

| 事实 | 值 | 来源 |
| --- | --- | --- |
| 生成侧每轮产出 | **12–30 字** | 场景定义 / `consistency-budget-v1.2` §5、§附录 |
| `hard_max_rounds`（计入基线） | **12** | `budgeted-v1.2-baseline.json` 的 `config` 与 20 场景一致为 `12` |
| 12 轮 × 30 字上限 | **360 字** | 算术 |
| 3000 字下限（`target_range.minimum`） | **2700 字** | 基线 JSON（±10%） |
| 实际结果 | `rounds=2`、`characters=46`、`stop_reason=no_progress` | 基线 JSON |
| sc-19 | `rounds=2`、`characters=2318`、`stop_reason=budget_exhausted`（外部预算中断） | 基线 JSON |

**结论：`12 × 30 = 360 < 2700`，轮数再多也到不了下限 → 3000 字档的 8/9 在现有样本 / 评分 / provider 下数学不可达。**
要凑到 8/9，只能改样本、改评分或改外部中断语义——三者都不允许，因此**不再重跑**。

> 口径订正（与任务描述有出入，按证据写）：任务书写「硬上限 12 轮」，基线 JSON 实测也是 **12**；
> 但 `docs/consistency-budget-v1.2-2026-10-23.md:81` 出现过「`hard_max_rounds` 上限 24，24 × 30 = 720 < 2700」。
> 24 是附录 A 的**非计入敏感性变体**，不是计入基线的配置。两种轮数下 `24 × 30 = 720 < 2700` 同样不达标，
> 结论不受影响；引用时请以**基线 12 轮**为准，不要把 24 当成当前配置。

> capable 补充（2026-10-30）：在 provider 服从 `max_characters` / `min_characters` 且首轮额度合入提示的
> §3.2 契约下，`sc-18` 可在 **12 轮 / 2700 字 / `target_reached`** 通过；旧 t79 的对抗式失败仍然保留，
> 不重开、不改写。该通过用满 12 轮硬上限，属于边界通过。

### 4.2 sc-15 超冲：语义已修，场景仍失败

| 字段 | 值 |
| --- | --- |
| `target_characters` / `target_range` | 4000 / 3600–4400 |
| `characters` | **4661**（超上限 **261**，超目标 16.5%） |
| `rounds` / `stop_reason` | 1 / **`overshoot`**（本轮起；旧报告为 `target_reached`） |
| `target_reached` / `within_tolerance` | `true` / **`false`** |
| `overshoot` | **261 字**（`characters - upper`） |

引擎现在在 `total_characters >= lower` 后先比较上限：超过 `upper` 返回 `OVERSHOOT`，否则 `TARGET_REACHED`
（seed 侧 `backend/moyu_continuation.py:737-739`、循环侧 `:797-799`；常量 `:67`、`STOP_REASONS` `:69`）。
正文仍不裁剪，`within_tolerance` 仍为 `false`；`sc-15` 仍计入 4000 字子集失败（4/6）。
当前 `target_reached` 仍只判下限（`:875`），`within_tolerance` 才判带宽（`:876`）；因此 `target_reached=true` 不等于带内达标，旧报告的 `target_reached` 停止原因仅作历史记录。

依据：`docs/consistency-budget-v1.2-2026-10-23.md` §6.1.1（历史口径）；当前字节以 `benchmarks/budgeted/results/budgeted-baseline.json` 为准。

> capable 补充（2026-10-30）：在 §3.2 契约下 `sc-15` 为 **4400 / 4000 / 1 轮 / `target_reached`**，
> 恰好落在上限内，计为 PASS；旧 adversarial 的 **4661 超冲 261** 仍保留为失败，不改写旧报告。

---

## 5. 仍然不能宣称的部分（发布前必读）

### 5.1 独立复核：t91 已完成但 `needs_revision`，t89 仍独立性降级

- `docs/audit-review-current-2026-10-25.md` 已落盘，但它是 **captain 接管撰写**（t89）。
- 原定独立成员复核**连续 9 次因上游 502/503、同账号并发上限失败**，用户要求继续后由 captain 按恢复手册接管。
- **「独立」二字对 t89 不成立：该报告不是第三方 verdict。** 报告中的命令、hash、CI 结果真实，但结论主体是执行方。
- **t91 已由独立成员完成一次真复核**：`docs/audit-review-independent-2026-10-26.md`，复核人 `verifier-flash`
  （未参与本轮任何实现/修复），对其当时的 HEAD `5d22d8ad` 的字节绑定、CI、Q1 双分母、t79 数学不可达、11/23 证据边界
  五项独立复现且与本文件一致。**但 verdict = `needs_revision`**：desktop 的「7 次 6 过 1 挂」写法被其独立复跑
  （1 跑 1 挂，`activation_attempts=55`）否定，要求按历史观测口径重写。
- 因此**独立复核不再「完全缺位」，但也绝不等于通过**。该报告按约定**保留原样、不改写、不重开、不洗白**；
  本轮只按它的 F1/F2 修订本文件对应措辞，报告的 verdict 与 findings 原文不动。
- 状态解除条件：需**替代独立验收**（独立成员或独立账号/路由）对本文件修订后的字节给出新 verdict。
  该条件已依次由 **t93**（F1/F2 文档收窄的限定 `pass`）、**t94**（当轮字节 content-bound `pass`）与
  **t98**（B3 closure 复核，`B3_CLOSED: true`）满足，**B3 已于 t98 关闭**，见 §5.6；这些 verdict 均不覆盖 sc-15 后的当前文档字节。
  但 **B4/Q1 仍未达标**，因此**仍不 bump 版本、不发 rc**。

### 5.2 desktop 焦点竞态：t101 修测试辅助激活路径（t88 历史失败保留）

| 事实 | 值 |
| --- | --- |
| 某次会话的连续实跑（历史观测） | **7 次：6 次 10/10 通过、1 次卡住** —— 仅该会话样本，**不是稳定通过率** |
| 另一次独立会话复跑（t91） | **1 跑 1 挂**：`activation_attempts=55`、`window_focused=false`、`application_active=false` |
| 卡住位置 | `activate-native-desktop-window`（`tests/desktop_smoke.cjs:504`）→ `TIMEOUT_NATIVE_WINDOW_FOCUS` |
| 决定性字段 | `activation_attempts` 55–59（10 秒内请求激活数十次全部被 OS 拒绝） |
| 失败发生在 | 任何产品断言**之前**（剪贴板链路一次都没执行） |
| 定性 | **macOS 原生窗口焦点竞态，装置/会话级，不是产品缺陷** |
| 当前状态（**t101 后**） | **测试辅助激活路径已加强**：`activation_attempts` 由 55–59 收敛到 **1–5 次内拿到焦点**；t101 本机 4/4、t102 独立复跑 2/2，均为 **10/10** |
| 仍未证明 | **跨机器 / CI / 无 WindowServer 会话**下的稳定性 —— 因此**仍不宣称 26/26** |

- **t101 只改测试辅助代码**：新增 `/usr/bin/osascript` 按 PID 置前、`setActivationPolicy('regular')`、
  `moveTop()`、短暂 `setAlwaysOnTop(true,'floating')` 后恢复；`window.isFocused()===true` 断言与 10 秒
  `NATIVE_WINDOW_FOCUS` 轮询**原样保留**，无 `skip`、无平台豁免，用例数仍 10；产品代码零改动（单文件 `+32/−6`）。
- **t102 独立验收**（`docs/verification-desktop-smoke-focus-2026-10-26.md`，90 行 / sha256
  `1405a3a7c474ebf952a657b11681b7ca1cf2531de774f61343918c1162556659`）实跑 2 次：
  run1 `EXIT=0`、10/10、`window_focused=true`、`application_active=true`、`activation_attempts=5`；
  run2 `EXIT=0`、10/10、`activation_attempts=1`；两次 `native_activation.ok=true`。
  被测字节跑前跑后三次一致：`b409533e…` / 810 行。对照旧字节 `c6d13a0e…` / 784 行（t91 复跑 1/1 挂、`attempts=55`），
  同机同 GUI 会话下新字节收敛到 1–5 次内拿到焦点。
- **「7 次 6 过 1 挂」只是某次会话的个别观测，不是该用例的稳定属性**，同样**不能**读成 6/7 的稳定通过率、
  也不能读成 14% 的偶发失败率。
- **边界（不得越过）**：t101/t102 的通过都在**同一台机器、同一 GUI 会话、Automation 权限已授予**
  （`osascript` 返回 `ok:true`）的前提下取得；换机器、进 CI、或在没有 WindowServer 的会话里跑，
  `osascript` 会返回错误并由 `native_activation` 记入诊断，用例会**如实失败**而不会静默降级。
  因此本轮**仍不宣称 26/26、也不写「桌面端全部通过」**。`activation_policy=null` 是该 Electron 版本无此 API 的如实记录。
- 22 个 CJS 套件批量曾 22/22，那是**套件级**结果，不等于 `desktop_smoke` 单独稳定。

依据：`docs/browser-focus-triage-2026-10-25.md`（t88/t91 时的历史定性）、
`docs/verification-desktop-smoke-focus-2026-10-26.md`（t102 独立验收，90 行）。

### 5.3 真实模型复跑：链路已通，确定性未过

- **2026-10-27 本机复跑（当前证据）**：`zzniu` / `deepseek-v4.1-flash`，`--mode openai --limit 5 --repeat 2`，
  5 章 / 33 120 字 / 43 条断言 / 4 条 rejected（1 `empty_value`、3 `quote_mismatch`）/ **0 `provider_error`**；
  默认 `--audit-limit=3`，43 条中 10 条逐字证据可见。`determinism.identical=false`、digest 前缀 `9c09f616…`；
  产物 291 行 / `ddc05a21…`，引擎 1041 行 / `6b5d4313…`。完整记录：
  `docs/verification-real-model-2026-10-27.md`。
- **应用连接 probe**：`moyu_settings.probe_settings()` 返回 `success=true`、`returned_model=deepseek-v4.1-flash`、
  `finish_reason=stop`、`configuration_stale=false`；key 只存在 `~/.moyu/credentials`（0700），不打印、不提交。
- **2026-10-25 旧轮（历史）**：5 章 / 33 120 字、23 条断言、5 条 `empty_value`、0 `provider_error`；
  2 章 / 12 463 字、17 条、14 条 rejected（6 empty / 8 quote_mismatch）；均为 `repeat=1`，
  5 章 JSON 只有 11 条逐字证据可见。
- **边界**：真实请求链路已证明，但两轮的分母、参数和模型输出不同，**拒绝率不可合并**；当前轮
  `identical=false`，不能证明确定性，也不能外推召回率或语义质量。真实模型仍**不是**发布放行条件。
- **产物时间戳口径（必读）**：`/private/tmp` 下旧产物（`moyu-real-current-5.json`、`-180.json` 等）的
  `generated_at` 仍是旧脚本硬编码 `2026-10-24T00:00:00+08:00`，不能当作生成时刻；引用时以 mtime 与
  sha256 为准。当前 `benchmarks/real_corpus/extract.py:254` 已改用 `datetime.now()`，新产物不再有此问题。
  该订正属已披露问题（`docs/audit-response-2026-10-25.md:19` A8、`docs/audit-review-team-2026-10-25.md:45`），
  不改变旧轮 11/23 的证据边界。

### 5.3.1 真实模型 3000 字续写探针（2026-10-28）

- **范围**：本机一次性生产 HTTP 探针，不是 Q1 fake-provider 基准，不是发布放行，也不证明语义/文风/事实质量。
  完整记录：`docs/verification-real-continuation-2026-10-28.md`。
- **方法**：复制应用数据库到临时库并重建临时凭据引用，启动生产服务，`GET /api/ai/status` 为 `mode=provider`；
  目标 `3000`、`max_rounds=6`、`min_round_gain=50`、`max_stall_retries=1`、`hard_max_rounds=12`，目标区间 `[2700,3300]`。
- **结果**（三个臂各 1 次，job 均 `success`、`model=deepseek-v4.1-flash`、`completion_state=complete`）：

| 臂 | `round_target_ratio` | 字数 | `stop_reason` | `within_tolerance` | 轮数 |
| --- | --- | ---: | --- | --- | ---: |
| 默认 | `null` | 3635 | `overshoot` | `false` | 3 |
| 旧对照 | `1.35` | 3571 | `overshoot` | `false` | 2 |
| 收紧 | `1.0` | 3176 | `target_reached` | `true` | 2 |

- **重复 3 次（同一 `1.0` 口径，2026-10-28 21:19–21:21）**：2856 / 3134 / 3714 字，
  `target_reached` / `target_reached` / `overshoot`，`within_tolerance` = `true` / `true` / `false`；
  **2/3 = 66.7% 带内**。三个 job 均 `success`、`model=deepseek-v4.1-flash`、`semantic_verified=false`。
- **`0.9` 不可配置**：HTTP 队列三次均返回 `400 每轮额度系数必须在 1 到 10 之间。`，没有 job、没有字数结果；
  `1.0` 是当前下限，不能把 `0.9` 写成待验证臂。
- **`2.0` 三次尝试**：2721 / 907（job `failed`，提供方中途报错）/ 2778 字；两次成功 job 均
  `target_reached`、`within_tolerance=true`，另一次 `status=failed`、`usage=null`、只生成 907 字。
  按全部尝试为 **2/3 成功且带内**，按成功 job 为 2/2 带内；不能只报后者。
- **结论**：真实模型在本链路能完成 3000 字；`1.0` 是 2/3 带内 + 1 次 `overshoot`，`2.0` 是 2/3 成功带内 +
  1 次提供方中途错误，**没有 3/3 的稳定口径**；默认值不改，不能写成稳定通过。
  `semantic_verified=false`，不能把机械协议通过写成语义质量通过。
- **边界**：`round_target_ratio` 默认仍为 `None`，既有行为与预算契约不变（本轮未重跑预算套件）；本探针不改变 Q1
  `7/9=77.8%<80%`、`q1_target_met=false`、t79 数学不可达与 B4 未关闭。探针脚本直接读本机应用库，
  未入库、不是 CI 基准；job id 与结果保存在本机临时库，未提交。
- **产品契约（2026-10-30，作者接受）**：`±10%` 作为**软目标**——`[2700,3300]` 外允许保留全文，不裁剪；
  超冲/未达必须在界面明确提示，作者自行决定采纳。契约与字段见
  `docs/product-contract-soft-target-2026-10-30.md`；这不改变 Q1 7/9、t79 或预算台架判定。
- **复算工具（2026-10-30）**：`benchmarks/real-model/compare_round_target.py` 已允许服务端使用应用内模型
  配置（无需调用方持有 `MOYU_AI_*`），命令与输出口径见 `benchmarks/real-model/README.md`；汇总同时统计
  `target_reached` 与 `overshoot` 两种未带内停止原因。本轮没有新增真实模型运行。

### 5.4 2026-10-25 旧轮：23 条断言只有 11 条逐字可见

> 本节只指 2026-10-25 旧轮；2026-10-27 轮为 43 条中 10 条可见，见 §5.3 与
> `docs/verification-real-model-2026-10-27.md`。

| 章 | 断言条数 | 可见 audit 条数 |
| --- | --- | --- |
| 1 | 0 | 0 |
| 2 | 7 | 3 |
| 3 | 7 | 3 |
| 4 | 7 | 3 |
| 5 | 2 | 2 |
| **合计** | **23** | **11** |

- 每章 `audit_limit=3` 截断，剩余 **12 条无逐字证据**。
- **禁止表述为「23 条断言已逐条审计」**；正确表述：「23 条断言中 11 条有逐字可见证据，12 条未落盘，
  审计边界见 `docs/real-corpus-extract-2026-10-24.md` §6.5 与 §8」。

### 5.5 content-bound verdict 的绑定范围（t93 / t94）

- **2026-10-30 当前字节 manifest**：新增 `docs/current-bytes-manifest-2026-10-30.json`（22 个关键文件）
  与 `scripts/verify_manifest.py`；`tests/current_bytes_fixtures.py` 在 CI 中重算 sha256/行数，任何漂移硬失败。
  规则是：历史 verdict 只有在它绑定的文件 sha256 与本 manifest **完全相同**时才可按 content-bound 复用；
  否则一律视为历史证据，必须重跑，不得写成“当前通过”。该 manifest 覆盖
  `backend/moyu_continuation.py`、`backend/server.py`、`backend/moyu_consistency.py`、`backend/moyu_extract.py`、预算基准/产物、Q1 新臂、前端软目标代码、产品契约，以及被旧 verdict 点名的 `tests/*_fixtures.py` 与两份历史证据文档；
  不覆盖发布说明等会持续修订的文档本身。
- 逐族可复用性台账见 `docs/current-bytes-verdict-ledger-2026-10-31.md`：本轮检查的旧 verdict 均至少有一个绑定文件漂移，
  因此**没有任何旧 pass 被复用**；旧任务状态（`t79` failed、`t88` failed、`t91`/`t96` 未完成）不变，台账不是发布放行。
- 此前若干 verdict 绑定的是**旧字节**；对当前 `backend/server.py`、`backend/moyu_consistency.py` 等
  需要重新绑定。本文件**不**把旧 verdict 当作当前通过（依据 t89 §3.5）。
- **t93**（`docs/verification-release-readiness-2026-10-26.md`）的 `pass` **只覆盖 t92 的 F1/F2 文档收窄**，
  即「desktop 历史观测口径」与「旧产物 `generated_at` 口径」两处措辞；
  **它不把 t91 的 `needs_revision` 改写成通过；它当时也不关闭 B3/B4（B3 后由 t98 关闭，见 §5.6）。**
- **t94**（`docs/verification-current-release-2026-10-26.md`，124 行）对当轮本文件字节
  `81267784857e78b7732ef0e14ff05184bf69cf06` 做了独立 content-bound 验收：`verdict=pass`，
  95/95 fixture、6/6 evaluation、7/7 py_compile、33/33 文档探针、11/11 前后字节对账通过。
  **该 pass 只绑定 t94 当轮字节，不代表 Q1 达标、不代表发布放行；它当时也不关闭 B3/B4（B3 后由 t98 关闭，见 §5.6）。**
  - 报告实测（t95）：`wc -l` = 124；sha256 = `4426769a9fd0413f07953d55cdc6cec3df90cb68758da4cbf9965cf6d418c31b`。
    （该文件此前未被 git 跟踪，若后续被改写，以该报告的提交版本为准。）
- 结论（t99 更新）：t93/t94 的 `pass` 本身不关闭 B3/B4；**B3 其后由 t98 关闭**（见 §5.6），
  **B4（Q1 未达标）仍未关闭**。

### 5.6 B3 关闭依据（t98 替代独立复核）

- 报告：`docs/audit-review-b3-closure-2026-10-26.md`，复核人 `verifier-zz`
  （未参与 t91/t92/t93/t94/t95/t97 的实现或原验收裁决）。
  实测（t99）：**96 行**，sha256 `10879f52faa30fd48da63f93c2b31d01e1fa188d66e83b5582eda09b38c33a65`。
- t98 裁决：**`B3_closable: yes`、`B3_CLOSED: true`**；关闭对象**仅为**「独立复核缺位／未通过」。
- 关闭链（四段，缺一不可）：
  1. **t91** 已独立完成字节绑定、CI、Q1 双分母、t79 数学不可达、11/23 边界五项复现；
     其 `needs_revision` 的 findings **只有 F1/F2**（`docs/audit-review-independent-2026-10-26.md:141`、`:145-146`）。
  2. **t92** 按 F1/F2 收窄本文件措辞；**t93** 对这两项给出独立、限定范围的 `pass`（只覆盖文档收窄）。
  3. **t94** 对当轮代码基线 `8126778` 完成独立 content-bound 验收 `pass`；**t95** 当时更新 HEAD/CI 并保留全部边界，均属历史记录。
  4. **t98** 复核上述证据链足以闭合「独立复核缺位／未通过」，给出 `B3_CLOSED: true`。
- **边界（不得越过）**：
  - t91 报告的 `verdict = needs_revision` **原样保留**；本文件不改写它，也**不得称「t91 通过」**。
  - t98 的 `B3_CLOSED` **只用于关闭 B3，不是发布放行**；B4/Q1、t79 数学不可达、t88 焦点竞态（当前状态见 §5.2）、
    真实模型 11/23 证据边界（2026-10-25 旧轮；2026-10-27 轮见 §5.3）**全部不变**。
  - t98 §5.3 把 **T97-F1**（本文件原「工作树 clean」措辞）列为**未闭合 low**；本文件已按 §0 的「工作树口径」修正，
    但不把该修正记作 B3 的关闭理由。
  - t98 报告内引用本文件的行号，绑定的是 t98 复核时的 **296 行版本**（sha256 `52c7c078…`）；
    本次 t99 修订后行号会位移，跨文档引用请以**章节号**为准。
- 已关闭 B3 ≠ 已验收发布：**本轮仍不 bump、不打 tag、不发布**（B4 未达标）。

---

## 6. 发布前待办（按阻塞级别）

### 6.1 阻断项（不解决不应发 rc）

| # | 项 | 现状 | 归属 |
| --- | --- | --- | --- |
| B1 | 版本号决定：本轮不 bump | `package.json` 保持 **`0.1.3`**；不创建 `v0.1.4-rc.1` tag；**B4 未解**（B3 已于 t98 关闭），避免制造未验证 RC | release owner（2026-10-26 决定） |
| B2 | 代码基线 CI | **`68578c9`** run **`40319600547`**：`completed`/`success`，headSha 与 commit 一致；本次 docs-only 提交与 CI 单独核对，不预写未产生的结果 | captain 核对（2026-10-26） |
| B3 | 独立复核缺位／未通过 → **已由 t98 关闭** | `B3_CLOSED: true`（**只关闭 B3**）：t91 五项独立复现已存在、其 F1/F2 经 t92 修复并由 t93 限定 `pass`、t94 对当轮字节 `pass`、t98 复核闭合（报告 96 行 / `10879f52…`）。**t91 原 `verdict=needs_revision` 原样保留，不改写、不称通过** | `verifier-zz`（t98，2026-10-26） |
| B4 | Q1 未达标 | 7/9 = 77.8%，`q1_target_met=false` | 产品/基准 owner |

### 6.2 已知但不阻断（必须如实写进发布说明）

- **t79 数学不可达**：`sc-18` 12–30 字/轮 × 12 轮 = 360 < 2700；`sc-19` 外部预算中断。不再重跑。
- **desktop 焦点竞态**：**历史失败如实保留** —— t88 某次会话 7 跑 6 过（个别观测）、t91 另一次独立复跑 **1 跑 1 挂**
  （`activation_attempts=55`、`window_focused=false`、`application_active=false`）。
  **t101 已加强测试辅助激活路径**（`osascript` 按 PID 置前 + `moveTop`/`setActivationPolicy`，断言不放宽），
  **t102 独立实跑 2/2、10/10**，`activation_attempts` 由 55–59 收敛到 1–5、`native_activation.ok=true`。
  t101/t102 都只动测试辅助代码，**不是产品缺陷**；边界是同机 GUI + Automation 已授权，
  跨机器 / CI / 无 WindowServer 未证明，**仍不宣称 26/26**。
- **sc-15 超冲**：`4661/4000`、`stop_reason=overshoot` 与 `within_tolerance=false`；显式失败已修，正文不裁剪，仍计入 4000 子集失败（4/6）。
- **真实模型证据**：2026-10-27 轮 `repeat=2`、5 章 / 33 120 字、43 条断言、4 条 rejected、0 `provider_error`、`identical=false`；2026-10-25 旧轮 `repeat=1`、23 条中 11 条可见、分母 28/31 不可合并。两轮不可合并，均不能当质量通过。
- **真实模型 3000 字续写探针（2026-10-28）**：默认 `None` 3635/`overshoot`、`1.35` 3571/`overshoot`、`1.0` 3176/`target_reached`（`[2700,3300]` 带内）；同一 `1.0` 重复 3 次为 2856/3134/3714，**2/3 带内、1 次 `overshoot`**；`0.9` 被 HTTP 400 拒绝（下限 `1.0`），`2.0` 三次为 2721/907(failed)/2778，**2/3 成功带内 + 1 次提供方中途错误**。成功 job 均 `success`、`semantic_verified=false`。这是小样本探针，不是 Q1 的 7/9，不关闭 B4（见 §5.3.1）。
- **Q1 capable-provider arm（2026-10-30）**：engine 3000 字 **8/8=100.0%**（外部中断从分母排除），
  capable all **8/9=88.9%**（未到 90%）；全部目标 18/20=90.0%；`sc-15` 4400/4000 PASS、`sc-18` 2700/12 轮 PASS，
  `sc-19`/`sc-20` 仍因外部预算/取消失败。旧 adversarial **7/9=77.8%、`q1_target_met=false` 不变**，
  B4 不关闭；这是 §3.2 的新增能力口径，不能覆盖旧 Q1（见 `docs/verification-q1-capable-2026-10-30.md`）。

---

## 7. 建议发布措辞（未来模板；本轮不发布）

> **本轮决定（2026-10-26）：不发布 v0.1.4-rc.1、不 bump、不打 tag。** B3 已由 t98 关闭，但以下措辞仍需在 **B4（Q1）**关闭后方可复用。
>
> 若未来发布，本版为 **v0.1.4-rc.1 候选版本，定位本地单用户技术预览**，不是生产可上线版本，不支持多租户与公网部署。
> 当前代码/测试基线 `68578c9` 的 CI（run `40319600547`）已 `completed`/`success`；`d766492`、`8126778`、`605b895` 及其 run 均为历史证据。本文 docs-only 提交后的 CI 另核，不把代码 CI 绿扩张成全量发布放行。
> 基准方面：3000 字章一次通过率 **7/9 = 77.8%**（Q1 门槛 80%，**未达成**），4000 字 **4/6 = 66.7%**。
> Q1 capable-provider arm（provider 服从字数提示、首轮额度合入提示、外部中断排除）为 **8/8 = 100.0%**，
> 含外部中断的 capable all 为 **8/9 = 88.9%**，全部目标 18/20 = 90.0%；这只是新增能力口径，
> **不能覆盖或替换 7/9=77.8% 的旧 Q1**，B4 仍未关闭。
> 已知限制（以下均指旧 adversarial 口径）：`sc-18` 受生成侧字数上限约束，本轮样本下 8/9 数学不可达；`sc-19` 属外部预算中断；
> `sc-15` 的越界已显式标记为 `overshoot`（不再报 `target_reached`），正文不裁剪，仍计入旧失败；
> desktop 冒烟在 macOS 上**曾有**窗口焦点竞态（某次会话 7 跑 6 过、另一次独立会话 1 跑 1 挂，`activation_attempts=55`；
> 历史失败保留）；t101 已加强**测试辅助**激活路径、t102 独立复跑 2/2 与 10/10，
> 但仅覆盖同机 GUI 会话 + Automation 已授权，**跨机器 / CI / 无 WindowServer 未证明，仍不宣称 26/26**。
> 真实模型证据：2026-10-27 本机复跑 5 章 / 33 120 字、43 条断言 / 4 条 rejected / 0 `provider_error`，
> 但 `repeat=2` 下 `identical=false`；2026-10-25 旧轮 23 条中 11 条可见。两者均不构成质量、召回或确定性通过。
> 真实模型 3000 字续写探针（2026-10-28）：默认 3635 超冲、`1.35` 3571 超冲、`1.0` 3176 带内；
> 同一 `1.0` 重复 3 次为 2856/3134/3714，**2/3 带内、1 次超冲**；`0.9` 被 HTTP 400 拒绝（下限 1.0），
> `2.0` 三次为 2721/907(failed)/2778，**2/3 成功带内 + 1 次提供方中途错误**；
> 仅证明该生产 HTTP 链路可完成目标且没有 3/3 稳定口径，不改变 Q1 7/9=77.8%<80%，也不构成发布放行。
> 本说明为草稿；独立复核（t91）原 `verdict=needs_revision` 保留不改写，B3 已由 t98 替代独立复核关闭；
> 采用上述措辞仍须先关闭 **B4（Q1 未达标）**。

---

## 8. 本文件的自证

- t90 只新增本文件；未改任何代码、未创建 tag、未读取或复述任何 API key、未记录任何评分。
- t92（repair-flash）按 t91 独立复核的 F1/F2 只修订本文件措辞（desktop 历史观测口径、产物 `generated_at` 口径、B3 状态）；
  **未改动 `docs/audit-review-independent-2026-10-26.md`**，其 verdict=`needs_revision` 与 findings 原文完整保留。
- t95（repair-flash）按 t94 的 T94-F1 只修订本文件的 HEAD/CI 与 §2.1 证据链：
  §0、§2、§2.1、§5.5、§6.1 B2/B3、§7 的代码基线统一为 `8126778`，CI 统一为 `40290743348`（t95 当时 HEAD；docs-only `605b895`/`40303229735` 见文首 HEAD 口径）；
  旧 `bc4709e` / `40278484458` / `10cff13` / `d766492` 只以**历史**口径出现，不再写成当前；
  **未改动** `docs/audit-review-independent-2026-10-26.md`、`docs/verification-release-readiness-2026-10-26.md`、
  `docs/verification-current-release-2026-10-26.md`、代码、测试与 `docs/CHANGELOG.md`。
- 引用 t94 报告：`docs/verification-current-release-2026-10-26.md`（124 行，t95 实测 sha256
  `4426769a9fd0413f07953d55cdc6cec3df90cb68758da4cbf9965cf6d418c31b`），其 `verdict=pass` **只绑定 t94 当轮字节，
  不关闭 B3/B4、不改写 t91 的 `needs_revision`**。
- t99（repair-flash）按 t98 关闭 B3、按 T97-F1 修正工作树措辞，**只改本文件**：
  §0 的「工作树口径」与硬阻塞计数、§0 维度表「独立复核」行与「版本号」行、§2 结论句、§5.1 状态解除条件、
  §5.5 时态与结论、**新增 §5.6**、§6.1 B1/B3、§7 两处措辞；
  代码基线/CI 仍为 `8126778` / `40290743348`（未回退；docs-only 提交见文首 HEAD 口径），B4/Q1 口径与版本 `0.1.3` 不变；
  未改代码、测试、`docs/CHANGELOG.md` 与其他任何报告，未 commit/push，未读取或复述任何 key，未记录评分。
- 引用 t98 报告：`docs/audit-review-b3-closure-2026-10-26.md`（96 行，t99 实测 sha256
  `10879f52faa30fd48da63f93c2b31d01e1fa188d66e83b5582eda09b38c33a65`），其结论**只关闭 B3**，
  不关闭 B4、不批准发布、不改写 t91。
- t103（repair-flash）按 t101/t102 更新 desktop 焦点结论与字节，**只改本文件**：
  §0 的「工作树口径」补入 t101 未提交的测试改动、§2.1 的 `tests/desktop_smoke.cjs` 行由
  `c6d13a0e…` / 784 行改为 `b409533e…` / 810 行（t101 后）并新增「例外」说明、§5.2 整节重写
  （t88/t91 历史失败保留 + t101 改动 + t102 独立 2/2 + 同机边界）、§5.6 边界项指回 §5.2、
  §6.2 与 §7 同步口径；
  代码基线/CI 仍为 `8126778` / `40290743348`（未回退；docs-only 提交见文首 HEAD 口径），B4/Q1 7/9=77.8%、`q1_target_met=false`、
  版本 `0.1.3`/不发布、t79 数学不可达、真实模型 11/23、B3 已关闭结论**全部不变**；
  未改代码、测试、`docs/CHANGELOG.md`、t91/t98/t100/t102 报告，未 commit/push，未读取或复述任何 key，未记录评分。
- 引用 t102 报告：`docs/verification-desktop-smoke-focus-2026-10-26.md`（90 行，sha256
  `1405a3a7c474ebf952a657b11681b7ca1cf2531de774f61343918c1162556659`），其 `pass` **只覆盖 desktop smoke 焦点修复**，
  不关闭 B4/Q1、不构成发布放行。
- t104（captain 接管，独立性降级）：修正文首 HEAD 口径与 §0/§2/§8 的自引用，明确「代码基线 `8126778`」与「本文 docs-only 提交 `605b895` / CI `40303229735`」；未改代码、测试、`docs/CHANGELOG.md` 或其他报告。
- 本次 t104 自证修正：上一行原写“t104 未改其他报告”，不准确；t104 实际新增了 `docs/verification-desktop-focus-release-2026-10-26.md`，其最终行数与 hash 见该报告与 t104 完成记录（本文不内嵌其自引用 hash）；该新增只记录验收，不改代码/测试。
- 本次 sc-15 文档收口（captain）：当前代码/测试基线改为 `68578c9` / CI `40319600547`（completed/success），更新 continuation 的 899 行/hash 与 sc-15 当前行号；`d766492..8126778` 仅保留为历史差异证据；t94/t95/t99/t103/t104 的 pass 与“当前”均按其验收窗口理解，不平移到当前字节。`docs/CHANGELOG.md` 同批修正预算 fake-provider 与真实模型的口径混淆及错误章节引用。本批为 docs-only，不内嵌尚未产生的本批提交号或 CI 号，不构成独立验收或发布放行。
- 2026-10-27 真实模型复跑（captain，本机）：应用模型连接写入 `default-project`，`probe_settings()` 返回
  `success=true`；生产 CLI 5 章 / 43 条断言 / 4 条 rejected / 0 `provider_error`，`repeat=2` 下
  `identical=false`。新增 `docs/verification-real-model-2026-10-27.md`；本次只改文档，不关闭 B4、不发布。
- 2026-10-28 真实模型 3000 字续写探针（captain，本机）：默认 / `1.35` / `1.0` 三臂字数
  3635 / 3571 / 3176，停止原因 `overshoot` / `overshoot` / `target_reached`；只有 `1.0` 带内。
  同一 `1.0` 重复 3 次为 2856 / 3134 / 3714，`target_reached` / `target_reached` / `overshoot`，
  **2/3 带内**。新增 `docs/verification-real-continuation-2026-10-28.md`；只改文档，不关闭 Q1/B4，不改产品默认。
- 2026-10-28 系数边界与 `2.0` 对照（captain，本机）：`0.9` 三次均被 HTTP 400
  `每轮额度系数必须在 1 到 10 之间。` 拒绝（`1.0` 为下限，无 job）；`2.0` 三次为
  2721 / 907（job failed，提供方中途错误）/ 2778 字，**2/3 成功且带内 + 1 次提供方错误**。
  与 `1.0` 的 2/3 带内 + 1 次超冲合并看，没有 3/3 稳定口径；停止调参，不改默认、不碰 Q1 分母。
- 2026-10-30 Q1 capable-provider arm（captain，本机）：新增 `benchmarks/budgeted/capable.py` 与
  `tests/q1_capable_fixtures.py`；`q1_engine=8/8=100.0%`、`q1_capable_all=8/9=88.9%`、全部目标 18/20=90.0%，
  `sc-15`/`sc-18` 在 capable 契约下 PASS，`sc-19`/`sc-20` 外部中断失败；旧 adversarial 7/9=77.8%
  与 `q1_target_met=false` 原样保留，B4 未关闭。新增 `docs/verification-q1-capable-2026-10-30.md`，
  并把当前关键字节固定到 `docs/current-bytes-manifest-2026-10-30.json`；`scripts/verify_manifest.py`
  与 `tests/current_bytes_fixtures.py` 可重算并拒绝漂移。本批只改基准/测试/文档，不改产品引擎默认值，
  不发布、不记录评分。
- 数字来源均已标注到文件/章节；未使用记忆值或转述值。
- 行数与 sha256 以提交后的 `wc -l`、`shasum -a 256` 为准。
