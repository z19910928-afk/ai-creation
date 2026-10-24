# 贡献指南

感谢参与墨屿。这份文档说明本地开发、测试与提交约定；一致性引擎的规则细节见[一致性引擎文档](docs/consistency-engine.md)，产品与接口说明见[使用与开发参考](docs/reference-guide.md)。

## 开发环境

- Python 3.11（推荐；运行源码版所需的环境，依赖见 `requirements.txt`）
- Node.js 24（仅用于开发验证和桌面构建，不是运行产品的依赖）
- macOS、Windows 或 Linux

首次准备 Python 环境：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell 使用 `.venv\Scripts\python.exe`：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 起本地服务

在项目根目录运行：

```bash
.venv/bin/python server.py
```

默认监听 `http://127.0.0.1:4173`，用浏览器打开即可。服务首次启动会在项目目录创建 `moyu.sqlite3`，并按顺序应用 `docs/schema-v1.sql` 与 `docs/schema-v1.1-migration.sql`。

可用环境变量：

| 变量 | 作用 |
| --- | --- |
| `MOYU_PORT` | 修改监听端口，默认 `4173` |
| `MOYU_DB` | 指定 SQLite 数据库文件，默认项目目录的 `moyu.sqlite3` |
| `MOYU_CREDENTIALS_DIR` | 指定模型密钥等凭据目录 |

示例：

```bash
MOYU_PORT=4174 MOYU_DB=/tmp/moyu-dev.sqlite3 .venv/bin/python server.py
```

服务只监听 `127.0.0.1`，面向单人本机使用；没有登录、用户权限或公网部署保护。仅做页面静态预览时可以运行 `python3 -m http.server 4173 --directory frontend`，但书架、保存和一致性检查等本机接口都不可用。

## 跑 fixture 测试

`tests/*_fixtures.py` 是相互独立的可执行脚本，**逐个执行**，任一失败即停止：

```bash
for fixture in tests/*_fixtures.py; do
  echo "== $fixture"
  .venv/bin/python "$fixture" || exit 1
done
```

Windows PowerShell：

```powershell
Get-ChildItem tests/*_fixtures.py | ForEach-Object {
  Write-Host "== $_"
  .venv\Scripts\python.exe $_.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

一致性引擎的两个专项：

```bash
.venv/bin/python tests/consistency_fixtures.py
.venv/bin/python tests/consistency_http_fixtures.py
```

fixture 使用内存或临时 SQLite、免费本机模拟提供方，不调用真实模型、不需要密钥；不要拿真实作品数据库跑测试。

## 跑浏览器回归

浏览器回归使用 Playwright，脚本位于 `tests/*.cjs`。首次准备：

```bash
npm install
npx playwright install chromium
```

单个脚本直接执行，例如：

```bash
MOYU_TEST_PYTHON="$PWD/.venv/bin/python" node tests/frontend_browser_regressions.cjs
```

全部逐个执行：

```bash
for test in tests/*.cjs; do
  echo "== $test"
  node "$test" || exit 1
done
```

说明：

- 大多数 `tests/*_browser_regressions.cjs` 会自行启动隔离的本机服务、临时 SQLite 和免费模拟提供方，不写真实作品库、不调用收费模型。
- `tests/editor_sync_fixtures.cjs` 等专项同样用 `node` 直接运行；`tests/desktop_runtime.test.cjs` 用 `node --test tests/desktop_runtime.test.cjs` 运行；`tests/desktop_smoke.cjs` 需要可用的 Electron 桌面构建产物。
- 可用 `MOYU_PLAYWRIGHT_MODULE` 指定 Playwright 模块路径，`MOYU_TEST_PYTHON` 指定启动服务的 Python 解释器。
- 浏览器回归通过只代表对应脚本覆盖的界面行为，不代表真实模型的文学质量。

## 提交规范

采用 Conventional Commits，一个提交只做一件事：

```text
<type>(<scope>): <简述>

<为什么这样改、验证了什么；可附命令与结果>
```

常用 `type`：

| type | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 只改文档 |
| `test` | 只改测试或 fixture |
| `refactor` | 不改变行为的重构 |
| `perf` | 性能优化 |
| `build` | 构建、打包、依赖 |
| `ci` | 持续集成配置 |
| `chore` | 杂项维护 |

约定：

- 简述使用中文或英文均可，不超过 72 个字符，不以句号结尾。
- 正文写清动机、影响范围和实际执行过的验证命令；没有验证证据的“已完成”不算完成。
- 不要把无关改动混进同一个提交；不要为了通过检查而修改测试、评分脚本或隐藏用例。
- 不提交 `moyu.sqlite3`、`*.sqlite3`、`backups/`、`.venv/`、`node_modules/`、`.env` 和任何真实密钥（`.gitignore` 已覆盖大部分）。

## 数据契约红线

墨屿的正文、证据和故事状态是用户资产，以下契约不可破坏：

1. **迁移只追加。** `docs/schema-v1.sql` 和 `docs/schema-v1.1-migration.sql` 是已发布的数据契约，不修改已应用的迁移；需要变更 schema 时新增迁移文件，并同步提升 `PRAGMA user_version`。
2. **append-only 不可弱化。** `document_revision`、`event_log`、`deletion_event`、`audit_event` 的更新/删除/重复插入触发器（见 `docs/schema-v1.1-migration.sql`）不得删除、绕过或放宽。
3. **正典事实必须有证据和复核人。** `assertion` 进入 `state='canon'` 时必须有非空 `evidence_json` 数组以及 `reviewed_by`、`reviewed_at`；对应触发器是 `check_assertion_canon_insert` / `check_assertion_canon_update`（`docs/schema-v1.1-migration.sql:72-73`）。
4. **接口字段是契约。** 前端、fixture 和桌面端都依赖 `GET/POST/PATCH` 的响应字段名与语义；新增字段可以，改名、删字段或改变语义必须同步前端、测试和 `docs/reference-guide.md`，必要时做版本化。
5. **一致性引擎只读且确定性。** `backend/moyu_consistency.py` 的分析过程不得写数据库、不得修改 projection；同一份数据必须得到相同的 finding id 与分数；`min_severity` 只裁剪返回清单，不得改变 `score`、`counts`、`categories` 或 `examined`。
6. **删除用墓碑或归档。** 章节删除写 `deletion_event`/`app_deleted_node`，资料用归档状态；不做不可恢复的物理删除。
7. **测试是验收的一部分。** 修 bug 先补能复现的 fixture；新行为补对应测试；不得通过删除断言、放宽阈值或跳过用例来“通过”。

## 提交前检查清单

```bash
.venv/bin/python -m py_compile *.py backend/*.py desktop/*.py scripts/*.py tests/*.py
for source in frontend/*.js desktop/*.cjs scripts/*.cjs tests/*.cjs; do
  node --check "$source"
done
for fixture in tests/*_fixtures.py; do
  .venv/bin/python "$fixture" || exit 1
done
```

再按改动范围执行相关浏览器回归（例如改了工作台就跑 `tests/frontend_browser_regressions.cjs`），并在提交正文中记录命令与结果。文档变更至少检查链接和示例命令是否仍然可用。
