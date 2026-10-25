# 真实稿件 → 结构化断言：LLM 抽取器（2026-10-24）

本文记录 `backend/moyu_extract.py` 的设计边界，以及它在真实公版语料上**实际跑出来的数字**。
所有数字来自本机可复算的命令，命令与退出码写在 §7。

> 一句话结论：抽取器已落地并离线可跑（确定性规则模式），在 360 章真实语料上抽出
> **360 条断言 / 0 条 rejected / 2 353 378 字 / 37.9 ms**；LLM 分支的协议、失败降级、
> 传输与确定性都在 fixture 里用注入 transport 覆盖了，但**真实模型的外部复验仍未完成**
> （本机无 `MOYU_EXTRACT_*` 凭据，`--mode openai` 缺变量时 `exit 2`，那不算通过）——
> §6.3 与 §6.4 写清这个缺口，不给假数字。

---

## 1. 交付物

| 文件 | 作用 | 行数 | sha256（前 12 位） |
| --- | --- | --- | --- |
| `backend/moyu_extract.py` | 抽取器本体：schema 校验、逐字证据校验、规则 provider、OpenAI 兼容 provider、curl/urllib 传输 | 1041 | `6b5d43136405` |
| `benchmarks/real_corpus/extract.py` | 真实语料按章抽取基准：JSON + 统计 + 确定性复跑 | 293 | `a9e230535188` |
| `tests/extract_fixtures.py` | 67 个回归用例（schema / 证据定位 / 非有限数值 / 确定性 / provider 失败路径 / 传输与脱敏） | 1372 | `f48b4f191d7f` |
| `docs/real-corpus-extract-2026-10-24.md` | 本文 | — | — |

行数与 sha256 是本次交付时的实测值（`wc -l` / `shasum -a 256`）；后续改动后请重新测量，不要沿用本表。

依赖：**只用标准库**（`json`/`re`/`hashlib`/`statistics`/`urllib`），没有新增第三方包。

## 2. 对外接口

```python
extract_chapter(text, *, chapter_id, provider=None) -> list[dict]
extract_chapter_report(text, *, chapter_id, provider=None, environ=None,
                       max_assertions=500) -> dict
```

- 每条断言恰好 8 个键：`subject, predicate, attribute, value, evidence_quote,
  char_start, char_end, confidence`；未知键、缺键、类型不符**一律拒绝**（不是忽略）。
- `predicate` 白名单与生产端一致：`has_story_fact` / `has_attribute`
  （与 `backend/server.py` 的 `ASSERTION_PREDICATES` 同集合，但**不 import server**，保持离线可跑）。
- `extract_chapter_report` 额外返回 `rejected`（逐条明细）与 `rejected_reasons`（原因分布），
  并且**从不向调用方抛异常**：provider 崩了、模型吐了非法 JSON，都变成 rejected。
- `attribute` 允许 `None`（`has_story_fact` 无语义槽位时），但 `has_attribute` 必须给槽位名。

## 3. 证据闸门：只保证「可逐字定位」，不保证「语义正确」

| 闸门 | 规则 | 拒绝原因码 |
| --- | --- | --- |
| 区间形状 | `char_start >= 0`、`char_end > char_start`、且 `char_end - char_start == len(evidence_quote)` | `bad_range` |
| 逐字相等 | `text[char_start:char_end] == evidence_quote`，多一个字、少一个字、整体偏移一个字都算不通过 | `quote_mismatch` |
| 区间越界 | `char_end <= len(text)` | `bad_range` |
| 有限数值 | `value` / `confidence` 必须是有限数值（`NaN` / `Infinity` / `-Infinity` 拒绝） | `non_finite` |

**闸门保证什么、不保证什么**（t54 复审后的口径修正，之前写「防幻觉」是超出代码的承诺）：

| | 是 / 否 | 说明 |
| --- | --- | --- |
| 引文出自本章该码点区间 | ✅ 保证 | `text[char_start:char_end]` 与 `evidence_quote` 逐字相等，否则拒 |
| 断言结构合法（八键、类型、白名单、区间形状、有限数值） | ✅ 保证 | 任何一条不符即 `rejected`，不做「尽力修补」 |
| `subject` 指的是引文里那个人 | ❌ 不保证 | 抽取器不做实体链接/指代消解/别名合并；把「姓甄名费」标成 `subject=贾化` 照样过闸门 |
| `value` / `attribute` 与引文语义相符 | ❌ 不保证 | 没有语义蕴含校验，只比坐标 |
| 断言之间、跨章之间不矛盾 | ❌ 不保证 | 那是 `backend/moyu_consistency.py` 的活（读 `assertion` 表做一致性检查） |

