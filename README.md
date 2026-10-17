# 墨屿 · 小说创作台

一个本地优先的中文长篇小说创作工具。作品、正文版本、设定、记忆证据和任务通过 SQLite（保存在本机的数据库文件）保存，作者在页面中完成规划、写作、局部改稿、记忆确认、备份和导出。

## 桌面软件

桌面版包含创作窗口、常驻写作总管和本机服务，安装后无需另外准备 Python、Node.js 或终端。已配置 Mac Apple 芯片、Mac Intel 和 Windows 64 位构建流程；各平台验证与下载状态见[桌面安装说明](docs/desktop-packaging.md)。

Mac 打开 DMG，将“墨屿”拖入“应用程序”；Windows 打开 EXE，按安装窗口完成。模型地址和密钥在软件设置中填写，安装包不携带开发电脑的作品或密钥。现有浏览器版作品请先从“备份中心”导出完整 ZIP，再在桌面版“备份中心”导入恢复。

桌面作品独立保存在 Mac 的 `~/Library/Application Support/Moyu/` 或 Windows 的 `%APPDATA%\Moyu\`；菜单“帮助 → 打开作品数据文件夹”可直接找到。升级程序时保留这个目录，换电脑前先导出完整作品备份。本版没有发行者证书，Mac 未经 Apple 公证，系统可能显示开发者或发布者提示。

## 当前状态

2026-10-17 的模型库与桌面版按“填写地址、密钥、模型名称 → 测试通过 → 保存模型 → 分配用途”执行。修改连接后必须重新测试，失效的原用途分配保留并提示，保存失败不切换模型。[本轮验收说明](docs/audits/2026-10-14/comprehensive/desktop-model-library-20261017.md)及[完整整轮03](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-desktop-model-library-20261017-03.json)记录136项检查、20组电脑浏览器269个场景全部通过，163份源码前后相同，116份附件指纹匹配。本机 Mac arm64 内置服务15项、真实应用10项、DMG安装后10项、包内模型库8项和桌面运行边界5项均通过；[打包本机证据](docs/audits/2026-10-14/comprehensive/evidence/desktop-packaging-local-20261017.json)与实际软件截图公开保留，均为合成作品和免费本机调用。Intel Mac、Windows及GitHub发布状态须由对应提交的原生运行另行确认；工程成绩不证明文学第一梯队。

首次提交 `05fa49c` 的GitHub产品检查全部通过，但[三平台桌面运行](docs/audits/2026-10-14/comprehensive/evidence/github-desktop-05fa49c-failure-20261017.json)发现启动检查过早、空剪贴板条目和Windows数据库连接未关闭问题，未上传安装包或发布。随后修正测试等待与剪贴板保留，并[关闭备份临时数据库连接](docs/audits/2026-10-14/comprehensive/evidence/desktop-sqlite-closure-20261017.json)，成功与异常路径均有修前失败、修后通过的检查。[重新构建后的本机验收](docs/audits/2026-10-14/comprehensive/evidence/desktop-native-fix-local-20261017.json)通过后台16项、真实软件10项、DMG安装后10项及运行边界5项，163份源码前后不变。该窄修有独立定向验证，不把之前整轮源码指纹当作修后源码。

写作总管的续写、整章修订、章节检查和全书规划建议，确认后留在原聊天卡片，显示来源章、任务状态和“模型调用 已用 / 上限”。作者点击“启动本次任务”才授权执行；确认计划和核对进度不调用这次写作模型，聊天回复另计。需要比较候选、决定章后资料或处理规划时，点击“查看本次结果”打开对应原章和准确任务。流程与限制见[聊天原卡管理写作任务](docs/audits/2026-10-14/comprehensive/chat-inline-tasks-20261016.md)。

此前聊天原卡版本的真实服务作者流程5项、电脑聊天交互15项、章间导航7项和完整前端定向53项分别通过，均未新增收费调用。[完整整轮02](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-agent-chat-inline-release-20261016-02.json)执行130项，128项通过、2项失败，原失败保留；修正后的[完整整轮03](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-agent-chat-inline-release-20261016-03.json)于上海2026-10-16 23:45:59至23:59:24执行完，130项全部通过、20套电脑浏览器257个场景，146份源码前后不变，附件116份（31份JSON、85张合成截图）。[独立复核](docs/audits/2026-10-14/comprehensive/evidence/agent-chat-inline-release-20261016-03-independent-review.json)通过，文档于10月17日整合；该版[静态资源核对](docs/audits/2026-10-14/comprehensive/evidence/local-chat-inline-preview-agent-chat-inline-release-20261016-03.json)与[两尺寸只读预览](docs/audits/2026-10-14/comprehensive/evidence/local-chat-inline-browser-agent-chat-inline-release-20261016-03.json)已通过。该次预览的两个模型连接均未配置，没有任务卡，也未确认作者原有标签已加载新版。这是此前预览状态，本次桌面版与模型库按上方独立证据验收。

章后资料确认已接入写作总管：正文采纳并提取资料后，总管等待作者逐项决定，再以已确认资料接续审稿。支持分次保存、全部不采用、空分析明确确认、进程中断恢复和重复确认去重；按任务保存的分析编号打开结果。正文、规划和正式事实仍由作者决定，检查最多两次、续写或整章修订最多三次模型调用。[章后确认记录](docs/audits/2026-10-14/comprehensive/agent-memory-confirmation.md)说明流程和保护边界。

章后确认的[02完整验收](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-memory-confirmation-release-20261016-02.json)于上海2026-10-16 22:05:28至22:18:23通过130个执行项、76份Python专项和20套电脑浏览器251个场景，146份源码前后不变；[独立复核](docs/audits/2026-10-14/comprehensive/evidence/memory-confirmation-release-02-independent-review-20261016.json)核对31份JSON与83张合成数据截图共114份附件。上一提交 `54a3bdd` 的[GitHub失败记录](docs/audits/2026-10-14/comprehensive/evidence/github-memory-confirmation-54a3bdd-failure-20261016.json)保留；修正仅涉及切换作品测试的完整等待及旧请求成功、报错和断连三种迟到检查，没有修改产品代码或增加收费调用。修正提交 `5e18f65` 的[GitHub运行38916906449](docs/audits/2026-10-14/comprehensive/evidence/github-memory-confirmation-5e18f65-success-20261016.json)于上海2026-10-16 22:53:16完成，29个必需步骤和34个实际步骤全部成功。此历史成绩不代替后续聊天原卡版本的独立验收，也不证明文学第一梯队。

此前连续写作已接入主聊天：总管可提出“完成本章，打开下一章”，作者确认后检查记忆、审稿和任务卡，在同一次保存中归档本章并打开后续章；已有正文与规划保留，目录末尾只创建一个空章。“承接上章，准备本章”可带入上章交接，作者编辑确认后保存本章任务。旧聊天结果能准确打开原任务，中断确认后可找回已建立的任务。[连续写作记录](docs/audits/2026-10-14/comprehensive/chapter-flow-agent.md)说明操作与边界。其[01完整验收](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-chapter-flow-release-20261016-01.json)通过129个执行项和248个电脑场景，[独立复核](docs/audits/2026-10-14/comprehensive/evidence/chapter-flow-release-independent-review-20261016.json)与[五张截图目视](docs/audits/2026-10-14/comprehensive/chapter-flow-visual-review-20261016.md)保留为历史证据。

2026-10-16 持续对话版已接通：右侧“写作总管”在切换创作、资料和设置页面时持续保留，可带着当前章或最多三处原文讨论；确认建议后接入真实续写、修订、规划、章后记忆与审稿，结果回到原章比较与处理。连接集中到“模型与风格”，可沿用主连接或独立设置聊天连接；聊天历史随完整作品备份保存，恢复副本中的旧对话只读。[NovelForge流程与界面研究](docs/research/novelforge-writing-flow-and-interface-2026-10-16.md)明确研究与实现范围，完整开书到全书完稿的自动编排尚未完成。

此前持续对话版的[04完整整轮](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-agent-chat-release-20261016-04.json)通过124个执行项，19套电脑浏览器241个场景，139份源码前后不变，100份附件指纹逐份匹配；[独立复核](docs/audits/2026-10-14/comprehensive/evidence/agent-chat-release-independent-review-20261016.json)通过，[12张最终截图逐张目视](docs/audits/2026-10-14/comprehensive/persistent-agent-chat-visual-review-20261016.md)未发现可见区域影响主要操作的新增遮挡或栏间重叠，并保留截图未展示部分的边界。[历史专项](docs/audits/2026-10-14/comprehensive/persistent-agent-chat.md)保留01失败、02覆盖缺口和03超时及当时状态。这些历史结果不代替本批章节流程验收，也不证明文学质量或第一梯队。

此前的[资料关联、全书待办与阅读工作区](docs/audits/2026-10-14/comprehensive/knowledge-inbox-workspace.md)可从人物或物品资料查看关联、回链与原句来源；按四类事项查看当前作品和分支的全书待办，并精确打开旧任务；正文支持本机字号、行距和宽度偏好，以及按作品、分支和章节恢复阅读位置。该历史版本的[02完整验收](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-knowledge-inbox-release-20261016-02.json)已通过114个执行项、16套浏览器226个电脑场景，128份源码前后不变；[独立复核](docs/audits/2026-10-14/comprehensive/evidence/knowledge-inbox-release-independent-review-20261016.json)核对86份附件、原日志与当时源码一致，[九张最终截图目视检查](docs/audits/2026-10-14/comprehensive/knowledge-inbox-visual-review-20261016.md)未发现影响主要操作的排版问题。这些历史结果不代替持续对话版验收，也不证明文学质量达到第一梯队。

此前的[电脑创作工作台与写作总管](docs/audits/2026-10-14/comprehensive/writing-workbench-agent.md)已接通目录/知识库、正文和任务三栏。总管基于 LangGraph（负责保存多步骤任务并在中断后接续的框架），协调续写、整章修订、章节检查和四阶段全书规划；正文采纳、规划确认和正式事实继续由作者决定。该历史版本的[05完整验收](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-agent-workbench-release-20261016-05.json)通过103个执行项、199个电脑场景，115份来源前后相同、78附件逐份匹配；[独立复核](docs/audits/2026-10-14/comprehensive/evidence/agent-workbench-release-independent-review-20261016.json)及九张截图目视检查通过。历史代码提交 `e9fe9eac15896c5aeda2cf9672003a0ed4433967` 已正常推送，[对应GitHub检查38776837992](docs/audits/2026-10-14/comprehensive/evidence/github-agent-workbench-e9fe9ea-success-20261016.json)于上海2026-10-16 01:37:40完成，28步全部成功；[对应本机服务](docs/audits/2026-10-14/comprehensive/evidence/local-workbench-preview-20261016.json)也已独立验收。这些工程结果不作为文学第一梯队成绩。

此前章后确认版本的本机预览入口为 `http://127.0.0.1:4174`，使用当前工作树中的 `moyu.sqlite3` 副本；原 `4173` 服务和原库保留。[章后确认服务更新](docs/audits/2026-10-14/comprehensive/evidence/local-memory-confirmation-service-memory-confirmation-release-20261016-01.json)及[三种电脑尺寸只读复核](docs/audits/2026-10-14/comprehensive/evidence/local-memory-confirmation-browser-memory-confirmation-release-20261016-01.json)已完成，156项检查和135次GET通过。该版本02仅测试文件变化，[02最小只读复核](docs/audits/2026-10-14/comprehensive/evidence/local-memory-confirmation-preview-minimal-memory-confirmation-release-20261016-02.json)确认13份实际页面资源匹配当时整轮，两个服务身份、预览72张表和原库65张表内容、结构及版本不变；当次未重启或重复运行预览浏览器整套检查。本批聊天原卡预览另见上方两份独立记录，不混用历史版本证据。预览远程模型未配置，历史核对未新增收费调用；数据库、原稿、30张私有截图和密钥不随源码上传，完整Obsidian双向同步尚未实现。

