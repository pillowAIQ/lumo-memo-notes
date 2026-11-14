"""Non-blocking WebDAV directory picker with session cache and folder creation."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from . import theme
from .sync.transport import WebDAVClient, NotFoundError

CACHE_TTL = 60


class _TaskSignals(QObject):
    finished = Signal(object)


class _DirectoryTask(QRunnable):
    def __init__(self, client, request_id, path, fallback=False, create_path=None):
        super().__init__()
        self.signals = _TaskSignals()
        self.client = client
        self.request_id = request_id
        self.path = path
        self.fallback = fallback
        self.create_path = create_path

    def run(self):
        result = {"id": self.request_id, "path": self.path, "requested": self.path,
                  "created": None, "error": ""}
        try:
            if self.create_path is not None:
                self.client.create_dir(self.create_path)
                result["created"] = self.create_path.rsplit("/", 1)[-1]
            try:
                entries = self.client.list_dir(self.path)
            except NotFoundError:
                if not self.fallback or not self.path:
                    raise
                result["path"] = ""
                entries = self.client.list_dir("")
            result["entries"] = entries
        except Exception as e:
            result["error"] = str(e)
        self.signals.finished.emit(result)


class RemoteDirDialog(QDialog):
    """result_dir() is relative to the WebDAV root (empty string = root)."""

    def __init__(self, base_url, account, app_password, current="", parent=None, *, cache=None):
        super().__init__(parent)
        self.setWindowTitle("选择同步目录")
        self.setMinimumSize(520, 400)
        self._client = WebDAVClient(base_url, account, app_password, root_dir="", timeout=15)
        self._cache = cache if cache is not None else {}
        self._cache_prefix = (self._client.base_url, account.casefold())
        self._current = current.strip().strip("/")
        self._request_id = 0
        self._busy = False
        self._valid = False
        self._closed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("dialogHint")
        lay.addWidget(self.path_label)
        self.list = QListWidget()
        self.list.setObjectName("remoteDirectoryList")
        # Override the main memo list's transparent selection, including when unfocused.
        t = theme.palette()
        self.list.setStyleSheet(f"""
            QListWidget#remoteDirectoryList {{ background: {t['surface']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 8px; padding: 4px;
                selection-background-color: {t['accent_soft']}; selection-color: {t['accent_text']}; }}
            QListWidget#remoteDirectoryList::item {{ padding: 8px; border-radius: 6px; }}
            QListWidget#remoteDirectoryList::item:hover {{ background: {t['hover']}; }}
            QListWidget#remoteDirectoryList::item:selected,
            QListWidget#remoteDirectoryList::item:selected:!active {{
                background: {t['accent_soft']}; color: {t['accent_text']}; }}
        """)
        self.list.itemDoubleClicked.connect(self._on_item_double)
        lay.addWidget(self.list, 1)
        self.loading_label = QLabel("")
        self.loading_label.setObjectName("dialogHint")
        lay.addWidget(self.loading_label)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("statusErr")
        lay.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.up_btn = QPushButton("上一级")
        self.up_btn.clicked.connect(self._go_up)
        actions.addWidget(self.up_btn)
        self.new_btn = QPushButton("新建文件夹")
        self.new_btn.clicked.connect(self._new_folder)
        actions.addWidget(self.new_btn)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self._refresh(force=True))
        actions.addWidget(self.refresh_btn)
        actions.addStretch(1)
        lay.addLayout(actions)
        row = QHBoxLayout()
        row.addWidget(QLabel("双击进入，或选中文件夹后确认"), 1)
        self.select_btn = QPushButton("选择目录")
        self.select_btn.setDefault(True)
        self.select_btn.clicked.connect(self.accept)
        row.addWidget(self.select_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)
        self._refresh(fallback_to_root=True)

    def result_dir(self):
        item = self.list.currentItem()
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        if name:
            return f"{self._current}/{name}" if self._current else name
        return self._current

    def _key(self, path):
        return (*self._cache_prefix, path)

    def _set_busy(self, busy):
        self._busy = busy
        self.list.setEnabled(not busy)
        self.up_btn.setEnabled(bool(self._current) and not busy)
        self.refresh_btn.setEnabled(not busy)
        self.new_btn.setEnabled(not busy and self._valid)
        self.select_btn.setEnabled(not busy and self._valid)

    def _refresh(self, *, fallback_to_root=False, force=False):
        if self._closed:
            return
        self._request_id += 1
        self._valid = False
        self.error_label.clear()
        self.list.clear()
        self.path_label.setText("当前位置：/" + self._current)
        cached = self._cache.get(self._key(self._current))
        if not force and cached and time.monotonic() - cached[0] < CACHE_TTL:
            if cached[1] is not None:
                self._render(cached[1])
                return
            if fallback_to_root:
                self._current = ""
                self._refresh()
                return
        self.loading_label.setText("正在读取坚果云目录…")
        self._start_task(fallback=fallback_to_root)

    def _start_task(self, *, fallback=False, create_path=None):
        self._set_busy(True)
        task = _DirectoryTask(self._client, self._request_id, self._current,
                              fallback=fallback, create_path=create_path)
        task.signals.finished.connect(self._on_loaded)
        QThreadPool.globalInstance().start(task)

    @Slot(object)
    def _on_loaded(self, result):
        if self._closed or result["id"] != self._request_id:
            return
        self._current = result["path"]
        self.path_label.setText("当前位置：/" + self._current)
        if result["error"]:
            self.loading_label.clear()
            if result["created"]:
                self._cache.pop(self._key(self._current), None)
                self._valid = False
                self.error_label.setText("文件夹已创建，但列表刷新失败，请点击刷新。")
            else:
                self.error_label.setText("操作失败：" + result["error"])
            self._set_busy(False)
            return
        entries = result["entries"]
        if result["requested"] != self._current:
            self._cache[self._key(result["requested"])] = (time.monotonic(), None)
        self._cache[self._key(self._current)] = (time.monotonic(), entries)
        self._render(entries, selected=result["created"])

    def _render(self, entries, selected=None):
        self.list.clear()
        entries = sorted((e for e in entries if e.rel),
                         key=lambda e: (not e.is_collection, e.rel.casefold()))
        for e in entries:
            item = QListWidgetItem(e.rel + ("/" if e.is_collection else ""))
            if e.is_collection:
                item.setData(Qt.ItemDataRole.UserRole, e.rel)
            else:
                item.setForeground(QColor(theme.c("faint")))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
            if e.rel == selected:
                self.list.setCurrentItem(item)
        self._valid = True
        self._set_busy(False)
        self.loading_label.setText("" if entries else "此目录为空，可新建文件夹或直接选择。")

    def _on_item_double(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name and not self._busy:
            self._current = f"{self._current}/{name}" if self._current else name
            self._refresh()

    def _go_up(self):
        if self._current and not self._busy:
            self._current = self._current.rpartition("/")[0]
            self._refresh()

    def _new_folder(self):
        if self._busy or not self._valid:
            return
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称")
        if not ok:
            return
        if (not name or name != name.strip() or name in (".", "..")
                or any(c in name for c in "/\\") or any(ord(c) < 32 for c in name)):
            self.error_label.setText("请输入单个文件夹名称，不能包含斜杠、首尾空格或特殊路径。")
            return
        if any(self.list.item(i).data(Qt.ItemDataRole.UserRole) == name
               for i in range(self.list.count())):
            self.error_label.setText("同名文件夹已存在，请选择其他名称。")
            return
        path = f"{self._current}/{name}" if self._current else name
        self._request_id += 1
        self.error_label.clear()
        self.loading_label.setText("正在创建文件夹…")
        self._start_task(create_path=path)

    def done(self, result):
        if result == QDialog.DialogCode.Accepted and (self._busy or not self._valid):
            return
        self._closed = True
        self._request_id += 1
        super().done(result)
