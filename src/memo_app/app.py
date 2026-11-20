"""PySide6 主界面：标题栏 + 分类侧栏 + 分组列表 + 详情栏 + 回收站。

两种风格（theme.STYLES）共用同一套控件树：云母 = 三栏、行式列表、详情常驻；
纸笺 = 日历页侧栏、宋体细线列表、详情选中时展开。差异集中在 QSS 与 paintEvent。
"""
from __future__ import annotations

import ctypes
import sqlite3
from datetime import datetime
from html import escape

from PySide6.QtCore import QEasingCurve, QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QButtonGroup,
    QDialog,
    QFileDialog,
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
from . import images
from . import nlp_time
from .category_dialog import CategoryPickerDialog
from .db import Category, Memo
from .detail_pane import DetailPane
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


# 二十四节气的常见日期（公历，逐年会有 ±1 天浮动——文案只说"时节/将至"，不断言当天）
_TERMS = (
    (1, 6, "小寒"), (1, 20, "大寒"), (2, 4, "立春"), (2, 19, "雨水"), (3, 6, "惊蛰"), (3, 21, "春分"),
    (4, 5, "清明"), (4, 20, "谷雨"), (5, 6, "立夏"), (5, 21, "小满"), (6, 6, "芒种"), (6, 21, "夏至"),
    (7, 7, "小暑"), (7, 23, "大暑"), (8, 8, "立秋"), (8, 23, "处暑"), (9, 8, "白露"), (9, 23, "秋分"),
    (10, 8, "寒露"), (10, 23, "霜降"), (11, 7, "立冬"), (11, 22, "小雪"), (12, 7, "大雪"), (12, 22, "冬至"),
)


def almanac_line(now: datetime) -> str:
    """纸笺侧栏的"黄历"小字：干支年 · 节气时节（立春前仍算上一年）。"""
    year = now.year - (1 if (now.month, now.day) < (2, 4) else 0)
    ganzhi = "甲乙丙丁戊己庚辛壬癸"[(year - 4) % 10] + "子丑寅卯辰巳午未申酉戌亥"[(year - 4) % 12]
    md = (now.month, now.day)
    current = next((t for t in reversed(_TERMS) if (t[0], t[1]) <= md), _TERMS[-1])
    upcoming = next((t for t in _TERMS if (t[0], t[1]) > md), _TERMS[0])
    nxt = datetime(now.year + (1 if upcoming is _TERMS[0] and md >= (1, 6) else 0), upcoming[0], upcoming[1])
    days = (nxt.date() - now.date()).days
    term = f"{upcoming[2]}将至" if 0 < days <= 2 else f"{current[2]}时节"
    return f"{ganzhi}年 · {term}"


def group_memos(memos: list[Memo], paper: bool, now_ts: int | None = None) -> list[tuple[str, bool, list[Memo]]]:
    """按风格分组：云母按截止日期（逾期/今天/明天/以后/无期限），纸笺按 置顶/有期限/随记。

    返回 [(标题, 是否警示, 条目)]，空组已剔除；组内保持数据库顺序，有期限的组按截止时间升序。
    """
    now = datetime.fromtimestamp(now_ts) if now_ts is not None else datetime.now()
    today = now.date()
    if paper:
        pinned = [m for m in memos if m.is_pinned]
        dated = sorted((m for m in memos if not m.is_pinned and m.due_ts), key=lambda m: m.due_ts)
        rest = [m for m in memos if not m.is_pinned and not m.due_ts]
        groups = [("置顶", False, pinned), ("有期限", False, dated), ("随记", False, rest)]
    else:
        buckets: dict[str, list[Memo]] = {k: [] for k in ("overdue", "today", "tomorrow", "later", "none")}
        for m in memos:
            if not m.due_ts:
                buckets["none"].append(m)
                continue
            if m.due_ts < now.timestamp() and not m.is_done:
                buckets["overdue"].append(m)
                continue
            delta = (datetime.fromtimestamp(m.due_ts).date() - today).days
            buckets["today" if delta <= 0 else "tomorrow" if delta == 1 else "later"].append(m)
        for key in ("overdue", "today", "tomorrow", "later"):
            buckets[key].sort(key=lambda m: m.due_ts)
        groups = [("已逾期", True, buckets["overdue"]), ("今天", False, buckets["today"]),
                  ("明天", False, buckets["tomorrow"]), ("以后", False, buckets["later"]),
                  ("无期限", False, buckets["none"])]
    return [g for g in groups if g[2]]


