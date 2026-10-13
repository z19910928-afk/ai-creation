# 91Writing 与 NovelForge 独立交叉复核

日期：2026-10-13。复核者：`/root/audit_storyforge`。复核对象：

- `./.audit-cache/2026-10-13/agent-91writing.md`
- `./.audit-cache/2026-10-13/agent-novelforge.md`

复核固定版本：91Writing `df3de0362e8e4028b76dffa78c5bba7b8bbd20a1`；NovelForge `a485814411546b3980df0a1696a0fb6a043d56ce`；墨屿 `62f1c66a7b16bb84f2940dc1af48e685aaf58818`。本次沿作者入口、调用、处理、保存、后续消费独立复读源码，没有修改三个产品、运行完整上游应用、推送、发布或真实模型请求。未运行91Writing的`api-test.js`，不复述其凭据。

路径缩写：`91/`为`./.audit-cache/2026-10-13/references/91Writing/`；`NF/`为`./.audit-cache/2026-10-13/references/NovelForge/`；`墨屿/`为`./`。表内文件路径和行号均对应上述固定文件。

证据等级：R为实际执行，必须带实际运行范围；E为源码调用及保存/消费可达；S为字段或结构；D为文档；U为未验证。上游专责已执行的探针仅作为二次复读材料，不冒称本交叉复核重新执行。文学效果、真实计费、完整页面、手机布局、完整构建与生产可用性均未由本交叉复核验证。

## 复核结论

两份专责报告的主要产品判断与固定源码一致：91Writing的多作品、作者选材料、提示词库可以作为操作设计参考；NovelForge的层级卡片、选区预览、提取预览确认以及确认资料用于后续创作确有源码链。二者均不能由这些证据升级为可靠长篇写作或文学质量标杆。

复核了91Writing的12个重要主张、NovelForge的12个重要主张。主要纠偏是证据范围，未发现需要推翻两报告整体判断的反证：

1. 91Writing旧`Home`/`BackupManager`不在当前路由；设置页另有真实JSON导出与导入，不能从旧组件丢数据推断“91Writing所有备份不可恢复”。
2. 91Writing初始化和切章探针使用真实函数，但有替身和省略；初始化存储空窗有E链，切章编辑器误覆盖有隔离R，完整页面及持久误写没有该R证据。
3. NovelForge旧`scheduler.py`探针不能代表当前工作流。当前`WorkflowRuntime`确有排队取消/暂停代码，数据库确有运行和节点状态；进程内控制、意外崩溃接续、收费动作不重复是不同问题。
4. NovelForge动态信息不是孤立提取：作者预览、确认、写入人物卡、刷新、下次上下文消费均可达E；来源修订、故事时点及重复确认边界仍不足。
5. 墨屿后台范围补丁、任务队列、取消、流回放已经存在；作者网页未接好与后台不存在必须分开。墨屿本轮浏览器错章和长章候选覆盖不能写成“已持久化丢稿”，根代理观察到数据库拒绝保存且保留原稿。

## 91Writing：12项独立复核

