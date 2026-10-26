# desktop smoke macOS 焦点竞态修复 · 独立验收（t102）

- 验收对象：`tests/desktop_smoke.cjs` 当前工作树字节（t101 修复，未提交）
- 任务：t102（attempt 1，attempt_id `ebd391a8-a23f-41e7-aa0e-bb29406f8bba`）
- 验收人：verifier-current（独立于 t101 实现者）
- 时间窗口：2026-10-26 18:27:47 – 18:29:40 +08:00
- 结论：**pass**——仅限本测试；不关闭 B4/Q1，不代表发布放行。

## 一、被测字节（验收前后各采一次）

工作树基线：HEAD `605b89541a20e6a689ea10842005ec9bdd31afa5`，分支 `codex/repository-layout`。

| 采样点 | 时间 | sha256 | `wc -l` |
| --- | --- | --- | --- |
| 验收开始（跑前） | 18:27:47 | `b409533e73bddf8e66195ac73dc3e942be5b99db6657b5bbb5a31999a356e22d` | 810 |
| 验收结束（run1 后） | 18:29:09 | `b409533e73bddf8e66195ac73dc3e942be5b99db6657b5bbb5a31999a356e22d` | 810 |
| 验收结束（run2 后） | 18:29:40 | `b409533e73bddf8e66195ac73dc3e942be5b99db6657b5bbb5a31999a356e22d` | 810 |

三次一致。sha256 与 t101 自报值逐字相同（独立复算，非转述）。

## 二、独立实跑（同一 GUI 会话，直接 `node tests/desktop_smoke.cjs`）

| 运行 | 退出码 | 用时 | 用例 | `passed` | `window_focused` | `application_active` | `activation_attempts` | `native_activation` | `activation_policy` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| run1（18:28） | 0 | 9s | 10/10 | true | true | true | 5 | `{pid: 27013, ok: true}` | null |
| run2（18:29） | 0 | 9s | 10/10 | true | true | true | 1 | `{pid: 27161, ok: true}` | null |

其余关键字段（run1 报告原文）：`window_visible=true`、`window_minimized=false`、`always_on_top_used=true`、`page_errors=0`、`unexpected_external_requests=0`、`provider_jobs=0`、`synthetic_data_only=true`、`model_discovery.passed=true`（`generation_requests=0`）；4 个下载产物均有实际字节与 sha256（manuscript-json 2051B、markdown 2079B、epub 3649B、complete-backup 3753B）。

`activation_attempts` 在两次实跑中为 5 与 1，t101 自报为 2–3——同一量级、有波动，如实记录，不当作固定值。

## 三、修复是否真实（对照旧字节的已知失败证据）

| 条件 | 字节 | 结果 |
| --- | --- | --- |
| t88 会话历史观测 | 旧字节（784 行） | 7 跑 6 过 1 挂；失败时 `window_focused=false`、`application_active=false`，`activation_attempts` 55–59 仍未拿到焦点 |
| t91 独立复跑（2026-10-26 15:42 前后） | 旧字节 784 行 `c6d13a0e…faf4` | 1 跑 1 挂：`activation_attempts=55`、`window_focused=false`、`application_active=false` |
| 本次独立验收 run1/run2 | 新字节 810 行 `b409533e…e22d` | 2 跑 2 过：10/10，`window_focused=true`，`activation_attempts` 5 与 1 |

同一台机器、同一 GUI 会话下：旧字节可复现连焦点都拿不到（55 次轮询仍 false），新字节收敛到 1–5 次内拿到焦点。修复方向成立，且不是靠放宽判定得到的。

## 四、断言未被放宽（逐条核对当前字节）

