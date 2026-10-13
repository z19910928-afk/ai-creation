# 根代理独立复核：StoryForge 与墨屿严重结论

日期：2026-10-13。复核者：`/root`；原专责：`/root/audit_storyforge`。墨屿正式目录的 HEAD 与固定基线相同。此记录区分直接重新运行、独立源码复读与引用他人结果。

## StoryForge 专项结论

| 原主张 | 独立复核与证据 | 裁决 |
| --- | --- | --- |
| 多书和长篇工作区有作者入口 | `src/App.tsx:72` 注册 LongformLibraryPage；专项列出的 `create-workspace.ts:136` 建立归属身份，符合多书入口。未运行上游页面 | 确认 E；完整页面 U |
| 选区被冻结，采纳前保护作者新修改 | 独立读取 `FloatingToolbar.tsx:142-183`：先保存、冻结范围；采纳前比较当前 HTML hash。`selection-edit-durable.ts:859-878` 正式采纳携带源文本和 HTML 双 hash 条件 | 确认 E；没有独立运行该上游保存事务，不升级为 R |
| 候选具有可恢复的正式写入身份 | `selection-edit-durable.ts:827-856` 持久采纳意图与确认事件；`:880-902` 验证期望正文并记录正式提交，避免把回调当前章作为目标 | 确认 E；完整崩溃恢复 U |
| 模型不能自行指定其他作品身份 | `registry/adopt.ts:75-84` 拒绝结构化输入的 World/Work owner ID，正式归属由 resolveScope 得出 | 确认 E；未做上游权限攻击实验 |
| 改正文会使递进摘要失效 | `registry/adopt.ts:31-44` 按章、卷和全书将摘要节点标 stale。不是只在某个 AI 按钮回调操作 | 确认 E；是否所有外部写入都收口此处，仍须运行验收 |
| 章后摘要和交接参与后续取料 | `run-chapter-memory.ts:22-79` 固定章与正文、按源文本 hash 写 summary/handoff；`ChapterEditor.tsx:1148,2509` 为实际调用；`text-normalization.ts:62-75` 校验来源；`context-sources.ts:1516` 仅取 verified 交接 | 确认 E，补充入口和消费链；没有真实模型提取质量 R |
| 128 张表和统计测试证明第一梯队 | 专责实际只运行表一致性和统计组件；未复跑上游长篇/页面/模型 | 驳回这种外推；表结构/计算运行不支持文学或产品整体等级 |
| 官网体验禁用模型说明所有模型功能不存在 | `showcase/policy.ts:1-8` 限制体验构建；源码模型及作者链另有入口 | 驳回这种外推；需按具体构建说明 |
| 浏览器存储和记住密钥可直接复制 | `stores/ai-config.ts:267-276` 明确记住时持久配置；墨屿当前服务端环境配置、独立数据库的边界应保留 | 确认设计取舍，未断言上游发生泄密或数据丢失 |
| 墨屿新故事入口会在正常旧稿页面直接覆写 | `app.js:135-136` 的 isFreshProject/workflowCard 及 `:218` 限制正常入口；直接执行函数的条件实验不等于普通页面操作 | 下调为多书扩展前的保护要求，不进入正常入口 P0/P1 缺陷清单 |
| 生成完成后任意切章都可错采纳 | `app.js:226` 切章清候选，因此简单切章不能如此泛化。真实页面复现的是请求尚未返回时切章，迟到回应重新赋 aiDraft | 修改触发条件；确认这个异步窗口 R |
| 错章结果已存入数据库 | 根代理真实页面显示乙章内错候选，数据库回读没有；候选审计使版本增加，后续 PUT 冲突 | 驳回持久化误写结论；确认浏览器错采纳、重复采纳、假保存问题 |

StoryForge 此次仍是源码链为主。没有完整应用启动、真实模型、跨设备、百万字符性能或文学效果实证。根总报告不采用“第一梯队已经认证”一类表述。

## 墨屿严重结论的独立复现

以下并非把专项报告换一种写法：根代理在正式目录、临时数据库中重新运行实际服务器与浏览器。免费模拟模型仅替换生成文本，保存、版本和页面处理均为原产品代码。

| 主张 | 根代理独立结果 | 最终边界 |
| --- | --- | --- |
| 保存响应期间继续输入会冲突 | `root-browser.json:typing_during_save` 两次版本 `[6,6]`，最新输入在浏览器、不在数据库，backend_online=false | R 页面+HTTP；未宣称浏览器文字已经丢失 |
| AI 决策后正文保存冲突 | `runtime-results.json:ai_adoption_version_conflict` 生成200、decision201、PUT409；页面采纳也独立复现相同停存 | R HTTP+页面；候选没保存，审计已保存 |
| 异步切章和重复采纳 | `root-browser.json:ai_switch_chapter` 甲候选进入乙章浏览器，同候选采纳两份；数据库无该候选 | R 页面、生成文本模拟；不写成数据库错章 |
| 已确认证据漂移阻止正文保存 | `runtime-results.json:accepted_memory_anchor_edit` 插字后PUT400，原稿仍在，新稿未存 | R HTTP；事务保护原稿，但正常改稿失败 |
| 拒绝记忆后打开错误作品 | `root-browser.json:rejected_memory_reload` 新浏览器显示starter、后端false，数据库真实标题仍在 | R 页面+真实回读；不是数据库覆写 |
| 旧备份不能还原已删除章 | `runtime-results.json:deleted_chapter_json_restore` DELETE200、导入409唯一编号冲突、回读0章 | R HTTP；合法旧稿件包无法恢复这条对象 |
| 长章润色覆盖范围超输入范围 | `root-browser.json:long_chapter_polish` 原15412，真实预览无章首；等待异步采纳完成后浏览器正文90，章首消失；数据库仍15412 | R 页面+真实提示预览，文本模拟。修正最初读回应过早的实验；不写成持久丢稿 |
| 旧审稿放行新正文 | `root-components.json:review_not_bound_to_body_revision` 改成不同正文后passed；导出manifest allowed=true、issues=[] | R 真实HTTP；当前稿未获得有效新审查 |
| 未来和独知材料注入 | `root-components.json:context_scope_and_middle_evidence` 实际ai_context保留future memory/timeline与villain-only state | R 原组件，状态输入受控；不是模型生成剧透率 |
| 召回后丢中段证据 | 同结果中旧章被选中、关键句未进入prompt | R 原组件；不宣称全体召回准确率 |
| 流、配额、锁和取消 | 根重跑持久 `runtime_probe.py`，实际HTTP/本机模拟提供方：首回应等完整流；配额1调用2次；生成期间保存延迟>1秒；running取消后提供方正常结束 | R 受控运行；没有真实供应商收费或取消证据 |

## 对旧文档的纠偏

`docs/comparison-six-projects-2026-10-12.md` 的“可靠闭环”“保守 token 上界”“scheduler 持久优先级”等结论不能继续作当前验收依据；本轮证据已分别证明保存/范围失败、字符估算非可靠 token 上界、旧 scheduler 为内存 FIFO。当前 NovelForge 工作流由 WorkflowRuntime 承担进程内控制，并通过 StateManager/ExecutionState 持久保存与恢复状态，不能因旧类问题否定整个主流程。

旧文档保留为历史，不删除。最新裁决以本次总报告、交叉复核及原始结果为准。
