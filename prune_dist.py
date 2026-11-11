"""精简 PyInstaller 产物：删除未使用的 Qt6 模块 DLL 和插件。

memo_app 只用 QtCore/Gui/Widgets（+Network 依赖保留），
PySide6 hook 会把 WebEngine/Quick/3D 等全部打包进来，
这里裁剪到只剩运行所需。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PS_SIDE6 = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/memo/_internal/PySide6")

KEEP_DLL = {
    "Qt6Core.dll",
    "Qt6Gui.dll",
    "Qt6Widgets.dll",
    "Qt6Network.dll",
}
KEEP_PLUGINS = {"platforms", "styles", "iconengines"}

removed = 0
for f in PS_SIDE6.iterdir():
    if not f.is_file():
        continue
    n = f.name
    if n.startswith(("Qt6", "av", "sw")) and n not in KEEP_DLL:
        f.unlink()
        removed += 1

plugins = PS_SIDE6 / "plugins"
if plugins.is_dir():
    for d in plugins.iterdir():
        if d.is_dir() and d.name not in KEEP_PLUGINS:
            shutil.rmtree(d)
            removed += 1

print(f"pruned {removed} items from {PS_SIDE6}")
