# AI 创作/写作系统能力审查（2026-10-09）

本审查以墨屿当前单用户、本地优先原型为基线：已有章节编辑、设定卡、情节/伏笔看板、搜索、JSON 备份，以及“候选记忆→人工确认→证据召回”的设计。以下是对优秀开源编辑器、协作产品和研究论文的能力核对。

## 优先级

### P0：可信写作闭环（应在首个 AI 版完成）

1. **结构化 AI 编辑与 provenance**：续写、改写、扩/缩、翻译、校对、总结既支持选区也支持全文；模型输出统一为可校验 patch。逐段 diff、接受/拒绝、撤销、锁定正典；保存 prompt、模型、上下文、来源 span、时间和用户决定，支持导出 AI 使用披露。参考 Sundial 的逐编辑历史；HaLLMark 实验显示 provenance 能提高作者控制/所有权（https://github.com/sundial-org/sundial-desktop；https://arxiv.org/abs/2311.13057）。
2. **长篇工作流编排**：outline → scene plan → draft → revision → continuity/fact-check → finalize/export。项目级实体、目标、约束和上下文预算；计划/中间摘要后生成。规划训练在长文上提高组织、相关性和可验证性（+2.5% ROUGE-Lsum，人评 win/loss 3.60；https://arxiv.org/abs/2410.06203）。
3. **作者主导的可控生成**：风格、语气、视角、节奏、受众、字数等软约束与字数/禁词/事实等硬约束；候选并排、保留原文、明确采纳。提供摘要/问题/建议等“views”以支持自我修订，而非默认代写（Textfocals：https://arxiv.org/abs/2403.01055）。CoDI-Eval（https://ojs.aaai.org/index.php/AAAI/article/view/29734）和 UltraBench 表明模型在复杂、多属性约束下仍不稳定，必须在产品中显式校验约束。
4. **评测与质量门**：中文小说 golden set；实体/事实召回、证据精度、冲突精度、伏笔漏报、时间线一致、风格/节奏、编辑距离/Revision Distance、人工 pairwise。EditEval 证明编辑指标常与人评低相关（https://aclanthology.org/2024.conll-1.7/）；WritingBench（6 域/100 子域）与 LongGenBench（长文指令遵循）可作外部参考（https://arxiv.org/abs/2503.05244；https://proceedings.iclr.cc/paper_files/paper/2025/hash/141304a37d59ec7f116f3535f1b74bde-Abstract-Conference.html）。记录过程指标：请求/接受/采纳、延迟、人工编辑量、ownership/agency、错误发现率（CoAuthor/Wordcraft/HALIE/Parachute）。
5. **证据化检索与事实核查**：召回片段、章节/版本、claim→evidence、置信度/新鲜度；候选事实无出处只能待确认。SWIF²T 显示 planner+investigator+reviewer+controller 和全文/文献检索能提升反馈具体性，但仍有幻觉和成本/延迟问题（https://arxiv.org/abs/2405.20477）。
6. **可恢复性、成本与隐私**：流式/取消、并发候选、失败重试、模型路由和 token 成本/延迟可见；本地/BYOK、断网继续写、加密和数据保留策略。禁止静默改正典。

### P1：体验与差异化

7. **协作**：Yjs/Tiptap CRDT、presence/游标、评论和 tracked changes、离线同步、快照/比较/恢复、权限；Yjs 官方提供多编辑器绑定、offline、snapshot、undo/redo、shared cursors（https://docs.yjs.dev/）；Tiptap 提供历史与评论（https://tiptap.dev/docs/collaboration/getting-started/overview）。
8. **连续性和写作分析器**：实体别名/关系、人物状态、时间线、伏笔生命周期、节奏/对话占比/强度曲线；Error/Warning/Info 内联高亮并回跳原文（Novalist：https://github.com/Drommedhar/novalist）。
9. **编辑器与出版互操作**：富文本、表格/公式/媒体、Markdown/GFM/Pandoc、DOCX/EPUB/PDF 往返、可访问性、快捷键、移动端、Focus/Typewriter 模式。AiEditor（1.8k★）和 MarkText（62k★）可作能力基准（https://github.com/aieditor-team/AiEditor；https://github.com/marktext/marktext）。
10. **多模型与本地 RAG**：可替换 provider、按任务路由、上下文/检索策略可视；Novelforge 展示 outline→draft→review→finalize + local RAG + multi-model routing（https://github.com/LunaRime/novelforge）。
11. **高效发散交互**：整段候选、并行流式、键盘优先、可回退再生（Arrows：https://github.com/p-e-w/arrows）。

### P2：扩展路线

- 可见计划、证据和沙箱的多智能体专家角色；
- 图像参考、语音转写、分镜/剧本等多模态；
- 行业模板、排版/编译、版权与 AI 使用披露；
- 教练模式（让作者修改而非替作者写）、跨项目风格画像；
- 插件/工作流 SDK 与自动化评测仪表盘。

## 设计结论

“暂不实时协作”可作为 MVP 取舍；P0 的 patch/provenance、版本可恢复、作者控制、证据召回和过程+文本评测则是可信 AI 写作的底线。直接生成模式应降级为显式选择，默认采用 Human-Primary（AI 编辑用户文本）而非 AI-Primary 起草；379 人 CHI 实验发现后者虽提高效率，却降低 ownership、独特性和责任感（https://arxiv.org/abs/2403.12004）。
