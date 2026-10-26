# 独立复核：HEAD `5d22d8ad` 的发布阻塞项 B3/B4 与当前字节证据

- 复核人：`verifier-flash`（独立于 captain，未参与本轮任何实现/修复）
- 复核时间：2026-10-26
- 复核对象：分支 `codex/repository-layout`，HEAD `5d22d8add0d15c7cea31f1e175fcad0224e5b85f`
- 复核性质：**只读**。未修改产品代码、测试、其他文档；未 commit/push；未调用真实模型；未读取或复述任何 API key；未记录任何评分。
- 唯一写入文件：本报告。
- 独立性声明：本报告不沿用 `docs/audit-review-current-2026-10-25.md`（captain 接管的 t89，独立性已降级）的任何结论作为前提。下列每一项都给出我自己跑出的命令与输出；凡无法亲验的，单列为「未证实」。

---

## 一、字节绑定（验收开始 / 结束两次采集）

| 文件 | sha256（前 16） | 行数 |
| --- | --- | --- |
| `backend/server.py` | `f74c885f3a058c2b` | 8198 |
| `backend/moyu_continuation.py` | `583059fe2fe989a9` | 896 |
| `backend/moyu_extract.py` | `6b5d431364057b5a` | 1041 |
| `benchmarks/real_corpus/extract.py` | `2e046994280e26be` | 301 |
| `tests/extract_fixtures.py` | `f48b4f191d7fa390` | 1372 |
| `docs/release-readiness-2026-10-25.md` | `f689ea1d43a88c98` | 246 |
| `docs/CHANGELOG.md` | `c7992e821f26e179` | 240 |
| `package.json` | `a8ea4f320c8916c9` | 23 |

- 命令：`shasum -a 256 <file> | cut -c1-16` 与 `wc -l < <file>`，验收开始与结束各采集一次。
- 结果：**8/8 两次完全一致**（同一 sha 前缀、同一行数）。
- `git status --porcelain` 开始与结束两次均为空 → 工作树 clean，本报告落盘前仓库无未提交改动。
- `git log --oneline -3`：`5d22d8a docs: record release decision to hold 0.1.3` / `bc4709e docs: add release readiness draft` / `10cff13 docs: record current-byte captain review`。

## 二、CI 与 HEAD 对齐（必查 2）

```
gh run view 40281317350 --json headSha,conclusion,status,workflowName,createdAt
→ {"conclusion":"success","createdAt":"2026-10-26T06:41:38Z",
   "databaseId":40281317350,"headSha":"5d22d8add0d15c7cea31f1e175fcad0224e5b85f",
   "status":"completed","workflowName":"墨屿检查"}
```

- `headSha` 与本地 `git rev-parse HEAD` **逐字符相同**；`conclusion=success`。
- 参考：`gh run list --limit 5` 中 `40278484458`（`bc4709e`）、`40193598447`（`10cff13`）、`40188967438`（`d766492`）、`40187117293`（`85b13fe`）亦均 success。
- **口径限定**：CI 绿 = 该 SHA 上「墨屿检查」工作流通过，**不等于** Q1 达标、不等于真实模型语义正确、不构成生产放行。

## 三、Q1 两个分母（必查 3）——已证实

数据源自仓库内真实产物 `benchmarks/budgeted/results/budgeted-v1.2-baseline.json`（我自己 `json.load` 读出，未抄文档）：

```
summary.total=20  first_pass=16  rate=0.8
by_target["3000"] = {total:9, first_pass:7, failed:2, rate:0.7778}
by_target["4000"] = {total:6, first_pass:4, failed:2, rate:0.6667}
q1_target_met = False
```

逐场景复算（我按 `first_pass` 字段聚合，不从文档转述）：

- 3000 字：通过 `sc-02/03/07/08/12/14/17` = **7/9**，失败 `sc-18`（stalling，rounds=2，chars=46，stop=no_progress）、`sc-19`（budget_limited，rounds=2，chars=2318，stop=budget_exhausted）。
- 4000 字：通过 `sc-04/09/10/13` = **4/6**，失败 `sc-15`、`sc-20`。

与 `docs/release-readiness-2026-10-25.md:23-25`、`:92-93`、`:216` 完全一致：**7/9 = 77.8%、4/6 = 66.7%、`q1_target_met=false`**。两个分母互相独立，不可合并、不可互相代入。