1. **10 秒轮询仍在**：`:548-550` 仍为 `await until(async () => { … return activation.window_focused; }, 'NATIVE_WINDOW_FOCUS', 10000);`，该段在本次 diff 中无改动。
2. **判定仍由 `window.isFocused()` 决定**：`:535` `window_focused: window.isFocused()`；`:549` 返回该值；`until`（`:53-60`，每 100ms 轮询）超时抛 `TIMEOUT_NATIVE_WINDOW_FOCUS`，用例即失败。
3. **无 skip、无平台豁免**：全文 grep 只有 `:510` 的 osascript 助手在非 darwin 时返回 `{skipped: true}`——它只跳过“按 PID 置前”这一条原生助手路径，不跳过用例、不改变 `window.isFocused()` 判定；没有 `test.skip`，也没有把断言包进 `if (darwin)` 的写法。
4. **用例数未变**：`grep -c "^  await test("` = 10，与 784 行版本一致；diff 未动用例结构。
5. **diff 只有两类改动**（`git diff --stat`：1 file changed，32 insertions(+)，6 deletions(−)）：`:10` require 增加 `execFile`；`:503-560` 区段为激活路径增强（`setActivationPolicy('regular')` → `dock.show()` → `restore()`/`show()` → 短时 `setAlwaysOnTop(true,'floating')` → `moveTop()` → `focus()` → `app.focus({steal:true})` → 恢复置顶）与诊断字段（失败分支 `native_activation`、成功分支 `clipboard_activation`）。无一处降低期望值。
6. **副作用可控**：`setAlwaysOnTop` 之后恢复原状态（`if (!wasAlwaysOnTop) window.setAlwaysOnTop(false)`），不改变产品窗口语义；新增诊断不改判定。

## 五、只读范围与工作树

- `git diff --name-only` → 仅 `tests/desktop_smoke.cjs`（唯一改动文件）。
- `git diff --check` → 无输出，exit 0（验收前、验收后各一次）。
- `git status --porcelain` → 仅 ` M tests/desktop_smoke.cjs`；`desktop/main.cjs` 等产品代码未被改动。
- 未 commit、未 push。

## 六、边界（不作数的事）

- 本次 pass 只覆盖 `tests/desktop_smoke.cjs` 的 10/10，且条件为：本机当前 GUI 会话 + Automation 权限已授予（`native_activation.ok=true`）。
- 不等于跨机器 / CI / 无 WindowServer 会话的确定性：若权限被撤销或会话无 GUI，osascript 返回 error 而非静默降级，`native_activation` 会记录 error/stderr，用例会如实失败（不 skip、不放宽）。
- **t88 原失败保留**：该竞态是环境级问题，不是已证实的产品缺陷；「7 跑 6 过 1 挂」仍只是历史会话观测，不是稳定通过率，也不能读成 26/26。t101 的 4/4 与本次 2/2 都不改变这一口径。
- `activation_policy=null` 如实记录（该 Electron 版本 `app.getActivationPolicy?.()` 不可用），不影响判定。
- 本 pass **不关闭 B4、不改变 Q1**：Q1 仍为 3000 字 7/9=77.8% 未达标、4000 字 4/6=66.7%、`q1_target_met=false`；不代表发布放行。

## 七、FAIL 计数

- 验收项 FAIL = **0**（字节绑定 3/3、实跑 2/2 exit 0 且 10/10、断言核对 6/6、范围核对 3/3）。
- verdict：**pass**（仅限本测试的独立验收）。

## 八、本次执行的命令

```text
git rev-parse HEAD
git branch --show-current
git status --porcelain
git diff --name-only
git diff --check
git diff --stat tests/desktop_smoke.cjs
git diff tests/desktop_smoke.cjs
shasum -a 256 tests/desktop_smoke.cjs
wc -l tests/desktop_smoke.cjs
grep -n 'skip\|darwin\|platform' tests/desktop_smoke.cjs
grep -n 'isFocused\|NATIVE_WINDOW_FOCUS\|window_focused' tests/desktop_smoke.cjs
grep -c '^  await test(' tests/desktop_smoke.cjs
node tests/desktop_smoke.cjs        # run1：EXIT=0，9s，10/10
node tests/desktop_smoke.cjs        # run2：EXIT=0，9s，10/10
```

未调用真实模型（`provider_jobs=0`、`generation_requests=0`、`synthetic_data_only=true`），未读取或复述任何 API key，未记录评分。
