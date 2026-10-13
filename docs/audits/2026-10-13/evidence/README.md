# 验证证据与复跑范围

审查日期：2026-10-13；交付记录更新至上海时间2026-10-14，目录沿用原审查日期。墨屿固定基线为 `62f1c66a7b16bb84f2940dc1af48e685aaf58818`。以下基线探针在独立临时数据库和新浏览器缓存中运行；当时未修改原产品代码、真实作品或用户数据库。生成文本来自离线逻辑或免费本机模拟，不含真实收费模型调用。

最新工程入口为[候选整理电脑端完整复验](compatibility-python311-node24-candidate-revision-final.json)，实际运行于上海时间2026-10-14 02:03:08至02:06:19：51个执行项、36份Python测试与55个真实电脑浏览器场景通过，66份源码前后相同，13份附件指纹核验，详细范围列在末尾。历史[电脑端交付复验](compatibility-python311-node24-desktop-release-final.json)于00:11:10至00:13:55通过50项/49场景、64份源码不变，仅证明当时版本；本次中间失败与新成功分别保留，旧通过不覆盖本次代码。

工程修复历史提交 `eaca3586ddd2d8210f1ed62b1d1e22cd2c383886` 已上传GitHub分支 `codex/full-function-audit`，[增强检查运行38434003558](github-actions-desktop-release-success.json)于上海时间2026-10-14 00:52:11至00:56:02完成并成功；详细范围见历史远程记录。该成功不覆盖本次候选整理；分支未合并main，工程通过不代表文学、第一梯队或公网部署通过。

## 墨屿直接验证

| 文件 | 实际范围 | 结果与限制 |
| --- | --- | --- |
| [runtime_probe.py](runtime_probe.py)、[runtime-results.json](runtime-results.json) | 原服务器、真实本机 HTTP、临时 SQLite；流式和配额使用本机模拟提供方；结束解析及字段类型使用原组件 | 复现证据漂移阻止保存、旧备份恢复冲突、决策后版本冲突、拒绝状态不兼容、空密钥脱敏污染、缓冲流、配额突破、保存锁阻塞及取消只丢结果。完整重跑退出 0，无 `probe_error`；探针正常执行不代表产品通过验收。 |
| [root_component_probe.py](root_component_probe.py)、[root-components.json](root-components.json) | 原 `ai_context` 和裁剪组件，受控输入；审稿与导出部分使用真实 HTTP 和临时数据库 | 实际材料包含未来记忆、未来时间线、反派独知状态；已选旧章缺中部原句，限额 0 仍返回 1 条记忆；15412 字符长章只提供尾部 12000；换正文后旧 `passed` 仍能放行。检索部分不是模型剧透率或真实小说整体召回评测。 |
| [browser_probe.cjs](browser_probe.cjs)、[root-browser.json](root-browser.json) | Playwright 控制真实 Chrome 和本机页面；真实保存/回读；生成回应在浏览器侧模拟，保存成功回应可受控延迟 | 复现保存版本 `[6,6]`、迟到候选进入另一章浏览器、重复采纳、拒绝记忆后显示初始作品，以及长章被 90 字符候选替换。错章/长章结果未进入数据库，不能称持久丢稿。修正等待异步采纳完成的时机后完整重跑退出 0，无 `probe_error`。 |
| [desktop-editor.png](desktop-editor.png)、[mobile-editor.png](mobile-editor.png) | 1440×1000 和 390×844 的原页面；另外在 1280×900 操作慢保存 | 无整页横向溢出及页面脚本异常；手机仍有按钮文字溢出、补充要求输入宽度仅 40 像素。截图不证明保存、生成或文学质量通过。 |
| [baseline-results.json](baseline-results.json) | 仓库 19 份现有 Python 测试脚本 | 全部退出 0。部分只检查源码字符串或使用手写预测，不能排除真实页面失败。文件也保留首次固定评估失败摘要。 |
| [fixed-evaluation.json](fixed-evaluation.json) | 首次固定 JSON 评估 | 部分执行项的子进程找不到 `python`，整体失败；结果保留，未改产品来隐藏环境问题。 |
| [fixed-evaluation-path-recheck.json](fixed-evaluation-path-recheck.json) | 将既有 Python 运行目录补入 PATH 后重跑同一评估 | 退出 0，`passed=true`。评测器对固定样本的成功不等于实际检索、真实长篇一致性或文学效果达到门槛。JSON 内的 measured 标签仍须结合样本来源理解。 |

接口错误的数字是服务器回应：400 表示拒绝这份输入，409 表示版本或对象编号冲突。关键失败已在[缺陷清单](../findings.md)给出触发条件、代码位置和修改后的通过标准。

