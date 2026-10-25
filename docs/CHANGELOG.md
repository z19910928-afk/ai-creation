# 更新记录

## 2026-10-25 · 审计复核修复：F1–F6 文档与证据补齐

复核结论为 `needs_revision`（`docs/audit-review-team-2026-10-25.md`），本轮只做文档与 `--help` 修复，**未改任何产品逻辑、未跑真实模型**。

- **F1 真实模型拒绝率与可用性进文档**：`docs/real-corpus-extract-2026-10-24.md` 新增 §6.5——5 章轮拒绝 **5/28 = 17.9%**、2 章轮拒绝 **14/31 = 45.2%**，两个分母不同源、**不可合并**；单章均值 **34.4 s（5 章，`per_chapter_ms.mean=34437.66`）/ 51.3 s（2 章，`51337.99`）**；360 章单线程 **≈3.4 h 仅为按 34.4 s/章外推的粗估、非实测**。同项状态写入 `docs/audit-response-2026-10-25.md` §7 第 7 条，并注明仍受 `repeat=1` 限制。
- **F2 `sc-15` 超冲缺陷归属澄清（不声明已修）**：`docs/consistency-budget-v1.2-2026-10-23.md` 新增 §6.1.1，`docs/audit-response-2026-10-25.md` §7 新增第 8 条。写清 `characters=4661` / `target=4000` / `range=3600–4400` / `stop_reason=target_reached` / `target_reached=true` / `within_tolerance=false` / `overshoot=261` 是**三个不同口径**：`target_reached` 只判下限（`backend/moyu_continuation.py:872`、循环侧 `:794-796` = `if` → `stop_reason=TARGET_REACHED` → `break`），`within_tolerance` 才判带宽（`:873`），故 `target_reached` 不等于带内达标。该「溢出不可回收」显式失败语义**由 continuation-80 / 引擎 owner 承接**，本轮不覆盖；`sc-15` 仍计入 4000 子集（4/6=66.7%），Q1 仍 7/9=77.8%。
- **F3 §6.4 标题数修正**：`四档` → `五档`，并注明表内后 3 行是同一条真实模型链路分 3 次运行拆出，不是三套能力。
- **F4 `--audit-limit` 负数语义写入 `--help`**：明确负数按 `max(0, N)` 处理、等同 `0` 即导出全部证据、不报错；`docs/real-corpus-extract-2026-10-24.md` §7 同步实测矩阵（缺省 `(5,3)(4,3)`、`0` 与 `-5` 均 `(5,5)(4,4)`、`1.5` → `exit 2`）。
- **F5 时间口径修正**：`docs/audit-response-2026-10-25.md` §2.1 第三行 `16:04` → **`16:50:48`（mtime）**，并把另两行补齐为 `16:56:21` / `16:52:53`，表头标注「文件 mtime，非 `generated_at`」。
- **F6 `generated_at` 证据链修正**：原引用的 `/private/tmp/audit-full-test.json`（mtime `17:46:44`，早于提交 `17:52:38`，`generated_at` 仍为旧硬编码）不再充当修复证据；新增 §5.1 说明该错位，并改用修复后重跑的 `/private/tmp/audit-full-test-postfix.json`（`generated_at=2026-10-25T18:27:09+08:00`，与 mtime 逐秒吻合，9/9 可见）。

## 2026-10-25 · 安全加固：HTTP 非回环绑定需显式 opt-in

- `backend/server.py` 新增 `resolve_host()`：默认只允许 `127.0.0.1`、`localhost`、`::1`；设置 `MOYU_HOST=0.0.0.0` 等非回环地址时必须同时显式设置 `MOYU_ALLOW_REMOTE=1`，否则启动即拒绝，避免无认证本地 API 被误暴露到局域网。
- `tests/server_bind_fixtures.py` 4 条回归覆盖默认、空值、拒绝、显式放行；报告与残余风险见 `docs/security-audit-current-2026-10-25.md`。显式远程仍无认证，公网/多人部署不放行。

## 2026-10-25 · 审计修复：真实模型证据、Q1 口径与 F1 边界

- `benchmarks/real_corpus/extract.py` 新增 `--audit-limit`（默认每章 3 条，`0`=全部）和 `audit_total` 字段；`generated_at` 从硬编码旧日期改为运行时刻，避免 JSON 时间戳误导。实测 `--mode dry-run --limit 2 --audit-limit 0` 的 `audit` 条数与 `totals.assertions` 一致（9/9）。
- 授权服务真实复跑已进入模型：5 章 33 120 字、23 条断言、5 条 `empty_value`、0 条 `provider_error`（172.19s，`/private/tmp/moyu-real-current-5.json`）；2 章 12 463 字、17 条断言、14 条拒绝（6 `empty_value`、8 `quote_mismatch`），0 条 `provider_error`。两次都是 `repeat=1`，且 5 章 JSON 只有 11 条逐字证据可见，不能写成真实模型质量通过。
- Q1 口径保持 **7/9 = 77.8%，`q1_target_met=false`**；3000 字 9 场景不含 `sc-20`，`sc-15` 是 4000 字场景。另列明确标注的诊断指标 7/8=87.5%（剔除 `sc-19` 外部预算中断），不替代 Q1；4000 字仍为 4/6=66.7%。
- F1 边界写死：抽取器只验逐字证据，不验 subject/value 语义；真实样本 `封肃 → real_name=封` 可通过，只有补上竞争值后才由一致性引擎报 high 冲突。11 条可见真实断言在每章单 revision 下为 100 A、0 findings，但不能外推为 23 条全量或生产零误报。详见 `docs/audit-response-2026-10-25.md`。

## 2026-10-25 · 检索相关性下限：min_relevance + 词面覆盖率（默认 0，opt-in）

### 改了什么

