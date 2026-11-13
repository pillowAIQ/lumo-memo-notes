"""PySide6 主界面：导航栏 + 分类侧边栏 + 卡片流 + 回收站。"""
from __future__ import annotations

import ctypes
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import db
from . import nlp_time
from .category_dialog import CategoryPickerDialog
from .db import Category, Memo
from .icons import make_icon
from .settings_dialog import SettingsDialog
from .task_dialog import TaskPropsDialog

C_TEXT = "#2b2925"
C_TEXT_DONE = "#7d786b"
C_PIN = "#e8a33d"

_CHECK_PNG = (Path(__file__).parent / "assets" / "check.png").as_posix()

QSS = """
QWidget#central { background: #f7f6f2; }

/* ===== 自绘标题栏 ===== */
QWidget#titleBar {
    background: #efede6;
    border-bottom: 1px solid #e2ded4;
}
QLabel#tbIcon { border: none; background: transparent; }
QLabel#tbTitle {
    font-size: 13px; font-weight: 600; color: #3a362e;
    border: none; background: transparent;
}
QToolButton#navBtn {
    border: none; background: transparent; color: #5c574c;
    font-size: 12px; padding: 4px 10px; border-radius: 6px;
}
QToolButton#navBtn:hover { background: #e6e3d7; color: #2b2925; }
QToolButton#navBtn::menu-indicator { image: none; }
QToolButton#winBtn {
    border: none; background: transparent; color: #5c574c;
    font-size: 12px; padding: 4px 11px; border-radius: 6px;
}
QToolButton#winBtn:hover { background: #e0ddd2; color: #2b2925; }
QToolButton#closeBtn {
    border: none; background: transparent; color: #5c574c;
    font-size: 12px; padding: 4px 11px; border-radius: 6px;
}
QToolButton#closeBtn:hover { background: #e81123; color: #ffffff; }

/* ===== 弹出菜单 ===== */
QMenu {
    background: #fbfaf6;
    border: 1px solid #e2ded4;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 14px 6px 12px;
    border-radius: 6px;
    color: #3a362e;
}
QMenu::item:selected { background: #fbeee5; }
QMenu::separator { height: 1px; background: #ece9e0; margin: 4px 10px; }

/* ===== 对话框通用 ===== */
QDialog { background: #fbfaf6; }
QDialog QLabel { color: #3a362e; background: transparent; border: none; }
QLineEdit {
    border: 1px solid #d8d3c6; border-radius: 8px;
    padding: 7px 10px; background: #ffffff; color: #2b2925;
}
QLineEdit:focus { border-color: #d96c3f; }
QPushButton {
    border: 1px solid #d8d3c6; border-radius: 8px;
    padding: 6px 16px; background: #ffffff; color: #3a362e;
}
QPushButton:hover { background: #f3f1ea; }
QPushButton:default {
    background: #d96c3f; border-color: #d96c3f; color: #ffffff;
}
QPushButton:default:hover { background: #c85f33; }
QCheckBox { color: #3a362e; spacing: 6px; background: transparent; border: none; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid #c7c2b2; border-radius: 5px; background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #d96c3f; }
QCheckBox::indicator:checked {
    background: #d96c3f; border-color: #d96c3f;
    image: url(@CHECK@);
}

/* ===== 侧边栏 ===== */
QWidget#sidePanel { background: transparent; border: none; }
QWidget#catRow { border-radius: 8px; }
QWidget#catRow:hover { background: #e7e4d9; }
QWidget#catRow[selected="true"] { background: #fbeee5; }
QLabel#catName { font-size: 13px; color: #3a362e; }
QPushButton#catDel {
    border: none; background: transparent; color: #8f8a80;
    font-size: 13px; padding: 0 4px;
}
QPushButton#catDel:hover { color: #d64541; }
QPushButton#addCatBtn {
    border: 1px dashed #c7c2b2; border-radius: 8px;
    color: #7d786b; padding: 6px; background: transparent;
}
QPushButton#addCatBtn:hover { color: #3a362e; border-color: #7d786b; }

/* 输入区 */
QLineEdit#mainInput {
    font-size: 15px; padding: 10px 14px;
    border: 1px solid #d8d3c6; border-radius: 12px; background: #ffffff;
    color: #2b2925;
}
QLineEdit#mainInput:focus { border-color: #d96c3f; }

QLineEdit#searchInput {
    border: 1px solid #d8d3c6; border-radius: 10px;
    padding: 6px 12px; background: #ffffff; color: #2b2925;
}
QLineEdit#searchInput:focus { border-color: #d96c3f; }

QPushButton#pillBtn {
    border: 1px solid #d8d3c6; border-radius: 10px;
    padding: 5px 14px; background: #ffffff; color: #5c574c;
}
QPushButton#pillBtn:hover { background: #f3f1ea; }
QPushButton#recordBtn:checked {
    background: #d96c3f; border-color: #d96c3f; color: #ffffff;
}
QPushButton#recordBtn:checked:hover { background: #c85f33; }

/* 备忘录卡片 */
QWidget#memoItem {
    background: #ffffff;
    border: 1px solid #e6e2d8;
    border-radius: 12px;
}
QWidget#memoItem:hover { border-color: #d0cbbd; }
QWidget#memoItem[selected="true"] {
    border-color: #d96c3f88;
    background: #fffaf4;
}
QLabel#memoContent { font-size: 14px; }
QLabel#memoTime { font-size: 11px; color: #7d786b; border: none; background: transparent; }
QLabel#memoPin { font-size: 12px; border: none; background: transparent; }
QLabel#memoTags { font-size: 10px; color: #7a86c8; border: none; background: transparent; }
QLabel#memoBadges { font-size: 10px; border: none; background: transparent; }

QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border: none; }
QListWidget::item:selected { background: transparent; }

/* 细 overlay 滚动条 */
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 2px 2px 2px 0;
}
QScrollBar::handle:vertical {
    background: #d0cbbd; border-radius: 5px; min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: #bdb7a6; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QLabel#emptyLabel { color: #8f8a80; font-size: 14px; border: none; background: transparent; }
QLabel#statusLabel { color: #8f8a80; font-size: 11px; border: none; background: transparent; }

/* 回收站横幅 */
QWidget#trashBar {
    background: #faf5e9; border: 1px solid #e9e1cd; border-radius: 10px;
}
QPushButton#trashBack {
    border: none; background: transparent; color: #5c574c;
    font-weight: 600; padding: 4px 8px;
}
QPushButton#trashBack:hover { color: #d96c3f; }
QLabel#trashTitle { color: #7d786b; font-size: 12px; border: none; background: transparent; }
QPushButton#trashPurge {
    border: 1px solid #e5c8bc; border-radius: 8px; background: #ffffff;
    color: #b3503f; padding: 4px 14px;
}
QPushButton#trashPurge:hover { background: #fdf1ec; border-color: #d98d7d; color: #d64541; }
""".replace("@CHECK@", _CHECK_PNG)


