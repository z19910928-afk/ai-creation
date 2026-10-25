# 审计修复工单（外部审计方提出，2026-10-25 17:40）

> 提出方：另一会话的独立审计（未参与本仓库实现）
> 受理方：本仓库当前实现方
> 性质：**请先审计核实，再修复**。本文所有"结论"都附了可复算路径，若你的核验与本文冲突，以你的核验为准，并在报告里指出本文哪一条不成立。

---

## 0. 一句话

三件事实与文档不一致，两处指标不可用，一件遗留指标未处理。请审计后按 §4 的顺序修复。

---

## 1. 请优先核实的三个事实

### 1.1 文档说"外部真实模型没有跑"，但 /tmp 里有跑通的证据

- 文档表述：`docs/real-corpus-extract-2026-10-24.md` §6.4 一行写「外部真实模型（成员环境）：**没有跑**：本机无 `MOYU_EXTRACT_*` 凭据」。
- 实测证据（均在 `/tmp`，请自行复核）：
  - `/tmp/moyu-real-current-5.json` — 5 章 / 33,120 字，**23 条断言**，rejected 5（`empty_value`），耗时 172.19 s。
  - `/tmp/moyu-real-current-180.json` — 2 章 / 12,463 字，**17 条断言**，rejected 14（`empty_value` 6 + `quote_mismatch` 8）。
  - `/tmp/moyu-real-current.json` — 2 章，2 条断言，rejected 2。
  - `/tmp/moyu-chat-smoke.json` — 裸 chat completion 冒烟，返回真实模型 `deepseek-v4.1-flash`，`created` 时间戳 1792849347 = **2026-10-24 21:42:27**。
- 字节绑定：上述三份 `moyu-real-current*.json` 的 `engine.sha256` 为 `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8`，与当前 `backend/moyu_extract.py` 实测 sha256 **完全一致**（可用 `shasum -a 256 backend/moyu_extract.py` 复核）。
- 时间线：文档定稿 10-25 10:31；证据产生 10-25 16:50–16:56，**晚 6 小时**。
- **请审计**：这批证据是否真实（不是伪造的 JSON）、是否确实由当前字节产生、断言内容是否合理。
- **不要**把这批证据当作"模型质量已验收"——它只证明链路能产出断言，见 1.3。

### 1.2 那批证据里的确定性指标是空判

- `/tmp/moyu-real-current-5.json` → `determinism: {"repeat": 1, "identical": true, "digest": "60ae8769…"}`。
- 实现：`benchmarks/real_corpus/extract.py:177-179` → `"identical": len(set(digests)) == 1`，`repeat` 默认 2（`:113`、`:219`）。
- `repeat=1` 时集合恒为 1 个元素，`identical` **恒为 true，没有信息量**。
- **请审计**：这三份证据是否用了 `--repeat 1`；若是，`identical=true` 不得写入任何交付表述。
- 若要让确定性成立：`--repeat 2`，成本 172 s → ≈344 s。

### 1.3 F1（t53 复审的 medium）已在真实数据上出现实物案例

- `docs/review-t53-2026-10-24.md` F1 原文：证据闸门只锚定"引文 + 区间"，不校验 `subject`/`value`/`attribute` 与引文语义一致。
- 实物（`/tmp/moyu-real-current-5.json` → `chapters[1].audit[0]`）：

  ```
  subject="封肃"  predicate="has_attribute"  attribute="real_name"
  value="封"      evidence_quote="小人姓封"   char_start=67  char_end=71
  ```

- 引文逐字存在、区间精确（67+4=71）、坐标校验通过；但"小人"是封肃自称，`value="封"` 把姓氏截成了一个字，且这层 subject 归属在原文中没有直接语句支撑。
- **请审计**：这类断言进入 `assertion` 表后，对一致性引擎会产生什么后果（尤其与既有 `real_name` 断言比对时）。
- 这条不是要求你立刻改成 LLM 语义校验（成本高），而是要求：**F1 的边界要么在文档里写死"不做语义校验"，要么在写入端加一道可控的成本闸**。二选一，并写明选了哪个。

---

## 2. 两处指标不可用

### 2.1 234 万字"零误报"只覆盖干净输入

- `docs/real-corpus-acceptance-2026-10-24.md` §0：三部公版、基线误报 0/0/0、注入矛盾 83/83。
- 该验收的抽取器是**确定性规则**（`benchmarks/real_corpus/extract_rules.py`），只抽能确定主语的事实——**输入天然干净**。
- 因此这个 0 证明的是"引擎对干净断言不误报"，**没有回答** P0-2 原本要的问题：真实脏输入（LLM 抽取噪音）下误报多少。
- 现在 LLM 链路已通（§1.1），这个缺口**第一次可以被填上**：用 `/tmp/moyu-real-current-5.json` 的 23 条断言入库跑一致性，人工判定每条 finding 是否误报。

