# Q1 收尾冲刺 · 独立验收报告（2026-10-23）

## 结论

**needs_revision** —— 四项交付的测试、基准与文档大体自洽，但**超冲保护在生产链路实际未生效**（引擎算出的 `max_characters` 提示被接线层丢弃、首轮完全没有额度约束），且**被点名的 sc-15 / sc-18 两个真实失败在计入口径里仍然失败**、3000 字子集 77.8% 未达 ≥80%，按验收规则不能放行。

复现与证据全部来自本报告作者独立实跑：12 条必跑命令 + 2 个自造探针（`/tmp/probe_budgeted_t5.py`、`/tmp/probe_workshop_badge.cjs`，均在仓库外，不改被测代码）。唯一写入的仓库文件是本报告。

---

## 一、验收范围与环境

- 仓库：`/Users/zhaozhen/Downloads/神经网络写作`，分支工作区含本轮未提交改动。
- 解释器：`.venv/bin/python`（CPython 3.11.17）。
- 浏览器回归：Electron 当 Node，`MOYU_PLAYWRIGHT_MODULE=$PWD/node_modules/playwright`、`MOYU_TEST_PYTHON=$PWD/.venv/bin/python`。
- 验收对象：`backend/server.py`、`backend/moyu_continuation.py`、`benchmarks/budgeted/run.py`、`frontend/prompt-workshop.js`（+CSS）、`docs/continuation-production.md`、`docs/consistency-engine.md`、`docs/CHANGELOG.md`、`docs/strategy-beat-novelforge-2026-10-23.md`、`tests/budgeted_continuation_http_fixtures.py`。

## 二、必跑命令（本机实测，非转抄）

| # | 命令 | 退出码 | 关键输出（实测摘录） |
|---|------|--------|----------------------|
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py backend/moyu_consistency.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 43}` |
| 3 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 9 tests in 5.61s` / `OK` |
| 4 | `.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` | 0 | `verdict PASS`；v1.2 digest `sha256:9a6a72f079fe2b9c49a7619dd09cbec6bd77863c6773b94b6827dfc84e6b98ac`；v1.1 `sha256:847d620f…c99e8`；v1.0 `sha256:f3afbcf9…94ca8d`；`real 0.13s` |
| 5 | `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 6 | `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests` / `OK` |
| 7 | `.venv/bin/python tests/prompt_workshop_fixtures.py` | 0 | `Ran 12 tests` / `OK` |
| 8 | `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests` / `OK` |
| 9 | `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests` / `OK` |
| 10 | `.venv/bin/python tests/provider_queue_integration_fixtures.py` | 0 | `Ran 55 tests in 21.99s` / `OK` |
| 11 | `MOYU_PLAYWRIGHT_MODULE=… MOYU_TEST_PYTHON=… ELECTRON_RUN_AS_NODE=1 …/Electron tests/prompt_workshop_browser_regressions.cjs` | 0 | `desktop_scenario_count: 18`，18 用例全 `passed`；套件内部 `assert.deepEqual(pageErrors/consoleErrors/faviconRequests, [])` 通过 |
| 12 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" >/dev/null 2>&1 || echo "FAIL $f"; done` | 0 | 无 `FAIL` 行；glob 命中 **91** 个 fixture 文件（t1 报告写 90，与我实测差 1，属统计口径，不影响结论） |

连跑两次 `--compare` 后，`benchmarks/budgeted/results/budgeted-v1.2-baseline.json` 两次 md5 均为 `359482cc4bed2098979dc74ac620dd0b`、`budgeted-comparison.json` 均为 `0ac817f68f06083e48ae0d7247ef1888`，产物 mtime 在运行后更新 —— 说明 `--compare` 真的重跑了三臂，而不是只校验旧文件。可复现成立。

### 自造探针（补充证据）

| 探针 | 结果 | 覆盖 |
|------|------|------|
| `/tmp/probe_budgeted_t5.py`（假 SSE provider 记录每轮 messages/request_id） | **48 checks，FAIL 1**（见 P1） | 默认单次、多轮提示差异、chunk seq、usage 累加、取消、额度边界、11 组非法参数、上游 500、no_progress、run 详情、**引擎提示是否真的到达模型** |
| `/tmp/probe_workshop_badge.cjs`（Playwright，真实服务 + 临时 SQLite） | **17 checks，全 PASS** | 两条自定义的徽章归属、保存后转移、同秒 rowid tie-break、删除回落、孤儿动作 `none`、创建后工具栏按钮复活、无 page/console 错误 |

两处探针自身踩坑已修正后重跑：① 取消用例原先 gate 第 1 轮导致「已写轮次为 0」，改 gate 第 2 轮；② 徽章探针原先点 `.first()` 的 `[data-prompt-refresh]` 落在隐藏视图，改为 `page.reload()` 后重新进 prompts 视图。**修正的是探针，不是被测代码。**

---

## 三、12 条重点挑刺逐条结论

### 1. 默认行为是否被改坏 —— PASS

不传 `budgeted_continuation` 的 `continue` 请求：假 provider 记录 `calls=1`；结果对象无 `budgeted_continuation` 键；建单返回也无预算字段。分派处 `_process_ai_job_claimed` 仅在 `budget is not None and row["task"] in BUDGETED_CONTINUATION_TASKS` 时走新路径，否则原 `generate_ai` 一字未改。

### 2. 多轮 prompt 是否真的不同 —— PASS

target 1500、provider 每轮 300 字：`calls=5`，5 条 user message **两两不同**（`unique=5/5`）；第 1 轮无 `continuation_round` 材料；第 2 轮包含第 1 轮正文原文与「不要重复已经写过的内容」。不是「同一提示词跑 N 遍」。

### 3. chunk 是否撞号 —— PASS

多轮结束后 `app_ai_chunk.seq=[1,2]`（连续、无重复、无空洞）；拼接逐字等于结果正文（长度 1508）；`ai_run.output_hash == server.hash_text(正文)`。多轮只在 `finish_ai_job` 落一次 chunk，未逐轮复用 seq。

### 4. 额度是否说实话 —— PASS

