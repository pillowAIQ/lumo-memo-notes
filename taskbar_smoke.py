"""Lumo 主窗口 Windows 外壳集成检查（真实窗口平台，非 offscreen）。

验证 FramelessWindowHint + 系统按钮提示（WindowSystemMenuHint /
WindowMinimizeButtonHint / WindowMaximizeButtonHint）让主窗口获得
WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX 原生样式且不出现原生边框，
从而任务栏点击在 Windows 外壳内部收敛到的命令路径真实生效：

- WM_SYSCOMMAND / SC_MINIMIZE / SC_RESTORE（任务栏/系统菜单同等命令）
- ShowWindow / SW_MINIMIZE / SW_RESTORE（外壳另一入口）
- Controller.show_main()（托盘/二次启动共用的应用层恢复路径）
- 标题栏最小化/最大化/还原按钮回归

注意：这是"系统命令级"验证——发送的是外壳对任务栏点击的同等命令，
并断言外壳判定所依赖的原生样式位；它不是模拟真人鼠标点击任务栏按钮
本身，但覆盖了该行为的全部原生前提。

运行：.venv/Scripts/python.exe taskbar_smoke.py
隔离：始终使用自有临时目录覆盖 MEMO_APP_DATA_DIR（忽略继承值，
退出时还原）；QSettings 强制 IniFormat + 唯一应用标识 + 临时路径，
不触真实托盘/热键/同步/设置；临时目录在 widget 释放后清理。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# offscreen 平台没有真实原生窗口，无法验证任务栏集成——直接拒绝。
if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
    print(
        "[taskbar_smoke] FATAL: QT_QPA_PLATFORM=offscreen；"
        "本检查需要真实 windows 平台",
        file=sys.stderr,
    )
    sys.exit(2)

if sys.platform != "win32":
    print("[taskbar_smoke] FATAL: 仅支持 Windows", file=sys.stderr)
    sys.exit(2)


class _SyncStub:
    """最小同步服务替身（无网络/无凭据），对齐 ui_smoke。"""

    enabled = False
    base_url = "https://dav.jianguoyun.com/dav/"
    remote_dir = "memo-app"
    conflict_mode = "lww"
    status_text = "未启用"
    directory_cache = {}

    class _Sig:
        def connect(self, *a, **k):
            pass

    status_changed = _Sig()
    conn_tested = _Sig()
    vault_ready = _Sig()
    synced = _Sig()

    def __init__(self, parent=None):
        pass

    def shutdown(self):
        pass

    def vault_id(self):
        return None

    def save_config(self, **kw):
        return True

    def queue_setup(self, *a, **k):
        pass

    def sync_now(self, *a, **k):
        pass

    def test_connection(self, *a, **k):
        pass

    def logout(self):
        pass

    def notify_change(self):
        pass


_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    suffix = f"  ({detail})" if detail else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}", flush=True)


def pump(app, ms: int = 80) -> None:
    """让 Qt 事件循环消化窗口状态变化（WindowStateChange 等）。"""
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _cleanup_tmp(tmp: tempfile.TemporaryDirectory) -> None:
    """Windows 上句柄释放有延迟：有界重试，最终静默兜底。"""
    for _ in range(30):
        try:
            tmp.cleanup()
            return
        except OSError:
            time.sleep(0.2)
    shutil.rmtree(tmp.name, ignore_errors=True)


def run() -> int:
    # 始终使用自有临时目录，绝不沿用继承的 MEMO_APP_DATA_DIR
    # （它可能指向真实用户数据）；退出时还原先前环境。
    prior_data_dir = os.environ.get("MEMO_APP_DATA_DIR")
    tmp = tempfile.TemporaryDirectory(prefix="lumo-taskbar-")
    os.environ["MEMO_APP_DATA_DIR"] = tmp.name
    try:
        # import memo_app 必须先于 PySide6：Windows DLL 预加载 workaround
        import memo_app  # noqa: F401
        from PySide6.QtCore import Qt, QSettings
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        # Win32 原型（HWND/WPARAM/LPARAM/LONG_PTR 指针宽度安全）
        from memo_app import windows_window as ww
        from memo_app.app import MainWindow
        from memo_app.controller import Controller

        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setStyle("Fusion")
        # QSettings 隔离：唯一应用标识 + IniFormat + 临时路径，
        # 不读不写真实注册表/设置（对齐 packaging_smoke.py 模式）
        app.setOrganizationName("memo-taskbar-check")
        app.setApplicationName(uuid.uuid4().hex)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp.name
        )
        QSettings.setPath(
            QSettings.Format.IniFormat, QSettings.Scope.SystemScope, tmp.name
        )

        with (
            patch.object(
                QSystemTrayIcon, "isSystemTrayAvailable", return_value=False
            ),
            patch("PySide6.QtCore.QSettings.value", return_value="ctrl+o"),
            patch("pynput.keyboard.Listener"),
            patch.object(Controller, "_check_reminders"),
            patch("memo_app.sync.service.SyncService", _SyncStub),
        ):
            win = MainWindow()
            ctl = Controller(win)
            ctl._remind_timer.stop()
            try:
                # ---- Qt 窗口标志契约 ----
                flags = win.windowFlags()
                for label, bit in (
                    ("Qt.Window", Qt.WindowType.Window),
                    ("FramelessWindowHint", Qt.WindowType.FramelessWindowHint),
                    ("WindowSystemMenuHint", Qt.WindowType.WindowSystemMenuHint),
                    ("WindowMinimizeButtonHint", Qt.WindowType.WindowMinimizeButtonHint),
                    ("WindowMaximizeButtonHint", Qt.WindowType.WindowMaximizeButtonHint),
                ):
                    check(f"windowFlags 含 {label}", bool(flags & bit))

                win.show()
                pump(app, 120)
                hwnd = ww.hwnd(win)
                check("获得原生 HWND", hwnd != 0, f"hwnd=0x{hwnd:X}")

                style = ww.style_bits(hwnd)
                exstyle = ww.exstyle_bits(hwnd)
                print(
                    f"[info] GWL_STYLE=0x{style:08X}  GWL_EXSTYLE=0x{exstyle:08X}",
                    flush=True,
                )
                check(
                    "WS_SYSMENU|WS_MINIMIZEBOX|WS_MAXIMIZEBOX 齐备",
                    style & ww.REQUIRED_SHELL_STYLE == ww.REQUIRED_SHELL_STYLE,
                    f"missing=0x{ww.REQUIRED_SHELL_STYLE & ~style & 0xFFFFFFFF:08X}",
                )
                check(
                    "无 WS_CAPTION/WS_THICKFRAME/WS_BORDER/WS_DLGFRAME（无边框保持）",
                    not (style & ww.FORBIDDEN_FRAME_STYLE),
                )
                check(
                    "无 WS_EX_TOOLWINDOW（确为任务栏应用窗口）",
                    not (exstyle & ww.WS_EX_TOOLWINDOW),
                )
                check("窗口可见", ww.is_window_visible(hwnd))

                # ---- WM_SYSCOMMAND 系统命令路径（任务栏/系统菜单同等命令）----
                ww.send_sys_command(hwnd, ww.SC_MINIMIZE)
                pump(app, 120)
                check("SC_MINIMIZE -> IsIconic", ww.is_iconic(hwnd))
                check("SC_MINIMIZE -> win.isMinimized()", win.isMinimized())
                check(
                    "最小化而非隐藏（IsWindowVisible 仍为真）",
                    ww.is_window_visible(hwnd),
                )
                check("桌面待办挂件自动显示", ctl.widget.isVisible())

                ww.send_sys_command(hwnd, ww.SC_RESTORE)
                pump(app, 120)
                check("SC_RESTORE -> 非 iconic", not ww.is_iconic(hwnd))
                check(
                    "SC_RESTORE -> win 非最小化且可见",
                    win.isVisible() and not win.isMinimized(),
                )
                check("还原后挂件收起", not ctl.widget.isVisible())

                # ---- ShowWindow 路径（外壳另一入口）----
                ww.show_window(hwnd, ww.SW_MINIMIZE)
                pump(app, 120)
                check(
                    "ShowWindow(SW_MINIMIZE) -> iconic",
                    ww.is_iconic(hwnd) and win.isMinimized(),
                )
                ww.show_window(hwnd, ww.SW_RESTORE)
                pump(app, 120)
                check(
                    "ShowWindow(SW_RESTORE) -> 还原",
                    not ww.is_iconic(hwnd) and not win.isMinimized(),
                )

                # ---- 最大化状态下最小化/还原：应回到最大化 ----
                win.titlebar.max_btn.click()
                pump(app, 120)
                check("标题栏最大化按钮", win.isMaximized())
                ww.send_sys_command(hwnd, ww.SC_MINIMIZE)
                pump(app, 120)
                check("最大化下 SC_MINIMIZE -> iconic", ww.is_iconic(hwnd))
                ww.send_sys_command(hwnd, ww.SC_RESTORE)
                pump(app, 120)
                check("SC_RESTORE 回到最大化状态", win.isMaximized())

                # ---- 应用层恢复路径（托盘/二次启动共用 show_main）----
                win.showNormal()
                pump(app, 60)
                ww.show_window(hwnd, ww.SW_MINIMIZE)
                pump(app, 120)
                ctl.show_main()
                pump(app, 120)
                check(
                    "show_main 解除最小化并显示",
                    win.isVisible() and not win.isMinimized(),
                )
                check("show_main 后挂件收起", not ctl.widget.isVisible())

                # ---- 标题栏按钮回归 ----
                win.titlebar.min_btn.click()
                pump(app, 120)
                check(
                    "标题栏最小化按钮",
                    win.isMinimized() and ww.is_iconic(hwnd),
                )
                ww.send_sys_command(hwnd, ww.SC_RESTORE)
                pump(app, 120)
                win.titlebar.max_btn.click()
                pump(app, 120)
                check("标题栏最大化按钮(还原后)", win.isMaximized())
                win.titlebar.max_btn.click()
                pump(app, 120)
                check(
                    "标题栏还原按钮",
                    not win.isMaximized() and win.isVisible(),
                )
            finally:
                # 先释放全部 widget/Qt 资源，再允许外层清理临时目录
                ctl._prepare_quit()
                win.removeEventFilter(ctl)
                win.tray_available = False
                win.close()
                ctl.widget.close()
                ctl.capture.close()
                try:
                    QApplication.instance().aboutToQuit.disconnect(
                        ctl._prepare_quit
                    )
                except (TypeError, RuntimeError):
                    pass
                ctl.deleteLater()
                win.deleteLater()
                pump(app, 60)
    finally:
        if prior_data_dir is None:
            os.environ.pop("MEMO_APP_DATA_DIR", None)
        else:
            os.environ["MEMO_APP_DATA_DIR"] = prior_data_dir
        _cleanup_tmp(tmp)

    failed = [name for name, ok, _ in _RESULTS if not ok]
    print("-" * 60, flush=True)
    if failed:
        print(f"FAILED {len(failed)}/{len(_RESULTS)}: {', '.join(failed)}", flush=True)
        return 1
    print(f"ALL CHECKS PASSED ({len(_RESULTS)})", flush=True)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
