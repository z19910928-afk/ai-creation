# 开源设计参考（2026-10-09）

本项目只借鉴公开的交互与产品思路，不复制代码、品牌或素材。实现前需继续核对许可证和第三方依赖。

- [novelWriter](https://github.com/saga-soft/novelWriter) · GPL-3.0 · 约 3.2k stars：纯文本、多文件小说项目、章节元数据和交叉引用。借鉴“正文是可读文件，长期可迁移”。
- [Manuskript](https://github.com/olivierkes/manuskript) · GPL-3.0 · 约 2.4k stars：人物、世界、情节、卡片、时间线、章节重排和多格式导出。借鉴“写作与规划在同一个工作流”。
- [Story Architect](https://github.com/story-apps/starc) · GPL-3.0 · 约 394 stars：大纲、卡片、时间线、关系/地点报告和多种导入导出。借鉴“结构化报告帮助发现连续性问题”。
- [Logseq](https://github.com/logseq/logseq) · AGPL-3.0 · 约 45k stars：本地优先、Markdown/Org、双向链接、图谱和插件。借鉴“每个实体可被反向追踪”，同时注意其 DB/mobile 稳定性警告。
- [Foam](https://github.com/foambubble/foam) · MIT · 约 17k stars：Markdown、双向链接、反向链接、模板和图谱。借鉴“轻量链接网络”，保留 MIT notice，不复制实现。
- [SilverBullet](https://github.com/silverbulletmd/silverbullet) · MIT · 约 6.3k stars：磁盘 Markdown、Git 修订、查询和模板。借鉴“记忆必须可回溯到文件和版本”。
- [Markra](https://github.com/markrahq/markra) · AGPL-3.0 · 约 908 stars：Markdown/WYSIWYG、AI 修改预览后再应用、备份与导出。借鉴“AI 建议必须可审阅，不直接改正典”。
- [Novalist](https://github.com/Drommedhar/novalist-official) · MIT · 约 38 stars：人物、地点、物品、关系、情节网格、时间线和多格式导出。借鉴“世界观实体与正文用同一套项目文件组织”。

## 本项目的取舍

第一版采用单用户、本地优先、结构化记忆候选 + 人工确认：章节中的 `【人物】`、`【物品】`、`【地点】`、`【伏笔】` 标记会进入候选池；确认后才能成为事实记忆。这样比让模型直接“猜并写入”更安全，也能在冲突时追溯来源章节。

后续再接入向量检索、关系图谱、时间线和版本差异。GPL/AGPL 项目若直接衍生代码需遵守相应 copyleft；MIT 项目需保留版权和许可证声明；Outline 的 BSL 1.1 仅作 source-available 参考，不视为宽松开源。