## 上游组件证据

以下脚本归档自本轮缓存，保留固定上游源码、探针逻辑和结论范围；公开副本仅将本机路径换成约定缓存目录，原字节和变更清单见[归档说明](archive-privacy.md)。未启动七个上游整站，也未用真实模型评测其小说质量。

| 项目 | 脚本与结果 | 可证明的范围 |
| --- | --- | --- |
| NovelForge | [novelforge-component-probe.py](upstream-probes/novelforge-component-probe.py)、[结果](upstream-probes/novelforge-component-probe-result.json) | 原旧 scheduler 的内存顺序/取消及伏笔启发式；不能代表当前 WorkflowRuntime。 |
| NovelForge 独立复核 | [cross-storyforge-version-probe.mjs](upstream-probes/cross-storyforge-version-probe.mjs)、[结果](upstream-probes/cross-storyforge-version-probe-result.json) | 直接导入原版本服务，提供内存 localStorage；25 条只保留 20 条、作品/卡片隔离、清空及序列化恢复、损坏 JSON 处理。不是完整浏览器恢复或服务器修订。 |
| MuMuAINovel | [mumu_isolated_probe.py](upstream-probes/mumu_isolated_probe.py) | 原 TXT 解析、最新人物状态 helper、旧基稿局部应用；注册检查为静态源码。结果详见[专项](../projects/agent-mumu.md)与[交叉记录](../cross-review/cross-91writing-memory-mumu.md)，没有单独 JSON 结果文件。 |
| arboris-novel | [arboris-component-probe.py](upstream-probes/arboris-component-probe.py)、[结果](upstream-probes/arboris-component-probe-result.json) | AST 提取原函数，模拟 store/模型；护栏、入库参数、秘密/未来材料和删除方法断点。不是整站 HTTP 或数据库验收。 |
| 91Writing | [probe-91writing.cjs](upstream-probes/probe-91writing.cjs)、[结果](upstream-probes/probe-91writing-results.json) | 原函数在替身环境中执行；初始化空材料窗口、编辑器切章覆盖及配置问题。旧 BackupManager 不代表当前设置页，切章结果未证明持久误写。 |
| webnovel-writer v8 | [webnovel_runtime_probe_c.mjs](upstream-probes/webnovel_runtime_probe_c.mjs)、[结果](upstream-probes/webnovel-runtime-probe-c.json) | 导入原 TS 文件事务模块，真实子进程在半写后退出；恢复和外部编辑冲突隔离。还包括固定墨屿快照的前端函数模拟。不是宿主整站或正文生成耐久恢复。 |
| storyforge | [storyforge-isolated-probe.mjs](upstream-probes/storyforge-isolated-probe.mjs) | 原统计组件及墨屿原函数条件实验；输出见[专项报告](../projects/agent-storyforge.md)。不把直接起稿函数实验列为旧作品正常入口数据损失。 |

固定来源 JSON 在 [source-metadata](source-metadata/)，共 17 份：墨屿和七个项目的仓库/分支信息，加 webnovel 的 v8 分支。固定提交、分支区别和归档无 `.git` 的校验边界见[来源记录](../sources.md)。

## 运行环境与复跑

基线探针实际使用Python 3.12.14、Node 24.19.0、Chrome，以及Codex自带的Playwright。后续修复补验Python 3.11/Node 20与当前Python 3.11/Node 24组合；GitHub基线 `62f1c66` 的旧版成功只覆盖其旧配置。当前增强配置的独立远程结果见[运行38434003558](github-actions-desktop-release-success.json)，它只对应工程提交 `eaca358`，不能声称覆盖所有系统与运行版本。当前产品验收以电脑端为范围，手机记录保留为历史补充。

以下为历史基线探针的维护记录，用户不需要执行。脚本分析当时基线，不能直接用于断言当前产品失败或通过。复验应先准备固定基线的隔离副本，输出使用新的目录和文件名；不要覆盖本目录已有JSON或截图。当前源码的检查入口在仓库[使用说明](../../../../README.md)中。

```sh
python3 docs/audits/2026-10-13/evidence/runtime_probe.py

python3 docs/audits/2026-10-13/evidence/root_component_probe.py --root .

MOYU_AUDIT_PYTHON=python3 \
MOYU_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
MOYU_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
node docs/audits/2026-10-13/evidence/browser_probe.cjs .

python3 scripts/evaluation_runner.py \
  --fixtures-dir tests/fixtures --output /tmp/moyu-fixed-evaluation-recheck.json --pretty
```