## 四、t79：sc-18/sc-19 在冻结样本/provider/评分下 8/9 数学不可达（必查 4）——已证实

我自己复算，不引用 t79 自述：

`benchmarks/budgeted/run.py` 的冻结定义（`sed -n` 读出）：
- `:571-574` `first_pass = (result["stop_reason"] == TARGET_REACHED and result["within_tolerance"] is True)`；`:12` 界定达标区间 `[ceil(0.9×目标), floor(1.1×目标)]` → target=3000 时为 **2700–3300**。
- `:169-173` `stalling` → `fixed_yield: (12, 30)`，`yield_note`「单轮固定 12–30 字，低于 min_round_gain=50」。
- `:175-178` + `:544-546` `budget_limited` → `ratio: (0.50, 0.08)`，`budget_check = lambda round_index: round_index <= 2`（**只放行第 1、2 轮**）。
- `:115/:124` `hard_max_rounds: 12`（v1.1 起）。

我的复算（独立脚本，纯数学 + 20 万次蒙特卡洛，未改仓库）：

| 场景 | 理论上界 | 距下限 2700 | 可达？ |
| --- | --- | --- | --- |
| sc-18（stalling，12 轮 × 30 字上限） | 360 字 | 差 2340 | 否 |
| sc-18（即使把 `hard_max_rounds` 放到引擎上限 24） | 720 字 | 差 1980 | 否 |
| sc-19（两轮，单轮最多 0.58×剩余） | 第 1 轮 1740 + 第 2 轮 731 = **2471**（闭式 2470.8） | 差 229 | 否 |

蒙特卡洛（seed 7 / seed 11，各 20 万次）：sc-19 两轮最大值 **2470**、sc-18 12 轮最大值 **331**，均 `< 2700`。

**结论**：在冻结样本、冻结 provider（`fixed_yield`/`ratio` 与 `budget_check` 都在 benchmark 侧，且任务契约禁止改动）与冻结评分（`within_tolerance` 未放宽）三者不变的前提下，3000 字子集的可达上界就是 **7/9**；把 7/9 提到 8/9 在数学上不可能，需要改样本/评分/契约之一，而不是改引擎。t79 的 BLOCKED 结论**成立**，且 sc-19 只有 229 字余量这一点也写实了——它确实不是「差很多」，而是「差一点但确定到不了」。

## 五、t88：desktop_smoke 的焦点结论（必查 5）——机制成立，但「7 次 6 过 1 挂」这个比例不可复现

代码层（`sed -n '503,535p' tests/desktop_smoke.cjs`）我亲验：
- 卡点是 `currentStep = 'activate-native-desktop-window'`，用 `until(..., 'NATIVE_WINDOW_FOCUS', 10000)` 在 **10 秒内重复请求** `app.dock.show()` + `app.focus({steal:true})` + `window.focus()`；
- **断言未放宽**：循环条件仍是 `activation.window_focused`，失败即抛 `TIMEOUT_NATIVE_WINDOW_FOCUS`；
- 失败时落 `clipboard_diagnostics`（含 `activation_attempts`、`ssh_session`、`terminal_program`），可诊断。

旧日志 `/private/tmp/cjs-desktop_smoke.cjs.log`（mtime 10-25 14:40）确认：10 用例 9 过 1 挂，仅 `clipboard-write-from-user-gesture` 失败，`page_errors=0`、`unexpected_external_requests=0`、`clipboard_diagnostics={window_focused:false, window_visible:true, window_minimized:false, application_hidden:false}`。

**我自己的独立复跑（本次 1 次，非引用他人数字）**：

```
node tests/desktop_smoke.cjs  → EXIT=1
cases=10 passed=9，唯一失败 clipboard-write-from-user-gesture
clipboard_diagnostics = {"window_focused": false, "window_visible": true,
  "window_minimized": false, "application_active": false,
  "application_hidden": false, "activation_attempts": 55,
  "ssh_session": false, "terminal_program": null}
failure = activate-native-desktop-window / TIMEOUT_NATIVE_WINDOW_FOCUS
```

