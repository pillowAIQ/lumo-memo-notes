# Windows 轻量化打包

## 一条命令

在项目根目录运行 `build_exe.bat`。需要 uv 和已配置好的项目虚拟环境 `.venv`；脚本使用项目 `.venv` 并从 `requirements-build.txt` 安装固定版本。
解释器统一使用 AGENTS.md 指定的 `.venv/Scripts/python.exe`。构建环境仅安装 PySide6-Essentials，不安装 Qt Addons、WebEngine、PDF 扩展。

运行前从系统托盘退出轻笺（Lumo）；构建流程不会强制结束正在使用的程序。

## 产物

- `dist/Lumo/Lumo.exe`：唯一正式版本；整个 `Lumo` 文件夹必须一起分发。
- `build/Lumo-release.zip`：相同版本的压缩分发包；解压后运行。
- `build/release-report.json`：文件体积、主要组件和检查结果。
- `build/package-verification.json`：最终 EXE 的隔离验证结果。

构建先在 `build/release-stage` 生成候选包，通过验证后归并到 `dist/Lumo`，旧正式版本移入回收站。不会更改真实数据库、同步配置和账号凭据。
应用更名为轻笺（Lumo）后，旧版 `dist/Lumo` 不会被自动删除，确认新版可用后可手动移除；数据目录与设置保持不变。
需要在应用运行时先检查候选包，可运行 `.venv/Scripts/python.exe build_release.py --stage-only`；退出应用后用 `--publish-only` 再次验证候选包并发布。

## 体积控制

`memo.spec` 是唯一 PyInstaller 配置入口，不再手动收集整个 Qt plugins 目录。
Qt hooks 根据实际导入收集依赖；`prune_dist.py` 在收集前过滤未使用的 Qt DLL 和插件，并对最终目录做只读审计。

保留 Qt Core、Gui、Widgets、Network、Svg，以及 Windows 平台、SVG 和可用的 Windows 样式插件。
排除 WebEngine、QML/Quick、多媒体、3D、PDF、未使用的翻译及纯 Widgets 绘制无需使用的软件 OpenGL 库。以后引入图片格式或新增 Qt 模块时，应同步更新白名单和验证流程。
运行时需要的 Python、SQLite、SSL、加密及凭据后端仍保留，原生库的传递依赖由 PyInstaller 解析。

构建子进程使用干净 PATH，避免从 Anaconda 等开发环境误带 DLL。
不使用 UPX；目录模式无需每次启动解压整个运行时。

## 验证

发布前自动运行：

1. 全部 unittest（包含双设备模拟同步和冲突回归）。
2. `ui_smoke.py` 界面回归。
3. 最终文件白名单和必要组件审计。
4. 使用最终 EXE 的 `--verify-package` 模式检查 Windows Qt 平台、界面、SVG、托盘图标、SQLite、AES-GCM/scrypt、TLS 校验、热键后端及 Windows 凭据存储。

最终 EXE 验证在临时数据库、独立 QSettings 和随机测试凭据服务下运行；不访问真实云端。验证失败时不替换正式版本。

更改依赖时修改 `requirements-build.in`，然后执行：

```powershell
uv pip compile requirements-build.in --python .venv/Scripts/python.exe --output-file requirements-build.txt
```

依赖升级后重新运行完整打包流程。体积数字是此版本和当前 Windows 环境的测量结果，不能代替其他 Windows 版本的兼容性测试。

## 本次测量（2026-11-14）

| 项目 | 体积 |
|---|---:|
| 优化前目录 | 397.13 MiB |
| 优化后目录 | 78.92 MiB |
| ZIP 分发包 | 32.20 MiB |

85 项自动化测试、界面回归、最终 EXE 的九项隔离检查通过。详细测量以 `build/release-report.json` 为准。
