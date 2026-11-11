"""半透明桌面待办挂件：常驻屏幕一角，显示未完成的备忘录。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import db
from .db import Memo

MAX_ROWS = 8
GROUP_MAX_ROWS = 6


class GroupHeader(QWidget):
    """挂件里的分类分组头：点击切换展开/折叠。"""

    toggled = Signal(str)

    def __init__(self, key: str, title: str, count: int, expanded: bool) -> None:
        super().__init__()
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(24)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        self.arrow = QLabel("▾" if expanded else "▸")
        self.arrow.setObjectName("widgetGroupArrow")
        name = QLabel(title)
        name.setObjectName("widgetGroupName")
        cnt = QLabel(str(count))
        cnt.setObjectName("widgetGroupCount")
        for w in (self.arrow, name, cnt):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.arrow)
        lay.addWidget(name, 1)
        lay.addWidget(cnt)

    def set_state(self, expanded: bool, count: int) -> None:
        self.arrow.setText("▾" if expanded else "▸")
        # 右侧计数在 layout 最后一个 widget
        self.layout().itemAt(self.layout().count() - 1).widget().setText(str(count))

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit(self.key)
            e.accept()  # 吃掉事件：不触发挂件拖动，也不穿透
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        # 连点的第二下会以 DoubleClick 事件到达而非 Press，
        # 必须同样触发展开/收起，否则快速连点会"卡住"
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit(self.key)
            e.accept()  # 吃掉：不穿透触发挂件的"双击打开主界面"
            return
        super().mouseDoubleClickEvent(e)


class TodoWidget(QWidget):
    """无边框置顶半透明小面板。可拖动，单击空白处打开主界面。"""

    open_main = Signal()
    done_toggled = Signal(int)  # memo_id
    visibility_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(300)
        self.setObjectName("todoWidget")
        self.setStyleSheet(
            """
            /* 面板背景由 paintEvent 手绘（半透明顶层窗口 QSS 背景不生效） */
            QLabel#widgetTitle { font-size: 13px; font-weight: bold; color: #f5f5ef; }
            QLabel#widgetCount { font-size: 11px; color: #9a9a90; }
            QLabel#widgetItem { font-size: 13px; color: #eeeeea; }
            QLabel#widgetHint { font-size: 10px; color: #9a9a92; }
            QLabel#widgetGroupName { font-size: 13px; font-weight: bold; color: #cfccc2; }
            QLabel#widgetGroupArrow { font-size: 9px; color: #8f8f86; }
            QLabel#widgetGroupCount { font-size: 10px; color: #8f8f86; }
            GroupHeader { background: transparent; }
            QPushButton#widgetBtn {
                border: none; background: transparent; color: #b8b8ae;
                font-size: 13px; padding: 0 4px;
            }
            QPushButton#widgetBtn:hover { color: #ffffff; }
            QPushButton#widgetDone {
                border: 1.5px solid rgba(255, 255, 255, 110);
                border-radius: 9px;
                background: transparent; color: transparent;
                font-size: 10px; font-weight: bold;
            }
            QPushButton#widgetDone:hover { border-color: #4ecb8d; color: #4ecb8d; }
            QPushButton#widgetDone[done="true"] {
                background: #2f9e63; border-color: #2f9e63; color: white;
            }
            """
        )
        self._build()
        self._press_pos = None
        self._dragging = False
        # 由 Controller 注入：返回经过分类折叠隐私过滤的备忘录列表
        self._memo_provider = None
        # 分组展开状态（会话内记忆，重启默认全部折叠 = 隐私默认）
        self._expanded: set[str] = set()
        self._groups: dict[str, dict] = {}
        # 连续点击时合并窗口尺寸调整，避免半透明面板反复重绘卡顿
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(40)
        self._resize_timer.timeout.connect(self.adjustSize)
        self._move_to_corner()

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.title_label = QLabel("待办")
        self.title_label.setObjectName("widgetTitle")
        self.count_label = QLabel("")
        self.count_label.setObjectName("widgetCount")
        for _w in (self.title_label, self.count_label):
            _w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header.addWidget(self.title_label)
        header.addWidget(self.count_label)
        header.addStretch(1)
        self.open_btn = QPushButton("↗")
        self.open_btn.setObjectName("widgetBtn")
        self.open_btn.setFixedSize(22, 22)
        self.open_btn.setToolTip("打开主界面")
        self.hide_btn = QPushButton("×")
        self.hide_btn.setObjectName("widgetBtn")
        self.hide_btn.setFixedSize(22, 22)
        self.hide_btn.setToolTip("隐藏挂件（托盘菜单可再打开）")
        header.addWidget(self.open_btn)
        header.addWidget(self.hide_btn)
        root.addLayout(header)

        self.list_host = QVBoxLayout()
        self.list_host.setSpacing(2)
        self.list_host.setContentsMargins(0, 2, 0, 0)
        root.addLayout(self.list_host)

        self.more_label = QLabel("")
        self.more_label.setObjectName("widgetHint")
        self.more_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.more_label)

        self.hint_label = QLabel("Ctrl+O 快捷记录 · 双击打开 · 按住拖动")
        self.hint_label.setObjectName("widgetHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.hint_label)

        self.open_btn.clicked.connect(self.open_main)
        self.hide_btn.clicked.connect(self.hide)

    # ---------- 数据 ----------

    def reload(self) -> None:
        # 清空旧行
        while self.list_host.count():
            item = self.list_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._groups.clear()

        provider = self._memo_provider or db.list_memos
        todos = [m for m in provider() if not m.is_done]
        self.count_label.setText(f"{len(todos)} 项" if todos else "")

        # 按分类分组：未分类("0")排最后；优先归入未折叠的分类
        cats = {c.id: c for c in db.list_categories()}
        collapsed_ids = {c.id for c in cats.values() if c.collapsed}
        cat_map = db.memo_category_map()

        def group_key(m: Memo) -> str:
            cids = cat_map.get(m.id, set())
            for cid in sorted(cids):
                if cid not in collapsed_ids:
                    return str(cid)
            return str(sorted(cids)[0]) if cids else "0"

        groups: dict[str, list[Memo]] = {}
        for m in todos:
            groups.setdefault(group_key(m), []).append(m)
        ordered = sorted(groups.keys(), key=lambda k: (k == "0", int(k)))

        for key in ordered:
            memos = groups[key]
            title = "未分类" if key == "0" else cats.get(int(key), None)
            title = title.name if hasattr(title, "name") else str(title)
            expanded = key in self._expanded
            hdr = GroupHeader(key, title, len(memos), expanded)
            hdr.toggled.connect(self._toggle_group)
            self.list_host.addWidget(hdr)

            # 分组内容容器：折叠时隐藏，展开时懒加载条目
            body = QWidget()
            bl = QVBoxLayout(body)
            bl.setContentsMargins(0, 0, 0, 4)
            bl.setSpacing(2)
            body.setVisible(expanded)
            if expanded:
                self._fill_group(bl, memos)
            self.list_host.addWidget(body)
            self._groups[key] = {"hdr": hdr, "body": body, "memos": memos}

        self.more_label.setVisible(False)
        self.adjustSize()

    def _fill_group(self, lay: QVBoxLayout, memos: list[Memo]) -> None:
        for memo in memos[:GROUP_MAX_ROWS]:
            lay.addWidget(self._make_row(memo))
        rest = len(memos) - GROUP_MAX_ROWS
        if rest > 0:
            more = QLabel(f"… 还有 {rest} 条")
            more.setObjectName("widgetHint")
            more.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lay.addWidget(more)

    def _toggle_group(self, key: str) -> None:
        """只切换该组容器显隐，不重建挂件 —— 点击即时响应。"""
        g = self._groups.get(key)
        if g is None:
            return
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
            if g["body"].layout().count() == 0:
                self._fill_group(g["body"].layout(), g["memos"])
        expanded = key in self._expanded
        g["hdr"].set_state(expanded, len(g["memos"]))
        g["body"].setVisible(expanded)
        self._resize_timer.start()

    def _make_row(self, memo: Memo) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(8)

        btn = QPushButton("○")
        btn.setObjectName("widgetDone")
        btn.setFixedSize(18, 18)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mid = memo.id
        btn.clicked.connect(lambda _c=False, i=mid: self.done_toggled.emit(i))
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

        label = QLabel(memo.content)
        label.setObjectName("widgetItem")
        label.setToolTip(memo.content)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 单行省略：宽度固定（面板定宽 300，扣除边距/圆圈/间距）
        label.setFixedWidth(300 - 12 * 2 - 18 - 8 - 8)
        fm = label.fontMetrics()
        label.setText(fm.elidedText(memo.content, Qt.TextElideMode.ElideRight, label.width()))
        lay.addWidget(label, 1)

        # 置顶标记
        if memo.is_pinned:
            pin = QLabel("📌")
            pin.setStyleSheet("font-size: 10px;")
            pin.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lay.addWidget(pin, 0, Qt.AlignmentFlag.AlignVCenter)

        return row

    # ---------- 显示 / 位置 ----------

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(
            geo.right() - self.width() - 24,
            geo.bottom() - 320,
        )

    # ---------- 绘制 / 显隐 ----------

    def paintEvent(self, e):  # noqa: N802
        """手绘圆角半透明深色面板（QSS 背景在半透明顶层窗口上不生效）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0), 16, 16
        )
        p.fillPath(path, QColor(38, 38, 44, 190))
        p.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
        p.drawPath(path)
        p.end()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.visibility_changed.emit(True)

    def hideEvent(self, e):  # noqa: N802
        super().hideEvent(e)
        self.visibility_changed.emit(False)

    # ---------- 拖动 ----------

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._press_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            cur = e.globalPosition().toPoint()
            if not self._dragging and (cur - self._press_pos).manhattanLength() > 5:
                self._dragging = True
            if self._dragging:
                self.move(self.pos() + cur - self._press_pos)
                self._press_pos = cur
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            self._press_pos = None
            self._dragging = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.open_main.emit()
        super().mouseDoubleClickEvent(e)