class GroupHeaderRow(QWidget):
    """列表分组标题（不可选中）；纸笺风格在标题后画一条细线。"""

    def __init__(self, title: str, count: int, warn: bool) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        paper = theme.is_paper()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6 if paper else 12, 10 if paper else 8, 0, 2)
        lay.setSpacing(8)
        self.label = QLabel()
        self.label.setObjectName("groupHeader")
        self.label.setProperty("warn", "true" if warn else "false")
        self.label.setTextFormat(Qt.TextFormat.RichText)
        shown = " ".join(title) if paper else title  # 纸笺：疏排字距
        self.label.setText(f'{shown}&nbsp;&nbsp;<span style="color:{theme.c("faint")}">{count}</span>')
        self.label.setAccessibleName(f"{title} {count} 条")
        lay.addWidget(self.label)
        lay.addStretch(1)

    def paintEvent(self, e):  # noqa: N802
        if not theme.is_paper():
            return
        p = QPainter(self)
        x = self.label.geometry().right() + 14
        y = self.label.geometry().center().y() + 1
        p.setPen(QPen(theme.qc("line"), 1.0))
        p.drawLine(QPoint(x, y), QPoint(self.width() - 4, y))
        p.end()


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


def cat_marker(cat_id: int, size: int = 16) -> QPixmap:
    """分类色标：云母为圆角小方块，纸笺为菱形（像书签上的朱印）。"""
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(theme.cat_color(cat_id))
    c = size / 2
    path = QPainterPath()
    if theme.is_paper():
        r = size * 0.22
        path.moveTo(c, c - r)
        path.lineTo(c + r, c)
        path.lineTo(c, c + r)
        path.lineTo(c - r, c)
        path.closeSubpath()
    else:
        r = size * 0.3
        path.addRoundedRect(QRectF(c - r, c - r, 2 * r, 2 * r), 2.5, 2.5)
    p.fillPath(path, color)
    p.end()
    return pm


_CAT_MIME = "application/x-lumo-category"


