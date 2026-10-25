# t66 最终字节绑定验收：F1 修复、抽取器与收口文档

**结论：pass（needs no revision）。**

本轮唯一被测窗口是 t55 提交 `27448029b4c8505deea4bb3a7b9095d25799efab` 之后的稳定快照：10 个文件在验收前后逐字节一致，F1（curl 失败详情先截断后脱敏）在 200 字符边界、换行、短 key 三种形态下都不再泄漏凭据前缀，传输契约（curl 优先、urllib 回落、单次请求不重试、密钥只进 stdin 配置、超时与非 2xx 落 `provider_error`）与文档表述一致；抽取器与文档的 1041 / 293 / 1372 行、67 个用例、benchmark 293 行均与 `wc -l` 实测相符。唯一的 low 是抽取器文档自述行数一栏写「—」，不构成阻断。

## 零、被测字节（验收前 = 验收后）

| 文件 | sha256 | 行数 |
| --- | --- | --- |
| `backend/moyu_extract.py` | `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8` | 1041 |
| `backend/moyu_consistency.py` | `b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2` | 1222 |
| `backend/server.py` | `319b4346ed418197d225ad63c8b34ecf6070105a07910f0e5677fac2bee3c810` | 8153 |
| `tests/extract_fixtures.py` | `f48b4f191d7fa390962539dd69c1a49453354351c3615350bf2246db1f2f71b5` | 1372 |
| `tests/consistency_fixtures.py` | `043b6ac5da07f69e61cd5d8c09f735b5990c7fab7e1af98b4c3b8075549b8f7a` | 640 |
| `benchmarks/real_corpus/extract.py` | `a9e23053518848ed8d6dfd51b7c8960283f4c5177f6d8411016e7a81ee6662f7` | 293 |
| `docs/real-corpus-extract-2026-10-24.md` | `1f59286d4cc017842510aaf5cf8fabdadb328f5c3ee44d398cc146eb5a5cfc8d` | 241 |
| `docs/real-corpus-acceptance-2026-10-24.md` | `55aca63113d7569b4f09857bc2a4e5a20087337936c5d0cea9d5e8295d506e5e` | 205 |
| `docs/consistency-engine.md` | `90e8baf5ec7c4400c155c500e46f7ee7d657415bb0d8239208d440e210fb23f2` | 388 |
| `docs/CHANGELOG.md` | `5852ed5f315124630e73258ecd04ce12d9b574d9ab79ae4bf23b7cbd60dd4576` | 182 |

> 表内全部为 `shasum -a 256` 实测值；`/tmp/t66_pre.txt` 的逐文件比对结果为 `BYTES_UNCHANGED=True`（SAME，无一行差异）。报告落盘后我又重算一遍 10 个文件，仍为 SAME。

`git show --stat 2744802` 只含 4 个 docs 文件（`CHANGELOG.md` / `consistency-engine.md` / `real-corpus-acceptance-2026-10-24.md` / `real-corpus-extract-2026-10-24.md`），没有 `clear/`，也没有未跟踪代码被卷入该提交。

## 一、命令表