- `search_materials()` 增加 `min_relevance`（非负 BM25 下限，默认 `0.0`）和 `min_term_coverage`（0–1，默认 `0.0`），并接受 `coverage_query`：在排序后、取 `limit` 前同时过滤；每条结果写入 `term_coverage`，返回体写入 `filtered_count`（只计被阈值/覆盖率拒绝的候选，不含权限排除）。
- `assemble_context()` 同样暴露 `min_relevance` / `min_term_coverage`，把阈值传给历史检索，不改变预算或其他检索策略。
- `assemble_context()` 用 `instruction` 作为覆盖率聚焦文本（为空才退到章节元数据；两者都为空才退回完整 query，这是唯一会包含当前正文末尾 700 字的退化路径）。
- HTTP `retrieval_profile` 与工作台上下文设置均暴露该字段；越界返回 400，旧客户端不传字段仍保持旧行为。
- 真实基准新增 `--coverage-thresholds` 和 `term_coverage_sweep`。

### 实测

- 三本公版长篇各 120 回，共 360 回 / 2,343,664 字；每书 5/6/7 条 24 字逐字针，R@1 全 1.0。
- 一致性注入补跑（2026-10-25）：三书 360 回 / 2,344,024 字；按事实池容量分配 3/48/49，注入 100 / 检出 100（recall 全 1.0）；干净库 main/book2/book3 均 100.0/A、0 findings。命令与明细见 `docs/real-corpus-acceptance-2026-10-24.md` §9。
- absent 句在阈值 0.0 时三书均返回 20 条；0.1 时全部降为 0，平均返回条数分别为 16.6 / 16.1667 / 12.7143；0.25 时降至 6.0 / 2.1667 / 1.7143；0.5 与 0.75 只剩 1.0 条/查询。
- **关键反例**：把同一条 24 字针包进约 100 字续写指令后，红楼梦第 16/31/46 回相关章节的覆盖率只有 0.345455 / 0.287129 / 0.314286；若默认 0.5，会把这些真实证据全部过滤。因此默认仍为 0.0，0.5 只适合短精确查询，不能读成“语义相关性”。
- 回归：`tests/context_pipeline_fixtures.py` 18 个测试通过；`tests/ai_contract_fixtures.py` 通过（0.5 回显、1.5 返回 400）；`tests/context_http_fixtures.py` 通过；`tests/context_scope_intersection_fixtures.py` 通过；`tests/reference_passages_http_fixtures.py` 36 个测试通过；`tests/real_corpus_fixtures.py` 通过；`node --check` 通过。
- 浏览器：`tests/reference_passages_browser_regressions.cjs` 16/16 通过（含 `min_term_coverage` 0.05 步进保存、0.5 回显、1.5 提示）；26 个 CJS 全量 25/26，唯一失败是 `desktop_smoke.cjs` 的 `clipboard-write-from-user-gesture` 在无 GUI 会话中无法取得原生窗口焦点（`TIMEOUT_NATIVE_WINDOW_FOCUS`），其余 25 个 rc=0；不是页面断言失败，但真实桌面焦点仍需在有窗口的会话复验。

### 边界（别读成“语义召回已解决”）

- `term_coverage` 是词面覆盖率，不是语义相似度；同义改写、指代、跨章节因果仍不覆盖。
- 只有一条 absent 句/书，没有人工标注的“应返回空”查询集；0.1/0.25 是实验值，不是产品定值。
- 24 字逐字针的 R@1=1.0 只是词面下限；长指令探针只有 3 条，0.25 的普遍安全性未证。
- 一致性“30 万字 × 3 部”已在 2026-10-25 补跑规则抽取链路：360 回 / 2,344,024 字，注入 100 条检出 100 条、基线 0 误报（见 `docs/real-corpus-acceptance-2026-10-24.md` §9）。但抽取器不是 AI，真实模型链路仍未验证；Q1 的 3000 字 77.8%（7/9）<80% 仍未达成。
- **3000 字目标复验（未改口径）**：`q1_target_met=false`，3000 子集 7/9；失败场景不是调参能修——sc-18 的 FakeProvider 每轮固定输出 46 字、12 轮上限仍只有约 360 字（<2700），sc-19 在第 3 轮前被外部预算耗尽，二者都不能在不改评分/样本/外部中断语义的前提下通过。详见 `docs/consistency-budget-v1.2-2026-10-23.md` §6 与 `benchmarks/budgeted/run.py:244-245`。
- 浏览器自动化已跑：参考片段 16/16；26 个 CJS 中 25 个通过，`desktop_smoke.cjs` 只差原生窗口焦点一项（无 GUI 会话限制），其余页面/导出/桌面用例通过。该失败不能算产品通过，也不能算代码断言失败。
- 细节与复算命令见 [`docs/retrieval-relevance-threshold-2026-10-25.md`](retrieval-relevance-threshold-2026-10-25.md)。

## 2026-10-24 · 收口：同值去重 + 离线抽取器（仍未达成项照旧）

> 历史口径：本节记录 2026-10-24 当时状态；30 万字 × 3 部已在 2026-10-25 补跑规则抽取链路 100/100，见顶部与 `docs/real-corpus-acceptance-2026-10-24.md` §9；AI 抽取链路仍未验证。

本轮交付两件事：同槽位同值事实不再两两配对、真实稿件的离线抽取器与证据闸门。**两项都不改变 Q1 的未达成记录**——预算续写的 3000 字章一次通过率仍是 77.8%（7/9），未达 ≥80%；「30 万字 × 3 部真实作品」一致性验收仍未执行（见「2026-10-23 · Q1 阶段交付（含未达成项）」）。

### 去重：同值副本不再配对，异值最坏仍是 C(n,2)

