# Arboris 全功能专项与 NovelForge、MuMu 交叉复核

审查日期：2026-10-14。专责代理：`comprehensive_arboris`。

本轮重新读取官方固定源码，沿页面、接口、保存、后续消费及撤销边界审查。没有把 2026-10-13 的墨屿旧基线缺口直接当成本次现状。产品源码只读，测试只用隔离数据库和合成故事，没有真实模型调用、真实作品或密钥读取。

## 版本与结论

官方 API 本次核实 Arboris 默认 `main` 仍是 `dd80c69bc30a80d68b375bd504c6bc308164ff9e`，树 `08647938cbd5a602a27f5e7aa228742a4b1d8c4b`。这是重新核实后的相同版本。仓库 API 的许可证为空；README 虽有 MIT 声明，固定归档内没有找到 LICENSE，故仅研究产品设计，不复制实现、提示词或素材。[来源元数据](../evidence/arboris-source-metadata.json)保留官方入口和归档指纹。

墨屿审查起点为 `64fe155387e0a3c2d902b60a2d82822f2457af0d`，同时存在备份恢复与完整字符名称处理的未提交修复。本文新增复现固定在 `2026-10-14T14:47:09Z` 的 `server.py` 与 `moyu_context.py` 指纹；主流程随后开始修复设定范围，不得把本报告的修前失败当成修后结论。