class CategoryRow(QWidget):
    """侧边栏分类行：点击选中，悬停出现删除，按住拖动可调整分类顺序。"""

    activated = Signal(int)          # cat_id，0 = 全部
    delete_requested = Signal(int)
    move_requested = Signal(int, int, bool)  # 被拖的 cat_id, 目标 cat_id, 是否放在目标之后

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
        self.setToolTip(name if cat_id == 0 else f"{name}\n按住拖动可调整顺序")
        self._press_pos: QPoint | None = None
        self._drop_mark: str | None = None  # "above" / "below"：拖放落点指示线
        self.setAcceptDrops(cat_id != 0)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(9)

        icon = QLabel()
        icon.setPixmap(
            ui_icon("notes", theme.c("muted"), 16).pixmap(16, 16) if cat_id == 0
            else cat_marker(cat_id, 16)
        )
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
            self._press_pos = e.position().toPoint()
            self.activated.emit(self.cat_id)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        if (
            self.cat_id != 0 and self._press_pos is not None
            and e.buttons() & Qt.MouseButton.LeftButton
            and (e.position().toPoint() - self._press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_pos = None
            self._start_drag()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def _start_drag(self) -> None:
        mime = QMimeData()
        mime.setData(_CAT_MIME, str(self.cat_id).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = self.grab()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(self.width() // 2, self.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    @staticmethod
    def _drag_source_id(e) -> int | None:
        md = e.mimeData()
        if not md.hasFormat(_CAT_MIME):
            return None
        try:
            return int(bytes(md.data(_CAT_MIME)).decode())
        except ValueError:
            return None

    def _set_drop_mark(self, mark: str | None) -> None:
        if mark != self._drop_mark:
            self._drop_mark = mark
            self.update()

    def dragEnterEvent(self, e):  # noqa: N802
        src = self._drag_source_id(e)
        if src is not None and src != self.cat_id:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):  # noqa: N802
        src = self._drag_source_id(e)
        if src is None or src == self.cat_id:
            self._set_drop_mark(None)
            e.ignore()
            return
        self._set_drop_mark("below" if e.position().y() > self.height() / 2 else "above")
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):  # noqa: N802
        self._set_drop_mark(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):  # noqa: N802
        src = self._drag_source_id(e)
        after = e.position().y() > self.height() / 2
        self._set_drop_mark(None)
        if src is None or src == self.cat_id:
            e.ignore()
            return
        e.acceptProposedAction()
        # 落点处理会重建侧边栏（销毁本行），推迟到拖放事件返回之后
        QTimer.singleShot(0, lambda s=src, t=self.cat_id, a=after: self.move_requested.emit(s, t, a))

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if self._drop_mark is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = 1.5 if self._drop_mark == "above" else self.height() - 1.5
        p.setPen(QPen(theme.qc("accent"), 2.0))
        p.drawLine(QPointF(6, y), QPointF(self.width() - 6, y))
        p.setBrush(theme.qc("accent"))
        p.drawEllipse(QPointF(6, y), 3, 3)
        p.end()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.cat_id)
            return
        # Alt+↑ / Alt+↓：键盘调整分类顺序
        if (
            self.cat_id != 0 and e.modifiers() & Qt.KeyboardModifier.AltModifier
            and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
        ):
            self.move_requested.emit(self.cat_id, self.cat_id, e.key() == Qt.Key.Key_Down)
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
    """单条备忘录行（内容自动换行、高度自适应）。

    云母：圆角行，悬停/选中填充，选中左侧强调条；
    纸笺：无框，点线分隔，选中朱砂竖条；高优先级盖「急」字小印。
    """

    HEIGHT = 56
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
        paper = theme.is_paper()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*((6, 13, 8, 12) if paper else (14, 10, 10, 10)))
        lay.setSpacing(5 if paper else 4)
        content_row = QHBoxLayout()
        self.content_row = content_row
        content_row.setSpacing(14 if paper else 12)

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

        # 纸笺：高优先级的「急」字小印
        self.seal = QLabel("急")
        self.seal.setObjectName("memoSeal")
        self.seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seal.setFixedSize(18, 18)
        self.seal.setToolTip("高优先级")
        self.seal.setVisible(False)

        # 删除按钮：静止时隐形（不占视觉），悬停行时浮现，悬停按钮变红
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

        # 元信息一行：任务徽标（截止/优先级/重复）· 分类色点 · 置顶 · 相对时间
        self.meta = QHBoxLayout()
        self.meta.setContentsMargins(34 if paper else 32, 0, 0, 0)
        self.meta.setSpacing(12 if paper else 10)
        self.badges_label = QLabel()
        self.badges_label.setObjectName("memoBadges")
        self.badges_label.setTextFormat(Qt.TextFormat.RichText)
        self.tags_label = QLabel()
        self.tags_label.setObjectName("memoTags")
        self.tags_label.setTextFormat(Qt.TextFormat.RichText)
        self.tags_label.setWordWrap(True)
        self.tags_label.setMinimumWidth(0)
        self.tags_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.pin_label = QLabel()
        self.pin_label.setObjectName("memoPin")
        self.pin_label.setToolTip("已置顶")
        self.time_label = QLabel()
        self.time_label.setObjectName("memoTime")
        for w in (self.badges_label, self.tags_label, self.pin_label, self.time_label):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.meta.addWidget(self.seal)
        self.meta.addWidget(self.badges_label)
        self.meta.addWidget(self.tags_label, 1)
        self.meta.addWidget(self.pin_label)
        self.meta.addWidget(self.time_label)
        lay.addLayout(self.meta)

    # ---------- 状态 ----------

    def set_selected(self, on: bool) -> None:
        if self.property("selected") != on:
            self.setProperty("selected", on)
            self._sync_action_icons(self.underMouse())
            self.update()

    def _sync_action_icons(self, card_hovered: bool) -> None:
        # 静止时操作图标隐形（按钮仍占位，悬停时不会让正文重新换行），
        # 悬停或选中行时浮现，悬停按钮本身再强调
        shown = card_hovered or bool(self.property("selected")) or self.hasFocus()
        if shown:
            idle_props = ui_icon("sliders", theme.c("text_2"), 16)
            idle_del = ui_icon("trash", theme.c("text_2"), 15)
        else:
            idle_props = idle_del = QIcon()
        effects.hover_icon(self.props_btn, idle_props, ui_icon("sliders", theme.c("accent"), 16))
        effects.hover_icon(self.del_btn, idle_del, ui_icon("trash", theme.c("danger"), 15))

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
        paper = theme.is_paper()
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
        # 云母：高优先级的完成圈描成橙色（纸笺用「急」字印表达）
        self.done_btn.setProperty("prio", "true" if self.memo.priority >= 3 and not paper else "false")
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
        self.seal.setVisible(paper and self.memo.priority >= 3 and not done)
        self.pin_label.setPixmap(ui_icon("star" if not paper else "pin", theme.c("warn"), 13).pixmap(13, 13))
        self.pin_label.setVisible(self.memo.is_pinned)
        self.time_label.setText(rel_time(self.memo.updated_at))

        # 分类：色点 + 名称（与侧栏色标一致）
        cat_ids = self.win._memo_cats.get(self.memo.id, set())
        cats = [(c, self.win._cat_names[c]) for c in sorted(cat_ids) if c in self.win._cat_names]
        self.tags_label.setText("&nbsp;&nbsp;&nbsp;".join(
            f'<span style="color:{theme.cat_color(c)}">●</span>&nbsp;{escape(n)}' for c, n in cats
        ))
        self.tags_label.setToolTip(" / ".join(n for _c, n in cats))
        self.tags_label.setVisible(bool(cats))

        # 截止/优先级/重复徽标
        self.badges_label.setText(self._badges_html())
        self.badges_label.setVisible(bool(self.badges_label.text()))
        self.setToolTip(self._badges_tooltip())

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
        meta_w = max(1, self.width() - 32)
        meta_h = max(
            self.time_label.sizeHint().height(),
            self.badges_label.sizeHint().height() if not self.badges_label.isHidden() else 0,
            self.tags_label.heightForWidth(max(1, self.tags_label.width() or meta_w // 2))
            if not self.tags_label.isHidden() else 0,
        )
        m = self.layout().contentsMargins()
        h = max(self.HEIGHT, max(br.height(), 22) + meta_h + self.layout().spacing() + m.top() + m.bottom())
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
        if 0 < m.priority <= 3 and not (theme.is_paper() and m.priority == 3):
            pcolor = theme.c({1: "muted", 2: "warn", 3: "overdue"}[m.priority])
            parts.append(
                f'<span style="color:{pcolor}">{db.PRIORITY_LABELS[m.priority]}优先级</span>'
            )
        if m.recur:
            parts.append(f'<span style="color:{theme.c("muted")}">{escape(db.RECUR_LABELS.get(m.recur, m.recur))}</span>')
        n_img = getattr(self.win, "_img_counts", {}).get(m.id, 0)
        if n_img:
            parts.append(f'<span style="color:{theme.c("muted")}">图片 {n_img}</span>')
        return "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)

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
        n_img = getattr(self.win, "_img_counts", {}).get(m.id, 0)
        if n_img:
            tips.append(f"{n_img} 张图片（选中后在详情栏查看）")
        return "\n".join(tips)

    # ---------- 绘制 / 动效 ----------

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h, f = self._hover_t, self._flash_t
        selected = bool(self.property("selected"))
        r = QRectF(self.rect())
        if theme.is_paper():
            # 纸面：无框；悬停/选中淡淡洇开，点线分隔，选中朱砂竖条
            if selected:
                p.fillRect(r.adjusted(0, 0, 0, -1), theme.qc("selected"))
            elif h > 0:
                p.fillRect(r.adjusted(0, 0, 0, -1), theme.qc("hover", round(150 * h)))
            if f > 0:
                p.fillRect(r.adjusted(0, 0, 0, -1), theme.qc("flash", round(230 * f)))
            p.setPen(QPen(theme.qc("line"), 1.0, Qt.PenStyle.DotLine))
            p.drawLine(QPoint(0, self.height() - 1), QPoint(self.width(), self.height() - 1))
            if selected:
                p.fillRect(QRectF(0, 8, 2.5, r.height() - 17), theme.qc("accent"))
            p.end()
            return
        # 云母：圆角行，静止透明；悬停浅填充，选中强调色淡底 + 左侧强调条
        card = r.adjusted(2, 1, -2, -1)
        path = QPainterPath()
        path.addRoundedRect(card, 8, 8)
        if selected:
            p.fillPath(path, theme.qc("selected"))
        elif h > 0:
            p.fillPath(path, theme.qc("hover", round(255 * h)))
        if f > 0:
            p.fillPath(path, theme.qc("flash", round(240 * f)))
        if selected:
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(card.left() + 1, card.center().y() - 9, 3, 18), 1.5, 1.5)
            p.fillPath(bar, theme.qc("accent"))
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
        r = QRectF(self.rect())
        if theme.is_paper():
            # 纸笺：只有一条墨线，聚焦时转为朱砂并加粗
            y = r.bottom() - 2.5
            p.setPen(QPen(effects.mix(theme.c("text_2"), theme.c("accent"), t), 1.2 + 0.6 * t))
            p.drawLine(QPoint(int(r.left()), int(y)), QPoint(int(r.right()), int(y)))
            p.end()
            return
        # 云母：白底圆角框，聚焦时底边出现 2px 强调色（Fluent 输入框）
        card = r.adjusted(1.5, 1.5, -1.5, -2.5)
        shadow = QPainterPath()
        shadow.addRoundedRect(card.translated(0, 1), 8, 8)
        p.fillPath(shadow, theme.qc("shadow", 10 if not theme.is_dark() else 30))
        path = QPainterPath()
        path.addRoundedRect(card, 8, 8)
        p.fillPath(path, theme.qc("surface"))
        p.setPen(QPen(effects.mix(theme.c("border"), theme.c("border_hover"), t), 1.0))
        p.drawPath(path)
        if t > 0:
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(card.left(), card.bottom() - 2, card.width(), 2), theme.qc("accent", round(255 * t)))
            p.restore()
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
        self.setWindowIcon(make_icon())
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
        self.resize(1160, 800)
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

        self.brand_box = QWidget()
        brand = QHBoxLayout(self.brand_box)
        brand.setContentsMargins(8, 0, 0, 0)
        brand.setSpacing(10)
        self.brand_mark = QLabel()
        self.brand_mark.setPixmap(mark_pixmap(32))
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
        side_lay.addWidget(self.brand_box)
        # 纸笺：侧栏顶部是一张"日历页"（大号日期 + 星期 + 干支节气）
        self.paper_head = QWidget()
        ph = QVBoxLayout(self.paper_head)
        ph.setContentsMargins(12, 0, 0, 0)
        ph.setSpacing(2)
        self.paper_day = QLabel()
        self.paper_day.setObjectName("paperDay")
        self.paper_date = QLabel()
        self.paper_date.setObjectName("paperDate")
        self.paper_term = QLabel()
        self.paper_term.setObjectName("paperTerm")
        for w in (self.paper_day, self.paper_date, self.paper_term):
            ph.addWidget(w)
        side_lay.addWidget(self.paper_head)
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
        content.setObjectName("listPanel")
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
        self.composer_lay = composer_lay
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
        self.input_edit.setAcceptDrops(True)
        input_row.addWidget(self.input_edit, 1)
        # 待随新记录保存的图片（粘贴 / 拖入 / 选择文件）
        self._pending_images: list = []
        self.pending_chip = QPushButton()
        self.pending_chip.setObjectName("chipGhost")
        self.pending_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pending_chip.setToolTip("点击移除待添加的图片")
        self.pending_chip.setAccessibleName("移除待添加的图片")
        self.pending_chip.clicked.connect(self._clear_pending_images)
        self.pending_chip.setVisible(False)
        input_row.addWidget(self.pending_chip)
        self.img_btn = QToolButton()
        self.img_btn.setObjectName("sectionAdd")
        self.img_btn.setFixedSize(30, 30)
        self.img_btn.setIconSize(QSize(17, 17))
        self.img_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.img_btn.setToolTip("添加图片（也可直接 Ctrl+V 粘贴截图，或把图片拖到输入框）")
        self.img_btn.setAccessibleName("添加图片")
        self.img_btn.clicked.connect(self._pick_compose_images)
        input_row.addWidget(self.img_btn)
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

        # ----- 详情栏 -----
        self._detail_id: int | None = None
        self._reloading = False
        self.detail = DetailPane(self)
        self.detail.close_requested.connect(self._close_detail)
        body.addWidget(self.detail)
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

    def _retheme(self, *_args) -> None:
        """皮肤切换：刷新所有按调色板着色的图标，并重建列表/侧栏。"""
        text_2, muted = theme.c("text_2"), theme.c("muted")
        self.titlebar.retheme()
        self.trash_btn.setIcon(ui_icon("trash", text_2, 16))
        self.settings_btn.setIcon(ui_icon("gear", text_2, 16))
        self.add_cat_btn.setIcon(ui_icon("plus", muted, 14))
        self.compose_icon.setPixmap(ui_icon("note", muted, 22).pixmap(22, 22))
        self.img_btn.setIcon(ui_icon("image", muted, 17))
        self.empty_icon.setPixmap(ui_icon("note", muted, 40).pixmap(40, 40))
        self.trash_back_btn.setIcon(ui_icon("back", theme.c("trash_text"), 14))
        self._sync_save_btn(self.input_edit.text())
        self._on_sync_status(self._sync_text)
        self._apply_style_layout()
        self.composer.update()
        if hasattr(self, "_all_memos"):
            self._sidebar_signature = None
            self.reload()

    def _apply_style_layout(self) -> None:
        """风格切换时调整结构性差异（页眉、记录框、列表间距、详情栏）。"""
        paper = theme.is_paper()
        self.brand_box.setVisible(not paper)
        self.paper_head.setVisible(paper)
        self.compose_icon.setVisible(not paper)
        self.composer_lay.setContentsMargins(*((2, 4, 0, 12) if paper else (19, 13, 15, 13)))
        self.composer_hint.setContentsMargins(0 if paper else 34, 0, 0, 0)
        self.composer_hint.setText(
            "试试  /工作 归类  ·  明天九点 定时  ·  !高 标急" if paper
            else "Enter 保存  ·  支持「明天9点」设提醒  ·  !高 设优先级"
        )
        self.input_edit.setPlaceholderText("此刻，写下一句……" if paper else "此刻，有什么想记下来？")
        self.list.setSpacing(0 if paper else 1)
        self.save_btn.setText("记 下" if paper else "记录")
        self._sync_save_btn(self.input_edit.text())
        self.detail.retheme()
        self._update_date()
        self.resizeEvent(None)


    # ---------- 分类 ----------

    def _refresh_category_data(self) -> None:
        self._categories = db.list_categories()
        self._memo_cats = db.memo_category_map()
        self._img_counts = db.memo_image_counts()
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
        old = getattr(self, "_sidebar_signature", None)
        self._sidebar_signature = signature
        if old is not None and old[:2] == signature[:2]:
            # 只有选中项变了：原地更新高亮，不重建——按下的那一行要继续接收
            # 鼠标移动事件，才能被拖动排序
            for i in range(self.side_rows.count()):
                w = self.side_rows.itemAt(i).widget()
                if isinstance(w, CategoryRow):
                    sel = (self.selected_category is None if w.cat_id == 0
                           else self.selected_category == w.cat_id)
                    w.set_selected(sel and not self.trash_mode)
            return
        focused = QApplication.focusWidget()
        focused_cat = focused.cat_id if isinstance(focused, CategoryRow) else None
        while self.side_rows.count():
            item = self.side_rows.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.section_header:
                w.hide()  # 立即从界面消失；真正销毁要等回到事件循环
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
            row.move_requested.connect(self._on_cat_move)
            self.side_rows.addWidget(row)
            if focused_cat == cat.id:
                QTimer.singleShot(0, row, row.setFocus)

    def _on_cat_select(self, cat_id: int) -> None:
        self.selected_category = None if cat_id == 0 else cat_id
        if self.trash_mode:
            self._set_trash(False)
        else:
            self.reload()

    def _on_cat_move(self, cat_id: int, target_id: int, after: bool) -> None:
        """拖动排序；target_id == cat_id 表示键盘 Alt+↑/↓ 与相邻分类交换。"""
        if target_id == cat_id:
            order = [c.id for c in self._categories]
            if cat_id not in order:
                return
            i = order.index(cat_id) + (1 if after else -1)
            if not 0 <= i < len(order):
                return
            target_id = order[i]
        if not db.move_category(cat_id, target_id, after):
            return
        self.reload()
        # 键盘排序后焦点跟随被移动的分类
        for i in range(self.side_rows.count()):
            w = self.side_rows.itemAt(i).widget()
            if isinstance(w, CategoryRow) and w.cat_id == cat_id:
                w.setFocus()
                break

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

    def set_task_props(self, memo_id: int, **props) -> None:
        """详情栏的即时修改：写库后刷新（列表分组/徽标/挂件随之更新）。"""
        try:
            db.set_task_props(memo_id, **props)
        except (sqlite3.Error, ValueError):
            return
        self.reload()

    def select_memo(self, memo_id: int) -> None:
        for item, mid in self.memo_items():
            if mid == memo_id:
                self.list.scrollToItem(item, QListWidget.ScrollHint.EnsureVisible)
                self.list.setCurrentItem(item)  # 可能触发写库刷新，之后不再使用 item
                return

    def edit_task_props(self, memo_id: int) -> None:
        memo = next((m for m in self._memos if m.id == memo_id), None)
        if memo is None:
            return
        if self.width() >= 960 and not self.trash_mode:
            # 详情栏放得下：选中这条，在右侧直接改（纸笺会随之展开详情栏）
            self.select_memo(memo_id)
            self.detail.title.setFocus()
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
        months = "一二三四五六七八九十"
        month = months[now.month - 1] if now.month <= 10 else "十" + months[now.month - 11]
        self.paper_day.setText(str(now.day))
        self.paper_date.setText(f"{month}月  ·  星期{weekday}")
        self.paper_term.setText(almanac_line(now))

    def resizeEvent(self, e):  # noqa: N802
        if e is not None:
            super().resizeEvent(e)
        if not hasattr(self, "detail"):
            return
        self._layout_detail()
        compact = self.width() < 880
        self.side.setFixedWidth(188 if compact else 220)
        margin = 22 if compact else 40
        self.content_layout.setContentsMargins(margin, 18 if compact else 28, margin, 14 if compact else 16)
        self.search_edit.setFixedWidth(230 if compact else 340)
        self.page_subtitle.setVisible(not compact)
        self.brand_sub.setVisible(not compact)
        self.side_layout.setContentsMargins(10 if compact else 12, 14 if compact else 18, 10 if compact else 12, 10)
        self.date_label.setVisible(not compact and not theme.is_paper())
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

    def _detail_wanted(self) -> bool:
        # 三栏放不下时（窄窗）详情栏让位，任务属性回到对话框
        if self.trash_mode or self.width() < 960:
            return False
        return self._detail_id is not None if theme.is_paper() else True

    def _layout_detail(self) -> None:
        self.detail.setVisible(self._detail_wanted())

    def _close_detail(self) -> None:
        self._detail_id = None
        self._reloading = True
        try:
            self.list.setCurrentItem(None)
            self.list.clearSelection()
        finally:
            self._reloading = False
        self._sync_selection()
        self.input_edit.setFocus()

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
        self._layout_detail()
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
        self._reloading = True  # 重建期间的选中变化不改详情栏绑定
        try:
            self.list.clear()
            groups = [("", False, memos)] if self.trash_mode else group_memos(memos, theme.is_paper())
            # 只有一个分组时不显示标题（"全部都是无期限"不值得占一行）
            headers = len(groups) > 1
            selected_item = None
            for title, warn, items in groups:
                if headers:
                    hdr = QListWidgetItem()
                    hdr.setFlags(Qt.ItemFlag.NoItemFlags)
                    hdr.setSizeHint(QSize(0, 40 if theme.is_paper() else 34))
                    self.list.addItem(hdr)
                    self.list.setItemWidget(hdr, GroupHeaderRow(title, len(items), warn))
                for memo in items:
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
                    if memo.id == self._detail_id:
                        selected_item = item
            if selected_item is not None:
                self.list.setCurrentItem(selected_item)
            else:
                self._detail_id = None
        finally:
            self._reloading = False
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

    def memo_items(self) -> list[tuple[QListWidgetItem, int]]:
        """列表里的备忘录条目（跳过分组标题）。"""
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            mid = item.data(Qt.ItemDataRole.UserRole)
            if mid is not None:
                out.append((item, int(mid)))
        return out

    def memo_count(self) -> int:
        return len(self.memo_items())

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
        if not self._reloading:
            mid = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
            self._detail_id = int(mid) if mid is not None and not self.trash_mode else None
        for i in range(self.list.count()):
            item = self.list.item(i)
            w = self.list.itemWidget(item)
            if isinstance(w, MemoItemWidget):
                w.set_selected(item is current and self._detail_id is not None)
        memo = next((m for m in self._memos if m.id == self._detail_id), None)
        if memo is None:
            self._detail_id = None
        self.detail.show_memo(memo)
        self._layout_detail()

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
        on = bool(text.strip()) or bool(getattr(self, "_pending_images", None))
        self.save_btn.setEnabled(on)
        color = ("accent" if theme.is_paper() else "on_accent") if on else "faint"
        self.save_btn.setIcon(ui_icon("arrow", theme.c(color), 14))

    # ---------- 新记录附带图片 ----------

    def _add_pending_images(self, imgs: list) -> None:
        imgs = [i for i in imgs if i is not None and not i.isNull()]
        if not imgs:
            return
        self._pending_images.extend(imgs)
        self._sync_pending_chip()
        self.input_edit.setFocus()

    def _clear_pending_images(self) -> None:
        self._pending_images.clear()
        self._sync_pending_chip()
        self.input_edit.setFocus()

    def _sync_pending_chip(self) -> None:
        n = len(self._pending_images)
        self.pending_chip.setText(f"{n} 张图片  ✕")
        self.pending_chip.setVisible(n > 0)
        self._sync_save_btn(self.input_edit.text())

    def _pick_compose_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "添加图片", "", images.FILE_FILTER)
        loaded = [images.load_file(p) for p in paths]
        ok = [i for i in loaded if not i.isNull()]
        if paths and not ok:
            self._notify("无法读取所选图片", "info")
        self._add_pending_images(ok)

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
        text = self.input_edit.text()
        pending = list(self._pending_images)
        if not text.strip() and pending:
            text = "图片笔记"
        mid = self.add_memo_text(text, cats)
        if mid is not None and pending:
            added = sum(1 for img in pending if images.attach_image(mid, img))
            self._pending_images.clear()
            self._sync_pending_chip()
            self.reload()
            if added < len(pending):
                self._notify(f"有 {len(pending) - added} 张图片保存失败", "info")
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
        mid = item.data(Qt.ItemDataRole.UserRole)
        if mid is None:  # 分组标题
            return
        memo_id = int(mid)
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

    def edit_memo(self, memo_id: int, text: str) -> bool:
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
            return False
        self._flash_id = memo_id
        self.reload()
        return True

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
        if obj is getattr(self, "input_edit", None):
            et = e.type()
            if et == QEvent.Type.KeyPress and e.matches(QKeySequence.StandardKey.Paste):
                md = QApplication.clipboard().mimeData()
                # 纯文本照常粘贴；截图 / 复制的图片文件转为待添加图片
                if not (md is not None and md.hasText() and not md.hasUrls()):
                    imgs = images.images_from_mime(md)
                    if imgs:
                        self._add_pending_images(imgs)
                        return True
            elif et in (QEvent.Type.DragEnter, QEvent.Type.DragMove) and images.mime_has_image(e.mimeData()):
                e.acceptProposedAction()
                return True
            elif et == QEvent.Type.Drop and images.mime_has_image(e.mimeData()):
                imgs = images.images_from_mime(e.mimeData())
                e.acceptProposedAction()
                QTimer.singleShot(0, self, lambda: self._add_pending_images(imgs))
                return True
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