- **改了什么**：`_detect_fact()` 先用 `_canonical(取值)` 把同槽位事实分桶，只在跨桶之间枚举配对，canonical 文本相同的副本不再进入 `_classify_object_conflict()`。
- **配对次数实测**（来源：`docs/verification-dedup-final-2026-10-24.md` §三，探针在执行期替换 `_classify_object_conflict()` 直接计数，非静态估算）：同值 10 / 20 / 40 条 → **0 次配对**、0 findings、100.0/A（旧实现 C(n,2) 分别为 45 / 190 / 780）；互不相同 10 / 20 / 40 条 → 45 / 190 / 780 次、全部 high，n 翻倍 ×4.22 / ×4.11，**最坏仍是 O(n²)，不能宣称所有输入近似线性**；两桶 20/20 → 400、三桶 20/15/5 → 475，等于跨桶大小之积（20×15 + 20×5 + 15×5）。
- **判定语义没变**（同报告 §四）：同槽位两个不同取值仍 1 条 high/92.0/B；`located_in` 冲突、名字漂移仍各 1 条 high；`1` 与 `1.0` 因 `_canonical`（文本键）与 `_same_value()`（语义等价）层次不同，落在不同桶、多比较 1 次但 0 findings（不误报）；dict 键序不同同桶；`True` 与 `1` 仍判 high。
- **边界**：`_canonical()` 的输出是分桶键与展示文本，不是「是否矛盾」的依据（t56 只在 docstring 写清这件事，可执行逻辑零变更）；重复密集场景 7.31 s → 0.186 s 是该输入形态的实测结果，不可外推到取值互不相同的库。

### 抽取器：离线规则模式 + 只保证「可逐字定位」的证据闸门

- **交付**：`backend/moyu_extract.py`（抽取与校验）、`benchmarks/real_corpus/extract.py`（CLI）、`tests/extract_fixtures.py`、说明文档 `docs/real-corpus-extract-2026-10-24.md`；接口是 `python -m benchmarks.real_corpus.extract --mode rule|openai`，输出严格 JSON 与 `sha256:` digest。
- **证据闸门**（`verify_evidence()`）：`char_start >= 0`、`char_end > char_start`、`char_end - char_start == len(evidence_quote)`、`text[char_start:char_end] == evidence_quote`，多一个字、少一个字、整体偏移一个字都拒（`bad_range` / `quote_mismatch`）；字符下标是 Python code point，与生产端口径一致。
- **严格 JSON 与非法数值**：`validate_assertion()` 用 `math.isfinite()` 拦 `NaN` / `Infinity` / `-Infinity`（原因码 `non_finite`，Python 直传与 JSON 字面量两条路径都拦），`strict_json()`（`digest()` 与去重键同走）用 `allow_nan=False`，不再产出「Python 读得回、浏览器 `JSON.parse` 读不了」的字面量。
- **确定性**：`--limit 20` 两次 digest 均为 `241b76bb…`、`identical=True`（20 章 / 124,534 字 / 10 条断言 / 0 rejected）；`--limit 400` 全语料 360 章 / 2,353,378 字 / 360 条断言，digest `d4b2d0b5…`（来源：`docs/real-corpus-extract-2026-10-24.md` §5 / §6）。
- **真实模型传输**：默认走 curl 子进程（请求头经 `--config -` 从 stdin 进、请求体走临时文件、`--max-time` 为超时 + 15 秒余量）；`curl_available()` 为假才回落 urllib；超时 `DEFAULT_TIMEOUT = 180.0`，可用 `MOYU_EXTRACT_TIMEOUT` 覆盖；非 2xx 一律 `provider_error`（实测 403 与 error code 1010 可见），全程不重试，密钥只进 stdin。
- **落盘脱敏**：要写进报告/异常文本的内容走 `_mask_credentials()`，按**形状**识别而不是按已知 key——Authorization 整段、Digest 参数值、裸 scheme 令牌（`Bearer`/`Token`/`Digest`）、`api_key=`/`token:` 一类键值对、`sk-`/`ghp_`/`AKIA`/`ASIA` 等前缀、≥32 位高熵串，一律换成 `***`；HTTP 状态码、`error code 1010`、`bad_gateway` 这类短标识保留，别把诊断信息一起抹了。
- **回归**：`tests/extract_fixtures.py` 67 个用例（`grep -c '^def test_'` 实测；`tests/*_fixtures.py` 共 94 个文件），含 urllib 403 → curl 200 优先级、curl 失败与超时、失败只发生 1 次（不重试）、密钥不泄露、非有限数值拒绝。

### 未达成与已知局限（别读成「做完了」）

- **真实模型外部复验未完成**：本机无 `MOYU_EXTRACT_*` 凭据，`--mode openai` 缺变量时 `exit 2`（那是「没跑」不是「通过」）；captain 另用已授权服务跑了当前生产 CLI 2 章，**进程 `rc=0` 但两章业务结果均为 `provider_error`**，失败发生在网络层、请求没有进入模型：captain 那次复跑当时，无凭据直连、代理、HTTP/1.1 与 IPv4 四条路径均报 `Could not resolve host`（**当时的观察，不代表此后仍不可达**），即那一轮 DNS / endpoint 未建立真实请求，需要可解析且可达的 base URL 才能复跑；同一轮诊断里另观察到 `192.168.8.50` TCP 连接被拒、`CONNECT 200` 之后的 `SSL_ERROR_SYSCALL`、DoH 返回 `Status=3`（NXDOMAIN），同为**先前观察记录**。**本次未进入模型质量判定，不能据此评价模型**。两种情形都读作**未复验**，不得写成「真实模型已通过」。已覆盖的只有协议、解析、降级、传输与确定性，**真实召回率与语义幻觉率未验收**；默认可用的规则模式是 dry-run 级（只覆盖六类开场套式，心理、因果、关系类事实抽不到）。
- **证据闸门不验语义**：`subject` 指的是不是引文里那个人、`value` / `attribute` 与引文语义是否相符，闸门一律放行（不做实体链接、指代消解、别名合并）；`tests/extract_fixtures.py::test_quote_gate_does_not_verify_semantics` 用「引文对、主语错」的样本把这个缺口钉成可执行断言。语义正确性靠服务端断言复用比较与 `backend/moyu_consistency.py` 的一致性检查兜底。
- **残余边界（均 low，不阻塞）**：已关闭——t63 复审的 F1（curl 失败详情先截断再脱敏）由 t64 关闭；t74 复审的「全角分隔符 / 裸 `Basic` 未被识别」与「为脱敏把诊断信息一并吞掉」由 t75/t76 关闭（改为按形状识别 + 保留状态码与错误码）。仍按边界记录——t76 两条 low：中文 token 值会被过度抹除（属诊断信息损失，不是漏抹）、URL / 百分号编码里的凭据形态未覆盖；t60 两条 low：`tests/extract_fixtures.py` 里 `digest(x) == digest(x)` 是自比、对确定性零约束，`strict_json()` 的 `default=str` 会把不可序列化对象静默字符串化（当前 schema 不可达）；Windows 与外部网关行为未复验。
- **仍未达成的 Q1 项**：预算续写 3000 字子集 77.8%（7/9），未达 ≥80%（差距原因见 `docs/consistency-budget-v1.2-2026-10-23.md` §6 的指标口径缺陷）；「30 万字 × 3 部真实作品」的规则抽取链路已在 2026-10-25 补跑（100/100），但 AI 抽取链路仍未验证，不能算正式 Q1 通过。

