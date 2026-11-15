"""任务栏最小化/还原回归。

背景：MainWindow 是 FramelessWindowHint 无边框窗口；若缺少
WindowSystemMenuHint / WindowMinimizeButtonHint / WindowMaximizeButtonHint，
Windows 原生样式缺 WS_SYSMENU / WS_MINIMIZEBOX / WS_MAXIMIZEBOX，
任务栏点击发来的 WM_SYSCOMMAND SC_MINIMIZE / SC_RESTORE 会被忽略——
表现为"点任务栏图标不收起"。

本文件两层验证（全部子进程隔离，不碰真实数据/托盘/热键/设置）：
- offscreen 契约：窗口标志位必须包含上述提示（任何平台可跑）。
- win32 原生：子进程执行 taskbar_smoke.py，用真实 HWND 样式位与
  WM_SYSCOMMAND / ShowWindow 系统命令验证最小化/还原。
  这是系统命令级验证，不等同于真人点击任务栏按钮；真人点击由
  Windows 外壳转换为同等命令路径。

运行：.venv/Scripts/python.exe -m unittest discover -s tests -p test_taskbar_window.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
ROOT = Path(__file__).resolve().parent.parent

# offscreen 契约检查：只验证 Qt 窗口标志位，不需要真实窗口。
# MEMO_APP_DATA_DIR 由外层 unittest 传入自有临时目录；QSettings 强制
# IniFormat + 唯一应用标识 + 该临时路径，不触真实设置。
FLAGS_CHILD = r"""
import os, sys, uuid
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import memo_app  # noqa: F401 - load Windows DLLs before importing Qt
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
app.setOrganizationName("memo-taskbar-check")
app.setApplicationName(uuid.uuid4().hex)
data_dir = os.environ["MEMO_APP_DATA_DIR"]
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, data_dir)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, data_dir)
from memo_app.app import MainWindow
win = MainWindow()
flags = win.windowFlags()
need = (
    ("Window", Qt.WindowType.Window),
    ("FramelessWindowHint", Qt.WindowType.FramelessWindowHint),
    ("WindowSystemMenuHint", Qt.WindowType.WindowSystemMenuHint),
    ("WindowMinimizeButtonHint", Qt.WindowType.WindowMinimizeButtonHint),
    ("WindowMaximizeButtonHint", Qt.WindowType.WindowMaximizeButtonHint),
)
missing = [name for name, bit in need if not (flags & bit)]
print("windowFlags=%s missing=%s" % (flags, missing), flush=True)
sys.exit(1 if missing else 0)
"""


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    if extra:
        env.update(extra)
    return env


def _rmtree_retry(path: str, attempts: int = 30) -> None:
    """Windows 上子进程句柄释放有延迟，清理临时目录需重试。"""
    import time

    for _ in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except OSError:
            time.sleep(0.2)
    shutil.rmtree(path, ignore_errors=True)


class TaskbarWindowTest(unittest.TestCase):
    def test_window_flags_contract_offscreen(self) -> None:
        """MainWindow 的 Qt 窗口标志必须包含系统按钮提示（offscreen 契约）。"""
        data_dir = tempfile.mkdtemp(prefix="memo-tb-flags-")
        try:
            proc = subprocess.run(
                [sys.executable, "-c", FLAGS_CHILD],
                env=_child_env({"MEMO_APP_DATA_DIR": data_dir}),
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("missing=[]", proc.stdout)
        finally:
            _rmtree_retry(data_dir)

    @unittest.skipUnless(sys.platform == "win32", "原生 HWND/系统命令检查仅 Windows")
    def test_native_shell_minimize_restore(self) -> None:
        """真实窗口平台：OS 样式位 + WM_SYSCOMMAND/ShowWindow 最小化与还原。

        子进程执行 taskbar_smoke.py（真实 windows 平台，临时数据目录，
        无真实托盘/热键/云同步）。这是系统命令级验证，不是真人点击
        任务栏按钮；真人点击由 Windows 外壳转换为同等命令路径。
        """
        data_dir = tempfile.mkdtemp(prefix="memo-tb-native-")
        try:
            env = _child_env({"MEMO_APP_DATA_DIR": data_dir})
            # 确保子进程跑在真实窗口平台（父进程环境可能带 offscreen）
            env.pop("QT_QPA_PLATFORM", None)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "taskbar_smoke.py")],
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("ALL CHECKS PASSED", proc.stdout)
        finally:
            _rmtree_retry(data_dir)


if __name__ == "__main__":
    unittest.main()