墨屿探针自行建立临时数据库、独立端口和浏览器缓存，并结束所建服务。上游脚本依赖 `./.audit-cache/2026-10-13/references/` 的固定源码；部分另读 `./.audit-cache/moyu-baseline/`。缓存不提交，失效时需按[固定来源](../sources.md)重新取得相同提交，再恢复路径。不能声称本目录独立包含了完整上游运行环境。个别上游脚本只输出标准输出，或仍将结果写到原缓存路径，需按脚本实际行为归档新结果。

上述固定基线探针没有验证真实付费请求、供应商实际取消/计费、真实向量库质量、跨设备恢复、生产部署或文学盲评。这些限制已纳入[后续路线和验收标准](../roadmap.md)。

## 修复后的本机兼容复验

复验日期为2026-10-13，运行环境为macOS arm64、Python 3.11.17、Node 20.20.2、Playwright 1.62.1、Chrome 155.0.8059.40。只使用临时数据库、新浏览器缓存和免费本机模拟提供方；没有读取真实凭据、增加收费请求或修改真实作品。这是历史Node 20本机证据；当时增强Node 24远程检查尚未运行，2026-10-14的新提交远程结果另列本文末尾。

| 文件 | 实际范围 | 结果与限制 |
| --- | --- | --- |
| [compatibility-python311-node20-post-summary-nonbrowser.json](compatibility-python311-node20-post-summary-nonbrowser.json) | 全部应用与测试语法、29 份 Python 测试脚本、固定评测、JavaScript 语法与编辑合并检查 | 37 个执行项全部通过。包含提供方协议 31 项、队列集成 25 项、文学评测工具 29 项和章节分析 13 项。保存了源码指纹，运行期间没有源码变化。 |
| [context-http-summary-invalidation-final.json](context-http-summary-invalidation-final.json) | 实际本机 HTTP、临时数据库和模拟模型；另建正常作品验证摘要失效 | 只改正文、全量保存携带旧摘要、同次作者另写交接三种情况全部通过；返回与重新读取的数据一致。检索分数仍只代表固定合成样本，不是小说质量。 |
| [compatibility-python311-node20-browser-final.json](compatibility-python311-node20-browser-final.json) | 1440×1000 桌面、真实 Chrome、完整本机保存和模型通信流程；另有390×844手机历史补充 | 20 个页面场景全部通过，其中一个场景实际执行 20 轮写作、切章、生成和单次采纳。覆盖迟保存期间继续改任务卡、断网恢复、选区润色、旧摘要失效与同页修改不复活、审稿、作品隔离、卷章规划、用途模型、旧书导入、参考书阅读搜索、下载备份及恢复副本。手机结果不列为当前发布门槛。 |
| [compatibility-browser-final-source-recheck.json](compatibility-browser-final-source-recheck.json) | 比较非浏览器复验后的源码指纹，再检查最终浏览器脚本语法 | 产品源码及 Python 测试未变化；唯一变化是浏览器脚本等待时机与交互断言，最终脚本已通过语法检查及上述完整浏览器运行。 |
| [repaired-desktop-editor.png](repaired-desktop-editor.png)、[repaired-mobile-editor.png](repaired-mobile-editor.png) | 完整页面截图 | 桌面显示已恢复的独立作品，手机显示实际编辑器。截图经过查看，未见文字溢出；功能结论依据上面的操作和数据回读。 |

修复了章节保存顺序导致的旧自动摘要回写，以及页面输入框残留旧任务卡文字导致的再次写回。后端在同一事务内先写入章节资料，再使旧分析失效；作者同次新写的摘要或交接仍保留。页面只同步合并后的任务卡，单个输入事件只修改它对应的字段。

历史失败记录全部保留：`browser-timeout`、`browser-sequence-failure`、`browser-summary-failure`、`browser-adoption-wait-failure`、`browser-blueprint-wait-failure` 和 `browser-reference-collapsed-failure`。后四项分别记录真实摘要缺陷、采纳异步等待、建立章节完成时机和参考章节未展开的断言问题；没有删除失败证据或降低原稿保护断言。公开副本仅脱敏路径和非必要个人邮箱，字段与原件校验范围见[归档说明](archive-privacy.md)。

这些通过结果证明本机流程和协议兼容，不证明真实供应商的最终费用、线上部署、跨设备恢复或文学效果达到第一梯队。文学诊断账本、冻结请求及未知费用必须另行保留和核对。

分阶段规划回归入口为[SQLite工作流](../../../../tests/planning_workflow_fixtures.py)、[真实本机HTTP](../../../../tests/planning_http_fixtures.py)和[规划备份恢复](../../../../tests/planning_backup_fixtures.py)，已在下文历史电脑端完整快照中执行。恢复副本中的历史规划只供阅读；要调用模型需在副本中重新创建规划，历史候选不会自动采纳或重发。