## 2026-10-23 · Q1 阶段交付（含未达成项）

Q1 阶段交付记录；每条的细节见对应专项文档。

**状态**：五项交付已落库，但有两项未达成，别把本节读成「Q1 全做完了」——①预算续写的 3000 字章一次通过率 77.8%（7/9），未达 ≥80%（差距原因见本节第 5 条与[预算续写引擎 v1.2 复测](consistency-budget-v1.2-2026-10-23.md) §6）；②一致性引擎的「30 万字 × 3 部真实作品」验收尚未执行，现有数字来自单部合成库自测（局限见[一致性引擎 v1 · LongFormBench 基线](consistency-baseline-2026-10-23.md)）。

- **一致性引擎 v1**：新增只读检测模块 `backend/moyu_consistency.py`（七类检测器，0–100 分与 A/B/C/D 等级），入口是 `GET /api/consistency`、`python -m backend.moyu_consistency` CLI 与工作台「一致性检查」面板。
- **LongFormBench**：新增 `benchmarks/longform/run.py` 合成基准与基线报告，40 章、105,182 汉字、35 条注入矛盾，召回 100.0%（35/35），干净库 0 条 finding、脏库 0 分 D（来源：`docs/consistency-baseline-2026-10-23.md` 的「汉字数」「注入矛盾数」「召回率」「干净基线」「脏库」字段）。
- **提示词工坊**：`backend/moyu_prompt_templates.py` 随库种入 22 个内置模板，覆盖 `server.AI_TASK_IDS` 的 10 个 AI 任务；内置本体只读（`update_template()` / `delete_template()` 对 `is_builtin` 抛 `PromptTemplateConflict`），作者克隆成自定义模板后可改正文、版本号自增，也可用 `reset_template()` 恢复出厂正文。
- **生成门禁**：`export_finalization_issues()` 末尾追加一致性门禁，`fact` / `knowledge` / `evidence` 三类的 `blocker` 与 `high` 阻断 EPUB / Markdown 导出，作者以不少于 8 个字符的 `X-Moyu-Export-Override` 理由放行。
- **预算续写引擎 v1.2 + 生产接线**：`backend/moyu_continuation.py` 升到 v1.2，新增两项默认开启的生成前干预——`overshoot_guard` 把「本轮最多还能写多少」(`max_characters`) 与超额提示注入下一轮上下文，`stall_strategy` 让每次停滞重试换一种展开方式并给出「本次至少写 N 字」；两项只影响提示，不裁剪正文，超冲仍如实记 `within_tolerance=false`。基准复测：全部 20 场景一次通过率 70.0% → 80.0%（14/20 → 16/20），**3000 字子集 66.7% → 77.8%（6/9 → 7/9），Q1 的 ≥80% 尚未达成**；平均轮数 3.20 → 3.85，`body_invariance` 20/20、`candidate_only` 20/20，引擎 digest `sha256:9a6a72f079fe2b9c49a7619dd09cbec6bd77863c6773b94b6827dfc84e6b98ac`（来源：`benchmarks/budgeted/results/budgeted-v1.2-baseline.json` 的 `summary` 与 `engine` 字段，该文件同时记 `q1_target_met: false`）。`POST /api/ai/queue` 接受可选对象 `budgeted_continuation`（`target_characters` / `max_rounds` / `min_round_gain` / `max_stall_retries` / `hard_max_rounds`，默认值与取值范围以 `moyu_continuation.DEFAULT_*` 与 `*_LIMITS` 为准），仅 `continue` 任务可用；第 1 轮沿用冻结请求，第 2 轮起清 `_frozen_request` 并注入已写与还差的进度键，合并正文只在 `finish_ai_job()` 落一次 chunk，逐轮进度记 `ai_run.round` 事件。额度按 run 预留 1 次而真实调用 N 次，轮数与派发次数写进 `consent_json.budgeted_continuation_report` 供核对；复测报告见[预算续写引擎 v1.2 复测](consistency-budget-v1.2-2026-10-23.md)，其中 §6 说明「≥80%」这个指标缺少模型服从度假设、属口径缺陷；接线说明见[多轮字数预算续写的生产链路](continuation-production.md)。

## 2026-10-24 · 一致性引擎 F1 修复（无槽位 `has_attribute` 假阳，双侧）

- **写入端**：`assertion_predicate()` 新增前置判断——模型显式请求 `has_attribute` 但 `assertion_attribute()` 为空时，不再按白名单空放行，回落成 `has_story_fact` 并把原值作为被拒谓词回传；`ensure_memory_assertion()` 据此记 `memory.assertion_predicate_fallback` 事件，`reason` 写作「谓词缺少 attribute 槽位，已回落到结构化默认值」，与「谓词不在白名单内」分开，避免审计把缺槽位误读成模型乱写。
- **引擎端**：`_classify_object_conflict()` 在「两侧都没有 `attribute` 槽位」时按谓词是否带槽位语义分流——`_has_slot_semantics()` 依据 `_SLOT_SEMANTIC_PREDICATES`（目前只有 `has_attribute` 与 `has_story_fact`）判定，命中则跳过配对（只保留名字漂移判断），兜住库里已有的历史行；`located_in` / `age` / `owns_item` 这类不带槽位语义的单值谓词仍按「两个取值即冲突」判 high，未被这次放宽误伤。
- **判据**：事实冲突的判定依据是「数据形状 + 谓词是否带槽位语义」，不是「谓词名 → 严重度」的硬编码表；新增带槽位谓词必须同时同步 `ASSERTION_PREDICATES` 与 `_SLOT_SEMANTIC_PREDICATES`。
- **量化**：同一角色两条互不矛盾的无槽位事实（`{"name":"林昭","note":"左臂有疤"}` 与 `…"note":"惯用右手"`，均 `predicate=has_attribute`）——修复前 1 条 high（score = 100 − 8 = 92.0，等级被 high 封顶为 B）；修复后 0 条 / 100.0 分 / A 级（来源：`SEVERITY_WEIGHTS` 的 `high: 8.0`、`_GRADE_THRESHOLDS`（90/75/60）与 `analyze()` 的等级封顶规则；修复后数值为本机实测，修复前数值为旧逻辑下的算式值 + `tests/consistency_fixtures.py` 的 t42 回归断言 `score == 92.0` / `grade == "B"`）。
- **回归**：`tests/consistency_fixtures.py` 的 t42 用例同时覆盖两件相反的事——三条无槽位 `has_attribute` 事实不产生 finding，而同一项目里一对 `located_in` 冲突仍恰好 1 条 high。

