# 七项目全面复核后的差距矩阵

本矩阵合并2026-10-14七名专责的独立源码审查及原文交叉复核，覆盖电脑端。上游字段或函数存在、原组件执行、墨屿真实HTTP/页面执行、文学效果和生产运行分别记录，不按星数、按钮数或测试文件数排名。每项修前失败留在专项报告中，修后结论以[本轮总入口](README.md)的冻结源码验收为准。

历史[指南与确认完整性整轮](evidence/compatibility-python311-node24-guidance-candidate-integrity-release.json)于上海2026-10-15 02:39:43至02:46:58完成，85项检查全部通过；95份冻结来源不变，10组真实电脑浏览器125个场景，11份JSON及32张截图共43份附件SHA匹配。包含下述指南、部分来源、复用、人物身份和旧测试前提纠正，原文件不改；不能覆盖之后参考片段源码，也不扩成真实文学、GitHub检查或真实服务结论。

新增参考原句已实现，独立13方法、15场景专项通过，3次免费调用、收费0；真实电脑参考15场景通过。首次[参考包整轮](evidence/compatibility-python311-node24-reference-passages-integrity-release-20261015.json)89项中87通过、2失败，98源码前后相同，原失败保留。校验顺序与浏览器归档修正后，[新完整复验](evidence/compatibility-python311-node24-reference-passages-integrity-refined-release-20261015.json)于上海2026-10-15 04:05:50结束，89项全部通过、11套浏览器140个电脑场景、98份来源不变，14份JSON和36张截图共50份附件指纹一致，收费0。[本地服务独立验收](evidence/local-service-reconciled-20261015.md)只对应 `3a369807252e5dcda9ce5bda039d169744fedee5`，首次更新失败保留，后续核对/搜索衍生修复/8场景只读成功。该提交GitHub运行 `38620315120` 仍为failure，新参考包远程检查和实际服务更新尚待核验。

## 本轮已经实施的修复