## 电脑端 Node 24 历史快照

本轮后续在macOS arm64、Python 3.11.17、Node 24.19.0上复跑全部检查，只使用隔离数据库和免费本机提供方。此处历史电脑端快照为50个执行项、35份Python测试脚本、41个浏览器场景，全部通过，前后源码指纹相同。它只证明当时范围；用量、撤销重做和独立故事门等后续修复已另有末尾 `desktop-release-final` 快照，不能由本节推定远程检查或文学效果已通过。

该快照保留总报告、四份结果和八张截图的原始字节，总报告 SHA-256 为 `223e5e6233f117d234d5d627376ba94de44200cca30613e28a80d59be7db9980`。报告中的附件路径仍是运行时的 `*-current` 名称；复查这轮原件时将 JSON 的 `-current` 替换为 `-desktop-flow-baseline`、截图的 `node24-current-` 替换为 `node24-desktop-flow-baseline-`。对应12项附件均已逐一核对指纹；不会修改历史报告中的路径来假装它是新运行。

| 文件 | 实际范围 | 结果与限制 |
| --- | --- | --- |
| [compatibility-python311-node24-planning-baseline.json](compatibility-python311-node24-planning-baseline.json)、[browser-python311-node24-planning-baseline.json](browser-python311-node24-planning-baseline.json) | 较早的46项检查及29个基础浏览器场景 | 当时全部通过，原字节保留；不包含新增规划保稿5场景和五章作者7场景。 |
| [compatibility-python311-node24-path-failure.json](compatibility-python311-node24-path-failure.json)、[fixed-evaluation-python311-node24-path-failure.json](fixed-evaluation-python311-node24-path-failure.json) | 首次50项完整执行 | 固定评测的内嵌执行命令找不到`python`，其余49项通过。检查驱动将已指定Python目录加入子进程路径后原样完整复跑；没有修改固定题或门槛。 |
| [compatibility-python311-node24-desktop-flow-baseline.json](compatibility-python311-node24-desktop-flow-baseline.json) | 50项完整检查；35份Python回归、语法、固定评测、编辑器合并及3项浏览器检查 | 全部通过，运行期间源码未变化。保留源码与各结果/截图指纹；运行后的新增修复需新快照。 |
| [browser-python311-node24-desktop-flow-baseline.json](browser-python311-node24-desktop-flow-baseline.json) | 29个基础浏览器场景，含连续20轮写作 | 真实保存/回读、候选、章后记忆、语义审稿与局部修订、输出阻断、四阶段规划中断/失败重做、跨作品迟到保护、模型设置、旧书与完整备份恢复通过。本机模型不证明文学质量。 |
| [planning-browser-python311-node24-desktop-flow-baseline.json](planning-browser-python311-node24-desktop-flow-baseline.json) | 规划保稿5场景 | 同框连续自动保存、采用等待期间切页返回仍锁定、保存失败刷新恢复本地草稿、两窗口冲突保留双方并选择、第二轮规划建立章节保留旧章全部字段通过。规划阶段使用本机提供方，部分初始化与生成使用真实本机API；关键保稿交互通过页面执行。 |
| [desktop-author-flow-python311-node24-desktop-flow-baseline.json](desktop-author-flow-python311-node24-desktop-flow-baseline.json) | 7个五章作者场景；1440×1000及1280×900电脑窗口 | 所有作者写入都通过页面。新建作品、五章普通正文、逐章章后记忆确认和审稿归档；第1/3/5章埋/推进/回收来源定位；删除回收原句阻断界面及真实MD导出；恢复文字不自动放行，显式选择旧链接重锚保留原ID；完稿持久且不创建下一章；真实下载MD/EPUB逐字核对五章标题/正文和原稿指纹，另查EPUB阅读顺序与可读正文。空白第六章不进入导出，默认作品未变。 |
| [规划截图](node24-desktop-flow-baseline-planning-second-round.png)、[五章截图](node24-desktop-flow-baseline-desktop-five-chapter-author.png)、[1280电脑截图](node24-desktop-flow-baseline-compact-desktop-five-chapter-author.png)、[回收失效阻断](node24-desktop-flow-baseline-desktop-stale-payoff-blocked.png)、[完稿状态](node24-desktop-flow-baseline-desktop-final-complete.png) | 真浏览器页面截图 | 已查看布局；功能判断仍依据操作、原稿逐字对照和实际数据回读。 |