## 2026-10-24 · 真实公版长篇端到端基线（234 万字）

补上「真实稿件端到端链路」的可复算证据，同时**暴露出一个检索缺陷**。本节只报告实测数字，不含任何模型调用（本机无 provider 凭据）。

- **语料与规模**：红楼梦、三国演义、水浒传（公版）共 **360 回 / 2,343,664 字**，导入 **0.128 s**，段落块 10,881 个，库 15,814,656 字节。复算命令见[真实公版长篇端到端基线](real-corpus-baseline-2026-10-24.md)。
- **导入保真度 0 失配**：每回从 `document_revision.canonical_text` 回读比 sha256，360/360 一致。这一步专门用来抓「按 UTF-8 字节切中文」这类不报错的截断。
- **检索只测到下限**：以「全库唯一」的 24 字窗口为针，R@1/R@5/R@20 均为 1.0，延迟 p50 68.68–353.35 ms。**这不是语义召回**——针是逐字存在的窗口，BM25 理应命中，同义改写与跨章因果未测。
- **缺陷：检索没有相关性下限**（`backend/moyu_context.py` 的 `assemble_context()` 消费 `results` 时无阈值，`relevance` 只写入证据记录）。原书不存在的句子在三本书上各返回满额 20 条。该现象依赖语料与查询共享字面，用字不相交的小语料返回 0 条；修复需要先有标注集来定阈值，本轮**未改代码**。
- **一致性引擎只测到耗时**：0.002–0.003 s、100 分 A、0 条 finding——因为库里只有正文、没有一条 `assertion`，是**空库基线**，不得引用为真实水平。
- **回归**：新增 `tests/real_corpus_fixtures.py`（三章合成稿，含脂批、导航行、含「批」的正常叙事句与回目笔误），把 `prepare.py` 的归一化规则与 `run.py` 的端到端契约钉进 CI；`tests/consistency_fixtures.py` 与 `tests/consistency_http_fixtures.py` 仍分别 OK（13.0/100.0）与 6 tests OK。
- **仍未达成**：「30 万字 × 3 部真实作品的一致性验收」依旧未完成——本轮只打通了正文侧，缺「AI 抽取真实稿件 → 采纳 → 引擎」这一段（无模型凭据）。原 Q1 未达成记录不变。

## 2026-10-24 · 一致性引擎 fact 判定改为按属性槽位比较

本轮只改 `fact` 一类的判定口径：从「同一实体、同一谓词就两两比较」改成「同一实体、同一谓词、同一属性槽位才比较」。写入端与引擎端各改一半，缺一半都不成立。

- **写入端：谓词不再一律压成 `has_story_fact`。** `ensure_memory_assertion()` 现在按 `assertion_predicate()` 取谓词，白名单是 `ASSERTION_PREDICATES = {"has_story_fact", "has_attribute"}`：记忆带属性槽位时写 `has_attribute`，否则写 `has_story_fact`。`assertion_object()` 保留原有的 `name` / `type` / `note` 三个键（读取端按旧形状取字段），只在有属性时追加 `attribute` 与 `value`；`value` 原样保留结构，不字符串化。白名单外的谓词不会落库为原值，而是回落到上面两种之一，并在 `ensure_memory_assertion()` 里记一条 `memory.assertion_predicate_fallback` 事件（`ASSERTION_PREDICATE_FALLBACK_EVENT`，带 `requested` / `used` / `reason`）。
- **引擎端：分组键加槽位，删除 `note` 比较。** `_detect_fact()` 的分组键是 `(subject_entity_id, predicate, attribute)`；`attribute` 由 `_attribute_slot()` 从 `object.attribute` 取出去空白后的字符串，非字符串或缺失记为 `""`（「未分类」是真实槽位，坏 JSON 也落这里）。`_classify_object_conflict()` 按固定顺序判定：①两侧 `_same_value` 相同 → 跳过（在 `_detect_fact()` 里）；②任一侧不是 JSON 对象 → high `<predicate> 存在两个不同取值`；③`if left_attribute or right_attribute:` 但槽位不同（含「有槽位 vs 未分类」）→ 跳过；④同槽位任一侧有 `value` 且两侧取值不同 → high `属性「<attribute>」存在两个不同取值`；⑤同槽位取值相同或双方都未记录 `value` → 继续；⑥无槽位且谓词不是 `has_story_fact` → high `<predicate> 存在冲突取值`；⑦两侧 `name` 都非空且不同 → high `同一实体被记成两个名字`。判定依据是**数据形状**（`object` 里有没有 `attribute`）而不是谓词名，所以 `has_attribute`、`has_story_fact` 与将来新增的带槽位谓词走同一套；`note` 不再参与任何 fact 判定。
- **量化：N² 假阳被拆掉。** 同一角色的 10 条互不矛盾事实（无槽位）旧行为是组内两两配对 45 条 low、扣 45 分、score 55.0 → D（历史实测；`tests/consistency_fixtures.py` 的 t24 回归注释同样记录 45 条 low / 扣 45 分；扣分与等级按 `SEVERITY_WEIGHTS` 的 `low=1.0` 与 `_GRADE_THRESHOLDS` 的 90/75/60 可对账——**旧判定代码已删除，今天无法用命令复跑**）；新行为是 0 条 finding、score 100.0 → A（`tests/consistency_fixtures.py` 的 t24 回归直接断言 `analyze()`）。同一实体 5 个不同属性槽位：0 条 / 100.0 / A（同文件 t25 回归断言）。同槽位不同取值：恰好 1 条 high / 92.0 / B（同文件断言；92.0 本应 A，B 来自 `analyze()` 末尾的严重度封顶 `elif counts["high"]: grade = "B" if grade == "A" else grade`）。这条假阳不拆，Q1 的「30 万字 × 3 部真实作品」验收会被自己人堵死：真实稿件里同一角色挂十几条互不相干事实是常态，两两配对会把干净稿打成 D 级。
- **边界：这是「一条误报路径」的修复，不是误报清零。** ①白名单只有 2 个谓词，**任何**白名单外谓词（模型自起的关系/位置类谓词也一样）都会回落，并在 `event_log` 里堆 `memory.assertion_predicate_fallback`，真实稿件跑批要盯这张表的增速；②`attribute` 缺失记为 `""`，未分类断言与任何带槽位断言都不比较——这是设计上的漏报面；③任一侧不是 JSON 对象时槽位判定不参与，直接按 `<predicate> 存在两个不同取值` 报 high；④**只修了 `fact` 类的一条误报路径**，`timeline` / `knowledge` / `attribute` / `relation` / `foreshadow` / `evidence` 六类检测器的误报源都还没处理，不能把本节读成「一致性问题已经解决」。