5 次真实调用：`token_json.usage.total_tokens = 60 = 12×5`（逐轮 12），`usage_state=reported`；`consent.budgeted_continuation_report.dispatched_calls=5`；`GET /api/ai/quota` 200；`GET /api/ai/runs/<id>` 的 consent 里同样带 `dispatched_calls`。没有把 N 报成 1。

### 5. 取消是否真能中断 —— PASS

gate 第 2 轮后取消：provider 调用停在 2 次，状态 `canceled`，第 1 轮 300 字正文保留在结果里，`safe_to_apply=false`，后续轮不再派发。

### 6. 基准数字是否可复现 —— PASS

两次运行 digest 逐字节一致（见命令 4 的 md5 对比）；`budgeted-v1.2-baseline.json` 的 `acceptance` 实测：`body=true`「20/20 哈希一致」、`candidate=true`「20 个场景 candidate_only」、`determinism=true`。数字与产物对账见第 11 条。

### 7. v1.0 语义是否仍可锁定 —— PASS

`benchmarks/budgeted/run.py` 的 `ENGINE_VARIANTS["v1"]` 显式三开关全关（`closing_hint: False, overshoot_guard: False, stall_strategy: False`），v1.1 只开 `closing_hint`，v1.2 全开；`VARIANT_ORDER=v1,v1.1,v1.2`。v1.0 四产物未被覆盖（`baseline_untouched=true`），v1.0 digest 仍为 `f3afbcf9…`。

### 8. 超冲处理是否作弊 —— PASS（引擎侧）/ 生产侧见 P1、P2

基准 `body=true`「20/20 哈希一致」；我的探针里 provider 第 1 轮硬吐 5000 字（target 1000）时，结果正文**原样 5000 字、一字未裁**，只如实标 `within_tolerance=false`、`target_range={min:900,max:1100}`。引擎没有靠删字把 sc-15 做进区间。**但**生产链路把超冲提示丢了（P1）、首轮无约束（P2）。

### 9. 前端徽章是否正确 —— PASS

独立探针 17/17：
- A/B 同动作两条自定义：只有 B（后建、rowid 大）带 `data-prompt-effective-badge`，A 行带 `data-prompt-inactive`；
- PUT 更新 A 后：徽章**转移到 A**，B 变 inactive；
- 同秒创建 C/D（`updated_at` 相同）：按 rowid 大者 D 生效，与后端渲染表 `ORDER BY is_builtin DESC,updated_at,rowid` 的覆盖顺序一致；
- 删除 D 后徽章回落 C；
- 无内置模板的孤儿动作：编辑区 `data-prompt-effective="none"`，列表不带徽章；
- 创建模板后工具栏「↻ 刷新」`disabled=false`（t3 声称修掉的 `mutate()` 按钮禁用 bug 确实修好）。

### 10. 文档是否有裸行号 —— PASS

```
grep -nE '(server|moyu_continuation|moyu_consistency|run)\.py:[0-9]+' docs/continuation-production.md docs/consistency-engine.md
```
无输出（exit 1）。扩查 `\.py:[0-9]+` 覆盖 CHANGELOG 与策略文档，同样无输出。

### 11. 文档数字是否与产物一致 —— PASS

抽查并全量对账 `benchmarks/budgeted/results/*.json`：

| 文档表述 | JSON 实测 | 结论 |
|---|---|---|
| 20 场景 70.0%→80.0%（14/20→16/20） | v1.0 `total=20, first_pass=14, rate=0.7`；v1.2 `first_pass=16, rate=0.8` | 一致 |
| 3000 子集 66.7%→77.8%（6/9→7/9） | v1.0 `by_target.3000: 6/9 rate 0.6667`；v1.2 `7/9 rate 0.7778` | 一致 |
| 平均轮数 3.20→3.85 | 按 scenarios 逐项求均值：3.2 / 3.85 | 一致 |
| digest `sha256:9a6a72f0…b98ac` | `determinism.digest` 完全相同 | 一致 |
| `q1_target_met: false` | v1.2 JSON `q1_target_met=false` | 一致 |
| `body_invariance 20/20`、`candidate_only 20/20` | acceptance `body`=20/20、`candidate`=20 场景 | 一致 |

### 12. 策略文档口径是否诚实 —— PASS

`docs/strategy-beat-novelforge-2026-10-23.md` Q1 表后明确写：**「77.8%（7/9），未达成 ≥80%」**，并说明差距来自对抗式 provider 不读长度提示、sc-15 首轮 4661 字越上界 261 字、sc-18 每轮 12–30 字；`docs/CHANGELOG.md` 状态行同样写「两项未达成」，并把 20 场景 80.0% 只作参照。没有拿上界或附录数字冒充 headline。

---

## 四、问题清单

### P1（high）生产链路丢弃引擎的超冲额度提示，`overshoot_guard` 对真实用户完全无效

- **文件与符号**：`backend/server.py` 的 `continuation_round_material()`；对照 `backend/moyu_continuation.py` 的 `_build_next_context()`（写入 `max_characters` / `overshoot_hint` / `min_characters` / `deficit_characters` / `pace_hint`）。
- **问题**：引擎第 2 轮起把这些键放进 `prompt_context`，但 `continuation_round_material()` 只转抄 `continuation_round`、`previous_text`、`continuation_requirement`、`closing_hint`、`stall_retry_hint`，**`max_characters` / `overshoot_hint` 被静默丢弃**。生产环境里模型永远看不到「本轮最多再写 N 字」，v1.2 的超冲保护只存在于基准的直连引擎路径。
- **证据**：探针 `/tmp/probe_budgeted_t5.py` 用例 2f：target 1500、每轮 300 字时，第 2 轮 user message 只有 `continuation_round` + `previous_text` + `continuation_requirement`，**不含「最多再写」**（`FAIL 2f`）；同批 2g「还差约」通过，说明材料通道本身是通的，缺的是超冲键。
- **复现**：假 provider 记录 messages → `POST /api/ai/queue`（`task=continue`、`budgeted_continuation.target_characters=1500`）→ 检查第 2 轮 user message 是否含 `max_characters` / 「最多再写」→ 不含。
- **要求**：把 `max_characters`、`overshoot_hint`（以及 `min_characters` / `deficit_characters` / `pace_hint`）纳入 `continuation_round_material()` 的白名单，并加 fixture 断言第 2 轮提示词里能看到这些键；否则 `docs/continuation-production.md` 必须明写「生产只透传 closing/stall 两类提示，超冲保护未接入」。

