# 墨屿桌面安装包

桌面版把创作页面和本机服务一起装进应用，使用者无需另外安装 Python、Node.js 或启动命令。创作与写作总管保持在同一个主窗口，不包含手机端。

本文说明打包方案及验证要求。三个平台是否实际通过，以相应本机记录或 GitHub 对应提交的运行结果为准；配置完成不代表安装包已经通过验证或发布。

2026-10-17 本机 Mac Apple 芯片版本已完成真实应用、DMG复制安装和模型库操作验证，完整产品回归136项、269个电脑页面场景全部通过，见[本轮验收说明](audits/2026-10-14/comprehensive/desktop-model-library-20261017.md)。这份本机记录不代表 Intel Mac、Windows 或 GitHub 发布已经通过；各平台以对应提交的原生运行结果为准。

首次GitHub桌面运行暴露跨平台兼容问题后，修正了测试启动等待、空剪贴板条目，以及Windows备份临时数据库的关闭顺序。[新版本机重包验收](audits/2026-10-14/comprehensive/evidence/desktop-native-fix-local-20261017.json)已通过内置服务16项、真实窗口10项、DMG安装后10项与运行边界5项。后续原生构建以修复后的对应提交为准。

后续`86e39e5`产品检查与两种Mac原生检查通过，Windows再次定位到临时备份快照只读句柄无法同步落盘的问题。可写句柄修复后的第一次本机窗口检查受锁屏阻止，失败记录保留。2026-10-17解锁后，最新源码重新构建并通过运行边界5项、后台17项、真实窗口10项和DMG复制安装后10项；164份源码前后相同，剪贴板恢复成功，外部请求与模型调用均为0。具体状态见[跨平台后续复验](audits/2026-10-14/comprehensive/desktop-windows-native-followup-20261017.md)及[解锁后新包记录](audits/2026-10-14/comprehensive/evidence/desktop-final-unlocked-local-20261017.json)。Intel Mac、Windows和正式发布仍以同提交的GitHub原生检查及发布页为准。

## 选择安装包

| 电脑 | 安装文件 | 备用压缩包 |
| --- | --- | --- |
| Mac，Apple 芯片 | `Moyu-0.1.0-mac-arm64.dmg` | `Moyu-0.1.0-mac-arm64.zip` |
| Mac，Intel 芯片 | `Moyu-0.1.0-mac-x64.dmg` | `Moyu-0.1.0-mac-x64.zip` |
| Windows，64 位 Intel 或 AMD | `Moyu-0.1.0-win-x64.exe` | `Moyu-0.1.0-win-x64.zip` |

Mac 需要 macOS 15 或更新版本，可在左上角苹果菜单的“关于本机”查看系统版本和芯片名称。打开 DMG 后将“墨屿”拖入“应用程序”；Windows 打开 EXE，按安装窗口完成安装。Windows 安装包可选择安装目录，默认只为当前用户安装。

当前版本没有发行者证书，Mac 仅使用本机临时签名检查应用完整性，没有进行 Apple 公证。系统可能显示无法验证开发者或未知发布者；这与通过工程测试是两件不同的事。请核对下载来源及随包 `SHA256SUMS-*.txt`，不要把系统提示当作已经完成安全审核的证明。Mac 首次打开受阻时，可在确认来源后从“系统设置 → 隐私与安全性”使用系统提供的“仍要打开”；Windows 可查看系统提示中的发行者与文件信息。受单位设备策略限制时，应遵循单位规则。

## 作品与连接

安装包仅包含程序、页面资源和运行所需依赖。作品数据库、个人原稿、已有聊天、模型地址和密钥不会从开发电脑复制到安装包，也不会随源码发布。首次启动创建独立的桌面数据目录，不自动导入浏览器版或其他本机服务的作品；已有作品请在原版导出完整备份，再在桌面版恢复。

