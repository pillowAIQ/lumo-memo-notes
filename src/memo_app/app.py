"""PySide6 主界面：导航栏 + 分类侧边栏 + 卡片流 + 回收站。"""
from __future__ import annotations

import ctypes
import sqlite3
from datetime import datetime
from html import escape

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
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
from . import effects
from . import nlp_time
from .category_dialog import CategoryPickerDialog
from .db import Category, Memo
from .icons import make_icon, mark_pixmap, ui_icon
from .settings_dialog import SettingsView
from .task_dialog import TaskPropsDialog
from . import theme


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
    """自绘标题栏：品牌 + 居中搜索（仿编辑器命令栏）+ 同步状态 + 窗口控制。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(6)

        self.icon = QLabel()
        self.icon.setPixmap(mark_pixmap(18))
        lay.addWidget(self.icon)
        title = QLabel(f"{theme.APP_NAME}  {theme.APP_NAME_EN}")
        title.setObjectName("tbTitle")
        lay.addWidget(title)
        lay.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("titleSearch")
        self.search.setPlaceholderText("搜索笔记    Ctrl+F")
        self.search.setAccessibleName("搜索备忘录")
        self.search.setToolTip("搜索备忘录 (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(340)
        self.search_action = self.search.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        lay.addWidget(self.search)
        lay.addStretch(1)

        # 多设备同步入口（仿 Obsidian Sync：导航栏一键唤出状态菜单）
        self.sync_btn = QToolButton()
        self.sync_btn.setText("同步")
        self.sync_btn.setIconSize(QSize(14, 14))
        self.sync_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sync_btn.setObjectName("navBtn")
        self.sync_btn.setToolTip("多设备同步")
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.clicked.connect(lambda: win._open_sync_menu(self.sync_btn))
        lay.addWidget(self.sync_btn)
        lay.addSpacing(6)

        self.min_btn = self._wbtn("winBtn", "最小化", win.showMinimized)
        self.max_btn = self._wbtn("winBtn", "最大化/还原", self._toggle_max)
        self.close_btn = self._wbtn("closeBtn", "关闭到托盘", win.close)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            lay.addWidget(b)
        self.retheme()

    @staticmethod
    def _wbtn(objname, tip, slot) -> QToolButton:
        b = QToolButton()
        b.setIconSize(QSize(14, 14))
        b.setObjectName(objname)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(38, 30)
        b.clicked.connect(slot)
        return b

    def retheme(self) -> None:
        muted = theme.c("text_2")
        self.search_action.setIcon(ui_icon("search", theme.c("muted"), 15))
        self.min_btn.setIcon(ui_icon("minus", muted, 14))
        effects.hover_icon(self.close_btn, ui_icon("close", muted, 14), ui_icon("close", "#FFFFFF", 14))
        self.sync_max()

    def _toggle_max(self) -> None:
        if self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()
        self.sync_max()

    def sync_max(self) -> None:
        name = "restore_window" if self.win.isMaximized() else "maximize"
        self.max_btn.setIcon(ui_icon(name, theme.c("text_2"), 14))

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
        icon.setPixmap(ui_icon("notes" if cat_id == 0 else "folder", theme.c("muted"), 16).pixmap(16, 16))
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
        self.count_label.setFixedHeight(18)
        self.count_label.setMinimumWidth(18)
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.del_btn = QPushButton()
        self.del_btn.setIcon(ui_icon("close", theme.c("danger"), 12))
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

    HEIGHT = 90
    height_changed = Signal(int)

    def __init__(self, memo: Memo, win: "MainWindow"):
        super().__init__()
        self.memo = memo
        self.win = win
        self.full_text = memo.content
        self._editing = False
        self.setObjectName("memoItem")
        self.setProperty("selected", False)
        # 卡片背景/边框/投影在 paintEvent 手绘，以便悬停浮起与高亮做平滑过渡
        self._hover_t = 0.0
        self._flash_t = 0.0
        self._hover_fx = QObject(self)
        self._flash_fx = QObject(self)
        self._build()
        self._apply_state()
        self.setMinimumHeight(self.HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        QTimer.singleShot(0, self, self._update_height)

    # ---------- 构建 ----------

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 15, 14, 15)
        lay.setSpacing(8)
        content_row = QHBoxLayout()
        self.content_row = content_row
        content_row.setSpacing(12)

        self.done_btn = QPushButton()
        self.done_btn.setObjectName("doneBtn")
        self.done_btn.setFixedSize(20, 20)
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.setFlat(True)
        self.done_btn.clicked.connect(self._on_done_clicked)
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
        self.del_btn.setFixedSize(28, 28)
        self.del_btn.setToolTip("移到回收站")
        self.del_btn.setAccessibleName("移到回收站")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.setFlat(True)
        self.del_btn.clicked.connect(lambda: self.win.delete_memo(self.memo.id))

        # 任务属性按钮：截止/优先级/重复/提醒
        self.props_btn = QPushButton()
        self.props_btn.setObjectName("cardAction")
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
        self.restore_btn.setFixedSize(28, 28)
        self.restore_btn.setToolTip("恢复备忘录")
        self.restore_btn.setAccessibleName("恢复备忘录")
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.setFlat(True)
        self.restore_btn.setVisible(False)
        self.restore_btn.clicked.connect(lambda: self.win.restore_memo(self.memo.id))
        content_row.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(content_row)
        effects.hover_icon(self.restore_btn, ui_icon("restore", theme.c("accent"), 16), ui_icon("restore", theme.c("accent_press"), 16))
        self._sync_action_icons(False)

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
        if self.property("selected") != on:
            self.setProperty("selected", on)
            self.update()

    def _sync_action_icons(self, card_hovered: bool) -> None:
        # 卡片静止时操作图标更轻，悬停卡片时加深，悬停按钮本身再强调
        idle = theme.c("text_2" if card_hovered else "faint")
        effects.hover_icon(self.props_btn, ui_icon("sliders", idle, 16), ui_icon("sliders", theme.c("accent"), 16))
        effects.hover_icon(self.del_btn, ui_icon("trash", idle, 15), ui_icon("trash", theme.c("danger"), 15))

    def _on_done_clicked(self) -> None:
        if not self.memo.is_done:
            # 先让圆圈填充打勾，给出即时反馈，再淡出卡片
            self.done_btn.setProperty("done", "true")
            check = ui_icon("check", theme.c("on_accent"), 14)
            effects.hover_icon(self.done_btn, check, check)
            self.done_btn.style().unpolish(self.done_btn)
            self.done_btn.style().polish(self.done_btn)
        self.win.toggle_done(self.memo.id)

    def _apply_state(self) -> None:
        # 高优先级卡片淡红底色提示
        self.setProperty("prioHigh", "true" if self.memo.priority >= 3 else "false")
        self.setProperty("completed", self.memo.is_done)
        self.style().unpolish(self)
        self.style().polish(self)

        done = self.memo.is_done
        self.done_btn.setProperty("done", "true" if done else "false")
        if done:
            check = ui_icon("check", theme.c("on_accent"), 14)
            effects.hover_icon(self.done_btn, check, check)
        else:
            effects.hover_icon(self.done_btn, QIcon(), ui_icon("check", theme.c("accent"), 14))
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
        h = max(self.HEIGHT, max(br.height(), 28) + meta_h + badge_h + 38)
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
            color = theme.c("overdue") if overdue else theme.c("warn" if today else "muted")
            prefix = "已逾期 · " if overdue else "截止 · "
            parts.append(
                f'<span style="color:{color}">{prefix}{nlp_time.format_due(m.due_ts)}</span>'
            )
        if 0 < m.priority <= 3:
            pcolor = theme.c({1: "muted", 2: "warn", 3: "overdue"}[m.priority])
            parts.append(
                f'<span style="color:{pcolor}">{db.PRIORITY_LABELS[m.priority]}优先级</span>'
            )
        if m.recur:
            parts.append(f'<span style="color:{theme.c("muted")}">{escape(db.RECUR_LABELS.get(m.recur, m.recur))}</span>')
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

    # ---------- 绘制 / 动效 ----------

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h, f = self._hover_t, self._flash_t
        selected = bool(self.property("selected"))
        card = QRectF(self.rect()).adjusted(1.5, 1.0, -1.5, -3.5)
        # 底部柔和投影：静止时几乎不可见，悬停时轻微浮起
        boost = 2.2 if theme.is_dark() else 1.0
        for dy, rest, lifted in ((3.0, 5, 13), (2.0, 7, 15), (1.0, 9, 17)):
            shadow = QPainterPath()
            shadow.addRoundedRect(card.translated(0, dy), 10, 10)
            p.fillPath(shadow, theme.qc("shadow", round((rest + (lifted - rest) * h) * boost)))
        path = QPainterPath()
        path.addRoundedRect(card, 10, 10)
        if self.property("completed"):
            bg = theme.qc("surface_muted")
        else:
            bg = theme.qc("selected" if selected else "surface")
        p.fillPath(path, effects.mix(bg, theme.c("flash"), f * 0.9))
        if self.property("prioHigh") == "true":
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(card.left(), card.top(), 3.5, card.height()), theme.qc("prio"))
            p.restore()
        border = theme.qc("focus") if selected else effects.mix(theme.c("border"), theme.c("border_hover"), h)
        p.setPen(QPen(effects.mix(border, theme.c("flash_border"), f), 1.0))
        p.drawPath(path)
        p.end()

    def _set_hover_t(self, v: float) -> None:
        self._hover_t = v
        self.update()

    def _set_flash_t(self, v: float) -> None:
        self._flash_t = v
        self.update()

    def enterEvent(self, e):  # noqa: N802
        effects.tween(self._hover_fx, self._hover_t, 1.0, effects.NORMAL, self._set_hover_t)
        self._sync_action_icons(True)
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        effects.tween(self._hover_fx, self._hover_t, 0.0, effects.SLOW, self._set_hover_t)
        self._sync_action_icons(False)
        super().leaveEvent(e)

    def flash(self) -> None:
        """新增 / 恢复 / 置顶后短暂高亮，让视线找到它。"""
        effects.tween(self._flash_fx, 1.0, 0.0, 1100, self._set_flash_t, QEasingCurve.Type.InQuad)

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
            m.addAction("复制", lambda: self.win.copy_text(self.full_text))
            m.addSeparator()
            m.addAction("删除", lambda: self.win.delete_memo(self.memo.id))
        m.exec(self.mapToGlobal(pos))


class ComposerFrame(QWidget):
    """顶部输入卡片：聚焦时边框加深并淡入一圈柔和的焦点光环。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("composer")
        self.setProperty("active", False)
        self._focus_t = 0.0
        self._fx = QObject(self)

    def set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        effects.tween(self._fx, self._focus_t, 1.0 if on else 0.0, effects.NORMAL, self._set_focus_t)

    def _set_focus_t(self, v: float) -> None:
        self._focus_t = v
        self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._focus_t
        card = QRectF(self.rect()).adjusted(3.5, 3.5, -3.5, -3.5)
        boost = 2.5 if theme.is_dark() else 1.0
        for dy, alpha in ((2.0, 6), (1.0, 9)):
            shadow = QPainterPath()
            shadow.addRoundedRect(card.translated(0, dy), 12, 12)
            p.fillPath(shadow, theme.qc("shadow", round(alpha * boost)))
        if t > 0:
            ring = QPainterPath()
            ring.addRoundedRect(card.adjusted(-1.5, -1.5, 1.5, 1.5), 13.5, 13.5)
            p.setPen(QPen(theme.qc("focus", round(70 * t)), 3.0))
            p.drawPath(ring)
        path = QPainterPath()
        path.addRoundedRect(card, 12, 12)
        p.fillPath(path, theme.qc("surface"))
        p.setPen(QPen(effects.mix(theme.c("border"), theme.c("focus"), t), 1.0))
        p.drawPath(path)
        p.end()


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
        self._flash_id: int | None = None
        self._leaving_ids: set[int] = set()
        self._reload_gen = 0
        self._build_ui()
        self.reload()
        # 首次显示时焦点落在输入框，而不是 Tab 顺序第一位的标题栏搜索
        self.input_edit.setFocus()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{theme.APP_NAME} · {theme.APP_NAME_EN}")
        self.setWindowIcon(make_icon(64))
        # FramelessWindowHint 会移除 WS_SYSMENU/WS_MINIMIZEBOX/WS_MAXIMIZEBOX，
        # 任务栏点击发来的 WM_SYSCOMMAND SC_MINIMIZE/SC_RESTORE 会被忽略；
        # 这三个提示只补回对应原生样式位，不产生可见边框。
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1080, 800)
        self.setMinimumSize(680, 520)
        app = QApplication.instance()
        effects.install(app)
        if not app.styleSheet():
            theme.apply(theme.current_name())

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ===== 自绘标题栏（含居中搜索） =====
        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)
        self.search_edit = self.titlebar.search
        self.search_edit.installEventFilter(self)
        self.search_action = QAction("搜索备忘录", self)
        self.search_action.setShortcut(QKeySequence.StandardKey.Find)
        self.search_action.triggered.connect(lambda: self._toggle_search(True))
        self.addAction(self.search_action)

        # ===== 主体：笔记页 / 设置页 =====
        self.body_stack = QStackedWidget()
        root.addWidget(self.body_stack, 1)
        self.settings_view: SettingsView | None = None
        main_page = QWidget()
        body = QHBoxLayout(main_page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.body_stack.addWidget(main_page)

        # ----- 侧栏 -----
        self.side = QWidget()
        self.side.setObjectName("sidePanel")
        self.side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.side.setFixedWidth(220)
        side_lay = QVBoxLayout(self.side)
        self.side_layout = side_lay
        side_lay.setContentsMargins(12, 18, 12, 10)
        side_lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 0, 0)
        brand.setSpacing(10)
        self.brand_mark = QLabel()
        brand.addWidget(self.brand_mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        name = QLabel(theme.APP_NAME)
        name.setObjectName("brandName")
        self.brand_sub = QLabel(f"{theme.APP_NAME_EN} · 随手记与待办")
        self.brand_sub.setObjectName("brandSub")
        brand_text.addWidget(name)
        brand_text.addWidget(self.brand_sub)
        brand.addLayout(brand_text, 1)
        side_lay.addLayout(brand)
        side_lay.addSpacing(18)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_host = QWidget()
        self.side_rows = QVBoxLayout(rows_host)
        self.side_rows.setSpacing(2)
        self.side_rows.setContentsMargins(0, 0, 0, 0)
        self.side_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(rows_host)
        side_lay.addWidget(scroll, 1)

        # 分类分区标题：名称 + 行内新建按钮（重建侧栏时复用，不删除）
        self.section_header = QWidget()
        sh = QHBoxLayout(self.section_header)
        sh.setContentsMargins(10, 16, 2, 4)
        section = QLabel("分类")
        section.setObjectName("sectionLabel")
        sh.addWidget(section)
        sh.addStretch(1)
        self.add_cat_btn = QToolButton()
        self.add_cat_btn.setObjectName("sectionAdd")
        self.add_cat_btn.setIconSize(QSize(14, 14))
        self.add_cat_btn.setFixedSize(24, 24)
        self.add_cat_btn.setToolTip("新建分类")
        self.add_cat_btn.setAccessibleName("新建分类")
        self.add_cat_btn.clicked.connect(self._add_category)
        sh.addWidget(self.add_cat_btn)

        # 底部：回收站 + 设置菜单（仿编辑器左下角齿轮）
        self.side_footer = QWidget()
        self.side_footer.setObjectName("sideFooter")
        self.side_footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QHBoxLayout(self.side_footer)
        fl.setContentsMargins(0, 8, 0, 0)
        fl.setSpacing(4)
        self.trash_btn = QToolButton()
        self.trash_btn.setObjectName("footerBtn")
        self.trash_btn.setText("回收站")
        self.trash_btn.setCheckable(True)
        self.trash_btn.setIconSize(QSize(16, 16))
        self.trash_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.trash_btn.clicked.connect(self._set_trash)
        fl.addWidget(self.trash_btn)
        fl.addStretch(1)
        self.settings_menu = QMenu(self)
        for key, label in (("general", "常规"), ("appearance", "外观与皮肤"),
                           ("hotkeys", "快捷键"), ("sync", "多端同步")):
            action = self.settings_menu.addAction(label)
            action.setData(key)
            action.triggered.connect(lambda _c=False, k=key: self.open_settings(k))
        self.settings_menu.addSeparator()
        about = self.settings_menu.addAction(f"关于{theme.APP_NAME}")
        about.setData("about")
        about.triggered.connect(lambda: self.open_settings("about"))
        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("footerBtn")
        self.settings_btn.setText("设置")
        self.settings_btn.setIconSize(QSize(16, 16))
        self.settings_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_btn.setMenu(self.settings_menu)
        self.settings_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.settings_btn.setToolTip("设置")
        fl.addWidget(self.settings_btn)
        side_lay.addWidget(self.side_footer)
        body.addWidget(self.side)

        # ----- 内容区 -----
        content = QWidget()
        self.content = content
        content.setObjectName("contentPanel")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        maincol = QVBoxLayout(content)
        self.content_layout = maincol
        maincol.setContentsMargins(40, 28, 40, 16)
        maincol.setSpacing(0)
        self._vertical_gaps = []

        def gap(normal: int, compact: int) -> None:
            spacer = QSpacerItem(0, normal, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            maincol.addItem(spacer)
            self._vertical_gaps.append((spacer, normal, compact))

        heading = QHBoxLayout()
        heading.setSpacing(10)
        self.page_title = QLabel("所有笔记")
        self.page_title.setTextFormat(Qt.TextFormat.PlainText)
        self.page_title.setObjectName("pageTitle")
        self.page_title.setMinimumWidth(0)
        heading.addWidget(self.page_title)
        self.count_badge = QLabel("0")
        self.count_badge.setObjectName("pageCount")
        heading.addWidget(self.count_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addStretch(1)
        self.date_label = QLabel()
        self.date_label.setObjectName("dateLabel")
        heading.addWidget(self.date_label, 0, Qt.AlignmentFlag.AlignVCenter)
        maincol.addLayout(heading)
        gap(6, 2)

        self.page_subtitle = QLabel("把琐事放在这里，把注意力留给生活。")
        self.page_subtitle.setObjectName("pageSubtitle")
        self.page_subtitle.setWordWrap(True)
        maincol.addWidget(self.page_subtitle)
        gap(20, 12)

        self.composer = ComposerFrame()
        composer_lay = QVBoxLayout(self.composer)
        composer_lay.setContentsMargins(19, 13, 15, 13)
        composer_lay.setSpacing(4)
        input_row = QHBoxLayout()
        input_row.setSpacing(12)
        self.compose_icon = QLabel()
        input_row.addWidget(self.compose_icon)
        self.input_edit = QLineEdit()
        self.input_edit.setObjectName("mainInput")
        self.input_edit.setPlaceholderText("此刻，有什么想记下来？")
        self.input_edit.setAccessibleName("新备忘录内容")
        self.input_edit.installEventFilter(self)
        input_row.addWidget(self.input_edit, 1)
        self.save_btn = QPushButton("记录")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setFixedHeight(34)
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.save_btn.setToolTip("保存 (Enter)")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_add)
        input_row.addWidget(self.save_btn)
        composer_lay.addLayout(input_row)
        self.composer_hint = QLabel("Enter 保存  ·  支持「明天9点」设提醒  ·  !高 设优先级")
        self.composer_hint.setObjectName("composerHint")
        self.composer_hint.setWordWrap(True)
        self.composer_hint.setContentsMargins(34, 0, 0, 0)
        self.composer_hint.setToolTip("例如：明天9点开会 !高 @每周 提前30分钟\n双击记录编辑，右键可置顶、分类或复制")
        composer_lay.addWidget(self.composer_hint)
        maincol.addWidget(self.composer)
        gap(18, 12)

        filters = QHBoxLayout()
        self.filter_group = QWidget()
        self.filter_group.setObjectName("filterGroup")
        self.filter_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        self.list_heading = QLabel("按置顶与更新时间排序")
        self.list_heading.setObjectName("listHeading")
        filters.addWidget(self.list_heading)
        maincol.addLayout(filters)
        gap(12, 10)

        # 回收站横幅：仅回收站模式显示（返回 + 计数 + 清空）
        self.trash_bar = QWidget()
        self.trash_bar.setObjectName("trashBar")
        self.trash_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.trash_bar.setVisible(False)
        tbl = QHBoxLayout(self.trash_bar)
        tbl.setContentsMargins(10, 5, 10, 5)
        tbl.setSpacing(6)
        self.trash_back_btn = QPushButton(" 返回")
        self.trash_back_btn.setObjectName("trashBack")
        self.trash_back_btn.clicked.connect(lambda: self._set_trash(False))
        tbl.addWidget(self.trash_back_btn)
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
        self.empty_icon = QLabel()
        self.empty_icon.setObjectName("emptyIcon")
        self.empty_icon.setFixedSize(88, 88)
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel("给想法一个落脚点")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        ev.addStretch(1)
        ev.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
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
        self.footer_hint = QLabel("双击编辑  ·  右键更多")
        self.footer_hint.setObjectName("statusLabel")
        bottom.addWidget(self.footer_hint)
        gap(10, 8)
        maincol.addLayout(bottom)

        body.addWidget(content, 1)
        self.toast = effects.Toast(content)
        app.installEventFilter(self)
        self._date_timer = QTimer(self)
        self._date_timer.setInterval(60_000)
        self._date_timer.timeout.connect(self._update_date)
        self._date_timer.start()
        self._update_date()
        for button in self.findChildren(QAbstractButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sync_text = ""
        self._retheme()
        theme.notifier.changed.connect(self._retheme)

        # 信号
        self.input_edit.returnPressed.connect(self._on_add)
        self.input_edit.textChanged.connect(self._sync_save_btn)
        self.search_edit.textChanged.connect(lambda _t: self.reload())
        self.list.itemSelectionChanged.connect(self._sync_selection)

        sc = QShortcut(QKeySequence.StandardKey.Delete, self.list)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._delete_selected)

    def _mark(self, size: int):
        return mark_pixmap(size, theme.c("accent_hover"), theme.c("accent_press"))

    def _retheme(self, *_args) -> None:
        """皮肤切换：刷新所有按调色板着色的图标，并重建列表/侧栏。"""
        text_2, muted = theme.c("text_2"), theme.c("muted")
        self.titlebar.retheme()
        self.titlebar.icon.setPixmap(self._mark(18))
        self.brand_mark.setPixmap(self._mark(32))
        self.trash_btn.setIcon(ui_icon("trash", text_2, 16))
        self.settings_btn.setIcon(ui_icon("gear", text_2, 16))
        self.add_cat_btn.setIcon(ui_icon("plus", muted, 14))
        self.compose_icon.setPixmap(ui_icon("note", muted, 22).pixmap(22, 22))
        self.empty_icon.setPixmap(ui_icon("note", muted, 40).pixmap(40, 40))
        self.trash_back_btn.setIcon(ui_icon("back", theme.c("trash_text"), 14))
        self._sync_save_btn(self.input_edit.text())
        self._on_sync_status(self._sync_text)
        self.composer.update()
        if hasattr(self, "_all_memos"):
            self._sidebar_signature = None
            self.reload()


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
            if w is not None and w is not self.section_header:
                w.deleteLater()

        all_row = CategoryRow(0, "所有笔记", len(self._all_memos))
        all_row.set_selected(self.selected_category is None and not self.trash_mode)
        all_row.activated.connect(self._on_cat_select)
        self.side_rows.addWidget(all_row)
        if focused_cat == 0:
            QTimer.singleShot(0, all_row, all_row.setFocus)

        self.side_rows.addWidget(self.section_header)
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
        if on and self.settings_view is not None:
            self.close_settings()
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
        self.side.setFixedWidth(188 if compact else 220)
        margin = 22 if compact else 40
        self.content_layout.setContentsMargins(margin, 18 if compact else 28, margin, 14 if compact else 16)
        self.search_edit.setFixedWidth(230 if compact else 340)
        self.page_subtitle.setVisible(not compact)
        self.brand_sub.setVisible(not compact)
        self.side_layout.setContentsMargins(10 if compact else 12, 14 if compact else 18, 10 if compact else 12, 10)
        self.date_label.setVisible(not compact)
        self.footer_hint.setVisible(not compact)
        self.list_heading.setVisible(not compact)
        self.page_title.setProperty("compact", compact)
        self.page_title.style().unpolish(self.page_title)
        self.page_title.style().polish(self.page_title)
        for spacer, normal, small in self._vertical_gaps:
            spacer.changeSize(0, small if compact else normal,
                              QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.content_layout.invalidate()
        self._set_page_title(self.page_title.toolTip() or self.page_title.text())

    def _set_trash(self, on: bool) -> None:
        self.trash_mode = on
        self.trash_btn.setChecked(on)
        self.composer.setVisible(not on)
        self.filter_group.setVisible(not on)
        self.search_edit.setPlaceholderText("搜索回收站    Ctrl+F" if on else "搜索笔记    Ctrl+F")
        self.search_edit.setAccessibleName("搜索回收站" if on else "搜索备忘录")
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.trash_bar.setVisible(on)
        self.footer_hint.setText("右键恢复  ·  Delete 彻底删除" if on else "双击编辑  ·  右键更多")
        self.reload()

    # ---------- 设置整页 ----------

    def _open_settings(self) -> None:
        self.open_settings("general")

    def open_settings(self, page: str = "general") -> None:
        if self.controller is None:
            return
        if not isinstance(page, str):  # 直接连到 triggered(bool) 时
            page = "general"
        view = self.settings_view
        if view is None:
            view = SettingsView(self.controller, page)
            view.back_requested.connect(self.close_settings)
            for button in view.findChildren(QAbstractButton):
                button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.settings_view = view
            self.body_stack.addWidget(view)
        else:
            view.show_page(page if page in view.pages else "general")
        self.body_stack.setCurrentWidget(view)
        self.search_edit.hide()
        view.setFocus()

    def close_settings(self) -> None:
        view, self.settings_view = self.settings_view, None
        self.body_stack.setCurrentIndex(0)
        self.search_edit.show()
        if view is not None:
            self.body_stack.removeWidget(view)
            view.deleteLater()
        self.input_edit.setFocus()

    # ---------- 同步（仿 Obsidian Sync 插件：导航栏状态菜单） ----------

    def _open_sync_menu(self, anchor) -> None:
        svc = (
            getattr(self.controller, "sync", None)
            if self.controller is not None
            else None
        )
        menu = QMenu(self)
        status = svc.status_text if svc is not None else "同步未就绪"
        act_status = menu.addAction(status or "未启用")
        act_status.setEnabled(False)
        menu.addSeparator()
        act_sync = menu.addAction("立即同步…")
        can_sync = bool(
            svc is not None and svc.enabled and svc.vault_id()
        )
        act_sync.setEnabled(can_sync)
        if can_sync:
            act_sync.triggered.connect(
                lambda: self.controller.sync_now_interactive(self)
            )
        act_setup = menu.addAction("同步设置…")
        act_setup.triggered.connect(lambda: self.open_settings("sync"))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _on_sync_status(self, text: str) -> None:
        """同步状态 → 标题栏按钮图标变色 + tooltip。"""
        btn = getattr(self.titlebar, "sync_btn", None)
        if btn is None:
            return
        btn.setToolTip(text or "多设备同步")
        self._sync_text = text
        if "失败" in text or "错误" in text or "不可用" in text:
            color = theme.c("danger")
        elif "已同步" in text or "就绪" in text:
            color = theme.c("accent")
        else:
            color = theme.c("text_2")
        btn.setIcon(ui_icon("sync", color=color, size=14))

    # ---------- 数据 ----------

    def reload(self) -> None:
        self._reload_gen += 1
        self._leaving_ids.clear()
        flash_id, self._flash_id = self._flash_id, None
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
        title = "回收站" if self.trash_mode else self._cat_names.get(self.selected_category, "所有笔记")
        self._set_page_title(title)
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
        flashed = self._card(flash_id) if flash_id is not None else None
        if flashed is not None:
            flashed.flash()
            QTimer.singleShot(0, self.list, lambda: self._scroll_to(flash_id))

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

    def _card(self, memo_id: int) -> MemoItemWidget | None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == memo_id:
                w = self.list.itemWidget(item)
                return w if isinstance(w, MemoItemWidget) else None
        return None

    def _scroll_to(self, memo_id: int) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == memo_id:
                self.list.scrollToItem(item, QListWidget.ScrollHint.EnsureVisible)
                return

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

    def _set_page_title(self, title: str) -> None:
        self.page_title.setToolTip(title)
        limit = max(120, self.content.width() - 260)
        self.page_title.setText(self.page_title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, limit))

    def _update_status(self, shown: int) -> None:
        self.count_badge.setText(str(shown))
        self.list_heading.setText("最近删除在前" if self.trash_mode else "按置顶与更新时间排序")
        sync = self._sync_text if self._sync_text and self._sync_text != "未启用" else "本地存储"
        if self.trash_mode:
            self.status_label.setText(f"回收站 {shown} 条")
            self.trash_title.setText(f"回收站 · {shown} 条备忘录")
        else:
            self.status_label.setText(f"{shown} 条记录  ·  {sync}")

    # ---------- 操作 ----------

    def _sync_save_btn(self, text: str) -> None:
        on = bool(text.strip())
        self.save_btn.setEnabled(on)
        self.save_btn.setIcon(ui_icon("arrow", theme.c("on_accent" if on else "faint"), 14))

    def _notify(self, text: str, icon: str = "check", action: str | None = None, callback=None) -> None:
        # 挂件/托盘触发的操作不在隐藏的主窗口里弹提示
        if self.isVisible() and not self.isMinimized():
            self.toast.show_message(text, icon, action, callback)

    def _remove_card(self, memo_id: int) -> None:
        """数据已写入：卡片淡出后再重建列表（重建本身可随时被其他刷新抢先）。"""
        card = self._card(memo_id)
        if card is None or not self.isVisible() or not effects.enabled():
            self.reload()
            return
        self._leaving_ids.add(memo_id)
        card.setEnabled(False)  # 淡出期间屏蔽按钮、右键与双击编辑
        gen = self._reload_gen
        # 期间若已有其他刷新（如撤销），淡出结束时不再重复重建，避免打断新状态的高亮
        effects.fade_widget(card, 1.0, 0.0, effects.NORMAL,
                            lambda: gen == self._reload_gen and self.reload())

    def _undo_to_todo(self, memo_id: int) -> None:
        db.restore(memo_id)
        self._flash_id = memo_id
        if self.trash_mode:
            self._set_trash(False)
        else:
            self.reload()

    def copy_text(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self._notify("已复制到剪贴板", "copy")

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
        self._flash_id = mid
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
        self._flash_id = memo_id
        self.reload()

    def toggle_done(self, memo_id: int) -> None:
        if memo_id in self._leaving_ids:
            return
        before = next((m for m in db.list_memos() if m.id == memo_id), None)
        db.toggle_done(memo_id)
        if before is None or before.is_done:
            self.reload()
            return
        after = next((m for m in db.list_memos() if m.id == memo_id), None)
        if after is not None:
            # 重复任务不进回收站，而是顺延到下一周期
            self._flash_id = memo_id
            self.reload()
            due = f"已顺延至 {nlp_time.format_due(after.due_ts)}" if after.due_ts else "已完成本周期"
            self._notify(due, "restore")
            return
        self._notify("已完成，移入回收站", "check", "撤销", lambda: self._undo_to_todo(memo_id))
        self._remove_card(memo_id)

    def delete_memo(self, memo_id: int) -> None:
        if memo_id in self._leaving_ids:
            return
        db.soft_delete(memo_id)
        self._notify("已移到回收站", "trash", "撤销", lambda: self._undo_to_todo(memo_id))
        self._remove_card(memo_id)

    def restore_memo(self, memo_id: int) -> None:
        if memo_id in self._leaving_ids:
            return
        db.restore(memo_id)
        self._notify("已恢复到待办", "restore")
        self._remove_card(memo_id)

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
        self._flash_id = memo_id
        self.reload()

    def purge_memo(self, memo_id: int) -> None:
        if memo_id in self._leaving_ids:
            return
        if QMessageBox.question(
            self, "彻底删除", "确定要彻底删除这条备忘录吗？此操作不可恢复。"
        ) == QMessageBox.StandardButton.Yes:
            db.purge(memo_id)
            self._notify("已彻底删除", "trash")
            self._remove_card(memo_id)

    def _on_purge_all(self) -> None:
        if QMessageBox.question(
            self, "清空回收站", "确定要清空回收站吗？所有条目将被彻底删除。"
        ) == QMessageBox.StandardButton.Yes:
            count = db.purge_all_trash()
            self._notify(f"已清空回收站 · {count} 条", "trash")
            self.reload()

    # ---------- 无边框窗口：边缘缩放 / 标题栏拖动 ----------

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is getattr(self, "input_edit", None) and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.composer.set_active(e.type() == QEvent.Type.FocusIn)
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
