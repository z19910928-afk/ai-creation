# t93 替代独立验收：发布说明 t91 F1/F2 修复（当前字节终验）

- 验收日期：2026-10-26
- 验收人：verifier-current（独立验收员，未参与 t91/t92 的任何实现或修复）
- 被测对象：`docs/release-readiness-2026-10-25.md` 的 t92 修订（F1/F2 + B3/§5.1 同步）
- 唯一写入文件：本报告 `docs/verification-release-readiness-2026-10-26.md`
- 性质：**这是 t92 修复的替代独立验收，不是 t91 被改成通过。** t91 的 `verdict = needs_revision` 与其报告 `docs/audit-review-independent-2026-10-26.md` 原样保留，本报告不重开、不改写、不洗白。
- 结论：**pass**（仅表示 F1/F2 的文档收窄在当前字节上成立、关键口径无回退；不表示 Q1 达标、不表示桌面 26/26、不表示 B3 已关闭）。

## 一、被测字节与工作树状态

- 分支：`codex/repository-layout`
- HEAD：`5d22d8add0d15c7cea31f1e175fcad0224e5b85f`（`git rev-parse HEAD`）
- 工作树：验收开始时 `git status --porcelain` 仅两行——` M docs/release-readiness-2026-10-25.md`（t92 修订，**未提交**）、`?? docs/audit-review-independent-2026-10-26.md`（t91 报告，未跟踪）；本报告落盘后新增第三行 `?? docs/verification-release-readiness-2026-10-26.md`（本次唯一新增文件，无其他副作用）。因此本次绑定的是**工作树当前字节**（sha256 + 行数），不存在可绑定的 commit id；本报告不 commit、不 push。
- 产品/测试字节自 `85b13fe` 起只改过 `tests/desktop_smoke.cjs`（`d766492` 自身的诊断改动），`backend/`、`frontend/`、`benchmarks/`、`clear/` 零改动（`git diff --stat 85b13fe HEAD -- backend frontend benchmarks tests clear` 仅该文件 +19/−9）。

被测字节（验收开始采集，`shasum -a 256` + `wc -l`，完整清单见 `/tmp/t93-before.txt`）：

| 文件 | sha256 | 行数 |
| --- | --- | --- |
| `docs/release-readiness-2026-10-25.md` | `31e498e0106e0c801675f791a01e574582f4b8cf1fbeace7bb72c2766a88f172` | 267 |
| `docs/audit-review-independent-2026-10-26.md`（t91 报告） | `c18e54d60f6308cb202872e6492657e4778bfebc633fd54a573d3ed1cd7f5c6c` | 175 |
| `package.json` | `a8ea4f320c8916c9ca856bb3320d9f5c79e95f5ecb7ae2658788a98b5c1a6288` | 23 |
| `docs/CHANGELOG.md` | `c7992e821f26e1794e838fdb70227cba64e085c2f3af21f2b2f318e99e41c2ab` | 240 |
| `docs/consistency-budget-v1.2-2026-10-23.md` | `e711a1135713768de847826e3c93f273e75ef387f24d91a302515b45c17c1d55` | 234 |
| `benchmarks/budgeted/results/budgeted-v1.2-baseline.json` | `8a138dbf1c445d6ae83e2ab6926d98db85020d66fd31e609854370a28a4bb78c` | 1668 |
| `backend/moyu_extract.py` | `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8` | 1041 |
| `tests/desktop_smoke.cjs` | `c6d13a0ea450a8eb14ba2450f6de6d61ba0c706c70be06e9821c3ba4d81ffaf4` | 784 |

验收结束再次采集（`/tmp/t93-after.txt`）与上表**逐行完全一致**：`diff <(grep -v '^# t93' /tmp/t93-before.txt) <(grep -v '^# t93' /tmp/t93-after.txt)` 无输出、`EXIT=0`（两文件唯一差异是首行采集时间戳，见 §十）。t91 报告另核：mtime `2026-10-26 15:42:08`、size `14906`、未跟踪状态未变，说明本轮没有任何人改写它。

## 二、验收方法

