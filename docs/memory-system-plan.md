# 墨屿长篇小说记忆系统方案（研究版）

状态：研究和设计阶段，尚未开始正式实现。

## 1. 产品目标

在长篇写作中，系统持续维护“可验证的故事记忆”，并在当前场景需要时召回相关事实、旧原文和未回收伏笔。系统的职责是减少遗忘和前后矛盾，不替作者决定正典。

## 2. 统一数据模型

核心实体：`project`、`chapter`、`scene`、`entity`、`fact`、`relation`、`plot_thread`、`foreshadowing`、`timeline_event`、`source_span`、`revision`、`memory_candidate`。

建议字段：

- entity：id、type、name、aliases、status、first_seen、last_seen
- fact：id、entity_id、text、state(candidate/canon/contradicted/resolved)、confidence、valid_from、valid_to
- relation：subject_id、predicate、object_id、source_span_id、state
- foreshadowing：setup_span、promise、target_chapter、state(setup/promised/paid_off/abandoned)
- source_span：chapter_id、scene_id、start_offset、end_offset、exact_text、revision_id
- revision：parent_id、created_at、author、diff、reason
- embedding_chunk：source_span_id、embedding_model、vector、token_range、index_version

每个 AI 生成的记忆都必须保留 source_span；没有原文证据的内容只能进入候选区。

## 3. 记忆生命周期

1. 捕获：作者保存章节后，按场景切段；识别人名、物品、地点、事件、时间和可能伏笔。
2. 消歧：先用别名、章节位置、关系邻居和人工确认匹配已有实体；同名不自动合并。
3. 候选：新增事实和关系进入 `memory_candidate`，展示原文、推断理由和置信度。
4. 确认：作者确认、修改或拒绝；只有确认的内容才能进入正典。
5. 索引：确认事实写入 FTS、向量索引和关系表；原章节变更时保留旧版本。
6. 召回：写作时按当前文本、活动人物、地点、时间和未回收伏笔做混合检索。
7. 检查：比较新文本和召回证据，输出冲突、状态跳变、时间不可能和悬空伏笔。
8. 修订：作者选择采纳、忽略或建立解释；系统保存理由和新版本，绝不覆盖旧证据。

## 4. 召回排序

`最终分 = 语义相似度 + 关键词命中 + 关系邻近度 + 故事时间匹配 + 正典可靠度 - 冲突风险`

召回结果按四组显示：

- 必须知道：当前场景直接涉及的确认事实
- 可能相关：语义相近或关系相邻的片段
- 需要检查：与当前句子可能冲突的旧证据
- 可以回收：尚未回收且与当前场景相关的伏笔

每条结果都显示“章节 / 原句 / 版本 / 为什么召回”。

## 5. 数据库分阶段

### 研究原型
Markdown 正文文件 + SQLite 元数据 + FTS5 + JSON 导出。向量先做可插拔接口，不把具体供应商写死。

### 单机正式版
SQLite 记录实体、关系、来源、修订和候选；sqlite-vec 或等效本地向量索引；所有表可随作品目录备份。索引损坏时可由正文和元数据重建。

### 规模版
向量数量或并发真正超过单机后，再接 Qdrant（Apache-2.0）。关系图仍先留在关系数据库；只有图查询成为瓶颈时才评估 Neo4j。

## 6. 质量评估

用一组人工标注的章节测试集评估：

- 实体召回率：旧人物/物品是否被找回
- 证据准确率：召回片段是否真的支持结论
- 冲突精确率：提示是否有真实矛盾
- 伏笔漏报率：已确认未回收伏笔是否被漏掉
- 误合并率：不同人物是否被合成一个
- 可解释性：作者能否一键回到原文
- 性能：保存后索引时间和检索延迟

先追求证据准确和误合并率低，再追求“记得更多”。

## 7. 暂不做

不训练新基础模型；不让 Agent 自动改正典；不先上 Milvus/Weaviate/Neo4j 全家桶；不做实时多人协作；不把整本书塞进上下文；不把没有出处的模型总结当事实。

## 8. 开源边界

借鉴 novelWriter、Manuskript、STARC、Logseq、Foam、SilverBullet、Markra 的产品思路；不复制实现。GPL/AGPL 代码若直接衍生需要履行 copyleft；MIT/Apache 代码需保留许可和版权；Weaviate 与 Neo4j 的仓库包含不同许可区域，采用前必须逐文件审查。
