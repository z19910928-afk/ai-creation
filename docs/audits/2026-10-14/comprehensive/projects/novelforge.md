# NovelForge 与当前墨屿的专项复核

审查时间：2026-10-14，UTC 14:50:27，上海时间22:50:27。本报告由独立专责重新核对当前源码，不复用2026-10-13的墨屿缺失清单。

## 结论

当前差距已经不是“没有书架、任务队列、选区改稿、层级规划或章后记忆”。这些流程在墨屿现有代码中已经连接。修前最需先修的是：**已保存并建立章节的蓝图，没有完整进入续写、润色和故事审稿的真实材料。** 本轮用隔离作品及免费本机模型实际复现了这个断点；随后已实施有界当前指南，[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)中的10项真实HTTP与捕获验证通过。后续范围、别名、材料和章序保护已取得[独立专项结果](../evidence/README.md#范围材料与历史结果依赖)，最新兼容修复后的完整源码验收从[总入口](../README.md)查阅。

当前最新[指南与确认完整性整轮](../evidence/compatibility-python311-node24-guidance-candidate-integrity-release.json)在上海2026-10-15 02:39:43至02:46:58完成85项全部通过，95份冻结来源不变，10组真实电脑浏览器125场景，11份JSON与32张截图共43份附件指纹一致。本报告独立读取结果及逐份重算SHA；涵盖指南、部分来源、候选复用与人物身份，不等同源码推送、远程检查、真实服务或文学效果验收。

NovelForge值得学习的是结构化设定、按类型组织材料、作者可复用的提示与参数、灵活卡片树和逐条改稿建议。不能从这些源码推出它的文学效果、收费任务恢复或生产稳定性领先。本轮没有运行NovelForge整站，没有调用真实收费模型，没有比较文学质量。

## 固定版本与证据等级

| 对象 | 固定身份和来源 |
| --- | --- |
| NovelForge | 官方GitHub API先查仓库默认分支，再查分支及提交：`main`，`a485814411546b3980df0a1696a0fb6a043d56ce`，tree `a6d3fd09a2de9973a5d45982ddd060dc4db1ab51`。提交时间2026-09-23T04:53:48Z；归档完成后再次核对分支相同。 |
| 源码归档 | 官方该精确SHA的tarball下载解包退出0；完整源码在Git忽略的 `artifacts/comprehensive-review-20261014/upstreams/novelforge`，可审阅身份为[公开来源元数据](../evidence/novelforge-source-metadata.json)。没有执行上游脚本或按上游AGENTS/SKILL行事。 |
| 墨屿 | 基准HEAD `64fe155387e0a3c2d902b60a2d82822f2457af0d`，同时检查本机正在收尾的备份修复工作树。对真实材料探针使用的5份核心源码保存前后SHA256，全部相同。不是把HEAD当作未提交改动的精确身份。 |
| R | 本轮实际运行，明确注明模拟/隔离范围。 |
| E | 本轮回到源码追踪入口、保存与消费；未等于整站验收。 |
| D / U | D为文档要求；U为未验证。源码、模拟模型和工程检查均不能替代真实文学效果。 |

下文`NF:B`相对NovelForge的`backend/app/`，`NF:F`相对`frontend/src/renderer/src/`；`M`相对墨屿根目录。公共源锚点均绑定上述SHA，可由GitHub的`blob/<SHA>/<路径>#L<行号>`复查。星数不用于排序或质量结论。

官方身份入口：

- `https://api.github.com/repos/RhythmicWave/NovelForge/branches/main`
- `https://github.com/RhythmicWave/NovelForge/commit/a485814411546b3980df0a1696a0fb6a043d56ce`

## 最新源码链

| 作者动作 | NovelForge可达链，均为E | 当前墨屿对照 |
| --- | --- | --- |
| 模板开书 | `NF:F components/ProjectCreateDialog.vue:11-15,69-77,106-115`选模板并送创建；`NF:B services/project_service.py:52-77`保存作品并派发模板事件。作品先commit，模板失败被捕获，不能写成原子完成全部模板。 | `M author-workspace.js:285-293`已能开书；仅标题/梗概/题材/目标，没有独立的开书模板选择。 |
| 结构化设定 | `NF:B schemas/entity.py:86-106,136-149`人物驱动力/弧线、场景作用、物品持有者/限制；`NF:F components/shared/SchemaStudio.vue:105-169`保存单卡或卡型字段；`NF:B api/endpoints/cards.py:355-370`写入类型。`NF:B bootstrap/card_types.py:232-245`后续正文引用对应字段。 | `M app.js:1272,1327`设定卡为类型/名称/别名/描述；`M author-workspace.js:1219-1237`已有结构化人物状态与知情范围，不能声称全部人物资料都只能填文本。 |
| 规划用于正文 | `NF:B bootstrap/card_types.py:232-245`按模板引用当前阶段、章纲、上一章、下章计划和本卷写作指南；`NF:F components/editors/CodeMirrorEditor.vue:1283-1303,2582-2604`解析后真正放进`context_info`。这是E，不证明时点或知情过滤已正确。 | 修前 `M server.py:932-945`仅规划类任务额外读蓝图，见下方R；修后当前指南进入continue/polish/semantic_review，计划单独标记、过滤其他视角场景，10项HTTP验证通过。 |
| 普通正文提取、确认和再消费 | `NF:F components/editors/CodeMirrorEditor.vue:3448-3527`提取、可编辑预览、确认、刷新人物卡；`NF:B api/endpoints/memory.py:176-188`→`memory_extractors/character_dynamic.py:44-59`→`services/memory_service.py:630-733`；正文模板取dynamic_info。 | `M moyu_context.py:789-911`普通正文与精确证据校验；`M moyu_analysis.py:148-182,279-374`保存及人工决定；`M author-workspace.js:905-960`逐项处理。这条核心能力已补，不再列缺失；真实提取准确率仍U。 |
| 流、任务与恢复 | `NF:B services/workflow/engine/state_manager.py:27-79`运行/节点状态入库；`run_manager.py:114-119,154-196,299-319`使用进程内运行器，重启后恢复会重新启动。 | `M server.py:1656-1672`真实逐块落盘，`1724-1740`外部执行未知不盲目重发；`M app.js:886-905`刷新任务并接流。旧“墨屿页面未接队列”的结论已不适用。两方均没有本轮多节点生产调度或远端取消计费验收。 |
| 候选、修订与导出 | `NF:F components/editors/CodeMirrorEditor.vue:169-186,4203-4227`逐条接受/拒绝；`services/versionService.ts:14-38`每卡20条浏览器历史；`NF:B services/card_export_service.py`输出卡片TXT/MD/JSON。 | `M app.js:838-870`原稿/候选对照后原子采纳；`M author-workspace.js:1125-1146`服务器历史恢复为新版本；完整备份和MD/EPUB已有，不应为追随上游退回浏览器20条或删除现有证据边界。 |

## 修前实际材料断点：R

复现脚本在Git忽略的 `artifacts/comprehensive-review-20261014/blueprint-consumption-probe.py`；[公开修前捕获](../evidence/novelforge-blueprint-before-fix.json)包含真正发送到本机模型的3份请求与核心源码SHA256，私有原件继续保留。

命令：

```sh
python3 artifacts/comprehensive-review-20261014/blueprint-consumption-probe.py
```

该证据已存在时脚本拒绝覆盖；复测应另建新证据。运行使用真实`server.Handler`、独立临时SQLite、空密钥和免费本机HTTP提供方，保存蓝图→建立章节→写正文→真实队列→真实处理→捕获提供方请求；没有访问真实作品，没有付费请求。服务线程均关闭，临时目录已清理。

| 已保存蓝图字段的独特标记 | 续写实际请求 | 润色实际请求 | 故事审稿实际请求 |
| --- | --- | --- | --- |
| 读者、基调、创作约定、总纲 | 全部缺失 | 全部缺失 | 全部缺失 |
| 所在卷主要变化 | 缺失 | 缺失 | 缺失 |
| 当前稳定场景的地点、独立视角 | 缺失 | 缺失 | 缺失 |
| 当前场景目标 | 存在 | 存在 | 存在 |

修前三次任务均返回success，但成功仅证明模拟输出及请求链完成。修前根因：`M server.py:4147-4149`建立章节只转标题、目标、章视角、出口及“场景名：目标”字符串；`M moyu_context.py:716`不带独立场景地点/视角。`M server.py:942`只向outline/scene/brainstorm加入完整蓝图。独立Arboris专责也回读上述当时函数，确认E链一致；这些旧行号和失败捕获不代表修后源码现状。

这不是建议把整个蓝图无条件塞入上下文。蓝图还包含未来计划、未发生的事件和其他场景视角；全量注入可能破坏现有知情边界。修前`M server.py:1768-1801`基本审稿指纹也没有蓝图字段。修后将“创作约定、当前卷章场景指南”和“可被角色知道的事实”分区，指南身份进入创建、派发、重试、采纳及审稿校验；空指南不占固定预算，预览=发送、材料字节统计和超预算拒绝均由[10项HTTP测试脚本](../../../../../tests/blueprint_guidance_http_fixtures.py)及[指定快照成功结果](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)验证。后续别名扩展11项、范围扩展14项与材料扩展29项已分别通过，计数与源码快照另见[能力矩阵](../capability-matrix.md)及依赖索引。

旧任务依赖还包括实际消费的资料与章序，而非只有蓝图字段。[章序专项9项](../evidence/material-scope-order-after-fix.json)覆盖删除早章后的派发、采纳与原任务两种重试，以及无正文证据伏笔的已消费标题。真实旧AI分析缺材料标记时保留只读，旧审稿可免费按当前全作品资料解释；资料在生成期间改变后完整结果与用量仍保存，不能沿原任务重发。这些是工程边界，不证明模型遵循规划或文学收益；当前完整结果使用85项新整轮。

后来[77项材料修后整轮](../evidence/compatibility-python311-node24-materials-comprehensive-refined-release.json)虽然取得122个电脑场景和40附件一致，继续复核仍发现章后分析未冻结原指南：只改故事前提或创作约定、不重新建立章节，旧summary/handoff/candidate仍能确认；缺指南审稿与双缺旧说明也未正确失效。[章后12项首修后](../evidence/analysis-guidance-after-fix.json)和[审稿11项首修后](../evidence/review-guidance-after-fix.json)均通过，原反例、正常自身确认、他书隔离和最小组件边界见[指南阶段](../evidence/README.md#保存报告的创作指南依赖)。原guidance保留，决定后当前guidance单独记录，不因确认交接或新增人物使正常后续确认失效；外部改动仍先拒绝。77项是发现该问题前的中间快照，当前85项整轮已经覆盖后续修复。

部分私有来源另有独立反例：无实际run却只剩指南、孤立或空决定标记时仍可确认。[原8项修前](../evidence/analysis-partial-source-before-fix.json)6个测试组失败、2个通过；[冻结同8项修后](../evidence/analysis-partial-source-after-fix.json)全通过，前后0请求、0收费、11份来源不变。决定来源不能代替原来源，原值只需完整而不因作者正常自身决定被替换；仅指南旧审稿的免费说明也绑定当前全作品材料，人物说明变化后旧理由失效。原审稿11项最新复验通过，计数与额外形状校验限制见[部分来源阶段](../evidence/README.md#部分私有来源与组件兼容边界)；两项均纳入当前85项整轮。

跨报告正常确认另取得[复用7项](../evidence/analysis-candidate-reuse-after-fix.json)、[五类记录13项](../evidence/analysis-candidate-typed-after-fix.json)及[人物身份8项](../evidence/analysis-character-identity-after-fix.json)同集合修后通过，分别15、72、13次免费、0收费。未变内容复用原正式记录，撤回/改动/歧义/来源损坏均原子拒绝；状态主体和关系两端须当前人物。[指南电脑3场景](../evidence/analysis-guidance-browser-after-fix.json)也通过，3次免费、0收费。13为正式测试组，36为观察场景、29为显式子测试，42条结果不等于42测试；早期隔离错误与各自修前在[完整性索引](../evidence/README.md#跨报告确认复用证据与人物身份)区分，不扩成自动动态资料质量或文学成绩。

## 八项修前差距与当前进展

P1优先形成正确的作者流程；P2是效率与可复用能力。没有为凑功能数把上游代码编辑器、任意脚本工作流或云端协作设为墨屿必补项。

| 编号/优先级、证据 | 作者影响 | 最小修改范围 | 可执行验收 |
| --- | --- | --- | --- |
| NF01 / P1 / 修前R；本轮已实施并专项验证 | 修前三次真实请求缺读者承诺、卷目标和场景地点，原证据保留。 | 已在`moyu_context.py`与`server.ai_context`共用有界指南，按当前卷/章/场景身份选取，区分计划与事实，并绑定指南来源。 | 10项实际HTTP覆盖continue/polish/semantic_review、范围/预算、预览=发送及改指南失效；后续材料/范围专项另有通过结果，最新整轮见总入口，不能据此证明文学提升。 |
| NF02 / P1 / E：设定缺可编辑的稳定字段 | 人物动机、场景作用、物品规则要从长描述反复寻找；难选择只给模型本次需要的字段。`NF:B schemas/entity.py:86-106,136-149`对`M app.js:1327`。 | 先给人物增加驱动力/弧线，场景增加作用/规则，物品增加限制/持有者；沿用稳定实体ID、原句与范围元数据。旧description无损兼容，不先做任意代码或无限Schema。 | 旧卡升级不丢字；不同类别字段可保存/刷新/备份恢复；选择指定字段后，真实生成消费；秘密字段按人物及时点隔离；改名不产生第二个人物。 |
| NF03 / P1 / E：用途模型已有，任务参数未形成作者可保存的预设 | 审稿想要低变化、创意想要高变化时，要改全书全局设置。`NF:F components/common/AIPerCardParams.vue:15-42,122-153`有单卡/类型保存；`M moyu_settings.py:192-212`只接一套parameters，虽然`moyu_provider.py:250-266`已支持内部task_parameters。 | 扩展现有设置持久化和表单，先实现“正文/规划/审稿”三类参数，不新造调用层；将实际生效预设与版本冻结入任务。 | 同书三个任务可保留不同温度/输出限额；温度0有效；刷新保持；切书隔离；旧任务重试沿原预设，改预设不篡改历史请求；预览与实际请求一致。 |
| NF04 / P2 / E：缺可复用的提示/写法库 | 口语对话、压缩解释、感官描写等要求反复输入，作者成功用过的写法不能命名复用。`NF:F components/setting/PromptWorkshop.vue:39-96`＋`NF:B services/prompt_service.py:28-110`有编辑、变量、保存和重置；`M server.py:523-550`仅固定任务提示+全局风格+一次要求。 | 增加有限用途的作者要求模板、变量校验及追加版本，保留系统任务/输出/证据硬约束；不用上游模板文本。 | 建模板、预览变量、保存、选择、真实发送；缺变量在调用前报错；修改成新版本；历史候选能追溯旧版；导出无凭据，跨书复用需明确复制或引用。 |
| NF05 / P2 / E：结构可上下移动卷章，但场景难重组、章节难跨卷移动 | 长篇改纲常需把场景移到别章，当前需要人工重建，稳定来源关系易丢。`NF:F views/Editor.vue:835-878`→`NF:B api/endpoints/cards.py:255-284`批次保存parent/order；`M author-workspace.js:433-469`只有卷/同卷章移动，场景仅编辑。 | 给现有稳定ID结构增加场景排序及跨章移动、章跨卷移动；先提供按钮/菜单即可，不以拖拽为验收；移动预览受影响身份/指南/已写正文。 | 移动后ID不变、正文逐字保持，刷新及备份往返相同；重新建立章不重复；旧候选和依赖被标明过期；并发冲突保双方；不能静默改错人物知情时点。 |
| NF06 / P2 / E：缺可选的开书准备模板 | 新手立项仍要自己猜先准备哪些资料；四阶段规划存在，开书准备没有按类型形成可复用默认材料。`NF:F components/ProjectCreateDialog.vue:11-15`对`M author-workspace.js:285-293`。 | 先提供空白/通用长篇/悬疑等少量独立设计模板，预览将创建的契约与卡片；模板只建准备数据，不默认调用模型或写正式事实。 | 选模板建新书、刷新后结构齐全；空白不添强制网文设定；取消/失败不留半套；重复提交只建一套；现有作品和真实正文不变；后续规划消费模板约定。 |
| NF07 / P2 / E：电脑长时间阅读缺字号/行距偏好 | 当前正文textarea与字号样式固定；作者不能按屏幕/阅读习惯调排版。`NF:F components/editors/CodeMirrorEditor.vue:8-39,1868-1888`持久化字号/行距；`M author-workspace.js:77-109,153-158`只有基本格式操作。 | 在现有编辑器增加字号、行距、可选专注显示的本机偏好；不为此替换编辑器、改变保存契约或正文。 | 1440×1000与1280×900真实页面调节、刷新保持；长中文和emoji输入、撤销、选区、候选范围不变；未保存草稿不因切模式丢失；控件不重叠。 |
| NF08 / P2 / E：候选对照已有，单次多处建议缺逐条裁决 | 一个润色候选包含多个改动时只能整段确认或手改候选，很难只接受一句。`NF:F components/editors/CodeMirrorEditor.vue:169-186,4203-4227`有逐条建议；`M app.js:838-870`当前对整份replacement确认。 | 复用现有范围补丁与幂等应用，增加同一来源快照的独立建议项、差异和逐项接受/拒绝；先处理不重叠范围，重叠项要求重算。 | 三处建议只接受第二处，第一/三处逐字不变；重复句第二处、emoji和跨段范围正确；双击不重复；范围原文变化拒绝旧项；不因逐项接受误发新收费请求；撤销保留版本。 |

NF08是逐条改稿能力，不是“NovelForge已证明多模型多候选竞赛”；本轮没有找到证据允许这样扩展结论。NF01已实施并专项验证，NF02至NF08仍需实施与新验收，报告本身不代表完成。

## 旧报告结论的当前纠偏

本轮重新读`M app.js`、`author-workspace.js`、`server.py`、`moyu_workspace.py`、`moyu_planning.py`、`moyu_settings.py`、`moyu_context.py`、`moyu_analysis.py`、`moyu_backups.py`及现有roadmap后，以下不能再写成缺失：

- 作品书架/新建/复制/归档/切换：`M author-workspace.js:248-311`。
- 分阶段规划和稳定卷章场景：`M moyu_planning.py:25-29,444-493,890-973`；`M author-workspace.js:336-571`。
- 选区来源、原稿/候选对照、原子幂等采纳：`M app.js:463-479,838-870`。
- 实时任务/失败片段回看/执行未知保护：`M app.js:176-237,886-905`与`M server.py:1656-1740`。
- 普通正文证据化记忆及语义审稿：`M moyu_context.py:789-911,930-1040`、`M moyu_analysis.py`及`M author-workspace.js:905-1097`。
- 完整作品备份/隔离恢复：`M moyu_backups.py:89-124`及后续移植；`M author-workspace.js:669-708`。恢复名称、失败重开与长标题已在[指定53项浏览器快照](../evidence/frontend_browser_regressions-python311-node24-comprehensive-workflow-refined-final.json)通过；本报告没有把该工作树或局部成功冒充已发布提交及后续源码验收。

这些是本轮E以及已有roadmap记录的指定运行范围；本专责没有重跑全部电脑场景，也没有验证真实4173服务已加载这次后端修改。

## 两项目独立交叉复核

原`.audit-cache`已不存在，以下从官方GitHub`contents` API的**旧报告精确SHA**重新取原源码并读行，没有只读报告摘要，也没有执行上游技能。此处复核旧结论，不冒充两项目当下最新分支审查。

| 旧报告关键主张 | 独立复核结果与原源码锚点 | 边界 |
| --- | --- | --- |
| Arboris选择成功不代表入库成功 | 确认E：`dd80c69bc30a80d68b375bd504c6bc308164ff9e`，`backend/app/api/routers/writer.py:833,845-855`先选版本，调用ingest缺`user_id`并吞错；`backend/app/services/chapter_ingest_service.py:38-47`该参数必需且keyword-only。 | 本轮没有运行其数据库/HTTP；不把旧组件R写成本轮整机R。 |
| Arboris六维失败默认高分 | 确认E：同SHA，`backend/app/services/six_dimension_review_service.py:58-60,87-97,145-163`缺模板/解析失败进入overall80、维度100、零问题。 | 不声称每类网络异常都走此分支；不据此贬低实际小说质量。 |
| webnovel v8审读指纹拒绝旧稿回写 | 确认E：`a0bdd2d4e2f39bc3de32fc5cbfe605067b5b486b`，`packages/review/src/run.ts:151-174,268-299`从实际读入快照算指纹，自动回写缺指纹或不匹配拒绝；作者意见分开。 | 未执行DSH宿主或真实模型，指纹不证明语义审读准确。 |
| webnovel v8选区请求不能当跨重启任务 | 确认E：同SHA，`packages/bundle/src/study/editor-requests.ts:161-198,263-285,376-425`WeakMap明确仅内存，存在取消/重复/引文保护。 | 不从snapshot字段推导耐久性；没有运行该完整编辑UI。 |

精确API路径示例：

```text
repos/t59688/arboris-novel/contents/backend/app/api/routers/writer.py?ref=dd80c69bc30a80d68b375bd504c6bc308164ff9e
repos/t59688/arboris-novel/contents/backend/app/services/chapter_ingest_service.py?ref=dd80c69bc30a80d68b375bd504c6bc308164ff9e
repos/t59688/arboris-novel/contents/backend/app/services/six_dimension_review_service.py?ref=dd80c69bc30a80d68b375bd504c6bc308164ff9e
repos/lingfengQAQ/webnovel-writer/contents/packages/review/src/run.ts?ref=a0bdd2d4e2f39bc3de32fc5cbfe605067b5b486b
repos/lingfengQAQ/webnovel-writer/contents/packages/bundle/src/study/editor-requests.ts?ref=a0bdd2d4e2f39bc3de32fc5cbfe605067b5b486b
```

## 不宜照搬与未测项

NovelForge运行状态持久化成立，但运行器仍在进程内；`run_manager.py:299-319`重启恢复是重新启动，不能作为收费请求不会重复的实测保证。其卡片历史是localStorage每卡20条（`NF:F services/versionService.ts:14-38`），不能替代墨屿追加修订与完整备份。

`NF:B services/context_service.py:164-170`关系取料`max_chapter_id=None`，动态状态仍需时点与知情核验；其卡型正文模板直接含未来章计划，不能照抄并宣称防剧透。`NF:B services/foreshadow_service.py:15-52`伏笔建议为正则启发式，不代表全书兑现闭环。

未运行NovelForge完整网站、桌面包和完整测试；未安装DSH或Arboris整站；未调用收费模型；未测真实长篇召回、语义审稿precision/recall、作者改稿耗时或文学偏好。保留墨屿现有授权预算、失败候选与账本，本轮没有新增文学请求。**这份报告证明差距与修复方向，不证明第一梯队已经达到。**
