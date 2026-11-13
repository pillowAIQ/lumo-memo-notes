"""分类勾选对话框：为备忘录选择归属分类。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .db import Category


class CategoryPickerDialog(QDialog):
    def __init__(
        self,
        categories: list[Category],
        selected: set[int],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择分类")
        self.setMinimumWidth(340)
        self._checks: dict[int, QCheckBox] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        heading = QLabel("给记录找个归属")
        heading.setObjectName("dialogTitle")
        hint = QLabel("一条备忘录可以放进多个分类。")
        hint.setObjectName("dialogHint")
        lay.addWidget(heading)
        lay.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMaximumHeight(300)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setSpacing(6)
        bl.setContentsMargins(0, 0, 4, 0)
        if not categories:
            bl.addWidget(QLabel("还没有分类，先在左侧栏新建一个吧"))
        for cat in categories:
            cb = QCheckBox(cat.name)
            cb.setChecked(cat.id in selected)
            self._checks[cat.id] = cb
            bl.addWidget(cb)
        bl.addStretch(1)
        scroll.setWidget(box)
        lay.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("完成")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        lay.addWidget(buttons)

    def selected_ids(self) -> set[int]:
        return {cid for cid, cb in self._checks.items() if cb.isChecked()}