### 2.2 真实模型幻觉率已有第一个数，但没进任何文档

- 5 章：候选 23+5 = 28 条，通过 23 → 拒 5（`empty_value`），本轮拒绝率 17.9%。
- 另 2 章（`-180` 那轮）：17 通过 + 14 拒（8 `quote_mismatch` + 6 `empty_value`），拒绝率 45.2%。
- 两轮口径不同（一次 5 章一次 2 章，一次带 `--repeat`），**不能直接合并**。要报数必须先固定口径。
- 单章均值 34.4 s，360 章单线程 ≈ 3.4 小时——这是产品可用性数据，也要进文档。

---

## 3. 一件遗留指标（判断：真未完成，非跑偏）

- Q1 硬指标：3000 字章一次通过率 **7/9 = 77.8%**，目标 ≥80%。
- `docs/consistency-budget-v1.2-2026-10-23.md` §6 已论证：在"模型不服从提示"的假设下 ≥80% 不可达，属指标定义缺陷。
- 但 headline 仍记 77.8%、分母仍是 9（含 `sc-19` 预算中断、`sc-20` 作者取消两个外部原因场景）。
- **请审计**：口径到底改不改。二选一并给出理由——(a) 分母剔除 sc-19/sc-20 后重算并保留原数字为对照；(b) 补 `sc-15` 超冲（volatile 档单轮爆字，需要"溢出不可回收"的显式失败）。**不要**两个都不做而继续挂着"未达成"。

---

## 4. 修复顺序（按 ROI，建议照此执行）

| # | 动作 | 成本 | 验收 |
| --- | --- | --- | --- |
| 1 | 修 `docs/real-corpus-extract-2026-10-24.md` §6.4：补入 §1.1 三份证据 + 21:42 冒烟，明确 `repeat=1` 使 `identical` 无效 | 10 分钟 | 文档不再出现"外部真实模型没有跑"这类与 `/tmp` 冲突的表述 |
| 2 | 用已通的链路跑 P0-2 真正要的东西：23 条真实 LLM 断言入库 → 一致性 `analyze()` → 逐条人工判定误报 | 半天 | 产出 `docs/consistency-realworld-2026-XX-XX.md`，含逐条判定与误报率；**这是 P0-2 唯一没被回答的部分** |
| 3 | Q1 口径二选一（§3） | 1 小时 | 数字与口径一致，`q1_target_met` 有明确解释 |
| 4 | 提交当前工作树（12 改 + 15 未跟踪） | 10 分钟 | `git status` 干净；两个 commit 分开：性能/去重 与 抽取器/检索 |

---

## 5. 一条给审计方的提醒（我自己犯过的错）

我上一轮判断"八轮复审花在一条从未跑通的链路上"，依据是 §6.4 那句"没有跑"，**没有查 `/tmp` 里的产物**，结论错了。

如果本工单任何一条与你的核验冲突：以你的核验为准，并在报告里点名本文哪条不成立——包括这一条提醒本身。

---

## 6. 复核命令（全部本机可跑）

```bash
cd /Users/zhaozhen/Downloads/神经网络写作

# 字节绑定
shasum -a 256 backend/moyu_extract.py

# 三份真实模型证据的关键字段
.venv/bin/python -c "
import json
for p in ['/tmp/moyu-real-current.json','/tmp/moyu-real-current-5.json','/tmp/moyu-real-current-180.json']:
    d=json.load(open(p))
    print(p, d['engine']['sha256'][:16], 'limit=',d['corpus']['limit'],
          'assertions=',d['totals']['assertions'],
          'rejected=',d['totals']['rejected_reasons'],
          'determinism=',d['determinism'])
"

# 第一条实物断言的坐标复核
.venv/bin/python -c "
import json
d=json.load(open('/tmp/moyu-real-current-5.json'))
a=d['chapters'][1]['audit'][0]
print(a)
print('len(quote)=',len(a['evidence_quote']),'end-start=',a['char_end']-a['char_start'])
"

# Q1 指标
.venv/bin/python -c "
import json;d=json.load(open('benchmarks/budgeted/results/budgeted-v1.2-baseline.json'))
print(json.dumps(d['summary']['by_target'],ensure_ascii=False))"
```
