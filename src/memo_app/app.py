"""PySide6 主界面：导航栏 + 分类侧边栏 + 卡片流 + 回收站。"""
from __future__ import annotations

import ctypes
import sqlite3
from datetime import datetime
from html import escape

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QButtonGroup,
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
    QScrollArea,
    QSpacerItem,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import db
from . import nlp_time
from .category_dialog import CategoryPickerDialog
from .db import Category, Memo
from .icons import make_icon, ui_icon
from .settings_dialog import SettingsDialog
from .task_dialog import TaskPropsDialog
from .theme import QSS


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


def _fmt_advance(minutes: int) -> str:
    """提前提醒分钟数的人性化展示：0->空, 60->1小时, 1440->1天。"""
    if not minutes:
        return ""
    if minutes % 1440 == 0:
        return f"（提前{minutes // 1440}天提醒）"
    if minutes % 60 == 0:
        return f"（提前{minutes // 60}小时提醒）"
    return f"（提前{minutes}分钟提醒）"


class _NavButton(QToolButton):
    """导航菜单按钮：点击或悬停展开 QMenu。"""

    def __init__(self, text: str, menu: QMenu, parent=None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(6)

        icon = QLabel()
        icon.setObjectName("tbIcon")
        icon.setPixmap(make_icon(18).pixmap(18, 18))
        lay.addWidget(icon)
        title = QLabel("memo  /  个人备忘录")
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

        self.min_btn = self._wbtn("minus", "winBtn", "最小化", win.showMinimized)
        self.max_btn = self._wbtn("maximize", "winBtn", "最大化/还原", self._toggle_max)
        close_btn = self._wbtn("close", "closeBtn", "关闭到托盘", win.close)
        for b in (self.min_btn, self.max_btn, close_btn):
            lay.addWidget(b)

    @staticmethod
    def _wbtn(icon, objname, tip, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(ui_icon(icon, size=14))
        b.setIconSize(QSize(14, 14))
        b.setObjectName(objname)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(36, 30)
        b.clicked.connect(slot)
        return b

    def _toggle_max(self) -> None:
        if self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()
        self.sync_max()

    def sync_max(self) -> None:
        self.max_btn.setIcon(ui_icon("restore_window" if self.win.isMaximized() else "maximize", size=14))

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
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        lay.addWidget(heading)
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

    def __init__(self, cat_id: int, name: str, count: int = 0):
        super().__init__()
        self.cat_id = cat_id
        self.full_name = name
        self.setObjectName("catRow")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{name}，{count} 条备忘录")
        self.setToolTip(name)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(9)

        icon = QLabel()
        icon.setPixmap(ui_icon("notes" if cat_id == 0 else "folder", "#6D8469", 16).pixmap(16, 16))
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(icon)

        self.name = QLabel(name)
        self.name.setObjectName("catName")
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.name.installEventFilter(self)
        lay.addWidget(self.name, 1)

        self.count_label = QLabel(str(count))
        self.count_label.setObjectName("catCount")
        self.count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.count_label)

        self.del_btn = QPushButton()
        self.del_btn.setIcon(ui_icon("close", "#9B523D", 12))
        self.del_btn.setObjectName("catDel")
        self.del_btn.setFixedSize(18, 18)
        self.del_btn.setAccessibleName(f"删除分类 {name}")
        self.del_btn.setToolTip("删除分类")
        self.del_btn.installEventFilter(self)
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

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.cat_id)
            return
        if e.key() == Qt.Key.Key_Delete and self.cat_id != 0:
            self.delete_requested.emit(self.cat_id)
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):  # noqa: N802
        self.del_btn.setVisible(self.cat_id != 0)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        QTimer.singleShot(0, self, self._sync_delete_visibility)
        super().focusOutEvent(e)

    def _sync_delete_visibility(self) -> None:
        self.del_btn.setVisible(
            self.cat_id != 0 and (self.underMouse() or self.hasFocus() or self.del_btn.hasFocus())
        )

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is getattr(self, "name", None) and e.type() == QEvent.Type.Resize:
            self.name.setText(self.name.fontMetrics().elidedText(
                self.full_name, Qt.TextElideMode.ElideRight, max(1, e.size().width())
            ))
        if obj is getattr(self, "del_btn", None) and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self, self._sync_delete_visibility)
        return super().eventFilter(obj, e)

    def enterEvent(self, e):  # noqa: N802
        self.del_btn.setVisible(self.cat_id != 0)
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._sync_delete_visibility()
        super().leaveEvent(e)


