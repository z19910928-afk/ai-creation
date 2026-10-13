# 文学协议烟雾试评 v14（离线准备）

状态：仅生成并校验冻结方案，未调用外部模型接口，未读取或保存密钥。
核查日期：2026-10-14（Asia/Shanghai）。本轮只做桌面端；不做手机端。

## 目的

这不是文学质量排名，也不能证明“第一梯队”。它只检验一个明确变量：在与 v13 相同的故事、提示、温度和输出预算下，双方候选是否都能在 `thinking.type=disabled`、`stream=false` 下返回完整、无明显规划污染且满足 600–850 字协议。

本轮与 v13 使用新的方案文件和新的独立运行身份（运行身份只有首次 `generate` 冻结时才产生）。不得复制 v13 的 `run.json`、`frozen-writer-requests.json`、账本或候选；不得重发 v13 的请求身份。

## 冻结范围

- 题目：仅 `mystery-tide`；基线与墨屿各 1 次，共最多 2 条新请求。
- 写作模型：由执行时环境提供的 `MOYU_AI_MODEL`；不要在方案中写入密钥。
- 参数：`temperature=0.65`、`thinking={"type":"disabled"}`、非流式、`max_tokens=2000`。
- 候选门：非空白字符 600–850；协议必须完整、结束原因为 `stop`、`safe_to_apply=true`；不能有明显规划/分析污染；思考字段计数必须为 0。
- 预算：本轮最多 2 条、已报告用量阈值 12,000 tokens；全局 v13 累计 37 条/73,118 可信完整 tokens 和 10 条缺完整用量仍然有效，换目录不重置。费用未知，不把未知计为零。

## 执行前免费检查

```sh
cd "/Users/zhaozhen/Documents/ChatGPT/神经网络写作"
python3 -m json.tool docs/audits/2026-10-14/literary-pilot-v14-probe/plan.json >/dev/null
python3 - <<'PY'
import json
from pathlib import Path
p = Path("docs/audits/2026-10-14/literary-pilot-v14-probe/plan.json")
data = json.loads(p.read_text(encoding="utf-8"))
assert len(data["cases"]) == 1
assert data["budget"]["max_requests"] == 2
assert data["writer_parameters"] == {"temperature": 0.65, "thinking": {"type": "disabled"}}
assert data["writer_stream"] is False
print("offline plan checks: ok")
PY
```

上述命令不读取密钥、不联网、不预留请求。

## 真实执行顺序（只在明确授权后）

不要把密钥写进命令行、文件、日志或提交。由已有受控运行环境提供 `MOYU_AI_BASE_URL`、`MOYU_AI_MODEL`、`MOYU_AI_API_KEY`；执行前先确认环境变量已经由安全凭据管理设置，不能在终端回显。

```sh
cd "/Users/zhaozhen/Documents/ChatGPT/神经网络写作"
python3 scripts/literary_evaluation.py generate \
  --plan docs/audits/2026-10-14/literary-pilot-v14-probe/plan.json \
  --output-dir docs/audits/2026-10-14/literary-pilot-v14-probe \
  --max-new-requests 1 \
  --execute-paid
```

第一条只会先发送 `baseline`。只有它完整结束、返回模型可记录、用量完整可信、思考字段为空、正文 600–850 字且无规划污染，才允许继续同一目录的第二次调用：

```sh
python3 scripts/literary_evaluation.py generate \
  --plan docs/audits/2026-10-14/literary-pilot-v14-probe/plan.json \
  --output-dir docs/audits/2026-10-14/literary-pilot-v14-probe \
  --max-new-requests 1 \
  --execute-paid
```

如果第一条有任何未知费用、网络/HTTP错误、非 `stop`、正文为空或超出字数、非空思考字段、明显规划污染，立即停止，不执行第二次、不重试、不截短、不进入 `blind` 或 `judge`。第二条也必须经过同一闸门；两条都通过时，仅说明这次固定输入的协议烟雾通过，不说明文学质量。

## 离线收尾

只有在不发送盲评的前提下，可运行：

```sh
python3 scripts/literary_evaluation.py blind \
  --plan docs/audits/2026-10-14/literary-pilot-v14-probe/plan.json \
  --output-dir docs/audits/2026-10-14/literary-pilot-v14-probe
python3 scripts/literary_evaluation.py report \
  --plan docs/audits/2026-10-14/literary-pilot-v14-probe/plan.json \
  --output-dir docs/audits/2026-10-14/literary-pilot-v14-probe
```

`blind` 只会在双方候选都通过机械闸门时生成匿名材料；本轮没有足够独立故事和评审请求，不能把它解释为盲评结果。若任一候选失败，直接运行 `report` 保存失败证据即可。
