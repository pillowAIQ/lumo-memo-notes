"""Windows 原生窗口样式/命令的只读检查助手（HWND 指针宽度安全）。

供 taskbar_smoke.py 等原生验证使用：读取 GWL_STYLE/GWL_EXSTYLE、
判断 iconic/visible、发送 WM_SYSCOMMAND 与 ShowWindow 命令。
不做任何窗口样式修改；HWND/WPARAM/LPARAM/LONG_PTR 均按指针宽度传递，
不按 c_int 截断。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_DLGFRAME = 0x00400000
WS_CAPTION = 0x00C00000  # = WS_BORDER | WS_DLGFRAME
WS_BORDER = 0x00800000

WS_EX_TOOLWINDOW = 0x00000080

WM_SYSCOMMAND = 0x0112

SC_MINIMIZE = 0xF020
SC_RESTORE = 0xF120

SW_MINIMIZE = 6
SW_RESTORE = 9

# 任务栏/系统命令路径（WM_SYSCOMMAND SC_MINIMIZE/SC_RESTORE）依赖的必要样式位
REQUIRED_SHELL_STYLE = WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
# 无边框窗口不应出现的可见边框样式位
FORBIDDEN_FRAME_STYLE = WS_CAPTION | WS_THICKFRAME | WS_BORDER | WS_DLGFRAME

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _LONG_PTR = ctypes.c_ssize_t  # Win64 上 = c_longlong
    _user32.GetWindowLongPtrW.restype = _LONG_PTR
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, wintypes.INT]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, wintypes.INT]
    _user32.SendMessageW.restype = _LONG_PTR
    _user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]


def is_windows() -> bool:
    return sys.platform == "win32"


def hwnd(widget) -> int:
    """QWidget -> 原生 HWND（完整 64 位整数，不截断）。"""
    return int(widget.winId())


def style_bits(hwnd_: int) -> int:
    """GWL_STYLE（无符号 32 位）。"""
    return _user32.GetWindowLongPtrW(wintypes.HWND(hwnd_), GWL_STYLE) & 0xFFFFFFFF


def exstyle_bits(hwnd_: int) -> int:
    """GWL_EXSTYLE（无符号 32 位）。"""
    return _user32.GetWindowLongPtrW(wintypes.HWND(hwnd_), GWL_EXSTYLE) & 0xFFFFFFFF


def is_iconic(hwnd_: int) -> bool:
    return bool(_user32.IsIconic(wintypes.HWND(hwnd_)))


def is_window_visible(hwnd_: int) -> bool:
    return bool(_user32.IsWindowVisible(wintypes.HWND(hwnd_)))


def send_sys_command(hwnd_: int, cmd: int) -> None:
    """发送 WM_SYSCOMMAND（SC_MINIMIZE/SC_RESTORE 等系统命令）。"""
    _user32.SendMessageW(wintypes.HWND(hwnd_), WM_SYSCOMMAND, cmd, 0)


def show_window(hwnd_: int, cmd: int) -> None:
    _user32.ShowWindow(wintypes.HWND(hwnd_), cmd)
