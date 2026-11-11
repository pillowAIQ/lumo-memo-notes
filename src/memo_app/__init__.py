"""memo_app —— 个人极简备忘录。"""
import os
import sys

__version__ = "0.1.0"

if sys.platform == "win32":
    # ---- DLL 预加载（规避 PATH 污染导致的 PySide6 导入失败）----
    # 本机 PATH 中存在会抢占同名 DLL 的目录，例如：
    #   - anaconda/Library/bin/icuuc.dll 实为旧版 ICU 58，缺 Qt6 所需导出
    #   - PyInstaller 输出目录 _internal/python3.dll 为 Python 3.8 的 shim
    # 正常按名解析会加载到错误版本，报 "找不到指定的程序"。
    # 这里在导入 PySide6 之前按全路径预加载正确版本，让后续按名解析
    # 命中已加载模块。
    import ctypes
    from pathlib import Path

    _preload: list[str] = []

    # 当前解释器的 python3.dll（stable ABI shim）
    _py3 = Path(sys.base_prefix) / "python3.dll"
    if _py3.exists():
        _preload.append(str(_py3))

    # 系统自带 ICU（PySide6 6.12 起不再捆绑 icu*.dll，依赖系统 ICU）
    _sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for _name in ("icu.dll", "icuuc.dll", "icuin.dll"):
        _p = _sys32 / _name
        if _p.exists():
            _preload.append(str(_p))

    for _p in _preload:
        try:
            ctypes.CDLL(_p)
        except OSError:
            pass