伏笔修复保留历史链接身份，作者在“来源记录”选择原记录重新确认；界面终稿条件改为读取服务端实际导出清单，完稿状态保存完成后才能显示成功。只把卡片标为回收而没有有效回收原句，也会被阻断。

五章测试开发时的失败仍保留：[状态字段断言](desktop-author-flow-fixture-status-failure.json)、[导出预检等待](desktop-author-flow-preflight-wait-failure.json)和[完稿等待](desktop-author-flow-completion-wait-failure.json)。前者误要求API必须返回`stale:false`字段；后两者分别误等待已被页面预检阻止的导出请求，以及在异步完稿保存结束前读取状态。修正为核对有效审稿版本、实际阻断窗口加直接只读导出409、等待完稿窗口消失后回读。没有通过修改正文、API写审稿状态或删除旧失效链接绕过流程。

该历史快照中，三项浏览器入口已接入[自动检查配置](../../../../.github/workflows/ci.yml)，但当时本机成功没有远程执行结论；历史 `62f1c66` 远程成功只覆盖其旧配置。历史工程提交 `eaca358` 的增强检查成功另有[独立运行记录](github-actions-desktop-release-success.json)，不能回写成旧快照当时已远程通过，也不覆盖新增候选整理。

## 用量与候选修复历史快照

2026-10-13 22:26:59 至22:29:08（上海时间）完整重跑50个执行项，全部通过。环境为Python 3.11.17、Node 24.19.0、macOS arm64和真实Chrome；35份Python测试、8项JavaScript语法、固定评测、编辑器合并与三组浏览器检查共同执行。64份源码在运行前后指纹相同，并在运行后再次逐文件核对；12份附件也与总报告登记的指纹一致。

| 文件 | 实际范围 | 结果与限制 |
| --- | --- | --- |
| [完整报告](compatibility-python311-node24-usage-ui-final.json) | 当时冻结源码50项完整检查 | 全部通过，`source_unchanged=true`；提供方协议45项、模型队列49项、文学工具54项均在该历史运行中通过。可信总数/分量推导/部分或矛盾计数、流式计数倒退、未知费用、预算下界、旧账本和最低评审/题材门均有回归；不证明后续修复、实际文学合格或费用已核实。 |
| [基础浏览器](browser-python311-node24-usage-ui-final.json) | 29个真实页面场景，另含连续20轮写作 | 真实本机保存、原稿保护、普通候选可编辑与比较采纳、记忆/审稿、四阶段规划、作品隔离、模型设置、导入及完整备份恢复全部通过。 |
| [规划保稿](planning-browser-python311-node24-usage-ui-final.json) | 5个规划场景 | 连续自动保存、采用跨页面重绘锁、保存失败刷新恢复、两窗口冲突保留双方及作者选择、第二轮建章保留旧章全部字段通过。 |
| [五章作者链](desktop-author-flow-python311-node24-usage-ui-final.json) | 7个五章页面场景及10次分析候选检查 | 74次作者写请求全部来自可见控件；章后记忆和故事审稿显示真实任务名、只读非JSON摘要及专用查看按钮，即使下一次生成的下拉框为续写也进入正确窗口；没有正文采纳请求，五章原文逐字不变。伏笔失效阻断、保留旧ID重新确认、完稿持久及真实MD/EPUB下载仍通过。 |
| [电脑编辑器](node24-usage-ui-final-desktop-editor.png)、[1280五章编辑器](node24-usage-ui-final-compact-desktop-five-chapter-author.png)、[二轮规划](node24-usage-ui-final-planning-second-round.png)、[失效阻断](node24-usage-ui-final-desktop-stale-payoff-blocked.png)、[完稿状态](node24-usage-ui-final-desktop-final-complete.png) | 真实浏览器截图 | 截图已查看；任务摘要、按钮、正文及规划布局未见溢出，功能结论依照实际操作与数据回读。 |

这轮总报告SHA-256为 `50e4770cfb10ebeed2062c4c2913b1688068d85ae9f04a1325776e37ab43cdca`。总报告与四份JSON结果、八张PNG按原始字节另存 `usage-ui-final`，保留此前`desktop-flow-baseline`。总报告内附件仍使用运行时 `*-current` 路径；复查本轮原件时将JSON的 `-current` 替换为 `-usage-ui-final`、PNG的 `node24-current-` 替换为 `node24-usage-ui-final-`。

该历史快照不用真实密钥、不调用收费提供方、不修改用户真实作品，只证明当时的电脑端本机单人流程与指定模拟样本。原件保持不变，当前工程结论见下节，真实文学结论见[评测说明](../../../evaluations/README.md)。

