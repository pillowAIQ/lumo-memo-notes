"""设置对话框：目前只有呼出快捷键可配置。"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.ctl = controller
        self._recording = False
        self.setWindowTitle("设置")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("呼出快捷键"))
        self.hotkey_edit = QLineEdit(controller.hotkey_display())
        self.hotkey_edit.setReadOnly(True)
        row.addWidget(self.hotkey_edit, 1)
        self.record_btn = QPushButton("点击录制")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setCheckable(True)
        row.addWidget(self.record_btn)
        lay.addLayout(row)

        self.tip = QLabel("录制时按下你想要的组合键（需含 Ctrl/Shift/Alt/Win + 一个键）")
        self.tip.setStyleSheet("color: #8b877b; font-size: 11px;")
        self.tip.setWordWrap(True)
        lay.addWidget(self.tip)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.record_btn.toggled.connect(self._on_record_toggled)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll)

    def _on_record_toggled(self, on: bool) -> None:
        self._recording = on
        self.record_btn.setText("录制中… 再点取消" if on else "点击录制")
        if on:
            self.hotkey_edit.clear()
            self.hotkey_edit.setPlaceholderText("按下快捷键…")
            self._timer.start()
        else:
            self._timer.stop()

    def _poll(self) -> None:
        if not self._recording:
            return
        desc = self.ctl.describe_pressed()
        if desc:
            self.hotkey_edit.setText(desc)
            self.record_btn.setChecked(False)
            self._on_record_toggled(False)

    def _on_save(self) -> None:
        if not self.ctl.set_hotkey(self.hotkey_edit.text()):
            self.tip.setText("快捷键格式无法识别，请重新录制（需修饰键+普通键）")
            self.tip.setStyleSheet("color: #d64541; font-size: 11px;")
            return
        self.accept()