历史完整备份恢复版本 `1552104` 已正常推送，[GitHub检查](docs/audits/2026-10-14/comprehensive/evidence/github-portable-1552104-success-20261015.json)全部成功，并已[更新到本机](docs/audits/2026-10-14/comprehensive/evidence/local-service-portable-1552104-20261015.md)。更新前后65张表完整内容和结构保持，两个电脑尺寸共8个只读页面检查通过，没有模型调用或新增收费。

之后的[B1分用途参数](docs/audits/2026-10-14/comprehensive/task-parameters.md)已完成[新源码整轮](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-task-parameters-release-20261015.json)：上海2026-10-15 06:50:27至07:00:30，96项全部通过，105份源码前后不变，12套浏览器167个电脑场景、20份JSON和47张截图共67附件逐份匹配。[独立复核](docs/audits/2026-10-14/comprehensive/evidence/task-parameters-release-independent-review-20261015.json)通过；9种用途可分别保存温度、输出上限和思考程度，旧队列、原请求重试及ZIP往返保持原值。本轮仅用隔离数据和免费本机提供方，新增收费0。B1提交 `c83f897` 的[GitHub运行38712478084](docs/audits/2026-10-14/comprehensive/evidence/github-task-parameters-c83f897-success-20261015.json)已独立核对全部成功；该历史提交的实际本机更新未另作验收，不能借用A2或本轮工作台结果。

2026-10-13 的审查已进入产品修复，后续电脑工作台和知识待办验收延续至上海时间 2026-10-16。本轮及后续产品验收只以电脑端为范围，手机端不在当前目标内。保存冲突、候选身份、证据失效、任务分片、普通正文章后记忆、四阶段规划、作品备份和导入导出均有实现与指定快照的本机回归证据。[七项目全面复核](docs/audits/2026-10-14/comprehensive/README.md)逐项区分官方源码、限定组件运行、墨屿工程验证和真实文学效果，**第一梯队是持续目标，尚未证明达到**。

本批修复设定揭晓章节与知情人物的保存、当前规划实际进入写作材料、审稿作者决定进入归档和出版检查、沿已有后续章继续写作、保存成功后打开失败的恢复、创作要求草稿和长名称布局。模型任务绑定本次实际使用的资料；资料、章序或指南改变后，旧任务不能发送、重试、整理或采纳，原候选和已知用量继续保留。旧模型分析缺完整资料记录时只读，旧审稿可免费结合当前稿件逐项解释；解释后单纯归档不会使理由失效，正文或资料再变仍需重新核对。历史证据数组中混有文字、数字或空值时也能读取和编辑，原条目继续保留。

章后分析和审稿保存、读取及确认还会核对原创作指南；只改故事前提或创作约定也会使旧报告只读。正常逐项确认自己的交接或新人物时，独立记录本次决定后的来源，保留原模型请求。报告只剩部分来源标记时仍按旧模型结果处理，不能借用兼容路径继续确认；作者可免费结合当前稿件和资料重新说明旧审稿，资料再变则理由失效。

同一原句重新分析后，内容、知情范围、人物身份及关联记录完全一致的五类记忆会复用已有记录。原句依据、正式事实、人物类型、状态关联、关系对象或伏笔状态已经改变时，确认会提示核对原记忆并保持数据不变；已撤回记录不会由新报告恢复。人物状态与人物关系的对象必须是人物。

历史[指南与确认完整性复验](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-guidance-candidate-integrity-release.json)在上海时间2026-10-15 02:39:43至02:46:58通过85个执行项，10套真实浏览器检查合计125个电脑场景。95份代码文件在运行前后保持相同指纹，11份JSON与32张截图共43份附件已逐一核对；全部使用临时数据库与免费本机模拟模型，没有新增收费请求。这份原报告保持不变，只覆盖当时的来源完整性、重复记忆和人物身份修复，不能代替之后参考片段代码的整体验证。

本轮已接通作者手选参考原句：从参考库选择一段，明确放入本次续写或润色的材料，最多900个完整字符。服务端重读原句、核对作品及分支、范围和三份内容指纹，预览与任务使用同一份材料；参考只供学习写作技法，不写成本书事实或章后记忆。必保原句超出容量时先拒绝，已消费来源改变或冻结记录损坏时，发送、重试、整理与采纳均阻断。独立完整性专项13个测试方法、15个实际场景通过，3次免费本机调用、收费0；真实电脑参考专项15场景通过。首次[参考包整轮](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-reference-passages-integrity-release-20261015.json)89项中87通过、2失败，分别为旧稿整理错误码顺序和浏览器结果/截图归档不完整；98份源码前后相同，原失败保留。

修正后[参考包完整复验](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-reference-passages-integrity-refined-release-20261015.json)于上海2026-10-15 03:57:31至04:05:50完成，89项检查全部通过，11套真实浏览器共140个电脑场景；98份来源前后相同，14份JSON和36张截图共50份附件逐份指纹一致。正式参考HTTP36方法、独立完整性13方法/15叶场景及电脑参考15场景均纳入这次冻结版本，只有临时数据和免费本机提供方，没有新增收费请求。该结果覆盖该阶段参考包的本机工程验收；之后的表单等待修正与参考提交GitHub结果另列于下文，实际服务更新、文学及第一梯队仍须分别验收。

[实际本地服务验收](docs/audits/2026-10-14/comprehensive/evidence/local-service-reconciled-20261015.md)对应提交 `3a369807252e5dcda9ce5bda039d169744fedee5`。首次更新因新增空表被严格数量检查拒绝，失败原件保留；随后独立核对确认64张既有表全部内容及结构、原稿和历史版本不变，仅新增预期空表。搜索修复只刷新7张搜索衍生表，状态恢复healthy/current、滞后0；两种电脑尺寸的8个只读页面场景通过，未调用模型。该服务证据不代表后续参考片段版本已经加载。

本轮修复失败、取消和中断任务刷新后无法回看已保存正文片段的问题；任务中心可打开只读片段，执行或费用未知的请求不出现重试入口，也不能采纳、整理或再次停止。文学工具同时修复两种方法没有使用同一冻结字数契约的问题，95项离线测试通过。新的[v25单候选真实诊断](docs/audits/2026-10-14/literary-pilot-v25-single-candidate/diagnosis.md)只发送墨屿一条，返回898字，仍超600至850字硬门，未执行基线或评审。累计56/60次共享预留、105621个已知tokens，8条缺完整用量；剩4次不足新建一组完整对照，不提高预算或降低门槛。

历史[中断片段与契约完整复验](docs/audits/2026-10-13/evidence/compatibility-python311-node24-interrupted-fragment-contract-final.json)在上海时间2026-10-14 21:30:46至21:34:21通过57个执行项，包含38份Python测试、10项JavaScript语法、固定评测和12项编辑器检查；文学工具95项通过。真实浏览器48个基础、5个搜索、5个规划、14个模型设置、7个五章场景合计79个电脑场景通过，包含20轮连续写作和五章10次专用候选检查。70份源码前后指纹相同，6份JSON与14张截图共20份附件留存；仅使用临时数据库与免费本机模拟模型。[当轮中间失败](docs/audits/2026-10-13/evidence/compatibility-python311-node24-interrupted-fragment-contract-release.json)保留原件，记录模型设置显示竞态和运行期间源码变化，新成功不覆盖旧失败。工程通过不代表文学效果通过。