## 2026-10-23 · 一致性引擎 v1

- 新增只读一致性引擎 `backend/moyu_consistency.py`：七类检测覆盖事实矛盾、时间线矛盾、知识越界、属性漂移、关系/称谓矛盾、伏笔断线与来源失效，输出 0–100 分与 A/B/C/D 等级，等级受最严重问题封顶。
- 新增只读接口 `GET /api/consistency`（支持 `?min_severity=`）与命令行 `python -m backend.moyu_consistency`；工作台左侧新增「一致性检查」面板，展示分数、等级、分类统计与可定位的问题清单。
- 导出 EPUB / Markdown 时，事实、知识、来源三类中的阻断与高危问题进入发表门禁；作者可用不少于 8 个字符的覆盖理由放行。
- 新增 LongFormBench 合成基准：`.venv/bin/python benchmarks/longform/run.py --chapters 40 --seed 20261023` 生成 40 章、105,182 汉字、35 条注入矛盾、七类全覆盖的数据集；召回 100%（35/35），干净基线 0 误报，单次全量分析中位 6.72 ms（5 次重复的中位数，见[一致性引擎 v1 · LongFormBench 基线](consistency-baseline-2026-10-23.md)的「分析耗时（中位数 / 5 次）」字段）。
- 许可证统一为 MIT：修正 `package.json` 中与 `LICENSE` 冲突的 `UNLICENSED` / `private: true`，README 增加许可证说明；新增 `CONTRIBUTING.md` 与[一致性引擎说明](consistency-engine.md)。
- **局限**：上述基准使用合成数据，生成器与引擎同源，注入是规则级矛盾、不测正文抽取器；v1 只把事实、知识、来源三类中的阻断与高危纳入发表门禁，其余类别因误报率未达标不进门禁。本记录不代表文学质量达标，也不代表一致性问题已经解决。

## 2026-10-22 · 维护

- GitHub Actions 产品检查作业超时从 35 分钟调整为 45 分钟：此前一次运行在 35 分钟上限被取消（第 18 步在 31 分钟才开始，重跑 32 分钟通过），属于执行环境变慢，不是测试失败。

## 2026-10-21 · v0.1.3 写作总管任务恢复