### P2（high）首轮没有任何额度约束，`round_budget()` 生产零调用

- **文件与符号**：`backend/moyu_continuation.py` 的 `round_budget()`；`backend/server.py` 的 `_run_budgeted_continuation_job()` 第 1 轮 `generate()`（沿用 `_frozen_request`，`prompt_context` 为空字典）。
- **问题**：`round_budget()` 是 v1.2 专门给调用方约束**首轮**的入口，但全仓只有 `tests/budgeted_continuation_fixtures.py` 与 `benchmarks/budgeted/run.py` 附录调用它，**`backend/server.py` 从不调用**。生产多轮续写的第 1 轮没有任何上界提示，sc-15 的根因（首轮一口气写 4661 字）在生产同样存在。
- **证据**：探针 pass 5（超冲场景）：target 1000、provider 第 1 轮 5000 字 → 结果正文 5000 字原样返回，`within_tolerance=false`，`target_range={min:900,max:1100}`，`overshoot_guard` 报 `characters_over_upper=4100`，但提示从未进过请求。
- **复现**：同 P1 的假 provider，首轮返回 5000 字，target 1000 → 检查 `result.text` 长度与首轮 payload 是否含额度提示（不含）。
- **要求**：要么在首轮把 `round_budget()` 的额度折进可审计的普通键（注意不能破坏 `_frozen_request` 的逐字全等校验，需走非冻结分支或把额度并入 instruction 材料），要么在 `docs/continuation-production.md` 的已知边界里明确写「首轮无超冲约束」，并下调 t2 对 sc-15 的修复声明。

### P3（high）被点名的两个真实失败在计入口径里仍然失败，3000 字子集未达 ≥80%

- **文件与产物**：`benchmarks/budgeted/results/budgeted-v1.2-baseline.json` 的 `scenarios`（sc-15 / sc-18）与 `summary.by_target["3000"]`。
- **问题**：团队目标写的是「修基准里 sc-15 超冲与 sc-18 停滞两个真实失败」「把 3000 字通过率拉到 ≥80%」，但 v1.2 计入口径（对抗式 provider）实测：
  - `sc-15`：`characters=4661`，`within_tolerance=false`，`first_pass=false`，`stop_reason=target_reached`（目标 4000，上界 4400，超 261）；
  - `sc-18`：`characters=46`，`stop_reason=no_progress`，`first_pass=false`（v1.0 为 30 字/1 轮，v1.2 为 46 字/2 轮 —— 引擎确实换了策略，但结果仍不达标）；
  - 14→16 的增益实际来自 **sc-16 / sc-17**（v1.0 `max_rounds` 失败 → v1.2 `target_reached`），不是 sc-15 / sc-18；
  - 3000 子集 `7/9=77.8% < 80%`，`q1_target_met=false`。
- **说明**：t2 的报告如实披露了这一点（没有谎报），但**目标本身未达成**，不能按「已修」收口。
- **复现**：`.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` 后读 JSON，或用 `.venv/bin/python -c` 打印 sc-15/sc-18 行。
- **要求**：由 captain 二选一——(a) 承认 Q1 该验收未达成并在 CHANGELOG/策略文档把「修 sc-15/sc-18」改成「引擎侧干预已实现、计入口径仍未通过」；(b) 修改基准的模型服从度假设（另立验收），但不得把附录上界数字计入 headline。

### P4（medium）生产接线文档夸大了超冲保护的实际生效范围

- **文件与符号**：`docs/continuation-production.md` §7「已知边界」第 2 条；同文件 §3 的「可选 `closing_hint` 与 `stall_retry_hint`」清单。
- **问题**：边界第 2 条写「`overshoot_guard`（默认开）只能在生成前把『本轮最多还能写多少』(`max_characters`) 和提示注入上下文」，读者会认为生产请求里模型能看到该额度；实际 `continuation_round_material()` 并不透传它（P1），首轮更是完全没有（P2）。§3 的清单虽只列了两个键，但没有说明「其余引擎键被丢弃」这个后果。
- **要求**：在 §3/§7.2 明确写「生产只透传 `closing_hint` 与 `stall_retry_hint`；`max_characters` / `overshoot_hint` / `min_characters` 目前在接线层被丢弃，超冲保护尚未在生产生效」，直到 P1 修完。

### P5（low）t3 声称修掉的按钮禁用 bug 没有任何回归断言保护

- **文件**：`tests/prompt_workshop_browser_regressions.cjs`。
- **问题**：18 个用例里搜不到 `disabled` / 「刷新」相关断言（`grep -n "disabled\|刷新"` 无输出），但 t3 报告把「mutate() 在 busy=true 后未复位按钮导致刷新永久禁用」列为顺带修掉的真实 bug。修是修了（我的探针实测创建后 `disabled=false`），但没有测试锁住，后续重构可能悄悄复发。
- **要求**：加一条「创建/保存/删除后工具栏按钮 `disabled=false` 且再次刷新能取到新数据」的用例。

### P6（low）t2 报告的引擎用例数与实测不符

- **文件**：`tests/budgeted_continuation_fixtures.py`。
- **问题**：t2 报告写「引擎单测 41 cases 仍 OK」，我实测输出 `budgeted continuation fixtures: OK {'cases': 43}`。数字对不上（可能是报告写完后 fixture 又加了两条），不影响功能，但验收材料应可追溯。
- **要求**：报告与 fixture 的 `cases` 计数对齐，或改为引用命令输出而不写死数字。

---

## 五、已知边界（本次验收没覆盖的部分，明说）