class MemoItemWidget(QWidget):
    """单条备忘录的展示控件（卡片样式，内容自动换行、高度自适应）。"""

    HEIGHT = 88
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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 15, 14, 13)
        lay.setSpacing(8)
        content_row = QHBoxLayout()
        self.content_row = content_row
        content_row.setSpacing(12)

        self.done_btn = QPushButton()
        self.done_btn.setObjectName("doneBtn")
        self.done_btn.setFixedSize(20, 20)
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.setFlat(True)
        self.done_btn.clicked.connect(
            lambda: self.win.toggle_done(self.memo.id)
        )
        content_row.addWidget(self.done_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 内容区：label / 编辑框 叠放
        self.host_stack = QStackedWidget()
        self.host_stack.setMinimumWidth(0)
        self.host_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.content_label = QLabel()
        self.content_label.setObjectName("memoContent")
        self.content_label.setTextFormat(Qt.TextFormat.PlainText)
        self.content_label.setWordWrap(True)
        self.content_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        pl.addWidget(self.content_label)
        self.host_stack.addWidget(page)
        self.edit = QLineEdit()
        self.edit.setObjectName("inlineEdit")
        self.edit.setAccessibleName("编辑备忘录内容")
        self.edit.returnPressed.connect(lambda: self._end_edit(save=True))
        self.edit.editingFinished.connect(self._end_edit)
        self.edit.installEventFilter(self)
        self.host_stack.addWidget(self.edit)
        content_row.addWidget(self.host_stack, 1)

        # 删除按钮：常显暖灰，悬停变红
        self.del_btn = QPushButton()
        self.del_btn.setObjectName("deleteAction")
        self.del_btn.setIcon(ui_icon("trash", "#8B8172", 15))
        self.del_btn.setFixedSize(28, 28)
        self.del_btn.setToolTip("移到回收站")
        self.del_btn.setAccessibleName("移到回收站")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.setFlat(True)
        self.del_btn.clicked.connect(lambda: self.win.delete_memo(self.memo.id))

        # 任务属性按钮：截止/优先级/重复/提醒
        self.props_btn = QPushButton()
        self.props_btn.setObjectName("cardAction")
        self.props_btn.setIcon(ui_icon("sliders", size=16))
        self.props_btn.setFixedSize(28, 28)
        self.props_btn.setToolTip("截止时间、优先级与提醒")
        self.props_btn.setAccessibleName("任务属性")
        self.props_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.props_btn.setFlat(True)
        self.props_btn.clicked.connect(
            lambda: self.win.edit_task_props(self.memo.id)
        )
        content_row.addWidget(self.props_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        content_row.addWidget(self.del_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 回收站模式：显示"恢复"按钮
        self.restore_btn = QPushButton()
        self.restore_btn.setObjectName("cardAction")
        self.restore_btn.setIcon(ui_icon("restore", "#2D6550", 16))
        self.restore_btn.setFixedSize(28, 28)
        self.restore_btn.setToolTip("恢复备忘录")
        self.restore_btn.setAccessibleName("恢复备忘录")
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.setFlat(True)
        self.restore_btn.setVisible(False)
        self.restore_btn.clicked.connect(lambda: self.win.restore_memo(self.memo.id))
        content_row.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(content_row)

        # 右侧元信息：置顶图钉 + 相对时间 + 分类标签
        self.meta = QHBoxLayout()
        self.meta.setContentsMargins(32, 0, 0, 0)
        self.meta.setSpacing(12)
        self.pin_label = QLabel("置顶")
        self.pin_label.setObjectName("memoPin")
        self.time_label = QLabel()
        self.time_label.setObjectName("memoTime")
        self.tags_label = QLabel()
        self.tags_label.setObjectName("memoTags")
        self.tags_label.setTextFormat(Qt.TextFormat.PlainText)
        self.tags_label.setWordWrap(True)
        self.tags_label.setMinimumWidth(0)
        self.tags_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.tags_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.badges_label = QLabel()
        self.badges_label.setObjectName("memoBadges")
        self.badges_label.setContentsMargins(32, 0, 0, 0)
        self.badges_label.setWordWrap(True)
        self.meta.addWidget(self.pin_label)
        self.meta.addWidget(self.time_label)
        self.meta.addWidget(self.tags_label, 1)
        lay.addLayout(self.meta)
        lay.addWidget(self.badges_label)

    # ---------- 状态 ----------

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_state(self) -> None:
        # 高优先级卡片淡红底色提示
        self.setProperty("prioHigh", "true" if self.memo.priority >= 3 else "false")
        self.setProperty("completed", self.memo.is_done)
        self.style().unpolish(self)
        self.style().polish(self)

        done = self.memo.is_done
        self.done_btn.setProperty("done", "true" if done else "false")
        self.done_btn.setIcon(ui_icon("check", "#FFFFFF", 14) if done else QIcon())
        self.done_btn.setIconSize(QSize(14, 14))
        self.done_btn.setAccessibleName("取消完成" if done else "标记完成")
        self.done_btn.setToolTip("取消完成" if done else "标记完成")
        self.done_btn.style().unpolish(self.done_btn)
        self.done_btn.style().polish(self.done_btn)

        font = self.content_label.font()
        font.setStrikeOut(done)
        self.content_label.setFont(font)
        self.content_label.setProperty("completed", done)
        self.content_label.style().unpolish(self.content_label)
        self.content_label.style().polish(self.content_label)

        self.content_label.setText(self.full_text)
        self.content_label.setToolTip(self.full_text)
        self.pin_label.setVisible(self.memo.is_pinned)
        self.time_label.setText(rel_time(self.memo.updated_at))

        # 分类标签
        cat_ids = self.win._memo_cats.get(self.memo.id, set())
        names = [self.win._cat_names[c] for c in sorted(cat_ids) if c in self.win._cat_names]
        self.tags_label.setText(" ".join(f"#{n}" for n in names))
        self.tags_label.setToolTip(" / ".join(names))
        self.tags_label.setVisible(bool(names))

        # 截止/优先级/重复徽标
        self.badges_label.setText(self._badges_html())
        self.badges_label.setVisible(bool(self.badges_label.text()))
        self.badges_label.setToolTip(self._badges_tooltip())

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
        alignment = Qt.AlignmentFlag.AlignTop if br.height() > 35 else Qt.AlignmentFlag.AlignVCenter
        for button in (self.done_btn, self.props_btn, self.del_btn, self.restore_btn):
            self.content_row.setAlignment(button, alignment)
        meta_h = max(self.time_label.sizeHint().height(),
                     self.tags_label.heightForWidth(max(1, self.tags_label.width())) if self.tags_label.isVisible() else 0)
        badge_h = 0
        if not self.badges_label.isHidden():
            badge_h = self.badges_label.heightForWidth(max(1, self.width() - 32)) + 8
        h = max(self.HEIGHT, max(br.height(), 28) + meta_h + badge_h + 36)
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
            color = "#AC503B" if overdue else ("#8D6339" if today else "#69766E")
            prefix = "已逾期 · " if overdue else "截止 · "
            parts.append(
                f'<span style="color:{color}">{prefix}{nlp_time.format_due(m.due_ts)}</span>'
            )
        if 0 < m.priority <= 3:
            pcolor = {1: "#69766E", 2: "#8D6339", 3: "#AC503B"}[m.priority]
            parts.append(
                f'<span style="color:{pcolor}">{db.PRIORITY_LABELS[m.priority]}优先级</span>'
            )
        if m.recur:
            parts.append(f'<span style="color:#69766E">{escape(db.RECUR_LABELS.get(m.recur, m.recur))}</span>')
        return " &nbsp; / &nbsp; ".join(parts)

    def _badges_tooltip(self) -> str:
        """徽标悬停提示：完整截止时间 + 优先级 + 重复 + 提前量。"""
        m = self.memo
        tips = []
        if m.due_ts:
            exact = datetime.fromtimestamp(m.due_ts).strftime("%Y-%m-%d %H:%M")
            tips.append(f"截止 {exact}{_fmt_advance(m.remind_advance)}")
        if 0 < m.priority <= 3:
            tips.append(f"优先级：{db.PRIORITY_LABELS[m.priority]}")
        if m.recur:
            tips.append(f"重复：{db.RECUR_LABELS.get(m.recur, m.recur)}")
        return "\n".join(tips)

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
        self.status_filter = "all"
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
        self.resize(1080, 800)
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
        self.search_action.setShortcut(QKeySequence.StandardKey.Find)
        self.search_action.triggered.connect(lambda: self._toggle_search(True))
        self.addAction(self.search_action)
        edit_menu.addSeparator()
        self.trash_action = edit_menu.addAction("回收站")
        self.trash_action.setCheckable(True)
        self.trash_action.triggered.connect(self._toggle_trash)

        # ===== 自绘标题栏 =====
        self.titlebar = TitleBar(self, edit_menu)
        root.addWidget(self.titlebar)

        # ===== 主体：侧边栏 + 内容区 =====
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.side = QWidget()
        self.side.setObjectName("sidePanel")
        self.side.setFixedWidth(214)
        side_lay = QVBoxLayout(self.side)
        self.side_layout = side_lay
        side_lay.setContentsMargins(16, 24, 16, 16)
        side_lay.setSpacing(8)

        brand = QHBoxLayout()
        brand.setContentsMargins(10, 0, 0, 0)
        mark = QLabel()
        mark.setPixmap(make_icon(32).pixmap(32, 32))
        brand.addWidget(mark)
        wordmark = QLabel("memo")
        wordmark.setObjectName("wordmark")
        brand.addWidget(wordmark)
        brand.addStretch(1)
        side_lay.addLayout(brand)
        workspace = QLabel("留住想法，轻装前行。")
        self.workspace_label = workspace
        workspace.setObjectName("workspaceLabel")
        workspace.setContentsMargins(10, 4, 0, 18)
        side_lay.addWidget(workspace)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_host = QWidget()

        self.side_rows = QVBoxLayout(rows_host)
        self.side_rows.setSpacing(4)
        self.side_rows.setContentsMargins(0, 0, 0, 0)
        self.side_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(rows_host)
        side_lay.addWidget(scroll, 1)

        self.add_cat_btn = QPushButton("  新建分类")
        self.add_cat_btn.setIcon(ui_icon("plus", size=15))
        self.add_cat_btn.setObjectName("addCatBtn")
        self.add_cat_btn.clicked.connect(self._add_category)
        side_lay.addWidget(self.add_cat_btn)
        self.trash_btn = QPushButton("  回收站")
        self.trash_btn.setIcon(ui_icon("trash", size=16))
        self.trash_btn.setObjectName("sideNav")
        self.trash_btn.setCheckable(True)
        self.trash_btn.clicked.connect(self._set_trash)
        side_lay.addWidget(self.trash_btn)
        settings_btn = QPushButton("  设置")
        settings_btn.setIcon(ui_icon("sliders", size=16))
        settings_btn.setObjectName("sideNav")
        settings_btn.clicked.connect(self._open_settings)
        side_lay.addWidget(settings_btn)

        local_card = QWidget()
        self.local_card = local_card
        local_card.setObjectName("localCard")
        local_lay = QVBoxLayout(local_card)
        local_lay.setContentsMargins(12, 12, 12, 12)
        local_lay.setSpacing(5)
        local_title = QLabel("你的记录，只属于你")
        local_title.setObjectName("localTitle")
        local_hint = QLabel("本地存储  /  无需联网")
        local_hint.setObjectName("localHint")
        local_lay.addWidget(local_title)
        local_lay.addWidget(local_hint)
        self.local_card_gap = QSpacerItem(0, 8, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        side_lay.addItem(self.local_card_gap)
        side_lay.addWidget(local_card)
        body.addWidget(self.side)

        content = QWidget()
        content.setObjectName("contentPanel")
        maincol = QVBoxLayout(content)
        self.content_layout = maincol
        maincol.setContentsMargins(36, 26, 36, 18)
        maincol.setSpacing(0)
        self._vertical_gaps = []

        def gap(normal: int, compact: int) -> None:
            spacer = QSpacerItem(0, normal, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            maincol.addItem(spacer)
            self._vertical_gaps.append((spacer, normal, compact))

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("搜索备忘录")
        self.search_edit.setAccessibleName("搜索备忘录")
        self.search_edit.installEventFilter(self)
        self.search_edit.addAction(ui_icon("search", size=15), QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(212)
        self.search_edit.setToolTip("搜索备忘录 (Ctrl+F)")
        heading = QHBoxLayout()
        heading.setSpacing(16)
        self.page_title = QLabel("所有备忘录")
        self.page_title.setTextFormat(Qt.TextFormat.PlainText)
        self.page_title.setObjectName("pageTitle")
        self.page_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.page_title.setMinimumWidth(0)
        heading.addWidget(self.page_title, 1)
        heading.addWidget(self.search_edit)
        maincol.addLayout(heading)
        gap(6, 0)

        subheading = QHBoxLayout()
        self.page_subtitle = QLabel("把琐事放在这里，把注意力留给生活。")
        self.page_subtitle.setObjectName("pageSubtitle")
        self.page_subtitle.setWordWrap(True)
        subheading.addWidget(self.page_subtitle, 1)
        self.date_label = QLabel()
        self.date_label.setObjectName("dateLabel")
        subheading.addWidget(self.date_label)
        maincol.addLayout(subheading)
        gap(22, 14)

        self.composer = QWidget()
        self.composer.setObjectName("composer")
        composer_lay = QVBoxLayout(self.composer)
        composer_lay.setContentsMargins(16, 10, 12, 10)
        composer_lay.setSpacing(4)
        input_row = QHBoxLayout()
        input_row.setSpacing(12)
        compose_icon = QLabel()
        compose_icon.setPixmap(ui_icon("note", "#6D8469", 22).pixmap(22, 22))
        input_row.addWidget(compose_icon)
        self.input_edit = QLineEdit()
        self.input_edit.setObjectName("mainInput")
        self.input_edit.setPlaceholderText("此刻，有什么想记下来？")
        self.input_edit.setAccessibleName("新备忘录内容")
        self.input_edit.installEventFilter(self)
        input_row.addWidget(self.input_edit, 1)
        self.save_btn = QPushButton("记录")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_add)
        input_row.addWidget(self.save_btn)
        composer_lay.addLayout(input_row)
        self.composer_hint = QLabel("Enter 保存  ·  支持「明天9点」设提醒")
        self.composer_hint.setObjectName("composerHint")
        self.composer_hint.setWordWrap(True)
        self.composer_hint.setContentsMargins(34, 0, 0, 0)
        self.composer_hint.setToolTip("例如：明天9点开会 !高 @每周 提前30分钟\n双击记录编辑，右键可置顶、分类或复制")
        composer_lay.addWidget(self.composer_hint)
        maincol.addWidget(self.composer)
        gap(18, 14)

        filters = QHBoxLayout()
        self.filter_group = QWidget()
        self.filter_group.setObjectName("filterGroup")
        filter_lay = QHBoxLayout(self.filter_group)
        filter_lay.setContentsMargins(3, 3, 3, 3)
        filter_lay.setSpacing(2)
        self.filter_buttons = QButtonGroup(self)
        for key, label in (("all", "全部"), ("pinned", "已置顶"), ("scheduled", "有提醒")):
            btn = QPushButton(label)
            btn.setObjectName("filterBtn")
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.clicked.connect(lambda _checked=False, k=key: self._set_status_filter(k))
            self.filter_buttons.addButton(btn)
            filter_lay.addWidget(btn)
        filters.addWidget(self.filter_group)
        filters.addStretch(1)
        self.list_heading = QLabel("最近记录")
        self.list_heading.setObjectName("listHeading")
        filters.addWidget(self.list_heading)
        maincol.addLayout(filters)
        gap(12, 10)

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
        self.list.setSpacing(5)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(False)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.stack.addWidget(self.list)

        empty_wrap = QWidget()
        ev = QVBoxLayout(empty_wrap)
        ev.setContentsMargins(0, 0, 0, 0)
        empty_icon = QLabel()
        empty_icon.setPixmap(ui_icon("note", "#8EA585", 56).pixmap(56, 56))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel("给想法一个落脚点")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        ev.addStretch(1)
        ev.addWidget(empty_icon)
        ev.addSpacing(18)
        ev.addWidget(self.empty_title)
        ev.addSpacing(6)
        ev.addWidget(self.empty_label)
        ev.addStretch(2)
        self.stack.addWidget(empty_wrap)
        maincol.addWidget(self.stack, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        self.footer_hint = QLabel("双击编辑  /  右键更多")
        self.footer_hint.setObjectName("statusLabel")
        bottom.addWidget(self.footer_hint)
        gap(10, 8)
        maincol.addLayout(bottom)

        body.addWidget(content, 1)
        root.addLayout(body, 1)
        QApplication.instance().installEventFilter(self)
        self._date_timer = QTimer(self)
        self._date_timer.setInterval(60_000)
        self._date_timer.timeout.connect(self._update_date)
        self._date_timer.start()
        self._update_date()
        for button in self.findChildren(QAbstractButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)

        # 信号
        self.input_edit.returnPressed.connect(self._on_add)
        self.input_edit.textChanged.connect(lambda t: self.save_btn.setEnabled(bool(t.strip())))
        self.search_edit.textChanged.connect(lambda _t: self.reload())
        self.list.itemSelectionChanged.connect(self._sync_selection)

        sc = QShortcut(QKeySequence.StandardKey.Delete, self.list)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._delete_selected)

    # ---------- 分类 ----------

    def _refresh_category_data(self) -> None:
        self._categories = db.list_categories()
        self._memo_cats = db.memo_category_map()
        self._all_memos = db.list_memos()
        self._cat_names = {c.id: c.name for c in self._categories}
        if self.selected_category is not None and self.selected_category not in self._cat_names:
            self.selected_category = None

    def _rebuild_sidebar(self) -> None:
        counts = {}
        for memo in self._all_memos:
            for cat_id in self._memo_cats.get(memo.id, set()):
                counts[cat_id] = counts.get(cat_id, 0) + 1
        signature = (
            tuple((c.id, c.name, counts.get(c.id, 0)) for c in self._categories),
            len(self._all_memos), self.selected_category, self.trash_mode,
        )
        if signature == getattr(self, "_sidebar_signature", None):
            return
        self._sidebar_signature = signature
        focused = QApplication.focusWidget()
        focused_cat = focused.cat_id if isinstance(focused, CategoryRow) else None
        while self.side_rows.count():
            item = self.side_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        all_row = CategoryRow(0, "所有备忘录", len(self._all_memos))
        all_row.set_selected(self.selected_category is None and not self.trash_mode)
        all_row.activated.connect(self._on_cat_select)
        self.side_rows.addWidget(all_row)
        if focused_cat == 0:
            QTimer.singleShot(0, all_row, all_row.setFocus)

        section = QLabel("我的分类")
        section.setObjectName("sectionLabel")
        section.setContentsMargins(0, 20, 0, 8)
        self.side_rows.addWidget(section)
        for cat in self._categories:
            row = CategoryRow(cat.id, cat.name, counts.get(cat.id, 0))
            row.set_selected(self.selected_category == cat.id and not self.trash_mode)
            row.activated.connect(self._on_cat_select)
            row.delete_requested.connect(self._on_cat_delete)
            self.side_rows.addWidget(row)
            if focused_cat == cat.id:
                QTimer.singleShot(0, row, row.setFocus)

    def _on_cat_select(self, cat_id: int) -> None:
        self.selected_category = None if cat_id == 0 else cat_id
        if self.trash_mode:
            self._set_trash(False)
        else:
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
        if on:
            self.search_edit.setFocus()
            self.search_edit.selectAll()
        else:
            self.search_edit.clear()

    def _set_status_filter(self, key: str) -> None:
        self.status_filter = key
        self.reload()

    def _update_date(self) -> None:
        now = datetime.now()
        weekday = "一二三四五六日"[now.weekday()]
        self.date_label.setText(f"{now.month}月{now.day}日  星期{weekday}")

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if not hasattr(self, "side"):
            return
        compact = self.width() < 880
        self.side.setFixedWidth(174 if compact else 214)
        margin = 20 if compact else 36
        self.content_layout.setContentsMargins(margin, 20 if compact else 26, margin, 18)
        self.search_edit.setFixedWidth(170 if compact else 212)
        self.page_subtitle.setVisible(not compact)
        self.workspace_label.setVisible(not compact)
        self.local_card.setVisible(not compact)
        self.local_card_gap.changeSize(0, 0 if compact else 8,
                                      QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if compact:
            self.side_layout.setContentsMargins(12, 16, 12, 12)
        else:
            self.side_layout.setContentsMargins(16, 24, 16, 16)
        self.date_label.setVisible(not compact)
        self.footer_hint.setVisible(not compact)
        self.page_title.setProperty("compact", compact)
        self.page_title.style().unpolish(self.page_title)
        self.page_title.style().polish(self.page_title)
        for spacer, normal, small in self._vertical_gaps:
            spacer.changeSize(0, small if compact else normal,
                              QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.content_layout.invalidate()

    def _toggle_trash(self) -> None:
        self._set_trash(not self.trash_mode)

    def _set_trash(self, on: bool) -> None:
        self.trash_mode = on
        self.trash_action.setChecked(on)
        self.trash_btn.setChecked(on)
        self.composer.setVisible(not on)
        self.filter_group.setVisible(not on)
        self.search_edit.setPlaceholderText("搜索回收站" if on else "搜索备忘录")
        self.search_edit.setAccessibleName("搜索回收站" if on else "搜索备忘录")
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.trash_bar.setVisible(on)
        self.footer_hint.setText("右键恢复  /  Delete 彻底删除" if on else "双击编辑  /  右键更多")
        self.reload()

    def _open_settings(self) -> None:
        if self.controller is not None:
            SettingsDialog(self.controller, self).exec()

    # ---------- 数据 ----------

    def reload(self) -> None:
        self._refresh_category_data()
        self._rebuild_sidebar()
        query = self.search_edit.text()
        memos = db.list_memos(trash=self.trash_mode, query=query) if self.trash_mode or query else self._all_memos
        if not self.trash_mode:
            memos = self._filter_by_category(memos)
            if self.status_filter == "pinned":
                memos = [m for m in memos if m.is_pinned]
            elif self.status_filter == "scheduled":
                memos = [m for m in memos if m.due_ts is not None]
        self._memos = memos
        title = "回收站" if self.trash_mode else self._cat_names.get(self.selected_category, "所有备忘录")
        self.page_title.setText(title)
        self.page_title.setToolTip(title)
        self.page_subtitle.setText(
            "暂时告别的记录，也可以重新找回。" if self.trash_mode
            else "把琐事放在这里，把注意力留给生活。"
        )

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
                self.empty_title.setText("这里干干净净")
                self.empty_label.setText("删除的备忘录会暂存在这里，方便随时恢复。")
            elif query.strip():
                self.empty_title.setText("没有找到相关记录")
                self.empty_label.setText("换个关键词，或清空搜索再看看。")
            elif self.status_filter != "all":
                self.empty_title.setText("暂时没有" + ("置顶的记录" if self.status_filter == "pinned" else "设置提醒的记录"))
                self.empty_label.setText("切换到「全部」，查看这个分类的所有记录。")
            elif self.selected_category is not None:
                self.empty_title.setText("为这个分类写下第一条")
                self.empty_label.setText("在上方随手记，新记录会自动归入当前分类。")
            else:
                self.empty_title.setText("给想法一个落脚点")
                self.empty_label.setText("一句灵感、一件小事，从上方写下你的第一条记录。")
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
        self.list_heading.setText(f"{'已删除' if self.trash_mode else '最近记录'}  ·  {shown}")
        if self.trash_mode:
            self.status_label.setText(f"回收站 {shown} 条")
            self.trash_title.setText(f"回收站 · {shown} 条备忘录")
        else:
            self.status_label.setText(f"{shown} 条记录  ·  本地存储")

    # ---------- 操作 ----------

    def add_memo_text(
        self,
        text: str,
        cat_ids: list[int] | None = None,
        cat_name: str | None = None,
    ) -> int | None:
        text = text.strip()
        if not text:
            return None
        # NLP 解析："明天9点开会 !高 @每周 提前30分钟" -> 截止/优先级/重复/提醒
        due_ts, priority, recur, advance, cleaned = nlp_time.parse_task(text)
        if cleaned.strip():
            text = cleaned.strip()
        try:
            # cat_ids/cat_name 与记录同事务提交（同步原子性）
            mid = db.add_memo(
                text,
                due_ts=due_ts,
                priority=priority,
                recur=recur,
                remind_advance=advance,
                cat_ids=cat_ids,
                cat_name=cat_name,
            )
        except (sqlite3.Error, ValueError):
            return None
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
            if self.status_filter != "all" or self.search_edit.text():
                self.status_filter = "all"
                self.filter_buttons.buttons()[0].setChecked(True)
                self.search_edit.blockSignals(True)
                self.search_edit.clear()
                self.search_edit.blockSignals(False)
                self.reload()
            self.input_edit.clear()
            self.input_edit.setFocus()

    def _delete_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        memo_id = int(item.data(Qt.ItemDataRole.UserRole))
        if self.trash_mode:
            self.purge_memo(memo_id)
        else:
            self.delete_memo(memo_id)

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
        props = {}
        if due_ts is not None:
            props["due_ts"] = due_ts
        if priority:
            props["priority"] = priority
        if recur is not None:
            props["recur"] = recur
        if advance:
            props["remind_advance"] = advance
        try:
            # 正文+属性单事务、单条同步修订
            db.edit_memo(memo_id, text, props)
        except (ValueError, sqlite3.Error):
            return
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
        if obj is getattr(self, "input_edit", None) and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.composer.setProperty("active", e.type() == QEvent.Type.FocusIn)
            self.composer.style().unpolish(self.composer)
            self.composer.style().polish(self.composer)
        if obj is getattr(self, "search_edit", None) and e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.search_edit.clear()
            (self.list if self.trash_mode else self.input_edit).setFocus()
            return True
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
