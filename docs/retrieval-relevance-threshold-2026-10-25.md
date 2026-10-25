# 检索相关性下限：词面覆盖率（2026-10-25）

**结论先行**：`search_materials()` 新增 `min_term_coverage`（0–1，默认 `0.0`）和每条结果的 `term_coverage`。它按“命中的段落含有多少比例的查询词项”过滤，先过滤再取 `limit`，避免无关结果占满预算。默认值保持旧行为；工作台高级设置和 HTTP `retrieval_profile` 可显式开启。

这不是语义相关性模型：`term_coverage` 是**词面覆盖率**，不是概率、不是语义相似度，也不证明“每句话有来路”已经完成。

## 1. 旧行为：一个原书不存在的句子也能填满 20 条

`docs/real-corpus-baseline-2026-10-24.md` §3.2 已记录：

- 对原书不存在的句子，三本书各返回 **20 条**，正好等于 `limit`；
- `assemble_context()` 只按 `chapter_top_k` 取结果，未检查相关性；
- 根因是中文按重叠 n-gram 匹配 + 没有下限，不是“任何查询都必返回结果”。

原文把词项写成“中文单字词项”；按当前实现更正：`lexical_terms()`（`backend/moyu_context.py:266-274`）生成的是 **2–3 字重叠 n-gram**，单字不进入词项，`_COMMON_GRAMS` 还会排除一批常用词。机制结论不变：常用 n-gram 遍布全书时，一个不存在的句子仍能凑满 `limit`。

## 2. 机制：覆盖率是过滤器，不是第二检索器

`search_materials()` 的新增参数和返回字段（`backend/moyu_context.py:375-450`）：

- 参数 `min_term_coverage: float | None`，默认 `0.0`；必须在闭区间 `[0, 1]`，`NaN`、`inf`、布尔值、负数、`>1` 均抛 `ValueError`；
- 参数 `coverage_query: str | None`：覆盖率按这段聚焦文本计算；不传时退回检索 `query`；
- 每次调用还返回 `filtered_count`，只统计被 `min_relevance` 或 `min_term_coverage` 拒绝的候选，不把权限/分支排除混进去；
- 每条结果新增 `term_coverage`，计算为：

  `|coverage_terms ∩ candidate_terms| / |coverage_terms|`

  其中 `candidate_terms` 是该段标题、正文、名称、备注、描述和 `source_quote` 的 2–3 字 n-gram 集合；
- 过滤发生在 `ranked` 收集阶段，早于 `ranked[:limit]`，因此被拒绝的结果不会占用上下文预算；
- 结果必须先在检索 `query` 上有非零 BM25 分，才会进入覆盖率检查。`coverage_query` 只能**过滤**已检索到的结果，不会单独召回；
- 完整章节、设定记录和作者 pin 走各自独立路径，不受这个历史检索过滤器影响。

`assemble_context()` 的聚焦文本顺序（`backend/moyu_context.py:734-735`）：

1. `instruction.strip()`；
2. 若为空，用章节元数据（`title`、`chapter_goal`、`scene_plan`、`next_brief`、`pov`）；
3. 两者都为空时，才退回完整检索 `query`。

这样做的原因：`assemble_context()` 的检索 `query` 会包含当前正文末尾 700 字；用完整 `query` 算覆盖率会让“当前正文自己的词”抬高分母/命中，等于把待写正文当作检索证据。前两级优先避开当前正文；第 3 级是没有指令也没有章节元数据的退化路径，此时无法再区分“查询焦点”和“当前正文”，只能如实按完整 `query` 计算。

## 3. 接口与工作台

- HTTP `GET /api/context`、`POST /api/ai/prompt-preview`、`POST /api/ai` 的 `retrieval_profile` 接受 `min_term_coverage`；`backend/server.py:225-277` 负责归一化和 400 拒绝。
- 合法示例：`{"min_term_coverage": 0.1}`；越界示例 `1.5` 返回 400。
- 未传该字段的旧客户端保持默认 `0.0`；未知字段仍按原有校验拒绝。
- 工作台上下文设置新增“词面覆盖下限”输入（`frontend/app.js:969-971`、`:1772`），步长 `0.05`；其余 profile 字段仍是整数，覆盖率保留三位小数，避免 `Math.round` 把 `0.05` 吃掉。

## 4. 真实长篇 sweep：0.1 能清掉 absent，0.5 太激进

基准：三本公版长篇小说，各 120 回，共 **360 回 / 2,343,664 字**；每本书选 5/6/7 条 24 字逐字针，另用同一句原书不存在的句子测量 absent。命令：

```bash
.venv/bin/python -m benchmarks.real_corpus.run \
  --corpus /tmp/moyu-real-corpus/corpus \
  --db /tmp/moyu-real-corpus/coverage.sqlite3 \
  --out /tmp/moyu-real-corpus/result-coverage.json
```

`exit 0`。三本书 R@1 全为 `1.0`；下表是 `term_coverage_sweep.rows` 的实测值，格式为“平均返回条数 / absent 条数”：

| 书 | 查询数 | 0.0 | 0.1 | 0.25 | 0.5 | 0.75 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 红楼梦 | 5 | 20 / 20 | 16.6 / 0 | 6.0 / 0 | 1.0 / 0 | 1.0 / 0 |
| 三国演义 | 6 | 20 / 20 | 16.1667 / 0 | 2.1667 / 0 | 1.0 / 0 | 1.0 / 0 |
| 水浒传 | 7 | 20 / 20 | 12.7143 / 0 | 1.7143 / 0 | 1.0 / 0 | 1.0 / 0 |