## 电脑端交付完整快照

上海时间2026-10-14 00:11:10至00:13:55（报告UTC为 `2026-10-13T16:11:10.843Z` 至 `2026-10-13T16:13:55.087Z`）完整执行50个检查项，全部通过。环境为macOS arm64、Python 3.11.17、Node 24.19.0和真实Chrome；35份Python测试、8项JavaScript语法、固定评测、12项编辑器合并及三组浏览器检查共同执行。提供方协议45项、队列54项、文学工具62项、输出检查34项通过，37个基础、5个规划、7个五章场景合计49个真实浏览器场景通过；连续20轮写作和五章10次专用候选检查为实际执行内容，不能再算作49之外的独立场景。

| 文件 | 实际范围 | 结果与限制 |
| --- | --- | --- |
| [完整报告](compatibility-python311-node24-desktop-release-final.json) | 当时冻结源码50项完整检查 | `passed=true`、`source_unchanged=true`，64份源码运行前后指纹相同。修复每日额度读取，流式首片检查采用同步事件确认先收到首片再允许模型结束；指定离线/模拟范围通过，不代表本次整理改动、真实供应商费用或文学质量。 |
| [固定评测](fixed-evaluation-python311-node24-desktop-release-final.json) | 固定JSON样本与预先门槛 | 全部通过；结果仍须结合固定样本来源，不能当作真实小说文学成绩。 |
| [基础浏览器](browser-python311-node24-desktop-release-final.json) | 37个真实页面场景、连续20轮写作；1440×1000与1280×800电脑窗口 | 保稿、采纳、局部选区、记忆/审稿、规划、作品隔离、用量设置、旧书及完整恢复通过。新增三方撤销/重做保留另一窗口文字、同起点插入/删改双向冲突、格式快捷键、远端覆盖撤销位置时拒绝、作用域历史隔离、真实中文组合输入保稿及迟到合并切章保护。组合输入覆盖指定Chrome事件，不代表所有系统输入法。 |
| [规划保稿](planning-browser-python311-node24-desktop-release-final.json) | 5个规划场景 | 连续保存、采用等待期间跨页锁定、保存失败刷新恢复、两窗口双方选择和第二轮建章保留原章全部字段通过。 |
| [五章作者链](desktop-author-flow-python311-node24-desktop-release-final.json) | 7个五章页面场景；1440×1000和1280×900电脑窗口；10次专用候选检查 | 作者写请求来自页面可见控件，五章原文逐字不变；记忆/审稿显示只读摘要和专用入口，不写成正文。伏笔失效阻断、保留原ID重新确认、完稿刷新持久及真实MD/EPUB下载核对通过。 |
| [电脑编辑器](node24-desktop-release-final-desktop-editor.png)、[紧凑电脑编辑器](node24-desktop-release-final-compact-desktop-editor.png)、[电脑规划](node24-desktop-release-final-desktop-planning.png)、[第二轮规划](node24-desktop-release-final-planning-second-round.png) | 前两组浏览器检查的四张截图 | 页面截图与运行报告一同保留；功能结论依据操作与数据回读。 |
| [五章电脑编辑器](node24-desktop-release-final-desktop-five-chapter-author.png)、[1280五章编辑器](node24-desktop-release-final-compact-desktop-five-chapter-author.png)、[回收失效阻断](node24-desktop-release-final-desktop-stale-payoff-blocked.png)、[完稿状态](node24-desktop-release-final-desktop-final-complete.png) | 五章作者链的四张截图 | 与真实下载、原稿指纹和来源身份核对共同归档；截图本身不能证明文学质量。 |

总报告SHA-256为 `a849a71c25f33424505e7e9bafe440efe083479493e60f884b76d755bf815df1`。报告的12项附件登记三份浏览器JSON、固定评测JSON和八张PNG，均为真实 `desktop-release-final` 文件路径，可直接按记录读取和核对指纹，无须替换 `-current` 名称。此前 `desktop-flow-baseline`、`usage-ui-final` 及失败记录保留原字节，其历史路径映射只适用于对应旧报告。

未来复查可以先只读比较报告的 `source_files_sha256_after` 与当前文件，再核对 `artifacts` 的每个附件指纹；只读一致性核查不是重新运行测试。源码或检查条件改变时应使用新结果文件，保留本次快照，不覆盖历史证据。