**结论分两层**：
1. **机制与定性成立**：窗口 `visible=true` 但 `isFocused()=false`、`application_active=false`，55 次激活全失败，且失败点不在任何产品功能路径上（其余 9 个用例含保存/下载/单实例/退出/重启恢复全部通过，`page_errors=0`）。「macOS 原生窗口焦点竞态、环境级、非产品缺陷、未放宽断言」这一判断我独立确认。
2. **比例不成立**：`docs/release-readiness-2026-10-25.md:174`、`:221`、`:236` 把「7 次实跑 6 过 1 挂」当作该用例的稳定属性。我这一次独立复跑是 **1/1 失败**（55 次激活尝试），说明失败与否取决于运行会话当时是否有可被激活的前台应用，**不是 6/7 ≈ 86% 的稳定通过率**。该数字应降级为「某次会话 7 跑 6 过」的个别观测。

## 六、真实模型 23 条断言的证据边界（必查 6）——已证实

我自己对 `/private/tmp/moyu-real-current-5.json` 逐章统计：

| 章 | `assertions` | `audit` 可见条数 |
| --- | --- | --- |
| 红楼梦/001 | 0 | 0 |
| 红楼梦/002 | 7 | 3 |
| 红楼梦/003 | 7 | 3 |
| 红楼梦/004 | 7 | 3 |
| 红楼梦/005 | 2 | 2 |
| **合计** | **23** | **11** |

`files exist`：`moyu-real-current-5.json`（5 章 / 33120 字 / 23 断言 / 5 拒全 `empty_value` / `wall_s=172.192` / `repeat=1`）、`moyu-real-current-180.json`（2 章 / 12463 字 / 17 断言 / 14 拒 = 6 `empty_value` + 8 `quote_mismatch` / `wall_s=102.677`）均在盘，字段与文档一致。

`benchmarks/real_corpus/extract.py:159-161` 亲验 `--audit-limit` 语义：默认 `3`；`audit_limit <= 0` 时写全部断言；`:250` 入口 `audit_limit=max(0, args.audit_limit)` → 负数钳成 0（fail-open，多留证据）、非整数由 argparse `type=int` 直接 `exit 2`；`:254` `generated_at` 现为 `datetime.now()`。文档 `docs/real-corpus-extract-2026-10-24.md:264-278` 的三条边界语义与代码逐条一致。

全仓检索 `23 条全部` / `23 条逐条审完` / `23/23`：**无任何一处把 23 条真实断言写成已验证**；唯一命中 `docs/CHANGELOG.md:163` 的 `19/19、55/55、23/23、31/31` 是旧的本机工程复核计数（与本主题无关），另一处命中的是本复核团队的旧报告自述。**「23 条全部验证」类虚假表述：不存在。**

**边界必须保持**：11 条可见证据 → 只能说「11 条上 0 findings」；**不得**说 23 条语义正确、不得说稳定通过率、不得外推为生产零误报。

附：三个 `/private/tmp` 产物的 `generated_at` 仍是旧硬编码 `2026-10-24T00:00:00+08:00`（`extract.py:254` 早已改 `datetime.now()`，故它们是旧脚本产物），而 mtime 为 `2026-10-25 16:50/16:52/16:56`。这是**已披露问题**（`docs/audit-response-2026-10-25.md:19` A8、`docs/audit-review-team-2026-10-25.md:45`）：「旧产物时间戳不可信，以 mtime/hash 为准」。我独立确认该结论成立，不重复计为新问题。

## 七、B1/版本号决定（必查 7）——与证据一致

- `package.json:3` `"version": "0.1.3"`；`git tag -l` 仅 `v0.1.0`–`v0.1.3`，**无 `v0.1.4*`**。
- 发布说明 `:27`、`:213`、`:229` 承诺「仍为 0.1.3、不 bump、不打 tag」，与仓库现状逐项吻合。
- 该决定与证据自洽：Q1 3000 字 7/9=77.8% < 80%（§三）、4000 字 4/6（§三）、`q1_target_met=false`、sc-18/sc-19 数学不可达（§四）、真实模型仅 11/23 可见证据（§六）、desktop 焦点用例非稳定通过（§五）。在这些前提下宣布 `v0.1.4-rc.1` 会把未验证状态包装成 RC。**「保持 0.1.3、不发布 v0.1.4-rc.1」是当下唯一与证据一致的动作。**

## 八、verdict 与 findings

**verdict = needs_revision**

理由：六项事实性证据（字节绑定、CI、Q1 双分母、t79 数学不可达、11/23 边界、0.1.3 决定）我都独立复现且与文档一致，**B3 的独立性缺位已由本报告在字节/CI/Q1/t79/11-23 这五项上补齐**；但 t88 的「7 次 6 过 1 挂」在我独立复跑下是 1/1 失败，该数字作为「稳定失败率」的写法不成立，需要修订后才能用作发布说明的量化依据。按「不伪造独立通过」的原则，不给出 pass。

