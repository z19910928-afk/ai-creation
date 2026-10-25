# 安全复核：d477912 及其新增审计文档的凭据与文档边界

复核日期：2026-10-25
复核对象：提交 `d477912`（`fix: make real-corpus audit evidence and q1 scope verifiable`）
复核范围：该提交的 5 个改动文件 + 提交信息 + 提交引入的 `/private/tmp` 证据引用
复核方式：**只读**。用 `git cat-file` / `git show` 读取 d477912 的 blob（不使用工作树内容），跑仓库自带隐私闸门，再用自建正则扫一遍该提交的全部改动 blob。全程**不打印、不复述任何 key 值**，命中只报「路径 / 行号 / 类别 / 计数」。

**Verdict：PASS** —— 未发现真实 API key、Bearer、Authorization、token、endpoint 凭据或任何可复用秘密；`/private/tmp` 产物只把「文件路径 + 统计数 + 公版文本引文」带进文档，产物本身不含凭据字段；三条安全声明均被代码证据支持，无夸大。另有 4 条**非阻塞**残余风险，见 §5。

---

## 0. 一个必须写明的边界：我审的是提交，不是工作树

复核时刻工作树**不等于** `d477912`：`git status --short` 显示 5 个已改未提交文件 + 2 个未跟踪文件。

| 状态 | 文件 |
| --- | --- |
| `M` | `benchmarks/real_corpus/extract.py`、`docs/CHANGELOG.md`、`docs/audit-response-2026-10-25.md`、`docs/consistency-budget-v1.2-2026-10-23.md`、`docs/real-corpus-extract-2026-10-24.md` |
| `??` | `docs/audit-request-2026-10-25.md`、`docs/audit-review-team-2026-10-25.md` |

因此本文所有行号引用都指向 **`d477912` 的提交版**（`git cat-file -p d477912:<path>`），不是工作树行号。工作树那批未提交改动我也扫了一遍（见 §2.3），结论同样是 0 命中；但它们不属于 d477912，不应记在本提交账上。

一句话口径：**提交版 `docs/audit-response-2026-10-25.md` 是 116 行；工作树已长到 158 行。本文引用行号一律按 116 行那版。**

---

## 1. 结论表

| # | 复核项 | 结果 | 依据 |
| --- | --- | --- | --- |
| 1 | 新文档/报告/提交信息是否含真实 API key、Bearer、Authorization、token、endpoint 凭据 | **不成立（无泄露）** | §2.1、§2.2：6 类凭据形状 0 命中 |
| 2 | 审计修复是否把 `/private/tmp` 真实产物中的敏感内容带入 docs | **不成立（未带入敏感内容）** | §3：产物无 provider/base_url/api_key 字段；docs 只引路径、统计数与《红楼梦》公版引文 |
| 3 | `audit-response` 与 `real-corpus` 的安全表述是否夸大/虚假 | **未发现夸大**；1 处「证据与声明不对齐」属非安全项 | §4：3 条安全声明逐条对到代码；§5 F4 |
| 4 | 仓库既有隐私/秘密扫描 | **通过** | §2.1：`repository privacy fixtures: ok`，exit 0 |
| 5 | 是否有凭据类文件被跟踪 | **无** | §2.4：只有 `.env.example` 模板，值全空 |

---

## 2. 扫描证据

### 2.1 仓库自带隐私闸门（权威判定）

```text
.venv/bin/python tests/repository_privacy_fixtures.py
-> repository privacy fixtures: ok (Git index only)
-> exit=0
```

说明：该闸门**只读 Git index，从不读工作树**（`tests/repository_privacy_fixtures.py:70-117`），因此 ignored 的本地凭据、数据库不会被误扫误印。复核时刻 index 与 HEAD 一致（改动均未 `git add`），所以它扫到的正是 `d477912` 的整棵树。

它覆盖的规则：`sk-` 前缀密钥、`Authorization: Bearer`、`API_KEY` 环境变量赋值、PEM 私钥块、敏感文件名（`.env` / `credentials.json` / `*.pem` / `blind-key-private.json` 等），并断言 `.env*` 变体被 ignore、模板仍可入库。

### 2.2 自建正则扫描（补闸门未覆盖的形状）

对 d477912 改动的 5 个文件逐一 `git show d477912:<path>` 后切片，按类别统计命中。**只输出路径、行号、类别、计数，不输出命中原文。**