该历史工程复验只使用临时数据库、独立浏览器状态和免费本机提供方，未增加收费请求、未使用真实作品。对应工程提交的增强GitHub成功证据另列下节，文件名中的 `release` 本身不代表发布结论。真实[第十二轮](../literary-pilot-v12/report.md)首对942/868字均超600至850字、输出0/6、有效评审0/12、题材3/1/0/0；截至该轮，含独立协议诊断累计31条请求预留、49642个可信完整tokens与已知下界，10条缺完整用量、实际金额未知。原始请求、账本和候选保持不变；新增整理诊断与当前累计另列末尾。

## 历史GitHub增强检查与本机启动

[公开远程成功记录](github-actions-desktop-release-success.json)绑定工程修复提交 `eaca3586ddd2d8210f1ed62b1d1e22cd2c383886`、分支 `codex/full-function-audit`、运行 `38434003558`。分支内容与该本机提交已核对一致；运行和 `checks` 作业状态均为 `completed`、结论均为 `success`，等待执行完成的命令退出0。

作业于上海时间2026-10-14 00:52:11至00:56:02执行（UTC为 `2026-10-13T16:52:11Z` 至 `2026-10-13T16:56:02Z`），语法、全部Python契约测试、固定评测、12项编辑器合并、37个基础浏览器、5个规划和7个五章作者场景均通过。这是在Linux上的独立远程工程运行，提供方仍为免费本机模拟。运行环境弃用提示不是检查失败，也不等于文学或供应商费用验收。

本机交付另外实际启动服务，`/api/health` 返回 `ok=true`、数据库版本11，真实电脑编辑页面正常打开。电脑入口为 `http://127.0.0.1:4173`，作品保存于被Git忽略的本机 `moyu.sqlite3`，连接凭据保存于本机 `~/.moyu/credentials/` 私有文件；数据库、凭据和本机页面状态均不随源码上传。此启动检查不额外发送模型请求。

远程成功只属于上述工程提交，不覆盖本次候选整理代码，后续文档或源码提交需分别记录对应检查；本分支尚未合并main，尚无公网部署验收，不能据工程检查宣称第一梯队或文学达标。

## 原候选整理与真实失败

本次实现保留父候选全文，沿用冻结模型、参数、输出预算、字数和稿件范围；单父单子、独立用量、重复请求幂等，只有作者明确采纳才写正文。失联先只读核对已有任务，执行或费用未知不重发，恢复结果不自动采纳。备份恢复与独立作品副本保留历史候选、父子关系、用量和原请求指纹；历史候选只读，不能继续采纳、整理、重试或自动收费。

纯请求组装与公开关联由[候选整理模块](../../../../moyu_revision.py)实现；`public_binding` 仅公开 `operation`、`parent_run_id`、`root_run_id`、`parent_characters`，不公开全文、整理备注或 `request_id`。回归入口为[候选整理保护](../../../../tests/candidate_revision_fixtures.py)、[正文HTTP流程](../../../../tests/prose_output_http_fixtures.py)和[备份恢复](../../../../tests/import_archive_lifecycle_fixtures.py)，已在下节新完整快照中执行；结论仅限指定样本和实际免费模拟流程。

[真实失败诊断](../literary-revision-v1/diagnosis.md)只读核对双方父稿、冻结请求、原返回和账本：第十二轮基线942字、墨屿868字完整保留；同一故事双方各追加一次整理，均为 `glm-5.1`、`stop`、完整协议，但两份正文都是“铁”1字，整理输出合格0/2。用量分别4740和4747、合计9487 tokens；当前累计33条请求预留、59129个可信完整tokens及已知下界，仍有10条缺完整用量，实际金额未知。未修复首稿、未新增独立故事、未发送盲评，文学质量、事实安全、作者效率与第一梯队均未证明。

## 候选整理中间检查失败

[中间完整检查](compatibility-python311-node24-candidate-revision-release.json)实际运行于上海时间2026-10-14 01:44:03至01:45:48（UTC为 `2026-10-13T17:44:03.104Z` 至 `2026-10-13T17:45:48.360Z`），51项中48项通过、3项退出1，整体 `passed=false`、`source_unchanged=false`。运行期间 `server.py` 和 `tests/frontend_browser_regressions.cjs` 前后指纹变化，不能作为冻结源码完整通过证据。

失败项为基础浏览器、规划浏览器和五章作者链，三项都报 `browserType.launch: Executable doesn't exist`，找不到Playwright缓存中的浏览器可执行文件；场景为空，没有完成真实页面验收。JSON不能单独证明浏览器配置的最终根因，也不能据空场景宣称业务断言通过或失败。

[固定评测中间结果](fixed-evaluation-python311-node24-candidate-revision-release.json)与原完整报告分别保留；没有修改旧报告、原日志或截图来掩盖这次失败。随后配置真实浏览器并冻结源码，独立完成下节 `candidate-revision-final` 新验证；新成功不回写或删除旧失败。