| 主张 | 裁决 | 独立复读源码与边界 |
| --- | --- | --- |
| 当前写作入口是`Writer.vue`，不能把旧Home或重构文件算进当前能力 | 确认E | `91/src/router/index.js:11,64-66`导入并注册Writer；Home在`:12`只有导入，没有对应route。`Writer_refactored.vue`未被当前路由使用。此结论决定备份、摘要等旧组件是否可达。 |
| 多作品创建、保存及进入写作存在 | 确认E | `91/src/views/NovelManagement.vue:1203-1243`表单校验、创建作品及材料数组、`:1234`保存、`:1242`进入`/writer?novelId=`；`:611-617`写`localStorage.novels`。这是真实浏览器持久化设计，不是服务端作品数据库，也未由本复核进行完整UI操作。 |
| 打开作品初始化时会先保存尚未装载的材料 | 确认缺陷E；限制R | `91/src/views/Writer.vue:7637-7671`加载章节后先`saveNovelData()`；人物到`:7675`、世界到`:7678-7683`、语料与事件到`:7685-7686`才加载。`:7607-7633`以当前材料数组覆盖作品。实际`:2451-2454`选章先保存当前章，首次进入当前章为空时不会消除此顺序缺陷。专责探针把`selectChapter`置空，证明隔离初始化存储空窗；不能称完整页面实测或材料永不恢复，后续正常保存可能恢复内存材料。 |
| API配置页随机连接测试，设置页还存在真实`/models`链 | 确认E，拒绝泛化“全是假测试” | `91/src/views/ApiConfig.vue:613-632`的连接结果来自`Math.random()>0.3`与定时器，未发请求；`:161,325`有作者触发入口。另一组件`91/src/components/ApiConfig.vue:567-596`经store到`91/src/services/api.js:799-809`真实GET`/models`。`91/src/views/Settings.vue:354-357`全测仍仅提示。报告应分别标注。 |
| 保存参考库仅成功提示，未形成刷新后可使用的参考库 | 确认E缺口 | `91/src/views/BookAnalysis.vue:206,249`按钮触发`:1184-1186`，函数只调用成功消息，无写入、请求或后续读取消费。不能把拆书结果页与持久参考库等同。 |
| 孤立BackupManager丢正文，所以当前设置页备份也不可恢复 | 前半确认；后半驳回 | `91/src/views/Home.vue:382,410`挂旧BackupManager，Home无当前路由。`91/src/components/BackupManager.vue:603-609`删掉data只保存元数据，`:566-579`只保存自动备份开关。当前`91/src/views/Settings.vue:391-411`真实下载包含novels的JSON，`:507-548`确认后真实写回localStorage；未做严格结构校验。允许报告旧组件缺陷，不能否定设置页已有导出/导入E链。 |
| 正文生成期间切到另一章，最终返回仍可能覆盖当前章 | 确认缺陷E；隔离R不升级持久化 | `91/src/views/Writer.vue:5126`记生成章，`:5337-5340`仅流回调校验章ID；`:5347-5350`最终无条件写当前内容及状态，`:5354-5356`延时保存。`:2466-2472`把内容写入调用时的currentChapter并保存作品。专责脚本只模拟editor，保存与timer为空，R仅证明编辑器被A结果改写；实际持久错写是E风险，不是该探针已跑出的事实。 |
| 选区候选未锁定原请求范围，接受时重新读当前选区 | 确认E | `91/src/views/Writer.vue:6141-6160`读取选中文字作为原文；`:6464-6484`接受时再调用`getSelectionText()`和`insertText()`，再定时保存。未见请求章/原文范围/修订hash校验；移选区或改稿后的正确落点未验证。 |
| 换行被清理、温度0失效、null长度回退、超时固定 | 确认E；模拟R范围正确 | `91/src/services/api.js:216`删除包含换行的控制字符；`:231`用`||`回退maxTokens；`:248`用`||`令温度0回退；`:266`固定5分钟超时。专责捕获2000000/0.7是其沙箱配置下的请求值，不能据此声称所有真实模型均接受或产生该长度。 |
| “停止”仅改变界面状态，不能证明上游请求及计费已停止 | 确认E | `91/src/views/Writer.vue:2946-2954`只清状态与候选；Writer/api链未找到对应AbortController或abort，API服务使用的是`:266`超时信号。最终完成写入缺乏停止状态检查。应写“未接通主动中止”，不写“已停止实际计费”。 |
| 人物、世界、语料、事件、前文选项确实进入正文提示 | 确认E；拒绝升级为长期记忆 | `91/src/views/Writer.vue:5168-5220`读取作者手选材料并拼提示，`:5324-5328`进入请求。该链是材料选择，不是语义检索、来源证据、事实失效和修订重锚系统。提示内一致性要求不构成验证。 |
| 提示词保存、导入、变量及Writer消费存在 | 确认E；保留入口缺陷 | `91/src/views/PromptsLibrary.vue:524,1331`保存，`:585-696`导入处理；Writer`:2970`加载、`:3791`提取变量、`:3363`替换、`:5114`消费。Writer`:7890`“刷新”只弹窗，`:7896`跳`/prompts-library`但路由`router/index.js:29`为`/prompts`。不能因局部入口错误否定提示词库整体，也不能称路径全部健康。 |

91Writing探针的独立复读材料：`./.audit-cache/2026-10-13/probe-91writing.cjs`及`probe-91writing-results.json`。脚本模拟fetch、使用占位配置、没有真实模型请求；本交叉复核未重跑其四项结果。其初始化与切章模拟范围必须与专责报告运行段一起呈现。

## NovelForge：12项独立复核

