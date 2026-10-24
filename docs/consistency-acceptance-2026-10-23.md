# 墨屿一致性引擎 v1 全链路验收（2026-10-23）

验收人：verifier（t5，attempt `e119ccbb-43b8-4275-8416-f079ff235139`）。环境：`.venv` Python + Playwright Chromium；4199=`/tmp/moyu-verify.sqlite3`，4200=`/tmp/moyu-dirty.sqlite3`。

**总 verdict：pass**（核心验收全过；O1/O2 为低风险观察，不阻断）

## 1. 前端面板

```bash
MOYU_DB=/tmp/moyu-verify.sqlite3 MOYU_PORT=4199 .venv/bin/python server.py
MOYU_DB=/tmp/moyu-dirty.sqlite3  MOYU_PORT=4200 .venv/bin/python server.py
ELECTRON_RUN_AS_NODE=1 ./node_modules/electron/dist/Electron.app/Contents/MacOS/Electron /tmp/t5/browser-check.js
```

`browser-check.js` exit **0**，**26 PASS / 1 FAIL**（FAIL 仅为 favicon）：4199 分数 `100`/等级 `A`/七类卡/五个 severity 按钮/空态文案；4200 分数 `92`/等级 `B`/`counts=阻断0、高危1、中危0、低危0`/1 条「home_city 存在冲突取值」；高危筛选请求 `http://127.0.0.1:4200/api/consistency?min_severity=high`，筛选前后分数均 `92`、counts 不变、清单仍 1 条；阻断筛选空态「显示 0 / 共 1 个问题」；离线态分数 `—` +「暂时读不到一致性报告…」；`pageerror=[]`。

原始 console：2 条 `Failed to load resource: the server responded with a status of 404 (static asset not found)`（`/favicon.ico`）+ 1 条离线测试主动断网的 `net::ERR_FAILED`；隔离 favicon 后 4199/4200 均 `consoleErrors=[]`、`pageErrors=[]`。截图：`/tmp/t5/panel-clean-4199.png`、`/tmp/t5/panel-dirty-4200.png`、`/tmp/t5/panel-offline-4199.png`。**结论 pass**。

## 2. 文档与许可证

| 位置 | 证据 | 结论 |
| --- | --- | --- |
| `LICENSE:1` | `MIT License` | MIT |
| `package.json:4,7` | `"private": false`、`"license": "MIT"` | MIT / private=false |
| `package-lock.json:10` | 根包 `license: "MIT"`（lockfile 不记录 `private`，无冲突） | MIT |
| `README.md:15,205` | badge「许可证 MIT」+「[MIT 许可证](LICENSE)」 | MIT |
| `docs/CHANGELOG.md:3` | `## 2026-10-23 · 一致性引擎 v1` | 存在 |

- HTTP 响应 keys：`engine,version,generated_at,project_id,branch_id,score,grade,counts,categories,examined,total_examined,findings,returned,filtered_by,summary`，**无 `all_findings`**；`docs/consistency-engine.md:34,63` 的「HTTP 移除、CLI/analyze 保留」描述正确。
- 门禁：`backend/server.py:3315-3318` 与文档 3315-3318 一致（仅 `fact/knowledge/evidence` 的 `blocker/high`）；无 override 的 `GET /api/export/markdown` 返回 **409**，body 含「一致性阻断：home_city 存在冲突取值…」与 `override_required:true`；带 `X-Moyu-Export-Override: independent acceptance override` 返回 **200**、`Content-Type: text/markdown`。
- 等级封顶：`backend/moyu_consistency.py:982-987`（`blocker→C`、`high→B`）；92 分 + 1 high 实测 `B`。
- CLI：干净库 `--json` exit `0`；脏库 `--fail-on high` exit `1`；脏库 `--min-severity blocker --fail-on high` exit `1`（证明看 `all_findings`）；不存在作品 exit `2`（`一致性检查失败：项目不存在：...`）；数据库不存在 exit `1`（`数据库不存在：...`，非零 `SystemExit`）。

