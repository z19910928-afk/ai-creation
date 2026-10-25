# desktop_smoke 焦点用例定性报告（2026-10-25）

- 任务：t88（承 t80 的 26 项 CJS 回归唯一失败项 `clipboard-write-from-user-gesture`）
- 执行者：browser-flash（浏览器回归工程师）；`tests/desktop_smoke.cjs` 的改动由 captain 落，本文负责复现、定性、取证
- 范围：`tests/desktop_smoke.cjs`（唯一代码改动）、本文件（唯一新文档）；未改 `backend/`、`frontend/`、`desktop/` 产品代码
- 未调用真实模型，未读取或复述任何 API key

---

## 1. 结论（先给判定，含"未修"）

**这是会话/装置级的 macOS 激活限制，不是产品缺陷；且 captain 的"重试"修复实测无效——按任务第 4 条，结论是「未修」。**

三条独立证据：

1. **失败已在本机复现一次**（不是只听 t80 的转述）。失败诊断与 t80 完全同签名，并多了一个决定性字段
   `activation_attempts: 59`：10 秒等待窗口内**重复请求激活 59 次，全部被 OS 拒绝**。
   即 `window_visible=true`、`window_minimized=false`、`application_hidden=false`、
   `application_active=false`、`window_focused=false`。
2. **同签名在多数运行里不出现**：本机同一会话共 7 次完整实跑，6 次 10/10 通过、1 次卡在该用例
   （见 §3）。通过/失败与「前台 bash / 后台作业」无相关性（后台 2 次：1 失败 1 通过），
   说明是**时间相关的 OS 激活拒绝**，不是哪种 shell 的决定性差异，更不是代码分支。
3. **失败发生在任何产品断言之前**：用例在 `activate-native-desktop-window`（`tests/desktop_smoke.cjs:504`）
   就抛 `TIMEOUT_NATIVE_WINDOW_FOCUS`，`preserve-all-clipboard-formats`、`navigator.clipboard.writeText`、
   原生剪贴板比对**一次都没执行**。因此不是 `RENDERER_CLIPBOARD_WRITE_REJECTED`，不能归因产品剪贴板链路。

产品侧为何不受影响：`desktop/main.cjs:176` 是
`window.once('ready-to-show', () => {if (!smoke) window.show();});`
—— 只有冒烟模式故意不显示窗口。真实启动路径在 `ready-to-show` 就 `show()`，用户点开应用即完成激活；
「从未显示过窗口的进程后来自己抢激活」这个脆弱场景只存在于测试装置里。

## 2. 装置侧修复为什么不算"修好"

改动只在 `tests/desktop_smoke.cjs:503-536`：

| 项 | 改动前 | 改动后 | 结论 |
| --- | --- | --- | --- |
| 激活 | 单次 `app.focus({steal:true})+show()+focus()` | 封装为 `activate()`，`show()` 前加 `isVisible()` 守卫 | 等价 |
| 等待 | `until(isFocused(), 10000)`，只读轮询 | `until(activate().window_focused, 10000)`，10s 内重复请求激活 | **实测无效**：失败那次重试 59 次仍全被拒 |
| 断言 | `isFocused()` 必须为真，超时抛 `TIMEOUT_NATIVE_WINDOW_FOCUS` | 完全一致（`tests/desktop_smoke.cjs:523-527`） | **未放宽，未删除** |
| 诊断 | `window_focused/visible/minimized/application_hidden` | 追加 `application_active`、`activation_attempts`、`ssh_session`、`terminal_program`（`tests/desktop_smoke.cjs:528-533`） | 有价值（正是它给出了 59 次被拒的证据） |

判据：`activation_attempts=59 > 1` 且 `application_active=false` —— 重试次数已经足够，问题不是"请求没发出去"，
而是**OS 层拒绝把激活交给这个进程**。进程内没有更强的 API：`app.focus({steal:true})` 已是 Electron 文档里最强的一档。

附带核对：`application_active` 用 `app.isActive?.()`。Electron 44.9.0 的类型声明
（`node_modules/electron/electron.d.ts:1506`）是 `isActive(): boolean;`，即**方法**不是属性，
故 `?.()` 调用安全；写成属性 `app.isActive` 会把函数本身记进诊断，属于失真。