| 主张 | 裁决 | 独立复读源码与边界 |
| --- | --- | --- |
| 旧scheduler的FIFO及取消结果代表当前全部工作流 | 驳回泛化；旧类探针保持组件R | `NF/backend/app/api/endpoints/workflows.py:521-556`实际注册task、获取槽位、创建并注册执行器均走WorkflowRuntime；`NF/backend/app/services/workflow/engine/run_manager.py:114-119,154-196`和`NF/backend/app/services/workflow/triggers.py:210-212,328`也用该runtime。旧scheduler原类R可说明旧代码，不能证明当前全部工作流排队不可取消或无恢复。 |
| 工作流运行及节点输出有数据库保存与恢复 | 确认E；完整重启U | `NF/backend/app/services/workflow/engine/state_manager.py:27-79`commit运行状态与state_json；`execution_state.py:125-132`恢复成功/跳过节点输出、`:150-201`保存节点输出及checkpoint；`async_executor.py:127-164`读取恢复状态。不能把这条E写成全流程重启实测通过。 |
| 调度控制仍进程内，无执行器时可以恢复暂停任务 | 确认E；限制恢复条件 | `NF/backend/app/services/workflow/engine/runtime.py:31-40`保存线程并发槽、task/executor、pending/cancel/pause集合；`:66-100`等待槽位会检查取消/暂停。`run_manager.py:299-319`仅run为paused时可在无executor情况下重新`start_run`。意外崩溃留下running、自动扫描接续、收费调用幂等均未由此证实，不能宣称外部耐久队列。 |
| 作者有暂停、恢复与取消入口 | 确认E；当前页面完整运行U | `NF/frontend/src/renderer/src/components/workflow/dialogs/WorkflowRunsDialog.vue:291-331`真实POST pause/resume/cancel，`:311-317`发resume-run事件。该入口与当前runtime链相连；完整UI、断线及服务重启未由本复核操作。 |
| 动态提取、可编辑预览、确认及人物卡持久保存接通 | 确认E，驳回“孤立提取” | `NF/frontend/src/renderer/src/components/editors/CodeMirrorEditor.vue:3448-3468`发送正文并打开预览，模板`:299,403`提供确认入口；`:3475-3517`清理候选、updateDynamicInfoOnly并刷新。`NF/backend/app/api/endpoints/memory.py:176-188`到apply_preview；`NF/backend/app/services/memory_extractors/character_dynamic.py:44-59`到`memory_service.py:630-733`commit人物卡。模型提取准确率与真实页面U。 |
| 确认后的动态状态被后续生成消费 | 确认E | `NF/backend/app/bootstrap/card_types.py:232-244`正文模板按entity_list取角色dynamic_info与其他实体状态，前后章/阶段材料一并构造；`NF/frontend/src/renderer/src/components/editors/CodeMirrorEditor.vue:1283-1303`解析材料模板，`:2585-2604`把context_info放进生成请求。不能把有消费升级为正确时间线或知识边界。 |
| 动态状态缺少来源、时点与幂等，满额可能丢新项 | 确认E风险 | `NF/backend/app/schemas/entity.py:32-35`动态项仅id/info；`NF/backend/app/services/memory_service.py:643,668-670`按项目/标题找人物卡；`:694-707`直接append并补ID；`:709-716`心理快照保留最后N条，其他类别保留前N条，满额时新项会被裁掉。`:659,729`删除与新增分开commit。不是每次确认必丢，也不能声称所有类别都丢最新。 |
| 卡片版本历史是浏览器每卡20条 | 确认E，并新增独立组件R | `NF/frontend/src/renderer/src/services/versionService.ts:14-38`读写localStorage、unshift后slice(0,20)；实际保存与恢复入口是`NF/frontend/src/renderer/src/components/cards/GenericCardEditor.vue:859-881,1357-1379`，路径为cards而非editors。新增探针直接import原TS模块，在内存localStorage加入25条只留下6至25，详下文；清浏览器存储会消失，不能当服务器修订。 |
| 伏笔建议是正则启发式，未找到作者UI消费 | 确认E/U边界 | `NF/backend/app/services/foreshadow_service.py:15-52`启发式建议，`:55-102`CRUD。前端搜索`foreshadowSuggest`仅`NF/frontend/src/renderer/src/api/ai.ts:146-174`定义及生成类型，未找到Vue调用。专责人物误抽R只代表该原方法；后台CRUD不等于作者埋设/发展/回收闭环。 |
| 选区改稿有预览接受/拒绝，但续写仍直接追加 | 确认E | `NF/frontend/src/renderer/src/components/editors/CodeMirrorEditor.vue:2904`传本次选区、`:2907-2940`接受/拒绝、`:2964-3013`在原文后展示候选和高亮；`:3015-3018`续写直接追加。不能概括为所有任务均先确认，也未由本复核证明生成等待期间改稿后的修订安全。 |
| 关系材料没有当前章上界，阶段不合格仍清源列表 | 确认两项E，未升R | `NF/backend/app/services/context_service.py:164-170`query_subgraph传`max_chapter_id=None`，`:173-176`参与者过滤不是时间过滤。`NF/backend/app/bootstrap/workflows/阶段大纲.wf:24`校验失败返回[]，`:30-53`两次BatchUpsert，`:59`wait，`:64-69`仍清空源大纲；`NF/backend/app/services/workflow/nodes/card/batch_upsert.py:95-98`空列表正常不循环。完整工作流未运行，不写“已实测丢大纲”。 |
| 审稿保存目标快照、提示词与模型 | 确认E；改稿有效性及出版门禁U | `NF/backend/app/services/review/review_service.py:113-141`构造target_snapshot、prompt_name、llm_config_id、reviewed_at等；`:294-319`保存审核卡。实际文件在services/review/下。保存快照不自动证明改稿后审核失效，更不能拿这条E当文学审稿正确率。 |

