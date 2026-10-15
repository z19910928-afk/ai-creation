# 桌面长篇写作工作台：七项目对照与实施建议

研究日期：2026-10-15，上海时间。范围仅为电脑端。

## 结论

墨屿应成为“以正文为中心、知识可关联、写作总管贯穿全过程”的桌面工作台。最值得吸收的是 NovelForge 的三栏关联工作区、MuMu 的全项目助手和执行过程、Arboris 的创意到章节流程、Ai-Novel 的生成前材料核对、91Writing 的材料与写法复用、webnovel-writer 的章节提交门，以及 storyforge 的卷章场景一体编辑。**不是把七个项目的菜单合并，也不是另造一个 Agent 聊天页面。**

当前墨屿已有规划、目录、真实正文、候选采纳、版本、记忆确认、审稿、完整备份与 LangGraph 写作总管。应先让这些能力形成一条清楚的作者动线，不应为追随参考项目而删除已有的来源、过期检查、费用未知保护和本地恢复能力。

### 研究边界

- 本轮重新请求官方 GitHub 分支提交，阅读对应源码与页面挂载链；未安装、运行七个上游完整应用，未进行其真实作者体验或收费模型测试。
- 下文“支持”“入口”均指源码可见，不代表生产稳定性、文学质量或人工效率已实测。布局判断来自页面与组件结构，不冒充截图体验。
- `comparison-six-projects-2026-10-12.md` 实际包含 NovelForge 和另外五个项目。为满足“NovelForge 和其他六个”，补入 2026-10-14 全面审查已经覆盖的 storyforge，共七个，不重复计数 NovelForge。
- 上游研究过程未读取或使用真实 API 密钥，未发送模型请求。墨屿工作台的独立实现与本地回归另行记录，不能作为七个上游已运行的证据。

## 固定来源

本轮核对到的官方分支身份如下。分支可能继续更新，下面的具体提交链接用于复核，不使用星数或功能数排序。