1. **没有真实模型调用**：所有生成由假 SSE provider / 离线 provider 驱动，真实模型的服从度、延迟、失败率未验证；P3 的口径争议（对抗式 provider 是否合理）我没有权限替 captain 拍板。
2. **并发未做专项探针**：只验了单 run 的额度边界（`remaining >= round_index`）与「取消在轮边界生效」，没有并发提交多个多轮任务压 `app_quota_reservation`，也没有跨项目串数据测试（本轮改动集中在 AI 任务链，未触及项目过滤，但未实测）。
3. **前端只回归了提示词工坊**：其余视图（作者工作台、候选箱等）未跑浏览器回归；多轮续写仍无 UI 入口这一点由文档自认，我未验证「作者能否只靠界面用上多轮」。
4. **成本/性能未评估**：`estimated_not_provider_bill` 的估算与真实账单偏差、多轮产生的实际 token 成本未核对。
5. **平台单一**：仅 macOS + 本机 Electron/Chromium；Windows/Linux 路径与文件锁行为未验证。
6. **未覆盖 t4 的一致性引擎历史口径**（如策略文档里「30 万字 × 3 部」验收未执行）——那是上一轮遗留，本轮只核对了新增/修改段落的数字与链接。

---

## 六、验收判定

| 维度 | 结果 |
|------|------|
| 必跑命令 | 12/12 全绿（91 个 fixture 无 FAIL） |
| 默认行为不变 | 通过 |
| 多轮提示/ chunk / 额度 / 取消 | 通过 |
| 基准可复现与不改写正文 | 通过 |
| 工坊生效徽章 | 通过（独立 17 项探针） |
| 文档裸行号 / 数字对账 / 诚实性 | 通过 |
| 生产超冲保护 | **未通过（P1、P2）** |
| sc-15 / sc-18 与 ≥80% 目标 | **未达成（P3）** |

**结论：needs_revision。** 建议 captain 先裁决 P3 的验收口径（是承认未达成，还是重立「模型服从」假设），再让 t1/t2 修 P1、P2；P1/P2 修完前，不应对外宣称「续写引擎已带超冲保护接入生产」。

---

## 七、附录 A：生产链路键透传权威清单（m00257 要求，从代码 + 只读探针复核）

**复核时间**：2026-10-23 22:00 之后（本轮改动含 t8 的 `create_ai_job` 首轮额度落地）。
**复核方法**：只读 `backend/server.py`、`backend/moyu_continuation.py`，并用三个仓库外探针取证：

| 探针 | 作用 | 结果 |
|------|------|------|
| `/tmp/probe_passthrough_audit.py` | 假 SSE provider 跑 target=1500/max_rounds=5，逐轮解析真实 user message 里的 `<manuscript>` material JSON | 8 轮，全部抓取成功 |
| `/tmp/probe_passthrough_stall.py` | monkeypatch 假 provider 让第 2 轮只写 10 字，强制停滞重试 + 恢复期 | 8 轮，第 3 轮出现 `stall_retry_hint` |
| `/tmp/probe_engine_keys.py` | 直接驱动 `moyu_continuation.run_budgeted_continuation`，记录每轮 `prompt_context` 的键 | 第 3–8 轮均出现 `min_characters`/`deficit_characters`/`pace_hint` |

**生效分支**（决定键在不在）：
1. 请求不带 `budgeted_continuation`：`create_ai_job` 不注入任何预算键，走原单次 `generate_ai`，本清单全部不适用（这正是默认行为不变的证据）。
2. 带预算 + `task=="continue"`：`create_ai_job` 先注入**首轮额度**（`moyu_continuation.round_budget(0, target_characters)`），第 1 轮走冻结请求；第 2 轮起 `continuation_round_material()` 用引擎 `prompt_context` 覆盖。
3. `retry_ai_job`：继承 `consent["budgeted_continuation"]` 与冻结 payload，**首轮仍是原开场额度**（不按已写字数重算）。

### A.1 键级清单（键名 → 透传/丢弃 → 出现轮次 → 依据）

| 键名 | 生产提示词里的状态 | 出现轮次 | 依据（函数 + 条件） |
|------|------|------|------|
| `remaining_characters`（顶层） | **冲突**：值固定为首轮注入的 `target_characters`，第 2 轮起不再更新；同时材料里另有一个新鲜的 `continuation_round.remaining_characters` | 1–N | `create_ai_job` 写入一次；`continuation_round_material()` 只写嵌套 `continuation_round`，不覆盖顶层键 → 顶层值永远是首轮值（本次实测 8 轮全为 1500） |
| `continuation_round.remaining_characters` | 透传（新鲜，唯一可信的剩余量） | 2–N | `continuation_round_material()` 无条件写 `continuation_round`，值取自引擎 `context["remaining_characters"]` |
| `continuation_round.round_index` / `target_characters` / `written_characters` | 透传 | 2–N | 同上；`written_characters` 取自引擎 `context["characters"]` |
| `max_characters` | **透传（已修）**：首轮 = `floor(1.1×target)`；第 2 轮起逐轮递减 | 1–N | 首轮 `create_ai_job` 的 `round_budget(0, target)`；第 ≥2 轮 `continuation_round_material()` 的 `for key in ("max_characters", "overshoot_hint")` 覆盖 |
| `overshoot_hint` | **透传（已修）**：与 `max_characters` 同步 | 1–N | 同上（字符串直接覆盖） |
| `previous_text` | 透传：累积全文（引擎 `previous_text + 本轮 text`） | 2–N | `continuation_round_material()` 在 `previous_text` 非空时写 |
| `continuation_requirement` | 透传：生产侧生成的中文指令（已写/目标/还差/不要重复/不要重开） | 2–N | 同上，与 `previous_text` 同时写 |
| `closing_hint` | 透传：引擎在 `0 < remaining < CLOSING_HINT_THRESHOLD(600)` 时给 | 余量 < 600 的轮次（本次 7、8 轮） | `continuation_round_material()` 的 `if context.get("closing_hint")` |
| `stall_retry_hint` | 透传：中文换策略提示，文本内含「本次至少写 N 字」 | 仅停滞重试轮（本次第 3 轮） | `continuation_round_material()` 的 `if context.get("stall_retry_hint")` |
| `stall_retry_min_characters` | **丢弃**（引擎给了 190，材料里没有） | 停滞重试轮（引擎第 3 轮） | `continuation_round_material()` 白名单无此键 |
| `stall_retry_attempt` | **丢弃**（引擎给了 1） | 停滞重试轮 | 同上 |
| `min_characters` | **丢弃**（引擎第 3–8 轮逐轮给，恢复期配额） | 恢复期每轮 | 同上；引擎 `_default_next_context` 在 `stall_strategy and pace_min_characters is not None` 时写 |
| `deficit_characters` | **丢弃**（引擎第 3–8 轮逐轮给） | 恢复期每轮 | 同上 |
| `pace_hint` | **丢弃**（引擎给出的「本轮至少写 M 字、剩余 R 轮」中文提示） | 恢复期每轮 | 同上 |
| `characters`（引擎顶层） | 丢弃（材料改用 `continuation_round.written_characters`） | 2–N | 白名单无此键；信息未丢失，属可接受改名 |
| `round_index`（引擎顶层） | 丢弃（材料改用 `continuation_round.round_index`） | 2–N | 同上，属可接受改名 |