1. 只读通读 t91 报告、t92 实际 diff 与发布说明当前全文（267 行）。
2. 自写只读探针 `/tmp/t93_probe.py`（仅标准库；读上表文件 + `git tag` 子进程），把 F1 三处措辞、F2 时间戳口径、Q1 双分母、11/23 边界、版本决定、t91/t89/B3 状态写成 48 条可复跑断言，逐条打印 `PASS/FAIL`，末行 `TOTAL=<n> FAIL=<m>`，`FAIL>0` 时退出码 1。
3. 独立复算 baseline JSON（未引用任何自述数字）；核对 `extract.py` 的 `generated_at` 实现与两处披露锚点。
4. 跑合同规定的 `git diff --check` 与 `tests/repository_privacy_fixtures.py`。
5. 写盘后回读本报告并与外部记录对账（§十）。

## 三、探针结果

命令：`.venv/bin/python /tmp/t93_probe.py`

```text
TOTAL=48 FAIL=0
EXIT=0
```

48 条按组分布：A 字节/版本 3 条；B t91 历史保留 9 条；C F1 三处措辞 11 条；D F2 时间戳口径 9 条；E Q1 与 11/23 边界 12 条；F 版本决定/发布范围 4 条。全部 PASS，`FAIL=0`。

其中反向断言（证明没有粉饰）包括：
- `26/26` 出现 6 行（`:168`、`:180`、`:185`、`:187`、`:240`、`:255`）逐行都在「不得宣称 / 禁止 / 不宣称 / 非稳定」语境；`6/7` 出现的行逐行含否定或「个别观测」限定；`14%` 出现的行逐行含「不能」。
- 全文无 `B3 已关闭` / `B3 通过` / `t91 已通过` / `t91 verdict=pass`；无 `23/23`；`已逐条审计` 只出现在「禁止表述为…」一句里；`Q1 达成` 只出现在「属禁止行为」一句里。

## 四、F1（desktop 历史 7 跑 6 过被写成稳定比例）逐条

t91 的 F1（medium）指原 `:174`/`:221`/`:236` 把「某次会话 7 次实跑 6 过 1 挂」写成稳定属性，而 t91 独立复跑为 1 跑 1 挂（`activation_attempts=55`、`window_focused=false`、`application_active=false`）。当前字节：

| 位置 | 当前写法 | 判定 |
| --- | --- | --- |
| `:172-173` 表 | 「某次会话的连续实跑（历史观测）｜**7 次：6 次 10/10 通过、1 次卡住** —— 仅该会话样本，**不是稳定通过率**」+「另一次独立会话复跑（t91）｜**1 跑 1 挂**：`activation_attempts=55`…」 | 历史观测与独立复跑拆开，6/7 不再当稳定比例 |
| `:175` 决定性字段 | 「`activation_attempts` 55–59」 | 用区间覆盖两次会话（55 与 59），不再是单点 59 |
| `:181-184` 正文 | 「只是某次会话的个别观测，不是该用例的稳定属性」；「**不能把它读成 6/7 的稳定通过率，也不能把它读成 14% 的偶发失败率**」；「环境级、**不是已证实的产品缺陷**」 | 三句齐全，且保留「环境级/非产品缺陷」原定性 |
| `:185-187` | **禁止**写「26/26 全绿」「桌面端全部通过」；正确模板含「55 次激活尝试全部未获焦点」「未修…不宣称 26/26」 | 收窄为「未修 + 不宣称」 |
| `:240` §6.2 | 某次会话 7 跑 6 过（个别观测）；t91 另一次独立复跑 **1 跑 1 挂** | 与 §5.2 一致，无「14% 偶发率」正面用法 |
| `:255` §7 | 「某次会话 7 跑 6 过、另一次独立会话 1 跑 1 挂；未修，不宣称 26/26」 | 复用模板同步 |

结论：F1 的三处（§5.2 表、§5.2 正文、§6.2/§7）已按事实收窄；`26/26` 与 `6/7`、`14%` 的反向断言全部 PASS，未发现任何把历史观测写成稳定通过率的残留。