## 3. 实测证据（7 次完整实跑）

环境事实：

```text
macOS 26.6.1 (25G76), arm64
launchctl managername → Aqua              # 有 WindowServer 交互会话
IOConsoleLocked = No                      # 屏幕未锁（排除锁屏）
console user → zhaozhen
前台应用 → DeepSeek Harness
electron 44.9.0；python .venv/bin/python
shell env: ELECTRON_RUN_AS_NODE=<set>      # 受控 shell 特征，冒烟脚本在 tests/desktop_smoke.cjs:409 过滤
```

命令统一为 `MOYU_TEST_PYTHON=$PWD/.venv/bin/python node tests/desktop_smoke.cjs`，日志在 `/tmp/t88/`：

| # | 版本 | 上下文 | exit | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 改动前（HEAD） | 前台 bash | **0** | 10/10（含焦点用例） |
| 2 | captain 修复版 | 前台 bash | **0** | 10/10 |
| 3 | captain 修复版 | **后台作业** | **1** | **9/10，`clipboard-write-from-user-gesture` 失败** |
| 4 | captain 修复版 | 前台 bash | **0** | 10/10 |
| 5 | captain 修复版 | 后台作业 | **0** | 10/10 |
| 6 | captain 修复版 | 前台 bash | **0** | 10/10 |
| 7 | captain 修复版 | 前台 bash | **0** | 10/10 |

失败那次的诊断原样（`/tmp/t88/smoke-run2.log`）：

```json
{"window_focused": false, "window_visible": true, "window_minimized": false,
 "application_active": false, "application_hidden": false,
 "activation_attempts": 59, "ssh_session": false, "terminal_program": null}
```

失败率：本会话 1/7（14%），失败项恒为该焦点用例；其余 9 个用例 7/7 稳定通过，`page_errors=0`、
`unexpected_external_requests=0`、`provider_jobs=0`、`clipboard_restored=true` 在所有运行中均成立。

> 口径声明：本报告**只**声明 `desktop_smoke.cjs` 这一个套件。26 项 CJS 全量由 captain 在后台并行执行，
> **本文不声明 26/26**；以我这 7 次运行为准，该套件是 6/7，不是全绿。

## 4. 未能做的部分（如实记录）

- 无法把激活拒绝变成确定性复现：锁屏被排除（`IOConsoleLocked=No`），本机没有可切换的 headless/无 WindowServer
  会话，`setActivationPolicy('accessory')` 之类注入会改变被测对象（属于伪造，不采用）。因此只能给出
  「7 次里 1 次」这种概率证据，而不是 100% 复现脚本。
- 未尝试用 `open -a` 借助 LaunchServices 强抢激活：该路径会以不同 `userData` 拉起第二个实例并可能创建
  指向真实用户目录的服务，风险大于收益，已主动放弃。

## 5. 建议（验收口径 + 代码取舍）

1. **验收口径拆两条，不再混写**：
   - CI / 无交互会话：焦点用例标记「需可交互桌面会话复验」，**不计入通过率**，且不得改写成 skipped 冒充通过；
   - 有窗口的桌面会话：`desktop_smoke.cjs` 必须 10/10 且 exit 0（本次 7 次运行中 6 次达到）。
2. **代码取舍（captain 决策）**：重试不修复该失败模式，可选
   (a) 回退到单次激活、保留原断言 —— 最小 diff；或
   (b) 保留现在的写法但**只当作诊断增强**，不得对外表述为"已修复焦点失败"，因为 59 次重试已被实测证伪。
   两者都不允许动 `isFocused()` 断言。
3. **失败分流表**（用新增诊断）：

   | 诊断 | 含义 | 归属 |
   | --- | --- | --- |
   | `activation_attempts` 逼近上限 + `application_active=false` + `window_focused=false` | OS 拒绝激活 | 环境级（本报告这一例） |
   | `window_visible=false` 或 `window_minimized=true` | 窗口状态异常 | 装置问题，需查装置 |
   | `window_focused=true` 之后才 `RENDERER_CLIPBOARD_WRITE_REJECTED` | 渲染层剪贴板被拒 | 才轮到产品排查（本次从未出现） |