### A.2 逐轮实测证据（target=1500，上界 1650）

| 轮次 | 顶层 `max_characters` | 顶层 `remaining_characters` | `continuation_round.remaining_characters` | 其他 |
|------|------|------|------|------|
| 1（冻结） | 1650 | 1500 | 无 | 无 `previous_text`，`continuation_round` 不存在 |
| 2 | 1450 | **1500（陈旧）** | 1300 | `previous_text` + `continuation_requirement` 出现 |
| 3（停滞重试） | 1440 | **1500** | 1290 | `stall_retry_hint`（含「至少写 190 字」）；`stall_retry_min_characters` 缺失 |
| 4–6（恢复期） | 1240 / 1040 / 840 | **1500** | 1090 / 890 / 690 | 引擎侧每轮有 `min_characters`/`deficit_characters`/`pace_hint`，材料里全部缺失 |
| 7–8 | 640 / 440 | **1500** | 490 / 290 | 追加 `closing_hint` |

> 关键矛盾：同一份 material 里，`remaining_characters=1500` 与 `continuation_round.remaining_characters=1300` 并存，`continuation_requirement` 又写「还差约 1300 字」。模型收到两个互相打架的剩余量。

### A.3 对 P1 / P2 的改判（按 m00257 裁决，证据以本附录为准）

| 原问题 | 当前事实 | 判定 |
|------|------|------|
| P2 首轮无额度 | `create_ai_job` 已调用 `round_budget(0, target_characters)` 并注入 `remaining_characters`/`max_characters`/`overshoot_hint`；首轮实测 `max_characters=1650`、`overshoot_hint="本轮最多再写 1650 字…"`，且首轮 messages 与 consent 审计逐字一致 | **已关闭（P2 不再成立）** |
| P1 超冲提示被丢弃 | `max_characters`/`overshoot_hint` 已透传（首轮 + 每轮覆盖）；**残余**是停滞/恢复期三件套：`min_characters`、`deficit_characters`、`pace_hint`，以及 `stall_retry_min_characters`/`stall_retry_attempt` | **缩小为 P7 / P9，原 high 降为 medium**（信息仍有部分经 `stall_retry_hint` 中文文本送达，但恢复期「每轮至少写 M 字」在生产提示词里完全缺失） |

### A.4 给 docs-engineer 的改写依据（可直接引用本节）

1. 可以写「首轮与每轮都会带 `max_characters` / `overshoot_hint`」——已由首轮 1650 与逐轮递减实测证明。
2. **不能**写「停滞恢复期每轮都下发 `min_characters`/`deficit_characters`/`pace_hint`」——引擎有、接线层丢弃；生产只有重试轮的 `stall_retry_hint` 中文提示。
3. 首轮额度的口径必须注明：`round_budget(0, target)` 的 `characters_done` 恒为 0，即「额度按本次续写从零起算」，不抵扣章节已有正文（与引擎文档「target 只算本次」一致）。
4. `remaining_characters` 顶层值是首轮快照、会与 `continuation_round.remaining_characters` 冲突；文档若要引用剩余量，只能引用后者（或等 t9/P8 修完再改）。

---

## 八、附录 B：m00257 裁决后的状态更新（append-only，不改原判定）

原判定 **needs_revision** 保持不变（P3 未达成、P4 未改、P7/P8 为新发现）。以下为对本报告问题清单的增量修订，旧条目保留原文以便追溯。

| 编号 | 原/新 severity | 状态 | 说明 |
|------|------|------|------|
| P1 | high → **medium** | 缩小 | 超冲两键已透传（见附录 A.3）；残余为恢复期三键，转 P7 |
| P2 | high → **已关闭** | 已修 | `create_ai_job` 首轮调用 `round_budget(0, target)`，实测首轮 `max_characters=1650`、首轮 messages 与 consent 审计逐字一致 |
| P3 | high | 保持 | captain 已裁决承认未达成，`q1_target_met=false` 与 77.8% 不变 |
| P4 | medium | 保持（待 docs-engineer 改） | 按 A.4 改写，不得声称恢复期配额已下发 |
| P5 | low | 保持（captain 接受为已知边界） | 按钮 bug 无独立断言，已有行为用例覆盖 |
| P6 | low | 保持（engine-engineer 订正） | 报告 cases 数应为 43 |
| **P7（新）** | **medium** | 待 t9 | `continuation_round_material()` 丢弃 `min_characters` / `deficit_characters` / `pace_hint`，生产提示词里没有恢复期「每轮至少写 M 字」，t2 声称的「恢复期每轮下发配额」对真实作者不成立。复现：`/tmp/probe_passthrough_stall.py`（第 3–8 轮材料无这三键）或 `/tmp/probe_engine_keys.py`（引擎侧有）。文件与符号：`backend/server.py` 的 `continuation_round_material()`。要求：加入白名单并补 fixture 断言；或在文档明写未生效。 |
| **P8（新）** | **medium** | 待 t9 | 顶层 `remaining_characters` 永远停留在首轮值（本次 8 轮全 1500），与 `continuation_round.remaining_characters`（1300→290）同一份 material 内互相矛盾；`continuation_requirement` 又按新鲜值写「还差约 N 字」。复现：`/tmp/probe_passthrough_audit.py`。文件与符号：`backend/server.py` 的 `create_ai_job`（写入首轮快照）+ `continuation_round_material()`（不覆盖顶层键）。要求：每轮用引擎值覆盖顶层 `remaining_characters`，或从材料里删掉该顶层键只留嵌套。 |
| **P9（新）** | **low** | 待 t9 | `stall_retry_min_characters`（190）与 `stall_retry_attempt` 被丢弃；因 `stall_retry_hint` 中文文本已含「本次至少写 190 字」，信息未完全丢失，故记 low。复现：`/tmp/probe_passthrough_stall.py` 第 3 轮。 |