## 五、F2（旧 `/private/tmp` 产物 `generated_at` 硬编码）逐条

t91 的 F2（low）指三份旧产物 `generated_at` 是旧脚本硬编码值，容易被当成生成时刻。当前字节 `:197-202` 新增「**产物时间戳口径（必读）**」：

- `:198` 明写旧硬编码值 `2026-10-24T00:00:00+08:00`「**不能当作生成时刻**」；
- `:199` 明写「引用时以文件 **mtime（10-25 16:50 / 16:52 / 16:56）与 sha256** 为准」；
- `:200` 明写当前 `benchmarks/real_corpus/extract.py:254` 已改用 `datetime.now()`；
- `:201` 指明属**已披露问题**（`docs/audit-response-2026-10-25.md:19` A8、`docs/audit-review-team-2026-10-25.md:45`）；
- `:202` 明写**不改变**上面的 11/23 证据边界。

独立核对（不只读文档）：
- `benchmarks/real_corpus/extract.py:254` 确为 `"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),`，全文无 `2026-10-24T00:00:00+08:00` 硬编码（探针 D7 PASS）。
- `docs/audit-response-2026-10-25.md:19`（A8 行）确含该硬编码值及「旧产物时间戳不可信…以 mtime/hash 为准」（D8 PASS）。
- `docs/audit-review-team-2026-10-25.md:45` 确含「三份 JSON 的 generated_at 均为旧硬编码…」（D9 PASS）。

结论：F2 已按 t91 建议落成半节「必读」口径，并给出 mtime/hash 替代、代码锚与已披露来源；无夸大。

## 六、关键口径无回退

| 口径 | 当前字节证据 | 独立复算 |
| --- | --- | --- |
| Q1 3000 字 7/9 = 77.8% 未达标 | `:23`、`:235`、`:252` | baseline JSON `by_target["3000"] = {total:9, first_pass:7, failed:2, rate:0.7778}` |
| Q1 4000 字 4/6 = 66.7% | `:24`、`:252` | `by_target["4000"] = {6,4,2,0.6667}` |
| `q1_target_met = false` | `:235` | 顶层 `q1_target_met = false`（Python 读到的是 `False`） |
| 7/8 = 87.5% 只是诊断 | `:104`、`:106`、`:107` | 文档明写「明确标注的诊断值」「禁止冒充指标」「4000 独立不能套用」；探针 E8/E9 PASS |
| 23 条中 11 条有逐字证据 | `:213` 表（0/0、7/3、7/3、7/3、2/2，合计 23/11）、`:215` 剩 12 条、`:216` 禁止「已逐条审计」 | 表内 0+7+7+7+2=23、0+3+3+3+2=11，逐行相加一致 |
| 版本保持 0.1.3、不 bump | `:27`、`:232` B1、`:248` | `package.json` = `0.1.3`；`git tag -l` 仅 `v0.1.0`–`v0.1.3`，无 `v0.1.4*` |
| t89 是 captain 接管、独立性降级 | `:158`、`:234` B3、`:160` | 文档明写「『独立』二字对 t89 不成立」，未把 t89 当独立 verdict |
| t91 = needs_revision、不宣称通过 | `:154` 标题、`:163`、`:234` | t91 报告 `:139` 仍为 `**verdict = needs_revision**`，`:145` F1 medium、`:146` F2 low 原文未动 |
| B3 未通过且未关闭、需替代验收 | `:234` | 「独立复核**未通过**（不是「缺位」，也未关闭）…需**替代独立验收**」；`:166`「拿到 `pass` 之前 **B3 不关闭**」 |

补充：文档 §5.5 保留「历史 content-bound verdict 未重绑，不把旧 verdict 当当前通过」，未在本次修订中被放宽（该项为只读通读确认，未纳入探针断言）。

## 七、合同验证命令

| 命令 | 结果 |
| --- | --- |
| `git diff --check` | 无输出，`EXIT=0` |
| `.venv/bin/python tests/repository_privacy_fixtures.py` | `repository privacy fixtures: ok (Git index only)`，`EXIT=0` |

