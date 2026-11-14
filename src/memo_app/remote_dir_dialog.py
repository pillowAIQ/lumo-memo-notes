"""远程目录选择器（仿 Obsidian 同步设置的「浏览」按钮）。

用 PROPFIND Depth:1 列出坚果云 WebDAV 目录，逐级下钻选择
vault 数据的存放目录。同步调用+等待光标——单次请求很快，
不值得为它单开线程。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .sync.transport import WebDAVClient, WebDAVError


class RemoteDirDialog(QDialog):
    """浏览并选择坚果云上的同步目录。result_dir() 返回相对路径
    （"" 表示 WebDAV 根目录）。"""

    def __init__(
        self,
        base_url: str,
        account: str,
        app_password: str,
        current: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择同步目录")
        self.setMinimumSize(460, 380)
        self._client = WebDAVClient(
            base_url, account, app_password,
            root_dir="", timeout=15,
        )
        self._current = current.strip().strip("/")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        self.path_label = QLabel()
        self.path_label.setObjectName("dialogHint")
        lay.addWidget(self.path_label)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._on_item_double)
        lay.addWidget(self.list, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #d64541; font-size: 11px;")
        lay.addWidget(self.error_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.up_btn = QPushButton("上一级")
        self.up_btn.clicked.connect(self._go_up)
        row.addWidget(self.up_btn)
        row.addStretch(1)
        self.select_btn = QPushButton("选择此目录")
        self.select_btn.setDefault(True)
        self.select_btn.clicked.connect(self.accept)
        row.addWidget(self.select_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)

        self._refresh()

    # ---------- 导航 ----------

    def result_dir(self) -> str:
        return self._current

    def _display_path(self) -> str:
        return "/" + self._current if self._current else "/"

    def _refresh(self) -> None:
        self.path_label.setText(f"当前位置：{self._display_path()}")
        self.up_btn.setEnabled(bool(self._current))
        self.error_label.setText("")
        self.list.clear()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            entries = self._client.list_dir(self._current)
        except WebDAVError as e:
            self.error_label.setText(f"读取目录失败：{e}")
            entries = []
        finally:
            QApplication.restoreOverrideCursor()
        # 目录在前、文件在后，各自按名称排序；跳过自身条目
        dirs = sorted(
            (e for e in entries if e.is_collection and e.rel),
            key=lambda e: e.rel.lower(),
        )
        files = sorted(
            (e for e in entries if not e.is_collection and e.rel),
            key=lambda e: e.rel.lower(),
        )
        for e in dirs + files:
            text = e.rel + ("/" if e.is_collection else "")
            item = QListWidgetItem(text)
            if not e.is_collection:
                item.setForeground(QColor("gray"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setData(Qt.ItemDataRole.UserRole, e.rel)
            self.list.addItem(item)

    def _on_item_double(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self._current = (
                f"{self._current}/{name}" if self._current else name
            )
            self._refresh()

    def _go_up(self) -> None:
        if self._current:
            self._current = self._current.rsplit("/", 1)[0]
            self._refresh()