**第二轮复验基准**：t9 交付后，用附录 A.1 表逐键核对「透传」列是否全部为透传（P7/P9 六键）+ P8 顶层键是否更新；其余（P2 已关闭）只做回归不重开。P4 文档改写核对点见 A.4。

---

## 九、附录 C：t9 复测 + P4 文档复核（append-only，不改原判定）

### C.1 t9 复测（当前代码，server.py mtime 22:03:17）

| 检查 | 命令 / 探针 | 结果 |
|------|------|------|
| 语法 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py backend/moyu_consistency.py` | exit 0 |
| 接线用例 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | **Ran 13 tests OK**（7.43s），新增 `test_stall_recovery_quota_reaches_the_prompt` |
| 透传实测（无停滞，target=1500，7 轮） | `/tmp/probe_passthrough_audit.py` | `min_characters` 等仍 ABSENT（无恢复期，符合预期） |
| 透传实测（第 2 轮强制 30 字停滞，8 轮） | `/tmp/probe_passthrough_stall.py` | `min_characters` 第 3–8 轮出现（187→…）、`stall_retry_min_characters` 第 3 轮出现（187）、`minimum_hint` 同步生成；`deficit_characters` / `pace_hint` / `stall_retry_attempt` 仍 ABSENT |

**对附录 B 的收窄**：
- **P7**：`min_characters` 已修复（恢复期每轮透传 + `minimum_hint`）。残余仅 `deficit_characters` / `pace_hint` 两个键——信息不完全丢失（`minimum_hint` 给出「本轮至少写 N 字」），故 P7 由 medium 降为 **low（残余）**。
- **P9**：`stall_retry_min_characters` 已修复；残余仅 `stall_retry_attempt`（且 `stall_retry_hint` 文本已含重试语义），降为 **nit**。
- **P8 仍未修**：顶层 `remaining_characters` 在 8 轮实测中恒为 1500，与嵌套值（1270→…→290）及 `continuation_requirement` 的「还差约 N 字」并存 → 保持 **medium**，需接线层每轮覆盖或删除该顶层键。

### C.2 P4 文档复核（docs/continuation-production.md，180 行）

四条自查核对结果：

| 自查项 | 结论 |
|------|------|
| ① 未声称「恢复期配额已下发」 | 成立：§3 表写「透传 + `minimum_hint`」，并单列坑说明 `deficit_characters`/`pace_hint`/`stall_retry_attempt` 仍未透传（与实测一致） |
| ② 未把顶层 `remaining_characters` 当余量 | 成立：明确写「首轮快照，8 轮恒为 1500，嵌套值 1300→…→290，只能引嵌套值」 |
| ③ 首轮 `round_budget(0, …)` 从零起算、不抵扣已有正文 | 成立：§3 结论与 §7 第 3 条都写明 |
| ④ 其余可写项落表（首轮 1650 / 逐轮覆盖 / previous_text / closing_hint<600 / stall_retry_hint 含「至少写 N 字」） | 成立 |

文档卫生：`git diff --check` exit 0；裸行号 grep（`(server|moyu_continuation|moyu_consistency|run)\.py:[0-9]+`）无匹配；全文只用符号名。

**P10（low，文档精度）**：§3 透传表两行轮次描述不精确——
- `continuation_round` 行写「每轮」，实测第 1 轮没有该键（首轮 material 只有 `max_characters`/`overshoot_hint`/`remaining_characters`），应为「第 2 轮起（`previous_text` 非空时才有 `continuation_requirement`）」。
- `min_characters` / `stall_retry_min_characters` 行未注明轮次：前者只在**停滞恢复期**（触发停滞后的每轮）出现，后者只在**停滞重试轮**出现，第 2 轮（未停滞）实测确实没有。
- 附注（nit）：「这些数字模型看不到，只体现在 `minimum_hint` 的措辞里」略含糊——`minimum_hint` 只给出「本轮至少写 N 字 + 把缺口补回来」，既不含 `deficit_characters` 数字也不含剩余轮数；建议改成「只以『本轮至少写 N 字』的派生配额出现」。

复现：`/tmp/probe_passthrough_audit.py`（第 1 轮无 `continuation_round`）、`/tmp/probe_passthrough_stall.py`（`min_characters` 仅第 3–8 轮、`stall_retry_min_characters` 仅第 3 轮）。文件与符号：`docs/continuation-production.md` §3 表。要求：改这两行的轮次描述即可，其余不动。

### C.3 当前判定

原判定 **needs_revision** 不变：P3（≥80% 未达成）与 P8（顶层余量冲突）仍开；P4 主体已合格，仅剩 P10 两行轮次精度（low）。P5/P6/P7/P9 均已按 captain 裁决收口或降级。

### C.4 P10 复核（docs-engineer 二次修改，2026-10-23 22:06:53）

三处逐条核对，全部与实测一致：

1. §3 表 `continuation_round` 行已改「第 2 轮起（第 1 轮走冻结请求，提示里没有这组键）」——与 `/tmp/probe_passthrough_audit.py` 第 1 轮无该键一致；顺带补的 `previous_text` 为空时 `continuation_requirement` 随之缺席，也与 `continuation_round_material()` 的 `if previous_text:` 条件一致。
2. `min_characters` / `stall_retry_min_characters` 行已注明「仅停滞场景：`min_characters` 在停滞恢复期每轮出现，`stall_retry_min_characters` 只在停滞重试轮出现（未停滞的第 2 轮没有）」——与 `/tmp/probe_passthrough_stall.py` 的 presence（min_characters 3–8、stall_retry_min_characters 仅 3）一致。
3. nit 已改为「模型既看不到 deficit 的数值，也看不到剩余轮数，只以 `minimum_hint` 里那句『本轮至少写 N 字』的派生配额形式出现」——与 `continuation_round_material()` 生成的 `minimum_hint` 文案一致。

卫生复核：`git diff --check` exit 0；裸行号 grep 无匹配；文件 180 行（docs-engineer 报 181 行、§7 第 2 条报 167 行，实际为 166 行，属其自报坐标偏差 1 行，不影响内容）。

**P10 关闭。** P4 至此全部合格。当前仅剩：P3（按 captain 裁决保持未达成口径）、P8（medium，顶层 `remaining_characters` 冲突，待新任务）。原判定 needs_revision 不变。

### C.5 坐标口径更正（docs-engineer 说明，append-only）

docs-engineer 说明其「N 行」来自 `text.count("\n") + 1`，文件以换行结尾时多算一个空行，故此前报 181（实际 `wc -l` 180）。我已按 `wc -l` 复核其重报坐标，全部命中：

| 坐标 | 实际内容 | 结论 |
|------|------|------|
| 84 | `### 哪些引擎提示会到达模型` | 命中 |
| 88 | 透传表表头 | 命中 |
| 90–95 | 表体（`previous_text` 行 90 … `min_characters` 行 95） | 命中 |
| 97–100 | 「两个容易踩的坑」段（100 为顶层 `remaining_characters` 警告） | 命中 |
| 102–105 | 「两条可以背下来的结论」（106 为空行） | 命中（区间末端应为 105，非 106） |
| 166 | §7 边界第 2 条（`overshoot_guard` 只保证「说到」） | 命中 |
| 180 | 维护约定末条（新键必须显式透传） | 命中 |

