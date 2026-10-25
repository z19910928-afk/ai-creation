# 当前字节安全审计（2026-10-25）

## 结论

在本轮实际执行的范围内，**没有发现新的 P0 级当前字节漏洞**。历史报告中的 SQL 基线阻塞项已由当前 schema 负向回归验证覆盖；但项目仍是**本地单用户技术预览**，不是多用户生产服务，不能把本报告当成“生产可上线”证明。

本次实际修复了一个部署侧 P1：无认证的 HTTP API 过去可通过 `MOYU_HOST=0.0.0.0` 直接暴露到局域网；现在非回环地址必须显式设置 `MOYU_ALLOW_REMOTE=1`，否则服务启动即拒绝。默认仍为 `127.0.0.1`。

## 范围与方法

- 当前工作树，`backend/server.py`、`backend/moyu_extract.py`、`backend/moyu_consistency.py` 及 schema 回归。
- 只读审计 + 可复现回归；未调用真实模型，未做网络渗透，未验证多用户/多租户。
- 历史结论只作为线索；每条当前结论必须有当前字节的命令或代码证据。

## 已验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| SQL 作用域、append-only、canon 证据、export 确认、迁移账本 | `.venv/bin/python tests/schema_negative_fixtures.py` | `schema regression fixtures passed`，exit 0 |
| 一致性 HTTP 契约与发表门禁 | `.venv/bin/python tests/consistency_http_fixtures.py` | 6 tests OK，exit 0 |
| HTTP 绑定保护 | `.venv/bin/python tests/server_bind_fixtures.py` | 4 tests OK，exit 0 |
| 抽取器脱敏/契约 | `.venv/bin/python tests/extract_fixtures.py` | 当前工作树 fixture 通过（全量 sweep 另行确认） |

关键代码位置：

- `backend/server.py:62` 起：`resolve_host()` 只允许回环地址；非回环必须 `MOYU_ALLOW_REMOTE=1`。
- `backend/server.py:6219`：Origin/Referer 校验仍存在。
- `backend/server.py:8167`：实际绑定使用经过校验的 `HOST`。
- schema 迁移中的 `check_export_confirmation`、`check_*_scope`、append-only 触发器由 `tests/schema_negative_fixtures.py` 当前字节验证。

## 发现

### S1（已修复，P1）无认证 API 可被显式绑定到非回环地址

- 原行为：`HOST = os.environ.get("MOYU_HOST", "127.0.0.1")`；设置 `MOYU_HOST=0.0.0.0` 即把无认证 API 暴露到局域网。
- 影响：同网段任何进程可读写本地项目接口；没有登录/令牌层。
- 修复：`resolve_host()` 默认只允许 `127.0.0.1`、`localhost`、`::1`；要远程绑定必须显式 `MOYU_ALLOW_REMOTE=1`。已经在 `tests/server_bind_fixtures.py` 覆盖默认、空值、拒绝和显式放行四条路径。
- 残余风险：显式设置 `MOYU_ALLOW_REMOTE=1` 仍是无认证暴露；远程部署必须另加反向代理鉴权或 VPN，不能只靠该开关。

### S2（接受，P2）本地单用户信任模型

- 当前 HTTP API 没有用户登录；项目隔离靠 `X-Moyu-Project` 等头，不是安全边界。
- 这是桌面单用户产品的既定假设；如果未来做多人/云服务，必须先引入认证与租户隔离，不能直接暴露当前 server。

### S3（接受，P2）密钥只在环境/进程边界

- 抽取器只从 `MOYU_EXTRACT_BASE_URL`、`MOYU_EXTRACT_MODEL`、`MOYU_EXTRACT_API_KEY` 读取；HTTP/content 错误详情先脱敏后截断，通用异常只保留类型。
- 残余：shell history、进程环境、宿主机日志不属于应用可完全控制的边界；发布/截图/日志时不得打印环境变量。

### S4（文档边界，非代码漏洞）历史 P0 与产品完成度

- 历史独立报告中的 append-only、跨 scope、迁移、canon/export 阻塞项，当前 schema 负向回归已覆盖并通过；这是“SQL 基线”结论，不等于产品状态机全部完成。
- 当前应用仍是本地 SQLite + 本地文件工作流；没有多用户认证、没有生产级审计日志、没有外部密钥托管。发布说明必须保留这个边界。

## 未覆盖

- 真实模型/外部网关的网络安全与凭据轮换。
- 操作系统级密钥链、进程注入、恶意本地用户。
- 多用户、多租户、公网部署与 DDoS。
- 浏览器供应链与第三方依赖 CVE（本轮只跑现有回归，未做依赖审计）。

## 发布建议

- 本地单用户技术预览：可以继续；默认回环绑定已加固。
- 任何公网/局域网多人使用：**不放行**。先完成认证、租户隔离、审计日志和远程绑定威胁建模。
