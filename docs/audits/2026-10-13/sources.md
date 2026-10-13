# 固定版本、来源与审查身份

采集及审查日期：2026-10-13，Asia/Shanghai。以下版本是本轮固定输入，不声称将来仍为最新。星数不参与成熟度判定。

GitHub直接网页/普通Git下载在本轮部分请求中不可用；采用官方REST API的仓库、分支及固定提交归档。[evidence/source-metadata](evidence/source-metadata/) 保存公开元数据副本，个人邮箱已脱敏，固定提交身份未变。原始字节、原指纹与签名复验范围见[归档说明](evidence/archive-privacy.md)。上游源码缓存约定为 `./.audit-cache/2026-10-13/references/`，不提交进产品仓库。

## 固定输入

| 项目 | 仓库与固定提交 | 分支 | 本轮许可记录 |
| --- | --- | --- | --- |
| 墨屿 | [neural-network-ai-creation / 62f1c66](https://github.com/z19910928-afk/neural-network-ai-creation/tree/62f1c66a7b16bb84f2940dc1af48e685aaf58818) | GitHub main；本地codex/full-function-audit | 本项目自身文档规则 |
| NovelForge | [RhythmicWave/NovelForge / a485814](https://github.com/RhythmicWave/NovelForge/tree/a485814411546b3980df0a1696a0fb6a043d56ce) | main | LICENSE：AGPL-3.0 |
| MuMuAINovel | [xiamuceer-j/MuMuAINovel / 99f0aec](https://github.com/xiamuceer-j/MuMuAINovel/tree/99f0aec6ce3b1c65652d20007be930ebba6a6d20) | main | LICENSE：GPL-3.0 |
| arboris-novel | [t59688/arboris-novel / dd80c69](https://github.com/t59688/arboris-novel/tree/dd80c69bc30a80d68b375bd504c6bc308164ff9e) | main | 固定树未找到正式LICENSE；README徽章不作为文件证据 |
| Ai-Novel | [inliver233/Ai-Novel / 8b85b2b](https://github.com/inliver233/Ai-Novel/tree/8b85b2b6f29af4c69241be7c42417dbe4795db30) | dev，区别于README建议的lite | 固定树未找到正式LICENSE，README说后续补充 |
| 91Writing | [ponysb/91Writing / df3de03](https://github.com/ponysb/91Writing/tree/df3de0362e8e4028b76dffa78c5bba7b8bbd20a1) | main | LICENSE：MIT |
| webnovel-writer维护线 | [lingfengQAQ/webnovel-writer / 0af0c0b](https://github.com/lingfengQAQ/webnovel-writer/tree/0af0c0b2fc2e4104e8a12c1d62136c6595a6a983) | master；旧v6维护线 | LICENSE：GPL-3.0 |
| webnovel-writer新工作台 | [lingfengQAQ/webnovel-writer / a0bdd2d](https://github.com/lingfengQAQ/webnovel-writer/tree/a0bdd2d4e2f39bc3de32fc5cbfe605067b5b486b) | v8；README Scriptor8.2.2，宿主DSH | LICENSE：GPL-3.0-only |
| storyforge | [yuanbw2025/storyforge / 83ad023](https://github.com/yuanbw2025/storyforge/tree/83ad0231c7ab85f1b9725940861899b07a7d69b1) | main | LICENSE：MIT |

七个项目分别对照；webnovel两个分支属于同一个项目，不能充当第八个项目，也不能把两条产品线的能力混成一个默认网页产品。

NovelForge和MuMu的包/文档版本标识有差异时以固定提交为判断依据。Ai-Novel的dev旧记忆模块退役必须按当前路由/迁移核查；不能拿lite/main或旧README当这份代码。

这里只记录文件与元数据，不提供某种具体分发的法律判断。本轮观察设计与失败行为；没有复制上游实现、提示词、素材或小说示例到墨屿产品。

## 专责与交叉关系

用户要求每个项目至少一名专责、总计不少于七名；实际完成如下七份专责，失败的早期NovelForge代理不计作完成。

| 项目 | 完成专责的代理 | 独立交叉复核者 |
| --- | --- | --- |
| NovelForge | `/root/audit_novelforge` | `/root/audit_storyforge` |
| MuMuAINovel | `/root/audit_mumu` | `/root/audit_91writing` |
| arboris-novel | `/root/audit_arboris` | `/root/audit_novelforge` |
| Ai-Novel | `/root/audit_memory` | `/root/audit_91writing` |
| 91Writing | `/root/audit_91writing` | `/root/audit_storyforge` |
| webnovel-writer | `/root/audit_runtime` | `/root/audit_novelforge` |
| storyforge | `/root/audit_storyforge` | `/root` |

根代理另外在正式墨屿目录复现保存、采纳、回读、上下文、导出和桌面/手机页面，并统一裁决严重结论。复核者沿源码和必要探针重新取证，记录确认、下调或驳回；不是只对专责报告作摘要。

## 证据解释

| 等级 | 能证明的范围 | 不能推断 |
| --- | --- | --- |
| R | 本轮实际运行的具体页面、HTTP、组件或故障样本 | 整站所有路径、真实模型文学水平、线上稳定性 |
| E | 入口、处理、保存/消费的源码路径；风险也注明推导 | 本轮环境已部署、默认配置必可用、效果领先 |
| S | 表、字段或类型存在 | 作者已有可用入口和完整流程 |
| D | README、说明或参考项目编排要求 | 代码已连接、本轮已执行 |
| U | 未测或完整闭环证据不足 | 不能为了列表完整改写成成功 |

上游目录来自归档，没有`.git`；提交归属依据官方采集记录，不以归档目录内 `git rev-parse` 假装二次验证。墨屿本地 HEAD 已直接核对；产品文件改动情况由最终Git差异校验。
