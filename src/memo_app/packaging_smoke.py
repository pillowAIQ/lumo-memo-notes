"""Checks executed by the frozen EXE in an isolated profile; no real cloud access."""
import json
import os
from pathlib import Path
import tempfile
import traceback
import uuid


def verify(report_path):
    report = {"ok": False, "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="memo-package-") as temporary:
            os.environ["MEMO_APP_DATA_DIR"] = temporary
            import ssl
            from PySide6.QtCore import QSettings, QCoreApplication, QEvent
            from PySide6.QtGui import QImage, QPainter
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtWidgets import QApplication, QSystemTrayIcon
            app = QApplication([])
            from . import theme
            report["version"] = theme.APP_VERSION
            app.setOrganizationName("memo-package-check")
            app.setApplicationName(uuid.uuid4().hex)
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, temporary)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, temporary)
            report["checks"].append("Qt Windows platform")

            from . import db
            db.init_db()
            db.add_memo("packaged SQLite check", cat_name="test")
            assert db.list_memos()[0].content == "packaged SQLite check"
            report["checks"].append("SQLite read/write")
            from .reminders import ReminderManager
            reminder_id = db.add_memo("isolated packaged reminder", due_ts=1)
            reminders = ReminderManager(db.toggle_done, lambda: None, clock=lambda: 1, sound=lambda: None)
            reminders.check()
            assert reminders.dialog.isVisible() and not reminders.dialog.grab().isNull()
            reminders.shutdown()
            db.purge(reminder_id)
            if os.name == "nt":
                import winsound  # Verify bundled backend without playing audio.
                assert callable(winsound.MessageBeep)
            report["checks"].append("Persistent reminders and Windows sound backend")
            from .sync import crypto, credentials
            mk = crypto.generate_master_key()
            sealed = crypto.seal_vault(mk, "temporary-package-password")
            assert crypto.open_vault(sealed, "temporary-package-password") == mk
            report["checks"].append("AES-GCM and scrypt")
            ctx = ssl.create_default_context()
            assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
            report["checks"].append("TLS certificate verification")

            original_service = credentials.SERVICE
            credentials.SERVICE = "memo-package-test-" + uuid.uuid4().hex
            try:
                credentials.save_credentials("package-test", "temporary-secret")
                assert credentials.load_credentials() == ("package-test", "temporary-secret")
                from .app import MainWindow
                from .capture import QuickCapture
                from .widget import TodoWidget
                from .settings_dialog import SyncSettingsPage
                from .remote_dir_dialog import RemoteDirDialog
                from .sync.transport import RemoteEntry
                from .sync.service import SyncService
                from .controller import parse_hotkey
                from .icons import make_icon
                from .theme import QSS
                from types import SimpleNamespace
                import pynput.keyboard
                assert parse_hotkey("ctrl+shift+k")
                report["checks"].append("Windows hotkey backend")
                app.setStyleSheet(QSS)
                win = MainWindow()
                capture = QuickCapture()
                todo = TodoWidget()
                svc = SyncService()
                try:
                    settings = SyncSettingsPage(SimpleNamespace(sync=svc, hotkey_display=lambda:"Ctrl+O"))
                    assert settings.password_row.isHidden()
                    url = "https://dav.jianguoyun.com/dav/"
                    cache = {(url, "package-test", ""): (__import__("time").monotonic(),
                             [RemoteEntry("", "test-folder", True)])}
                    picker = RemoteDirDialog(url, "package-test", "temporary-secret", cache=cache)
                    assert not picker._busy and picker.list.count() == 1
                    picker.list.setCurrentRow(0)
                    assert picker.result_dir() == "test-folder"
                    tray = QSystemTrayIcon(make_icon(22), win)
                    assert not tray.icon().isNull()
                    renderer = QSvgRenderer(b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="green"/></svg>')
                    assert renderer.isValid()
                    image = QImage(16, 16, QImage.Format.Format_ARGB32)
                    painter = QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    assert image.pixelColor(8,8).green() > 0
                    assert not win.grab().isNull()
                    report["checks"].extend(["Widgets and settings", "SVG and tray icon", "directory picker"])
                    for widget in (picker, settings, todo, capture, win):
                        widget.close()
                        widget.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
                finally:
                    svc.shutdown()
                    svc.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            finally:
                credentials.clear_credentials()
                credentials.SERVICE = original_service
            report["checks"].append("Windows Credential Manager isolated write/read/delete")
            report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
