"""Persistent, visible reminders. All bookkeeping is local to this device."""
from __future__ import annotations

import logging
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QTextEdit, QVBoxLayout,
)

from . import db, nlp_time, theme


def system_sound():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except (ImportError, RuntimeError, OSError):
        QApplication.beep()


class ReminderDialog(QDialog):
    def __init__(self, manager):
        # Deliberately no main-window parent: minimizing it must not hide us.
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.manager = manager
        self.setWindowTitle("轻笺 · 到期提醒")
        self.resize(580, 510)
        self.setMinimumSize(360, 380)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        self.heading = QLabel("该处理待办了")
        font = self.heading.font(); font.setPointSize(21); font.setBold(True)
        self.heading.setFont(font)
        self.time_label = QLabel()
        root.addWidget(self.heading); root.addWidget(self.time_label)
        self.items = QListWidget()
        self.items.setObjectName("reminderList")
        self.items.setMaximumHeight(110)
        self.items.currentRowChanged.connect(self.show_selected)
        root.addWidget(self.items)
        self.content = QTextEdit()
        self.content.setReadOnly(True)
        self.content.setAccessibleName("到期备忘录完整内容")
        font = self.content.font(); font.setPointSize(14); self.content.setFont(font)
        root.addWidget(self.content, 1)
        buttons = QHBoxLayout()
        for title, action in (("完成", manager.complete), ("5 分钟后提醒", manager.snooze),
                              ("打开轻笺", manager.open_main), ("知道了", manager.dismiss)):
            button = QPushButton(title)
            button.clicked.connect(action)
            buttons.addWidget(button)
        root.addLayout(buttons)
        theme.notifier.changed.connect(self.retheme)
        self.retheme()

    def retheme(self, *_):
        self.setStyleSheet(
            f"QDialog {{background:{theme.c('bg')}; color:{theme.c('text')};}}"
            f"QLabel {{color:{theme.c('text')};}}"
            f"QTextEdit,QListWidget {{background:{theme.c('surface')};color:{theme.c('text')};"
            f"border:1px solid {theme.c('border')};border-radius:8px;padding:8px;}}"
            f"QListWidget#reminderList::item:selected {{background:{theme.c('accent_soft')};"
            f"color:{theme.c('accent_text')};border-radius:4px;}}"
            f"QPushButton {{background:{theme.c('accent')};color:{theme.c('on_accent')};"
            "border:none;border-radius:6px;padding:10px 8px;}"
            f"QPushButton:hover {{background:{theme.c('accent_hover')};}}"
        )

    def selected_id(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        selected = self.selected_id()
        self.items.blockSignals(True)
        self.items.clear()
        index = 0
        for mid, (memo, _) in self.manager.pending.items():
            self.items.addItem(memo.content.replace("\n", " ")[:70])
            item = self.items.item(self.items.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, mid)
            if mid == selected:
                index = self.items.count() - 1
        self.items.setCurrentRow(index)
        self.items.blockSignals(False)
        self.heading.setText(f"你有 {len(self.manager.pending)} 条待办需要留意")
        self.show_selected()

    def show_selected(self, *_):
        entry = self.manager.pending.get(self.selected_id())
        if entry:
            memo, token = entry
            stage = "已到期" if token[-1] == db.NOTIFY_DUE else "提前提醒"
            self.time_label.setText(f"{stage} · {nlp_time.format_due(memo.due_ts)}")
            self.content.setPlainText(memo.content)
        else:
            self.content.clear(); self.time_label.clear()

    def closeEvent(self, event):  # noqa: N802
        self.manager.dismiss()
        event.accept()


class ReminderManager(QObject):
    def __init__(self, on_complete, on_open, parent=None, *, clock=time.time, sound=system_sound):
        super().__init__(parent)
        self.on_complete, self.on_open = on_complete, on_open
        self.clock, self.sound = clock, sound
        self.pending = OrderedDict()
        self.dialog = None
        self.stopped = False

    @staticmethod
    def token(memo, stage):
        return (memo.uuid, memo.due_ts, memo.remind_advance, stage)

    def valid(self, mid, token=None):
        memo = db.get_memo(mid)
        if memo is None or memo.deleted_at or memo.is_done or memo.due_ts is None:
            return None
        if token and self.token(memo, token[-1]) != token:
            return None
        return memo

    def check(self):
        if self.stopped:
            return
        now = int(self.clock())
        for mid, (_, token) in list(self.pending.items()):
            memo = self.valid(mid, token)
            if memo is None:
                self.pending.pop(mid, None)
            else:
                self.pending[mid] = (memo, token)
        new = []
        for memo in db.memos_due(now):
            token = self.token(memo, db.reminder_stage(memo, now))
            if memo.id not in self.pending or self.pending[memo.id][1] != token:
                self.pending[memo.id] = (memo, token)
                new.append((memo.id, token))
        if self.pending:
            if self.dialog is None:
                self.dialog = ReminderDialog(self)
            self.dialog.refresh()
            if not new and not self.dialog.isVisible():
                new = [(mid, token) for mid, (_, token) in self.pending.items()]
            if new:
                self.dialog.show()
                self.dialog.raise_()
                self.dialog.activateWindow()
                if self.dialog.isVisible():
                    for mid, token in new:
                        db.mark_notified(mid, token[-1])
                    try:
                        self.sound()
                    except Exception:
                        logging.getLogger(__name__).warning("Reminder sound unavailable", exc_info=True)
                    QApplication.alert(self.dialog, 0)
        elif self.dialog is not None:
            self.dialog.hide()

    def selected(self):
        mid = self.dialog.selected_id() if self.dialog else None
        entry = self.pending.get(mid)
        return (mid, self.valid(mid, entry[1])) if entry else (None, None)

    def complete(self):
        mid, memo = self.selected()
        if memo:
            self.on_complete(mid)
        self.pending.pop(mid, None)
        self.check()

    def snooze(self):
        mid, memo = self.selected()
        if memo:
            db.snooze_memo(mid, now_ts=int(self.clock()))
        self.pending.pop(mid, None)
        self.check()

    def dismiss(self):
        self.pending.clear()
        if self.dialog is not None:
            self.dialog.hide()

    def open_main(self):
        self.dismiss()
        self.on_open()

    def shutdown(self):
        self.stopped = True
        self.dismiss()
        if self.dialog is not None:
            self.dialog.deleteLater()
            self.dialog = None