读法：

- `0.0` 是旧行为，absent 20 = 填满 `limit`；
- `0.1` 把 absent 压到 0，同时保留 12.7–16.6 条/查询；
- `0.25` 更激进，只剩 1.7–6 条；
- `0.5`/`0.75` 只剩 1 条/查询——**不能**把 R@1=1.0 读成“仍能正常召回”；针是逐字窗口，1 条刚好还是它。

## 5. 关键反例：0.5 会挡掉真实长指令的证据

上面的 24 字针太理想。另取红楼梦第 16/31/46 回，把同一条 24 字针包进约 100 字的真实续写指令：

```text
请续写下一章，保持冷峻克制的叙述节奏，回收“<24字针>”这条线索，
并解释它和主角当前处境的关系，不要提前揭示结局。
```

结果：

| 章 | 指令词项数 | 相关章节 `term_coverage` | 短针 `term_coverage` |
| ---: | ---: | ---: | ---: |
| 16 | 110 | 0.345455 | 1.0 |
| 31 | 101 | 0.287129 | 1.0 |
| 46 | 105 | 0.314286 | 1.0 |

这三条相关章节都排第 1、都含完整针，但长指令把覆盖率分母拉大，相关章节只有 **0.287–0.345**。若把 `0.5` 设成默认，这三条真实相关证据会被全部过滤——这正是“阈值拍脑袋会把真证据挡掉”的实测反例。

因此：

- **默认保持 `0.0`**；不要因为真实 sweep 的 R@1 好看就改默认；
- 若作者要开启，`0.1` 是最保守的可用起点；它在 absent 清零的同时保留最多结果；
- `0.25` 也在三条长指令探针之上，但只验证了 3 条，不能宣称普遍安全；
- `0.5`/`0.75` 只适合短而精确的查询，不适合普通续写指令。

## 6. 回归与证据

本轮在当前工作树执行：

- `.venv/bin/python tests/context_pipeline_fixtures.py`：18 个测试全通过；新增用例覆盖 `min_term_coverage` 过滤、`coverage_query` 聚焦、`limit=0`、非法值；
- `.venv/bin/python tests/ai_contract_fixtures.py`：通过；HTTP `retrieval_profile={"min_term_coverage":0.5}` 回显 `0.5`，`1.5` 返回 400；
- `.venv/bin/python tests/context_http_fixtures.py`：通过；30/100/300 章，K=1/5/10 passage recall 1.0、citation_accuracy 1.0、illegal_materials 0（模拟 provider，不代表模型质量）；
- `.venv/bin/python tests/context_scope_intersection_fixtures.py`：通过；
- `.venv/bin/python tests/reference_passages_http_fixtures.py`：36 个测试通过（临时合成库、真实 loopback HTTP、free local provider）；
- `.venv/bin/python tests/real_corpus_fixtures.py`：通过；真实基准字段和 coverage sweep 结构有效；
- `node --check frontend/app.js && node --check frontend/author-workspace.js`：通过（只证明语法，不证明浏览器交互）。

## 7. 未验证与边界

- **不是语义相关性**：同义改写、指代消解、跨章节因果仍不在覆盖范围；`term_coverage` 高也不代表语义相关。
- **只有一条 absent 句子/书**：没有人工标注的“应当返回空”的查询集，`0.1`/`0.25` 不是产品定值，只是本轮可复算的实验值。
- **R@1=1.0 是词面针下限**：不能写成“长程语义召回 100%”；`docs/real-corpus-baseline-2026-10-24.md` §3.1 已说明这一点。
- **长指令探针只有 3 条**：0.25 的普遍安全性未证；0.5 的反例已证。
- **30 万字 × 3 部规则抽取链路已补跑**：2026-10-25 三书 360 回 / 2,344,024 字，注入 100 / 检出 100、基线 0 误报（见 `docs/real-corpus-acceptance-2026-10-24.md` §9）；本轮仍只测检索门槛，不改变 Q1 的 3000 字 77.8%（7/9）<80%，也不把规则抽取当成 AI 抽取质量。
- **真实模型未参与**：本轮是人造查询 + 本地 BM25，不是模型质量结论。
- **前端只做语法检查**：未跑浏览器自动化；字段和 HTTP 契约已有测试，视觉/交互仍需人工确认。

## 8. 涉及文件

- `backend/moyu_context.py`：`min_term_coverage` / `term_coverage` / `coverage_query`、profile 默认值与范围、`assemble_context` 聚焦文本；
- `backend/server.py`：HTTP profile 归一化与 400 校验；
- `frontend/app.js`：覆盖下限输入、步长 `0.05`、小数保留；
- `tests/context_pipeline_fixtures.py`：过滤、聚焦、非法值和默认行为回归；
- `tests/ai_contract_fixtures.py`：HTTP 回显与越界拒绝；
- `benchmarks/real_corpus/run.py`：`--coverage-thresholds` 与 `term_coverage_sweep`；
- `tests/real_corpus_fixtures.py`：sweep 结构断言；
- 本文与 `docs/CHANGELOG.md`、`docs/reference-guide.md`、`docs/real-corpus-baseline-2026-10-24.md` 的状态注记。