- 汇总 `v0.1.2` 之后的模型清单恢复、聊天回复兼容、灵感确认保存、任务步骤和成果预览，以及历史规划保护，详见[本版说明](releases/v0.1.3.md)。
- 请求没有送达本机服务时，作者可核对原任务并手动重试原步骤；刷新、轮询和切换聊天不会自动再次发送。
- 重试前核对作品、分支、任务、检查点和规划候选。来源过期、候选变化、历史只读或离开原聊天时停止；并发恢复只提交一次。
- 修复缺失的历史规划被重复读取或切页后误切到最新规划的问题，保留原选择并要求作者明确操作。
- 本轮工程验证使用隔离作品和本机模拟服务；不代表文学质量达到第一梯队。详细范围见[任务中断恢复验收](audits/agent-task-recovery-20261021.md)。
- 0.1.3 已[正式发布](https://github.com/z19910928-afk/neural-network-ai-creation/releases/tag/v0.1.3)，三平台原生构建与验收全部通过：Mac Apple 芯片、Mac Intel 的包内后台 19/19、真实窗口 10/10、安装后运行 10/10；Windows 为后台 18 通过（1 项 POSIX 专属检查跳过）、窗口 10/10、安装后运行 10/10。本机 Mac Apple 芯片包的构建命令与下载摘要见[本机安装包验收](audits/desktop-v013-local-20261021.md)。

## 2026-10-21 · 任务中断后的手动恢复

- 修复启动、规划确认或完整采用请求未送达时，任务一直停在“操作结果待核对”的问题；新增“核对并重试原步骤”，仅作者手动点击才恢复，刷新、轮询和切聊天不会自动重发。
- 恢复前重新核对作品、分支、任务、原检查点与规划候选；候选变化、来源过期、历史只读或切换聊天时停止恢复。并发同检查点请求只派发一次，完整采用不新增模型调用。
- 修复显式缺失的规划记录在重复读取、切页返回后仍可能退回最新规划的问题；保留原选中，只有作者明确选择才打开当前最新规划。
- 本机使用隔离作品和免费模拟模型验证；旧版本缺少原候选校验的未知记录仍只供核对与回看。源码和桌面开发版验证不代表已更新安装包或文学质量达标，详见[任务中断恢复验收](audits/agent-task-recovery-20261021.md)。

## 2026-10-21 · 写作总管任务卡与历史规划保护

- 写作总管任务卡显示后端真实步骤和正文采纳节点，提供大纲、场景与正文候选的安全折叠预览；聊天重绘和“阶段候选→完整规划”切换后保留作者展开状态与预览滚动位置，卡内衔接启动、阶段确认、完整采用、正文采纳、章后资料确认和精确审稿定位。
- 修复历史只读或缺失规划记录时可能打开另一份规划的问题：聊天结果入口与写作工作台入口都停止跳转并在卡片内说明原因，规划历史不再静默回退到最新规划；不伪造、不补猜原规划内容。
- 本机工程复核：19/19、55/55、23/23、31/31，写作 Agent 24 项通过、1 项按条件跳过；常驻聊天浏览器回归 30/30、写作流程浏览器回归 10/10、写作工作台浏览器回归 19/19，页面错误 0，付费调用 0。使用隔离临时数据库与免费本机模拟模型，不评价文学质量或“第一梯队”。
- 补充回归覆盖规划确认/采用回应丢失后不重复写入、操作前候选变化时放弃确认、场景候选显示所属卷章标题、预览展开/滚动状态跨阶段保持；GitHub Actions 作业超时从 25 分钟调整为 35 分钟（此前一次运行在 25 分钟上限被取消，不是测试失败）。
- 已发布的 Mac、Windows 安装包未包含本轮更新；验收范围与本机证据见[写作总管任务步骤与成果预览复核](audits/agent-chat-task-cards-20261021.md)。

## 2026-10-18 · 项目首页目录整理

- 将页面文件归入 `frontend/`，本机服务与写作模块归入 `backend/`，桌面打包配置归入 `desktop/`，GitHub仓库根目录从58项减少到15项。
- 保留根目录 `server.py` 启动入口、原有页面地址和本机数据位置，同步测试、构建与开发文档中的文件路径。
- 桌面服务入口使用 `desktop/service_entry.py`，避免打包时与 `backend/` 包重名。
- 此次整理不改变已发布的 v0.1.2 安装包；验证范围与结果见[目录整理验收记录](audits/repository-layout-20261018.md)。

## 2026-10-14 · 中断片段回看与冻结字数契约

- 修复失败、取消和中断任务刷新后无法回看已保存片段的问题，任务中心提供只读入口；缺少最终结果时只回读已有分片账本。
- 只读片段不能采纳、整理或再次停止；执行或费用未知不显示重试入口，保留作品、分支和章节身份检查。
- 模型设置与助手状态读完后才显示新表单，等待期间切页或切作品会重新核对身份，防止新模型表单与旧助手状态混用。
- 文学评测两臂统一使用冻结的输出字数契约，私有契约字段不进入故事素材，原上下文和历史请求保持不变；95项离线测试通过。
- 上海时间21:30:46至21:34:21的[完整复验](audits/2026-10-13/evidence/compatibility-python311-node24-interrupted-fragment-contract-final.json)57项和79个电脑浏览器场景通过，70份源码前后相同，20份结果及截图归档；本轮中间失败原件保留。验证使用隔离数据与免费本机模拟模型。
- [v25真实单候选诊断](audits/2026-10-14/literary-pilot-v25-single-candidate/diagnosis.md)完整返回898字，超600至850字硬门，新增1次请求和1592 tokens，没有基线、评审或文学通过结论。累计56/60次共享预留、105621个已知tokens、8条缺完整用量，金额未知；不擅自扩大预算。

## 2026-10-14 · 原候选整理与失败诊断

- 未采纳的续写或润色原候选可确认整理，保留父候选全文并产生独立子候选；沿用冻结模型、参数、输出预算、字数和原稿范围，父子用量分别记录。
- 每个父候选只建立一个子候选，重复请求返回已有任务；只有作者明确比较并采纳才写正文，过期原稿、已采纳或手改父候选拒绝整理。
- 提交失联先只读核对已有任务，执行或费用未知不重发、不自动采纳。备份恢复与作品副本保留历史候选及关联，只供阅读，不能继续采纳、整理、重试或自动收费。
- 公开整理关联只提供 `operation`、`parent_run_id`、`root_run_id`、`parent_characters`，不公开候选全文、整理备注或 `request_id`。
- [真实整理诊断](audits/2026-10-13/literary-revision-v1/diagnosis.md)基线与墨屿均只返回“铁”1字，整理输出合格0/2；两次均为 `glm-5.1`、`stop`、完整协议，未修复第十二轮942/868字失败首稿，未新增独立故事或发送盲评。新增4740和4747、合计9487 tokens，当前累计33条请求预留、59129个可信完整tokens与已知下界，仍有10条缺完整用量，实际金额未知。
- 上海时间02:03:08至02:06:19的[候选整理完整复验](audits/2026-10-13/evidence/compatibility-python311-node24-candidate-revision-final.json)51个执行项、36份Python测试和55个电脑浏览器场景通过；66份源码前后指纹相同，13份附件指纹核验。候选整理19项、正文HTTP15项、20轮连续写作和五章10次专用候选检查均实际执行，提供方为免费本机模拟。
- 文学质量、事实安全、作者效率与第一梯队均未证明。[中间工程失败](audits/2026-10-13/evidence/compatibility-python311-node24-candidate-revision-release.json)因源码变化与浏览器启动失败保留原件，随后独立完整复验通过；提交 `89ab69d` 已上传并通过[GitHub Actions 运行38447803077](audits/2026-10-13/evidence/github-actions-candidate-revision-ci-fix-success.json)。工程检查成功不改变真实文学失败结论。
- 回复模式诊断先执行一次同条件不分段请求，仍得到“铁”1字并观察到非空 `reasoning_content`；按停止条件未发送第二次流式请求。累计更新为34条预留、63876个可信完整tokens，10条缺完整用量，费用未知。[诊断记录](audits/2026-10-13/literary-transport-v1/report.md)不代表文学达标或第一梯队。
- 修正 `ai_contract_fixtures.py` 的服务健康等待：远程运行器现在最多等待10秒，并在服务提前退出时保留启动日志。修复后的[工程快照](audits/2026-10-13/evidence/compatibility-python311-node24-candidate-revision-ci-fix.json)51项、36份Python测试和55个电脑浏览器场景通过；这只修复检查启动竞态，不改变文学失败结论。
- 新增“协议完整但正文只有1字”的正文 HTTP 回归，确认候选仍可回看但页面与后端均阻止采纳，原稿和候选历史保持不变；该场景与真实“铁”1字诊断分开记录。

## 2026-10-14 · 电脑端交付复验与评测范围收口

- 撤销与重做只处理本页改稿，保留另一窗口的新文字；历史按作品、分支和章节隔离。输入法组合输入及冲突时拒绝不安全操作，并保留可重试记录。
- 同起点插入与删除、替换在两个方向均保留冲突双方，由作者选择后保存，避免静默丢字。
- 修正每日额度读取；流式回归直接验证首片已经到达后模型才继续结束，不以机器速度判断通过。
- 文学工具只把双方候选合格、两个不同实际评审模型的双顺序均完成的故事计入对应对照。每个对照仍需30个独立故事；计划数量、任务或题材被篡改的记录不能充数。
- 上海时间00:11:10至00:13:55的[电脑端复验](audits/2026-10-13/evidence/compatibility-python311-node24-desktop-release-final.json)50个执行项、35份Python测试和49个真实浏览器场景全部通过；64份源码前后相同，包含连续20轮写作和五章10次专用候选检查。附件使用真实路径，旧报告与截图保留。
- [第十二轮文学诊断](audits/2026-10-13/literary-pilot-v12/report.md)首对942/868字均超600至850字门，输出合格0/6、有效评审0/12，停止扩题和盲评。仅墨屿系统收束一句变化，不能由单对差异宣称文学改善。含独立诊断累计31条请求预留、49642个可信完整tokens，10条缺完整用量，实际金额未知。
- 工程修复提交 `eaca3586ddd2d8210f1ed62b1d1e22cd2c383886` 已上传分支 `codex/full-function-audit`；GitHub增强自动检查运行 `38434003558` 于上海时间00:52:11至00:56:02真实成功，语法、全部Python契约测试、固定评测、编辑器12项和浏览器37/规划5/五章7场景通过，见[远程记录](audits/2026-10-13/evidence/github-actions-desktop-release-success.json)。
- 本机服务和真实编辑页面已启动验证，电脑入口为 `http://127.0.0.1:4173`；作品保存于忽略上传的 `moyu.sqlite3`，连接凭据保存在本机私有文件。本轮限电脑端，尚未合并 `main`；公网部署、七竞品同条件文学对照、第一梯队结论仍未完成。

## 2026-10-13 · 保存保护、长篇资料与产品修复

- 保存采用原稿与版本检查，迟到回应和并行改稿会保留本页文字；冲突可比较、整理后保存。
- AI 候选绑定作品、章节、原稿版本和选中文字，单次采纳有记录；字数不合要求、明显思考标记和不完整回复会阻止直接采纳。
- 增加多作品、卷章场景蓝图、普通正文的章后记忆、带原句的审稿处理、正文导入、参考阅读、完整备份及独立恢复副本。
- 模型设置按作品保存，密钥存于本机私有文件；任务统一排队和预留每日次数，费用未知的旧请求不会自动再次发送。
- 长篇材料按章节时点、人物知情和有效原句选取；改稿使旧摘要、来源与审稿过期。
- 文学试评保存固定材料、请求、完整候选、失败和费用未知记录。已有工程回归与真实模型诊断分别归档；当前仍未证明文学质量达到第一梯队，远程检查与正式部署另行验收。
- 自动检查使用 Python 3.11 / Node 24，加入编辑器合并和真实浏览器回归。运行版本与验证范围见[审查证据](audits/2026-10-13/evidence/README.md)。

## 2026-10-11 · 任务卡、来源证据与独立审查

- 为章节增加目标、视角、场景计划、伏笔动作、回顾、下一章简报和 hook，写作流程从任务卡开始，并在完成前执行可重复的质量审查。
- 将记忆候选接到章节来源 span 与正典 assertion：保存原文引句、范围和哈希，正文修订后旧来源会标记为 stale。
- 增加有界上下文预览和 AI 运行记录，报告估算 token、检索章节、来源 ID、提示/上下文哈希与延迟；不把有限召回描述成百万上下文。
- 记录 NovelForge、MuMuAINovel、arboris-novel、Ai-Novel、91Writing 和 webnovel-writer 的 clean-room 借鉴边界、许可证注意事项、AI 写作失效模式和剩余 P2/P3 缺口，详见 [`docs/third-party-audit.md`](third-party-audit.md)。

## 2026-10-11 · 全书完成与终稿闭环

- 增加持久化的 `book_status`，支持“标记全书完稿”和“恢复写作”。
- 完稿后首页显示终稿检查入口，并阻止误新建章节或继续归档。
- 修正 Windows 保留设备名作为导出文件名时的处理。

## 2026-10-10 · AI 创作助手预览版

- 新增 `/api/ai/status` 和 `/api/ai`，支持 OpenAI 兼容接口以及完全离线的写作卡。
- 新增续写、场景卡、大纲、发散、润色、总结和连续性检查任务。
- 编辑器加入候选式 AI 面板：结果先预览、可编辑，续写/润色只有明确采纳才进入正文。
- 增加首次创作向导、首页下一步行动和“完成本章，写下一章”推进动作，打通从一句话故事设定到连续章节的基本闭环。
- AI 上下文只发送当前章节工作集、相邻章节片段、设定卡和已确认记忆；未确认候选不会进入“确认记忆”集合。
- 远程 provider 仅接受 HTTPS（本机模型可用回环 HTTP），API key 只从环境变量读取并阻断重定向转发。
- 增加 MIT 许可证、安全边界说明和 `.gitignore`。

这仍是单人本地预览版，不包含公网认证、向量检索、百万 token 上下文、多人协作或自动改正典。