| 项目 | 本轮读取版本 | 本轮重点 |
| --- | --- | --- |
| [NovelForge](https://github.com/RhythmicWave/NovelForge) | `main` / [`a485814411546b3980df0a1696a0fb6a043d56ce`](https://github.com/RhythmicWave/NovelForge/commit/a485814411546b3980df0a1696a0fb6a043d56ce) | 卡片树、正文工作区、右侧上下文与助手、逐条改稿 |
| [MuMuAINovel](https://github.com/xiamuceer-j/MuMuAINovel) | `main` / [`99f0aec6ce3b1c65652d20007be930ebba6a6d20`](https://github.com/xiamuceer-j/MuMuAINovel/commit/99f0aec6ce3b1c65652d20007be930ebba6a6d20) | 全项目 Agent、执行步骤、工具确认、章节分析和局部重写 |
| [arboris-novel](https://github.com/t59688/arboris-novel) | `main` / [`dd80c69bc30a80d68b375bd504c6bc308164ff9e`](https://github.com/t59688/arboris-novel/commit/dd80c69bc30a80d68b375bd504c6bc308164ff9e) | 蓝图、章节目录、生成/评估/版本选择、定向优化 |
| [Ai-Novel](https://github.com/inliver233/Ai-Novel) | `dev` / [`8b85b2b6f29af4c69241be7c42417dbe4795db30`](https://github.com/inliver233/Ai-Novel/commit/8b85b2b6f29af4c69241be7c42417dbe4795db30) | 章节列表、写作表单、生成前材料与提示检查 |
| [91Writing](https://github.com/ponysb/91Writing) | `main` / [`df3de0362e8e4028b76dffa78c5bba7b8bbd20a1`](https://github.com/ponysb/91Writing/commit/df3de0362e8e4028b76dffa78c5bba7b8bbd20a1) | 章节状态、人物/世界/语料、可保存写法库 |
| [webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) | `master` / [`86e617ee7f4f04be9d3f8575f90c38f879ed80ed`](https://github.com/lingfengQAQ/webnovel-writer/commit/86e617ee7f4f04be9d3f8575f90c38f879ed80ed) | 写前/提交/写后门、真源与派生资料、健康检查 |
| [storyforge](https://github.com/yuanbw2025/storyforge) | `main` / [`e8ce0a0dbab8b25be6851cf7d77c9e7c4786ed2b`](https://github.com/yuanbw2025/storyforge/commit/e8ce0a0dbab8b25be6851cf7d77c9e7c4786ed2b) | 卷章场景一体工作区、全局助手、计划确认与候选 |

前五个提交与既有全面审查相同。webnovel-writer 的 `master` 同样沿用全面审查中的 `86e617`，它相对10月12日早期研究的 `0af0c0` 已更新；不能把不同参照混成全面审查后再次更新。storyforge 则从全面审查的 `5147924` 更新至本表 `e8ce0a0`，本轮直接读取新提交的章节面板和助手，不把旧提交的全部审查结论扩展为新版本已验证。webnovel-writer 的 `master` 与既有审查中的 v8/DSH 宿主方案分开：旧 v8 结论只作历史参考，不能用来声称当前 master 自带完整正文编辑器。

### 许可对照

许可结论只针对上表固定提交。前五项沿用同一提交的既有只读快照核对；webnovel-writer 与 storyforge 的上表提交另从官方 GitHub Contents 接口读取许可文件，不用仓库主页的当前标签替代。

| 项目 | 固定提交的许可证据 | 本项目处理 |
| --- | --- | --- |
| NovelForge | 根目录 [`LICENSE`](https://github.com/RhythmicWave/NovelForge/blob/a485814411546b3980df0a1696a0fb6a043d56ce/LICENSE)：AGPL-3.0。 | 研究产品动线，独立实现，不引入其源码或提示词。 |
| MuMuAINovel | 根目录 [`LICENSE`](https://github.com/xiamuceer-j/MuMuAINovel/blob/99f0aec6ce3b1c65652d20007be930ebba6a6d20/LICENSE)：GPL-3.0。 | 研究总管交互与任务阶段，独立实现。 |
| arboris-novel | 同提交快照未发现正式 `LICENSE`；README 的 MIT 徽标不能替代许可文件，见[既有核对](../third-party-audit.md)。 | 按未明确许可处理，不引入源码、模板或素材。 |
| Ai-Novel | 此 `dev` 提交未发现正式 `LICENSE`，README 说明待补充；不能引用其他分支的许可，见[既有核对](../third-party-audit.md)。 | 按未明确许可处理，只记录公开界面与流程观察。 |
| 91Writing | 根目录 [`LICENSE`](https://github.com/ponysb/91Writing/blob/df3de0362e8e4028b76dffa78c5bba7b8bbd20a1/LICENSE)：MIT。 | 本轮仍独立实现；未来代码级使用须保留版权、许可及来源记录。 |
| webnovel-writer | 该 `master` 提交根目录 [`LICENSE`](https://github.com/lingfengQAQ/webnovel-writer/blob/86e617ee7f4f04be9d3f8575f90c38f879ed80ed/LICENSE)：GPL-3.0；官方返回文件 blob `f288702d2fa16d3cdf0035b15a9fcbc552cd88e7`。 | 只研究章节提交门、真源与派生资料边界。 |
| storyforge | 新提交根目录 [`LICENSE`](https://github.com/yuanbw2025/storyforge/blob/e8ce0a0dbab8b25be6851cf7d77c9e7c4786ed2b/LICENSE)：MIT；官方返回文件 blob `c944bc0c30e7c97d0c8f396f0fb4ff6fbc2e05f8`。 | 本轮独立实现工作台；未来代码级使用须另查所用文件、资产及依赖许可。 |

这份对照用于界定本轮借鉴范围，不表示仓库所有资产、依赖与商标都由根许可证统一授权。本轮未复制七项目的实现、提示词、模板或品牌素材。

## 作者视角对照

| 项目 | 实际工作区与流程观察 | 最值得吸收 | 不宜直接沿用 |
| --- | --- | --- | --- |
| **NovelForge** | 左侧卡型与项目卡片树，中间卡片编辑器/关系图，右侧随正文卡变化的助手、参与实体、提取、大纲、审核结果；正文提供字号、行距、续写、审核、选区快速编辑和逐条接受/拒绝。 | 章节、设定、引用处于同一工作区；资料预览与原文之间距离短；多处修订可以逐条裁决。 | 卡型和参数过多可能让新作者先学工具再写作；不能用浏览器每卡有限历史替换墨屿的服务端追加修订；引用关联不等于时点与人物知情正确。 |
| **MuMuAINovel** | 项目导航区分世界、人物、关系、大纲、章节、分析和伏笔；项目外壳挂载持续存在的 Agent 面板，显示会话、工具调用、执行步骤与待确认；章节页另有分析队列、批量生成及局部重写。 | 总管应跨页面存在，并且展示“正在做什么、改哪些资料、等待谁确认”，不只是聊天记录。 | 不把自动批准设为默认；不将复杂项目菜单全部平铺到墨屿首屏；界面的停止提示不能独立证明上游请求或计费已经停止。 |
| **Arboris** | 写作台保持蓝图/章节目录与当前章，按空章、生成中、版本选择、正文、失败切换；版本评估和对话/环境/心理等定向优化有明确入口。 | 让作者始终知道本章处在哪一步；将“改得更好”拆成可理解的具体目标；规划必须真正送到正文任务。 | 正文阅读后再开弹窗手动编辑会割裂长时间创作；整章重新生成和评分不能代替局部修订与作者裁决；不能把评估高分视为真实文学质量。 |
| **Ai-Novel** | 章节列表与当前章并列；标题、状态、计划、正文、摘要可编辑；生成、历史、提示检查放入抽屉。提示检查构造正文尾部、人物、世界、记忆模块等材料。 | 把生成前的依据、材料和预算变成可查看内容；区分未保存稿、生成候选与已保存稿。 | 不应把所有资料和参数默认摊开压缩正文；流式填入编辑态的做法不应取消墨屿显式候选采纳保护。 |
| **91Writing** | 同作品下用编辑/人物/世界观/语料/事件线切换，左侧章节可见字数和状态；写法库独立分类、搜索和保存。 | 高使用频率的材料选择、常用写法与章节状态；要求草稿切页后不能丢。 | 太多整页切换会中断当前章；旧整章覆盖与弱停止行为不应照搬；提示库存在不等于变量安全或版本追溯可靠。 |
| **webnovel-writer** | master 的主要价值是写作流程与章节提交真源，dashboard 属于健康/状态观察，不是独立正文编辑器；写前、提交前、写后资料更新具有明确阶段。 | 完成一章不是仅改状态，要把正文版本、审稿决定和派生记忆绑定；失败能定位并重试。 | 不为借鉴流程更换宿主；不把健康检查、文档或索引存在当成文学与长篇记忆通过。 |
| **storyforge** | 章节面板把按卷目录、可折叠场景细纲和正文放在一起；目录显示章状态和字数；项目外壳提供助手，助手呈现计划、执行进度、待采纳候选和输入来源。 | 减少“大纲页到正文页”的往返；上下文随章走，计划、候选、进度与来源分工明确。 | 功能多不等于动线清晰；章节创建/排序再跳到另一处会产生割裂；不得把代码中有预算/候选标签扩成真实费用、取消、恢复已验收。 |

### 本轮直接阅读的源码入口

- NovelForge：[三栏编辑器](https://github.com/RhythmicWave/NovelForge/blob/a485814411546b3980df0a1696a0fb6a043d56ce/frontend/src/renderer/src/views/Editor.vue)、[正文与逐条修订](https://github.com/RhythmicWave/NovelForge/blob/a485814411546b3980df0a1696a0fb6a043d56ce/frontend/src/renderer/src/components/editors/CodeMirrorEditor.vue)、[参与实体与关系材料](https://github.com/RhythmicWave/NovelForge/blob/a485814411546b3980df0a1696a0fb6a043d56ce/frontend/src/renderer/src/components/panels/ContextPanel.vue)、[助手与引用](https://github.com/RhythmicWave/NovelForge/blob/a485814411546b3980df0a1696a0fb6a043d56ce/frontend/src/renderer/src/components/assistants/AssistantPanel.vue)。
- MuMu：[项目外壳](https://github.com/xiamuceer-j/MuMuAINovel/blob/99f0aec6ce3b1c65652d20007be930ebba6a6d20/frontend/src/pages/ProjectDetail.tsx)、[项目 Agent](https://github.com/xiamuceer-j/MuMuAINovel/blob/99f0aec6ce3b1c65652d20007be930ebba6a6d20/frontend/src/components/project-agent/ProjectAgentPanel.tsx)、[章节流程](https://github.com/xiamuceer-j/MuMuAINovel/blob/99f0aec6ce3b1c65652d20007be930ebba6a6d20/frontend/src/pages/Chapters.tsx)。
- Arboris：[写作台挂载链](https://github.com/t59688/arboris-novel/blob/dd80c69bc30a80d68b375bd504c6bc308164ff9e/frontend/src/views/WritingDesk.vue)、[章节状态工作区](https://github.com/t59688/arboris-novel/blob/dd80c69bc30a80d68b375bd504c6bc308164ff9e/frontend/src/components/writing-desk/WDWorkspace.vue)、[定向优化](https://github.com/t59688/arboris-novel/blob/dd80c69bc30a80d68b375bd504c6bc308164ff9e/frontend/src/components/writing-desk/workspace/ChapterContent.vue)。
- Ai-Novel：[正文与章节工作区](https://github.com/inliver233/Ai-Novel/blob/8b85b2b6f29af4c69241be7c42417dbe4795db30/frontend/src/pages/writing/WritingPageSections.tsx)、[生成前提示与材料检查](https://github.com/inliver233/Ai-Novel/blob/8b85b2b6f29af4c69241be7c42417dbe4795db30/frontend/src/components/writing/PromptInspectorDrawer.tsx)。
- 91Writing：[编辑与资料](https://github.com/ponysb/91Writing/blob/df3de0362e8e4028b76dffa78c5bba7b8bbd20a1/src/views/Writer.vue)、[写法库](https://github.com/ponysb/91Writing/blob/df3de0362e8e4028b76dffa78c5bba7b8bbd20a1/src/views/PromptsLibrary.vue)。
- webnovel-writer：[master 文档](https://github.com/lingfengQAQ/webnovel-writer/blob/86e617ee7f4f04be9d3f8575f90c38f879ed80ed/README.md)、[当前只读 dashboard](https://github.com/lingfengQAQ/webnovel-writer/blob/86e617ee7f4f04be9d3f8575f90c38f879ed80ed/webnovel-writer/dashboard/app.py)、[当前真源与投影架构](https://github.com/lingfengQAQ/webnovel-writer/blob/86e617ee7f4f04be9d3f8575f90c38f879ed80ed/docs/architecture/overview.md)。v8 另见本仓库[限定说明](../audits/2026-10-14/comprehensive/projects/webnovel-writer.md)。
- storyforge：[当前卷章场景工作区](https://github.com/yuanbw2025/storyforge/blob/e8ce0a0dbab8b25be6851cf7d77c9e7c4786ed2b/src/components/editor/ChaptersListPanel.tsx)、[当前总管界面](https://github.com/yuanbw2025/storyforge/blob/e8ce0a0dbab8b25be6851cf7d77c9e7c4786ed2b/src/components/agent/ChatCopilotPanel.tsx)、[当前项目外壳与助手挂载](https://github.com/yuanbw2025/storyforge/blob/e8ce0a0dbab8b25be6851cf7d77c9e7c4786ed2b/src/pages/WorkspacePage.tsx)。新提交源码可达不等于新提交整站运行通过。

## 墨屿应采用的工作台

以下为独立产品建议，不是上述项目已经共同实现的能力。

| 区域 | 默认内容 | 必须保持的行为 |
| --- | --- | --- |
| 顶部工作栏 | 作品 / 分支 / 卷章路径、章节状态、真实保存状态、专注模式；模型详情收进设置或运行详情。 | “正在保存、已保存、失败、有未保存改动”分开；切书切章前保存失败就留在当前稿，并允许重试。 |
| 左侧资料栏 | “目录 / 知识库”两个视图。目录显示卷章、状态和字数；知识库按人物、地点、物品、规则、剧情线和伏笔组织并可搜索。 | 当前章高亮；长标题可完整查看；打开资料不重建正文输入框。不要同时放一套总导航和一套重复章节导航。 |
| 中间主编辑区 | 当前章标题、可收起的目标/场景计划、正文；底部仅保留字数和保存信息。 | 正文获得最大面积；不把整章正文放进有巨大留白的装饰卡；字号、行距与专注偏好本机保存，正文不随偏好改变。 |
| 右侧写作总管 | “任务 / 依据 / 审稿”三个互斥视图。任务默认显示当前阶段、下一步、运行与待确认；依据显示当前事实、计划和参考；审稿显示问题与修订入口。 | 始终绑定当前作品、分支和章节。来源可回到原文；后台任务完成不抢走焦点、不覆盖当前稿；待确认数量在收起时仍可见。 |
| 候选审阅区 | 生成后就地出现原稿/建议差异，可采纳、拒绝、编辑；多项建议逐条处理。 | 不自动覆盖正文；绑定原版本与选区；原文变化后旧建议保持只读，不用“重新生成”暗中触发收费请求。 |

电脑验收尺寸至少包含 1280×900、1440×1000、1920×1080。建议左栏约 220–260px、右栏约 300–360px，可收起；中间正文保持可用宽度，窄电脑先收资料栏而不是缩成手机界面。默认展示任务摘要，详细参数、长日志和全部历史按需展开。

## 写作总管如何贯穿全过程

继续使用已有 LangGraph，即能够记录多步骤任务并在中断后接续的成熟执行框架；不要因为 UI 改版另外做一套仅在浏览器内存中的任务调度。界面只是同一工作状态的投影，实际动作继续走现有服务、队列、冻结来源、候选与作者确认。

成熟框架负责步骤推进、中断与持久化，不会自动补齐小说的事实判断、人物知情范围或文学质量。墨屿现有绑定负责作品/版本/章节隔离、原稿检查、有限调用和作者回执；本轮新增流程也应复用这些边界。当前有限写作任务不能等同于“已具备所有创作阶段的自主总管”，下表是必须逐步接通并验收的目标动线，不是全部已经实现的清单。

| 创作阶段 | 总管负责的协调工作 | 作者看到并决定什么 |
| --- | --- | --- |
| 立项 | 识别题材、读者承诺和创作约定的缺口，形成可编辑的开书准备建议。 | 先确认方向，不把灵感讨论自动写成正式设定。 |
| 规划 | 读取当前蓝图与知识库，提出卷章/场景目标，标明计划而非既成事实。 | 在原有规划结构上调整和确认，不在聊天中留下无法编辑的唯一计划。 |
| 写前 | 检查前章交接、当前视角、人物知情、伏笔和资料范围，构造有界材料。 | 右栏显示本次用到的依据、排除原因和调用上限；作者可补充要求或移除参考。 |
| 起草与续写 | 把已确认目标交给正文任务，显示真实队列、流式进度与失败状态。 | 继续手写或看候选；停止、失败片段、恢复与费用未知各有明确状态。 |
| 修订 | 按作者目标或审稿问题提出范围建议，保留来源版本。 | 原处看差异，选择接受哪一条；不要求作者手动在多个功能页传递文本。 |
| 章后整理 | 正文采纳后重新核对当前版本，准备摘要、交接与记忆候选。 | 对新增事实逐项编辑、确认或拒绝；确认后才进入正式知识库。 |
| 审稿与完成 | 读当前稿及有效资料，串接事实/故事检查和章提交门。 | 待修订问题就地定位；修改或说明理由后复查；完成后打开既有下一章，不重复造空章。 |
| 全书整理 | 汇总长期伏笔、人物线、重复与未决问题，提出分批处理计划。 | 先看影响范围，再批准具体批次；没有独立证据时不显示“全书一致性通过”。 |

“全局管理”不意味着擅自改稿或改正典。可自动执行的是检索、检查、组织候选和接续已授权步骤；正文落稿、事实晋升、删除和正式发布仍保留作者裁决。总管应减少作者搬运资料和寻找功能的次数，而不是增加一层让作者逐节点操作的流程图。

## 知识库与 Obsidian 思路

建议吸收的是“稳定笔记身份 + 双向关联 + 来源 + 本地文件可带走”的工作方式，不是要求用户必须先安装 Obsidian 或把它作为运行前提。

- 小说中每个人物、地点、物品、规则、线索和伏笔拥有稳定 ID；改名不新建第二个对象，显示别名与关联章节。
- 正文引用与反向链接能回答“这条设定在哪里出现、哪些章节依赖它、修改会影响谁”；右栏优先展示本章相关邻居，全书大图另开，不长期占据正文。
- 区分正式事实、候选、作者计划、参考书技法和推断；图谱连线不能将它们混成同一种可信事实。
- 资料必须携带来源章节、修订和原文位置；未来秘密、其他分支与人物未知资料即使存在于知识库，也不能进入不该知道的任务。
- Markdown/JSON 导出保留稳定 ID、关联和来源元数据；以后若做外部知识库同步，先解决双向修改冲突和删除预览，不能静默覆盖本地作品。

上述是下一阶段设计建议，不表示本轮已经实现 Obsidian 同步、完整双链或知识图谱推理。现有 SQLite 真源、备份与审计应继续保留，不能仅为“像知识库”而降级为未经校验的散文件写入。

## 实施顺序与验收

1. **本轮：把既有能力放回正文工作区。** 左侧目录/知识库，中间正文，右侧总管；接真实保存状态、当前章任务、依据与审稿。验收必须实际完成“开作品→选择已规划章→手写→续写候选→采纳→章后确认→审稿→下一章”。
2. **随后：减少跨页和重复操作。** 资料就地预览、原文跳转、可收起场景计划、字号行距、逐条范围建议。验证来回查看人物时正文、选区、滚动和要求草稿不丢。
3. **再后：强化长篇组织。** 结构化人物/规则字段、双向引用与影响预览、场景重组、可保存写法和分任务参数。每项都验证保存、刷新、切书、备份恢复与实际任务消费。

所有阶段共同负向验收：长书名和长章名、万字章节、重复原句与 emoji、切章后的迟到响应、保存失败、断网、刷新恢复、中断片段、旧候选过期、费用未知不自动重发、重复确认幂等、跨书和跨分支不串资料。新界面不能仅通过截图，还要使用真实页面触发服务端动作并回读结果。

“第一梯队”需要另行提供同模型对照、冻结材料、盲评、真实长篇连续性和作者改稿耗时证据。七项目研究、界面改版、工程回归与真实文学效果分别报告，本文件不将任何一项冒充全部完成。

## 借鉴边界

只吸收公开产品概念、交互模式、失败条件和可验收要求；不复制七个项目的源码、提示词、模板、品牌或素材。既有许可与证据边界见[第三方审查](../third-party-audit.md)和[全面审查入口](../audits/2026-10-14/comprehensive/README.md)。若未来考虑代码级依赖，需要对具体新提交重新核对许可，不能沿用旧快照结论。