| 类别 | 命中 | 判定 |
| --- | --- | --- |
| `sk-` 系密钥（`sk-` / `sk-proj-`） | **0** | — |
| `Authorization:` 头 | **0** | — |
| `Bearer <token>` | **0** | — |
| JWT 三段式（`eyJ…`） | **0** | — |
| PEM 私钥块 | **0** | — |
| 环境变量秘密赋值（非占位） | **0** | 唯一一处是 `benchmarks/real_corpus/extract.py:11` 文档串里的 `MOYU_EXTRACT_API_KEY=...`，值为字面 `...` |
| 40+ 位十六进制串 | 9 处 / 6 行（`docs/CHANGELOG.md:84,189`；`docs/consistency-budget-v1.2-2026-10-23.md:14,25,173,184`） | 全部是 **sha256 digest、commit sha、GitHub run id**，公开可复算，不是秘密 |
| `/private/tmp/` 具体文件名 | 13 行（见 §3.2） | 本地临时文件路径，见 §5 F1 |
| 私网 IPv4 | 2 行（`docs/CHANGELOG.md:69`、`docs/real-corpus-extract-2026-10-24.md:188`，均为 `192.168.8.50`） | **本次未引入**，见 §5 F2 |
| 外部 URL | 4 行（`benchmarks/real_corpus/extract.py:9`、`docs/CHANGELOG.md:135,190`、`docs/real-corpus-extract-2026-10-24.md:220`） | 公开端点/GitHub 链接，见 §5 F3 |
| `/Users/` 家目录路径 | **0** | 产物里的 `corpus.root` 不是家目录路径，且未进 docs |

### 2.3 工作树未提交改动（覆盖面补充，不计入本提交）

对本轮 `git status` 里 5 个已改文件与 2 个未跟踪文件跑同一套凭据形状（含 `sk-`、`sk-proj-`、`Authorization: Bearer`、`Bearer`、JWT、PEM、非占位 env 赋值）：**0 命中**。

结论：这些未提交内容继续提交**不会新增**凭据泄露面；但它们不属于 d477912 的复核结论。

### 2.4 凭据类文件与模板

```text
git ls-files | grep -Ei '(^|/)\.env|\.pem$|\.key$|\.p12$|\.pfx$|credentials|secrets|api[-_]keys?\.json|blind-key'
-> .env.example        （唯一命中）
```

`.env.example` 逐变量扫描：`MOYU_AI_API_KEY=` 等全部为空值/注释，非占位赋值命中 0（`MOYU_HOST`/`MOYU_PORT`/`MOYU_AI_*` 均无值）。模板里明写 `Never commit a real API key.`。

### 2.5 提交信息与元数据

`d477912` 的 subject / body / author / committer 逐字段读过：只有「改动意图 + 数字口径 + 已知缺口」，无 token、无 URL 凭据、无内网主机名。

---

## 3. `/private/tmp` 产物是否把敏感内容带进 docs

### 3.1 产物本身有没有凭据（先查源头）

三份产物 `/private/tmp/moyu-real-current*.json` 的**键名**（不看值）：

| 层级 | 键 |
| --- | --- |
| 顶层 | `benchmark, books, chapters, corpus, determinism, engine, generated_at, mode, timing, totals, version, wall_s` |
| `chapters[]` | `assertions, audit, book, chapter, chars, digest, elapsed_ms, ordinal, rejected, rejected_reasons, sha256, sha256_ok, title` |
| `audit[]` | `attribute, char_end, char_start, confidence, evidence_quote, predicate, subject, value` |

**没有** `api_key` / `base_url` / `endpoint` / `headers` / `authorization` 任何一项；`corpus` 只有 `books/limit/manifest/root`。凭据形状扫描：三份产物 + `/private/tmp/audit-realworld-visible.json` + `/private/tmp/audit_realworld.py`，`sk`/`bearer`/`authorization`/`api_key`/`pem` **各 0 命中**。

所以：**源头没有凭据可带**。

### 3.2 docs 实际引用了哪些 `/private/tmp` 内容

| 文件（提交版） | 命中行数 | 引用形态 |
| --- | --- | --- |
| `docs/audit-response-2026-10-25.md` | 10 行提及 `/private/tmp`，其中 9 行带具体文件名（`:12,16,27,28,29,35,85,87,97`） | 证据文件名 + 统计数（章数、字数、断言/拒绝数、`wall_s`） |
| `docs/real-corpus-extract-2026-10-24.md` | 3 行（`:173,174,175`） | 同上 |
| `docs/CHANGELOG.md` | 1 行（`:11`） | 同上 |
| `docs/consistency-budget-v1.2-2026-10-23.md` | 0 行 | — |