历史[候选整理电脑端完整复验](docs/audits/2026-10-13/evidence/compatibility-python311-node24-candidate-revision-final.json)在上海时间2026-10-14 02:03:08至02:06:19通过51个执行项，包含36份Python测试、8项JavaScript语法、固定评测和12项编辑器检查；候选整理保护19项、正文HTTP流程15项通过。真实浏览器43个基础、5个规划、7个五章场景合计55个电脑场景通过，包含20轮连续写作和五章10次专用候选检查。66份源码前后指纹相同，4份JSON和9张截图共13份附件指纹逐一核验；仅使用临时数据库与免费本机模拟模型。这份历史快照覆盖当时的候选整理，不能替代之后的代码复验。

历史[电脑端交付复验报告](docs/audits/2026-10-13/evidence/compatibility-python311-node24-desktop-release-final.json)在上海时间 2026-10-14 00:11:10 至 00:13:55 完整执行：Python 3.11.17 / Node 24.19.0 的50个执行项全部通过，包含35份Python测试脚本、8项JavaScript语法、固定评测和12项编辑器合并检查；提供方协议45项、队列54项、文学工具62项、输出检查34项通过。真实浏览器的37个基础场景、5个规划场景和7个五章作者场景合计49个场景通过，另含连续20轮写作和五章10次专用候选检查。64份源码在运行前后指纹相同；三份浏览器结果、固定评测和八张截图使用真实 `desktop-release-final` 路径，无需改名映射。撤销与重做保留另一窗口改稿，同起点插入和删改保留双方供选择；作品、分支和章节历史隔离，输入法组合输入及冲突时拒绝不安全撤销。范围与历史证据见[验证说明](docs/audits/2026-10-13/evidence/README.md)；该快照不覆盖之后新增的候选整理代码。

实际文学试评仍没有有效对照成绩。[第十三轮](docs/audits/2026-10-14/literary-pilot-v13/diagnosis.md)只实际执行首个悬疑题的一对候选：基线1176字、墨屿5320字；两份均返回 `glm-5.2`，基线超出600至850字上限，墨屿以 `length` 截断且正文开头泄漏 `Let me understand the task`，因此输出合格0/12、有效评审0/24，没有扩展其余故事或发送盲评。本轮新增2条、4495个可信完整 tokens；全局累计37条请求预留、73118个可信完整 tokens，另有10条缺完整用量，实际金额未知。原始候选、请求和账本保持不变，详见[文学评测说明](docs/evaluations/README.md)与[v13诊断](docs/audits/2026-10-14/literary-pilot-v13/diagnosis.md)。

随后四轮新的电脑端 MiniMax-M3 探针继续验证输出协议：v14 第一条基线为1197字且有非空思考字段；v15 两条为1327/1201字；v16 在 `max_tokens=900` 下为783/1114字；v17 在 `max_tokens=560` 下为825字截断/859字完整超限。四轮新增7条请求、10954个可信完整 tokens，截至v17累计44条请求预留、84072个可信完整 tokens；没有一轮形成双方合格候选或盲评，第一梯队仍未证明。评测说明见[最新真实探针记录](docs/evaluations/README.md)。

随后 v18-v23 又进行了六轮只做电脑端的首题参数探针：v18（520 tokens）两份均截断；v19（560 tokens、目标约620字）墨屿762字完整但基线截断；v20（600 tokens）两份超限；v21（580 tokens、温度0.3）基线841字合格但墨屿截断超限；v22（580 tokens、目标约600字）两份截断超限；v23（560 tokens、温度0.3）两份均截断。按原始账本复核，v23 前累计55条请求预留、101017个已知 tokens；仍未形成双方合格候选、盲评或第一梯队证据。每轮完整账本与报告见[文学评测记录](docs/evaluations/README.md)。

随后产品提示新增“达到目标字数附近立即收束”的短篇续写要求。新的[v24 电脑端真实探针](docs/audits/2026-10-14/literary-pilot-v24-live-probe/report.md)在同一新冻结方案中实际发送两条非流式请求：基线1000字、墨屿879字，均为完整 `stop`、关闭思考且无非空思考字段，但都超过600至850字硬门；因此输出合格0/12、盲评0/24，按首对失败保护停止后续请求。v24 新增3012个可信完整 tokens；全目录账本有57条原始记录，按 `request_id + request_sha256` 合并重复身份后为55次共享预留、104029个已知 tokens、8条用量未知，实际金额仍未知。独立协议诊断不计入文学账本。新提示已完成真实验证但没有修复输出收束，第一梯队仍未证明。

后续[原候选整理诊断](docs/audits/2026-10-13/literary-revision-v1/diagnosis.md)为这两份失败首稿各追加一次整理，完整保留父稿并沿用相同冻结模型、参数、3000 tokens输出上限和600至850字要求。两份回复均为“铁”1字，均返回 `glm-5.1`、`stop`、完整协议，整理输出合格0/2；没有修复首稿，没有新增独立故事或发送盲评。新增4740和4747、合计9487 tokens，当轮累计33条请求预留、59129个可信完整tokens与已知下界，仍有10条缺完整用量、实际金额未知。文学质量、事实安全、作者效率和第一梯队均未证明。

随后[回复模式诊断](docs/audits/2026-10-13/literary-transport-v1/report.md)在同一份墨屿整理输入上只切换为 `stream=false`；第一阶段返回正文“铁”1字，并观察到非空 `reasoning_content`。虽为 HTTP 200、`stop`、完整用量4747 tokens，仍因正文长度和非空思考字段失败，第二阶段未执行，也没有重试、截断或盲评。新增1条后累计34条请求预留、63876个可信完整tokens及已知下界，仍有10条缺完整用量，费用未知；该结果只说明协议异常复现，文学质量、事实安全、作者效率和第一梯队均未证明。

随后[相邻用户消息合并诊断](docs/audits/2026-10-13/literary-message-shape-v1/report.md)只把同一固定输入的两条相邻 `user` 消息合并为两个换行，实际执行第一阶段非流式请求一次。接口仍返回正文“铁”1字，并在关闭思考时返回非空 `reasoning_content`（计数1次、字符数5621）；HTTP 200、模型名、`stop` 和用量4747 tokens完整，但字数与思考一致性闸门失败，第二阶段未执行、没有重试或盲评。累计35条请求预留、68623个可信完整tokens及已知下界，仍有10条缺完整用量，费用未知；消息边界变化未消除异常，文学质量、事实安全、作者效率和第一梯队仍未证明。

工程修复历史提交 `eaca3586ddd2d8210f1ed62b1d1e22cd2c383886` 已上传GitHub分支 `codex/full-function-audit`。历史增强版运行 `38434003558`、候选整理提交运行 `38447803077` 和前一版运行 `38487730352` 均成功；代码提交 `e96e00c0e7bcebe9e045623a90cf970041bcc1bb` 的 GitHub Actions（GitHub自动运行的检查）运行 `38491492838` 于 UTC 2026-10-14 00:40:24 至 00:45:25 完成并成功，`checks` 作业的语法、契约与固定评测、编辑器合并、基础/规划/模型设置/五章电脑端回归步骤全部成功，见[代码提交远程成功记录](docs/audits/2026-10-13/evidence/github-actions-e96e00c-success.json)。这项远程工程结果仍只证明代码提交的自动检查，不证明文学质量、第一梯队、费用或公网部署；历史基线和中间失败原件继续保留。分支尚未合并至 `main`，没有公网部署验收结论。修复和剩余验收见[路线图](docs/audits/2026-10-13/roadmap.md)，原始失败与当前入口见[审查报告](docs/audits/2026-10-13/README.md)。

后续提交 `3a369807252e5dcda9ce5bda039d169744fedee5` 的GitHub运行 `38620315120` 已核实为failure：创作要求的10个行为场景通过，但1280×900下控件底部超出视口0.359375像素，布局检查失败；其后4项浏览器检查跳过。参考包提交 `032a89f43fb9b0130c637218009e270410bd243c` 的[运行38630792129](docs/audits/2026-10-14/comprehensive/evidence/github-reference-passages-032a89f-failure-20261015.json)也为failure：模型设置第9个场景等待超时，后7套浏览器步骤跳过。两份失败均保留，不能用历史成功覆盖。

本次修正模型设置测试的等待顺序：真实提交表单后，等待保存处理与页面重绘全部结束；迟到保存及排队保存也追踪原处理过程，保留全部原断言。两种本机浏览器各14场景通过后，[新整轮复验](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-reference-settings-await-release-20261015.json)于上海2026-10-15 04:42:55至04:51:13通过89项检查、11套浏览器140个电脑场景；98份源码不变，14份JSON及36张截图共50份附件指纹一致。提交 `94e2ade9f8a50fdbf722f18f877c6550e423308d` 的[GitHub检查38636187806](docs/audits/2026-10-14/comprehensive/evidence/github-reference-settings-94e2ade-success-20261015.json)随后全部成功；完成于上海2026-10-15 05:06:42。该结果覆盖参考包，不包含之后的完整ZIP恢复或实际服务更新，新增收费请求0。

电脑完整ZIP恢复已实现文件选择、校验预览、确认建立独立作品、取消未保存预览和只读找回保存结果。完整包可在新数据目录、空备份登记和空凭据目录恢复；原作品保持，恢复历史候选和规划只供阅读，不自动重放任务。回复丢失、重复确认和服务重启后通过已保存回执找到同一副本，不再创建另一份。完整相同的旧记忆保存保留作者、审批时间和原资料；当前有效原句按正文版本重锚，旧失效引用不自动恢复。