`tests/extract_fixtures.py::test_quote_gate_does_not_verify_semantics` 把这个缺口写成可执行
断言：**故意用「引文对、主语错」的样本锁住「抽取器不做语义校验」这一事实**，防止以后有人把
quote 闸门当语义保证。

其余原因码：`unknown_key` / `missing_key` / `type_error` / `non_finite` / `empty_value` /
`predicate_not_allowed` / `duplicate` / `provider_error` / `invalid_json`，另有
`empty_text`（空章节）。`non_finite` 是 t54 复审补的：`json.dumps` 默认会把
`float("nan")` 写成裸 `NaN`，Python 自己读得回来、但浏览器 `JSON.parse` / Go / Postgres
`jsonb` 全部解析失败；现在 `validate_assertion()` 用 `math.isfinite()` 拦，
`strict_json()`（`digest()` 也走它）用 `allow_nan=False` 保证不产出非法字面量。

完整原因码集合：`REJECT_REASONS`。fixture 里每个码都有对应用例，且断言
「`rejected_reasons` 恰好等于该码 1 次」，避免出现「拒绝是拒绝了，但原因是别的」。

## 4. 两种 provider

### 4.1 规则模式（默认，离线）

六条正则规则，**全部要求主语已知**（命中位置前 30 字内出现过「姓X名Y」），
取不到主语就跳过而不是瞎填：

| 规则 | 模式 | 槽位 | 置信度 |
| --- | --- | --- | --- |
| `_rule_name` | `姓X名Y` | `real_name` | 0.9 |
| `_rule_called_name` | `名唤/名曰/名叫/叫做X` | `real_name` | 0.75 |
| `_rule_courtesy_name` | `表字X` / `字X` | `courtesy_name` | 0.7 |
| `_rule_origin` | `乃/系/本贯/祖贯 + X人氏` | `hometown` | 0.7 |
| `_rule_age` | `年方X岁`（中文数字转整数） | `age` | 0.85 |
| `_rule_height` | `身长X尺` | `height` | 0.8 |

规则只产出 `has_attribute`（规则命中天然带槽位），**不产出 `has_story_fact`**——那类
自由叙事事实交给 LLM 分支，规则不猜。

### 4.2 OpenAI 兼容模式（有凭据时）

- 三个环境变量齐全才启用：`MOYU_EXTRACT_BASE_URL` / `MOYU_EXTRACT_MODEL` / `MOYU_EXTRACT_API_KEY`；
  缺任意一个 → 回落到规则模式，**不报错、不崩**。
- 请求：`POST {base}/chat/completions`，`temperature=0`，system 要求「只输出 JSON」，
  user 侧是 `PROMPT_TEMPLATE` + 正文前 12 000 字。