NovelForge专责探针的复读材料：`./.audit-cache/2026-10-13/novelforge-component-probe.py`及`novelforge-component-probe-result.json`。旧scheduler顺序`[1,2,3]`和排队取消false只对应旧原类；正则人物误抽只对应suggest方法。两项均非本交叉复核新跑、非当前Runtime整体R。

## 本交叉复核新增R

探针：`./.audit-cache/2026-10-13/cross-storyforge-version-probe.mjs`。

结果：`./.audit-cache/2026-10-13/cross-storyforge-version-probe-result.json`。

运行方式：机器已有Node v24.19.0直接import NovelForge原`versionService.ts`，Node内建去除类型声明，仅提供内存localStorage替身。没有复制重写版本函数、加载Vue/数据库、联网、收费或修改产品存储。实际通过的断言：

- 连续保存25条，同一作品同一卡只剩20条，最新为25，最旧为6。
- 其他作品与其他卡片查询为空。
- 清掉内存localStorage后历史为空；恢复已序列化数据后20条恢复。
- 损坏JSON返回空历史，不抛异常。

R的有效范围是原版本服务模块和模拟浏览器存储；不是完整作者页面恢复、跨设备恢复、服务器耐久性或生产备份。

## 墨屿统一纠偏

本节复读当前墨屿源码，并使用根代理已明确描述的浏览器观察；未冒称重新进行其页面探针：

- `墨屿/server.py:3502-3528`有state version及branch head冲突检查；前台保存错乱不能因此直接解释为数据库已丢稿，必须查响应与数据库。根代理观察的切章晚到候选采纳只改浏览器乙章，数据库409拒绝且保留原稿。
- `墨屿/server.py:3758-3783`范围补丁要求base revision/hash并校验，`:3785`按范围拼新正文。后台局部改稿已经存在；`墨屿/app.js:71-73`网页当前同步JSON候选与整章采纳没有接入该范围保护，缺的是作者链及候选绑定。
- `墨屿/server.py:3895-3929`有queue与run行、`:3944-3950`有SSE；同步分支先完成generate、record与chunks保存后才发送事件，因此不是“完全无流”，也不是“上游产生一片就实时给作者一片”。
- 根代理最新长章观察：15412字正文提示没有章首哨兵，90字模拟润色候选替换浏览器整章；数据库仍保留15412字且冲突。这支持截取提示与整章采纳危险、保存前后不一致，不支持已持久丢稿。
- 起稿直接函数在已有作品状态可覆盖，但本交叉代理此前复读正常页面只在新项目显示该入口。直接状态探针不能推断普通作者正常路径必现覆盖。

汇总报告建议把“已有后台基础”“作者入口接通”“隔离函数复现”“真实页面复现”“数据库持久结果”“长篇/文学实测”分别标注。两份专责报告可以纳入总审，以上限定必须同步保留。