另做只读交叉核对：`gh run view <id> --json headSha,conclusion,status` → `40188967438` = `d766492…/success`、`40278484458` = `bc4709e…/success`、`40281317350` = `5d22d8ad…/success`；即被测 `d766492`、文档所引 `bc4709e`、真实 HEAD `5d22d8ad` 三者 CI 均绿，文档「CI 全绿」的实质结论成立。

## 八、观察项（低，不阻塞 pass；建议下一轮修文档）

以下三条都是文档事实标签/坐标问题，不影响 F1/F2 收窄结论，也不改变 Q1/版本/B3/11-23 口径：

1. **「当前 HEAD」标签不一致（低）**：文档 `:22`、`:233` B2、`:251` 写「当前 HEAD bc4709e」，但 `:57`、`:59`、`:67-68`、`:70`、`:81` 以 `10cff13` 为当前 HEAD；真实 HEAD 是 `5d22d8ad`。三个 commit 的 CI 均 success，且自 `d766492` 起 docs 之外零改动，所以结论不失效；建议统一写成「发布草稿编写时 HEAD」或更新为 `5d22d8ad`。
2. **§2 的 diff 命令在当前 HEAD 不可复现（低）**：文档给出「`d766492..10cff13` 的完整差异（`git diff --stat d766492..HEAD`）」为 +75/1 个 docs 文件；当前 `git diff --stat d766492..HEAD` 实测 = `docs/CHANGELOG.md +4`、`docs/audit-review-current-2026-10-25.md +75`、`docs/release-readiness-2026-10-25.md +246`（3 files +325）。建议把命令钉子改成当时的具体 ref，或按当前 HEAD 重算。
3. **两处坐标漂移（低，其一为 pre-existing）**：`docs/audit-response-2026-10-25.md:19` 引 `benchmarks/real_corpus/extract.py:253`，当前实际在 `:254`（本报告的 §5.3 已正确写 `:254`）；文档「34 步全绿」按 API 实测为 37 steps 全 success（33 实质步骤 + 3 个 Post 清理 + Complete job），按「33+Complete」可复算为 34，属计法差异而非错误。

## 九、验收局限

- 本次只做文档口径与证据链复核；**未跑真实模型**、**未跑桌面冒烟**（不重跑 `desktop_smoke.cjs`），因此不重新判定 t88 的环境级定性，只核对文档是否如实收窄。
- 不覆盖 Q1 指标本身（7/9 未达标是既有结论，不是本次修复对象），也不覆盖 sc-15/sc-18 的引擎修复状态。
- 未改任何被测文件、探针、产品代码、测试或样本；未读取/复述任何 API key；未记录评分。

## 十、回读对账

- 本报告写盘定稿后，先把自身 `sha256` 与 `wc -l` 记录到外部文件 `/tmp/t93-report-identity.txt`，再整文件回读并按同一命令复算，两次结果一致（`ALL_MATCH`）；报告自身 sha256/行数不写进正文，避免「写入即改变哈希」的循环，具体值见该外部文件与验收提交 evidence。
- 被测 8 文件验收前后表（§一）逐行一致：`diff <(grep -v '^# t93' /tmp/t93-before.txt) <(grep -v '^# t93' /tmp/t93-after.txt)` 无输出（`EXIT=0`）；8 个文件的 sha256 + 行数 8/8 相同，两文件唯一差异是首行采集时间戳。

## 十一、验收结论

- **verdict = pass**：t92 对 t91 F1/F2 的修订在当前字节上成立，三处 desktop 措辞已收窄为「历史会话观测 + t91 独立 1 跑 1 挂」，旧产物时间戳已明确以 mtime/hash 为准；Q1 双分母、`q1_target_met=false`、11/23 边界、0.1.3 不 bump、t89 独立性降级、B3 未通过未关闭均无回退。
- 本结论是**替代独立验收**：t91 的 `needs_revision` 与其报告原样保留，本报告不构成对 t91 的改判，也不等于 B3 关闭、Q1 达标或发布放行（发布决定仍由 §7 的「本轮不发布」与 B3/B4 状态约束）。
