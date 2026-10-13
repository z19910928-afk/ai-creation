# 独立交叉复核索引

日期：2026-10-13。七个项目均由不同于原专责的复核者重新读取固定源码；必要时重跑原组件。复核不是仅对专项报告作摘要，也不代表七个上游应用都已完整运行。

## 项目覆盖

| 项目 | 原专责 | 独立复核者 | 复核记录 |
| --- | --- | --- | --- |
| NovelForge | audit_novelforge | audit_storyforge | [12 项核查及版本组件实测](cross-storyforge-novelforge-91writing.md) |
| MuMuAINovel | audit_mumu | audit_91writing | [9 项核查及隔离组件复跑](cross-91writing-memory-mumu.md) |
| arboris-novel | audit_arboris | audit_novelforge | [10 项核查及组件复跑](cross-novelforge-arboris-webnovel.md) |
| Ai-Novel | audit_memory | audit_91writing | [10 项源码链核查](cross-91writing-memory-mumu.md) |
| 91Writing | audit_91writing | audit_storyforge | [12 项源码链与原探针范围核查](cross-storyforge-novelforge-91writing.md) |
| webnovel-writer | audit_runtime | audit_novelforge | [10 项核查及文件事务崩溃复跑](cross-novelforge-arboris-webnovel.md) |
| storyforge | audit_storyforge | root | [源码链及墨屿严重结论复核](root-storyforge-and-severity.md) |

三份交叉报告分别核查 19、24、20 项，共 63 项。根代理另行复核 storyforge，并在墨屿正式目录使用临时数据库、真实本机服务和浏览器复现严重问题。webnovel 的 master 与 v8 属于同一个项目，不重复计数。

## 对最终结论的修正

| 裁决 | 最终采用的边界 |
| --- | --- |
| 确认 | 七个项目分别有值得学习的创作规划、材料组织、选区处理、章后资料或恢复设计；具体优势以源码入口、保存与后续消费链为依据。 |
| 确认 | 墨屿保存、采纳、记忆回读、旧 JSON 恢复、长章处理及审稿有效性存在可复现问题，详见[原始证据](../evidence/README.md)。 |
| 下调 | 墨屿浏览器中错章采纳或整章覆盖，不写成数据库已持久丢稿；此次数据库因保存冲突保留了原稿。 |
| 下调 | 墨屿已有持久单任务、取消/重试接口和缓冲重放；作者任务入口、真正逐段返回及多章节批量流程尚未验收。 |
| 驳回 | 将后台范围补丁、历史版本、人物状态、关系/时间线/伏笔和材料预览写成完全不存在。部分已有页面入口，其他缺口是未接通或范围不可信。 |
| 驳回 | 用 NovelForge 旧内存 scheduler 的组件结果否定当前 WorkflowRuntime。当前有持久状态和暂停恢复源码，意外崩溃与收费动作不重复仍未测。 |
| 下调 | Ai-Novel 的摘要和写后任务不等于完整事实确认及改稿失效闭环；smart context 已过滤过去章，风险在额外 memory/vector 层。 |
| 下调 | MuMu 的过去章记忆过滤确实存在；最新人物状态是另一条入口。旧基稿局部应用是隔离组件实测，取消断链仅限定相应页面。 |
| 驳回 | 用 91Writing 旧备份组件缺陷否定当前设置页真实 JSON 导入导出；切章探针仅证明编辑器误覆盖，持久错写为源码风险。 |
| 下调 | webnovel v8 的文件事务恢复实测不等于完整写作或收费任务恢复；选区请求在内存中，不支持由此宣称跨重启恢复。 |
| 驳回 | 用表数量、测试数量、星数、默认审稿分数或宣传的长篇容量认定文学质量已达第一梯队。 |
| 驳回 | 将直接调用墨屿起稿函数的条件实验当作旧作品正常页面入口必然覆写。最终只保留多作品扩展前需要保护这一要求。 |

## 阅读顺序与原文说明

先读[总报告](../README.md)，再按需要查看[能力矩阵](../capability-matrix.md)、[缺陷清单](../findings.md)和上述独立复核。专项与三份交叉报告是归档原文，保留当时的临时缓存路径；例如交叉原文说报告“位于本目录”，归档后实际对应 `../projects/`。这些路径的重新定位见[证据说明](../evidence/README.md)。

原文中较宽的措辞及个别上游源码路径已由交叉报告明确纠正。最终裁决以总报告、缺陷清单和交叉限定为准。证据等级 R 表示明确范围的实际运行，E 表示源码链，S 表示结构，D 表示文档，U 表示未验证；不把任何一级自动扩大到完整产品或真实模型效果。