| 作者流程 | 修前问题与独立来源 | 本轮实现 | 验收入口 |
| --- | --- | --- | --- |
| 设定按章及人物使用 | 范围在真实保存回读后丢失，早章材料含未来或反派独知资料；[Arboris](projects/arboris-novel.md)，[Ai-Novel](projects/ai-novel.md)交叉确认 | 独立范围表、正常表单、回读、旧客户端保留约束、复制与完整恢复重映射；损坏范围拒绝消费；实际归属与嵌套作品/分支声明共同限制 | [23项隔离HTTP](../../../../tests/record_scope_http_fixtures.py)，基础浏览器保存/刷新/早章排除/作者改范围/JSON导出；[嵌套范围12项](evidence/nested-record-scope-after-fix.json) |
| 保存的规划进入正文 | 读者、创作约定、卷目标及结构化场景没有进入续写、润色和故事审稿；[NovelForge](projects/novelforge.md)、[MuMu](projects/mumu-ai-novel.md)、[Arboris](projects/arboris-novel.md)独立发现；后续章后报告缺原指南、只改蓝图仍能确认 | 有界当前指南明确标作作者计划，过滤其他POV场景，排除未来总纲/其他卷章；指南身份进入创建、派发、重试、采纳及报告保存/确认；正常章后自身确认保留原指南并单独记录决定来源，同稿新指南不复用旧报告 | [10项实际HTTP脚本](../../../../tests/blueprint_guidance_http_fixtures.py)及[已归档中间快照结果](evidence/compatibility-python311-node24-comprehensive-workflow-refined-final.json)，预览与实际发送相同、字节统计精确、空指南不占固定预算、超预算先拒绝；[别名扩展11项](evidence/guidance-alias-material-final.json)、[范围扩展14项](evidence/guidance-scope-change-material-final.json)、[章后指南12项首修后](evidence/analysis-guidance-after-fix.json) |
| 审稿决定进入归档和出版 | 作者认领需修问题后仍可基础通过、三阶段归档和无理由出版；[webnovel](projects/webnovel-writer.md)实际HTTP复现；保存决定后缺局部修订目标；材料保护接入后纯章节状态让刚保存理由失效；缺指南旧审稿及说明未随蓝图失效 | 有效待办进入共同门；过期问题可免费按当前来源及指南解释/忽略并留理由；正文/资料/计划变化使理由失效，专用作者凭据仅排除章状态且不授权模型任务；缺指南不授权认领/修订/迟到保存；保存决定后重读完整报告，失败保留窗口并只读重开 | [11项隔离HTTP结果](evidence/webnovel-semantic-publication-final.json)，真实页面免费解释及MD/EPUB下载；[53项基础浏览器中间快照](evidence/frontend_browser_regressions-python311-node24-comprehensive-workflow-refined-final.json)含决定后局部修订和读取失败1次提交/0额外模型；[作者说明归档9项](evidence/author-resolution-material-after-fix.json)、[审稿指南11项首修后](evidence/review-guidance-after-fix.json) |
| 沿规划继续下一章 | 归档始终新建空章并跳过既有后续章；[MuMu](projects/mumu-ai-novel.md)组件复现，[storyforge](projects/storyforge.md)独立源码确认 | 沿已有未完成章，保留正文、目标、视角、场景及卷身份；交接独立显示，无后续才创建；失败重试复用已建身份 | [生命周期浏览器](../../../../tests/author_workspace_lifecycle_browser_regressions.cjs)：现有五章、跨卷、重复/双击、失败重试 |
| 已保存但暂时打不开 | 导入成功后GET失败仍关窗报成功；[storyforge](projects/storyforge.md)组件复现，新建/复制同类源码链 | 记住已经保存的作品/参考身份，保留窗口并只读重开，真正打开后才提示成功 | 新建/复制/导入各测原作、目标、双方回读失败，POST恰好1次；参考库重开 |
| 创作要求跨页保留 | 去设定库再回来会从润色变续写、丢补充要求；[91Writing](projects/91writing.md)真实浏览器复现 | 按作品/分支/章节缓存任务与要求，提供明确清空，迟到事件不串书；存储失败提示并同页回退 | [11项草稿浏览器](../../../../tests/instruction_draft_browser_regressions.cjs)，包括刷新、隔离、旧任务冻结及两种电脑尺寸 |
| 完整字符名称与导航 | 备份恢复超长名称；导入emoji按双单位截断；长标题挤压导航；[Ai-Novel](projects/ai-novel.md)、[storyforge](projects/storyforge.md)确认 | 作品200/章节500按完整字符校验，恢复默认后缀保留完整字符；导航允许换行，短电脑窗口可操作 | 基础浏览器备份/恢复/重开及长标题1440/1280/短高度；生命周期导入200/500边界 |
| 中文出版例外理由 | 中文不能直接作为浏览器HTTP头；前后端字数口径不一致 | 浏览器UTF-8编码，服务端严格解码再按完整字符校验；理由原样写入清单与事件，改稿后重新校验 | [真实下载浏览器](../../../../tests/export_override_browser_regressions.cjs)，中文/百分号/emoji、短理由及改稿失效 |
| 旧任务、候选与报告随材料失效 | 已消费说明、记忆、状态、关系、时间线、伏笔、前章及章序变化后旧请求仍可执行；真实旧AI报告缺材料标记仍可确认 | 私有材料来源绑定已消费资料及有效章序，创建/派发/重试/整理/采纳共用校验；旧结果可读，完整候选及用量保留；真实旧分析只读，旧审稿可免费按当前全作品材料说明，资料再变理由失效 | [材料扩展29项](evidence/material-dependency-after-fix.json)、[章序9项](evidence/material-scope-order-after-fix.json)、[真实旧报告扩展10项](evidence/material-legacy-after-fix-membership.json)、[电脑浏览器5场景](evidence/material-dependency-browser-after-fix.json)；前后集合及控制边界见[索引](evidence/README.md#范围材料与历史结果依赖) |
| 参考原句进入本次写作 | [Ai-Novel专项](projects/ai-novel.md)修前参考仅可查看/搜索，选中原句未进入请求；后续独立复核发现参考绑定及冻结请求损坏仍可能执行 | 作者显式选择一段、最多900个完整字符，服务端按当前作品/分支、范围及三份hash重读；仅continue/polish，独立reference材料且非本书事实；必保预算拒绝、只绑定已消费来源，完整请求与审计联合核验 | [正式参考HTTP](../../../../tests/reference_passages_http_fixtures.py)36方法、[独立完整性13方法/15场景](../../../../tests/reference_passages_integrity_http_fixtures.py)及参考页面15场景均在新89项整轮通过；完整性3次免费、收费0，修前及修后原件保留 |

以上“实施”不是第一梯队成绩。范围过滤只对本轮指定数据成立；审稿门正确不等于审稿意见准确；免费模拟输出不计入真实文学成绩。

冻结后的范围、材料、章序和真实旧报告反例已分别修复并取得上表专项结果；原失败证据保留。[第一次材料整轮](evidence/compatibility-python311-node24-materials-comprehensive-release.json)76项中73项通过，旧证据兼容、作者生命周期和基础浏览器3项失败，87份来源前后相同，仍是失败中间快照。历史证据兼容扩展9项、作者说明归档9项与修后的旧报告10项均另通过；[材料修后整轮](evidence/compatibility-python311-node24-materials-comprehensive-refined-release.json)77项通过、88份来源不变、122个电脑场景和40份附件一致。后续又发现并修复保存报告的指南依赖断点，章后12项、审稿11项仅为首修后专项；历史85项整轮覆盖该阶段后续源码及测试变化。已有68项、77项及85项快照不作为之后参考包的最终发布证据，旧测试前提纠正也不计作新增漏洞。

指南首修后新增[部分来源8项修前](evidence/analysis-partial-source-before-fix.json)：没有实际run但有私有服务端来源的报告，仅剩指南、孤立决定材料或显式空决定来源仍可能放行；仅指南报告的旧作者说明也未随人物说明变化失效。6个测试组失败、2个控制组通过，含子测试8个失败断言。[冻结同8项修后](evidence/analysis-partial-source-after-fix.json)全部通过，前后0请求、0收费、11份来源不变；缺失原标记不由决定标记兜底，免费说明绑定当前全作品材料，人物说明变化使旧理由失效。[原审稿11项最新复验](evidence/review-guidance-after-partial-source-fix.json)也通过、9次免费、0收费，均已纳入历史85项整轮。各阶段源码及计数仍分开记录。

跨报告相同事实确认新增[复用7项](evidence/analysis-candidate-reuse-after-fix.json)、[五类记录13项](evidence/analysis-candidate-typed-after-fix.json)、[人物身份8项](evidence/analysis-character-identity-after-fix.json)及[指南电脑3场景](evidence/analysis-guidance-browser-after-fix.json)，均同冻结集合修后通过，分别15、72、13、3次免费，收费0。正常内容复用原正式记录，已改动、撤回、身份歧义及来源损坏不恢复；人物状态主体与关系两端必须当前人物。13个正式测试组覆盖36场景及29显式子测试，42条结果含父组，不能扩成42独立测试；原失败与隔离问题见[确认完整性索引](evidence/README.md#跨报告确认复用证据与人物身份)。这些是正确性修复，不替代参考包独立验收、受控批次或文学收益。

## 仍需继续实施或量测

P1表示下一批优先解决作者正常流程；P2表示随后改善效率。相同缺口合并成一项，不重复计数。

| 顺序 | 合并缺口与作者收益 | 来源编号 | 当前状态及进入完成的条件 |
| --- | --- | --- | --- |
| 2 / P1 | 从下载到电脑的完整ZIP恢复独立作品 | SF04 | 本机已登记备份可恢复，外部文件选择入口未接；需新数据目录仅凭ZIP恢复所有表、坏包零写入、旧任务只读且不自动收费 |
| 3 / P1 | 正文/规划/审稿各自保存用途参数 | NF03、SF07、Ai-Novel用途专项 | 用途模型已有，一套参数仍共享；需温度0、删除回默认、作品隔离、真实请求冻结及旧请求不改 |
| 4 / P2 | 有版本的可复用作者写法 | NF04、MM05、SF08、91Writing顺序1 | 作品风格与一次要求已有，命名/变量/版本库未实现；需缺变量先阻断、特殊符号按字面量、回退追加版本、完整备份及历史可追溯 |
| 5 / P2 | 安全查找替换与联动改名 | SF05、91Writing顺序2 | 跨资料搜索和稳定实体改名已有，正文替换链未实现；先当前章命中预览/逐项选择/一个修订/一次撤销，再全书原子处理与并发全拒绝 |
| 6 / P2 | 专业人物、世界和物品档案 | NF02、MM04、Arboris顺序3、91Writing顺序3 | 知情范围/状态/关系已实现，动机/阻力/限制等专门字段不足；需旧卡无损、稳定ID、字段级范围、保存/复制/恢复与真实消费 |
| 7 / P2 | 场景排序、跨章移动、章跨卷移动 | NF05、SF06 | 同卷章/卷顺序可编辑，跨父级重组未接；需ID保持、正文不改、知情时点与指南影响预览、旧任务失效、备份往返 |
| 8 / P2 | 受控多章作者任务及旧书范围整理 | MM03/MM07、Ai-Novel批次专项 | 耐久单任务已有，跨章游标和旧书资料提取向导未接；逐章候选及作者确认，明确范围/种类/调用上限，失败不重做已完成章，未知执行不重发，参考不变本书事实 |
| 9 / P2 | 长时间编辑与审阅效率 | NF07/NF08、MM06、Arboris定向润色 | 字号/行距偏好、持续问题侧栏、逐条改稿建议及便捷维度选择仍缺；不为这些功能替换现有保稿和选区协议 |
| 10 / P2 | 开书准备模板与创意讨论 | NF06、Arboris开书专项 | 一句话向导及四阶段规划已有，可选准备模板/多轮讨论不足；默认只建立准备资料，取消/失败不留半套，不自动产生费用 |
| 11 / 量测 | 大作品、恢复时间与真实作者效率 | Ai-Novel大稿、各专项U项 | 当前state读全书，目录全部渲染；300/1000章首开、切章、连续输入及大包恢复需量测，不能先宣称卡顿或性能领先 |
| 12 / 量测 | 真实小说检索、审稿准确率、文学偏好 | 七专项U项及W21 | 合成30/100/300章工程材料已测，真实作品标注/误报漏报/等预算七竞品对照未完成；文学预算不足完整新对照，保留失败，不降门或扩预算 |

可选卷章范围出版、卷末对账、定时备份、DOCX/PDF仍在后续范围。多用户、手机端、社区、漫画、冒险产品及任意插件不作为当前本地小说工作台必须追齐的内容。