可从应用菜单的“打开作品数据文件夹”找到资料。Mac 默认保存到 `~/Library/Application Support/Moyu/`，Windows 默认保存到 `%APPDATA%\Moyu\`，数据库文件名为 `moyu.sqlite3`。浏览器草稿随同桌面应用的页面资料保存在这个独立目录，升级程序应保留此目录。

首次使用时，打开“模型与风格”，在模型库填写地址、密钥和模型名称，先“测试连接”，通过后再“保存模型”。然后在“用途与风格”选择默认模型和各用途模型，可新选的连接仅包含已测试保存的有效连接，失效的原分配仍保留并显示提示；修改连接后须重新测试。保存连接或用途不调用模型，点击测试会调用所填服务并可能产生费用。恢复备份会保留连接名称、地址和模型名，密钥和有效验证状态须在这台电脑重新建立。

已分配的连接失效时，软件保留原分配并显示失效提示，不会自动改用默认模型。此时先重新测试保存，或主动重选、清除失效分配，再保存用途与文风；保存失败会保留原配置。

同页的“写作总管连接”默认沿用所选默认模型，也可独立填写对话连接。独立连接仅用于聊天，续写、润色、规划、记忆和审稿使用各自用途选择的模型。桌面版不读取开发电脑的模型环境变量。

软件不会自带可消费的密钥，也不会在安装检查中调用收费模型。卸载 Windows 程序时默认保留应用数据；正式删除数据前请先导出完整备份。

## 本机构建

维护者需要 Python 3.11、Node.js 24 和对应目标架构的电脑。Python 负责内置服务，Electron 负责桌面窗口；PyInstaller 将 Python 服务及依赖收进目录，electron-builder 将该目录和窗口程序制作成安装包。Python 构建依赖由 `desktop/requirements-build.txt` 统一读取 `desktop/requirements-lock.txt` 固定直接与间接版本，并按系统选择构建辅助包；窗口依赖由 `package.json` 和 `package-lock.json` 固定。

在项目目录安装依赖后构建，命令应使用项目虚拟环境中的 Python：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r desktop/requirements-build.txt
npm ci
.venv/bin/python scripts/build_desktop.py
```

Windows 使用相同的安装步骤，将 Python 路径换为 `.venv\Scripts\python.exe`：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r desktop/requirements-build.txt
npm ci
.venv\Scripts\python.exe scripts/build_desktop.py
```

脚本用当前解释器执行：

```text
python -m PyInstaller --noconfirm --clean --distpath build/backend --workpath build/pyinstaller desktop/backend.spec
```

内置服务位于 `build/backend/moyu-service/`，安装包位于 `dist/`。默认构建当前电脑架构；`--arch arm64` 或 `--arch x64` 只允许与当前 Python、Node.js 及机器架构一致。Windows 当前只支持 x64。不能在 Mac 上声称已构建并验证 Windows 安装包，也不能用另一架构的旧后台目录跳过构建。

`--backend-only` 只构建服务，`--dir` 构建可直接运行的未封装应用。`--node` 可明确指定 Node.js 可执行文件路径。构建脚本始终传入 `--publish never`，并关闭自动寻找签名证书；本机构建不会上传安装包。

`electron-builder.yml` 只将 `desktop/main.cjs`、`desktop/runtime.cjs` 和构建工具自动保留的 `package.json` 放入窗口程序；内置服务仅从 `build/backend/moyu-service/` 复制至应用资源的 `backend/`。图标来自 `desktop/assets/`。服务包含必要的数据库建表脚本等运行资源，研究文档、检查截图、日志和现有数据不打包；不会将整个项目目录复制进安装包。

## 验证与发布

构建后必须先测试真实内置服务，再打开打包后的真实 Electron 窗口。两者使用独立临时数据，不连接作者正在运行的服务，也不消费模型额度：

```sh
.venv/bin/python tests/desktop_backend_fixtures.py --backend build/backend/moyu-service/moyu-service
node tests/desktop_smoke.cjs --executable "dist/mac-arm64/墨屿.app/Contents/MacOS/墨屿"
```

Mac Intel 的应用路径是 `dist/mac/墨屿.app/Contents/MacOS/墨屿`；Windows 的后台路径是 `build/backend/moyu-service/moyu-service.exe`，应用路径是 `dist/win-unpacked/墨屿.exe`。后台测试检查受保护的健康入口、页面资源、隔离数据保存与重启；桌面测试检查真实窗口和内置服务协作。具体覆盖与失败信息以测试实际结果为准。

GitHub 的“墨屿桌面安装包”任务可手动运行，也会在 `codex/desktop-installers` 分支更新时运行。三个独立环境分别使用 `macos-15` 的 arm64、`macos-15-intel` 的 x64 和 `windows-latest` 的 x64，均安装 Python 3.11 与 Node.js 24，按锁文件安装依赖、原生构建并执行上述验证。每个平台验证成功后仅上传自己的安装包、压缩包和 SHA256 校验文件，不上传私有截图、日志或数据库。

普通分支构建和手动运行只生成 GitHub 构建附件。推送与 `package.json` 版本严格一致的 `v*` 标签才允许发布正式下载，例如版本 `0.1.0` 对应 `v0.1.0`；必须三个平台全部通过，再重新核对六个安装文件、三个校验文件及全部 SHA256，才创建 GitHub Release。工程验证不等于已获得签名、公证或真实文学质量评测通过。

## 官方依据

- [electron-builder 26 文件与资源配置](https://www.electron.build/v26/docs/contents/)
- [electron-builder 26 构建命令](https://www.electron.build/v26/docs/cli/)
- [electron-builder 26 Mac 签名说明](https://www.electron.build/v26/docs/features/code-signing/code-signing-mac/)
- [PyInstaller 使用与平台限制](https://pyinstaller.org/en/stable/usage.html)
- [GitHub 托管运行环境](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