def rel_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    delta = datetime.now() - dt
    secs = delta.total_seconds()
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    if secs < 86400 * 7:
        return f"{int(secs // 86400)} 天前"
    return dt.strftime("%Y-%m-%d")


class _NavButton(QToolButton):
    """导航菜单按钮：点击或悬停展开 QMenu。"""

    def __init__(self, text: str, menu: QMenu, parent=None) -> None:
        super().__init__(parent)
        self.setText(text + "  ▾")
        self.setObjectName("navBtn")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = menu
        self.setMenu(menu)
        self._hover = QTimer(self)
        self._hover.setSingleShot(True)
        self._hover.setInterval(400)
        self._hover.timeout.connect(self._popup_if_hovered)

    def enterEvent(self, e):  # noqa: N802
        self._hover.start()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover.stop()
        super().leaveEvent(e)

    def _popup_if_hovered(self) -> None:
        if self.underMouse() and not self._menu.isVisible():
            self._menu.popup(self.mapToGlobal(QPoint(0, self.height())))


class TitleBar(QWidget):
    """自绘标题栏：图标+名称+编辑菜单+设置+窗口控制按钮。"""

    def __init__(self, win: "MainWindow", edit_menu: QMenu) -> None:
        super().__init__()
        self.win = win
        self.setObjectName("titleBar")
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(6)

        icon = QLabel()
        icon.setObjectName("tbIcon")
        icon.setPixmap(make_icon(18).pixmap(18, 18))
        lay.addWidget(icon)
        title = QLabel("memo")
        title.setObjectName("tbTitle")
        lay.addWidget(title)
        lay.addStretch(1)

        self.edit_nav = _NavButton("编辑", edit_menu, self)
        lay.addWidget(self.edit_nav)

        settings_btn = QToolButton()
        settings_btn.setText("设置")
        settings_btn.setObjectName("navBtn")
        settings_btn.clicked.connect(win._open_settings)
        lay.addWidget(settings_btn)
        lay.addSpacing(8)

        # 窗口控制按钮用 Segoe MDL2 图标字体，字形统一不跳动
        mdl2 = QFont("Segoe MDL2 Assets", 10)
        self.min_btn = self._wbtn("\ue921", "winBtn", "最小化", win.showMinimized, mdl2)
        self.max_btn = self._wbtn("\ue922", "winBtn", "最大化/还原", self._toggle_max, mdl2)
        close_btn = self._wbtn("\ue8bb", "closeBtn", "关闭", win.close, mdl2)
        for b in (self.min_btn, self.max_btn, close_btn):
            lay.addWidget(b)

    @staticmethod
    def _wbtn(text, objname, tip, slot, font=None) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setObjectName(objname)
        b.setToolTip(tip)
        b.setFixedSize(36, 30)
        if font is not None:
            b.setFont(font)
        b.clicked.connect(slot)
        return b

    def _toggle_max(self) -> None:
        if self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()
        self.sync_max()

    def sync_max(self) -> None:
        self.max_btn.setText("\ue923" if self.win.isMaximized() else "\ue922")

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()
        super().mouseDoubleClickEvent(e)