## 最新候选整理电脑端完整快照

上海时间2026-10-14 02:03:08至02:06:19（报告UTC为 `2026-10-13T18:03:08.348Z` 至 `2026-10-13T18:06:19.784Z`）完整执行51项并通过。环境为macOS arm64、Python 3.11.17、Node 24.19.0与真实Chrome；36份Python测试、8项JavaScript语法、固定评测、12项编辑器检查和三组浏览器运行共同完成。候选整理保护19项、正文HTTP15项实际通过；43个基础、5个规划、7个五章场景合计55个真实电脑浏览器场景，包含20轮连续写作和五章10次专用候选检查，后两项不能再算独立场景。

| 文件 | 实际范围 | 结果与限制 |
| --- | --- | --- |
| [完整报告](compatibility-python311-node24-candidate-revision-final.json) | 本次冻结源码51项完整检查 | `passed=true`、`source_unchanged=true`，66份源码前后指纹相同；使用临时数据库与免费本机模拟提供方，不读取真实密钥、不增加收费请求。 |
| [固定评测](fixed-evaluation-python311-node24-candidate-revision-final.json) | 固定JSON样本与预先门槛 | 实际执行通过，不作为真实小说或文学质量成绩。 |
| [基础浏览器](browser-python311-node24-candidate-revision-final.json) | 43个真实电脑页面场景、20轮连续写作 | 整理父稿保留、双击一次调用、合限子候选等待采纳、手改父稿拒绝、原稿变化阻断、回应丢失后只读恢复与身份保留通过；缓存存在仍读取固定任务、迟到流不覆盖已编辑候选、复制副本历史只读也通过。其他保存、选区、记忆、审稿、作品隔离和用量场景继续通过。 |
| [规划保稿](planning-browser-python311-node24-candidate-revision-final.json) | 5个真实规划场景 | 连续保存、采用等待跨页锁定、保存失败刷新恢复、两窗口冲突选择、第二轮建章保留原章字段通过。 |
| [五章作者链](desktop-author-flow-python311-node24-candidate-revision-final.json) | 7个五章电脑场景、10次专用候选检查 | 普通正文的记忆确认、审稿、归档、伏笔回收失效和重锚、完稿刷新及真实MD/EPUB下载核对通过；作者写入经可见控件，五章正文逐字保护。 |
| [电脑编辑器](node24-candidate-revision-final-desktop-editor.png)、[紧凑电脑](node24-candidate-revision-final-compact-desktop-editor.png)、[电脑规划](node24-candidate-revision-final-desktop-planning.png)、[整理子候选](node24-candidate-revision-final-desktop-revised-candidate.png)、[第二轮规划](node24-candidate-revision-final-planning-second-round.png) | 基础与规划运行的5张截图 | 按原路径登记；功能结论依据操作和数据回读，截图不证明事实或文学质量。 |
| [五章电脑](node24-candidate-revision-final-desktop-five-chapter-author.png)、[1280五章](node24-candidate-revision-final-compact-desktop-five-chapter-author.png)、[回收失效阻断](node24-candidate-revision-final-desktop-stale-payoff-blocked.png)、[完稿状态](node24-candidate-revision-final-desktop-final-complete.png) | 五章运行的4张截图 | 与下载、正文、原稿指纹和来源身份核对一同归档。 |

总报告SHA-256为 `62bbf70fa6ae53b244ee730b7bca0947727de1ac4a135b72bbce5b3ccf27ab14`；4份JSON与9张PNG共13份附件均在报告中登记真实 `candidate-revision-final` 路径，逐个指纹核验通过，无需改名映射。66份源码前后指纹一致不包括本次结果后的文档变更；后续工程源码变化仍需新快照。

这份最新本机完整复验覆盖本次候选整理的工程流程与机械字数门。真实整理仍是两份1字失败；文学质量、事实安全、作者效率与第一梯队均未证明。对应源码上传与远程检查尚未登记，历史 `eaca358`/`38434003558` 成功不覆盖本次代码；分支未合并main，公网部署未验收。

## 回复模式诊断记录

[回复模式诊断](../literary-transport-v1/report.md)在相同冻结墨屿整理输入上先执行一次不分段请求。返回200、`glm-5.1`、`stop`、完整用量4747 tokens，但正文仍只有1字“铁”，同时观察到非空 `reasoning_content`；因此第二次流式请求按停止条件未执行。该真实请求新增一条，累计34条预留、63876个可信完整tokens，10条缺完整用量，费用未知。它是接口行为诊断，不是文学质量或第一梯队证据。