| id | 严重度 | 问题 | 建议修复 |
| --- | --- | --- | --- |
| F1 | medium | `docs/release-readiness-2026-10-25.md:174`、`:221`、`:236` 把「7 次实跑 6 过 1 挂」写成 desktop 焦点用例的稳定属性；独立复跑 1/1 失败（`activation_attempts=55`、`window_focused=false`、`application_active=false`），说明失败率依赖运行会话是否有可激活前台应用，6/7 不可复现为比例 | 改为「某次会话 7 跑 6 过；另一次独立会话 1 跑 1 挂（55 次激活尝试全部未获焦点）→ 该用例在无前台可激活应用的环境下呈确定性失败，不是 14% 的偶发率」。只改这两个文档的这三处措辞，不动结论「环境级、非产品缺陷、不宣称 26/26」 |
| F2 | low | `/private/tmp` 三份产物 `generated_at` 为旧硬编码 `2026-10-24T00:00:00+08:00`，与 mtime（10-25 16:50–16:56）不符、也与当前 `extract.py:254` 的 `datetime.now()` 不符 | 已在 `audit-response:19`(A8) 与 `audit-review-team:45` 披露；建议在发布说明的产物引用处加半句「时间戳为旧硬编码，以 mtime/hash 为准」，避免读者把 `generated_at` 当作生成时刻 |

## 九、三类清单

**已证实（我自己跑出）**
1. HEAD `5d22d8ad`、工作树 clean、8 个关键文件验收前后 sha256+行数 8/8 一致。
2. CI `40281317350` 的 `headSha` = HEAD，`conclusion=success`。
3. Q1：3000 字 7/9=77.8%、4000 字 4/6=66.7%、`q1_target_met=false`，逐场景 ID 与文档一致。
4. sc-18 上界 360（或放宽到 24 轮也只有 720）、sc-19 上界 2471，均 < 2700 → 8/9 数学不可达，t79 BLOCKED 成立。
5. 23 断言 / 11 条可见证据（0,7,7,7,2 → 0,3,3,3,2）；`--audit-limit` 默认 3、0=全部、负数钳 0、非整数 exit 2；`generated_at` 已改 `datetime.now()`。
6. 无「23 条全部验证」类虚假表述。
7. `package.json` 0.1.3、无 `v0.1.4*` tag；保持 0.1.3 的决定与全部不利证据一致。
8. desktop_smoke 失败点非产品路径（其余 9 用例全过、`page_errors=0`、`unexpected_external_requests=0`），断言未被放宽。

**未证实（我未独立重跑，只能标注为沿用的自述）**
1. 「7 次实跑 6 过 1 挂」的 **7 次**：我只做了 1 次独立复跑（结果 1/1 失败），无法复现 6 次通过的分布。
2. 真实模型 5 章/2 章复跑的**真实性**：我核对了产物存在性、字段、聚合数（33120 字 / 23 断言 / 5 拒 等），但**没有也无法**在不调用真实模型的前提下验证「这些字节由授权模型在 10-25 16:5x 生成」。该链路的真实性仍是文档自述 + mtime/hash 旁证。
3. t89 报告（captain 接管、独立性降级）的结论——本报告不以其为前提，也不对其背书。

**外部阻塞**
1. 真实模型全量 23 条审计需授权 key 重跑（`--audit-limit 0`），本任务禁止调用真实模型 → 12 条无逐字证据的断言无法在本轮补齐。
2. 8/9 需要改动冻结样本/provider/评分之一（benchmark 契约与本次 in-scope 均不允许）→ 数学不可达无法在本轮解除。
3. desktop 焦点竞态需要「有可激活前台应用」的 GUI 会话才能复现通过路径，与 agent 会话环境强相关 → 稳定 26/26 本轮无法证明。

## 十、本报告不做什么

- 不替代 captain 的发布裁决：本报告只提供候选结论 + 证据链 + 剩余风险。
- 不把 CI 绿等同于 Q1 达标、不等于生产放行。
- 不修改任何产品代码/测试/其他文档，不 commit，不 push。
- 不读取、不复述、不保存任何 API key；不记录任何评分。
