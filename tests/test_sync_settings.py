"""Isolated regression tests for sync account and directory settings."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import unittest
import threading
import time
from unittest.mock import patch, Mock
import memo_app
from PySide6.QtWidgets import QApplication
from memo_app.remote_dir_dialog import RemoteDirDialog
from memo_app.sync.transport import NotFoundError
from memo_app.sync.service import SyncService
from memo_app.sync import credentials

app = QApplication.instance() or QApplication([])

def tearDownModule():
    from PySide6.QtCore import QThreadPool, QCoreApplication, QEvent
    QThreadPool.globalInstance().waitForDone(5000)
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


class DirectoryTests(unittest.TestCase):
    def wait_loaded(self, dlg):
        from PySide6.QtTest import QTest
        deadline = time.monotonic() + 4
        while dlg._busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(dlg._busy, "background request did not finish")

    def test_slow_network_does_not_block_dialog_or_close(self):
        gate = threading.Event()
        started = threading.Event()
        def slow(path):
            started.set()
            gate.wait(3)
            return []
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.side_effect = slow
            begin = time.monotonic()
            dlg = RemoteDirDialog("https://example.com/dav/", "u", "p")
            try:
                self.assertLess(time.monotonic()-begin, 0.5)
                self.assertTrue(started.wait(1))
                self.assertFalse(dlg.select_btn.isEnabled())
                dlg.reject()
            finally:
                gate.set()
                from PySide6.QtCore import QThreadPool
                QThreadPool.globalInstance().waitForDone(4000)
                app.processEvents()

    def test_missing_initial_directory_falls_back_to_root(self):
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.side_effect = [NotFoundError("not found",404), []]
            dlg = RemoteDirDialog("https://example.com/dav/", "u", "p", "memo-app")
            self.wait_loaded(dlg)
            self.assertEqual(dlg.result_dir(), "")
            self.assertTrue(dlg.select_btn.isEnabled())

    def test_go_up_uses_cached_root(self):
        from memo_app.sync.transport import RemoteEntry
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.return_value = [RemoteEntry("", "folder", True)]
            dlg = RemoteDirDialog("https://example.com/dav/", "u", "p")
            self.wait_loaded(dlg)
            dlg._on_item_double(dlg.list.item(0))
            self.wait_loaded(dlg)
            dlg._go_up()
            self.assertEqual(dlg.result_dir(), "")
            self.assertFalse(dlg._busy)
            self.assertEqual(factory.return_value.list_dir.call_count, 2)

    def test_reopen_uses_shared_cache_including_missing_default(self):
        cache = {}
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.side_effect = [NotFoundError("not found",404), []]
            first = RemoteDirDialog("https://example.com/dav/", "u", "p", "memo-app", cache=cache)
            self.wait_loaded(first)
            first.reject()
            second = RemoteDirDialog("https://example.com/dav/", "u", "p", "memo-app", cache=cache)
            self.assertFalse(second._busy)
            self.assertEqual(second.result_dir(), "")
            self.assertEqual(factory.return_value.list_dir.call_count, 2)
            factory.return_value.list_dir.side_effect = None
            factory.return_value.list_dir.return_value = []
            second._refresh(force=True)
            self.wait_loaded(second)
            self.assertEqual(factory.return_value.list_dir.call_count, 3)

    def test_failed_listing_cannot_be_selected(self):
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.side_effect = NotFoundError("not found",404)
            dlg = RemoteDirDialog("https://example.com/dav/", "u", "p")
            self.wait_loaded(dlg)
            self.assertFalse(dlg.select_btn.isEnabled())
            self.assertTrue(dlg.refresh_btn.isEnabled())

    def test_create_folder_refreshes_and_selects_created_folder(self):
        from memo_app.sync.transport import RemoteEntry
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.side_effect = [[], [RemoteEntry("", "新建目录", True)]]
            dlg = RemoteDirDialog("https://example.com/dav/", "u", "p", "parent")
            self.wait_loaded(dlg)
            with patch("memo_app.remote_dir_dialog.QInputDialog.getText", return_value=("新建目录", True)):
                dlg._new_folder()
            self.wait_loaded(dlg)
            factory.return_value.create_dir.assert_called_once_with("parent/新建目录")
            self.assertEqual(dlg.list.currentItem().text(), "新建目录/")
            self.assertEqual(dlg.result_dir(), "parent/新建目录")

    def test_create_rejects_path_instead_of_single_name(self):
        with patch("memo_app.remote_dir_dialog.WebDAVClient") as factory:
            factory.return_value.list_dir.return_value = []
            dlg = RemoteDirDialog("https://example.com/dav/", "u", "p")
            self.wait_loaded(dlg)
            for bad in ("../x", "a/b", "a\\b", ".", "..", " "):
                with patch("memo_app.remote_dir_dialog.QInputDialog.getText", return_value=(bad, True)):
                    dlg._new_folder()
                factory.return_value.create_dir.assert_not_called()

class AccountTests(unittest.TestCase):
    def make_service(self):
        with patch.object(SyncService, "enabled", False):
            svc = SyncService()
        svc._settings = Mock()
        self.addCleanup(svc.shutdown)
        return svc

    def test_success_persists_verified_account_only(self):
        svc = self.make_service()
        with patch.object(svc, "_enqueue"), patch.object(credentials, "save_credentials") as save:
            svc.test_connection("user@example.com", "secret", "https://example.com/dav/")
            save.assert_not_called()
            svc._on_result({"kind":"test_conn", "ok":True})
            save.assert_called_once_with("user@example.com", "secret")
            svc._settings.setValue.assert_called_with("sync_base_url", "https://example.com/dav/")

    def test_storage_failure_is_reported(self):
        svc = self.make_service()
        result = Mock()
        svc.conn_tested.connect(result)
        with patch.object(svc, "_enqueue"), patch.object(credentials, "save_credentials", side_effect=credentials.CredentialError("failed")):
            svc.test_connection("user@example.com", "secret")
            svc._on_result({"kind":"test_conn", "ok":True})
            self.assertFalse(result.call_args.args[0])
            svc._settings.setValue.assert_not_called()

    def test_logout_during_check_does_not_restore_credentials(self):
        svc = self.make_service()
        with patch.object(svc, "_enqueue"), patch.object(credentials, "clear_credentials"), patch.object(credentials, "save_credentials") as save:
            svc.test_connection("user@example.com", "secret")
            svc.logout()
            svc._on_result({"kind":"test_conn", "ok":True})
            save.assert_not_called()

    def test_failure_does_not_persist(self):
        svc = self.make_service()
        with patch.object(svc, "_enqueue"), patch.object(credentials, "save_credentials") as save:
            svc.test_connection("user@example.com", "secret")
            svc._on_result({"kind":"test_conn", "ok":False, "error":"401"})
            save.assert_not_called()

class SettingsUITests(unittest.TestCase):
    def controller(self):
        from types import SimpleNamespace
        svc = SimpleNamespace(enabled=False, base_url="https://example.com/dav/",
            remote_dir="memo-app", conflict_mode="lww", status_text="未启用",
            status_changed=Mock(), conn_tested=Mock(), vault_ready=Mock(),
            vault_id=lambda:None, logout=Mock())
        return SimpleNamespace(sync=svc, hotkey_display=lambda:"Ctrl+O")

    def test_saved_login_is_compact_on_reopen(self):
        from memo_app.settings_dialog import SyncSettingsPage
        with patch.object(credentials, "load_credentials", return_value=("user@example.com", "secret")):
            for _ in range(2):
                dlg = SyncSettingsPage(self.controller())
                self.assertFalse(dlg.login_row.isHidden())
                self.assertTrue(dlg.account_row.isHidden())
                self.assertTrue(dlg.password_row.isHidden())
                self.assertTrue(dlg.url_row.isHidden())
                self.assertEqual(dlg.test_btn.text(), "检查连接")
                self.assertEqual(dlg.sync_account.text(), "user@example.com")
                self.assertEqual(dlg.sync_app_pw.text(), "")
                dlg.deleteLater()

    def test_logout_restores_form_and_clears_password(self):
        from memo_app.settings_dialog import SyncSettingsPage
        with patch.object(credentials, "load_credentials", return_value=("user@example.com", "secret")) as load:
            dlg = SyncSettingsPage(self.controller())
            dlg.sync_app_pw.setText("secret")
            load.return_value = (None, None)
            dlg._on_logout()
            self.assertTrue(dlg.login_row.isHidden())
            self.assertFalse(dlg.password_row.isHidden())
            self.assertEqual(dlg.sync_app_pw.text(), "")
            self.assertEqual(dlg.test_btn.text(), "登录并保存")
            dlg.deleteLater()

if __name__ == "__main__":
    unittest.main()