内容未变，P10/P4 维持关闭。另约定：若后续 P8 修复改成「顶层 `remaining_characters` 逐轮更新」，docs-engineer 会删除现有警告并改为正常描述，届时我随第二轮复验一并核对。

---

## 十、第二轮：t8 + P8 复核（append-only，保留第一轮 needs_revision 作为历史）

### 10.1 结论

**第二轮 verdict：pass。** 当前字节（`backend/server.py` mtime 22:15:08、`tests/budgeted_continuation_http_fixtures.py` mtime 22:15:20）上，P2（首轮无额度）与 P8（顶层 `remaining_characters` 首轮快照）均已闭合，默认路径零回归，文档 77.8% / `q1_target_met=false` 口径与 JSON 一致，全量 91 个 fixture 0 FAIL。第一轮的 `needs_revision` 保留为历史记录（P3 未达成口径不变，P7/P9 残余如实降级，新增 P11 low 文档精度）。

### 10.2 命令表（本轮实跑，退出码与关键输出）

| # | 命令 | 退出码 | 关键输出 |
|---|------|------|------|
| 1 | `.venv/bin/python -m py_compile backend/server.py backend/moyu_continuation.py backend/moyu_consistency.py` | 0 | 无输出 |
| 2 | `.venv/bin/python tests/budgeted_continuation_http_fixtures.py` | 0 | `Ran 14 tests in 7.975s OK` |
| 3 | `.venv/bin/python tests/budgeted_continuation_fixtures.py` | 0 | `budgeted continuation fixtures: OK {'cases': 43}` |
| 4 | `.venv/bin/python tests/continuation_progress_http_fixtures.py` | 0 | `Ran 4 tests in 2.981s OK` |
| 5 | `.venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023 --repeat 2` | 0 | `verdict：PASS`；连跑两次产物 md5 逐字节一致（baseline `359482cc4bed2098979dc74ac620dd0b`、comparison `f943c7e512e02595f22a981752c39116`）；v1.2 digest `sha256:9a6a72f079fe2b9c49a7619dd09cbec6bd77863c6773b94b6827dfc84e6b98ac` |
| 6 | `/tmp/probe_round2.py`（自造假 provider 探针，复用 fixture harness） | 0 | `CHECKS=36 FAILS=0` |
| 7 | `for f in tests/*_fixtures.py; do .venv/bin/python "$f" ...; done` | 0 | 91 个文件全部执行，无 `FAIL <file>`；日志扫描无 `FAILED` / `Traceback`；`reference_passages_http_fixtures.py` 报 `source_unchanged: true`、`passed: true`（36 checks） |
| 8 | 裸行号 grep + `git diff --check` | 1 / 0 | 无裸行号匹配；`git diff --check` exit 0 |

补充说明（如实记录）：本轮 `--compare` 的 `budgeted-comparison.json` md5（`f943c7e5…`）与第一轮（`0ac817f6…`）不同。原因是 `benchmarks/budgeted/run.py` 在第一轮运行后被改过（mtime 22:04:32，含 v1.1/v1.2 臂与 sensitivity 的补齐），不是不确定性：同一份当前字节连跑两次 md5 完全一致，且 `budgeted-v1.2-baseline.json` md5 与第一轮相同（`359482cc…`），v1.2 场景结果未变。

### 10.3 逐条复验

**① P2 关闭：首轮额度真的到达模型，且与冻结审计逐字一致。**

探针走真实 HTTP 路径（`POST /api/ai/queue` → `POST /api/ai/jobs/<id>/process`，假 SSE provider 记录每轮 payload）：

| target | 首轮 material `max_characters` | `remaining_characters` | `overshoot_hint` | 首轮真实 messages == consent `request_audit.payload.messages` |
|---|---|---|---|---|
| 1000 | **1100** | 1000 | 含「1100」 | 逐字相等 ✅ |
| 3000 | **3300** | 3000 | 含「3300」 | 逐字相等 ✅ |

