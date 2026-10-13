# 七份专责报告

这些文件由各项目专责完成后归档，保留原审查结论和历史验收建议；公开副本将本机路径脱敏为 `./.audit-cache/2026-10-13/` 复跑目录，范围和原件指纹见[归档说明](../evidence/archive-privacy.md)。探针副本已归档到[upstream-probes](../evidence/upstream-probes/)。原报告中的桌面/手机建议属于当时审查范围，当前产品验收只要求电脑端。最新裁决以[交叉复核](../cross-review/README.md)、[总报告](../README.md)和[当前路线](../roadmap.md)为准。

| 项目 | 专项报告 | 内容 |
| --- | --- | --- |
| NovelForge | [作者流程与卡片上下文](agent-novelforge.md) | 15维对照、3条写入/消费链、旧调度器与当前Runtime区别、提取/历史边界 |
| MuMuAINovel | [建书、状态、任务与迁移](agent-mumu.md) | 25维对照、批量/写后分析、局部修改、最新状态、导出/导入边界 |
| arboris-novel | [蓝图、多候选与高级链断点](agent-arboris.md) | 普通入口与高级后台、护栏/入库断点、秘密过滤、13类能力对照 |
| Ai-Novel | [长篇记忆与实际材料](agent-memory-ai-novel.md) | dev退役模块、分块/重排、层次历史、范围策略和写后维护 |
| 91Writing | [作者入口、材料与保存](agent-91writing.md) | 书架/提示库/富文本、真实Writer路由、初始化/切章、占位成功与旧备份 |
| webnovel-writer | [运行可靠性与两个产品线](agent-runtime-webnovel.md) | master/v8区别、指纹/归档、真实半写恢复、墨屿运行失败 |
| storyforge | [范围冻结、登记与章后交接](agent-storyforge.md) | 多书/场景、双hash采纳、材料/备份登记、章后摘要、条件实验纠偏 |

共同未测：真实模型文学质量、所有上游完整页面、完整上游构建测试、生产部署及真实供应商账单。上游组件R不能升级成整站R。