class NameDialog(QDialog):
    """简单的单行输入对话框（替代塑料感的 QInputDialog）。"""

    def __init__(self, title: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(320)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(QLabel(label))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("输入名称…")
        lay.addWidget(self.edit)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        ok = QPushButton("确定")
        ok.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        self.edit.returnPressed.connect(self._ok)

    def _ok(self) -> None:
        if self.edit.text().strip():
            self.accept()

    def text(self) -> str:
        return self.edit.text().strip()


class CategoryRow(QWidget):
    """侧边栏分类行：点击选中，悬停出现删除。"""

    activated = Signal(int)          # cat_id，0 = 全部
    delete_requested = Signal(int)

    def __init__(self, cat_id: int, name: str):
        super().__init__()
        self.cat_id = cat_id
        self.setObjectName("catRow")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(30)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(4)

        self.name = QLabel(name)
        self.name.setObjectName("catName")
        self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.name, 1)

        self.del_btn = QPushButton("×")
        self.del_btn.setObjectName("catDel")
        self.del_btn.setFixedSize(18, 18)
        self.del_btn.setVisible(False)
        self.del_btn.clicked.connect(lambda: self.delete_requested.emit(cat_id))
        lay.addWidget(self.del_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.cat_id)
        super().mousePressEvent(e)

    def enterEvent(self, e):  # noqa: N802
        self.del_btn.setVisible(self.cat_id != 0)
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self.del_btn.setVisible(False)
        super().leaveEvent(e)


