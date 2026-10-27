# 真实模型抽取复跑记录（2026-10-27）

> **结论：真实请求链路已跑通，但不是质量通过。** 本机用 `zzniu` / `deepseek-v4.1-flash`
> 对《红楼梦》前 5 章跑生产抽取 CLI：**5 章 / 33 120 字 / 43 条断言 / 4 条 rejected /
> 0 条 provider_error**；默认 `--repeat 2` 下两次结果 **`identical=false`**。
> 这证明真实请求进入模型并逐条记账，不证明召回率、语义幻觉率或确定性，也不构成发布放行。
> 本报告由 captain 在本机执行，**不是独立 verifier 验收**。

## 1. 应用模型连接

- 应用数据库：`~/Library/Application Support/Moyu/moyu.sqlite3`，项目 `default-project`。
- 通过 `backend/moyu_settings.py` 的 `save_settings()` 写入：`base_url=https://api.icodexs.com/v1`、
  `model=deepseek-v4.1-flash`；API key 只写入 `~/.moyu/credentials`（目录权限 `0700`），
  **不打印、不复述、不提交、不写入本报告**。
- `probe_settings()` 结果：`success=true`、`returned_model=deepseek-v4.1-flash`、
  `protocol_complete=true`、`finish_reason=stop`、`configuration_stale=false`、
  `latency_ms=4135`。该 probe 只比较提供方回传名称，名称相同不能证明底层模型身份。

## 2. 复跑命令

只把凭据放进子进程环境变量，命令本身不包含 key：

```bash
export MOYU_EXTRACT_BASE_URL=https://api.icodexs.com/v1
export MOYU_EXTRACT_MODEL=deepseek-v4.1-flash
export MOYU_EXTRACT_API_KEY=...          # 仅进程环境，不落盘
export MOYU_EXTRACT_TIMEOUT=180

.venv/bin/python benchmarks/real_corpus/extract.py \
  --mode openai --limit 5 --repeat 2 \
  --json /tmp/moyu-real-current-2026-10-27.json
```

- 引擎：`backend/moyu_extract.py`，**1041 行**，sha256
  `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8`。
- 产物：`/tmp/moyu-real-current-2026-10-27.json`，**291 行**，sha256
  `ddc05a219734515883c4762b49c877db6b433d035d6629cbe8e615697e4e37ce`。
- 进程 `exit 0`，`mode=openai`，没有回落到规则模式。

## 3. 结果

| 指标 | 观测值 |
| --- | --- |
| 章数 / 字数 | 5 / 33 120 |
| 接受断言 | 43 |
| rejected | 4（`empty_value` 1、`quote_mismatch` 3） |
| `provider_error` | 0 |
| 默认每章 audit 可见 | 10 / 43 |
| 确定性 | `repeat=2`、`identical=false`、digest 前缀 `9c09f6160875e634d35…` |
| 耗时 | `extract=218 196.091 ms`、`total=512 877.771 ms` |
| 每章耗时 | 均值 `43 639 ms`、最大 `112 307 ms` |

逐章明细：

| 章 | 字数 | 断言 | rejected | 可见 audit | 耗时 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 红楼梦/001 | 6 774 | 1 | 3 | 1 | 28 083.5 |
| 红楼梦/002 | 5 689 | 2 | 0 | 2 | 21 405.0 |
| 红楼梦/003 | 7 922 | 1 | 1 | 1 | 14 977.0 |
| 红楼梦/004 | 5 380 | 9 | 0 | 3 | 41 424.1 |
| 红楼梦/005 | 7 355 | 30 | 0 | 3 | 112 306.5 |
| **合计** | **33 120** | **43** | **4** | **10** | **218 196.1** |

拒绝原因分布：`quote_mismatch=3`（第 1 章）、`empty_value=1`（第 3 章）。默认 `--audit-limit=3`
只把每章前 3 条证据写入 JSON，因此 43 条接受断言中只有 10 条逐字证据可见；其余 33 条没有落盘证据，
不能声称“43 条已逐条审计”。

## 4. 边界

1. **不是质量验收**：43 条只是被 schema 与逐字证据闸门接受的断言数；语义正确性、召回率、幻觉率未验收。
2. **不是确定性验收**：`repeat=2` 已得到 `identical=false`；两次输出不同，不能把单轮结果当作可复现基准，
   也不能据此外推 360 章。
3. **旧轮保留**：2026-10-25 的 5 章 23 条 / 11 条可见、2 章 17 条等数字仍是历史证据；本轮与其参数、
   分母都不同，不能合并拒绝率。
4. **Q1 不变**：续写 3000 字仍是 `7/9=77.8%`、`q1_target_met=false`；本报告不改变 Q1。
5. **发布不变**：仍保持 `0.1.3`、不打 tag、不发布；本报告不是独立验收，也不关闭 B4。

## 5. 自证

- 本报告只新增文档，不改代码、测试或应用逻辑。
- API key 未出现在本报告、命令、Git diff 或日志中；`~/.moyu/credentials` 是应用自己的私有凭据文件。
- 本报告不记录评分，也不创建 tag 或发布。