同时确认：首轮 material 无 `continuation_round`（第 1 轮走冻结请求）；第 2 轮 `max_characters` 已收窄（不复读开场值）；`consent.request_audit` 的 JSON 里也能搜到开场额度数值（冻结即证据）。代码位置与顺序也复核过：`create_ai_job()` 的 `round_budget(0, target)` 注入发生在 `ai_result_metrics()` 与 `ai_prompt_preview()` 之前。

**② P8 关闭：真实多轮路径顶层 `remaining_characters` 逐轮跟住嵌套值（按 captain 要求重测）。**

探针 target=1500、`max_rounds=5`、`hard_max_rounds=5`、每轮 200 字，逐轮解析 user message 里的 material JSON：

| 轮 | 顶层 `remaining_characters` | 嵌套 `continuation_round.remaining_characters` |
|---|---|---|
| 1 | 1500 | —（第 1 轮无该组键） |
| 2 | 1300 | 1300 |
| 3 | 1100 | 1100 |
| 4 | 900 | 900 |
| 5 | 700 | 700 |

顶层与嵌套第 2 轮起逐轮相等且严格单调下降，**没有「8 轮恒 1500」**；`budgeted_continuation_report.characters=1000`（5×200）。机制与 captain 判断一致：`continuation_round_material()` 无条件覆盖顶层键 + `generate()` 用 `round_context.update(...)` 落地。此证据来自当前字节（server.py 22:15:08），不是第一轮的旧版本。

**③ P1/P7 残余如实记录（不升级、不掩盖）。**

探针在停滞场景（第 2 轮只写 30 字逼出恢复期）清点 material 键：`min_characters`、`stall_retry_min_characters` **已透传**（第 3 轮起）；`deficit_characters`、`pace_hint`、`stall_retry_attempt` **仍被丢弃**。因此 P7 维持 low（残余 defect/pace）、P9 维持 nit（残余 stall_retry_attempt），文档 §3 坑段对此的描述与实测一致。

**④ 默认单次路径零回归。**

不传 `budgeted_continuation` 的 `continue`：provider **只调 1 次**；`result` 无 `budgeted_continuation` 字段；`app_ai_chunk` 的 seq 从 1 连续、拼接等于正文；`GET /api/ai/jobs/<id>` 正常完成。

**⑤ P3 口径保持未达成，文档与 JSON 三处一致。**

- JSON：`q1_target_met=false`、`first_pass=16/20`（rate 0.8）、3000 子集 `7/9`（rate 0.7778）、`rounds.mean=3.85`、`body_invariance 20/20`、`candidate_only 20/20`、digest `sha256:9a6a72f0…b98ac`。
- `docs/CHANGELOG.md`：状态行明写「77.8%（7/9），未达 ≥80%」；第 5 条明写 70.0%→80.0%、66.7%→77.8%、`q1_target_met: false`、digest，全部与 JSON 一致。
- `docs/strategy-beat-novelforge-2026-10-23.md`:197 明写「77.8%（7/9），未达成 ≥80%」并说明对上界只作附录、非计入。
- `docs/consistency-budget-v1.2-2026-10-23.md` 的 §6/§7 口径未改。

未发现「把未实现能力写成已实现」的新增表述：`continuation-production.md` 对 `deficit_characters`/`pace_hint`/`stall_retry_attempt` 明确写「未透传」，对超冲明确写「不裁剪正文」。

**⑥ 冻结后全量：0 FAIL。**

所有写域在 sweep 期间未变（sweep 前 server.py 22:15:08 / fixtures 22:15:20，sweep 后一致；`reference_passages_http_fixtures.py` 内 `source_files_sha256_before == after`、`source_unchanged: true`）。91 个 fixture 全部 exit 0，无失败清单。

### 10.4 问题清单（第二轮新增）

**P11（low，文档精度 / 文档落后代码）**：`docs/continuation-production.md:100` 仍写「顶层 `remaining_characters` 是 `create_ai_job()` 冻结的首轮快照，实测 target=1500 时 8 轮恒为 1500，要引用余量就引嵌套值，顶层值只能当本次目标」。t8 在 `continuation_round_material()` 里无条件覆盖顶层键后，这句话已不成立（本轮实测 1500/1300/1100/900/700，与嵌套逐轮相等）。同页 §3 结论（第 2 轮起必须用实时值覆盖）是对的，于是同一节内出现自相矛盾。
- 复现：`/tmp/probe_round2.py` 的 `probe_p8()`，打印 `round material top=... nested=...`。
- 要求：把该坑改为「顶层 `remaining_characters` 第 1 轮等于 `target_characters`，第 2 轮起由 `continuation_round_material()` 覆盖为实时余量，与嵌套值相等；不要用首轮快照当余量」；如保留历史说明，标注「v1.2 接线后已修」。
- 不阻断本轮交付：这属于「已实现能力被写成未实现」，不影响运行与验收数字，但会误导后续维护者写出错误断言，需在下一次文档改动时修掉。

**P5（low，建议下一轮补，不阻断）**：t3 修掉的 `mutate()` 按钮禁用回归仍无独立断言。当前浏览器 18 用例通过，但未直接断言「创建/保存/删除后工具栏刷新按钮 `disabled=false`」。建议下一轮加一条，避免同类回归再次静默。

### 10.5 已知边界

1. 本轮未重跑浏览器回归（`frontend/prompt-workshop.js` mtime 21:12:51，与第一轮相同字节）；t3 结论沿用第一轮 18 用例全 passed 的证据。
2. `deficit_characters`/`pace_hint` 的「值本身」是否真的有用（模型是否读得懂）不在本轮验证范围，只验证了透传/丢弃状态。
3. 基准的对抗式 provider 不读提示，因此 sc-15/sc-18 在计入口径下仍失败；本轮只验证「口径未改、未裁剪、未作弊」，不评价模型服从度假设。
4. 探针只覆盖 `continue` 任务；其他任务类型带预算仍按 400 拒绝（第一轮已验证，本轮未重复）。
