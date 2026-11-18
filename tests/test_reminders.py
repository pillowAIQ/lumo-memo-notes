import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import memo_app
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from memo_app import db
from memo_app.reminders import ReminderManager

app = QApplication.instance() or QApplication([])


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MEMO_APP_DATA_DIR": self.temp.name})
        self.env.start(); db.init_db()
        self.now = [1000]
        self.sound = Mock(); self.open = Mock()
        self.manager = self.make_manager()

    def make_manager(self):
        manager = ReminderManager(db.toggle_done, self.open, clock=lambda: self.now[0], sound=self.sound)
        self.addCleanup(manager.shutdown)
        return manager

    def tearDown(self):
        self.doCleanups(); app.processEvents()
        self.env.stop(); self.temp.cleanup()

    def test_no_tray_persistent_merge_and_dedup(self):
        first = db.add_memo("完整内容\n" * 80, due_ts=1000)
        self.manager.check()
        dialog = self.manager.dialog
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual(dialog.content.toPlainText(), db.get_memo(first).content)
        self.manager.check(); app.processEvents()
        self.assertEqual(self.sound.call_count, 1)
        db.add_memo("另一条同时到期", due_ts=999)
        self.manager.check()
        self.assertEqual(dialog.items.count(), 2)
        self.assertIs(dialog, self.manager.dialog)
        self.assertTrue(dialog.isVisible())
        dialog.close()
        self.manager.check()
        self.assertFalse(dialog.isVisible())

    def test_advance_does_not_suppress_due(self):
        mid = db.add_memo("提前和到期都要提醒", due_ts=1100, remind_advance=5)
        self.manager.check(); self.manager.dismiss()
        self.assertEqual(db.get_memo(mid).notified, db.NOTIFY_ADVANCE)
        self.now[0] = 1100
        self.manager.check()
        self.assertTrue(self.manager.dialog.isVisible())
        self.assertTrue(db.get_memo(mid).notified & db.NOTIFY_DUE)
        self.assertEqual(self.sound.call_count, 2)

    def test_persistent_snooze_keeps_due_and_sync_state(self):
        mid = db.add_memo("稍后", due_ts=1000)
        before = db.get_memo(mid)
        with db.db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
        self.manager.check(); self.manager.snooze(); self.manager.shutdown()
        db.init_db()  # Real restart migration must retain the snooze.
        self.manager = self.make_manager(); self.now[0] = 1299
        self.manager.check(); self.assertIsNone(self.manager.dialog)
        self.now[0] = 1300; self.manager.check()
        self.assertTrue(self.manager.dialog.isVisible())
        self.assertEqual(db.get_memo(mid).due_ts, before.due_ts)
        self.assertEqual(db.get_memo(mid).updated_at, before.updated_at)
        with db.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0], count)

    def test_deleted_or_rescheduled_item_is_safe(self):
        mid = db.add_memo("删除", due_ts=1000)
        self.manager.check(); db.purge(mid)
        self.manager.complete()
        self.assertFalse(self.manager.pending)
        mid = db.add_memo("改期", due_ts=1000)
        self.manager.check(); db.set_task_props(mid, due_ts=5000)
        self.manager.check(); self.assertFalse(self.manager.pending)

    def test_completion_uses_recurrence(self):
        mid = db.add_memo("每天", due_ts=1000)
        db.set_task_props(mid, recur=db.RECUR_DAILY)
        # Use a recent date so recurrence's existing iteration bound applies.
        import time
        db.set_task_props(mid, due_ts=int(time.time()) - 1)
        self.now[0] = int(time.time())
        self.manager.check(); self.manager.complete()
        updated = db.get_memo(mid)
        self.assertFalse(updated.is_done)
        self.assertGreater(updated.due_ts, self.now[0])
        self.assertFalse(self.manager.pending)


if __name__ == "__main__":
    unittest.main()