**O1（low，文档行号漂移）**：`docs/consistency-engine.md` 的 engine 行号引用整体 +8，规则/字段本身正确。例：`:47` 写 `996-1013`，实际 `return` 在 `1004-1021`；`:72` 写 `148-170`，`_finding()` 在 `156-178`；`:122` 写 `974-979`，grade 封顶在 `982-987`；`:317` 写 `1106-1115`，`--fail-on` 在 `1114-1124`。原因：P17 在 `_normalize_for_compare` 附近插入 8 行未同步；server 引用（3258-3319、3315-3318、5427-5429、5782-5785）实测正确。建议：批量 +8 或改用符号引用。**结论：规则/字段/许可证 pass；O1 为 low 文档瑕疵。**

## 3. P17 复核

```text
2**53 vs 2**53+1: got=False expected=False -> PASS
10**400 vs 10**400: got=True expected=True -> PASS
1 vs 1.0: got=True expected=True -> PASS
True vs 1: got=False expected=False -> PASS
```

`/tmp/t5/dirty.sqlite3` 的 `p1/b1` 含两条 10**400 级整数断言：`analyze` 返回 `score=5.0 grade=D counts={blocker:0,high:9,medium:7,low:2} findings=18`，其中 `fact/high huge_value 存在冲突取值`，无 `OverflowError`。复制脏库把 `as_1/as_2` 改为 `{"value": true}` / `{"value": 1}` 后 `analyze` 返回 `score=92.0 grade=B counts={blocker:0,high:1,...}`，finding 为 `fact/high home_city 存在冲突取值`。**结论 pass**。

## 4. 补充

`ELECTRON_RUN_AS_NODE=1 ... --check frontend/consistency-panel.js` exit `0`；`frontend/app.js` exit `0`。`GET /api/consistency` 前后对两个库全部 72 张业务表逐表计数，`before == after` 均为 **True**（报告只读）。

## 5. 后端回归与新 hash 基线

| 命令 | exit | 关键输出 |
| --- | ---: | --- |
| `.venv/bin/python tests/consistency_fixtures.py` | 0 | `consistency fixtures: OK {'dirty': 13.0, 'clean': 100.0}` |
| `.venv/bin/python tests/consistency_http_fixtures.py` | 0 | `Ran 6 tests in 4.074s` / `OK` |
| `.venv/bin/python tests/story_review_publication_http_fixtures.py` | 0 | `Ran 11 tests in 6.147s` / `OK` |
| `.venv/bin/python tests/publication_assistant_fixtures.py` | 0 | `Ran 9 tests in 5.585s` / `OK` |
| `.venv/bin/python benchmarks/longform/run.py --chapters 40 --seed 20261023` | 0 | 105,182 汉字；35/35 检出；精确率 100%；干净基线 0 findings；中位 6.72 ms |

```text
7aa174f2e0df7402cd40290a4d7df0603d651ca1a97e9631583f51b7471cf93c  backend/moyu_consistency.py
28066e132c57d12ad35c6132b080e8fe248e11dc9e9cc6b39a3616a6a5e1b575  backend/server.py
28fcc741208c3ad4e47411e72979b9dfdf95911191b7e37b3bed83c50595c1c7  tests/consistency_fixtures.py
86d0ad6d6e009555f256a1b2fb225f8dae576a565fe2929d30f0f61f27501b4a  tests/consistency_http_fixtures.py
05a08274d76dc374328601486ead48e61dd23305968ac27033f57bd0c29e9cea  benchmarks/longform/run.py
```

未覆盖项：390px 视口 topbar 横向溢出（任务声明为既有问题，不计缺陷）；移动端布局/触摸交互未验。

## 发现的问题

| id | severity | 文件:行号 | 问题 / 复现 / 建议 |
| --- | --- | --- | --- |
| O1 | low | `docs/consistency-engine.md:47,72,113,122,144,317` | engine 行号引用整体 +8；`nl -ba` 对照实际行即可复现；批量 +8 或改符号引用 |
| O2 | low | `frontend/index.html:1-15` | 无 favicon link，浏览器自动请求 `/favicon.ico` → 404 console error；隔离 favicon 后 0；建议加 `<link rel="icon" href="data:,">` 或提供 favicon |

**总 verdict：pass**：前端面板、文档规则/字段、许可证、P17、门禁与只读检查均通过；O1/O2 低风险，不阻断本次交付，建议后续修正。
