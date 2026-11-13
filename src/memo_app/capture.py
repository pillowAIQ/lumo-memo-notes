"""Ctrl+O 全局呼出的极简输入框：只显示一个输入栏，回车即存。

支持 Raycast 式分类语法：输入 / 弹出分类列表，Tab/空格 补全，
"/新分类名 内容" 回车自动创建分类并归入。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QListWidget, QLineEdit, QVBoxLayout, QWidget

from . import db
from .icons import ui_icon


class QuickCapture(QWidget):
    """无边框、置顶、半透明的快捷记录弹窗。"""

    submitted = Signal(str, str)  # (内容, 分类名或"")

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(600, 106)
        self.setObjectName("capture")
        self.setStyleSheet(
            """
            /* 面板背景由 paintEvent 手绘（半透明顶层窗口 QSS 背景不生效） */
            QLineEdit {
                border: none;
                background: transparent;
                font-size: 16px;
                padding: 0;
                color: #283D35;
            }
            QLabel { background: transparent; border: none; color: #69766E; font-size: 11px; }
            QLabel#enterKey { background: #E5ECE0; border-radius: 5px; padding: 5px 9px; color: #2D6550; }
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 12)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(14)
        icon = QLabel()
        icon.setPixmap(ui_icon("note", "#2D6550", 24).pixmap(24, 24))
        row.addWidget(icon)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("让这一刻的想法，留在这里。")
        self.edit.setAccessibleName("快捷记录内容")
        self.edit.setToolTip("/分类名 归类 · 明天9点 设截止 · !高 @每周 提前30分钟")
        self.edit.textChanged.connect(self._refresh_popup)
        self.edit.installEventFilter(self)
        row.addWidget(self.edit, 1)
        key = QLabel("Enter")
        key.setObjectName("enterKey")
        row.addWidget(key, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row, 1)
        hint = QLabel("/ 分类  ·  自然语言设提醒                       Enter 保存  /  Esc 关闭")
        hint.setContentsMargins(38, 0, 0, 0)
        lay.addWidget(hint)
        self.edit.returnPressed.connect(self._submit)

        # 分类补全弹窗（独立顶层窗口，不能作为子控件否则被 68px 高度裁剪）
        self.popup = QListWidget()
        self.popup.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.ToolTip
        )
        self.popup.setFixedWidth(self.width())
        self.popup.setMaximumHeight(180)
        self.popup.setStyleSheet(
            "QListWidget { background: #FCFCF9; border: 1px solid #DCE2D7;"
            " border-radius: 10px; padding: 4px; }"
            "QListWidget::item { padding: 9px 14px; border-radius: 6px; color: #42594B; }"
            "QListWidget::item:selected { background: #DEE8DB; }"
        )
        self.popup.itemClicked.connect(self._on_popup_click)
        self.popup.hide()

    # ---------- 绘制 ----------

    def paintEvent(self, e):  # noqa: N802
        """手绘圆角半透明浅色面板。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0), 16, 16
        )
        p.fillPath(path, QColor(250, 252, 248, 250))
        p.setPen(QPen(QColor("#BBCBB4"), 1.0))
        p.drawPath(path)
        p.end()

    # ---------- 显示 / 隐藏 ----------

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.present()

    def present(self) -> None:
        self._move_to_top_center()
        self.edit.clear()
        self.edit.setPlaceholderText("让这一刻的想法，留在这里。")
        self.popup.hide()
        self.show()
        self.raise_()
        self._force_activate()
        # 激活是异步的，延迟补一次焦点兜底
        QTimer.singleShot(60, self._focus_edit)

    def _focus_edit(self) -> None:
        if self.isVisible():
            self.edit.setFocus()
            self.edit.setCursorPosition(0)

    def _force_activate(self) -> None:
        """Windows 前台锁定可能拒绝 Tool 窗口激活，强制置前并激活。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(self.winId())
            foreground = user32.GetForegroundWindow()
            cur_thread = kernel32.GetCurrentThreadId()
            fore_thread = user32.GetWindowThreadProcessId(foreground, None)
            attached = False
            if fore_thread and fore_thread != cur_thread:
                attached = bool(user32.AttachThreadInput(cur_thread, fore_thread, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(cur_thread, fore_thread, False)
        except Exception:
            pass
        self.activateWindow()

    def _move_to_top_center(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.center().x() - self.width() // 2
        y = geo.top() + int(geo.height() * 0.18)
        self.move(x, y)

    def _submit(self) -> None:
        text = self.edit.text().strip()
        self.popup.hide()
        self.hide()
        if not text:
            return
        # "/分类名 内容" 或 "/分类名"（仅创建分类）语法解析；
        # 分类名含 / 的按普通文本处理
        m = re.match(r"^/(\S+)(?:\s+(.+))?$", text)
        if m and "/" not in m.group(1):
            self.submitted.emit((m.group(2) or "").strip(), m.group(1))
        else:
            self.submitted.emit(text, "")

    # ---------- 分类补全 ----------

    def _refresh_popup(self, _text: str = "") -> None:
        t = self.edit.text()
        if re.match(r"^/\S*$", t):
            prefix = t[1:].lower()
            cats = [
                c
                for c in db.list_categories()
                if c.name.lower().startswith(prefix)
            ][:8]
            if cats:
                self.popup.clear()
                for c in cats:
                    self.popup.addItem(c.name)
                self.popup.setCurrentRow(0)
                pos = self.mapToGlobal(QPoint(0, self.height() + 6))
                self.popup.move(pos)
                self.popup.show()
                return
        self.popup.hide()

    def _complete_cat(self, name: str) -> None:
        self.edit.setText(f"/{name} ")
        self.edit.setCursorPosition(len(name) + 2)
        self.popup.hide()
        self.edit.setFocus()

    def _on_popup_click(self, item) -> None:
        self._complete_cat(item.text())

    # ---------- 事件 ----------

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.edit:
            if e.type() == QEvent.Type.KeyPress:
                key = e.key()
                if key == Qt.Key.Key_Escape:
                    self.popup.hide()
                    self.hide()
                    return True
                if self.popup.isVisible():
                    count = self.popup.count()
                    cur = self.popup.currentRow()
                    if key == Qt.Key.Key_Down and count:
                        self.popup.setCurrentRow(min(cur + 1, count - 1) if cur >= 0 else 0)
                        return True
                    if key == Qt.Key.Key_Up and count:
                        self.popup.setCurrentRow(max(cur - 1, 0) if cur >= 0 else count - 1)
                        return True
                    if key in (
                        Qt.Key.Key_Tab,
                        Qt.Key.Key_Space,
                        Qt.Key.Key_Return,
                        Qt.Key.Key_Enter,  # 小键盘回车
                    ):
                        row = cur if cur >= 0 else 0
                        if count:
                            self._complete_cat(self.popup.item(row).text())
                            return True
            if e.type() == QEvent.Type.FocusOut:
                # 点击到别处自动收起（延迟一下，避免与新焦点竞争）
                QTimer.singleShot(150, self._hide_if_unfocused)
        return super().eventFilter(obj, e)

    def _hide_if_unfocused(self) -> None:
        if not self.isVisible() or self.edit.hasFocus():
            return
        fw = QApplication.focusWidget()
        if fw is not None and self.popup.isAncestorOf(fw):
            return  # 焦点在分类弹窗内，保持呼出
        self.popup.hide()
        self.hide()
