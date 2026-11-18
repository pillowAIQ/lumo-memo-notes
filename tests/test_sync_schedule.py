"""SyncService 调度回归：30 分钟周期 + 编辑防抖 + 启动延迟首同步。

运行：./.venv/Scripts/python.exe -m unittest discover -s tests -p test_sync_schedule.py
真实 SyncService（真 QThread/QTimer）；QSettings/凭据/工作线程入队全部
替身或 Mock，临时 MEMO_APP_DATA_DIR，不触网、不读真实凭据。
"""
from __future__ import annotations

import contextlib
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import memo_app  # noqa: F401  Windows DLL 预加载须先于 PySide6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from memo_app import db
from memo_app.sync import credentials, store
from memo_app.sync.service import (
    DEBOUNCE_MS,
    PERIODIC_MS,
    STARTUP_DELAY_MS,
    SyncService,
)

app = QApplication.instance() or QApplication([])


def tearDownModule():
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    QThreadPool.globalInstance().waitForDone(5000)
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("MEMO_APP_DATA_DIR")
        os.environ["MEMO_APP_DATA_DIR"] = self._tmp.name
        db.init_db()

    def tearDown(self) -> None:
        if self._old_data_dir is None:
            os.environ.pop("MEMO_APP_DATA_DIR", None)
        else:
            os.environ["MEMO_APP_DATA_DIR"] = self._old_data_dir
        self._tmp.cleanup()

    # ---------- 工具 ----------

    @staticmethod
    def _wire_settings(svc: SyncService, enabled: bool) -> dict:
        """QSettings 替身：value/setValue 落在同一内存 dict。"""
        values = {
            "sync_enabled": enabled,
            "sync_base_url": "https://dav.jianguoyun.com/dav/",
            "sync_remote_dir": "memo-app",
            "sync_conflict_mode": "lww",
        }
        svc._settings = Mock()
        svc._settings.value.side_effect = lambda k, d=None: values.get(k, d)
        svc._settings.setValue.side_effect = \
            lambda k, v: values.__setitem__(k, v)
        return values

    def make_service(self, *, enabled: bool = False) -> SyncService:
        """真实 SyncService（真线程/真定时器）；settings 用内存替身。"""
        with patch.object(SyncService, "enabled", enabled):
            svc = SyncService()
        values = self._wire_settings(svc, enabled)
        self.addCleanup(svc.shutdown)
        # 构造时注册的 8s 启动 singleShot 无法取消：用例结束后把
        # enabled 置 False，防止稍后事件循环触发时读到真实库/凭据
        self.addCleanup(lambda: values.__setitem__("sync_enabled", False))
        return svc

    def bind_vault(self) -> None:
        with db.db() as conn:
            store.set_profile_vault_id(conn, str(uuid.uuid4()))

    @contextlib.contextmanager
    def sync_gate(self, svc: SyncService):
        """拦截工作线程入队 + 隔离凭据存储（不触网）。"""
        with patch.object(svc, "_enqueue") as enqueue, \
                patch.object(credentials, "load_credentials",
                             return_value=("user@example.com", "app-pw")), \
                patch.object(credentials, "load_vault_password",
                             return_value="vault-pw"):
            yield enqueue

    # ---------- 周期 ----------

    def test_periodic_interval_is_thirty_minutes(self) -> None:
        svc = self.make_service()
        self.assertEqual(PERIODIC_MS, 30 * 60 * 1000)
        self.assertEqual(svc._periodic.interval(), 30 * 60 * 1000)
        self.assertFalse(svc._periodic.isSingleShot())

    def test_restart_timer_follows_sync_enabled(self) -> None:
        on = self.make_service(enabled=True)
        self.assertTrue(on.enabled)
        self.assertTrue(on._periodic.isActive())
        off = self.make_service(enabled=False)
        self.assertFalse(off.enabled)
        self.assertFalse(off._periodic.isActive())

    def test_periodic_tick_enqueues_sync(self) -> None:
        svc = self.make_service(enabled=True)
        self.bind_vault()
        # 测试内缩短周期，观察真实 QTimer -> request_sync -> 入队链路
        svc._periodic.setInterval(30)
        svc._periodic.start()
        with self.sync_gate(svc) as enqueue:
            deadline = time.monotonic() + 3
            while not enqueue.called and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
        self.assertTrue(enqueue.called, "周期 timer 未触发 sync")
        self.assertEqual(enqueue.call_args.args[0]["kind"], "sync")

    # ---------- 编辑防抖（保留） ----------

    def test_edit_arms_debounce_only_when_work_pending(self) -> None:
        svc = self.make_service(enabled=True)
        svc.notify_change()
        self.assertFalse(svc._debounce.isActive())
        db.add_memo("编辑触发防抖同步")
        svc.notify_change()
        self.assertEqual(DEBOUNCE_MS, 20_000)
        self.assertTrue(svc._debounce.isSingleShot())
        self.assertEqual(svc._debounce.interval(), DEBOUNCE_MS)
        self.assertTrue(svc._debounce.isActive())

    def test_notify_change_ignored_when_disabled(self) -> None:
        svc = self.make_service(enabled=False)
        db.add_memo("disabled")
        svc.notify_change()
        self.assertFalse(svc._debounce.isActive())

    # ---------- 启动延迟首同步（保留） ----------

    def test_startup_autosync_still_scheduled(self) -> None:
        with patch.object(SyncService, "enabled", True), \
                patch.object(QTimer, "singleShot") as shot:
            svc = SyncService()
        values = self._wire_settings(svc, True)
        self.addCleanup(svc.shutdown)
        self.addCleanup(lambda: values.__setitem__("sync_enabled", False))
        shot.assert_called_once()
        msec, due = shot.call_args.args
        self.assertEqual(STARTUP_DELAY_MS, 8_000)
        self.assertEqual(msec, STARTUP_DELAY_MS)
        self.bind_vault()
        with self.sync_gate(svc) as enqueue:
            due()  # 模拟 8s 到点：_maybe_autostart -> request_sync
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0]["kind"], "sync")

    def test_autostart_noop_when_disabled(self) -> None:
        svc = self.make_service(enabled=False)
        self.bind_vault()
        with self.sync_gate(svc) as enqueue:
            svc._maybe_autostart()
        enqueue.assert_not_called()

    # ---------- 手动 / setup 立即同步（保留） ----------

    def test_manual_and_setup_sync_stay_immediate(self) -> None:
        svc = self.make_service(enabled=True)
        self.bind_vault()
        with self.sync_gate(svc) as enqueue:
            svc.sync_now()
            self.assertEqual(enqueue.call_args.args[0]["kind"], "sync")
            enqueue.reset_mock()
            # setup（建库/加入）完成 -> 立即补一轮 sync，不等周期
            svc._on_result({
                "ok": True, "kind": "setup",
                "vault_id": str(uuid.uuid4()),
            })
            self.assertEqual(enqueue.call_count, 1)
            self.assertEqual(enqueue.call_args.args[0]["kind"], "sync")


if __name__ == "__main__":
    unittest.main()