| 命令 | 退出码 | 关键输出 |
| --- | --- | --- |
| `.venv/bin/python -m py_compile backend/moyu_extract.py backend/moyu_consistency.py backend/server.py tests/extract_fixtures.py tests/consistency_fixtures.py` | 0 | 无输出 |
| `MOYU_TEST_PYTHON=$PWD/.venv/bin/python .venv/bin/python tests/extract_fixtures.py` | 0 | `extract fixtures: OK {'cases': 67}` |
| `MOYU_TEST_PYTHON=$PWD/.venv/bin/python .venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| 全量 `tests/*_fixtures.py`（脚本 `/tmp/t66_sweep.sh`，逐文件日志） | 0 | `FILES=94 FAILS=0`；94 份日志无 `FAILED` / `Traceback` |
| `.venv/bin/python /tmp/probe_t66.py`（自造，27 checks） | 0 | `CHECKS=27 FAILS=0` |
| `wc -l` 三文件 + 文档表对照 | 0 | 1041 / 293 / 1372，与文档表一致；67 用例与 fixture 输出一致 |

## 二、F1：跨 200 字符边界脱敏（独立复现）

自造探针 `/tmp/probe_t66.py`（只读 import，不改仓库）：

1. **A 组：curl stderr 跨界**。构造 `"A"*190 + "Bearer sk-…" + " tail"`（key 从第 197 字符开始，跨越 200 字符截断点），注入 `runner` 让 `_curl_transport` 以退出码 7 失败。
   - `A2`：`detail` 不含完整 key；`A3`：不含 8 字符前缀；`A4`：仍保留「curl 失败（退出码 7，请求超时设定 5 秒）」。
   - `A5` 是对照组，按旧顺序（先 `[:200]` 再脱敏）重算，结果尾部残留 `'AAAAA*** sk-'` —— 证明「先脱敏后截断」这个修复不是恒真断言，旧实现确实会泄漏 key 前缀。
2. **A6/A7：换行 + 短 key**。stderr 中插入换行并只用 `sk-abc123`，`detail` 既不含短 key，也不含 `Bearer` scheme。
3. **B 组：HTTP 非 2xx 与 content 路径**。让响应体 / content 在 200 字符附近回显 `Authorization: Bearer …`，`detail` 不含 key 或前缀，且 `HTTP 403：…` 状态码仍可见、`reason == "provider_error"`；`detail` 长度 209，说明是「先完整脱敏再截断到 200 字符窗口」。

结论：F1 的修复顺序正确，且我对「跨界」「换行」「短 key」三种形态都拿到了不泄漏的实测输出。

## 三、传输契约（curl 优先 / 回落 / 不重试 / 凭据不进 argv）

| 断言 | 实测 |
| --- | --- |
| curl 可用时走 curl，且只走一次 | `C1 calls=1`；`C2` 返回状态码 200 原样透传 |
| curl 缺失时回落 urllib | 把 `curl_available` 置 False 并注入 `urlopen` 计数：`C3 calls=1`，异常文本命中注入点 |
| 非 2xx 只发一次请求 | `E1 calls=1`（注入 transport 返回 500） |
| 非 2xx → `provider_error`，状态码/网关码可见 | `E2 reason=provider_error`，`detail` 含 `HTTP 500` 与 `1010` |
| 超时 → `provider_error` 且说明超时 | `D1 reason=provider_error`，文案「curl 超时：超过 20 秒被强制终止（请求超时设定 5 秒）」 |
| 非法 timeout 被拒绝 | `D2`：0 / -1 / 3601 / `"x"` 全部抛错 |
| argv 不含凭据 | `E3` argv 恰为 `['curl', '--config', '-']`；`E4/E6` argv 无 key；`E5` 凭据出现在 stdin 配置文本里（确实发出） |
| curl 缺失（子进程 FileNotFoundError）显式报错 | `F1 detail` 含「curl 不可用」 |

这 8 条覆盖了任务要求的「传输三条路径」：curl 成功、curl 失败（退出码/超时/缺失）、urllib 回落；以及「无自动重试」「密钥只进 stdin」。

## 四、文档与提交一致性

- `docs/real-corpus-extract-2026-10-24.md` §1 表：`moyu_extract.py` 1041、`real_corpus/extract.py` 293、`extract_fixtures.py` 1372、67 个用例，全部与 `wc -l` / fixture 输出一致；sha256 前 12 位与实测一致。
- `docs/real-corpus-extract-2026-10-24.md` 第 7–8 行、172–188 行、222–234 行：明写「真实模型外部复验仍未完成」「captain 授权的 2 章复跑 `rc=0` 但业务结果 `provider_error` × 2」「证据闸门不验语义」；与 t66 要求保留的未达成立场一致。
- `docs/CHANGELOG.md`：第 5 行与第 29 行保留「3000 字子集 77.8%（7/9），未达 ≥80%」与「30 万字 × 3 部未执行」；第 26 行如实写「真实模型未复验、两条 low、Windows/外部网关未验」。
- `docs/consistency-engine.md` 388 行、`docs/real-corpus-acceptance-2026-10-24.md` 205 行，与 captain 给的冻结快照一致；`git status --porcelain docs/` 只有历史 review 报告为未跟踪文件，没有对上述 4 个 docs 的未提交修改。
- 全仓 `grep -nE '(moyu_extract|moyu_consistency|server)\.py:[0-9]+' docs/real-corpus-extract-2026-10-24.md docs/real-corpus-acceptance-2026-10-24.md` 无输出（无裸行号）。

## 五、未达成项、外部阻塞与保留边界（不得读成「全做完了」）

1. **Q1 预算续写 3000 字子集仍是 77.8%（7/9）< 80%**，`q1_target_met=false`；本轮不动该记录。
2. **「30 万字 × 3 部真实作品」一致性验收仍未执行**：现有数字来自单部 40 章 / 105,182 汉字的合成库自测（召回 100%、干净库 0 误报），生成器与检测器同源，属自测而非第三方语料验收。
3. **真实模型外部复验未完成（外部阻塞）**：本机无 `MOYU_EXTRACT_*` 凭据；captain 授权的 2 章复跑进程 `rc=0`，但两章业务结果均为 `provider_error`，请求未进入模型，只能证明失败被如实记账。真实召回率、幻觉率、跨网关兼容性均未验证。
4. **保留的 low 边界**：t76 两条（中文 token 值过度抹除、URL/百分号编码凭据形态未覆盖）、t60 两条（fixture 自比 digest、`strict_json` 的 `default=str` 静默字符串化当前 schema 不可达）、Windows 与外部网关行为未复验。本轮没有关闭也没有恶化它们。
5. **`clear/` 与未跟踪代码**：不在 `2744802` 提交内；本轮未验证 `clear/` 目录内容（任务明确不写该目录）。

## 六、问题清单

| 编号 | severity | 文件 / 符号 | 问题 | 要求怎么修 |
| --- | --- | --- | --- | --- |
| P1 | low | `docs/real-corpus-extract-2026-10-24.md` 第 18 行表格 | 文档自述行数一栏写「—」，而表头声明「行数与 sha256 是本次交付时的实测值」，自指行无法按同口径对账 | 下一轮把该文件自身行数（241）填进表格，或把表头改成「前三个文件」 |

没有 blocker / high / medium；P1 不影响字节绑定与结论。

## 七、已知边界（我没覆盖到的）

- 没有访问真实网关：curl 的 200/403/1010 行为是代码与既有文档记录，本轮只验证了「拿到状态码后如何记账」；无法验证网关指纹差异。
- 没有真实 `MOYU_EXTRACT_*` 凭据，`--mode openai` 的端到端召回率/幻觉率未跑（与文档的「未复验」一致）。
- 抽取文档里「360 条断言 / 0 rejected / 2,353,378 字」来自既有回归产物，我没有重跑 360 章语料；只验证了 67 个 fixture 用例与文档表数字。
- 没有测试 Windows 路径与 urllib 在真实代理下的行为。

**验收绑定**：本报告结论只对 `backend/moyu_extract.py` `6b5d43136405…`、`tests/extract_fixtures.py` `f48b4f191d7f…`、`benchmarks/real_corpus/extract.py` `a9e230535188…`、`docs/real-corpus-extract-2026-10-24.md` `1f59286d4cc0…`、`docs/real-corpus-acceptance-2026-10-24.md` `55aca63113d7…`、`docs/consistency-engine.md` `90e8baf5ec7c…`、`docs/CHANGELOG.md` `5852ed5f3151…` 这一组字节成立；任一文件变动后本结论自动失效。
