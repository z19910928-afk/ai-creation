# 仓库首页目录整理验收

日期：2026-10-18。范围：缩短 GitHub 首页文件列表，同步源码启动、开发检查与桌面打包路径。

## 目录变化

按 Git 跟踪的首页条目计算，根目录从58项减少到15项。15份页面文件归入 `frontend/`，27份服务实现归入 `backend/`，桌面构建配置归入 `desktop/`，更新记录归入 `docs/`，安全说明归入 `.github/`。删除没有实现内容的 `src/.gitkeep`。

根目录保留 `server.py`，仍可运行 `python server.py`。旧导入与新的 `backend.server` 指向同一个可变模块；页面资源地址、作品数据库与密钥保存位置保持不变。页面文件与整理前逐字一致。

桌面服务入口改名为 `desktop/service_entry.py`，避免 PyInstaller 将原来的 `desktop/backend.py` 误认成新的 `backend` 包。重建后的分析清单包含全部28份后台包文件。

## 本机检查

| 检查范围 | 结果 |
| --- | --- |
| 全部 Python 功能检查脚本 | 84/84成功，分为两个42份的并行组 |
| 真实浏览器交互 | 22套、321/321场景通过 |
| 编辑器合并 | 12/12通过 |
| 桌面运行边界 | 5/5通过 |
| 源码目录与启动兼容 | 2/2通过，包含从其他工作目录启动和静态资源内容核对 |
| Python / JavaScript 语法 | 122份 / 37份通过 |
| 固定工程评测 | 6/6通过，仅使用本机测试材料 |
| Mac Apple Silicon 包内服务 | 重建后19/19通过 |
| 桌面入口改名后的源码后台与静态缓存 | 19/19、1/1通过 |
| Mac Apple Silicon 真实包内窗口 | 10/10通过，页面错误与意外外部请求均为0 |
| 当前文档的本地链接 | 194/194有效 |

全量业务与浏览器检查完成后，对桌面入口重命名单独补做源码后台、静态缓存与真实包内窗口检查，避免将旧入口的结果混作新入口验收。窗口验证包含1100、1280和1440三种电脑宽度、实际编辑、模型列表获取、四种导出、剪贴板恢复、单实例、退出关闭后台，以及重启恢复正文与聊天草稿。

桌面构建下载较慢时，校验本机已有的同版本 Electron 压缩包：完整性检查通过，SHA256与依赖所附的校验值一致。随后仅为本次构建指定本机缓存文件；不改仓库构建配置，不跳过包内服务和窗口验收。最终桌面窗口测试明确使用构建产物，报告 `target=packaged`。

## 数据与范围

本机预览重启前已备份；重启后核对原数据库全部72张表，结构、内容与行数一致。验证使用隔离的临时作品和免费本机模拟服务，没有调用真实模型，没有上传作者作品或私密配置。

初次包内服务检查未通过的原日志保留。定位并修复入口同名冲突后，重新构建和执行验收，不覆盖失败记录。

本轮不修改版本号，不替换既有 v0.1.2 安装包，不重发旧标签。工程通过不代表文学效果达标，也不构成第一梯队结论。

## 本机证据位置

详细日志仅保存在本机忽略上传的 `.audit-cache/` 中：

- `repository-layout-python-even-20261018/result.json`
- `repository-layout-python-odd-20261018/result.json`
- `repository-layout-browser-20261018/corrected-summary.json`
- `repository-layout-independent-audit-20261018/`
- `repository-layout-packaged-backend-repaired-20261018.log`
- `repository-layout-desktop-entry-source-20261018/results.json`
- `repository-layout-desktop-window-20261018/result.json`
- `repository-layout-electron-cached-20261018.log`
- `repository-layout-preview-20261018/after.json`

浏览器调度器原来按 TAP 格式解析桌面边界检查，Node 24 实际输出为默认 spec 格式；原始日志的退出码为0且5项通过。修正后的汇总保留原件与说明，没有重跑或隐藏失败场景。
