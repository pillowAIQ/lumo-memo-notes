# 安装与更新

轻笺从 0.3.0 开始提供安装包和应用内更新。原来的 ZIP 便携版仍可使用。

## 第一次从便携版改用安装版

1. 从系统托盘菜单退出正在运行的轻笺。
2. 从 [GitHub Releases](https://github.com/pillowAIQ/lumo-notes/releases/latest) 下载最新版 `Lumo-<版本>-windows-x64-setup.exe`，按安装向导操作。
3. 默认只为当前 Windows 用户安装到 `%LOCALAPPDATA%\Programs\Lumo`，无需管理员权限。
4. 从开始菜单打开“轻笺 Lumo”；安装时也可选择创建桌面快捷方式。

安装版继续读取原来的 `~/.memo_app/memo.db`，QSettings 和 Windows 凭据管理器标识也保持不变。无需手动搬运数据库。若原来使用 `MEMO_APP_DATA_DIR` 自定义目录，仍需保留该环境配置。

安装、升级及卸载均不删除个人备忘录。卸载会移除应用和安装创建的快捷方式，个人数据目录独立保留。

## 以后如何更新

打开「设置 → 关于 → 软件更新」，点击“检查更新”。发现新版后可查看更新说明、下载并校验安装包，再点击“安装更新并退出轻笺”。确认后轻笺正常退出，安装向导覆盖升级；安装结束后可重新启动轻笺。

检查和下载在后台执行，可以取消下载。不会未经确认自动安装，也不会原地覆盖正在使用的 EXE。源码运行模式可以检查和下载，但不能执行安装程序；便携版可通过安装包迁移为安装版。

更新源固定为 `pillowAIQ/lumo-notes` 的 GitHub Releases。只接受比当前版本新的正式版本，以及名称匹配的 Windows x64 Setup。下载前要求 GitHub 提供文件大小与 SHA-256；下载完成和运行安装程序前分别校验。安装包缺失、校验失败、网络错误时不会执行更新，可稍后重试。

本地构建成功不代表 GitHub 已发布。GitHub 当前旧版若只有 ZIP，不具备安装包更新条件。本次开发不自动上传仓库或发布 Release。

## 开发者构建与发布

先安装 [Inno Setup](https://jrsoftware.org/isdl.php)。该工具也用于 [VS Code 的 Windows 安装包](https://github.com/microsoft/vscode/blob/main/build/win32/code.iss)。

```powershell
.venv/Scripts/python.exe build_release.py
.venv/Scripts/python.exe build_installer.py --iscc "C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
```

`build_installer.py` 会重新审计和运行目标 EXE 的隔离验证，然后生成：

- `build/Lumo-<版本>-windows-x64-setup.exe`
- `build/SHA256SUMS.txt`
- `build/installer-report.json`

发布新版时同步修改 `theme.APP_VERSION` 和 `pyproject.toml` 的版本，完整构建和验证后，经用户授权再发布 GitHub 正式 Release，标签如 `v0.3.0`，附上同版本 Setup 和校验文件。GitHub API 会提供资产 digest，应用更新器据此验证文件。不要用预发布版代替正式版，也不要改动安装包文件名约定。

生产安装使用稳定 AppId。自动测试应使用 `build_installer.py --test-mode`，使用独立 AppId、无快捷方式和无安装后启动，再用 `/DIR=<临时目录> /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` 验证安装、覆盖升级、卸载。

当前本地 Setup 没有 Authenticode 代码签名，Windows 可能显示未知发布者提示。SHA-256 是下载完整性校验，不等同于发布者代码签名；面向更广泛分发时可另行配置正式签名证书。

## 到期提醒

应用运行且电脑唤醒时，每 5 秒检查一次到期事项。提醒使用独立置顶窗口和系统提示音，不依赖托盘气泡；支持完整内容、多条事项、完成、5 分钟后提醒、打开轻笺和关闭提醒。提醒不会自行在几秒后消失。

“5 分钟后提醒”只保存在本机，不改截止时间，不上传到同步库，重启后仍有效。提前提醒和实际到期提醒分开处理。退出应用或电脑关机、睡眠时不会弹窗；恢复运行后会检查遗漏的到期事项。