- 响应解析容忍 ```` ```json ```` 围栏、`{"assertions": [...]}` 信封、裸数组；
  其余（非 JSON、缺 `choices[0].message.content`、content 不是断言数组）→ `invalid_json`；
  非 2xx、网络异常 → `provider_error`。**这两类都只进 rejected，不抛给调用方**。
- 传输：默认走 curl 子进程（`_curl_transport()`），请求头经 `--config -` 从 stdin 进、请求体走临时文件、
  `--max-time` 为超时 + 15 秒余量；`curl_available()` 为假才回落 `_urllib_transport()`。超时
  `DEFAULT_TIMEOUT = 180.0`，可用 `MOYU_EXTRACT_TIMEOUT` 覆盖（非法值显式 `ValueError`，不静默取默认）。
  两条路径都**只发一次请求、不自动重试**；非 2xx 一律 `provider_error`（403 与 error code 1010 实测可见），
  密钥只经 stdin 传入并在异常与报告里脱敏为 `***`。

> 期间抓到一个真 bug：`PROMPT_TEMPLATE` 里写了字面量 `{"assertions": []}`，而代码用
> `str.format()` 渲染 → `KeyError: '"assertions"'`，**任何真实 LLM 调用都会立刻失败**。
> 是 `test_openai_provider_accepts_json` 用注入 transport 跑出来的，改成 `str.replace` 后修复。

## 5. 确定性

- 规则 provider 是纯函数，无随机、无时间、无并发；
- 断言按 `(char_start, char_end, subject, predicate, attribute, value)` 排序后返回；
- `digest(assertions)` 用 `json.dumps(sort_keys, separators)` 的 sha256；
- 基准脚本默认 `--repeat 2`，两次**每章 digest 拼接后再取 sha256** 比对。

实测 `determinism.identical=True`（20 章与 360 章两次都成立）。

## 6. 真实语料上的产出量

语料：`/tmp/moyu-real-corpus/corpus`，三本公版长篇各 120 章（共 360 章），
逐章 sha256 与 `benchmarks/real_corpus/corpus-manifest-2026-10-24.json` 一致
（脚本读不到文件或 sha256 不符会直接退出，不会静默跳过）。

### 6.1 `--limit 20`（默认验收口径）

| 指标 | 值 |
| --- | --- |
| 章数 | 20（红楼梦 1–20） |
| 正文字符 | 124 534 |
| 断言 | **10** |
| rejected | 0（原因分布 `{}`） |
| 槽位分布 | `real_name` 5、`courtesy_name` 3、`hometown` 2 |
| 抽取耗时 | 2.124 ms（总 6.667 ms） |
| 确定性 | `identical=True`，digest 前缀 `241b76bbd562a59fcd4…` |

### 6.2 全语料 360 章（`--limit 400`）

| 指标 | 值 |
| --- | --- |
| 章数 | 360（三国演义 120 / 水浒传 120 / 红楼梦 120） |
| 正文字符 | 2 353 378 |
| 断言 | **360**（三国 195 / 水浒 154 / 红楼 11） |
| rejected | 0（原因分布 `{}`） |
| 槽位分布 | `real_name` 247、`courtesy_name` 91、`hometown` 18、`age` 2、`height` 2 |
| 抽取耗时 | 37.873 ms（总 113.785 ms） |
| 确定性 | `identical=True`，digest 前缀 `d4b2d0b5e399308dc6f…` |

### 6.3 怎么读这些数字

- **rejected 为 0 只说明「没有坐标错误」**：规则模式按构造产出候选（引文直接从原文切），
  坐标必然对得上，所以 `quote_mismatch`/`bad_range` 恒为 0；这不代表断言语义正确。
  rejected 的真实战场是 LLM 模式。fixture 用注入 transport 造了偏移一字、区间错位、
  引文替换、超长引文四类坐标错误，全部按预期落 `quote_mismatch`/`bad_range`。
- **召回率低是设计选择**：规则只在「能确定主语 + 能逐字定位」时出手。红楼梦 120 章只有 11 条，
  因为古典白话里人物出场多为「只见那边来了一个……」而不复述全名；宁可漏，不可错。
- 每章平均 1 条（360/360），三国/水浒明显更高（1.63、1.28），符合演义体「姓甚名谁、何方人氏、
  年方几何」的开场套式。

### 6.4 验证范围：哪些是 smoke、哪些是真实语料、哪些没跑

把「跑过」按证据强度分成四档，别混着读：

| 档 | 实际做了什么 | 能得出的结论 | 不能得出的结论 |
| --- | --- | --- | --- |
| 本机 smoke | 注入 transport / 本地假服务，走 CLI 全链路（解析、拒绝码、脱敏、超时矩阵、不重试计数） | 协议与失败路径按设计工作 | 真实网关的行为 |
| 真实公版语料（规则模式） | 360 章真实文本、2 353 378 字，抽出 360 条断言 | 规则在真实文本上的产量与确定性 | 语义正确性、LLM 召回率 |
| 外部真实模型（成员环境） | **没有跑**：本机无 `MOYU_EXTRACT_*` 凭据 | 目前只能确认「无凭据时 `--mode openai` 退出 2 并打印缺失项」 | 真实召回率、幻觉率、跨网关兼容性 |
| 外部真实模型（captain 授权复跑） | 用已授权服务跑当前生产 CLI 2 章：**进程 `rc=0`，业务结果 `provider_error` × 2** | 只能证明失败被如实记账、没有把失败读成成功 | 任何抽取质量；本次未进入模型质量判定 |

- **exit 2 不是通过**：`--mode openai` 缺凭据时脚本直接 `exit 2`（打印缺失的环境变量），
  这是「没跑」，不能写进通过项；也不要把它当成「已复验真实模型」。
- **`rc=0` 也不是通过**：captain 授权的那次复跑退出码为 0，但两章的业务结果都是 `provider_error`。
  这次失败发生在网络层、请求没有进入模型：captain 复跑当时，无凭据直连、代理、HTTP/1.1 与 IPv4
  四条路径均报 `Could not resolve host`（**当时的观察，不代表此后仍不可达**），即那一轮
  DNS / endpoint 未建立真实请求，需要可解析且可达的 base URL 才能复跑。同一轮诊断里另观察到
  该域名被解析到 `192.168.8.50` 且 TCP 连接被拒、代理 `CONNECT` 返回 200 之后仍
  `SSL_ERROR_SYSCALL`、以及 Google DNS-over-HTTPS 查询返回 `Status=3`（NXDOMAIN）——同为
  **先前诊断的观察记录**，不作为当前结论使用。
  **本次未进入模型质量判定，不能据此评价模型**；「成员环境无凭据」与「captain 已授权复跑」
  是两件事，都要读成**未复验**，不得写成「真实模型已通过」。
- 旧样本（一次早期真实样本导入，1 章 6 774 字）只证明「CLI → 默认 curl → 解析」闭环，
  样本本身不代表语料规模，本文不拿它当验收数字。
- Windows 与外部网关行为属静态推断，未复验。

## 7. 复现

```bash
# 编译
.venv/bin/python -m py_compile backend/moyu_extract.py benchmarks/real_corpus/extract.py

