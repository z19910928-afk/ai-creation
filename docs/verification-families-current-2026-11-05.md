# server / consistency / extract 当前字节重验 — 2026-11-05

状态：**仅覆盖这三族的当前测试字节**，不是全仓库发布放行，也不把旧 verdict 平移成当前通过。

## 1. 绑定字节

| 文件 | 行数 | sha256 |
| --- | ---: | --- |
| `backend/server.py` | 8198 | `f74c885f3a058c2b82b19907705dc48b2971403e20d71373a9acd78c2403b263` |
| `backend/moyu_consistency.py` | 1222 | `b855e9106e1d2b8a144231310bb860752c07cbab1b933c272cba4e5ded463cd2` |
| `backend/moyu_extract.py` | 1041 | `6b5d431364057b5a11441b882429993a40036e76e8082f43868baa836126cff8` |
| `backend/moyu_context.py` | 1258 | `9f5a4bc60e42af78ac1d898f68f6a96fe7ad08dc38e5ad7607d69dcb1205df87` |
| `tests/server_bind_fixtures.py` | 32 | `92ab9535d83f179e15b2935079382d8628240a00d2e0d521ab2cc5d62be75292` |
| `tests/ai_contract_fixtures.py` | 117 | `88c1982e2d67c1af7d7a0e6e28c02f9adcd9b54149e7bf8541e9e1731034ae5c` |
| `tests/context_http_fixtures.py` | 435 | `c7a9477a06190ff8d13977009220e0e3c50ed96550ab3edff5dcaf314ce7c140` |
| `tests/consistency_fixtures.py` | 640 | `043b6ac5da07f69e61cd5d8c09f735b5990c7fab7e1af98b4c3b8075549b8f7a` |
| `tests/consistency_http_fixtures.py` | 199 | `86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a` |
| `tests/extract_fixtures.py` | 1372 | `f48b4f191d7fa390962539dd69c1a49453354351c3615350bf2246db1f2f71b5` |

## 2. 命令与结果

```text
.venv/bin/python tests/server_bind_fixtures.py
# Ran 4 tests in 0.000s
# OK

.venv/bin/python tests/ai_contract_fixtures.py
# AI contract fixtures: ok

.venv/bin/python tests/context_http_fixtures.py
# passed: True
# results: 3 (30/100/300 chapter scales)
# workflow_checks: 3/3 passed

.venv/bin/python tests/consistency_fixtures.py
# consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}

.venv/bin/python tests/consistency_http_fixtures.py
# Ran 6 tests in 3.386s
# OK

.venv/bin/python tests/extract_fixtures.py
# extract fixtures: OK {'cases': 67}

.venv/bin/python scripts/verify_manifest.py
# current bytes manifest: ok

.venv/bin/python tests/current_bytes_fixtures.py
# current bytes fixtures: 5 checks ok (22 files)
```

## 3. 结论

- `server`：loopback 绑定策略 4/4、AI 契约、context HTTP 的 3 个规模档和 3 个 workflow 检查均通过。
- `consistency`：确定性 fixtures 通过（dirty 13.0 / clean 100.0），HTTP 6/6 通过；这是规则链路，不是 AI 抽取。
- `extract`：离线 fixtures 67 cases 通过；没有在本轮调用真实模型，不能据此宣称真实模型抽取质量。
- 22 个当前输入 manifest 与 `tests/current_bytes_fixtures.py` 仍通过，说明本轮报告没有引入字节漂移。

## 4. 与旧 verdict 的关系

- 本报告是**针对当前字节的新验收**，不是复用旧 `t18` / `t27` / `t54` 等 verdict。
- 旧任务状态不变：`t79` failed、`t88` failed、`t91`/`t96` 未完成；旧 pass 仍不能在未逐字匹配的输入上复用。
- 台账中的 `stale` 判定不因本报告删除；它只增加“当前族重验通过”的可用证据。

## 5. 未覆盖

- 没有跑真实三书一致性语料、真实模型 3000 字 Q1 或 desktop GUI；浏览器全量回归由 CI `42009192670` 覆盖（见 §6），但它不是 desktop 原生焦点。
- 没有修改样本、评分、provider、默认参数或中断语义。
- `q1_capable_all` 仍是 **8/9=88.9%**，旧 Q1 仍是 **7/9=77.8%**，版本仍 0.1.3 / 不发布。

## 6. 浏览器 CI 交叉证据（不是 desktop）

| 项目 | 值 |
| --- | --- |
| 提交 | `9e027c5` 本地 / `9a7ba9e` GitHub；父提交 `85e4f1b` 的代码树相同，本提交只改文档 |
| CI | `42009192670` · completed / success |
| 浏览器步骤 | 24/24 browser/editor 步骤 success（含 real browser、author flow、reference passage、prompt workshop） |
| 前端字节 | `frontend/app.js` 1906 行 / `e22c79e85754…`；`frontend/styles.css` 550 行 / `fb2b661f48f8…` |
| 边界 | 不覆盖 desktop 原生窗口焦点；`t88` 历史失败、`t101`/`t102` 同机验收口径不变，不能据此宣称 26/26 |

CI 只证明该提交上的 Web 浏览器回归全绿；它不是发布放行，也不把 `t79`/`t88`/`t91`/`t96` 改成通过。