引用的**文本内容**只有一处：《红楼梦》原文 `小人姓封`（`real-corpus:252`、`audit-response:14,45`）。该书 1791 年成书，属公版；语料是 360 章公版长篇。**没有**现代私人文本、人名、联系方式或业务数据。

### 3.3 判定

**未带入敏感内容。** 带入的是「本地取证路径 + 聚合统计数 + 公版引文」。计入残余风险（F1），不计入泄露。

---

## 4. 安全表述逐条对码

| # | 文档声明（提交版行号） | 代码证据 | 判定 |
| --- | --- | --- | --- |
| S1 | 「请求头经 `--config -` 从 stdin 进…密钥只经 stdin 传入」`real-corpus:105,109` | `backend/moyu_extract.py:725-726`（docstring：请求头含 `Authorization` **只走 stdin 配置文件**，`ps` 看不到 key）；`:780` `[curl_bin,"--config","-"], input=config` | **成立**，与代码逐字一致 |
| S2 | 「异常与报告里脱敏为 `***`」`real-corpus:109` | `moyu_extract.py:506-528`（`_redact_detail` / `ExtractProviderError(...,_redact(...))`）；`:607-651`（授权头、digest 参数、scheme token、凭据键值对、常见令牌前缀、高熵串统一换 `***`） | **成立**，且覆盖面比声明更宽 |
| S3 | 「缺凭据直接 `exit 2`，不会悄悄跑规则模式冒充 LLM」`real-corpus:230`、`182` | `benchmarks/real_corpus/extract.py:235-240`：`elif args.mode == "openai"` → `provider_from_env()` 为 `None` 时打印缺失项并 `return 2`，不回落 `dry_run_provider` | **成立** |
| S4 | 「三个环境变量齐全才启用」`real-corpus:98` | `moyu_extract.py:834` `ENV_KEYS = ("MOYU_EXTRACT_BASE_URL","MOYU_EXTRACT_MODEL","MOYU_EXTRACT_API_KEY")`；`:873-877` 全齐才返回 provider | **成立** |
| S5 | 文档内的 key 一律占位 | `real-corpus:222` `export MOYU_EXTRACT_API_KEY=...`，值为字面 `...`；扫描 §2.2 env 赋值 0 命中 | **成立** |

顺带核对了提交声称的可复算数据（不是安全项，但决定这份审计回复可不可信）：

| 声明 | 实测 | 判定 |
| --- | --- | --- |
| `benchmarks/real_corpus/extract.py` 300 行 / `a46f89a2cbdb` | 提交 blob 300 行 / `a46f89a2cbdb` | ✅ |
| `backend/moyu_extract.py` 1041 行 / `6b5d43136405` | 1041 行 / `6b5d43136405` | ✅ |
| `tests/extract_fixtures.py` 1372 行 / `f48b4f191d7f` | 1372 行 / `f48b4f191d7f` | ✅ |

---

## 5. 残余风险（4 条，全部非阻塞）

### F1（low）`/private/tmp` 取证路径进入文档 —— 本次引入

- 位置：`docs/audit-response-2026-10-25.md:12,16,27,28,29,35,85,87,97`；`docs/real-corpus-extract-2026-10-24.md:173,174,175`；`docs/CHANGELOG.md:11`
- 内容：本机临时证据文件名与聚合统计，**无凭据、无 PII、无语料原文（除公版引文）**
- 为什么不判泄露：临时路径不含身份信息；产物无凭据字段（§3.1）
- 建议（非阻塞）：若这批文档要对外发布，把文件名换成「本机 `/tmp` 证据（文件名略）」，只保留统计数与 sha256 绑定

### F2（low）私网 IP `192.168.8.50` —— **非本次引入**

- 位置：`docs/CHANGELOG.md:69`、`docs/real-corpus-extract-2026-10-24.md:188`
- 证据：`git show d477912^:` 同名文件命中数 1 → 提交后仍为 1，**计数未变**，即 d477912 未新增
- 风险：暴露内网网段拓扑，配合代理/域名描述可推测内网结构。属历史遗留
- 建议（非阻塞）：后续文档统一改写成「解析到内网地址并被拒」