# 回归（67 个用例）
.venv/bin/python tests/extract_fixtures.py

# 20 章验收口径
.venv/bin/python benchmarks/real_corpus/extract.py --limit 20 --json /tmp/extract-out.json

# 全语料
.venv/bin/python benchmarks/real_corpus/extract.py --limit 400 --json /tmp/extract-all.json

# 全量 fixture 巡检（逐文件取 python 自身退出码；不要用 timeout，macOS 没有 GNU timeout）
for f in tests/*_fixtures.py; do .venv/bin/python "$f" || exit 1; done
```

真实模型抽取（外部复验未完成，本文数字均不来自这一条路径）：

```bash
export MOYU_EXTRACT_BASE_URL=https://api.deepseek.com/v1
export MOYU_EXTRACT_MODEL=deepseek-chat
export MOYU_EXTRACT_API_KEY=...
.venv/bin/python benchmarks/real_corpus/extract.py --mode openai --limit 20 --json /tmp/llm.json
```

`--mode openai` 但三个变量不全时脚本会直接 `exit 2` 并打印缺失项，不会悄悄跑规则模式冒充 LLM。

## 8. 已知局限（诚实清单）

1. **真实模型外部复验未完成**：本机无 `MOYU_EXTRACT_*` 凭据，`--mode openai` 缺变量时 `exit 2`
   （见 §6.4，退出码 2 是「没跑」不是「通过」）；已覆盖的只有协议、解析、降级、传输与确定性，
   真实召回率/幻觉率**未知**。
2. **规则召回低且偏结构化**：只覆盖六类开场套式；心理、因果、关系类事实抽不到。
3. **不做指代消解**：`_nearest_subject` 只看前 30 字，跨段回指（「他」「那妇人」）不追。
4. **字符下标是 Python code point**：与生产端口径一致（`len(text)`），但若未来接 UTF-16 索引需换算。
5. **引文上限 240 字**：更长的证据片段会被拒（`bad_range`），避免把整段当证据。
6. **单章上限 500 条**：超出部分记 `duplicate`（复用同一原因码表示「超限」），
   这件事在原因分布里看不出来，只能从 `rejected` 明细读 `detail`。
7. **未接入生产链路**：本任务只交抽取器与基准，写库（`ensure_memory_assertion`）由 server 侧负责，
   不在本文范围内。
8. **证据闸门不验语义（最重要的一条）**：`verify_evidence()` 只比较 `text[char_start:char_end]`
   与 `evidence_quote`。主语指错人、取值张冠李戴、`attribute` 槽位名与引文无关——本模块一律放行。
   语义正确性必须靠服务端的断言复用比较与 `backend/moyu_consistency.py` 的一致性检查兜底，
   不要把这个闸门当成「幻觉进不去」的保证。t54 复审之前本文宣称的「防幻觉」是超出代码的承诺，
   已按实际能力收窄（见 §3 的「保证什么 / 不保证什么」表）。
9. **非法数值已拦但 LLM 语义幻觉仍未验收**：`NaN` / `Infinity` / `-Infinity` 现在在
   `validate_assertion()` 被拒（`non_finite`），`digest()` 与 `strict_json()` 也不会输出非法
   JSON 字面量；但「模型把甲的事写成乙」这类语义幻觉，本仓库在没有 `MOYU_EXTRACT_*` 凭据的
   情况下**无法验收**，只能等有凭据时用真实语料单独测召回率与幻觉率。
