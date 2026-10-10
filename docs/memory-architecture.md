# 长篇小说记忆中枢设计

## 目标

让系统在写作时积累可追溯的“故事事实”，在后文按当前场景召回相关事实，并在写入前提示矛盾、未回收伏笔和人物状态跳变。记忆不会由模型直接改写正典。

## 四层记忆

1. **正典实体层（Canonical）**：人物、地点、组织、物品、能力、规则、剧情线、伏笔、时间事件。每条记录有稳定 ID、别名、首次出现章节、最近确认章节、状态和来源片段。
2. **关系图层（Graph）**：`人物—拥有→物品`、`人物—位于→地点`、`伏笔—回收于→章节`、`事件—先于→事件`。用于解释“为什么召回”和发现断裂关系。
3. **语义索引层（Retrieval）**：章节/场景按小段切分，保存 embedding 与关键词。写新段落时用当前段落、角色、地点和未回收伏笔检索 Top-K 片段；结果必须带原文和章节来源。
4. **版本与审计层（Evidence）**：保存快照、变更、冲突、确认人和时间。任何 AI 建议先进入候选区，用户确认后才更新正典。

## 写作流水线

`输入新段落 → 提取实体/事件/候选事实 → 关键词 + 向量召回 → 时间线/关系/属性冲突检查 → 显示“相关旧记忆”和“可能未回收伏笔” → 用户确认 → 写入正典并建立来源链接 → 建立新 embedding`

## 记忆卡片最小字段

```json
{
  "id": "entity-uuid",
  "type": "character|item|place|event|thread|foreshadowing",
  "name": "旧钥匙",
  "aliases": ["铜钥匙"],
  "facts": [{"text": "边缘有月牙缺口", "source": "ch-03#p12", "confidence": 1}],
  "relations": [{"kind": "owned_by", "target": "character-uuid", "source": "ch-03#p12"}],
  "status": "candidate|canon|contradicted|resolved",
  "story_time": {"start": "2024-03-01", "end": null},
  "embedding": null,
  "updated_at": "2026-10-09T00:00:00Z"
}
```

## 重要的防混乱规则

- 新事实默认是 `candidate`，不能覆盖旧事实。
- 同一实体的别名合并要可撤销；同名人物不能自动合并。
- 冲突显示原文证据，例如“第三章说左手受伤，第十二章写左手持剑”，不让模型静默选择一条。
- 召回结果显示来源章节、原句和版本，避免只给模型无出处摘要。
- 伏笔有 `setup`、`promised`、`paid_off`、`abandoned`，结尾前检查 `promised`。
- 长上下文按当前场景动态召回，不把整本书塞给模型。
- 重要实体和关系支持锁定，AI 只能提议变更。

## 推荐实现路线

- 原型：浏览器 LocalStorage + JSON，现有“标记捕获 → 人工确认”流程。
- 第二阶段：本地 SQLite/SQLite-vec（或同类向量扩展）保存实体、关系、片段和快照；全文搜索用 FTS。
- 第三阶段：可替换 embedding 提供商；默认支持本地模型或用户自己的 API，显示隐私和成本。
- 第四阶段：连续性检查器、伏笔面板、人物状态时间线和可解释召回。

## 开源借鉴与边界

novelWriter/Manuskript/STARC 提供作品结构；Logseq/Foam/SilverBullet 提供本地 Markdown、双向链接和版本思路；Markra 提供“AI 先预览再应用”的交互思路。直接采用 GPL/AGPL 代码需履行对应 copyleft，MIT 代码需保留声明；本项目当前只借鉴设计，不复制实现。
