"""任务属性对话框：截止时间 / 优先级 / 重复。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from . import db
from .db import Memo


class TaskPropsDialog(QDialog):
    """编辑单条备忘录的截止/优先级/重复属性。"""

    def __init__(self, memo: Memo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务属性")
        self.setMinimumWidth(340)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)
        lay.addLayout(form)

        # 截止时间
        self.due_check = QCheckBox("设置截止时间")
        self.due_check.setChecked(memo.due_ts is not None)
        self.due_edit = QDateTimeEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.due_edit.setDateTime(
            QDateTime.fromString(
                datetime.fromtimestamp(memo.due_ts).strftime("%Y-%m-%d %H:%M"),
                "yyyy-MM-dd HH:mm",
            )
            if memo.due_ts
            else QDateTime.currentDateTime().addSecs(3600)
        )
        self.due_edit.setEnabled(self.due_check.isChecked())
        self.due_check.toggled.connect(self.due_edit.setEnabled)
        form.addRow("截止", self._due_widget())

        # 优先级
        self.prio_combo = QComboBox()
        for p in (0, 1, 2, 3):
            self.prio_combo.addItem(db.PRIORITY_LABELS[p], p)
        self.prio_combo.setCurrentIndex(memo.priority)
        form.addRow("优先级", self.prio_combo)

        # 重复（必须先有截止时间才有意义）
        self.recur_combo = QComboBox()
        self.recur_combo.addItem("不重复", None)
        for key, label in db.RECUR_LABELS.items():
            self.recur_combo.addItem(label, key)
        idx = self.recur_combo.findData(memo.recur)
        self.recur_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.recur_combo.setEnabled(self.due_check.isChecked())
        self.due_check.toggled.connect(self.recur_combo.setEnabled)
        form.addRow("重复", self.recur_combo)

        # 提前提醒（分钟）；无截止时间时禁用
        self.advance_combo = QComboBox()
        for label, mins in (
            ("到期时提醒", 0),
            ("提前 5 分钟", 5),
            ("提前 15 分钟", 15),
            ("提前 30 分钟", 30),
            ("提前 1 小时", 60),
            ("提前 1 天", 1440),
        ):
            self.advance_combo.addItem(label, mins)
        aidx = self.advance_combo.findData(memo.remind_advance)
        if aidx < 0 and memo.remind_advance:
            # NLP 写入的自定义值（如提前10分钟）：补一项以便正确回显
            self.advance_combo.insertItem(
                1, f"提前 {memo.remind_advance} 分钟", memo.remind_advance
            )
            aidx = 1
        self.advance_combo.setCurrentIndex(aidx if aidx >= 0 else 0)
        self.advance_combo.setEnabled(self.due_check.isChecked())
        self.due_check.toggled.connect(self.advance_combo.setEnabled)
        form.addRow("提醒", self.advance_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _due_widget(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.due_check)
        lay.addWidget(self.due_edit)
        return w

    def result_props(self) -> tuple[int | None, int, str | None, int]:
        """(due_ts, priority, recur, advance_min)；due_ts 为 Unix 秒。"""
        due_ts = None
        if self.due_check.isChecked():
            qdt = self.due_edit.dateTime()
            dt = datetime(qdt.date().year(), qdt.date().month(), qdt.date().day(),
                          qdt.time().hour(), qdt.time().minute())
            due_ts = int(dt.timestamp())
        return (
            due_ts,
            int(self.prio_combo.currentData()),
            self.recur_combo.currentData() if due_ts is not None else None,
            int(self.advance_combo.currentData()) if due_ts is not None else 0,
        )