Arboris 的创意对话、可编辑蓝图、三种方向候选和定向润色确有作者入口。墨屿现在已有书架、四阶段规划、卷章场景、选区差异、任务中心、章后提取、逐项确认、伏笔证据、语义审稿和完整恢复，不能重复写成全部缺失。修前新复现的问题是：**设定范围能在内存过滤，却无法通过真实保存回读保留；未来和反派独知资料因此进入早章材料。**随后已实施范围持久化，[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的23项隔离HTTP通过。后续范围变更、嵌套归属、材料与章序依赖也已取得独立专项结果，见[依赖保护索引](../evidence/README.md#范围材料与历史结果依赖)；当前完整结果使用下述85项整轮。

规划的读者、创作约定、卷概要未进入普通正文任务也是修前缺陷，现已实施有界指南；[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的指南HTTP10项验证预览与发送一致、计划与事实分开及超预算拒绝。结构化人物字段、创意多轮讨论、多候选比较与便捷润色维度仍是待实施体验项。文学、长篇收益和第一梯队地位仍未证明。

最新[指南与确认完整性整轮](../evidence/compatibility-python311-node24-guidance-candidate-integrity-release.json)在上海2026-10-15 02:39:43至02:46:58完成85项全部通过，95份来源前后相同，10组真实电脑浏览器125场景，11份JSON及32张截图共43份附件SHA一致。本报告独立读取及重算指纹；包含范围保存、材料消费、冻结请求、指南及人物身份保护，源码推送、远程检查、真实服务和文学效果仍分别验收。

证据记号：R 为本次实际执行；E 为源码链；D 为声明；U 为未测。组件 R 只证明其原函数行为；本轮未完整启动上游应用，不能记上游整机通过。

## 全功能对照

以下 Arboris 路径相对其固定源码根目录；墨屿路径相对本仓库。行号对应本轮读取版本，修后可能变化。

| 能力 | Arboris 作者入口、保存与消费 | 墨屿本次现状和真实差距 |
| --- | --- | --- |
| 开书与创意讨论 | E：`frontend/src/stores/novel.ts:33,90,112,129` 调创建、概念对话、蓝图生成与保存；`backend/app/api/routers/novels.py:144,215,303` 读取已存对话并写蓝图 | E：书架与四阶段规划已经接通；`author-workspace.js:414` 从一句话开始。缺少可持续追问、修改前一轮决定及保留弃用方向的创意对话，不是“没有规划” |
| 蓝图与写作约定 | E：`novel_service.py:252-313` 持久化受众、风格、基调、梗概、世界、人物、关系与章纲；`writer.py:515-627` 普通正文消费裁剪蓝图 | 修前E：普通正文未消费这些字段；修后R：当前约定/卷章场景进入continue/polish/semantic_review，[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的10项HTTP验证预览=发送、计划/事实隔离及预算。后续范围/材料专项另验，不由该快照代替 |
| 卷章场景层级 | E：普通章纲写 `ChapterOutline(title,summary)`；高级 director 的任务脚本另存在 metadata，未见普通可操作卷场景树 | E：墨屿已具备卷、章节、稳定场景及四阶段确认；不能以 Arboris 高级类数量要求重建。应检查场景地点、视角、目标、出口的保存后实际消费 |
| 人物、世界与关系 | E：`novel_service.py:267-298` 保留身份、性格、目标、能力及扩展字段；`BlueprintEditModal` 使用增强人物编辑器 | E：通用卡已有类型、名称、别名、描述、范围；人物驱动、限制、能力等专门字段仍缺。范围丢失为下节修前反例；持久化修复在[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的23项HTTP中通过 |
| 多方向候选 | E：`writer.py:735-817` 顺序生成细腻/冲突/悬念风格，保存后等人工选择；`WritingDesk.vue:474` 调选择接口 | E：墨屿有独立候选、父子整理、历史回看和差异采纳；未见同次多方向并排比较。可做可选的逐个生成与比较，不能默认增加收费次数 |
| 历史、选区及撤销 | E：Arboris `replace_chapter_versions:443` 删除旧候选；`edit-fast` 和 optimizer 原地改选中版本，不能算耐久历史 | E：墨屿追加修订、基稿检查、范围补丁和撤销已完成并有回归。保留这些边界，不照搬直接覆盖 |
| 检索旧正文 | E：`writer.py:589-597` 作者要求参与查询；`chapter_context_service.py:61-99` 调 embedding 和向量片段/摘要。没有当前章节上界参数 | E：墨屿有本地段落检索、要求加权、别名、原句范围、时点/人物范围和预算。没有向量/重排及真实小说增益结论，不能因此判必然比向量差 |
| 未来秘密与人物知情 | E：Arboris `writer_context_builder.py:89-121` 保留已登场人物整对象和世界字段，限制出场不等于过滤秘密；普通向量查询只按作品隔离 | **修前R：setting 范围保存丢失**；[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的修后23项真实HTTP覆盖保存/回读、早章排除、复制/恢复、旧客户端保留及损坏拒绝。后续范围指南扩展14项、嵌套声明12项、材料扩展29项及章序9项另见依赖索引；指定成功不扩为所有真实作品安全 |
| 写后摘要、事实和状态 | E：Arboris 下一章 `writer.py:490-513` 补前章摘要并保存；普通 select 的向量入库缺 `user_id`。高级 finalize 有状态快照，但普通页面未调用 | E：墨屿已有普通正文提取、原句预览、编辑确认、拒绝、旧稿失效和下一章交接。模型提取召回/精度未正式测量，不能记事实质量通过 |
| 伏笔与时间线 | E：可见 `ForeshadowingSection.vue:260` 调 analytics；持久化伏笔 create/resolve 的接口存在，但未见其普通页面调用 | E：墨屿统一看板与链接、埋设/发展/回收原句、失效重锚和终稿阻断已经接通。不是本次应重复补的缺口 |
| 审稿与定向润色 | E：`ChapterContent.vue:322,346` 有对话/环境/心理/节奏优化、预览和应用；`six_dimension_review_service.py:145` 失败默认80/各维100，且普通页面不调用该六维入口 | E：墨屿有证据绑定的语义审稿、处理记录和选区润色；缺少便捷的维度选择及正式审稿准确率成绩。无需模仿失败高分 |
| 连续创作与任务控制 | E：Arboris 普通 UI `api/novel.ts:224-249` 等完整 JSON，多版本顺序生成；未见同路径持久队列/断线续片/取消 | E：墨屿任务中心、逐片落盘、取消、未知不重发、刷新恢复和只读中断片段已经实现。真实服务取消计费仍 U |
| 模型与风格 | E：Arboris 个人 LLM 设置页及实际调用读用户配置存在；其用户密钥持久化数据库，不可用 README 的 env 描述替代隐私结论 | E：墨屿模型设置、用途模型、温度、长度、额度、私有凭据及掩码已完成。模板分类/变量/版本仍是后续项，真实文学仍失败 |
| 多作品与恢复 | E：Arboris 有书架、用户所有权；重生成删旧候选，未见完整项目恢复作者链 | E：墨屿书架、作品隔离、完整 ZIP、独立副本恢复、历史候选只读已有真实测试；长名称、恢复与只读重开已随当前85项整轮重新验证，旧[中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)保留 |
| 导入与出版 | E：Arboris TXT 导入逐项 commit、依赖模型解析；`ChapterContent.vue:295` 有单章 TXT 导出 | E：墨屿已有 TXT/MD/EPUB 预览、独立参考库、原子稿件导入、MD/EPUB 出版往返。DOCX/PDF、自动定时备份未实现，需按作者真实需求排期 |
| 工程与效果证明 | U：本轮未运行上游整机、真实向量或模型文学比较；部署与健康文件只能作 E | 墨屿当前85项整轮覆盖最新指南与确认完整性修复，使用免费工程样本；旧[中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)及文学对照失败保留。七项目源码审查与工程通过不能证明文学第一梯队 |

## 本轮新问题：设定范围保存丢失

修前源码链：

1. `app.js:1327` 的设定编辑器没有“可用于第几章、哪些人物知道、资料性质”字段。
2. `server.py:1967` 的 `app_entity_meta` 只保存描述；`sync_state:3997-4024` 读取类型、名称、描述和别名，忽略 `knowledge_scope/known_by`。
3. `state_from_db:3603-3620` 只回读这些字段。创建接口 `:5415-5423`、修改接口 `:6123-6137` 也没有保存范围。
4. `moyu_context.py:132-219` 的过滤器能正确处理带范围的 setting；但真实回读已失去范围，`:684` 将其作为普通卡片送入材料。过滤器本身通过不代表来源链正确。

**R，免费真实 HTTP：**临时库中第1章用主角视角；增加一张第9章可用的设定和一张仅反派可知的设定。直接内存调用先正确排除两张。随后实际 `GET state -> PUT state -> GET state -> POST prompt-preview` 均200，回读均无范围，最终提示包含两段原本应排除的合成秘密。[修前原始结果](../evidence/arboris-setting-scope-before-fix.json)保留源码指纹与边界。

没有发送模型请求，所以这是“非法资料已进入发送材料构造链”的修前复现，不是“模型已经写出剧透”的复现。主流程随后修复初始保存遗漏，[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的23项范围HTTP通过，修前失败原件继续保留。之后发现的范围变更旧任务依赖问题由[当前能力矩阵](../capability-matrix.md)另记，不能用上述成功覆盖。

修前提出的最小实施范围：设定范围的专用持久化、状态同步/CRUD回读、正常页面编辑、导入/复制/完整恢复、审稿材料指纹及模型最终材料共用。保存修复已采用独立投影，避免破坏旧完整包或现有三列描述表；后续依赖失效已按独立专项验收，原失败与扩展集合分别保留。旧卡默认未标注，不能无证据猜其知情范围。

最新[材料29项](../evidence/material-dependency-after-fix.json)与[真实旧报告10项](../evidence/material-legacy-after-fix-membership.json)进一步覆盖说明、记忆、状态、关系、时间线、伏笔及前章变化：旧请求不再执行、整理或采纳，已取得候选与用量仍保留。真实旧分析缺材料标记时只读，旧审稿可免费按当前全作品材料解释，资料增删后理由失效。纯合成假run_id的组件兼容不代表真实旧AI报告可以确认。这些结果固定在各自源码快照；随后合法旧候选证据文字的兼容问题及完整验收状态见[总入口](../README.md)。

[材料修后77项整轮](../evidence/compatibility-python311-node24-materials-comprehensive-refined-release.json)后来取得122个电脑场景和40附件一致，仍在后续发现保存报告的指南依赖遗漏：只改故事前提或创作约定、不重建章节，旧章后分析仍可确认，缺指南审稿与旧说明未失效。[章后12项首修后](../evidence/analysis-guidance-after-fix.json)、[审稿11项首修后](../evidence/review-guidance-after-fix.json)分别通过，完整原来源保存，正常自身确认单独更新决定指南，外部变动先拒绝；旧审稿免费说明始终绑定当前指南，蓝图再变重新阻断。阶段计数、原件与旧测试前提纠正在[指南索引](../evidence/README.md#保存报告的创作指南依赖)记录，77项不代表这些新源码的最终验收。

[部分来源修前8项](../evidence/analysis-partial-source-before-fix.json)继续得到6个失败测试组、2个通过，合成报告无实际run但保留部分私有来源仍误放行；[冻结同8项修后](../evidence/analysis-partial-source-after-fix.json)全通过，0请求、0收费、11份来源不变。原材料和指南必须完整，孤立/空决定来源不替代原凭据，仅指南旧说明随人物description改变也重新失效。[人物身份8项修前](../evidence/analysis-character-identity-before-fix.json)另有6项误200、2正常控制通过；[同8项修后](../evidence/analysis-character-identity-after-fix.json)全通过，前后各13次免费、0收费。人物状态主体及关系两端都须当前人物，首次非人物400、已确认后改类型的新报告409，均全库不变；正常人物的首次确认与新报告复用仍200。人物字段数量不能替代稳定类型与真实消费边界，这些脚本均已纳入当前85项整轮。

[跨报告复用同7项](../evidence/analysis-candidate-reuse-after-fix.json)、[五类记录同13项](../evidence/analysis-candidate-typed-after-fix.json)和[指南电脑同3场景](../evidence/analysis-guidance-browser-after-fix.json)也通过，分别15、72、3次免费、0收费。未变内容复用原正式记录，改动、撤回、身份歧义及来源证据损坏不恢复；typed为13测试组、36观察场景、29显式子测试，不把42条结果扩成42测试。规范修前与早期隔离错误见[确认完整性索引](../evidence/README.md#跨报告确认复用证据与人物身份)，不能据此判断上游人物理解或中文文学领先。

验收至少覆盖：第9章秘密不进第1章；反派独知不进主角视角；作者计划不能冒充已发生；保存、刷新、编辑名称、导入、复制和恢复后范围保持；正文与范围修改使旧审稿/候选按既有契约处理；正式发送材料与页面预览一致。

## Arboris 原组件复跑

本次从固定源文件解析并执行原始函数的语法树，未重写业务算法、未安装上游数据库或调用外部模型。[组件结果](../evidence/arboris-original-component-cross-review.json)含原文件指纹：

| 核查 | 本次结果 | 边界 |
| --- | --- | --- |
| `_rewrite_with_guardrails` 已有配置提示时 | R：返回 `None`；`writer.py:173-188` 在取到提示后没有后续返回，重写内容错误地落在下个函数 | 只证明原组件；未声称本次上游 HTTP 请求毁稿 |
| select 入库调用的签名 | R：以 `writer.py:845-850` 的真实参数绑定 `chapter_ingest_service.py:38-47`，报缺必需 `user_id` | 仅签名绑定，不执行向量库；保存和错误捕获的顺序仍是 E |
| 普通高级功能消费 | E：前端普通调用 `/chapters/generate` 与 `/chapters/select`；未找到 `/advanced/generate`、`finalize`、`six-dimension` 的普通页面调用 | 不能用孤立高级服务文件作为作者已可用功能；定向 optimizer 则有普通页面调用，不应一并否定 |

## 独立交叉复核

### NovelForge

固定 `main` 为 `a485814411546b3980df0a1696a0fb6a043d56ce`，树 `a6d3fd09a2de9973a5d45982ddd060dc4db1ab51`。复核方独立读取本次官方固定源码，未只复述专责报告：

- 确认 E：`backend/app/bootstrap/card_types.py:232-245` 的正文引用当前阶段、章纲、下一章计划及本卷写作指南；真正编辑器 `frontend/src/renderer/src/components/editors/CodeMirrorEditor.vue:1283-1296,2585-2596` 解析并组成生成上下文。不是只看模板字段存在。
- 确认 E：`backend/app/schemas/entity.py:86-106` 人物驱动、弧光及动态资料，场景用途/状态有结构化字段。不能把字段本身说成模型判断准确。
- 独立对照修前墨屿 `server.ai_context:932-945`，确认蓝图只注入规划任务，普通正文缺契约、受众、卷概要。随后有界指南修复已实施，[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的10项指南HTTP通过；采纳规划和真正消费规划分别验收，后续范围依赖进展见[当前能力矩阵](../capability-matrix.md)。
- 下调边界：未运行 NovelForge 完整前端、持久运行器崩溃恢复或真实模型，不确认其额外上下文必然改善文学或不重复计费；其下章计划引用也须视为作者计划，而非人物已知事实。

### MuMuAINovel

固定 `main` 为 `99f0aec6ce3b1c65652d20007be930ebba6a6d20`，树 `992469d7bafe16c76e1488741f68df19e86e035c`：

- 确认 E：`backend/app/services/chapter_context_service.py:31-45` 直接格式化人物最新状态，没有当前章参数；`:560` 在真实 1-N 人物材料构建中调用，`backend/app/api/chapters.py:1603-1616,1697-1707` 将该材料送入生成提示。
- 新组件 R：执行原 helper，合成第50章死亡及秘密状态均出现在结果里。该 helper 无目标章输入，只能输出最新状态；不得把早章重写安全寄托在下游模型自行忽略。
- 同时确认 E：`chapter_context_service.py:769-775` 的相关记忆检索确有限定 `(1,current-1)`。上述结果针对另一条“人物最新值”入口，不能泛称 MuMu 所有记忆都无时间过滤。
- 边界：没有启动 MuMu 整机、Chroma 或真实 embedding。最新状态风险的整条模型链为 E；只把 helper 的执行记为组件 R。

## 建议实施顺序与交付边界

1. **范围真实保存及依赖保护已实施并通过当前整轮。**[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的23项HTTP证明初始保存；后续范围、嵌套声明、材料与章序有独立结果。当前85项整轮已重新核对页面、持久化、消费与冻结请求，后续改动仍需新快照。
2. **规划进入正文任务已实施。**[已归档中间快照](../evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)的10项HTTP专项验证创作约定、当前卷章及场景；未来情节不混入已发生事实。后续冻结源码和整轮结果从[总入口](../README.md)核验。
3. **补结构化人物与定向改稿体验。**人物欲望、限制、能力等先用稳定字段；润色维度只改变作者意图，不扩大默认付费调用。
4. **再评估创意多轮与多候选比较。**先证明作者减少复制与修改成本，再增加多调用；不得以“生成三个版本”替代文学收益测量。

本轮没有收费文学、真实小说检索增益、上游整机或生产验收。移动端按用户要求不列范围。结论可支持修复顺序，不能支持“已处于第一梯队”。