### F3（low）公开厂商端点与 GitHub 账号 —— **非本次引入**

- 位置：`docs/real-corpus-extract-2026-10-24.md:220`（`https://api.deepseek.com/v1`）、`docs/CHANGELOG.md:135`（`github.com/z19910928-afk/...`）、`benchmarks/real_corpus/extract.py:9` 同端点
- 判定：端点 URL 与仓库账号均为公开信息，**不是凭据**；任务里「endpoint 凭据」指带凭证的 URL，此处不存在
- 证据同上：父提交命中数与提交后一致

### F4（low）提交版引用的「9/9 证据」早于 `generated_at` 修复 —— 证据口径，非安全项

- 位置：`docs/audit-response-2026-10-25.md:85` 引用 `/private/tmp/audit-full-test.json` 作为 `--audit-limit 0` 复跑证据
- 实测：该文件 `mtime=2026-10-25 17:46:44`，**早于**提交时刻 `2026-10-25 17:52:38`；其 `generated_at` 仍是旧硬编码 `2026-10-24T00:00:00+08:00`
- 所以它只能证明「9/9 条证据可见」，**不能**证明 `generated_at` 已改为运行时刻；`audit-response:73` 写「已改，待提交」是对的，`:101` 第 5 条「没有把旧硬编码时间当运行时间」也没有虚假陈述，但 §5 的证据清单与声明存在错位
- 现状：工作树已有未提交修订（新增 §5.1 + `/private/tmp/audit-full-test-postfix.json`，`generated_at=2026-10-25T18:27:09+08:00`、mtime 逐秒一致），即该错位已在提交外被修
- 建议（非阻塞）：随下次提交落地 §5.1，并把 `:85` 那行标注为「仅证 9/9，不证时间戳」

---

## 6. 本次复核的命令（可复算）

```text
# 1) 仓库隐私闸门（只读 index）
.venv/bin/python tests/repository_privacy_fixtures.py
-> repository privacy fixtures: ok (Git index only)   exit=0

# 2) 凭据类文件与模板
git ls-files | grep -Ei '(^|/)\.env|\.pem$|\.key$|\.p12$|\.pfx$|credentials|secrets|api[-_]keys?\.json|blind-key'
-> .env.example

# 3) 提交版字节（不用工作树）
git show d477912:docs/audit-response-2026-10-25.md | wc -l        -> 116
git show d477912:benchmarks/real_corpus/extract.py | wc -l        -> 300
git cat-file -p d477912:benchmarks/real_corpus/extract.py | shasum -a 256 | cut -c1-12 -> a46f89a2cbdb

# 4) 是否有本次新增的私网 IP / 端点 / 家目录路径
git show d477912^:docs/CHANGELOG.md | grep -c '192.168.8.50'      -> 1
git show d477912:docs/CHANGELOG.md  | grep -c '192.168.8.50'      -> 1
（api.deepseek.com 同法：1 -> 1；/Users/ 在四个文档中 0 命中）

# 5) 产物是否含凭据字段（只看键名）
.venv/bin/python -c "import json;d=json.load(open('/private/tmp/moyu-real-current-5.json'));print(sorted(d))"
-> benchmark, books, chapters, corpus, determinism, engine, generated_at, mode, timing, totals, version, wall_s
```

---

## 7. 局限（不写成已覆盖）

1. **没跑真实模型、没读 key**：全程未调用任何 provider，`MOYU_EXTRACT_*` 的值从未被读取或打印。
2. **正则扫描按形状匹配**：低熵密钥（例如 12–16 位纯字母 token）可能逃过 `sk-`/`Bearer`/JWT 形状；仓库闸门的 `_ENV_ASSIGNMENT` 只认 `MOYU_AI_API_KEY` / `OPENAI_API_KEY` / `API_KEY` 三个变量名，其他命名（如 `XXX_TOKEN`）只有我这次自建扫描覆盖到。
3. **未审计 `.git` 历史与远端**：只审 `d477912` 的树与其父提交对比。更早提交、`git notes`、GitHub release 资产不在范围。
4. **未审工作树未提交改动的语义正确性**：只做凭据形状扫描（0 命中），那是别人的在途工作。
5. **`backend/` / `benchmarks/` / `tests/` / `frontend/` / `clear/` 未改动**：本次复核为只读，唯一写入是本文件。