[上一轮ZIP冻结检查](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-portable-complete-backup-release-20261015.json)于上海2026-10-15 05:16:33至05:25:48通过94项检查，103份来源前后不变，12套电脑浏览器共161个登记场景，18份JSON与44张截图共62份附件指纹匹配。随后旧删除账本遗留事实的只读窄修取得同29方法、133次真实本机HTTP通过；进一步独立复现未来章资料经人物状态或别名冲突进入早章提示，故[中间整轮已停止](docs/audits/2026-10-14/comprehensive/evidence/portable-legacy-evidence-release-20261015-interruption.json)，原报告未完成且`passed=false`。

扩展同45方法的修前、修后对照现已通过，覆盖人物及冲突的实际原句来源、早晚章节、多原句顺序和恢复映射。再增4个非章节来源场景时，原严格材料检查已以400拦住预览，并未发送越界提示；本次补齐只读派生过期及来源章节完整匹配后，同49方法、257次真实本机HTTP、65次预览全部通过。49次读取与7次初始化均保持65张表及结构，原审核历史不改，该专项模型、后台执行及收费均0。

[新版完整整轮](docs/audits/2026-10-14/comprehensive/evidence/compatibility-python311-node24-portable-evidence-scope-release-20261015.json)于上海2026-10-15 06:17:11至06:26:55完成，95项检查全部通过，104份来源在运行前后及当时独立复核时指纹一致；12套真实电脑浏览器共161个登记场景，19份JSON与44张截图共63份附件逐份指纹匹配。最新49方法及ZIP辅助、恢复HTTP、新删章和Chrome恢复均纳入本轮，原94项和中间中断报告保持。本轮使用临时数据与免费本机提供方，没有新增收费请求；当时尚待GitHub检查和实际4173服务更新，后续已由上文 `1552104` 独立完成交付。阶段统计与指纹见[验证索引](docs/audits/2026-10-14/comprehensive/evidence/README.md#电脑完整zip恢复与删章边界)，协议及阶段原件见[完整ZIP恢复记录](docs/audits/2026-10-14/comprehensive/portable-complete-backup.md)。[能力矩阵](docs/audits/2026-10-14/comprehensive/capability-matrix.md)现保留十类待实施或量测差距，第一梯队仍未证明。

## 运行

运行需要 Python 3.10 或更新版本，推荐 Python 3.11。写作总管采用 LangGraph（用于保存并恢复多步骤任务的成熟执行框架），运行版本固定在 `requirements.txt`。首次使用时在独立环境中安装依赖，再启动服务：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

自行启动时默认在电脑浏览器打开 `http://127.0.0.1:4173`；新版预览使用 `http://127.0.0.1:4174`，具体加载范围见上方独立服务记录。旧版工作台验收见[历史专项](docs/audits/2026-10-14/comprehensive/writing-workbench-agent.md)，本批验收状态见[章后确认记录](docs/audits/2026-10-14/comprehensive/agent-memory-confirmation.md)。作品保存于下文的本机数据库，模型凭据保存在本机私有文件，不随源码上传。

以后进入项目目录后执行 `source .venv/bin/activate`、`python server.py` 即可。下文的开发检查命令也应在同一环境中运行；浏览器检查可通过 `MOYU_TEST_PYTHON="$PWD/.venv/bin/python"` 使用已安装依赖的解释器。

服务首次启动会在项目目录创建 `moyu.sqlite3`，按顺序应用 `docs/schema-v1.sql` 和
`docs/schema-v1.1-migration.sql`（`PRAGMA user_version=11`），再安装工作台扩展表。可以用环境变量指定数据库或端口：

```bash
MOYU_DB=/path/to/story.sqlite3 MOYU_PORT=4173 python3 server.py
```

默认监听 `127.0.0.1`，供单人本机使用。浏览器写请求检查同源 `Origin`/`Referer`，跨站和 `Origin: null` 被拒绝；只允许访问列出的页面资源，不直接提供数据库和源码。**当前没有登录、用户权限或公网部署保护**；作品隔离不能代替账号权限。改为局域网或公网监听前需要另行完成访问控制和部署验收。

[`.env.example`](.env.example)列出可用的启动配置，不含真实密钥；服务不会自动加载 `.env`。一般作者可直接在“模型与风格”页面配置，不必编辑环境文件。

## 数据保存

下表列出浏览器版的默认位置。桌面版统一使用上方独立数据目录，其中 `credentials/` 保存连接凭据，`backups/` 保存完整备份。

| 内容 | 默认位置与范围 |
| --- | --- |
| 作品、正文历史、设定、规划、证据、任务、总管对话与审计 | 项目目录的 `moyu.sqlite3`；`MOYU_DB` 可指定其他文件 |
| 完整作品 ZIP 备份 | 数据库所在目录的 `backups/`；包含选中作品，不含其他作品和密钥 |
| 页面保存的模型库与独立总管密钥 | `~/.moyu/credentials/`；目录权限 `0700`，文件权限 `0600`，SQLite 只存随机引用 |
| 浏览器草稿和界面缓存 | 浏览器本地存储；总管输入草稿按作品、分支和对话隔离，不含连接密钥，不能代替完整作品备份 |
| 导入原文件与来源记录 | 作品数据库中保存原始字节、内容指纹和解析损失说明 |

密钥可用 `MOYU_CREDENTIALS_DIR` 指定受控目录。换设备、复制或恢复为独立作品后需重新配置密钥；备份不会携带密钥和旧连接测试结论。数据库和备份目录已被 Git 忽略，不随源码推送。

## 本地 API

API（页面与本机服务交换数据的入口）由页面使用，作者无需手动调用。作品请求携带 `X-Moyu-Project` 和分支范围；这些范围只隔离作品数据，不是用户身份认证。

- `GET /api/health`：服务与 schema 版本
- `GET/POST /api/projects`、`PATCH /api/projects/:id`、`POST /api/projects/:id/duplicate`：书架、新建、归档/恢复和独立复制
- `GET /api/state`：读取当前作品的完整 UI 状态
- `PUT /api/state`：事务性全量同步（请求必须带 `full_state:true` 和完整数组；省略项会写入删除/归档投影）
- `GET /api/chapters`、`POST /api/chapters`、`PATCH /api/chapters/:id`、`DELETE /api/chapters/:id`：章节 CRUD；删除写入 `deletion_event`，正文修改会追加 `document_revision` 和 `event_log`。创建、更新、恢复、删除可在 JSON 中携带 `expected_head`（或 `X-Moyu-Branch-Expected-Head`）启用分支 head CAS，过期写入返回 409
- `GET/POST /api/chapters/:id/handoff`：预览并确认承接紧邻上章的任务；预览只读，保存核对两章正文、任务、目录位置和作品状态，相同请求只保存一次
- `POST /api/chapters/:id/patch`：范围级候选 patch；请求携带 `start/end`（Unicode codepoint）、`replacement`、`base_revision_id` 或 `base_hash`，默认只预览 diff；加 `apply:true` 后在 CAS 校验下创建新 revision、标记旧证据 stale 并追加 `chapter.range_patched` 事件
- `POST /api/chapters/:id/restore`、`POST /api/records/:id/restore`：恢复章节 tombstone 或归档设定并记录事件
- `GET/POST /api/records`、`PATCH/DELETE /api/records/:id`（记忆、伏笔同样支持 `/api/memory`、`/api/plot`）：最小 CRUD 投影，并为变更写入 `event_log`
- `GET /api/records/:id/knowledge`：只读查看资料的有向关联、回链、原文来源及本章可用范围，支持分页与错误状态
- `GET /api/events`、`POST /api/events`：查看或写入幂等事件
- `GET/PUT /api/blueprint`、`POST /api/blueprint/materialize`：保存作品蓝图、卷章场景和规划版本，按稳定身份建立章节，重复请求不重复建章
- `GET/POST /api/planning-workflows`、`GET /api/planning-workflows/:id`、`POST /api/planning-workflows/:id/start|confirm|cancel|apply`：四阶段候选规划、作者确认、停止和完整采用；最终采用只更新蓝图
- `GET/PUT /api/ai/settings`、`POST /api/ai/settings/probe`：作品模型、用途模型、风格、参数和每日次数设置；连接测试是真实模型调用，消耗额度
- `GET/POST /api/ai/connections`、`POST /api/ai/connections/probe`、`DELETE /api/ai/connections/:id`：模型库的查看、测试、凭测试凭证保存与移除；用途只能新选当前作品已测试保存的有效连接，失效原分配保留并要求作者处理
- `GET /api/context`、`POST /api/ai/prompt-preview`、`POST /api/ai`：查看有界工作集、预览任务契约并生成候选；支持 `retrieval_profile` 配置预算、章节召回数、近章窗口和记忆/设定上限，并返回分层计数与证据来源。续写和润色可携带 `reference_selections` 的来源身份、范围和指纹，由服务端重新取原句；不接受客户端自填原文
- `GET /api/ai/status`、`GET /api/ai/quota`、`GET /api/ai/runs/:id`、`GET /api/ai/runs/:id/stream?after_seq=N`、`POST /api/ai/runs/:id/retry|cancel`：查看模型、用量和运行审计；SSE（服务持续分段发送文本）支持实时分片与按序号重放，每块先落盘；非流式返回、离线、截断、取消和未知结果分别标记
- `POST /api/ai/runs/:id/revise`：作者确认整理未采纳的续写或润色原候选；保留父候选全文，冻结原模型、参数、输出预算、字数与稿件范围，为同一父候选建立一个独立计用量的子候选；重复请求返回已有任务，不重复生成
- `POST /api/ai/queue` 或 `/api/ai` 携带 `async:true`：将请求持久化为队列任务；`GET /api/ai/queue`、`GET /api/ai/jobs/:id` 查看状态，`process|cancel|retry` 控制单个任务，`queue/drain` 恢复人工或重启后的积压任务
- `POST /api/ai/runs/:id/apply`：在同一事务中验证作品、分支、章节、正文版本、范围与作者决策，应用一次候选；旧稿或错误范围拒绝，重复请求不重复追加正文
- `GET /api/chapter-analysis`、`POST /api/chapter-analysis/:id/confirm`：普通正文章后摘要、交接和记忆候选的回读与逐项确认
- `GET /api/story-reviews`、`POST /api/story-reviews/:id/issues/:issue_id/decision`：故事审稿和作者采纳、忽略、解释记录；接受建议不等于已经修好正文
- `GET /api/search?q=...&explain=1`：搜索章节、设定、记忆、伏笔和时间线；使用 CJK FTS/BM25 与新近度、证据分数的可解释 RRF 融合，支持项目/分支、章节、实体、状态和时间硬过滤，结果保留来源章节与范围元数据；`explain=1` 返回各信号排名和过滤原因
- `GET /api/doctor`（兼容别名 `/api/projection-health`、`/api/health/projections`）：只读检查 state/event、分支 head、搜索索引、AI 队列和章节提交投影的新鲜度、失败与缺口
- `GET /api/story-map`、`POST /api/relationships`、`POST /api/timeline`、`POST /api/foreshadow-links`：读取或写入关系、时间线、伏笔生命周期和冲突投影；故事结构页提供关系 SVG 图谱、时间线总览和当前章节人物状态知识范围审查
- `GET/POST/PATCH/DELETE /api/character-states`、`/api/plot-threads`：管理带时间范围、知识范围和证据链接的人物状态与剧情线
- `GET /api/assertions`、`GET /api/assertions/:id`、`POST /api/assertions/:id/review`、`GET /api/memory/:id/evidence`：审查正典事实、冲突、依赖和失效证据
- `GET /api/chapters/:id/revisions`、`POST /api/chapters/:id/restore-revision`、`GET /api/chapters/:id/commits`、`POST /api/chapters/:id/commit`：查看差异、恢复为新 revision，并执行章节提交阶段
- `GET/POST /api/backups`、`GET /api/backups/:id/download`、`POST /api/backups/:id/restore`：完整作品备份、下载、校验后恢复为独立作品
- `POST /api/backups/import/preview|apply|cancel|status`：从电脑上传完整ZIP，校验预览、确认建立独立作品、释放未保存预览及只读查找保存回执；迟到预览不能重新占用已取消身份，保存中的预览不能被取消
- `POST /api/import/preview`、`POST /api/import/apply`、`GET /api/references`、`GET /api/references/:id`、`GET /api/references/:id/search?q=...`：TXT/MD/EPUB 分章预览、导入独立稿件或参考库、来源检索；导入不自动发送收费分析
- `POST /api/assistant/query`、`POST /api/assistant/confirm`：有限任务的只读预览与作者确认后创建候选；正文仍需另行采纳
- `GET/POST /api/writing-agent`、`POST /api/writing-agent/:id/resume`：写作总管的保存计划、授权、自动接续、停止与历史回看；重复提交不会重复建立任务，`GET` 可按 `session_id` 精确读取旧任务
- `GET/POST /api/agent-chat/settings`、`POST /api/agent-chat/settings/probe`：总管对话的继承/独立连接与真实连接测试；独立连接不替代主写作连接，密钥不在公开回读或作品备份中返回
- `GET/POST /api/agent-chat/sessions`、`GET /api/agent-chat/sessions/:id`、`POST /api/agent-chat/sessions/:id/messages`：保存和回读当前作品、分支的持续对话，消息固定本次正文、引用和材料
- `GET /api/agent-chat/runs/:id`、`POST /api/agent-chat/runs/:id/cancel`、`POST /api/agent-chat/actions/:id/confirm|dismiss`：查看或停止回复、确认或放弃建议；确认使用稳定请求身份恢复已有步骤，正文仍须比较采纳
- `GET /api/writing-inbox`：读取当前作品和分支的四类全书待办、完整分类计数及分页；不启动或重试模型任务
- `GET /api/export/markdown`、`GET /api/export/epub`、`GET /api/export/manifest`：导出当前稿件及范围/损失清单；内嵌原稿与指纹，墨屿可精确回读正文；直连也受终稿门保护，作者覆盖需带 `X-Moyu-Export-Override`

伏笔投影的 `DELETE /api/plot/:id` 会归档条目并记录事件；章节和设定使用上面的专用 restore 路径恢复。

章节正文采用追加式 revision，数据库中的 `document_revision`、`event_log` 和
`candidate_memory` 遵循 schema 的外键、作用域及 append-only 触发器约束。

仅做静态预览时可以运行 `python3 -m http.server 4173`。API 不可用时页面使用浏览器本地缓存；书架、任务、完整备份等服务功能不具备，正式创作请使用 `server.py`。

## 当前功能

- 电脑端创作工作台：左侧目录与知识库、中间正文、右侧持续对话；任务、正文候选和本章依据按需展开，资料保存和结果回看保留稿件位置，专注模式可收起目录
- 人物与知识卡可查看有向关联、回链和逐条原句来源；有效来源点击后再次核对并定位，未来章节、未确认事实和失效证据保留范围说明
- 全书待办按写作总管、生成结果、记忆确认和故事审稿分类，完整计数与分页，精确打开旧任务和对应记录；右栏局部数字仍保留其原有含义
- 本机字号、行距和正文宽度偏好；按作品、分支、章节记住选区与滚动，明确原句定位优先于旧阅读位置
- 多作品书架、新建、切换、复制、归档/恢复，作品总览、字数目标和章节进度
- 蓝图、卷/章/场景规划、四阶段模型候选与逐阶段确认、自动保存、移动和按规划建立章节
- 章节编辑器、草稿与已确认快照、自动保存、两窗口冲突合并、按作品/分支/章隔离的历史版本，以及保留另一窗口改稿的撤销和重做
- 章节任务卡（目标、视角、场景计划、伏笔动作、回顾、下一章简报）与完成前质量审查
- 任务中心、最多两个并行模型任务、逐块保存、取消、重连和显式重试
- 失败、截断、取消和中断任务的已保存片段可从任务中心只读回看；未知执行或费用不显示重试入口
- 选区润色、候选差异、范围采纳；中文和 emoji 按字符范围转换，长章未选范围不会整章覆盖
- 未采纳原候选的一次整理确认、父子候选回看、独立用量与重复保护；整理结果仍需作者比较后采纳
- 人物、地点、势力、物品等设定卡片
- 情节与伏笔看板
- 全文搜索
- 普通正文的章后摘要、交接、事件、人物状态、关系与伏笔候选，原句预览和作者确认
- 故事审稿及作者处理记录，正文或材料变更使旧结果过期
- JSON 稿件导入/导出、完整作品 ZIP 备份、从电脑选择ZIP预览并独立恢复、取消预览和找回同一已保存副本
- TXT/MD/EPUB 导入预览、拆分/合并/改名、原始来源留存和参考库搜索
- 手选一段参考原句用于本次续写或润色、原句回看和撤回；参考与本书事实分开，超容量或来源变化先阻断
- Markdown/EPUB 终稿导出、原稿指纹、正文回读及损失清单
- 有界上下文预览（估算 token、可配置检索层、来源 ID/范围）与 AI 运行记录
- AI 材料 pin/omit/order、完整 Prompt 契约预览与结构化 JSON 任务结果
- 上下文预算会记录裁剪原因和被省略来源；固定材料不会被静默裁剪，固定集合超出预算时请求会明确阻断
- 人物关系、时间线、伏笔生命周期和冲突查询
- 章节 `prewrite → precommit → postcommit` 提交快照、revision diff 与恢复前预览
- 先测试再保存的模型库、9种用途的连接与独立参数、失效分配保护、私密凭据保存和每日生成次数限制
- 模型原生 SSE 解析、带序号的输出账本、断点重放、结束原因检查和已完成运行取消保护
- 有限自然语言项目助手：只读搜索与任务预览，确认后创建候选
- 持续写作对话、当前章与选区引用、历史恢复、作品与分支隔离，以及由聊天建议进入真实任务、候选比较、记忆与审稿；连接配置集中于设置
- 基于 LangGraph 的写作总管：协调规划、续写、整章修订、章后记忆与故事审稿，采纳正文、确认规划与确认事实继续由作者决定
- 全书完稿状态：终稿检查后可标记完成，状态持久化并支持恢复写作
- 本轮验收和继续开发只覆盖电脑端；手机端不在当前目标内

## 设计原则

采用单用户、本地优先、作者确认的工作流。未配置模型时保持离线，配置后只在作者触发任务时发送有界材料。所有正文修改追加版本，候选不能越作品或替换已经变化的原稿；模型猜测不会自动写成正典。

一般全文搜索使用 SQLite CJK FTS/BM25 和多信号排序；实际 AI 材料由 `moyu_context.assemble_context` 使用本地段落检索统一组装，并检查作品、分支、故事时点、视角、事实与作者计划边界。两者不是同一持久化全文索引。片段保留版本、字符范围和指纹；固定材料超预算时阻断，不静默裁掉。

目前没有向量检索、真实模型分词计数、外部多进程任务调度、定时自动备份、提示模板版本库、DOCX/PDF、团队权限或完整 EPUB 版式往返。四阶段蓝图与五章伏笔作者链已有指定快照的真实电脑端回归；真实模型规划质量和审稿准确率仍未正式量测。持续对话可讨论并提出真实创作步骤，写作总管通过固定流程协调现有功能；尚未自动包办完整开书和下一章建立，正文采纳和事实确认仍保留作者决定。

## 长篇记忆

“记忆中枢”的“提取当前章记忆”直接分析普通正文，不要求作者插入标签。返回摘要、下一章交接和有原句范围的事件、状态、关系、伏笔等候选；作者可修改候选说明并逐项确认或拒绝。确认后的有效事实进入后续章节材料，同一正文版本重复提取和确认不重复建立事实。正文改变、来源删除或旧任务迟到会让相关材料失效，不能继续作为正典使用；作者手写的交接不会被过期自动摘要覆盖。

新发生的删章已统一处理专用删除和整份作品保存时遗漏章节两种入口：撤回该章所有旧版本原句支持的记忆与正式事实，保留正文版本和人物状态历史，其他章有效资料及作者手写状态保持。新95项整轮重新通过5方法真实HTTP控制，包含删除后提示不再使用旧事实、处理中途失败全库回滚，以及无变化历史保存逐行保真。旧删除账本遗留状态采用只读派生可用性；人物状态及别名冲突按全部实际原句的最晚章节限制使用，不能把未来章资料提前放进早章提示。来源无法完整对应活动章节时，衍生材料标为过期并排除，原审核断言与历史不改；人工状态及合法混合备注保留。同49方法、257次真实HTTP专项已纳入95项完整整轮通过；该历史版本的远程和实际服务交付见上文 `1552104`，不能覆盖之后的工作台源码。

原有 `【人物】`、`【物品】`、`【地点】`、`【伏笔】` 标记仍可使用。章后提取和故事审稿都需要作者复核，空报告、失败或过期不算通过。

固定合成小说的 30/100/300 章实际 HTTP 测试捕获了最终发给模拟模型的材料。每种规模五条标注查询，Recall@1/5/10（应找出的原句实际被送入的比例）均为 1.0，来源引用准确率为 1.0，非法材料为 0，见[原始结果](docs/audits/2026-10-13/evidence/context-provider-recall-actual.json)。这些数字只适用于该合成数据；延迟包含本机模拟模型，token 为 UTF-8 字节保守估算，不能推断真实小说效果、远程模型延迟或无限上下文能力。

开源设计参考见 `docs/design-references.md`；最新七项目逐项对照见
[能力矩阵](docs/audits/2026-10-14/comprehensive/capability-matrix.md)，早期六项目对比保留在
[`docs/comparison-six-projects-2026-10-12.md`](docs/comparison-six-projects-2026-10-12.md)。

## AI 创作助手（可选）

页面右侧“写作总管”提供持续对话。可以直接讨论人物、情节和写法，也可勾选带入当前章；选中正文后点击“引用选区”，能够回看原文并请总管提出局部修改。每次最多引用三处；引用和本次材料保留来源版本，原稿或资料已变时，旧建议会要求重新讨论。切换功能页、刷新或服务重启后可继续查看已保存对话；切换作品与分支时各自隔离。

总管认为需要创作操作时，会给出可确认或放弃的建议。确认续写、整章修订、章节检查或全书规划后，页面留在原聊天卡片，显示本次任务的来源章、状态和调用上限。核实任务身份、来源和进度后，才提供“启动本次任务”；启动前先保存正文与未保存的规划，保存失败不发送写作请求。“核对进度”只读取原任务；“查看本次结果”打开它绑定的准确任务和来源章，供作者比较候选或继续决定。详见[聊天原卡说明](docs/audits/2026-10-14/comprehensive/chat-inline-tasks-20261016.md)。

选区修订仍进入引用原文对应章节的正文候选，比较并采纳后才替换原范围。普通回答不会直接修改正文或自动建立写作任务。“任务进度”“正文候选”“本章依据”仍从聊天顶部打开，在正文下方查看和处理，可随时收起。

多步写作先在原卡保存并核实计划，由作者单独启动；也可从“写作总管”管理页直接建立计划。下列次数只计写作流程，聊天回复另计调用并共用每日额度，次数上限不是金额保证。“续写”最多调用三次模型，依次生成正文候选、整理章后记忆、执行故事审稿。正文候选仍需作者比较并采纳；章后资料生成后，总管等待作者逐项决定，再继续审稿。“整章修订”先提供整章修订候选，采纳后同样接续记忆、作者决定与审稿，最多三次；整章超过12000字时先选段使用局部修订。12000字为整章修订硬上限，当前材料容量不足时也会在发送前拒绝，可选段修订或调整容量。“章节检查”只执行章后记忆和故事审稿，最多两次。

总管显示“等待章后确认”时，点击“确认章后资料”，为事实、章节回顾和下一章任务选择采用或不采用。可以分次保存，尚未处理完时不会启动审稿；全部保存后自动继续。刷新或重启后沿用原进度；若最后一次保存后进度尚未更新，可点击“核对确认结果”。总管始终打开本次任务对应的分析，保留原始生成依据；正文、资料或规划另有变化时会阻止继续，不会静默重新生成。完整备份恢复的旧任务仍仅供回看。专项说明见 [章后确认与总管接续](docs/audits/2026-10-14/comprehensive/agent-memory-confirmation.md)。

“全书规划”复用四阶段规划：故事总纲、分卷、章节、场景；每阶段由作者确认后自动安排下一阶段，最多四次。四阶段确认完成后仍需“采用完整规划”才更新蓝图，已写正文保持。规划规模继承现有蓝图，空蓝图默认1卷、每卷3章、每章2场景；单次上限为3卷、每卷8章、每章4场景，实际规模在计划中显示。生成结果不符合范围、来源变化或费用未知时流程停止并保留已有候选。

总管计划、进度检查点（用于中断后找回同一步的保存记录）和任务关联保存在同一份本机 SQLite 中。刷新、服务重启或重复确认会回读同一任务；执行或费用未知时停止，不能借恢复自动重发。章节任务检查原稿、实际材料和模型设置变化；全书规划检查蓝图和模型设置变化，单纯修改正文不会使规划失效。相应来源变化后需按新资料建立任务。完整 ZIP 包含总管历史，恢复副本中的旧总管仅供回看，不能继续执行封存检查点。工程测试通过只说明流程和数据保护有效，不代表模型已经达到目标文学质量或第一梯队。

启动服务后，打开“创作工作台”即可使用候选式创作助手。它支持续写、场景卡、大纲、发散、润色、总结和连续性检查；生成结果先显示在候选区，只有点击“采纳”才会写入正文。未配置模型时也能使用离线写作卡，不会暗中联网。

第一次使用从首页“开始创作”进入：填写一句话故事设定、类型、主角和眼前阻力，墨屿会建立第一章和初始设定。之后沿着“填写任务卡 → 写作 → 生成候选 → 人工采纳 → 确认记忆 → 审查本章 → 完成本章 → 自动准备下一章”的路径推进，首页会持续显示下一步行动。写完后从“终稿检查”标记全书完稿，作品会进入收尾入口；需要修改时可以恢复写作。

“创作规划”的“分阶段规划”按总纲、卷、章、场景四步生成候选，每步由作者确认后才进入下一步。规划固定原蓝图版本、内容指纹和实际请求；确认后的阶段供下一阶段使用，明确失败时可手动只重做当前阶段。执行状态未知的请求保留记录，不自动或手动重发同一未知阶段；原蓝图变化会阻止旧规划继续写入。四阶段确认后，“采用完整规划”只更新蓝图，建立章节仍由作者另行触发，已写正文不会被规划结果覆盖。重复刷新未变的状态或错误不重复追加记录。

规划输入会持续自动保存。保存失败时保留独立的本地规划草稿，刷新后可继续保存；另一个窗口同时改同一项时保留双方文字，由作者选择后保存。采用规划期间暂时锁定规划输入，切页返回仍保持锁定，回应结束后继续编辑；第二套规划建立章节只增加新章，保留旧章节的标题、正文和任务卡。

具体操作：进入“创作工作台”，填写本章目标、视角、场景计划和章节出口；可用“上下文预览”检查材料。生成后先比较和编辑候选，再点“比较并采纳”。局部改稿先选中原文再润色，采纳会检查原稿版本和范围；生成期间改稿或切章后，旧结果不能写到错误位置。“生成任务”可查看进度、取消、续接和处理失败，任务结果只采纳一次。

需要借鉴旧书写法时，点击“选择参考片段”，选参考书并搜索原句，点击“用于本次写作”，返回写作后可查看完整原句或撤回。每次只选一段、最多900个完整字符；续写或润色的上下文和提示预览会显示同一段。参考选择属于当次操作，换作品、分支或章节以及刷新后须重新选择；生成请求成功进入队列后清除本次选择，已排队任务保留原冻结材料。参考书人物与情节不会自动进入本书事实，参考中的命令文字也不会成为系统要求。

未采纳的续写或润色原候选可点击“整理候选”。确认窗口显示原候选字数、原字数要求、来源章节与范围，并说明这次整理单独计入模型用量；可补充整理要求。整理完整保留父候选，沿用原任务冻结的模型、参数、输出预算、字数和原稿范围，产生独立子候选；每个父候选只建立一个子候选，重复提交返回已有任务。可回看原候选，只有作者明确比较并采纳后才写入正文，整理要求不能放宽原字数或改写已确认事实。

提交失联时先只读核对已有任务；模型执行或费用未知的请求不重发，恢复结果不自动采纳。公开整理关联由 `public_binding` 仅提供 `operation`、`parent_run_id`、`root_run_id`、`parent_characters` 四个字段，不包含候选全文、整理备注或 `request_id`。正文、来源、输出要求与明确采纳仍通过同一保护路径检查；这项功能的存在不代表真实模型能够完成合格整理。

若本次请求明确关闭思考，回复仍含非空思考字段，页面提示“回复与关闭思考设置不一致”，候选保留供阅读，采纳和整理均被阻止，不会自动重试。`reasoning_observation` 同时检查`reasoning_content`和`reasoning`，只保存全程字段次数、非空次数、异常类型次数和字符长度，不保存思考内容；计数不受128条协议形状记录的截断影响。非null、非字符串的思考字段会提示无法校验并阻断，不把未知形状称为已知非空文字。实际正文、结束原因、完成状态和用量保持提供方原值，协议完整仍须另外通过正文检查；空思考字段或明确启用思考不触发这项检查。

写完当前章，打开“章后记忆”逐项确认原句证据，再打开“本章审稿”处理问题。完成本章后准备下一章；“历史版本”可恢复为新版本，相关旧来源与审稿须重新确认。接受审稿建议只是记录作者决定，正文修改后仍要重新审查。

章后记忆与故事审稿完成后，候选栏显示对应任务名和只读摘要；点击“查看章后记忆”或“查看故事审稿”进入逐项处理，不把分析结果当正文采纳。普通续写和润色仍可编辑候选、比较后采纳。

“情节与伏笔”的“状态与原句”可选择已有来源记录重新确认，保留原记录身份。删除回收原句后，终稿会阻断；恢复文字仍需作者重新确认该来源，再重新进行基础审查。只把卡片移到“回收”而没有有效回收原句，也不能通过终稿。

顶部“终稿检查”向服务读取当前稿件的实际导出条件：未完成章节、未通过审查、未确认记忆、失效证据、未回收伏笔和明显空稿会阻断 Markdown/EPUB 终稿导出，直接调用导出 API 也会再次检查。作者确实要带着问题交付时，可以点击“作者覆盖并记录原因”，填写至少 8 个字符的理由；理由与当前稿件指纹一起保存在本机，EPUB 包内也会写入覆盖记录，稿件发生变化后需要重新覆盖。全书完稿后保存状态并禁止再归档创建下一章，恢复写作后可继续。JSON 备份仍可用于保留工作稿，不绕过终稿门。

在“模型与风格”的“模型库”先填写服务地址、访问密钥和模型名称，点击“测试连接”，通过后再点击“保存模型”。测试通过只验证当前输入，不会提前保存或切换正在使用的连接；修改地址、密钥或模型名称后必须重新测试。测试会真实调用模型并可能产生费用，保存模型本身不调用模型。原有连接保持有效，但需在模型库中重新测试保存，才能出现在新用途选项中。

在“用途与风格”选择已经测试并保存的默认模型，再为续写、润色、规划、回顾、记忆和审稿等用途单独选择连接；可新选的连接仅包含当前作品已测试保存的有效连接，失效的原分配仍保留并显示提示，不能直接输入未验证的模型名称。不同用途可使用不同服务地址和密钥；没有单独选择时沿用默认，章后记忆与故事审稿未单设时分别沿用回顾与检查。保存用途和文风不调用模型。自行启动的浏览器服务仍可通过 `MOYU_AI_BASE_URL`、`MOYU_AI_MODEL`、`MOYU_AI_API_KEY` 提供未保存设置时的兼容配置，但不会自动成为已验证的模型库条目；桌面版忽略这些模型环境变量。

已分配的连接凭据失效后，用途仍显示原连接并提示重新测试，不会自动换成默认模型。仅修改文风而未处理失效连接时，保存会明确失败并保留原设置；作者重新测试保存，或主动选择其他有效模型、清除分配后，才能保存新的用途设置。

同页“写作总管连接”默认沿用选中的默认模型，也可选择独立连接，单独保存对话的地址、模型、密钥和参数。独立连接只供聊天使用，续写、润色、规划、记忆和审稿按照用途选择的模型执行；两者共用每日额度。保存总管设置不调用模型，点击“实测连接”才会真实调用。默认模型改变后，总管的继承状态和旧测试是否失效会同步刷新，同时保留尚未保存的独立连接输入。密钥只进入本机私密凭据文件，独立连接配置不随完整备份复制。

用途表可分别填写变化程度、输出上限和思考程度；空白沿用全局，章后记忆和故事审稿未单独设置时分别沿用回顾与检查。清除一行只移除该用途参数，所选模型连接保留。温度0会保存为0，已排队任务和原请求重试继续使用创建时的连接和参数；旧连接或密钥已变化时拒绝重试，不会悄悄换用新密钥。

连接检测必须完整结束、精确回复“连接正常”、回传名称与所选模型一致且符合思考设置，才能保存为已验证连接；名字一致仍不能证明底层模型身份。失败会显示双方名称和原因，历史未按现行条件验证的成功记录显示为未验证。切换页面或作品会清空未保存密钥和测试凭证，迟到回应不能覆盖当前设置；这些操作不会自动重试模型。完整备份保留模型库的名称、地址和模型名，但不保留密钥、测试凭证或有效验证状态，恢复后必须重新测试保存并选择用途。

本地 Ollama 地址可用 `http://127.0.0.1:11434/v1`，远程地址要求 HTTPS。网页保存的密钥进入本机私密文件，公开回读只显示“已保存密钥”，不会放进浏览器缓存、作品数据库正文、任务公开数据或备份。每次只发送本次工作集，完整作品留在本机。

`length`、缺少完成标记、错误结构或未知结束原因不算完整候选，不能直接采纳。取消会关闭本机连接，无法保证远端同步停止或不收费。可能已发送的未知请求不自动重复；每日额度限制生成次数，未知用量和价格不能当作零，也不是人民币费用保证。真实调用的协议兼容性和文学质量仍须分别检验。

任务失败、取消或中断后，已经到达的正文片段仍保留。刷新页面或进入“生成任务”，点击“查看片段”即可只读回看；服务中断时仅通过已有输出账本补回片段，不会重发模型。片段不会写入正文，也不能整理；切换作品、分支或已删除来源章后，旧回看不能覆盖当前候选。执行或费用未知时没有重试入口，明确失败且用量完整的任务仍按原规则由作者显式重试。

参考项目、许可证边界和第三方审查见[第三方审查](docs/third-party-audit.md)。

## 可复现评测 runner

固定 JSON 测试样本由 runner（自动执行测试并整理结果的脚本）计算 K=1、5、10 的证据 Recall/Precision，输出指标快照并执行门槛。分母为 0 标记 `NA`；未实测门槛不会通过。失败样本返回非零退出码：

```bash
python3 scripts/evaluation_runner.py \
  --fixtures-dir tests/fixtures \
  --output artifacts/evaluation.json \
  --pretty
```

`tests/fixtures/schema_scope.json` 运行 SQLite schema 负向 fixture，`corrupt_import.json` 和 `reversible_patch.json` 分别覆盖损坏导入拒绝与 AI patch 撤销。单个 JSON 可用 `--fixture path/to/fixture.json` 运行。

严格功能边界回归可运行 `python3 tests/strict_feature_fixtures.py`：它验证无证据记忆不能确认、带来源记忆会生成正典断言、AI 决策可追溯，以及模型失败会写入 `failed` 运行记录。

文学试评另用 `scripts/literary_evaluation.py`，固定故事、材料、模型、参数和实际请求，并记录发送意图、用量及未知费用；候选匿名双顺序评判。默认六题试评不能证明第一梯队，也不等于运行七竞品。普通命令不调用收费模型，真实执行需要明确的收费执行开关，详见[评测说明](docs/evaluations/README.md)。

## 验证

仓库的[自动检查配置](.github/workflows/ci.yml)使用 Python 3.11 / Node 24，在推送和拉取请求时先安装 `requirements.txt`，再执行语法、全部 `tests/*_fixtures.py`、固定评测、编辑器合并及各类电脑浏览器回归，覆盖规划保稿、五章作者流程、资料待办和阅读偏好。Node（执行页面自动检查所需的开发工具）只用于开发验证，不是运行产品的依赖。浏览器回归使用 Playwright 1.62.1（自动点击真实浏览器的测试工具），使用隔离数据和免费本机模拟提供方；资料待办与阅读偏好专项不调用模型，不使用真实密钥。历史 Node 20 证据保留原运行版本，不能当作 Node 24 或增强版远程检查已经通过。

```bash
python3 -m py_compile *.py scripts/*.py tests/*.py
for fixture in tests/*_fixtures.py; do python3 "$fixture" || exit 1; done
node tests/editor_sync_fixtures.cjs
node tests/frontend_browser_regressions.cjs
node tests/planning_browser_regressions.cjs
node tests/desktop_author_flow_regressions.cjs
node tests/knowledge_inbox_browser_regressions.cjs
node tests/editor_preferences_browser_regressions.cjs
```

浏览器测试需安装 Playwright 与浏览器；`MOYU_PLAYWRIGHT_MODULE` 可指定模块路径，`MOYU_TEST_PYTHON` 可指定启动服务的 Python。参考片段的HTTP与完整性测试为 `tests/reference_passages_http_fixtures.py`、`tests/reference_passages_integrity_http_fixtures.py`，真实电脑页面测试为 `tests/reference_passages_browser_regressions.cjs`。电脑ZIP恢复的逐表和真实HTTP验证分别为 `tests/portable_backup_fixtures.py`、`tests/portable_backup_http_fixtures.py`，电脑页面验证为 `tests/portable_backup_browser_regressions.cjs`；新删章与旧遗留资料分别由 `tests/chapter_deletion_dependency_http_fixtures.py`、`tests/legacy_deleted_dependency_http_fixtures.py`覆盖。专项边界见[完整ZIP恢复记录](docs/audits/2026-10-14/comprehensive/portable-complete-backup.md)。资料关联与全书待办的规则及接口测试为 `tests/record_knowledge_fixtures.py`、`tests/writing_inbox_fixtures.py`、`tests/knowledge_inbox_http_fixtures.py`；共享材料的范围交集另由 `tests/context_scope_intersection_fixtures.py` 和 `tests/nested_record_scope_http_fixtures.py`覆盖。本批浏览器结果、截图和整轮状态单独登记在[资料与待办专项](docs/audits/2026-10-14/comprehensive/knowledge-inbox-workspace.md)。

持续对话与连接设置的接口专项为 `tests/agent_chat_fixtures.py` 和 `tests/agent_settings_fixtures.py`。`tests/agent_chat_browser_regressions.cjs` 使用受控接口验证页面交互；`tests/agent_chat_flow_browser_regressions.cjs` 通过真实本机接口和免费模拟模型走通讨论、任务确认、候选采纳、记忆与审稿、停止和重启；`tests/agent_settings_browser_regressions.cjs` 验证连接继承、独立设置、联动刷新和密钥保护。这些专项不证明真实模型文学质量，本版完整验收与远程结果仍须对应具体源码记录。

检查配置存在和本机通过不能替代实际远程执行；`e96e00c` 的[历史GitHub成功](docs/audits/2026-10-13/evidence/github-actions-e96e00c-success.json)、`3a369807` 的[后续失败](docs/audits/2026-10-14/comprehensive/evidence/local-service-reconciled-20261015.md)、`94e2ade` 的[参考包成功](docs/audits/2026-10-14/comprehensive/evidence/github-reference-settings-94e2ade-success-20261015.json)及 `1552104` 的[完整恢复交付](docs/audits/2026-10-14/comprehensive/evidence/github-portable-1552104-success-20261015.json)分别保留。写作总管和三栏工作台的05历史验收及其对应远程结果在[历史专项](docs/audits/2026-10-14/comprehensive/writing-workbench-agent.md)登记，不能代替本批新增源码的完整验收或具体提交的 GitHub 检查。

## 备份、恢复与导入

作者在“备份中心”点击“完整备份”，可查看清单、下载 ZIP，恢复时建立独立作品并保留原稿。完整备份包括正文、版本、设定、规划、人物状态、关系、时间线、伏笔、原句证据、任务、总管对话、审计及参考来源；通过一致数据库快照、内容指纹、行数和外键检查后恢复。运行中或待执行的外部模型任务会标为失败或执行未知，不自动再次收费；恢复的旧总管对话只读，需要新建对话继续，独立对话连接和密钥须重新配置。**当前没有定时自动备份**。

要从电脑恢复完整备份，打开“备份中心”，点击“从电脑恢复 ZIP”，选择以前下载的完整作品 ZIP。先核对原作品、备份时间及章节和历史数量，再填写恢复后的作品名，勾选“我确认建立独立作品”，点击“建立独立作品”。会创建新作品并保留原稿；文件最多32 MiB，预览30分钟内有效。密钥需要在新作品中重新配置。

保存结果不明确时，回到备份中心点击“确认上次恢复结果”；已经建立但暂时打不开时，点击“重新打开副本”，会继续打开同一份作品。未确认的旧预览可点击“释放旧预览”后重新选择文件；网络恢复前先保留电脑上的ZIP。

选择另一文件、关闭预览、切作品或分支及刷新页面时，会请求释放尚未保存的预览；服务收到取消后，即使预览回应尚未到达也会保留取消记录，迟到回应不能重新占用同一身份。确认正在保存时先等待结果，不能通过取消打断已经开始的保存。查找结果只读取回执，不建立新作品。预览仅在本机服务内存保留，未保存的文件不会写进真实数据库；坏包拒绝时原作品不改。

首次打开作品失败时，未编辑的占位内容不能成为该作品的草稿；合法的本机离线稿和已确认副本分别保留。切换作品后的迟到回应不能污染当前作品，保存后打不开时仍保留已保存身份供只读重开。

恢复或复制产生的独立作品保留历史候选、父子整理关系、原请求指纹和用量。副本中的历史候选只供阅读，不能继续采纳、整理、重试或修改历史决定，也不会自动调用模型；继续创作须按副本当前稿件生成新候选。原请求和父候选全文不因身份映射而改写。

规划恢复保留冻结候选、已采用历史、原蓝图与结果版本及作品身份映射；原始规划候选与其内容指纹不改写。恢复副本里的历史规划只供阅读，不继续确认未采用候选或再次调用模型；排队、运行中或执行未知的规划请求均保留为未知。要在副本中继续模型规划，需重新创建规划，历史采用结果不会再次写入正文。

“旧书与参考库”支持 TXT/MD 的 UTF-8/GB18030 编码和 EPUB 可见文字提取，先预览再拆分、合并、改名，导入独立作品或当前作品参考来源。参考来源不写成本书正典。原文件上限 32 MB、最多 2000 章、每章最多 200 万字符；EPUB 解压合计上限 128 MB、单文件 16 MB。第三方 EPUB 的版式、字体、图片等损失会列出；墨屿自身 MD/EPUB 导出的正文原文可精确回读，仍不等于完整版式往返。出版原稿当前上限 16 MB，超限须分卷，尚无 DOCX/PDF。

手选参考只绑定本次实际使用的片段及所属来源，不把整座参考库灌入模型，也不因无关参考变化阻断任务。已消费原句、分节或文档信息改变时，旧候选仍可回看，不能继续发送、重试、整理或采纳。原句和依赖都丢失、冻结请求被篡改等损坏也按失败处理，不借旧请求绕过校验；完整恢复副本仍按历史候选只读规则处理。

浏览器 JSON 导出用于便携稿件备份，不能替代完整作品 ZIP。下列 SQLite 脚本供维护和故障演练使用，不要求作者手动执行：

数据库运行时用 Online Backup API 或 `VACUUM INTO` 生成快照，不直接复制正在使用的数据库文件。

```bash
# 默认使用 sqlite3.Connection.backup，目标文件和 JSON 清单均原子替换
python3 scripts/sqlite_backup.py backup moyu.sqlite3 backups/moyu.sqlite

# 需要 VACUUM INTO 时显式选择；--force 才会覆盖已有目标
python3 scripts/sqlite_backup.py backup moyu.sqlite3 backups/moyu.sqlite \
  --method vacuum --force

# 检查格式、quick_check、integrity_check、外键、页数以及清单 SHA-256
python3 scripts/sqlite_backup.py verify backups/moyu.sqlite

# 恢复前先验证清单中的 SHA-256；恢复到临时文件后再次校验，再原子替换目标
python3 scripts/sqlite_backup.py restore backups/moyu.sqlite backups/recovered.sqlite \
  --report artifacts/restore.json
```

每个备份旁边会生成 `*.manifest.json`，包含 `quick_check`、`integrity_check`、`foreign_key_check`、数据库 SHA-256、`user_version`、备份方法以及 RPO 记录。默认 RPO 以源文件 mtime 作为保守估计；应用可通过 `--last-commit-at` 传入最后一次提交时间，清单会将其标记为 `measured`。恢复报告包含 `rto_seconds`。损坏、截断、页数不一致、完整性检查失败或清单摘要不匹配都会在目标文件被触碰前拒绝。

运行可重复的故障演练（生成 fixture、损坏副本、确认拒绝，再恢复有效副本）：

```bash
python3 scripts/recovery_drill.py --output artifacts/recovery-drill.json
# 使用现有数据库并保留演练产物
python3 scripts/recovery_drill.py --database moyu.sqlite3 \
  --work-dir artifacts/recovery --output artifacts/recovery.json
```

演练退出码为 0 的条件是：损坏备份被拒绝、有效备份恢复成功、恢复后的 schema/user_version/各表行数快照与备份一致，并且报告含有限的非负 `rpo_seconds`/`rto_seconds`（自动生成的 fixture 还会校验哨兵记录）。可运行 fixture：

```bash
python3 tests/backup_recovery_fixtures.py
```

完整恢复的逐表核验、损坏包保护和恢复后继续写作见 `tests/import_archive_lifecycle_fixtures.py`；网页恢复见前述浏览器证据。RPO（可能丢失的最近写作时间）≤ 300 秒、RTO（恢复所需时间）≤ 900 秒是[验收目标](docs/evaluation-spec-v1.md)，不是所有真实规模已经达到的承诺。

## 历史回复诊断

在同一份冻结墨屿整理输入上，先后完成一次不分段回复和一次仅合并相邻用户消息边界的非流式诊断：`glm-5.1`、`thinking.type=disabled`、3000 输出 tokens、原600至850字门均未改变。两次均为 HTTP 200、模型名一致、`stop`、用量完整，但正文仍只有“铁”1字，并观察到非空 `reasoning_content`；第二次也未通过字数与关闭思考一致性闸门，因此没有继续发送稳定性请求或盲评。完整请求、字段统计和累计账本见[回复模式诊断](docs/audits/2026-10-13/literary-transport-v1/report.md)及[相邻消息合并诊断](docs/audits/2026-10-13/literary-message-shape-v1/report.md)。之后的 v13 诊断改测 `glm-5.2`，但未发送关闭思考参数，不能与此前结果混为同一对照。截至v13当轮累计37条预留、73118个可信完整tokens，10条缺完整用量，费用未知；最新累计以[v25诊断](docs/audits/2026-10-14/literary-pilot-v25-single-candidate/diagnosis.md)和[文学评测账本](docs/evaluations/README.md)为准，文学质量和第一梯队仍未证明。
