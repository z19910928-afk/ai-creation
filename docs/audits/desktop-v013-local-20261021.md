# 0.1.3 桌面版本机验收

日期：2026-10-21。源码基线 `8da5491`，分支 `codex/repository-layout`。本轮只重建本机 Mac Apple 芯片安装包并验收，未修改产品业务代码。

## 构建环境

Python 3.11.17、Node.js 24.21.0、PyInstaller 6.22.3、electron-builder 26.15.3，依赖按锁文件安装。构建前 `node` 与 `npm` 必须同时在 PATH：本机原先只有 Node 运行时而没有 npm，electron-builder 收集依赖时报 `spawn npm ENOENT`。本轮把 npm 12.2.0 装在项目忽略目录 `.audit-cache/tools/npm`，只对该次构建生效，未改动系统 Node 或其他全局配置。

## 本机结果

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 包内真实后台 | 19/19 | 资源与源码一致、受保护健康入口、隔离数据保存与重启、模型库与清单、备份和凭据保护 |
| 打包后的真实窗口 | 10/10 | 正文保存、聊天草稿、三个电脑尺寸、剪贴板、四类下载、关闭与单实例、退出与重开 |
| DMG 安装后运行 | 10/10 | 只读挂载、复制至独立路径、签名完整性、真实窗口、卸载卷及临时文件清理 |

真实窗口的 10 项检查为：隔离启动与渲染进程沙箱、正文保存与常驻聊天草稿、1100/1280/1440 布局、用户手势剪贴板写入、外部导航与弹窗拦截、JSON/Markdown/EPUB/完整备份四类真实下载、未保存关闭取消保留窗口与草稿、单实例复用、退出时停止自有服务、重开恢复正文和聊天草稿。

模型清单使用四种接入组合（主写作与写作总管各验证 Chat 与 Responses），每组读取三个合成模型，共 4 次本机清单请求；未点击连接测试或保存，保存设置前后相同。页面错误、意外外部请求、生成任务均为 0；下载均为合成资料，原剪贴板已恢复。

| 下载 | 字节 | SHA256 |
| --- | --- | --- |
| 原稿 JSON | 2051 | `d88edda5a3690e69f2f4300ee2adc6677f67cd01c2ad89f704dd5789b9004798` |
| Markdown | 2079 | `f6cef90f0f16f8e7151f49a3b891d9d89e60ca158b0677ad7f95aa5f9539acba` |
| EPUB | 3647 | `c35f7e59cdb154e74f9af3106b7c45953cd866c0887e1d6d6e109f98e3b888ee` |
| 完整备份 | 3755 | `7b309768cde9617db5de8ca57374217fde1da7034cd75faff15911208bc30d21` |

## 本机构建文件

应用版本为 `0.1.3`，签名方式为 adhoc，未进行 Apple 公证。

| 文件 | 大小 | SHA256 |
| --- | --- | --- |
| `Moyu-0.1.3-mac-arm64.dmg` | 142M | `ad2e9df1c309f6a9d025309e5b07d71caaf52b46ebb304f5c614f8bc0b3ea2eb` |
| `Moyu-0.1.3-mac-arm64.zip` | 143M | `63d9957ceb8fe502008dfd69c0e2c7630a66a28270a45c7c8147975bc3f47c2a` |

这些校验值仅对应本次本机构建；GitHub 原生构建会重新生成文件，正式下载以发布页随包的校验文件为准。

## 复现命令

```sh
.venv/bin/python scripts/build_desktop.py --node <node 可执行文件>
.venv/bin/python tests/desktop_backend_fixtures.py --backend build/backend/moyu-service/moyu-service
node tests/desktop_smoke.cjs --executable "dist/mac-arm64/墨屿.app/Contents/MacOS/墨屿"
.venv/bin/python tests/macos_installer_smoke.py --dmg dist/Moyu-0.1.3-mac-arm64.dmg --node <node>
```

三段检查各自使用隔离临时作品和本机模拟服务，不连接作者正在运行的服务，也不消费模型额度。

## 验证边界

本记录只证明本次 Mac Apple 芯片本机结果。Intel Mac 与 Windows 安装包未在本机构建或验证，正式发布仍需三个平台的原生构建、真实窗口和安装后检查全部通过，并推送与 `package.json` 版本一致的 `v0.1.3` 标签。

未调用真实远程模型，也没有新增文学质量结论；工程验收不代表已获得签名、公证或文学效果达到“第一梯队”。发布状态与下载入口见[桌面安装说明](../desktop-packaging.md)。