class MemoItemWidget(QWidget):
    """单条备忘录的展示控件（卡片样式，内容自动换行、高度自适应）。"""

    HEIGHT = 54  # 最小高度（单行）
    height_changed = Signal(int)

    def __init__(self, memo: Memo, win: "MainWindow"):
        super().__init__()
        self.memo = memo
        self.win = win
        self.full_text = memo.content
        self._editing = False
        self.setObjectName("memoItem")
        self.setProperty("selected", False)
        # 自定义 QWidget 子类需此属性 QSS 背景/边框/圆角才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build()
        self._apply_state()
        self.setMinimumHeight(self.HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        QTimer.singleShot(0, self, self._update_height)

    # ---------- 构建 ----------

    def _build(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 7, 14, 7)
        lay.setSpacing(10)

        self.done_btn = QPushButton()
        self.done_btn.setFixedSize(22, 22)
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.setFlat(True)
        self.done_btn.setStyleSheet(
            "QPushButton { border: 1.5px solid #d8d4c8; border-radius: 11px;"
            " padding: 0; background: transparent; }"
            "QPushButton:hover { border-color: #2f9e63; }"
            "QPushButton[done=\"true\"] { background: #2f9e63;"
            " border-color: #2f9e63; color: white; font-weight: bold; font-size: 12px; }"
        )
        self.done_btn.clicked.connect(
            lambda: self.win.toggle_done(self.memo.id)
        )
        lay.addWidget(self.done_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 内容区：label / 编辑框 叠放
        self.host_stack = QStackedWidget()
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        self.content_label = QLabel()
        self.content_label.setObjectName("memoContent")
        self.content_label.setWordWrap(True)
        self.content_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        pl.addWidget(self.content_label)
        self.host_stack.addWidget(page)
        self.edit = QLineEdit()
        self.edit.setStyleSheet(
            "border: none; background: #f7f6f2; border-radius: 8px; padding: 4px 8px;"
        )
        self.edit.returnPressed.connect(lambda: self._end_edit(save=True))
        self.edit.editingFinished.connect(self._end_edit)
        self.edit.installEventFilter(self)
        self.host_stack.addWidget(self.edit)
        lay.addWidget(self.host_stack, 1)

        # 删除按钮：常显暖灰，悬停变红
        self.del_btn = QPushButton("×")
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.setFlat(True)
        self.del_btn.setStyleSheet(
            "QPushButton { border: none; padding: 0; color: #8f8a80; font-size: 15px; }"
            "QPushButton:hover { color: #d64541; }"
        )
        self.del_btn.clicked.connect(lambda: self.win.delete_memo(self.memo.id))
        lay.addWidget(self.del_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 任务属性按钮：截止/优先级/重复/提醒
        self.props_btn = QPushButton("⚙")
        self.props_btn.setFixedSize(22, 22)
        self.props_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.props_btn.setFlat(True)
        self.props_btn.setStyleSheet(
            "QPushButton { border: none; padding: 0; color: #8f8a80; font-size: 13px; }"
            "QPushButton:hover { color: #5a7fb5; }"
        )
        self.props_btn.clicked.connect(
            lambda: self.win.edit_task_props(self.memo.id)
        )
        lay.addWidget(self.props_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 回收站模式：显示"恢复"按钮
        self.restore_btn = QPushButton("↩")
        self.restore_btn.setFixedSize(22, 22)
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.setFlat(True)
        self.restore_btn.setStyleSheet(
            "QPushButton { border: none; padding: 0; color: #7d786b; font-size: 14px; }"
            "QPushButton:hover { color: #2f9e63; }"
        )
        self.restore_btn.setVisible(False)
        self.restore_btn.clicked.connect(lambda: self.win.restore_memo(self.memo.id))
        lay.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 右侧元信息：置顶图钉 + 相对时间 + 分类标签
        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(0)
        self.pin_label = QLabel("📌")
        self.pin_label.setObjectName("memoPin")
        self.pin_label.setStyleSheet(f"color: {C_PIN};")
        self.pin_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.time_label = QLabel()
        self.time_label.setObjectName("memoTime")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tags_label = QLabel()
        self.tags_label.setObjectName("memoTags")
        self.tags_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.badges_label = QLabel()
        self.badges_label.setObjectName("memoBadges")
        self.badges_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.badges_label.setWordWrap(True)
        meta.addWidget(self.pin_label)
        meta.addWidget(self.time_label)
        meta.addWidget(self.badges_label)
        meta.addWidget(self.tags_label)
        lay.addLayout(meta)

    # ---------- 状态 ----------

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_state(self) -> None:
        done = self.memo.is_done
        self.done_btn.setProperty("done", "true" if done else "false")
        self.done_btn.setText("✓" if done else "")
        self.done_btn.style().unpolish(self.done_btn)
        self.done_btn.style().polish(self.done_btn)

        font = self.content_label.font()
        font.setStrikeOut(done)
        self.content_label.setFont(font)
        pal = self.content_label.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(C_TEXT_DONE if done else C_TEXT))
        self.content_label.setPalette(pal)

        self.content_label.setText(self.full_text)
        self.content_label.setToolTip(self.full_text)
        self.pin_label.setVisible(self.memo.is_pinned)
        self.time_label.setText(rel_time(self.memo.updated_at))

        # 分类标签
        cat_ids = self.win._memo_cats.get(self.memo.id, set())
        names = [self.win._cat_names[c] for c in cat_ids if c in self.win._cat_names]
        self.tags_label.setText(" ".join(f"#{n}" for n in names))
        self.tags_label.setVisible(bool(names))

        # 截止/优先级/重复徽标
        self.badges_label.setText(self._badges_html())
        self.badges_label.setVisible(bool(self.badges_label.text()))

        trash = self.win.trash_mode
        self.done_btn.setVisible(not trash)
        self.del_btn.setVisible(not trash)
        self.props_btn.setVisible(not trash)
        self.restore_btn.setVisible(trash)
        QTimer.singleShot(0, self, self._update_height)

    def _update_height(self) -> None:
        """按换行后的文本高度调整卡片高度（长备忘录完整显示）。"""
        if self._editing:
            return
        label_w = self.content_label.width()
        if label_w <= 4:
            return
        br = self.content_label.fontMetrics().boundingRect(
            QRect(0, 0, label_w, 0),
            Qt.TextFlag.TextWordWrap,
            self.full_text,
        )
        # 上下布局内边距各 7px；至少保留单行最小高度
        h = max(self.HEIGHT, br.height() + 14)
        if abs(h - self.height()) > 1:
            self.setFixedHeight(h)
            self.height_changed.emit(h)

    def _badges_html(self) -> str:
        """截止/优先级/重复的富文本徽标。"""
        parts = []
        m = self.memo
        now_ts = int(datetime.now().timestamp())
        if m.due_ts:
            overdue = m.due_ts < now_ts
            today = datetime.now().date() == datetime.fromtimestamp(m.due_ts).date()
            color = "#d64541" if overdue else ("#b8860b" if today else "#7d786b")
            prefix = "已逾期 " if overdue else "⏰ "
            parts.append(
                f'<span style="color:{color}">{prefix}{nlp_time.format_due(m.due_ts)}</span>'
            )
        if 0 < m.priority <= 3:
            pcolor = {1: "#7d786b", 2: "#b8860b", 3: "#d64541"}[m.priority]
            parts.append(
                f'<span style="color:{pcolor}">❗{db.PRIORITY_LABELS[m.priority]}</span>'
            )
        if m.recur:
            parts.append(f'<span style="color:#7d786b">↻{db.RECUR_LABELS.get(m.recur, m.recur)}</span>')
        return " ".join(parts)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        # 宽度变化会改变换行行数，延迟到布局稳定后重算高度
        QTimer.singleShot(0, self, self._update_height)

    # ---------- 编辑 ----------

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if not self.win.trash_mode and not self._editing:
            self._start_edit()
        else:
            super().mouseDoubleClickEvent(e)

    def _start_edit(self) -> None:
        self._editing = True
        self.edit.setText(self.full_text)
        self.host_stack.setCurrentWidget(self.edit)
        self.edit.setFocus()
        self.edit.selectAll()

    def _end_edit(self, save: bool = True) -> None:
        if not self._editing:
            return
        self._editing = False
        text = self.edit.text().strip()
        self.host_stack.setCurrentWidget(self.host_stack.widget(0))
        QTimer.singleShot(0, self, self._update_height)
        if save and text and text != self.full_text:
            self.win.edit_memo(self.memo.id, text)
        else:
            self.content_label.setText(self.full_text)

    def eventFilter(self, obj, e):  # noqa: N802
        if (
            obj is self.edit
            and e.type() == QEvent.Type.KeyPress
            and e.key() == Qt.Key.Key_Escape
        ):
            self._end_edit(save=False)
            return True
        return super().eventFilter(obj, e)

    # ---------- 右键菜单 ----------

    def _show_menu(self, pos) -> None:
        m = QMenu(self)
        if self.win.trash_mode:
            m.addAction("恢复", lambda: self.win.restore_memo(self.memo.id))
            m.addAction(
                "彻底删除",
                lambda: self.win.purge_memo(self.memo.id),
            )
        else:
            m.addAction(
                "取消置顶" if self.memo.is_pinned else "置顶",
                lambda: self.win.toggle_pin(self.memo.id),
            )
            m.addAction(
                "取消完成" if self.memo.is_done else "标记完成",
                lambda: self.win.toggle_done(self.memo.id),
            )
            m.addAction("分类…", lambda: self.win.assign_categories(self.memo.id))
            m.addAction("任务属性…", lambda: self.win.edit_task_props(self.memo.id))
            m.addAction("复制", lambda: QApplication.clipboard().setText(self.full_text))
            m.addSeparator()
            m.addAction("删除", lambda: self.win.delete_memo(self.memo.id))
        m.exec(self.mapToGlobal(pos))


class MainWindow(QMainWindow):
    memos_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        db.init_db()
        self.trash_mode = False
        self.tray_available = False
        self.controller = None
        self.selected_category: int | None = None
        self._memos: list[Memo] = []
        self._categories: list[Category] = []
        self._memo_cats: dict[int, set[int]] = {}
        self._cat_names: dict[int, str] = {}
        self._build_ui()
        self.reload()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.setWindowTitle("memo")
        self.setWindowIcon(make_icon(64))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.resize(860, 800)
        self.setMinimumSize(680, 520)
        self.setStyleSheet(QSS)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ===== 编辑菜单（挂在标题栏） =====
        edit_menu = QMenu(self)
        self.search_action = edit_menu.addAction("搜索备忘录")
        self.search_action.setCheckable(True)
        self.search_action.triggered.connect(self._toggle_search)
        edit_menu.addSeparator()
        self.trash_action = edit_menu.addAction("回收站")
        self.trash_action.setCheckable(True)
        self.trash_action.triggered.connect(self._toggle_trash)

        # ===== 自绘标题栏 =====
        self.titlebar = TitleBar(self, edit_menu)
        root.addWidget(self.titlebar)

        # ===== 主体：侧边栏 + 内容区 =====
        body_wrap = QWidget()
        body_lay = QVBoxLayout(body_wrap)
        body_lay.setContentsMargins(12, 10, 12, 12)
        body_lay.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(10)

        side = QWidget()
        side.setObjectName("sidePanel")
        side.setFixedWidth(170)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(8, 10, 8, 10)
        side_lay.setSpacing(2)

        self.side_rows = QVBoxLayout()
        self.side_rows.setSpacing(2)
        self.side_rows.setContentsMargins(0, 0, 0, 0)
        side_lay.addLayout(self.side_rows)
        side_lay.addStretch(1)

        self.add_cat_btn = QPushButton("+ 新建分类")
        self.add_cat_btn.setObjectName("addCatBtn")
        self.add_cat_btn.clicked.connect(self._add_category)
        side_lay.addWidget(self.add_cat_btn)
        body.addWidget(side)

        maincol = QVBoxLayout()
        maincol.setSpacing(8)

        # 搜索框（默认隐藏，由"编辑"菜单呼出）
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("搜索备忘录…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setVisible(False)
        maincol.addWidget(self.search_edit)

        self.input_edit = QLineEdit()
        self.input_edit.setObjectName("mainInput")
        self.input_edit.setPlaceholderText("想记点什么？回车保存 · 明天9点 设截止 · !高 @每周 提前30分钟 · 双击可编辑")
        maincol.addWidget(self.input_edit)

        # 回收站横幅：仅回收站模式显示（返回 + 计数 + 清空）
        self.trash_bar = QWidget()
        self.trash_bar.setObjectName("trashBar")
        self.trash_bar.setVisible(False)
        tbl = QHBoxLayout(self.trash_bar)
        tbl.setContentsMargins(12, 5, 10, 5)
        tbl.setSpacing(6)
        back_btn = QPushButton("← 返回")
        back_btn.setObjectName("trashBack")
        back_btn.clicked.connect(lambda: self._set_trash(False))
        tbl.addWidget(back_btn)
        self.trash_title = QLabel("")
        self.trash_title.setObjectName("trashTitle")
        tbl.addWidget(self.trash_title)
        tbl.addStretch(1)
        self.trash_purge_btn = QPushButton("清空回收站")
        self.trash_purge_btn.setObjectName("trashPurge")
        self.trash_purge_btn.clicked.connect(self._on_purge_all)
        tbl.addWidget(self.trash_purge_btn)
        maincol.addWidget(self.trash_bar)

        self.stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setSpacing(8)
        self.list.setUniformItemSizes(False)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.stack.addWidget(self.list)

        empty_wrap = QWidget()
        ev = QVBoxLayout(empty_wrap)
        ev.setContentsMargins(0, 0, 0, 0)
        empty_icon = QLabel()
        empty_icon.setPixmap(make_icon(56).pixmap(56, 56))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addStretch(1)
        ev.addWidget(empty_icon)
        ev.addSpacing(10)
        ev.addWidget(self.empty_label)
        ev.addStretch(2)
        self.stack.addWidget(empty_wrap)
        maincol.addWidget(self.stack, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        maincol.addLayout(bottom)

        body.addLayout(maincol, 1)
        body_lay.addLayout(body, 1)
        root.addWidget(body_wrap, 1)
        QApplication.instance().installEventFilter(self)

        # 信号
        self.input_edit.returnPressed.connect(self._on_add)
        self.search_edit.textChanged.connect(lambda _t: self.reload())
        self.list.itemSelectionChanged.connect(self._sync_selection)

        sc = QShortcut(QKeySequence.StandardKey.Delete, self.list)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._delete_selected)

    # ---------- 分类 ----------

    def _refresh_category_data(self) -> None:
        self._categories = db.list_categories()
        self._memo_cats = {} if self.trash_mode else db.memo_category_map()
        self._cat_names = {c.id: c.name for c in self._categories}
        if self.selected_category is not None and self.selected_category not in self._cat_names:
            self.selected_category = None

    def _rebuild_sidebar(self) -> None:
        while self.side_rows.count():
            item = self.side_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        all_row = CategoryRow(0, "全部")
        all_row.set_selected(self.selected_category is None)
        all_row.activated.connect(self._on_cat_select)
        self.side_rows.addWidget(all_row)

        for cat in self._categories:
            row = CategoryRow(cat.id, cat.name)
            row.set_selected(self.selected_category == cat.id)
            row.activated.connect(self._on_cat_select)
            row.delete_requested.connect(self._on_cat_delete)
            self.side_rows.addWidget(row)

    def _on_cat_select(self, cat_id: int) -> None:
        self.selected_category = None if cat_id == 0 else cat_id
        self.reload()

    def _on_cat_delete(self, cat_id: int) -> None:
        cat = next((c for c in self._categories if c.id == cat_id), None)
        name = cat.name if cat else "该分类"
        if QMessageBox.question(
            self, "删除分类",
            f"确定删除分类「{name}」吗？其中的备忘录将移入回收站。",
        ) == QMessageBox.StandardButton.Yes:
            db.delete_category(cat_id)
            if self.selected_category == cat_id:
                self.selected_category = None
            self.reload()

    def _add_category(self) -> None:
        dlg = NameDialog("新建分类", "分类名称：", self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            db.create_category(dlg.text())
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "新建分类", "同名分类已存在")
            return
        except ValueError as e:
            QMessageBox.warning(self, "新建分类", str(e))
            return
        self.reload()

    def _filter_by_category(self, memos: list[Memo]) -> list[Memo]:
        if self.selected_category is not None:
            return [
                m for m in memos
                if self.selected_category in self._memo_cats.get(m.id, set())
            ]
        return memos

    def visible_memos(self) -> list[Memo]:
        """桌面挂件用：非回收站的全部备忘录（不受侧边栏选中/回收站浏览影响；分组折叠由挂件自身控制）。"""
        return db.list_memos()

    def assign_categories(self, memo_id: int) -> None:
        dlg = CategoryPickerDialog(
            db.list_categories(), self._memo_cats.get(memo_id, set()), self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            db.set_memo_categories(memo_id, list(dlg.selected_ids()))
            self.reload()

    def edit_task_props(self, memo_id: int) -> None:
        memo = next((m for m in self._memos if m.id == memo_id), None)
        if memo is None:
            return
        dlg = TaskPropsDialog(memo, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            due_ts, priority, recur, advance = dlg.result_props()
            db.set_task_props(
                memo_id,
                due_ts=due_ts,
                priority=priority,
                recur=recur,
                remind_advance=advance,
            )
            self.reload()

    # ---------- 搜索 / 回收站 / 设置 ----------

    def _toggle_search(self, on: bool) -> None:
        self.search_edit.setVisible(on)
        self.search_action.setChecked(on)
        if on:
            self.search_edit.setFocus()
        else:
            self.search_edit.clear()
            self.reload()

    def _toggle_trash(self) -> None:
        self._set_trash(not self.trash_mode)

    def _set_trash(self, on: bool) -> None:
        self.trash_mode = on
        self.trash_action.setChecked(on)
        self.input_edit.setVisible(not on)
        self.search_edit.setPlaceholderText("搜索回收站…" if on else "搜索备忘录…")
        if on:
            self.search_edit.setVisible(False)
            self.search_action.setChecked(False)
        self.trash_bar.setVisible(on)
        self.reload()

    def _open_settings(self) -> None:
        if self.controller is not None:
            SettingsDialog(self.controller, self).exec()

    # ---------- 数据 ----------

    def reload(self) -> None:
        self._refresh_category_data()
        self._rebuild_sidebar()
        query = self.search_edit.text()
        memos = db.list_memos(trash=self.trash_mode, query=query)
        if not self.trash_mode:
            memos = self._filter_by_category(memos)
        self._memos = memos

        sb = self.list.verticalScrollBar()
        pos = sb.value()
        self.list.clear()
        for memo in memos:
            w = MemoItemWidget(memo, self)
            w.height_changed.connect(
                lambda h, mid=memo.id: self._sync_item_height(mid, h)
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, memo.id)
            # 宽度不指定（0=跟随视口）；若用文本理想宽度，窄窗口不换行且出横向滚动条
            item.setSizeHint(QSize(0, MemoItemWidget.HEIGHT))
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
        # 首帧布局完成后按实际换行高度校准各条目尺寸
        QTimer.singleShot(0, self._update_item_heights)
        QTimer.singleShot(0, lambda: sb.setValue(min(pos, sb.maximum())))
        self._sync_selection()

        if memos:
            self.stack.setCurrentWidget(self.list)
        else:
            if self.trash_mode:
                self.empty_label.setText("回收站是空的")
            elif query.strip():
                self.empty_label.setText("没有匹配的备忘录")
            elif self.selected_category is not None:
                self.empty_label.setText("该分类下暂无备忘录")
            else:
                self.empty_label.setText("暂无备忘录\n按快捷键随时记录，或在上方输入")
            self.stack.setCurrentWidget(self.stack.widget(1))
        self._update_status(len(memos))
        self.memos_changed.emit()

    def _sync_item_height(self, memo_id: int, h: int) -> None:
        """卡片高度变化时同步 QListWidgetItem 的尺寸提示。"""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == memo_id:
                sz = it.sizeHint()
                sz.setHeight(h)
                it.setSizeHint(sz)
                break

    def _update_item_heights(self) -> None:
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if isinstance(w, MemoItemWidget):
                w._update_height()

    def _sync_selection(self) -> None:
        current = self.list.currentItem()
        for i in range(self.list.count()):
            item = self.list.item(i)
            w = self.list.itemWidget(item)
            if isinstance(w, MemoItemWidget):
                w.set_selected(item is current)

    def _update_status(self, shown: int) -> None:
        if self.trash_mode:
            self.status_label.setText(f"回收站 {shown} 条")
            self.trash_title.setText(f"回收站 · {shown} 条备忘录")
        else:
            self.status_label.setText(f"共 {shown} 条")

    # ---------- 操作 ----------

    def add_memo_text(self, text: str, cat_ids: list[int] | None = None) -> int | None:
        text = text.strip()
        if not text:
            return None
        # NLP 解析："明天9点开会 !高 @每周 提前30分钟" -> 截止/优先级/重复/提醒
        due_ts, priority, recur, advance, cleaned = nlp_time.parse_task(text)
        if cleaned.strip():
            text = cleaned.strip()
        try:
            mid = db.add_memo(
                text,
                due_ts=due_ts,
                priority=priority,
                recur=recur,
                remind_advance=advance,
            )
        except sqlite3.Error:
            return None
        if cat_ids:
            db.set_memo_categories(mid, cat_ids)
        if self.trash_mode:
            self._set_trash(False)
        else:
            self.reload()
        return mid

    def _on_add(self) -> None:
        # 侧边栏选中的分类自动归入新备忘录
        cats = (
            [self.selected_category]
            if self.selected_category is not None
            else None
        )
        mid = self.add_memo_text(self.input_edit.text(), cats)
        if mid is not None:
            self.input_edit.clear()
            self.input_edit.setFocus()

    def _delete_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.delete_memo(int(item.data(Qt.ItemDataRole.UserRole)))

    def toggle_pin(self, memo_id: int) -> None:
        db.toggle_pin(memo_id)
        self.reload()

    def toggle_done(self, memo_id: int) -> None:
        db.toggle_done(memo_id)
        self.reload()

    def delete_memo(self, memo_id: int) -> None:
        db.soft_delete(memo_id)
        self.reload()

    def restore_memo(self, memo_id: int) -> None:
        db.restore(memo_id)
        self.reload()

    def edit_memo(self, memo_id: int, text: str) -> None:
        # 编辑时同样解析时间/标记；只更新识别到的字段，不清空既有属性
        due_ts, priority, recur, advance, cleaned = nlp_time.parse_task(text)
        if cleaned.strip():
            text = cleaned.strip()
        try:
            db.update_content(memo_id, text)
        except (ValueError, sqlite3.Error):
            return
        props = {}
        if due_ts is not None:
            props["due_ts"] = due_ts
        if priority:
            props["priority"] = priority
        if recur is not None:
            props["recur"] = recur
        if advance:
            props["remind_advance"] = advance
        if props:
            db.set_task_props(memo_id, **props)
        self.reload()

    def purge_memo(self, memo_id: int) -> None:
        if QMessageBox.question(
            self, "彻底删除", "确定要彻底删除这条备忘录吗？此操作不可恢复。"
        ) == QMessageBox.StandardButton.Yes:
            db.purge(memo_id)
            self.reload()

    def _on_purge_all(self) -> None:
        if QMessageBox.question(
            self, "清空回收站", "确定要清空回收站吗？所有条目将被彻底删除。"
        ) == QMessageBox.StandardButton.Yes:
            db.purge_all_trash()
            self.reload()

    # ---------- 无边框窗口：边缘缩放 / 标题栏拖动 ----------

    def eventFilter(self, obj, e):  # noqa: N802
        if (
            e.type() == QEvent.Type.MouseButtonPress
            and e.button() == Qt.MouseButton.LeftButton
        ):
            # 只处理主窗口自身的点击；托盘菜单/快捷弹窗/挂件是独立窗口，跳过
            w = QApplication.widgetAt(e.globalPosition().toPoint())
            if w is None or w.window() is not self:
                return False
            ht = self._hit_test(e.globalPosition().toPoint())
            if ht:
                gp = e.globalPosition().toPoint()
                user32 = ctypes.windll.user32
                user32.ReleaseCapture()
                user32.SendMessageW(
                    int(self.winId()), 0x00A1, ht, (gp.y() << 16) | (gp.x() & 0xFFFF)
                )
                return True
        return False

    def _hit_test(self, gp: QPoint) -> int:
        """返回 HT* 代码；0 表示不处理。"""
        f = self.frameGeometry()
        x, y = gp.x() - f.x(), gp.y() - f.y()
        B, W, H = 5, f.width(), f.height()
        if not self.isMaximized():
            if y < B:
                if x < B:
                    return 13  # HTTOPLEFT
                if x >= W - B:
                    return 14  # HTTOPRIGHT
                return 12  # HTTOP
            if y >= H - B:
                if x < B:
                    return 16  # HTBOTTOMLEFT
                if x >= W - B:
                    return 17  # HTBOTTOMRIGHT
                return 15  # HTBOTTOM
            if x < B:
                return 10  # HTLEFT
            if x >= W - B:
                return 11  # HTRIGHT
        # 标题栏区域 -> HTCAPTION（交给 Windows 原生拖动/双击最大化/贴靠）
        if y < 40:
            w = QApplication.widgetAt(gp)
            if w is not None and isinstance(
                w, (QAbstractButton, QMenu, QLineEdit, QListWidget, QStackedWidget)
            ):
                return 0
            return 2  # HTCAPTION
        return 0

    def changeEvent(self, e):  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and hasattr(self, "titlebar"):
            self.titlebar.sync_max()

    # ---------- 关闭行为 ----------

    def closeEvent(self, e):  # noqa: N802
        # 有托盘时关闭主窗口 = 隐藏到托盘，不退出程序
        if getattr(self, "tray_available", False):
            e.ignore()
            self.hide()
        else:
            super().closeEvent(e)
